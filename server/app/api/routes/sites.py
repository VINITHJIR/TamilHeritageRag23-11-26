"""Site exploration and statistics endpoints."""

import logging
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import text as sa_text
from sqlalchemy.orm import Session
from app.core.config import settings
from app.db.postgres import get_db
from app.db.qdrant import get_qdrant_client
from app.schemas.site import (
    SiteAliasItem,
    SiteChunkItem,
    SiteDetail,
    SiteFactItem,
    SitesListResponse,
    SiteSourceItem,
    SiteSummary,
)
from app.schemas.stats import StatsResponse

logger = logging.getLogger(__name__)
router = APIRouter(tags=["Sites"])


@router.get("/stats", response_model=StatsResponse)
def get_system_stats(db: Session = Depends(get_db)) -> StatsResponse:
    """Return database counts, vector index point count, language breakdown, and category breakdown."""
    sites_cnt = db.execute(sa_text("SELECT count(*) FROM sites")).scalar() or 0
    chunks_cnt = db.execute(sa_text("SELECT count(*) FROM chunk_registry")).scalar() or 0
    users_cnt = db.execute(sa_text("SELECT count(*) FROM users")).scalar() or 0
    messages_cnt = db.execute(sa_text("SELECT count(*) FROM chat_messages")).scalar() or 0

    # Chunks per language
    lang_rows = db.execute(sa_text("SELECT lang, count(*) FROM chunk_registry GROUP BY lang")).fetchall()
    chunks_per_lang = {r[0]: int(r[1]) for r in lang_rows}

    # Sites per primary category
    cat_rows = db.execute(sa_text("SELECT primary_category, count(*) FROM sites GROUP BY primary_category")).fetchall()
    sites_per_cat = {r[0]: int(r[1]) for r in cat_rows}

    # Qdrant point count
    qdrant_points = 0
    try:
        qdrant = get_qdrant_client()
        info = qdrant.get_collection(settings.QDRANT_COLLECTION)
        qdrant_points = info.points_count or 0
    except Exception as e:
        logger.warning(f"Could not read Qdrant point count: {e}")
        qdrant_points = 0

    return StatsResponse(
        sites=sites_cnt,
        chunks_registry=chunks_cnt,
        qdrant_points=qdrant_points,
        users=users_cnt,
        chat_messages=messages_cnt,
        chunks_per_lang=chunks_per_lang,
        sites_per_primary_category=sites_per_cat,
    )


@router.get("/sites", response_model=SitesListResponse)
def list_sites(
    category: Optional[str] = Query(default=None, description="Filter by category code"),
    district: Optional[str] = Query(default=None, description="Filter by district name"),
    status: Optional[str] = Query(default=None, description="Filter by site_status"),
    q: Optional[str] = Query(default=None, description="Search query by name or alias (pg_trgm similarity)"),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=10, ge=1, le=100),
    db: Session = Depends(get_db),
) -> SitesListResponse:
    """List heritage sites with filtering, pagination, and pg_trgm similarity search."""
    offset = (page - 1) * page_size
    params = {}
    where_clauses = ["1=1"]

    if category:
        where_clauses.append("(:category = ANY(s.categories) OR s.primary_category = :category)")
        params["category"] = category

    if district:
        where_clauses.append("lower(d.name_en) = lower(:district)")
        params["district"] = district

    if status:
        where_clauses.append("s.status = CAST(:status AS site_status)")
        params["status"] = status

    order_by = "s.name_en ASC"

    if q and q.strip():
        q_clean = q.strip().lower()
        params["q"] = q_clean
        # Use pg_trgm similarity across site names and aliases
        where_clauses.append("""(
            s.name_en ILIKE '%' || :q || '%'
            OR s.name_ta ILIKE '%' || :q || '%'
            OR similarity(lower(s.name_en), :q) > 0.15
            OR EXISTS (
                SELECT 1 FROM site_aliases a
                WHERE a.site_id = s.site_id
                AND (a.alias_norm ILIKE '%' || :q || '%' OR similarity(a.alias_norm, :q) > 0.15)
            )
        )""")
        order_by = """
            GREATEST(
                similarity(lower(s.name_en), :q),
                COALESCE((
                    SELECT MAX(similarity(a.alias_norm, :q))
                    FROM site_aliases a WHERE a.site_id = s.site_id
                ), 0)
            ) DESC, s.name_en ASC
        """

    where_sql = " AND ".join(where_clauses)

    count_sql = sa_text(f"""
        SELECT count(*)
        FROM sites s
        JOIN locations d ON s.district_id = d.location_id
        WHERE {where_sql}
    """)
    total = db.execute(count_sql, params).scalar() or 0

    select_sql = sa_text(f"""
        SELECT s.site_id, s.name_en, s.name_ta, s.primary_category, s.categories,
               d.name_en AS district_name, s.status, s.trust, s.lat, s.lon
        FROM sites s
        JOIN locations d ON s.district_id = d.location_id
        WHERE {where_sql}
        ORDER BY {order_by}
        LIMIT :limit OFFSET :offset
    """)
    params["limit"] = page_size
    params["offset"] = offset

    rows = db.execute(select_sql, params).mappings().fetchall()

    site_summaries = [
        SiteSummary(
            site_id=r["site_id"],
            name_en=r["name_en"],
            name_ta=r["name_ta"],
            primary_category=r["primary_category"],
            categories=list(r["categories"]) if r["categories"] else [],
            district_name=r["district_name"],
            status=str(r["status"]),
            trust=str(r["trust"]),
            lat=float(r["lat"]) if r["lat"] is not None else None,
            lon=float(r["lon"]) if r["lon"] is not None else None,
        )
        for r in rows
    ]

    return SitesListResponse(
        total=total,
        page=page,
        page_size=page_size,
        sites=site_summaries,
    )


@router.get("/sites/{site_id}", response_model=SiteDetail)
def get_site_detail(site_id: str, db: Session = Depends(get_db)) -> SiteDetail:
    """Retrieve full details of a specific site including locations, aliases, facts, sources, and chunks."""
    site_sql = sa_text("""
        SELECT s.site_id, s.parent_site_id, s.name_en, s.name_ta, s.primary_category, s.categories,
               s.district_id, d.name_en AS district_name,
               s.subdistrict_id, sub.name_en AS subdistrict_name,
               s.local_body_id, lb.name_en AS local_body_name, lb.local_body_type,
               s.lat, s.lon, s.period, s.dynasty, s.status, s.trust, s.origin
        FROM sites s
        JOIN locations d ON s.district_id = d.location_id
        LEFT JOIN locations sub ON s.subdistrict_id = sub.location_id
        LEFT JOIN locations lb ON s.local_body_id = lb.location_id
        WHERE s.site_id = :site_id
    """)
    site_row = db.execute(site_sql, {"site_id": site_id}).mappings().first()
    if not site_row:
        raise HTTPException(status_code=404, detail=f"Site '{site_id}' not found")

    # 1. Fetch aliases
    aliases_sql = sa_text("SELECT alias, lang FROM site_aliases WHERE site_id = :site_id ORDER BY lang, alias")
    aliases_rows = db.execute(aliases_sql, {"site_id": site_id}).mappings().fetchall()
    aliases = [SiteAliasItem(alias=r["alias"], lang=r["lang"]) for r in aliases_rows]

    # 2. Fetch facts with optional source url
    facts_sql = sa_text("""
        SELECT f.field, f.value, f.lang, f.score, f.status, src.url AS source_url
        FROM site_facts f
        LEFT JOIN sources src ON f.source_id = src.source_id
        WHERE f.site_id = :site_id
        ORDER BY f.field, f.lang
    """)
    facts_rows = db.execute(facts_sql, {"site_id": site_id}).mappings().fetchall()
    facts = [
        SiteFactItem(
            field=r["field"],
            value=r["value"],
            lang=r["lang"],
            score=float(r["score"]) if r["score"] is not None else None,
            status=str(r["status"]),
            source_url=r["source_url"],
        )
        for r in facts_rows
    ]

    # 3. Fetch sources associated with site
    sources_sql = sa_text("""
        SELECT DISTINCT src.source_id, src.url, src.domain, src.tier
        FROM sources src
        WHERE src.source_id IN (
            SELECT source_id FROM site_facts WHERE site_id = :site_id AND source_id IS NOT NULL
            UNION
            SELECT source_id FROM chunk_registry WHERE site_id = :site_id AND source_id IS NOT NULL
        )
        ORDER BY src.tier, src.domain
    """)
    sources_rows = db.execute(sources_sql, {"site_id": site_id}).mappings().fetchall()
    sources = [
        SiteSourceItem(
            source_id=r["source_id"],
            url=r["url"],
            domain=r["domain"],
            tier=r["tier"],
        )
        for r in sources_rows
    ]

    # 4. Fetch chunks metadata from chunk_registry
    chunks_sql = sa_text("""
        SELECT chunk_id, section, lang, source_id, token_count
        FROM chunk_registry
        WHERE site_id = :site_id
        ORDER BY section, lang
    """)
    chunk_rows = db.execute(chunks_sql, {"site_id": site_id}).mappings().fetchall()

    # Retrieve text payloads from Qdrant
    chunk_ids_str = [str(r["chunk_id"]) for r in chunk_rows]
    qdrant_text_map = {}
    if chunk_ids_str:
        try:
            qdrant = get_qdrant_client()
            retrieved_points = qdrant.retrieve(
                collection_name=settings.QDRANT_COLLECTION,
                ids=chunk_ids_str,
                with_payload=True
            )
            for p in retrieved_points:
                if p.payload and "text" in p.payload:
                    qdrant_text_map[str(p.id)] = p.payload["text"]
        except Exception as e:
            logger.warning(f"Failed to retrieve chunk texts from Qdrant: {e}")

    chunks = []
    for r in chunk_rows:
        cid_str = str(r["chunk_id"])
        chunk_text = qdrant_text_map.get(cid_str, "")
        chunks.append(
            SiteChunkItem(
                chunk_id=cid_str,
                section=r["section"],
                lang=r["lang"],
                text=chunk_text,
                source_id=r["source_id"],
                token_count=r["token_count"],
            )
        )

    return SiteDetail(
        site_id=site_row["site_id"],
        parent_site_id=site_row["parent_site_id"],
        name_en=site_row["name_en"],
        name_ta=site_row["name_ta"],
        primary_category=site_row["primary_category"],
        categories=list(site_row["categories"]) if site_row["categories"] else [],
        district_id=site_row["district_id"],
        district_name=site_row["district_name"],
        subdistrict_id=site_row["subdistrict_id"],
        subdistrict_name=site_row["subdistrict_name"],
        local_body_id=site_row["local_body_id"],
        local_body_name=site_row["local_body_name"],
        local_body_type=str(site_row["local_body_type"]) if site_row["local_body_type"] else None,
        lat=float(site_row["lat"]) if site_row["lat"] is not None else None,
        lon=float(site_row["lon"]) if site_row["lon"] is not None else None,
        period=site_row["period"],
        dynasty=site_row["dynasty"],
        status=str(site_row["status"]),
        trust=str(site_row["trust"]),
        origin=site_row["origin"],
        aliases=aliases,
        facts=facts,
        sources=sources,
        chunks=chunks,
    )
