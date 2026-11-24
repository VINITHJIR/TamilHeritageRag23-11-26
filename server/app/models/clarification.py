# SQLAlchemy model for clarification flow

from sqlalchemy import Column, String, Text, JSON, TIMESTAMP, Enum, func
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from app.db.postgres import Base

class Clarification(Base):
    __tablename__ = "clarifications"

    clarification_id = Column(PG_UUID(as_uuid=True), primary_key=True, server_default=func.gen_random_uuid())
    session_id = Column(PG_UUID(as_uuid=True), nullable=False)
    asked_message_id = Column(String, nullable=False)  # using string to be safe across DBs
    entity_name = Column(Text, nullable=False)
    entity_name_ta = Column(Text)
    question_type = Column(Text, nullable=False)
    original_query = Column(Text, nullable=False)
    candidates = Column(JSON, nullable=False)
    status = Column(
        Enum("open", "resolved", "cancelled", "expired", name="clarification_status"),
        nullable=False,
        default="open",
    )
    resolved_option = Column(JSON)
    created_at = Column(TIMESTAMP(timezone=True), server_default=func.now(), nullable=False)
    resolved_at = Column(TIMESTAMP(timezone=True), onupdate=func.now())
