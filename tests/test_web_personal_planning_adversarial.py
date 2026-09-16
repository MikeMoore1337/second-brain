"""Stage 17.5 adversarial privacy and failure-boundary tests."""

from __future__ import annotations

import json
from pathlib import Path
from uuid import uuid7

from fastapi.testclient import TestClient
from httpx import Response

from second_brain.application.personal_planning import (
    PlanningContextPackV1,
    PlanningProposalV1,
)
from second_brain.application.personal_planning_store import PersonalPlanningOperationalStore
from second_brain.entrypoints.web.app import (
    PERSONAL_PLANNING_ACCEPT_PATH,
    PERSONAL_PLANNING_CONTEXT_PATH,
    PERSONAL_PLANNING_EDIT_PATH,
    PERSONAL_PLANNING_GENERATE_PATH,
    PERSONAL_PLANNING_STATE_PATH,
)
from tests.test_web_personal_planning import (
    BASE_URL,
    _app,
    _context_body,
    _headers,
    _pack,
    _proposal,
    _Service,
)


def _response_headers(response: Response) -> None:
    headers = response.headers
    assert headers["cache-control"] == "no-store"
    assert headers["content-security-policy"].endswith("style-src 'self'")
    assert headers["x-content-type-options"] == "nosniff"
    assert headers["x-frame-options"] == "DENY"
    assert headers["referrer-policy"] == "no-referrer"


def _service(tmp_path: Path) -> tuple[_Service, PlanningContextPackV1]:
    pack = _pack()
    store = PersonalPlanningOperationalStore(tmp_path / "personal-planning")
    return _Service(pack, _proposal(pack), store), pack


def test_duplicate_nonfinite_and_unknown_json_are_generic_and_non_echoing(tmp_path: Path) -> None:
    service, pack = _service(tmp_path)

    with TestClient(_app(service), base_url=BASE_URL) as client:
        duplicate = client.post(
            PERSONAL_PLANNING_STATE_PATH,
            headers=_headers(),
            content='{"extra":"first","extra":"secret-provider-token"}',
        )
        nonfinite = client.post(
            PERSONAL_PLANNING_STATE_PATH,
            headers=_headers(),
            content='{"extra":NaN}',
        )
        unknown = client.post(
            PERSONAL_PLANNING_CONTEXT_PATH,
            headers=_headers(),
            json={**_context_body(pack), "secret_provider_token": "must-not-echo"},
        )

    for response in (duplicate, nonfinite, unknown):
        assert response.status_code == 400
        assert response.json() == {
            "error": {
                "code": "PERSONAL_PLANNING_INVALID_REQUEST",
                "message": "Запрос личного планирования некорректен.",
            }
        }
        assert "secret-provider-token" not in response.text
        assert "must-not-echo" not in response.text
        _response_headers(response)
    assert service.advisor_calls == 0


class _SourceChangesOnRevalidation(_Service):
    def revalidate_context(self, pack: PlanningContextPackV1) -> PlanningContextPackV1:
        del pack
        return _pack()


def test_generate_fails_closed_when_authoritative_source_changes(tmp_path: Path) -> None:
    stable_pack = _pack()
    store = PersonalPlanningOperationalStore(tmp_path / "personal-planning")
    service = _SourceChangesOnRevalidation(stable_pack, _proposal(stable_pack), store)

    with TestClient(_app(service), base_url=BASE_URL) as client:
        context = client.post(
            PERSONAL_PLANNING_CONTEXT_PATH,
            headers=_headers(),
            json=_context_body(stable_pack),
        ).json()
        response = client.post(
            PERSONAL_PLANNING_GENERATE_PATH,
            headers=_headers(),
            json={
                "context_pack": context["context_pack"],
                "provider_preview": context["provider_preview"]["canonical_json"],
            },
        )

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "PERSONAL_PLANNING_SOURCE_CHANGED"
    assert service.advisor_calls == 0
    _response_headers(response)


class _SecretFailingService(_Service):
    def generate(self, pack: PlanningContextPackV1) -> PlanningProposalV1:
        del pack
        raise RuntimeError("secret-provider-token")


def test_unexpected_provider_failure_is_safe_and_bounded(tmp_path: Path) -> None:
    pack = _pack()
    store = PersonalPlanningOperationalStore(tmp_path / "personal-planning")
    service = _SecretFailingService(pack, _proposal(pack), store)

    with TestClient(_app(service), base_url=BASE_URL) as client:
        context = client.post(
            PERSONAL_PLANNING_CONTEXT_PATH,
            headers=_headers(),
            json=_context_body(pack),
        ).json()
        response = client.post(
            PERSONAL_PLANNING_GENERATE_PATH,
            headers=_headers(),
            json={
                "context_pack": context["context_pack"],
                "provider_preview": context["provider_preview"]["canonical_json"],
            },
        )

    assert response.status_code == 503
    assert response.json() == {
        "error": {
            "code": "PERSONAL_PLANNING_SOURCE_UNAVAILABLE",
            "message": "Личное планирование сейчас недоступно.",
        }
    }
    assert "secret-provider-token" not in response.text
    _response_headers(response)


def test_accept_replay_with_changed_intent_is_rejected_without_second_event(tmp_path: Path) -> None:
    pack = _pack()
    store = PersonalPlanningOperationalStore(tmp_path / "personal-planning")
    service = _Service(pack, _proposal(pack), store)

    with TestClient(_app(service), base_url=BASE_URL) as client:
        context = client.post(
            PERSONAL_PLANNING_CONTEXT_PATH,
            headers=_headers(),
            json=_context_body(pack),
        ).json()
        generated = client.post(
            PERSONAL_PLANNING_GENERATE_PATH,
            headers=_headers(),
            json={
                "context_pack": context["context_pack"],
                "provider_preview": context["provider_preview"]["canonical_json"],
            },
        ).json()
        operation_id = str(uuid7())
        accepted_payload = {
            "context_pack": context["context_pack"],
            "proposal": generated["proposal"],
            "selected_item_ids": ["next-1"],
            "item_order": ["next-1"],
            "operation_id": operation_id,
            "accepted_at": "2026-09-16T06:00:00Z",
            "expected_current_plan_fingerprint": None,
        }
        first = client.post(
            PERSONAL_PLANNING_ACCEPT_PATH,
            headers=_headers(),
            json=accepted_payload,
        )
        replay = client.post(
            PERSONAL_PLANNING_ACCEPT_PATH,
            headers=_headers(),
            json={**accepted_payload, "accepted_at": "2026-09-16T06:01:00Z"},
        )

    assert first.status_code == 200
    assert replay.status_code == 409
    assert replay.json()["error"]["code"] == "PERSONAL_PLANNING_IDEMPOTENCY_CONFLICT"
    assert len(store.read_events()) == 1
    _response_headers(replay)


def test_edit_rejects_unknown_selection_and_preserves_current_plan(tmp_path: Path) -> None:
    pack = _pack()
    store = PersonalPlanningOperationalStore(tmp_path / "personal-planning")
    service = _Service(pack, _proposal(pack), store)

    with TestClient(_app(service), base_url=BASE_URL) as client:
        context = client.post(
            PERSONAL_PLANNING_CONTEXT_PATH,
            headers=_headers(),
            json=_context_body(pack),
        ).json()
        generated = client.post(
            PERSONAL_PLANNING_GENERATE_PATH,
            headers=_headers(),
            json={
                "context_pack": context["context_pack"],
                "provider_preview": context["provider_preview"]["canonical_json"],
            },
        ).json()
        accepted = client.post(
            PERSONAL_PLANNING_ACCEPT_PATH,
            headers=_headers(),
            json={
                "context_pack": context["context_pack"],
                "proposal": generated["proposal"],
                "selected_item_ids": ["next-1"],
                "item_order": ["next-1"],
                "operation_id": str(uuid7()),
                "expected_current_plan_fingerprint": None,
            },
        ).json()
        invalid_edit = client.post(
            PERSONAL_PLANNING_EDIT_PATH,
            headers=_headers(),
            json={
                "items": accepted["plan"]["items"],
                "selected_item_ids": ["not-an-item"],
                "item_order": ["not-an-item"],
                "operation_id": str(uuid7()),
                "expected_current_plan_fingerprint": accepted["plan"]["plan_fingerprint"],
            },
        )

    assert invalid_edit.status_code == 400
    assert invalid_edit.json()["error"]["code"] == "PERSONAL_PLANNING_INVALID_REQUEST"
    current = store.current_plan()
    assert current is not None
    assert current.revision == 1
    _response_headers(invalid_edit)


def test_context_json_preview_is_exact_utf8_and_does_not_depend_on_provider(tmp_path: Path) -> None:
    service, pack = _service(tmp_path)

    with TestClient(_app(service), base_url=BASE_URL) as client:
        response = client.post(
            PERSONAL_PLANNING_CONTEXT_PATH,
            headers=_headers(),
            json=_context_body(pack),
        )

    body = response.json()
    canonical = body["provider_preview"]["canonical_json"]
    assert canonical.encode("utf-8").decode("utf-8") == canonical
    assert body["provider_preview"]["canonical_bytes_sha256"]
    assert service.advisor_calls == 0
    assert json.dumps(body, ensure_ascii=False, separators=(",", ":"))
