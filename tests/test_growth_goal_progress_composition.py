"""Focused provider-free tests for Cognitive Twin v3 Stage 12D."""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID

import pytest

from second_brain.adapters.vault import FileSystemVaultReader
from second_brain.application.behavioral_observation import BuildBehavioralObservations
from second_brain.application.decision_journal import render_decision_journal_body
from second_brain.application.goal_progress import (
    GOAL_PROGRESS_POLICY_FINGERPRINT,
    DefinitionRecordV1,
    goal_progress_hash_json,
    parse_definition_record,
)
from second_brain.application.goal_progress_read import GoalProgressStatusV1
from second_brain.application.growth import (
    GROWTH_POLICY_FINGERPRINT,
    BuildGrowthEngine,
    BuildGrowthGoalContext,
    GrowthEngineRequestV1,
    GrowthGoalChoiceMappingAcceptanceRequestV1,
    GrowthGoalChoiceMappingSelectorV1,
    GrowthGoalRelationV1,
    GrowthGoalSelectionModeV1,
    GrowthGoalSelectionV1,
    GrowthMappingStore,
    GrowthRelationStateV1,
)
from second_brain.application.growth_goal_progress_composition import (
    COMPOSITION_CAVEATS,
    COMPOSITION_POLICY_FINGERPRINT,
    MAX_COMPOSITION_RESULT_BYTES,
    BuildGrowthGoalProgressCompositionV1,
    GrowthGoalProgressCompositionError,
    GrowthGoalProgressCompositionErrorCodeV1,
    GrowthGoalProgressCompositionRequestV1,
    GrowthGoalProgressCompositionResultV1,
    validate_growth_goal_progress_composition_policy,
    validate_growth_goal_progress_composition_result,
)
from second_brain.application.reports import VaultSnapshot
from second_brain.application.self_model import DEFAULT_SELF_MODEL_POLICY
from tests.conftest import create_vault, snapshot_tree, write_note

GROWTH_AT = datetime(2026, 9, 13, 12, 0, tzinfo=UTC)
PROGRESS_AS_OF = datetime(2026, 9, 13, 14, 0, tzinfo=UTC)
GOAL_ID = UUID("0198f4c5-6a00-7000-8000-000000001201")
GOAL_B_ID = UUID("0198f4c5-6a00-7000-8000-000000001202")
DEFINITION_ID = UUID("0198f4c5-6a00-7000-8000-000000001203")
OBSERVATION_ID = UUID("0198f4c5-6a00-7000-8000-000000001204")
DECISION_IDS = tuple(
    UUID(f"0198f4c5-6a00-7000-8000-0000000012{index:02d}") for index in range(10, 30)
)


def _goal_note(goal_id: UUID = GOAL_ID, *, body: str = "Цель A") -> str:
    return "\n".join(
        (
            "---",
            f"id: {goal_id}",
            "type: zettel",
            "created: 2026-09-13T10:00:00Z",
            "tags: []",
            "second_brain_personal_memory: 1",
            "evidence_kind: user_statement",
            "self_kind: goal",
            "evidence_at: 2026-09-13T09:00:00Z",
            "evidence_at_precision: exact",
            "domain: work",
            "---",
            body,
        )
    )


def _decision_note(
    note_id: UUID,
    *,
    chosen: str = "Alpha",
    situation: str = "Situation",
) -> str:
    body = render_decision_journal_body(
        situation=situation,
        available_options=("Alpha", "Beta"),
        information_known_at_decision_time="Information",
        criteria=("Speed",),
        chosen_option=chosen,
        reasons="A bounded reason.",
        confidence="Medium.",
        expected_result="A bounded result.",
    )
    return "\n".join(
        (
            "---",
            f"id: {note_id}",
            "type: zettel",
            "created: 2026-09-13T10:00:00Z",
            "tags: []",
            "second_brain_personal_memory: 1",
            "evidence_kind: observed_decision",
            "self_kind: decision",
            "evidence_at: 2026-09-13T09:00:00Z",
            "evidence_at_precision: exact",
            "domain: work",
            "---",
            body,
        )
    )


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


def _seed(
    tmp_path: Path,
    *,
    decisions: tuple[tuple[str, str], ...] | None = None,
    include_goal_b: bool = False,
) -> tuple[Path, GrowthMappingStore, FileSystemVaultReader]:
    vault = create_vault(tmp_path / "vault")
    write_note(vault, "10 Projects/Goal-A.md", _goal_note())
    if include_goal_b:
        write_note(vault, "10 Projects/Goal-B.md", _goal_note(GOAL_B_ID, body="Цель A"))
    decision_values = (
        tuple(("Alpha", "Situation") for _ in DECISION_IDS) if decisions is None else decisions
    )
    for index, (chosen, situation) in enumerate(decision_values):
        write_note(
            vault,
            f"10 Projects/Decision-{index}.md",
            _decision_note(DECISION_IDS[index], chosen=chosen, situation=situation),
        )
    return vault, GrowthMappingStore(tmp_path / "growth-goal-mapping"), FileSystemVaultReader(vault)


def _goal_hash(reader: FileSystemVaultReader) -> str:
    context = BuildGrowthGoalContext(
        reader,
        policy=DEFAULT_SELF_MODEL_POLICY,
        clock=lambda: GROWTH_AT,
    ).execute(
        GrowthEngineRequestV1(
            selection=GrowthGoalSelectionV1(GrowthGoalSelectionModeV1.SELECTED_GOAL, GOAL_ID)
        )
    )
    assert len(context.goals) == 1
    return goal_progress_hash_json(context.goals[0].as_dict())


def _numeric_definition_fields(goal_hash: str) -> dict[str, object]:
    return {
        "id": str(DEFINITION_ID),
        "type": "zettel",
        "created": "2026-09-13T10:00:00Z",
        "tags": [],
        "second_brain_goal_progress": 1,
        "goal_progress_kind": "definition",
        "goal_source_uuid": str(GOAL_ID),
        "goal_identity_fingerprint": goal_hash,
        "goal_progress_policy_fingerprint": GOAL_PROGRESS_POLICY_FINGERPRINT,
        "definition_reviewed_at": "2026-09-13T11:00:00Z",
        "progress_model": "numeric_target",
        "metric_id": "weight",
        "unit": "kg",
        "baseline": "80",
        "target": "72",
        "direction": "decrease_to",
        "lower_bound": "40",
        "upper_bound": "120",
    }


def _milestone_definition_fields(goal_hash: str) -> dict[str, object]:
    return {
        "id": str(DEFINITION_ID),
        "type": "zettel",
        "created": "2026-09-13T10:00:00Z",
        "tags": [],
        "second_brain_goal_progress": 1,
        "goal_progress_kind": "definition",
        "goal_source_uuid": str(GOAL_ID),
        "goal_identity_fingerprint": goal_hash,
        "goal_progress_policy_fingerprint": GOAL_PROGRESS_POLICY_FINGERPRINT,
        "definition_reviewed_at": "2026-09-13T11:00:00Z",
        "progress_model": "milestone_set",
        "ordering": "display_only_v1",
        "milestones": [
            {"id": "start", "label": "Начать", "ordinal": 1},
            {"id": "finish", "label": "Завершить", "ordinal": 2},
        ],
    }


def _observation_fields(
    definition: DefinitionRecordV1,
    *,
    value: str = "76",
    milestone: bool = False,
) -> dict[str, object]:
    definition_id = definition.id
    definition_fingerprint = definition.definition_fingerprint
    fields: dict[str, object] = {
        "id": str(OBSERVATION_ID),
        "type": "zettel",
        "created": "2026-09-13T12:00:00Z",
        "tags": [],
        "second_brain_goal_progress": 1,
        "goal_progress_kind": "observation",
        "goal_source_uuid": str(GOAL_ID),
        "goal_identity_fingerprint": _goal_hash_from_definition(definition),
        "goal_progress_policy_fingerprint": GOAL_PROGRESS_POLICY_FINGERPRINT,
        "progress_definition_id": str(definition_id),
        "definition_fingerprint": str(definition_fingerprint),
        "progress_model": "milestone_set" if milestone else "numeric_target",
        "observed_at": "2026-09-13T12:00:00Z",
        "observed_at_precision": "exact",
        "observation_reviewed_at": "2026-09-13T13:00:00Z",
    }
    if milestone:
        fields.update({"milestone_id": "start", "state": "completed"})
    else:
        fields.update({"metric_id": "weight", "unit": "kg", "value": value})
    return fields


def _goal_hash_from_definition(definition: DefinitionRecordV1) -> str:
    return definition.goal_identity_fingerprint


def _write_definition_and_observation(
    vault: Path,
    reader: FileSystemVaultReader,
    *,
    value: str = "76",
    milestone: bool = False,
    with_observation: bool = True,
) -> None:
    goal_hash = _goal_hash(reader)
    fields = (
        _milestone_definition_fields(goal_hash)
        if milestone
        else _numeric_definition_fields(goal_hash)
    )
    definition = parse_definition_record(fields)
    assert definition is not None
    write_note(vault, "40 Zettelkasten/Definition.md", _render_note(fields))
    if with_observation:
        write_note(
            vault,
            "40 Zettelkasten/Observation.md",
            _render_note(
                _observation_fields(definition, value=value, milestone=milestone),
            ),
        )


def _accept_mapping(
    reader: FileSystemVaultReader,
    store: GrowthMappingStore,
    relation: GrowthGoalRelationV1,
) -> None:
    observations = BuildBehavioralObservations(reader, clock=lambda: GROWTH_AT).execute()
    assert observations.cohorts
    cohort = observations.cohorts[0]
    option = cohort.observations[0].chosen_option
    selector = GrowthGoalChoiceMappingSelectorV1(
        source_note_uuid=GOAL_ID,
        behavioral_cohort_fingerprint=cohort.cohort.cohort_fingerprint,
        behavioral_option_index=option.option_index,
        behavioral_option_fingerprint=option.option_fingerprint,
    )
    service = BuildGrowthEngine(reader, store, clock=lambda: GROWTH_AT)
    projection = service.review(selector)
    service.accept(
        GrowthGoalChoiceMappingAcceptanceRequestV1(
            selector=selector,
            operation_id=UUID("0198f4c5-6a00-7000-8000-000000001299"),
            relation=relation,
            confirmed=True,
            review_projection=projection,
        )
    )


def _build(
    reader: FileSystemVaultReader,
    store: GrowthMappingStore,
) -> GrowthGoalProgressCompositionResultV1:
    return BuildGrowthGoalProgressCompositionV1(
        reader=reader,
        store=store,
        policy=DEFAULT_SELF_MODEL_POLICY,
        growth_clock=lambda: GROWTH_AT,
    ).execute(GrowthGoalProgressCompositionRequestV1(GOAL_ID, PROGRESS_AS_OF))


@pytest.mark.parametrize(
    ("relation", "value", "growth_state", "progress_status"),
    (
        (
            GrowthGoalRelationV1.SUPPORTS_GOAL,
            "76",
            GrowthRelationStateV1.SUPPORTS_GOAL,
            GoalProgressStatusV1.TOWARD_TARGET,
        ),
        (
            GrowthGoalRelationV1.SUPPORTS_GOAL,
            "84",
            GrowthRelationStateV1.SUPPORTS_GOAL,
            GoalProgressStatusV1.AWAY_FROM_TARGET,
        ),
        (
            GrowthGoalRelationV1.SUPPORTS_GOAL,
            "72",
            GrowthRelationStateV1.SUPPORTS_GOAL,
            GoalProgressStatusV1.TARGET_MET,
        ),
        (
            GrowthGoalRelationV1.CONFLICTS_WITH_GOAL,
            "76",
            GrowthRelationStateV1.CONFLICTS_WITH_GOAL,
            GoalProgressStatusV1.TOWARD_TARGET,
        ),
        (
            GrowthGoalRelationV1.CONFLICTS_WITH_GOAL,
            "84",
            GrowthRelationStateV1.CONFLICTS_WITH_GOAL,
            GoalProgressStatusV1.AWAY_FROM_TARGET,
        ),
        (
            GrowthGoalRelationV1.NEUTRAL_OR_UNKNOWN,
            "80",
            GrowthRelationStateV1.NEUTRAL_OR_UNKNOWN,
            GoalProgressStatusV1.UNCHANGED,
        ),
    ),
)
def test_composes_independent_growth_and_progress_states(
    tmp_path: Path,
    relation: GrowthGoalRelationV1,
    value: str,
    growth_state: GrowthRelationStateV1,
    progress_status: GoalProgressStatusV1,
) -> None:
    vault, store, reader = _seed(tmp_path)
    _write_definition_and_observation(vault, reader, value=value)
    _accept_mapping(reader, store, relation)

    result = _build(reader, store)

    assert result.growth_result.goal_results[0].state is growth_state
    assert result.growth_result.goal_results[0].mapping is not None
    assert result.goal_progress_result.status is progress_status
    assert (
        result.current_goal_identity_fingerprint
        == result.goal_progress_result.current_goal_identity_fingerprint
    )
    assert result.progress_as_of == PROGRESS_AS_OF
    assert result.growth_result.generated_at == GROWTH_AT
    assert result.goal_progress_result.as_of == PROGRESS_AS_OF
    assert result.growth_result.goal_results[0].advisor is None
    assert result.caveats == COMPOSITION_CAVEATS
    assert "overall_status" not in result.as_dict()
    assert "alignment_score" not in result.as_dict()
    assert "recommendation" not in result.as_dict()


def test_composes_definition_missing_and_missing_mapping_without_synthesis(tmp_path: Path) -> None:
    _vault, store, reader = _seed(tmp_path)
    _accept_mapping(reader, store, GrowthGoalRelationV1.SUPPORTS_GOAL)
    definition_missing = _build(reader, store)
    assert (
        definition_missing.growth_result.goal_results[0].state
        is GrowthRelationStateV1.SUPPORTS_GOAL
    )
    assert definition_missing.goal_progress_result.status is GoalProgressStatusV1.DEFINITION_MISSING

    vault2, store2, reader2 = _seed(tmp_path / "insufficient")
    _write_definition_and_observation(vault2, reader2, with_observation=False)
    insufficient = _build(reader2, store2)
    assert (
        insufficient.growth_result.goal_results[0].state
        is GrowthRelationStateV1.GOAL_MAPPING_MISSING
    )
    assert (
        insufficient.goal_progress_result.status is GoalProgressStatusV1.INSUFFICIENT_OBSERVATIONS
    )


def test_composes_behavioral_insufficient_with_target_met(tmp_path: Path) -> None:
    vault, store, reader = _seed(tmp_path, decisions=())
    _write_definition_and_observation(vault, reader, value="72")

    result = _build(reader, store)

    assert (
        result.growth_result.goal_results[0].state
        is GrowthRelationStateV1.BEHAVIORAL_EVIDENCE_INSUFFICIENT
    )
    assert result.goal_progress_result.status is GoalProgressStatusV1.TARGET_MET


def test_composes_mixed_behavior_with_milestone_observations_available(tmp_path: Path) -> None:
    decisions = (("Alpha", "Situation"), ("Alpha", "Situation"), ("Beta", "Situation"))
    vault, store, reader = _seed(tmp_path, decisions=decisions)
    _write_definition_and_observation(vault, reader, milestone=True)

    result = _build(reader, store)

    assert result.growth_result.goal_results[0].state is GrowthRelationStateV1.MIXED_BEHAVIOR
    assert (
        result.goal_progress_result.status is GoalProgressStatusV1.MILESTONE_OBSERVATIONS_AVAILABLE
    )


def test_selected_goal_preserves_multiple_growth_cohorts_and_one_progress_result(
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

    result = _build(reader, store)

    assert len(result.growth_result.goal_results) == 2
    assert len({item.cohort_fingerprint for item in result.growth_result.goal_results}) == 2
    assert all(
        item.goal is not None and item.goal.source_note_uuid == GOAL_ID
        for item in result.growth_result.goal_results
    )
    assert result.goal_progress_result.selected_goal_source_uuid == GOAL_ID
    assert "aggregate" not in result.to_json()


def test_cross_goal_isolation_excludes_other_goal_ids_and_progress(tmp_path: Path) -> None:
    vault, store, reader = _seed(tmp_path, include_goal_b=True)
    _write_definition_and_observation(vault, reader)
    goal_b_hash = _goal_hash_for(reader, GOAL_B_ID)
    definition_b = _numeric_definition_fields(goal_b_hash)
    definition_b["id"] = "0198f4c5-6a00-7000-8000-000000001205"
    definition_b["goal_source_uuid"] = str(GOAL_B_ID)
    parsed_b = parse_definition_record(definition_b)
    assert parsed_b is not None
    observation_b = _observation_fields(parsed_b, value="75")
    observation_b["id"] = "0198f4c5-6a00-7000-8000-000000001206"
    observation_b["goal_source_uuid"] = str(GOAL_B_ID)
    write_note(vault, "40 Zettelkasten/Definition-B.md", _render_note(definition_b))
    write_note(vault, "40 Zettelkasten/Observation-B.md", _render_note(observation_b))

    result = _build(reader, store)
    encoded = result.to_json()

    assert str(GOAL_ID) in encoded
    assert str(GOAL_B_ID) not in encoded
    assert goal_b_hash not in encoded
    assert "000000001205" not in encoded
    assert "000000001206" not in encoded


def _goal_hash_for(reader: FileSystemVaultReader, goal_id: UUID) -> str:
    context = BuildGrowthGoalContext(
        reader,
        clock=lambda: GROWTH_AT,
    ).execute(
        GrowthEngineRequestV1(
            selection=GrowthGoalSelectionV1(GrowthGoalSelectionModeV1.SELECTED_GOAL, goal_id)
        )
    )
    assert len(context.goals) == 1
    return goal_progress_hash_json(context.goals[0].as_dict())


def test_source_change_between_branch_reads_fails_closed(tmp_path: Path) -> None:
    vault, store, reader = _seed(tmp_path)
    _write_definition_and_observation(vault, reader)
    first_snapshot = reader.scan()
    write_note(vault, "10 Projects/Goal-A.md", _goal_note(body="Changed Goal"))
    second_snapshot = reader.scan()

    class _SequenceReader:
        def __init__(self, snapshots: tuple[VaultSnapshot, ...]) -> None:
            self._snapshots = snapshots
            self._index = 0

        def scan(self) -> VaultSnapshot:
            snapshot = self._snapshots[min(self._index, len(self._snapshots) - 1)]
            self._index += 1
            return snapshot

    sequence_reader = _SequenceReader((first_snapshot, second_snapshot, second_snapshot))
    with pytest.raises(GrowthGoalProgressCompositionError) as raised:
        BuildGrowthGoalProgressCompositionV1(
            reader=sequence_reader,
            store=store,
            policy=DEFAULT_SELF_MODEL_POLICY,
            growth_clock=lambda: GROWTH_AT,
        ).execute(GrowthGoalProgressCompositionRequestV1(GOAL_ID, PROGRESS_AS_OF))
    assert raised.value.code == GrowthGoalProgressCompositionErrorCodeV1.SOURCE_CHANGED.value


def test_same_text_different_uuid_and_changed_identity_fail_binding(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import second_brain.application.growth_goal_progress_composition as composition

    vault, store, reader = _seed(tmp_path, include_goal_b=True)
    _write_definition_and_observation(vault, reader)
    valid = _build(reader, store)
    different_uuid = replace(
        valid.goal_progress_result,
        selected_goal_source_uuid=GOAL_B_ID,
        provenance=None,
    )
    monkeypatch.setattr(
        composition,
        "_build_progress_branch",
        lambda *args, **kwargs: different_uuid,
    )
    with pytest.raises(GrowthGoalProgressCompositionError) as raised:
        _build(reader, store)
    assert raised.value.code == GrowthGoalProgressCompositionErrorCodeV1.GOAL_BINDING_MISMATCH.value

    changed_identity = replace(
        valid.goal_progress_result,
        current_goal_identity_fingerprint="sha256:" + "0" * 64,
        provenance=None,
    )
    monkeypatch.setattr(
        composition,
        "_build_progress_branch",
        lambda *args, **kwargs: changed_identity,
    )
    with pytest.raises(GrowthGoalProgressCompositionError) as raised:
        _build(reader, store)
    assert raised.value.code == GrowthGoalProgressCompositionErrorCodeV1.GOAL_BINDING_MISMATCH.value


def test_goal_progress_source_changed_is_never_composed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import second_brain.application.growth_goal_progress_composition as composition

    vault, store, reader = _seed(tmp_path)
    _write_definition_and_observation(vault, reader)
    valid = _build(reader, store)
    changed = replace(
        valid.goal_progress_result,
        status=GoalProgressStatusV1.GOAL_SOURCE_CHANGED,
        provenance=None,
    )
    monkeypatch.setattr(composition, "_build_progress_branch", lambda *args, **kwargs: changed)

    with pytest.raises(GrowthGoalProgressCompositionError) as raised:
        _build(reader, store)
    assert raised.value.code == GrowthGoalProgressCompositionErrorCodeV1.SOURCE_CHANGED.value


def test_same_data_has_stable_serialization_and_semantic_fingerprint(tmp_path: Path) -> None:
    vault, store, reader = _seed(tmp_path)
    _write_definition_and_observation(vault, reader)
    first = _build(reader, store)
    before = snapshot_tree(vault)
    second = _build(FileSystemVaultReader(vault), store)

    assert first.to_json() == second.to_json()
    assert first.semantic_fingerprint == second.semantic_fingerprint
    assert first.to_json() == first.to_json()
    assert snapshot_tree(vault) == before
    assert len(first.to_json().encode("utf-8")) < MAX_COMPOSITION_RESULT_BYTES


def test_policy_and_result_size_are_fixed_and_fail_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert validate_growth_goal_progress_composition_policy() == COMPOSITION_POLICY_FINGERPRINT
    vault, store, reader = _seed(tmp_path)
    _write_definition_and_observation(vault, reader)
    result = _build(reader, store)
    monkeypatch.setattr(
        "second_brain.application.growth_goal_progress_composition.MAX_COMPOSITION_RESULT_BYTES",
        len(result.to_json().encode("utf-8")) - 1,
    )
    with pytest.raises(GrowthGoalProgressCompositionError) as raised:
        validate_growth_goal_progress_composition_result(result)
    assert raised.value.code == GrowthGoalProgressCompositionErrorCodeV1.RESULT_TOO_LARGE.value


def test_advisor_learning_and_writer_boundaries_are_not_invoked(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from second_brain.application import growth_advisor, growth_learning

    calls = {"advisor": 0, "learning": 0, "writer": 0}

    def _advisor(*args: object, **kwargs: object) -> Any:
        calls["advisor"] += 1
        raise AssertionError("Advisor must not be called")

    def _learning(*args: object, **kwargs: object) -> Any:
        calls["learning"] += 1
        raise AssertionError("Learning must not be called")

    def _writer(*args: object, **kwargs: object) -> Any:
        calls["writer"] += 1
        raise AssertionError("Writer must not be called")

    monkeypatch.setattr(growth_advisor.BuildGrowthAdvisor, "execute", _advisor)
    monkeypatch.setattr(growth_learning.BuildGrowthLearningQuestion, "execute", _learning)
    monkeypatch.setattr(GrowthMappingStore, "append_mapping", _writer)
    vault, store, reader = _seed(tmp_path)
    _write_definition_and_observation(vault, reader)

    result = _build(reader, store)

    assert result.goal_progress_result.status is GoalProgressStatusV1.TOWARD_TARGET
    assert calls == {"advisor": 0, "learning": 0, "writer": 0}


def test_request_is_exact_and_invalid_request_does_not_read_source(tmp_path: Path) -> None:
    class _ExplodingReader:
        def scan(self) -> VaultSnapshot:
            raise AssertionError("invalid requests must fail before scan")

    with pytest.raises(GrowthGoalProgressCompositionError) as raised:
        BuildGrowthGoalProgressCompositionV1(
            reader=_ExplodingReader(),
            store=GrowthMappingStore(tmp_path / "store"),
            policy=DEFAULT_SELF_MODEL_POLICY,
            growth_clock=lambda: GROWTH_AT,
        ).execute(GrowthGoalProgressCompositionRequestV1(None, PROGRESS_AS_OF))
    assert raised.value.code == GrowthGoalProgressCompositionErrorCodeV1.INVALID_REQUEST.value


def test_composition_result_keeps_only_branch_status_namespaces(tmp_path: Path) -> None:
    vault, store, reader = _seed(tmp_path)
    _write_definition_and_observation(vault, reader)
    result = _build(reader, store)
    payload = json.loads(result.to_json())

    forbidden_keys = {
        "overall_status",
        "alignment_score",
        "effectiveness",
        "recommendation",
        "winner",
        "best_choice",
        "goal_fit",
        "percentage",
        "caused_by",
    }

    def _keys(value: object) -> set[str]:
        if isinstance(value, dict):
            return set(value) | {key for item in value.values() for key in _keys(item)}
        if isinstance(value, list):
            return {key for item in value for key in _keys(item)}
        return set()

    assert forbidden_keys.isdisjoint(_keys(payload))
    assert payload["growth_result"]["policy_fingerprint"] == GROWTH_POLICY_FINGERPRINT
    assert (
        payload["goal_progress_result"]["goal_progress_policy_fingerprint"]
        == GOAL_PROGRESS_POLICY_FINGERPRINT
    )
