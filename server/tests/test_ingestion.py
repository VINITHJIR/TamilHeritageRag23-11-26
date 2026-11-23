"""Unit test suite for TamilHeritage-GPT ingestion modules.

Covers:
  1. normalize: Unicode NFC, whitespace cleanup, administrative note stripping,
     district alias resolution, and phonetic match keys.
  2. classify: Ordered keyword rules across English, Tamil script, and transliterations;
     ASI protected tagging; fallback to other_monument; rule recording.
  3. resolve: Multi-feature entity resolution (name similarity, district compatibility,
     Haversine geo scoring, missing-coord re-weighting, threshold tiers).
  4. chunker: Token-bounded sentence packing (<=350 tokens, 50 overlap) with Tamil
     sentence punctuation and abbreviation safety; structured fact cards.
  5. ids: District code3 mapping (all 38 districts) and site_id persistence idempotency.
"""

import pytest
from app.ingestion.config import DISTRICT_CODE3, ResolveConfig
from app.ingestion.chunker import (
    chunk_text,
    split_sentences,
    build_fact_cards,
)
from app.ingestion.classify import classify, FALLBACK
from app.ingestion.ids import (
    IdAllocator,
    code3_for,
)
from app.ingestion.normalize import (
    canonical_district,
    clean_text,
    match_key,
    strip_admin_notes,
)
from app.ingestion.resolve import (
    decide,
    geo_score,
    haversine_m,
    pair_sim,
    score_pair,
)


# =====================================================================
# 1. NORMALIZE TESTS
# =====================================================================
class TestNormalize:
    def test_clean_text_unicode_nfc_and_whitespace(self):
        # Combining characters, non-breaking spaces, curly quotes
        raw = "தஞ்சை பெரிய\u00a0கோவில் ‘Brihadeeswarar’ – Temple"
        cleaned = clean_text(raw)
        assert "\u00a0" not in cleaned
        assert "‘" not in cleaned and "'" in cleaned
        assert cleaned == "தஞ்சை பெரிய கோவில் 'Brihadeeswarar' - Temple"

    def test_strip_admin_notes(self):
        cases = [
            ("Fort St. George (in Block No. II/7)", "Fort St. George"),
            ("Clive's House Block No. IV/13", "Clive's House"),
            ("Prehistoric Site (Survey No. 45/2)", "Prehistoric Site"),
            ("Jain Beds S.F. Nos. 12, 13 and 14", "Jain Beds"),
            ("Gingee Fort (Protected area)", "Gingee Fort"),
        ]
        for raw, expected in cases:
            res = strip_admin_notes(raw)
            assert res == expected, f"Expected '{expected}', got '{res}'"

    def test_district_alias_canonicalization(self):
        assert canonical_district("Trichy") == "Tiruchirappalli"
        assert canonical_district("Tiruchi") == "Tiruchirappalli"
        assert canonical_district("Kanchipuram") == "Kancheepuram"
        assert canonical_district("Tuticorin") == "Thoothukkudi"
        assert canonical_district("Tanjore") == "Thanjavur"
        assert canonical_district("Madras") == "Chennai"
        assert canonical_district("Nellai") == "Tirunelveli"

    def test_match_key_folding(self):
        # Romanisation folding & synonym mapping
        k1 = match_key("Peruvudaiyar Kovil")
        k2 = match_key("Peruvudaiyar Koil")
        assert k1 == k2
        assert "temple" in k1

        # Double consonants folding
        assert match_key("Thanjavur") == match_key("Tanjavur")


# =====================================================================
# 2. CLASSIFY TESTS
# =====================================================================
class TestClassify:
    def test_ordered_keyword_rules(self):
        cases = [
            ("St. Mary's Church", "church"),
            ("Jumma Masjid, Vellore", "mosque"),
            ("Nagore Dargah", "dargah"),
            ("Sittanavasal Jain Beds and Cave", "jain_site"),
            ("British Cemetery and Tombs", "tomb_cemetery"),
            ("Megalithic Urn Burial Site", "megalithic_site"),
            ("Tiger Cave Rock-cut Mandapa", "rock_cut"),
            ("Vellore Fort and Bastion", "fort"),
            ("Padmanabhapuram Palace Complex", "palace"),
            ("Brihadeeswarar Temple", "temple"),
            ("தெப்பக்குளம் (Mariamman Teppakulam)", "step_well_tank"),
            ("Government Museum, Egmore", "museum"),
            ("Rajendra Chola Memorial Pillar", "memorial"),
        ]
        for name, expected_primary in cases:
            res = classify(name)
            assert res["primary_category"] == expected_primary, (
                f"For '{name}': expected {expected_primary}, got {res['primary_category']}"
            )
            assert res["rule"] is not None

    def test_asi_protected_tag(self):
        res = classify("Gingee Fort Complex", is_asi=True)
        assert "asi_protected" in res["categories"]
        assert "fort" in res["categories"]

    def test_unmatched_fallback_to_other_monument(self):
        res = classify("Random Nameless Structure 123")
        assert res["primary_category"] == FALLBACK
        assert res["needs_review"] is True
        assert res["rule"] is None


# =====================================================================
# 3. RESOLVE (ENTITY RESOLUTION) TESTS
# =====================================================================
class TestResolve:
    @pytest.fixture
    def cfg(self):
        return ResolveConfig(
            w_name=0.60,
            w_district=0.25,
            w_geo=0.15,
            auto_link=0.90,
            review=0.75
        )

    def test_haversine_distance(self):
        # Thanjavur temple coords
        lat1, lon1 = 10.7828, 79.1318
        lat2, lon2 = 10.7830, 79.1320
        d = haversine_m(lat1, lon1, lat2, lon2)
        assert 0 < d < 100  # under 100 meters

    def test_geo_scoring_tiers(self, cfg):
        assert geo_score(150.0, cfg) == 1.0     # < 200m -> 1.0
        assert geo_score(2500.0, cfg) == 0.0    # > 2km -> 0.0
        # Linear between 200m and 2000m
        mid = geo_score(1100.0, cfg)
        assert 0.45 < mid < 0.55

    def test_synthetic_auto_link(self, cfg):
        # Candidate and record describe Brihadeeswarar Temple in Thanjavur
        record = {
            "name_en": "Brihadeeswara Temple, Thanjavur",
            "district": "Thanjavur",
            "lat": 10.7828,
            "lon": 79.1318,
        }
        candidate = {
            "id": "TN-TNJ-0001",
            "names": ["Brihadisvara Temple", "Big Temple", "Peruvudaiyar Kovil"],
            "district": "Thanjavur",
            "lat": 10.7827,
            "lon": 79.1319,
        }
        res = score_pair(record, candidate, cfg)
        assert res["score"] >= cfg.auto_link, f"Expected auto-link (>=0.90), got {res['score']}"
        assert decide(res["score"], cfg) == "link"

    def test_synthetic_review_match(self, cfg):
        # Partial match needing review (0.75 - 0.90)
        record = {
            "name_en": "Kailasanathar Shrine",
            "district": "Kancheepuram",
        }
        candidate = {
            "id": "TN-KPM-0001",
            "names": ["Kailasanatha Temple Complex"],
            "district": "Kancheepuram",
        }
        res = score_pair(record, candidate, cfg)
        assert cfg.review <= res["score"] < cfg.auto_link
        assert decide(res["score"], cfg) == "review"

    def test_synthetic_distinct_new_site(self, cfg):
        # Different monuments in same district
        record = {
            "name_en": "Shore Temple",
            "district": "Chengalpattu",
            "lat": 12.6167,
            "lon": 80.1994,
        }
        candidate = {
            "id": "TN-CGL-0002",
            "names": ["Five Rathas"],
            "district": "Chengalpattu",
            "lat": 12.6111,
            "lon": 80.1928,
        }
        res = score_pair(record, candidate, cfg)
        assert res["score"] < cfg.review
        assert decide(res["score"], cfg) == "new"


# =====================================================================
# 4. CHUNKER TESTS
# =====================================================================
class TestChunker:
    def test_tamil_sentence_splitting_and_abbreviations(self):
        # Abbreviation safety: கி.பி. should not split sentence
        text = "இக்கோயில் கி.பி. 1010-இல் இராஜராஜ சோழனால் கட்டப்பட்டது. இது உலகப் பாரம்பரியச் சின்னமாகும்."
        sents = split_sentences(text)
        assert len(sents) == 2
        assert "கி.பி. 1010-இல்" in sents[0]

    def test_token_bounded_chunking(self):
        # Generate synthetic multi-sentence paragraph
        para = " ".join([f"This is heritage sentence number {i} with notable architectural history." for i in range(40)])
        # Use simple word count as mock counter for fast test
        chunks = chunk_text(para, count=lambda s: len(s.split()))
        assert len(chunks) > 1
        for ch in chunks:
            assert len(ch.split()) <= 350

    def test_build_fact_cards_from_db_fields(self):
        site_record = {
            "site_id": "TN-TNJ-0001",
            "name_en": "Brihadeeswarar Temple",
            "name_ta": "பெருவுடையார் கோயில்",
            "primary_label_en": "Temple",
            "category_labels_en": ["Temple", "UNESCO Heritage"],
            "district_en": "Thanjavur",
            "period": "11th century CE",
            "dynasty": "Chola",
            "categories": ["temple", "unesco"],
            "aliases": [{"alias": "Big Temple", "lang": "en"}],
            "facts": [{"field": "built_by", "value": "Rajaraja Chola I", "lang": "en"}]
        }
        cards = build_fact_cards(site_record)
        assert len(cards) >= 1
        en_lang, en_text = next(c for c in cards if c[0] == "en")
        assert "Brihadeeswarar Temple" in en_text
        assert "Chola" in en_text
        assert "Thanjavur" in en_text
        assert "Big Temple" in en_text


# =====================================================================
# 5. IDS & CODE3 STABILITY TESTS
# =====================================================================
class TestIDs:
    def test_all_38_districts_present_and_format(self):
        assert len(DISTRICT_CODE3) == 38
        for name, code in DISTRICT_CODE3.items():
            assert len(code) == 3, f"Code '{code}' for '{name}' is not 3 letters"
            assert code.isupper(), f"Code '{code}' is not uppercase"

    def test_seed_district_codes_exact_match(self):
        # Must strictly preserve Level 1 seed codes
        assert code3_for("Thanjavur") == "TNJ"
        assert code3_for("Madurai") == "MDU"
        assert code3_for("Chennai") == "CHN"
        assert code3_for("Chengalpattu") == "CGL"
        assert code3_for("Erode") == "ERD"
        assert code3_for("Viluppuram") == "VPM"
        assert code3_for("Sivaganga") == "SVG"
        assert code3_for("Kanniyakumari") == "KNK"

    def test_id_allocator_stability(self):
        alloc = IdAllocator(used_ids=set(), persisted={})
        id1, is_new1 = alloc.allocate("asi|chennai:monument:1", "Thanjavur")
        assert id1 == "TN-TNJ-0001"
        assert is_new1 is True

        # Re-running with same key returns identical site_id
        id2, is_new2 = alloc.allocate("asi|chennai:monument:1", "Thanjavur")
        assert id2 == "TN-TNJ-0001"
        assert is_new2 is False

        # Next key in same district receives sequence 0002
        id3, is_new3 = alloc.allocate("asi|chennai:monument:2", "Thanjavur")
        assert id3 == "TN-TNJ-0002"
        assert is_new3 is True
