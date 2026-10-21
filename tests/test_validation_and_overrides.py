from datetime import date
from decimal import Decimal as D

import pytest

from src.extract.ministry_tables import IDENTITY_TOLERANCE, _validate_row
from src.extract.model import ExtractedTable, Observation
from src.extract.overrides import COLUMNS, apply_overrides, load_overrides


def flows(bf, r, tot, d, p):
    return {
        "brought_forward": D(bf),
        "receipts": D(r),
        "total_grievances": D(tot),
        "disposals": D(d),
        "pending": D(p),
    }


def test_exact_identities_are_valid():
    # CBDT, Aug 2023 (Annexure 1.5): 13036 + 46711 = 59747; 59747 - 48237 = 11510
    assert _validate_row(flows(13036, 46711, 59747, 48237, 11510)) == ("valid", D(0))


def test_small_published_gap_is_a_warning_not_a_failure():
    # Rural Development, Feb 2024: 33528 - 8306 = 25222, published Pending 25221
    status, gap = _validate_row(flows(2999, 30529, 33528, 8306, 25221))
    assert status == "warning" and gap == D(1)


def test_large_gap_fails():
    status, gap = _validate_row(flows(100, 200, 300, 100, 150))
    assert status == "failed" and abs(gap) > IDENTITY_TOLERANCE


def test_scores_out_of_range_fail_and_unknown_layouts_are_unvalidated():
    assert _validate_row({"grai_score": D("1.2")})[0] == "failed"
    assert _validate_row({"cases_disposed": D(10), "avg_closing_time_days": D(5)})[0] == "unvalidated"


class _Extraction:
    def __init__(self, tables):
        self.record = type("R", (), {"month": date(2025, 8, 1)})()
        self.tables = tables
        self.issues = []


def _table():
    t = ExtractedTable(
        "pending_over_1000", "Annexure 1.3", 35, 35, date(2025, 1, 1), date(2025, 8, 31), "test", "0"
    )
    t.observations.append(
        Observation(
            0,
            "pending",
            "Pending",
            "13959",
            D(13959),
            date(2025, 1, 1),
            date(2025, 8, 31),
            35,
            "Ministry of Labour and Employment",
            "1",
        )
    )
    return t


def _write(tmp_path, rows):
    path = tmp_path / "overrides.csv"
    path.write_text(",".join(COLUMNS) + "\n" + "".join(",".join(r) + "\n" for r in rows), encoding="utf-8")
    return path


def test_override_replaces_value_and_records_who_verified(tmp_path):
    path = _write(
        tmp_path,
        [
            [
                "2025-08",
                "pending_over_1000",
                "",
                "1",
                "pending",
                "",
                "13960",
                "misread digit",
                "reviewer",
                "2026-10-21",
            ]
        ],
    )
    ext = _Extraction([_table()])
    assert apply_overrides(ext, load_overrides(path)) == 1
    obs = ext.tables[0].observations[0]
    assert obs.value_numeric == D(13960) and obs.validation_status == "overridden"
    assert obs.value_original == "13959"  # the extracted text is kept
    issue = ext.tables[0].issues[0]
    assert issue.status == "resolved" and "reviewer" in issue.resolution_note


def test_unmatched_override_is_an_error(tmp_path):
    path = _write(
        tmp_path,
        [["2025-08", "pending_over_1000", "", "99", "pending", "", "1", "typo", "reviewer", "2026-10-21"]],
    )
    ext = _Extraction([_table()])
    assert apply_overrides(ext, load_overrides(path)) == 0
    assert ext.issues[0].issue_type == "override_unmatched"


def test_override_without_reviewer_is_rejected(tmp_path):
    path = _write(tmp_path, [["2025-08", "pending_over_1000", "", "1", "pending", "", "1", "typo", "", ""]])
    with pytest.raises(ValueError, match="verified_by"):
        load_overrides(path)
