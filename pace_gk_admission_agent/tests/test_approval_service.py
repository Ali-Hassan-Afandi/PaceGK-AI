"""Unit tests for the approval service (human-in-the-loop approve/reject)."""

from datetime import datetime

from app.models.batch import Batch
from app.models.student import Student
from app.services.approval_service import approve_student, reject_student


def _make_fee_submitted_student(db_session):
    batch = Batch(batch_code="NOV26-1A", name="Batch 1A", fee_amount=15000.0)
    db_session.add(batch)
    db_session.flush()
    student = Student(
        phone_number="03333333333",
        status="FEE_SUBMITTED",
        batch_id=batch.id,
        submitted_at=datetime.utcnow(),
    )
    db_session.add(student)
    db_session.commit()
    return student


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


def test_approve_student_requires_fee_submitted_state(db_session):
    # A student that has not submitted a fee cannot be approved.
    batch = Batch(batch_code="NOV26-1A", name="Batch 1A", fee_amount=15000.0)
    db_session.add(batch)
    db_session.flush()
    student = Student(phone_number="03333333333", status="INQUIRY", batch_id=batch.id)
    db_session.add(student)
    db_session.commit()

    result = approve_student(db_session, "03333333333", "03367483185", category="NEW")
    assert result is None
    # State must be unchanged.
    assert student.status == "INQUIRY"


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


def test_reject_unknown_student_returns_none(db_session):
    assert reject_student(db_session, "0000000000", "03367483185", reason="x") is None
