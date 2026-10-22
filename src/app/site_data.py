"""Data for the analytics pages of the website, read from the validated Part A exports.

Everything here comes from data/processed/ (Power BI exports, analysis results, mandate knowledge base);
no database is needed at run time and nothing is estimated. Values that were never published stay absent
(JSON null), never zero.
"""

from __future__ import annotations

import json
import math
from functools import lru_cache

import pandas as pd

from src.config import PROJECT_ROOT
from src.performance.stats import _load, department_performance

EXPORTS = PROJECT_ROOT / "data" / "processed" / "powerbi"
ANALYSIS = PROJECT_ROOT / "data" / "processed" / "analysis"
KB = PROJECT_ROOT / "data" / "processed" / "knowledge" / "mandates.jsonl"


def _clean(value):
    """JSON-safe scalar: NaN -> None, numpy -> python, dates -> ISO text."""
    if value is None:
        return None
    if isinstance(value, float) and math.isnan(value):
        return None
    if hasattr(value, "item"):
        value = value.item()
        if isinstance(value, float) and math.isnan(value):
            return None
    return value


def _records(df: pd.DataFrame) -> list[dict]:
    return [{k: _clean(v) for k, v in row.items()} for row in df.to_dict(orient="records")]


@lru_cache(maxsize=1)
def _extra() -> dict[str, pd.DataFrame]:
    names = ["overall_trend", "grai_ranks", "scorecard", "trend_tests", "demand_backlog_sensitivity"]
    frames = {n: pd.read_csv(EXPORTS / f"{n}.csv") for n in names}
    frames["by_year"] = pd.read_csv(ANALYSIS / "A_overall_by_year.csv")
    return frames


@lru_cache(maxsize=1)
def _kb() -> dict[str, dict]:
    if not KB.exists():
        return {}
    return {
        r["ministry_key"]: r
        for r in (json.loads(line) for line in KB.read_text(encoding="utf-8").splitlines())
    }


def _ministries() -> pd.DataFrame:
    return _load(EXPORTS)["dim_ministry"]


def _name_by_id() -> dict[int, str]:
    m = _ministries()
    return dict(zip(m["ministry_id"], m["canonical_name"], strict=True))


def _key_by_id() -> dict[int, str]:
    m = _ministries()
    return dict(zip(m["ministry_id"], m["ministry_key"], strict=True))


def overview() -> dict:
    x = _extra()
    t = x["overall_trend"].sort_values("month")
    latest = t.iloc[-1]
    tests = x["trend_tests"]
    f1 = tests[tests["family"].str.startswith("F1")]
    f3 = tests[tests["family"].str.startswith("F3")]
    reports = _load(EXPORTS)["dim_report"]
    return {
        "latest": {
            "month": str(latest["month"])[:7],
            "receipts": _clean(latest["receipts"]),
            "disposals": _clean(latest["disposals"]),
            "pending": _clean(latest["pending"]),
            "source_report": _clean(latest["source_report"]),
        },
        "coverage": {
            "reports": int(len(reports)),
            "first_report_month": str(reports["report_month"].min())[:7],
            "last_report_month": str(reports["report_month"].max())[:7],
            "ministries": int(len(_ministries())),
            "months": int(len(t)),
        },
        "monthly": _records(
            t[["month", "receipts", "disposals", "pending", "has_partial_value", "source_report"]].assign(
                month=t["month"].astype(str).str[:7]
            )
        ),
        "by_year": _records(x["by_year"]),
        "trend_tests": _records(
            f1[["subject", "n", "span", "sen_slope", "relative_slope_pct", "q_value", "conclusion"]]
        ),
        "ministry_trends": f3["conclusion"].value_counts().to_dict(),
    }


def ministries() -> list[dict]:
    f = _load(EXPORTS)
    x = _extra()
    m = f["dim_ministry"]
    rl = f["repeated_listings"]
    over = rl[rl["list_code"] == "pending_over_1000"].set_index("ministry_id")
    bg = f["backlog_growth"].set_index("ministry_id")
    sc = x["scorecard"].set_index("ministry_id")
    kb = _kb()
    rows = []
    for _, r in m.iterrows():
        mid = int(r["ministry_id"])
        rows.append(
            {
                "ministry_key": r["ministry_key"],
                "name": r["canonical_name"],
                "entity_type": r["entity_type"],
                "first_month": str(r["first_report_month"])[:7],
                "last_month": str(r["last_report_month"])[:7],
                "grai_group": _clean(r["grai_group_2026"]),
                "over_1000_reports": _clean(over["appearances"].get(mid)),
                "panel_pending_end": _clean(bg["pending_end"].get(mid)),
                "backlog_trend": _clean(bg["trend_conclusion"].get(mid)),
                "score": _clean(sc["score"].get(mid)),
                "rank_in_group": _clean(sc["rank_in_group"].get(mid)),
                "in_assistant": r["ministry_key"] in kb,
            }
        )
    return sorted(rows, key=lambda d: d["name"])


def ministry_detail(key: str) -> dict | None:
    f = _load(EXPORTS)
    m = f["dim_ministry"]
    row = m[m["ministry_key"] == key]
    if row.empty:
        return None
    mid = int(row.iloc[0]["ministry_id"])
    perf = department_performance(key, EXPORTS).model_dump()
    g = _extra()["grai_ranks"]
    g = g[g["ministry_id"] == mid].merge(
        f["dim_report"][["report_number", "report_month"]], on="report_number"
    )
    grai = _records(
        g[
            [
                "report_number",
                "report_month",
                "grai_group",
                "grai_methodology",
                "grai_rank",
                "group_size",
                "rank_percentile",
            ]
        ]
        .assign(report_month=g["report_month"].astype(str).str[:7])
        .sort_values("report_number")
    )
    la = f["list_appearances"]
    lists = f["dim_list"].set_index("list_code")
    appearances = []
    for code, grp in la[la["ministry_id"] == mid].groupby("list_code"):
        appearances.append(
            {
                "list_code": code,
                "list_name": lists.loc[code, "list_name"],
                "reports": sorted(int(n) for n in grp["report_number"].unique()),
                "reports_with_list": int(lists.loc[code, "reports_with_list"]),
            }
        )
    kb = _kb().get(key)
    mandate = None
    if kb:
        mandate = {
            "official_name": kb["official_name"],
            "parent_ministry": kb["parent_ministry"],
            "responsibilities": kb["grievance_responsibilities"],
            "official_items": len(kb["official_mandate"]),
            "official_urls": [u["url"] for u in kb["official_urls"]],
        }
    return {"performance": perf, "grai": grai, "list_appearances": appearances, "mandate": mandate}


def scorecard() -> dict:
    sc = _extra()["scorecard"].copy()
    names, keys = _name_by_id(), _key_by_id()
    sc["name"] = sc["ministry_id"].map(names)
    sc["ministry_key"] = sc["ministry_id"].map(keys)
    cols = [
        "ministry_key",
        "name",
        "grai_group",
        "score",
        "rank_in_group",
        "rank_random_p05",
        "rank_random_p95",
        "group_size",
        "components_used",
        "grai_percentile_pctl",
        "backlog_trend_pct_pctl",
        "backlog_months_pctl",
        "slow_closing_share_pctl",
        "not_scored_reason",
    ]
    out = {}
    for grp, g in sc.groupby("grai_group"):
        out[grp] = _records(g.sort_values(["rank_in_group", "name"], na_position="last")[cols])
    return out


def listings() -> dict:
    f = _load(EXPORTS)
    names, keys = _name_by_id(), _key_by_id()
    rl = f["repeated_listings"].copy()
    rl["name"] = rl["ministry_id"].map(names)
    rl["ministry_key"] = rl["ministry_id"].map(keys)
    lists = _records(f["dim_list"])
    by_code = {
        code: _records(
            g.sort_values(["appearances", "longest_consecutive_run"], ascending=False)[
                [
                    "ministry_key",
                    "name",
                    "appearances",
                    "reports_with_list",
                    "share_of_reports",
                    "longest_consecutive_run",
                    "run_to_last_list",
                    "median_value",
                    "max_value",
                    "reorganisation_note",
                ]
            ]
        )
        for code, g in rl.groupby("list_code")
    }
    return {"lists": lists, "by_list": by_code}


def demand_backlog() -> dict:
    f = _load(EXPORTS)
    q = f["demand_backlog_quadrants"].copy()
    q["name"] = q["ministry_id"].map(_name_by_id())
    q["ministry_key"] = q["ministry_id"].map(_key_by_id())
    sens = _extra()["demand_backlog_sensitivity"]
    return {
        "ministries": _records(
            q[
                [
                    "ministry_key",
                    "name",
                    "status",
                    "mean_monthly_receipts",
                    "mean_pending",
                    "demand_rel_slope_pct",
                    "backlog_rel_slope_pct",
                    "demand_trend",
                    "backlog_trend",
                    "quadrant",
                    "demand_q",
                    "backlog_q",
                ]
            ]
        ),
        "sensitivity": _records(sens),
    }
