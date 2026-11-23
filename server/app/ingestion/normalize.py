"""Text and place-name normalisation.

Method
------
1. ``clean_text``: Unicode NFC, unify quotes/dashes, collapse whitespace (incl. NBSP,
   zero-width chars kept only where Tamil needs them: ZWJ/ZWNJ are preserved).
2. ``strip_admin_notes``: remove administrative annotations that ASI lists append to
   names, e.g. "Block No. IV/13", "(in Block No. II/7)", "S.F. No. 123/4",
   "Survey No. 45", "(Protected area ...)". Patterns are in ADMIN_NOTE_PATTERNS.
3. ``match_key``: matching form of a name for fuzzy comparison: clean -> lower ->
   strip punctuation -> fold common romanisation variants of Tamil/Sanskrit words
   (ee->i, oo->u, w->v, sh->s, th->t, dh->d, double consonants -> single) and map
   generic synonyms (kovil/koil -> temple). Tamil-script text is kept as is.
4. ``canonical_district``: district spelling -> LGD district via DISTRICT_ALIASES
   (Trichy/Tiruchi -> Tiruchirappalli, Kanchipuram -> Kancheepuram, Tuticorin ->
   Thoothukkudi, ...), with a rapidfuzz fallback (>= 90) for typos.
5. ``match_locality``: locality -> LGD local body / village panchayat by rapidfuzz
   ``WRatio`` restricted to the record's district and its post-bifurcation successor
   districts (DISTRICT_SUCCESSORS). A match in a successor district re-assigns the
   record's district (e.g. Mamallapuram listed under Kancheepuram -> Chengalpattu).
"""

from __future__ import annotations

import re
import unicodedata
from typing import Optional

from rapidfuzz import fuzz, process

from app.ingestion import config as C

_WS = re.compile(r"[\s  -​  　]+")
_QUOTES = str.maketrans({"‘": "'", "’": "'", "“": '"', "”": '"',
                         "–": "-", "—": "-", "−": "-"})

ADMIN_NOTE_PATTERNS = [
    r"\(\s*in\s+block\s+no\.?\s*[ivxlc]+\s*/\s*\d+[a-z]?\s*\)",
    r"\bblock\s+no\.?\s*[ivxlc]+\s*/\s*\d+[a-z]?\b",
    r"\(?\s*\bs\.?\s*f\.?\s*nos?\.?\s*[\d/,\s&and-]+\)?",
    r"\(?\s*\bsurvey\s+nos?\.?\s*[\d/,\s&and-]+\)?",
    r"\(?\s*\br\.?\s*s\.?\s*nos?\.?\s*[\d/,\s&and-]+\)?",
    r"\(\s*protected\s+area[^)]*\)",
    r"\(\s*\d+\s*\)\s*$",                      # trailing footnote markers "(2)"
    r"\[\s*missing\s*\]",
]
_ADMIN_RE = [re.compile(p, re.I) for p in ADMIN_NOTE_PATTERNS]

TAMIL_RE = re.compile(r"[஀-௿]")
KANNADA_RE = re.compile(r"[ಀ-೿]")

# generic-word synonyms applied in match_key (after folding)
SYNONYMS = {
    "kovil": "temple", "koil": "temple", "kovl": "temple", "alayam": "temple", "temples": "temple",
    "kotai": "fort", "kottai": "fort", "fortress": "fort", "forts": "fort",
    "mandapam": "mandapa", "mantapa": "mandapa", "mantapam": "mandapa",
    "masjid": "mosque", "st": "saint", "sri": "", "shri": "", "arulmigu": "", "thiru": "tiru",
}
STOPWORDS = {"the", "of", "at", "and", "in", "near", "a", "an", "with"}
_FOLDS = [("ee", "i"), ("oo", "u"), ("aa", "a"), ("sh", "s"), ("zh", "l"), ("th", "t"), ("dh", "d"),
          ("bh", "b"), ("kh", "k"), ("ph", "p"), ("gh", "g"), ("w", "v"), ("y", "i"), ("z", "s")]
_DOUBLE = re.compile(r"([bcdfgjklmnpqrstvz])\1")


def clean_text(s: Optional[str]) -> str:
    if not s:
        return ""
    s = unicodedata.normalize("NFC", str(s)).translate(_QUOTES)
    s = _WS.sub(" ", s)
    return s.strip(" ,;:-")


def strip_admin_notes(s: str) -> str:
    out = s
    for rx in _ADMIN_RE:
        out = rx.sub(" ", out)
    out = re.sub(r"\(\s*\)", " ", out)
    return clean_text(out)


def fold_latin(token: str) -> str:
    t = token
    for a, b in _FOLDS:
        t = t.replace(a, b)
    t = _DOUBLE.sub(r"\1", t)
    return t


def match_key(name: str) -> str:
    """Matching form of a name (see module docstring, step 3)."""
    s = clean_text(name).lower()
    s = re.sub(r"[^\w\s஀-௿ಀ-೿]", " ", s)
    out = []
    for tok in s.split():
        if tok in STOPWORDS:
            continue
        if not TAMIL_RE.search(tok) and not KANNADA_RE.search(tok):
            tok = SYNONYMS.get(tok, tok)
            tok = fold_latin(tok)
            tok = SYNONYMS.get(tok, tok)
        if tok:
            out.append(tok)
    return " ".join(out)


def has_tamil(s: Optional[str]) -> bool:
    return bool(s and TAMIL_RE.search(s))


# ----------------------------------------------------------------- districts
_DISTRICT_KEYS = {fold_latin(re.sub(r"[^a-z ]", "", k)): v for k, v in C.DISTRICT_ALIASES.items()}


def canonical_district(raw: Optional[str]) -> Optional[str]:
    """Map any district spelling to the canonical LGD English name (or None)."""
    if not raw:
        return None
    s = clean_text(raw).lower()
    s = re.sub(r"\bdistrict\b|\bdist\.?\b", " ", s)
    s = re.sub(r"[^a-z ]", " ", s)
    s = " ".join(s.split())
    if s in C.DISTRICT_ALIASES:
        return C.DISTRICT_ALIASES[s]
    f = fold_latin(s)
    if f in _DISTRICT_KEYS:
        return _DISTRICT_KEYS[f]
    hit = process.extractOne(f, list(_DISTRICT_KEYS), scorer=fuzz.ratio, score_cutoff=90)
    return _DISTRICT_KEYS[hit[0]] if hit else None


def districts_compatible(a: Optional[str], b: Optional[str]) -> bool:
    """True if a == b or one is a post-bifurcation successor of the other."""
    if not a or not b:
        return False
    if a == b:
        return True
    return b in C.DISTRICT_SUCCESSORS.get(a, []) or a in C.DISTRICT_SUCCESSORS.get(b, [])


# ----------------------------------------------------------------- localities
class LocalityIndex:
    """Fuzzy index of LGD local bodies / village panchayats grouped by district."""

    def __init__(self, locations: list[dict], min_score: float = 88.0):
        self.min_score = min_score
        self.by_district: dict[str, list[dict]] = {}
        for loc in locations:
            if loc.get("level") in ("local_body", "subdistrict") and loc.get("district"):
                self.by_district.setdefault(loc["district"], []).append(loc)
        self._keys = {d: [match_key(_strip_lb_suffix(l["name_en"])) for l in locs]
                      for d, locs in self.by_district.items()}

    def match(self, locality: str, district: Optional[str]) -> Optional[dict]:
        """Return {'location', 'district', 'score'} for the best match, or None."""
        if not locality or not district:
            return None
        q = match_key(locality)
        if not q:
            return None
        best = None
        for d in [district] + C.DISTRICT_SUCCESSORS.get(district, []):
            keys = self._keys.get(d)
            if not keys:
                continue
            hit = process.extractOne(q, keys, scorer=fuzz.WRatio, score_cutoff=self.min_score)
            if hit and (best is None or hit[1] > best["score"]
                        or (hit[1] == best["score"] and _level_rank(self.by_district[d][hit[2]]) < _level_rank(best["location"]))):
                best = {"location": self.by_district[d][hit[2]], "district": d, "score": float(hit[1])}
        return best


def _strip_lb_suffix(name: str) -> str:
    return re.sub(r"\b(corporation|municipality|town panchayat|village panchayat|panchayat|"
                  r"city municipal corporation|greater)\b", " ", name, flags=re.I)


def _level_rank(loc: dict) -> int:
    # prefer local bodies over sub-districts on ties
    return 0 if loc.get("level") == "local_body" else 1
