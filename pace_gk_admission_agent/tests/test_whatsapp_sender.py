"""Unit tests for the WhatsApp Cloud API sender (app.utils.whatsapp).

These tests exercise the real ``send_whatsapp_text`` HTTP path without making
any network calls: ``httpx.AsyncClient`` is replaced with a fake that returns
a canned response.
"""

import logging
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from app.utils import whatsapp

FAKE_PHONE_NUMBER_ID = "1438039112719687"
FAKE_ACCESS_TOKEN = "EAABfakeaccesstokenforloggingcheck"
FAKE_RECIPIENT = "923333333333"
FAKE_URL = f"https://graph.facebook.com/v26.0/{FAKE_PHONE_NUMBER_ID}/messages"


@pytest.fixture(autouse=True)
def _whatsapp_credentials(monkeypatch):
    """Configure fake (non-secret) WhatsApp credentials for the sender tests."""
    monkeypatch.setattr(whatsapp.settings, "whatsapp_phone_number_id", FAKE_PHONE_NUMBER_ID)
    monkeypatch.setattr(whatsapp.settings, "whatsapp_access_token", FAKE_ACCESS_TOKEN)


def _make_response(status_code: int, json_body, url: str = FAKE_URL) -> httpx.Response:
    request = httpx.Request("POST", url)
    return httpx.Response(status_code, request=request, json=json_body)


def _make_client(response: httpx.Response) -> AsyncMock:
    client = AsyncMock()
    client.post = AsyncMock(return_value=response)
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=False)
    return client


# ---------------------------------------------------------------------------
# Error handling: HTTPStatusError is caught, logged, and re-raised.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_send_whatsapp_text_re_raises_http_status_error(caplog):
    """A non-2xx Meta response must raise httpx.HTTPStatusError (not suppressed)."""
    response = _make_response(403, {
        "error": {
            "code": 190,
            "message": "Invalid OAuth 2.0 Access Token",
            "type": "OAuthException",
            "error_subcode": 1234567,
        }
    })
    with patch("app.utils.whatsapp.httpx.AsyncClient", return_value=_make_client(response)):
        with caplog.at_level(logging.ERROR, logger="app.utils.whatsapp"):
            with pytest.raises(httpx.HTTPStatusError):
                await whatsapp.send_whatsapp_text(FAKE_RECIPIENT, "hello")

    joined = "\n".join(rec.getMessage() for rec in caplog.records)
    assert "status=403" in joined
    assert "meta_error_code=190" in joined
    assert "meta_error_message=Invalid OAuth 2.0 Access Token" in joined
    assert "meta_error_type=OAuthException" in joined
    assert "meta_error_subcode=1234567" in joined
    assert FAKE_PHONE_NUMBER_ID in joined
    assert FAKE_URL in joined


@pytest.mark.asyncio
async def test_send_whatsapp_text_error_log_excludes_secrets(caplog):
    """The error log must never contain the access token or Authorization header."""
    response = _make_response(401, {"error": {"code": 190, "message": "Token invalid", "type": "OAuthException"}})
    with patch("app.utils.whatsapp.httpx.AsyncClient", return_value=_make_client(response)):
        with caplog.at_level(logging.ERROR, logger="app.utils.whatsapp"):
            with pytest.raises(httpx.HTTPStatusError):
                await whatsapp.send_whatsapp_text(FAKE_RECIPIENT, "hello")

    joined = "\n".join(rec.getMessage() for rec in caplog.records)
    assert FAKE_ACCESS_TOKEN not in joined
    assert "Authorization" not in joined


@pytest.mark.asyncio
async def test_send_whatsapp_text_error_log_excludes_full_student_number(caplog):
    """The error log must never contain the full recipient phone number."""
    response = _make_response(400, {"error": {"code": 100, "message": "Invalid recipient", "type": "OAuthException"}})
    with patch("app.utils.whatsapp.httpx.AsyncClient", return_value=_make_client(response)):
        with caplog.at_level(logging.ERROR, logger="app.utils.whatsapp"):
            with pytest.raises(httpx.HTTPStatusError):
                await whatsapp.send_whatsapp_text(FAKE_RECIPIENT, "hello")

    joined = "\n".join(rec.getMessage() for rec in caplog.records)
    assert FAKE_RECIPIENT not in joined
    assert "923333333333" not in joined


@pytest.mark.asyncio
async def test_send_whatsapp_text_meta_error_without_subcode(caplog):
    """Missing optional fields (e.g. error_subcode) are logged as None, not crashed."""
    response = _make_response(400, {"error": {"code": 100, "message": "Invalid parameter", "type": "OAuthException"}})
    with patch("app.utils.whatsapp.httpx.AsyncClient", return_value=_make_client(response)):
        with caplog.at_level(logging.ERROR, logger="app.utils.whatsapp"):
            with pytest.raises(httpx.HTTPStatusError):
                await whatsapp.send_whatsapp_text(FAKE_RECIPIENT, "hello")

    joined = "\n".join(rec.getMessage() for rec in caplog.records)
    assert "meta_error_code=100" in joined
    assert "meta_error_message=Invalid parameter" in joined
    assert "meta_error_type=OAuthException" in joined
    assert "meta_error_subcode=None" in joined


@pytest.mark.asyncio
async def test_send_whatsapp_text_non_json_error_body(caplog):
    """A non-JSON error body must not break the diagnostic logging."""
    request = httpx.Request("POST", FAKE_URL)
    response = httpx.Response(500, request=request, content=b"not json")
    with patch("app.utils.whatsapp.httpx.AsyncClient", return_value=_make_client(response)):
        with caplog.at_level(logging.ERROR, logger="app.utils.whatsapp"):
            with pytest.raises(httpx.HTTPStatusError):
                await whatsapp.send_whatsapp_text(FAKE_RECIPIENT, "hello")

    joined = "\n".join(rec.getMessage() for rec in caplog.records)
    assert "status=500" in joined
    assert "meta_error_code=None" in joined


@pytest.mark.asyncio
async def test_send_whatsapp_text_http400_logs_error_data_details_and_fbtrace_id(caplog):
    """An HTTP 400 Meta response must log error_data.details and fbtrace_id when present."""
    response = _make_response(400, {
        "error": {
            "code": 100,
            "message": "Invalid parameter",
            "type": "OAuthException",
            "error_subcode": 2018000,
            "error_data": {
                "details": {
                    "is_unregistrable": False,
                    "phone_number": "+1234567890",
                }
            },
            "fbtrace_id": "AbcdEfgh123",
        }
    })
    with patch("app.utils.whatsapp.httpx.AsyncClient", return_value=_make_client(response)):
        with caplog.at_level(logging.ERROR, logger="app.utils.whatsapp"):
            with pytest.raises(httpx.HTTPStatusError):
                await whatsapp.send_whatsapp_text(FAKE_RECIPIENT, "hello")

    joined = "\n".join(rec.getMessage() for rec in caplog.records)
    assert "status=400" in joined
    assert "meta_error_code=100" in joined
    assert "meta_error_message=Invalid parameter" in joined
    assert "meta_error_type=OAuthException" in joined
    assert "meta_error_subcode=2018000" in joined
    assert "meta_error_data_details=" in joined
    assert "meta_error_fbtrace_id=AbcdEfgh123" in joined


@pytest.mark.asyncio
async def test_send_whatsapp_text_http400_without_optional_fields(caplog):
    """Missing optional Meta fields (error_data.details / fbtrace_id) are logged as None."""
    response = _make_response(400, {
        "error": {
            "code": 100,
            "message": "Invalid parameter",
            "type": "OAuthException",
        }
    })
    with patch("app.utils.whatsapp.httpx.AsyncClient", return_value=_make_client(response)):
        with caplog.at_level(logging.ERROR, logger="app.utils.whatsapp"):
            with pytest.raises(httpx.HTTPStatusError):
                await whatsapp.send_whatsapp_text(FAKE_RECIPIENT, "hello")

    joined = "\n".join(rec.getMessage() for rec in caplog.records)
    assert "status=400" in joined
    assert "meta_error_code=100" in joined
    assert "meta_error_message=Invalid parameter" in joined
    assert "meta_error_type=OAuthException" in joined
    assert "meta_error_subcode=None" in joined
    assert "meta_error_data_details=None" in joined
    assert "meta_error_fbtrace_id=None" in joined


# ---------------------------------------------------------------------------
# Success path: no secrets/personal info logged.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_send_whatsapp_text_success_masks_recipient_and_drops_contacts(caplog):
    """On success the recipient number is masked and ``contacts`` (PII) is not logged."""
    response = _make_response(200, {
        "messaging_product": "whatsapp",
        "contacts": [{"input": FAKE_RECIPIENT, "wa_id": FAKE_RECIPIENT}],
        "messages": [{"id": "wamid.fake", "type": "text"}],
    })
    with patch("app.utils.whatsapp.httpx.AsyncClient", return_value=_make_client(response)):
        with caplog.at_level(logging.INFO, logger="app.utils.whatsapp"):
            result = await whatsapp.send_whatsapp_text(FAKE_RECIPIENT, "hello")

    assert result["messages"][0]["id"] == "wamid.fake"
    joined = "\n".join(rec.getMessage() for rec in caplog.records)
    assert FAKE_RECIPIENT not in joined
    assert "wa_id" not in joined
    assert "***3333" in joined


# ---------------------------------------------------------------------------
# Credentials-not-configured short-circuit (existing behaviour).
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_send_whatsapp_text_missing_credentials_returns_dict(monkeypatch):
    """When credentials are absent the sender returns an error dict instead of calling Meta."""
    monkeypatch.setattr(whatsapp.settings, "whatsapp_phone_number_id", "")
    monkeypatch.setattr(whatsapp.settings, "whatsapp_access_token", "")
    with patch("app.utils.whatsapp.httpx.AsyncClient") as fake_client:
        result = await whatsapp.send_whatsapp_text(FAKE_RECIPIENT, "hello")
        fake_client.assert_not_called()
    assert result == {
        "error": "whatsapp_credentials_not_configured",
        "to": FAKE_RECIPIENT,
        "body": "hello",
    }


# ---------------------------------------------------------------------------
# Helpers.
# ---------------------------------------------------------------------------


def test_extract_meta_error_missing_error():
    request = httpx.Request("POST", FAKE_URL)
    response = httpx.Response(200, request=request, json={"messaging_product": "whatsapp"})
    assert whatsapp._extract_meta_error(response) == {}


def test_extract_meta_error_invalid_body():
    request = httpx.Request("POST", FAKE_URL)
    response = httpx.Response(200, request=request, content=b"not json")
    assert whatsapp._extract_meta_error(response) == {}


def test_normalize_preserves_country_code():
    assert whatsapp._normalize_phone_number("whatsapp:+92 333-333-3333") == "923333333333"
    assert whatsapp._normalize_phone_number("+923333333333") == "923333333333"
