import csv
from pathlib import Path
from typing import List, Optional

from sqlalchemy.orm import Session

from ..models.batch import Batch
from ..models.student import Student


STUDENT_EXPORT_COLUMNS = [
    "student_name",
    "father_name",
    "domicile",
    "id_card_number",
    "whatsapp_number",
    "student_pic_url",
    "fee_paid_pic_url",
    "batch_code",
    "batch_name",
    "status",
    "fee_category",
    "fee_amount",
    "fee_paid",
    "submitted_at",
    "approved_by",
    "approved_at",
]


def get_export_dir() -> Path:
    from ..config import settings

    return Path(settings.admission_export_dir)


def get_batch_students_file(batch_code: str) -> Path:
    export_dir = get_export_dir()
    export_dir.mkdir(parents=True, exist_ok=True)
    return export_dir / f"{batch_code}_students.csv"


def export_batch_students(db: Session, batch_id: Optional[int]) -> Optional[Path]:
    """
    Create (or refresh) the admission data sheet for a single batch.

    One file per batch; each file contains all the registered (approved) students
    of that batch. The file is written as CSV (openable in Excel / Google Sheets).
    """
    batch: Optional[Batch] = db.query(Batch).filter(Batch.id == batch_id).first() if batch_id else None
    if batch is None:
        return None

    students: List[Student] = (
        db.query(Student)
        .filter(Student.batch_id == batch.id)
        .filter(Student.status.in_(["APPROVED", "CONFIRMED"]))
        .all()
    )

    file_path: Path = get_batch_students_file(batch.batch_code)
    rows: List[dict] = []
    for student in students:
        rows.append(
            {
                "student_name": student.name or "",
                "father_name": student.father_name or "",
                "domicile": student.domicile or "",
                "id_card_number": student.id_card_number or "",
                "whatsapp_number": student.phone_number or "",
                "student_pic_url": student.student_pic_url or "",
                "fee_paid_pic_url": student.fee_paid_pic_url or "",
                "batch_code": batch.batch_code or "",
                "batch_name": batch.name or "",
                "status": student.status or "",
                "fee_category": student.fee_category or "",
                "fee_amount": float(student.fee_amount) if student.fee_amount is not None else 0.0,
                "fee_paid": student.fee_paid or "",
                "submitted_at": student.submitted_at.isoformat() if student.submitted_at else "",
                "approved_by": student.approved_by or "",
                "approved_at": student.approved_at.isoformat() if student.approved_at else "",
            }
        )

    with open(file_path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=STUDENT_EXPORT_COLUMNS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)

    return file_path
