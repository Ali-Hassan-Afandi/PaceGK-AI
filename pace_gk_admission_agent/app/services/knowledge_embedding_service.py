"""Persistent, one-time (incremental) embedding storage for knowledge base chunks.

Scans the raw source folders (``DataDocs`` for general academy info, ``AdmissionDocs``
for admission/fee details), splits documents into chunks, and embeds only new or
changed chunks using OpenAI's embeddings API. Embedding vectors are stored in the
database so the application never re-embeds the full corpus on every run.

Retrieval loads the stored embeddings for the requested source folder, embeds the
query once, and re-ranks chunks by cosine similarity. Falls back to an empty result
when embeddings are disabled or no OpenAI key is configured (keyword retrieval is
the fallback in that case).
"""

from __future__ import annotations

import hashlib
import json
import logging
from pathlib import Path
from typing import List, Optional, Tuple

from sqlalchemy.orm import Session

from ..config import settings
from ..database import SessionLocal
from ..models.knowledge_embedding import KnowledgeEmbedding
from ..services.knowledge_service import (
    SOURCE_TYPE_ACADEMY,
    SOURCE_TYPE_ADMISSION,
    _extract_docx_text,
    _ocr_image,
    _split_chunks,
    _tokenize,
)

logger = logging.getLogger(__name__)

# Directories that must never be scanned as knowledge sources.
SKIPPED_DIR_NAMES = {
    ".git",
    "__pycache__",
    ".venv",
    "venv",
    ".pytest_cache",
    "node_modules",
    "admission_storage",
    "admission_data",
}

# Mapping of source folder name to source_type used by KnowledgeBase.
SOURCE_FOLDER_TO_TYPE = {
    "DataDocs": SOURCE_TYPE_ACADEMY,
    "AdmissionDocs": SOURCE_TYPE_ADMISSION,
}


def _content_hash(text: str) -> str:
    return hashlib.md5(text.encode("utf-8")).hexdigest()


def _find_source_root(leaf_name: str) -> Optional[Path]:
    """Locate a source folder by checking several candidate locations."""
    start = Path.cwd().resolve()
    candidates: List[Path] = [
        start / leaf_name,
        start.parent / leaf_name,
        start.parent.parent / leaf_name,
        Path("/app") / leaf_name,
        Path("/DataDocs") if leaf_name == "DataDocs" else Path("/AdmissionDocs"),
        Path("/app") / leaf_name,
        Path("/repo") / leaf_name,
        start / "pace_gk_admission_agent" / leaf_name,
    ]
    for candidate in candidates:
        try:
            if candidate.is_dir():
                return candidate
        except (OSError, PermissionError):
            continue
    for candidate in [start, *start.parents]:
        try:
            if (candidate / leaf_name).is_dir():
                return candidate / leaf_name
        except (OSError, PermissionError):
            continue
    return None


def _read_file_text(path: Path) -> str:
    """Read text from a supported source file. Returns '' for unsupported/empty files."""
    suffix = path.suffix.lower()
    if suffix == ".docx":
        return _extract_docx_text(path)
    if suffix in (".jpg", ".jpeg", ".png", ".webp"):
        return _ocr_image(path)
    if suffix in (".txt", ".md"):
        try:
            return path.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            logger.exception("Failed to read text source at %s", path)
            return ""
    return ""


class KnowledgeEmbeddingService:
    """Embeds knowledge chunks once and persists them for reuse across restarts."""

    def __init__(self, db: Optional[Session] = None) -> None:
        self.db = db or SessionLocal()
        self._owns_db = db is None
        self._embed_client: object = None

    def __enter__(self) -> "KnowledgeEmbeddingService":
        return self

    def __exit__(self, *args) -> None:
        if self._owns_db:
            self.db.close()

    # ------------------------------------------------------------------ #
    # Embedding client (OpenAI only — Groq has no embeddings API)
    # ------------------------------------------------------------------ #
    @property
    def _client(self) -> object:
        if self._embed_client is None:
            from openai import AsyncOpenAI

            # Embeddings are OpenAI-only: never fall back to the Groq key — Groq
            # does not provide an embeddings endpoint. A Groq key (gsk_...) used
            # here would fail with a 401 AuthenticationError, so ignore it.
            api_key = settings.knowledge_embedding_api_key or settings.openai_api_key
            if api_key and str(api_key).startswith("gsk_"):
                api_key = None
            base_url = settings.knowledge_embedding_base_url or "https://api.openai.com/v1"
            self._embed_client = AsyncOpenAI(api_key=api_key, base_url=base_url)
        return self._embed_client

    async def _embed_text(self, text: str) -> Optional[List[float]]:
        """Embed a single text via OpenAI. Returns None when embeddings are disabled."""
        if not settings.knowledge_embedding_enabled:
            return None
        api_key = settings.knowledge_embedding_api_key or settings.openai_api_key
        # Groq does not offer an embeddings API; never send a Groq key to OpenAI.
        if api_key and str(api_key).startswith("gsk_"):
            api_key = None
        if not api_key:
            return None
        try:
            response = await self._client.embeddings.create(
                model=settings.knowledge_embedding_model,
                input=text,
            )
            data = response.data or []
            if not data:
                return None
            embedding = getattr(data[0], "embedding", None)
            return list(embedding) if embedding else None
        except Exception:
            logger.exception("Embedding failed for a chunk; it will be skipped.")
            return None

    # ------------------------------------------------------------------ #
    # Ingestion: scan folders, embed new/changed chunks, persist to DB
    # ------------------------------------------------------------------ #
    def _get_stored_hashes(self, source_folder: str) -> dict:
        """Return {(source_file, chunk_index): content_hash} for already-stored embeddings."""
        rows = (
            self.db.query(KnowledgeEmbedding)
            .filter(KnowledgeEmbedding.source_folder == source_folder)
            .all()
        )
        return {(r.source_file, r.chunk_index): r.content_hash for r in rows}

    def _upsert_embedding(
        self,
        source_folder: str,
        source_file: str,
        chunk_index: int,
        chunk_text: str,
        embedding: Optional[List[float]],
        content_hash: str,
    ) -> None:
        row = (
            self.db.query(KnowledgeEmbedding)
            .filter(
                KnowledgeEmbedding.source_folder == source_folder,
                KnowledgeEmbedding.source_file == source_file,
                KnowledgeEmbedding.chunk_index == chunk_index,
            )
            .first()
        )
        embedding_json = json.dumps(embedding) if embedding is not None else None
        if row is not None:
            row.chunk_text = chunk_text
            row.content_hash = content_hash
            row.embedding = embedding_json
        else:
            row = KnowledgeEmbedding(
                source_folder=source_folder,
                source_file=source_file,
                chunk_text=chunk_text,
                chunk_index=chunk_index,
                content_hash=content_hash,
                embedding=embedding_json,
            )
            self.db.add(row)

    async def scan_and_embed_folder(
        self, root: str, source_folder: str, source_type: str
    ) -> int:
        """Scan a source folder, embed new/changed chunks, and persist them.

        Chunks whose content hash matches a stored row are skipped entirely —
        only genuinely new or modified chunks are embedded.
        """
        base = Path(root)
        if not base.exists():
            logger.warning("Knowledge folder does not exist: %s", root)
            return 0

        stored_hashes = self._get_stored_hashes(source_folder)
        embedded_count = 0

        for path in sorted(base.rglob("*")):
            try:
                if not path.is_file():
                    continue
            except (OSError, PermissionError):
                continue
            if any(part.lower() in SKIPPED_DIR_NAMES for part in path.parts):
                continue

            text = _read_file_text(path)
            if not text.strip():
                continue

            chunks = _split_chunks(text, str(path), source_type, path.stem)
            for chunk in chunks:
                content_hash = _content_hash(chunk.text)
                cache_key = (path.name, chunk.chunk_index)
                if (
                    cache_key in stored_hashes
                    and stored_hashes[cache_key] == content_hash
                ):
                    continue  # already embedded, unchanged

                embedding = await self._embed_text(chunk.text)
                self._upsert_embedding(
                    source_folder=source_folder,
                    source_file=path.name,
                    chunk_index=chunk.chunk_index,
                    chunk_text=chunk.text,
                    embedding=embedding,
                    content_hash=content_hash,
                )
                embedded_count += 1

        self.db.commit()
        logger.info(
            "Embedded %d new/changed chunks from %s (%s)",
            embedded_count,
            root,
            source_folder,
        )
        return embedded_count

    async def initialize_embeddings(self) -> dict:
        """Embed both knowledge folders (DataDocs + AdmissionDocs) on startup.

        Returns a dict with per-folder embedded-chunk counts.
        """
        results: dict = {}
        sources = [
            (SOURCE_TYPE_ACADEMY, "DataDocs"),
            (SOURCE_TYPE_ADMISSION, "AdmissionDocs"),
        ]
        for source_type, leaf_name in sources:
            root = _find_source_root(leaf_name)
            if root is None or not root.is_dir():
                logger.warning(
                    "Knowledge source folder not found for %s; skipping embedding.", leaf_name
                )
                results[leaf_name] = 0
                continue
            count = await self.scan_and_embed_folder(str(root), leaf_name, source_type)
            results[leaf_name] = count
        return results

    # ------------------------------------------------------------------ #
    # Retrieval: load stored embeddings, re-rank by cosine similarity
    # ------------------------------------------------------------------ #

    async def retrieve_from_embeddings(
        self,
        query: str,
        source_folder: Optional[str] = None,
        top_k: int = 3,
    ) -> List[Tuple[str, str, str, float]]:
        """Retrieve top chunks using stored embeddings + cosine similarity.

        Returns a list of ``(chunk_text, source_file, source_folder, score)``.
        Returns an empty list when embeddings are disabled, no key is configured,
        or no stored embeddings exist for the requested folder.
        """
        if not settings.knowledge_embedding_enabled:
            return []
        api_key = settings.knowledge_embedding_api_key or settings.openai_api_key
        if not api_key:
            return []

        query_tokens = _tokenize(query)
        query_embedding = await self._embed_text(" ".join(sorted(query_tokens)))
        if query_embedding is None:
            return []

        q = self.db.query(KnowledgeEmbedding)
        if source_folder is not None:
            q = q.filter(KnowledgeEmbedding.source_folder == source_folder)

        rows = q.all()
        if not rows:
            return []

        results: List[Tuple[str, str, str, float]] = []
        for row in rows:
            if not row.embedding:
                continue
            try:
                stored_embedding = json.loads(row.embedding)
            except (json.JSONDecodeError, TypeError):
                continue
            score = self._cosine(query_embedding, stored_embedding)
            results.append((row.chunk_text, row.source_file, row.source_folder, score))

        results.sort(key=lambda item: -item[3])
        return results[:top_k]

    @staticmethod
    def _cosine(a: List[float], b: List[float]) -> float:
        if not a or not b or len(a) != len(b):
            return 0.0
        dot = sum(x * y for x, y in zip(a, b))
        na = sum(x * x for x in a) ** 0.5
        nb = sum(y * y for y in b) ** 0.5
        if not na or not nb:
            return 0.0
        return dot / (na * nb)
