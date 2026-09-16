"""Deterministic Stage 18 projection, feedback, and calibration tests."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID, uuid7

import pytest

from second_brain.application.execution_feedback import (
    ExecutionDeviationCodeV1,
    ExecutionEffortPrecisionV1,
    ExecutionEventTypeV1,
    ExecutionEventV1,
    ExecutionReasonCodeV1,
    create_execution_event,
)
from second_brain.application.execution_feedback_projection import (
    ExecutionFeedbackProjectionHistoryError,
    ExecutionFeedbackProjectionInputError,
    ExecutionLifecycleStateV1,
    ExecutionWindowRelationV1,
    build_execution_calibration,
    build_execution_feedback_report,
    project_execution_item,
)
from second_brain.application.executive_strategy import (
    POLICY_FINGERPRINT as STAGE16_POLICY_FINGERPRINT,
)
from second_brain.application.executive_strategy import POLICY_ID as STAGE16_POLICY_ID
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


def _plan(
    *,
    plan_id: UUID | None = None,
    item_specs: tuple[tuple[str, PlanningItemKindV1, int, str | None, str | None], ...],
    timezone: str = "UTC",
    start_local: str = "2026-09-16",
    end_local: str = "2026-09-16",
) -> PlanningPlanV1:
    goal_id = uuid7()
    goal_ref = PlanningGoalRefV1(
        goal_source_uuid=goal_id,
        goal_identity_fingerprint="sha256:" + "1" * 64,
    )
    items: list[PlanningItemV1] = []
    for index, (item_id, kind, effort, target_start, target_end) in enumerate(item_specs):
        action_ref = PlanningActionBindingV1(
            goal_source_uuid=goal_id,
            goal_identity_fingerprint=goal_ref.goal_identity_fingerprint,
            strategy_snapshot_id=uuid7(),
            strategy_snapshot_fingerprint="2" * 64,
            reviewed_action_id=f"action-{index}",
            reviewed_action_fingerprint=(str(index + 3) * 64)[:64],
            stage16_policy_id=STAGE16_POLICY_ID,
            stage16_policy_fingerprint=STAGE16_POLICY_FINGERPRINT,
        )
        items.append(
            PlanningItemV1(
                item_id=item_id,
                kind=kind,
                title=f"Проверить {item_id}",
                description="Ограниченный шаг для projection-теста.",
                goal_refs=(goal_ref,),
                action_refs=(action_ref,),
                parent_item_id=None,
                target_start_local=target_start,
                target_end_local=target_end,
                effort_minutes=effort,
                effort_source=PlanningEffortSourceV1.PROVIDER_PROPOSED,
                dependency_ids=(),
            )
        )
    resolved_plan_id = plan_id or uuid7()
    core = {
        "plan_version": "1",
        "plan_id": str(resolved_plan_id),
        "revision": 1,
        "as_of": "2026-09-16T04:00:00Z",
        "source_pack_fingerprint": "4" * 64,
        "provider_envelope_fingerprint": "5" * 64,
        "provider_result_fingerprint": "6" * 64,
        "proposal_fingerprint": "7" * 64,
        "policy_id": PLANNING_POLICY_ID,
        "policy_fingerprint": PLANNING_POLICY_FINGERPRINT,
        "start_local": start_local,
        "end_local": end_local,
        "timezone": timezone,
        "capacity": [
            {"date": start_local, "available_minutes": sum(item[2] for item in item_specs)}
        ],
        "fixed_windows": [],
        "items": [item.as_dict() for item in items],
        "selected_item_ids": [item[0] for item in item_specs],
        "item_order": [item[0] for item in item_specs],
    }
    return PlanningPlanV1(
        plan_version="1",
        plan_id=resolved_plan_id,
        revision=1,
        as_of=datetime(2026, 9, 16, 4, tzinfo=UTC),
        source_pack_fingerprint="4" * 64,
        provider_envelope_fingerprint="5" * 64,
        provider_result_fingerprint="6" * 64,
        proposal_fingerprint="7" * 64,
        policy_id=PLANNING_POLICY_ID,
        policy_fingerprint=PLANNING_POLICY_FINGERPRINT,
        start_local=start_local,
        end_local=end_local,
        timezone=timezone,
        capacity=(
            PlanningCapacityEntryV1(
                local_date=start_local,
                available_minutes=sum(item[2] for item in item_specs),
            ),
        ),
        fixed_windows=(),
        items=tuple(items),
        selected_item_ids=tuple(item[0] for item in item_specs),
        item_order=tuple(item[0] for item in item_specs),
        plan_fingerprint=personal_planning_store_hash(core),
    )


def _event(
    plan: PlanningPlanV1,
    item_id: str,
    event_type: ExecutionEventTypeV1,
    operation: str,
    minute: int,
    *,
    effort: int | None = None,
    precision: ExecutionEffortPrecisionV1 = ExecutionEffortPrecisionV1.UNKNOWN,
    reasons: tuple[ExecutionReasonCodeV1, ...] = (),
    deviations: tuple[ExecutionDeviationCodeV1, ...] = (),
    hour: int = 5,
) -> ExecutionEventV1:
    return create_execution_event(
        plan,
        item_id=item_id,
        event_type=event_type,
        operation_id=operation,
        occurred_at=datetime(2026, 9, 16, hour, minute, tzinfo=UTC),
        received_at=datetime(2026, 9, 16, 7, 0, tzinfo=UTC),
        actual_effort_minutes=effort,
        effort_precision=precision,
        reason_codes=reasons,
        deviation_codes=deviations,
    )


def _events(*events: ExecutionEventV1) -> tuple[ExecutionEventV1, ...]:
    return events


def test_item_projection_replays_exact_history_and_terminal_feedback() -> None:
    plan = _plan(
        item_specs=(
            ("item-1", PlanningItemKindV1.NEXT_ACTION, 30, "2026-09-16T05:00", "2026-09-16T05:30"),
        )
    )
    start = _event(plan, "item-1", ExecutionEventTypeV1.START, "start", 1)
    pause = _event(plan, "item-1", ExecutionEventTypeV1.PAUSE, "pause", 5)
    resume = _event(plan, "item-1", ExecutionEventTypeV1.RESUME, "resume", 8)
    complete = _event(
        plan,
        "item-1",
        ExecutionEventTypeV1.COMPLETE,
        "complete",
        15,
        effort=35,
        precision=ExecutionEffortPrecisionV1.EXACT,
        deviations=(ExecutionDeviationCodeV1.ESTIMATE_MISMATCH,),
    )
    state = project_execution_item(
        plan, item_id="item-1", events=_events(start, pause, resume, complete)
    )
    assert state.state is ExecutionLifecycleStateV1.COMPLETED
    assert state.first_start_at == datetime(2026, 9, 16, 5, 1, tzinfo=UTC)
    assert state.terminal_at == datetime(2026, 9, 16, 5, 15, tzinfo=UTC)
    assert state.actual_effort_minutes == 35
    assert state.effort_precision is ExecutionEffortPrecisionV1.EXACT
    assert state.window_relation is ExecutionWindowRelationV1.WITHIN_WINDOW
    assert state.event_count == state.effective_event_count == 4
    assert state.terminal_feedback is not None
    assert state.terminal_feedback.deviation_codes == (ExecutionDeviationCodeV1.ESTIMATE_MISMATCH,)


def test_report_counts_states_and_excludes_unknown_effort_from_delta() -> None:
    specs = (
        ("complete", PlanningItemKindV1.NEXT_ACTION, 30, "2026-09-16T05:00", "2026-09-16T05:30"),
        ("unknown", PlanningItemKindV1.COMMITMENT, 20, "2026-09-16T05:00", "2026-09-16T05:30"),
        ("progress", PlanningItemKindV1.NEXT_ACTION, 15, None, None),
        ("blocked", PlanningItemKindV1.COMMITMENT, 10, None, None),
        ("paused", PlanningItemKindV1.NEXT_ACTION, 12, None, None),
        ("empty", PlanningItemKindV1.COMMITMENT, 8, None, None),
    )
    plan = _plan(item_specs=specs)
    events = _events(
        _event(plan, "complete", ExecutionEventTypeV1.START, "c-start", 1),
        _event(
            plan,
            "complete",
            ExecutionEventTypeV1.COMPLETE,
            "c-complete",
            15,
            effort=35,
            precision=ExecutionEffortPrecisionV1.EXACT,
        ),
        _event(plan, "unknown", ExecutionEventTypeV1.COMPLETE, "u-complete", 20),
        _event(plan, "progress", ExecutionEventTypeV1.START, "p-start", 2),
        _event(plan, "blocked", ExecutionEventTypeV1.START, "b-start", 3),
        _event(
            plan,
            "blocked",
            ExecutionEventTypeV1.BLOCK,
            "b-block",
            4,
            reasons=(ExecutionReasonCodeV1.DEPENDENCY,),
        ),
        _event(plan, "paused", ExecutionEventTypeV1.START, "pause-start", 6),
        _event(plan, "paused", ExecutionEventTypeV1.PAUSE, "pause", 7),
    )
    report = build_execution_feedback_report(plan, events)
    assert report.planned_executable_item_count == 6
    assert report.items_with_any_event_count == 5
    assert report.completed_count == 2
    assert report.in_progress_count == 1
    assert report.blocked_count == 1
    assert report.paused_count == 1
    assert report.not_started_count == 1
    assert report.terminal_exact_effort_count == 1
    assert report.terminal_unknown_effort_count == 1
    assert report.missing_terminal_effort_count == 4
    assert report.terminal_effort_coverage_denominator == 2
    assert report.comparable_effort_count == 1
    assert report.comparable_planned_effort_minutes == 30
    assert report.comparable_actual_effort_minutes == 35
    assert report.aggregate_effort_delta_minutes == 5
    assert report.effort_deltas[0].item_id == "complete"
    assert report.blocker_reason_counts[0].as_dict() == {"code": "dependency", "count": 1}
    assert "unknown_effort_excluded_from_comparable_total" in report.caveats


def test_window_relation_is_half_open_and_explicit() -> None:
    specs = (
        ("before", PlanningItemKindV1.NEXT_ACTION, 10, "2026-09-16T05:00", "2026-09-16T05:30"),
        ("within", PlanningItemKindV1.NEXT_ACTION, 10, "2026-09-16T05:00", "2026-09-16T05:30"),
        ("end", PlanningItemKindV1.NEXT_ACTION, 10, "2026-09-16T05:00", "2026-09-16T05:30"),
        ("none", PlanningItemKindV1.NEXT_ACTION, 10, None, None),
        ("open", PlanningItemKindV1.NEXT_ACTION, 10, "2026-09-16T05:00", "2026-09-16T05:30"),
    )
    plan = _plan(item_specs=specs)
    events = _events(
        _event(plan, "before", ExecutionEventTypeV1.COMPLETE, "before", 59, hour=4),
        _event(plan, "within", ExecutionEventTypeV1.COMPLETE, "within", 15),
        _event(plan, "end", ExecutionEventTypeV1.COMPLETE, "end", 30),
        _event(plan, "none", ExecutionEventTypeV1.COMPLETE, "none", 15),
        _event(plan, "open", ExecutionEventTypeV1.START, "open", 15),
    )
    report = build_execution_feedback_report(plan, events)
    assert report.before_window_count == 1
    assert report.within_window_count == 1
    assert report.after_window_count == 1
    assert report.no_window_count == 1
    assert report.unknown_window_count == 1
    assert report.window_relation_denominator == 5


def test_projection_rejects_cross_plan_event_and_does_not_infer_empty_state() -> None:
    item_specs = (("same-title", PlanningItemKindV1.NEXT_ACTION, 10, None, None),)
    plan = _plan(plan_id=UUID("0198f4c5-6a00-7000-8000-000000000011"), item_specs=item_specs)
    other_plan = _plan(
        plan_id=UUID("0198f4c5-6a00-7000-8000-000000000012"),
        item_specs=item_specs,
    )
    empty = build_execution_feedback_report(plan)
    assert empty.not_started_count == 1
    assert empty.items_with_any_event_count == 0
    with pytest.raises(ExecutionFeedbackProjectionHistoryError):
        build_execution_feedback_report(
            plan,
            _events(_event(other_plan, "same-title", ExecutionEventTypeV1.START, "foreign", 1)),
        )


def test_calibration_uses_only_explicit_exact_plan_selection() -> None:
    specs = (("same-title", PlanningItemKindV1.NEXT_ACTION, 20, None, None),)
    first = _plan(plan_id=UUID("0198f4c5-6a00-7000-8000-000000000021"), item_specs=specs)
    second = _plan(plan_id=UUID("0198f4c5-6a00-7000-8000-000000000022"), item_specs=specs)
    outside = _plan(plan_id=UUID("0198f4c5-6a00-7000-8000-000000000023"), item_specs=specs)
    first_event = _event(
        first,
        "same-title",
        ExecutionEventTypeV1.COMPLETE,
        "first-complete",
        15,
        effort=25,
        precision=ExecutionEffortPrecisionV1.EXACT,
    )
    second_event = _event(
        second,
        "same-title",
        ExecutionEventTypeV1.COMPLETE,
        "second-complete",
        15,
        effort=30,
        precision=ExecutionEffortPrecisionV1.EXACT,
    )
    outside_event = _event(
        outside,
        "same-title",
        ExecutionEventTypeV1.COMPLETE,
        "outside-complete",
        15,
        effort=999,
        precision=ExecutionEffortPrecisionV1.EXACT,
    )
    calibration = build_execution_calibration(
        (first, second),
        _events(first_event, second_event, outside_event),
    )
    assert calibration.selected_plan_count == 2
    assert calibration.selected_executable_item_count == 2
    assert calibration.execution_observed_item_count == 2
    assert calibration.effort_comparable_count == 2
    assert calibration.sum_planned_effort_minutes == 40
    assert calibration.sum_actual_effort_minutes == 55
    assert calibration.sum_delta_minutes == 15
    assert all(report.source_status.value == "superseded" for report in calibration.plan_reports)
    assert "descriptive_only_no_automatic_replanning" in calibration.caveats


def test_calibration_rejects_duplicate_exact_selection() -> None:
    plan = _plan(
        plan_id=UUID("0198f4c5-6a00-7000-8000-000000000031"),
        item_specs=(("item-1", PlanningItemKindV1.NEXT_ACTION, 10, None, None),),
    )
    with pytest.raises(ExecutionFeedbackProjectionInputError):
        build_execution_calibration((plan, plan))
