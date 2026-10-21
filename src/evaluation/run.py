"""Phase 11 evaluation of the router (and baseline) and of the drafter, on the isolated evaluation set.

Usage (PowerShell, project root, venv active):
    python -m src.evaluation.run                                   # proxy labels (generation intent)
    python -m src.evaluation.run --labels data/complaints/eval_owner_labels.csv   # owner labels (final)
    python -m src.evaluation.run --systems offline,baseline        # skip the hybrid router (no model download)

Writes docs/evaluation_results.md and data/processed/evaluation/*.csv (predictions by id, confusion matrices,
drafter rubric). Complaint text is not copied into the CSVs.

Labels. Until the owner has labelled data/complaints/eval_owner_labels.csv, the reference is the
*generation intent* (what the author meant when writing each synthetic complaint). Results computed that
way are marked PROXY everywhere: they are not an independent evaluation. With --labels, the owner's labels
are used and agreement between owner labels and generation intent is reported.

Isolation. Nothing here changes any model or threshold. The router thresholds and the baseline were
fixed on development data before this script was first run (src/routing/thresholds.json).
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import random
import sys
from collections import Counter, defaultdict
from datetime import date
from pathlib import Path

import pandas as pd

from src.config import PROJECT_ROOT
from src.drafting.drafter import DraftRequest, build_draft
from src.routing.baseline import Baseline, load_dev
from src.routing.router import THRESHOLDS_PATH, Router, load_thresholds
from src.routing.settings import RouterSettings, load_router_settings

EVAL_TEXTS = PROJECT_ROOT / "data" / "complaints" / "eval_texts.jsonl"
INTENT = PROJECT_ROOT / "data" / "complaints" / "eval_generation_intent.csv"
OUT_DIR = PROJECT_ROOT / "data" / "processed" / "evaluation"
RESULTS_MD = PROJECT_ROOT / "docs" / "evaluation_results.md"
SPECIAL = {"state_government", "out_of_scope", "insufficient_information"}
DRAFT_SAMPLE = 50
SEED = 2026


def wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    if n == 0:
        return (math.nan, math.nan)
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return (max(0.0, c - h), min(1.0, c + h))


def load_items(labels_path: Path | None) -> tuple[list[dict], str]:
    texts = {
        json.loads(line)["id"]: json.loads(line)
        for line in EVAL_TEXTS.read_text(encoding="utf-8").splitlines()
    }
    intent = {r["id"]: r for r in csv.DictReader(INTENT.open(encoding="utf-8"))}
    items = []
    if labels_path:
        rows = list(csv.DictReader(labels_path.open(encoding="utf-8-sig")))
        missing = [
            r["id"]
            for r in rows
            if not (r.get("department") and r.get("labelled_by") and r.get("labelled_on"))
        ]
        if missing:
            raise SystemExit(
                f"{len(missing)} rows lack department/labelled_by/labelled_on, e.g. {missing[:3]}"
            )
        for r in rows:
            alts = [a.strip() for a in (r.get("acceptable_alternatives") or "").split(";") if a.strip()]
            items.append(
                {
                    **texts[r["id"]],
                    "label": r["department"].strip(),
                    "acceptable": [r["department"].strip(), *alts],
                    "language": r.get("language") or texts[r["id"]]["language_hint"],
                    "scenario": intent[r["id"]]["scenario"],
                    "intent": intent[r["id"]]["intended_department"],
                    "labelled_by": r["labelled_by"].strip(),
                }
            )
        return items, "owner"
    for i, t in texts.items():
        r = intent[i]
        items.append(
            {
                **t,
                "label": r["intended_department"],
                "acceptable": r["acceptable_departments"].split(";"),
                "language": r["language"],
                "scenario": r["scenario"],
                "intent": r["intended_department"],
            }
        )
    return items, "proxy"


def predict(items: list[dict], systems: list[str]) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {}
    base = RouterSettings()
    routers = {"offline": Router(settings=base, selector=None)}
    if "hybrid" in systems:
        hs = RouterSettings(retrieval_method="hybrid")
        routers["hybrid"] = Router(settings=hs, selector=None, thresholds=load_thresholds("hybrid"))
    llm_settings = load_router_settings()
    if "llm" in systems and llm_settings.llm_provider != "none":
        routers["llm"] = Router(settings=llm_settings)
    for name, router in routers.items():
        if name not in systems:
            continue
        preds = []
        for it in items:
            r = router.route(it["text"])
            preds.append(
                {
                    "id": it["id"],
                    "pred": r.department_key,
                    "abstain": r.abstain,
                    "abstain_reason": r.abstain_reason,
                    "top3": [c.ministry_key for c in r.candidates[:3]],
                    "confidence": r.confidence,
                    "method": r.method,
                    "injection": r.injection_detected,
                    "language_detected": r.language_detected,
                }
            )
        out[name] = preds
    if "baseline" in systems:
        dev = load_dev()
        train = [d for d in dev if d["split"] == "train"]
        cfg = json.loads(THRESHOLDS_PATH.read_text(encoding="utf-8"))["baseline"]
        model = Baseline(c=cfg["C"]).fit(
            [d["text"] for d in train], [d["intended_department"] for d in train]
        )
        model.threshold = cfg["threshold"]
        names = routers["offline"].names
        preds = []
        for it in items:
            r = model.route(it["text"], names)
            ranked = [k for k, _ in model.ranked(it["text"]) if k in names][:3]
            preds.append(
                {
                    "id": it["id"],
                    "pred": r.department_key,
                    "abstain": r.abstain,
                    "abstain_reason": r.abstain_reason,
                    "top3": ranked,
                    "confidence": r.confidence,
                    "method": r.method,
                    "injection": False,
                    "language_detected": "",
                }
            )
        out["baseline"] = preds
    return out


def metrics(items: list[dict], preds: list[dict]) -> dict:
    from sklearn.metrics import f1_score

    by_id = {p["id"]: p for p in preds}
    routable = [it for it in items if it["label"] not in SPECIAL]
    special = [it for it in items if it["label"] in SPECIAL]
    m: dict = {}
    k1 = sum(by_id[it["id"]]["pred"] in it["acceptable"] for it in routable)
    k3 = sum(any(c in it["acceptable"] for c in by_id[it["id"]]["top3"]) for it in routable)
    answered = [it for it in routable if not by_id[it["id"]]["abstain"]]
    ka = sum(by_id[it["id"]]["pred"] in it["acceptable"] for it in answered)
    n = len(routable)
    m["routable_n"] = n
    m["top1"], m["top1_ci"] = k1 / n, wilson(k1, n)
    m["top3"], m["top3_ci"] = k3 / n, wilson(k3, n)
    m["abstention_rate_routable"] = 1 - len(answered) / n
    m["accuracy_when_answered"] = ka / len(answered) if answered else math.nan
    m["accuracy_when_answered_ci"] = wilson(ka, len(answered))
    y_true = [it["label"] for it in routable]
    y_pred = [
        it["label"] if by_id[it["id"]]["pred"] in it["acceptable"] else (by_id[it["id"]]["pred"] or "ABSTAIN")
        for it in routable
    ]
    m["macro_f1"] = f1_score(y_true, y_pred, labels=sorted(set(y_true)), average="macro", zero_division=0)
    m["special_n"] = len(special)
    m["special_abstained"] = sum(by_id[it["id"]]["abstain"] for it in special)
    m["special_by_type"] = {
        lab: (
            sum(by_id[it["id"]]["abstain"] for it in special if it["label"] == lab),
            sum(it["label"] == lab for it in special),
        )
        for lab in sorted(SPECIAL)
    }
    m["overall_abstention_rate"] = sum(p["abstain"] for p in preds) / len(preds)
    langs = defaultdict(lambda: [0, 0])
    for it in routable:
        lang = it["language"]
        langs[lang][0] += by_id[it["id"]]["pred"] in it["acceptable"]
        langs[lang][1] += 1
    m["by_language"] = {lang: (k, nn, k / nn, wilson(k, nn)) for lang, (k, nn) in sorted(langs.items())}
    scen = defaultdict(lambda: [0, 0])
    for it in routable:
        scen[it["scenario"]][0] += by_id[it["id"]]["pred"] in it["acceptable"]
        scen[it["scenario"]][1] += 1
    m["by_scenario"] = {s: (k, nn) for s, (k, nn) in sorted(scen.items())}
    dept = defaultdict(lambda: [0, 0])
    for it in routable:
        dept[it["label"]][0] += by_id[it["id"]]["pred"] in it["acceptable"]
        dept[it["label"]][1] += 1
    m["by_department"] = dict(dept)
    conf = Counter(
        (it["label"], by_id[it["id"]]["pred"])
        for it in routable
        if by_id[it["id"]]["pred"] not in it["acceptable"] and by_id[it["id"]]["pred"] is not None
    )
    m["confusions"] = conf.most_common(10)
    adv = [it for it in items if it["scenario"] == "adv"]
    m["adv_correct"] = (sum(by_id[it["id"]]["pred"] in it["acceptable"] for it in adv), len(adv))
    m["adv_flagged"] = sum(by_id[it["id"]]["injection"] for it in adv)
    m["confusion_matrix"] = pd.crosstab(pd.Series(y_true, name="label"), pd.Series(y_pred, name="predicted"))
    return m


def evaluate_drafter(items: list[dict], offline_preds: list[dict], names: dict[str, str]) -> pd.DataFrame:
    """Automated rubric on a stratified sample of drafts. Clarity needs a human: a sheet is written for it."""
    rng = random.Random(SEED)
    by_lang = defaultdict(list)
    for it in items:
        by_lang[it["language"]].append(it)
    sample = []
    langs = sorted(by_lang)
    quota = {
        lang: DRAFT_SAMPLE // len(langs) + (1 if i < DRAFT_SAMPLE % len(langs) else 0)
        for i, lang in enumerate(langs)
    }
    for lang in langs:
        group = sorted(by_lang[lang], key=lambda x: x["id"])
        rng.shuffle(group)
        sample += group[: quota[lang]]
    pred = {p["id"]: p for p in offline_preds}
    rows = []
    for it in sample[:DRAFT_SAMPLE]:
        p = pred[it["id"]]
        lang = "hi" if it["language"] == "hi" else "en"
        d = build_draft(
            DraftRequest(
                complaint=it["text"],
                language=lang,
                department_key=p["pred"],
                department_name=names.get(p["pred"]),
            ),
            with_statistics=False,
        )
        sections_ok = len(d.sections) == 7 and bool(d.subject) and bool(d.addressed_to)
        verbatim = d.sections[0].body.split() == it["text"].split()
        ref_section = d.sections[2]
        has_ref_in_text = not ref_section.placeholder
        rows.append(
            {
                "id": it["id"],
                "language": it["language"],
                "label": it["label"],
                "routed_to": p["pred"] or "",
                "completeness_sections": sections_ok,
                "faithfulness_verbatim_complaint": verbatim,
                "unsupported_factual_tokens": len(d.factcheck.unsupported_tokens),
                "unsupported_claim_words": len(d.factcheck.unsupported_claim_words),
                "placeholders": len(d.placeholders),
                "missing_info_handled": ref_section.placeholder or has_ref_in_text,
                "routing_correct_in_draft": (p["pred"] in it["acceptable"])
                if it["label"] not in SPECIAL
                else p["pred"] is None,
                "source": "synthetic",
            }
        )
    return pd.DataFrame(rows)


def _pct(x: float) -> str:
    return "n/a" if x != x else f"{100 * x:.1f}%"


def _ci(ci: tuple[float, float]) -> str:
    return f"[{100 * ci[0]:.1f}, {100 * ci[1]:.1f}]"


def write_report(items, mode, results, drafts, names, systems_not_run, human=None) -> None:
    banner = (
        (
            "**PROXY RESULTS: the reference labels are the author's generation intent, not the owner's "
            "independent labels.** They are not an independent evaluation; the final evaluation is pending "
            "owner labelling (docs/labelling_guide.md)."
        )
        if mode == "proxy"
        else (
            "Reference labels: data/complaints/eval_owner_labels.csv, labelled by: "
            f"**{', '.join(sorted({it['labelled_by'] for it in items}))}**. "
            "Agreement of these labels with the author's generation intent: "
            f"{sum(it['label'] == it['intent'] for it in items)} / {len(items)} complaints."
        )
    )
    n_rout = results[next(iter(results))]["routable_n"]
    lines = [
        "# Evaluation results (generated)",
        "",
        f"Generated by `python -m src.evaluation.run` on {date.today()}. Method and limitations: "
        "[evaluation.md](evaluation.md). All data are **synthetic**; nothing here measures performance on real "
        "citizen complaints.",
        "",
        f"> {banner}",
        "",
        f"Evaluation set: {len(items)} complaints ({n_rout} routable to one of 37 departments, "
        f"{len(items) - n_rout} State / out-of-scope / too-vague). "
        f"Languages: {dict(Counter(it['language'] for it in items))}.",
        "",
        "## Router comparison",
        "",
        "| System | Top-1 (95% CI) | Top-3 | Macro-F1 | Abstains on routable | Accuracy when answering | "
        "Non-routable correctly abstained | Injection items routed correctly |",
        "|---|---|---|---|---|---|---|---|",
    ]
    labels = {
        "offline": "Router, retrieval + rules (lexical, default)",
        "hybrid": "Router, retrieval + rules (hybrid)",
        "baseline": "Baseline, TF-IDF + logistic regression",
        "llm": "Router, retrieval + LLM",
    }
    for name, m in results.items():
        lines.append(
            f"| {labels[name]} | {_pct(m['top1'])} {_ci(m['top1_ci'])} | {_pct(m['top3'])} | {m['macro_f1']:.3f} | "
            f"{_pct(m['abstention_rate_routable'])} | {_pct(m['accuracy_when_answered'])} | "
            f"{m['special_abstained']} / {m['special_n']} | {m['adv_correct'][0]} / {m['adv_correct'][1]} |"
        )
    for s in systems_not_run:
        lines.append(f"| {labels[s]} | not run: {systems_not_run[s]} | | | | | | |")
    lines += [
        "",
        "Top-1 counts an abstention on a routable complaint as incorrect; top-3 uses the system's three "
        "best candidates whether or not it abstained.",
        "",
    ]
    for name, m in results.items():
        lines += [
            f"## {labels[name]}",
            "",
            "| Language | Correct | n | Top-1 | 95% CI |",
            "|---|---|---|---|---|",
        ]
        lines += [
            f"| {lang} | {k} | {nn} | {_pct(p)} | {_ci(ci)} |"
            for lang, (k, nn, p, ci) in m["by_language"].items()
        ]
        lines += ["", "| Scenario (routable) | Correct / n |", "|---|---|"]
        lines += [f"| {s} | {k} / {nn} |" for s, (k, nn) in m["by_scenario"].items()]
        lines += [
            "",
            "Non-routable complaints abstained: "
            + ", ".join(f"{lab.replace('_', ' ')} {k}/{nn}" for lab, (k, nn) in m["special_by_type"].items())
            + ".",
            f"Prompt-injection items flagged: {m['adv_flagged']} / {m['adv_correct'][1]}.",
            "",
            "Most common confusions (reference -> predicted):",
            "",
        ]
        lines += [f"- {names.get(a, a)} -> {names.get(b, b)}: {c}" for (a, b), c in m["confusions"]] or [
            "- none"
        ]
        weak = sorted(((k / nn, d, k, nn) for d, (k, nn) in m["by_department"].items()))[:6]
        lines += [
            "",
            "Lowest per-department results (6-9 routable examples each, so anecdotal): "
            + "; ".join(f"{names.get(d, d)} {k}/{nn}" for _, d, k, nn in weak)
            + ".",
            "",
        ]
    if drafts is not None:
        d = drafts
        viol = int((d["unsupported_factual_tokens"] > 0).sum() + (d["unsupported_claim_words"] > 0).sum())
        lines += [
            "## Drafter (automated rubric, 50 synthetic drafts)",
            "",
            "| Criterion | Result |",
            "|---|---|",
            f"| Completeness: all 7 sections, subject and addressee present | {int(d['completeness_sections'].sum())} / {len(d)} |",
            f"| Faithfulness: complaint reproduced verbatim | {int(d['faithfulness_verbatim_complaint'].sum())} / {len(d)} |",
            f"| Unsupported factual tokens (high severity) | {int(d['unsupported_factual_tokens'].sum())} tokens in "
            f"{int((d['unsupported_factual_tokens'] > 0).sum())} drafts |",
            f"| Unsupported claim words (high severity) | {int(d['unsupported_claim_words'].sum())} in "
            f"{int((d['unsupported_claim_words'] > 0).sum())} drafts |",
            f"| Missing-information handling: reference section is a value from the text or a placeholder | "
            f"{int(d['missing_info_handled'].sum())} / {len(d)} |",
            f"| Routing correctness of the addressee (vs reference label) | {int(d['routing_correct_in_draft'].sum())} / {len(d)} |",
            f"| Mean placeholders per draft | {d['placeholders'].mean():.1f} |",
            "| Clarity | **pending human rubric** (data/processed/evaluation/drafter_human_rubric.csv) |"
            if human is None
            else f"| Rubric (1-5) by {human['reviewer'].dropna().iloc[0]} | clarity {human['clarity_1to5'].mean():.2f}, "
            f"faithfulness {human['faithfulness_1to5'].mean():.2f}, completeness {human['completeness_1to5'].mean():.2f}, "
            f"missing-information handling {human['missing_info_1to5'].mean():.2f} |",
            "",
            f"Factuality violations found by the screen: {viol}. Real-source drafts: none (no real data used).",
            "",
        ]
    RESULTS_MD.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Evaluate router, baseline and drafter on the evaluation set")
    p.add_argument("--labels", type=Path, default=None, help="owner-labelled CSV (final evaluation)")
    p.add_argument("--systems", default="offline,hybrid,baseline,llm")
    p.add_argument(
        "--limit", type=int, default=None, help="evaluate only the first N complaints (smoke test)"
    )
    args = p.parse_args(argv)
    systems = args.systems.split(",")
    items, mode = load_items(args.labels)
    if args.limit:
        items = items[: args.limit]
    not_run = {}
    if "hybrid" in systems:
        try:
            import sentence_transformers  # noqa: F401
        except ImportError:
            systems.remove("hybrid")
            not_run["hybrid"] = "requirements-embeddings.txt not installed"
    if "llm" in systems and load_router_settings().llm_provider == "none":
        systems.remove("llm")
        not_run["llm"] = "no LLM configured (LLM_PROVIDER=none); no API key in this environment"
    preds = predict(items, systems)
    results = {name: metrics(items, pr) for name, pr in preds.items()}
    names = Router(settings=RouterSettings(), selector=None).names
    drafts = evaluate_drafter(items, preds["offline"], names) if "offline" in preds else None
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    for name, pr in preds.items():
        pd.DataFrame(pr).assign(label=[it["label"] for it in items], mode=mode).to_csv(
            OUT_DIR / f"predictions_{name}.csv", index=False
        )
        results[name]["confusion_matrix"].to_csv(OUT_DIR / f"confusion_{name}.csv")
    human = None
    rubric_path = OUT_DIR / "drafter_human_rubric.csv"
    if drafts is not None:
        drafts.to_csv(OUT_DIR / "drafter_rubric_auto.csv", index=False)
        existing = pd.read_csv(rubric_path) if rubric_path.exists() else None
        if existing is not None and existing["reviewer"].notna().any():
            human = existing  # never overwrite a filled rubric
        else:
            drafts[["id", "language"]].assign(
                clarity_1to5="",
                faithfulness_1to5="",
                completeness_1to5="",
                missing_info_1to5="",
                notes="",
                reviewer="",
            ).to_csv(rubric_path, index=False)
    write_report(items, mode, results, drafts, names, not_run, human)
    for name, m in results.items():
        print(
            f"{name}: top1 {m['top1']:.3f} top3 {m['top3']:.3f} macroF1 {m['macro_f1']:.3f} "
            f"abstain(routable) {m['abstention_rate_routable']:.3f} special {m['special_abstained']}/{m['special_n']}"
        )
    print(
        f"mode={mode}; wrote {RESULTS_MD.relative_to(PROJECT_ROOT)} and {OUT_DIR.relative_to(PROJECT_ROOT)}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
