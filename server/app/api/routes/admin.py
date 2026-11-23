"""Admin maintenance endpoints."""

import logging
from pathlib import Path
from fastapi import APIRouter, HTTPException
from app.services.ingest import ingest_from_file

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/admin", tags=["Admin"])


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
