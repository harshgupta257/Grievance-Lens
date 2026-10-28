-- 03_rolling_average.sql  (Athena / Trino)
-- Rolling three-month means of receipts, disposals and month-end pending (Central Secretariat aggregate).
-- Port of analysis A: rolling(3, min_periods=3) over complete months only, so a window touching a month
-- with only a partial (pre-cut-off) value has no average rather than a distorted one.
-- overall_trend has one row per calendar month with no gaps (dim_month is continuous), so 2 PRECEDING
-- rows are exactly the two previous months.
-- Self-check: the three averages must equal the published *_roll3 columns.
WITH masked AS (
    SELECT month,
           has_partial_value,
           receipts_roll3 AS published_receipts_roll3,
           disposals_roll3 AS published_disposals_roll3,
           pending_roll3 AS published_pending_roll3,
           CASE WHEN has_partial_value THEN NULL ELSE CAST(receipts AS DOUBLE) END AS receipts_c,
           CASE WHEN has_partial_value THEN NULL ELSE CAST(disposals AS DOUBLE) END AS disposals_c,
           CASE WHEN has_partial_value THEN NULL ELSE CAST(pending AS DOUBLE) END AS pending_c
    FROM grievance_lens.overall_trend
),
rolled AS (
    SELECT month,
           has_partial_value,
           CASE WHEN COUNT(receipts_c) OVER (ORDER BY month ROWS BETWEEN 2 PRECEDING AND CURRENT ROW) = 3
                THEN AVG(receipts_c) OVER (ORDER BY month ROWS BETWEEN 2 PRECEDING AND CURRENT ROW) END AS receipts_roll3,
           CASE WHEN COUNT(disposals_c) OVER (ORDER BY month ROWS BETWEEN 2 PRECEDING AND CURRENT ROW) = 3
                THEN AVG(disposals_c) OVER (ORDER BY month ROWS BETWEEN 2 PRECEDING AND CURRENT ROW) END AS disposals_roll3,
           CASE WHEN COUNT(pending_c) OVER (ORDER BY month ROWS BETWEEN 2 PRECEDING AND CURRENT ROW) = 3
                THEN AVG(pending_c) OVER (ORDER BY month ROWS BETWEEN 2 PRECEDING AND CURRENT ROW) END AS pending_roll3,
           published_receipts_roll3,
           published_disposals_roll3,
           published_pending_roll3
    FROM masked
)
SELECT month,
       has_partial_value,
       receipts_roll3,
       disposals_roll3,
       pending_roll3,
       ((ABS(receipts_roll3 - published_receipts_roll3) < 1e-6
         OR (receipts_roll3 IS NULL AND published_receipts_roll3 IS NULL))
        AND (ABS(disposals_roll3 - published_disposals_roll3) < 1e-6
         OR (disposals_roll3 IS NULL AND published_disposals_roll3 IS NULL))
        AND (ABS(pending_roll3 - published_pending_roll3) < 1e-6
         OR (pending_roll3 IS NULL AND published_pending_roll3 IS NULL))) AS matches_published
FROM rolled
ORDER BY month
