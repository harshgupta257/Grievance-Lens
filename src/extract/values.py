"""Parsing of numeric cells and of the date/period phrases used in CPGRAMS reports."""

from __future__ import annotations

import calendar
import re
from datetime import date
from decimal import Decimal, InvalidOperation

MONTHS = {m.lower(): i for i, m in enumerate(calendar.month_name) if m}
MONTHS.update({m.lower(): i for i, m in enumerate(calendar.month_abbr) if m})
MONTHS["sept"] = 9

# Indian grouping ("1,17,061"), Western grouping ("117,061"), plain integers, decimals and percentages.
_NUMBER = re.compile(r"^(\d{1,3}(?:,\d{2,3})+|\d+)(\.\d+)?%?$")
_NULL_CELLS = {"-", "–", "—", "NA", "N/A", "nil", "Nil"}
# Spreadsheet overflow printed in the source PDF itself (seen in Sep 2026, Annexure 1.4): the value
# exists but cannot be read. Kept as a cell so the row still parses; stored as NULL with an issue.
UNREADABLE_CELLS = {"####", "###", "#####"}


def is_number(token: str) -> bool:
    return bool(_NUMBER.match(token)) or token in _NULL_CELLS or token in UNREADABLE_CELLS


def parse_number(token: str) -> Decimal | None:
    """Parse a published cell value. Return None for explicit null or unreadable cells ("-", "NA", "####").

    Raises ValueError for anything else, so a malformed token can never pass as a number.
    """
    token = token.strip()
    if token in _NULL_CELLS or token in UNREADABLE_CELLS:
        return None
    if not _NUMBER.match(token):
        raise ValueError(f"not a numeric cell: {token!r}")
    try:
        return Decimal(token.rstrip("%").replace(",", ""))
    except InvalidOperation as exc:  # pragma: no cover - the regex already guarantees validity
        raise ValueError(f"not a numeric cell: {token!r}") from exc


def month_end(year: int, month: int) -> date:
    return date(year, month, calendar.monthrange(year, month)[1])


_DAY_MONTH_YEAR = re.compile(
    r"(\d{1,2})(?:st|nd|rd|th)?[\s\-/.]*"
    r"(\d{1,2}|[A-Za-z]{3,9})[,\s\-/.]*"
    r"(20\d\d)?",
)


def parse_date(text: str, default_year: int | None = None) -> date | None:
    """Parse '25/05/2022', '25-05-2022', '31st August 2025', '30th September, 2026', '1st January'."""
    m = _DAY_MONTH_YEAR.fullmatch(text.strip())
    if not m:
        return None
    day, mon, year = m.groups()
    month = int(mon) if mon.isdigit() else MONTHS.get(mon.lower())
    year_i = int(year) if year else default_year
    if not month or not year_i:
        return None
    try:
        return date(year_i, month, int(day))
    except ValueError:
        return None


_DATE = r"\d{1,2}(?:st|nd|rd|th)?[\s\-/.]*(?:\d{1,2}|[A-Za-z]{3,9})[,\s\-/.]*(?:20\d\d)?"
_RANGE = re.compile(rf"({_DATE})\s+(?:to|till)\s+({_DATE})", re.I)
_MONTH_RANGE = re.compile(r"\b(January|[A-Z][a-z]{2,8})\s+to\s+([A-Z][a-z]{2,8}),?\s+(20\d\d)", re.I)


def parse_period(text: str, cutoff: date | None = None) -> tuple[date, date] | None:
    """Find a reference period in heading/notes text.

    Handles '01/01/2022 to 25/05/2022', '1st January 2025 to 31st August 2025',
    '1st September to 30th September, 2026' (year taken from the end date) and 'January to August, 2023'.
    For month-only ranges, the end is the report cut-off date when it falls in that month (cut-offs
    were the 25th until March 2023), otherwise the month end.
    """
    flat = re.sub(r"\s+", " ", text)
    m = _RANGE.search(flat)
    if m:
        end = parse_date(m.group(2))
        if end:
            start = parse_date(m.group(1), default_year=end.year)
            if start and start <= end:
                return start, end
    m = _MONTH_RANGE.search(flat)
    if m:
        first, last, year = MONTHS.get(m.group(1).lower()), MONTHS.get(m.group(2).lower()), int(m.group(3))
        if first and last and first <= last:
            end = month_end(year, last)
            if cutoff and (cutoff.year, cutoff.month) == (year, last):
                end = cutoff
            return date(year, first, 1), end
    return None


_MONTH_LABEL = re.compile(r"^([A-Za-z]{3,9})(?:[-'’ ](\d{2}|\d{4}))?$")


def parse_month_label(token: str) -> tuple[int, int | None] | None:
    """'January' -> (1, None); 'Jan-25' -> (1, 2025); 'Sep-2026' -> (9, 2026)."""
    m = _MONTH_LABEL.match(token)
    if not m or m.group(1).lower() not in MONTHS:
        return None
    year = m.group(2)
    if year is not None:
        year = int(year) + (2000 if len(year) == 2 else 0)
    return MONTHS[m.group(1).lower()], year


def assign_years(labels: list[tuple[int, int | None]], last_year: int, last_month: int) -> list[date] | None:
    """Turn month labels into first-of-month dates. Labels without a year are dated backwards from the
    report month (so 'October … March' in a March 2024 report becomes Oct 2023 … Mar 2024).
    Returns None if the labels are not consecutive months ending at the report month."""
    out: list[date] = []
    y, mth = last_year, last_month
    for month, year in reversed(labels):
        if month != mth or (year is not None and year != y):
            return None
        out.append(date(y, mth, 1))
        y, mth = (y, mth - 1) if mth > 1 else (y - 1, 12)
    return list(reversed(out))
