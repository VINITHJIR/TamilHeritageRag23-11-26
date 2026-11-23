"""Chat sessions, conversational messages, citations, and user feedback routes."""

import logging
from typing import List, Optional
import uuid
from fastapi import APIRouter, Depends, Header, HTTPException, Query, status
from sqlalchemy import text as sa_text
from sqlalchemy.orm import Session
from app.db.postgres import get_db
from app.schemas.chat import (
    ChatMessageResponse,
    CitationItem,
    FeedbackRequest,
    FeedbackResponse,
    MessageCreateRequest,
    SessionCreateRequest,
    SessionMessageItem,
    SessionResponse,
    SessionUpdateRequest,
)
from app.services.chat_pipeline import process_chat_message

logger = logging.getLogger(__name__)
router = APIRouter(tags=["Chat"])


# -------------------------------------------------------------------------
# Chat Sessions
# -------------------------------------------------------------------------
@router.get("/chat/sessions", response_model=List[SessionResponse])
def list_sessions(
    user_id: Optional[uuid.UUID] = Query(default=None, description="Filter by user UUID"),
    include_archived: bool = Query(default=True, description="Whether to include archived sessions"),
    db: Session = Depends(get_db),
) -> List[SessionResponse]:
    """List chat sessions, optionally filtered by user_id and archive status."""
    conditions = ["1=1"]
    params = {}

    if user_id is not None:
        conditions.append("user_id = :uid")
        params["uid"] = user_id

    if not include_archived:
        conditions.append("is_archived = FALSE")

    sql = sa_text(f"""
        SELECT session_id, user_id, title, client, started_at, last_message_at, is_archived
        FROM chat_sessions
        WHERE {" AND ".join(conditions)}
        ORDER BY last_message_at DESC
    """)
    rows = db.execute(sql, params).mappings().fetchall()

    return [
        SessionResponse(
            session_id=r["session_id"],
            user_id=r["user_id"],
            title=r["title"],
            client=r["client"],
            started_at=r["started_at"],
            last_message_at=r["last_message_at"],
            is_archived=r["is_archived"],
        )
        for r in rows
    ]


@router.post("/chat/sessions", response_model=SessionResponse, status_code=status.HTTP_201_CREATED)
def create_session(
    request: SessionCreateRequest,
    db: Session = Depends(get_db),
) -> SessionResponse:
    """Create a new chat conversation session for a user."""
    # Verify user exists
    user_row = db.execute(
        sa_text("SELECT user_id FROM users WHERE user_id = :uid"),
        {"uid": request.user_id},
    ).fetchone()
    if not user_row:
        raise HTTPException(status_code=404, detail="User not found.")

    session_id = uuid.uuid4()
    title = request.title or "New Chat"
    client_name = request.client or "web"

    stmt = sa_text("""
        INSERT INTO chat_sessions (session_id, user_id, title, client, started_at, last_message_at, is_archived)
        VALUES (:session_id, :user_id, :title, :client, now(), now(), FALSE)
        RETURNING session_id, user_id, title, client, started_at, last_message_at, is_archived
    """)
    row = db.execute(stmt, {
        "session_id": session_id,
        "user_id": request.user_id,
        "title": title,
        "client": client_name,
    }).mappings().fetchone()
    db.commit()

    return SessionResponse(
        session_id=row["session_id"],
        user_id=row["user_id"],
        title=row["title"],
        client=row["client"],
        started_at=row["started_at"],
        last_message_at=row["last_message_at"],
        is_archived=row["is_archived"],
    )


@router.patch("/chat/sessions/{session_id}", response_model=SessionResponse)
def update_session(
    session_id: uuid.UUID,
    request: SessionUpdateRequest,
    db: Session = Depends(get_db),
) -> SessionResponse:
    """Update session title or archive status."""
    check_row = db.execute(
        sa_text("SELECT session_id FROM chat_sessions WHERE session_id = :sid"),
        {"sid": session_id},
    ).fetchone()
    if not check_row:
        raise HTTPException(status_code=404, detail="Chat session not found.")

    updates = []
    params = {"sid": session_id}

    if request.title is not None:
        updates.append("title = :title")
        params["title"] = request.title

    if request.is_archived is not None:
        updates.append("is_archived = :is_archived")
        params["is_archived"] = request.is_archived

    if updates:
        stmt = sa_text(f"""
            UPDATE chat_sessions
            SET {", ".join(updates)}
            WHERE session_id = :sid
            RETURNING session_id, user_id, title, client, started_at, last_message_at, is_archived
        """)
        row = db.execute(stmt, params).mappings().fetchone()
        db.commit()
    else:
        stmt = sa_text("""
            SELECT session_id, user_id, title, client, started_at, last_message_at, is_archived
            FROM chat_sessions WHERE session_id = :sid
        """)
        row = db.execute(stmt, params).mappings().fetchone()

    return SessionResponse(
        session_id=row["session_id"],
        user_id=row["user_id"],
        title=row["title"],
        client=row["client"],
        started_at=row["started_at"],
        last_message_at=row["last_message_at"],
        is_archived=row["is_archived"],
    )


# -------------------------------------------------------------------------
# Session Messages
# -------------------------------------------------------------------------
@router.get("/chat/sessions/{session_id}/messages", response_model=List[SessionMessageItem])
def get_session_messages(
    session_id: uuid.UUID,
    db: Session = Depends(get_db),
) -> List[SessionMessageItem]:
    """Retrieve full message history for a chat session including citations and metadata."""
    session_check = db.execute(
        sa_text("SELECT session_id FROM chat_sessions WHERE session_id = :sid"),
        {"sid": session_id},
    ).fetchone()
    if not session_check:
        raise HTTPException(status_code=404, detail="Chat session not found.")

    # Fetch messages in order
    msg_sql = sa_text("""
        SELECT m.message_id, m.session_id, m.role, m.content, m.lang,
               m.question_type, m.resolved_site, m.reply_to, m.created_at,
               ql.path, ql.top_score, ql.latency_ms
        FROM chat_messages m
        LEFT JOIN query_logs ql ON (
            (m.role = 'user' AND ql.message_id = m.message_id)
            OR (m.role = 'assistant' AND ql.message_id = m.reply_to)
        )
        WHERE m.session_id = :sid
        ORDER BY m.created_at ASC, m.message_id ASC
    """)
    msg_rows = db.execute(msg_sql, {"sid": session_id}).mappings().fetchall()

    if not msg_rows:
        return []

    # Preload citations for assistant messages in this session
    assistant_msg_ids = [r["message_id"] for r in msg_rows if r["role"] == "assistant"]
    citations_by_msg = {mid: [] for mid in assistant_msg_ids}

    if assistant_msg_ids:
        cit_sql = sa_text("""
            SELECT mc.message_id, mc.chunk_id, mc.rank, mc.score,
                   s.site_id, s.name_en AS site_name_en, s.name_ta AS site_name_ta,
                   cr.section, cr.lang, cr.origin, s.trust,
                   src.url AS source_url
            FROM message_citations mc
            JOIN chunk_registry cr ON mc.chunk_id = cr.chunk_id
            JOIN sites s ON cr.site_id = s.site_id
            LEFT JOIN sources src ON cr.source_id = src.source_id
            WHERE mc.message_id = ANY(:mids)
            ORDER BY mc.message_id, mc.rank ASC
        """)
        cit_rows = db.execute(cit_sql, {"mids": assistant_msg_ids}).mappings().fetchall()
        for cr in cit_rows:
            mid = cr["message_id"]
            citations_by_msg[mid].append(
                CitationItem(
                    chunk_id=str(cr["chunk_id"]),
                    site_id=cr["site_id"],
                    site_name_en=cr["site_name_en"],
                    site_name_ta=cr["site_name_ta"],
                    section=cr["section"],
                    lang=cr["lang"],
                    score=float(cr["score"]) if cr["score"] is not None else 0.0,
                    trust=str(cr["trust"]),
                    origin=str(cr["origin"]),
                    source_url=cr["source_url"],
                )
            )

    result = []
    for r in msg_rows:
        mid = r["message_id"]
        cits = citations_by_msg.get(mid, [])
        path = r["path"]
        if r["role"] == "assistant" and not path:
            path = "db" if cits else "rejected"

        result.append(
            SessionMessageItem(
                message_id=mid,
                session_id=r["session_id"],
                role=str(r["role"]),
                content=r["content"],
                lang=r["lang"],
                question_type=r["question_type"],
                resolved_site=r["resolved_site"],
                reply_to=r["reply_to"],
                created_at=r["created_at"],
                path=path,
                top_score=float(r["top_score"]) if r["top_score"] is not None else None,
                latency_ms=r["latency_ms"],
                citations=cits,
            )
        )

    return result


@router.post("/chat/sessions/{session_id}/messages", response_model=ChatMessageResponse)
def post_chat_message(
    session_id: uuid.UUID,
    request: MessageCreateRequest,
    idempotency_key: Optional[str] = Header(None, alias="Idempotency-Key"),
    db: Session = Depends(get_db),
) -> ChatMessageResponse:
    """Send a question to the conversational assistant for a session.

    Runs language detection, question classification, entity linking, vector retrieval,
    extractive answer formulation, and persistence.
    Supports request idempotency via request body idempotency_key or Idempotency-Key header.
    """
    key = request.idempotency_key or idempotency_key
    try:
        return process_chat_message(db, session_id, request.content, idempotency_key=key)
    except ValueError as ve:
        raise HTTPException(status_code=404, detail=str(ve))
    except Exception as e:
        logger.error(f"Error executing chat pipeline: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to process chat message.")


# -------------------------------------------------------------------------
# User Feedback
# -------------------------------------------------------------------------
@router.post("/messages/{message_id}/feedback", response_model=FeedbackResponse)
def submit_feedback(
    message_id: int,
    request: FeedbackRequest,
    db: Session = Depends(get_db),
) -> FeedbackResponse:
    """Submit rating and feedback for an assistant message (upsert)."""
    # Verify message exists
    msg = db.execute(
        sa_text("SELECT message_id FROM chat_messages WHERE message_id = :mid"),
        {"mid": message_id},
    ).fetchone()
    if not msg:
        raise HTTPException(status_code=404, detail="Message not found.")

    if request.rating not in (-1, 1):
        raise HTTPException(status_code=400, detail="Rating must be 1 or -1.")

    valid_reasons = {"wrong_fact", "wrong_place", "wrong_language", "incomplete", "other"}
    if request.reason and request.reason not in valid_reasons:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid reason. Must be one of: {', '.join(sorted(valid_reasons))}",
        )

    stmt = sa_text("""
        INSERT INTO message_feedback (message_id, user_id, rating, reason, comment, created_at)
        VALUES (:mid, :uid, :rating, :reason, :comment, now())
        ON CONFLICT (message_id, user_id)
        DO UPDATE SET
            rating = EXCLUDED.rating,
            reason = EXCLUDED.reason,
            comment = EXCLUDED.comment,
            created_at = now()
        RETURNING message_id, user_id, rating, reason, comment
    """)

    row = db.execute(stmt, {
        "mid": message_id,
        "uid": request.user_id,
        "rating": request.rating,
        "reason": request.reason,
        "comment": request.comment,
    }).mappings().fetchone()
    db.commit()

    return FeedbackResponse(
        status="ok",
        message_id=row["message_id"],
        user_id=row["user_id"],
        rating=row["rating"],
        reason=row["reason"],
        comment=row["comment"],
    )
