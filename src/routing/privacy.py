"""Personal-data redaction before complaint text leaves the process (LLM calls) or reaches logs.

Redaction is pattern-based. It catches structured identifiers (Aadhaar-like 12-digit numbers, PAN, mobile
numbers, e-mail addresses, bank account and card numbers, IFSC codes). It does NOT catch personal names or
addresses written in free text; the UI tells users not to include them. Reference numbers that the citizen
needs in the draft are kept locally: redaction applies only to what is sent to the LLM.
"""

from __future__ import annotations

import re

PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("EMAIL", re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")),
    ("CARD", re.compile(r"(?<!\d)(?:\d{4}[\s-]?){3}\d{4}(?!\d)")),  # before AADHAAR: 16 digits contain 12
    ("AADHAAR", re.compile(r"(?<!\d)\d{4}[\s-]?\d{4}[\s-]?\d{4}(?!\d)")),
    ("PHONE", re.compile(r"(?<![\d\w])(?:\+?91[\s-]?)?[6-9]\d{4}[\s-]?\d{5}(?!\d)")),
    ("PAN", re.compile(r"\b[A-Z]{5}\d{4}[A-Z]\b")),
    ("IFSC", re.compile(r"\b[A-Z]{4}0[A-Z0-9]{6}\b")),
    ("ACCOUNT", re.compile(r"(?i)(?:a/?c|account)\s*(?:no\.?|number|#)?\s*[:\-]?\s*\d{9,18}")),
]


def redact(text: str) -> tuple[str, dict[str, int]]:
    """Replace personal identifiers with [REDACTED-TYPE]; return the text and a count per type."""
    counts: dict[str, int] = {}
    for name, pattern in PATTERNS:
        text, n = pattern.subn(f"[REDACTED-{name}]", text)
        if n:
            counts[name] = counts.get(name, 0) + n
    return text, counts
