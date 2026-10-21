"""Extract all (or selected) reports to staging JSON and write an extraction summary.

Usage (PowerShell, project root, venv active):
    python -m src.extract.run                      # all 53 English reports
    python -m src.extract.run --report 2025-08     # one report (repeatable)
    python -m src.extract.run --limit 3            # first N reports, for quick checks

Outputs:
    data/interim/extracted/<YYYY-MM>_rNN_en.json   one file per report (tables, observations, issues)
    data/interim/extraction_summary.csv            one row per report and table
    docs/extraction_summary.md                     human-readable summary (regenerated each run)
Load the staging files into PostgreSQL afterwards with:  python -m src.extract.load
"""

from __future__ import annotations

import argparse
import collections
import csv
import json
import sys
import time
from dataclasses import asdict
from datetime import date
from decimal import Decimal
from pathlib import Path

from src.config import PROJECT_ROOT
from src.extract.model import Issue
from src.extract.overrides import apply_overrides, load_overrides
from src.extract.report import ReportExtraction, extract_report, load_registry

STAGING = PROJECT_ROOT / "data" / "interim" / "extracted"
SUMMARY_CSV = PROJECT_ROOT / "data" / "interim" / "extraction_summary.csv"
SUMMARY_MD = PROJECT_ROOT / "docs" / "extraction_summary.md"


def _json_default(value):
    if isinstance(value, (date, Decimal)):
        return str(value)
    raise TypeError(f"cannot serialise {type(value).__name__}")


def write_staging(result: ReportExtraction, directory: Path = STAGING) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    payload = {
        "report": {**asdict(result.record), "month": result.record.month},
        "cutoff": result.cutoff,
        "cutoff_method": result.cutoff_method,
        "page_count": result.page_count,
        "issues": [asdict(i) for i in result.issues],
        "tables": [t.to_dict() for t in result.tables],
    }
    path = directory / f"{result.record.key}.json"
    path.write_text(
        json.dumps(payload, default=_json_default, indent=1, ensure_ascii=False), encoding="utf-8"
    )
    return path


def summarise(results: list[ReportExtraction]) -> list[dict]:
    rows = []
    for r in results:
        for t in r.tables:
            status = collections.Counter(o.validation_status for o in t.observations)
            sev = collections.Counter(i.severity for i in t.issues)
            rows.append(
                {
                    "report": r.record.key,
                    "table": t.table_type + (f":{t.grai_group}" if t.grai_group else ""),
                    "pages": f"{t.pdf_page_start}-{t.pdf_page_end}",
                    "period": f"{t.period_start}..{t.period_end}",
                    "layout": t.grai_methodology or "",
                    "threshold": t.threshold_value or "",
                    "rows": len({o.row_index for o in t.observations}),
                    "observations": len(t.observations),
                    "valid": status["valid"],
                    "warning": status["warning"],
                    "failed": status["failed"],
                    "unvalidated": status["unvalidated"],
                    "overridden": status["overridden"],
                    "issues_error": sev["error"],
                    "issues_warning": sev["warning"],
                    "issues_info": sev["info"],
                }
            )
        for i in r.issues:
            rows.append(
                {
                    "report": r.record.key,
                    "table": f"(report) {i.issue_type}",
                    "issues_error": int(i.severity == "error"),
                    "issues_warning": int(i.severity == "warning"),
                    "issues_info": int(i.severity == "info"),
                }
            )
    return rows


def write_summary(results: list[ReportExtraction], rows: list[dict]) -> None:
    SUMMARY_CSV.parent.mkdir(parents=True, exist_ok=True)
    fields = [
        "report",
        "table",
        "pages",
        "period",
        "layout",
        "threshold",
        "rows",
        "observations",
        "valid",
        "warning",
        "failed",
        "unvalidated",
        "overridden",
        "issues_error",
        "issues_warning",
        "issues_info",
    ]
    with SUMMARY_CSV.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=fields, restval="")
        w.writeheader()
        w.writerows(rows)

    by_table: dict[str, collections.Counter] = collections.defaultdict(collections.Counter)
    for row in rows:
        if row["table"].startswith("(report)"):
            continue
        c = by_table[row["table"]]
        c["reports"] += 1
        for k in (
            "rows",
            "observations",
            "valid",
            "warning",
            "failed",
            "unvalidated",
            "overridden",
            "issues_error",
            "issues_warning",
        ):
            c[k] += int(row[k] or 0)
    issue_types = collections.Counter(
        (i.severity, i.issue_type) for r in results for t in r.tables for i in t.issues
    ) + collections.Counter((i.severity, i.issue_type) for r in results for i in r.issues)

    lines = [
        "# Extraction summary",
        "",
        "Generated by `python -m src.extract.run`. Do not edit by hand. Counts are produced by the parsers; "
        "they are not a statement that every value is correct. "
        "See `docs/data_quality.md` (Phase 3) for verification.",
        "",
        f"Reports processed: **{len(results)}**. "
        f"Tables extracted: **{sum(len(r.tables) for r in results)}**. "
        f"Observations: **{sum(len(t.observations) for r in results for t in r.tables)}**.",
        "",
        "## By table type",
        "",
        "| Table | Reports | Rows | Observations | Valid | Warning | Failed | Unvalidated | Overridden "
        "| Error issues | Warning issues |",
        "|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for name in sorted(by_table):
        c = by_table[name]
        lines.append(
            f"| {name} | {c['reports']} | {c['rows']} | {c['observations']} | {c['valid']} | "
            f"{c['warning']} | {c['failed']} | {c['unvalidated']} | {c['overridden']} | "
            f"{c['issues_error']} | {c['issues_warning']} |"
        )
    lines += ["", "## Issues by type", "", "| Severity | Type | Count |", "|---|---|---|"]
    order = {"error": 0, "warning": 1, "info": 2}
    for (sev, typ), n in sorted(issue_types.items(), key=lambda kv: (order[kv[0][0]], -kv[1])):
        lines.append(f"| {sev} | {typ} | {n} |")
    lines += [
        "",
        "## Per report",
        "",
        "| Report | Cut-off | Tables | Observations | Error issues | Missing tables |",
        "|---|---|---|---|---|---|",
    ]
    for r in results:
        errors = sum(i.severity == "error" for t in r.tables for i in t.issues) + sum(
            i.severity == "error" for i in r.issues
        )
        missing = ", ".join(
            i.description.split(" not found")[0] for i in r.issues if i.issue_type == "table_not_found"
        )
        lines.append(
            f"| {r.record.key} | {r.cutoff} ({r.cutoff_method.split(' ')[0]}) | {len(r.tables)} | "
            f"{sum(len(t.observations) for t in r.tables)} | {errors} | {missing or '—'} |"
        )
    SUMMARY_MD.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _reload(result: ReportExtraction) -> ReportExtraction:
    """Re-read issues and validation statuses written to the staging file after extraction."""
    payload = json.loads((STAGING / f"{result.record.key}.json").read_text(encoding="utf-8"))
    for table, saved in zip(result.tables, payload["tables"], strict=True):
        known = {i.issue_type for i in table.issues}
        for i in saved["issues"]:
            if i["issue_type"] not in known:
                table.issues.append(Issue(**i))
        for obs, saved_obs in zip(table.observations, saved["observations"], strict=True):
            obs.validation_status = saved_obs["validation_status"]
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Extract CPGRAMS report tables to staging JSON.")
    parser.add_argument(
        "--report", action="append", metavar="YYYY-MM", help="only this report month (repeatable)"
    )
    parser.add_argument("--limit", type=int, help="process at most N reports")
    args = parser.parse_args(argv)

    records = load_registry()
    if args.report:
        records = [r for r in records if f"{r.month:%Y-%m}" in set(args.report)]
    if args.limit:
        records = records[: args.limit]
    missing = [r.filename for r in records if not r.path.exists()]
    if missing:
        print(f"Missing PDFs ({len(missing)}): run  python -m src.discover.download  first.", file=sys.stderr)
        return 1
    overrides = load_overrides()
    results = []
    for rec in records:
        t0 = time.perf_counter()
        result = extract_report(rec)
        n_over = apply_overrides(result, overrides)
        write_staging(result)
        results.append(result)
        errors = sum(i.severity == "error" for t in result.tables for i in t.issues) + sum(
            i.severity == "error" for i in result.issues
        )
        print(
            f"{rec.key}: {len(result.tables)} tables, "
            f"{sum(len(t.observations) for t in result.tables)} observations, "
            f"{errors} error issues, {n_over} overrides ({time.perf_counter() - t0:.1f}s)"
        )
    from src.extract.reprints import flag_reprints  # local import: reprints imports STAGING from this module

    flagged = flag_reprints()
    if flagged:
        print(f"Flagged {len(flagged)} tables identical to the previous report: {flagged}")
        # re-read the flags into the in-memory results so the summary counts them
        results = [_reload(r) for r in results]
    write_summary(results, summarise(results))
    print(
        f"Wrote {len(results)} staging files to {STAGING.relative_to(PROJECT_ROOT)} "
        f"and {SUMMARY_MD.relative_to(PROJECT_ROOT)}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
