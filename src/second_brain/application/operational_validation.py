"""Read-only evidence projection for Second Brain v4 operational burn-in.

The module deliberately reads only already-initialized Stage 16--20 stores.
It never constructs a store through its normal constructor because those
constructors are allowed to initialize an absent operational store.  The
private verified readers are used on an object created without construction;
this keeps a report observational even when a store is missing.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any, Final, cast

from second_brain.application import action_gateway_store as action_store
from second_brain.application import execution_feedback_store as execution_store
from second_brain.application import executive_strategy_store as strategy_store
from second_brain.application import personal_agent_run_store as agent_store
from second_brain.application import personal_planning_store as planning_store
from second_brain.application.action_gateway import (
    ActionKindV1,
    ActionReceiptKindV1,
    ActionReceiptStateV1,
    ActionReceiptV1,
)
from second_brain.application.execution_feedback import (
    ExecutionEffortPrecisionV1,
    ExecutionEventTypeV1,
    ExecutionEventV1,
)
from second_brain.application.personal_agent_planner import AgentRunStepKindV1
from second_brain.application.personal_agent_run import (
    AgentRunEventKindV1,
    AgentRunStateV1,
    AgentStage19ReceiptRefV1,
)
from second_brain.application.personal_planning import PlanningItemKindV1


class EvidenceStatusV1(StrEnum):
    """Closed status vocabulary for one validation dimension."""

    OBSERVED = "observed"
    NOT_OBSERVED = "not_observed"
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"
    NOT_APPLICABLE = "not_applicable"
    UNSUPPORTED_BY_CURRENT_OPERATIONAL_DATA = "unsupported_by_current_operational_data"


class StoreAvailabilityV1(StrEnum):
    """Safe availability labels that never contain a filesystem path."""

    NOT_CONFIGURED = "not_configured"
    MISSING = "missing"
    AVAILABLE = "available"
    CORRUPT = "corrupt"
    UNAVAILABLE = "unavailable"


VALIDATION_CONTRACT: Final[str] = "second-brain-v4-operational-validation-v1"
NO_NATURAL_STAGE19_ACTION: Final[str] = "no natural Stage19 action demand observed"

_EXECUTABLE_ITEM_KINDS: Final[frozenset[PlanningItemKindV1]] = frozenset(
    {PlanningItemKindV1.COMMITMENT, PlanningItemKindV1.NEXT_ACTION}
)
_TERMINAL_EXECUTION_EVENTS: Final[frozenset[ExecutionEventTypeV1]] = frozenset(
    {ExecutionEventTypeV1.COMPLETE, ExecutionEventTypeV1.ABANDON}
)
_STORE_FAILURES: Final[frozenset[StoreAvailabilityV1]] = frozenset(
    {StoreAvailabilityV1.CORRUPT, StoreAvailabilityV1.UNAVAILABLE}
)


@dataclass(frozen=True, slots=True)
class _StoreRead:
    availability: StoreAvailabilityV1
    snapshot: Any | None


def _read_existing_store(
    env_file: Path | None,
    derive_root: Callable[[Path], Path | None],
    store_type: type[Any],
    corrupt_errors: tuple[type[Exception], ...],
    unavailable_errors: tuple[type[Exception], ...],
) -> _StoreRead:
    """Read one existing store without creating a root, lock, or payload."""

    if env_file is None:
        return _StoreRead(StoreAvailabilityV1.NOT_CONFIGURED, None)
    try:
        root = derive_root(env_file)
    except Exception:
        return _StoreRead(StoreAvailabilityV1.UNAVAILABLE, None)
    if root is None:
        return _StoreRead(StoreAvailabilityV1.UNAVAILABLE, None)
    try:
        if not root.exists():
            return _StoreRead(StoreAvailabilityV1.MISSING, None)
        if root.is_symlink() or not root.is_dir():
            return _StoreRead(StoreAvailabilityV1.CORRUPT, None)

        # Normal store constructors initialize missing roots.  Bypassing the
        # constructor is intentional and is part of the validation contract.
        store: Any = object.__new__(store_type)
        store.root = root
        store._expected_owner_group = None
        store._clock = lambda: datetime.now(UTC)
        store._validate_root(None)
        snapshot = store._read_verified_unlocked()
        return _StoreRead(StoreAvailabilityV1.AVAILABLE, snapshot)
    except corrupt_errors:
        return _StoreRead(StoreAvailabilityV1.CORRUPT, None)
    except unavailable_errors:
        return _StoreRead(StoreAvailabilityV1.UNAVAILABLE, None)
    except OSError, TypeError, UnicodeError, ValueError, RecursionError:
        return _StoreRead(StoreAvailabilityV1.UNAVAILABLE, None)
    except Exception:
        # An unexpected parser failure is still a safe, bounded failure.  Do
        # not leak exception text, paths, or partially parsed records.
        return _StoreRead(StoreAvailabilityV1.CORRUPT, None)


def _read_strategy(env_file: Path | None) -> _StoreRead:
    return _read_existing_store(
        env_file,
        strategy_store.derive_executive_strategy_store_root,
        strategy_store.ExecutiveStrategySnapshotStore,
        (strategy_store.ExecutiveStrategyStoreCorruptError,),
        (strategy_store.ExecutiveStrategyStoreUnavailableError,),
    )


def _read_planning(env_file: Path | None) -> _StoreRead:
    return _read_existing_store(
        env_file,
        planning_store.derive_personal_planning_store_root,
        planning_store.PersonalPlanningOperationalStore,
        (planning_store.PersonalPlanningStoreCorruptError,),
        (planning_store.PersonalPlanningStoreUnavailableError,),
    )


def _read_execution(env_file: Path | None) -> _StoreRead:
    return _read_existing_store(
        env_file,
        execution_store.derive_execution_feedback_store_root,
        execution_store.ExecutionFeedbackOperationalStore,
        (execution_store.ExecutionFeedbackStoreCorruptError,),
        (execution_store.ExecutionFeedbackStoreUnavailableError,),
    )


def _read_action(env_file: Path | None) -> _StoreRead:
    return _read_existing_store(
        env_file,
        action_store.derive_action_gateway_store_root,
        action_store.ActionGatewayOperationalStore,
        (action_store.ActionGatewayStoreCorruptError,),
        (action_store.ActionGatewayStoreUnavailableError,),
    )


def _read_agent(env_file: Path | None) -> _StoreRead:
    return _read_existing_store(
        env_file,
        agent_store.derive_personal_agent_run_store_root,
        agent_store.PersonalAgentRunOperationalStore,
        (agent_store.PersonalAgentRunStoreCorruptError,),
        (agent_store.PersonalAgentRunStoreUnavailableError,),
    )


def _timestamp(value: datetime | None) -> str | None:
    if value is None:
        return None
    rendered = value.astimezone(UTC).isoformat(
        timespec="microseconds" if value.microsecond else "seconds"
    )
    return rendered.removesuffix("+00:00") + "Z"


def _first_last(values: Iterable[datetime]) -> tuple[str | None, str | None]:
    ordered = sorted(values)
    if not ordered:
        return None, None
    return _timestamp(ordered[0]), _timestamp(ordered[-1])


def _enum_text(value: object) -> str:
    if isinstance(value, StrEnum):
        return value.value
    return str(value)


def _closed_counts(enum_values: Sequence[StrEnum], values: Iterable[object]) -> dict[str, int]:
    result = {item.value: 0 for item in enum_values}
    for value in values:
        key = _enum_text(value)
        if key in result:
            result[key] += 1
    return result


def _status_for_count(read: _StoreRead, count: int) -> EvidenceStatusV1:
    if read.availability in _STORE_FAILURES:
        return EvidenceStatusV1.INSUFFICIENT_EVIDENCE
    return EvidenceStatusV1.OBSERVED if count else EvidenceStatusV1.NOT_OBSERVED


def _base(read: _StoreRead, status: EvidenceStatusV1) -> dict[str, object]:
    return {
        "status": status.value,
        "store_availability": read.availability.value,
    }


def _aggregate_strategy(
    read: _StoreRead,
) -> tuple[dict[str, object], tuple[Any, ...]]:
    accepted: tuple[Any, ...] = ()
    current: tuple[Any, ...] = ()
    if read.snapshot is not None:
        verified = cast(strategy_store.ExecutiveStrategyStoreVerifiedSnapshotV1, read.snapshot)
        state = strategy_store._replay_state(verified.envelopes)
        accepted = tuple(state.accepted_snapshots)
        current = tuple(state.current_snapshots)
    first, last = _first_last(cast(datetime, item.accepted_at) for item in accepted)
    goals = {(str(item.goal_source_uuid), item.goal_identity_fingerprint) for item in accepted}
    superseded = sum(item.prior_snapshot_id is not None for item in accepted)
    status = _status_for_count(read, len(accepted))
    result = _base(read, status)
    result.update(
        {
            "accepted_strategy_snapshots": len(accepted),
            "distinct_exact_goal_bindings": len(goals),
            "first_accepted_at": first,
            "last_accepted_at": last,
            "supersession_count": superseded,
            "current_snapshot_count": len(current),
            "superseded_snapshot_count": max(0, len(accepted) - len(current)),
            "generation_review_cycles": {
                "status": EvidenceStatusV1.UNSUPPORTED_BY_CURRENT_OPERATIONAL_DATA.value,
                "reason": "provider_proposals_are_ephemeral",
            },
        }
    )
    return result, accepted


def _aggregate_planning(
    read: _StoreRead,
) -> tuple[dict[str, object], tuple[Any, ...]]:
    history: tuple[Any, ...] = ()
    current: Any | None = None
    replacement_count = 0
    edit_count = 0
    if read.snapshot is not None:
        verified = cast(planning_store.PersonalPlanningStoreVerifiedSnapshotV1, read.snapshot)
        state = planning_store._replay_state(verified.envelopes)
        history = tuple(state.plan_history)
        current = state.current_plan
        replacement_count = sum(
            item.record.event_type is planning_store.PersonalPlanningStoreRecordTypeV1.PLAN_REPLACED
            for item in verified.envelopes
        )
        edit_count = sum(
            item.record.event_type is planning_store.PersonalPlanningStoreRecordTypeV1.PLAN_EDITED
            for item in verified.envelopes
        )
    goal_refs = {
        (str(ref.goal_source_uuid), ref.goal_identity_fingerprint)
        for plan in history
        for item in plan.items
        for ref in item.goal_refs
    }
    executable_items = {
        (item.item_id, planning_store.personal_planning_store_hash(item.as_dict()))
        for plan in history
        for item in plan.items
        if item.item_id in plan.selected_item_ids and item.kind in _EXECUTABLE_ITEM_KINDS
    }
    first, last = _first_last(cast(datetime, item.as_of) for item in history)
    status = _status_for_count(read, len(history))
    result = _base(read, status)
    result.update(
        {
            "accepted_planning_snapshots": len(history),
            "distinct_exact_goal_refs": len(goal_refs),
            "accepted_executable_items": len(executable_items),
            "current_snapshot_count": 1 if current is not None else 0,
            "superseded_snapshot_count": max(0, len(history) - (1 if current else 0)),
            "first_accepted_at": first,
            "last_accepted_at": last,
            "replacement_count": replacement_count,
            "edit_revision_count": edit_count,
        }
    )
    return result, history


def _aggregate_execution(
    read: _StoreRead,
) -> tuple[dict[str, object], tuple[Any, ...]]:
    events: tuple[Any, ...] = ()
    if read.snapshot is not None:
        verified = cast(execution_store.ExecutionFeedbackStoreVerifiedSnapshotV1, read.snapshot)
        events = tuple(item.record for item in verified.envelopes)
    typed_events = cast(tuple[ExecutionEventV1, ...], events)
    event_types = tuple(item.event_type for item in typed_events)
    item_keys = {
        (
            str(item.planning_snapshot_id),
            item.planning_snapshot_fingerprint,
            item.item_id,
            item.accepted_item_fingerprint,
        )
        for item in typed_events
    }
    terminal = [item for item in typed_events if item.event_type in _TERMINAL_EXECUTION_EVENTS]
    known_effort = sum(
        item.effort_precision is ExecutionEffortPrecisionV1.EXACT for item in terminal
    )
    unknown_effort = sum(
        item.effort_precision is ExecutionEffortPrecisionV1.UNKNOWN for item in terminal
    )
    first, last = _first_last(cast(datetime, item.occurred_at) for item in typed_events)
    status = _status_for_count(read, len(typed_events))
    result = _base(read, status)
    result.update(
        {
            "event_count": len(typed_events),
            "distinct_execution_items": len(item_keys),
            "events_by_lifecycle_type": _closed_counts(tuple(ExecutionEventTypeV1), event_types),
            "known_actual_effort_count": known_effort,
            "unknown_actual_effort_count": unknown_effort,
            "terminal_event_count": len(terminal),
            "non_terminal_event_count": len(typed_events) - len(terminal),
            "first_event_at": first,
            "last_event_at": last,
        }
    )
    return result, events


def _receipt_time(receipt: ActionReceiptV1) -> datetime | None:
    return receipt.attempt_started_at or receipt.sent_at or receipt.finished_at


def _aggregate_action(
    read: _StoreRead,
) -> tuple[dict[str, object], tuple[Any, ...]]:
    receipts: tuple[Any, ...] = ()
    if read.snapshot is not None:
        verified = cast(action_store.ActionGatewayStoreSnapshotV1, read.snapshot)
        receipts = tuple(item.record for item in verified.envelopes)
    typed_receipts = cast(tuple[ActionReceiptV1, ...], receipts)
    times = [value for receipt in typed_receipts if (value := _receipt_time(receipt)) is not None]
    first, last = _first_last(times)
    receipt_kinds = tuple(item.receipt_kind for item in typed_receipts)
    action_kinds = tuple(item.action_kind for item in typed_receipts)
    states = tuple(item.state for item in typed_receipts)
    status = _status_for_count(read, len(typed_receipts))
    result = _base(read, status)
    result.update(
        {
            "receipt_count": len(typed_receipts),
            "receipt_kind_counts": _closed_counts(tuple(ActionReceiptKindV1), receipt_kinds),
            "action_kind_counts": _closed_counts(tuple(ActionKindV1), action_kinds),
            "receipt_state_counts": _closed_counts(tuple(ActionReceiptStateV1), states),
            "reconciliation_count": sum(
                item is ActionReceiptKindV1.RECONCILIATION for item in receipt_kinds
            ),
            "compensation_count": sum(
                item is ActionReceiptKindV1.COMPENSATION for item in receipt_kinds
            ),
            "first_receipt_at": first,
            "last_receipt_at": last,
        }
    )
    return result, receipts


def _aggregate_agent(
    read: _StoreRead,
) -> tuple[dict[str, object], tuple[Any, ...], dict[Any, tuple[Any, ...]]]:
    histories: dict[Any, tuple[Any, ...]] = {}
    if read.snapshot is not None:
        verified = cast(agent_store.PersonalAgentRunStoreSnapshotV1, read.snapshot)
        histories = agent_store._replay(verified.envelopes)
    all_events: tuple[Any, ...] = ()
    if read.snapshot is not None:
        verified = cast(agent_store.PersonalAgentRunStoreSnapshotV1, read.snapshot)
        all_events = tuple(item.record for item in verified.envelopes)
    event_kinds = tuple(item.event_type for item in all_events)
    latest = tuple(history[-1] for history in histories.values() if history)
    accepted = tuple(history[0] for history in histories.values() if history)
    step_counts = _closed_counts(
        tuple(AgentRunStepKindV1),
        (step.step.kind for snapshot in accepted for step in snapshot.steps),
    )
    state_counts = _closed_counts(tuple(AgentRunStateV1), (item.state for item in latest))
    accepted_times = [cast(datetime, item.accepted_at) for item in accepted]
    updated_times = [cast(datetime, item.updated_at) for item in latest]
    first, _ = _first_last(accepted_times)
    _, last = _first_last(updated_times)
    status = _status_for_count(read, len(accepted))
    result = _base(read, status)
    result.update(
        {
            "run_count": len(histories),
            "mission_count": len({item.mission.fingerprint for item in accepted}),
            "accepted_run_count": sum(item is AgentRunEventKindV1.ACCEPT for item in event_kinds),
            "started_run_count": sum(item is AgentRunEventKindV1.START for item in event_kinds),
            "completed_run_count": sum(
                item is AgentRunEventKindV1.COMPLETE for item in event_kinds
            ),
            "abandoned_run_count": sum(item is AgentRunEventKindV1.ABANDON for item in event_kinds),
            "paused_count": sum(item is AgentRunEventKindV1.PAUSE for item in event_kinds),
            "resumed_count": sum(item is AgentRunEventKindV1.RESUME for item in event_kinds),
            "revision_count": sum(max(0, len(history) - 1) for history in histories.values()),
            "supersession_count": sum(
                item is AgentRunEventKindV1.SUPERSEDE for item in event_kinds
            ),
            "event_counts": _closed_counts(tuple(AgentRunEventKindV1), event_kinds),
            "run_state_counts": state_counts,
            "step_counts_by_kind": step_counts,
            "clarify_count": step_counts[AgentRunStepKindV1.CLARIFY.value],
            "checkpoint_count": step_counts[AgentRunStepKindV1.CHECKPOINT.value],
            "stage19_action_count": step_counts[AgentRunStepKindV1.STAGE19_ACTION.value],
            "hold_count": step_counts[AgentRunStepKindV1.HOLD.value],
            "skip_count": sum(item is AgentRunEventKindV1.SKIP for item in event_kinds),
            "first_run_at": first,
            "last_run_at": last,
        }
    )
    return result, accepted, histories


def _store_failed(read: _StoreRead) -> bool:
    return read.availability in _STORE_FAILURES


def _stage16_to_stage17(
    strategy_read: _StoreRead,
    planning_read: _StoreRead,
    strategies: tuple[Any, ...],
    plans: tuple[Any, ...],
) -> dict[str, object]:
    refs = [ref for plan in plans for item in plan.items for ref in item.action_refs]
    if not plans:
        status = (
            EvidenceStatusV1.INSUFFICIENT_EVIDENCE
            if _store_failed(strategy_read) or _store_failed(planning_read)
            else EvidenceStatusV1.NOT_OBSERVED
        )
        return {"status": status.value, "exact_binding_count": 0, "unresolved_binding_count": 0}
    strategy_by_key = {
        (str(snapshot.snapshot_id), snapshot.snapshot_fingerprint): snapshot
        for snapshot in strategies
    }
    matched = 0
    for ref in refs:
        snapshot = strategy_by_key.get(
            (str(ref.strategy_snapshot_id), ref.strategy_snapshot_fingerprint)
        )
        if snapshot is None:
            continue
        if any(
            action.action_id == ref.reviewed_action_id and action == ref.reviewed_action
            for action in snapshot.selected_actions
        ):
            matched += 1
    if not refs:
        status = EvidenceStatusV1.INSUFFICIENT_EVIDENCE
    elif matched:
        status = EvidenceStatusV1.OBSERVED
    else:
        status = EvidenceStatusV1.INSUFFICIENT_EVIDENCE
    return {
        "status": status.value,
        "exact_binding_count": matched,
        "unresolved_binding_count": len(refs) - matched,
    }


def _plan_item(plan: Any, item_id: str, item_fingerprint: str) -> Any | None:
    for item in plan.items:
        if (
            item.item_id == item_id
            and planning_store.personal_planning_store_hash(item.as_dict()) == item_fingerprint
        ):
            return item
    return None


def _stage17_to_stage18(
    planning_read: _StoreRead,
    execution_read: _StoreRead,
    plans: tuple[Any, ...],
    events: tuple[Any, ...],
) -> dict[str, object]:
    if not events:
        status = (
            EvidenceStatusV1.INSUFFICIENT_EVIDENCE
            if _store_failed(planning_read) or _store_failed(execution_read)
            else EvidenceStatusV1.NOT_OBSERVED
        )
        return {"status": status.value, "matched_event_count": 0, "unresolved_event_count": 0}
    plans_by_key = {(str(plan.plan_id), plan.plan_fingerprint): plan for plan in plans}
    matched = 0
    for event in cast(tuple[ExecutionEventV1, ...], events):
        plan = plans_by_key.get(
            (str(event.planning_snapshot_id), event.planning_snapshot_fingerprint)
        )
        if plan is None or plan.revision != event.planning_plan_revision:
            continue
        if (
            plan.source_pack_fingerprint != event.planning_source_pack_fingerprint
            or plan.proposal_fingerprint != event.planning_proposal_fingerprint
        ):
            continue
        item = _plan_item(plan, event.item_id, event.accepted_item_fingerprint)
        if item is not None and item == event.accepted_item:
            matched += 1
    status = EvidenceStatusV1.OBSERVED if matched else EvidenceStatusV1.INSUFFICIENT_EVIDENCE
    return {
        "status": status.value,
        "matched_event_count": matched,
        "unresolved_event_count": len(events) - matched,
    }


def _stage17_stage18_to_stage20(
    planning_read: _StoreRead,
    execution_read: _StoreRead,
    agent_read: _StoreRead,
    plans: tuple[Any, ...],
    events: tuple[Any, ...],
    accepted_runs: tuple[Any, ...],
) -> dict[str, object]:
    if not accepted_runs:
        status = (
            EvidenceStatusV1.INSUFFICIENT_EVIDENCE
            if _store_failed(planning_read)
            or _store_failed(execution_read)
            or _store_failed(agent_read)
            else EvidenceStatusV1.NOT_OBSERVED
        )
        return {"status": status.value, "matched_run_count": 0, "unresolved_run_count": 0}
    plans_by_key = {(str(plan.plan_id), plan.plan_fingerprint): plan for plan in plans}
    execution_keys = {
        (
            str(event.planning_snapshot_id),
            event.planning_snapshot_fingerprint,
            event.item_id,
            event.accepted_item_fingerprint,
        )
        for event in cast(tuple[ExecutionEventV1, ...], events)
    }
    matched = 0
    for run in accepted_runs:
        plan = plans_by_key.get((str(run.planning_snapshot_id), run.planning_snapshot_fingerprint))
        if plan is None:
            continue
        item_matches = all(
            _plan_item(plan, binding.item_id, binding.accepted_item_fingerprint) is not None
            for binding in run.stage18_bindings
        )
        event_matches = all(
            (
                str(run.planning_snapshot_id),
                run.planning_snapshot_fingerprint,
                binding.item_id,
                binding.accepted_item_fingerprint,
            )
            in execution_keys
            for binding in run.stage18_bindings
        )
        if item_matches and event_matches:
            matched += 1
    status = EvidenceStatusV1.OBSERVED if matched else EvidenceStatusV1.INSUFFICIENT_EVIDENCE
    return {
        "status": status.value,
        "matched_run_count": matched,
        "unresolved_run_count": len(accepted_runs) - matched,
    }


def _receipt_ref_matches(receipt: ActionReceiptV1, reference: AgentStage19ReceiptRefV1) -> bool:
    return (
        receipt.receipt_id == reference.receipt_id
        and _enum_text(receipt.receipt_kind) == _enum_text(reference.receipt_kind)
        and receipt.operation_id_fingerprint == reference.operation_id_fingerprint
        and receipt.intent_fingerprint == reference.intent_fingerprint
        and _enum_text(receipt.action_kind) == _enum_text(reference.action_kind)
        and receipt.payload_fingerprint == reference.payload_fingerprint
        and _enum_text(receipt.state) == _enum_text(reference.state)
        and receipt.safe_error_code == reference.safe_error_code
        and receipt.parent_receipt_id == reference.parent_receipt_id
    )


def _stage20_to_stage19(
    agent_read: _StoreRead,
    action_read: _StoreRead,
    histories: dict[Any, tuple[Any, ...]],
    receipts: tuple[Any, ...],
) -> dict[str, object]:
    references: dict[str, AgentStage19ReceiptRefV1] = {}
    action_steps_without_receipt = 0
    for history in histories.values():
        for snapshot in history:
            for reference in snapshot.receipt_refs:
                references[str(reference.receipt_id)] = reference
            for step in snapshot.steps:
                if step.step.kind is AgentRunStepKindV1.STAGE19_ACTION and step.receipt_ref is None:
                    action_steps_without_receipt += 1
    if not references:
        if action_steps_without_receipt or _store_failed(agent_read) or _store_failed(action_read):
            status = EvidenceStatusV1.INSUFFICIENT_EVIDENCE
        else:
            status = EvidenceStatusV1.NOT_OBSERVED
        return {
            "status": status.value,
            "matched_receipt_count": 0,
            "unresolved_receipt_count": 0,
            "stage19_action_steps_without_receipt": action_steps_without_receipt,
        }
    typed_receipts = cast(tuple[ActionReceiptV1, ...], receipts)
    matched = sum(
        any(_receipt_ref_matches(receipt, reference) for receipt in typed_receipts)
        for reference in references.values()
    )
    status = EvidenceStatusV1.OBSERVED if matched else EvidenceStatusV1.INSUFFICIENT_EVIDENCE
    return {
        "status": status.value,
        "matched_receipt_count": matched,
        "unresolved_receipt_count": len(references) - matched,
        "stage19_action_steps_without_receipt": action_steps_without_receipt,
    }


def _coverage_targets(
    strategy: dict[str, object],
    planning: dict[str, object],
    execution: dict[str, object],
    action: dict[str, object],
    agent: dict[str, object],
) -> dict[str, object]:
    return {
        "stage16_strategy_generation_review_cycles": {
            "target": 5,
            "observed": None,
            "status": EvidenceStatusV1.UNSUPPORTED_BY_CURRENT_OPERATIONAL_DATA.value,
        },
        "stage17_accepted_planning_snapshots": {
            "target": 3,
            "observed": planning["accepted_planning_snapshots"],
            "status": planning["status"],
            "real_work_use_requires_owner_evidence": True,
        },
        "stage18_lifecycle_feedback_events": {
            "target": 10,
            "observed": execution["event_count"],
            "status": execution["status"],
        },
        "stage18_distinct_executable_items": {
            "target": 3,
            "observed": execution["distinct_execution_items"],
            "status": execution["status"],
        },
        "stage20_missions_or_runs": {
            "target": 3,
            "observed": agent["run_count"],
            "status": agent["status"],
        },
        "stage19_natural_action": {
            "target": "natural_demand_only",
            "observed": action["receipt_count"],
            "status": action["status"],
            "note": (None if cast(int, action["receipt_count"]) > 0 else NO_NATURAL_STAGE19_ACTION),
        },
        "clarify_or_checkpoint": {
            "target": "natural_demand_only",
            "observed": cast(int, agent["clarify_count"]) + cast(int, agent["checkpoint_count"]),
            "status": agent["status"],
        },
    }


def build_v4_validation_report(
    env_file: Path | None,
    *,
    generated_at: datetime | None = None,
) -> dict[str, object]:
    """Build the bounded, privacy-safe v4 validation projection."""

    strategy_read = _read_strategy(env_file)
    planning_read = _read_planning(env_file)
    execution_read = _read_execution(env_file)
    action_read = _read_action(env_file)
    agent_read = _read_agent(env_file)

    strategy, strategies = _aggregate_strategy(strategy_read)
    planning, plans = _aggregate_planning(planning_read)
    execution, events = _aggregate_execution(execution_read)
    action, receipts = _aggregate_action(action_read)
    agent, accepted_runs, histories = _aggregate_agent(agent_read)

    cross_stage = {
        "stage16_to_stage17": _stage16_to_stage17(strategy_read, planning_read, strategies, plans),
        "stage17_to_stage18": _stage17_to_stage18(planning_read, execution_read, plans, events),
        "stage17_stage18_to_stage20": _stage17_stage18_to_stage20(
            planning_read, execution_read, agent_read, plans, events, accepted_runs
        ),
        "stage20_to_stage19": _stage20_to_stage19(agent_read, action_read, histories, receipts),
    }
    gaps = [
        "stage16_generation_review_cycles_not_durable",
        "real_work_use_requires_owner_issue_386_evidence",
        "qualitative_friction_requires_owner_issue_386_evidence",
        "burn_in_start_requires_successful_production_deployment",
        "fourteen_calendar_day_window_requires_factual_deployment_time",
    ]
    if cast(int, action["receipt_count"]) == 0:
        gaps.append(NO_NATURAL_STAGE19_ACTION)
    gaps.extend(
        f"{name}_exact_provenance_incomplete"
        for name, relation in cross_stage.items()
        if relation["status"] == EvidenceStatusV1.INSUFFICIENT_EVIDENCE.value
    )
    timestamp = generated_at or datetime.now(UTC)
    return {
        "v4_validation_contract": VALIDATION_CONTRACT,
        "generated_at": _timestamp(timestamp),
        "stage16": strategy,
        "stage17": planning,
        "stage18": execution,
        "stage19": action,
        "stage20": agent,
        "cross_stage": cross_stage,
        "coverage_targets": _coverage_targets(strategy, planning, execution, action, agent),
        "unresolved_evidence_gaps": list(dict.fromkeys(gaps)),
        "manual_evidence_required": [
            "confirm real work use in Issue #386",
            "record qualitative friction in Issue #386",
            "record useful versus unnecessary confirmations in Issue #386",
            "record missing, stale, or wrong context examples in Issue #386",
            "record natural Stage19 demand only when it occurs",
            "owner review after the real-use window",
        ],
        "burn_in_decision_status": "observation_in_progress",
    }


def render_v4_validation_text(report: dict[str, object]) -> str:
    """Render only bounded fields from a validation report."""

    def stage_line(label: str, stage: dict[str, object]) -> str:
        return f"{label}: статус={stage['status']}; хранилище={stage['store_availability']}"

    stage16 = cast(dict[str, object], report["stage16"])
    stage17 = cast(dict[str, object], report["stage17"])
    stage18 = cast(dict[str, object], report["stage18"])
    stage19 = cast(dict[str, object], report["stage19"])
    stage20 = cast(dict[str, object], report["stage20"])
    generation_cycles = cast(dict[str, object], stage16["generation_review_cycles"])
    lines = [
        "Second Brain v4 — операционная проверка и burn-in",
        f"Сгенерировано: {report['generated_at']}",
        f"Состояние наблюдения: {report['burn_in_decision_status']}",
        stage_line("Stage16 Strategy", stage16),
        f"  Принятые снимки: {stage16['accepted_strategy_snapshots']}; "
        f"циклы генерации/проверки: {generation_cycles['status']}",
        stage_line("Stage17 Planning", stage17),
        f"  Принятые снимки планирования: {stage17['accepted_planning_snapshots']}; "
        f"исполняемые элементы: {stage17['accepted_executable_items']}",
        stage_line("Stage18 Execution", stage18),
        f"  События: {stage18['event_count']}; "
        f"исполняемые элементы: {stage18['distinct_execution_items']}",
        stage_line("Stage19 Action Gateway", stage19),
        f"  Receipts: {stage19['receipt_count']}; "
        f"reconciliation: {stage19['reconciliation_count']}; "
        f"compensation: {stage19['compensation_count']}",
        stage_line("Stage20 Agent", stage20),
        f"  Runs: {stage20['run_count']}; завершены: {stage20['completed_run_count']}; "
        f"прерваны: {stage20['abandoned_run_count']}",
        "Stage19 natural action: "
        + ("observed" if cast(int, stage19["receipt_count"]) > 0 else NO_NATURAL_STAGE19_ACTION),
        "Автоматическое решение о Stage21/v5: отсутствует",
    ]
    return "\n".join(lines)


__all__ = [
    "NO_NATURAL_STAGE19_ACTION",
    "VALIDATION_CONTRACT",
    "EvidenceStatusV1",
    "StoreAvailabilityV1",
    "build_v4_validation_report",
    "render_v4_validation_text",
]
