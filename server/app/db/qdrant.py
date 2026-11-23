"""Qdrant client and collection management."""

import logging
from typing import Optional
from qdrant_client import QdrantClient
from qdrant_client.http import models
from app.core.config import settings

logger = logging.getLogger(__name__)

# Qdrant client singleton
_qdrant_client: Optional[QdrantClient] = None


def get_qdrant_client() -> QdrantClient:
    """Return a singleton instance of the Qdrant client."""
    global _qdrant_client
    if _qdrant_client is None:
        _qdrant_client = QdrantClient(
            url=settings.QDRANT_URL,
            api_key=settings.QDRANT_API_KEY if settings.QDRANT_API_KEY else None,
            timeout=30.0
        )
    return _qdrant_client


_qdrant_probe_client: Optional[QdrantClient] = None


def get_qdrant_probe_client() -> QdrantClient:
    """Return a Qdrant client with a short (3 s) timeout, used only by health checks.

    The main client uses a 30 s timeout for bulk upserts; reusing it in /api/health
    would make the dashboard hang for 30 s whenever Qdrant is down.
    """
    global _qdrant_probe_client
    if _qdrant_probe_client is None:
        _qdrant_probe_client = QdrantClient(
            url=settings.QDRANT_URL,
            api_key=settings.QDRANT_API_KEY if settings.QDRANT_API_KEY else None,
            timeout=3.0
        )
    return _qdrant_probe_client


def ensure_collection() -> None:
    """Ensure the target Qdrant collection exists with the required vector specs and payload indexes.

    Spec:
      - Collection name: settings.QDRANT_COLLECTION ('heritage_chunks')
      - Named dense vectors: 'dense' (size 1024, COSINE)
      - Empty sparse vector config: 'sparse' (for Level 3 hybrid search)
      - Payload indexes (keyword): site_id, lang, section, categories, district, trust
    """
    client = get_qdrant_client()
    col_name = settings.QDRANT_COLLECTION

    collections_resp = client.get_collections()
    existing = [col.name for col in collections_resp.collections]

    if col_name not in existing:
        logger.info(f"Creating Qdrant collection '{col_name}'...")
        client.create_collection(
            collection_name=col_name,
            vectors_config={
                "dense": models.VectorParams(
                    size=1024,
                    distance=models.Distance.COSINE
                )
            },
            sparse_vectors_config={
                "sparse": models.SparseVectorParams()
            }
        )
        logger.info(f"Collection '{col_name}' created successfully.")
    else:
        logger.info(f"Collection '{col_name}' already exists.")

    # Create keyword payload indexes for filtering
    fields_to_index = ["site_id", "lang", "section", "categories", "district", "trust"]
    for field in fields_to_index:
        try:
            client.create_payload_index(
                collection_name=col_name,
                field_name=field,
                field_schema=models.PayloadSchemaType.KEYWORD
            )
        except Exception as e:
            # Index might already exist
            logger.debug(f"Payload index for '{field}' note: {e}")
