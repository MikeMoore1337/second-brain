"""Focused provider-free tests for Cognitive Twin v3 Stage 12B Safe Write."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

import pytest

from second_brain.adapters.vault import FileSystemVaultReader, FileSystemVaultWriter
from second_brain.application.goal_progress import (
    GOAL_PROGRESS_POLICY_FINGERPRINT,
    GoalProgressValidationError,
    NumericDirectionV1,
    NumericObservationV1,
    NumericTargetDefinitionV1,
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
from second_brain.application.writes import CreateStatus
from tests.conftest import create_vault, snapshot_tree, write_note

GOAL_ID = "0198f4c5-6a00-7000-8000-000000000301"
FIXED_NOW = datetime(2026, 9, 14, 12, 0, tzinfo=UTC)
FIXED_REVIEWED_AT = datetime(2026, 9, 14, 10, 0, tzinfo=UTC)


def _goal_note(body: str = "Хочу завершить проект.") -> str:
    return f"""---
id: {GOAL_ID}
type: zettel
created: 2026-09-14T09:00:00Z
tags: []
second_brain_personal_memory: 1
evidence_kind: user_statement
self_kind: goal
evidence_at: 2026-09-14T08:00:00Z
evidence_at_precision: exact
domain: work
---
{body}
"""


def _install_templates(vault: Path, *, zettel_body: str = "# Zettel template\n") -> None:
    for name in ("Project.md", "Area.md", "Resource.md"):
        (vault / "_templates" / name).write_text(f"# {name}\n", encoding="utf-8")
    (vault / "_templates" / "Zettel.md").write_text(zettel_body, encoding="utf-8")


def _service(vault: Path) -> GoalProgressSafeWrite:
    return GoalProgressSafeWrite(
        FileSystemVaultReader(vault),
        FileSystemVaultWriter(vault),
        clock=lambda: FIXED_NOW,
    )


def _goal_binding(vault: Path) -> tuple[UUID, str]:
    context = BuildGrowthGoalContext(
        FileSystemVaultReader(vault),
        clock=lambda: FIXED_NOW,
    ).execute(GrowthEngineRequestV1())
    assert len(context.goals) == 1
    goal = context.goals[0]
    return UUID(str(goal.source_note_uuid)), growth_hash_json(goal.as_dict())


def _definition_draft(
    vault: Path,
    *,
    supersedes: str | None = None,
) -> GoalProgressDefinitionDraftV1:
    goal_uuid, goal_hash = _goal_binding(vault)
    return GoalProgressDefinitionDraftV1(
        goal_source_uuid=goal_uuid,
        goal_identity_fingerprint=goal_hash,
        definition_reviewed_at=FIXED_REVIEWED_AT,
        progress_model=ProgressModelV1.NUMERIC_TARGET,
        numeric_target=NumericTargetDefinitionV1(
            metric_id="weight",
            unit="kg",
            baseline="80.000",
            target="72",
            direction=NumericDirectionV1.DECREASE_TO,
            lower_bound="40",
            upper_bound="120",
        ),
        supersedes_definition_id=supersedes,
    )


def _observation_draft(
    vault: Path,
    definition_id: UUID,
    *,
    value: str = "76.800",
    supersedes: UUID | None = None,
) -> GoalProgressObservationDraftV1:
    goal_uuid, goal_hash = _goal_binding(vault)
    return GoalProgressObservationDraftV1(
        goal_source_uuid=goal_uuid,
        goal_identity_fingerprint=goal_hash,
        progress_definition_id=definition_id,
        progress_model=ProgressModelV1.NUMERIC_TARGET,
        observed_at=datetime(2026, 9, 14, 11, 0, tzinfo=UTC),
        observed_at_precision="exact",
        observation_reviewed_at=FIXED_REVIEWED_AT,
        numeric_observation=NumericObservationV1("weight", "kg", value),
        supersedes_observation_id=supersedes,
    )


def test_prepare_is_dry_run_and_apply_writes_exact_reviewed_plan(tmp_path: Path) -> None:
    vault = create_vault(tmp_path / "vault")
    _install_templates(vault)
    write_note(vault, "10 Projects/Goal.md", _goal_note())
    before = snapshot_tree(vault)

    service = _service(vault)
    prepared = service.prepare_definition(_definition_draft(vault))

    assert prepared.status is CreateStatus.DRY_RUN
    assert prepared.plan is not None
    plan = prepared.plan
    assert snapshot_tree(vault) == before
    assert plan.record_kind.value == "definition"
    assert plan.record.id == plan.note_id
    assert plan.as_dict()["payload"] == plan.record.as_dict()
    assert plan.as_dict()["source_fingerprint"]
    assert plan.as_dict()["plan_sha256"] == plan.plan_sha256
    assert plan.relative_path.startswith("40 Zettelkasten/goal-progress-definition-")
    assert plan.record.goal_progress_policy_fingerprint == GOAL_PROGRESS_POLICY_FINGERPRINT

    applied = service.apply(plan, plan.plan_sha256)

    assert applied.status is CreateStatus.CREATED
    assert applied.validation_report is not None
    assert applied.validation_report.error_count == 0
    assert applied.receipt is not None
    assert (vault / plan.relative_path).is_file()
    assert len(snapshot_tree(vault)) == len(before) + 1

    retry = service.apply(plan, plan.plan_sha256)
    assert retry.status is CreateStatus.REJECTED
    assert retry.diagnostics[0].code == "CREATE_TARGET_EXISTS"
    assert (vault / plan.relative_path).is_file()


def test_observation_derives_definition_fingerprint_and_correction_is_append_only(
    tmp_path: Path,
) -> None:
    vault = create_vault(tmp_path / "vault")
    _install_templates(vault)
    write_note(vault, "10 Projects/Goal.md", _goal_note())
    service = _service(vault)

    definition_result = service.prepare_definition(_definition_draft(vault))
    assert definition_result.plan is not None
    assert (
        service.apply(
            definition_result.plan,
            definition_result.plan.plan_sha256,
        ).status
        is CreateStatus.CREATED
    )
    definition_id = definition_result.plan.note_id

    observation_result = service.prepare_observation(_observation_draft(vault, definition_id))
    assert observation_result.status is CreateStatus.DRY_RUN
    assert observation_result.plan is not None
    observation_plan = observation_result.plan
    assert observation_plan.definition_fingerprint == (
        definition_result.plan.record.definition_fingerprint
    )
    assert (
        service.apply(observation_plan, observation_plan.plan_sha256).status is CreateStatus.CREATED
    )

    correction_result = service.prepare_observation(
        _observation_draft(
            vault,
            definition_id,
            value="75.900",
            supersedes=observation_plan.note_id,
        )
    )
    assert correction_result.status is CreateStatus.DRY_RUN
    assert correction_result.plan is not None
    correction = service.apply(correction_result.plan, correction_result.plan.plan_sha256)
    assert correction.status is CreateStatus.CREATED

    state = service._read_current_state()
    assert len(state.report.goal_progress_definitions) == 1
    observations = state.report.goal_progress_observations
    assert len(observations) == 2
    assert {item.supersedes_observation_id for item in observations} == {
        None,
        observation_plan.note_id,
    }


def test_second_root_is_rejected_and_exact_predecessor_replacement_is_allowed(
    tmp_path: Path,
) -> None:
    vault = create_vault(tmp_path / "vault")
    _install_templates(vault)
    write_note(vault, "10 Projects/Goal.md", _goal_note())
    service = _service(vault)

    first = service.prepare_definition(_definition_draft(vault))
    assert first.plan is not None
    assert service.apply(first.plan, first.plan.plan_sha256).status is CreateStatus.CREATED

    second_root = service.prepare_definition(_definition_draft(vault))
    assert second_root.status is CreateStatus.REJECTED
    assert second_root.diagnostics[0].code == "GOAL_PROGRESS_DEFINITION_CONFLICT"

    replacement = service.prepare_definition(
        _definition_draft(vault, supersedes=str(first.plan.note_id))
    )
    assert replacement.status is CreateStatus.DRY_RUN
    assert replacement.plan is not None
    applied = service.apply(replacement.plan, replacement.plan.plan_sha256)
    assert applied.status is CreateStatus.CREATED
    state = service._read_current_state()
    assert len(state.report.goal_progress_definitions) == 2


def test_hash_tampering_and_source_change_fail_closed_without_write(tmp_path: Path) -> None:
    vault = create_vault(tmp_path / "vault")
    _install_templates(vault)
    write_note(vault, "10 Projects/Goal.md", _goal_note())
    service = _service(vault)
    prepared = service.prepare_definition(_definition_draft(vault))
    assert prepared.plan is not None
    plan = prepared.plan
    before = snapshot_tree(vault)

    tampered = replace(plan, plan_sha256="sha256:" + "0" * 64)
    tampered_result = service.apply(tampered, tampered.plan_sha256)
    assert tampered_result.status is CreateStatus.REJECTED
    assert tampered_result.diagnostics[0].code == "GOAL_PROGRESS_SAFE_WRITE_REVIEW_REQUIRED"
    assert snapshot_tree(vault) == before

    goal_path = vault / "10 Projects/Goal.md"
    goal_path.write_text(_goal_note("Изменённая цель."), encoding="utf-8")
    stale = service.apply(plan, plan.plan_sha256)
    assert stale.status is CreateStatus.REJECTED
    assert stale.diagnostics[0].code == "GOAL_PROGRESS_VAULT_CHANGED"
    assert snapshot_tree(vault).keys() == before.keys()


def test_post_write_full_validation_rolls_back_the_new_record(tmp_path: Path) -> None:
    vault = create_vault(tmp_path / "vault")
    _install_templates(vault, zettel_body="# Template\n\n[[Missing target]]\n")
    write_note(vault, "10 Projects/Goal.md", _goal_note())
    before = snapshot_tree(vault)
    service = _service(vault)

    prepared = service.prepare_definition(_definition_draft(vault))
    assert prepared.plan is not None
    result = service.apply(prepared.plan, prepared.plan.plan_sha256)

    assert result.status is CreateStatus.ROLLED_BACK
    assert result.rollback_succeeded is True
    assert result.diagnostics[0].code == "GOAL_PROGRESS_SAFE_WRITE_VALIDATION_FAILED"
    assert snapshot_tree(vault) == before


def test_rollback_race_is_fail_closed(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    vault = create_vault(tmp_path / "vault")
    _install_templates(vault, zettel_body="# Template\n\n[[Missing target]]\n")
    write_note(vault, "10 Projects/Goal.md", _goal_note())
    service = _service(vault)
    prepared = service.prepare_definition(_definition_draft(vault))
    assert prepared.plan is not None
    monkeypatch.setattr(service.writer, "rollback", lambda receipt: False)

    result = service.apply(prepared.plan, prepared.plan.plan_sha256)

    assert result.status is CreateStatus.ROLLED_BACK
    assert result.rollback_succeeded is False
    assert result.diagnostics[-1].code == "GOAL_PROGRESS_ROLLBACK_FAILED"
    assert (vault / prepared.plan.relative_path).is_file()


def test_typed_drafts_reject_raw_storage_and_observation_fingerprint_fields() -> None:
    with pytest.raises(GoalProgressValidationError):
        GoalProgressDefinitionDraftV1.from_dict(
            {
                "id": "0198f4c5-6a00-7000-8000-000000000302",
                "goal_source_uuid": GOAL_ID,
                "goal_identity_fingerprint": "sha256:" + "a" * 64,
                "definition_reviewed_at": FIXED_REVIEWED_AT,
                "progress_model": "numeric_target",
                "numeric_target": None,
                "milestone_set": None,
                "supersedes_definition_id": None,
            }
        )
    with pytest.raises(GoalProgressValidationError):
        GoalProgressObservationDraftV1.from_dict(
            {
                "goal_source_uuid": GOAL_ID,
                "goal_identity_fingerprint": "sha256:" + "a" * 64,
                "progress_definition_id": "0198f4c5-6a00-7000-8000-000000000302",
                "definition_fingerprint": "sha256:" + "b" * 64,
                "progress_model": "numeric_target",
                "observed_at": "unknown",
                "observed_at_precision": "unknown",
                "observation_reviewed_at": FIXED_REVIEWED_AT,
                "numeric_observation": {
                    "metric_id": "weight",
                    "unit": "kg",
                    "value": "1",
                },
                "milestone_observation": None,
                "supersedes_observation_id": None,
            }
        )
