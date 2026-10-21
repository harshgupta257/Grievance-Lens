import json

from src.extract.reprints import ISSUE_TYPE, flag_reprints


def obs(name, value, status="valid"):
    return {
        "entity_name_original": name,
        "metric_code": "avg_closing_time_days",
        "value_original": value,
        "validation_status": status,
    }


def write(tmp_path, key, rows):
    table = {
        "table_type": "avg_closing_time_high",
        "grai_group": None,
        "pdf_page_start": 33,
        "observations": rows,
        "issues": [],
    }
    (tmp_path / f"{key}.json").write_text(json.dumps({"tables": [table]}), encoding="utf-8")


def test_identical_table_is_flagged_once_and_downgraded(tmp_path):
    write(tmp_path, "2025-08_r40_en", [obs("CBDT", "56"), obs("SSC", "27")])
    # same values, other order; closing-time rows carry no identity check, so they start 'unvalidated'
    write(tmp_path, "2025-09_r41_en", [obs("SSC", "27", "unvalidated"), obs("CBDT", "56")])
    assert flag_reprints(tmp_path) == [("2025-09_r41_en", "avg_closing_time_high")]
    t = json.loads((tmp_path / "2025-09_r41_en.json").read_text(encoding="utf-8"))["tables"][0]
    assert [i["issue_type"] for i in t["issues"]] == [ISSUE_TYPE]
    assert {o["validation_status"] for o in t["observations"]} == {"warning"}
    flag_reprints(tmp_path)  # idempotent: no second issue
    t = json.loads((tmp_path / "2025-09_r41_en.json").read_text(encoding="utf-8"))["tables"][0]
    assert len(t["issues"]) == 1


def test_changed_table_is_not_flagged(tmp_path):
    write(tmp_path, "2025-08_r40_en", [obs("CBDT", "56")])
    write(tmp_path, "2025-09_r41_en", [obs("CBDT", "57")])
    assert flag_reprints(tmp_path) == []
