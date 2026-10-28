-- 05_demand_vs_backlog.sql  (Athena / Trino)
-- Demand vs backlog per ministry: is intake (receipts) rising, and is the backlog (month-end pending) rising
-- faster or slower? Port of analysis G (src/analysis/analyses.py analysis_g, src/analysis/config.py):
--   * window: single-month flows #29-#45 (v_flow_panel); a ministry needs >= 15 gap-free consecutive reports,
--     otherwise 'insufficient months';
--   * mean monthly receipts >= 100 and mean pending >= 50, otherwise 'insufficient volume';
--   * trend size = Sen's slope (median of all pairwise slopes (y_j - y_i) / (j - i)), computed exactly here,
--     as a % of the series mean per month; |slope| < 1.0 %/month = 'stable' (STABLE_BAND_PCT_PER_MONTH).
-- The Mann-Kendall p/q-values of the original are not recomputed in SQL (autocorrelation-corrected variance);
-- they are descriptive only in the quadrant labels, which depend on the slopes and the band alone.
-- Self-check: status, both trend labels and the quadrant must equal demand_backlog_quadrants.
WITH panel AS (
    SELECT ministry_id, report_number, CAST(receipts AS DOUBLE) AS receipts, CAST(pending AS DOUBLE) AS pending
    FROM grievance_lens.v_flow_panel
),
per_ministry AS (
    SELECT ministry_id,
           COUNT(*) AS months,
           MAX(report_number) - MIN(report_number) + 1 AS span,
           AVG(receipts) AS mean_monthly_receipts,
           AVG(pending) AS mean_pending
    FROM panel
    GROUP BY ministry_id
),
eligible AS (
    SELECT m.*,
           CASE WHEN m.months < 15 OR m.span <> m.months THEN 'insufficient months'
                WHEN m.mean_monthly_receipts < 100 OR m.mean_pending < 50 THEN 'insufficient volume'
                ELSE 'classified' END AS status
    FROM per_ministry m
),
pair_slopes AS (
    SELECT a.ministry_id,
           (b.receipts - a.receipts) / (b.report_number - a.report_number) AS receipts_slope,
           (b.pending - a.pending) / (b.report_number - a.report_number) AS pending_slope
    FROM panel a
    JOIN panel b ON b.ministry_id = a.ministry_id AND b.report_number > a.report_number
    JOIN eligible e ON e.ministry_id = a.ministry_id AND e.status = 'classified'
),
ranked AS (
    SELECT ministry_id,
           receipts_slope,
           pending_slope,
           ROW_NUMBER() OVER (PARTITION BY ministry_id ORDER BY receipts_slope) AS r_rn,
           ROW_NUMBER() OVER (PARTITION BY ministry_id ORDER BY pending_slope) AS p_rn,
           COUNT(*) OVER (PARTITION BY ministry_id) AS pairs
    FROM pair_slopes
),
sen AS (
    -- exact median: the middle value, or the mean of the two middle values when the count is even
    SELECT ministry_id,
           AVG(CASE WHEN r_rn IN (FLOOR((pairs + 1) / 2.0), CEIL((pairs + 1) / 2.0)) THEN receipts_slope END)
               AS receipts_sen_slope,
           AVG(CASE WHEN p_rn IN (FLOOR((pairs + 1) / 2.0), CEIL((pairs + 1) / 2.0)) THEN pending_slope END)
               AS pending_sen_slope
    FROM ranked
    GROUP BY ministry_id
),
classified AS (
    SELECT e.ministry_id,
           e.months,
           e.mean_monthly_receipts,
           e.mean_pending,
           e.status,
           100 * s.receipts_sen_slope / e.mean_monthly_receipts AS demand_rel_slope_pct,
           100 * s.pending_sen_slope / e.mean_pending AS backlog_rel_slope_pct
    FROM eligible e
    LEFT JOIN sen s ON s.ministry_id = e.ministry_id
),
labelled AS (
    SELECT c.*,
           CASE WHEN c.status <> 'classified' THEN NULL
                WHEN c.demand_rel_slope_pct >= 1.0 THEN 'rising'
                WHEN c.demand_rel_slope_pct <= -1.0 THEN 'falling' ELSE 'stable' END AS demand_trend,
           CASE WHEN c.status <> 'classified' THEN NULL
                WHEN c.backlog_rel_slope_pct >= 1.0 THEN 'rising'
                WHEN c.backlog_rel_slope_pct <= -1.0 THEN 'falling' ELSE 'stable' END AS backlog_trend
    FROM classified c
)
SELECT l.ministry_id,
       d.canonical_name,
       l.months,
       l.mean_monthly_receipts,
       l.mean_pending,
       l.status,
       l.demand_rel_slope_pct,
       l.backlog_rel_slope_pct,
       l.demand_trend,
       l.backlog_trend,
       CASE WHEN l.status = 'classified'
            THEN 'demand ' || l.demand_trend || ' / backlog ' || l.backlog_trend END AS quadrant,
       (l.status = q.status
        AND (l.demand_trend = q.demand_trend OR (l.demand_trend IS NULL AND q.demand_trend IS NULL))
        AND (l.backlog_trend = q.backlog_trend OR (l.backlog_trend IS NULL AND q.backlog_trend IS NULL))
        AND (ABS(l.demand_rel_slope_pct - q.demand_rel_slope_pct) < 1e-9
             OR (l.demand_rel_slope_pct IS NULL AND q.demand_rel_slope_pct IS NULL))
        AND (ABS(l.backlog_rel_slope_pct - q.backlog_rel_slope_pct) < 1e-9
             OR (l.backlog_rel_slope_pct IS NULL AND q.backlog_rel_slope_pct IS NULL))) AS matches_published
FROM labelled l
JOIN grievance_lens.dim_ministry d ON d.ministry_id = l.ministry_id
LEFT JOIN grievance_lens.demand_backlog_quadrants q ON q.ministry_id = l.ministry_id
ORDER BY l.status, quadrant, l.ministry_id
