"""Health check endpoint.

Design rules (Level 1B, Phase 0):
  * Must never load the embedding model (bge-m3 is ~2.3 GB and takes tens of
    seconds to load); it only *reports* whether the model is already in memory.
  * Must never raise: every dependency check is isolated, so the endpoint always
    returns HTTP 200 with a per-component status the dashboard can render.
  * Must be fast: Qdrant is probed with a 3 s timeout client.
"""

import logging
from fastapi import APIRouter, Depends
from sqlalchemy import text as sa_text
from sqlalchemy.orm import Session
from app.db.postgres import get_db
from app.db.qdrant import get_qdrant_probe_client
from app.services.embeddings import is_embedding_model_loaded
from app.schemas.stats import HealthResponse

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/health", tags=["Health"])


@router.get("", response_model=HealthResponse)
def check_health(db: Session = Depends(get_db)) -> HealthResponse:
    """Return health status of PostgreSQL, Qdrant, and embedding model."""
    pg_status = "error"
    try:
        db.execute(sa_text("SELECT 1"))
        pg_status = "ok"
    except Exception as exc:  # report, never raise
        logger.warning("Health: PostgreSQL check failed: %s", type(exc).__name__)

    qdrant_status = "error"
    try:
        get_qdrant_probe_client().get_collections()
        qdrant_status = "ok"
    except Exception as exc:
        logger.warning("Health: Qdrant check failed: %s", type(exc).__name__)

    try:
        embed_loaded = is_embedding_model_loaded()
    except Exception:
        embed_loaded = False

    return HealthResponse(
        postgres=pg_status,
        qdrant=qdrant_status,
        embed_model_loaded=embed_loaded,
    )
