"""T6 pilot (Feb 2026 onwards): "Group A/B: Data Used for Score Calculation" tables.

These ~21-column tables list every ranked ministry "in no particular order" (no serial numbers), with
headers broken into fragments over ~10 lines. Only the first two grievance columns are extracted:
Brought Forward (pending at the start of the GRAI month) and Receipts (grievances received in the month).
The table has no Disposal or Pending column for grievances, so it cannot extend the full flow panel.

Each column is accepted only if the header fragments printed above it read "Broug…" and "Recei…";
otherwise the table is skipped with an error issue.

PILOT RESULT (DESIGN.md D-017): NOT ADOPTED. On May, Jun and Sep 2026 Group A, the extracted values were
compared with the same ministries' figures derived from the >1000-pending annexures (BF vs the previous
month-end Pending; Receipts vs year-to-date differencing): 1 of 91 comparisons matched; the pilot/annexure
ratio had median 0.953 (range 0.036-1.050). Most values are a few percent lower, which points to a
different, unstated definition (not verified); a few outliers were not investigated. The table is
therefore not part of the pipeline. Group B headers failed the column check. Reproduce with:
    python -m src.extract.grai_inputs 2026-05 2026-06 2026-09
(needs the staging JSON from `python -m src.extract.run`).
"""

from __future__ import annotations

import collections
import json
import statistics
import sys
from datetime import date
from decimal import Decimal

from src.extract.layout import PdfDocument
from src.extract.ministry_tables import PARSER_VERSION, ReportContext, _grai_period
from src.extract.model import ExtractedTable, Issue, Observation
from src.extract.rows import collect_rows
from src.extract.values import parse_number

HEADING_RX = r"Group\s+{group}:\s+Data\s+Used\s+for\s+Score\s+Calculation"
MIN_VALUES = 15  # anchors are lines ending in at least this many numbers
COLUMNS = (
    ("brought_forward", "Grievances: Brought Forward", "broug"),
    ("receipts", "Grievances: Receipts", "recei"),
)


def _column_header_ok(doc: PdfDocument, page: int, below: float, above: float, x: float, prefix: str) -> bool:
    """Header fragments within 18 pt of a column's x position, read top to bottom, start with `prefix`."""
    fragments = [
        w.text.lower()
        for ln in doc.lines(page)
        if below < ln.top < above
        for w in ln.words
        if abs(w.x0 - x) < 18
    ]
    return "".join(fragments).startswith(prefix) or any(f.startswith(prefix) for f in fragments)


def parse_grai_inputs(doc: PdfDocument, ctx: ReportContext, group: str) -> ExtractedTable | None:
    heading = _select_table_heading_numeric(doc, HEADING_RX.format(group=group))
    if heading is None:
        return None
    collected = collect_rows(doc, heading.page, heading.bottom, min_values=MIN_VALUES)
    issues: list[Issue] = []
    period = _grai_period(doc, heading, ctx, issues)
    table = ExtractedTable(
        table_type="grai_score_inputs",
        original_title=heading.text[:300],
        pdf_page_start=heading.page,
        pdf_page_end=collected.last_page,
        period_start=period[0],
        period_end=period[1],
        parser_name="grai_score_inputs_pilot",
        parser_version=PARSER_VERSION,
        grai_group=group,
        grai_methodology="GRAI_4D_dims",
    )
    table.issues.extend(issues)
    rows = collected.rows
    if not rows:
        table.issues.append(Issue("error", "no_rows", "no data rows found", heading.page))
        return table
    modal = collections.Counter(len(r.values) for r in rows).most_common(1)[0][0]
    first_rows = [r for r in rows if r.page == heading.page]
    for idx, (_, _, prefix) in enumerate(COLUMNS):
        x = statistics.median(r.value_x[idx] for r in rows if len(r.values) == modal)
        if not _column_header_ok(doc, heading.page, heading.bottom, first_rows[0].top - 6, x, prefix):
            table.issues.append(
                Issue(
                    "error",
                    "unexpected_header",
                    f"column {idx + 1} header does not read '{prefix}…'",
                    heading.page,
                )
            )
            return table
    for index, row in enumerate(rows):
        if not row.name:
            table.issues.append(
                Issue("error", "missing_name", "row has no ministry name", row.page, row.anchor_text)
            )
            continue
        if len(row.values) != modal:
            table.issues.append(
                Issue(
                    "error",
                    "column_count_mismatch",
                    f"expected {modal} values, found {len(row.values)}",
                    row.page,
                    f"{row.name} | {row.anchor_text}",
                )
            )
            continue
        for col, (code, label, _) in enumerate(COLUMNS):
            raw = row.values[col]
            table.observations.append(
                Observation(
                    row_index=index,
                    metric_code=code,
                    metric_label_original=label,
                    value_original=raw,
                    value_numeric=parse_number(raw),
                    period_start=period[0],
                    period_end=period[1],
                    pdf_page=row.page,
                    entity_name_original=row.name,
                    serial_number_original=row.serial,
                )
            )
    table.issues.append(
        Issue(
            "info",
            "pilot_partial_table",
            f"pilot: only the first 2 of {modal} columns are extracted",
            heading.page,
        )
    )
    return table


def _select_table_heading_numeric(doc: PdfDocument, pattern: str):
    for heading in reversed(doc.find_headings(pattern)):
        if collect_rows(doc, heading.page, heading.bottom, max_pages=2, min_values=MIN_VALUES).rows:
            return heading
    return None


def _annexure(month: str) -> dict[str, dict[str, Decimal]]:
    """>1000-pending annexure values per printed ministry name, from the staging JSON of `month`."""
    from src.extract.run import STAGING

    payload = json.loads(next(STAGING.glob(f"{month}_*.json")).read_text(encoding="utf-8"))
    table = next(t for t in payload["tables"] if t["table_type"] == "pending_over_1000")
    out: dict[str, dict[str, Decimal]] = collections.defaultdict(dict)
    for o in table["observations"]:
        out[o["entity_name_original"]][o["metric_code"]] = Decimal(o["value_numeric"])
    return out


def validate(months: list[str]) -> list[dict]:
    """Compare pilot values with annexure-derived ones:
    BF(m) = Pending(m-1) and Receipts(m) = YTD Receipts(m) - YTD Receipts(m-1)."""
    from src.extract.report import load_registry
    from src.extract.run import STAGING

    registry = {f"{r.month:%Y-%m}": r for r in load_registry()}
    results = []
    for month in months:
        rec = registry[month]
        cutoff = json.loads(next(STAGING.glob(f"{month}_*.json")).read_text(encoding="utf-8"))["cutoff"]
        prev = f"{rec.month.year}-{rec.month.month - 1:02d}"
        cur, old = _annexure(month), _annexure(prev)
        with PdfDocument(rec.path) as doc:
            table = parse_grai_inputs(doc, ReportContext(rec.month, date.fromisoformat(cutoff)), "A")
        values: dict[str, dict[str, Decimal]] = collections.defaultdict(dict)
        for o in table.observations if table else []:
            values[o.entity_name_original][o.metric_code] = o.value_numeric
        for name, v in values.items():
            if name in old:
                results.append(
                    {
                        "month": month,
                        "name": name,
                        "metric": "brought_forward",
                        "pilot": v["brought_forward"],
                        "annexure": old[name]["pending"],
                    }
                )
            if name in old and name in cur:
                results.append(
                    {
                        "month": month,
                        "name": name,
                        "metric": "receipts",
                        "pilot": v["receipts"],
                        "annexure": cur[name]["receipts"] - old[name]["receipts"],
                    }
                )
    return results


if __name__ == "__main__":
    rows = validate(sys.argv[1:] or ["2026-05", "2026-06", "2026-09"])
    equal = sum(r["pilot"] == r["annexure"] for r in rows)
    ratios = sorted(float(r["pilot"] / r["annexure"]) for r in rows if r["annexure"])
    print(
        f"{len(rows)} comparisons, {equal} equal; "
        f"pilot/annexure ratio median {statistics.median(ratios):.3f}, "
        f"range {ratios[0]:.3f}-{ratios[-1]:.3f}"
    )
    for r in rows[:8]:
        print(
            f"  {r['month']} {r['metric']:15} {r['name'][:45]:45} pilot={r['pilot']} annexure={r['annexure']}"
        )
