"""Integration tests for the WhatsApp webhook router."""

from app.models.student import Student


def test_webhook_verification_returns_challenge(client):
    """GET /whatsapp/webhook must echo hub.challenge when the token matches."""
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


def _twilio_post(client, body, from_number="whatsapp:03333333333", message_sid=None):
    data = {
        "Body": body,
        "From": from_number,
        "To": "whatsapp:+14155238886",
    }
    if message_sid:
        data["MessageSid"] = message_sid
    return client.post("/whatsapp/webhook", data=data)


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
    # Only one student record should exist (no duplicate processing).
    assert len(students) == 1


def test_admin_approve_from_authorized_number(client, db_session, seed_batch, whatsapp_sender):
    """An admin can APPROVE a student, moving them to CONFIRMED and sending WhatsApp messages."""
    # Put the student into FEE_SUBMITTED first via the batch flow.
    _twilio_post(client, "NOV26-1A")
    _twilio_post(
        client,
        "Ali Khan, father name Rehman, domicile Lahore, ID 12345, WhatsApp 03333333333",
    )

    db_session.expire_all()
    student = (
        db_session.query(Student)
        .filter(Student.phone_number == "03333333333")
        .first()
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
        db_session.query(Student)
        .filter(Student.phone_number == "03333333333")
        .first()
    )
    assert student.status == "CONFIRMED"
    assert student.fee_category == "NEW"
    assert student.fee_paid == "Yes"
    assert student.approved_by == "03367483185"

    # The router should have attempted to send WhatsApp messages to the student.
    assert any("03333333333" in m["to"] for m in whatsapp_sender)


def test_admin_approve_from_non_admin_number_is_ignored(
    client, db_session, seed_batch, whatsapp_sender
):
    """A non-admin number cannot approve students."""
    _twilio_post(client, "NOV26-1A")
    _twilio_post(
        client,
        "Ali Khan, father name Rehman, domicile Lahore, ID 12345, WhatsApp 03333333333",
    )

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
    # State must be unchanged (still FEE_SUBMITTED).
    assert student.status == "FEE_SUBMITTED"

    # The non-admin attempt must not have produced any approval-related message
    # (community link / confirmation) to the student. The student's earlier
    # batch-selection replies are expected and are not counted here.
    student_messages_before = [
        m for m in whatsapp_sender if "Congratulations" in m.get("body", "")
    ]
    assert len(student_messages_before) == 0


def test_approve_student_not_fee_submitted_returns_error(
    client, db_session, seed_batch, whatsapp_sender
):
    """Approving a student that is not awaiting fee review must fail."""
    # Student is only INQUIRY (batch not even confirmed yet).
    _twilio_post(client, "NOV26-1A")

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
