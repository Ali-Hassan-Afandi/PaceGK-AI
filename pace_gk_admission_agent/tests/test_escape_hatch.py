"""Regression tests for the admission-flow escape hatch.

A student stuck in BATCH_CONFIRMED / FORM_FILL must be able to:
* ask a general academy question and get a knowledge-base answer without
  leaving the admission state, and
* cancel / restart the flow at any time.
"""

import pytest

from app.agents.admission_agent import AdmissionAgent, NO_INFORMATION_REPLY


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


# ---------------------------------------------------------------------------
# General-question escape hatch
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_batch_confirmed_general_question_stays_in_batch_confirmed(
    db_session, seed_batch, monkeypatch
):
    """Asking a general question in BATCH_CONFIRMED must not advance to FORM_FILL."""
    import app.agents.admission_agent as agent_module
    from app.services.knowledge_service import KnowledgeChunk

    agent = AdmissionAgent()
    await _student(
        db_session, "03333333333", "BATCH_CONFIRMED", batch_code="NOV26-1T"
    )

    async def fake_retrieve(query, top_k=None, source_types=None):
        return [
            KnowledgeChunk(
                text="The November 2026 batch admission fee is 15000 rupees.",
                source="academy_doc/faq.docx",
                source_type="academy_doc",
                title="FAQ",
            )
        ]

    async def fake_generate(messages):
        return "The November 2026 batch admission fee is 15000 rupees."

    agent_module.retrieve_knowledge = fake_retrieve
    agent_module.format_context = (
        lambda chunks: "Source: academy_doc/faq.docx\nThe November 2026 batch admission fee is 15000 rupees."
    )
    monkeypatch.setattr(agent, "_generate", fake_generate)

    reply = await agent.generate_reply(
        messages=[{"role": "user", "content": "What is the batch fee?"}],
        phone_number="03333333333",
    )

    from app.models.student import Student

    db_session.expire_all()
    student = (
        db_session.query(Student).filter(Student.phone_number == "03333333333").first()
    )
    assert student.status == "BATCH_CONFIRMED"
    assert "15000 rupees" in reply


@pytest.mark.asyncio
async def test_form_fill_general_question_stays_in_form_fill(
    db_session, seed_batch, monkeypatch
):
    """Asking a general question in FORM_FILL must not advance to FEE_PENDING."""
    import app.agents.admission_agent as agent_module
    from app.services.knowledge_service import KnowledgeChunk

    agent = AdmissionAgent()
    await _student(db_session, "03333333333", "FORM_FILL")

    async def fake_retrieve(query, top_k=None, source_types=None):
        return [
            KnowledgeChunk(
                text="Classes run from 6 PM to 9 PM, Monday to Saturday.",
                source="academy_doc/schedule.docx",
                source_type="academy_doc",
                title="Schedule",
            )
        ]

    async def fake_generate(messages):
        return "Classes run from 6 PM to 9 PM, Monday to Saturday."

    agent_module.retrieve_knowledge = fake_retrieve
    agent_module.format_context = (
        lambda chunks: "Source: academy_doc/schedule.docx\nClasses run from 6 PM to 9 PM, Monday to Saturday."
    )
    monkeypatch.setattr(agent, "_generate", fake_generate)

    reply = await agent.generate_reply(
        messages=[{"role": "user", "content": "What are the class timings?"}],
        phone_number="03333333333",
    )

    from app.models.student import Student

    db_session.expire_all()
    student = (
        db_session.query(Student).filter(Student.phone_number == "03333333333").first()
    )
    assert student.status == "FORM_FILL"
    assert "6 PM to 9 PM" in reply


@pytest.mark.asyncio
async def test_batch_confirmed_general_question_no_knowledge_safe_fallback(
    db_session, seed_batch, monkeypatch
):
    """A general question with no knowledge match returns the safe fallback."""
    import app.agents.admission_agent as agent_module

    agent = AdmissionAgent()
    await _student(
        db_session, "03333333333", "BATCH_CONFIRMED", batch_code="NOV26-1T"
    )

    async def fake_retrieve(query, top_k=None, source_types=None):
        return []

    agent_module.retrieve_knowledge = fake_retrieve
    agent_module.format_context = lambda chunks: ""

    reply = await agent.generate_reply(
        messages=[{"role": "user", "content": "Where is the academy located?"}],
        phone_number="03333333333",
    )

    assert reply == NO_INFORMATION_REPLY


# ---------------------------------------------------------------------------
# Cancel / restart
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_batch_confirmed_cancel_resets_to_inquiry(db_session, seed_batch):
    """Sending cancel in BATCH_CONFIRMED resets the student to INQUIRY."""
    agent = AdmissionAgent()
    await _student(
        db_session, "03333333333", "BATCH_CONFIRMED", batch_code="NOV26-1T"
    )

    reply = await agent.generate_reply(
        messages=[{"role": "user", "content": "cancel"}],
        phone_number="03333333333",
    )

    from app.models.student import Student

    db_session.expire_all()
    student = (
        db_session.query(Student).filter(Student.phone_number == "03333333333").first()
    )
    assert student.status == "INQUIRY"
    assert student.batch_id is None
    assert "paused" in reply.lower() or "start again" in reply.lower()


@pytest.mark.asyncio
async def test_form_fill_cancel_resets_to_inquiry(db_session, seed_batch):
    """Sending cancel in FORM_FILL resets the student to INQUIRY."""
    agent = AdmissionAgent()
    await _student(db_session, "03333333333", "FORM_FILL")

    reply = await agent.generate_reply(
        messages=[{"role": "user", "content": "stop"}],
        phone_number="03333333333",
    )

    from app.models.student import Student

    db_session.expire_all()
    student = (
        db_session.query(Student).filter(Student.phone_number == "03333333333").first()
    )
    assert student.status == "INQUIRY"


# ---------------------------------------------------------------------------
# Non-admission text in BATCH_CONFIRMED does not auto-advance
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_batch_confirmed_plain_text_asks_for_details_without_advancing(
    db_session, seed_batch
):
    """Non-admission text in BATCH_CONFIRMED asks for details but stays BATCH_CONFIRMED."""
    agent = AdmissionAgent()
    await _student(
        db_session, "03333333333", "BATCH_CONFIRMED", batch_code="NOV26-1T"
    )

    # "xyz123" is not a general keyword, has no admission labels, and is not a
    # valid name (contains digits), so the deterministic extractor yields nothing
    # and the state must not advance.
    reply = await agent.generate_reply(
        messages=[{"role": "user", "content": "xyz123"}],
        phone_number="03333333333",
    )

    from app.models.student import Student

    db_session.expire_all()
    student = (
        db_session.query(Student).filter(Student.phone_number == "03333333333").first()
    )
    assert student.status == "BATCH_CONFIRMED"
    assert "admission details" in reply.lower()
