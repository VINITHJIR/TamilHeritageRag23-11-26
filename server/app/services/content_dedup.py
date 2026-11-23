"""Content deduplication, MinHash clustering, and relevance filtering for TamilHeritage-GPT.

Implements:
1. SHA256 hashing for exact duplicate detection.
2. MinHash (num_perm=64) on 3-shingles with Jaccard similarity estimation.
3. Cluster key assignment (first-seen domain) for copycat penalty.
4. Entity mention validation (relevance gate).
"""

import hashlib
import re
from typing import Any, Dict, List, Set

# Universal hash parameters (64 deterministic pairs of a, b modulo large prime)
_LARGE_PRIME = 4294967311  # 2^32 + 15
_NUM_PERM = 64

# Deterministic linear congruential seeds for reproducibility
_A_VALS = [((i * 1000003 + 3) % (_LARGE_PRIME - 1)) + 1 for i in range(_NUM_PERM)]
_B_VALS = [((i * 2000003 + 7) % _LARGE_PRIME) for i in range(_NUM_PERM)]


def normalize_text_for_hash(text: str) -> str:
    """Normalize text: strip, lowercase, collapse whitespace."""
    if not text:
        return ""
    return re.sub(r"\s+", " ", text.strip().lower())


def compute_text_sha256(text: str) -> str:
    """Compute SHA-256 hash of normalized text for exact duplicate detection."""
    norm = normalize_text_for_hash(text)
    return hashlib.sha256(norm.encode("utf-8")).hexdigest()


def _get_3_shingles(text: str) -> Set[str]:
    """Generate character 3-shingles from normalized text."""
    norm = normalize_text_for_hash(text)
    if len(norm) >= 3:
        return {norm[i:i+3] for i in range(len(norm) - 2)}
    return {norm} if norm else set()


def compute_minhash(text: str, num_perm: int = _NUM_PERM) -> List[int]:
    """Compute MinHash signature of length num_perm using 3-shingles."""
    shingles = _get_3_shingles(text)
    if not shingles:
        return [0] * num_perm

    # Convert shingles to 32-bit integer hashes
    shingle_hashes = [
        int(hashlib.md5(s.encode("utf-8")).hexdigest()[:8], 16)
        for s in shingles
    ]

    min_hashes = []
    for i in range(num_perm):
        a = _A_VALS[i]
        b = _B_VALS[i]
        # h(x) = (a * x + b) % prime
        min_val = min((a * sh + b) % _LARGE_PRIME for sh in shingle_hashes)
        min_hashes.append(min_val)

    return min_hashes


def compute_jaccard_similarity(minhash1: List[int], minhash2: List[int]) -> float:
    """Estimate Jaccard similarity between two MinHash signatures."""
    if not minhash1 or not minhash2 or len(minhash1) != len(minhash2):
        return 0.0

    matches = sum(1 for h1, h2 in zip(minhash1, minhash2) if h1 == h2)
    return round(matches / len(minhash1), 4)


def cluster_sources(
    sources: List[Dict[str, Any]],
    jaccard_threshold: float = 0.70,
) -> List[Dict[str, Any]]:
    """Cluster sources based on text Jaccard similarity.

    Assigns cluster_key = first seen domain in the cluster.
    If Jaccard similarity >= jaccard_threshold, joins existing cluster.
    """
    clusters: List[Dict[str, Any]] = []  # [{cluster_key, minhash, domain}]

    for src in sources:
        text = src.get("text", "") or src.get("excerpt", "")
        minhash = src.get("minhash") or compute_minhash(text)
        src["minhash"] = minhash
        domain = src.get("domain", "unknown")

        matched_cluster = None
        for cl in clusters:
            sim = compute_jaccard_similarity(minhash, cl["minhash"])
            if sim >= jaccard_threshold:
                matched_cluster = cl
                break

        if matched_cluster:
            src["cluster_key"] = matched_cluster["cluster_key"]
        else:
            # New cluster: cluster key is first seen domain
            new_key = f"{domain}_{len(clusters) + 1}"
            src["cluster_key"] = new_key
            clusters.append({
                "cluster_key": new_key,
                "minhash": minhash,
                "domain": domain,
            })

    return sources


def page_mentions_entity(page_text: str, entity_name: str) -> bool:
    """Check if page_text mentions the target entity (case-insensitive relevance filter)."""
    if not page_text or not entity_name:
        return False

    norm_text = normalize_text_for_hash(page_text)
    norm_entity = normalize_text_for_hash(entity_name)

    # 1. Exact entity substring match
    if norm_entity in norm_text:
        return True

    # 2. Check significant entity words (>= 4 chars, ignoring common stops like temple, amman, kovil)
    stopwords = {"temple", "kovil", "koyil", "fort", "palace", "near", "road", "street", "amman"}
    entity_words = [w for w in re.findall(r"[a-z0-9]+", norm_entity) if len(w) >= 3 and w not in stopwords]
    if not entity_words:
        entity_words = [w for w in re.findall(r"[a-z0-9]+", norm_entity) if len(w) >= 3]

    if entity_words and all(w in norm_text for w in entity_words):
        return True

    return False
