"""Deterministic, formatting-only cleaning of printed ministry names (Tier 1 of D-023).

Nothing here decides that two *different* spellings are the same body: case, "&", footnote marks,
bracket spacing, stray dashes and words broken across lines in narrow table columns are the only
differences removed. Spelling and naming differences ("Labor"/"Labour", "Department"/"Ministry of Heavy
Industries") are alias decisions, made with evidence in data/reference/ministry_aliases.csv.
"""

from __future__ import annotations

import collections
import re
import unicodedata

LEXICON_MIN_COUNT = 5  # a word must occur in at least this many printed rows to be trusted for re-joining


def _tokens(name: str) -> list[str]:
    name = unicodedata.normalize("NFKC", name).replace("*", " ")
    name = re.sub(r"\(\s+", "(", name)
    name = re.sub(r"\s+\)", ")", name)
    return [t for t in name.split() if t not in {"-", "–", "—"}]


def build_lexicon(names_with_counts: dict[str, int], min_count: int = LEXICON_MIN_COUNT) -> set[str]:
    """Lower-case words seen in at least `min_count` printed rows (bracket/punctuation stripped)."""
    counts: collections.Counter[str] = collections.Counter()
    for name, n in names_with_counts.items():
        for tok in _tokens(name):
            counts[_bare(tok)] += n
    return {w for w, c in counts.items() if c >= min_count and w}


def _bare(token: str) -> str:
    return re.sub(r"[^\w]", "", token.lower())


def rejoin(tokens: list[str], lexicon: set[str]) -> list[str]:
    """Join a word broken across lines ("Telecommunica tions") when the joined word is a trusted word and at
    least one of the pieces is not ("Bio Technology" stays: both pieces are trusted words)."""
    out: list[str] = []
    i = 0
    while i < len(tokens):
        if i + 1 < len(tokens):
            a, b = tokens[i], tokens[i + 1]
            joined = _bare(a + b)
            # both pieces must be word fragments: "Health" + "&" must never become "Health&"
            if (
                _bare(a)
                and _bare(b)
                and joined in lexicon
                and (_bare(a) not in lexicon or _bare(b) not in lexicon)
            ):
                out.append(a + b)
                i += 2
                continue
        out.append(tokens[i])
        i += 1
    return out


def clean_name(name: str, lexicon: set[str]) -> str:
    """Display form: printed name with formatting artefacts removed (case kept)."""
    return " ".join(rejoin(_tokens(name), lexicon))


def name_key(name: str, lexicon: set[str]) -> str:
    """Comparison key: cleaned name, lower-cased, '&' -> 'and', punctuation other than brackets removed."""
    text = clean_name(name, lexicon).lower().replace("&", " and ")
    text = re.sub(r"[^\w()/ ]", " ", text)
    return " ".join(text.split())


GENERIC_WORDS = {"ministry", "department", "of", "and", "the", "for", "o/o"}


def core(key: str) -> str:
    """The distinguishing words of a name key: generic words such as "Ministry of" and "and" removed, so
    "department of heavy industry" and "ministry of heavy industries" compare as
    "heavy industry" / "heavy industries"."""
    return " ".join(t for t in key.replace("(", " ").replace(")", " ").split() if t not in GENERIC_WORDS)


def entity_type(canonical_name: str) -> str:
    if canonical_name.startswith("Ministry of"):
        return "ministry"
    if (
        canonical_name.startswith(("Department of", "Department for"))
        or canonical_name == "Legislative Department"
    ):
        return "department"
    return "organisation"  # CBDT, CBIC, UIDAI, SSC, NITI Aayog, O/o the CAG


def slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", name.lower().replace("&", "and")).strip("_")
