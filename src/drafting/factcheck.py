"""Screen a draft for factual tokens and claim words that are not grounded in the user's input.

A *factual token* is a number (Latin or Devanagari digits, with or without separators), an amount, a
reference-like code (letters and digits) or an English month name. Every factual token in the draft must
appear in the user's input (complaint, clarifications, supporting details) or in fixed, known text (the
draft template, the department name, the portal address). *Claim words* (allegations, threats, legal
conclusions) must not appear unless the user wrote them.

This is a screening tool. It cannot prove a draft is faithful: it does not check meaning, and translations
of numbers into words are not recognised.
"""

from __future__ import annotations

import re

from pydantic import BaseModel, Field

DEVANAGARI_DIGITS = str.maketrans("०१२३४५६७८९", "0123456789")
MONTHS = r"(january|february|march|april|may|june|july|august|september|october|november|december)"
TOKEN = re.compile(
    r"(?i)\b[A-Z]*\d[\w/-]*\b|"  # numbers and codes containing a digit: 21, 18,000 -> 18 000, PF-2026-0457
    rf"\b{MONTHS}\b"
)
CLAIM_WORDS = [
    "corrupt",
    "bribe",
    "fraud",
    "illegal",
    "negligen",
    "criminal",
    "harass",
    "legal action",
    "court",
    "lawsuit",
    "sue ",
    "compensation",
    "damages",
    "contempt",
    "punish",
    "suspend",
    "भ्रष्ट",
    "रिश्वत",
    "धोखाधड़ी",
    "अवैध",
    "लापरवाह",
    "कानूनी कार्रवाई",
    "अदालत",
    "मुआवज़ा",
]


class FactCheck(BaseModel):
    unsupported_tokens: list[str] = Field(default_factory=list)
    unsupported_claim_words: list[str] = Field(default_factory=list)
    checked_tokens: int = 0

    @property
    def passed(self) -> bool:
        return not self.unsupported_tokens and not self.unsupported_claim_words


def normalise_text(text: str) -> str:
    text = text.translate(DEVANAGARI_DIGITS)
    text = re.sub(r"(?<=\d),(?=\d)", "", text)  # 18,000 -> 18000 ; 1,23,456 -> 123456
    return text


def factual_tokens(text: str) -> set[str]:
    return {t.lower() for t in (m.group(0) for m in TOKEN.finditer(normalise_text(text)))}


def check(draft_text: str, inputs: list[str], fixed_text: list[str]) -> FactCheck:
    allowed = (
        set().union(*(factual_tokens(t) for t in inputs + fixed_text)) if inputs or fixed_text else set()
    )
    found = factual_tokens(draft_text)
    unsupported = sorted(t for t in found if t not in allowed)
    joined_inputs = " ".join(inputs).lower()
    fixed = " ".join(fixed_text).lower()
    low = draft_text.lower()
    claims = [w for w in CLAIM_WORDS if w in low and w not in joined_inputs and w not in fixed]
    return FactCheck(
        unsupported_tokens=unsupported, unsupported_claim_words=claims, checked_tokens=len(found)
    )
