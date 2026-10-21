"""Pydantic schemas for routing requests and results (validated at every boundary)."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

Urgency = Literal["high", "medium", "low", "unknown"]
AbstainReason = Literal[
    "none",
    "low_evidence",
    "ambiguous",
    "state_matter",
    "out_of_scope",
    "insufficient_information",
    "llm_refusal",
]
DISCLAIMER = (
    "This is a suggestion, not an official decision. This tool never submits complaints. Review the "
    "suggestion and lodge your grievance yourself on the official CPGRAMS portal: https://pgportal.gov.in/"
)


class Evidence(BaseModel):
    text: str
    citation: str
    official: bool  # True: names or Allocation of Business Rules text. False: project-authored keywords etc.


class CandidateOut(BaseModel):
    ministry_key: str
    name: str
    retrieval_rank: int
    retrieval_score: float
    evidence: list[Evidence]


class RouteRequest(BaseModel):
    text: str = Field(..., description="complaint text (untrusted input)")
    language: Literal["auto", "en", "hi", "hinglish"] = "auto"


class RouteResult(BaseModel):
    department: str | None = Field(None, description="canonical department name, or null when abstaining")
    department_key: str | None = None
    alternatives: list[str] = Field(default_factory=list, description="other plausible department names")
    alternative_keys: list[str] = Field(default_factory=list)
    category: str = "unknown"
    urgency: Urgency = "unknown"
    missing_information: list[str] = Field(default_factory=list)
    confidence: float = Field(0.0, ge=0, le=1)
    confidence_source: Literal["llm_self_report", "retrieval_rule", "classifier_probability"] = (
        "retrieval_rule"
    )
    confidence_note: str = "Not a calibrated probability. See docs/routing.md section 4."
    abstain: bool = False
    abstain_reason: AbstainReason = "none"
    clarifying_question: str | None = None
    reason: str = ""
    evidence: list[Evidence] = Field(default_factory=list)
    possible_state_matter: bool = False
    injection_detected: bool = False
    language_detected: str = "en"
    method: str = "retrieval_rules"
    candidates: list[CandidateOut] = Field(default_factory=list)
    disclaimer: str = DISCLAIMER


class LLMSelection(BaseModel):
    """What the LLM must return. `department_key` is constrained to the retrieved candidates (or null)."""

    department_key: str | None
    alternative_keys: list[str] = Field(max_length=3)
    category: str = Field(max_length=80)
    urgency: Urgency
    missing_information: list[str] = Field(max_length=5)
    confidence: float = Field(ge=0, le=1)
    abstain: bool
    abstain_reason: AbstainReason
    possible_state_matter: bool
    clarifying_question: str | None = Field(None, max_length=300)
    reason: str = Field(max_length=600)
    evidence_ids: list[str] = Field(max_length=5)


def llm_json_schema(candidate_keys: list[str], evidence_ids: list[str]) -> dict:
    """JSON schema for structured output; enums restrict departments and evidence to what was retrieved."""
    nullable_key = {"anyOf": [{"type": "string", "enum": candidate_keys}, {"type": "null"}]}
    return {
        "type": "object",
        "properties": {
            "department_key": nullable_key,
            "alternative_keys": {"type": "array", "items": {"type": "string", "enum": candidate_keys}},
            "category": {"type": "string"},
            "urgency": {"type": "string", "enum": ["high", "medium", "low", "unknown"]},
            "missing_information": {"type": "array", "items": {"type": "string"}},
            "confidence": {"type": "number"},
            "abstain": {"type": "boolean"},
            "abstain_reason": {"type": "string", "enum": list(AbstainReason.__args__)},
            "possible_state_matter": {"type": "boolean"},
            "clarifying_question": {"anyOf": [{"type": "string"}, {"type": "null"}]},
            "reason": {"type": "string"},
            "evidence_ids": {"type": "array", "items": {"type": "string", "enum": evidence_ids}},
        },
        "required": [
            "department_key",
            "alternative_keys",
            "category",
            "urgency",
            "missing_information",
            "confidence",
            "abstain",
            "abstain_reason",
            "possible_state_matter",
            "clarifying_question",
            "reason",
            "evidence_ids",
        ],
        "additionalProperties": False,
    }
