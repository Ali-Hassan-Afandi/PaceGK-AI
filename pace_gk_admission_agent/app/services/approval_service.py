"""Secure admin approval and rejection for admissions.

Approval is a human-in-the-loop operation. The APPROVE command represents the
admin's independent verification of the payment. Before approving:

1. The sender must be an authorized admin (enforced by the router).
2. The applicant must exist.
3. The applicant must be in FEE_SUBMITTED.
4. The fee receipt storage reference must exist.
5. The required applicant information must be complete.
6. The admin verifies the payment independently (the APPROVE command itself).
7. The fee is calculated using the fee service (batch fee or default).
8. The administrator identity and timestamp are recorded.
9. The application moves to APPROVED (not CONFIRMED).

CONFIRMED is set separately by the router only after the WhatsApp API accepts
the confirmation/community-link notifications.

A student is NOT marked fee-paid merely because an APPROVE command was received;
the receipt must exist and the admin is the human verifier.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from sqlalchemy.orm import Session

from ..models.student import Student
from ..services.admission_validation import all_text_fields_complete, fee_receipt_stored
from ..services.fee_service import ALLOWED_FEE_CATEGORIES, apply_fee, mark_fee_paid


def normalize_phone(phone_number: str) -> str:
    return phone_number.replace("whatsapp:", "").strip().lstrip("+")


def get_student_by_phone(db: Session, phone_number: str) -> Optional[Student]:
    return (
        db.query(Student)
        .filter(Student.phone_number == normalize_phone(phone_number))
        .first()
    )


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def approve_student(
    db: Session, student_phone: str, admin_number: str, category: str = "NEW"
) -> Optional[Student]:
    """
    Human-in-the-loop approval.

    Valid transition: FEE_SUBMITTED -> APPROVED.

    Preconditions:
      - student exists and is in FEE_SUBMITTED
      - a fee receipt is stored permanently (fee_paid_pic_url)
      - all five mandatory applicant details are complete and valid

    The admin issuing APPROVE is the human verifier of the payment, so the fee
    is marked paid here. The LLM or student never sets the fee amount.
    """
    category_u = (category or "NEW").upper()
    if category_u not in ALLOWED_FEE_CATEGORIES:
        return None

    student = get_student_by_phone(db, student_phone)
    if student is None:
        return None
    if student.status != "FEE_SUBMITTED":
        return None
    if not fee_receipt_stored(student):
        return None
    if not all_text_fields_complete(student):
        return None

    # Apply the fee (batch fee where configured, else default full fee).
    apply_fee(db, student, category_u)
    # Human-verified payment (the admin is the verifier).
    mark_fee_paid(db, student, "Yes")

    # Assign roll number based on registration serial within the batch.
    # Earlier registrants (by submitted_at, then id) get lower roll numbers.
    if student.roll_number is None:
        earlier = (
            db.query(Student)
            .filter(Student.batch_id == student.batch_id)
            .filter(Student.status.in_(["APPROVED", "CONFIRMED"]))
            .filter(Student.roll_number.isnot(None))
            .all()
        )
        # Count those registered before this student (by submitted_at, then id).
        count_before = 0
        for s in earlier:
            s_submitted = s.submitted_at or s.id
            cur_submitted = student.submitted_at or student.id
            if (s_submitted, s.id) < (cur_submitted, student.id):
                count_before += 1
        student.roll_number = count_before + 1

    student.status = "APPROVED"
    student.approved_by = normalize_phone(admin_number)
    student.approved_at = now_utc()
    db.commit()
    db.refresh(student)
    return student


def reject_student(
    db: Session, student_phone: str, admin_number: str, reason: str = ""
) -> Optional[Student]:
    """Reject an application from a review state (FEE_SUBMITTED or APPROVED).

    Records the reason, administrator and timestamp, and notifies the student
    (notification sending is handled by the router).
    """
    student = get_student_by_phone(db, student_phone)
    if student is None:
        return None
    if student.status not in ("FEE_SUBMITTED", "APPROVED"):
        return None

    student.status = "REJECTED"
    student.rejection_reason = (reason or "").strip() or None
    student.approved_by = normalize_phone(admin_number)
    student.approved_at = now_utc()
    db.commit()
    db.refresh(student)
    return student
