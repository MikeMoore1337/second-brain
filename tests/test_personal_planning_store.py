"""Focused Phase 17.3 tests for the append-only operational plan store."""

from __future__ import annotations

import hashlib
import json
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path
from typing import cast
from uuid import UUID, uuid7

import pytest

from second_brain.application.assistant import (
    ASSISTANT_CONTRACT_VERSION,
    ASSISTANT_OUTPUT_LABEL,
    AssistantEvidenceRef,
    AssistantEvidenceRole,
    AssistantEvidenceSource,
    AssistantInputRef,
    AssistantInputSource,
    AssistantReasoningEnvelopeV1,
    AssistantResultEnvelopeV1,
    AssistantResultKind,
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
    PLANNING_POLICY_FINGERPRINT,
    PLANNING_POLICY_ID,
    BuildPersonalPlanner,
    PlanningActionBindingV1,
    PlanningContextPackV1,
    PlanningEffortSourceV1,
    PlanningGoalRefV1,
    PlanningGoalSelectionV1,
    PlanningItemKindV1,
    PlanningItemV1,
    PlanningProposalV1,
    PlanningResultStateV1,
    PlanningWindowKindV1,
    PlanningWindowV1,
    build_planning_context_pack,
)
from second_brain.application.personal_planning_store import (
    PERSONAL_PLANNING_STORE_DIRECTORY_NAME,
    PersonalPlanningOperationalStore,
    PersonalPlanningStoreCapacityConflictError,
    PersonalPlanningStoreCorruptError,
    PersonalPlanningStoreIdempotencyConflictError,
    PersonalPlanningStoreSourceChangedError,
    PersonalPlanningStoreStateConflictError,
    PlanningPlanV1,
    derive_personal_planning_store_root,
)
from second_brain.application.ports import AdvisorPort, CancellationToken, CancellationTokenSource

PACK_TIME = datetime(2026, 9, 16, 5, 30, 0, tzinfo=UTC)
LATER = datetime(2026, 9, 16, 5, 31, 0, tzinfo=UTC)
PLAN_ID = UUID("0198f4c5-6a00-7000-8000-000000000001")


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


def _pack(*, final_day_minutes: int = 60) -> PlanningContextPackV1:
    goal = _goal()
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
    strategy_proposal: StrategyProposalV1 = _new_proposal(
        proposal_id=uuid7(),
        as_of=PACK_TIME,
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
    snapshot: StrategySnapshotV1 = build_strategy_snapshot(
        strategy_proposal,
        (reviewed,),
        sequence=1,
        reviewed_at=PACK_TIME,
        accepted_at=PACK_TIME,
        snapshot_id=uuid7(),
    )
    return build_planning_context_pack(
        (
            PlanningGoalSelectionV1(
                goal=goal,
                goal_text="Улучшить выносливость",
                strategy_snapshot=snapshot,
                selected_action_ids=("action-1",),
            ),
        ),
        start_local="2026-09-16",
        end_local="2026-09-18",
        timezone="UTC",
        available_minutes_by_date={
            "2026-09-16": 30,
            "2026-09-17": 0,
            "2026-09-18": final_day_minutes,
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
        as_of=PACK_TIME,
    )


class _Advisor(AdvisorPort):
    def __init__(self, recommendation: str) -> None:
        self.recommendation = recommendation

    def advise(
        self,
        request: AssistantReasoningEnvelopeV1,
        *,
        cancellation: CancellationToken,
    ) -> AssistantResultEnvelopeV1:
        del request, cancellation
        return AssistantResultEnvelopeV1(
            output_label=ASSISTANT_OUTPUT_LABEL,
            kind=AssistantResultKind.RECOMMENDATION,
            recommendation=self.recommendation,
            selected_option=None,
            rationale=("Предложение связано с явным действием.",),
            evidence_refs=(
                AssistantEvidenceRef(
                    AssistantEvidenceSource.EXPLICIT_CONTEXT,
                    1,
                    AssistantEvidenceRole.REPORTED_FACT,
                ),
            ),
            constraints_used=(AssistantInputRef(AssistantInputSource.EXPLICIT_CONSTRAINT, 1),),
            objectives_used=(AssistantInputRef(AssistantInputSource.EXPLICIT_GOAL, 1),),
            uncertainty=(),
            abstention_code=None,
            contract_version=ASSISTANT_CONTRACT_VERSION,
        )


def _proposal_and_pack(
    *,
    final_day_minutes: int = 60,
    two_items: bool = False,
    dependent_items: bool = False,
) -> tuple[PlanningContextPackV1, PlanningProposalV1]:
    pack = _pack(final_day_minutes=final_day_minutes)
    binding = pack.portfolio[0]
    goal = PlanningGoalRefV1(
        goal_source_uuid=binding.goal_source_uuid,
        goal_identity_fingerprint=binding.goal_identity_fingerprint,
    )
    action = PlanningActionBindingV1.from_action_ref(binding.selected_reviewed_action_refs[0])
    item: dict[str, object] = {
        "item_id": "next-1",
        "kind": PlanningItemKindV1.NEXT_ACTION.value,
        "title": "Сделать шаг",
        "description": "Выполнить действие.",
        "goal_refs": [goal.as_dict()],
        "action_refs": [action.as_dict()],
        "parent_item_id": None,
        "target_start_local": "2026-09-18T09:00",
        "target_end_local": "2026-09-18T09:30",
        "effort_minutes": 30,
        "effort_source": PlanningEffortSourceV1.PROVIDER_PROPOSED.value,
        "dependency_ids": [],
    }
    items: list[dict[str, object]] = [item]
    order = ["next-1"]
    if two_items:
        items.append(
            {
                **item,
                "item_id": "next-2",
                "title": "Второй шаг",
                "dependency_ids": ["next-1"] if dependent_items else [],
            }
        )
        order.append("next-2")
    draft = {
        "result_state": PlanningResultStateV1.PROPOSAL.value,
        "items": items,
        "suggested_order": order,
        "reasons": ["Связано с целью."],
        "caveats": [],
    }
    if two_items:
        proposal_items = tuple(PlanningItemV1.from_dict(item) for item in items)
        proposal_core = {
            "proposal_id": str(PLAN_ID),
            "proposal_version": "1",
            "result_state": PlanningResultStateV1.PROPOSAL.value,
            "as_of": "2026-09-16T05:30:00Z",
            "source_pack_fingerprint": pack.pack_fingerprint,
            "provider_envelope_fingerprint": "1" * 64,
            "provider_result_fingerprint": "2" * 64,
            "policy_id": PLANNING_POLICY_ID,
            "policy_fingerprint": PLANNING_POLICY_FINGERPRINT,
            "items": [item.as_dict() for item in proposal_items],
            "suggested_order": order,
            "reasons": ["Связано с целью."],
            "caveats": [],
        }
        proposal = PlanningProposalV1(
            proposal_id=PLAN_ID,
            proposal_version="1",
            result_state=PlanningResultStateV1.PROPOSAL,
            as_of=PACK_TIME,
            source_pack_fingerprint=pack.pack_fingerprint,
            provider_envelope_fingerprint="1" * 64,
            provider_result_fingerprint="2" * 64,
            policy_id=PLANNING_POLICY_ID,
            policy_fingerprint=PLANNING_POLICY_FINGERPRINT,
            items=proposal_items,
            suggested_order=tuple(order),
            reasons=("Связано с целью.",),
            caveats=(),
            proposal_fingerprint=hashlib.sha256(
                json.dumps(
                    proposal_core,
                    ensure_ascii=False,
                    allow_nan=False,
                    separators=(",", ":"),
                ).encode("utf-8")
            ).hexdigest(),
        )
        return pack, proposal
    proposal = BuildPersonalPlanner(
        _Advisor(json.dumps(draft, ensure_ascii=False, separators=(",", ":")))
    ).execute(
        pack,
        cancellation=CancellationTokenSource().token,
        as_of=PACK_TIME,
        proposal_id=PLAN_ID,
    )
    return pack, proposal


def _store(tmp_path: Path) -> tuple[PersonalPlanningOperationalStore, Path]:
    runtime = tmp_path / "runtime"
    runtime.mkdir(parents=True)
    env_file = runtime / "web.env"
    env_file.write_text("SECOND_BRAIN_VAULT=/srv/second-brain-vault\n", encoding="utf-8")
    root = derive_personal_planning_store_root(env_file)
    assert root == runtime / "prospective-audit" / PERSONAL_PLANNING_STORE_DIRECTORY_NAME
    return PersonalPlanningOperationalStore(root), root


def test_store_is_derived_outside_vault_and_initializes_empty(tmp_path: Path) -> None:
    store, root = _store(tmp_path)

    assert store.records_path == root / "plans.jsonl"
    assert store.read_events() == ()
    assert store.current_plan() is None
    assert store.manifest.record_count == 0
    assert store.records_path.read_bytes() == b""
    assert derive_personal_planning_store_root(None) is None
    assert derive_personal_planning_store_root(tmp_path / "missing.env") is None


def test_accept_keeps_one_current_plan_and_only_exact_provenance(tmp_path: Path) -> None:
    pack, proposal = _proposal_and_pack()
    store, _ = _store(tmp_path)

    accepted = store.accept(
        proposal,
        context_pack=pack,
        operation_id="accept-1",
        selected_item_ids=("next-1",),
        item_order=("next-1",),
        accepted_at=PACK_TIME,
    )

    assert accepted.revision == 1
    assert accepted.selected_item_ids == ("next-1",)
    assert accepted.item_order == ("next-1",)
    assert accepted.source_pack_fingerprint == pack.pack_fingerprint
    assert accepted.proposal_fingerprint == proposal.proposal_fingerprint
    assert store.current_plan() == accepted
    assert len(store.read_state().plan_history) == 1
    raw = store.records_path.read_text(encoding="utf-8")
    assert "planning.payload.part" not in raw
    assert "provider_payload_json" not in raw


def test_edit_is_owner_bounded_and_exactly_idempotent(tmp_path: Path) -> None:
    pack, proposal = _proposal_and_pack()
    store, _ = _store(tmp_path)
    accepted = store.accept(
        proposal,
        context_pack=pack,
        operation_id="accept-edit",
        accepted_at=PACK_TIME,
    )
    edited_item = accepted.items[0]
    edited_item = type(edited_item)(
        item_id=edited_item.item_id,
        kind=edited_item.kind,
        title="Проверить один выбранный шаг",
        description="Владелец уточнил формулировку действия.",
        goal_refs=edited_item.goal_refs,
        action_refs=edited_item.action_refs,
        parent_item_id=edited_item.parent_item_id,
        target_start_local="2026-09-16T09:00",
        target_end_local="2026-09-16T09:20",
        effort_minutes=20,
        effort_source=edited_item.effort_source,
        dependency_ids=edited_item.dependency_ids,
    )
    edited = store.edit(
        items=(edited_item,),
        selected_item_ids=("next-1",),
        item_order=("next-1",),
        operation_id="edit-1",
        expected_current_plan_fingerprint=accepted.plan_fingerprint,
        edited_at=LATER,
    )

    assert edited.revision == 2
    assert edited.plan_id == accepted.plan_id
    assert edited.items[0].title == "Проверить один выбранный шаг"
    assert edited.provider_result_fingerprint == accepted.provider_result_fingerprint
    assert (
        store.edit(
            items=(edited_item,),
            selected_item_ids=("next-1",),
            item_order=("next-1",),
            operation_id="edit-1",
            expected_current_plan_fingerprint=accepted.plan_fingerprint,
            edited_at=LATER,
        )
        == edited
    )


def test_selection_dependencies_and_capacity_fail_closed(tmp_path: Path) -> None:
    pack, proposal = _proposal_and_pack(final_day_minutes=20)
    store, _ = _store(tmp_path)
    with pytest.raises(PersonalPlanningStoreCapacityConflictError):
        store.accept(proposal, context_pack=pack, operation_id="over-capacity")

    pack, dependent_proposal = _proposal_and_pack(two_items=True, dependent_items=True)
    with pytest.raises(PersonalPlanningStoreCapacityConflictError):
        store.accept(
            dependent_proposal,
            context_pack=pack,
            operation_id="dependent-selection",
            selected_item_ids=("next-2",),
            item_order=("next-2",),
        )


def test_idempotency_concurrency_and_stale_writer_conflict(tmp_path: Path) -> None:
    pack, proposal = _proposal_and_pack()
    store, _ = _store(tmp_path)

    def accept() -> object:
        return store.accept(
            proposal,
            context_pack=pack,
            operation_id="concurrent-accept",
            accepted_at=PACK_TIME,
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda _item: accept(), range(2)))
    assert results[0] == results[1]
    assert len(store.read_events()) == 1

    with pytest.raises(PersonalPlanningStoreIdempotencyConflictError):
        store.accept(
            proposal,
            context_pack=pack,
            operation_id="concurrent-accept",
            selected_item_ids=(),
            item_order=(),
            accepted_at=PACK_TIME,
        )

    first_plan = cast(PlanningPlanV1, results[0])
    edited_item = first_plan.items[0]
    with pytest.raises(PersonalPlanningStoreStateConflictError):
        store.edit(
            items=(edited_item,),
            selected_item_ids=("next-1",),
            item_order=("next-1",),
            operation_id="stale-edit",
            expected_current_plan_fingerprint="0" * 64,
        )


def test_source_changed_and_tampering_are_rejected(tmp_path: Path) -> None:
    pack, proposal = _proposal_and_pack()
    other_pack, _ = _proposal_and_pack()
    store, _ = _store(tmp_path)
    with pytest.raises((PersonalPlanningStoreSourceChangedError, ValueError)):
        store.accept(proposal, context_pack=other_pack, operation_id="wrong-source")

    store.accept(proposal, context_pack=pack, operation_id="tamper")
    raw = store.records_path.read_bytes()
    store.records_path.write_bytes(raw[:-1])
    with pytest.raises(PersonalPlanningStoreCorruptError):
        store.read_events()

    manifest_store, _ = _store(tmp_path / "manifest")
    manifest_store.accept(proposal, context_pack=pack, operation_id="manifest-tamper")
    manifest = json.loads(manifest_store.manifest_path.read_text(encoding="utf-8"))
    manifest["record_count"] = 0
    manifest_store.manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
        encoding="utf-8",
    )
    with pytest.raises(PersonalPlanningStoreCorruptError):
        manifest_store.read_events()
