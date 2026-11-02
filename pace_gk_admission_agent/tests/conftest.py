"""Pytest configuration and shared fixtures for the admission agent tests.

The fixtures below give every test an isolated SQLite database (so the real
``pace_gk_admission_agent.db`` is never touched), a FastAPI ``TestClient``, a
cleared WhatsApp message de-duplication set, and a recording mock for the
outbound WhatsApp sender (no real SMS/WhatsApp/network calls are made).
"""

from pathlib import Path
import os
import shutil
import tempfile

import pytest
from fastapi.testclient import TestClient

# ---------------------------------------------------------------------------
# Isolated test database / export directory.
#
# These environment variables must be set *before* the ``app`` package is
# imported (conftest.py is loaded by pytest before any test module), so that
# pydantic-settings reads the test database URL instead of the real one.
# ---------------------------------------------------------------------------
_TEST_DIR = Path(tempfile.mkdtemp(prefix="pace_admission_tests_"))
os.environ["DATABASE_URL"] = f"sqlite:///{_TEST_DIR / 'test_pace.db'}"
os.environ["ADMISSION_EXPORT_DIR"] = str(_TEST_DIR / "admission_data")
os.environ["WHATSAPP_VERIFY_TOKEN"] = "test-verify-token"
os.environ["WHATSAPP_PHONE_NUMBER_ID"] = ""
os.environ["WHATSAPP_ACCESS_TOKEN"] = ""
os.environ["OPENAI_API_KEY"] = ""
os.environ["GROQ_API_KEY"] = ""

ADMIN_NUMBERS = ["03367483185", "03463156495", "03327041010"]

TEST_STUDENT_PHONE = "03333333333"
TEST_BATCH_CODE = "NOV26-1A"


@pytest.fixture(scope="session", autouse=True)
def _remove_test_dir_after_session():
    yield
    shutil.rmtree(_TEST_DIR, ignore_errors=True)


@pytest.fixture
def db_session():
    """A fresh SQLAlchemy session against an isolated test database.

    Tables are created before the test and dropped afterwards so each test
    starts from a clean slate.
    """
    from app.database import Base, engine, SessionLocal

    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
        Base.metadata.drop_all(bind=engine)


@pytest.fixture
def client():
    """FastAPI test client."""
    from app.main import app

    return TestClient(app)


@pytest.fixture(autouse=True)
def _clear_processed_message_ids():
    """Clear the in-process WhatsApp de-duplication set around every test."""
    import app.routers.whatsapp as whatsapp_router

    whatsapp_router._processed_message_ids.clear()
    yield
    whatsapp_router._processed_message_ids.clear()


@pytest.fixture
def admin_numbers():
    """Ensure the configured admin numbers are the test admin numbers."""
    from app.config import settings

    settings.admin_numbers = list(ADMIN_NUMBERS)
    return settings.admin_numbers


@pytest.fixture
def whatsapp_sender(monkeypatch):
    """Replace the outbound WhatsApp sender with a recording fake.

    Returns a list of ``{"to": ..., "body": ...}`` dicts capturing every
    message the router attempted to send during the test.
    """
    import app.routers.whatsapp as whatsapp_router

    sent = []

    async def fake_send(to_number: str, body: str):
        sent.append({"to": to_number, "body": body})
        return {"messages": [{"id": "fake-sid"}]}

    monkeypatch.setattr(whatsapp_router, "send_whatsapp_text", fake_send)
    return sent


@pytest.fixture
def seed_batch(db_session):
    """Create the demo batches used by the app and return them keyed by code."""
    from app.services.batch_service import create_batch

    batches = {}
    for code, name, community_link in [
        (TEST_BATCH_CODE, "NOV 2026 Batch 1A", "https://chat.whatsapp.com/PACE1A"),
        ("NOV26-1B", "NOV 2026 Batch 1B", "https://chat.whatsapp.com/PACE1B"),
        ("NOV26-1C", "NOV 2026 Batch 1C", ""),
    ]:
        batches[code] = create_batch(
            db_session,
            batch_code=code,
            name=name,
            poster_url="",
            community_link=community_link,
        )
    return batches
