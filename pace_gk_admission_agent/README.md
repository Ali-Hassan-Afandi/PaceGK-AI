# PACE GK Academy Admission Agent — Milestones

Modular FastAPI + OpenAI + WhatsApp chatbot scaffold for the PACE GK Academy admission agent.

## Milestone status

- **M1 ✅ Done** — FastAPI + WhatsApp webhook + agent scaffold.
- **M2 ✅ Done** — Admission flow: INQUIRY / AWAITING_BATCH → ask batch → batch code → `BATCH_CONFIRMED` → ask admission form.
- **M3 ✅ Done** — Admission form fill state → fee submission step → human approval (IHL) → community link on approval. Admin numbers include `03367483185`, `03463156495`, and test number `03327041010`.
- **M4 ✅ Done** — Fee categorization (new full / old −20% / orphan-disabled −20% / needy free) + fee-paid column update + final confirmation on approval.
- **M4.1 ✅ Done** — End-to-end state machine integration: `FORM_FILL → FEE_SUBMITTED → FEE_CATEGORIZED → APPROVED → CONFIRMED` (plus `REJECTED`) wired through agent + admin approval services.

## Implemented scope

- FastAPI app bootstrap (`app.main:app`)
- WhatsApp webhook router (`app.routers.whatsapp`)
- Admission agent (`app.agents.admission_agent`) using `gpt-4o-mini` + deterministic flow
- Batch service + student service (get_or_create) with SQLAlchemy models / Pydantic schemas
- WhatsApp sender util (Twilio-ready, Meta-ready next)

## Endpoints

- `GET /health`
- `GET /whatsapp/webhook?token=<WHATSAPP_VERIFY_TOKEN>`
- `POST /webhook`

## Run locally

```bash
pip install -r requirements.txt
cp .env.example .env
uvicorn app.main:app --reload
```

## Env vars

See `.env.example` — WhatsApp (Twilio), OpenAI key, database URL.

## Testing roadmap

- Later: web-based app link for bot testing.
- Final: deployed on WhatsApp.
