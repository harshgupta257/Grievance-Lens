-- 03_ministry.sql: ministry-level views (analyses B, C, D, E, G). All keyed by canonical ministry_id.

-- Ministry rows of one table, pivoted to one row per ministry (helper; table-type filters applied by callers).
CREATE OR REPLACE VIEW v_ministry_rows AS
SELECT t.table_id,
       t.table_type,
       t.grai_group,
       t.grai_methodology,
       t.threshold_value,
       t.period_start,
       t.period_end,
       r.report_number,
       r.report_month,
       r.data_cutoff_date,
       r.in_core_window,
       o.ministry_id,
       m.canonical_name,
       min(o.serial_number_original) AS serial,
       min(o.pdf_page) AS pdf_page,
       max(o.value_numeric) FILTER (WHERE o.metric_code = 'brought_forward') AS brought_forward,
       max(o.value_numeric) FILTER (WHERE o.metric_code = 'receipts') AS receipts,
       max(o.value_numeric) FILTER (WHERE o.metric_code = 'total_grievances') AS total_grievances,
       max(o.value_numeric) FILTER (WHERE o.metric_code = 'disposals') AS disposals,
       max(o.value_numeric) FILTER (WHERE o.metric_code = 'pending') AS pending,
       max(o.value_numeric) FILTER (WHERE o.metric_code = 'pending_over_threshold') AS pending_over_threshold,
       max(o.value_numeric) FILTER (WHERE o.metric_code = 'cases_disposed') AS cases_disposed,
       max(o.value_numeric) FILTER (WHERE o.metric_code = 'avg_closing_time_days') AS avg_closing_time_days,
       max(o.value_numeric) FILTER (WHERE o.metric_code = 'grai_score') AS grai_score,
       max(o.value_numeric) FILTER (WHERE o.metric_code = 'grai_rank') AS grai_rank,
       max(o.value_numeric) FILTER (WHERE o.metric_code = 'gri_percentage') AS gri_percentage,
       bool_or(o.validation_status = 'warning') AS has_warning,
       (t.table_id IN (SELECT table_id FROM v_excluded_tables)) AS excluded
FROM observations o
JOIN source_tables t USING (table_id)
JOIN v_reports r USING (report_id)
JOIN ministries m USING (ministry_id)
WHERE o.ministry_id IS NOT NULL
GROUP BY t.table_id, t.table_type, t.grai_group, t.grai_methodology, t.threshold_value, t.period_start,
         t.period_end, r.report_number, r.report_month, r.data_cutoff_date, r.in_core_window, o.ministry_id,
         m.canonical_name;

-- B, C: ministries listed with more than 1000 pending grievances. `pending` is the stock at the report
-- cut-off; receipts/disposals are year-to-date (reset each January). Absent = not listed, never zero.
CREATE OR REPLACE VIEW v_pendency_list AS
SELECT report_number, report_month, data_cutoff_date, in_core_window, ministry_id, canonical_name, serial,
       pdf_page, brought_forward AS brought_forward_ytd, receipts AS receipts_ytd, disposals AS disposals_ytd,
       pending, has_warning
FROM v_ministry_rows
WHERE table_type = 'pending_over_1000';

-- B: Top-10 lists of grievances pending more than N days (N = 45, 30 or 21 by era; compare within one N).
CREATE OR REPLACE VIEW v_long_pending_list AS
SELECT report_number, report_month, in_core_window, ministry_id, canonical_name, serial, pdf_page,
       threshold_value::int AS threshold_days, pending, pending_over_threshold, has_warning
FROM v_ministry_rows
WHERE table_type = 'long_pending_top10';

-- B: published average closing time (Top-10 high / low lists, year-to-date). Stale reprints excluded.
CREATE OR REPLACE VIEW v_closing_time_list AS
SELECT report_number, report_month, in_core_window, ministry_id, canonical_name, serial, pdf_page,
       CASE table_type WHEN 'avg_closing_time_high' THEN 'high' ELSE 'low' END AS list,
       cases_disposed, avg_closing_time_days, has_warning
FROM v_ministry_rows
WHERE table_type IN ('avg_closing_time_high', 'avg_closing_time_low') AND NOT excluded;

-- D: GRI/GRAI rankings with the layout era (comparable only within one era and group).
CREATE OR REPLACE VIEW v_grai AS
SELECT report_number, report_month, in_core_window, ministry_id, canonical_name, grai_group, grai_methodology,
       threshold_value AS group_threshold, period_start, period_end,
       -- length of the GRAI data window in months (28 days -> 1, 1 Jul - 31 Aug -> 2)
       round((period_end - period_start + 1) / 30.44)::int AS window_months,
       grai_rank::int AS grai_rank, grai_score, gri_percentage,
       count(*) OVER (PARTITION BY table_id) AS group_size, pdf_page, has_warning
FROM v_ministry_rows
WHERE table_type = 'grai_ranking';

-- C, E, G: the T6 monthly ministry panel (#26-#45, Jun 2024 - Jan 2026; D-017). Flows cover `window_months`
-- (1, except #28: 2 months); pending is the month-end stock.
CREATE OR REPLACE VIEW v_ministry_monthly_flows AS
SELECT report_number, report_month, ministry_id, canonical_name, grai_group, period_start, period_end,
       round((period_end - period_start + 1) / 30.44)::int AS window_months,
       pdf_page, brought_forward, receipts, disposals, pending, has_warning
FROM v_ministry_rows
WHERE table_type = 'grai_ranking' AND grai_methodology IN ('GRAI_4D_flows', 'GRAI_4D_total_flows');

-- Reorganisations (D-025), so analyses can tell "left a list" apart from "ceased to exist in that form".
CREATE OR REPLACE VIEW v_reorganisations AS
SELECT r.from_ministry_id AS ministry_id, f.canonical_name, r.relation, r.effective_month,
       t.canonical_name AS successor
FROM ministry_relationships r
JOIN ministries f ON f.ministry_id = r.from_ministry_id
JOIN ministries t ON t.ministry_id = r.to_ministry_id;
