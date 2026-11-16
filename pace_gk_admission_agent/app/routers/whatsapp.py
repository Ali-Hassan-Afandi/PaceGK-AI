"""WhatsApp webhook router (Meta WhatsApp Cloud API + Twilio fallback).

Handles:
* Meta webhook verification (GET)
* Inbound text and image messages (POST JSON)
* Twilio form-encoded webhooks (POST)
* Admin APPROVE / REJECT commands
* Durable webhook idempotency via the database (survives Railway restarts and
  multiple instances)
* Student admission state machine via AdmissionAgent
* Outbound WhatsApp sending via send_whatsapp_text

Personal data (phones, tokens, full CNICs, private image URLs) is masked or
omitted from log output.
"""

import logging
import urllib.parse
from typing import Any, Dict, Optional, Set

from fastapi import APIRouter, Request, Response
from pydantic import BaseModel
from sqlalchemy.exc import IntegrityError

from ..agents.admission_agent import AdmissionAgent, now_utc
from ..config import settings
from ..database import SessionLocal, ensure_schema
from ..models.batch import Batch
from ..models.student import Student
from ..models.webhook import ProcessedWebhookMessage
from ..services.admission_export_service import export_batch_students
from ..services.admission_validation import (
    all_text_fields_complete,
    fee_receipt_stored,
    missing_text_fields,
    photograph_stored,
)
from ..services.approval_service import approve_student, reject_student
from ..services.fee_service import ALLOWED_FEE_CATEGORIES as FEE_CATEGORIES
from ..utils.whatsapp import send_whatsapp_text

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/whatsapp", tags=["whatsapp"])

agent = AdmissionAgent()

# Ensure durable schema (idempotency table + notification columns) on startup.
ensure_schema()


class WhatsAppIncomingMessage(BaseModel):
    phone_number: str
    text: str
    message_id: Optional[str] = None
    media_url: Optional[str] = None
    media_id: Optional[str] = None
    media_type: Optional[str] = None  # "text", "image", ...
    content_type: Optional[str] = None  # MIME type of the media


def _reserve_message_id(
    message_id: str, phone_number: str, message_type: Optional[str]
) -> bool:
    """Atomically reserve a webhook message ID in the database.

    Returns True if this is the first time the ID is seen (caller should
    process the message), False if it was already processed (duplicate).
    Safe for concurrent duplicate requests: the unique constraint on
    ``message_id`` makes the reservation atomic.
    """
    db = SessionLocal()
    try:
        row = ProcessedWebhookMessage(
            message_id=message_id,
            phone_number=phone_number,
            message_type=message_type,
        )
        db.add(row)
        db.commit()
        return True
    except IntegrityError:
        db.rollback()
        return False
    finally:
        db.close()


def _is_duplicate(message_id: Optional[str]) -> bool:
    if not message_id:
        return False
    db = SessionLocal()
    try:
        row = (
            db.query(ProcessedWebhookMessage)
            .filter(ProcessedWebhookMessage.message_id == message_id)
            .first()
        )
        return row is not None
    finally:
        db.close()


class WhatsAppRouterError(Exception):
    pass


def _extract_from_twilio(form: Dict[str, str]) -> Optional[WhatsAppIncomingMessage]:
    body = form.get("Body")
    frm = form.get("From")
    if not body or not frm:
        return None
    return WhatsAppIncomingMessage(
        phone_number=frm.replace("whatsapp:", ""),
        text=body,
        media_url=form.get("MediaUrl0") or None,
        media_type="image" if form.get("MediaUrl0") else "text",
        content_type=form.get("MediaContentType0") or None,
    )


def _extract_from_meta(value: Dict[str, Any]) -> Optional[WhatsAppIncomingMessage]:
    """Extract an incoming message from a Meta WhatsApp Cloud API webhook payload.

    Supports text and image messages. Status-only notifications (delivery/read)
    return None.
    """
    try:
        entry = value["entry"][0]
        change = entry["changes"][0]
        value_data = change["value"]

        if "messages" not in value_data:
            # Status notification (delivered, read, etc.) — nothing to reply to.
            return None

        msg = value_data["messages"][0]
        message_type = msg.get("type") or "text"
        message_id = msg.get("id") or None
        frm = msg.get("from") or ""

        if message_type == "text":
            text = msg.get("text", {}).get("body") or ""
            return WhatsAppIncomingMessage(
                phone_number=frm,
                text=text,
                message_id=message_id,
                media_type="text",
            )

        if message_type == "image":
            image = msg.get("image", {}) or {}
            media_id = image.get("id") or message_id
            return WhatsAppIncomingMessage(
                phone_number=frm,
                text="",  # image messages carry no text body
                message_id=message_id,
                media_url=None,
                media_type="image",
                media_id=media_id,
                content_type=image.get("mime_type") or None,
            )

        # Other message types (audio, video, document) are acknowledged but not
        # processed as admission input.
        return WhatsAppIncomingMessage(
            phone_number=frm,
            text="",
            message_id=message_id,
            media_type=message_type,
        )
    except (KeyError, IndexError, TypeError):
        return None


def _to_whatsapp_number(phone_number: str) -> str:
    """Return phone number in plain international digit format for Meta API."""
    number = phone_number.strip()
    if number.startswith("whatsapp:"):
        number = number[len("whatsapp:"):]
    return number.lstrip("+")


def _admin_numbers_normalized() -> Set[str]:
    return {
        n.replace("whatsapp:", "").strip().lstrip("+")
        for n in settings.admin_numbers
    }


async def _send_to_student(student_phone: str, body: str) -> bool:
    """Send a text to a student. Returns True when the WhatsApp API accepted it."""
    try:
        await send_whatsapp_text(_to_whatsapp_number(student_phone), body)
        return True
    except Exception:
        logger.exception("Failed to send WhatsApp message to student.")
        return False


async def _handle_admin_command(text: str, sender_phone: str) -> Optional[str]:
    sender_clean = sender_phone.replace("whatsapp:", "").strip().lstrip("+")
    if sender_clean not in _admin_numbers_normalized():
        return None

    parts = text.strip().split()
    if not parts:
        return None
    command = parts[0].upper()
    if command not in ("APPROVE", "REJECT"):
        return None

    if len(parts) < 2:
        return (
            "Please provide the student phone number. "
            "Usage: APPROVE <student_phone> <fee_category> | "
            "REJECT <student_phone> <reason>"
        )

    target_phone = parts[1].replace("whatsapp:", "").strip().lstrip("+")
    if not target_phone:
        return "Invalid student phone number."

    db = SessionLocal()
    try:
        if command == "APPROVE":
            category = parts[2].upper() if len(parts) > 2 else "NEW"
            if category not in FEE_CATEGORIES:
                return (
                    f"Invalid fee category '{category}'. "
                    f"Allowed: {', '.join(sorted(FEE_CATEGORIES))}."
                )

            student = approve_student(db, target_phone, sender_clean, category)
            if student is None:
                # Retry path: an admin may re-issue APPROVE for a student that is
                # already APPROVED but whose confirmation notifications have not
                # yet been accepted (e.g. a previous send failed). Re-send the
                # notifications without re-running approval.
                from ..services.approval_service import get_student_by_phone

                existing = get_student_by_phone(db, target_phone)
                if existing and existing.status == "APPROVED":
                    student = existing
                else:
                    return (
                        f"Student {target_phone} not found or not ready for approval. "
                        "Expected state: FEE_SUBMITTED, with a stored fee receipt and "
                        "complete admission details."
                    )

            batch = (
                db.query(Batch).filter(Batch.id == student.batch_id).first()
                if student.batch_id
                else None
            )
            community_link = batch.community_link if batch else ""

            # Community link (idempotent: only sent once).
            if community_link and not student.community_link_sent_at:
                if await _send_to_student(student.phone_number, community_link):
                    student.community_link_sent_at = now_utc()
                    db.commit()

            # Confirmation message (idempotent: only sent once).
            if not student.confirmation_sent_at:
                confirmation = (
                    f"Congratulations! Your application for "
                    f"{batch.name if batch else 'the batch'} "
                    f"(category {category}) has been approved. "
                    "Please join the community via the link we sent."
                )
                if await _send_to_student(student.phone_number, confirmation):
                    student.confirmation_sent_at = now_utc()
                    db.commit()

            # CONFIRMED only when the confirmation was accepted by WhatsApp
            # (and the community link, if any, was accepted too).
            notifications_ok = student.confirmation_sent_at is not None and (
                not community_link or student.community_link_sent_at is not None
            )
            if notifications_ok and student.status != "CONFIRMED":
                student.status = "CONFIRMED"
                db.commit()

            batch_sheet_path: Optional[str] = None
            if student.batch_id:
                try:
                    exported_path = export_batch_students(db, student.batch_id)
                    batch_sheet_path = str(exported_path) if exported_path else None
                except Exception:
                    logger.exception("Failed to export admission batch sheet.")
                    batch_sheet_path = None

            fee_amount = float(student.fee_amount) if student.fee_amount is not None else 0.0
            reply = (
                f"Approved {target_phone}. Fee category: {category}. "
                f"Amount: {fee_amount}. "
            )
            if community_link:
                reply += "Community link sent to student. "
            if student.confirmation_sent_at:
                reply += "Confirmation sent to student. "
            if batch_sheet_path:
                reply += f"Admission batch sheet created: {batch_sheet_path}"
            return reply.strip()

        # REJECT
        reason = " ".join(parts[2:]) if len(parts) > 2 else ""
        student = reject_student(db, target_phone, sender_clean, reason)
        if student is None:
            return f"Student {target_phone} not found."
        try:
            await _send_to_student(
                student.phone_number,
                f"Your admission application has been rejected. Reason: {reason or 'not specified'}",
            )
        except Exception:
            logger.exception("Failed to send rejection notice to student.")
        return f"Rejected {target_phone}."
    finally:
        db.close()


@router.get("/webhook")
async def verify_webhook(request: Request):
    mode = request.query_params.get("hub.mode")
    token = request.query_params.get("hub.verify_token")
    challenge = request.query_params.get("hub.challenge")

    if (
        mode == "subscribe"
        and settings.whatsapp_verify_token
        and token == settings.whatsapp_verify_token
        and challenge is not None
    ):
        return Response(
            content=challenge,
            status_code=200,
            media_type="text/plain",
        )

    return Response(
        status_code=403,
        content="Invalid verification token or mode",
        media_type="text/plain",
    )


@router.post("/webhook")
async def receive_webhook(request: Request) -> Dict[str, str]:
    content_type = request.headers.get("content-type", "")
    incoming: Optional[WhatsAppIncomingMessage] = None

    if "application/json" in content_type:
        payload = await request.json()
        incoming = _extract_from_meta(payload)
    else:
        raw_body = (await request.body()).decode("utf-8")
        form: Dict[str, str] = {}
        if raw_body:
            parsed = urllib.parse.parse_qs(raw_body)
            form = {k: v[0] for k, v in parsed.items() if v}
        incoming = _extract_from_twilio(form)

    # No processable incoming message (e.g. status notification) -> acknowledge.
    if incoming is None:
        logger.info("Incoming WhatsApp webhook received with no processable message.")
        return {"status": "ok"}

    # Durable duplicate protection (database-backed).
    if incoming.message_id:
        if _is_duplicate(incoming.message_id):
            logger.info(
                "Duplicate WhatsApp message ID ignored. message_id=%s",
                incoming.message_id,
            )
            return {"status": "ok"}
        if not _reserve_message_id(
            incoming.message_id, incoming.phone_number, incoming.media_type
        ):
            # Another concurrent request already reserved it.
            logger.info(
                "Concurrent duplicate WhatsApp message ID ignored. message_id=%s",
                incoming.message_id,
            )
            return {"status": "ok"}

    sender_phone = incoming.phone_number
    logger.info(
        "Incoming WhatsApp message received. from=%s type=%s",
        sender_phone,
        incoming.media_type or "text",
    )

    # Admin commands take precedence.
    admin_reply = await _handle_admin_command(incoming.text, sender_phone)
    if admin_reply is not None:
        try:
            await send_whatsapp_text(_to_whatsapp_number(sender_phone), admin_reply)
        except Exception:
            logger.exception(
                "Failed to send admin reply via WhatsApp Cloud API. from=%s", sender_phone
            )
        return {"status": "ok"}

    # Student message -> admission agent (state machine handles text & images).
    try:
        reply = await agent.generate_reply(
            messages=[{"role": "user", "content": incoming.text}],
            phone_number=incoming.phone_number,
            media_url=incoming.media_url,
            media_type=incoming.media_type,
            media_id=incoming.media_id,
            content_type=incoming.content_type,
        )
    except Exception:
        logger.exception("AI response generation failed. from=%s", sender_phone)
        return {"status": "ok"}

    try:
        await send_whatsapp_text(_to_whatsapp_number(incoming.phone_number), reply)
    except Exception:
        logger.exception("Failed to send AI reply via WhatsApp Cloud API. from=%s", sender_phone)

    return {"status": "ok"}
