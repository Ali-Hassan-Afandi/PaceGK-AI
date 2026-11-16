"""Unit tests for the persistent storage abstraction."""

import os
from unittest.mock import patch

import pytest

from app.services import storage_service as ss


def _jpeg_bytes() -> bytes:
    return b"\xff\xd8\xff" + b"\x00" * 100


@pytest.mark.asyncio
async def test_local_provider_uploads_and_returns_reference(tmp_path):
    provider = ss.LocalStorageProvider(base_path=str(tmp_path / "storage"))
    ref = await provider.upload_image(_jpeg_bytes(), "image/jpeg", ss.FOLDER_STUDENT_PHOTOS)

    assert ref is not None and ref != ""
    assert os.path.exists(ref)
    photo_ref = await provider.upload_image(_jpeg_bytes(), "image/jpeg", ss.FOLDER_STUDENT_PHOTOS)
    receipt_ref = await provider.upload_image(_jpeg_bytes(), "image/jpeg", ss.FOLDER_FEE_RECEIPTS)
    assert ss.FOLDER_STUDENT_PHOTOS in photo_ref
    assert ss.FOLDER_FEE_RECEIPTS in receipt_ref


@pytest.mark.asyncio
async def test_local_provider_rejects_empty_image(tmp_path):
    provider = ss.LocalStorageProvider(base_path=str(tmp_path / "storage"))
    with pytest.raises(ValueError):
        await provider.upload_image(b"", "image/jpeg", ss.FOLDER_STUDENT_PHOTOS)


@pytest.mark.asyncio
async def test_local_provider_rejects_unsupported_mime(tmp_path):
    provider = ss.LocalStorageProvider(base_path=str(tmp_path / "storage"))
    with pytest.raises(ValueError):
        await provider.upload_image(b"\xff\xd8\xff", "text/plain", ss.FOLDER_STUDENT_PHOTOS)


@pytest.mark.asyncio
async def test_local_provider_rejects_oversized_image(tmp_path):
    provider = ss.LocalStorageProvider(base_path=str(tmp_path / "storage"))
    big = b"\xff\xd8\xff" + b"\x00" * (ss.MAX_STORAGE_IMAGE_SIZE_BYTES + 1)
    with pytest.raises(ValueError):
        await provider.upload_image(big, "image/jpeg", ss.FOLDER_STUDENT_PHOTOS)


def test_get_storage_provider_defaults_to_local(monkeypatch):
    monkeypatch.setattr(ss.settings, "storage_provider", "local")
    provider = ss.get_storage_provider()
    assert isinstance(provider, ss.LocalStorageProvider)


def test_get_storage_provider_supabase_missing_config(monkeypatch):
    monkeypatch.setattr(ss.settings, "storage_provider", "supabase")
    monkeypatch.setattr(ss.settings, "supabase_url", "")
    monkeypatch.setattr(ss.settings, "supabase_service_role_key", "")
    monkeypatch.setattr(ss.settings, "supabase_storage_bucket", "")
    with pytest.raises(ValueError):
        ss.get_storage_provider()


def test_is_safe_image_url():
    assert ss.is_safe_image_url("https://example.com/img.jpg") is True
    assert ss.is_safe_image_url("http://example.com/img.png") is True
    assert ss.is_safe_image_url("ftp://example.com/img.jpg") is False
    assert ss.is_safe_image_url("") is False
    assert ss.is_safe_image_url(None) is False
    assert ss.is_safe_image_url("javascript:alert(1)") is False
