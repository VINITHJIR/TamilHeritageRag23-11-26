"""Stable site identifiers: site_id = f"TN-{district.code3}-{seq:04d}".

Method
------
1. District codes. config.DISTRICT_CODE3 assigns a unique 3-letter code to each of the
   38 LGD districts. The eight Level 1 codes (TNJ, MDU, CHN, CGL, ERD, VPM, SVG, KNK)
   are fixed by the seed data; ``fill_code3`` writes all codes to locations.code3.
2. Allocation. ``IdAllocator`` is loaded with every site_id already used (sites table
   + site_source_keys) and the persisted (source, source_key) -> site_id map.
   * key already persisted -> the same site_id is returned (never re-numbered);
   * otherwise seq = 1 + max seq used in that district, and the allocation is kept in
     memory so a dry run assigns distinct ids too.
   Callers allocate in a deterministic order (sorted source keys), so a fresh database
   produces the same ids for the same inputs.
3. Persistence. load.py writes each new (source, source_key, site_id) to
   site_source_keys in the same transaction as the site row. The Level 1 seed sites are
   registered as ('curated', site_id) by ``register_curated``.
"""

from __future__ import annotations

import re
from typing import Optional

from sqlalchemy import text as sa_text

from app.ingestion import config as C

SITE_ID_RE = re.compile(r"^TN-([A-Z]{3})-(\d{4})$")


def code3_for(district: Optional[str]) -> Optional[str]:
    return C.DISTRICT_CODE3.get(district or "")


class IdAllocator:
    def __init__(self, used_ids: set[str], persisted: dict[str, str]):
        self.persisted = dict(persisted)       # "source|source_key" -> site_id
        self.max_seq: dict[str, int] = {}
        for sid in set(used_ids) | set(persisted.values()):
            m = SITE_ID_RE.match(sid)
            if m:
                self.max_seq[m.group(1)] = max(self.max_seq.get(m.group(1), 0), int(m.group(2)))
        self.new_keys: dict[str, str] = {}

    def get(self, key: str) -> Optional[str]:
        return self.persisted.get(key) or self.new_keys.get(key)

    def allocate(self, key: str, district: str) -> tuple[str, bool]:
        """Return (site_id, is_new). Raises if the district has no code3."""
        existing = self.get(key)
        if existing:
            return existing, False
        code = code3_for(district)
        if not code:
            raise ValueError(f"no code3 for district {district!r}")
        seq = self.max_seq.get(code, 0) + 1
        if seq > 9999:
            raise ValueError(f"site_id sequence exhausted for {code}")
        self.max_seq[code] = seq
        sid = f"TN-{code}-{seq:04d}"
        self.new_keys[key] = sid
        return sid, True


def load_allocator(db) -> IdAllocator:
    used = {r[0] for r in db.execute(sa_text("SELECT site_id FROM sites"))}
    persisted = {f"{s}|{k}": sid for s, k, sid in
                 db.execute(sa_text("SELECT source, source_key, site_id FROM site_source_keys"))}
    return IdAllocator(used, persisted)


def fill_code3(db, dry_run: bool = False) -> dict:
    """Set locations.code3 for district rows (matched by canonical district name)."""
    from app.ingestion.normalize import canonical_district
    rows = db.execute(sa_text("SELECT location_id, name_en, code3 FROM locations WHERE level='district'")).fetchall()
    changes, unknown = [], []
    for loc_id, name, cur in rows:
        code = code3_for(canonical_district(name))
        if not code:
            unknown.append(name)
        elif cur != code:
            changes.append((loc_id, name, cur, code))
    if not dry_run:
        for loc_id, _, _, code in changes:
            db.execute(sa_text("UPDATE locations SET code3=:c WHERE location_id=:i"), {"c": code, "i": loc_id})
    return {"updated": len(changes), "unknown": unknown, "changes": changes}


def register_curated(db, dry_run: bool = False) -> int:
    """Register every existing site without a source key as ('curated', site_id)."""
    missing = [r[0] for r in db.execute(sa_text(
        "SELECT s.site_id FROM sites s WHERE NOT EXISTS "
        "(SELECT 1 FROM site_source_keys k WHERE k.site_id = s.site_id)"))]
    if not dry_run:
        for sid in missing:
            db.execute(sa_text("INSERT INTO site_source_keys(source, source_key, site_id) "
                               "VALUES ('curated', :k, :k) ON CONFLICT DO NOTHING"), {"k": sid})
    return len(missing)
