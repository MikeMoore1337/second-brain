"""Provider-free tests for the Stage 14B reviewed Safe Write boundary."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import cast
from uuid import UUID

import pytest

from second_brain.adapters.vault import FileSystemVaultReader, FileSystemVaultWriter
from second_brain.application.goal_progress import (
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
    PersonalExperimentDispositionV1,
    PersonalExperimentLifecycleEventV1,
    PersonalExperimentLifecycleRecordV1,
    PersonalExperimentObservationRecordV1,
)
from second_brain.application.personal_experiments_safe_write import (
    PersonalExperimentDefinitionDraftV1,
    PersonalExperimentLifecycleDraftV1,
    PersonalExperimentObservationDraftV1,
    PersonalExperimentReassessmentDraftV1,
    PersonalExperimentReviewError,
    PersonalExperimentReviewStore,
    PersonalExperimentSafeWrite,
)
from second_brain.application.writes import CreateStatus
from tests.conftest import create_vault, snapshot_tree, write_note

GOAL_ID = "0198f4c5-6a00-7000-8000-000000000801"
FIXED_NOW = datetime(2026, 9, 14, 12, 0, tzinfo=UTC)
STAGE12_REVIEWED_AT = datetime(2026, 9, 14, 8, 0, tzinfo=UTC)


def _goal_note(body: str = "Хочу завершить рабочий проект.") -> str:
    return f"""---
id: {GOAL_ID}
type: zettel
created: 2026-09-14T07:00:00Z
tags: []
second_brain_personal_memory: 1
evidence_kind: user_statement
self_kind: goal
evidence_at: 2026-09-14T06:00:00Z
evidence_at_precision: exact
domain: work
---
{body}
"""


def _install_templates(vault: Path, *, zettel_body: str = "# Шаблон записи\n") -> None:
    for name in ("Project.md", "Area.md", "Resource.md"):
        (vault / "_templates" / name).write_text(f"# {name}\n", encoding="utf-8")
    (vault / "_templates" / "Zettel.md").write_text(zettel_body, encoding="utf-8")


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
    service = _stage12_service(vault)
    prepared = service.prepare_definition(
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
    assert prepared.plan is not None
    applied = service.apply(prepared.plan, prepared.plan.plan_sha256)
    assert applied.status is CreateStatus.CREATED
    return cast(DefinitionRecordV1, prepared.plan.record)


def _stage12_observation(vault: Path, definition_id: UUID) -> ObservationRecordV1:
    goal_uuid, goal_hash = _goal_binding(vault)
    service = _stage12_service(vault)
    prepared = service.prepare_observation(
        GoalProgressObservationDraftV1(
            goal_source_uuid=goal_uuid,
            goal_identity_fingerprint=goal_hash,
            progress_definition_id=definition_id,
            progress_model=ProgressModelV1.NUMERIC_TARGET,
            observed_at="2026-09-14T10:00:00Z",
            observed_at_precision="exact",
            observation_reviewed_at="2026-09-14T10:30:00Z",
            numeric_observation=NumericObservationV1("hours", "h", "15"),
        )
    )
    assert prepared.plan is not None
    applied = service.apply(prepared.plan, prepared.plan.plan_sha256)
    assert applied.status is CreateStatus.CREATED
    return cast(ObservationRecordV1, prepared.plan.record)


def _service(vault: Path) -> PersonalExperimentSafeWrite:
    return PersonalExperimentSafeWrite(
        FileSystemVaultReader(vault),
        FileSystemVaultWriter(vault),
        clock=lambda: FIXED_NOW,
    )


def _definition_draft(
    vault: Path, stage12_definition: DefinitionRecordV1
) -> PersonalExperimentDefinitionDraftV1:
    goal_uuid, goal_hash = _goal_binding(vault)
    return PersonalExperimentDefinitionDraftV1(
        goal_source_uuid=goal_uuid,
        goal_identity_fingerprint=goal_hash,
        goal_progress_definition_id=UUID(str(stage12_definition.id)),
        goal_progress_definition_fingerprint=stage12_definition.definition_fingerprint,
        hypothesis="Регулярная практика увеличит объём выполненной работы.",
        intervention="Планировать один дополнительный час практики в день.",
        baseline_strategy=PersonalExperimentBaselineStrategyV1.STAGE12_DEFINITION_EXPLICIT,
    )


def test_prepare_is_dry_run_and_apply_round_trips_exact_record(tmp_path: Path) -> None:
    vault = create_vault(tmp_path / "vault")
    _install_templates(vault)
    write_note(vault, "10 Projects/Goal.md", _goal_note())
    stage12 = _stage12_definition(vault)
    before = snapshot_tree(vault)
    service = _service(vault)

    prepared = service.prepare_definition(_definition_draft(vault, stage12))

    assert prepared.status is CreateStatus.DRY_RUN
    assert prepared.plan is not None
    plan = prepared.plan
    assert snapshot_tree(vault) == before
    assert plan.record.id == plan.note_id
    assert plan.record_kind.value == "definition"
    assert plan.as_dict()["payload"] == plan.record.as_dict()
    assert plan.as_dict()["plan_sha256"] == plan.plan_sha256
    assert plan.relative_path.startswith("40 Zettelkasten/personal-experiment-definition-")

    applied = service.apply(plan, plan.plan_sha256)

    assert applied.status is CreateStatus.CREATED
    assert applied.validation_report is not None
    assert applied.validation_report.error_count == 0
    assert applied.validation_report.personal_experiment_definitions == (plan.record,)
    assert (vault / plan.relative_path).is_file()
    retry = service.apply(plan, plan.plan_sha256)
    assert retry.status is CreateStatus.REJECTED
    assert retry.diagnostics[0].code == "CREATE_TARGET_EXISTS"


def test_lifecycle_and_enrollment_are_explicit_and_append_only(tmp_path: Path) -> None:
    vault = create_vault(tmp_path / "vault")
    _install_templates(vault)
    write_note(vault, "10 Projects/Goal.md", _goal_note())
    stage12_definition = _stage12_definition(vault)
    stage12_observation = _stage12_observation(vault, UUID(str(stage12_definition.id)))
    service = _service(vault)
    definition = service.prepare_definition(_definition_draft(vault, stage12_definition))
    assert definition.plan is not None
    assert (
        service.apply(definition.plan, definition.plan.plan_sha256).status is CreateStatus.CREATED
    )

    activation = service.prepare_lifecycle(
        PersonalExperimentLifecycleDraftV1(
            experiment_definition_id=definition.plan.note_id,
            experiment_definition_fingerprint=definition.plan.record.experiment_definition_fingerprint,
            lifecycle_event=PersonalExperimentLifecycleEventV1.ACTIVATION,
            event_at="2026-09-14T09:00:00Z",
        )
    )
    assert activation.plan is not None, activation.diagnostics[0].code
    assert (
        service.apply(activation.plan, activation.plan.plan_sha256).status is CreateStatus.CREATED
    )
    activation_correction = service.prepare_lifecycle(
        PersonalExperimentLifecycleDraftV1(
            experiment_definition_id=definition.plan.note_id,
            experiment_definition_fingerprint=definition.plan.record.experiment_definition_fingerprint,
            lifecycle_event=PersonalExperimentLifecycleEventV1.ACTIVATION,
            event_at="2026-09-14T09:30:00Z",
            supersedes_lifecycle_id=activation.plan.note_id,
            supersedes_lifecycle_fingerprint=cast(
                PersonalExperimentLifecycleRecordV1,
                activation.plan.record,
            ).lifecycle_fingerprint,
        )
    )
    assert activation_correction.plan is not None
    assert (
        service.apply(
            activation_correction.plan,
            activation_correction.plan.plan_sha256,
        ).status
        is CreateStatus.CREATED
    )

    enrollment = service.prepare_observation(
        PersonalExperimentObservationDraftV1(
            experiment_definition_id=definition.plan.note_id,
            experiment_definition_fingerprint=definition.plan.record.experiment_definition_fingerprint,
            stage12_observation_id=stage12_observation.id,
            stage12_observation_fingerprint=stage12_observation.observation_fingerprint,
        )
    )
    assert enrollment.plan is not None
    assert (
        service.apply(enrollment.plan, enrollment.plan.plan_sha256).status is CreateStatus.CREATED
    )

    completion = service.prepare_lifecycle(
        PersonalExperimentLifecycleDraftV1(
            experiment_definition_id=definition.plan.note_id,
            experiment_definition_fingerprint=definition.plan.record.experiment_definition_fingerprint,
            lifecycle_event=PersonalExperimentLifecycleEventV1.COMPLETION,
            event_at="2026-09-14T11:00:00Z",
        )
    )
    assert completion.plan is not None
    assert (
        service.apply(completion.plan, completion.plan.plan_sha256).status is CreateStatus.CREATED
    )

    reassessment = service.prepare_reassessment(
        PersonalExperimentReassessmentDraftV1(
            experiment_definition_id=definition.plan.note_id,
            experiment_definition_fingerprint=definition.plan.record.experiment_definition_fingerprint,
            result_fingerprint="sha256:" + "a" * 64,
            evaluation_as_of="2026-09-14T11:00:00Z",
            evaluation_policy_fingerprint=PERSONAL_EXPERIMENT_POLICY_FINGERPRINT,
            disposition=PersonalExperimentDispositionV1.HOLD,
            rationale="Нужно сохранить результат для явного пересмотра.",
        )
    )
    assert reassessment.plan is not None
    assert (
        service.apply(reassessment.plan, reassessment.plan.plan_sha256).status
        is CreateStatus.CREATED
    )

    observation_correction = service.prepare_observation(
        PersonalExperimentObservationDraftV1(
            experiment_definition_id=definition.plan.note_id,
            experiment_definition_fingerprint=definition.plan.record.experiment_definition_fingerprint,
            stage12_observation_id=stage12_observation.id,
            stage12_observation_fingerprint=stage12_observation.observation_fingerprint,
            supersedes_observation_id=enrollment.plan.note_id,
            supersedes_observation_fingerprint=cast(
                PersonalExperimentObservationRecordV1,
                enrollment.plan.record,
            ).observation_fingerprint,
        )
    )
    assert observation_correction.plan is not None
    assert (
        service.apply(
            observation_correction.plan,
            observation_correction.plan.plan_sha256,
        ).status
        is CreateStatus.CREATED
    )

    report = service._read_current_state().report
    assert len(report.personal_experiment_lifecycles) == 3
    assert len(report.personal_experiment_observations) == 2
    assert len(report.personal_experiment_reassessments) == 1
    assert report.error_count == 0


def test_stale_source_and_modified_preview_never_write(tmp_path: Path) -> None:
    vault = create_vault(tmp_path / "vault")
    _install_templates(vault)
    goal_path = write_note(vault, "10 Projects/Goal.md", _goal_note())
    stage12 = _stage12_definition(vault)
    service = _service(vault)
    prepared = service.prepare_definition(_definition_draft(vault, stage12))
    assert prepared.plan is not None
    plan = prepared.plan
    before = snapshot_tree(vault)

    tampered = replace(plan, plan_sha256="sha256:" + "0" * 64)
    assert service.apply(tampered, tampered.plan_sha256).diagnostics[0].code == (
        "PERSONAL_EXPERIMENT_SAFE_WRITE_REVIEW_REQUIRED"
    )
    assert snapshot_tree(vault) == before

    goal_path.write_text(_goal_note("Изменённая цель."), encoding="utf-8")
    stale = service.apply(plan, plan.plan_sha256)
    assert stale.status is CreateStatus.REJECTED
    assert stale.diagnostics[0].code == "PERSONAL_EXPERIMENT_VAULT_CHANGED"
    assert snapshot_tree(vault).keys() == before.keys()


def test_review_store_input_rejects_unknown_and_expired_token() -> None:
    now = [10.0]
    store = PersonalExperimentReviewStore(ttl_seconds=5, clock=lambda: now[0])
    with pytest.raises(PersonalExperimentReviewError):
        store.consume("owner", "unknown", "sha256:" + "0" * 64)
    now[0] = 20.0
    with pytest.raises(PersonalExperimentReviewError):
        store.consume("owner", "unknown", "sha256:" + "0" * 64)


def test_review_token_is_owner_bound_and_one_time(tmp_path: Path) -> None:
    vault = create_vault(tmp_path / "vault")
    _install_templates(vault)
    write_note(vault, "10 Projects/Goal.md", _goal_note())
    stage12 = _stage12_definition(vault)
    service = _service(vault)
    prepared = service.prepare_definition(_definition_draft(vault, stage12))
    assert prepared.plan is not None
    store = PersonalExperimentReviewStore()
    token = store.issue("owner-1", prepared.plan)

    with pytest.raises(PersonalExperimentReviewError):
        store.consume("owner-2", token, prepared.plan.plan_sha256)
    not_confirmed = service.apply_reviewed(
        "owner-1",
        token,
        prepared.plan.plan_sha256,
        store,
        confirmed=False,
    )
    assert not_confirmed.status is CreateStatus.REJECTED
    assert (
        service.apply_reviewed(
            "owner-1",
            token,
            prepared.plan.plan_sha256,
            store,
            confirmed=True,
        ).status
        is CreateStatus.CREATED
    )
    with pytest.raises(PersonalExperimentReviewError):
        store.consume("owner-1", token, prepared.plan.plan_sha256)


def test_unknown_target_is_never_overwritten_and_rollback_is_safe(tmp_path: Path) -> None:
    vault = create_vault(tmp_path / "vault")
    _install_templates(vault)
    write_note(vault, "10 Projects/Goal.md", _goal_note())
    stage12 = _stage12_definition(vault)
    service = _service(vault)
    prepared = service.prepare_definition(_definition_draft(vault, stage12))
    assert prepared.plan is not None
    target = vault / prepared.plan.relative_path
    target.write_text("owned by somebody else\n", encoding="utf-8")
    assert service.apply(prepared.plan, prepared.plan.plan_sha256).diagnostics[0].code == (
        "CREATE_TARGET_EXISTS"
    )
    assert target.read_text(encoding="utf-8") == "owned by somebody else\n"

    rollback_vault = create_vault(tmp_path / "rollback-vault")
    _install_templates(rollback_vault)
    write_note(rollback_vault, "10 Projects/Goal.md", _goal_note())
    rollback_service = _service(rollback_vault)
    rollback_stage12 = _stage12_definition(rollback_vault)
    (rollback_vault / "_templates" / "Zettel.md").write_text(
        "# Шаблон\n\n[[Отсутствующая заметка]]\n",
        encoding="utf-8",
    )
    rollback_plan_result = rollback_service.prepare_definition(
        _definition_draft(rollback_vault, rollback_stage12)
    )
    assert rollback_plan_result.plan is not None
    result = rollback_service.apply(
        rollback_plan_result.plan,
        rollback_plan_result.plan.plan_sha256,
    )
    assert result.status is CreateStatus.ROLLED_BACK
    assert result.rollback_succeeded is True
    assert not (rollback_vault / rollback_plan_result.plan.relative_path).exists()


def test_lifecycle_window_and_baseline_are_fail_closed(tmp_path: Path) -> None:
    vault = create_vault(tmp_path / "vault")
    _install_templates(vault)
    write_note(vault, "10 Projects/Goal.md", _goal_note())
    stage12 = _stage12_definition(vault)
    stage12_observation = _stage12_observation(vault, UUID(str(stage12.id)))
    service = _service(vault)

    pre_activation = replace(
        _definition_draft(vault, stage12),
        baseline_strategy=PersonalExperimentBaselineStrategyV1.REVIEWED_PRE_ACTIVATION_OBSERVATION,
        baseline_observation_uuid=stage12_observation.id,
        baseline_observation_fingerprint=stage12_observation.observation_fingerprint,
    )
    definition = service.prepare_definition(pre_activation)
    assert definition.plan is not None
    assert (
        service.apply(definition.plan, definition.plan.plan_sha256).status is CreateStatus.CREATED
    )

    too_early = service.prepare_lifecycle(
        PersonalExperimentLifecycleDraftV1(
            definition.plan.note_id,
            definition.plan.record.experiment_definition_fingerprint,
            PersonalExperimentLifecycleEventV1.ACTIVATION,
            "2026-09-14T09:00:00Z",
        )
    )
    assert too_early.status is CreateStatus.REJECTED
    assert too_early.diagnostics[0].code == "PERSONAL_EXPERIMENT_BASELINE_INVALID"

    explicit = service.prepare_lifecycle(
        PersonalExperimentLifecycleDraftV1(
            definition.plan.note_id,
            definition.plan.record.experiment_definition_fingerprint,
            PersonalExperimentLifecycleEventV1.ACTIVATION,
            "2026-09-14T11:00:00Z",
        )
    )
    assert explicit.plan is not None
    assert service.apply(explicit.plan, explicit.plan.plan_sha256).status is CreateStatus.CREATED

    before_activation = service.prepare_observation(
        PersonalExperimentObservationDraftV1(
            definition.plan.note_id,
            definition.plan.record.experiment_definition_fingerprint,
            stage12_observation.id,
            stage12_observation.observation_fingerprint,
        )
    )
    assert before_activation.status is CreateStatus.REJECTED
    assert before_activation.diagnostics[0].code == (
        "PERSONAL_EXPERIMENT_OBSERVATION_OUTSIDE_WINDOW"
    )
