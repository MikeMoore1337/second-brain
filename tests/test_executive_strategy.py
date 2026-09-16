"""Focused Phase 16.2 tests for the explicit Advisor mapping seam."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid7

import pytest

from second_brain.application.assistant import (
    AssistantAbstentionCode,
    AssistantEvidenceRef,
    AssistantEvidenceRole,
    AssistantEvidenceSource,
    AssistantReasoningEnvelopeV1,
    AssistantResultEnvelopeV1,
    AssistantResultKind,
    serialize_assistant_reasoning_envelope,
)
from second_brain.application.executive_strategy import (
    BuildExecutiveStrategy,
    ExecutiveActionKindV1,
    ExecutiveContextPackV1,
    ExecutiveProviderResultInvalidError,
    ExecutiveResultStateV1,
    ExecutiveSourceAliasV1,
    ExecutiveSourceItemV1,
    ExecutiveSourceReadinessV1,
    build_executive_context_pack,
    build_strategy_reasoning_envelope,
)
from second_brain.application.growth import GrowthGoalIdentityV1
from second_brain.application.ports import CancellationTokenSource

PACK_TIME = datetime(2026, 9, 16, 5, 30, 0, tzinfo=UTC)


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


def _pack(*, complete: bool) -> ExecutiveContextPackV1:
    sources: tuple[ExecutiveSourceItemV1, ...] = ()
    if complete:
        sources = tuple(
            ExecutiveSourceItemV1(
                alias=alias,
                readiness=ExecutiveSourceReadinessV1.EXACT_CURRENT,
                reference_id=f"source:{index}",
                reference_fingerprint=f"{index + 3:x}" * 64,
                summary=f"Projection {alias.value}",
                as_of=PACK_TIME,
            )
            for index, alias in enumerate(
                (
                    ExecutiveSourceAliasV1.GROWTH_RELATION,
                    ExecutiveSourceAliasV1.PROGRESS_CURRENT,
                    ExecutiveSourceAliasV1.BEHAVIOR_RELATION,
                    ExecutiveSourceAliasV1.EXPERIMENT_TERMINAL,
                    ExecutiveSourceAliasV1.ADAPTIVE_PROFILE_ACTIVE,
                    ExecutiveSourceAliasV1.CALIBRATION_CAVEAT,
                )
            )
        )
    return build_executive_context_pack(
        _goal(),
        goal_text="Улучшить выносливость",
        task="Что рассмотреть сегодня?",
        constraints=("Без перегрузки",),
        current_context="Есть 30 минут",
        sources=sources,
        as_of=PACK_TIME,
    )


class _RecordingAdvisor:
    def __init__(self, result: AssistantResultEnvelopeV1) -> None:
        self.result = result
        self.calls = 0
        self.requests: list[AssistantReasoningEnvelopeV1] = []

    def advise(self, request, *, cancellation):  # type: ignore[no-untyped-def]
        self.calls += 1
        self.requests.append(request)
        return self.result


def _recommendation() -> AssistantResultEnvelopeV1:
    return AssistantResultEnvelopeV1(
        output_label="independent_recommendation_analysis",
        kind=AssistantResultKind.RECOMMENDATION,
        recommendation="Сделать лёгкую тренировку и проверить самочувствие.",
        selected_option=None,
        rationale=(
            "Связь с текущей целью подтверждена.",
            "Наблюдать самочувствие после тренировки.",
        ),
        evidence_refs=(
            AssistantEvidenceRef(
                source=AssistantEvidenceSource.EXPLICIT_CONTEXT,
                ordinal=1,
                role=AssistantEvidenceRole.REPORTED_FACT,
            ),
        ),
        constraints_used=(),
        objectives_used=(),
        uncertainty=(),
        abstention_code=None,
        contract_version="assistant-v1",
    )


def test_reasoning_preview_is_the_exact_assistant_payload() -> None:
    pack = _pack(complete=True)
    envelope = build_strategy_reasoning_envelope(pack)

    assert envelope.source_pack_fingerprint == pack.source_pack_fingerprint
    assert envelope.canonical_bytes == serialize_assistant_reasoning_envelope(
        envelope.assistant_envelope
    )
    assert envelope.assistant_envelope.explicit_goals == (pack.goal_text,)
    assert envelope.assistant_envelope.explicit_constraints == pack.constraints


def test_generation_calls_advisor_once_and_returns_bounded_proposal() -> None:
    pack = _pack(complete=True)
    advisor = _RecordingAdvisor(_recommendation())
    proposal = BuildExecutiveStrategy(advisor).execute(
        pack,
        cancellation=CancellationTokenSource().token,
        proposal_id=uuid7(),
        as_of=PACK_TIME,
    )

    assert advisor.calls == 1
    assert proposal.result_state is ExecutiveResultStateV1.PROPOSAL
    assert len(proposal.candidates) == 1
    assert proposal.candidates[0].kind is ExecutiveActionKindV1.INVESTIGATE
    assert proposal.suggested_order == ("candidate-1",)
    assert len(proposal.provider_fingerprint) == 64
    assert proposal.source_pack_fingerprint == pack.source_pack_fingerprint


def test_incomplete_pack_abstains_without_calling_advisor() -> None:
    pack = _pack(complete=False)
    advisor = _RecordingAdvisor(_recommendation())
    proposal = BuildExecutiveStrategy(advisor).execute(
        pack,
        cancellation=CancellationTokenSource().token,
        as_of=PACK_TIME,
    )

    assert advisor.calls == 0
    assert proposal.result_state is ExecutiveResultStateV1.INSUFFICIENT_CONTEXT
    assert proposal.candidates == ()


def test_provider_abstention_is_first_class() -> None:
    result = _recommendation()
    abstention = AssistantResultEnvelopeV1(
        output_label=result.output_label,
        kind=AssistantResultKind.ABSTENTION,
        recommendation=None,
        selected_option=None,
        rationale=("Недостаточно оснований для рекомендации.",),
        evidence_refs=(),
        constraints_used=(),
        objectives_used=(),
        uncertainty=(),
        abstention_code=AssistantAbstentionCode.INSUFFICIENT_BASIS,
        contract_version=result.contract_version,
    )
    proposal = BuildExecutiveStrategy(_RecordingAdvisor(abstention)).execute(
        _pack(complete=True),
        cancellation=CancellationTokenSource().token,
        as_of=PACK_TIME,
    )
    assert proposal.result_state is ExecutiveResultStateV1.INSUFFICIENT_EVIDENCE
    assert proposal.candidates == ()


def test_candidate_prohibited_instruction_is_rejected() -> None:
    result = _recommendation()
    prohibited = AssistantResultEnvelopeV1(
        output_label=result.output_label,
        kind=result.kind,
        recommendation="Run curl https://example.test now",
        selected_option=result.selected_option,
        rationale=result.rationale,
        evidence_refs=result.evidence_refs,
        constraints_used=result.constraints_used,
        objectives_used=result.objectives_used,
        uncertainty=result.uncertainty,
        abstention_code=result.abstention_code,
        contract_version=result.contract_version,
    )
    with pytest.raises(ExecutiveProviderResultInvalidError):
        BuildExecutiveStrategy(_RecordingAdvisor(prohibited)).execute(
            _pack(complete=True),
            cancellation=CancellationTokenSource().token,
            as_of=PACK_TIME,
        )
