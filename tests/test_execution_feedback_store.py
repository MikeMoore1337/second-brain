"""Adversarial tests for the Stage 18 bounded operational event store."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid7

import pytest

from second_brain.application.execution_feedback import (
    ExecutionEffortPrecisionV1,
    ExecutionEventTypeV1,
    ExecutionEventV1,
    ExecutionFeedbackStaleError,
    ExecutionReasonCodeV1,
    create_execution_event,
    execution_event_fingerprint,
    replay_execution_events,
)
from second_brain.application.execution_feedback_store import (
    EXECUTION_FEEDBACK_STORE_DIRECTORY_NAME,
    ExecutionFeedbackOperationalStore,
    ExecutionFeedbackStoreCorruptError,
    ExecutionFeedbackStoreIdempotencyConflictError,
    ExecutionFeedbackStoreUnavailableError,
    derive_execution_feedback_store_root,
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
from second_brain.application.personal_planning_store import (
    PlanningPlanV1,
    personal_planning_store_hash,
)

NOW = datetime(2026, 9, 16, 5, 30, tzinfo=UTC)


def _plan() -> PlanningPlanV1:
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
        item_id="next-1",
        kind=PlanningItemKindV1.NEXT_ACTION,
        title="Проверить один выбранный шаг",
        description="Сделать ограниченный шаг по выбранной цели.",
        goal_refs=(goal_ref,),
        action_refs=(action_ref,),
        parent_item_id=None,
        target_start_local="2026-09-16T09:00",
        target_end_local="2026-09-16T09:30",
        effort_minutes=30,
        effort_source=PlanningEffortSourceV1.PROVIDER_PROPOSED,
        dependency_ids=(),
    )
    plan_id = UUID("0198f4c5-6a00-7000-8000-000000000001")
    core = {
        "plan_version": "1",
        "plan_id": str(plan_id),
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
        "items": [item.as_dict()],
        "selected_item_ids": [item.item_id],
        "item_order": [item.item_id],
    }
    return PlanningPlanV1(
        plan_version="1",
        plan_id=plan_id,
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
        items=(item,),
        selected_item_ids=(item.item_id,),
        item_order=(item.item_id,),
        plan_fingerprint=personal_planning_store_hash(core),
    )


def _event(
    plan: PlanningPlanV1,
    event_type: ExecutionEventTypeV1,
    operation: str,
    minute: int,
    *,
    effort: int | None = None,
    precision: ExecutionEffortPrecisionV1 = ExecutionEffortPrecisionV1.UNKNOWN,
    reasons: tuple[ExecutionReasonCodeV1, ...] = (),
    event_id: UUID | None = None,
    target_id: UUID | str | None = None,
    target_fingerprint: str | None = None,
    correction_reason: str | None = None,
) -> ExecutionEventV1:
    return create_execution_event(
        plan,
        item_id="next-1",
        event_type=event_type,
        operation_id=operation,
        occurred_at=datetime(2026, 9, 16, 5, minute, tzinfo=UTC),
        received_at=datetime(2026, 9, 16, 6, 0, tzinfo=UTC),
        actual_effort_minutes=effort,
        effort_precision=precision,
        reason_codes=reasons,
        event_id=event_id,
        void_target_event_id=target_id,
        void_target_event_fingerprint=target_fingerprint,
        correction_reason=correction_reason,
    )


def _store(tmp_path: Path) -> tuple[ExecutionFeedbackOperationalStore, Path]:
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    env_file = runtime / "web.env"
    env_file.write_text("SECOND_BRAIN_VAULT=/srv/second-brain-vault\n", encoding="utf-8")
    root = derive_execution_feedback_store_root(env_file)
    assert root == runtime / "prospective-audit" / EXECUTION_FEEDBACK_STORE_DIRECTORY_NAME
    return ExecutionFeedbackOperationalStore(root), root


def test_store_derivation_and_empty_manifest_are_outside_vault_and_repo(tmp_path: Path) -> None:
    store, root = _store(tmp_path)
    assert store.records_path == root / "events.jsonl"
    assert store.read_events() == ()
    assert store.manifest.record_count == 0
    assert store.records_path.read_bytes() == b""
    assert derive_execution_feedback_store_root(None) is None
    assert derive_execution_feedback_store_root(tmp_path / "missing.env") is None
    with pytest.raises(ExecutionFeedbackStoreUnavailableError):
        ExecutionFeedbackOperationalStore(Path.cwd() / "runtime" / "execution-feedback")


def test_append_is_hash_chained_and_replays_exact_item_history(tmp_path: Path) -> None:
    store, _ = _store(tmp_path)
    plan = _plan()
    start = _event(plan, ExecutionEventTypeV1.START, "private-operation", 31)
    complete = _event(
        plan,
        ExecutionEventTypeV1.COMPLETE,
        "complete",
        32,
        effort=35,
        precision=ExecutionEffortPrecisionV1.EXACT,
    )
    first = store.append_event(start, plan=plan, current_plan=plan)
    second = store.append_event(complete, plan=plan)
    assert first.sequence == 1
    assert second.previous_record_digest == first.record_digest
    assert store.manifest.record_count == 2
    assert (
        replay_execution_events(tuple(item.record for item in store.read_events())).state
        == "completed"
    )
    raw = store.records_path.read_text(encoding="utf-8")
    assert "start" in raw and "complete" in raw
    assert "private-operation" not in raw
    assert start.operation_id_fingerprint in raw


def test_start_requires_exact_current_plan_and_source_status(tmp_path: Path) -> None:
    store, _ = _store(tmp_path)
    plan = _plan()
    start = _event(plan, ExecutionEventTypeV1.START, "start", 31)
    with pytest.raises(ExecutionFeedbackStaleError):
        store.append_event(start)
    with pytest.raises(ExecutionFeedbackStaleError):
        store.append_event(start, plan=plan, current_plan=None)
    with pytest.raises(ExecutionFeedbackStaleError):
        store.append_event(start, plan=plan, current_plan=plan, source_status="stale")
    store.append_event(start, plan=plan, current_plan=plan)


def test_operation_retry_is_exactly_idempotent_and_conflicts_on_changed_intent(
    tmp_path: Path,
) -> None:
    store, _ = _store(tmp_path)
    plan = _plan()
    first_event = _event(plan, ExecutionEventTypeV1.START, "same-operation", 31)
    retry_event = _event(plan, ExecutionEventTypeV1.START, "same-operation", 31, event_id=uuid7())
    first = store.append_event(first_event, plan=plan, current_plan=plan)
    retry = store.append_event(retry_event, plan=plan, current_plan=plan)
    assert retry == first
    changed = _event(plan, ExecutionEventTypeV1.START, "same-operation", 32)
    with pytest.raises(ExecutionFeedbackStoreIdempotencyConflictError):
        store.append_event(changed, plan=plan, current_plan=plan)
    assert store.manifest.record_count == 1


def test_concurrent_same_operation_has_one_durable_record(tmp_path: Path) -> None:
    store, _ = _store(tmp_path)
    plan = _plan()
    event = _event(plan, ExecutionEventTypeV1.START, "concurrent", 31)

    def append() -> object:
        return store.append_event(event, plan=plan, current_plan=plan)

    with ThreadPoolExecutor(max_workers=4) as executor:
        results = list(executor.map(lambda _: append(), range(4)))
    assert results[0] == results[1] == results[2] == results[3]
    assert len(store.read_events()) == 1


def test_void_correction_preserves_original_and_updates_effective_replay(tmp_path: Path) -> None:
    store, _ = _store(tmp_path)
    plan = _plan()
    start = _event(plan, ExecutionEventTypeV1.START, "start", 31)
    store.append_event(start, plan=plan, current_plan=plan)
    correction = _event(
        plan,
        ExecutionEventTypeV1.VOID,
        "void-start",
        32,
        event_id=uuid7(),
        target_id=start.event_id,
        target_fingerprint=execution_event_fingerprint(start),
        correction_reason="Владелец исправил ошибочную отметку.",
    )
    store.append_event(correction, plan=plan)
    records = store.read_event_records()
    assert len(records) == 2
    assert records[0] == start
    assert replay_execution_events(tuple(records)).effective_events == ()


def test_tamper_is_detected_without_repair_or_truncation(tmp_path: Path) -> None:
    store, _ = _store(tmp_path)
    plan = _plan()
    store.append_event(
        _event(plan, ExecutionEventTypeV1.START, "start", 31), plan=plan, current_plan=plan
    )
    original = store.records_path.read_bytes()
    store.records_path.write_bytes(original.replace(b"next-1", b"next-2", 1))
    with pytest.raises(ExecutionFeedbackStoreCorruptError):
        store.read_events()
    assert store.records_path.read_bytes() != b""


def test_duplicate_json_keys_and_noncanonical_manifest_are_rejected(tmp_path: Path) -> None:
    store, _ = _store(tmp_path)
    manifest = store.manifest_path.read_text(encoding="utf-8")
    store.manifest_path.write_text(
        manifest.replace('"format_version":1', '"format_version":1,"format_version":1'),
        encoding="utf-8",
    )
    with pytest.raises(ExecutionFeedbackStoreCorruptError):
        store.read_manifest()
