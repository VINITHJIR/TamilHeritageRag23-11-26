"""Rule-based heritage-category classification.

Method
------
An ordered list of keyword rules (RULES) is matched against the record's name, plus
locality as a weak signal. Each rule = (rule_id, category, regex). Patterns cover English,
Tamil script and common Tamil transliterations (kovil/koil, kottai, kulam, ...).

* ``categories``      = every category whose rule matched (multi-label), in rule order.
* ``primary_category``= category of the FIRST matching rule. Order therefore encodes
  precedence: specific religious/structural types (church, mosque, dargah, jain_site,
  tomb_cemetery, megalithic_site, rock_cut, cave) precede broad ones (fort, palace,
  temple, ...), and weak signals (inscription, archaeological_site) come last, so
  "Temple with inscriptions" -> primary temple, categories [temple, inscription].
* ASI records always receive the extra tag 'asi_protected'.
* No rule matched -> primary_category 'other_monument' and a review_queue item
  (kind='category'); ``rule`` is None.
* The function returns which rule fired and the matched text, so the report
  ``category_rules_hit.csv`` can document rule coverage for the paper.
"""

from __future__ import annotations

import re
from typing import Optional

from app.ingestion.normalize import clean_text

# (rule_id, category, pattern) -- ORDER MATTERS (precedence for primary_category)
RULES: list[tuple[str, str, str]] = [
    ("R01", "church", r"\bchurch\b|cathedral|basilica|\bchapel\b|தேவாலய|சர்ச்|மாதா\s*கோ"),
    ("R02", "mosque", r"\bmosque\b|masjid|\bjama\b|பள்ளிவாச|மசூதி"),
    ("R03", "dargah", r"darga[h]?\b|durgah|dargha|தர்கா"),
    ("R04", "jain_site", r"\bjain|\bjaina\b|tirthankara|tirthankar|mahavir|parsvanat|parshvanat|"
                         r"\bsamana\b|\bamanar\b|சமண|அமணர்"),
    ("R05", "tomb_cemetery", r"\btomb|\bgraves?\b|cemetery|cemetry|cenotaph|sepulchre|burial ground|"
                             r"கல்லறை|இடுகாடு"),
    ("R06", "megalithic_site", r"megalith|dolmen|\bcairns?\b|menhir|\bcists?\b|urn burial|burial site|"
                               r"stone circle|பெருங்கற்|முதுமக்கள் தாழி"),
    ("R07", "rock_cut", r"rock[\s-]*cut|cave temple|kudaivarai|kudavarai|monolith|\bratha|\brathas\b|"
                        r"குடைவரை|ஒற்றைக்கல்"),
    ("R08", "cave", r"\bcaves?\b|cavern|rock shelter|\bguha|குகை"),
    ("R09", "fort", r"\bforts?\b|fortress|fortification|kottai|\bkota\b|bastion|rampart|கோட்டை"),
    ("R10", "palace", r"palace|\bmahal\b|aranmanai|அரண்மனை"),
    ("R11", "temple", r"temple|\bkovil\b|\bkoil\b|\bkoyil\b|shrine|mandapa|mantapa|gopura|vimana|"
                      r"\b\w*(isvara|eswara|eeswara|iswara|eshwara)r?\b|perumal|\bamman\b|\bsiva\b|"
                      r"vishnu|\bdevi\b|கோயில்|கோவில்|ஆலயம்|ஈஸ்வர|பெருமாள்"),
    ("R12", "step_well_tank", r"\btank\b|step[\s-]*well|\bkulam\b|teppakulam|pushkarini|\btirtham\b|"
                              r"\bwell\b|குளம்|தெப்பக்குளம்|கிணறு"),
    ("R13", "museum", r"museum|அருங்காட்சியக"),
    ("R14", "colonial_building", r"\bbuilding\b|\bhouse\b|\bbungalow|residency|\boffice\b|\bcourt\b|"
                                 r"college|clock tower|light ?house|flag ?staff|\bbritish\b|\bdutch\b|"
                                 r"\bdanish\b|portuguese|\bfrench\b|\bhall\b|\bgate(way)?\b"),
    ("R15", "memorial", r"memorial|\bstatue\b|\bpillar\b|\bcolumn\b|\bobelisk\b|நினைவு"),
    ("R16", "inscription", r"inscription|inscribed|\bedicts?\b|kalvettu|கல்வெட்டு"),
    ("R17", "archaeological_site", r"excavat|\bmound\b|\bmedu\b|\bmedus\b|ancient (site|habitation)|"
                                   r"archaeolog|\bruins?\b|\bremains\b|\bsite\b|habitation|"
                                   r"தொல்லியல்|அகழாய்வு|மேடு"),
]
_COMPILED = [(rid, cat, re.compile(p, re.I)) for rid, cat, p in RULES]

FALLBACK = "other_monument"


def classify(name: str, locality: str = "", is_asi: bool = False, kind: Optional[str] = None) -> dict:
    """Classify one record. Returns
    {primary_category, categories, rule, rule_match, needs_review}."""
    text = clean_text(name)
    hits: list[tuple[str, str, str]] = []
    for rid, cat, rx in _COMPILED:
        m = rx.search(text)
        if m and cat not in [h[1] for h in hits]:
            hits.append((rid, cat, m.group(0)))
    # ASI 'site' tables (archaeological sites & remains) are archaeological even when
    # the name only says e.g. "Prehistoric site".
    if not hits and kind == "site":
        hits.append(("R17k", "archaeological_site", "kind=site"))
    if hits:
        primary, rule, match = hits[0][1], hits[0][0], hits[0][2]
        cats = [h[1] for h in hits]
        needs_review = False
    else:
        primary, rule, match, cats, needs_review = FALLBACK, None, None, [FALLBACK], True
    if is_asi and "asi_protected" not in cats:
        cats.append("asi_protected")
    return {"primary_category": primary, "categories": cats, "rule": rule,
            "rule_match": match, "rules_all": [h[0] for h in hits], "needs_review": needs_review}
