import logging
from contextlib import asynccontextmanager
from typing import Any, Dict, Optional

from fastapi import FastAPI
from pydantic import BaseModel

from .database import Base, engine, SessionLocal, ensure_schema
from .models import Batch, Student  # noqa: F401
from .routers.whatsapp import agent, router as whatsapp_router
from .services.batch_service import create_batch, get_batches
from .services.knowledge_embedding_service import KnowledgeEmbeddingService

logger = logging.getLogger(__name__)


class AdmissionChatRequest(BaseModel):
    phone_number: str
    message: str
    media_url: Optional[str] = None


class AdmissionChatResponse(BaseModel):
    reply: str


DEMO_BATCHES = [
    ("NOV26-1A", "NOV 2026 Batch 1A", "", ""),
    ("NOV26-1B", "NOV 2026 Batch 1B", "", ""),
    ("NOV26-1C", "NOV 2026 Batch 1C", "", ""),
]


@asynccontextmanager
async def lifespan(app: FastAPI):
    Base.metadata.create_all(bind=engine)
    # Repeatable, data-safe schema updates (idempotency table, notification columns).
    ensure_schema()
    db = SessionLocal()
    try:
        if not get_batches(db):
            for code, name, poster_url, community_link in DEMO_BATCHES:
                create_batch(
                    db,
                    batch_code=code,
                    name=name,
                    poster_url=poster_url,
                    community_link=community_link,
                )
    finally:
        db.close()

    # One-time (incremental) embedding of both knowledge folders on startup.
    # Only new/changed chunks are embedded; already-embedded chunks are skipped.
    try:
        async with KnowledgeEmbeddingService() as svc:
            results = await svc.initialize_embeddings()
        logger.info("Startup embedding initialized: %s", results)
    except Exception:
        logger.exception("Startup embedding initialization failed; embeddings will be lazy.")

    yield
    engine.dispose()


app = FastAPI(
    title="PACE GK Academy Admission Agent",
    version="0.2.0",
    description="Real-time WhatsApp chatbot + admission agent for PACE GK Academy",
    lifespan=lifespan,
)

app.include_router(whatsapp_router)


@app.post("/admission/chat")
async def admission_chat(payload: AdmissionChatRequest) -> Dict[str, Any]:
    """Internal endpoint used by the general-knowledge chatbot to hand an
    admission-related turn over to the admission agent (state machine).

    The admission agent owns the remaining student process:
    batch selection -> admission form -> fee submission -> human approval -> community link.

    Any image reference is processed through the persistent storage layer: it is
    downloaded, validated and stored permanently, and only the permanent storage
    reference is associated with the student. Temporary URLs are never stored as
    permanent image references.
    """
    media_type = "image" if payload.media_url else None
    reply = await agent.generate_reply(
        messages=[{"role": "user", "content": payload.message}],
        phone_number=payload.phone_number,
        media_url=payload.media_url,
        media_type=media_type,
    )
    return {"reply": reply}


@app.get("/health")
async def health_check():
    return {"status": "ok"}
