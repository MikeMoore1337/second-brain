"""Web Diagnostics v1 projection and lazy composition regressions."""

from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

from second_brain.application.diagnostics import (
    DoctorDiagnosticCount,
    DoctorLayerStatus,
    DoctorReport,
    DoctorStatus,
)
from second_brain.application.reports import DiagnosticSeverity
from second_brain.entrypoints.web.app import (
    DIAGNOSTICS_REQUEST_HEADER_NAME,
    DIAGNOSTICS_REQUEST_HEADER_VALUE,
    create_app,
)
from second_brain.entrypoints.web.diagnostics import (
    DiagnosticsService,
    build_production_diagnostics_service,
)

LOOPBACK_BASE_URL = "http://127.0.0.1"
GENERATED_AT = datetime(2026, 9, 7, 12, 0, tzinfo=UTC)


def _report() -> DoctorReport:
    """Build a complete fixture using only the exact application DTO."""

    return DoctorReport(
        status=DoctorStatus.DEGRADED,
        generated_at=GENERATED_AT,
        config_resolvable=True,
        manifest_available=True,
        content_roots_available=True,
        attachments_scan_complete=True,
        manifest_schema_version=1,
        managed_note_count=4,
        enrolled_personal_memory_count=2,
        valid_decision_count=1,
        valid_outcome_count=1,
        attachment_bytes=512,
        attachment_count=3,
        timeline=DoctorLayerStatus(DoctorStatus.HEALTHY, True),
        self_model=DoctorLayerStatus(DoctorStatus.DEGRADED, True, "MODEL_WARNING"),
        self_retrieval=DoctorLayerStatus(DoctorStatus.HEALTHY, False),
        diagnostics=(DoctorDiagnosticCount("MODEL_WARNING", DiagnosticSeverity.WARNING, 1),),
    )


class RecordingDiagnosticsService:
    """Inject one DTO and prove the Web layer does not rebuild its semantics."""

    def __init__(self, report: DoctorReport) -> None:
        self.report = report
        self.calls = 0

    def build(self) -> DoctorReport:
        self.calls += 1
        return self.report


def _headers() -> dict[str, str]:
    """Return the minimum valid same-origin request metadata."""

    return {
        "Content-Type": "application/json",
        DIAGNOSTICS_REQUEST_HEADER_NAME: DIAGNOSTICS_REQUEST_HEADER_VALUE,
    }


def test_diagnostics_returns_exact_doctor_report_projection_without_leaks() -> None:
    report = _report()
    service = RecordingDiagnosticsService(report)
    application = create_app(diagnostics_service=service)

    with TestClient(application, base_url=LOOPBACK_BASE_URL) as client:
        response = client.post("/api/diagnostics", headers=_headers(), json={})

    assert response.status_code == 200
    assert response.json() == report.as_dict()
    assert service.calls == 1
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["content-type"].startswith("application/json")
    assert "access-control-allow-origin" not in response.headers
    serialized = json.dumps(response.json(), ensure_ascii=False)
    assert "traceback" not in serialized.lower()
    assert "exception" not in serialized.lower()
    assert "vault_path" not in serialized
    assert "second-brain.yaml" not in serialized


def test_production_diagnostics_service_is_lazy(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []

    def fail_if_loaded(**kwargs: object) -> object:
        del kwargs
        calls.append("load_config")
        raise AssertionError("configuration must be loaded only after refresh")

    monkeypatch.setattr(
        "second_brain.entrypoints.web.diagnostics.load_config",
        fail_if_loaded,
    )
    service: DiagnosticsService = build_production_diagnostics_service()
    assert calls == []

    report = service.build()

    assert calls == ["load_config"]
    assert report.status is DoctorStatus.UNAVAILABLE
    assert report.config_resolvable is False
    assert report.diagnostics[0].code == "CONFIG_UNAVAILABLE"


def test_diagnostics_route_is_hidden_and_post_only() -> None:
    application = create_app(diagnostics_service=RecordingDiagnosticsService(_report()))

    assert application.docs_url is None
    assert application.openapi_url is None
    with TestClient(application, base_url=LOOPBACK_BASE_URL) as client:
        response = client.get("/api/diagnostics")

    assert response.status_code == 405
    assert response.headers["cache-control"] == "no-store"
    assert "access-control-allow-origin" not in response.headers
