"""Provider-free Stage 20 Mission and Context Pack contract tests."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import cast
from uuid import uuid7

import pytest

from second_brain.application.action_gateway import (
    ACTION_GATEWAY_CONNECTOR,
    ACTION_GATEWAY_POLICY_FINGERPRINT,
    ACTION_GATEWAY_POLICY_ID,
)
from second_brain.application.execution_feedback import (
    ExecutionSourceStatusV1,
    accepted_item_fingerprint,
)
from second_brain.application.execution_feedback_projection import (
    ExecutionItemStateV1,
    project_execution_item,
)
from second_brain.application.executive_strategy import (
    POLICY_FINGERPRINT as STAGE16_POLICY_FINGERPRINT,
)
from second_brain.application.executive_strategy import POLICY_ID as STAGE16_POLICY_ID
from second_brain.application.personal_agent import (
    AGENT_MISSION_CONTRACT_VERSION,
    AGENT_POLICY_FINGERPRINT,
    AgentCapabilityInvalidError,
    AgentContextReadinessV1,
    AgentExternalTargetRefV1,
    AgentItemNotSelectedError,
    AgentMissionItemBindingV1,
    AgentMissionV1,
    AgentNonExecutableItemError,
    AgentSourceMismatchError,
    AgentSourceStaleError,
    AgentStage19CapabilityProjectionV1,
    AgentStage19StatusV1,
    AgentTargetNotAllowedError,
    build_agent_context_pack,
    default_stage19_action_catalog,
    serialize_agent_context_pack,
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


def _plan(
    *,
    item_specs: tuple[tuple[str, PlanningItemKindV1, int], ...],
    selected_ids: tuple[str, ...] | None = None,
) -> PlanningPlanV1:
    goal_id = uuid7()
    goal_ref = PlanningGoalRefV1(
        goal_source_uuid=goal_id,
        goal_identity_fingerprint="sha256:" + "1" * 64,
    )
    items: list[PlanningItemV1] = []
    for index, (item_id, kind, effort) in enumerate(item_specs):
        action_ref = PlanningActionBindingV1(
            goal_source_uuid=goal_id,
            goal_identity_fingerprint=goal_ref.goal_identity_fingerprint,
            strategy_snapshot_id=uuid7(),
            strategy_snapshot_fingerprint="2" * 64,
            reviewed_action_id=f"action-{index}",
            reviewed_action_fingerprint=f"{index + 3}" * 64,
            stage16_policy_id=STAGE16_POLICY_ID,
            stage16_policy_fingerprint=STAGE16_POLICY_FINGERPRINT,
        )
        items.append(
            PlanningItemV1(
                item_id=item_id,
                kind=kind,
                title=f"Проверить {item_id}",
                description="Ограниченный provider-free шаг.",
                goal_refs=(goal_ref,),
                action_refs=(action_ref,),
                parent_item_id=None,
                target_start_local=None,
                target_end_local=None,
                effort_minutes=effort,
                effort_source=PlanningEffortSourceV1.PROVIDER_PROPOSED,
                dependency_ids=(),
            )
        )
    resolved_selected_ids = selected_ids or tuple(item[0] for item in item_specs)
    plan_id = uuid7()
    core = {
        "plan_version": "1",
        "plan_id": str(plan_id),
        "revision": 1,
        "as_of": "2026-09-16T04:00:00Z",
        "source_pack_fingerprint": "4" * 64,
        "provider_envelope_fingerprint": "5" * 64,
        "provider_result_fingerprint": "6" * 64,
        "proposal_fingerprint": "7" * 64,
        "policy_id": PLANNING_POLICY_ID,
        "policy_fingerprint": PLANNING_POLICY_FINGERPRINT,
        "start_local": "2026-09-16",
        "end_local": "2026-09-16",
        "timezone": "UTC",
        "capacity": [
            {
                "date": "2026-09-16",
                "available_minutes": sum(
                    effort
                    for item_id, _kind, effort in item_specs
                    if item_id in resolved_selected_ids
                ),
            }
        ],
        "fixed_windows": [],
        "items": [item.as_dict() for item in items],
        "selected_item_ids": list(resolved_selected_ids),
        "item_order": list(resolved_selected_ids),
    }
    return PlanningPlanV1(
        plan_version="1",
        plan_id=plan_id,
        revision=1,
        as_of=datetime(2026, 9, 16, 4, tzinfo=UTC),
        source_pack_fingerprint="4" * 64,
        provider_envelope_fingerprint="5" * 64,
        provider_result_fingerprint="6" * 64,
        proposal_fingerprint="7" * 64,
        policy_id=PLANNING_POLICY_ID,
        policy_fingerprint=PLANNING_POLICY_FINGERPRINT,
        start_local="2026-09-16",
        end_local="2026-09-16",
        timezone="UTC",
        capacity=(
            PlanningCapacityEntryV1(
                local_date="2026-09-16",
                available_minutes=sum(
                    effort
                    for item_id, _kind, effort in item_specs
                    if item_id in resolved_selected_ids
                ),
            ),
        ),
        fixed_windows=(),
        items=tuple(items),
        selected_item_ids=resolved_selected_ids,
        item_order=resolved_selected_ids,
        plan_fingerprint=personal_planning_store_hash(core),
    )


def _stage19(
    *,
    status: AgentStage19StatusV1 = AgentStage19StatusV1.READY,
    repositories: tuple[str, ...] = ("MikeMoore1337/second-brain",),
) -> AgentStage19CapabilityProjectionV1:
    return AgentStage19CapabilityProjectionV1(
        contract="action-gateway-v1",
        connector=ACTION_GATEWAY_CONNECTOR,
        policy_id=ACTION_GATEWAY_POLICY_ID,
        policy_fingerprint=ACTION_GATEWAY_POLICY_FINGERPRINT,
        status=status,
        configured=status is AgentStage19StatusV1.READY,
        repositories=repositories,
        action_catalog=default_stage19_action_catalog(),
        owner_confirmation_required=True,
        background_execution=False,
    )


def _mission(
    plan: PlanningPlanV1,
    item: PlanningItemV1,
    *,
    external_targets: tuple[AgentExternalTargetRefV1, ...] = (),
) -> AgentMissionV1:
    return AgentMissionV1(
        contract_version=AGENT_MISSION_CONTRACT_VERSION,
        mission_id=uuid7(),
        planning_snapshot_id=plan.plan_id,
        planning_snapshot_fingerprint=plan.plan_fingerprint,
        planning_policy_id=PLANNING_POLICY_ID,
        planning_policy_fingerprint=PLANNING_POLICY_FINGERPRINT,
        selected_items=(
            AgentMissionItemBindingV1(
                item_id=item.item_id,
                accepted_item_fingerprint=accepted_item_fingerprint(item),
                item_kind=item.kind,
                goal_refs=item.goal_refs,
                action_refs=item.action_refs,
            ),
        ),
        task="Проверить выбранный шаг",
        constraints=("Не менять исходный план",),
        current_context=("Контекст собран из точных DTO",),
        external_targets=external_targets,
        created_at="2026-09-16T04:00:00Z",
        reviewed_at="2026-09-16T04:01:00Z",
    )


def _pack_inputs(
    *,
    stage19: AgentStage19CapabilityProjectionV1 | None = None,
) -> tuple[
    AgentMissionV1,
    PlanningPlanV1,
    dict[str, ExecutionItemStateV1],
    AgentStage19CapabilityProjectionV1,
]:
    plan = _plan(
        item_specs=(
            ("item-1", PlanningItemKindV1.NEXT_ACTION, 30),
            ("item-2", PlanningItemKindV1.COMMITMENT, 20),
        )
    )
    item = plan.items[0]
    mission = _mission(plan, item)
    state = project_execution_item(plan, item_id=item.item_id, events=())
    return mission, plan, {item.item_id: state}, stage19 or _stage19()


def test_context_pack_is_deterministic_and_contains_only_selected_neutral_context() -> None:
    mission, plan, states, stage19 = _pack_inputs()

    first = build_agent_context_pack(mission, plan, states, stage19, current_planning_plan=plan)
    second = build_agent_context_pack(mission, plan, states, stage19, current_planning_plan=plan)

    assert first == second
    assert first.readiness is AgentContextReadinessV1.EXACT_CURRENT
    assert first.fingerprint == first.pack_fingerprint
    assert serialize_agent_context_pack(first) == serialize_agent_context_pack(second)
    assert [item.item_id for item in first.selected_items] == ["item-1"]
    execution = first.selected_items[0].execution.as_dict()
    assert "history" not in execution
    assert "current_block_note" not in execution
    assert "title" not in execution
    assert "credential_profile_id" not in cast(dict[str, object], first.as_dict()["stage19"])
    assert first.as_dict()["policy_fingerprint"] == AGENT_POLICY_FINGERPRINT


def test_context_pack_rejects_stale_plan_and_stale_stage18_projection() -> None:
    mission, plan, states, stage19 = _pack_inputs()

    with pytest.raises(AgentSourceStaleError):
        build_agent_context_pack(
            mission,
            plan,
            states,
            stage19,
            current_planning_plan=_plan(
                item_specs=(("other-item", PlanningItemKindV1.NEXT_ACTION, 30),)
            ),
        )

    stale = project_execution_item(
        plan,
        item_id="item-1",
        events=(),
        source_status=ExecutionSourceStatusV1.STALE,
    )
    with pytest.raises(AgentSourceStaleError):
        build_agent_context_pack(
            mission,
            plan,
            {"item-1": stale},
            stage19,
            current_planning_plan=plan,
        )


def test_context_pack_rejects_item_identity_mismatch_and_unselected_item() -> None:
    _mission_from_inputs, plan, states, stage19 = _pack_inputs()
    changed_item = replace(plan.items[0], description="Изменённый источник")
    changed_mission = _mission(plan, changed_item)

    with pytest.raises(AgentSourceMismatchError):
        build_agent_context_pack(
            changed_mission,
            plan,
            states,
            stage19,
            current_planning_plan=plan,
        )

    unselected_plan = _plan(
        item_specs=(
            ("item-1", PlanningItemKindV1.NEXT_ACTION, 30),
            ("item-2", PlanningItemKindV1.COMMITMENT, 20),
        ),
        selected_ids=("item-1",),
    )
    unselected_mission = _mission(unselected_plan, unselected_plan.items[1])
    with pytest.raises(AgentItemNotSelectedError):
        build_agent_context_pack(
            unselected_mission,
            unselected_plan,
            {},
            stage19,
            current_planning_plan=unselected_plan,
        )


def test_non_executable_item_and_external_target_are_fail_closed() -> None:
    plan = _plan(item_specs=(("project-1", PlanningItemKindV1.PROJECT, 0),))
    with pytest.raises(AgentNonExecutableItemError):
        AgentMissionItemBindingV1(
            item_id="project-1",
            accepted_item_fingerprint=accepted_item_fingerprint(plan.items[0]),
            item_kind=plan.items[0].kind,
            goal_refs=plan.items[0].goal_refs,
            action_refs=plan.items[0].action_refs,
        )

    executable_plan = _plan(item_specs=(("item-1", PlanningItemKindV1.NEXT_ACTION, 30),))
    target = AgentExternalTargetRefV1(
        action_kind="github.issue.create",
        repository="OtherOwner/other-repo",
    )
    mission = _mission(executable_plan, executable_plan.items[0], external_targets=(target,))
    state = project_execution_item(executable_plan, item_id="item-1", events=())
    with pytest.raises(AgentTargetNotAllowedError):
        build_agent_context_pack(
            mission,
            executable_plan,
            {"item-1": state},
            _stage19(),
            current_planning_plan=executable_plan,
        )


def test_capability_unavailable_is_explicit_and_safe_mapping_rejects_credentials() -> None:
    mission, plan, states, _ = _pack_inputs()
    unavailable = _stage19(status=AgentStage19StatusV1.CREDENTIAL_UNAVAILABLE)
    pack = build_agent_context_pack(
        mission,
        plan,
        states,
        unavailable,
        current_planning_plan=plan,
    )

    assert pack.readiness is AgentContextReadinessV1.CAPABILITY_UNAVAILABLE
    assert pack.caveats == ("stage19_connector_not_ready",)
    assert "credential_profile_id" not in cast(dict[str, object], pack.as_dict()["stage19"])

    unsafe = unavailable.as_dict()
    unsafe["credential_profile_id"] = "must-not-cross-boundary"
    with pytest.raises(AgentCapabilityInvalidError):
        AgentStage19CapabilityProjectionV1.from_safe_mapping(unsafe)


def test_personal_agent_module_has_no_direct_provider_or_network_boundary() -> None:
    source = Path("src/second_brain/application/personal_agent.py").read_text(encoding="utf-8")

    assert "GitHubIssuesActionConnectorV1" not in source
    assert "second_brain.adapters" not in source
    assert "requests" not in source
    assert "httpx" not in source
