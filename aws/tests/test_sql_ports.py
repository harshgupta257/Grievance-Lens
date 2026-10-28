"""The five Athena analyses (Trino syntax) reproduce the project's published values; benchmark pairs are
equivalent; the Redshift files are valid Redshift SQL. Runs on DuckDB over curated Parquet built from the
real exports (no Spark, no AWS)."""

from __future__ import annotations

import re

import duckdb
import pytest
import sqlglot
from conftest import AWS


@pytest.fixture(scope="module")
def con(curated_pyarrow, contract, staged_batch):
    c = duckdb.connect()
    c.execute("CREATE SCHEMA grievance_lens")
    c.execute("CREATE SCHEMA grievance_lens_raw")
    for t in contract["tables"]:
        base = (curated_pyarrow / t["name"]).as_posix()
        glob, hive = (
            (base + "/**/*.parquet", ", hive_partitioning = true")
            if t.get("partition")
            else (base + "/*.parquet", "")
        )
        c.execute(f"CREATE VIEW grievance_lens.{t['name']} AS SELECT * FROM read_parquet('{glob}'{hive})")
        raw = (staged_batch / t["name"] / f"{t['name']}.csv").as_posix()
        c.execute(
            f"CREATE VIEW grievance_lens_raw.{t['name']} AS "
            f"SELECT * FROM read_csv('{raw}', all_varchar = true, header = true)"
        )
    c.execute(
        sqlglot.transpile(
            (AWS / "athena" / "00_views.sql").read_text(encoding="utf-8"), read="trino", write="duckdb"
        )[0]
    )
    return c


def athena(name: str) -> str:
    return next((AWS / "athena").glob(f"{name}*.sql")).read_text(encoding="utf-8")


EXPECTED_ROWS = {"01": 57, "02": 90, "03": 57, "04": 181, "05": 90}


@pytest.mark.parametrize("query", sorted(EXPECTED_ROWS))
def test_athena_query_matches_published_values(con, query):
    sql = athena(query)
    sqlglot.parse_one(sql, read="trino")
    rows = con.execute(sqlglot.transpile(sql, read="trino", write="duckdb")[0]).fetchdf()
    assert len(rows) == EXPECTED_ROWS[query]
    assert rows["matches_published"].astype(bool).all(), rows[~rows["matches_published"].astype(bool)].head()


def test_demand_backlog_headline_reproduces(con):
    df = con.execute(sqlglot.transpile(athena("05"), read="trino", write="duckdb")[0]).fetchdf()
    classified = df[df["status"] == "classified"]
    assert len(classified) == 66
    assert (classified["quadrant"] == "demand rising / backlog rising").sum() == 37  # README finding 4.6


def test_benchmark_pairs_return_identical_rows(con):
    import benchmark_runner

    for name, pair in benchmark_runner.load_pairs().items():
        out = []
        for kind in ("csv", "parquet"):
            sqlglot.parse_one(pair[kind], read="trino")
            rows = con.execute(sqlglot.transpile(pair[kind], read="trino", write="duckdb")[0]).fetchall()
            out.append(benchmark_runner.rows_hash([list(r) for r in rows]))
        assert out[0] == out[1], name


def test_redshift_sql_parses_as_redshift():
    for f in ("views.sql", "analytics.sql"):
        text = (AWS / "redshift" / f).read_text(encoding="utf-8")
        body = "\n".join(line for line in text.splitlines() if not line.strip().startswith("--"))
        for stmt in (s for s in re.split(r";\s*\n", body + "\n") if s.strip()):
            sqlglot.parse_one(stmt, read="redshift")


def test_redshift_analytics_differ_only_by_documented_rewrites():
    from aws.contract.render_ddl import to_redshift

    text = (AWS / "redshift" / "analytics.sql").read_text(encoding="utf-8")
    for f in sorted((AWS / "athena").glob("0[1-5]_*.sql")):
        assert to_redshift(f.read_text(encoding="utf-8")).rstrip() + ";" in text
