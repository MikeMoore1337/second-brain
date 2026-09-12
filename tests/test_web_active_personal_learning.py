"""Security and projection tests for the Active Personal Learning Web/API slice."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import cast

import pytest
from fastapi.testclient import TestClient

from second_brain.application.active_personal_learning import (
    ActiveLearningError,
    ActiveLearningErrorCodeV1,
    ActiveLearningNoCandidateCodeV1,
    ActiveLearningResultV1,
    ActiveLearningSourceUnavailableError,
    ActiveLearningStatusV1,
    QuestionCandidateV1,
    QuestionOptionV1,
    QuestionReasonCodeV1,
    serialize_active_learning_result,
)
from second_brain.application.simulate_me import (
    POLICY_FINGERPRINT as SIMULATE_ME_POLICY_FINGERPRINT,
)
from second_brain.application.simulate_me import (
    SimulateMeOption,
    SimulateMeRequest,
)
from second_brain.entrypoints.web.active_personal_learning import (
    ACTIVE_LEARNING_REQUEST_HEADER_VALUE,
    MAX_RAW_ACTIVE_LEARNING_BODY_BYTES,
    ActiveLearningQuestionsRequestPayload,
    ActiveLearningWebService,
    LazyVaultActiveLearningService,
    build_production_active_learning_service,
)
from second_brain.entrypoints.web.app import create_app

BASE_URL = "http://127.0.0.1"
NOW = datetime(2026, 9, 12, 10, 0, tzinfo=UTC)


def _candidate() -> QuestionCandidateV1:
    return QuestionCandidateV1(
        contract_version="active-personal-learning-v1",
        candidate_id="apl1:" + "0" * 64,
        kind="choice",
        reason_code=QuestionReasonCodeV1.MISSING_EVIDENCE,
        task="Текущий выбор",
        question="Какой вариант лучше всего описывает ваш текущий выбор?",
        options=(QuestionOptionV1(id="a", label="A"), QuestionOptionV1(id="b", label="B")),
        source="simulate-me-abstention-v1",
        source_derivation_version="simulate-me-v1",
        source_policy_id="simulate-me-direct-exact-v1",
        source_policy_fingerprint=SIMULATE_ME_POLICY_FINGERPRINT,
        evidence_note_ids=(),
        basis_fingerprint="sha256:" + "1" * 64,
        issued_at=NOW,
        expires_at=NOW + timedelta(seconds=600),
    )


@dataclass(slots=True)
class RecordingActiveLearningService:
    result: ActiveLearningResultV1
    calls: list[SimulateMeRequest] = field(default_factory=list)

    def execute(self, request: SimulateMeRequest) -> ActiveLearningResultV1:
        self.calls.append(request)
        return self.result


@dataclass(slots=True)
class RaisingActiveLearningService:
    error: BaseException
    calls: int = 0

    def execute(self, request: SimulateMeRequest) -> ActiveLearningResultV1:
        del request
        self.calls += 1
        raise self.error


def _headers() -> dict[str, str]:
    return {
        "Origin": BASE_URL,
        "X-Second-Brain-Request": ACTIVE_LEARNING_REQUEST_HEADER_VALUE,
    }


def _request_body() -> dict[str, object]:
    return {
        "query": "Текущий выбор",
        "options": [{"id": "a", "label": "A"}, {"id": "b", "label": "B"}],
    }


def test_active_learning_api_projects_the_exact_core_result() -> None:
    result = ActiveLearningResultV1(
        status=ActiveLearningStatusV1.CANDIDATE,
        candidate=_candidate(),
        no_candidate_code=None,
    )
    service = RecordingActiveLearningService(result)
    app = create_app(active_learning_service=cast(ActiveLearningWebService, service))

    with TestClient(app, base_url=BASE_URL) as client:
        response = client.post(
            "/api/active-learning/questions",
            json=_request_body(),
            headers=_headers(),
        )

    assert response.status_code == 200
    assert response.content == serialize_active_learning_result(result)
    assert response.headers["cache-control"] == "no-store"
    assert "access-control-allow-origin" not in response.headers
    assert service.calls == [
        SimulateMeRequest(
            query="Текущий выбор",
            options=(SimulateMeOption("a", "A"), SimulateMeOption("b", "B")),
        )
    ]
    payload = response.json()
    assert set(payload) == {"status", "candidate", "no_candidate_code"}
    assert payload["status"] == "candidate"
    assert payload["candidate"]["question"] == (
        "Какой вариант лучше всего описывает ваш текущий выбор?"
    )
    assert not any(
        forbidden in response.content.lower()
        for forbidden in (b"front_matter", b"absolute_path", b"secret", b"traceback")
    )


def test_active_learning_api_projects_no_candidate_without_recomputation() -> None:
    result = ActiveLearningResultV1(
        status=ActiveLearningStatusV1.NO_CANDIDATE,
        candidate=None,
        no_candidate_code=ActiveLearningNoCandidateCodeV1.NO_ACTIONABLE_GAP,
    )
    service = RecordingActiveLearningService(result)
    app = create_app(active_learning_service=cast(ActiveLearningWebService, service))

    with TestClient(app, base_url=BASE_URL) as client:
        response = client.post(
            "/api/active-learning/questions",
            json=_request_body(),
            headers=_headers(),
        )

    assert response.status_code == 200
    assert response.json() == {
        "candidate": None,
        "no_candidate_code": "no_actionable_gap",
        "status": "no_candidate",
    }


@pytest.mark.parametrize("payload", [{"query": "x", "options": [], "extra": True}, [], None])
def test_active_learning_api_rejects_unknown_or_non_object_input(payload: object) -> None:
    service = RecordingActiveLearningService(
        ActiveLearningResultV1(
            status=ActiveLearningStatusV1.NO_CANDIDATE,
            candidate=None,
            no_candidate_code=ActiveLearningNoCandidateCodeV1.NO_ACTIONABLE_GAP,
        )
    )
    app = create_app(active_learning_service=cast(ActiveLearningWebService, service))

    with TestClient(app, base_url=BASE_URL) as client:
        response = client.post(
            "/api/active-learning/questions",
            json=payload,
            headers=_headers(),
        )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == ActiveLearningErrorCodeV1.INVALID_REQUEST.value
    assert service.calls == []


def test_active_learning_boundary_rejects_wrong_origin_purpose_content_type_and_raw_oversize() -> (
    None
):
    service = RecordingActiveLearningService(
        ActiveLearningResultV1(
            status=ActiveLearningStatusV1.NO_CANDIDATE,
            candidate=None,
            no_candidate_code=ActiveLearningNoCandidateCodeV1.NO_ACTIONABLE_GAP,
        )
    )
    app = create_app(active_learning_service=cast(ActiveLearningWebService, service))

    with TestClient(app, base_url=BASE_URL) as client:
        wrong_origin = client.post(
            "/api/active-learning/questions",
            json=_request_body(),
            headers={**_headers(), "Origin": "https://evil.example"},
        )
        wrong_purpose = client.post(
            "/api/active-learning/questions",
            json=_request_body(),
            headers={**_headers(), "X-Second-Brain-Request": "wrong-purpose-v1"},
        )
        wrong_content_type = client.post(
            "/api/active-learning/questions",
            content=b"{}",
            headers={**_headers(), "Content-Type": "text/plain"},
        )
        too_large = client.post(
            "/api/active-learning/questions",
            content=b"x" * (MAX_RAW_ACTIVE_LEARNING_BODY_BYTES + 1),
            headers={**_headers(), "Content-Type": "application/json"},
        )

    assert [
        response.status_code for response in (wrong_origin, wrong_purpose, wrong_content_type)
    ] == [400, 400, 400]
    assert too_large.status_code == 413
    assert too_large.json()["error"]["code"] == "ACTIVE_LEARNING_CONTENT_TOO_LARGE"
    assert service.calls == []


@pytest.mark.parametrize(
    ("error", "status", "code"),
    [
        (ActiveLearningSourceUnavailableError(), 503, "ACTIVE_LEARNING_SOURCE_UNAVAILABLE"),
        (
            ActiveLearningError("ACTIVE_LEARNING_RESULT_TOO_LARGE"),
            500,
            "ACTIVE_LEARNING_RESULT_TOO_LARGE",
        ),
    ],
)
def test_active_learning_api_maps_only_safe_core_errors(
    error: ActiveLearningError,
    status: int,
    code: str,
) -> None:
    service = RaisingActiveLearningService(error)
    app = create_app(active_learning_service=cast(ActiveLearningWebService, service))

    with TestClient(app, base_url=BASE_URL) as client:
        response = client.post(
            "/api/active-learning/questions",
            json=_request_body(),
            headers=_headers(),
        )

    assert response.status_code == status
    assert response.json()["error"]["code"] == code
    assert set(response.json()["error"]) == {"code", "message"}
    assert response.headers["cache-control"] == "no-store"


def test_active_learning_api_hides_unexpected_service_details() -> None:
    service = RaisingActiveLearningService(RuntimeError("D:\\private\\vault\\secret.md"))
    app = create_app(active_learning_service=cast(ActiveLearningWebService, service))

    with TestClient(app, base_url=BASE_URL) as client:
        response = client.post(
            "/api/active-learning/questions",
            json=_request_body(),
            headers=_headers(),
        )

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "ACTIVE_LEARNING_SOURCE_UNAVAILABLE"
    assert "private" not in response.text
    assert "secret.md" not in response.text


def test_production_service_is_lazy_and_keeps_the_existing_vault_contract(tmp_path: Path) -> None:
    service = build_production_active_learning_service(
        env_file=tmp_path / ".env",
        vault_path_override=str(tmp_path / "vault"),
    )

    assert isinstance(service, LazyVaultActiveLearningService)
    assert service.env_file == tmp_path / ".env"
    assert service.vault_path_override == str(tmp_path / "vault")


def test_active_learning_request_payload_has_only_query_and_options() -> None:
    payload = ActiveLearningQuestionsRequestPayload.model_validate(
        _request_body(),
        strict=True,
    )

    assert payload.model_dump() == _request_body()
