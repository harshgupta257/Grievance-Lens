# Local verification of the AWS pipeline (no AWS account involved)

Generated 2026-10-26T19:23:19+00:00 by `python aws/local/verify_local.py` for ingestion `2026-10-27_20261027T101500Z`. These are **local** results: Spark local mode, DuckDB in place of Athena, PostgreSQL in place of Redshift. They show the code and SQL are correct on the real exports; they are not AWS measurements (see [benchmark_report_template.md](benchmark_report_template.md)).

## ETL (aws/glue/etl_job.py, local Spark)

- Status **VALIDATED**; 9,624 rows processed, 0 rejected; 31.98 s; Parquet writer: `pyarrow`; Spark 3.5.4.
- Critical issues: 0.

## Raw CSV vs curated Parquet

| Table | Raw rows | Curated rows | Value mismatches | CSV bytes | Parquet bytes |
|---|---:|---:|---:|---:|---:|
| demand_backlog_sensitivity | 27 | 27 | 0 | 1,005 | 2,354 |
| dim_list | 6 | 6 | 0 | 1,403 | 3,840 |
| dim_ministry | 92 | 92 | 0 | 6,892 | 5,091 |
| dim_month | 57 | 57 | 0 | 2,839 | 3,339 |
| scorecard_sensitivity | 10 | 10 | 0 | 542 | 2,460 |
| backlog_growth | 90 | 90 | 0 | 15,762 | 11,363 |
| demand_backlog_quadrants | 90 | 90 | 0 | 16,202 | 9,585 |
| dim_report | 53 | 53 | 0 | 8,275 | 5,769 |
| overall_trend | 57 | 57 | 0 | 8,735 | 8,433 |
| repeated_listings | 181 | 181 | 0 | 11,532 | 8,147 |
| scorecard | 91 | 91 | 0 | 21,638 | 14,264 |
| trend_tests | 99 | 99 | 0 | 23,421 | 12,911 |
| grai_ranks | 4,490 | 4,490 | 0 | 396,389 | 38,429 |
| list_appearances | 2,481 | 2,481 | 0 | 109,089 | 22,942 |
| ministry_monthly | 1,800 | 1,800 | 0 | 116,786 | 47,917 |
| **total** | **9,624** | **9,624** | **0** | **740,510** | **196,844** |

Parquet totals 27% of the CSV bytes. At this size Parquet's fixed per-file metadata is significant, so small tables can be larger as Parquet; Athena bytes-scanned savings come mainly from reading only the referenced columns, which only an Athena run can measure.

## Athena SQL (Trino syntax, executed via DuckDB)

| Query | Trino parse | Rows | Mismatches vs published values |
|---|---|---:|---:|
| 00_views | ok | view | - |
| 01_month_over_month | ok | 57 | 0 |
| 02_ministry_backlog | ok | 90 | 0 |
| 03_rolling_average | ok | 57 | 0 |
| 04_first_last_appearance | ok | 181 | 0 |
| 05_demand_vs_backlog | ok | 90 | 0 |

## Redshift SQL (executed unchanged on PostgreSQL)

- Schema: 32 statements applied with keys enforced.
- Publish batch: 32 statements, one transaction.
- Publish guard refuses a different ingestion: **True**.
- Row-count reconciliation (warehouse vs curated): all tables match

| Query | Athena rows | Redshift rows | Differing rows |
|---|---:|---:|---:|
| 01_month_over_month | 57 | 57 | 0 |
| 02_ministry_backlog | 90 | 90 | 0 |
| 03_rolling_average | 57 | 57 | 0 |
| 04_first_last_appearance | 181 | 181 | 0 |
| 05_demand_vs_backlog | 90 | 90 | 0 |
