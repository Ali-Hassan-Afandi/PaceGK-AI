# PRD.md — Online Academy Chatbot & Admission Agent

**Document status:** Baseline product requirements  
**Version:** 1.0  
**Date:** 2026-10-23  
**Product:** Online Academy Website Chatbot, Admission Workflow, and Staff Operations Portal

## 1. Product vision

Build a website-based AI assistant for a Pakistani online competitive-exam academy. The assistant answers questions using approved academy materials, helps prospective students choose courses and sessions, captures leads, guides applicants through fee payment, accepts payment receipt screenshots for review, and—only after authorized staff approval—provides the correct WhatsApp community invitation.

The system must support FPSC, PPSC, KPSC, SPSC, General Knowledge (GK), test series, interview preparation, and other configurable exams/courses. It must support multiple concurrently active sessions for the same course category, including monthly GK intakes, interview sessions, and distinct test-series batches.

## 2. Problem statement

Information is distributed across posters, documents, spreadsheets, and text announcements. Students repeatedly ask questions about course availability, fees, dates, eligibility, and admission. Staff manually gather student details, inspect payment screenshots, and place paid students in the appropriate WhatsApp community. Without structured records, enquiries can be lost, sessions can be confused, and payment or enrollment status can become inconsistent.

## 3. Goals and measurable outcomes

1. Answer common questions from approved, traceable academy sources.
2. Record every chatbot visitor interaction at an appropriate level, subject to the published privacy notice and retention policy.
3. Maintain a separate all-users/contacts registry and separate admission application records.
4. Model courses and individual sessions/batches as distinct entities with unique batch codes.
5. Track each student's enrollment and payment separately for each session.
6. Require staff approval of payment before enrollment is confirmed or a WhatsApp invitation is released.
7. Provide role-specific staff screens and an audit trail for important actions.
8. Retain session, enrollment, payment, and operational history for at least 12 months, subject to applicable law and a documented retention policy.
9. Deploy the public website and backend independently of the owner's personal computer.

### Initial success measures

- Every new visitor can be assigned a visitor/session identifier.
- Every created admission application has a unique ID and links to a user/contact.
- Every session has a unique batch code and independently configurable fee, dates, capacity, status, and WhatsApp invitation.
- No unapproved payment can cause automatic confirmation or release of a community invitation.
- Staff can find a student's complete session-by-session history.
- Knowledge-based answers show source references where possible and abstain when the evidence is missing or conflicting.

## 4. Users and roles

### Public users
- Anonymous visitor: browses, asks questions, and can optionally provide contact details.
- Registered/enquiry user: has a persistent contact record and can return to continue an enquiry.
- Applicant/student: has one or more admission applications and enrollments.

### Internal roles
- **Administrator:** manages staff accounts/roles, settings, retention, courses, sessions, reports, and audit access.
- **Admission staff:** reviews enquiries and applications, assists with course selection, and views relevant student information.
- **Finance staff:** reviews payment evidence, records verified/rejected/needs-more-information decisions, and records reconciliation references.
- **Academic staff:** manages approved learning/course information, schedules, and academic announcements. Academic staff should not automatically receive finance privileges.

Use least-privilege access. A staff member may have more than one role only if explicitly assigned by an administrator.

## 5. Scope

### MVP — required
- Responsive website chatbot.
- Persistent visitor, conversation, and user/contact tracking.
- Separate user/contact and admission application records.
- Knowledge-base ingestion for PDF, DOCX, TXT/Markdown, images/posters with OCR, XLSX, and CSV.
- Retrieval-augmented generation (RAG) over approved academy documents.
- Commission/exam/course/session discovery.
- Course/session catalogue with unique batch codes and independent session fees/dates.
- Application form and status tracking.
- Payment instructions by configurable supported methods.
- Receipt image upload to private storage.
- AI-assisted receipt field extraction and discrepancy flags; no AI-only payment approval.
- Finance staff review and decision.
- Approval-gated WhatsApp invite release for the exact session.
- Staff dashboard with role-based access and audit trail.
- Deployment configuration for Vercel frontend + Hugging Face Spaces/API + persistent external database/storage.
- Basic monitoring, backups, tests, and operator documentation.

### Later phases
- Official commission announcement ingestion/monitoring.
- WhatsApp Business Platform messaging integration, if account eligibility, templates, opt-in, and provider requirements are met.
- Payment-provider API reconciliation.
- Email/SMS notifications.
- Advanced analytics and multi-language support.
- Automated expiry/retention jobs and data export workflows.

### Out of scope for MVP
- Automatically declaring a payment genuine based only on a screenshot.
- Automatically adding users to WhatsApp communities through unsupported or unofficial mechanisms.
- Guaranteeing eligibility, exam dates, vacancies, or official policy without a current reliable source.
- Scraping private WhatsApp communities.
- Treating unofficial academy posters as official commission notices.

## 6. Core user journeys

### A. Visitor asks a question
1. Website creates/uses a random visitor ID and conversation ID.
2. Show a concise privacy notice before requesting personal details or a receipt.
3. User asks about a course, commission, post, fee, schedule, or batch.
4. RAG searches approved, current documents and returns a grounded answer with document/date references where available.
5. If the answer cannot be supported, the assistant says it cannot confirm and offers human follow-up.
6. With suitable notice/consent, store conversation events and optional contact details.

### B. Student explores and applies
1. Assistant clarifies target commission, exam/post, course type, and preferred session.
2. Show currently open sessions and distinguish sessions by title, batch code, dates, fee, and status.
3. Student supplies full name, phone/WhatsApp number, target commission/exam/post, selected session, and education/qualification.
4. Create or match a user/contact record; create a separate application record.
5. Show application reference and next steps.
6. Do not collect unnecessary identity documents or sensitive information in the MVP.

### C. Payment submission and verification
1. Student opens the specific application/session and sees approved payment methods and exact fee.
2. Student submits a receipt screenshot and, if required, transaction reference, amount, date, and payer name.
3. Store receipt privately; create a payment record linked to the application and session.
4. AI may extract visible fields and flag mismatch/low confidence; show extracted values to finance staff.
5. Payment enters `pending_review`.
6. Authorized finance staff checks the actual payment channel/statement or provider records and approves, rejects, or requests more information.
7. Record reviewer, timestamp, reason, and reconciliation reference. Never store payment PINs, passwords, or OTPs.
8. Only an approved payment can make the relevant enrollment eligible for confirmation.

### D. WhatsApp invitation and enrollment
1. On approval, the system confirms the exact session and checks that its invite link is active.
2. The student is shown/sent the invite link for that session only.
3. Record invitation issuance time and enrollment state; avoid exposing invite links to unrelated users.
4. Staff can revoke/rotate a link and update its state.
5. Do not promise that the system can directly add a person to a WhatsApp community. The MVP sends an invitation link. Direct messaging or group-management automation depends on official supported APIs and permissions and may not expose community administration functions.

### E. Returning student and multiple sessions
- One user can have multiple applications and enrollments.
- Each enrollment belongs to one specific session/batch.
- Payments are linked to a specific application/enrollment/session and are never inferred from another course's payment.
- A GK student can enroll in January, February, and March batches separately, or a defined multi-month package if the academy explicitly configures it.
- Each interview session and each test series receives its own unique batch code and lifecycle.

## 7. Course and session model

A **course type** is a category such as GK, FPSC preparation, test series, or interview preparation. A **session/batch** is a specific delivery/intake instance.

Each session must include:
- Unique session ID and unique human-readable batch code.
- Course type, display title, target commission/exam/post (where relevant).
- Start/end dates, registration opening/closing dates, timezone (`Asia/Karachi`), status, capacity (optional), and schedule.
- Fee amount and currency (PKR), payment instructions, and whether it is a single payment or an explicitly defined installment/package.
- WhatsApp invite link reference/status and staff owner.
- Created/updated timestamps and change history.

Suggested session states: `draft`, `scheduled`, `open`, `full`, `closed`, `in_progress`, `completed`, `cancelled`, `archived`.

Batch codes must be unique and immutable once published; if a correction is required, preserve the old code as an alias or record the change in audit history. Never identify a session using only a generic label like “GK batch.”

## 8. Data separation and retention

The database must logically separate the following records (they may share one managed database but must not be collapsed into a single table):

1. **Visitor/analytics records:** random visitor ID, timestamps, consent state, coarse technical diagnostics where justified, and conversation IDs. Avoid unnecessary device fingerprinting.
2. **Users/contacts:** one record per known person, contact details, consent/preferences, creation and last-seen timestamps.
3. **Conversations/messages:** conversation and message history, source citations, timestamps, and escalation events.
4. **Admission applications:** target exam/post, selected session, qualification, application status, and user link.
5. **Courses and sessions:** catalogue definitions and individual batch instances.
6. **Payments and receipt metadata:** amount, method, transaction reference, private receipt object key, AI extraction, review decision, reviewer, and timestamps.
7. **Enrollments:** one per student/session, with independent status.
8. **Invitation issuance:** session-specific link reference, issue time, expiry/revocation state, and delivery status.
9. **Staff accounts/roles and audit events.**

Retain session, enrollment, payment-review, and operational records for **at least 12 months**. Configure retention by record class. Since chat messages, receipts, and personal data can carry additional privacy risk, define a published retention period for each class and a lawful deletion/anonymization workflow. Do not retain everything indefinitely by default. Legal, accounting, or dispute-related holds require an authorized documented policy. Backups should have their own finite expiry schedule.

## 9. Knowledge base and source quality

The academy's existing folder will be added later at a documented location such as `data/academy_knowledge/`. The app must start gracefully when that folder is empty and show “knowledge base not yet configured” to administrators.

Supported input formats: PDF, DOCX, TXT, Markdown, PNG/JPG posters, XLSX, and CSV. OCR is required for text embedded in posters/scanned pages. Extracted content should retain source filename, page/sheet, heading, modified/import time, content hash, and validity dates where available.

Keep source classes separate:
- Academy-approved materials: course details, fees, schedules, policies, posters.
- Official commission sources: official domains/documents imported and reviewed separately.
- Unverified/public commentary: excluded from authoritative claims unless explicitly labelled.

When facts conflict or expire, prefer the latest approved source under an explicit policy and flag the conflict for staff. The assistant must not invent vacancies, exam dates, eligibility, fees, or batch availability. Commission information is not included in the existing academy folder by assumption; it must be sourced and maintained separately.

## 10. Functional requirements

- **FR-01:** Create a visitor/conversation identifier on first use.
- **FR-02:** Persist user/contact details separately from applications.
- **FR-03:** Allow a visitor to ask questions without forcing registration for every question.
- **FR-04:** Answer from retrieved evidence and expose source references where possible.
- **FR-05:** Provide a staff-maintained catalogue of courses and sessions.
- **FR-06:** Enforce unique session IDs and batch codes.
- **FR-07:** Capture the required application fields and link them to the chosen session.
- **FR-08:** Allow multiple applications/enrollments per user.
- **FR-09:** Accept private receipt uploads with file type/size checks.
- **FR-10:** Use AI only for assistance/extraction, not final payment approval.
- **FR-11:** Enforce finance role for payment decisions.
- **FR-12:** Release session-specific WhatsApp invitation only after payment approval and eligibility checks.
- **FR-13:** Maintain an append-only audit trail for sensitive state changes.
- **FR-14:** Enforce role-based authorization on backend endpoints.
- **FR-15:** Provide search/filter/export controls appropriate to each staff role.
- **FR-16:** Retain operational records for at least 12 months, with configurable class-specific privacy retention.
- **FR-17:** Show uncertainty and escalate when source evidence is absent, stale, or conflicting.
- **FR-18:** Support knowledge-base ingestion and re-indexing without redeploying the frontend.
- **FR-19:** Support session closure/cancellation and prevent new payments/enrollments when not allowed.
- **FR-20:** Prevent duplicate payment processing and duplicate enrollments using idempotency/uniqueness constraints.

## 11. Non-functional requirements

- Security: TLS, private storage, secrets in platform environment variables, password hashing or managed identity, role checks, upload validation, rate limiting, audit logs.
- Reliability: persistent external database; do not use local filesystem or in-memory state as the production source of truth.
- Performance: paginate staff tables; limit upload sizes; make long ingestion tasks asynchronous where practical.
- Maintainability: modular Python API, typed request/response schemas, migrations, environment examples, and automated tests.
- Accessibility: responsive layout, readable contrast, keyboard navigation, form labels, and clear validation errors.
- Explainability: cite the source document/page/sheet for knowledge-base answers where available.
- Privacy: collect only required information, display notice, restrict receipt access, and define deletion/export procedures.
- Availability: document free-tier sleep, quotas, cold starts, and backup limits. “Free tier” does not guarantee uninterrupted uptime.

## 12. Acceptance criteria

1. A new website visitor creates a visitor record without automatically becoming an admission applicant.
2. Supplying contact details creates/matches a user record, separately from the application.
3. Staff can create two concurrent interview sessions with different batch codes and fees.
4. Staff can create a monthly GK session and a separate test-series session without overwriting one another.
5. One user can have distinct enrollments in multiple sessions.
6. Receipt upload produces a pending payment review; no automatic approval occurs.
7. Admission staff cannot approve payments unless also assigned the finance permission.
8. An unapproved/rejected payment cannot trigger the invitation release flow.
9. Approval for one session does not unlock a different session's invitation.
10. Staff can see the reviewer and reason for each payment decision.
11. The knowledge base can ingest PDF, DOCX, TXT/Markdown, images, XLSX, and CSV and preserve source metadata.
12. With no documents loaded, the bot clearly states it cannot confirm academy-specific details.
13. Backend secrets and private receipts are not exposed to the browser or public repository.
14. Operational records remain queryable for at least 12 months under the configured retention policy.
15. Tests verify role access, duplicate submissions, cross-session isolation, and approval gating.

## 13. Risks and mitigations

- **Incorrect AI answers:** RAG grounding, source dates, refusal/escalation, staff review of knowledge sources.
- **Fraudulent receipt screenshots:** staff verification against financial records/provider API; AI extraction is advisory only.
- **Wrong batch access:** unique session IDs, payment-to-session linkage, session-specific invitation gating.
- **Personal-data exposure:** role permissions, private object storage, limited retention, access logs.
- **Free-tier limitations:** external persistent database/storage, monitoring, backups, documented migration plan.
- **WhatsApp API limitations:** MVP uses controlled invitation links; verify current official API capabilities before promising community automation.
- **Outdated commission announcements:** separate official-source registry with `published_at`, `checked_at`, `valid_until`, and review state.

## 14. Delivery phases

1. **Foundation:** repository, environment configuration, database, migrations, authentication, roles, audit log.
2. **Catalogue:** courses, sessions, batch codes, fees, schedules, lifecycle management.
3. **Visitor/chatbot:** visitor tracking, conversations, RAG, knowledge ingestion, source citations.
4. **Admissions:** contacts, applications, validation, application status.
5. **Payments:** private receipt storage, AI extraction, finance review queue, approval gates.
6. **Enrollment:** enrollment states and WhatsApp invitation release.
7. **Hardening:** tests, rate limits, backups, retention jobs, logs, privacy/security review.
8. **Deployment:** Vercel frontend, Hugging Face backend, persistent database/storage, domain/CORS/health checks.

## 15. Open operational configuration (not blockers for the specification)

Before production, academy staff must provide the official academy name/branding, approved payment methods and instructions, session fee schedule, source-of-truth rules, privacy contact/notice, staff accounts, WhatsApp invitation management policy, and data retention/deletion policy. Do not hard-code these values in source code.
