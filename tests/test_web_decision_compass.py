"""Focused private transport tests for Stage 13C Decision Compass."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from second_brain.adapters.vault import FileSystemVaultReader
from second_brain.application.compare import CompareExecutionContextV1
from second_brain.application.decision_compass import (
    BuildDecisionCompass,
    DecisionCompassRequestV1,
    DecisionCompassResultV1,
)
from second_brain.application.growth_advisor import (
    GROWTH_ADVISOR_POLICY_ID,
    GrowthAdvisorBranchStateV1,
    GrowthAdvisorBranchV1,
    GrowthAdvisorErrorCode,
    GrowthAdvisorErrorV1,
    GrowthAdvisorGoalPreviewV1,
)
from second_brain.application.ports import CancellationTokenSource
from second_brain.entrypoints.web.app import (
    DECISION_COMPASS_ADVISOR_EXECUTE_PATH,
    DECISION_COMPASS_ADVISOR_PREVIEW_PATH,
    DECISION_COMPASS_PATH,
    DECISION_COMPASS_REQUEST_HEADER_VALUE,
    MAX_RAW_DECISION_COMPASS_BODY_BYTES,
    create_app,
)
from second_brain.entrypoints.web.auth import (
    DEFAULT_OAUTH_STATE_TTL_SECONDS,
    DEFAULT_SESSION_TTL_SECONDS,
    WebAuthConfig,
)
from tests.test_growth_goal_progress_composition import (
    GOAL_ID,
    PROGRESS_AS_OF,
    _goal_hash,
    _seed,
    _write_definition_and_observation,
)

BASE_URL = "http://127.0.0.1"


def _progress_as_of_text() -> str:
    return PROGRESS_AS_OF.isoformat().replace("+00:00", "Z")


class _GrowthStub:
    """Keep the explicit Advisor branch typed without contacting a provider."""

    def __init__(self) -> None:
        self.preview_calls = 0
        self.execute_calls = 0

    def preview(
        self,
        request: object,
        *,
        cancellation: object = None,
    ) -> GrowthAdvisorGoalPreviewV1:
        del cancellation
        self.preview_calls += 1
        source_uuid = request.goal_source_uuid  # type: ignore[attr-defined]
        fingerprint = request.goal_identity_fingerprint  # type: ignore[attr-defined]
        return GrowthAdvisorGoalPreviewV1(
            contract_version="growth-advisor-v1",
            goal_source_uuid=source_uuid,
            goal_identity_fingerprint=fingerprint,
            assistant_contract_version="assistant-v1",
            advisor_policy_id=GROWTH_ADVISOR_POLICY_ID,
            goal_text="Выбранная текущая цель",
            goal_text_utf8_bytes=len("Выбранная текущая цель".encode()),
        )

    def execute(
        self,
        request: object,
        preview: object,
        *,
        confirmed: bool = True,
        cancellation: object = None,
    ) -> GrowthAdvisorBranchV1:
        del request, preview, confirmed, cancellation
        self.execute_calls += 1
        return GrowthAdvisorBranchV1(
            branch="advisor",
            state=GrowthAdvisorBranchStateV1.ERROR,
            assistant_result=None,
            error=GrowthAdvisorErrorV1(GrowthAdvisorErrorCode.FAILURE),
            provenance=None,
        )


@dataclass(slots=True)
class _FixtureService:
    compass: BuildDecisionCompass
    growth: _GrowthStub
    reader: FileSystemVaultReader
    build_calls: list[DecisionCompassRequestV1] = field(default_factory=list)

    @staticmethod
    def _execution() -> CompareExecutionContextV1:
        return CompareExecutionContextV1(
            cancellation=CancellationTokenSource().token,
            deadline=float(time.monotonic() + 30),
        )

    def build(self, request: DecisionCompassRequestV1) -> object:
        self.build_calls.append(request)
        return self.compass.execute(request, execution=self._execution())

    def preview_advisor(self, request: DecisionCompassRequestV1) -> object:
        return self.compass.preview_advisor(request)

    def execute_advisor(
        self,
        request: DecisionCompassRequestV1,
        preview: object,
        *,
        confirmed: bool,
    ) -> object:
        base = self.compass.execute(request, execution=self._execution())
        assert isinstance(base, DecisionCompassResultV1)
        return self.compass.execute_advisor(
            request,
            base,
            preview,
            execution=self._execution(),
            confirmed=confirmed,
        )


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


def _request_payload(goal_fingerprint: str) -> dict[str, object]:
    return {
        "contract_version": "growth-compare-v1",
        "task": "Выбрать следующий шаг",
        "options": [
            {"id": "alpha", "label": "Сначала прояснить"},
            {"id": "beta", "label": "Сначала действовать"},
        ],
        "selected_goal": {
            "source_uuid": str(GOAL_ID),
            "identity_fingerprint": goal_fingerprint,
        },
        "criteria": [{"id": "speed", "label": "Скорость", "description": None}],
        "explicit_constraints": [],
        "explicit_context": [],
        "progress_as_of": _progress_as_of_text(),
        "behavioral_scope": None,
        "behavioral_option_binding": None,
        "max_result_bytes": 65536,
    }


def _fixture_app(tmp_path: Path) -> tuple[FastAPI, _FixtureService]:
    vault, store, reader = _seed(tmp_path)
    _write_definition_and_observation(vault, reader)
    growth = _GrowthStub()
    compass = BuildDecisionCompass(
        reader=reader,
        store=store,
        growth_advisor=growth,
        growth_clock=lambda: PROGRESS_AS_OF,
        behavioral_clock=lambda: PROGRESS_AS_OF,
        goal_clock=lambda: PROGRESS_AS_OF,
    )
    service = _FixtureService(compass, growth, reader)
    return create_app(
        decision_compass_service=service,
        web_auth_config=_disabled_auth_config(),
    ), service


def _headers() -> dict[str, str]:
    return {
        "Content-Type": "application/json",
        "X-Second-Brain-Request": DECISION_COMPASS_REQUEST_HEADER_VALUE,
    }


def test_base_route_is_provider_free_and_defaults_advisor_to_not_requested(tmp_path: Path) -> None:
    application, service = _fixture_app(tmp_path)
    goal_fingerprint = _goal_hash(service.reader)
    with TestClient(application, base_url=BASE_URL) as client:
        response = client.post(
            DECISION_COMPASS_PATH,
            json=_request_payload(goal_fingerprint),
            headers=_headers(),
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["advisor"]["state"] == "not_requested"
    assert payload["behavioral"]["state"] == "not_selected"
    assert payload["provenance"]["provider"] == "none"
    assert payload["provenance"]["write"] == "none"
    assert service.growth.preview_calls == 0
    assert service.growth.execute_calls == 0
    assert response.headers["cache-control"] == "no-store"
    assert "access-control-allow-origin" not in response.headers


def test_explicit_advisor_preview_and_execute_are_separate_actions(tmp_path: Path) -> None:
    application, service = _fixture_app(tmp_path)
    goal_fingerprint = _goal_hash(service.reader)
    request = _request_payload(goal_fingerprint)
    with TestClient(application, base_url=BASE_URL) as client:
        preview_response = client.post(
            DECISION_COMPASS_ADVISOR_PREVIEW_PATH,
            json=request,
            headers=_headers(),
        )
        preview = preview_response.json()
        execute_response = client.post(
            DECISION_COMPASS_ADVISOR_EXECUTE_PATH,
            json={"request": request, "preview": preview, "confirmed": True},
            headers=_headers(),
        )

    assert preview_response.status_code == 200
    assert preview["advisor_policy_id"] == GROWTH_ADVISOR_POLICY_ID
    assert execute_response.status_code == 200
    assert execute_response.json()["advisor"]["state"] == "error"
    assert execute_response.json()["simulate_me"] is not None
    assert service.growth.preview_calls == 1
    assert service.growth.execute_calls == 1


def test_route_rejects_unknown_duplicate_false_confirmation_and_bad_boundary_before_service(
    tmp_path: Path,
) -> None:
    application, service = _fixture_app(tmp_path)
    goal_fingerprint = _goal_hash(service.reader)
    request = _request_payload(goal_fingerprint)
    with TestClient(application, base_url=BASE_URL) as client:
        extra = client.post(
            DECISION_COMPASS_PATH,
            json={**request, "unsupported": True},
            headers=_headers(),
        )
        duplicate = client.post(
            DECISION_COMPASS_PATH,
            content=(
                b'{"task":"Task","task":"Task","options":[],"selected_goal":null,'
                b'"progress_as_of":"2026-09-14T10:00:00Z"}'
            ),
            headers=_headers(),
        )
        false_confirmation = client.post(
            DECISION_COMPASS_ADVISOR_EXECUTE_PATH,
            json={"request": request, "preview": {}, "confirmed": False},
            headers=_headers(),
        )
        foreign_origin = client.post(
            DECISION_COMPASS_PATH,
            json=request,
            headers={**_headers(), "Origin": "https://evil.example"},
        )
        oversized = client.post(
            DECISION_COMPASS_PATH,
            content=b"x" * (MAX_RAW_DECISION_COMPASS_BODY_BYTES + 1),
            headers=_headers(),
        )

    assert extra.status_code == 400
    assert duplicate.status_code == 400
    assert false_confirmation.status_code == 400
    assert foreign_origin.status_code == 400
    assert oversized.status_code == 413
    assert service.build_calls == []
    assert service.growth.preview_calls == 0
    assert service.growth.execute_calls == 0


def test_github_mode_requires_owner_session_before_decision_compass_service(tmp_path: Path) -> None:
    application, service = _fixture_app(tmp_path)
    application = create_app(
        decision_compass_service=service,
        web_auth_config=_github_auth_config(),
    )
    goal_fingerprint = _goal_hash(service.reader)
    with TestClient(application, base_url=BASE_URL) as client:
        response = client.post(
            DECISION_COMPASS_PATH,
            json=_request_payload(goal_fingerprint),
            headers=_headers(),
        )

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "AUTH_REQUIRED"
    assert service.build_calls == []
