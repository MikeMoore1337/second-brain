"""Deterministic Web API tests for read-only text and public WEB drafts."""

from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

from second_brain.adapters.llm import cloudflare_workers_ai
from second_brain.application.llm import LlmGateway, LlmRequest, NoteDraft
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
from second_brain.entrypoints.web.app import create_app
from second_brain.entrypoints.web.drafts import CAPTURE_INSTRUCTION, GatewayDraftService

LOOPBACK_BASE_URL = "http://127.0.0.1"


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
        response = client.post("/api/drafts/text", json={"text": user_text})

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
        response = client.post("/api/drafts/url", json={"url": source.uri})

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
        response = client.post(path, json=payload)

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
        form_response = client.post("/api/drafts/text", data={"text": "not JSON"})
        blank_text = client.post("/api/drafts/text", json={"text": " \n "})
        blank_url = client.post("/api/drafts/url", json={"url": "  "})

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
        response = client.post(path, json=payload)

    assert response.status_code == status
    assert response.json()["error"]["code"] == code
    assert "provider" not in response.text.lower()
    assert "secret" not in response.text.lower()
    assert "traceback" not in response.text.lower()
    assert response.headers["cache-control"] == "no-store"


def test_unexpected_runtime_error_is_generic_and_does_not_leak_details() -> None:
    service = StubDraftService(error=RuntimeError("provider secret and raw upstream body"))

    with TestClient(create_app(draft_service=service), base_url=LOOPBACK_BASE_URL) as client:
        response = client.post("/api/drafts/text", json={"text": "text"})

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
        response = client.post("/api/drafts/text", json={"text": "text"})

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "LLM_BACKEND_UNAVAILABLE"
    assert load_calls == 1


def test_unexpected_host_is_rejected_before_draft_service() -> None:
    service = StubDraftService()

    with TestClient(create_app(draft_service=service), base_url=LOOPBACK_BASE_URL) as client:
        response = client.post(
            "/api/drafts/text",
            json={"text": "text"},
            headers={"host": "unexpected.example"},
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
