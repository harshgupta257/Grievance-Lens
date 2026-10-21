-- 0003_ministry_resolution.sql
-- Phase 3: ministry name resolution (DESIGN.md D-023 .. D-025).
--
-- 1. Several printed spellings legitimately share one cleaned key ("Department of Health & Family Welfare" and
--    "Department of Health and Family Welfare"), so uniqueness moves from alias_normalised to alias_original:
--    each printed spelling maps to exactly one ministry.
-- 2. Reorganisations are not aliases. A split or rename between two canonical entities is recorded as a
--    relationship with its evidence, so analyses can see the break instead of an invented continuity.
-- 3. Each canonical entity records the DARPG reports in which it was observed.

ALTER TABLE ministry_aliases DROP CONSTRAINT ministry_aliases_alias_normalised_key;
ALTER TABLE ministry_aliases ADD CONSTRAINT uq_ministry_aliases_original UNIQUE (alias_original);
CREATE INDEX ix_ministry_aliases_normalised ON ministry_aliases (alias_normalised);
ALTER TABLE ministry_aliases ADD COLUMN evidence text;

ALTER TABLE ministries ADD COLUMN ministry_key text;
ALTER TABLE ministries ADD CONSTRAINT uq_ministries_key UNIQUE (ministry_key);
ALTER TABLE ministries ADD CONSTRAINT ck_ministries_key CHECK (ministry_key ~ '^[a-z0-9_]+$');
ALTER TABLE ministries ADD COLUMN first_report_month date;
ALTER TABLE ministries ADD COLUMN last_report_month date;

CREATE TABLE ministry_relationships (
    relationship_id     integer GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    from_ministry_id    integer NOT NULL REFERENCES ministries (ministry_id) ON DELETE CASCADE,
    to_ministry_id      integer NOT NULL REFERENCES ministries (ministry_id) ON DELETE CASCADE,
    relation            text    NOT NULL CHECK (relation IN ('renamed_to', 'split_into', 'merged_into')),
    effective_month     date    CHECK (effective_month IS NULL OR EXTRACT(DAY FROM effective_month) = 1),
    evidence            text    NOT NULL,
    CONSTRAINT ck_relationship_distinct CHECK (from_ministry_id <> to_ministry_id),
    CONSTRAINT uq_relationship UNIQUE (from_ministry_id, to_ministry_id, relation)
);
