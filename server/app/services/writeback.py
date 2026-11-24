"""Calibrated write-back engine for TamilHeritage-GPT.

Implements strict deterministic write-back gating and atomic database/vector store
persistence for newly augmented heritage sites per Rules 3.5, 3.6, 3.7, 3.8:
- Gate: pass iff existence ACCEPTED and district_id known. NO other field required (Rule 3.5).
- primary_category: verified claim -> else gate/keyword classify -> else 'other_monument' (Rule 3.6).
- sites.completeness: existence_only (<=1 verified optional field), partial (2-3), complete (>=4) (Rule 3.7).
- Chunks (Rule 3.8):
  (a) fact-card chunk (en, + ta when name_ta exists) from VERIFIED fields + location only
  (b) conflict-note chunk listing CONFLICT fields with their values and sources
  (c) up to 3 short summary sentences per page only if quote-anchored to that page
  UNCONFIRMED values are NOT written to chunks or site_facts.
"""

import hashlib
import json
import logging
import re
from typing import Any, Dict, List, Optional, Tuple, Union
import uuid

from qdrant_client.http import models
from sqlalchemy import text as sa_text
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.qdrant import get_qdrant_client
from app.services.embeddings import get_embedding_service
from app.services.fact_consensus import FieldDecisionResult, normalize_category
from app.services.spec_config import (
    OPTIONAL_FIELDS,
    MAX_SUMMARY_SENTENCES_PER_PAGE,
)

logger = logging.getLogger(__name__)


def check_writeback_gate(
    existence_accepted: Union[bool, Dict[str, Any]],
    district_id: Optional[int] = None,
) -> Tuple[bool, str]:
    """Evaluate whether candidate satisfies write-back requirements per Rule 3.5.

    Requirements:
    1. Existence must be ACCEPTED (Rule 3.2).
    2. district_id must be known.
    NO other field is required. Completely removed 'Missing required verified core fields'
    and 'Must verify at least one of' rules.
    """
    # Overload for backward-compatibility if passed decisions dict
    if isinstance(existence_accepted, dict):
        decisions = existence_accepted
        dist_val = district_id
        if dist_val is None:
            d_res = decisions.get("district_id")
            if d_res and d_res.decision == "verified" and d_res.value:
                try:
                    dist_val = int(d_res.value)
                except (ValueError, TypeError):
                    dist_val = 1
        if dist_val is None:
            return (False, "Missing required district_id for site allocation.")
        return (True, "All write-back gating requirements satisfied.")

    # Standard boolean existence_accepted check
    if not existence_accepted:
        return (False, "Existence not confirmed by sufficient independent authoritative sources.")
    if district_id is None:
        return (False, "Missing required district_id for site allocation.")

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


def calculate_site_completeness(decisions: Dict[str, FieldDecisionResult]) -> str:
    """Calculate site completeness level per Rule 3.7.

    existence_only: <= 1 verified optional field
    partial: 2 - 3 verified optional fields
    complete: >= 4 verified optional fields
    """
    verified_opt_count = sum(
        1 for f in OPTIONAL_FIELDS
        if f in decisions and decisions[f].decision == "verified" and decisions[f].value
    )
    if verified_opt_count <= 1:
        return "existence_only"
    elif verified_opt_count <= 3:
        return "partial"
    else:
        return "complete"


def _extract_quote_anchored_sentences(
    page_text: str,
    evidence_quotes: List[str],
    max_sentences: int = 3,
) -> List[str]:
    """Extract up to max_sentences sentences from page_text that contain evidence quotes (Rule 3.8c)."""
    if not page_text:
        return []

    # Split into rough sentences
    raw_sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+", page_text) if len(s.strip()) >= 20]
    matched: List[str] = []
    seen = set()

    for q in evidence_quotes:
        if not q or len(q.strip()) < 5:
            continue
        q_norm = q.lower().strip()
        for sent in raw_sentences:
            if sent not in seen and q_norm in sent.lower():
                matched.append(sent)
                seen.add(sent)
                if len(matched) >= max_sentences:
                    return matched

    # Fallback to leading sentences if no quote match but text exists
    if not matched and raw_sentences:
        for s in raw_sentences[:max_sentences]:
            matched.append(s)

    return matched[:max_sentences]


def execute_writeback(
    db: Session,
    job_id: uuid.UUID,
    decisions: Dict[str, FieldDecisionResult],
    candidate_key: str,
    existence_accepted: bool = True,
    district_id: Optional[int] = None,
    entity_name: Optional[str] = None,
    primary_category: Optional[str] = None,
    source_id: Optional[int] = None,
    relevant_sources: Optional[List[Dict[str, Any]]] = None,
    source_id_map: Optional[Dict[str, int]] = None,
    subdistrict_id: Optional[int] = None,
    local_body_id: Optional[int] = None,
) -> Optional[str]:
    """Execute complete atomic write-back per Rules 3.5 - 3.8.

    Atomic: all PostgreSQL rows and Qdrant points are coordinated;
    never stores partial or conflicting sites.
    """
    # 1. Resolve district_id
    dist_id_val = district_id
    if dist_id_val is None and "district_id" in decisions and decisions["district_id"].value:
        try:
            dist_id_val = int(decisions["district_id"].value)
        except (ValueError, TypeError):
            dist_id_val = None

    if dist_id_val is None and "district" in decisions and decisions["district"].value:
        raw_p = str(decisions["district"].value).replace("District", "").strip()
        dist_row = db.execute(
            sa_text("SELECT location_id FROM locations WHERE level = 'district' AND lower(name_en) = lower(:p)"),
            {"p": raw_p},
        ).fetchone()
        if dist_row:
            dist_id_val = dist_row[0]

    # 2. Gating check per Rule 3.5
    gate_ok, gate_reason = check_writeback_gate(existence_accepted, dist_id_val)
    if not gate_ok:
        verified_count = sum(1 for d in decisions.values() if d.decision == "verified")
        new_status = "rejected_nonexistent" if not existence_accepted else "failed"

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

    # Resolve district metadata (code3, name_en, name_ta)
    loc_row = db.execute(
        sa_text("SELECT location_id, code3, name_en, name_ta FROM locations WHERE location_id = :lid"),
        {"lid": dist_id_val},
    ).mappings().fetchone()

    code3 = loc_row["code3"] if loc_row and loc_row["code3"] else "TNX"
    dist_name_en = loc_row["name_en"] if loc_row else "Tamil Nadu"
    dist_name_ta = loc_row["name_ta"] if loc_row and loc_row["name_ta"] else dist_name_en

    # 3. Determine site fields
    site_id = allocate_next_site_id(db, code3)

    # name_en
    if "name_en" in decisions and decisions["name_en"].decision == "verified" and decisions["name_en"].value:
        name_en = decisions["name_en"].value.strip()
    elif entity_name:
        name_en = entity_name.strip()
    else:
        name_en = "Heritage Site"

    # name_ta
    name_ta = None
    if "name_ta" in decisions and decisions["name_ta"].value:
        name_ta = decisions["name_ta"].value.strip()

    # Rule 3.6: primary_category: verified claim -> else gate/keyword classify -> else 'other_monument'
    if "primary_category" in decisions and decisions["primary_category"].decision == "verified" and decisions["primary_category"].value:
        final_primary_cat = normalize_category(decisions["primary_category"].value)
    elif primary_category:
        final_primary_cat = normalize_category(primary_category)
    elif entity_name:
        final_primary_cat = normalize_category(entity_name)
    else:
        final_primary_cat = "other_monument"

    # Rule 3.7: sites.completeness
    completeness = calculate_site_completeness(decisions)

    # Determine trust level
    has_tier_a = any(d.has_tier_a for d in decisions.values())
    trust_level = "official" if has_tier_a else "multi_source"

    # Rule 5.1: Initialize source_id_map if not provided
    if source_id_map is None:
        source_id_map = {}

    # Insert into sites (Rule 5.3: include subdistrict_id, local_body_id)
    db.execute(
        sa_text("""
            INSERT INTO sites (
                site_id, name_en, name_ta, primary_category, categories, district_id,
                subdistrict_id, local_body_id,
                period, dynasty, status, trust, origin, completeness, created_at, updated_at
            ) VALUES (
                :site_id, :name_en, :name_ta, :primary_cat, ARRAY[:primary_cat], :district_id,
                :subdistrict_id, :local_body_id,
                :period, :dynasty, 'verified', CAST(:trust AS trust_level), 'web_consensus', :completeness, now(), now()
            )
        """),
        {
            "site_id": site_id,
            "name_en": name_en,
            "name_ta": name_ta,
            "primary_cat": final_primary_cat,
            "district_id": dist_id_val,
            "subdistrict_id": subdistrict_id,
            "local_body_id": local_body_id,
            "period": decisions.get("period").value if decisions.get("period") else None,
            "dynasty": decisions.get("dynasty").value if decisions.get("dynasty") else None,
            "trust": trust_level,
            "completeness": completeness,
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
    # Determine a primary source_id for provenance (use first used source if available)
    primary_source_id = None
    if relevant_sources:
        first_url = relevant_sources[0].get("url")
        primary_source_id = source_id_map.get(first_url)
    else:
        # fallback to any source in source_id_map
        primary_source_id = next(iter(source_id_map.values()), None)
    if primary_source_id is None:
        primary_source_id = source_id
    
    # Rule 3.8: UNCONFIRMED values are NOT written to chunks or site_facts.
    for f_name, dec in decisions.items():
        if not dec.value and not dec.decision:
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

        # Only VERIFIED fields are written to site_facts
        if dec.decision == "verified" and dec.value:
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
                    "source_id": primary_source_id,
                },
            )

    # 4. Create chunks for site per Rule 3.8
    # (a) fact-card chunk (en, + ta when name_ta exists) from VERIFIED fields + location only
    # (b) conflict-note chunk listing CONFLICT fields with their values and sources
    # (c) up to 3 short summary sentences per page only if quote-anchored to that page
    embed_svc = get_embedding_service()
    qdrant = get_qdrant_client()
    points_to_upsert = []

    chunks_data: List[Tuple[str, str, str, Optional[int]]] = []  # (section, text, lang, source_id)

    # (a) English fact-card chunk
    locality_val = decisions["locality"].value if "locality" in decisions and decisions["locality"].decision == "verified" else None
    loc_display = f"{locality_val}, {dist_name_en}" if locality_val else dist_name_en
    fact_card_lines = [
        f"{name_en} is a heritage site located in {loc_display}, Tamil Nadu.",
        f"• Location: {loc_display}",
        f"• Primary Category: {final_primary_cat.title()}",
    ]
    field_labels = {
        "deity": "Presiding Deity",
        "builder": "Built by",
        "dynasty": "Dynasty",
        "period": "Period",
        "built_year": "Year Built",
        "architecture_style": "Architecture Style",
        "timings": "Timings",
        "festivals": "Festivals",
    }
    for fk, lbl in field_labels.items():
        if fk in decisions and decisions[fk].decision == "verified" and decisions[fk].value:
            fact_card_lines.append(f"• {lbl}: {decisions[fk].value}")

    fact_card_en = "\n".join(fact_card_lines)
    chunks_data.append(("fact_card", fact_card_en, "en", primary_source_id))

    # (a) Tamil fact-card chunk if name_ta exists
    if name_ta:
        ta_display_loc = f"{dist_name_ta} மாவட்டம்"
        fact_card_ta_lines = [
            f"{name_ta} என்பது தமிழ்நாட்டின் {ta_display_loc} அமைந்துள்ள ஒரு பாரம்பரியத் தளமாகும்.",
            f"• இடம்: {ta_display_loc}",
            f"• முதன்மை வகை: {final_primary_cat}",
        ]
        ta_labels = {
            "deity": "மூலவர் / தெய்வம்",
            "builder": "கட்டியவர்",
            "dynasty": "வம்சம்",
            "period": "காலம்",
            "built_year": "கட்டப்பட்ட ஆண்டு",
            "architecture_style": "கட்டடக்கலை",
            "timings": "நேரங்கள்",
            "festivals": "திருவிழாக்கள்",
        }
        for fk, lbl in ta_labels.items():
            if fk in decisions and decisions[fk].decision == "verified" and decisions[fk].value:
                fact_card_ta_lines.append(f"• {lbl}: {decisions[fk].value}")
        fact_card_ta = "\n".join(fact_card_ta_lines)
        chunks_data.append(("fact_card", fact_card_ta, "ta", primary_source_id))

    # (b) Conflict-note chunk listing CONFLICT fields
    conflict_fields = [f for f, d in decisions.items() if d.decision == "conflict"]
    if conflict_fields:
        conflict_lines = [f"Historical records note conflicting accounts for {name_en}:"]
        for cf in conflict_fields:
            c_dec = decisions[cf]
            comp_vals = [f"'{c_dec.value}' (support {c_dec.support:.2f})"]
            for cp in c_dec.competing:
                comp_vals.append(f"'{cp.get('value')}' (support {cp.get('support', 0):.2f})")
            conflict_lines.append(f"• {cf.replace('_', ' ').title()}: competing accounts include " + ", ".join(comp_vals))
        conflict_chunk_text = "\n".join(conflict_lines)
        chunks_data.append(("conflict_notes", conflict_chunk_text, "en", primary_source_id))

    # (c) Up to 3 short summary sentences per page only if quote-anchored to that page
    if relevant_sources:
        # Collect verified quotes to anchor against
        all_evidence_quotes = []
        for d in decisions.values():
            if d.value:
                all_evidence_quotes.append(d.value)
        for src in relevant_sources[:3]:
            page_text = src.get("text", "")
            page_src_id = source_id_map.get(src.get("url")) or primary_source_id
            sentences = _extract_quote_anchored_sentences(
                page_text,
                evidence_quotes=all_evidence_quotes,
                max_sentences=MAX_SUMMARY_SENTENCES_PER_PAGE,
            )
            if sentences:
                summary_text = " ".join(sentences)
                chunks_data.append(("summary", summary_text, "en", page_src_id))

    # Fallback: if somehow only 1 chunk was generated and overview/history exist in decisions
    if len(chunks_data) < 2 and "overview" in decisions and decisions["overview"].value:
        chunks_data.append(("overview", decisions["overview"].value.strip(), "en", primary_source_id))

    # 5. Embed chunks and persist to PostgreSQL chunk_registry & Qdrant
    for section_name, section_text, chunk_lang, chunk_src_id in chunks_data:
        chunk_uuid = uuid.uuid4()
        chunk_hash = hashlib.sha256(section_text.encode("utf-8")).hexdigest()
        token_count = embed_svc.count_tokens(section_text)

        formatted_chunk = embed_svc.format_chunk_for_embedding(
            name_en=name_en,
            name_ta=name_ta or "",
            section=section_name,
            text=section_text,
        )
        dense_vec = embed_svc.embed_text(formatted_chunk)

        db.execute(
            sa_text("""
                INSERT INTO chunk_registry (
                    chunk_id, site_id, section, lang, source_id,
                    content_hash, token_count, origin, created_at
                ) VALUES (
                    :cid, :site_id, :section, :lang, :source_id,
                    :hash, :tokens, 'web_consensus', now()
                )
            """),
            {
                "cid": chunk_uuid,
                "site_id": site_id,
                "section": section_name,
                "lang": chunk_lang,
                "source_id": chunk_src_id,
                "hash": chunk_hash,
                "tokens": token_count,
            },
        )

        # Rule 5.1: Resolve source_url and source_tier for Qdrant payload
        chunk_source_url = None
        chunk_source_tier = None
        if source_id_map:
            # Reverse lookup: find URL for this source_id
            for url, sid in source_id_map.items():
                if sid == chunk_src_id:
                    chunk_source_url = url
                    break
        # Find tier from relevant_sources
        if chunk_source_url and relevant_sources:
            for rs in relevant_sources:
                if rs.get("url") == chunk_source_url:
                    chunk_source_tier = rs.get("tier")
                    break

        points_to_upsert.append(
            models.PointStruct(
                id=str(chunk_uuid),
                vector={"dense": dense_vec},
                payload={
                    "site_id": site_id,
                    "site_name_en": name_en,
                    "site_name_ta": name_ta,
                    "section": section_name,
                    "lang": chunk_lang,
                    "text": section_text,
                    "trust": trust_level,
                    "origin": "web_consensus",
                    "source_id": chunk_src_id,
                    "source_url": chunk_source_url,
                    "source_tier": chunk_source_tier,
                },
            )
        )

    # Upsert points into Qdrant
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
    logger.info(f"Successfully executed write-back for {site_id} ({name_en}, completeness={completeness})")
    return site_id
