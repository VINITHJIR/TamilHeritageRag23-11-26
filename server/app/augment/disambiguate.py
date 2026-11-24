"""Disambiguation module for clarification flow (Rule 2.2).

Performs candidate discovery and identity clarification:
1. Search candidate_cache first (TTL 30 days).
2. If miss, quick web search (max 5 URLs) extracting (locality, district) pairs mentioned with entity.
3. Cluster them, compute cluster support, store in candidate_cache.
4. Decision branching:
   - 0 candidates: rejected_nonexistent
   - 1 candidate or dominant (>= 70% support): resolved_direct
   - 2-4 candidates: needs_clarification (persisted in clarifications table)
"""

from datetime import datetime, timedelta, timezone
import json
import logging
import re
from typing import Any, Callable, Dict, List, Optional, Tuple
from urllib.parse import quote_plus
import uuid

import httpx
from sqlalchemy import text as sa_text
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.candidate_cache import CandidateCache
from app.services.augment_orchestrator import default_web_search, fetch_page_content
from app.services.clarifications import create_clarification, get_open_clarification
from app.services.place_resolution import TOWN_DISTRICT_ALIASES, resolve_place
from app.services.spec_config import (
    CANDIDATE_CACHE_TTL_DAYS,
    DOMINANT_CANDIDATE_RATIO,
    MAX_CLARIFICATION_CANDIDATES,
)

logger = logging.getLogger(__name__)


def _cache_key(query: str) -> str:
    return f"disamb:{query.lower().strip()}"


def _load_cached_candidates(db: Session, key: str) -> List[Dict]:
    """Retrieve candidates from candidate_cache if within TTL."""
    entry = db.query(CandidateCache).filter(CandidateCache.candidate_key == key).first()
    if entry:
        cutoff = datetime.now(timezone.utc) - timedelta(days=entry.ttl_days or CANDIDATE_CACHE_TTL_DAYS)
        fetched_at = entry.fetched_at
        if fetched_at.tzinfo is None:
            fetched_at = fetched_at.replace(tzinfo=timezone.utc)
        if fetched_at >= cutoff:
            return entry.candidates or []
    return []


def _store_candidates_cache(db: Session, key: str, candidates: List[Dict]):
    """Save discovered candidate list into candidate_cache with 30-day TTL."""
    existing = db.query(CandidateCache).filter(CandidateCache.candidate_key == key).first()
    if existing:
        existing.candidates = candidates
        existing.fetched_at = datetime.now(timezone.utc)
    else:
        cache = CandidateCache(
            candidate_key=key,
            candidates=candidates,
            ttl_days=CANDIDATE_CACHE_TTL_DAYS,
        )
        db.add(cache)
    db.commit()


def _discover_candidates_from_web(entity_name: str, db: Optional[Session] = None) -> List[Dict[str, Any]]:
    """Discover candidate (locality, district) pairs via web search, Wikipedia, and DB (Rule 2.2)."""
    discovered_pairs: List[Tuple[str, str]] = []
    page_texts: List[str] = []

    # 1. Check existing sites/aliases in DB first
    if db:
        try:
            db_sites = db.execute(
                sa_text("""
                    SELECT s.name_en, l.name_en AS district
                    FROM sites s
                    JOIN locations l ON s.district_id = l.location_id
                    JOIN site_aliases sa ON sa.site_id = s.site_id
                    WHERE similarity(sa.alias_norm, :ent) >= 0.35
                    LIMIT 4
                """),
                {"ent": entity_name.lower().strip()},
            ).fetchall()
            for s_name, dist in db_sites:
                if dist:
                    discovered_pairs.append((s_name, dist))
        except Exception as e:
            logger.debug(f"DB candidate lookup error: {e}")
            try:
                db.rollback()
            except Exception:
                pass

    # 2. Wikipedia search API for reliable encyclopedic snippets
    try:
        wiki_url = f"https://en.wikipedia.org/w/api.php?action=query&list=search&srsearch={quote_plus(entity_name + ' temple')}&format=json"
        with httpx.Client(timeout=4.0, headers={"User-Agent": "TamilHeritageBot/1.0"}) as client:
            w_res = client.get(wiki_url)
            if w_res.status_code == 200:
                for item in w_res.json().get("query", {}).get("search", [])[:4]:
                    snippet = re.sub(r"<[^>]+>", " ", item.get("snippet", ""))
                    title = item.get("title", "")
                    page_texts.append(f"{title} {snippet}")
    except Exception as e:
        logger.debug(f"Wiki candidate discovery failed: {e}")

    # 3. Quick web search (max 5 URLs)
    search_q = f"{entity_name} temple Tamil Nadu"
    try:
        urls = default_web_search(search_q, max_results=5)
        logger.info(f"Disambiguation search for '{entity_name}' found {len(urls)} URLs")
        for url in urls[:5]:
            page = fetch_page_content(url)
            if not page:
                continue
            text, status, final_url = page
            if text and len(text) >= 50:
                page_texts.append(text)
    except Exception as e:
        logger.debug(f"Web search candidate discovery failed: {e}")

    # 4. Try Gemini extraction if API key configured and page_texts exist
    if settings.GEMINI_API_KEY and page_texts:
        combined = "\n\n".join(t[:2000] for t in page_texts[:3])
        prompt = (
            f"Extract all distinct temple locations in Tamil Nadu for '{entity_name}'.\n"
            "Return a JSON array of objects with keys: 'locality' and 'district'.\n"
            f"Text:\n{combined[:3500]}"
        )
        try:
            from app.services.gemini import _call_gemini_api
            res = _call_gemini_api(prompt, json_response=True, timeout=8.0)
            items = json.loads(res["text"])
            if isinstance(items, list):
                for item in items:
                    loc = (item.get("locality") or "").strip()
                    dist = (item.get("district") or "").strip()
                    if loc or dist:
                        discovered_pairs.append((loc or dist, dist or loc))
        except Exception as e:
            logger.debug(f"Gemini candidate extraction failed: {e}")

    # 5. Deterministic scanning against town aliases & place resolution
    if page_texts:
        for text in page_texts:
            tokens = re.findall(r"[a-zA-Z\u0B80-\u0BFF]+", text)
            for tok in tokens:
                tok_lower = tok.lower()
                if tok_lower in TOWN_DISTRICT_ALIASES:
                    dist = TOWN_DISTRICT_ALIASES[tok_lower]
                    discovered_pairs.append((tok.title(), dist))

    # Cluster by (locality, district)
    cluster_counts: Dict[str, int] = {}
    pair_map: Dict[str, Tuple[str, str]] = {}
    for loc, dist in discovered_pairs:
        # Normalize: if loc is same as dist or empty, keep cleaner label
        key = f"{loc.lower()}|{dist.lower()}"
        cluster_counts[key] = cluster_counts.get(key, 0) + 1
        if key not in pair_map:
            pair_map[key] = (loc, dist)

    total_mentions = sum(cluster_counts.values()) or 1
    candidates = []
    opt_idx = 1
    for key, count in sorted(cluster_counts.items(), key=lambda x: x[1], reverse=True):
        loc, dist = pair_map[key]
        ratio = count / total_mentions
        loc_display = f"{loc}, " if loc.lower() != dist.lower() else ""
        cand = {
            "option_id": f"opt-{opt_idx}",
            "locality": loc,
            "district": dist,
            "label": f"{loc_display}{dist}",
            "support_ratio": round(ratio, 2),
            "cluster_count": count,
        }
        candidates.append(cand)
        opt_idx += 1

    return candidates[:MAX_CLARIFICATION_CANDIDATES]


def disambiguate_query(
    db: Session,
    session_id: str,
    asked_message_id: Optional[int] = None,
    entity_name: str = "",
    entity_name_ta: Optional[str] = None,
    question_type: str = "general_heritage",
    original_query: str = "",
) -> Dict[str, Any]:
    """Identity clarification entry point (Rule 2.2).

    Returns dict with keys:
      status: 'needs_clarification' | 'resolved_direct' | 'rejected_nonexistent'
      candidates: List[Dict]
      selected_candidate: Optional[Dict]
      clarification_id: Optional[str]
    """
    # 0. Check if there is already an open clarification for this session
    open_clar = get_open_clarification(db, session_id)
    if open_clar:
        return {
            "status": "needs_clarification",
            "clarification_id": str(open_clar.clarification_id),
            "candidates": open_clar.candidates,
        }

    # 1. Candidate discovery: search candidate_cache first (TTL 30 days)
    cache_key = _cache_key(entity_name)
    cached = _load_cached_candidates(db, cache_key)
    if cached:
        candidates = cached
    else:
        candidates = _discover_candidates_from_web(entity_name, db=db)
        if candidates:
            _store_candidates_cache(db, cache_key, candidates)

    # 2. Decision branching
    # Branch A: 0 candidates -> rejected_nonexistent
    if not candidates:
        return {
            "status": "rejected_nonexistent",
            "candidates": [],
        }

    # Branch B: 1 candidate, or one candidate supported by >= 70% of source clusters
    dominant_cand = next((c for c in candidates if c.get("support_ratio", 0.0) >= DOMINANT_CANDIDATE_RATIO), None)
    if len(candidates) == 1 or dominant_cand is not None:
        selected = dominant_cand or candidates[0]
        return {
            "status": "resolved_direct",
            "selected_candidate": selected,
            "candidates": [selected],
        }

    # Branch C: 2-4 candidates -> ask clarification in chat, status needs_clarification
    clar = create_clarification(
        db,
        session_id=session_id,
        asked_message_id=int(asked_message_id or 0),
        entity_name=entity_name,
        entity_name_ta=entity_name_ta,
        question_type=question_type,
        original_query=original_query,
        candidates=candidates[:MAX_CLARIFICATION_CANDIDATES],
    )
    return {
        "status": "needs_clarification",
        "clarification_id": str(clar.clarification_id),
        "candidates": candidates[:MAX_CLARIFICATION_CANDIDATES],
    }
