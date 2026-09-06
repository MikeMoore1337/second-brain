"""Centralized security regression matrix for every current private Web route."""

from __future__ import annotations

import asyncio
import json
import re
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
    MAX_RAW_SELF_RETRIEVAL_BODY_BYTES,
    MAX_RAW_TIMELINE_BODY_BYTES,
    MAX_RAW_TRANSCRIPTION_BODY_BYTES,
    SEARCH_REQUEST_HEADER_NAME,
    SEARCH_REQUEST_HEADER_VALUE,
    SELF_MODEL_REQUEST_HEADER_NAME,
    SELF_MODEL_REQUEST_HEADER_VALUE,
    SELF_RETRIEVAL_REQUEST_HEADER_NAME,
    SELF_RETRIEVAL_REQUEST_HEADER_VALUE,
    TIMELINE_REQUEST_HEADER_NAME,
    TIMELINE_REQUEST_HEADER_VALUE,
    TRANSCRIPTION_REQUEST_HEADER_NAME,
    TRANSCRIPTION_REQUEST_HEADER_VALUE,
    create_app,
)

LOOPBACK_BASE_URL = "http://127.0.0.1"
_REVIEW_TOKEN = "review-token-v1"
_CONFIRMATION_TOKEN = "confirmation-token-v1"
_DECISION_ID = "0198c8a0-0000-7000-8000-000000000001"


def _json_body(payload: object) -> bytes:
    """Encode one strict JSON request body used by the boundary matrix."""

    return json.dumps(payload, separators=(",", ":")).encode("utf-8")


_DRAFT_PAYLOAD = {
    "title": "Boundary fixture",
    "note_type": "note",
    "content": "Boundary fixture content.",
    "tags": ["fixture"],
    "links": [],
}
_DRAFT_BODY = _json_body(_DRAFT_PAYLOAD)
_PERSONAL_MEMORY_PAYLOAD = {
    "evidence_kind": "explicit_user_fact",
    "self_kind": "preference",
    "evidence_at": "2026-09-06",
    "evidence_at_precision": "day",
    "domain": "testing",
}
_DECISION_PAYLOAD = {
    "title": "Boundary decision",
    "note_type": "decision_journal",
    "tags": ["fixture"],
    "links": [],
    "evidence_at": "2026-09-06",
    "evidence_at_precision": "day",
    "domain": "testing",
    "situation": "A bounded security fixture.",
    "available_options": ["Keep the boundary test"],
    "information_known_at_decision_time": "The test is local.",
    "criteria": ["Safe and deterministic"],
    "chosen_option": "Keep the boundary test",
    "reasons": "It stays within the security regression scope.",
    "confidence": "not_assessed",
    "expected_result": "The boundary remains fail-closed.",
}
_OUTCOME_PAYLOAD = {
    "title": "Boundary outcome",
    "note_type": "outcome_observation",
    "tags": ["fixture"],
    "links": [],
    "decision_id": _DECISION_ID,
    "evidence_at": "2026-09-06",
    "evidence_at_precision": "day",
    "domain": "testing",
    "actual_result": "The boundary rejected the request.",
    "reassessment": "No reassessment is needed.",
    "notes": "Fixture only.",
}


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
    *,
    body: bytes = _DRAFT_BODY,
) -> PrivateRoute:
    """Build one JSON draft route descriptor with the shared draft cap."""

    return PrivateRoute(
        path=path,
        request_header_name=DRAFT_REQUEST_HEADER_NAME,
        request_header_value=DRAFT_REQUEST_HEADER_VALUE,
        content_type="application/json",
        body=body,
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
    _draft_route(
        "/api/drafts/text",
        body=_json_body({"text": "Boundary fixture text."}),
    ),
    _draft_route(
        "/api/drafts/url",
        "RESEARCH_INVALID_REQUEST",
        body=_json_body({"url": "https://example.com/boundary-fixture"}),
    ),
    _draft_route(
        "/api/drafts/preview",
        "DRAFT_INVALID_REQUEST",
        body=_json_body({"content": "# Boundary fixture"}),
    ),
    _draft_route(
        "/api/drafts/save/prepare",
        "DRAFT_INVALID_REQUEST",
        body=_json_body({"review_token": _REVIEW_TOKEN, "draft": _DRAFT_PAYLOAD}),
    ),
    _draft_route(
        "/api/drafts/save/apply",
        "DRAFT_INVALID_REQUEST",
        body=_json_body(
            {
                "review_token": _REVIEW_TOKEN,
                "confirmation_token": _CONFIRMATION_TOKEN,
                "draft": _DRAFT_PAYLOAD,
            }
        ),
    ),
    _draft_route(
        "/api/drafts/personal-memory/save/prepare",
        "PERSONAL_MEMORY_INVALID_REQUEST",
        body=_json_body(
            {
                "review_token": _REVIEW_TOKEN,
                "draft": _DRAFT_PAYLOAD,
                "personal_memory": _PERSONAL_MEMORY_PAYLOAD,
            }
        ),
    ),
    _draft_route(
        "/api/drafts/personal-memory/save/apply",
        "PERSONAL_MEMORY_INVALID_REQUEST",
        body=_json_body(
            {
                "review_token": _REVIEW_TOKEN,
                "confirmation_token": _CONFIRMATION_TOKEN,
                "draft": _DRAFT_PAYLOAD,
                "personal_memory": _PERSONAL_MEMORY_PAYLOAD,
            }
        ),
    ),
    _draft_route(
        "/api/drafts/decision-journal/save/prepare",
        "DECISION_JOURNAL_INVALID_REQUEST",
        body=_json_body({"decision": _DECISION_PAYLOAD}),
    ),
    _draft_route(
        "/api/drafts/decision-journal/save/apply",
        "DECISION_JOURNAL_INVALID_REQUEST",
        body=_json_body(
            {
                "confirmation_token": _CONFIRMATION_TOKEN,
                "decision": _DECISION_PAYLOAD,
            }
        ),
    ),
    _draft_route(
        "/api/drafts/outcome-observation/save/prepare",
        "OUTCOME_OBSERVATION_INVALID_REQUEST",
        body=_json_body({"outcome": _OUTCOME_PAYLOAD}),
    ),
    _draft_route(
        "/api/drafts/outcome-observation/save/apply",
        "OUTCOME_OBSERVATION_INVALID_REQUEST",
        body=_json_body(
            {
                "confirmation_token": _CONFIRMATION_TOKEN,
                "outcome": _OUTCOME_PAYLOAD,
            }
        ),
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
        body=_json_body({"query": "boundary fixture", "limit": 1}),
        invalid_code="SEARCH_INVALID_REQUEST",
        content_too_large_code="SEARCH_CONTENT_TOO_LARGE",
        max_body_bytes=MAX_RAW_SEARCH_BODY_BYTES,
    ),
    PrivateRoute(
        path="/api/retrieval/note",
        request_header_name=SEARCH_REQUEST_HEADER_NAME,
        request_header_value=SEARCH_REQUEST_HEADER_VALUE,
        content_type="application/json",
        body=_json_body({"id": _DECISION_ID}),
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
    PrivateRoute(
        path="/api/self-retrieval",
        request_header_name=SELF_RETRIEVAL_REQUEST_HEADER_NAME,
        request_header_value=SELF_RETRIEVAL_REQUEST_HEADER_VALUE,
        content_type="application/json",
        body=_json_body({"query": "boundary fixture", "limit": 1}),
        invalid_code="SELF_RETRIEVAL_INVALID_REQUEST",
        content_too_large_code="SELF_RETRIEVAL_RESULT_TOO_LARGE",
        max_body_bytes=MAX_RAW_SELF_RETRIEVAL_BODY_BYTES,
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

    raw_headers = list(headers.items())
    if violation != "wrong-host":
        raw_headers.insert(0, ("host", "127.0.0.1"))
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
    body_chunks: tuple[bytes, ...] | None = None,
    method: str = "POST",
) -> tuple[int, dict[str, str], bytes, int]:
    """Send duplicate headers and controlled body chunks directly to ASGI."""

    encoded_headers = [
        (name.lower().encode("ascii"), value.encode("latin-1")) for name, value in headers
    ]
    chunks = (body,) if body_chunks is None else body_chunks
    messages: list[Message] = [
        {
            "type": "http.request",
            "body": chunk,
            "more_body": index < len(chunks) - 1,
        }
        for index, chunk in enumerate(chunks)
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
        "method": method,
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
@pytest.mark.parametrize(
    "duplicate_name",
    ["host", "purpose", "content-type", "origin", "content-length"],
)
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
    elif duplicate_name == "host":
        headers.append(("host", "127.0.0.1"))
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
    "route",
    PRIVATE_ROUTES,
    ids=lambda route: route.path,
)
@pytest.mark.parametrize(
    "declared_length",
    [None, "1"],
    ids=["missing-content-length", "understated-content-length"],
)
def test_every_private_route_enforces_streamed_raw_body_cap(
    route: PrivateRoute,
    declared_length: str | None,
) -> None:
    """Reject streamed oversize bodies even when framing metadata is absent or false."""

    headers = [
        ("host", "127.0.0.1"),
        ("content-type", route.content_type),
        (route.request_header_name, route.request_header_value),
    ]
    if declared_length is not None:
        headers.append(("content-length", declared_length))

    status, response_headers, body, receive_calls = _raw_request(
        create_app(),
        path=route.path,
        headers=headers,
        body=b"",
        body_chunks=(b"x" * route.max_body_bytes, b"x"),
    )

    assert status == 413
    assert receive_calls >= 2
    assert response_headers["cache-control"] == "no-store"
    assert response_headers["x-content-type-options"] == "nosniff"
    assert response_headers["referrer-policy"] == "no-referrer"
    assert response_headers["x-frame-options"] == "DENY"
    assert f'"code":"{route.content_too_large_code}"'.encode() in body


def test_private_route_matrix_covers_all_registered_private_post_routes() -> None:
    """Keep the security matrix exhaustive as private POST routes are added."""

    application = create_app()
    registered_private_post_paths: set[str] = set()
    for route in application.routes:
        route_path = getattr(route, "path", None)
        route_methods = getattr(route, "methods", None)
        if (
            isinstance(route_path, str)
            and route_path.startswith("/api/")
            and isinstance(route_methods, set)
            and "POST" in route_methods
        ):
            registered_private_post_paths.add(route_path)

    assert registered_private_post_paths == {route.path for route in PRIVATE_ROUTES}


@pytest.mark.parametrize(
    "path",
    [route.path for route in PRIVATE_ROUTES],
    ids=[route.path for route in PRIVATE_ROUTES],
)
@pytest.mark.parametrize("method", ["GET", "OPTIONS", "PUT", "PATCH", "DELETE"])
def test_private_routes_are_post_only_and_cacheless_on_method_errors(
    path: str,
    method: str,
) -> None:
    """Keep accidental non-POST exposure out of the private API surface."""

    with TestClient(create_app(), base_url=LOOPBACK_BASE_URL) as client:
        response = client.request(method, path)

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

    assert not re.search(r"\bsetTimeout\s*\(", javascript)

    assert 'fetch("/api/' in javascript
