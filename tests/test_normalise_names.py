"""Tier-1 name cleaning (formatting only) and the review-candidate similarity."""

import pytest
from rapidfuzz import fuzz

from src.normalise.names import build_lexicon, clean_name, core, entity_type, name_key, slug

LEXICON = build_lexicon(
    {
        "Department of Telecommunications": 86,
        "Department of Health & Family Welfare": 154,
        "Department of Bio Technology": 63,
        "Ministry of Skill Development and Entrepreneurship": 48,
        "Ministry of Electronics & Information Technology": 51,
    }
)


@pytest.mark.parametrize(
    ("printed", "expected"),
    [
        ("Department of Telecommunica tions", "Department of Telecommunications"),  # word broken across lines
        ("Department of Telecommunication s", "Department of Telecommunications"),
        (
            "Ministry of Skill Development and Entrepreneurs hip",
            "Ministry of Skill Development and Entrepreneurship",
        ),
        ("Department of Revenue*", "Department of Revenue"),  # footnote mark
        ("Ministry of Railways ( Railway Board)", "Ministry of Railways (Railway Board)"),
        (
            "Ministry of Micro Small and Medium - Enterprises",
            "Ministry of Micro Small and Medium Enterprises",
        ),
        ("Department of Health & Family Welfare", "Department of Health & Family Welfare"),  # '&' never glued
        (
            "Ministry of Electronics & Information Technology",
            "Ministry of Electronics & Information Technology",
        ),
        ("Department of Bio Technology", "Department of Bio Technology"),  # two trusted words: not joined
    ],
)
def test_clean_name_removes_formatting_artefacts_only(printed, expected):
    assert clean_name(printed, LEXICON) == expected


def test_keys_ignore_case_ampersand_and_punctuation():
    assert name_key("Department of Health & Family Welfare", LEXICON) == name_key(
        "Department of Health and Family Welfare", LEXICON
    )
    assert name_key("Ministry of AYUSH", LEXICON) == name_key("Ministry of Ayush", LEXICON)


def test_spelling_variants_are_not_merged_by_cleaning():
    # these are alias decisions (Tier 2), not formatting
    assert name_key("Ministry of Labor and Employment", LEXICON) != name_key(
        "Ministry of Labour and Employment", LEXICON
    )
    assert name_key("Department of Biotechnology", LEXICON) != name_key(
        "Department of Bio Technology", LEXICON
    )


def test_core_similarity_separates_known_pairs():
    def score(a, b):
        return fuzz.token_sort_ratio(core(name_key(a, LEXICON)), core(name_key(b, LEXICON)))

    assert score("Department of Heavy Industry", "Ministry of Heavy Industries") >= 85
    assert (
        score("Department of Drinking Water and Sanitation", "Ministry of Drinking Water and Sanitation")
        == 100
    )
    assert score("Department of Defence", "Department of Defence Finance") < 85
    assert (
        score(
            "Department of Financial Services (Banking Division)",
            "Department of Financial Services (Insurance Division)",
        )
        < 85
    )


@pytest.mark.parametrize(
    ("name", "kind"),
    [
        ("Ministry of Coal", "ministry"),
        ("Department for Promotion of Industry and Internal Trade", "department"),
        ("Legislative Department", "department"),
        ("Unique Identification Authority of India", "organisation"),
    ],
)
def test_entity_type(name, kind):
    assert entity_type(name) == kind


def test_slug():
    assert (
        slug("O/o the Comptroller & Auditor General of India")
        == "o_o_the_comptroller_and_auditor_general_of_india"
    )
