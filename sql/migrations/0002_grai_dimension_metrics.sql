-- 0002_grai_dimension_metrics.sql
-- Metric codes for the GRAI dimension scores, observed in the ranking tables from May 2023 onwards
-- (layouts GRAI_4D_total, GRAI_4D_receipts_disposal and GRAI_4D_dims; see src/extract/ministry_tables.py).
-- Dimension weights are published in each report's method table and are not restated here.

INSERT INTO metric_definitions (metric_code, label, measure_type, unit, is_official, definition) VALUES
 ('grai_efficiency',     'GRAI Efficiency',                'score', 'score', true, 'GRAI dimension score: efficiency, as published. Comparable only within one GRAI methodology.'),
 ('grai_feedback',       'GRAI Feedback',                  'score', 'score', true, 'GRAI dimension score: citizen feedback, as published. Comparable only within one GRAI methodology.'),
 ('grai_domain',         'GRAI Domain',                    'score', 'score', true, 'GRAI dimension score: domain, as published. Comparable only within one GRAI methodology.'),
 ('grai_org_commitment', 'GRAI Organisational Commitment', 'score', 'score', true, 'GRAI dimension score: organisational commitment, as published. Comparable only within one GRAI methodology.');
