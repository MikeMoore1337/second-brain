"""Adversarial Stage 18 private API and service-boundary tests."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast
from uuid import UUID, uuid7

import pytest
from fastapi.testclient import TestClient

from second_brain.application.execution_feedback import (
    ExecutionReasonCodeV1,
    accepted_item_fingerprint,
)
from second_brain.application.execution_feedback_store import ExecutionFeedbackOperationalStore
from second_brain.application.executive_strategy import (
    POLICY_FINGERPRINT as STAGE16_POLICY_FINGERPRINT,
)
from second_brain.application.executive_strategy import (
    POLICY_ID as STAGE16_POLICY_ID,
)
from second_brain.application.personal_planning import (
    PLANNING_POLICY_FINGERPRINT,
    PLANNING_POLICY_ID,
    PlanningActionBindingV1,
    PlanningCapacityEntryV1,
    PlanningEffortSourceV1,
    PlanningGoalRefV1,
    PlanningItemKindV1,
    PlanningItemV1,
)
from second_brain.application.personal_planning_store import (
    PlanningPlanV1,
    personal_planning_store_hash,
)
from second_brain.entrypoints.web.app import (
    EXECUTION_FEEDBACK_CALIBRATION_PATH,
    EXECUTION_FEEDBACK_EVENT_PATH,
    EXECUTION_FEEDBACK_FEEDBACK_PATH,
    EXECUTION_FEEDBACK_REQUEST_HEADER_VALUE,
    EXECUTION_FEEDBACK_STATE_PATH,
    MAX_RAW_EXECUTION_FEEDBACK_BODY_BYTES,
    create_app,
)
from second_brain.entrypoints.web.auth import (
    DEFAULT_OAUTH_STATE_TTL_SECONDS,
    DEFAULT_SESSION_TTL_SECONDS,
    WebAuthConfig,
)
from second_brain.entrypoints.web.execution_feedback import (
    ExecutionFeedbackCalibrationPayload,
    ExecutionFeedbackCorrectionPayload,
    ExecutionFeedbackEventPayload,
    ExecutionFeedbackFeedbackPayload,
    ExecutionFeedbackStalePlanError,
    ExecutionFeedbackStateConflictError,
    ProductionExecutionFeedbackWebService,
)

BASE_URL = "http://127.0.0.1"
NOW = datetime(2026, 9, 16, 7, 0, tzinfo=UTC)


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


def _plan(
    *,
    plan_id: UUID | None = None,
    item_id: str = "item-1",
    kind: PlanningItemKindV1 = PlanningItemKindV1.NEXT_ACTION,
    effort: int = 30,
) -> PlanningPlanV1:
    goal_id = uuid7()
    goal_ref = PlanningGoalRefV1(
        goal_source_uuid=goal_id,
        goal_identity_fingerprint="sha256:" + "1" * 64,
    )
    action_ref = PlanningActionBindingV1(
        goal_source_uuid=goal_id,
        goal_identity_fingerprint=goal_ref.goal_identity_fingerprint,
        strategy_snapshot_id=uuid7(),
        strategy_snapshot_fingerprint="2" * 64,
        reviewed_action_id="action-1",
        reviewed_action_fingerprint="3" * 64,
        stage16_policy_id=STAGE16_POLICY_ID,
        stage16_policy_fingerprint=STAGE16_POLICY_FINGERPRINT,
    )
    item = PlanningItemV1(
        item_id=item_id,
        kind=kind,
        title="Подготовить небольшой шаг",
        description="Ограниченный owner-selected шаг для API-теста.",
        goal_refs=(goal_ref,),
        action_refs=(action_ref,),
        parent_item_id=None,
        target_start_local="2026-09-16T05:00"
        if kind
        in {
            PlanningItemKindV1.NEXT_ACTION,
            PlanningItemKindV1.COMMITMENT,
        }
        else None,
        target_end_local="2026-09-16T05:30"
        if kind
        in {
            PlanningItemKindV1.NEXT_ACTION,
            PlanningItemKindV1.COMMITMENT,
        }
        else None,
        effort_minutes=effort,
        effort_source=PlanningEffortSourceV1.PROVIDER_PROPOSED,
        dependency_ids=(),
    )
    resolved_id = plan_id or uuid7()
    core = {
        "plan_version": "1",
        "plan_id": str(resolved_id),
        "revision": 1,
        "as_of": "2026-09-16T04:00:00Z",
        "source_pack_fingerprint": "5" * 64,
        "provider_envelope_fingerprint": "6" * 64,
        "provider_result_fingerprint": "7" * 64,
        "proposal_fingerprint": "8" * 64,
        "policy_id": PLANNING_POLICY_ID,
        "policy_fingerprint": PLANNING_POLICY_FINGERPRINT,
        "start_local": "2026-09-16",
        "end_local": "2026-09-16",
        "timezone": "UTC",
        "capacity": [{"date": "2026-09-16", "available_minutes": effort}],
        "fixed_windows": [],
        "items": [item.as_dict()],
        "selected_item_ids": [item_id],
        "item_order": [item_id],
    }
    return PlanningPlanV1(
        plan_version="1",
        plan_id=resolved_id,
        revision=1,
        as_of=datetime(2026, 9, 16, 4, tzinfo=UTC),
        source_pack_fingerprint="5" * 64,
        provider_envelope_fingerprint="6" * 64,
        provider_result_fingerprint="7" * 64,
        proposal_fingerprint="8" * 64,
        policy_id=PLANNING_POLICY_ID,
        policy_fingerprint=PLANNING_POLICY_FINGERPRINT,
        start_local="2026-09-16",
        end_local="2026-09-16",
        timezone="UTC",
        capacity=(PlanningCapacityEntryV1("2026-09-16", effort),),
        fixed_windows=(),
        items=(item,),
        selected_item_ids=(item_id,),
        item_order=(item_id,),
        plan_fingerprint=personal_planning_store_hash(core),
    )


@dataclass
class _PlanningStub:
    current: PlanningPlanV1 | None
    history: tuple[PlanningPlanV1, ...] = ()
    calls: list[str] = field(default_factory=list)
    source_current: bool = True

    def current_plan(self) -> PlanningPlanV1 | None:
        self.calls.append("current_plan")
        return self.current

    def historical_plans(self) -> tuple[PlanningPlanV1, ...]:
        self.calls.append("historical_plans")
        return self.history

    def execution_source_status(self, _plan: PlanningPlanV1) -> bool:
        self.calls.append("execution_source_status")
        return self.source_current


def _service(
    tmp_path: Path,
    planning: _PlanningStub,
) -> ProductionExecutionFeedbackWebService:
    return ProductionExecutionFeedbackWebService(
        planning_service=cast(Any, planning),
        execution_store=ExecutionFeedbackOperationalStore(tmp_path / "execution-feedback"),
        clock=lambda: NOW,
    )


def _headers(**overrides: str) -> dict[str, str]:
    result = {
        "Origin": BASE_URL,
        "X-Second-Brain-Request": EXECUTION_FEEDBACK_REQUEST_HEADER_VALUE,
        "Content-Type": "application/json",
    }
    result.update(overrides)
    return result


def _event_body(
    plan: PlanningPlanV1,
    *,
    event_type: str,
    operation_id: str,
    occurred_at: str = "2026-09-16T05:01:00Z",
    effort: int | None = None,
    precision: str = "unknown",
    note: str = "",
    reasons: list[str] | None = None,
) -> dict[str, object]:
    item = plan.items[0]
    return {
        "planning_snapshot_id": str(plan.plan_id),
        "planning_snapshot_fingerprint": plan.plan_fingerprint,
        "item_id": item.item_id,
        "accepted_item_fingerprint": accepted_item_fingerprint(item),
        "operation_id": operation_id,
        "event_type": event_type,
        "occurred_at": occurred_at,
        "actual_effort_minutes": effort,
        "effort_precision": precision,
        "actual_result_note": note,
        "reason_codes": [] if reasons is None else reasons,
        "deviation_codes": [],
        "result_disposition": None,
    }


def test_state_read_does_not_create_missing_execution_store(tmp_path: Path) -> None:
    plan = _plan()
    root = tmp_path / "deployment" / "prospective-audit" / "execution-feedback"
    env_file = tmp_path / "deployment" / ".env"
    env_file.parent.mkdir()
    env_file.write_text("SECOND_BRAIN_WEB_AUTH=disabled\n", encoding="utf-8")
    service = ProductionExecutionFeedbackWebService(
        planning_service=cast(Any, _PlanningStub(plan)),
        env_file=env_file,
        clock=lambda: NOW,
    )

    body = service.state()

    assert body["report"]["not_started_count"] == 1  # type: ignore[index]
    assert not root.exists()


def test_private_api_records_events_and_returns_transparent_feedback(
    tmp_path: Path,
) -> None:
    plan = _plan()
    service = _service(tmp_path, _PlanningStub(plan, history=(plan,)))
    app = create_app(
        execution_feedback_web_service=service,
        web_auth_config=_disabled_auth_config(),
    )
    start_body = _event_body(
        plan,
        event_type="start",
        operation_id="start-1",
    )
    complete_body = _event_body(
        plan,
        event_type="complete",
        operation_id="complete-1",
        occurred_at="2026-09-16T05:15:00+00:00",
        effort=35,
        precision="exact",
        note="Результат явно отмечен owner.",
    )
    with TestClient(app, base_url=BASE_URL) as client:
        start = client.post(EXECUTION_FEEDBACK_EVENT_PATH, json=start_body, headers=_headers())
        complete = client.post(
            EXECUTION_FEEDBACK_EVENT_PATH,
            json=complete_body,
            headers=_headers(),
        )
        state = client.post(
            EXECUTION_FEEDBACK_STATE_PATH,
            json={},
            headers=_headers(),
        )
        feedback = client.post(
            EXECUTION_FEEDBACK_FEEDBACK_PATH,
            json={
                "planning_snapshot_id": str(plan.plan_id),
                "planning_snapshot_fingerprint": plan.plan_fingerprint,
            },
            headers=_headers(),
        )
        calibration = client.post(
            EXECUTION_FEEDBACK_CALIBRATION_PATH,
            json={
                "snapshots": [
                    {
                        "planning_snapshot_id": str(plan.plan_id),
                        "planning_snapshot_fingerprint": plan.plan_fingerprint,
                    }
                ]
            },
            headers=_headers(),
        )

    assert start.status_code == 200
    assert complete.status_code == 200
    assert complete.json()["report"]["aggregate_effort_delta_minutes"] == 5
    assert complete.json()["report"]["comparable_effort_count"] == 1
    assert complete.json()["item"]["state"] == "completed"
    assert state.status_code == 200
    assert state.json()["report"]["completed_count"] == 1
    assert feedback.status_code == 200
    assert feedback.json()["report"]["terminal_effort_coverage_denominator"] == 1
    assert calibration.status_code == 200
    assert calibration.json()["calibration"]["selected_plan_count"] == 1
    assert feedback.headers["cache-control"] == "no-store, private"
    assert feedback.headers["x-content-type-options"] == "nosniff"


def test_boundary_rejects_bad_authority_purpose_content_and_oversize() -> None:
    calls: list[str] = []

    class Stub:
        def state(self) -> dict[str, object]:
            calls.append("state")
            return {"current_plan": None, "report": None, "available_snapshots": [], "caveats": []}

        def event(self, _payload: object) -> dict[str, object]:
            calls.append("event")
            return {}

        def feedback(self, _payload: object) -> dict[str, object]:
            calls.append("feedback")
            return {}

        def calibration(self, _payload: object) -> dict[str, object]:
            calls.append("calibration")
            return {}

        def correction(self, _payload: object) -> dict[str, object]:
            calls.append("correction")
            return {}

    app = create_app(
        execution_feedback_web_service=cast(Any, Stub()),
        web_auth_config=_disabled_auth_config(),
    )
    with TestClient(app, base_url=BASE_URL) as client:
        wrong_origin = client.post(
            EXECUTION_FEEDBACK_STATE_PATH,
            json={},
            headers=_headers(Origin="http://evil.example.test"),
        )
        wrong_purpose = client.post(
            EXECUTION_FEEDBACK_STATE_PATH,
            json={},
            headers=_headers(**{"X-Second-Brain-Request": "personal-planning-v1"}),
        )
        wrong_content = client.post(
            EXECUTION_FEEDBACK_STATE_PATH,
            content=b"{}",
            headers=_headers(**{"Content-Type": "text/plain"}),
        )
        too_large = client.post(
            EXECUTION_FEEDBACK_STATE_PATH,
            content=b"{" + b"a" * MAX_RAW_EXECUTION_FEEDBACK_BODY_BYTES + b"}",
            headers=_headers(),
        )
        method = client.get(EXECUTION_FEEDBACK_STATE_PATH, headers={"Origin": BASE_URL})

    assert wrong_origin.status_code == 400
    assert wrong_purpose.status_code == 400
    assert wrong_content.status_code == 400
    assert too_large.status_code == 413
    assert method.status_code == 405
    assert calls == []


def test_anonymous_private_api_rejects_before_execution_service_work() -> None:
    calls: list[str] = []

    class Stub:
        def state(self) -> dict[str, object]:
            calls.append("state")
            return {}

        def event(self, _payload: object) -> dict[str, object]:
            calls.append("event")
            return {}

        def feedback(self, _payload: object) -> dict[str, object]:
            calls.append("feedback")
            return {}

        def calibration(self, _payload: object) -> dict[str, object]:
            calls.append("calibration")
            return {}

        def correction(self, _payload: object) -> dict[str, object]:
            calls.append("correction")
            return {}

    config = WebAuthConfig(
        mode="github",
        public_base_url="https://brain.example.test",
        github_client_id="client-id",
        github_client_secret="client-secret",
        allowed_user_id="42142321",
        session_secret=b"test-session-secret-not-a-credential-000000",
        session_ttl_seconds=DEFAULT_SESSION_TTL_SECONDS,
        oauth_state_ttl_seconds=DEFAULT_OAUTH_STATE_TTL_SECONDS,
        trusted_authorities=frozenset({("brain.example.test", 443)}),
    )
    app = create_app(
        execution_feedback_web_service=cast(Any, Stub()),
        web_auth_config=config,
    )
    with TestClient(app, base_url="https://brain.example.test") as client:
        response = client.post(
            EXECUTION_FEEDBACK_STATE_PATH,
            json={},
            headers=_headers(Origin="https://brain.example.test"),
        )

    assert response.status_code == 401
    assert response.json() == {"error": {"code": "AUTH_REQUIRED", "message": "Требуется вход"}}
    assert calls == []


def test_exact_lifecycle_idempotency_stale_source_and_correction(tmp_path: Path) -> None:
    plan = _plan()
    planning = _PlanningStub(plan, history=(plan,))
    service = _service(tmp_path, planning)
    payload = ExecutionFeedbackEventPayload.model_validate(
        _event_body(plan, event_type="start", operation_id="retry-key"),
        strict=True,
    )

    first = service.event(payload)
    replay = service.event(payload)
    first_event = cast(dict[str, object], first["event"])
    replay_event = cast(dict[str, object], replay["event"])
    assert first["status"] == "recorded"
    assert replay["status"] == "replayed"
    assert replay_event["event_id"] == first_event["event_id"]

    with pytest.raises(ExecutionFeedbackStateConflictError):
        service.event(
            ExecutionFeedbackEventPayload.model_validate(
                _event_body(plan, event_type="start", operation_id="different-key"),
                strict=True,
            )
        )

    correction = ExecutionFeedbackCorrectionPayload.model_validate(
        {
            "planning_snapshot_id": str(plan.plan_id),
            "planning_snapshot_fingerprint": plan.plan_fingerprint,
            "item_id": plan.items[0].item_id,
            "accepted_item_fingerprint": accepted_item_fingerprint(plan.items[0]),
            "operation_id": "void-start",
            "occurred_at": "2026-09-16T05:02:00Z",
            "void_target_event_id": first_event["event_id"],
            "void_target_event_fingerprint": first_event["event_fingerprint"],
            "correction_reason": "Исправлена ошибочная запись.",
        },
        strict=True,
    )
    corrected = service.correction(correction)
    corrected_event = cast(dict[str, object], corrected["event"])
    corrected_report = cast(dict[str, object], corrected["report"])
    assert corrected_event["event_type"] == "void"
    assert corrected_report["not_started_count"] == 1

    planning.source_current = False
    with pytest.raises(ExecutionFeedbackStalePlanError):
        service.event(
            ExecutionFeedbackEventPayload.model_validate(
                _event_body(plan, event_type="start", operation_id="stale-start"),
                strict=True,
            )
        )


def test_superseded_plan_cannot_start_but_started_chain_can_close(tmp_path: Path) -> None:
    old_plan = _plan(plan_id=UUID("0198f4c5-6a00-7000-8000-000000000041"))
    current_plan = _plan(plan_id=UUID("0198f4c5-6a00-7000-8000-000000000042"))
    planning = _PlanningStub(old_plan, history=(old_plan, current_plan))
    service = _service(tmp_path, planning)
    old_start = ExecutionFeedbackEventPayload.model_validate(
        _event_body(old_plan, event_type="start", operation_id="old-start"),
        strict=True,
    )
    service.event(old_start)
    planning.current = current_plan

    with pytest.raises(ExecutionFeedbackStalePlanError):
        service.event(
            ExecutionFeedbackEventPayload.model_validate(
                _event_body(old_plan, event_type="start", operation_id="old-second-start"),
                strict=True,
            )
        )
    closed = service.event(
        ExecutionFeedbackEventPayload.model_validate(
            _event_body(
                old_plan,
                event_type="complete",
                operation_id="old-complete",
                occurred_at="2026-09-16T05:20:00Z",
                effort=30,
                precision="exact",
            ),
            strict=True,
        )
    )
    closed_plan = cast(dict[str, object], closed["plan"])
    closed_item = cast(dict[str, object], closed["item"])
    assert closed_plan["source_status"] == "superseded"
    assert closed_item["state"] == "completed"


def test_calibration_requires_explicit_exact_selection_and_rejects_unknown_payload() -> None:
    plan = _plan()
    payload = ExecutionFeedbackCalibrationPayload.model_validate(
        {
            "snapshots": [
                {
                    "planning_snapshot_id": str(plan.plan_id),
                    "planning_snapshot_fingerprint": plan.plan_fingerprint,
                }
            ]
        },
        strict=True,
    )
    assert payload.snapshots[0].planning_snapshot_fingerprint == plan.plan_fingerprint
    with pytest.raises(ValueError):
        ExecutionFeedbackFeedbackPayload.model_validate(
            {
                "planning_snapshot_id": str(plan.plan_id),
                "planning_snapshot_fingerprint": plan.plan_fingerprint,
                "extra": "must fail",
            },
            strict=True,
        )


def test_reason_and_effort_inputs_are_strictly_bounded() -> None:
    plan = _plan()
    payload = _event_body(
        plan,
        event_type="block",
        operation_id="block-key",
        reasons=[ExecutionReasonCodeV1.DEPENDENCY.value],
    )
    payload["actual_effort_minutes"] = True
    with pytest.raises(ValueError):
        ExecutionFeedbackEventPayload.model_validate(payload, strict=True)
    payload["actual_effort_minutes"] = None
    payload["actual_result_note"] = "x" * 2049
    with pytest.raises(ValueError):
        ExecutionFeedbackEventPayload.model_validate(payload, strict=True)
