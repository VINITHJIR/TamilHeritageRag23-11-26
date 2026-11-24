"""Centralized specification configuration and thresholds for TamilHeritage-GPT.

All thresholds and DecisionCodes live in this file, each with a comment naming its rule.
As per general rules:
- Gemini only classifies, extracts claims with evidence quotes, and writes answer text.
- Every accept/reject/store decision is deterministic Python.
- All thresholds live in ONE file: server/app/services/spec_config.py.
"""

from enum import Enum
from typing import Set


class DecisionCode(str, Enum):
    """Controlled decision codes for augment_jobs and query_logs path_detail."""
    OUT_OF_SCOPE = "OUT_OF_SCOPE"  # Rule 1.2: Query is non-heritage
    NEEDS_CLARIFICATION = "NEEDS_CLARIFICATION"  # Rule 2.2: Ambiguous entity needing location clarification
    NEEDS_CHOICE = "NEEDS_CHOICE"  # Rule 4.2: Conflicting accounts on asked field needing user selection
    EXISTENCE_NOT_CONFIRMED = "EXISTENCE_NOT_CONFIRMED"  # Rule 3.2: Entity existence not confirmed by sources
    PROVISIONAL_PARTIAL_EVIDENCE = "PROVISIONAL_PARTIAL_EVIDENCE"  # Rule 4.1: Partial evidence unconfirmed
    VERIFIED_NEW = "VERIFIED_NEW"  # Rule 3.5: Newly verified site written to DB
    VERIFIED_REUSED = "VERIFIED_REUSED"  # Rule 2.1: Reused verified site within 24h
    QUOTA_EXHAUSTED = "QUOTA_EXHAUSTED"  # Rule 2.1: Gemini API rate limit / budget limit reached
    EXCEPTION = "EXCEPTION"  # Real unexpected pipeline error


# ALLOWED STATUS VALUES for augment_jobs.status and query_logs.path_detail.status
ALLOWED_STATUSES: Set[str] = {
    "verified",
    "provisional",
    "needs_clarification",
    "needs_choice",
    "out_of_scope",
    "rejected_nonexistent",
    "quota_exhausted",
    "failed",  # 'failed' ONLY for real exceptions
}

# ALLOWED query_logs.path values
ALLOWED_PATHS: Set[str] = {
    "greeting",
    "out_of_scope",
    "db",
    "web_consensus",
    "web_provisional",
    "needs_clarification",
    "needs_choice",
    "rejected",
}

# --- Thresholds ---

# Rule 1.1: Gemini classification timeout (8 s)
GEMINI_CLASSIFY_TIMEOUT_SEC: float = 8.0

# Rule 0c: Rapid duplicate post suppression interval (seconds)
RAPID_DOUBLE_POST_SECONDS: int = 3

# Rule 1.1: Standard Gemini LLM call timeout (seconds)
GEMINI_TIMEOUT_SEC: float = 30.0

# Rule 2.1: Gemini daily call budget limit
GEMINI_DAILY_BUDGET: int = 1000

# Rule 3.2 / 5: Trigram similarity threshold on site_aliases for detect_site
SITE_DETECT_SIM_THRESHOLD: float = 0.40

# Rule 5: Dense vector retrieval top_k limit
RETRIEVAL_TOP_K: int = 8

# Rule 5: Fallback to unfiltered search if filtered search top hit is below this score
UNFILTERED_FALLBACK_SCORE: float = 0.40

# Rule 5: pg_trgm similarity threshold for entity identity verification
IDENTITY_SIM_THRESHOLD: float = 0.55

# Rule 5: Fallback word_similarity threshold for entity identity verification
IDENTITY_WORD_SIM_THRESHOLD: float = 0.70

# Rule 6: Dense vector score cutoff below which augmentation is triggered
RETRIEVAL_SCORE_THRESHOLD: float = 0.45

# Rule 6j: Threshold to include second chunk in direct DB answer
SECOND_CHUNK_SCORE_THRESHOLD: float = 0.45

# Rule 2.2 / 4.3: Clarification and choice pending expiry timeout (minutes)
CLARIFICATION_EXPIRY_MINUTES: int = 30

# Rule 2.2: difflib SequenceMatcher similarity score threshold (0-100) for clarification option text
CLARIFICATION_MATCH_THRESHOLD: int = 85

# Rule 2.2: Candidate cache TTL in days
CANDIDATE_CACHE_TTL_DAYS: int = 30

# Rule 2.2: Maximum candidates to show in identity clarification
MAX_CLARIFICATION_CANDIDATES: int = 4

# Rule 2.2: Dominant candidate threshold (if one candidate supported by >= 70% of clusters, resolve direct)
DOMINANT_CANDIDATE_RATIO: float = 0.70

# Rule 3.2: Minimum Tier-A clusters mentioning entity+district for existence acceptance
EXISTENCE_MIN_TIER_A_CLUSTERS: int = 1

# Rule 3.2: Minimum independent clusters mentioning entity+district for existence acceptance (Tier B/C)
EXISTENCE_MIN_INDEPENDENT_CLUSTERS: int = 3

# Rule 3.3: Base domain reliability prior for Tier A (official / governmental)
TIER_A_PRIOR: float = 1.0

# Rule 3.3: Base domain reliability prior for Tier B (encyclopedic / academic / major news)
TIER_B_PRIOR: float = 0.6

# Rule 3.3: Base domain reliability prior for Tier C (general blogs / travel sites)
TIER_C_PRIOR: float = 0.25

# Rule 3.3: Minimum support_ratio(v) required for a field value to be VERIFIED
CONSENSUS_SUPPORT_RATIO_THRESHOLD: float = 0.80

# Rule 3.3: Minimum weight_sum(v) required for a field value to be VERIFIED
CONSENSUS_WEIGHT_SUM_THRESHOLD: float = 1.20

# Rule 3.3: Minimum support_ratio(v) for competing values to trigger CONFLICT
CONFLICT_SUPPORT_RATIO_MIN: float = 0.20

# Rule 3.3: Numeric tolerance ratio (±5%) for numeric conflict merging
NUMERIC_TOLERANCE_RATIO: float = 0.05

# Rule 3.4: Extracted claim fields subject to consensus (overview and history are NOT consensus fields)
CONSENSUS_FIELDS: list[str] = [
    "name_ta",
    "primary_category",
    "deity",
    "builder",
    "dynasty",
    "period",
    "built_year",
    "festivals",
    "architecture_style",
    "timings",
    "locality",
]

# Rule 3.7: Optional fields for sites.completeness calculation
OPTIONAL_FIELDS: list[str] = [
    "deity",
    "builder",
    "dynasty",
    "period",
    "built_year",
    "festivals",
    "architecture_style",
    "timings",
    "locality",
    "name_ta",
]

# Rule 3.7: sites.completeness thresholds (existence_only <= 1, partial 2-3, complete >= 4)
COMPLETENESS_EXISTENCE_ONLY_MAX: int = 1
COMPLETENESS_PARTIAL_MAX: int = 3
COMPLETENESS_COMPLETE_MIN: int = 4

# Rule 3.8: Maximum quote-anchored summary sentences per page for chunks
MAX_SUMMARY_SENTENCES_PER_PAGE: int = 3

# Rule 4.2: Maximum options to present in value choice prompt
MAX_VALUE_CHOICES: int = 4

# Rule 6c: Multi-source web search URL budget (maximum candidate URLs fetched)
MAX_WEB_URLS: int = 8

# Rule 6d: Web page fetch timeout per URL (seconds)
WEB_FETCH_TIMEOUT_SEC: float = 7.0

# Rule 6d: Minimum cleaned text length for web page to be valid
MIN_PAGE_TEXT_LENGTH: int = 50

# Rule 6d: Maximum excerpt character length stored in augment_sources
MAX_EXCERPT_LENGTH: int = 500

# Rule 6d: MinHash Jaccard similarity threshold for copy-aware clustering
MINHASH_JACCARD_THRESHOLD: float = 0.70

# Rule 6d: Top relevant pages selected for claim extraction
TOP_PAGES_TO_EXTRACT: int = 5
