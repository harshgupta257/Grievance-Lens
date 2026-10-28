"""Refresh the Redshift Serverless warehouse from the latest published curated batch (explicit, manual step).

    python aws/scripts/refresh_warehouse.py --dry-run                      # print the SQL, touch nothing
    python aws/scripts/refresh_warehouse.py --create-schema                # once: schema.sql + views.sql
    python aws/scripts/refresh_warehouse.py                                # stage -> verify -> publish -> reconcile
    python aws/scripts/refresh_warehouse.py --ingestion-id 2026-10-27_20261027T101500Z   # a specific batch

Settings come from flags or environment variables (aws/config/pipeline.env.example):
    GL_BUCKET, GL_REGION, GL_REDSHIFT_WORKGROUP, GL_REDSHIFT_DATABASE (default dev), GL_REDSHIFT_ROLE_ARN

Safety:
  * reads control/published/latest.json (or history/<id>.json): only batches the Glue job published, i.e.
    batches that passed every data-quality check, can be loaded;
  * takes an S3 lock (control/warehouse/refresh.lock, If-None-Match) so two refreshes cannot interleave;
  * stage and publish are separate transactions (aws/redshift/warehouse.py); gl.* changes only if both succeed;
  * finishes with a row-count reconciliation of every gl.* table against the published counts.
Redshift Serverless bills compute while statements run (RPU-seconds); this command runs a few dozen
small statements and then stops.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

import boto3
from botocore.exceptions import ClientError

AWS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(AWS / "redshift"))
from warehouse import (  # noqa: E402
    publish_statements,
    reconciliation_query,
    stage_statements,
)

LOCK_KEY = "control/warehouse/refresh.lock"


def split_sql(text: str) -> list[str]:
    """Statements from a .sql file: comment lines dropped, split on ';' at line ends."""
    body = "\n".join(line for line in text.splitlines() if not line.strip().startswith("--"))
    return [s.strip() for s in re.split(r";\s*\n", body + "\n") if s.strip()]


class DataApi:
    def __init__(self, client, workgroup: str, database: str):
        self.c, self.workgroup, self.database = client, workgroup, database

    def _wait(self, statement_id: str) -> dict:
        while True:
            d = self.c.describe_statement(Id=statement_id)
            if d["Status"] in ("FINISHED", "FAILED", "ABORTED"):
                if d["Status"] != "FINISHED":
                    raise RuntimeError(f"statement {statement_id} {d['Status']}: {d.get('Error', '')}")
                return d
            time.sleep(2)

    def batch(self, sqls: list[str], name: str) -> dict:
        r = self.c.batch_execute_statement(
            WorkgroupName=self.workgroup, Database=self.database, Sqls=sqls, StatementName=name
        )
        return self._wait(r["Id"])

    def query(self, sql: str) -> list[list]:
        r = self.c.execute_statement(WorkgroupName=self.workgroup, Database=self.database, Sql=sql)
        self._wait(r["Id"])
        rows, token = [], None
        while True:
            kw = {"Id": r["Id"], **({"NextToken": token} if token else {})}
            page = self.c.get_statement_result(**kw)
            rows += [[next(iter(v.values())) if v else None for v in rec] for rec in page["Records"]]
            token = page.get("NextToken")
            if not token:
                return rows


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--bucket", default=os.environ.get("GL_BUCKET"))
    p.add_argument("--region", default=os.environ.get("GL_REGION", "ap-south-1"))
    p.add_argument("--workgroup", default=os.environ.get("GL_REDSHIFT_WORKGROUP"))
    p.add_argument("--database", default=os.environ.get("GL_REDSHIFT_DATABASE", "dev"))
    p.add_argument("--iam-role-arn", default=os.environ.get("GL_REDSHIFT_ROLE_ARN"))
    p.add_argument("--ingestion-id", help="default: control/published/latest.json")
    p.add_argument("--create-schema", action="store_true", help="run schema.sql and views.sql, then exit")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--force-unlock", action="store_true", help="remove a stale refresh lock left by a crash")
    a = p.parse_args(argv)
    for name in ("bucket", "workgroup", "iam_role_arn"):
        if not getattr(a, name) and not (a.dry_run and name != "bucket"):
            p.error(f"--{name.replace('_', '-')} (or its GL_* environment variable) is required")

    s3 = boto3.client("s3", region_name=a.region)
    key = (
        f"control/published/history/{a.ingestion_id}.json"
        if a.ingestion_id
        else "control/published/latest.json"
    )
    pointer = json.loads(s3.get_object(Bucket=a.bucket, Key=key)["Body"].read())
    role = a.iam_role_arn or "arn:aws:iam::123456789012:role/DRY-RUN-PLACEHOLDER"
    stage, publish = stage_statements(pointer, role), publish_statements(pointer)
    if a.dry_run:
        print(f"-- ingestion {pointer['ingestion_id']}: stage batch ({len(stage)} statements)")
        print(";\n".join(stage) + ";")
        print(f"\n-- publish batch ({len(publish)} statements)")
        print(";\n".join(publish) + ";")
        return 0

    api = DataApi(boto3.client("redshift-data", region_name=a.region), a.workgroup, a.database)
    if a.create_schema:
        for f in ("schema.sql", "views.sql"):
            sqls = split_sql((AWS / "redshift" / f).read_text(encoding="utf-8"))
            for i in range(0, len(sqls), 40):
                api.batch(sqls[i : i + 40], f"grievance-lens-{f.split('.')[0]}")
            print(f"applied {f} ({len(sqls)} statements)")
        return 0

    if a.force_unlock:
        s3.delete_object(Bucket=a.bucket, Key=LOCK_KEY)
    try:
        s3.put_object(
            Bucket=a.bucket,
            Key=LOCK_KEY,
            IfNoneMatch="*",
            ContentType="application/json",
            Body=json.dumps(
                {"ingestion_id": pointer["ingestion_id"], "at": datetime.now(UTC).isoformat()}
            ).encode(),
        )
    except ClientError as exc:
        if exc.response.get("Error", {}).get("Code") in ("PreconditionFailed", "ConditionalRequestConflict"):
            print(f"another refresh holds s3://{a.bucket}/{LOCK_KEY}; use --force-unlock only if it crashed")
            return 2
        raise
    started = time.time()
    try:
        api.batch(stage, f"gl-stage-{pointer['ingestion_id']}"[:500])
        print(f"staged and verified {len(pointer['load_order'])} tables")
        api.batch(publish, f"gl-publish-{pointer['ingestion_id']}"[:500])
        print("published to gl.*")
        rows = api.query(reconciliation_query(pointer))
    finally:
        s3.delete_object(Bucket=a.bucket, Key=LOCK_KEY)
    bad = [r for r in rows if int(r[1]) != int(r[2])]
    for r in rows:
        print(
            f"  {r[0]:<28} warehouse={r[1]:>6} curated={r[2]:>6} {'OK' if int(r[1]) == int(r[2]) else 'MISMATCH'}"
        )
    result = {
        "ingestion_id": pointer["ingestion_id"],
        "finished_at": datetime.now(UTC).isoformat(),
        "seconds": round(time.time() - started, 1),
        "tables": {r[0]: {"warehouse_rows": int(r[1]), "curated_rows": int(r[2])} for r in rows},
        "status": "RECONCILED" if not bad else "MISMATCH",
    }
    s3.put_object(
        Bucket=a.bucket,
        Key=f"control/warehouse/refreshes/{pointer['ingestion_id']}.json",
        Body=json.dumps(result, indent=2).encode(),
        ContentType="application/json",
    )
    print(f"{result['status']} in {result['seconds']}s")
    return 0 if not bad else 1


if __name__ == "__main__":
    raise SystemExit(main())
