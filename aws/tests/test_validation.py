"""Glue job validation and the full run: real exports publish; corrupted batches fail without publishing;
re-runs are idempotent. Needs Java (skipped otherwise). The Glue catalog is a recording fake."""

from __future__ import annotations

import json
import shutil

import etl_job as etl
import pytest
from conftest import AWS


class FakeGlueCatalog:
    class exceptions:  # noqa: N801 - mirrors boto3's client.exceptions
        class EntityNotFoundException(Exception):
            pass

    def __init__(self):
        self.databases, self.tables, self.calls = set(), {}, []

    def get_database(self, Name):  # noqa: N803
        if Name not in self.databases:
            raise self.exceptions.EntityNotFoundException(Name)

    def create_database(self, DatabaseInput):  # noqa: N803
        self.databases.add(DatabaseInput["Name"])

    def update_table(self, DatabaseName, TableInput):  # noqa: N803
        if TableInput["Name"] not in self.tables:
            raise self.exceptions.EntityNotFoundException(TableInput["Name"])
        self.calls.append(("update", TableInput["Name"]))
        self.tables[TableInput["Name"]] = TableInput

    def create_table(self, DatabaseName, TableInput):  # noqa: N803
        self.calls.append(("create", TableInput["Name"]))
        self.tables[TableInput["Name"]] = TableInput


def config(batch_dir, tmp_path, publish=True, **kw) -> etl.JobConfig:
    run_part, batch_part = batch_dir.parent.name, batch_dir.name
    return etl.JobConfig(
        run_date=run_part.split("=", 1)[1],
        batch_id=batch_part.split("=", 1)[1],
        raw_prefix=str(batch_dir) + "/",
        curated_root=str(tmp_path / "curated") + "/",
        control_root=str(tmp_path / "control") + "/",
        contract_uri=str(AWS / "contract" / "contract.json"),
        publish=publish,
        **kw,
    )


@pytest.fixture
def batch_copy(staged_batch, tmp_path):
    dest = tmp_path / "raw" / staged_batch.parent.name / staged_batch.name
    shutil.copytree(staged_batch, dest)
    return dest


def test_real_exports_validate_write_and_publish(spark, staged_batch, tmp_path, contract):
    glue = FakeGlueCatalog()
    cfg = config(staged_batch, tmp_path)
    report = etl.run(spark, cfg, etl.Storage(), glue=glue)
    assert report["status"] == "PUBLISHED" and report["critical_issues"] == []
    assert report["rows_processed"] == 9624 and report["rows_rejected"] == 0
    assert all(t["rows_written"] == t["expected_rows"] for t in report["tables"].values())
    assert report["tables"]["ministry_monthly"]["partitions"] == [
        "period_year=2024",
        "period_year=2025",
        "period_year=2026",
    ]
    assert (
        all(fk["orphan_values"] == 0 for fk in report["foreign_keys"]) and len(report["foreign_keys"]) == 15
    )
    # catalog: every table points at this ingestion; ministry_monthly uses partition projection
    assert set(glue.tables) == {t["name"] for t in contract["tables"]}
    mm = glue.tables["ministry_monthly"]
    assert mm["PartitionKeys"][0]["Name"] == "period_year"
    assert mm["Parameters"]["projection.enabled"] == "true"
    assert mm["StorageDescriptor"]["Location"].endswith(
        "ingestion_id=2026-10-27_TESTBATCH01/ministry_monthly/"
    )
    # pointer written last, with the counts the warehouse refresh will verify
    pointer = json.loads((tmp_path / "control" / "published" / "latest.json").read_text(encoding="utf-8"))
    assert pointer["ingestion_id"] == cfg.ingestion_id and sum(pointer["table_rows"].values()) == 9624
    manifest = json.loads(
        (
            tmp_path
            / "control"
            / "redshift_manifests"
            / f"ingestion_id={cfg.ingestion_id}"
            / "grai_ranks.manifest"
        ).read_text(encoding="utf-8")
    )
    assert manifest["entries"] and all(
        e["meta"]["content_length"] > 0 and e["url"].endswith(".parquet") for e in manifest["entries"]
    )


def test_rerun_is_idempotent(spark, staged_batch, tmp_path):
    glue = FakeGlueCatalog()
    cfg = config(staged_batch, tmp_path)
    first = etl.run(spark, cfg, etl.Storage(), glue=glue)
    files_1 = sorted(p.relative_to(tmp_path) for p in (tmp_path / "curated").rglob("*.parquet"))
    second = etl.run(spark, cfg, etl.Storage(), glue=glue)
    files_2 = sorted(p.relative_to(tmp_path) for p in (tmp_path / "curated").rglob("*.parquet"))
    assert files_1 == files_2  # overwritten in place, never duplicated
    assert {t: v["rows_written"] for t, v in first["tables"].items()} == {
        t: v["rows_written"] for t, v in second["tables"].items()
    }
    assert [c[0] for c in glue.calls].count("create") == 15  # second run only updates


def _rewrite(batch_dir, table, edit):
    path = batch_dir / table / f"{table}.csv"
    text = edit(path.read_text(encoding="utf-8"))
    path.write_text(text, encoding="utf-8", newline="\n")


def _rehash(batch_dir):
    """Update the manifest so the corruption is caught by data checks, not by the checksum."""
    import hashlib

    mpath = batch_dir / "_manifest.json"
    m = json.loads(mpath.read_text(encoding="utf-8"))
    for f in m["files"]:
        data = (batch_dir / f["path"]).read_bytes()
        f["bytes"], f["sha256"] = len(data), hashlib.sha256(data).hexdigest()
    mpath.write_text(json.dumps(m), encoding="utf-8")


def assert_failed_not_published(spark, batch_copy, tmp_path, fragment):
    glue = FakeGlueCatalog()
    with pytest.raises(etl.EtlError):
        etl.run(spark, config(batch_copy, tmp_path), etl.Storage(), glue=glue)
    report = json.loads(
        next((tmp_path / "control" / "quality").rglob("report.json")).read_text(encoding="utf-8")
    )
    assert report["status"] == "FAILED" and any(fragment in i for i in report["critical_issues"]), report[
        "critical_issues"
    ]
    assert not glue.tables and not (tmp_path / "control" / "published").exists()
    return report


def test_checksum_mismatch_fails(spark, batch_copy, tmp_path):
    _rewrite(batch_copy, "dim_month", lambda t: t.replace("Jan", "JAN", 1))
    assert_failed_not_published(spark, batch_copy, tmp_path, "SHA-256")


def test_duplicate_primary_key_fails(spark, batch_copy, tmp_path):
    def dup(text):
        lines = text.splitlines()
        return "\n".join(lines + [lines[1]]) + "\n"

    _rewrite(batch_copy, "dim_ministry", dup)
    _rehash(batch_copy)
    report = assert_failed_not_published(spark, batch_copy, tmp_path, "duplicate keys")
    assert report["tables"]["dim_ministry"]["pk_duplicates"] == 1


def test_orphan_foreign_key_fails(spark, batch_copy, tmp_path):
    def orphan(text):
        lines = text.splitlines()
        first = lines[1].split(",")
        first[1] = "999"  # a ministry_id that is not in dim_ministry
        return "\n".join([lines[0], ",".join(first)] + lines[2:]) + "\n"

    _rewrite(batch_copy, "ministry_monthly", orphan)
    _rehash(batch_copy)
    assert_failed_not_published(
        spark, batch_copy, tmp_path, "ministry_monthly.ministry_id: 1 values not in dim_ministry"
    )


def test_bad_value_is_rejected_and_fails_at_zero_tolerance(spark, batch_copy, tmp_path):
    _rewrite(
        batch_copy,
        "overall_trend",
        lambda t: t.replace("\n2022-01-01,94300,", "\n2022-01-01,ninety-four,", 1),
    )
    _rehash(batch_copy)
    report = assert_failed_not_published(spark, batch_copy, tmp_path, "rows rejected")
    assert report["tables"]["overall_trend"]["rows_rejected"] == 1
    samples = json.loads(
        next((tmp_path / "control" / "rejects").rglob("overall_trend.json")).read_text(encoding="utf-8")
    )
    assert "receipts: not a valid int" in samples[0]["reason"]


def test_schema_drift_fails(spark, batch_copy, tmp_path):
    _rewrite(batch_copy, "dim_list", lambda t: t.replace("caveat", "warning", 1))
    _rehash(batch_copy)
    assert_failed_not_published(spark, batch_copy, tmp_path, "schema drift")


def test_manifest_for_another_batch_fails_before_reading(spark, batch_copy, tmp_path):
    mpath = batch_copy / "_manifest.json"
    m = json.loads(mpath.read_text(encoding="utf-8"))
    m["batch_id"] = "SOMETHING_ELSE"
    mpath.write_text(json.dumps(m), encoding="utf-8")
    report = assert_failed_not_published(spark, batch_copy, tmp_path, "manifest is for")
    assert report["tables"] == {}


def test_s3_paths_never_use_the_local_writer(monkeypatch):
    monkeypatch.setenv("GL_LOCAL_PARQUET_WRITER", "pyarrow")
    with pytest.raises(etl.EtlError, match="local runs only"):
        etl.write_parquet(None, "s3://bucket/curated/x/", {"name": "x", "partition": None})
