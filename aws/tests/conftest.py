"""Fixtures for the AWS pipeline tests (run with: python -m pytest aws/tests).

No test here needs AWS credentials or network access. S3/Glue are replaced by small fakes, Athena by DuckDB.
Spark tests need Java 17 (JAVA_HOME or java on PATH) and are skipped without it. On Windows, where Hadoop
cannot write local files without winutils.exe, the ETL's opt-in pyarrow writer is used; the Linux CI job
runs the same tests with the real Spark writer.
"""

from __future__ import annotations

import json
import os
import shutil
import sys
from datetime import date
from pathlib import Path

import pytest

AWS = Path(__file__).resolve().parents[1]
ROOT = AWS.parent
EXPORTS = ROOT / "data" / "processed" / "powerbi"
sys.path[:0] = [
    str(AWS / "glue"),
    str(AWS / "lambda"),
    str(AWS / "redshift"),
    str(AWS / "scripts"),
    str(AWS / "benchmarks"),
    str(AWS / "local"),
]

if os.name == "nt":
    os.environ.setdefault("GL_LOCAL_PARQUET_WRITER", "pyarrow")


@pytest.fixture(scope="session")
def contract() -> dict:
    return json.loads((AWS / "contract" / "contract.json").read_text(encoding="utf-8"))


def _java_available() -> bool:
    home = os.environ.get("JAVA_HOME")
    return bool((home and Path(home, "bin").exists()) or shutil.which("java"))


@pytest.fixture(scope="session")
def spark():
    if not _java_available():
        pytest.skip("Java 17 not found (set JAVA_HOME); Spark tests skipped")
    pyspark = pytest.importorskip("pyspark")
    from pyspark.sql import SparkSession

    os.environ.setdefault("PYSPARK_PYTHON", sys.executable)
    session = (
        SparkSession.builder.master("local[1]")
        .appName("grievance-lens-tests")
        .config("spark.sql.shuffle.partitions", "2")
        .config("spark.ui.enabled", "false")
        .config("spark.sql.session.timeZone", "UTC")
        .config("spark.hadoop.mapreduce.fileoutputcommitter.marksuccessfuljobs", "false")
        .getOrCreate()
    )
    assert pyspark.__version__.startswith("3.5")
    yield session
    session.stop()


@pytest.fixture(scope="session")
def staged_batch(tmp_path_factory) -> Path:
    """The real exports staged as one batch (byte-identical copies) with its manifest."""
    import build_batch

    out = tmp_path_factory.mktemp("raw")
    rc = build_batch.main(["--out", str(out), "--run-date", "2026-10-27", "--batch-id", "TESTBATCH01"])
    assert rc == 0
    return out / "run_date=2026-10-27" / "batch=TESTBATCH01"


def parse_value(value: str, col_type: str):
    if value == "":
        return None
    return {
        "int": int,
        "double": float,
        "string": str,
        "boolean": lambda v: v == "True",
        "date": date.fromisoformat,
    }[col_type](value)


@pytest.fixture(scope="session")
def curated_pyarrow(tmp_path_factory, contract) -> Path:
    """Curated Parquet built directly with pyarrow from the real exports (no Spark), for SQL tests.
    Typed exactly by the contract; ministry_monthly partitioned by period_year, as the ETL writes it."""
    import csv

    import pyarrow as pa
    import pyarrow.parquet as pq

    types = {
        "int": pa.int32(),
        "double": pa.float64(),
        "boolean": pa.bool_(),
        "date": pa.date32(),
        "string": pa.string(),
    }
    out = tmp_path_factory.mktemp("curated")
    for t in contract["tables"]:
        with open(EXPORTS / t["file"], encoding="utf-8", newline="") as fh:
            rows = [
                {c["name"]: parse_value(r[c["name"]], c["type"]) for c in t["columns"]}
                for r in csv.DictReader(fh)
            ]
        fields = [pa.field(c["name"], types[c["type"]]) for c in t["columns"]]
        part = t.get("partition")
        if part:
            for r in rows:
                r[part["column"]] = r[part["derived_from"]].year
            fields.append(pa.field(part["column"], pa.int32()))
            pq.write_to_dataset(
                pa.Table.from_pylist(rows, schema=pa.schema(fields)),
                str(out / t["name"]),
                partition_cols=[part["column"]],
            )
        else:
            (out / t["name"]).mkdir()
            pq.write_table(
                pa.Table.from_pylist(rows, schema=pa.schema(fields)), str(out / t["name"] / "part-0.parquet")
            )
    return out
