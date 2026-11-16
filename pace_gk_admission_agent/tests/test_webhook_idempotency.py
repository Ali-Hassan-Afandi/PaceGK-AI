"""Tests for durable webhook idempotency (database-backed, survives restarts)."""

from app.models.student import Student
from app.services.admission_service import get_or_create_student


def test_duplicate_webhook_message_id_is_ignored_durably(
    client, db_session, seed_batch, whatsapp_sender
):
    """The same MessageSid posted twice must not create duplicate students."""
    sid = "durable-sid-001"
    client.post(
        "/whatsapp/webhook",
        data={
            "Body": "NOV26-1A",
            "From": "whatsapp:03333333333",
            "To": "whatsapp:+14155238886",
            "MessageSid": sid,
        },
    )
    client.post(
        "/whatsapp/webhook",
        data={
            "Body": "NOV26-1A",
            "From": "whatsapp:03333333333",
            "To": "whatsapp:+14155238886",
            "MessageSid": sid,
        },
    )

    db_session.expire_all()
    students = (
        db_session.query(Student)
        .filter(Student.phone_number == "03333333333")
        .all()
    )
    assert len(students) == 1, "duplicate webhook must not create duplicate applicants"


def test_webhook_without_message_id_is_still_processed(client, db_session, seed_batch):
    """Messages without a message ID are processed (no dedup key to enforce)."""
    client.post(
        "/whatsapp/webhook",
        data={
            "Body": "NOV26-1A",
            "From": "whatsapp:03333333333",
            "To": "whatsapp:+14155238886",
        },
    )
    db_session.expire_all()
    student = (
        db_session.query(Student)
        .filter(Student.phone_number == "03333333333")
        .first()
    )
    assert student is not None
    assert student.status == "BATCH_CONFIRMED"


def test_duplicate_fee_receipt_webhook_does_not_duplicate_receipt(
    client, db_session, seed_batch, whatsapp_sender, monkeypatch
):
    """A redelivered image webhook must not store a duplicate receipt."""
    import app.agents.admission_agent as agent_module

    uploads = []

    class FakeStorage:
        async def upload_image(self, file_bytes, content_type, folder):
            uploads.append((content_type, folder, len(file_bytes)))
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

    # Put student in FEE_PENDING with complete details first.
    student = get_or_create_student(db_session, "03333333333")
    student.name = "Ali Khan"
    student.father_name = "Rehman"
    student.domicile = "Lahore"
    student.id_card_number = "1234512345671"
    student.student_pic_url = "https://storage.example.com/photo.jpg"
    student.status = "FEE_PENDING"
    db_session.commit()

    sid = "receipt-sid-001"
    payload = {
        "entry": [
            {
                "changes": [
                    {
                        "value": {
                            "messages": [
                                {
                                    "id": sid,
                                    "from": "03333333333",
                                    "type": "image",
                                    "image": {
                                        "id": "media-id-1",
                                        "mime_type": "image/jpeg",
                                    },
                                }
                            ]
                        }
                    }
                ]
            }
        ]
    }
    # Meta image retrieval is faked via the agent's module-level helpers above.
    for _ in range(2):
        client.post(
            "/whatsapp/webhook",
            json=payload,
            headers={"content-type": "application/json"},
        )

    db_session.expire_all()
    student = (
        db_session.query(Student)
        .filter(Student.phone_number == "03333333333")
        .first()
    )
    assert student.status == "FEE_SUBMITTED"
    assert len(uploads) == 1, "duplicate receipt webhook must not store duplicate receipts"
