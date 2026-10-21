"""Parsers for Central Secretariat aggregate tables (T1).

* Month-wise series: the data table printed under the "Month-wise Status of Grievances" chart, with rows
  `Receipts|Received`, `Disposal`, `Pending|Pendency` and a month-label row. Chart-only pages (line or
  bar charts whose labels float near the marks) are deliberately *not* parsed: values cannot be tied to
  a series reliably. Every month Jan 2022 - Sep 2026 is covered by at least one clean table (Phase 2 survey).
* Overview box: year-to-date Brought Forward / Receipts / Disposed / Pending, used as a cross-check.
"""

from __future__ import annotations

import re
from datetime import date

from src.extract.layout import PdfDocument
from src.extract.ministry_tables import PARSER_VERSION, ReportContext
from src.extract.model import ExtractedTable, Issue, Observation
from src.extract.values import (
    assign_years,
    is_number,
    month_end,
    parse_month_label,
    parse_number,
    parse_period,
)

SERIES = {
    "receipts": ("receipts", "received"),
    "disposals": ("disposal", "disposed"),
    "pending": ("pending", "pendency"),
}
MONTHWISE_RX = re.compile(
    r"Month[- ]wise\s+Status\s+of\s+(Grievances|Receipts)|Status\s+of\s+Receipts,\s+Disposal", re.I
)


def _series_rows(lines) -> dict[str, tuple[str, list[str], int]]:
    found: dict[str, tuple[str, list[str], int]] = {}
    for line in lines:
        tokens = [w.text for w in line.words]
        if len(tokens) < 2 or not all(is_number(t) for t in tokens[1:]):
            continue
        for code, labels in SERIES.items():
            if tokens[0].lower() in labels and code not in found:
                found[code] = (tokens[0], tokens[1:], line.page)
    return found


def _month_labels(lines) -> list[tuple[int, int | None]] | None:
    for line in lines:
        parsed = [parse_month_label(w.text) for w in line.words]
        if len(parsed) >= 1 and all(parsed):
            return parsed  # type: ignore[return-value]
    return None


def parse_monthwise(doc: PdfDocument, ctx: ReportContext) -> ExtractedTable | None:
    for page in range(4, doc.page_count + 1):
        text = doc.page_text(page)
        heading = MONTHWISE_RX.search(text)
        if not heading or re.search(r"of\s+Appeals", text[heading.start() : heading.end() + 40], re.I):
            continue
        lines = doc.lines(page)
        rows = _series_rows(lines)
        if len(rows) < 3:
            continue  # chart-only page (or appeals); look further
        labels = _month_labels(lines)
        table = ExtractedTable(
            table_type="overall_monthwise",
            original_title=heading.group(0),
            pdf_page_start=page,
            pdf_page_end=page,
            period_start=ctx.month,
            period_end=ctx.cutoff,
            parser_name="monthwise_series",
            parser_version=PARSER_VERSION,
        )
        months = assign_years(labels, ctx.month.year, ctx.month.month) if labels else None
        if months is None:
            table.issues.append(
                Issue(
                    "error",
                    "month_labels",
                    "month labels missing or not consecutive months ending at the report month",
                    page,
                    " ".join(str(x) for x in (labels or [])),
                )
            )
            return table
        for code, (label, values, row_page) in rows.items():
            if len(values) != len(months):
                table.issues.append(
                    Issue(
                        "error",
                        "column_count_mismatch",
                        f"{label}: {len(values)} values for {len(months)} months",
                        row_page,
                        " ".join(values),
                    )
                )
                continue
            for i, (raw, first) in enumerate(zip(values, months, strict=True)):
                last = (
                    ctx.cutoff
                    if (first.year, first.month) == (ctx.month.year, ctx.month.month)
                    else month_end(first.year, first.month)
                )
                table.observations.append(
                    Observation(
                        row_index=i,
                        metric_code=code,
                        metric_label_original=label,
                        value_original=raw,
                        value_numeric=parse_number(raw),
                        period_start=first,
                        period_end=last,
                        pdf_page=row_page,
                    )
                )
        table.period_start, table.period_end = months[0], ctx.cutoff
        _validate_chain(table)
        if ctx.cutoff.day != month_end(ctx.cutoff.year, ctx.cutoff.month).day:
            table.issues.append(
                Issue(
                    "info",
                    "non_month_end_cutoff",
                    f"report cut-off is {ctx.cutoff.isoformat()}; earlier months in this series may also "
                    "end on that day of the month, not the calendar month end",
                    page,
                )
            )
        return table
    return None


def _validate_chain(table: ExtractedTable) -> None:
    """Pending(t) = Pending(t-1) + Receipts(t) - Disposal(t) is checked month to month. DARPG figures do not
    always satisfy it exactly (transfers and re-opened cases), so a mismatch is a warning, not a failure."""
    by = {(o.metric_code, o.period_start): o for o in table.observations}
    months = sorted({o.period_start for o in table.observations})
    for prev, cur in zip(months, months[1:], strict=False):
        try:
            p0, p1 = by[("pending", prev)].value_numeric, by[("pending", cur)].value_numeric
            r, d = by[("receipts", cur)].value_numeric, by[("disposals", cur)].value_numeric
        except KeyError:
            continue
        gap = p1 - (p0 + r - d)
        status = "valid" if gap == 0 else "warning"
        for code in ("pending", "receipts", "disposals"):
            by[(code, cur)].validation_status = status
        if gap:
            table.issues.append(
                Issue(
                    "info",
                    "flow_identity_gap",
                    f"{cur:%b %Y}: pending differs from previous pending + receipts - disposal by {gap}",
                    table.pdf_page_start,
                )
            )


def parse_overview(doc: PdfDocument, ctx: ReportContext) -> ExtractedTable | None:
    """The YTD overview box: header words Brought/Forward, Receipts, Disposed|Disposal, Pending, with values
    on the line(s) below, matched to the nearest header word by x position."""
    for page in range(4, min(doc.page_count, 12) + 1):
        # GRAI ranking tables (#36-#44) also have a "Brought Forward / Receipts / Disposal / Pending" header
        # followed by numbers; they are never the overview box.
        if re.search(r"Ranking\s+of\s+Ministries|GRAI\s+Score|GRAI\s+Rank", doc.page_text(page), re.I):
            continue
        lines = doc.lines(page)
        for i, line in enumerate(lines):
            texts = [w.text.lower() for w in line.words]
            if "receipts" not in texts or not any(t.startswith("pend") for t in texts):
                continue
            if not any(
                t == "brought"
                for ln in lines[max(0, i - 1) : i + 2]
                for t in (w.text.lower() for w in ln.words)
            ):
                continue
            header = [
                w
                for ln in lines[max(0, i - 1) : i + 2]
                for w in ln.words
                if w.text.lower() in {"brought", "receipts", "disposed", "disposal", "pending"}
            ]
            centers = {_overview_code(w.text): (w.x0 + w.x1) / 2 for w in header}
            if set(centers) != {"brought_forward", "receipts", "disposals", "pending"}:
                continue
            # aggregate counts are whole numbers >= 1,000 (this skips chart-axis digits and GRAI scores)
            values = [
                w
                for ln in lines[i + 1 : i + 5]
                for w in ln.words
                if re.fullmatch(r"[\d,]+", w.text) and len(w.text.replace(",", "")) >= 4
            ]
            assigned: dict[str, str] = {}
            for w in values:
                code = min(centers, key=lambda c: abs(centers[c] - (w.x0 + w.x1) / 2))
                assigned.setdefault(code, w.text)
            if len(assigned) != 4:
                continue
            period_text = " ".join(ln.text for ln in lines[i : i + 6])
            period = parse_period(period_text, cutoff=ctx.cutoff) or (date(ctx.month.year, 1, 1), ctx.cutoff)
            table = ExtractedTable(
                table_type="overall_overview",
                original_title=" ".join(ln.text for ln in lines[max(0, i - 2) : i + 1])[:300],
                pdf_page_start=page,
                pdf_page_end=page,
                period_start=period[0],
                period_end=period[1],
                parser_name="overview_box",
                parser_version=PARSER_VERSION,
            )
            nums = {c: parse_number(v) for c, v in assigned.items()}
            ok = nums["brought_forward"] + nums["receipts"] - nums["disposals"] == nums["pending"]
            for j, code in enumerate(("brought_forward", "receipts", "disposals", "pending")):
                table.observations.append(
                    Observation(
                        row_index=j,
                        metric_code=code,
                        metric_label_original=code.replace("_", " ").title(),
                        value_original=assigned[code],
                        value_numeric=nums[code],
                        period_start=period[0],
                        period_end=period[1],
                        pdf_page=page,
                        validation_status="valid" if ok else "warning",
                    )
                )
            if not ok:
                table.issues.append(
                    Issue(
                        "info",
                        "flow_identity_gap",
                        "overview: BF + Receipts - Disposed differs from Pending",
                        page,
                    )
                )
            return table
    return None


def _overview_code(word: str) -> str:
    w = word.lower()
    return {
        "brought": "brought_forward",
        "receipts": "receipts",
        "disposed": "disposals",
        "disposal": "disposals",
        "pending": "pending",
    }[w]
