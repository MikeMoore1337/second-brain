"""Deterministic Stage 18 execution projections and calibration.

The functions in this module consume an exact accepted Stage 17 plan and its
validated Stage 18 history.  They never consult a provider, mutate a plan, or
turn missing/unknown owner feedback into an inferred value.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import Final, cast
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from second_brain.application.execution_feedback import (
    ExecutionDeviationCodeV1,
    ExecutionEffortPrecisionV1,
    ExecutionEventTypeV1,
    ExecutionEventV1,
    ExecutionFeedbackError,
    ExecutionFeedbackNonExecutableError,
    ExecutionFeedbackSnapshotMismatchError,
    ExecutionLifecycleStateV1,
    ExecutionReasonCodeV1,
    ExecutionReplayV1,
    ExecutionResultDispositionV1,
    ExecutionSourceStatusV1,
    accepted_item_fingerprint,
    bind_execution_event_to_plan,
    execution_event_fingerprint,
    format_execution_timestamp,
    replay_execution_events,
)
from second_brain.application.execution_feedback_store import (
    ExecutionFeedbackStoreEnvelopeV1,
    ExecutionFeedbackStoreVerifiedSnapshotV1,
)
from second_brain.application.personal_planning import (
    PlanningActionBindingV1,
    PlanningGoalRefV1,
    PlanningItemKindV1,
    PlanningItemV1,
)
from second_brain.application.personal_planning_store import PlanningPlanV1

EXECUTION_PROJECTION_MAX_EVENTS: Final[int] = 32768
EXECUTION_CALIBRATION_MAX_PLANS: Final[int] = 32
EXECUTION_CALIBRATION_MAX_ITEMS: Final[int] = 1024


class ExecutionFeedbackProjectionError(ValueError):
    """Safe error for invalid projection input or unresolvable history."""

    code: str = "execution_projection_invalid"

    def __init__(self, message: str | None = None) -> None:
        super().__init__(message or self.code)


class ExecutionFeedbackProjectionInputError(ExecutionFeedbackProjectionError):
    """The plan, event collection, or exact selection is invalid."""

    code = "invalid_projection_input"


class ExecutionFeedbackProjectionHistoryError(ExecutionFeedbackProjectionError):
    """The selected history cannot be replayed into a valid projection."""

    code = "invalid_projection_history"


class ExecutionWindowRelationV1(StrEnum):
    """Neutral temporal relation for a terminal event and target window."""

    WITHIN_WINDOW = "within_window"
    BEFORE_WINDOW = "before_window"
    AFTER_WINDOW = "after_window"
    NO_WINDOW = "no_window"
    UNKNOWN = "unknown"


ExecutionWindowRelation = ExecutionWindowRelationV1


@dataclass(frozen=True, slots=True)
class ExecutionReasonCountV1:
    """One transparent count for an owner-selected neutral code."""

    code: str
    count: int

    def as_dict(self) -> dict[str, object]:
        return {"code": self.code, "count": self.count}


@dataclass(frozen=True, slots=True)
class ExecutionEffortDeltaV1:
    """Comparable planned-versus-reported effort for one exact item."""

    item_id: str
    accepted_item_fingerprint: str
    planned_effort_minutes: int
    actual_effort_minutes: int
    delta_minutes: int

    def as_dict(self) -> dict[str, object]:
        return {
            "item_id": self.item_id,
            "accepted_item_fingerprint": self.accepted_item_fingerprint,
            "planned_effort_minutes": self.planned_effort_minutes,
            "actual_effort_minutes": self.actual_effort_minutes,
            "delta_minutes": self.delta_minutes,
        }


@dataclass(frozen=True, slots=True)
class ExecutionTerminalFeedbackV1:
    """Owner-entered fields from the effective terminal event."""

    event_id: UUID
    event_fingerprint: str
    occurred_at: datetime
    actual_effort_minutes: int | None
    effort_precision: ExecutionEffortPrecisionV1
    actual_result_note: str
    reason_codes: tuple[ExecutionReasonCodeV1, ...]
    deviation_codes: tuple[ExecutionDeviationCodeV1, ...]
    result_disposition: ExecutionResultDispositionV1 | None

    def as_dict(self) -> dict[str, object]:
        return {
            "event_id": str(self.event_id),
            "event_fingerprint": self.event_fingerprint,
            "occurred_at": format_execution_timestamp(self.occurred_at),
            "actual_effort_minutes": self.actual_effort_minutes,
            "effort_precision": self.effort_precision.value,
            "actual_result_note": self.actual_result_note,
            "reason_codes": [code.value for code in self.reason_codes],
            "deviation_codes": [code.value for code in self.deviation_codes],
            "result_disposition": (
                None if self.result_disposition is None else self.result_disposition.value
            ),
        }


@dataclass(frozen=True, slots=True)
class ExecutionHistoryEventV1:
    """Safe progressive-disclosure history row without store internals."""

    event_id: UUID
    event_fingerprint: str
    event_type: ExecutionEventTypeV1
    occurred_at: datetime
    effective: bool
    voided: bool
    correction: bool
    void_target_event_id: UUID | None
    actual_effort_minutes: int | None
    effort_precision: ExecutionEffortPrecisionV1
    actual_result_note: str
    reason_codes: tuple[ExecutionReasonCodeV1, ...]
    deviation_codes: tuple[ExecutionDeviationCodeV1, ...]
    result_disposition: ExecutionResultDispositionV1 | None

    def as_dict(self) -> dict[str, object]:
        return {
            "event_id": str(self.event_id),
            "event_fingerprint": self.event_fingerprint,
            "event_type": self.event_type.value,
            "occurred_at": format_execution_timestamp(self.occurred_at),
            "effective": self.effective,
            "voided": self.voided,
            "correction": self.correction,
            "void_target_event_id": (
                None if self.void_target_event_id is None else str(self.void_target_event_id)
            ),
            "actual_effort_minutes": self.actual_effort_minutes,
            "effort_precision": self.effort_precision.value,
            "actual_result_note": self.actual_result_note,
            "reason_codes": [code.value for code in self.reason_codes],
            "deviation_codes": [code.value for code in self.deviation_codes],
            "result_disposition": (
                None if self.result_disposition is None else self.result_disposition.value
            ),
        }


@dataclass(frozen=True, slots=True)
class ExecutionItemStateV1:
    """Deterministic state and provenance for one exact accepted item."""

    planning_snapshot_id: UUID
    planning_snapshot_fingerprint: str
    planning_plan_revision: int
    planning_policy_id: str
    planning_policy_fingerprint: str
    item_id: str
    accepted_item_fingerprint: str
    item_kind: PlanningItemKindV1
    title: str
    description: str
    goal_refs: tuple[PlanningGoalRefV1, ...]
    action_refs: tuple[PlanningActionBindingV1, ...]
    parent_item_id: str | None
    target_start_local: str | None
    target_end_local: str | None
    planning_timezone: str
    planned_effort_minutes: int
    source_status: ExecutionSourceStatusV1
    state: ExecutionLifecycleStateV1
    first_start_at: datetime | None
    latest_event_at: datetime | None
    latest_effective_event_at: datetime | None
    terminal_at: datetime | None
    current_block_reasons: tuple[ExecutionReasonCodeV1, ...]
    current_block_note: str
    actual_effort_minutes: int | None
    effort_precision: ExecutionEffortPrecisionV1
    terminal_feedback: ExecutionTerminalFeedbackV1 | None
    window_relation: ExecutionWindowRelationV1
    event_count: int
    effective_event_count: int
    voided_event_count: int
    correction_count: int
    history: tuple[ExecutionHistoryEventV1, ...]
    caveats: tuple[str, ...]

    @property
    def has_execution_event(self) -> bool:
        return self.event_count > 0

    @property
    def has_effective_start(self) -> bool:
        return self.first_start_at is not None

    @property
    def is_terminal(self) -> bool:
        return self.state in {
            ExecutionLifecycleStateV1.COMPLETED,
            ExecutionLifecycleStateV1.ABANDONED,
        }

    def as_dict(self) -> dict[str, object]:
        return {
            "planning_snapshot_id": str(self.planning_snapshot_id),
            "planning_snapshot_fingerprint": self.planning_snapshot_fingerprint,
            "planning_plan_revision": self.planning_plan_revision,
            "planning_policy_id": self.planning_policy_id,
            "planning_policy_fingerprint": self.planning_policy_fingerprint,
            "item_id": self.item_id,
            "accepted_item_fingerprint": self.accepted_item_fingerprint,
            "item_kind": self.item_kind.value,
            "title": self.title,
            "description": self.description,
            "goal_refs": [ref.as_dict() for ref in self.goal_refs],
            "action_refs": [ref.as_dict() for ref in self.action_refs],
            "parent_item_id": self.parent_item_id,
            "target_start_local": self.target_start_local,
            "target_end_local": self.target_end_local,
            "planning_timezone": self.planning_timezone,
            "planned_effort_minutes": self.planned_effort_minutes,
            "source_status": self.source_status.value,
            "state": self.state.value,
            "first_start_at": (
                None
                if self.first_start_at is None
                else format_execution_timestamp(self.first_start_at)
            ),
            "latest_event_at": (
                None
                if self.latest_event_at is None
                else format_execution_timestamp(self.latest_event_at)
            ),
            "latest_effective_event_at": (
                None
                if self.latest_effective_event_at is None
                else format_execution_timestamp(self.latest_effective_event_at)
            ),
            "terminal_at": (
                None if self.terminal_at is None else format_execution_timestamp(self.terminal_at)
            ),
            "current_block_reasons": [code.value for code in self.current_block_reasons],
            "current_block_note": self.current_block_note,
            "actual_effort_minutes": self.actual_effort_minutes,
            "effort_precision": self.effort_precision.value,
            "terminal_feedback": (
                None if self.terminal_feedback is None else self.terminal_feedback.as_dict()
            ),
            "window_relation": self.window_relation.value,
            "event_count": self.event_count,
            "effective_event_count": self.effective_event_count,
            "voided_event_count": self.voided_event_count,
            "correction_count": self.correction_count,
            "history": [event.as_dict() for event in self.history],
            "caveats": list(self.caveats),
        }


@dataclass(frozen=True, slots=True)
class ExecutionFeedbackReportV1:
    """Transparent plan-level execution feedback with explicit denominators."""

    planning_snapshot_id: UUID
    planning_snapshot_fingerprint: str
    planning_plan_revision: int
    planning_policy_id: str
    planning_policy_fingerprint: str
    plan_start_local: str
    plan_end_local: str
    planning_timezone: str
    source_status: ExecutionSourceStatusV1
    selected_item_count: int
    planned_executable_item_count: int
    items_with_any_event_count: int
    items_with_effective_start_count: int
    not_started_count: int
    in_progress_count: int
    paused_count: int
    blocked_count: int
    completed_count: int
    abandoned_count: int
    terminal_count: int
    terminal_exact_effort_count: int
    terminal_unknown_effort_count: int
    missing_terminal_effort_count: int
    terminal_effort_coverage_denominator: int
    comparable_effort_count: int
    comparable_planned_effort_minutes: int
    comparable_actual_effort_minutes: int
    aggregate_effort_delta_minutes: int
    effort_deltas: tuple[ExecutionEffortDeltaV1, ...]
    within_window_count: int
    before_window_count: int
    after_window_count: int
    no_window_count: int
    unknown_window_count: int
    window_relation_denominator: int
    blocker_reason_counts: tuple[ExecutionReasonCountV1, ...]
    terminal_reason_counts: tuple[ExecutionReasonCountV1, ...]
    deviation_reason_counts: tuple[ExecutionReasonCountV1, ...]
    item_states: tuple[ExecutionItemStateV1, ...]
    caveats: tuple[str, ...]

    def as_dict(self) -> dict[str, object]:
        return {
            "planning_snapshot_id": str(self.planning_snapshot_id),
            "planning_snapshot_fingerprint": self.planning_snapshot_fingerprint,
            "planning_plan_revision": self.planning_plan_revision,
            "planning_policy_id": self.planning_policy_id,
            "planning_policy_fingerprint": self.planning_policy_fingerprint,
            "plan_start_local": self.plan_start_local,
            "plan_end_local": self.plan_end_local,
            "planning_timezone": self.planning_timezone,
            "source_status": self.source_status.value,
            "selected_item_count": self.selected_item_count,
            "planned_executable_item_count": self.planned_executable_item_count,
            "items_with_any_event_count": self.items_with_any_event_count,
            "items_with_effective_start_count": self.items_with_effective_start_count,
            "not_started_count": self.not_started_count,
            "in_progress_count": self.in_progress_count,
            "paused_count": self.paused_count,
            "blocked_count": self.blocked_count,
            "completed_count": self.completed_count,
            "abandoned_count": self.abandoned_count,
            "terminal_count": self.terminal_count,
            "terminal_exact_effort_count": self.terminal_exact_effort_count,
            "terminal_unknown_effort_count": self.terminal_unknown_effort_count,
            "missing_terminal_effort_count": self.missing_terminal_effort_count,
            "terminal_effort_coverage_denominator": self.terminal_effort_coverage_denominator,
            "comparable_effort_count": self.comparable_effort_count,
            "comparable_planned_effort_minutes": self.comparable_planned_effort_minutes,
            "comparable_actual_effort_minutes": self.comparable_actual_effort_minutes,
            "aggregate_effort_delta_minutes": self.aggregate_effort_delta_minutes,
            "effort_deltas": [delta.as_dict() for delta in self.effort_deltas],
            "within_window_count": self.within_window_count,
            "before_window_count": self.before_window_count,
            "after_window_count": self.after_window_count,
            "no_window_count": self.no_window_count,
            "unknown_window_count": self.unknown_window_count,
            "window_relation_denominator": self.window_relation_denominator,
            "blocker_reason_counts": [item.as_dict() for item in self.blocker_reason_counts],
            "terminal_reason_counts": [item.as_dict() for item in self.terminal_reason_counts],
            "deviation_reason_counts": [item.as_dict() for item in self.deviation_reason_counts],
            "items": [item.as_dict() for item in self.item_states],
            "caveats": list(self.caveats),
        }


@dataclass(frozen=True, slots=True)
class ExecutionCalibrationV1:
    """Owner-selected bounded aggregate over exact historical plans."""

    selected_plan_count: int
    selected_executable_item_count: int
    execution_observed_item_count: int
    not_started_count: int
    in_progress_count: int
    paused_count: int
    blocked_current_count: int
    completed_count: int
    abandoned_count: int
    terminal_exact_effort_count: int
    terminal_unknown_effort_count: int
    missing_terminal_effort_count: int
    terminal_effort_coverage_denominator: int
    effort_comparable_count: int
    sum_planned_effort_minutes: int
    sum_actual_effort_minutes: int
    sum_delta_minutes: int
    within_window_count: int
    before_window_count: int
    after_window_count: int
    no_window_count: int
    unknown_window_count: int
    window_relation_denominator: int
    blocker_reason_counts: tuple[ExecutionReasonCountV1, ...]
    terminal_reason_counts: tuple[ExecutionReasonCountV1, ...]
    deviation_reason_counts: tuple[ExecutionReasonCountV1, ...]
    plan_reports: tuple[ExecutionFeedbackReportV1, ...]
    caveats: tuple[str, ...]

    def as_dict(self) -> dict[str, object]:
        return {
            "selected_plan_count": self.selected_plan_count,
            "selected_executable_item_count": self.selected_executable_item_count,
            "execution_observed_item_count": self.execution_observed_item_count,
            "not_started_count": self.not_started_count,
            "in_progress_count": self.in_progress_count,
            "paused_count": self.paused_count,
            "blocked_current_count": self.blocked_current_count,
            "completed_count": self.completed_count,
            "abandoned_count": self.abandoned_count,
            "terminal_exact_effort_count": self.terminal_exact_effort_count,
            "terminal_unknown_effort_count": self.terminal_unknown_effort_count,
            "missing_terminal_effort_count": self.missing_terminal_effort_count,
            "terminal_effort_coverage_denominator": self.terminal_effort_coverage_denominator,
            "effort_comparable_count": self.effort_comparable_count,
            "sum_planned_effort_minutes": self.sum_planned_effort_minutes,
            "sum_actual_effort_minutes": self.sum_actual_effort_minutes,
            "sum_delta_minutes": self.sum_delta_minutes,
            "within_window_count": self.within_window_count,
            "before_window_count": self.before_window_count,
            "after_window_count": self.after_window_count,
            "no_window_count": self.no_window_count,
            "unknown_window_count": self.unknown_window_count,
            "window_relation_denominator": self.window_relation_denominator,
            "blocker_reason_counts": [item.as_dict() for item in self.blocker_reason_counts],
            "terminal_reason_counts": [item.as_dict() for item in self.terminal_reason_counts],
            "deviation_reason_counts": [item.as_dict() for item in self.deviation_reason_counts],
            "plans": [report.as_dict() for report in self.plan_reports],
            "caveats": list(self.caveats),
        }


PlanningCalibrationAggregateV1 = ExecutionCalibrationV1
ExecutionPlanFeedbackV1 = ExecutionFeedbackReportV1


def _projection_invalid() -> ExecutionFeedbackProjectionInputError:
    return ExecutionFeedbackProjectionInputError()


def _source_status(value: ExecutionSourceStatusV1 | str) -> ExecutionSourceStatusV1:
    if type(value) is ExecutionSourceStatusV1:
        return value
    if type(value) is str:
        try:
            return ExecutionSourceStatusV1(value)
        except ValueError:
            pass
    raise _projection_invalid()


def _event_records(
    events: tuple[ExecutionEventV1 | ExecutionFeedbackStoreEnvelopeV1, ...]
    | ExecutionFeedbackStoreVerifiedSnapshotV1,
) -> tuple[ExecutionEventV1, ...]:
    if type(events) is ExecutionFeedbackStoreVerifiedSnapshotV1:
        return tuple(envelope.record for envelope in events.envelopes)
    if type(events) is not tuple or len(events) > EXECUTION_PROJECTION_MAX_EVENTS:
        raise _projection_invalid()
    records: list[ExecutionEventV1] = []
    for value in events:
        if type(value) is ExecutionEventV1:
            records.append(value)
        elif type(value) is ExecutionFeedbackStoreEnvelopeV1:
            records.append(value.record)
        else:
            raise _projection_invalid()
    return tuple(records)


def _require_plan(plan: PlanningPlanV1) -> PlanningPlanV1:
    if type(plan) is not PlanningPlanV1:
        raise _projection_invalid()
    return plan


def _selected_items(plan: PlanningPlanV1) -> tuple[PlanningItemV1, ...]:
    item_by_id = {item.item_id: item for item in plan.items}
    selected: list[PlanningItemV1] = []
    for item_id in plan.item_order:
        if item_id not in plan.selected_item_ids:
            raise _projection_invalid()
        item = item_by_id.get(item_id)
        if item is None:
            raise _projection_invalid()
        if item.kind in {PlanningItemKindV1.COMMITMENT, PlanningItemKindV1.NEXT_ACTION}:
            selected.append(item)
    if set(item_by_id).intersection(plan.selected_item_ids) != set(plan.selected_item_ids):
        raise _projection_invalid()
    return tuple(selected)


def _group_plan_events(
    plan: PlanningPlanV1,
    records: tuple[ExecutionEventV1, ...],
) -> dict[str, tuple[ExecutionEventV1, ...]]:
    selected = {item.item_id for item in _selected_items(plan)}
    grouped: dict[str, list[ExecutionEventV1]] = {}
    for event in records:
        try:
            bind_execution_event_to_plan(event, plan)
        except (
            ExecutionFeedbackError,
            ExecutionFeedbackSnapshotMismatchError,
            ExecutionFeedbackNonExecutableError,
        ) as exc:
            raise ExecutionFeedbackProjectionHistoryError() from exc
        if event.item_id not in selected:
            raise ExecutionFeedbackProjectionHistoryError()
        grouped.setdefault(event.item_id, []).append(event)
    return {item_id: tuple(item_events) for item_id, item_events in grouped.items()}


def _resolve_local(naive: datetime, timezone: str) -> datetime | None:
    try:
        zone = UTC if timezone == "UTC" else ZoneInfo(timezone)
    except ZoneInfoNotFoundError, ValueError:
        return None
    candidates: list[datetime] = []
    for fold in (0, 1):
        candidate = naive.replace(tzinfo=zone, fold=fold).astimezone(UTC)
        if candidate.astimezone(zone).replace(tzinfo=None) == naive and candidate not in candidates:
            candidates.append(candidate)
    if len(candidates) != 1:
        return None
    return candidates[0]


def _window_relation(
    item: PlanningItemV1,
    plan: PlanningPlanV1,
    terminal_event: ExecutionEventV1 | None,
) -> ExecutionWindowRelationV1:
    if terminal_event is None:
        return ExecutionWindowRelationV1.UNKNOWN
    if item.target_start_local is None or item.target_end_local is None:
        return ExecutionWindowRelationV1.NO_WINDOW
    try:
        start_local = datetime.fromisoformat(item.target_start_local)
        end_local = datetime.fromisoformat(item.target_end_local)
    except ValueError:
        return ExecutionWindowRelationV1.UNKNOWN
    start = _resolve_local(start_local, plan.timezone)
    end = _resolve_local(end_local, plan.timezone)
    if start is None or end is None or end <= start:
        return ExecutionWindowRelationV1.UNKNOWN
    occurred_at = cast(datetime, terminal_event.occurred_at)
    if occurred_at < start:
        return ExecutionWindowRelationV1.BEFORE_WINDOW
    if occurred_at < end:
        return ExecutionWindowRelationV1.WITHIN_WINDOW
    return ExecutionWindowRelationV1.AFTER_WINDOW


def _history_row(
    event: ExecutionEventV1, *, voided_ids: frozenset[UUID]
) -> ExecutionHistoryEventV1:
    event_id = cast(UUID, event.event_id)
    event_type = cast(ExecutionEventTypeV1, event.event_type)
    return ExecutionHistoryEventV1(
        event_id=event_id,
        event_fingerprint=execution_event_fingerprint(event),
        event_type=event_type,
        occurred_at=cast(datetime, event.occurred_at),
        effective=event_type is not ExecutionEventTypeV1.VOID and event_id not in voided_ids,
        voided=event_id in voided_ids,
        correction=event_type is ExecutionEventTypeV1.VOID,
        void_target_event_id=(
            None if event.void_target_event_id is None else cast(UUID, event.void_target_event_id)
        ),
        actual_effort_minutes=event.actual_effort_minutes,
        effort_precision=cast(ExecutionEffortPrecisionV1, event.effort_precision),
        actual_result_note=event.actual_result_note,
        reason_codes=tuple(cast(ExecutionReasonCodeV1, code) for code in event.reason_codes),
        deviation_codes=tuple(
            cast(ExecutionDeviationCodeV1, code) for code in event.deviation_codes
        ),
        result_disposition=(
            None
            if event.result_disposition is None
            else cast(ExecutionResultDispositionV1, event.result_disposition)
        ),
    )


def _terminal_feedback(event: ExecutionEventV1 | None) -> ExecutionTerminalFeedbackV1 | None:
    if event is None:
        return None
    return ExecutionTerminalFeedbackV1(
        event_id=cast(UUID, event.event_id),
        event_fingerprint=execution_event_fingerprint(event),
        occurred_at=cast(datetime, event.occurred_at),
        actual_effort_minutes=event.actual_effort_minutes,
        effort_precision=cast(ExecutionEffortPrecisionV1, event.effort_precision),
        actual_result_note=event.actual_result_note,
        reason_codes=tuple(cast(ExecutionReasonCodeV1, code) for code in event.reason_codes),
        deviation_codes=tuple(
            cast(ExecutionDeviationCodeV1, code) for code in event.deviation_codes
        ),
        result_disposition=(
            None
            if event.result_disposition is None
            else cast(ExecutionResultDispositionV1, event.result_disposition)
        ),
    )


def _item_caveats(
    events: tuple[ExecutionEventV1, ...],
    replay: ExecutionReplayV1,
    relation: ExecutionWindowRelationV1,
) -> tuple[str, ...]:
    caveats: list[str] = []
    if not events:
        caveats.append("no_execution_event")
    if replay.terminal_event is None:
        caveats.append("terminal_event_missing")
    elif replay.terminal_event.effort_precision is ExecutionEffortPrecisionV1.UNKNOWN:
        caveats.append("actual_effort_unknown")
    if relation is ExecutionWindowRelationV1.UNKNOWN:
        caveats.append("window_relation_unknown")
    elif relation is ExecutionWindowRelationV1.NO_WINDOW:
        caveats.append("target_window_missing")
    if replay.voided_event_ids:
        caveats.append("history_contains_void_correction")
    return tuple(caveats)


def _project_item_events(
    plan: PlanningPlanV1,
    item: PlanningItemV1,
    events: tuple[ExecutionEventV1, ...],
    *,
    source_status: ExecutionSourceStatusV1,
) -> ExecutionItemStateV1:
    try:
        replay = replay_execution_events(events, item_id=item.item_id)
    except (ExecutionFeedbackError, TypeError, ValueError) as exc:
        raise ExecutionFeedbackProjectionHistoryError() from exc
    effective_events = replay.effective_events
    terminal_event = replay.terminal_event
    first_start = next(
        (
            cast(datetime, event.occurred_at)
            for event in effective_events
            if event.event_type is ExecutionEventTypeV1.START
        ),
        None,
    )
    latest_raw = None if not events else cast(datetime, events[-1].occurred_at)
    latest_effective = (
        None if replay.latest_event is None else cast(datetime, replay.latest_event.occurred_at)
    )
    block_event = (
        effective_events[-1]
        if replay.state is ExecutionLifecycleStateV1.BLOCKED
        and effective_events
        and effective_events[-1].event_type is ExecutionEventTypeV1.BLOCK
        else None
    )
    relation = _window_relation(item, plan, terminal_event)
    terminal_feedback = _terminal_feedback(terminal_event)
    return ExecutionItemStateV1(
        planning_snapshot_id=cast(UUID, plan.plan_id),
        planning_snapshot_fingerprint=plan.plan_fingerprint,
        planning_plan_revision=plan.revision,
        planning_policy_id=plan.policy_id,
        planning_policy_fingerprint=plan.policy_fingerprint,
        item_id=item.item_id,
        accepted_item_fingerprint=accepted_item_fingerprint(item),
        item_kind=cast(PlanningItemKindV1, item.kind),
        title=item.title,
        description=item.description,
        goal_refs=item.goal_refs,
        action_refs=item.action_refs,
        parent_item_id=item.parent_item_id,
        target_start_local=item.target_start_local,
        target_end_local=item.target_end_local,
        planning_timezone=plan.timezone,
        planned_effort_minutes=item.effort_minutes,
        source_status=source_status,
        state=replay.state,
        first_start_at=first_start,
        latest_event_at=latest_raw,
        latest_effective_event_at=latest_effective,
        terminal_at=(
            None if terminal_event is None else cast(datetime, terminal_event.occurred_at)
        ),
        current_block_reasons=(
            ()
            if block_event is None
            else tuple(cast(ExecutionReasonCodeV1, code) for code in block_event.reason_codes)
        ),
        current_block_note="" if block_event is None else block_event.actual_result_note,
        actual_effort_minutes=(
            None
            if terminal_event is None
            or terminal_event.effort_precision is ExecutionEffortPrecisionV1.UNKNOWN
            else terminal_event.actual_effort_minutes
        ),
        effort_precision=(
            ExecutionEffortPrecisionV1.UNKNOWN
            if terminal_event is None
            else cast(ExecutionEffortPrecisionV1, terminal_event.effort_precision)
        ),
        terminal_feedback=terminal_feedback,
        window_relation=relation,
        event_count=len(events),
        effective_event_count=len(effective_events),
        voided_event_count=len(replay.voided_event_ids),
        correction_count=len(replay.correction_events),
        history=tuple(
            _history_row(event, voided_ids=frozenset(replay.voided_event_ids)) for event in events
        ),
        caveats=_item_caveats(events, replay, relation),
    )


def project_execution_item(
    plan: PlanningPlanV1,
    *,
    item_id: str,
    events: tuple[ExecutionEventV1 | ExecutionFeedbackStoreEnvelopeV1, ...]
    | ExecutionFeedbackStoreVerifiedSnapshotV1 = (),
    source_status: ExecutionSourceStatusV1 | str = ExecutionSourceStatusV1.CURRENT,
) -> ExecutionItemStateV1:
    """Project one exact selected executable item and its exact history."""

    plan = _require_plan(plan)
    status = _source_status(source_status)
    records = _event_records(events)
    selected = {item.item_id: item for item in _selected_items(plan)}
    item = selected.get(item_id)
    if item is None:
        raise ExecutionFeedbackProjectionInputError()
    grouped = _group_plan_events(plan, records)
    return _project_item_events(plan, item, grouped.get(item.item_id, ()), source_status=status)


def _reason_counts(counter: Counter[str]) -> tuple[ExecutionReasonCountV1, ...]:
    return tuple(ExecutionReasonCountV1(code=code, count=counter[code]) for code in sorted(counter))


def _report_caveats(states: tuple[ExecutionItemStateV1, ...]) -> tuple[str, ...]:
    caveats: list[str] = []
    if not states:
        caveats.append("no_executable_selected_items")
    if any("actual_effort_unknown" in state.caveats for state in states):
        caveats.append("unknown_effort_excluded_from_comparable_total")
    if any("terminal_event_missing" in state.caveats for state in states):
        caveats.append("incomplete_execution_history")
    if any(state.window_relation is ExecutionWindowRelationV1.UNKNOWN for state in states):
        caveats.append("window_relation_has_incomplete_or_ambiguous_items")
    if any(state.voided_event_count for state in states):
        caveats.append("voided_events_remain_auditable")
    return tuple(caveats)


def build_execution_feedback_report(
    plan: PlanningPlanV1,
    events: tuple[ExecutionEventV1 | ExecutionFeedbackStoreEnvelopeV1, ...]
    | ExecutionFeedbackStoreVerifiedSnapshotV1 = (),
    *,
    source_status: ExecutionSourceStatusV1 | str = ExecutionSourceStatusV1.CURRENT,
) -> ExecutionFeedbackReportV1:
    """Build a descriptive report with exact denominators and no score."""

    plan = _require_plan(plan)
    status = _source_status(source_status)
    records = _event_records(events)
    selected_all = tuple(
        item for item_id in plan.item_order for item in plan.items if item.item_id == item_id
    )
    executable = _selected_items(plan)
    grouped = _group_plan_events(plan, records)
    states = tuple(
        _project_item_events(plan, item, grouped.get(item.item_id, ()), source_status=status)
        for item in executable
    )
    state_counts = Counter(state.state for state in states)
    deltas: list[ExecutionEffortDeltaV1] = []
    terminal_exact = 0
    terminal_unknown = 0
    missing_terminal = 0
    blocker_reasons: Counter[str] = Counter()
    terminal_reasons: Counter[str] = Counter()
    deviation_reasons: Counter[str] = Counter()
    for state in states:
        if not state.is_terminal:
            missing_terminal += 1
        elif state.effort_precision is ExecutionEffortPrecisionV1.EXACT:
            terminal_exact += 1
            if state.actual_effort_minutes is None:
                raise ExecutionFeedbackProjectionHistoryError()
            deltas.append(
                ExecutionEffortDeltaV1(
                    item_id=state.item_id,
                    accepted_item_fingerprint=state.accepted_item_fingerprint,
                    planned_effort_minutes=state.planned_effort_minutes,
                    actual_effort_minutes=state.actual_effort_minutes,
                    delta_minutes=state.actual_effort_minutes - state.planned_effort_minutes,
                )
            )
        else:
            terminal_unknown += 1
        for history in state.history:
            if not history.effective:
                continue
            if history.event_type is ExecutionEventTypeV1.BLOCK:
                blocker_reasons.update(code.value for code in history.reason_codes)
            if history.event_type in {ExecutionEventTypeV1.COMPLETE, ExecutionEventTypeV1.ABANDON}:
                terminal_reasons.update(code.value for code in history.reason_codes)
                deviation_reasons.update(code.value for code in history.deviation_codes)
    relation_counts = Counter(state.window_relation for state in states)
    planned_total = sum(item.planned_effort_minutes for item in deltas)
    actual_total = sum(item.actual_effort_minutes for item in deltas)
    aggregate_delta = actual_total - planned_total
    return ExecutionFeedbackReportV1(
        planning_snapshot_id=cast(UUID, plan.plan_id),
        planning_snapshot_fingerprint=plan.plan_fingerprint,
        planning_plan_revision=plan.revision,
        planning_policy_id=plan.policy_id,
        planning_policy_fingerprint=plan.policy_fingerprint,
        plan_start_local=plan.start_local,
        plan_end_local=plan.end_local,
        planning_timezone=plan.timezone,
        source_status=status,
        selected_item_count=len(selected_all),
        planned_executable_item_count=len(executable),
        items_with_any_event_count=sum(state.has_execution_event for state in states),
        items_with_effective_start_count=sum(state.has_effective_start for state in states),
        not_started_count=state_counts[ExecutionLifecycleStateV1.NOT_STARTED],
        in_progress_count=state_counts[ExecutionLifecycleStateV1.IN_PROGRESS],
        paused_count=state_counts[ExecutionLifecycleStateV1.PAUSED],
        blocked_count=state_counts[ExecutionLifecycleStateV1.BLOCKED],
        completed_count=state_counts[ExecutionLifecycleStateV1.COMPLETED],
        abandoned_count=state_counts[ExecutionLifecycleStateV1.ABANDONED],
        terminal_count=terminal_exact + terminal_unknown,
        terminal_exact_effort_count=terminal_exact,
        terminal_unknown_effort_count=terminal_unknown,
        missing_terminal_effort_count=missing_terminal,
        terminal_effort_coverage_denominator=terminal_exact + terminal_unknown,
        comparable_effort_count=len(deltas),
        comparable_planned_effort_minutes=planned_total,
        comparable_actual_effort_minutes=actual_total,
        aggregate_effort_delta_minutes=aggregate_delta,
        effort_deltas=tuple(deltas),
        within_window_count=relation_counts[ExecutionWindowRelationV1.WITHIN_WINDOW],
        before_window_count=relation_counts[ExecutionWindowRelationV1.BEFORE_WINDOW],
        after_window_count=relation_counts[ExecutionWindowRelationV1.AFTER_WINDOW],
        no_window_count=relation_counts[ExecutionWindowRelationV1.NO_WINDOW],
        unknown_window_count=relation_counts[ExecutionWindowRelationV1.UNKNOWN],
        window_relation_denominator=len(states),
        blocker_reason_counts=_reason_counts(blocker_reasons),
        terminal_reason_counts=_reason_counts(terminal_reasons),
        deviation_reason_counts=_reason_counts(deviation_reasons),
        item_states=states,
        caveats=_report_caveats(states),
    )


project_execution_plan = build_execution_feedback_report
build_execution_projection = build_execution_feedback_report


def _calibration_caveats(reports: tuple[ExecutionFeedbackReportV1, ...]) -> tuple[str, ...]:
    caveats = ["descriptive_only_no_automatic_replanning"]
    if any(report.caveats for report in reports):
        caveats.append("selected_history_has_explicit_coverage_caveats")
    return tuple(caveats)


def build_execution_calibration(
    plans: tuple[PlanningPlanV1, ...],
    events: tuple[ExecutionEventV1 | ExecutionFeedbackStoreEnvelopeV1, ...]
    | ExecutionFeedbackStoreVerifiedSnapshotV1 = (),
) -> ExecutionCalibrationV1:
    """Aggregate an explicit exact historical plan selection.

    Events belonging to plans outside the explicit selection are ignored by
    exact snapshot identity.  Events selected by a plan's exact identity are
    fully validated and can never be rebound by title or item text.
    """

    if type(plans) is not tuple or not 1 <= len(plans) <= EXECUTION_CALIBRATION_MAX_PLANS:
        raise ExecutionFeedbackProjectionInputError()
    selected_plans = tuple(_require_plan(plan) for plan in plans)
    plan_keys: set[tuple[UUID, str]] = set()
    plan_ids: set[UUID] = set()
    for plan in selected_plans:
        plan_id = cast(UUID, plan.plan_id)
        key = (plan_id, plan.plan_fingerprint)
        if key in plan_keys or plan_id in plan_ids:
            raise ExecutionFeedbackProjectionInputError()
        plan_keys.add(key)
        plan_ids.add(plan_id)
    executable_count = sum(len(_selected_items(plan)) for plan in selected_plans)
    if executable_count > EXECUTION_CALIBRATION_MAX_ITEMS:
        raise ExecutionFeedbackProjectionInputError()
    records = _event_records(events)
    events_by_plan: dict[tuple[UUID, str], list[ExecutionEventV1]] = {}
    for event in records:
        key = (cast(UUID, event.planning_snapshot_id), event.planning_snapshot_fingerprint)
        if key in plan_keys:
            events_by_plan.setdefault(key, []).append(event)
    reports = tuple(
        build_execution_feedback_report(
            plan,
            tuple(events_by_plan.get((cast(UUID, plan.plan_id), plan.plan_fingerprint), ())),
            source_status=ExecutionSourceStatusV1.SUPERSEDED,
        )
        for plan in selected_plans
    )
    blocker_reasons: Counter[str] = Counter()
    terminal_reasons: Counter[str] = Counter()
    deviation_reasons: Counter[str] = Counter()
    for report in reports:
        blocker_reasons.update({item.code: item.count for item in report.blocker_reason_counts})
        terminal_reasons.update({item.code: item.count for item in report.terminal_reason_counts})
        deviation_reasons.update({item.code: item.count for item in report.deviation_reason_counts})
    return ExecutionCalibrationV1(
        selected_plan_count=len(reports),
        selected_executable_item_count=sum(
            report.planned_executable_item_count for report in reports
        ),
        execution_observed_item_count=sum(report.items_with_any_event_count for report in reports),
        not_started_count=sum(report.not_started_count for report in reports),
        in_progress_count=sum(report.in_progress_count for report in reports),
        paused_count=sum(report.paused_count for report in reports),
        blocked_current_count=sum(report.blocked_count for report in reports),
        completed_count=sum(report.completed_count for report in reports),
        abandoned_count=sum(report.abandoned_count for report in reports),
        terminal_exact_effort_count=sum(report.terminal_exact_effort_count for report in reports),
        terminal_unknown_effort_count=sum(
            report.terminal_unknown_effort_count for report in reports
        ),
        missing_terminal_effort_count=sum(
            report.missing_terminal_effort_count for report in reports
        ),
        terminal_effort_coverage_denominator=sum(
            report.terminal_effort_coverage_denominator for report in reports
        ),
        effort_comparable_count=sum(report.comparable_effort_count for report in reports),
        sum_planned_effort_minutes=sum(
            report.comparable_planned_effort_minutes for report in reports
        ),
        sum_actual_effort_minutes=sum(
            report.comparable_actual_effort_minutes for report in reports
        ),
        sum_delta_minutes=sum(report.aggregate_effort_delta_minutes for report in reports),
        within_window_count=sum(report.within_window_count for report in reports),
        before_window_count=sum(report.before_window_count for report in reports),
        after_window_count=sum(report.after_window_count for report in reports),
        no_window_count=sum(report.no_window_count for report in reports),
        unknown_window_count=sum(report.unknown_window_count for report in reports),
        window_relation_denominator=sum(report.window_relation_denominator for report in reports),
        blocker_reason_counts=_reason_counts(blocker_reasons),
        terminal_reason_counts=_reason_counts(terminal_reasons),
        deviation_reason_counts=_reason_counts(deviation_reasons),
        plan_reports=reports,
        caveats=_calibration_caveats(reports),
    )


build_planning_calibration = build_execution_calibration
project_execution_calibration = build_execution_calibration


__all__ = [
    "EXECUTION_CALIBRATION_MAX_ITEMS",
    "EXECUTION_CALIBRATION_MAX_PLANS",
    "EXECUTION_PROJECTION_MAX_EVENTS",
    "ExecutionCalibrationV1",
    "ExecutionEffortDeltaV1",
    "ExecutionFeedbackProjectionError",
    "ExecutionFeedbackProjectionHistoryError",
    "ExecutionFeedbackProjectionInputError",
    "ExecutionFeedbackReportV1",
    "ExecutionHistoryEventV1",
    "ExecutionItemStateV1",
    "ExecutionLifecycleStateV1",
    "ExecutionPlanFeedbackV1",
    "ExecutionReasonCountV1",
    "ExecutionTerminalFeedbackV1",
    "ExecutionWindowRelation",
    "ExecutionWindowRelationV1",
    "PlanningCalibrationAggregateV1",
    "build_execution_calibration",
    "build_execution_feedback_report",
    "build_execution_projection",
    "build_planning_calibration",
    "project_execution_calibration",
    "project_execution_item",
    "project_execution_plan",
]
