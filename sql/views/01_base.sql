-- 01_base.sql: report attributes and table-level flags used by every analysis view.
-- Views are (re)created by `python -m src.analysis.run` with CREATE OR REPLACE, in file-name order.

-- English reports with the comparability flags of docs/data_quality.md section 5.
CREATE OR REPLACE VIEW v_reports AS
SELECT report_id,
       report_number,
       report_month,
       data_cutoff_date,
       data_cutoff_date = (report_month + INTERVAL '1 month - 1 day')::date AS cutoff_is_month_end,
       report_month >= DATE '2023-04-01' AS in_core_window,        -- D-006: statistical tests window
       filename,
       source_url
FROM reports
WHERE language = 'en';

-- Tables that must not be used for period-specific results (D-027: stale reprints).
CREATE OR REPLACE VIEW v_excluded_tables AS
SELECT DISTINCT table_id, issue_type AS reason
FROM extraction_issues
WHERE issue_type = 'identical_to_previous_report' AND table_id IS NOT NULL;
