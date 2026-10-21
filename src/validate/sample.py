"""Draw the manual-verification sample: a stratified, reproducible random selection of extracted values.

Usage (PowerShell, project root, venv active, database loaded):
    python -m src.validate.sample            # writes docs/manual_verification.csv (12 values)

One value per stratum (table type x era), chosen by a seeded hash of the value's natural key, so the same
values are drawn after every reload (database ids change, natural keys do not). The reviewer opens each PDF
at the physical page given, finds the row, and fills `your_value` and `match` (Y/N).
"""

from __future__ import annotations

import csv
import sys

from sqlalchemy import Engine, text

from src.config import PROJECT_ROOT, ConfigError, load_settings
from src.db import get_engine

OUT = PROJECT_ROOT / "docs" / "manual_verification.csv"
SEED = "cpgrams-2026"
STRATA = [  # (table_type, first report number, last report number) — 12 strata
    ("overall_monthwise", 1, 26),
    ("overall_monthwise", 27, 53),
    ("pending_over_1000", 1, 26),
    ("pending_over_1000", 27, 53),
    ("long_pending_top10", 1, 26),
    ("long_pending_top10", 27, 53),
    ("avg_closing_time_high", 1, 53),
    ("avg_closing_time_low", 1, 53),
    ("grai_ranking", 4, 12),
    ("grai_ranking", 13, 25),
    ("grai_ranking", 26, 45),
    ("grai_ranking", 46, 53),
]

SQL = """
SELECT r.report_number, to_char(r.report_month, 'YYYY-MM') AS report_month, r.filename, o.pdf_page,
       CASE WHEN r.printed_page_offset IS NULL THEN '' ELSE (o.pdf_page + r.printed_page_offset)::text END
           AS printed_page,
       t.table_type, coalesce(t.grai_group, '') AS grai_group, left(t.original_title, 90) AS table_title,
       coalesce(o.serial_number_original, '') AS serial,
       coalesce(o.entity_name_original, 'Central Secretariat total, ' || to_char(o.period_start, 'Mon YYYY'))
           AS row_label,
       o.metric_label_original AS column_label, o.value_original AS extracted_value
FROM observations o JOIN source_tables t USING (table_id) JOIN reports r USING (report_id)
WHERE t.table_type = :tt AND r.report_number BETWEEN :lo AND :hi AND r.language = 'en'
ORDER BY md5(:seed || r.report_number || t.table_type || coalesce(t.grai_group, '') || o.row_index
             || o.metric_code || o.period_start)
LIMIT 1
"""


def draw(engine: Engine) -> list[dict]:
    rows = []
    with engine.connect() as conn:
        for n, (tt, lo, hi) in enumerate(STRATA, start=1):
            row = (
                conn.execute(text(SQL), {"tt": tt, "lo": lo, "hi": hi, "seed": SEED}).mappings().one_or_none()
            )
            if row is None:
                raise SystemExit(f"stratum {tt} #{lo}-#{hi} has no observations: is the database loaded?")
            rows.append({"id": n, **row, "your_value": "", "match": "", "notes": ""})
    return rows


def main() -> int:
    try:
        settings = load_settings()
    except ConfigError as exc:
        print(f"[config] {exc}", file=sys.stderr)
        return 1
    engine = get_engine(settings.database_url)
    try:
        rows = draw(engine)
    finally:
        engine.dispose()
    with OUT.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0]), lineterminator="\n")
        w.writeheader()
        w.writerows(rows)
    print(f"Wrote {len(rows)} values to {OUT.relative_to(PROJECT_ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
