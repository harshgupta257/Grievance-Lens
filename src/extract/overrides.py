"""Manual corrections for extraction errors, kept in data/overrides/overrides.csv.

An override replaces one parsed number after a person has checked the value in the PDF. The original
extracted text stays in `value_original`; the observation is marked `overridden`, and a resolved issue
records who verified it and why. An override that matches nothing is reported as an error, so stale
corrections cannot linger silently.

CSV columns:
    report_month (YYYY-MM), table_type, grai_group (A/B/empty), serial_number, metric_code,
    period_end (YYYY-MM-DD, empty = table period end), corrected_value, reason, verified_by, verified_on
"""

from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from pathlib import Path

from src.config import PROJECT_ROOT
from src.extract.model import Issue

OVERRIDES_CSV = PROJECT_ROOT / "data" / "overrides" / "overrides.csv"
COLUMNS = [
    "report_month",
    "table_type",
    "grai_group",
    "serial_number",
    "metric_code",
    "period_end",
    "corrected_value",
    "reason",
    "verified_by",
    "verified_on",
]


@dataclass(frozen=True)
class Override:
    report_month: str
    table_type: str
    grai_group: str | None
    serial_number: str
    metric_code: str
    period_end: date | None
    corrected_value: Decimal
    reason: str
    verified_by: str
    verified_on: str


def load_overrides(path: Path = OVERRIDES_CSV) -> list[Override]:
    if not path.exists():
        return []
    out = []
    with path.open(encoding="utf-8", newline="") as fh:
        reader = csv.DictReader(fh)
        if reader.fieldnames != COLUMNS:
            raise ValueError(f"{path}: expected columns {COLUMNS}, found {reader.fieldnames}")
        for n, row in enumerate(reader, start=2):
            if not row["reason"].strip() or not row["verified_by"].strip():
                raise ValueError(f"{path}:{n}: every override needs a reason and verified_by")
            out.append(
                Override(
                    report_month=row["report_month"],
                    table_type=row["table_type"],
                    grai_group=row["grai_group"] or None,
                    serial_number=row["serial_number"],
                    metric_code=row["metric_code"],
                    period_end=date.fromisoformat(row["period_end"]) if row["period_end"] else None,
                    corrected_value=Decimal(row["corrected_value"]),
                    reason=row["reason"],
                    verified_by=row["verified_by"],
                    verified_on=row["verified_on"],
                )
            )
    return out


def apply_overrides(extraction, overrides: list[Override]) -> int:
    """Apply the overrides for this report in place. Returns how many were applied."""
    month = f"{extraction.record.month:%Y-%m}"
    applied = 0
    for ov in (o for o in overrides if o.report_month == month):
        hits = [
            (t, obs)
            for t in extraction.tables
            if t.table_type == ov.table_type and t.grai_group == ov.grai_group
            for obs in t.observations
            if obs.serial_number_original == ov.serial_number
            and obs.metric_code == ov.metric_code
            and (ov.period_end is None or obs.period_end == ov.period_end)
        ]
        if len(hits) != 1:
            extraction.issues.append(
                Issue(
                    "error",
                    "override_unmatched",
                    f"override matched {len(hits)} observations (expected 1): {ov}",
                )
            )
            continue
        table, obs = hits[0]
        old = obs.value_numeric
        obs.value_numeric = ov.corrected_value
        obs.validation_status = "overridden"
        table.issues.append(
            Issue(
                "info",
                "manual_override",
                f"{ov.metric_code} serial {ov.serial_number}: {old} -> {ov.corrected_value}",
                obs.pdf_page,
                obs.value_original,
                status="resolved",
                resolution_note=f"{ov.reason} (verified by {ov.verified_by} on {ov.verified_on})",
            )
        )
        applied += 1
    return applied
