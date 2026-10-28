-- 01_month_over_month.sql  (Athena / Trino)
-- Month-over-month change of the Central Secretariat aggregate: grievances received and month-end pending.
-- Port of analysis A (src/analysis/analyses.py):
--   * values from months with only a partial (pre-cut-off) figure are masked before differencing;
--   * the percentage is shown only when the previous month is at least 100 (MIN_BASE_FOR_PCT_CHANGE).
-- Self-check: pending_change must equal the published overall_trend.pending_change on every row.
WITH monthly AS (
    SELECT month,
           receipts,
           pending,
           has_partial_value,
           pending_change AS published_pending_change,
           pending_change_pct AS published_pending_change_pct,
           CASE WHEN has_partial_value THEN NULL ELSE receipts END AS receipts_complete,
           CASE WHEN has_partial_value THEN NULL ELSE pending END AS pending_complete
    FROM grievance_lens.overall_trend
),
lagged AS (
    SELECT m.*,
           LAG(receipts_complete) OVER (ORDER BY month) AS receipts_prev,
           LAG(pending_complete) OVER (ORDER BY month) AS pending_prev
    FROM monthly m
),
changes AS (
    SELECT month,
           has_partial_value,
           receipts,
           receipts_prev,
           receipts_complete - receipts_prev AS receipts_mom_change,
           CASE WHEN receipts_prev >= 100
                THEN 100 * CAST(receipts_complete - receipts_prev AS DOUBLE) / receipts_prev END AS receipts_mom_pct,
           pending,
           pending_prev,
           pending_complete - pending_prev AS pending_change,
           CASE WHEN pending_prev >= 100
                THEN 100 * CAST(pending_complete - pending_prev AS DOUBLE) / pending_prev END AS pending_change_pct,
           published_pending_change,
           published_pending_change_pct
    FROM lagged
)
SELECT month,
       has_partial_value,
       receipts,
       receipts_prev,
       receipts_mom_change,
       receipts_mom_pct,
       pending,
       pending_prev,
       pending_change,
       pending_change_pct,
       (pending_change = published_pending_change
        OR (pending_change IS NULL AND published_pending_change IS NULL)) AS matches_published
FROM changes
ORDER BY month
