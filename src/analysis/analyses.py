"""Analyses A-G (docs/methodology.md). Each function takes data frames from the analysis views and returns
result frames; nothing here reads or writes files or the database.

Data rules (docs/data_quality.md section 5) are applied here: latest-published value per month (in the
view), no partial months in derived metrics, trend tests only on the core window, comparisons of lists
only within one threshold, rankings only within one layout era and group, list absence = not observed.
"""

from __future__ import annotations

import math

import numpy as np
import pandas as pd

from src.analysis import config as cfg
from src.analysis.stats import benjamini_hochberg, longest_run, mann_kendall, run_ending_at, spearman

LAST_REPORT = 53


def _pct(cur: pd.Series, prev: pd.Series) -> pd.Series:
    return np.where(prev >= cfg.MIN_BASE_FOR_PCT_CHANGE, 100 * (cur - prev) / prev, np.nan)


# --- A: overall grievance trends ------------------------------------------------------------------------


def analysis_a(monthly: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    m = monthly.sort_values("month").reset_index(drop=True).copy()
    complete = ~m["has_partial_value"]
    for col in ("receipts", "disposals", "pending"):
        v = m[col].where(complete)
        m[f"{col}_roll3"] = v.rolling(3, min_periods=3).mean()
    rec, dis, pen = (m[c].where(complete) for c in ("receipts", "disposals", "pending"))
    m["disposal_to_receipt_ratio"] = dis / rec
    m["pending_change"] = pen - pen.shift(1)
    m["pending_change_pct"] = _pct(pen, pen.shift(1))
    m["receipts_yoy_pct"] = _pct(rec, rec.shift(12))
    m["year"] = pd.to_datetime(m["month"]).dt.year
    rows = []
    for year, g in m.groupby("year"):
        c = g[~g["has_partial_value"]]
        rows.append(
            {
                "year": year,
                "complete_months": len(c),
                "partial_months": int(g["has_partial_value"].sum()),
                "receipts_total_complete_months": c["receipts"].sum(),
                "mean_monthly_receipts": c["receipts"].mean(),
                "mean_monthly_disposals": c["disposals"].mean(),
                "disposal_to_receipt_ratio": c["disposals"].sum() / c["receipts"].sum(),
                "pending_last_complete_month": c["pending"].iloc[-1],
                "last_complete_month": c["month"].iloc[-1],
            }
        )
    return m.drop(columns="year"), pd.DataFrame(rows)


# --- B: repeated appearances in published lists --------------------------------------------------------


def analysis_b(
    pendency: pd.DataFrame,
    long_pending: pd.DataFrame,
    closing: pd.DataFrame,
    n_reports: int,
    reorganisations: pd.DataFrame | None = None,
):
    notes = {}
    for _, r in (reorganisations if reorganisations is not None else pd.DataFrame()).iterrows():
        notes[r["ministry_id"]] = (
            f"{r['relation'].replace('_', ' ')} {r['successor']} from {r['effective_month']}"
        )
    rows = []
    core_reports = pendency.loc[pendency["in_core_window"], "report_number"].nunique()
    for (mid, name), g in pendency.groupby(["ministry_id", "canonical_name"]):
        nums = sorted(g["report_number"].astype(int))
        core = g[g["in_core_window"]]
        rows.append(
            {
                "ministry_id": mid,
                "canonical_name": name,
                "reports_listed": len(nums),
                "share_of_reports": len(nums) / n_reports,
                "first_listed": g["report_month"].min(),
                "last_listed": g["report_month"].max(),
                "longest_consecutive_run": longest_run(nums),
                "current_run_to_latest": run_ending_at(nums, LAST_REPORT),
                "core_window_reports_listed": len(core),
                "core_window_share": len(core) / core_reports,
                "median_pending_when_listed": g["pending"].median(),
                "max_pending": g["pending"].max(),
                # a run can end because the body was reorganised, not because its backlog fell
                "reorganisation_note": notes.get(mid, ""),
            }
        )
    appearances = pd.DataFrame(rows).sort_values(["reports_listed", "max_pending"], ascending=False)

    seg_sizes = long_pending.groupby("threshold_days")["report_number"].nunique()
    lp = (
        long_pending.groupby(["ministry_id", "canonical_name", "threshold_days"])
        .agg(
            appearances=("report_number", "nunique"),
            median_over_threshold=("pending_over_threshold", "median"),
        )
        .reset_index()
    )
    lp["reports_in_segment"] = lp["threshold_days"].map(seg_sizes)
    lp["share_of_segment"] = lp["appearances"] / lp["reports_in_segment"]
    lp = lp.sort_values(["threshold_days", "appearances"], ascending=[True, False])

    lists_available = closing.groupby("list")["report_number"].nunique()
    ct = (
        closing.groupby(["ministry_id", "canonical_name", "list"])
        .agg(
            appearances=("report_number", "nunique"),
            median_days=("avg_closing_time_days", "median"),
            max_days=("avg_closing_time_days", "max"),
            first=("report_month", "min"),
            last=("report_month", "max"),
        )
        .reset_index()
    )
    ct["reports_with_list"] = ct["list"].map(lists_available)
    ct = ct.sort_values(["list", "appearances"], ascending=[True, False])
    return appearances, lp, ct


# --- C: backlog changes --------------------------------------------------------------------------------


def _consecutive_changes(df: pd.DataFrame, value: str) -> pd.DataFrame:
    d = df.sort_values(["ministry_id", "report_number"]).copy()
    prev_num = d.groupby("ministry_id")["report_number"].shift(1)
    prev_val = d.groupby("ministry_id")[value].shift(1)
    consecutive = (d["report_number"] - prev_num) == 1
    d[f"prev_{value}"] = prev_val.where(consecutive)
    d[f"{value}_change"] = (d[value] - d[f"prev_{value}"]).where(consecutive)
    d[f"{value}_change_pct"] = np.where(consecutive, _pct(d[value], d[f"prev_{value}"]), np.nan)
    # three-report rolling mean only over three consecutive reports
    two_back = d.groupby("ministry_id")["report_number"].shift(2)
    three = consecutive & ((d["report_number"] - two_back) == 2)
    d[f"{value}_roll3"] = (
        d.groupby("ministry_id")[value].transform(lambda s: s.rolling(3).mean()).where(three)
    )
    return d


def analysis_c(pendency: pd.DataFrame, flows: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    listed = _consecutive_changes(pendency, "pending")[
        [
            "report_number",
            "report_month",
            "ministry_id",
            "canonical_name",
            "pending",
            "prev_pending",
            "pending_change",
            "pending_change_pct",
            "pending_roll3",
        ]
    ]
    panel = _consecutive_changes(flows, "pending")
    rows = []
    lo, hi = cfg.PANEL_REPORTS
    for (mid, name), g in panel.groupby(["ministry_id", "canonical_name"]):
        g = g.sort_values("report_number")
        start, end = g.iloc[0], g.iloc[-1]
        rows.append(
            {
                "ministry_id": mid,
                "canonical_name": name,
                "months": len(g),
                "complete_panel": len(g) == hi - lo + 1,
                "start_month": start["report_month"],
                "end_month": end["report_month"],
                "pending_start": start["pending"],
                "pending_end": end["pending"],
                "pending_change": end["pending"] - start["pending"],
                "pending_change_pct": _pct(pd.Series([end["pending"]]), pd.Series([start["pending"]]))[0],
                "months_pending_rose": int((g["pending_change"] > 0).sum()),
                "months_pending_fell": int((g["pending_change"] < 0).sum()),
            }
        )
    summary = pd.DataFrame(rows).sort_values("pending_change", ascending=False)
    return listed, summary


# --- D: ranking stability ------------------------------------------------------------------------------


def analysis_d(grai: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Rank agreement between consecutive reports, only where layout era and group threshold are equal."""
    g = grai.dropna(subset=["grai_rank"])
    meta = g.groupby(["report_number", "grai_group"]).agg(
        methodology=("grai_methodology", "first"),
        threshold=("group_threshold", "first"),
        size=("ministry_id", "nunique"),
        month=("report_month", "first"),
    )
    rows = []
    for (num, grp), info in meta.iterrows():
        nxt = (num + 1, grp)
        if nxt not in meta.index:
            continue
        ninfo = meta.loc[nxt]
        row = {
            "report": num,
            "next_report": num + 1,
            "month": info["month"],
            "group": grp,
            "methodology": info["methodology"],
            "next_methodology": ninfo["methodology"],
        }
        if info["methodology"] != ninfo["methodology"] or info["threshold"] != ninfo["threshold"]:
            row["comparable"] = False
            row["reason"] = "layout era or group threshold changed"
            rows.append(row)
            continue
        a = g[(g["report_number"] == num) & (g["grai_group"] == grp)].set_index("ministry_id")["grai_rank"]
        b = g[(g["report_number"] == num + 1) & (g["grai_group"] == grp)].set_index("ministry_id")[
            "grai_rank"
        ]
        common = a.index.intersection(b.index)
        rho, p = spearman(a[common].tolist(), b[common].tolist())
        top_a, top_b = set(a.nsmallest(5).index), set(b.nsmallest(5).index)
        row.update(
            {
                "comparable": True,
                "reason": "",
                "ministries_both": len(common),
                "group_size": info["size"],
                "spearman_rho": rho,
                "spearman_p": p,
                "mean_abs_rank_change": float((a[common] - b[common]).abs().mean()),
                "mean_abs_rank_change_share_of_group": float(
                    (a[common] - b[common]).abs().mean() / info["size"]
                ),
                "top5_retained": len(top_a & top_b) / 5,
            }
        )
        rows.append(row)
    pairs = pd.DataFrame(rows)
    comp = pairs[pairs["comparable"]]
    eras = (
        comp.groupby(["methodology", "group"])
        .agg(
            pairs=("report", "count"),
            first_report=("report", "min"),
            last_report=("next_report", "max"),
            median_rho=("spearman_rho", "median"),
            min_rho=("spearman_rho", "min"),
            median_rank_change_share=("mean_abs_rank_change_share_of_group", "median"),
            mean_top5_retained=("top5_retained", "mean"),
        )
        .reset_index()
        .sort_values(["first_report", "group"])
    )
    return pairs, eras


# --- E: trend tests ------------------------------------------------------------------------------------


def _mk_row(family: str, subject: str, series: pd.Series, extra: dict | None = None) -> dict:
    res = mann_kendall(series.to_numpy(dtype=float))
    return {"family": family, "subject": subject, **res, **(extra or {})}


def analysis_e(monthly: pd.DataFrame, pendency: pd.DataFrame, flows: pd.DataFrame) -> pd.DataFrame:
    rows = []
    core = monthly[monthly["in_core_window"] & ~monthly["has_partial_value"]].sort_values("month")
    span = f"{core['month'].min()}..{core['month'].max()}"
    for metric in ("receipts", "disposals", "pending"):
        s = core[metric]
        if len(s) >= cfg.MIN_TREND_POINTS_OVERALL:
            rows.append(_mk_row("F1 aggregate series (core window)", metric, s, {"span": span}))
    ratio = core["disposals"] / core["receipts"]
    rows.append(
        _mk_row("F1 aggregate series (core window)", "disposal_to_receipt_ratio", ratio, {"span": span})
    )

    core_reports = sorted(pendency.loc[pendency["in_core_window"], "report_number"].unique())
    for (mid, name), g in pendency[pendency["in_core_window"]].groupby(["ministry_id", "canonical_name"]):
        if sorted(g["report_number"]) == core_reports:  # listed in every core report: no censoring gaps
            s = g.sort_values("report_number")["pending"]
            rows.append(
                _mk_row(
                    "F2 pending, ministries listed >1000 in every core report",
                    name,
                    s,
                    {"ministry_id": mid, "span": span},
                )
            )

    lo, hi = cfg.PANEL_REPORTS
    for (mid, name), g in flows.groupby(["ministry_id", "canonical_name"]):
        g = g.sort_values("report_number")
        nums = g["report_number"].tolist()
        if len(nums) >= cfg.MIN_TREND_POINTS_MINISTRY and nums == list(range(nums[0], nums[0] + len(nums))):
            rows.append(
                _mk_row(
                    "F3 month-end pending, T6 panel",
                    name,
                    g["pending"],
                    {"ministry_id": mid, "span": f"#{nums[0]}..#{nums[-1]}"},
                )
            )
    out = pd.DataFrame(rows)
    out["q_value"] = np.nan
    for _family, idx in out.groupby("family").groups.items():
        out.loc[idx, "q_value"] = benjamini_hochberg(out.loc[idx, "p_modified"].tolist())
    out["conclusion"] = np.where(
        out["q_value"] < cfg.ALPHA,
        np.where(out["tau"] > 0, "increasing", "decreasing"),
        "no significant trend",
    )
    return out


# --- F: seasonality (descriptive only) -----------------------------------------------------------------


def analysis_f(monthly: pd.DataFrame) -> pd.DataFrame:
    core = monthly[monthly["in_core_window"] & ~monthly["has_partial_value"]].copy()
    core["month_of_year"] = pd.to_datetime(core["month"]).dt.month
    overall = core["receipts"].mean()
    out = (
        core.groupby("month_of_year")
        .agg(
            years_observed=("receipts", "count"),
            mean_receipts=("receipts", "mean"),
            min_receipts=("receipts", "min"),
            max_receipts=("receipts", "max"),
        )
        .reset_index()
    )
    out["ratio_to_overall_mean"] = out["mean_receipts"] / overall
    out["note"] = "descriptive only: at most 4 observations per calendar month; no seasonality claim"
    return out


# --- G: demand vs backlog (T6 panel, single-month flows) -----------------------------------------------


def _classify(rel_slope: float, band: float) -> str:
    if math.isnan(rel_slope):
        return "n/a"
    if rel_slope >= band:
        return "rising"
    if rel_slope <= -band:
        return "falling"
    return "stable"


def analysis_g(flows: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    lo, hi = cfg.PANEL_FLOW_REPORTS
    window = flows[(flows["report_number"] >= lo) & (flows["report_number"] <= hi)]
    rows = []
    for (mid, name), g in window.groupby(["ministry_id", "canonical_name"]):
        g = g.sort_values("report_number")
        nums = g["report_number"].tolist()
        consecutive = nums == list(range(nums[0], nums[0] + len(nums)))
        base = {
            "ministry_id": mid,
            "canonical_name": name,
            "months": len(g),
            "mean_monthly_receipts": g["receipts"].mean(),
            "mean_pending": g["pending"].mean(),
        }
        if len(g) < cfg.MIN_POINTS_DEMAND_BACKLOG or not consecutive:
            rows.append({**base, "status": "insufficient months"})
            continue
        if (
            base["mean_monthly_receipts"] < cfg.MIN_MEAN_MONTHLY_RECEIPTS
            or base["mean_pending"] < cfg.MIN_MEAN_PENDING
        ):
            rows.append({**base, "status": "insufficient volume"})
            continue
        d, b = mann_kendall(g["receipts"]), mann_kendall(g["pending"])
        rows.append(
            {
                **base,
                "status": "classified",
                "demand_rel_slope_pct": d["relative_slope_pct"],
                "demand_p_modified": d["p_modified"],
                "backlog_rel_slope_pct": b["relative_slope_pct"],
                "backlog_p_modified": b["p_modified"],
            }
        )
    out = pd.DataFrame(rows)
    cls = out["status"] == "classified"
    out.loc[cls, "demand_q"] = benjamini_hochberg(out.loc[cls, "demand_p_modified"].tolist())
    out.loc[cls, "backlog_q"] = benjamini_hochberg(out.loc[cls, "backlog_p_modified"].tolist())
    band = cfg.STABLE_BAND_PCT_PER_MONTH
    out["demand_trend"] = [
        _classify(x, band) if c else ""
        for x, c in zip(out.get("demand_rel_slope_pct", np.nan), cls, strict=True)
    ]
    out["backlog_trend"] = [
        _classify(x, band) if c else ""
        for x, c in zip(out.get("backlog_rel_slope_pct", np.nan), cls, strict=True)
    ]
    out["quadrant"] = np.where(
        cls, "demand " + out["demand_trend"] + " / backlog " + out["backlog_trend"], ""
    )

    sens = []
    classified = out[cls]
    for band_alt in cfg.SENSITIVITY_BANDS:
        labels = [
            f"{_classify(d, band_alt)}/{_classify(b, band_alt)}"
            for d, b in zip(
                classified["demand_rel_slope_pct"], classified["backlog_rel_slope_pct"], strict=True
            )
        ]
        base_labels = [
            f"{x}/{y}" for x, y in zip(classified["demand_trend"], classified["backlog_trend"], strict=True)
        ]
        counts = pd.Series(labels).value_counts().to_dict()
        sens.append(
            {
                "stable_band_pct_per_month": band_alt,
                "classified_ministries": len(labels),
                "same_quadrant_as_default": sum(x == y for x, y in zip(labels, base_labels, strict=True)),
                **{f"n_{k}": v for k, v in sorted(counts.items())},
            }
        )
    return out, pd.DataFrame(sens)
