"""Stage 17.5 adversarial tests for the operational plan store."""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest

from second_brain.application.personal_planning_store import (
    PersonalPlanningStoreCapacityConflictError,
    PersonalPlanningStoreCorruptError,
    PersonalPlanningStoreInvalidRequestError,
    PersonalPlanningStoreSourceChangedError,
)
from tests.test_personal_planning_store import (
    PACK_TIME,
    _proposal_and_pack,
    _store,
)


def test_edit_cannot_select_or_order_an_unknown_item(tmp_path: Path) -> None:
    pack, proposal = _proposal_and_pack()
    store, _ = _store(tmp_path)
    current = store.accept(
        proposal, context_pack=pack, operation_id="accept-invalid-edit", accepted_at=PACK_TIME
    )

    with pytest.raises(PersonalPlanningStoreInvalidRequestError):
        store.edit(
            items=current.items,
            selected_item_ids=("missing-item",),
            item_order=("missing-item",),
            operation_id="edit-unknown-item",
            expected_current_plan_fingerprint=current.plan_fingerprint,
            edited_at=PACK_TIME,
        )

    assert len(store.read_events()) == 1
    assert store.current_plan() == current


def test_edit_revalidates_capacity_and_dependencies_after_owner_changes(tmp_path: Path) -> None:
    pack, proposal = _proposal_and_pack(final_day_minutes=30)
    store, _ = _store(tmp_path)
    current = store.accept(
        proposal, context_pack=pack, operation_id="accept-capacity", accepted_at=PACK_TIME
    )
    original = current.items[0]
    over_capacity = type(original)(
        item_id=original.item_id,
        kind=original.kind,
        title=original.title,
        description=original.description,
        goal_refs=original.goal_refs,
        action_refs=original.action_refs,
        parent_item_id=original.parent_item_id,
        target_start_local=original.target_start_local,
        target_end_local=original.target_end_local,
        effort_minutes=31,
        effort_source=original.effort_source,
        dependency_ids=original.dependency_ids,
    )

    with pytest.raises(PersonalPlanningStoreCapacityConflictError):
        store.edit(
            items=(over_capacity,),
            selected_item_ids=(original.item_id,),
            item_order=(original.item_id,),
            operation_id="edit-over-capacity",
            expected_current_plan_fingerprint=current.plan_fingerprint,
            edited_at=PACK_TIME,
        )

    assert len(store.read_events()) == 1
    assert store.current_plan() == current


def test_edit_cannot_replace_provenance_bindings(tmp_path: Path) -> None:
    pack, proposal = _proposal_and_pack()
    store, _ = _store(tmp_path)
    current = store.accept(
        proposal, context_pack=pack, operation_id="accept-provenance", accepted_at=PACK_TIME
    )
    original = current.items[0]
    substituted = type(original)(
        item_id=original.item_id,
        kind=original.kind,
        title=original.title,
        description=original.description,
        goal_refs=original.goal_refs,
        action_refs=(replace(original.action_refs[0], reviewed_action_fingerprint="c" * 64),),
        parent_item_id=original.parent_item_id,
        target_start_local=original.target_start_local,
        target_end_local=original.target_end_local,
        effort_minutes=original.effort_minutes,
        effort_source=original.effort_source,
        dependency_ids=original.dependency_ids,
    )

    with pytest.raises(PersonalPlanningStoreSourceChangedError):
        store.edit(
            items=(substituted,),
            selected_item_ids=(original.item_id,),
            item_order=(original.item_id,),
            operation_id="edit-provenance-substitution",
            expected_current_plan_fingerprint=current.plan_fingerprint,
            edited_at=PACK_TIME,
        )

    assert len(store.read_events()) == 1


def test_tampering_with_a_single_event_field_breaks_the_verified_chain(tmp_path: Path) -> None:
    pack, proposal = _proposal_and_pack()
    store, _ = _store(tmp_path)
    store.accept(proposal, context_pack=pack, operation_id="accept-tamper", accepted_at=PACK_TIME)

    event = json.loads(store.records_path.read_text(encoding="utf-8"))
    event["record"]["plan"]["items"][0]["title"] = "Подмена"
    store.records_path.write_text(
        json.dumps(event, ensure_ascii=False, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )

    with pytest.raises(PersonalPlanningStoreCorruptError):
        store.read_events()
