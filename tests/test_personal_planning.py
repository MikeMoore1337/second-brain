"""Focused Phase 17.1 tests for the provider-free planning context pack."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from typing import cast
from uuid import UUID, uuid7

import pytest

from second_brain.application.executive_strategy import (
    POLICY_FINGERPRINT as STAGE16_POLICY_FINGERPRINT,
)
from second_brain.application.executive_strategy import (
    POLICY_ID as STAGE16_POLICY_ID,
)
from second_brain.application.executive_strategy import (
    ExecutiveActionCandidateV1,
    ExecutiveActionKindV1,
    ExecutiveResultStateV1,
    ExecutiveSourceAliasV1,
    StrategyProposalV1,
    StrategySnapshotV1,
    _new_proposal,
    build_reviewed_action,
    build_strategy_snapshot,
    goal_identity_fingerprint,
)
from second_brain.application.growth import GrowthGoalIdentityV1
from second_brain.application.personal_planning import (
    PLANNING_POLICY_CANONICAL_JSON,
    PLANNING_POLICY_FINGERPRINT,
    PLANNING_POLICY_ID,
    PersonalPlanningError,
    PlanningContextPackV1,
    PlanningGoalSelectionV1,
    PlanningPackReadinessV1,
    PlanningWindowKindV1,
    PlanningWindowV1,
    aggregate_planning_readiness,
    build_planning_context_pack,
    serialize_planning_context_pack,
    validate_planning_policy,
)

NOW = datetime(2026, 9, 16, 5, 30, tzinfo=UTC)


def _goal() -> GrowthGoalIdentityV1:
    return GrowthGoalIdentityV1(
        source_note_uuid=uuid7(),
        dimension="goal",
        source_evidence_kind="user_statement",
        source_self_kind="goal",
        domain="health",
        evidence_at="unknown",
        evidence_at_precision="unknown",
        source_contract_version="self-model-v1",
        source_derivation_version="self-model-derivation-v1",
        self_model_policy_fingerprint="0" * 64,
        source_fingerprint="sha256:" + "1" * 64,
        claim_fingerprint="sha256:" + "2" * 64,
    )


def _snapshot(goal: GrowthGoalIdentityV1, *, sequence: int = 1) -> StrategySnapshotV1:
    candidate = ExecutiveActionCandidateV1(
        action_id="action-1",
        kind=ExecutiveActionKindV1.ACT,
        title="Небольшой шаг",
        description="Сделать ограниченный шаг по выбранной цели.",
        basis_aliases=(ExecutiveSourceAliasV1.GOAL_CURRENT,),
        goal_relation="Прямо связан с выбранной целью.",
        expected_observable_signal="Появится наблюдаемый результат.",
        prerequisites=(),
        caveats=(),
    )
    reviewed = build_reviewed_action(candidate)
    proposal: StrategyProposalV1 = _new_proposal(
        proposal_id=uuid7(),
        as_of=NOW,
        result_state=ExecutiveResultStateV1.PROPOSAL,
        goal_source_uuid=cast(UUID, goal.source_note_uuid),
        goal_identity_fingerprint=goal_identity_fingerprint(goal),
        source_pack_fingerprint="a" * 64,
        candidates=(candidate,),
        suggested_order=(candidate.action_id,),
        reasons=("Тестовое основание.",),
        caveats=(),
        provider_fingerprint="b" * 64,
    )
    return build_strategy_snapshot(
        proposal,
        (reviewed,),
        sequence=sequence,
        reviewed_at=NOW,
        accepted_at=NOW,
        snapshot_id=uuid7(),
    )


def _selection(goal: GrowthGoalIdentityV1 | None = None) -> PlanningGoalSelectionV1:
    selected_goal = goal or _goal()
    return PlanningGoalSelectionV1(
        goal=selected_goal,
        goal_text="Улучшить выносливость",
        strategy_snapshot=_snapshot(selected_goal),
        selected_action_ids=("action-1",),
    )


def _pack(*selections: PlanningGoalSelectionV1) -> PlanningContextPackV1:
    return build_planning_context_pack(
        selections,
        start_local="2026-09-16",
        end_local="2026-09-18",
        timezone="UTC",
        available_minutes_by_date={
            "2026-09-18": 20,
            "2026-09-16": 30,
            "2026-09-17": 0,
        },
        fixed_windows=(
            PlanningWindowV1(
                window_id="fixed-1",
                kind=PlanningWindowKindV1.FIXED_COMMITMENT,
                title="Фиксированная встреча",
                start_local="2026-09-16T12:00",
                end_local="2026-09-16T13:00",
            ),
        ),
        planning_constraints=("Не планировать больше одного шага подряд.",),
        planning_context="На этой неделе доступно немного времени.",
        as_of=NOW,
    )


def test_policy_bytes_and_precedence_are_closed() -> None:
    assert hashlib.sha256(PLANNING_POLICY_CANONICAL_JSON.encode("utf-8")).hexdigest() == (
        PLANNING_POLICY_FINGERPRINT
    )
    assert validate_planning_policy() == PLANNING_POLICY_FINGERPRINT
    assert aggregate_planning_readiness(()) is PlanningPackReadinessV1.EXACT_CURRENT
    assert (
        aggregate_planning_readiness(
            (
                PlanningPackReadinessV1.INCOMPLETE,
                PlanningPackReadinessV1.STALE,
                PlanningPackReadinessV1.CONFLICT,
            )
        )
        is PlanningPackReadinessV1.CONFLICT
    )
    assert PLANNING_POLICY_ID == "stage17-personal-planning-v1"
    assert STAGE16_POLICY_ID == "stage16-executive-strategy-v1"
    assert len(STAGE16_POLICY_FINGERPRINT) == 64


def test_pack_is_deterministic_sorted_and_round_trips() -> None:
    selection = _selection()
    first = _pack(selection)
    second = _pack(selection)

    assert first == second
    assert first.readiness is PlanningPackReadinessV1.EXACT_CURRENT
    assert first.portfolio_order == (str(selection.goal.source_note_uuid),)
    assert tuple(entry.local_date for entry in first.capacity) == (
        "2026-09-16",
        "2026-09-17",
        "2026-09-18",
    )
    assert first.source_pack_fingerprint == first.pack_fingerprint
    assert serialize_planning_context_pack(first) == first.to_json()
    assert type(first.from_json(first.to_json())) is type(first)
    assert first.from_json(first.to_json()) == first


def test_multi_goal_order_is_explicit_and_not_recomputed() -> None:
    first_selection = _selection()
    second_selection = _selection()
    pack = _pack(second_selection, first_selection)

    assert pack.portfolio_order == (
        str(second_selection.goal.source_note_uuid),
        str(first_selection.goal.source_note_uuid),
    )
    assert pack.portfolio[0].goal_text == second_selection.goal_text
    assert pack.portfolio[0].selected_reviewed_action_refs[0].reviewed_action_id == "action-1"


def test_exact_provenance_rejects_goal_or_action_substitution() -> None:
    goal = _goal()
    snapshot = _snapshot(goal)
    with pytest.raises(PersonalPlanningError):
        PlanningGoalSelectionV1(
            goal=_goal(),
            goal_text="Улучшить выносливость",
            strategy_snapshot=snapshot,
            selected_action_ids=("action-1",),
        )
    with pytest.raises(PersonalPlanningError):
        PlanningGoalSelectionV1(
            goal=goal,
            goal_text="Улучшить выносливость",
            strategy_snapshot=snapshot,
            selected_action_ids=("action-unknown",),
        )


def test_capacity_and_windows_fail_closed() -> None:
    selection = _selection()
    with pytest.raises(PersonalPlanningError):
        build_planning_context_pack(
            (selection,),
            start_local="2026-09-16",
            end_local="2026-09-18",
            timezone="UTC",
            available_minutes_by_date={"2026-09-16": 30, "2026-09-17": 30},
            as_of=NOW,
        )
    overlapping = (
        PlanningWindowV1(
            window_id="fixed-1",
            kind=PlanningWindowKindV1.FIXED_COMMITMENT,
            title="Встреча",
            start_local="2026-09-16T12:00",
            end_local="2026-09-16T13:00",
        ),
        PlanningWindowV1(
            window_id="fixed-2",
            kind=PlanningWindowKindV1.UNAVAILABLE,
            title="Недоступность",
            start_local="2026-09-16T12:30",
            end_local="2026-09-16T14:00",
        ),
    )
    with pytest.raises(PersonalPlanningError):
        build_planning_context_pack(
            (selection,),
            start_local="2026-09-16",
            end_local="2026-09-18",
            timezone="UTC",
            available_minutes_by_date={
                "2026-09-16": 30,
                "2026-09-17": 30,
                "2026-09-18": 30,
            },
            fixed_windows=overlapping,
            as_of=NOW,
        )


def test_pack_tampering_and_duplicate_json_keys_are_rejected() -> None:
    pack = _pack(_selection())
    tampered = pack.as_dict()
    tampered["planning_context"] = "Подмена"
    with pytest.raises(PersonalPlanningError):
        type(pack).from_dict(tampered)
    duplicate = pack.to_json()[:-1] + ',"pack_fingerprint":"' + pack.pack_fingerprint + '"}'
    with pytest.raises(PersonalPlanningError):
        type(pack).from_json(duplicate)


def test_non_exact_readiness_requires_visible_caveat() -> None:
    selection = _selection()
    with pytest.raises(PersonalPlanningError):
        build_planning_context_pack(
            (selection,),
            start_local="2026-09-16",
            end_local="2026-09-16",
            timezone="UTC",
            available_minutes_by_date={"2026-09-16": 30},
            as_of=NOW,
            readiness=PlanningPackReadinessV1.STALE,
        )
    pack = build_planning_context_pack(
        (selection,),
        start_local="2026-09-16",
        end_local="2026-09-16",
        timezone="UTC",
        available_minutes_by_date={"2026-09-16": 30},
        as_of=NOW,
        readiness=PlanningPackReadinessV1.STALE,
        pack_caveats=("Источник устарел.",),
    )
    assert pack.readiness is PlanningPackReadinessV1.STALE


def test_pack_json_rejects_nan() -> None:
    pack = _pack(_selection())
    raw = json.dumps(pack.as_dict(), ensure_ascii=False, separators=(",", ":"))
    assert "NaN" not in raw
    with pytest.raises(PersonalPlanningError):
        type(pack).from_json(
            raw.replace(
                '"planning_context":"На этой неделе доступно немного времени."',
                '"planning_context":NaN',
            )
        )


def test_non_nfc_text_is_rejected_instead_of_silently_rewritten() -> None:
    with pytest.raises(PersonalPlanningError):
        PlanningWindowV1(
            window_id="fixed-1",
            kind=PlanningWindowKindV1.FIXED_COMMITMENT,
            title="Cafe\u0301",
            start_local="2026-09-16T12:00",
            end_local="2026-09-16T13:00",
        )
