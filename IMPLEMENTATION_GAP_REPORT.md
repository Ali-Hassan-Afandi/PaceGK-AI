# Implementation Gap Report
## PACE GK Academy Admission Agent

**Prepared:** Mon Nov 16 2026  
**Project root:** `E:\Academy\Academy_Admission_Agent_Specifications`  
**Implemented codebase:** `pace_gk_admission_agent/` (FastAPI WhatsApp admission agent)

---

## 1. Executive Summary

The project contains **three specification documents** (`PLAN.md`, `PRD.md`, `Spec.md`) describing two related but different visions:

| Document | Scope | Status |
|---|---|---|
| `PLAN.md` | WhatsApp-based admission chatbot + agent (the "build phase" plan) | **Mostly implemented** (M1–M4 done; M5 not started) |
| `PRD.md` | Full web-based academy chatbot, admission workflow, + staff operations portal (MVP) | **Not implemented** |
| `Spec.md` | Technical spec for Next.js frontend + FastAPI/Hugging Face backend + Postgres + object storage | **Not implemented** |

The actual code in `pace_gk_admission_agent/` implements the **PLAN.md WhatsApp admission agent**. It is a working, test-covered scaffold covering the core admission funnel (inquiry → batch → form → fee receipt → human approval → community link → confirmation). 

**What is yet to be implemented falls into two buckets:**
1. **Remaining PLAN.md items** (small): M5 (test series agent + interview agent), a few hardening items.
2. **The entire PRD.md / Spec.md vision** (large): Next.js frontend, staff portal, role-based access, separate user/contact/application/enrollment/payment/invitation tables, audit trail, RAG persistence (pgvector), PDF/XLSX/CSV ingestion, deployment to Vercel + Hugging Face, monitoring, backups, retention jobs, etc.

---

## 2. What IS Implemented (Current State)

The following exists in `pace_gk_admission_agent/` and is covered by tests:

### 2.1 Core admission state machine (WhatsApp)
- States: `INQUIRY → BATCH_CONFIRMED → FORM_FILL → FEE_PENDING → FEE_SUBMITTED → APPROVED → CONFIRMED` (+ `REJECTED`)
- Five mandatory fields validated deterministically: name, father's name, domicile, CNIC (13 digits), student photograph
- WhatsApp phone number captured from sender; no separate contact entry required
- LLM-assisted detail extraction (OpenAI/Groq) with deterministic regex fallback; LLM output is validated before persistence
- Safe fallback reply when no knowledge base match is found (no hallucination)

### 2.2 WhatsApp transport
- Meta WhatsApp Cloud API webhook (JSON) + Twilio form-encoded webhook
- Webhook verification (GET) for Meta
- Durable idempotency via DB table `processed_webhook_messages` (survives restarts)
- Admin `APPROVE <phone> <category>` and `REJECT <phone> [reason]` commands from authorized numbers (`03367483185`, `03463156495`, test `03327041010`)

### 2.3 Image handling (student photo + fee receipt)
- Download from Meta temporary URL → validate MIME/size/magic bytes → permanent storage (local filesystem dev; Supabase Storage prod option)
- 5 MB limit; allowed: JPEG, PNG, WEBP
- Storage abstraction (`LocalStorageProvider`, `SupabaseStorageProvider`)

### 2.4 Fee logic & human-in-the-loop
- Fee categories: `NEW` (full), `OLD`/`ORPHAN`/`DISABLED` (20% less), `NEEDY` (free)
- Batch fee from DB; default 15000 PKR
- Approval requires `FEE_SUBMITTED` + stored receipt + complete details
- `fee_paid` column set to "Yes" on approval; `approved_by`, `approved_at` recorded
- Community link + confirmation message sent (idempotent, tracked via `community_link_sent_at`, `confirmation_sent_at`)
- `CONFIRMED` only after WhatsApp API accepts notifications

### 2.5 Batch data sheet (export)
- `export_batch_students` writes one CSV per batch (`admission_data/<batch_code>_students.csv`) with all PLAN.md columns

### 2.6 Knowledge base / RAG
- Ingests `DataDocs/*.docx` and `AdmissionDocs/` images (OCR via pytesseract) + `.txt`/`.md`
- Keyword (token overlap) + optional embedding (OpenAI/Groq cosine) retrieval
- Grounded system prompt; safe fallback when nothing retrieved

### 2.7 Database (SQLite locally, Postgres target)
- Tables: `batches`, `students`, `processed_webhook_messages`
- Demo batches `NOV26-1A/1B/1C` seeded on startup
- Alembic-style idempotent schema updates (`ensure_schema`)

### 2.8 Configuration & deployment
- `.env.example` with all placeholder env vars
- `railway.json` (Nixpacks, healthcheck `/health`)
- `requirements.txt` (FastAPI, SQLAlchemy, OpenAI, Groq, httpx, python-docx, pytest)
- Test suite: 15 test files covering validation, approval, fee, batch, knowledge, storage, webhook idempotency, WhatsApp sender/media/router

---

## 3. What Is Yet to Be Implemented

### 3.1 Remaining PLAN.md items (WhatsApp agent scope)

| # | Item | PLAN.md ref | Status | Notes |
|---|------|-------------|--------|-------|
| 3.1.1 | **M5: Test series agent + interview agent** | Section 10 | ❌ Not started | Only GK admission batches exist; no test-series or interview session types |
| 3.1.2 | Exact fee amounts per batch configured (not hard-coded default) | Section 11 | ⚠️ Partial | `fee_amount` column exists but demo batches have empty fee; no staff UI to set fees |
| 3.1.3 | Batch names/IDs sourced from posters, not hard-coded | Section 11 | ⚠️ Partial | Demo batches hard-coded in `main.py`; posters exist in `AdmissionDocs` but are not parsed into batch records |
| 3.1.4 | Community link content/location finalized | Section 11 | ⚠️ Partial | `community_link` column exists; demo batches have empty links; PLAN says one link added for checking |
| 3.1.5 | Old-student eligibility data source | Section 11 | ❌ Not started | No logic to detect old vs. new student |
| 3.1.6 | Needy verification process | Section 11 | ❌ Not started | `NEEDY` category sets fee to 0 but no verification workflow |
| 3.1.7 | Admin approval UX (WhatsApp commands) refined | Section 11 | ⚠️ Partial | Basic APPROVE/REJECT commands exist; no richer UI |
| 3.1.8 | Language support (English / Urdu) | Section 11 | ❌ Not started | All replies are English |
| 3.1.9 | Consent, opt-out, rate limits, idempotency beyond message dedup | Section 11 | ⚠️ Partial | Message ID dedup exists; no consent tracking, opt-out, or rate limiting |
| 3.1.10 | Web-based app link for bot testing | README "Testing roadmap" | ❌ Not started | README lists "Later: web-based app link for bot testing" |
| 3.1.11 | Final deployment on WhatsApp (production Twilio/Meta) | README | ⚠️ Partial | Code is Twilio/Meta-ready but no credentials configured; not deployed |

### 3.2 PRD.md gaps (the broader MVP vision not yet built)

The PRD describes a website-based system with a staff portal. None of the following exists in the codebase:

| # | PRD requirement | PRD ref | Status |
|---|----------------|---------|--------|
| 3.2.1 | Responsive website chatbot (Next.js frontend) | §5 MVP | ❌ No frontend exists |
| 3.2.2 | Visitor/conversation identifier on first use (FR-01) | §10 | ❌ No `visitors` or `conversations` tables |
| 3.2.3 | Persistent user/contact records separate from applications (FR-02) | §10 | ❌ Only a `students` table; no `users`/`contacts` separation |
| 3.2.4 | Visitor can ask questions without registration (FR-03) | §10 | ❌ WhatsApp-only; no web visitor flow |
| 3.2.5 | Staff-maintained course/session catalogue with unique batch codes (FR-05, FR-06) | §10 | ⚠️ Partial | `batches` table exists but no `course_types`, no full session lifecycle (draft/scheduled/open/full/closed/in_progress/completed/cancelled/archived) |
| 3.2.6 | Multiple concurrent sessions per course category | §1, §7 | ❌ Not supported |
| 3.2.7 | Application form with unique application number + status tracking (FR-07) | §10 | ❌ No `applications` table; no `application_number`, statuses `draft/submitted/needs_information/accepted_for_payment/cancelled/closed` |
| 3.2.8 | Multiple applications/enrollments per user (FR-08) | §10 | ❌ One student record per phone; no enrollment model |
| 3.2.9 | Payment instructions by configurable supported methods | §5 MVP | ❌ Not implemented |
| 3.2.10 | Receipt image upload to private storage | §5 MVP | ⚠️ Partial | Works for WhatsApp images only; no web upload flow |
| 3.2.11 | **AI-assisted receipt field extraction + discrepancy flags** (no AI-only approval) | §5 MVP, FR-10 | ❌ Not implemented | Receipt stored but no extraction of amount/transaction reference/date |
| 3.2.12 | **Finance staff review and decision** (role-separated from admission staff) | §5 MVP, FR-11 | ⚠️ Partial | Admin approves payments; no finance vs. admission role split |
| 3.2.13 | Approval-gated WhatsApp invite release for exact session (FR-12) | §10 | ⚠️ Partial | Community link sent on approval, but no `invitation_issuances` table; link is on `batches` not per-enrollment |
| 3.2.14 | **Staff dashboard with role-based access + audit trail** (FR-13, FR-14) | §5 MVP | ❌ No staff dashboard, no `staff_users`/`staff_roles`/`audit_events` tables |
| 3.2.15 | Search/filter/export per staff role (FR-15) | §10 | ❌ Not implemented |
| 3.2.16 | Retention ≥ 12 months with class-specific privacy retention (FR-16) | §10 | ❌ No retention jobs |
| 3.2.17 | Knowledge-base ingestion without redeploy (FR-18) | §10 | ⚠️ Partial | In-memory KB rebuilt at startup; no persistence/reindexing API |
| 3.2.18 | Session closure/cancellation prevents new payments (FR-19) | §10 | ❌ Not implemented |
| 3.2.19 | Idempotency/uniqueness constraints to prevent duplicate payments/enrollments (FR-20) | §10 | ⚠️ Partial | Webhook message dedup only; no payment/enrollment uniqueness constraints |
| 3.2.20 | PDF ingestion | §9 | ❌ Not implemented (only DOCX, images, TXT/MD) |
| 3.2.21 | XLSX and CSV ingestion | §9 | ❌ Not implemented |
| 3.2.22 | OCR for scanned PDFs | §9 | ❌ Not implemented (OCR only for images) |
| 3.2.23 | Source classes: academy_approved / official_commission / unverified_reference with lifecycle | §9 | ❌ Not implemented |
| 3.2.24 | Deployment: Vercel frontend + Hugging Face Spaces backend + persistent external DB/storage | §5 MVP, §14 | ❌ Target is Railway + SQLite default |
| 3.2.25 | Health endpoints `/health/live` and `/health/ready` | Spec §14 | ❌ Only `/health` exists |
| 3.2.26 | Role matrix: Administrator / Admission / Finance / Academic (least privilege) | §4 | ❌ Not implemented |
| 3.2.27 | Payment-provider API reconciliation | Later phases | ❌ Not implemented |
| 3.2.28 | Email/SMS notifications | Later phases | ❌ Not implemented |
| 3.2.29 | Multi-language support | Later phases | ❌ Not implemented |
| 3.2.30 | Automated expiry/retention jobs + data export workflows | Later phases | ❌ Not implemented |

### 3.3 Spec.md gaps (technical specification not yet built)

| # | Spec requirement | Spec ref | Status |
|---|------------------|----------|--------|
| 3.3.1 | Next.js + TypeScript frontend (public chatbot, catalogue, staff portal) | §1 | ❌ Not implemented |
| 3.3.2 | Managed PostgreSQL (not SQLite) as system of record | §1, §4 | ❌ SQLite default; no migrations (Alembic) |
| 3.3.3 | `pgvector` / persistent vector search | §1 | ❌ In-memory knowledge base only |
| 3.3.4 | Private object storage with signed URLs (Supabase configured but not wired to prod) | §1 | ⚠️ Code exists; `STORAGE_PROVIDER=local` default |
| 3.3.5 | Full ORM model set: visitors, users, visitor_user_links, conversations, messages, course_types, sessions, applications, payments, enrollments, invitation_issuances, staff_users, staff_roles, audit_events, knowledge_sources, knowledge_chunks, ingestion_jobs | §4 | ❌ Only `batches`, `students`, `processed_webhook_messages` exist |
| 3.3.6 | Money stored as integer paisa or NUMERIC + ISO currency; no float | §4 | ⚠️ `fee_amount` is Numeric but no currency field |
| 3.3.7 | Full `/api/v1` REST contract (visitors, conversations, chat, courses, sessions, contacts, applications, payments, staff queues, decisions, enrollments, invitations, knowledge ingestion, health) | §7 | ❌ Only `/whatsapp/webhook`, `/admission/chat`, `/health` exist |
| 3.3.8 | Role-based authorization on every backend endpoint | §7, §12 | ❌ Not implemented |
| 3.3.9 | WhatsApp invite link stored as protected session config (not in RAG/public API) | §7, §11 | ⚠️ Partial | `community_link` on `batches`; not exposed in API but not per-enrollment |
| 3.3.10 | Rate limiting on chatbot/login/upload/invitation endpoints | §12 | ❌ Not implemented |
| 3.3.11 | IDOR prevention (ownership verified server-side) | §12 | ❌ Not implemented |
| 3.3.12 | Audit events append-only for privileged actions | §12 | ❌ Not implemented |
| 3.3.13 | Consent/privacy notice at point of collection | §12 | ❌ Not implemented |
| 3.3.14 | Data export/correction/deletion process | §12 | ❌ Not implemented |
| 3.3.15 | Retention + backup jobs with idempotent summaries | §13 | ❌ Not implemented |
| 3.3.16 | Beginner-friendly build plan Phases 1–10 (Spec §15) — foundation through deployment | §15 | ⚠️ Partial | WhatsApp agent phases done; web frontend, staff portal, deployment phases not done |

### 3.4 Code-level / quality gaps observed during review

| # | Gap | Severity | Details |
|---|-----|----------|---------|
| 3.4.1 | **Twilio configured but not implemented** | Medium | `.env.example` and config have `TWILIO_*` vars; router parses Twilio forms but `app/utils/whatsapp.py` only implements Meta Cloud API; no Twilio send path |
| 3.4.2 | **In-memory conversation state** | Medium | `AdmissionAgent.conversations` and `answer_cache` are Python dicts — lost on restart; not durable |
| 3.4.3 | **No Alembic migrations** | Medium | Schema changes via `ensure_schema()` ALTER TABLE only; no versioned migrations |
| 3.4.4 | **Demo batches hard-coded** | Low | `DEMO_BATCHES` in `main.py`; batch data should come from staff configuration |
| 3.4.5 | **No fee currency field** | Low | `fee_amount` is Numeric but no `currency` column (PKR assumed) |
| 3.4.6 | **No rate limiting** | Medium | Chatbot and upload endpoints have no rate limits (PRD §11, Spec §12) |
| 3.4.7 | **No tests/conftest under `tests/` root config mismatch** | Low | `pytest.ini` sets `testpaths = tests` but root-level `test_admission_flow.py` also exists; duplication |
| 3.4.8 | **No production secrets validation at startup** | Medium | Missing required env vars (WhatsApp tokens, storage keys) fail silently at runtime rather than startup |

---

## 4. Gap Analysis Matrix: Spec → Implementation

| Requirement group | PLAN.md | PRD.md | Spec.md | Implemented? |
|---|---|---|---|---|
| WhatsApp admission webhook | ✅ M1–M4 | — | — | ✅ Yes |
| Admission state machine | ✅ M1–M4 | — | — | ✅ Yes |
| Five-field form + photo validation | ✅ M2 | ✅ MVP | §4 | ✅ Yes |
| Fee receipt intake + storage | ✅ M3 | ✅ MVP | §10 | ✅ Yes |
| Human-in-the-loop approval | ✅ M3 | ✅ MVP | §6 | ✅ Yes |
| Fee categorization (NEW/OLD/ORPHAN/DISABLED/NEEDY) | ✅ M4 | ✅ MVP | §8 | ✅ Yes |
| Community link + confirmation on approval | ✅ M4 | ✅ MVP | §11 | ✅ Yes |
| Batch data sheet CSV export | ✅ M4 | — | — | ✅ Yes |
| RAG knowledge base (DOCX/images/OCR) | — | ✅ MVP | §9 | ✅ Partial (no PDF/XLSX/CSV) |
| Website frontend (Next.js) | — | ✅ MVP | §1 | ❌ No |
| Visitor/contact/user separation | — | ✅ MVP | §4 | ❌ No |
| Course/session catalogue + batch codes | — | ✅ MVP | §4 | ⚠️ Partial (`batches` only) |
| Application + enrollment models | — | ✅ MVP | §4 | ❌ No |
| Payment receipt AI extraction | — | ✅ MVP | §10 | ❌ No |
| Finance role (separate from admin) | — | ✅ MVP | §4, §12 | ❌ No |
| Staff dashboard + audit trail | — | ✅ MVP | §4, §12 | ❌ No |
| pgvector / persistent RAG | — | ✅ MVP | §1 | ❌ No |
| Managed Postgres + migrations | — | ✅ MVP | §1, §4 | ❌ No (SQLite) |
| Vercel + Hugging Face deploy | — | ✅ MVP | §14 | ❌ No (Railway target) |
| Rate limits, audit, retention, backups | — | ✅ MVP | §12, §13 | ❌ No |
| M5: test series + interview agents | ❌ M5 | Later | — | ❌ No |

---

## 5. Recommendations / Suggested Next Steps

### Immediate (within the existing WhatsApp agent)
1. **Finalize M5**: Build test-series and interview agent variants (new batch types, possibly separate state machines).
2. **Replace hard-coded demo batches** with staff-configurable batch creation (parse `AdmissionDocs` posters or add an admin endpoint).
3. **Add fee currency field** and source exact batch fees from academy (currently default 15000 PKR).
4. **Implement old-student detection** and **needy verification** workflow.
5. **Add startup validation** for required env vars (WhatsApp, storage) so misconfiguration fails fast.
6. **Add Twilio send path** (or remove Twilio env vars if Meta is the chosen provider).
7. **Add Alembic migrations** for versioned schema changes.
8. **Add rate limiting** on webhook and `/admission/chat`.

### Medium term (bridge toward PRD/Spec)
9. **Add the missing ORM models**: `course_types`, `sessions` (full lifecycle), `applications`, `enrollments`, `payments` (with receipt AI extraction), `invitation_issuances`, `staff_users`, `staff_roles`, `audit_events`, `visitors`, `conversations`, `messages`.
10. **Implement role-based access control** (admin / admission / finance / academic).
11. **Build the staff dashboard** (batch CRUD, finance review queue, audit log).
12. **Add PDF, XLSX, CSV ingestion** to the knowledge service and persistent vector store (pgvector).
13. **Implement the full `/api/v1` REST contract** per Spec §7.

### Long term
14. **Next.js frontend** on Vercel (public chatbot + staff portal).
15. **Deploy backend to Hugging Face Spaces** (Docker) with managed PostgreSQL + Supabase Storage.
16. **Monitoring, backups, retention jobs, restore tests** per Spec §13–14.
17. **Production WhatsApp rollout** with real Twilio/Meta credentials.

---

## 6. Conclusion

The **WhatsApp admission agent** (`pace_gk_admission_agent/`) is substantially complete for the PLAN.md scope: the full admission funnel, human-in-the-loop approval, fee categorization, community link delivery, batch data-sheet export, RAG knowledge base, durable webhook idempotency, and a comprehensive test suite are all implemented and tested.

**The PRD.md and Spec.md describe a significantly larger system** (web frontend, staff portal, role-based access, full relational data model, persistent RAG, production deployment pipeline) that has **not yet been built**. If the intent is to deliver the PRD/Spec MVP, the bulk of the work remains ahead. If the intent is only the WhatsApp agent per PLAN.md, the remaining work is limited to M5 and the hardening items in Section 3.1.

---

*End of report*