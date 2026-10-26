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
    "focused on general knowledge (GK) studies. When a user asks about admission, guide "
    "them: ask which admission batch they want, then ask them to fill the admission form "
    "with name, father name, domicile, ID card number, WhatsApp number, student picture and "
    "fee paid picture. Keep replies short, friendly and WhatsApp-friendly."
)

DEFAULT_REPLY_WITHOUT_KEY = (
    "Assalam-o-Alaikum! Welcome to PACE GK Academy admission. "
    "Which admission batch are you interested in? Please share the batch name/code."
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

    async def get_available_batches(self) -> List[Dict[str, Any]]:
        db = SessionLocal()
        try:
            batches: List[BatchResponse] = get_batches(db)
        finally:
            db.close()
        return [b.model_dump() for b in batches]

    async def generate_reply(self, messages: List[Dict[str, Any]], phone_number: str) -> str:
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
                        "Please fill the admission form: name, father name, domicile, "
                        "ID card number, WhatsApp number, student picture and fee paid picture."
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
                student.status = "FORM_FILL"
                db.commit()
                return (
                    "Please send your admission form details: name, father name, domicile, "
                    "ID card number, WhatsApp number, student picture and fee paid picture."
                )

            if student.status == "FORM_FILL":
                student.status = "FEE_SUBMITTED"
                if not student.submitted_at:
                    student.submitted_at = datetime.utcnow()
                db.commit()
                return (
                    "We have received your admission details. Next step: fee submission. "
                    "Please submit the fee and send the fee paid screenshot."
                )

            return DEFAULT_REPLY_WITHOUT_KEY
        finally:
            db.close()
