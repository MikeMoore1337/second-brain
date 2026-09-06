"""Focused Web Self Model v1 projection and boundary tests."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

import pytest
from fastapi.testclient import TestClient

from second_brain.application.self_model import (
    DEFAULT_SELF_MODEL_POLICY,
    SelfModelClaim,
    SelfModelConfidence,
    SelfModelConfidenceState,
    SelfModelDimension,
    SelfModelError,
    SelfModelEvidenceRef,
    SelfModelInvalidClockError,
    SelfModelInvalidRequestError,
    SelfModelPolicyUnavailableError,
    SelfModelRequest,
    SelfModelResult,
    SelfModelResultInvalidError,
    SelfModelResultTooLargeError,
    SelfModelTemporalContext,
    SelfModelVaultUnavailableError,
    validate_self_model_policy,
)
from second_brain.domain.models import EvidenceAtPrecision, EvidenceKind, SelfKind
from second_brain.entrypoints.web.app import (
    MAX_RAW_SELF_MODEL_BODY_BYTES,
    SELF_MODEL_REQUEST_HEADER_NAME,
    SELF_MODEL_REQUEST_HEADER_VALUE,
    create_app,
)
from second_brain.entrypoints.web.self_model import (
    LazyVaultSelfModelService,
    SelfModelRequestPayload,
)
from tests.conftest import create_vault, snapshot_tree, write_note
from tests.test_web_drafts import send_raw_asgi_request

LOOPBACK_BASE_URL = "http://127.0.0.1"
SELF_MODEL_HEADERS = {
    SELF_MODEL_REQUEST_HEADER_NAME: SELF_MODEL_REQUEST_HEADER_VALUE,
}
PREFERENCE_ID = UUID("0198f4c5-6a00-7000-8000-000000000030")
GENERATED_AT = datetime(2026, 9, 5, 20, 0, tzinfo=UTC)
EVIDENCE_AT = datetime(2026, 9, 5, 12, 30, tzinfo=UTC)


def self_model_result() -> SelfModelResult:
    """Return one representative exact v1 core result."""

    evidence = SelfModelEvidenceRef(
        note_id=PREFERENCE_ID,
        evidence_kind=EvidenceKind.EXPLICIT_USER_FACT,
        self_kind=SelfKind.PREFERENCE,
        domain="work",
        evidence_at=EVIDENCE_AT,
        evidence_at_precision=EvidenceAtPrecision.EXACT,
    )
    claim = SelfModelClaim(
        dimension=SelfModelDimension.PREFERENCE,
        claim="I prefer focused work.",
        domain="work",
        supporting_evidence=(evidence,),
        contradicting_evidence=(),
        contextual_evidence=(),
        confidence=SelfModelConfidence(
            state=SelfModelConfidenceState.NOT_ASSESSED,
            score=None,
            policy_version=DEFAULT_SELF_MODEL_POLICY.confidence_policy,
            supporting_evidence_count=1,
            contradicting_evidence_count=0,
            unknown_time_count=0,
        ),
        temporal_context=SelfModelTemporalContext(
            earliest_known_evidence_at=EVIDENCE_AT,
            latest_known_evidence_at=EVIDENCE_AT,
            known_evidence_count=1,
            unknown_evidence_count=0,
        ),
        generated_at=GENERATED_AT,
        derivation_version=DEFAULT_SELF_MODEL_POLICY.derivation_version,
    )
    return SelfModelResult(
        claims=(claim,),
        eligible_evidence_count=1,
        represented_evidence_count=1,
        generated_at=GENERATED_AT,
        derivation_version=DEFAULT_SELF_MODEL_POLICY.derivation_version,
        policy_fingerprint=validate_self_model_policy(DEFAULT_SELF_MODEL_POLICY),
    )


class RecordingSelfModelService:
    """Record the exact application request and return a bounded result."""

    def __init__(
        self,
        result: SelfModelResult | None = None,
        error: Exception | None = None,
    ) -> None:
        self.result = result or self_model_result()
        self.error = error
        self.requests: list[SelfModelRequest] = []

    def build(self, request: SelfModelRequest) -> SelfModelResult:
        self.requests.append(request)
        if self.error is not None:
            raise self.error
        return self.result


def test_self_model_request_payload_is_strict_and_forbids_new_controls() -> None:
    assert set(SelfModelRequestPayload.model_fields) == {
        "max_claims",
        "max_evidence_refs_per_claim",
    }
    with pytest.raises(ValueError):
        SelfModelRequestPayload.model_validate({"max_claims": True})
    with pytest.raises(ValueError):
        SelfModelRequestPayload.model_validate({"policy": "custom"})


def test_self_model_success_preserves_core_order_and_safe_shape() -> None:
    service = RecordingSelfModelService()
    with TestClient(create_app(self_model_service=service), base_url=LOOPBACK_BASE_URL) as client:
        response = client.post("/api/self-model", headers=SELF_MODEL_HEADERS, json={})

    assert response.status_code == 200
    assert service.requests == [SelfModelRequest()]
    assert response.headers["cache-control"] == "no-store"
    assert "access-control-allow-origin" not in response.headers
    body = response.json()
    assert set(body) == {
        "claims",
        "eligible_evidence_count",
        "represented_evidence_count",
        "generated_at",
        "derivation_version",
        "policy_fingerprint",
    }
    assert body["eligible_evidence_count"] == 1
    assert body["represented_evidence_count"] == 1
    assert body["claims"][0] == {
        "dimension": "preference",
        "claim": "I prefer focused work.",
        "domain": "work",
        "supporting_evidence": [
            {
                "id": str(PREFERENCE_ID),
                "evidence_kind": "explicit_user_fact",
                "self_kind": "preference",
                "domain": "work",
                "evidence_at": EVIDENCE_AT.isoformat().replace("+00:00", "Z"),
                "evidence_at_precision": "exact",
                "related_note_ids": [],
            }
        ],
        "contradicting_evidence": [],
        "contextual_evidence": [],
        "confidence": {
            "state": "not_assessed",
            "score": None,
            "policy_version": "unassessed-v1",
            "supporting_evidence_count": 1,
            "contradicting_evidence_count": 0,
            "unknown_time_count": 0,
        },
        "temporal_context": {
            "earliest_known_evidence_at": EVIDENCE_AT.isoformat().replace("+00:00", "Z"),
            "latest_known_evidence_at": EVIDENCE_AT.isoformat().replace("+00:00", "Z"),
            "known_evidence_count": 1,
            "unknown_evidence_count": 0,
        },
        "generated_at": GENERATED_AT.isoformat().replace("+00:00", "Z"),
        "derivation_version": "self-model-derivation-v1",
        "status": None,
    }
    assert "body" not in body["claims"][0]
    assert "front_matter" not in body["claims"][0]
    assert "absolute_path" not in body["claims"][0]


def test_self_model_request_values_are_passed_to_core_without_client_policy() -> None:
    service = RecordingSelfModelService()
    with TestClient(create_app(self_model_service=service), base_url=LOOPBACK_BASE_URL) as client:
        response = client.post(
            "/api/self-model",
            headers=SELF_MODEL_HEADERS,
            json={"max_claims": 7, "max_evidence_refs_per_claim": 9},
        )

    assert response.status_code == 200
    assert service.requests == [SelfModelRequest(max_claims=7, max_evidence_refs_per_claim=9)]


@pytest.mark.parametrize(
    "payload",
    [
        {"max_claims": True},
        {"max_evidence_refs_per_claim": "100"},
        {"max_claims": 201},
        {"max_evidence_refs_per_claim": 0},
        {"policy": "default"},
    ],
)
def test_self_model_invalid_request_is_400_and_never_partial(
    payload: dict[str, object],
) -> None:
    service = RecordingSelfModelService()
    with TestClient(create_app(self_model_service=service), base_url=LOOPBACK_BASE_URL) as client:
        response = client.post("/api/self-model", headers=SELF_MODEL_HEADERS, json=payload)

    assert response.status_code == 400
    assert response.json() == {
        "error": {
            "code": "SELF_MODEL_INVALID_REQUEST",
            "message": "self model request failed validation",
        }
    }
    assert "claims" not in response.text
    assert service.requests == []


@pytest.mark.parametrize(
    ("error", "status", "code"),
    [
        (SelfModelError("SELF_MODEL_EVIDENCE_INVALID"), 409, "SELF_MODEL_EVIDENCE_INVALID"),
        (SelfModelVaultUnavailableError(), 503, "SELF_MODEL_VAULT_UNAVAILABLE"),
        (SelfModelInvalidClockError(), 500, "SELF_MODEL_INVALID_CLOCK"),
        (SelfModelPolicyUnavailableError(), 500, "SELF_MODEL_POLICY_UNAVAILABLE"),
        (SelfModelResultInvalidError(), 500, "SELF_MODEL_RESULT_INVALID"),
        (SelfModelResultTooLargeError(), 413, "SELF_MODEL_RESULT_TOO_LARGE"),
        (SelfModelInvalidRequestError(), 400, "SELF_MODEL_INVALID_REQUEST"),
    ],
)
def test_self_model_core_errors_are_safe_and_have_no_partial_result(
    error: Exception,
    status: int,
    code: str,
) -> None:
    service = RecordingSelfModelService(error=error)
    with TestClient(create_app(self_model_service=service), base_url=LOOPBACK_BASE_URL) as client:
        response = client.post("/api/self-model", headers=SELF_MODEL_HEADERS, json={})

    assert response.status_code == status
    assert response.headers["cache-control"] == "no-store"
    assert response.json()["error"]["code"] == code
    assert "absolute" not in response.text.lower()
    assert "exception" not in response.text.lower()
    assert "claims" not in response.text


@pytest.mark.parametrize(
    "headers",
    [
        {},
        {SELF_MODEL_REQUEST_HEADER_NAME: "timeline-v1"},
        {SELF_MODEL_REQUEST_HEADER_NAME: SELF_MODEL_REQUEST_HEADER_VALUE, "host": "evil.test"},
        {
            SELF_MODEL_REQUEST_HEADER_NAME: SELF_MODEL_REQUEST_HEADER_VALUE,
            "origin": "https://evil.test",
        },
    ],
)
def test_self_model_boundary_rejects_wrong_purpose_host_origin_before_service(
    headers: dict[str, str],
) -> None:
    service = RecordingSelfModelService()
    with TestClient(create_app(self_model_service=service), base_url=LOOPBACK_BASE_URL) as client:
        response = client.post("/api/self-model", headers=headers, json={})

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "SELF_MODEL_INVALID_REQUEST"
    assert service.requests == []


def test_self_model_boundary_requires_json_and_post() -> None:
    service = RecordingSelfModelService()
    with TestClient(create_app(self_model_service=service), base_url=LOOPBACK_BASE_URL) as client:
        non_json = client.post(
            "/api/self-model",
            headers={**SELF_MODEL_HEADERS, "content-type": "text/plain"},
            content="{}",
        )
        get_response = client.get("/api/self-model")

    assert non_json.status_code == 400
    assert non_json.json()["error"]["code"] == "SELF_MODEL_INVALID_REQUEST"
    assert get_response.status_code == 405
    assert get_response.headers["cache-control"] == "no-store"
    assert service.requests == []


def test_self_model_boundary_accepts_same_origin_json_with_utf8_charset() -> None:
    service = RecordingSelfModelService()
    with TestClient(create_app(self_model_service=service), base_url=LOOPBACK_BASE_URL) as client:
        response = client.post(
            "/api/self-model",
            headers={
                **SELF_MODEL_HEADERS,
                "content-type": "application/json; charset=utf-8",
                "origin": LOOPBACK_BASE_URL,
            },
            content="{}",
        )

    assert response.status_code == 200
    assert service.requests == [SelfModelRequest()]


def test_self_model_declared_raw_body_cap_applies_before_fastapi_parser() -> None:
    service = RecordingSelfModelService()
    headers = {
        "host": "127.0.0.1",
        "content-type": "application/json",
        SELF_MODEL_REQUEST_HEADER_NAME: SELF_MODEL_REQUEST_HEADER_VALUE,
        "content-length": str(MAX_RAW_SELF_MODEL_BODY_BYTES + 1),
    }
    status, response_headers, body, receive_calls = send_raw_asgi_request(
        create_app(self_model_service=service),
        path="/api/self-model",
        headers=headers,
        body_chunks=(b"{}",),
    )

    assert status == 413
    assert response_headers["cache-control"] == "no-store"
    assert b"SELF_MODEL_CONTENT_TOO_LARGE" in body
    assert receive_calls == 0
    assert service.requests == []


def test_self_model_actual_raw_body_cap_rejects_misleading_length() -> None:
    service = RecordingSelfModelService()
    headers = {
        "host": "127.0.0.1",
        "content-type": "application/json",
        SELF_MODEL_REQUEST_HEADER_NAME: SELF_MODEL_REQUEST_HEADER_VALUE,
        "content-length": "1",
    }
    status, response_headers, body, receive_calls = send_raw_asgi_request(
        create_app(self_model_service=service),
        path="/api/self-model",
        headers=headers,
        body_chunks=(b"{}", b"x" * MAX_RAW_SELF_MODEL_BODY_BYTES),
    )

    assert status == 413
    assert response_headers["cache-control"] == "no-store"
    assert b"SELF_MODEL_CONTENT_TOO_LARGE" in body
    assert receive_calls == 2
    assert service.requests == []


def test_self_model_is_not_in_openapi_and_success_has_no_cors() -> None:
    with TestClient(
        create_app(self_model_service=RecordingSelfModelService()),
        base_url=LOOPBACK_BASE_URL,
    ) as client:
        openapi = client.get("/openapi.json")
        response = client.post("/api/self-model", headers=SELF_MODEL_HEADERS, json={})

    assert openapi.status_code == 404
    assert response.status_code == 200
    assert "access-control-allow-origin" not in response.headers


def _personal_memory_note(note_id: str, *, self_kind: str, evidence_at: str) -> str:
    """Return one minimal enrolled direct assertion for lazy rebuild tests."""

    return f"""---
id: {note_id}
type: zettel
created: 2026-09-05T18:00:00+03:00
tags: []
second_brain_personal_memory: 1
evidence_kind: explicit_user_fact
self_kind: {self_kind}
evidence_at: \"{evidence_at}\"
evidence_at_precision: exact
---
Canonical direct assertion body.
"""


def test_lazy_self_model_service_resolves_config_only_on_request_and_rebuilds_current_vault(
    tmp_path: Path,
) -> None:
    vault = create_vault(tmp_path / "vault")
    write_note(
        vault,
        "10 Projects/First.md",
        _personal_memory_note(
            "0198f4c5-6a00-7000-8000-000000000040",
            self_kind="preference",
            evidence_at="2026-09-05T10:00:00Z",
        ),
    )
    before_startup = snapshot_tree(vault)
    service = LazyVaultSelfModelService(vault_path_override=str(vault))
    app = create_app(vault_path_override=str(vault))
    assert snapshot_tree(vault) == before_startup

    with TestClient(app, base_url=LOOPBACK_BASE_URL) as client:
        first = client.post("/api/self-model", headers=SELF_MODEL_HEADERS, json={})
        assert snapshot_tree(vault) == before_startup
        write_note(
            vault,
            "10 Projects/Second.md",
            _personal_memory_note(
                "0198f4c5-6a00-7000-8000-000000000041",
                self_kind="goal",
                evidence_at="2026-09-05T11:00:00Z",
            ),
        )
        second = client.post("/api/self-model", headers=SELF_MODEL_HEADERS, json={})

    assert service.build(SelfModelRequest()).eligible_evidence_count == 2
    assert first.status_code == 200
    assert len(first.json()["claims"]) == 1
    assert second.status_code == 200
    assert len(second.json()["claims"]) == 2
    assert snapshot_tree(vault) != before_startup


def test_self_model_ui_is_explicit_safe_and_exposes_audit_fields() -> None:
    root = Path("src/second_brain/entrypoints/web/static")
    html = (root / "index.html").read_text(encoding="utf-8")
    javascript = (root / "self-model.js").read_text(encoding="utf-8")
    assert html.index('id="timeline"') < html.index('id="self-model"') < html.index('id="search"')
    assert "/static/self-model.js" in html
    assert "data-self-model-refresh" in html
    assert "data-self-model-list" in html
    assert "Canonical UUID" in javascript
    assert "policy_fingerprint" in javascript
    assert "textContent" in javascript
    assert ".sort(" not in javascript
    assert "innerHTML" not in javascript
    assert "localStorage" not in javascript
    assert "sessionStorage" not in javascript
    assert "indexedDB" not in javascript
    assert "setInterval" not in javascript
    assert 'X-Second-Brain-Request": "self-model-v1"' in javascript
    assert "loadSelfModel();" not in javascript
