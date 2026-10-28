"""Lambda orchestration: event filtering, manifest validation, incomplete batches, duplicate delivery,
Glue concurrency limits. S3 and Glue are in-memory fakes that mimic the real error codes."""

from __future__ import annotations

import io
import json

import handler
import pytest
from botocore.exceptions import ClientError

BUCKET = "test-bucket"
ENV = {"BUCKET": BUCKET, "GLUE_JOB_NAME": "grievance-lens-etl"}
PREFIX = "raw/powerbi/run_date=2026-10-27/batch=B1/"


class FakeS3:
    def __init__(self):
        self.objects: dict[str, bytes] = {}
        self.puts: list[str] = []

    def _err(self, code: str, op: str):
        return ClientError({"Error": {"Code": code, "Message": code}}, op)

    def put_object(self, Bucket, Key, Body, ContentType=None, IfNoneMatch=None):  # noqa: N803 - boto3 names
        if IfNoneMatch == "*" and Key in self.objects:
            raise self._err("PreconditionFailed", "PutObject")
        self.objects[Key] = Body if isinstance(Body, bytes) else Body.encode()
        self.puts.append(Key)
        return {}

    def get_object(self, Bucket, Key):  # noqa: N803
        if Key not in self.objects:
            raise self._err("NoSuchKey", "GetObject")
        return {"Body": io.BytesIO(self.objects[Key])}

    def head_object(self, Bucket, Key):  # noqa: N803
        if Key not in self.objects:
            raise self._err("404", "HeadObject")
        return {"ContentLength": len(self.objects[Key])}

    def delete_object(self, Bucket, Key):  # noqa: N803
        self.objects.pop(Key, None)
        return {}


class FakeGlue:
    def __init__(self, errors: list[str] | None = None):
        self.errors = list(errors or [])
        self.runs: list[dict] = []

    def start_job_run(self, JobName, Arguments):  # noqa: N803
        if self.errors:
            code = self.errors.pop(0)
            raise ClientError({"Error": {"Code": code, "Message": code}}, "StartJobRun")
        self.runs.append({"JobName": JobName, "Arguments": Arguments})
        return {"JobRunId": f"jr_{len(self.runs)}"}


@pytest.fixture
def s3(contract):
    s = FakeS3()
    s.objects["scripts/contract.json"] = json.dumps(contract).encode()
    return s


def upload_batch(
    s3: FakeS3, contract: dict, *, skip: str | None = None, truncate: str | None = None, **overrides
) -> str:
    files = []
    for t in contract["tables"]:
        body = f"header\nrow-of-{t['name']}\n".encode()
        files.append(
            {
                "table": t["name"],
                "path": f"{t['name']}/{t['name']}.csv",
                "bytes": len(body),
                "sha256": "x",
                "rows": 1,
            }
        )
        if t["name"] == skip:
            continue
        s3.objects[PREFIX + files[-1]["path"]] = body[:-3] if t["name"] == truncate else body
    manifest = {
        "manifest_version": 1,
        "run_date": "2026-10-27",
        "batch_id": "B1",
        "contract_hash": contract["contract_hash"],
        "files": files,
        **overrides,
    }
    s3.objects[PREFIX + "_manifest.json"] = json.dumps(manifest).encode()
    return PREFIX + "_manifest.json"


def event(key: str, bucket: str = BUCKET, name: str = "ObjectCreated:Put", size: int = 500) -> dict:
    return {
        "Records": [
            {
                "eventSource": "aws:s3",
                "eventName": name,
                "s3": {"bucket": {"name": bucket}, "object": {"key": key, "size": size}},
            }
        ]
    }


def run(evt, s3, glue):
    return handler.handle(evt, "req-1", s3=s3, glue=glue, env=ENV)["results"]


def test_complete_batch_starts_one_glue_run(s3, contract):
    glue = FakeGlue()
    key = upload_batch(s3, contract)
    [res] = run(event(key), s3, glue)
    assert res == {"status": "started", "job_run_id": "jr_1", "ingestion_id": "2026-10-27_B1"}
    args = glue.runs[0]["Arguments"]
    assert args["--raw_prefix"] == f"s3://{BUCKET}/{PREFIX}"
    assert args["--run_date"] == "2026-10-27" and args["--batch_id"] == "B1" and args["--publish"] == "true"
    lock = json.loads(s3.objects["control/batches/run_date=2026-10-27/batch=B1/lock.json"])
    assert lock["state"] == "STARTED" and lock["job_run_id"] == "jr_1"


def test_duplicate_notification_is_ignored(s3, contract):
    glue = FakeGlue()
    key = upload_batch(s3, contract)
    first = run(event(key), s3, glue)
    second = run(event(key), s3, glue)
    assert first[0]["status"] == "started" and second[0]["status"] == "duplicate"
    assert len(glue.runs) == 1


def test_duplicate_records_in_one_event(s3, contract):
    glue = FakeGlue()
    key = upload_batch(s3, contract)
    evt = event(key)
    evt["Records"] *= 2
    statuses = [r["status"] for r in run(evt, s3, glue)]
    assert statuses == ["started", "duplicate"] and len(glue.runs) == 1


@pytest.mark.parametrize(
    "key",
    [
        PREFIX + "dim_month/dim_month.csv",  # a CSV, not the manifest
        "curated/ingestion_id=x/dim_month/part-0.parquet",  # ETL output: must never re-trigger
        "control/batches/run_date=2026-10-27/batch=B1/lock.json",  # the function's own write
        "raw/powerbi/_manifest.json",  # wrong layout
        "raw/powerbi/run_date=2026-10-27/batch=../x/_manifest.json",
    ],
)
def test_unrelated_objects_are_ignored(key, s3):
    glue = FakeGlue()
    [res] = run(event(key), s3, glue)
    assert res["status"] == "ignored" and not glue.runs


def test_other_bucket_and_other_events_are_ignored(s3, contract):
    glue = FakeGlue()
    key = upload_batch(s3, contract)
    assert run(event(key, bucket="someone-elses-bucket"), s3, glue)[0]["status"] == "ignored"
    assert run(event(key, name="ObjectRemoved:Delete"), s3, glue)[0]["status"] == "ignored"
    assert not glue.runs


def test_url_encoded_key_is_decoded(s3, contract):
    glue = FakeGlue()
    key = upload_batch(s3, contract)
    encoded = key.replace("=", "%3D")
    assert run(event(encoded), s3, glue)[0]["status"] == "started"


def test_missing_file_raises_for_retry_and_takes_no_lock(s3, contract):
    glue = FakeGlue()
    key = upload_batch(s3, contract, skip="grai_ranks")
    with pytest.raises(handler.BatchIncomplete, match="grai_ranks"):
        run(event(key), s3, glue)
    assert not glue.runs and not any(k.endswith("lock.json") for k in s3.objects)
    # the file arrives (out-of-order delivery); Lambda's retry of the same event now starts the run
    s3.objects[PREFIX + "grai_ranks/grai_ranks.csv"] = b"header\nrow-of-grai_ranks\n"
    assert run(event(key), s3, glue)[0]["status"] == "started"


def test_partially_uploaded_file_is_incomplete(s3, contract):
    key = upload_batch(s3, contract, truncate="dim_month")
    with pytest.raises(handler.BatchIncomplete, match="dim_month"):
        run(event(key), s3, FakeGlue())


@pytest.mark.parametrize(
    "overrides,problem",
    [
        ({"contract_hash": "0000000000000000"}, "contract_hash"),
        ({"batch_id": "OTHER"}, "do not match its S3 key"),
        ({"manifest_version": 2}, "manifest_version"),
    ],
)
def test_invalid_manifest_is_rejected_without_retry(s3, contract, overrides, problem):
    glue = FakeGlue()
    key = upload_batch(s3, contract, **overrides)
    [res] = run(event(key), s3, glue)
    assert res["status"] == "invalid" and any(problem in p for p in res["problems"]) and not glue.runs


def test_manifest_with_wrong_tables_or_paths_is_rejected(s3, contract):
    key = upload_batch(s3, contract)
    m = json.loads(s3.objects[key])
    m["files"] = m["files"][1:] + [dict(m["files"][0], path="../../curated/evil.csv")]
    s3.objects[key] = json.dumps(m).encode()
    [res] = run(event(key), s3, FakeGlue())
    assert res["status"] == "invalid" and any("not a CSV inside the batch" in p for p in res["problems"])
    m["files"] = m["files"][1:]
    s3.objects[key] = json.dumps(m).encode()
    [res] = run(event(key), s3, FakeGlue())
    assert any("tables differ from the contract" in p for p in res["problems"])


def test_unparseable_manifest(s3, contract):
    key = upload_batch(s3, contract)
    s3.objects[key] = b"{not json"
    assert run(event(key), s3, FakeGlue())[0]["status"] == "invalid"


def test_glue_concurrency_limit_releases_lock_and_retries(s3, contract):
    glue = FakeGlue(errors=["ConcurrentRunsExceededException"])
    key = upload_batch(s3, contract)
    with pytest.raises(handler.RetryLater):
        run(event(key), s3, glue)
    assert not any(k.endswith("lock.json") for k in s3.objects)  # released, so the retry is not a "duplicate"
    assert run(event(key), s3, glue)[0]["status"] == "started" and len(glue.runs) == 1


def test_glue_permission_error_is_not_swallowed(s3, contract):
    glue = FakeGlue(errors=["AccessDeniedException"])
    key = upload_batch(s3, contract)
    with pytest.raises(ClientError):
        run(event(key), s3, glue)
    assert not any(k.endswith("lock.json") for k in s3.objects)


def test_missing_environment_is_a_clear_error():
    with pytest.raises(RuntimeError, match="BUCKET"):
        handler.handle({"Records": []}, "r", s3=FakeS3(), glue=FakeGlue(), env={})
