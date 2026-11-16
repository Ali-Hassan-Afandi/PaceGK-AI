# PACE GK Academy Admission Agent — Milestones

Modular FastAPI + LLM + WhatsApp chatbot scaffold for the PACE GK Academy admission agent.

## Milestone status

- **M1 ✅ Done** — FastAPI + WhatsApp webhook + agent scaffold.
- **M2 ✅ Done** — Admission flow: INQUIRY → ask batch → batch code → `BATCH_CONFIRMED` → ask admission form.
- **M3 ✅ Done** — Admission form fill state → fee submission step → human approval (IHL) → community link on approval. Admin numbers include `03367483185`, `03463156495`, and test number `03327041010`.
- **M4 ✅ Done** — Fee categorization (new full / old −20% / orphan-disabled −20% / needy free) + fee-paid column update + final confirmation on approval.
- **M4.1 ✅ Done** — End-to-end state machine integration: `INQUIRY → BATCH_CONFIRMED → FORM_FILL → FEE_PENDING → FEE_SUBMITTED → APPROVED → CONFIRMED` (plus `REJECTED`) wired through agent + admin approval services.

## Implemented scope

- FastAPI app bootstrap (`app.main:app`)
- WhatsApp webhook router (`app.routers.whatsapp`) — Twilio + Meta Cloud API, durable idempotency, admin commands
- Admission agent (`app.agents.admission_agent`) — deterministic state machine + optional LLM-assisted replies/extraction
- Batch / student / admission-validation / approval / fee / export services
- Persistent image storage abstraction (`app.services.storage_service`) — local filesystem (dev) or Supabase Storage (prod)
- WhatsApp sender util (`app.utils.whatsapp`) — Twilio + Meta Cloud API + mock
- **RAG knowledge base** (`app.services.knowledge_service`) — built from the raw source folders and used to ground chatbot answers

## Knowledge base / RAG

- The agent answers academy questions from a RAG knowledge base built from the raw sources:
  - Academy knowledge: `../DataDocs` (`.docx`) — source type `academy_doc`
  - Admission documents: `../AdmissionDocs` (posters / images, OCR) — source type `admission_doc`
- Retrieval supports keyword scoring + (when configured) OpenAI/Groq embeddings (`KNOWLEDGE_EMBEDDING_*` env vars) with cosine re-ranking.
- The admission agent builds its LLM replies with `GROUNDING_SYSTEM_PROMPT`, feeding in retrieved context. If nothing relevant is retrieved, it replies with a safe fallback instead of inventing an answer.
- Batch-specific admission info (NOV26-1A/1B/1C) is served from the `batches` database records (source of truth), with admission posters as supporting sources.

## Admission state machine

```
INQUIRY
    → (batch chosen) → BATCH_CONFIRMED
        → (details complete + student photo stored) → FEE_PENDING
            → (fee receipt image stored, validated) → FEE_SUBMITTED
                → (admin APPROVE: FEE_SUBMITTED + receipt + complete details)
                    → APPROVED
                        → (WhatsApp notifications accepted) → CONFIRMED
                            → (next cycle) → INQUIRY
            → (admin REJECT) → REJECTED
```

Key rules:

- Five mandatory fields: name, father name, domicile, CNIC (ID card number), student photograph (uploaded image). Phone number comes from the WhatsApp sender.
- `FEE_PENDING` requires all five fields plus a stored student photograph before a fee receipt can be accepted.
- Fee receipts: text alone never advances to `FEE_SUBMITTED`; an image receipt is downloaded from Meta, validated (image type/size), uploaded to permanent storage, and only then does the state move.
- `APPROVE <student_phone> <category>` is only accepted for admin numbers; approval requires the student to be `FEE_SUBMITTED` with a stored receipt and complete details. `REJECT <student_phone> [reason]` is available from the review states.
- `CONFIRMED` is set only after WhatsApp notifications (community link + approval confirmation) were accepted; an approval persists (`APPROVED`) even if a notification send fails, and can be retried.

## Architecture

See [ARCHITECTURE.md](./ARCHITECTURE.md) for the full system design.

## Endpoints

- `GET /health`
- `GET /whatsapp/webhook?token=<WHATSAPP_VERIFY_TOKEN>`
- `POST /whatsapp/webhook`
- `POST /admission/chat`

## Run locally

```bash
pip install -r requirements.txt
cp .env.example .env
uvicorn app.main:app --reload
```

## Deploy to Railway (WhatsApp webhook testing)

The repo ships with a Railway deploy config at `pace_gk_admission_agent/railway.json` (Nixpacks builder) and a
start command of `uvicorn app.main:app --host 0.0.0.0 --port $PORT`.

> **Important — build context:** the Python app lives in the `pace_gk_admission_agent/` subdirectory.
> Railway must be pointed at that folder, otherwise the build runs from the repository root and fails
> ("Python application lives in a subdirectory… Railpack found no recognizable project files").

1. Push this repository to GitHub (already done here).
2. Create a new project on [Railway](https://railway.app) → **Deploy from GitHub repo**.
3. In the service settings, set the **Root Directory** to `pace_gk_admission_agent`
   (the folder containing `app/`, `requirements.txt`, `railway.json`).
4. Add the required environment variables (see `.env.example`) in the Railway service:
   - `TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN`, `WHATSAPP_FROM_NUMBER`, `WHATSAPP_PHONE_NUMBER_ID`,
     `WHATSAPP_ACCESS_TOKEN`, `WHATSAPP_VERIFY_TOKEN`
   - `OPENAI_API_KEY` or `GROQ_API_KEY` (LLM used by the admission agent)
   - `DATABASE_URL` (use a Railway Postgres DB, or keep SQLite for testing)
   - `ADMIN_NUMBERS`
   - Storage (production): `STORAGE_PROVIDER=supabase`, `SUPABASE_URL`, `SUPABASE_SERVICE_ROLE_KEY`,
     `SUPABASE_STORAGE_BUCKET` (or keep `STORAGE_PROVIDER=local` with a mounted volume).
5. Deploy. Railway runs the Nixpacks build and starts the FastAPI app; health check hits `/health`.
6. Grab the public URL (e.g. `https://xxx.up.railway.app`).
7. In the **Twilio Console**, set the WhatsApp sender webhook:
   - **Messaging → Senders → WhatsApp Sender phone number → Webhook**
   - URL: `https://<your-railway-app>.up.railway.app/whatsapp/webhook?token=<WHATSAPP_VERIFY_TOKEN>`
   - HTTP Method: **POST**
8. In **Twilio Console → Messaging → Services**, set the WhatsApp message service /
   sender status to **Active** and the "Use webhook" flow so inbound messages reach the app.

## Env vars

See `.env.example` — WhatsApp (Twilio / Meta), LLM keys, database URL, admin numbers, and
persistent image storage (`STORAGE_PROVIDER`, `STORAGE_LOCAL_PATH`, `SUPABASE_*`).

## Testing

Run the full test suite from the project root:

```bash
python -m pytest
```

The suite covers the webhook verification, Meta + Twilio ingress, durable idempotency,
the full admission funnel (`BATCH_CONFIRMED → FORM_FILL → FEE_PENDING → FEE_SUBMITTED → APPROVED → CONFIRMED`),
image intake/storage, validation and the admin approval workflow.

## Testing roadmap

- Later: web-based app link for bot testing.
- Final: deployed on WhatsApp.
