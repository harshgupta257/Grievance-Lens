"""Input handling for complaint text: validation, language detection and prompt-injection flags.

Complaint text is untrusted data. Nothing in it is ever executed or followed; injection patterns are only
flagged (so the response can say an embedded instruction was ignored) and the text is passed to the LLM
inside a delimited data block with explicit instructions to treat it as data.
"""

from __future__ import annotations

import re
import unicodedata

DEVANAGARI = re.compile(r"[ऀ-ॿ]")
LATIN = re.compile(r"[A-Za-z]")
# common Roman-script Hindi function words; two or more distinct hits suggest Hinglish
HINGLISH_MARKERS = {
    "hai",
    "hain",
    "nahi",
    "nahin",
    "nhi",
    "ka",
    "ki",
    "ke",
    "ko",
    "se",
    "me",
    "mein",
    "mera",
    "meri",
    "mere",
    "tha",
    "thi",
    "ho",
    "gaya",
    "gayi",
    "kiya",
    "kar",
    "raha",
    "rahi",
    "bhi",
    "abhi",
    "tak",
    "par",
    "aur",
    "wale",
    "wala",
    "kya",
    "kaise",
    "kab",
    "bol",
    "bolta",
    "bolte",
    "paisa",
    "kuch",
    "koi",
    "sab",
    "hua",
    "mila",
    "mili",
    "aaya",
    "aayi",
    "diya",
    "liya",
    "ghar",
    "gaon",
}
INJECTION_PATTERNS = [
    re.compile(p, re.I)
    for p in (
        r"ignore (all |any |your )?(previous |prior |above )?(instructions|rules|prompt)",
        r"(system prompt|api key|secret|password|credentials)",
        r"\b(system|admin|developer)\s*(override|mode|:)",
        r"</?\s*(complaint|system|instructions?)\s*>",
        r"\b(drop|delete|truncate)\s+table\b",
        r"[{\[]\s*\"?(department|confidence|abstain)\"?\s*[:=]",
        r"\b(you are now|you must not abstain|always answer|route this to|confidence 1(\.0)?)\b",
        r"(bhool jao|bhul jao|nirdesh)",
        r"(नियम भूल|निर्देश|अनदेखा करो)",
    )
]


class InputError(ValueError):
    """The complaint cannot be processed as given (empty, too short, too long, unreadable)."""


def clean(text: str, min_chars: int, max_chars: int) -> str:
    """Normalise Unicode (NFC), drop control characters, collapse whitespace, enforce length limits."""
    if not isinstance(text, str):
        raise InputError("complaint text must be a string")
    text = unicodedata.normalize("NFC", text)
    text = "".join(ch for ch in text if ch in "\n\t" or unicodedata.category(ch)[0] != "C")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    if len(text) < min_chars:
        raise InputError(f"complaint is too short ({len(text)} characters; at least {min_chars} needed)")
    if len(text) > max_chars:
        raise InputError(f"complaint is too long ({len(text)} characters; at most {max_chars})")
    return text


def detect_language(text: str) -> str:
    """'hi' (mostly Devanagari), 'en', 'hinglish' (Roman-script Hindi) or 'mixed' (both scripts)."""
    dev = len(DEVANAGARI.findall(text))
    lat = len(LATIN.findall(text))
    if dev and lat and min(dev, lat) / (dev + lat) > 0.15:
        return "mixed"
    if dev > lat:
        return "hi"
    words = set(re.findall(r"[a-z]+", text.lower()))
    return "hinglish" if len(words & HINGLISH_MARKERS) >= 2 else "en"


def injection_flags(text: str) -> list[str]:
    """Patterns that look like instructions to the system. Flagged only; never followed."""
    return [p.pattern for p in INJECTION_PATTERNS if p.search(text)]
