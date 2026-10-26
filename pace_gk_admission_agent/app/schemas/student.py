from typing import Optional
from pydantic import BaseModel


class StudentCreate(BaseModel):
    phone_number: str
    name: Optional[str] = None
    father_name: Optional[str] = None
    domicile: Optional[str] = None
    id_card_number: Optional[str] = None
    student_pic_url: Optional[str] = None
    fee_paid_pic_url: Optional[str] = None
    batch_id: Optional[int] = None
