"""Deterministic admission field validation.

This module validates the five mandatory applicant fields without relying on the LLM:

1. Applicant full name
2. Father's name
3. Domicile
4. CNIC (identity card number)
5. Student photograph

The WhatsApp phone number is already available from the inbound message and is retained
separately.

All validation is deterministic. The LLM may help interpret free-form messages, but its
output is validated here before anything is persisted. Nothing is ever invented or
auto-corrected.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional, Tuple

# ---------------------------------------------------------------------------
# Placeholder / meaningless values that must be rejected for name-like fields.
# ---------------------------------------------------------------------------
PLACEHOLDER_VALUES = {
    "",
    "n/a",
    "na",
    "none",
    "null",
    "tbd",
    "xxx",
    "test",
    "abc",
    "nill",
    "nil",
    "-",
    "/",
    "...",
    "n.a",
    "n a",
    "not available",
    "not applicable",
    "unknown",
    "n\\a",
}

# A loosely meaningful name must contain at least two letters and no digits.
_NAME_RE = re.compile(r"^[A-Za-z][A-Za-z'.\s]{1,}$")
# A domicile should contain at least one letter and no digits.
_DOMICILE_RE = re.compile(r"^[A-Za-z][A-Za-z'.\s/-]{1,}$")

# CNIC: 13 digits, optionally formatted as 12345-1234567-1.
_CNIC_DIGITS_RE = re.compile(r"^\d{13}$")
_CNIC_FORMATTED_RE = re.compile(r"^\d{5}-\d{7}-\d{1}$")


@dataclass
class FieldResult:
    """Outcome of validating a single field."""

    ok: bool
    value: Optional[str] = None  # cleaned/normalized value when ok
    error: Optional[str] = None  # human-readable message when not ok


def _is_placeholder(value: Optional[str]) -> bool:
    if value is None:
        return True
    return value.strip().lower() in PLACEHOLDER_VALUES


# ---------------------------------------------------------------------------
# Full name
# ---------------------------------------------------------------------------
def validate_name(value: Optional[str]) -> FieldResult:
    """Validate the applicant's full name.

    Rules:
    - Must not be empty or a placeholder.
    - Must preserve the supplied name accurately (trimmed).
    - Must not contain digits (names are alphabetic).
    """
    if value is None:
        return FieldResult(False, error="Full name is required.")
    cleaned = value.strip()
    if _is_placeholder(cleaned):
        return FieldResult(False, error="Please provide a real full name (not a placeholder).")
    if len(cleaned) < 2:
        return FieldResult(False, error="Full name is too short. Please provide your full name.")
    if not _NAME_RE.match(cleaned):
        return FieldResult(False, error="Full name must contain only letters, spaces, apostrophes or dots.")
    return FieldResult(True, value=cleaned)


# ---------------------------------------------------------------------------
# Father's name
# ---------------------------------------------------------------------------
def validate_father_name(value: Optional[str]) -> FieldResult:
    """Validate the father's name (same rules as full name)."""
    if value is None:
        return FieldResult(False, error="Father's name is required.")
    cleaned = value.strip()
    if _is_placeholder(cleaned):
        return FieldResult(False, error="Please provide a real father's name (not a placeholder).")
    if len(cleaned) < 2:
        return FieldResult(False, error="Father's name is too short.")
    if not _NAME_RE.match(cleaned):
        return FieldResult(False, error="Father's name must contain only letters, spaces, apostrophes or dots.")
    return FieldResult(True, value=cleaned)


# ---------------------------------------------------------------------------
# Domicile
# ---------------------------------------------------------------------------
def validate_domicile(value: Optional[str]) -> FieldResult:
    """Validate the domicile (permanent address district/city)."""
    if value is None:
        return FieldResult(False, error="Domicile is required.")
    cleaned = value.strip()
    if _is_placeholder(cleaned):
        return FieldResult(False, error="Please provide a real domicile (not a placeholder).")
    if len(cleaned) < 2:
        return FieldResult(False, error="Domicile is too short.")
    if not _DOMICILE_RE.match(cleaned):
        return FieldResult(False, error="Domicile must not contain digits.")
    return FieldResult(True, value=cleaned)


# ---------------------------------------------------------------------------
# CNIC
# ---------------------------------------------------------------------------
def _strip_cnic_formatting(value: str) -> str:
    """Remove spaces and hyphens from a CNIC for digit-only validation."""
    return re.sub(r"[\s\-]+", "", value)


def validate_id_card_number(value: Optional[str]) -> FieldResult:
    """Validate and normalize a CNIC.

    Accepts:
    - 13 contiguous digits: 1234512345671
    - Standard formatting: 12345-1234567-1

    Rejects invalid lengths, non-numeric values, and obvious placeholders.
    Stores a consistent normalized 13-digit format.
    """
    if value is None:
        return FieldResult(False, error="CNIC (ID card number) is required.")
    cleaned = _strip_cnic_formatting(value.strip())
    if not cleaned:
        return FieldResult(False, error="CNIC is required.")
    if not cleaned.isdigit():
        return FieldResult(False, error="CNIC must contain only digits (optionally separated by hyphens).")
    if len(cleaned) != 13:
        return FieldResult(False, error="CNIC must be exactly 13 digits.")
    if cleaned == "0" * 13 or len(set(cleaned)) == 1:
        return FieldResult(False, error="CNIC appears to be a placeholder. Please provide a valid CNIC.")
    # Normalized storage format: 13 contiguous digits.
    return FieldResult(True, value=cleaned)


# ---------------------------------------------------------------------------
# Required-fields summary
# ---------------------------------------------------------------------------
REQUIRED_TEXT_FIELDS = ("name", "father_name", "domicile", "id_card_number")

TEXT_VALIDATORS = {
    "name": validate_name,
    "father_name": validate_father_name,
    "domicile": validate_domicile,
    "id_card_number": validate_id_card_number,
}


def missing_text_fields(student) -> Tuple[str, ...]:
    """Return the names of mandatory text fields that are not yet valid."""
    missing = []
    for field in REQUIRED_TEXT_FIELDS:
        value = getattr(student, field, None)
        if value is None or str(value).strip() == "":
            missing.append(field)
    return tuple(missing)


def all_text_fields_complete(student) -> bool:
    return len(missing_text_fields(student)) == 0


def photograph_stored(student) -> bool:
    return bool(getattr(student, "student_pic_url", None))


def fee_receipt_stored(student) -> bool:
    return bool(getattr(student, "fee_paid_pic_url", None))


def all_admission_requirements_complete(student) -> bool:
    """True only when all five mandatory fields are valid and the photo is stored."""
    return all_text_fields_complete(student) and photograph_stored(student)


# ---------------------------------------------------------------------------
# Student photograph validation (bytes + metadata)
# ---------------------------------------------------------------------------
ALLOWED_IMAGE_MIME_TYPES = {
    "image/jpeg",
    "image/jpg",
    "image/png",
    "image/webp",
}

MAX_IMAGE_SIZE_BYTES = 5 * 1024 * 1024  # 5 MB
MIN_IMAGE_SIZE_BYTES = 64  # below this is not a real image

# Magic-byte signatures for basic content validation (protects against
# renamed non-image files uploaded with an image MIME type).
_IMAGE_SIGNATURES = {
    "image/jpeg": b"\xff\xd8\xff",
    "image/jpg": b"\xff\xd8\xff",
    "image/png": b"\x89PNG\r\n\x1a\n",
    "image/webp": b"RIFF",
}


def validate_student_photograph(
    content_type: Optional[str],
    size: Optional[int],
    file_bytes: Optional[bytes],
) -> FieldResult:
    """Validate an uploaded student photograph.

    Accepts only approved image MIME types, validates file size and content
    (magic bytes), and rejects arbitrary URLs or text as proof of an image.
    """
    if content_type is None:
        return FieldResult(False, error="No image provided. Please upload a photo (JPG, PNG or WEBP).")
    mime = content_type.strip().lower()
    if mime not in ALLOWED_IMAGE_MIME_TYPES:
        return FieldResult(False, error="Unsupported image type. Please upload JPG, PNG or WEBP only.")
    if file_bytes is None or len(file_bytes) == 0:
        return FieldResult(False, error="The uploaded image is empty.")
    if size is not None:
        if size < MIN_IMAGE_SIZE_BYTES:
            return FieldResult(False, error="The image is too small to be a valid photograph.")
        if size > MAX_IMAGE_SIZE_BYTES:
            return FieldResult(False, error="The image is too large (maximum 5 MB).")
    # Content validation: confirm the file starts with a real image signature.
    signature = _IMAGE_SIGNATURES.get(mime)
    if signature is not None and file_bytes is not None:
        if not file_bytes.startswith(signature):
            # WEBP files can start with "RIFF" but must also contain "WEBP".
            if mime == "image/webp" and b"WEBP" not in file_bytes[:12]:
                return FieldResult(False, error="The uploaded file is not a valid image.")
            elif mime != "image/webp":
                return FieldResult(False, error="The uploaded file is not a valid image.")
    return FieldResult(True, value="stored")


def validate_fee_receipt(
    content_type: Optional[str],
    size: Optional[int],
    file_bytes: Optional[bytes],
) -> FieldResult:
    """Validate a fee payment receipt image (same rules as a photograph)."""
    return validate_student_photograph(content_type, size, file_bytes)
