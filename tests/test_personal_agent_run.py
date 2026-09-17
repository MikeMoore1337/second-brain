"""Focused Stage 20 Run lifecycle and operational-store tests."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from second_brain.application.assistant import (
    ASSISTANT_CONTRACT_VERSION,
    ASSISTANT_OUTPUT_LABEL,
    AssistantResultEnvelopeV1,
    AssistantResultKind,
)
from second_brain.application.personal_agent import AgentContextPackV1, build_agent_context_pack
from second_brain.application.personal_agent_planner import (
    build_agent_run_proposal_from_advisor_result,
)
from second_brain.application.personal_agent_run import (
    AgentRunService,
    AgentRunStateV1,
    AgentRunStepStateV1,
)
from second_brain.application.personal_agent_run_store import (
    PersonalAgentRunOperationalStore,
    PersonalAgentRunStoreCorruptError,
    PersonalAgentRunStoreStateConflictError,
)
from tests.test_personal_agent import _pack_inputs


def _pack() -> AgentContextPackV1:
    mission, plan, states, stage19 = _pack_inputs()
    return build_agent_context_pack(
        mission,
        plan,
        states,
        stage19,
        current_planning_plan=plan,
    )


def _recommendation(steps: list[dict[str, object]]) -> AssistantResultEnvelopeV1:
    import json

    return AssistantResultEnvelopeV1(
        output_label=ASSISTANT_OUTPUT_LABEL,
        kind=AssistantResultKind.RECOMMENDATION,
        recommendation=json.dumps(
            {"steps": steps, "caveats": []},
            ensure_ascii=False,
            sort_keys=True,
        ),
        selected_option=None,
        rationale=("Ограниченный план для явного просмотра владельцем.",),
        evidence_refs=(),
        constraints_used=(),
        objectives_used=(),
        uncertainty=(),
        abstention_code=None,
        contract_version=ASSISTANT_CONTRACT_VERSION,
    )


def _proposal(pack: AgentContextPackV1):
    return build_agent_run_proposal_from_advisor_result(
        pack,
        _recommendation(
            [
                {
                    "step_id": "checkpoint-1",
                    "position": 1,
                    "kind": "checkpoint",
                    "summary": "Проверь исходный контекст.",
                },
                {
                    "step_id": "clarify-1",
                    "position": 2,
                    "kind": "clarify",
                    "question": "Какой результат считаем достаточным?",
                    "reason": "Нужна явная граница готовности.",
                    "answer_shape": "Короткая формулировка.",
                },
                {
                    "step_id": "hold-1",
                    "position": 3,
                    "kind": "hold",
                    "reason": "Дальнейшее действие требует отдельного решения.",
                },
                {
                    "step_id": "checkpoint-2",
                    "position": 4,
                    "kind": "checkpoint",
                    "summary": "Проверь результат перед завершением.",
                },
            ]
        ),
    )


def test_run_lifecycle_requires_explicit_owner_progression_and_replays(tmp_path: Path) -> None:
    pack = _pack()
    proposal = _proposal(pack)
    store = PersonalAgentRunOperationalStore(tmp_path / "operational")
    now = datetime(2026, 9, 17, 12, 0, tzinfo=UTC)
    service = AgentRunService(store, clock=lambda: now)
    run_id = "0199f6c0-0000-7000-8000-000000000001"

    accepted = service.accept(pack, proposal, operation_id="run/accept", run_id=run_id)
    assert accepted.state is AgentRunStateV1.ACCEPTED
    assert accepted.current_step is None
    assert store.current() == accepted

    retry = service.accept(pack, proposal, operation_id="run/accept", run_id=run_id)
    assert retry == accepted
    assert store.manifest.record_count == 1

    started = service.start(pack, run_id, operation_id="run/start")
    assert started.state is AgentRunStateV1.WAITING_OWNER
    assert started.current_step is not None
    assert started.current_step.step.step_id == "checkpoint-1"

    clarified = service.continue_run(pack, run_id, operation_id="run/continue-1")
    assert clarified.current_step is not None
    assert clarified.current_step.step.step_id == "clarify-1"
    assert clarified.current_step.state is AgentRunStepStateV1.WAITING_OWNER

    answered = service.answer(
        pack,
        run_id,
        "Готово, если зафиксирован проверяемый результат.",
        operation_id="run/answer-1",
    )
    assert answered.current_step is not None
    assert answered.current_step.step.step_id == "hold-1"

    skipped = service.skip(
        pack, run_id, "Оставляем как явное ограничение.", operation_id="run/skip"
    )
    assert skipped.state is AgentRunStateV1.ACTIVE
    assert skipped.current_step is None

    resumed = service.continue_run(pack, run_id, operation_id="run/continue-2")
    assert resumed.state is AgentRunStateV1.WAITING_OWNER
    assert resumed.current_step is not None
    assert resumed.current_step.step.step_id == "checkpoint-2"

    ready = service.skip(pack, run_id, "Проверка выполнена владельцем.", operation_id="run/skip-2")
    assert ready.state is AgentRunStateV1.READY_TO_COMPLETE
    completed = service.complete(run_id, operation_id="run/complete")
    assert completed.state is AgentRunStateV1.COMPLETED
    assert store.current() is None

    reloaded = PersonalAgentRunOperationalStore(store.root)
    history = reloaded.history(run_id)
    assert history[-1] == completed
    assert len(history) == 8
    ledger = reloaded.records_path.read_text(encoding="utf-8")
    assert "credential_profile_id" not in ledger
    assert "confirmation_token" not in ledger


def test_store_rejects_second_current_run_and_preserves_hash_chain(tmp_path: Path) -> None:
    pack = _pack()
    proposal = _proposal(pack)
    store = PersonalAgentRunOperationalStore(tmp_path / "operational")
    service = AgentRunService(store)
    service.accept(
        pack,
        proposal,
        operation_id="first/accept",
        run_id="0199f6c0-0000-7000-8000-000000000002",
    )

    with pytest.raises(PersonalAgentRunStoreStateConflictError):
        service.accept(
            pack,
            proposal,
            operation_id="second/accept",
            run_id="0199f6c0-0000-7000-8000-000000000003",
        )

    verified = store.read_verified_snapshot()
    assert verified.manifest.record_count == 1
    assert verified.envelopes[0].expected_record_digest == verified.envelopes[0].record_digest


def test_store_rejects_tampered_snapshot_after_restart(tmp_path: Path) -> None:
    pack = _pack()
    proposal = _proposal(pack)
    store = PersonalAgentRunOperationalStore(tmp_path / "operational")
    service = AgentRunService(store)
    service.accept(
        pack,
        proposal,
        operation_id="tamper/accept",
        run_id="0199f6c0-0000-7000-8000-000000000004",
    )
    payload = store.records_path.read_text(encoding="utf-8")
    store.records_path.write_text(
        payload.replace('"record_fingerprint":"', '"record_fingerprint":"0', 1),
        encoding="utf-8",
    )

    with pytest.raises(PersonalAgentRunStoreCorruptError):
        PersonalAgentRunOperationalStore(store.root)
