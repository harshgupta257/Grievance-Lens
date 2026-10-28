"""Lambda: start the Grievance Lens Glue ETL when a complete export batch has been uploaded to S3.

Trigger: S3 ObjectCreated notifications filtered to prefix `raw/powerbi/` and suffix `_manifest.json`.
The upload scripts write the 15 CSVs first and the manifest last, so the manifest marks a finished batch.
The function only orchestrates; it never reads the CSV contents.

For each notification record:
  1. Ignore anything that is not  s3://$BUCKET/raw/powerbi/run_date=YYYY-MM-DD/batch=<id>/_manifest.json
     (other buckets, other prefixes, curated/ or control/ writes, CSVs): no recursion, no stray runs.
  2. Read and validate the manifest: run_date/batch match the key, it lists exactly the contract's tables,
     every file sits inside this batch, and its contract_hash matches the deployed contract.
     An invalid manifest is reported and NOT retried (retrying cannot fix it).
  3. HEAD every listed CSV and compare its size with the manifest. If any is missing or still the wrong
     size, raise BatchIncomplete: Lambda's asynchronous retries (and S3's at-least-once delivery) re-check
     later, which covers out-of-order arrival. After 2 retries the event is dropped; every attempt is logged.
  4. Take the batch lock: PUT control/batches/run_date=.../batch=.../lock.json with If-None-Match: *
     (S3 conditional write). Only one invocation can create it, so duplicate notifications are ignored.
  5. Start the Glue job with the batch's parameters. If Glue refuses (e.g. MaxConcurrentRuns reached), the
     lock is released and the error re-raised so the retry can start it later.
  6. Record the Glue JobRunId in the lock object and log a structured line with both identifiers.

The Redshift warehouse is NOT refreshed from here: that is an explicit command run after a successful,
published batch (aws/redshift/README.md).
"""

from __future__ import annotations

import json
import logging
import os
import re
from datetime import UTC, datetime
from typing import Any
from urllib.parse import unquote_plus

import boto3
from botocore.exceptions import ClientError

LOG = logging.getLogger()
LOG.setLevel(os.environ.get("LOG_LEVEL", "INFO"))

MANIFEST_KEY = re.compile(
    r"^(?P<prefix>raw/powerbi/)run_date=(?P<run_date>\d{4}-\d{2}-\d{2})/batch=(?P<batch>[A-Za-z0-9][A-Za-z0-9_-]{0,63})"
    r"/_manifest\.json$"
)
MAX_MANIFEST_BYTES = 1_000_000
LOCK_TAKEN_CODES = {"PreconditionFailed", "ConditionalRequestConflict"}


class BatchIncomplete(Exception):
    """Some listed files are not (fully) uploaded yet. Raised so that Lambda retries the event."""


class RetryLater(Exception):
    """Glue could not start the run now (e.g. concurrency limit). Raised so that Lambda retries."""


def log_event(**fields: Any) -> None:
    LOG.info(json.dumps({"ts": datetime.now(UTC).isoformat(timespec="seconds"), **fields}, default=str))


class Settings:
    def __init__(self, env: dict[str, str]):
        try:
            self.bucket = env["BUCKET"]
            self.glue_job = env["GLUE_JOB_NAME"]
            self.contract_key = env.get("CONTRACT_KEY", "scripts/contract.json")
        except KeyError as exc:
            raise RuntimeError(f"missing environment variable {exc}") from exc
        self.control_prefix = env.get("CONTROL_PREFIX", "control/").rstrip("/") + "/"
        self.curated_prefix = env.get("CURATED_PREFIX", "curated/").rstrip("/") + "/"
        self.glue_database = env.get("GLUE_DATABASE", "grievance_lens")
        self.max_reject_ratio = env.get("MAX_REJECT_RATIO", "0")

    def s3_uri(self, key: str) -> str:
        return f"s3://{self.bucket}/{key}"


def parse_record(record: dict[str, Any], settings: Settings) -> tuple[dict[str, str] | None, str]:
    """(batch, reason): batch is None when the record must be ignored."""
    if record.get("eventSource") != "aws:s3" or not str(record.get("eventName", "")).startswith(
        "ObjectCreated"
    ):
        return None, "not an S3 ObjectCreated event"
    s3 = record.get("s3", {})
    bucket = s3.get("bucket", {}).get("name")
    key = unquote_plus(s3.get("object", {}).get("key", ""))  # event keys are URL-encoded
    if bucket != settings.bucket:
        return None, f"unexpected bucket {bucket!r}"
    m = MANIFEST_KEY.match(key)
    if not m:
        return None, f"not a batch manifest: {key!r}"
    size = int(s3.get("object", {}).get("size", 0))
    if size > MAX_MANIFEST_BYTES:
        return None, f"manifest too large ({size} bytes)"
    batch_prefix = key[: -len("_manifest.json")]
    return {"key": key, "run_date": m["run_date"], "batch_id": m["batch"], "batch_prefix": batch_prefix}, "ok"


def validate_manifest(manifest: dict[str, Any], batch: dict[str, str], contract: dict[str, Any]) -> list[str]:
    problems = []
    if manifest.get("manifest_version") != 1:
        problems.append(f"unsupported manifest_version {manifest.get('manifest_version')!r}")
    if manifest.get("run_date") != batch["run_date"] or manifest.get("batch_id") != batch["batch_id"]:
        problems.append("run_date/batch_id in the manifest do not match its S3 key")
    if manifest.get("contract_hash") != contract.get("contract_hash"):
        problems.append(
            f"contract_hash {manifest.get('contract_hash')!r} differs from the deployed contract "
            f"{contract.get('contract_hash')!r}: redeploy the contract or rebuild the batch"
        )
    files = manifest.get("files") or []
    tables = [f.get("table") for f in files]
    expected = {t["name"] for t in contract.get("tables", [])}
    if len(tables) != len(set(tables)) or set(tables) != expected:
        problems.append(
            f"tables differ from the contract: missing={sorted(expected - set(tables))} "
            f"unexpected={sorted(set(tables) - expected)}"
        )
    for f in files:
        path = str(f.get("path", ""))
        if path.startswith("/") or ".." in path.split("/") or not path.endswith(".csv"):
            problems.append(f"file path {path!r} is not a CSV inside the batch")
        if not isinstance(f.get("bytes"), int) or f["bytes"] < 0:
            problems.append(f"file {path!r} has no valid byte size")
    return problems


def missing_files(s3, settings: Settings, batch: dict[str, str], manifest: dict[str, Any]) -> list[str]:
    missing = []
    for f in manifest["files"]:
        key = batch["batch_prefix"] + f["path"]
        try:
            head = s3.head_object(Bucket=settings.bucket, Key=key)
        except ClientError as exc:
            if exc.response.get("Error", {}).get("Code") in ("404", "NoSuchKey", "NotFound"):
                missing.append(f"{f['path']} (not found)")
                continue
            raise
        if int(head["ContentLength"]) != int(f["bytes"]):
            missing.append(f"{f['path']} ({head['ContentLength']} of {f['bytes']} bytes)")
    return missing


def lock_key(settings: Settings, batch: dict[str, str]) -> str:
    return (
        f"{settings.control_prefix}batches/run_date={batch['run_date']}/batch={batch['batch_id']}/lock.json"
    )


def acquire_lock(s3, settings: Settings, batch: dict[str, str], request_id: str) -> bool:
    body = {
        "state": "STARTING",
        "request_id": request_id,
        "acquired_at": datetime.now(UTC).isoformat(),
        **batch,
    }
    try:
        s3.put_object(
            Bucket=settings.bucket,
            Key=lock_key(settings, batch),
            Body=json.dumps(body).encode(),
            ContentType="application/json",
            IfNoneMatch="*",
        )
        return True
    except ClientError as exc:
        if exc.response.get("Error", {}).get("Code") in LOCK_TAKEN_CODES:
            return False
        raise


def job_arguments(settings: Settings, batch: dict[str, str]) -> dict[str, str]:
    return {
        "--run_date": batch["run_date"],
        "--batch_id": batch["batch_id"],
        "--raw_prefix": settings.s3_uri(batch["batch_prefix"]),
        "--curated_root": settings.s3_uri(settings.curated_prefix),
        "--control_root": settings.s3_uri(settings.control_prefix),
        "--contract_uri": settings.s3_uri(settings.contract_key),
        "--glue_database": settings.glue_database,
        "--max_reject_ratio": settings.max_reject_ratio,
        "--publish": "true",
    }


def process_batch(
    s3, glue, settings: Settings, batch: dict[str, str], contract: dict[str, Any], request_id: str
):
    obj = s3.get_object(Bucket=settings.bucket, Key=batch["key"])
    try:
        manifest = json.loads(obj["Body"].read())
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        return {"status": "invalid", "problems": [f"manifest is not valid JSON: {exc}"]}
    problems = validate_manifest(manifest, batch, contract)
    if problems:
        return {"status": "invalid", "problems": problems}
    missing = missing_files(s3, settings, batch, manifest)
    if missing:
        raise BatchIncomplete(f"{len(missing)} file(s) not ready: {missing[:5]}")
    if not acquire_lock(s3, settings, batch, request_id):
        return {"status": "duplicate", "lock": lock_key(settings, batch)}
    try:
        run = glue.start_job_run(JobName=settings.glue_job, Arguments=job_arguments(settings, batch))
    except ClientError as exc:
        s3.delete_object(Bucket=settings.bucket, Key=lock_key(settings, batch))  # let a retry try again
        code = exc.response.get("Error", {}).get("Code", "")
        if code in (
            "ConcurrentRunsExceededException",
            "ThrottlingException",
            "ResourceNumberLimitExceededException",
        ):
            raise RetryLater(f"Glue did not start the run ({code})") from exc
        raise
    s3.put_object(
        Bucket=settings.bucket,
        Key=lock_key(settings, batch),
        ContentType="application/json",
        Body=json.dumps(
            {
                "state": "STARTED",
                "job_run_id": run["JobRunId"],
                "glue_job": settings.glue_job,
                "request_id": request_id,
                "started_at": datetime.now(UTC).isoformat(),
                **batch,
            }
        ).encode(),
    )
    return {
        "status": "started",
        "job_run_id": run["JobRunId"],
        "ingestion_id": f"{batch['run_date']}_{batch['batch_id']}",
    }


def load_contract(s3, settings: Settings) -> dict[str, Any]:
    return json.loads(s3.get_object(Bucket=settings.bucket, Key=settings.contract_key)["Body"].read())


def handle(
    event: dict[str, Any], request_id: str, s3=None, glue=None, env: dict[str, str] | None = None
) -> dict:
    settings = Settings(env if env is not None else dict(os.environ))
    s3 = s3 or boto3.client("s3")
    glue = glue or boto3.client("glue")
    results, contract = [], None
    for record in event.get("Records", []):
        batch, reason = parse_record(record, settings)
        if batch is None:
            log_event(action="ignored", reason=reason, request_id=request_id)
            results.append({"status": "ignored", "reason": reason})
            continue
        contract = contract or load_contract(s3, settings)
        result = process_batch(s3, glue, settings, batch, contract, request_id)
        log_event(
            action=result["status"],
            request_id=request_id,
            manifest=batch["key"],
            **{k: v for k, v in result.items() if k != "status"},
        )
        results.append(result)
    return {"results": results}


def lambda_handler(event, context):  # AWS entry point
    return handle(event, getattr(context, "aws_request_id", "local"))
