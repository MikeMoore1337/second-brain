from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID, uuid7

import pytest

from second_brain.application.adaptive_cognitive_twin import (
    ADAPTIVE_CANDIDATE_POLICY_ID,
    ADAPTIVE_CONTRACT_VERSION,
    ADAPTIVE_EVALUATION_POLICY_ID,
    ADAPTIVE_NON_CAUSAL_LANGUAGE,
    ADAPTIVE_POLICY_FINGERPRINT,
    AdaptiveCognitiveTwinInputError,
    AdaptiveSourceReadinessV1,
    AdaptiveSourceSnapshotV1,
    AdaptiveSufficiencyStateV1,
    GoalSourceSnapshotV1,
    Stage9CalibrationSnapshotV1,
    Stage10BehavioralSnapshotV1,
    Stage12ProgressSnapshotV1,
    Stage14ExperimentSnapshotV1,
    Stage15CaveatV1,
    Stage15EvaluationPlanV1,
    Stage15InteractionModeV1,
    Stage15MeasureV1,
    Stage15ProfileProposalV1,
    Stage15ProjectionFocusV1,
    adaptive_hash_json,
    build_adaptive_profile,
    derive_adaptive_candidate,
    evaluate_adaptive_sufficiency,
    load_stage14_experiment_snapshot,
    validate_adaptive_policy,
)
from second_brain.application.behavioral_self_model import (
    POLICY_FINGERPRINT as BEHAVIORAL_POLICY_FINGERPRINT,
)
from second_brain.application.behavioral_self_model import (
    BehavioralPatternStateV1,
    BehavioralPatternTypeV1,
)
from second_brain.application.goal_progress import GOAL_PROGRESS_POLICY_FINGERPRINT
from second_brain.application.growth import (
    GROWTH_MAPPING_POLICY_FINGERPRINT,
    GROWTH_POLICY_FINGERPRINT,
    GrowthGoalIdentityV1,
    GrowthRelationStateV1,
    growth_hash_json,
)
from second_brain.application.personal_experiments import (
    PERSONAL_EXPERIMENT_POLICY_FINGERPRINT,
    PersonalExperimentDispositionV1,
)
from second_brain.application.personal_experiments_evaluator import (
    PersonalExperimentResultStatusV1,
)
from second_brain.application.prospective_audit import (
    PROSPECTIVE_CALIBRATION_POLICY_FINGERPRINT,
    PROSPECTIVE_CALIBRATION_POLICY_ID,
)

HASH_A = "sha256:" + "a" * 64
HASH_B = "sha256:" + "b" * 64
HASH_C = "sha256:" + "c" * 64
HASH_D = "sha256:" + "d" * 64
NOW = datetime(2026, 9, 15, 12, 0, tzinfo=UTC)


def _goal() -> GrowthGoalIdentityV1:
    return GrowthGoalIdentityV1(
        source_note_uuid=uuid7(),
        dimension="goal",
        source_evidence_kind="user_statement",
        source_self_kind="goal",
        domain="health",
        evidence_at=NOW,
        evidence_at_precision="exact",
        source_contract_version="self-model-v1",
        source_derivation_version="self-model-derivation-v1",
        self_model_policy_fingerprint="e" * 64,
        source_fingerprint=HASH_A,
        claim_fingerprint=HASH_B,
    )


def _source_snapshot(
    goal: GrowthGoalIdentityV1,
    *,
    stage9_readiness: AdaptiveSourceReadinessV1 = AdaptiveSourceReadinessV1.EXACT_CURRENT,
    stage10_readiness: AdaptiveSourceReadinessV1 = AdaptiveSourceReadinessV1.EXACT_CURRENT,
    stage10_state: GrowthRelationStateV1 = GrowthRelationStateV1.SUPPORTS_GOAL,
    stage12_readiness: AdaptiveSourceReadinessV1 = AdaptiveSourceReadinessV1.EXACT_CURRENT,
    stage12_status: str = "target_met",
    stage14_readiness: AdaptiveSourceReadinessV1 = AdaptiveSourceReadinessV1.EXACT_CURRENT,
    stage14_disposition: PersonalExperimentDispositionV1 = PersonalExperimentDispositionV1.STOP,
) -> AdaptiveSourceSnapshotV1:
    goal_fingerprint = growth_hash_json(goal.as_dict())
    goal_source = GoalSourceSnapshotV1(
        readiness=AdaptiveSourceReadinessV1.EXACT_CURRENT,
        goal_source_uuid=goal.source_note_uuid,
        goal_identity_fingerprint=goal_fingerprint,
        growth_policy_fingerprint=GROWTH_POLICY_FINGERPRINT,
        source_fingerprint=goal.source_fingerprint,
    )
    stage9 = Stage9CalibrationSnapshotV1(
        readiness=stage9_readiness,
        generation_id=uuid7()
        if stage9_readiness is not AdaptiveSourceReadinessV1.SOURCE_MISSING
        else None,
        as_of=NOW,
        result_fingerprint=HASH_C
        if stage9_readiness is not AdaptiveSourceReadinessV1.SOURCE_MISSING
        else None,
        policy_id=PROSPECTIVE_CALIBRATION_POLICY_ID
        if stage9_readiness is not AdaptiveSourceReadinessV1.SOURCE_MISSING
        else None,
        policy_fingerprint=PROSPECTIVE_CALIBRATION_POLICY_FINGERPRINT
        if stage9_readiness is not AdaptiveSourceReadinessV1.SOURCE_MISSING
        else None,
        audited_operations=2
        if stage9_readiness is not AdaptiveSourceReadinessV1.SOURCE_MISSING
        else None,
        linked_actual_decisions=1
        if stage9_readiness is not AdaptiveSourceReadinessV1.SOURCE_MISSING
        else None,
        evaluated_predictions=1
        if stage9_readiness is not AdaptiveSourceReadinessV1.SOURCE_MISSING
        else None,
    )
    stage10 = Stage10BehavioralSnapshotV1(
        readiness=stage10_readiness,
        goal_source_uuid=goal.source_note_uuid,
        goal_identity_fingerprint=goal_fingerprint,
        growth_policy_fingerprint=GROWTH_POLICY_FINGERPRINT,
        behavioral_policy_fingerprint=BEHAVIORAL_POLICY_FINGERPRINT,
        cohort_fingerprint=HASH_A,
        pattern_fingerprint=HASH_B,
        behavioral_source_fingerprint=HASH_C,
        behavioral_provenance_fingerprint=HASH_D,
        pattern_type=BehavioralPatternTypeV1.REPEATED_EXACT_CHOICE,
        pattern_state=BehavioralPatternStateV1.CURRENT,
        mapping_id=uuid7(),
        mapping_fingerprint=HASH_D,
        mapping_policy_fingerprint=GROWTH_MAPPING_POLICY_FINGERPRINT,
        relation_state=stage10_state,
    )
    stage12 = Stage12ProgressSnapshotV1(
        readiness=stage12_readiness,
        goal_source_uuid=goal.source_note_uuid,
        goal_identity_fingerprint=goal_fingerprint,
        progress_result_fingerprint=HASH_A,
        progress_policy_fingerprint=GOAL_PROGRESS_POLICY_FINGERPRINT,
        progress_as_of=NOW,
        definition_id=uuid7(),
        definition_fingerprint=HASH_B,
        status=stage12_status,
    )
    stage14 = Stage14ExperimentSnapshotV1(
        readiness=stage14_readiness,
        goal_source_uuid=goal.source_note_uuid,
        goal_identity_fingerprint=goal_fingerprint,
        experiment_definition_id=uuid7(),
        experiment_definition_fingerprint=HASH_A,
        terminal_result_fingerprint=HASH_B,
        terminal_result_as_of=NOW,
        terminal_status=PersonalExperimentResultStatusV1.OBSERVED_TOWARD_TARGET,
        experiment_policy_fingerprint=PERSONAL_EXPERIMENT_POLICY_FINGERPRINT,
        reassessment_id=uuid7(),
        reassessment_fingerprint=HASH_C,
        reassessment_evaluation_policy_fingerprint=PERSONAL_EXPERIMENT_POLICY_FINGERPRINT,
        disposition=stage14_disposition,
        reassessment_reviewed_at=NOW,
    )
    return AdaptiveSourceSnapshotV1(
        contract_version=ADAPTIVE_CONTRACT_VERSION,
        snapshot_version="1",
        goal=goal_source,
        stage9_calibration=stage9,
        stage10_behavioral=stage10,
        stage12_progress=stage12,
        stage14_experiment=stage14,
        as_of=NOW,
    )


def test_policy_fingerprint_and_evaluation_plan_are_closed() -> None:
    assert validate_adaptive_policy() == ADAPTIVE_POLICY_FINGERPRINT
    plan = Stage15EvaluationPlanV1(
        later_source_required=True,
        explicit_as_of_required=True,
        baseline_source_snapshot_fingerprint=HASH_A,
        measures=(Stage15MeasureV1.PROGRESS_STATE,),
        evaluation_policy_id=ADAPTIVE_EVALUATION_POLICY_ID,
        evaluation_policy_fingerprint=ADAPTIVE_POLICY_FINGERPRINT,
        non_causal_wording_id=ADAPTIVE_NON_CAUSAL_LANGUAGE,
    )
    assert plan.as_dict()["measures"] == ["progress_state"]
    with pytest.raises(AdaptiveCognitiveTwinInputError):
        Stage15EvaluationPlanV1(
            later_source_required=False,
            explicit_as_of_required=True,
            baseline_source_snapshot_fingerprint=HASH_A,
            measures=(Stage15MeasureV1.PROGRESS_STATE,),
            evaluation_policy_id=ADAPTIVE_EVALUATION_POLICY_ID,
            evaluation_policy_fingerprint=ADAPTIVE_POLICY_FINGERPRINT,
            non_causal_wording_id=ADAPTIVE_NON_CAUSAL_LANGUAGE,
        )


def test_source_pack_and_candidate_are_deterministic_for_one_exact_snapshot() -> None:
    snapshot = _source_snapshot(_goal())
    first = derive_adaptive_candidate(snapshot, as_of=NOW)
    second = derive_adaptive_candidate(snapshot, as_of=NOW)

    assert snapshot.to_json() == snapshot.to_json()
    assert first.to_json() == second.to_json()
    assert first.candidate_status is AdaptiveSufficiencyStateV1.CANDIDATE
    assert first.proposed_profile is not None
    assert first.proposed_profile.projection_focus is Stage15ProjectionFocusV1.PROGRESS_CONTEXT
    assert first.evaluation_plan.measure is Stage15MeasureV1.PROGRESS_STATE
    assert first.caveats == (
        Stage15CaveatV1.EXACT_SOURCE_SNAPSHOT,
        Stage15CaveatV1.OWNER_REVIEW_REQUIRED,
        Stage15CaveatV1.NO_AUTOMATIC_ACTIVATION,
        Stage15CaveatV1.DESCRIPTIVE_NON_CAUSAL,
    )
    assert ADAPTIVE_CANDIDATE_POLICY_ID == "adaptive-candidate-exact-source-pack-v1"


def test_missing_source_wins_before_closed_rules() -> None:
    snapshot = _source_snapshot(
        _goal(),
        stage9_readiness=AdaptiveSourceReadinessV1.SOURCE_MISSING,
    )
    decision = evaluate_adaptive_sufficiency(snapshot, safe_delta=True)
    candidate = derive_adaptive_candidate(snapshot, as_of=NOW)

    assert decision.state is AdaptiveSufficiencyStateV1.INSUFFICIENT
    assert candidate.candidate_status is AdaptiveSufficiencyStateV1.INSUFFICIENT
    assert candidate.proposed_profile is None


def test_source_changed_wins_over_missing_rule_and_never_proposes() -> None:
    snapshot = _source_snapshot(
        _goal(),
        stage12_readiness=AdaptiveSourceReadinessV1.SOURCE_CHANGED,
    )
    candidate = derive_adaptive_candidate(snapshot, as_of=NOW)

    assert candidate.candidate_status is AdaptiveSufficiencyStateV1.SOURCE_CHANGED
    assert candidate.proposed_profile is None


def test_opposing_exact_signals_hold_without_profile() -> None:
    snapshot = _source_snapshot(
        _goal(),
        stage10_state=GrowthRelationStateV1.SUPPORTS_GOAL,
        stage12_status="away_from_target",
    )
    candidate = derive_adaptive_candidate(snapshot, as_of=NOW)

    assert candidate.candidate_status is AdaptiveSufficiencyStateV1.HOLD
    assert candidate.proposed_profile is None
    reasons = candidate.as_dict()["reasons"]
    assert isinstance(reasons, list)
    assert "sources_conflict" in reasons


def test_active_profile_requires_same_exact_goal_and_same_shape_is_noop() -> None:
    goal = _goal()
    snapshot = _source_snapshot(goal)
    initial = derive_adaptive_candidate(snapshot, as_of=NOW)
    assert initial.proposed_profile is not None
    active = build_adaptive_profile(initial.proposed_profile, profile_id=uuid7())

    unchanged = derive_adaptive_candidate(snapshot, as_of=NOW, active_profile=active)
    assert unchanged.candidate_status is AdaptiveSufficiencyStateV1.HOLD
    assert unchanged.proposed_profile is None

    other_snapshot = _source_snapshot(_goal())
    with pytest.raises(AdaptiveCognitiveTwinInputError):
        derive_adaptive_candidate(other_snapshot, as_of=NOW, active_profile=active)


def test_stage14_without_explicit_selector_is_source_missing() -> None:
    result = load_stage14_experiment_snapshot(
        None,
        result=None,
        reassessment=None,
        goal=_goal(),
        as_of=NOW,
    )
    assert result.readiness is AdaptiveSourceReadinessV1.SOURCE_MISSING


def test_profile_and_candidate_fingerprints_exclude_runtime_identity() -> None:
    proposal = Stage15ProfileProposalV1(
        contract_version=ADAPTIVE_CONTRACT_VERSION,
        profile_version="1",
        goal_source_uuid=UUID("0199c000-0000-7000-8000-000000000001"),
        goal_identity_fingerprint=HASH_A,
        source_snapshot_fingerprint=HASH_B,
        projection_focus=Stage15ProjectionFocusV1.CALIBRATION_CONTEXT,
        interaction_mode=Stage15InteractionModeV1.BALANCED_EVIDENCE,
        evaluation_measure=Stage15MeasureV1.CALIBRATION_LINKAGE,
        profile_policy_id="stage15-adaptive-profile-v1",
        profile_policy_fingerprint=ADAPTIVE_POLICY_FINGERPRINT,
    )
    first = build_adaptive_profile(
        proposal,
        profile_id=UUID("0199c000-0000-7000-8000-000000000002"),
    )
    second = build_adaptive_profile(
        proposal,
        profile_id=UUID("0199c000-0000-7000-8000-000000000003"),
    )
    assert proposal.profile_fingerprint == adaptive_hash_json(proposal.fingerprint_payload())
    assert first.profile_shape_fingerprint == second.profile_shape_fingerprint
    assert first.profile_fingerprint != second.profile_fingerprint
