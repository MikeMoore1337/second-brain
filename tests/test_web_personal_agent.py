"""Owner-only Stage 20 Web boundary and provider-preview tests."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, cast

from fastapi import FastAPI
from fastapi.testclient import TestClient

from second_brain.application.assistant import (
    ASSISTANT_CONTRACT_VERSION,
    ASSISTANT_OUTPUT_LABEL,
    AssistantReasoningEnvelopeV1,
    AssistantResultEnvelopeV1,
    AssistantResultKind,
)
from second_brain.application.execution_feedback_projection import ExecutionItemStateV1
from second_brain.application.personal_agent import AgentMissionV1
from second_brain.application.personal_agent_run_store import PersonalAgentRunOperationalStore
from second_brain.entrypoints.web.app import create_app
from second_brain.entrypoints.web.auth import (
    DEFAULT_OAUTH_STATE_TTL_SECONDS,
    DEFAULT_SESSION_TTL_SECONDS,
    SignedSessionCodec,
    WebAuthConfig,
)
from second_brain.entrypoints.web.personal_agent import (
    MAX_RAW_PERSONAL_AGENT_BODY_BYTES,
    PERSONAL_AGENT_BUILD_PATH,
    PERSONAL_AGENT_CONTEXT_PATH,
    PERSONAL_AGENT_REQUEST_HEADER_VALUE,
    PERSONAL_AGENT_STATE_PATH,
    ProductionPersonalAgentWebService,
)
from second_brain.entrypoints.web.personal_planning import PersonalPlanningWebService
from tests.test_personal_agent import _pack_inputs
from tests.test_web_action_gateway import RecordingActionService

BASE_URL = "https://brain.example.test"
OWNER_ID = "42142321"
SESSION_SECRET = b"session-secret-that-is-long-enough-for-tests-123456"


def _auth_config() -> WebAuthConfig:
    return WebAuthConfig(
        mode="github",
        public_base_url=BASE_URL,
        github_client_id="client-id",
        github_client_secret="client-secret",
        allowed_user_id=OWNER_ID,
        session_secret=SESSION_SECRET,
        session_ttl_seconds=DEFAULT_SESSION_TTL_SECONDS,
        oauth_state_ttl_seconds=DEFAULT_OAUTH_STATE_TTL_SECONDS,
        trusted_authorities=frozenset({("brain.example.test", 443)}),
    )


def _session_cookie() -> str:
    codec = SignedSessionCodec(
        secret=SESSION_SECRET,
        allowed_user_id=OWNER_ID,
        ttl_seconds=DEFAULT_SESSION_TTL_SECONDS,
    )
    return codec.issue(OWNER_ID)


def _headers(*, origin: str = BASE_URL) -> dict[str, str]:
    return {
        "Content-Type": "application/json",
        "Origin": origin,
        "X-Second-Brain-Request": PERSONAL_AGENT_REQUEST_HEADER_VALUE,
        "Cookie": f"second_brain_session={_session_cookie()}",
    }


@dataclass
class RecordingPlanningService:
    plan: Any

    def current_plan(self) -> Any:
        return self.plan


@dataclass
class RecordingExecutionService:
    states: dict[str, ExecutionItemStateV1]

    def agent_item_states(self, plan: Any) -> dict[str, ExecutionItemStateV1]:
        del plan
        return dict(self.states)


@dataclass
class RecordingAdvisor:
    calls: int = 0
    requests: list[AssistantReasoningEnvelopeV1] = field(default_factory=list)

    def advise(
        self,
        request: AssistantReasoningEnvelopeV1,
        *,
        cancellation: object,
    ) -> AssistantResultEnvelopeV1:
        del cancellation
        self.calls += 1
        self.requests.append(request)
        return AssistantResultEnvelopeV1(
            output_label=ASSISTANT_OUTPUT_LABEL,
            kind=AssistantResultKind.RECOMMENDATION,
            recommendation=json.dumps(
                {
                    "steps": [
                        {
                            "step_id": "checkpoint-1",
                            "position": 1,
                            "kind": "checkpoint",
                            "summary": "Проверь исходный контекст.",
                        }
                    ],
                    "caveats": [],
                },
                ensure_ascii=False,
                sort_keys=True,
            ),
            selected_option=None,
            rationale=("Ограниченный шаг для явного просмотра владельцем.",),
            evidence_refs=(),
            constraints_used=(),
            objectives_used=(),
            uncertainty=(),
            abstention_code=None,
            contract_version=ASSISTANT_CONTRACT_VERSION,
        )


def _app(service: ProductionPersonalAgentWebService) -> FastAPI:
    return create_app(personal_agent_web_service=service, web_auth_config=_auth_config())


def _service(
    tmp_path: Path,
) -> tuple[ProductionPersonalAgentWebService, RecordingAdvisor, AgentMissionV1]:
    mission, plan, states, _stage19 = _pack_inputs()
    advisor = RecordingAdvisor()
    action_service = RecordingActionService()
    service = ProductionPersonalAgentWebService(
        planning_service=cast(PersonalPlanningWebService, RecordingPlanningService(plan)),
        execution_service=RecordingExecutionService(states),
        action_gateway_service=action_service,
        advisor=advisor,
        run_store=PersonalAgentRunOperationalStore(tmp_path / "operational"),
    )
    return service, advisor, mission


def test_anonymous_stage20_request_is_rejected_before_store_or_provider(tmp_path: Path) -> None:
    service, advisor, _mission = _service(tmp_path)
    with TestClient(_app(service), base_url=BASE_URL) as client:
        response = client.post(
            PERSONAL_AGENT_STATE_PATH,
            json={},
            headers={
                "Content-Type": "application/json",
                "Origin": BASE_URL,
                "X-Second-Brain-Request": PERSONAL_AGENT_REQUEST_HEADER_VALUE,
            },
        )

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "AUTH_REQUIRED"
    assert response.headers["cache-control"] == "no-store"
    assert advisor.calls == 0


def test_stage20_boundary_rejects_cross_origin_duplicate_and_oversized_requests(
    tmp_path: Path,
) -> None:
    service, advisor, _mission = _service(tmp_path)
    with TestClient(_app(service), base_url=BASE_URL) as client:
        foreign_origin = client.post(
            PERSONAL_AGENT_STATE_PATH,
            json={},
            headers=_headers(origin="https://evil.example"),
        )
        duplicate_json = client.post(
            PERSONAL_AGENT_STATE_PATH,
            content=b'{"unexpected":true,"unexpected":false}',
            headers=_headers(),
        )
        oversized = client.post(
            PERSONAL_AGENT_STATE_PATH,
            content=b"x" * (MAX_RAW_PERSONAL_AGENT_BODY_BYTES + 1),
            headers=_headers(),
        )
        get_response = client.get(PERSONAL_AGENT_STATE_PATH, headers=_headers())

    assert foreign_origin.status_code == 400
    assert duplicate_json.status_code == 400
    assert oversized.status_code == 413
    assert get_response.status_code == 405
    assert advisor.calls == 0


def test_context_is_provider_free_and_build_requires_exact_preview(tmp_path: Path) -> None:
    service, advisor, mission = _service(tmp_path)
    with TestClient(_app(service), base_url=BASE_URL) as client:
        context = client.post(
            PERSONAL_AGENT_CONTEXT_PATH,
            json={"mission": mission.as_dict()},
            headers=_headers(),
        )
        assert context.status_code == 200
        context_payload = context.json()
        assert advisor.calls == 0
        assert context.headers["cache-control"] == "no-store, private"

        build_payload = {
            "mission": mission.as_dict(),
            "context_pack": context_payload["context_pack"],
            "provider_preview": context_payload["provider_preview"],
        }
        built = client.post(PERSONAL_AGENT_BUILD_PATH, json=build_payload, headers=_headers())
        assert built.status_code == 200
        assert advisor.calls == 1
        assert built.json()["proposal"]["steps"][0]["kind"] == "checkpoint"

        tampered = json.loads(json.dumps(build_payload))
        tampered["provider_preview"]["canonical_json"] += " "
        rejected = client.post(PERSONAL_AGENT_BUILD_PATH, json=tampered, headers=_headers())

    assert rejected.status_code == 409
    assert rejected.json()["error"]["code"] == "SOURCE_MISMATCH"
    assert advisor.calls == 1
