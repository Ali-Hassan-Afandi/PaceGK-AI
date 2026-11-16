"""End-to-end admission flow test.

Covers the corrected human-in-the-loop admission funnel:

    visitor asks for a batch
        -> BATCH_CONFIRMED
    student submits admission details
        -> FORM_FILL (not FEE_SUBMITTED; photograph still missing)
    student uploads photograph
        -> FEE_PENDING (all 5 details + photo stored)
    student uploads fee receipt
        -> FEE_SUBMITTED (receipt persistently stored)
    admin approves
        -> APPROVED (receipt + details verified by the admin)
    WhatsApp notifications accepted
        -> CONFIRMED
    admission batch data sheet exported
"""

import os

from app.models.student import Student


def _meta_image_post(client, phone, sid, media_id="media-id-1"):
    return client.post(
        "/whatsapp/webhook",
        json={
            "entry": [
                {
                    "changes": [
                        {
                            "value": {
                                "messages": [
                                    {
                                        "id": sid,
                                        "from": phone,
                                        "type": "image",
                                        "image": {
                                            "id": media_id,
                                            "mime_type": "image/jpeg",
                                        },
                                    }
                                ]
                            }
                        }
                    ]
                }
            ]
        },
        headers={"content-type": "application/json"},
    )


def _fake_image_layer(monkeypatch):
    """Fake Meta media download + permanent storage for the agent."""
    import app.agents.admission_agent as agent_module

    uploads = []

    class FakeStorage:
        async def upload_image(self, file_bytes, content_type, folder):
            uploads.append(folder)
            return f"https://storage.example.com/{folder}/{len(uploads)}.jpg"

        async def delete_image(self, reference):
            return True

        def get_public_url(self, reference):
            return reference

    async def _fake_download_url(media_id):
        return "https://download.example.com/media"

    async def _fake_download(url):
        return b"\xff\xd8\xff" + b"\x00" * 100

    monkeypatch.setattr(agent_module, "get_storage_provider", lambda: FakeStorage())
    monkeypatch.setattr(agent_module, "get_media_download_url", _fake_download_url)
    monkeypatch.setattr(agent_module, "download_media", _fake_download)
    return uploads


def test_full_admission_flow(client, db_session, seed_batch, whatsapp_sender, admin_numbers, monkeypatch):
    """Complete corrected admission funnel over the WhatsApp webhook."""
    uploads = _fake_image_layer(monkeypatch)

    student_phone = "03333333333"
    admin_phone = "03367483185"

    # --- Step 1: student asks for a batch -> BATCH_CONFIRMED ---------------
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

    # --- Step 2: student submits admission details -> FORM_FILL ------------
    r = client.post(
        "/whatsapp/webhook",
        data={
            "Body": "Ali Khan, father name Rehman, domicile Lahore, ID card 12345-1234567-1, WhatsApp 03333333333",
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
    assert student.status == "FORM_FILL"
    assert student.name == "Ali Khan"
    assert student.id_card_number == "1234512345671"
    # Photograph still missing -> not FEE_PENDING / not FEE_SUBMITTED.
    assert student.student_pic_url is None

    # --- Step 3: student uploads photograph -> FEE_PENDING -----------------
    r = _meta_image_post(client, student_phone, "e2e-003")
    assert r.status_code == 200

    db_session.expire_all()
    student = (
        db_session.query(Student)
        .filter(Student.phone_number == student_phone)
        .first()
    )
    assert student.status == "FEE_PENDING"
    assert student.student_pic_url is not None

    # --- Step 4: student uploads fee receipt -> FEE_SUBMITTED --------------
    r = _meta_image_post(client, student_phone, "e2e-004")
    assert r.status_code == 200

    db_session.expire_all()
    student = (
        db_session.query(Student)
        .filter(Student.phone_number == student_phone)
        .first()
    )
    assert student.status == "FEE_SUBMITTED"
    assert student.fee_paid_pic_url is not None
    # Photograph and receipt are stored separately (different folders).
    assert any(f == "student_photos" for f in uploads)
    assert any(f == "fee_receipts" for f in uploads)

    # --- Step 5: admin approves (human-verified payment) -> APPROVED -------
    r = client.post(
        "/whatsapp/webhook",
        data={
            "Body": f"APPROVE {student_phone} NEW",
            "From": f"whatsapp:{admin_phone}",
            "To": "whatsapp:+14155238886",
            "MessageSid": "e2e-005",
        },
    )
    assert r.status_code == 200

    db_session.expire_all()
    student = (
        db_session.query(Student)
        .filter(Student.phone_number == student_phone)
        .first()
    )
    # APPROVED first; CONFIRMED only after notifications are accepted. The mocked
    # sender always accepts, so with accepted notifications the student moves on
    # to CONFIRMED (the retry/notification-failure path is covered elsewhere).
    assert student.status in ("APPROVED", "CONFIRMED")
    assert student.fee_category == "NEW"
    assert student.fee_amount == 15000.0
    assert student.fee_paid == "Yes"
    assert student.approved_by == admin_phone
    assert student.approved_at is not None

    # The mocked WhatsApp sender accepted the messages, so confirmation
    # notification was accepted -> CONFIRMED.
    db_session.expire_all()
    student = (
        db_session.query(Student)
        .filter(Student.phone_number == student_phone)
        .first()
    )
    assert student.status == "CONFIRMED"
    assert student.confirmation_sent_at is not None

    # --- Step 6: confirmation sent to the student --------------------------
    student_messages = [
        m for m in whatsapp_sender if student_phone in m["to"]
    ]
    assert len(student_messages) >= 1
    bodies = " ".join(m["body"] for m in student_messages)
    assert "Congratulations" in bodies

    # --- Step 7: admission batch data sheet exported -----------------------
    from pathlib import Path

    export_dir = Path(os.environ["ADMISSION_EXPORT_DIR"])
    batch_sheet = export_dir / "NOV26-1A_students.csv"
    assert batch_sheet.exists(), "admission batch data sheet should be exported"

    content = batch_sheet.read_text(encoding="utf-8")
    assert "Ali Khan" in content
    assert "NOV26-1A" in content
    assert "CONFIRMED" in content


def test_payment_for_one_batch_cannot_unlock_another_batch(
    client, db_session, seed_batch, whatsapp_sender, admin_numbers, monkeypatch
):
    """Cross-session isolation: a student's payment/approval must not confirm
    or grant batch access to a different student."""
    _fake_image_layer(monkeypatch)

    student_a = "03333333333"
    student_b = "03344444444"

    def bring_to_fee_submitted(phone, code):
        client.post(
            "/whatsapp/webhook",
            data={
                "Body": code,
                "From": f"whatsapp:{phone}",
                "To": "whatsapp:+14155238886",
                "MessageSid": f"fs-{phone}",
            },
        )
        client.post(
            "/whatsapp/webhook",
            data={
                "Body": "Ali Khan, father name Rehman, domicile Lahore, ID card 12345-1234567-1",
                "From": f"whatsapp:{phone}",
                "To": "whatsapp:+14155238886",
                "MessageSid": f"fs-details-{phone}",
            },
        )
        _meta_image_post(client, phone, f"fs-photo-{phone}")  # photograph -> FEE_PENDING
        _meta_image_post(client, phone, f"fs-receipt-{phone}")  # receipt -> FEE_SUBMITTED

    bring_to_fee_submitted(student_a, "NOV26-1A")
    bring_to_fee_submitted(student_b, "NOV26-1B")

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
    assert b.status == "FEE_SUBMITTED"  # B remains awaiting review
    assert a.batch_id != b.batch_id


def test_student_cannot_reach_fee_submitted_by_text_alone(
    client, db_session, seed_batch
):
    """Regression: text details without a photograph must never reach FEE_SUBMITTED."""
    client.post(
        "/whatsapp/webhook",
        data={
            "Body": "NOV26-1A",
            "From": "whatsapp:03333333333",
            "To": "whatsapp:+14155238886",
            "MessageSid": "text-001",
        },
    )
    client.post(
        "/whatsapp/webhook",
        data={
            "Body": "Ali Khan, father name Rehman, domicile Lahore, ID card 12345-1234567-1",
            "From": "whatsapp:03333333333",
            "To": "whatsapp:+14155238886",
            "MessageSid": "text-002",
        },
    )

    db_session.expire_all()
    student = (
        db_session.query(Student)
        .filter(Student.phone_number == "03333333333")
        .first()
    )
    assert student.status == "FORM_FILL"
    assert student.student_pic_url is None
    assert student.fee_paid_pic_url is None
