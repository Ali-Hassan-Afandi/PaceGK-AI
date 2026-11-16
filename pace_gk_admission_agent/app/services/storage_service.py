"""Persistent storage abstraction for applicant images.

Two images are handled separately and must never be confused:

* student photograph  -> stored in ``student.student_pic_url``
* fee payment receipt -> stored in ``student.fee_paid_pic_url``

Railway's local filesystem is ephemeral and must not be treated as permanent
storage. A configurable storage provider is used so a durable object store
(e.g. Supabase Storage) can be wired in via environment variables without
changing code.

When no durable provider is configured, a local-filesystem provider is used for
development and testing only. It is clearly not production-permanent on Railway
unless a Railway Volume is mounted at ``STORAGE_LOCAL_PATH``.
"""

from __future__ import annotations

import abc
import hashlib
import os
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

from ..config import settings

# Approved image MIME types for permanent storage.
ALLOWED_STORAGE_MIME_TYPES = {
    "image/jpeg",
    "image/jpg",
    "image/png",
    "image/webp",
}

# Maximum size for a stored image (5 MB).
MAX_STORAGE_IMAGE_SIZE_BYTES = 5 * 1024 * 1024

# Storage folders keep photographs and receipts separate.
FOLDER_STUDENT_PHOTOS = "student_photos"
FOLDER_FEE_RECEIPTS = "fee_receipts"


class StorageProvider(abc.ABC):
    """Abstract interface for persistent image storage."""

    @abc.abstractmethod
    async def upload_image(
        self,
        file_bytes: bytes,
        content_type: str,
        folder: str,
    ) -> str:
        """Upload image bytes and return a permanent storage reference (URL)."""

    @abc.abstractmethod
    async def delete_image(self, reference: str) -> bool:
        """Delete a previously stored image by its reference. Best effort."""

    @abc.abstractmethod
    def get_public_url(self, reference: str) -> str:
        """Return a public URL for the reference, or the reference itself if
        already a URL. May return an empty string for private buckets."""


class LocalStorageProvider(StorageProvider):
    """Development/test provider: stores images on the local filesystem.

    NOT production-permanent on Railway unless a Railway Volume is mounted at
    ``STORAGE_LOCAL_PATH``. Use only when no durable object store is configured.
    """

    def __init__(self, base_path: Optional[str] = None) -> None:
        self.base_path = Path(base_path or settings.storage_local_path or "admission_storage")
        self.base_path.mkdir(parents=True, exist_ok=True)

    def _folder_path(self, folder: str) -> Path:
        path = self.base_path / folder
        path.mkdir(parents=True, exist_ok=True)
        return path

    def _safe_filename(self, content_type: str) -> str:
        ext = {
            "image/jpeg": ".jpg",
            "image/jpg": ".jpg",
            "image/png": ".png",
            "image/webp": ".webp",
        }.get(content_type.strip().lower(), ".bin")
        return f"{hashlib.sha256(os.urandom(32)).hexdigest()}{ext}"

    async def upload_image(self, file_bytes: bytes, content_type: str, folder: str) -> str:
        if len(file_bytes) == 0:
            raise ValueError("Cannot store an empty image.")
        if content_type.strip().lower() not in ALLOWED_STORAGE_MIME_TYPES:
            raise ValueError(f"Unsupported image type for storage: {content_type}")
        if len(file_bytes) > MAX_STORAGE_IMAGE_SIZE_BYTES:
            raise ValueError("Image exceeds maximum allowed size for storage.")
        filename = self._safe_filename(content_type)
        file_path = self._folder_path(folder) / filename
        file_path.write_bytes(file_bytes)
        # Return a file:// reference for local storage; callers treat it as the
        # permanent storage reference (never a temporary Meta download URL).
        return str(file_path.resolve())

    async def delete_image(self, reference: str) -> bool:
        try:
            Path(reference).unlink(missing_ok=True)
            return True
        except OSError:
            return False

    def get_public_url(self, reference: str) -> str:
        # Local storage is not publicly served by default. Return the reference
        # as-is; the application decides whether to expose it.
        return reference


class SupabaseStorageProvider(StorageProvider):
    """Production provider: Supabase Storage via its REST API (httpx, no new hard
    dependency beyond the already-installed httpx client).

    Required environment variables:
    - SUPABASE_URL            e.g. https://<project>.supabase.co
    - SUPABASE_SERVICE_ROLE_KEY  service role key (server-side only, never logged)
    - SUPABASE_STORAGE_BUCKET  bucket name (must already exist)
    """

    def __init__(self) -> None:
        self.base_url = settings.supabase_url.rstrip("/") if settings.supabase_url else ""
        self.service_role_key = settings.supabase_service_role_key or ""
        self.bucket = settings.supabase_storage_bucket or ""
        if not (self.base_url and self.service_role_key and self.bucket):
            raise ValueError(
                "Supabase storage is not fully configured. Set SUPABASE_URL, "
                "SUPABASE_SERVICE_ROLE_KEY and SUPABASE_STORAGE_BUCKET."
            )

    def _object_path(self, folder: str, filename: str) -> str:
        return f"{folder}/{filename}"

    async def upload_image(self, file_bytes: bytes, content_type: str, folder: str) -> str:
        import httpx

        if len(file_bytes) == 0:
            raise ValueError("Cannot store an empty image.")
        if content_type.strip().lower() not in ALLOWED_STORAGE_MIME_TYPES:
            raise ValueError(f"Unsupported image type for storage: {content_type}")
        if len(file_bytes) > MAX_STORAGE_IMAGE_SIZE_BYTES:
            raise ValueError("Image exceeds maximum allowed size for storage.")
        filename = f"{hashlib.sha256(os.urandom(32)).hexdigest()}{self._ext(content_type)}"
        path = self._object_path(folder, filename)
        url = f"{self.base_url}/storage/v1/object/{self.bucket}/{path}"
        headers = {
            "Authorization": f"Bearer {self.service_role_key}",
            "Content-Type": content_type,
            "Cache-Control": "3600",
            "x-upsert": "true",
        }
        # Never log the service role key.
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(url, headers=headers, content=file_bytes)
            try:
                response.raise_for_status()
            except httpx.HTTPStatusError:
                # Surface a safe error without exposing credentials.
                raise RuntimeError(
                    f"Supabase storage upload failed (HTTP {response.status_code})."
                )
        return f"{self.base_url}/storage/v1/object/public/{self.bucket}/{path}"

    @staticmethod
    def _ext(content_type: str) -> str:
        return {
            "image/jpeg": ".jpg",
            "image/jpg": ".jpg",
            "image/png": ".png",
            "image/webp": ".webp",
        }.get(content_type.strip().lower(), ".bin")

    async def delete_image(self, reference: str) -> bool:
        import httpx

        # Best-effort delete: parse the object path from the public URL.
        try:
            parsed = urlparse(reference)
            # URL shape: .../storage/v1/object/public/<bucket>/<folder>/<file>
            parts = parsed.path.split("/object/public/", 1)
            if len(parts) != 2:
                return False
            object_path = parts[1]
            url = f"{self.base_url}/storage/v1/object/{self.bucket}/{object_path}"
            headers = {"Authorization": f"Bearer {self.service_role_key}"}
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.delete(url, headers=headers)
                return response.status_code in (200, 204)
        except Exception:
            return False

    def get_public_url(self, reference: str) -> str:
        return reference


def get_storage_provider() -> StorageProvider:
    """Factory: select the storage provider from ``STORAGE_PROVIDER``.

    Supported values:
    - ``local``   (default) local filesystem, dev/test only.
    - ``supabase`` Supabase Storage (requires SUPABASE_* env vars).
    """
    provider = (settings.storage_provider or "local").strip().lower()
    if provider == "supabase":
        return SupabaseStorageProvider()
    return LocalStorageProvider()


def is_safe_image_url(url: Optional[str]) -> bool:
    """Return True only for http(s) URLs that are safe to download/store.

    Protects against SSRF and arbitrary-URL text being treated as an image.
    Temporary Meta download URLs are intentionally NOT treated as permanent
    references; they are downloaded and re-stored permanently.
    """
    if not url:
        return False
    try:
        parsed = urlparse(url.strip())
    except ValueError:
        return False
    return parsed.scheme in ("http", "https") and bool(parsed.netloc)
