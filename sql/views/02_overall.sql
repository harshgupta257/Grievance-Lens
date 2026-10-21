-- 02_overall.sql: Central Secretariat aggregate series (analysis A, E, F).

-- Every printed month-wise value, with the report it came from.
CREATE OR REPLACE VIEW v_monthwise_all AS
SELECT o.metric_code,
       o.period_start AS month,
       o.value_numeric AS value,
       o.pdf_page,
       r.report_number,
       r.report_month,
       r.filename,
       -- before Apr 2023 a report's own month ends on its cut-off (the 25th): a partial month (D-026)
       (r.report_month = o.period_start AND NOT r.cutoff_is_month_end) AS partial_month
FROM observations o
JOIN source_tables t USING (table_id)
JOIN v_reports r USING (report_id)
WHERE t.table_type = 'overall_monthwise';

-- One value per metric and month: the latest report that prints it (D-026). Provenance and revision size kept.
CREATE OR REPLACE VIEW v_monthly_overall_long AS
WITH ranked AS (
    SELECT a.*,
           row_number() OVER w_desc AS latest_rank,
           count(*) OVER w AS n_reports_printed,
           max(value) OVER w - min(value) OVER w AS revision_spread
    FROM v_monthwise_all a
    WINDOW w AS (PARTITION BY metric_code, month),
           w_desc AS (PARTITION BY metric_code, month ORDER BY report_number DESC)
)
SELECT metric_code, month, value, report_number AS source_report, pdf_page AS source_pdf_page, filename,
       n_reports_printed, revision_spread,
       partial_month AS only_partial_value_available,
       month >= DATE '2023-04-01' AS in_core_window
FROM ranked
WHERE latest_rank = 1;

-- Wide form: one row per month (also the Power BI overall-trend table in Phase 6).
CREATE OR REPLACE VIEW v_monthly_overall AS
SELECT month,
       max(value) FILTER (WHERE metric_code = 'receipts') AS receipts,
       max(value) FILTER (WHERE metric_code = 'disposals') AS disposals,
       max(value) FILTER (WHERE metric_code = 'pending') AS pending,
       max(source_report) AS source_report,
       bool_or(only_partial_value_available) AS has_partial_value,
       max(revision_spread) AS max_revision_spread,
       bool_and(in_core_window) AS in_core_window
FROM v_monthly_overall_long
GROUP BY month;
