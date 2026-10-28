"""Spark transformation logic of the Glue job: typing, rejection reasons, malformed lines, schema drift,
partition columns. Needs Java (skipped otherwise). Inputs are SYNTHETIC rows, not project data."""

from __future__ import annotations

import datetime as dt

import etl_job as etl
import pytest

TABLE = {
    "name": "synthetic_table",
    "columns": [
        {"name": "id", "type": "int", "nullable": False, "description": ""},
        {"name": "score", "type": "double", "nullable": True, "description": ""},
        {"name": "flag", "type": "boolean", "nullable": True, "description": ""},
        {"name": "day", "type": "date", "nullable": True, "description": ""},
        {"name": "label", "type": "string", "nullable": True, "description": ""},
    ],
    "primary_key": ["id"],
    "foreign_keys": [],
    "partition": None,
}


def frame(spark, rows):
    schema = etl.string_schema(TABLE)
    return spark.createDataFrame([(*r, None) for r in rows], schema)


def test_valid_values_are_typed_and_blanks_stay_null(spark):
    good, bad = etl.apply_contract(
        frame(
            spark,
            [
                (" 1 ", "0.5", "True", "2024-06-01", "  keep spaces "),
                ("2", None, None, None, None),
                ("3", "1e-08", "false", "2026-01-31", ""),
            ],
        ),
        TABLE,
    )
    assert bad.count() == 0
    rows = {r["id"]: r for r in good.collect()}
    assert rows[1]["score"] == 0.5 and rows[1]["flag"] is True and rows[1]["day"] == dt.date(2024, 6, 1)
    assert rows[1]["label"] == "  keep spaces "  # strings are never altered
    assert rows[2]["score"] is None and rows[2]["flag"] is None and rows[2]["day"] is None
    assert rows[3]["score"] == 1e-08 and rows[3]["flag"] is False
    assert [f.dataType.simpleString() for f in good.schema.fields] == [
        "int",
        "double",
        "boolean",
        "date",
        "string",
    ]


@pytest.mark.parametrize(
    "row,reason",
    [
        (("1.5", "0", "True", "2024-01-01", "x"), "id: not a valid int"),
        (("3000000000", "0", "True", "2024-01-01", "x"), "id: not a valid int"),  # int32 overflow
        (("1", "NaN", "True", "2024-01-01", "x"), "score: not a valid double"),
        (("1", "0", "yes", "2024-01-01", "x"), "flag: not a valid boolean"),
        (("1", "0", "True", "2024-13-01", "x"), "day: not a valid date"),
        (("1", "0", "True", "01/02/2024", "x"), "day: not a valid date"),
    ],
)
def test_invalid_values_reject_the_row_with_a_reason(spark, row, reason):
    good, bad = etl.apply_contract(frame(spark, [row, ("9", "1", "True", "2024-01-01", "ok")]), TABLE)
    assert good.count() == 1
    [rej] = bad.collect()
    assert reason in rej["reason"]


def test_several_failures_are_all_reported(spark):
    _, bad = etl.apply_contract(frame(spark, [("x", "y", "z", "w", "ok")]), TABLE)
    reason = bad.first()["reason"]
    assert all(f"{c}: not a valid" in reason for c in ("id", "score", "flag", "day"))


def test_malformed_csv_line_is_rejected_not_dropped(spark, tmp_path):
    p = tmp_path / "t.csv"
    p.write_text(
        'id,score,flag,day,label\n1,0.5,True,2024-01-01,a\n2,0.1,False,2024-01-02,b,EXTRA\n"3,broken\n',
        encoding="utf-8",
    )
    raw = etl.read_raw_csv(spark, str(p), TABLE).cache()
    good, bad = etl.apply_contract(raw, TABLE)
    assert raw.count() == 3
    assert good.count() == 1
    reasons = [r["reason"] for r in bad.collect()]
    assert len(reasons) == 2 and all("malformed line" in r for r in reasons)


def test_quoted_commas_are_values_not_separators(spark, tmp_path):
    p = tmp_path / "t.csv"
    p.write_text(
        'id,score,flag,day,label\n1,1,True,2024-01-01,"Ministries with more than 1,000 pending"\n',
        encoding="utf-8",
    )
    good, bad = etl.apply_contract(etl.read_raw_csv(spark, str(p), TABLE).cache(), TABLE)
    assert bad.count() == 0 and good.first()["label"] == "Ministries with more than 1,000 pending"


@pytest.mark.parametrize(
    "header,expected",
    [
        ("id,score,flag,day,label", []),
        ("﻿id,score,flag,day,label", []),
        ("id,score,flag,label", ["missing=['day']"]),
        ("id,score,flag,day,label,new_col", ["unexpected=['new_col']"]),
        ("score,id,flag,day,label", ["reordered"]),
        ("id,score,flag,date,label", ["missing=['day'] unexpected=['date']"]),
    ],
)
def test_schema_drift_detection(header, expected):
    problems = etl.header_problems(header + "\r\n", TABLE)
    assert len(problems) == len(expected) and all(e in p for e, p in zip(expected, problems, strict=True))


def test_partition_column_is_derived(spark):
    table = dict(
        TABLE,
        partition={"column": "period_year", "type": "int", "derived_from": "day", "expression": "year(day)"},
    )
    good, _ = etl.apply_contract(frame(spark, [("1", "1", "True", "2025-03-01", "x")]), TABLE)
    out = etl.add_partition_columns(good, table).first()
    assert out["period_year"] == 2025


def test_job_argument_parsing_ignores_glue_extras():
    args = etl.parse_job_args(
        [
            "etl_job.py",
            "--JOB_NAME",
            "j",
            "--run_date",
            "2026-10-27",
            "--batch_id",
            "B1",
            "--raw_prefix=s3://b/raw/",
            "--TempDir",
            "s3://b/tmp",
            "--publish",
        ]
    )
    assert (
        args["run_date"] == "2026-10-27" and args["raw_prefix"] == "s3://b/raw/" and args["publish"] == "true"
    )
    with pytest.raises(etl.EtlError, match="missing job parameter"):
        etl.load_config(args)
    full = dict(args, curated_root="s3://b/c", control_root="s3://b/k", contract_uri="s3://b/s/c.json")
    cfg = etl.load_config(full)
    assert cfg.ingestion_id == "2026-10-27_B1" and cfg.curated_root == "s3://b/c/"
    with pytest.raises(etl.EtlError, match="run_date"):
        etl.load_config(dict(full, run_date="27-10-2026"))
    with pytest.raises(etl.EtlError, match="batch_id"):
        etl.load_config(dict(full, batch_id="../x"))
