-- 04_first_last_appearance.sql  (Athena / Trino)
-- First and most recent appearance of each ministry in each published list type, with the report months
-- and the value printed at the latest appearance. Port of analysis B / repeated_listings (src/export/powerbi.py).
-- Lists are compared only within one list type (thresholds differ between types; dim_list.caveat), and
-- absence from a list is never read as zero.
-- in_latest_list: listed in the most recent report that published this list type.
-- Self-check: appearances and first/last report must equal the published repeated_listings row.
WITH appearances AS (
    SELECT la.list_code,
           la.ministry_id,
           la.report_number,
           dr.report_month,
           COALESCE(la.pending, la.pending_over_threshold, la.avg_closing_time_days) AS printed_value,
           ROW_NUMBER() OVER (PARTITION BY la.list_code, la.ministry_id ORDER BY la.report_number DESC) AS recency
    FROM grievance_lens.list_appearances la
    JOIN grievance_lens.dim_report dr ON dr.report_number = la.report_number
),
summary AS (
    SELECT list_code,
           ministry_id,
           COUNT(DISTINCT report_number) AS appearances,
           MIN(report_number) AS first_listed_report,
           MAX(report_number) AS last_listed_report,
           MIN(report_month) AS first_listed_month,
           MAX(report_month) AS last_listed_month,
           MAX(CASE WHEN recency = 1 THEN printed_value END) AS value_at_last_appearance
    FROM appearances
    GROUP BY list_code, ministry_id
)
SELECT s.list_code,
       s.ministry_id,
       d.canonical_name,
       s.appearances,
       s.first_listed_report,
       s.first_listed_month,
       s.last_listed_report,
       s.last_listed_month,
       s.value_at_last_appearance,
       l.value_measure,
       (s.last_listed_report = l.last_report) AS in_latest_list,
       (s.appearances = r.appearances
        AND s.first_listed_report = r.first_listed_report
        AND s.last_listed_report = r.last_listed_report) AS matches_published
FROM summary s
JOIN grievance_lens.dim_ministry d ON d.ministry_id = s.ministry_id
JOIN grievance_lens.dim_list l ON l.list_code = s.list_code
LEFT JOIN grievance_lens.repeated_listings r
       ON r.list_code = s.list_code AND r.ministry_id = s.ministry_id
ORDER BY s.list_code, s.appearances DESC, s.ministry_id
