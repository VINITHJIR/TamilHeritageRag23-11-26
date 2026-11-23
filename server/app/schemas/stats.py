"""Pydantic schemas for health and system statistics."""

from typing import Dict
from pydantic import BaseModel


class HealthResponse(BaseModel):
    """Response schema for /api/health."""
    postgres: str
    qdrant: str
    embed_model_loaded: bool


class StatsResponse(BaseModel):
    """Response schema for /api/stats."""
    sites: int
    chunks_registry: int
    qdrant_points: int
    users: int
    chat_messages: int
    chunks_per_lang: Dict[str, int]
    sites_per_primary_category: Dict[str, int]
