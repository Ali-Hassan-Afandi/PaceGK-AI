# Spec.md — Technical Specification

**Version:** 1.0  
**Date:** 2026-10-23  
**Target deployment:** Vercel frontend + Hugging Face Spaces backend + managed persistent database and private object storage

## 1. Architecture overview

Use a modular monorepo:

```text
academy-admission-agent/
├── frontend/                  # Next.js + TypeScript website and staff portal
├── backend/
│   ├── app/
│   │   ├── main.py
│   │   ├── core/               # settings, logging, auth, permissions
│   │   ├── api/v1/             # routers
│   │   ├── models/             # ORM models
│   │   ├── schemas/            # Pydantic request/response schemas
│   │   ├── services/           # business workflows
│   │   ├── repositories/       # database access
│   │   ├── agents/             # chatbot and receipt-extraction orchestration
│   │   ├── rag/                # ingestion, OCR, chunking, retrieval
│   │   ├── integrations/       # LLM, storage, payment, WhatsApp abstractions
│   │   └── jobs/               # reindexing, retention, notification tasks
│   ├── tests/
│   ├── requirements.txt
│   └── Dockerfile
├── data/
│   └── academy_knowledge/      # user adds academy files here later; do not commit private data
├── docs/
├── .env.example
├── .gitignore
└── README.md
```

### Service boundaries

- **Frontend (Vercel):** public chatbot, course/session catalogue, application form, student status page, staff dashboard.
- **Backend (FastAPI on Hugging Face Spaces):** authentication/authorization, business rules, chatbot orchestration, admissions, payment review, enrollment, ingestion APIs.
- **Managed PostgreSQL:** durable system of record. Supabase Postgres is a practical candidate; configure connection pooling and migrations. Do not use in-memory Python objects or a Space's local disk as the canonical database.
- **Private object storage:** receipts and uploaded source documents. Use a managed storage service with private buckets and short-lived signed URLs. Never expose raw receipt objects publicly.
- **Vector search:** start with PostgreSQL + pgvector if supported by the selected database plan, otherwise use a compatible vector service. Store source metadata alongside embeddings.
- **LLM/OCR providers:** behind interfaces so providers can be changed without rewriting business logic. Keep API keys on the server only.

Free plans and provider features change. Confirm current quotas, sleep/cold-start behavior, persistent storage, database connection limits, and commercial terms before production.

## 2. Suggested implementation stack

- Frontend: Next.js, TypeScript, React, accessible component system, server-side environment variables for non-public configuration.
- Backend: Python 3.11 or a currently supported compatible version, FastAPI, Pydantic v2, SQLAlchemy 2, Alembic, PostgreSQL driver.
- Authentication: managed authentication where practical; otherwise secure server-side sessions or short-lived JWTs with refresh/revocation strategy. Staff accounts must require strong passwords and preferably MFA.
- RAG: provider-neutral embeddings interface, PostgreSQL/pgvector or equivalent, hybrid lexical/vector retrieval, source metadata.
- OCR: pluggable OCR engine for poster/scanned PDF text; verify Urdu/English support against actual source samples.
- Receipt extraction: vision-capable model or OCR + structured extraction schema; output is untrusted, advisory metadata only.
- Storage: private object storage with signed URLs and lifecycle controls.
- Tests: pytest for backend; frontend unit/component tests; Playwright or equivalent end-to-end smoke tests.
- Deployment: Vercel for frontend, Hugging Face Docker Space for FastAPI backend, managed database and object storage.

## 3. Configuration and secrets

Create `.env.example` with placeholders only:

```dotenv
APP_ENV=development
APP_NAME=Academy Admission Agent
API_V1_PREFIX=/api/v1
FRONTEND_ORIGINS=http://localhost:3000
DATABASE_URL=postgresql+psycopg://USER:PASSWORD@HOST:5432/DB
AUTH_SECRET_KEY=replace-with-long-random-secret
LLM_PROVIDER=your-provider
LLM_API_KEY=
EMBEDDING_PROVIDER=your-provider
EMBEDDING_API_KEY=
OBJECT_STORAGE_ENDPOINT=
OBJECT_STORAGE_BUCKET=
OBJECT_STORAGE_ACCESS_KEY=
OBJECT_STORAGE_SECRET_KEY=
MAX_RECEIPT_UPLOAD_MB=8
KNOWLEDGE_BASE_PATH=../data/academy_knowledge
DEFAULT_TIMEZONE=Asia/Karachi
```

Adapt database URL format to the chosen driver and provider. Never commit `.env`, real API keys, service-role keys, passwords, receipt files, student exports, or private source documents. On Vercel/Hugging Face, set secrets in project settings. Rotate any key accidentally committed.

## 4. Data model

Use UUID primary keys, UTC timestamps, and `Asia/Karachi` for displaying academy schedules. Store money as integer paisa or `NUMERIC(12,2)` plus ISO currency (`PKR`); never use floating-point arithmetic for money.

### Core tables

#### `visitors`
- `id UUID PK`
- `anonymous_id_hash` (random client ID hash; avoid fingerprinting)
- `first_seen_at`, `last_seen_at`
- `consent_status`, `consent_updated_at`
- limited diagnostic fields only where justified

#### `users`
- `id UUID PK`
- `full_name` nullable until provided
- `phone_e164` nullable; normalize Pakistan phone numbers and enforce appropriate uniqueness policy
- `whatsapp_number` nullable
- `education_qualification` nullable
- `contact_consent`, `marketing_consent`, `created_at`, `updated_at`, `last_seen_at`
- Do not assume a phone number alone proves identity. Handle shared/family phone numbers and duplicate resolution safely.

#### `visitor_user_links`
- `visitor_id`, `user_id`, `linked_at`, `link_method`
- Restrict access and log merges; never silently merge records solely on a weak match.

#### `conversations`
- `id UUID PK`, `visitor_id` nullable, `user_id` nullable
- `channel` (website), `started_at`, `ended_at`, `consent_snapshot`, `status`

#### `messages`
- `id UUID PK`, `conversation_id FK`, `role`, `content`, `created_at`
- `source_refs JSONB`, `model/provider metadata` (avoid storing hidden reasoning)
- apply a defined retention policy and access controls; consider redaction of unnecessary personal data.

#### `course_types`
- `id UUID PK`, `name`, `category`, `description`, `active`, timestamps
- Examples: FPSC, PPSC, KPSC, SPSC, GK, test series, interview preparation, other exams.

#### `sessions`
- `id UUID PK`, `course_type_id FK`, `batch_code UNIQUE`, `title`
- `target_commission`, `target_exam_or_post`
- `registration_opens_at`, `registration_closes_at`, `starts_at`, `ends_at`
- `timezone`, `fee_amount`, `currency`, `fee_model`, `capacity` nullable
- `status`, `schedule_json`, `payment_instructions_json`
- `whatsapp_invite_secret_ref` or encrypted invite value, `invite_status`
- `created_by`, `updated_by`, timestamps

`batch_code` must be unique. Use a format configured by the academy, for example `GK-2026-11-A`; this is illustrative only, not a real batch. Do not derive a batch's identity from month or course name alone. If the academy sells a multi-month package, represent the package explicitly and define how it maps to included sessions.

#### `applications`
- `id UUID PK`, `application_number UNIQUE`, `user_id FK`, `session_id FK`
- `target_commission`, `target_exam`, `target_post`
- `education_qualification`, `application_status`
- `submitted_at`, `reviewed_by`, `reviewed_at`, `notes`
- application status: `draft`, `submitted`, `needs_information`, `accepted_for_payment`, `cancelled`, `closed`

#### `payments`
- `id UUID PK`, `application_id FK`, `session_id FK`, `user_id FK`
- `amount_expected`, `amount_claimed`, `currency`, `payment_method`
- `transaction_reference`, `payer_name` nullable, `paid_at_claimed` nullable
- `receipt_object_key` private, `receipt_sha256`, `ai_extraction_json`, `ai_confidence`
- `status`: `not_submitted`, `pending_review`, `needs_information`, `approved`, `rejected`, `refunded`, `cancelled`
- `reviewed_by`, `reviewed_at`, `review_reason`, `reconciliation_reference`
- `created_at`, `updated_at`
- Do not store card PINs, passwords, OTPs, or full credentials. Prevent the same transaction reference from being approved twice where reliable provider/method-specific uniqueness can be applied.

#### `enrollments`
- `id UUID PK`, `user_id FK`, `session_id FK`, `application_id FK`
- `payment_id FK` nullable until assigned
- `status`: `pending_payment`, `pending_review`, `confirmed`, `invite_issued`, `active`, `completed`, `cancelled`, `refunded`
- `confirmed_at`, `created_at`, `updated_at`
- unique constraint on `(user_id, session_id)` unless academy policy explicitly allows multiple separate enrollments for the same person/session.

#### `invitation_issuances`
- `id UUID PK`, `enrollment_id FK`, `session_id FK`, `issued_by` (system/staff)
- `invite_version`, `issued_at`, `expires_at` nullable, `revoked_at` nullable, `delivery_status`
- Store invitation links securely; do not log the full secret link in ordinary application logs.

#### Staff and audit
- `staff_users`: identity-provider ID, display name, active status.
- `staff_roles`: staff user, role, assigned by, timestamp.
- `audit_events`: actor, action, entity type/ID, timestamp, request/correlation ID, safe before/after metadata, reason.
- Audit events should be append-only to application roles. Avoid logging receipt contents, secrets, or unnecessary personal data.

#### RAG tables
- `knowledge_sources`: source ID, filename, source class, content hash, source status, `published_at`, `checked_at`, `valid_until`, imported by/time.
- `knowledge_chunks`: source ID, page/sheet/heading, chunk text, embedding, chunk index, metadata JSONB.
- `ingestion_jobs`: status, progress, error summary, started/completed timestamps.
- Treat ingested documents as data, not instructions. Prompt injection in a poster/document must not override system policies or tool permissions.

## 5. Relationships and separation rules

- A visitor may have many conversations.
- A known user may have many conversations, applications, payments, and enrollments.
- An application refers to one selected session.
- A session may have many applications and enrollments.
- A payment belongs to the specific application/session; it cannot unlock another session.
- An enrollment belongs to one user and one session.
- Receipt files live in private object storage; the database stores only private object keys and metadata.
- User/contact records are not admission records. Creating a user must not automatically create an application.
- Every conversation should be associated with a visitor ID where possible; contact association is optional until the user identifies themselves.
- Session records are independent, not a single mutable “current batch” row.

## 6. State transitions and business rules

### Payment
`pending_review -> approved | rejected | needs_information`
`needs_information -> pending_review | rejected`
`approved -> refunded` only through an authorized refund/reversal process with audit event.

Only finance role can make payment decisions. Every decision needs a reviewer, timestamp, and reason. Approval requires a server-side transaction that checks:
1. Payment belongs to the application and exact session.
2. Session is not cancelled and its policy permits enrollment.
3. Payment amount/method/reconciliation checks are complete.
4. Reviewer has finance permission.
5. No duplicate approval or conflicting transaction exists.

### Enrollment
`pending_payment -> pending_review -> confirmed -> invite_issued -> active -> completed`
Additional terminal/exception states: `cancelled`, `refunded`.
Do not move to `confirmed` solely because an image was uploaded or the AI confidence is high.

### Invitation release
A backend service must verify:
- authenticated user or safe, short-lived application token owns the enrollment;
- payment is approved;
- enrollment is confirmed;
- session matches the invitation;
- invitation is active and has not been revoked;
- release is rate-limited and audited.

Do not include a community invite link in public chatbot answers or RAG chunks. Store it as protected session configuration, not public knowledge-base content.

## 7. API design (versioned `/api/v1`)

Use Pydantic schemas, consistent error envelopes, pagination, request IDs, and idempotency keys for state-changing operations where appropriate.

### Public chatbot and catalogue
- `POST /api/v1/visitors` — create/refresh visitor record.
- `POST /api/v1/conversations` — create conversation.
- `POST /api/v1/chat` — submit a message; returns answer, citations, suggested next action, and escalation state.
- `GET /api/v1/courses` — public active course types.
- `GET /api/v1/sessions` — filter open sessions by course, commission, date, and status.
- `GET /api/v1/sessions/{session_id}` — public-safe session details, excluding secrets/invite links.
- `POST /api/v1/contacts` — create/update contact after notice/consent.
- `POST /api/v1/applications` — submit application.
- `GET /api/v1/applications/{application_number}/status` — safe status endpoint requiring an ownership token or authentication; do not allow enumeration.

### Payment
- `POST /api/v1/applications/{application_id}/payments` — create payment attempt and issue upload authorization.
- `POST /api/v1/payments/{payment_id}/receipt` — upload receipt through signed URL or validated upload service.
- `GET /api/v1/payments/{payment_id}/status` — owner/staff authorized status.
- `GET /api/v1/staff/payments` — finance queue, filters and pagination.
- `POST /api/v1/staff/payments/{payment_id}/decision` — approve/reject/request info with reason and reconciliation data.

### Enrollment and invitations
- `GET /api/v1/student/enrollments` — authenticated student's enrollments.
- `POST /api/v1/staff/enrollments/{enrollment_id}/confirm` — authorized workflow action if not performed transactionally with payment approval.
- `POST /api/v1/enrollments/{enrollment_id}/invitation` — owner-safe, approval-gated invitation release.
- `POST /api/v1/staff/sessions/{session_id}/invite/rotate` — rotate/revoke invitation, admin-authorized.

### Staff operations
- CRUD endpoints for sessions/course types, with staff role checks.
- `GET /api/v1/staff/users`, `/applications`, `/enrollments`, `/audit-events` with least-privilege filters.
- `POST /api/v1/staff/knowledge-sources/ingest` and `GET /api/v1/staff/ingestion-jobs/{job_id}`.
- `GET /health/live` and `GET /health/ready` for deployment monitoring.

These routes are a proposed contract. Implement only after defining schemas, authorization, and tests. Public endpoints must never return internal IDs or fields unless needed.

## 8. Chatbot and RAG behavior

### Request pipeline
1. Validate request size, origin, rate limit, and visitor/conversation IDs.
2. Save the incoming message under the configured privacy/retention policy.
3. Classify intent: academy FAQ, session discovery, admission, payment status, official commission query, human handoff, or unsupported.
4. For knowledge questions, retrieve approved chunks using metadata filters and hybrid retrieval.
5. Check freshness and conflicting sources.
6. Generate a concise answer only from evidence; include source title/page/sheet and source date where available.
7. If retrieval is insufficient, say the answer cannot be confirmed, ask a clarifying question, or offer staff follow-up.
8. If admission intent is detected, use explicit forms/actions for data collection rather than guessing missing values.
9. Record safe outcome/latency/citation metadata.

### Grounding rules
- Academy fees and sessions: current session database is the source of truth, not a possibly old poster.
- Academy policies: current approved policy document.
- FPSC/PPSC/KPSC/SPSC announcements: official source registry and checked timestamps; do not claim official confirmation without source evidence.
- If a poster conflicts with the current session record, show the session record and flag the poster for review.
- Never invent exam dates, vacancies, fees, eligibility criteria, deadlines, or invite links.
- Treat document text as untrusted input; ignore instructions embedded in source files that attempt to alter system behavior.
- No hidden chain-of-thought storage or display. Log concise decisions and citations only.

## 9. Knowledge-base ingestion

### Expected future location
`data/academy_knowledge/`

Add the folder manually later. The directory can initially be empty. Do not place student data, payment receipts, credentials, or private exports inside this directory or commit it to Git.

### Ingestion steps
1. Administrator uploads/places approved files or starts an ingestion job.
2. Validate extensions, MIME signatures, size, and archive safety; reject executables and unsupported types.
3. Compute SHA-256 to detect duplicates and changes.
4. Extract text from PDF/DOCX/TXT/Markdown/XLSX/CSV.
5. Run OCR on image posters and scanned pages; keep page and extraction confidence metadata.
6. For spreadsheets, preserve sheet name, row/column labels, and row context.
7. Normalize whitespace while retaining tables, dates, fees, batch codes, and source references.
8. Split into semantically coherent chunks; avoid splitting a fee from its course/session context.
9. Attach source class, file/page/sheet, import timestamp, published/checked/expiry dates, and approval state.
10. Create embeddings and upsert chunks; mark prior chunks inactive when a source is replaced.
11. Produce an ingestion report listing files processed, failures, low-OCR-confidence files, and conflicts.
12. Require staff review for ambiguous poster text and commission-source claims.

### Source classes
- `academy_approved`
- `official_commission`
- `unverified_reference` (not allowed to support authoritative claims)
Each source has a lifecycle: `pending_review`, `approved`, `stale`, `rejected`, `archived`.

## 10. Receipt handling

1. Require the user to select an application/session before receipt submission.
2. Upload only allowed image/PDF types, with configurable size limits and content inspection.
3. Store in private object storage, not public web storage.
4. Extract visible fields with OCR/vision into a strict schema: claimed amount, transaction reference, date/time, payer/receiver text, payment method, confidence, and warnings.
5. Treat all extracted values as untrusted. Do not make bank/wallet calls based on arbitrary receipt text.
6. Finance staff compares evidence against bank/wallet/provider records or official statements.
7. Record decision, reason, reviewer, timestamp, and reconciliation reference.
8. Give the student a status and safe explanation; do not expose internal fraud rules.
9. Restrict access to receipt files; create short-lived signed viewing links and audit their use.
10. Define deletion/retention for receipts and related records according to legal/accounting needs and the academy's published policy.

## 11. WhatsApp integration limits and safe implementation

### MVP
- Staff configure a separate invite link for each session.
- Keep the link private and encrypted or otherwise protected.
- After payment approval, the backend issues the correct session link to the verified applicant/student and records issuance.
- Admin can rotate/revoke links and stop new releases.
- Never put invite links in chatbot RAG documents or public session API responses.

### Later official integration
Before building direct messaging or community administration, confirm the current official WhatsApp Business Platform/Cloud API capabilities, eligible account setup, user opt-in, template rules, messaging windows, and whether the required community/group operations are supported. The existence of a messaging API does not imply that it can add members to WhatsApp Communities or manage every group operation.

Do not use unofficial browser automation, session-cookie scraping, or tools that risk account bans or expose credentials. If official APIs do not support the exact operation, retain invite-link delivery and staff-managed enrollment.

## 12. Security and privacy controls

- TLS everywhere; strict CORS allowlist for the Vercel production domain and approved local development origin.
- Authenticate staff; enforce authorization in every backend route, not only by hiding frontend controls.
- Roles: `admin`, `admission`, `finance`, `academic`; explicit permission matrix and tests.
- Use managed auth where practical, MFA for staff if available, strong password hashing if self-managed, session expiry and revocation.
- Store secrets in deployment settings; least-privilege credentials per environment.
- Private object buckets, signed URLs, file type/size checks, malware scanning where available, and rate limits.
- Parameterized ORM queries; schema validation; CSRF protection if cookie-based auth; XSS-safe rendering; secure headers.
- Rate-limit chatbot, login, application-status, receipt-upload, and invitation endpoints.
- Prevent IDOR: every student request must verify ownership server-side.
- Avoid leaking personal data in application logs; redact phone numbers and receipt data.
- Encrypt sensitive fields at rest where supported; protect database backups and restrict access.
- Append-only audit events for staff role changes, payment decisions, session changes, invitation release/rotation, exports, and data deletion.
- Consent and privacy notice at the point of collection; clear purpose and contact details.
- Provide data export/correction/deletion process where legally applicable. Retention exceptions must be documented.
- Back up database and storage metadata; periodically test restore.
- Never use student data or receipts for model training unless there is a separate valid legal basis and explicit policy/consent.

## 13. Retention and backup jobs

- Operational records for sessions, applications, enrollments, payments, and audit history: retain at least 12 months.
- Conversation text, visitor analytics, contact details, and receipt files: each must have a documented retention period; use minimum necessary retention, then delete or anonymize when appropriate.
- Use scheduled jobs to identify eligible records, but require policy approval before destructive deletion. Keep legal/accounting holds and audit the job.
- Set finite backup retention and document restore-point objectives.
- Retention jobs must be idempotent and produce a summary report without copying personal data into logs.

## 14. Deployment runbook

### A. GitHub
1. Create a private repository initially if it contains configuration or early test data.
2. Commit code, migration files, `.env.example`, and documentation only.
3. Add `.env`, `.venv/`, `node_modules/`, `.next/`, `__pycache__/`, local databases, logs, uploaded receipts, private academy/student data, and generated indexes to `.gitignore`.
4. Never commit real secrets or actual student records.

### B. Database and storage
1. Create a managed PostgreSQL database.
2. Enable required extensions (such as `pgvector`) only if available on the chosen plan.
3. Create private object storage for receipts and source uploads.
4. Set production credentials as server-side environment variables.
5. Run Alembic migrations and verify backup/restore configuration.
6. Create initial administrator securely; do not ship default passwords.

### C. Backend on Hugging Face Spaces
1. Create a Docker Space for the FastAPI backend.
2. Add a Dockerfile that starts Uvicorn on the port expected by the Space configuration.
3. Set database, LLM, embedding, storage, and auth secrets in Space Settings.
4. Confirm the database is external and persistent; do not rely on the Space's local disk for canonical data.
5. Add `/health/live` and `/health/ready`.
6. Verify CORS allows only the production frontend and approved development origins.
7. Test cold start, timeouts, uploads, database connection limits, and LLM quotas.
8. Configure logs without secrets or receipt content.

### D. Frontend on Vercel
1. Import the GitHub repository and set the root directory to `frontend/`.
2. Add a public API base URL pointing to the deployed backend.
3. Put only intentionally public variables in `NEXT_PUBLIC_*`; never put secret keys there.
4. Set the production domain in backend CORS.
5. Deploy a preview, run end-to-end tests, then promote to production.

### E. Production checks
- Public chatbot can answer a grounded sample question.
- Empty knowledge base returns a safe fallback.
- Database records survive backend restart/redeployment.
- Staff login and all role restrictions work.
- Receipt remains private.
- Payment approval is required before invite release.
- A session's link cannot be used to access another session's enrollment data.
- Verify monitoring, backups, restore procedure, privacy notice, and retention schedule.

## 15. Beginner-friendly build plan

### Phase 1 — Repository foundation
- [ ] Install Git, Python, Node.js LTS, and VS Code.
- [ ] Create the repository and folders from Section 1.
- [ ] Add `.gitignore`, `.env.example`, and README.
- [ ] Create FastAPI health route and Next.js starter page.
- [ ] Confirm frontend can call backend locally.

### Phase 2 — Database
- [ ] Provision PostgreSQL.
- [ ] Configure SQLAlchemy and Alembic.
- [ ] Create tables for users, visitors, conversations, course types, sessions, applications, payments, enrollments, staff roles, and audit events.
- [ ] Add foreign keys, indexes, unique batch codes, and transaction constraints.
- [ ] Write migration and CRUD tests before adding AI.

### Phase 3 — Authentication and staff roles
- [ ] Implement staff sign-in and initial admin setup.
- [ ] Define role/permission matrix.
- [ ] Protect every staff API.
- [ ] Test that admission staff cannot approve payments without finance permission.
- [ ] Add audit events for privileged actions.

### Phase 4 — Course and session catalogue
- [ ] Build course-type management.
- [ ] Build session creation/edit/close/cancel screens.
- [ ] Validate unique batch codes and PKR fees.
- [ ] Add two simultaneous interview sessions, monthly GK examples, and test-series examples as clearly labelled test data.
- [ ] Test that editing one session does not alter another.

### Phase 5 — Visitor tracking and chatbot
- [ ] Create visitor and conversation IDs.
- [ ] Add privacy notice and optional contact capture.
- [ ] Add chat UI, backend chat endpoint, rate limiting, and message persistence.
- [ ] Implement RAG only after database/session truth is available.
- [ ] Show source references and safe fallbacks.

### Phase 6 — Knowledge ingestion
- [ ] Create `data/academy_knowledge/` and document how to copy the academy folder there.
- [ ] Implement PDF, DOCX, TXT/Markdown, image/OCR, XLSX, and CSV parsers.
- [ ] Add source approval, content hashes, reindexing, and ingestion reports.
- [ ] Test scanned posters, spreadsheets, conflicting fees, and empty folders.
- [ ] Add official commission material separately after identifying trusted official sources.

### Phase 7 — Applications
- [ ] Implement course/session discovery.
- [ ] Collect name, phone, target commission/exam/post, qualification, and chosen session.
- [ ] Create/match the user record independently of the application.
- [ ] Show an application reference and status.
- [ ] Test one user with multiple sessions and duplicate submissions.

### Phase 8 — Payments and review
- [ ] Configure academy-approved payment methods and instructions.
- [ ] Set up private receipt storage and signed upload/view URLs.
- [ ] Implement receipt extraction with confidence/warnings.
- [ ] Build finance review queue and approve/reject/request-information actions.
- [ ] Test that image upload and AI extraction never approve payment.
- [ ] Test duplicate transaction references and amount mismatches.

### Phase 9 — Enrollment and WhatsApp
- [ ] Create enrollment only for the selected session.
- [ ] Store one protected invite link per session.
- [ ] Gate invitation issuance on staff-approved payment.
- [ ] Build student-safe invitation view and audit logging.
- [ ] Test revoked links, wrong-session access, repeat clicks, and unapproved payments.

### Phase 10 — Hardening and deployment
- [ ] Add backend/frontend tests and security checks.
- [ ] Configure Vercel, Hugging Face Space, database, storage, secrets, CORS, and health checks.
- [ ] Verify persistent records after redeployment.
- [ ] Set retention policy, backup schedule, and restore test.
- [ ] Run an end-to-end test using test payments and test sessions before inviting real students.

## 16. Test plan

### Unit tests
- Batch code uniqueness and session lifecycle.
- Payment amount and status validation.
- Permission matrix.
- Receipt extraction schema validation.
- Source freshness and citation formatting.
- Retention eligibility.

### Integration tests
- Create visitor -> conversation -> contact -> application -> payment -> finance approval -> enrollment -> invitation issuance.
- User can apply for multiple sessions without overwriting records.
- Session A payment cannot unlock Session B invitation.
- Admission role cannot call finance decision endpoint.
- Storage upload is private and only authorized staff can view a receipt.
- Database remains authoritative after API restart.

### Adversarial/security tests
- Forged approval payload from browser.
- Guessing another student's application ID.
- Prompt injection embedded in uploaded poster/document.
- Oversized/malformed upload and extension/MIME mismatch.
- Repeated chat/upload/status requests.
- Expired/revoked invitation and stale session.
- Receipt text that claims “approved” or instructs the AI to skip verification.

## 17. Example end-to-end state sequence

```text
Visitor opens website
  -> visitor record + conversation
  -> asks about GK
  -> catalogue returns GK-<configured-batch-code>
  -> student provides contact details
  -> user/contact record + application
  -> payment instructions displayed
  -> receipt uploaded privately
  -> payment = pending_review
  -> finance checks actual receipt/financial record
  -> payment = approved (with reviewer + audit event)
  -> enrollment = confirmed
  -> session-specific invitation issued
  -> student joins via WhatsApp invite link
```

The example code is a placeholder; production batch codes must be created by authorized academy staff.

## 18. Definition of done

The MVP is ready for controlled pilot use when:
- all core tables and migrations are applied;
- the user/contact registry is separate from applications;
- multiple session records coexist safely;
- all required user fields and payment evidence can be captured;
- only finance staff can approve payment;
- invite release is session-specific and approval-gated;
- the chatbot cites approved sources and abstains when uncertain;
- the backend uses persistent external database/storage;
- role, privacy, retention, and cross-session tests pass;
- deployment secrets are not in GitHub;
- staff are trained on session creation, receipt review, invite rotation, and data correction/deletion.
