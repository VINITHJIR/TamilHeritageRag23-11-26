# ORM model for candidate cache

from sqlalchemy import Column, String, JSON, TIMESTAMP, Integer, func
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from app.db.postgres import Base

class CandidateCache(Base):
    __tablename__ = "candidate_cache"

    candidate_key = Column(String, primary_key=True)
    candidates = Column(JSON, nullable=False)
    fetched_at = Column(TIMESTAMP(timezone=True), server_default=func.now(), nullable=False)
    ttl_days = Column(Integer, nullable=False, default=30)
