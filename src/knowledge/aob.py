"""Parse the Second Schedule of the Government of India (Allocation of Business) Rules, 1961 into sections and
numbered items: the official list of subjects allocated to each ministry and department.

Usage (PowerShell, project root, venv active):
    python -m src.knowledge.aob --download   # first time: fetch the PDF (one request) and verify its SHA-256
    python -m src.knowledge.aob

Input:  data/raw/mandates/aob_rules_cabsec_4265.pdf (Cabinet Secretariat consolidated text, Amendment Series
        no. 386 dated 22 July 2026; URL and SHA-256 in data/reference/mandates/sources.csv)
Output: data/reference/mandates/aob_items.csv   one row per item: section, part, item number, text, PDF page

Item text is kept verbatim, except that page footers, footnote blocks and footnote reference numbers are
removed and wrapped lines are joined. Section headers are found in the text (the PDF's index has page errors).
"""

from __future__ import annotations

import csv
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

from src.config import PROJECT_ROOT

PDF = PROJECT_ROOT / "data" / "raw" / "mandates" / "aob_rules_cabsec_4265.pdf"
OUT = PROJECT_ROOT / "data" / "reference" / "mandates" / "aob_items.csv"
SOURCES = PROJECT_ROOT / "data" / "reference" / "mandates" / "sources.csv"
SCHEDULE_PAGES = (16, 190)  # Second Schedule; the appendix starts on page 191

SECTION = re.compile(
    r"^(?:[A-Z]{1,2}\.\s+)?((?:MINISTRY|DEPARTMENT) (?:OF|FOR) .+|LEGISLATIVE DEPARTMENT|RAILWAY BOARD|"
    r"CABINET SECRETARIAT|PRESIDENT'S SECRETARIAT|PRIME MINISTER'S OFFICE|NITI AAYOG.*)$"
)
PART = re.compile(r"^((?:PART\s+)?[IVX]{1,5})\.?\s+(.+)$")
ITEM = re.compile(r"^(\d{1,3}[A-Z]{0,2})\.\s*(.*)$")
FOOTER = re.compile(r"^Page \d+ of \d+$")
# footnotes start at a rule line, or at the first "1 Modified vide ..." line when the rule is missing
FOOTNOTE_RULE = re.compile(r"^[-_]{3,}")
FOOTNOTE_LINE = re.compile(
    r"^\d{1,2}\s*(?:Modified|Inserted|Omitted|Substituted|Renumbered|Re-numbered|Amended|Added|Deleted|Shifted)\b",
    re.I,
)
FOOTNOTE_REF = re.compile(r"(?:(?<=[.;:)\]’”])\s?\d{1,2}|(?<=[A-Za-z])\d{1,2})$")
# "Scheme 3" ends in a footnote reference; "section 3" or "Article 3" ends in part of the text
SPACED_REF = re.compile(r"(?<=[A-Za-z])\s(\d{1,2})$")
NUMBER_WORDS = {
    "article",
    "articles",
    "section",
    "sections",
    "item",
    "items",
    "entry",
    "entries",
    "list",
    "rule",
    "rules",
    "part",
    "schedule",
    "clause",
    "chapter",
    "no",
    "number",
    "of",
    "and",
    "to",
    "regulation",
}


def _strip_refs(text: str) -> str:
    text = FOOTNOTE_REF.sub("", text).strip()
    m = SPACED_REF.search(text)
    if m and text[: m.start()].split()[-1].lower().rstrip(".") not in NUMBER_WORDS:
        text = text[: m.start()]
    return text.strip()


HINDI_NAME = re.compile(
    r"^\(([A-Z][A-Za-z ,’'()-]+(?:VIBHAG|MANTRALAYA|BOARD|SACHIVALAYA|KARYALAYA)[A-Za-z ,()]*)\)\s*\d*$", re.I
)


@dataclass
class Item:
    section: str
    part: str
    number: str
    page: int
    lines: list[str] = field(default_factory=list)

    @property
    def text(self) -> str:
        return re.sub(r"\s+", " ", " ".join(self.lines)).strip()


def _clean_header(line: str) -> str:
    return re.sub(r"\s*\d{1,2}$", "", line).strip()


def page_lines(pdf: Path = PDF) -> list[tuple[int, str]]:
    import pdfplumber

    out = []
    with pdfplumber.open(pdf) as doc:
        lo, hi = SCHEDULE_PAGES
        for number in range(lo, hi + 1):
            in_footnotes = False
            for line in (doc.pages[number - 1].extract_text() or "").splitlines():
                line = line.strip()
                if FOOTNOTE_RULE.match(line) or FOOTNOTE_LINE.match(line):
                    in_footnotes = True  # footnotes run to the end of the page
                if not line or in_footnotes or FOOTER.match(line):
                    continue
                out.append((number, line))
    return out


def parse(lines: list[tuple[int, str]]) -> tuple[list[Item], dict[str, dict]]:
    items: list[Item] = []
    sections: dict[str, dict] = {}
    ministry = section = part = ""
    current: Item | None = None
    i = 0
    while i < len(lines):
        page, line = lines[i]
        header = _clean_header(line)
        m = SECTION.match(header) if header.upper() == header else None
        if m:
            name = m.group(1)
            # a wrapped header continues on the next upper-case line that is not the Hindi name or an item
            while i + 1 < len(lines):
                nxt = _clean_header(lines[i + 1][1])
                if (
                    nxt.upper() == nxt
                    and not nxt.startswith("(")
                    and not ITEM.match(nxt)
                    and not PART.match(nxt)
                    and not SECTION.match(nxt)
                ):
                    name, i = f"{name} {nxt}", i + 1
                else:
                    break
            name = _clean_header(name)
            if name.startswith("MINISTRY"):
                ministry = name
            elif not re.match(r"^[A-Z]{1,2}\.\s", header) and name != "RAILWAY BOARD":
                ministry = ""  # a department or office not under a ministry (e.g. Department of Space)
            section, part, current = name, "", None
            sections[section] = {"ministry": ministry, "first_page": page, "hindi_name": ""}
            i += 1
            continue
        h = HINDI_NAME.match(line)
        if h and section and not sections[section]["hindi_name"] and current is None:
            sections[section]["hindi_name"] = _clean_header(h.group(1))
            i += 1
            continue
        p = PART.match(line)
        if p and (line.upper() == line or line.startswith("PART ")) and section:
            part, current = _clean_header(p.group(1) + ". " + p.group(2)), None
            # a wrapped part heading continues while lines stay upper-case and are not items or a new section
            while (
                i + 1 < len(lines)
                and lines[i + 1][1].upper() == lines[i + 1][1]
                and not ITEM.match(lines[i + 1][1])
                and not SECTION.match(_clean_header(lines[i + 1][1]))
            ):
                i += 1
                part = f"{part} {_clean_header(lines[i][1])}"
            i += 1
            continue
        it = ITEM.match(line)
        if it and section:
            current = Item(section, part, it.group(1), page, [_strip_refs(it.group(2))])
            items.append(current)
        elif current is not None:
            current.lines.append(_strip_refs(line))
        i += 1
    return items, sections


def write(items: list[Item], sections: dict[str, dict], out: Path = OUT) -> None:
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f, lineterminator="\n")
        w.writerow(
            ["section", "ministry", "section_hindi_name", "part", "item", "omitted", "pdf_page", "text"]
        )
        for it in items:
            s = sections[it.section]
            omitted = it.text.lower().rstrip(".").strip() in {"omitted", ""} or it.text.lower().startswith(
                "omitted"
            )
            w.writerow(
                [it.section, s["ministry"], s["hindi_name"], it.part, it.number, omitted, it.page, it.text]
            )


def download(sources: Path = SOURCES, dest: Path = PDF) -> None:
    """Fetch the AoB PDF once from the URL in sources.csv and refuse it unless its SHA-256 matches."""
    import hashlib
    import urllib.request

    with sources.open(encoding="utf-8") as f:
        row = next(r for r in csv.DictReader(f) if r["source_id"] == "aob_rules")
    req = urllib.request.Request(row["url"], headers={"User-Agent": "cpgrams-portfolio-research"})
    with urllib.request.urlopen(req, timeout=60) as resp:
        data = resp.read()
    digest = hashlib.sha256(data).hexdigest().upper()
    if digest != row["sha256"].upper():
        raise ValueError(
            f"checksum mismatch: got {digest}, expected {row['sha256']} (has the PDF been replaced?)"
        )
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(data)


def main() -> int:
    if "--download" in sys.argv[1:]:
        download()
        print(f"Downloaded and verified {PDF.relative_to(PROJECT_ROOT)}")
    if not PDF.exists():
        print(f"[aob] missing {PDF.relative_to(PROJECT_ROOT)}; run with --download", file=sys.stderr)
        return 1
    items, sections = parse(page_lines())
    write(items, sections)
    with_items = {it.section for it in items}
    print(
        f"{len(sections)} section headers, {len(with_items)} with items, {len(items)} items "
        f"-> {OUT.relative_to(PROJECT_ROOT)}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
