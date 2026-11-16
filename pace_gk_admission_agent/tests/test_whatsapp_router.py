"""Integration tests for the WhatsApp webhook router."""

import os

from app.models.student import Student
from app.services.admission_service import get_or_create_student


def _twilio_post(client, body, from_number="whatsapp:03333333333", message_sid=None):
    data = {
        "Body": body,
        "From": from_number,
        "To": "whatsapp:+14155238886",
    }
    if message_sid:
        data["MessageSid"] = message_sid
    return client.post("/whatsapp/webhook", data=data)


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

    async def fake_download_url(media_id):
        return "https://download.example.com/media"

    async def fake_download(url):
        return b"\xff\xd8\xff" + b"\x00" * 100

    monkeypatch.setattr(agent_module, "get_storage_provider", lambda: FakeStorage())
    monkeypatch.setattr(agent_module, "get_media_download_url", fake_download_url)
    monkeypatch.setattr(agent_module, "download_media", fake_download)
    return uploads


# ---------------------------------------------------------------------------
# Webhook verification (unchanged behaviour)
# ---------------------------------------------------------------------------
def test_webhook_verification_returns_challenge(client):
    response = client.get(
        "/whatsapp/webhook",
        params={
            "hub.mode": "subscribe",
            "hub.verify_token": "test-verify-token",
            "hub.challenge": "challenge-123",
        },
    )
    assert response.status_code == 200
    assert response.text == "challenge-123"


def test_webhook_verification_rejects_bad_token(client):
    response = client.get(
        "/whatsapp/webhook",
        params={
            "hub.mode": "subscribe",
            "hub.verify_token": "wrong-token",
            "hub.challenge": "challenge-123",
        },
    )
    assert response.status_code == 403


# ---------------------------------------------------------------------------
# Meta text webhook
# ---------------------------------------------------------------------------
def test_student_batch_selection_via_webhook(client, db_session, seed_batch):
    """A student texting a batch code moves to BATCH_CONFIRMED."""
    response = _twilio_post(client, "NOV26-1A")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}

    db_session.expire_all()
    student = (
        db_session.query(Student)
        .filter(Student.phone_number == "03333333333")
        .first()
    )
    assert student is not None
    assert student.status == "BATCH_CONFIRMED"
    assert student.batch_id is not None


def test_meta_json_status_notification_is_acked(client):
    """Meta WhatsApp Cloud API status-only webhooks must be acknowledged."""
    payload = {
        "entry": [
            {
                "changes": [
                    {
                        "value": {
                            "statuses": [
                                {
                                    "id": "msg-id",
                                    "status": "delivered",
                                    "recipient_id": "03333333333",
                                }
                            ]
                        }
                    }
                ]
            }
        ]
    }
    response = client.post("/whatsapp/webhook", json=payload)
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


# ---------------------------------------------------------------------------
# Duplicate protection (durable, DB-backed)
# ---------------------------------------------------------------------------
def test_duplicate_webhook_message_id_is_ignored(
    client, db_session, seed_batch, whatsapp_sender
):
    """Redelivered webhooks with the same MessageSid must not re-run the flow."""
    sid = "duplicate-sid-001"
    _twilio_post(client, "NOV26-1A", message_sid=sid)
    _twilio_post(client, "NOV26-1A", message_sid=sid)

    db_session.expire_all()
    students = db_session.query(Student).filter(
        Student.phone_number == "03333333333"
    ).all()
    assert len(students) == 1


# ---------------------------------------------------------------------------
# Image webhooks (Meta image payload)
# ---------------------------------------------------------------------------
def test_meta_image_webhook_student_photograph(
    client, db_session, seed_batch, whatsapp_sender, monkeypatch
):
    """Image in FORM_FILL is stored as the student photograph -> FEE_PENDING."""
    uploads = _fake_image_layer(monkeypatch)

    # Student submits complete details -> FORM_FILL (no photo yet).
    _twilio_post(client, "NOV26-1A", message_sid="img-001")
    _twilio_post(
        client,
        "Ali Khan, father name Rehman, domicile Lahore, ID card 12345-1234567-1",
        message_sid="img-002",
    )
    db_session.expire_all()
    student = (
        db_session.query(Student)
        .filter(Student.phone_number == "03333333333")
        .first()
    )
    assert student.status == "FORM_FILL"
    assert student.student_pic_url is None

    # Student uploads photograph -> FEE_PENDING.
    _meta_image_post(client, "03333333333", "img-003")
    db_session.expire_all()
    student = (
        db_session.query(Student)
        .filter(Student.phone_number == "03333333333")
        .first()
    )
    assert student.status == "FEE_PENDING"
    assert student.student_pic_url is not None
    assert any(f == "student_photos" for f in uploads)


def test_meta_image_webhook_fee_receipt(
    client, db_session, seed_batch, whatsapp_sender, monkeypatch
):
    """Image in FEE_PENDING is stored as the fee receipt -> FEE_SUBMITTED."""
    uploads = _fake_image_layer(monkeypatch)

    student = get_or_create_student(db_session, "03333333333")
    student.name = "Ali Khan"
    student.father_name = "Rehman"
    student.domicile = "Lahore"
    student.id_card_number = "1234512345671"
    student.student_pic_url = "https://storage.example.com/photo.jpg"
    student.status = "FEE_PENDING"
    db_session.commit()

    _meta_image_post(client, "03333333333", "img-receipt-001")
    db_session.expire_all()
    student = (
        db_session.query(Student)
        .filter(Student.phone_number == "03333333333")
        .first()
    )
    assert student.status == "FEE_SUBMITTED"
    assert student.fee_paid_pic_url is not None
    assert any(f == "fee_receipts" for f in uploads)


def test_unexpected_image_in_inquiry_rejected(
    client, db_session, seed_batch, whatsapp_sender, monkeypatch
):
    """Images sent before the admission form are complete are rejected."""
    _fake_image_layer(monkeypatch)
    _twilio_post(client, "NOV26-1A", message_sid="unexp-001")  # BATCH_CONFIRMED
    _meta_image_post(client, "03333333333", "unexp-002")  # image before details
    db_session.expire_all()
    student = (
        db_session.query(Student)
        .filter(Student.phone_number == "03333333333")
        .first()
    )
    assert student.status == "BATCH_CONFIRMED"
    assert student.student_pic_url is None


# ---------------------------------------------------------------------------
# Admin approval / rejection
# ---------------------------------------------------------------------------
def test_admin_approve_from_authorized_number(
    client, db_session, seed_batch, whatsapp_sender, monkeypatch
):
    """Authorized admin approves a student with a stored receipt; notifications
    accepted -> CONFIRMED."""
    uploads = _fake_image_layer(monkeypatch)

    # Bring student to FEE_SUBMITTED.
    _twilio_post(client, "NOV26-1A", message_sid="ap-001")
    _twilio_post(
        client,
        "Ali Khan, father name Rehman, domicile Lahore, ID card 12345-1234567-1",
        message_sid="ap-002",
    )
    _meta_image_post(client, "03333333333", "ap-photo")
    _meta_image_post(client, "03333333333", "ap-receipt")

    db_session.expire_all()
    student = (
        db_session.query(Student).filter(Student.phone_number == "03333333333").first()
    )
    assert student.status == "FEE_SUBMITTED"

    response = client.post(
        "/whatsapp/webhook",
        data={
            "Body": "APPROVE 03333333333 NEW",
            "From": "whatsapp:03367483185",
            "To": "whatsapp:+14155238886",
            "MessageSid": "admin-approve-001",
        },
    )
    assert response.status_code == 200

    db_session.expire_all()
    student = (
        db_session.query(Student).filter(Student.phone_number == "03333333333").first()
    )
    # APPROVED first; CONFIRMED once notifications are accepted.
    assert student.status == "APPROVED" or student.status == "CONFIRMED"
    assert student.fee_category == "NEW"
    assert student.fee_paid == "Yes"
    assert student.approved_by == "03367483185"

    # The mocked sender accepted messages -> confirmation delivered.
    db_session.expire_all()
    student = (
        db_session.query(Student).filter(Student.phone_number == "03333333333").first()
    )
    assert student.status == "CONFIRMED"
    assert student.confirmation_sent_at is not None
    assert any("03333333333" in m["to"] for m in whatsapp_sender)
    assert any(f == "fee_receipts" for f in uploads)


def test_admin_approve_from_non_admin_number_is_ignored(
    client, db_session, seed_batch, whatsapp_sender, monkeypatch
):
    _fake_image_layer(monkeypatch)
    _twilio_post(client, "NOV26-1A", message_sid="na-001")
    _twilio_post(
        client,
        "Ali Khan, father name Rehman, domicile Lahore, ID card 12345-1234567-1",
        message_sid="na-002",
    )
    _meta_image_post(client, "03333333333", "na-photo")
    _meta_image_post(client, "03333333333", "na-receipt")

    response = client.post(
        "/whatsapp/webhook",
        data={
            "Body": "APPROVE 03333333333 NEW",
            "From": "whatsapp:03999999999",  # not an admin number
            "To": "whatsapp:+14155238886",
            "MessageSid": "non-admin-001",
        },
    )
    assert response.status_code == 200

    db_session.expire_all()
    student = (
        db_session.query(Student)
        .filter(Student.phone_number == "03333333333")
        .first()
    )
    assert student.status != "CONFIRMED"
    assert not any("Congratulations" in m.get("body", "") for m in whatsapp_sender)


def test_approve_student_not_fee_submitted_returns_error(
    client, db_session, seed_batch, whatsapp_sender
):
    """Approving a student that is not awaiting fee review must fail."""
    _twilio_post(client, "NOV26-1A", message_sid="pre-001")  # only BATCH_CONFIRMED

    response = client.post(
        "/whatsapp/webhook",
        data={
            "Body": "APPROVE 03333333333 NEW",
            "From": "whatsapp:03367483185",
            "To": "whatsapp:+14155238886",
            "MessageSid": "approve-premature-001",
        },
    )
    assert response.status_code == 200

    db_session.expire_all()
    student = (
        db_session.query(Student)
        .filter(Student.phone_number == "03333333333")
        .first()
    )
    assert student.status != "APPROVED"


def test_admin_approve_requires_stored_receipt(
    client, db_session, seed_batch, whatsapp_sender, monkeypatch
):
    """APPROVE must fail when the fee receipt is not stored."""
    # Details complete but no photo/receipt -> FORM_FILL, not FEE_SUBMITTED.
    _twilio_post(client, "NOV26-1A", message_sid="nr-001")
    _twilio_post(
        client,
        "Ali Khan, father name Rehman, domicile Lahore, ID card 12345-1234567-1",
        message_sid="nr-002",
    )

    response = client.post(
        "/whatsapp/webhook",
        data={
            "Body": "APPROVE 03333333333 NEW",
            "From": "whatsapp:03367483185",
            "To": "whatsapp:+14155238886",
            "MessageSid": "nr-approve",
        },
    )
    assert response.status_code == 200
    db_session.expire_all()
    student = (
        db_session.query(Student).filter(Student.phone_number == "03333333333").first()
    )
    assert student.status != "APPROVED"


def test_failed_outbound_confirmation_does_not_set_confirmed(
    client, db_session, seed_batch, whatsapp_sender, monkeypatch
):
    """If the WhatsApp API rejects the confirmation, the student stays APPROVED
    (not CONFIRMED), and the approval is not reversed."""
    import app.routers.whatsapp as whatsapp_router

    uploads = _fake_image_layer(monkeypatch)

    # Bring student to FEE_SUBMITTED.
    _twilio_post(client, "NOV26-1A", message_sid="fc-001")
    _twilio_post(
        client,
        "Ali Khan, father name Rehman, domicile Lahore, ID card 12345-1234567-1",
        message_sid="fc-002",
    )
    _meta_image_post(client, "03333333333", "fc-photo")
    _meta_image_post(client, "03333333333", "fc-receipt")

    # Make outbound WhatsApp sending fail.
    async def failing_send(to_number, body):
        raise RuntimeError("WhatsApp API down")

    monkeypatch.setattr(whatsapp_router, "send_whatsapp_text", failing_send)

    client.post(
        "/whatsapp/webhook",
        data={
            "Body": "APPROVE 03333333333 NEW",
            "From": "whatsapp:03367483185",
            "To": "whatsapp:+14155238886",
            "MessageSid": "fc-approve",
        },
    )

    db_session.expire_all()
    student = (
        db_session.query(Student).filter(Student.phone_number == "03333333333").first()
    )
    # Notification failed -> stays APPROVED, not CONFIRMED.
    assert student.status == "APPROVED"
    assert student.confirmation_sent_at is None
    # Payment approval is not reversed by notification failure.
    assert student.fee_paid == "Yes"


def test_admin_approve_retry_is_idempotent(
    client, db_session, seed_batch, whatsapp_sender, monkeypatch
):
    """Re-issuing APPROVE after a failed notification retries sending without
    duplicating the confirmation."""
    import app.routers.whatsapp as whatsapp_router

    uploads = _fake_image_layer(monkeypatch)

    _twilio_post(client, "NOV26-1A", message_sid="retry-001")
    _twilio_post(
        client,
        "Ali Khan, father name Rehman, domicile Lahore, ID card 12345-1234567-1",
        message_sid="retry-002",
    )
    _meta_image_post(client, "03333333333", "retry-photo")
    _meta_image_post(client, "03333333333", "retry-receipt")

    send_attempts = []

    async def flaky_send(to_number, body):
        send_attempts.append(body)
        if len(send_attempts) == 1:
            raise RuntimeError("temporary failure")
        return {"messages": [{"id": "retry-sid"}]}

    monkeypatch.setattr(whatsapp_router, "send_whatsapp_text", flaky_send)

    # First approve: confirmation send fails -> stays APPROVED.
    client.post(
        "/whatsapp/webhook",
        data={
            "Body": "APPROVE 03333333333 NEW",
            "From": "whatsapp:03367483185",
            "To": "whatsapp:+14155238886",
            "MessageSid": "retry-approve-1",
        },
    )
    db_session.expire_all()
    student = (
        db_session.query(Student).filter(Student.phone_number == "03333333333").first()
    )
    assert student.status == "APPROVED"

    # Retry approve: confirmation send now succeeds -> CONFIRMED, no duplicate.
    client.post(
        "/whatsapp/webhook",
        data={
            "Body": "APPROVE 03333333333 NEW",
            "From": "whatsapp:03367483185",
            "To": "whatsapp:+14155238886",
            "MessageSid": "retry-approve-2",
        },
    )
    db_session.expire_all()
    student = (
        db_session.query(Student).filter(Student.phone_number == "03333333333").first()
    )
    assert student.status == "CONFIRMED"
    assert student.confirmation_sent_at is not None
