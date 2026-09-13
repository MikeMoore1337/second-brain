"""Focused Stage 10D Web/API and integration tests with synthetic temp fixtures."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid7

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from second_brain.adapters.vault import FileSystemVaultReader
from second_brain.application.behavioral_self_model import (
    BehavioralSelfModelRequest,
    BehavioralSelfModelResultV1,
    BuildBehavioralSelfModel,
)
from second_brain.application.stated_observed_mapping import (
    StatedObservedMappingService,
    StatedObservedMappingStore,
)
from second_brain.entrypoints.web.app import (
    BEHAVIORAL_SELF_MODEL_PATH,
    COGNITIVE_TWIN_REQUEST_HEADER_VALUE,
    MAX_RAW_COGNITIVE_TWIN_BODY_BYTES,
    STATED_OBSERVED_COMPOSITION_PATH,
    STATED_OBSERVED_MAPPING_CONFIRM_PATH,
    STATED_OBSERVED_MAPPING_REVIEW_PATH,
    STATED_OBSERVED_MAPPING_STATUS_PATH,
    create_app,
)
from tests.test_stated_observed_mapping import NOW, PREFERENCE_ID, _seed_vault, _selector

BASE_URL = "http://127.0.0.1"


@dataclass(slots=True)
class FixtureBehavioralService:
    """Use the real Stage 10B builder over a synthetic temporary vault."""

    vault: Path
    calls: int = 0

    def build(self, request: BehavioralSelfModelRequest) -> BehavioralSelfModelResultV1:
        self.calls += 1
        return BuildBehavioralSelfModel(
            FileSystemVaultReader(self.vault),
            clock=lambda: NOW,
        ).execute(request)


def _headers(*, origin: str = BASE_URL) -> dict[str, str]:
    return {
        "Origin": origin,
        "X-Second-Brain-Request": COGNITIVE_TWIN_REQUEST_HEADER_VALUE,
    }


def _service(vault: Path, store_root: Path) -> StatedObservedMappingService:
    return StatedObservedMappingService(
        FileSystemVaultReader(vault),
        StatedObservedMappingStore(store_root, clock=lambda: NOW),
        clock=lambda: NOW,
    )


def _app(
    vault: Path,
    store_root: Path,
) -> tuple[FastAPI, StatedObservedMappingService, FixtureBehavioralService]:
    mapping = _service(vault, store_root)
    behavioral = FixtureBehavioralService(vault)
    return (
        create_app(
            behavioral_self_model_service=behavioral,
            stated_observed_mapping_service=mapping,
        ),
        mapping,
        behavioral,
    )


def _selector_payload(vault: Path) -> dict[str, object]:
    selector = _selector(vault)
    return {
        "source_note_uuid": str(selector.source_note_uuid),
        "behavioral_cohort_fingerprint": selector.behavioral_cohort_fingerprint,
        "behavioral_option_index": selector.behavioral_option_index,
        "behavioral_option_fingerprint": selector.behavioral_option_fingerprint,
    }


def test_behavioral_api_is_exact_bounded_and_raw_label_free(tmp_path: Path) -> None:
    vault = _seed_vault(tmp_path / "vault")
    application, _mapping, behavioral = _app(vault, tmp_path / "mapping-store")

    with TestClient(application, base_url=BASE_URL) as client:
        response = client.post(BEHAVIORAL_SELF_MODEL_PATH, headers=_headers(), json={})

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert response.json()["patterns"][0]["pattern_type"] == "repeated_exact_choice"
    assert response.json()["patterns"][0]["choice_support"][0]["support_ratio"] == {
        "numerator": 3,
        "denominator": 3,
    }
    assert "Alpha" not in response.text
    assert "Choose a direction" not in response.text
    assert "Люблю короткие циклы" not in response.text
    assert behavioral.calls == 1


def test_mapping_review_confirm_retry_and_composition_are_integrated(tmp_path: Path) -> None:
    vault = _seed_vault(tmp_path / "vault")
    store_root = tmp_path / "mapping-store"
    application, mapping, _behavioral = _app(vault, store_root)
    selector = _selector_payload(vault)
    operation_id = str(uuid7())

    with TestClient(application, base_url=BASE_URL) as client:
        empty = client.post(
            STATED_OBSERVED_MAPPING_STATUS_PATH,
            headers=_headers(),
            json={},
        )
        assert not store_root.exists()
        review = client.post(
            STATED_OBSERVED_MAPPING_REVIEW_PATH,
            headers=_headers(),
            json=selector,
        )
        confirm = client.post(
            STATED_OBSERVED_MAPPING_CONFIRM_PATH,
            headers=_headers(),
            json={**selector, "operation_id": operation_id, "confirmed": True},
        )
        retry = client.post(
            STATED_OBSERVED_MAPPING_CONFIRM_PATH,
            headers=_headers(),
            json={**selector, "operation_id": operation_id, "confirmed": True},
        )
        status = client.post(
            STATED_OBSERVED_MAPPING_STATUS_PATH,
            headers=_headers(),
            json={},
        )
        composition = client.post(
            STATED_OBSERVED_COMPOSITION_PATH,
            headers=_headers(),
            json={"source_note_uuid": str(PREFERENCE_ID)},
        )

    assert empty.status_code == 200
    assert empty.json() == {
        "mapping_policy_id": "stated-observed-explicit-mapping-v1",
        "mappings": [],
        "active_mapping_count": 0,
    }
    assert review.status_code == 200
    assert review.json()["claim_text"] == "Люблю короткие циклы.\n"
    assert review.json()["ordered_options"][0]["label"] == "Alpha"
    assert "Labels shown here are only for human review" not in review.text
    assert confirm.status_code == 200
    assert confirm.json()["status"] == "accepted"
    assert "Люблю короткие циклы" not in confirm.text
    assert "Alpha" not in confirm.text
    assert retry.status_code == 200
    assert retry.json()["mapping"]["mapping_id"] == confirm.json()["mapping"]["mapping_id"]
    assert status.status_code == 200
    assert status.json()["active_mapping_count"] == 1
    assert len(status.json()["mappings"]) == 1
    assert composition.status_code == 200
    assert composition.json()["state"] == "aligned"
    assert composition.json()["reason_code"] is None
    assert len(mapping.store.read_active()) == 1
    assert store_root.joinpath("mappings.jsonl").exists()


def test_mapping_confirmation_requires_explicit_boolean_and_uuid7(tmp_path: Path) -> None:
    vault = _seed_vault(tmp_path / "vault")
    application, mapping, _behavioral = _app(vault, tmp_path / "mapping-store")
    selector = _selector_payload(vault)

    with TestClient(application, base_url=BASE_URL) as client:
        wrong_confirmation = client.post(
            STATED_OBSERVED_MAPPING_CONFIRM_PATH,
            headers=_headers(),
            json={**selector, "operation_id": str(uuid7()), "confirmed": False},
        )
        wrong_operation = client.post(
            STATED_OBSERVED_MAPPING_CONFIRM_PATH,
            headers=_headers(),
            json={**selector, "operation_id": "not-a-uuid7", "confirmed": True},
        )
        extra = client.post(
            STATED_OBSERVED_MAPPING_REVIEW_PATH,
            headers=_headers(),
            json={**selector, "claim_text": "client authority"},
        )

    assert wrong_confirmation.status_code == 400
    assert wrong_operation.status_code == 400
    assert extra.status_code == 400
    assert mapping.store.read_active() == ()


@pytest.mark.parametrize(
    "path",
    [
        BEHAVIORAL_SELF_MODEL_PATH,
        STATED_OBSERVED_MAPPING_STATUS_PATH,
        STATED_OBSERVED_MAPPING_REVIEW_PATH,
        STATED_OBSERVED_MAPPING_CONFIRM_PATH,
        STATED_OBSERVED_COMPOSITION_PATH,
    ],
)
def test_stage10_boundary_rejects_foreign_origin_and_raw_oversize(
    tmp_path: Path,
    path: str,
) -> None:
    vault = _seed_vault(tmp_path / "vault")
    application, mapping, behavioral = _app(vault, tmp_path / "mapping-store")
    with TestClient(application, base_url=BASE_URL) as client:
        foreign = client.post(
            path,
            headers={**_headers(origin="https://evil.example")},
            json={},
        )
        too_large = client.post(
            path,
            headers={**_headers(), "Content-Type": "application/json"},
            content=b"x" * (MAX_RAW_COGNITIVE_TWIN_BODY_BYTES + 1),
        )

    assert foreign.status_code == 400
    assert foreign.json()["error"]["code"] == "COGNITIVE_TWIN_INVALID_REQUEST"
    assert too_large.status_code == 413
    assert too_large.json()["error"]["code"] == "COGNITIVE_TWIN_CONTENT_TOO_LARGE"
    assert behavioral.calls == 0
    assert mapping.store.read_active() == ()


def test_stage10_responses_have_no_openapi_surface_or_cors(tmp_path: Path) -> None:
    vault = _seed_vault(tmp_path / "vault")
    application, _mapping, _behavioral = _app(vault, tmp_path / "mapping-store")
    with TestClient(application, base_url=BASE_URL) as client:
        openapi = client.get("/openapi.json")
        method = client.get(BEHAVIORAL_SELF_MODEL_PATH)

    assert openapi.status_code == 404
    assert method.status_code == 405
    assert method.headers["cache-control"] == "no-store"
    assert "access-control-allow-origin" not in method.headers


def test_review_projection_is_transient_and_not_in_mapping_store(tmp_path: Path) -> None:
    vault = _seed_vault(tmp_path / "vault")
    store_root = tmp_path / "mapping-store"
    application, _mapping, _behavioral = _app(vault, store_root)
    selector = _selector_payload(vault)
    with TestClient(application, base_url=BASE_URL) as client:
        response = client.post(
            STATED_OBSERVED_MAPPING_REVIEW_PATH,
            headers=_headers(),
            json=selector,
        )

    assert response.status_code == 200
    assert len(response.content) <= 32768
    assert not store_root.exists()
    decoded = json.loads(response.content)
    assert decoded["situation"] == "Choose a direction"
    assert decoded["criteria"] == ["Speed"]
