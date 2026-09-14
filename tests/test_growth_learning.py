"""Provider-free focused tests for Cognitive Twin v2 Stage 11D."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID

import pytest

from second_brain.adapters.vault import FileSystemVaultReader
from second_brain.application.decision_journal import render_decision_journal_body
from second_brain.application.growth import (
    GROWTH_POLICY_FINGERPRINT,
    GROWTH_POLICY_ID,
    BuildGrowthEngine,
    GrowthEngineRequestV1,
    GrowthEngineResultV1,
    GrowthGoalChoiceMappingAcceptanceRequestV1,
    GrowthGoalChoiceMappingSelectorV1,
    GrowthGoalRelationV1,
    GrowthGoalSelectionModeV1,
    GrowthGoalSelectionV1,
    GrowthMappingStore,
    GrowthRelationStateV1,
    growth_hash_json,
)
from second_brain.application.growth_learning import (
    GROWTH_LEARNING_POLICY_FINGERPRINT,
    GROWTH_LEARNING_POLICY_ID,
    BuildGrowthLearningQuestion,
    GrowthLearningAnswerDraftV1,
    GrowthLearningAnswerTooLargeError,
    GrowthLearningCandidateAlreadyResolvedError,
    GrowthLearningCandidateExpiredError,
    GrowthLearningDispositionV1,
    GrowthLearningHandoffKindV1,
    GrowthLearningInvalidResolutionError,
    GrowthLearningKindV1,
    GrowthLearningNoCandidateCodeV1,
    GrowthLearningOperationV1,
    GrowthLearningReasonCodeV1,
    GrowthLearningRequestV1,
    GrowthLearningResolutionV1,
    GrowthLearningStaleCandidateError,
    GrowthLearningStatusV1,
    build_growth_learning_question,
    prepare_growth_learning_personal_memory_handoff,
    resolve_growth_learning_question,
    serialize_growth_learning_candidate,
    serialize_growth_learning_result,
    validate_growth_learning_candidate,
    validate_growth_learning_policy,
)
from second_brain.application.personal_memory import PERSONAL_MEMORY_MARKER
from tests.conftest import create_vault, write_note

NOW = datetime(2026, 9, 14, 12, 0, tzinfo=UTC)
GOAL_ID = UUID("0198f4c5-6a00-7000-8000-000000000200")
DECISION_IDS = tuple(
    UUID(f"0198f4c5-6a00-7000-8000-0000000002{index:02d}") for index in range(1, 4)
)


def _goal_note() -> str:
    return "\n".join(
        (
            "---",
            f"id: {GOAL_ID}",
            "type: zettel",
            "created: 2026-09-14T10:00:00Z",
            "tags: []",
            f"{PERSONAL_MEMORY_MARKER}: 1",
            "evidence_kind: user_statement",
            "self_kind: goal",
            "evidence_at: 2026-09-14T09:00:00Z",
            "evidence_at_precision: exact",
            "domain: work",
            "---",
            "PRIVATE GOAL BODY",
        )
    )


def _decision_note(note_id: UUID) -> str:
    body = render_decision_journal_body(
        situation="PRIVATE SITUATION",
        available_options=("PRIVATE ALPHA", "PRIVATE BETA"),
        information_known_at_decision_time="PRIVATE INFORMATION",
        criteria=("Speed",),
        chosen_option="PRIVATE ALPHA",
        reasons="A bounded reason.",
        confidence="Medium.",
        expected_result="A bounded result.",
    )
    return "\n".join(
        (
            "---",
            f"id: {note_id}",
            "type: zettel",
            "created: 2026-09-14T10:00:00Z",
            "tags: []",
            f"{PERSONAL_MEMORY_MARKER}: 1",
            "evidence_kind: observed_decision",
            "self_kind: decision",
            "evidence_at: 2026-09-14T09:00:00Z",
            "evidence_at_precision: exact",
            "domain: work",
            "---",
            body,
        )
    )


def _growth_fixture(tmp_path: Path) -> tuple[BuildGrowthEngine, GrowthMappingStore]:
    vault = create_vault(tmp_path / "vault")
    write_note(vault, "10 Projects/Goal.md", _goal_note())
    for index, note_id in enumerate(DECISION_IDS):
        write_note(vault, f"10 Projects/Decision-{index}.md", _decision_note(note_id))
    store = GrowthMappingStore(tmp_path / "growth-goal-mapping", clock=lambda: NOW)
    engine = BuildGrowthEngine(
        FileSystemVaultReader(vault),
        store,
        clock=lambda: NOW,
    )
    return engine, store


def _growth_request() -> GrowthEngineRequestV1:
    return GrowthEngineRequestV1(
        selection=GrowthGoalSelectionV1(GrowthGoalSelectionModeV1.SELECTED_GOAL, GOAL_ID)
    )


def _missing_result(
    tmp_path: Path,
) -> tuple[BuildGrowthEngine, GrowthMappingStore, GrowthEngineResultV1]:
    engine, store = _growth_fixture(tmp_path)
    return engine, store, engine.execute(_growth_request())


def _conflict_result(
    tmp_path: Path,
) -> tuple[BuildGrowthEngine, GrowthMappingStore, GrowthEngineResultV1]:
    engine, store, missing = _missing_result(tmp_path)
    relation = missing.goal_results[0]
    assert relation.behavioral_option is not None
    assert relation.cohort_fingerprint is not None
    selector = GrowthGoalChoiceMappingSelectorV1(
        source_note_uuid=GOAL_ID,
        behavioral_cohort_fingerprint=relation.cohort_fingerprint,
        behavioral_option_index=relation.behavioral_option.option_index,
        behavioral_option_fingerprint=relation.behavioral_option.option_fingerprint,
    )
    projection = engine.review(selector)
    engine.accept(
        GrowthGoalChoiceMappingAcceptanceRequestV1(
            selector=selector,
            operation_id=UUID("0198f4c5-6a00-7000-8000-000000000299"),
            relation=GrowthGoalRelationV1.CONFLICTS_WITH_GOAL,
            confirmed=True,
            review_projection=projection,
        )
    )
    return engine, store, engine.execute(_growth_request())


def _variant(result: GrowthEngineResultV1, state: GrowthRelationStateV1) -> GrowthEngineResultV1:
    relation = result.goal_results[0]
    if state in {
        GrowthRelationStateV1.MIXED_BEHAVIOR,
        GrowthRelationStateV1.CHANGED_BEHAVIOR,
    }:
        assert relation.behavioral_pattern is not None
        pattern = replace(
            relation.behavioral_pattern,
            current_option=None,
            reference_fingerprint=growth_hash_json({"variant": state.value}),
        )
        relation = replace(
            relation,
            state=state,
            behavioral_pattern=pattern,
            behavioral_option=None,
            mapping=None,
        )
    elif state is GrowthRelationStateV1.BEHAVIORAL_EVIDENCE_INSUFFICIENT:
        relation = replace(
            relation,
            state=state,
            behavioral_pattern=None,
            behavioral_option=None,
            cohort_fingerprint=None,
            mapping=None,
        )
    elif state is GrowthRelationStateV1.GOAL_SOURCE_MISSING:
        relation = replace(relation, state=state, goal=None)
    elif state is GrowthRelationStateV1.CONFLICTS_WITH_GOAL:
        relation = replace(relation, state=state)
    else:
        relation = replace(relation, state=state, mapping=None)
    if state is GrowthRelationStateV1.GOAL_SELECTION_REQUIRED:
        return replace(
            result,
            selection_mode=GrowthGoalSelectionModeV1.EACH_CURRENT_GOAL,
            selected_goal_source_uuid=None,
            goal_results=(relation,),
        )
    return replace(result, goal_results=(relation,))


@pytest.mark.parametrize(
    ("state", "candidate_kind", "reason", "no_candidate"),
    [
        (
            GrowthRelationStateV1.GOAL_MAPPING_MISSING,
            GrowthLearningKindV1.RELATION_REVIEW,
            GrowthLearningReasonCodeV1.MISSING_GOAL_MAPPING,
            None,
        ),
        (
            GrowthRelationStateV1.CONFLICTS_WITH_GOAL,
            GrowthLearningKindV1.REFLECTION,
            GrowthLearningReasonCodeV1.EXPLICIT_GOAL_CONFLICT,
            None,
        ),
        (
            GrowthRelationStateV1.MIXED_BEHAVIOR,
            GrowthLearningKindV1.CONTEXT_CLARIFICATION,
            GrowthLearningReasonCodeV1.MIXED_BEHAVIOR,
            None,
        ),
        (
            GrowthRelationStateV1.CHANGED_BEHAVIOR,
            GrowthLearningKindV1.CONTEXT_CLARIFICATION,
            GrowthLearningReasonCodeV1.CHANGED_BEHAVIOR,
            None,
        ),
        (
            GrowthRelationStateV1.BEHAVIORAL_EVIDENCE_INSUFFICIENT,
            GrowthLearningKindV1.EVIDENCE_CLARIFICATION,
            GrowthLearningReasonCodeV1.BEHAVIORAL_EVIDENCE_INSUFFICIENT,
            None,
        ),
        (
            GrowthRelationStateV1.SUPPORTS_GOAL,
            None,
            None,
            GrowthLearningNoCandidateCodeV1.NO_ACTIONABLE_GROWTH_GAP,
        ),
        (
            GrowthRelationStateV1.NEUTRAL_OR_UNKNOWN,
            None,
            None,
            GrowthLearningNoCandidateCodeV1.NO_ACTIONABLE_GROWTH_GAP,
        ),
        (
            GrowthRelationStateV1.NOT_COMPARABLE,
            None,
            None,
            GrowthLearningNoCandidateCodeV1.GROWTH_STATE_NOT_COMPARABLE,
        ),
        (
            GrowthRelationStateV1.GOAL_SOURCE_MISSING,
            None,
            None,
            GrowthLearningNoCandidateCodeV1.GOAL_SOURCE_MISSING,
        ),
        (
            GrowthRelationStateV1.GOAL_SELECTION_REQUIRED,
            None,
            None,
            GrowthLearningNoCandidateCodeV1.GOAL_SELECTION_REQUIRED,
        ),
    ],
)
def test_all_growth_states_have_closed_learning_outcome(
    tmp_path: Path,
    state: GrowthRelationStateV1,
    candidate_kind: GrowthLearningKindV1 | None,
    reason: GrowthLearningReasonCodeV1 | None,
    no_candidate: GrowthLearningNoCandidateCodeV1 | None,
) -> None:
    if state is GrowthRelationStateV1.CONFLICTS_WITH_GOAL:
        _engine, _store, result = _conflict_result(tmp_path)
    else:
        _engine, _store, result = _missing_result(tmp_path)
    result = _variant(result, state)
    learning = build_growth_learning_question(result, questions_enabled=True, now=NOW)
    if candidate_kind is not None:
        assert learning.status is GrowthLearningStatusV1.CANDIDATE
        assert learning.candidate is not None
        assert learning.candidate.kind is candidate_kind
        assert learning.candidate.reason_code is reason
        if state is GrowthRelationStateV1.GOAL_MAPPING_MISSING:
            assert learning.candidate.mapping_id is None
            assert learning.candidate.mapping_fingerprint is None
    else:
        assert learning.status is GrowthLearningStatusV1.NO_CANDIDATE
        assert learning.candidate is None
        assert learning.no_candidate_code is no_candidate


def test_precedence_and_deterministic_identity(tmp_path: Path) -> None:
    _engine, _store, result = _missing_result(tmp_path)
    first = result.goal_results[0]
    assert first.behavioral_pattern is not None
    mixed_pattern = replace(
        first.behavioral_pattern,
        current_option=None,
        reference_fingerprint=growth_hash_json({"mixed": True}),
    )
    mixed = replace(
        first,
        state=GrowthRelationStateV1.MIXED_BEHAVIOR,
        behavioral_pattern=mixed_pattern,
        behavioral_option=None,
        mapping=None,
    )
    combined = replace(result, goal_results=(first, mixed))
    # Mapping missing wins over mixed, and time is not part of identity.
    first_candidate = build_growth_learning_question(
        combined, questions_enabled=True, now=NOW
    ).candidate
    later_candidate = build_growth_learning_question(
        combined,
        questions_enabled=True,
        now=NOW + timedelta(seconds=7),
    ).candidate
    assert first_candidate is not None
    assert first_candidate.reason_code is GrowthLearningReasonCodeV1.MISSING_GOAL_MAPPING
    assert later_candidate is not None
    assert later_candidate.candidate_id == first_candidate.candidate_id
    assert later_candidate.basis_fingerprint == first_candidate.basis_fingerprint
    assert later_candidate.issued_at != first_candidate.issued_at


def test_policy_candidate_and_result_hashes_are_bounded(tmp_path: Path) -> None:
    assert validate_growth_learning_policy() == GROWTH_LEARNING_POLICY_FINGERPRINT
    _engine, _store, result = _missing_result(tmp_path)
    learning = build_growth_learning_question(result, questions_enabled=True, now=NOW)
    assert learning.candidate is not None
    candidate = learning.candidate
    assert candidate.growth_policy_id == GROWTH_POLICY_ID
    assert candidate.growth_policy_fingerprint == GROWTH_POLICY_FINGERPRINT
    assert GROWTH_LEARNING_POLICY_ID == "growth-learning-foreground-question-v1"
    assert candidate.contract_version == "growth-learning-v1"
    assert candidate.derivation_version == "growth-learning-derivation-v1"
    assert candidate.candidate_id.startswith("gl1:")
    assert len(serialize_growth_learning_candidate(candidate)) <= 16 * 1024
    assert len(serialize_growth_learning_result(learning)) <= 32 * 1024
    assert "PRIVATE" not in candidate.to_json()
    validate_growth_learning_candidate(candidate)


def test_explicit_engine_request_rebuilds_selected_goal_and_review_is_read_only(
    tmp_path: Path,
) -> None:
    engine, store, _result = _missing_result(tmp_path)
    runtime = BuildGrowthLearningQuestion(growth_engine=engine, clock=lambda: NOW)
    learning = runtime.execute(GrowthLearningRequestV1(goal_source_uuid=GOAL_ID))
    assert learning.candidate is not None
    assert store.read_active() == ()
    resolved = runtime.resolve(
        GrowthLearningRequestV1(goal_source_uuid=GOAL_ID),
        learning.candidate,
        GrowthLearningResolutionV1(
            candidate_id=learning.candidate.candidate_id,
            disposition=GrowthLearningDispositionV1.REVIEW,
            answer=None,
        ),
        now=NOW + timedelta(seconds=1),
    )
    assert resolved.handoff is not None
    assert resolved.handoff.kind is GrowthLearningHandoffKindV1.RELATION_REVIEW
    assert resolved.handoff.selector is not None
    assert resolved.handoff.review_projection is not None
    assert store.read_active() == ()


def test_resolution_controls_are_ephemeral_and_expiry_is_exact(tmp_path: Path) -> None:
    _engine, _store, result = _missing_result(tmp_path)
    candidate = build_growth_learning_question(result, questions_enabled=True, now=NOW).candidate
    assert candidate is not None
    operation = GrowthLearningOperationV1()
    issued = operation.build(result, questions_enabled=True, now=NOW)
    assert issued.candidate is not None
    ignored = operation.resolve(
        result,
        candidate,
        GrowthLearningResolutionV1(candidate.candidate_id, GrowthLearningDispositionV1.IGNORE),
        now=NOW + timedelta(microseconds=1),
    )
    assert ignored.answer_draft is None
    assert ignored.handoff is None
    assert ignored.next_state.terminal is True
    with pytest.raises(GrowthLearningCandidateAlreadyResolvedError):
        operation.resolve(
            result,
            candidate,
            GrowthLearningResolutionV1(candidate.candidate_id, GrowthLearningDispositionV1.REJECT),
            now=NOW + timedelta(seconds=1),
        )
    with pytest.raises(GrowthLearningCandidateExpiredError):
        resolve_growth_learning_question(
            result,
            candidate,
            GrowthLearningResolutionV1(candidate.candidate_id, GrowthLearningDispositionV1.IGNORE),
            now=NOW + timedelta(seconds=600),
        )


def test_answer_boundary_and_personal_memory_handoff_do_not_write(tmp_path: Path) -> None:
    _engine, store, result = _missing_result(tmp_path)
    mixed = _variant(result, GrowthRelationStateV1.MIXED_BEHAVIOR)
    candidate = build_growth_learning_question(mixed, questions_enabled=True, now=NOW).candidate
    assert candidate is not None
    answer = "x" * 4096
    resolved = resolve_growth_learning_question(
        mixed,
        candidate,
        GrowthLearningResolutionV1(
            candidate.candidate_id, GrowthLearningDispositionV1.ANSWER, answer
        ),
        now=NOW + timedelta(seconds=1),
    )
    assert resolved.answer_draft is not None
    assert len(resolved.answer_draft.text.encode("utf-8")) == 4096
    assert (
        prepare_growth_learning_personal_memory_handoff(candidate, resolved.answer_draft).kind
        is GrowthLearningHandoffKindV1.PERSONAL_MEMORY_REVIEW
    )
    assert store.read_active() == ()
    with pytest.raises(GrowthLearningAnswerTooLargeError):
        resolve_growth_learning_question(
            mixed,
            candidate,
            GrowthLearningResolutionV1(
                candidate.candidate_id,
                GrowthLearningDispositionV1.ANSWER,
                "x" * 4097,
            ),
            now=NOW + timedelta(seconds=1),
        )
    with pytest.raises(GrowthLearningInvalidResolutionError):
        prepare_growth_learning_personal_memory_handoff(
            candidate,
            GrowthLearningAnswerDraftV1(candidate.candidate_id, " context "),
        )


def test_candidate_becomes_stale_on_goal_behavior_or_mapping_drift(tmp_path: Path) -> None:
    _engine, _store, result = _missing_result(tmp_path)
    candidate = build_growth_learning_question(result, questions_enabled=True, now=NOW).candidate
    assert candidate is not None
    original_goal = result.goal_results[0].goal
    assert original_goal is not None
    changed_goal = replace(original_goal, claim_fingerprint=growth_hash_json({"changed": True}))
    changed_result = replace(
        result,
        goal_results=(replace(result.goal_results[0], goal=changed_goal),),
    )
    with pytest.raises(GrowthLearningStaleCandidateError):
        resolve_growth_learning_question(
            changed_result,
            candidate,
            GrowthLearningResolutionV1(candidate.candidate_id, GrowthLearningDispositionV1.IGNORE),
            now=NOW + timedelta(seconds=1),
        )

    conflict_engine, _conflict_store, conflict = _conflict_result(tmp_path / "conflict")
    conflict_candidate = build_growth_learning_question(
        conflict, questions_enabled=True, now=NOW
    ).candidate
    assert conflict_candidate is not None
    current_mapping = conflict.goal_results[0].mapping
    assert current_mapping is not None
    mappingless = replace(
        conflict,
        goal_results=(
            replace(
                conflict.goal_results[0],
                state=GrowthRelationStateV1.GOAL_MAPPING_MISSING,
                mapping=None,
            ),
        ),
    )
    with pytest.raises(GrowthLearningStaleCandidateError):
        resolve_growth_learning_question(
            mappingless,
            conflict_candidate,
            GrowthLearningResolutionV1(
                conflict_candidate.candidate_id,
                GrowthLearningDispositionV1.IGNORE,
            ),
            now=NOW + timedelta(seconds=1),
        )
    assert conflict_engine is not None
