"""Pydantic schemas for vector search requests and responses."""

from typing import List, Optional
from pydantic import BaseModel, Field


class SearchFilters(BaseModel):
    """Optional metadata filters for vector search."""
    category: Optional[str] = None
    district: Optional[str] = None
    lang: Optional[str] = None
    site_id: Optional[str] = None


class SearchRequest(BaseModel):
    """Request payload for /api/search."""
    query: str = Field(..., min_length=1, description="Natural language search query")
    top_k: int = Field(default=5, ge=1, le=50, description="Number of results to retrieve")
    filters: Optional[SearchFilters] = None


class SearchResultItem(BaseModel):
    """A retrieved chunk from Qdrant vector search."""
    score: float
    site_id: str
    site_name_en: str
    site_name_ta: Optional[str] = None
    section: str
    lang: str
    text: str


class SearchResponse(BaseModel):
    """Response containing retrieved search results."""
    query: str
    results: List[SearchResultItem]
    total_results: int
