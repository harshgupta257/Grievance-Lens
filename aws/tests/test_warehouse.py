"""Redshift refresh SQL: Data API limits, guards, load order and refusal of unsafe input."""

from __future__ import annotations

import pytest
import warehouse

ROLE = "arn:aws:iam::123456789012:role/grievance-lens-redshift-copy"


@pytest.fixture
def pointer(contract):
    return {
        "ingestion_id": "2026-10-27_TESTBATCH01",
        "contract_hash": contract["contract_hash"],
        "redshift_manifest_prefix": "s3://my-bucket/control/redshift_manifests/ingestion_id=2026-10-27_TESTBATCH01/",
        "load_order": contract["load_order"],
        "table_rows": {t: 10 for t in contract["load_order"]},
    }


def test_batches_fit_the_data_api_limit(pointer):
    assert len(warehouse.stage_statements(pointer, ROLE)) <= warehouse.MAX_BATCH_STATEMENTS
    assert len(warehouse.publish_statements(pointer)) <= warehouse.MAX_BATCH_STATEMENTS


def test_stage_copies_every_table_in_load_order_then_checks_counts(pointer):
    sqls = warehouse.stage_statements(pointer, ROLE)
    copies = [s for s in sqls if s.startswith("COPY")]
    assert [s.split()[1] for s in copies] == [f"stg.{t}" for t in pointer["load_order"]]
    assert all("FORMAT AS PARQUET MANIFEST" in s and ROLE in s for s in copies)
    assert all(f"/{s.split()[1][4:]}.manifest'" in s for s in copies)
    guard = next(s for s in sqls if "staged_counts_ok" in s)
    assert guard.startswith("SELECT 1 / (CASE WHEN") and "(SELECT COUNT(*) FROM stg.dim_month) = 10" in guard
    assert sqls.index(guard) > sqls.index(copies[-1])


def test_publish_is_guarded_by_marker_and_counts(pointer):
    sqls = warehouse.publish_statements(pointer)
    assert "stg.batch_marker WHERE ingestion_id = '2026-10-27_TESTBATCH01'" in sqls[0]
    assert sqls[-1].startswith("INSERT INTO gl.load_audit") and "'PUBLISHED'" in sqls[-1]
    inserts = [s for s in sqls if s.startswith("INSERT INTO gl.") and "load_audit" not in s]
    assert [s.split()[2] for s in inserts] == [f"gl.{t}" for t in pointer["load_order"]]


@pytest.mark.parametrize(
    "field,value",
    [
        ("ingestion_id", "2026-10-27_x'; DROP TABLE gl.dim_month; --"),
        ("redshift_manifest_prefix", "https://evil.example/"),
        ("load_order", ["dim_month; DROP"]),
    ],
)
def test_unsafe_values_are_refused(pointer, field, value):
    bad = dict(pointer, **{field: value})
    if field == "load_order":
        bad["table_rows"] = {value[0]: 1}
    with pytest.raises(warehouse.RefreshError):
        warehouse.stage_statements(bad, ROLE)


def test_role_arn_is_validated(pointer):
    with pytest.raises(warehouse.RefreshError):
        warehouse.stage_statements(pointer, "arn:aws:iam::123:role/x' IAM_ROLE 'other")


def test_reconciliation_query(pointer):
    q = warehouse.reconciliation_query(pointer)
    assert q.count("UNION ALL") == len(pointer["load_order"]) - 1
    assert "COUNT(*) AS warehouse_rows, 10 AS curated_rows FROM gl.grai_ranks" in q
