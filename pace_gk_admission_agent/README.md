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

## Architecture

See [ARCHITECTURE.md](./ARCHITECTURE.md) for the full system design: layer responsibilities,
WhatsApp webhook request flow, internal `/admission/chat` handoff, admission state machine,
LLM provider strategy, fee logic and database layout.

## Endpoints

- `GET /health`
- `GET /whatsapp/webhook?token=<WHATSAPP_VERIFY_TOKEN>`
- `POST /webhook`
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
5. Deploy. Railway runs the Nixpacks build and starts the FastAPI app; health check hits `/health`.
6. Grab the public URL (e.g. `https://xxx.up.railway.app`).
7. In the **Twilio Console**, set the WhatsApp sender webhook:
   - **Messaging → Senders → WhatsApp Sender phone number → Webhook**
   - URL: `https://<your-railway-app>.up.railway.app/whatsapp/webhook?token=<WHATSAPP_VERIFY_TOKEN>`
   - HTTP Method: **POST**
8. In **Twilio Console → Messaging → Services**, set the WhatsApp message service /
   sender status to **Active** and the "Use webhook" flow so inbound messages reach the app.

## Env vars

See `.env.example` — WhatsApp (Twilio), OpenAI key, database URL.

## Testing roadmap

- Later: web-based app link for bot testing.
- Final: deployed on WhatsApp.
