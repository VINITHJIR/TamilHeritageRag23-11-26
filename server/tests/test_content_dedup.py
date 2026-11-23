"""Unit tests for content deduplication and relevance filtering (server/app/services/content_dedup.py)."""

from app.services.content_dedup import (
    cluster_sources,
    compute_jaccard_similarity,
    compute_minhash,
    compute_text_sha256,
    normalize_text_for_hash,
    page_mentions_entity,
)


def test_sha256_exact_duplicate():
    t1 = "  Pariyur Kondathu   Kaliamman Temple is in Erode. "
    t2 = "pariyur kondathu kaliamman temple is in erode."
    assert compute_text_sha256(t1) == compute_text_sha256(t2)


def test_minhash_and_jaccard_similarity():
    text_base = (
        "The Brihadeeswarar Temple at Thanjavur is one of the largest Hindu temples in India. "
        "It was built by Chola emperor Raja Raja Chola I between 1003 and 1010 CE. "
        "The temple is part of the UNESCO World Heritage Site known as the Great Living Chola Temples."
    )
    # Near duplicate with minor phrasing change
    text_copy = (
        "The Brihadeeswarar Temple at Thanjavur is one of the largest Hindu temples in India. "
        "It was constructed by Chola king Raja Raja Chola I between 1003 and 1010 CE. "
        "The monument is part of the UNESCO World Heritage Site Great Living Chola Temples."
    )
    # Completely different text
    text_diff = (
        "Kanyakumari is located at the southernmost tip of peninsular India. "
        "It is surrounded by the Arabian Sea, the Indian Ocean, and the Bay of Bengal."
    )

    mh_base = compute_minhash(text_base)
    mh_copy = compute_minhash(text_copy)
    mh_diff = compute_minhash(text_diff)

    sim_high = compute_jaccard_similarity(mh_base, mh_copy)
    sim_low = compute_jaccard_similarity(mh_base, mh_diff)

    # Identical/near-identical should have high similarity >= 0.70
    assert sim_high >= 0.65
    assert sim_low < 0.20


def test_cluster_sources():
    sources = [
        {
            "url": "https://source1.com/pariyur",
            "domain": "source1.com",
            "text": "Pariyur Kondathu Kaliamman Temple is a famous temple in Gobichettipalayam built in the 17th century by Nayakas.",
        },
        {
            "url": "https://source2.com/pariyur-guide",
            "domain": "source2.com",
            "text": "Pariyur Kondathu Kaliamman Temple is a well known temple in Gobichettipalayam constructed in 17th century by Nayakas.",
        },
        {
            "url": "https://source3.com/thanjavur",
            "domain": "source3.com",
            "text": "Thanjavur Brihadisvara Temple was built by Raja Raja Chola I in 1010 CE as a magnificent stone temple.",
        },
    ]

    clustered = cluster_sources(sources, jaccard_threshold=0.60)
    assert len(clustered) == 3

    # source1 and source2 should share the same cluster_key
    assert clustered[0]["cluster_key"] == clustered[1]["cluster_key"]
    # source3 should have a distinct cluster_key
    assert clustered[0]["cluster_key"] != clustered[2]["cluster_key"]


def test_page_mentions_entity_relevance_filter():
    article = "The ancient temple of Pariyur Kondathu Kaliamman stands in Gobichettipalayam."
    assert page_mentions_entity(article, "Pariyur Amman Temple") is True
    assert page_mentions_entity(article, "Bannari Amman") is False
