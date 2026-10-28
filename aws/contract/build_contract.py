"""Build the schema contract that the AWS pipeline enforces (aws/contract/contract.json).

    python -m aws.contract.build_contract            # rewrite contract.json
    python -m aws.contract.build_contract --check    # exit 1 if contract.json is out of date (used by tests)

The contract is derived, not hand-typed:
* tables, column order, grain, primary keys and foreign keys come from src/export/powerbi.py (TABLES,
  FOREIGN_KEYS), the same definitions that produce and validate the Power BI CSV exports;
* column types come from the values in data/processed/powerbi/*.csv, using the fixed rules below, plus
  TYPE_OVERRIDES where the observed values would mislead.

Type rules (checked on non-blank values only; blank = NULL):
    all True/False            -> boolean
    all YYYY-MM-DD            -> date
    all integers, |x| < 2^31  -> int      (no identifier in the exports has leading zeros; see README)
    all numbers               -> double   (p-values reach 1e-8: fixed decimals would round them to zero)
    anything else             -> string   (max_length recorded for warehouse VARCHAR sizing)

Only key columns are NOT NULL. Other columns may legitimately be blank ("not available"), and the exporter
never zero-fills them (docs/powerbi_guide.md), so nullability is not inferred from today's values.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
EXPORT_DIR = ROOT / "data" / "processed" / "powerbi"
CONTRACT_PATH = Path(__file__).with_name("contract.json")

# Observed values can mislead; each override states why.
TYPE_OVERRIDES: dict[tuple[str, str], tuple[str, str]] = {
    ("dim_list", "reports_missing_in_span"): (
        "string",
        "semicolon-separated report numbers per the data dictionary; "
        "today's values happen to be single numbers",
    ),
}

# Partitioning is chosen per table from size and query pattern, not applied everywhere (aws/README.md).
PARTITIONS: dict[str, dict[str, str]] = {
    "ministry_monthly": {
        "column": "period_year",
        "type": "int",
        "derived_from": "period_start",
        "expression": "year(period_start)",
        "reason": "the only multi-year ministry-level fact queried by period; 3 partitions (2024-2026)",
    },
}

_INT = re.compile(r"^-?\d+$")
_NUM = re.compile(r"^-?(\d+\.?\d*|\.\d+)([eE][-+]?\d+)?$")
_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def infer_type(values: list[str]) -> str:
    present = [v for v in values if v != ""]
    if not present:
        return "string"
    if all(v in ("True", "False") for v in present):
        return "boolean"
    if all(_DATE.match(v) for v in present):
        return "date"
    if all(_INT.match(v) for v in present) and max(abs(int(v)) for v in present) < 2**31:
        return "int"
    if all(_NUM.match(v) for v in present):
        return "double"
    return "string"


def _varchar_length(max_len: int) -> int:
    """Warehouse VARCHAR size: at least double the longest value seen, as a power of two, 16..2048."""
    size = 16
    while size < 2 * max_len and size < 2048:
        size *= 2
    return size


def build_contract() -> dict:
    sys.path.insert(0, str(ROOT))
    from src.export.powerbi import FOREIGN_KEYS, TABLES  # noqa: PLC0415 - project code, imported on demand

    tables = []
    for name, spec in TABLES.items():
        path = EXPORT_DIR / f"{name}.csv"
        with open(path, encoding="utf-8", newline="") as fh:
            reader = csv.DictReader(fh)
            header = reader.fieldnames or []
            rows = list(reader)
        if header != list(spec.columns):
            raise ValueError(f"{path.name}: header {header} differs from TABLES['{name}'].columns")
        columns = []
        for col, description in spec.columns.items():
            values = [r[col] for r in rows]
            col_type, why = TYPE_OVERRIDES.get((name, col), (infer_type(values), ""))
            entry = {
                "name": col,
                "type": col_type,
                "nullable": col not in spec.key,
                "description": description,
            }
            if col_type == "string":
                entry["max_length"] = _varchar_length(max((len(v) for v in values), default=1))
            if why:
                entry["type_override_reason"] = why
            columns.append(entry)
        fks = [
            {"column": col, "ref_table": ref[0], "ref_column": ref[1]}
            for (table, col), ref in FOREIGN_KEYS.items()
            if table == name
        ]
        tables.append(
            {
                "name": name,
                "file": path.name,
                "kind": "dimension" if name.startswith("dim_") else "fact",
                "grain": spec.grain,
                "primary_key": list(spec.key),
                "columns": columns,
                "foreign_keys": fks,
                "partition": PARTITIONS.get(name),
            }
        )

    # Load order: a table's foreign-key targets always come before it (dimensions first).
    order, placed = [], set()
    pending = {t["name"]: {fk["ref_table"] for fk in t["foreign_keys"]} - {t["name"]} for t in tables}
    while pending:
        ready = sorted(n for n, deps in pending.items() if deps <= placed)
        if not ready:
            raise ValueError(f"foreign-key cycle among {sorted(pending)}")
        for n in ready:
            order.append(n)
            placed.add(n)
            del pending[n]

    body = {
        "contract_version": 1,
        "dataset": "grievance_lens_powerbi",
        "source": "data/processed/powerbi (python -m src.export.powerbi)",
        "load_order": order,
        "tables": sorted(tables, key=lambda t: order.index(t["name"])),
    }
    digest = hashlib.sha256(json.dumps(body, sort_keys=True).encode()).hexdigest()[:16]
    return {**body, "contract_hash": digest}


def render(contract: dict) -> str:
    return json.dumps(contract, indent=2, ensure_ascii=False) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--check", action="store_true", help="fail if contract.json is not up to date")
    args = parser.parse_args()
    text = render(build_contract())
    if args.check:
        current = CONTRACT_PATH.read_text(encoding="utf-8") if CONTRACT_PATH.exists() else ""
        if current != text:
            print("aws/contract/contract.json is out of date: run python -m aws.contract.build_contract")
            return 1
        print("contract.json is up to date")
        return 0
    CONTRACT_PATH.write_text(text, encoding="utf-8", newline="\n")
    print(f"wrote {CONTRACT_PATH.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
