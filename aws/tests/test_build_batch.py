"""Batch staging: completeness checks against the exporter's manifest, byte-identical copies, immutability."""

from __future__ import annotations

import hashlib
import json
import shutil

import build_batch
import pytest
from conftest import EXPORTS


def test_staged_batch_matches_export_manifest(staged_batch, contract):
    manifest = json.loads((staged_batch / "_manifest.json").read_text(encoding="utf-8"))
    assert manifest["ingestion_id"] == "2026-10-27_TESTBATCH01"
    assert manifest["contract_hash"] == contract["contract_hash"]
    assert [f["table"] for f in manifest["files"]] == [t["name"] for t in contract["tables"]]
    for f in manifest["files"]:
        data = (staged_batch / f["path"]).read_bytes()
        assert len(data) == f["bytes"]
        assert hashlib.sha256(data).hexdigest() == f["sha256"]
        assert data == (EXPORTS / f"{f['table']}.csv").read_bytes()  # copied byte-for-byte


def test_staging_is_immutable(staged_batch):
    with pytest.raises(FileExistsError):
        build_batch.main(
            ["--out", str(staged_batch.parents[1]), "--run-date", "2026-10-27", "--batch-id", "TESTBATCH01"]
        )


def _copy_exports(tmp_path):
    src = tmp_path / "exports"
    shutil.copytree(EXPORTS, src)
    return src


def test_tampered_csv_is_refused(tmp_path, capsys):
    src = _copy_exports(tmp_path)
    with open(src / "dim_month.csv", "a", encoding="utf-8") as fh:
        fh.write("2026-10-01,57,2026,10,Oct,2026-10,FY2026-27,True\n")
    rc = build_batch.main(["--source", str(src), "--out", str(tmp_path / "out"), "--batch-id", "X1"])
    err = capsys.readouterr().err
    assert rc == 1 and "dim_month: SHA-256 differs" in err and "rows" in err
    assert not (tmp_path / "out").exists()


def test_missing_table_is_refused(tmp_path, capsys):
    src = _copy_exports(tmp_path)
    (src / "grai_ranks.csv").unlink()
    rc = build_batch.main(["--source", str(src), "--out", str(tmp_path / "out"), "--batch-id", "X2"])
    assert rc == 1 and "missing CSV(s) for contract tables: ['grai_ranks']" in capsys.readouterr().err


def test_unexpected_table_is_refused(tmp_path, capsys):
    src = _copy_exports(tmp_path)
    (src / "extra_table.csv").write_text("a\n1\n", encoding="utf-8")
    rc = build_batch.main(["--source", str(src), "--out", str(tmp_path / "out"), "--batch-id", "X3"])
    assert rc == 1 and "unexpected CSV" in capsys.readouterr().err


@pytest.mark.parametrize("batch_id", ["../escape", "has space", "x" * 65])
def test_invalid_batch_id(batch_id, tmp_path):
    with pytest.raises(SystemExit):
        build_batch.main(["--out", str(tmp_path), "--batch-id", batch_id])
