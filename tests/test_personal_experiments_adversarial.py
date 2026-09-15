"""Adversarial Stage 14 security, isolation and Safe Write regressions."""

from __future__ import annotations

import re
from concurrent.futures import ThreadPoolExecutor
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
    PERSONAL_EXPERIMENT_MARKER,
    PersonalExperimentBaselineStrategyV1,
    PersonalExperimentDefinitionRecordV1,
    PersonalExperimentLifecycleEventV1,
    PersonalExperimentRecordError,
    parse_personal_experiment_record,
    validate_personal_experiment_definition_chain,
)
from second_brain.application.personal_experiments_safe_write import (
    PersonalExperimentDefinitionDraftV1,
    PersonalExperimentLifecycleDraftV1,
    PersonalExperimentObservationDraftV1,
    PersonalExperimentReviewStore,
    PersonalExperimentSafeWrite,
    PersonalExperimentSafeWriteError,
    PersonalExperimentSafeWriteResult,
)
from second_brain.application.reports import VaultSnapshot
from second_brain.application.writes import CreateStatus
from tests.conftest import create_vault, snapshot_tree, write_note
from tests.test_personal_experiments_evaluator import (
    FIXED_NOW,
    _activate,
    _enroll,
    _experiment_definition,
    _goal_note,
    _install_templates,
    _stage12_definition,
    _stage14_service,
)
from tests.test_personal_experiments_safe_write import _definition_draft

SECOND_GOAL_ID = UUID("0198f4c5-6a00-7000-8000-000000000902")
SECOND_EXPERIMENT_REPLACEMENT_ID = UUID("0198f4c5-6a00-7000-8000-000000000906")


def _second_goal_note() -> str:
    return f"""---
id: {SECOND_GOAL_ID}
type: zettel
created: 2026-09-14T06:01:00Z
tags: []
second_brain_personal_memory: 1
evidence_kind: user_statement
self_kind: goal
evidence_at: 2026-09-14T05:01:00Z
evidence_at_precision: exact
domain: work
---
Хочу завершить рабочий проект.
"""


def _goal_bindings(vault: Path) -> dict[UUID, str]:
    context = BuildGrowthGoalContext(
        FileSystemVaultReader(vault),
        clock=lambda: FIXED_NOW,
    ).execute(GrowthEngineRequestV1())
    return {
        UUID(str(goal.source_note_uuid)): growth_hash_json(goal.as_dict()) for goal in context.goals
    }


def _stage12_definition_for(vault: Path, goal_id: UUID) -> DefinitionRecordV1:
    bindings = _goal_bindings(vault)
    service = GoalProgressSafeWrite(
        FileSystemVaultReader(vault),
        FileSystemVaultWriter(vault),
        clock=lambda: FIXED_NOW,
    )
    prepared = service.prepare_definition(
        GoalProgressDefinitionDraftV1(
            goal_source_uuid=goal_id,
            goal_identity_fingerprint=bindings[goal_id],
            definition_reviewed_at="2026-09-14T07:00:00Z",
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
    applied = service.apply(prepared.plan, prepared.plan.plan_sha256)
    assert applied.status is CreateStatus.CREATED
    return cast(DefinitionRecordV1, prepared.plan.record)


def _stage12_observation_for(
    vault: Path,
    definition: DefinitionRecordV1,
    goal_id: UUID,
) -> ObservationRecordV1:
    bindings = _goal_bindings(vault)
    service = GoalProgressSafeWrite(
        FileSystemVaultReader(vault),
        FileSystemVaultWriter(vault),
        clock=lambda: FIXED_NOW,
    )
    prepared = service.prepare_observation(
        GoalProgressObservationDraftV1(
            goal_source_uuid=goal_id,
            goal_identity_fingerprint=bindings[goal_id],
            progress_definition_id=UUID(str(definition.id)),
            progress_model=ProgressModelV1.NUMERIC_TARGET,
            observed_at="2026-09-14T10:00:00Z",
            observed_at_precision="exact",
            observation_reviewed_at="2026-09-14T10:30:00Z",
            numeric_observation=NumericObservationV1("hours", "h", "15"),
        )
    )
    assert prepared.plan is not None, prepared.diagnostics
    applied = service.apply(prepared.plan, prepared.plan.plan_sha256)
    assert applied.status is CreateStatus.CREATED
    return cast(ObservationRecordV1, prepared.plan.record)


def _personal_definition_for(
    vault: Path,
    goal_id: UUID,
    stage12_definition: DefinitionRecordV1,
    *,
    hypothesis: str,
) -> PersonalExperimentDefinitionRecordV1:
    prepared = PersonalExperimentSafeWrite(
        FileSystemVaultReader(vault),
        FileSystemVaultWriter(vault),
        clock=lambda: FIXED_NOW,
    ).prepare_definition(
        PersonalExperimentDefinitionDraftV1(
            goal_source_uuid=goal_id,
            goal_identity_fingerprint=_goal_bindings(vault)[goal_id],
            goal_progress_definition_id=cast(UUID, stage12_definition.id),
            goal_progress_definition_fingerprint=stage12_definition.definition_fingerprint,
            hypothesis=hypothesis,
            intervention="Выполнить отдельное действие для этой цели.",
            baseline_strategy=PersonalExperimentBaselineStrategyV1.STAGE12_DEFINITION_EXPLICIT,
        )
    )
    assert prepared.plan is not None, prepared.diagnostics
    service = _stage14_service(vault)
    assert service.apply(prepared.plan, prepared.plan.plan_sha256).status is CreateStatus.CREATED
    return cast(PersonalExperimentDefinitionRecordV1, prepared.plan.record)


def test_exact_goal_identity_rejects_same_text_cross_goal_and_wrong_stage12(
    tmp_path: Path,
) -> None:
    """Equal labels never substitute for the exact Goal and Stage 12 identity."""

    vault = create_vault(tmp_path / "vault")
    _install_templates(vault)
    write_note(vault, "10 Projects/Goal-one.md", _goal_note())
    write_note(vault, "10 Projects/Goal-two.md", _second_goal_note())
    bindings = _goal_bindings(vault)
    first_goal_id = UUID("0198f4c5-6a00-7000-8000-000000000901")
    first_definition = _stage12_definition_for(vault, first_goal_id)
    service = _stage14_service(vault)

    cross_goal = PersonalExperimentDefinitionDraftV1(
        goal_source_uuid=SECOND_GOAL_ID,
        goal_identity_fingerprint=bindings[SECOND_GOAL_ID],
        goal_progress_definition_id=cast(UUID, first_definition.id),
        goal_progress_definition_fingerprint=first_definition.definition_fingerprint,
        hypothesis="Проверить точную привязку цели.",
        intervention="Не подменять одну цель другой.",
        baseline_strategy=PersonalExperimentBaselineStrategyV1.STAGE12_DEFINITION_EXPLICIT,
    )
    result = service.prepare_definition(cross_goal)
    assert result.status is CreateStatus.REJECTED
    assert result.diagnostics[0].code == "PERSONAL_EXPERIMENT_STAGE12_DEFINITION_REQUIRED"

    wrong_goal_fingerprint = replace(
        cross_goal,
        goal_identity_fingerprint=bindings[first_goal_id],
    )
    changed = service.prepare_definition(wrong_goal_fingerprint)
    assert changed.status is CreateStatus.REJECTED
    assert changed.diagnostics[0].code == "PERSONAL_EXPERIMENT_GOAL_SOURCE_CHANGED"

    second_definition = _stage12_definition_for(vault, SECOND_GOAL_ID)
    wrong_stage12 = replace(
        cross_goal,
        goal_source_uuid=first_goal_id,
        goal_identity_fingerprint=bindings[first_goal_id],
        goal_progress_definition_id=cast(UUID, second_definition.id),
        goal_progress_definition_fingerprint=second_definition.definition_fingerprint,
    )
    wrong_stage12_result = service.prepare_definition(wrong_stage12)
    assert wrong_stage12_result.status is CreateStatus.REJECTED
    assert (
        wrong_stage12_result.diagnostics[0].code == "PERSONAL_EXPERIMENT_STAGE12_DEFINITION_CHANGED"
    )
    assert not any(vault.rglob("personal-experiment-definition-*.md"))


def test_cross_experiment_observation_and_stale_source_are_rejected(tmp_path: Path) -> None:
    """An enrollment must belong to the exact experiment's Goal/Stage 12 source."""

    vault = create_vault(tmp_path / "vault")
    _install_templates(vault)
    write_note(vault, "10 Projects/Goal-one.md", _goal_note())
    write_note(vault, "10 Projects/Goal-two.md", _second_goal_note())
    first_goal_id = UUID("0198f4c5-6a00-7000-8000-000000000901")
    first_definition = _stage12_definition_for(vault, first_goal_id)
    second_definition = _stage12_definition_for(vault, SECOND_GOAL_ID)
    first_source = _stage12_observation_for(vault, first_definition, first_goal_id)
    second_source = _stage12_observation_for(vault, second_definition, SECOND_GOAL_ID)
    first_experiment = _personal_definition_for(
        vault,
        first_goal_id,
        first_definition,
        hypothesis="Проверить первую точную цель.",
    )
    second_experiment = _personal_definition_for(
        vault,
        SECOND_GOAL_ID,
        second_definition,
        hypothesis="Проверить вторую точную цель.",
    )
    _activate(vault, first_experiment)
    _enroll(vault, first_experiment, first_source)
    service = _stage14_service(vault)

    cross_experiment = service.prepare_observation(
        PersonalExperimentObservationDraftV1(
            experiment_definition_id=cast(UUID, second_experiment.id),
            experiment_definition_fingerprint=second_experiment.experiment_definition_fingerprint,
            stage12_observation_id=cast(UUID, first_source.id),
            stage12_observation_fingerprint=first_source.observation_fingerprint,
        )
    )
    assert cross_experiment.status is CreateStatus.REJECTED
    assert cross_experiment.diagnostics[0].code == "PERSONAL_EXPERIMENT_SOURCE_CHANGED"

    stale = service.prepare_observation(
        PersonalExperimentObservationDraftV1(
            experiment_definition_id=cast(UUID, second_experiment.id),
            experiment_definition_fingerprint=second_experiment.experiment_definition_fingerprint,
            stage12_observation_id=cast(UUID, second_source.id),
            stage12_observation_fingerprint="sha256:" + "0" * 64,
        )
    )
    assert stale.status is CreateStatus.REJECTED
    assert stale.diagnostics[0].code == "PERSONAL_EXPERIMENT_SOURCE_CHANGED"


def test_lifecycle_guards_duplicate_activation_and_second_active_goal(tmp_path: Path) -> None:
    """Lifecycle transitions and the one-active-per-Goal rule fail closed."""

    vault = create_vault(tmp_path / "vault")
    _install_templates(vault)
    write_note(vault, "10 Projects/Goal.md", _goal_note())
    stage12 = _stage12_definition(vault)
    definition = _experiment_definition(vault, stage12)
    service = _stage14_service(vault)

    for event in (
        PersonalExperimentLifecycleEventV1.COMPLETION,
        PersonalExperimentLifecycleEventV1.CANCELLATION,
    ):
        before_activation = service.prepare_lifecycle(
            PersonalExperimentLifecycleDraftV1(
                experiment_definition_id=cast(UUID, definition.id),
                experiment_definition_fingerprint=definition.experiment_definition_fingerprint,
                lifecycle_event=event,
                event_at="2026-09-14T10:00:00Z",
            )
        )
        assert before_activation.status is CreateStatus.REJECTED
        assert before_activation.diagnostics[0].code == "PERSONAL_EXPERIMENT_LIFECYCLE_REQUIRED"

    activation = _activate(vault, definition)
    duplicate = service.prepare_lifecycle(
        PersonalExperimentLifecycleDraftV1(
            experiment_definition_id=cast(UUID, definition.id),
            experiment_definition_fingerprint=definition.experiment_definition_fingerprint,
            lifecycle_event=PersonalExperimentLifecycleEventV1.ACTIVATION,
            event_at="2026-09-14T09:30:00Z",
        )
    )
    assert duplicate.status is CreateStatus.REJECTED
    assert duplicate.diagnostics[0].code == "PERSONAL_EXPERIMENT_LIFECYCLE_CONFLICT"

    # Seed a valid append-only replacement to emulate an existing historical
    # definition whose predecessor remains actively running.
    replacement = replace(
        definition,
        id=SECOND_EXPERIMENT_REPLACEMENT_ID,
        hypothesis="Проверить замену после активного эксперимента.",
        supersedes_definition_id=definition.id,
        supersedes_definition_fingerprint=definition.experiment_definition_fingerprint,
        definition_reviewed_at="2026-09-14T12:30:00Z",
    )
    report_snapshot = FileSystemVaultReader(vault).scan()
    assert report_snapshot.manifest is not None
    writer = FileSystemVaultWriter(vault)
    physical = writer.prepare_personal_experiment(
        report_snapshot.manifest,
        replacement,
        f"personal-experiment-definition-{replacement.id}",
        cast(UUID, replacement.id),
        datetime(2026, 9, 14, 12, 30, tzinfo=UTC),
    )
    writer.write(physical)
    assert validate_personal_experiment_definition_chain(
        (definition, replacement)
    ).active_records == (replacement,)

    second_activation = service.prepare_lifecycle(
        PersonalExperimentLifecycleDraftV1(
            experiment_definition_id=cast(UUID, replacement.id),
            experiment_definition_fingerprint=replacement.experiment_definition_fingerprint,
            lifecycle_event=PersonalExperimentLifecycleEventV1.ACTIVATION,
            event_at="2026-09-14T13:00:00Z",
        )
    )
    assert second_activation.status is CreateStatus.REJECTED
    assert second_activation.diagnostics[0].code == "PERSONAL_EXPERIMENT_ONE_ACTIVE_PER_GOAL"
    assert activation.id != replacement.id


def test_safe_write_rejects_path_tampering_and_malformed_marker(tmp_path: Path) -> None:
    """A reviewed plan cannot be redirected or made marker-ambiguous."""

    vault = create_vault(tmp_path / "vault")
    _install_templates(vault)
    write_note(vault, "10 Projects/Goal.md", _goal_note())
    stage12 = _stage12_definition(vault)
    service = _stage14_service(vault)
    prepared = service.prepare_definition(_definition_draft(vault, stage12))
    assert prepared.plan is not None, prepared.diagnostics
    plan = prepared.plan
    before = snapshot_tree(vault)

    with pytest.raises(ValueError):
        replace(plan, relative_path="../escape.md")
    assert snapshot_tree(vault) == before
    assert not (vault.parent / "escape.md").exists()

    raw = plan.record.as_dict()
    raw[PERSONAL_EXPERIMENT_MARKER] = True
    assert parse_personal_experiment_record(raw) is None
    with pytest.raises(PersonalExperimentSafeWriteError):
        PersonalExperimentDefinitionDraftV1.from_dict(raw)
    with pytest.raises(PersonalExperimentRecordError):
        parse_personal_experiment_record(
            {PERSONAL_EXPERIMENT_MARKER: 1, "personal_experiment_kind": "nope"}
        )


def test_interrupted_post_write_validation_rolls_back_the_publication(tmp_path: Path) -> None:
    """A failed reread cannot leave a partially accepted Stage 14 note."""

    vault = create_vault(tmp_path / "vault")
    _install_templates(vault)
    write_note(vault, "10 Projects/Goal.md", _goal_note())
    stage12 = _stage12_definition(vault)
    stale_snapshot = FileSystemVaultReader(vault).scan()

    class StaleReader:
        def scan(self) -> VaultSnapshot:
            return stale_snapshot

    service = PersonalExperimentSafeWrite(
        reader=StaleReader(),
        writer=FileSystemVaultWriter(vault),
        clock=lambda: FIXED_NOW,
    )
    prepared = service.prepare_definition(_definition_draft(vault, stage12))
    assert prepared.plan is not None, prepared.diagnostics
    result = service.apply(prepared.plan, prepared.plan.plan_sha256)
    assert result.status is CreateStatus.ROLLED_BACK
    assert result.rollback_succeeded is True
    assert not (vault / prepared.plan.relative_path).exists()


def test_reviewed_apply_is_at_most_once_under_concurrency(tmp_path: Path) -> None:
    """Two simultaneous applies of one review token publish at most one note."""

    vault = create_vault(tmp_path / "vault")
    _install_templates(vault)
    write_note(vault, "10 Projects/Goal.md", _goal_note())
    stage12 = _stage12_definition(vault)
    service = _stage14_service(vault)
    prepared = service.prepare_definition(_definition_draft(vault, stage12))
    assert prepared.plan is not None, prepared.diagnostics
    plan = prepared.plan
    store = PersonalExperimentReviewStore()
    token = store.issue("owner-1", plan)

    def apply_once() -> PersonalExperimentSafeWriteResult:
        return service.apply_reviewed(
            "owner-1",
            token,
            plan.plan_sha256,
            store,
            confirmed=True,
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = tuple(pool.map(lambda _: apply_once(), range(2)))

    assert sum(result.status is CreateStatus.CREATED for result in results) == 1
    assert sum(result.status is CreateStatus.REJECTED for result in results) == 1
    assert (vault / plan.relative_path).is_file()


def test_stage14_private_sources_have_no_persistence_or_private_logging() -> None:
    """The UI/API boundary remains memory-only and never logs private payloads."""

    root = Path(__file__).parents[1]
    source_paths = (
        root / "web" / "src" / "personal-experiments-api.ts",
        root / "web" / "src" / "personal-experiments-surface.tsx",
        root / "src" / "second_brain" / "entrypoints" / "web" / "personal_experiments.py",
    )
    source = "\n".join(path.read_text(encoding="utf-8") for path in source_paths)
    for forbidden in (
        "localStorage",
        "sessionStorage",
        "indexedDB",
        "Cache Storage",
        "serviceWorker",
    ):
        assert forbidden.casefold() not in source.casefold()
    assert re.search(r"\bconsole\s*\.", source) is None
    assert re.search(r"\b(?:logger|logging|print)\s*\(", source) is None

    worker = (root / "web" / "src" / "sw.ts").read_text(encoding="utf-8")
    assert "isApiOrAuthPath" in worker
    assert "personal-experiments" not in worker
    assert "caches.open(" not in worker
    assert "caches.put(" not in worker
