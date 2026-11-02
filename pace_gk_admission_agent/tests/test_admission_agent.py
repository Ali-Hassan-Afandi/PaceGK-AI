"""Unit tests for the admission agent state machine (no LLM keys -> deterministic)."""

import pytest

from app.agents.admission_agent import AdmissionAgent, FEE_RECEIVED_REPLY


@pytest.mark.asyncio
async def _student(db_session, phone, status, batch_code=None, batch_id=None):
    from app.models.batch import Batch
    from app.models.student import Student

    batch = None
    if batch_code:
        batch = Batch(batch_code=batch_code, name="Batch 1A", fee_amount=15000.0)
        db_session.add(batch)
        db_session.flush()
        batch_id = batch.id
    student = Student(phone_number=phone, status=status, batch_id=batch_id)
    db_session.add(student)
    db_session.commit()
    return student


@pytest.mark.asyncio
async def test_inquiry_with_batch_code_moves_to_batch_confirmed(db_session, seed_batch):
    agent = AdmissionAgent()
    await _student(db_session, "03333333333", "INQUIRY")

    reply = await agent.generate_reply(
        messages=[{"role": "user", "content": "NOV26-1A"}],
        phone_number="03333333333",
    )

    from app.models.student import Student

    db_session.expire_all()
    student = (
        db_session.query(Student).filter(Student.phone_number == "03333333333").first()
    )
    assert student.status == "BATCH_CONFIRMED"
    assert student.batch_id is not None
    assert "NOV 2026 Batch 1A" in reply


@pytest.mark.asyncio
async def test_inquiry_without_batch_code_asks_for_batch(db_session):
    agent = AdmissionAgent()
    await _student(db_session, "03333333333", "INQUIRY")

    reply = await agent.generate_reply(
        messages=[{"role": "user", "content": "I want admission"}],
        phone_number="03333333333",
    )

    assert "batch code" in reply.lower() or "batch" in reply.lower()
    from app.models.student import Student

    db_session.expire_all()
    student = (
        db_session.query(Student).filter(Student.phone_number == "03333333333").first()
    )
    assert student.status in ("INQUIRY", "AWAITING_BATCH")


@pytest.mark.asyncio
async def test_batch_confirmed_details_move_to_fee_submitted(db_session, seed_batch):
    agent = AdmissionAgent()
    await _student(
        db_session,
        "03333333333",
        "BATCH_CONFIRMED",
        batch_code="NOV26-1T",
    )

    reply = await agent.generate_reply(
        messages=[
            {
                "role": "user",
                "content": "Ali Khan, father name Rehman, domicile Lahore, ID 12345, "
                "WhatsApp 03333333333",
            }
        ],
        phone_number="03333333333",
    )

    from app.models.student import Student

    db_session.expire_all()
    student = (
        db_session.query(Student).filter(Student.phone_number == "03333333333").first()
    )
    assert student.status == "FEE_SUBMITTED"
    assert "fee submission" in reply.lower()


@pytest.mark.asyncio
async def test_fee_payment_picture_in_batch_confirmed_moves_to_fee_submitted(
    db_session, seed_batch
):
    agent = AdmissionAgent()
    await _student(
        db_session,
        "03333333333",
        "BATCH_CONFIRMED",
        batch_code="NOV26-1T",
    )

    reply = await agent.generate_reply(
        messages=[{"role": "user", "content": "here is my fee picture"}],
        phone_number="03333333333",
        media_url="https://storage.example.com/receipts/abc.png",
    )

    assert reply == FEE_RECEIVED_REPLY
    from app.models.student import Student

    db_session.expire_all()
    student = (
        db_session.query(Student).filter(Student.phone_number == "03333333333").first()
    )
    assert student.status == "FEE_SUBMITTED"
    assert student.fee_paid_pic_url == "https://storage.example.com/receipts/abc.png"


@pytest.mark.asyncio
async def test_fee_submitted_student_told_to_wait_for_review(db_session):
    agent = AdmissionAgent()
    await _student(db_session, "03333333333", "FEE_SUBMITTED")

    reply = await agent.generate_reply(
        messages=[{"role": "user", "content": "any update?"}],
        phone_number="03333333333",
    )

    assert "verification" in reply.lower() or "review" in reply.lower()
    from app.models.student import Student

    db_session.expire_all()
    student = (
        db_session.query(Student).filter(Student.phone_number == "03333333333").first()
    )
    assert student.status == "FEE_SUBMITTED"


@pytest.mark.asyncio
async def test_confirmed_student_starts_fresh_inquiry(db_session, seed_batch):
    agent = AdmissionAgent()
    await _student(
        db_session,
        "03333333333",
        "CONFIRMED",
        batch_code="NOV26-1T",
    )

    reply = await agent.generate_reply(
        messages=[{"role": "user", "content": "NOV26-1B"}],
        phone_number="03333333333",
    )

    from app.models.student import Student

    db_session.expire_all()
    student = (
        db_session.query(Student).filter(Student.phone_number == "03333333333").first()
    )
    assert student.status == "BATCH_CONFIRMED"
    assert student.batch_id is not None
