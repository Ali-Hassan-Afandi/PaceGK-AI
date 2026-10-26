from sqlalchemy.orm import Session

from ..models.student import Student


DEFAULT_FULL_FEE = 15000.0
DISCOUNT_RATE = 0.8  # 20% less


def apply_fee(db: Session, student: Student, category: str) -> float:
    full_fee = 0.0
    if student.batch is not None and student.batch.fee_amount is not None:
        full_fee = float(student.batch.fee_amount)
    full_fee = full_fee or DEFAULT_FULL_FEE

    category_u = (category or "NEW").upper()
    if category_u in ("OLD", "ORPHAN", "DISABLED"):
        fee_amount = full_fee * DISCOUNT_RATE
    elif category_u == "NEEDY":
        fee_amount = 0.0
    else:
        fee_amount = full_fee

    student.fee_category = category_u
    student.fee_amount = fee_amount
    db.commit()
    db.refresh(student)
    return fee_amount


def mark_fee_paid(db: Session, student: Student, value: str = "Yes") -> None:
    student.fee_paid = value
    db.commit()
    db.refresh(student)
