"""Analysis thresholds in one place (docs/methodology.md explains each). Change here, not in the code."""

from datetime import date

CORE_WINDOW_START = date(2023, 4, 1)  # first month with month-end cut-offs (D-006)
ALPHA = 0.05  # significance level, applied to Benjamini-Hochberg q-values within a test family
MIN_TREND_POINTS_OVERALL = 24  # Mann-Kendall on the aggregate series needs at least this many months
MIN_TREND_POINTS_MINISTRY = 18  # ...and per ministry (T6 panel has 20 months; consecutive months required)
MIN_BASE_FOR_PCT_CHANGE = 100  # percentage change only when the previous value is at least this large

# T6 monthly ministry panel (#26-#45). #28's GRAI flows cover two months (Jul-Aug 2024), so analyses that
# use monthly *flows* start at #29; month-end pending (a stock) can use the whole panel.
PANEL_REPORTS = (26, 45)
PANEL_FLOW_REPORTS = (29, 45)  # Sep 2024 - Jan 2026: 17 gap-free months of single-month flows
MIN_POINTS_DEMAND_BACKLOG = 15

# Analysis G (demand vs backlog, T6 panel)
STABLE_BAND_PCT_PER_MONTH = 1.0  # |relative Sen slope| below this (% of mean level per month) = "stable"
SENSITIVITY_BANDS = (0.5, 1.0, 2.0)  # bands re-run to test how much the classification depends on the choice
MIN_MEAN_MONTHLY_RECEIPTS = 100  # ministries smaller than this are "insufficient volume" (noisy ratios)
MIN_MEAN_PENDING = 50

# --- Phase 5: ministry scorecard (a project construct, not an official rating; docs/scorecard.md) ---
SCORECARD_GRAI_REPORTS = (46, 53)  # one GRAI method era (Feb-Sep 2026, normalisation removed)
SCORECARD_MIN_MONTHS_RANKED = 6  # of the 8 reports in the era
SCORECARD_MIN_COMPONENTS = 3  # fewer available (applicable) components -> no score, never zero-filled
# component -> default weight. Directions: higher GRAI percentile is better; for the others lower is better.
SCORECARD_WEIGHTS = {
    "grai_percentile": 0.25,  # official GRAI rank within group, as a percentile, mean over the era
    "backlog_trend_pct": 0.25,  # relative Sen slope of month-end pending, T6 panel #26-#45
    "backlog_months": 0.25,  # mean pending / mean monthly receipts, T6 panel #29-#45 (size-neutral)
    "slow_closing_share": 0.25,  # share of era reports on the high average-closing-time list (Group A only)
    "over_1000_share": 0.0,  # share of era reports on the >1000-pending list: size-biased, information only
}
SCORECARD_VARIANTS = {
    "equal_default": SCORECARD_WEIGHTS,
    "official_rank_heavy": {
        "grai_percentile": 0.5,
        "backlog_trend_pct": 1 / 6,
        "backlog_months": 1 / 6,
        "slow_closing_share": 1 / 6,
        "over_1000_share": 0.0,
    },
    "backlog_heavy": {
        "grai_percentile": 0.1,
        "backlog_trend_pct": 0.35,
        "backlog_months": 0.35,
        "slow_closing_share": 0.2,
        "over_1000_share": 0.0,
    },
    "without_closing_time": {
        "grai_percentile": 1 / 3,
        "backlog_trend_pct": 1 / 3,
        "backlog_months": 1 / 3,
        "slow_closing_share": 0.0,
        "over_1000_share": 0.0,
    },
    "with_over_1000_list": {
        "grai_percentile": 0.2,
        "backlog_trend_pct": 0.2,
        "backlog_months": 0.2,
        "slow_closing_share": 0.2,
        "over_1000_share": 0.2,
    },
}
SCORECARD_RANDOM_DRAWS = 1000  # Dirichlet(1,...,1) weight draws over the default components
SCORECARD_SEED = 2026
