"""Knowledge base ingestion and retrieval (RAG) for the PACE GK Academy agent.

The knowledge base is built from the raw source folders:

* Academy knowledge documents: ``settings.knowledge_dir`` (default ``DataDocs``)
* Admission documents: ``settings.admission_docs_dir`` (default ``AdmissionDocs``)

Supported source types:

* ``academy_doc``   - general academy knowledge (FAQ, fees, policies, subjects, ...).
* ``admission_doc`` - admission-specific material (posters, fee submission details, ...).

Supported input formats:

* ``.docx`` documents - parsed with ``python-docx`` (headings + paragraphs + tables).
* Image posters (``.jpg`` / ``.jpeg`` / ``.png`` / ``.webp``) - best-effort OCR via
  ``pytesseract`` when the OCR runtime is available; otherwise the file is skipped.

Retrieval strategy:

* Keyword (lexical) scoring over token overlap + full-phrase containment.
* Semantic (embedding) similarity when an embedding API key/model is configured
  (OpenAI or Groq, OpenAI-compatible endpoint). The top candidates are re-ranked
  with cosine similarity when embeddings are available.

The agent must answer ONLY from retrieved chunks (grounded generation). This
service never invents content; when nothing relevant is retrieved the caller is
expected to use a safe fallback answer.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, List, Optional

from ..config import settings

logger = logging.getLogger(__name__)

SOURCE_TYPE_ACADEMY = "academy_doc"
SOURCE_TYPE_ADMISSION = "admission_doc"

# Rough maximum characters per knowledge chunk (keeps LLM context small).
MAX_CHUNK_CHARS = 900

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


@dataclass
class KnowledgeChunk:
    """A retrievable piece of academy / admission knowledge."""

    text: str
    source: str
    source_type: str
    title: str = ""
    chunk_index: int = 0
    embedding: Optional[List[float]] = field(default=None, repr=False)


def _tokenize(text: str) -> set:
    """Lowercase alphanumeric tokens (simple, fast, good enough for GK Q&A)."""
    return set(re.findall(r"[a-z0-9]+", (text or "").lower()))


def _resolve_path(root: str) -> Path:
    """Resolve a source-folder path, tolerating run-from-subfolder layouts."""
    path = Path(root)
    if path.exists():
        return path
    candidate = path.parent / path.name
    if candidate.exists():
        return candidate
    parent = path.parent
    return parent if parent.exists() else path


def _find_source_root(leaf_name: str) -> Optional[Path]:
    """Locate a source folder by checking several candidate locations.

    Checks, in order:
      * ``<cwd>/<leaf>``, ``<cwd>/../<leaf>``, ``<cwd>/../../<leaf>``
      * ``/app/<leaf>``, ``/DataDocs``, ``/app/DataDocs``, ``/repo/<leaf>``
      * walk-up search from the working directory for ``<ancestor>/<leaf>``
    """
    start = Path.cwd().resolve()
    candidates: List[Path] = [
        start / leaf_name,
        start.parent / leaf_name,
        start.parent.parent / leaf_name,
        Path("/app") / leaf_name,
        Path("/DataDocs"),
        Path("/app/DataDocs"),
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


def _keyword_score(query_tokens: set, text_tokens: set) -> float:
    if not query_tokens:
        return 0.0
    overlap = len(query_tokens & text_tokens)
    return overlap / len(query_tokens)


def _cosine(a: Optional[List[float]], b: Optional[List[float]]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = sum(x * x for x in a) ** 0.5
    nb = sum(y * y for y in b) ** 0.5
    if not na or not nb:
        return 0.0
    return dot / (na * nb)


def _split_chunks(
    text: str, source: str, source_type: str, title: str
) -> List[KnowledgeChunk]:
    """Split document text into manageable chunks."""
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", text or "") if p and p.strip()]
    chunks: List[KnowledgeChunk] = []
    current = ""
    index = 0
    for para in paragraphs:
        if current and len(current) + 2 + len(para) > MAX_CHUNK_CHARS:
            chunks.append(
                KnowledgeChunk(
                    text=current.strip(),
                    source=source,
                    source_type=source_type,
                    title=title,
                    chunk_index=index,
                )
            )
            index += 1
            current = para
        else:
            current = f"{current}\n\n{para}" if current else para
    if current and current.strip():
        chunks.append(
            KnowledgeChunk(
                text=current.strip(),
                source=source,
                source_type=source_type,
                title=title,
                chunk_index=index,
            )
        )
    return chunks


def _extract_docx_text(path: Path) -> str:
    from docx import Document

    document = Document(str(path))
    parts: List[str] = []
    for para in document.paragraphs:
        text = (para.text or "").strip()
        if text:
            parts.append(text)
    for table in document.tables:
        for row in table.rows:
            cells = [(cell.text or "").strip() for cell in row.cells]
            line = " | ".join(c for c in cells if c)
            if line.strip():
                parts.append(line)
    return "\n\n".join(parts)


def _ocr_image(path: Path) -> str:
    """Best-effort OCR for image posters. Returns "" when OCR is unavailable."""
    try:
        import pytesseract
        from PIL import Image

        image = Image.open(str(path))
        text = pytesseract.image_to_string(image) or ""
        return re.sub(r"\s+", " ", text).strip()
    except Exception:
        logger.warning("OCR skipped for %s (pytesseract/PIL or tesseract binary unavailable).", path)
        return ""


class KnowledgeBase:
    """In-memory knowledge base built from the raw source folders."""

    def __init__(self, chunks: Optional[List[KnowledgeChunk]] = None) -> None:
        self.chunks: List[KnowledgeChunk] = list(chunks or [])
        self._embed_client: object = None

    # ------------------------------------------------------------------
    # Ingestion
    # ------------------------------------------------------------------
    @classmethod
    def from_directory(
        cls, root: str, source_type: Optional[str] = None
    ) -> "KnowledgeBase":
        kb = cls()
        base = _resolve_path(root)
        if not base.exists():
            logger.warning("Knowledge base directory does not exist: %s", root)
            return kb
        stype = source_type or (
            SOURCE_TYPE_ADMISSION if base.name.lower() == "admissiondocs" else SOURCE_TYPE_ACADEMY
        )
        for path in sorted(base.rglob("*")):
            try:
                if not path.is_file():
                    continue
            except (OSError, PermissionError):
                # Skip unreadable entries (e.g. virtual filesystems) safely.
                continue
            if any(part.lower() in SKIPPED_DIR_NAMES for part in path.parts):
                continue
            suffix = path.suffix.lower()
            if suffix == ".docx":
                try:
                    text = _extract_docx_text(path)
                except Exception:
                    logger.exception("Failed to parse DOCX at %s", path)
                    continue
                kb.chunks.extend(
                    _split_chunks(text, f"{stype}/{path.name}", stype, path.stem)
                )
            elif suffix in (".jpg", ".jpeg", ".png", ".webp"):
                text = _ocr_image(path)
                if text:
                    kb.chunks.extend(
                        _split_chunks(text, f"{stype}/{path.name}", stype, path.stem)
                    )
            elif suffix in (".txt", ".md"):
                try:
                    text = path.read_text(encoding="utf-8", errors="ignore")
                except Exception:
                    logger.exception("Failed to read text source at %s", path)
                    continue
                kb.chunks.extend(
                    _split_chunks(text, f"{stype}/{path.name}", stype, path.stem)
                )
        logger.info("Knowledge base loaded with %d chunks from %s", len(kb.chunks), root)
        return kb

    def add_chunk(self, chunk: KnowledgeChunk) -> None:
        self.chunks.append(chunk)

    # ------------------------------------------------------------------
    # Retrieval
    # ------------------------------------------------------------------
    def _embed_client_instance(self) -> object:
        if self._embed_client is None:
            from openai import AsyncOpenAI

            api_key = (
                settings.knowledge_embedding_api_key
                or settings.openai_api_key
                or settings.groq_api_key
            )
            base_url = settings.knowledge_embedding_base_url or (
                "https://api.groq.com/openai/v1" if settings.groq_api_key else "https://api.openai.com/v1"
            )
            self._embed_client = AsyncOpenAI(api_key=api_key, base_url=base_url)
        return self._embed_client

    async def _embed(self, text: str) -> Optional[List[float]]:
        api_key = (
            settings.knowledge_embedding_api_key
            or settings.openai_api_key
            or settings.groq_api_key
        )
        if not api_key:
            return None
        try:
            client = self._embed_client_instance()
            response = await client.embeddings.create(
                model=settings.knowledge_embedding_model,
                input=text,
            )
            data = response.data or []
            if not data:
                return None
            embedding = getattr(data[0], "embedding", None)
            return list(embedding) if embedding else None
        except Exception:
            logger.exception("Knowledge embedding failed; falling back to keyword retrieval.")
            return None

    async def retrieve(
        self,
        query: str,
        top_k: Optional[int] = None,
        source_types: Optional[Iterable[str]] = None,
    ) -> List[KnowledgeChunk]:
        """Return the most relevant chunks for ``query``."""
        k = top_k or settings.knowledge_top_k
        if not self.chunks:
            return []
        allowed_types = set(source_types) if source_types is not None else None
        query_tokens = _tokenize(query)
        scored: List[tuple] = []
        for chunk in self.chunks:
            if allowed_types is not None and chunk.source_type not in allowed_types:
                continue
            score = _keyword_score(query_tokens, _tokenize(chunk.text))
            if query.strip() and query.strip().lower() in chunk.text.lower():
                score = max(score, 1.0)
            scored.append((score, chunk))
        scored.sort(key=lambda item: -item[0])
        candidates = [chunk for _, chunk in scored if chunk.text and chunk.text.strip()]
        candidates = candidates[: max(k * 4, 4)]

        query_embedding = await self._embed(" ".join(sorted(query_tokens)))
        results: List[tuple] = []
        for chunk in candidates:
            score = _keyword_score(query_tokens, _tokenize(chunk.text))
            if query.strip() and query.strip().lower() in chunk.text.lower():
                score = max(score, 1.0)
            if query_embedding is not None and chunk.embedding:
                score = max(score, _cosine(query_embedding, chunk.embedding))
            results.append((score, chunk))
        results = [item for item in results if item[0] > 0]
        results.sort(key=lambda item: -item[0])
        return [chunk for _, chunk in results[:k]]


_knowledge_base: Optional[KnowledgeBase] = None


def _build_default_knowledge_base() -> KnowledgeBase:
    kb = KnowledgeBase()
    sources = [
        (SOURCE_TYPE_ACADEMY, "DataDocs"),
        (SOURCE_TYPE_ADMISSION, "AdmissionDocs"),
    ]
    for source_type, leaf_name in sources:
        root = _find_source_root(leaf_name)
        if root is None:
            configured = (
                settings.knowledge_dir if source_type == SOURCE_TYPE_ACADEMY else settings.admission_docs_dir
            )
            candidate = Path(configured)
            if candidate.is_dir():
                root = candidate
        if root is None or not root.is_dir():
            logger.warning("Knowledge source folder not found for %s; skipping.", source_type)
            continue
        base = KnowledgeBase.from_directory(str(root), source_type=source_type)
        kb.chunks.extend(base.chunks)
        logger.info(
            "Knowledge source resolved: %s -> %s (source_type=%s) chunks=%d",
            source_type,
            root,
            source_type,
            len(base.chunks),
        )
    logger.info("Default knowledge base ready with %d chunks", len(kb.chunks))
    return kb


def get_knowledge_base(root: Optional[str] = None) -> KnowledgeBase:
    """Return the cached knowledge base (built lazily from the raw source folders)."""
    global _knowledge_base
    if _knowledge_base is None:
        try:
            _knowledge_base = (
                _build_default_knowledge_base() if root is None else KnowledgeBase.from_directory(root)
            )
        except Exception:
            logger.exception("Failed to build the knowledge base; returning an empty one.")
            _knowledge_base = KnowledgeBase()
    return _knowledge_base


def reset_knowledge_base() -> None:
    """Reset the cached knowledge base (used by tests)."""
    global _knowledge_base
    _knowledge_base = None


async def retrieve_knowledge(
    query: str,
    top_k: Optional[int] = None,
    source_types: Optional[Iterable[str]] = None,
) -> List[KnowledgeChunk]:
    """Retrieve the most relevant knowledge chunks for a query."""
    return await get_knowledge_base().retrieve(query, top_k=top_k, source_types=source_types)


def format_context(chunks: List[KnowledgeChunk]) -> str:
    """Format retrieved chunks into an LLM context block with source metadata."""
    if not chunks:
        return ""
    parts: List[str] = []
    for chunk in chunks:
        header = f"Source: {chunk.source}"
        if chunk.title:
            header += f" | Document: {chunk.title}"
        parts.append(f"{header}\n{chunk.text.strip()}")
    return "\n\n---\n\n".join(parts)
