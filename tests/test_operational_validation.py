"""Focused privacy and read-only tests for the v4 validation projection."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

from typer.testing import CliRunner

from second_brain.application.action_gateway_store import (
    ActionGatewayOperationalStore,
    derive_action_gateway_store_root,
)
from second_brain.application.execution_feedback_store import (
    ExecutionFeedbackOperationalStore,
    derive_execution_feedback_store_root,
)
from second_brain.application.executive_strategy_store import (
    ExecutiveStrategySnapshotStore,
    derive_executive_strategy_store_root,
)
from second_brain.application.operational_validation import (
    NO_NATURAL_STAGE19_ACTION,
    EvidenceStatusV1,
    StoreAvailabilityV1,
    _stage17_to_stage18,
    _StoreRead,
    build_v4_validation_report,
    render_v4_validation_text,
)
from second_brain.application.personal_agent_run_store import (
    PersonalAgentRunOperationalStore,
    derive_personal_agent_run_store_root,
)
from second_brain.application.personal_planning_store import (
    PersonalPlanningOperationalStore,
    derive_personal_planning_store_root,
)
from second_brain.entrypoints.cli.app import app

FIXED_GENERATED_AT = datetime(2026, 9, 17, 12, 0, tzinfo=UTC)


def _env_file(tmp_path: Path) -> tuple[Path, Path]:
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    env_file = runtime / "web.env"
    env_file.write_text("SECOND_BRAIN_VAULT_PATH=/srv/second-brain-vault\n", encoding="utf-8")
    return runtime, env_file


def _stage(report: dict[str, object], name: str) -> dict[str, Any]:
    return cast(dict[str, Any], report[name])


def _file_state(root: Path) -> dict[str, tuple[bytes, int]]:
    return {
        path.relative_to(root).as_posix(): (path.read_bytes(), path.stat().st_mtime_ns)
        for path in root.rglob("*")
        if path.is_file()
    }


def _initialize_empty_stores(env_file: Path) -> None:
    stores = (
        (derive_executive_strategy_store_root, ExecutiveStrategySnapshotStore),
        (derive_personal_planning_store_root, PersonalPlanningOperationalStore),
        (derive_execution_feedback_store_root, ExecutionFeedbackOperationalStore),
        (derive_action_gateway_store_root, ActionGatewayOperationalStore),
        (derive_personal_agent_run_store_root, PersonalAgentRunOperationalStore),
    )
    for derive_root, store_type in stores:
        root = derive_root(env_file)
        assert root is not None
        store_type(root)


def test_missing_stores_are_not_initialized_and_report_is_deterministic(tmp_path: Path) -> None:
    runtime, env_file = _env_file(tmp_path)

    first = build_v4_validation_report(env_file, generated_at=FIXED_GENERATED_AT)
    second = build_v4_validation_report(env_file, generated_at=FIXED_GENERATED_AT)

    assert first == second
    assert not (runtime / "prospective-audit").exists()
    assert all(
        _stage(first, stage)["status"] == EvidenceStatusV1.NOT_OBSERVED.value
        for stage in ("stage16", "stage17", "stage18", "stage19", "stage20")
    )
    assert _stage(first, "stage16")["generation_review_cycles"] == {
        "status": EvidenceStatusV1.UNSUPPORTED_BY_CURRENT_OPERATIONAL_DATA.value,
        "reason": "provider_proposals_are_ephemeral",
    }
    assert (
        cast(dict[str, Any], first["coverage_targets"])["stage19_natural_action"]["note"]
        == NO_NATURAL_STAGE19_ACTION
    )


def test_existing_empty_stores_are_read_without_mutation(tmp_path: Path) -> None:
    runtime, env_file = _env_file(tmp_path)
    _initialize_empty_stores(env_file)
    before = _file_state(runtime)

    report = build_v4_validation_report(env_file, generated_at=FIXED_GENERATED_AT)
    after = _file_state(runtime)

    assert before == after
    assert all(
        _stage(report, stage)["store_availability"] == "available"
        for stage in ("stage16", "stage17", "stage18", "stage19", "stage20")
    )
    assert all(
        _stage(report, stage)["status"] == EvidenceStatusV1.NOT_OBSERVED.value
        for stage in ("stage16", "stage17", "stage18", "stage19", "stage20")
    )


def test_corrupt_existing_store_is_insufficient_without_leaking_input(tmp_path: Path) -> None:
    runtime, env_file = _env_file(tmp_path)
    strategy_root = runtime / "prospective-audit" / "executive-strategy"
    strategy_root.mkdir(parents=True)
    secret = "provider-response-secret-and-absolute-path"
    (strategy_root / "snapshots.jsonl").write_text(secret + "\n", encoding="utf-8")

    report = build_v4_validation_report(env_file, generated_at=FIXED_GENERATED_AT)
    rendered = json.dumps(report, ensure_ascii=False, sort_keys=True)

    assert _stage(report, "stage16")["store_availability"] == "corrupt"
    assert _stage(report, "stage16")["status"] == EvidenceStatusV1.INSUFFICIENT_EVIDENCE.value
    assert not (runtime / "prospective-audit" / "personal-planning").exists()
    assert secret not in rendered
    assert str(strategy_root) not in rendered


def test_status_values_are_closed_and_output_is_bounded(tmp_path: Path) -> None:
    _runtime, env_file = _env_file(tmp_path)
    report = build_v4_validation_report(env_file, generated_at=FIXED_GENERATED_AT)
    encoded = json.dumps(report, ensure_ascii=False, sort_keys=True)
    allowed = {item.value for item in EvidenceStatusV1}
    statuses: list[str] = []

    def collect(value: object) -> None:
        if isinstance(value, dict):
            status = value.get("status")
            if isinstance(status, str):
                statuses.append(status)
            for child in value.values():
                collect(child)
        elif isinstance(value, list):
            for child in value:
                collect(child)

    collect(report)

    assert statuses
    assert set(statuses) <= allowed
    assert len(encoded.encode("utf-8")) < 20_000
    assert NO_NATURAL_STAGE19_ACTION in encoded


def test_cli_exposes_machine_readable_status_without_paths_or_traceback() -> None:
    result = CliRunner().invoke(app, ["validation", "v4-status", "--json"])

    assert result.exit_code == 0
    assert result.stderr == ""
    payload = json.loads(result.stdout)
    assert payload["v4_validation_contract"] == "second-brain-v4-operational-validation-v1"
    assert payload["burn_in_decision_status"] == "observation_in_progress"
    assert NO_NATURAL_STAGE19_ACTION in result.stdout
    assert "Traceback" not in result.stdout


def test_text_projection_keeps_stage21_decision_absent(tmp_path: Path) -> None:
    _runtime, env_file = _env_file(tmp_path)
    report = build_v4_validation_report(env_file, generated_at=FIXED_GENERATED_AT)
    rendered = render_v4_validation_text(report)

    assert NO_NATURAL_STAGE19_ACTION in rendered
    assert "Автоматическое решение о Stage21/v5: отсутствует" in rendered
    assert "GO_V5" not in rendered


def test_stage16_report_uses_only_safe_accepted_snapshot_aggregates(tmp_path: Path) -> None:
    from second_brain.application.executive_strategy import build_reviewed_action
    from tests.test_executive_strategy_store import _proposal_and_pack

    _runtime, env_file = _env_file(tmp_path)
    root = derive_executive_strategy_store_root(env_file)
    assert root is not None
    pack, proposal = _proposal_and_pack()
    ExecutiveStrategySnapshotStore(root).accept(
        proposal,
        (build_reviewed_action(proposal.candidates[0]),),
        context_pack=pack,
        operation_id="validation/stage16",
        reviewed_at=FIXED_GENERATED_AT,
    )

    stage16 = _stage(
        build_v4_validation_report(env_file, generated_at=FIXED_GENERATED_AT), "stage16"
    )

    assert stage16["accepted_strategy_snapshots"] == 1
    assert stage16["distinct_exact_goal_bindings"] == 1
    assert stage16["current_snapshot_count"] == 1
    assert stage16["superseded_snapshot_count"] == 0
    assert stage16["generation_review_cycles"]["status"] == (
        EvidenceStatusV1.UNSUPPORTED_BY_CURRENT_OPERATIONAL_DATA.value
    )
    assert "Улучшить выносливость" not in json.dumps(stage16, ensure_ascii=False)


def test_stage17_report_counts_accepted_executable_items_and_exact_goal_refs(
    tmp_path: Path,
) -> None:
    from tests.test_personal_planning_store import _proposal_and_pack

    _runtime, env_file = _env_file(tmp_path)
    root = derive_personal_planning_store_root(env_file)
    assert root is not None
    pack, proposal = _proposal_and_pack()
    PersonalPlanningOperationalStore(root).accept(
        proposal,
        context_pack=pack,
        operation_id="validation/stage17",
        accepted_at=FIXED_GENERATED_AT,
    )

    stage17 = _stage(
        build_v4_validation_report(env_file, generated_at=FIXED_GENERATED_AT), "stage17"
    )

    assert stage17["accepted_planning_snapshots"] == 1
    assert stage17["distinct_exact_goal_refs"] == 1
    assert stage17["accepted_executable_items"] == 1
    assert stage17["current_snapshot_count"] == 1
    assert stage17["superseded_snapshot_count"] == 0
    assert "Сделать шаг" not in json.dumps(stage17, ensure_ascii=False)


def test_stage18_report_counts_lifecycle_and_effort_without_event_payloads(tmp_path: Path) -> None:
    from second_brain.application.execution_feedback import (
        ExecutionEffortPrecisionV1,
        ExecutionEventTypeV1,
    )
    from tests.test_execution_feedback_store import _event, _plan

    _runtime, env_file = _env_file(tmp_path)
    root = derive_execution_feedback_store_root(env_file)
    assert root is not None
    store = ExecutionFeedbackOperationalStore(root)
    plan = _plan()
    store.append_event(
        _event(plan, ExecutionEventTypeV1.START, "validation/start", 31),
        plan=plan,
        current_plan=plan,
    )
    store.append_event(
        _event(
            plan,
            ExecutionEventTypeV1.COMPLETE,
            "validation/complete",
            32,
            effort=35,
            precision=ExecutionEffortPrecisionV1.EXACT,
        ),
        plan=plan,
    )

    stage18 = _stage(
        build_v4_validation_report(env_file, generated_at=FIXED_GENERATED_AT), "stage18"
    )
    lifecycle = cast(dict[str, int], stage18["events_by_lifecycle_type"])

    assert stage18["event_count"] == 2
    assert stage18["distinct_execution_items"] == 1
    assert lifecycle[ExecutionEventTypeV1.START.value] == 1
    assert lifecycle[ExecutionEventTypeV1.COMPLETE.value] == 1
    assert stage18["known_actual_effort_count"] == 1
    assert stage18["unknown_actual_effort_count"] == 0
    assert stage18["terminal_event_count"] == 1
    assert stage18["non_terminal_event_count"] == 1
    assert "validation/start" not in json.dumps(stage18, ensure_ascii=False)


def test_stage19_report_counts_verified_receipts_without_payload_or_secret(tmp_path: Path) -> None:
    from tests.test_action_gateway_store import _prepared

    _runtime, env_file = _env_file(tmp_path)
    root = derive_action_gateway_store_root(env_file)
    assert root is not None
    prepared = _prepared("validation/stage19")
    ActionGatewayOperationalStore(root).begin_execution(prepared, now=FIXED_GENERATED_AT)

    stage19 = _stage(
        build_v4_validation_report(env_file, generated_at=FIXED_GENERATED_AT), "stage19"
    )
    encoded = json.dumps(stage19, ensure_ascii=False)

    assert stage19["receipt_count"] == 1
    assert stage19["reconciliation_count"] == 0
    assert stage19["compensation_count"] == 0
    assert "Задача" not in encoded
    assert "credential_profile_id" not in encoded
    assert "MikeMoore1337/second-brain" not in encoded


def test_stage20_report_counts_runs_and_closed_step_kinds_without_run_text(tmp_path: Path) -> None:
    from second_brain.application.personal_agent_run import AgentRunService
    from tests.test_personal_agent_run import _pack, _proposal

    _runtime, env_file = _env_file(tmp_path)
    root = derive_personal_agent_run_store_root(env_file)
    assert root is not None
    pack = _pack()
    proposal = _proposal(pack)
    store = PersonalAgentRunOperationalStore(root)
    service = AgentRunService(store, clock=lambda: FIXED_GENERATED_AT)
    service.accept(
        pack,
        proposal,
        operation_id="validation/stage20/accept",
        run_id="0199f6c0-0000-7000-8000-000000000099",
    )
    service.start(
        pack,
        "0199f6c0-0000-7000-8000-000000000099",
        operation_id="validation/stage20/start",
    )

    stage20 = _stage(
        build_v4_validation_report(env_file, generated_at=FIXED_GENERATED_AT), "stage20"
    )
    step_counts = cast(dict[str, int], stage20["step_counts_by_kind"])

    assert stage20["run_count"] == 1
    assert stage20["accepted_run_count"] == 1
    assert stage20["started_run_count"] == 1
    assert stage20["completed_run_count"] == 0
    assert stage20["clarify_count"] == 1
    assert stage20["checkpoint_count"] == 2
    assert stage20["hold_count"] == 1
    assert step_counts["clarify"] == 1
    assert "Какой результат считаем достаточным?" not in json.dumps(stage20, ensure_ascii=False)


def test_stage17_to_stage18_relation_requires_exact_item_fingerprint() -> None:
    from second_brain.application.execution_feedback import ExecutionEventTypeV1
    from tests.test_execution_feedback_store import _event, _plan

    plan = _plan()
    event = _event(plan, ExecutionEventTypeV1.START, "validation/exact", 31)
    available = _StoreRead(StoreAvailabilityV1.AVAILABLE, object())

    observed = _stage17_to_stage18(available, available, (plan,), (event,))
    assert observed["status"] == EvidenceStatusV1.OBSERVED.value
    assert observed["matched_event_count"] == 1

    fuzzy_only_event = SimpleNamespace(
        planning_snapshot_id=event.planning_snapshot_id,
        planning_snapshot_fingerprint=event.planning_snapshot_fingerprint,
        planning_plan_revision=event.planning_plan_revision,
        planning_source_pack_fingerprint=event.planning_source_pack_fingerprint,
        planning_proposal_fingerprint=event.planning_proposal_fingerprint,
        item_id=event.item_id,
        accepted_item_fingerprint="f" * 64,
        accepted_item=event.accepted_item,
    )
    unresolved = _stage17_to_stage18(available, available, (plan,), (fuzzy_only_event,))

    assert unresolved["status"] == EvidenceStatusV1.INSUFFICIENT_EVIDENCE.value
    assert unresolved["matched_event_count"] == 0
