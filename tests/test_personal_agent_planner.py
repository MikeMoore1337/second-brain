"""Focused Phase 20.2 Advisor boundary and Run Proposal tests."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import replace

import pytest

from second_brain.application.assistant import (
    ASSISTANT_CONTRACT_VERSION,
    ASSISTANT_OUTPUT_LABEL,
    AssistantAbstentionCode,
    AssistantReasoningEnvelopeV1,
    AssistantResultEnvelopeV1,
    AssistantResultKind,
    serialize_assistant_reasoning_envelope,
)
from second_brain.application.personal_agent import (
    AgentContextPackV1,
    AgentExternalTargetRefV1,
    AgentMissionV1,
    AgentStage19ActionCapabilityV1,
    AgentStage19CapabilityProjectionV1,
    build_agent_context_pack,
)
from second_brain.application.personal_agent_planner import (
    AGENT_REASONING_ENVELOPE_CONTRACT_VERSION,
    AgentHoldStepV1,
    AgentPlannerCancelledError,
    AgentReasoningEnvelopeTooLargeError,
    AgentRunProposalInvalidError,
    AgentRunStepKindV1,
    AgentStage19ActionStepV1,
    BuildPersonalAgentRun,
    build_agent_reasoning_envelope,
    build_agent_run_proposal_from_advisor_result,
    serialize_agent_reasoning_envelope,
    serialize_agent_run_proposal,
    validate_agent_run_proposal,
)
from second_brain.application.personal_planning_store import PlanningPlanV1
from second_brain.application.ports import CancellationToken
from tests.test_personal_agent import _mission, _pack_inputs


class _Cancellation:
    def __init__(self, cancelled: bool = False) -> None:
        self.cancelled = cancelled

    def is_cancelled(self) -> bool:
        return self.cancelled


class _RecordingAdvisor:
    def __init__(self, result: AssistantResultEnvelopeV1) -> None:
        self.result = result
        self.calls: list[AssistantReasoningEnvelopeV1] = []

    def advise(
        self,
        request: AssistantReasoningEnvelopeV1,
        *,
        cancellation: CancellationToken,
    ) -> AssistantResultEnvelopeV1:
        self.calls.append(request)
        return self.result


def _recommendation(payload: Mapping[str, object]) -> AssistantResultEnvelopeV1:
    return AssistantResultEnvelopeV1(
        output_label=ASSISTANT_OUTPUT_LABEL,
        kind=AssistantResultKind.RECOMMENDATION,
        recommendation=json.dumps(payload, ensure_ascii=False, sort_keys=True),
        selected_option=None,
        rationale=("Ограниченное предложение для проверки владельцем.",),
        evidence_refs=(),
        constraints_used=(),
        objectives_used=(),
        uncertainty=(),
        abstention_code=None,
        contract_version=ASSISTANT_CONTRACT_VERSION,
    )


def _pack_with_target() -> tuple[
    AgentContextPackV1,
    AgentMissionV1,
    PlanningPlanV1,
    AgentStage19CapabilityProjectionV1,
]:
    mission, plan, states, stage19 = _pack_inputs()
    target = AgentExternalTargetRefV1(
        action_kind=AgentStage19ActionCapabilityV1.GITHUB_ISSUE_CREATE,
        repository="MikeMoore1337/second-brain",
    )
    mission = _mission(plan, plan.items[0], external_targets=(target,))
    pack = build_agent_context_pack(
        mission,
        plan,
        states,
        stage19,
        current_planning_plan=plan,
    )
    return pack, mission, plan, stage19


def _pack_without_target() -> AgentContextPackV1:
    mission, plan, states, stage19 = _pack_inputs()
    return build_agent_context_pack(
        mission,
        plan,
        states,
        stage19,
        current_planning_plan=plan,
    )


def test_reasoning_preview_is_exactly_the_payload_sent_to_advisor() -> None:
    pack, _mission_value, _plan_value, _stage19_value = _pack_with_target()

    envelope = build_agent_reasoning_envelope(pack)
    request = envelope.to_assistant_envelope()

    assert envelope.contract_version == AGENT_REASONING_ENVELOPE_CONTRACT_VERSION
    assert serialize_agent_reasoning_envelope(envelope) == serialize_assistant_reasoning_envelope(
        request
    )
    assert envelope.canonical_bytes == serialize_assistant_reasoning_envelope(request)
    assert "credential_profile_id" not in envelope.as_dict()
    assert "history" not in envelope.as_dict()
    assert "recommendation" not in envelope.as_dict()


def test_build_run_calls_existing_advisor_once_and_returns_non_executable_linear_proposal() -> None:
    pack, _mission_value, _plan_value, _stage19_value = _pack_with_target()
    payload = {
        "steps": [
            {
                "step_id": "checkpoint-1",
                "position": 1,
                "kind": "checkpoint",
                "summary": "Проверь исходный контекст перед действием.",
            },
            {
                "step_id": "action-1",
                "position": 2,
                "kind": "stage19_action",
                "action": {
                    "action_kind": "github.issue.create",
                    "repository": "MikeMoore1337/second-brain",
                    "title": "Проверка Stage 20",
                    "body": "Текст остаётся кандидатом до отдельного Stage 19 review.",
                },
            },
        ],
        "caveats": ["Требуется отдельное подтверждение владельца."],
    }
    advisor = _RecordingAdvisor(_recommendation(payload))
    runtime = BuildPersonalAgentRun(advisor)

    proposal = runtime.execute(
        pack,
        cancellation=_Cancellation(),
        proposal_id="0199f6c0-0000-7000-8000-000000000001",
    )

    assert len(advisor.calls) == 1
    envelope = build_agent_reasoning_envelope(pack)
    assert serialize_assistant_reasoning_envelope(advisor.calls[0]) == envelope.canonical_bytes
    assert tuple(step.position for step in proposal.steps) == (1, 2)
    assert tuple(step.kind for step in proposal.steps) == (
        AgentRunStepKindV1.CHECKPOINT,
        AgentRunStepKindV1.STAGE19_ACTION,
    )
    assert isinstance(proposal.steps[1], AgentStage19ActionStepV1)
    assert proposal.steps[1].action.repository == "MikeMoore1337/second-brain"
    assert "recommendation" not in proposal.as_dict()
    assert validate_agent_run_proposal(proposal) is proposal
    assert proposal == type(proposal).from_dict(proposal.as_dict())
    assert serialize_agent_run_proposal(proposal)


def test_provider_output_cannot_invent_target_or_action_fields() -> None:
    pack = _pack_without_target()
    unknown_target = _recommendation(
        {
            "steps": [
                {
                    "step_id": "action-1",
                    "position": 1,
                    "kind": "stage19_action",
                    "action": {
                        "action_kind": "github.issue.create",
                        "repository": "MikeMoore1337/second-brain",
                        "title": "Инъекция",
                        "body": "Нет explicit target.",
                        "labels": ["forbidden"],
                    },
                }
            ],
            "caveats": [],
        }
    )

    with pytest.raises(AgentRunProposalInvalidError):
        build_agent_run_proposal_from_advisor_result(pack, unknown_target)


def test_provider_output_is_bounded_linear_and_rejects_unknown_kind() -> None:
    pack = _pack_without_target()
    too_many_steps = {
        "steps": [
            {
                "step_id": f"checkpoint-{index}",
                "position": index,
                "kind": "checkpoint",
                "summary": "Короткая проверка.",
            }
            for index in range(1, 14)
        ],
        "caveats": [],
    }
    unknown_kind = {
        "steps": [
            {
                "step_id": "tool-1",
                "position": 1,
                "kind": "tool",
                "summary": "shell rm -rf",
            }
        ],
        "caveats": [],
    }

    with pytest.raises(AgentRunProposalInvalidError):
        build_agent_run_proposal_from_advisor_result(pack, _recommendation(too_many_steps))
    with pytest.raises(AgentRunProposalInvalidError):
        build_agent_run_proposal_from_advisor_result(pack, _recommendation(unknown_kind))


def test_abstention_becomes_safe_hold_without_raw_provider_text() -> None:
    pack = _pack_without_target()
    result = AssistantResultEnvelopeV1(
        output_label=ASSISTANT_OUTPUT_LABEL,
        kind=AssistantResultKind.ABSTENTION,
        recommendation=None,
        selected_option=None,
        rationale=("Внутренний provider text не сохраняется.",),
        evidence_refs=(),
        constraints_used=(),
        objectives_used=(),
        uncertainty=("provider-private detail",),
        abstention_code=AssistantAbstentionCode.INSUFFICIENT_BASIS,
        contract_version=ASSISTANT_CONTRACT_VERSION,
    )

    proposal = build_agent_run_proposal_from_advisor_result(pack, result)

    assert len(proposal.steps) == 1
    assert isinstance(proposal.steps[0], AgentHoldStepV1)
    assert proposal.steps[0].kind is AgentRunStepKindV1.HOLD
    assert "provider-private" not in json.dumps(proposal.as_dict(), ensure_ascii=False)


def test_build_run_is_explicit_and_cancellation_prevents_advisor_call() -> None:
    pack, _mission_value, _plan_value, _stage19_value = _pack_with_target()
    advisor = _RecordingAdvisor(_recommendation({"steps": [], "caveats": []}))
    runtime = BuildPersonalAgentRun(advisor)

    with pytest.raises(AgentPlannerCancelledError):
        runtime.execute(pack, cancellation=_Cancellation(cancelled=True))

    assert advisor.calls == []


def test_provider_envelope_fails_closed_when_existing_assistant_cap_cannot_carry_exact_pack() -> (
    None
):
    mission, plan, states, stage19 = _pack_inputs()
    oversized_mission = replace(mission, current_context=("Контекст " + "x" * 2000,))
    pack = build_agent_context_pack(
        oversized_mission,
        plan,
        states,
        stage19,
        current_planning_plan=plan,
    )

    with pytest.raises(AgentReasoningEnvelopeTooLargeError):
        build_agent_reasoning_envelope(pack).to_assistant_request()
