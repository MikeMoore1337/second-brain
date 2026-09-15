"""Provider-free deterministic tests for Stage 14A canonical records."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from uuid import UUID

import pytest

from second_brain.adapters.vault import FileSystemVaultReader
from second_brain.application.goal_progress import (
    GOAL_PROGRESS_POLICY_FINGERPRINT,
    DefinitionRecordV1,
    NumericDirectionV1,
    NumericObservationV1,
    NumericTargetDefinitionV1,
    ObservationRecordV1,
    ProgressModelV1,
)
from second_brain.application.growth import GrowthGoalIdentityV1
from second_brain.application.personal_experiments import (
    PERSONAL_EXPERIMENT_POLICY_CANONICAL_JSON,
    PERSONAL_EXPERIMENT_POLICY_FINGERPRINT,
    PersonalExperimentBaselineStrategyV1,
    PersonalExperimentChainStateV1,
    PersonalExperimentDefinitionRecordV1,
    PersonalExperimentDispositionV1,
    PersonalExperimentLifecycleEventV1,
    PersonalExperimentLifecycleRecordV1,
    PersonalExperimentObservationRecordV1,
    PersonalExperimentReassessmentRecordV1,
    PersonalExperimentRecordError,
    PersonalExperimentSourceStateV1,
    build_personal_experiment_read_projection,
    is_personal_experiment_enrolled,
    parse_personal_experiment_record,
    personal_experiment_hash_json,
    validate_personal_experiment_definition_binding,
    validate_personal_experiment_definition_chain,
    validate_personal_experiment_lifecycle_chain,
    validate_personal_experiment_observation_binding,
    validate_personal_experiment_observation_chain,
    validate_personal_experiment_policy,
    validate_personal_experiment_reassessment_chain,
)
from second_brain.application.services import ValidateVault
from tests.conftest import create_vault, write_note

GOAL_ID = UUID("0198f4c5-6a00-7000-8000-000000000701")
STAGE12_DEFINITION_ID = UUID("0198f4c5-6a00-7000-8000-000000000702")
STAGE12_OBSERVATION_ID = UUID("0198f4c5-6a00-7000-8000-000000000703")
EXPERIMENT_ID = UUID("0198f4c5-6a00-7000-8000-000000000704")
EXPERIMENT_REPLACEMENT_ID = UUID("0198f4c5-6a00-7000-8000-000000000705")
ACTIVATION_ID = UUID("0198f4c5-6a00-7000-8000-000000000706")
COMPLETION_ID = UUID("0198f4c5-6a00-7000-8000-000000000707")
ENROLLMENT_ID = UUID("0198f4c5-6a00-7000-8000-000000000708")
REASSESSMENT_ID = UUID("0198f4c5-6a00-7000-8000-000000000709")
OTHER_REPLACEMENT_ID = UUID("0198f4c5-6a00-7000-8000-000000000710")
GOAL_HASH = "sha256:" + "a" * 64
STAGE12_DEFINITION_HASH = "sha256:" + "b" * 64
STAGE12_OBSERVATION_HASH = "sha256:" + "c" * 64
RESULT_HASH = "sha256:" + "d" * 64


def _definition(
    *,
    record_id: UUID = EXPERIMENT_ID,
    hypothesis: str = "Регулярный вечерний обзор улучшит измеряемый прогресс.",
    intervention: str = "Делать вечерний обзор пять дней в неделю.",
    baseline_strategy: PersonalExperimentBaselineStrategyV1 = (
        PersonalExperimentBaselineStrategyV1.STAGE12_DEFINITION_EXPLICIT
    ),
    stage12_definition_fingerprint: str = STAGE12_DEFINITION_HASH,
    baseline_id: UUID | None = None,
    baseline_hash: str | None = None,
    supersedes_id: UUID | None = None,
    supersedes_hash: str | None = None,
) -> PersonalExperimentDefinitionRecordV1:
    return PersonalExperimentDefinitionRecordV1(
        id=record_id,
        experiment_policy_fingerprint=PERSONAL_EXPERIMENT_POLICY_FINGERPRINT,
        goal_source_uuid=GOAL_ID,
        goal_identity_fingerprint=GOAL_HASH,
        goal_progress_definition_id=STAGE12_DEFINITION_ID,
        goal_progress_definition_fingerprint=stage12_definition_fingerprint,
        goal_progress_policy_fingerprint=GOAL_PROGRESS_POLICY_FINGERPRINT,
        hypothesis=hypothesis,
        intervention=intervention,
        baseline_strategy=baseline_strategy,
        definition_reviewed_at="2026-09-05T10:00:00Z",
        baseline_observation_uuid=baseline_id,
        baseline_observation_fingerprint=baseline_hash,
        supersedes_definition_id=supersedes_id,
        supersedes_definition_fingerprint=supersedes_hash,
    )


def _lifecycle(
    *,
    record_id: UUID,
    event: PersonalExperimentLifecycleEventV1,
    event_at: str,
    supersedes_id: UUID | None = None,
    supersedes_hash: str | None = None,
    definition: PersonalExperimentDefinitionRecordV1 | None = None,
) -> PersonalExperimentLifecycleRecordV1:
    current = definition or _definition()
    return PersonalExperimentLifecycleRecordV1(
        id=record_id,
        experiment_definition_id=EXPERIMENT_ID,
        experiment_definition_fingerprint=current.experiment_definition_fingerprint,
        goal_source_uuid=GOAL_ID,
        goal_identity_fingerprint=GOAL_HASH,
        lifecycle_event=event,
        event_at=event_at,
        lifecycle_reviewed_at="2026-09-05T11:00:00Z",
        supersedes_lifecycle_id=supersedes_id,
        supersedes_lifecycle_fingerprint=supersedes_hash,
    )


def _stage12_definition(*, goal_hash: str = GOAL_HASH) -> DefinitionRecordV1:
    return DefinitionRecordV1(
        id=STAGE12_DEFINITION_ID,
        goal_source_uuid=GOAL_ID,
        goal_identity_fingerprint=goal_hash,
        goal_progress_policy_fingerprint=GOAL_PROGRESS_POLICY_FINGERPRINT,
        definition_reviewed_at="2026-09-05T09:00:00Z",
        progress_model=ProgressModelV1.NUMERIC_TARGET,
        numeric_target=NumericTargetDefinitionV1(
            metric_id="weight",
            unit="kg",
            baseline=Decimal("80"),
            target=Decimal("72"),
            direction=NumericDirectionV1.DECREASE_TO,
        ),
    )


def _stage12_observation() -> ObservationRecordV1:
    definition = _stage12_definition()
    return ObservationRecordV1(
        id=STAGE12_OBSERVATION_ID,
        goal_source_uuid=GOAL_ID,
        goal_identity_fingerprint=GOAL_HASH,
        goal_progress_policy_fingerprint=GOAL_PROGRESS_POLICY_FINGERPRINT,
        progress_definition_id=definition.id,
        definition_fingerprint=definition.definition_fingerprint,
        progress_model=ProgressModelV1.NUMERIC_TARGET,
        observed_at="2026-09-06T10:00:00Z",
        observed_at_precision="exact",
        observation_reviewed_at="2026-09-06T11:00:00Z",
        numeric_observation=NumericObservationV1(
            metric_id="weight",
            unit="kg",
            value="78",
        ),
    )


def _growth_goal() -> GrowthGoalIdentityV1:
    return GrowthGoalIdentityV1(
        source_note_uuid=GOAL_ID,
        dimension="goal",
        source_evidence_kind="user_statement",
        source_self_kind="goal",
        domain="health",
        evidence_at=datetime(2026, 9, 5, 9, 0, tzinfo=UTC),
        evidence_at_precision="exact",
        source_contract_version="self-model-v1",
        source_derivation_version="self-model-derivation-v1",
        self_model_policy_fingerprint="e" * 64,
        source_fingerprint="sha256:" + "f" * 64,
        claim_fingerprint=GOAL_HASH,
    )


def _yaml_scalar(value: object) -> str:
    if type(value) is int:
        return str(value)
    return json.dumps(value, ensure_ascii=False)


def _managed_record_note(record: object) -> str:
    payload = record.as_dict()  # type: ignore[attr-defined]
    lines = [
        "---",
        "type: zettel",
        'created: "2026-09-05T12:00:00Z"',
        "tags: []",
    ]
    lines.extend(f"{key}: {_yaml_scalar(value)}" for key, value in payload.items())
    lines.extend(("---", "# Запись эксперимента", "", "Тестовая запись."))
    return "\n".join(lines) + "\n"


def test_policy_payload_and_fingerprint_are_fixed() -> None:
    assert validate_personal_experiment_policy() == PERSONAL_EXPERIMENT_POLICY_FINGERPRINT
    assert (
        personal_experiment_hash_json(json.loads(PERSONAL_EXPERIMENT_POLICY_CANONICAL_JSON))
        == PERSONAL_EXPERIMENT_POLICY_FINGERPRINT
    )


def test_scanner_exposes_typed_records_and_projection(tmp_path: Path) -> None:
    vault = create_vault(tmp_path / "vault")
    stage12_definition = _stage12_definition()
    definition = _definition(
        stage12_definition_fingerprint=stage12_definition.definition_fingerprint
    )
    write_note(vault, "10 Projects/Stage12.md", _managed_record_note(stage12_definition))
    write_note(vault, "10 Projects/Experiment.md", _managed_record_note(definition))

    report = ValidateVault(FileSystemVaultReader(vault)).execute()

    assert report.error_count == 0
    assert report.personal_experiment_definitions == (definition,)
    assert report.personal_experiment_lifecycles == ()
    assert report.personal_experiment_observations == ()
    assert report.personal_experiment_reassessments == ()
    assert report.personal_experiment_read_projection.definitions == (definition,)


def test_exact_marker_and_four_families_round_trip() -> None:
    definition = _definition()
    activation = _lifecycle(
        record_id=ACTIVATION_ID,
        event=PersonalExperimentLifecycleEventV1.ACTIVATION,
        event_at="2026-09-06T09:00:00Z",
        definition=definition,
    )
    enrollment = PersonalExperimentObservationRecordV1(
        id=ENROLLMENT_ID,
        experiment_definition_id=EXPERIMENT_ID,
        experiment_definition_fingerprint=definition.experiment_definition_fingerprint,
        goal_source_uuid=GOAL_ID,
        goal_identity_fingerprint=GOAL_HASH,
        goal_progress_definition_id=STAGE12_DEFINITION_ID,
        goal_progress_definition_fingerprint=STAGE12_DEFINITION_HASH,
        goal_progress_policy_fingerprint=GOAL_PROGRESS_POLICY_FINGERPRINT,
        stage12_observation_id=STAGE12_OBSERVATION_ID,
        stage12_observation_fingerprint=STAGE12_OBSERVATION_HASH,
        observation_reviewed_at="2026-09-07T10:00:00Z",
    )
    reassessment = PersonalExperimentReassessmentRecordV1(
        id=REASSESSMENT_ID,
        experiment_definition_id=EXPERIMENT_ID,
        experiment_definition_fingerprint=definition.experiment_definition_fingerprint,
        goal_source_uuid=GOAL_ID,
        goal_identity_fingerprint=GOAL_HASH,
        result_fingerprint=RESULT_HASH,
        evaluation_as_of="2026-09-10T10:00:00Z",
        evaluation_policy_fingerprint=PERSONAL_EXPERIMENT_POLICY_FINGERPRINT,
        disposition=PersonalExperimentDispositionV1.HOLD,
        rationale="Нужно ещё одно явно выбранное наблюдение.",
        reassessment_reviewed_at="2026-09-10T11:00:00Z",
    )
    for record in (definition, activation, enrollment, reassessment):
        parsed = parse_personal_experiment_record(record.as_dict())
        assert parsed == record
    assert is_personal_experiment_enrolled({"second_brain_personal_experiment": 1})
    assert not is_personal_experiment_enrolled({"second_brain_personal_experiment": True})


def test_definition_requires_explicit_baseline_pair_and_rejects_unknown_fields() -> None:
    with pytest.raises(PersonalExperimentRecordError, match="PERSONAL_EXPERIMENT_INVALID_FIELD"):
        _definition(
            baseline_strategy=PersonalExperimentBaselineStrategyV1.REVIEWED_PRE_ACTIVATION_OBSERVATION
        )
    with pytest.raises(PersonalExperimentRecordError, match="PERSONAL_EXPERIMENT_INVALID_FIELD"):
        _definition(
            baseline_strategy=PersonalExperimentBaselineStrategyV1.REVIEWED_PRE_ACTIVATION_OBSERVATION,
            baseline_id=STAGE12_OBSERVATION_ID,
        )
    raw = _definition().as_dict()
    raw["value"] = "не measurement Stage 14"
    with pytest.raises(PersonalExperimentRecordError, match="PERSONAL_EXPERIMENT_INVALID_FIELD"):
        parse_personal_experiment_record(raw)


def test_definition_chain_is_append_only_and_does_not_pick_a_winner() -> None:
    first = _definition()
    replacement = _definition(
        record_id=EXPERIMENT_REPLACEMENT_ID,
        hypothesis="Другая проверяемая гипотеза.",
        supersedes_id=EXPERIMENT_ID,
        supersedes_hash=first.experiment_definition_fingerprint,
    )
    chain = validate_personal_experiment_definition_chain((first, replacement))
    assert chain.state is PersonalExperimentChainStateV1.ONE_ACTIVE
    assert chain.active_records == (replacement,)

    other = _definition(
        record_id=OTHER_REPLACEMENT_ID,
        hypothesis="Параллельная ветка.",
        supersedes_id=EXPERIMENT_ID,
        supersedes_hash=first.experiment_definition_fingerprint,
    )
    conflict = validate_personal_experiment_definition_chain((first, replacement, other))
    assert conflict.state is PersonalExperimentChainStateV1.CONFLICT


def test_lifecycle_observation_and_reassessment_chains_are_typed() -> None:
    definition = _definition()
    activation = _lifecycle(
        record_id=ACTIVATION_ID,
        event=PersonalExperimentLifecycleEventV1.ACTIVATION,
        event_at="2026-09-06T09:00:00Z",
        definition=definition,
    )
    completion = _lifecycle(
        record_id=COMPLETION_ID,
        event=PersonalExperimentLifecycleEventV1.COMPLETION,
        event_at="2026-09-10T09:00:00Z",
        definition=definition,
    )
    lifecycle = validate_personal_experiment_lifecycle_chain((activation, completion))
    assert lifecycle.state == "completed"
    assert lifecycle.activation == activation
    assert lifecycle.terminal == completion

    enrollment = PersonalExperimentObservationRecordV1(
        id=ENROLLMENT_ID,
        experiment_definition_id=EXPERIMENT_ID,
        experiment_definition_fingerprint=definition.experiment_definition_fingerprint,
        goal_source_uuid=GOAL_ID,
        goal_identity_fingerprint=GOAL_HASH,
        goal_progress_definition_id=STAGE12_DEFINITION_ID,
        goal_progress_definition_fingerprint=STAGE12_DEFINITION_HASH,
        goal_progress_policy_fingerprint=GOAL_PROGRESS_POLICY_FINGERPRINT,
        stage12_observation_id=STAGE12_OBSERVATION_ID,
        stage12_observation_fingerprint=STAGE12_OBSERVATION_HASH,
        observation_reviewed_at="2026-09-07T10:00:00Z",
    )
    assert validate_personal_experiment_observation_chain((enrollment,)).state is (
        PersonalExperimentChainStateV1.ONE_ACTIVE
    )
    reassessment = PersonalExperimentReassessmentRecordV1(
        id=REASSESSMENT_ID,
        experiment_definition_id=EXPERIMENT_ID,
        experiment_definition_fingerprint=definition.experiment_definition_fingerprint,
        goal_source_uuid=GOAL_ID,
        goal_identity_fingerprint=GOAL_HASH,
        result_fingerprint=RESULT_HASH,
        evaluation_as_of="2026-09-10T10:00:00Z",
        evaluation_policy_fingerprint=PERSONAL_EXPERIMENT_POLICY_FINGERPRINT,
        disposition=PersonalExperimentDispositionV1.HOLD,
        rationale="Пока наблюдений недостаточно.",
        reassessment_reviewed_at="2026-09-10T11:00:00Z",
    )
    assert validate_personal_experiment_reassessment_chain((reassessment,)).state is (
        PersonalExperimentChainStateV1.ONE_ACTIVE
    )


def test_exact_current_goal_and_stage12_links_are_required() -> None:
    definition = _definition()
    goal = _growth_goal()
    goal_hash = personal_experiment_hash_json(goal.as_dict())
    stage12 = _stage12_definition(goal_hash=goal_hash)
    record = PersonalExperimentDefinitionRecordV1(
        id=definition.id,
        experiment_policy_fingerprint=definition.experiment_policy_fingerprint,
        goal_source_uuid=GOAL_ID,
        goal_identity_fingerprint=goal_hash,
        goal_progress_definition_id=stage12.id,
        goal_progress_definition_fingerprint=stage12.definition_fingerprint,
        goal_progress_policy_fingerprint=GOAL_PROGRESS_POLICY_FINGERPRINT,
        hypothesis=definition.hypothesis,
        intervention=definition.intervention,
        baseline_strategy=definition.baseline_strategy,
        definition_reviewed_at=definition.definition_reviewed_at,
    )
    binding = validate_personal_experiment_definition_binding(
        record,
        current_goals=(goal,),
        stage12_definitions=(stage12,),
        explicit_goal_source_uuid=GOAL_ID,
    )
    assert binding.state is PersonalExperimentSourceStateV1.EXACT_CURRENT
    changed = validate_personal_experiment_definition_binding(
        record,
        current_goals=(
            GrowthGoalIdentityV1(
                source_note_uuid=GOAL_ID,
                dimension="goal",
                source_evidence_kind="user_statement",
                source_self_kind="goal",
                domain="health",
                evidence_at=datetime(2026, 9, 5, 9, 0, tzinfo=UTC),
                evidence_at_precision="exact",
                source_contract_version="self-model-v1",
                source_derivation_version="self-model-derivation-v1",
                self_model_policy_fingerprint="e" * 64,
                source_fingerprint="sha256:" + "1" * 64,
                claim_fingerprint=GOAL_HASH,
            ),
        ),
        stage12_definitions=(stage12,),
        explicit_goal_source_uuid=GOAL_ID,
    )
    assert changed.state is PersonalExperimentSourceStateV1.SOURCE_CHANGED


def test_observation_link_re_reads_stage12_identity() -> None:
    definition = _definition()
    enrollment = PersonalExperimentObservationRecordV1(
        id=ENROLLMENT_ID,
        experiment_definition_id=EXPERIMENT_ID,
        experiment_definition_fingerprint=definition.experiment_definition_fingerprint,
        goal_source_uuid=GOAL_ID,
        goal_identity_fingerprint=GOAL_HASH,
        goal_progress_definition_id=STAGE12_DEFINITION_ID,
        goal_progress_definition_fingerprint=STAGE12_DEFINITION_HASH,
        goal_progress_policy_fingerprint=GOAL_PROGRESS_POLICY_FINGERPRINT,
        stage12_observation_id=STAGE12_OBSERVATION_ID,
        stage12_observation_fingerprint=STAGE12_OBSERVATION_HASH,
        observation_reviewed_at="2026-09-07T10:00:00Z",
    )
    observation = _stage12_observation()
    exact = validate_personal_experiment_observation_binding(
        enrollment,
        stage12_observations=(observation,),
    )
    assert exact.state is PersonalExperimentSourceStateV1.SOURCE_CHANGED
    unchanged = PersonalExperimentObservationRecordV1(
        id=ENROLLMENT_ID,
        experiment_definition_id=EXPERIMENT_ID,
        experiment_definition_fingerprint=definition.experiment_definition_fingerprint,
        goal_source_uuid=GOAL_ID,
        goal_identity_fingerprint=GOAL_HASH,
        goal_progress_definition_id=STAGE12_DEFINITION_ID,
        goal_progress_definition_fingerprint=observation.definition_fingerprint,
        goal_progress_policy_fingerprint=GOAL_PROGRESS_POLICY_FINGERPRINT,
        stage12_observation_id=STAGE12_OBSERVATION_ID,
        stage12_observation_fingerprint=observation.observation_fingerprint,
        observation_reviewed_at="2026-09-07T10:00:00Z",
    )
    assert (
        validate_personal_experiment_observation_binding(
            unchanged,
            stage12_observations=(observation,),
        ).state
        is PersonalExperimentSourceStateV1.EXACT_CURRENT
    )


def test_read_projection_is_sorted_and_contains_independent_chain_views() -> None:
    definition = _definition()
    activation = _lifecycle(
        record_id=ACTIVATION_ID,
        event=PersonalExperimentLifecycleEventV1.ACTIVATION,
        event_at="2026-09-06T09:00:00Z",
        definition=definition,
    )
    projection = build_personal_experiment_read_projection((activation, definition))
    assert projection.definitions == (definition,)
    assert projection.lifecycle == (activation,)
    assert len(projection.definition_chains) == 1
    assert len(projection.lifecycle_chains) == 1
