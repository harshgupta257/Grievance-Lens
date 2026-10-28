# S3 data lake

One private bucket per deployment, in `ap-south-1` (Mumbai). Choose a globally unique name, for example
`grievance-lens-<initials>-<random suffix>`. This page uses `<bucket>` throughout. `aws/scripts/deploy.py`
applies every setting below; the AWS CLI equivalents are listed so each one can be checked by hand.

## Layout

| Prefix | Written by | Read by | Contents |
|---|---|---|---|
| `raw/powerbi/run_date=YYYY-MM-DD/batch=<id>/<table>/<table>.csv` | operator (upload script) | Lambda (HEAD), Glue, Athena raw tables | the 15 export CSVs, byte-identical copies, never modified |
| `raw/powerbi/run_date=.../batch=<id>/_manifest.json` | operator, **last** | Lambda, Glue | batch-completion marker: table list, bytes, SHA-256, rows, contract hash |
| `curated/ingestion_id=<run_date>_<batch>/<table>/` | Glue | Athena (via catalog), Redshift COPY | Snappy Parquet, one file per table; `ministry_monthly/period_year=YYYY/` |
| `control/batches/run_date=.../batch=.../lock.json` | Lambda | Lambda | idempotency lock and Glue run id |
| `control/quality/ingestion_id=<id>/report.json` | Glue | people, benchmark | row counts, rejects, null counts, key and FK checks, timings, status |
| `control/rejects/ingestion_id=<id>/<table>.json` | Glue | people | up to 20 rejected rows per table, with reasons |
| `control/redshift_manifests/ingestion_id=<id>/<table>.manifest` | Glue | Redshift COPY | exact Parquet file list with `content_length` |
| `control/published/latest.json` and `history/<id>.json` | Glue (last step of a successful run) | refresh, benchmark, crawler audit | the published ingestion pointer |
| `control/warehouse/` | refresh script | refresh script | refresh lock and reconciliation results |
| `athena-results/` | Athena | operator | query results; deleted after 30 days |
| `scripts/` | deploy.py | Glue, Lambda | `etl_job.py`, `contract.json`, Glue temp dir |

Why one folder per table under each batch: an Athena or Glue table's location must contain only that table's
files. Putting all 15 CSVs side by side would make a raw table read all of them.

## Settings

| Setting | Value | CLI check |
|---|---|---|
| Block Public Access | all four on | `aws s3api get-public-access-block --bucket <bucket>` |
| Object ownership | BucketOwnerEnforced (ACLs disabled) | `aws s3api get-bucket-ownership-controls --bucket <bucket>` |
| Default encryption | SSE-S3 (AES-256), bucket key on | `aws s3api get-bucket-encryption --bucket <bucket>` |
| Bucket policy | deny any request with `aws:SecureTransport = false` | `aws s3api get-bucket-policy --bucket <bucket>` |
| Versioning | enabled: an accidental overwrite of a raw CSV keeps the previous version | `aws s3api get-bucket-versioning --bucket <bucket>` |
| Lifecycle | `athena-results/` expire after 30 days; noncurrent versions after 90 days; incomplete multipart uploads after 7 days. Raw and curated data are never expired automatically | `aws s3api get-bucket-lifecycle-configuration --bucket <bucket>` |
| Event notification | `s3:ObjectCreated:*`, prefix `raw/powerbi/`, suffix `_manifest.json` -> the Lambda | `aws s3api get-bucket-notification-configuration --bucket <bucket>` |

SSE-S3 rather than SSE-KMS: it costs nothing extra, needs no key policy, and every role in this project
already has narrowly scoped S3 access. Use SSE-KMS with a customer-managed key if your organisation requires
key-level audit or separation of duties; add `kms:Decrypt`/`kms:GenerateDataKey` to the three role
policies and the Redshift role.

## Uploading a batch

```bash
export GL_BUCKET=<bucket> AWS_PROFILE=grievance-lens
aws/scripts/upload_exports.sh                    # today's run_date, a new UTC batch id
```
```powershell
$env:GL_BUCKET = "<bucket>"; $env:AWS_PROFILE = "grievance-lens"
.\aws\scripts\upload_exports.ps1
```

What happens:
1. `build_batch.py` checks every CSV against `data/processed/powerbi/export_manifest.csv` (SHA-256 and row
   count). It stages copies under `aws/.local/staging/run_date=.../batch=.../<table>/`, and writes the manifest.
2. The script refuses to continue if that batch already has a manifest in S3.
3. `aws s3 sync <staged batch> s3://<bucket>/raw/powerbi/run_date=.../batch=.../ --exclude _manifest.json`
   uploads the CSVs. **`sync` mirrors files; it is not version history.** That is why every upload goes to a
   new `batch=` prefix, and why `--delete` is never used. Versioning is the safety net for mistakes.
4. `aws s3 cp _manifest.json` is uploaded last and triggers the pipeline.

To upload a different export folder, pass `--source <dir>` (`-Source` in PowerShell). The folder must
contain the 15 CSVs and its `export_manifest.csv`.

## Who can touch what

See `aws/iam/policy_templates/`: Glue reads `raw/` and writes only `curated/` and `control/`; Lambda reads
`raw/powerbi/` and writes only `control/batches/`; the Redshift role reads only `curated/` and
`control/redshift_manifests/`; operators upload to `raw/powerbi/` and read results. Nothing is public, and
query results are never shared outside the account.
