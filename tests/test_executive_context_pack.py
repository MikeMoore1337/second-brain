"""Focused Phase 16.1 tests for the provider-free Executive Context Pack."""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from uuid import uuid7

import pytest

from second_brain.application.executive_strategy import (
    EXECUTIVE_POLICY_CANONICAL_JSON,
    EXECUTIVE_POLICY_FINGERPRINT,
    SOURCE_ALIASES,
    ExecutiveContextError,
    ExecutiveContextInvalidError,
    ExecutiveContextPackV1,
    ExecutivePackReadinessV1,
    ExecutiveSourceAliasV1,
    ExecutiveSourceItemV1,
    ExecutiveSourceReadinessV1,
    build_executive_context_pack,
    goal_identity_fingerprint,
    serialize_executive_context_pack,
)
from second_brain.application.growth import GrowthGoalIdentityV1

PACK_TIME = datetime(2026, 9, 16, 5, 30, 0, tzinfo=UTC)


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


def test_policy_bytes_are_stable_and_alias_order_is_closed() -> None:
    assert hashlib.sha256(EXECUTIVE_POLICY_CANONICAL_JSON.encode("utf-8")).hexdigest() == (
        EXECUTIVE_POLICY_FINGERPRINT
    )
    assert tuple(alias.value for alias in SOURCE_ALIASES) == (
        "goal.current",
        "growth.relation",
        "progress.current",
        "behavior.relation",
        "experiment.terminal",
        "adaptive_profile.active",
        "calibration.caveat",
        "caller.task",
        "caller.constraints",
        "caller.context",
    )


def test_pack_is_deterministic_and_binds_exact_growth_identity() -> None:
    goal = _goal()
    first = build_executive_context_pack(
        goal,
        goal_text="Улучшить выносливость",
        task="Что рассмотреть сегодня?",
        constraints=("Без перегрузки",),
        current_context="Есть 30 минут",
        as_of=PACK_TIME,
        expected_goal_identity_fingerprint=goal_identity_fingerprint(goal),
    )
    second = build_executive_context_pack(
        goal,
        goal_text="Улучшить выносливость",
        task="Что рассмотреть сегодня?",
        constraints=("Без перегрузки",),
        current_context="Есть 30 минут",
        as_of=PACK_TIME,
        expected_goal_identity_fingerprint=goal_identity_fingerprint(goal),
    )

    assert first == second
    assert serialize_executive_context_pack(first) == serialize_executive_context_pack(second)
    assert first.readiness is ExecutivePackReadinessV1.INCOMPLETE
    assert first.sources[0].alias is ExecutiveSourceAliasV1.GOAL_CURRENT
    assert first.sources[0].reference_id == str(goal.source_note_uuid)
    assert first.sources[0].reference_fingerprint == goal_identity_fingerprint(goal)
    assert first.sources[7].summary == "Что рассмотреть сегодня?"


def test_pack_accepts_exact_source_projection_and_aggregates_conflict() -> None:
    goal = _goal()
    source = ExecutiveSourceItemV1(
        alias=ExecutiveSourceAliasV1.PROGRESS_CURRENT,
        readiness=ExecutiveSourceReadinessV1.CONFLICT,
        reference_id="progress:current",
        reference_fingerprint="a" * 64,
        summary="Progress binding is conflicting",
        as_of=PACK_TIME,
    )
    pack = build_executive_context_pack(
        goal,
        goal_text="Проверить прогресс",
        task="Выбрать следующий шаг",
        sources=(source,),
        as_of=PACK_TIME,
    )
    assert pack.readiness is ExecutivePackReadinessV1.CONFLICT
    assert pack.sources[2] == source


def test_goal_rebind_and_duplicate_source_alias_fail_closed() -> None:
    goal = _goal()
    with pytest.raises(ExecutiveContextError):
        build_executive_context_pack(
            goal,
            goal_text="Цель",
            task="Запрос",
            expected_goal_identity_fingerprint="sha256:" + "f" * 64,
            as_of=PACK_TIME,
        )

    source = ExecutiveSourceItemV1(
        alias=ExecutiveSourceAliasV1.PROGRESS_CURRENT,
        readiness=ExecutiveSourceReadinessV1.EXACT_CURRENT,
        reference_id="progress:current",
        reference_fingerprint="a" * 64,
        summary="Текущее состояние",
        as_of=PACK_TIME,
    )
    with pytest.raises(ExecutiveContextInvalidError):
        build_executive_context_pack(
            goal,
            goal_text="Цель",
            task="Запрос",
            sources=(source, source),
            as_of=PACK_TIME,
        )


def test_pack_fingerprint_tampering_is_rejected() -> None:
    goal = _goal()
    pack = build_executive_context_pack(
        goal,
        goal_text="Цель",
        task="Запрос",
        as_of=PACK_TIME,
    )
    with pytest.raises(ExecutiveContextInvalidError):
        ExecutiveContextPackV1(
            contract_version=pack.contract_version,
            pack_version=pack.pack_version,
            as_of=pack.as_of,
            goal_source_uuid=pack.goal_source_uuid,
            goal_identity_fingerprint=pack.goal_identity_fingerprint,
            goal_text=pack.goal_text,
            task="Подменённый запрос",
            constraints=pack.constraints,
            current_context=pack.current_context,
            sources=pack.sources,
            readiness=pack.readiness,
            pack_caveats=pack.pack_caveats,
            policy_id=pack.policy_id,
            policy_fingerprint=pack.policy_fingerprint,
            source_pack_fingerprint=pack.source_pack_fingerprint,
        )
