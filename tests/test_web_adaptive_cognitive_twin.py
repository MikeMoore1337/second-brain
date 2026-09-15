"""Focused Stage 15.4 transport coverage for the Adaptive Cognitive Twin UI API."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from fastapi.testclient import TestClient

from second_brain.entrypoints.web.adaptive_cognitive_twin import (
    ADAPTIVE_COGNITIVE_TWIN_ACTIVATE_PATH,
    ADAPTIVE_COGNITIVE_TWIN_CANDIDATE_PATH,
    ADAPTIVE_COGNITIVE_TWIN_EVALUATE_PATH,
    ADAPTIVE_COGNITIVE_TWIN_REJECT_PATH,
    ADAPTIVE_COGNITIVE_TWIN_REQUEST_HEADER_VALUE,
    ADAPTIVE_COGNITIVE_TWIN_REVERT_PATH,
    ADAPTIVE_COGNITIVE_TWIN_REVIEW_PATH,
    ADAPTIVE_COGNITIVE_TWIN_STATE_PATH,
    ADAPTIVE_COGNITIVE_TWIN_SUPERSEDE_PATH,
    MAX_RAW_ADAPTIVE_COGNITIVE_TWIN_BODY_BYTES,
    AdaptiveCognitiveTwinActivatePayload,
    AdaptiveCognitiveTwinCandidatePayload,
    AdaptiveCognitiveTwinEvaluatePayload,
    AdaptiveCognitiveTwinRejectPayload,
    AdaptiveCognitiveTwinRevertPayload,
    AdaptiveCognitiveTwinReviewPayload,
    AdaptiveCognitiveTwinStatePayload,
    AdaptiveCognitiveTwinSupersedePayload,
)
from second_brain.entrypoints.web.app import create_app
from second_brain.entrypoints.web.auth import (
    DEFAULT_OAUTH_STATE_TTL_SECONDS,
    DEFAULT_SESSION_TTL_SECONDS,
    WebAuthConfig,
)

BASE_URL = "http://127.0.0.1"
GOAL_UUID = "0198f4c5-6a00-7000-8000-000000001200"
GOAL_FINGERPRINT = "sha256:" + "a" * 64
SOURCE_FINGERPRINT = "sha256:" + "b" * 64
CANDIDATE_FINGERPRINT = "sha256:" + "c" * 64
OPERATION_UUID = "0198f4c5-6a00-7000-8000-000000001201"
PROFILE_UUID = "0198f4c5-6a00-7000-8000-000000001202"
PROFILE_FINGERPRINT = "sha256:" + "d" * 64


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


def _headers(*, origin: str | None = None) -> dict[str, str]:
    headers = {
        "Content-Type": "application/json",
        "X-Second-Brain-Request": ADAPTIVE_COGNITIVE_TWIN_REQUEST_HEADER_VALUE,
    }
    if origin is not None:
        headers["Origin"] = origin
    return headers


def _selection() -> dict[str, str]:
    return {
        "goal_source_uuid": GOAL_UUID,
        "goal_identity_fingerprint": GOAL_FINGERPRINT,
        "as_of": "2026-09-16T12:00:00Z",
    }


def _operation_payload() -> dict[str, Any]:
    return {
        **_selection(),
        "candidate_fingerprint": CANDIDATE_FINGERPRINT,
        "source_snapshot_fingerprint": SOURCE_FINGERPRINT,
        "operation_id": OPERATION_UUID,
        "confirmed": True,
    }


@dataclass(slots=True)
class _StubService:
    calls: list[str] = field(default_factory=list)

    @staticmethod
    def _projection() -> dict[str, object]:
        return {
            "source_snapshot_fingerprint": SOURCE_FINGERPRINT,
            "candidate": {
                "candidate_fingerprint": CANDIDATE_FINGERPRINT,
                "candidate_status": "candidate",
            },
        }

    def state(self, payload: AdaptiveCognitiveTwinStatePayload) -> dict[str, object]:
        self.calls.append("state")
        return {
            "web_contract": "adaptive_cognitive_twin_web_v1",
            "as_of": payload.as_of or "2026-09-16T12:00:00Z",
            "goals": [],
            "selected_goal": None,
            "stage14_experiment_selectors": [],
            "projection": None,
            "non_causal_phrase": "Наблюдаемое изменение не является доказательством причинности.",
        }

    def candidate(self, payload: AdaptiveCognitiveTwinCandidatePayload) -> dict[str, object]:
        self.calls.append("candidate")
        return {"web_contract": "adaptive_cognitive_twin_web_v1", "projection": self._projection()}

    def review(self, payload: AdaptiveCognitiveTwinReviewPayload) -> dict[str, object]:
        self.calls.append("review")
        return {"web_contract": "adaptive_cognitive_twin_web_v1", "status": "reviewed"}

    def activate(self, payload: AdaptiveCognitiveTwinActivatePayload) -> dict[str, object]:
        self.calls.append("activate")
        return {"web_contract": "adaptive_cognitive_twin_web_v1", "status": "activated"}

    def reject(self, payload: AdaptiveCognitiveTwinRejectPayload) -> dict[str, object]:
        self.calls.append("reject")
        return {"web_contract": "adaptive_cognitive_twin_web_v1", "status": "rejected"}

    def evaluate(self, payload: AdaptiveCognitiveTwinEvaluatePayload) -> dict[str, object]:
        self.calls.append("evaluate")
        return {"web_contract": "adaptive_cognitive_twin_web_v1", "status": "evaluated"}

    def supersede(self, payload: AdaptiveCognitiveTwinSupersedePayload) -> dict[str, object]:
        self.calls.append("supersede")
        return {"web_contract": "adaptive_cognitive_twin_web_v1", "status": "superseded"}

    def revert(self, payload: AdaptiveCognitiveTwinRevertPayload) -> dict[str, object]:
        self.calls.append("revert")
        return {"web_contract": "adaptive_cognitive_twin_web_v1", "status": "reverted"}


def _app(service: _StubService, *, auth: WebAuthConfig | None = None) -> Any:
    return create_app(
        adaptive_cognitive_twin_web_service=service,
        web_auth_config=auth or _disabled_auth_config(),
    )


def test_all_stage15_4_routes_use_private_json_boundary_and_no_store() -> None:
    service = _StubService()
    application = _app(service)
    routes: tuple[tuple[str, Any], ...] = (
        (ADAPTIVE_COGNITIVE_TWIN_STATE_PATH, {}),
        (ADAPTIVE_COGNITIVE_TWIN_CANDIDATE_PATH, _selection()),
        (ADAPTIVE_COGNITIVE_TWIN_REVIEW_PATH, _operation_payload()),
        (ADAPTIVE_COGNITIVE_TWIN_ACTIVATE_PATH, _operation_payload()),
        (ADAPTIVE_COGNITIVE_TWIN_REJECT_PATH, _operation_payload()),
        (
            ADAPTIVE_COGNITIVE_TWIN_EVALUATE_PATH,
            {
                **_selection(),
                "active_profile_id": PROFILE_UUID,
                "active_profile_fingerprint": PROFILE_FINGERPRINT,
                "activation_source_snapshot_fingerprint": SOURCE_FINGERPRINT,
                "operation_id": OPERATION_UUID,
                "confirmed": True,
            },
        ),
        (
            ADAPTIVE_COGNITIVE_TWIN_SUPERSEDE_PATH,
            {
                **_operation_payload(),
                "prior_profile_id": PROFILE_UUID,
                "prior_profile_fingerprint": PROFILE_FINGERPRINT,
            },
        ),
        (
            ADAPTIVE_COGNITIVE_TWIN_REVERT_PATH,
            {
                **_selection(),
                "target_profile_id": PROFILE_UUID,
                "target_profile_fingerprint": PROFILE_FINGERPRINT,
                "operation_id": OPERATION_UUID,
                "confirmed": True,
            },
        ),
    )

    with TestClient(application, base_url=BASE_URL) as client:
        responses = [client.post(path, json=body, headers=_headers()) for path, body in routes]

    assert [response.status_code for response in responses] == [200] * len(routes)
    assert all(response.headers["cache-control"] == "no-store" for response in responses)
    assert all(response.headers["x-content-type-options"] == "nosniff" for response in responses)
    assert all("access-control-allow-origin" not in response.headers for response in responses)
    assert service.calls == [
        "state",
        "candidate",
        "review",
        "activate",
        "reject",
        "evaluate",
        "supersede",
        "revert",
    ]


def test_schema_and_same_origin_attacks_do_not_reach_service() -> None:
    service = _StubService()
    application = _app(service)
    unknown = {**_selection(), "unexpected": "rejected"}

    with TestClient(application, base_url=BASE_URL) as client:
        responses = [
            client.post(ADAPTIVE_COGNITIVE_TWIN_CANDIDATE_PATH, json=unknown, headers=_headers()),
            client.post(
                ADAPTIVE_COGNITIVE_TWIN_CANDIDATE_PATH,
                content=b'{"goal_source_uuid":"one","goal_source_uuid":"two"}',
                headers=_headers(),
            ),
            client.post(
                ADAPTIVE_COGNITIVE_TWIN_CANDIDATE_PATH,
                content=b'{"as_of":NaN}',
                headers=_headers(),
            ),
            client.post(
                ADAPTIVE_COGNITIVE_TWIN_CANDIDATE_PATH,
                json=_selection(),
                headers=_headers(origin="https://evil.example.test"),
            ),
            client.post(
                ADAPTIVE_COGNITIVE_TWIN_CANDIDATE_PATH,
                json=_selection(),
                headers={"Content-Type": "application/json"},
            ),
        ]

    assert [response.status_code for response in responses] == [400] * len(responses)
    assert all(
        response.json()["error"]["code"] == "ADAPTIVE_COGNITIVE_TWIN_INVALID_REQUEST"
        for response in responses
    )
    assert service.calls == []


def test_declared_and_streamed_body_caps_fail_closed() -> None:
    service = _StubService()
    application = _app(service)
    headers = {
        **_headers(),
        "Content-Length": str(MAX_RAW_ADAPTIVE_COGNITIVE_TWIN_BODY_BYTES + 1),
    }

    with TestClient(application, base_url=BASE_URL) as client:
        response = client.post(ADAPTIVE_COGNITIVE_TWIN_STATE_PATH, content=b"{}", headers=headers)

    assert response.status_code == 413
    assert response.json()["error"]["code"] == "ADAPTIVE_COGNITIVE_TWIN_RESULT_TOO_LARGE"
    assert service.calls == []


def test_owner_auth_is_checked_before_adaptive_service() -> None:
    service = _StubService()
    application = _app(service, auth=_github_auth_config())

    with TestClient(application, base_url=BASE_URL) as client:
        response = client.post(ADAPTIVE_COGNITIVE_TWIN_STATE_PATH, json={}, headers=_headers())

    assert response.status_code == 401
    assert response.json() == {"error": {"code": "AUTH_REQUIRED", "message": "Требуется вход"}}
    assert service.calls == []
