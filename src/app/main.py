"""FastAPI application: routing, drafting and verified department statistics, plus the web page.

Run locally (PowerShell, project root, venv active):
    uvicorn src.app.main:app --host 127.0.0.1 --port 8000
then open http://127.0.0.1:8000/

Deployment settings come from environment variables (docs/deploy.md):
    APP_ENV=production       hides the API docs (/docs, /openapi.json) and sends HSTS
    TRUSTED_PROXY_HOPS=N     number of reverse proxies in front of the app; the client address used for rate
                             limiting is then read from X-Forwarded-For (0, the default, ignores that header)
    TRUSTED_CLIENT_IP_HEADER a header set by the edge proxy that carries the visitor's address and cannot be
                             forged by the visitor (e.g. cf-connecting-ip); takes precedence over the hops
    CLIENT_IP_DEBUG=true     temporary: /health echoes the caller's own address headers, to choose the two
                             settings above on a new host (docs/deploy.md). Never leave it on.

The application never submits complaints. It does not store complaint text, and its logs contain only
method, path, status and timing. Settings are validated at startup (src/routing/settings.py); without any
LLM settings it runs offline.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import time
from collections import deque
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from src.app import site_data
from src.drafting.drafter import CLARIFICATION_FIELDS, Draft, DraftRequest, build_draft
from src.performance.stats import DepartmentPerformance, department_performance
from src.routing.router import Router
from src.routing.schemas import RouteResult
from src.routing.settings import RouterSettings, load_router_settings
from src.routing.text import InputError

STATIC = Path(__file__).with_name("static")
PAGES = {
    "/": "index.html",
    "/trends": "trends.html",
    "/ministries": "ministries.html",
    "/ministry": "ministry.html",
    "/scorecard": "scorecard.html",
    "/lists": "lists.html",
    "/demand": "demand.html",
    "/assistant": "assistant.html",
    "/about": "about.html",
    "/privacy": "privacy.html",
    "/terms": "terms.html",
}


def _page(name: str):
    def serve() -> FileResponse:
        return FileResponse(STATIC / name)

    return serve


REQUEST_TIMEOUT_S = 90.0
log = logging.getLogger("cpgrams.app")


@dataclass(frozen=True)
class AppSettings:
    production: bool = False
    trusted_proxy_hops: int = 0
    client_ip_header: str = ""  # lower-case header name; empty = not used
    client_ip_debug: bool = False


def load_app_settings() -> AppSettings:
    env = os.environ.get("APP_ENV", "development").strip().lower()
    if env not in ("development", "production"):
        raise ValueError(f"APP_ENV must be 'development' or 'production', not {env!r}")
    hops = os.environ.get("TRUSTED_PROXY_HOPS", "0").strip()
    if not hops.isdigit() or int(hops) > 5:
        raise ValueError(f"TRUSTED_PROXY_HOPS must be a whole number from 0 to 5, not {hops!r}")
    header = os.environ.get("TRUSTED_CLIENT_IP_HEADER", "").strip().lower()
    if header and not re.fullmatch(r"[a-z0-9-]{1,64}", header):
        raise ValueError(f"TRUSTED_CLIENT_IP_HEADER must be a header name, not {header!r}")
    debug = os.environ.get("CLIENT_IP_DEBUG", "").strip().lower() == "true"
    return AppSettings(
        production=env == "production",
        trusted_proxy_hops=int(hops),
        client_ip_header=header,
        client_ip_debug=debug,
    )


def check_llm_plan(s: RouterSettings, conf: AppSettings) -> None:
    """Refuse to start a public site that would send visitors' complaints to Gemini's free tier.

    Google's terms let it use free-tier prompts to improve its products (with human review) and ask users not
    to submit personal information there; the privacy page promises paid plans only (DESIGN.md D-050).
    """
    if conf.production and s.llm_provider == "gemini" and os.environ.get("GEMINI_PAID_TIER", "") != "true":
        raise RuntimeError(
            "LLM_PROVIDER=gemini in production needs a billing-enabled (paid) Gemini API project. "
            "Set GEMINI_PAID_TIER=true once billing is on, or unset LLM_PROVIDER to run offline."
        )


def client_address(
    peer: str | None, forwarded_for: str | None, hops: int, edge_value: str | None = None
) -> str:
    """The client address for rate limiting.

    `edge_value` is the value of TRUSTED_CLIENT_IP_HEADER, which the edge proxy overwrites, so it wins when
    present. Otherwise each trusted proxy appends the address it received the request from to
    X-Forwarded-For, so with N proxies the real client is the N-th entry from the right. Entries further left
    are set by the client and can be forged, so they are never used. With no trusted proxies the header is
    ignored.
    """
    if edge_value and edge_value.strip():
        return edge_value.split(",")[0].strip()
    if hops and forwarded_for:
        parts = [p.strip() for p in forwarded_for.split(",") if p.strip()]
        if len(parts) >= hops:
            return parts[-hops]
    return peer or "unknown"


class RateLimiter:
    """Sliding-window limit per client for expensive endpoints (in memory; one process).

    Memory is bounded: once more than `max_clients` addresses are tracked, idle ones are dropped.
    """

    def __init__(self, limit: int, window_s: float = 60.0, max_clients: int = 10_000):
        self.limit, self.window, self.max_clients = limit, window_s, max_clients
        self.hits: dict[str, deque[float]] = {}

    def _prune(self, now: float) -> None:
        for key in [k for k, q in self.hits.items() if not q or now - q[-1] > self.window]:
            del self.hits[key]
        while len(self.hits) >= self.max_clients:  # all active: drop the oldest-tracked client
            del self.hits[next(iter(self.hits))]

    def allow(self, key: str, now: float | None = None) -> bool:
        now = time.monotonic() if now is None else now
        if key not in self.hits and len(self.hits) >= self.max_clients:
            self._prune(now)
        q = self.hits.setdefault(key, deque())
        while q and now - q[0] > self.window:
            q.popleft()
        if len(q) >= self.limit:
            return False
        q.append(now)
        return True


class RouteIn(BaseModel):
    text: str = Field(..., min_length=1, max_length=20000, description="complaint text (untrusted)")
    language: Literal["auto", "en", "hi", "hinglish"] = "auto"


class DraftIn(BaseModel):
    complaint: str = Field(..., min_length=1, max_length=20000)
    language: Literal["en", "hi"] = "en"
    department_key: str | None = None
    category: str | None = Field(None, max_length=80)
    clarifications: dict[str, str] = Field(default_factory=dict)
    supporting_details: str | None = Field(None, max_length=1000)


def create_app(
    settings: RouterSettings | None = None,
    router: Router | None = None,
    route_limit_per_min: int | None = None,
    app_settings: AppSettings | None = None,
) -> FastAPI:
    conf = app_settings or load_app_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        s = settings or load_router_settings()  # validated here: a bad .env fails at startup
        check_llm_plan(s, conf)
        app.state.settings = s
        app.state.router = router or Router(settings=s)
        app.state.names = app.state.router.names
        limit = route_limit_per_min or (10 if s.llm_provider != "none" else 60)
        app.state.limiter = RateLimiter(limit)
        log.info(
            "started: %d departments, llm_provider=%s, retrieval=%s",
            len(app.state.names),
            s.llm_provider,
            s.retrieval_method,
        )
        yield

    app = FastAPI(
        title="Grievance Lens (unofficial): CPGRAMS data and complaint assistant",
        version="0.12.0",
        lifespan=lifespan,
        description="Suggests a department and drafts a complaint. Never submits anything.",
        docs_url=None if conf.production else "/docs",
        redoc_url=None if conf.production else "/redoc",
        openapi_url=None if conf.production else "/openapi.json",
    )

    @app.middleware("http")
    async def safe_logging(request: Request, call_next):
        start = time.perf_counter()
        try:
            response = await call_next(request)
        except Exception as exc:  # log the error type and path only, never the request body
            log.error("%s %s 500 unhandled %s", request.method, request.url.path, type(exc).__name__)
            response = JSONResponse({"error": "Something went wrong. Please try again."}, status_code=500)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'"
        )
        if conf.production:  # the host terminates HTTPS; tell browsers never to fall back to HTTP
            response.headers["Strict-Transport-Security"] = "max-age=31536000"
        log.info(
            "%s %s %d %.0fms",
            request.method,
            request.url.path,
            response.status_code,
            1000 * (time.perf_counter() - start),
        )  # never the body
        return response

    def _limit(request: Request) -> None:
        client = client_address(
            request.client.host if request.client else None,
            request.headers.get("x-forwarded-for"),
            conf.trusted_proxy_hops,
            request.headers.get(conf.client_ip_header) if conf.client_ip_header else None,
        )
        if not request.app.state.limiter.allow(client):
            raise HTTPException(429, "Too many requests. Please wait a minute and try again.")

    async def _run(fn, *args):
        try:
            return await asyncio.wait_for(run_in_threadpool(fn, *args), timeout=REQUEST_TIMEOUT_S)
        except TimeoutError as exc:
            raise HTTPException(504, "The request took too long. Please try again.") from exc
        except InputError as exc:
            raise HTTPException(422, str(exc)) from exc

    @app.get("/health")
    def health(request: Request) -> dict:
        s = request.app.state.settings
        body = {
            "status": "ok",
            "departments": len(request.app.state.names),
            "llm_provider": s.llm_provider,
            "llm_model": s.llm_model if s.llm_provider != "none" else None,
            "retrieval": s.retrieval_method,
            "submits_complaints": False,
        }
        if conf.client_ip_debug:  # the caller's own request headers only; nothing is logged or stored
            hd = request.headers
            body["client_ip_debug"] = {
                "peer": request.client.host if request.client else None,
                "x_forwarded_for": [p.strip() for p in hd.get("x-forwarded-for", "").split(",") if p.strip()],
                "cf_connecting_ip": hd.get("cf-connecting-ip"),
                "true_client_ip": hd.get("true-client-ip"),
                "x_real_ip": hd.get("x-real-ip"),
                "resolved": client_address(
                    request.client.host if request.client else None,
                    hd.get("x-forwarded-for"),
                    conf.trusted_proxy_hops,
                    hd.get(conf.client_ip_header) if conf.client_ip_header else None,
                ),
            }
        return body

    @app.post("/route", response_model=RouteResult)
    async def route(body: RouteIn, request: Request) -> RouteResult:
        _limit(request)
        return await _run(request.app.state.router.route, body.text, body.language)

    @app.post("/draft", response_model=Draft)
    async def draft(body: DraftIn, request: Request) -> Draft:
        _limit(request)
        names = request.app.state.names
        if body.department_key is not None and body.department_key not in names:
            raise HTTPException(422, "Unknown department. Choose one of the suggested departments.")
        unknown = set(body.clarifications) - set(CLARIFICATION_FIELDS)
        if unknown:
            raise HTTPException(422, f"Unknown clarification field(s): {', '.join(sorted(unknown))}")
        req = DraftRequest(
            complaint=body.complaint,
            language=body.language,
            department_key=body.department_key,
            department_name=names.get(body.department_key),
            category=body.category,
            clarifications=body.clarifications,
            supporting_details=body.supporting_details,
        )
        return await _run(build_draft, req)

    @app.get("/ministry/{ministry_key}/performance", response_model=DepartmentPerformance)
    def performance(ministry_key: str, request: Request) -> DepartmentPerformance:
        if ministry_key not in request.app.state.names:
            raise HTTPException(404, "Unknown department.")
        return department_performance(ministry_key)

    @app.get("/departments")
    def departments(request: Request) -> list[dict]:
        return [
            {"ministry_key": k, "name": v}
            for k, v in sorted(request.app.state.names.items(), key=lambda x: x[1])
        ]

    @app.exception_handler(HTTPException)
    async def http_error(_request: Request, exc: HTTPException):
        return JSONResponse({"error": exc.detail}, status_code=exc.status_code)

    # --- analytics data for the website (read-only, from the validated exports) ---
    @app.get("/api/overview")
    def api_overview() -> dict:
        return site_data.overview()

    @app.get("/api/ministries")
    def api_ministries() -> list[dict]:
        return site_data.ministries()

    @app.get("/api/ministries/{ministry_key}")
    def api_ministry(ministry_key: str) -> dict:
        detail = site_data.ministry_detail(ministry_key)
        if detail is None:
            raise HTTPException(404, "Unknown ministry or department.")
        return detail

    @app.get("/api/scorecard")
    def api_scorecard() -> dict:
        return site_data.scorecard()

    @app.get("/api/listings")
    def api_listings() -> dict:
        return site_data.listings()

    @app.get("/api/demand-backlog")
    def api_demand_backlog() -> dict:
        return site_data.demand_backlog()

    # --- website pages ---
    for path, page in PAGES.items():
        app.add_api_route(path, _page(page), methods=["GET"], include_in_schema=False)

    app.mount("/static", StaticFiles(directory=STATIC), name="static")
    return app


logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
app = create_app()
