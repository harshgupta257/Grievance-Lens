# Batch-trigger Lambda (`handler.py`)

Starts the Glue ETL when a complete batch has been uploaded. It only orchestrates. It never reads CSV
contents and never touches Redshift.

## Trigger

An S3 event notification on the data bucket:

| Field | Value |
|---|---|
| Id | `grievance-lens-batch-manifest` (any other notification configurations on the bucket are kept) |
| Events | `s3:ObjectCreated:*` |
| Filter | prefix `raw/powerbi/`, suffix `_manifest.json` |
| Target | `grievance-lens-batch-trigger` |

Why the manifest and not each CSV: a batch is 15 files. Triggering on every CSV would start 15 runs, each
seeing a partial batch. The upload scripts write `_manifest.json` **last**, so its arrival means "the batch
is complete". The function still checks this, because S3 notifications can arrive late, twice, or out of order.

## What one invocation does

1. **Filters** each record. It must be an `ObjectCreated` event, in this bucket, with a key matching
   `raw/powerbi/run_date=YYYY-MM-DD/batch=<id>/_manifest.json`. Anything else is ignored with a log line.
   This also rules out recursion: the function writes only under `control/`.
2. **Validates the manifest:**
   - manifest version 1;
   - run_date and batch match the key;
   - exactly the contract's 15 tables;
   - every path inside this batch;
   - `contract_hash` equal to the deployed contract's.
   
   If the manifest is invalid, the function logs `status: invalid` and returns. It is **not retried**,
   because retrying cannot fix it.
3. **Checks completeness:** it sends a HEAD request for every listed CSV and compares the size. If a file is
   missing or the wrong size, it raises `BatchIncomplete`, so Lambda retries.
4. **Locks:** it writes `control/batches/run_date=…/batch=…/lock.json` with `If-None-Match: *`. If the lock
   already exists (`PreconditionFailed`), the event is a duplicate and the function logs `status: duplicate`.
5. **Starts Glue** (`StartJobRun` with the run's arguments). If Glue refuses (for example
   `ConcurrentRunsExceededException`, because MaxConcurrentRuns is 1), the function deletes the lock and
   raises `RetryLater`.
6. **Records** the Glue `JobRunId` in the lock object and logs a structured line:
   `{"action": "started", "ingestion_id": …, "job_run_id": …, "request_id": …}`.

## Configuration

| Setting | Value |
|---|---|
| Runtime | python3.12, handler `handler.lambda_handler`, 128 MB, 60 s timeout |
| Async invoke | `MaximumRetryAttempts=2`, `MaximumEventAgeInSeconds=3600`. After that the event is dropped. Each attempt is in CloudWatch Logs, so a dropped event can be re-run by hand (below) |
| Logs | `/aws/lambda/grievance-lens-batch-trigger`, 30-day retention |
| Role | `grievance-lens-lambda-orchestrator` ([policy](../iam/policy_templates/lambda_orchestrator_policy.json)): `s3:GetObject` on `raw/powerbi/*` and the contract; `s3:ListBucket` limited to `raw/powerbi/` and `control/batches/` (without it a missing file returns 403 rather than 404); put/delete under `control/batches/`; `glue:StartJobRun` on the one job; its own log group |
| Package | `handler.py` only. The Lambda runtime's boto3 supports conditional writes; `requirements.txt` pins the version used in tests |

Environment variables (set by `deploy.py`; no secrets):

| Variable | Required | Default |
|---|---|---|
| `BUCKET` | yes | — |
| `GLUE_JOB_NAME` | yes | — |
| `CONTRACT_KEY` | no | `scripts/contract.json` |
| `CONTROL_PREFIX` | no | `control/` |
| `CURATED_PREFIX` | no | `curated/` |
| `GLUE_DATABASE` | no | `grievance_lens` |
| `MAX_REJECT_RATIO` | no | `0` |
| `LOG_LEVEL` | no | `INFO` |

## Deploy

`python aws/scripts/deploy.py --bucket <bucket>` prints the plan; add `--apply` to create or update the role,
function, permission and notification. Re-running it is safe: existing resources are updated, not duplicated.

## Safe test procedure

1. **Locally, with no AWS involved:** `pytest aws/tests/test_lambda_handler.py`. Fakes for S3 and Glue
   cover the scenarios below.
2. **On AWS, without uploading data:** invoke the function with a hand-written event for a key that does not
   match. It should log `ignored`, and nothing should start.
   ```bash
   aws lambda invoke --function-name grievance-lens-batch-trigger --cli-binary-format raw-in-base64-out \
     --payload '{"Records":[{"eventName":"ObjectCreated:Put","s3":{"bucket":{"name":"<bucket>"},"object":{"key":"raw/powerbi/readme.txt","size":1}}}]}' out.json
   ```
3. **End to end:** upload one batch with `upload_exports.sh` or `.ps1`. Then check:
   - the Lambda log shows `started`, with a job run id;
   - `control/batches/…/lock.json` exists;
   - `aws glue get-job-runs --job-name grievance-lens-etl` shows the run.

## Scenarios

| Scenario | Outcome |
|---|---|
| Manifest arrives before a CSV finishes | `BatchIncomplete`, retried; starts once all files are present (within 2 retries) |
| Same notification delivered twice / manifest re-uploaded | second invocation finds the lock → `duplicate`, no second run |
| Glue job already running | lock released, `RetryLater`, retried later |
| Manifest from an old contract | `invalid` (contract hash mismatch), not retried |
| CSV uploaded to `curated/` or any other prefix | not delivered (notification filter); ignored if it were |
| Glue run fails validation | Lambda has already succeeded; the batch is **not published**, and the reason is in `control/quality/…/report.json` and the Glue logs |

## Re-running a batch by hand

After an event was dropped, or after fixing a failed run:

```bash
GL_BUCKET=<bucket> aws/scripts/run_etl.sh <run_date> <batch_id> --wait
```

The ETL is idempotent per ingestion. To let the Lambda pick the batch up again instead, delete its
`lock.json` and copy the manifest onto itself:

```bash
aws s3 cp s3://<bucket>/raw/.../_manifest.json s3://<bucket>/raw/.../_manifest.json --metadata-directive REPLACE
```
