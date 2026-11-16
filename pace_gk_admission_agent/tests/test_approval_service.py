"""Unit tests for the approval service (human-in-the-loop approve/reject).

Approval now requires:
* the student to be in FEE_SUBMITTED,
* a stored fee receipt reference,
* complete admission details.

The LLM or student never sets the fee amount.
"""

from datetime import datetime

from app.models.batch import Batch
from app.models.student import Student
from app.services.approval_service import approve_student, reject_student


def _make_fee_submitted_student(
    db_session, batch_code="NOV26-1A", with_receipt=True, with_details=True
):
    batch = Batch(batch_code=batch_code, name="Batch 1A", fee_amount=15000.0)
    db_session.add(batch)
    db_session.flush()
    student = (
        db_session.query(Student).filter(Student.phone_number == "03333333333").first()
    )
    if student is None:
        student = Student(
            phone_number="03333333333",
            status="FEE_SUBMITTED",
            batch_id=batch.id,
            submitted_at=datetime.now(),
        )
    else:
        student.status = "FEE_SUBMITTED"
        student.batch_id = batch.id
        student.submitted_at = datetime.now()
    student.fee_paid_pic_url = (
        "https://storage.example.com/receipts/abc.jpg" if with_receipt else None
    )
    if with_details:
        student.name = "Ali Khan"
        student.father_name = "Rehman"
        student.domicile = "Lahore"
        student.id_card_number = "1234512345671"
    db_session.add(student)
    db_session.commit()
    return student


def test_approve_student_requires_complete_details_and_receipt(db_session):
    # Missing receipt -> cannot approve (batch 1A).
    student = _make_fee_submitted_student(db_session, batch_code="NOV26-1A", with_receipt=True, with_details=False)
    result = approve_student(db_session, "03333333333", "03367483185", category="NEW")
    assert result is None
    assert student.status == "FEE_SUBMITTED"

    # Missing details -> cannot approve (batch 1B).
    student = _make_fee_submitted_student(db_session, batch_code="NOV26-1B", with_receipt=False, with_details=True)
    result = approve_student(db_session, "03333333333", "03367483185", category="NEW")
    assert result is None
    assert student.status == "FEE_SUBMITTED"


def test_approve_student_requires_fee_submitted_state(db_session):
    batch = Batch(batch_code="NOV26-1A", name="Batch 1A", fee_amount=15000.0)
    db_session.add(batch)
    db_session.flush()
    student = Student(
        phone_number="03333333333",
        status="FORM_FILL",
        batch_id=batch.id,
        student_pic_url="https://storage.example.com/photo.jpg",
    )
    db_session.add(student)
    db_session.commit()

    result = approve_student(db_session, "03333333333", "03367483185", category="NEW")
    assert result is None
    assert student.status == "FORM_FILL"


def test_approve_student_transitions_to_approved(db_session):
    student = _make_fee_submitted_student(db_session)

    result = approve_student(db_session, "03333333333", "03367483185", category="NEW")

    assert result is not None
    assert result.status == "APPROVED"
    assert result.fee_category == "NEW"
    assert result.fee_amount == 15000.0
    assert result.fee_paid == "Yes"
    assert result.approved_by == "03367483185"
    assert result.approved_at is not None
    # Not CONFIRMED: confirmation is separate and notification-driven.
    assert result.status != "CONFIRMED"


def test_approve_student_fee_categories(db_session):
    batch = Batch(batch_code="NOV26-1A", name="Batch 1A", fee_amount=15000.0)
    db_session.add(batch)
    db_session.flush()

    cases = {
        "NEW": 15000.0,
        "OLD": 12000.0,
        "ORPHAN": 12000.0,
        "DISABLED": 12000.0,
        "NEEDY": 0.0,
    }
    for index, (category, expected) in enumerate(cases.items()):
        student = Student(
            phone_number=f"03333333{index:02d}",
            status="FEE_SUBMITTED",
            batch_id=batch.id,
            fee_paid_pic_url="https://storage.example.com/receipts/x.jpg",
            name="Ali", father_name="Rehman", domicile="Lahore",
            id_card_number="1234512345671",
        )
        db_session.add(student)
        db_session.commit()
        result = approve_student(db_session, student.phone_number, "03367483185", category=category)
        assert result is not None
        assert result.fee_category == category
        assert result.fee_amount == expected, f"{category}: expected {expected}, got {result.fee_amount}"
        assert result.status == "APPROVED"


def test_approve_unknown_student_returns_none(db_session):
    assert approve_student(db_session, "0000000000", "03367483185", category="NEW") is None


def test_reject_student_sets_rejected(db_session):
    student = _make_fee_submitted_student(db_session)

    result = reject_student(db_session, "03333333333", "03367483185", reason="unclear receipt")

    assert result is not None
    assert result.status == "REJECTED"
    assert result.rejection_reason == "unclear receipt"
    assert result.approved_by == "03367483185"
    assert result.approved_at is not None


def test_reject_student_from_approved_state(db_session):
    student = _make_fee_submitted_student(db_session)
    approve_student(db_session, "03333333333", "03367483185", category="NEW")

    result = reject_student(db_session, "03333333333", "03367483185", reason="changed mind")
    assert result is not None
    assert result.status == "REJECTED"


def test_reject_unknown_student_returns_none(db_session):
    assert reject_student(db_session, "0000000000", "03367483185", reason="x") is None
