"""Tune abstention thresholds (router) and C + probability threshold (baseline) on development data only.

Usage (PowerShell, project root, venv active):
    python -m src.routing.tune                    # lexical router + baseline
    python -m src.routing.tune --methods lexical,hybrid

Data: data/complaints/dev.jsonl only. The evaluation set is never opened.
  - Router thresholds: all 361 development complaints (retrieval is not trained, so none of them is training
    data for it; three thresholds are fitted).
  - Baseline: trained on the 281 `train` rows; C and its threshold chosen on the 80 `dev` rows.
Objective (utility per complaint, averaged), stated in docs/routing.md:
  standard: correct route 1, abstain 0.3, wrong 0;  ambiguous: acceptable route or abstain 1, wrong 0;
  state / out-of-scope / too vague: abstain 1, any route 0.  Prompt-injection items count as standard.
Writes src/routing/thresholds.json.
"""

from __future__ import annotations

import argparse
import itertools
import json
import sys
from datetime import date

import numpy as np

from src.knowledge.retrieve import load_kb, make_retriever
from src.routing.baseline import SPECIAL_TO_REASON, Baseline, load_dev
from src.routing.router import THRESHOLDS_PATH, decide_offline
from src.routing.rules import state_matter_hits

SPECIAL = set(SPECIAL_TO_REASON)


def utility(item: dict, routed: str | None) -> float:
    acceptable = set(item["acceptable_departments"])
    if item["intended_department"] in SPECIAL:
        return 1.0 if routed is None else 0.0
    if item["scenario"] == "amb":
        return 1.0 if routed is None or routed in acceptable else 0.0
    if routed is None:
        return 0.3
    return 1.0 if routed in acceptable else 0.0


def tune_router(items: list[dict], method: str) -> dict:
    records = load_kb()
    retriever = make_retriever(method, records)
    pre = [
        (it, retriever.search(it["text"], 5), state_matter_hits(it["text"]), len(it["text"].split()))
        for it in items
    ]
    tops = np.array([c[0].score for _, c, _, _ in pre])
    grid_top = np.unique(np.quantile(tops, np.linspace(0, 0.5, 21)))
    grid_margin = [0.0, 0.02, 0.04, 0.06, 0.08, 0.1, 0.13, 0.16, 0.2, 0.25, 0.3]
    grid_state = list(np.unique(np.quantile(tops, np.linspace(0, 1, 11)))) + [float("inf")]
    best = None
    for t_top, t_margin, t_state in itertools.product(grid_top, grid_margin, grid_state):
        t = {
            "min_top_score": float(t_top),
            "min_relative_margin": t_margin,
            "state_override_score": float(t_state),
        }
        u = np.mean([utility(it, decide_offline(c, s, n, t)[1]) for it, c, s, n in pre])
        # ties: prefer fewer abstentions (lower thresholds) for a usable tool
        key = (round(u, 6), -t_top, -t_margin, -min(t_state, 9.0))
        if best is None or key > best[0]:
            best = (key, t, u)
    _, t, u = best
    decisions = [decide_offline(c, s, n, t)[1] for it, c, s, n in pre]
    std = [(it, d) for it, d in zip(items, decisions, strict=True) if it["scenario"] in {"std", "adv"}]
    return {
        "thresholds": t,
        "utility": round(float(u), 4),
        "n_items": len(items),
        "abstention_rate": round(float(np.mean([d is None for d in decisions])), 4),
        "standard_top1_when_routed": round(
            float(np.mean([d in it["acceptable_departments"] for it, d in std if d is not None]) or 0.0), 4
        ),
        "data": "data/complaints/dev.jsonl (train + dev splits; synthetic)",
    }


def tune_baseline(items: list[dict]) -> dict:
    train = [it for it in items if it["split"] == "train"]
    dev = [it for it in items if it["split"] == "dev"]
    best = None
    for c in (1.0, 10.0, 100.0):
        model = Baseline(c=c).fit([it["text"] for it in train], [it["intended_department"] for it in train])
        ranked = [model.ranked(it["text"]) for it in dev]
        for thr in np.linspace(0.0, 0.5, 26):
            u = np.mean(
                [
                    utility(it, None if (r[0][0] in SPECIAL or r[0][1] < thr) else r[0][0])
                    for it, r in zip(dev, ranked, strict=True)
                ]
            )
            key = (round(u, 6), -thr, -c)
            if best is None or key > best[0]:
                best = (key, c, float(thr), u)
    _, c, thr, u = best
    return {
        "C": c,
        "threshold": round(thr, 4),
        "utility_on_dev_split": round(float(u), 4),
        "n_train": len(train),
        "n_dev": len(dev),
    }


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Tune routing thresholds on development data only")
    p.add_argument("--methods", default="lexical")
    args = p.parse_args(argv)
    items = load_dev()
    out = json.loads(THRESHOLDS_PATH.read_text(encoding="utf-8")) if THRESHOLDS_PATH.exists() else {}
    for method in args.methods.split(","):
        out[method] = tune_router(items, method)
        print(method, json.dumps(out[method]))
    out["baseline"] = tune_baseline(items)
    print("baseline", json.dumps(out["baseline"]))
    out["_meta"] = {"tuned_on": str(date.today()), "objective": "mean utility, see module docstring"}
    THRESHOLDS_PATH.write_text(json.dumps(out, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote {THRESHOLDS_PATH}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
