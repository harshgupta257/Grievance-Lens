"""FastAPI application: routing, drafting and verified department statistics, plus the web page.

Run locally (PowerShell, project root, venv active):
    uvicorn src.app.main:app --host 127.0.0.1 --port 8000
then open http://127.0.0.1:8000/

The application never submits complaints. It does not store complaint text, and its logs contain only
method, path, status and timing. Settings are validated at startup (src/routing/settings.py); without any
LLM settings it runs offline.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections import defaultdict, deque
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from src.drafting.drafter import CLARIFICATION_FIELDS, Draft, DraftRequest, build_draft
from src.performance.stats import DepartmentPerformance, department_performance
from src.routing.router import Router
from src.routing.schemas import RouteResult
from src.routing.settings import RouterSettings, load_router_settings
from src.routing.text import InputError

STATIC = Path(__file__).with_name("static")
REQUEST_TIMEOUT_S = 90.0
log = logging.getLogger("cpgrams.app")


class RateLimiter:
    """Sliding-window limit per client for expensive endpoints (in memory; one process)."""

    def __init__(self, limit: int, window_s: float = 60.0):
        self.limit, self.window = limit, window_s
        self.hits: dict[str, deque[float]] = defaultdict(deque)

    def allow(self, key: str, now: float | None = None) -> bool:
        now = time.monotonic() if now is None else now
        q = self.hits[key]
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
) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        s = settings or load_router_settings()  # validated here: a bad .env fails at startup
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
        title="CPGRAMS grievance routing assistant",
        version="0.12.0",
        lifespan=lifespan,
        description="Suggests a department and drafts a complaint. Never submits anything.",
    )

    @app.middleware("http")
    async def safe_logging(request: Request, call_next):
        start = time.perf_counter()
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'"
        )
        log.info(
            "%s %s %d %.0fms",
            request.method,
            request.url.path,
            response.status_code,
            1000 * (time.perf_counter() - start),
        )  # never the body
        return response

    def _limit(request: Request) -> None:
        client = request.client.host if request.client else "unknown"
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
        return {
            "status": "ok",
            "departments": len(request.app.state.names),
            "llm_provider": s.llm_provider,
            "llm_model": s.llm_model if s.llm_provider != "none" else None,
            "retrieval": s.retrieval_method,
            "submits_complaints": False,
        }

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

    @app.get("/", include_in_schema=False)
    def index() -> FileResponse:
        return FileResponse(STATIC / "index.html")

    app.mount("/static", StaticFiles(directory=STATIC), name="static")
    return app


logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
app = create_app()
