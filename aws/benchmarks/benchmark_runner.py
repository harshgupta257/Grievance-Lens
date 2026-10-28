"""CSV vs Parquet benchmark on Amazon Athena, from real query execution metadata only.

    python aws/benchmarks/benchmark_runner.py --repetitions 5
    (settings: GL_BUCKET, GL_REGION, GL_ATHENA_WORKGROUP; the latest published batch is used by default)

Method:
  1. (Re)create grievance_lens_raw.* over the raw CSV batch of the published ingestion
     (aws/athena/raw_csv_tables.sql), so CSV and Parquet represent the same logical data.
  2. For every pair in aws/athena/benchmark_queries.sql, run CSV and Parquet alternately, `--repetitions`
     times each, with Athena query-result reuse DISABLED (otherwise repeat runs read a cached result).
  3. For each execution record QueryExecutionId, submission time, DataScannedInBytes,
     EngineExecutionTimeInMillis, TotalExecutionTimeInMillis, QueryQueueTimeInMillis,
     QueryPlanningTimeInMillis, ServiceProcessingTimeInMillis, the result row count and a hash of the rows.
  4. A pair is reported only if both members returned identical rows on every run.
  5. Write benchmarks/results/<UTC timestamp>.json (all raw metadata) and .md (medians, ranges, bytes-scanned
     reduction). Nothing is estimated: a metric Athena did not return is shown as "n/a".

Cost: Athena bills data scanned with a 10 MB minimum per query, so at this dataset size every query bills
the minimum whatever the format; the benchmark costs roughly (pairs x 2 x repetitions) x 10 MB.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import statistics
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

AWS = Path(__file__).resolve().parents[1]


def load_pairs(path: Path = AWS / "athena" / "benchmark_queries.sql") -> dict[str, dict[str, Any]]:
    text = path.read_text(encoding="utf-8")
    blocks = re.split(r"^-- name: (\S+)\n", text, flags=re.M)
    pairs: dict[str, dict[str, Any]] = {}
    for i in range(1, len(blocks), 2):
        name, body = blocks[i], blocks[i + 1]
        bench, kind = name.rsplit("_", 1)
        if kind not in ("csv", "parquet"):
            raise ValueError(f"benchmark block {name!r} must end in _csv or _parquet")
        m = re.search(r"^-- measures: (.+)$", body, flags=re.M)
        sql = "\n".join(line for line in body.splitlines() if not line.startswith("--")).strip().rstrip(";")
        pairs.setdefault(bench, {"measures": m.group(1) if m else ""})[kind] = sql
    incomplete = [b for b, p in pairs.items() if not {"csv", "parquet"} <= p.keys()]
    if incomplete:
        raise ValueError(f"benchmarks without both members: {incomplete}")
    return pairs


def rows_hash(rows: list[list[Any]]) -> str:
    def norm(v: Any) -> str:
        try:
            f = float(v)
            return f"{f:.9g}"
        except (TypeError, ValueError):
            return "" if v is None else str(v)

    return hashlib.sha256(json.dumps([[norm(v) for v in r] for r in rows]).encode()).hexdigest()[:16]


def stats_row(execution: dict[str, Any]) -> dict[str, Any]:
    s = execution.get("Statistics", {})
    return {
        "query_execution_id": execution["QueryExecutionId"],
        "submitted_at": str(execution.get("Status", {}).get("SubmissionDateTime", "")),
        "state": execution.get("Status", {}).get("State"),
        "data_scanned_bytes": s.get("DataScannedInBytes"),
        "engine_ms": s.get("EngineExecutionTimeInMillis"),
        "total_ms": s.get("TotalExecutionTimeInMillis"),
        "queue_ms": s.get("QueryQueueTimeInMillis"),
        "planning_ms": s.get("QueryPlanningTimeInMillis"),
        "service_ms": s.get("ServiceProcessingTimeInMillis"),
        "result_reused": s.get("ResultReuseInformation", {}).get("ReusedPreviousResult"),
        "output_location": execution.get("ResultConfiguration", {}).get("OutputLocation"),
    }


def summarize(runs: list[dict[str, Any]]) -> dict[str, Any]:
    def med(key: str):
        vals = [r[key] for r in runs if r.get(key) is not None]
        return statistics.median(vals) if vals else None

    def rng(key: str):
        vals = [r[key] for r in runs if r.get(key) is not None]
        return [min(vals), max(vals)] if vals else None

    return {
        "runs": len(runs),
        "median_bytes": med("data_scanned_bytes"),
        "median_engine_ms": med("engine_ms"),
        "median_total_ms": med("total_ms"),
        "engine_ms_range": rng("engine_ms"),
        "total_ms_range": rng("total_ms"),
        "row_counts": sorted({r["rows"] for r in runs}),
        "row_hashes": sorted({r["rows_hash"] for r in runs}),
    }


def compare(csv_s: dict[str, Any], pq_s: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {
        "results_identical": csv_s["row_hashes"] == pq_s["row_hashes"] and len(pq_s["row_hashes"]) == 1
    }
    if csv_s["median_bytes"] and pq_s["median_bytes"] is not None:
        out["bytes_reduction_pct"] = round(100 * (1 - pq_s["median_bytes"] / csv_s["median_bytes"]), 1)
    if csv_s["median_engine_ms"] and pq_s["median_engine_ms"] is not None:
        out["engine_time_change_pct"] = round(
            100 * (pq_s["median_engine_ms"] / csv_s["median_engine_ms"] - 1), 1
        )
    return out


class Athena:
    def __init__(self, client, workgroup: str, output: str | None):
        self.c, self.workgroup, self.output = client, workgroup, output

    def run(self, sql: str) -> tuple[dict[str, Any], list[list[Any]]]:
        kw: dict[str, Any] = {
            "QueryString": sql,
            "WorkGroup": self.workgroup,
            "ResultReuseConfiguration": {"ResultReuseByAgeConfiguration": {"Enabled": False}},
        }
        if self.output:
            kw["ResultConfiguration"] = {"OutputLocation": self.output}
        qid = self.c.start_query_execution(**kw)["QueryExecutionId"]
        while True:
            ex = self.c.get_query_execution(QueryExecutionId=qid)["QueryExecution"]
            state = ex["Status"]["State"]
            if state in ("SUCCEEDED", "FAILED", "CANCELLED"):
                break
            time.sleep(0.5)
        if state != "SUCCEEDED":
            raise RuntimeError(f"query {qid} {state}: {ex['Status'].get('StateChangeReason', '')}")
        rows, token, first = [], None, True
        while True:
            page = self.c.get_query_results(QueryExecutionId=qid, **({"NextToken": token} if token else {}))
            data = [[d.get("VarCharValue") for d in r["Data"]] for r in page["ResultSet"]["Rows"]]
            rows += data[1:] if first else data  # first row of the first page is the header
            first, token = False, page.get("NextToken")
            if not token:
                return ex, rows


def render_report(result: dict[str, Any]) -> str:
    lines = [
        "# Athena benchmark: raw CSV vs curated Parquet",
        "",
        f"Run {result['run_at']} in `{result['region']}`, workgroup `{result['workgroup']}`, ingestion "
        f"`{result['ingestion_id']}`, {result['repetitions']} repetitions per query, result reuse disabled.",
        "All figures are Athena's own execution statistics (raw metadata in the matching .json file).",
        "",
        "| Pair | Measures | Rows identical | CSV bytes (median) | Parquet bytes (median) | Bytes reduction | "
        "CSV engine ms (median, range) | Parquet engine ms (median, range) | Engine time change |",
        "|---|---|---|---:|---:|---:|---:|---:|---:|",
    ]
    for name, p in result["pairs"].items():
        c, q, d = p["csv"]["summary"], p["parquet"]["summary"], p["comparison"]
        lines.append(
            f"| {name} | {p['measures']} | {'yes' if d['results_identical'] else '**NO**'} | "
            f"{c['median_bytes']:,} | {q['median_bytes']:,} | {d.get('bytes_reduction_pct', 'n/a')}% | "
            f"{c['median_engine_ms']} ({c['engine_ms_range']}) | {q['median_engine_ms']} ({q['engine_ms_range']}) | "
            f"{d.get('engine_time_change_pct', 'n/a')}% |"
        )
    lines += [
        "",
        "Reading the results: bytes scanned is deterministic and is where Parquet's column pruning, compression "
        "and partition pruning show. Execution time on a dataset this small is dominated by fixed planning and "
        "scheduling overhead and varies run to run; treat time differences within the observed ranges as noise.",
        "Athena bills at least 10 MB per query, so both formats cost the same per query at this size.",
        "",
    ]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    import boto3

    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--bucket", default=os.environ.get("GL_BUCKET"))
    p.add_argument("--region", default=os.environ.get("GL_REGION", "ap-south-1"))
    p.add_argument("--workgroup", default=os.environ.get("GL_ATHENA_WORKGROUP", "grievance-lens"))
    p.add_argument("--output-location", help="only if the workgroup does not enforce one")
    p.add_argument("--repetitions", type=int, default=5)
    p.add_argument("--ingestion-id", help="default: control/published/latest.json")
    a = p.parse_args(argv)
    if not a.bucket:
        p.error("--bucket or GL_BUCKET is required")
    s3 = boto3.client("s3", region_name=a.region)
    key = (
        f"control/published/history/{a.ingestion_id}.json"
        if a.ingestion_id
        else "control/published/latest.json"
    )
    pointer = json.loads(s3.get_object(Bucket=a.bucket, Key=key)["Body"].read())
    run_date, batch_id = pointer["ingestion_id"].split("_", 1)
    raw_prefix = f"s3://{a.bucket}/raw/powerbi/run_date={run_date}/batch={batch_id}/"
    athena = Athena(boto3.client("athena", region_name=a.region), a.workgroup, a.output_location)

    ddl = (
        (AWS / "athena" / "raw_csv_tables.sql")
        .read_text(encoding="utf-8")
        .replace("${RAW_BATCH_PREFIX}", raw_prefix)
    )
    body = "\n".join(line for line in ddl.splitlines() if not line.strip().startswith("--"))
    for stmt in (s.strip() for s in re.split(r";\s*\n", body) if s.strip()):
        athena.run(stmt)
    print(f"raw CSV tables point at {raw_prefix}")

    pairs = load_pairs()
    result: dict[str, Any] = {
        "run_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "region": a.region,
        "workgroup": a.workgroup,
        "ingestion_id": pointer["ingestion_id"],
        "raw_prefix": raw_prefix,
        "curated_prefix": pointer["curated_prefix"],
        "repetitions": a.repetitions,
        "pairs": {},
    }
    for name, pair in pairs.items():
        runs: dict[str, list[dict[str, Any]]] = {"csv": [], "parquet": []}
        for _ in range(a.repetitions):
            for kind in ("csv", "parquet"):  # alternate, so drift affects both formats alike
                ex, rows = athena.run(pair[kind])
                runs[kind].append({**stats_row(ex), "rows": len(rows), "rows_hash": rows_hash(rows)})
        entry = {"measures": pair["measures"]}
        for kind in ("csv", "parquet"):
            entry[kind] = {"executions": runs[kind], "summary": summarize(runs[kind])}
        entry["comparison"] = compare(entry["csv"]["summary"], entry["parquet"]["summary"])
        result["pairs"][name] = entry
        print(f"{name}: {entry['comparison']}")
    out_dir = AWS / "benchmarks" / "results"
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = out_dir / datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    stem.with_suffix(".json").write_text(json.dumps(result, indent=2, default=str) + "\n", encoding="utf-8")
    stem.with_suffix(".md").write_text(render_report(result), encoding="utf-8")
    print(f"wrote {stem}.json and .md")
    bad = [n for n, e in result["pairs"].items() if not e["comparison"]["results_identical"]]
    if bad:
        print(f"pairs with different results (not comparable): {bad}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
