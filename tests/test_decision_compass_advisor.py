"""Focused tests for the explicit Stage 13B Growth Advisor handoff."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from pathlib import Path

import pytest

from second_brain.application.assistant import (
    ASSISTANT_CONTRACT_VERSION,
    ASSISTANT_OUTPUT_LABEL,
    AssistantAbstentionCode,
    AssistantExplicitContext,
    AssistantOption,
    AssistantReasoningEnvelopeV1,
    AssistantResultEnvelopeV1,
    AssistantResultKind,
)
from second_brain.application.compare import (
    CompareBranchStateV1,
    CompareExecutionContextV1,
)
from second_brain.application.decision_compass import (
    BuildDecisionCompass,
    DecisionCompassAdvisorStateV1,
    DecisionCompassBehaviorOptionBindingV1,
    DecisionCompassBehaviorScopeV1,
    DecisionCompassCriterionV1,
    DecisionCompassErrorCodeV1,
    DecisionCompassErrorV1,
    DecisionCompassGoalSelectorV1,
    DecisionCompassOptionV1,
    DecisionCompassRequestV1,
    DecisionCompassResultV1,
    DecisionCompassStructuralRelationCodeV1,
    build_growth_advisor_request,
)
from second_brain.application.growth_advisor import (
    BuildGrowthAdvisor,
    GrowthAdvisorBranchStateV1,
    GrowthAdvisorGoalPreviewV1,
    GrowthAdvisorRequestV1,
)
from second_brain.application.ports import CancellationToken, CancellationTokenSource
from second_brain.application.simulate_me import (
    DERIVATION_VERSION as SIMULATE_ME_DERIVATION_VERSION,
)
from second_brain.application.simulate_me import (
    POLICY_FINGERPRINT as SIMULATE_ME_POLICY_FINGERPRINT,
)
from second_brain.application.simulate_me import (
    POLICY_ID as SIMULATE_ME_POLICY_ID,
)
from second_brain.application.simulate_me import (
    SimulateMeAbstentionCode,
    SimulateMeOption,
    SimulateMeRequest,
    SimulateMeResult,
    SimulateMeResultKind,
)
from tests.conftest import write_note
from tests.test_growth_goal_progress_composition import (
    GOAL_ID,
    GROWTH_AT,
    PROGRESS_AS_OF,
    _goal_hash,
    _goal_note,
    _seed,
    _write_definition_and_observation,
)


def _execution(token: CancellationTokenSource | None = None) -> CompareExecutionContextV1:
    return CompareExecutionContextV1(
        cancellation=token or CancellationTokenSource(),
        deadline=10_000_000_000.0,
    )


def _request(
    goal_fingerprint: str,
    *,
    scope: DecisionCompassBehaviorScopeV1 | None = None,
    binding: DecisionCompassBehaviorOptionBindingV1 | None = None,
    criteria: tuple[object, ...] = (),
) -> DecisionCompassRequestV1:
    return DecisionCompassRequestV1(
        task="Choose one",
        options=(
            DecisionCompassOptionV1("alpha", "Alpha"),
            DecisionCompassOptionV1("beta", "Beta"),
        ),
        selected_goal=DecisionCompassGoalSelectorV1(GOAL_ID, goal_fingerprint),
        criteria=criteria,  # type: ignore[arg-type]
        explicit_constraints=("Keep the selected Goal unchanged",),
        explicit_context=(AssistantExplicitContext("fact", "One hour is available"),),
        progress_as_of=PROGRESS_AS_OF,
        behavioral_scope=scope,
        behavioral_option_binding=binding,
    )


class _RecordingAdvisor:
    def __init__(self, outcome: AssistantResultEnvelopeV1 | Exception) -> None:
        self.outcome = outcome
        self.calls = 0
        self.requests: list[AssistantReasoningEnvelopeV1] = []
        self.on_call: Callable[[], None] | None = None

    def advise(
        self,
        request: AssistantReasoningEnvelopeV1,
        *,
        cancellation: CancellationToken,
    ) -> AssistantResultEnvelopeV1:
        del cancellation
        self.calls += 1
        self.requests.append(request)
        if self.on_call is not None:
            self.on_call()
        if isinstance(self.outcome, Exception):
            raise self.outcome
        return self.outcome


class _FixedSimulateMe:
    def __init__(self, selected_id: str | None) -> None:
        self.selected_id = selected_id

    def execute(
        self,
        request: SimulateMeRequest,
        *,
        execution: CompareExecutionContextV1,
    ) -> SimulateMeResult:
        del execution
        selected = None
        if self.selected_id is not None:
            label = next(
                option.label for option in request.options if option.id == self.selected_id
            )
            selected = SimulateMeOption(self.selected_id, label)
        return SimulateMeResult(
            kind=(
                SimulateMeResultKind.PREDICTION
                if selected is not None
                else SimulateMeResultKind.ABSTENTION
            ),
            selected_option=selected,
            evidence_refs=(),
            contextual_evidence_refs=(),
            temporal_caveats=(),
            abstention_code=(
                None if selected is not None else SimulateMeAbstentionCode.NO_MATCHING_EVIDENCE
            ),
            derivation_version=SIMULATE_ME_DERIVATION_VERSION,
            policy_id=SIMULATE_ME_POLICY_ID,
            policy_fingerprint=SIMULATE_ME_POLICY_FINGERPRINT,
        )


def _recommendation(option_id: str) -> AssistantResultEnvelopeV1:
    label = "Alpha" if option_id == "alpha" else "Beta"
    return AssistantResultEnvelopeV1(
        output_label=ASSISTANT_OUTPUT_LABEL,
        kind=AssistantResultKind.RECOMMENDATION,
        recommendation=f"Выбран вариант {label}.",
        selected_option=AssistantOption(option_id, label),
        rationale=("Это независимый ответ на явный запрос.",),
        evidence_refs=(),
        constraints_used=(),
        objectives_used=(),
        uncertainty=(),
        abstention_code=None,
        contract_version=ASSISTANT_CONTRACT_VERSION,
    )


def _abstention() -> AssistantResultEnvelopeV1:
    return AssistantResultEnvelopeV1(
        output_label=ASSISTANT_OUTPUT_LABEL,
        kind=AssistantResultKind.ABSTENTION,
        recommendation=None,
        selected_option=None,
        rationale=("Недостаточно явных оснований.",),
        evidence_refs=(),
        constraints_used=(),
        objectives_used=(),
        uncertainty=(),
        abstention_code=AssistantAbstentionCode.INSUFFICIENT_BASIS,
        contract_version=ASSISTANT_CONTRACT_VERSION,
    )


def _setup(
    tmp_path: Path,
    outcome: AssistantResultEnvelopeV1 | Exception,
    *,
    simulate_id: str | None = "alpha",
    request: DecisionCompassRequestV1 | None = None,
) -> tuple[
    Path,
    DecisionCompassRequestV1,
    BuildDecisionCompass,
    BuildGrowthAdvisor,
    _RecordingAdvisor,
]:
    vault, store, reader = _seed(tmp_path)
    _write_definition_and_observation(vault, reader)
    port = _RecordingAdvisor(outcome)
    growth_advisor = BuildGrowthAdvisor(reader, port, clock=lambda: GROWTH_AT)
    compass = BuildDecisionCompass(
        reader=reader,
        store=store,
        growth_advisor=growth_advisor,
        simulate_me=_FixedSimulateMe(simulate_id),
        growth_clock=lambda: GROWTH_AT,
        behavioral_clock=lambda: GROWTH_AT,
        goal_clock=lambda: GROWTH_AT,
    )
    normalized_request = request or _request(_goal_hash(reader))
    return vault, normalized_request, compass, growth_advisor, port


def _build_base(
    compass: BuildDecisionCompass,
    request: DecisionCompassRequestV1,
) -> DecisionCompassResultV1:
    result = compass.execute(request, execution=_execution())
    assert isinstance(result, DecisionCompassResultV1)
    return result


def test_base_and_preview_are_provider_free_and_explicit_result_is_transient(
    tmp_path: Path,
) -> None:
    vault, request, compass, growth_advisor, port = _setup(tmp_path, _recommendation("alpha"))
    request = replace(
        request,
        criteria=(DecisionCompassCriterionV1("speed", "Speed", "Owner reference"),),
    )
    base = _build_base(compass, request)
    assert port.calls == 0
    assert base.advisor.state is DecisionCompassAdvisorStateV1.NOT_REQUESTED

    preview = compass.preview_advisor(request)
    assert isinstance(preview, GrowthAdvisorGoalPreviewV1)
    assert port.calls == 0
    advisor_request = build_growth_advisor_request(request)
    assert isinstance(advisor_request, GrowthAdvisorRequestV1)
    assert tuple(option.id for option in advisor_request.options) == ("alpha", "beta")

    result = compass.execute_advisor(
        request,
        base,
        preview,
        execution=_execution(),
        confirmed=True,
    )
    assert isinstance(result, DecisionCompassResultV1)
    assert result.advisor.state is DecisionCompassAdvisorStateV1.RESULT
    assert result.advisor.growth_advisor_branch is not None
    assert result.advisor.growth_advisor_branch.provenance is not None
    assert result.advisor.growth_advisor_branch.provenance.goal_source_uuid == GOAL_ID
    assert port.calls == 1
    assert growth_advisor is not None
    assert result.provenance.advisor == "growth-advisor-v1-explicit"
    assert DecisionCompassStructuralRelationCodeV1.ADVISOR_NOT_REQUESTED not in {
        relation.code for relation in result.structural_relations
    }
    assert any(
        relation.code is DecisionCompassStructuralRelationCodeV1.SIMULATE_ADVISOR_SAME_OPTION
        for relation in result.structural_relations
    )
    payload = result.to_json()
    assert "overall_score" not in payload
    assert "best_option" not in payload
    assert "criteria" not in port.requests[0].__class__.__annotations__
    assert port.requests[0].explicit_goals == (preview.goal_text,)
    assert port.requests[0].explicit_constraints == request.explicit_constraints
    assert port.requests[0].explicit_context == request.explicit_context
    assert vault.exists()

    rebuilt = compass.execute(request, execution=_execution())
    assert isinstance(rebuilt, DecisionCompassResultV1)
    assert rebuilt.advisor.state is DecisionCompassAdvisorStateV1.NOT_REQUESTED
    assert port.calls == 1


def test_explicit_different_option_and_abstention_are_not_promoted(
    tmp_path: Path,
) -> None:
    _vault, request, compass, _growth, port = _setup(tmp_path, _recommendation("beta"))
    base = _build_base(compass, request)
    preview = compass.preview_advisor(request)
    result = compass.execute_advisor(request, base, preview, execution=_execution())
    assert isinstance(result, DecisionCompassResultV1)
    assert any(
        relation.code is DecisionCompassStructuralRelationCodeV1.SIMULATE_ADVISOR_DIFFERENT_OPTIONS
        for relation in result.structural_relations
    )
    assert result.advisor.growth_advisor_branch is not None
    assert result.advisor.growth_advisor_branch.assistant_result is not None
    assert result.advisor.growth_advisor_branch.assistant_result.selected_option == AssistantOption(
        "beta",
        "Beta",
    )
    assert port.calls == 1

    _vault2, request2, compass2, _growth2, port2 = _setup(tmp_path / "abstention", _abstention())
    base2 = _build_base(compass2, request2)
    preview2 = compass2.preview_advisor(request2)
    result2 = compass2.execute_advisor(request2, base2, preview2, execution=_execution())
    assert isinstance(result2, DecisionCompassResultV1)
    assert any(
        relation.code is DecisionCompassStructuralRelationCodeV1.SIMULATE_ADVISOR_NOT_COMPARABLE
        for relation in result2.structural_relations
    )
    assert port2.calls == 1


def test_confirmation_is_required_by_existing_growth_advisor_boundary(
    tmp_path: Path,
) -> None:
    _vault, request, compass, _growth, port = _setup(tmp_path, _recommendation("alpha"))
    base = _build_base(compass, request)
    preview = compass.preview_advisor(request)
    result = compass.execute_advisor(
        request,
        base,
        preview,
        execution=_execution(),
        confirmed=False,
    )
    assert isinstance(result, DecisionCompassResultV1)
    assert result.advisor.state is DecisionCompassAdvisorStateV1.ERROR
    assert result.advisor.error is not None
    assert result.advisor.error.code == "DECISION_COMPASS_ADVISOR_INVALID_REQUEST"
    assert port.calls == 0


def test_advisor_abstention_and_timeout_keep_valid_siblings(tmp_path: Path) -> None:
    _vault, request, compass, _growth, port = _setup(tmp_path, _abstention())
    base = _build_base(compass, request)
    preview = compass.preview_advisor(request)
    result = compass.execute_advisor(request, base, preview, execution=_execution())
    assert isinstance(result, DecisionCompassResultV1)
    assert result.advisor.state is DecisionCompassAdvisorStateV1.ABSTENTION
    assert result.advisor.growth_advisor_branch is not None
    assert result.advisor.growth_advisor_branch.state is GrowthAdvisorBranchStateV1.ABSTENTION
    assert result.simulate_me.state is CompareBranchStateV1.RESULT
    assert port.calls == 1

    _vault2, request2, compass2, _growth2, port2 = _setup(tmp_path / "timeout", TimeoutError())
    base2 = _build_base(compass2, request2)
    preview2 = compass2.preview_advisor(request2)
    result2 = compass2.execute_advisor(request2, base2, preview2, execution=_execution())
    assert isinstance(result2, DecisionCompassResultV1)
    assert result2.advisor.state is DecisionCompassAdvisorStateV1.ERROR
    assert result2.advisor.error is not None
    assert result2.advisor.error.code == "DECISION_COMPASS_ADVISOR_TIMEOUT"
    assert result2.simulate_me.state is CompareBranchStateV1.RESULT
    assert port2.calls == 1


def test_goal_drift_before_and_during_handoff_fails_closed(tmp_path: Path) -> None:
    vault, request, compass, _growth, port = _setup(tmp_path, _recommendation("alpha"))
    base = _build_base(compass, request)
    preview = compass.preview_advisor(request)
    write_note(vault, "10 Projects/Goal-A.md", _goal_note(body="Changed Goal"))

    result = compass.execute_advisor(request, base, preview, execution=_execution())
    assert isinstance(result, DecisionCompassErrorV1)
    assert result.code is DecisionCompassErrorCodeV1.GOAL_SOURCE_CHANGED
    assert port.calls == 0

    vault2, request2, compass2, _growth2, port2 = _setup(
        tmp_path / "during",
        _recommendation("alpha"),
    )
    base2 = _build_base(compass2, request2)
    preview2 = compass2.preview_advisor(request2)

    def mutate_goal() -> None:
        write_note(
            vault2,
            "10 Projects/Goal-A.md",
            _goal_note(body="Changed during Advisor"),
        )

    port2.on_call = mutate_goal
    result2 = compass2.execute_advisor(request2, base2, preview2, execution=_execution())
    assert isinstance(result2, DecisionCompassErrorV1)
    assert result2.code is DecisionCompassErrorCodeV1.GOAL_SOURCE_CHANGED


def test_behavior_relation_requires_exact_binding_and_never_uses_labels(
    tmp_path: Path,
) -> None:
    vault, store, reader = _seed(tmp_path)
    _write_definition_and_observation(vault, reader)
    from second_brain.application.behavioral_self_model import BuildBehavioralSelfModel

    behavior = BuildBehavioralSelfModel(reader, clock=lambda: GROWTH_AT).execute()
    pattern = behavior.patterns[0]
    assert pattern.cohort is not None
    assert pattern.selected_option is not None
    binding = DecisionCompassBehaviorOptionBindingV1(
        request_option_id="alpha",
        behavioral_cohort_fingerprint=pattern.cohort.cohort_fingerprint,
        behavioral_option_index=pattern.selected_option.option_index,
        behavioral_option_fingerprint=pattern.selected_option.option_fingerprint,
    )
    request = _request(
        _goal_hash(reader),
        scope=DecisionCompassBehaviorScopeV1(pattern.cohort.cohort_fingerprint),
        binding=binding,
    )
    port = _RecordingAdvisor(_recommendation("alpha"))
    growth = BuildGrowthAdvisor(reader, port, clock=lambda: GROWTH_AT)
    compass = BuildDecisionCompass(
        reader=reader,
        store=store,
        growth_advisor=growth,
        simulate_me=_FixedSimulateMe("alpha"),
        growth_clock=lambda: GROWTH_AT,
        behavioral_clock=lambda: GROWTH_AT,
        goal_clock=lambda: GROWTH_AT,
    )
    base = _build_base(compass, request)
    preview = compass.preview_advisor(request)
    result = compass.execute_advisor(request, base, preview, execution=_execution())
    assert isinstance(result, DecisionCompassResultV1)
    assert any(
        relation.code is DecisionCompassStructuralRelationCodeV1.ADVISOR_BEHAVIOR_SAME_OPTION
        for relation in result.structural_relations
    )

    stale_request = replace(
        request,
        behavioral_option_binding=replace(
            binding,
            behavioral_option_fingerprint="sha256:" + "0" * 64,
        ),
    )
    stale_base = compass.execute(stale_request, execution=_execution())
    assert isinstance(stale_base, DecisionCompassResultV1)
    stale_preview = compass.preview_advisor(stale_request)
    stale_result = compass.execute_advisor(
        stale_request,
        stale_base,
        stale_preview,
        execution=_execution(),
    )
    assert isinstance(stale_result, DecisionCompassResultV1)
    assert any(
        relation.code is DecisionCompassStructuralRelationCodeV1.ADVISOR_BEHAVIOR_NOT_COMPARABLE
        for relation in stale_result.structural_relations
    )
    stale_relations = tuple(
        relation
        for relation in stale_result.structural_relations
        if relation.code
        in {
            DecisionCompassStructuralRelationCodeV1.ADVISOR_BEHAVIOR_NOT_COMPARABLE,
            DecisionCompassStructuralRelationCodeV1.ADVISOR_BEHAVIOR_SAME_OPTION,
            DecisionCompassStructuralRelationCodeV1.ADVISOR_BEHAVIOR_DIFFERENT_OPTIONS,
        }
    )
    assert stale_relations
    assert all(
        relation.left_option_id is None
        or relation.right_option_id is None
        or relation.left_option_id != relation.right_option_id
        for relation in stale_relations
    )


def test_cancelled_handoff_makes_no_provider_call(tmp_path: Path) -> None:
    _vault, request, compass, _growth, port = _setup(tmp_path, _recommendation("alpha"))
    base = _build_base(compass, request)
    preview = compass.preview_advisor(request)
    token = CancellationTokenSource()
    token.cancel()
    result = compass.execute_advisor(
        request,
        base,
        preview,
        execution=_execution(token),
    )
    assert isinstance(result, DecisionCompassErrorV1)
    assert result.code is DecisionCompassErrorCodeV1.CANCELLED
    assert port.calls == 0


def test_invalid_growth_advisor_builder_is_rejected() -> None:
    with pytest.raises(ValueError):
        BuildDecisionCompass(growth_advisor=object())  # type: ignore[arg-type]
