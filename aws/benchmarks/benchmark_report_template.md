# Athena benchmark: raw CSV vs curated Parquet

> **Status: pending.** No AWS run has been made yet, so this page holds no numbers. Run
> `python aws/benchmarks/benchmark_runner.py` after a batch has been published; it writes
> `aws/benchmarks/results/<timestamp>.md` in the layout below, filled only with Athena's own statistics.
> Local results (DuckDB/PostgreSQL, no AWS) are in [local_verification.md](local_verification.md) and are not
> Athena measurements.

| Pair | Measures | Rows identical | CSV bytes (median) | Parquet bytes (median) | Bytes reduction | CSV engine ms (median, range) | Parquet engine ms (median, range) | Engine time change |
|---|---|---|---:|---:|---:|---:|---:|---:|
| b1 | column pruning on the largest table (grai_ranks) | pending | pending | pending | pending | pending | pending | pending |
| b2 | partition pruning (ministry_monthly, period_year = 2025) | pending | pending | pending | pending | pending | pending | pending |
| b3 | fact-to-dimension join with a selective filter | pending | pending | pending | pending | pending | pending | pending |
| b4 | complete analysis (backlog_months) | pending | pending | pending | pending | pending | pending | pending |

## Method

- Same logical data: the raw tables point at the exact CSV batch whose curated output is published
  (`control/published/latest.json`), and `aws/local/verify_local.py` checks every curated value against
  its CSV value. Each pair must also return identical rows on every run, or it is not reported.
- Each query runs `--repetitions` times (default 5), CSV and Parquet alternately, with Athena result reuse
  disabled. Medians and ranges are reported; every execution's raw metadata (QueryExecutionId, submission
  time, bytes scanned, engine/total/queue/planning time, result location) is kept in the `.json` file.

## Why Parquet can scan fewer bytes

- **Columnar layout:** a query reads only the columns it references. CSV is row-oriented, so Athena must
  read every byte of every row even when the query needs three of fifteen columns.
- **Compression:** Snappy-compressed, dictionary- and run-length-encoded columns are smaller than the
  equivalent text, and repeated values (group codes, methodology labels) compress very well.
- **Typed values:** numbers and dates are stored in binary, so there is no text parsing or `TRY_CAST` at
  query time.
- **Partition pruning:** with `ministry_monthly` partitioned by `period_year` (partition projection), a
  `WHERE period_year = 2025` query reads only that year's folder. The CSV table must read the whole file and
  filter afterwards.
- **Row-group statistics:** Parquet stores min/max per column chunk, so some predicates can skip data
  (limited here: each table is a single small file).

## What to expect at this size, honestly

- **Bytes scanned** should fall clearly for queries that touch few columns of wide tables (b1, b2).
  Measured locally, the Parquet files total 27% of the CSV bytes before any column pruning.
- **Execution time** is dominated by fixed planning and scheduling overhead for files of a few hundred
  kilobytes. Differences within the observed run-to-run range are noise, not a format effect, and Parquet
  is not expected to be faster on every query.
- **Cost** is identical per query: Athena bills a minimum of 10 MB scanned per query, which exceeds this
  whole dataset.
- **Caching:** with result reuse disabled, Athena still benefits from warm metadata and storage paths on
  repeated runs. That is why the first run of each query is often slower; medians reduce its influence.
