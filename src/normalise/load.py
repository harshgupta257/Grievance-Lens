"""Load the ministry master, aliases and relationships into PostgreSQL and link observations. Safe to re-run.

Usage (PowerShell, project root, venv active, after `python -m src.extract.load`):
    python -m src.normalise.load

In one transaction: ministries are upserted by `ministry_key`; aliases are replaced from name_map.csv;
relationships are replaced from ministry_relationships.csv; every observation's `ministry_id` is set from its
printed name (aggregate rows keep NULL). The command fails if any printed ministry name in the database has
no alias, so an unmapped name can never be silently ignored.
"""

from __future__ import annotations

import csv
import sys

from sqlalchemy import Engine, text

from src.config import ConfigError, load_settings
from src.db import get_engine, pending_migrations
from src.normalise.build import (
    ALIAS_COLUMNS,
    ALIASES_CSV,
    MINISTRIES_CSV,
    NAME_MAP_CSV,
    RELATIONSHIPS_CSV,
    read_csv,
)


def _decisions_by_key() -> dict[str, dict[str, str]]:
    return {d["from_key"]: d for d in read_csv(ALIASES_CSV, ALIAS_COLUMNS) if d["decision"] == "accept"}


def load(engine: Engine) -> dict[str, int]:
    with MINISTRIES_CSV.open(encoding="utf-8") as fh:
        ministries = list(csv.DictReader(fh))
    with NAME_MAP_CSV.open(encoding="utf-8") as fh:
        name_map = list(csv.DictReader(fh))
    with RELATIONSHIPS_CSV.open(encoding="utf-8") as fh:
        relationships = list(csv.DictReader(fh))
    decisions = _decisions_by_key()
    unresolved = [m["printed_name"] for m in name_map if not m["ministry_key"]]
    if unresolved:
        raise SystemExit(
            f"{len(unresolved)} printed names are unresolved; decide them first: {unresolved[:5]}"
        )

    with engine.begin() as conn:
        ids: dict[str, int] = {}
        for m in ministries:
            ids[m["ministry_key"]] = conn.execute(
                text("""
                    INSERT INTO ministries (ministry_key, canonical_name, entity_type, first_report_month,
                                            last_report_month)
                    VALUES (:k, :name, :type, :first, :last)
                    ON CONFLICT (ministry_key) DO UPDATE SET canonical_name = EXCLUDED.canonical_name,
                        entity_type = EXCLUDED.entity_type, first_report_month = EXCLUDED.first_report_month,
                        last_report_month = EXCLUDED.last_report_month
                    RETURNING ministry_id"""),
                {
                    "k": m["ministry_key"],
                    "name": m["canonical_name"],
                    "type": m["entity_type"],
                    "first": m["first_report_month"] + "-01",
                    "last": m["last_report_month"] + "-01",
                },
            ).scalar_one()
        conn.execute(text("UPDATE observations SET ministry_id = NULL WHERE ministry_id IS NOT NULL"))
        conn.execute(text("DELETE FROM ministry_aliases"))
        conn.execute(text("DELETE FROM ministry_relationships"))
        # entities no longer produced by the build (e.g. after a new merge) are removed
        conn.execute(
            text("DELETE FROM ministries WHERE NOT (ministry_key = ANY(:keys))"), {"keys": list(ids)}
        )
        for m in name_map:
            d = decisions.get(m["name_key"])
            conn.execute(
                text("""
                    INSERT INTO ministry_aliases (ministry_id, alias_original, alias_normalised, match_method,
                                                  match_score, reviewed_by, reviewed_on, evidence)
                    VALUES (:mid, :orig, :norm, :method, :score, :by, :on, :evidence)"""),
                {
                    "mid": ids[m["ministry_key"]],
                    "orig": m["printed_name"],
                    "norm": m["name_key"],
                    "method": m["match_method"],
                    "score": d["score"] if d else None,
                    "by": d["reviewed_by"] if d else None,
                    "on": d["reviewed_on"] if d else None,
                    "evidence": m["evidence"],
                },
            )
        for r in relationships:
            conn.execute(
                text("""
                    INSERT INTO ministry_relationships (from_ministry_id, to_ministry_id, relation,
                                                        effective_month, evidence)
                    VALUES (:a, :b, :rel, :month, :evidence)"""),
                {
                    "a": ids[r["from_ministry_key"]],
                    "b": ids[r["to_ministry_key"]],
                    "rel": r["relation"],
                    "month": r["effective_month"] + "-01",
                    "evidence": r["evidence"],
                },
            )
        linked = conn.execute(
            text("""
                UPDATE observations o SET ministry_id = a.ministry_id
                FROM ministry_aliases a WHERE o.entity_name_original = a.alias_original""")
        ).rowcount
        missing = (
            conn.execute(
                text("""SELECT DISTINCT entity_name_original FROM observations
                    WHERE NOT is_aggregate AND ministry_id IS NULL""")
            )
            .scalars()
            .all()
        )
        if missing:
            raise SystemExit(
                f"printed names in the database without an alias: {missing[:5]} (rebuild the name map)"
            )
    return {
        "ministries": len(ids),
        "aliases": len(name_map),
        "relationships": len(relationships),
        "linked": linked,
    }


def main() -> int:
    try:
        settings = load_settings()
    except ConfigError as exc:
        print(f"[config] {exc}", file=sys.stderr)
        return 1
    engine = get_engine(settings.database_url)
    try:
        if pending_migrations(engine):
            print("Pending migrations: run  python -m src.check_db --migrate  first.", file=sys.stderr)
            return 1
        counts = load(engine)
    finally:
        engine.dispose()
    print(
        f"Loaded {counts['ministries']} ministries, {counts['aliases']} aliases, {counts['relationships']} "
        f"relationships; linked {counts['linked']} observations"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
