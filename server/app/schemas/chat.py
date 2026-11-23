"""Pydantic schemas for users, chat sessions, messages, citations, and feedback."""

from datetime import datetime
from typing import List, Literal, Optional
from uuid import UUID
from pydantic import BaseModel, Field


# -------------------------------------------------------------------------
# Users
# -------------------------------------------------------------------------
class GuestUserResponse(BaseModel):
    user_id: UUID
    auth_provider: str = "guest"
    created_at: Optional[datetime] = None


# -------------------------------------------------------------------------
# Sessions
# -------------------------------------------------------------------------
class SessionCreateRequest(BaseModel):
    user_id: UUID
    title: Optional[str] = None
    client: Optional[str] = "web"


class SessionUpdateRequest(BaseModel):
    title: Optional[str] = None
    is_archived: Optional[bool] = None


class SessionResponse(BaseModel):
    session_id: UUID
    user_id: UUID
    title: Optional[str] = None
    client: str = "web"
    started_at: datetime
    last_message_at: datetime
    is_archived: bool = False


# -------------------------------------------------------------------------
# Messages & Citations
# -------------------------------------------------------------------------
class CitationItem(BaseModel):
    chunk_id: str
    site_id: str
    site_name_en: str
    site_name_ta: Optional[str] = None
    section: str
    lang: str
    score: float
    trust: str
    origin: str
    source_url: Optional[str] = None


class MessageCreateRequest(BaseModel):
    content: str = Field(..., min_length=1, max_length=2000, description="User message text")
    idempotency_key: Optional[str] = Field(default=None, description="Optional client-generated unique idempotency token")


class UserMessagePayload(BaseModel):
    message_id: int
    session_id: UUID
    role: Literal["user"] = "user"
    content: str
    lang: Optional[str] = None
    question_type: Optional[str] = None
    resolved_site: Optional[str] = None
    created_at: datetime


class AssistantMessagePayload(BaseModel):
    message_id: int
    session_id: Optional[UUID] = None
    role: Literal["assistant"] = "assistant"
    content: str
    lang: Optional[str] = None
    path: str = Field(
        default="db",
        description="'db' | 'rejected' | 'web_pending' | 'web_consensus'"
    )
    top_score: Optional[float] = None
    latency_ms: Optional[int] = None
    question_type: Optional[str] = None
    resolved_site: Optional[str] = None
    reply_to: Optional[int] = None
    created_at: Optional[datetime] = None
    citations: List[CitationItem] = Field(default_factory=list)


class ChatMessageResponse(BaseModel):
    user_message: UserMessagePayload
    assistant_message: AssistantMessagePayload


class SessionMessageItem(BaseModel):
    message_id: int
    session_id: UUID
    role: str
    content: str
    lang: Optional[str] = None
    question_type: Optional[str] = None
    resolved_site: Optional[str] = None
    reply_to: Optional[int] = None
    created_at: datetime
    path: Optional[str] = None
    top_score: Optional[float] = None
    latency_ms: Optional[int] = None
    citations: List[CitationItem] = Field(default_factory=list)


# -------------------------------------------------------------------------
# Feedback
# -------------------------------------------------------------------------
class FeedbackRequest(BaseModel):
    user_id: UUID
    rating: int = Field(..., description="Rating score: 1 (positive) or -1 (negative)")
    reason: Optional[str] = Field(
        default=None,
        description="Reason: wrong_fact | wrong_place | wrong_language | incomplete | other"
    )
    comment: Optional[str] = None


class FeedbackResponse(BaseModel):
    status: str = "ok"
    message_id: int
    user_id: UUID
    rating: int
    reason: Optional[str] = None
    comment: Optional[str] = None
