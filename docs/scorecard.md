# Ministry grievance scorecard (Phase 5)

> **This is an analytical construct built for this project. It is not an official Government of India or
> DARPG rating.** It combines published CPGRAMS indicators in a documented way so they can be compared
> within like groups. It says nothing about individual complaints, staff conduct or causes, and results are
> provisional until the sampled values are visually verified (see data_quality.md §6).

Reproduce: `python -m src.analysis.run`. Configuration: `src/analysis/config.py` (`SCORECARD_*`).
Results: `data/processed/analysis/S_scorecard.csv`, `S_sensitivity.csv` and `S_component_correlations.csv`;
headline tables in [analysis_results.md](analysis_results.md).

## 1. Is a scorecard justified?

Only partly. Of the brief's candidate components:

| Candidate | Decision | Reason |
|---|---|---|
| Published average disposal time | **Used as list membership only** | Values are published only for the top-10 slowest and fastest. As a number, it is missing for most ministries, and missing must not become zero |
| Backlog trend | Used | Available for ~90 ministries (T6 panel, Jun 2024 – Jan 2026) |
| Repeated high-pendency listing (> 1,000 pending) | **Information only (weight 0)** | The absolute threshold makes it mechanically size-dependent: a small ministry can never be listed. It is kept as a column and tested in the sensitivity analysis |
| Comparable published rankings (GRAI) | Used, within one era | Only #46–#53 (Feb–Sep 2026) share one method; ranks are turned into within-group percentiles |
| Backlog relative to demand | **Added** | A size-neutral measure available from the T6 panel |

## 2. Definition

Scope: ministries ranked in the GRAI in #46–#53 (91), each assigned its most frequent GRAI group (A: ≥ 500
grievances, B: fewer). 4 ministries switched group; only their months in the assigned group count.
**Ministries are compared only within their group.**

| Component | Definition | Direction | Default weight |
|---|---|---|---|
| `grai_percentile` | Mean over #46–#53 of 100 × (group size − rank) ÷ (group size − 1); needs ≥ 6 of 8 months | higher = better | 0.25 |
| `backlog_trend_pct` | Relative Sen slope of month-end pending, #26–#45 (% of mean per month) | lower = better | 0.25 |
| `backlog_months` | Mean month-end pending ÷ mean monthly receipts, #29–#45 ("months of intake waiting") | lower = better | 0.25 |
| `slow_closing_share` | Share of #46–#53 reports on the high average-closing-time Top-10 | lower = better | 0.25, **Group A only** |
| `over_1000_share` | Share of #46–#53 reports on the > 1,000-pending list | lower = better | 0 (information) |

**Why slow-closing is Group A only:** from 2026 that list includes only ministries with more than 100
disposals. Many Group B ministries cannot appear on it at all, so their absence is not evidence. For Group B
the component is *not applicable*, not zero.

**Scale:** each component is converted to a percentile among the ministries of the same group (0 = worst,
100 = best value in the group; ties share the average rank). Percentiles make components with different units
comparable and blunt outliers, such as the Rural Development spike.

**Score:** the weighted mean of the available, applicable component percentiles, with weights renormalised over
those components. A ministry needs at least **3** such components (Group B has exactly 3 applicable components,
so it needs all of them). Otherwise it gets no score, with `not_scored_reason` filled. **Missing values are
never treated as zero.**

## 3. Results (provisional)

- Scored: **42 of 43** Group A ministries and **46 of 48** Group B ministries. Not scored: the Department of
  Urban Development (exists only from Sep 2026), the Department of Legal Affairs and the Ministry of Coal
  (5 of 8 months ranked).
- The score is a relative position *within the group in this window*. A low score means the ministry's
  published indicators were less favourable than its group peers' over Feb–Sep 2026 (GRAI) and
  Jun 2024 – Jan 2026 (backlog). It is not a statement about why.

**Sensitivity (does the ranking depend on the weights?)** Five named weightings and 1,000 random weightings
(Dirichlet, seed 2026) were run:

| Variant | Spearman ρ with default rank (A / B) | Max rank shift (A / B) | Top-quartile changes (A / B) |
|---|---|---|---|
| Official GRAI rank weighted 0.5 | 0.951 / 0.923 | 10 / 14 | 4 / 6 |
| Backlog components weighted 0.35 each | 0.949 / 0.908 | 12 / 14 | 0 / 4 |
| Without slow-closing component | 0.956 / 1.000 | 14 / 0 | 0 / 0 |
| With the > 1,000 list at 0.2 | 0.949 / 1.000 | 9 / 2 | 4 / 0 |

Ranks are broadly robust (ρ ≥ 0.9 everywhere), but individual ministries move by up to 14 places, and up to 6
ministries enter or leave a top quartile. The random-weight 90% interval is narrow at the extremes (e.g.
Ministry of Power, Group A rank 1, interval 1–5) and wide in the middle (Department of Posts, rank 5, interval
1–16). **Always report a ministry's rank together with its interval (`rank_random_p05`–`rank_random_p95`).**

**Component overlap** (`S_component_correlations.csv`, Group A): `backlog_months` correlates with
`slow_closing_share` (ρ = 0.63) and with `over_1000_share` (0.58). `backlog_trend_pct` is almost uncorrelated
with the others (|ρ| ≤ 0.18), so it adds distinct information. In Group B, `over_1000_share` is constant
(no Group B ministry ever exceeded 1,000 pending), so its correlations are undefined.

## 4. Limitations

- **Different windows:** the GRAI component covers Feb–Sep 2026; the backlog components cover Jun 2024 –
  Jan 2026, because 2026 ministry flows failed validation (D-017). They are not the same months.
- **Mandates differ.** A ministry handling pension or tax grievances faces different cases from a cultural body;
  grouping by GRAI volume class does not remove that. Percentiles are relative to the peers in the group.
- **The GRAI percentile is itself a composite** whose indicators partly overlap with the backlog components.
- Weights are a choice; equal weights are a neutral default, not a finding. The sensitivity analysis shows how
  much they matter.
- 2 years or less of data per component; no trend in the score itself is computed.
- Not suitable for predicting how any individual complaint will be handled (Part B must say so wherever
  historical figures are shown).
