"""Private Web/API integration tests for Stage 12E."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from second_brain.application.goal_progress import GOAL_PROGRESS_POLICY_FINGERPRINT
from second_brain.entrypoints.web.app import (
    GOAL_PROGRESS_DEFINITION_APPLY_PATH,
    GOAL_PROGRESS_DEFINITION_PREPARE_PATH,
    GOAL_PROGRESS_OBSERVATION_APPLY_PATH,
    GOAL_PROGRESS_OBSERVATION_PREPARE_PATH,
    GOAL_PROGRESS_PATH,
    GOAL_PROGRESS_REQUEST_HEADER_VALUE,
    GROWTH_GOAL_PROGRESS_PATH,
    GROWTH_GOAL_PROGRESS_REQUEST_HEADER_VALUE,
    create_app,
)
from second_brain.entrypoints.web.auth import (
    DEFAULT_OAUTH_STATE_TTL_SECONDS,
    DEFAULT_SESSION_TTL_SECONDS,
    WebAuthConfig,
)
from second_brain.entrypoints.web.goal_progress import ProductionGoalProgressWebService
from tests.conftest import create_vault, snapshot_tree, write_note
from tests.test_growth_goal_progress_composition import GOAL_ID as COMPOSITION_GOAL_ID
from tests.test_growth_goal_progress_composition import _seed as composition_seed

FIXED_NOW = datetime(2026, 9, 14, 12, 0, tzinfo=UTC)
AS_OF = "2026-09-14T14:00:00Z"


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


def _headers(purpose: str = GOAL_PROGRESS_REQUEST_HEADER_VALUE) -> dict[str, str]:
    return {"X-Second-Brain-Request": purpose}


def _goal_note(goal_id: str, body: str = "Хочу завершить проект.") -> str:
    return f"""---
id: {goal_id}
type: zettel
created: 2026-09-14T09:00:00Z
tags: []
second_brain_personal_memory: 1
evidence_kind: user_statement
self_kind: goal
evidence_at: 2026-09-14T08:00:00Z
evidence_at_precision: exact
domain: work
---
{body}
"""


def _install_templates(vault: Path) -> None:
    for name in ("Project.md", "Area.md", "Resource.md", "Zettel.md"):
        write_note(vault, f"_templates/{name}", f"# {name}\n")


def _service(vault: Path, env_file: Path) -> ProductionGoalProgressWebService:
    return ProductionGoalProgressWebService(
        env_file=env_file,
        vault_path_override=str(vault),
        clock=lambda: FIXED_NOW,
    )


def _app(service: ProductionGoalProgressWebService) -> FastAPI:
    return create_app(
        goal_progress_web_service=service,
        web_auth_config=_disabled_auth_config(),
    )


def _numeric_definition_payload(goal_id: str) -> dict[str, object]:
    return {
        "goal_source_uuid": goal_id,
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
    }


def test_read_and_composition_are_private_bounded_and_do_not_write(tmp_path: Path) -> None:
    vault, _store, _reader = composition_seed(tmp_path)
    env_file = tmp_path / "runtime.env"
    env_file.write_text("SECOND_BRAIN_VAULT_PATH=./vault\n", encoding="utf-8")
    service = _service(vault, env_file)

    before = snapshot_tree(vault)
    with TestClient(_app(service), base_url="http://127.0.0.1") as client:
        progress = client.post(
            GOAL_PROGRESS_PATH,
            headers=_headers(),
            json={"goal_source_uuid": str(COMPOSITION_GOAL_ID), "as_of": AS_OF},
        )
        composition = client.post(
            GROWTH_GOAL_PROGRESS_PATH,
            headers=_headers(GROWTH_GOAL_PROGRESS_REQUEST_HEADER_VALUE),
            json={"goal_source_uuid": str(COMPOSITION_GOAL_ID), "progress_as_of": AS_OF},
        )

    assert progress.status_code == 200
    assert progress.headers["cache-control"] == "no-store"
    assert progress.json()["status"] == "definition_missing"
    assert progress.json()["goal"]["text"] == "Цель A"
    assert "raw" not in progress.text.casefold()
    assert composition.status_code == 200
    assert composition.json()["web_contract"] == "growth_goal_progress_composition_web_v1"
    assert composition.json()["growth_result"]["selected_goal_source_uuid"] == str(
        COMPOSITION_GOAL_ID
    )
    assert composition.json()["goal_progress_result"]["status"] == "definition_missing"
    assert snapshot_tree(vault) == before


def test_numeric_definition_and_observation_use_prepare_review_apply(tmp_path: Path) -> None:
    vault = create_vault(tmp_path / "vault")
    _install_templates(vault)
    goal_id = "0198f4c5-6a00-7000-8000-000000000301"
    write_note(vault, "10 Projects/Goal.md", _goal_note(goal_id))
    env_file = tmp_path / "runtime.env"
    env_file.write_text("SECOND_BRAIN_VAULT_PATH=./vault\n", encoding="utf-8")
    service = _service(vault, env_file)

    with TestClient(_app(service), base_url="http://127.0.0.1") as client:
        goals = service.goals()
        selected = goals.goals[0].goal.source_note_uuid
        before = snapshot_tree(vault)
        prepared = client.post(
            GOAL_PROGRESS_DEFINITION_PREPARE_PATH,
            headers=_headers(),
            json=_numeric_definition_payload(str(selected)),
        )

        assert prepared.status_code == 200
        review = prepared.json()
        assert review["status"] == "dry-run"
        assert (
            review["record"]["goal_progress_policy_fingerprint"] == GOAL_PROGRESS_POLICY_FINGERPRINT
        )
        assert "content" not in review
        assert str(vault) not in prepared.text
        assert snapshot_tree(vault) == before

        wrong_hash = client.post(
            GOAL_PROGRESS_DEFINITION_APPLY_PATH,
            headers=_headers(),
            json={
                "review_token": review["review_token"],
                "accepted_plan_sha256": "sha256:" + "0" * 64,
                "confirmed": True,
            },
        )
        assert wrong_hash.status_code == 409
        assert wrong_hash.json()["error"]["code"] == "GOAL_PROGRESS_SAFE_WRITE_REVIEW_REQUIRED"
        assert snapshot_tree(vault) == before

        applied = client.post(
            GOAL_PROGRESS_DEFINITION_APPLY_PATH,
            headers=_headers(),
            json={
                "review_token": review["review_token"],
                "accepted_plan_sha256": review["plan_sha256"],
                "confirmed": True,
            },
        )
        assert applied.status_code == 200
        definition_id = applied.json()["record_id"]
        assert applied.json()["status"] == "saved"
        assert len(snapshot_tree(vault)) == len(before) + 1

        for value, observed_at in (
            ("80", "2026-09-14T12:30:00Z"),
            ("76.5", "2026-09-14T13:00:00Z"),
        ):
            observation = client.post(
                GOAL_PROGRESS_OBSERVATION_PREPARE_PATH,
                headers=_headers(),
                json={
                    "goal_source_uuid": str(selected),
                    "progress_definition_id": definition_id,
                    "value": value,
                    "milestone_id": None,
                    "state": None,
                    "observed_at": observed_at,
                    "observed_at_precision": "exact",
                    "supersedes_observation_id": None,
                },
            )
            assert observation.status_code == 200
            observation_review = observation.json()
            assert observation_review["record_kind"] == "observation"
            assert observation_review["record"]["definition_fingerprint"]
            assert "---" not in observation.text

            observation_applied = client.post(
                GOAL_PROGRESS_OBSERVATION_APPLY_PATH,
                headers=_headers(),
                json={
                    "review_token": observation_review["review_token"],
                    "accepted_plan_sha256": observation_review["plan_sha256"],
                    "confirmed": True,
                },
            )
            assert observation_applied.status_code == 200
            assert observation_applied.json()["status"] == "saved"

        current = client.post(
            GOAL_PROGRESS_PATH,
            headers=_headers(),
            json={"goal_source_uuid": str(selected), "as_of": AS_OF},
        )

    assert current.status_code == 200
    assert current.json()["status"] == "toward_target"
    assert current.json()["definition"]["metric_id"] == "weight"
    assert current.json()["observations"][0]["value"] == "76.5"


def test_review_plan_is_bound_to_the_authenticated_session_cookie(tmp_path: Path) -> None:
    vault = create_vault(tmp_path / "vault")
    _install_templates(vault)
    goal_id = "0198f4c5-6a00-7000-8000-000000000301"
    write_note(vault, "10 Projects/Goal.md", _goal_note(goal_id))
    env_file = tmp_path / "runtime.env"
    env_file.write_text("SECOND_BRAIN_VAULT_PATH=./vault\n", encoding="utf-8")
    service = _service(vault, env_file)
    before = snapshot_tree(vault)

    with TestClient(_app(service), base_url="http://127.0.0.1") as client:
        prepared = client.post(
            GOAL_PROGRESS_DEFINITION_PREPARE_PATH,
            headers={**_headers(), "Cookie": "second_brain_session=session-a"},
            json=_numeric_definition_payload(goal_id),
        )
        review = prepared.json()
        mismatched_session = client.post(
            GOAL_PROGRESS_DEFINITION_APPLY_PATH,
            headers={**_headers(), "Cookie": "second_brain_session=session-b"},
            json={
                "review_token": review["review_token"],
                "accepted_plan_sha256": review["plan_sha256"],
                "confirmed": True,
            },
        )
        after_mismatch = snapshot_tree(vault)
        applied = client.post(
            GOAL_PROGRESS_DEFINITION_APPLY_PATH,
            headers={**_headers(), "Cookie": "second_brain_session=session-a"},
            json={
                "review_token": review["review_token"],
                "accepted_plan_sha256": review["plan_sha256"],
                "confirmed": True,
            },
        )

    assert prepared.status_code == 200
    assert mismatched_session.status_code == 409
    assert mismatched_session.json()["error"]["code"] == "GOAL_PROGRESS_SAFE_WRITE_REVIEW_REQUIRED"
    assert after_mismatch == before
    assert applied.status_code == 200
    assert len(snapshot_tree(vault)) == len(before) + 1


def test_boundary_rejects_wrong_purpose_and_unknown_fields_without_service_call(
    tmp_path: Path,
) -> None:
    vault = create_vault(tmp_path / "vault")
    _install_templates(vault)
    goal_id = "0198f4c5-6a00-7000-8000-000000000301"
    write_note(vault, "10 Projects/Goal.md", _goal_note(goal_id))
    env_file = tmp_path / "runtime.env"
    env_file.write_text("SECOND_BRAIN_VAULT_PATH=./vault\n", encoding="utf-8")
    service = _service(vault, env_file)
    before = snapshot_tree(vault)

    with TestClient(_app(service), base_url="http://127.0.0.1") as client:
        wrong_header = client.post(
            GOAL_PROGRESS_PATH,
            headers=_headers("growth-engine-v1"),
            json={"goal_source_uuid": goal_id, "as_of": AS_OF},
        )
        unknown_field = client.post(
            GOAL_PROGRESS_PATH,
            headers=_headers(),
            json={"goal_source_uuid": goal_id, "as_of": AS_OF, "private": "leak"},
        )
        get_request = client.get(GOAL_PROGRESS_PATH, headers=_headers())

    assert wrong_header.status_code == 400
    assert unknown_field.status_code == 400
    assert get_request.status_code == 405
    assert snapshot_tree(vault) == before


def test_milestone_definition_and_observation_are_explicit_and_bounded(tmp_path: Path) -> None:
    vault = create_vault(tmp_path / "vault")
    _install_templates(vault)
    goal_id = "0198f4c5-6a00-7000-8000-000000000301"
    write_note(vault, "10 Projects/Goal.md", _goal_note(goal_id, "Запустить проект."))
    env_file = tmp_path / "runtime.env"
    env_file.write_text("SECOND_BRAIN_VAULT_PATH=./vault\n", encoding="utf-8")
    service = _service(vault, env_file)

    definition_payload = {
        "goal_source_uuid": goal_id,
        "progress_model": "milestone_set",
        "metric_id": None,
        "unit": None,
        "baseline": None,
        "target": None,
        "direction": None,
        "lower_bound": None,
        "upper_bound": None,
        "milestones": [
            {"id": "start", "label": "Начать", "ordinal": 1},
            {"id": "finish", "label": "Завершить", "ordinal": 2},
        ],
        "supersedes_definition_id": None,
    }
    with TestClient(_app(service), base_url="http://127.0.0.1") as client:
        review = client.post(
            GOAL_PROGRESS_DEFINITION_PREPARE_PATH,
            headers=_headers(),
            json=definition_payload,
        )
        assert review.status_code == 200
        definition = client.post(
            GOAL_PROGRESS_DEFINITION_APPLY_PATH,
            headers=_headers(),
            json={
                "review_token": review.json()["review_token"],
                "accepted_plan_sha256": review.json()["plan_sha256"],
                "confirmed": True,
            },
        )
        assert definition.status_code == 200
        observation = client.post(
            GOAL_PROGRESS_OBSERVATION_PREPARE_PATH,
            headers=_headers(),
            json={
                "goal_source_uuid": goal_id,
                "progress_definition_id": definition.json()["record_id"],
                "value": None,
                "milestone_id": "finish",
                "state": "completed",
                "observed_at": "unknown",
                "observed_at_precision": "unknown",
                "supersedes_observation_id": None,
            },
        )
        assert observation.status_code == 200
        assert observation.json()["record"]["observed_at"] == "unknown"
        applied = client.post(
            GOAL_PROGRESS_OBSERVATION_APPLY_PATH,
            headers=_headers(),
            json={
                "review_token": observation.json()["review_token"],
                "accepted_plan_sha256": observation.json()["plan_sha256"],
                "confirmed": True,
            },
        )
        assert applied.status_code == 200
        current = client.post(
            GOAL_PROGRESS_PATH,
            headers=_headers(),
            json={"goal_source_uuid": goal_id, "as_of": AS_OF},
        )

    assert current.status_code == 200
    assert current.json()["status"] == "insufficient_observations"
    assert current.json()["unknown_time_count"] == 1
    assert current.json()["current_observation_uuids"] == []
