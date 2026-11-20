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
from pathlib import Path
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
from ..services.knowledge_embedding_service import KnowledgeEmbeddingService
from ..services.knowledge_service import (
    SOURCE_TYPE_ACADEMY,
    format_context,
    retrieve_knowledge,
)
from ..services.storage_service import (
    FOLDER_FEE_RECEIPTS,
    FOLDER_STUDENT_PHOTOS,
    get_storage_provider,
)
from ..services.admission_service import get_or_create_student
from ..services.batch_service import get_batches
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
    "Assalam-o-Alaikum! Your admission details are complete. "
    "Please pay your fee using one of these methods:\n"
    "- Faysal Bank: PK75FAYS3286301000005585 (Account holder: Muhammad Javaid)\n"
    "- JazzCash: 0300-1864920 (Muhammad Javaid)\n"
    "- Easypaisa: 0345-0668111 (Muhammad Javaid)\n\n"
    "After payment, send a clear photo/screenshot of the fee receipt as proof of payment."
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
        # Simple in-memory conversation memory and answer cache (per WhatsApp number).
        self.conversations: Dict[str, List[Dict[str, str]]] = {}
        self.answer_cache: Dict[Tuple[str, str], str] = {}
        # Max stored conversation turns (user + assistant messages).
        self.MAX_CONVERSATION_TURNS = 10

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

    def _answer_cache_key(self, phone_number: str, text: str) -> Tuple[str, str]:
        """Cache key for identical repeated questions from the same student."""
        normalized = re.sub(r"\s+", " ", (text or "").strip().lower())
        return (phone_number, normalized)

    def _remember_answer(self, phone_number: str, text: str, reply: str) -> None:
        self.answer_cache[self._answer_cache_key(phone_number, text)] = reply

    def _record_turn(self, phone_number: str, role: str, content: str) -> None:
        turns = self.conversations.setdefault(phone_number, [])
        turns.append({"role": role, "content": content})
        max_turns = self.MAX_CONVERSATION_TURNS * 2
        while len(turns) > max_turns:
            turns.pop(0)

    def _conversation_history(self, phone_number: str) -> List[Dict[str, str]]:
        """Return the bounded recent conversation turns (user + assistant)."""
        turns = [
            t
            for t in (self.conversations.get(phone_number) or [])
            if (t.get("content") or "").strip()
        ]
        max_turns = self.MAX_CONVERSATION_TURNS * 2
        return turns[-max_turns:]

    def _has_llm(self) -> bool:
        if self.client is not None or self.groq_client is not None:
            return True
        return bool(settings.openai_api_key or settings.groq_api_key)

    async def _generate(self, messages: List[Dict[str, Any]]) -> str:
        provider = self._provider()
        try:
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
        except Exception:
            logger.exception("LLM chat completion failed; returning safe fallback.")
        return DEFAULT_REPLY_WITHOUT_KEY

    async def _generate_json(self, messages: List[Dict[str, Any]]) -> Dict[str, Any]:
        provider = self._provider()
        try:
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
        except Exception:
            logger.exception("LLM JSON generation failed; returning empty fallback.")
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
    # Intent detection & general Q&A escape hatch (so a student stuck in the
    # admission flow can still ask other academy questions).
    # ------------------------------------------------------------------

    # Commands that let a student pause/restart the admission flow at any time.
    CANCEL_COMMANDS = frozenset(
        {
            "cancel",
            "stop",
            "exit",
            "reset",
            "menu",
            "main menu",
            "start over",
            "restart",
        }
    )

    # Keywords that signal a general academy question (not admission form data).
    GENERAL_QUESTION_KEYWORDS = (
        "fee", "fees", "timing", "timings", "schedule", "subject",
        "course", "location", "address", "contact", "phone", "number",
        "help", "menu", "main menu", "about", "academy", "pace",
        "hello", "hi", "assalam", "salam", "good morning", "good evening",
        "what", "where", "when", "who", "how", "can i", "i want to know",
    )

    def _is_general_question(self, text: str) -> bool:
        """Heuristic: treat as a general academy question if it contains common
        inquiry keywords and is not a batch code or structured admission data."""
        t = (text or "").strip().lower()
        if not t:
            return False
        # A batch code (e.g. NOV26-1A) is admission intent, not a question.
        if re.fullmatch(r"[A-Z]{2,}\d{2,}[- ]?\d+[A-Z]?", t.upper()):
            return False
        # Structured admission data (labelled fields / CNIC pattern) is not a
        # general question even if it happens to contain a keyword.
        if re.search(r"\b(father\s*(['\s]?s?)?\s*name|domicile|id card)\b", t):
            return False
        if re.search(r"\d{5}-\d{7}-\d{1}|\b\d{13}\b", t):
            return False
        return any(k in t for k in self.GENERAL_QUESTION_KEYWORDS)

    # ------------------------------------------------------------------
    # Admission intent detection
    # ------------------------------------------------------------------

    ADMISSION_INTENT_KEYWORDS = (
        "admission", "admissions", "admit", "admitted",
        "register", "registration", "registered", "registering",
        "enroll", "enrollment", "enrolled", "enrolling",
        "apply", "application", "applied", "applying",
        "join", "joining", "joined",
        "admission form", "registration form", "enrollment form",
        "fee submission", "fee submit", "fee payment",
        "cnic", "id card", "identity card",
        "domicile", "father name", "father's name",
        "student photo", "student photograph", "student picture",
        "batch code", "batch selection", "choose batch",
    )

    def _is_admission_intent(self, text: str) -> bool:
        """True when the message signals admission / registration / enrollment intent.

        These messages should route to the admission state machine rather than
        being answered as general academy questions from DataDocs.
        """
        t = (text or "").strip().lower()
        if not t:
            return False
        # A batch code (e.g. NOV26-1A) is always admission intent.
        if re.fullmatch(r"[A-Z]{2,}\d{2,}[- ]?\d+[A-Z]?", t.upper()):
            return True
        # CNIC or structured admission fields are admission intent.
        if re.search(r"\b(father\s*(['\s]?s?)?\s*name|domicile|id card)\b", t):
            return True
        if re.search(r"\d{5}-\d{7}-\d{1}|\b\d{13}\b", t):
            return True
        return any(k in t for k in self.ADMISSION_INTENT_KEYWORDS)

    async def _answer_general_question(
        self,
        text: str,
        phone_number: str,
        messages: List[Dict[str, Any]],
    ) -> str:
        """Answer a general academy question from DataDocs only (general academy info).

        Tries persistent embedding-based retrieval first (one-time embedded corpus,
        incrementally updated), falling back to keyword-based retrieval from DataDocs.
        AdmissionDocs is never used for general questions.
        """
        retrieved_chunks: List[KnowledgeChunk] = []
        context = ""

        # 1) Try embedding-based retrieval from stored DataDocs embeddings.
        if settings.knowledge_embedding_enabled:
            try:
                async with KnowledgeEmbeddingService() as svc:
                    embedding_results = await svc.retrieve_from_embeddings(
                        text, source_folder="DataDocs", top_k=settings.knowledge_top_k
                    )
                for chunk_text, source_file, _folder, _score in embedding_results:
                    retrieved_chunks.append(
                        KnowledgeChunk(
                            text=chunk_text,
                            source=f"DataDocs/{source_file}",
                            source_type=SOURCE_TYPE_ACADEMY,
                            title=Path(source_file).stem,
                        )
                    )
            except Exception:
                logger.exception("Embedding-based general-question retrieval failed.")

        # 2) Fallback: keyword-based retrieval from DataDocs only.
        if not retrieved_chunks:
            retrieved = await retrieve_knowledge(
                text, top_k=settings.knowledge_top_k, source_types=[SOURCE_TYPE_ACADEMY]
            )
            context = format_context(retrieved)
            retrieved_chunks = list(retrieved)
        else:
            context = format_context(retrieved_chunks)

        if not retrieved_chunks:
            return NO_INFORMATION_REPLY
        self._record_turn(phone_number, "user", text)
        history = self._conversation_history(phone_number)
        reply = await self._generate(
            [{"role": "system", "content": self._grounded_system_prompt(context)}]
            + history
        )
        self._record_turn(phone_number, "assistant", reply)
        self._remember_answer(phone_number, text, reply)
        return reply

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
            # TEXT messages: cancel / general Q&A escape hatch (any text state)
            # ------------------------------------------------------------------
            if media_type != "image" and not media_id:
                lower = latest_text.strip().lower()
                if lower in self.CANCEL_COMMANDS:
                    student.status = "INQUIRY"
                    student.batch_id = None
                    db.commit()
                    return (
                        "Admission flow paused. Ask me any academy question, or "
                        "reply with a batch code (e.g., NOV26-1A) to start again."
                    )

                # General academy question -> answer from the knowledge base
                # without disturbing the admission state machine. Only the two
                # "stuck" states need this escape hatch; INQUIRY already handles
                # general questions, and FEE_PENDING / FEE_SUBMITTED keep their
                # receipt-reminder behaviour.
                if (
                    student.status in ("BATCH_CONFIRMED", "FORM_FILL")
                    and self._is_general_question(latest_text)
                ):
                    return await self._answer_general_question(
                        latest_text, phone_number, messages
                    )

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

                # Admission intent but no batch code matched yet -> ask for batch code.
                if self._is_admission_intent(latest_text):
                    return (
                        "Which admission batch are you interested in? "
                        "Please share the batch code (e.g., NOV26-1A)."
                    )

                # General academy question -> answer from DataDocs only.
                # Simple memory: skip RAG + LLM for repeated questions from the same student.
                cache_key = self._answer_cache_key(phone_number, latest_text)
                if cache_key in self.answer_cache:
                    return self.answer_cache[cache_key]

                return await self._answer_general_question(
                    latest_text, phone_number, messages
                )

            if student.status in ("BATCH_CONFIRMED", "FORM_FILL"):
                extracted = await self._extract_details(latest_text, messages)
                if student.status == "BATCH_CONFIRMED":
                    # Only advance to form fill when the message actually contains
                    # admission data. Non-admission text is already handled by the
                    # general-question escape hatch above; anything else just asks
                    # for the details without advancing the state.
                    if not extracted:
                        return (
                            "Please share your admission details: full name, "
                            "father's name, domicile, CNIC (ID card number). "
                            "You can send these across one or more messages, or "
                            "ask a general question about the academy."
                        )
                    student.status = "FORM_FILL"
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
