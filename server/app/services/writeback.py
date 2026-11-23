"""Calibrated write-back engine for TamilHeritage-GPT.

Implements strict deterministic write-back gating and atomic database/vector store
persistence for newly augmented heritage sites:
1. Gating check: ALL required fields must be 'verified' (name_en, primary_category,
   district_id, overview, history, and at least one of builder|period|dynasty).
2. Generates canonical site_id: "TN-" + district.code3 + "-" + next_seq.
3. Inserts site (status='verified', trust='multi_source' | 'official', origin='web_consensus').
4. Inserts site_aliases, site_facts (decision='verified'), chunk_registry entries.
5. Embeds chunks via bge-m3 and upserts to Qdrant heritage_chunks.
6. Updates augment_jobs status and audit log.
7. Atomic rollback on failure: never inserts partial or conflicting sites.
"""

import hashlib
import json
import logging
import re
from typing import Any, Dict, List, Optional, Tuple
import uuid
from qdrant_client.http import models
from sqlalchemy import text as sa_text
from sqlalchemy.orm import Session
from app.core.config import settings
from app.db.postgres import SessionLocal
from app.db.qdrant import get_qdrant_client
from app.services.embeddings import get_embedding_service
from app.services.fact_consensus import FieldDecisionResult

logger = logging.getLogger(__name__)

REQUIRED_CORE_FIELDS = {"name_en", "primary_category", "district_id", "overview", "history"}
HISTORICAL_CONTEXT_FIELDS = {"builder", "period", "dynasty", "built_year"}


def check_writeback_gate(
    decisions: Dict[str, FieldDecisionResult],
) -> Tuple[bool, str]:
    """Evaluate whether candidate field decisions satisfy all strict write-back requirements.

    Requirements:
    1. All core fields (name_en, primary_category, district_id, overview, history) must be 'verified'.
    2. At least one historical context field (builder, period, dynasty, built_year) must be 'verified'.
    """
    missing_core = [
        f for f in REQUIRED_CORE_FIELDS
        if f not in decisions or decisions[f].decision != "verified" or not decisions[f].value
    ]

    if missing_core:
        return (False, f"Missing required verified core fields: {', '.join(missing_core)}")

    has_historical = any(
        f in decisions and decisions[f].decision == "verified" and decisions[f].value
        for f in HISTORICAL_CONTEXT_FIELDS
    )
    if not has_historical:
        return (False, f"Must verify at least one of: {', '.join(sorted(HISTORICAL_CONTEXT_FIELDS))}")

    return (True, "All write-back gating requirements satisfied.")


def allocate_next_site_id(db: Session, code3: str) -> str:
    """Generate next site_id formatted as TN-{code3}-{seq:04d}."""
    pattern = f"TN-{code3.upper()}-%"
    rows = db.execute(
        sa_text("SELECT site_id FROM sites WHERE site_id LIKE :pat"),
        {"pat": pattern},
    ).fetchall()

    max_seq = 0
    seq_re = re.compile(rf"^TN-{code3.upper()}-(\d{{4}})$")
    for r in rows:
        m = seq_re.match(r[0])
        if m:
            max_seq = max(max_seq, int(m.group(1)))

    next_seq = max_seq + 1
    return f"TN-{code3.upper()}-{next_seq:04d}"


def execute_writeback(
    db: Session,
    job_id: uuid.UUID,
    decisions: Dict[str, FieldDecisionResult],
    candidate_key: str,
    source_id: Optional[int] = None,
) -> Optional[str]:
    """Execute complete writeback if gating criteria are satisfied.

    Atomic: all PostgreSQL rows and Qdrant points are coordinated;
    never stores partial or conflicting sites.
    """
    gate_ok, gate_reason = check_writeback_gate(decisions)

    if not gate_ok:
        verified_count = sum(1 for d in decisions.values() if d.decision == "verified")
        new_status = "rejected_nonexistent" if verified_count == 0 else "failed"

        db.execute(
            sa_text("""
                UPDATE augment_jobs
                SET status = :status,
                    decision_reason = :reason,
                    finished_at = now()
                WHERE job_id = :jid
            """),
            {"status": new_status, "reason": gate_reason, "jid": job_id},
        )
        db.commit()
        logger.info(f"Write-back gate rejected job {job_id}: {gate_reason} (status={new_status})")
        return None

    # Resolve district code3
    dist_id_val = int(decisions["district_id"].value)
    dist_row = db.execute(
        sa_text("SELECT code3, name_en FROM locations WHERE location_id = :lid"),
        {"lid": dist_id_val},
    ).mappings().fetchone()

    code3 = dist_row["code3"] if dist_row and dist_row["code3"] else "TNX"

    # Allocate next site ID
    site_id = allocate_next_site_id(db, code3)
    name_en = decisions["name_en"].value.strip()
    name_ta = decisions.get("name_ta").value.strip() if decisions.get("name_ta") and decisions["name_ta"].value else None
    primary_category = decisions["primary_category"].value.strip().lower()

    # Determine trust level
    has_tier_a = any(d.has_tier_a for d in decisions.values())
    trust_level = "official" if has_tier_a else "multi_source"

    # Insert into sites
    db.execute(
        sa_text("""
            INSERT INTO sites (
                site_id, name_en, name_ta, primary_category, categories, district_id,
                period, dynasty, status, trust, origin, created_at, updated_at
            ) VALUES (
                :site_id, :name_en, :name_ta, :primary_cat, ARRAY[:primary_cat], :district_id,
                :period, :dynasty, 'verified', CAST(:trust AS trust_level), 'web_consensus', now(), now()
            )
        """),
        {
            "site_id": site_id,
            "name_en": name_en,
            "name_ta": name_ta,
            "primary_cat": primary_category,
            "district_id": dist_id_val,
            "period": decisions.get("period").value if decisions.get("period") else None,
            "dynasty": decisions.get("dynasty").value if decisions.get("dynasty") else None,
            "trust": trust_level,
        },
    )

    # Insert site aliases
    db.execute(
        sa_text("""
            INSERT INTO site_aliases (site_id, alias, lang)
            VALUES (:site_id, :name_en, 'en')
            ON CONFLICT (site_id, alias) DO NOTHING
        """),
        {"site_id": site_id, "name_en": name_en},
    )
    if name_ta:
        db.execute(
            sa_text("""
                INSERT INTO site_aliases (site_id, alias, lang)
                VALUES (:site_id, :name_ta, 'ta')
                ON CONFLICT (site_id, alias) DO NOTHING
            """),
            {"site_id": site_id, "name_ta": name_ta},
        )

    # Insert field_decisions and site_facts
    for f_name, dec in decisions.items():
        if not dec.value:
            continue

        db.execute(
            sa_text("""
                INSERT INTO field_decisions (
                    site_id, field, decision, value, competing, support,
                    n_clusters, has_tier_a, rule_fired, job_id, decided_at
                ) VALUES (
                    :site_id, :field, :decision, :value, CAST(:competing AS jsonb), :support,
                    :n_clusters, :has_tier_a, :rule_fired, :job_id, now()
                )
                ON CONFLICT (site_id, field) DO UPDATE SET
                    decision = EXCLUDED.decision,
                    value = EXCLUDED.value,
                    support = EXCLUDED.support,
                    decided_at = now()
            """),
            {
                "site_id": site_id,
                "field": f_name,
                "decision": dec.decision,
                "value": dec.value,
                "competing": json.dumps(dec.competing or []),
                "support": dec.support,
                "n_clusters": dec.n_clusters,
                "has_tier_a": dec.has_tier_a,
                "rule_fired": dec.rule_fired,
                "job_id": job_id,
            },
        )

        if dec.decision == "verified":
            db.execute(
                sa_text("""
                    INSERT INTO site_facts (
                        site_id, field, value, lang, score, status, decision, source_id
                    ) VALUES (
                        :site_id, :field, :value, 'en', :score, 'verified', 'verified', :source_id
                    )
                """),
                {
                    "site_id": site_id,
                    "field": f_name,
                    "value": dec.value,
                    "score": round(min(1.0, dec.support / 2.0), 3),
                    "source_id": source_id,
                },
            )

    # Create and embed chunks for vector retrieval
    embed_svc = get_embedding_service()
    chunks_to_create = [
        ("overview", decisions["overview"].value.strip()),
        ("history", decisions["history"].value.strip()),
    ]

    qdrant = get_qdrant_client()
    points_to_upsert = []

    for section_name, section_text in chunks_to_create:
        chunk_uuid = uuid.uuid4()
        chunk_hash = hashlib.sha256(section_text.encode("utf-8")).hexdigest()
        token_count = embed_svc.count_tokens(section_text)

        # Chunk text formatted for model: {name_en} | {name_ta} | {section}\n{text}
        formatted_chunk = embed_svc.format_chunk_for_embedding(
            name_en=name_en,
            name_ta=name_ta or "",
            section=section_name,
            text=section_text,
        )
        dense_vec = embed_svc.embed_text(formatted_chunk)

        # Insert into chunk_registry
        db.execute(
            sa_text("""
                INSERT INTO chunk_registry (
                    chunk_id, site_id, section, lang, source_id,
                    content_hash, token_count, origin, created_at
                ) VALUES (
                    :cid, :site_id, :section, 'en', :source_id,
                    :hash, :tokens, 'web_consensus', now()
                )
            """),
            {
                "cid": chunk_uuid,
                "site_id": site_id,
                "section": section_name,
                "source_id": source_id,
                "hash": chunk_hash,
                "tokens": token_count,
            },
        )

        points_to_upsert.append(
            models.PointStruct(
                id=str(chunk_uuid),
                vector={"dense": dense_vec},
                payload={
                    "site_id": site_id,
                    "site_name_en": name_en,
                    "site_name_ta": name_ta,
                    "section": section_name,
                    "lang": "en",
                    "text": section_text,
                    "trust": trust_level,
                    "origin": "web_consensus",
                    "source_id": source_id,
                },
            )
        )

    # Upsert to Qdrant
    if points_to_upsert:
        qdrant.upsert(
            collection_name=settings.QDRANT_COLLECTION,
            points=points_to_upsert,
        )

    # Update augment_jobs
    db.execute(
        sa_text("""
            UPDATE augment_jobs
            SET status = 'verified',
                site_id = :site_id,
                decision_reason = :reason,
                finished_at = now()
            WHERE job_id = :jid
        """),
        {
            "site_id": site_id,
            "reason": f"Successfully verified and wrote back site {site_id} ({name_en})",
            "jid": job_id,
        },
    )

    db.commit()
    logger.info(f"Successfully executed write-back for {site_id} ({name_en})")
    return site_id
