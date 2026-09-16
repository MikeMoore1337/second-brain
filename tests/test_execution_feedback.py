"""Adversarial tests for the provider-free Stage 18 execution core."""

from __future__ import annotations

import hashlib
from dataclasses import replace
from datetime import UTC, datetime
from uuid import UUID, uuid7

import pytest

from second_brain.application.execution_feedback import (
    EXECUTION_POLICY_CANONICAL_JSON,
    EXECUTION_POLICY_FINGERPRINT,
    ExecutionEffortPrecisionV1,
    ExecutionEventTypeV1,
    ExecutionEventV1,
    ExecutionFeedbackCorrectionError,
    ExecutionFeedbackItemMismatchError,
    ExecutionFeedbackLifecycleError,
    ExecutionFeedbackNonExecutableError,
    ExecutionFeedbackSnapshotMismatchError,
    ExecutionFeedbackStaleError,
    ExecutionLifecycleStateV1,
    ExecutionReasonCodeV1,
    PlanningPortfolioSnapshotV1,
    accepted_item_fingerprint,
    create_execution_event,
    execution_event_fingerprint,
    operation_id_fingerprint,
    replay_execution_events,
    validate_execution_event_for_plan,
    validate_execution_event_time,
    validate_execution_policy,
)
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
from second_brain.application.personal_planning_store import personal_planning_store_hash

NOW = datetime(2026, 9, 16, 5, 30, tzinfo=UTC)


def _item(
    *,
    item_id: str = "next-1",
    kind: PlanningItemKindV1 = PlanningItemKindV1.NEXT_ACTION,
    effort_minutes: int = 30,
) -> PlanningItemV1:
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
    return PlanningItemV1(
        item_id=item_id,
        kind=kind,
        title="Проверить один выбранный шаг",
        description="Сделать ограниченный шаг по выбранной цели.",
        goal_refs=(goal_ref,),
        action_refs=(action_ref,),
        parent_item_id=None,
        target_start_local="2026-09-16T09:00",
        target_end_local="2026-09-16T09:30",
        effort_minutes=effort_minutes,
        effort_source=PlanningEffortSourceV1.PROVIDER_PROPOSED,
        dependency_ids=(),
    )


def _plan(item: PlanningItemV1 | None = None) -> PlanningPortfolioSnapshotV1:
    selected = item or _item()
    core = {
        "plan_version": "1",
        "plan_id": str(UUID("0198f4c5-6a00-7000-8000-000000000001")),
        "revision": 1,
        "as_of": "2026-09-16T05:30:00Z",
        "source_pack_fingerprint": "4" * 64,
        "provider_envelope_fingerprint": "5" * 64,
        "provider_result_fingerprint": "6" * 64,
        "proposal_fingerprint": "7" * 64,
        "policy_id": PLANNING_POLICY_ID,
        "policy_fingerprint": PLANNING_POLICY_FINGERPRINT,
        "start_local": "2026-09-16",
        "end_local": "2026-09-16",
        "timezone": "UTC",
        "capacity": [{"date": "2026-09-16", "available_minutes": 60}],
        "fixed_windows": [],
        "items": [selected.as_dict()],
        "selected_item_ids": [selected.item_id],
        "item_order": [selected.item_id],
    }
    return PlanningPortfolioSnapshotV1(
        plan_version="1",
        plan_id=UUID("0198f4c5-6a00-7000-8000-000000000001"),
        revision=1,
        as_of=NOW,
        source_pack_fingerprint="4" * 64,
        provider_envelope_fingerprint="5" * 64,
        provider_result_fingerprint="6" * 64,
        proposal_fingerprint="7" * 64,
        policy_id=PLANNING_POLICY_ID,
        policy_fingerprint=PLANNING_POLICY_FINGERPRINT,
        start_local="2026-09-16",
        end_local="2026-09-16",
        timezone="UTC",
        capacity=(PlanningCapacityEntryV1(local_date="2026-09-16", available_minutes=60),),
        fixed_windows=(),
        items=(selected,),
        selected_item_ids=(selected.item_id,),
        item_order=(selected.item_id,),
        plan_fingerprint=personal_planning_store_hash(core),
    )


def _event(
    plan: PlanningPortfolioSnapshotV1,
    event_type: ExecutionEventTypeV1,
    *,
    operation: str,
    minute: int,
    effort: int | None = None,
    precision: ExecutionEffortPrecisionV1 = ExecutionEffortPrecisionV1.UNKNOWN,
    reasons: tuple[ExecutionReasonCodeV1, ...] = (),
    note: str = "",
    event_id: UUID | None = None,
    target_id: UUID | str | None = None,
    target_fingerprint: str | None = None,
    correction_reason: str | None = None,
) -> ExecutionEventV1:
    return create_execution_event(
        plan,
        item_id=plan.selected_item_ids[0],
        event_type=event_type,
        operation_id=operation,
        occurred_at=datetime(2026, 9, 16, 5, minute, tzinfo=UTC),
        received_at=datetime(2026, 9, 16, 6, 0, tzinfo=UTC),
        actual_effort_minutes=effort,
        effort_precision=precision,
        reason_codes=reasons,
        actual_result_note=note,
        event_id=event_id,
        void_target_event_id=target_id,
        void_target_event_fingerprint=target_fingerprint,
        correction_reason=correction_reason,
    )


def test_policy_and_exact_identity_are_closed() -> None:
    assert hashlib.sha256(EXECUTION_POLICY_CANONICAL_JSON.encode("utf-8")).hexdigest() == (
        EXECUTION_POLICY_FINGERPRINT
    )
    assert validate_execution_policy() == EXECUTION_POLICY_FINGERPRINT
    plan = _plan()
    item = plan.items[0]
    assert accepted_item_fingerprint(item) == personal_planning_store_hash(item.as_dict())
    assert operation_id_fingerprint("same-key") == operation_id_fingerprint("same-key")
    assert len(operation_id_fingerprint("same-key")) == 64


def test_event_round_trip_is_canonical_and_does_not_retain_operation_id() -> None:
    plan = _plan()
    event = _event(plan, ExecutionEventTypeV1.START, operation="private-operation", minute=31)
    assert "private-operation" not in event.to_json()
    assert type(event.from_json(event.to_json())) is type(event)
    assert event.from_json(event.to_json()) == event
    assert execution_event_fingerprint(event) == execution_event_fingerprint(event)


def test_lifecycle_replay_rejects_illegal_order_and_allows_explicit_backfill() -> None:
    plan = _plan()
    start = _event(plan, ExecutionEventTypeV1.START, operation="start", minute=31)
    pause = _event(plan, ExecutionEventTypeV1.PAUSE, operation="pause", minute=32)
    resume = _event(plan, ExecutionEventTypeV1.RESUME, operation="resume", minute=33)
    complete = _event(
        plan,
        ExecutionEventTypeV1.COMPLETE,
        operation="complete",
        minute=34,
        effort=35,
        precision=ExecutionEffortPrecisionV1.EXACT,
    )
    replay = replay_execution_events((start, pause, resume, complete))
    assert replay.state is ExecutionLifecycleStateV1.COMPLETED
    assert replay.terminal_event == complete
    with pytest.raises(ExecutionFeedbackLifecycleError):
        replay_execution_events((pause,))
    backfill = _event(
        plan,
        ExecutionEventTypeV1.COMPLETE,
        operation="backfill",
        minute=31,
        effort=0,
        precision=ExecutionEffortPrecisionV1.EXACT,
    )
    assert replay_execution_events((backfill,)).state is ExecutionLifecycleStateV1.COMPLETED


def test_block_requires_reason_and_terminal_unknown_is_not_zero() -> None:
    plan = _plan()
    with pytest.raises(ValueError):
        _event(plan, ExecutionEventTypeV1.BLOCK, operation="block", minute=31)
    blocked = _event(
        plan,
        ExecutionEventTypeV1.BLOCK,
        operation="block",
        minute=32,
        reasons=(ExecutionReasonCodeV1.DEPENDENCY,),
    )
    started = _event(plan, ExecutionEventTypeV1.START, operation="started", minute=31)
    assert replay_execution_events((started, blocked)).state is ExecutionLifecycleStateV1.BLOCKED
    unknown = _event(plan, ExecutionEventTypeV1.COMPLETE, operation="unknown", minute=32)
    assert unknown.actual_effort_minutes is None
    assert unknown.effort_precision is ExecutionEffortPrecisionV1.UNKNOWN


def test_exact_effort_is_terminal_bounded_and_nonterminal_cannot_fake_it() -> None:
    plan = _plan()
    with pytest.raises(ValueError):
        _event(
            plan,
            ExecutionEventTypeV1.START,
            operation="bad-start",
            minute=31,
            effort=1,
            precision=ExecutionEffortPrecisionV1.EXACT,
        )
    with pytest.raises(ValueError):
        _event(
            plan,
            ExecutionEventTypeV1.COMPLETE,
            operation="bad-effort",
            minute=31,
            effort=1441,
            precision=ExecutionEffortPrecisionV1.EXACT,
        )


def test_exact_binding_rejects_wrong_snapshot_or_caller_selected_text() -> None:
    plan = _plan()
    event = _event(plan, ExecutionEventTypeV1.START, operation="start", minute=31)
    other = replace(event, planning_snapshot_id=uuid7())
    with pytest.raises(ExecutionFeedbackSnapshotMismatchError):
        validate_execution_event_for_plan(other, plan, current_plan=plan)
    changed_item = replace(plan.items[0], title="Изменённый текст")
    with pytest.raises(ExecutionFeedbackItemMismatchError):
        changed_event = replace(
            event,
            accepted_item=changed_item,
            accepted_item_fingerprint=accepted_item_fingerprint(changed_item),
        )
        validate_execution_event_for_plan(changed_event, plan, current_plan=plan)


def test_non_executable_item_is_rejected_before_execution() -> None:
    project_plan = _plan(_item(kind=PlanningItemKindV1.PROJECT, effort_minutes=0))
    with pytest.raises(ExecutionFeedbackNonExecutableError):
        create_execution_event(
            project_plan,
            item_id=project_plan.selected_item_ids[0],
            event_type=ExecutionEventTypeV1.START,
            operation_id="project-start",
            occurred_at="2026-09-16T05:31:00+00:00",
        )


def test_start_is_fail_closed_for_stale_superseded_or_unavailable_source() -> None:
    plan = _plan()
    event = _event(plan, ExecutionEventTypeV1.START, operation="start", minute=31)
    with pytest.raises(ExecutionFeedbackStaleError):
        validate_execution_event_for_plan(event, plan, current_plan=None)
    with pytest.raises(ExecutionFeedbackStaleError):
        validate_execution_event_for_plan(event, plan, current_plan=plan, source_status="stale")
    assert (
        validate_execution_event_for_plan(
            event,
            plan,
            current_plan=plan,
            source_status="current",
        ).item_id
        == plan.selected_item_ids[0]
    )


def test_explicit_timestamp_rejects_missing_offset_and_future_fallback() -> None:
    with pytest.raises(ValueError):
        validate_execution_event_time(
            "2026-09-16T05:31:00",
            received_at="2026-09-16T05:31:00Z",
        )
    with pytest.raises(ValueError):
        validate_execution_event_time(
            "2026-09-16T06:37:00Z",
            received_at="2026-09-16T05:31:00Z",
        )


def test_void_correction_is_append_only_and_replays_the_effective_chain() -> None:
    plan = _plan()
    start = _event(plan, ExecutionEventTypeV1.START, operation="start", minute=31)
    correction = _event(
        plan,
        ExecutionEventTypeV1.VOID,
        operation="void-start",
        minute=32,
        event_id=uuid7(),
        target_id=start.event_id,
        target_fingerprint=execution_event_fingerprint(start),
        correction_reason="Владелец исправил ошибочную отметку.",
    )
    replay = replay_execution_events((start, correction))
    assert replay.state is ExecutionLifecycleStateV1.NOT_STARTED
    assert replay.voided_event_ids == (start.event_id,)
    assert replay.effective_events == ()
    with pytest.raises(ExecutionFeedbackCorrectionError):
        bad = replace(correction, void_target_event_fingerprint="9" * 64)
        replay_execution_events((start, bad))
