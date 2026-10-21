import math

import pandas as pd
import pytest

from src.analysis.analyses import _classify, _consecutive_changes, analysis_b


def test_changes_are_only_computed_between_consecutive_reports():
    # ministry 1 is listed in #10, #11 and #13: #12 is missing (below threshold = censored)
    df = pd.DataFrame(
        {"ministry_id": [1, 1, 1], "report_number": [10, 11, 13], "pending": [2000.0, 2500.0, 1500.0]}
    )
    out = _consecutive_changes(df, "pending").set_index("report_number")
    assert out.loc[11, "pending_change"] == 500 and out.loc[11, "pending_change_pct"] == pytest.approx(25.0)
    assert math.isnan(out.loc[13, "pending_change"])  # never across the gap
    assert math.isnan(out.loc[13, "pending_roll3"])


def test_percentage_change_needs_a_meaningful_base():
    df = pd.DataFrame({"ministry_id": [1, 1], "report_number": [1, 2], "pending": [40.0, 80.0]})
    out = _consecutive_changes(df, "pending")
    assert out["pending_change"].iloc[1] == 40 and math.isnan(out["pending_change_pct"].iloc[1])


@pytest.mark.parametrize(
    ("slope", "label"), [(1.5, "rising"), (-1.0, "falling"), (0.4, "stable"), (math.nan, "n/a")]
)
def test_classify_uses_a_symmetric_stable_band(slope, label):
    assert _classify(slope, 1.0) == label


def test_appearances_count_runs_and_note_reorganisations():
    pend = pd.DataFrame(
        {
            "ministry_id": [7, 7, 7, 8],
            "canonical_name": ["A", "A", "A", "B"],
            "report_number": [51, 52, 53, 52],
            "report_month": pd.to_datetime(["2026-07-01", "2026-08-01", "2026-09-01", "2026-08-01"]).date,
            "in_core_window": [True] * 4,
            "pending": [1500.0, 1600.0, 1700.0, 1200.0],
        }
    )
    empty = pd.DataFrame(
        columns=[
            "ministry_id",
            "canonical_name",
            "threshold_days",
            "report_number",
            "pending_over_threshold",
            "list",
            "avg_closing_time_days",
            "report_month",
        ]
    )
    reorg = pd.DataFrame(
        {
            "ministry_id": [8],
            "relation": ["split_into"],
            "successor": ["Dept X"],
            "effective_month": ["2026-09-01"],
        }
    )
    app, _, _ = analysis_b(pend, empty, empty, 53, reorg)
    a = app.set_index("canonical_name")
    assert a.loc["A", "current_run_to_latest"] == 3 and a.loc["B", "current_run_to_latest"] == 0
    assert (
        "split into Dept X" in a.loc["B", "reorganisation_note"] and a.loc["A", "reorganisation_note"] == ""
    )
