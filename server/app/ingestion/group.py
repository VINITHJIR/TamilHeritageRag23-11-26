"""Parent–child (complex -> component) detection.

Method
------
1. Records are bucketed by (district, match_key(locality)). Locality is the ASI
   "Locality" column after normalisation, so "Mamallapuram"/"Mahabalipuram" variants
   fold to the same key only if they are textually close; the alias map is not used here.
2. A bucket with >= GroupConfig.min_children (default 3) records forms a complex.
3. Parent choice:
   a. If one member's own name equals the locality (match_key equal, or rapidfuzz
      token_set_ratio >= 95, e.g. "Fort St. George" listed at locality "Fort St. George"),
      that member becomes the parent and the others its children.
   b. Otherwise a synthetic parent record is created:
      name_en = "Group of Monuments at <Locality>", source='group',
      source_key = "group:<district>:<locality_key>", same district, categories = union of
      the children's categories, primary = the most frequent child primary category.
4. Every decision is emitted as a review item (kind='parent', status='auto') with the
   member list, so all automatic groupings can be audited and reverted by a reviewer.

Children get ``parent_key`` = "<source>|<source_key>" of the parent. resolve.py later
maps parent keys to site_ids, and treats a child whose best match is its own parent's
site as a *component* (new child site) rather than a duplicate of the parent.
"""

from __future__ import annotations

from collections import Counter, defaultdict

from rapidfuzz import fuzz

from app.ingestion.config import GroupConfig
from app.ingestion.normalize import match_key


def rkey(r: dict) -> str:
    return f"{r['source']}|{r['source_key']}"


def group_records(records: list[dict], cfg: GroupConfig = GroupConfig()) -> tuple[list[dict], list[dict]]:
    """Return (records_with_parents_added, review_items)."""
    buckets: dict[tuple, list[dict]] = defaultdict(list)
    for r in records:
        loc = match_key(r.get("locality") or "")
        if loc and r.get("district"):
            buckets[(r["district"], loc)].append(r)

    out = list(records)
    reviews: list[dict] = []
    for (district, loc_key), members in sorted(buckets.items()):
        if len(members) < cfg.min_children:
            continue
        locality = members[0]["locality"]
        parent = None
        for m in members:
            mk = match_key(m["name_en"])
            if mk == loc_key or fuzz.token_set_ratio(mk, loc_key) >= 95:
                parent = m
                break
        if parent is None:
            cats = Counter(c for m in members for c in m.get("categories", []))
            prim = Counter(m.get("primary_category") for m in members).most_common(1)[0][0]
            parent = {
                "source": "group", "source_key": f"group:{district}:{loc_key}",
                "name_en": f"Group of Monuments at {locality}", "name_raw": None,
                "locality": locality, "district": district, "kind": "complex",
                "primary_category": prim, "categories": [c for c, _ in cats.most_common()],
                "rule": "group", "tier": members[0].get("tier", "A"), "is_group_parent": True,
                "synthetic": True,
            }
            out.append(parent)
            method = "synthetic_parent"
        else:
            parent["is_group_parent"] = True
            method = "member_named_as_locality"
        pk = rkey(parent)
        children = [m for m in members if m is not parent]
        for c in children:
            c["parent_key"] = pk
        reviews.append({
            "kind": "parent", "source": parent["source"], "source_key": parent["source_key"],
            "status": "auto", "reason": f"{len(children)} monuments share locality '{locality}' in {district} ({method})",
            "payload": {"parent": parent["name_en"], "district": district, "locality": locality, "method": method},
            "candidates": [{"source_key": c["source_key"], "name_en": c["name_en"]} for c in children],
        })
    return out, reviews
