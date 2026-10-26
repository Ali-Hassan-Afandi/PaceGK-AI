from datetime import datetime
from typing import Optional

from sqlalchemy.orm import Session

from ..models.student import Student
from ..services.fee_service import apply_fee, mark_fee_paid


def normalize_phone(phone_number: str) -> str:
    return phone_number.replace("whatsapp:", "").strip().lstrip("+")


def get_student_by_phone(db: Session, phone_number: str) -> Optional[Student]:
    return (
        db.query(Student)
        .filter(Student.phone_number == normalize_phone(phone_number))
        .first()
    )


def approve_student(
    db: Session, student_phone: str, admin_number: str, category: str = "NEW"
) -> Optional[Student]:
    """
    Human-in-the-loop approval.

    Valid transition: FEE_SUBMITTED -> FEE_CATEGORIZED -> APPROVED
    (fee applied + marked paid, awaiting community link / final confirmation).
    """
    student = get_student_by_phone(db, student_phone)
    if student is None or student.status != "FEE_SUBMITTED":
        return None

    # 1. Fee review passed -> categorize fee
    student.status = "FEE_CATEGORIZED"
    db.commit()

    fee_amount = apply_fee(db, student, category)
    mark_fee_paid(db, student, "Yes")

    # 2. Approved -> finalize approval record
    student.status = "APPROVED"
    student.approved_by = admin_number
    student.approved_at = datetime.utcnow()
    db.commit()
    db.refresh(student)
    return student


def reject_student(
    db: Session, student_phone: str, admin_number: str, reason: str = ""
) -> Optional[Student]:
    student = get_student_by_phone(db, student_phone)
    if student is None:
        return None

    student.status = "REJECTED"
    student.approved_by = admin_number
    student.approved_at = datetime.utcnow()
    student.rejection_reason = reason or None
    db.commit()
    db.refresh(student)
    return student
