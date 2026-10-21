"""Scorecard rules on a small synthetic component table (no database)."""

import math

import numpy as np
import pandas as pd
import pytest

from src.analysis.scorecard import score, to_percentiles

W = {
    "grai_percentile": 0.25,
    "backlog_trend_pct": 0.25,
    "backlog_months": 0.25,
    "slow_closing_share": 0.25,
    "over_1000_share": 0.0,
}


def components():
    return pd.DataFrame(
        {
            "ministry_id": [1, 2, 3, 4, 5],
            "canonical_name": ["a", "b", "c", "d", "e"],
            "grai_group": ["A", "A", "A", "B", "B"],
            "grai_percentile": [90.0, 50.0, 10.0, 80.0, 20.0],  # higher is better
            "backlog_trend_pct": [-5.0, 0.0, 5.0, -1.0, 1.0],  # lower is better
            "backlog_months": [0.2, 0.5, np.nan, 0.3, 0.6],  # ministry 3 missing
            "slow_closing_share": [0.0, 0.5, 1.0, np.nan, np.nan],  # not applicable in group B
            "over_1000_share": [0.0, 0.0, 0.0, 0.0, 0.0],
        }
    )


def test_percentiles_are_oriented_and_computed_within_group():
    p = to_percentiles(components()).set_index("ministry_id")
    assert p.loc[1, "grai_percentile_pctl"] == 100 and p.loc[3, "grai_percentile_pctl"] == 0
    assert (
        p.loc[1, "backlog_trend_pct_pctl"] == 100 and p.loc[3, "backlog_trend_pct_pctl"] == 0
    )  # lower = better
    # group B is ranked only against group B
    assert p.loc[4, "grai_percentile_pctl"] == 100 and p.loc[5, "grai_percentile_pctl"] == 0
    assert math.isnan(p.loc[3, "backlog_months_pctl"])  # missing stays missing
    assert p.loc[[1, 2, 3], "over_1000_share_pctl"].tolist() == [50.0, 50.0, 50.0]  # constant: no information


def test_missing_components_are_renormalised_not_zero_filled():
    s = score(to_percentiles(components()), W).set_index("ministry_id")
    # ministry 3: grai 0, trend 0, slow-closing 0 -> mean of 3 available = 0 (backlog_months not counted as 0)
    assert s.loc[3, "components_used"] == 3 and s.loc[3, "score"] == pytest.approx(0.0)
    # ministry 1 is best on every available component
    assert s.loc[1, "score"] == pytest.approx(100.0) and s.loc[1, "rank_in_group"] == 1


def test_group_b_scores_use_only_applicable_components():
    s = score(to_percentiles(components()), W).set_index("ministry_id")
    assert s.loc[4, "components_used"] == 3 and s.loc[4, "score"] == pytest.approx(100.0)


def test_too_few_components_gives_no_score():
    c = components()
    c.loc[c["ministry_id"] == 3, ["grai_percentile", "backlog_trend_pct"]] = np.nan  # only slow-closing left
    s = score(to_percentiles(c), W).set_index("ministry_id")
    assert math.isnan(s.loc[3, "score"]) and math.isnan(s.loc[3, "rank_in_group"])
