"""Focused private Web/API integration tests for Stage 11E Growth."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from uuid import UUID, uuid7

from fastapi import FastAPI
from fastapi.testclient import TestClient

from second_brain.application.growth import (
    BuildGrowthEngine,
    GrowthEngineRequestV1,
    GrowthEngineResultV1,
    GrowthGoalChoiceMappingReviewProjectionV1,
    GrowthGoalChoiceMappingSelectorV1,
    GrowthMappingAcceptanceRequestV1,
    GrowthMappingLifecycleEventV1,
    GrowthMappingLifecycleViewV1,
    GrowthMappingReviewRequestV1,
    growth_hash_json,
)
from second_brain.application.growth_learning import (
    GrowthLearningCandidateV1,
    GrowthLearningRequestV1,
    GrowthLearningResolutionResultV1,
    GrowthLearningResolutionV1,
    GrowthLearningResultV1,
    build_growth_learning_question,
    resolve_growth_learning_question,
)
from second_brain.entrypoints.web.app import (
    GROWTH_ENGINE_PATH,
    GROWTH_GOALS_PATH,
    GROWTH_LEARNING_QUESTIONS_PATH,
    GROWTH_LEARNING_REQUEST_HEADER_VALUE,
    GROWTH_LEARNING_RESOLVE_PATH,
    GROWTH_MAPPING_CONFIRM_PATH,
    GROWTH_MAPPING_REVIEW_PATH,
    GROWTH_MAPPING_STATUS_PATH,
    GROWTH_REQUEST_HEADER_NAME,
    GROWTH_REQUEST_HEADER_VALUE,
    MAX_RAW_GROWTH_BODY_BYTES,
    create_app,
)
from second_brain.entrypoints.web.auth import (
    DEFAULT_OAUTH_STATE_TTL_SECONDS,
    DEFAULT_SESSION_TTL_SECONDS,
    WebAuthConfig,
)
from second_brain.entrypoints.web.growth import (
    GrowthGoalOwnerItemV1,
    GrowthGoalsProjectionV1,
)
from second_brain.entrypoints.web.growth_learning import GrowthLearningWebService
from tests.test_growth_learning import NOW, _growth_fixture, _growth_request

BASE_URL = "http://127.0.0.1"


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


@dataclass(slots=True)
class _GrowthService:
    engine: BuildGrowthEngine
    goal_text: str = "PRIVATE GOAL TEXT"

    def goals(self) -> GrowthGoalsProjectionV1:
        result = self.engine.execute(GrowthEngineRequestV1())
        goal = result.goal_results[0].goal
        assert goal is not None
        return GrowthGoalsProjectionV1(
            generated_at=NOW,
            eligible_goal_count=1,
            goals=(
                GrowthGoalOwnerItemV1(
                    goal=goal,
                    goal_text=self.goal_text,
                    goal_identity_fingerprint=growth_hash_json(goal.as_dict()),
                ),
            ),
        )

    def execute(self, request: GrowthEngineRequestV1) -> GrowthEngineResultV1:
        return self.engine.execute(request)

    def review(
        self,
        request: GrowthMappingReviewRequestV1 | GrowthGoalChoiceMappingSelectorV1,
    ) -> GrowthGoalChoiceMappingReviewProjectionV1:
        return self.engine.review(request)

    def accept(self, request: GrowthMappingAcceptanceRequestV1) -> object:
        return self.engine.accept(request)

    def snapshot(self) -> tuple[GrowthMappingLifecycleViewV1, ...]:
        return self.engine.store.read_verified_snapshot()

    def invalidate(self, mapping_id: UUID, *, operation_id: UUID) -> GrowthMappingLifecycleEventV1:
        return self.engine.store.invalidate_mapping(mapping_id, operation_id=operation_id)

    def delete(self, mapping_id: UUID, *, operation_id: UUID) -> GrowthMappingLifecycleEventV1:
        return self.engine.store.delete_mapping(mapping_id, operation_id=operation_id)


@dataclass(slots=True)
class _LearningService(GrowthLearningWebService):
    source: object
    now: datetime = NOW
    candidate: GrowthLearningCandidateV1 | None = None
    result: GrowthLearningResultV1 | None = None

    def questions(self, request: GrowthLearningRequestV1) -> GrowthLearningResultV1:
        del request
        result = build_growth_learning_question(self.source, now=self.now)
        self.result = result
        self.candidate = result.candidate
        return result

    def resolve(
        self,
        request: GrowthLearningRequestV1,
        candidate: GrowthLearningCandidateV1,
        resolution: GrowthLearningResolutionV1,
    ) -> GrowthLearningResolutionResultV1:
        del request
        assert self.candidate is not None
        result = resolve_growth_learning_question(
            self.source,
            candidate,
            resolution,
            now=self.now,
        )
        self.candidate = None
        return result


def _headers(purpose: str = GROWTH_REQUEST_HEADER_VALUE) -> dict[str, str]:
    return {"X-Second-Brain-Request": purpose}


def _app(
    growth: _GrowthService,
    learning: _LearningService | None = None,
) -> FastAPI:
    return create_app(
        growth_web_service=growth,
        growth_learning_web_service=learning,
        web_auth_config=_disabled_auth_config(),
    )


def _relation_payload(result: GrowthEngineResultV1) -> dict[str, object]:
    relation = result.goal_results[0]
    assert relation.goal is not None
    assert relation.behavioral_option is not None
    assert relation.cohort_fingerprint is not None
    return {
        "source_note_uuid": str(relation.goal.source_note_uuid),
        "behavioral_cohort_fingerprint": relation.cohort_fingerprint,
        "behavioral_option_index": relation.behavioral_option.option_index,
        "behavioral_option_fingerprint": relation.behavioral_option.option_fingerprint,
    }


def test_growth_goals_and_read_result_are_server_owned_and_bounded(tmp_path: Path) -> None:
    engine, _store = _growth_fixture(tmp_path)
    growth = _GrowthService(engine)
    with TestClient(_app(growth), base_url=BASE_URL) as client:
        goals = client.post(GROWTH_GOALS_PATH, headers=_headers(), json={})
        result = client.post(
            GROWTH_ENGINE_PATH,
            headers=_headers(),
            json=_growth_request().as_dict(),
        )

    assert goals.status_code == 200
    assert goals.headers["cache-control"] == "no-store"
    assert goals.json()["goals"][0]["goal_text"] == "PRIVATE GOAL TEXT"
    assert result.status_code == 200
    assert result.json()["selection_mode"] == "selected_goal"
    assert result.json()["goal_results"][0]["state"] == "goal_mapping_missing"
    assert "PRIVATE GOAL TEXT" not in result.text


def test_growth_mapping_review_confirm_retry_and_status_are_integrated(tmp_path: Path) -> None:
    engine, store = _growth_fixture(tmp_path)
    growth = _GrowthService(engine)
    result = engine.execute(_growth_request())
    selector = _relation_payload(result)
    with TestClient(_app(growth), base_url=BASE_URL) as client:
        review = client.post(
            GROWTH_MAPPING_REVIEW_PATH,
            headers=_headers(),
            json={**selector, "relation": "conflicts_with_goal"},
        )
        confirm = client.post(
            GROWTH_MAPPING_CONFIRM_PATH,
            headers=_headers(),
            json={
                **selector,
                "relation": "conflicts_with_goal",
                "operation_id": str(uuid7()),
                "confirmed": True,
                "review_fingerprint": review.json()["candidate_mapping_fingerprint"],
            },
        )
        status = client.post(GROWTH_MAPPING_STATUS_PATH, headers=_headers(), json={})

    assert review.status_code == 200
    assert review.json()["goal_text"] == "PRIVATE GOAL BODY"
    assert confirm.status_code == 200
    assert confirm.json()["status"] == "accepted"
    assert status.status_code == 200
    assert status.json()["active_mapping_count"] == 1
    assert status.json()["mappings"][0]["relation"] == "conflicts_with_goal"
    assert store.read_active()


def test_growth_confirm_rejects_stale_review_without_write(tmp_path: Path) -> None:
    engine, store = _growth_fixture(tmp_path)
    growth = _GrowthService(engine)
    result = engine.execute(_growth_request())
    selector = _relation_payload(result)
    with TestClient(_app(growth), base_url=BASE_URL) as client:
        review = client.post(
            GROWTH_MAPPING_REVIEW_PATH,
            headers=_headers(),
            json={**selector, "relation": "supports_goal"},
        )
        stale = client.post(
            GROWTH_MAPPING_CONFIRM_PATH,
            headers=_headers(),
            json={
                **selector,
                "relation": "supports_goal",
                "operation_id": str(uuid7()),
                "confirmed": True,
                "review_fingerprint": growth_hash_json({"stale": True}),
            },
        )

    assert review.status_code == 200
    assert stale.status_code == 409
    assert stale.json()["error"]["code"] == "GROWTH_GOAL_MAPPING_STALE"
    assert store.read_active() == ()


def test_growth_learning_question_and_explicit_answer_are_ephemeral(tmp_path: Path) -> None:
    engine, _store = _growth_fixture(tmp_path)
    source = engine.execute(_growth_request())
    learning = _LearningService(source)
    request = GrowthLearningRequestV1(goal_source_uuid=source.selected_goal_source_uuid)
    with TestClient(_app(_GrowthService(engine), learning), base_url=BASE_URL) as client:
        question = client.post(
            GROWTH_LEARNING_QUESTIONS_PATH,
            headers=_headers(GROWTH_LEARNING_REQUEST_HEADER_VALUE),
            json=request.as_dict(),
        )
        candidate = question.json()["candidate"]
        resolved = client.post(
            GROWTH_LEARNING_RESOLVE_PATH,
            headers=_headers(GROWTH_LEARNING_REQUEST_HEADER_VALUE),
            json={
                "request": request.as_dict(),
                "candidate": candidate,
                "resolution": {
                    "candidate_id": candidate["candidate_id"],
                    "disposition": "answer",
                    "answer": "Уточнённый контекст владельца",
                },
            },
        )

    assert question.status_code == 200
    assert question.json()["status"] == "candidate"
    assert question.json()["candidate"]["reason_code"] == "missing_goal_mapping"
    assert resolved.status_code == 200
    assert resolved.json()["answer_draft"]["text"] == "Уточнённый контекст владельца"
    assert resolved.json()["handoff"] is None


def test_growth_boundaries_reject_foreign_origin_duplicates_and_non_post(tmp_path: Path) -> None:
    engine, _store = _growth_fixture(tmp_path)
    growth = _GrowthService(engine)
    with TestClient(_app(growth), base_url=BASE_URL) as client:
        foreign = client.post(
            GROWTH_GOALS_PATH,
            headers={**_headers(), "Origin": "https://evil.example"},
            json={},
        )
        duplicate = client.post(
            GROWTH_GOALS_PATH,
            headers={**_headers(), "Content-Type": "application/json"},
            content=b'{"x":{},"x":{}}',
        )
        method = client.get(GROWTH_GOALS_PATH)
        oversized = client.post(
            GROWTH_GOALS_PATH,
            headers={**_headers(), "Content-Type": "application/json"},
            content=b"x" * (MAX_RAW_GROWTH_BODY_BYTES + 1),
        )

    assert foreign.status_code == 400
    assert duplicate.status_code == 400
    assert method.status_code == 405
    assert method.headers["cache-control"] == "no-store"
    assert oversized.status_code == 413


def test_growth_security_contract_rejects_missing_purpose_content_type_and_extra_fields(
    tmp_path: Path,
) -> None:
    engine, store = _growth_fixture(tmp_path)
    growth = _GrowthService(engine)
    with TestClient(_app(growth), base_url=BASE_URL) as client:
        missing_purpose = client.post(GROWTH_GOALS_PATH, json={})
        wrong_content_type = client.post(
            GROWTH_GOALS_PATH,
            headers={GROWTH_REQUEST_HEADER_NAME: GROWTH_REQUEST_HEADER_VALUE},
            content=b"{}",
        )
        extra_field = client.post(
            GROWTH_GOALS_PATH,
            headers=_headers(),
            json={"unexpected": True},
        )
        hidden_openapi = client.get("/openapi.json")

    assert [
        response.status_code for response in (missing_purpose, wrong_content_type, extra_field)
    ] == [400, 400, 400]
    assert hidden_openapi.status_code == 404
    assert all(
        "access-control-allow-origin" not in response.headers
        for response in (
            missing_purpose,
            wrong_content_type,
            extra_field,
        )
    )
    assert store.read_active() == ()
