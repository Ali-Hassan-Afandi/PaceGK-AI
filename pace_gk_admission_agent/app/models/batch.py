from sqlalchemy import Column, Date, Integer, Numeric, String
from sqlalchemy.orm import relationship

from ..database import Base


class Batch(Base):
    __tablename__ = "batches"

    id = Column(Integer, primary_key=True, index=True)
    batch_code = Column(String, unique=True, index=True, nullable=False)
    name = Column(String, nullable=False)
    poster_url = Column(String, nullable=True)
    community_link = Column(String, nullable=True)
    start_date = Column(Date, nullable=True)
    fee_amount = Column(Numeric, nullable=True)
    students = relationship("Student", back_populates="batch")

    def __repr__(self) -> str:
        return f"<Batch(batch_code={self.batch_code})>"
