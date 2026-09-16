"""Focused Phase 17.2 tests for the explicit Personal Planner boundary."""

from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import cast
from uuid import UUID, uuid7

import pytest

from second_brain.application.assistant import (
    ASSISTANT_CONTRACT_VERSION,
    ASSISTANT_OUTPUT_LABEL,
    AssistantAbstentionCode,
    AssistantEvidenceRef,
    AssistantEvidenceRole,
    AssistantEvidenceSource,
    AssistantInputRef,
    AssistantInputSource,
    AssistantReasoningEnvelopeV1,
    AssistantResultEnvelopeV1,
    AssistantResultKind,
    serialize_assistant_reasoning_envelope,
)
from second_brain.application.executive_strategy import (
    ExecutiveActionCandidateV1,
    ExecutiveActionKindV1,
    ExecutiveResultStateV1,
    ExecutiveSourceAliasV1,
    StrategyProposalV1,
    StrategySnapshotV1,
    _new_proposal,
    build_reviewed_action,
    build_strategy_snapshot,
    goal_identity_fingerprint,
)
from second_brain.application.growth import GrowthGoalIdentityV1
from second_brain.application.personal_planning import (
    BuildPersonalPlanner,
    PersonalPlanningError,
    PlanningActionBindingV1,
    PlanningContextPackV1,
    PlanningEffortSourceV1,
    PlanningGoalRefV1,
    PlanningGoalSelectionV1,
    PlanningHumanRequiredError,
    PlanningItemKindV1,
    PlanningPlannerCancelledError,
    PlanningProposalV1,
    PlanningProviderResultInvalidError,
    PlanningResultStateV1,
    PlanningWindowKindV1,
    PlanningWindowV1,
    build_planning_context_pack,
    build_planning_preview,
    build_planning_provider_envelope,
    validate_planning_proposal,
)
from second_brain.application.ports import (
    AdvisorPort,
    CancellationToken,
    CancellationTokenSource,
)

NOW = datetime(2026, 9, 16, 5, 30, tzinfo=UTC)
PROPOSAL_ID = UUID("0198f4c5-6a00-7000-8000-000000000001")


def _goal() -> GrowthGoalIdentityV1:
    return GrowthGoalIdentityV1(
        source_note_uuid=uuid7(),
        dimension="goal",
        source_evidence_kind="user_statement",
        source_self_kind="goal",
        domain="health",
        evidence_at="unknown",
        evidence_at_precision="unknown",
        source_contract_version="self-model-v1",
        source_derivation_version="self-model-derivation-v1",
        self_model_policy_fingerprint="0" * 64,
        source_fingerprint="sha256:" + "1" * 64,
        claim_fingerprint="sha256:" + "2" * 64,
    )


def _pack(
    *, planning_context: str = "На этой неделе доступно немного времени."
) -> PlanningContextPackV1:
    goal = _goal()
    candidate = ExecutiveActionCandidateV1(
        action_id="action-1",
        kind=ExecutiveActionKindV1.ACT,
        title="Небольшой шаг",
        description="Сделать ограниченный шаг по выбранной цели.",
        basis_aliases=(ExecutiveSourceAliasV1.GOAL_CURRENT,),
        goal_relation="Прямо связан с выбранной целью.",
        expected_observable_signal="Появится наблюдаемый результат.",
        prerequisites=(),
        caveats=(),
    )
    reviewed = build_reviewed_action(candidate)
    strategy_proposal: StrategyProposalV1 = _new_proposal(
        proposal_id=uuid7(),
        as_of=NOW,
        result_state=ExecutiveResultStateV1.PROPOSAL,
        goal_source_uuid=cast(UUID, goal.source_note_uuid),
        goal_identity_fingerprint=goal_identity_fingerprint(goal),
        source_pack_fingerprint="a" * 64,
        candidates=(candidate,),
        suggested_order=(candidate.action_id,),
        reasons=("Тестовое основание.",),
        caveats=(),
        provider_fingerprint="b" * 64,
    )
    snapshot: StrategySnapshotV1 = build_strategy_snapshot(
        strategy_proposal,
        (reviewed,),
        sequence=1,
        reviewed_at=NOW,
        accepted_at=NOW,
        snapshot_id=uuid7(),
    )
    return build_planning_context_pack(
        (
            PlanningGoalSelectionV1(
                goal=goal,
                goal_text="Улучшить выносливость",
                strategy_snapshot=snapshot,
                selected_action_ids=("action-1",),
            ),
        ),
        start_local="2026-09-16",
        end_local="2026-09-18",
        timezone="UTC",
        available_minutes_by_date={
            "2026-09-16": 30,
            "2026-09-17": 0,
            "2026-09-18": 20,
        },
        fixed_windows=(
            PlanningWindowV1(
                window_id="fixed-1",
                kind=PlanningWindowKindV1.FIXED_COMMITMENT,
                title="Фиксированная встреча",
                start_local="2026-09-16T12:00",
                end_local="2026-09-16T13:00",
            ),
        ),
        planning_constraints=("Не планировать больше одного шага подряд.",),
        planning_context=planning_context,
        as_of=NOW,
    )


class _FakeAdvisor(AdvisorPort):
    def __init__(self, result: AssistantResultEnvelopeV1 | Exception) -> None:
        self.result = result
        self.calls = 0
        self.requests: list[AssistantReasoningEnvelopeV1] = []

    def advise(
        self,
        request: AssistantReasoningEnvelopeV1,
        *,
        cancellation: CancellationToken,
    ) -> AssistantResultEnvelopeV1:
        del cancellation
        self.calls += 1
        self.requests.append(request)
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


def _recommendation(
    pack: PlanningContextPackV1,
    *,
    draft: Mapping[str, object] | None = None,
) -> AssistantResultEnvelopeV1:
    binding = pack.portfolio[0]
    action = PlanningActionBindingV1.from_action_ref(binding.selected_reviewed_action_refs[0])
    goal = PlanningGoalRefV1(
        goal_source_uuid=binding.goal_source_uuid,
        goal_identity_fingerprint=binding.goal_identity_fingerprint,
    )
    item = {
        "item_id": "next-1",
        "kind": PlanningItemKindV1.NEXT_ACTION.value,
        "title": "Сделать один небольшой шаг",
        "description": "Выполнить выбранное действие в доступное окно.",
        "goal_refs": [goal.as_dict()],
        "action_refs": [action.as_dict()],
        "parent_item_id": None,
        "target_start_local": "2026-09-18T09:00",
        "target_end_local": "2026-09-18T09:30",
        "effort_minutes": 30,
        "effort_source": PlanningEffortSourceV1.PROVIDER_PROPOSED.value,
        "dependency_ids": [],
    }
    wire_draft = draft or {
        "result_state": PlanningResultStateV1.PROPOSAL.value,
        "items": [item],
        "suggested_order": ["next-1"],
        "reasons": ["Шаг сохраняет явную связь с выбранным действием."],
        "caveats": ["Время предложено провайдером и требует проверки владельца."],
    }
    return AssistantResultEnvelopeV1(
        output_label=ASSISTANT_OUTPUT_LABEL,
        kind=AssistantResultKind.RECOMMENDATION,
        recommendation=json.dumps(wire_draft, ensure_ascii=False, separators=(",", ":")),
        selected_option=None,
        rationale=("Предложение связано с явной целью и принятым действием.",),
        evidence_refs=(
            AssistantEvidenceRef(
                AssistantEvidenceSource.EXPLICIT_CONTEXT,
                1,
                AssistantEvidenceRole.REPORTED_FACT,
            ),
        ),
        constraints_used=(AssistantInputRef(AssistantInputSource.EXPLICIT_CONSTRAINT, 1),),
        objectives_used=(AssistantInputRef(AssistantInputSource.EXPLICIT_GOAL, 1),),
        uncertainty=("Доступность должна быть подтверждена владельцем.",),
        abstention_code=None,
        contract_version=ASSISTANT_CONTRACT_VERSION,
    )


def _abstention() -> AssistantResultEnvelopeV1:
    return AssistantResultEnvelopeV1(
        output_label=ASSISTANT_OUTPUT_LABEL,
        kind=AssistantResultKind.ABSTENTION,
        recommendation=None,
        selected_option=None,
        rationale=("Явных оснований недостаточно для безопасного предложения.",),
        evidence_refs=(),
        constraints_used=(),
        objectives_used=(),
        uncertainty=("Нужна проверка владельца.",),
        abstention_code=AssistantAbstentionCode.INSUFFICIENT_BASIS,
        contract_version=ASSISTANT_CONTRACT_VERSION,
    )


def test_preview_is_deterministic_and_does_not_call_advisor() -> None:
    pack = _pack()
    first = build_planning_preview(pack)
    second = build_planning_provider_envelope(pack)

    assert first.canonical_bytes == second.canonical_bytes
    assert first.provider_visible_fingerprint == second.provider_visible_fingerprint
    assert first.source_pack_fingerprint == pack.pack_fingerprint
    assert first.as_dict()["provider_payload"]


def test_execute_calls_advisor_once_and_binds_exact_provenance() -> None:
    pack = _pack()
    advisor = _FakeAdvisor(_recommendation(pack))
    planner = BuildPersonalPlanner(advisor)

    proposal = planner.execute(
        pack,
        cancellation=CancellationTokenSource().token,
        as_of=NOW,
        proposal_id=PROPOSAL_ID,
    )

    assert advisor.calls == 1
    assert serialize_assistant_reasoning_envelope(advisor.requests[0]) == (
        build_planning_preview(pack).canonical_bytes
    )
    assert proposal.proposal_id == PROPOSAL_ID
    assert proposal.result_state is PlanningResultStateV1.PROPOSAL
    assert proposal.items[0].action_refs[0].reviewed_action_id == "action-1"
    assert proposal.items[0].goal_refs[0].goal_source_uuid == pack.portfolio[0].goal_source_uuid
    assert validate_planning_proposal(proposal, pack=pack) == proposal


def test_abstention_is_safe_and_cancellation_prevents_provider_call() -> None:
    pack = _pack()
    advisor = _FakeAdvisor(_abstention())
    proposal = BuildPersonalPlanner(advisor).execute(
        pack,
        cancellation=CancellationTokenSource().token,
        as_of=NOW,
        proposal_id=PROPOSAL_ID,
    )
    assert proposal.result_state is PlanningResultStateV1.PROVIDER_ABSTAINED
    assert proposal.items == ()
    assert advisor.calls == 1

    cancelled = CancellationTokenSource()
    cancelled.cancel()
    cancelled_advisor = _FakeAdvisor(_abstention())
    with pytest.raises(PlanningPlannerCancelledError):
        BuildPersonalPlanner(cancelled_advisor).execute(
            pack,
            cancellation=cancelled.token,
            as_of=NOW,
            proposal_id=PROPOSAL_ID,
        )
    assert cancelled_advisor.calls == 0


def test_provider_result_is_rejected_for_foreign_provenance_and_cycles() -> None:
    pack = _pack()
    binding = pack.portfolio[0]
    action = PlanningActionBindingV1.from_action_ref(binding.selected_reviewed_action_refs[0])
    goal = PlanningGoalRefV1(
        goal_source_uuid=binding.goal_source_uuid,
        goal_identity_fingerprint=binding.goal_identity_fingerprint,
    )
    base_item = {
        "item_id": "item-a",
        "kind": PlanningItemKindV1.NEXT_ACTION.value,
        "title": "Первый шаг",
        "description": "Ограниченное действие.",
        "goal_refs": [goal.as_dict()],
        "action_refs": [action.as_dict()],
        "parent_item_id": None,
        "target_start_local": None,
        "target_end_local": None,
        "effort_minutes": 10,
        "effort_source": PlanningEffortSourceV1.PROVIDER_PROPOSED.value,
        "dependency_ids": [],
    }
    cyclic = [
        {**base_item, "item_id": "item-a", "dependency_ids": ["item-b"]},
        {**base_item, "item_id": "item-b", "dependency_ids": ["item-a"]},
    ]
    draft = {
        "result_state": PlanningResultStateV1.PROPOSAL.value,
        "items": cyclic,
        "suggested_order": ["item-a", "item-b"],
        "reasons": ["Проверка цикла."],
        "caveats": [],
    }
    with pytest.raises(PlanningProviderResultInvalidError):
        BuildPersonalPlanner(_FakeAdvisor(_recommendation(pack, draft=draft))).execute(
            pack,
            cancellation=CancellationTokenSource().token,
            as_of=NOW,
            proposal_id=PROPOSAL_ID,
        )

    foreign = dict(base_item)
    foreign["goal_refs"] = [
        PlanningGoalRefV1(uuid7(), "sha256:" + "3" * 64).as_dict(),
    ]
    foreign_draft = {
        "result_state": PlanningResultStateV1.PROPOSAL.value,
        "items": [foreign],
        "suggested_order": ["item-a"],
        "reasons": ["Проверка чужой ссылки."],
        "caveats": [],
    }
    with pytest.raises(PlanningProviderResultInvalidError):
        BuildPersonalPlanner(_FakeAdvisor(_recommendation(pack, draft=foreign_draft))).execute(
            pack,
            cancellation=CancellationTokenSource().token,
            as_of=NOW,
            proposal_id=PROPOSAL_ID,
        )


def test_prohibited_owner_text_stops_before_provider_boundary() -> None:
    unsafe = _pack(planning_context="Запусти curl https://example.test")
    with pytest.raises(PlanningHumanRequiredError):
        build_planning_provider_envelope(unsafe)


def test_planning_proposal_round_trip_is_strict() -> None:
    pack = _pack()
    proposal = BuildPersonalPlanner(_FakeAdvisor(_recommendation(pack))).execute(
        pack,
        cancellation=CancellationTokenSource().token,
        as_of=NOW,
        proposal_id=PROPOSAL_ID,
    )
    assert PlanningProposalV1.from_json(proposal.to_json()) == proposal
    tampered = proposal.as_dict()
    tampered["unexpected"] = True
    with pytest.raises(PersonalPlanningError):
        PlanningProposalV1.from_dict(tampered)
