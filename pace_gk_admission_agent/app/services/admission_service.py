from sqlalchemy.orm import Session

from ..models.student import Student
from ..schemas.student import StudentCreate


def get_or_create_student(db: Session, phone_number: str) -> Student:
    normalized_phone = phone_number.replace("whatsapp:", "").strip().lstrip("+")
    student = (
        db.query(Student)
        .filter(Student.phone_number == normalized_phone)
        .first()
    )
    if student is None:
        student = Student(phone_number=normalized_phone, status="INQUIRY")
        db.add(student)
        db.commit()
        db.refresh(student)
    return student


def create_student(db: Session, data: StudentCreate) -> Student:
    student = Student(**data.model_dump())
    db.add(student)
    db.commit()
    db.refresh(student)
    return student
