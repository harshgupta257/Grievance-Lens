"""Transparent baseline: TF-IDF (character + word n-grams) + logistic regression over department labels.

Trained only on the `train` split of data/complaints/dev.jsonl (synthetic). Labels include the special
outcomes state_government, out_of_scope and insufficient_information; predicting one of them is an
abstention. The evaluation set is never read here.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np

from src.config import PROJECT_ROOT
from src.routing.schemas import RouteResult

DEV_PATH = PROJECT_ROOT / "data" / "complaints" / "dev.jsonl"
SPECIAL_TO_REASON = {
    "state_government": "state_matter",
    "out_of_scope": "out_of_scope",
    "insufficient_information": "insufficient_information",
}


def load_dev(path: Path = DEV_PATH) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


class Baseline:
    def __init__(self, c: float = 10.0, seed: int = 2026):
        from sklearn.feature_extraction.text import TfidfVectorizer
        from sklearn.linear_model import LogisticRegression
        from sklearn.pipeline import FeatureUnion, Pipeline

        self.model = Pipeline(
            [
                (
                    "features",
                    FeatureUnion(
                        [
                            (
                                "char",
                                TfidfVectorizer(analyzer="char_wb", ngram_range=(2, 5), sublinear_tf=True),
                            ),
                            ("word", TfidfVectorizer(analyzer="word", ngram_range=(1, 2), sublinear_tf=True)),
                        ]
                    ),
                ),
                ("clf", LogisticRegression(C=c, max_iter=5000, class_weight="balanced", random_state=seed)),
            ]
        )
        self.threshold = 0.0

    def fit(self, texts: list[str], labels: list[str]) -> Baseline:
        self.model.fit([t.lower() for t in texts], labels)
        return self

    def ranked(self, text: str) -> list[tuple[str, float]]:
        probs = self.model.predict_proba([text.lower()])[0]
        order = np.argsort(probs)[::-1]
        classes = self.model.classes_
        return [(str(classes[i]), float(probs[i])) for i in order]

    def route(self, text: str, names: dict[str, str]) -> RouteResult:
        ranked = self.ranked(text)
        label, prob = ranked[0]
        depts = [(k, p) for k, p in ranked if k in names][:3]
        alt_keys = [k for k, _ in depts if k != label][:2]
        common = {
            "confidence": round(prob, 3),
            "confidence_source": "classifier_probability",
            "method": "baseline_tfidf_logreg",
            "alternatives": [names[k] for k in alt_keys],
            "alternative_keys": alt_keys,
        }
        if label in SPECIAL_TO_REASON:
            return RouteResult(abstain=True, abstain_reason=SPECIAL_TO_REASON[label], **common)
        if prob < self.threshold:
            return RouteResult(
                abstain=True,
                abstain_reason="low_evidence",
                **{
                    **common,
                    "alternatives": [names[k] for k, _ in depts],
                    "alternative_keys": [k for k, _ in depts],
                },
            )
        return RouteResult(department=names.get(label, label), department_key=label, **common)
