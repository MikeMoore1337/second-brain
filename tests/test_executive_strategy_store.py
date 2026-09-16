"""Focused Phase 16.3 tests for reviewed snapshot persistence."""

from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid7

import pytest

from second_brain.application.assistant import (
    AssistantEvidenceRef,
    AssistantEvidenceRole,
    AssistantEvidenceSource,
    AssistantResultEnvelopeV1,
    AssistantResultKind,
)
from second_brain.application.executive_strategy import (
    BuildExecutiveStrategy,
    ExecutiveActionCandidateV1,
    ExecutiveActionKindV1,
    ExecutiveContextPackV1,
    ExecutiveSourceAliasV1,
    ExecutiveSourceItemV1,
    ExecutiveSourceReadinessV1,
    StrategyProposalV1,
    StrategySnapshotStateV1,
    build_executive_context_pack,
    build_reviewed_action,
    build_strategy_snapshot,
    serialize_strategy_snapshot,
)
from second_brain.application.executive_strategy_store import (
    EXECUTIVE_STRATEGY_STORE_DIRECTORY_NAME,
    ExecutiveStrategySnapshotStore,
    ExecutiveStrategyStoreCorruptError,
    ExecutiveStrategyStoreIdempotencyConflictError,
    ExecutiveStrategyStoreSourceChangedError,
    ExecutiveStrategyStoreStateConflictError,
    ExecutiveStrategyStoreUnavailableError,
    derive_executive_strategy_store_root,
)
from second_brain.application.growth import GrowthGoalIdentityV1
from second_brain.application.ports import CancellationTokenSource

PACK_TIME = datetime(2026, 9, 16, 5, 30, 0, tzinfo=UTC)
LATER = datetime(2026, 9, 16, 5, 31, 0, tzinfo=UTC)


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


def _pack() -> ExecutiveContextPackV1:
    sources = tuple(
        ExecutiveSourceItemV1(
            alias=alias,
            readiness=ExecutiveSourceReadinessV1.EXACT_CURRENT,
            reference_id=f"source:{index}",
            reference_fingerprint=f"{index + 3:x}" * 64,
            summary=f"Projection {alias.value}",
            as_of=PACK_TIME,
        )
        for index, alias in enumerate(
            (
                ExecutiveSourceAliasV1.GROWTH_RELATION,
                ExecutiveSourceAliasV1.PROGRESS_CURRENT,
                ExecutiveSourceAliasV1.BEHAVIOR_RELATION,
                ExecutiveSourceAliasV1.EXPERIMENT_TERMINAL,
                ExecutiveSourceAliasV1.ADAPTIVE_PROFILE_ACTIVE,
                ExecutiveSourceAliasV1.CALIBRATION_CAVEAT,
            )
        )
    )
    return build_executive_context_pack(
        _goal(),
        goal_text="Улучшить выносливость",
        task="Что рассмотреть сегодня?",
        constraints=("Без перегрузки",),
        current_context="Есть 30 минут",
        sources=sources,
        as_of=PACK_TIME,
    )


class _RecordingAdvisor:
    def __init__(self) -> None:
        self.calls = 0

    def advise(self, request, *, cancellation):  # type: ignore[no-untyped-def]
        del request, cancellation
        self.calls += 1
        return AssistantResultEnvelopeV1(
            output_label="independent_recommendation_analysis",
            kind=AssistantResultKind.RECOMMENDATION,
            recommendation="Сделать лёгкую тренировку и проверить самочувствие.",
            selected_option=None,
            rationale=(
                "Связь с текущей целью подтверждена.",
                "Наблюдать самочувствие после тренировки.",
            ),
            evidence_refs=(
                AssistantEvidenceRef(
                    source=AssistantEvidenceSource.EXPLICIT_CONTEXT,
                    ordinal=1,
                    role=AssistantEvidenceRole.REPORTED_FACT,
                ),
            ),
            constraints_used=(),
            objectives_used=(),
            uncertainty=(),
            abstention_code=None,
            contract_version="assistant-v1",
        )


def _proposal_and_pack() -> tuple[ExecutiveContextPackV1, StrategyProposalV1]:
    pack = _pack()
    proposal = _proposal_for(pack)
    return pack, proposal


def _proposal_for(pack: ExecutiveContextPackV1) -> StrategyProposalV1:
    proposal = BuildExecutiveStrategy(_RecordingAdvisor()).execute(
        pack,
        cancellation=CancellationTokenSource().token,
        proposal_id=uuid7(),
        as_of=PACK_TIME,
    )
    return proposal


def _store(tmp_path: Path) -> tuple[ExecutiveStrategySnapshotStore, Path]:
    runtime = tmp_path / "runtime"
    runtime.mkdir(parents=True)
    env_file = runtime / "web.env"
    env_file.write_text("SECOND_BRAIN_VAULT=/srv/second-brain-vault\n", encoding="utf-8")
    root = derive_executive_strategy_store_root(env_file)
    assert root == runtime / "prospective-audit" / EXECUTIVE_STRATEGY_STORE_DIRECTORY_NAME
    return ExecutiveStrategySnapshotStore(root), root


def test_store_root_is_derived_from_explicit_env_parent_and_initializes_empty(
    tmp_path: Path,
) -> None:
    store, root = _store(tmp_path)

    assert store.records_path == root / "snapshots.jsonl"
    assert store.read_events() == ()
    assert store.read_state().current_snapshots == ()
    assert store.manifest.record_count == 0
    assert store.records_path.read_bytes() == b""
    assert derive_executive_strategy_store_root(None) is None
    assert derive_executive_strategy_store_root(tmp_path / "missing.env") is None


def test_reviewed_snapshot_preserves_generated_and_owner_edited_content() -> None:
    candidate = ExecutiveActionCandidateV1(
        action_id="candidate-1",
        kind=ExecutiveActionKindV1.INVESTIGATE,
        title="Проверить вариант",
        description="Собрать ограниченный сигнал.",
        basis_aliases=(ExecutiveSourceAliasV1.GOAL_CURRENT,),
        goal_relation="Связь с целью требует проверки.",
        expected_observable_signal="Появится наблюдаемый результат.",
    )
    reviewed_candidate = ExecutiveActionCandidateV1(
        action_id=candidate.action_id,
        kind=candidate.kind,
        title="Проверить один вариант",
        description=candidate.description,
        basis_aliases=candidate.basis_aliases,
        goal_relation=candidate.goal_relation,
        expected_observable_signal=candidate.expected_observable_signal,
    )
    reviewed = build_reviewed_action(candidate, reviewed=reviewed_candidate)
    assert reviewed.edited is True
    assert reviewed.generated.title == "Проверить вариант"
    assert reviewed.reviewed.title == "Проверить один вариант"
    assert reviewed.generated.basis_aliases == reviewed.reviewed.basis_aliases


def test_accept_is_exact_idempotent_and_reject_is_append_only(tmp_path: Path) -> None:
    pack, proposal = _proposal_and_pack()
    store, root = _store(tmp_path)
    action = build_reviewed_action(proposal.candidates[0])

    accepted = store.accept(
        proposal,
        (action,),
        context_pack=pack,
        operation_id="accept-1",
        reviewed_at=PACK_TIME,
    )
    assert accepted.state is StrategySnapshotStateV1.CURRENT
    assert accepted.sequence == 1
    assert store.current_snapshot(pack.goal_source_uuid, pack.goal_identity_fingerprint) == accepted
    assert (
        store.accept(
            proposal,
            (action,),
            context_pack=pack,
            operation_id="accept-1",
            reviewed_at=PACK_TIME,
        )
        == accepted
    )

    with pytest.raises(ExecutiveStrategyStoreIdempotencyConflictError):
        changed = build_reviewed_action(
            proposal.candidates[0],
            reviewed=ExecutiveActionCandidateV1(
                action_id=proposal.candidates[0].action_id,
                kind=proposal.candidates[0].kind,
                title="Изменённый вариант",
                description=proposal.candidates[0].description,
                basis_aliases=proposal.candidates[0].basis_aliases,
                goal_relation=proposal.candidates[0].goal_relation,
                expected_observable_signal=proposal.candidates[0].expected_observable_signal,
            ),
        )
        store.accept(
            proposal,
            (changed,),
            context_pack=pack,
            operation_id="accept-1",
            reviewed_at=PACK_TIME,
        )

    rejected = store.reject(proposal, operation_id="reject-1", reason="owner rejected")
    assert rejected.record.reason == "owner rejected"
    assert store.reject(proposal, operation_id="reject-1", reason="owner rejected") == rejected
    assert store.manifest.record_count == 2
    assert (root / "snapshots.jsonl").read_text(encoding="utf-8").count(
        "Улучшить выносливость"
    ) == 0


def test_supersession_requires_exact_prior_and_keeps_one_current_snapshot(tmp_path: Path) -> None:
    pack, first_proposal = _proposal_and_pack()
    store, _ = _store(tmp_path)
    first = store.accept(
        first_proposal,
        (build_reviewed_action(first_proposal.candidates[0]),),
        context_pack=pack,
        operation_id="accept-first",
        reviewed_at=PACK_TIME,
    )
    second_proposal = _proposal_for(pack)

    with pytest.raises(ExecutiveStrategyStoreStateConflictError):
        store.accept(
            second_proposal,
            (build_reviewed_action(second_proposal.candidates[0]),),
            context_pack=pack,
            operation_id="accept-second-without-prior",
            reviewed_at=LATER,
        )

    second = store.accept(
        second_proposal,
        (build_reviewed_action(second_proposal.candidates[0]),),
        context_pack=pack,
        operation_id="accept-second",
        reviewed_at=LATER,
        expected_prior_snapshot_id=first.snapshot_id,
        expected_prior_snapshot_fingerprint=first.snapshot_fingerprint,
    )
    assert second.sequence == 2
    assert second.prior_snapshot_id == first.snapshot_id
    assert second.prior_snapshot_fingerprint == first.snapshot_fingerprint
    assert store.current_snapshot(pack.goal_source_uuid, pack.goal_identity_fingerprint) == second
    assert len(store.read_state().current_snapshots) == 1
    assert store.read_snapshots() == (first, second)


def test_accept_revalidates_pack_and_snapshot_round_trip_is_canonical(tmp_path: Path) -> None:
    pack, proposal = _proposal_and_pack()
    store, _ = _store(tmp_path)
    action = build_reviewed_action(proposal.candidates[0])

    with pytest.raises(ExecutiveStrategyStoreSourceChangedError):
        store.accept(
            proposal,
            (action,),
            context_pack=build_executive_context_pack(
                _goal(),
                goal_text="Другая цель",
                task="Что рассмотреть сегодня?",
                as_of=PACK_TIME,
            ),
            operation_id="accept-stale",
            reviewed_at=PACK_TIME,
        )

    accepted = store.accept(
        proposal,
        (action,),
        context_pack=pack,
        operation_id="accept-round-trip",
        reviewed_at=PACK_TIME,
    )
    assert serialize_strategy_snapshot(accepted) == json.dumps(
        accepted.as_dict(), ensure_ascii=False, separators=(",", ":")
    ).encode("utf-8")

    reloaded = ExecutiveStrategySnapshotStore(store.root)
    assert reloaded.read_snapshots() == (accepted,)


def test_store_fails_closed_on_torn_append_digest_and_manifest_tampering(tmp_path: Path) -> None:
    pack, proposal = _proposal_and_pack()
    store, _ = _store(tmp_path)
    store.accept(
        proposal,
        (build_reviewed_action(proposal.candidates[0]),),
        context_pack=pack,
        operation_id="accept-tamper",
        reviewed_at=PACK_TIME,
    )

    raw = store.records_path.read_bytes()
    store.records_path.write_bytes(raw[:-1])
    with pytest.raises(ExecutiveStrategyStoreCorruptError):
        store.read_events()

    digest_store, _ = _store(tmp_path / "digest")
    digest_store.accept(
        proposal,
        (build_reviewed_action(proposal.candidates[0]),),
        context_pack=pack,
        operation_id="accept-digest",
        reviewed_at=PACK_TIME,
    )
    digest_raw = bytearray(digest_store.records_path.read_bytes())
    digest_raw[-2] = ord("0") if digest_raw[-2] != ord("0") else ord("1")
    digest_store.records_path.write_bytes(bytes(digest_raw))
    with pytest.raises(ExecutiveStrategyStoreCorruptError):
        digest_store.read_events()

    manifest_store, _ = _store(tmp_path / "manifest")
    manifest_store.accept(
        proposal,
        (build_reviewed_action(proposal.candidates[0]),),
        context_pack=pack,
        operation_id="accept-manifest",
        reviewed_at=PACK_TIME,
    )
    manifest = json.loads(manifest_store.manifest_path.read_text(encoding="utf-8"))
    manifest["record_count"] = 0
    manifest_store.manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
        encoding="utf-8",
    )
    with pytest.raises(ExecutiveStrategyStoreCorruptError):
        manifest_store.read_events()


def test_store_rejects_vault_release_and_reordered_record_paths(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    vault.mkdir()
    with pytest.raises(ExecutiveStrategyStoreUnavailableError):
        ExecutiveStrategySnapshotStore(vault / "store", vault_root=vault)

    with pytest.raises(ExecutiveStrategyStoreUnavailableError):
        ExecutiveStrategySnapshotStore(tmp_path / "releases" / "strategy")

    pack, proposal = _proposal_and_pack()
    store, _ = _store(tmp_path / "reordered")
    store.accept(
        proposal,
        (build_reviewed_action(proposal.candidates[0]),),
        context_pack=pack,
        operation_id="reorder-first",
        reviewed_at=PACK_TIME,
    )
    second_proposal = _proposal_for(pack)
    first = store.current_snapshot(pack.goal_source_uuid, pack.goal_identity_fingerprint)
    assert first is not None
    store.accept(
        second_proposal,
        (build_reviewed_action(second_proposal.candidates[0]),),
        context_pack=pack,
        operation_id="reorder-second",
        reviewed_at=LATER,
        expected_prior_snapshot_id=first.snapshot_id,
        expected_prior_snapshot_fingerprint=first.snapshot_fingerprint,
    )
    lines = store.records_path.read_bytes().splitlines(keepends=True)
    store.records_path.write_bytes(lines[1] + lines[0])
    with pytest.raises(ExecutiveStrategyStoreCorruptError):
        store.read_events()


def test_same_operation_is_idempotent_under_concurrent_acceptance(tmp_path: Path) -> None:
    pack, proposal = _proposal_and_pack()
    store, _ = _store(tmp_path)
    action = build_reviewed_action(proposal.candidates[0])

    def accept() -> object:
        return store.accept(
            proposal,
            (action,),
            context_pack=pack,
            operation_id="concurrent-accept",
            reviewed_at=PACK_TIME,
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda _item: accept(), range(2)))
    assert results[0] == results[1]
    assert len(store.read_state().current_snapshots) == 1
    assert len(store.read_events()) == 1


def test_snapshot_builder_rejects_unbound_generated_action() -> None:
    _pack_value, proposal = _proposal_and_pack()
    other = ExecutiveActionCandidateV1(
        action_id="other",
        kind=ExecutiveActionKindV1.HOLD,
        title="Удержать текущую стратегию",
        description="Наблюдать без изменения.",
        basis_aliases=(ExecutiveSourceAliasV1.GOAL_CURRENT,),
        goal_relation="Связь остаётся неопределённой.",
        expected_observable_signal="Новый сигнал не ожидается.",
    )
    with pytest.raises(ValueError):
        build_strategy_snapshot(
            proposal,
            (build_reviewed_action(other),),
            sequence=1,
            reviewed_at=PACK_TIME,
            accepted_at=PACK_TIME,
        )
