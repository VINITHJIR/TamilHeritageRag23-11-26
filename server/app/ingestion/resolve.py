"""Entity resolution: does an incoming record describe an existing site?

Method
------
For record r and candidate site s:

    score = 0.60 * name_sim + 0.25 * same_district + 0.15 * geo_score

* name_sim: max over all (r-name, s-name/alias) pairs of rapidfuzz ``token_set_ratio``/100
  on ``normalize.match_key`` forms (romanisation folding, kovil/koil -> temple, ...).
  Pairs are compared within the same script only (Latin-Latin, Tamil-Tamil).
  Generic-token guard: token_set_ratio returns 100 whenever one token set is a subset
  of the other, so "Siva Temple" would match "Kailasanatha Siva Temple" perfectly.
  We therefore drop GENERIC_TOKENS (temple, fort, group, monuments, ...) before
  scoring; if either side has no distinctive token left, the plain ``fuzz.ratio`` of
  the full keys is used instead.
* same_district: 1 if the canonical districts are equal or one is a post-2019
  successor of the other (old ASI lists say Kancheepuram for Mamallapuram, which is
  now Chengalpattu); 0 otherwise. Skipped if either district is unknown.
* geo_score: Haversine distance d; 1.0 if d <= 200 m, 0.0 if d >= 2 km, linear in
  between. Skipped if either side lacks coordinates.
* Skipped terms are removed and the remaining weights re-normalised to sum to 1.

Decision (thresholds in config.ResolveConfig):
    score >= 0.90         -> 'link' to the existing site
    0.75 <= score < 0.90  -> 'review' (review_queue kind='match'); with review_policy
                             'hold' no site is created until a reviewer decides
    score < 0.75          -> 'new' site

Stability and order:
* A record whose (source, source_key) is already in site_source_keys is linked to that
  site_id without scoring (method='source_key'), so re-runs never re-decide.
* Records are processed parents first, then in sorted key order; each new entity is
  added to the candidate pool, so later records (e.g. Wikidata items) can match it.
* Components: if a child's best candidate is the site its own parent resolved to, that
  candidate is excluded (a component is not a duplicate of its complex).
All best scores are kept for the score-distribution histogram in the report.
"""

from __future__ import annotations

import math
from typing import Iterable, Optional

from rapidfuzz import fuzz

from app.ingestion.config import ResolveConfig
from app.ingestion.normalize import TAMIL_RE, districts_compatible, match_key

GENERIC_TOKENS = {
    "temple", "fort", "group", "monuments", "monument", "mandapa", "shrine", "tank", "palace",
    "church", "mosque", "tomb", "tombs", "site", "sites", "ruins", "remains", "ancient", "old",
    "big", "great", "small", "rock", "cut", "cave", "caves", "inscription", "inscriptions", "saint",
    "cemetery", "grave", "graves", "memorial", "museum", "government", "complex", "buildings",
    "building", "pillar", "gate", "hill", "town", "village",
}


def haversine_m(lat1, lon1, lat2, lon2) -> float:
    r = 6371000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def geo_score(d_m: float, cfg: ResolveConfig) -> float:
    if d_m <= cfg.geo_full_m:
        return 1.0
    if d_m >= cfg.geo_zero_m:
        return 0.0
    return 1.0 - (d_m - cfg.geo_full_m) / (cfg.geo_zero_m - cfg.geo_full_m)


def _distinct(key: str) -> str:
    return " ".join(t for t in key.split() if t not in GENERIC_TOKENS)


def pair_sim(a: str, b: str) -> float:
    """Similarity of two names in [0, 1] (see module docstring)."""
    if bool(TAMIL_RE.search(a)) != bool(TAMIL_RE.search(b)):
        return 0.0
    ka, kb = match_key(a), match_key(b)
    if not ka or not kb:
        return 0.0
    da, db = _distinct(ka), _distinct(kb)
    if da and db:
        return fuzz.token_set_ratio(da, db) / 100.0
    return fuzz.ratio(ka, kb) / 100.0


def name_sim(names_a: Iterable[str], names_b: Iterable[str]) -> float:
    names_b = [n for n in names_b if n]
    return max((pair_sim(a, b) for a in names_a if a for b in names_b), default=0.0)


def score_pair(r: dict, s: dict, cfg: ResolveConfig) -> dict:
    ns = name_sim(record_names(r), s["names"])
    terms = [(cfg.w_name, ns)]
    dist = None
    if r.get("district") and s.get("district"):
        dist = 1.0 if districts_compatible(r["district"], s["district"]) else 0.0
        terms.append((cfg.w_district, dist))
    geo, d_m = None, None
    if r.get("lat") is not None and r.get("lon") is not None and s.get("lat") is not None and s.get("lon") is not None:
        d_m = haversine_m(float(r["lat"]), float(r["lon"]), float(s["lat"]), float(s["lon"]))
        geo = geo_score(d_m, cfg)
        terms.append((cfg.w_geo, geo))
    wsum = sum(w for w, _ in terms)
    score = sum(w * v for w, v in terms) / wsum
    return {"id": s["id"], "name": s["names"][0], "score": round(score, 4), "name_sim": round(ns, 4),
            "same_district": dist, "geo_score": None if geo is None else round(geo, 4),
            "distance_m": None if d_m is None else round(d_m)}


def record_names(r: dict) -> list[str]:
    names = [r.get("name_en"), r.get("name_ta"), r.get("name_raw")]
    names += [a["alias"] for a in r.get("aliases", []) if a.get("alias")]
    return list(dict.fromkeys(n for n in names if n))


def decide(score: float, cfg: ResolveConfig) -> str:
    if score >= cfg.auto_link:
        return "link"
    if score >= cfg.review:
        return "review"
    return "new"


class Resolver:
    """Stateful resolver over a candidate pool (existing sites + entities created so far)."""

    def __init__(self, existing_sites: list[dict], source_keys: dict[str, str],
                 cfg: ResolveConfig = ResolveConfig()):
        # existing_sites: [{id: site_id, names: [...], district, lat, lon}]
        self.cfg = cfg
        self.pool: list[dict] = [dict(s, is_existing=True) for s in existing_sites]
        self.source_keys = source_keys            # "source|source_key" -> site_id
        self.best_scores: list[float] = []
        self.resolved: dict[str, str] = {}        # record key -> target id (site_id or entity key)

    def resolve(self, r: dict, key: str) -> dict:
        if key in self.source_keys:
            target = self.source_keys[key]
            self.resolved[key] = target
            self._add_names(target, r)
            return {"action": "link", "target": target, "method": "source_key", "score": None, "candidates": []}

        exclude = None
        if r.get("parent_key"):
            exclude = self.resolved.get(r["parent_key"])
        scored = sorted((score_pair(r, s, self.cfg) for s in self.pool if s["id"] != exclude),
                        key=lambda x: (-x["score"], x["id"]))
        top = scored[:3]
        best = top[0] if top else None
        bscore = best["score"] if best else 0.0
        self.best_scores.append(bscore)
        action = decide(bscore, self.cfg)
        if action == "link":
            target = best["id"]
            self._add_names(target, r)
        else:
            target = key
            if action == "new" or self.cfg.review_policy == "create":
                self.pool.append({"id": key, "names": record_names(r), "district": r.get("district"),
                                  "lat": r.get("lat"), "lon": r.get("lon"), "is_existing": False})
        self.resolved[key] = target
        return {"action": action, "target": target, "method": "score", "score": bscore, "candidates": top}

    def _add_names(self, target: str, r: dict) -> None:
        """Linked records contribute their names/coords to the target (better later matches)."""
        for s in self.pool:
            if s["id"] == target:
                s["names"] = list(dict.fromkeys(s["names"] + record_names(r)))
                if s.get("lat") is None and r.get("lat") is not None:
                    s["lat"], s["lon"] = r["lat"], r["lon"]
                return


def histogram(scores: list[float], width: float = 0.05) -> list[tuple[str, int]]:
    bins = [0] * int(round(1 / width))
    for s in scores:
        bins[min(int(s / width), len(bins) - 1)] += 1
    return [(f"{i * width:.2f}-{(i + 1) * width:.2f}", n) for i, n in enumerate(bins)]
