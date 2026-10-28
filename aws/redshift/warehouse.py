"""SQL for an idempotent, all-or-nothing Redshift refresh from a published curated batch.

The refresh runs as TWO Redshift Data API BatchExecuteStatement calls. Each call is one transaction (if any
statement fails, the whole call rolls back) and a call may hold at most 40 statements, so a 15-table
"copy + verify + swap" cannot fit in one call:

  1. stage   (34 statements): for each table DELETE stg.<t> + COPY stg.<t> FROM its Parquet manifest;
             one guard statement that divides by zero unless every staged row count equals the published
             count; record the ingestion in stg.batch_marker; audit row 'STAGED'.
  2. publish (32 statements): guard (stg.batch_marker holds THIS ingestion and the counts still match);
             for each table DELETE gl.<t> + INSERT INTO gl.<t> SELECT * FROM stg.<t>; audit row 'PUBLISHED'.

If either call fails, gl.* (what users query) is unchanged. Re-running for the same ingestion reproduces the
same tables, so the refresh is idempotent. Dimensions are loaded before facts (contract load_order), although
Redshift does not enforce the foreign keys.
"""

from __future__ import annotations

import re
from typing import Any

MAX_BATCH_STATEMENTS = 40  # Redshift Data API BatchExecuteStatement limit
_INGESTION_ID = re.compile(r"^\d{4}-\d{2}-\d{2}_[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")
_ROLE_ARN = re.compile(r"^arn:aws[a-z-]*:iam::\d{12}:role/[A-Za-z0-9+=,.@_/-]{1,512}$")
_S3_PREFIX = re.compile(r"^s3://[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]/[A-Za-z0-9!_.*'()/=-]*$")
_TABLE = re.compile(r"^[a-z][a-z0-9_]{0,62}$")


class RefreshError(ValueError):
    pass


def validate_pointer(pointer: dict[str, Any]) -> None:
    """Everything interpolated into SQL is checked first (no free text reaches the warehouse)."""
    if not _INGESTION_ID.match(str(pointer.get("ingestion_id", ""))):
        raise RefreshError(f"invalid ingestion_id {pointer.get('ingestion_id')!r}")
    if not _S3_PREFIX.match(str(pointer.get("redshift_manifest_prefix", ""))):
        raise RefreshError("redshift_manifest_prefix must be an s3:// prefix")
    order, rows = pointer.get("load_order") or [], pointer.get("table_rows") or {}
    if not order or set(order) != set(rows):
        raise RefreshError("load_order and table_rows must list the same tables")
    for t in order:
        if not _TABLE.match(t):
            raise RefreshError(f"invalid table name {t!r}")
        if not isinstance(rows[t], int) or rows[t] < 0:
            raise RefreshError(f"invalid row count for {t}")


def _manifest_uri(pointer: dict[str, Any], table: str) -> str:
    prefix = pointer["redshift_manifest_prefix"]
    return (prefix if prefix.endswith("/") else prefix + "/") + f"{table}.manifest"


def _counts_condition(pointer: dict[str, Any], schema: str) -> str:
    return " AND ".join(
        f"(SELECT COUNT(*) FROM {schema}.{t}) = {pointer['table_rows'][t]}" for t in pointer["load_order"]
    )


def _audit(pointer: dict[str, Any], stage: str) -> str:
    run_date, batch_id = pointer["ingestion_id"].split("_", 1)
    total = sum(pointer["table_rows"].values())
    return (
        "INSERT INTO gl.load_audit (ingestion_id, run_date, batch_id, contract_hash, stage, tables_loaded, rows_loaded) "
        f"VALUES ('{pointer['ingestion_id']}', '{run_date}', '{batch_id}', "
        f"'{re.sub(r'[^0-9a-f]', '', str(pointer.get('contract_hash', '')))}', '{stage}', "
        f"{len(pointer['load_order'])}, {total})"
    )


def stage_statements(pointer: dict[str, Any], iam_role_arn: str) -> list[str]:
    validate_pointer(pointer)
    if not _ROLE_ARN.match(iam_role_arn):
        raise RefreshError("iam_role_arn is not a valid IAM role ARN")
    sqls = []
    for t in pointer["load_order"]:
        sqls.append(f"DELETE FROM stg.{t}")
        sqls.append(
            f"COPY stg.{t} FROM '{_manifest_uri(pointer, t)}' IAM_ROLE '{iam_role_arn}' FORMAT AS PARQUET MANIFEST"
        )
    sqls.append(
        f"SELECT 1 / (CASE WHEN {_counts_condition(pointer, 'stg')} THEN 1 ELSE 0 END) AS staged_counts_ok"
    )
    sqls.append("DELETE FROM stg.batch_marker")
    sqls.append(f"INSERT INTO stg.batch_marker (ingestion_id) VALUES ('{pointer['ingestion_id']}')")
    sqls.append(_audit(pointer, "STAGED"))
    _check_size(sqls, "stage")
    return sqls


def publish_statements(pointer: dict[str, Any]) -> list[str]:
    validate_pointer(pointer)
    marker = f"(SELECT COUNT(*) FROM stg.batch_marker WHERE ingestion_id = '{pointer['ingestion_id']}') = 1"
    sqls = [
        f"SELECT 1 / (CASE WHEN {marker} AND {_counts_condition(pointer, 'stg')} THEN 1 ELSE 0 END) AS publish_guard_ok"
    ]
    for t in pointer["load_order"]:
        sqls.append(f"DELETE FROM gl.{t}")
        sqls.append(f"INSERT INTO gl.{t} SELECT * FROM stg.{t}")
    sqls.append(_audit(pointer, "PUBLISHED"))
    _check_size(sqls, "publish")
    return sqls


def reconciliation_query(pointer: dict[str, Any]) -> str:
    """One row per table: warehouse count next to the published (curated) count."""
    validate_pointer(pointer)
    return " UNION ALL ".join(
        f"SELECT '{t}' AS table_name, COUNT(*) AS warehouse_rows, {pointer['table_rows'][t]} AS curated_rows FROM gl.{t}"
        for t in pointer["load_order"]
    )


def _check_size(sqls: list[str], name: str) -> None:
    if len(sqls) > MAX_BATCH_STATEMENTS:
        raise RefreshError(
            f"{name} batch has {len(sqls)} statements; the Data API allows {MAX_BATCH_STATEMENTS}"
        )
