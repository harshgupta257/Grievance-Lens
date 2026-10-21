"""Data-quality checks over the loaded database (Phase 3). Read-only; safe to re-run.

Usage (PowerShell, project root, venv active, after src.extract.load and src.normalise.load):
    python -m src.validate.checks

Writes docs/data_quality_checks.md (generated; do not edit) and one CSV per check with the detail rows to
data/interim/quality/. Each check states what it tests; a check *reports* problems, it never changes data.
"""

from __future__ import annotations

import csv
import sys
from dataclasses import dataclass

from sqlalchemy import Engine, text

from src.config import PROJECT_ROOT, ConfigError, load_settings
from src.db import get_engine

OUT_MD = PROJECT_ROOT / "docs" / "data_quality_checks.md"
OUT_DIR = PROJECT_ROOT / "data" / "interim" / "quality"


@dataclass(frozen=True)
class Check:
    code: str
    title: str
    tests: str
    summary_sql: str  # one row: (checked, problems)
    detail_sql: str | None = None


# Ministry-level year-to-date flow tables that share one reference period within a report.
YTD_TABLES = "('pending_over_1000', 'long_pending_top10')"

CHECKS = [
    Check(
        "C01",
        "Report continuity",
        "Every month from the first to the last report has exactly one English report, with consecutive numbers.",
        """WITH r AS (SELECT report_month, report_number,
                        report_number - row_number() OVER (ORDER BY report_month) AS grp FROM reports WHERE language = 'en')
             SELECT count(*), (SELECT count(*) FROM generate_series((SELECT min(report_month) FROM r),
                    (SELECT max(report_month) FROM r), interval '1 month') g
                    WHERE g::date NOT IN (SELECT report_month FROM r)) + (count(DISTINCT grp) - 1) FROM r""",
    ),
    Check(
        "C02",
        "No negative published values",
        "Published counts, durations and scores are never negative (also enforced by a schema constraint).",
        "SELECT count(*), count(*) FILTER (WHERE value_numeric < 0) FROM observations",
    ),
    Check(
        "C03",
        "Ministry linkage",
        "Every ministry-level observation is linked to a canonical ministry; aggregate rows are not.",
        """SELECT count(*), count(*) FILTER (WHERE (NOT is_aggregate AND ministry_id IS NULL)
                                                OR (is_aggregate AND ministry_id IS NOT NULL)) FROM observations""",
    ),
    Check(
        "C04",
        "No duplicate ministry within a table",
        "After name resolution, a ministry appears at most once per table and metric. A duplicate would mean "
        "two different printed rows were merged into one entity.",
        """SELECT count(*), count(*) FILTER (WHERE n > 1) FROM (
               SELECT table_id, ministry_id, metric_code, period_start, period_end, count(*) n FROM observations
               WHERE ministry_id IS NOT NULL GROUP BY 1, 2, 3, 4, 5) x""",
        """SELECT r.report_number, t.table_type, t.grai_group, m.canonical_name, o.metric_code,
                    string_agg(o.entity_name_original || '=' || o.value_original, ' | ') AS rows
             FROM observations o JOIN source_tables t USING (table_id) JOIN reports r USING (report_id)
             JOIN ministries m USING (ministry_id)
             GROUP BY o.table_id, r.report_number, t.table_type, t.grai_group, m.canonical_name, o.metric_code,
                      o.period_start, o.period_end HAVING count(*) > 1 ORDER BY 1""",
    ),
    Check(
        "C05",
        "Aggregate month-wise series is continuous",
        "Receipts, disposals and pending have at least one value for every month from Jan 2022 to the last report.",
        """WITH m AS (SELECT DISTINCT o.metric_code, o.period_start FROM observations o JOIN source_tables t USING (table_id)
                        WHERE t.table_type = 'overall_monthwise'),
                  g AS (SELECT c.metric_code, s::date AS month FROM (SELECT DISTINCT metric_code FROM m) c,
                        generate_series(date '2022-01-01', (SELECT max(report_month) FROM reports), interval '1 month') s)
             SELECT count(*), count(*) FILTER (WHERE m.period_start IS NULL)
             FROM g LEFT JOIN m ON m.metric_code = g.metric_code AND m.period_start = g.month""",
        """WITH m AS (SELECT DISTINCT o.metric_code, o.period_start FROM observations o JOIN source_tables t USING (table_id)
                        WHERE t.table_type = 'overall_monthwise'),
                  g AS (SELECT c.metric_code, s::date AS month FROM (SELECT DISTINCT metric_code FROM m) c,
                        generate_series(date '2022-01-01', (SELECT max(report_month) FROM reports), interval '1 month') s)
             SELECT g.metric_code, g.month FROM g LEFT JOIN m ON m.metric_code = g.metric_code AND m.period_start = g.month
             WHERE m.period_start IS NULL ORDER BY 1, 2""",
    ),
    Check(
        "C06",
        "Same month, same value across reports",
        "A month's aggregate receipts/disposals/pending printed in several reports has one value (no silent revision).",
        """SELECT count(*), count(*) FILTER (WHERE n_values > 1) FROM (
               SELECT o.metric_code, o.period_start, count(DISTINCT o.value_numeric) n_values
               FROM observations o JOIN source_tables t USING (table_id)
               WHERE t.table_type = 'overall_monthwise' GROUP BY 1, 2 HAVING count(*) > 1) x""",
        """SELECT o.metric_code, o.period_start,
                    string_agg('#' || r.report_number || '=' || o.value_original, ' ' ORDER BY r.report_number) AS values,
                    max(o.value_numeric) - min(o.value_numeric) AS spread
             FROM observations o JOIN source_tables t USING (table_id) JOIN reports r USING (report_id)
             WHERE t.table_type = 'overall_monthwise' GROUP BY 1, 2
             HAVING count(DISTINCT o.value_numeric) > 1 ORDER BY 2, 1""",
    ),
    Check(
        "C07",
        "Year-to-date overview equals the sum of its months",
        "Where a report prints both the YTD overview box and every month from January, receipts and disposals "
        "in the box equal the sum of the month-wise values of that report.",
        """WITH ov AS (SELECT t.report_id, o.metric_code, o.value_numeric v FROM observations o JOIN source_tables t USING (table_id)
                         WHERE t.table_type = 'overall_overview' AND o.metric_code IN ('receipts', 'disposals')),
                  mw AS (SELECT t.report_id, o.metric_code, sum(o.value_numeric) v, min(o.period_start) first_month
                         FROM observations o JOIN source_tables t USING (table_id)
                         WHERE t.table_type = 'overall_monthwise' GROUP BY 1, 2),
                  j AS (SELECT ov.report_id, ov.metric_code, ov.v ov_v, mw.v mw_v FROM ov JOIN mw USING (report_id, metric_code)
                        JOIN reports r USING (report_id)
                        WHERE mw.first_month = date_trunc('year', r.report_month)::date)
             SELECT count(*), count(*) FILTER (WHERE ov_v <> mw_v) FROM j""",
        """WITH ov AS (SELECT t.report_id, o.metric_code, o.value_numeric v FROM observations o JOIN source_tables t USING (table_id)
                         WHERE t.table_type = 'overall_overview' AND o.metric_code IN ('receipts', 'disposals')),
                  mw AS (SELECT t.report_id, o.metric_code, sum(o.value_numeric) v, min(o.period_start) first_month
                         FROM observations o JOIN source_tables t USING (table_id)
                         WHERE t.table_type = 'overall_monthwise' GROUP BY 1, 2)
             SELECT r.report_number, ov.metric_code, ov.v AS overview, mw.v AS sum_of_months, ov.v - mw.v AS diff
             FROM ov JOIN mw USING (report_id, metric_code) JOIN reports r USING (report_id)
             WHERE mw.first_month = date_trunc('year', r.report_month)::date AND ov.v <> mw.v ORDER BY 1, 2""",
    ),
    Check(
        "C08",
        "Long-pending rows repeat the >1000 rows",
        "A ministry in both the long-pending Top-10 and the >1000-pending list of the same report has identical "
        "BF / Receipts / Total / Disposal / Pending (both are year-to-date to the same cut-off).",
        f"""WITH x AS (SELECT t.report_id, t.table_type, o.ministry_id, o.metric_code, o.value_numeric v
                        FROM observations o JOIN source_tables t USING (table_id)
                        WHERE t.table_type IN {YTD_TABLES} AND o.metric_code <> 'pending_over_threshold')
             SELECT count(*), count(*) FILTER (WHERE a.v IS DISTINCT FROM b.v) FROM x a JOIN x b
               ON a.report_id = b.report_id AND a.ministry_id = b.ministry_id AND a.metric_code = b.metric_code
              AND a.table_type = 'long_pending_top10' AND b.table_type = 'pending_over_1000'""",
        f"""WITH x AS (SELECT t.report_id, t.table_type, o.ministry_id, o.metric_code, o.value_numeric v
                        FROM observations o JOIN source_tables t USING (table_id)
                        WHERE t.table_type IN {YTD_TABLES} AND o.metric_code <> 'pending_over_threshold')
             SELECT r.report_number, m.canonical_name, a.metric_code, a.v AS long_pending, b.v AS over_1000
             FROM x a JOIN x b ON a.report_id = b.report_id AND a.ministry_id = b.ministry_id AND a.metric_code = b.metric_code
              AND a.table_type = 'long_pending_top10' AND b.table_type = 'pending_over_1000'
             JOIN reports r ON r.report_id = a.report_id JOIN ministries m ON m.ministry_id = a.ministry_id
             WHERE a.v IS DISTINCT FROM b.v ORDER BY 1""",
    ),
    Check(
        "C09",
        "Closing-time 'cases disposed' equals annexure disposals",
        "For a ministry in both the average-closing-time list and the >1000 list of the same report, 'cases "
        "disposed' equals 'disposal' (same year-to-date period). Informational: definitions may differ.",
        """WITH c AS (SELECT t.report_id, o.ministry_id, o.value_numeric v FROM observations o JOIN source_tables t USING (table_id)
                        WHERE t.table_type IN ('avg_closing_time_high', 'avg_closing_time_low') AND o.metric_code = 'cases_disposed'),
                  p AS (SELECT t.report_id, o.ministry_id, o.value_numeric v FROM observations o JOIN source_tables t USING (table_id)
                        WHERE t.table_type = 'pending_over_1000' AND o.metric_code = 'disposals')
             SELECT count(*), count(*) FILTER (WHERE c.v <> p.v) FROM c JOIN p USING (report_id, ministry_id)""",
        """WITH c AS (SELECT t.report_id, o.ministry_id, o.value_numeric v FROM observations o JOIN source_tables t USING (table_id)
                        WHERE t.table_type IN ('avg_closing_time_high', 'avg_closing_time_low') AND o.metric_code = 'cases_disposed'),
                  p AS (SELECT t.report_id, o.ministry_id, o.value_numeric v FROM observations o JOIN source_tables t USING (table_id)
                        WHERE t.table_type = 'pending_over_1000' AND o.metric_code = 'disposals')
             SELECT r.report_number, m.canonical_name, c.v AS cases_disposed, p.v AS annexure_disposal, c.v - p.v AS diff
             FROM c JOIN p USING (report_id, ministry_id) JOIN reports r USING (report_id) JOIN ministries m USING (ministry_id)
             WHERE c.v <> p.v ORDER BY 1""",
    ),
    Check(
        "C10",
        "GRAI monthly Pending equals annexure Pending (T6 panel)",
        "In the flow-bearing GRAI layouts, month-end Pending equals the >1000 annexure's Pending for the same "
        "ministry and report (linked by canonical ministry, so name variants no longer hide matches).",
        """WITH g AS (SELECT t.report_id, o.ministry_id, o.value_numeric v FROM observations o JOIN source_tables t USING (table_id)
                        WHERE t.grai_methodology IN ('GRAI_4D_flows', 'GRAI_4D_total_flows') AND o.metric_code = 'pending'),
                  p AS (SELECT t.report_id, o.ministry_id, o.value_numeric v FROM observations o JOIN source_tables t USING (table_id)
                        WHERE t.table_type = 'pending_over_1000' AND o.metric_code = 'pending')
             SELECT count(*), count(*) FILTER (WHERE g.v <> p.v) FROM g JOIN p USING (report_id, ministry_id)""",
        """WITH g AS (SELECT t.report_id, o.ministry_id, o.value_numeric v FROM observations o JOIN source_tables t USING (table_id)
                        WHERE t.grai_methodology IN ('GRAI_4D_flows', 'GRAI_4D_total_flows') AND o.metric_code = 'pending'),
                  p AS (SELECT t.report_id, o.ministry_id, o.value_numeric v FROM observations o JOIN source_tables t USING (table_id)
                        WHERE t.table_type = 'pending_over_1000' AND o.metric_code = 'pending')
             SELECT r.report_number, m.canonical_name, g.v AS grai_pending, p.v AS annexure_pending
             FROM g JOIN p USING (report_id, ministry_id) JOIN reports r USING (report_id) JOIN ministries m USING (ministry_id)
             WHERE g.v <> p.v ORDER BY 1""",
    ),
    Check(
        "C11",
        "Validation status of observations",
        "Share of observations whose row identities failed beyond the tolerance (D-016). Warnings are listed by "
        "cause in docs/extraction_summary.md.",
        "SELECT count(*), count(*) FILTER (WHERE validation_status = 'failed') FROM observations",
    ),
    Check(
        "C12",
        "Open extraction errors",
        "No extraction issue of severity 'error' remains open.",
        "SELECT count(*), count(*) FILTER (WHERE severity = 'error' AND status = 'open') FROM extraction_issues",
    ),
]


def run(engine: Engine) -> list[dict]:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    results = []
    with engine.connect() as conn:
        for c in CHECKS:
            checked, problems = conn.execute(text(c.summary_sql)).one()
            detail_rows = 0
            if c.detail_sql:
                res = conn.execute(text(c.detail_sql))
                rows = res.all()
                detail_rows = len(rows)
                with (OUT_DIR / f"{c.code}.csv").open("w", newline="", encoding="utf-8") as fh:
                    w = csv.writer(fh, lineterminator="\n")
                    w.writerow(res.keys())
                    w.writerows(rows)
            results.append(
                {
                    "code": c.code,
                    "title": c.title,
                    "tests": c.tests,
                    "checked": checked,
                    "problems": problems,
                    "detail_rows": detail_rows,
                }
            )
    return results


def write_markdown(results: list[dict]) -> None:
    lines = [
        "# Data-quality checks",
        "",
        "Generated by `python -m src.validate.checks`. Do not edit by hand. Detail rows for each check are in "
        "`data/interim/quality/<code>.csv`. Interpretation: [data_quality.md](data_quality.md).",
        "",
        "| Code | Check | Checked | Problems | What it tests |",
        "|---|---|---|---|---|",
    ]
    for r in results:
        lines.append(f"| {r['code']} | {r['title']} | {r['checked']} | {r['problems']} | {r['tests']} |")
    OUT_MD.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    try:
        settings = load_settings()
    except ConfigError as exc:
        print(f"[config] {exc}", file=sys.stderr)
        return 1
    engine = get_engine(settings.database_url)
    try:
        results = run(engine)
    finally:
        engine.dispose()
    write_markdown(results)
    for r in results:
        print(f"{r['code']} {r['title'][:55]:55} checked={r['checked']:>6} problems={r['problems']}")
    print(f"Wrote {OUT_MD.relative_to(PROJECT_ROOT)} and {OUT_DIR.relative_to(PROJECT_ROOT)}/")
    return 0


if __name__ == "__main__":
    sys.exit(main())
