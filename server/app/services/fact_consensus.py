"""Fact consensus engine (IC³-WB: Identity-Checked, Copy-aware Consensus with Calibrated Write-Back).

Implements deterministic fact consensus per Rule 3.3:
1. Canonical normalization (years/centuries, builders, dynasties, deities, categories).
2. Pre-consensus conflict merging (year inside century/range, alias equality, drop vague, numeric ±5%).
3. Copy-aware cluster weights: w(cluster) = max(tier_prior * domain_reliability) of its pages.
4. Support ratio & weight sum computation.
5. Strict deterministic decision rules: VERIFIED, CONFLICT, UNCONFIRMED, REJECTED.
"""

from dataclasses import dataclass, field
import logging
import re
from typing import Any, Dict, List, Optional, Set, Tuple

from app.services.spec_config import (
    TIER_A_PRIOR,
    TIER_B_PRIOR,
    TIER_C_PRIOR,
    CONSENSUS_SUPPORT_RATIO_THRESHOLD,
    CONSENSUS_WEIGHT_SUM_THRESHOLD,
    CONFLICT_SUPPORT_RATIO_MIN,
    NUMERIC_TOLERANCE_RATIO,
)

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
    "ராஜராஜ சோழன்": "Raja Raja Chola I",
    "ராசராசன்": "Raja Raja Chola I",
    "rajendra i": "Rajendra Chola I",
    "rajendra chola": "Rajendra Chola I",
    "rajendra chola i": "Rajendra Chola I",
    "ராஜேந்திர சோழன்": "Rajendra Chola I",
    "kulothunga": "Kulothunga Chola I",
    "kulothunga chola": "Kulothunga Chola I",
    "குலோத்துங்க சோழன்": "Kulothunga Chola I",
    "karikala": "Karikala Chola",
    "karikala chola": "Karikala Chola",
    "karikalan": "Karikala Chola",
    "கரிகாலன்": "Karikala Chola",
    "narasimhavarman i": "Narasimhavarman I",
    "narasimhavarman": "Narasimhavarman I",
    "நரசிம்மவர்மன்": "Narasimhavarman I",
    "mahendravarman i": "Mahendravarman I",
    "mahendravarman": "Mahendravarman I",
    "மகேந்திரவர்மன்": "Mahendravarman I",
    "tirumala nayaka": "Tirumala Nayaka",
    "thirumalai nayakkar": "Tirumala Nayaka",
    "thirumalai nayak": "Tirumala Nayaka",
    "திருமலை நாயக்கர்": "Tirumala Nayaka",
    "marthanda varma": "Anizham Thirunal Marthanda Varma",
}

CANONICAL_DYNASTIES = {
    "chola": "Chola",
    "cholas": "Chola",
    "medieval chola": "Chola",
    "early chola": "Chola",
    "later chola": "Chola",
    "சோழர்": "Chola",
    "சோழர்கள்": "Chola",
    "pandya": "Pandya",
    "pandyas": "Pandya",
    "early pandya": "Pandya",
    "later pandya": "Pandya",
    "பாண்டியர்": "Pandya",
    "பாண்டியர்கள்": "Pandya",
    "pallava": "Pallava",
    "pallavas": "Pallava",
    "பல்லவர்": "Pallava",
    "பல்லவர்கள்": "Pallava",
    "chera": "Chera",
    "cheras": "Chera",
    "சேரர்": "Chera",
    "சேரர்கள்": "Chera",
    "nayak": "Nayaka",
    "nayaka": "Nayaka",
    "nayakas": "Nayaka",
    "madurai nayak": "Nayaka",
    "தஞ்சை நாயக்கர்": "Nayaka",
    "நாயக்கர்": "Nayaka",
    "vijayanagara": "Vijayanagara",
    "vijayanagar": "Vijayanagara",
    "விஜயநகர": "Vijayanagara",
}

CANONICAL_DEITIES = {
    "shiva": "Shiva",
    "lord shiva": "Shiva",
    "siva": "Shiva",
    "lord siva": "Shiva",
    "சிவன்": "Shiva",
    "ஈஸ்வரன்": "Shiva",
    "brihadisvara": "Brihadeeswarar (Shiva)",
    "brihadeeswarar": "Brihadeeswarar (Shiva)",
    "peruvudaiyar": "Brihadeeswarar (Shiva)",
    "பெருவுடையார்": "Brihadeeswarar (Shiva)",
    "பிரகதீஸ்வரர்": "Brihadeeswarar (Shiva)",
    "vishnu": "Vishnu",
    "lord vishnu": "Vishnu",
    "விஷ்ணு": "Vishnu",
    "பெருமாள்": "Vishnu",
    "ranganatha": "Ranganatha (Vishnu)",
    "sri ranganatha": "Ranganatha (Vishnu)",
    "ரங்கநாதர்": "Ranganatha (Vishnu)",
    "murugan": "Murugan",
    "lord murugan": "Murugan",
    "karthikeya": "Murugan",
    "subramanya": "Murugan",
    "முருகன்": "Murugan",
    "amman": "Mariamman",
    "mariamman": "Mariamman",
    "மாரியம்மன்": "Mariamman",
    "அம்மன்": "Mariamman",
    "bannari amman": "Bannari Amman",
    "பண்ணாரி அம்மன்": "Bannari Amman",
    "masani amman": "Masani Amman",
    "மாசாணி அம்மன்": "Masani Amman",
    "pariyur amman": "Kondathu Kaliamman",
    "kondathu kaliamman": "Kondathu Kaliamman",
    "meenakshi": "Goddess Meenakshi",
    "meenakshi amman": "Goddess Meenakshi",
    "மீனாட்சி": "Goddess Meenakshi",
}

CANONICAL_CATEGORIES = {
    "temple": "temple",
    "kovil": "temple",
    "koyil": "temple",
    "கோவில்": "temple",
    "கோயில்": "temple",
    "amman temple": "temple",
    "hindu temple": "temple",
    "shiva temple": "temple",
    "vishnu temple": "temple",
    "fort": "fort",
    "kottai": "fort",
    "கோட்டை": "fort",
    "palace": "palace",
    "aranmanai": "palace",
    "அரண்மனை": "palace",
    "museum": "museum",
    "அருங்காட்சியகம்": "museum",
    "monument": "monument",
    "நினைவுச்சின்னம்": "monument",
    "cave": "cave",
    "rock cut": "rock_cut",
    "rock-cut": "rock_cut",
    "குடைவரை": "rock_cut",
    "archaeological": "archaeological_site",
    "தொல்பொருள்": "archaeological_site",
    "megalithic": "megalithic_site",
    "inscription": "inscription",
    "கல்வெட்டு": "inscription",
    "memorial": "memorial",
    "church": "church",
    "mosque": "mosque",
    "jain": "jain_site",
}


def normalize_year(raw: str) -> Optional[str]:
    """Normalize a year or century string to standardized form (e.g. '1010 CE', '11th century CE')."""
    if not raw:
        return None
    r = raw.strip()

    # Match Tamil century forms (e.g. '11-ஆம் நூற்றாண்டு', '11வது நூற்றாண்டு')
    ta_cent_m = re.search(r"(\d{1,2})\s*(?:-?ஆம்|வது)?\s*நூற்றாண்டு", r)
    if ta_cent_m:
        c_num = int(ta_cent_m.group(1))
        return f"{c_num}th century CE"

    # Match centuries (e.g. '11th century', '11th century CE')
    cent_m = re.search(r"\b(\d{1,2})(?:st|nd|rd|th)?\s+century(?:\s+CE|\s+AD)?\b", r, re.IGNORECASE)
    if cent_m:
        c_num = int(cent_m.group(1))
        return f"{c_num}th century CE"

    # Match century ranges (e.g. '9th-10th century CE')
    cent_range_m = re.search(r"\b(\d{1,2})(?:st|nd|rd|th)?\s*[-–to]+\s*(\d{1,2})(?:st|nd|rd|th)?\s+century\b", r, re.IGNORECASE)
    if cent_range_m:
        c1, c2 = int(cent_range_m.group(1)), int(cent_range_m.group(2))
        return f"{c1}th-{c2}th century CE"

    # Look for 3-4 digit numbers (e.g. 1010, 1010 CE, 1010 AD)
    m = re.search(r"\b(\d{3,4})\b", r)
    if m:
        year_num = int(m.group(1))
        if 100 <= year_num <= 2026:
            return f"{year_num} CE"

    return r.strip()


def normalize_builder(raw: str) -> str:
    """Normalize a king/builder name to canonical form."""
    if not raw:
        return ""
    clean = raw.lower().strip()
    for alias, canonical in CANONICAL_BUILDERS.items():
        if alias in clean or clean in alias:
            return canonical
    return raw.strip().title()


def normalize_dynasty(raw: str) -> str:
    """Normalize a dynasty name to canonical form."""
    if not raw:
        return ""
    clean = raw.lower().strip()
    for alias, canonical in CANONICAL_DYNASTIES.items():
        if alias in clean or clean in alias:
            return canonical
    return raw.strip().title()


def normalize_deity(raw: str) -> str:
    """Normalize a Hindu deity or presiding entity name to canonical form."""
    if not raw:
        return ""
    clean = raw.lower().strip()
    for alias, canonical in CANONICAL_DEITIES.items():
        if alias in clean or clean in alias:
            return canonical
    return raw.strip().title()


def normalize_category(raw: str) -> str:
    """Normalize a raw category string to standard DB site category."""
    if not raw:
        return "temple"
    clean = raw.lower().strip()
    for k, v in CANONICAL_CATEGORIES.items():
        if k in clean:
            return v
    return raw.strip().lower()


def normalize_fact_value(field: str, raw: str) -> str:
    """Normalize value according to field semantics."""
    if not raw:
        return ""
    f = field.lower().strip()
    if f in ("built_year", "year", "year_built", "period"):
        norm_y = normalize_year(raw)
        return norm_y or raw.strip()
    elif f in ("builder", "patron", "king"):
        return normalize_builder(raw)
    elif f in ("dynasty",):
        return normalize_dynasty(raw)
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
    decision: str  # 'verified' | 'conflict' | 'unconfirmed' | 'rejected'
    value: Optional[str]
    support: float
    n_clusters: int
    has_tier_a: bool
    rule_fired: str
    competing: List[Dict[str, Any]] = field(default_factory=list)
    support_ratio: float = 0.0


# -----------------------------------------------------------------------------
# 3. Pre-Consensus Conflict Merging Rules (Rule 3.3)
# -----------------------------------------------------------------------------
VAGUE_FACT_VALUES: Set[str] = {
    "ancient", "very old", "historical", "traditional", "centuries old",
    "unknown", "old", "antiquity", "ancient times", "very ancient",
}


def _extract_year_int(val_str: str) -> Optional[int]:
    """Extract integer year from string like '1010 CE'."""
    m = re.search(r"\b(\d{3,4})\s*(?:CE|AD)?\b", val_str)
    return int(m.group(1)) if m else None


def _extract_century_int(val_str: str) -> Optional[int]:
    """Extract integer century from string like '11th century CE'."""
    m = re.search(r"\b(\d{1,2})(?:st|nd|rd|th)?\s+century\b", val_str, re.IGNORECASE)
    return int(m.group(1)) if m else None


def _extract_century_range(val_str: str) -> Optional[Tuple[int, int]]:
    """Extract integer century range from string like '9th-10th century CE'."""
    m = re.search(r"\b(\d{1,2})(?:st|nd|rd|th)?\s*[-–to]+\s*(\d{1,2})(?:st|nd|rd|th)?\s+century\b", val_str, re.IGNORECASE)
    return (int(m.group(1)), int(m.group(2))) if m else None


def _year_in_century(year: int, century: int) -> bool:
    """True if year falls in century (e.g. 1010 is in 11th century: 1001..1100)."""
    return (century - 1) * 100 < year <= century * 100


def _year_in_century_range(year: int, c_start: int, c_end: int) -> bool:
    """True if year falls in century range."""
    return (c_start - 1) * 100 < year <= c_end * 100


def _merge_cluster_dicts(target: Dict[str, float], source: Dict[str, float]) -> None:
    """Combine two cluster weight dictionaries taking the maximum weight per cluster."""
    for clk, wt in source.items():
        target[clk] = max(target.get(clk, 0.0), wt)


def apply_conflict_merging_rules(
    value_data: Dict[str, Dict[str, Any]],
    field_name: str,
) -> Dict[str, Dict[str, Any]]:
    """Apply conflict-merging rules before deciding:
    1. Drop vague values ('ancient') when a specific one exists.
    2. A year inside a century/range -> merge into specific year (keep most specific).
    3. Numeric ±5% tolerance merge.
    """
    if len(value_data) <= 1:
        return value_data

    # Rule A: Drop vague values when a specific one exists
    specific_values = [v for v in value_data.keys() if v.lower() not in VAGUE_FACT_VALUES]
    if specific_values and len(specific_values) < len(value_data):
        vague_keys = [v for v in value_data.keys() if v.lower() in VAGUE_FACT_VALUES]
        for vk in vague_keys:
            logger.info(f"Dropping vague value '{vk}' for field '{field_name}' in favor of specific claims.")
            del value_data[vk]

    if len(value_data) <= 1:
        return value_data

    # Rule B: Year inside a century/range -> same value (keep the most specific)
    # Check if we have year values and century values
    year_keys = [k for k in list(value_data.keys()) if _extract_year_int(k) is not None and _extract_century_int(k) is None]
    century_keys = [k for k in list(value_data.keys()) if _extract_century_int(k) is not None or _extract_century_range(k) is not None]

    for yk in year_keys:
        y_int = _extract_year_int(yk)
        if y_int is None:
            continue
        for ck in century_keys:
            if ck not in value_data:
                continue
            c_int = _extract_century_int(ck)
            c_range = _extract_century_range(ck)
            is_inside = False
            if c_int and _year_in_century(y_int, c_int):
                is_inside = True
            elif c_range and _year_in_century_range(y_int, c_range[0], c_range[1]):
                is_inside = True

            if is_inside:
                logger.info(f"Merging century claim '{ck}' into specific year '{yk}' for field '{field_name}'.")
                # Merge clusters and data from ck into yk
                _merge_cluster_dicts(value_data[yk]["clusters"], value_data[ck]["clusters"])
                value_data[yk]["has_tier_a"] = value_data[yk]["has_tier_a"] or value_data[ck]["has_tier_a"]
                value_data[yk]["has_quote_verified"] = value_data[yk]["has_quote_verified"] or value_data[ck]["has_quote_verified"]
                value_data[yk]["raw_values"].update(value_data[ck]["raw_values"])
                del value_data[ck]

    if len(value_data) <= 1:
        return value_data

    # Rule C: Numeric ±5% tolerance merge
    num_entries = []
    for k in list(value_data.keys()):
        # Try to parse pure number or year
        m = re.search(r"^\s*(\d+(?:\.\d+)?)\s*(?:CE|AD)?\s*$", k, re.IGNORECASE)
        if m:
            num_entries.append((k, float(m.group(1))))

    if len(num_entries) >= 2:
        num_entries.sort(key=lambda x: sum(value_data[x[0]]["clusters"].values()), reverse=True)
        primary_k, primary_val = num_entries[0]
        for sec_k, sec_val in num_entries[1:]:
            if sec_k in value_data and primary_k in value_data:
                denom = max(primary_val, sec_val)
                diff_ratio = abs(primary_val - sec_val) / denom if denom > 0 else 1.0
                if diff_ratio <= NUMERIC_TOLERANCE_RATIO:
                    logger.info(f"Merging numeric value '{sec_k}' into '{primary_k}' within 5% tolerance ({diff_ratio:.3f}).")
                    _merge_cluster_dicts(value_data[primary_k]["clusters"], value_data[sec_k]["clusters"])
                    value_data[primary_k]["has_tier_a"] = value_data[primary_k]["has_tier_a"] or value_data[sec_k]["has_tier_a"]
                    value_data[primary_k]["has_quote_verified"] = value_data[primary_k]["has_quote_verified"] or value_data[sec_k]["has_quote_verified"]
                    value_data[primary_k]["raw_values"].update(value_data[sec_k]["raw_values"])
                    del value_data[sec_k]

    return value_data


# -----------------------------------------------------------------------------
# 4. Consensus Evaluator
# -----------------------------------------------------------------------------
def evaluate_field_consensus(
    field_name: str,
    claims: List[Dict[str, Any]],
) -> FieldDecisionResult:
    """Evaluate field consensus over copy-aware clusters per Rule 3.3.

    w(cluster) = max(tier_prior * domain_reliability) of its pages.
    support_ratio(v) = Σ w(clusters asserting v) / Σ w(clusters asserting any value)
    weight_sum(v)    = Σ w(clusters asserting v)

    Outcomes:
    - VERIFIED:
        (a tier-A cluster asserts v and no tier-A asserts another value)
        OR (support_ratio(v) >= 0.80 AND weight_sum(v) >= 1.2)
    - CONFLICT:
        if not verified and (two tier-A disagree OR 2 or more values each have support_ratio >= 0.20)
    - UNCONFIRMED:
        if not verified/conflict but exactly one value exists with quote-verified evidence
        (single cluster or weight too low)
    - REJECTED:
        otherwise
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
            support_ratio=0.0,
        )

    # 1. Group claims by normalized value
    # val -> { clusters: { cluster_key: weight }, has_tier_a: bool, has_quote_verified: bool, raw_values: set }
    value_data: Dict[str, Dict[str, Any]] = {}

    for c in claims:
        val = normalize_fact_value(field_name, c.get("norm_value") or c.get("raw_value") or "")
        if not val:
            continue

        tier = str(c.get("tier", "C")).upper()
        if c.get("domain_prior") is not None:
            prior = float(c["domain_prior"])
        elif tier == "A":
            prior = TIER_A_PRIOR
        elif tier == "B":
            prior = TIER_B_PRIOR
        else:
            prior = TIER_C_PRIOR

        # domain_reliability defaults to 1.0 if not explicitly specified
        if c.get("domain_reliability") is not None:
            reliability = float(c["domain_reliability"])
        else:
            reliability = 1.0

        # w = max(tier_prior * domain_reliability)
        page_weight = round(prior * min(1.0, max(0.1, reliability)), 4)

        cluster = str(
            c.get("cluster_key")
            if c.get("cluster_key") is not None
            else (c.get("cluster_id") if c.get("cluster_id") is not None else (c.get("domain") or "default_cluster"))
        )
        quote_verified = bool(c.get("quote_found", True))

        if val not in value_data:
            value_data[val] = {
                "clusters": {},
                "tier_a_clusters": set(),
                "has_tier_a": False,
                "has_quote_verified": False,
                "raw_values": set(),
            }

        if tier == "A":
            value_data[val]["has_tier_a"] = True
            value_data[val]["tier_a_clusters"].add(cluster)

        if quote_verified:
            value_data[val]["has_quote_verified"] = True

        if c.get("raw_value"):
            value_data[val]["raw_values"].add(str(c["raw_value"]))

        # w(cluster) = max(tier_prior * domain_reliability) of its pages asserting v
        curr_wt = value_data[val]["clusters"].get(cluster, 0.0)
        value_data[val]["clusters"][cluster] = max(curr_wt, page_weight)

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
            support_ratio=0.0,
        )

    # 2. Apply Pre-Consensus Conflict Merging Rules
    value_data = apply_conflict_merging_rules(value_data, field_name)

    # 3. Compute weight_sum and totals
    scored_values: List[Dict[str, Any]] = []
    total_field_weight = 0.0

    for val, data in value_data.items():
        w_sum = round(sum(data["clusters"].values()), 4)
        total_field_weight += w_sum
        scored_values.append({
            "value": val,
            "weight_sum": w_sum,
            "n_clusters": len(data["clusters"]),
            "has_tier_a": data["has_tier_a"],
            "has_quote_verified": data["has_quote_verified"],
            "raw_values": list(data["raw_values"]),
        })

    total_field_weight = round(total_field_weight, 4)

    # Compute support_ratio for each value
    for sv in scored_values:
        if total_field_weight > 0:
            sv["support_ratio"] = round(sv["weight_sum"] / total_field_weight, 4)
        else:
            sv["support_ratio"] = 0.0

    # Sort descending by support_ratio, then weight_sum, then tier_a
    scored_values.sort(key=lambda x: (x["support_ratio"], x["weight_sum"], x["has_tier_a"]), reverse=True)

    top_val = scored_values[0]
    competing_entries = [
        {
            "value": sv["value"],
            "support": sv["weight_sum"],
            "support_ratio": sv["support_ratio"],
            "n_clusters": sv["n_clusters"],
            "has_tier_a": sv["has_tier_a"],
        }
        for sv in scored_values[1:]
    ]

    # Check Tier-A disagreement across values
    tier_a_values = [sv["value"] for sv in scored_values if sv["has_tier_a"]]
    two_tier_a_disagree = len(tier_a_values) >= 2

    # 4. Deterministic Decision Rules (Rule 3.3)

    # A. Check VERIFIED:
    # Condition 1: a tier-A cluster asserts v and no tier-A asserts another value
    tier_a_verified = (top_val["has_tier_a"] and not two_tier_a_disagree)
    # Condition 2: support_ratio(v) >= 0.80 AND weight_sum(v) >= 1.2
    consensus_verified = (
        top_val["support_ratio"] >= CONSENSUS_SUPPORT_RATIO_THRESHOLD
        and top_val["weight_sum"] >= CONSENSUS_WEIGHT_SUM_THRESHOLD
    )

    if tier_a_verified and not two_tier_a_disagree:
        return FieldDecisionResult(
            field=field_name,
            decision="verified",
            value=top_val["value"],
            support=top_val["weight_sum"],
            n_clusters=top_val["n_clusters"],
            has_tier_a=True,
            rule_fired="verified_tier_a_authoritative",
            competing=competing_entries,
            support_ratio=top_val["support_ratio"],
        )

    if consensus_verified and not two_tier_a_disagree:
        return FieldDecisionResult(
            field=field_name,
            decision="verified",
            value=top_val["value"],
            support=top_val["weight_sum"],
            n_clusters=top_val["n_clusters"],
            has_tier_a=top_val["has_tier_a"],
            rule_fired="verified_multi_cluster_consensus",
            competing=competing_entries,
            support_ratio=top_val["support_ratio"],
        )

    # B. Check CONFLICT:
    # If not verified and 2 or more values each have support_ratio >= 0.20, or two tier-A disagree
    competing_ratios_high = (
        len(scored_values) >= 2
        and scored_values[0]["support_ratio"] >= CONFLICT_SUPPORT_RATIO_MIN
        and scored_values[1]["support_ratio"] >= CONFLICT_SUPPORT_RATIO_MIN
    )

    if two_tier_a_disagree or competing_ratios_high:
        return FieldDecisionResult(
            field=field_name,
            decision="conflict",
            value=top_val["value"],
            support=top_val["weight_sum"],
            n_clusters=top_val["n_clusters"],
            has_tier_a=top_val["has_tier_a"],
            rule_fired="conflict_competing_high_support" if competing_ratios_high else "conflict_tier_a_disagreement",
            competing=competing_entries,
            support_ratio=top_val["support_ratio"],
        )

    # C. Check UNCONFIRMED:
    # If not verified/conflict but exactly one value exists with quote-verified evidence
    # (single cluster or weight too low)
    quote_verified_values = [sv for sv in scored_values if sv["has_quote_verified"]]
    if len(quote_verified_values) == 1:
        uv = quote_verified_values[0]
        return FieldDecisionResult(
            field=field_name,
            decision="unconfirmed",
            value=uv["value"],
            support=uv["weight_sum"],
            n_clusters=uv["n_clusters"],
            has_tier_a=uv["has_tier_a"],
            rule_fired="unconfirmed_partial_evidence",
            competing=competing_entries,
            support_ratio=uv["support_ratio"],
        )

    # D. REJECTED:
    # Otherwise
    return FieldDecisionResult(
        field=field_name,
        decision="rejected",
        value=top_val["value"] if top_val["weight_sum"] >= 0.4 else None,
        support=top_val["weight_sum"],
        n_clusters=top_val["n_clusters"],
        has_tier_a=top_val["has_tier_a"],
        rule_fired="rejected_insufficient_support",
        competing=competing_entries,
        support_ratio=top_val["support_ratio"],
    )
