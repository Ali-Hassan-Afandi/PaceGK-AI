"""Admission agent: deterministic state machine + LLM-assisted replies.

State machine (single source of truth):

    INQUIRY -> BATCH_CONFIRMED -> FORM_FILL -> FEE_PENDING -> FEE_SUBMITTED
                                                            -> APPROVED -> CONFIRMED
    REJECTED is reachable from a review state via an authorized admin action.

Key rules:
* BATCH_CONFIRMED -> FORM_FILL (never straight to FEE_SUBMITTED on text).
* FORM_FILL collects and validates the five mandatory fields across one or many
  messages; valid information is never overwritten by empty/invalid values.
* FORM_FILL -> FEE_PENDING only when name, father's name, domicile, CNIC and the
  student photograph are all valid and stored.
* FEE_PENDING waits for an actual fee receipt image. Text alone never advances.
* FEE_SUBMITTED only after the receipt image is downloaded, validated and stored
  permanently. Temporary Meta URLs are never stored as permanent references.
* APPROVED and CONFIRMED are controlled by the admin approval workflow.

The LLM (when configured) may help interpret free-form messages, but every value
it suggests is validated here before persistence. The LLM never invents CNICs,
names, domiciles, image URLs, payments or approvals.
"""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from groq import AsyncGroq
from openai import AsyncOpenAI

from ..config import settings
from ..database import SessionLocal
from ..models.batch import Batch
from ..schemas.batch import BatchResponse
from ..services.admission_validation import (
    FieldResult,
    all_admission_requirements_complete,
    all_text_fields_complete,
    fee_receipt_stored,
    missing_text_fields,
    photograph_stored,
    validate_fee_receipt,
    validate_id_card_number,
    validate_student_photograph,
    TEXT_VALIDATORS,
)
from ..services.storage_service import (
    FOLDER_FEE_RECEIPTS,
    FOLDER_STUDENT_PHOTOS,
    get_storage_provider,
)
from ..services.admission_service import get_or_create_student
from ..services.batch_service import get_batches
from ..services.knowledge_service import format_context, retrieve_knowledge
from ..utils.whatsapp import download_media, get_media_download_url, get_media_metadata

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = (
    "You are the admission assistant for PACE GK Academy, an online academy "
    "for general knowledge (GK) competitive-exam preparation. Answer general "
    "academy questions (batches, fees, timings, subjects, policies) clearly "
    "and briefly, and never invent facts you are not sure about. "
    "When a user asks about admission, guide them to choose a batch first, then "
    "help them complete the admission form (name, father's name, domicile, CNIC, "
    "student photograph) and finally submit the fee payment receipt. "
    "Keep replies short, friendly and WhatsApp-friendly."
)

GROUNDING_SYSTEM_PROMPT = (
    "You are the admission assistant for PACE GK Academy, an online academy "
    "for general knowledge (GK) competitive-exam preparation. Answer using ONLY "
    "the provided knowledge base context. "
    "If the context does not contain the answer, clearly say you cannot confirm "
    "it and that the admission team will follow up. "
    "Never invent facts, fees, dates, eligibility criteria, vacancies, timings or "
    "invite links that are not present in the context. "
    "When a user asks about admission, guide them to choose a batch first, then "
    "help them complete the admission form (name, father's name, domicile, CNIC, "
    "student photograph) and finally submit the fee payment receipt. "
    "Keep replies short, friendly and WhatsApp-friendly."
)

NO_INFORMATION_REPLY = (
    "Assalam-o-Alaikum! I could not find specific information about that in our "
    "knowledge base at the moment. Our admission team will review your question "
    "and get back to you with the correct details. In the meantime, which admission "
    "batch are you interested in? Please share the batch code (e.g., NOV26-1A)."
)

DETAILS_EXTRACTION_PROMPT = (
    "You are the admission desk assistant for PACE GK Academy. Read the "
    "conversation and extract the applicant's admission details. Respond with "
    "ONLY a JSON object using these exact keys: 'name', 'father_name', "
    "'domicile', 'id_card_number', 'student_pic_url', 'fee_paid_pic_url', "
    "'fee_amount', 'fee_category'. Use empty strings for anything not provided. "
    "Never invent values."
)

DEFAULT_REPLY_WITHOUT_KEY = (
    "Assalam-o-Alaikum! Welcome to PACE GK Academy admission. "
    "Which admission batch are you interested in? Please share the batch code."
)

BATCH_CONFIRMED_REPLY = (
    "Great choice! {batch_label} is confirmed. "
    "Please share your admission details: full name, father's name, domicile, "
    "CNIC (ID card number), and a student photograph. You can send these across "
    "one or more messages."
)

FORM_FILL_REPLY = (
    "To complete your admission form I still need: {missing}. "
    "Please send the missing detail(s). {photo_prompt}"
)

FEE_PENDING_REPLY = (
    "Thank you, your admission details are complete. "
    "Please submit the fee payment and send a clear photo/screenshot of the fee "
    "receipt as proof of payment."
)

FEE_RECEIVED_REPLY = (
    "Assalam-o-Alaikum! We have received your fee payment receipt. "
    "Our admission team is now reviewing your payment for human verification. "
    "Please wait; you will be notified once verification is complete."
)

FEE_SUBMITTED_WAIT_REPLY = (
    "Your fee payment receipt is with our admission team for human verification. "
    "Please wait while we verify the payment. You will be notified once the "
    "verification is complete."
)

IMAGE_NOT_ALLOWED_REPLY = (
    "I can only accept a student photograph after your batch is confirmed and "
    "your admission details are complete, and a fee receipt after all details "
    "are complete. Please complete the admission form first."
)

IMAGE_RECEIVED_REPLY = (
    "We received your image, but there was a problem processing it: {error}. "
    "Please try uploading it again."
)


def now_utc() -> datetime:
    """Timezone-aware UTC now (replaces deprecated datetime.utcnow)."""
    return datetime.now(timezone.utc)


def _looks_like_name(value: str) -> bool:
    cleaned = value.strip()
    if len(cleaned) < 2:
        return False
    if not re.match(r"^[A-Za-z][A-Za-z'.\s]{1,}$", cleaned):
        return False
    if re.search(r"\d", cleaned):
        return False
    return True


def _strip_trailing_keywords(value: str) -> str:
    """Cut a value off at the next known field keyword."""
    return re.split(
        r"\s+(?:father|domicile|id|cnic|whatsapp|phone)\b", value, flags=re.IGNORECASE
    )[0].strip()


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

    def _grounded_system_prompt(self, context: str) -> str:
        """Build the system prompt with the retrieved RAG context (grounding)."""
        system = GROUNDING_SYSTEM_PROMPT
        if context and context.strip():
            system += (
                "\n\nKnowledge base context (answer the student using ONLY this context):\n"
                + context.strip()
            )
        else:
            system += (
                "\n\nKnowledge base context: none available for this question. "
                "Do not answer from memory; tell the student you cannot confirm and "
                "offer human follow-up."
            )
        return system

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

    async def _extract_details_llm(self, messages: List[Dict[str, Any]]) -> Dict[str, Any]:
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

    def _extract_details_deterministic(self, text: str) -> Dict[str, str]:
        """Extract admission details from free text without an LLM.

        Handles labelled fields ("father name Rehman", "domicile Lahore"),
        CNIC patterns, and a leading bare name ("Ali Khan, ...").
        """
        found: Dict[str, str] = {}
        if not text:
            return found

        # CNIC anywhere in the message (formatted or 13 digits). Raw value kept;
        # normalization happens in validate_id_card_number before persistence.
        m = re.search(r"\d{5}-\d{7}-\d{1}|\b\d{13}\b", text)
        if m:
            found["id_card_number"] = m.group(0)

        # Leading bare name (e.g. "Ali Khan, father name ...") takes priority so
        # the "name" inside "father name" is not mistaken for the applicant name.
        if "name" not in found:
            first = text.split(",")[0].strip()
            if _looks_like_name(first):
                found["name"] = first

        # Label-based fields.
        patterns = {
            "father_name": [
                r"father['\s]?s?\s*name\s*:?\s*(.+)",
                r"father\s*:?\s*(.+)",
            ],
            "domicile": [
                r"domicile\s*:?\s*(.+)",
                r"city\s*:?\s*(.+)",
            ],
            # Negative lookbehind avoids matching the "name" inside "father name".
            "name": [
                r"(?:my\s+name\s+is|i\s+am|full\s+name\s+is)\s*(.+)",
                r"(?<!father\s)name\s*:?\s*(.+)",
            ],
        }
        for field, pats in patterns.items():
            if field in found:
                continue
            for pat in pats:
                m = re.search(pat, text, re.IGNORECASE)
                if m:
                    val = _strip_trailing_keywords(m.group(1).strip()).rstrip(",.;: ")
                    if val:
                        found[field] = val
                    break

        return found

    async def _extract_details(
        self, text: str, messages: List[Dict[str, Any]]
    ) -> Dict[str, Any]:
        """Combine LLM (if available) and deterministic extraction.

        LLM output is never trusted blindly; every value is validated by the
        caller before persistence. Deterministic extraction is the fallback when
        no LLM credentials are configured.
        """
        combined: Dict[str, Any] = {}
        if self._has_llm():
            try:
                llm = await self._extract_details_llm(messages)
            except Exception:
                logger.exception("LLM detail extraction failed; using deterministic fallback.")
                llm = {}
            combined.update(llm)
        deterministic = self._extract_details_deterministic(text)
        # Deterministic values take precedence for the fields they capture, but
        # only when non-empty (do not blank out an LLM value with an empty one).
        for k, v in deterministic.items():
            if v:
                combined[k] = v
        return combined

    def _apply_extracted(self, student, extracted: Dict[str, Any]) -> None:
        """Persist validated fields without overwriting valid data.

        A newly validated value is stored only if the current value is missing
        or invalid. Invalid extracted values are ignored (never written).
        """
        for field, validator in TEXT_VALIDATORS.items():
            if field not in extracted:
                continue
            result = validator(extracted.get(field))
            if not result.ok or not result.value:
                continue
            current = getattr(student, field, None)
            current_ok = validator(current).ok if current else False
            if not current_ok:
                setattr(student, field, result.value)

    # ------------------------------------------------------------------
    # Image intake: download -> validate -> permanent storage
    # ------------------------------------------------------------------
    async def _fetch_image_bytes(
        self, media_id: Optional[str], media_url: Optional[str]
    ) -> Optional[bytes]:
        if media_id:
            download_url = await get_media_download_url(media_id)
            return await download_media(download_url)
        if media_url:
            return await download_media(media_url)
        return None

    async def _handle_image(
        self,
        student,
        media_id: Optional[str],
        media_url: Optional[str],
        content_type: Optional[str],
        db,
    ) -> str:
        """Process an uploaded image according to the current workflow state."""
        try:
            file_bytes = await self._fetch_image_bytes(media_id, media_url)
        except Exception:
            logger.exception("Failed to download image media.")
            return IMAGE_RECEIVED_REPLY.format(error="could not download the image")

        if not file_bytes:
            return IMAGE_RECEIVED_REPLY.format(error="no image data received")

        size = len(file_bytes)
        # Determine the image purpose from the workflow state.
        if student.status == "FORM_FILL":
            validation = validate_student_photograph(content_type, size, file_bytes)
            folder = FOLDER_STUDENT_PHOTOS
            field = "student_pic_url"
            purpose = "student photograph"
        elif student.status == "FEE_PENDING":
            if not all_text_fields_complete(student):
                missing = ", ".join(missing_text_fields(student))
                return (
                    "Please complete your admission details first "
                    f"({missing}) before sending a fee receipt."
                )
            validation = validate_fee_receipt(content_type, size, file_bytes)
            folder = FOLDER_FEE_RECEIPTS
            field = "fee_paid_pic_url"
            purpose = "fee receipt"
        else:
            return IMAGE_NOT_ALLOWED_REPLY

        if not validation.ok:
            return IMAGE_RECEIVED_REPLY.format(error=validation.error or "invalid image")

        try:
            provider = get_storage_provider()
            permanent_ref = await provider.upload_image(
                file_bytes, content_type or "image/jpeg", folder
            )
        except Exception:
            logger.exception("Failed to store image permanently.")
            return IMAGE_RECEIVED_REPLY.format(error="could not store the image securely")

        setattr(student, field, permanent_ref)
        db.commit()

        if field == "student_pic_url":
            if all_text_fields_complete(student):
                student.status = "FEE_PENDING"
                db.commit()
                return FEE_PENDING_REPLY
            missing = missing_text_fields(student)
            return (
                f"{purpose} received and stored. "
                f"Still needed: {', '.join(missing)}."
            )
        else:
            # fee receipt stored -> FEE_SUBMITTED
            student.status = "FEE_SUBMITTED"
            if not student.submitted_at:
                student.submitted_at = now_utc()
            db.commit()
            return FEE_RECEIVED_REPLY

    # ------------------------------------------------------------------
    # Batch selection
    # ------------------------------------------------------------------
    async def get_available_batches(self) -> List[Dict[str, Any]]:
        db = SessionLocal()
        try:
            batches: List[BatchResponse] = get_batches(db)
        finally:
            db.close()
        return [b.model_dump() for b in batches]

    def _match_batch(self, text: str, batches: List[BatchResponse]) -> Optional[BatchResponse]:
        upper = text.upper()
        return next((b for b in batches if b.batch_code.upper() in upper), None)

    # ------------------------------------------------------------------
    # Main entry point
    # ------------------------------------------------------------------
    async def generate_reply(
        self,
        messages: List[Dict[str, Any]],
        phone_number: str,
        media_url: Optional[str] = None,
        media_type: Optional[str] = None,
        media_id: Optional[str] = None,
        content_type: Optional[str] = None,
    ) -> str:
        db = SessionLocal()
        try:
            student = get_or_create_student(db, phone_number)
            latest_text = (messages[-1].get("content") if messages else "") or ""
            upper_text = latest_text.upper()

            batches: List[BatchResponse] = get_batches(db)

            # A completed admission cycle starts a fresh inquiry when the student
            # messages again.
            if student.status == "CONFIRMED":
                student.status = "INQUIRY"
                student.batch_id = None
                db.commit()

            # ------------------------------------------------------------------
            # IMAGE messages
            # ------------------------------------------------------------------
            if media_type == "image" or media_id:
                return await self._handle_image(
                    student, media_id, media_url, content_type, db
                )

            # ------------------------------------------------------------------
            # TEXT messages
            # ------------------------------------------------------------------
            if student.status == "INQUIRY":
                matched_batch = self._match_batch(latest_text, batches)
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
                    return BATCH_CONFIRMED_REPLY.format(batch_label=batch_label)

                if not self._has_llm():
                    return (
                        "Welcome to PACE GK Academy! 🎓 Which admission batch are you interested in? "
                        "Please reply with the batch code (e.g., NOV26-1A)."
                    )
                # Grounded generation: retrieve relevant knowledge before answering.
                retrieved = await retrieve_knowledge(latest_text, top_k=settings.knowledge_top_k)
                context = format_context(retrieved)
                if not retrieved:
                    # Nothing found in the knowledge base -> safe, non-hallucinating reply.
                    return NO_INFORMATION_REPLY
                reply = await self._generate(
                    [
                        {"role": "system", "content": self._grounded_system_prompt(context)},
                        {"role": "user", "content": latest_text},
                    ]
                )
                return reply

            if student.status in ("BATCH_CONFIRMED", "FORM_FILL"):
                # BATCH_CONFIRMED -> FORM_FILL; then collect this message's details.
                if student.status == "BATCH_CONFIRMED":
                    student.status = "FORM_FILL"
                extracted = await self._extract_details(latest_text, messages)
                self._apply_extracted(student, extracted)
                db.commit()

                missing = missing_text_fields(student)
                if not missing and photograph_stored(student):
                    student.status = "FEE_PENDING"
                    db.commit()
                    return FEE_PENDING_REPLY
                return self._form_fill_prompt(student)

            if student.status == "FEE_PENDING":
                # Text alone never advances past FEE_PENDING.
                return FEE_PENDING_REPLY

            if student.status == "FEE_SUBMITTED":
                return FEE_SUBMITTED_WAIT_REPLY

            if student.status == "APPROVED":
                return (
                    "Your application has been approved. We are finalising your "
                    "batch access and will send the confirmation shortly."
                )

            return DEFAULT_REPLY_WITHOUT_KEY
        finally:
            db.close()

    def _form_fill_prompt(self, student) -> str:
        """Build the reply asking the student to complete remaining form fields."""
        missing = missing_text_fields(student)
        if not missing and photograph_stored(student):
            return FEE_PENDING_REPLY
        if not missing:
            return (
                "Your admission details are complete. "
                "Please upload your student photograph (JPG, PNG or WEBP)."
            )
        photo_prompt = (
            "You also need to upload a student photograph."
            if not photograph_stored(student)
            else "Your student photograph is on file."
        )
        return FORM_FILL_REPLY.format(
            missing=", ".join(missing), photo_prompt=photo_prompt
        )
