# SQLAlchemy model for clarification flow

from sqlalchemy import BigInteger, Column, String, Text, TIMESTAMP, func
from sqlalchemy.dialects.postgresql import UUID as PG_UUID, JSONB
from app.db.postgres import Base

class Clarification(Base):
    __tablename__ = "clarifications"

    clarification_id = Column(PG_UUID(as_uuid=True), primary_key=True, server_default=func.gen_random_uuid())
    session_id = Column(PG_UUID(as_uuid=True), nullable=False)
    asked_message_id = Column(BigInteger, nullable=False)
    entity_name = Column(Text, nullable=False)
    entity_name_ta = Column(Text)
    question_type = Column(Text, nullable=False)
    original_query = Column(Text, nullable=False)
    candidates = Column(JSONB, nullable=False)
    status = Column(Text, nullable=False, default="open")
    resolved_option = Column(JSONB)
    created_at = Column(TIMESTAMP(timezone=True), server_default=func.now(), nullable=False)
    resolved_at = Column(TIMESTAMP(timezone=True), onupdate=func.now())
