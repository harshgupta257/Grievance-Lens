"""Crawler schema audit: detects missing tables, renamed columns and type changes against the contract."""

from __future__ import annotations

import sys

from conftest import AWS

sys.path.insert(0, str(AWS / "catalog"))
import crawler_audit  # noqa: E402


def crawled_from(contract: dict) -> dict:
    out = {}
    for t in contract["tables"]:
        cols = [{"Name": c["name"], "Type": crawler_audit.GLUE_TYPES[c["type"]]} for c in t["columns"]]
        if t.get("partition"):
            cols.append({"Name": t["partition"]["column"], "Type": "int"})
        out[t["name"]] = cols
    return out


def test_matching_schemas_report_nothing(contract):
    assert crawler_audit.compare_schemas(contract, crawled_from(contract)) == []


def test_drift_is_reported(contract):
    crawled = crawled_from(contract)
    del crawled["dim_list"]
    crawled["overall_trend"] = [
        dict(c, Type="bigint") if c["Name"] == "receipts" else c for c in crawled["overall_trend"]
    ]
    crawled["dim_month"][0]["Name"] = "month_start"
    problems = crawler_audit.compare_schemas(contract, crawled)
    assert "dim_list: not found by the crawler" in problems
    assert "overall_trend.receipts: contract=int crawled=bigint" in problems
    assert "dim_month.month: contract=date crawled=None" in problems
    assert "dim_month.month_start: contract=None crawled=date" in problems
