# SQLAlchemy model for option_choices (Rule 4.2: user pick for value choice)

from sqlalchemy import BigInteger, Column, String, Text, TIMESTAMP, func
from sqlalchemy.dialects.postgresql import UUID as PG_UUID, JSONB
from app.db.postgres import Base


class OptionChoice(Base):
    __tablename__ = "option_choices"

    choice_id = Column(PG_UUID(as_uuid=True), primary_key=True, server_default=func.gen_random_uuid())
    message_id = Column(BigInteger, nullable=True)
    user_id = Column(PG_UUID(as_uuid=True), nullable=True)
    field = Column(Text, nullable=False)
    chosen_value = Column(Text, nullable=False)
    options = Column(JSONB, nullable=False, server_default="[]")
    created_at = Column(TIMESTAMP(timezone=True), server_default=func.now(), nullable=False)
