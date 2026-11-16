"""Tests for the RAG knowledge base service (ingestion + retrieval)."""

import pytest

from app.services.knowledge_service import (
    KnowledgeBase,
    KnowledgeChunk,
    SOURCE_TYPE_ACADEMY,
    SOURCE_TYPE_ADMISSION,
    format_context,
    retrieve_knowledge,
)


def _chunk(
    text: str,
    source: str = "academy_doc/faq.docx",
    source_type: str = SOURCE_TYPE_ACADEMY,
    title: str = "FAQ",
):
    return KnowledgeChunk(
        text=text,
        source=source,
        source_type=source_type,
        title=title,
    )


@pytest.mark.asyncio
async def test_retrieve_returns_relevant_chunk():
    kb = KnowledgeBase(
        chunks=[
            _chunk("The full admission fee is 15000 rupees for the November batch.", source="academy_doc/faq.docx"),
            _chunk("Mock test scheduling details are available on the portal.", source="academy_doc/quiz.docx"),
        ]
    )
    results = await kb.retrieve("admission fee", top_k=2)
    assert len(results) == 1
    assert "15000" in results[0].text


@pytest.mark.asyncio
async def test_retrieve_returns_empty_when_nothing_is_relevant():
    kb = KnowledgeBase(chunks=[_chunk("Mock test scheduling details.", source="academy_doc/quiz.docx")])
    results = await kb.retrieve("quantum physics cosmology", top_k=2)
    assert results == []


@pytest.mark.asyncio
async def test_retrieve_source_type_filter():
    kb = KnowledgeBase(
        chunks=[
            _chunk("Fee details here.", source="admission_doc/poster.jpeg", source_type=SOURCE_TYPE_ADMISSION),
            _chunk("Fee details here.", source="academy_doc/faq.docx", source_type=SOURCE_TYPE_ACADEMY),
        ]
    )
    results = await kb.retrieve("fee details", top_k=2, source_types=[SOURCE_TYPE_ACADEMY])
    assert len(results) == 1
    assert results[0].source_type == SOURCE_TYPE_ACADEMY


def test_format_context_contains_source_metadata():
    context = format_context(
        [_chunk("The batch fee is 15000.", source="academy_doc/faq.docx", title="FAQ")]
    )
    assert "Source: academy_doc/faq.docx" in context
    assert "The batch fee is 15000." in context


@pytest.mark.asyncio
async def test_knowledge_base_ingests_docx_from_directory(tmp_path):
    from docx import Document

    document = Document()
    document.add_paragraph("PACE GK Academy admission batch November 2026 fee details.")
    path = tmp_path / "sample_batch.docx"
    document.save(str(path))

    kb = KnowledgeBase.from_directory(str(tmp_path), source_type=SOURCE_TYPE_ACADEMY)
    assert len(kb.chunks) == 1
    results = await kb.retrieve("admission batch fee")
    assert len(results) == 1
    assert "fee" in results[0].text.lower()


@pytest.mark.asyncio
async def test_retrieve_knowledge_uses_cached_base(monkeypatch, tmp_path):
    """retrieve_knowledge should route through the (cached) knowledge base factory."""
    from app.services import knowledge_service

    built = KnowledgeBase(chunks=[_chunk("Batch fee is 15000.", source="academy_doc/faq.docx")])

    def fake_get_base(root=None):
        return built

    monkeypatch.setattr(knowledge_service, "get_knowledge_base", fake_get_base)
    results = await retrieve_knowledge("batch fee")
    assert len(results) == 1
    assert "15000" in results[0].text
