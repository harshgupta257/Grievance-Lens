"""API endpoints with FastAPI's test client (offline router, no network, no API key)."""

import logging

import pytest
from fastapi.testclient import TestClient

from src.app.main import RateLimiter, create_app
from src.routing.router import Router
from src.routing.settings import RouterSettings


@pytest.fixture(scope="module")
def client():
    s = RouterSettings()
    app = create_app(settings=s, router=Router(settings=s, selector=None), route_limit_per_min=1000)
    with TestClient(app) as c:
        yield c


def test_health(client):
    r = client.get("/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok" and body["departments"] == 37 and body["submits_complaints"] is False


def test_route_returns_validated_result(client):
    r = client.post("/route", json={"text": "gas cylinder book kiya tha 10 din ho gaye delivery nahi hui"})
    assert r.status_code == 200
    body = r.json()
    assert (
        body["department_key"] == "ministry_of_petroleum_and_natural_gas"
        and "pgportal.gov.in" in body["disclaimer"]
    )
    assert body["confidence_source"] == "retrieval_rule" and body["candidates"]


@pytest.mark.parametrize(
    "payload, status",
    [
        ({"text": "too short"}, 422),  # router length rule -> readable message
        ({"text": ""}, 422),  # schema
        ({"text": "My PF claim is pending for months now.", "language": "fr"}, 422),
        ({}, 422),
    ],
)
def test_route_validation_errors(client, payload, status):
    r = client.post("/route", json=payload)
    assert r.status_code == status


def test_route_too_short_message_is_readable(client):
    r = client.post("/route", json={"text": "help me"})
    assert r.status_code == 422 and "too short" in r.json()["error"]


def test_draft_and_unknown_inputs(client):
    ok = client.post(
        "/draft",
        json={
            "complaint": "My speed post parcel has not been delivered for 12 days.",
            "department_key": "department_of_posts",
            "language": "en",
            "clarifications": {"action_requested": "Deliver the parcel or refund the postage."},
        },
    )
    assert ok.status_code == 200
    d = ok.json()
    assert (
        d["addressed_to"].startswith("To: Department of Posts") and d["factcheck"]["unsupported_tokens"] == []
    )
    assert d["department_statistics"]["available"] is True
    assert (
        client.post("/draft", json={"complaint": "x" * 30, "department_key": "ministry_of_magic"}).status_code
        == 422
    )
    bad = client.post("/draft", json={"complaint": "x" * 30, "clarifications": {"name": "someone"}})
    assert bad.status_code == 422 and "Unknown clarification" in bad.json()["error"]


def test_performance_endpoint(client):
    r = client.get("/ministry/department_of_posts/performance")
    assert r.status_code == 200 and r.json()["latest_reporting_month"] == "2026-09"
    assert client.get("/ministry/not_a_department/performance").status_code == 404


def test_page_and_security_headers(client):
    r = client.get("/")
    assert r.status_code == 200 and "never submits complaints" in r.text
    assert "default-src 'self'" in r.headers["content-security-policy"]
    assert client.get("/static/app.js").status_code == 200


def test_every_site_page_is_served(client):
    for path in [
        "/",
        "/trends",
        "/ministries",
        "/ministry",
        "/scorecard",
        "/lists",
        "/demand",
        "/about",
        "/assistant",
    ]:
        r = client.get(path)
        assert r.status_code == 200 and "/static/site.js" in r.text, path
    assert 'id="complaint"' in client.get("/assistant").text


def test_site_data_endpoints(client):
    ov = client.get("/api/overview").json()
    assert ov["latest"]["month"] == "2026-09" and len(ov["monthly"]) == 57
    ms = client.get("/api/ministries").json()
    assert len(ms) == 92 and {"ministry_key", "name", "score", "in_assistant"} <= set(ms[0])
    detail = client.get("/api/ministries/department_of_posts").json()
    assert detail["performance"]["latest_reporting_month"] == "2026-09"
    assert client.get("/api/ministries/not_a_department").status_code == 404
    for path in ["/api/scorecard", "/api/listings", "/api/demand-backlog"]:
        assert client.get(path).status_code == 200, path


def test_no_endpoint_submits_anything(client):
    paths = {route.path for route in client.app.routes}
    assert not any("submit" in p for p in paths)


def test_logs_never_contain_complaint_text(client, caplog):
    secret = "UNIQUE-COMPLAINT-MARKER-4471 my pension is pending"
    with caplog.at_level(logging.INFO, logger="cpgrams.app"):
        client.post("/route", json={"text": secret})
    assert "POST /route 200" in caplog.text and "UNIQUE-COMPLAINT-MARKER" not in caplog.text


def test_rate_limiter_window():
    rl = RateLimiter(limit=2, window_s=60)
    assert rl.allow("a", 0) and rl.allow("a", 1) and not rl.allow("a", 2)
    assert rl.allow("b", 2) and rl.allow("a", 61.5)


def test_rate_limit_returns_429():
    s = RouterSettings()
    app = create_app(settings=s, router=Router(settings=s, selector=None), route_limit_per_min=1)
    with TestClient(app) as c:
        text = {"text": "My speed post parcel has not been delivered for 12 days."}
        assert c.post("/route", json=text).status_code == 200
        r = c.post("/route", json=text)
        assert r.status_code == 429 and "Too many requests" in r.json()["error"]
