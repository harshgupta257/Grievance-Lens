"""Evaluation metrics on a tiny controlled example."""

import math

import pytest

from src.evaluation.run import metrics, wilson


def test_wilson_interval():
    lo, hi = wilson(62, 100)
    assert lo == pytest.approx(0.522, abs=0.002) and hi == pytest.approx(0.709, abs=0.002)
    assert all(math.isnan(x) for x in wilson(0, 0))


def _it(i, label, lang="en", scenario="std", acceptable=None):
    return {
        "id": str(i),
        "label": label,
        "acceptable": acceptable or [label],
        "language": lang,
        "scenario": scenario,
    }


def _p(i, pred, top3, abstain=False, injection=False):
    return {"id": str(i), "pred": pred, "top3": top3, "abstain": abstain, "injection": injection}


def test_metrics_count_abstention_as_wrong_and_handle_alternatives_and_specials():
    items = [
        _it(1, "a"),
        _it(2, "a", "hi"),
        _it(3, "b", "hinglish"),
        _it(4, "c", scenario="amb", acceptable=["c", "d"]),
        _it(5, "state_government", scenario="state"),
        _it(6, "out_of_scope", scenario="oos"),
        _it(7, "b", scenario="adv"),
    ]
    preds = [
        _p(1, "a", ["a", "b", "c"]),
        _p(2, None, ["a", "b", "c"], abstain=True),
        _p(3, "a", ["a", "c", "d"]),
        _p(4, "d", ["d", "c", "a"]),
        _p(5, None, [], abstain=True),
        _p(6, "a", ["a"]),
        _p(7, "b", ["b"], injection=True),
    ]
    m = metrics(items, preds)
    assert m["routable_n"] == 5
    assert m["top1"] == pytest.approx(3 / 5)  # items 1, 4 (alternative), 7
    assert m["top3"] == pytest.approx(4 / 5)  # item 3 misses: b not in top 3
    assert m["abstention_rate_routable"] == pytest.approx(1 / 5)
    assert m["accuracy_when_answered"] == pytest.approx(3 / 4)
    assert m["special_abstained"] == 1 and m["special_n"] == 2
    assert m["adv_correct"] == (1, 1) and m["adv_flagged"] == 1
    assert m["by_language"]["hi"][:2] == (0, 1)
