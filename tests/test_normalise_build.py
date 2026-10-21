"""Name resolution rules on synthetic name statistics (no files, no database)."""

from src.normalise.build import NameStats, build


def stats(rows, months, tables, figures=None):
    return NameStats(rows=rows, months=set(months), tables=set(tables), figures=figures or {})


def tab(month, kind="pending_over_1000", group=None):
    return (month, kind, group)


def entity_of(name_map, printed):
    return next(m["ministry_key"] for m in name_map if m["printed_name"] == printed)


def test_similar_names_are_proposed_but_never_applied():
    s = {
        "Ministry of Labour and Employment": stats(
            100, ["2023-04", "2023-05"], [tab("2023-04"), tab("2023-05")]
        ),
        "Ministry of Labor and Employment": stats(2, ["2023-05"], [tab("2023-05", "grai_ranking", "A")]),
    }
    entities, name_map, proposals, _ = build(s, decisions=[])
    assert [(p["from_key"], p["to_key"]) for p in proposals] == [
        ("ministry of labor and employment", "ministry of labour and employment")
    ]
    assert entity_of(name_map, "Ministry of Labor and Employment") == ""  # unresolved, not a new entity
    assert [e["canonical_name"] for e in entities] == ["Ministry of Labour and Employment"]


def test_accepted_decision_merges_and_names_the_entity_by_its_latest_form():
    s = {
        "Department of Heavy Industry": stats(47, ["2024-01"], [tab("2024-01")]),
        "Ministry of Heavy Industries": stats(21, ["2026-09"], [tab("2026-09")]),
    }
    decision = {
        "from_key": "ministry of heavy industries",
        "to_key": "department of heavy industry",
        "decision": "accept",
        "match_method": "fuzzy_reviewed",
        "score": "86.7",
        "evidence": "test",
        "reviewed_by": "tester",
        "reviewed_on": "2026-10-21",
    }
    entities, name_map, proposals, _ = build(s, decisions=[decision])
    assert proposals == []
    assert len(entities) == 1 and entities[0]["canonical_name"] == "Ministry of Heavy Industries"
    assert {m["match_method"] for m in name_map} == {"exact", "fuzzy_reviewed"}


def test_names_printed_in_the_same_table_are_never_proposed():
    same_table = [tab("2025-01", "grai_ranking", "B")]
    s = {
        "Department of Heavy Industry": stats(10, ["2025-01"], same_table),
        "Ministry of Heavy Industries": stats(10, ["2025-01"], same_table),
    }
    entities, _, proposals, auto_distinct = build(s, decisions=[])
    assert proposals == [] and auto_distinct == 1 and len(entities) == 2


def test_identical_figures_propose_a_rename_even_when_names_differ():
    figs = {"brought_forward": "1013", "receipts": "5511", "total_grievances": "6524", "pending": "1479"}
    s = {
        "Central Board of Excise and Customs": stats(
            1, ["2022-05"], [tab("2022-05", "long_pending_top10")], {("2022-05", "long_pending_top10"): figs}
        ),
        "Central Board of Indirect Taxes and Customs": stats(
            50, ["2022-05"], [tab("2022-05")], {("2022-05", "pending_over_1000"): figs}
        ),
    }
    _, _, proposals, _ = build(s, decisions=[])
    assert len(proposals) == 1
    assert proposals[0]["figures_identical"] == "yes" and proposals[0]["recommendation"] == "accept"


def test_formatting_variants_share_one_entity_without_any_decision():
    s = {
        "Department of Health & Family Welfare": stats(154, ["2026-09"], [tab("2026-09")]),
        "Department of Health and Family Welfare": stats(3, ["2022-08"], [tab("2022-08")]),
        "Department of Revenue*": stats(1, ["2025-04"], [tab("2025-04")]),
        "Department of Revenue": stats(96, ["2026-09"], [tab("2026-09")]),
    }
    entities, name_map, proposals, _ = build(s, decisions=[])
    assert proposals == [] and len(entities) == 2
    assert {m["match_method"] for m in name_map} == {"exact"}
