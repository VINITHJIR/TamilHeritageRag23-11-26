"""ASI (Archaeological Survey of India) monument-list extraction from PDFs.

Method
------
1. Tables are extracted page by page with pdfplumber ``extract_tables`` (lattice
   detection on ruling lines; ASI and EAC-PM lists are ruled tables).
2. Header detection: a row is a header if it contains a name column ("Name of the
   Monument", "Monument", "Site", "British Graves/Cemeteries", ...) and at least one
   of "Locality"/"Location"/"Place" or "District". Column roles are taken from the
   header and carried across pages until a new header appears.
3. Kind: 'site' if the name column header mentions "site" (e.g. "Archaeological
   sites and remains"), otherwise 'monument'.
4. Multi-line cells: newlines inside a cell are joined with spaces. A row whose serial
   number is empty but which has text is a continuation of the previous row (pdfplumber
   splits tall cells across rows); its cells are appended to the previous row.
5. Scope: a one-cell row naming a state ("Tamil Nadu", "Kerala", ...) opens a state
   section; only rows inside a Tamil Nadu section are kept. If a document has no state
   sections at all (circle-level lists), rows are kept when their district maps to a
   Tamil Nadu district.
6. Circle: from the file name (asi_chennai*.pdf -> 'chennai', *trichy*/*tiruchirappalli*
   -> 'trichy') or a "<X> Circle" phrase in the first pages; otherwise the file stem.
7. source_key = f"asi_{circle}:{kind}:{slno}". Duplicate keys inside one document get a
   deterministic "#n" suffix (logged), so keys are stable for an unchanged file.

Output records: source='asi', source_key, slno, name_raw, name_en, locality, district_raw,
district (canonical), sub_circle, circle, kind, page, source_file.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Optional

from app.ingestion import config as C
from app.ingestion.normalize import canonical_district, clean_text, strip_admin_notes

logger = logging.getLogger(__name__)

NAME_HEADERS = ("name of the monument", "name of monument", "monument", "site", "british graves",
                "graves/cemeteries", "name of the site", "kos minar")
LOCALITY_HEADERS = ("locality", "location", "place", "village")
DISTRICT_HEADERS = ("district",)
SUBCIRCLE_HEADERS = ("sub circle", "sub-circle", "subcircle")
SLNO_HEADERS = ("sl", "s.no", "s. no", "sl.no", "sl. no", "no.", "sr")

INDIAN_STATES = {
    "andhra pradesh", "arunachal pradesh", "assam", "bihar", "chhattisgarh", "goa", "gujarat", "haryana",
    "himachal pradesh", "jharkhand", "karnataka", "kerala", "madhya pradesh", "maharashtra", "manipur",
    "meghalaya", "mizoram", "nagaland", "odisha", "orissa", "punjab", "rajasthan", "sikkim", "tamil nadu",
    "telangana", "tripura", "uttar pradesh", "uttarakhand", "west bengal", "delhi", "nct delhi",
    "jammu & kashmir", "jammu and kashmir", "ladakh", "puducherry", "pondicherry", "chandigarh",
    "daman & diu", "dadra & nagar haveli", "andaman & nicobar islands", "lakshadweep",
}


def _cell(c) -> str:
    return clean_text((c or "").replace("\n", " "))


def _role_map(header: list[str]) -> Optional[dict]:
    low = [h.lower() for h in header]
    roles: dict[str, int] = {}
    for j, h in enumerate(low):
        if not h:
            continue
        if "slno" not in roles and any(h.startswith(s) for s in SLNO_HEADERS) and len(h) <= 8:
            roles["slno"] = j
        elif "sub_circle" not in roles and any(s in h for s in SUBCIRCLE_HEADERS):
            roles["sub_circle"] = j
        elif "district" not in roles and any(s in h for s in DISTRICT_HEADERS):
            roles["district"] = j
        elif "locality" not in roles and any(s in h for s in LOCALITY_HEADERS):
            roles["locality"] = j
        elif "name" not in roles and any(s in h for s in NAME_HEADERS):
            roles["name"] = j
            roles["_kind"] = "site" if "site" in h and "monument" not in h else "monument"
    if "name" in roles and ("locality" in roles or "district" in roles):
        return roles
    return None


def detect_circle(path: Path, first_text: str) -> str:
    stem = path.stem.lower()
    if "chennai" in stem:
        return "chennai"
    if "trichy" in stem or "tiruchi" in stem:
        return "trichy"
    m = re.search(r"\b(chennai|trichy|tiruchirappalli)\s+circle\b", first_text, re.I)
    if m:
        return "trichy" if m.group(1).lower().startswith("t") else "chennai"
    return re.sub(r"[^a-z0-9]+", "_", stem).strip("_")[:40] or "doc"


def extract_asi_pdf(path: Path, limit: Optional[int] = None) -> list[dict]:
    import pdfplumber  # imported lazily: heavy

    out: list[dict] = []
    with pdfplumber.open(str(path)) as pdf:
        first_text = " ".join((p.extract_text() or "") for p in pdf.pages[:3])
        circle = detect_circle(path, first_text)
        has_state_sections = False
        state: Optional[str] = None
        roles: Optional[dict] = None
        rows: list[dict] = []

        for pno, page in enumerate(pdf.pages, start=1):
            for table in page.extract_tables():
                for raw in table:
                    cells = [_cell(c) for c in raw]
                    nonempty = [c for c in cells if c]
                    if not nonempty:
                        continue
                    new_roles = _role_map(cells)
                    if new_roles:
                        roles = new_roles
                        continue
                    # Section header: the only text sits in the serial-number column
                    # (or a known state name anywhere) and is not a number. Any such
                    # header closes the previous section, even for unlisted states.
                    sl_idx = roles.get("slno", 0) if roles else 0
                    only = nonempty[0] if len(nonempty) == 1 else None
                    if only and not re.search(r"\d", only) and (
                            only.lower().strip(" :") in INDIAN_STATES
                            or (sl_idx < len(cells) and cells[sl_idx] == only)):
                        state = only.lower().strip(" :")
                        has_state_sections = has_state_sections or state in INDIAN_STATES
                        continue
                    if roles is None:
                        continue

                    def g(role):
                        j = roles.get(role)
                        return cells[j] if j is not None and j < len(cells) else ""

                    slno = re.sub(r"[^\d]", "", g("slno")) if "slno" in roles else ""
                    rec = {"slno": slno, "name_raw": g("name"), "locality": g("locality"),
                           "district_raw": g("district"), "sub_circle": g("sub_circle"),
                           "kind": roles["_kind"], "page": pno, "state": state}
                    if not slno and rows and rows[-1]["page"] >= pno - 1 and "slno" in roles:
                        prev = rows[-1]          # continuation of a tall cell
                        for k in ("name_raw", "locality", "district_raw", "sub_circle"):
                            if rec[k]:
                                prev[k] = clean_text(f"{prev[k]} {rec[k]}")
                        continue
                    if rec["name_raw"]:
                        rows.append(rec)

    seen: dict[str, int] = {}
    for r in rows:
        if has_state_sections and r["state"] != "tamil nadu":
            continue
        district = canonical_district(r["district_raw"]) or canonical_district(r["locality"])
        if not has_state_sections and district is None:
            continue
        key = f"asi_{circle}:{r['kind']}:{r['slno'] or 'p' + str(r['page'])}"
        n = seen.get(key, 0)
        seen[key] = n + 1
        if n:
            logger.warning("ASI: duplicate key %s in %s; using suffix #%d", key, path.name, n + 1)
            key = f"{key}#{n + 1}"
        out.append({
            "source": "asi", "source_key": key, "slno": r["slno"], "circle": circle,
            "name_raw": r["name_raw"], "name_en": strip_admin_notes(r["name_raw"]),
            "locality": strip_admin_notes(r["locality"]), "district_raw": r["district_raw"],
            "district": district, "sub_circle": r["sub_circle"] or None, "kind": r["kind"],
            "page": r["page"], "source_file": path.name, "tier": "A",
        })
        if limit and len(out) >= limit:
            break
    logger.info("ASI: %s (circle=%s) -> %d Tamil Nadu rows", path.name, circle, len(out))
    return out


def find_asi_files(raw_dir: Path = C.RAW_DIR) -> list[Path]:
    files = set()
    asi_dir = raw_dir / "asi"
    if asi_dir.exists():
        files |= set(asi_dir.rglob("*.pdf"))
    files |= set(raw_dir.glob("*.pdf"))
    return sorted(files)
