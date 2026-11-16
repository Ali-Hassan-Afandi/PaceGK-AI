"""Durable webhook idempotency model.

Tracks processed inbound WhatsApp message IDs in the database so duplicate
webhook deliveries (e.g. Meta redelivery or Railway restarts) cannot create
duplicate applicants, duplicate receipts, or duplicate approval actions.

This survives process restarts and multiple application instances, unlike an
in-memory set.
"""

from sqlalchemy import Column, DateTime, Integer, String, Text
from sqlalchemy.sql import func

from ..database import Base


class ProcessedWebhookMessage(Base):
    __tablename__ = "processed_webhook_messages"

    id = Column(Integer, primary_key=True, index=True)
    message_id = Column(String, unique=True, index=True, nullable=False)
    phone_number = Column(String, nullable=True)
    message_type = Column(String, nullable=True)  # "text", "image", ...
    processed_at = Column(DateTime, server_default=func.now(), nullable=True)
    raw_payload = Column(Text, nullable=True)  # trimmed/safe summary only; no secrets

    def __repr__(self) -> str:
        return f"<ProcessedWebhookMessage(message_id={self.message_id})>"
