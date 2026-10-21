"""Statistical helpers: Mann-Kendall trend tests, Sen's slope, Benjamini-Hochberg, Spearman, run lengths."""

from __future__ import annotations

import math
from collections.abc import Sequence

import numpy as np
import pymannkendall as mk
from scipy import stats as sps


def mann_kendall(values: Sequence[float]) -> dict:
    """Mann-Kendall trend test on an equally spaced, gap-free series.

    H0: no monotonic trend; H1: a monotonic increasing or decreasing trend (two-sided).
    The p-value used for inference is the Hamed-Rao modification, which corrects the variance of S for
    autocorrelation (monthly administrative series are autocorrelated, which inflates the original test's
    significance). For short, strongly autocorrelated series the Hamed-Rao corrected variance can become
    negative (p undefined); the trend-free pre-whitening test (Yue et al. 2002) is then used instead and
    `p_method` says so. The original p-value is reported for comparison. Sen's slope is the median of
    pairwise slopes, in units per period (per month here): an effect size that is robust to outliers.
    """
    x = np.asarray(values, dtype=float)
    if np.isnan(x).any():
        raise ValueError("series contains missing values; pass a gap-free series")
    if len(x) < 4:
        raise ValueError("at least 4 points are needed")
    original = mk.original_test(x)
    with np.errstate(invalid="ignore"):  # a negative corrected variance is handled below, not warned about
        modified = mk.hamed_rao_modification_test(x)
    p_method = "hamed_rao"
    if not np.isfinite(modified.p):
        modified = mk.trend_free_pre_whitening_modification_test(x)
        p_method = "tfpw_yue2002"
    if not np.isfinite(modified.p):
        raise ArithmeticError("no autocorrelation-corrected Mann-Kendall p-value could be computed")
    return {
        "n": len(x),
        "s": float(original.s),
        "tau": float(original.Tau),
        "z_modified": float(modified.z),
        "p_modified": float(modified.p),
        "p_method": p_method,
        "p_original": float(original.p),
        "sen_slope": float(original.slope),
        "median_level": float(np.median(x)),
        "relative_slope_pct": float(100 * original.slope / np.mean(x)) if np.mean(x) else math.nan,
    }


def benjamini_hochberg(pvalues: Sequence[float]) -> list[float]:
    """Benjamini-Hochberg adjusted p-values (q-values), controlling the false discovery rate in one family.

    NaN p-values are passed through as NaN and do not count towards the family size.
    """
    p = np.asarray(pvalues, dtype=float)
    q = np.full(p.shape, np.nan)
    ok = ~np.isnan(p)
    m = int(ok.sum())
    if m == 0:
        return q.tolist()
    idx = np.where(ok)[0]
    order = idx[np.argsort(p[idx])]
    ranked = p[order] * m / np.arange(1, m + 1)
    ranked = np.minimum.accumulate(ranked[::-1])[::-1]  # step-up: enforce monotonicity from the largest p
    q[order] = np.minimum(ranked, 1.0)
    return q.tolist()


def spearman(a: Sequence[float], b: Sequence[float]) -> tuple[float, float]:
    """Spearman rank correlation and its p-value (NaN when fewer than 3 pairs or an input is constant)."""
    if len(a) < 3 or len(set(a)) < 2 or len(set(b)) < 2:
        return math.nan, math.nan
    res = sps.spearmanr(a, b)
    return float(res.statistic), float(res.pvalue)


def longest_run(sorted_numbers: Sequence[int]) -> int:
    """Longest run of consecutive integers (e.g. report numbers in which a ministry is listed)."""
    best = cur = 0
    prev = None
    for n in sorted_numbers:
        cur = cur + 1 if prev is not None and n == prev + 1 else 1
        best = max(best, cur)
        prev = n
    return best


def run_ending_at(sorted_numbers: Sequence[int], last: int) -> int:
    """Length of the consecutive run that ends exactly at `last` (0 if `last` is not included)."""
    s = set(sorted_numbers)
    n = 0
    while last - n in s:
        n += 1
    return n
