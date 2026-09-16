"""Adversarial privacy and integration regressions for the Stage 16 surface."""

from __future__ import annotations

import ast
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import cast
from uuid import UUID, uuid7

import pytest

from second_brain.application.executive_strategy import (
    ExecutiveSourceAliasV1,
    ExecutiveSourceReadinessV1,
    goal_identity_fingerprint,
)
from second_brain.application.goal_progress_read import GoalProgressRequestV1
from second_brain.application.growth import GrowthGoalIdentityV1
from second_brain.application.ports import AdvisorPort
from second_brain.entrypoints.web.goal_progress import GoalProgressWebService
from second_brain.entrypoints.web.growth import (
    GrowthGoalOwnerItemV1,
    GrowthGoalsProjectionV1,
    GrowthWebService,
)
from second_brain.entrypoints.web.personal_strategy import (
    PersonalStrategyContextRequest,
    PersonalStrategySourceChangedError,
    ProductionPersonalStrategyWebService,
)

NOW = datetime(2026, 9, 16, 6, 0, tzinfo=UTC)


def _goal(source_note_uuid: UUID | None = None) -> GrowthGoalIdentityV1:
    return GrowthGoalIdentityV1(
        source_note_uuid=source_note_uuid or uuid7(),
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


class _GoalsOnlyService:
    def __init__(self, item: GrowthGoalOwnerItemV1) -> None:
        self.projection = GrowthGoalsProjectionV1(
            generated_at=NOW,
            eligible_goal_count=1,
            goals=(item,),
        )

    def goals(self) -> GrowthGoalsProjectionV1:
        return self.projection

    def execute(self, request: object) -> object:
        del request
        raise RuntimeError("stale Growth fixture")


class _StaleProgressService:
    def read(self, request: GoalProgressRequestV1) -> object:
        del request
        raise RuntimeError("stale Progress fixture")


def _production_service(goal: GrowthGoalIdentityV1) -> ProductionPersonalStrategyWebService:
    item = GrowthGoalOwnerItemV1(
        goal=goal,
        goal_text="Одна и та же формулировка",
        goal_identity_fingerprint=goal_identity_fingerprint(goal),
    )
    return ProductionPersonalStrategyWebService(
        advisor=cast(AdvisorPort, object()),
        growth_service=cast(GrowthWebService, _GoalsOnlyService(item)),
        progress_service=cast(GoalProgressWebService, _StaleProgressService()),
        clock=lambda: NOW,
    )


def test_goal_binding_rejects_wrong_uuid_and_stale_fingerprint_even_for_same_text() -> None:
    current = _goal()
    other = _goal()
    service = _production_service(current)

    with pytest.raises(PersonalStrategySourceChangedError):
        service._goal(str(other.source_note_uuid), goal_identity_fingerprint(other))
    with pytest.raises(PersonalStrategySourceChangedError):
        service._goal(str(current.source_note_uuid), "sha256:" + "f" * 64)


def test_stale_growth_and_progress_are_explicitly_missing_and_never_reused() -> None:
    goal = _goal()
    service = _production_service(goal)

    pack = service.build_context(
        PersonalStrategyContextRequest(
            goal_source_uuid=str(goal.source_note_uuid),
            goal_identity_fingerprint=goal_identity_fingerprint(goal),
            task="Что проверить сейчас?",
            constraints=(),
            current_context="",
        )
    )
    by_alias = {item.alias: item for item in pack.sources}

    assert (
        by_alias[ExecutiveSourceAliasV1.GROWTH_RELATION].readiness
        is ExecutiveSourceReadinessV1.MISSING
    )
    assert (
        by_alias[ExecutiveSourceAliasV1.PROGRESS_CURRENT].readiness
        is ExecutiveSourceReadinessV1.MISSING
    )
    assert (
        by_alias[ExecutiveSourceAliasV1.BEHAVIOR_RELATION].readiness
        is ExecutiveSourceReadinessV1.MISSING
    )
    assert (
        by_alias[ExecutiveSourceAliasV1.EXPERIMENT_TERMINAL].readiness
        is ExecutiveSourceReadinessV1.MISSING
    )
    assert (
        by_alias[ExecutiveSourceAliasV1.ADAPTIVE_PROFILE_ACTIVE].readiness
        is ExecutiveSourceReadinessV1.MISSING
    )
    assert (
        by_alias[ExecutiveSourceAliasV1.CALIBRATION_CAVEAT].readiness
        is ExecutiveSourceReadinessV1.MISSING
    )


def test_personal_strategy_browser_surface_has_no_storage_logs_or_private_sw_route() -> None:
    root = Path(__file__).parents[1]
    source_paths = (
        root / "web" / "src" / "personal-strategy-api.ts",
        root / "web" / "src" / "personal-strategy-surface.tsx",
        root / "src" / "second_brain" / "entrypoints" / "web" / "personal_strategy.py",
    )
    source = "\n".join(path.read_text(encoding="utf-8") for path in source_paths)
    for forbidden in (
        "localStorage",
        "sessionStorage",
        "indexedDB",
        "Cache Storage",
        "serviceWorker",
    ):
        assert forbidden.casefold() not in source.casefold()
    assert re.search(r"\bconsole\s*\.", source) is None
    assert re.search(r"\b(?:logger|logging|print)\s*\(", source) is None

    worker = (root / "web" / "src" / "sw.ts").read_text(encoding="utf-8")
    assert "personal-strategy" not in worker
    assert "isApiOrAuthPath" in worker
    assert "caches.open(" not in worker
    assert "caches.put(" not in worker


def test_personal_strategy_transport_does_not_import_network_or_process_capabilities() -> None:
    root = Path(__file__).parents[1]
    path = root / "src" / "second_brain" / "entrypoints" / "web" / "personal_strategy.py"
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    imported_roots: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported_roots.update(alias.name.split(".", 1)[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported_roots.add(node.module.split(".", 1)[0])

    assert imported_roots.isdisjoint({"httpx", "openai", "requests", "socket", "subprocess"})
