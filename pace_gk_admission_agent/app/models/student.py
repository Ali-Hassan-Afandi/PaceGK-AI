from sqlalchemy import Column, DateTime, ForeignKey, Integer, Numeric, String
from sqlalchemy.orm import relationship

from ..database import Base
from .batch import Batch


class Student(Base):
    __tablename__ = "students"

    id = Column(Integer, primary_key=True, index=True)
    phone_number = Column(String, unique=True, index=True, nullable=False)
    name = Column(String, nullable=True)
    father_name = Column(String, nullable=True)
    domicile = Column(String, nullable=True)
    id_card_number = Column(String, nullable=True)
    student_pic_url = Column(String, nullable=True)
    fee_paid_pic_url = Column(String, nullable=True)
    batch_id = Column(Integer, ForeignKey("batches.id"), nullable=True)
    status = Column(String, nullable=False, default="INQUIRY")
    fee_category = Column(String, nullable=True)
    fee_amount = Column(Numeric, nullable=True)
    fee_paid = Column(String, nullable=True)
    submitted_at = Column(DateTime, nullable=True)
    approved_by = Column(String, nullable=True)
    approved_at = Column(DateTime, nullable=True)
    rejection_reason = Column(String, nullable=True)
    # Roll number assigned on approval based on registration serial (order of
    # registration within the batch). Starts at 1 for the earliest registrant.
    roll_number = Column(Integer, nullable=True)
    # Notification tracking: records whether the WhatsApp API accepted each
    # outbound notification. CONFIRMED is only set once these are accepted.
    confirmation_sent_at = Column(DateTime, nullable=True)
    community_link_sent_at = Column(DateTime, nullable=True)
    batch = relationship("Batch", back_populates="students")

    def __repr__(self) -> str:
        return f"<Student(phone_number={self.phone_number})>"
