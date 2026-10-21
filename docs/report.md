# CPGRAMS grievance analytics and routing assistant: report

*As of 22 October 2026. All Part A findings are provisional until the project owner has visually verified 12
sampled values against the PDFs (`docs/manual_verification.csv`). All Part B results use synthetic data and
proxy labels.*

## 1. Data coverage

| | |
|---|---|
| Source | DARPG's monthly CPGRAMS reports for Central Ministries/Departments: 53 English reports, #1 (May 2022) to #53 (Sep 2026), downloaded from darpg.gov.in, checksummed, with provenance kept for every value |
| Extracted | 387 tables, **36,718 observations** (35,732 at ministry level, all linked to one of 92 canonical ministries). Validation: 33,728 valid, 790 warning, 2,200 unvalidated, 0 failed |
| Aggregate series | Receipts, disposals and month-end pending, Jan 2022 – Sep 2026 (57 months; latest published value per month) |
| Ministry series | Published lists (>1,000 pending; long-pending Top 10 at 45/30/21 days; average closing time Top 10), GRI/GRAI rankings (#4–#53), and a monthly panel of about 90 ministries (Jun 2024 – Jan 2026) |
| Not covered | Hindi editions (only 2 exist), State/UT series, appeals, ministry-level flows after Jan 2026 (failed validation), individual grievances (not public) |

## 2. Key verified findings (observational; no causal claims)

1. **Demand roughly doubled, and disposals kept pace.** Monthly receipts rose from 109,209 (Apr 2023) to
   227,837 (Sep 2026): Sen's slope +2,232 a month, Mann–Kendall q = 0.004. Disposals rose in step
   (+2,203 a month, q = 0.004). Neither the disposal-to-receipt ratio (q = 0.90) nor month-end pending
   (q = 0.23) shows a monotonic trend.
   *Caveat:* in March 2024 the Department of Rural Development alone received 344,265 of 476,498
   grievances (72%).
2. **Persistent high pendency is concentrated.** 35 of 92 ministries were ever listed with more than 1,000
   pending. Five were listed in all 53 reports: Labour and Employment, Health & Family Welfare, CBDT,
   DFS (Banking) and Railways. Within that selected group, Labour's pending rose (+4.0% a month,
   q < 0.001), and CBDT, DFS Banking and Health fell (all q < 0.01).
3. **CBDT recurs across independent lists.** It is in every long-pending Top 10 (50 reports), and in the
   high average-closing-time list in 50 of 52 reports (median 60 days). Listing reflects volume and
   DARPG's definitions; it is not evidence about conduct.
4. **Ministry backlogs moved both ways** (monthly panel, 90 ministries): 16 significant increases, 11
   decreases, 63 no significant trend (Benjamini–Hochberg q < 0.05).
5. **One-month rank changes are noise-prone.** Between consecutive comparable reports, Spearman ρ is
   0.79–0.98 for Group A and 0.61–0.91 for Group B.
6. **Demand and backlog mostly rose together.** Of 66 ministries with enough volume, 37 had both demand and
   backlog rising (Sep 2024 – Jan 2026). 58 and 51 keep their label when the stable band is set to ±0.5% and
   ±2%.

## 3. Statistical methodology

- **Comparability first.** Analyses use only comparable periods: core trend window Apr 2023 – Sep 2026
  (month-end cut-offs); lists compared only within one threshold; ranks only within one layout era and
  group. Absence from a list is never treated as zero.
- **Trend tests.** Mann–Kendall with the Hamed–Rao autocorrelation correction, falling back to trend-free
  pre-whitening where the corrected variance is invalid (1 of 99 tests). Effect size is Sen's slope.
  Benjamini–Hochberg FDR within each test family. No seasonality claims (at most four observations per
  calendar month).
- **Scorecard (project construct, not an official rating).**
  - Method: within-group percentiles of four published indicators; no zero-filling; at least 3 components.
  - Coverage: 88 of 91 ministries scored.
  - Sensitivity: 1,000 random weightings. Overall rank correlation stays high (ρ ≥ 0.91), but individual
    ministries move up to 14 places, so ranks are always quoted with an interval.
- Details: `docs/methodology.md`, `docs/scorecard.md`, `docs/data_quality.md`.

## 4. Ministry-level observations

- **Ministry of Labour and Employment:** the largest absolute rise in month-end pending in the monthly
  panel (4,895 → 13,773, Jun 2024 – Jan 2026) and +4.0% a month over the core window. It also has the
  highest grievance volume (about 15% of panel receipts).
- **Department of Rural Development:** the largest decrease (22,711 → 1,089), the unwinding of its 2024
  surge.
- **CBDT, DFS Banking, Health & Family Welfare:** always listed, with falling listed pending.
- **Department of Posts:** listed with more than 1,000 pending in 46 of 53 reports; panel pending rose
  from 979 to 1,342 (significant increase, q = 0.02).
- **Scorecard.** Within-group ranks are stable at the extremes and wide in the middle. For example,
  Ministry of Power is rank 1 of 42 (interval 1–5) and Department of Posts rank 5 (interval 1–16).

## 5. Router evaluation (synthetic, proxy labels)

300 synthetic complaints in English, Hindi and Hinglish; the reference labels are the author's
generation intent. The owner's independent labels are pending.

| System | Top-1 (95% CI) | Top-3 | Macro-F1 | Non-routable correctly abstained |
|---|---|---|---|---|
| Retrieval + rules (lexical, default) | 62.1% (56.0–67.8) | 86.3% | 0.66 | 24 / 44 |
| Retrieval + rules (hybrid) | 66.4% (60.4–71.9) | 88.7% | 0.70 | 28 / 44 |
| TF-IDF + logistic regression baseline | 9.4% (6.4–13.6) | 67.6% | 0.15 | 43 / 44 (it abstains on 90%) |
| Retrieval + LLM | not run (no API key) | | | |

- **Most common errors:** vocabulary collisions ("company" → Corporate Affairs; "card" or "bank" →
  Banking), near-duplicate mandates (MoHUA vs Urban Development), and vague complaints being routed
  instead of questioned (10 of 12).
- **Prompt injection:** 9 of 10 attempts flagged; none changed the output.

## 6. Drafter evaluation (50 synthetic drafts)

- **Automated checks:** all 50 drafts are complete and reproduce the complaint verbatim, with **0
  unsupported factual tokens and 0 unsupported claim words**.
- **Addressee:** correct in 29 of 50, because routing errors carry into the draft.
- **Not yet done:** human clarity ratings.
- **Screen test:** a deliberately corrupted draft (an invented amount, deadline, threat and allegation)
  was caught in full.

## 7. Limitations

- **Part A:** provisional until visual verification. Published lists are censored at their thresholds.
  The monthly ministry panel covers only 20 months. Validation failures in 2026 ministry flows end the
  panel in Jan 2026. No causal inference is possible.
- **Part B data:** synthetic data written by one author, who also wrote the keywords, and proxy labels.
  The results are not evidence about real citizen complaints.
- **Part B system:**
  - The LLM path has not been run against the live API.
  - The knowledge base covers 37 of 92 entities, and the mapping of one entity is inferred.
  - Abstention on vague complaints is weak.
- **Engineering:** Docker was not executed here (WSL2 missing). No Power BI report has been built. No
  deployment exists.

## 8. Three evidence-based recommendations

These are recommendations about *measurement and tooling*. The data cannot support claims about causes
or conduct.

1. **Report backlog relative to demand, not alone.** Receipts doubled while the aggregate
   disposal-to-receipt ratio stayed flat, and 37 of 66 ministries had demand and backlog rising together.
   Dashboards (including the Power BI guide here) should show months-of-intake-waiting (pending ÷ monthly
   receipts) beside absolute pending, so that rising demand is not read as worse handling.
2. **Read rankings over several months, with intervals.** Consecutive-month rank correlations go as low
   as 0.61 for smaller ministries, and scorecard ranks move up to 14 places under other reasonable
   weightings. Any league table should be a multi-month view with uncertainty, and never a single month's
   rank.
3. **Before any production use of automated routing, evaluate on independently labelled real complaints
   and build "ask, don't guess" into the tool.** Even on clean synthetic text, the best router is right at
   rank 1 only about two times in three, while a department is in its top 3 about 9 times in 10. The
   evidence supports showing a short list with official mandate evidence and asking a clarifying question.
   It does not support auto-assigning.
