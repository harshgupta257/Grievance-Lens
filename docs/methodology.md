# Analytics methodology (Phase 4)

How every metric and test is defined, which data each analysis uses, and what the results can and cannot
support. Generated result tables: [analysis_results.md](analysis_results.md) and `data/processed/analysis/`.
Data rules: [data_quality.md §5](data_quality.md).

> **Status:** the 12 sampled values ([manual_verification.csv](manual_verification.csv)) were checked
> against the rendered PDF pages on 2026-10-22: 12 of 12 match. The check was done by the AI assistant at the
> owner's request, not by an independent person. Part A was also rebuilt from the PDFs into a fresh database
> with identical outputs.

Reproduce: `python -m src.analysis.run` (re-creates the SQL views in `sql/views/`, then runs `src/analysis/`).
Thresholds live in `src/analysis/config.py`.

## 1. Data and periods

| Source (view) | Content | Period |
|---|---|---|
| `v_monthly_overall` | Central Secretariat receipts, disposals and month-end pending, **one value per month from the latest report that prints it** (D-026). Provenance: source report, PDF page, revision spread | Jan 2022 – Sep 2026 (57 months). Dec 2022 is printed only in its own partial report and is excluded from derived metrics |
| `v_pendency_list` | Ministries listed with > 1,000 pending: pending (stock at cut-off) and year-to-date flows | 53 reports |
| `v_long_pending_list` | Top-10 ministries with grievances pending > N days | N = 45 (#1–#3), 30 (#4–#28), 21 (#29–#53) |
| `v_closing_time_list` | Top-10 high / low published average closing time (year-to-date) | 52 reports (#41 excluded: stale reprint, D-027) |
| `v_grai` | GRI/GRAI rank and score per group, with layout era and data window | #4–#53 |
| `v_ministry_monthly_flows` | T6 panel: monthly BF / receipts / disposals / month-end pending for ~90 ranked ministries | #26–#45 (Jun 2024 – Jan 2026); #28 flows cover Jul–Aug 2024 |

**Core window** for trend tests: Apr 2023 – Sep 2026 (42 months, month-end cut-offs; D-006).

## 2. Metric definitions

| Metric | Definition | Type | Official or derived |
|---|---|---|---|
| Receipts (month) | Grievances received by Central Ministries/Departments in the calendar month | flow, count | Official |
| Disposals (month) | Grievances disposed of in the month | flow, count | Official |
| Pending (month-end) | Grievances pending at the month's end (or the report's cut-off) | stock, count | Official |
| Disposal-to-receipt ratio | Disposals ÷ receipts in the same month. Above 1 means more cases closed than received | ratio | Derived |
| Pending change | Pending(t) − Pending(t−1), only between consecutive months or reports | change, count | Derived |
| Pending change % | 100 × change ÷ Pending(t−1), only when Pending(t−1) ≥ 100 | change, % | Derived |
| Rolling 3-month mean | Mean of three consecutive complete months (or reports) | count | Derived |
| Listed (> 1,000) | The ministry appears in that report's ">1000 pending" annexure. **Not listed ≠ zero**: it means below the threshold or not published | indicator | Official list |
| Longest / current run | Longest run of consecutive reports in which listed; run ending at #53 | count of reports | Derived |
| Long-pending appearance | In that report's Top-10 for pending > N days (compare within one N only) | indicator | Official list |
| Average closing time | Published mean days to close grievances, year-to-date, Top-10 high and low lists only | duration, days | Official |
| GRAI rank / score | Published rank within group A (≥ 500 grievances) or B. Comparable only within one layout era and group | rank / score | Official |
| Sen's slope | Median of all pairwise slopes of a series, per month | trend, units/month | Derived |
| Relative slope | 100 × Sen's slope ÷ series mean (% of the mean level per month) | trend, %/month | Derived |
| Spearman ρ | Rank correlation of a group's ranks in consecutive reports (ministries in both) | correlation | Derived |
| Top-5 retained | Share of a group's top-5 that is still top-5 in the next report | share | Derived |

## 3. Analyses

**A. Aggregate trends.** Monthly series with ratio, changes, rolling means and year-on-year change of
receipts; yearly summary over complete months only. Output: `A_overall_monthly.csv`, `A_overall_by_year.csv`.

**B. Repeated appearances.** For each ministry: reports listed (> 1,000), share of 53, longest and current
consecutive runs, core-window share, median and maximum pending when listed, and a `reorganisation_note`
(a run can end because the body was split, e.g. MoHUA in Sep 2026). Reports are consecutive (#1–#53, no
gaps), so runs are well defined. Long-pending appearances are counted per threshold segment. Closing-time
list appearances exclude #41. Outputs: `B_*.csv`.

**C. Backlog changes.** Pending changes between consecutive reports, for listed ministries only (a change is
never computed across a gap in list membership, because the gap is censoring), plus a full-panel summary
(Jun 2024 → Jan 2026) for the ~90 ministries of T6. Absolute and percentage changes are kept separate.
Outputs: `C_*.csv`.

**D. Ranking stability.** For each pair of consecutive reports and group, compared **only if the layout era
and the group threshold are the same**: Spearman ρ on the ministries ranked in both, mean absolute rank
change (also as a share of group size), and top-5 retention, summarised per era. The indicator set may also
change *within* an era (e.g. 12 indicators in #16 vs 11 in #22); this was not checked report by report, so
comparisons are limited to adjacent months. Outputs: `D_*.csv`.

**E. Trend tests.** See §4. Three test families:
- F1: the aggregate series in the core window (receipts, disposals, pending, ratio). n = 42.
- F2: month-end pending of the ministries listed > 1,000 in **every** core-window report (5 ministries,
  n = 42). Requiring every report avoids gaps from censoring, but it selects persistently high-pendency
  bodies: a selection effect.
- F3: month-end pending in the T6 panel (90 ministries, n = 20, Jun 2024 – Jan 2026). Pending is a stock,
  so #28's two-month flow window does not affect it.

**F. Seasonality.** Only a descriptive month-of-year table of receipts in the core window. Each calendar month
is observed 3–4 times, which is far too few to estimate seasonality, so no seasonal test is run and no
seasonal claim is made. The high March mean is driven by March 2024 (below).

**G. Demand vs backlog.** T6 panel, **Sep 2024 – Jan 2026 (#29–#45, 17 gap-free single-month windows**; #26–#28
are left out because #28's flows cover two months). For each ministry with mean monthly receipts ≥ 100 and mean
pending ≥ 50, the relative Sen slope of monthly receipts (demand) and of month-end pending (backlog) is computed.
Each slope is "rising" if ≥ +1% of its mean per month, "falling" if ≤ −1%, otherwise "stable". The ministry's
quadrant is the pair of labels. Mann–Kendall q-values for both axes are reported alongside. Sensitivity: bands of
0.5% and 2%. **A quadrant describes co-movement; it is not a performance rating.** Rising demand is not poor
performance, and a falling backlog may reflect falling demand. Outputs: `G_*.csv`.

## 4. Statistical tests

**Mann–Kendall trend test** (`src/analysis/stats.py`, pymannkendall 1.4.3).
- H0: the series has no monotonic trend. H1: a monotonic increasing or decreasing trend (two-sided).
- Statistic: S = Σ sign(x_j − x_i) over all pairs i < j; Kendall's τ = S normalised to [−1, 1]; z from S and
  its variance (with tie correction).
- **Autocorrelation:** monthly series are positively autocorrelated, which inflates the original test's
  significance. The p-value used is the **Hamed–Rao modification**, which rescales Var(S) by an effective sample
  size. In 1 of 99 tests (O/o the CAG, F3) the corrected variance was negative, so the **trend-free
  pre-whitening** test (Yue et al. 2002) is used instead; `p_method` records which. The original p-value is
  kept for comparison.
- Effect size: Sen's slope and relative slope. Series must be gap-free and equally spaced.
- **Multiple testing:** Benjamini–Hochberg FDR within each family (F1, F2, F3, and each axis of G); significant
  if q < 0.05. With 90 tests in F3, about 4–5 "significant" results would be expected from noise alone at
  p < 0.05 without correction; the FDR adjustment controls that.

**Spearman rank correlation** (SciPy) for ranking stability. It is descriptive; its p-values are not used for
claims.

**What significance means here.** A small q says a monotonic trend is unlikely under H0 over this window. It
does not say the trend is large (see Sen's slope), will continue, or has a cause. All data are observational;
no causal statement is made.

## 5. Findings (strongest evidence first)

1. **Demand roughly doubled in the core window, and disposals kept pace in aggregate.** Monthly receipts rose
   from 109,209 (Apr 2023) to 227,837 (Sep 2026): Sen's slope +2,232/month (1.44% of the mean), τ = 0.58,
   q = 0.004. Disposals rose with them: +2,203/month, q = 0.004. The disposal-to-receipt ratio shows no trend
   (τ = 0.02, q = 0.90), and month-end pending has no significant monotonic trend (q = 0.23; range 53,481 in
   Nov 2024 to 104,100 in Mar 2024).
   *Caveat:* in March 2024 the Department of Rural Development alone received 344,265 grievances, 72% of that
   month's 476,498. The rank-based test is robust to one outlier, but 2024's levels and averages are not.
2. **Persistent high pendency is concentrated in a few bodies.** 35 of 92 ministries were ever listed with
   > 1,000 pending. Five were listed in **all 53 reports**: the Ministry of Labour and Employment, the
   Department of Health & Family Welfare, CBDT (Income Tax), DFS (Banking Division) and the Ministry of Railways.
   Over the 42 core months, Labour's pending increased (+4.0%/month, τ = 0.85, q < 0.001) and Railways' rose
   modestly (+1.2%/month, q = 0.047). CBDT (−2.3%/month), DFS Banking (−2.2%) and Health & FW (−1.3%)
   decreased (all q < 0.01). This is a selected group (§3E).
3. **CBDT recurs across independent lists.** It is in the long-pending Top-10 in all 25 reports with the
   30-day threshold and all 25 with the 21-day threshold, and in the high average-closing-time list in 50 of
   52 reports (median 60 days). Its listed pending fell over the core window (finding 2). Listing reflects
   workload and backlog under DARPG's published definitions; it is not a finding about conduct.
4. **Ministry-level backlog moved in both directions (T6 panel, Jun 2024 – Jan 2026, 90 ministries).** 16
   ministries show a significant increase in month-end pending, 11 a decrease, and 63 no significant trend
   (BH q < 0.05). The largest absolute increase is the Ministry of Labour and Employment (4,895 → 13,773). The
   largest decrease is the Department of Rural Development (22,711 → 1,089), the unwinding of its 2024 spike.
5. **Single-month ranks are noisy.** Between consecutive comparable reports, the median Spearman ρ is 0.79–0.98
   for Group A and 0.61–0.91 for Group B, depending on the era (minimum 0.43). Group B is less stable than
   Group A in every era. A one-month rank change should not be read as a change in performance.
6. **Demand and backlog mostly rose together** (66 ministries with enough volume, Sep 2024 – Jan 2026). 37 are
   "demand rising / backlog rising", 9 "demand rising / backlog falling", and 7 "demand rising / backlog
   stable". The classification is moderately sensitive to the band: 58 of 66 keep their quadrant at ±0.5% and
   51 of 66 at ±2%. Rising demand is not poor performance.

## 6. What the data cannot support

- Any statement about ministries never listed (censoring), or about list absence as improvement.
- Rank comparisons across layout eras, or long-run rank trends (D-030).
- Seasonality, or year-on-year comparisons involving 2022 partial months.
- Statements about individual complaints: how long *your* grievance will take is not predicted by any of this.
- Causes of any trend (staffing, policy, campaigns, portal changes), and any judgement of conduct.
- 2026 ministry-level flows: the "Data Used" tables failed validation (D-017).
