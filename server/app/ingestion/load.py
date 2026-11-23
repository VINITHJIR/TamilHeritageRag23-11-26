"""Database and vector index loader for the ingestion pipeline.

Method
------
1. Transactional Relational Upsert:
   Sites, aliases, sources, facts, and site_source_keys are loaded in atomic batches.
   Trust tiers are mapped systematically from source tiers:
     - Tier 'A' (official, ASI/Govt) -> trust 'official'
     - Tier 'B' (reference, Wikipedia/Wikidata) -> trust 'multi_source'
     - Tier 'C' (unverified) -> trust 'unverified'
2. Vector Index & Chunk Registry:
   Chunks are embedded using the BAAI/bge-m3 dense 1024-dim model (via EmbeddingService).
   Chunk IDs are generated deterministically using the Level 1 UUID5 formula:
     uuid5(NAMESPACE, f"{site_id}|{section}|{lang}|{sha256(text)}")
   PostgreSQL `chunk_registry` rows and Qdrant point IDs are guaranteed to be 100% identical.
"""

from __future__ import annotations

import hashlib
import logging
from typing import Any, Dict, List, Optional
import uuid

from qdrant_client.http import models
from sqlalchemy import text as sa_text
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.postgres import SessionLocal
from app.db.qdrant import ensure_collection, get_qdrant_client
from app.services.embeddings import get_embedding_service
from app.services.ingest import NAMESPACE, generate_chunk_id

logger = logging.getLogger(__name__)

TIER_TO_TRUST = {
    "A": "official",
    "B": "multi_source",
    "C": "unverified",
}


def load_sites_batch(
    db: Session,
    sites: List[Dict[str, Any]],
    aliases: Optional[List[Dict[str, Any]]] = None,
    sources: Optional[List[Dict[str, Any]]] = None,
    facts: Optional[List[Dict[str, Any]]] = None,
    dry_run: bool = False
) -> Dict[str, int]:
    """Upsert sites, aliases, sources, and facts in a single database transaction.

    Args:
        db: Active SQLAlchemy session.
        sites: List of site dictionaries.
        aliases: Optional list of site aliases.
        sources: Optional list of source records.
        facts: Optional list of site facts.
        dry_run: If True, simulate without committing changes.

    Returns:
        Dictionary of counts: {'sites': n, 'aliases': n, 'sources': n, 'facts': n}.
    """
    counts = {"sites": 0, "aliases": 0, "sources": 0, "facts": 0}
    if dry_run:
        counts["sites"] = len(sites)
        counts["aliases"] = len(aliases or [])
        counts["sources"] = len(sources or [])
        counts["facts"] = len(facts or [])
        return counts

    # 1. Upsert Sources
    source_id_map: Dict[str, int] = {}
    if sources:
        src_stmt = sa_text("""
            INSERT INTO sources (url, domain, tier, fetched_at, content_hash, license)
            VALUES (:url, :domain, :tier, now(), :content_hash, :license)
            ON CONFLICT (url) DO UPDATE SET
                content_hash = EXCLUDED.content_hash,
                fetched_at = now(),
                license = COALESCE(EXCLUDED.license, sources.license)
            RETURNING source_id, url
        """)
        for s in sources:
            res = db.execute(src_stmt, {
                "url": s["url"],
                "domain": s.get("domain", ""),
                "tier": s.get("tier", "B"),
                "content_hash": s.get("content_hash"),
                "license": s.get("license")
            }).fetchone()
            if res:
                source_id_map[res[1]] = res[0]
                counts["sources"] += 1

    # 2. Upsert Sites
    site_stmt = sa_text("""
        INSERT INTO sites (
            site_id, parent_site_id, name_en, name_ta, primary_category,
            categories, district_id, subdistrict_id, local_body_id,
            lat, lon, period, dynasty, status, trust, origin,
            wikidata_qid, asi_id, updated_at
        ) VALUES (
            :site_id, :parent_site_id, :name_en, :name_ta, :primary_category,
            :categories, :district_id, :subdistrict_id, :local_body_id,
            :lat, :lon, :period, :dynasty, CAST(:status AS site_status), CAST(:trust AS trust_level), :origin,
            :wikidata_qid, :asi_id, now()
        )
        ON CONFLICT (site_id) DO UPDATE SET
            parent_site_id = COALESCE(EXCLUDED.parent_site_id, sites.parent_site_id),
            name_ta = COALESCE(EXCLUDED.name_ta, sites.name_ta),
            categories = EXCLUDED.categories,
            lat = COALESCE(EXCLUDED.lat, sites.lat),
            lon = COALESCE(EXCLUDED.lon, sites.lon),
            wikidata_qid = COALESCE(EXCLUDED.wikidata_qid, sites.wikidata_qid),
            asi_id = COALESCE(EXCLUDED.asi_id, sites.asi_id),
            updated_at = now()
    """)

    for st in sites:
        trust_val = TIER_TO_TRUST.get(st.get("tier", "B"), "unverified")
        db.execute(site_stmt, {
            "site_id": st["site_id"],
            "parent_site_id": st.get("parent_site_id"),
            "name_en": st["name_en"],
            "name_ta": st.get("name_ta"),
            "primary_category": st["primary_category"],
            "categories": st.get("categories", [st["primary_category"]]),
            "district_id": st["district_id"],
            "subdistrict_id": st.get("subdistrict_id"),
            "local_body_id": st.get("local_body_id"),
            "lat": st.get("lat"),
            "lon": st.get("lon"),
            "period": st.get("period"),
            "dynasty": st.get("dynasty"),
            "status": st.get("status", "verified"),
            "trust": trust_val,
            "origin": st.get("origin", "curated"),
            "wikidata_qid": st.get("wikidata_qid"),
            "asi_id": st.get("asi_id")
        })
        counts["sites"] += 1

    # 3. Upsert Aliases
    if aliases:
        alias_stmt = sa_text("""
            INSERT INTO site_aliases (site_id, alias, lang)
            VALUES (:site_id, :alias, :lang)
            ON CONFLICT (site_id, alias) DO NOTHING
        """)
        for al in aliases:
            if al.get("alias") and al.get("site_id"):
                db.execute(alias_stmt, {
                    "site_id": al["site_id"],
                    "alias": al["alias"].strip(),
                    "lang": al.get("lang", "en")
                })
                counts["aliases"] += 1

    # 4. Upsert Facts
    if facts:
        fact_stmt = sa_text("""
            INSERT INTO site_facts (site_id, field, value, lang, source_id, score, status)
            VALUES (:site_id, :field, :value, :lang, :source_id, :score, CAST(:status AS site_status))
        """)
        for fc in facts:
            if fc.get("site_id") and fc.get("field") and fc.get("value"):
                src_url = fc.get("source_url")
                sid = source_id_map.get(src_url) if src_url else fc.get("source_id")
                db.execute(fact_stmt, {
                    "site_id": fc["site_id"],
                    "field": fc["field"],
                    "value": fc["value"],
                    "lang": fc.get("lang", "en"),
                    "source_id": sid,
                    "score": fc.get("score", 1.0),
                    "status": fc.get("status", "verified")
                })
                counts["facts"] += 1

    db.commit()
    return counts


def load_chunks_and_embeddings(
    db: Session,
    chunks: List[Dict[str, Any]],
    batch_size: int = 16,
    dry_run: bool = False
) -> Dict[str, int]:
    """Compute embeddings and upsert chunks into PostgreSQL chunk_registry and Qdrant.

    Args:
        db: Active SQLAlchemy session.
        chunks: List of chunk dictionaries containing {site_id, section, lang, text, ...}.
        batch_size: Embedding batch size.
        dry_run: If True, simulate without vectorizing or writing.

    Returns:
        Summary counts {'chunks_registered': n, 'vectors_upserted': n}.
    """
    if dry_run:
        return {"chunks_registered": len(chunks), "vectors_upserted": len(chunks)}

    if not chunks:
        return {"chunks_registered": 0, "vectors_upserted": 0}

    ensure_collection()
    qdrant = get_qdrant_client()
    embedder = get_embedding_service()

    # Pre-fetch site metadata from DB to enrich Qdrant payloads
    site_ids = list({c["site_id"] for c in chunks})
    meta_rows = db.execute(sa_text("""
        SELECT s.site_id, s.name_en, s.name_ta, s.categories, s.trust, s.status,
               l.name_en AS district_name
        FROM sites s
        JOIN locations l ON s.district_id = l.location_id
        WHERE s.site_id = ANY(:ids)
    """), {"ids": site_ids}).mappings().fetchall()

    site_map = {
        r["site_id"]: {
            "name_en": r["name_en"],
            "name_ta": r["name_ta"] or "",
            "categories": list(r["categories"]) if r["categories"] else [],
            "trust": str(r["trust"]),
            "status": str(r["status"]),
            "district": r["district_name"] or "",
        }
        for r in meta_rows
    }

    prepared: List[Dict[str, Any]] = []
    texts_to_embed: List[str] = []

    for item in chunks:
        sid = item["site_id"]
        sec = item["section"]
        lng = item["lang"]
        txt = item["text"]
        src_id = item.get("source_id")

        meta = site_map.get(sid, {
            "name_en": item.get("site_name_en", sid),
            "name_ta": item.get("site_name_ta", ""),
            "categories": item.get("categories", []),
            "trust": item.get("trust", "multi_source"),
            "status": "verified",
            "district": item.get("district", ""),
        })

        chunk_uuid, content_hash = generate_chunk_id(sid, sec, lng, txt)
        token_count = item.get("token_count") or embedder.count_tokens(txt)

        embed_text = embedder.format_chunk_for_embedding(
            meta["name_en"], meta["name_ta"], sec, txt
        )
        texts_to_embed.append(embed_text)

        prepared.append({
            "chunk_id": chunk_uuid,
            "site_id": sid,
            "section": sec,
            "lang": lng,
            "source_id": src_id,
            "content_hash": content_hash,
            "token_count": token_count,
            "origin": item.get("origin", "curated"),
            "text": txt,
            "site_name_en": meta["name_en"],
            "site_name_ta": meta["name_ta"],
            "categories": meta["categories"],
            "district": meta["district"],
            "trust": meta["trust"],
            "status": meta["status"],
        })

    logger.info(f"Computing embeddings for {len(texts_to_embed)} chunks...")
    embeddings = embedder.embed_texts(texts_to_embed, batch_size=batch_size)

    # 1. Insert into PostgreSQL chunk_registry
    pg_insert = sa_text("""
        INSERT INTO chunk_registry (
            chunk_id, site_id, section, lang, source_id, content_hash, token_count, origin
        ) VALUES (
            :chunk_id, :site_id, :section, :lang, :source_id, :content_hash, :token_count, :origin
        )
        ON CONFLICT (chunk_id) DO NOTHING
    """)

    for chunk in prepared:
        db.execute(pg_insert, {
            "chunk_id": chunk["chunk_id"],
            "site_id": chunk["site_id"],
            "section": chunk["section"],
            "lang": chunk["lang"],
            "source_id": chunk["source_id"],
            "content_hash": chunk["content_hash"],
            "token_count": chunk["token_count"],
            "origin": chunk["origin"],
        })
    db.commit()

    # 2. Upsert into Qdrant in batches
    points = [
        models.PointStruct(
            id=str(c["chunk_id"]),
            vector={"dense": embeddings[i]},
            payload={
                "chunk_id": str(c["chunk_id"]),
                "site_id": c["site_id"],
                "section": c["section"],
                "lang": c["lang"],
                "text": c["text"],
                "site_name_en": c["site_name_en"],
                "site_name_ta": c["site_name_ta"],
                "categories": c["categories"],
                "district": c["district"],
                "trust": c["trust"],
                "status": c["status"],
            }
        )
        for i, c in enumerate(prepared)
    ]

    for b_start in range(0, len(points), 100):
        b_slice = points[b_start:b_start + 100]
        qdrant.upsert(
            collection_name=settings.QDRANT_COLLECTION,
            points=b_slice
        )

    logger.info(f"Successfully upserted {len(points)} vectors to Qdrant collection '{settings.QDRANT_COLLECTION}'")
    return {
        "chunks_registered": len(prepared),
        "vectors_upserted": len(points)
    }
