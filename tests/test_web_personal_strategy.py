"""Focused owner-bound Web/API tests for the Phase 16.4 surface."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import cast
from uuid import uuid7

from fastapi import FastAPI
from fastapi.testclient import TestClient

from second_brain.application.assistant import (
    AssistantEvidenceRef,
    AssistantEvidenceRole,
    AssistantEvidenceSource,
    AssistantResultEnvelopeV1,
    AssistantResultKind,
)
from second_brain.application.executive_strategy import (
    BuildExecutiveStrategy,
    ExecutiveContextPackV1,
    ExecutiveSourceAliasV1,
    ExecutiveSourceItemV1,
    ExecutiveSourceReadinessV1,
    ReviewedActionV1,
    StrategyProposalV1,
    build_executive_context_pack,
    build_reviewed_action,
    build_strategy_snapshot,
    goal_identity_fingerprint,
)
from second_brain.application.growth import GrowthGoalIdentityV1
from second_brain.application.ports import CancellationTokenSource
from second_brain.entrypoints.web.app import (
    PERSONAL_STRATEGY_ACCEPT_PATH,
    PERSONAL_STRATEGY_CONTEXT_PATH,
    PERSONAL_STRATEGY_GENERATE_PATH,
    PERSONAL_STRATEGY_REQUEST_HEADER_VALUE,
    PERSONAL_STRATEGY_STATE_PATH,
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


def _pack(goal: GrowthGoalIdentityV1) -> ExecutiveContextPackV1:
    sources = tuple(
        ExecutiveSourceItemV1(
            alias=alias,
            readiness=ExecutiveSourceReadinessV1.EXACT_CURRENT,
            reference_id=f"source:{index}",
            reference_fingerprint=f"{index + 3:x}" * 64,
            summary=f"Проекция {alias.value}",
            as_of=NOW,
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
        goal,
        goal_text="Улучшить выносливость",
        task="Что рассмотреть сейчас?",
        constraints=("Без перегрузки",),
        current_context="Есть 30 минут",
        sources=sources,
        as_of=NOW,
        expected_goal_identity_fingerprint=goal_identity_fingerprint(goal),
    )


def _proposal(pack: ExecutiveContextPackV1) -> StrategyProposalV1:
    result = AssistantResultEnvelopeV1(
        output_label="independent_recommendation_analysis",
        kind=AssistantResultKind.RECOMMENDATION,
        recommendation="Рассмотреть короткую тренировку и проверить самочувствие.",
        selected_option=None,
        rationale=("Связь с текущей целью подтверждена.", "Проверить наблюдаемый сигнал."),
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

    class RecordingAdvisor:
        def advise(self, request: object, *, cancellation: object) -> AssistantResultEnvelopeV1:
            del request, cancellation
            return result

    return BuildExecutiveStrategy(RecordingAdvisor()).execute(
        pack,
        cancellation=CancellationTokenSource().token,
        proposal_id=uuid7(),
        as_of=NOW,
    )


@dataclass(slots=True)
class _Service:
    pack: ExecutiveContextPackV1
    proposal: StrategyProposalV1
    generate_calls: int = 0
    accepted: object | None = None

    def state(self) -> dict[str, object]:
        return {"goals": [], "current_snapshots": [], "eligible_goal_count": 0}

    def build_context(self, request: object) -> ExecutiveContextPackV1:
        del request
        return self.pack

    def revalidate_context(self, pack: ExecutiveContextPackV1) -> ExecutiveContextPackV1:
        del pack
        return self.pack

    def current_snapshot(self, pack: ExecutiveContextPackV1) -> dict[str, object] | None:
        del pack
        return None

    def generate(self, pack: ExecutiveContextPackV1) -> StrategyProposalV1:
        assert pack == self.pack
        self.generate_calls += 1
        return self.proposal

    def reject(self, proposal: StrategyProposalV1, *, operation_id: str, reason: str) -> None:
        del proposal, operation_id, reason

    def accept(
        self,
        pack: ExecutiveContextPackV1,
        proposal: StrategyProposalV1,
        selected_actions: tuple[object, ...],
        *,
        operation_id: str,
        expected_prior_snapshot_id: str | None,
        expected_prior_snapshot_fingerprint: str | None,
    ) -> object:
        del operation_id, expected_prior_snapshot_id, expected_prior_snapshot_fingerprint
        assert pack == self.pack
        assert proposal == self.proposal
        typed_actions = cast(tuple[ReviewedActionV1, ...], selected_actions)
        self.accepted = build_strategy_snapshot(
            proposal,
            typed_actions,
            sequence=1,
            reviewed_at=NOW,
            accepted_at=NOW,
        )
        return self.accepted


def _headers(*, origin: str = BASE_URL) -> dict[str, str]:
    return {
        "Origin": origin,
        "X-Second-Brain-Request": PERSONAL_STRATEGY_REQUEST_HEADER_VALUE,
        "Content-Type": "application/json",
    }


def _app(service: _Service) -> FastAPI:
    return create_app(
        personal_strategy_web_service=service,
        web_auth_config=_disabled_auth_config(),
    )


def test_state_and_context_are_read_only_until_explicit_generate(tmp_path) -> None:
    del tmp_path
    goal = _goal()
    pack = _pack(goal)
    service = _Service(pack, _proposal(pack))

    with TestClient(_app(service), base_url=BASE_URL) as client:
        state = client.post(PERSONAL_STRATEGY_STATE_PATH, headers=_headers(), json={})
        assert state.status_code == 200
        context = client.post(
            PERSONAL_STRATEGY_CONTEXT_PATH,
            headers=_headers(),
            json={
                "goal_source_uuid": str(goal.source_note_uuid),
                "goal_identity_fingerprint": goal_identity_fingerprint(goal),
                "task": pack.task,
                "constraints": list(pack.constraints),
                "current_context": pack.current_context,
            },
        )

    assert context.status_code == 200
    assert context.headers["cache-control"] == "no-store"
    assert context.json()["context_pack"]["source_pack_fingerprint"] == pack.source_pack_fingerprint
    assert service.generate_calls == 0


def test_generate_requires_the_exact_provider_preview_and_calls_once() -> None:
    goal = _goal()
    pack = _pack(goal)
    service = _Service(pack, _proposal(pack))

    with TestClient(_app(service), base_url=BASE_URL) as client:
        context = client.post(
            PERSONAL_STRATEGY_CONTEXT_PATH,
            headers=_headers(),
            json={
                "goal_source_uuid": str(goal.source_note_uuid),
                "goal_identity_fingerprint": goal_identity_fingerprint(goal),
                "task": pack.task,
                "constraints": list(pack.constraints),
                "current_context": pack.current_context,
            },
        ).json()
        rejected = client.post(
            PERSONAL_STRATEGY_GENERATE_PATH,
            headers=_headers(),
            json={
                "context_pack": context["context_pack"],
                "provider_preview": "{}",
            },
        )
        generated = client.post(
            PERSONAL_STRATEGY_GENERATE_PATH,
            headers=_headers(),
            json={
                "context_pack": context["context_pack"],
                "provider_preview": context["provider_preview"]["canonical_json"],
            },
        )

    assert rejected.status_code == 409
    assert rejected.json()["error"]["code"] == "PERSONAL_STRATEGY_PREVIEW_MISMATCH"
    assert generated.status_code == 200
    assert (
        generated.json()["proposal"]["proposal_fingerprint"]
        == service.proposal.proposal_fingerprint
    )
    assert service.generate_calls == 1


def test_accept_revalidates_reviewed_action_and_returns_snapshot() -> None:
    goal = _goal()
    pack = _pack(goal)
    proposal = _proposal(pack)
    service = _Service(pack, proposal)
    candidate = proposal.candidates[0]
    reviewed = build_reviewed_action(candidate)

    with TestClient(_app(service), base_url=BASE_URL) as client:
        accepted = client.post(
            PERSONAL_STRATEGY_ACCEPT_PATH,
            headers=_headers(),
            json={
                "context_pack": pack.as_dict(),
                "proposal": proposal.as_dict(),
                "selected_actions": [reviewed.as_dict()],
                "operation_id": str(uuid7()),
                "expected_prior_snapshot_id": None,
                "expected_prior_snapshot_fingerprint": None,
            },
        )

    assert accepted.status_code == 200
    assert accepted.json()["status"] == "accepted"
    assert accepted.json()["snapshot"]["selected_actions"][0]["edited"] is False
    assert service.accepted is not None


def test_boundary_rejects_missing_origin_unknown_fields_and_wrong_method() -> None:
    goal = _goal()
    pack = _pack(goal)
    service = _Service(pack, _proposal(pack))

    with TestClient(_app(service), base_url=BASE_URL) as client:
        missing_origin = client.post(
            PERSONAL_STRATEGY_STATE_PATH,
            headers={
                "X-Second-Brain-Request": PERSONAL_STRATEGY_REQUEST_HEADER_VALUE,
                "Content-Type": "application/json",
            },
            json={},
        )
        unknown = client.post(
            PERSONAL_STRATEGY_STATE_PATH,
            headers=_headers(),
            json={"extra": True},
        )
        wrong_method = client.get(PERSONAL_STRATEGY_STATE_PATH, headers=_headers())

    assert missing_origin.status_code == 400
    assert unknown.status_code == 400
    assert wrong_method.status_code == 405
