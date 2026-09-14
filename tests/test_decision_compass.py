"""Focused provider-free tests for Cognitive Twin v3 Stage 13A."""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest

from second_brain.application.behavioral_self_model import (
    DEFAULT_BEHAVIORAL_SELF_MODEL_REQUEST,
    BehavioralPatternTypeV1,
    BehavioralSelfModelRequest,
    BehavioralSelfModelResultV1,
    BuildBehavioralSelfModel,
)
from second_brain.application.compare import (
    CompareBranchErrorCodeV1,
    CompareBranchStateV1,
    CompareExecutionContextV1,
)
from second_brain.application.decision_compass import (
    POLICY_FINGERPRINT,
    BuildDecisionCompass,
    DecisionCompassBehavioralStateV1,
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
    validate_decision_compass_policy,
    validate_decision_compass_request,
)
from second_brain.application.growth import (
    BuildGrowthGoalContext,
    GrowthEngineRequestV1,
    GrowthGoalMissingError,
    GrowthGoalSelectionModeV1,
    GrowthGoalSelectionV1,
)
from second_brain.application.growth_goal_progress_composition import (
    BuildGrowthGoalProgressCompositionV1,
    GrowthGoalProgressCompositionRequestV1,
    GrowthGoalProgressCompositionResultV1,
)
from second_brain.application.ports import CancellationTokenSource, VaultReader
from second_brain.application.reports import VaultSnapshot
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
    SimulateMeDimension,
    SimulateMeEvidenceRef,
    SimulateMeOption,
    SimulateMeRequest,
    SimulateMeResult,
    SimulateMeResultKind,
)
from tests.test_growth_goal_progress_composition import (
    GOAL_ID,
    GROWTH_AT,
    PROGRESS_AS_OF,
    _goal_hash,
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
    task: str = "Choose one",
    scope: DecisionCompassBehaviorScopeV1 | None = None,
    binding: DecisionCompassBehaviorOptionBindingV1 | None = None,
    max_result_bytes: int = 131_072,
) -> DecisionCompassRequestV1:
    return DecisionCompassRequestV1(
        task=task,
        options=(
            DecisionCompassOptionV1("alpha", "Alpha"),
            DecisionCompassOptionV1("beta", "Beta"),
        ),
        selected_goal=DecisionCompassGoalSelectorV1(GOAL_ID, goal_fingerprint),
        progress_as_of=PROGRESS_AS_OF,
        behavioral_scope=scope,
        behavioral_option_binding=binding,
        max_result_bytes=max_result_bytes,
    )


class _CountingSimulateMe:
    def __init__(self, selected_id: str | None) -> None:
        self.selected_id = selected_id
        self.calls: list[tuple[SimulateMeRequest, CompareExecutionContextV1]] = []

    def execute(
        self,
        request: SimulateMeRequest,
        *,
        execution: CompareExecutionContextV1,
    ) -> SimulateMeResult:
        self.calls.append((request, execution))
        selected = (
            SimulateMeOption(self.selected_id, "Alpha" if self.selected_id == "alpha" else "Beta")
            if self.selected_id is not None
            else None
        )
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


class _InvalidTemporalSimulateMe:
    def execute(
        self,
        request: SimulateMeRequest,
        *,
        execution: CompareExecutionContextV1,
    ) -> SimulateMeResult:
        del request, execution
        return SimulateMeResult(
            kind=SimulateMeResultKind.PREDICTION,
            selected_option=SimulateMeOption("alpha", "Alpha"),
            evidence_refs=(
                SimulateMeEvidenceRef(
                    claim_id=GOAL_ID,
                    dimension=SimulateMeDimension.PREFERENCE,
                    note_ids=(GOAL_ID,),
                    evidence_at="unknown",
                ),
            ),
            contextual_evidence_refs=(),
            temporal_caveats=(),
            abstention_code=None,
            derivation_version=SIMULATE_ME_DERIVATION_VERSION,
            policy_id=SIMULATE_ME_POLICY_ID,
            policy_fingerprint=SIMULATE_ME_POLICY_FINGERPRINT,
        )


class _CountingBehavioral:
    def __init__(self, reader: VaultReader) -> None:
        self.calls = 0
        self._delegate = BuildBehavioralSelfModel(reader)

    def execute(
        self,
        request: BehavioralSelfModelRequest = DEFAULT_BEHAVIORAL_SELF_MODEL_REQUEST,
    ) -> BehavioralSelfModelResultV1:
        self.calls += 1
        return self._delegate.execute(request)


def test_policy_fingerprint_is_exact() -> None:
    assert validate_decision_compass_policy() == POLICY_FINGERPRINT
    assert POLICY_FINGERPRINT == (
        "sha256:a128e475cc3d39eeca1e25b39004002e409f3c89f61426289ffaf04a0e5f0aed"
    )


def test_request_bounds_and_strict_parser_reject_invalid_values() -> None:
    valid = _request("sha256:" + "a" * 64)
    assert validate_decision_compass_request(valid).options == valid.options

    invalid_requests = (
        replace(valid, task="\u0000task"),
        replace(
            valid,
            options=(valid.options[0], valid.options[0]),
        ),
        replace(
            valid,
            criteria=tuple(DecisionCompassCriterionV1(f"c{index}", "x", "x") for index in range(9)),
        ),
        replace(valid, max_result_bytes=131_073),
        replace(valid, progress_as_of="2026-09-13T14:00:00+03:00"),
    )
    for invalid in invalid_requests:
        with pytest.raises(ValueError):
            validate_decision_compass_request(invalid)

    with pytest.raises(ValueError):
        DecisionCompassRequestV1.from_dict({"task": "Choose", "unknown": True})


def test_binding_without_scope_or_for_unknown_request_option_is_rejected() -> None:
    valid = _request("sha256:" + "a" * 64)
    binding = DecisionCompassBehaviorOptionBindingV1(
        request_option_id="missing",
        behavioral_cohort_fingerprint="sha256:" + "b" * 64,
        behavioral_option_index=0,
        behavioral_option_fingerprint="sha256:" + "c" * 64,
    )
    with pytest.raises(ValueError):
        validate_decision_compass_request(replace(valid, behavioral_option_binding=binding))


def test_invalid_request_and_missing_goal_fail_before_source_reads() -> None:
    class _ExplodingReader:
        def scan(self) -> VaultSnapshot:
            raise AssertionError("invalid requests must fail before source reads")

    request = DecisionCompassRequestV1(
        task="Choose one",
        options=(DecisionCompassOptionV1("alpha", "Alpha"),),
        selected_goal=None,
        progress_as_of=PROGRESS_AS_OF,
    )
    with pytest.raises(ValueError):
        validate_decision_compass_request(request)
    result = BuildDecisionCompass(reader=_ExplodingReader()).execute(
        request,
        execution=_execution(),
    )
    assert isinstance(result, DecisionCompassErrorV1)
    assert result.code is DecisionCompassErrorCodeV1.GOAL_REQUIRED


def test_composes_real_stage12d_without_behavioral_scope_or_advisor(
    tmp_path: Path,
) -> None:
    vault, store, reader = _seed(tmp_path)
    _write_definition_and_observation(vault, reader)
    request = _request(_goal_hash(reader))

    result = BuildDecisionCompass(
        reader=reader,
        store=store,
        growth_clock=lambda: GROWTH_AT,
        behavioral_clock=lambda: GROWTH_AT,
        goal_clock=lambda: GROWTH_AT,
    ).execute(request, execution=_execution())

    assert isinstance(result, DecisionCompassResultV1)
    assert result.behavioral.state is DecisionCompassBehavioralStateV1.NOT_SELECTED
    assert result.advisor.state == "not_requested"
    assert {relation.code for relation in result.structural_relations} == {
        DecisionCompassStructuralRelationCodeV1.ADVISOR_NOT_REQUESTED,
        DecisionCompassStructuralRelationCodeV1.BEHAVIOR_SCOPE_NOT_SELECTED,
    }
    payload = json.loads(result.to_json())
    assert payload["growth_progress"]["selected_goal_source_uuid"] == str(GOAL_ID)
    assert "overall_score" not in json.dumps(payload)
    assert "best_option" not in json.dumps(payload)


def test_criteria_are_preserved_but_inert_for_all_authoritative_branches(
    tmp_path: Path,
) -> None:
    vault, store, reader = _seed(tmp_path)
    _write_definition_and_observation(vault, reader)
    fingerprint = _goal_hash(reader)
    baseline = BuildDecisionCompass(
        reader=reader,
        store=store,
        growth_clock=lambda: GROWTH_AT,
        behavioral_clock=lambda: GROWTH_AT,
    ).execute(_request(fingerprint), execution=_execution())
    with_criteria = BuildDecisionCompass(
        reader=reader,
        store=store,
        growth_clock=lambda: GROWTH_AT,
        behavioral_clock=lambda: GROWTH_AT,
    ).execute(
        replace(
            _request(fingerprint),
            criteria=(DecisionCompassCriterionV1("speed", "Speed", "Owner supplied"),),
        ),
        execution=_execution(),
    )
    assert isinstance(baseline, DecisionCompassResultV1)
    assert isinstance(with_criteria, DecisionCompassResultV1)
    assert baseline.simulate_me == with_criteria.simulate_me
    assert baseline.behavioral == with_criteria.behavioral
    assert baseline.growth_progress == with_criteria.growth_progress
    assert with_criteria.request.criteria[0].label == "Speed"


def test_scope_is_explicit_and_behavior_is_reread_with_exact_binding(
    tmp_path: Path,
) -> None:
    vault, store, reader = _seed(tmp_path)
    _write_definition_and_observation(vault, reader)
    behavioral = BuildBehavioralSelfModel(reader, clock=lambda: GROWTH_AT).execute()
    assert len(behavioral.patterns) == 1
    pattern = behavioral.patterns[0]
    assert pattern.pattern_type is BehavioralPatternTypeV1.REPEATED_EXACT_CHOICE
    assert pattern.cohort is not None
    assert pattern.selected_option is not None

    scope = DecisionCompassBehaviorScopeV1(pattern.cohort.cohort_fingerprint)
    binding = DecisionCompassBehaviorOptionBindingV1(
        request_option_id="alpha",
        behavioral_cohort_fingerprint=pattern.cohort.cohort_fingerprint,
        behavioral_option_index=pattern.selected_option.option_index,
        behavioral_option_fingerprint=pattern.selected_option.option_fingerprint,
    )
    simulate = _CountingSimulateMe("alpha")
    result = BuildDecisionCompass(
        reader=reader,
        store=store,
        simulate_me=simulate,
        growth_clock=lambda: GROWTH_AT,
        behavioral_clock=lambda: GROWTH_AT,
        goal_clock=lambda: GROWTH_AT,
    ).execute(
        _request(_goal_hash(reader), scope=scope, binding=binding),
        execution=_execution(),
    )

    assert isinstance(result, DecisionCompassResultV1)
    assert len(simulate.calls) == 1
    simulate_request = simulate.calls[0][0]
    assert simulate_request.query == "Choose one"
    assert tuple(option.id for option in simulate_request.options) == (
        "alpha",
        "beta",
    )
    assert result.behavioral.state is DecisionCompassBehavioralStateV1.RESULT
    assert any(
        relation.code is DecisionCompassStructuralRelationCodeV1.SIMULATE_BEHAVIOR_SAME_OPTION
        for relation in result.structural_relations
    )


def test_explicit_behavior_scope_calls_behavioral_builder_once(tmp_path: Path) -> None:
    vault, store, reader = _seed(tmp_path)
    _write_definition_and_observation(vault, reader)
    behavioral_result = BuildBehavioralSelfModel(reader, clock=lambda: GROWTH_AT).execute()
    pattern = behavioral_result.patterns[0]
    assert pattern.cohort is not None
    behavioral = _CountingBehavioral(reader)

    result = BuildDecisionCompass(
        reader=reader,
        store=store,
        behavioral=behavioral,
        simulate_me=_CountingSimulateMe(None),
        growth_clock=lambda: GROWTH_AT,
        behavioral_clock=lambda: GROWTH_AT,
        goal_clock=lambda: GROWTH_AT,
    ).execute(
        _request(
            _goal_hash(reader),
            scope=DecisionCompassBehaviorScopeV1(pattern.cohort.cohort_fingerprint),
        ),
        execution=_execution(),
    )

    assert isinstance(result, DecisionCompassResultV1)
    assert behavioral.calls == 1


def test_unwrapped_missing_growth_goal_maps_to_goal_unavailable() -> None:
    class _MissingGrowth:
        def execute(
            self, request: GrowthGoalProgressCompositionRequestV1
        ) -> GrowthGoalProgressCompositionResultV1:
            del request
            raise GrowthGoalMissingError()

    result = BuildDecisionCompass(growth_progress=_MissingGrowth()).execute(
        _request("sha256:" + "a" * 64),
        execution=_execution(),
    )

    assert isinstance(result, DecisionCompassErrorV1)
    assert result.code is DecisionCompassErrorCodeV1.GOAL_UNAVAILABLE


def test_binding_mismatch_is_not_comparable_and_does_not_use_labels(
    tmp_path: Path,
) -> None:
    vault, store, reader = _seed(tmp_path)
    _write_definition_and_observation(vault, reader)
    behavioral = BuildBehavioralSelfModel(reader, clock=lambda: GROWTH_AT).execute()
    pattern = behavioral.patterns[0]
    assert pattern.cohort is not None
    assert pattern.selected_option is not None
    scope = DecisionCompassBehaviorScopeV1(pattern.cohort.cohort_fingerprint)
    binding = DecisionCompassBehaviorOptionBindingV1(
        request_option_id="alpha",
        behavioral_cohort_fingerprint=pattern.cohort.cohort_fingerprint,
        behavioral_option_index=pattern.selected_option.option_index,
        behavioral_option_fingerprint="sha256:" + "0" * 64,
    )
    result = BuildDecisionCompass(
        reader=reader,
        store=store,
        simulate_me=_CountingSimulateMe("alpha"),
        growth_clock=lambda: GROWTH_AT,
        behavioral_clock=lambda: GROWTH_AT,
        goal_clock=lambda: GROWTH_AT,
    ).execute(
        _request(_goal_hash(reader), scope=scope, binding=binding),
        execution=_execution(),
    )
    assert isinstance(result, DecisionCompassResultV1)
    assert (
        any(
            relation.code
            is DecisionCompassStructuralRelationCodeV1.SIMULATE_BEHAVIOR_DIFFERENT_OPTIONS
            for relation in result.structural_relations
        )
        is False
    )
    assert any(
        relation.code is DecisionCompassStructuralRelationCodeV1.SIMULATE_BEHAVIOR_NOT_COMPARABLE
        for relation in result.structural_relations
    )


def test_mixed_behavior_remains_a_valid_typed_result_without_terminal_option(
    tmp_path: Path,
) -> None:
    decisions = (
        ("Alpha", "Situation"),
        ("Alpha", "Situation"),
        ("Beta", "Situation"),
    )
    vault, store, reader = _seed(tmp_path, decisions=decisions)
    _write_definition_and_observation(vault, reader)
    behavioral = BuildBehavioralSelfModel(reader, clock=lambda: GROWTH_AT).execute()
    pattern = behavioral.patterns[0]
    assert pattern.pattern_type is BehavioralPatternTypeV1.MIXED_EXACT_CHOICES
    assert pattern.cohort is not None
    observed = pattern.choice_support[0].option
    binding = DecisionCompassBehaviorOptionBindingV1(
        request_option_id="alpha" if observed.option_index == 0 else "beta",
        behavioral_cohort_fingerprint=pattern.cohort.cohort_fingerprint,
        behavioral_option_index=observed.option_index,
        behavioral_option_fingerprint=observed.option_fingerprint,
    )
    result = BuildDecisionCompass(
        reader=reader,
        store=store,
        simulate_me=_CountingSimulateMe(binding.request_option_id),
        growth_clock=lambda: GROWTH_AT,
        behavioral_clock=lambda: GROWTH_AT,
        goal_clock=lambda: GROWTH_AT,
    ).execute(
        _request(
            _goal_hash(reader),
            scope=DecisionCompassBehaviorScopeV1(pattern.cohort.cohort_fingerprint),
            binding=binding,
        ),
        execution=_execution(),
    )
    assert isinstance(result, DecisionCompassResultV1)
    assert result.behavioral.state is DecisionCompassBehavioralStateV1.RESULT
    assert result.behavioral.pattern is not None
    assert result.behavioral.pattern.selected_option is None
    assert any(
        relation.code is DecisionCompassStructuralRelationCodeV1.SIMULATE_BEHAVIOR_NOT_COMPARABLE
        for relation in result.structural_relations
    )


def test_invalid_simulate_temporal_caveat_is_a_branch_error_without_raw_details(
    tmp_path: Path,
) -> None:
    vault, store, reader = _seed(tmp_path)
    _write_definition_and_observation(vault, reader)
    result = BuildDecisionCompass(
        reader=reader,
        store=store,
        simulate_me=_InvalidTemporalSimulateMe(),
        growth_clock=lambda: GROWTH_AT,
        behavioral_clock=lambda: GROWTH_AT,
        goal_clock=lambda: GROWTH_AT,
    ).execute(_request(_goal_hash(reader)), execution=_execution())
    assert isinstance(result, DecisionCompassResultV1)
    assert result.simulate_me.state is CompareBranchStateV1.ERROR
    assert result.simulate_me.error is not None
    assert result.simulate_me.error.code is CompareBranchErrorCodeV1.RESULT_INVALID
    assert "GOAL_ID" not in result.simulate_me.error.message


def test_current_cohort_is_selected_by_exact_fingerprint_and_other_cohort_is_not_exposed(
    tmp_path: Path,
) -> None:
    decisions = (
        ("Alpha", "Situation A"),
        ("Alpha", "Situation A"),
        ("Alpha", "Situation A"),
        ("Beta", "Situation B"),
        ("Beta", "Situation B"),
        ("Beta", "Situation B"),
    )
    vault, store, reader = _seed(tmp_path, decisions=decisions)
    _write_definition_and_observation(vault, reader)
    behavioral = BuildBehavioralSelfModel(reader, clock=lambda: GROWTH_AT).execute()
    assert len(behavioral.patterns) == 2
    selected = behavioral.patterns[0]
    other = behavioral.patterns[1]
    assert selected.cohort is not None
    assert other.cohort is not None
    result = BuildDecisionCompass(
        reader=reader,
        store=store,
        simulate_me=_CountingSimulateMe(None),
        growth_clock=lambda: GROWTH_AT,
        behavioral_clock=lambda: GROWTH_AT,
    ).execute(
        _request(
            _goal_hash(reader),
            scope=DecisionCompassBehaviorScopeV1(selected.cohort.cohort_fingerprint),
        ),
        execution=_execution(),
    )
    assert isinstance(result, DecisionCompassResultV1)
    behavioral_payload = json.loads(result.to_json())["behavioral"]
    encoded_behavioral = json.dumps(behavioral_payload)
    assert selected.cohort.cohort_fingerprint in encoded_behavioral
    assert other.cohort.cohort_fingerprint not in encoded_behavioral


def test_cancellation_prevents_all_branch_calls(tmp_path: Path) -> None:
    vault, store, reader = _seed(tmp_path)
    _write_definition_and_observation(vault, reader)
    token = CancellationTokenSource()
    token.cancel()
    simulate = _CountingSimulateMe("alpha")
    result = BuildDecisionCompass(
        reader=reader,
        store=store,
        simulate_me=simulate,
        growth_clock=lambda: GROWTH_AT,
    ).execute(_request(_goal_hash(reader)), execution=_execution(token))
    assert isinstance(result, DecisionCompassErrorV1)
    assert result.code is DecisionCompassErrorCodeV1.CANCELLED
    assert simulate.calls == []


def test_deadline_expires_before_source_reads() -> None:
    class _ExplodingReader:
        def scan(self) -> VaultSnapshot:
            raise AssertionError("expired requests must not read sources")

    request = _request("sha256:" + "a" * 64)
    result = BuildDecisionCompass(reader=_ExplodingReader()).execute(
        request,
        execution=CompareExecutionContextV1(
            cancellation=CancellationTokenSource(),
            deadline=0.0,
        ),
    )
    assert isinstance(result, DecisionCompassErrorV1)
    assert result.code is DecisionCompassErrorCodeV1.TIMEOUT


def test_goal_drift_at_final_narrow_revalidation_fails_closed(tmp_path: Path) -> None:
    vault, store, reader = _seed(tmp_path)
    _write_definition_and_observation(vault, reader)
    fingerprint = _goal_hash(reader)
    progress = BuildGrowthGoalProgressCompositionV1(
        reader=reader,
        store=store,
        growth_clock=lambda: GROWTH_AT,
    ).execute(request=GrowthGoalProgressCompositionRequestV1(GOAL_ID, PROGRESS_AS_OF))
    current = BuildGrowthGoalContext(reader, clock=lambda: GROWTH_AT).execute(
        GrowthEngineRequestV1(
            selection=GrowthGoalSelectionV1(GrowthGoalSelectionModeV1.SELECTED_GOAL, GOAL_ID)
        )
    )
    changed_goal = replace(current.goals[0], domain="changed")
    changed_context = replace(current, goals=(changed_goal,))

    class _StaticProgress:
        def execute(self, request: object) -> GrowthGoalProgressCompositionResultV1:
            del request
            return progress

    class _ChangedGoal:
        def execute(self, request: GrowthEngineRequestV1) -> object:
            del request
            return changed_context

    result = BuildDecisionCompass(
        growth_progress=_StaticProgress(),
        goal_context=_ChangedGoal(),
        simulate_me=_CountingSimulateMe(None),
    ).execute(_request(fingerprint), execution=_execution())
    assert isinstance(result, DecisionCompassErrorV1)
    assert result.code is DecisionCompassErrorCodeV1.GOAL_SOURCE_CHANGED


def test_small_valid_result_cap_is_honored_without_truncation(tmp_path: Path) -> None:
    vault, store, reader = _seed(tmp_path)
    _write_definition_and_observation(vault, reader)
    result = BuildDecisionCompass(
        reader=reader,
        store=store,
        growth_clock=lambda: GROWTH_AT,
    ).execute(
        _request(_goal_hash(reader), max_result_bytes=1_000),
        execution=_execution(),
    )
    assert isinstance(result, DecisionCompassErrorV1)
    assert result.code is DecisionCompassErrorCodeV1.RESULT_TOO_LARGE


def test_compare_state_is_preserved_for_simulate_abstention(
    tmp_path: Path,
) -> None:
    vault, store, reader = _seed(tmp_path)
    _write_definition_and_observation(vault, reader)
    simulate = _CountingSimulateMe(None)
    result = BuildDecisionCompass(
        reader=reader,
        store=store,
        simulate_me=simulate,
        growth_clock=lambda: GROWTH_AT,
    ).execute(_request(_goal_hash(reader)), execution=_execution())
    assert isinstance(result, DecisionCompassResultV1)
    assert result.simulate_me.state is CompareBranchStateV1.ABSTENTION


def test_stage12d_result_is_kept_typed_and_not_flattened(tmp_path: Path) -> None:
    vault, store, reader = _seed(tmp_path)
    _write_definition_and_observation(vault, reader)
    result = BuildDecisionCompass(
        reader=reader,
        store=store,
        growth_clock=lambda: GROWTH_AT,
    ).execute(_request(_goal_hash(reader)), execution=_execution())
    assert isinstance(result, DecisionCompassResultV1)
    assert isinstance(result.growth_progress, GrowthGoalProgressCompositionResultV1)
    assert result.growth_progress.progress_as_of == PROGRESS_AS_OF
