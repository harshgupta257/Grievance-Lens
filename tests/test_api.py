"""API endpoints with FastAPI's test client (offline router, no network, no API key)."""

import logging

import pytest
from fastapi.testclient import TestClient

from src.app.main import (
    AppSettings,
    RateLimiter,
    check_llm_plan,
    client_address,
    create_app,
    load_app_settings,
)
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
        "/privacy",
        "/terms",
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


def test_client_address_uses_only_trusted_hops():
    assert client_address("10.0.0.1", "1.1.1.1", 0) == "10.0.0.1"  # no proxies: header ignored
    assert client_address("10.0.0.1", "6.6.6.6, 2.2.2.2", 1) == "2.2.2.2"  # forged left entry ignored
    assert client_address("10.0.0.1", "6.6.6.6, 3.3.3.3, 172.16.0.9", 2) == "3.3.3.3"
    assert client_address("10.0.0.1", None, 1) == "10.0.0.1"
    assert client_address(None, None, 0) == "unknown"


def test_edge_header_wins_over_forwarded_for():
    assert client_address("10.0.0.1", "6.6.6.6, 2.2.2.2", 1, "8.8.8.8") == "8.8.8.8"
    assert client_address("10.0.0.1", "6.6.6.6, 2.2.2.2", 1, "") == "2.2.2.2"  # empty header: fall back


def test_client_ip_debug_is_off_by_default_and_echoes_only_when_on(client):
    assert "client_ip_debug" not in client.get("/health").json()
    s = RouterSettings()
    app = create_app(
        settings=s,
        router=Router(settings=s, selector=None),
        app_settings=AppSettings(trusted_proxy_hops=1, client_ip_debug=True),
    )
    with TestClient(app) as c:
        dbg = c.get("/health", headers={"X-Forwarded-For": "9.9.9.9, 3.3.3.3"}).json()["client_ip_debug"]
    assert dbg["x_forwarded_for"] == ["9.9.9.9", "3.3.3.3"] and dbg["resolved"] == "3.3.3.3"


def test_rate_limit_is_per_forwarded_client():
    s = RouterSettings()
    app = create_app(
        settings=s,
        router=Router(settings=s, selector=None),
        route_limit_per_min=1,
        app_settings=AppSettings(trusted_proxy_hops=1),
    )
    text = {"text": "My speed post parcel has not been delivered for 12 days."}
    with TestClient(app) as c:
        assert c.post("/route", json=text, headers={"X-Forwarded-For": "2.2.2.2"}).status_code == 200
        assert c.post("/route", json=text, headers={"X-Forwarded-For": "2.2.2.2"}).status_code == 429
        assert c.post("/route", json=text, headers={"X-Forwarded-For": "9.9.9.9, 3.3.3.3"}).status_code == 200


def test_rate_limiter_memory_is_bounded():
    rl = RateLimiter(limit=5, window_s=60, max_clients=100)
    for i in range(1000):
        rl.allow(f"client-{i}", now=float(i))
    assert len(rl.hits) <= 100


def test_production_hides_docs_and_sends_hsts():
    s = RouterSettings()
    app = create_app(
        settings=s, router=Router(settings=s, selector=None), app_settings=AppSettings(production=True)
    )
    with TestClient(app) as c:
        assert c.get("/docs").status_code == 404 and c.get("/openapi.json").status_code == 404
        r = c.get("/")
        assert r.status_code == 200 and "max-age" in r.headers["strict-transport-security"]


def test_development_keeps_docs(client):
    assert client.get("/openapi.json").status_code == 200
    assert "strict-transport-security" not in client.get("/").headers


def test_app_settings_reject_bad_values(monkeypatch):
    monkeypatch.setenv("APP_ENV", "prod")
    with pytest.raises(ValueError, match="APP_ENV"):
        load_app_settings()
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("TRUSTED_PROXY_HOPS", "-1")
    with pytest.raises(ValueError, match="TRUSTED_PROXY_HOPS"):
        load_app_settings()
    monkeypatch.setenv("TRUSTED_PROXY_HOPS", "1")
    assert load_app_settings() == AppSettings(production=True, trusted_proxy_hops=1)
    monkeypatch.setenv("TRUSTED_CLIENT_IP_HEADER", "bad header!")
    with pytest.raises(ValueError, match="TRUSTED_CLIENT_IP_HEADER"):
        load_app_settings()
    monkeypatch.setenv("TRUSTED_CLIENT_IP_HEADER", "CF-Connecting-IP")
    assert load_app_settings().client_ip_header == "cf-connecting-ip"


def test_production_refuses_gemini_free_tier(monkeypatch):
    s = RouterSettings(llm_provider="gemini")
    monkeypatch.delenv("GEMINI_PAID_TIER", raising=False)
    with pytest.raises(RuntimeError, match="GEMINI_PAID_TIER"):
        check_llm_plan(s, AppSettings(production=True))
    check_llm_plan(s, AppSettings(production=False))  # local testing on the free tier is allowed
    monkeypatch.setenv("GEMINI_PAID_TIER", "true")
    check_llm_plan(s, AppSettings(production=True))


def test_theme_script_loads_before_paint_on_every_page(client):
    assert client.get("/static/theme.js").status_code == 200
    for path in ["/", "/trends", "/privacy", "/terms", "/assistant"]:
        head = client.get(path).text.split("</head>")[0]
        assert '<script src="/static/theme.js"></script>' in head, path


def test_privacy_page_and_unofficial_name(client):
    r = client.get("/privacy")
    assert r.status_code == 200 and 'id="ai-status"' in r.text and "not store" in r.text
    for path in ["/", "/assistant", "/about"]:
        text = client.get(path).text
        assert "not a government website" in text and "/privacy" in text, path
