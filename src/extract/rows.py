"""Row assembly for ministry-level tables, from word coordinates.

Every data row has one *anchor line* that starts with the serial number and carries the numeric cells.
Ministry names wrap onto lines above and/or below the anchor (cells are vertically centred). The name
lines between two anchors are split at the largest vertical gap, because the padding between table rows
is wider than the line spacing inside a cell (2022: ~12 vs ~15.5 pt; narrow 2026 GRAI tables: 16.2 vs
16.7 pt). Lines below a page's last anchor that follow a row-boundary-sized gap belong to the first row of
the next page. A table continues onto the next page only if that page's first anchor has the next serial.
"""

from __future__ import annotations

import re
import statistics
from dataclasses import dataclass, field

from src.extract.layout import HEADING_PREFIX, LINE_TOLERANCE, Line, PdfDocument, is_footer
from src.extract.values import is_number

_SERIAL = re.compile(r"^\d{1,3}$")
# Words that occur in table headers and never in data cells.
HEADER_TOKENS = {
    "brought",
    "forward",
    "receipts",
    "received",
    "total",
    "grievances",
    "disposal",
    "pending",
    "cases",
    "disposed",
    "average",
    "avg.",
    "closing",
    "time",
    "(in",
    "days)",
    "days",
    "score",
    "percentage",
    "rank",
    "grai",
    "efficiency",
    "feedback",
    "domain",
    "org.",
    "commitment",
    "dimension-wise",
    ">",
}
MAX_NAME_DISTANCE = 40.0  # points; safety limit for lines above the first / below the last anchor
SPLIT_ANCHOR_GAP = 7.5  # points; serial (alone, or with the first name line) offset from its figures
RANK_LINE_GAP = (
    18.0  # points; May 2024 Group B: figures on the first name line, "serial … rank" 1-2 lines below
)
GAP_TIE = 0.3  # points; gaps closer than this are treated as equal (fall back to the nearest anchor)
TIME_PERIOD = re.compile(r"^\(?\s*Time\s+Period", re.I)


@dataclass
class RawRow:
    serial: str
    page: int
    top: float  # highest word on the anchor line(s)
    bottom_top: float  # top of the lowest word: a merged anchor can span two printed lines
    name_parts: list[tuple[int, float, float, str]] = field(default_factory=list)  # (page, top, x0, text)
    values: list[str] = field(default_factory=list)
    value_x: list[float] = field(default_factory=list)  # x0 of each value token
    anchor_text: str = ""

    @property
    def name(self) -> str:
        return " ".join(t for *_, t in sorted(self.name_parts) if t).strip()

    def add_name_line(self, line: Line) -> None:
        text = " ".join(w.text for w in line.words if not is_number(w.text))
        self.name_parts.append((line.page, line.top, line.words[0].x0, text))


@dataclass
class TableRows:
    rows: list[RawRow]
    header_lines: list[str]
    last_page: int
    problems: list[tuple[int, str, str]]  # (page, problem, raw text)

    @property
    def header_text(self) -> str:
        """All context above the rows: title continuation, "(Time Period …)" notes and column headers."""
        return " ".join(self.header_lines)

    @property
    def column_header_text(self) -> str:
        """Column header words only: parenthetical notes such as
        "(… Total Grievances more than 500)" are excluded."""
        return " ".join(t for t in self.header_lines if not t.startswith("("))


def _is_header_line(line: Line) -> bool:
    tokens = [w.text.lower() for w in line.words]
    return sum(t in HEADER_TOKENS for t in tokens) >= max(1, len(tokens) // 3)


def _split_anchor(line: Line):
    """If `line` is an anchor, return (serial, name words, value words, x of the first digit value)."""
    words = line.words
    if len(words) < 2 or not _SERIAL.match(words[0].text):
        return None
    i = len(words)
    while i > 1 and is_number(words[i - 1].text):
        i -= 1
    values = words[i:]
    digits = [w.x0 for w in values if w.text[0].isdigit()]
    if not values or not digits:
        return None
    return words[0].text, words[1:i], values, digits[0]


def _split_numeric_anchor(line: Line, min_values: int):
    """Anchor for tables without serial numbers (2026 "Data Used for Score Calculation"): a line ending in
    at least `min_values` numbers. The serial is assigned later, in reading order."""
    words = line.words
    i = len(words)
    while i > 0 and is_number(words[i - 1].text):
        i -= 1
    values = words[i:]
    if len(values) < min_values:
        return None
    return "", words[:i], values, values[0].x0


def merge_split_anchors(lines: list[Line]) -> list[Line]:
    """Join a line carrying a row's figures with the line holding its serial number when the two are printed
    at slightly different heights: a lone serial (Sep 2026), the serial plus the first name line (Aug 2024),
    or the serial plus the rank, below a name line with the other figures (May 2024).

    The merged line keeps reading order: serial, name words line by line, then all figures left to right.
    """
    out = list(lines)
    i = 0
    while i < len(out):
        ln = out[i]
        numeric = [w for w in ln.words if is_number(w.text)]
        # a proper anchor (serial followed by a name word) needs no merging; a numbers-only line may start
        # with a value that merely looks like a serial ("609 2097 2460 ...")
        proper_anchor = (
            _SERIAL.match(ln.words[0].text) is not None
            and len(ln.words) > 1
            and not is_number(ln.words[1].text)
        )
        if len(numeric) >= 2 and not proper_anchor:
            for j in (i + 1, i - 1, i + 2, i - 2):
                if not 0 <= j < len(out):
                    continue
                cand = out[j]
                gap_limit = (
                    RANK_LINE_GAP
                    if len(cand.words) > 1 and is_number(cand.words[-1].text)
                    else SPLIT_ANCHOR_GAP
                )
                if (
                    _SERIAL.match(cand.words[0].text)
                    and sum(is_number(w.text) for w in cand.words[1:]) <= 1
                    and numeric[0].x0 > cand.words[0].x1 + 40
                    and abs(cand.top - ln.top) <= gap_limit
                ):
                    upper, lower = (cand, ln) if cand.top < ln.top else (ln, cand)
                    serial = cand.words[0]
                    names = [
                        w
                        for src in (upper, lower)
                        for w in src.words
                        if w is not serial and not is_number(w.text)
                    ]
                    figures = sorted(
                        (w for w in ln.words + cand.words[1:] if is_number(w.text)), key=lambda w: w.x0
                    )
                    lo, hi = min(i, j), max(i, j)
                    out[lo] = Line([serial, *names, *figures])
                    del out[hi]
                    break
        i += 1
    return out


def _gap_tops(above: RawRow, lines: list[Line], below: RawRow) -> list[float]:
    return [above.bottom_top, *(ln.top for ln in lines), below.top]


def _clear_split(above: RawRow, lines: list[Line], below: RawRow) -> int | None:
    """Number of `lines` belonging to the row above, if one gap is clearly the largest; otherwise None."""
    tops = _gap_tops(above, lines, below)
    gaps = [b - a for a, b in zip(tops, tops[1:], strict=False)]
    ranked = sorted(range(len(gaps)), key=lambda k: gaps[k], reverse=True)
    if len(ranked) == 1 or gaps[ranked[0]] - gaps[ranked[1]] >= GAP_TIE:
        return ranked[0]
    return None


def _tie_split(above: RawRow, lines: list[Line], below: RawRow, top_aligned: bool) -> int:
    """Uniform spacing gives no boundary. Top-aligned cells (2022) continue downward, so the lines belong to
    the row above; vertically centred cells: nearest anchor per line."""
    if top_aligned:
        return len(lines)
    return sum(1 for ln in lines if ln.top - above.bottom_top <= below.top - ln.top)


def collect_rows(
    doc: PdfDocument, page: int, start_y: float, *, max_pages: int = 6, min_values: int | None = None
) -> TableRows:
    """Collect table rows that start below `start_y` on `page`, following continuations onto later pages.

    With `min_values`, rows are anchored on lines ending in at least that many numbers instead of on a
    serial number (tables printed "in no particular order"); serials are then assigned 1, 2, … in order.
    """

    def split(line: Line):
        return _split_numeric_anchor(line, min_values) if min_values else _split_anchor(line)

    rows: list[RawRow] = []
    problems: list[tuple[int, str, str]] = []
    header_lines: list[str] = []
    value_x: float | None = None  # left edge of the numeric zone, learned from the anchors
    carry: list[Line] = []  # name lines at the foot of a page that belong to the next page's first row
    split_gaps: list[float] = []  # row-boundary gaps chosen in this table (to place page-foot lines)
    for current in range(page, min(doc.page_count, page + max_pages - 1) + 1):
        lines = [ln for ln in doc.lines(current) if current != page or ln.top > start_y]
        if not min_values:
            lines = merge_split_anchors(lines)
        # Lines above the first anchor: everything down to the last header line (title continuation,
        # "(Time Period …)", the column header, repeated on continuation pages) is header context; only
        # lines after it can be wrapped names of the first row.
        pre: list[Line] = []
        rest: list[Line] = []
        new_section = False
        for ln in lines:
            if not rest and not split(ln):
                if current != page and HEADING_PREFIX.match(ln.text) and not _is_header_line(ln):
                    new_section = True  # a new section starts: the table did not continue
                    break
                pre.append(ln)
            else:
                rest.append(ln)
        if new_section:
            break
        if min_values and rest:
            # fragmented headers ("Broug / ht / Forwa / rd"): anything reaching into the numeric zone
            zone = split(rest[0])[3] - 5
            last_header = max(
                (i for i, ln in enumerate(pre) if any(w.x1 > zone for w in ln.words)), default=-1
            )
        else:
            last_header = max(
                (i for i, ln in enumerate(pre) if _is_header_line(ln) or ln.text.startswith("(")), default=-1
            )
        header_lines.extend(ln.text for ln in pre[: last_header + 1])

        # sequence of anchors and name lines on this page
        anchors: list[RawRow] = []
        before_first: list[Line] = []
        between: list[list[Line]] = []  # between[k] = name lines after anchors[k]
        ended = False
        for ln in pre[last_header + 1 :] + rest:
            if is_footer(ln):
                break
            if TIME_PERIOD.match(ln.text):
                header_lines.append(
                    ln.text
                )  # e.g. "(Time Period: 01/01/2022 to 25/09/2022)" below the header
                continue
            anchor = split(ln)
            if anchor:
                serial, name_words, values, vx = anchor
                if min_values:
                    serial = str(len(rows) + len(anchors) + 1)
                elif current != page and rows and not anchors and int(serial) != int(rows[-1].serial) + 1:
                    ended = True  # the first anchor of a continuation page must carry the next serial
                    break
                value_x = vx if value_x is None else min(value_x, vx)
                lowest = max(w.top for w in ln.words)  # a merged anchor spans two printed lines
                row = RawRow(
                    serial,
                    current,
                    ln.top,
                    lowest if lowest - ln.top > LINE_TOLERANCE else ln.top,
                    values=[w.text for w in values],
                    value_x=[w.x0 for w in values],
                    anchor_text=ln.text,
                )
                if name_words:
                    row.name_parts.append(
                        (current, ln.top, name_words[0].x0, " ".join(w.text for w in name_words))
                    )
                anchors.append(row)
                between.append([])
                continue
            if HEADING_PREFIX.match(ln.text) or ln.text.lower().startswith(("note", "source")):
                ended = True
                break
            if value_x is not None and any(w.x1 > value_x + 2 and not is_number(w.text) for w in ln.words):
                ended = True  # running text crossing into the numeric zone: the table is over
                break
            if any(is_number(w.text) for w in ln.words):
                problems.append((current, "stray_number", ln.text))
            (between[-1] if anchors else before_first).append(ln)

        if not anchors:
            if rows or current != page:
                break  # the table did not continue onto this page
            continue  # heading at the foot of the page: rows may start on the next page

        # On a continuation page, lines above the first anchor may finish the previous page's last row (a row
        # split by the page break): they end at a row-boundary-sized gap, after which the first row's
        # head starts.
        if rows and before_first and split_gaps:
            limit = statistics.median(split_gaps) - GAP_TIE
            tops = [ln.top for ln in before_first] + [anchors[0].top]
            gaps = [b - a for a, b in zip(tops, tops[1:], strict=False)]
            cut = max(range(len(gaps)), key=lambda g: gaps[g])  # gaps[cut] follows before_first[cut]
            if gaps[cut] >= limit:
                for ln in before_first[: cut + 1]:
                    rows[-1].add_name_line(ln)
                before_first = before_first[cut + 1 :]
        # lines above the first anchor (and lines carried from the previous page) head the first row
        for ln in carry + before_first:
            if abs(anchors[0].top - ln.top) > MAX_NAME_DISTANCE and ln not in carry:
                problems.append((current, "unassigned_line", ln.text))
            else:
                anchors[0].add_name_line(ln)
        carry = []
        # name lines between consecutive anchors: split at the row boundary. Whether cells are top-aligned
        # (names only continue downward) is decided from the unambiguous splits on this page.
        clear = {
            k: _clear_split(anchors[k], between[k], anchors[k + 1])
            for k in range(len(anchors) - 1)
            if between[k]
        }
        decided = [(k, n) for k, n in clear.items() if n is not None]
        if decided:
            top_aligned = all(n == len(between[k]) for k, n in decided)
        else:
            top_aligned = not before_first and all(r.name_parts for r in anchors)
        for k in range(len(anchors) - 1):
            gap_lines = between[k]
            if not gap_lines:
                continue
            n_above = (
                clear[k]
                if clear[k] is not None
                else _tie_split(anchors[k], gap_lines, anchors[k + 1], top_aligned)
            )
            for ln in gap_lines[:n_above]:
                anchors[k].add_name_line(ln)
            for ln in gap_lines[n_above:]:
                anchors[k + 1].add_name_line(ln)
            tops = _gap_tops(anchors[k], gap_lines, anchors[k + 1])
            split_gaps.append(tops[n_above + 1] - tops[n_above])
        # lines below the last anchor: tail of the last row until a row-boundary-sized gap
        tail = between[-1]
        prev_top = anchors[-1].bottom_top
        limit = (statistics.median(split_gaps) - GAP_TIE) if split_gaps else MAX_NAME_DISTANCE / 2
        for idx, ln in enumerate(tail):
            gap = ln.top - prev_top
            if gap >= limit or abs(ln.top - anchors[-1].top) > MAX_NAME_DISTANCE:
                carry = tail[idx:]
                break
            anchors[-1].add_name_line(ln)
            prev_top = ln.top
        rows.extend(anchors)
        if ended:
            break
    for ln in carry:
        problems.append((ln.page, "unassigned_line", ln.text))
    _reclaim_name_placeholders(rows)
    last_page = rows[-1].page if rows else page
    return TableRows(rows, header_lines, last_page, problems)


def _reclaim_name_placeholders(rows: list[RawRow]) -> None:
    """A '-' inside a name ("Ministry of Micro - Small and Medium Enterprises", Oct 2023) looks like a null
    cell. Placeholder tokens well left of the table's usual first value column belong to the name."""
    firsts = [r.value_x[0] for r in rows if r.value_x and r.values[0][0].isdigit()]
    if not firsts:
        return
    typical = statistics.median(firsts)
    for r in rows:
        while r.values and not r.values[0][0].isdigit() and r.value_x[0] < typical - 12:
            r.name_parts.append((r.page, r.top, r.value_x.pop(0), r.values.pop(0)))
