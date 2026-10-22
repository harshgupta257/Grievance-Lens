"""Routing pipeline: validate -> detect language -> flag injection -> retrieve -> select -> abstain.

Two selection modes behind one interface:
  retrieval_rules  offline and deterministic: the top retrieved department, unless thresholds (tuned on the
                   development split only, src/routing/thresholds.json) call for abstention
  retrieval+llm    an LLM (src.routing.llm) chooses among the retrieved candidates or abstains; its output
                   is re-validated here. If the LLM is unavailable, errors or refuses, the offline decision is
                   used and the method says so; there is no silent switch to another model.

Usage (PowerShell, project root, venv active):
    python -m src.routing.router "gas cylinder book kiya tha, 10 din se delivery nahi hui"
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from src.knowledge.retrieve import Candidate, load_kb, make_retriever
from src.routing import rules
from src.routing.llm import AnthropicSelector, GeminiSelector, Selector
from src.routing.privacy import redact
from src.routing.schemas import CandidateOut, Evidence, RouteResult
from src.routing.settings import RouterSettings, load_router_settings
from src.routing.text import clean, detect_language, injection_flags

THRESHOLDS_PATH = Path(__file__).with_name("thresholds.json")
DEFAULT_THRESHOLDS = {"min_top_score": 0.15, "min_relative_margin": 0.05, "state_override_score": 0.45}


def load_thresholds(method: str, path: Path = THRESHOLDS_PATH) -> dict:
    if path.exists():
        data = json.loads(path.read_text(encoding="utf-8"))
        if method in data:
            return data[method]["thresholds"]
    return dict(DEFAULT_THRESHOLDS)


def _evidence(c: Candidate, n: int = 3) -> list[Evidence]:
    out = []
    for _score, ch in c.evidence[:n]:
        out.append(Evidence(text=ch.text[:300], citation=ch.citation or ch.kind, official=ch.official))
    return out


def decide_offline(
    cands: list[Candidate], state_hits: list[str], n_words: int, t: dict
) -> tuple[str, str | None]:
    """(abstain_reason, department_key). abstain_reason 'none' means route to department_key."""
    top = cands[0].score
    second = cands[1].score if len(cands) > 1 else 0.0
    rel_margin = (top - second) / top if top > 0 else 0.0
    if top < t["min_top_score"]:
        return ("insufficient_information" if n_words < 12 else "low_evidence"), None
    if state_hits and top < t["state_override_score"]:
        return "state_matter", None
    if rel_margin < t["min_relative_margin"]:
        return "ambiguous", None
    return "none", cands[0].ministry_key


class Router:
    def __init__(
        self,
        settings: RouterSettings | None = None,
        selector: Selector | None = None,
        records: list[dict] | None = None,
        thresholds: dict | None = None,
    ):
        self.s = settings or load_router_settings()
        self.records = records or load_kb()
        self.names = {r["ministry_key"]: r["canonical_name"] for r in self.records}
        self.categories = {r["ministry_key"]: r["common_grievance_categories"] for r in self.records}
        self.retriever = make_retriever(self.s.retrieval_method, self.records)
        self.thresholds = thresholds or load_thresholds(self.s.retrieval_method)
        if selector is None and self.s.llm_provider == "anthropic":
            selector = AnthropicSelector(self.s)
        elif selector is None and self.s.llm_provider == "gemini":
            selector = GeminiSelector(self.s)
        self.selector = selector

    def _category(self, key: str, text: str) -> str:
        """Best-matching project-authored category of the chosen department (lexical overlap), or unknown."""
        words = set(text.lower().split())
        best, best_n = "unknown", 0
        for cat in self.categories.get(key, []):
            n = len(words & set(cat.lower().split()))
            if n > best_n:
                best, best_n = cat, n
        return best

    def route(self, text: str, language: str = "auto") -> RouteResult:
        text = clean(text, self.s.min_complaint_chars, self.s.max_complaint_chars)  # raises InputError
        lang = detect_language(text) if language == "auto" else language
        flags = injection_flags(text)
        cands = self.retriever.search(text, self.s.candidates_k)
        cand_out = [
            CandidateOut(
                ministry_key=c.ministry_key,
                name=c.canonical_name,
                retrieval_rank=i,
                retrieval_score=round(c.score, 4),
                evidence=_evidence(c),
            )
            for i, c in enumerate(cands, start=1)
        ]
        state_hits = rules.state_matter_hits(text)
        base = {
            "urgency": rules.urgency(text),
            "missing_information": rules.missing_information(text),
            "possible_state_matter": bool(state_hits),
            "injection_detected": bool(flags),
            "language_detected": lang,
            "candidates": cand_out,
        }
        if self.selector is not None:
            llm_result = self._route_llm(text, cand_out, base)
            if llm_result is not None:
                return llm_result
            base["method"] = "retrieval_rules (LLM unavailable, refused or invalid; offline decision used)"
        return self._route_offline(text, cands, cand_out, state_hits, base)

    def _route_offline(self, text, cands, cand_out, state_hits, base) -> RouteResult:
        reason_code, key = decide_offline(cands, state_hits, len(text.split()), self.thresholds)
        top, second = cands[0].score, (cands[1].score if len(cands) > 1 else 0.0)
        confidence = round(top / (top + second), 3) if top + second > 0 else 0.0
        method = base.pop("method", "retrieval_rules")
        alt = [c for c in cand_out if c.ministry_key != key][:2]
        if key is None:
            names = [c.name for c in cand_out[:2]]
            return RouteResult(
                **base,
                abstain=True,
                abstain_reason=reason_code,
                method=method,
                confidence=confidence,
                alternatives=[c.name for c in cand_out[:3]],
                alternative_keys=[c.ministry_key for c in cand_out[:3]],
                clarifying_question=rules.clarifying_question(reason_code, names),
                reason=_abstain_reason_text(reason_code),
                evidence=cand_out[0].evidence if cand_out else [],
            )
        chosen = cand_out[0]
        support = (
            "official text" if any(e.official for e in chosen.evidence) else "project-written keywords only"
        )
        return RouteResult(
            **base,
            department=chosen.name,
            department_key=key,
            method=method,
            confidence=confidence,
            alternatives=[c.name for c in alt],
            alternative_keys=[c.ministry_key for c in alt],
            category=self._category(key, text),
            reason=(
                f"Your complaint best matches the responsibilities of {chosen.name} in the knowledge base "
                f"(strongest support: {support})."
            ),
            evidence=chosen.evidence,
        )

    def _route_llm(self, text, cand_out, base) -> RouteResult | None:
        redacted, _counts = redact(text)
        cands = [c.model_dump() for c in cand_out]
        sel, meta = self.selector.select(redacted, cands)
        if sel is None:
            return None
        keys = {c.ministry_key for c in cand_out}
        names = {c.ministry_key: c.name for c in cand_out}
        dept = sel.department_key if sel.department_key in keys else None  # re-validate: never trust output
        abstain = sel.abstain or dept is None
        reason_code = sel.abstain_reason if abstain else "none"
        if abstain and reason_code == "none":
            reason_code = "low_evidence"
        evidence = [
            Evidence(
                text=meta.evidence_map[e]["text"],
                citation=meta.evidence_map[e]["citation"],
                official=meta.evidence_map[e]["official"],
            )
            for e in sel.evidence_ids
            if e in meta.evidence_map
        ]
        alts = [k for k in sel.alternative_keys if k in keys and k != dept]
        return RouteResult(
            **{
                **base,
                "urgency": sel.urgency,
                "missing_information": sel.missing_information or base["missing_information"],
                "possible_state_matter": sel.possible_state_matter or base["possible_state_matter"],
            },
            department=None if abstain else names[dept],
            department_key=None if abstain else dept,
            alternatives=[names[k] for k in alts],
            alternative_keys=alts,
            category=sel.category or "unknown",
            confidence=sel.confidence,
            confidence_source="llm_self_report",
            abstain=abstain,
            abstain_reason=reason_code,
            clarifying_question=(
                sel.clarifying_question or rules.clarifying_question(reason_code, [names[k] for k in alts])
            )
            if abstain
            else None,
            reason=sel.reason,
            evidence=evidence,
            method=f"retrieval+llm ({meta.provider}:{meta.model}{', cached' if meta.cache_hit else ''})",
        )


def _abstain_reason_text(code: str) -> str:
    return {
        "insufficient_information": "The complaint does not say enough to identify the department.",
        "low_evidence": "No department in the knowledge base clearly matches this complaint.",
        "state_matter": "This may be handled by a State Government or local office, not a central ministry.",
        "ambiguous": "More than one department could be responsible; a short clarification is needed.",
        "out_of_scope": "This seems to concern a central body outside the departments this tool covers.",
        "llm_refusal": "The model declined to process this request.",
    }.get(code, "")


def main(argv: list[str] | None = None) -> int:
    argv = argv if argv is not None else sys.argv[1:]
    if not argv:
        print('usage: python -m src.routing.router "complaint text"', file=sys.stderr)
        return 2
    result = Router().route(" ".join(argv))
    print(result.model_dump_json(indent=2, exclude={"candidates"}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
