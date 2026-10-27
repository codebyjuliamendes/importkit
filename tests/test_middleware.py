from __future__ import annotations

from collections.abc import Callable

import pytest
from fastapi.testclient import TestClient

from importkit.api.middleware import RateLimitMiddleware


def test_security_headers_on_pages_and_api(client: TestClient) -> None:
    for path in ("/", "/api/dashboard", "/api/nope"):
        headers = client.get(path).headers
        csp = headers["content-security-policy"]
        assert "script-src 'self'" in csp
        assert "unsafe-inline" not in csp
        assert "frame-ancestors 'self'" in csp
        assert headers["x-content-type-options"] == "nosniff"
        assert headers["referrer-policy"] == "no-referrer"
        assert headers["x-request-id"]
    assert client.get("/api/dashboard").headers["cache-control"] == "no-store"
    assert "strict-transport-security" not in client.get("/").headers


def test_docs_get_their_own_policy(client: TestClient) -> None:
    assert "cdn.jsdelivr.net" in client.get("/docs").headers["content-security-policy"]


def test_hsts_in_production(make_client: Callable[..., TestClient]) -> None:
    client = make_client(environment="production")
    assert "max-age" in client.get("/").headers["strict-transport-security"]


def test_frame_ancestors_configurable(make_client: Callable[..., TestClient]) -> None:
    client = make_client(frame_ancestors=["https://app.example.com"])
    csp = client.get("/").headers["content-security-policy"]
    assert "frame-ancestors https://app.example.com" in csp


def test_request_id_is_propagated_when_valid(client: TestClient) -> None:
    good = client.get("/api/health", headers={"X-Request-ID": "abc12345-req"})
    assert good.headers["x-request-id"] == "abc12345-req"
    bad = client.get("/api/health", headers={"X-Request-ID": "bad id\x01"})
    assert bad.headers["x-request-id"] != "bad id\x01"


def test_cors_disabled_by_default(client: TestClient) -> None:
    response = client.get("/api/dashboard", headers={"Origin": "https://evil.example"})
    assert "access-control-allow-origin" not in response.headers


def test_cors_allows_configured_origin_only(make_client: Callable[..., TestClient]) -> None:
    client = make_client(cors_origins=["https://app.example.com"])
    ok = client.options(
        "/api/imports/preview",
        headers={
            "Origin": "https://app.example.com",
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "content-type",
        },
    )
    assert ok.headers["access-control-allow-origin"] == "https://app.example.com"
    denied = client.get("/api/dashboard", headers={"Origin": "https://evil.example"})
    assert "access-control-allow-origin" not in denied.headers


def test_rate_limit_applies_to_writes_only(make_client: Callable[..., TestClient]) -> None:
    client = make_client(rate_limit_writes=2, rate_limit_window_seconds=60)
    statuses = [client.post("/api/imports/999/commit").status_code for _ in range(3)]
    assert statuses == [404, 404, 429]
    limited = client.post("/api/imports/999/commit")
    assert limited.json()["code"] == "rate_limited"
    assert int(limited.headers["retry-after"]) >= 1
    assert client.get("/api/dashboard").status_code == 200


def test_rate_limit_bucket_refills_and_prunes() -> None:
    async def app(scope: object, receive: object, send: object) -> None:
        return None

    limiter = RateLimitMiddleware(app, limit=1, window_seconds=10)  # type: ignore[arg-type]
    assert limiter._take("a", 0.0) == 0
    assert limiter._take("a", 1.0) == pytest.approx(9.0)
    assert limiter._take("a", 11.0) == 0
    limiter.MAX_KEYS = 2
    limiter._take("b", 100.0)
    limiter._take("c", 100.0)
    assert set(limiter.buckets) <= {"b", "c"}


def test_body_limit_by_content_length(make_client: Callable[..., TestClient]) -> None:
    client = make_client(max_upload_bytes=1_000)
    response = client.post(
        "/api/imports/preview",
        content=b"x" * 200_000,
        headers={"content-type": "application/json"},
    )
    assert response.status_code == 413
    assert response.json()["code"] == "request_too_large"


def test_body_limit_when_streamed(make_client: Callable[..., TestClient]) -> None:
    client = make_client(max_upload_bytes=1_000)

    def chunks() -> object:
        for _ in range(100):
            yield b"x" * 2_000

    response = client.post(
        "/api/imports/preview", content=chunks(), headers={"content-type": "application/json"}
    )
    assert response.status_code == 413
    assert response.json()["error"].startswith("The request is larger than")


def test_unhandled_errors_return_json(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    from importkit import services

    def boom(*args: object, **kwargs: object) -> None:
        raise RuntimeError("boom")

    monkeypatch.setattr(services, "dashboard", boom)
    response = client.get("/api/dashboard")
    assert response.status_code == 500
    body = response.json()
    assert body["code"] == "internal_error"
    assert body["requestId"] == response.headers["x-request-id"]
    assert "boom" not in response.text
