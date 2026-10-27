"""Pure-ASGI middleware: request context, security headers, body limit, rate limit."""

from __future__ import annotations

import json
import math
import re
import time
import uuid
from collections.abc import Iterable
from dataclasses import dataclass

import structlog
from starlette.datastructures import MutableHeaders
from starlette.exceptions import HTTPException
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from importkit.logs import get_logger

log = get_logger("importkit.http")

_REQUEST_ID = re.compile(r"^[A-Za-z0-9._-]{8,128}$")
WRITE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})
DOCS_PATHS = ("/docs", "/redoc")


async def send_json(
    send: Send,
    status: int,
    payload: dict[str, object],
    headers: Iterable[tuple[str, str]] = (),
) -> None:
    body = json.dumps(payload).encode()
    raw = [
        (b"content-type", b"application/json"),
        (b"content-length", str(len(body)).encode()),
        *((k.encode("latin-1"), v.encode("latin-1")) for k, v in headers),
    ]
    await send({"type": "http.response.start", "status": status, "headers": raw})
    await send({"type": "http.response.body", "body": body})


def build_csp(frame_ancestors: Iterable[str]) -> str:
    ancestors = " ".join(frame_ancestors)
    return "; ".join(
        (
            "default-src 'self'",
            "script-src 'self'",
            "style-src 'self'",
            "font-src 'self'",
            "img-src 'self' data:",
            "connect-src 'self'",
            "object-src 'none'",
            "base-uri 'none'",
            "form-action 'self'",
            f"frame-ancestors {ancestors}",
        )
    )


# Swagger UI / ReDoc ship inline bootstrap scripts and CDN assets.
DOCS_CSP = (
    "default-src 'self'; script-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; "
    "style-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net https://fonts.googleapis.com; "
    "font-src 'self' https://fonts.gstatic.com; img-src 'self' data: https://fastapi.tiangolo.com "
    "https://cdn.redoc.ly; worker-src blob:; object-src 'none'; base-uri 'none'; "
    "frame-ancestors 'none'"
)


class EdgeMiddleware:
    """Request id, access log, security headers and a JSON 500 safety net."""

    def __init__(self, app: ASGIApp, *, frame_ancestors: Iterable[str], hsts: bool) -> None:
        self.app = app
        self.csp = build_csp(frame_ancestors)
        self.hsts = hsts

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        incoming = dict(scope.get("headers", [])).get(b"x-request-id", b"").decode("latin-1")
        request_id = incoming if _REQUEST_ID.match(incoming) else uuid.uuid4().hex
        scope.setdefault("state", {})["request_id"] = request_id
        path: str = scope.get("path", "")
        started = time.perf_counter()
        status_holder = {"status": 500, "started": False}
        structlog.contextvars.bind_contextvars(request_id=request_id)

        security = self._headers(path, request_id)

        async def send_wrapper(message: Message) -> None:
            if message["type"] == "http.response.start":
                status_holder["status"] = message["status"]
                status_holder["started"] = True
                headers = MutableHeaders(scope=message)
                for key, value in security:
                    if key not in headers:
                        headers[key] = value
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        except Exception:
            log.exception("unhandled_error", path=path)
            if not status_holder["started"]:
                await send_json(
                    send_wrapper,
                    500,
                    {
                        "error": "Something went wrong. Try again.",
                        "code": "internal_error",
                        "requestId": request_id,
                    },
                )
        finally:
            duration_ms = round((time.perf_counter() - started) * 1000, 1)
            client = scope.get("client")
            log.info(
                "request",
                method=scope.get("method"),
                path=path,
                status=status_holder["status"],
                duration_ms=duration_ms,
                client=client[0] if client else None,
            )
            structlog.contextvars.unbind_contextvars("request_id")

    def _headers(self, path: str, request_id: str) -> list[tuple[str, str]]:
        is_docs = path.startswith(DOCS_PATHS) or path == "/openapi.json"
        headers = [
            ("X-Request-ID", request_id),
            ("Content-Security-Policy", DOCS_CSP if is_docs else self.csp),
            ("X-Content-Type-Options", "nosniff"),
            ("Referrer-Policy", "no-referrer"),
            ("Permissions-Policy", "camera=(), microphone=(), geolocation=(), payment=()"),
            ("Cross-Origin-Opener-Policy", "same-origin"),
            ("Cross-Origin-Resource-Policy", "same-origin"),
        ]
        if path.startswith("/api/"):
            headers.append(("Cache-Control", "no-store"))
        if self.hsts:
            headers.append(("Strict-Transport-Security", "max-age=63072000; includeSubDomains"))
        return headers


class BodyTooLargeError(HTTPException):
    """Raised from ``receive``; FastAPI re-raises HTTPException, so it renders as JSON 413."""

    def __init__(self, max_bytes: int) -> None:
        super().__init__(413, f"The request is larger than {max_bytes // 1000:,} KB.")


class BodyLimitMiddleware:
    """Reject request bodies above ``max_bytes`` (declared or streamed) with 413."""

    def __init__(self, app: ASGIApp, *, max_bytes: int) -> None:
        self.app = app
        self.max_bytes = max_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        payload: dict[str, object] = {
            "error": f"The request is larger than {self.max_bytes // 1000:,} KB.",
            "code": "request_too_large",
        }
        declared = dict(scope.get("headers", [])).get(b"content-length")
        if declared is not None:
            try:
                too_big = int(declared) > self.max_bytes
            except ValueError:
                too_big = True
            if too_big:
                await send_json(send, 413, payload, [("Connection", "close")])
                return

        received = 0
        started = False

        async def limited_receive() -> Message:
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > self.max_bytes:
                    raise BodyTooLargeError(self.max_bytes)
            return message

        async def tracking_send(message: Message) -> None:
            nonlocal started
            if message["type"] == "http.response.start":
                started = True
            await send(message)

        try:
            await self.app(scope, limited_receive, tracking_send)
        except BodyTooLargeError:
            if not started:
                await send_json(send, 413, payload, [("Connection", "close")])


@dataclass(slots=True)
class _Bucket:
    tokens: float
    updated: float


class RateLimitMiddleware:
    """In-memory token bucket per client address, applied to API writes only.

    Per-process: behind several workers or replicas use a shared limiter
    (e.g. at the reverse proxy) instead.
    """

    MAX_KEYS = 10_000

    def __init__(self, app: ASGIApp, *, limit: int, window_seconds: int) -> None:
        self.app = app
        self.capacity = float(limit)
        self.rate = limit / window_seconds if window_seconds else 0.0
        self.buckets: dict[str, _Bucket] = {}

    def _take(self, key: str, now: float) -> float:
        """Consume one token; return 0 when allowed, else seconds until the next token."""
        bucket = self.buckets.get(key)
        if bucket is None:
            if len(self.buckets) >= self.MAX_KEYS:
                self._prune(now)
            bucket = self.buckets[key] = _Bucket(self.capacity, now)
        bucket.tokens = min(self.capacity, bucket.tokens + (now - bucket.updated) * self.rate)
        bucket.updated = now
        if bucket.tokens >= 1:
            bucket.tokens -= 1
            return 0.0
        return (1 - bucket.tokens) / self.rate

    def _prune(self, now: float) -> None:
        for key, bucket in list(self.buckets.items()):
            if bucket.tokens + (now - bucket.updated) * self.rate >= self.capacity:
                del self.buckets[key]
        if len(self.buckets) >= self.MAX_KEYS:
            self.buckets.clear()

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if (
            scope["type"] != "http"
            or self.capacity <= 0
            or scope.get("method") not in WRITE_METHODS
            or not scope.get("path", "").startswith("/api/")
        ):
            await self.app(scope, receive, send)
            return
        client = scope.get("client")
        key = client[0] if client else "unknown"
        wait = self._take(key, time.monotonic())
        if wait > 0:
            retry = max(1, math.ceil(wait))
            await send_json(
                send,
                429,
                {
                    "error": f"Too many changes in a short time. Try again in {retry} s.",
                    "code": "rate_limited",
                },
                [("Retry-After", str(retry))],
            )
            return
        await self.app(scope, receive, send)
