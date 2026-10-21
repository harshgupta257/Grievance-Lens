"""Parsers for ministry-level tables: >1000 pending (T2), long-pending Top-10 (T3), average closing time
(T4) and GRI/GRAI rankings (T5, which in the Aug 2024 - Jan 2026 layout also carries the T6 monthly flows).

Each parser locates its heading, checks the header matches a known column layout, assembles rows from
word coordinates, and validates each row with the identities the layout allows. Rows that cannot be
parsed produce an `error` issue with the raw text; they are never dropped silently.
"""

from __future__ import annotations

import collections
import re
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from src.extract.layout import Heading, PdfDocument
from src.extract.model import ExtractedTable, Issue, Observation
from src.extract.rows import RawRow, collect_rows
from src.extract.values import UNREADABLE_CELLS, parse_number, parse_period

PARSER_VERSION = "2.0.0"
# Published rows sometimes miss Total - Disposal = Pending by a few grievances (44 rows in 2022-2026, all
# within +/-3; BF + Receipts = Total always held). Such rows are faithful transcriptions of a small source
# inconsistency: kept as "warning". Larger gaps suggest a parsing error and are marked "failed" (D-016).
IDENTITY_TOLERANCE = Decimal(3)


@dataclass(frozen=True)
class Layout:
    name: str
    columns: tuple[tuple[str, str], ...]  # (metric_code, original column label), left to right
    required_header: tuple[str, ...]  # words that must appear in the header; "a|b" accepts either

    def matches(self, header: set[str]) -> bool:
        return all(any(alt in header for alt in req.split("|")) for req in self.required_header)


FLOWS = (
    ("brought_forward", "Brought Forward"),
    ("receipts", "Receipts"),
    ("total_grievances", "Total Grievances"),
    ("disposals", "Disposal"),
    ("pending", "Pending"),
)
PENDING_1000 = Layout(
    "bf_r_total_d_p", FLOWS, ("brought", "receipts|received", "total", "disposal", "pending")
)
# Jun 2022 long-pending table has no Total column (BF, Receipts, Disposal, Pending, > N Days)
PENDING_NO_TOTAL = Layout(
    "bf_r_d_p",
    tuple(c for c in FLOWS if c[0] != "total_grievances"),
    ("brought", "receipts|received", "disposal", "pending"),
)
CLOSING_TIME = Layout(
    "cases_avg_days",
    (("cases_disposed", "Cases Disposed"), ("avg_closing_time_days", "Average Closing Time (in days)")),
    ("closing",),
)
GRAI_LAYOUTS = (
    # checked in order; the first whose required header words are all present wins
    Layout(
        "GRAI_4D_total_flows",
        (
            ("total_grievances", "Total Grievances"),
            ("brought_forward", "Brought Forward"),
            ("receipts", "Receipts"),
            ("disposals", "Disposal"),
            ("pending", "Pending"),
            ("grai_score", "GRAI Score"),
            ("grai_rank", "GRAI Rank"),
        ),
        ("total", "brought", "receipts", "score", "rank"),
    ),  # Jun 2024
    Layout(
        "GRAI_4D_flows",
        (
            ("brought_forward", "Brought Forward"),
            ("receipts", "Receipts"),
            ("disposals", "Disposal"),
            ("pending", "Pending"),
            ("grai_score", "GRAI Score"),
            ("grai_rank", "GRAI Rank"),
        ),
        ("brought", "receipts", "score", "rank"),
    ),
    Layout(
        "GRAI_4D_receipts_disposal",
        (
            ("receipts", "Receipts"),
            ("disposals", "Disposal"),
            ("grai_efficiency", "Efficiency"),
            ("grai_feedback", "Feedback"),
            ("grai_domain", "Domain"),
            ("grai_org_commitment", "Org. Commitment"),
            ("grai_score", "GRAI Score"),
            ("grai_rank", "GRAI Rank"),
        ),
        ("receipts", "disposal", "efficiency", "score", "rank"),
    ),  # Jul 2023
    Layout(
        "GRAI_4D_total",
        (
            ("total_grievances", "Total Grievances"),
            ("grai_efficiency", "Efficiency"),
            ("grai_feedback", "Feedback"),
            ("grai_domain", "Domain"),
            ("grai_org_commitment", "Org. Commitment"),
            ("grai_score", "GRAI Score"),
            ("grai_rank", "GRAI Rank"),
        ),
        ("total", "efficiency", "score", "rank"),
    ),
    Layout(
        "GRAI_4D_dims",
        (
            ("grai_efficiency", "Efficiency"),
            ("grai_feedback", "Feedback"),
            ("grai_domain", "Domain"),
            ("grai_org_commitment", "Org. Commitment"),
            ("grai_score", "GRAI Score"),
            ("grai_rank", "GRAI Rank"),
        ),
        ("efficiency", "score", "rank"),
    ),
    Layout(
        "GRI_percentage",
        (
            ("total_grievances", "Total Grievances"),
            ("gri_percentage", "Score Percentage"),
            ("grai_rank", "Rank"),
        ),
        ("percentage", "rank"),
    ),
)

P1000_RX = r"more\s+than\s+1000\s+Pend(?:ing|ency)"
LONG_RX = r"Pending\s+Grievances\s+for\s+more\s+than\s+(\d+)\s*Days"
HIGH_RX = r"High\s+Average\s+Closing\s+Time(?!\s+of\s+Appeals)"
LOW_RX = r"Low\s+Average\s+Closing\s+Time(?!\s+of\s+Appeals)"
GRAI_RX = r"Ranking\s+of\s+Ministries/Departments\s*[–-]\s*Group\s+{group}\b"
GROUP_THRESHOLD_RX = re.compile(
    r"Total\s+Grievances\s+(?:more|less)\s+than\s+(?:equal\s+to\s+)?(\d[\d,]*)", re.I
)
# captures up to the end of the sentence, e.g. "1st March, 2022 to 25th August, 2022"
GRAI_PERIOD_RX = re.compile(
    r"data\s+used\s+in\s+preparing\s+the\s+[\w\s()]{2,40}?\s+(?:has\s+been\s+)?taken\s+from\s+([^.]{8,90})",
    re.I,
)


@dataclass
class ReportContext:
    """What parsers need to know about the report being parsed."""

    month: date  # first day of the report month
    cutoff: date  # data cut-off date (25th until Mar 2023, then month-end; 28 Mar 2025)


def _header_words(text: str) -> set[str]:
    return {w.strip("().,:*").lower() for w in text.split()}  # "*Receipts" carries a footnote mark (2025)


def _select_table_heading(doc: PdfDocument, pattern: str) -> Heading | None:
    """The table heading is the match followed by rows; narrative mentions of the same phrase are not."""
    for heading in reversed(doc.find_headings(pattern)):
        rows = collect_rows(doc, heading.page, heading.bottom, max_pages=2)  # rows may start on the next page
        if rows.rows:
            return heading
    return None


def _rows_to_observations(
    table: ExtractedTable, rows: list[RawRow], layout: Layout, *, extra: tuple[str, str] | None = None
) -> None:
    columns = layout.columns + ((extra,) if extra else ())
    previous_serial = 0
    for index, row in enumerate(rows):
        if int(row.serial) != previous_serial + 1:
            table.issues.append(
                Issue(
                    "warning",
                    "serial_gap",
                    f"serial {row.serial} follows {previous_serial}",
                    row.page,
                    row.anchor_text,
                )
            )
        previous_serial = int(row.serial)
        name = row.name
        if not name:
            table.issues.append(
                Issue("error", "missing_name", "row has no ministry name", row.page, row.anchor_text)
            )
            continue
        if len(row.values) != len(columns):
            table.issues.append(
                Issue(
                    "error",
                    "column_count_mismatch",
                    f"expected {len(columns)} values ({', '.join(c for c, _ in columns)}), "
                    f"found {len(row.values)}",
                    row.page,
                    f"{name} | {row.anchor_text}",
                )
            )
            continue
        try:
            numbers = [parse_number(v) for v in row.values]
        except ValueError as exc:
            table.issues.append(Issue("error", "unparseable_value", str(exc), row.page, row.anchor_text))
            continue
        status, gap = _validate_row(dict(zip([c for c, _ in columns], numbers, strict=True)))
        if status == "failed":
            table.issues.append(
                Issue(
                    "warning",
                    "identity_failed",
                    f"values miss BF + Receipts = Total / Total - Disposal = Pending by {gap}; "
                    "possible extraction error",
                    row.page,
                    f"{name} | {row.anchor_text}",
                )
            )
        elif gap:
            table.issues.append(
                Issue(
                    "info",
                    "source_identity_gap",
                    f"published Pending differs from Total - Disposal by {gap} (within tolerance)",
                    row.page,
                    f"{name} | {row.anchor_text}",
                )
            )
        for raw in row.values:
            if raw in UNREADABLE_CELLS:
                table.issues.append(
                    Issue(
                        "warning",
                        "unreadable_cell",
                        f"the PDF prints {raw!r} instead of a value; stored as NULL",
                        row.page,
                        f"{name} | {row.anchor_text}",
                    )
                )
        for (code, label), raw, value in zip(columns, row.values, numbers, strict=True):
            table.observations.append(
                Observation(
                    row_index=index,
                    metric_code=code,
                    metric_label_original=label,
                    value_original=raw,
                    value_numeric=value,
                    period_start=table.period_start,
                    period_end=table.period_end,
                    pdf_page=row.page,
                    entity_name_original=name,
                    serial_number_original=row.serial,
                    validation_status=status,
                )
            )


def _validate_row(v: dict[str, Decimal | None]) -> tuple[str, Decimal]:
    """Return (status, largest flow-identity gap). Status is 'valid' if every identity the layout supports
    holds exactly, 'warning' if a flow identity is off by at most IDENTITY_TOLERANCE, 'failed' if any check
    fails beyond that, and 'unvalidated' if the layout allows no check."""
    checks: list[bool] = []
    gaps: list[Decimal] = []
    bf, r, tot, d, p = (
        v.get(k) for k in ("brought_forward", "receipts", "total_grievances", "disposals", "pending")
    )
    if None not in (bf, r, tot):
        gaps.append(bf + r - tot)
    if None not in (tot, d, p):
        gaps.append(tot - d - p)
    if tot is None and None not in (bf, r, d, p):
        gaps.append(bf + r - d - p)
    if None not in (p, v.get("pending_over_threshold")) and "pending_over_threshold" in v:
        checks.append(v["pending_over_threshold"] <= p)
    for code in ("grai_score", "grai_efficiency", "grai_feedback", "grai_domain", "grai_org_commitment"):
        if v.get(code) is not None:
            checks.append(Decimal(0) <= v[code] <= Decimal(1))
    if v.get("gri_percentage") is not None:
        checks.append(Decimal(0) <= v["gri_percentage"] <= Decimal(100))
    worst = max(gaps, key=abs, default=Decimal(0))
    checks.extend(abs(g) <= IDENTITY_TOLERANCE for g in gaps)
    if not checks:
        return "unvalidated", worst
    if not all(checks):
        return "failed", worst
    return ("warning" if worst else "valid"), worst


def _new_table(
    table_type: str, heading: Heading, rows_last_page: int, period: tuple[date, date], parser_name: str, **kw
) -> ExtractedTable:
    return ExtractedTable(
        table_type=table_type,
        original_title=heading.text[:300],
        pdf_page_start=heading.page,
        pdf_page_end=rows_last_page,
        period_start=period[0],
        period_end=period[1],
        parser_name=parser_name,
        parser_version=PARSER_VERSION,
        **kw,
    )


def _period_or_default(text: str, ctx: ReportContext, issues: list[Issue], page: int) -> tuple[date, date]:
    period = parse_period(text, cutoff=ctx.cutoff)
    if period:
        return period
    issues.append(
        Issue(
            "warning",
            "period_inferred",
            "no period found in heading; assumed 1 January to the report cut-off (year-to-date)",
            page,
            text[:200],
        )
    )
    return date(ctx.month.year, 1, 1), ctx.cutoff


def _ministry_table(
    doc, ctx, table_type, pattern, layout, *, threshold=None, extra=None, extra_context: str = ""
) -> ExtractedTable | None:
    heading = _select_table_heading(doc, pattern)
    if heading is None:
        return None
    collected = collect_rows(doc, heading.page, heading.bottom)
    issues: list[Issue] = []
    # the period sits in the heading or in a "(Time Period: …)" line inside the header block
    period = _period_or_default(
        " ".join((heading.text, collected.header_text, heading.context, extra_context)),
        ctx,
        issues,
        heading.page,
    )
    table = _new_table(
        table_type,
        heading,
        collected.last_page,
        period,
        "ministry_table",
        threshold_value=threshold,
        threshold_unit="grievances"
        if table_type == "pending_over_1000"
        else ("days" if threshold is not None else None),
    )
    table.issues.extend(issues)
    header = _header_words(collected.column_header_text)
    if layout is PENDING_1000:
        # "Total" is sometimes a qualifier ("Total Received / Total Disposal", Jun 2022) rather than a column:
        # choose the flow layout whose column count matches the rows; identities then validate the choice.
        counts = collections.Counter(len(r.values) for r in collected.rows)
        modal = counts.most_common(1)[0][0] if counts else 0
        extra_n = 1 if extra else 0
        for candidate in (PENDING_1000, PENDING_NO_TOTAL):
            if len(candidate.columns) + extra_n == modal and candidate.matches(header):
                layout = candidate
                break
    if not layout.matches(header):
        table.issues.append(
            Issue(
                "error",
                "unexpected_header",
                f"header lacks one of {layout.required_header}; table skipped",
                heading.page,
                collected.header_text[:300],
            )
        )
        return table
    for page, problem, raw in collected.problems:
        table.issues.append(Issue("warning", problem, "line could not be assigned to a row", page, raw))
    _rows_to_observations(table, collected.rows, layout, extra=extra)
    return table


def parse_pending_over_1000(doc: PdfDocument, ctx: ReportContext) -> ExtractedTable | None:
    return _ministry_table(doc, ctx, "pending_over_1000", P1000_RX, PENDING_1000, threshold=Decimal(1000))


def parse_long_pending(doc: PdfDocument, ctx: ReportContext) -> ExtractedTable | None:
    heading = _select_table_heading(doc, LONG_RX)
    if heading is None:
        return None
    days = int(re.search(LONG_RX, heading.text, re.I).group(1))
    return _ministry_table(
        doc,
        ctx,
        "long_pending_top10",
        LONG_RX,
        PENDING_1000,
        threshold=Decimal(days),
        extra=("pending_over_threshold", f"> {days} Days"),
    )


def parse_closing_time(doc: PdfDocument, ctx: ReportContext, which: str) -> ExtractedTable | None:
    pattern, table_type = (
        (HIGH_RX, "avg_closing_time_high") if which == "high" else (LOW_RX, "avg_closing_time_low")
    )
    # both tables sit under a parent heading that carries the period, e.g.
    # "Annexure 2: Average Closing Time – 1st January 2025 to 31st August 2025"
    parents = doc.find_headings(r"^\s*Annexure\s*\d+\s*:?\s*Average\s+Closing\s+Time\s*[–-]")
    return _ministry_table(
        doc, ctx, table_type, pattern, CLOSING_TIME, extra_context=parents[-1].text if parents else ""
    )


def parse_grai(doc: PdfDocument, ctx: ReportContext, group: str) -> ExtractedTable | None:
    heading = _select_table_heading(doc, GRAI_RX.format(group=group))
    if heading is None:
        return None
    collected = collect_rows(doc, heading.page, heading.bottom)
    header = _header_words(collected.column_header_text)
    layout = next((lay for lay in GRAI_LAYOUTS if lay.matches(header)), None)
    issues: list[Issue] = []
    period = _grai_period(doc, heading, ctx, issues)
    threshold = GROUP_THRESHOLD_RX.search(heading.text + " " + collected.header_text)
    table = _new_table(
        "grai_ranking",
        heading,
        collected.last_page,
        period,
        "grai_ranking",
        grai_group=group,
        grai_methodology=layout.name if layout else "unknown",
        threshold_value=parse_number(threshold.group(1)) if threshold else None,
        threshold_unit="grievances" if threshold else None,
    )
    table.issues.extend(issues)
    if layout is None:
        table.issues.append(
            Issue(
                "error",
                "unexpected_header",
                "no known GRAI layout matches; table skipped",
                heading.page,
                collected.header_text[:300],
            )
        )
        return table
    for page, problem, raw in collected.problems:
        table.issues.append(Issue("warning", problem, "line could not be assigned to a row", page, raw))
    _rows_to_observations(table, collected.rows, layout)
    _check_rank_sequence(table)
    return table


def _grai_period(
    doc: PdfDocument, heading: Heading, ctx: ReportContext, issues: list[Issue]
) -> tuple[date, date]:
    """GRAI periods are stated in prose near the method description, a few pages before the ranking."""
    for page in range(heading.page, max(heading.page - 8, 3), -1):
        m = GRAI_PERIOD_RX.search(re.sub(r"\s+", " ", doc.page_text(page)))
        if m:
            period = parse_period(m.group(1), cutoff=ctx.cutoff)
            if period:
                if period[0] >= date(2023, 5, 1) and period != (ctx.month, ctx.cutoff):
                    issues.append(
                        Issue(
                            "info",
                            "grai_window",
                            f"stated GRAI data period {period[0]}..{period[1]} "
                            "differs from the report month; stored as stated",
                            page,
                            m.group(0)[:200],
                        )
                    )
                return period
    issues.append(
        Issue(
            "warning",
            "period_inferred",
            "GRAI data period not stated; assumed the report month (1st to cut-off)",
            heading.page,
        )
    )
    return ctx.month, ctx.cutoff


def _check_rank_sequence(table: ExtractedTable) -> None:
    ranks = [o.value_numeric for o in table.observations if o.metric_code == "grai_rank"]
    if ranks and sorted(ranks) != [Decimal(i) for i in range(1, len(ranks) + 1)]:
        table.issues.append(
            Issue(
                "info",
                "rank_sequence",
                f"ranks are not exactly 1..{len(ranks)} (ties or skipped ranks are possible)",
                table.pdf_page_start,
            )
        )
