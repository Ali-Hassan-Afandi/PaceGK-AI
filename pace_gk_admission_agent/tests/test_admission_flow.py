"""End-to-end admission flow test.

Covers the complete human-in-the-loop admission funnel:

    visitor asks for a batch
        -> batch confirmed (BATCH_CONFIRMED)
    student submits admission details
        -> fee submission queued (FEE_SUBMITTED)
    admin approves the student
        -> fee categorized + marked paid + CONFIRMED
        -> confirmation sent via WhatsApp
        -> admission batch data sheet exported
"""

from app.models.student import Student


def test_full_admission_flow(client, db_session, seed_batch, whatsapp_sender, admin_numbers, monkeypatch):
    """Complete human-in-the-loop admission funnel over the WhatsApp webhook."""
    import app.routers.whatsapp as whatsapp_router

    # No LLM key is configured in tests, so the agent's deterministic path would
    # skip detail extraction. Patch the agent so the webhook path captures the
    # submitted admission details end-to-end (restored automatically after test).
    async def fake_extract_details(messages):
        return {
            "name": "Ali Khan",
            "father_name": "Rehman",
            "domicile": "Lahore",
            "id_card_number": "12345",
            "student_pic_url": "",
            "fee_paid_pic_url": "",
            "fee_amount": "",
            "fee_category": "",
        }

    monkeypatch.setattr(whatsapp_router.agent, "_has_llm", lambda: True)
    monkeypatch.setattr(whatsapp_router.agent, "_extract_details", fake_extract_details)

    student_phone = "03333333333"
    admin_phone = "03367483185"

    # --- Step 1: student asks about admission with a batch code -------------
    r = client.post(
        "/whatsapp/webhook",
        data={
            "Body": "NOV26-1A",
            "From": f"whatsapp:{student_phone}",
            "To": "whatsapp:+14155238886",
            "MessageSid": "e2e-001",
        },
    )
    assert r.status_code == 200

    db_session.expire_all()
    student = (
        db_session.query(Student)
        .filter(Student.phone_number == student_phone)
        .first()
    )
    assert student.status == "BATCH_CONFIRMED"
    assert student.batch_id is not None

    # --- Step 2: student submits admission details --------------------------
    r = client.post(
        "/whatsapp/webhook",
        data={
            "Body": "Ali Khan, father name Rehman, domicile Lahore, ID card 12345, WhatsApp 03333333333",
            "From": f"whatsapp:{student_phone}",
            "To": "whatsapp:+14155238886",
            "MessageSid": "e2e-002",
        },
    )
    assert r.status_code == 200

    db_session.expire_all()
    student = (
        db_session.query(Student)
        .filter(Student.phone_number == student_phone)
        .first()
    )
    assert student.status == "FEE_SUBMITTED"
    assert student.name == "Ali Khan"

    # --- Step 3: admin approves the student (human-in-the-loop) -------------
    r = client.post(
        "/whatsapp/webhook",
        data={
            "Body": f"APPROVE {student_phone} NEW",
            "From": f"whatsapp:{admin_phone}",
            "To": "whatsapp:+14155238886",
            "MessageSid": "e2e-003",
        },
    )
    assert r.status_code == 200

    db_session.expire_all()
    student = (
        db_session.query(Student)
        .filter(Student.phone_number == student_phone)
        .first()
    )
    assert student.status == "CONFIRMED"
    assert student.fee_category == "NEW"
    assert student.fee_amount == 15000.0
    assert student.fee_paid == "Yes"
    assert student.approved_by == admin_phone
    assert student.approved_at is not None

    # --- Step 4: confirmation sent to the student ---------------------------
    student_messages = [
        m for m in whatsapp_sender if student_phone in m["to"]
    ]
    assert len(student_messages) >= 1, "expected at least one WhatsApp message to the student"
    bodies = " ".join(m["body"] for m in student_messages)
    assert "Congratulations" in bodies

    # --- Step 5: admission batch data sheet exported ------------------------
    from pathlib import Path

    import os

    export_dir = Path(os.environ["ADMISSION_EXPORT_DIR"])
    batch_sheet = export_dir / "NOV26-1A_students.csv"
    assert batch_sheet.exists(), "admission batch data sheet should be exported"

    content = batch_sheet.read_text(encoding="utf-8")
    assert "Ali Khan" in content
    assert "NOV26-1A" in content
    assert "CONFIRMED" in content


def test_payment_for_one_batch_cannot_unlock_another_batch(
    client, db_session, seed_batch, whatsapp_sender, admin_numbers
):
    """Cross-session isolation: approving a student in batch 1A must not
    confirm or grant batch 1B access to a different student."""
    student_a = "03333333333"
    student_b = "03344444444"

    for phone, code in [(student_a, "NOV26-1A"), (student_b, "NOV26-1B")]:
        client.post(
            "/whatsapp/webhook",
            data={
                "Body": code,
                "From": f"whatsapp:{phone}",
                "To": "whatsapp:+14155238886",
                "MessageSid": f"iso-{phone}",
            },
        )
        client.post(
            "/whatsapp/webhook",
            data={
                "Body": "some admission details",
                "From": f"whatsapp:{phone}",
                "To": "whatsapp:+14155238886",
                "MessageSid": f"iso-details-{phone}",
            },
        )

    # Approve only student A.
    client.post(
        "/whatsapp/webhook",
        data={
            "Body": f"APPROVE {student_a} NEW",
            "From": "whatsapp:03367483185",
            "To": "whatsapp:+14155238886",
            "MessageSid": "iso-approve-a",
        },
    )

    db_session.expire_all()
    a = (
        db_session.query(Student)
        .filter(Student.phone_number == student_a)
        .first()
    )
    b = (
        db_session.query(Student)
        .filter(Student.phone_number == student_b)
        .first()
    )
    assert a.status == "CONFIRMED"
    assert b.status == "FEE_SUBMITTED"  # B must remain awaiting review
    assert a.batch_id != b.batch_id
