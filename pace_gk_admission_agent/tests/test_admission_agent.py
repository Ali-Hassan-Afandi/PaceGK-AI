"""Unit tests for the admission agent state machine (no LLM keys -> deterministic)."""

import pytest

from app.agents.admission_agent import AdmissionAgent


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
async def test_batch_confirmed_moves_to_form_fill(db_session, seed_batch):
    """BATCH_CONFIRMED must transition to FORM_FILL, not straight to FEE_SUBMITTED."""
    agent = AdmissionAgent()
    await _student(
        db_session, "03333333333", "BATCH_CONFIRMED", batch_code="NOV26-1T"
    )

    reply = await agent.generate_reply(
        messages=[
            {
                "role": "user",
                "content": "Ali Khan, father name Rehman, domicile Lahore, ID 12345-1234567-1",
            }
        ],
        phone_number="03333333333",
    )

    from app.models.student import Student

    db_session.expire_all()
    student = (
        db_session.query(Student).filter(Student.phone_number == "03333333333").first()
    )
    assert student.status == "FORM_FILL"
    assert student.name == "Ali Khan"
    assert student.father_name == "Rehman"
    assert student.domicile == "Lahore"
    assert student.id_card_number == "1234512345671"  # normalized CNIC
    assert "admission form" in reply.lower() or "detail" in reply.lower()


@pytest.mark.asyncio
async def test_form_fill_stays_form_fill_while_fields_incomplete(db_session, seed_batch):
    agent = AdmissionAgent()
    await _student(db_session, "03333333333", "FORM_FILL")

    reply = await agent.generate_reply(
        messages=[{"role": "user", "content": "Ali Khan"}],
        phone_number="03333333333",
    )

    db_session.expire_all()
    from app.models.student import Student

    student = (
        db_session.query(Student).filter(Student.phone_number == "03333333333").first()
    )
    assert student.status == "FORM_FILL"
    assert student.name == "Ali Khan"
    assert "father" in reply.lower() or "domicile" in reply.lower() or "CNIC" in reply


@pytest.mark.asyncio
async def test_form_fill_to_fee_pending_only_after_all_fields_and_photo(
    db_session, seed_batch
):
    """FORM_FILL -> FEE_PENDING only when all 5 fields valid AND photo stored."""
    agent = AdmissionAgent()
    await _student(
        db_session,
        "03333333333",
        "FORM_FILL",
        batch_code="NOV26-1T",
    )
    # Simulate the student photograph already stored (persisted field).
    from app.models.student import Student

    student = (
        db_session.query(Student).filter(Student.phone_number == "03333333333").first()
    )
    student.student_pic_url = "https://storage.example.com/photos/03333333333.jpg"
    db_session.commit()

    reply = await agent.generate_reply(
        messages=[
            {
                "role": "user",
                "content": "Ali Khan, father name Rehman, domicile Lahore, ID 12345-1234567-1",
            }
        ],
        phone_number="03333333333",
    )

    db_session.expire_all()
    student = (
        db_session.query(Student).filter(Student.phone_number == "03333333333").first()
    )
    assert student.status == "FEE_PENDING"
    assert "fee receipt" in reply.lower()


@pytest.mark.asyncio
async def test_text_only_details_never_produce_fee_submitted(db_session, seed_batch):
    """Sending text details alone must never reach FEE_SUBMITTED."""
    agent = AdmissionAgent()
    await _student(
        db_session,
        "03333333333",
        "BATCH_CONFIRMED",
        batch_code="NOV26-1T",
    )

    # Send all text details but no photograph.
    reply = await agent.generate_reply(
        messages=[
            {
                "role": "user",
                "content": "Ali Khan, father name Rehman, domicile Lahore, ID 12345-1234567-1",
            }
        ],
        phone_number="03333333333",
    )

    db_session.expire_all()
    from app.models.student import Student

    student = (
        db_session.query(Student).filter(Student.phone_number == "03333333333").first()
    )
    # Photo missing -> cannot reach FEE_PENDING/FEE_SUBMITTED.
    assert student.status == "FORM_FILL"
    assert student.student_pic_url is None


@pytest.mark.asyncio
async def test_fee_pending_waits_for_fee_receipt(db_session):
    agent = AdmissionAgent()
    await _student(db_session, "03333333333", "FEE_PENDING")

    reply = await agent.generate_reply(
        messages=[{"role": "user", "content": "I paid the fee"}],
        phone_number="03333333333",
    )

    db_session.expire_all()
    from app.models.student import Student

    student = (
        db_session.query(Student).filter(Student.phone_number == "03333333333").first()
    )
    assert student.status == "FEE_PENDING"
    assert "receipt" in reply.lower() or "image" in reply.lower()


@pytest.mark.asyncio
async def test_fee_receipt_upload_leads_to_fee_submitted(
    db_session, seed_batch, monkeypatch
):
    """A validated, persistently stored fee receipt moves FEE_PENDING -> FEE_SUBMITTED."""
    import app.agents.admission_agent as agent_module

    uploads = []

    class FakeStorage:
        async def upload_image(self, file_bytes, content_type, folder):
            uploads.append(folder)
            return f"https://storage.example.com/{folder}/receipt.jpg"

        async def delete_image(self, reference):
            return True

        def get_public_url(self, reference):
            return reference

    async def fake_download_url(media_id):
        return "https://download.example.com/media"

    async def fake_download(url):
        return b"\xff\xd8\xff" + b"\x00" * 100

    monkeypatch.setattr(agent_module, "get_storage_provider", lambda: FakeStorage())
    monkeypatch.setattr(agent_module, "get_media_download_url", fake_download_url)
    monkeypatch.setattr(agent_module, "download_media", fake_download)

    agent = AdmissionAgent()
    await _student(
        db_session,
        "03333333333",
        "FEE_PENDING",
        batch_code="NOV26-1T",
    )
    from app.models.student import Student

    student = (
        db_session.query(Student).filter(Student.phone_number == "03333333333").first()
    )
    student.name = "Ali Khan"
    student.father_name = "Rehman"
    student.domicile = "Lahore"
    student.id_card_number = "1234512345671"
    student.student_pic_url = "https://storage.example.com/photo.jpg"
    db_session.commit()

    reply = await agent.generate_reply(
        messages=[{"role": "user", "content": "here is my receipt"}],
        phone_number="03333333333",
        media_type="image",
        media_id="media-id-1",
        content_type="image/jpeg",
    )

    db_session.expire_all()
    student = (
        db_session.query(Student).filter(Student.phone_number == "03333333333").first()
    )
    assert student.status == "FEE_SUBMITTED"
    assert student.fee_paid_pic_url is not None
    assert "review" in reply.lower() or "verification" in reply.lower()
    assert any(f == "fee_receipts" for f in uploads)


@pytest.mark.asyncio
async def test_failed_image_storage_must_not_lead_to_fee_submitted(
    db_session, seed_batch, monkeypatch
):
    import app.agents.admission_agent as agent_module

    class FailingStorage:
        async def upload_image(self, file_bytes, content_type, folder):
            raise RuntimeError("storage down")

        async def delete_image(self, reference):
            return True

        def get_public_url(self, reference):
            return reference

    async def fake_download_url(media_id):
        return "https://download.example.com/media"

    async def fake_download(url):
        return b"\xff\xd8\xff" + b"\x00" * 100

    monkeypatch.setattr(agent_module, "get_storage_provider", lambda: FailingStorage())
    monkeypatch.setattr(agent_module, "get_media_download_url", fake_download_url)
    monkeypatch.setattr(agent_module, "download_media", fake_download)

    agent = AdmissionAgent()
    await _student(
        db_session,
        "03333333333",
        "FEE_PENDING",
        batch_code="NOV26-1T",
    )
    from app.models.student import Student

    student = (
        db_session.query(Student).filter(Student.phone_number == "03333333333").first()
    )
    student.name = "Ali Khan"
    student.father_name = "Rehman"
    student.domicile = "Lahore"
    student.id_card_number = "1234512345671"
    student.student_pic_url = "https://storage.example.com/photo.jpg"
    db_session.commit()

    reply = await agent.generate_reply(
        messages=[{"role": "user", "content": "receipt"}],
        phone_number="03333333333",
        media_type="image",
        media_id="media-id-1",
        content_type="image/jpeg",
    )

    db_session.expire_all()
    student = (
        db_session.query(Student).filter(Student.phone_number == "03333333333").first()
    )
    assert student.status == "FEE_PENDING"  # storage failure must not advance
    assert student.fee_paid_pic_url is None


@pytest.mark.asyncio
async def test_fee_submitted_remains_awaiting_review(db_session):
    agent = AdmissionAgent()
    await _student(db_session, "03333333333", "FEE_SUBMITTED")

    reply = await agent.generate_reply(
        messages=[{"role": "user", "content": "any update?"}],
        phone_number="03333333333",
    )

    assert "verification" in reply.lower() or "review" in reply.lower()
    db_session.expire_all()
    from app.models.student import Student

    student = (
        db_session.query(Student).filter(Student.phone_number == "03333333333").first()
    )
    assert student.status == "FEE_SUBMITTED"


@pytest.mark.asyncio
async def test_details_collected_across_multiple_messages(db_session, seed_batch):
    agent = AdmissionAgent()
    await _student(db_session, "03333333333", "FORM_FILL")

    await agent.generate_reply(
        messages=[{"role": "user", "content": "Ali Khan"}],
        phone_number="03333333333",
    )
    await agent.generate_reply(
        messages=[{"role": "user", "content": "father name Rehman"}],
        phone_number="03333333333",
    )
    await agent.generate_reply(
        messages=[{"role": "user", "content": "domicile Lahore"}],
        phone_number="03333333333",
    )
    await agent.generate_reply(
        messages=[{"role": "user", "content": "ID 12345-1234567-1"}],
        phone_number="03333333333",
    )

    db_session.expire_all()
    from app.models.student import Student

    student = (
        db_session.query(Student).filter(Student.phone_number == "03333333333").first()
    )
    assert student.name == "Ali Khan"
    assert student.father_name == "Rehman"
    assert student.domicile == "Lahore"
    assert student.id_card_number == "1234512345671"
    assert student.status == "FORM_FILL"  # photo still missing


@pytest.mark.asyncio
async def test_valid_info_not_overwritten_by_empty_values(db_session, seed_batch):
    agent = AdmissionAgent()
    await _student(db_session, "03333333333", "FORM_FILL")

    await agent.generate_reply(
        messages=[{"role": "user", "content": "Ali Khan, father name Rehman"}],
        phone_number="03333333333",
    )
    # Second message with empty/invalid name must not overwrite the valid name.
    await agent.generate_reply(
        messages=[{"role": "user", "content": "n/a"}],
        phone_number="03333333333",
    )

    db_session.expire_all()
    from app.models.student import Student

    student = (
        db_session.query(Student).filter(Student.phone_number == "03333333333").first()
    )
    assert student.name == "Ali Khan"
    assert student.father_name == "Rehman"


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


# ---------------------------------------------------------------------------
# RAG grounding tests
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_inquiry_answers_use_retrieved_knowledge_context(db_session, monkeypatch):
    """The agent must ground LLM answers in retrieved knowledge chunks, not free memory."""
    import app.agents.admission_agent as agent_module
    from app.services.knowledge_service import KnowledgeChunk
    from openai import AsyncOpenAI

    captured: dict = {}

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
        captured["messages"] = messages
        return "The November 2026 batch admission fee is 15000 rupees."

    agent_module.retrieve_knowledge = fake_retrieve  # type: ignore[assignment]
    agent_module.format_context = (
        lambda chunks: "Source: academy_doc/faq.docx\nThe November 2026 batch admission fee is 15000 rupees."
    )  # type: ignore[assignment]

    agent = AdmissionAgent(client=AsyncOpenAI(api_key="test-key"))
    monkeypatch.setattr(agent, "_generate", fake_generate)
    reply = await agent.generate_reply(
        messages=[{"role": "user", "content": "What is the November batch fee?"}],
        phone_number="03333333333",
    )
    assert reply == "The November 2026 batch admission fee is 15000 rupees."
    system_message = captured["messages"][0]
    assert "The November 2026 batch admission fee is 15000 rupees." in system_message["content"]


@pytest.mark.asyncio
async def test_inquiry_without_relevant_knowledge_returns_safe_fallback(db_session, monkeypatch):
    """When retrieval finds nothing, the agent must not answer from model memory."""
    import app.agents.admission_agent as agent_module
    from app.agents.admission_agent import NO_INFORMATION_REPLY
    from openai import AsyncOpenAI

    async def fake_retrieve(query, top_k=None, source_types=None):
        return []

    agent_module.retrieve_knowledge = fake_retrieve  # type: ignore[assignment]
    agent_module.format_context = lambda chunks: ""  # type: ignore[assignment]

    agent = AdmissionAgent(client=AsyncOpenAI(api_key="test-key"))
    monkeypatch.setattr(agent, "_generate", None)  # ensure no real LLM call happens

    reply = await agent.generate_reply(
        messages=[{"role": "user", "content": "Tell me something obscure nobody asked about."}],
        phone_number="03333333333",
    )
    assert reply == NO_INFORMATION_REPLY


# ---------------------------------------------------------------------------
# Simple memory tests (answer cache + conversation history)
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_repeated_question_reuses_cached_answer(db_session, monkeypatch):
    """A repeated question from the same student should not hit RAG + LLM again."""
    import app.agents.admission_agent as agent_module
    from app.services.knowledge_service import KnowledgeChunk
    from openai import AsyncOpenAI

    retrieve_calls = {"count": 0}

    async def fake_retrieve(query, top_k=None, source_types=None):
        retrieve_calls["count"] += 1
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

    agent_module.retrieve_knowledge = fake_retrieve  # type: ignore[assignment]
    agent_module.format_context = lambda chunks: "ctx"  # type: ignore[assignment]

    agent = AdmissionAgent(client=AsyncOpenAI(api_key="test-key"))
    monkeypatch.setattr(agent, "_generate", fake_generate)

    phone = "03344444444"
    first = await agent.generate_reply(
        messages=[{"role": "user", "content": "What is the batch fee?"}],
        phone_number=phone,
    )
    second = await agent.generate_reply(
        messages=[{"role": "user", "content": "What is the batch fee?"}],
        phone_number=phone,
    )
    assert first == second == "The November 2026 batch admission fee is 15000 rupees."
    assert retrieve_calls["count"] == 1  # RAG was not queried again for the repeat


@pytest.mark.asyncio
async def test_conversation_memory_carries_previous_turns(db_session, monkeypatch):
    """LLM conversation messages should include prior user/assistant turns."""
    import app.agents.admission_agent as agent_module
    from app.services.knowledge_service import KnowledgeChunk
    from openai import AsyncOpenAI

    captured: dict = {}

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
        captured.setdefault("messages", []).append(list(messages))
        return "The November 2026 batch admission fee is 15000 rupees."

    agent_module.retrieve_knowledge = fake_retrieve  # type: ignore[assignment]
    agent_module.format_context = lambda chunks: "ctx"  # type: ignore[assignment]

    agent = AdmissionAgent(client=AsyncOpenAI(api_key="test-key"))
    monkeypatch.setattr(agent, "_generate", fake_generate)

    phone = "03344444445"
    await agent.generate_reply(
        messages=[{"role": "user", "content": "What is the batch fee?"}],
        phone_number=phone,
    )
    await agent.generate_reply(
        messages=[{"role": "user", "content": "And what about the timings?"}],
        phone_number=phone,
    )

    assert len(captured["messages"]) == 2
    second_call_messages = captured["messages"][1]
    roles = [m["role"] for m in second_call_messages]
    assert "system" in roles
    assert "user" in roles
    assert "assistant" in roles
    # The second call must carry the previous Q&A turn into the LLM context.
    assert any(m.get("content") == "What is the batch fee?" for m in second_call_messages)