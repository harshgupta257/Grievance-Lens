"""Verified historical performance of one department, from the Part A exports (data/processed/powerbi/).

Every figure carries its source report(s). Anything not published for the department is listed under
`unavailable` instead of being estimated. No figure here says anything about how an individual complaint
will be handled.

Usage (PowerShell, project root, venv active):
    python -m src.performance.stats department_of_posts
"""

from __future__ import annotations

import json
import math
import sys
from functools import lru_cache
from pathlib import Path

import pandas as pd
from pydantic import BaseModel, Field

from src.config import PROJECT_ROOT

EXPORTS = PROJECT_ROOT / "data" / "processed" / "powerbi"
MINISTRIES = PROJECT_ROOT / "data" / "reference" / "ministries.csv"
CAVEATS = [
    "Figures are copied or derived from DARPG's monthly CPGRAMS reports for Central Ministries/Departments; "
    "they are aggregates, not a prediction for any individual complaint.",
    "Appearing on a published list reflects the list's threshold and the department's volume; it is not, on "
    "its own, evidence of poor performance.",
    "The monthly ministry panel ends in January 2026 (later ministry-level flows failed validation).",
    "Values are provisional until the project owner completes the visual verification of sampled values.",
]


class Metric(BaseModel):
    label: str
    value: str
    period: str
    source: str


class DepartmentPerformance(BaseModel):
    ministry_key: str
    department: str
    available: bool
    latest_reporting_month: str | None = None
    metrics: list[Metric] = Field(default_factory=list)
    backlog_trend: dict | None = None
    pending_series: list[dict] = Field(default_factory=list)  # month-end pending in the panel, for a chart
    unavailable: list[str] = Field(default_factory=list)
    sources: list[dict] = Field(default_factory=list)
    caveats: list[str] = Field(default_factory=lambda: list(CAVEATS))


@lru_cache(maxsize=1)
def _load(exports: Path = EXPORTS) -> dict[str, pd.DataFrame]:
    names = [
        "dim_ministry",
        "dim_report",
        "dim_list",
        "repeated_listings",
        "list_appearances",
        "backlog_growth",
        "ministry_monthly",
        "demand_backlog_quadrants",
    ]
    frames = {n: pd.read_csv(exports / f"{n}.csv") for n in names}
    keys = pd.read_csv(MINISTRIES)[["ministry_key", "canonical_name"]]
    frames["dim_ministry"] = frames["dim_ministry"].merge(keys, on="canonical_name", how="left")
    return frames


def _month(value) -> str:
    return str(value)[:7] if isinstance(value, str) and value else ""


def _report_ref(reports: pd.DataFrame, number: int) -> dict:
    r = reports[reports["report_number"] == number].iloc[0]
    return {
        "report_number": int(number),
        "report_month": _month(r["report_month"]),
        "source_url": r["source_url"],
    }


def department_performance(ministry_key: str, exports: Path = EXPORTS) -> DepartmentPerformance:
    f = _load(exports)
    dim = f["dim_ministry"]
    row = dim[dim["ministry_key"] == ministry_key]
    if row.empty:
        return DepartmentPerformance(
            ministry_key=ministry_key,
            department=ministry_key,
            available=False,
            unavailable=["This department is not in the Part A dataset."],
        )
    row = row.iloc[0]
    mid, name = int(row["ministry_id"]), row["canonical_name"]
    reports = f["dim_report"]
    out = DepartmentPerformance(
        ministry_key=ministry_key,
        department=name,
        available=True,
        latest_reporting_month=_month(row["last_report_month"]),
    )
    cited: set[int] = set()

    # repeated appearances in published lists
    rl = f["repeated_listings"][f["repeated_listings"]["ministry_id"] == mid].set_index("list_code")
    lists = f["dim_list"].set_index("list_code")
    for code in ("pending_over_1000", "long_pending_21d", "closing_time_high", "closing_time_low"):
        spec = lists.loc[code]
        if code in rl.index:
            r = rl.loc[code]
            last = int(r["last_listed_report"])
            cited.add(last)
            out.metrics.append(
                Metric(
                    label=spec["list_name"],
                    value=f"listed in {int(r['appearances'])} of {int(r['reports_with_list'])} reports "
                    f"(latest: report #{last}, {_report_ref(reports, last)['report_month']})",
                    period=f"reports #{int(spec['first_report'])}-#{int(spec['last_report'])}",
                    source="DARPG monthly reports; project count (repeated_listings)",
                )
            )
        else:
            out.unavailable.append(
                f"Not listed in any published '{spec['list_name']}' (reports "
                f"#{int(spec['first_report'])}-#{int(spec['last_report'])})."
            )

    # published average closing time: only when the department was in a Top-10 list
    la = f["list_appearances"]
    ct = la[(la["ministry_id"] == mid) & la["list_code"].isin(["closing_time_high", "closing_time_low"])]
    if not ct.empty:
        last = ct.sort_values("report_number").iloc[-1]
        n = int(last["report_number"])
        cited.add(n)
        out.metrics.append(
            Metric(
                label="Published average closing time (year-to-date)",
                value=f"{last['avg_closing_time_days']:.0f} days",
                period=f"report #{n} ({_report_ref(reports, n)['report_month']})",
                source=f"DARPG report #{n}, page {int(last['pdf_page'])} (Top-10 list)",
            )
        )
    else:
        out.unavailable.append("Average closing time: not published for this department (only Top-10 lists).")

    # monthly panel: backlog trend and month-end pending
    bg = f["backlog_growth"][f["backlog_growth"]["ministry_id"] == mid]
    mm = f["ministry_monthly"][f["ministry_monthly"]["ministry_id"] == mid].sort_values("report_number")
    if not bg.empty:
        b = bg.iloc[0]
        out.backlog_trend = {
            "period": f"{_month(b['start_month'])} to {_month(b['end_month'])}",
            "pending_start": int(b["pending_start"]),
            "pending_end": int(b["pending_end"]),
            "trend": b["trend_conclusion"],
            "relative_slope_pct_per_month": None
            if pd.isna(b["pending_relative_slope_pct"])
            else round(float(b["pending_relative_slope_pct"]), 2),
            "q_value": None if pd.isna(b["trend_q_value"]) else round(float(b["trend_q_value"]), 4),
            "method": "Mann-Kendall (autocorrelation-corrected), Benjamini-Hochberg q < 0.05",
        }
        for _, m in mm.iterrows():
            out.pending_series.append(
                {
                    "report_number": int(m["report_number"]),
                    "period_end": str(m["period_end"]),
                    "pending": int(m["pending"]),
                    "pdf_page": int(m["pdf_page"]),
                }
            )
        last_n = int(mm.iloc[-1]["report_number"])
        cited.update({int(mm.iloc[0]["report_number"]), last_n})
        out.metrics.append(
            Metric(
                label="Pending at month end (latest month in the monthly panel)",
                value=f"{int(mm.iloc[-1]['pending']):,}",
                period=str(mm.iloc[-1]["period_end"]),
                source=f"DARPG report #{last_n}, page {int(mm.iloc[-1]['pdf_page'])} (GRAI table)",
            )
        )
    else:
        out.unavailable.append("Monthly backlog series: the department is not in the monthly panel.")
    out.sources = [_report_ref(reports, n) for n in sorted(cited)]
    if isinstance(row.get("reorganisation_note"), str) and row["reorganisation_note"]:
        out.caveats.append(f"Reorganisation: {row['reorganisation_note']}.")
    return out


def _json_default(o):
    return None if isinstance(o, float) and math.isnan(o) else str(o)


def main(argv: list[str] | None = None) -> int:
    argv = argv if argv is not None else sys.argv[1:]
    if not argv:
        print("usage: python -m src.performance.stats <ministry_key>", file=sys.stderr)
        return 2
    print(json.dumps(department_performance(argv[0]).model_dump(), indent=2, default=_json_default))
    return 0


if __name__ == "__main__":
    sys.exit(main())
