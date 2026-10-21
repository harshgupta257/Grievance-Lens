"""Power BI export rules on small synthetic frames (no database)."""

from datetime import date

import numpy as np
import pandas as pd
import pytest

from src.export.powerbi import (
    FOREIGN_KEYS,
    TABLES,
    _demand_backlog_sensitivity,
    _dim_month,
    _ministry_monthly,
    _repeated_listings,
    _tidy,
    validate_exports,
)


def test_every_key_and_foreign_key_column_is_documented():
    for name, spec in TABLES.items():
        assert set(spec.key) <= set(spec.columns), name
    for (table, col), (dim, key) in FOREIGN_KEYS.items():
        assert col in TABLES[table].columns and key in TABLES[dim].columns


def test_dim_month_is_contiguous_with_indian_fiscal_year():
    d = _dim_month(pd.Series([date(2023, 2, 1), date(2024, 4, 1)]))
    assert len(d) == 15 and d["month_index"].tolist() == list(range(15))
    fy = d.set_index("month")["fiscal_year"]
    assert fy[date(2023, 3, 1)] == "FY2022-23" and fy[date(2023, 4, 1)] == "FY2023-24"
    assert not d.set_index("month")["in_core_window"][date(2023, 3, 1)]


def _app(rows):
    return pd.DataFrame(rows, columns=["report_number", "list_code", "ministry_id", "avg_closing_time_days"])


def test_runs_count_consecutive_published_lists_not_report_numbers():
    # the list was not published in report 3 (e.g. a stale reprint was excluded)
    rows = [(n, "closing_time_high", 1, 50.0) for n in (1, 2, 4, 5)] + [(1, "closing_time_high", 2, 60.0)]
    rows += [(n, "closing_time_high", 9, 10.0) for n in (2, 4, 5)]  # keeps reports 2, 4, 5 published
    r = _repeated_listings(_app(rows), {2: "split into X from 2026-09-01"}).set_index("ministry_id")
    assert r.loc[1, "reports_with_list"] == 4 and r.loc[1, "appearances"] == 4
    assert r.loc[1, "longest_consecutive_run"] == 4 and r.loc[1, "run_to_last_list"] == 4
    assert r.loc[2, "run_to_last_list"] == 0 and r.loc[2, "reorganisation_note"].startswith("split")
    assert r.loc[1, "median_value"] == 50.0


def test_flows_flag_two_month_window_and_change_only_between_consecutive_reports():
    flows = pd.DataFrame(
        {
            "report_number": [27, 28, 29, 31],
            "ministry_id": [1, 1, 1, 1],
            "window_months": [1, 2, 1, 1],
            "pending": [100.0, 120.0, 90.0, 80.0],
        }
    )
    m = _ministry_monthly(flows).set_index("report_number")
    assert m["single_month_flows"].tolist() == [True, False, True, True]
    assert m.loc[29, "pending_change"] == -30 and np.isnan(m.loc[31, "pending_change"])


def test_absent_quadrant_combination_is_a_true_zero_count():
    sens = pd.DataFrame(
        [
            {
                "stable_band_pct_per_month": 1.0,
                "classified_ministries": 3,
                "same_quadrant_as_default": 3,
                "n_rising/rising": 2,
                "n_stable/falling": 1,
            }
        ]
    )
    s = _demand_backlog_sensitivity(sens).set_index(["demand_trend", "backlog_trend"])
    assert len(s) == 9 and s["ministries"].sum() == 3
    assert s.loc[("falling", "rising"), "ministries"] == 0 and s["is_default_band"].all()


def test_tidy_orders_columns_converts_whole_floats_and_keeps_small_p_values():
    df = pd.DataFrame({"b": [1.0, np.nan], "a": [6.8e-08, 0.5], "extra": [1, 2]})
    t = _tidy(df, ["a", "b"])
    assert list(t.columns) == ["a", "b"] and str(t["b"].dtype) == "Int64"
    assert t.loc[0, "a"] == pytest.approx(6.8e-08)
    with pytest.raises(KeyError):
        _tidy(df, ["a", "missing"])


def _minimal_exports():
    """One valid row per table, with only the key and foreign-key columns."""
    ex = {}
    for name, spec in TABLES.items():
        cols = set(spec.key) | {c for (t, c) in FOREIGN_KEYS if t == name}
        ex[name] = pd.DataFrame({c: ["x"] for c in cols})
    ex["dim_month"]["month"] = [date(2024, 1, 1)]
    ex["dim_report"] = pd.DataFrame({"report_number": [1], "report_month": [date(2024, 1, 1)]})
    ex["overall_trend"]["month"] = [date(2024, 1, 1)]
    ex["dim_ministry"]["ministry_id"] = [7]
    for (t, c), (dim, _key) in FOREIGN_KEYS.items():
        if dim == "dim_report":
            ex[t][c] = [1]
        elif dim == "dim_ministry":
            ex[t][c] = [7]
    ex["dim_list"]["list_code"] = ["pending_over_1000"]
    for t in ("list_appearances", "repeated_listings"):
        ex[t]["list_code"] = ["pending_over_1000"]
    ex["repeated_listings"] = ex["repeated_listings"].assign(
        appearances=[1], longest_consecutive_run=[1], run_to_last_list=[1]
    )
    return ex


def _results(**overrides):
    b = pd.DataFrame(
        {
            "ministry_id": [7],
            "reports_listed": [1],
            "longest_consecutive_run": [1],
            "current_run_to_latest": [1],
        }
    )
    base = {
        "B_pendency_list_appearances": b,
        "B_long_pending_appearances": pd.DataFrame(columns=["ministry_id", "threshold_days", "appearances"]),
        "B_closing_time_appearances": pd.DataFrame(columns=["ministry_id", "list", "appearances"]),
    }
    return {**base, **overrides}


def test_validation_passes_on_consistent_tables():
    assert validate_exports(_minimal_exports(), _results()) == []


def test_validation_reports_duplicate_keys_orphans_and_reconciliation_failures():
    ex = _minimal_exports()
    ex["dim_report"] = pd.concat([ex["dim_report"], ex["dim_report"]])  # duplicate key
    ex["scorecard"]["ministry_id"] = [99]  # not in dim_ministry
    b = _results()["B_pendency_list_appearances"].assign(reports_listed=[5])
    problems = validate_exports(ex, _results(B_pendency_list_appearances=b))
    text = "\n".join(problems)
    assert "dim_report: 1 duplicate key rows" in text
    assert "scorecard.ministry_id" in text
    assert "repeated_listings pending_over_1000 ministry 7" in text
