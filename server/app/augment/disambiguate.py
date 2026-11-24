# Disambiguation module for clarification flow

import logging
from typing import List, Dict
from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from app.services.clarifications import create_clarification, get_open_clarification
from app.models.candidate_cache import CandidateCache
from app.services.gemini import extract_claims  # assume utility to parse page content into candidates
from app.services.augment_orchestrator import default_web_search, fetch_page_content

logger = logging.getLogger(__name__)

CACHE_TTL_DAYS = 30
MAX_CANDIDATES = 8

def _cache_key(query: str) -> str:
    return f"disamb:{query.lower().strip()}"

def _load_cached_candidates(db: Session, key: str) -> List[Dict]:
    entry = db.query(CandidateCache).filter(CandidateCache.candidate_key == key).first()
    if entry and entry.fetched_at >= datetime.utcnow() - timedelta(days=entry.ttl_days):
        return entry.candidates
    return []

def _store_candidates_cache(db: Session, key: str, candidates: List[Dict]):
    existing = db.query(CandidateCache).filter(CandidateCache.candidate_key == key).first()
    if existing:
        existing.candidates = candidates
        existing.fetched_at = datetime.utcnow()
    else:
        cache = CandidateCache(candidate_key=key, candidates=candidates)
        db.add(cache)
    db.commit()

def disambiguate_query(
    db: Session,
    session_id: str,
    asked_message_id: str,
    entity_name: str,
    entity_name_ta: str | None,
    question_type: str,
    original_query: str,
) -> Dict:
    """Main entry point used by the chat pipeline.

    Returns a dict with keys:
        - status: one of 'needs_clarification', 'resolved_direct', 'rejected_nonexistent'
        - candidates: list of candidate dicts (when needed)
        - clarification_id: if created
    """
    # Fast path: check if there is already an open clarification for this session
    open_clar = get_open_clarification(db, session_id)
    if open_clar:
        # Let the pipeline handle the existing clarification (it will be presented to user)
        return {"status": "needs_clarification", "clarification_id": str(open_clar.clarification_id), "candidates": open_clar.candidates}

    # Determine ambiguity: simple heuristic – if place is missing and trigram match >=2
    # For brevity we assume any query without a district name is ambiguous.
    ambiguous = True  # placeholder; replace with real check using DB trigram similarity.

    if not ambiguous:
        return {"status": "resolved_direct", "candidates": []}

    # Candidate discovery
    cache_key = _cache_key(original_query)
    cached = _load_cached_candidates(db, cache_key)
    if cached:
        candidates = cached
    else:
        # Web search (reuse existing default_web_search)
        urls = default_web_search(original_query, max_results=12)
        candidates = []
        for url in urls[:12]:
            page = fetch_page_content(url)
            if not page:
                continue
            # Use Gemini to extract candidate info – here we mock with a simple dict
            # In production call `extract_claims` or a specialized schema.
            try:
                claims = extract_claims(page[0])  # assume returns list of dicts with needed fields
                for c in claims:
                    candidates.append(c)
            except Exception as e:
                logger.debug(f"Gemini extraction failed for {url}: {e}")
        # Trim & rank (placeholder: keep first MAX_CANDIDATES)
        candidates = candidates[:MAX_CANDIDATES]
        _store_candidates_cache(db, cache_key, candidates)

    if not candidates:
        return {"status": "rejected_nonexistent", "candidates": []}

    if len(candidates) == 1:
        # Directly resolve
        clar = create_clarification(
            db,
            session_id=session_id,
            asked_message_id=asked_message_id,
            entity_name=entity_name,
            entity_name_ta=entity_name_ta,
            question_type=question_type,
            original_query=original_query,
            candidates=candidates,
        )
        return {"status": "resolved_direct", "clarification_id": str(clar.clarification_id), "candidates": candidates}

    # Multiple candidates → create clarification entry
    clar = create_clarification(
        db,
        session_id=session_id,
        asked_message_id=asked_message_id,
        entity_name=entity_name,
        entity_name_ta=entity_name_ta,
        question_type=question_type,
        original_query=original_query,
        candidates=candidates,
    )
    return {"status": "needs_clarification", "clarification_id": str(clar.clarification_id), "candidates": candidates}
