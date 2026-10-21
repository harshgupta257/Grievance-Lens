"""Row assembly on synthetic word layouts that reproduce the patterns found in the real reports."""

from __future__ import annotations

from src.extract.layout import Line, Word, group_lines
from src.extract.rows import collect_rows

NAME_X, VALUE_X = 100.0, 300.0


class FakeDoc:
    """Minimal stand-in for PdfDocument: lines per page built from (top, [(x0, text), ...])."""

    def __init__(self, pages: dict[int, list[tuple[float, list[tuple[float, str]]]]]):
        self.page_count = max(pages)
        self._pages = pages

    def lines(self, page: int) -> list[Line]:
        words = [
            Word(text, x, x + 8 * len(text), top, top + 8, page)
            for top, items in self._pages.get(page, [])
            for x, text in items
        ]
        return group_lines(words)


def header(top=50.0):
    return [
        (top, [(40, "S."), (NAME_X, "Name"), (VALUE_X, "Brought"), (360, "Receipts"), (420, "Pending")]),
        (top + 8, [(40, "No."), (VALUE_X, "Forward")]),
    ]


def anchor(top, serial, name, values):
    items = [(40, serial)] + [(NAME_X + 40 * i, w) for i, w in enumerate(name.split())]
    return (top, items + [(VALUE_X + 60 * i, v) for i, v in enumerate(values)])


def name_line(top, text):
    return (top, [(NAME_X + 40 * i, w) for i, w in enumerate(text.split())])


def names(doc, page=1, start=0.0):
    return [r.name for r in collect_rows(doc, page, start).rows]


def test_single_line_rows():
    doc = FakeDoc(
        {
            1: header()
            + [
                anchor(80, "1", "Department of Posts", ["1", "2", "3"]),
                anchor(96, "2", "Ministry of Coal", ["4", "5", "6"]),
            ]
        }
    )
    result = collect_rows(doc, 1, 0.0)
    assert [r.name for r in result.rows] == ["Department of Posts", "Ministry of Coal"]
    assert result.rows[1].values == ["4", "5", "6"]
    assert "Brought" in result.column_header_text


def test_vertically_centred_names_split_at_row_boundary():
    # cell padding (16.7) is slightly larger than intra-cell spacing (16.2), as in the 2026 GRAI tables
    doc = FakeDoc(
        {
            1: header()
            + [
                name_line(90.0, "Department of"),
                anchor(106.2, "1", "", ["1", "2", "3"]),
                name_line(122.4, "Posts"),
                name_line(139.1, "Department of"),
                name_line(155.3, "Financial"),
                anchor(171.5, "2", "Services", ["4", "5", "6"]),
                name_line(187.7, "(Insurance"),
                name_line(203.9, "Division)"),
            ]
        }
    )
    assert names(doc) == ["Department of Posts", "Department of Financial Services (Insurance Division)"]


def test_top_aligned_names_with_uniform_spacing_continue_downward():
    # 2022 layout: names start on the anchor line and wrap below with the same spacing as between rows
    doc = FakeDoc(
        {
            1: header()
            + [
                anchor(80.0, "1", "Central Board of Direct Taxes", ["1", "2", "3"]),
                name_line(92.0, "(Income Tax)"),
                anchor(104.0, "2", "Department of Financial Services", ["4", "5", "6"]),
                name_line(116.0, "(Banking Division)"),
                anchor(131.0, "3", "Ministry of Cooperation", ["7", "8", "9"]),
            ]
        }
    )
    assert names(doc) == [
        "Central Board of Direct Taxes (Income Tax)",
        "Department of Financial Services (Banking Division)",
        "Ministry of Cooperation",
    ]


def test_serial_printed_below_its_figures_is_merged():
    # Sep 2026, rows 21-22: the serial sits ~4.5 pt below the numbers line
    doc = FakeDoc(
        {
            1: header()
            + [
                name_line(100.0, "Department of Rural"),
                (105.8, [(VALUE_X, "1025"), (360, "67045"), (420, "1020")]),
                (110.3, [(40, "21")]),
                name_line(113.6, "Development"),
            ]
        }
    )
    rows = collect_rows(doc, 1, 0.0).rows
    assert [(r.serial, r.name, r.values) for r in rows] == [
        ("21", "Department of Rural Development", ["1025", "67045", "1020"])
    ]


def test_rank_on_serial_line_below_figures_keeps_name_order():
    # May 2024 Group B: figures on the first name line; "serial ... rank" on the next line
    doc = FakeDoc(
        {
            1: header()
            + [
                (
                    100.0,
                    [
                        (NAME_X, "Department"),
                        (NAME_X + 50, "of"),
                        (NAME_X + 70, "Social"),
                        (VALUE_X, "449"),
                        (360, "0.682"),
                    ],
                ),
                (111.5, [(40, "11"), (NAME_X, "Justice"), (NAME_X + 50, "and"), (480, "11")]),
                name_line(123.0, "Empowerment"),
                anchor(141.0, "12", "Ministry of Coal", ["250", "0.652", "12"]),
            ]
        }
    )
    rows = collect_rows(doc, 1, 0.0).rows
    assert rows[0].name == "Department of Social Justice and Empowerment"
    assert rows[0].values == ["449", "0.682", "11"]
    assert rows[1].name == "Ministry of Coal"


def test_table_continues_on_next_page_only_with_next_serial():
    page1 = header() + [
        anchor(80, "1", "Ministry of Coal", ["1", "2", "3"]),
        (780, [(450, "5"), (465, "|"), (475, "Page")]),
    ]
    page2 = header() + [anchor(80, "2", "Ministry of Steel", ["4", "5", "6"])]
    page3 = header() + [anchor(80, "1", "Another Table Row", ["7", "8", "9"])]
    doc = FakeDoc({1: page1, 2: page2, 3: page3})
    result = collect_rows(doc, 1, 0.0)
    assert [r.name for r in result.rows] == ["Ministry of Coal", "Ministry of Steel"]
    assert result.last_page == 2


def test_row_split_by_page_break_keeps_its_tail():
    # Feb 2026: "Unique Identification" ends page 17; "Authority of / India" open page 18, then the next row
    footer = (780, [(450, "5"), (465, "|"), (475, "Page")])
    page1 = header() + [
        name_line(90.0, "Department of"),
        anchor(106.2, "1", "", ["1", "2", "3"]),
        name_line(122.4, "Posts"),
        name_line(139.1, "Unique"),
        anchor(155.3, "2", "Identification", ["4", "5", "6"]),
        footer,
    ]
    page2 = header() + [
        name_line(90.0, "Authority of"),
        name_line(106.2, "India"),
        name_line(122.9, "Ministry of Civil"),
        anchor(130.9, "3", "", ["7", "8", "9"]),
        name_line(139.1, "Aviation"),
    ]
    rows = collect_rows(FakeDoc({1: page1, 2: page2}), 1, 0.0).rows
    assert [r.name for r in rows] == [
        "Department of Posts",
        "Unique Identification Authority of India",
        "Ministry of Civil Aviation",
    ]


def test_running_text_after_table_is_not_taken_as_a_name():
    doc = FakeDoc(
        {
            1: header()
            + [
                anchor(80, "1", "Ministry of Coal", ["1", "2", "3"]),
                (
                    100,
                    [
                        (NAME_X, "Note:"),
                        (VALUE_X + 20, "values"),
                        (VALUE_X + 80, "are"),
                        (VALUE_X + 120, "provisional"),
                    ],
                ),
            ]
        }
    )
    assert names(doc) == ["Ministry of Coal"]


def test_dash_inside_a_name_is_not_a_value():
    # Oct 2023: "Ministry of Micro - Small and Medium Enterprises" — the '-' sits in the name column
    doc = FakeDoc(
        {
            1: header()
            + [
                anchor(80, "26", "Department of Posts", ["85", "0.298", "26"]),
                (
                    100,
                    [
                        (40, "27"),
                        (NAME_X, "Small"),
                        (NAME_X + 50, "Medium"),
                        (NAME_X + 110, "-"),
                        (VALUE_X, "229"),
                        (360, "0.318"),
                        (420, "27"),
                    ],
                ),
                anchor(120, "28", "Department of Space", ["32", "0.190", "28"]),
            ]
        }
    )
    row = collect_rows(doc, 1, 0.0).rows[1]
    assert row.values == ["229", "0.318", "27"]
    assert row.name == "Small Medium -"
