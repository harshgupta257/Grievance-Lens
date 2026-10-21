"""Integration tests: apply the real migrations to a temporary schema and check structure and constraints.

Opt-in: set TEST_DATABASE_URL (see .env.example). Otherwise these tests are reported as skipped.
"""

from __future__ import annotations

import shutil

import pytest
from sqlalchemy import Engine, text
from sqlalchemy.exc import IntegrityError

from src.config import MIGRATIONS_DIR
from src.db import MigrationError, discover_migrations, migrate, pending_migrations

pytestmark = pytest.mark.integration

EXPECTED_TABLES = {
    "schema_migrations",
    "reports",
    "ministries",
    "ministry_aliases",
    "metric_definitions",
    "source_tables",
    "observations",
    "extraction_issues",
}

REPORT = dict(
    n=40,
    month="2025-08-01",
    cutoff="2025-08-31",
    filename="cpgrams_centre_2025-08_r40_en.pdf",
    url="https://www.darpg.gov.in/static/uploads/2025/10/748098b5d8254ba2ddf9fbc01d389243.pdf",
    sha="a" * 64,
)


@pytest.fixture()
def db(isolated_engine: Engine) -> Engine:
    migrate(isolated_engine)
    return isolated_engine


def insert_report(conn, **overrides) -> int:
    p = {**REPORT, **overrides}
    return conn.execute(
        text(
            "INSERT INTO reports (report_number, report_month, language, data_cutoff_date, filename,"
            " source_url, sha256)"
            " VALUES (:n, :month, 'en', :cutoff, :filename, :url, :sha) RETURNING report_id"
        ),
        p,
    ).scalar_one()


def insert_table(
    conn,
    report_id: int,
    table_type="pending_over_1000",
    threshold=1000,
    unit="grievances",
    group=None,
    method=None,
) -> int:
    return conn.execute(
        text(
            "INSERT INTO source_tables (report_id, table_type, original_title, pdf_page_start, pdf_page_end,"
            " period_start, period_end, threshold_value, threshold_unit, grai_group, grai_methodology,"
            " parser_name, parser_version, extraction_method)"
            " VALUES (:r, :t, 'Annexure 1.3', 35, 35, '2025-01-01', '2025-08-31', :thr, :unit, :g, :m,"
            " 'test', '0', 'manual') RETURNING table_id"
        ),
        {
            "r": report_id,
            "t": table_type,
            "thr": threshold,
            "unit": unit if threshold else None,
            "g": group,
            "m": method,
        },
    ).scalar_one()


def insert_observation(
    conn, table_id: int, value="13959", name="Ministry of Labour and Employment", aggregate=False, row=0
):
    conn.execute(
        text(
            "INSERT INTO observations (table_id, row_index, is_aggregate, entity_name_original, metric_code,"
            " metric_label_original, value_original, value_numeric, period_start, period_end, pdf_page)"
            " VALUES (:t, :row, :agg, :name, 'pending', 'Pending', :v, :num, '2025-01-01', '2025-08-31', 35)"
        ),
        {"t": table_id, "row": row, "agg": aggregate, "name": name, "v": value, "num": float(value)},
    )


def test_migrations_apply_once_and_are_idempotent(isolated_engine):
    applied = migrate(isolated_engine)
    assert [m.version for m in applied] == [m.version for m in discover_migrations()]
    assert migrate(isolated_engine) == []
    assert pending_migrations(isolated_engine) == []


def test_expected_tables_exist(db):
    with db.connect() as conn:
        tables = set(
            conn.execute(
                text("SELECT table_name FROM information_schema.tables WHERE table_schema = current_schema()")
            ).scalars()
        )
    assert EXPECTED_TABLES <= tables


def test_metric_vocabulary_is_seeded(db):
    with db.connect() as conn:
        codes = set(conn.execute(text("SELECT metric_code FROM metric_definitions")).scalars())
    assert {
        "receipts",
        "disposals",
        "pending",
        "pending_over_threshold",
        "avg_closing_time_days",
        "grai_score",
        "grai_rank",
    } <= codes


def test_valid_provenance_chain_can_be_stored(db):
    with db.begin() as conn:
        table_id = insert_table(conn, insert_report(conn))
        insert_observation(conn, table_id)
        row = conn.execute(
            text(
                "SELECT r.report_number, t.pdf_page_start, o.value_numeric FROM observations o"
                " JOIN source_tables t USING (table_id) JOIN reports r USING (report_id)"
            )
        ).one()
    assert (row[0], row[1], float(row[2])) == (40, 35, 13959.0)


# Each negative test asserts the specific constraint that must fire, not just "some IntegrityError".
@pytest.mark.parametrize(
    ("overrides", "constraint"),
    [
        ({"sha": "not-a-checksum"}, "reports_sha256_check"),
        ({"cutoff": "2025-09-30"}, "ck_reports_cutoff_in_month"),
        ({"month": "2025-08-15", "cutoff": None}, "reports_report_month_check"),
        ({"url": "http://insecure.example/report.pdf"}, "reports_source_url_check"),
    ],
)
def test_invalid_reports_are_rejected(db, overrides, constraint):
    with pytest.raises(IntegrityError, match=constraint), db.begin() as conn:
        insert_report(conn, **overrides)


def test_duplicate_report_checksum_is_rejected(db):
    with db.begin() as conn:
        insert_report(conn)
    with pytest.raises(IntegrityError, match="reports_sha256_key"), db.begin() as conn:
        insert_report(conn, n=41, month="2025-09-01", cutoff=None, filename="other.pdf")


def test_threshold_tables_require_a_threshold(db):
    with db.begin() as conn:
        report_id = insert_report(conn)
    with pytest.raises(IntegrityError, match="ck_source_tables_threshold_required"), db.begin() as conn:
        insert_table(conn, report_id, threshold=None)


def test_grai_tables_require_group_and_methodology(db):
    with db.begin() as conn:
        report_id = insert_report(conn)
    with pytest.raises(IntegrityError, match="ck_source_tables_grai_fields"), db.begin() as conn:
        insert_table(conn, report_id, table_type="grai_ranking", threshold=None, group="A", method=None)


def test_same_table_cannot_be_registered_twice_even_with_null_group(db):
    with db.begin() as conn:
        report_id = insert_report(conn)
        insert_table(conn, report_id)
    with pytest.raises(IntegrityError, match="uq_source_tables_one_per_report"), db.begin() as conn:
        insert_table(conn, report_id)


@pytest.mark.parametrize(
    ("kwargs", "constraint"),
    [
        ({"value": "-5"}, "ck_observations_non_negative"),
        ({"aggregate": True}, "ck_observations_aggregate_name"),  # aggregate rows carry no name
        ({"aggregate": False, "name": None}, "ck_observations_aggregate_name"),  # ministry rows need one
    ],
)
def test_invalid_observations_are_rejected(db, kwargs, constraint):
    with db.begin() as conn:
        table_id = insert_table(conn, insert_report(conn))
    with pytest.raises(IntegrityError, match=constraint), db.begin() as conn:
        insert_observation(conn, table_id, **kwargs)


def test_duplicate_observation_is_rejected(db):
    with db.begin() as conn:
        table_id = insert_table(conn, insert_report(conn))
        insert_observation(conn, table_id)
    with pytest.raises(IntegrityError, match="uq_observations_value"), db.begin() as conn:
        insert_observation(conn, table_id)


def test_fuzzy_alias_requires_score_and_reviewer(db):
    with db.begin() as conn:
        ministry_id = conn.execute(
            text(
                "INSERT INTO ministries (canonical_name, entity_type)"
                " VALUES ('Ministry of Railways (Railway Board)',"
                " 'ministry') RETURNING ministry_id"
            )
        ).scalar_one()
    with pytest.raises(IntegrityError, match="ck_aliases_fuzzy_reviewed"), db.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO ministry_aliases (ministry_id, alias_original, alias_normalised, match_method)"
                " VALUES (:m, 'Ministry of Railways ( Railway Board)', 'ministry of railways railway board',"
                " 'fuzzy_reviewed')"
            ),
            {"m": ministry_id},
        )


def test_resolved_issue_requires_a_resolution_note(db):
    with db.begin() as conn:
        report_id = insert_report(conn)
    with pytest.raises(IntegrityError, match="ck_issues_resolution_note"), db.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO extraction_issues (report_id, severity, issue_type, description, status)"
                " VALUES (:r, 'warning', 'wrapped_name', 'name split over lines', 'resolved')"
            ),
            {"r": report_id},
        )


def test_editing_an_applied_migration_is_detected(isolated_engine, tmp_path):
    for f in MIGRATIONS_DIR.glob("*.sql"):
        shutil.copy(f, tmp_path / f.name)
    migrate(isolated_engine, tmp_path)
    first = sorted(tmp_path.glob("*.sql"))[0]
    first.write_text(first.read_text(encoding="utf-8") + "\n-- edited\n", encoding="utf-8")
    with pytest.raises(MigrationError, match="edited after being applied"):
        pending_migrations(isolated_engine, tmp_path)


def _ministry(conn, name, key):
    return conn.execute(
        text(
            "INSERT INTO ministries (canonical_name, entity_type, ministry_key) VALUES (:n, 'ministry', :k)"
            " RETURNING ministry_id"
        ),
        {"n": name, "k": key},
    ).scalar_one()


def test_two_printed_spellings_may_share_a_key_but_each_spelling_maps_once(db):
    insert = text(
        "INSERT INTO ministry_aliases (ministry_id, alias_original, alias_normalised, match_method)"
        " VALUES (:m, :orig, 'department of health and family welfare', 'exact')"
    )
    with db.begin() as conn:
        m = _ministry(
            conn, "Department of Health & Family Welfare", "department_of_health_and_family_welfare"
        )
        conn.execute(insert, {"m": m, "orig": "Department of Health & Family Welfare"})
        conn.execute(insert, {"m": m, "orig": "Department of Health and Family Welfare"})
    with pytest.raises(IntegrityError, match="uq_ministry_aliases_original"), db.begin() as conn:
        conn.execute(insert, {"m": m, "orig": "Department of Health & Family Welfare"})


def test_relationship_cannot_point_to_itself(db):
    with db.begin() as conn:
        m = _ministry(conn, "Ministry of Housing and Urban Affairs", "ministry_of_housing_and_urban_affairs")
    with pytest.raises(IntegrityError, match="ck_relationship_distinct"), db.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO ministry_relationships (from_ministry_id, to_ministry_id, relation, evidence)"
                " VALUES (:m, :m, 'split_into', 'test')"
            ),
            {"m": m},
        )
