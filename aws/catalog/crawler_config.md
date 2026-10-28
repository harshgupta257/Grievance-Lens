# Glue Data Catalog

| Item | Value |
|---|---|
| Database | `grievance_lens` (curated Parquet, authoritative) |
| Tables | the 15 contract tables, same names as the CSV exports (`dim_month`, …, `demand_backlog_sensitivity`) |
| Location | `s3://<bucket>/curated/ingestion_id=<published id>/<table>/` |
| Format | Parquet, Snappy, `ParquetHiveSerDe` |
| Partitions | `ministry_monthly.period_year` (int), via **partition projection** (`projection.period_year.range = 2022,2035`, `storage.location.template = …/period_year=${period_year}/`) |
| Raw tables (benchmark only) | `grievance_lens_raw.*`, OpenCSVSerde, all STRING, created by the benchmark runner for one batch |
| Crawler audit database | `grievance_lens_crawler_audit` (never queried by analyses) |

## Decision: explicit table definitions, not a crawler

The tables are written by the ETL job itself (`glue:UpdateTable`), from `aws/contract/contract.json`, after
every data check has passed. The DDL in [curated_tables.sql](curated_tables.sql) produces the identical
definitions, for rebuilding them by hand. A crawler is not the source of truth, for four reasons:

1. **Types must not drift.** A crawler infers types from whatever files it sees. A future column that
   happens to be all-integer would become `bigint`, then flip to `double` when a decimal arrives. The
   contract fixes every type once (for example `reports_missing_in_span` is a string by definition).
2. **Atomic publish.** Each ingestion is written to its own folder, and the catalog is repointed only after
   validation. A crawler pointed at `curated/` would see every ingestion as a partition, or pick up a
   half-written or failed batch.
3. **New partitions need nothing.** Partition projection computes the year partitions at query time, so there
   is no crawler run and no `MSCK REPAIR TABLE` when a new year appears.
4. **Cost and moving parts.** Crawlers bill per DPU-hour. A catalog update is a few API calls the job already
   makes.

## Where a crawler still helps: schema-drift audit (optional)

`deploy.py --with-crawler` creates `grievance-lens-schema-audit`:

| Setting | Value | Why |
|---|---|---|
| Target database | `grievance_lens_crawler_audit` | can never overwrite the authoritative tables |
| SchemaChangePolicy | `UpdateBehavior: LOG`, `DeleteBehavior: LOG` | changes are reported, never applied |
| RecrawlPolicy | `CRAWL_EVERYTHING` | each audit sees the full published ingestion |
| Grouping | `CombineCompatibleSchemas` | one table per folder, not per file |
| Schedule | none (on demand) | no recurring cost |
| Role | `grievance-lens-glue-etl` (read curated, write only the audit database) | |

Run it after a publish:

```bash
GL_BUCKET=<bucket> python aws/catalog/crawler_audit.py
```

The script:
1. points the crawler at the published ingestion's 15 table folders (from `control/published/latest.json`);
2. runs the crawler and waits for it to finish;
3. compares every crawled column name and type with the contract;
4. exits 1 on any difference.

Differences should be impossible, because the ETL's read-back check compares the Parquet schema with the
contract before publishing. The audit is an independent second opinion.

## How schema changes are handled

1. A change starts in the exporter (`src/export/powerbi.py`, `TABLES`).
2. `python -m aws.contract.build_contract` regenerates the contract; `python -m aws.contract.render_ddl`
   regenerates the Athena and Redshift DDL. CI fails if either is out of date.
3. The contract hash changes. The Lambda rejects any batch built against a different contract
   (`contract_hash` in the manifest), so old and new layouts cannot mix.
4. Redeploy (`deploy.py --apply` uploads the new contract and job). The next published batch updates the
   catalog tables. For Redshift, apply the new `schema.sql` change (an `ALTER TABLE`, or recreate the table
   and refresh) before the next refresh.
5. A raw batch whose header differs from the contract fails with "schema drift" and is not published.
