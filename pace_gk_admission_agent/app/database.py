from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import declarative_base, sessionmaker

from .config import settings

_engine_kwargs = {"echo": False}
if settings.database_url.startswith("sqlite"):
    _engine_kwargs["connect_args"] = {"check_same_thread": False}

engine = create_engine(settings.database_url, **_engine_kwargs)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
Base = declarative_base()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def _column_exists(db, table_name: str, column_name: str) -> bool:
    """Check whether a column exists on a table (SQLite-aware, repeatable)."""
    inspector = inspect(db.bind)
    try:
        columns = {col["name"] for col in inspector.get_columns(table_name)}
    except Exception:
        return False
    return column_name in columns


def ensure_schema(db=None) -> None:
    """Apply repeatable, safe schema updates without dropping tables or data.

    * Creates any tables that do not yet exist (e.g. processed_webhook_messages).
    * Adds new columns to existing tables only when they are missing
      (confirmation_sent_at, community_link_sent_at on students).
    * Never rewrites existing rows or statuses.

    Safe to run on every startup (lifespan) and is idempotent.
    """
    close_db = False
    if db is None:
        db = SessionLocal()
        close_db = True
    try:
        # Import models lazily so their tables are registered with Base.metadata
        # before create_all (avoids a circular import: models import this module).
        from .models import batch, knowledge_embedding, student, webhook  # noqa: F401

        # Create missing tables (no-op for existing tables).
        Base.metadata.create_all(bind=engine)

        # Add notification-tracking columns to students if missing.
        if _column_exists(db, "students", "id"):
            if not _column_exists(db, "students", "confirmation_sent_at"):
                db.execute(
                    text(
                        "ALTER TABLE students ADD COLUMN confirmation_sent_at DATETIME"
                    )
                )
                db.commit()
            if not _column_exists(db, "students", "community_link_sent_at"):
                db.execute(
                    text(
                        "ALTER TABLE students ADD COLUMN community_link_sent_at DATETIME"
                    )
                )
                db.commit()
            if not _column_exists(db, "students", "roll_number"):
                db.execute(
                    text("ALTER TABLE students ADD COLUMN roll_number INTEGER")
                )
                db.commit()
    finally:
        if close_db:
            db.close()
