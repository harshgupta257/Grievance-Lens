"""Local end-to-end verification of the AWS pipeline, without an AWS account.

    python aws/local/verify_local.py --curated aws/.local/curated/ingestion_id=<id> \
        --raw aws/.local/raw/powerbi/run_date=<date>/batch=<id> --pg-url postgresql+psycopg2://postgres@127.0.0.1:5433/postgres

What it proves, and what it does not:
  * Athena path - each aws/athena/*.sql file is parsed as Trino SQL (sqlglot, read='trino'), transpiled to
    DuckDB and run over the curated Parquet. Every query carries a matches_published column comparing its
    result with the value the project already published; all rows must match.
  * Redshift path - aws/redshift/schema.sql (minus DISTSTYLE/SORTKEY, which have no Postgres meaning) is
    applied to a local PostgreSQL, which ENFORCES the primary and foreign keys that Redshift only declares.
    The curated Parquet is loaded into stg.*, then the real publish batch from aws/redshift/warehouse.py and
    the generated aws/redshift/analytics.sql run unchanged. Each Redshift query must return exactly the
    rows the Athena version returned.
  * Raw vs curated - every curated value is compared with the raw CSV value it came from, and the file sizes
    of both representations are recorded.
  * NOT proven here: anything AWS-specific (S3 permissions, Glue workers, the Athena engine itself, COPY from
    Parquet, Redshift performance). Those are covered by the AWS run book (aws/README.md) and remain pending
    until run in an account.
Writes aws/benchmarks/local_verification.json and .md.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import re
import sys
import time
from datetime import UTC, date, datetime
from pathlib import Path

import duckdb
import pyarrow.dataset as ds
import sqlglot
from sqlalchemy import create_engine, text

AWS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(AWS / "redshift"))
from warehouse import publish_statements, reconciliation_query  # noqa: E402

CONTRACT = json.loads((AWS / "contract" / "contract.json").read_text(encoding="utf-8"))


def athena_queries() -> list[tuple[str, str]]:
    return [(f.stem, f.read_text(encoding="utf-8")) for f in sorted((AWS / "athena").glob("0*.sql"))]


def redshift_queries() -> dict[str, str]:
    text_ = (AWS / "redshift" / "analytics.sql").read_text(encoding="utf-8")
    parts = re.split(r"^-- name: (\S+)\n", text_, flags=re.M)
    return {parts[i]: parts[i + 1].strip().rstrip(";") for i in range(1, len(parts), 2)}


def read_curated(curated: Path, table: dict) -> list[dict]:
    path = curated / table["name"]
    part = table.get("partition")
    dataset = ds.dataset(path, format="parquet", partitioning="hive" if part else None)
    return dataset.to_table().to_pylist()


# ------------------------------------------------------------------------------------------- Athena path


def run_athena_path(curated: Path) -> tuple[dict, dict]:
    con = duckdb.connect()
    con.execute("CREATE SCHEMA grievance_lens")
    for t in CONTRACT["tables"]:
        glob = (curated / t["name"]).as_posix() + ("/**/*.parquet" if t.get("partition") else "/*.parquet")
        hive = ", hive_partitioning = true" if t.get("partition") else ""
        con.execute(f"CREATE VIEW grievance_lens.{t['name']} AS SELECT * FROM read_parquet('{glob}'{hive})")
    summary, results = {}, {}
    for name, sql in athena_queries():
        sqlglot.parse_one(sql, read="trino")  # raises on anything that is not valid Trino SQL
        duck = sqlglot.transpile(sql, read="trino", write="duckdb")[0]
        if name.startswith("00_"):
            con.execute(duck)
            summary[name] = {"trino_parse": "ok", "created": "view"}
            continue
        t0 = time.perf_counter()
        cur = con.execute(duck)
        cols = [d[0] for d in cur.description]
        rows = [dict(zip(cols, r, strict=True)) for r in cur.fetchall()]
        mism = sum(1 for r in rows if not r["matches_published"])
        summary[name] = {
            "trino_parse": "ok",
            "rows": len(rows),
            "mismatches_vs_published": mism,
            "duckdb_ms": round(1000 * (time.perf_counter() - t0), 1),
        }
        results[name] = rows
    return summary, results


# ----------------------------------------------------------------------------------------- Redshift path


def postgres_ddl() -> list[str]:
    body = "\n".join(
        line
        for line in (AWS / "redshift" / "schema.sql").read_text(encoding="utf-8").splitlines()
        if not line.strip().startswith("--")
    )
    out = []
    for stmt in (s.strip() for s in re.split(r";\s*\n", body) if s.strip()):
        sqlglot.parse_one(stmt, read="redshift")  # Redshift-dialect syntax check of the real DDL
        stmt = re.sub(
            r"\)\s*DISTSTYLE.*$", ")", stmt, flags=re.S
        )  # DISTSTYLE/DISTKEY/SORTKEY: no PG equivalent
        out.append(stmt.replace("GETDATE()", "now()"))
    return out


def run_redshift_path(curated: Path, pg_url: str, pointer: dict) -> tuple[dict, dict]:
    admin = create_engine(pg_url, isolation_level="AUTOCOMMIT")
    with admin.connect() as c:
        c.execute(text("DROP DATABASE IF EXISTS gl_warehouse_local"))
        c.execute(text("CREATE DATABASE gl_warehouse_local"))
    admin.dispose()
    eng = create_engine(pg_url.rsplit("/", 1)[0] + "/gl_warehouse_local")
    summary: dict = {}
    with eng.begin() as c:
        for stmt in postgres_ddl():
            c.execute(text(stmt))
    summary["schema_statements"] = len(postgres_ddl())
    # stg.* stands in for "COPY stg.<t> FROM '<manifest>' FORMAT AS PARQUET": same rows, same column order.
    with eng.begin() as c:
        for t in CONTRACT["load_order"]:
            table = next(x for x in CONTRACT["tables"] if x["name"] == t)
            names = [col["name"] for col in table["columns"]]
            rows = [{k: r[k] for k in names} for r in read_curated(curated, table)]
            if rows:
                c.execute(
                    text(
                        f"INSERT INTO stg.{t} ({', '.join(chr(34) + n + chr(34) for n in names)}) "
                        f"VALUES ({', '.join(':' + n for n in names)})"
                    ),
                    rows,
                )
        c.execute(text(f"INSERT INTO stg.batch_marker (ingestion_id) VALUES ('{pointer['ingestion_id']}')"))
    # The real publish transaction (guard + DELETE/INSERT per table + audit), unchanged.
    with eng.begin() as c:
        for stmt in publish_statements(pointer):
            c.execute(text(stmt))
    summary["publish_statements"] = len(publish_statements(pointer))
    with eng.connect() as c:
        rec = c.execute(text(reconciliation_query(pointer))).fetchall()
    summary["reconciliation"] = {r[0]: {"warehouse_rows": int(r[1]), "curated_rows": int(r[2])} for r in rec}
    # A wrong ingestion must be refused by the guard (division by zero aborts the transaction).
    bad = dict(pointer, ingestion_id="1999-01-01_wrongbatch")
    try:
        with eng.begin() as c:
            for stmt in publish_statements(bad):
                c.execute(text(stmt))
        summary["guard_refuses_other_ingestion"] = False
    except Exception:  # noqa: BLE001 - any database error means the guard worked
        summary["guard_refuses_other_ingestion"] = True
    with eng.begin() as c:
        view = (AWS / "redshift" / "views.sql").read_text(encoding="utf-8")
        view_sql = "\n".join(line for line in view.splitlines() if not line.strip().startswith("--"))
        sqlglot.parse_one(view_sql, read="redshift")
        c.execute(text(view_sql))
    results = {}
    with eng.connect() as c:
        for name, sql in redshift_queries().items():
            sqlglot.parse_one(sql, read="redshift")
            res = c.execute(text(sql))
            cols = list(res.keys())
            results[name] = [dict(zip(cols, r, strict=True)) for r in res.fetchall()]
    eng.dispose()
    return summary, results


def same_value(a, b) -> bool:
    if a is None or b is None:
        return a is None and b is None
    if isinstance(a, float) or isinstance(b, float):
        return math.isclose(float(a), float(b), rel_tol=1e-9, abs_tol=1e-9)
    if isinstance(a, datetime) or isinstance(b, datetime):
        return str(a)[:10] == str(b)[:10]
    return str(a) == str(b) if isinstance(a, date) or isinstance(b, date) else a == b


def compare_results(athena: dict, redshift: dict) -> dict:
    out = {}
    for name, rows in athena.items():
        other = redshift.get(name, [])
        key_cols = [c for c in rows[0] if c in ("month", "ministry_id", "list_code")] if rows else []

        def ordered(rs, cols=tuple(key_cols)):
            return sorted(rs, key=lambda r: tuple(str(r[k]) for k in cols))

        a_rows, r_rows = ordered(rows), ordered(other)
        diffs = 0
        if len(a_rows) != len(r_rows):
            diffs = abs(len(a_rows) - len(r_rows))
        else:
            for ra, rr in zip(a_rows, r_rows, strict=True):
                if any(not same_value(ra[c], rr.get(c)) for c in ra):
                    diffs += 1
        out[name] = {"athena_rows": len(a_rows), "redshift_rows": len(r_rows), "differing_rows": diffs}
    return out


# ---------------------------------------------------------------------------------------- raw vs curated


def parse_raw(value: str, col_type: str):
    if value == "":
        return None
    return {
        "int": int,
        "double": float,
        "string": str,
        "boolean": lambda v: v == "True",
        "date": date.fromisoformat,
    }[col_type](value)


def raw_vs_curated(raw: Path, curated: Path) -> dict:
    out = {}
    for t in CONTRACT["tables"]:
        with open(raw / t["name"] / f"{t['name']}.csv", encoding="utf-8", newline="") as fh:
            raw_rows = list(csv.DictReader(fh))
        cur = read_curated(curated, t)
        key = t["primary_key"]
        index = {tuple(str(r[k]) for k in key): r for r in cur}
        mismatched = 0
        for r in raw_rows:
            c = index.get(
                tuple(
                    str(parse_raw(r[k], next(x["type"] for x in t["columns"] if x["name"] == k))) for k in key
                )
            )
            if c is None or any(
                parse_raw(r[col["name"]], col["type"]) != c[col["name"]] for col in t["columns"]
            ):
                mismatched += 1
        raw_bytes = (raw / t["name"] / f"{t['name']}.csv").stat().st_size
        cur_bytes = sum(p.stat().st_size for p in (curated / t["name"]).rglob("*.parquet"))
        out[t["name"]] = {
            "raw_rows": len(raw_rows),
            "curated_rows": len(cur),
            "value_mismatches": mismatched,
            "csv_bytes": raw_bytes,
            "parquet_bytes": cur_bytes,
        }
    return out


def render_md(report: dict) -> str:
    rv = report["raw_vs_curated"]
    csv_total = sum(v["csv_bytes"] for v in rv.values())
    pq_total = sum(v["parquet_bytes"] for v in rv.values())
    lines = [
        "# Local verification of the AWS pipeline (no AWS account involved)",
        "",
        f"Generated {report['generated_at']} by `python aws/local/verify_local.py` for ingestion "
        f"`{report['ingestion_id']}`. These are **local** results: Spark local mode, DuckDB in place of Athena, "
        "PostgreSQL in place of Redshift. They show the code and SQL are correct on the real exports; they are "
        "not AWS measurements (see [benchmark_report_template.md](benchmark_report_template.md)).",
        "",
        "## ETL (aws/glue/etl_job.py, local Spark)",
        "",
        f"- Status **{report['etl']['status']}**; {report['etl']['rows_processed']:,} rows processed, "
        f"{report['etl']['rows_rejected']} rejected; {report['etl']['duration_seconds']} s; Parquet writer: "
        f"`{report['etl']['parquet_writer']}`; Spark {report['etl']['spark_version']}.",
        f"- Critical issues: {len(report['etl']['critical_issues'])}.",
        "",
        "## Raw CSV vs curated Parquet",
        "",
        "| Table | Raw rows | Curated rows | Value mismatches | CSV bytes | Parquet bytes |",
        "|---|---:|---:|---:|---:|---:|",
        *[
            f"| {k} | {v['raw_rows']:,} | {v['curated_rows']:,} | {v['value_mismatches']} | {v['csv_bytes']:,} | "
            f"{v['parquet_bytes']:,} |"
            for k, v in rv.items()
        ],
        f"| **total** | **{sum(v['raw_rows'] for v in rv.values()):,}** | "
        f"**{sum(v['curated_rows'] for v in rv.values()):,}** | "
        f"**{sum(v['value_mismatches'] for v in rv.values())}** | **{csv_total:,}** | **{pq_total:,}** |",
        "",
        f"Parquet totals {pq_total / csv_total:.0%} of the CSV bytes. At this size Parquet's fixed per-file "
        "metadata is significant, so small tables can be larger as Parquet; Athena bytes-scanned savings come "
        "mainly from reading only the referenced columns, which only an Athena run can measure.",
        "",
        "## Athena SQL (Trino syntax, executed via DuckDB)",
        "",
        "| Query | Trino parse | Rows | Mismatches vs published values |",
        "|---|---|---:|---:|",
        *[
            f"| {k} | {v['trino_parse']} | {v.get('rows', 'view')} | {v.get('mismatches_vs_published', '-')} |"
            for k, v in report["athena"].items()
        ],
        "",
        "## Redshift SQL (executed unchanged on PostgreSQL)",
        "",
        f"- Schema: {report['redshift']['schema_statements']} statements applied with keys enforced.",
        f"- Publish batch: {report['redshift']['publish_statements']} statements, one transaction.",
        f"- Publish guard refuses a different ingestion: **{report['redshift']['guard_refuses_other_ingestion']}**.",
        "- Row-count reconciliation (warehouse vs curated): "
        + (
            "all tables match"
            if all(
                v["warehouse_rows"] == v["curated_rows"]
                for v in report["redshift"]["reconciliation"].values()
            )
            else "MISMATCH"
        ),
        "",
        "| Query | Athena rows | Redshift rows | Differing rows |",
        "|---|---:|---:|---:|",
        *[
            f"| {k} | {v['athena_rows']} | {v['redshift_rows']} | {v['differing_rows']} |"
            for k, v in report["athena_vs_redshift"].items()
        ],
        "",
    ]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--curated", type=Path, required=True)
    p.add_argument("--raw", type=Path, required=True)
    p.add_argument("--quality-report", type=Path, required=True)
    p.add_argument(
        "--pg-url", required=True, help="SQLAlchemy URL of a PostgreSQL server (a scratch DB is created)"
    )
    p.add_argument("--out", type=Path, default=AWS / "benchmarks" / "local_verification")
    a = p.parse_args(argv)
    etl = json.loads(a.quality_report.read_text(encoding="utf-8"))
    pointer = {
        "ingestion_id": etl["ingestion_id"],
        "contract_hash": etl["contract_hash"],
        "redshift_manifest_prefix": "s3://local-verification-placeholder/control/redshift_manifests/",
        "load_order": CONTRACT["load_order"],
        "table_rows": {k: v["rows_written"] for k, v in etl["tables"].items()},
    }
    athena_summary, athena_rows = run_athena_path(a.curated)
    redshift_summary, redshift_rows = run_redshift_path(a.curated, a.pg_url, pointer)
    report = {
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "ingestion_id": etl["ingestion_id"],
        "etl": {
            k: etl[k]
            for k in (
                "status",
                "rows_processed",
                "rows_rejected",
                "duration_seconds",
                "parquet_writer",
                "spark_version",
                "critical_issues",
            )
        },
        "raw_vs_curated": raw_vs_curated(a.raw, a.curated),
        "athena": athena_summary,
        "redshift": redshift_summary,
        "athena_vs_redshift": compare_results(athena_rows, redshift_rows),
    }
    a.out.parent.mkdir(parents=True, exist_ok=True)
    a.out.with_suffix(".json").write_text(json.dumps(report, indent=2, default=str) + "\n", encoding="utf-8")
    a.out.with_suffix(".md").write_text(render_md(report), encoding="utf-8")
    failed = (
        report["etl"]["critical_issues"]
        or any(
            v["value_mismatches"] or v["raw_rows"] != v["curated_rows"]
            for v in report["raw_vs_curated"].values()
        )
        or any(v.get("mismatches_vs_published") for v in athena_summary.values())
        or any(v["differing_rows"] for v in report["athena_vs_redshift"].values())
        or not redshift_summary["guard_refuses_other_ingestion"]
        or any(v["warehouse_rows"] != v["curated_rows"] for v in redshift_summary["reconciliation"].values())
    )
    print(render_md(report))
    print("LOCAL VERIFICATION " + ("FAILED" if failed else "PASSED"))
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
