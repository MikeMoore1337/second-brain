"""Focused Web projection and boundary tests for Simulate Me v1."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

import pytest
from fastapi.testclient import TestClient

from second_brain.application.simulate_me import (
    DERIVATION_VERSION,
    POLICY_FINGERPRINT,
    POLICY_ID,
    SimulateMeAbstentionCode,
    SimulateMeDimension,
    SimulateMeError,
    SimulateMeEvidenceRef,
    SimulateMeInvalidRequestError,
    SimulateMeOption,
    SimulateMeRequest,
    SimulateMeResult,
    SimulateMeResultKind,
)
from second_brain.config import ConfigurationError
from second_brain.entrypoints.web.app import (
    MAX_RAW_SIMULATE_ME_BODY_BYTES,
    SIMULATE_ME_REQUEST_HEADER_NAME,
    SIMULATE_ME_REQUEST_HEADER_VALUE,
    create_app,
)
from second_brain.entrypoints.web.simulate_me import (
    SimulateMeRequestPayload,
    simulate_me_request,
)
from tests.test_web_drafts import send_raw_asgi_request

LOOPBACK_BASE_URL = "http://127.0.0.1"
EVIDENCE_AT = datetime(2026, 9, 5, 12, 0, tzinfo=UTC)
NOTE_ID = UUID("0198f4c5-6a00-7000-8000-000000000101")


def _result(*, claim: str | None = "A") -> SimulateMeResult:
    if claim is None:
        return SimulateMeResult(
            kind=SimulateMeResultKind.ABSTENTION,
            selected_option=None,
            evidence_refs=(),
            contextual_evidence_refs=(),
            temporal_caveats=(),
            abstention_code=SimulateMeAbstentionCode.NO_MATCHING_EVIDENCE,
            derivation_version=DERIVATION_VERSION,
            policy_id=POLICY_ID,
            policy_fingerprint=POLICY_FINGERPRINT,
        )
    evidence = SimulateMeEvidenceRef(
        claim_id=NOTE_ID,
        dimension=SimulateMeDimension.PREFERENCE,
        note_ids=(NOTE_ID,),
        evidence_at=EVIDENCE_AT,
    )
    return SimulateMeResult(
        kind=SimulateMeResultKind.PREDICTION,
        selected_option=SimulateMeOption("a", claim),
        evidence_refs=(evidence,),
        contextual_evidence_refs=(),
        temporal_caveats=(),
        abstention_code=None,
        derivation_version=DERIVATION_VERSION,
        policy_id=POLICY_ID,
        policy_fingerprint=POLICY_FINGERPRINT,
    )


class RecordingSimulateMeService:
    """Record exact core requests and return a deterministic fixture result."""

    def __init__(
        self,
        result: SimulateMeResult | None = None,
        error: Exception | None = None,
    ) -> None:
        self.result = result
        self.error = error
        self.requests: list[SimulateMeRequest] = []

    def build(self, request: SimulateMeRequest) -> SimulateMeResult:
        self.requests.append(request)
        if self.error is not None:
            raise self.error
        assert self.result is not None
        return self.result


def _headers() -> dict[str, str]:
    return {
        SIMULATE_ME_REQUEST_HEADER_NAME: SIMULATE_ME_REQUEST_HEADER_VALUE,
    }


def _request_payload() -> dict[str, object]:
    return {"query": "literal task", "options": [{"id": "a", "label": "A"}]}


def test_simulate_me_payload_is_strict_and_converts_only_core_fields() -> None:
    payload = SimulateMeRequestPayload.model_validate(_request_payload())

    assert set(SimulateMeRequestPayload.model_fields) == {"query", "options"}
    assert simulate_me_request(payload) == SimulateMeRequest(
        query="literal task",
        options=(SimulateMeOption("a", "A"),),
    )


@pytest.mark.parametrize(
    "payload",
    [
        {"query": "task", "options": [{"id": "a", "label": "A", "score": 1}]},
        {"query": "task", "options": [{"id": "a", "label": 7}]},
        {"query": "task", "options": []},
    ],
)
def test_simulate_me_invalid_payload_is_400_before_service(payload: dict[str, object]) -> None:
    service = RecordingSimulateMeService(result=_result())
    with TestClient(create_app(simulate_me_service=service), base_url=LOOPBACK_BASE_URL) as client:
        response = client.post("/api/simulate-me", headers=_headers(), json=payload)

    assert response.status_code == 400
    assert response.json() == {
        "error": {
            "code": "SIMULATE_ME_INVALID_REQUEST",
            "message": "Запрос прогноза не прошёл проверку",
        }
    }
    assert service.requests == []


def test_simulate_me_prediction_is_exact_read_only_projection() -> None:
    service = RecordingSimulateMeService(result=_result())
    with TestClient(create_app(simulate_me_service=service), base_url=LOOPBACK_BASE_URL) as client:
        response = client.post("/api/simulate-me", headers=_headers(), json=_request_payload())

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert "access-control-allow-origin" not in response.headers
    assert service.requests == [
        SimulateMeRequest(
            query="literal task",
            options=(SimulateMeOption("a", "A"),),
        )
    ]
    body = response.json()
    assert set(body) == {
        "kind",
        "selected_option",
        "evidence_refs",
        "contextual_evidence_refs",
        "temporal_caveats",
        "abstention_code",
        "derivation_version",
        "policy_id",
        "policy_fingerprint",
    }
    assert body["kind"] == "prediction"
    assert body["selected_option"] == {"id": "a", "label": "A"}
    assert body["evidence_refs"][0]["claim_id"] == str(NOTE_ID)
    assert body["contextual_evidence_refs"] == []
    assert body["temporal_caveats"] == []
    assert body["abstention_code"] is None
    for forbidden in ("confidence", "score", "recommendation", "advice", "best", "optimal"):
        assert forbidden not in body


def test_simulate_me_abstention_is_preserved_without_client_inference() -> None:
    service = RecordingSimulateMeService(result=_result(claim=None))
    with TestClient(create_app(simulate_me_service=service), base_url=LOOPBACK_BASE_URL) as client:
        response = client.post("/api/simulate-me", headers=_headers(), json=_request_payload())

    assert response.status_code == 200
    body = response.json()
    assert body["kind"] == "abstention"
    assert body["selected_option"] is None
    assert body["abstention_code"] == SimulateMeAbstentionCode.NO_MATCHING_EVIDENCE.value


@pytest.mark.parametrize(
    "headers",
    [
        {},
        {SIMULATE_ME_REQUEST_HEADER_NAME: "self-model-v1"},
        {SIMULATE_ME_REQUEST_HEADER_NAME: SIMULATE_ME_REQUEST_HEADER_VALUE, "host": "evil.test"},
        {
            SIMULATE_ME_REQUEST_HEADER_NAME: SIMULATE_ME_REQUEST_HEADER_VALUE,
            "origin": "https://evil.test",
        },
    ],
)
def test_simulate_me_boundary_rejects_wrong_metadata_before_service(
    headers: dict[str, str],
) -> None:
    service = RecordingSimulateMeService(result=_result())
    with TestClient(create_app(simulate_me_service=service), base_url=LOOPBACK_BASE_URL) as client:
        response = client.post("/api/simulate-me", headers=headers, json=_request_payload())

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "SIMULATE_ME_INVALID_REQUEST"
    assert service.requests == []


def test_simulate_me_boundary_requires_json_post_and_hides_openapi() -> None:
    service = RecordingSimulateMeService(result=_result())
    with TestClient(create_app(simulate_me_service=service), base_url=LOOPBACK_BASE_URL) as client:
        non_json = client.post(
            "/api/simulate-me",
            headers={
                SIMULATE_ME_REQUEST_HEADER_NAME: SIMULATE_ME_REQUEST_HEADER_VALUE,
                "content-type": "text/plain",
            },
            content="{}",
        )
        get_response = client.get("/api/simulate-me")
        openapi = client.get("/openapi.json")

    assert non_json.status_code == 400
    assert get_response.status_code == 405
    assert get_response.headers["cache-control"] == "no-store"
    assert openapi.status_code == 404
    assert service.requests == []


def test_simulate_me_declared_raw_body_cap_runs_before_parser_or_service() -> None:
    service = RecordingSimulateMeService(result=_result())
    headers = {
        "host": "127.0.0.1",
        "content-type": "application/json",
        SIMULATE_ME_REQUEST_HEADER_NAME: SIMULATE_ME_REQUEST_HEADER_VALUE,
        "content-length": str(MAX_RAW_SIMULATE_ME_BODY_BYTES + 1),
    }
    status, response_headers, body, receive_calls = send_raw_asgi_request(
        create_app(simulate_me_service=service),
        path="/api/simulate-me",
        headers=headers,
        body_chunks=(b"{}",),
    )

    assert status == 413
    assert response_headers["cache-control"] == "no-store"
    assert b"SIMULATE_ME_CONTENT_TOO_LARGE" in body
    assert receive_calls == 0
    assert service.requests == []


@pytest.mark.parametrize(
    ("error", "status", "code"),
    [
        (SimulateMeInvalidRequestError(), 400, "SIMULATE_ME_INVALID_REQUEST"),
        (ConfigurationError("missing"), 503, "SIMULATE_ME_VAULT_UNAVAILABLE"),
        (SimulateMeError("SIMULATE_ME_RESULT_INVALID"), 500, "SIMULATE_ME_RESULT_INVALID"),
    ],
)
def test_simulate_me_service_errors_are_safe(
    error: Exception,
    status: int,
    code: str,
) -> None:
    service = RecordingSimulateMeService(error=error)
    with TestClient(create_app(simulate_me_service=service), base_url=LOOPBACK_BASE_URL) as client:
        response = client.post("/api/simulate-me", headers=_headers(), json=_request_payload())

    assert response.status_code == status
    assert response.json()["error"]["code"] == code
    assert "missing" not in response.text
    assert "exception" not in response.text.lower()
