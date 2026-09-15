"""Private owner-only Web/API tests for Personal Experiments v1."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

from fastapi import FastAPI
from fastapi.testclient import TestClient

from second_brain.application.personal_experiments_evaluator import (
    PersonalExperimentEvaluationRequestV1,
    PersonalExperimentEvaluationResultV1,
)
from second_brain.application.personal_experiments_safe_write import (
    PersonalExperimentSafeWritePlanV1,
    PersonalExperimentSafeWriteResult,
)
from second_brain.entrypoints.web.app import (
    GOAL_PROGRESS_DEFINITION_APPLY_PATH,
    GOAL_PROGRESS_DEFINITION_PREPARE_PATH,
    GOAL_PROGRESS_REQUEST_HEADER_VALUE,
    MAX_RAW_PERSONAL_EXPERIMENT_BODY_BYTES,
    PERSONAL_EXPERIMENT_DEFINITION_APPLY_PATH,
    PERSONAL_EXPERIMENT_DEFINITION_PREPARE_PATH,
    PERSONAL_EXPERIMENT_EVALUATE_PATH,
    PERSONAL_EXPERIMENT_REQUEST_HEADER_VALUE,
    PERSONAL_EXPERIMENTS_PATH,
    PersonalExperimentWebService,
    create_app,
)
from second_brain.entrypoints.web.auth import (
    DEFAULT_OAUTH_STATE_TTL_SECONDS,
    DEFAULT_SESSION_TTL_SECONDS,
    WebAuthConfig,
)
from second_brain.entrypoints.web.goal_progress import ProductionGoalProgressWebService
from second_brain.entrypoints.web.personal_experiments import ProductionPersonalExperimentWebService
from tests.conftest import create_vault, snapshot_tree, write_note

BASE_URL = "http://127.0.0.1"
NOW = datetime(2026, 9, 15, 12, 0, tzinfo=UTC)
EXPERIMENT_ID = "0198f4c5-6a00-7000-8000-000000000301"
HASH = "sha256:" + "1" * 64


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


def _headers(purpose: str = PERSONAL_EXPERIMENT_REQUEST_HEADER_VALUE) -> dict[str, str]:
    return {
        "Origin": BASE_URL,
        "X-Second-Brain-Request": purpose,
    }


def _result() -> PersonalExperimentEvaluationResultV1:
    return PersonalExperimentEvaluationResultV1(
        experiment_definition_id=EXPERIMENT_ID,
        experiment_definition_fingerprint=HASH,
        status="not_evaluated",
        as_of=NOW,
    )


def _goal_note(goal_id: str) -> str:
    return f"""---
id: {goal_id}
type: zettel
created: 2026-09-15T09:00:00Z
tags: []
second_brain_personal_memory: 1
evidence_kind: user_statement
self_kind: goal
evidence_at: 2026-09-15T08:00:00Z
evidence_at_precision: exact
domain: work
---
Завершить важный проект.
"""


@dataclass(slots=True)
class RecordingPersonalExperimentService:
    state_value: dict[str, object] = field(
        default_factory=lambda: {
            "web_contract": "personal_experiments_web_v1",
            "contract_id": "personal-experiments-v1",
            "derivation_id": "personal-experiment-derivation-v1",
            "policy_id": "personal-experiment-v1",
            "policy_fingerprint": HASH,
            "generated_at": "2026-09-15T12:00:00Z",
            "goals": [],
            "stage12_definitions": [],
            "stage12_observations": [],
            "experiments": [],
            "caveats": ["observed_change_is_not_proof_of_causation"],
        }
    )
    state_calls: int = 0
    evaluation_calls: list[PersonalExperimentEvaluationRequestV1] = field(default_factory=list)

    def state(self) -> dict[str, object]:
        self.state_calls += 1
        return self.state_value

    def prepare(self, draft: object) -> PersonalExperimentSafeWriteResult:
        del draft
        raise AssertionError("prepare must not run in this transport test")

    def apply(
        self,
        plan: PersonalExperimentSafeWritePlanV1,
        accepted_plan_sha256: str,
    ) -> PersonalExperimentSafeWriteResult:
        del plan, accepted_plan_sha256
        raise AssertionError("apply must not run in this transport test")

    def evaluate(
        self,
        request: PersonalExperimentEvaluationRequestV1,
    ) -> PersonalExperimentEvaluationResultV1:
        self.evaluation_calls.append(request)
        return _result()


def _app(service: RecordingPersonalExperimentService) -> FastAPI:
    return create_app(
        personal_experiments_web_service=cast(PersonalExperimentWebService, service),
        web_auth_config=_disabled_auth_config(),
    )


def test_list_is_strict_bounded_no_store_and_does_not_expose_storage() -> None:
    service = RecordingPersonalExperimentService()

    with TestClient(_app(service), base_url=BASE_URL) as client:
        response = client.post(PERSONAL_EXPERIMENTS_PATH, headers=_headers(), json={})

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["x-frame-options"] == "DENY"
    assert "access-control-allow-origin" not in response.headers
    assert service.state_calls == 1
    assert "content" not in response.text
    assert "relative_path" not in response.text
    assert "vault" not in response.text.casefold()


def test_list_rejects_unknown_fields_before_service_call() -> None:
    service = RecordingPersonalExperimentService()

    with TestClient(_app(service), base_url=BASE_URL) as client:
        response = client.post(
            PERSONAL_EXPERIMENTS_PATH,
            headers=_headers(),
            json={"unexpected": True},
        )

    assert response.status_code == 400
    assert response.json() == {
        "error": {
            "code": "PERSONAL_EXPERIMENT_WEB_INVALID_REQUEST",
            "message": "Запрос личного эксперимента не прошёл проверку.",
        }
    }
    assert service.state_calls == 0


def test_boundary_rejects_wrong_origin_purpose_and_method_without_service_call() -> None:
    service = RecordingPersonalExperimentService()

    with TestClient(_app(service), base_url=BASE_URL) as client:
        wrong_origin = client.post(
            PERSONAL_EXPERIMENTS_PATH,
            headers={**_headers(), "Origin": "https://attacker.example"},
            json={},
        )
        wrong_purpose = client.post(
            PERSONAL_EXPERIMENTS_PATH,
            headers=_headers("goal-progress-v1"),
            json={},
        )
        wrong_method = client.get(
            PERSONAL_EXPERIMENTS_PATH,
            headers=_headers(),
        )

    assert wrong_origin.status_code == 400
    assert wrong_purpose.status_code == 400
    assert wrong_method.status_code == 405
    assert service.state_calls == 0
    assert all(
        "attacker.example" not in response.text for response in (wrong_origin, wrong_purpose)
    )


def test_boundary_rejects_oversized_body_before_service_call() -> None:
    service = RecordingPersonalExperimentService()
    oversized = "{" + '"x":' + '"a' * MAX_RAW_PERSONAL_EXPERIMENT_BODY_BYTES + '"}'

    with TestClient(_app(service), base_url=BASE_URL) as client:
        response = client.post(
            PERSONAL_EXPERIMENTS_PATH,
            headers={**_headers(), "Content-Type": "application/json"},
            content=oversized,
        )

    assert response.status_code == 413
    assert response.json()["error"]["code"] == "PERSONAL_EXPERIMENT_WEB_CONTENT_TOO_LARGE"
    assert service.state_calls == 0


def test_evaluation_projects_exact_provider_free_result() -> None:
    service = RecordingPersonalExperimentService()
    request_body = {
        "experiment_definition_id": EXPERIMENT_ID,
        "experiment_definition_fingerprint": HASH,
        "as_of": "2026-09-15T12:00:00Z",
    }

    with TestClient(_app(service), base_url=BASE_URL) as client:
        response = client.post(
            PERSONAL_EXPERIMENT_EVALUATE_PATH,
            headers=_headers(),
            json=request_body,
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["web_contract"] == "personal_experiment_evaluation_web_v1"
    assert payload["contract"] == "personal_experiment_result_v1"
    assert payload["experiment_definition_id"] == EXPERIMENT_ID
    assert payload["provenance"] == {
        "source": "current_vault",
        "provider": "none",
        "network": "none",
        "write": "none",
        "as_of": "2026-09-15T12:00:00Z",
        "experiment_definition_id": EXPERIMENT_ID,
        "experiment_definition_fingerprint": HASH,
        "goal_source_uuid": None,
        "goal_identity_fingerprint": None,
        "stage12_definition_id": None,
        "stage12_definition_fingerprint": None,
        "activation_record_id": None,
        "terminal_record_id": None,
        "included_enrollment_ids": [],
        "excluded_enrollment_ids": [],
    }
    assert service.evaluation_calls[0].as_of == NOW


def test_evaluation_rejects_unknown_fields_before_service_call() -> None:
    service = RecordingPersonalExperimentService()

    with TestClient(_app(service), base_url=BASE_URL) as client:
        response = client.post(
            PERSONAL_EXPERIMENT_EVALUATE_PATH,
            headers=_headers(),
            json={
                "experiment_definition_id": EXPERIMENT_ID,
                "experiment_definition_fingerprint": HASH,
                "as_of": "2026-09-15T12:00:00Z",
                "provider": "unexpected",
            },
        )

    assert response.status_code == 400
    assert service.evaluation_calls == []


def test_evaluation_rejects_duplicate_json_keys_before_service_call() -> None:
    service = RecordingPersonalExperimentService()
    duplicate_body = (
        '{"experiment_definition_id":"'
        + EXPERIMENT_ID
        + '","experiment_definition_fingerprint":"'
        + HASH
        + '","as_of":"2026-09-15T12:00:00Z","as_of":"2026-09-15T12:01:00Z"}'
    )

    with TestClient(_app(service), base_url=BASE_URL) as client:
        response = client.post(
            PERSONAL_EXPERIMENT_EVALUATE_PATH,
            headers={**_headers(), "Content-Type": "application/json"},
            content=duplicate_body,
        )

    assert response.status_code == 400
    assert service.evaluation_calls == []


def test_production_list_projects_only_current_goals_without_storage_metadata(
    tmp_path: Path,
) -> None:
    vault = create_vault(tmp_path / "vault")
    write_note(vault, "10 Projects/Goal.md", _goal_note(EXPERIMENT_ID))
    env_file = tmp_path / "runtime.env"
    env_file.write_text("SECOND_BRAIN_VAULT_PATH=./vault\n", encoding="utf-8")
    service = ProductionPersonalExperimentWebService(
        env_file=env_file,
        vault_path_override=str(vault),
        clock=lambda: NOW,
    )

    with TestClient(
        create_app(
            personal_experiments_web_service=cast(PersonalExperimentWebService, service),
            web_auth_config=_disabled_auth_config(),
        ),
        base_url=BASE_URL,
    ) as client:
        response = client.post(PERSONAL_EXPERIMENTS_PATH, headers=_headers(), json={})

    assert response.status_code == 200
    assert response.json()["goals"][0]["source_note_uuid"] == EXPERIMENT_ID
    assert response.json()["experiments"] == []
    assert str(vault) not in response.text
    assert "relative_path" not in response.text


def test_production_definition_uses_stage12_review_and_safe_write(tmp_path: Path) -> None:
    vault = create_vault(tmp_path / "vault")
    write_note(vault, "_templates/Zettel.md", "# Zettel\n")
    write_note(vault, "10 Projects/Goal.md", _goal_note(EXPERIMENT_ID))
    env_file = tmp_path / "runtime.env"
    env_file.write_text("SECOND_BRAIN_VAULT_PATH=./vault\n", encoding="utf-8")
    goal_progress_service = ProductionGoalProgressWebService(
        env_file=env_file,
        vault_path_override=str(vault),
        clock=lambda: NOW,
    )
    personal_service = ProductionPersonalExperimentWebService(
        env_file=env_file,
        vault_path_override=str(vault),
        clock=lambda: NOW,
    )
    before = snapshot_tree(vault)
    progress_headers = {
        "Origin": BASE_URL,
        "X-Second-Brain-Request": GOAL_PROGRESS_REQUEST_HEADER_VALUE,
    }

    with TestClient(
        create_app(
            goal_progress_web_service=goal_progress_service,
            personal_experiments_web_service=personal_service,
            web_auth_config=_disabled_auth_config(),
        ),
        base_url=BASE_URL,
    ) as client:
        initial = client.post(PERSONAL_EXPERIMENTS_PATH, headers=_headers(), json={})
        goal = initial.json()["goals"][0]
        progress_review_response = client.post(
            GOAL_PROGRESS_DEFINITION_PREPARE_PATH,
            headers=progress_headers,
            json={
                "goal_source_uuid": goal["source_note_uuid"],
                "progress_model": "numeric_target",
                "metric_id": "weight",
                "unit": "kg",
                "baseline": "80",
                "target": "72",
                "direction": "decrease_to",
                "lower_bound": "40",
                "upper_bound": "120",
                "milestones": None,
                "supersedes_definition_id": None,
            },
        )
        assert progress_review_response.status_code == 200, progress_review_response.text
        progress = progress_review_response.json()
        progress_applied = client.post(
            GOAL_PROGRESS_DEFINITION_APPLY_PATH,
            headers=progress_headers,
            json={
                "review_token": progress["review_token"],
                "accepted_plan_sha256": progress["plan_sha256"],
                "confirmed": True,
            },
        )
        assert progress_applied.status_code == 200
        after_stage12 = snapshot_tree(vault)
        stage12 = client.post(PERSONAL_EXPERIMENTS_PATH, headers=_headers(), json={}).json()
        stage12_definition = stage12["stage12_definitions"][0]
        personal_review_response = client.post(
            PERSONAL_EXPERIMENT_DEFINITION_PREPARE_PATH,
            headers=_headers(),
            json={
                "goal_source_uuid": goal["source_note_uuid"],
                "goal_identity_fingerprint": goal["goal_identity_fingerprint"],
                "goal_progress_definition_id": stage12_definition["id"],
                "goal_progress_definition_fingerprint": stage12_definition[
                    "definition_fingerprint"
                ],
                "hypothesis": "Две короткие сессии помогут завершать больше задач.",
                "intervention": "Планировать две короткие сессии каждый рабочий день.",
                "baseline_strategy": "stage12_definition_explicit",
                "baseline_observation_uuid": None,
                "baseline_observation_fingerprint": None,
                "supersedes_definition_id": None,
                "supersedes_definition_fingerprint": None,
            },
        )
        assert personal_review_response.status_code == 200
        personal_review = personal_review_response.json()
        assert personal_review["status"] == "dry-run"
        assert '"content":' not in personal_review_response.text
        assert "relative_path" not in personal_review_response.text
        assert snapshot_tree(vault) == after_stage12
        personal_applied = client.post(
            PERSONAL_EXPERIMENT_DEFINITION_APPLY_PATH,
            headers=_headers(),
            json={
                "review_token": personal_review["review_token"],
                "accepted_plan_sha256": personal_review["plan_sha256"],
                "confirmed": True,
            },
        )
        final = client.post(PERSONAL_EXPERIMENTS_PATH, headers=_headers(), json={})

    assert personal_applied.status_code == 200
    assert personal_applied.json()["status"] == "saved"
    assert final.status_code == 200
    assert len(final.json()["experiments"]) == 1
    assert len(snapshot_tree(vault)) == len(before) + 2
