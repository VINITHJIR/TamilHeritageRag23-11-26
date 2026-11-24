from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.db.postgres import get_db
from app.services.clarifications import get_open_clarification
from app.augment.clarify import process_clarification_reply
from pydantic import BaseModel
from typing import Optional, Dict, List

router = APIRouter(prefix="/clarifications", tags=["Clarifications"])

class ClarificationReply(BaseModel):
    session_id: str
    # Either an option_id (chip click) or free‑text "text"
    option_id: Optional[str] = None
    text: Optional[str] = None

@router.get("/open", response_model=Dict)
def get_open(session_id: str = Query(..., description="Session identifier"), db: Session = Depends(get_db)):
    clar = get_open_clarification(db, session_id)
    if not clar:
        return {"status": "none"}
    return {
        "status": "open",
        "clarification_id": str(clar.clarification_id),
        "candidates": clar.candidates,
        "question_type": clar.question_type,
    }

@router.post("/respond", response_model=Dict)
def respond(reply: ClarificationReply, db: Session = Depends(get_db)):
    if not reply.option_id and not reply.text:
        raise HTTPException(status_code=400, detail="Either option_id or text must be provided")
    payload: Dict = {}
    if reply.option_id:
        payload["option_id"] = reply.option_id
    if reply.text:
        payload["text"] = reply.text
    result = process_clarification_reply(db, reply.session_id, payload)
    return result
