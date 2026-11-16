"""Unit tests for Meta WhatsApp Cloud API media retrieval (mocked, no network)."""

from unittest.mock import AsyncMock, patch

import httpx
import pytest

from app.utils import whatsapp

FAKE_TOKEN = "EAABfaketoken"
FAKE_MEDIA_ID = "1234567890"


@pytest.fixture(autouse=True)
def _whatsapp_token(monkeypatch):
    monkeypatch.setattr(whatsapp.settings, "whatsapp_access_token", FAKE_TOKEN)


def _make_response(status_code: int, json_body=None, content=None, url="https://graph.facebook.com/v26.0/1234567890") -> httpx.Response:
    request = httpx.Request("GET", url)
    kwargs = {"status_code": status_code, "request": request}
    if json_body is not None:
        kwargs["json"] = json_body
    elif content is not None:
        kwargs["content"] = content
    return httpx.Response(**kwargs)


def _make_client(response: httpx.Response) -> AsyncMock:
    client = AsyncMock()
    client.get = AsyncMock(return_value=response)
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=False)
    return client


@pytest.mark.asyncio
async def test_get_media_metadata_returns_metadata(caplog):
    metadata = {
        "url": "https://download.example.com/media",
        "mime_type": "image/jpeg",
        "file_size": 4096,
        "sha256": "abc",
    }
    with patch(
        "app.utils.whatsapp.httpx.AsyncClient",
        return_value=_make_client(_make_response(200, metadata)),
    ):
        result = await whatsapp.get_media_metadata(FAKE_MEDIA_ID)

    assert result == metadata
    joined = "\n".join(rec.getMessage() for rec in caplog.records)
    assert FAKE_TOKEN not in joined


@pytest.mark.asyncio
async def test_get_media_download_url_returns_url():
    metadata = {"url": "https://download.example.com/media", "mime_type": "image/jpeg"}
    with patch(
        "app.utils.whatsapp.httpx.AsyncClient",
        return_value=_make_client(_make_response(200, metadata)),
    ):
        url = await whatsapp.get_media_download_url(FAKE_MEDIA_ID)

    assert url == "https://download.example.com/media"


@pytest.mark.asyncio
async def test_download_media_returns_bytes():
    download_url = "https://download.example.com/media"
    image_bytes = b"\xff\xd8\xff" + b"\x00" * 100
    request = httpx.Request("GET", download_url)
    response = httpx.Response(200, request=request, content=image_bytes)
    with patch(
        "app.utils.whatsapp.httpx.AsyncClient", return_value=_make_client(response)
    ):
        data = await whatsapp.download_media(download_url)

    assert data == image_bytes


@pytest.mark.asyncio
async def test_get_media_metadata_missing_token(monkeypatch):
    monkeypatch.setattr(whatsapp.settings, "whatsapp_access_token", "")
    with pytest.raises(RuntimeError, match="WHATSAPP_ACCESS_TOKEN is not configured"):
        await whatsapp.get_media_metadata(FAKE_MEDIA_ID)


@pytest.mark.asyncio
async def test_get_media_download_url_no_url_raises():
    with patch(
        "app.utils.whatsapp.httpx.AsyncClient",
        return_value=_make_client(_make_response(200, {"mime_type": "image/jpeg"})),
    ):
        with pytest.raises(RuntimeError, match="No download URL"):
            await whatsapp.get_media_download_url(FAKE_MEDIA_ID)
