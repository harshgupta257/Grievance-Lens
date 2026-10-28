"""The schema contract and every generated DDL file stay consistent with the exporter and the CSVs."""

from __future__ import annotations

import csv
import subprocess
import sys

from conftest import EXPORTS, ROOT


def _run(module: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, "-m", module, "--check"], cwd=ROOT, capture_output=True, text=True)


def test_contract_is_up_to_date():
    r = _run("aws.contract.build_contract")
    assert r.returncode == 0, r.stdout + r.stderr


def test_generated_ddl_is_up_to_date():
    r = _run("aws.contract.render_ddl")
    assert r.returncode == 0, r.stdout + r.stderr


def test_contract_matches_exporter_definitions(contract):
    sys.path.insert(0, str(ROOT))
    from src.export.powerbi import FOREIGN_KEYS, TABLES

    assert {t["name"] for t in contract["tables"]} == set(TABLES)
    for t in contract["tables"]:
        spec = TABLES[t["name"]]
        assert [c["name"] for c in t["columns"]] == list(spec.columns)
        assert t["primary_key"] == list(spec.key)
        assert {
            (t["name"], fk["column"]): (fk["ref_table"], fk["ref_column"]) for fk in t["foreign_keys"]
        } == {k: v for k, v in FOREIGN_KEYS.items() if k[0] == t["name"]}


def test_csv_headers_and_row_counts(contract):
    manifest = {
        r["table"]: r for r in csv.DictReader(open(EXPORTS / "export_manifest.csv", encoding="utf-8"))
    }
    for t in contract["tables"]:
        with open(EXPORTS / t["file"], encoding="utf-8", newline="") as fh:
            reader = csv.reader(fh)
            header = next(reader)
            rows = sum(1 for _ in reader)
        assert header == [c["name"] for c in t["columns"]]
        assert rows == int(manifest[t["name"]]["rows"])


def test_keys_not_null_and_other_columns_nullable(contract):
    for t in contract["tables"]:
        for c in t["columns"]:
            assert c["nullable"] is (c["name"] not in t["primary_key"])


def test_load_order_puts_parents_first(contract):
    pos = {n: i for i, n in enumerate(contract["load_order"])}
    for t in contract["tables"]:
        for fk in t["foreign_keys"]:
            if fk["ref_table"] != t["name"]:
                assert pos[fk["ref_table"]] < pos[t["name"]], (t["name"], fk)


def test_type_override_and_partition(contract):
    tables = {t["name"]: t for t in contract["tables"]}
    col = next(c for c in tables["dim_list"]["columns"] if c["name"] == "reports_missing_in_span")
    assert col["type"] == "string" and "semicolon" in col["type_override_reason"]
    assert tables["ministry_monthly"]["partition"]["column"] == "period_year"
    assert [t for t, v in tables.items() if v["partition"]] == ["ministry_monthly"]


def test_infer_type_rules():
    from aws.contract.build_contract import infer_type

    assert infer_type(["True", "False", ""]) == "boolean"
    assert infer_type(["2024-01-01", ""]) == "date"
    assert infer_type(["1", "-2", ""]) == "int"
    assert infer_type(["3000000000"]) == "double"  # beyond int32
    assert infer_type(["0.5", "1e-08"]) == "double"
    assert infer_type(["A", "1"]) == "string"
    assert infer_type(["", ""]) == "string"
