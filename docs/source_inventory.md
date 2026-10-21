# Source Inventory — CPGRAMS Monthly Reports (Central Ministries/Departments)

Phase 0 output. Inspection performed **2026-10-21**. Every statement below is based on files that were actually
downloaded and opened, unless it is explicitly marked *unverified* or *not checked*.

---

## 1. Official source

| Item | Value |
|---|---|
| Publisher | Department of Administrative Reforms and Public Grievances (DARPG), Government of India |
| Website | https://www.darpg.gov.in/ (Next.js front-end; page content is loaded client-side) |
| Content backend | WordPress REST API at `https://www.darpg.gov.in/cms/wp-json/wp/v2/` (canonical host shown in API links: `cms-darpg.digifootprint.gov.in`) |
| Post type holding reports | `central_documents` (fields `acf.pdf`, `acf.pdf_hindi`, `acf.pdf_both` hold WordPress media IDs) |
| PDF location pattern | `https://www.darpg.gov.in/static/uploads/<YYYY>/<MM>/<32-hex>.pdf` (upload date, **not** report month) |
| Legacy URL pattern | `https://darpg.gov.in/sites/default/files/<YYYY-MM-01>.pdf` (old Drupal site). One tested URL (`2024-11-01.pdf`) returns **HTTP 404**. Treat legacy links as dead. |
| robots.txt | `User-agent: * / Allow: /` (checked 2026-10-21) |
| Copyright policy | "Material featured on this Website may be reproduced free of charge. However, the material has to be reproduced accurately and not to be used in a derogatory manner or in a misleading context. Wherever the material is being published or issued to others, the source must be prominently acknowledged." (CMS page id 3202) |
| Authentication | None required for the `wp/v2` endpoints used. The front-end bundle also ships an API key for a custom endpoint; **it was not used** (see DESIGN.md D-001). |

**Implication of the copyright policy for this project:** reuse is allowed with attribution and accurate reproduction.
"Not … in a misleading context" reinforces the project rule that backlog figures must not be presented as proof of
negligence.

### Secondary / corroborating sources (not used as data)
- PIB press releases and `static.pib.gov.in` PDF copies of some reports were found in search results. Not downloaded or verified.
- News coverage (e.g. newsonair.gov.in, May 2026) refers to the April 2026 report as the "48th". This is consistent with the numbering inferred below.
- A data.gov.in structured CPGRAMS dataset was **not found** in one search. Treat it as unverified/absent.

---

## 2. Discovery results (counts are from actual work)

| Step | Count |
|---|---|
| CMS catalogue entries matching "monthly report" (`central_documents`) | 330 (all report types: Centre, States/UTs, RTI, NeSDA, Secretariat Reforms, …) |
| Entries with Centre-like titles, plus extra entries found by month-specific searches | 76 candidate entries considered |
| Candidate media IDs resolved to a PDF URL via the API | 76 / 76 |
| PDFs downloaded and confirmed to start with `%PDF` | 59 |
| Downloaded files discarded as not being CPGRAMS Centre reports (mislabelled NeSDA / Secretariat Reforms) | 3 |
| Downloaded file removed as a duplicate (text identical on all 35 pages to another June 2022 file) | 1 |
| Entries not downloaded because their byte size equals an already downloaded file (presumed duplicate; checksum **not** verified) | 17 |
| **PDFs retained in `data/raw/pdfs/`** | **55** (53 English + 2 Hindi editions) |
| PDFs opened with pdfplumber (cover + full-text section sweep) | 56 (all retained files, plus the duplicate before removal) |
| Reports whose tables were inspected page by page | 9 (see §4) |

### Series coverage
- The series is **numbered continuously from Report #1 (May 2022) to Report #53 (September 2026)**. All 53 months
  are available in English, so there are **no gaps**.
- The most recent 36 months (October 2023 to September 2026, #18 to #53) are complete.
- November 2025 (#43) and January 2026 (#45) each appear twice on the site: once as an English-slot entry that contains
  the **Hindi** edition, and once as a separate English entry. Both editions were downloaded.
- For 14 reports the report number is not legible in the text layer (image covers, or doubled glyphs). Their
  numbers are inferred from the continuous sequence and flagged in `data/manifest.csv`.

### CMS metadata is unreliable — do not trust titles or dates
- Titles vary ("CPGRAMS Monthly Report – Centre-…", "DARPG Monthly Report Central, …", "DARPG Monthly Report, …",
  bare "April 2024") and some are mislabelled (3 entries were other DARPG reports).
- `acf.file_date` is sometimes wrong (e.g. `19840801` on a States report, and `20250501` on an entry titled September 2025).
- Most archive files (2022–Oct 2025) were re-uploaded around Oct–Dec 2025, so CMS post dates are **not** publication
  dates. The manifest therefore leaves `publication_date` blank.
- Report month and number were assigned only after reading the cover or key-highlights page of each file.

---

## 3. Per-report inventory

Generated from `data/manifest.csv` and a regex sweep of each PDF's text layer. "yes" means the section heading text
was found. "—" means it was not found, which can be a regex miss and is not proof of absence; #1 and #9 need manual
confirmation. The column "Printed−PDF page" is the printed page label minus the 1-based physical PDF page number.

| Month | # | Lang | Pages | Printed−PDF page | Cut-off | Month-wise overall | >1000 pending | Long-pending (threshold) | Avg closing time | GRAI/GRI ranking | File |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 2022-05 | 1 | en | 31 | +0 | day 25 | — | yes | 45 d | yes | — | `cpgrams_centre_2022-05_r01_en.pdf` |
| 2022-06 | 2 | en | 35 | +0 | day 25 | yes | yes | 45 d | yes | — | `cpgrams_centre_2022-06_r02_en.pdf` |
| 2022-07 | 3 | en | 30 | +0 | day 25 | yes | yes | 45 d | yes | — | `cpgrams_centre_2022-07_r03_en.pdf` |
| 2022-08 | 4 | en | 29 | +0 | day 25 | yes | yes | 30 d | yes | yes | `cpgrams_centre_2022-08_r04_en.pdf` |
| 2022-09 | 5 | en | 29 | +0 | day 25 | yes | yes | 30 d | yes | yes | `cpgrams_centre_2022-09_r05_en.pdf` |
| 2022-10 | 6 | en | 25 | +0 | day 25 | yes | yes | 30 d | yes | yes | `cpgrams_centre_2022-10_r06_en.pdf` |
| 2022-11 | 7 | en | 21 | +0 | day 25 | yes | yes | 30 d | yes | yes | `cpgrams_centre_2022-11_r07_en.pdf` |
| 2022-12 | 8 | en | 27 | +0 | day 25 | yes | yes | 30 d | yes | yes | `cpgrams_centre_2022-12_r08_en.pdf` |
| 2023-01 | 9 | en | 27 | +0 | day 24 | — | yes | 30 d | yes | yes | `cpgrams_centre_2023-01_r09_en.pdf` |
| 2023-02 | 10 | en | 28 | +0 | day 25 | yes | yes | 30 d | yes | yes | `cpgrams_centre_2023-02_r10_en.pdf` |
| 2023-03 | 11 | en | 31 | +0 | day 25 | yes | yes | 30 d | yes | yes | `cpgrams_centre_2023-03_r11_en.pdf` |
| 2023-04 | 12 | en | 31 | +0 | month-end | yes | yes | 30 d | yes | yes | `cpgrams_centre_2023-04_r12_en.pdf` |
| 2023-05 | 13 | en | 40 | +0 | month-end | yes | yes | 30 d | yes | yes | `cpgrams_centre_2023-05_r13_en.pdf` |
| 2023-06 | 14 | en | 44 | +0 | month-end | yes | yes | 30 d | yes | yes | `cpgrams_centre_2023-06_r14_en.pdf` |
| 2023-07 | 15 | en | 37 | +0 | month-end | yes | yes | 30 d | yes | yes | `cpgrams_centre_2023-07_r15_en.pdf` |
| 2023-08 | 16 | en | 35 | +0 | month-end | yes | yes | 30 d | yes | yes | `cpgrams_centre_2023-08_r16_en.pdf` |
| 2023-09 | 17 | en | 43 | +0 | month-end | yes | yes | 30 d | yes | yes | `cpgrams_centre_2023-09_r17_en.pdf` |
| 2023-10 | 18 | en | 43 | +0 | month-end | yes | yes | 30 d | yes | yes | `cpgrams_centre_2023-10_r18_en.pdf` |
| 2023-11 | 19 | en | 39 | +0 | month-end | yes | yes | 30 d | yes | yes | `cpgrams_centre_2023-11_r19_en.pdf` |
| 2023-12 | 20 | en | 39 | +0 | month-end | yes | yes | 30 d | yes | yes | `cpgrams_centre_2023-12_r20_en.pdf` |
| 2024-01 | 21 | en | 35 | +0 | month-end | yes | yes | 30 d | yes | yes | `cpgrams_centre_2024-01_r21_en.pdf` |
| 2024-02 | 22 | en | 41 | +0 | month-end | yes | yes | 30 d | yes | yes | `cpgrams_centre_2024-02_r22_en.pdf` |
| 2024-03 | 23 | en | 49 | +0 | month-end | yes | yes | 30 d | yes | yes | `cpgrams_centre_2024-03_r23_en.pdf` |
| 2024-04 | 24 | en | 40 | +0 | month-end | yes | yes | 30 d | yes | yes | `cpgrams_centre_2024-04_r24_en.pdf` |
| 2024-05 | 25 | en | 45 | +0 | month-end | yes | yes | 30 d | yes | yes | `cpgrams_centre_2024-05_r25_en.pdf` |
| 2024-06 | 26 | en | 49 | +0 | month-end | yes | yes | 30 d | yes | yes | `cpgrams_centre_2024-06_r26_en.pdf` |
| 2024-07 | 27 | en | 36 | +0 | month-end | yes | yes | 30 d | yes | yes | `cpgrams_centre_2024-07_r27_en.pdf` |
| 2024-08 | 28 | en | 32 | +0 | month-end | yes | yes | 30 d | yes | yes | `cpgrams_centre_2024-08_r28_en.pdf` |
| 2024-09 | 29 | en | 38 | +0 | month-end | yes | yes | 21 d | yes | yes | `cpgrams_centre_2024-09_r29_en.pdf` |
| 2024-10 | 30 | en | 41 | +0 | month-end | yes | yes | 21 d | yes | yes | `cpgrams_centre_2024-10_r30_en.pdf` |
| 2024-11 | 31 | en | 43 | +0 | month-end | yes | yes | 21 d | yes | yes | `cpgrams_centre_2024-11_r31_en.pdf` |
| 2024-12 | 32 | en | 46 | +0 | month-end | yes | yes | 21 d | yes | yes | `cpgrams_centre_2024-12_r32_en.pdf` |
| 2025-01 | 33 | en | 38 | +0 | month-end | yes | yes | 21 d | yes | yes | `cpgrams_centre_2025-01_r33_en.pdf` |
| 2025-02 | 34 | en | 38 | +0 | month-end | yes | yes | 21 d | yes | yes | `cpgrams_centre_2025-02_r34_en.pdf` |
| 2025-03 | 35 | en | 47 | +0 | day 28 | yes | yes | 21 d | yes | yes | `cpgrams_centre_2025-03_r35_en.pdf` |
| 2025-04 | 36 | en | 49 | +0 | month-end | yes | yes | 21 d | yes | yes | `cpgrams_centre_2025-04_r36_en.pdf` |
| 2025-05 | 37 | en | 43 | +0 | month-end | yes | yes | 21 d | yes | yes | `cpgrams_centre_2025-05_r37_en.pdf` |
| 2025-06 | 38 | en | 45 | +0 | month-end | yes | yes | 21 d | yes | yes | `cpgrams_centre_2025-06_r38_en.pdf` |
| 2025-07 | 39 | en | 50 | +0 | month-end | yes | yes | 21 d | yes | yes | `cpgrams_centre_2025-07_r39_en.pdf` |
| 2025-08 | 40 | en | 44 | +0 | month-end | yes | yes | 21 d | yes | yes | `cpgrams_centre_2025-08_r40_en.pdf` |
| 2025-09 | 41 | en | 41 | -1 | month-end | yes | yes | 21 d | yes | yes | `cpgrams_centre_2025-09_r41_en.pdf` |
| 2025-10 | 42 | en | 37 | -1 | month-end | yes | yes | 21 d | yes | yes | `cpgrams_centre_2025-10_r42_en.pdf` |
| 2025-11 | 43 | en | 37 | -1 | month-end | yes | yes | 21 d | yes | yes | `cpgrams_centre_2025-11_r43_en.pdf` |
| 2025-11 | 43 | hi | 37 | n/a | n/a | — | — | — | — | — | `cpgrams_centre_2025-11_r43_hi.pdf` |
| 2025-12 | 44 | en | 39 | -1 | month-end | yes | yes | 21 d | yes | yes | `cpgrams_centre_2025-12_r44_en.pdf` |
| 2026-01 | 45 | en | 38 | -2 | month-end | yes | yes | 21 d | yes | yes | `cpgrams_centre_2026-01_r45_en.pdf` |
| 2026-01 | 45 | hi | 40 | n/a | n/a | — | — | — | — | — | `cpgrams_centre_2026-01_r45_hi.pdf` |
| 2026-02 | 46 | en | 45 | -2 | not checked | yes | yes | 21 d | yes | yes | `cpgrams_centre_2026-02_r46_en.pdf` |
| 2026-03 | 47 | en | 54 | -2 | not checked | yes | yes | 21 d | yes | yes | `cpgrams_centre_2026-03_r47_en.pdf` |
| 2026-04 | 48 | en | 64 | -2 | not checked | yes | yes | 21 d | yes | yes | `cpgrams_centre_2026-04_r48_en.pdf` |
| 2026-05 | 49 | en | 75 | -2 | not checked | yes | yes | 21 d | yes | yes | `cpgrams_centre_2026-05_r49_en.pdf` |
| 2026-06 | 50 | en | 66 | -2 | not checked | yes | yes | 21 d | yes | yes | `cpgrams_centre_2026-06_r50_en.pdf` |
| 2026-07 | 51 | en | 64 | -2 | not checked | yes | yes | 21 d | yes | yes | `cpgrams_centre_2026-07_r51_en.pdf` |
| 2026-08 | 52 | en | 65 | -2 | not checked | yes | yes | 21 d | yes | yes | `cpgrams_centre_2026-08_r52_en.pdf` |
| 2026-09 | 53 | en | 73 | -2 | month-end | yes | yes | 21 d | yes | yes | `cpgrams_centre_2026-09_r53_en.pdf` |

The regex sweep only searched the Hindi editions for English headings, so their "—" cells are expected.

---

## 4. Reports inspected page by page

| Report | PDF pages inspected (1-based physical) | Tables / sections observed |
|---|---|---|
| #1 May 2022 | 1–3 (TOC), 6, 18 | Overview (BF/Receipts/Disposed/Pending, 01/01/2022–25/05/2022); age-wise pendency (>1 yr, >6 m, >45 d, ≤45 d); Top-10 max/min receipts/disposal/pendency; >1000 pending; Top-10 pending >45 days; category-wise status (23 categories) and Top-10 by category pendency; high/low average closing time; PG officers; ATR; appeals; root-cause examples |
| #4 Aug 2022 | 5, 9–10 | Key highlights with monthly PG cases (received 79,674; redressed 90,287; pending 91,677). **Grievance Redressal Index (GRI)**: Group A = total grievances >2,500; score shown as a percentage; data window 1 Mar 2022 – 25 Aug 2022 |
| #5 Sep 2022 | 6, 24 | Month-wise Jan–Sep 2022 receipts/disposal/pending (chart data labels); Top-10 max pendency (YTD) |
| #9 Jan 2023 | 5, 18, 21 | Narrative highlights; >1000 pending list "as on 24th January, 2023" |
| #16 Aug 2023 | 1–3 (TOC), 6–7, 9, 16, 25–26 | Overview + month-wise Jan–Aug 2023 (note: "as per the previously published Monthly Reports"); age-wise pendency/disposal (≤30 d, >30 d, >6 m, >1 yr); **GRAI** with 4 dimensions and 12 indicators, Group A ≥500 grievances; appeals month-wise; Annexures 1.1–1.6 (YTD), 2.1/2.2 average closing time, 3 PG officers, 4 ATR, 5 appeals, 6 root cause |
| #28 Aug 2024 | 15 | GRAI Group A ranking table **now includes per-ministry Brought Forward / Receipts / Disposal / Pending** plus GRAI score and rank |
| #32 Dec 2024 | 2–4 | TOC (adds user registration, CSC outreach, passport analysis, taxonomy, success stories); two-column highlights page |
| #40 Aug 2025 | 2–4, 7, 9, 35–36 | Month-wise Jan–Aug 2025; GRAI ranking with BF/R/D/P; Annexure 1.3 >1000 pending (21 rows; pdfplumber detected one 25-row table); 1.4 Top-10 pending >21 days; 2.1 high average closing time |
| #53 Sep 2026 | 2–4, 8, 25, 32, 50, 54–59 | GRAI indicator formula table; **"Data Used for Score Calculation"** wide table (~22 numeric columns per ministry); GRAI dimension scores and rank; month-wise Jan–Sep 2026; appeals; Annexures 1.1–1.4, 2.1/2.2 (now restricted to ministries with >100 disposals), 3.1/3.2 appeals |

Cross-report consistency check (performed): the January–August 2025 overall month-wise receipts, disposal and pending
values are **identical** in reports #40, #41 and #44. In this sample, no silent revision of earlier months was observed.

---

## 5. Metric availability and meaning

| Metric | Where | Availability | Semantics / caveats |
|---|---|---|---|
| Overall monthly receipts, disposals, pending (Central Secretariat aggregate) | "Month-wise Status of Grievances" chart data labels, plus highlights text | Every report from #2 onward (year-to-date months each time); #1 overview only | Month flows and month-end stock. **Cut-off was the 25th (24th in Jan 2023) until #11 (Mar 2023), month-end from #12 (Apr 2023)**, and 28 Mar in #35. Pre-Apr-2023 "months" are not calendar months. The wording changes from "grievances" to "PG cases" over time; whether the scope is identical is *unverified*. |
| Overall YTD brought-forward, receipts, disposed, pending | "Overview" box | Most reports | Cumulative from 1 January. |
| Age-wise pendency/disposal (aggregate) | Section 3 (2023) | Early and mid series; later reports give "~X% pending < 21 days" in the highlights | Bucket boundaries change (45 → 30 → 21 days). |
| Ministries with >1000 pending grievances | Annexure 1.3 / 1.5 | **All 53 English reports** (threshold wording constant) | Rows give **YTD** BF, receipts, total, disposal and **pending at cut-off**. Pending is a point-in-time stock and is comparable month to month. Receipts and disposal are cumulative since 1 January and reset each January. Membership is **censored**: ministries ≤1000 are not observed. Verified identity: BF + Receipts − Disposal = Pending (CBDT, Aug 2023: 13,036 + 46,711 − 48,237 = 11,510). |
| Top-10 ministries with grievances pending > N days | Annexure 1.4 / 1.6 | All 53 | **Threshold changes: 45 days (#1–#3), 30 days (#4–#28), 21 days (#29–#53).** Top-10 only. Comparable only within a threshold segment. |
| Top-10 receipts / disposals | Annexure 1.1 / 1.2 | All | YTD. Top-10 only. |
| Average closing time (days) | Annexure 2.1 (high) / 2.2 (low) | All 53 | **Top-10 and bottom-10 only, YTD.** From #53 the list is restricted to ministries with >100 disposals (the start month of this restriction is *not determined*). The overall average (e.g. "13 days" for 2024) appears in the highlights. **Not available for every ministry every month.** |
| GRI / GRAI score and rank (Group A, Group B) | Section "Grievance Redressal (Assessment and) Index" | #4 onward | **Methodology breaks:** GRI (percentage score, Group A >2,500, cumulative window) at #4; GRAI with 4 dimensions and 12 indicators and Group A ≥500 observed by #16 (exact start not determined); per-ministry BF/R/D/P columns observed in #28 (absent in #22); **normalisation removed from Feb 2026 (#46)**, as stated in #53. Ranks are comparable only within a methodology segment and a group. |
| Per-ministry monthly BF / receipts / disposal / pending for *all ranked ministries* (~90) | GRAI ranking tables (#28 to ~#45); "Data Used for Score Calculation" (#46+) | Partial (start month to be confirmed) | Potentially the best ministry-level panel. GRAI data window is the single month (e.g. "1st September to 30th September, 2026"). One implausible-looking value was noticed (Dept of Rural Development, 122,321 receipts, Aug 2024) and needs validation. |
| Category-level status | "Category-wise Status" (23 categories) | **#1–#3 only** | Later reports have narrative "Categorisation", "Taxonomy" (2 departments per month) and "PRIORITY AREA" chapters (#48+). Not assessed in detail. |
| Appeals (overall + top-10) | Appeals sections / Annexure 3 / 5 | Most reports | Not core to this project. |
| Feedback, user registrations, CSC, media, success stories | Narrative chapters | Varies | Out of scope. |

---

## 6. Format changes over time (eras)

| Era | Reports | Characteristics |
|---|---|---|
| E1 | #1–#3 (May–Jul 2022) | "CPGRAMS Dashboard" layout; category-wise tables; >45-day list; 25th-of-month cut-off; no ranking |
| E2 | #4–#11 (Aug 2022–Mar 2023) | GRI ranking introduced; >30-day list; category tables dropped; 25th cut-off |
| E3 | #12–~#27 (Apr 2023–Jul 2024) | Month-end cut-off; GRAI 4-dimension model, Group A ≥500 (exact start between #11 and #16 not determined); ranking table has scores only |
| E4 | ~#28–#45 (Aug 2024–Jan 2026) | Ranking table adds per-ministry BF/R/D/P; >21-day list from #29; printed page offset −1 from #41 and −2 from #45 |
| E5 | #46–#53 (Feb 2026–Sep 2026) | GRAI normalisation removed; wide "Data Used for Score Calculation" tables; reports 45–75 pages; image-only cover page |

---

## 7. PDF page numbering

- The printed footer label ("N | Page") differs from the physical PDF page: printed = physical for #1–#40,
  physical − 1 for #41–#44, and physical − 2 for #45–#53. In #45–#53 the cover is printed "0", and the TOC page
  is unnumbered or also "0".
- **Recommendation (D-004):** record `pdf_page` (1-based physical index, as shown by PDF viewers) as the primary
  provenance key, and `printed_page_label` as a secondary field. Viewers such as Edge or Acrobat jump to the
  physical page, so this is the number a reviewer can verify.

---

## 8. Known extraction risks (all observed, not hypothetical)

1. **Wrapped ministry names**: names split across 2–3 lines, with the serial number and figures on the middle line (e.g. "Department of Financial Services (Insurance / 10 1096 29377 … / Division)").
2. **Tables continuing across pages** without repeating the annexure heading (e.g. Annexure 1.2, #53).
3. **Words split mid-token** by narrow columns ("Telecommunication s", "Departme nt").
4. **Very wide GRAI data tables** (#46+), ~22 numeric columns with fragmented multi-line headers. Highest-risk parser.
5. **Overall monthly series exist only as chart data labels**; chart axis text is interleaved ("s e100000 c n a").
6. **Doubled glyphs** (fake-bold) on 2024 covers, e.g. "JJuullyy 22002244" and "[[RReeppoorrtt NNuummbbeerr 2234]]" (April 2024; true number #24).
7. **Image-only covers** (#45–#53): report number and month are not in the text layer.
8. **Two-column narrative pages** (#32 highlights) are read interleaved by pdfplumber.
9. **Ministry name variants**: "Ministry of Railways ( Railway Board)" vs "Ministry of Railways (Railway Board)"; "Department of Financial Service" vs "Services"; "Telecommunications" split.
10. **Hindi editions** use legacy font encoding (e.g. "माससक" instead of "मासिक"); the text layer is unreliable. English editions exist for all months, so the Hindi ones are not needed.
11. **Number formats**: narrative uses Indian digit grouping ("1,17,061"); tables use plain integers; GRAI scores are decimals with variable precision.
12. **Definition drift**: cut-off date, long-pending threshold, GRAI groups and methodology, and the "grievances" vs "PG cases" wording (§5, §6).

---

## 9. Suitability for longitudinal analysis — summary

| Candidate analysis | Verdict | Basis |
|---|---|---|
| A. Overall receipts/disposal/pending trends | **Supported** | Monthly series in every report. Use **Apr 2023–Sep 2026 (42 month-end points)** as the core window; earlier months have a different cut-off and must be flagged |
| B. Repeated >1000-pending appearances (count and consecutive runs) | **Supported** | Constant threshold, 53 consecutive months, no gaps |
| B′. Long-pending Top-10 appearances | **Supported within threshold segments only** | 45/30/21-day changes |
| C. Backlog change (MoM, %, rolling 3-month) | **Supported** for the overall series and for ministries while they are on the >1000 list (censored); **stretch** for the full ministry panel from GRAI tables |
| D. Ranking stability | **Partially supported** | Only within a GRAI methodology segment and group; group membership changes monthly |
| E. Mann–Kendall trend tests | **Supported with caveats** | 42-point overall series; ministry-level only where the series is long enough; autocorrelation → modified MK; multiple-testing correction needed |
| F. Seasonality | **Not supported for claims** | ~3.5 comparable years. Descriptive month-of-year view only |
| G. Demand vs backlog | **Partially supported** | Needs ministry receipts and pending together: available for listed ministries (YTD differencing) or the full panel if T6 succeeds |
| Published average disposal time per ministry | **Limited** | Top-/bottom-10 YTD only |
| Category-level analysis | **Drop** | Tables in #1–#3 only |
| Ministry performance panel in the app | **Supported with coverage warnings** | Show >1000-list history, GRAI rank (within segment), and closing time only when listed |

---

## 10. Phase 2 corrections and additions (2026-10-21)

Phase 2 parsed every report in full. Where that contradicts or sharpens the Phase 0 findings above,
**this section takes precedence**. Phase 0 text is kept unchanged as the record of what was known then.

| Phase 0 statement | Phase 2 finding |
|---|---|
| Per-ministry BF/R/D/P columns in the GRAI table were "observed in #28 (absent in #22)"; start month undetermined | They first appear in **#26 (Jun 2024)**, in a one-off layout with Total Grievances printed first, and run to **#45 (Jan 2026)**. From #46 the ranking table carries dimension scores only |
| The 4-dimension GRAI was "observed by #16 (exact start not determined)" | It starts in **#13 (May 2023)**. #4–#12 use the GRI percentage score. #15 (Jul 2023) prints Receipts and Disposal instead of Total Grievances |
| GRI Group A threshold ">2,500, later ≥500" | #4–#8: >2,500; #9: 500; #10: 1,000; #11–#12: 1,500; #13 onward: ≥500 |
| GRAI data window | Stated in every report from #4: rolling ~6 months ending on the 25th (#4–#8), year-to-date (#9–#12), single month from #13, except **#28: 1 Jul – 31 Aug 2024** |
| Feb–Aug 2026 cut-offs "not checked" | Detected from the report text: month-end in all of them |
| "Data Used for Score Calculation" (#46+) is the T6 source | It appears from **#49 (May 2026)**. #46–#48 print a different "Indicator Wise Scores Calculation" table. The "Data Used" figures fail validation against the annexures (D-017) |
| Month-wise series on every report | 13 reports print only charts (#1, #2, #9, #10, #18–#21, #28–#32). Some reports print rolling 6-month windows. Every month Jan 2022 – Sep 2026 has at least one clean data table (D-018) |
| — | The 2022 long-pending table in #2 has no Total column ("Total Received / Total Disposal / Total Pending" are qualifiers) |
| — | #53 Annexure 1.4 prints `####` for one cell (Ministry of Petroleum and Natural Gas, > 21 days) |

Extraction results and their verification: [extraction_summary.md](extraction_summary.md) (generated) and
[extraction_verification.md](extraction_verification.md).
