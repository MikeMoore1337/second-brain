"""Provider-free Stage 12C Goal Progress read-model tests."""

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
    MilestoneObservationV1,
    MilestoneSetDefinitionV1,
    MilestoneStateV1,
    MilestoneV1,
    NumericDirectionV1,
    NumericObservationV1,
    NumericTargetDefinitionV1,
    ProgressModelV1,
    goal_progress_hash_json,
    parse_definition_record,
)
from second_brain.application.goal_progress_read import (
    BuildGoalProgress,
    GoalProgressExclusionReasonV1,
    GoalProgressGoalRequiredError,
    GoalProgressInvalidRequestError,
    GoalProgressRequestV1,
    GoalProgressStatusV1,
)
from second_brain.application.goal_progress_safe_write import (
    GoalProgressDefinitionDraftV1,
    GoalProgressObservationDraftV1,
    GoalProgressSafeWrite,
    GoalProgressSafeWriteResult,
)
from second_brain.application.growth import (
    BuildGrowthGoalContext,
    GrowthEngineRequestV1,
    GrowthGoalSelectionModeV1,
    GrowthGoalSelectionV1,
)
from second_brain.application.ports import VaultReader
from second_brain.application.writes import CreateStatus
from tests.conftest import create_vault, write_note

GOAL_ID = "0198f4c5-6a00-7000-8000-000000000301"
GOAL_B_ID = "0198f4c5-6a00-7000-8000-000000000312"
DEFINITION_ID = "0198f4c5-6a00-7000-8000-000000000302"
NUMERIC_OLD_ID = "0198f4c5-6a00-7000-8000-000000000303"
NUMERIC_CURRENT_ID = "0198f4c5-6a00-7000-8000-000000000304"
UNKNOWN_ID = "0198f4c5-6a00-7000-8000-000000000305"
FUTURE_ID = "0198f4c5-6a00-7000-8000-000000000306"
BEFORE_DEFINITION_ID = "0198f4c5-6a00-7000-8000-000000000307"
MILESTONE_DEFINITION_ID = "0198f4c5-6a00-7000-8000-000000000308"
MILESTONE_START_ID = "0198f4c5-6a00-7000-8000-000000000309"
MILESTONE_FINISH_ID = "0198f4c5-6a00-7000-8000-000000000310"
DUPLICATE_EVENT_ID = "0198f4c5-6a00-7000-8000-000000000311"
DEFINITION_B_ID = "0198f4c5-6a00-7000-8000-000000000313"
OBSERVATION_B_ID = "0198f4c5-6a00-7000-8000-000000000314"

AS_OF = datetime(2026, 9, 10, 12, 0, tzinfo=UTC)


def _goal_note(*, goal_id: str = GOAL_ID, body: str = "Цель пользователя.") -> str:
    return f"""---
id: {goal_id}
type: zettel
created: "2026-09-05T12:00:00+03:00"
tags: []
second_brain_personal_memory: 1
evidence_kind: user_statement
self_kind: goal
evidence_at: "2026-09-05T10:00:00Z"
evidence_at_precision: exact
domain: work
---
{body}
"""


def _render_note(fields: dict[str, object], *, body: str = "# Companion record\n") -> str:
    lines = ["---"]
    for key, value in fields.items():
        if key == "milestones":
            assert isinstance(value, list)
            lines.append("milestones:")
            for milestone in value:
                assert isinstance(milestone, dict)
                lines.extend(
                    [
                        f"  - id: {milestone['id']}",
                        f'    label: "{milestone["label"]}"',
                        f"    ordinal: {milestone['ordinal']}",
                    ]
                )
        elif isinstance(value, str):
            lines.append(f'{key}: "{value}"')
        elif isinstance(value, list):
            lines.append(f"{key}: []")
        else:
            lines.append(f"{key}: {value}")
    return "\n".join([*lines, "---", body])


def _goal_identity_hash(vault: Path, *, goal_id: str = GOAL_ID) -> str:
    context = BuildGrowthGoalContext(
        FileSystemVaultReader(vault),
        clock=lambda: AS_OF,
    ).execute(
        GrowthEngineRequestV1(
            selection=GrowthGoalSelectionV1(
                GrowthGoalSelectionModeV1.SELECTED_GOAL,
                UUID(goal_id),
            )
        )
    )
    assert len(context.goals) == 1
    return goal_progress_hash_json(context.goals[0].as_dict())


def _definition_fields(
    goal_hash: str,
    *,
    goal_id: str = GOAL_ID,
    record_id: str = DEFINITION_ID,
    model: str = "numeric_target",
    reviewed_at: str = "2026-09-05T11:00:00Z",
) -> dict[str, object]:
    fields: dict[str, object] = {
        "id": record_id,
        "type": "zettel",
        "created": "2026-09-05T12:00:00+03:00",
        "tags": [],
        "second_brain_goal_progress": 1,
        "goal_progress_kind": "definition",
        "goal_source_uuid": goal_id,
        "goal_identity_fingerprint": goal_hash,
        "goal_progress_policy_fingerprint": GOAL_PROGRESS_POLICY_FINGERPRINT,
        "definition_reviewed_at": reviewed_at,
        "progress_model": model,
    }
    if model == "numeric_target":
        fields.update(
            {
                "metric_id": "weight",
                "unit": "kg",
                "baseline": "80.000",
                "target": "72.000",
                "direction": "decrease_to",
                "lower_bound": "40.000",
                "upper_bound": "120.000",
            }
        )
    else:
        fields.update(
            {
                "ordering": "display_only_v1",
                "milestones": [
                    {"id": "start", "label": "Начать", "ordinal": 1},
                    {"id": "finish", "label": "Завершить", "ordinal": 2},
                ],
            }
        )
    return fields


def _observation_fields(
    goal_hash: str,
    definition: DefinitionRecordV1,
    *,
    goal_id: str = GOAL_ID,
    record_id: str,
    observed_at: str,
    value: str = "76.000",
    model: str = "numeric_target",
    supersedes: str | None = None,
    milestone_id: str = "start",
    state: str = "completed",
) -> dict[str, object]:
    fields: dict[str, object] = {
        "id": record_id,
        "type": "zettel",
        "created": "2026-09-06T12:00:00+03:00",
        "tags": [],
        "second_brain_goal_progress": 1,
        "goal_progress_kind": "observation",
        "goal_source_uuid": goal_id,
        "goal_identity_fingerprint": goal_hash,
        "goal_progress_policy_fingerprint": GOAL_PROGRESS_POLICY_FINGERPRINT,
        "progress_definition_id": str(definition.id),
        "definition_fingerprint": str(definition.definition_fingerprint),
        "progress_model": model,
        "observed_at": observed_at,
        "observed_at_precision": "unknown" if observed_at == "unknown" else "exact",
        "observation_reviewed_at": "2026-09-06T13:00:00Z",
    }
    if model == "numeric_target":
        fields.update({"metric_id": "weight", "unit": "kg", "value": value})
    else:
        fields.update({"milestone_id": milestone_id, "state": state})
    if supersedes is not None:
        fields["supersedes_observation_id"] = supersedes
    return fields


def _write_companion(vault: Path, filename: str, fields: dict[str, object]) -> None:
    write_note(vault, f"40 Zettelkasten/{filename}.md", _render_note(fields))


def _request(goal_id: str = GOAL_ID) -> GoalProgressRequestV1:
    return GoalProgressRequestV1(UUID(goal_id), AS_OF)


def test_stage12c_a_numeric_current_vault_read_model_is_exact_and_deterministic(
    tmp_path: Path,
) -> None:
    vault = create_vault(tmp_path / "vault-a")
    write_note(vault, "10 Projects/Goal.md", _goal_note())
    goal_hash = _goal_identity_hash(vault)
    definition_fields = _definition_fields(goal_hash)
    definition = parse_definition_record(definition_fields)
    assert definition is not None
    _write_companion(vault, "Definition", definition_fields)
    _write_companion(
        vault,
        "Observation",
        _observation_fields(
            goal_hash,
            definition,
            record_id=NUMERIC_CURRENT_ID,
            observed_at="2026-09-06T10:00:00Z",
        ),
    )

    result = BuildGoalProgress(FileSystemVaultReader(vault)).execute(_request())

    assert result.status is GoalProgressStatusV1.TOWARD_TARGET
    assert result.progress_model == "numeric_target"
    assert result.current_observation_uuids == (UUID(NUMERIC_CURRENT_ID),)
    assert result.eligible_count == 1
    assert result.excluded_observations == ()
    assert result.provenance is not None
    assert result.provenance.source == "current_vault"
    assert result.provenance.provider == "none"
    assert result.provenance.network == "none"
    assert result.provenance.write == "none"
    assert "Цель пользователя" not in result.to_json()
    assert (
        result.to_json()
        == BuildGoalProgress(FileSystemVaultReader(vault)).execute(_request()).to_json()
    )


def test_stage12c_b_temporal_and_supersession_classification_is_bounded(
    tmp_path: Path,
) -> None:
    vault = create_vault(tmp_path / "vault-b")
    write_note(vault, "10 Projects/Goal.md", _goal_note())
    goal_hash = _goal_identity_hash(vault)
    definition_fields = _definition_fields(goal_hash)
    definition = parse_definition_record(definition_fields)
    assert definition is not None
    _write_companion(vault, "Definition", definition_fields)
    _write_companion(
        vault,
        "OldObservation",
        _observation_fields(
            goal_hash,
            definition,
            record_id=NUMERIC_OLD_ID,
            observed_at="2026-09-07T10:00:00Z",
            value="78.000",
        ),
    )
    _write_companion(
        vault,
        "CurrentObservation",
        _observation_fields(
            goal_hash,
            definition,
            record_id=NUMERIC_CURRENT_ID,
            observed_at="2026-09-07T10:00:00Z",
            value="76.000",
            supersedes=NUMERIC_OLD_ID,
        ),
    )
    _write_companion(
        vault,
        "UnknownObservation",
        _observation_fields(
            goal_hash,
            definition,
            record_id=UNKNOWN_ID,
            observed_at="unknown",
        ),
    )
    _write_companion(
        vault,
        "FutureObservation",
        _observation_fields(
            goal_hash,
            definition,
            record_id=FUTURE_ID,
            observed_at="2026-09-20T10:00:00Z",
        ),
    )
    _write_companion(
        vault,
        "BeforeDefinitionObservation",
        _observation_fields(
            goal_hash,
            definition,
            record_id=BEFORE_DEFINITION_ID,
            observed_at="2026-09-04T10:00:00Z",
        ),
    )

    result = BuildGoalProgress(FileSystemVaultReader(vault)).execute(_request())

    reasons = {str(item.observation_id): item.reason for item in result.excluded_observations}
    assert result.status is GoalProgressStatusV1.TOWARD_TARGET
    assert result.current_observation_uuids == (UUID(NUMERIC_CURRENT_ID),)
    assert result.eligible_count == 1
    assert result.unknown_time_count == 1
    assert result.superseded_count == 1
    assert reasons[NUMERIC_OLD_ID] is GoalProgressExclusionReasonV1.SUPERSEDED
    assert reasons[UNKNOWN_ID] is GoalProgressExclusionReasonV1.UNKNOWN_TIME
    assert reasons[FUTURE_ID] is GoalProgressExclusionReasonV1.FUTURE_AS_OF
    assert reasons[BEFORE_DEFINITION_ID] is GoalProgressExclusionReasonV1.BEFORE_DEFINITION_REVIEW


def test_stage12c_c_milestones_and_current_goal_drift_fail_closed(
    tmp_path: Path,
) -> None:
    vault = create_vault(tmp_path / "vault-c")
    goal_path = write_note(vault, "10 Projects/Goal.md", _goal_note())
    goal_hash = _goal_identity_hash(vault)
    definition_fields = _definition_fields(
        goal_hash,
        record_id=MILESTONE_DEFINITION_ID,
        model="milestone_set",
    )
    definition = parse_definition_record(definition_fields)
    assert definition is not None
    _write_companion(vault, "MilestoneDefinition", definition_fields)
    _write_companion(
        vault,
        "StartObservation",
        _observation_fields(
            goal_hash,
            definition,
            record_id=MILESTONE_START_ID,
            observed_at="2026-09-06T10:00:00Z",
            model="milestone_set",
            milestone_id="start",
            state="completed",
        ),
    )
    _write_companion(
        vault,
        "FinishObservation",
        _observation_fields(
            goal_hash,
            definition,
            record_id=MILESTONE_FINISH_ID,
            observed_at="2026-09-07T10:00:00Z",
            model="milestone_set",
            milestone_id="finish",
            state="not_completed",
        ),
    )

    result = BuildGoalProgress(FileSystemVaultReader(vault)).execute(_request())

    assert result.status is GoalProgressStatusV1.MILESTONE_OBSERVATIONS_AVAILABLE
    assert result.completed_milestone_ids == ("start",)
    assert result.not_completed_milestone_ids == ("finish",)
    assert result.missing_milestone_ids == ()

    goal_path.write_text(_goal_note(body="Изменённая текущая цель."), encoding="utf-8")
    drifted = BuildGoalProgress(FileSystemVaultReader(vault)).execute(_request())

    assert drifted.status is GoalProgressStatusV1.GOAL_SOURCE_CHANGED
    assert drifted.current_observation_uuids == ()
    assert {item.reason for item in drifted.excluded_observations} == {
        GoalProgressExclusionReasonV1.GOAL_SOURCE_CHANGED
    }


SAFE_NOW = datetime(2026, 9, 14, 12, 0, tzinfo=UTC)
SAFE_REVIEWED_AT = datetime(2026, 9, 14, 10, 0, tzinfo=UTC)


def _install_safe_write_templates(vault: Path) -> None:
    for name in ("Project.md", "Area.md", "Resource.md", "Zettel.md"):
        write_note(vault, f"_templates/{name}", f"# {name}\n")


def _safe_write_service(vault: Path) -> GoalProgressSafeWrite:
    return GoalProgressSafeWrite(
        FileSystemVaultReader(vault),
        FileSystemVaultWriter(vault),
        clock=lambda: SAFE_NOW,
    )


def _safe_write_goal_binding(vault: Path) -> tuple[UUID, str]:
    context = BuildGrowthGoalContext(
        FileSystemVaultReader(vault),
        clock=lambda: SAFE_NOW,
    ).execute(
        GrowthEngineRequestV1(
            selection=GrowthGoalSelectionV1(
                GrowthGoalSelectionModeV1.SELECTED_GOAL,
                UUID(GOAL_ID),
            )
        )
    )
    assert len(context.goals) == 1
    return UUID(GOAL_ID), goal_progress_hash_json(context.goals[0].as_dict())


def _apply_safe_write_plan(
    service: GoalProgressSafeWrite,
    prepared: GoalProgressSafeWriteResult,
) -> UUID:
    plan = prepared.plan
    assert plan is not None
    applied = service.apply(plan, plan.plan_sha256)
    assert applied.status is CreateStatus.CREATED
    return plan.note_id


def test_stage12c_stage12b_safe_write_numeric_round_trip(tmp_path: Path) -> None:
    vault = create_vault(tmp_path / "vault-safe-numeric")
    _install_safe_write_templates(vault)
    write_note(vault, "10 Projects/Goal.md", _goal_note())
    service = _safe_write_service(vault)
    goal_uuid, goal_hash = _safe_write_goal_binding(vault)

    definition = GoalProgressDefinitionDraftV1(
        goal_source_uuid=goal_uuid,
        goal_identity_fingerprint=goal_hash,
        definition_reviewed_at=SAFE_REVIEWED_AT,
        progress_model=ProgressModelV1.NUMERIC_TARGET,
        numeric_target=NumericTargetDefinitionV1(
            metric_id="weight",
            unit="kg",
            baseline="80.000",
            target="72.000",
            direction=NumericDirectionV1.DECREASE_TO,
            lower_bound="40",
            upper_bound="120",
        ),
    )
    definition_result = service.prepare_definition(definition)
    definition_id = _apply_safe_write_plan(service, definition_result)
    observation = GoalProgressObservationDraftV1(
        goal_source_uuid=goal_uuid,
        goal_identity_fingerprint=goal_hash,
        progress_definition_id=definition_id,
        progress_model=ProgressModelV1.NUMERIC_TARGET,
        observed_at=datetime(2026, 9, 14, 11, 0, tzinfo=UTC),
        observed_at_precision="exact",
        observation_reviewed_at=SAFE_REVIEWED_AT,
        numeric_observation=NumericObservationV1("weight", "kg", "76.800"),
    )
    observation_id = _apply_safe_write_plan(service, service.prepare_observation(observation))

    result = BuildGoalProgress(FileSystemVaultReader(vault)).execute(
        GoalProgressRequestV1(goal_uuid, SAFE_NOW)
    )

    assert result.status is GoalProgressStatusV1.TOWARD_TARGET
    assert result.current_observation_uuids == (observation_id,)
    assert result.provenance is not None
    assert result.provenance.write == "none"


def test_stage12c_stage12b_safe_write_milestone_round_trip(tmp_path: Path) -> None:
    vault = create_vault(tmp_path / "vault-safe-milestones")
    _install_safe_write_templates(vault)
    write_note(vault, "10 Projects/Goal.md", _goal_note())
    service = _safe_write_service(vault)
    goal_uuid, goal_hash = _safe_write_goal_binding(vault)
    definition = GoalProgressDefinitionDraftV1(
        goal_source_uuid=goal_uuid,
        goal_identity_fingerprint=goal_hash,
        definition_reviewed_at=SAFE_REVIEWED_AT,
        progress_model=ProgressModelV1.MILESTONE_SET,
        milestone_set=MilestoneSetDefinitionV1(
            ordering="display_only_v1",
            milestones=(
                MilestoneV1("start", "Начать", 1),
                MilestoneV1("finish", "Завершить", 2),
            ),
        ),
    )
    definition_id = _apply_safe_write_plan(service, service.prepare_definition(definition))
    for milestone_id, state, observed_at in (
        ("start", MilestoneStateV1.COMPLETED, datetime(2026, 9, 14, 11, 0, tzinfo=UTC)),
        (
            "finish",
            MilestoneStateV1.NOT_COMPLETED,
            datetime(2026, 9, 14, 11, 30, tzinfo=UTC),
        ),
    ):
        observation = GoalProgressObservationDraftV1(
            goal_source_uuid=goal_uuid,
            goal_identity_fingerprint=goal_hash,
            progress_definition_id=definition_id,
            progress_model=ProgressModelV1.MILESTONE_SET,
            observed_at=observed_at,
            observed_at_precision="exact",
            observation_reviewed_at=SAFE_REVIEWED_AT,
            milestone_observation=MilestoneObservationV1(milestone_id, state),
        )
        _apply_safe_write_plan(service, service.prepare_observation(observation))

    result = BuildGoalProgress(FileSystemVaultReader(vault)).execute(
        GoalProgressRequestV1(goal_uuid, SAFE_NOW)
    )

    assert result.status is GoalProgressStatusV1.MILESTONE_OBSERVATIONS_AVAILABLE
    assert result.completed_milestone_ids == ("start",)
    assert result.not_completed_milestone_ids == ("finish",)


def test_stage12c_request_isolated_from_unrelated_goal_records(tmp_path: Path) -> None:
    vault = create_vault(tmp_path / "vault-isolation")
    write_note(vault, "10 Projects/GoalA.md", _goal_note(goal_id=GOAL_ID))
    write_note(vault, "10 Projects/GoalB.md", _goal_note(goal_id=GOAL_B_ID, body="Чужая цель."))
    goal_a_hash = _goal_identity_hash(vault, goal_id=GOAL_ID)
    goal_b_hash = _goal_identity_hash(vault, goal_id=GOAL_B_ID)
    definition_a_fields = _definition_fields(goal_a_hash)
    definition_b_fields = _definition_fields(
        goal_b_hash,
        goal_id=GOAL_B_ID,
        record_id=DEFINITION_B_ID,
    )
    definition_a = parse_definition_record(definition_a_fields)
    definition_b = parse_definition_record(definition_b_fields)
    assert definition_a is not None
    assert definition_b is not None
    _write_companion(vault, "DefinitionA", definition_a_fields)
    _write_companion(vault, "DefinitionB", definition_b_fields)
    _write_companion(
        vault,
        "ObservationA",
        _observation_fields(
            goal_a_hash,
            definition_a,
            record_id=NUMERIC_CURRENT_ID,
            observed_at="2026-09-06T10:00:00Z",
        ),
    )
    _write_companion(
        vault,
        "ObservationB",
        _observation_fields(
            goal_b_hash,
            definition_b,
            goal_id=GOAL_B_ID,
            record_id=OBSERVATION_B_ID,
            observed_at="2026-09-06T10:00:00Z",
        ),
    )

    result = BuildGoalProgress(FileSystemVaultReader(vault)).execute(_request(GOAL_ID))
    payload = result.to_json()

    assert result.status is GoalProgressStatusV1.TOWARD_TARGET
    assert result.current_observation_uuids == (UUID(NUMERIC_CURRENT_ID),)
    assert OBSERVATION_B_ID not in payload
    assert DEFINITION_B_ID not in payload
    assert GOAL_B_ID not in payload


def test_stage12c_rebuild_is_stable_when_record_filenames_are_reordered(
    tmp_path: Path,
) -> None:
    results: list[str] = []
    for vault_name, definition_name, observation_name in (
        ("vault-order-a", "A-definition", "Z-observation"),
        ("vault-order-b", "Z-definition", "A-observation"),
    ):
        vault = create_vault(tmp_path / vault_name)
        write_note(vault, "10 Projects/Goal.md", _goal_note())
        goal_hash = _goal_identity_hash(vault)
        definition_fields = _definition_fields(goal_hash)
        definition = parse_definition_record(definition_fields)
        assert definition is not None
        _write_companion(vault, definition_name, definition_fields)
        _write_companion(
            vault,
            observation_name,
            _observation_fields(
                goal_hash,
                definition,
                record_id=NUMERIC_CURRENT_ID,
                observed_at="2026-09-06T10:00:00Z",
            ),
        )
        results.append(
            BuildGoalProgress(FileSystemVaultReader(vault)).execute(_request()).to_json()
        )

    assert results[0] == results[1]


def test_stage12c_duplicate_active_event_leaves_are_not_comparable(tmp_path: Path) -> None:
    vault = create_vault(tmp_path / "vault-duplicate")
    write_note(vault, "10 Projects/Goal.md", _goal_note())
    goal_hash = _goal_identity_hash(vault)
    definition_fields = _definition_fields(goal_hash)
    definition = parse_definition_record(definition_fields)
    assert definition is not None
    _write_companion(vault, "Definition", definition_fields)
    for filename, record_id, value in (
        ("FirstObservation", NUMERIC_CURRENT_ID, "76.000"),
        ("SecondObservation", DUPLICATE_EVENT_ID, "75.000"),
    ):
        _write_companion(
            vault,
            filename,
            _observation_fields(
                goal_hash,
                definition,
                record_id=record_id,
                observed_at="2026-09-06T10:00:00Z",
                value=value,
            ),
        )

    result = BuildGoalProgress(FileSystemVaultReader(vault)).execute(_request())

    assert result.status is GoalProgressStatusV1.NOT_COMPARABLE
    assert result.current_observation_uuids == ()
    assert result.eligible_count == 2


def test_stage12c_request_requires_explicit_goal_and_as_of() -> None:
    with pytest.raises(GoalProgressGoalRequiredError):
        BuildGoalProgress(cast(VaultReader, object())).execute(GoalProgressRequestV1(as_of=AS_OF))
    with pytest.raises(GoalProgressInvalidRequestError):
        BuildGoalProgress(cast(VaultReader, object())).execute(
            GoalProgressRequestV1(UUID(GOAL_ID), "2026-09-10T15:00:00+03:00")
        )
