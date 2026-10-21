"""Regression tests on real reports: values read from the printed pages during Phase 2, re-extracted.

These need the source PDFs (git-ignored; fetch with `python -m src.discover.download`) and take ~2 min,
so they are opt-in: `python -m pytest -m pdf`. Each case cites report, physical PDF page and table, so the
value can be checked by eye in any PDF viewer.
"""

from __future__ import annotations

from decimal import Decimal as D

import pytest

from src.extract.report import extract_report, load_registry

pytestmark = pytest.mark.pdf

# (report month, table_type, grai_group, serial, metric_code, expected value, pdf_page, what to look for)
GOLDEN = [
    ("2022-05", "pending_over_1000", None, "1", "pending", D(17068), 11, "Ministry of Cooperation"),
    ("2022-08", "grai_ranking", "A", "1", "gri_percentage", D("62.60"), 10, "UIDAI, GRI 62.60%"),
    ("2023-08", "pending_over_1000", None, "1", "brought_forward", D(13036), 25, "CBDT (Income Tax)"),
    ("2023-08", "pending_over_1000", None, "1", "pending", D(11510), 25, "CBDT (Income Tax)"),
    (
        "2023-08",
        "avg_closing_time_high",
        None,
        "1",
        "avg_closing_time_days",
        D(111),
        26,
        "Department of Revenue",
    ),
    ("2023-10", "grai_ranking", "B", "27", "total_grievances", D(229), 15, "MSME row with '-' in the name"),
    ("2024-06", "grai_ranking", "A", "1", "total_grievances", D(1006), 18, "CBIC, Total printed first"),
    ("2024-06", "grai_ranking", "A", "1", "pending", D(99), 18, "CBIC"),
    ("2025-08", "pending_over_1000", None, "1", "pending", D(13959), 35, "Ministry of Labour and Employment"),
    (
        "2026-09",
        "pending_over_1000",
        None,
        "21",
        "pending",
        D(1020),
        56,
        "Rural Development, serial below figures",
    ),
    ("2026-09", "grai_ranking", "A", "1", "grai_score", D("0.799"), 25, "Department of Posts"),
    (
        "2026-09",
        "long_pending_top10",
        None,
        "1",
        "pending_over_threshold",
        None,
        56,
        "Petroleum: '####' printed",
    ),
]
MONTHWISE = [
    ("2025-08", "receipts", "2025-08-01", D(161265), 7),
    ("2026-09", "pending", "2026-09-01", D(85687), 32),
]

_cache: dict[str, object] = {}


def _extraction(month: str):
    if month not in _cache:
        record = next((r for r in load_registry() if f"{r.month:%Y-%m}" == month), None)
        if record is None or not record.path.exists():
            pytest.skip(f"PDF for {month} not available (run python -m src.discover.download)")
        _cache[month] = extract_report(record)
    return _cache[month]


@pytest.mark.parametrize(
    ("month", "table_type", "group", "serial", "metric", "expected", "page", "label"), GOLDEN
)
def test_golden_ministry_value(month, table_type, group, serial, metric, expected, page, label):
    result = _extraction(month)
    table = next(t for t in result.tables if t.table_type == table_type and t.grai_group == group)
    obs = [o for o in table.observations if o.serial_number_original == serial and o.metric_code == metric]
    assert len(obs) == 1, f"{label}: expected one observation"
    assert obs[0].value_numeric == expected, label
    assert obs[0].pdf_page == page, f"{label}: provenance page"


@pytest.mark.parametrize(("month", "metric", "period_start", "expected", "page"), MONTHWISE)
def test_golden_monthwise_value(month, metric, period_start, expected, page):
    table = next(t for t in _extraction(month).tables if t.table_type == "overall_monthwise")
    obs = [o for o in table.observations if o.metric_code == metric and str(o.period_start) == period_start]
    assert len(obs) == 1 and obs[0].value_numeric == expected and obs[0].pdf_page == page


def test_long_pending_thresholds_follow_the_published_definition_changes():
    expected = {"2022-05": 45, "2023-08": 30, "2025-08": 21}
    for month, days in expected.items():
        table = next(t for t in _extraction(month).tables if t.table_type == "long_pending_top10")
        assert table.threshold_value == D(days), month
