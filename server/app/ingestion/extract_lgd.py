"""Local Government Directory (LGD) extraction.

Method
------
LGD (lgdirectory.gov.in, Ministry of Panchayati Raj) exports the same entities in
several formats and with header names that vary between report types and releases.

1. Reading. ``read_table`` turns any supported file into a list of string rows:
   * CSV: encoding auto-detected by trial decoding (utf-8-sig, utf-8, cp1252, latin-1);
     delimiter sniffed (comma / semicolon / tab).
   * Excel 2003 XML Spreadsheet (what LGD serves as ".xls"): parsed with ElementTree,
     honouring ss:Index (skipped cells) and ss:MergeAcross (merged header cells).
   * HTML tables (some LGD ".xls" downloads are HTML): parsed with a tolerant regex.
2. Header detection. The first 25 rows are scored by how many cells match a known
   header synonym (config.LGD_COLUMN_MAP); the best row is the header. If the row
   below it is a sub-header such as "(In English) | (In Local)", the two rows are
   combined column-wise ("District Name" + "(In English)").
3. Column mapping. Each canonical field is mapped by exact synonym match first, then
   by prefix match; a column is never assigned to two fields.
4. File kind. Determined from the mapped fields, not the file name:
   village-panchayat (vp_code+vp_name) > urban local body (localbody_code+name)
   > sub-district > district.
5. Tamil names. A "local name" is kept only if it contains Tamil script
   (U+0B80-U+0BFF) and is not merely the English name; the 2024 district export
   repeats the English name in capitals for 30 of 38 districts.
6. Scope. Rows with an explicit state code other than 33 are dropped.

Output: plain dict records with keys
    level, lgd_code, name_en, name_ta, local_body_type, district, district_lgd,
    subdistrict_lgd, block_lgd, block_name, source_file
where ``district`` is the canonical LGD English district name.
"""

from __future__ import annotations

import csv
import io
import logging
import re
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Optional

from app.ingestion import config as C
from app.ingestion.normalize import canonical_district, clean_text, has_tamil

logger = logging.getLogger(__name__)

SS = "{urn:schemas-microsoft-com:office:spreadsheet}"
SUPPORTED_SUFFIXES = {".csv", ".xls", ".xml", ".htm", ".html", ".txt"}


# ----------------------------------------------------------------- readers
def detect_encoding(raw: bytes) -> str:
    for enc in ("utf-8-sig", "utf-8", "cp1252", "latin-1"):
        try:
            raw.decode(enc)
            return enc
        except UnicodeDecodeError:
            continue
    return "latin-1"


def _read_spreadsheet_xml(text: str) -> list[list[str]]:
    root = ET.fromstring(text)
    rows: list[list[str]] = []
    for ws in root.iter(f"{SS}Worksheet"):
        for row in ws.iter(f"{SS}Row"):
            cur: list[str] = []
            for cell in row.findall(f"{SS}Cell"):
                idx = cell.get(f"{SS}Index")
                if idx:
                    while len(cur) < int(idx) - 1:
                        cur.append("")
                data = cell.find(f"{SS}Data")
                val = "".join(data.itertext()) if data is not None else ""
                cur.append(clean_text(val))
                for _ in range(int(cell.get(f"{SS}MergeAcross", "0"))):
                    cur.append(clean_text(val))      # replicate merged header text
            rows.append(cur)
    return rows


def _read_html_table(text: str) -> list[list[str]]:
    rows = []
    for tr in re.findall(r"<tr[^>]*>(.*?)</tr>", text, re.S | re.I):
        cells = re.findall(r"<t[hd][^>]*>(.*?)</t[hd]>", tr, re.S | re.I)
        rows.append([clean_text(re.sub(r"<[^>]+>", " ", c)) for c in cells])
    return rows


def read_table(path: Path) -> list[list[str]]:
    raw = path.read_bytes()
    text = raw.decode(detect_encoding(raw))
    head = text[:2048].lstrip().lower()
    if head.startswith("<?xml") or "<workbook" in head:
        return _read_spreadsheet_xml(text)
    if "<table" in text[:200000].lower():
        return _read_html_table(text)
    sample = text[:8192]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",;\t")
    except csv.Error:
        dialect = csv.excel
    return [[clean_text(c) for c in row] for row in csv.reader(io.StringIO(text), dialect)]


# ----------------------------------------------------------------- headers
def _norm_header(h: str) -> str:
    return " ".join(h.lower().replace("_", " ").split())


_ALL_SYNONYMS = {s for syns in C.LGD_COLUMN_MAP.values() for s in syns}


def _header_score(row: list[str]) -> int:
    return sum(1 for c in row if _norm_header(c) in _ALL_SYNONYMS
               or any(_norm_header(c).startswith(s) for s in _ALL_SYNONYMS if len(s) > 8))


def find_header(rows: list[list[str]]) -> tuple[int, list[str], int]:
    """Return (header_row_index, combined_header, first_data_row_index)."""
    best_i, best_s = 0, -1
    for i, r in enumerate(rows[:25]):
        s = _header_score(r)
        if s > best_s:
            best_i, best_s = i, s
    header = list(rows[best_i])
    start = best_i + 1
    if start < len(rows):
        nxt = rows[start]
        if any(re.fullmatch(r"\(?\s*in\s+(english|local)\s*\)?", _norm_header(c)) for c in nxt if c):
            width = max(len(header), len(nxt))
            header += [""] * (width - len(header))
            header = [f"{h} {nxt[j]}".strip() if j < len(nxt) and nxt[j] else h for j, h in enumerate(header)]
            start += 1
    return best_i, header, start


def map_columns(header: list[str]) -> dict[str, int]:
    norm = [_norm_header(h) for h in header]
    used: set[int] = set()
    mapping: dict[str, int] = {}
    for exact in (True, False):
        for field, syns in C.LGD_COLUMN_MAP.items():
            if field in mapping:
                continue
            for syn in syns:
                for j, h in enumerate(norm):
                    if j in used:
                        continue
                    if (h == syn) if exact else (h.startswith(syn) and len(syn) > 8):
                        mapping[field] = j
                        used.add(j)
                        break
                if field in mapping:
                    break
    # "District Name (In English)" can also be the plain 'district name' column
    return mapping


def detect_kind(cols: dict[str, int]) -> Optional[str]:
    if "vp_code" in cols and "vp_name" in cols:
        return "village_panchayat"
    if "localbody_code" in cols and "localbody_name" in cols:
        return "urban_local_body"
    if "subdistrict_code" in cols and "subdistrict_name" in cols:
        return "subdistrict"
    if "district_code" in cols and "district_name" in cols:
        return "district"
    return None


def local_body_type(text: Optional[str], default: str = "other") -> str:
    t = (text or "").lower()
    for needle, enum in C.LOCAL_BODY_TYPE_MAP:
        if needle in t:
            return enum
    return default


def clean_tamil_name(name_local: Optional[str], name_en: str) -> Optional[str]:
    """Keep a local name only if it is genuine Tamil script (see module docstring, 5)."""
    s = clean_text(name_local)
    if not s or s.upper() == clean_text(name_en).upper() or not has_tamil(s):
        return None
    return s


def _int(v: Optional[str]) -> Optional[int]:
    v = (v or "").strip()
    v = v[:-2] if v.endswith(".0") else v
    return int(v) if v.isdigit() else None


# ----------------------------------------------------------------- extraction
def extract_lgd_file(path: Path) -> tuple[str, list[dict]]:
    """Extract one LGD file. Returns (kind, records)."""
    rows = [r for r in read_table(path) if any(c for c in r)]
    if not rows:
        return "empty", []
    _, header, start = find_header(rows)
    cols = map_columns(header)
    kind = detect_kind(cols)
    if kind is None:
        logger.warning("LGD: could not recognise columns in %s: %s", path.name, header)
        return "unknown", []

    def g(row, field):
        j = cols.get(field)
        return row[j] if j is not None and j < len(row) else ""

    out: list[dict] = []
    for row in rows[start:]:
        sc = _int(g(row, "state_code"))
        if sc is not None and sc != C.STATE_LGD_CODE:
            continue
        district_raw = g(row, "district_name")
        district = canonical_district(district_raw)
        rec = {"district": district, "district_raw": district_raw or None,
               "district_lgd": _int(g(row, "district_code")), "source_file": path.name}
        if kind == "district":
            code, name = _int(g(row, "district_code")), district_raw
            if code is None or not name:
                continue
            rec.update(level="district", lgd_code=code, name_en=name,
                       name_ta=clean_tamil_name(g(row, "district_name_local"), name), local_body_type=None)
        elif kind == "subdistrict":
            code, name = _int(g(row, "subdistrict_code")), g(row, "subdistrict_name")
            if code is None or not name:
                continue
            rec.update(level="subdistrict", lgd_code=code, name_en=name,
                       name_ta=clean_tamil_name(g(row, "subdistrict_name_local"), name), local_body_type=None)
        elif kind == "urban_local_body":
            code, name = _int(g(row, "localbody_code")), g(row, "localbody_name")
            if code is None or not name:
                continue
            rec.update(level="local_body", lgd_code=code, name_en=name,
                       name_ta=clean_tamil_name(g(row, "localbody_name_local"), name),
                       local_body_type=local_body_type(g(row, "localbody_type") or name))
        else:  # village_panchayat
            code, name = _int(g(row, "vp_code")), g(row, "vp_name")
            if code is None or not name:
                continue
            rec.update(level="local_body", lgd_code=code, name_en=name,
                       name_ta=clean_tamil_name(g(row, "vp_name_local"), name),
                       local_body_type="village_panchayat",
                       block_lgd=_int(g(row, "block_code")), block_name=g(row, "block_name") or None)
        out.append(rec)
    logger.info("LGD: %s -> kind=%s, %d records", path.name, kind, len(out))
    return kind, out


def find_lgd_files(raw_dir: Path = C.RAW_DIR) -> list[Path]:
    """LGD inputs: server/data/raw/lgd/** plus any top-level file named lgd*."""
    files = set()
    lgd_dir = raw_dir / "lgd"
    if lgd_dir.exists():
        files |= {p for p in lgd_dir.rglob("*") if p.is_file() and p.suffix.lower() in SUPPORTED_SUFFIXES}
    files |= {p for p in raw_dir.glob("lgd*") if p.is_file() and p.suffix.lower() in SUPPORTED_SUFFIXES}
    return sorted(files)


def extract_all(paths: list[Path]) -> dict:
    """Extract and de-duplicate all LGD files (by (level, lgd_code))."""
    seen: dict[tuple, dict] = {}
    kinds: dict[str, str] = {}
    unmapped_districts: set[str] = set()
    for p in paths:
        kind, recs = extract_lgd_file(p)
        kinds[p.name] = kind
        for r in recs:
            if r["district"] is None:
                unmapped_districts.add(r.get("district_raw") or "?")
            seen.setdefault((r["level"], r["lgd_code"]), r)
    records = sorted(seen.values(), key=lambda r: ({"district": 0, "subdistrict": 1, "local_body": 2}[r["level"]],
                                                    r["district"] or "", r["name_en"]))
    for r in records:  # curated Tamil fallback for districts only (provenance flagged)
        if r["level"] == "district":
            r["district"] = r["district"] or canonical_district(r["name_en"])
            if not r.get("name_ta") and r["district"] in C.DISTRICT_NAME_TA:
                r["name_ta"] = C.DISTRICT_NAME_TA[r["district"]]
                r["name_ta_source"] = "curated"
    return {"records": records, "kinds": kinds, "unmapped_districts": sorted(unmapped_districts)}
