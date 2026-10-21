import math
from types import SimpleNamespace

import numpy as np
import pytest

from src.analysis.stats import benjamini_hochberg, longest_run, mann_kendall, run_ending_at, spearman


def test_mann_kendall_detects_a_clear_trend_and_estimates_its_slope():
    rng = np.random.default_rng(7)
    x = 1000 + 25 * np.arange(42) + rng.normal(0, 40, 42)
    r = mann_kendall(x)
    assert r["n"] == 42 and r["tau"] > 0.8 and r["p_modified"] < 0.001
    assert 20 < r["sen_slope"] < 30  # true slope 25 per period


def test_mann_kendall_on_pure_noise_is_not_significant():
    rng = np.random.default_rng(11)
    r = mann_kendall(rng.normal(1000, 50, 42))
    assert r["p_modified"] > 0.05


def test_mann_kendall_rejects_gaps_and_short_series():
    with pytest.raises(ValueError, match="missing"):
        mann_kendall([1, 2, float("nan"), 4, 5])
    with pytest.raises(ValueError, match="at least 4"):
        mann_kendall([1, 2, 3])


def test_benjamini_hochberg_matches_hand_computed_example():
    # p sorted: .01 .02 .03 .04 .20 (m=5) -> raw p*m/i: .05 .05 .05 .05 .20 -> monotone: .05 .05 .05 .05 .20
    q = benjamini_hochberg([0.03, 0.20, 0.01, 0.04, 0.02])
    assert q == pytest.approx([0.05, 0.20, 0.05, 0.05, 0.05])


def test_benjamini_hochberg_ignores_nan():
    q = benjamini_hochberg([0.01, math.nan, 0.04])
    assert q[0] == pytest.approx(0.02) and math.isnan(q[1]) and q[2] == pytest.approx(0.04)


def test_spearman_and_runs():
    rho, _ = spearman([1, 2, 3, 4], [1, 2, 3, 4])
    assert rho == pytest.approx(1.0)
    assert math.isnan(spearman([1, 2], [2, 1])[0])
    assert longest_run([1, 2, 3, 7, 8, 10]) == 3
    assert run_ending_at([1, 2, 3, 7, 8], 8) == 2 and run_ending_at([1, 2], 5) == 0


def test_undefined_hamed_rao_p_falls_back_to_pre_whitening(monkeypatch):
    import src.analysis.stats as st

    monkeypatch.setattr(
        st.mk, "hamed_rao_modification_test", lambda x: SimpleNamespace(p=float("nan"), z=float("nan"))
    )
    noisy = (
        1000 + 25 * np.arange(20.0) + np.random.default_rng(3).normal(0, 30, 20)
    )  # non-degenerate residuals
    r = st.mann_kendall(noisy)
    assert r["p_method"] == "tfpw_yue2002" and math.isfinite(r["p_modified"])
