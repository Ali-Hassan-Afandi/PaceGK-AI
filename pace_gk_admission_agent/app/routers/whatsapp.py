import logging
import urllib.parse
from typing import Any, Dict, Optional, Set

from fastapi import APIRouter, Request, Response
from pydantic import BaseModel

from ..agents.admission_agent import AdmissionAgent
from ..config import settings
from ..database import SessionLocal
from ..models.batch import Batch
from ..services.admission_export_service import export_batch_students
from ..services.approval_service import approve_student, reject_student
from ..utils.whatsapp import send_whatsapp_text

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/whatsapp", tags=["whatsapp"])

agent = AdmissionAgent()

# Track processed incoming WhatsApp message IDs to avoid duplicate replies
# from redelivered webhooks. Scope: single process instance.
_processed_message_ids: Set[str] = set()


class WhatsAppIncomingMessage(BaseModel):
    phone_number: str
    text: str
    message_id: Optional[str] = None
    media_url: Optional[str] = None


def _extract_from_twilio(form: Dict[str, str]) -> Optional[WhatsAppIncomingMessage]:
    body = form.get("Body")
    frm = form.get("From")
    if not body or not frm:
        return None
    return WhatsAppIncomingMessage(
        phone_number=frm.replace("whatsapp:", ""),
        text=body,
        media_url=form.get("MediaUrl0") or None,
    )


def _extract_from_meta(value: Dict[str, Any]) -> Optional[WhatsAppIncomingMessage]:
    """Extract an incoming message from a Meta WhatsApp Cloud API webhook payload.

    Returns None for status-only webhooks (delivery/read receipts) or any
    payload that does not contain a message object.
    """
    try:
        # Ignore status-only notifications (no messages array).
        entry = value["entry"][0]
        change = entry["changes"][0]
        value_data = change["value"]

        if "messages" not in value_data:
            # Status notification (delivered, read, etc.) — nothing to reply to.
            return None

        msg = value_data["messages"][0]

        # Ignore non-text message types safely (no body to process).
        if msg.get("type") != "text":
            return None

        text = msg.get("text", {}).get("body") or ""
        frm = msg.get("from") or ""
        message_id = msg.get("id") or None

        return WhatsAppIncomingMessage(
            phone_number=frm.replace("whatsapp:", ""),
            text=text,
            message_id=message_id,
            media_url=None,
        )
    except (KeyError, IndexError, TypeError):
        return None


def _to_whatsapp_number(phone_number: str) -> str:
    """Return phone number in plain international digit format for Meta API.

    Strips 'whatsapp:' prefix and leading '+'. Further normalization
    (removing spaces, hyphens, parentheses) is handled in send_whatsapp_text.
    """
    number = phone_number.strip()
    if number.startswith("whatsapp:"):
        number = number[len("whatsapp:"):]
    return number.lstrip("+")


async def _handle_admin_command(text: str, sender_phone: str) -> Optional[str]:
    sender_clean = sender_phone.replace("whatsapp:", "").strip().lstrip("+")
    if sender_clean not in [n.replace("whatsapp:", "").strip() for n in settings.admin_numbers]:
        return None

    parts = text.strip().split()
    if not parts:
        return None
    command = parts[0].upper()
    if command not in ("APPROVE", "REJECT"):
        return None
    reason = parts[2] if len(parts) > 2 and command == "REJECT" else ""

    target_phone = (parts[1] if len(parts) > 1 else "").replace("whatsapp:", "").strip()
    if not target_phone:
        return "Please provide the student phone number. Usage: APPROVE <student_phone>"

    db = SessionLocal()
    try:
        if command == "APPROVE":
            category = parts[2].upper() if len(parts) > 2 else "NEW"
            student = approve_student(db, target_phone, sender_clean, category)
            if student is None:
                return f"Student {target_phone} not found or not awaiting fee review."

            batch = (
                db.query(Batch).filter(Batch.id == student.batch_id).first()
                if student.batch_id
                else None
            )
            community_link = batch.community_link if batch else ""
            if community_link:
                try:
                    await send_whatsapp_text(_to_whatsapp_number(student.phone_number), community_link)
                except Exception:
                    logger.exception("Failed to send community link via WhatsApp Cloud API.")
            confirmation = (
                f"Congratulations {student.phone_number}! You have been approved for "
                f"{batch.name if batch else 'the batch'} under category {category}. "
                "Please join the community via the link we sent. After joining, you will receive seat confirmation."
            )
            try:
                await send_whatsapp_text(_to_whatsapp_number(student.phone_number), confirmation)
            except Exception:
                logger.exception("Failed to send confirmation via WhatsApp Cloud API.")

            # Move to CONFIRMED only after the community link + confirmation were sent.
            student.status = "CONFIRMED"
            db.commit()
            fee_amount = float(student.fee_amount) if student.fee_amount is not None else 0.0

            # Human verified the fee -> prepare the admission batch data sheet
            batch_sheet_path: Optional[str] = None
            if student.batch_id:
                try:
                    exported_path = export_batch_students(db, student.batch_id)
                    batch_sheet_path = str(exported_path) if exported_path else None
                except Exception:
                    batch_sheet_path = None

            return (
                f"Approved {student.phone_number}. Fee category: {category}. "
                f"Amount: {fee_amount}. Community link sent to student."
                + (f" Admission batch sheet created: {batch_sheet_path}" if batch_sheet_path else "")
            )

        student = reject_student(db, target_phone, sender_clean, reason)
        if student is None:
            return f"Student {target_phone} not found."
        return f"Rejected {student.phone_number}."
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

    # Duplicate protection: skip if we already processed this message ID.
    if incoming.message_id and incoming.message_id in _processed_message_ids:
        logger.info(
            "Duplicate WhatsApp message ID ignored. message_id=%s", incoming.message_id
        )
        return {"status": "ok"}
    if incoming.message_id:
        _processed_message_ids.add(incoming.message_id)

    sender_phone = incoming.phone_number

    logger.info("Incoming WhatsApp message received. from=%s", sender_phone)

    admin_reply = await _handle_admin_command(incoming.text, sender_phone)
    if admin_reply is not None:
        try:
            await send_whatsapp_text(_to_whatsapp_number(sender_phone), admin_reply)
        except Exception:
            logger.exception("Failed to send admin reply via WhatsApp Cloud API. from=%s", sender_phone)
        return {"status": "ok"}

    try:
        reply = await agent.generate_reply(
            messages=[{"role": "user", "content": incoming.text}],
            phone_number=incoming.phone_number,
            media_url=incoming.media_url,
        )
    except Exception as exc:
        logger.exception("AI response generation failed. from=%s", sender_phone)
        return {"status": "ok"}

    logger.info("AI response generated. from=%s", sender_phone)
    try:
        await send_whatsapp_text(_to_whatsapp_number(incoming.phone_number), reply)
        logger.info("Sending reply through WhatsApp Cloud API. from=%s", sender_phone)
    except Exception:
        logger.exception("Failed to send AI reply via WhatsApp Cloud API. from=%s", sender_phone)

    return {"status": "ok"}
