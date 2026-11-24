# Clarification service utilities

from sqlalchemy.orm import Session
from app.models.clarification import Clarification
from typing import List, Optional, Dict
from datetime import datetime, timedelta

def create_clarification(
    db: Session,
    session_id: str,
    asked_message_id: str,
    entity_name: str,
    entity_name_ta: Optional[str],
    question_type: str,
    original_query: str,
    candidates: List[Dict],
) -> Clarification:
    """Insert a new clarification entry with status 'open'."""
    clar = Clarification(
        session_id=session_id,
        asked_message_id=asked_message_id,
        entity_name=entity_name,
        entity_name_ta=entity_name_ta,
        question_type=question_type,
        original_query=original_query,
        candidates=candidates,
        status="open",
    )
    db.add(clar)
    db.commit()
    db.refresh(clar)
    return clar

def get_open_clarification(db: Session, session_id: str) -> Optional[Clarification]:
    """Return the first open clarification for a session, respecting expiration policy."""
    clar = (
        db.query(Clarification)
        .filter(Clarification.session_id == session_id, Clarification.status == "open")
        .order_by(Clarification.created_at.asc())
        .first()
    )
    if clar:
        # expire after 30 minutes
        if clar.created_at < datetime.utcnow() - timedelta(minutes=30):
            clar.status = "expired"
            db.commit()
            return None
    return clar

def resolve_clarification(
    db: Session,
    clarification_id: str,
    resolved_option: Dict,
) -> Clarification:
    """Mark clarification as resolved and store the chosen option."""
    clar = db.query(Clarification).filter(Clarification.clarification_id == clarification_id).first()
    if not clar:
        raise ValueError("Clarification not found")
    clar.status = "resolved"
    clar.resolved_option = resolved_option
    clar.resolved_at = datetime.utcnow()
    db.commit()
    db.refresh(clar)
    return clar

def cancel_clarification(db: Session, clarification_id: str) -> Clarification:
    """Cancel an open clarification (e.g., user asked unrelated question)."""
    clar = db.query(Clarification).filter(Clarification.clarification_id == clarification_id).first()
    if not clar:
        raise ValueError("Clarification not found")
    clar.status = "cancelled"
    db.commit()
    db.refresh(clar)
    return clar
