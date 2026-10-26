from typing import List

from sqlalchemy.orm import Session

from ..models.batch import Batch
from ..schemas.batch import BatchResponse


def get_batches(db: Session) -> List[BatchResponse]:
    batches: List[Batch] = db.query(Batch).all()
    return [
        BatchResponse(
            batch_code=b.batch_code,
            name=b.name,
            poster_url=b.poster_url or "",
            community_link=b.community_link,
        )
        for b in batches
    ]


def create_batch(
    db: Session,
    batch_code: str,
    name: str,
    poster_url: str = "",
    community_link: str = "",
):
    batch = Batch(
        batch_code=batch_code,
        name=name,
        poster_url=poster_url,
        community_link=community_link,
    )
    db.add(batch)
    db.commit()
    db.refresh(batch)
    return batch
