"""Focused Web Self Retrieval v1 projection and boundary tests."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

import pytest
from fastapi.testclient import TestClient

from second_brain.application.self_model import (
    DEFAULT_SELF_MODEL_POLICY,
    SelfModelDimension,
    validate_self_model_policy,
)
from second_brain.application.self_retrieval import (
    SelfContextClaim,
    SelfContextItem,
    SelfContextRequest,
    SelfContextResult,
    SelfRetrievalCurrentReadUnavailableError,
    SelfRetrievalError,
    SelfRetrievalErrorCode,
    SelfRetrievalInvalidRequestError,
    SelfRetrievalResultInvalidError,
    SelfRetrievalResultTooLargeError,
    SelfRetrievalSearchUnavailableError,
    SelfRetrievalSelfModelUnavailableError,
)
from second_brain.domain.models import NoteType
from second_brain.entrypoints.web.app import (
    MAX_RAW_SELF_RETRIEVAL_BODY_BYTES,
    SELF_RETRIEVAL_REQUEST_HEADER_NAME,
    SELF_RETRIEVAL_REQUEST_HEADER_VALUE,
    create_app,
)
from second_brain.entrypoints.web.self_retrieval import (
    LazyVaultSelfRetrievalService,
    SelfRetrievalRequestPayload,
)
from tests.conftest import create_vault, managed_note, snapshot_tree, write_note
from tests.test_web_drafts import send_raw_asgi_request

LOOPBACK_BASE_URL = "http://127.0.0.1"
SELF_RETRIEVAL_HEADERS = {
    SELF_RETRIEVAL_REQUEST_HEADER_NAME: SELF_RETRIEVAL_REQUEST_HEADER_VALUE,
}
NOTE_ID = UUID("0198f4c5-6a00-7000-8000-000000000030")
CREATED = datetime(2026, 9, 5, 12, tzinfo=UTC)
UPDATED = datetime(2026, 9, 5, 13, tzinfo=UTC)
POLICY_FINGERPRINT = validate_self_model_policy(DEFAULT_SELF_MODEL_POLICY)


def context_result(*, body: str = "Current canonical body.") -> SelfContextResult:
    """Return one representative exact core result."""

    claim = SelfContextClaim(
        dimension=SelfModelDimension.PREFERENCE,
        claim="I prefer focused work.",
        supporting_note_ids=(NOTE_ID,),
        derivation_version=DEFAULT_SELF_MODEL_POLICY.derivation_version,
        policy_fingerprint=POLICY_FINGERPRINT,
    )
    item = SelfContextItem(
        note_id=NOTE_ID,
        note_type=NoteType.ZETTEL,
        title="Current note",
        body=body,
        tags=("python", "testing"),
        created=CREATED,
        updated=UPDATED,
        search_rank=1,
        self_model_claims=(claim,),
    )
    content_bytes = len("\n".join((item.title, item.body, *item.tags)).encode("utf-8"))
    return SelfContextResult(
        items=(item,),
        candidate_count=1,
        included_count=1,
        excluded_count=0,
        exclusions=(),
        truncated=False,
        content_bytes=content_bytes,
        self_model_derivation_version=DEFAULT_SELF_MODEL_POLICY.derivation_version,
        self_model_policy_fingerprint=POLICY_FINGERPRINT,
    )


class RecordingSelfRetrievalService:
    """Record the exact core request and return a bounded result."""

    def __init__(
        self,
        result: SelfContextResult | None = None,
        error: Exception | None = None,
    ) -> None:
        self.result = result or context_result()
        self.error = error
        self.requests: list[SelfContextRequest] = []

    def build(self, request: SelfContextRequest) -> SelfContextResult:
        self.requests.append(request)
        if self.error is not None:
            raise self.error
        return self.result


def test_self_retrieval_request_payload_is_strict_and_has_only_core_fields() -> None:
    assert set(SelfRetrievalRequestPayload.model_fields) == {
        "query",
        "limit",
        "max_content_bytes",
    }
    with pytest.raises(ValueError):
        SelfRetrievalRequestPayload.model_validate({"query": "x", "limit": True})
    with pytest.raises(ValueError):
        SelfRetrievalRequestPayload.model_validate({"query": "x", "policy": "custom"})


def test_self_retrieval_success_preserves_current_core_shape_and_safe_fields() -> None:
    service = RecordingSelfRetrievalService()
    with TestClient(
        create_app(self_retrieval_service=service), base_url=LOOPBACK_BASE_URL
    ) as client:
        response = client.post(
            "/api/self-retrieval",
            headers=SELF_RETRIEVAL_HEADERS,
            json={"query": "fastapi", "limit": 7, "max_content_bytes": 4096},
        )

    assert response.status_code == 200
    assert service.requests == [
        SelfContextRequest(query="fastapi", limit=7, max_content_bytes=4096)
    ]
    assert response.headers["cache-control"] == "no-store"
    assert "access-control-allow-origin" not in response.headers
    body = response.json()
    assert set(body) == {
        "items",
        "candidate_count",
        "included_count",
        "excluded_count",
        "exclusions",
        "truncated",
        "content_bytes",
        "self_model_derivation_version",
        "self_model_policy_fingerprint",
    }
    assert body["items"][0] == {
        "note_id": str(NOTE_ID),
        "note_type": "zettel",
        "title": "Current note",
        "body": "Current canonical body.",
        "tags": ["python", "testing"],
        "created": CREATED.isoformat().replace("+00:00", "Z"),
        "updated": UPDATED.isoformat().replace("+00:00", "Z"),
        "search_rank": 1,
        "self_model_claims": [
            {
                "dimension": "preference",
                "claim": "I prefer focused work.",
                "supporting_note_ids": [str(NOTE_ID)],
                "derivation_version": "self-model-derivation-v1",
                "policy_fingerprint": POLICY_FINGERPRINT,
            }
        ],
    }
    assert "relative_path" not in body["items"][0]
    assert "snippet" not in body["items"][0]
    assert "score" not in response.text
    assert "absolute_path" not in response.text


@pytest.mark.parametrize(
    "payload",
    [
        {"query": "   "},
        {"query": "fastapi", "limit": True},
        {"query": "fastapi", "limit": 0},
        {"query": "fastapi", "max_content_bytes": 0},
        {"query": "fastapi", "max_content_bytes": 65537},
        {"query": "fastapi", "authority": str(NOTE_ID)},
    ],
)
def test_self_retrieval_invalid_request_is_400_before_service(payload: dict[str, object]) -> None:
    service = RecordingSelfRetrievalService()
    with TestClient(
        create_app(self_retrieval_service=service), base_url=LOOPBACK_BASE_URL
    ) as client:
        response = client.post(
            "/api/self-retrieval",
            headers=SELF_RETRIEVAL_HEADERS,
            json=payload,
        )

    assert response.status_code == 400
    assert response.json() == {
        "error": {
            "code": "SELF_RETRIEVAL_INVALID_REQUEST",
            "message": "Запрос сбора контекста не прошёл проверку",
        }
    }
    assert service.requests == []
    assert "body" not in response.text


@pytest.mark.parametrize(
    "error,status,code",
    [
        (SelfRetrievalInvalidRequestError(), 400, SelfRetrievalErrorCode.INVALID_REQUEST.value),
        (
            SelfRetrievalSearchUnavailableError(),
            503,
            SelfRetrievalErrorCode.SEARCH_UNAVAILABLE.value,
        ),
        (
            SelfRetrievalCurrentReadUnavailableError(),
            503,
            SelfRetrievalErrorCode.CURRENT_READ_UNAVAILABLE.value,
        ),
        (
            SelfRetrievalSelfModelUnavailableError(),
            503,
            SelfRetrievalErrorCode.SELF_MODEL_UNAVAILABLE.value,
        ),
        (SelfRetrievalResultInvalidError(), 500, SelfRetrievalErrorCode.RESULT_INVALID.value),
        (SelfRetrievalResultTooLargeError(), 413, SelfRetrievalErrorCode.RESULT_TOO_LARGE.value),
    ],
)
def test_self_retrieval_core_errors_are_safe_and_have_no_partial_result(
    error: Exception,
    status: int,
    code: str,
) -> None:
    service = RecordingSelfRetrievalService(error=error)
    with TestClient(
        create_app(self_retrieval_service=service), base_url=LOOPBACK_BASE_URL
    ) as client:
        response = client.post(
            "/api/self-retrieval",
            headers=SELF_RETRIEVAL_HEADERS,
            json={"query": "fastapi"},
        )

    assert response.status_code == status
    assert response.headers["cache-control"] == "no-store"
    assert response.json()["error"]["code"] == code
    assert "absolute" not in response.text.lower()
    assert "exception" not in response.text.lower()
    assert "items" not in response.text


@pytest.mark.parametrize(
    "headers",
    [
        {},
        {SELF_RETRIEVAL_REQUEST_HEADER_NAME: "search-v1"},
        {
            SELF_RETRIEVAL_REQUEST_HEADER_NAME: SELF_RETRIEVAL_REQUEST_HEADER_VALUE,
            "host": "evil.test",
        },
        {
            SELF_RETRIEVAL_REQUEST_HEADER_NAME: SELF_RETRIEVAL_REQUEST_HEADER_VALUE,
            "origin": "https://evil.test",
        },
    ],
)
def test_self_retrieval_boundary_rejects_wrong_purpose_host_origin_before_service(
    headers: dict[str, str],
) -> None:
    service = RecordingSelfRetrievalService()
    with TestClient(
        create_app(self_retrieval_service=service), base_url=LOOPBACK_BASE_URL
    ) as client:
        response = client.post("/api/self-retrieval", headers=headers, json={"query": "x"})

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "SELF_RETRIEVAL_INVALID_REQUEST"
    assert service.requests == []


def test_self_retrieval_boundary_requires_json_and_post() -> None:
    service = RecordingSelfRetrievalService()
    with TestClient(
        create_app(self_retrieval_service=service), base_url=LOOPBACK_BASE_URL
    ) as client:
        non_json = client.post(
            "/api/self-retrieval",
            headers={**SELF_RETRIEVAL_HEADERS, "content-type": "text/plain"},
            content='{"query":"x"}',
        )
        get_response = client.get("/api/self-retrieval")

    assert non_json.status_code == 400
    assert non_json.json()["error"]["code"] == "SELF_RETRIEVAL_INVALID_REQUEST"
    assert get_response.status_code == 405
    assert get_response.headers["cache-control"] == "no-store"
    assert service.requests == []


def test_self_retrieval_boundary_accepts_same_origin_json_with_utf8_charset() -> None:
    service = RecordingSelfRetrievalService()
    with TestClient(
        create_app(self_retrieval_service=service), base_url=LOOPBACK_BASE_URL
    ) as client:
        response = client.post(
            "/api/self-retrieval",
            headers={
                **SELF_RETRIEVAL_HEADERS,
                "content-type": "application/json; charset=utf-8",
                "origin": LOOPBACK_BASE_URL,
            },
            content='{"query":"x"}',
        )

    assert response.status_code == 200
    assert service.requests == [SelfContextRequest(query="x")]


def test_self_retrieval_declared_raw_body_cap_applies_before_parser() -> None:
    service = RecordingSelfRetrievalService()
    headers = {
        "host": "127.0.0.1",
        "content-type": "application/json",
        SELF_RETRIEVAL_REQUEST_HEADER_NAME: SELF_RETRIEVAL_REQUEST_HEADER_VALUE,
        "content-length": str(MAX_RAW_SELF_RETRIEVAL_BODY_BYTES + 1),
    }
    status, response_headers, body, receive_calls = send_raw_asgi_request(
        create_app(self_retrieval_service=service),
        path="/api/self-retrieval",
        headers=headers,
        body_chunks=(b"{}",),
    )

    assert status == 413
    assert response_headers["cache-control"] == "no-store"
    assert b"SELF_RETRIEVAL_RESULT_TOO_LARGE" in body
    assert receive_calls == 0
    assert service.requests == []


def test_self_retrieval_actual_raw_body_cap_rejects_misleading_length() -> None:
    service = RecordingSelfRetrievalService()
    headers = {
        "host": "127.0.0.1",
        "content-type": "application/json",
        SELF_RETRIEVAL_REQUEST_HEADER_NAME: SELF_RETRIEVAL_REQUEST_HEADER_VALUE,
        "content-length": "1",
    }
    status, response_headers, body, receive_calls = send_raw_asgi_request(
        create_app(self_retrieval_service=service),
        path="/api/self-retrieval",
        headers=headers,
        body_chunks=(b"{}", b"x" * MAX_RAW_SELF_RETRIEVAL_BODY_BYTES),
    )

    assert status == 413
    assert response_headers["cache-control"] == "no-store"
    assert b"SELF_RETRIEVAL_RESULT_TOO_LARGE" in body
    assert receive_calls == 2
    assert service.requests == []


def test_self_retrieval_is_hidden_from_openapi_and_has_no_cors() -> None:
    with TestClient(
        create_app(self_retrieval_service=RecordingSelfRetrievalService()),
        base_url=LOOPBACK_BASE_URL,
    ) as client:
        openapi = client.get("/openapi.json")
        response = client.post(
            "/api/self-retrieval",
            headers=SELF_RETRIEVAL_HEADERS,
            json={"query": "x"},
        )

    assert openapi.status_code == 404
    assert response.status_code == 200
    assert "access-control-allow-origin" not in response.headers


def test_lazy_self_retrieval_service_rebuilds_current_body_without_startup_vault_read(
    tmp_path: Path,
) -> None:
    vault = create_vault(tmp_path / "vault")
    note_path = write_note(
        vault,
        "10 Projects/Current.md",
        managed_note(str(NOTE_ID)).replace("Текст заметки.", "fastapi first body."),
    )
    before_request = snapshot_tree(vault)
    service = LazyVaultSelfRetrievalService(vault_path_override=str(vault))
    app = create_app(vault_path_override=str(vault))
    assert snapshot_tree(vault) == before_request

    with TestClient(app, base_url=LOOPBACK_BASE_URL) as client:
        first = client.post(
            "/api/self-retrieval",
            headers=SELF_RETRIEVAL_HEADERS,
            json={"query": "fastapi"},
        )
        note_path.write_text(
            managed_note(str(NOTE_ID)).replace("Текст заметки.", "fastapi current body."),
            encoding="utf-8",
        )
        second = client.post(
            "/api/self-retrieval",
            headers=SELF_RETRIEVAL_HEADERS,
            json={"query": "fastapi"},
        )

    assert service.build(SelfContextRequest(query="fastapi")).items[0].body == (
        "# Тестовая заметка\n\nfastapi current body.\n"
    )
    assert first.status_code == 200
    assert first.json()["items"][0]["body"] == "# Тестовая заметка\n\nfastapi first body.\n"
    assert second.status_code == 200
    assert second.json()["items"][0]["body"] == "# Тестовая заметка\n\nfastapi current body.\n"
    assert snapshot_tree(vault) != before_request


def test_self_retrieval_ui_is_explicit_safe_and_keeps_server_order() -> None:
    root = Path("src/second_brain/entrypoints/web/static")
    html = (root / "index.html").read_text(encoding="utf-8")
    javascript = (root / "self-retrieval.js").read_text(encoding="utf-8")
    assert (
        html.index('id="self-model"')
        < html.index('id="self-retrieval"')
        < html.index('id="search"')
    )
    assert "/static/self-retrieval.js" in html
    assert "data-self-retrieval-form" in html
    assert "Current reread body" in javascript
    assert "Supporting UUIDs" in javascript
    assert "textContent" in javascript
    assert ".sort(" not in javascript
    assert "innerHTML" not in javascript
    assert "localStorage" not in javascript
    assert "sessionStorage" not in javascript
    assert "indexedDB" not in javascript
    assert "setInterval" not in javascript
    assert 'X-Second-Brain-Request": "self-retrieval-v1"' in javascript
    assert "loadSelfRetrieval();" not in javascript


def test_self_retrieval_error_base_keeps_safe_code() -> None:
    error = SelfRetrievalError("SELF_RETRIEVAL_RESULT_INVALID", "leak this")
    assert error.code == "SELF_RETRIEVAL_RESULT_INVALID"
    assert error.message == "self retrieval result failed validation"
