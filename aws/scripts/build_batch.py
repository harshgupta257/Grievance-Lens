"""Stage one ingestion batch of the Power BI CSV exports and write its _manifest.json.

    python aws/scripts/build_batch.py                                   # today's run_date, new batch id
    python aws/scripts/build_batch.py --run-date 2026-10-27 --batch-id 20261027T101500Z --out DIR

Called by upload_exports.sh / upload_exports.ps1, which then `aws s3 sync` the staged folder and upload the
manifest LAST. The manifest is the batch-completion marker: the S3 notification that starts the pipeline
fires only for `_manifest.json`, never for the CSVs themselves.

Checks before staging (any failure stops with exit code 1, nothing is staged):
  * every table in the schema contract has a CSV, and there are no unexpected CSVs;
  * each CSV's SHA-256 and row count match data/processed/powerbi/export_manifest.csv, i.e. the files are
    exactly what `python -m src.export.powerbi` validated and wrote.

Staged layout (one folder per table, because an Athena/Glue table location must hold only that table):
    <out>/run_date=YYYY-MM-DD/batch=<batch_id>/<table>/<table>.csv
    <out>/run_date=YYYY-MM-DD/batch=<batch_id>/_manifest.json
Files are copied byte-for-byte; nothing in data/processed/powerbi is modified.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import shutil
import sys
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_SOURCE = ROOT / "data" / "processed" / "powerbi"
CONTRACT = ROOT / "aws" / "contract" / "contract.json"
BATCH_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def data_rows(path: Path) -> int:
    with open(path, encoding="utf-8", newline="") as fh:
        return sum(1 for _ in csv.reader(fh)) - 1  # minus the header; quoted newlines count once


def build_manifest(source: Path, run_date: str, batch_id: str, contract: dict) -> tuple[dict, list[str]]:
    problems: list[str] = []
    export_manifest = {
        r["table"]: r for r in csv.DictReader(open(source / "export_manifest.csv", encoding="utf-8"))
    }
    wanted = [t["name"] for t in contract["tables"]]
    present = {p.stem for p in source.glob("*.csv")} - {"export_manifest", "data_dictionary"}
    if missing := sorted(set(wanted) - present):
        problems.append(f"missing CSV(s) for contract tables: {missing}")
    if extra := sorted(present - set(wanted)):
        problems.append(f"unexpected CSV(s) not in the contract: {extra}")
    files = []
    for table in wanted:
        path = source / f"{table}.csv"
        if not path.exists():
            continue
        digest, rows = sha256(path), data_rows(path)
        exp = export_manifest.get(table)
        if exp is None:
            problems.append(f"{table}: not listed in export_manifest.csv")
        else:
            if exp["sha256"] != digest:
                problems.append(f"{table}: SHA-256 differs from export_manifest.csv (re-run the export)")
            if int(exp["rows"]) != rows:
                problems.append(f"{table}: {rows} rows, export_manifest.csv says {exp['rows']}")
        files.append(
            {
                "table": table,
                "path": f"{table}/{table}.csv",
                "bytes": path.stat().st_size,
                "sha256": digest,
                "rows": rows,
            }
        )
    manifest = {
        "manifest_version": 1,
        "dataset": contract["dataset"],
        "run_date": run_date,
        "batch_id": batch_id,
        "ingestion_id": f"{run_date}_{batch_id}",
        "created_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "contract_hash": contract["contract_hash"],
        "source": "data/processed/powerbi",
        "files": files,
    }
    return manifest, problems


def stage(source: Path, out_root: Path, manifest: dict) -> Path:
    batch_dir = out_root / f"run_date={manifest['run_date']}" / f"batch={manifest['batch_id']}"
    if batch_dir.exists():
        raise FileExistsError(f"{batch_dir} already exists: batches are immutable, use a new --batch-id")
    for f in manifest["files"]:
        dest = batch_dir / f["path"]
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source / f"{f['table']}.csv", dest)
        if sha256(dest) != f["sha256"]:
            raise OSError(f"copy of {f['table']} does not match its source")
    (batch_dir / "_manifest.json").write_text(
        json.dumps(manifest, indent=2) + "\n", encoding="utf-8", newline="\n"
    )
    return batch_dir


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--out", type=Path, default=ROOT / "aws" / ".local" / "staging")
    parser.add_argument("--run-date", default=datetime.now(UTC).strftime("%Y-%m-%d"))
    parser.add_argument("--batch-id", default=datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ"))
    parser.add_argument("--print-dir", action="store_true", help="print only the staged batch directory")
    args = parser.parse_args(argv)
    if not re.match(r"^\d{4}-\d{2}-\d{2}$", args.run_date):
        parser.error("--run-date must be YYYY-MM-DD")
    if not BATCH_ID.match(args.batch_id):
        parser.error("--batch-id may contain only letters, digits, '-' and '_' (max 64)")
    contract = json.loads(CONTRACT.read_text(encoding="utf-8"))
    manifest, problems = build_manifest(args.source, args.run_date, args.batch_id, contract)
    if problems:
        for p in problems:
            print(f"ERROR: {p}", file=sys.stderr)
        return 1
    batch_dir = stage(args.source, args.out, manifest)
    if args.print_dir:
        print(batch_dir)
    else:
        rows = sum(f["rows"] for f in manifest["files"])
        print(f"staged {len(manifest['files'])} tables, {rows} rows -> {batch_dir}")
        print(f"ingestion_id: {manifest['ingestion_id']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
