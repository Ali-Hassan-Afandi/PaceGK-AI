# PLAN.md — PACE GK Academy Admission Agent & Real-Time Chatbot

> **Type:** Implementation Plan (Build phase)
> **Created:** 2026-10-25
> **Status:** Draft — ready for build execution after review

---

## 1. Objective

Build a **real-time chatbot + admission agent** for **PACE GK Academy** that handles the complete WhatsApp-based admission funnel:

1. Student asks for admission → chatbot responds.
2. Chatbot asks for the **specific admission batch** (sourced from `AdmissionDocs` posters).
3. Chatbot drives the **admission form**:
   - Name
   - Father name
   - Domicile
   - ID card number
   - WhatsApp number
   - Student picture
   - Fee paid picture
4. Data saved into database → specific datasheet after fee submission.
5. **Human-in-the-loop:** human approves fee details.
6. After approval, fee categorized:
   - New student → full fee
   - Old student → 20% less
   - Orphan / disabled → 20% less
   - Needy → free of cost after academy verification
7. **Fee paid** column updated in DB datasheet.
8. On approval → chatbot sends **WhatsApp community link** to student (only paid / approved students, via specific numbers).
9. After successful session registration → student gets **confirmation message** on WhatsApp.

---

## 2. Inputs / Source Materials Available

- **Project root:** `E:\Academy\Academy_Admission_Agent_Specifications`
- `PRD.md` — product requirements.
- `Spec.md` — specifications.
- `DataDocs\` — 16 `.docx` academy knowledge-base files (about, FAQ, policies, subjects, testimonials, etc.).
- `AdmissionDocs\` — admission posters:
  - `NOV26-1A.jpeg`
  - `NOV26-1B.jpeg`
  - `NOV26-1C.jpeg`
- **Community link:** will be added inside `AdmissionDocs\` folder along with the pictures/poster for the admission session in specific batch. Currently, **one community link is being added for checking the system**.

---

## 3. Updated Requirement — Community Link Handling

- The **community link** must be placed inside the `AdmissionDocs` folder alongside the admission posters (e.g., `NOV26-1A.jpeg`, `NOV26-1B.jpeg`, `NOV26-1C.jpeg` and the community link file/reference).
- Currently, **one community link** is being added for **system checking**.
- Post-approval, the bot sends this community link to the student.

---

## 4. End-to-End Admission Workflow

### 4.1 Student Journey

| Step | Trigger | Action | Result |
|---|---|---|---|
| 1 | Admission query | Bot responds | → Batch selection |
| 2 | Admission intent | Bot asks batch | Student picks batch |
| 3 | Batch chosen | Bot sends / asks admission form | Student fills form |
| 4 | Form + fee submitted | Save to DB datasheet | → Human approval |
| 5 | Fee submitted | Human reviews | Approve / Reject |
| 6 | Human approves | Apply fee category | → Approval |
| 7 | Fee paid update | Mark `Fee Paid` | → Community link |
| 8 | Approved / Paid | Send WhatsApp community link | → Confirmation |
| 9 | Session registered | Send confirmation on WhatsApp | Done |

### 4.2 State Machine

```
INQUIRY
   → (admission intent) → BATCH_SELECTION
        → (batch chosen) → FORM_FILL
             → (form + fee submitted) → FEE_SUBMITTED
                  → (human approves) → FEE_CATEGORIZED
                       → (approved) → APPROVED
                            → (community link sent) → CONFIRMED
```

| State | Bot Behaviour | Exit |
|---|---|---|
| `INQUIRY` | Answer admission queries | → `BATCH_SELECTION` |
| `BATCH_SELECTION` | Ask batch from AdmissionAgent data | → `FORM_FILL` |
| `FORM_FILL` | Collect form fields | → `FEE_SUBMITTED` |
| `FEE_SUBMITTED` | Save to DB datasheet | → `HUMAN_APPROVAL` |
| `HUMAN_APPROVAL` | Route to admin numbers 03367483185 / 03463156495 | → `FEE_CATEGORIZED` / `REJECTED` |
| `FEE_CATEGORIZED` | Apply fee tier | → `APPROVED` |
| `APPROVED` | Send WhatsApp community link | → `CONFIRMED` |
| `CONFIRMED` | Send confirmation message | → `INQUIRY` |

---

## 5. Fee Logic

| Category | Fee |
|---|---|
| New student | Full fee |
| Old student | 20% less |
| Orphan / Disabled | 20% less |
| Needy | Free of cost (after academy verification) |

---

## 6. Human-in-the-Loop (IHL)

- Fee approval mandatory before proceeding.
- Admins: **03367483185** and **03463156495**.
- Bot never auto-approves; waits for explicit human decision.

---

## 7. Tech Stack (Proposed)

- **WhatsApp:** Meta WhatsApp Cloud API (production) / Twilio (MVP) / WATI / whatsapp-web.js.
- **LLM:** GPT-4o-mini (+ function calling / structured outputs); fallback: Claude / Gemini.
- **Backend:** FastAPI (Python) or Express (Node).
- **DB:** Supabase (PostgreSQL) / Google Sheets / Airtable / Firebase.
- **Storage:** S3 / Cloudinary / local (student pic + fee-paid pic).
- **Hosting:** Render / Railway / Fly / Vercel.

---

## 8. Database / Datasheet Design

**Students sheet columns:**

`Student Name, Father Name, Domicile, ID Card Number, WhatsApp Number, Student Pic URL, Fee Paid Pic URL, Batch, Status, Fee Category, Fee Amount, Fee Paid (Yes/No), Submitted At, Approved By, Approved At`

---

## 9. Cost Estimate (MVP)

| Component | Approx. Cost |
|---|---|
| WhatsApp BSP / number | $0–20/mo |
| LLM | ~$5–30/mo |
| DB + Storage + Hosting | $0–20/mo |
| **Total** | **~$20–70/month** |

---

## 10. Milestones — One Mile at a Time

- **M1:** WhatsApp bot live → admission queries + batch info from posters.
- **M2:** Batch selection + admission form capture → DB.
- **M3:** Fee submission + human approval (admin numbers).
- **M4:** Fee categorization + community link + confirmation.
- **M5:** Test series agent + interview agent (later).

---

## 11. Open Questions / Risks

- Exact fee amounts per batch.
- Batch names / IDs.
- Community link content/location.
- Old-student eligibility data.
- Needy verification process.
- Admin approval UX (WhatsApp commands).
- Language support (English / Urdu).
- Consent, opt-out, rate limits, idempotency.

---

*End of PLAN.md*
