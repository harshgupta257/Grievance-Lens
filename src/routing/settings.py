"""Router and LLM settings, from environment variables (and `.env`), validated at startup.

Nothing here is required: with no settings the router runs offline (retrieval + rules, no LLM, no API key).
The LLM is used only when LLM_PROVIDER=anthropic and credentials are available (ANTHROPIC_API_KEY or an
`ant auth login` profile, resolved by the Anthropic SDK). There is no silent fallback to another model:
the configured model is the only one called, apart from the API's own refusal fallback when enabled.
"""

from __future__ import annotations

import os
from typing import Literal

from dotenv import load_dotenv
from pydantic import BaseModel, Field

from src.config import PROJECT_ROOT


class RouterSettings(BaseModel):
    llm_provider: Literal["none", "anthropic"] = "none"
    llm_model: str = "claude-opus-5-5"
    llm_effort: Literal["low", "medium", "high", "xhigh", "max"] = "low"  # a short classification task
    llm_max_tokens: int = Field(4096, ge=256, le=32000)
    llm_timeout_s: float = Field(60.0, gt=0, le=600)
    llm_max_retries: int = Field(2, ge=0, le=5)  # the SDK retries 408/409/429/5xx with backoff
    llm_refusal_fallback: bool = True  # server-side `fallbacks: "default"` on a policy refusal
    llm_max_calls_per_process: int = Field(500, ge=0)  # hard cap on paid calls per process (cost control)
    llm_input_price_per_mtok: float = 4.0  # USD, for the cost estimate in the usage log (claude-opus-5-5)
    llm_output_price_per_mtok: float = 20.0
    retrieval_method: Literal["lexical", "hybrid"] = "lexical"
    candidates_k: int = Field(5, ge=2, le=10)
    max_complaint_chars: int = Field(4000, ge=200, le=20000)
    min_complaint_chars: int = Field(15, ge=1)


_ENV = {
    "llm_provider": "LLM_PROVIDER",
    "llm_model": "LLM_MODEL",
    "llm_effort": "LLM_EFFORT",
    "llm_max_tokens": "LLM_MAX_TOKENS",
    "llm_timeout_s": "LLM_TIMEOUT_S",
    "llm_max_retries": "LLM_MAX_RETRIES",
    "llm_refusal_fallback": "LLM_REFUSAL_FALLBACK",
    "llm_max_calls_per_process": "LLM_MAX_CALLS_PER_PROCESS",
    "llm_input_price_per_mtok": "LLM_INPUT_PRICE_PER_MTOK",
    "llm_output_price_per_mtok": "LLM_OUTPUT_PRICE_PER_MTOK",
    "retrieval_method": "ROUTER_RETRIEVAL",
    "candidates_k": "ROUTER_CANDIDATES_K",
    "max_complaint_chars": "ROUTER_MAX_CHARS",
}


def load_router_settings(env: dict[str, str] | None = None) -> RouterSettings:
    if env is None:
        load_dotenv(PROJECT_ROOT / ".env", override=False)
        env = dict(os.environ)
    values = {field: env[var] for field, var in _ENV.items() if env.get(var, "") != ""}
    return RouterSettings(**values)
