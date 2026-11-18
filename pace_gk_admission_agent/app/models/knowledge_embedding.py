"""Persistent storage for knowledge-base chunk embeddings.

Embeddings are computed once (incrementally: only new/changed chunks on each
startup) and stored here so the application never re-embeds the full corpus on
every run. Retrieval is done by loading stored embeddings and re-ranking with
cosine similarity against the query embedding.

Groq is used for chat completions only — it has no embeddings API — so
embeddings require OpenAI (``OPENAI_API_KEY``) and are opt-in via
``KNOWLEDGE_EMBEDDING_ENABLED=true``.
"""

from __future__ import annotations

from sqlalchemy import DateTime, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from ..database import Base


class KnowledgeEmbedding(Base):
    """One row per knowledge chunk with its persisted embedding vector."""

    __tablename__ = "knowledge_embeddings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    # "DataDocs" (general academy) or "AdmissionDocs" (admission/fee).
    source_folder: Mapped[str] = mapped_column(String, nullable=False, index=True)
    source_file: Mapped[str] = mapped_column(String, nullable=False, index=True)
    chunk_text: Mapped[str] = mapped_column(Text, nullable=False)
    chunk_index: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # MD5 of the chunk text — used to detect changed chunks and re-embed only them.
    content_hash: Mapped[str] = mapped_column(String, nullable=False, index=True)
    # JSON-serialised list of floats (the embedding vector).
    embedding: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[DateTime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[DateTime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )
