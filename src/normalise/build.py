"""Build the ministry master and the printed-name map from the staging data (D-023, D-024).

Usage (PowerShell, project root, venv active):
    python -m src.normalise.build                                  # rebuild master, name map, review file
    python -m src.normalise.build --apply-review --reviewer "NAME"  # record decisions made in the review file

Inputs:   data/interim/extracted/*.json              printed names, with where and how often they occur
          data/reference/ministry_aliases.csv         alias decisions (accept / reject) with reviewer
          data/reference/ministry_relationships.csv   reorganisations (not aliases)
Outputs:  data/reference/ministries.csv              canonical entities
          data/reference/name_map.csv                every printed name -> entity (or unresolved)
          data/reference/ministry_alias_review.csv   undecided similar pairs, with evidence

Matching order: (1) identical key after formatting-only cleaning; (2) an accepted decision in
ministry_aliases.csv. Similarity of the distinguishing words (RapidFuzz token_sort_ratio) or identical
year-to-date figures in the same report only *propose* pairs for review: neither ever maps a name.
A proposal is dropped automatically when both names are printed in the same table of the same report,
which proves they are different bodies. While a proposal is undecided, the less frequent name stays
unresolved (no entity), so no duplicate entity is created.
"""

from __future__ import annotations

import argparse
import collections
import csv
import json
import sys
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from rapidfuzz import fuzz

from src.config import PROJECT_ROOT
from src.extract.run import STAGING
from src.normalise.names import build_lexicon, clean_name, core, entity_type, name_key, slug

REFERENCE = PROJECT_ROOT / "data" / "reference"
ALIASES_CSV = REFERENCE / "ministry_aliases.csv"
RELATIONSHIPS_CSV = REFERENCE / "ministry_relationships.csv"
MINISTRIES_CSV = REFERENCE / "ministries.csv"
NAME_MAP_CSV = REFERENCE / "name_map.csv"
REVIEW_CSV = REFERENCE / "ministry_alias_review.csv"

# token_sort_ratio of the distinguishing words (names.core) at or above this proposes a pair for review; it
# never accepts one. Calibrated on known pairs: same bodies scored >= 86.7, different bodies <= 75 (D-024).
REVIEW_THRESHOLD = 85.0
ALIAS_COLUMNS = [
    "from_key",
    "to_key",
    "decision",
    "match_method",
    "score",
    "evidence",
    "reviewed_by",
    "reviewed_on",
]
REVIEW_COLUMNS = [
    "from_key",
    "to_key",
    "score",
    "from_rows",
    "to_rows",
    "from_months",
    "to_months",
    "same_report",
    "figures_identical",
    "recommendation",
    "rationale",
    "decision",
]
YTD_FLOWS = ("brought_forward", "receipts", "total_grievances", "disposals", "pending")


@dataclass
class NameStats:
    rows: int = 0
    months: set[str] = field(default_factory=set)
    tables: set[tuple[str, str, str | None]] = field(default_factory=set)  # (month, table_type, group)
    figures: dict[tuple[str, str], dict[str, str]] = field(
        default_factory=dict
    )  # (month, table) -> metric -> value


def collect_names(staging: Path = STAGING) -> dict[str, NameStats]:
    stats: dict[str, NameStats] = collections.defaultdict(NameStats)
    for path in sorted(staging.glob("*.json")):
        month = path.name[:7]
        payload = json.loads(path.read_text(encoding="utf-8"))
        for t in payload["tables"]:
            rows_seen: set[int] = set()
            for o in t["observations"]:
                name = o["entity_name_original"]
                if not name:
                    continue
                s = stats[name]
                if o["row_index"] not in rows_seen:
                    rows_seen.add(o["row_index"])
                    s.rows += 1
                s.months.add(month)
                s.tables.add((month, t["table_type"], t["grai_group"]))
                if (
                    t["table_type"] != "grai_ranking" and o["metric_code"] in YTD_FLOWS
                ):  # comparable YTD figures
                    s.figures.setdefault((month, t["table_type"]), {})[o["metric_code"]] = o["value_numeric"]
    return dict(stats)


class _UnionFind:
    def __init__(self, items):
        self.parent = {i: i for i in items}

    def find(self, x):
        while self.parent[x] != x:
            self.parent[x] = self.parent[self.parent[x]]
            x = self.parent[x]
        return x

    def union(self, a, b):
        self.parent[self.find(a)] = self.find(b)


def read_csv(path: Path, columns: list[str]) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open(encoding="utf-8", newline="") as fh:
        reader = csv.DictReader(fh)
        if reader.fieldnames != columns:
            raise ValueError(f"{path}: expected columns {columns}, found {reader.fieldnames}")
        return list(reader)


def write_csv(path: Path, columns: list[str], rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=columns, lineterminator="\n")
        w.writeheader()
        w.writerows(rows)


@dataclass
class KeyGroup:
    key: str
    names: list[str]
    rows: int
    months: set[str]
    tables: set[tuple[str, str, str | None]]


def _figures_identical(a: list[NameStats], b: list[NameStats]) -> str:
    """'yes' if, in some report, both names carry identical year-to-date figures in comparable tables."""
    fa = {k: v for s in a for k, v in s.figures.items()}
    fb = {k: v for s in b for k, v in s.figures.items()}
    verdicts = []
    for (month, _table_a), va in fa.items():
        for (month_b, _table_b), vb in fb.items():
            if month == month_b:
                common = set(va) & set(vb)
                if len(common) >= 3:
                    verdicts.append(all(va[m] == vb[m] for m in common))
    if not verdicts:
        return "n/a"
    return "yes" if all(verdicts) else "no"


def build(stats: dict[str, NameStats], decisions: list[dict[str, str]]):
    lexicon = build_lexicon({n: s.rows for n, s in stats.items()})
    key_of = {n: name_key(n, lexicon) for n in stats}
    groups: dict[str, KeyGroup] = {}
    for n, k in key_of.items():
        g = groups.setdefault(k, KeyGroup(k, [], 0, set(), set()))
        g.names.append(n)
        g.rows += stats[n].rows
        g.months |= stats[n].months
        g.tables |= stats[n].tables

    accepted = [d for d in decisions if d["decision"] == "accept"]
    decided_pairs = {frozenset((d["from_key"], d["to_key"])) for d in decisions}
    for d in decisions:
        for k in (d["from_key"], d["to_key"]):
            if k not in groups:
                raise ValueError(
                    f"alias decision refers to unknown key {k!r} (keys are printed in name_map.csv)"
                )
    uf = _UnionFind(groups)
    method_of: dict[str, tuple[str, str]] = {}  # key -> (match_method, evidence) when merged by a decision
    for d in accepted:
        uf.union(d["from_key"], d["to_key"])
        method_of[d["from_key"]] = (d["match_method"], d["evidence"])

    # proposals: similar keys in different entities, not yet decided
    keys = sorted(groups)
    proposals, auto_distinct = [], 0
    for i, a in enumerate(keys):
        for b in keys[i + 1 :]:
            if uf.find(a) == uf.find(b) or frozenset((a, b)) in decided_pairs:
                continue
            ga, gb = groups[a], groups[b]
            score = fuzz.token_sort_ratio(core(a), core(b))
            identical = _figures_identical([stats[n] for n in ga.names], [stats[n] for n in gb.names])
            if score < REVIEW_THRESHOLD and identical != "yes":
                continue
            if ga.tables & gb.tables:
                auto_distinct += 1  # printed together in one table: provably different bodies
                continue
            small, big = (ga, gb) if ga.rows <= gb.rows else (gb, ga)
            same_report = sorted(ga.months & gb.months)
            if identical == "yes":
                rec, why = "accept", "identical year-to-date figures under both names in the same report"
            elif same_report:
                rec, why = "judgement", "both names appear in the same report but in different tables"
            else:
                rec, why = "judgement", "never printed in the same report; check whether this is a rename"
            proposals.append(
                {
                    "from_key": small.key,
                    "to_key": big.key,
                    "score": f"{score:.1f}",
                    "from_rows": small.rows,
                    "to_rows": big.rows,
                    "from_months": f"{min(small.months)}..{max(small.months)}",
                    "to_months": f"{min(big.months)}..{max(big.months)}",
                    "same_report": ";".join(same_report[:5]),
                    "figures_identical": identical,
                    "recommendation": rec,
                    "rationale": why,
                    "decision": "",
                }
            )
    unresolved_keys = {p["from_key"] for p in proposals}

    # canonical entities: union-find groups minus unresolved keys
    entities: dict[str, dict] = {}
    entity_of_key: dict[str, str] = {}
    members: dict[str, list[KeyGroup]] = collections.defaultdict(list)
    for k in keys:
        if k not in unresolved_keys:
            members[uf.find(k)].append(groups[k])
    for gs in members.values():
        all_names = [n for g in gs for n in g.names]
        # canonical display name: the cleaned form printed in the most recent report, then the most rows
        best = max(all_names, key=lambda n: (max(stats[n].months), stats[n].rows))
        canonical = clean_name(best, lexicon)
        mkey = slug(canonical)
        months = set().union(*(g.months for g in gs))
        entities[mkey] = {
            "ministry_key": mkey,
            "canonical_name": canonical,
            "entity_type": entity_type(canonical),
            "first_report_month": min(months),
            "last_report_month": max(months),
            "printed_variants": len(all_names),
            "rows": sum(g.rows for g in gs),
        }
        for g in gs:
            entity_of_key[g.key] = mkey

    name_map = []
    for n in sorted(stats, key=str.lower):
        k = key_of[n]
        mkey = entity_of_key.get(k)
        if mkey is None:
            method, evidence = "unresolved", "pending review (see ministry_alias_review.csv)"
        elif k in method_of:
            method, evidence = method_of[k]
        else:
            method, evidence = "exact", "identical after formatting-only cleaning"
        name_map.append(
            {
                "printed_name": n,
                "cleaned_name": clean_name(n, lexicon),
                "name_key": k,
                "ministry_key": mkey or "",
                "match_method": method,
                "evidence": evidence,
                "rows": stats[n].rows,
            }
        )
    return sorted(entities.values(), key=lambda e: e["canonical_name"]), name_map, proposals, auto_distinct


def apply_review(reviewer: str) -> int:
    """Move rows with a decision (accept/reject) from the review file into ministry_aliases.csv."""
    review = read_csv(REVIEW_CSV, REVIEW_COLUMNS)
    decisions = read_csv(ALIASES_CSV, ALIAS_COLUMNS)
    moved = 0
    for r in review:
        decision = r["decision"].strip().lower()
        if not decision:
            continue
        if decision not in ("accept", "reject"):
            raise ValueError(
                f"decision must be 'accept' or 'reject', got {r['decision']!r} for {r['from_key']}"
            )
        method = "approved_alias" if r["figures_identical"] == "yes" else "fuzzy_reviewed"
        decisions.append(
            {
                "from_key": r["from_key"],
                "to_key": r["to_key"],
                "decision": decision,
                "match_method": method,
                "score": r["score"],
                "evidence": f"{r['rationale']}; same report: {r['same_report'] or 'none'}; "
                f"figures identical: {r['figures_identical']}",
                "reviewed_by": reviewer,
                "reviewed_on": date.today().isoformat(),
            }
        )
        moved += 1
    write_csv(ALIASES_CSV, ALIAS_COLUMNS, decisions)
    return moved


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build the ministry master and printed-name map.")
    parser.add_argument("--apply-review", action="store_true", help="record decisions from the review file")
    parser.add_argument("--reviewer", help="who made the decisions (required with --apply-review)")
    args = parser.parse_args(argv)
    if args.apply_review:
        if not args.reviewer:
            parser.error("--apply-review needs --reviewer")
        print(f"Recorded {apply_review(args.reviewer)} decisions in {ALIASES_CSV.relative_to(PROJECT_ROOT)}")
    stats = collect_names()
    if not stats:
        print("No staging data: run  python -m src.extract.run  first.", file=sys.stderr)
        return 1
    entities, name_map, proposals, auto_distinct = build(stats, read_csv(ALIASES_CSV, ALIAS_COLUMNS))
    write_csv(MINISTRIES_CSV, list(entities[0]), entities)
    write_csv(NAME_MAP_CSV, list(name_map[0]), name_map)
    write_csv(REVIEW_CSV, REVIEW_COLUMNS, proposals)
    methods = collections.Counter(m["match_method"] for m in name_map)
    print(f"{len(stats)} printed names -> {len(entities)} entities; name map: {dict(methods)}")
    print(
        f"{len(proposals)} pairs for review; {auto_distinct} similar pairs auto-confirmed distinct "
        f"(printed in the same table)"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
