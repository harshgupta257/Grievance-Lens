# Redshift Serverless warehouse (optional)

Athena already answers every analysis directly from S3. Redshift is here for one purpose: a warehouse copy of
the star schema that Power BI can query at low latency through its native Redshift connector. It is **opt-in**
(`deploy.py --with-redshift`) because, unlike Athena, it can bill while you are not looking.

| File | Purpose |
|---|---|
| [schema.sql](schema.sql) | schemas `gl` (what users query) and `stg` (load area), 15 tables each, `gl.load_audit`, `stg.batch_marker`. Generated from the contract by `python -m aws.contract.render_ddl` |
| [views.sql](views.sql) | `gl.v_flow_panel`, the same view as `aws/athena/00_views.sql` |
| [analytics.sql](analytics.sql) | the 5 Athena analyses, rendered for Redshift (blocks marked `-- name: <query>`) |
| [warehouse.py](warehouse.py) | builds the refresh transactions (pure functions, unit-tested) |
| [../scripts/refresh_warehouse.py](../scripts/refresh_warehouse.py) | runs a refresh through the Redshift Data API |

## Table design

- **Every identifier is quoted.** One export column is named `group`, a reserved word in Redshift (and Postgres).
- **Types** come from the contract: int → `INTEGER`, double → `DOUBLE PRECISION`, date → `DATE`,
  boolean → `BOOLEAN`, string → `VARCHAR(n)`. `n` is a power of two, at least twice the longest observed
  value (16 to 2048), so normal growth fits but columns are not 65535 wide.
- **Keys.** Primary and foreign keys are declared. Redshift does not enforce them, but the planner uses them.
  Uniqueness and referential integrity are enforced earlier by the Glue job.
- **Distribution and sort keys:**

| Tables | Distribution | Sort key | Why |
|---|---|---|---|
| `dim_*` and the small analysis tables (all ≤ 181 rows) | `DISTSTYLE ALL` | primary key | a full copy on every node, so joins never move data |
| `ministry_monthly`, `list_appearances`, `grai_ranks` | `DISTKEY("ministry_id")` | `("report_number", …)` | the three facts join on `ministry_id`, so co-locating them keeps those joins local; the analyses filter and order by report number |

At ~9,600 rows any layout performs well. These choices are what the model calls for as it grows, not a
measured speed-up.

## Load and refresh (manual, by design)

A refresh is **never** started by an upload. Run it after the ETL has published a batch:

```bash
export GL_BUCKET=<bucket> GL_REDSHIFT_WORKGROUP=grievance-lens-wg \
       GL_REDSHIFT_ROLE_ARN=arn:aws:iam::<account-id>:role/grievance-lens-redshift-copy
python aws/scripts/refresh_warehouse.py --create-schema   # first time only: schema.sql + views.sql
python aws/scripts/refresh_warehouse.py --dry-run         # print the SQL, touch nothing
python aws/scripts/refresh_warehouse.py                   # refresh to control/published/latest.json
```

The refresh:
1. Takes an S3 lock (`control/warehouse/refresh.lock`, conditional write), so two refreshes cannot overlap.
   `--force-unlock` removes a lock left by a crash.
2. Reads `latest.json`, or the pointer of `--ingestion-id`. Every value placed into SQL is validated first.
3. **Transaction 1, stage** (34 statements): for each table, `DELETE FROM stg.t` and then
   `COPY stg.t FROM '<manifest>' IAM_ROLE '<role>' FORMAT AS PARQUET MANIFEST`. A guard statement then
   divides by zero unless every staged count equals the published count, so a short load rolls back the
   whole transaction. Finally it records the ingestion in `stg.batch_marker` and writes a `STAGED` audit row.
4. **Transaction 2, publish** (32 statements): a guard checks that `stg.batch_marker` holds this ingestion
   and that the counts still match. Then, for each table, `DELETE FROM gl.t; INSERT INTO gl.t SELECT * FROM stg.t`
   in contract load order, and a `PUBLISHED` audit row.
5. **Reconciliation:** a separate query compares `COUNT(*)` of every `gl` table with the published
   counts. The result goes to `control/warehouse/refreshes/<ingestion_id>.json`.

Two transactions are needed because one Data API `BatchExecuteStatement` call is one transaction and holds
at most 40 statements. If either transaction fails, `gl.*` is unchanged. Re-running for the same ingestion
produces identical tables.

`COPY` reads the exact file list from the Glue job's manifests (`control/redshift_manifests/`), never
a prefix listing. Leftover or partial files cannot be loaded.

## Querying

Use the Redshift query editor v2 (signed in through IAM, so no database password is needed), or the Data API:

```bash
aws redshift-data execute-statement --workgroup-name grievance-lens-wg --database dev \
  --sql 'SELECT * FROM gl.load_audit ORDER BY recorded_at DESC LIMIT 5'
```

Run any block of [analytics.sql](analytics.sql). Its results were verified locally to be identical to the
Athena versions (see below).

## Athena or Redshift?

| | Athena | Redshift Serverless |
|---|---|---|
| Billing | per TB scanned (MB here); nothing while idle | per RPU-hour while queries run (60-second minimum per period of activity) plus managed storage |
| Data | queries the curated Parquet in S3 directly | its own copy, refreshed explicitly |
| Freshness | the moment a batch is published | after `refresh_warehouse.py` |
| Latency | seconds (query start-up) | sub-second once warm |
| Power BI | Athena connector (ODBC driver, Import recommended) | native connector, suitable for DirectQuery |
| Best for | ad-hoc analysis, low and irregular use | dashboards with many interactive users |

**For this project's size and use, Athena is the right default.** Redshift is the documented upgrade path,
not a requirement.

## Verified so far

- **Locally, on PostgreSQL** (`aws/local/verify_local.py`):
  - `schema.sql` applies with every key enforced;
  - the real publish transaction loads all 15 tables, and its guard refuses a different ingestion;
  - row counts reconcile;
  - all 5 analyses return results identical to the Athena SQL.
  
  Postgres has no `COPY … FORMAT AS PARQUET` or `DISTSTYLE`, so the harness loads `stg` itself and removes
  the distribution clauses.
- **On AWS: pending.** COPY from S3, the Data API run, and the timings have not been executed yet.

## Cost guardrails and cleanup

- `deploy.py` creates the workgroup with 8 RPU base capacity (the minimum), no public access, and a usage
  limit of 1 RPU-hour per day (`--redshift-max-rpu-hours-per-day`) with `breachAction=deactivate`.
- Redshift Serverless charges only while queries run, but **managed storage, snapshots and recovery points
  are billed while it sits idle.** Delete the workgroup and namespace when you finish
  ([teardown.md](../scripts/teardown.md)). Taking a final snapshot is optional, and the snapshot is also billed.
- Power BI in DirectQuery mode queries the warehouse on every visual interaction, so every interaction
  consumes RPU time. Use Import, with a scheduled refresh, unless live data is needed.
