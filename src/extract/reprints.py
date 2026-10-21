"""Flag tables that repeat the previous report's table value for value (stale reprints). Idempotent.

Found in Phase 3: report #41 (Sep 2025) prints the average-closing-time annexures of #40 (Aug 2025)
unchanged, while its heading claims data to 30 Sep 2025 (20 of 20 values identical). Such a table describes
the earlier period, so its observations are downgraded to `warning` and the table gets an
`identical_to_previous_report` issue. Values are kept: the flag makes the problem visible, it does not
delete published data.

Runs automatically at the end of `python -m src.extract.run`; standalone: `python -m src.extract.reprints`.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from src.extract.run import STAGING

ISSUE_TYPE = "identical_to_previous_report"


def _signature(table: dict) -> tuple:
    return tuple(
        sorted(
            (o["entity_name_original"] or "", o["metric_code"], o["value_original"])
            for o in table["observations"]
        )
    )


def flag_reprints(staging: Path = STAGING) -> list[tuple[str, str]]:
    """Mark reprinted tables in the staging files; returns [(report key, table)] flagged."""
    files = sorted(p for p in staging.glob("*_en.json"))
    flagged: list[tuple[str, str]] = []
    previous: dict[tuple[str, str | None], tuple] = {}
    previous_key = None
    for path in files:
        payload = json.loads(path.read_text(encoding="utf-8"))
        current: dict[tuple[str, str | None], tuple] = {}
        changed = False
        for t in payload["tables"]:
            ident = (t["table_type"], t["grai_group"])
            sig = _signature(t)
            current[ident] = sig
            if not sig or previous.get(ident) != sig:
                continue
            flagged.append((path.stem, t["table_type"] + (f":{t['grai_group']}" if t["grai_group"] else "")))
            for o in t["observations"]:
                if o["validation_status"] in ("valid", "unvalidated"):
                    o["validation_status"] = "warning"
                    changed = True
            if any(i["issue_type"] == ISSUE_TYPE for i in t["issues"]):
                continue  # already flagged by an earlier run
            t["issues"].append(
                {
                    "severity": "warning",
                    "issue_type": ISSUE_TYPE,
                    "pdf_page": t["pdf_page_start"],
                    "raw_text": None,
                    "status": "open",
                    "resolution_note": None,
                    "description": f"every value equals the same table in {previous_key}: probably a stale "
                    "reprint; the observations describe that report's period, not this one",
                }
            )
            changed = True
        if changed:
            path.write_text(json.dumps(payload, indent=1, ensure_ascii=False), encoding="utf-8")
        previous, previous_key = current, path.stem
    return flagged


def main() -> int:
    flagged = flag_reprints()
    for key, table in flagged:
        print(f"reprint: {key} {table}")
    print(f"{len(flagged)} reprinted tables flagged")
    return 0


if __name__ == "__main__":
    sys.exit(main())
