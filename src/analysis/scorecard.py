"""Ministry grievance scorecard (Phase 5): a transparent, configurable index built by this project.

It is NOT an official government rating. It combines published indicators into one within-group
percentile score, states every direction, scale and weight, never fills missing data with zero, and
tests how much the result depends on the weights. See docs/scorecard.md.

Usage (PowerShell, project root, venv active, database loaded):
    python -m src.analysis.scorecard

Components (one value per ministry; config in src/analysis/config.py):
  grai_percentile     mean within-group GRAI percentile over #46-#53 (one method era)   higher = better
  backlog_trend_pct   relative Sen slope of month-end pending, T6 panel #26-#45          lower  = better
  backlog_months      mean pending / mean monthly receipts, T6 panel #29-#45 (size-neutral) lower = better
  slow_closing_share  share of era reports on the high average-closing-time list          lower  = better
                      (Group A only: the 2026 list includes only ministries with > 100 disposals)
  over_1000_share     share of era reports on the >1000-pending list (size-biased; weight 0 by default)
Each component becomes a percentile among the ministries of the same GRAI group (0 = worst, 100 = best).
The score is the weighted mean of the applicable, available component percentiles, renormalising weights.
"""

from __future__ import annotations

import sys

import numpy as np
import pandas as pd

from src.analysis import config as cfg
from src.analysis.stats import mann_kendall, spearman

COMPONENTS = (
    "grai_percentile",
    "backlog_trend_pct",
    "backlog_months",
    "slow_closing_share",
    "over_1000_share",
)
HIGHER_IS_BETTER = {"grai_percentile"}


def build_components(grai: pd.DataFrame, flows: pd.DataFrame, closing: pd.DataFrame, pendency: pd.DataFrame):
    lo, hi = cfg.SCORECARD_GRAI_REPORTS
    era_reports = hi - lo + 1
    g = (
        grai[(grai["report_number"] >= lo) & (grai["report_number"] <= hi)]
        .dropna(subset=["grai_rank"])
        .copy()
    )
    g["percentile"] = np.where(
        g["group_size"] > 1, 100 * (g["group_size"] - g["grai_rank"]) / (g["group_size"] - 1), np.nan
    )
    rows = []
    for (mid, name), m in g.groupby(["ministry_id", "canonical_name"]):
        group = m["grai_group"].mode().iloc[0]  # most frequent group in the era
        in_group = m[m["grai_group"] == group]
        rows.append(
            {
                "ministry_id": mid,
                "canonical_name": name,
                "grai_group": group,
                "months_ranked": len(in_group),
                "switched_group": m["grai_group"].nunique() > 1,
                "grai_percentile": in_group["percentile"].mean()
                if len(in_group) >= cfg.SCORECARD_MIN_MONTHS_RANKED
                else np.nan,
            }
        )
    comp = pd.DataFrame(rows).set_index("ministry_id")

    # backlog trend over the whole panel (pending is a stock, unaffected by #28's two-month flow window)
    p_lo, p_hi = cfg.PANEL_REPORTS
    f_lo, f_hi = cfg.PANEL_FLOW_REPORTS
    trend, months = {}, {}
    for mid, f in flows.groupby("ministry_id"):
        f = f.sort_values("report_number")
        nums = f["report_number"].tolist()
        if len(nums) >= cfg.MIN_TREND_POINTS_MINISTRY and nums == list(range(nums[0], nums[0] + len(nums))):
            trend[mid] = mann_kendall(f["pending"])["relative_slope_pct"]
        w = f[(f["report_number"] >= f_lo) & (f["report_number"] <= f_hi)]
        if len(w) >= cfg.MIN_POINTS_DEMAND_BACKLOG and w["receipts"].mean() > 0:
            months[mid] = w["pending"].mean() / w["receipts"].mean()
    comp["backlog_trend_pct"] = pd.Series(trend)
    comp["backlog_months"] = pd.Series(months)

    ct = closing[(closing["list"] == "high") & closing["report_number"].between(lo, hi)]
    reports_with_list = ct["report_number"].nunique()
    slow = ct.groupby("ministry_id")["report_number"].nunique() / reports_with_list
    comp["slow_closing_share"] = slow.reindex(comp.index).fillna(
        0.0
    )  # not listed = observed "not among the 10"
    comp.loc[comp["grai_group"] != "A", "slow_closing_share"] = (
        np.nan
    )  # not applicable (> 100 disposals rule)

    pend = pendency[pendency["report_number"].between(lo, hi)]
    over = pend.groupby("ministry_id")["report_number"].nunique() / era_reports
    comp["over_1000_share"] = over.reindex(comp.index).fillna(0.0)
    return comp.reset_index()


def to_percentiles(comp: pd.DataFrame) -> pd.DataFrame:
    """Within-group percentile per component, oriented so that 100 is the best value in the group."""
    out = comp.copy()
    for c in COMPONENTS:

        def pct(s: pd.Series, c=c) -> pd.Series:
            valid = s.dropna()
            if len(valid) < 2:
                return pd.Series(np.nan, index=s.index)
            r = valid.rank(method="average", ascending=c in HIGHER_IS_BETTER)  # rank 1 = worst
            return (100 * (r - 1) / (len(valid) - 1)).reindex(s.index)

        out[f"{c}_pctl"] = out.groupby("grai_group")[c].transform(pct)
    return out


def score(pctl: pd.DataFrame, weights: dict[str, float]) -> pd.DataFrame:
    """Weighted mean of available component percentiles (weights renormalised); NaN below minimum coverage."""
    used = [c for c in COMPONENTS if weights.get(c, 0) > 0]
    vals = pctl[[f"{c}_pctl" for c in used]].to_numpy(dtype=float)
    w = np.array([weights[c] for c in used], dtype=float)
    avail = ~np.isnan(vals)
    applicable = avail.sum(axis=1)
    num = np.nansum(vals * w, axis=1)
    den = (avail * w).sum(axis=1)
    s = np.where(
        (applicable >= min(cfg.SCORECARD_MIN_COMPONENTS, len(used))) & (den > 0),
        num / np.where(den > 0, den, 1),
        np.nan,
    )
    out = pctl[["ministry_id", "canonical_name", "grai_group"]].copy()
    out["components_used"] = applicable
    out["score"] = s
    out["rank_in_group"] = out.groupby("grai_group")["score"].rank(ascending=False, method="min")
    return out


def sensitivity(pctl: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    base = score(pctl, cfg.SCORECARD_WEIGHTS)
    rows = []
    for name, w in cfg.SCORECARD_VARIANTS.items():
        alt = score(pctl, w)
        for grp in sorted(base["grai_group"].unique()):
            b = base[base["grai_group"] == grp].set_index("ministry_id")
            a = alt[alt["grai_group"] == grp].set_index("ministry_id")
            both = b.index[b["score"].notna() & a["score"].reindex(b.index).notna()]
            rho, _ = spearman(b.loc[both, "rank_in_group"].tolist(), a.loc[both, "rank_in_group"].tolist())
            shift = (b.loc[both, "rank_in_group"] - a.loc[both, "rank_in_group"]).abs()
            q_b = b.loc[both, "rank_in_group"] <= np.ceil(len(both) / 4)
            q_a = a.loc[both, "rank_in_group"] <= np.ceil(len(both) / 4)
            rows.append(
                {
                    "variant": name,
                    "group": grp,
                    "ministries": len(both),
                    "spearman_vs_default": rho,
                    "max_rank_shift": shift.max(),
                    "median_rank_shift": shift.median(),
                    "top_quartile_changes": int((q_a != q_b).sum()),
                }
            )
    variants = pd.DataFrame(rows)

    # random weights: Dirichlet(1, ..., 1) over the default (non-zero) components
    rng = np.random.default_rng(cfg.SCORECARD_SEED)
    comps = [c for c, w in cfg.SCORECARD_WEIGHTS.items() if w > 0]
    ranks = []
    for draw in rng.dirichlet(np.ones(len(comps)), size=cfg.SCORECARD_RANDOM_DRAWS):
        s = score(pctl, dict(zip(comps, draw, strict=True)))
        ranks.append(s.set_index("ministry_id")["rank_in_group"])
    r = pd.concat(ranks, axis=1)
    intervals = pd.DataFrame(
        {
            "rank_random_median": r.median(axis=1),
            "rank_random_p05": r.quantile(0.05, axis=1),
            "rank_random_p95": r.quantile(0.95, axis=1),
        }
    )
    return variants, intervals


def component_correlations(pctl: pd.DataFrame) -> pd.DataFrame:
    rows = []
    comps = list(COMPONENTS)
    for grp, g in pctl.groupby("grai_group"):
        for i, a in enumerate(comps):
            for b in comps[i + 1 :]:
                d = g[[f"{a}_pctl", f"{b}_pctl"]].dropna()
                rho, _ = spearman(d.iloc[:, 0].tolist(), d.iloc[:, 1].tolist())  # NaN if one side is constant
                rows.append(
                    {"group": grp, "component_a": a, "component_b": b, "n": len(d), "spearman_rho": rho}
                )
    return pd.DataFrame(rows)


def build_scorecard(frames: dict[str, pd.DataFrame]) -> dict[str, pd.DataFrame]:
    comp = build_components(frames["grai"], frames["flows"], frames["closing"], frames["pendency"])
    pctl = to_percentiles(comp)
    base = score(pctl, cfg.SCORECARD_WEIGHTS)
    variants, intervals = sensitivity(pctl)
    card = (
        pctl.merge(base[["ministry_id", "components_used", "score", "rank_in_group"]], on="ministry_id")
        .merge(intervals, left_on="ministry_id", right_index=True, how="left")
        .assign(group_size=lambda d: d.groupby("grai_group")["score"].transform("count"))
        .sort_values(["grai_group", "rank_in_group"])
    )
    card["not_scored_reason"] = np.where(
        card["score"].isna(),
        "fewer than "
        + str(cfg.SCORECARD_MIN_COMPONENTS)
        + " applicable components with data (months ranked in era: "
        + card["months_ranked"].astype(str)
        + ")",
        "",
    )
    card["label"] = "project construct, not an official rating"
    return {
        "S_scorecard": card,
        "S_sensitivity": variants,
        "S_component_correlations": component_correlations(pctl),
    }


def main() -> int:
    from src.analysis.run import OUT_DIR, load_frames
    from src.analysis.views import apply_views
    from src.config import ConfigError, load_settings
    from src.db import get_engine

    try:
        settings = load_settings()
    except ConfigError as exc:
        print(f"[config] {exc}", file=sys.stderr)
        return 1
    engine = get_engine(settings.database_url)
    try:
        apply_views(engine)
        frames = load_frames(engine)
    finally:
        engine.dispose()
    results = build_scorecard(frames)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    for name, df in results.items():
        df.to_csv(OUT_DIR / f"{name}.csv", index=False, lineterminator="\n")
        print(f"{name}: {len(df)} rows")
    return 0


if __name__ == "__main__":
    sys.exit(main())
