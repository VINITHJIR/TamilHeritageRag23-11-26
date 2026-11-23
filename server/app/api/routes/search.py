"""Vector search endpoint using BAAI/bge-m3 dense embeddings and Qdrant."""

import logging
from fastapi import APIRouter, HTTPException
from qdrant_client.http import models
from app.core.config import settings
from app.db.qdrant import get_qdrant_client
from app.schemas.search import SearchRequest, SearchResponse, SearchResultItem
from app.services.embeddings import get_embedding_service

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/search", tags=["Search"])


@router.post("", response_model=SearchResponse)
def vector_search(request: SearchRequest) -> SearchResponse:
    """Perform dense vector search against the heritage_chunks Qdrant collection.

    Embeds the user query using BAAI/bge-m3 (1024-dim normalized vector) and
    filters by optional metadata (category, district, lang, site_id).
    Notice: To protect user privacy in academic research, query text is never logged alongside user identifiers.
    """
    logger.info(f"Executing vector search (top_k={request.top_k}, filters={request.filters})")

    try:
        embedder = get_embedding_service()
        query_vector = embedder.embed_query(request.query)
    except Exception as e:
        logger.error(f"Failed to generate embedding for query: {e}")
        raise HTTPException(status_code=500, detail="Failed to generate query embedding.")

    # Build Qdrant filter conditions
    must_conditions = []
    if request.filters:
        if request.filters.category:
            must_conditions.append(
                models.FieldCondition(
                    key="categories",
                    match=models.MatchValue(value=request.filters.category)
                )
            )
        if request.filters.district:
            must_conditions.append(
                models.FieldCondition(
                    key="district",
                    match=models.MatchValue(value=request.filters.district)
                )
            )
        if request.filters.lang:
            must_conditions.append(
                models.FieldCondition(
                    key="lang",
                    match=models.MatchValue(value=request.filters.lang)
                )
            )
        if request.filters.site_id:
            must_conditions.append(
                models.FieldCondition(
                    key="site_id",
                    match=models.MatchValue(value=request.filters.site_id)
                )
            )

    qdrant_filter = models.Filter(must=must_conditions) if must_conditions else None

    try:
        qdrant = get_qdrant_client()
        search_results = qdrant.search(
            collection_name=settings.QDRANT_COLLECTION,
            query_vector=("dense", query_vector),
            query_filter=qdrant_filter,
            limit=request.top_k,
            with_payload=True,
        )
    except Exception as e:
        logger.error(f"Qdrant search error: {e}")
        raise HTTPException(status_code=503, detail=f"Vector search service error: {str(e)}")

    items = []
    for hit in search_results:
        payload = hit.payload or {}
        items.append(
            SearchResultItem(
                score=round(float(hit.score), 4),
                site_id=payload.get("site_id", ""),
                site_name_en=payload.get("site_name_en", ""),
                site_name_ta=payload.get("site_name_ta"),
                section=payload.get("section", ""),
                lang=payload.get("lang", ""),
                text=payload.get("text", ""),
            )
        )

    return SearchResponse(
        query=request.query,
        results=items,
        total_results=len(items)
    )
