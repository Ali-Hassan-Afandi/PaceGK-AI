from contextlib import asynccontextmanager
from typing import Any, Dict

from fastapi import FastAPI
from pydantic import BaseModel

from .database import Base, engine, SessionLocal
from .models import Batch, Student  # noqa: F401
from .routers.whatsapp import agent, router as whatsapp_router
from .services.batch_service import create_batch, get_batches


class AdmissionChatRequest(BaseModel):
    phone_number: str
    message: str


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
    """
    reply = await agent.generate_reply(
        messages=[{"role": "user", "content": payload.message}],
        phone_number=payload.phone_number,
    )
    return {"reply": reply}


@app.get("/health")
async def health_check():
    return {"status": "ok"}
