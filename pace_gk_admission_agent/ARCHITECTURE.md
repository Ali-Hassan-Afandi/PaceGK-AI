# PACE GK Academy Admission Agent — Architecture

This document describes the current architecture of the FastAPI admission agent, how
requests flow, and how the pieces fit together.

## 1. Purpose

A WhatsApp chatbot + admission agent for **PACE GK Academy** that runs the admission
funnel over chat:

```
Student asks about admission
        │
        ▼
Chatbot asks / detects admission batch
        │
        ▼
Batch confirmed
        │
        ▼
Admission form details collected
        │
        ▼
Fee submission received
        │
        ▼
Human-in-the-loop approval (admin WhatsApp commands)
        │
        ▼
Fee categorized → Approved → Community link → Confirmation
```

## 2. High-Level Layout

```
pace_gk_admission_agent/
├── app/
│   ├── main.py                  # FastAPI app entrypoint + lifespan + /admission/chat
│   ├── config.py                # Settings (pydantic-settings, env-driven)
│   ├── database.py              # SQLAlchemy engine/session
│   ├── models/                  # SQLAlchemy ORM models (Batch, Student)
│   ├── schemas/                 # Pydantic request/response schemas
│   ├── routers/                 # HTTP routes (WhatsApp webhook, admin commands)
│   ├── agents/                  # AdmissionAgent (LLM + deterministic flow)
│   ├── services/                # Business logic (admission, batch, approval, fee)
│   └── utils/                   # WhatsApp sender (Twilio / Meta / mock)
├── requirements.txt
├── .env.example
├── .env                         # local only (git-ignored)
├── test_admission_flow.py      # end-to-end webhook tests via TestClient
└── *.db                         # local SQLite DB (git-ignored)
```

### Layer responsibilities

| Layer | File(s) | Responsibility |
|---|---|---|
| Entrypoint | `app/main.py` | FastAPI app creation, lifespan (DB bootstrap + demo batches), routes |
| Routing / transport | `app/routers/whatsapp.py` | `/whatsapp/webhook` (Twilio + Meta), admin `APPROVE` / `REJECT` command handling |
| Agent (LLM + flow) | `app/agents/admission_agent.py` | Conversation state machine + LLM reply generation |
| Services | `app/services/*` | Student/batch lookup, fee application, approval transitions |
| Models / schemas | `app/models/*`, `app/schemas/*` | Persistence + validation contracts |
| Utils | `app/utils/whatsapp.py` | Outbound WhatsApp sending (Twilio now, Meta later, mock fallback) |
| Config | `app/config.py` | Env vars: WhatsApp, LLM, database, admin numbers |

## 3. Request Flow (WhatsApp Webhook)

```
Twilio (or Meta) WhatsApp
        │  POST /whatsapp/webhook?token=<WHATSAPP_VERIFY_TOKEN>
        │  (Twilio form-encoded  OR  Meta JSON)
        ▼
app/routers/whatsapp.py::receive_webhook
        │
        ├─ Not a valid message?            → {"status": "ok"}
        ├─ Admin command (APPROVE/REJECT)? → _handle_admin_command
        │       └─ approve_student / reject_student (human-in-the-loop)
        │       └─ send community link / decision reply back to student
        └─ Student message → AdmissionAgent.generate_reply
                └─ agent updates Student.status + replies (LLM or deterministic)
                └─ send_whatsapp_text back to student
```

- **Verification:** `GET /whatsapp/webhook?token=...` must return `200 ok` for the token
  in `WHATSAPP_VERIFY_TOKEN` (handled by Twilio webhook validation).
- **Ingress formats:** Twilio (`application/x-www-form-urlencoded` with `Body` / `From`)
  and Meta WhatsApp Cloud API (`application/json` with `entry[0].changes[0].value.messages`).
- **Admin commands:** only numbers in `ADMIN_NUMBERS` may run `APPROVE <student_phone> [CATEGORY]`
  or `REJECT <student_phone> [reason]`.

## 4. Internal Handoff Endpoint

`POST /admission/chat` (added recently) is an internal endpoint used by the
general-knowledge chatbot to hand an admission-related turn over to the admission
agent:

```
General chatbot
        │  POST /admission/chat  { phone_number, message }
        ▼
AdmissionAgent.generate_reply
        └─ same state machine as the WhatsApp webhook path
```

This keeps a single source of truth for the admission state machine.

## 5. Admission State Machine

Student statuses used by the agent (`app/agents/admission_agent.py`):

```
INQUIRY
    → (admission intent + batch code match, or LLM batch question)
    → AWAITING_BATCH
        → (batch code chosen) → BATCH_CONFIRMED
            → (form details asked) → FORM_FILL
                → (form + fee submitted) → FEE_SUBMITTED
                    → (admin approves) → FEE_CATEGORIZED
                        → (approved) → APPROVED
                            → (community link sent) → CONFIRMED
                                → (next cycle) → INQUIRY
            → (admin rejects) → REJECTED
```

Key transitions and bot behaviour:

| Status | Bot behaviour | Exit |
|---|---|---|
| `INQUIRY` | Answer admission queries / ask batch | → `AWAITING_BATCH` |
| `AWAITING_BATCH` | Ask for batch code (or LLM-assisted) | → `BATCH_CONFIRMED` / stay |
| `BATCH_CONFIRMED` | Ask for admission form details | → `FORM_FILL` |
| `FORM_FILL` | Collect form fields; fee submission step | → `FEE_SUBMITTED` |
| `FEE_SUBMITTED` | Save to DB datasheet | → human approval |
| `FEE_CATEGORIZED` | Apply fee tier | → `APPROVED` |
| `APPROVED` | Send WhatsApp community link | → `CONFIRMED` |
| `CONFIRMED` | Send confirmation; next cycle resets to `INQUIRY` |
| `REJECTED` | Decision recorded (reason optional) | — |

## 6. LLM Provider Strategy

`AdmissionAgent` supports OpenAI (`gpt-4o-mini`) and Groq (`llama-3.3-70b-versatile`)
via async clients; provider is chosen automatically from configured keys. When no LLM
key is configured, deterministic fallbacks are used (batch question / default reply).

## 7. Fee Logic

| Category | Fee applied |
|---|---|
| `NEW` | Full batch fee (default `15000`, fallback full fee) |
| `OLD` / `ORPHAN` / `DISABLED` | 20% less than full fee |
| `NEEDY` | Free (0) after academy verification |

`apply_fee` sets `fee_category`, `fee_amount`; `mark_fee_paid` records payment.

## 8. Database

- SQLite locally: `sqlite:///./pace_gk_admission_agent.db` (tables `batches`, `students`).
- Production target: managed PostgreSQL (e.g. Railway Postgres) via `DATABASE_URL`.

## 9. Environment Variables

See `.env.example`. Key groups:

- **WhatsApp / Twilio:** `TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN`, `WHATSAPP_FROM_NUMBER`,
  `WHATSAPP_PHONE_NUMBER_ID`, `WHATSAPP_ACCESS_TOKEN`, `WHATSAPP_VERIFY_TOKEN`.
- **LLM:** `OPENAI_API_KEY` / `GROQ_API_KEY`, plus `OPENAI_MODEL` / `GROQ_MODEL`.
- **Database:** `DATABASE_URL`.
- **Admin:** `ADMIN_NUMBERS` (WhatsApp numbers allowed to approve / reject).

## 10. Local Development

```bash
cd pace_gk_admission_agent
python -m venv .venv
.venv\Scripts\activate        # Windows
pip install -r requirements.txt
cp .env.example .env          # fill in secrets
uvicorn app.main:app --reload
```

Run flow tests:

```bash
.venv\Scripts\python.exe test_admission_flow.py
```

## 11. Deployment

Target: Railway (FastAPI service). See the deployment guide in the project README /
deployment notes for required variables and start command.
