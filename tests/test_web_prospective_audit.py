"""Focused Web/API and composition tests for Prospective Audit v1."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace
from typing import cast

import pytest
from fastapi.testclient import TestClient

from second_brain.application.prospective_audit import (
    DERIVATION_VERSION,
    POLICY_FINGERPRINT,
    POLICY_ID,
    ProspectiveAuditLinkInvalidError,
    ProspectiveAuditLinkReasonCode,
    ProspectiveCalibrationRequestV1,
    ProspectiveOptionMappingV1,
    fingerprint_decision_option,
)
from second_brain.application.simulate_me import (
    SimulateMeOption,
    SimulateMeRequest,
    SimulateMeResult,
    SimulateMeResultKind,
)
from second_brain.entrypoints.web.app import (
    MAX_RAW_PROSPECTIVE_AUDIT_BODY_BYTES,
    PROSPECTIVE_AUDIT_REQUEST_HEADER_VALUE,
    create_app,
)
from second_brain.entrypoints.web.prospective_audit import (
    PRODUCTION_PROSPECTIVE_AUDIT_OWNER_GROUP,
    PRODUCTION_PROSPECTIVE_AUDIT_STORE_ROOT,
    LazyProductionProspectiveAuditService,
    ProspectiveAuditCalibrationPayload,
    ProspectiveAuditExecutePayload,
    build_production_prospective_audit_service,
    derive_prospective_audit_store_root,
)
from second_brain.entrypoints.web.simulate_me import SimulateMeOptionPayload

BASE_URL = "http://127.0.0.1"


def _result() -> SimulateMeResult:
    return SimulateMeResult(
        kind=SimulateMeResultKind.PREDICTION,
        selected_option=SimulateMeOption("a", "A"),
        evidence_refs=(),
        contextual_evidence_refs=(),
        temporal_caveats=(),
        abstention_code=None,
        derivation_version=DERIVATION_VERSION,
        policy_id=POLICY_ID,
        policy_fingerprint=POLICY_FINGERPRINT,
    )


@dataclass(slots=True)
class RecordingProspectiveAuditService:
    error: BaseException | None = None
    execute_calls: list[tuple[SimulateMeRequest, str]] = field(default_factory=list)
    pending_calls: int = 0
    review_calls: list[tuple[str, str]] = field(default_factory=list)
    confirm_calls: list[tuple[str, str, str, tuple[ProspectiveOptionMappingV1, ...], bool]] = field(
        default_factory=list
    )
    calibration_calls: list[ProspectiveCalibrationRequestV1] = field(default_factory=list)

    def execute(self, request: SimulateMeRequest, operation_id: str) -> dict[str, object]:
        self.execute_calls.append((request, operation_id))
        if self.error is not None:
            raise self.error
        return {
            "event": {
                "event_id": "0198f4c5-6a00-7000-8000-000000000101",
                "created_at": "2026-09-13T12:00:00Z",
                "kind": "prediction",
                "options": [{"id": "a", "ordinal": 0, "label": "A"}],
                "predicted_option_id": "a",
                "predicted_option_label": "A",
                "abstention_code": None,
                "derivation_version": DERIVATION_VERSION,
                "policy_id": POLICY_ID,
            }
        }

    def pending(self) -> dict[str, object]:
        self.pending_calls += 1
        if self.error is not None:
            raise self.error
        return {
            "events": [],
            "decision_journals": [],
            "limits": {"max_events": 32, "max_decision_journals": 32},
        }

    def review(self, audit_event_id: str, decision_id: str) -> dict[str, object]:
        self.review_calls.append((audit_event_id, decision_id))
        if self.error is not None:
            raise self.error
        return {
            "event": {},
            "decision": {},
            "mapping_basis": "owner-explicit-v1",
            "requires_confirmation": True,
        }

    def confirm(
        self,
        audit_event_id: str,
        decision_id: str,
        operation_id: str,
        mapping: tuple[ProspectiveOptionMappingV1, ...],
        confirmed: bool,
    ) -> dict[str, object]:
        self.confirm_calls.append((audit_event_id, decision_id, operation_id, mapping, confirmed))
        if self.error is not None:
            raise self.error
        return {
            "status": "linked",
            "audit_event_id": audit_event_id,
            "decision_id": decision_id,
            "link_state": "LINKED_VALID",
            "linked_at": "2026-09-13T12:01:00Z",
        }

    def calibration(self) -> bytes:
        self.calibration_calls.append(ProspectiveCalibrationRequestV1())
        if self.error is not None:
            raise self.error
        return b'{"contract_version":"prospective-audit-calibration-v1"}'


def _headers(*, origin: str = BASE_URL) -> dict[str, str]:
    return {
        "Origin": origin,
        "X-Second-Brain-Request": PROSPECTIVE_AUDIT_REQUEST_HEADER_VALUE,
    }


def _execute_payload() -> dict[str, object]:
    return {
        "operation_id": "web-operation-1",
        "query": "private query must not be stored",
        "options": [{"id": "a", "label": "A"}],
    }


def test_payloads_are_strict_and_have_no_unapproved_fields() -> None:
    payload = ProspectiveAuditExecutePayload.model_validate(_execute_payload(), strict=True)
    assert set(ProspectiveAuditExecutePayload.model_fields) == {"operation_id", "query", "options"}
    assert payload.options == [SimulateMeOptionPayload(id="a", label="A")]
    assert set(ProspectiveAuditCalibrationPayload.model_fields) == set()

    with pytest.raises(ValueError):
        ProspectiveAuditExecutePayload.model_validate(
            {**_execute_payload(), "options": [{"id": "a", "label": "A", "score": 1}]},
            strict=True,
        )


def test_execute_api_projects_bounded_event_and_keeps_raw_query_out() -> None:
    service = RecordingProspectiveAuditService()
    with TestClient(create_app(prospective_audit_service=service), base_url=BASE_URL) as client:
        response = client.post(
            "/api/prospective-audit/execute",
            headers=_headers(),
            json=_execute_payload(),
        )

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert response.json()["event"]["kind"] == "prediction"
    assert "private query" not in response.text
    assert service.execute_calls == [
        (
            SimulateMeRequest("private query must not be stored", (SimulateMeOption("a", "A"),)),
            "web-operation-1",
        )
    ]


@pytest.mark.parametrize(
    "payload",
    [
        {**_execute_payload(), "unexpected": True},
        {**_execute_payload(), "operation_id": 7},
        {**_execute_payload(), "options": []},
    ],
)
def test_execute_invalid_payload_is_rejected_before_service(
    payload: dict[str, object],
) -> None:
    service = RecordingProspectiveAuditService()
    with TestClient(create_app(prospective_audit_service=service), base_url=BASE_URL) as client:
        response = client.post(
            "/api/prospective-audit/execute",
            headers=_headers(),
            json=payload,
        )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "PROSPECTIVE_AUDIT_INVALID_REQUEST"
    assert service.execute_calls == []


def test_stage9_boundary_rejects_origin_purpose_content_type_and_raw_oversize() -> None:
    service = RecordingProspectiveAuditService()
    with TestClient(create_app(prospective_audit_service=service), base_url=BASE_URL) as client:
        wrong_origin = client.post(
            "/api/prospective-audit/execute",
            headers=_headers(origin="https://evil.example"),
            json=_execute_payload(),
        )
        wrong_purpose = client.post(
            "/api/prospective-audit/pending",
            headers={**_headers(), "X-Second-Brain-Request": "wrong-v1"},
            json={},
        )
        wrong_content_type = client.post(
            "/api/prospective-audit/pending",
            content=b"{}",
            headers={**_headers(), "Content-Type": "text/plain"},
        )
        too_large = client.post(
            "/api/prospective-audit/pending",
            content=b"x" * (MAX_RAW_PROSPECTIVE_AUDIT_BODY_BYTES + 1),
            headers={**_headers(), "Content-Type": "application/json"},
        )

    assert [
        response.status_code for response in (wrong_origin, wrong_purpose, wrong_content_type)
    ] == [
        400,
        400,
        400,
    ]
    assert too_large.status_code == 413
    assert too_large.json()["error"]["code"] == "PROSPECTIVE_AUDIT_CONTENT_TOO_LARGE"
    assert service.execute_calls == []
    assert service.pending_calls == 0


def test_read_routes_and_confirm_forward_only_bounded_typed_values() -> None:
    service = RecordingProspectiveAuditService()
    event_id = "0198f4c5-6a00-7000-8000-000000000101"
    decision_id = "0198f4c5-6a00-7000-8000-000000000102"
    fingerprint = fingerprint_decision_option("Остаться")
    with TestClient(create_app(prospective_audit_service=service), base_url=BASE_URL) as client:
        pending = client.post("/api/prospective-audit/pending", headers=_headers(), json={})
        review = client.post(
            "/api/prospective-audit/link/review",
            headers=_headers(),
            json={"audit_event_id": event_id, "decision_id": decision_id},
        )
        confirm = client.post(
            "/api/prospective-audit/link/confirm",
            headers=_headers(),
            json={
                "audit_event_id": event_id,
                "decision_id": decision_id,
                "operation_id": "confirm-1",
                "mapping": [
                    {
                        "audit_option_id": "a",
                        "decision_option_index": 0,
                        "decision_option_fingerprint": fingerprint,
                    }
                ],
                "confirmed": True,
            },
        )
        calibration = client.post(
            "/api/prospective-audit/calibration",
            headers=_headers(),
            json={},
        )

    assert pending.status_code == 200
    assert review.status_code == 200
    assert confirm.status_code == 200
    assert calibration.status_code == 200
    assert service.pending_calls == 1
    assert service.review_calls == [(event_id, decision_id)]
    assert service.confirm_calls[0][0:3] == (event_id, decision_id, "confirm-1")
    assert service.confirm_calls[0][3][0].decision_option_fingerprint == fingerprint
    assert service.calibration_calls == [ProspectiveCalibrationRequestV1()]


def test_stage9_errors_are_fixed_and_private_details_do_not_escape() -> None:
    service = RecordingProspectiveAuditService(error=RuntimeError("D:\\private\\secret.md"))
    with TestClient(create_app(prospective_audit_service=service), base_url=BASE_URL) as client:
        response = client.post(
            "/api/prospective-audit/pending",
            headers=_headers(),
            json={},
        )

    assert response.status_code == 503
    assert response.json() == {
        "error": {
            "code": "PROSPECTIVE_AUDIT_STORE_UNAVAILABLE",
            "message": "Операционный журнал аудита сейчас недоступен.",
        }
    }
    assert "private" not in response.text
    assert "secret.md" not in response.text


def test_link_error_exposes_only_fixed_code_message_and_reason() -> None:
    service = RecordingProspectiveAuditService(
        error=ProspectiveAuditLinkInvalidError(
            ProspectiveAuditLinkReasonCode.OPTION_MAPPING_INVALID
        )
    )
    with TestClient(create_app(prospective_audit_service=service), base_url=BASE_URL) as client:
        response = client.post("/api/prospective-audit/pending", headers=_headers(), json={})

    assert response.status_code == 409
    assert response.json()["error"] == {
        "code": "PROSPECTIVE_AUDIT_LINK_INVALID",
        "message": "Явная связь с журналом решений не прошла проверку.",
        "reason": "option_mapping_invalid",
    }


def test_store_root_is_derived_from_explicit_env_file_only(tmp_path: Path) -> None:
    env_file = tmp_path / "web.env"
    env_file.write_text("SECOND_BRAIN_VAULT_PATH=/tmp/vault\n", encoding="utf-8")

    assert derive_prospective_audit_store_root(env_file) == tmp_path / "prospective-audit"
    assert derive_prospective_audit_store_root(None) is None
    assert build_production_prospective_audit_service(env_file=env_file).store_root == (
        tmp_path / "prospective-audit"
    )
    assert tmp_path / "prospective-audit" != PRODUCTION_PROSPECTIVE_AUDIT_STORE_ROOT
    assert PRODUCTION_PROSPECTIVE_AUDIT_OWNER_GROUP == ("second-brain", "second-brain")


def test_app_creation_does_not_initialize_the_derived_store(tmp_path: Path) -> None:
    env_file = tmp_path / "web.env"
    env_file.write_text("SECOND_BRAIN_VAULT_PATH=./vault\n", encoding="utf-8")
    vault = tmp_path / "vault"
    vault.mkdir()
    store_root = tmp_path / "prospective-audit"

    create_app(env_file=env_file, vault_path_override=str(vault))

    assert not store_root.exists()


def test_lazy_production_service_durably_records_without_storing_raw_query(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import second_brain.entrypoints.web.prospective_audit as module

    env_file = tmp_path / "web.env"
    env_file.write_text("SECOND_BRAIN_VAULT_PATH=/tmp/vault\n", encoding="utf-8")
    vault = tmp_path / "vault"
    vault.mkdir()
    monkeypatch.setattr(module, "load_config", lambda **_: SimpleNamespace(vault_path=vault))

    @dataclass(slots=True)
    class FakeSimulateMe:
        calls: int = 0

        def build(self, request: SimulateMeRequest) -> SimulateMeResult:
            self.calls += 1
            assert request.query == "private query must not be stored"
            return _result()

    fake = FakeSimulateMe()
    monkeypatch.setattr(module, "build_production_simulate_me_service", lambda **_: fake)
    service = build_production_prospective_audit_service(env_file=env_file)

    result = service.execute(
        SimulateMeRequest("private query must not be stored", (SimulateMeOption("a", "A"),)),
        "web-operation-1",
    )

    assert isinstance(service, LazyProductionProspectiveAuditService)
    assert fake.calls == 1
    event = cast(dict[str, object], result["event"])
    assert event["kind"] == "prediction"
    assert service.store_root is not None
    assert (
        b"private query must not be stored"
        not in (service.store_root / "events.jsonl").read_bytes()
    )
