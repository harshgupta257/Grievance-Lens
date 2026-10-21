"""Load staging JSON (from `python -m src.extract.run`) into PostgreSQL. Safe to re-run.

Usage (PowerShell, project root, venv active, database running and migrated):
    python -m src.extract.load                    # all staging files
    python -m src.extract.load --report 2025-08   # one report (repeatable)

Each report is loaded in its own transaction: the report row is upserted by checksum, its previous
tables/observations/issues are deleted (cascade), and the new ones inserted. Re-running therefore
replaces a report's data exactly and never duplicates it.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

from psycopg2.extras import execute_values
from sqlalchemy import Engine, text

from src.config import ConfigError, load_settings
from src.db import get_engine, pending_migrations
from src.extract.run import STAGING

_OFFSET = re.compile(r"printed page label = pdf page ([+-]\d+)")


def _printed_label(pdf_page: int, offset: int | None) -> str | None:
    return None if offset is None else str(pdf_page + offset)


def load_report(engine: Engine, payload: dict) -> dict[str, int]:
    rep = payload["report"]
    m = _OFFSET.search(rep["notes"])
    offset = int(m.group(1)) if m else None
    counts = {"tables": 0, "observations": 0, "issues": 0}
    with engine.begin() as conn:
        report_id = conn.execute(
            text("""
            INSERT INTO reports (report_number, report_month, language, data_cutoff_date, filename,
                source_url, sha256, cms_media_id, page_count, printed_page_offset,
                report_number_inferred, notes)
            VALUES (:n, :month, :lang, :cutoff, :filename, :url, :sha, :media, :pages, :offset,
                :inferred, :notes)
            ON CONFLICT (sha256) DO UPDATE SET
                data_cutoff_date = EXCLUDED.data_cutoff_date, page_count = EXCLUDED.page_count,
                printed_page_offset = EXCLUDED.printed_page_offset, notes = EXCLUDED.notes
            RETURNING report_id"""),
            {
                "n": rep["report_number"],
                "month": rep["month"],
                "lang": rep["language"],
                "cutoff": payload["cutoff"],
                "filename": rep["filename"],
                "url": rep["source_url"],
                "sha": rep["sha256"],
                "media": rep["cms_media_id"],
                "pages": payload["page_count"],
                "offset": offset,
                "inferred": rep["report_number_inferred"],
                "notes": rep["notes"],
            },
        ).scalar_one()
        conn.execute(text("DELETE FROM extraction_issues WHERE report_id = :r"), {"r": report_id})
        conn.execute(text("DELETE FROM source_tables WHERE report_id = :r"), {"r": report_id})  # cascades
        cur = conn.connection.cursor()
        for t in payload["tables"]:
            table_id = conn.execute(
                text("""
                INSERT INTO source_tables (report_id, table_type, original_title, pdf_page_start,
                    pdf_page_end,
                    printed_page_label, period_start, period_end, threshold_value, threshold_unit, grai_group,
                    grai_methodology, parser_name, parser_version, extraction_method)
                VALUES (:r, :type, :title, :p0, :p1, :label, :ps, :pe, :thr, :unit, :grp, :meth, :pn, :pv,
                    :em)
                RETURNING table_id"""),
                {
                    "r": report_id,
                    "type": t["table_type"],
                    "title": t["original_title"],
                    "p0": t["pdf_page_start"],
                    "p1": t["pdf_page_end"],
                    "label": _printed_label(t["pdf_page_start"], offset),
                    "ps": t["period_start"],
                    "pe": t["period_end"],
                    "thr": t["threshold_value"],
                    "unit": t["threshold_unit"],
                    "grp": t["grai_group"],
                    "meth": t["grai_methodology"],
                    "pn": t["parser_name"],
                    "pv": t["parser_version"],
                    "em": t["extraction_method"],
                },
            ).scalar_one()
            obs = [
                (
                    table_id,
                    o["row_index"],
                    o["serial_number_original"],
                    o["entity_name_original"] is None,
                    o["entity_name_original"],
                    o["metric_code"],
                    o["metric_label_original"],
                    o["value_original"],
                    o["value_numeric"],
                    o["period_start"],
                    o["period_end"],
                    o["pdf_page"],
                    o["validation_status"],
                )
                for o in t["observations"]
            ]
            execute_values(
                cur,
                """
                INSERT INTO observations (table_id, row_index, serial_number_original, is_aggregate,
                    entity_name_original, metric_code, metric_label_original, value_original, value_numeric,
                    period_start, period_end, pdf_page, validation_status) VALUES %s""",
                obs,
                page_size=1000,
            )
            issues = [
                (
                    report_id,
                    table_id,
                    i["pdf_page"],
                    i["severity"],
                    i["issue_type"],
                    i["raw_text"],
                    i["description"],
                    i["status"],
                    i["resolution_note"],
                )
                for i in t["issues"]
            ]
            if issues:
                execute_values(
                    cur,
                    """
                    INSERT INTO extraction_issues (report_id, table_id, pdf_page, severity, issue_type,
                        raw_text,
                        description, status, resolution_note) VALUES %s""",
                    issues,
                )
            counts["tables"] += 1
            counts["observations"] += len(obs)
            counts["issues"] += len(issues)
        report_issues = [
            (
                report_id,
                None,
                i["pdf_page"],
                i["severity"],
                i["issue_type"],
                i["raw_text"],
                i["description"],
                i["status"],
                i["resolution_note"],
            )
            for i in payload["issues"]
        ]
        if report_issues:
            execute_values(
                cur,
                """
                INSERT INTO extraction_issues (report_id, table_id, pdf_page, severity, issue_type, raw_text,
                    description, status, resolution_note) VALUES %s""",
                report_issues,
            )
            counts["issues"] += len(report_issues)
    return counts


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Load staging JSON into PostgreSQL (idempotent).")
    parser.add_argument("--report", action="append", metavar="YYYY-MM")
    args = parser.parse_args(argv)
    try:
        settings = load_settings()
    except ConfigError as exc:
        print(f"[config] {exc}", file=sys.stderr)
        return 1
    engine = get_engine(settings.database_url)
    try:
        if pending_migrations(engine):
            print("Pending migrations: run  python -m src.check_db --migrate  first.", file=sys.stderr)
            return 1
        files = sorted(Path(STAGING).glob("*.json"))
        if args.report:
            files = [f for f in files if f.name[:7] in set(args.report)]
        if not files:
            print("No staging files: run  python -m src.extract.run  first.", file=sys.stderr)
            return 1
        totals = {"tables": 0, "observations": 0, "issues": 0}
        for f in files:
            counts = load_report(engine, json.loads(f.read_text(encoding="utf-8")))
            for k, v in counts.items():
                totals[k] += v
        print(
            f"Loaded {len(files)} reports: {totals['tables']} tables, {totals['observations']} observations, "
            f"{totals['issues']} issues"
        )
    finally:
        engine.dispose()
    return 0


if __name__ == "__main__":
    sys.exit(main())
