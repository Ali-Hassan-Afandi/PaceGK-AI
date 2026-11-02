import json
from datetime import datetime
from typing import Any, Dict, List, Optional

from groq import AsyncGroq
from openai import AsyncOpenAI

from ..config import settings
from ..database import SessionLocal
from ..models.batch import Batch
from ..schemas.batch import BatchResponse
from ..services.admission_service import get_or_create_student
from ..services.batch_service import get_batches


SYSTEM_PROMPT = (
    "You are the admission assistant chatbot for PACE GK Academy, an online academy "
    "focused on general knowledge (GK) studies for competitive exams. "
    "Answer general academy questions (batches, fees, timings, subjects, policies) clearly "
    "and briefly, and never invent facts that you are not sure about. "
    "When the user asks about admission or registering in a batch, guide them to choose a "
    "batch first, then ask them to submit the fee paid picture for verification. "
    "Keep replies short, friendly and WhatsApp-friendly."
)

DETAILS_EXTRACTION_PROMPT = (
    "You are the admission desk assistant for PACE GK Academy. Read the conversation and "
    "extract the applicant's admission details. Respond with ONLY a JSON object using these "
    "exact keys: 'name', 'father_name', 'domicile', 'id_card_number', 'student_pic_url', "
    "'fee_paid_pic_url', 'fee_amount', 'fee_category'. Use empty strings for anything that is "
    "not provided. Never invent values."
)

DEFAULT_REPLY_WITHOUT_KEY = (
    "Assalam-o-Alaikum! Welcome to PACE GK Academy admission. "
    "Which admission batch are you interested in? Please share the batch name/code."
)

FEE_RECEIVED_REPLY = (
    "Assalam-o-Alaikum! We have received your fee payment screenshot. "
    "Our admission team is now reviewing your fee payment for human verification. "
    "Please wait while we verify the fee payment; you will be notified once the verification "
    "is complete."
)


class AdmissionAgent:
    def __init__(
        self,
        client: Optional[AsyncOpenAI] = None,
        groq_client: Optional[AsyncGroq] = None,
    ) -> None:
        self.client = client
        self.groq_client = groq_client
        if client is None and settings.openai_api_key:
            self.client = AsyncOpenAI(api_key=settings.openai_api_key)
        if groq_client is None and settings.groq_api_key:
            self.groq_client = AsyncGroq(api_key=settings.groq_api_key)

    def _provider(self) -> Optional[str]:
        if self.client is not None:
            return "openai"
        if self.groq_client is not None:
            return "groq"
        return None

    def _has_llm(self) -> bool:
        if self.client is not None or self.groq_client is not None:
            return True
        return bool(settings.openai_api_key or settings.groq_api_key)

    async def _generate(self, messages: List[Dict[str, Any]]) -> str:
        provider = self._provider()
        if provider == "groq":
            completion = await self.groq_client.chat.completions.create(
                model=settings.groq_model,
                messages=messages,
                temperature=0.7,
                max_tokens=400,
            )
            return completion.choices[0].message.content or DEFAULT_REPLY_WITHOUT_KEY
        if provider == "openai":
            completion = await self.client.chat.completions.create(
                model=settings.openai_model,
                messages=messages,
                temperature=0.7,
                max_tokens=400,
            )
            return completion.choices[0].message.content or DEFAULT_REPLY_WITHOUT_KEY
        return DEFAULT_REPLY_WITHOUT_KEY

    async def _generate_json(self, messages: List[Dict[str, Any]]) -> Dict[str, Any]:
        provider = self._provider()
        if provider == "groq":
            completion = await self.groq_client.chat.completions.create(
                model=settings.groq_model,
                messages=messages,
                temperature=0.0,
                max_tokens=500,
            )
        elif provider == "openai":
            completion = await self.client.chat.completions.create(
                model=settings.openai_model,
                messages=messages,
                temperature=0.0,
                max_tokens=500,
            )
        else:
            return {}

        content = completion.choices[0].message.content or "{}"
        try:
            parsed = json.loads(content)
            if isinstance(parsed, dict):
                return {k: v for k, v in parsed.items() if v}
        except (ValueError, TypeError):
            return {}
        return {}

    async def _extract_details(self, messages: List[Dict[str, Any]]) -> Dict[str, Any]:
        if not self._has_llm():
            return {}
        conversation = "\n".join(
            f"{m.get('role')}: {m.get('content') or ''}" for m in messages
        )
        return await self._generate_json(
            [
                {"role": "system", "content": DETAILS_EXTRACTION_PROMPT},
                {"role": "user", "content": conversation},
            ]
        )

    async def get_available_batches(self) -> List[Dict[str, Any]]:
        db = SessionLocal()
        try:
            batches: List[BatchResponse] = get_batches(db)
        finally:
            db.close()
        return [b.model_dump() for b in batches]

    async def generate_reply(
        self,
        messages: List[Dict[str, Any]],
        phone_number: str,
        media_url: Optional[str] = None,
    ) -> str:
        db = SessionLocal()
        try:
            student = get_or_create_student(db, phone_number)
            latest_text = (messages[-1].get("content") if messages else "") or ""
            upper_text = latest_text.upper()

            batches: List[BatchResponse] = get_batches(db)
            matched_batch: Optional[BatchResponse] = next(
                (b for b in batches if b.batch_code.upper() in upper_text), None
            )

            if student.status == "CONFIRMED":
                # Previous admission cycle completed -> start a fresh inquiry.
                student.status = "INQUIRY"
                db.commit()

            # Fee payment picture submitted -> queue the fee for human verification.
            if media_url and student.status in ("BATCH_CONFIRMED", "FORM_FILL"):
                student.status = "FEE_SUBMITTED"
                if not student.submitted_at:
                    student.submitted_at = datetime.utcnow()
                student.fee_paid_pic_url = media_url
                db.commit()
                return FEE_RECEIVED_REPLY

            if student.status in ("INQUIRY", "AWAITING_BATCH"):
                if matched_batch:
                    batch = (
                        db.query(Batch)
                        .filter(Batch.batch_code == matched_batch.batch_code)
                        .first()
                    )
                    student.status = "BATCH_CONFIRMED"
                    student.batch_id = batch.id if batch else None
                    db.commit()

                    batch_label = (
                        f"{batch.name} ({batch.batch_code})"
                        if batch
                        else f"{matched_batch.name} ({matched_batch.batch_code})"
                    )
                    return (
                        f"Great choice! {batch_label} is confirmed. "
                        "Please fill the admission form: name, father name, domicile, ID card "
                        "number, WhatsApp number, student picture and fee paid picture. After "
                        "filling it, send the fee paid screenshot as the final step."
                    )

                if not self._has_llm():
                    return (
                        "Welcome to PACE GK Academy! 🎓 Which admission batch are you interested in? "
                        "Please reply with the batch code (e.g., NOV26-1A)."
                    )

                reply = await self._generate(
                    [
                        {"role": "system", "content": SYSTEM_PROMPT},
                        {"role": "user", "content": latest_text},
                    ]
                )
                return reply

            if student.status == "BATCH_CONFIRMED":
                # Batch confirmed -> take the student's admission input and move to fee
                # submission (student sends admission details + fee paid picture).
                details = await self._extract_details(messages) if self._has_llm() else {}
                for field, value in details.items():
                    if value and getattr(student, field, None) in (None, ""):
                        setattr(student, field, value)

                student.status = "FEE_SUBMITTED"
                if not student.submitted_at:
                    student.submitted_at = datetime.utcnow()
                db.commit()
                return (
                    "We have received your admission details. Next step: fee submission. "
                    "Please submit the fee and send the fee paid screenshot."
                )

            if student.status == "FORM_FILL":
                # Fee submission step reached (details received / fee payment pending).
                student.status = "FEE_SUBMITTED"
                if not student.submitted_at:
                    student.submitted_at = datetime.utcnow()
                return (
                    "We have received your admission details. Next step: fee submission. "
                    "Please submit the fee and send the fee paid screenshot."
                )

            if student.status == "FEE_SUBMITTED":
                # Fee payment is with the human-in-the-loop reviewer.
                return (
                    "Your fee payment is now with our admission team for human verification. "
                    "Please wait while we verify the fee payment screenshot. You will be notified "
                    "once the verification is complete."
                )

            return DEFAULT_REPLY_WITHOUT_KEY
        finally:
            db.close()
