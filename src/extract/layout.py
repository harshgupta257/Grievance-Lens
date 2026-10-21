"""Page layout helpers: words with coordinates, line grouping, and heading location.

pdfplumber's own table detection is not used for data rows: the CPGRAMS tables have no ruling lines
(only shaded header rectangles), so detected cells truncate ministry names and split wrapped names into
extra rows (see DESIGN.md D-015). Parsers work on word coordinates instead.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from functools import cached_property
from pathlib import Path

import pdfplumber

LINE_TOLERANCE = (
    3.2  # points; same visual line. Wrapped-name lines are >= 5.7 pt apart; serials can sit 2.5 pt off
)
FOOTER = re.compile(r"\d+\s*\|\s*P\s*a\s*g\s*e", re.I)
# Section headings carry a dotted number ("3.3.", "6.1") or "Annexure N"; table rows carry a bare serial.
HEADING_PREFIX = re.compile(r"^\s*(Annexure\s*[\d.]+\s*[:.]?|\d+\.\d*\.?(\d+\.?)?)\s+\S", re.I)
TOC_LEADER = re.compile(r"\.{6,}")


@dataclass(frozen=True)
class Word:
    text: str
    x0: float
    x1: float
    top: float
    bottom: float
    page: int  # 1-based physical page number


@dataclass
class Line:
    words: list[Word] = field(default_factory=list)

    @property
    def top(self) -> float:
        return min(w.top for w in self.words)

    @property
    def bottom(self) -> float:
        return max(w.bottom for w in self.words)

    @property
    def page(self) -> int:
        return self.words[0].page

    @property
    def text(self) -> str:
        return " ".join(w.text for w in self.words)


def group_lines(words: list[Word], tolerance: float = LINE_TOLERANCE) -> list[Line]:
    """Group words into visual lines (sorted top-to-bottom, words left-to-right)."""
    lines: list[Line] = []
    for w in sorted(words, key=lambda w: (w.top, w.x0)):
        if lines and abs(w.top - lines[-1].words[0].top) < tolerance:
            lines[-1].words.append(w)
        else:
            lines.append(Line([w]))
    for line in lines:
        line.words.sort(key=lambda w: w.x0)
    return lines


@dataclass(frozen=True)
class Heading:
    page: int
    top: float
    bottom: float  # bottom of the heading line, or of the next line when the match wraps onto it
    text: str  # the heading text (plus the wrapped line when used)
    context: str = ""  # the two lines above: parent headings often carry the period


class PdfDocument:
    """A report PDF with per-page lines cached. Pages are addressed by 1-based physical number."""

    def __init__(self, path: Path):
        self.path = path
        self._pdf = pdfplumber.open(path)
        self._lines: dict[int, list[Line]] = {}

    def close(self) -> None:
        self._pdf.close()

    def __enter__(self) -> PdfDocument:
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    @cached_property
    def page_count(self) -> int:
        return len(self._pdf.pages)

    def lines(self, page: int) -> list[Line]:
        if page not in self._lines:
            raw = self._pdf.pages[page - 1].extract_words(x_tolerance=1.5, y_tolerance=2)
            words = [Word(w["text"], w["x0"], w["x1"], w["top"], w["bottom"], page) for w in raw]
            self._lines[page] = group_lines(words)
        return self._lines[page]

    def page_text(self, page: int) -> str:
        return "\n".join(line.text for line in self.lines(page))

    def full_text(self) -> str:
        return "\n".join(self.page_text(p) for p in range(1, self.page_count + 1))

    def find_headings(self, pattern: str, first_page: int = 4, require_prefix: bool = True) -> list[Heading]:
        """Headings whose text (joined with the next line) matches `pattern`, skipping cover/TOC pages."""
        rx = re.compile(pattern, re.I)
        found: list[Heading] = []
        for page in range(first_page, self.page_count + 1):
            lines = self.lines(page)
            for i, line in enumerate(lines):
                if TOC_LEADER.search(line.text) or (require_prefix and not HEADING_PREFIX.match(line.text)):
                    continue
                context = " ".join(ln.text for ln in lines[max(0, i - 2) : i])
                if rx.search(line.text):
                    found.append(Heading(page, line.top, line.bottom, line.text, context))
                    continue
                nxt = lines[i + 1] if i + 1 < len(lines) else None
                if nxt and rx.search(line.text + " " + nxt.text):
                    found.append(Heading(page, line.top, nxt.bottom, line.text + " " + nxt.text, context))
        return found


def is_footer(line: Line) -> bool:
    return bool(FOOTER.search(line.text))
