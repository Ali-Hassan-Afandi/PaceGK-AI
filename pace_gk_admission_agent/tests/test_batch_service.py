"""Unit tests for the batch service (unique batch codes, creation)."""

from sqlalchemy.exc import IntegrityError


def test_create_batch_and_uniqueness(db_session):
    from app.services.batch_service import create_batch

    b1 = create_batch(db_session, batch_code="NOV26-1A", name="Batch 1A")
    b2 = create_batch(db_session, batch_code="NOV26-1B", name="Batch 1B")

    assert b1.batch_code == "NOV26-1A"
    assert b2.batch_code == "NOV26-1B"
    assert b1.id != b2.id

    # Batch codes are unique: inserting a duplicate must fail.
    try:
        create_batch(db_session, batch_code="NOV26-1A", name="Duplicate")
    except IntegrityError:
        db_session.rollback()
    else:
        raise AssertionError("Expected IntegrityError for duplicate batch code")


def test_get_batches_returns_all(db_session):
    from app.services.batch_service import create_batch, get_batches
    from app.schemas.batch import BatchResponse

    create_batch(db_session, batch_code="NOV26-1A", name="Batch 1A")
    create_batch(db_session, batch_code="NOV26-1B", name="Batch 1B")

    results = get_batches(db_session)
    assert len(results) == 2
    assert all(isinstance(r, BatchResponse) for r in results)
    codes = {r.batch_code for r in results}
    assert codes == {"NOV26-1A", "NOV26-1B"}
