"""Power BI exports (Phase 6): a small star schema of validated, analysis-ready CSVs.

Usage (PowerShell, project root, venv active, database loaded and normalised):
    python -m src.export.powerbi

Outputs (data/processed/powerbi/):
    dim_*.csv, fact and summary tables   one CSV per table in TABLES below
    data_dictionary.csv                  every column of every table, with its meaning and unit
    export_manifest.csv                  grain, key, row count and SHA-256 of each table

Rules: names live only in dim_ministry (facts carry ministry_id); every table is checked for a unique key
and valid foreign keys, and list summaries are reconciled with analysis B before anything is written. A
failed check writes nothing and exits 1. Only data the analyses support is exported: no ministry-level
flows after #45 (failed validation, D-017), no average closing time outside the published Top-10 lists.
docs/powerbi_guide.md describes the recommended report; no .pbix is built by this project.
"""

from __future__ import annotations

import hashlib
import sys
from dataclasses import dataclass, field

import numpy as np
import pandas as pd
from sqlalchemy import Engine, text

from src.analysis import config as cfg
from src.analysis.stats import longest_run, run_ending_at
from src.config import PROJECT_ROOT

OUT_DIR = PROJECT_ROOT / "data" / "processed" / "powerbi"


@dataclass(frozen=True)
class ListType:
    name: str
    measure: str  # the value printed in the list
    caveat: str


LISTS = {
    "pending_over_1000": ListType(
        "Ministries with more than 1,000 pending grievances (at the report cut-off)",
        "pending",
        "Absolute threshold: larger ministries are listed more often by construction; not a performance rating",
    ),
    "long_pending_45d": ListType(
        "Top 10 by grievances pending more than 45 days",
        "pending_over_threshold",
        "Compare only within one threshold (45, 30 and 21 days were used in different periods)",
    ),
    "long_pending_30d": ListType(
        "Top 10 by grievances pending more than 30 days",
        "pending_over_threshold",
        "Compare only within one threshold (45, 30 and 21 days were used in different periods)",
    ),
    "long_pending_21d": ListType(
        "Top 10 by grievances pending more than 21 days",
        "pending_over_threshold",
        "Compare only within one threshold (45, 30 and 21 days were used in different periods)",
    ),
    "closing_time_high": ListType(
        "Top 10 highest average closing time (days, year-to-date)",
        "avg_closing_time_days",
        "Only the listed ministries have a published value; from 2026 only ministries with more than 100 "
        "disposals are eligible; stale reprinted tables are excluded",
    ),
    "closing_time_low": ListType(
        "Top 10 lowest average closing time (days, year-to-date)",
        "avg_closing_time_days",
        "Only the listed ministries have a published value; from 2026 only ministries with more than 100 "
        "disposals are eligible; stale reprinted tables are excluded",
    ),
}


@dataclass(frozen=True)
class Table:
    grain: str
    key: tuple[str, ...]
    source: str
    columns: dict[str, str] = field(default_factory=dict)  # column -> description (data dictionary)


_PROV_REPORT = "Report number the value was read from (dim_report)"
_PDF_PAGE = "PDF page of the source table (1-based), for checking against the published report"
_WARNING = "True if any value in this row has validation_status = warning (e.g. a row identity off by 1-3)"
_MINISTRY = "Canonical ministry or department (dim_ministry)"

TABLES: dict[str, Table] = {
    "dim_month": Table(
        "one row per calendar month from the first printed month to the latest report month",
        ("month",),
        "generated calendar",
        {
            "month": "First day of the calendar month (date); key for overall_trend and dim_report",
            "month_index": "Months since the first month (0, 1, 2, ...); use for previous-month/year DAX",
            "year": "Calendar year",
            "month_number": "Calendar month 1-12",
            "month_name": "Short month name (Jan-Dec); sort by month_number",
            "month_label": "Month as YYYY-MM text, for axis labels",
            "fiscal_year": "Indian financial year (April-March), e.g. FY2024-25",
            "in_core_window": "True from April 2023: month-end cut-offs, used for trend tests (D-006)",
        },
    ),
    "dim_report": Table(
        "one row per English monthly report",
        ("report_number",),
        "reports table (Phase 2 manifest)",
        {
            "report_number": "Report number as printed (1 = May 2022)",
            "report_month": "Month the report covers (first day; dim_month)",
            "data_cutoff_date": "Date up to which the report's data were drawn",
            "cutoff_is_month_end": "False for reports cut off on the 25th (before April 2023) or early (#35)",
            "in_core_window": "True from April 2023 (D-006)",
            "filename": "Downloaded PDF file name (data/raw/; SHA-256 in data/manifest.csv)",
            "source_url": "Official DARPG download URL",
        },
    ),
    "dim_ministry": Table(
        "one row per canonical ministry or department",
        ("ministry_id",),
        "ministries table (Phase 3 name resolution)",
        {
            "ministry_id": "Canonical ministry ID (stable within this project)",
            "canonical_name": "Canonical name (most recent printed form, cleaned)",
            "entity_type": "ministry, department or other body, as recorded in Phase 3",
            "first_report_month": "Month of the first report that prints this entity",
            "last_report_month": "Month of the latest report that prints this entity",
            "grai_group_2026": "GRAI volume group (A >= 500 grievances, B fewer) used by the scorecard; "
            "blank if not ranked in #46-#53",
            "reorganisation_note": "Recorded reorganisation (D-025), e.g. split into a successor; blank if none",
        },
    ),
    "dim_list": Table(
        "one row per published ministry list type",
        ("list_code",),
        "derived from list_appearances",
        {
            "list_code": "List type code (key for list_appearances and repeated_listings)",
            "list_name": "What the list contains",
            "value_measure": "Column of list_appearances holding the printed value for this list",
            "reports_with_list": "Reports in which this list was published with at least one row",
            "first_report": "First report with this list",
            "last_report": "Latest report with this list",
            "reports_missing_in_span": "Reports between first and last without this list (semicolon-separated)",
            "caveat": "What the list cannot show",
        },
    ),
    "overall_trend": Table(
        "one row per calendar month (Central Secretariat aggregate)",
        ("month",),
        "analysis A (v_monthly_overall: latest published value per month, D-026)",
        {
            "month": "Calendar month (dim_month)",
            "receipts": "Grievances received in the month",
            "disposals": "Grievances disposed of in the month",
            "pending": "Grievances pending at month end (a stock: do not sum across months)",
            "disposal_to_receipt_ratio": "disposals / receipts; blank for partial months",
            "pending_change": "Change in pending from the previous month; blank next to a partial month",
            "pending_change_pct": "pending_change as % of the previous month (only if that month >= 100)",
            "receipts_roll3": "Mean receipts over this and the two previous months (complete months only)",
            "disposals_roll3": "Mean disposals over this and the two previous months (complete months only)",
            "pending_roll3": "Mean pending over this and the two previous months (complete months only)",
            "receipts_yoy_pct": "Receipts vs the same month a year earlier, %",
            "has_partial_value": "True if the only value available is from a report cut off before month end",
            "in_core_window": "True from April 2023 (D-006)",
            "source_report": _PROV_REPORT + " (the latest report that prints the month)",
            "max_revision_spread": "Largest difference between reports printing the same month (revisions)",
        },
    ),
    "ministry_monthly": Table(
        "one row per ministry per report in the T6 monthly panel (#26-#45, Jun 2024 - Jan 2026)",
        ("report_number", "ministry_id"),
        "v_ministry_monthly_flows (GRAI 'flows' tables, D-017)",
        {
            "report_number": _PROV_REPORT,
            "ministry_id": _MINISTRY,
            "grai_group": "GRAI volume group printed in that report",
            "period_start": "First day of the period the flows cover",
            "period_end": "Last day of the period the flows cover",
            "window_months": "Length of the flow period in months (2 for #28: July-August 2024)",
            "single_month_flows": "True if receipts/disposals cover one month; filter on it for monthly flows",
            "brought_forward": "Pending at the start of the period",
            "receipts": "Grievances received in the period",
            "disposals": "Grievances disposed of in the period",
            "pending": "Pending at the end of the period (a stock)",
            "pending_change": "Change in pending from the previous report (consecutive reports only)",
            "pdf_page": _PDF_PAGE,
            "has_warning": _WARNING,
        },
    ),
    "list_appearances": Table(
        "one row per ministry per published list per report",
        ("report_number", "list_code", "ministry_id"),
        "v_pendency_list, v_long_pending_list, v_closing_time_list",
        {
            "report_number": _PROV_REPORT,
            "list_code": "List type (dim_list)",
            "ministry_id": _MINISTRY,
            "serial_printed": "Serial number as printed in the list",
            "pending": "Pending grievances as printed (pending and long-pending lists)",
            "pending_over_threshold": "Grievances pending longer than the list threshold (long-pending lists)",
            "cases_disposed": "Cases disposed of, year-to-date (closing-time lists)",
            "avg_closing_time_days": "Published average closing time in days, year-to-date (closing-time lists)",
            "pdf_page": _PDF_PAGE,
            "has_warning": _WARNING,
        },
    ),
    "repeated_listings": Table(
        "one row per ministry per list type (ministries listed at least once)",
        ("list_code", "ministry_id"),
        "derived from list_appearances (same rules as analysis B)",
        {
            "list_code": "List type (dim_list)",
            "ministry_id": _MINISTRY,
            "appearances": "Reports in which the ministry is listed",
            "reports_with_list": "Reports in which this list was published",
            "share_of_reports": "appearances / reports_with_list",
            "first_listed_report": "First report listing the ministry",
            "last_listed_report": "Latest report listing the ministry",
            "longest_consecutive_run": "Longest run of consecutive published lists that include the ministry",
            "run_to_last_list": "Run of consecutive listings ending at the latest published list (0 if not in it)",
            "median_value": "Median printed value when listed (see dim_list.value_measure)",
            "max_value": "Largest printed value when listed",
            "reorganisation_note": "Recorded reorganisation; a run can end because the body was reorganised",
        },
    ),
    "backlog_growth": Table(
        "one row per ministry in the T6 panel",
        ("ministry_id",),
        "analysis C (panel summary) + analysis E family F3 (trend test) + backlog-to-demand ratio",
        {
            "ministry_id": _MINISTRY,
            "months": "Reports in the panel for this ministry",
            "complete_panel": "True if present in all 20 panel reports",
            "start_month": "First panel month",
            "end_month": "Last panel month",
            "pending_start": "Pending at the end of the first panel month",
            "pending_end": "Pending at the end of the last panel month",
            "pending_change": "pending_end - pending_start",
            "pending_change_pct": "pending_change as % of pending_start (only if pending_start >= 100)",
            "months_pending_rose": "Months in which pending rose",
            "months_pending_fell": "Months in which pending fell",
            "pending_sen_slope": "Sen's slope of month-end pending (grievances per month)",
            "pending_relative_slope_pct": "Sen's slope as % of the median pending level, per month",
            "trend_p_modified": "Autocorrelation-corrected Mann-Kendall p-value",
            "trend_q_value": "Benjamini-Hochberg q-value within family F3",
            "trend_conclusion": "increasing / decreasing (q < 0.05) or no significant trend",
            "backlog_months": "Mean pending / mean monthly receipts, #29-#45 (months of intake waiting)",
        },
    ),
    "trend_tests": Table(
        "one row per trend test",
        ("test_id",),
        "analysis E",
        {
            "test_id": "Row identifier",
            "family": "Test family; q-values are adjusted within a family",
            "subject": "Aggregate metric, or ministry name for ministry-level families",
            "ministry_id": _MINISTRY + "; blank for aggregate series",
            "span": "Months or reports covered",
            "n": "Number of points in the series",
            "tau": "Kendall's tau (direction and strength of the monotonic trend)",
            "sen_slope": "Sen's slope (units per month)",
            "relative_slope_pct": "Sen's slope as % of the median level, per month",
            "median_level": "Median of the series",
            "p_modified": "Autocorrelation-corrected p-value (method in p_method)",
            "p_method": "hamed_rao, or tfpw (trend-free pre-whitening) where Hamed-Rao is undefined",
            "p_original": "Uncorrected Mann-Kendall p-value (for comparison only; not used)",
            "q_value": "Benjamini-Hochberg q-value within the family",
            "conclusion": "increasing / decreasing (q < 0.05) or no significant trend",
        },
    ),
    "grai_ranks": Table(
        "one row per ministry per GRI/GRAI ranking table (#4-#53)",
        ("report_number", "ministry_id"),
        "v_grai",
        {
            "report_number": _PROV_REPORT,
            "ministry_id": _MINISTRY,
            "grai_group": "Volume group of the ranking table (A or B)",
            "grai_methodology": "Layout/method era; ranks are comparable only within one era and group",
            "group_threshold": "Grievance-count threshold separating the groups in that report",
            "period_start": "First day of the ranking's data window",
            "period_end": "Last day of the ranking's data window",
            "window_months": "Length of the data window in months",
            "grai_rank": "Published rank within the group (1 = best)",
            "group_size": "Ministries ranked in the group in that report",
            "rank_percentile": "100 * (group_size - rank) / (group_size - 1): 100 = first in its group",
            "grai_score": "Published GRAI score (eras that print one)",
            "gri_percentage": "Published GRI percentage (#4-#12 era)",
            "pdf_page": _PDF_PAGE,
            "has_warning": _WARNING,
        },
    ),
    "scorecard": Table(
        "one row per ministry ranked in GRAI #46-#53",
        ("ministry_id",),
        "Phase 5 scorecard (project construct, not an official rating; docs/scorecard.md)",
        {
            "ministry_id": _MINISTRY,
            "grai_group": "Group the ministry is compared within (most frequent in #46-#53)",
            "months_ranked": "Reports in #46-#53 that rank the ministry in that group",
            "switched_group": "True if the ministry appeared in both groups during #46-#53",
            "grai_percentile": "Component: mean within-group GRAI percentile (higher = better)",
            "backlog_trend_pct": "Component: relative Sen slope of pending, % per month (lower = better)",
            "backlog_months": "Component: mean pending / mean monthly receipts (lower = better)",
            "slow_closing_share": "Component: share of #46-#53 on the high closing-time list (lower = better; "
            "Group A only)",
            "over_1000_share": "Information only (weight 0): share of #46-#53 on the >1000-pending list",
            "grai_percentile_pctl": "Within-group percentile of grai_percentile (100 = best in group)",
            "backlog_trend_pct_pctl": "Within-group percentile of backlog_trend_pct (100 = best in group)",
            "backlog_months_pctl": "Within-group percentile of backlog_months (100 = best in group)",
            "slow_closing_share_pctl": "Within-group percentile of slow_closing_share (100 = best in group)",
            "over_1000_share_pctl": "Within-group percentile of over_1000_share (information only)",
            "components_used": "Applicable components with data",
            "score": "Weighted mean of the available component percentiles (0-100); blank = not scored",
            "rank_in_group": "Rank of score within the group (1 = highest)",
            "rank_random_median": "Median rank over 1,000 random weightings",
            "rank_random_p05": "5th percentile rank over random weightings (lower end of the 90% interval)",
            "rank_random_p95": "95th percentile rank over random weightings (upper end of the 90% interval)",
            "group_size": "Scored ministries in the group",
            "not_scored_reason": "Why no score was given; blank if scored",
            "label": "Always 'project construct, not an official rating'",
        },
    ),
    "scorecard_sensitivity": Table(
        "one row per weight variant per group",
        ("variant", "group"),
        "Phase 5 scorecard sensitivity",
        {
            "variant": "Named weighting (src/analysis/config.py SCORECARD_VARIANTS)",
            "group": "GRAI group",
            "ministries": "Ministries scored under both the default and this variant",
            "spearman_vs_default": "Spearman correlation of ranks with the default weighting",
            "max_rank_shift": "Largest rank change of any ministry",
            "median_rank_shift": "Median rank change",
            "top_quartile_changes": "Ministries entering or leaving the top quarter of the group",
        },
    ),
    "demand_backlog_quadrants": Table(
        "one row per ministry in the T6 panel",
        ("ministry_id",),
        "analysis G (#29-#45, Sep 2024 - Jan 2026)",
        {
            "ministry_id": _MINISTRY,
            "months": "Single-month flow reports used",
            "mean_monthly_receipts": "Mean receipts per month",
            "mean_pending": "Mean month-end pending",
            "status": "classified, insufficient months or insufficient volume (< 100 receipts or < 50 pending)",
            "demand_rel_slope_pct": "Relative Sen slope of receipts, % of mean per month",
            "demand_p_modified": "Corrected Mann-Kendall p-value for receipts",
            "backlog_rel_slope_pct": "Relative Sen slope of pending, % of mean per month",
            "backlog_p_modified": "Corrected Mann-Kendall p-value for pending",
            "demand_q": "Benjamini-Hochberg q-value for receipts (classified ministries)",
            "backlog_q": "Benjamini-Hochberg q-value for pending (classified ministries)",
            "demand_trend": f"rising / stable / falling at +/-{cfg.STABLE_BAND_PCT_PER_MONTH}% per month",
            "backlog_trend": f"rising / stable / falling at +/-{cfg.STABLE_BAND_PCT_PER_MONTH}% per month",
            "quadrant": "demand_trend / backlog_trend label; blank unless classified",
        },
    ),
    "demand_backlog_sensitivity": Table(
        "one row per stable band per demand/backlog combination",
        ("stable_band_pct_per_month", "demand_trend", "backlog_trend"),
        "analysis G sensitivity",
        {
            "stable_band_pct_per_month": "Band used to call a slope stable (% of mean per month)",
            "is_default_band": "True for the band used in demand_backlog_quadrants",
            "demand_trend": "rising / stable / falling",
            "backlog_trend": "rising / stable / falling",
            "ministries": "Classified ministries in this combination (0 = none)",
            "classified_ministries": "Classified ministries in total",
            "same_quadrant_as_default": "Ministries whose label equals the default band's label",
        },
    ),
}

# (table, column) -> (dimension table, dimension key)
FOREIGN_KEYS = {
    ("dim_report", "report_month"): ("dim_month", "month"),
    ("overall_trend", "month"): ("dim_month", "month"),
    **{
        (t, "report_number"): ("dim_report", "report_number")
        for t in ("ministry_monthly", "list_appearances", "grai_ranks")
    },
    **{
        (t, "ministry_id"): ("dim_ministry", "ministry_id")
        for t in (
            "ministry_monthly",
            "list_appearances",
            "repeated_listings",
            "backlog_growth",
            "trend_tests",
            "grai_ranks",
            "scorecard",
            "demand_backlog_quadrants",
        )
    },
    ("list_appearances", "list_code"): ("dim_list", "list_code"),
    ("repeated_listings", "list_code"): ("dim_list", "list_code"),
}


def _reorganisation_notes(reorganisations: pd.DataFrame) -> dict[int, str]:
    return {
        r["ministry_id"]: f"{r['relation'].replace('_', ' ')} {r['successor']} from {r['effective_month']}"
        for _, r in reorganisations.iterrows()
    }


def _dim_month(months: pd.Series) -> pd.DataFrame:
    idx = pd.date_range(pd.Timestamp(months.min()), pd.Timestamp(months.max()), freq="MS")
    fy = np.where(idx.month >= 4, idx.year, idx.year - 1)
    d = pd.DataFrame({"month": idx.date})
    return d.assign(
        month_index=range(len(d)),
        year=idx.year,
        month_number=idx.month,
        month_name=idx.strftime("%b"),
        month_label=idx.strftime("%Y-%m"),
        fiscal_year=[f"FY{y}-{(y + 1) % 100:02d}" for y in fy],
        in_core_window=d["month"] >= cfg.CORE_WINDOW_START,
    )


def _list_appearances(frames: dict[str, pd.DataFrame]) -> pd.DataFrame:
    lp = frames["long_pending"]
    parts = [
        frames["pendency"].assign(list_code="pending_over_1000"),
        lp.assign(list_code="long_pending_" + lp["threshold_days"].astype(int).astype(str) + "d"),
        frames["closing"].assign(list_code="closing_time_" + frames["closing"]["list"]),
    ]
    out = pd.concat(parts, ignore_index=True).rename(columns={"serial": "serial_printed"})
    unknown = set(out["list_code"]) - set(LISTS)
    if unknown:
        raise ValueError(f"list types without a definition in LISTS: {sorted(unknown)}")
    return out.reindex(columns=list(TABLES["list_appearances"].columns)).sort_values(
        ["report_number", "list_code", "ministry_id"]
    )


def _dim_list(app: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for code, spec in LISTS.items():
        nums = sorted(app.loc[app["list_code"] == code, "report_number"].unique())
        if not nums:
            continue
        missing = sorted(set(range(nums[0], nums[-1] + 1)) - set(nums))
        rows.append(
            {
                "list_code": code,
                "list_name": spec.name,
                "value_measure": spec.measure,
                "reports_with_list": len(nums),
                "first_report": nums[0],
                "last_report": nums[-1],
                "reports_missing_in_span": ";".join(str(n) for n in missing),
                "caveat": spec.caveat,
            }
        )
    return pd.DataFrame(rows)


def _repeated_listings(app: pd.DataFrame, notes: dict[int, str]) -> pd.DataFrame:
    rows = []
    for code, g in app.groupby("list_code"):
        published = sorted(g["report_number"].unique())
        position = {n: i for i, n in enumerate(published)}  # runs count consecutive *published* lists
        measure = LISTS[code].measure
        for mid, m in g.groupby("ministry_id"):
            nums = sorted(m["report_number"].unique())
            pos = [position[n] for n in nums]
            rows.append(
                {
                    "list_code": code,
                    "ministry_id": mid,
                    "appearances": len(nums),
                    "reports_with_list": len(published),
                    "share_of_reports": len(nums) / len(published),
                    "first_listed_report": nums[0],
                    "last_listed_report": nums[-1],
                    "longest_consecutive_run": longest_run(pos),
                    "run_to_last_list": run_ending_at(pos, len(published) - 1),
                    "median_value": m[measure].median(),
                    "max_value": m[measure].max(),
                    "reorganisation_note": notes.get(mid, ""),
                }
            )
    return pd.DataFrame(rows).sort_values(["list_code", "appearances"], ascending=[True, False])


def _ministry_monthly(flows: pd.DataFrame) -> pd.DataFrame:
    f = flows.sort_values(["ministry_id", "report_number"]).copy()
    f["single_month_flows"] = f["window_months"] == 1
    prev_num = f.groupby("ministry_id")["report_number"].shift(1)
    f["pending_change"] = (f["pending"] - f.groupby("ministry_id")["pending"].shift(1)).where(
        f["report_number"] - prev_num == 1
    )
    return f


def _backlog_growth(results: dict[str, pd.DataFrame], flows: pd.DataFrame) -> pd.DataFrame:
    e = results["E_trend_tests"]
    f3 = e[e["family"].str.startswith("F3")].rename(
        columns={
            "sen_slope": "pending_sen_slope",
            "relative_slope_pct": "pending_relative_slope_pct",
            "p_modified": "trend_p_modified",
            "q_value": "trend_q_value",
            "conclusion": "trend_conclusion",
        }
    )
    lo, hi = cfg.PANEL_FLOW_REPORTS
    w = flows[flows["report_number"].between(lo, hi)].groupby("ministry_id")
    ratio = (w["pending"].mean() / w["receipts"].mean().where(lambda s: s > 0)).where(
        w.size() >= cfg.MIN_POINTS_DEMAND_BACKLOG
    )
    out = results["C_panel_pending_summary"].merge(
        f3[
            [
                "ministry_id",
                "pending_sen_slope",
                "pending_relative_slope_pct",
                "trend_p_modified",
                "trend_q_value",
                "trend_conclusion",
            ]
        ],
        on="ministry_id",
        how="left",
    )
    out["backlog_months"] = out["ministry_id"].map(ratio)
    return out


def _demand_backlog_sensitivity(sens: pd.DataFrame) -> pd.DataFrame:
    levels = ("rising", "stable", "falling")
    rows = []
    for _, r in sens.iterrows():
        for d in levels:
            for b in levels:
                n = r.get(f"n_{d}/{b}")
                rows.append(
                    {
                        "stable_band_pct_per_month": r["stable_band_pct_per_month"],
                        "is_default_band": r["stable_band_pct_per_month"] == cfg.STABLE_BAND_PCT_PER_MONTH,
                        "demand_trend": d,
                        "backlog_trend": b,
                        # a combination absent from the counts has no classified ministries: a true zero
                        "ministries": 0 if pd.isna(n) else int(n),
                        "classified_ministries": r["classified_ministries"],
                        "same_quadrant_as_default": r["same_quadrant_as_default"],
                    }
                )
    return pd.DataFrame(rows)


def build_exports(
    frames: dict[str, pd.DataFrame],
    results: dict[str, pd.DataFrame],
    reports: pd.DataFrame,
    ministries: pd.DataFrame,
) -> dict[str, pd.DataFrame]:
    """All export tables, columns ordered as documented in TABLES. Pure: no files, no database."""
    notes = _reorganisation_notes(frames["reorganisations"])
    sc = results["S_scorecard"]
    app = _list_appearances(frames)
    grai = frames["grai"].assign(
        rank_percentile=lambda d: np.where(
            d["group_size"] > 1, 100 * (d["group_size"] - d["grai_rank"]) / (d["group_size"] - 1), np.nan
        )
    )
    trend = results["E_trend_tests"].reset_index(drop=True)
    overall = results["A_overall_monthly"]
    out = {
        "dim_month": _dim_month(pd.concat([overall["month"], reports["report_month"]])),
        "dim_report": reports,
        "dim_ministry": ministries.assign(
            grai_group_2026=ministries["ministry_id"].map(sc.set_index("ministry_id")["grai_group"]),
            reorganisation_note=ministries["ministry_id"].map(notes).fillna(""),
        ),
        "dim_list": _dim_list(app),
        "overall_trend": overall,
        "ministry_monthly": _ministry_monthly(frames["flows"]),
        "list_appearances": app,
        "repeated_listings": _repeated_listings(app, notes),
        "backlog_growth": _backlog_growth(results, frames["flows"]),
        "trend_tests": trend.assign(test_id=trend.index + 1),
        "grai_ranks": grai,
        "scorecard": sc,
        "scorecard_sensitivity": results["S_sensitivity"],
        "demand_backlog_quadrants": results["G_demand_backlog"],
        "demand_backlog_sensitivity": _demand_backlog_sensitivity(results["G_sensitivity"]),
    }
    return {name: _tidy(df, list(TABLES[name].columns)) for name, df in out.items()}


def _tidy(df: pd.DataFrame, columns: list[str]) -> pd.DataFrame:
    missing = [c for c in columns if c not in df.columns]
    if missing:
        raise KeyError(f"columns missing from source frame: {missing}")
    d = df[columns].reset_index(drop=True).copy()
    for c in d.columns:
        s = d[c]
        if s.dtype == bool or s.dtype == object:
            continue
        valid = s.dropna()
        # counts and ranks without a trailing .0; other floats are not rounded (small p-values must survive)
        if pd.api.types.is_float_dtype(s) and len(valid) and (valid == valid.round()).all():
            d[c] = s.astype("Int64")
    return d


def validate_exports(exports: dict[str, pd.DataFrame], results: dict[str, pd.DataFrame]) -> list[str]:
    """Problems that must stop the export: duplicate keys, broken foreign keys, failed reconciliation."""
    problems = []
    for name, spec in TABLES.items():
        df = exports[name]
        if df.empty:
            problems.append(f"{name}: no rows")
        if df[list(spec.key)].isna().any().any():
            problems.append(f"{name}: blank key value")
        dup = df.duplicated(subset=list(spec.key)).sum()
        if dup:
            problems.append(f"{name}: {dup} duplicate key rows on {spec.key}")
    for (table, col), (dim, key) in FOREIGN_KEYS.items():
        values = exports[table][col].dropna()
        orphans = set(values) - set(exports[dim][key])
        if orphans:
            problems.append(
                f"{table}.{col}: {len(orphans)} values not in {dim}.{key}, e.g. {sorted(orphans)[:3]}"
            )

    # reconciliation with analysis B (computed independently from the same views)
    rl = exports["repeated_listings"].set_index(["list_code", "ministry_id"])
    for _, r in results["B_pendency_list_appearances"].iterrows():
        got = rl.loc[("pending_over_1000", r["ministry_id"])]
        expected = (r["reports_listed"], r["longest_consecutive_run"], r["current_run_to_latest"])
        actual = (got["appearances"], got["longest_consecutive_run"], got["run_to_last_list"])
        if tuple(int(x) for x in expected) != tuple(int(x) for x in actual):
            problems.append(
                f"repeated_listings pending_over_1000 ministry {r['ministry_id']}: {actual} != B {expected}"
            )
    for _, r in results["B_long_pending_appearances"].iterrows():
        code = f"long_pending_{int(r['threshold_days'])}d"
        if int(rl.loc[(code, r["ministry_id"]), "appearances"]) != int(r["appearances"]):
            problems.append(
                f"repeated_listings {code} ministry {r['ministry_id']}: appearances differ from B"
            )
    for _, r in results["B_closing_time_appearances"].iterrows():
        code = f"closing_time_{r['list']}"
        if int(rl.loc[(code, r["ministry_id"]), "appearances"]) != int(r["appearances"]):
            problems.append(
                f"repeated_listings {code} ministry {r['ministry_id']}: appearances differ from B"
            )
    return problems


def data_dictionary() -> pd.DataFrame:
    rows = []
    for name, spec in TABLES.items():
        for col, desc in spec.columns.items():
            fk = FOREIGN_KEYS.get((name, col))
            rows.append(
                {
                    "table": name,
                    "column": col,
                    "is_key": col in spec.key,
                    "references": f"{fk[0]}.{fk[1]}" if fk else "",
                    "description": desc,
                }
            )
    return pd.DataFrame(rows)


def write_exports(exports: dict[str, pd.DataFrame], out_dir=OUT_DIR) -> pd.DataFrame:
    out_dir.mkdir(parents=True, exist_ok=True)
    manifest = []
    for name, df in exports.items():
        path = out_dir / f"{name}.csv"
        df.to_csv(path, index=False, lineterminator="\n", encoding="utf-8")
        spec = TABLES[name]
        manifest.append(
            {
                "table": name,
                "file": path.name,
                "rows": len(df),
                "columns": len(df.columns),
                "grain": spec.grain,
                "primary_key": "+".join(spec.key),
                "source": spec.source,
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            }
        )
    data_dictionary().to_csv(out_dir / "data_dictionary.csv", index=False, lineterminator="\n")
    m = pd.DataFrame(manifest)
    m.to_csv(out_dir / "export_manifest.csv", index=False, lineterminator="\n")
    return m


def load_dimensions(engine: Engine) -> tuple[pd.DataFrame, pd.DataFrame]:
    with engine.connect() as conn:
        reports = pd.read_sql(
            text(
                "SELECT report_number, report_month, data_cutoff_date, cutoff_is_month_end, in_core_window, "
                "filename, source_url FROM v_reports ORDER BY report_number"
            ),
            conn,
        )
        ministries = pd.read_sql(
            text(
                "SELECT ministry_id, canonical_name, entity_type, first_report_month, last_report_month "
                "FROM ministries ORDER BY ministry_id"
            ),
            conn,
        )
    return reports, ministries


def main() -> int:
    from src.analysis.run import load_frames, run_all
    from src.analysis.views import apply_views
    from src.config import ConfigError, load_settings
    from src.db import get_engine

    try:
        settings = load_settings()
    except ConfigError as exc:
        print(f"[config] {exc}", file=sys.stderr)
        return 1
    engine = get_engine(settings.database_url)
    try:
        apply_views(engine)
        frames = load_frames(engine)
        reports, ministries = load_dimensions(engine)
    finally:
        engine.dispose()
    results = run_all(frames)
    exports = build_exports(frames, results, reports, ministries)
    problems = validate_exports(exports, results)
    if problems:
        print(f"Export stopped: {len(problems)} problem(s); nothing written.", file=sys.stderr)
        for p in problems:
            print(f"  - {p}", file=sys.stderr)
        return 1
    manifest = write_exports(exports)
    for _, r in manifest.iterrows():
        print(f"{r['file']}: {r['rows']} rows")
    print(f"Wrote {OUT_DIR.relative_to(PROJECT_ROOT)} (data_dictionary.csv, export_manifest.csv)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
