"""Plain data containers produced by the parsers and consumed by the staging writer and DB loader.

Field names mirror the `source_tables`, `observations` and `extraction_issues` columns (sql/migrations).
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import date
from decimal import Decimal


@dataclass
class Issue:
    severity: str  # info | warning | error
    issue_type: str
    description: str
    pdf_page: int | None = None
    raw_text: str | None = None
    status: str = "open"
    resolution_note: str | None = None


@dataclass
class Observation:
    row_index: int
    metric_code: str
    metric_label_original: str
    value_original: str
    value_numeric: Decimal | None
    period_start: date
    period_end: date
    pdf_page: int
    entity_name_original: str | None = None  # None = aggregate (Central Secretariat) value
    serial_number_original: str | None = None
    validation_status: str = "unvalidated"

    @property
    def is_aggregate(self) -> bool:
        return self.entity_name_original is None


@dataclass
class ExtractedTable:
    table_type: str
    original_title: str
    pdf_page_start: int
    pdf_page_end: int
    period_start: date
    period_end: date
    parser_name: str
    parser_version: str
    threshold_value: Decimal | None = None
    threshold_unit: str | None = None
    grai_group: str | None = None
    grai_methodology: str | None = None
    printed_page_label: str | None = None
    extraction_method: str = "pdfplumber_text"
    observations: list[Observation] = field(default_factory=list)
    issues: list[Issue] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)
