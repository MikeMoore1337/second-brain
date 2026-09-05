"""Deterministic Web API tests for read-only text and public WEB drafts."""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient
from starlette.types import ASGIApp, Message, Scope

from second_brain.adapters.llm import cloudflare_workers_ai
from second_brain.application.llm import MAX_CONTEXT_BYTES, LlmGateway, LlmRequest, NoteDraft
from second_brain.application.ports import (
    CancellationToken,
    LlmBackendUnavailableError,
    LlmContentTooLargeError,
    LlmMalformedResultError,
    LlmTimeoutError,
    LlmUpstreamError,
    ResearchBackendUnavailableError,
    ResearchContentTooLargeError,
    ResearchMalformedResultError,
    ResearchTimeoutError,
    ResearchUpstreamError,
)
from second_brain.application.research import (
    ResearchGateway,
    ResearchRequest,
    ResearchSource,
    SourceKind,
    SourceProvenance,
)
from second_brain.application.research_draft import ResearchDraftGateway, ResearchDraftResult
from second_brain.domain.models import NoteType
from second_brain.entrypoints.web.app import (
    DRAFT_REQUEST_HEADER_NAME,
    DRAFT_REQUEST_HEADER_VALUE,
    MAX_RAW_DRAFT_BODY_BYTES,
    create_app,
)
from second_brain.entrypoints.web.drafts import CAPTURE_INSTRUCTION, GatewayDraftService

LOOPBACK_BASE_URL = "http://127.0.0.1"
DRAFT_REQUEST_HEADERS = {DRAFT_REQUEST_HEADER_NAME: DRAFT_REQUEST_HEADER_VALUE}


def send_raw_asgi_request(
    application: ASGIApp,
    *,
    path: str,
    headers: dict[str, str],
    body_chunks: tuple[bytes, ...],
) -> tuple[int, dict[str, str], bytes, int]:
    """Послать контролируемые ASGI chunks без httpx body buffering."""

    encoded_headers = [
        (name.lower().encode("ascii"), value.encode("latin-1")) for name, value in headers.items()
    ]
    messages: list[Message] = [
        {
            "type": "http.request",
            "body": chunk,
            "more_body": index < len(body_chunks) - 1,
        }
        for index, chunk in enumerate(body_chunks)
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


class RecordingLlmPort:
    """Фиксирует один LlmRequest без provider/network."""

    def __init__(self, result: NoteDraft, error: Exception | None = None) -> None:
        self.result = result
        self.error = error
        self.calls: list[LlmRequest] = []

    def draft_note(
        self,
        request: LlmRequest,
        *,
        cancellation: CancellationToken,
    ) -> NoteDraft:
        del cancellation
        self.calls.append(request)
        if self.error is not None:
            raise self.error
        return self.result


class RecordingResearchPort:
    """Фиксирует один ResearchRequest без external read."""

    def __init__(self, result: ResearchSource, error: Exception | None = None) -> None:
        self.result = result
        self.error = error
        self.calls: list[ResearchRequest] = []

    def read(
        self,
        request: ResearchRequest,
        *,
        cancellation: CancellationToken,
    ) -> ResearchSource:
        del cancellation
        self.calls.append(request)
        if self.error is not None:
            raise self.error
        return self.result


class StubDraftService:
    """DI fake для safe HTTP error mapping."""

    def __init__(self, error: Exception | None = None) -> None:
        self.error = error
        self.text_calls: list[str] = []
        self.url_calls: list[str] = []

    def draft_text(self, text: str) -> NoteDraft:
        self.text_calls.append(text)
        if self.error is not None:
            raise self.error
        return make_draft()

    def draft_url(self, url: str) -> ResearchDraftResult:
        self.url_calls.append(url)
        if self.error is not None:
            raise self.error
        source = make_source()
        return ResearchDraftResult(
            draft=make_draft(), source=SourceProvenance.from_research_source(source)
        )


def make_draft() -> NoteDraft:
    """Собрать bounded five-field NoteDraft."""

    return NoteDraft(
        title="Черновик из Web",
        note_type=NoteType.RESOURCE,
        content="Содержимое без автоматического source URL.",
        tags=("web", "draft"),
        links=("[[Knowledge]]",),
    )


def make_source() -> ResearchSource:
    """Собрать source с metadata, которая не должна попасть в raw API response."""

    return ResearchSource(
        uri="https://example.com/article",
        source_kind=SourceKind.WEB,
        retrieved_at=datetime(2026, 9, 5, 9, 0, tzinfo=UTC),
        backend="jina-backend-sentinel",
        content="source-content-sentinel\nТолько содержание источника.",
        title="source-title-sentinel",
        author="source-author-sentinel",
        media_type="text/markdown",
        upstream_id="source-upstream-sentinel",
        published_at=datetime(2026, 9, 4, 9, 0, tzinfo=UTC),
    )


def make_gateway_service() -> tuple[
    GatewayDraftService,
    RecordingLlmPort,
    RecordingResearchPort,
]:
    """Подключить оба существующих gateway к deterministic fake ports."""

    llm_port = RecordingLlmPort(make_draft())
    research_port = RecordingResearchPort(make_source())
    llm_gateway = LlmGateway(llm_port)
    return (
        GatewayDraftService(
            llm_gateway=llm_gateway,
            research_draft_gateway=ResearchDraftGateway(
                ResearchGateway(research_port),
                llm_gateway,
            ),
        ),
        llm_port,
        research_port,
    )


def test_text_route_uses_exact_context_once_and_returns_no_sources() -> None:
    service, llm_port, research_port = make_gateway_service()
    user_text = "  Русский текст\nс control-boundary смыслом.  "

    with TestClient(create_app(draft_service=service), base_url=LOOPBACK_BASE_URL) as client:
        response = client.post(
            "/api/drafts/text", json={"text": user_text}, headers=DRAFT_REQUEST_HEADERS
        )

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert response.json() == {
        "draft": {
            "title": "Черновик из Web",
            "note_type": "resource",
            "content": "Содержимое без автоматического source URL.",
            "tags": ["web", "draft"],
            "links": ["[[Knowledge]]"],
        },
        "sources": [],
    }
    assert len(llm_port.calls) == 1
    assert len(research_port.calls) == 0
    assert llm_port.calls[0].context == user_text
    assert llm_port.calls[0].instruction == CAPTURE_INSTRUCTION


def test_url_route_uses_web_gateway_once_and_returns_bounded_provenance() -> None:
    service, llm_port, research_port = make_gateway_service()
    source = make_source()

    with TestClient(create_app(draft_service=service), base_url=LOOPBACK_BASE_URL) as client:
        response = client.post(
            "/api/drafts/url", json={"url": source.uri}, headers=DRAFT_REQUEST_HEADERS
        )

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    payload = response.json()
    assert set(payload) == {"draft", "sources"}
    assert set(payload["draft"]) == {"title", "note_type", "content", "tags", "links"}
    assert payload["draft"]["content"] == "Содержимое без автоматического source URL."
    assert payload["draft"]["links"] == ["[[Knowledge]]"]
    assert payload["sources"] == [
        {
            "uri": source.uri,
            "kind": "web",
            "retrieved_at": "2026-09-05T09:00:00Z",
            "published_at": "2026-09-04T09:00:00Z",
            "title": source.title,
            "author": source.author,
            "upstream_id": source.upstream_id,
        }
    ]
    serialized = json.dumps(payload, ensure_ascii=False)
    assert "source-content-sentinel" not in serialized
    assert "source-backend-sentinel" not in serialized
    assert "media_type" not in serialized
    assert "provider" not in serialized
    assert len(research_port.calls) == 1
    assert research_port.calls[0].source_kind is SourceKind.WEB
    assert research_port.calls[0].uri == source.uri
    assert len(llm_port.calls) == 1
    assert llm_port.calls[0].context == source.content
    assert llm_port.calls[0].instruction == CAPTURE_INSTRUCTION
    assert "source-title-sentinel" not in llm_port.calls[0].instruction
    assert "source-author-sentinel" not in llm_port.calls[0].instruction


def test_draft_boundary_accepts_same_origin_json_with_utf8_charset() -> None:
    service = StubDraftService()
    body = json.dumps({"text": "same-origin"}).encode("utf-8")

    with TestClient(create_app(draft_service=service), base_url=LOOPBACK_BASE_URL) as client:
        response = client.post(
            "/api/drafts/text",
            content=body,
            headers={
                **DRAFT_REQUEST_HEADERS,
                "content-type": "application/json; charset=utf-8",
                "origin": LOOPBACK_BASE_URL,
            },
        )

    assert response.status_code == 200
    assert service.text_calls == ["same-origin"]


@pytest.mark.parametrize(
    ("headers", "body"),
    [
        ({}, b'{"text":"missing header"}'),
        ({DRAFT_REQUEST_HEADER_NAME: "wrong"}, b'{"text":"wrong header"}'),
        (
            {**DRAFT_REQUEST_HEADERS, "origin": "https://evil.example"},
            b'{"text":"foreign origin"}',
        ),
        (
            {**DRAFT_REQUEST_HEADERS, "origin": "http://127.0.0.1:9999"},
            b'{"text":"wrong origin port"}',
        ),
        (DRAFT_REQUEST_HEADERS, b'{"text":"missing content type"}'),
        (
            {**DRAFT_REQUEST_HEADERS, "content-type": "text/plain"},
            b'{"text":"wrong content type"}',
        ),
    ],
)
def test_draft_boundary_rejects_cross_origin_or_non_json_before_service(
    headers: dict[str, str], body: bytes
) -> None:
    service = StubDraftService()

    with TestClient(create_app(draft_service=service), base_url=LOOPBACK_BASE_URL) as client:
        response = client.post("/api/drafts/text", content=body, headers=headers)

    assert response.status_code == 400
    assert response.json() == {
        "error": {
            "code": "LLM_INVALID_REQUEST",
            "message": "draft request failed validation",
        }
    }
    assert response.headers["cache-control"] == "no-store"
    assert service.text_calls == []
    assert service.url_calls == []


@pytest.mark.parametrize(
    ("path", "code"),
    [
        ("/api/drafts/text", "LLM_CONTENT_TOO_LARGE"),
        ("/api/drafts/url", "RESEARCH_CONTENT_TOO_LARGE"),
    ],
)
def test_declared_raw_body_cap_rejects_before_receive_or_service(path: str, code: str) -> None:
    service = StubDraftService()
    status, headers, body, receive_calls = send_raw_asgi_request(
        create_app(draft_service=service),
        path=path,
        headers={
            **DRAFT_REQUEST_HEADERS,
            "content-type": "application/json",
            "content-length": str(MAX_RAW_DRAFT_BODY_BYTES + 1),
            "host": "127.0.0.1",
        },
        body_chunks=(b'{"text":"not read"}',),
    )

    assert status == 413
    assert headers["cache-control"] == "no-store"
    assert json.loads(body) == {
        "error": {
            "code": code,
            "message": (
                "research content is too large"
                if path.endswith("/url")
                else "draft content is too large"
            ),
        }
    }
    assert receive_calls == 0
    assert service.text_calls == []
    assert service.url_calls == []


@pytest.mark.parametrize("declared_length", [None, "1"])
def test_actual_raw_body_cap_rejects_missing_or_misleading_content_length(
    declared_length: str | None,
) -> None:
    service = StubDraftService()
    headers = {
        **DRAFT_REQUEST_HEADERS,
        "content-type": "application/json",
        "host": "127.0.0.1",
    }
    if declared_length is not None:
        headers["content-length"] = declared_length

    status, response_headers, body, receive_calls = send_raw_asgi_request(
        create_app(draft_service=service),
        path="/api/drafts/text",
        headers=headers,
        body_chunks=(b"{}", b"x" * MAX_RAW_DRAFT_BODY_BYTES),
    )

    assert status == 413
    assert response_headers["cache-control"] == "no-store"
    assert json.loads(body)["error"]["code"] == "LLM_CONTENT_TOO_LARGE"
    assert receive_calls == 2
    assert service.text_calls == []


def test_raw_body_cap_allows_valid_context_near_application_limit() -> None:
    service = StubDraftService()
    text = "a" * (MAX_CONTEXT_BYTES - 32)

    with TestClient(create_app(draft_service=service), base_url=LOOPBACK_BASE_URL) as client:
        response = client.post(
            "/api/drafts/text",
            json={"text": text},
            headers=DRAFT_REQUEST_HEADERS,
        )

    assert response.status_code == 200
    assert service.text_calls == [text]


@pytest.mark.parametrize(
    ("path", "payload", "expected_code"),
    [
        ("/api/drafts/text", {"text": "ok", "extra": True}, "LLM_INVALID_REQUEST"),
        ("/api/drafts/text", {"text": 42}, "LLM_INVALID_REQUEST"),
        ("/api/drafts/url", {"url": "ok", "extra": True}, "RESEARCH_INVALID_REQUEST"),
        ("/api/drafts/url", {"url": 42}, "RESEARCH_INVALID_REQUEST"),
    ],
)
def test_draft_requests_are_strict_json_and_reject_unknown_or_wrong_fields(
    path: str,
    payload: dict[str, object],
    expected_code: str,
) -> None:
    service = StubDraftService()

    with TestClient(create_app(draft_service=service), base_url=LOOPBACK_BASE_URL) as client:
        response = client.post(path, json=payload, headers=DRAFT_REQUEST_HEADERS)

    assert response.status_code == 400
    assert response.json() == {
        "error": {
            "code": expected_code,
            "message": (
                "research request failed validation"
                if expected_code.startswith("RESEARCH")
                else "draft request failed validation"
            ),
        }
    }
    assert response.headers["cache-control"] == "no-store"
    assert service.text_calls == []
    assert service.url_calls == []


def test_form_fallback_and_blank_values_are_rejected_before_service() -> None:
    service = StubDraftService()

    with TestClient(create_app(draft_service=service), base_url=LOOPBACK_BASE_URL) as client:
        form_response = client.post(
            "/api/drafts/text", data={"text": "not JSON"}, headers=DRAFT_REQUEST_HEADERS
        )
        blank_text = client.post(
            "/api/drafts/text", json={"text": " \n "}, headers=DRAFT_REQUEST_HEADERS
        )
        blank_url = client.post(
            "/api/drafts/url", json={"url": "  "}, headers=DRAFT_REQUEST_HEADERS
        )

    assert form_response.status_code == 400
    assert blank_text.status_code == 400
    assert blank_url.status_code == 400
    assert service.text_calls == []
    assert service.url_calls == []


@pytest.mark.parametrize(
    ("path", "payload", "error", "status", "code"),
    [
        (
            "/api/drafts/text",
            {"text": "text"},
            LlmBackendUnavailableError(),
            503,
            "LLM_BACKEND_UNAVAILABLE",
        ),
        ("/api/drafts/text", {"text": "text"}, LlmTimeoutError(), 504, "LLM_TIMEOUT"),
        (
            "/api/drafts/text",
            {"text": "text"},
            LlmUpstreamError(),
            502,
            "LLM_UPSTREAM_FAILURE",
        ),
        (
            "/api/drafts/text",
            {"text": "text"},
            LlmMalformedResultError(),
            502,
            "LLM_MALFORMED_RESULT",
        ),
        (
            "/api/drafts/text",
            {"text": "text"},
            LlmContentTooLargeError(),
            413,
            "LLM_CONTENT_TOO_LARGE",
        ),
        (
            "/api/drafts/url",
            {"url": "https://example.com/article"},
            ResearchBackendUnavailableError(),
            503,
            "RESEARCH_BACKEND_UNAVAILABLE",
        ),
        (
            "/api/drafts/url",
            {"url": "https://example.com/article"},
            ResearchTimeoutError(),
            504,
            "RESEARCH_TIMEOUT",
        ),
        (
            "/api/drafts/url",
            {"url": "https://example.com/article"},
            ResearchUpstreamError(),
            502,
            "RESEARCH_UPSTREAM_FAILURE",
        ),
        (
            "/api/drafts/url",
            {"url": "https://example.com/article"},
            ResearchMalformedResultError(),
            502,
            "RESEARCH_MALFORMED_RESULT",
        ),
        (
            "/api/drafts/url",
            {"url": "https://example.com/article"},
            ResearchContentTooLargeError(),
            413,
            "RESEARCH_CONTENT_TOO_LARGE",
        ),
    ],
)
def test_application_errors_have_safe_http_mapping(
    path: str,
    payload: dict[str, str],
    error: Exception,
    status: int,
    code: str,
) -> None:
    service = StubDraftService(error=error)

    with TestClient(create_app(draft_service=service), base_url=LOOPBACK_BASE_URL) as client:
        response = client.post(path, json=payload, headers=DRAFT_REQUEST_HEADERS)

    assert response.status_code == status
    assert response.json()["error"]["code"] == code
    assert "provider" not in response.text.lower()
    assert "secret" not in response.text.lower()
    assert "traceback" not in response.text.lower()
    assert response.headers["cache-control"] == "no-store"


def test_unexpected_runtime_error_is_generic_and_does_not_leak_details() -> None:
    service = StubDraftService(error=RuntimeError("provider secret and raw upstream body"))

    with TestClient(create_app(draft_service=service), base_url=LOOPBACK_BASE_URL) as client:
        response = client.post(
            "/api/drafts/text", json={"text": "text"}, headers=DRAFT_REQUEST_HEADERS
        )

    assert response.status_code == 500
    assert response.json() == {
        "error": {
            "code": "DRAFT_GENERATION_FAILED",
            "message": "draft generation failed",
        }
    }
    assert "provider secret" not in response.text


def test_production_composition_defers_cloudflare_config_until_real_post(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    load_calls = 0

    def fake_load() -> object:
        nonlocal load_calls
        load_calls += 1
        raise cloudflare_workers_ai.CloudflareWorkersAiConfigError("secret details")

    monkeypatch.setattr(cloudflare_workers_ai, "load_cloudflare_workers_ai_config", fake_load)
    application = create_app()

    with TestClient(application, base_url=LOOPBACK_BASE_URL) as client:
        assert client.get("/").status_code == 200
        assert client.get("/healthz").status_code == 200
        assert load_calls == 0
        response = client.post(
            "/api/drafts/text", json={"text": "text"}, headers=DRAFT_REQUEST_HEADERS
        )

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "LLM_BACKEND_UNAVAILABLE"
    assert load_calls == 1


def test_unexpected_host_is_rejected_before_draft_service() -> None:
    service = StubDraftService()

    with TestClient(create_app(draft_service=service), base_url=LOOPBACK_BASE_URL) as client:
        response = client.post(
            "/api/drafts/text",
            json={"text": "text"},
            headers={**DRAFT_REQUEST_HEADERS, "host": "unexpected.example"},
        )

    assert response.status_code == 400
    assert response.headers["cache-control"] == "no-store"
    assert service.text_calls == []
    assert service.url_calls == []


def test_loopback_hosts_are_allowed_and_no_cors_middleware_is_installed() -> None:
    application = create_app(draft_service=StubDraftService())

    with TestClient(application, base_url=LOOPBACK_BASE_URL) as client:
        response = client.get("/healthz", headers={"host": "localhost"})

    assert response.status_code == 200
    assert "access-control-allow-origin" not in response.headers
    assert all("CORS" not in repr(middleware.cls) for middleware in application.user_middleware)
