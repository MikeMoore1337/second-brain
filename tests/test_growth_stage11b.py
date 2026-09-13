"""Focused provider-free tests for Cognitive Twin v2 Stage 11B."""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

import pytest

from second_brain.adapters.vault import FileSystemVaultReader
from second_brain.application.behavioral_observation import BuildBehavioralObservations
from second_brain.application.decision_journal import render_decision_journal_body
from second_brain.application.growth import (
    GROWTH_MAPPING_POLICY_FINGERPRINT,
    BuildGrowthEngine,
    GrowthGoalChoiceMappingAcceptanceRequestV1,
    GrowthGoalChoiceMappingSelectorV1,
    GrowthGoalRelationV1,
    GrowthMappingConflictError,
    GrowthMappingLifecycleStateV1,
    GrowthMappingReviewRequestV1,
    GrowthMappingStore,
    GrowthMappingStoreCorruptError,
    GrowthRelationStateV1,
    create_growth_mapping_store,
    validate_growth_mapping_policy,
)
from second_brain.application.personal_memory import PERSONAL_MEMORY_MARKER
from tests.conftest import create_vault, write_note

GENERATED_AT = datetime(2026, 9, 13, 12, 0, tzinfo=UTC)
GOAL_ID = UUID("0198f4c5-6a00-7000-8000-000000000200")
DECISION_IDS = tuple(
    UUID(f"0198f4c5-6a00-7000-8000-0000000002{index:02d}") for index in range(1, 4)
)
OPERATION_ID = UUID("0198f4c5-6a00-7000-8000-000000000299")


def _goal_note() -> str:
    return "\n".join(
        (
            "---",
            f"id: {GOAL_ID}",
            "type: zettel",
            "created: 2026-09-13T10:00:00Z",
            "tags: []",
            f"{PERSONAL_MEMORY_MARKER}: 1",
            "evidence_kind: user_statement",
            "self_kind: goal",
            "evidence_at: 2026-09-13T09:00:00Z",
            "evidence_at_precision: exact",
            "domain: work",
            "---",
            "PRIVATE GOAL BODY",
        )
    )


def _decision_note(note_id: UUID, *, chosen: str = "PRIVATE ALPHA") -> str:
    body = render_decision_journal_body(
        situation="PRIVATE SITUATION",
        available_options=("PRIVATE ALPHA", "PRIVATE BETA"),
        information_known_at_decision_time="PRIVATE INFORMATION",
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
            f"{PERSONAL_MEMORY_MARKER}: 1",
            "evidence_kind: observed_decision",
            "self_kind: decision",
            "evidence_at: 2026-09-13T09:00:00Z",
            "evidence_at_precision: exact",
            "domain: work",
            "---",
            body,
        )
    )


def _seed(tmp_path: Path) -> tuple[Path, GrowthMappingStore, GrowthGoalChoiceMappingSelectorV1]:
    vault = create_vault(tmp_path / "vault")
    write_note(vault, "10 Projects/Goal.md", _goal_note())
    for index, note_id in enumerate(DECISION_IDS):
        write_note(vault, f"10 Projects/Decision-{index}.md", _decision_note(note_id))
    reader = FileSystemVaultReader(vault)
    observations = BuildBehavioralObservations(reader, clock=lambda: GENERATED_AT).execute()
    cohort = observations.cohorts[0]
    option = cohort.observations[0].chosen_option
    selector = GrowthGoalChoiceMappingSelectorV1(
        source_note_uuid=GOAL_ID,
        behavioral_cohort_fingerprint=cohort.cohort.cohort_fingerprint,
        behavioral_option_index=option.option_index,
        behavioral_option_fingerprint=option.option_fingerprint,
    )
    return vault, GrowthMappingStore(tmp_path / "growth-goal-mapping"), selector


def test_policy_and_lazy_store_initialization(tmp_path: Path) -> None:
    assert validate_growth_mapping_policy() == GROWTH_MAPPING_POLICY_FINGERPRINT
    store = GrowthMappingStore(tmp_path / "store")
    assert store.read_active() == ()
    assert not (tmp_path / "store").exists()
    assert store.verify() is None
    assert store.read_active() == ()
    assert not (tmp_path / "store").exists()


def test_review_accept_build_retry_and_no_private_durable_text(tmp_path: Path) -> None:
    vault, store, selector = _seed(tmp_path)
    service = BuildGrowthEngine(
        FileSystemVaultReader(vault),
        store,
        clock=lambda: GENERATED_AT,
    )
    projection = service.review(GrowthMappingReviewRequestV1(selector))
    assert projection.claim_text == "PRIVATE GOAL BODY"
    assert projection.cohort_domain == "work"
    assert projection.proposed_relation is None

    request = GrowthGoalChoiceMappingAcceptanceRequestV1(
        selector=selector,
        operation_id=OPERATION_ID,
        relation=GrowthGoalRelationV1.SUPPORTS_GOAL,
        confirmed=True,
        review_projection=projection,
    )
    mapping = service.accept(request)
    assert mapping.relation is GrowthGoalRelationV1.SUPPORTS_GOAL
    assert service.accept(request) == mapping
    result = service.execute()
    assert result.goal_results[0].state is GrowthRelationStateV1.SUPPORTS_GOAL
    assert result.goal_results[0].mapping is not None
    durable = (store.records_path).read_text(encoding="utf-8")
    assert "PRIVATE GOAL BODY" not in durable
    assert "PRIVATE SITUATION" not in durable
    assert "PRIVATE ALPHA" not in durable
    json.loads(durable.splitlines()[0])


def test_exact_tuple_conflict_needs_new_review(tmp_path: Path) -> None:
    vault, store, selector = _seed(tmp_path)
    service = BuildGrowthEngine(FileSystemVaultReader(vault), store, clock=lambda: GENERATED_AT)
    projection = service.review(selector)
    first = service.accept(
        GrowthGoalChoiceMappingAcceptanceRequestV1(
            selector=selector,
            operation_id=OPERATION_ID,
            relation=GrowthGoalRelationV1.NEUTRAL_OR_UNKNOWN,
            confirmed=True,
            review_projection=projection,
        )
    )
    assert first.mapping_id
    with pytest.raises(GrowthMappingConflictError):
        service.accept(
            GrowthGoalChoiceMappingAcceptanceRequestV1(
                selector=selector,
                operation_id=UUID("0198f4c5-6a00-7000-8000-000000000298"),
                relation=GrowthGoalRelationV1.CONFLICTS_WITH_GOAL,
                confirmed=True,
                review_projection=projection,
            )
        )


def test_correction_is_new_record_and_lifecycle_is_explicit(tmp_path: Path) -> None:
    vault, store, selector = _seed(tmp_path)
    service = BuildGrowthEngine(FileSystemVaultReader(vault), store, clock=lambda: GENERATED_AT)
    projection = service.review(selector)
    first = service.accept(
        GrowthGoalChoiceMappingAcceptanceRequestV1(
            selector=selector,
            operation_id=OPERATION_ID,
            relation=GrowthGoalRelationV1.SUPPORTS_GOAL,
            confirmed=True,
            review_projection=projection,
        )
    )
    second = service.accept(
        GrowthGoalChoiceMappingAcceptanceRequestV1(
            selector=selector,
            operation_id=UUID("0198f4c5-6a00-7000-8000-000000000297"),
            relation=GrowthGoalRelationV1.CONFLICTS_WITH_GOAL,
            confirmed=True,
            review_projection=projection,
            supersedes_mapping_id=first.mapping_id,
        )
    )
    assert second.mapping_id != first.mapping_id
    views = store.read_verified_snapshot()
    assert len(views) == 2
    assert (
        next(item for item in views if item.mapping_id == first.mapping_id).lifecycle_state
        is GrowthMappingLifecycleStateV1.SUPERSEDED
    )
    assert (
        next(item for item in views if item.mapping_id == second.mapping_id).lifecycle_state
        is GrowthMappingLifecycleStateV1.ACTIVE
    )
    assert service.execute().goal_results[0].state is GrowthRelationStateV1.CONFLICTS_WITH_GOAL
    store.invalidate_mapping(
        second.mapping_id,
        operation_id=UUID("0198f4c5-6a00-7000-8000-000000000296"),
    )
    assert service.execute().goal_results[0].state is GrowthRelationStateV1.GOAL_MAPPING_MISSING


def test_unmapped_changed_exact_option_is_missing_not_conflict(tmp_path: Path) -> None:
    vault, store, selector = _seed(tmp_path)
    service = BuildGrowthEngine(FileSystemVaultReader(vault), store, clock=lambda: GENERATED_AT)
    projection = service.review(selector)
    service.accept(
        GrowthGoalChoiceMappingAcceptanceRequestV1(
            selector=selector,
            operation_id=OPERATION_ID,
            relation=GrowthGoalRelationV1.SUPPORTS_GOAL,
            confirmed=True,
            review_projection=projection,
        )
    )
    for index, note_id in enumerate(DECISION_IDS):
        write_note(
            vault,
            f"10 Projects/Decision-{index}.md",
            _decision_note(note_id, chosen="PRIVATE BETA"),
        )
    result = service.execute()
    assert result.goal_results[0].state is GrowthRelationStateV1.GOAL_MAPPING_MISSING
    assert result.goal_results[0].reason_codes == ("GROWTH_GOAL_MAPPING_MISSING",)


def test_missing_or_mismatched_goal_domain_is_not_comparable(tmp_path: Path) -> None:
    vault, store, _selector = _seed(tmp_path)
    write_note(vault, "10 Projects/Goal.md", _goal_note().replace("domain: work", "domain: life"))
    result = BuildGrowthEngine(
        FileSystemVaultReader(vault),
        store,
        clock=lambda: GENERATED_AT,
    ).execute()
    assert result.goal_results[0].state is GrowthRelationStateV1.NOT_COMPARABLE
    assert result.goal_results[0].reason_codes == ("GROWTH_UNSUPPORTED_SEMANTIC_COMPARISON",)


def test_partial_line_fails_closed(tmp_path: Path) -> None:
    _vault, store, _selector = _seed(tmp_path)
    store.root.mkdir(mode=0o700)
    store.records_path.write_text('{"partial": true}', encoding="utf-8")
    store.manifest_path.write_text("{}\n", encoding="utf-8")
    os.chmod(store.records_path, 0o600)
    os.chmod(store.manifest_path, 0o600)
    with pytest.raises(GrowthMappingStoreCorruptError):
        store.read_active()


def test_env_file_derives_only_sibling_store(tmp_path: Path) -> None:
    env_file = tmp_path / "runtime" / "web.env"
    env_file.parent.mkdir()
    env_file.write_text("PRIVATE=not-read\n", encoding="utf-8")
    store = create_growth_mapping_store(env_file)
    assert store.root == env_file.parent / "growth-goal-mapping"
