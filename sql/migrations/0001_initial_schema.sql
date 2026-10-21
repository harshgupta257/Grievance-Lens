-- 0001_initial_schema.sql
-- Provenance-first schema based on the Phase 0 findings (docs/source_inventory.md, DESIGN.md D-004..D-006, D-010).
--
-- Design in one paragraph:
--   reports          one row per verified source PDF (from data/manifest.csv)
--   source_tables    one row per table found in a report, with its page range, reference period,
--                    threshold and GRAI group/methodology (all of which changed over time)
--   observations     one row per published value, keeping the original label/value text next to the
--                    parsed number (long format; analysis-friendly wide views come in Phase 4)
--   ministries / ministry_aliases   canonical names and reviewed name mappings (filled in Phase 3)
--   metric_definitions              documented meaning of every metric code
--   extraction_issues               every problem the parsers could not resolve, never silently dropped
--
-- Object names are unqualified on purpose: the migration runner sets search_path, so the integration
-- tests can apply this file into an isolated temporary schema.

CREATE TABLE reports (
    report_id               integer GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    report_number           smallint    NOT NULL CHECK (report_number > 0),
    report_month            date        NOT NULL CHECK (EXTRACT(DAY FROM report_month) = 1),
    language                char(2)     NOT NULL CHECK (language IN ('en', 'hi')),
    data_cutoff_date        date,       -- NULL = not yet verified (e.g. Feb-Aug 2026 in Phase 0)
    filename                text        NOT NULL UNIQUE,
    source_url              text        NOT NULL CHECK (source_url LIKE 'https://%'),
    sha256                  char(64)    NOT NULL UNIQUE CHECK (sha256 ~ '^[0-9a-f]{64}$'),
    cms_media_id            integer,
    page_count              smallint    CHECK (page_count > 0),
    printed_page_offset     smallint,   -- printed footer label minus 1-based physical page
    report_number_inferred  boolean     NOT NULL DEFAULT false,
    notes                   text,
    registered_at           timestamptz NOT NULL DEFAULT now(),
    UNIQUE (report_number, language),
    UNIQUE (report_month, language),
    -- the cut-off date (25th, 28th or month-end) must fall inside the report month
    CONSTRAINT ck_reports_cutoff_in_month CHECK (data_cutoff_date IS NULL OR date_trunc('month', data_cutoff_date)::date = report_month)
);

CREATE TABLE ministries (
    ministry_id         integer GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    canonical_name      text NOT NULL UNIQUE,
    entity_type         text NOT NULL CHECK (entity_type IN ('ministry', 'department', 'organisation', 'other')),
    parent_ministry_id  integer REFERENCES ministries (ministry_id) ON DELETE RESTRICT,
    notes               text,
    CONSTRAINT ck_ministries_not_own_parent CHECK (parent_ministry_id IS DISTINCT FROM ministry_id)
);

CREATE TABLE ministry_aliases (
    alias_id          integer GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    ministry_id       integer NOT NULL REFERENCES ministries (ministry_id) ON DELETE CASCADE,
    alias_original    text    NOT NULL,
    alias_normalised  text    NOT NULL UNIQUE,  -- one spelling maps to exactly one ministry
    match_method      text    NOT NULL CHECK (match_method IN ('exact', 'approved_alias', 'fuzzy_reviewed')),
    match_score       numeric(5, 2) CHECK (match_score BETWEEN 0 AND 100),
    reviewed_by       text,
    reviewed_on       date,
    -- fuzzy matches are never auto-accepted: they need a score and a named reviewer
    CONSTRAINT ck_aliases_fuzzy_reviewed CHECK (match_method <> 'fuzzy_reviewed' OR (match_score IS NOT NULL AND reviewed_by IS NOT NULL))
);
CREATE INDEX ix_ministry_aliases_ministry ON ministry_aliases (ministry_id);

CREATE TABLE metric_definitions (
    metric_code   text PRIMARY KEY CHECK (metric_code ~ '^[a-z][a-z0-9_]*$'),
    label         text    NOT NULL,
    measure_type  text    NOT NULL CHECK (measure_type IN ('flow', 'stock', 'total', 'duration', 'score', 'rank', 'ratio')),
    unit          text    NOT NULL CHECK (unit IN ('grievances', 'days', 'score', 'rank', 'percent')),
    is_official   boolean NOT NULL,  -- true = published by DARPG; false = derived by this project
    definition    text    NOT NULL
);

CREATE TABLE source_tables (
    table_id            integer GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    report_id           integer  NOT NULL REFERENCES reports (report_id) ON DELETE CASCADE,
    table_type          text     NOT NULL CHECK (table_type IN (
                            'overall_overview', 'overall_monthwise',
                            'top10_receipts', 'top10_disposals',
                            'pending_over_1000', 'long_pending_top10',
                            'avg_closing_time_high', 'avg_closing_time_low',
                            'grai_ranking', 'grai_score_inputs')),
    original_title      text     NOT NULL,
    pdf_page_start      smallint NOT NULL CHECK (pdf_page_start > 0),
    pdf_page_end        smallint NOT NULL,
    printed_page_label  text,
    period_start        date     NOT NULL,
    period_end          date     NOT NULL,
    threshold_value     numeric,  -- 1000 (grievances) or 45/30/21 (days)
    threshold_unit      text     CHECK (threshold_unit IN ('grievances', 'days')),
    grai_group          char(1)  CHECK (grai_group IN ('A', 'B')),
    grai_methodology    text,     -- e.g. 'GRI_2022', 'GRAI_4D', 'GRAI_4D_unnormalised'
    parser_name         text     NOT NULL,
    parser_version      text     NOT NULL,
    extraction_method   text     NOT NULL CHECK (extraction_method IN ('pdfplumber_table', 'pdfplumber_text', 'camelot', 'manual')),
    extracted_at        timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT ck_source_tables_page_range CHECK (pdf_page_end >= pdf_page_start),
    CONSTRAINT ck_source_tables_period CHECK (period_end >= period_start),
    CONSTRAINT ck_source_tables_threshold_pair CHECK ((threshold_value IS NULL) = (threshold_unit IS NULL)),
    CONSTRAINT ck_source_tables_threshold_required CHECK (table_type NOT IN ('pending_over_1000', 'long_pending_top10') OR threshold_value IS NOT NULL),
    CONSTRAINT ck_source_tables_grai_fields CHECK (table_type NOT IN ('grai_ranking', 'grai_score_inputs') OR (grai_group IS NOT NULL AND grai_methodology IS NOT NULL)),
    CONSTRAINT uq_source_tables_one_per_report UNIQUE NULLS NOT DISTINCT (report_id, table_type, grai_group)
);
CREATE INDEX ix_source_tables_report ON source_tables (report_id);

CREATE TABLE observations (
    observation_id         bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    table_id               integer  NOT NULL REFERENCES source_tables (table_id) ON DELETE CASCADE,
    row_index              smallint NOT NULL CHECK (row_index >= 0),  -- extraction order within the table
    serial_number_original text,      -- "S. No." exactly as printed
    is_aggregate           boolean  NOT NULL DEFAULT false,  -- Central Secretariat total, not a ministry
    entity_name_original   text,      -- ministry name exactly as printed (wrapped lines re-joined)
    ministry_id            integer  REFERENCES ministries (ministry_id) ON DELETE RESTRICT,  -- set in Phase 3
    metric_code            text     NOT NULL REFERENCES metric_definitions (metric_code),
    metric_label_original  text     NOT NULL,
    value_original         text     NOT NULL,
    value_numeric          numeric(14, 4),
    period_start           date     NOT NULL,
    period_end             date     NOT NULL,
    pdf_page               smallint NOT NULL CHECK (pdf_page > 0),
    validation_status      text     NOT NULL DEFAULT 'unvalidated'
                           CHECK (validation_status IN ('unvalidated', 'valid', 'warning', 'failed', 'overridden')),
    CONSTRAINT ck_observations_period CHECK (period_end >= period_start),
    CONSTRAINT ck_observations_aggregate_name CHECK (is_aggregate = (entity_name_original IS NULL)),
    CONSTRAINT ck_observations_aggregate_no_ministry CHECK (NOT is_aggregate OR ministry_id IS NULL),
    -- every published DARPG value is non-negative; signed changes are derived later in views
    CONSTRAINT ck_observations_non_negative CHECK (value_numeric IS NULL OR value_numeric >= 0),
    CONSTRAINT uq_observations_value UNIQUE (table_id, row_index, metric_code, period_start, period_end)
);
CREATE INDEX ix_observations_ministry_metric ON observations (ministry_id, metric_code, period_end);
CREATE INDEX ix_observations_metric_period ON observations (metric_code, period_end);

CREATE TABLE extraction_issues (
    issue_id         integer GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    report_id        integer NOT NULL REFERENCES reports (report_id) ON DELETE CASCADE,
    table_id         integer REFERENCES source_tables (table_id) ON DELETE CASCADE,
    pdf_page         smallint CHECK (pdf_page > 0),
    severity         text    NOT NULL CHECK (severity IN ('info', 'warning', 'error')),
    issue_type       text    NOT NULL,
    raw_text         text,
    description      text    NOT NULL,
    status           text    NOT NULL DEFAULT 'open' CHECK (status IN ('open', 'resolved', 'wont_fix')),
    resolution_note  text,
    created_at       timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT ck_issues_resolution_note CHECK (status = 'open' OR resolution_note IS NOT NULL)
);
CREATE INDEX ix_extraction_issues_report ON extraction_issues (report_id);

-- Metric vocabulary observed in Phase 0. Appeals metrics (approved as optional) get added when extracted.
INSERT INTO metric_definitions (metric_code, label, measure_type, unit, is_official, definition) VALUES
 ('brought_forward',        'Brought Forward',              'stock',    'grievances', true,  'Grievances pending at the start of the table period (1 January for year-to-date tables).'),
 ('receipts',               'Receipts',                     'flow',     'grievances', true,  'Grievances received during the table period (a single month, or year-to-date, as given by the period columns).'),
 ('total_grievances',       'Total Grievances',             'total',    'grievances', true,  'Brought Forward + Receipts for the table period, as published.'),
 ('disposals',              'Disposal',                     'flow',     'grievances', true,  'Grievances disposed of during the table period.'),
 ('pending',                'Pending',                      'stock',    'grievances', true,  'Grievances pending at the period end (the report cut-off date).'),
 ('pending_over_threshold', 'Pending > N days',             'stock',    'grievances', true,  'Grievances pending longer than the threshold given in source_tables.threshold_value (45, 30 or 21 days, depending on the era).'),
 ('cases_disposed',         'Cases Disposed',               'flow',     'grievances', true,  'Disposals counted in the average-closing-time tables (year-to-date).'),
 ('avg_closing_time_days',  'Average Closing Time',         'duration', 'days',       true,  'Published average closing time in days over the table period. Only the top and bottom 10 are published.'),
 ('grai_score',             'GRAI Score',                   'score',    'score',      true,  'Grievance Redressal Assessment and Index score. Comparable only within one methodology and group.'),
 ('grai_rank',              'GRAI Rank',                    'rank',     'rank',       true,  'Published rank within a GRAI group (A or B) for the month.'),
 ('gri_percentage',         'Grievance Redressal Index %',  'ratio',    'percent',    true,  'The 2022-23 Grievance Redressal Index (GRI) score, published as a percentage.');
