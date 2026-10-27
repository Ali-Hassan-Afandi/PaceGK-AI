import urllib.parse
from typing import Any, Dict, Optional

from fastapi import APIRouter, Request, Response
from pydantic import BaseModel

from ..agents.admission_agent import AdmissionAgent
from ..config import settings
from ..database import SessionLocal
from ..models.batch import Batch
from ..services.approval_service import approve_student, reject_student
from ..utils.whatsapp import send_whatsapp_text

router = APIRouter(prefix="/whatsapp", tags=["whatsapp"])

agent = AdmissionAgent()


class WhatsAppIncomingMessage(BaseModel):
    phone_number: str
    text: str
    message_id: Optional[str] = None


def _extract_from_twilio(form: Dict[str, str]) -> Optional[WhatsAppIncomingMessage]:
    body = form.get("Body")
    frm = form.get("From")
    if not body or not frm:
        return None
    return WhatsAppIncomingMessage(phone_number=frm.replace("whatsapp:", ""), text=body)


def _extract_from_meta(value: Dict[str, Any]) -> Optional[WhatsAppIncomingMessage]:
    try:
        msg = value["entry"][0]["changes"][0]["value"]["messages"][0]
        text = msg.get("text", {}).get("body") or ""
        frm = msg.get("from") or ""
        return WhatsAppIncomingMessage(phone_number=frm.replace("whatsapp:", ""), text=text)
    except (KeyError, IndexError, TypeError):
        return None


def _to_whatsapp_number(phone_number: str) -> str:
    if phone_number.startswith("whatsapp:"):
        return phone_number
    return f"whatsapp:+{phone_number.lstrip('+')}"


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
                await send_whatsapp_text(_to_whatsapp_number(student.phone_number), community_link)
            confirmation = (
                f"Congratulations {student.phone_number}! You have been approved for "
                f"{batch.name if batch else 'the batch'} under category {category}. "
                "Please join the community via the link we sent. After joining, you will receive seat confirmation."
            )
            await send_whatsapp_text(_to_whatsapp_number(student.phone_number), confirmation)

            # Move to CONFIRMED only after the community link + confirmation were sent.
            student.status = "CONFIRMED"
            db.commit()
            fee_amount = float(student.fee_amount) if student.fee_amount is not None else 0.0
            return (
                f"Approved {student.phone_number}. Fee category: {category}. "
                f"Amount: {fee_amount}. Community link sent to student."
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

    if incoming is None:
        return {"status": "ok"}

    sender_phone = incoming.phone_number

    admin_reply = await _handle_admin_command(incoming.text, sender_phone)
    if admin_reply is not None:
        await send_whatsapp_text(_to_whatsapp_number(sender_phone), admin_reply)
        return {"status": "ok"}

    reply = await agent.generate_reply(
        messages=[{"role": "user", "content": incoming.text}],
        phone_number=incoming.phone_number,
    )

    await send_whatsapp_text(_to_whatsapp_number(incoming.phone_number), reply)

    return {"status": "ok"}
