"""Chunk ingestion service.

Links PostgreSQL sites and chunk_registry with Qdrant vector index.
Ensures deterministic UUID5 generation, batched upserts, and idempotence.
"""

import hashlib
import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional
import uuid
from qdrant_client.http import models
from sqlalchemy import text as sa_text
from sqlalchemy.orm import Session
from app.core.config import settings
from app.db.postgres import SessionLocal
from app.db.qdrant import ensure_collection, get_qdrant_client
from app.services.embeddings import get_embedding_service

logger = logging.getLogger(__name__)

# Deterministic UUID5 namespace specified for TamilHeritage-GPT
NAMESPACE = uuid.UUID("6f1c2a52-6b7e-4d0e-9a51-3c0f2b7e9d11")


def generate_chunk_id(site_id: str, section: str, lang: str, text_content: str) -> tuple[uuid.UUID, str]:
    """Generate deterministic UUID5 and content SHA256 hash for a chunk.

    Formula: uuid5(NAMESPACE, f"{site_id}|{section}|{lang}|{sha256(text)}")
    """
    content_hash = hashlib.sha256(text_content.encode("utf-8")).hexdigest()
    raw_key = f"{site_id}|{section}|{lang}|{content_hash}"
    chunk_id = uuid.uuid5(NAMESPACE, raw_key)
    return chunk_id, content_hash


def load_sites_metadata(db: Session) -> Dict[str, Dict[str, Any]]:
    """Query PostgreSQL for site details needed to construct Qdrant payloads."""
    query = sa_text("""
        SELECT s.site_id, s.name_en, s.name_ta, s.categories, s.trust, s.status,
               l.name_en AS district_name
        FROM sites s
        JOIN locations l ON s.district_id = l.location_id
    """)
    rows = db.execute(query).mappings().fetchall()
    metadata = {}
    for r in rows:
        metadata[r["site_id"]] = {
            "name_en": r["name_en"],
            "name_ta": r["name_ta"] or "",
            "categories": list(r["categories"]) if r["categories"] else [],
            "trust": str(r["trust"]),
            "status": str(r["status"]),
            "district": r["district_name"] or "",
        }
    return metadata


def ingest_chunks_data(chunks_data: List[Dict[str, Any]], batch_size: int = 16) -> Dict[str, int]:
    """Ingest chunk records into PostgreSQL chunk_registry and Qdrant vector index.

    Returns summary counts of processed chunks.
    """
    ensure_collection()
    qdrant = get_qdrant_client()
    embedder = get_embedding_service()

    with SessionLocal() as db:
        sites_meta = load_sites_metadata(db)

        prepared_chunks = []
        texts_to_embed = []

        for item in chunks_data:
            site_id = item["site_id"]
            section = item["section"]
            lang = item["lang"]
            chunk_text = item["text"]
            source_id = item.get("source_id")

            site = sites_meta.get(site_id)
            if not site:
                logger.warning(f"Site '{site_id}' not found in sites table. Skipping chunk.")
                continue

            chunk_uuid, content_hash = generate_chunk_id(site_id, section, lang, chunk_text)
            token_count = embedder.count_tokens(chunk_text)

            embed_text = embedder.format_chunk_for_embedding(
                site["name_en"], site["name_ta"], section, chunk_text
            )
            texts_to_embed.append(embed_text)

            prepared_chunks.append({
                "chunk_id": chunk_uuid,
                "site_id": site_id,
                "section": section,
                "lang": lang,
                "source_id": source_id,
                "content_hash": content_hash,
                "token_count": token_count,
                "origin": "curated",
                "text": chunk_text,
                "site_name_en": site["name_en"],
                "site_name_ta": site["name_ta"],
                "categories": site["categories"],
                "district": site["district"],
                "trust": site["trust"],
                "status": site["status"],
            })

        if not prepared_chunks:
            return {"ingested": 0, "total": 0}

        # 1. Compute embeddings
        logger.info(f"Computing embeddings for {len(texts_to_embed)} chunks using {settings.EMBED_MODEL}...")
        embeddings = embedder.embed_texts(texts_to_embed, batch_size=batch_size)

        # 2. Insert into PostgreSQL chunk_registry
        pg_insert = sa_text("""
            INSERT INTO chunk_registry (
                chunk_id, site_id, section, lang, source_id, content_hash, token_count, origin
            ) VALUES (
                :chunk_id, :site_id, :section, :lang, :source_id, :content_hash, :token_count, :origin
            )
            ON CONFLICT (chunk_id) DO NOTHING
        """)

        for chunk in prepared_chunks:
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

        # 3. Upsert into Qdrant in batches
        points = []
        for i, chunk in enumerate(prepared_chunks):
            point = models.PointStruct(
                id=str(chunk["chunk_id"]),
                vector={"dense": embeddings[i]},
                payload={
                    "chunk_id": str(chunk["chunk_id"]),
                    "site_id": chunk["site_id"],
                    "site_name_en": chunk["site_name_en"],
                    "site_name_ta": chunk["site_name_ta"],
                    "section": chunk["section"],
                    "lang": chunk["lang"],
                    "categories": chunk["categories"],
                    "district": chunk["district"],
                    "trust": chunk["trust"],
                    "status": chunk["status"],
                    "source_id": chunk["source_id"],
                    "origin": chunk["origin"],
                    "content_hash": chunk["content_hash"],
                    "text": chunk["text"],
                }
            )
            points.append(point)

        for i in range(0, len(points), batch_size):
            batch = points[i : i + batch_size]
            qdrant.upsert(
                collection_name=settings.QDRANT_COLLECTION,
                points=batch,
                wait=True
            )

        logger.info(f"Successfully ingested {len(prepared_chunks)} chunks to Postgres & Qdrant.")
        return {"ingested": len(prepared_chunks), "total": len(chunks_data)}


def ingest_from_file(file_path: Path) -> Dict[str, int]:
    """Read a JSON chunks file and ingest records."""
    with open(file_path, "r", encoding="utf-8") as f:
        chunks_data = json.load(f)
    return ingest_chunks_data(chunks_data)
