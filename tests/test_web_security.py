"""Centralized security regression matrix for every current private Web route."""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from starlette.types import ASGIApp, Message, Scope

from second_brain.entrypoints.web.app import (
    DRAFT_REQUEST_HEADER_NAME,
    DRAFT_REQUEST_HEADER_VALUE,
    MAX_RAW_DRAFT_BODY_BYTES,
    MAX_RAW_SEARCH_BODY_BYTES,
    MAX_RAW_SELF_MODEL_BODY_BYTES,
    MAX_RAW_TIMELINE_BODY_BYTES,
    MAX_RAW_TRANSCRIPTION_BODY_BYTES,
    SEARCH_REQUEST_HEADER_NAME,
    SEARCH_REQUEST_HEADER_VALUE,
    SELF_MODEL_REQUEST_HEADER_NAME,
    SELF_MODEL_REQUEST_HEADER_VALUE,
    TIMELINE_REQUEST_HEADER_NAME,
    TIMELINE_REQUEST_HEADER_VALUE,
    TRANSCRIPTION_REQUEST_HEADER_NAME,
    TRANSCRIPTION_REQUEST_HEADER_VALUE,
    create_app,
)

LOOPBACK_BASE_URL = "http://127.0.0.1"


@dataclass(frozen=True, slots=True)
class PrivateRoute:
    """Describe one private route boundary without invoking its application service."""

    path: str
    request_header_name: str
    request_header_value: str
    content_type: str
    body: bytes
    invalid_code: str
    content_too_large_code: str
    max_body_bytes: int


def _draft_route(
    path: str,
    invalid_code: str = "LLM_INVALID_REQUEST",
) -> PrivateRoute:
    """Build one JSON draft route descriptor with the shared draft cap."""

    return PrivateRoute(
        path=path,
        request_header_name=DRAFT_REQUEST_HEADER_NAME,
        request_header_value=DRAFT_REQUEST_HEADER_VALUE,
        content_type="application/json",
        body=b"{}",
        invalid_code=invalid_code,
        content_too_large_code=(
            "RESEARCH_CONTENT_TOO_LARGE"
            if path.endswith("/url")
            else "DRAFT_CONTENT_TOO_LARGE"
            if path not in {"/api/drafts/text"}
            else "LLM_CONTENT_TOO_LARGE"
        ),
        max_body_bytes=MAX_RAW_DRAFT_BODY_BYTES,
    )


PRIVATE_ROUTES: tuple[PrivateRoute, ...] = (
    _draft_route("/api/drafts/text"),
    _draft_route("/api/drafts/url", "RESEARCH_INVALID_REQUEST"),
    _draft_route("/api/drafts/preview", "DRAFT_INVALID_REQUEST"),
    _draft_route("/api/drafts/save/prepare", "DRAFT_INVALID_REQUEST"),
    _draft_route("/api/drafts/save/apply", "DRAFT_INVALID_REQUEST"),
    _draft_route(
        "/api/drafts/personal-memory/save/prepare",
        "PERSONAL_MEMORY_INVALID_REQUEST",
    ),
    _draft_route(
        "/api/drafts/personal-memory/save/apply",
        "PERSONAL_MEMORY_INVALID_REQUEST",
    ),
    _draft_route(
        "/api/drafts/decision-journal/save/prepare",
        "DECISION_JOURNAL_INVALID_REQUEST",
    ),
    _draft_route(
        "/api/drafts/decision-journal/save/apply",
        "DECISION_JOURNAL_INVALID_REQUEST",
    ),
    _draft_route(
        "/api/drafts/outcome-observation/save/prepare",
        "OUTCOME_OBSERVATION_INVALID_REQUEST",
    ),
    _draft_route(
        "/api/drafts/outcome-observation/save/apply",
        "OUTCOME_OBSERVATION_INVALID_REQUEST",
    ),
    PrivateRoute(
        path="/api/transcriptions/audio",
        request_header_name=TRANSCRIPTION_REQUEST_HEADER_NAME,
        request_header_value=TRANSCRIPTION_REQUEST_HEADER_VALUE,
        content_type="audio/wav",
        body=b"audio",
        invalid_code="TRANSCRIPTION_INVALID_REQUEST",
        content_too_large_code="TRANSCRIPTION_CONTENT_TOO_LARGE",
        max_body_bytes=MAX_RAW_TRANSCRIPTION_BODY_BYTES,
    ),
    PrivateRoute(
        path="/api/search",
        request_header_name=SEARCH_REQUEST_HEADER_NAME,
        request_header_value=SEARCH_REQUEST_HEADER_VALUE,
        content_type="application/json",
        body=b"{}",
        invalid_code="SEARCH_INVALID_REQUEST",
        content_too_large_code="SEARCH_CONTENT_TOO_LARGE",
        max_body_bytes=MAX_RAW_SEARCH_BODY_BYTES,
    ),
    PrivateRoute(
        path="/api/retrieval/note",
        request_header_name=SEARCH_REQUEST_HEADER_NAME,
        request_header_value=SEARCH_REQUEST_HEADER_VALUE,
        content_type="application/json",
        body=b"{}",
        invalid_code="SEARCH_INVALID_REQUEST",
        content_too_large_code="SEARCH_CONTENT_TOO_LARGE",
        max_body_bytes=MAX_RAW_SEARCH_BODY_BYTES,
    ),
    PrivateRoute(
        path="/api/timeline",
        request_header_name=TIMELINE_REQUEST_HEADER_NAME,
        request_header_value=TIMELINE_REQUEST_HEADER_VALUE,
        content_type="application/json",
        body=b"{}",
        invalid_code="TIMELINE_INVALID_REQUEST",
        content_too_large_code="TIMELINE_CONTENT_TOO_LARGE",
        max_body_bytes=MAX_RAW_TIMELINE_BODY_BYTES,
    ),
    PrivateRoute(
        path="/api/self-model",
        request_header_name=SELF_MODEL_REQUEST_HEADER_NAME,
        request_header_value=SELF_MODEL_REQUEST_HEADER_VALUE,
        content_type="application/json",
        body=b"{}",
        invalid_code="SELF_MODEL_INVALID_REQUEST",
        content_too_large_code="SELF_MODEL_CONTENT_TOO_LARGE",
        max_body_bytes=MAX_RAW_SELF_MODEL_BODY_BYTES,
    ),
)


def _valid_headers(route: PrivateRoute) -> dict[str, str]:
    """Return the minimum valid request metadata for one private route."""

    return {
        "Content-Type": route.content_type,
        route.request_header_name: route.request_header_value,
    }


@pytest.mark.parametrize(
    "route",
    PRIVATE_ROUTES,
    ids=lambda route: route.path,
)
@pytest.mark.parametrize(
    "violation",
    ["missing-purpose", "wrong-purpose", "foreign-origin", "wrong-host", "wrong-content-type"],
)
def test_every_private_route_rejects_boundary_metadata_before_service(
    route: PrivateRoute,
    violation: str,
) -> None:
    """Keep purpose, loopback, same-origin and media-type gates aligned."""

    headers = _valid_headers(route)
    if violation == "missing-purpose":
        headers.pop(route.request_header_name)
    elif violation == "wrong-purpose":
        headers[route.request_header_name] = "wrong-purpose-v1"
    elif violation == "foreign-origin":
        headers["Origin"] = "https://evil.example"
    elif violation == "wrong-host":
        headers["Host"] = "evil.example"
    else:
        headers["Content-Type"] = "text/plain"

    raw_headers = [("host", "127.0.0.1"), *headers.items()]
    status, response_headers, response_body, receive_calls = _raw_request(
        create_app(),
        path=route.path,
        headers=raw_headers,
        body=route.body,
    )
    payload = json.loads(response_body)

    assert status == 400
    assert receive_calls == 0
    assert payload["error"]["code"] == route.invalid_code
    assert set(payload["error"]) == {"code", "message"}
    assert response_headers["cache-control"] == "no-store"
    assert response_headers["x-content-type-options"] == "nosniff"
    assert response_headers["referrer-policy"] == "no-referrer"
    assert response_headers["x-frame-options"] == "DENY"
    assert "content-security-policy" in response_headers
    assert "access-control-allow-origin" not in response_headers
    assert b"traceback" not in response_body.lower()
    assert b"exception" not in response_body.lower()


def _raw_request(
    application: ASGIApp,
    *,
    path: str,
    headers: list[tuple[str, str]],
    body: bytes,
) -> tuple[int, dict[str, str], bytes, int]:
    """Send duplicate headers and controlled body chunks directly to ASGI."""

    encoded_headers = [
        (name.lower().encode("ascii"), value.encode("latin-1")) for name, value in headers
    ]
    messages: list[Message] = [
        {"type": "http.request", "body": body, "more_body": False},
    ]
    receive_calls = 0
    sent_messages: list[Message] = []

    async def receive() -> Message:
        nonlocal receive_calls
        receive_calls += 1
        if messages:
            return messages.pop(0)
        return {"type": "http.disconnect"}

    async def send(message: Message) -> None:
        sent_messages.append(message)

    scope: Scope = {
        "type": "http",
        "asgi": {"version": "3.0", "spec_version": "2.4"},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": path,
        "raw_path": path.encode("ascii"),
        "query_string": b"",
        "headers": encoded_headers,
        "client": ("127.0.0.1", 12345),
        "server": ("127.0.0.1", 80),
    }

    async def run() -> None:
        await application(scope, receive, send)

    asyncio.run(run())
    response_start = next(
        message for message in sent_messages if message["type"] == "http.response.start"
    )
    response_headers = {
        name.decode("latin-1").lower(): value.decode("latin-1")
        for name, value in response_start["headers"]
    }
    response_body = b"".join(
        message.get("body", b"")
        for message in sent_messages
        if message["type"] == "http.response.body"
    )
    return int(response_start["status"]), response_headers, response_body, receive_calls


@pytest.mark.parametrize(
    "route",
    PRIVATE_ROUTES,
    ids=lambda route: route.path,
)
@pytest.mark.parametrize("duplicate_name", ["purpose", "content-type", "origin", "content-length"])
def test_every_private_route_rejects_duplicate_security_headers_before_body_read(
    route: PrivateRoute,
    duplicate_name: str,
) -> None:
    """Reject ambiguous duplicate metadata before any raw body is consumed."""

    headers = [
        ("host", "127.0.0.1"),
        ("content-type", route.content_type),
        (route.request_header_name, route.request_header_value),
    ]
    if duplicate_name == "purpose":
        headers.append((route.request_header_name, route.request_header_value))
    elif duplicate_name == "content-type":
        headers.append(("content-type", route.content_type))
    elif duplicate_name == "origin":
        headers.extend([("origin", LOOPBACK_BASE_URL), ("origin", LOOPBACK_BASE_URL)])
    else:
        headers.extend([("content-length", str(len(route.body)))] * 2)

    status, response_headers, body, receive_calls = _raw_request(
        create_app(),
        path=route.path,
        headers=headers,
        body=route.body,
    )

    assert status == 400
    assert receive_calls == 0
    assert response_headers["cache-control"] == "no-store"
    assert response_headers["x-content-type-options"] == "nosniff"
    assert response_headers["referrer-policy"] == "no-referrer"
    assert response_headers["x-frame-options"] == "DENY"
    assert "content-security-policy" in response_headers
    assert "access-control-allow-origin" not in response_headers
    assert f'"code":"{route.invalid_code}"'.encode() in body


@pytest.mark.parametrize(
    "route",
    PRIVATE_ROUTES,
    ids=lambda route: route.path,
)
def test_every_private_route_enforces_raw_body_cap_before_parser(route: PrivateRoute) -> None:
    """Keep declared oversize requests out of FastAPI parsing and services."""

    headers = [
        ("host", "127.0.0.1"),
        ("content-type", route.content_type),
        (route.request_header_name, route.request_header_value),
        ("content-length", str(route.max_body_bytes + 1)),
    ]

    status, response_headers, body, receive_calls = _raw_request(
        create_app(),
        path=route.path,
        headers=headers,
        body=route.body,
    )

    assert status == 413
    assert receive_calls == 0
    assert response_headers["cache-control"] == "no-store"
    assert response_headers["x-content-type-options"] == "nosniff"
    assert response_headers["referrer-policy"] == "no-referrer"
    assert response_headers["x-frame-options"] == "DENY"
    assert f'"code":"{route.content_too_large_code}"'.encode() in body


@pytest.mark.parametrize(
    "path",
    [route.path for route in PRIVATE_ROUTES],
    ids=[route.path for route in PRIVATE_ROUTES],
)
def test_private_routes_are_post_only_and_cacheless_on_method_errors(path: str) -> None:
    """Keep accidental GET/OPTIONS exposure out of the private API surface."""

    with TestClient(create_app(), base_url=LOOPBACK_BASE_URL) as client:
        get_response = client.get(path)
        options_response = client.options(path)

    for response in (get_response, options_response):
        assert response.status_code == 405
        assert response.headers["cache-control"] == "no-store"
        assert response.headers["x-content-type-options"] == "nosniff"
        assert response.headers["referrer-policy"] == "no-referrer"
        assert response.headers["x-frame-options"] == "DENY"
        assert "access-control-allow-origin" not in response.headers


def test_private_routes_are_not_documented_and_no_cors_is_configured() -> None:
    """Keep local private routes hidden from generated docs and CORS negotiation."""

    application = create_app()
    assert application.docs_url is None
    assert application.redoc_url is None
    assert application.openapi_url is None
    assert all(
        "CORSMiddleware" not in repr(middleware.cls) for middleware in application.user_middleware
    )

    with TestClient(application, base_url=LOOPBACK_BASE_URL) as client:
        assert client.get("/openapi.json").status_code == 404


def test_all_packaged_web_scripts_remain_storage_free_and_non_polling() -> None:
    """Protect the local review flow from browser persistence or background polling."""

    static_dir = (
        Path(__file__).parents[1] / "src" / "second_brain" / "entrypoints" / "web" / "static"
    )
    javascript = "\n".join(path.read_text(encoding="utf-8") for path in static_dir.glob("*.js"))

    for forbidden in (
        "localStorage",
        "sessionStorage",
        "indexedDB",
        "serviceWorker",
        "setInterval",
    ):
        assert forbidden not in javascript

    assert 'fetch("/api/' in javascript
