"""Build the synthetic complaint corpus (Phase 8) from the authoring files.

Usage (PowerShell, project root, venv active):
    python -m src.complaints.corpus

Inputs (one complaint per line, written by the assistant in this project; see docs/complaint_data.md):
    data/complaints/authored/dev_authored.txt
    data/complaints/authored/eval_authored.txt
    line format:  INTENT | lang | scenario | difficulty | urgency | category | text
        INTENT    department code (CODES below), "A/B" when either is acceptable, or STATE / OOS / INSUF,
                  optionally "ADV:" + code for a prompt-injection attempt wrapped around a real grievance
        lang      en | hi | hinglish;  scenario: std | amb | state | oos | insuf | adv
        difficulty easy | medium | hard;  urgency: high | medium | low | unknown

Outputs (data/complaints/):
    dev.jsonl                     development data: training, tuning and model selection (split train/dev)
    eval_texts.jsonl              evaluation texts only (no labels): for routing and for owner labelling
    eval_owner_labels.csv         labelling sheet with blank label columns (the owner fills it in)
    eval_generation_intent.csv    what the author intended when writing each evaluation text. NOT ground
                                  truth;
                                  keep closed until the owner has labelled (docs/labelling_guide.md)

Every record carries source = synthetic and its generation method. Records are checked for personal data
(12-digit numbers, phone numbers, e-mail addresses) and the run fails if any is found.
"""

from __future__ import annotations

import csv
import hashlib
import json
import random
import re
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

from src.config import PROJECT_ROOT

DIR = PROJECT_ROOT / "data" / "complaints"
AUTHORED = {"dev": DIR / "authored" / "dev_authored.txt", "eval": DIR / "authored" / "eval_authored.txt"}
GENERATION_METHOD = (
    "authored by an AI assistant (Claude) in this project from a written specification; "
    "no real complaint text or personal data was used"
)
DEV_HOLDOUT_SHARE = 0.25  # share of development data held out (stratified) for tuning and model selection
SEED = 2026

CODES = {
    "LAB": "ministry_of_labour_and_employment",
    "DFSB": "department_of_financial_services_banking_division",
    "DFSI": "department_of_financial_services_insurance_division",
    "DOT": "department_of_telecommunications",
    "RLY": "ministry_of_railways_railway_board",
    "MHA": "ministry_of_home_affairs",
    "POST": "department_of_posts",
    "CBDT": "central_board_of_direct_taxes_income_tax",
    "CBIC": "central_board_of_indirect_taxes_and_customs",
    "DOR": "department_of_revenue",
    "AGRI": "department_of_agriculture_and_farmers_welfare",
    "HLTH": "department_of_health_and_family_welfare",
    "UIDAI": "unique_identification_authority_of_india",
    "MEITY": "ministry_of_electronics_and_information_technology",
    "MORTH": "ministry_of_road_transport_and_highways",
    "DESW": "department_of_ex_servicemen_welfare",
    "MSME": "ministry_of_micro_small_and_medium_enterprises",
    "DEFFIN": "department_of_defence_finance",
    "DEF": "department_of_defence",
    "MOHUA": "ministry_of_housing_and_urban_affairs",
    "DOUD": "department_of_urban_development",
    "HED": "department_of_higher_education",
    "SED": "department_of_school_education_and_literacy",
    "DOCA": "department_of_consumer_affairs",
    "DFPD": "department_of_food_and_public_distribution",
    "MOPNG": "ministry_of_petroleum_and_natural_gas",
    "DOPT": "department_of_personnel_and_training",
    "SSC": "staff_selection_commission",
    "RD": "department_of_rural_development",
    "PR": "ministry_of_panchayati_raj",
    "MCA": "ministry_of_corporate_affairs",
    "MSDE": "ministry_of_skill_development_and_entrepreneurship",
    "DOJ": "department_of_justice",
    "DDWS": "ministry_of_drinking_water_and_sanitation",
    "DOSJE": "department_of_social_justice_and_empowerment",
    "WCD": "ministry_of_women_and_child_development",
    "MEA": "ministry_of_external_affairs",
}
SPECIAL = {"STATE": "state_government", "OOS": "out_of_scope", "INSUF": "insufficient_information"}
LANGS = {"en", "hi", "hinglish"}
SCENARIOS = {"std", "amb", "state", "oos", "insuf", "adv"}
DIFFICULTY = {"easy", "medium", "hard"}
URGENCY = {"high", "medium", "low", "unknown"}

PII_PATTERNS = {
    "12-digit number (Aadhaar-like)": re.compile(r"(?<!\d)\d{4}\s?\d{4}\s?\d{4}(?!\d)"),
    "Indian mobile number": re.compile(r"(?<!\d)(?:\+91[\s-]?)?[6-9]\d{9}(?!\d)"),
    "e-mail address": re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+"),
}


@dataclass
class Complaint:
    id: str
    split: str
    language: str
    text: str
    intended_department: str  # ministry_key, or state_government / out_of_scope / insufficient_information
    acceptable_departments: list[str]
    scenario: str
    difficulty: str
    urgency: str
    category: str
    source: str = "synthetic"
    generation_method: str = GENERATION_METHOD


def _resolve(code: str) -> str:
    if code in CODES:
        return CODES[code]
    if code in SPECIAL:
        return SPECIAL[code]
    raise ValueError(f"unknown intent code {code!r}")


def parse_line(line: str, number: int, prefix: str) -> Complaint:
    parts = [p.strip() for p in line.split("|")]
    if len(parts) != 7:
        raise ValueError(f"line {number}: expected 7 fields, got {len(parts)}")
    intent, lang, scenario, difficulty, urgency, category, text = parts
    intent = intent.removeprefix("ADV:")
    codes = intent.split("/")
    keys = [_resolve(c) for c in codes]
    for value, allowed, name in (
        (lang, LANGS, "language"),
        (scenario, SCENARIOS, "scenario"),
        (difficulty, DIFFICULTY, "difficulty"),
        (urgency, URGENCY, "urgency"),
    ):
        if value not in allowed:
            raise ValueError(f"line {number}: bad {name} {value!r}")
    if not text or len(text) < 15:
        raise ValueError(f"line {number}: text too short")
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()[:8]
    return Complaint(
        f"{prefix}-{number:04d}-{digest}",
        "",
        lang,
        text,
        keys[0],
        keys,
        scenario,
        difficulty,
        urgency,
        category,
    )


def read_authored(path: Path, prefix: str) -> list[Complaint]:
    out = []
    for i, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        line = raw.strip()
        if line and not line.startswith("#"):
            out.append(parse_line(line, i, prefix))
    return out


def pii_findings(records: list[Complaint]) -> list[str]:
    found = []
    for r in records:
        for name, pat in PII_PATTERNS.items():
            if pat.search(r.text):
                found.append(f"{r.id}: {name}")
    return found


def split_dev(records: list[Complaint], share: float = DEV_HOLDOUT_SHARE, seed: int = SEED) -> None:
    """Stratified by intended department: about `share` of each department goes to the tuning split."""
    rng = random.Random(seed)
    groups: dict[str, list[Complaint]] = {}
    for r in records:
        groups.setdefault(r.intended_department, []).append(r)
    for _, group in sorted(groups.items()):
        rng.shuffle(group)
        n_dev = max(1, round(len(group) * share)) if len(group) >= 3 else 0
        for i, r in enumerate(group):
            r.split = "dev" if i < n_dev else "train"


def check_isolation(dev: list[Complaint], ev: list[Complaint]) -> list[str]:
    """No evaluation text may repeat a development text (after normalising case and spacing)."""
    norm = lambda t: re.sub(r"\s+", " ", t.lower()).strip()  # noqa: E731
    dev_texts = {norm(r.text) for r in dev}
    dups = [r.id for r in ev if norm(r.text) in dev_texts]
    seen, inner = set(), []
    for r in dev + ev:
        if norm(r.text) in seen:
            inner.append(r.id)
        seen.add(norm(r.text))
    return [f"evaluation text repeats development text: {i}" for i in dups] + [
        f"duplicate text: {i}" for i in inner if i not in dups
    ]


def write_outputs(dev: list[Complaint], ev: list[Complaint], out: Path = DIR) -> None:
    out.mkdir(parents=True, exist_ok=True)
    with (out / "dev.jsonl").open("w", encoding="utf-8", newline="\n") as f:
        for r in dev:
            f.write(json.dumps(asdict(r), ensure_ascii=False) + "\n")
    with (out / "eval_texts.jsonl").open("w", encoding="utf-8", newline="\n") as f:
        for r in ev:
            f.write(
                json.dumps(
                    {
                        "id": r.id,
                        "language_hint": r.language,
                        "text": r.text,
                        "source": r.source,
                        "generation_method": r.generation_method,
                    },
                    ensure_ascii=False,
                )
                + "\n"
            )
    label_cols = [
        "id",
        "text",
        "department",
        "acceptable_alternatives",
        "category",
        "language",
        "ambiguity",
        "urgency",
        "sufficient_for_routing",
        "state_matter",
        "notes",
        "labelled_by",
        "labelled_on",
    ]
    # utf-8-sig so that Excel shows Devanagari correctly when the owner opens the sheet
    with (out / "eval_owner_labels.csv").open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(label_cols)
        for r in ev:
            w.writerow([r.id, r.text] + [""] * (len(label_cols) - 2))
    with (out / "eval_generation_intent.csv").open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f, lineterminator="\n")
        w.writerow(
            [
                "id",
                "intended_department",
                "acceptable_departments",
                "scenario",
                "difficulty",
                "urgency",
                "category",
                "language",
            ]
        )
        for r in ev:
            w.writerow(
                [
                    r.id,
                    r.intended_department,
                    ";".join(r.acceptable_departments),
                    r.scenario,
                    r.difficulty,
                    r.urgency,
                    r.category,
                    r.language,
                ]
            )


def main() -> int:
    dev = read_authored(AUTHORED["dev"], "dev")
    ev = read_authored(AUTHORED["eval"], "eval")
    split_dev(dev)
    problems = pii_findings(dev + ev) + check_isolation(dev, ev)
    if problems:
        print(f"Corpus not written: {len(problems)} problem(s).", file=sys.stderr)
        for p in problems:
            print(f"  - {p}", file=sys.stderr)
        return 1
    labels = DIR / "eval_owner_labels.csv"
    if labels.exists() and any(
        row.get("department") for row in csv.DictReader(labels.open(encoding="utf-8-sig"))
    ):
        print(
            "eval_owner_labels.csv already contains owner labels; refusing to overwrite it.", file=sys.stderr
        )
        return 1
    write_outputs(dev, ev)
    n_train = sum(r.split == "train" for r in dev)
    print(f"dev.jsonl: {len(dev)} ({n_train} train, {len(dev) - n_train} dev); eval_texts.jsonl: {len(ev)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
