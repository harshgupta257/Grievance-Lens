"""Schema-drift audit: crawl the published curated Parquet and compare what Glue infers with the contract.

    python aws/catalog/crawler_audit.py      # GL_BUCKET, GL_REGION; crawler from deploy.py --with-crawler

The crawler is NOT how tables are defined: the ETL job publishes the authoritative tables from the contract
(aws/catalog/crawler_config.md explains why). Here the crawler writes into a separate database,
grievance_lens_crawler_audit, with LOG-only schema-change behaviour, and this script reports any column
whose name or type differs from the contract. A difference means the Parquet files and the contract
disagree, which the ETL's own read-back check should already have stopped; the audit is an independent
second opinion. Exit code 1 if anything differs.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

AWS = Path(__file__).resolve().parents[1]
GLUE_TYPES = {"int": "int", "double": "double", "boolean": "boolean", "date": "date", "string": "string"}


def compare_schemas(contract: dict[str, Any], crawled: dict[str, list[dict[str, str]]]) -> list[str]:
    """crawled: {table_name: [{'Name': ..., 'Type': ...}, ...]} including partition keys."""
    problems = []
    for t in contract["tables"]:
        cols = crawled.get(t["name"])
        if cols is None:
            problems.append(f"{t['name']}: not found by the crawler")
            continue
        want = {c["name"]: GLUE_TYPES[c["type"]] for c in t["columns"]}
        if t.get("partition"):
            want[t["partition"]["column"]] = "int"
        got = {c["Name"]: c["Type"].lower() for c in cols}
        for name in sorted(set(want) | set(got)):
            if want.get(name) != got.get(name):
                problems.append(f"{t['name']}.{name}: contract={want.get(name)} crawled={got.get(name)}")
    return problems


def main(argv: list[str] | None = None) -> int:
    import boto3

    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--bucket", default=os.environ.get("GL_BUCKET"))
    p.add_argument("--region", default=os.environ.get("GL_REGION", "ap-south-1"))
    p.add_argument("--crawler", default="grievance-lens-schema-audit")
    p.add_argument("--database", default="grievance_lens_crawler_audit")
    a = p.parse_args(argv)
    if not a.bucket:
        p.error("--bucket or GL_BUCKET is required")
    s3, glue = boto3.client("s3", region_name=a.region), boto3.client("glue", region_name=a.region)
    pointer = json.loads(s3.get_object(Bucket=a.bucket, Key="control/published/latest.json")["Body"].read())
    contract = json.loads((AWS / "contract" / "contract.json").read_text(encoding="utf-8"))
    targets = [{"Path": pointer["curated_prefix"] + t["name"] + "/"} for t in contract["tables"]]
    glue.update_crawler(Name=a.crawler, Targets={"S3Targets": targets})
    glue.start_crawler(Name=a.crawler)
    while glue.get_crawler(Name=a.crawler)["Crawler"]["State"] != "READY":
        time.sleep(15)
    crawled: dict[str, list[dict[str, str]]] = {}
    for page in glue.get_paginator("get_tables").paginate(DatabaseName=a.database):
        for t in page["TableList"]:
            crawled[t["Name"]] = t["StorageDescriptor"]["Columns"] + t.get("PartitionKeys", [])
    problems = compare_schemas(contract, crawled)
    print(f"crawled ingestion {pointer['ingestion_id']}: {len(crawled)} tables")
    for line in problems:
        print("  DRIFT", line)
    print("no schema drift" if not problems else f"{len(problems)} difference(s)")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
