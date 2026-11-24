"""SQLAlchemy model for message_traces table."""

from sqlalchemy import BigInteger, Column, Integer, Text, TIMESTAMP, func
from sqlalchemy.dialects.postgresql import JSONB
from app.db.postgres import Base


class MessageTrace(Base):
    __tablename__ = "message_traces"

    trace_id = Column(BigInteger, primary_key=True, autoincrement=True)
    message_id = Column(BigInteger, nullable=False, index=True)
    stage = Column(Text, nullable=False, index=True)
    started_at = Column(TIMESTAMP(timezone=True), server_default=func.now(), nullable=False)
    duration_ms = Column(Integer)
    input = Column(JSONB)
    output = Column(JSONB)
    error = Column(Text)
