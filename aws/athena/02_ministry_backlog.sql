-- 02_ministry_backlog.sql  (Athena / Trino)
-- Ministry backlog ranking: backlog_months = mean month-end pending / mean monthly receipts over the
-- single-month flow window (#29-#45), i.e. months of intake waiting. Size-neutral, unlike raw pending.
-- Port of src/export/powerbi.py _backlog_growth and the scorecard component: a ministry needs at least
-- 15 reports in the window (MIN_POINTS_DEMAND_BACKLOG) and positive mean receipts.
-- Ranked overall and within its 2026 GRAI volume group (A >= 500 grievances, B fewer), 1 = largest backlog.
-- Self-check: backlog_months must equal the published backlog_growth.backlog_months.
WITH per_ministry AS (
    SELECT ministry_id,
           COUNT(*) AS reports_in_window,
           AVG(CAST(pending AS DOUBLE)) AS mean_pending,
           AVG(CAST(receipts AS DOUBLE)) AS mean_monthly_receipts,
           MAX(CASE WHEN report_number = 45 THEN pending END) AS pending_latest
    FROM grievance_lens.v_flow_panel
    GROUP BY ministry_id
),
scored AS (
    SELECT p.*,
           CASE WHEN p.reports_in_window >= 15 AND p.mean_monthly_receipts > 0
                THEN p.mean_pending / p.mean_monthly_receipts END AS backlog_months
    FROM per_ministry p
)
SELECT s.ministry_id,
       d.canonical_name,
       d.grai_group_2026,
       s.reports_in_window,
       s.mean_monthly_receipts,
       s.mean_pending,
       s.pending_latest,
       s.backlog_months,
       RANK() OVER (ORDER BY s.backlog_months DESC) AS backlog_rank,
       RANK() OVER (PARTITION BY d.grai_group_2026 ORDER BY s.backlog_months DESC) AS backlog_rank_in_group,
       RANK() OVER (ORDER BY s.pending_latest DESC) AS pending_rank_latest,
       (ABS(s.backlog_months - b.backlog_months) < 1e-9
        OR (s.backlog_months IS NULL AND b.backlog_months IS NULL)) AS matches_published
FROM scored s
JOIN grievance_lens.dim_ministry d ON d.ministry_id = s.ministry_id
LEFT JOIN grievance_lens.backlog_growth b ON b.ministry_id = s.ministry_id
WHERE s.backlog_months IS NOT NULL
ORDER BY backlog_rank, s.ministry_id
