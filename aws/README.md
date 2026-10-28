# AWS data pipeline for Grievance Lens

An S3 → Lambda → Glue → Athena → Redshift pipeline for the 15 star-schema CSVs that the project already
exports for Power BI (`src/export/powerbi.py` → `data/processed/powerbi/`). It extends the project. Nothing
in the existing analysis, website or CSV-based Power BI workflow changes or depends on AWS.

> **Status (2026-10-27): built and verified locally; not yet deployed to AWS.** Every component runs
> against the real exports on a laptop: Spark for Glue, DuckDB for Athena, PostgreSQL for Redshift, and
> fakes for S3, Lambda and Glue. No AWS run, timing or cost has been measured yet. Those figures are marked
> *pending* below and will be filled in only from real runs.

```
export CSVs ──build_batch + upload──▶ S3 raw/ (immutable batches) ── _manifest.json uploaded last
                                         │ S3 event (suffix _manifest.json)
                                         ▼
                                  Lambda: validate, check every file, lock (If-None-Match), StartJobRun
                                         ▼
                 Glue 5.0 PySpark: checksums · schema drift · typing · PK/FK · rejects → Snappy Parquet
                                         ▼ only if every check passed
                    Glue Data Catalog (15 tables, repointed atomically) ──▶ Athena: views + 5 analyses
                                         ▼ manual: refresh_warehouse.py
                    Redshift Serverless (optional): stage → guard → publish, reconciled ──▶ Power BI
```

Full diagram: [architecture/architecture.mmd](architecture/architecture.mmd). Solid arrows run
automatically; dashed arrows are manual or optional.

## Contents

| Folder | What | Docs |
|---|---|---|
| `contract/` | schema contract generated from the exporter: 15 tables, 179 typed columns, keys, partitioning | `python -m aws.contract.build_contract --check` |
| `s3/` | bucket layout, settings, upload procedure | [s3/setup.md](s3/setup.md) |
| `lambda/` | batch trigger | [lambda/README.md](lambda/README.md) |
| `glue/` | PySpark ETL and job definition | [glue/README.md](glue/README.md) |
| `catalog/` | Athena/Glue DDL, crawler decision, schema-drift audit | [catalog/crawler_config.md](catalog/crawler_config.md) |
| `athena/` | `00_views.sql`, 5 analyses, raw-CSV tables, benchmark queries | below |
| `benchmarks/` | CSV-vs-Parquet runner, report template, local verification results | [benchmarks/benchmark_report_template.md](benchmarks/benchmark_report_template.md) |
| `redshift/` | warehouse DDL, analytics, refresh logic | [redshift/README.md](redshift/README.md) |
| `iam/policy_templates/` | least-privilege role and bucket policies (placeholders, no account data) | below |
| `scripts/` | `deploy.py`, upload and run scripts (Bash + PowerShell), `refresh_warehouse.py`, teardown | [scripts/teardown.md](scripts/teardown.md) |
| `local/` | `verify_local.py`: the end-to-end local harness | below |
| `tests/` | 97 pytest tests (Spark tests need Java 17) | below |

## Prerequisites

- An AWS account in which you may create resources, region **ap-south-1**.
- AWS CLI v2, and Python 3.11+ with `pip install -r requirements.txt -r requirements-dev.txt -r aws/requirements-dev.txt`.
- For local Spark runs: Java 17 (`JAVA_HOME`). On Windows also set `GL_LOCAL_PARQUET_WRITER=pyarrow`
  (see [glue/README.md](glue/README.md#local-runs)).
- Fresh exports: `python -m src.export.powerbi` (writes the CSVs and `export_manifest.csv`).

### Credentials: temporary, never in the repo

Use **IAM Identity Center** (SSO) with a permission set built from
[iam/policy_templates/operator_policy.json](iam/policy_templates/operator_policy.json) for day-to-day work.
Deploying needs broader rights (create roles, Lambda, Glue job), so use an administrator permission set for
`deploy.py --apply` only.

```bash
aws configure sso --profile grievance-lens     # once: SSO start URL, region ap-south-1, account, permission set
aws sso login --profile grievance-lens         # each session; credentials expire on their own
export AWS_PROFILE=grievance-lens              # PowerShell: $env:AWS_PROFILE = "grievance-lens"
aws sts get-caller-identity                    # check which account you are in
```

- Do not create long-lived access keys for this project. Never use the root user; it must have MFA.
- Do not put keys, session tokens or account IDs in any file in this repository.
- Settings that vary by deployment (bucket name, workgroup) go in `aws/config/pipeline.env`, copied from
  [pipeline.env.example](config/pipeline.env.example). That file is gitignored and holds names, not secrets.

## Run it

```bash
export GL_BUCKET=<globally-unique-bucket-name> GL_REGION=ap-south-1

# 1. Deploy: prints the plan; nothing changes without --apply
python aws/scripts/deploy.py --bucket $GL_BUCKET
python aws/scripts/deploy.py --bucket $GL_BUCKET --apply --budget-email you@example.com
#    optional: --with-crawler (schema-drift audit), --with-redshift (warehouse; see its cost notes)

# 2. Upload a batch: checks checksums, uploads the CSVs, then the manifest (which triggers the ETL)
aws/scripts/upload_exports.sh                  # PowerShell: .\aws\scripts\upload_exports.ps1

# 3. Watch it
aws logs tail /aws/lambda/grievance-lens-batch-trigger --since 10m
aws glue get-job-runs --job-name grievance-lens-etl --max-items 1
aws s3 cp s3://$GL_BUCKET/control/published/latest.json -     # present once a batch is published

# 4. Query (Athena console, workgroup grievance-lens, database grievance_lens):
#    run aws/athena/00_views.sql once, then 01_… to 05_….

# 5. Optional
python aws/benchmarks/benchmark_runner.py      # CSV vs Parquet, 5 alternating repetitions
python aws/catalog/crawler_audit.py            # needs --with-crawler
python aws/scripts/refresh_warehouse.py --create-schema && python aws/scripts/refresh_warehouse.py   # needs --with-redshift
```

What a failed batch looks like: the Glue run ends `FAILED`, and nothing is published. Athena and Redshift
keep serving the previous batch. `control/quality/ingestion_id=<id>/report.json` lists every issue, and
`control/rejects/` holds sample rejected rows. Fix the source, then upload it as a **new** batch. Raw batches
are never edited in place.

## The Athena analyses

Ported from the project's existing analyses, using the same definitions:

| File | Question | Checks against |
|---|---|---|
| `01_month_over_month.sql` | national receipts, disposals and closing backlog, month on month | `overall_trend` |
| `02_ministry_backlog.sql` | which ministries' backlogs grew most (reports 29–45) | `backlog_growth` |
| `03_rolling_average.sql` | 3- and 6-month rolling averages and the Sen's slope of the national series | `overall_trend`, `trend_tests` |
| `04_first_last_appearance.sql` | first and last appearance of each ministry on each "top" list | `repeated_listings` |
| `05_demand_vs_backlog.sql` | demand vs backlog quadrants (66 classified ministries, 37 rising/rising) | `demand_backlog_quadrants` |

Each query returns a `matches_published` column, so a run shows whether SQL on the curated data reproduces
the project's published figures. The SQL avoids features that differ between engines, and the Redshift
versions in `redshift/analytics.sql` are rendered from the same text.

## Cost control

| Service | Cost driver | Guardrail in this project |
|---|---|---|
| S3 | storage (~1 MB per batch) and requests | lifecycle: Athena results 30 days, old versions 90 days, failed uploads 7 days |
| Lambda | invocations | one per batch; 128 MB, 60 s |
| Glue | DPU-hours, billed per second with a 1-minute minimum | smallest Spark size (G.1X × 2), FLEX, 15-minute timeout, no retries, 1 concurrent run, starts only for complete, valid batches |
| Glue crawler | DPU-hours | optional, on demand, no schedule |
| Athena | data scanned, with a 10 MB minimum per query | Parquet + partition projection; workgroup **1 GB per-query scan cutoff**; results expire |
| Redshift Serverless | RPU-hours while active, plus storage while idle | opt-in; 8 RPU base; **usage limit 1 RPU-hour/day → deactivate**; manual refresh only; delete when done |
| Budgets | free | optional `--budget-email`: $5/month, alerts at 80% and 100% actual and 100% forecast |

- **An AWS Budget is an alert, not a spending limit.** It sends emails; it does not stop resources.
- The Redshift usage limit does stop queries, but storage and snapshots keep billing until the namespace is
  deleted.
- Free-tier and trial credits vary by account and change over time; check your own account rather than
  relying on this page.
- Measured cost per run: **pending** (from Cost Explorer after the first AWS runs).
- To stop all charges: [scripts/teardown.md](scripts/teardown.md). It is a checklist, not a script, by design.

## Security

- **Bucket:**
  - private, all four Block Public Access settings on, ACLs disabled;
  - SSE-S3 encryption, and the bucket policy denies non-TLS requests;
  - versioning on, so an overwritten raw file can be recovered.
- **One role per component, each scoped to the prefixes and resources it needs** (see `iam/policy_templates/`):
  - Glue reads `raw/` and writes `curated/` and `control/`;
  - Lambda can read manifests, write locks and start one job;
  - Redshift reads `curated/` and the COPY manifests only.
  
  Templates contain `${PLACEHOLDERS}` only. `deploy.py` fills them at deploy time, from
  `sts:GetCallerIdentity` and your flags.
- **No secrets anywhere:**
  - no credentials in code, environment variables or the repo;
  - Redshift is reached through IAM (Data API, query editor v2), not a password;
  - the Lambda environment holds only names.
- **Untrusted input is validated before use:**
  - manifests are validated (schema, contract hash, paths confined to the batch);
  - every value interpolated into Redshift SQL is checked against a strict pattern;
  - the Lambda ignores keys outside its expected layout.
- **Data:** the exports are aggregated, published government statistics. They contain no personal data and
  no complaint text.

## What is verified, and what is pending

| Component | Verified locally (real exports) | On AWS |
|---|---|---|
| Contract + DDL generation | generated, `--check` in CI | n/a |
| Batch builder | checksums/rows vs export manifest, immutability (tests) | pending |
| Lambda | 20 tests with fake S3/Glue (incomplete, duplicate, lock race, invalid, concurrency) | pending |
| Glue ETL | local Spark 3.5.4: **VALIDATED, 9,624 rows, 0 rejected, 0 critical issues**; 0 value mismatches raw vs curated; corruption tests (bad types, malformed lines, drift, orphan FKs, checksum, duplicates) | pending |
| Parquet size | 740,510 B CSV → 196,844 B Parquet (**27%**), local write | pending (S3 object sizes) |
| Athena SQL | all files parse as Trino; 5 analyses reproduce the published values (57/90/57/181/90 rows, 0 mismatches) via DuckDB | pending |
| CSV vs Parquet benchmark | the 4 query pairs return identical results | **pending**: no scan or runtime figures yet |
| Redshift SQL | schema, publish transaction, guard, reconciliation and the 5 analyses on PostgreSQL; identical to the Athena results | pending (COPY, Data API) |
| deploy.py | plan mode prints 18 steps with no AWS calls; builders unit-tested | pending |
| End-to-end automation (upload → published) | not claimed | pending |
| Upload → Redshift | **not automatic, by design**: the refresh is a manual step | n/a |

Reproduce the local results:

```bash
python aws/local/verify_local.py --curated <curated dir> --raw <raw batch dir> --quality-report <report.json> \
  --pg-url postgresql+psycopg2://postgres@127.0.0.1:5432/postgres
pytest aws/tests -q
```

Output: [benchmarks/local_verification.md](benchmarks/local_verification.md).

## Measurable outcomes

| Measure | Value | Source |
|---|---|---|
| Rows validated per batch | 9,624 across 15 tables | local Spark run |
| Rows rejected / critical issues | 0 / 0 | local Spark run |
| Storage, CSV → Parquet | 740,510 B → 196,844 B (27%) | local run |
| Analyses ported to SQL and matching published values | 5 of 5, in both Athena and Redshift dialects | local DuckDB + PostgreSQL |
| Athena bytes scanned, CSV vs Parquet (4 query pairs) | *pending* | `benchmark_runner.py` on AWS |
| Athena runtime, CSV vs Parquet (median of 5) | *pending* | `benchmark_runner.py` on AWS |
| Glue run duration on AWS | *pending* | Glue job run |
| Upload-to-published latency | *pending* | Lambda log + `latest.json` timestamp |
| Cost per batch | *pending* | Cost Explorer |

## Resume bullet (no AWS numbers until measured)

> Extended Grievance Lens with an AWS data pipeline. CSV exports are uploaded to S3 as immutable batches; a
> manifest-triggered, idempotent Lambda starts a Glue PySpark ETL that validates schema, types and keys
> before writing partitioned Parquet to a Glue Catalog queried by Athena. An optional Redshift Serverless
> warehouse is refreshed in two guarded, reconciled transactions. 9,624 rows validated with zero rejects,
> and 5 analyses ported to SQL that reproduce the published figures, in a locally verified build with 97
> tests and least-privilege IAM.

After deploying and running the benchmark, add only the measured values, for example "Athena scans X% fewer
bytes on Parquet than on CSV", taken from `aws/benchmarks/results/`.

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| Upload finished, no Glue run | notification missing or filtered out; Lambda error | `get-bucket-notification-configuration`; Lambda logs. Re-run by hand with `run_etl.sh` |
| Lambda logs `incomplete` repeatedly | a CSV failed to upload or has a different size | re-run the upload. The batch prefix is the same, so `sync` re-sends only what differs |
| Lambda logs `invalid … contract_hash` | the batch was built with an older contract | rebuild the batch with the current code (`build_batch.py`) |
| Lambda logs `duplicate` | a repeated notification (normal) or a re-uploaded manifest | nothing; to force a re-run see [lambda/README.md](lambda/README.md#re-running-a-batch-by-hand) |
| Glue `FAILED`, "schema drift" | the export columns changed | regenerate the contract and DDL, redeploy (catalog doc, "How schema changes are handled") |
| Glue `FAILED`, "rejected rows" | values that do not fit the contract | see `control/rejects/`; fix the export |
| Glue run waits in `WAITING` | FLEX capacity | wait, or `--glue-execution-class STANDARD` |
| Athena: table not found, or the query is cancelled | no batch published yet; or the 1 GB workgroup scan cutoff | check `latest.json` exists; select database `grievance_lens`; a query over 1 GB here means a wrong table or a missing filter |
| Refresh says "lock exists" | another refresh is running, or one crashed | wait; then `refresh_warehouse.py --force-unlock` |
| Refresh fails with "division by zero" | the count guard: staged rows ≠ published rows | `gl.*` is unchanged; inspect `stg.*` and the COPY errors (`sys_load_error_detail`) |
| Spark on Windows: `UnsatisfiedLinkError` / winutils | Hadoop native IO on Windows | set `GL_LOCAL_PARQUET_WRITER=pyarrow` (local only) |

Design decisions: `DESIGN.md`, D-054 to D-062.
