# PACE GK Academy Admission Agent â€” Architecture

This document describes the current architecture of the FastAPI admission agent, how
requests flow, and how the pieces fit together.

## 1. Purpose

A WhatsApp chatbot + admission agent for **PACE GK Academy** that runs the admission
funnel over chat:

```
Student asks about admission
        â”‚
        â–¼
Chatbot answers general academy info / asks about admission
        â”‚
        â–¼
Admission agent asks / detects admission batch
        â”‚
        â–¼
Batch confirmed
        â”‚
        â–¼
Admission agent takes student input -> asks for fee paid picture
        â”‚
        â–¼
Fee payment picture submitted -> FEE_SUBMITTED (human verification)
        â”‚
        â–¼
Human-in-the-loop approval (admin WhatsApp commands)
        â”‚
        â–¼
Fee categorized â†’ Approved â†’ Community link â†’ Confirmation
        â”‚
        â–¼
Admission batch data sheet created (all registered students of the batch)
```

## 2. High-Level Layout

```
pace_gk_admission_agent/
â”œâ”€â”€ app/
â”‚   â”œâ”€â”€ main.py                  # FastAPI app entrypoint + lifespan + /admission/chat
â”‚   â”œâ”€â”€ config.py                # Settings (pydantic-settings, env-driven)
â”‚   â”œâ”€â”€ database.py              # SQLAlchemy engine/session
â”‚   â”œâ”€â”€ models/                  # SQLAlchemy ORM models (Batch, Student)
â”‚   â”œâ”€â”€ schemas/                 # Pydantic request/response schemas
â”‚   â”œâ”€â”€ routers/                 # HTTP routes (WhatsApp webhook, admin commands)
â”‚   â”œâ”€â”€ agents/                  # AdmissionAgent (LLM + deterministic flow)
â”‚   â”œâ”€â”€ services/                # Business logic (admission, batch, approval, fee, export)
â”‚   â””â”€â”€ utils/                   # WhatsApp sender (Twilio / Meta / mock)
â”œâ”€â”€ requirements.txt
â”œâ”€â”€ .env.example
â”œâ”€â”€ .env                         # local only (git-ignored)
â”œâ”€â”€ test_admission_flow.py      # end-to-end webhook tests via TestClient
â””â”€â”€ *.db                         # local SQLite DB (git-ignored)
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
        â”‚  POST /whatsapp/webhook?token=<WHATSAPP_VERIFY_TOKEN>
        â”‚  (Twilio form-encoded  OR  Meta JSON)
        â–¼
app/routers/whatsapp.py::receive_webhook
        â”‚
        â”œâ”€ Not a valid message?            â†’ {"status": "ok"}
        â”œâ”€ Admin command (APPROVE/REJECT)? â†’ _handle_admin_command
        â”‚       â””â”€ approve_student / reject_student (human-in-the-loop)
        â”‚       â””â”€ send community link / decision reply back to student
        â””â”€ Student message â†’ AdmissionAgent.generate_reply
                â””â”€ agent updates Student.status + replies (LLM or deterministic)
                â””â”€ send_whatsapp_text back to student
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
        â”‚  POST /admission/chat  { phone_number, message, media_url? }
        â–¼
AdmissionAgent.generate_reply
        â””â”€ same state machine as the WhatsApp webhook path
```

This keeps a single source of truth for the admission state machine.

## 5. Admission State Machine

Student statuses used by the agent (`app/agents/admission_agent.py`):

```
INQUIRY
    â†’ (admission intent; ask batch) â†’ INQUIRY / ask batch
    â†’ (batch chosen) â†’ BATCH_CONFIRMED
        â†’ (admission details complete + student photograph stored) â†’ FEE_PENDING
            â†’ (fee receipt image stored & validated) â†’ FEE_SUBMITTED
                â†’ (admin approves: FEE_SUBMITTED + stored receipt + complete details) â†’ APPROVED
                    â†’ (WhatsApp notifications accepted) â†’ CONFIRMED
                        â†’ (next cycle) â†’ INQUIRY
            â†’ (admin rejects) â†’ REJECTED
```

Key transitions and bot behaviour:

| Status | Bot behaviour | Exit |
|---|---|---|
| `INQUIRY` | Answer general academy questions / ask about admission | â†’ ask batch |
| `BATCH_CONFIRMED` | Ask for admission details + student photograph | â†’ `FORM_FILL` |
| `FORM_FILL` | Collect/validate the five mandatory fields across messages; ask for photograph | â†’ `FEE_PENDING` (when all five + photo valid & stored) |
| `FEE_PENDING` | Wait for a clear fee payment receipt image | â†’ `FEE_SUBMITTED` (image receipt validated + stored) |
| `FEE_SUBMITTED` | Fee receipt queued for human verification | â†’ human approval |
| `APPROVED` | Send community link; prepare batch data sheet | â†’ `CONFIRMED` (after notifications accepted) |
| `CONFIRMED` | Send confirmation; next cycle resets to `INQUIRY` |
| `REJECTED` | Decision recorded (reason optional) | â€” |

Key rules:

- Five mandatory fields: name, father name, domicile, CNIC (ID card number), student photograph. Phone number comes from the WhatsApp sender.
- `BATCH_CONFIRMED â†’ FORM_FILL`: the batch selection alone never collects details; a details message moves the student into form fill.
- `FORM_FILL â†’ FEE_PENDING` only when name, father's name, domicile, CNIC and the student photograph are all valid and stored.
- Fee receipts: text alone never advances past `FEE_PENDING`; an image receipt is downloaded from Meta (`get_media_download_url` / `download_media`), validated (image type/size, â‰¤ 5 MB, allowed MIME) and uploaded to permanent storage before the state moves to `FEE_SUBMITTED`.
- `APPROVE <student_phone> <CATEGORY>` (admin only) requires the student to be `FEE_SUBMITTED` with a stored fee receipt and complete details; approval applies the fee category. `REJECT <student_phone> [reason]` is available from the review states.
- `CONFIRMED` is set only after WhatsApp notifications (community link + approval confirmation) were accepted; an approval persists (`APPROVED`) even if a notification send fails, and can be retried.

## 6. LLM Provider Strategy

`AdmissionAgent` supports OpenAI (`gpt-4o-mini`) and Groq (`openai/gpt-oss-120b`)
via async clients; provider is chosen automatically from configured keys. When no LLM
key is configured, deterministic fallbacks are used (batch question / default reply).

## 7. Knowledge Base / RAG

- `app/services/knowledge_service.py` builds a `KnowledgeBase` from the raw source folders:
  - Academy docs: `KNOWLEDGE_DIR` (default `../DataDocs`) — source type `academy_doc`.
  - Admission docs: `ADMISSION_DOCS_DIR` (default `../AdmissionDocs`) — source type `admission_doc`.
- Supported inputs: `.docx` (via `python-docx`) and image posters (best-effort OCR via `pytesseract`).
- Retrieval: keyword (token overlap + phrase containment) scoring, re-ranked with OpenAI/Groq
  embeddings (cosine similarity) when an embedding API key is configured.
- Grounding: the agent retrieves chunks per student question and builds the LLM system prompt with
  `GROUNDING_SYSTEM_PROMPT` (`context` section). The LLM may only answer from the retrieved context;
  if nothing relevant was retrieved the agent replies with a safe fallback (`NO_INFORMATION_REPLY`).
- Batch-specific admission info (NOV26-1A/1B/1C) remains sourced from the `batches` DB records.

## 8. Fee Logic

| Category | Fee applied |
|---|---|
| `NEW` | Full batch fee (default `15000`, fallback full fee) |
| `OLD` / `ORPHAN` / `DISABLED` | 20% less than full fee |
| `NEEDY` | Free (0) after academy verification |

`apply_fee` sets `fee_category`, `fee_amount`; `mark_fee_paid` records payment.

## 8. Image Storage

- `app/services/storage_service.py` defines a `StorageProvider` abstraction
  (`LocalStorageProvider`, `SupabaseStorageProvider`) and a factory
  (`get_storage_provider`).
- Student photograph â†’ stored in folder `student_photos` (`student.student_pic_url`).
- Fee receipt â†’ stored in folder `fee_receipts` (`student.fee_paid_pic_url`).
- Images must never reference temporary Meta URLs: they are downloaded
  (`get_media_download_url` + `download_media`), validated and re-uploaded
  permanently. Receipt/approval state is only advanced after a successful
  permanent upload.
- Limits: allowed MIME types `image/jpeg`, `image/jpg`, `image/png`, `image/webp`;
  max size 5 MB.

## 9. Webhook Hardening / Idempotency

- Durable de-duplication via `processed_webhook_messages` (DB table with unique
  `message_id`) â€” survives restarts and multiple instances; duplicate deliveries are
  acknowledged (`{"status": "ok"}`) without re-running the flow.

## 10. Database

- SQLite locally: `sqlite:///./pace_gk_admission_agent.db` (tables `batches`, `students`,
  `processed_webhook_messages`).
- Production target: managed PostgreSQL (e.g. Railway Postgres) via `DATABASE_URL`.

## 11. Environment Variables

See `.env.example`. Key groups:

- **WhatsApp / Twilio / Meta:** `TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN`, `WHATSAPP_FROM_NUMBER`,
  `WHATSAPP_PHONE_NUMBER_ID`, `WHATSAPP_ACCESS_TOKEN`, `WHATSAPP_VERIFY_TOKEN`.
- **LLM:** `OPENAI_API_KEY` / `GROQ_API_KEY`, plus `OPENAI_MODEL` / `GROQ_MODEL`.
- **Database:** `DATABASE_URL`.
- **Admin:** `ADMIN_NUMBERS` (WhatsApp numbers allowed to approve / reject).
- **Storage:** `STORAGE_PROVIDER` (`local` | `supabase`), `STORAGE_LOCAL_PATH`,
  `SUPABASE_URL`, `SUPABASE_SERVICE_ROLE_KEY`, `SUPABASE_STORAGE_BUCKET`.

## 12. Local Development

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
python -m pytest
```

## 13. Deployment

Target: Railway (FastAPI service). See the deployment guide in the project README /
deployment notes for required variables and start command.
