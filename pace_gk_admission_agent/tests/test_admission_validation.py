"""Unit tests for deterministic admission field validation."""

import pytest

from app.services import admission_validation as av


# ---------------------------------------------------------------------------
# Full name
# ---------------------------------------------------------------------------
def test_valid_full_name():
    result = av.validate_name("Ali Khan")
    assert result.ok is True
    assert result.value == "Ali Khan"


def test_valid_name_with_apostrophe_and_dots():
    result = av.validate_name("O'Brien Jr.")
    assert result.ok is True


def test_name_too_short():
    assert av.validate_name("A").ok is False


def test_name_placeholder_is_rejected():
    for placeholder in ("n/a", "none", "null", "test", "xxx", "tbd", "-"):
        assert av.validate_name(placeholder).ok is False


def test_name_with_digits_rejected():
    assert av.validate_name("Ali123").ok is False


def test_name_empty_rejected():
    assert av.validate_name("").ok is False
    assert av.validate_name(None).ok is False


# ---------------------------------------------------------------------------
# Father's name
# ---------------------------------------------------------------------------
def test_valid_father_name():
    result = av.validate_father_name("Rehman")
    assert result.ok is True
    assert result.value == "Rehman"


def test_father_name_placeholder_rejected():
    assert av.validate_father_name("n/a").ok is False
    assert av.validate_father_name(None).ok is False


# ---------------------------------------------------------------------------
# Domicile
# ---------------------------------------------------------------------------
def test_valid_domicile():
    result = av.validate_domicile("Lahore")
    assert result.ok is True
    assert result.value == "Lahore"


def test_domicile_with_digits_rejected():
    assert av.validate_domicile("Lahore123").ok is False


def test_domicile_placeholder_rejected():
    assert av.validate_domicile("none").ok is False


# ---------------------------------------------------------------------------
# CNIC
# ---------------------------------------------------------------------------
def test_valid_cnic_13_digits():
    result = av.validate_id_card_number("1234512345671")
    assert result.ok is True
    assert result.value == "1234512345671"


def test_valid_cnic_formatted():
    result = av.validate_id_card_number("12345-1234567-1")
    assert result.ok is True
    assert result.value == "1234512345671"  # normalized to 13 digits


def test_valid_cnic_with_spaces():
    result = av.validate_id_card_number("12345 1234567 1")
    assert result.ok is True
    assert result.value == "1234512345671"


def test_invalid_cnic_wrong_length():
    assert av.validate_id_card_number("12345").ok is False
    assert av.validate_id_card_number("12345123456712").ok is False


def test_invalid_cnic_non_numeric():
    assert av.validate_id_card_number("12345-1234567-A").ok is False


def test_invalid_cnic_placeholder():
    assert av.validate_id_card_number("0000000000000").ok is False


def test_missing_cnic_rejected():
    assert av.validate_id_card_number("").ok is False
    assert av.validate_id_card_number(None).ok is False


# ---------------------------------------------------------------------------
# Missing fields summary
# ---------------------------------------------------------------------------
def _student(**kwargs):
    from app.models.student import Student

    return Student(phone_number="03333333333", **kwargs)


def test_missing_text_fields_reports_all_missing():
    student = _student()
    assert set(av.missing_text_fields(student)) == {
        "name", "father_name", "domicile", "id_card_number",
    }


def test_all_text_fields_complete_when_all_present():
    student = _student(
        name="Ali", father_name="Rehman", domicile="Lahore",
        id_card_number="1234512345671",
    )
    assert av.all_text_fields_complete(student) is True


def test_all_text_fields_complete_fails_when_any_missing():
    student = _student(name="Ali", father_name="Rehman", domicile="Lahore")
    assert av.all_text_fields_complete(student) is False


# ---------------------------------------------------------------------------
# Photograph / receipt validation
# ---------------------------------------------------------------------------
def _jpeg_bytes() -> bytes:
    return b"\xff\xd8\xff" + b"\x00" * 100


def _png_bytes() -> bytes:
    return b"\x89PNG\r\n\x1a\n" + b"\x00" * 100


def test_valid_student_photograph_jpeg():
    result = av.validate_student_photograph("image/jpeg", 2000, _jpeg_bytes())
    assert result.ok is True


def test_valid_student_photograph_png():
    result = av.validate_student_photograph("image/png", 2000, _png_bytes())
    assert result.ok is True


def test_invalid_image_mime_type_rejected():
    result = av.validate_student_photograph("text/plain", 100, b"hello")
    assert result.ok is False


def test_invalid_image_size_too_large_rejected():
    big = b"\xff\xd8\xff" + b"\x00" * (10 * 1024 * 1024)
    result = av.validate_student_photograph("image/jpeg", len(big), big)
    assert result.ok is False


def test_invalid_image_size_too_small_rejected():
    assert av.validate_student_photograph("image/jpeg", 10, b"\xff\xd8\xff").ok is False


def test_empty_image_rejected():
    assert av.validate_student_photograph("image/jpeg", 0, b"").ok is False


def test_non_image_content_with_image_mime_rejected():
    assert av.validate_student_photograph("image/jpeg", 100, b"this is not a jpeg").ok is False


def test_fee_receipt_same_rules_as_photograph():
    result = av.validate_fee_receipt("image/png", 2000, _png_bytes())
    assert result.ok is True


# ---------------------------------------------------------------------------
# Stored-reference helpers
# ---------------------------------------------------------------------------
def test_photograph_stored_false_when_no_url():
    assert av.photograph_stored(_student()) is False


def test_photograph_stored_true_when_url_present():
    assert av.photograph_stored(_student(student_pic_url="https://storage.example.com/photo.jpg")) is True


def test_fee_receipt_stored_false_when_no_url():
    assert av.fee_receipt_stored(_student()) is False


# ---------------------------------------------------------------------------
# Deterministic extraction (no LLM) via AdmissionAgent
# ---------------------------------------------------------------------------
def test_deterministic_extracts_labelled_fields(db_session):
    from app.agents.admission_agent import AdmissionAgent

    agent = AdmissionAgent()
    found = agent._extract_details_deterministic(
        "Ali Khan, father name Rehman, domicile Lahore, ID card 12345-1234567-1"
    )
    assert found["name"] == "Ali Khan"
    assert found["father_name"] == "Rehman"
    assert found["domicile"] == "Lahore"
    assert found["id_card_number"] == "12345-1234567-1"


def test_deterministic_extracts_formatted_cnic(db_session):
    from app.agents.admission_agent import AdmissionAgent

    agent = AdmissionAgent()
    found = agent._extract_details_deterministic(
        "name: Ali Khan, father name: Rehman, domicile: Lahore, CNIC: 12345-1234567-1"
    )
    # Extraction returns the raw value; normalization happens at validation time.
    assert found["id_card_number"] == "12345-1234567-1"
    assert av.validate_id_card_number("12345-1234567-1").value == "1234512345671"


def test_apply_extracted_keeps_first_valid_value_and_ignores_invalid(db_session):
    from app.agents.admission_agent import AdmissionAgent
    from app.models.student import Student

    agent = AdmissionAgent()
    student = Student(phone_number="03333333333", name="Ali Khan")
    agent._apply_extracted(student, {"name": "", "father_name": "Rehman"})
    # Empty name must not overwrite the valid existing name.
    assert student.name == "Ali Khan"
    # Valid father name is stored.
    assert student.father_name == "Rehman"
