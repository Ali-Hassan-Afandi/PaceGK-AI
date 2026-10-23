# PACE GK Academy Admission Agent

## Project objective
Build a website chatbot and admission-management system for PACE GK Academy. Follow PRD.md and Spec.md as the primary project requirements.

## Before implementation
- Read PRD.md and Spec.md before starting a milestone.
- Inspect existing files before editing them.
- Implement one approved milestone at a time.
- Explain proposed changes and wait for approval before modifying files.
- Do not start deployment before the application has been tested.

## Architecture
- Frontend: Vercel-compatible web application.
- Backend: Hugging Face-compatible API deployment.
- Database: persistent managed database.
- File storage: private object storage for payment receipts and other protected uploads.
- The application must not depend on the developer's computer remaining online.

## Data separation
Keep visitor/chat records separate from admission applications, course sessions, payments, and enrollments. Support multiple applications or enrollments for a person where permitted by the specifications.

## Payment safeguards
- An uploaded payment receipt is not proof that funds were received.
- AI may extract receipt information but must not independently approve payments.
- Finance staff or a verified payment-provider event must confirm payment.
- Record payment status changes and the responsible actor.

## WhatsApp safeguards
- Release only the invitation link associated with the approved application and correct session.
- Do not claim that the system automatically adds a person to a WhatsApp community unless that capability is implemented and verified.
- Never expose private invitation links to unauthorized users.

## Security and privacy
- Never commit API keys, passwords, tokens, private receipts, or real student records.
- Keep secrets in deployment environment settings, not source code.
- Validate uploads and restrict access to private files.
- Enforce authentication and role-based authorization on protected operations.
- Minimize personal-data collection and retention.
- Use synthetic test data only.

## Academy knowledge
- Do not invent course details, fees, schedules, admission rules, contacts, or official examination information.
- Use verified academy sources for academy-specific answers.
- Keep official public commission information distinguishable from academy-provided material.
- Escalate unanswered or conflicting questions instead of fabricating answers.

## Engineering standards
- Use modular, readable code and documented configuration.
- Add automated tests for important workflows and security controls.
- Run relevant tests after each approved milestone.
- Document environment variables in .env.example using placeholders only.
- Keep generated files and secrets out of Git as appropriate.
- Review git diff before committing.
- Report actual test results and blockers honestly.

## Workflow restrictions
- Never silently proceed to another milestone.
- Never overwrite files without inspecting them first.
- Never install packages, call external APIs, or deploy without approval.
- Never use real payment transactions or live student data for testing.
