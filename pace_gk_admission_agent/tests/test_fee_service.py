"""Unit tests for the fee service (fee categorization logic)."""

from app.models.batch import Batch
from app.models.student import Student
from app.services.fee_service import apply_fee, mark_fee_paid


def _make_student(
    db_session,
    batch_fee=15000.0,
    status="FEE_SUBMITTED",
    batch_code="NOV26-1A",
    phone="03333333333",
):
    batch = Batch(batch_code=batch_code, name="Batch 1A", fee_amount=batch_fee)
    db_session.add(batch)
    db_session.flush()
    student = Student(
        phone_number=phone,
        status=status,
        batch_id=batch.id,
    )
    db_session.add(student)
    db_session.commit()
    return student


def test_apply_fee_new_student_pays_full(db_session):
    student = _make_student(db_session, batch_fee=15000.0)
    amount = apply_fee(db_session, student, "NEW")
    assert amount == 15000.0
    assert student.fee_category == "NEW"
    assert student.fee_amount == 15000.0


def test_apply_fee_old_student_gets_twenty_percent_off(db_session):
    student = _make_student(db_session, batch_fee=15000.0)
    amount = apply_fee(db_session, student, "OLD")
    assert amount == 12000.0
    assert student.fee_category == "OLD"
    assert student.fee_amount == 12000.0


def test_apply_fee_orphan_and_disabled_get_twenty_percent_off(db_session):
    for index, category in enumerate(("ORPHAN", "DISABLED")):
        student = _make_student(
            db_session,
            batch_fee=15000.0,
            batch_code=f"NOV26-1X{index}",
            phone=f"03333333{index:03d}",
        )
        amount = apply_fee(db_session, student, category)
        assert amount == 12000.0
        assert student.fee_category == category.upper()


def test_apply_fee_needy_is_free(db_session):
    student = _make_student(db_session, batch_fee=15000.0)
    amount = apply_fee(db_session, student, "NEEDY")
    assert amount == 0.0
    assert student.fee_category == "NEEDY"
    assert student.fee_amount == 0.0


def test_apply_fee_uses_default_full_fee_when_batch_fee_missing(db_session):
    student = _make_student(db_session, batch_fee=None)
    amount = apply_fee(db_session, student, "NEW")
    assert amount == 15000.0  # DEFAULT_FULL_FEE


def test_mark_fee_paid_records_payment(db_session):
    student = _make_student(db_session, batch_fee=15000.0)
    mark_fee_paid(db_session, student, "Yes")
    assert student.fee_paid == "Yes"
