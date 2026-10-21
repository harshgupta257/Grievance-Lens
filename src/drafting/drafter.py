"""Complaint drafting (Phase 10): a structured draft built only from what the citizen wrote.

The drafter is deterministic and template-based. It never paraphrases, summarises or translates the
citizen's text: the complaint is reproduced as written, details the citizen supplied are inserted as given,
and everything missing becomes an explicit placeholder such as [ADD APPLICATION NUMBER]. Headings and
standard sentences are in the chosen language (English or Hindi). Historical statistics are returned
separately from the draft, with their period and source, and are never written into the draft.
Every draft is screened by src/drafting/factcheck.py; the result is returned with the draft.

Usage (PowerShell, project root, venv active):
    python -m src.drafting.drafter "mera pf claim 3 mahine se pending hai" --department ministry_of_labour_and_employment
"""

from __future__ import annotations

import argparse
import re
import sys
from typing import Literal

from pydantic import BaseModel, Field

from src.drafting.factcheck import FactCheck, check
from src.performance.stats import DepartmentPerformance, department_performance
from src.routing.rules import TIME
from src.routing.schemas import DISCLAIMER
from src.routing.text import clean

PORTAL = "https://pgportal.gov.in/"
CLARIFICATION_FIELDS = ("reference_numbers", "dates", "location", "previous_attempts", "action_requested")

T = {
    "en": {
        "subject": "Grievance regarding {category}",
        "subject_unknown": "Grievance: [ADD A SHORT SUBJECT]",
        "to": "To: {department}, Government of India (lodged through CPGRAMS)",
        "to_unknown": "To: [CHOOSE THE DEPARTMENT ON THE CPGRAMS PORTAL]",
        "details": "1. Details of the grievance (in my own words)",
        "timeline": "2. Relevant dates and timeline",
        "refs": "3. Application / reference numbers",
        "location": "4. Office, branch or place involved",
        "previous": "5. Previous attempts to resolve the issue",
        "action": "6. Action requested",
        "docs": "7. Documents I can attach",
        "closing": "I request that my grievance be examined and resolved.\n\n[YOUR NAME]\n[DATE]",
        "ph_dates": "[ADD DATES: when the problem started, when you applied or complained]",
        "ph_refs": "[ADD APPLICATION NUMBER / REFERENCE NUMBER, if any]",
        "ph_location": "[ADD THE OFFICE, BRANCH OR PLACE INVOLVED]",
        "ph_previous": "[ADD: offices you contacted, when, and any earlier complaint numbers]",
        "ph_action": "[STATE CLEARLY WHAT YOU WANT DONE]",
        "from_text": "As mentioned in my complaint: ",
        "docs_list": [
            "Copy of my application or acknowledgement receipt, if any",
            "Copies of earlier correspondence or complaint replies, if any",
            "Any receipt, statement or screenshot that shows the problem",
            "Identity document only if the portal asks for it (do not share a full Aadhaar number unless required)",
        ],
        "checklist": [
            "Read the whole draft. Does it say what happened, in your words?",
            "Replace every text in [SQUARE BRACKETS] with your own details, or delete the line.",
            "Check every date, amount and reference number against your documents.",
            "Do not add anything you cannot support with facts or documents.",
            "Remove personal details that are not needed (full Aadhaar number, bank PIN, passwords).",
            "Check the department suggested here; you can choose a different one on the portal.",
            f"Submit it yourself on the official CPGRAMS portal: {PORTAL}. This tool never submits complaints.",
        ],
    },
    "hi": {
        "subject": "विषय: {category} के संबंध में शिकायत",
        "subject_unknown": "विषय: [संक्षिप्त विषय लिखें (ADD A SHORT SUBJECT)]",
        "to": "सेवा में: {department}, भारत सरकार (सीपीग्राम्स के माध्यम से)",
        "to_unknown": "सेवा में: [सीपीग्राम्स पोर्टल पर विभाग चुनें (CHOOSE THE DEPARTMENT)]",
        "details": "1. शिकायत का विवरण (मेरे अपने शब्दों में)",
        "timeline": "2. संबंधित तिथियां और समयक्रम",
        "refs": "3. आवेदन / संदर्भ संख्या",
        "location": "4. संबंधित कार्यालय, शाखा या स्थान",
        "previous": "5. समस्या सुलझाने के पिछले प्रयास",
        "action": "6. अनुरोधित कार्रवाई",
        "docs": "7. संलग्न किए जा सकने वाले दस्तावेज़",
        "closing": "अनुरोध है कि मेरी शिकायत की जांच कर इसका समाधान किया जाए।\n\n[आपका नाम (YOUR NAME)]\n[तिथि (DATE)]",
        "ph_dates": "[तिथियां जोड़ें: समस्या कब शुरू हुई, आवेदन या शिकायत कब की (ADD DATES)]",
        "ph_refs": "[आवेदन / संदर्भ संख्या जोड़ें, यदि हो (ADD APPLICATION NUMBER)]",
        "ph_location": "[संबंधित कार्यालय, शाखा या स्थान जोड़ें (ADD OFFICE / PLACE)]",
        "ph_previous": "[जोड़ें: किन कार्यालयों से संपर्क किया, कब, और पिछली शिकायत संख्या (ADD PREVIOUS ATTEMPTS)]",
        "ph_action": "[स्पष्ट लिखें कि आप क्या कार्रवाई चाहते हैं (STATE WHAT YOU WANT DONE)]",
        "from_text": "जैसा कि मेरी शिकायत में बताया गया है: ",
        "docs_list": [
            "मेरे आवेदन या पावती की प्रति, यदि हो",
            "पिछले पत्राचार या शिकायत के उत्तर की प्रतियां, यदि हों",
            "समस्या दिखाने वाली कोई रसीद, विवरण या स्क्रीनशॉट",
            "पहचान दस्तावेज़ केवल तभी जब पोर्टल मांगे (आवश्यक न हो तो पूरा आधार नंबर साझा न करें)",
        ],
        "checklist": [
            "पूरा मसौदा पढ़ें। क्या इसमें आपके शब्दों में वही लिखा है जो हुआ?",
            "[कोष्ठक] में लिखे हर हिस्से को अपनी जानकारी से बदलें या पंक्ति हटा दें।",
            "हर तिथि, राशि और संदर्भ संख्या को अपने दस्तावेज़ों से मिलाएं।",
            "ऐसी कोई बात न जोड़ें जिसे आप तथ्य या दस्तावेज़ से साबित न कर सकें।",
            "अनावश्यक निजी जानकारी हटाएं (पूरा आधार नंबर, बैंक पिन, पासवर्ड)।",
            "सुझाया गया विभाग जांचें; पोर्टल पर आप दूसरा विभाग चुन सकते हैं।",
            f"शिकायत स्वयं आधिकारिक सीपीग्राम्स पोर्टल पर दर्ज करें: {PORTAL}। यह टूल कभी शिकायत दर्ज नहीं करता।",
        ],
    },
}


class DraftRequest(BaseModel):
    complaint: str
    language: Literal["en", "hi"] = "en"
    department_key: str | None = None
    department_name: str | None = None
    category: str | None = None
    clarifications: dict[str, str] = Field(default_factory=dict)
    supporting_details: str | None = None


class Section(BaseModel):
    title: str
    body: str
    from_user: bool  # True when the body is the user's own words or details
    placeholder: bool  # True when the body is a placeholder to fill in


class Draft(BaseModel):
    language: str
    subject: str
    addressed_to: str
    sections: list[Section]
    text: str
    placeholders: list[str]
    review_checklist: list[str]
    factcheck: FactCheck
    department_statistics: DepartmentPerformance | None = None
    statistics_note: str = (
        "Verified department-level statistics from DARPG reports, shown for context only. They are not part "
        "of your complaint and do not predict how your complaint will be handled."
    )
    disclaimer: str = DISCLAIMER


SENTENCE = re.compile(r"[^.!?।\n]+[.!?।]?")
# a reference value with its label ("Claim ID PF-2026-0457", "PNR 4521873390") or a bare code ("PF-2026-0457")
REF_VALUE = re.compile(
    r"(?i)\b(?:application|reference|ref|claim|complaint|registration|tracking|pnr|consignment|ticket|case|"
    r"policy|order|file|uan|form)\s*(?:no\.?|number|id|#)?\s*[:#]?\s*[A-Z0-9][\w/-]*\d[\w/-]*"
    r"|\b[A-Z]{2,}[-/]\d[\w/-]*"
)


def _sentences_with(pattern: re.Pattern[str], text: str) -> list[str]:
    """Whole sentences of the citizen's text that contain a match (verbatim), in order, at most four."""
    out = [s.strip() for s in SENTENCE.findall(text) if pattern.search(s)]
    return list(dict.fromkeys(s for s in out if s))[:4]


def _matches(pattern: re.Pattern[str], text: str) -> list[str]:
    """The matched text itself (e.g. 'Claim ID PF-2026-0457'), verbatim; drops matches inside longer ones."""
    found = [m.group(0).strip(" ,.;") for m in pattern.finditer(text)]
    kept = [f for f in dict.fromkeys(found) if not any(f != g and f in g for g in found)]
    return kept[:4]


def screen(text: str, req: DraftRequest) -> FactCheck:
    """Factuality screen of `text` against the request's inputs and the fixed template text."""
    t = T[req.language]
    inputs = [req.complaint, *req.clarifications.values(), req.supporting_details or ""]
    fixed = [
        *(v if isinstance(v, str) else " ".join(v) for v in t.values()),
        req.department_name or "",
        req.category or "",
        PORTAL,
    ]
    return check(text, inputs, fixed)


def build_draft(req: DraftRequest, with_statistics: bool = True) -> Draft:
    t = T[req.language]
    complaint = clean(req.complaint, 1, 20000)
    c = {k: (v or "").strip() for k, v in req.clarifications.items() if k in CLARIFICATION_FIELDS}

    def field_or(name: str, extracted: list[str], placeholder: str) -> tuple[str, bool, bool]:
        if c.get(name):
            return c[name], True, False
        if extracted:
            return t["from_text"] + "; ".join(f'"{e}"' for e in extracted), True, False
        return placeholder, False, True

    sections = [Section(title=t["details"], body=complaint, from_user=True, placeholder=False)]
    for title, name, found, ph in (
        (t["timeline"], "dates", _sentences_with(TIME, complaint), t["ph_dates"]),
        (t["refs"], "reference_numbers", _matches(REF_VALUE, complaint), t["ph_refs"]),
    ):
        body, from_user, is_ph = field_or(name, found, ph)
        sections.append(Section(title=title, body=body, from_user=from_user, placeholder=is_ph))
    for title, name, ph in (
        (t["location"], "location", t["ph_location"]),
        (t["previous"], "previous_attempts", t["ph_previous"]),
        (t["action"], "action_requested", t["ph_action"]),
    ):
        body, from_user, is_ph = field_or(name, [], ph)
        sections.append(Section(title=title, body=body, from_user=from_user, placeholder=is_ph))
    docs = "\n".join(f"- {d}" for d in t["docs_list"])
    if req.supporting_details:
        docs = f"- {req.supporting_details.strip()}\n{docs}"
    sections.append(
        Section(title=t["docs"], body=docs, from_user=bool(req.supporting_details), placeholder=False)
    )

    known_category = req.category and req.category != "unknown"
    subject = t["subject"].format(category=req.category) if known_category else t["subject_unknown"]
    to = t["to"].format(department=req.department_name) if req.department_name else t["to_unknown"]
    text = "\n\n".join([subject, to] + [f"{s.title}\n{s.body}" for s in sections] + [t["closing"]])
    placeholders = sorted(set(re.findall(r"\[[^\]]+\]", text)))
    fc = screen(text, req)
    stats = department_performance(req.department_key) if (with_statistics and req.department_key) else None
    return Draft(
        language=req.language,
        subject=subject,
        addressed_to=to,
        sections=sections,
        text=text,
        placeholders=placeholders,
        review_checklist=t["checklist"],
        factcheck=fc,
        department_statistics=stats,
    )


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Build a complaint draft (never submits anything)")
    p.add_argument("complaint")
    p.add_argument("--department", default=None, help="ministry_key from the knowledge base")
    p.add_argument("--language", choices=["en", "hi"], default="en")
    args = p.parse_args(argv)
    name = None
    if args.department:
        from src.knowledge.retrieve import load_kb

        name = {r["ministry_key"]: r["canonical_name"] for r in load_kb()}.get(args.department)
    d = build_draft(
        DraftRequest(
            complaint=args.complaint,
            language=args.language,
            department_key=args.department,
            department_name=name,
        )
    )
    print(d.text)
    print("\nFactuality screen:", "passed" if d.factcheck.passed else d.factcheck.model_dump())
    return 0


if __name__ == "__main__":
    sys.exit(main())
