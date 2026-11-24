"""Admin maintenance endpoints."""

import logging
from pathlib import Path
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import text as sa_text
from sqlalchemy.orm import Session
from app.db.postgres import get_db
from app.services.ingest import ingest_from_file

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/admin", tags=["Admin"])


@router.get("/messages/{message_id}/trace")
def get_message_trace(message_id: int, db: Session = Depends(get_db)):
    """Retrieve execution trace stages for a specific chat message.

    Rule 1.4: Returns all pipeline execution stages recorded in message_traces.
    """
    rows = db.execute(
        sa_text("""
            SELECT trace_id, message_id, stage, started_at, duration_ms, input, output, error
            FROM message_traces
            WHERE message_id = :mid
            ORDER BY started_at ASC, trace_id ASC
        """),
        {"mid": message_id},
    ).mappings().fetchall()

    if not rows:
        msg = db.execute(
            sa_text("SELECT message_id FROM chat_messages WHERE message_id = :mid"),
            {"mid": message_id},
        ).fetchone()
        if not msg:
            raise HTTPException(status_code=404, detail=f"Message {message_id} not found")
        return {"message_id": message_id, "stages": [], "total_duration_ms": 0}

    stages = []
    for r in rows:
        stages.append({
            "trace_id": r["trace_id"],
            "stage": r["stage"],
            "started_at": r["started_at"].isoformat() if r["started_at"] else None,
            "duration_ms": r["duration_ms"],
            "input": r["input"],
            "output": r["output"],
            "error": r["error"],
        })

    total_ms = sum((s["duration_ms"] or 0) for s in stages)
    return {
        "message_id": message_id,
        "stages": stages,
        "total_duration_ms": total_ms,
    }


@router.post("/reindex")
def reindex_chunks():
    """Re-ingest chunks from the seed chunks.json file into Qdrant and chunk_registry.

    Idempotent: updates existing points or adds missing ones without creating duplicates.
    """
    # admin.py is at: server/app/api/routes/admin.py
    # 4 parents up = server/, then db/seed/chunks.json
    chunks_path = Path(__file__).resolve().parent.parent.parent.parent / "db" / "seed" / "chunks.json"
    if not chunks_path.exists():
        # Fallback to _handoff if seed copy is missing
        chunks_path = Path(__file__).resolve().parent.parent.parent.parent.parent / "_handoff" / "chunks.json"

    if not chunks_path.exists():
        raise HTTPException(status_code=404, detail="chunks.json file not found")

    try:
        result = ingest_from_file(chunks_path)
        return {
            "status": "success",
            "message": f"Successfully reindexed {result['ingested']} of {result['total']} chunks into Qdrant & chunk_registry."
        }
    except Exception as e:
        logger.error(f"Reindexing failed: {e}")
        raise HTTPException(status_code=500, detail=f"Reindexing failed: {str(e)}")
