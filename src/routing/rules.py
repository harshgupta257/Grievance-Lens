"""Deterministic routing rules: State-matter hints, urgency, missing information and clarifying questions.

These are heuristics written from the labelling guide and the development split only (never the
evaluation set). They produce hints and questions; they never assign a department on their own.
"""

from __future__ import annotations

import re

STATE_TERMS = [
    # English
    r"police station",
    r"\bfir\b",
    r"\bthana\b",
    r"tehsil",
    r"patwari",
    r"mutation",
    r"land record",
    r"municipal",
    r"nagar (nigam|palika|panchayat)",
    r"\bdiscom\b",
    r"electricity (bill|board|meter)",
    r"state (transport|university|pension|scheme|portal|public service)",
    r"roadways",
    r"district hospital",
    r"primary health cent",
    r"\bphc\b",
    r"caste certificate",
    r"\bstray\b",
    r"garbage",
    r"\bdrain",
    # Hinglish
    r"\bbijli\b",
    r"\bnaali\b",
    r"\bkachra\b",
    r"vridha pension",
    r"\bmeter\b",
    # Hindi
    r"थान",
    r"तहसील",
    r"पटवारी",
    r"नगर निगम",
    r"नगर पालिका",
    r"बिजली",
    r"ज़िला अस्पताल",
    r"जिला अस्पताल",
    r"जाति प्रमाणपत्र",
    r"राज्य लोक सेवा",
    r"राज्य परिवहन",
]
# "police verification" for passports is a central (MEA) process, not a State police complaint
STATE_EXCEPTIONS = [r"police verification", r"पुलिस सत्यापन"]
HIGH_URGENCY = [
    r"emergenc",
    r"\burgent",
    r"critical",
    r"dying",
    r"surgery",
    r"dialysis",
    r"stranded",
    r"detained",
    r"missing (child|daughter|son)",
    r"threat to life",
    r"suicide",
    r"child marriage",
    r"bal vivah",
    r"phans",
    r"maar ?peet",
    r"accident",
    r"heart",
    r"danger",
    r"khatra",
    r"आपात",
    r"फंसे",
    r"लापता",
    r"मारपीट",
    r"बाल विवाह",
    r"खतरा",
    r"हालत खराब",
    r"मृत्यु",
]
MEDIUM_URGENCY = [
    r"wage",
    r"salary",
    r"pension",
    r"ration",
    r"refund",
    r"\bpf\b",
    r"provident",
    r"insurance",
    r"claim",
    r"scholarship",
    r"mazdoori",
    r"majduri",
    r"paisa",
    r"rupay",
    r"\brs\b",
    r"₹",
    r"वेतन",
    r"पेंशन",
    r"राशन",
    r"मज़दूरी",
    r"मजदूरी",
    r"पैसा",
    r"पैसे",
    r"रुपये",
    r"छात्रवृत्ति",
]
REFERENCE = re.compile(
    r"(?i)(\b(application|reference|ref|claim|complaint|registration|tracking|pnr|consignment|ticket|case|"
    r"policy|account|order|file)\b.{0,12}\b(no|number|id|#)\b|\b[A-Z]{2,}[-/]?\d{3,}|\d{6,})"
)
TIME = re.compile(
    r"(?i)(?<![\w-])(19|20)\d{2}(?![\w-])|\b(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\b|\b\d+\s*(day|week|month|"
    r"year|din|hafte|mahine|saal)s?\b|\b(yesterday|last (week|month|year)|since)\b|(दिन|हफ्ते|महीने|साल|वर्ष)"
)


def _any(patterns: list[str], text: str) -> list[str]:
    low = text.lower()
    return [p for p in patterns if re.search(p, low)]


def state_matter_hits(text: str) -> list[str]:
    if _any(STATE_EXCEPTIONS, text):
        return []
    return _any(STATE_TERMS, text)


def urgency(text: str) -> str:
    if _any(HIGH_URGENCY, text):
        return "high"
    if _any(MEDIUM_URGENCY, text):
        return "medium"
    return "low"


def missing_information(text: str) -> list[str]:
    """Generic details a grievance officer usually needs and that the text does not appear to contain."""
    missing = []
    if not REFERENCE.search(text):
        missing.append("Any application, reference, claim or complaint number you have")
    if not TIME.search(text):
        missing.append("When the problem started, and any important dates")
    return missing


def clarifying_question(reason: str, names: list[str]) -> str:
    if reason == "ambiguous" and len(names) >= 2:
        return (
            f"Your complaint could belong to more than one department ({names[0]} or {names[1]}). "
            "Which office or scheme handled your case, and what exactly is pending?"
        )
    if reason == "state_matter":
        return (
            "This looks like it may be handled by a State Government or local office (for example police, "
            "land records, the municipality or the State power company). Is it about a Central Government "
            "office or scheme? If so, which one?"
        )
    if reason in {"insufficient_information", "low_evidence"}:
        return (
            "Which government office, scheme or service is this about, what happened, and what would you "
            "like done?"
        )
    if reason == "out_of_scope":
        return "Which ministry, office or scheme is this about? It may be outside the departments covered."
    return "Could you add which office or scheme this concerns and what you want done?"
