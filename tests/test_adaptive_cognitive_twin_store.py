from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid7

import pytest

from second_brain.application.adaptive_cognitive_twin import (
    ADAPTIVE_CONTRACT_VERSION,
    AdaptiveSourceReadinessV1,
    AdaptiveSourceSnapshotV1,
    GoalSourceSnapshotV1,
    Stage9CalibrationSnapshotV1,
    Stage10BehavioralSnapshotV1,
    Stage12ProgressSnapshotV1,
    Stage14ExperimentSnapshotV1,
    derive_adaptive_candidate,
)
from second_brain.application.adaptive_cognitive_twin_store import (
    ADAPTIVE_PROFILE_STORE_MAX_PROFILE_RECORDS_PER_GOAL,
    AdaptiveCognitiveTwinStore,
    AdaptiveCognitiveTwinStoreCandidateNotReviewedError,
    AdaptiveCognitiveTwinStoreCorruptError,
    AdaptiveCognitiveTwinStoreIdempotencyConflictError,
    AdaptiveCognitiveTwinStoreProfileNotFoundError,
    AdaptiveCognitiveTwinStoreSourceChangedError,
    AdaptiveCognitiveTwinStoreStateConflictError,
    AdaptiveCognitiveTwinStoreUnavailableError,
    AdaptiveStoreEventTypeV1,
    derive_adaptive_cognitive_twin_store_root,
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
    stage9_evaluated: int = 1,
    stage10_state: GrowthRelationStateV1 = GrowthRelationStateV1.SUPPORTS_GOAL,
    stage12_status: str = "target_met",
) -> AdaptiveSourceSnapshotV1:
    goal_fingerprint = growth_hash_json(goal.as_dict())
    return AdaptiveSourceSnapshotV1(
        contract_version=ADAPTIVE_CONTRACT_VERSION,
        snapshot_version="1",
        goal=GoalSourceSnapshotV1(
            readiness=AdaptiveSourceReadinessV1.EXACT_CURRENT,
            goal_source_uuid=goal.source_note_uuid,
            goal_identity_fingerprint=goal_fingerprint,
            growth_policy_fingerprint=GROWTH_POLICY_FINGERPRINT,
            source_fingerprint=goal.source_fingerprint,
        ),
        stage9_calibration=Stage9CalibrationSnapshotV1(
            readiness=AdaptiveSourceReadinessV1.EXACT_CURRENT,
            generation_id=uuid7(),
            as_of=NOW,
            result_fingerprint=HASH_C,
            policy_id=PROSPECTIVE_CALIBRATION_POLICY_ID,
            policy_fingerprint=PROSPECTIVE_CALIBRATION_POLICY_FINGERPRINT,
            audited_operations=2,
            linked_actual_decisions=1,
            evaluated_predictions=stage9_evaluated,
        ),
        stage10_behavioral=Stage10BehavioralSnapshotV1(
            readiness=AdaptiveSourceReadinessV1.EXACT_CURRENT,
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
        ),
        stage12_progress=Stage12ProgressSnapshotV1(
            readiness=AdaptiveSourceReadinessV1.EXACT_CURRENT,
            goal_source_uuid=goal.source_note_uuid,
            goal_identity_fingerprint=goal_fingerprint,
            progress_result_fingerprint=HASH_A,
            progress_policy_fingerprint=GOAL_PROGRESS_POLICY_FINGERPRINT,
            progress_as_of=NOW,
            definition_id=uuid7(),
            definition_fingerprint=HASH_B,
            status=stage12_status,
        ),
        stage14_experiment=Stage14ExperimentSnapshotV1(
            readiness=AdaptiveSourceReadinessV1.EXACT_CURRENT,
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
            disposition=PersonalExperimentDispositionV1.STOP,
            reassessment_reviewed_at=NOW,
        ),
        as_of=NOW,
    )


def _store(tmp_path: Path) -> AdaptiveCognitiveTwinStore:
    tmp_path.mkdir(parents=True, exist_ok=True)
    vault = tmp_path / "vault"
    vault.mkdir(parents=True)
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    return AdaptiveCognitiveTwinStore(runtime / "adaptive", vault_root=vault)


def test_store_initializes_outside_vault_with_verified_empty_generation(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)

    assert store.records_path.name == "profiles.jsonl"
    assert store.manifest_path.name == "manifest.json"
    assert store.lock_path.name == ".store.lock"
    assert store.read_events() == ()
    assert store.read_state().active_profiles == ()
    assert store.manifest.record_count == 0
    assert store.records_path.read_bytes() == b""


def test_store_root_is_derived_only_from_explicit_runtime_env_file(tmp_path: Path) -> None:
    runtime = tmp_path / "runtime"
    env_file = runtime / "web.env"
    (runtime / "prospective-audit").mkdir(parents=True)
    env_file.write_text("VAULT_ROOT=/srv/second-brain-vault\n", encoding="utf-8")

    root = derive_adaptive_cognitive_twin_store_root(env_file)
    assert root == runtime / "prospective-audit" / "adaptive-cognitive-twin"
    assert derive_adaptive_cognitive_twin_store_root(None) is None
    assert derive_adaptive_cognitive_twin_store_root(runtime / "missing.env") is None


def test_review_reject_and_activation_require_explicit_state_machine(tmp_path: Path) -> None:
    store = _store(tmp_path)
    goal = _goal()
    source = _source_snapshot(goal)
    candidate = derive_adaptive_candidate(source, as_of=NOW)

    with pytest.raises(AdaptiveCognitiveTwinStoreCandidateNotReviewedError):
        store.activate_candidate(candidate, source, operation_id="activate-before-review")

    reviewed = store.review_candidate(candidate, operation_id="review-1")
    assert reviewed.record.event_type is AdaptiveStoreEventTypeV1.CANDIDATE_REVIEWED
    assert store.review_candidate(candidate, operation_id="review-1") == reviewed
    different_candidate = derive_adaptive_candidate(
        _source_snapshot(
            goal,
            stage9_evaluated=0,
            stage10_state=GrowthRelationStateV1.CONFLICTS_WITH_GOAL,
            stage12_status="away_from_target",
        ),
        as_of=NOW,
    )
    with pytest.raises(AdaptiveCognitiveTwinStoreIdempotencyConflictError):
        store.review_candidate(different_candidate, operation_id="review-1")
    with pytest.raises(AdaptiveCognitiveTwinStoreStateConflictError):
        store.review_candidate(candidate, operation_id="review-1-different")

    activated = store.activate_candidate(candidate, source, operation_id="activate-1")
    assert activated.record.event_type is AdaptiveStoreEventTypeV1.PROFILE_ACTIVATED
    assert activated.record.profile is not None
    assert store.activate_candidate(candidate, source, operation_id="activate-1") == activated
    with pytest.raises(AdaptiveCognitiveTwinStoreStateConflictError):
        store.activate_candidate(candidate, source, operation_id="activate-2")
    assert len(store.read_state().active_profiles) == 1


def test_rejection_is_append_only_and_cannot_be_activated(tmp_path: Path) -> None:
    store = _store(tmp_path)
    goal = _goal()
    source = _source_snapshot(goal)
    candidate = derive_adaptive_candidate(source, as_of=NOW)

    store.review_candidate(candidate, operation_id="review-reject")
    rejected = store.reject_candidate(candidate, operation_id="reject-1")
    assert rejected.record.event_type is AdaptiveStoreEventTypeV1.CANDIDATE_REJECTED
    with pytest.raises(AdaptiveCognitiveTwinStoreStateConflictError):
        store.activate_candidate(candidate, source, operation_id="activate-rejected")
    with pytest.raises(AdaptiveCognitiveTwinStoreStateConflictError):
        store.reject_candidate(candidate, operation_id="reject-2")
    assert store.read_state().active_profiles == ()


def test_activation_revalidates_exact_source_and_supersede_is_exact(tmp_path: Path) -> None:
    store = _store(tmp_path)
    goal = _goal()
    source = _source_snapshot(goal)
    candidate = derive_adaptive_candidate(source, as_of=NOW)
    store.review_candidate(candidate, operation_id="review-initial")

    changed_source = _source_snapshot(
        goal,
        stage9_evaluated=0,
        stage10_state=GrowthRelationStateV1.CONFLICTS_WITH_GOAL,
        stage12_status="away_from_target",
    )
    with pytest.raises(AdaptiveCognitiveTwinStoreSourceChangedError):
        store.activate_candidate(candidate, changed_source, operation_id="activate-stale")

    first = store.activate_candidate(candidate, source, operation_id="activate-initial")
    assert first.record.profile is not None
    active = first.record.profile
    next_candidate = derive_adaptive_candidate(
        changed_source,
        as_of=NOW,
        active_profile=active,
    )
    assert next_candidate.proposed_profile is not None
    store.review_candidate(next_candidate, operation_id="review-next")
    second = store.supersede_candidate(
        next_candidate,
        changed_source,
        operation_id="supersede-1",
    )
    assert second.record.event_type is AdaptiveStoreEventTypeV1.PROFILE_SUPERSEDED
    assert second.record.previous_profile_id == active.profile_id
    assert (
        store.supersede_candidate(
            next_candidate,
            changed_source,
            operation_id="supersede-1",
        )
        == second
    )
    assert (
        store.active_profile(
            goal.source_note_uuid,
            next_candidate.goal_identity_fingerprint,
        )
        == second.record.profile
    )


def test_revert_and_evaluation_keep_history_without_deleting_records(tmp_path: Path) -> None:
    store = _store(tmp_path)
    goal = _goal()
    source = _source_snapshot(goal)
    candidate = derive_adaptive_candidate(source, as_of=NOW)
    store.review_candidate(candidate, operation_id="review-activate")
    activation = store.activate_candidate(candidate, source, operation_id="activate")
    assert activation.record.profile is not None
    active = activation.record.profile

    later_source = _source_snapshot(
        goal,
        stage9_evaluated=0,
        stage10_state=GrowthRelationStateV1.CONFLICTS_WITH_GOAL,
        stage12_status="away_from_target",
    )
    next_candidate = derive_adaptive_candidate(later_source, as_of=NOW, active_profile=active)
    store.review_candidate(next_candidate, operation_id="review-supersede")
    superseded = store.supersede_candidate(
        next_candidate,
        later_source,
        operation_id="supersede",
    )
    assert superseded.record.profile is not None
    current = superseded.record.profile
    evaluation = store.record_evaluation(current, HASH_D, operation_id="evaluation-1")
    assert evaluation.record.event_type is AdaptiveStoreEventTypeV1.EVALUATION_RECORDED
    assert store.record_evaluation(current, HASH_D, operation_id="evaluation-1") == evaluation

    reverted = store.revert_profile(
        goal_source_uuid=goal.source_note_uuid,
        goal_identity_fingerprint=candidate.goal_identity_fingerprint,
        target_profile_id=active.profile_id,
        target_profile_fingerprint=active.profile_fingerprint,
        operation_id="revert-1",
    )
    assert reverted.record.event_type is AdaptiveStoreEventTypeV1.PROFILE_REVERTED
    assert (
        store.revert_profile(
            goal_source_uuid=goal.source_note_uuid,
            goal_identity_fingerprint=candidate.goal_identity_fingerprint,
            target_profile_id=active.profile_id,
            target_profile_fingerprint=active.profile_fingerprint,
            operation_id="revert-1",
        )
        == reverted
    )
    with pytest.raises(AdaptiveCognitiveTwinStoreProfileNotFoundError):
        store.record_evaluation(current, HASH_C, operation_id="evaluation-old-profile")
    assert len(store.read_state().profile_history) == 2
    assert len(store.read_events()) == 6


def test_activation_is_idempotent_under_concurrent_same_operation(tmp_path: Path) -> None:
    store = _store(tmp_path)
    goal = _goal()
    source = _source_snapshot(goal)
    candidate = derive_adaptive_candidate(source, as_of=NOW)
    store.review_candidate(candidate, operation_id="review-concurrent")

    def activate() -> object:
        return store.activate_candidate(candidate, source, operation_id="activate-concurrent")

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda _item: activate(), range(2)))
    assert results[0] == results[1]
    assert len(store.read_state().active_profiles) == 1
    assert len(store.read_events()) == 2


def test_store_detects_torn_append_digest_and_manifest_tampering(tmp_path: Path) -> None:
    store = _store(tmp_path / "torn")
    goal = _goal()
    source = _source_snapshot(goal)
    candidate = derive_adaptive_candidate(source, as_of=NOW)
    store.review_candidate(candidate, operation_id="review-tamper")
    raw = store.records_path.read_bytes()
    store.records_path.write_bytes(raw[:-1])
    with pytest.raises(AdaptiveCognitiveTwinStoreCorruptError):
        store.read_events()

    other = _store(tmp_path / "digest")
    other.review_candidate(candidate, operation_id="review-digest")
    digest_raw = bytearray(other.records_path.read_bytes())
    digest_raw[-2] = ord("0") if digest_raw[-2] != ord("0") else ord("1")
    other.records_path.write_bytes(bytes(digest_raw))
    with pytest.raises(AdaptiveCognitiveTwinStoreCorruptError):
        other.read_events()

    manifest_store = _store(tmp_path / "manifest")
    manifest_store.review_candidate(candidate, operation_id="review-manifest")
    manifest = manifest_store.manifest_path.read_text(encoding="utf-8")
    manifest_store.manifest_path.write_text(
        manifest.replace('"record_count":1', '"record_count":2'),
        encoding="utf-8",
    )
    with pytest.raises(AdaptiveCognitiveTwinStoreCorruptError):
        manifest_store.read_manifest()


def test_store_never_persists_raw_candidate_or_source_content(tmp_path: Path) -> None:
    store = _store(tmp_path)
    goal = _goal()
    source = _source_snapshot(goal)
    candidate = derive_adaptive_candidate(source, as_of=NOW)
    store.review_candidate(candidate, operation_id="review-private")
    activation = store.activate_candidate(candidate, source, operation_id="activate-private")
    assert activation.record.profile is not None

    persisted = store.records_path.read_text(encoding="utf-8")
    assert candidate.to_json() not in persisted
    assert "proposed_profile" not in persisted
    assert "user_statement" not in persisted
    assert '"candidate_fingerprint"' in persisted


def test_store_rejects_repository_vault_and_symlink_roots(tmp_path: Path) -> None:
    repo_root = Path(__file__).resolve().parents[1]
    with pytest.raises(AdaptiveCognitiveTwinStoreUnavailableError):
        AdaptiveCognitiveTwinStore(repo_root / "adaptive-cognitive-twin-test-root")

    vault = tmp_path / "vault"
    vault.mkdir()
    with pytest.raises(AdaptiveCognitiveTwinStoreUnavailableError):
        AdaptiveCognitiveTwinStore(vault / "adaptive", vault_root=vault)

    target = tmp_path / "target"
    target.mkdir()
    link = tmp_path / "link"
    try:
        os.symlink(target, link, target_is_directory=True)
    except OSError, NotImplementedError:
        pytest.skip("symlinks are unavailable in this test environment")
    with pytest.raises(AdaptiveCognitiveTwinStoreUnavailableError):
        AdaptiveCognitiveTwinStore(link / "adaptive")


def test_profile_retention_bound_is_closed(tmp_path: Path) -> None:
    assert ADAPTIVE_PROFILE_STORE_MAX_PROFILE_RECORDS_PER_GOAL == 128
    store = _store(tmp_path)
    goal = _goal()
    source = _source_snapshot(goal)
    candidate = derive_adaptive_candidate(source, as_of=NOW)
    store.review_candidate(candidate, operation_id="review-retention")
    activation = store.activate_candidate(candidate, source, operation_id="activate-retention")
    assert activation.record.profile is not None
    assert candidate.proposed_profile is not None
