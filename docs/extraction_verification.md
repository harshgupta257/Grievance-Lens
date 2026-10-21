# Extraction verification (Phase 2)

How the extracted data was checked, what passed, and what remains open. Generated counts live in
[extraction_summary.md](extraction_summary.md); this page explains them. All results below were produced
on 2026-10-21 from the 53 English reports listed in [../data/manifest.csv](../data/manifest.csv).

## 1. Values to verify by eye

Open the PDF named in the manifest (`data/raw/pdfs/<filename>`), go to the **physical** page number shown
by your PDF viewer (see D-004), and find the row. Each value is also asserted by
`tests/test_golden_values.py` (`python -m pytest -m pdf`), which re-runs the parsers on the real PDF.

| # | Report | PDF page | Table | Row | Metric | Extracted value |
|---|---|---|---|---|---|---|
| 1 | #1 May 2022 | 11 | 3.3 Ministries with more than 1000 Pendency | 1 Ministry of Cooperation | Pending | 17068 |
| 2 | #4 Aug 2022 | 10 | 5.1 Ranking – Group A (GRI) | 1 Unique Identification Authority of India | Score % | 62.60 |
| 3 | #16 Aug 2023 | 25 | Annexure 1.5 >1000 Pending | 1 Central Board of Direct Taxes (Income Tax) | Brought Forward | 13036 |
| 4 | #16 Aug 2023 | 25 | Annexure 1.5 >1000 Pending | 1 Central Board of Direct Taxes (Income Tax) | Pending | 11510 |
| 5 | #16 Aug 2023 | 26 | Annexure 2.1 High Average Closing Time | 1 Department of Revenue | Avg closing time (days) | 111 |
| 6 | #18 Oct 2023 | 15 | Ranking – Group B | 27 Ministry of Micro Small and Medium Enterprises | Total Grievances | 229 |
| 7 | #26 Jun 2024 | 18 | 7.1 Ranking – Group A | 1 Central Board of Indirect Taxes and Customs | Total Grievances | 1006 |
| 8 | #26 Jun 2024 | 18 | 7.1 Ranking – Group A | 1 Central Board of Indirect Taxes and Customs | Pending | 99 |
| 9 | #40 Aug 2025 | 35 | Annexure 1.3 >1000 Pending | 1 Ministry of Labour and Employment | Pending | 13959 |
| 10 | #40 Aug 2025 | 7 | Month-wise Status of Grievances | Aug-25 | Receipts | 161265 |
| 11 | #53 Sep 2026 | 56 | Annexure 1.3 >1000 Pending | 21 Department of Rural Development | Pending | 1020 |
| 12 | #53 Sep 2026 | 25 | 2.5 Ranking – Group A | 1 Department of Posts | GRAI Score | 0.799 |
| 13 | #53 Sep 2026 | 32 | Month-wise Status of Grievances | Sep-26 | Pendency | 85687 |
| 14 | #53 Sep 2026 | 56 | Annexure 1.4 Pending > 21 days | 1 Ministry of Petroleum and Natural Gas | > 21 Days | NULL (PDF prints `####`) |

These values were taken from the PDF text layer by the same library the parsers use, so the tests guard
against regressions; they are **not** an independent visual check. That check is yours, and it is part
of Phase 3 (at least 10 values, per the brief).

## 2. Automatic checks applied to every row

| Check | Applies to | Result over 53 reports |
|---|---|---|
| Brought Forward + Receipts = Total | >1000, long-pending, GRAI (Jun 2024 layout) | Held in every row where both sides exist |
| Total − Disposal = Pending (or BF + R − D = P) | same + GRAI flow layouts | Exact in all but 44 rows; those 44 miss by 1–3 grievances (26 by +1, 13 by −1). The digits parse cleanly, so they are recorded as `warning` with a `source_identity_gap` issue (D-016) |
| Value count per row = layout columns | all ministry tables | 0 mismatches remain |
| Serial numbers consecutive | all ministry tables | 0 gaps remain |
| Scores within [0, 1], GRI % within [0, 100] | GRAI / GRI | all pass |
| Pending(t) = Pending(t−1) + Receipts(t) − Disposal(t) | month-wise aggregate series | holds exactly for 74 of 242 checked months (222 of 726 observations); DARPG's monthly aggregates are not closed flows (transfers / reopened cases are plausible, not verified). Recorded as `info`, not a failure |
| Same month in several reports | month-wise aggregate series | Jan–Aug 2025 identical in #40, #41 and #44 (Phase 0); full cross-report comparison is Phase 3 |

Ministry-name quality was checked by scanning every extracted name for fragments (names ending in
"of"/"and", starting in lower case, or containing two "Ministry/Department" phrases). The scan led to the
row-assembly rules in D-019 and now flags **1** name: `Ministry of Statistics and Programme Implementati on`
(a word broken across lines in a narrow column) — left for Phase 3 normalisation. The extracted data holds
**121 distinct printed names**; mapping them to canonical ministries is Phase 3.

## 3. T6 pilot: ministry-level monthly flows

**Part 1 — GRAI ranking tables with BF / Receipts / Disposal / Pending (#26–#45, Jun 2024 – Jan 2026).**
For every ministry that appears in both the GRAI table and the >1000-pending annexure of the same report
(two independently printed tables), month-end Pending was compared. Exact name matches only:

| Reports | Ministries matched | Equal | Differ |
|---|---|---|---|
| 20 (#26–#45) | 316 | 313 | 3 (all in #43 Nov 2025, by 1 grievance) |

*Correction (Phase 3): these totals were first published as 326 / 323, an addition error in the summary;
the per-report rows always summed to 316 / 313. Matching by canonical ministry instead of printed name
(Phase 3 check C10) finds 318, of which 315 are equal and the same 3 differ.*

Result: **adopted** — the GRAI flow tables provide a monthly panel of about 90 ministries for Jun 2024 –
Jan 2026, including ministries below the 1000-pending threshold. Note: report #28 states its GRAI data
window as 1 July – 31 August 2024 (two months); that is stored as stated and flagged.

**Part 2 — "Data Used for Score Calculation" tables (#49–#53, May – Sep 2026).** These print ~21 columns per
ministry without serial numbers; for grievances they give Brought Forward and Receipts but no Disposal or
Pending. Pilot on #49, #50 and #53 (Group A), compared with annexure-derived values:

| Comparisons | Equal | Pilot / annexure ratio |
|---|---|---|
| 91 | 1 | median 0.953, range 0.036 – 1.050 |

Result: **not adopted** (D-017). Most values are a few percent lower than the annexure-derived ones, which
points to a different definition in the GRAI input data (unverified); the table is excluded from the
pipeline. Reproduce: `python -m src.extract.grai_inputs 2026-05 2026-06 2026-09`.
Feb–Apr 2026 (#46–#48) print a different "Indicator Wise Scores Calculation" table that was not piloted.

## 4. Database load

Loaded into PostgreSQL 16.2 (scratch server, not Docker — see D-013): 53 reports, 396 tables, 36,754
observations, 263 issues. Every row satisfied the schema constraints. A second load produced identical
counts (idempotent re-run).

## 5. Open extraction issues (all visible in `extraction_issues`)

| Severity | Type | Count | Meaning |
|---|---|---|---|
| warning | period_inferred | 31 | Table states no period; assumed 1 January to the report cut-off (year-to-date), consistent with the sibling tables of the same report |
| warning | stray_number | 1 | "…Public Grievances - PG Division" (#22): the dash is dropped from the name |
| warning | unreadable_cell | 1 | #53 `####` cell, stored as NULL |
| info | flow_identity_gap / source_identity_gap | 176 / 44 | Arithmetic gaps in the published figures (see §2) |
| info | non_month_end_cutoff | 8 | Reports with a 25th-of-month (or 28 Mar 2025) cut-off |
| info | grai_window | 2 | #28 two-month GRAI window (Groups A and B) |

Manual overrides applied: **0** (`data/overrides/overrides.csv` is empty). No value has been corrected by hand.
