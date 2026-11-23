"""Central configuration for the Level 1B ingestion pipeline.

All tunable method parameters (thresholds, weights, column maps, alias maps) live
here so they can be cited verbatim in the paper and changed without touching code.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

SERVER_ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = SERVER_ROOT / "data"
RAW_DIR = DATA_DIR / "raw"
INTERIM_DIR = DATA_DIR / "interim"
CACHE_DIR = DATA_DIR / "cache"
REPORTS_DIR = DATA_DIR / "reports"

STATE_LGD_CODE = 33  # Tamil Nadu

# ---------------------------------------------------------------------------
# HTTP etiquette (Wikimedia User-Agent policy). Contact is configurable via env
# so no personal data is hard-coded in the repository.
# ---------------------------------------------------------------------------
INGEST_CONTACT = os.environ.get("INGEST_CONTACT", "academic research project")
USER_AGENT = f"TamilHeritageGPT-ingestion/0.2 ({INGEST_CONTACT}) python-httpx"
HTTP_MIN_INTERVAL_S = 1.0       # >= 1 s between requests to the same host
HTTP_MAX_RETRIES = 5
HTTP_TIMEOUT_S = 60.0


@dataclass
class ResolveConfig:
    """Entity-resolution weights and thresholds (resolve.py)."""
    w_name: float = 0.60
    w_district: float = 0.25
    w_geo: float = 0.15
    geo_full_m: float = 200.0     # distance <= this -> geo_score 1.0
    geo_zero_m: float = 2000.0    # distance >= this -> geo_score 0.0
    auto_link: float = 0.90       # score >= auto_link -> link to existing site
    review: float = 0.75          # review <= score < auto_link -> review_queue(kind='match')
    # What to do with records sent to review: 'hold' (do not create a site until a
    # human decides; avoids duplicates) or 'create' (create a new site anyway).
    review_policy: str = "hold"


@dataclass
class ChunkConfig:
    max_tokens: int = 350
    overlap_tokens: int = 50


@dataclass
class GroupConfig:
    min_children: int = 3         # >= N monuments in same locality -> parent complex


@dataclass
class PipelineConfig:
    resolve: ResolveConfig = field(default_factory=ResolveConfig)
    chunk: ChunkConfig = field(default_factory=ChunkConfig)
    group: GroupConfig = field(default_factory=GroupConfig)
    locality_match_min: float = 88.0   # rapidfuzz score (0-100) for locality -> local body
    embed_batch_size: int = 16
    db_batch_size: int = 200


DEFAULT_CONFIG = PipelineConfig()

# ---------------------------------------------------------------------------
# LGD column map: canonical field -> accepted header spellings (lower-cased,
# whitespace-collapsed). LGD exports vary between downloads and report types.
# ---------------------------------------------------------------------------
LGD_COLUMN_MAP: dict[str, list[str]] = {
    "state_code":        ["state code", "state lgd code"],
    "district_code":     ["district code", "district lgd code", "district panchayat code",
                          "zp code", "district panchayat lgd code"],
    "district_name":     ["district name", "district name (in english)", "district panchayat name",
                          "district panchayat name (in english)", "zp name"],
    "district_name_local": ["district name (in local)", "district name in local"],
    "subdistrict_code":  ["sub-district code", "subdistrict code", "sub district code",
                          "sub-district lgd code", "taluk code"],
    "subdistrict_name":  ["sub-district name", "subdistrict name", "sub district name",
                          "sub-district name (in english)", "taluk name"],
    "subdistrict_name_local": ["sub-district name (in local)", "subdistrict name (in local)"],
    "localbody_code":    ["local body code", "localbody code", "urban local body code", "ulb code",
                          "local body lgd code"],
    "localbody_name":    ["local body name", "local body name (in english)", "localbody name",
                          "urban local body name", "ulb name"],
    "localbody_name_local": ["local body name (in local)", "local body name in local"],
    "localbody_type":    ["local body type", "local body type name", "localbody type", "ulb type",
                          "type of local body"],
    "block_code":        ["block code", "block panchayat code", "intermediate panchayat code",
                          "block panchayat lgd code"],
    "block_name":        ["block name", "block panchayat name", "intermediate panchayat name"],
    "vp_code":           ["village panchayat code", "gram panchayat code", "village panchayat lgd code",
                          "gp code"],
    "vp_name":           ["village panchayat name", "gram panchayat name", "village panchayat name (in english)",
                          "gp name"],
    "vp_name_local":     ["village panchayat name (in local)", "gram panchayat name (in local)"],
}

# Local-body type text (lower-cased substring) -> locations.local_body_type enum
LOCAL_BODY_TYPE_MAP: list[tuple[str, str]] = [
    ("corporation", "municipal_corporation"),
    ("town panchayat", "town_panchayat"),
    ("municipality", "municipality"),
    ("municipal council", "municipality"),
    ("village panchayat", "village_panchayat"),
    ("gram panchayat", "village_panchayat"),
]

# ---------------------------------------------------------------------------
# District aliases: any spelling (lower-case) -> canonical LGD English name.
# ---------------------------------------------------------------------------
LGD_DISTRICTS = [
    "Ariyalur", "Chengalpattu", "Chennai", "Coimbatore", "Cuddalore", "Dharmapuri", "Dindigul",
    "Erode", "Kallakurichi", "Kancheepuram", "Kanniyakumari", "Karur", "Krishnagiri", "Madurai",
    "Mayiladuthurai", "Nagapattinam", "Namakkal", "Perambalur", "Pudukkottai", "Ramanathapuram",
    "Ranipet", "Salem", "Sivaganga", "Tenkasi", "Thanjavur", "Theni", "The Nilgiris", "Thiruvallur",
    "Thiruvarur", "Thoothukkudi", "Tiruchirappalli", "Tirunelveli", "Tirupathur", "Tiruppur",
    "Tiruvannamalai", "Vellore", "Viluppuram", "Virudhunagar",
]

DISTRICT_ALIASES: dict[str, str] = {
    # Tiruchirappalli
    "trichy": "Tiruchirappalli", "tiruchi": "Tiruchirappalli", "trichinopoly": "Tiruchirappalli",
    "tiruchirapalli": "Tiruchirappalli", "thiruchirappalli": "Tiruchirappalli",
    # Kancheepuram
    "kanchipuram": "Kancheepuram", "kanchi": "Kancheepuram", "conjeevaram": "Kancheepuram",
    "chingleput": "Chengalpattu", "chengalpet": "Chengalpattu", "chengalpattu": "Chengalpattu",
    # Thoothukkudi
    "tuticorin": "Thoothukkudi", "thoothukudi": "Thoothukkudi", "tuticorn": "Thoothukkudi",
    # Viluppuram
    "villupuram": "Viluppuram", "villuppuram": "Viluppuram", "south arcot": "Cuddalore",
    # Kanniyakumari
    "kanyakumari": "Kanniyakumari", "kanniyakumari": "Kanniyakumari", "cape comorin": "Kanniyakumari",
    # others
    "tanjore": "Thanjavur", "thanjavur": "Thanjavur", "madras": "Chennai",
    "nilgiris": "The Nilgiris", "the nilgiris": "The Nilgiris", "nilgiri": "The Nilgiris",
    "tiruvallur": "Thiruvallur", "thiruvallur": "Thiruvallur",
    "tiruvarur": "Thiruvarur", "thiruvarur": "Thiruvarur",
    "tirupattur": "Tirupathur", "tirupathur": "Tirupathur",
    "tirupur": "Tiruppur", "tiruppur": "Tiruppur",
    "thiruvannamalai": "Tiruvannamalai", "tiruvannamalai": "Tiruvannamalai",
    "north arcot": "Vellore", "ramnad": "Ramanathapuram", "pudukottai": "Pudukkottai",
    "sivagangai": "Sivaganga", "sivaganga": "Sivaganga", "dindugal": "Dindigul",
    "kovai": "Coimbatore", "salem": "Salem", "nagai": "Nagapattinam",
    "mayavaram": "Mayiladuthurai", "palayamkottai": "Tirunelveli", "tinnevelly": "Tirunelveli", "nellai": "Tirunelveli",
}
DISTRICT_ALIASES.update({d.lower(): d for d in LGD_DISTRICTS})

# Districts split after older lists (ASI lists pre-date 2019-2020 bifurcations).
# A record listed under the parent may legitimately sit in a successor district.
DISTRICT_SUCCESSORS: dict[str, list[str]] = {
    "Kancheepuram": ["Chengalpattu"],
    "Viluppuram": ["Kallakurichi"],
    "Vellore": ["Ranipet", "Tirupathur"],
    "Tirunelveli": ["Tenkasi"],
    "Nagapattinam": ["Mayiladuthurai"],
    "Cuddalore": ["Viluppuram", "Kallakurichi"],   # 'South Arcot' historical
}

# 3-letter codes used in site_id = TN-<code3>-<seq>. The first eight MUST match
# the Level 1 seed ids (TNJ, MDU, CHN, CGL, ERD, VPM, SVG, KNK).
DISTRICT_CODE3: dict[str, str] = {
    "Thanjavur": "TNJ", "Madurai": "MDU", "Chennai": "CHN", "Chengalpattu": "CGL",
    "Erode": "ERD", "Viluppuram": "VPM", "Sivaganga": "SVG", "Kanniyakumari": "KNK",
    "Ariyalur": "ARY", "Coimbatore": "CBE", "Cuddalore": "CDL", "Dharmapuri": "DPI",
    "Dindigul": "DGL", "Kallakurichi": "KLK", "Kancheepuram": "KPM", "Karur": "KRR",
    "Krishnagiri": "KGI", "Mayiladuthurai": "MYD", "Nagapattinam": "NGP", "Namakkal": "NMK",
    "Perambalur": "PMB", "Pudukkottai": "PDK", "Ramanathapuram": "RMD", "Ranipet": "RPT",
    "Salem": "SLM", "Tenkasi": "TKS", "Theni": "THN", "The Nilgiris": "NLG",
    "Thiruvallur": "TVR", "Thiruvarur": "TVU", "Thoothukkudi": "TUT", "Tiruchirappalli": "TRY",
    "Tirunelveli": "TVL", "Tirupathur": "TPT", "Tiruppur": "TPR", "Tiruvannamalai": "TVM",
    "Vellore": "VLR", "Virudhunagar": "VNR",
}

# Tamil district names, used ONLY when the LGD "local name" column is not in
# Tamil script (the 2024 LGD export repeats the English name in capitals for 30
# of 38 districts). Provenance is recorded as 'curated' in the report.
DISTRICT_NAME_TA: dict[str, str] = {
    "Ariyalur": "அரியலூர்", "Chengalpattu": "செங்கல்பட்டு", "Chennai": "சென்னை",
    "Coimbatore": "கோயம்புத்தூர்", "Cuddalore": "கடலூர்", "Dharmapuri": "தருமபுரி",
    "Dindigul": "திண்டுக்கல்", "Erode": "ஈரோடு", "Kallakurichi": "கள்ளக்குறிச்சி",
    "Kancheepuram": "காஞ்சிபுரம்", "Kanniyakumari": "கன்னியாகுமரி", "Karur": "கரூர்",
    "Krishnagiri": "கிருஷ்ணகிரி", "Madurai": "மதுரை", "Mayiladuthurai": "மயிலாடுதுறை",
    "Nagapattinam": "நாகப்பட்டினம்", "Namakkal": "நாமக்கல்", "Perambalur": "பெரம்பலூர்",
    "Pudukkottai": "புதுக்கோட்டை", "Ramanathapuram": "இராமநாதபுரம்", "Ranipet": "ராணிப்பேட்டை",
    "Salem": "சேலம்", "Sivaganga": "சிவகங்கை", "Tenkasi": "தென்காசி", "Thanjavur": "தஞ்சாவூர்",
    "Theni": "தேனி", "The Nilgiris": "நீலகிரி", "Thiruvallur": "திருவள்ளூர்",
    "Thiruvarur": "திருவாரூர்", "Thoothukkudi": "தூத்துக்குடி", "Tiruchirappalli": "திருச்சிராப்பள்ளி",
    "Tirunelveli": "திருநெல்வேலி", "Tirupathur": "திருப்பத்தூர்", "Tiruppur": "திருப்பூர்",
    "Tiruvannamalai": "திருவண்ணாமலை", "Vellore": "வேலூர்", "Viluppuram": "விழுப்புரம்",
    "Virudhunagar": "விருதுநகர்",
}

# Source tier -> trust level written to sites.trust and Qdrant payload 'trust'.
TIER_TRUST = {"A": "official", "B": "multi_source", "C": "single_source"}

WIKIPEDIA_LICENSE = "CC BY-SA 4.0, Wikipedia contributors"
WIKIDATA_LICENSE = "CC0 1.0, Wikidata"
