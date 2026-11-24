"""LGD Place and Location Resolution Service.

Resolves place candidates extracted from user text or Gemini classification
against the official Local Government Directory (LGD) locations hierarchy:
State -> District -> Subdistrict (Taluk) -> Local Body.
"""

from dataclasses import dataclass
import logging
import re
from typing import Dict, List, Optional, Tuple
from sqlalchemy import text as sa_text
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)


@dataclass
class ResolvedPlace:
    district_id: Optional[int]
    district_name: Optional[str]
    subdistrict_id: Optional[int]
    subdistrict_name: Optional[str]
    local_body_id: Optional[int]
    local_body_name: Optional[str]
    matched_term: Optional[str]


# Pre-mapped common towns, taluks, historical areas, and municipal regions
# to their respective district names in Tamil Nadu.
TOWN_DISTRICT_ALIASES: Dict[str, str] = {
    # Coimbatore
    "pollachi": "Coimbatore",
    "anaimalai": "Coimbatore",
    "valparai": "Coimbatore",
    "mettupalayam": "Coimbatore",
    "sulur": "Coimbatore",
    "annur": "Coimbatore",
    "kinathukadavu": "Coimbatore",
    "kovai": "Coimbatore",

    # Erode
    "erode": "Erode",
    "gobichettipalayam": "Erode",
    "gobi": "Erode",
    "sathyamangalam": "Erode",
    "sathy": "Erode",
    "bhavani": "Erode",
    "perundurai": "Erode",
    "chennimalai": "Erode",
    "kodumudi": "Erode",
    "pariyur": "Erode",
    "bannari": "Erode",
    "anthiyur": "Erode",

    # Thanjavur
    "thanjavur": "Thanjavur",
    "tanjore": "Thanjavur",
    "thanjai": "Thanjavur",
    "தஞ்சாவூர்": "Thanjavur",
    "தஞ்சை": "Thanjavur",
    "kumbakonam": "Thanjavur",
    "dharasuram": "Thanjavur",
    "swamimalai": "Thanjavur",
    "papanasam": "Thanjavur",
    "pattukkottai": "Thanjavur",
    "thiruvaiyaru": "Thanjavur",

    # Chengalpattu
    "chengalpattu": "Chengalpattu",
    "mamallapuram": "Chengalpattu",
    "mahabalipuram": "Chengalpattu",
    "tambaram": "Chengalpattu",
    "thirukalukundram": "Chengalpattu",
    "madurantakam": "Chengalpattu",
    "pallavaram": "Chengalpattu",
    "chromepet": "Chengalpattu",
    "vandalur": "Chengalpattu",

    # Chennai
    "chennai": "Chennai",
    "madras": "Chennai",
    "egmore": "Chennai",
    "mylapore": "Chennai",
    "triplicane": "Chennai",
    "sholinganallur": "Chennai",
    "guindy": "Chennai",
    "t nagar": "Chennai",
    "george town": "Chennai",
    "adyar": "Chennai",
    "royapettah": "Chennai",
    "velachery": "Chennai",
    "saidapet": "Chennai",

    # Madurai
    "madurai": "Madurai",
    "thiruparankundram": "Madurai",
    "thirupparankundram": "Madurai",
    "pazhamudircholai": "Madurai",
    "melur": "Madurai",
    "usilampatti": "Madurai",
    "vadipatti": "Madurai",
    "thirumangalam": "Madurai",

    # Tiruchirappalli
    "tiruchirappalli": "Tiruchirappalli",
    "trichy": "Tiruchirappalli",
    "tiruchy": "Tiruchirappalli",
    "srirangam": "Tiruchirappalli",
    "samayapuram": "Tiruchirappalli",
    "manapparai": "Tiruchirappalli",
    "musiri": "Tiruchirappalli",
    "thuraiyur": "Tiruchirappalli",

    # Kanniyakumari
    "kanniyakumari": "Kanniyakumari",
    "kanyakumari": "Kanniyakumari",
    "nagercoil": "Kanniyakumari",
    "agastheeswaram": "Kanniyakumari",
    "padmanabhapuram": "Kanniyakumari",
    "thuckalay": "Kanniyakumari",
    "marthandam": "Kanniyakumari",
    "colachel": "Kanniyakumari",

    # Villupuram
    "villupuram": "Villupuram",
    "gingee": "Villupuram",
    "senji": "Villupuram",
    "tindivanam": "Villupuram",
    "vanur": "Villupuram",

    # Sivaganga
    "sivaganga": "Sivaganga",
    "keeladi": "Sivaganga",
    "karaikudi": "Sivaganga",
    "chettinad": "Sivaganga",
    "devakottai": "Sivaganga",
    "manamadurai": "Sivaganga",

    # Dindigul
    "dindigul": "Dindigul",
    "palani": "Dindigul",
    "kodaikanal": "Dindigul",
    "oddanchatram": "Dindigul",

    # Ramanathapuram
    "ramanathapuram": "Ramanathapuram",
    "ramnad": "Ramanathapuram",
    "rameswaram": "Ramanathapuram",
    "dhanushkodi": "Ramanathapuram",
    "paramakudi": "Ramanathapuram",

    # Thoothukkudi
    "thoothukkudi": "Thoothukkudi",
    "tuticorin": "Thoothukkudi",
    "thiruchendur": "Thoothukkudi",
    "tiruchendur": "Thoothukkudi",
    "kovilpatti": "Thoothukkudi",
    "kayalpattinam": "Thoothukkudi",

    # Cuddalore
    "cuddalore": "Cuddalore",
    "chidambaram": "Cuddalore",
    "virudhachalam": "Cuddalore",
    "panruti": "Cuddalore",

    # Tiruvannamalai
    "tiruvannamalai": "Tiruvannamalai",
    "thiruvannamalai": "Tiruvannamalai",
    "arani": "Tiruvannamalai",
    "polur": "Tiruvannamalai",

    # Ariyalur
    "ariyalur": "Ariyalur",
    "gangaikondacholapuram": "Ariyalur",
    "jayankondam": "Ariyalur",

    # Mayiladuthurai
    "mayiladuthurai": "Mayiladuthurai",
    "poombuhar": "Mayiladuthurai",
    "tharangambadi": "Mayiladuthurai",
    "tranquebar": "Mayiladuthurai",
    "sirkazhi": "Mayiladuthurai",

    # Thiruvallur
    "thiruvallur": "Thiruvallur",
    "tiruvallur": "Thiruvallur",
    "tiruttani": "Thiruvallur",
    "thiruttani": "Thiruvallur",
    "avadi": "Thiruvallur",
    "ponneri": "Thiruvallur",

    # Tenkasi
    "tenkasi": "Tenkasi",
    "courtallam": "Tenkasi",
    "kutralam": "Tenkasi",
    "sankarankovil": "Tenkasi",

    # Tirunelveli
    "tirunelveli": "Tirunelveli",
    "nellai": "Tirunelveli",
    "ambasamudram": "Tirunelveli",
    "kallidaikurichi": "Tirunelveli",

    # Salem
    "salem": "Salem",
    "mettur": "Salem",
    "attur": "Salem",
    "yercaud": "Salem",

    # Virudhunagar
    "virudhunagar": "Virudhunagar",
    "srivilliputhur": "Virudhunagar",
    "srivilliputtur": "Virudhunagar",
    "sivakasi": "Virudhunagar",
    "rajapalayam": "Virudhunagar",

    # Kancheepuram
    "kancheepuram": "Kancheepuram",
    "kanchipuram": "Kancheepuram",
    "kanchi": "Kancheepuram",
    "sriperumbudur": "Kancheepuram",
    "walajabad": "Kancheepuram",
}

PLACE_EXTRACTION_PATTERNS = [
    # "at Pollachi", "in Erode", "near Madurai"
    re.compile(r"\b(?:at|in|near)\s+([a-zA-Z\u0B80-\u0BFF]+(?:\s+[a-zA-Z\u0B80-\u0BFF]+)?)\b", re.IGNORECASE),
    # "Pollachi-la", "Erode-le", "Chennai la", "Madurai-il"
    re.compile(r"\b([a-zA-Z\u0B80-\u0BFF]+)(?:-la|-le|-il|\s+la|\s+le|\s+il)\b", re.IGNORECASE),
    # Tamil suffix "-இல்", "-ல்"
    re.compile(r"\b([a-zA-Z\u0B80-\u0BFF]+)(?:இல்|ல்)\b"),
]


def extract_place_candidates(text: str, place_name: Optional[str] = None) -> List[str]:
    """Extract candidate place names from structured place_name and query text regex."""
    candidates: List[str] = []
    seen = set()

    def _add(cand: Optional[str]):
        if not cand:
            return
        c_clean = cand.strip(" ?.,!;:-\"'()[]{}")
        if len(c_clean) >= 3 and c_clean.lower() not in seen:
            seen.add(c_clean.lower())
            candidates.append(c_clean)

    # 1. Primary place name if given by Gemini
    _add(place_name)

    # 2. Regex matches from query text
    for pattern in PLACE_EXTRACTION_PATTERNS:
        for match in pattern.finditer(text):
            cand = match.group(1).strip()
            # Exclude question words or common non-place tokens
            if cand.lower() not in {"what", "which", "where", "when", "who", "temple", "kovil", "amman", "festival", "history"}:
                _add(cand)

    # 3. Check for standalone town/district alias matches in tokens
    tokens = re.findall(r"[a-zA-Z\u0B80-\u0BFF]+", text)
    for tok in tokens:
        if tok.lower() in TOWN_DISTRICT_ALIASES and tok.lower() not in {"what", "which", "where", "when", "who", "temple", "kovil", "amman", "festival", "history"}:
            _add(tok)

    return candidates


def resolve_place(
    text: str,
    place_name: Optional[str] = None,
    db: Optional[Session] = None,
) -> ResolvedPlace:
    """Resolve candidate place against LGD locations hierarchy and town aliases.

    Returns: ResolvedPlace containing district_id, district_name,
    subdistrict_id, local_body_id, and matched term.
    """
    candidates = extract_place_candidates(text, place_name)
    if not candidates:
        return ResolvedPlace(None, None, None, None, None, None, None)

    for cand in candidates:
        cand_lower = cand.lower().strip()

        # 1. Check Town / Locality alias map
        if cand_lower in TOWN_DISTRICT_ALIASES:
            dist_target = TOWN_DISTRICT_ALIASES[cand_lower]
            if db:
                row = db.execute(
                    sa_text("""
                        SELECT location_id, name_en FROM locations
                        WHERE level = 'district' AND lower(name_en) = lower(:d)
                        LIMIT 1
                    """),
                    {"d": dist_target},
                ).mappings().fetchone()
                if row:
                    # Also check if cand matches a subdistrict or local body
                    sub_row = db.execute(
                        sa_text("""
                            SELECT location_id, level, name_en, parent_id
                            FROM locations
                            WHERE lower(name_en) = lower(:c)
                            LIMIT 1
                        """),
                        {"c": cand_lower},
                    ).mappings().fetchone()
                    sub_id = sub_row["location_id"] if sub_row and sub_row["level"] == "subdistrict" else None
                    sub_name = sub_row["name_en"] if sub_id else None
                    lb_id = sub_row["location_id"] if sub_row and sub_row["level"] == "local_body" else None
                    lb_name = sub_row["name_en"] if lb_id else None

                    return ResolvedPlace(
                        district_id=row["location_id"],
                        district_name=row["name_en"],
                        subdistrict_id=sub_id,
                        subdistrict_name=sub_name,
                        local_body_id=lb_id,
                        local_body_name=lb_name,
                        matched_term=cand,
                    )

        # 2. Direct database query in locations table
        if db:
            # Check district level
            dist_row = db.execute(
                sa_text("""
                    SELECT location_id, name_en, name_ta
                    FROM locations
                    WHERE level = 'district'
                      AND (lower(name_en) = lower(:c) OR lower(COALESCE(name_ta, '')) = lower(:c))
                    LIMIT 1
                """),
                {"c": cand_lower},
            ).mappings().fetchone()
            if dist_row:
                return ResolvedPlace(
                    district_id=dist_row["location_id"],
                    district_name=dist_row["name_en"],
                    subdistrict_id=None,
                    subdistrict_name=None,
                    local_body_id=None,
                    local_body_name=None,
                    matched_term=cand,
                )

            # Check subdistrict level
            sub_row = db.execute(
                sa_text("""
                    SELECT sub.location_id AS subdistrict_id, sub.name_en AS subdistrict_name,
                           d.location_id AS district_id, d.name_en AS district_name
                    FROM locations sub
                    JOIN locations d ON sub.parent_id = d.location_id
                    WHERE sub.level = 'subdistrict'
                      AND (lower(sub.name_en) = lower(:c) OR lower(COALESCE(sub.name_ta, '')) = lower(:c))
                    LIMIT 1
                """),
                {"c": cand_lower},
            ).mappings().fetchone()
            if sub_row:
                return ResolvedPlace(
                    district_id=sub_row["district_id"],
                    district_name=sub_row["district_name"],
                    subdistrict_id=sub_row["subdistrict_id"],
                    subdistrict_name=sub_row["subdistrict_name"],
                    local_body_id=None,
                    local_body_name=None,
                    matched_term=cand,
                )

            # Check local_body level
            lb_row = db.execute(
                sa_text("""
                    SELECT lb.location_id AS local_body_id, lb.name_en AS local_body_name,
                           d.location_id AS district_id, d.name_en AS district_name
                    FROM locations lb
                    LEFT JOIN locations d ON lb.parent_id = d.location_id
                    WHERE lb.level = 'local_body'
                      AND (lower(lb.name_en) = lower(:c) OR lower(COALESCE(lb.name_ta, '')) = lower(:c))
                    LIMIT 1
                """),
                {"c": cand_lower},
            ).mappings().fetchone()
            if lb_row:
                return ResolvedPlace(
                    district_id=lb_row["district_id"],
                    district_name=lb_row["district_name"],
                    subdistrict_id=None,
                    subdistrict_name=None,
                    local_body_id=lb_row["local_body_id"],
                    local_body_name=lb_row["local_body_name"],
                    matched_term=cand,
                )

    return ResolvedPlace(None, None, None, None, None, None, None)
