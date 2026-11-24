"""Fact consensus engine (IC³-WB: Identity-Checked, Copy-aware Consensus with Calibrated Write-Back).

Implements deterministic fact consensus:
1. Canonical normalization (years -> int CE, builders -> canonical king, deities -> canonical deity).
2. Domain prior weighting with extractor agreement calibration.
3. Copycat discount: collapsing duplicate claims within the same cluster_key.
4. Consensus decision rules (Verified, Conflict, Rejected) with full provenance trace.
"""

from dataclasses import dataclass, field
import logging
import re
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


# -----------------------------------------------------------------------------
# 1. Canonical Normalizers
# -----------------------------------------------------------------------------
CANONICAL_BUILDERS = {
    "rajaraja i": "Raja Raja Chola I",
    "rajaraja chola": "Raja Raja Chola I",
    "rajaraja chola i": "Raja Raja Chola I",
    "rajaraja cholan": "Raja Raja Chola I",
    "raja raja chola": "Raja Raja Chola I",
    "raja raja chola i": "Raja Raja Chola I",
    "king rajaraja": "Raja Raja Chola I",
    "rajendra i": "Rajendra Chola I",
    "rajendra chola": "Rajendra Chola I",
    "rajendra chola i": "Rajendra Chola I",
    "karikala": "Karikala Chola",
    "karikala chola": "Karikala Chola",
    "karikalan": "Karikala Chola",
    "narasimhavarman i": "Narasimhavarman I",
    "mahendravarman i": "Mahendravarman I",
    "tirumala nayaka": "Tirumala Nayaka",
    "thirumalai nayakkar": "Tirumala Nayaka",
    "thirumalai nayak": "Tirumala Nayaka",
    "marthanda varma": "Anizham Thirunal Marthanda Varma",
}

CANONICAL_DEITIES = {
    "shiva": "Shiva",
    "lord shiva": "Shiva",
    "siva": "Shiva",
    "lord siva": "Shiva",
    "brihadisvara": "Brihadeeswarar (Shiva)",
    "brihadeeswarar": "Brihadeeswarar (Shiva)",
    "peruvudaiyar": "Brihadeeswarar (Shiva)",
    "vishnu": "Vishnu",
    "lord vishnu": "Vishnu",
    "ranganatha": "Ranganatha (Vishnu)",
    "sri ranganatha": "Ranganatha (Vishnu)",
    "murugan": "Murugan",
    "lord murugan": "Murugan",
    "karthikeya": "Murugan",
    "subramanya": "Murugan",
    "amman": "Mariamman",
    "mariamman": "Mariamman",
    "bannari amman": "Bannari Amman",
    "pariyur amman": "Kondathu Kaliamman",
    "meenakshi": "Goddess Meenakshi",
    "meenakshi amman": "Goddess Meenakshi",
}


def normalize_year(raw: str) -> Optional[str]:
    """Normalize a year string to a standardized CE year string (e.g. '1010 CE')."""
    if not raw:
        return None
    r = raw.strip()

    # Look for 3-4 digit numbers
    m = re.search(r"\b(\d{3,4})\b", r)
    if m:
        year_num = int(m.group(1))
        # Filter plausible historical range (100 to 2026)
        if 100 <= year_num <= 2026:
            return f"{year_num} CE"

    # Match centuries (e.g. '11th century')
    cent_m = re.search(r"\b(\d{1,2})(?:st|nd|rd|th)?\s+century\b", r, re.IGNORECASE)
    if cent_m:
        c_num = int(cent_m.group(1))
        return f"{c_num}th century CE"

    return r.strip()


def normalize_builder(raw: str) -> str:
    """Normalize a king/builder name to canonical form."""
    if not raw:
        return ""
    clean = re.sub(r"[^\w\s]", "", raw.lower()).strip()
    clean = re.sub(r"\s+", " ", clean)

    for alias, canonical in CANONICAL_BUILDERS.items():
        if alias in clean or clean in alias:
            return canonical

    return raw.strip().title()


def normalize_deity(raw: str) -> str:
    """Normalize a Hindu deity or presiding entity name to canonical form."""
    if not raw:
        return ""
    clean = re.sub(r"[^\w\s]", "", raw.lower()).strip()
    clean = re.sub(r"\s+", " ", clean)

    for alias, canonical in CANONICAL_DEITIES.items():
        if alias in clean or clean in alias:
            return canonical

    return raw.strip().title()


CANONICAL_CATEGORIES = {
    "temple": "temple",
    "kovil": "temple",
    "amman temple": "temple",
    "hindu temple": "temple",
    "shiva temple": "temple",
    "vishnu temple": "temple",
    "fort": "fort",
    "kottai": "fort",
    "palace": "palace",
    "aranmanai": "palace",
    "museum": "museum",
    "monument": "monument",
    "cave": "cave",
    "rock cut": "rock_cut",
    "rock-cut": "rock_cut",
    "archaeological": "archaeological_site",
    "megalithic": "megalithic_site",
    "inscription": "inscription",
    "memorial": "memorial",
    "church": "church",
    "mosque": "mosque",
    "jain": "jain_site",
}


def normalize_category(raw: str) -> str:
    """Normalize a raw category string to standard DB site category."""
    if not raw:
        return "temple"
    clean = re.sub(r"[^\w\s]", "", raw.lower()).strip()
    for k, v in CANONICAL_CATEGORIES.items():
        if k in clean:
            return v
    return raw.strip().lower()


def normalize_fact_value(field: str, raw: str) -> str:
    """Normalize value according to field semantics."""
    if not raw:
        return ""
    f = field.lower().strip()
    if f in ("built_year", "year", "year_built"):
        norm_y = normalize_year(raw)
        return norm_y or raw.strip()
    elif f in ("builder", "patron", "king"):
        return normalize_builder(raw)
    elif f in ("deity", "main_deity", "presiding_deity"):
        return normalize_deity(raw)
    elif f in ("primary_category", "category"):
        return normalize_category(raw)
    elif f in ("is_unesco", "protected"):
        val_lower = raw.lower().strip()
        return "true" if any(w in val_lower for w in ("true", "yes", "1", "unesco")) else "false"

    return raw.strip()


# -----------------------------------------------------------------------------
# 2. Consensus Decision Data Structure
# -----------------------------------------------------------------------------
@dataclass
class FieldDecisionResult:
    field: str
    decision: str  # 'verified' | 'conflict' | 'rejected'
    value: Optional[str]
    support: float
    n_clusters: int
    has_tier_a: bool
    rule_fired: str
    competing: List[Dict[str, Any]] = field(default_factory=list)


# -----------------------------------------------------------------------------
# 3. IC³-WB Fact Consensus Evaluator
# -----------------------------------------------------------------------------
def evaluate_field_consensus(
    field_name: str,
    claims: List[Dict[str, Any]],
) -> FieldDecisionResult:
    """Evaluate consensus for a specific field across claims in the evidence ledger.

    Input claims list:
      list[{
        norm_value: str,
        raw_value: str,
        domain: str,
        tier: str ('A'|'B'|'C'),
        cluster_key: str,
        extractor_agreement: bool,
        domain_prior: float (e.g. 1.0 for A, 0.6 for B, 0.25 for C)
      }]

    Consensus logic:
    - Weight per claim = domain_prior * (1.0 if extractor_agreement else 0.7)
    - Copycat discount: within the same cluster_key and same norm_value, keep only max weight.
    - Sum discounted weights across distinct cluster_keys for each norm_value.

    Decision Rules:
    - Verified:
        Rule 1: (has_tier_a AND support >= 1.0) -> 'verified_tier_a_authoritative'
        Rule 2: (n_distinct_clusters >= 2 AND support >= 1.4) -> 'verified_multi_cluster_consensus'
    - Conflict:
        Top-2 competing norm_values both have support >= 0.8 -> 'conflict_competing_claims'
    - Rejected:
        Top support < 0.8 -> 'rejected_insufficient_support'
    """
    if not claims:
        return FieldDecisionResult(
            field=field_name,
            decision="rejected",
            value=None,
            support=0.0,
            n_clusters=0,
            has_tier_a=False,
            rule_fired="rejected_no_claims",
            competing=[],
        )

    # 1. Group claims by norm_value
    # value -> { cluster_key -> max_weight, has_tier_a, raw_values }
    value_data: Dict[str, Dict[str, Any]] = {}

    for c in claims:
        val = normalize_fact_value(field_name, c.get("norm_value") or c.get("raw_value") or "")
        if not val:
            continue

        tier = c.get("tier", "C")
        prior = float(c.get("domain_prior") or (1.0 if tier == "A" else (0.6 if tier == "B" else 0.25)))
        agreement = bool(c.get("extractor_agreement", True))

        # Weight calculation with extractor agreement calibration
        claim_weight = prior * (1.0 if agreement else 0.7)
        cluster = c.get("cluster_key") or c.get("domain") or "default_cluster"

        if val not in value_data:
            value_data[val] = {
                "clusters": {},
                "has_tier_a": False,
                "raw_values": set(),
            }

        if tier == "A":
            value_data[val]["has_tier_a"] = True

        if c.get("raw_value"):
            value_data[val]["raw_values"].add(c["raw_value"])

        # Copycat discount: take maximum weight within the same cluster
        curr_cluster_wt = value_data[val]["clusters"].get(cluster, 0.0)
        value_data[val]["clusters"][cluster] = max(curr_cluster_wt, claim_weight)

    if not value_data:
        return FieldDecisionResult(
            field=field_name,
            decision="rejected",
            value=None,
            support=0.0,
            n_clusters=0,
            has_tier_a=False,
            rule_fired="rejected_empty_normalized_values",
            competing=[],
        )

    # 2. Compute total support for each candidate value
    scored_values: List[Dict[str, Any]] = []
    for val, data in value_data.items():
        total_support = round(sum(data["clusters"].values()), 4)
        n_clusters = len(data["clusters"])
        has_tier_a = data["has_tier_a"]
        scored_values.append({
            "value": val,
            "support": total_support,
            "n_clusters": n_clusters,
            "has_tier_a": has_tier_a,
            "raw_values": list(data["raw_values"]),
        })

    # Sort descending by support
    scored_values.sort(key=lambda x: (x["support"], x["has_tier_a"], x["n_clusters"]), reverse=True)

    top_val = scored_values[0]
    competing_entries = [
        {"value": sv["value"], "support": sv["support"], "n_clusters": sv["n_clusters"]}
        for sv in scored_values[1:]
    ]

    # 3. Apply Decision Rules
    # Rule 3: Conflict Check (if top-2 competing both have support >= 0.8 for atomic fields)
    if field_name not in ("overview", "history") and len(scored_values) > 1:
        second_val = scored_values[1]
        if top_val["support"] >= 0.8 and second_val["support"] >= 0.8:
            return FieldDecisionResult(
                field=field_name,
                decision="conflict",
                value=top_val["value"],
                support=top_val["support"],
                n_clusters=top_val["n_clusters"],
                has_tier_a=top_val["has_tier_a"],
                rule_fired="conflict_competing_high_support",
                competing=competing_entries,
            )

    # Rule 1: Tier A verified (official source + support >= 1.0)
    if top_val["has_tier_a"] and top_val["support"] >= 1.0:
        return FieldDecisionResult(
            field=field_name,
            decision="verified",
            value=top_val["value"],
            support=top_val["support"],
            n_clusters=top_val["n_clusters"],
            has_tier_a=True,
            rule_fired="verified_tier_a_authoritative",
            competing=competing_entries,
        )

    # Rule 2: Multi-cluster consensus verified (>= 2 independent clusters + support >= 1.2)
    if top_val["n_clusters"] >= 2 and top_val["support"] >= 1.2:
        return FieldDecisionResult(
            field=field_name,
            decision="verified",
            value=top_val["value"],
            support=top_val["support"],
            n_clusters=top_val["n_clusters"],
            has_tier_a=top_val["has_tier_a"],
            rule_fired="verified_multi_cluster_consensus",
            competing=competing_entries,
        )

    # Rule 4: Insufficient support -> Rejected
    return FieldDecisionResult(
        field=field_name,
        decision="rejected",
        value=top_val["value"] if top_val["support"] >= 0.4 else None,
        support=top_val["support"],
        n_clusters=top_val["n_clusters"],
        has_tier_a=top_val["has_tier_a"],
        rule_fired="rejected_insufficient_support",
        competing=competing_entries,
    )
