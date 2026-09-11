"""Security and projection regression tests for Retrospective Calibration Web/API."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from second_brain.application.retrospective_calibration import (
    DERIVATION_VERSION,
    POLICY_FINGERPRINT,
    POLICY_ID,
    RECONSTRUCTION_MODE,
    RetrospectiveCalibrationCancelledError,
    RetrospectiveCalibrationCountV1,
    RetrospectiveCalibrationError,
    RetrospectiveCalibrationErrorCodeV1,
    RetrospectiveCalibrationExcludedCodeV1,
    RetrospectiveCalibrationMetricsV1,
    RetrospectiveCalibrationReplayInvalidCodeV1,
    RetrospectiveCalibrationReplayUnavailableCodeV1,
    RetrospectiveCalibrationRequestV1,
    RetrospectiveCalibrationResultTooLargeError,
    RetrospectiveCalibrationResultV1,
    RetrospectiveCalibrationSourceUnavailableError,
    RetrospectiveCalibrationTemporalCaveatCodeV1,
    RetrospectiveCalibrationTooLargeError,
    serialize_retrospective_calibration_result,
)
from second_brain.entrypoints.web.app import (
    MAX_RAW_RETROSPECTIVE_CALIBRATION_BODY_BYTES,
    RETROSPECTIVE_CALIBRATION_REQUEST_HEADER_VALUE,
    create_app,
)
from second_brain.entrypoints.web.retrospective_calibration import (
    LazyVaultRetrospectiveCalibrationService,
    RetrospectiveCalibrationRequestPayload,
    build_production_retrospective_calibration_service,
)

BASE_URL = "http://127.0.0.1"


def _counts(items: tuple[StrEnum, ...]) -> tuple[RetrospectiveCalibrationCountV1, ...]:
    return tuple(RetrospectiveCalibrationCountV1(item.value, 0) for item in items)


def _zero_result() -> RetrospectiveCalibrationResultV1:
    return RetrospectiveCalibrationResultV1(
        derivation_version=DERIVATION_VERSION,
        policy_id=POLICY_ID,
        policy_fingerprint=POLICY_FINGERPRINT,
        reconstruction_mode=RECONSTRUCTION_MODE,
        metrics=RetrospectiveCalibrationMetricsV1(
            decision_notes_seen=0,
            eligible_decisions=0,
            predicted_decisions=0,
            abstentions=0,
            exact_option_match_count=0,
            mismatch_count=0,
            unavailable_count=0,
            invalid_count=0,
            coverage=None,
            accuracy_non_abstained=None,
        ),
        excluded_decisions=_counts(tuple(RetrospectiveCalibrationExcludedCodeV1)),
        replay_unavailable=_counts(tuple(RetrospectiveCalibrationReplayUnavailableCodeV1)),
        replay_invalid=_counts(tuple(RetrospectiveCalibrationReplayInvalidCodeV1)),
        temporal_caveats=_counts(tuple(RetrospectiveCalibrationTemporalCaveatCodeV1)),
    )


@dataclass(slots=True)
class RecordingCalibrationService:
    result: RetrospectiveCalibrationResultV1 = field(default_factory=_zero_result)
    calls: list[RetrospectiveCalibrationRequestV1] = field(default_factory=list)

    def execute(
        self,
        request: RetrospectiveCalibrationRequestV1,
    ) -> RetrospectiveCalibrationResultV1:
        self.calls.append(request)
        return self.result


@dataclass(slots=True)
class RaisingCalibrationService:
    error: BaseException
    calls: int = 0

    def execute(
        self, request: RetrospectiveCalibrationRequestV1
    ) -> RetrospectiveCalibrationResultV1:
        del request
        self.calls += 1
        raise self.error


def _headers() -> dict[str, str]:
    return {
        "Origin": BASE_URL,
        "X-Second-Brain-Request": RETROSPECTIVE_CALIBRATION_REQUEST_HEADER_VALUE,
    }


def test_calibration_api_projects_exact_core_serialization_and_zero_case() -> None:
    service = RecordingCalibrationService()
    app = create_app(retrospective_calibration_service=service)
    expected = serialize_retrospective_calibration_result(service.result)

    with TestClient(app, base_url=BASE_URL) as client:
        response = client.post(
            "/api/retrospective-calibration",
            json={},
            headers=_headers(),
        )

    assert response.status_code == 200
    assert response.content == expected
    assert response.headers["cache-control"] == "no-store"
    assert "access-control-allow-origin" not in response.headers
    assert service.calls == [RetrospectiveCalibrationRequestV1()]
    payload = response.json()
    assert payload["metrics"]["eligible_decisions"] == 0
    assert payload["metrics"]["coverage"] is None
    assert payload["metrics"]["accuracy_non_abstained"] is None
    assert set(payload) == {
        "derivation_version",
        "policy_id",
        "policy_fingerprint",
        "reconstruction_mode",
        "metrics",
        "excluded_decisions",
        "replay_unavailable",
        "replay_invalid",
        "temporal_caveats",
    }
    assert not any(
        forbidden in response.content.lower()
        for forbidden in (b"situation", b"chosen_option", b"front_matter", b"absolute_path")
    )


@pytest.mark.parametrize("payload", [{"unexpected": True}, [], None])
def test_calibration_api_requires_strict_empty_object(payload: object) -> None:
    service = RecordingCalibrationService()
    app = create_app(retrospective_calibration_service=service)

    with TestClient(app, base_url=BASE_URL) as client:
        response = client.post(
            "/api/retrospective-calibration",
            json=payload,
            headers=_headers(),
        )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "RETROSPECTIVE_CALIBRATION_INVALID_REQUEST"
    assert service.calls == []


def test_calibration_boundary_rejects_wrong_origin_purpose_content_type_and_raw_oversize() -> None:
    service = RecordingCalibrationService()
    app = create_app(retrospective_calibration_service=service)

    with TestClient(app, base_url=BASE_URL) as client:
        wrong_origin = client.post(
            "/api/retrospective-calibration",
            json={},
            headers={**_headers(), "Origin": "https://evil.example"},
        )
        wrong_purpose = client.post(
            "/api/retrospective-calibration",
            json={},
            headers={**_headers(), "X-Second-Brain-Request": "wrong-purpose-v1"},
        )
        wrong_content_type = client.post(
            "/api/retrospective-calibration",
            content=b"{}",
            headers={**_headers(), "Content-Type": "text/plain"},
        )
        too_large = client.post(
            "/api/retrospective-calibration",
            content=b"x" * (MAX_RAW_RETROSPECTIVE_CALIBRATION_BODY_BYTES + 1),
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
    assert too_large.json()["error"]["code"] == "RETROSPECTIVE_CALIBRATION_TOO_LARGE"
    assert service.calls == []


@pytest.mark.parametrize(
    ("error", "status", "code"),
    [
        (
            RetrospectiveCalibrationCancelledError(),
            409,
            RetrospectiveCalibrationErrorCodeV1.CANCELLED.value,
        ),
        (
            RetrospectiveCalibrationSourceUnavailableError(),
            503,
            RetrospectiveCalibrationErrorCodeV1.SOURCE_UNAVAILABLE.value,
        ),
        (
            RetrospectiveCalibrationTooLargeError(),
            413,
            RetrospectiveCalibrationErrorCodeV1.TOO_LARGE.value,
        ),
        (
            RetrospectiveCalibrationResultTooLargeError(),
            500,
            RetrospectiveCalibrationErrorCodeV1.RESULT_TOO_LARGE.value,
        ),
    ],
)
def test_calibration_api_maps_only_closed_core_errors(
    error: RetrospectiveCalibrationError,
    status: int,
    code: str,
) -> None:
    service = RaisingCalibrationService(error)
    app = create_app(retrospective_calibration_service=service)

    with TestClient(app, base_url=BASE_URL) as client:
        response = client.post(
            "/api/retrospective-calibration",
            json={},
            headers=_headers(),
        )

    payload = response.json()
    assert response.status_code == status
    assert payload["error"]["code"] == code
    assert set(payload["error"]) == {"code", "message"}
    assert "traceback" not in response.text.lower()
    assert "exception" not in response.text.lower()
    assert response.headers["cache-control"] == "no-store"


def test_calibration_api_maps_unexpected_failure_without_private_details() -> None:
    service = RaisingCalibrationService(RuntimeError("D:\\private\\vault\\secret.md"))
    app = create_app(retrospective_calibration_service=service)

    with TestClient(app, base_url=BASE_URL) as client:
        response = client.post(
            "/api/retrospective-calibration",
            json={},
            headers=_headers(),
        )

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "RETROSPECTIVE_CALIBRATION_SOURCE_UNAVAILABLE"
    assert "private" not in response.text
    assert "secret.md" not in response.text


def test_production_service_uses_existing_core_with_bounded_scanner(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    import second_brain.entrypoints.web.retrospective_calibration as module

    calls: list[tuple[object, object, object, object]] = []

    class FakeBuild:
        def __init__(self, scanner: object, context: object, replay: object) -> None:
            calls.append((scanner, context, replay, None))

        def execute(
            self, request: RetrospectiveCalibrationRequestV1
        ) -> RetrospectiveCalibrationResultV1:
            scanner, context, replay, _ = calls[-1]
            calls[-1] = (scanner, context, replay, request)
            return _zero_result()

    vault_path = tmp_path / "configured-vault"
    vault_path.mkdir()
    monkeypatch.setattr(module, "load_config", lambda **_: SimpleNamespace(vault_path=vault_path))
    monkeypatch.setattr(module, "BuildRetrospectiveCalibration", FakeBuild)

    service = build_production_retrospective_calibration_service()
    result = service.execute(RetrospectiveCalibrationRequestV1())

    assert result == _zero_result()
    assert len(calls) == 1
    scanner, context, replay, request = calls[0]
    assert type(scanner).__name__ == "FileSystemRetrospectiveCalibrationScanner"
    assert type(context).__name__ == "BuildRetrospectiveCalibrationContext"
    assert type(replay).__name__ == "BuildRetrospectiveCalibrationReplay"
    assert request == RetrospectiveCalibrationRequestV1()
    assert isinstance(service, LazyVaultRetrospectiveCalibrationService)


def test_empty_request_payload_has_no_fields() -> None:
    payload = RetrospectiveCalibrationRequestPayload.model_validate({}, strict=True)
    assert payload.model_dump() == {}
