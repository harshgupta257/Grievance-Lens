"""Report registry (from data/manifest.csv) and per-report extraction."""

from __future__ import annotations

import collections
import csv
import re
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from src.config import PROJECT_ROOT
from src.extract import ministry_tables as mt
from src.extract import overall_tables as ot
from src.extract.layout import PdfDocument
from src.extract.model import ExtractedTable, Issue
from src.extract.values import month_end, parse_date

MANIFEST = PROJECT_ROOT / "data" / "manifest.csv"
PDF_DIR = PROJECT_ROOT / "data" / "raw" / "pdfs"


@dataclass(frozen=True)
class ReportRecord:
    report_number: int
    month: date
    language: str
    filename: str
    source_url: str
    sha256: str
    cms_media_id: int | None
    report_number_inferred: bool
    notes: str

    @property
    def path(self) -> Path:
        return PDF_DIR / self.filename

    @property
    def key(self) -> str:
        return f"{self.month:%Y-%m}_r{self.report_number:02d}_{self.language}"


def load_registry(manifest: Path = MANIFEST, language: str = "en") -> list[ReportRecord]:
    """Verified reports in the requested language, ordered by month (D-002: English is canonical)."""
    out = []
    with manifest.open(encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            if row["download_status"] != "downloaded_verified_pdf" or row["language"] != language:
                continue
            out.append(
                ReportRecord(
                    report_number=int(row["report_number"]),
                    month=date.fromisoformat(row["report_month"] + "-01"),
                    language=row["language"],
                    filename=row["filename"],
                    source_url=row["source_url"],
                    sha256=row["checksum"].removeprefix("sha256:"),
                    cms_media_id=int(row["cms_media_id"]) if row["cms_media_id"] else None,
                    report_number_inferred="inferred from continuous" in row["notes"],
                    notes=row["notes"],
                )
            )
    return sorted(out, key=lambda r: r.month)


_CUTOFF_RX = re.compile(
    r"(?:to|till|as\s+on)\s+(\d{1,2}(?:st|nd|rd|th)?[\s/\-.]*(?:\d{1,2}|[A-Za-z]{3,9})[,\s/\-.]*20\d\d)", re.I
)


def detect_cutoff(doc: PdfDocument, month: date) -> tuple[date, str]:
    """The data cut-off is the most frequent 'to/till/as on <date>' inside the report month.
    Falls back to the month end, flagged as inferred."""
    counts = collections.Counter()
    for m in _CUTOFF_RX.finditer(re.sub(r"\s+", " ", doc.full_text())):
        d = parse_date(m.group(1))
        if d and (d.year, d.month) == (month.year, month.month):
            counts[d] += 1
    if counts:
        return counts.most_common(1)[0][0], f"detected ({counts.most_common(1)[0][1]} mentions)"
    return month_end(month.year, month.month), "inferred_month_end"


@dataclass
class ReportExtraction:
    record: ReportRecord
    cutoff: date
    cutoff_method: str
    page_count: int
    tables: list[ExtractedTable] = field(default_factory=list)
    issues: list[Issue] = field(default_factory=list)  # report-level issues (e.g. a table not found)


EXPECTED = (
    "overall_monthwise",
    "pending_over_1000",
    "long_pending_top10",
    "avg_closing_time_high",
    "avg_closing_time_low",
    "grai_ranking:A",
    "grai_ranking:B",
)


def extract_report(record: ReportRecord) -> ReportExtraction:
    with PdfDocument(record.path) as doc:
        cutoff, method = detect_cutoff(doc, record.month)
        result = ReportExtraction(record, cutoff, method, doc.page_count)
        if method.startswith("inferred"):
            result.issues.append(
                Issue("warning", "cutoff_inferred", "no cut-off date found; assumed month end")
            )
        ctx = mt.ReportContext(month=record.month, cutoff=cutoff)
        parsers = [
            lambda: ot.parse_monthwise(doc, ctx),
            lambda: ot.parse_overview(doc, ctx),
            lambda: mt.parse_pending_over_1000(doc, ctx),
            lambda: mt.parse_long_pending(doc, ctx),
            lambda: mt.parse_closing_time(doc, ctx, "high"),
            lambda: mt.parse_closing_time(doc, ctx, "low"),
            lambda: mt.parse_grai(doc, ctx, "A"),
            lambda: mt.parse_grai(doc, ctx, "B"),
        ]
        for parse in parsers:
            try:
                table = parse()
            except Exception as exc:  # a parser crash must be visible, not abort the whole run
                result.issues.append(Issue("error", "parser_exception", f"{type(exc).__name__}: {exc}"))
                continue
            if table is not None:
                result.tables.append(table)
    found = {t.table_type + (f":{t.grai_group}" if t.grai_group else "") for t in result.tables}
    for expected in EXPECTED:
        if expected not in found and not _expected_absent(expected, record):
            result.issues.append(Issue("error", "table_not_found", f"{expected} not found in report"))
    return result


def _expected_absent(table: str, record: ReportRecord) -> bool:
    """Tables documented as absent in docs/source_inventory.md are not errors."""
    if table.startswith("grai_ranking") and record.report_number <= 3:
        return True  # GRI starts with report #4 (Aug 2022)
    if table == "overall_monthwise" and record.report_number in NO_CLEAN_MONTHWISE:
        return True
    return False


# Reports whose month-wise series is only a chart (or absent), per the Phase 2 survey. Their months are
# covered by clean tables in later reports of the same or the following year.
NO_CLEAN_MONTHWISE = {1, 2, 9, 10, 18, 19, 20, 21, 28, 29, 30, 31, 32}
