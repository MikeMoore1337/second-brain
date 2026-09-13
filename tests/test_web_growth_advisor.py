"""Private Stage 11C Web boundary tests without live vault/provider access."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from second_brain.application.growth_advisor import (
    GROWTH_ADVISOR_POLICY_ID,
    GrowthAdvisorBranchStateV1,
    GrowthAdvisorBranchV1,
    GrowthAdvisorErrorCode,
    GrowthAdvisorErrorV1,
    GrowthAdvisorGoalPreviewV1,
    GrowthAdvisorRequestV1,
)
from second_brain.entrypoints.web.app import (
    GROWTH_ADVISOR_EXECUTE_PATH,
    GROWTH_ADVISOR_PREVIEW_PATH,
    GROWTH_ADVISOR_REQUEST_HEADER_NAME,
    GROWTH_ADVISOR_REQUEST_HEADER_VALUE,
    MAX_RAW_GROWTH_ADVISOR_BODY_BYTES,
    create_app,
)
from second_brain.entrypoints.web.auth import (
    DEFAULT_OAUTH_STATE_TTL_SECONDS,
    DEFAULT_SESSION_TTL_SECONDS,
    WebAuthConfig,
)

BASE_URL = "http://127.0.0.1"
GOAL_ID = "0198c8a0-0000-7000-8000-000000000020"
FINGERPRINT = "sha256:" + "d" * 64


def _request_payload() -> dict[str, object]:
    return {
        "contract_version": "growth-advisor-v1",
        "goal_source_uuid": GOAL_ID,
        "goal_identity_fingerprint": FINGERPRINT,
        "task": "Разобрать следующий шаг",
        "options": [],
        "explicit_constraints": [],
        "explicit_context": [],
        "max_context_bytes": 65536,
        "max_result_bytes": 65536,
    }


def _preview_payload() -> dict[str, object]:
    return {
        "contract_version": "growth-advisor-v1",
        "goal_source_uuid": GOAL_ID,
        "goal_identity_fingerprint": FINGERPRINT,
        "assistant_contract_version": "assistant-v1",
        "advisor_policy_id": GROWTH_ADVISOR_POLICY_ID,
        "goal_text": "Текущая цель",
        "goal_text_utf8_bytes": len("Текущая цель".encode()),
    }


def _disabled_auth_config() -> WebAuthConfig:
    return WebAuthConfig(
        mode="disabled",
        public_base_url=None,
        github_client_id=None,
        github_client_secret=None,
        allowed_user_id=None,
        session_secret=None,
        session_ttl_seconds=DEFAULT_SESSION_TTL_SECONDS,
        oauth_state_ttl_seconds=DEFAULT_OAUTH_STATE_TTL_SECONDS,
        trusted_authorities=frozenset(),
    )


def _github_auth_config() -> WebAuthConfig:
    return WebAuthConfig(
        mode="github",
        public_base_url="https://brain.example.test",
        github_client_id="client-id",
        github_client_secret="client-secret",
        allowed_user_id="42142321",
        session_secret=b"session-secret-that-is-long-enough-for-tests-123456",
        session_ttl_seconds=DEFAULT_SESSION_TTL_SECONDS,
        oauth_state_ttl_seconds=DEFAULT_OAUTH_STATE_TTL_SECONDS,
        trusted_authorities=frozenset(),
    )


@dataclass(slots=True)
class _RecordingGrowthAdvisorService:
    preview_calls: list[GrowthAdvisorRequestV1] = field(default_factory=list)
    execute_calls: list[tuple[GrowthAdvisorRequestV1, GrowthAdvisorGoalPreviewV1, bool]] = field(
        default_factory=list
    )

    def preview(self, request: GrowthAdvisorRequestV1) -> GrowthAdvisorGoalPreviewV1:
        self.preview_calls.append(request)
        return GrowthAdvisorGoalPreviewV1(
            contract_version="growth-advisor-v1",
            goal_source_uuid=GOAL_ID,
            goal_identity_fingerprint=FINGERPRINT,
            assistant_contract_version="assistant-v1",
            advisor_policy_id=GROWTH_ADVISOR_POLICY_ID,
            goal_text="Текущая цель",
            goal_text_utf8_bytes=len("Текущая цель".encode()),
        )

    def execute(
        self,
        request: GrowthAdvisorRequestV1,
        preview: GrowthAdvisorGoalPreviewV1,
        *,
        confirmed: bool,
    ) -> GrowthAdvisorBranchV1:
        self.execute_calls.append((request, preview, confirmed))
        return GrowthAdvisorBranchV1(
            branch="advisor",
            state=GrowthAdvisorBranchStateV1.ERROR,
            assistant_result=None,
            error=GrowthAdvisorErrorV1(GrowthAdvisorErrorCode.FAILURE),
            provenance=None,
        )


def _app(
    service: _RecordingGrowthAdvisorService,
    *,
    auth: WebAuthConfig | None = None,
) -> FastAPI:
    return create_app(
        growth_advisor_service=service,
        web_auth_config=auth or _disabled_auth_config(),
    )


def _headers() -> dict[str, str]:
    return {GROWTH_ADVISOR_REQUEST_HEADER_NAME: GROWTH_ADVISOR_REQUEST_HEADER_VALUE}


def test_preview_is_owner_private_no_store_and_does_not_execute(tmp_path: Path) -> None:
    del tmp_path
    service = _RecordingGrowthAdvisorService()
    with TestClient(_app(service), base_url=BASE_URL) as client:
        response = client.post(
            GROWTH_ADVISOR_PREVIEW_PATH,
            json=_request_payload(),
            headers=_headers(),
        )

    assert response.status_code == 200
    assert response.json()["goal_text"] == "Текущая цель"
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert "access-control-allow-origin" not in response.headers
    assert len(service.preview_calls) == 1
    assert service.execute_calls == []


def test_execute_requires_explicit_confirmation_and_returns_separate_branch() -> None:
    service = _RecordingGrowthAdvisorService()
    body = {
        "request": _request_payload(),
        "preview": _preview_payload(),
        "confirmed": True,
    }
    with TestClient(_app(service), base_url=BASE_URL) as client:
        response = client.post(GROWTH_ADVISOR_EXECUTE_PATH, json=body, headers=_headers())

    assert response.status_code == 200
    payload = response.json()
    assert payload["branch"] == "advisor"
    assert payload["state"] == "error"
    assert payload["error"]["code"] == "GROWTH_ADVISOR_FAILURE"
    assert payload["assistant_result"] is None
    assert payload["provenance"] is None
    assert len(service.execute_calls) == 1
    assert service.execute_calls[0][2] is True


def test_unknown_duplicate_and_false_confirmation_are_rejected_before_service() -> None:
    service = _RecordingGrowthAdvisorService()
    with TestClient(_app(service), base_url=BASE_URL) as client:
        extra = client.post(
            GROWTH_ADVISOR_PREVIEW_PATH,
            json={**_request_payload(), "goal_text": "forbidden"},
            headers=_headers(),
        )
        duplicate = client.post(
            GROWTH_ADVISOR_PREVIEW_PATH,
            content=(
                '{"contract_version":"growth-advisor-v1",'
                '"contract_version":"growth-advisor-v1",'
                f'"goal_source_uuid":"{GOAL_ID}",'
                f'"goal_identity_fingerprint":"{FINGERPRINT}",'
                '"task":"Task","options":[],"explicit_constraints":[],'
                '"explicit_context":[],"max_context_bytes":65536,"max_result_bytes":65536}'
            ).encode(),
            headers={**_headers(), "Content-Type": "application/json"},
        )
        false_confirmation = client.post(
            GROWTH_ADVISOR_EXECUTE_PATH,
            json={
                "request": _request_payload(),
                "preview": _preview_payload(),
                "confirmed": False,
            },
            headers=_headers(),
        )

    assert extra.status_code == 400
    assert duplicate.status_code == 400
    assert false_confirmation.status_code == 400
    assert service.preview_calls == []
    assert service.execute_calls == []


def test_host_origin_purpose_and_raw_body_caps_fail_closed() -> None:
    service = _RecordingGrowthAdvisorService()
    with TestClient(_app(service), base_url=BASE_URL) as client:
        foreign_origin = client.post(
            GROWTH_ADVISOR_PREVIEW_PATH,
            json=_request_payload(),
            headers={**_headers(), "Origin": "https://evil.example"},
        )
        wrong_purpose = client.post(
            GROWTH_ADVISOR_PREVIEW_PATH,
            json=_request_payload(),
            headers={GROWTH_ADVISOR_REQUEST_HEADER_NAME: "wrong-purpose-v1"},
        )
        oversized = client.post(
            GROWTH_ADVISOR_PREVIEW_PATH,
            content=b"x" * (MAX_RAW_GROWTH_ADVISOR_BODY_BYTES + 1),
            headers={**_headers(), "Content-Type": "application/json"},
        )
        wrong_host = client.post(
            GROWTH_ADVISOR_PREVIEW_PATH,
            json=_request_payload(),
            headers={**_headers(), "Host": "evil.example"},
        )

    assert foreign_origin.status_code == 400
    assert wrong_purpose.status_code == 400
    assert oversized.status_code == 413
    assert wrong_host.status_code == 400
    assert service.preview_calls == []


def test_github_mode_rejects_unauthenticated_growth_advisor_before_route() -> None:
    service = _RecordingGrowthAdvisorService()
    with TestClient(_app(service, auth=_github_auth_config()), base_url=BASE_URL) as client:
        response = client.post(
            GROWTH_ADVISOR_PREVIEW_PATH,
            json=_request_payload(),
            headers=_headers(),
        )

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "AUTH_REQUIRED"
    assert response.headers["cache-control"] == "no-store"
    assert service.preview_calls == []
