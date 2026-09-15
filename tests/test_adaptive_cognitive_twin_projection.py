from __future__ import annotations

from dataclasses import replace
from datetime import timedelta
from typing import Any, cast
from uuid import uuid7

import pytest

from second_brain.application.adaptive_cognitive_twin import (
    ADAPTIVE_EVALUATION_POLICY_ID,
    ADAPTIVE_NON_CAUSAL_LANGUAGE,
    ADAPTIVE_POLICY_FINGERPRINT,
    AdaptiveCognitiveTwinInputError,
    AdaptiveSourceReadinessV1,
    AdaptiveSourceSnapshotV1,
    Stage15CaveatV1,
    Stage15EvaluationPlanV1,
    adaptive_hash_json,
    build_adaptive_profile,
    derive_adaptive_candidate,
)
from second_brain.application.adaptive_cognitive_twin_projection import (
    ADAPTIVE_NON_CAUSAL_PHRASE,
    Stage15EvaluationStateV1,
    Stage15ProfileFieldV1,
    Stage15ProfileValidityV1,
    Stage15ProjectionLifecycleStateV1,
    Stage15SourceFamilyV1,
    evaluate_adaptive_profile,
    project_adaptive_cognitive_twin,
    validate_adaptive_evaluation_result,
    validate_adaptive_projection,
)
from second_brain.application.goal_progress_read import GoalProgressStatusV1
from tests.test_adaptive_cognitive_twin import HASH_D, NOW, _goal, _source_snapshot


def _with_snapshot(
    snapshot: AdaptiveSourceSnapshotV1,
    **changes: object,
) -> AdaptiveSourceSnapshotV1:
    return replace(
        snapshot,
        **cast(Any, {**changes, "source_snapshot_fingerprint": ""}),
    )


def _with_stage12(
    snapshot: AdaptiveSourceSnapshotV1,
    *,
    readiness: AdaptiveSourceReadinessV1 | None = None,
    status: GoalProgressStatusV1 | None = None,
    fingerprint: str | None = None,
) -> AdaptiveSourceSnapshotV1:
    stage12 = snapshot.stage12_progress
    updated = replace(
        stage12,
        readiness=readiness if readiness is not None else stage12.readiness,
        status=status if status is not None else stage12.status,
        progress_result_fingerprint=(
            fingerprint if fingerprint is not None else stage12.progress_result_fingerprint
        ),
    )
    return _with_snapshot(snapshot, stage12_progress=updated)


def test_projection_is_deterministic_and_discloses_four_source_families() -> None:
    snapshot = _source_snapshot(_goal())

    first = project_adaptive_cognitive_twin(snapshot, as_of=NOW)
    second = project_adaptive_cognitive_twin(snapshot, as_of=NOW)

    assert first.to_json() == second.to_json()
    assert first.lifecycle_state is Stage15ProjectionLifecycleStateV1.CANDIDATE_PENDING_REVIEW
    assert first.profile_validity is Stage15ProfileValidityV1.NONE
    assert tuple(item.family for item in first.source_readiness) == (
        Stage15SourceFamilyV1.STAGE9_CALIBRATION,
        Stage15SourceFamilyV1.STAGE10_BEHAVIORAL,
        Stage15SourceFamilyV1.STAGE12_PROGRESS,
        Stage15SourceFamilyV1.STAGE14_EXPERIMENT,
    )
    assert all(
        item.readiness is AdaptiveSourceReadinessV1.EXACT_CURRENT for item in first.source_readiness
    )
    assert first.profile_diff is not None
    assert set(first.profile_diff.changed_fields) == {
        Stage15ProfileFieldV1.PROJECTION_FOCUS,
        Stage15ProfileFieldV1.INTERACTION_MODE,
        Stage15ProfileFieldV1.EVALUATION_MEASURE,
        Stage15ProfileFieldV1.SOURCE_SNAPSHOT_FINGERPRINT,
    }
    assert first.candidate.proposed_profile is not None
    assert first.to_json().encode("utf-8")
    assert validate_adaptive_projection(first) is first


def test_projection_marks_same_active_profile_valid_and_diff_free() -> None:
    snapshot = _source_snapshot(_goal())
    candidate = derive_adaptive_candidate(snapshot, as_of=NOW)
    assert candidate.proposed_profile is not None
    active = build_adaptive_profile(candidate.proposed_profile, profile_id=uuid7())

    projection = project_adaptive_cognitive_twin(
        snapshot,
        as_of=NOW,
        active_profile=active,
    )

    assert projection.lifecycle_state is Stage15ProjectionLifecycleStateV1.ACTIVE_PROFILE_VALID
    assert projection.profile_validity is Stage15ProfileValidityV1.VALID
    assert projection.candidate.candidate_status.value == "hold"
    assert projection.profile_diff is None
    assert projection.active_profile == active


def test_projection_marks_active_profile_stale_after_source_change() -> None:
    snapshot = _source_snapshot(_goal())
    candidate = derive_adaptive_candidate(snapshot, as_of=NOW)
    assert candidate.proposed_profile is not None
    active = build_adaptive_profile(candidate.proposed_profile, profile_id=uuid7())
    later = _with_stage12(snapshot, fingerprint=HASH_D)

    projection = project_adaptive_cognitive_twin(
        later,
        as_of=NOW,
        active_profile=active,
    )

    assert projection.lifecycle_state is Stage15ProjectionLifecycleStateV1.ACTIVE_PROFILE_STALE
    assert projection.profile_validity is Stage15ProfileValidityV1.STALE
    assert projection.source_snapshot_fingerprint != active.source_snapshot_fingerprint
    assert projection.candidate.prior_profile_id == active.profile_id


def test_projection_rejects_cross_goal_active_profile() -> None:
    snapshot = _source_snapshot(_goal())
    other = _source_snapshot(_goal())
    candidate = derive_adaptive_candidate(snapshot, as_of=NOW)
    assert candidate.proposed_profile is not None
    active = build_adaptive_profile(candidate.proposed_profile, profile_id=uuid7())

    with pytest.raises(AdaptiveCognitiveTwinInputError):
        project_adaptive_cognitive_twin(other, as_of=NOW, active_profile=active)


def test_evaluation_same_pack_is_explicit_and_descriptive() -> None:
    snapshot = _source_snapshot(_goal())
    candidate = derive_adaptive_candidate(snapshot, as_of=NOW)
    assert candidate.proposed_profile is not None
    active = build_adaptive_profile(candidate.proposed_profile, profile_id=uuid7())
    plan = candidate.evaluation_plan

    result = evaluate_adaptive_profile(
        active,
        later_snapshot=snapshot,
        as_of=NOW,
        activation_source_snapshot_fingerprint=active.source_snapshot_fingerprint,
        evaluation_plan=plan,
    )

    assert result.state is Stage15EvaluationStateV1.EVALUATED
    assert result.changed_sources == ()
    assert result.unchanged_sources == (Stage15SourceFamilyV1.SOURCE_PACK,)
    assert result.caveats == (
        Stage15CaveatV1.EXACT_SOURCE_SNAPSHOT,
        Stage15CaveatV1.OWNER_REVIEW_REQUIRED,
        Stage15CaveatV1.NO_AUTOMATIC_ACTIVATION,
        Stage15CaveatV1.DESCRIPTIVE_NON_CAUSAL,
    )
    assert result.non_causal_phrase == ADAPTIVE_NON_CAUSAL_PHRASE
    assert "success" not in result.as_dict()
    assert "failure" not in result.as_dict()
    assert "reward" not in result.as_dict()
    assert "confidence" not in result.as_dict()
    assert validate_adaptive_evaluation_result(result) is result


def test_evaluation_reports_aggregate_drift_without_claiming_causality() -> None:
    snapshot = _source_snapshot(_goal())
    candidate = derive_adaptive_candidate(snapshot, as_of=NOW)
    assert candidate.proposed_profile is not None
    active = build_adaptive_profile(candidate.proposed_profile, profile_id=uuid7())
    later = _with_stage12(snapshot, fingerprint=HASH_D)

    result = evaluate_adaptive_profile(
        active,
        later_snapshot=later,
        as_of=NOW,
        activation_source_snapshot_fingerprint=active.source_snapshot_fingerprint,
        evaluation_plan=candidate.evaluation_plan,
    )

    assert result.state is Stage15EvaluationStateV1.EVALUATED
    assert result.changed_sources == (Stage15SourceFamilyV1.SOURCE_PACK,)
    assert result.unchanged_sources == ()
    assert result.later_snapshot_fingerprint == later.source_snapshot_fingerprint
    assert ADAPTIVE_NON_CAUSAL_PHRASE not in result.to_json()


@pytest.mark.parametrize(
    ("field", "expected"),
    (
        ("stage9_calibration", Stage15EvaluationStateV1.INSUFFICIENT),
        ("stage10_behavioral", Stage15EvaluationStateV1.NOT_COMPARABLE),
        ("stage12_progress", Stage15EvaluationStateV1.SOURCE_CHANGED),
    ),
)
def test_evaluation_fails_closed_for_later_readiness(
    field: str,
    expected: Stage15EvaluationStateV1,
) -> None:
    snapshot = _source_snapshot(_goal())
    candidate = derive_adaptive_candidate(snapshot, as_of=NOW)
    assert candidate.proposed_profile is not None
    active = build_adaptive_profile(candidate.proposed_profile, profile_id=uuid7())
    if field == "stage9_calibration":
        later = _with_snapshot(
            snapshot,
            stage9_calibration=replace(
                snapshot.stage9_calibration,
                readiness=AdaptiveSourceReadinessV1.SOURCE_MISSING,
                generation_id=None,
                result_fingerprint=None,
                policy_id=None,
                policy_fingerprint=None,
                audited_operations=None,
                linked_actual_decisions=None,
                evaluated_predictions=None,
            ),
        )
    elif field == "stage10_behavioral":
        later = _with_snapshot(
            snapshot,
            stage10_behavioral=replace(
                snapshot.stage10_behavioral,
                readiness=AdaptiveSourceReadinessV1.NOT_COMPARABLE,
            ),
        )
    else:
        later = _with_stage12(snapshot, readiness=AdaptiveSourceReadinessV1.SOURCE_CHANGED)

    result = evaluate_adaptive_profile(
        active,
        later_snapshot=later,
        as_of=NOW,
        activation_source_snapshot_fingerprint=active.source_snapshot_fingerprint,
        evaluation_plan=candidate.evaluation_plan,
    )

    assert result.state is expected


def test_evaluation_policy_mismatch_and_cross_goal_are_not_comparable_or_actionable() -> None:
    snapshot = _source_snapshot(_goal())
    candidate = derive_adaptive_candidate(snapshot, as_of=NOW)
    assert candidate.proposed_profile is not None
    active = build_adaptive_profile(candidate.proposed_profile, profile_id=uuid7())
    policy_mismatch = _with_snapshot(
        snapshot,
        stage9_calibration=replace(
            snapshot.stage9_calibration,
            readiness=AdaptiveSourceReadinessV1.POLICY_MISMATCH,
        ),
    )
    policy_result = evaluate_adaptive_profile(
        active,
        later_snapshot=policy_mismatch,
        as_of=NOW,
        activation_source_snapshot_fingerprint=active.source_snapshot_fingerprint,
        evaluation_plan=candidate.evaluation_plan,
    )
    assert policy_result.state is Stage15EvaluationStateV1.POLICY_MISMATCH

    other_goal_result = evaluate_adaptive_profile(
        active,
        later_snapshot=_source_snapshot(_goal()),
        as_of=NOW,
        activation_source_snapshot_fingerprint=active.source_snapshot_fingerprint,
        evaluation_plan=candidate.evaluation_plan,
    )
    assert other_goal_result.state is Stage15EvaluationStateV1.NOT_COMPARABLE


def test_evaluation_requires_exact_activation_binding_and_cutoff() -> None:
    snapshot = _source_snapshot(_goal())
    candidate = derive_adaptive_candidate(snapshot, as_of=NOW)
    assert candidate.proposed_profile is not None
    active = build_adaptive_profile(candidate.proposed_profile, profile_id=uuid7())
    wrong_plan = Stage15EvaluationPlanV1(
        later_source_required=True,
        explicit_as_of_required=True,
        baseline_source_snapshot_fingerprint=adaptive_hash_json({"wrong": True}),
        measures=candidate.evaluation_plan.measures,
        evaluation_policy_id=ADAPTIVE_EVALUATION_POLICY_ID,
        evaluation_policy_fingerprint=ADAPTIVE_POLICY_FINGERPRINT,
        non_causal_wording_id=ADAPTIVE_NON_CAUSAL_LANGUAGE,
    )

    with pytest.raises(AdaptiveCognitiveTwinInputError):
        evaluate_adaptive_profile(
            active,
            later_snapshot=snapshot,
            as_of=NOW,
            activation_source_snapshot_fingerprint=active.source_snapshot_fingerprint,
            evaluation_plan=wrong_plan,
        )
    with pytest.raises(AdaptiveCognitiveTwinInputError):
        evaluate_adaptive_profile(
            active,
            later_snapshot=_with_snapshot(
                snapshot,
                as_of=NOW + timedelta(seconds=1),
            ),
            as_of=NOW,
            activation_source_snapshot_fingerprint=active.source_snapshot_fingerprint,
            evaluation_plan=candidate.evaluation_plan,
        )
