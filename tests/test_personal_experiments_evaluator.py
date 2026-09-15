"""Provider-free deterministic Stage 14C evaluation tests."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import cast
from uuid import UUID

import pytest

from second_brain.adapters.vault import FileSystemVaultReader, FileSystemVaultWriter
from second_brain.application.goal_progress import (
    GOAL_PROGRESS_POLICY_FINGERPRINT,
    DefinitionRecordV1,
    NumericDirectionV1,
    NumericObservationV1,
    NumericTargetDefinitionV1,
    ObservationRecordV1,
    ProgressModelV1,
)
from second_brain.application.goal_progress_safe_write import (
    GoalProgressDefinitionDraftV1,
    GoalProgressObservationDraftV1,
    GoalProgressSafeWrite,
)
from second_brain.application.growth import (
    BuildGrowthGoalContext,
    GrowthEngineRequestV1,
    growth_hash_json,
)
from second_brain.application.personal_experiments import (
    PERSONAL_EXPERIMENT_POLICY_FINGERPRINT,
    PersonalExperimentBaselineStrategyV1,
    PersonalExperimentDefinitionRecordV1,
    PersonalExperimentLifecycleEventV1,
    PersonalExperimentLifecycleRecordV1,
    PersonalExperimentObservationRecordV1,
)
from second_brain.application.personal_experiments_evaluator import (
    BuildPersonalExperimentEvaluation,
    PersonalExperimentBaselineSourceKindV1,
    PersonalExperimentEvaluationRequestInvalidError,
    PersonalExperimentEvaluationRequestV1,
    PersonalExperimentResultStatusV1,
)
from second_brain.application.personal_experiments_safe_write import (
    PersonalExperimentDefinitionDraftV1,
    PersonalExperimentLifecycleDraftV1,
    PersonalExperimentObservationDraftV1,
    PersonalExperimentSafeWrite,
)
from second_brain.application.reports import VaultSnapshot
from second_brain.application.writes import CreateStatus
from tests.conftest import create_vault, snapshot_tree, write_note

GOAL_ID = "0198f4c5-6a00-7000-8000-000000000901"
FIXED_NOW = datetime(2026, 9, 14, 13, 0, tzinfo=UTC)
STAGE12_REVIEWED_AT = "2026-09-14T07:00:00Z"


def _goal_note(body: str = "Хочу завершить рабочий проект.") -> str:
    return f"""---
id: {GOAL_ID}
type: zettel
created: 2026-09-14T06:00:00Z
tags: []
second_brain_personal_memory: 1
evidence_kind: user_statement
self_kind: goal
evidence_at: 2026-09-14T05:00:00Z
evidence_at_precision: exact
domain: work
---
{body}
"""


def _install_templates(vault: Path) -> None:
    for name in ("Project.md", "Area.md", "Resource.md"):
        (vault / "_templates" / name).write_text(f"# {name}\n", encoding="utf-8")
    (vault / "_templates" / "Zettel.md").write_text("# Запись\n", encoding="utf-8")


def _goal_binding(vault: Path) -> tuple[UUID, str]:
    context = BuildGrowthGoalContext(
        FileSystemVaultReader(vault),
        clock=lambda: FIXED_NOW,
    ).execute(GrowthEngineRequestV1())
    assert len(context.goals) == 1
    goal = context.goals[0]
    return UUID(str(goal.source_note_uuid)), growth_hash_json(goal.as_dict())


def _stage12_service(vault: Path) -> GoalProgressSafeWrite:
    return GoalProgressSafeWrite(
        FileSystemVaultReader(vault),
        FileSystemVaultWriter(vault),
        clock=lambda: FIXED_NOW,
    )


def _stage12_definition(vault: Path) -> DefinitionRecordV1:
    goal_uuid, goal_hash = _goal_binding(vault)
    prepared = _stage12_service(vault).prepare_definition(
        GoalProgressDefinitionDraftV1(
            goal_source_uuid=goal_uuid,
            goal_identity_fingerprint=goal_hash,
            definition_reviewed_at=STAGE12_REVIEWED_AT,
            progress_model=ProgressModelV1.NUMERIC_TARGET,
            numeric_target=NumericTargetDefinitionV1(
                metric_id="hours",
                unit="h",
                baseline="10",
                target="20",
                direction=NumericDirectionV1.INCREASE_TO,
                lower_bound="0",
                upper_bound="100",
            ),
        )
    )
    assert prepared.plan is not None, prepared.diagnostics
    assert (
        _stage12_service(vault)
        .apply(
            prepared.plan,
            prepared.plan.plan_sha256,
        )
        .status
        is CreateStatus.CREATED
    )
    return cast(DefinitionRecordV1, prepared.plan.record)


def _stage12_observation(
    vault: Path,
    definition: DefinitionRecordV1,
    *,
    observed_at: str,
    value: str,
) -> ObservationRecordV1:
    goal_uuid, goal_hash = _goal_binding(vault)
    prepared = _stage12_service(vault).prepare_observation(
        GoalProgressObservationDraftV1(
            goal_source_uuid=goal_uuid,
            goal_identity_fingerprint=goal_hash,
            progress_definition_id=cast(UUID, definition.id),
            progress_model=ProgressModelV1.NUMERIC_TARGET,
            observed_at=observed_at,
            observed_at_precision="exact",
            observation_reviewed_at=observed_at,
            numeric_observation=NumericObservationV1("hours", "h", value),
        )
    )
    assert prepared.plan is not None, prepared.diagnostics
    assert (
        _stage12_service(vault)
        .apply(
            prepared.plan,
            prepared.plan.plan_sha256,
        )
        .status
        is CreateStatus.CREATED
    )
    return cast(ObservationRecordV1, prepared.plan.record)


def _stage14_service(vault: Path) -> PersonalExperimentSafeWrite:
    return PersonalExperimentSafeWrite(
        FileSystemVaultReader(vault),
        FileSystemVaultWriter(vault),
        clock=lambda: FIXED_NOW,
    )


def _experiment_definition(
    vault: Path,
    stage12_definition: DefinitionRecordV1,
    *,
    baseline_strategy: PersonalExperimentBaselineStrategyV1 = (
        PersonalExperimentBaselineStrategyV1.STAGE12_DEFINITION_EXPLICIT
    ),
    baseline_observation: ObservationRecordV1 | None = None,
) -> PersonalExperimentDefinitionRecordV1:
    goal_uuid, goal_hash = _goal_binding(vault)
    prepared = _stage14_service(vault).prepare_definition(
        PersonalExperimentDefinitionDraftV1(
            goal_source_uuid=goal_uuid,
            goal_identity_fingerprint=goal_hash,
            goal_progress_definition_id=cast(UUID, stage12_definition.id),
            goal_progress_definition_fingerprint=stage12_definition.definition_fingerprint,
            hypothesis="Дополнительная практика увеличит объём выполненной работы.",
            intervention="Планировать один дополнительный час практики в день.",
            baseline_strategy=baseline_strategy,
            baseline_observation_uuid=(
                baseline_observation.id if baseline_observation is not None else None
            ),
            baseline_observation_fingerprint=(
                baseline_observation.observation_fingerprint
                if baseline_observation is not None
                else None
            ),
        )
    )
    assert prepared.plan is not None, prepared.diagnostics
    assert (
        _stage14_service(vault)
        .apply(
            prepared.plan,
            prepared.plan.plan_sha256,
        )
        .status
        is CreateStatus.CREATED
    )
    return cast(PersonalExperimentDefinitionRecordV1, prepared.plan.record)


def _activate(
    vault: Path,
    definition: PersonalExperimentDefinitionRecordV1,
    event_at: str = "2026-09-14T09:00:00Z",
) -> PersonalExperimentLifecycleRecordV1:
    prepared = _stage14_service(vault).prepare_lifecycle(
        PersonalExperimentLifecycleDraftV1(
            experiment_definition_id=cast(UUID, definition.id),
            experiment_definition_fingerprint=definition.experiment_definition_fingerprint,
            lifecycle_event=PersonalExperimentLifecycleEventV1.ACTIVATION,
            event_at=event_at,
        )
    )
    assert prepared.plan is not None, prepared.diagnostics
    assert (
        _stage14_service(vault)
        .apply(
            prepared.plan,
            prepared.plan.plan_sha256,
        )
        .status
        is CreateStatus.CREATED
    )
    return cast(PersonalExperimentLifecycleRecordV1, prepared.plan.record)


def _enroll(
    vault: Path,
    definition: PersonalExperimentDefinitionRecordV1,
    observation: ObservationRecordV1,
) -> PersonalExperimentObservationRecordV1:
    prepared = _stage14_service(vault).prepare_observation(
        PersonalExperimentObservationDraftV1(
            experiment_definition_id=cast(UUID, definition.id),
            experiment_definition_fingerprint=definition.experiment_definition_fingerprint,
            stage12_observation_id=cast(UUID, observation.id),
            stage12_observation_fingerprint=observation.observation_fingerprint,
        )
    )
    assert prepared.plan is not None, prepared.diagnostics
    assert (
        _stage14_service(vault)
        .apply(
            prepared.plan,
            prepared.plan.plan_sha256,
        )
        .status
        is CreateStatus.CREATED
    )
    return cast(PersonalExperimentObservationRecordV1, prepared.plan.record)


def _request(
    definition: PersonalExperimentDefinitionRecordV1,
    as_of: str = "2026-09-14T13:00:00Z",
) -> PersonalExperimentEvaluationRequestV1:
    return PersonalExperimentEvaluationRequestV1(
        cast(UUID, definition.id),
        definition.experiment_definition_fingerprint,
        as_of,
    )


def _scenario(
    vault: Path,
    *,
    value: str = "15",
    observation_at: str = "2026-09-14T10:00:00Z",
) -> tuple[
    PersonalExperimentDefinitionRecordV1,
    ObservationRecordV1,
    PersonalExperimentObservationRecordV1,
]:
    _install_templates(vault)
    write_note(vault, "10 Projects/Goal.md", _goal_note())
    stage12_definition = _stage12_definition(vault)
    stage12_observation = _stage12_observation(
        vault,
        stage12_definition,
        observed_at=observation_at,
        value=value,
    )
    definition = _experiment_definition(vault, stage12_definition)
    _activate(vault, definition)
    enrollment = _enroll(vault, definition, stage12_observation)
    return definition, stage12_observation, enrollment


def test_result_reuses_stage12_semantics_and_is_byte_deterministic(tmp_path: Path) -> None:
    vault = create_vault(tmp_path / "vault")
    definition, stage12_observation, enrollment = _scenario(vault)
    before = snapshot_tree(vault)
    builder = BuildPersonalExperimentEvaluation(FileSystemVaultReader(vault))

    first = builder.execute(_request(definition))
    second = builder.execute(_request(definition))

    assert first.status is PersonalExperimentResultStatusV1.OBSERVED_TOWARD_TARGET
    assert first.stage12_status == "toward_target"
    assert first.stage12_current_observation_ids == (stage12_observation.id,)
    assert first.included_observations[0].enrollment_id == enrollment.id
    assert first.baseline is not None
    assert first.baseline.source_kind is PersonalExperimentBaselineSourceKindV1.STAGE12_DEFINITION
    assert first.provenance is not None
    assert first.provenance.provider == "none"
    assert first.provenance.network == "none"
    assert first.provenance.write == "none"
    assert first.to_json() == second.to_json()
    assert first.result_fingerprint == second.result_fingerprint
    assert first.contract_id == "personal-experiments-v1"
    assert first.as_dict()["contract_id"] == "personal-experiments-v1"
    assert first.caveats == (
        "observed_change_is_not_proof_of_causation",
        "no_automatic_adaptation",
    )
    assert snapshot_tree(vault) == before


def test_only_explicitly_enrolled_observations_reach_stage12(tmp_path: Path) -> None:
    vault = create_vault(tmp_path / "vault")
    _install_templates(vault)
    write_note(vault, "10 Projects/Goal.md", _goal_note())
    stage12_definition = _stage12_definition(vault)
    enrolled_source = _stage12_observation(
        vault,
        stage12_definition,
        observed_at="2026-09-14T10:00:00Z",
        value="15",
    )
    not_enrolled_source = _stage12_observation(
        vault,
        stage12_definition,
        observed_at="2026-09-14T11:00:00Z",
        value="5",
    )
    definition = _experiment_definition(vault, stage12_definition)
    _activate(vault, definition)
    _enroll(vault, definition, enrolled_source)

    result = BuildPersonalExperimentEvaluation(FileSystemVaultReader(vault)).execute(
        _request(definition)
    )

    assert result.status is PersonalExperimentResultStatusV1.OBSERVED_TOWARD_TARGET
    assert result.stage12_current_observation_ids == (enrolled_source.id,)
    assert not_enrolled_source.id not in result.stage12_current_observation_ids
    assert all(
        item.stage12_observation_id != not_enrolled_source.id
        for item in result.included_observations
        + tuple(item.reference for item in result.excluded_observations)
    )


def test_lifecycle_precedence_and_terminal_historical_reconstruction(tmp_path: Path) -> None:
    planned_vault = create_vault(tmp_path / "planned")
    _install_templates(planned_vault)
    write_note(planned_vault, "10 Projects/Goal.md", _goal_note())
    planned_stage12 = _stage12_definition(planned_vault)
    planned_definition = _experiment_definition(planned_vault, planned_stage12)
    planned = BuildPersonalExperimentEvaluation(FileSystemVaultReader(planned_vault)).execute(
        _request(planned_definition)
    )
    assert planned.status is PersonalExperimentResultStatusV1.NOT_EVALUATED
    assert planned.reasons == ("no_activation",)

    completed_vault = create_vault(tmp_path / "completed")
    definition, stage12_observation, _ = _scenario(completed_vault)
    completion = _stage14_service(completed_vault).prepare_lifecycle(
        PersonalExperimentLifecycleDraftV1(
            experiment_definition_id=cast(UUID, definition.id),
            experiment_definition_fingerprint=definition.experiment_definition_fingerprint,
            lifecycle_event=PersonalExperimentLifecycleEventV1.COMPLETION,
            event_at="2026-09-14T12:00:00Z",
        )
    )
    assert completion.plan is not None, completion.diagnostics
    assert (
        _stage14_service(completed_vault)
        .apply(
            completion.plan,
            completion.plan.plan_sha256,
        )
        .status
        is CreateStatus.CREATED
    )
    result = BuildPersonalExperimentEvaluation(FileSystemVaultReader(completed_vault)).execute(
        _request(definition)
    )
    assert result.lifecycle_state == "completed"
    assert result.terminal_at == datetime(2026, 9, 14, 12, 0, tzinfo=UTC)
    assert result.status is PersonalExperimentResultStatusV1.OBSERVED_TOWARD_TARGET
    assert result.stage12_current_observation_ids == (stage12_observation.id,)

    cancelled_vault = create_vault(tmp_path / "cancelled")
    definition, _, _ = _scenario(cancelled_vault)
    cancellation = _stage14_service(cancelled_vault).prepare_lifecycle(
        PersonalExperimentLifecycleDraftV1(
            experiment_definition_id=cast(UUID, definition.id),
            experiment_definition_fingerprint=definition.experiment_definition_fingerprint,
            lifecycle_event=PersonalExperimentLifecycleEventV1.CANCELLATION,
            event_at="2026-09-14T12:00:00Z",
        )
    )
    assert cancellation.plan is not None, cancellation.diagnostics
    assert (
        _stage14_service(cancelled_vault)
        .apply(
            cancellation.plan,
            cancellation.plan.plan_sha256,
        )
        .status
        is CreateStatus.CREATED
    )
    cancelled = BuildPersonalExperimentEvaluation(FileSystemVaultReader(cancelled_vault)).execute(
        _request(definition)
    )
    assert cancelled.status is PersonalExperimentResultStatusV1.CANCELLED
    assert cancelled.reasons == ("cancelled_by_owner",)
    assert cancelled.stage12_status is None


def test_reviewed_pre_activation_baseline_is_explicit(tmp_path: Path) -> None:
    vault = create_vault(tmp_path / "vault")
    _install_templates(vault)
    write_note(vault, "10 Projects/Goal.md", _goal_note())
    stage12_definition = _stage12_definition(vault)
    baseline = _stage12_observation(
        vault,
        stage12_definition,
        observed_at="2026-09-14T08:00:00Z",
        value="10",
    )
    post_activation = _stage12_observation(
        vault,
        stage12_definition,
        observed_at="2026-09-14T10:00:00Z",
        value="15",
    )
    definition = _experiment_definition(
        vault,
        stage12_definition,
        baseline_strategy=PersonalExperimentBaselineStrategyV1.REVIEWED_PRE_ACTIVATION_OBSERVATION,
        baseline_observation=baseline,
    )
    _activate(vault, definition)
    _enroll(vault, definition, post_activation)

    result = BuildPersonalExperimentEvaluation(FileSystemVaultReader(vault)).execute(
        _request(definition)
    )

    assert result.status is PersonalExperimentResultStatusV1.OBSERVED_TOWARD_TARGET
    assert result.baseline is not None
    assert result.baseline.source_kind is PersonalExperimentBaselineSourceKindV1.STAGE12_OBSERVATION
    assert result.baseline.stage12_observation_id == baseline.id
    assert result.baseline.observed_at == datetime(2026, 9, 14, 8, 0, tzinfo=UTC)


def test_exact_identity_and_goal_drift_fail_closed(tmp_path: Path) -> None:
    vault = create_vault(tmp_path / "vault")
    definition, _, _ = _scenario(vault)
    reader = FileSystemVaultReader(vault)
    wrong_fingerprint = PersonalExperimentEvaluationRequestV1(
        cast(UUID, definition.id),
        "sha256:" + "0" * 64,
        "2026-09-14T13:00:00Z",
    )
    changed = BuildPersonalExperimentEvaluation(reader).execute(wrong_fingerprint)
    assert changed.status is PersonalExperimentResultStatusV1.SOURCE_CHANGED
    missing = BuildPersonalExperimentEvaluation(reader).execute(
        PersonalExperimentEvaluationRequestV1(
            UUID("0198f4c5-6a00-7000-8000-000000009999"),
            definition.experiment_definition_fingerprint,
            "2026-09-14T13:00:00Z",
        )
    )
    assert missing.status is PersonalExperimentResultStatusV1.NOT_COMPARABLE
    assert missing.reasons == ("exact_source_missing",)

    goal_path = vault / "10 Projects" / "Goal.md"
    goal_path.write_text(_goal_note("Изменённая цель."), encoding="utf-8")
    drifted = BuildPersonalExperimentEvaluation(reader).execute(_request(definition))
    assert drifted.status is PersonalExperimentResultStatusV1.SOURCE_CHANGED
    assert drifted.reasons == ("source_changed",)


def test_evaluator_reads_once_and_invalid_request_does_not_scan(tmp_path: Path) -> None:
    vault = create_vault(tmp_path / "vault")
    definition, _, _ = _scenario(vault)
    snapshot = FileSystemVaultReader(vault).scan()

    class CountingReader:
        def __init__(self, value: VaultSnapshot) -> None:
            self.value = value
            self.scans = 0

        def scan(self) -> VaultSnapshot:
            self.scans += 1
            return self.value

    # The malformed request is rejected before a reader is touched.
    invalid_reader = CountingReader(snapshot)
    with pytest.raises(PersonalExperimentEvaluationRequestInvalidError):
        BuildPersonalExperimentEvaluation(invalid_reader).execute(
            cast(PersonalExperimentEvaluationRequestV1, object())
        )
    assert invalid_reader.scans == 0

    counting = CountingReader(snapshot)
    result = BuildPersonalExperimentEvaluation(counting).execute(_request(definition))
    assert result.status is PersonalExperimentResultStatusV1.OBSERVED_TOWARD_TARGET
    assert counting.scans == 1


def test_result_does_not_copy_source_body_or_storage_paths(tmp_path: Path) -> None:
    vault = create_vault(tmp_path / "vault")
    definition, _, _ = _scenario(vault)
    result = BuildPersonalExperimentEvaluation(FileSystemVaultReader(vault)).execute(
        _request(definition)
    )
    result_json = result.to_json()
    assert "10 Projects" not in result_json
    assert "Хочу завершить рабочий проект" not in result_json
    assert "body" not in result_json
    assert "path" not in result_json
    assert PERSONAL_EXPERIMENT_POLICY_FINGERPRINT in result_json
    assert GOAL_PROGRESS_POLICY_FINGERPRINT in result_json
