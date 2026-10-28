-- benchmark_queries.sql  (Athena / Trino)  CSV vs Parquet, run by aws/benchmarks/benchmark_runner.py.
-- Each pair computes the same result from the raw CSV batch (grievance_lens_raw, every column STRING,
-- empty field = '') and from the curated Parquet tables (grievance_lens, typed). The runner checks that
-- both members of a pair return identical rows before it reports any timing.
-- Blocks start with '-- name: <id>_<csv|parquet>' and '-- measures: <what the pair isolates>'.

-- name: b1_csv
-- measures: column pruning on the largest table (grai_ranks, 15 columns; the query needs 3)
SELECT grai_group,
       grai_methodology,
       COUNT(*) AS rankings,
       AVG(TRY_CAST(NULLIF(rank_percentile, '') AS DOUBLE)) AS mean_rank_percentile
FROM grievance_lens_raw.grai_ranks
GROUP BY grai_group, grai_methodology
ORDER BY grai_group, grai_methodology;

-- name: b1_parquet
-- measures: column pruning on the largest table (grai_ranks, 15 columns; the query needs 3)
SELECT grai_group,
       grai_methodology,
       COUNT(*) AS rankings,
       AVG(rank_percentile) AS mean_rank_percentile
FROM grievance_lens.grai_ranks
GROUP BY grai_group, grai_methodology
ORDER BY grai_group, grai_methodology;

-- name: b2_csv
-- measures: partition pruning (ministry_monthly is partitioned by period_year; CSV must read everything)
SELECT TRY_CAST(NULLIF(ministry_id, '') AS INTEGER) AS ministry_id,
       SUM(TRY_CAST(NULLIF(receipts, '') AS INTEGER)) AS receipts_2025,
       SUM(TRY_CAST(NULLIF(disposals, '') AS INTEGER)) AS disposals_2025
FROM grievance_lens_raw.ministry_monthly
WHERE year(TRY_CAST(NULLIF(period_start, '') AS DATE)) = 2025
GROUP BY 1
ORDER BY 1;

-- name: b2_parquet
-- measures: partition pruning (ministry_monthly is partitioned by period_year; CSV must read everything)
SELECT ministry_id,
       SUM(receipts) AS receipts_2025,
       SUM(disposals) AS disposals_2025
FROM grievance_lens.ministry_monthly
WHERE period_year = 2025
GROUP BY 1
ORDER BY 1;

-- name: b3_csv
-- measures: fact-to-dimension join with a selective filter (top 10 bodies on the >1,000-pending list)
SELECT d.canonical_name,
       COUNT(*) AS reports_listed,
       MAX(TRY_CAST(NULLIF(la.pending, '') AS INTEGER)) AS max_pending
FROM grievance_lens_raw.list_appearances la
JOIN grievance_lens_raw.dim_ministry d ON d.ministry_id = la.ministry_id
WHERE la.list_code = 'pending_over_1000'
GROUP BY d.canonical_name
ORDER BY reports_listed DESC, d.canonical_name
LIMIT 10;

-- name: b3_parquet
-- measures: fact-to-dimension join with a selective filter (top 10 bodies on the >1,000-pending list)
SELECT d.canonical_name,
       COUNT(*) AS reports_listed,
       MAX(la.pending) AS max_pending
FROM grievance_lens.list_appearances la
JOIN grievance_lens.dim_ministry d ON d.ministry_id = la.ministry_id
WHERE la.list_code = 'pending_over_1000'
GROUP BY d.canonical_name
ORDER BY reports_listed DESC, d.canonical_name
LIMIT 10;

-- name: b4_csv
-- measures: a complete analysis (backlog_months, analysis C) with casts, filters, aggregation and a join
WITH w AS (
    SELECT TRY_CAST(NULLIF(ministry_id, '') AS INTEGER) AS ministry_id,
           TRY_CAST(NULLIF(pending, '') AS DOUBLE) AS pending,
           TRY_CAST(NULLIF(receipts, '') AS DOUBLE) AS receipts
    FROM grievance_lens_raw.ministry_monthly
    WHERE TRY_CAST(NULLIF(report_number, '') AS INTEGER) BETWEEN 29 AND 45
)
SELECT d.canonical_name,
       COUNT(*) AS reports_in_window,
       AVG(w.pending) / AVG(w.receipts) AS backlog_months
FROM w
JOIN grievance_lens_raw.dim_ministry d ON TRY_CAST(NULLIF(d.ministry_id, '') AS INTEGER) = w.ministry_id
GROUP BY d.canonical_name
HAVING COUNT(*) >= 15 AND AVG(w.receipts) > 0
ORDER BY backlog_months DESC, d.canonical_name;

-- name: b4_parquet
-- measures: a complete analysis (backlog_months, analysis C) with casts, filters, aggregation and a join
WITH w AS (
    SELECT ministry_id,
           CAST(pending AS DOUBLE) AS pending,
           CAST(receipts AS DOUBLE) AS receipts
    FROM grievance_lens.ministry_monthly
    WHERE report_number BETWEEN 29 AND 45
)
SELECT d.canonical_name,
       COUNT(*) AS reports_in_window,
       AVG(w.pending) / AVG(w.receipts) AS backlog_months
FROM w
JOIN grievance_lens.dim_ministry d ON d.ministry_id = w.ministry_id
GROUP BY d.canonical_name
HAVING COUNT(*) >= 15 AND AVG(w.receipts) > 0
ORDER BY backlog_months DESC, d.canonical_name;
