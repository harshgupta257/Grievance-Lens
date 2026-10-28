"""Benchmark bookkeeping and the deploy script's desired-state builders (no AWS calls)."""

from __future__ import annotations

import argparse

import benchmark_runner as br
import deploy
import pytest


def test_load_pairs_has_csv_and_parquet_members():
    pairs = br.load_pairs()
    assert set(pairs) == {"b1", "b2", "b3", "b4"}
    for p in pairs.values():
        assert "grievance_lens_raw." in p["csv"] and "grievance_lens." in p["parquet"] and p["measures"]


def test_rows_hash_normalises_numbers():
    assert br.rows_hash([["a", "1.0000000001"]]) == br.rows_hash([["a", 1.0]])
    assert br.rows_hash([["a", "1"]]) != br.rows_hash([["a", "2"]])


def _ex(qid, scanned, engine):
    return {
        "QueryExecutionId": qid,
        "Status": {"State": "SUCCEEDED", "SubmissionDateTime": "2026-10-27"},
        "Statistics": {
            "DataScannedInBytes": scanned,
            "EngineExecutionTimeInMillis": engine,
            "TotalExecutionTimeInMillis": engine + 100,
        },
    }


def test_summary_and_comparison_use_only_reported_metrics():
    csv_runs = [{**br.stats_row(_ex(f"c{i}", 1000, 900 + i)), "rows": 12, "rows_hash": "h"} for i in range(3)]
    pq_runs = [{**br.stats_row(_ex(f"p{i}", 250, 700 + i)), "rows": 12, "rows_hash": "h"} for i in range(3)]
    c, p = br.summarize(csv_runs), br.summarize(pq_runs)
    assert c["median_bytes"] == 1000 and p["median_engine_ms"] == 701
    cmp = br.compare(c, p)
    assert cmp == {
        "results_identical": True,
        "bytes_reduction_pct": 75.0,
        "engine_time_change_pct": round(100 * (701 / 901 - 1), 1),
    }
    missing = br.summarize(
        [{**br.stats_row({"QueryExecutionId": "x", "Status": {}}), "rows": 1, "rows_hash": "h"}]
    )
    assert missing["median_bytes"] is None  # never invented


def test_different_results_are_flagged():
    a = br.summarize([{"data_scanned_bytes": 1, "engine_ms": 1, "total_ms": 1, "rows": 1, "rows_hash": "a"}])
    b = br.summarize([{"data_scanned_bytes": 1, "engine_ms": 1, "total_ms": 1, "rows": 1, "rows_hash": "b"}])
    assert br.compare(a, b)["results_identical"] is False


@pytest.fixture
def cfg():
    a = deploy.parse_args(["--bucket", "grievance-lens-test-bucket", "--glue-execution-class", "FLEX"])
    return deploy.Config(a, "123456789012")


def test_templates_render_and_keep_aws_variables(cfg):
    for name in (
        "glue_etl_policy.json",
        "lambda_orchestrator_policy.json",
        "redshift_copy_policy.json",
        "operator_policy.json",
        "bucket_policy.json",
    ):
        text = str(deploy.render_template(name, cfg.values))
        assert "${BUCKET}" not in text and "grievance-lens-test-bucket" in text
    trust = str(deploy.render_template("trust_glue.json", cfg.values))
    assert "${ACCOUNT_ID}" not in trust and "123456789012" in trust
    op = str(deploy.render_template("operator_policy.json", cfg.values))
    assert "${aws:userid}" in op  # an IAM policy variable, not a placeholder


def test_lambda_policy_is_least_privilege(cfg):
    doc = deploy.render_template("lambda_orchestrator_policy.json", cfg.values)
    actions = {
        a for s in doc["Statement"] for a in ([s["Action"]] if isinstance(s["Action"], str) else s["Action"])
    }
    assert actions == {
        "s3:ListBucket",
        "s3:GetObject",
        "s3:PutObject",
        "s3:DeleteObject",
        "glue:StartJobRun",
        "logs:CreateLogStream",
        "logs:PutLogEvents",
    }
    start = next(s for s in doc["Statement"] if s["Action"] == "glue:StartJobRun")
    assert start["Resource"].endswith(":job/grievance-lens-etl")
    writes = next(s for s in doc["Statement"] if "s3:PutObject" in s["Action"])
    assert writes["Resource"] == "arn:aws:s3:::grievance-lens-test-bucket/control/batches/*"


def test_glue_job_definition(cfg):
    job = deploy.glue_job_definition(cfg)
    assert (job["GlueVersion"], job["WorkerType"], job["NumberOfWorkers"]) == ("5.0", "G.1X", 2)
    assert (
        job["ExecutionProperty"]["MaxConcurrentRuns"] == 1 and job["MaxRetries"] == 0 and job["Timeout"] == 15
    )
    assert job["DefaultArguments"]["--job-bookmark-option"] == "job-bookmark-disable"


def test_notification_merge_preserves_other_configurations():
    existing = {
        "LambdaFunctionConfigurations": [
            {"Id": "someone-else", "LambdaFunctionArn": "arn:x", "Events": ["s3:ObjectCreated:*"]},
            {"Id": deploy.NOTIFICATION_ID, "LambdaFunctionArn": "arn:old", "Events": []},
        ],
        "QueueConfigurations": [{"Id": "q", "QueueArn": "arn:q", "Events": ["s3:ObjectRemoved:*"]}],
        "ResponseMetadata": {"HTTPStatusCode": 200},
    }
    merged = deploy.merged_notification(existing, "arn:new")
    ids = [c["Id"] for c in merged["LambdaFunctionConfigurations"]]
    assert ids == ["someone-else", deploy.NOTIFICATION_ID]
    ours = merged["LambdaFunctionConfigurations"][-1]
    assert ours["LambdaFunctionArn"] == "arn:new"
    assert {r["Name"]: r["Value"] for r in ours["Filter"]["Key"]["FilterRules"]} == {
        "prefix": "raw/powerbi/",
        "suffix": "_manifest.json",
    }
    assert (
        merged["QueueConfigurations"] == existing["QueueConfigurations"] and "ResponseMetadata" not in merged
    )


def test_athena_workgroup_has_cost_guard(cfg):
    conf = deploy.athena_workgroup_configuration(cfg)
    assert conf["EnforceWorkGroupConfiguration"] and conf["BytesScannedCutoffPerQuery"] == 1024**3
    assert conf["ResultConfiguration"]["OutputLocation"] == "s3://grievance-lens-test-bucket/athena-results/"


def test_plan_makes_no_aws_calls(capsys):
    assert (
        deploy.main(["--bucket", "grievance-lens-test-bucket", "--with-redshift", "--budget-email", "a@b.c"])
        == 0
    )
    out = capsys.readouterr().out
    assert "Nothing was changed" in out and "usage limit" in out and "not a spending limit" in out


@pytest.mark.parametrize("bucket", ["UPPER", "a", "bad..dots", "xn--punycode"])
def test_invalid_bucket_names(bucket):
    with pytest.raises(SystemExit):
        deploy.parse_args(["--bucket", bucket])


def test_lifecycle_never_expires_raw_or_curated():
    rules = deploy.bucket_lifecycle()["Rules"]
    expiring = [r for r in rules if "Expiration" in r]
    assert [r["Filter"]["Prefix"] for r in expiring] == ["athena-results/"]
    assert isinstance(argparse.Namespace, type)
