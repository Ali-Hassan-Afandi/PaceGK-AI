from typing import Optional
from pydantic import BaseModel


class BatchResponse(BaseModel):
    batch_code: str
    name: str
    poster_url: str
    community_link: Optional[str] = None
