"""Mandate knowledge base: AoB parsing, build validation and lexical retrieval on synthetic inputs."""

import copy

import pandas as pd
import pytest

from src.knowledge.aob import _strip_refs, parse
from src.knowledge.build import resolve, validate
from src.knowledge.retrieve import LexicalRetriever, build_chunks, normalise


def _lines(page, text):
    return [(page, line) for line in text.strip().splitlines()]


AOB_SAMPLE = _lines(
    38,
    """
MINISTRY OF COMMUNICATIONS
B. DEPARTMENT OF POSTS
(DAK VIBHAG)
1. Execution of works pertaining to the Department of Posts.1
2. Posts, including Post Office Savings Banks
(Administration) and any agency function.2
2A. Matters related to India Post Payments Bank.3
3. Omitted. 4
""",
) + _lines(
    39,
    """
MINISTRY OF WATER AND
SANITATION 1
I. RURAL SUBJECTS
1. Rural water supply under section 3
PART II. URBAN
2. Health Scheme 3
DEPARTMENT OF SPACE
1. Space research.
""",
)


def test_aob_parser_sections_items_parts_and_footnote_references():
    items, sections = parse(AOB_SAMPLE)
    by = {(i.section, i.number): i for i in items}
    assert (
        by[("DEPARTMENT OF POSTS", "1")].text == "Execution of works pertaining to the Department of Posts."
    )
    assert by[("DEPARTMENT OF POSTS", "2")].text.endswith("(Administration) and any agency function.")
    assert by[("DEPARTMENT OF POSTS", "2A")].page == 38
    assert sections["DEPARTMENT OF POSTS"]["ministry"] == "MINISTRY OF COMMUNICATIONS"
    assert sections["DEPARTMENT OF POSTS"]["hindi_name"] == "DAK VIBHAG"
    assert "MINISTRY OF WATER AND SANITATION" in sections  # wrapped header joined, footnote mark dropped
    water = {i.number: i for i in items if i.section == "MINISTRY OF WATER AND SANITATION"}
    assert water["1"].part == "I. RURAL SUBJECTS" and water["1"].text.endswith("section 3")  # not a reference
    assert water["2"].part == "PART II. URBAN" and water["2"].text == "Health Scheme"  # spaced reference
    assert sections["DEPARTMENT OF SPACE"]["ministry"] == ""  # not under the previous ministry


def test_footnote_reference_stripping_keeps_numbers_that_belong_to_the_text():
    assert _strip_refs("Department of Posts.1") == "Department of Posts."
    assert _strip_refs("(UIDAI).8") == "(UIDAI)."
    assert _strip_refs("clause (1) of article 352") == "clause (1) of article 352"
    assert _strip_refs("Contributory Health Scheme 3") == "Contributory Health Scheme"


AOB_ITEMS = pd.DataFrame(
    [
        (
            "DEPARTMENT OF POSTS",
            "",
            "DAK VIBHAG",
            "2",
            False,
            38,
            "Posts, including Post Office Savings Banks.",
        ),
        ("DEPARTMENT OF POSTS", "", "DAK VIBHAG", "3", True, 38, "Omitted."),
        (
            "DEPARTMENT OF REVENUE",
            "",
            "RAJASWA VIBHAG",
            "4",
            False,
            71,
            "All questions relating to income tax.",
        ),
        ("DEPARTMENT OF REVENUE", "", "RAJASWA VIBHAG", "11", False, 71, "All matters relating to Customs."),
    ],
    columns=["section", "part", "section_hindi_name", "item", "omitted", "pdf_page", "text"],
)
URLS = pd.DataFrame(
    {
        "url": ["https://posts.example/", "https://tax.example/"],
        "checked_on": ["2026-10-21"] * 2,
        "http_status": [200, 403],
        "result": ["ok", "confirmed_by_official_link"],
        "note": ["", ""],
    }
)
MINISTRIES = pd.DataFrame(
    {
        "ministry_key": ["department_of_posts", "central_board_of_direct_taxes_income_tax"],
        "canonical_name": ["Department of Posts", "Central Board of Direct Taxes (Income Tax)"],
    }
)


def _entry(key, section, items, url, words):
    return {
        "ministry_key": key,
        "official_name": key,
        "parent_ministry": None,
        "kb_role": "aob_unit",
        "mapping_status": "direct",
        "aob_refs": [{"section": section, "items": items}],
        "official_urls": [url],
        "grievance_responsibilities": [{"text": "x", "aob_items": [f"{section}:{items[0]}"]}],
        "common_grievance_categories": [words[0]],
        "keywords": {"en": words, "hi": ["डाक"], "hinglish": [words[0] + " nahi aaya"]},
        "overlaps": [],
        "notes": "",
    }


KB = {
    "about": {"verified_on": "2026-10-21"},
    "departments": [
        _entry(
            "department_of_posts",
            "DEPARTMENT OF POSTS",
            ["2"],
            "https://posts.example/",
            ["speed post", "parcel", "post office"],
        ),
        _entry(
            "central_board_of_direct_taxes_income_tax",
            "DEPARTMENT OF REVENUE",
            ["4"],
            "https://tax.example/",
            ["income tax", "refund", "PAN"],
        ),
    ],
}


def test_valid_kb_passes_and_resolves_official_text():
    assert validate(KB, AOB_ITEMS, URLS, MINISTRIES) == []
    rec = {r["ministry_key"]: r for r in resolve(KB, AOB_ITEMS, URLS, MINISTRIES)}
    posts = rec["department_of_posts"]
    assert posts["official_mandate"][0]["text"] == "Posts, including Post Office Savings Banks."
    assert posts["official_mandate"][0]["pdf_page"] == 38 and posts["aob_hindi_names"] == ["Dak Vibhag"]
    assert posts["canonical_name"] == "Department of Posts"


@pytest.mark.parametrize(
    "mutate, message",
    [
        (lambda d: d.update(ministry_key="no_such_key"), "not a ministry_key"),
        (lambda d: d["aob_refs"][0].update(items=["3"]), "missing or omitted"),  # omitted item
        (
            lambda d: d["grievance_responsibilities"][0].update(aob_items=["DEPARTMENT OF REVENUE:11"]),
            "not among the entity's own AoB items",
        ),
        (lambda d: d.update(official_urls=["https://unchecked.example/"]), "URL without an accepted check"),
        (lambda d: d["keywords"].update(hi=["dak"]), "no Devanagari"),
        (
            lambda d: d.update(overlaps=[{"with": "unknown_key", "topic": "t", "note": "n"}]),
            "unknown KB entry",
        ),
    ],
)
def test_validation_catches_each_problem(mutate, message):
    kb = copy.deepcopy(KB)
    mutate(kb["departments"][0])
    problems = validate(kb, AOB_ITEMS, URLS, MINISTRIES)
    assert any(message in p for p in problems), problems


def test_normalise_keeps_devanagari_and_drops_punctuation():
    assert normalise("Aadhaar-card: आधार, अपडेट!") == "aadhaar card आधार अपडेट"


def test_lexical_retrieval_ranks_by_mandate_and_marks_evidence_source():
    records = resolve(KB, AOB_ITEMS, URLS, MINISTRIES)
    r = LexicalRetriever(records)
    top = r.search("my speed post parcel was not delivered", 2)
    assert top[0].ministry_key == "department_of_posts"
    assert (
        r.search("आयकर income tax refund pending", 1)[0].ministry_key
        == "central_board_of_direct_taxes_income_tax"
    )
    kinds = {c.kind for c in build_chunks(records)}
    assert kinds == {"name", "official", "responsibility", "category", "keywords"}
    assert {c.kind for c in build_chunks(records, official_only=True)} == {"name", "official"}
    assert top[0].evidence and all(isinstance(s, float) for s, _ in top[0].evidence)
