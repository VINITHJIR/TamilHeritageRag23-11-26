"""Pydantic schemas for site data models."""

from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field


class SiteSummary(BaseModel):
    """Compact summary of a heritage site for list views."""
    site_id: str
    name_en: str
    name_ta: Optional[str] = None
    primary_category: str
    categories: List[str] = Field(default_factory=list)
    district_name: Optional[str] = None
    status: str
    trust: str
    lat: Optional[float] = None
    lon: Optional[float] = None


class SitesListResponse(BaseModel):
    """Paginated list of heritage sites."""
    total: int
    page: int
    page_size: int
    sites: List[SiteSummary]


class SiteAliasItem(BaseModel):
    alias: str
    lang: str


class SiteFactItem(BaseModel):
    field: str
    value: str
    lang: str
    score: Optional[float] = None
    status: str
    source_url: Optional[str] = None


class SiteSourceItem(BaseModel):
    source_id: int
    url: str
    domain: str
    tier: str


class SiteChunkItem(BaseModel):
    chunk_id: str
    section: str
    lang: str
    text: str
    source_id: Optional[int] = None
    token_count: Optional[int] = None


class SiteDetail(BaseModel):
    """Detailed site representation including locations, aliases, facts, sources, and chunks."""
    site_id: str
    parent_site_id: Optional[str] = None
    name_en: str
    name_ta: Optional[str] = None
    primary_category: str
    categories: List[str] = Field(default_factory=list)
    district_id: int
    district_name: Optional[str] = None
    subdistrict_id: Optional[int] = None
    subdistrict_name: Optional[str] = None
    local_body_id: Optional[int] = None
    local_body_name: Optional[str] = None
    local_body_type: Optional[str] = None
    lat: Optional[float] = None
    lon: Optional[float] = None
    period: Optional[str] = None
    dynasty: Optional[str] = None
    status: str
    trust: str
    origin: str
    aliases: List[SiteAliasItem] = Field(default_factory=list)
    facts: List[SiteFactItem] = Field(default_factory=list)
    sources: List[SiteSourceItem] = Field(default_factory=list)
    chunks: List[SiteChunkItem] = Field(default_factory=list)
