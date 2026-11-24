# Clarification resolution module for CLARIFY‑THEN‑AUGMENT flow

"""Utilities to handle the user reply when a clarification is pending.

The chat pipeline will call `process_clarification_reply` with the raw
user message (or a JSON payload from a chip click) and the current DB
session. It will:

1. Retrieve the open clarification for the session.
2. Try to match the reply:
   * If the payload contains `option_id` → direct match.
   * Otherwise perform a fuzzy match against the `candidates` list using
     difflib's `SequenceMatcher` (simple and available in the stdlib).
   * If a location not in the options is identified, attempt to resolve
     it via the existing LGD utilities (not implemented here – placeholder).
3. Update the clarification status (`resolved` or `cancelled`).
4. Return a dict indicating the outcome and the selected candidate (if any).
"""

import logging
from datetime import datetime
from difflib import SequenceMatcher
from typing import Dict, Optional

from sqlalchemy.orm import Session

from app.models.clarification import Clarification
from app.services.clarifications import (
    get_open_clarification,
    resolve_clarification,
    cancel_clarification,
)

logger = logging.getLogger(__name__)

# Simple fuzzy similarity helper – returns a score 0..100
def _similarity(a: str, b: str) -> int:
    return int(SequenceMatcher(None, a.lower(), b.lower()).ratio() * 100)


def _match_option_text(reply: str, candidates: list) -> Optional[Dict]:
    """Return the candidate dict with the highest similarity ≥85, or None.

    Each candidate is expected to contain at least a ``locality`` and
    ``district`` field (or a combined ``label``). The function builds a
    simple display string for matching.
    """
    best = None
    best_score = 0
    for cand in candidates:
        # Build a human readable label – fallback to full_name if present
        label = cand.get("label") or f"{cand.get('locality', '')} {cand.get('district', '')}".strip()
        score = _similarity(reply, label)
        if score > best_score:
            best_score = score
            best = cand
    return best if best_score >= 85 else None


def process_clarification_reply(
    db: Session,
    session_id: str,
    reply_payload: Dict,
) -> Dict:
    """Handle a user's answer to a clarification request.

    ``reply_payload`` may be:
        * ``{"option_id": "<uuid>"}`` – from a chip click.
        * ``{"text": "..."}`` – free‑text answer.
    Returns a dict with keys:
        * ``status`` – ``resolved``, ``cancelled`` or ``unresolved``.
        * ``selected_candidate`` – the candidate dict if resolved.
        * ``message`` – human‑readable description.
    """
    clar = get_open_clarification(db, session_id)
    if not clar:
        return {"status": "unresolved", "message": "No open clarification for this session."}

    # Direct chip click handling
    if "option_id" in reply_payload:
        opt_id = reply_payload["option_id"]
        # Find matching candidate by its internal ``option_id`` field
        selected = next((c for c in clar.candidates if str(c.get("option_id")) == str(opt_id)), None)
        if selected:
            resolve_clarification(db, str(clar.clarification_id), selected)
            return {"status": "resolved", "selected_candidate": selected, "message": "Clarification resolved via chip selection."}
        else:
            # Unknown option – treat as cancelled
            cancel_clarification(db, str(clar.clarification_id))
            return {"status": "cancelled", "message": "Invalid option selected; clarification cancelled."}

    # Free‑text handling
    text = reply_payload.get("text", "").strip()
    if not text:
        return {"status": "unresolved", "message": "Empty reply received."}

    # Attempt to match against existing candidates
    matched = _match_option_text(text, clar.candidates)
    if matched:
        resolve_clarification(db, str(clar.clarification_id), matched)
        return {"status": "resolved", "selected_candidate": matched, "message": f"Matched user reply to candidate '{matched.get('label', text)}'."}

    # If not matched, we could attempt an external location resolve (placeholder)
    # For now, we cancel the clarification and let the normal pipeline handle the query.
    cancel_clarification(db, str(clar.clarification_id))
    return {"status": "cancelled", "message": "User reply did not match any candidate; clarification cancelled."}
