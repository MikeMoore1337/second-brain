"""Focused owner-bound Web/API tests for the Phase 17.4 surface."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import cast
from uuid import UUID, uuid7

from fastapi import FastAPI
from fastapi.testclient import TestClient

from second_brain.application.assistant import (
    ASSISTANT_CONTRACT_VERSION,
    ASSISTANT_OUTPUT_LABEL,
    AssistantEvidenceRef,
    AssistantEvidenceRole,
    AssistantEvidenceSource,
    AssistantInputRef,
    AssistantInputSource,
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
    BuildPersonalPlanner,
    PlanningActionBindingV1,
    PlanningContextPackV1,
    PlanningEffortSourceV1,
    PlanningGoalRefV1,
    PlanningGoalSelectionV1,
    PlanningItemKindV1,
    PlanningItemV1,
    PlanningProposalV1,
    PlanningWindowKindV1,
    PlanningWindowV1,
    build_planning_context_pack,
    build_planning_provider_envelope,
)
from second_brain.application.personal_planning_store import PersonalPlanningOperationalStore
from second_brain.application.ports import AdvisorPort, CancellationToken, CancellationTokenSource
from second_brain.entrypoints.web.app import (
    PERSONAL_PLANNING_ACCEPT_PATH,
    PERSONAL_PLANNING_CONTEXT_PATH,
    PERSONAL_PLANNING_EDIT_PATH,
    PERSONAL_PLANNING_GENERATE_PATH,
    PERSONAL_PLANNING_REQUEST_HEADER_VALUE,
    PERSONAL_PLANNING_STATE_PATH,
    create_app,
)
from second_brain.entrypoints.web.auth import (
    DEFAULT_OAUTH_STATE_TTL_SECONDS,
    DEFAULT_SESSION_TTL_SECONDS,
    WebAuthConfig,
)

BASE_URL = "http://127.0.0.1"
NOW = datetime(2026, 9, 16, 6, 0, tzinfo=UTC)


def _disabled_auth_config() -> WebAuthConfig:
    return WebAuthConfig(
        mode="disabled",
        public_base_url=None,
        github_client_id=None,
        github_client_secret=None,
        allowed_user_id=None,
        session_secret=None,
        session_ttl_seconds=DEFAULT_SESSION_TTL_SECONDS,
        oauth_state_ttl_seconds=DEFAULT_OAUTH_STATE_TTL_SECONDS,
        trusted_authorities=frozenset(),
    )


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


def _snapshot(goal: GrowthGoalIdentityV1) -> StrategySnapshotV1:
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
        (build_reviewed_action(candidate),),
        sequence=1,
        reviewed_at=NOW,
        accepted_at=NOW,
        snapshot_id=uuid7(),
    )


def _pack() -> PlanningContextPackV1:
    goal = _goal()
    return build_planning_context_pack(
        (
            PlanningGoalSelectionV1(
                goal=goal,
                goal_text="Улучшить выносливость",
                strategy_snapshot=_snapshot(goal),
                selected_action_ids=("action-1",),
            ),
        ),
        start_local="2026-09-16",
        end_local="2026-09-18",
        timezone="UTC",
        available_minutes_by_date={
            "2026-09-16": 30,
            "2026-09-17": 30,
            "2026-09-18": 30,
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
        planning_constraints=("Не перегружать день.",),
        planning_context="Доступно немного времени.",
        as_of=NOW,
    )


class _Advisor(AdvisorPort):
    def __init__(self, recommendation: str) -> None:
        self.recommendation = recommendation
        self.calls = 0

    def advise(
        self,
        request: object,
        *,
        cancellation: CancellationToken,
    ) -> AssistantResultEnvelopeV1:
        del request, cancellation
        self.calls += 1
        return AssistantResultEnvelopeV1(
            output_label=ASSISTANT_OUTPUT_LABEL,
            kind=AssistantResultKind.RECOMMENDATION,
            recommendation=self.recommendation,
            selected_option=None,
            rationale=("Связано с явной целью.",),
            evidence_refs=(
                AssistantEvidenceRef(
                    AssistantEvidenceSource.EXPLICIT_CONTEXT,
                    1,
                    AssistantEvidenceRole.REPORTED_FACT,
                ),
            ),
            constraints_used=(AssistantInputRef(AssistantInputSource.EXPLICIT_CONSTRAINT, 1),),
            objectives_used=(AssistantInputRef(AssistantInputSource.EXPLICIT_GOAL, 1),),
            uncertainty=("Требует проверки владельца.",),
            abstention_code=None,
            contract_version=ASSISTANT_CONTRACT_VERSION,
        )


def _proposal(pack: PlanningContextPackV1) -> PlanningProposalV1:
    binding = pack.portfolio[0]
    item = {
        "item_id": "next-1",
        "kind": PlanningItemKindV1.NEXT_ACTION.value,
        "title": "Сделать один небольшой шаг",
        "description": "Выполнить выбранное действие в доступное окно.",
        "goal_refs": [
            PlanningGoalRefV1(
                goal_source_uuid=binding.goal_source_uuid,
                goal_identity_fingerprint=binding.goal_identity_fingerprint,
            ).as_dict()
        ],
        "action_refs": [
            PlanningActionBindingV1.from_action_ref(
                binding.selected_reviewed_action_refs[0]
            ).as_dict()
        ],
        "parent_item_id": None,
        "target_start_local": "2026-09-18T09:00",
        "target_end_local": "2026-09-18T09:30",
        "effort_minutes": 30,
        "effort_source": PlanningEffortSourceV1.PROVIDER_PROPOSED.value,
        "dependency_ids": [],
    }
    recommendation = json.dumps(
        {
            "result_state": "proposal",
            "items": [item],
            "suggested_order": ["next-1"],
            "reasons": ["Связь с выбранной целью сохранена."],
            "caveats": ["Время требует проверки владельца."],
        },
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return BuildPersonalPlanner(_Advisor(recommendation)).execute(
        pack,
        cancellation=CancellationTokenSource().token,
        as_of=NOW,
        proposal_id=uuid7(),
    )


@dataclass(slots=True)
class _Service:
    pack: PlanningContextPackV1
    proposal: PlanningProposalV1
    planning_store: PersonalPlanningOperationalStore
    advisor_calls: int = 0

    def state(self) -> dict[str, object]:
        return {
            "goals": [],
            "eligible_goal_count": 0,
            "generated_at": "2026-09-16T06:00:00Z",
            "current_plan": None,
            "caveats": [],
        }

    def build_context(self, request: object) -> PlanningContextPackV1:
        del request
        return self.pack

    def revalidate_context(self, pack: PlanningContextPackV1) -> PlanningContextPackV1:
        return self.pack if pack == self.pack else pack

    def current_plan(self) -> object:
        return self.planning_store.current_plan()

    def generate(self, pack: PlanningContextPackV1) -> PlanningProposalV1:
        assert pack == self.pack
        self.advisor_calls += 1
        return self.proposal

    def accept(
        self,
        pack: PlanningContextPackV1,
        proposal: PlanningProposalV1,
        *,
        selected_item_ids: tuple[str, ...],
        item_order: tuple[str, ...],
        operation_id: str,
        expected_current_plan_fingerprint: str | None,
        accepted_at: datetime | None,
    ) -> object:
        return self.planning_store.accept(
            proposal,
            context_pack=pack,
            selected_item_ids=selected_item_ids,
            item_order=item_order,
            operation_id=operation_id,
            expected_current_plan_fingerprint=expected_current_plan_fingerprint,
            accepted_at=accepted_at,
        )

    def edit(
        self,
        *,
        items: tuple[object, ...],
        selected_item_ids: tuple[str, ...],
        item_order: tuple[str, ...],
        operation_id: str,
        expected_current_plan_fingerprint: str,
        edited_at: datetime | None,
    ) -> object:
        return self.planning_store.edit(
            items=cast(tuple[PlanningItemV1, ...], items),
            selected_item_ids=selected_item_ids,
            item_order=item_order,
            operation_id=operation_id,
            expected_current_plan_fingerprint=expected_current_plan_fingerprint,
            edited_at=edited_at,
        )


def _headers() -> dict[str, str]:
    return {
        "Origin": BASE_URL,
        "X-Second-Brain-Request": PERSONAL_PLANNING_REQUEST_HEADER_VALUE,
        "Content-Type": "application/json",
    }


def _context_body(pack: PlanningContextPackV1) -> dict[str, object]:
    return {
        "goal_source_uuids": [str(pack.portfolio[0].goal_source_uuid)],
        "start_local": pack.start_local,
        "end_local": pack.end_local,
        "timezone": pack.timezone,
        "capacity": [entry.as_dict() for entry in pack.capacity],
        "fixed_windows": [window.as_dict() for window in pack.fixed_windows],
        "planning_constraints": list(pack.planning_constraints),
        "planning_context": pack.planning_context,
    }


def _app(service: _Service) -> FastAPI:
    return create_app(
        personal_planning_web_service=service,
        web_auth_config=_disabled_auth_config(),
    )


def test_state_and_context_keep_provider_uninvoked(tmp_path: Path) -> None:
    pack = _pack()
    store = PersonalPlanningOperationalStore(tmp_path / "personal-planning")
    service = _Service(pack, _proposal(pack), store)

    with TestClient(_app(service), base_url=BASE_URL) as client:
        state = client.post(PERSONAL_PLANNING_STATE_PATH, headers=_headers(), json={})
        context = client.post(
            PERSONAL_PLANNING_CONTEXT_PATH,
            headers=_headers(),
            json=_context_body(pack),
        )

    assert state.status_code == 200
    assert context.status_code == 200
    assert context.headers["cache-control"] == "no-store"
    assert context.json()["context_pack"]["pack_fingerprint"] == pack.pack_fingerprint
    assert service.advisor_calls == 0


def test_generate_requires_exact_preview_and_accepts_one_current_plan(tmp_path: Path) -> None:
    pack = _pack()
    store = PersonalPlanningOperationalStore(tmp_path / "personal-planning")
    service = _Service(pack, _proposal(pack), store)

    with TestClient(_app(service), base_url=BASE_URL) as client:
        context = client.post(
            PERSONAL_PLANNING_CONTEXT_PATH,
            headers=_headers(),
            json=_context_body(pack),
        ).json()
        rejected = client.post(
            PERSONAL_PLANNING_GENERATE_PATH,
            headers=_headers(),
            json={"context_pack": context["context_pack"], "provider_preview": "{}"},
        )
        generated = client.post(
            PERSONAL_PLANNING_GENERATE_PATH,
            headers=_headers(),
            json={
                "context_pack": context["context_pack"],
                "provider_preview": context["provider_preview"]["canonical_json"],
            },
        )

        accepted = client.post(
            PERSONAL_PLANNING_ACCEPT_PATH,
            headers=_headers(),
            json={
                "context_pack": context["context_pack"],
                "proposal": generated.json()["proposal"],
                "selected_item_ids": ["next-1"],
                "item_order": ["next-1"],
                "operation_id": str(uuid7()),
                "accepted_at": "2026-09-16T06:00:00Z",
                "expected_current_plan_fingerprint": None,
            },
        )

    assert rejected.status_code == 409
    assert rejected.json()["error"]["code"] == "PERSONAL_PLANNING_PREVIEW_MISMATCH"
    assert generated.status_code == 200
    assert service.advisor_calls == 1
    assert accepted.status_code == 200
    assert accepted.json()["plan"]["selected_item_ids"] == ["next-1"]

    plan = accepted.json()["plan"]
    with TestClient(_app(service), base_url=BASE_URL) as client:
        edited = client.post(
            PERSONAL_PLANNING_EDIT_PATH,
            headers=_headers(),
            json={
                "items": [{**plan["items"][0], "title": "Сделать проверенный шаг"}],
                "selected_item_ids": ["next-1"],
                "item_order": ["next-1"],
                "operation_id": str(uuid7()),
                "edited_at": "2026-09-16T06:05:00Z",
                "expected_current_plan_fingerprint": plan["plan_fingerprint"],
            },
        )

    assert edited.status_code == 200
    assert edited.json()["plan"]["revision"] == 2
    assert edited.json()["plan"]["items"][0]["title"] == "Сделать проверенный шаг"


def test_boundary_rejects_wrong_purpose_unknown_fields_and_wrong_method(tmp_path: Path) -> None:
    pack = _pack()
    service = _Service(pack, _proposal(pack), PersonalPlanningOperationalStore(tmp_path / "store"))

    with TestClient(_app(service), base_url=BASE_URL) as client:
        wrong_purpose = client.post(
            PERSONAL_PLANNING_STATE_PATH,
            headers={**_headers(), "X-Second-Brain-Request": "wrong-v1"},
            json={},
        )
        unknown = client.post(
            PERSONAL_PLANNING_STATE_PATH,
            headers=_headers(),
            json={"extra": True},
        )
        wrong_method = client.get(PERSONAL_PLANNING_STATE_PATH, headers=_headers())

    assert wrong_purpose.status_code == 400
    assert unknown.status_code == 400
    assert wrong_method.status_code == 405


def test_preview_is_the_same_canonical_bytes_as_the_application_boundary() -> None:
    pack = _pack()
    preview = build_planning_provider_envelope(pack)
    assert preview.canonical_json == preview.canonical_bytes.decode("utf-8")
