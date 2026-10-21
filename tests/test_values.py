from datetime import date
from decimal import Decimal

import pytest

from src.extract.values import (
    assign_years,
    is_number,
    parse_date,
    parse_month_label,
    parse_number,
    parse_period,
)


@pytest.mark.parametrize(
    ("token", "expected"),
    [
        ("17068", Decimal(17068)),
        ("1,17,061", Decimal(117061)),  # Indian digit grouping
        ("117,061", Decimal(117061)),
        ("0.799", Decimal("0.799")),
        ("62.60%", Decimal("62.60")),
        ("-", None),
        ("NA", None),
        ("####", None),  # spreadsheet overflow printed in the source: unreadable
    ],
)
def test_parse_number(token, expected):
    assert parse_number(token) == expected


@pytest.mark.parametrize("token", ["12a", "1,2", "Ministry", "", "1.2.3"])
def test_parse_number_rejects_malformed_tokens(token):
    with pytest.raises(ValueError):
        parse_number(token)


def test_is_number_accepts_placeholders_but_not_words():
    assert is_number("####") and is_number("-") and is_number("2,975")
    assert not is_number("Department") and not is_number("(Income")


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("25/05/2022", date(2022, 5, 25)),
        ("31st August 2025", date(2025, 8, 31)),
        ("30th September, 2026", date(2026, 9, 30)),
        ("1st January", None),  # no year and no default
    ],
)
def test_parse_date(text, expected):
    assert parse_date(text) == expected


@pytest.mark.parametrize(
    ("text", "cutoff", "expected"),
    [
        ("(Time Period: 01/01/2022 to 25/05/2022)", None, (date(2022, 1, 1), date(2022, 5, 25))),
        ("– 1st January 2025 to 31st August 2025", None, (date(2025, 1, 1), date(2025, 8, 31))),
        ("taken from 1st September to 30th September, 2026", None, (date(2026, 9, 1), date(2026, 9, 30))),
        (
            "Annexure 2: Average Closing Time – January to August, 2023",
            None,
            (date(2023, 1, 1), date(2023, 8, 31)),
        ),
        # month-only range in a 25th-cut-off report ends on the cut-off, not the month end
        ("January to February, 2023", date(2023, 2, 25), (date(2023, 1, 1), date(2023, 2, 25))),
        ("Ministries/Departments with more than 1000 Pending Grievances", None, None),
    ],
)
def test_parse_period(text, cutoff, expected):
    assert parse_period(text, cutoff=cutoff) == expected


def test_month_labels_and_years_cross_a_year_boundary():
    labels = [
        parse_month_label(t) for t in ["October", "November", "December", "January", "February", "March"]
    ]
    assert assign_years(labels, 2024, 3) == [
        date(2023, 10, 1),
        date(2023, 11, 1),
        date(2023, 12, 1),
        date(2024, 1, 1),
        date(2024, 2, 1),
        date(2024, 3, 1),
    ]


def test_month_labels_with_years():
    labels = [parse_month_label(t) for t in ["Aug-25", "Sep-25"]]
    assert assign_years(labels, 2025, 9) == [date(2025, 8, 1), date(2025, 9, 1)]


def test_month_labels_must_end_at_report_month():
    labels = [parse_month_label(t) for t in ["January", "February"]]
    assert assign_years(labels, 2025, 3) is None
