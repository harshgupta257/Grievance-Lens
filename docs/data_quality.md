# Data quality report (Phase 3)

Status on 2026-10-21. Every count here comes from the pipeline: the check results are generated in
[data_quality_checks.md](data_quality_checks.md), with detail rows in `data/interim/quality/C*.csv`.
This page explains them and sets the rules the analyses (Phase 4) must follow.

Reproduce (PowerShell, venv active, database running):

```powershell
python -m src.extract.run; python -m src.extract.load
python -m src.normalise.build; python -m src.normalise.load
python -m src.validate.checks; python -m src.validate.sample
```

## 1. Summary

| Item | Result |
|---|---|
| Reports | 53 English reports, May 2022 – Sep 2026, consecutive (C01) |
| Extracted observations | 36,718 in 387 tables: 35,732 ministry-level, 986 Central Secretariat aggregates |
| Validation status | 33,728 valid · 790 warning · 2,200 unvalidated (no arithmetic identity exists for them) · **0 failed** |
| Printed ministry names | 121 → **92 canonical entities** (49 departments, 37 ministries, 6 organisations) |
| Name matches | 112 exact after formatting-only cleaning · 9 by reviewed alias decisions · **0 unresolved** |
| Ministry linkage | 35,732 of 35,732 ministry observations linked (C03) |
| Duplicate observations | 0 (C04): no ministry appears twice in one table, so no accepted merge collapsed two different rows |
| Missing months | 0 report months (C01); 0 months missing from the aggregate series Jan 2022 – Sep 2026 (C05) |
| Open extraction errors | 0 (C12) |
| Manual visual verification | **Pending:** 12 values in [manual_verification.csv](manual_verification.csv) for the project owner |

## 2. Ministry name resolution

**Rules (D-023, D-024).**
1. **Formatting-only cleaning** (automatic): Unicode normalisation, footnote `*`, `&`/`and`, bracket spacing,
   stray dashes, case. Words broken across lines are rejoined only when the joined word occurs in at least
   5 printed rows and one piece does not (`src/normalise/names.py`). This merged 112 printed forms, e.g.
   7 spellings of "Department of Telecommunications" such as "Telecommunica tions".
2. **Alias decisions** (reviewed): recorded in `data/reference/ministry_aliases.csv`, with evidence, reviewer and date.
3. **Fuzzy similarity only proposes.** Similarity of the distinguishing words (generic words such as
   "Ministry of" removed; RapidFuzz `token_sort_ratio` ≥ 85), or identical year-to-date figures under two
   names in one report, puts a pair in `ministry_alias_review.csv`. Calibration on known pairs: same bodies
   scored ≥ 86.7, different bodies ≤ 75. A pair printed in the same table is different by definition, and is
   dropped automatically (1 such pair). While undecided, the less frequent name stays unmapped.
4. **Canonical name** = the cleaned form printed in the most recent report, so renamed bodies show their
   current CPGRAMS name. Names follow DARPG's spelling (e.g. "Department of Bio Technology"); official
   Government of India names are collected in Phase 7.

**The 10 decisions** (all accepted by the project owner on 2026-10-21):

| From | To | Evidence |
|---|---|---|
| Central Board of Excise and Customs | Central Board of Indirect Taxes and Customs | **Proven**: all 5 figures identical under both names in #1 (May 2022) |
| Ministry of Labor and Employment | Ministry of Labour and Employment | Spelling; "Labor" only in 2 GRAI tables (#13, #14), "Labour" in the annexures of the same reports |
| Department of Telecommunication | Department of Telecommunications | Spelling; both in #5, in different tables |
| Department of Pharmaceutical | Department of Pharmaceuticals | Spelling; consecutive periods (#4–#18, then #19 onward), never together |
| Department of Biotechnology | Department of Bio Technology | Spacing; 1 row (#11) vs 63 |
| Department of Heavy Industry / Industries | Ministry of Heavy Industries | Renamed in CPGRAMS around Apr 2025: both names in #36 (different tables); never in one table |
| Department of Drinking Water and Sanitation | Ministry of Drinking Water and Sanitation | Never in the same report; name alternates 2022–23 |
| Department of Administrative Reforms and Public Grievances | … PG Division | Weakest evidence: 1 row each (#13, #22) |

**Not an alias:** the Ministry of Housing and Urban Affairs was *split* (note in #53, PDF pages 11 and 28)
into the Department of Urban Development, which reports from Sep 2026, and the Department of Capital
Development, which is not yet on CPGRAMS. This is stored as a `split_into` relationship (D-025); the two
series are not joined.

## 3. Findings about the source data

These are properties of DARPG's published figures, verified against the extracted values. They are not
extraction errors.

**3.1 Earlier months are revised in later reports (C06: 49 of 153 month–metric values printed in several reports).**
- *2022 reports (#3–#8) recompute every earlier month each time.* Jan 2022 pending: 83,167 (#3) → 83,291 →
  83,375 → 83,456 → 83,525 → 83,545 (#8). The changes are small and monotonic, consistent with each report
  querying the live system at its own cut-off.
- *Before Apr 2023, a report's own month is partial.* Jul 2022 receipts: 77,414 in #3 (cut-off 25 Jul) vs
  96,536 in #4, a ratio of 0.80 (25/31 = 0.81). The same holds for Aug–Nov 2022 and Mar 2023. This confirms
  that the 25th-of-month cut-off truncates the report month.
- *#14 (Jun 2023) restated Jan–May 2023 once* (e.g. Apr 2023 receipts 105,218 → 109,209). The values are
  identical in every later report, matching the printed note "as per the previously published Monthly Reports".
- *Apr 2024 receipts* are 146,492 in #24–#25 and 146,942 in #26–#27: a digit transposition ("49"/"94") or a
  revision. The source does not say which.

**3.2 The year-to-date overview box differs slightly from the sum of the printed months (C07: 16 of 32, max 0.31%).**
The box is computed at the report's cut-off; the month-wise values carry earlier reports' figures (3.1).
Note: in #36–#44 the parser had matched the GRAI table header instead of an overview box. This was found by
C07, fixed, and those 9 tables are gone; #36–#53 print no overview box.

**3.3 #41 (Sep 2025) reprints #40's average-closing-time annexures.** 20 of 20 values are identical, while the
heading claims data to 30 Sep 2025. The two tables are flagged `identical_to_previous_report` and their 40
observations set to `warning` (`src/extract/reprints.py`; detection runs on every table, and these are the
only 2 of 387 tables flagged). This explains 5 of the 13 C09 differences. The other 8 differ by at most 10.

**3.4 Small arithmetic gaps in published ministry rows** (44 rows, ±1–3; D-016) and between the GRAI and
annexure Pending (C10: 3 of 318, all in #43, by 1).

**3.5 Department of Rural Development 2024 receipts spike** (712,619 year-to-date by Aug 2024), consistent
across independent tables. It is a published figure; its cause is unverified.

**3.6 #36 (Apr 2025) footnote** (PDF page 12): of 714 and 570 grievances received, 368 for the Department of
Revenue and 500 for the "Ministry of Military Affairs" (as printed; the tables say Department of Military
Affairs) "have been closed with new ATR". Both are marked `*` in that report's GRAI table; their April 2025
scores carry this caveat.

## 4. Consistency checks that passed

| Check | Result |
|---|---|
| C08 Long-pending rows equal the >1000 rows for the same ministry and report | 2,560 of 2,560 identical |
| C10 GRAI month-end Pending equals annexure Pending | 315 of 318 identical (3 differ by 1) |
| C05 Aggregate series complete | 171 metric-months, 0 missing |
| C02 / C11 Negative values / failed identities | 0 / 0 |

## 5. Rules for the analyses (Phase 4)

1. **One value per month for the aggregate series:** take the value from the *latest* report that prints that
   month. It is a complete month and the most revised value (3.1). Keep all printed values for provenance.
   **Never use a pre-Apr-2023 report's own month** (partial). (D-026)
2. Trend tests use Apr 2023 – Sep 2026 only (D-006); 2022 values are descriptive and carry a break marker.
3. **Exclude tables flagged `identical_to_previous_report`** (#41 closing time) from period-specific analyses.
4. Ministry series use `ministry_id`, never printed names. Do not join the Ministry of Housing and Urban
   Affairs and the Department of Urban Development series (split, Sep 2026).
5. Absence from a list means "below threshold or not listed", never zero (censoring, Phase 0).
6. Long-pending lists are compared only within one threshold (45 / 30 / 21 days); GRAI ranks only within one
   layout era and group (see DESIGN.md limitations).
7. Ministry-level monthly flows exist only for #26–#45 (Jun 2024 – Jan 2026; T6, D-017). #28's GRAI window is
   two months (Jul–Aug 2024).

## 6. Manual verification (required by the brief: at least 10 values)

[manual_verification.csv](manual_verification.csv) lists **12 values drawn at random**, one per stratum
(table type × era, seeded so the same values are drawn after every reload). For each row: open
`data/raw/pdfs/<filename>`, go to the physical page `pdf_page` in your PDF viewer (the printed footer number
is in `printed_page`), find the row and column, and fill `your_value` and `match` (Y/N).

**Status: not yet done.** The parsers' own regression values (docs/extraction_verification.md §1) come from
the same PDF text layer, so they are not an independent check. Until the sheet is filled, extraction accuracy
is supported by the internal identities and cross-table checks above, not by visual confirmation.

## 7. Remaining limitations

- 31 tables state no period (year-to-date assumed); 1 dash dropped from a name ("…PG Division", #22).
- 8 alias decisions rest on naming evidence rather than proven figure identity; they are recorded with the
  reviewer and can be reversed in `ministry_aliases.csv`, followed by a rebuild.
- Department of Capital Development has no CPGRAMS data yet; its share of the former MoHUA workload is not observable.
- Parent-ministry links (e.g. departments of the Ministry of Finance) are not modelled yet (Phase 7).
- Docker is still blocked by the missing WSL2, so all database work ran on PostgreSQL 16.2 outside Docker.
