# Glue PySpark ETL (`etl_job.py`)

Turns one raw batch of the 15 Power BI CSV exports into validated, typed Parquet and publishes it. The same
file runs on AWS Glue 5.0 and in local Spark (tests and `aws/local/verify_local.py`). It deliberately does
not import `awsglue`: bookmarks are not used, so plain PySpark keeps the job testable.

## What a run does

| Step | Detail | On failure |
|---|---|---|
| 1. Contract and manifest | Loads `contract.json` and the batch's `_manifest.json`; the manifest must be for this run_date/batch and list exactly the 15 contract tables | batch rejected before reading data |
| 2. Raw file integrity | Byte size and SHA-256 of each CSV against the manifest | critical: partial or altered upload |
| 3. Schema drift | Header compared with the contract: missing, unexpected or reordered columns | critical |
| 4. Read | Explicit all-string schema, header skipped, blanks = NULL, quoted commas kept, malformed lines captured in `_corrupt_record` (never dropped) | malformed lines become rejected rows |
| 5. Typing | Per contract type. Typed columns are trimmed; strings are kept exactly. A non-blank value that fails its cast (e.g. `1.5` for an int, `NaN`, `2024-13-01`, an int32 overflow) rejects the row with a reason. Blanks stay NULL, never zero | rejected rows counted, sampled to `control/rejects/` |
| 6. Validation | Rows read = manifest rows; rejected share <= `--max_reject_ratio` (default 0); primary keys non-null and unique; all 15 foreign keys resolve | critical |
| 7. Write | Snappy Parquet to `curated/ingestion_id=<id>/<table>/`, one file per table; `ministry_monthly` partitioned by `period_year = year(period_start)`. No `_SUCCESS` files | — |
| 8. Read-back | Row count and Parquet schema re-read and compared with the contract | critical |
| 9. Control outputs | `control/quality/.../report.json`, Redshift COPY manifests | — |
| 10. Publish | Only with zero critical issues: `glue:UpdateTable` (or `CreateTable`) for every table, pointing at this ingestion; then `control/published/latest.json` | not published; the job fails with the first issue in the error |

**Identifiers** (`ministry_id`, `report_number`, `test_id`) are integers in the source with no leading zeros,
so integer typing preserves them exactly. A column whose observed values would mislead is overridden in the
contract, with the reason recorded: `dim_list.reports_missing_in_span` is a string because the data
dictionary defines it as a semicolon-separated list.

**Idempotency.** `ingestion_id = <run_date>_<batch_id>`. Re-running a batch overwrites the same curated
folder with the same output and republishes the same location, so it never duplicates records. Raw files are
read-only to the job.

**Partitioning.** Only `ministry_monthly` (1,800 rows) is partitioned. It is the one multi-year
ministry-level fact queried by period, giving 3 partitions (2024, 2025, 2026). Every other table is tiny
and read whole, so partitioning would only create small files. Athena uses partition projection (no crawler
or `MSCK REPAIR` needed).

## Parameters

See [job_config.yaml](job_config.yaml). Required per run: `--run_date`, `--batch_id`, `--raw_prefix`,
`--curated_root`, `--control_root`, `--contract_uri`. Optional: `--glue_database` (grievance_lens),
`--publish` (true), `--max_reject_ratio` (0), `--aws_region`.

## Worker capacity

G.1X x 2 is the smallest configuration Glue allows for a Spark job, and far more than ~750 KB of CSV
needs. The run is dominated by Spark start-up, not data. FLEX execution is the default to reduce cost; it
can start later than STANDARD. With a 15-minute timeout and no retries, a stuck run cannot bill for long.

## Create and run

`python aws/scripts/deploy.py --bucket <bucket> --apply` creates the job and uploads the script and contract.
Runs normally start from the Lambda when a batch manifest arrives; to run one by hand (recovery):

```bash
GL_BUCKET=<bucket> aws/scripts/run_etl.sh 2026-10-27 20261027T101500Z --wait
```
```powershell
$env:GL_BUCKET = "<bucket>"; .\aws\scripts\run_etl.ps1 -RunDate 2026-10-27 -BatchId 20261027T101500Z -Wait
```

Logs are in CloudWatch under `/aws-glue/jobs/output`, `/aws-glue/jobs/error` and the continuous log group.
The job logs one line per table (`validated <table>: read=… valid=… rejected=…`) and a final status line.

## Example output (from a local run on the real exports)

```
curated/ingestion_id=2026-10-27_20261027T101500Z/
  dim_month/part-00000.snappy.parquet
  ...
  ministry_monthly/period_year=2024/part-....snappy.parquet
  ministry_monthly/period_year=2025/part-....snappy.parquet
  ministry_monthly/period_year=2026/part-....snappy.parquet
control/quality/ingestion_id=2026-10-27_20261027T101500Z/report.json   status VALIDATED, 9,624 rows, 0 rejected
control/redshift_manifests/ingestion_id=2026-10-27_20261027T101500Z/<table>.manifest  (x15)
```

## Local runs

```powershell
$env:JAVA_HOME = "C:\path\to\jdk-17"
$env:GL_LOCAL_PARQUET_WRITER = "pyarrow"   # Windows only (see below)
python aws/scripts/build_batch.py --out aws/.local/raw/powerbi --run-date 2026-10-27 --batch-id LOCAL1
python aws/glue/etl_job.py --run_date 2026-10-27 --batch_id LOCAL1 `
  --raw_prefix aws/.local/raw/powerbi/run_date=2026-10-27/batch=LOCAL1/ `
  --curated_root aws/.local/curated/ --control_root aws/.local/control/ `
  --contract_uri aws/contract/contract.json --publish false
```

On Windows, Hadoop needs `winutils.exe` to write local files. Rather than install unofficial native
binaries, the job has an opt-in local writer (`GL_LOCAL_PARQUET_WRITER=pyarrow`):
- it changes only how the Parquet files are written and read back;
- all reading, typing and validation still runs in Spark;
- it is refused for `s3://` paths;
- the quality report records which writer was used.

On Linux (and in CI), and always on Glue, the Spark writer is used.
