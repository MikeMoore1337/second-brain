"""Focused Web Personal Timeline v1 projection and boundary tests."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

import pytest
from fastapi.testclient import TestClient

from second_brain.application.timeline import (
    PersonalTimelineRequest,
    PersonalTimelineResult,
    TimelineEvidenceInvalidError,
    TimelineInvalidClockError,
    TimelineInvalidRequestError,
    TimelineItem,
    TimelineVaultUnavailableError,
    validate_personal_timeline_request,
)
from second_brain.domain.models import EvidenceAtPrecision, EvidenceKind, SelfKind
from second_brain.entrypoints.web.app import (
    MAX_RAW_TIMELINE_BODY_BYTES,
    TIMELINE_REQUEST_HEADER_NAME,
    TIMELINE_REQUEST_HEADER_VALUE,
    create_app,
)
from second_brain.entrypoints.web.timeline import (
    LazyVaultTimelineService,
    TimelineRequestPayload,
)
from tests.conftest import create_vault, snapshot_tree, write_note
from tests.test_web_drafts import send_raw_asgi_request

LOOPBACK_BASE_URL = "http://127.0.0.1"
TIMELINE_HEADERS = {
    TIMELINE_REQUEST_HEADER_NAME: TIMELINE_REQUEST_HEADER_VALUE,
}
KNOWN_ID = UUID("0198f4c5-6a00-7000-8000-000000000010")
DECISION_ID = UUID("0198f4c5-6a00-7000-8000-000000000011")
UNKNOWN_ID = UUID("0198f4c5-6a00-7000-8000-000000000012")
EVENT_AT = datetime(2026, 9, 5, 12, 30, tzinfo=UTC)
STORAGE_CREATED = datetime(2026, 9, 5, 18, 0, tzinfo=UTC)
STORAGE_UPDATED = datetime(2026, 9, 5, 19, 0, tzinfo=UTC)
GENERATED_AT = datetime(2026, 9, 5, 20, 0, tzinfo=UTC)


def timeline_result() -> PersonalTimelineResult:
    """Return a representative result with independent event/evidence kinds."""

    return PersonalTimelineResult(
        known_items=(
            TimelineItem(
                note_id=KNOWN_ID,
                relative_path="10 Projects/Outcome.md",
                event_kind=SelfKind.OUTCOME,
                evidence_kind=EvidenceKind.OUTCOME_LATER_OBSERVATION,
                event_at=EVENT_AT,
                precision=EvidenceAtPrecision.EXACT,
                domain="work",
                summary="Результат выбора",
                related_note_ids=(DECISION_ID,),
                storage_created_at=STORAGE_CREATED,
                storage_updated_at=STORAGE_UPDATED,
            ),
        ),
        unknown_items=(
            TimelineItem(
                note_id=UNKNOWN_ID,
                relative_path="30 Resources/Memory.md",
                event_kind=SelfKind.MEMORY,
                evidence_kind=EvidenceKind.USER_STATEMENT,
                event_at="unknown",
                precision=EvidenceAtPrecision.UNKNOWN,
                domain=None,
                summary="Время события не указано",
                related_note_ids=(),
                storage_created_at=STORAGE_CREATED,
                storage_updated_at=None,
            ),
        ),
        known_total=2,
        unknown_total=3,
        generated_at=GENERATED_AT,
    )


class RecordingTimelineService:
    """Record the exact application request and delegate validation to core."""

    def __init__(
        self,
        result: PersonalTimelineResult | None = None,
        error: Exception | None = None,
    ):
        self.result = result or timeline_result()
        self.error = error
        self.requests: list[PersonalTimelineRequest] = []

    def build(self, request: PersonalTimelineRequest) -> PersonalTimelineResult:
        self.requests.append(request)
        if self.error is not None:
            raise self.error
        validate_personal_timeline_request(request)
        return self.result


def test_timeline_request_payload_is_strict_and_forbids_new_filters() -> None:
    assert set(TimelineRequestPayload.model_fields) == {"order", "known_limit", "unknown_limit"}
    with pytest.raises(ValueError):
        TimelineRequestPayload.model_validate({"known_limit": True})
    with pytest.raises(ValueError):
        TimelineRequestPayload.model_validate({"order": "desc", "domain": "work"})


def test_timeline_success_preserves_core_order_times_and_safe_shape() -> None:
    service = RecordingTimelineService()
    with TestClient(create_app(timeline_service=service), base_url=LOOPBACK_BASE_URL) as client:
        response = client.post("/api/timeline", headers=TIMELINE_HEADERS, json={})

    assert response.status_code == 200
    assert service.requests == [PersonalTimelineRequest()]
    assert response.headers["cache-control"] == "no-store"
    assert "access-control-allow-origin" not in response.headers
    body = response.json()
    assert set(body) == {
        "known_items",
        "unknown_items",
        "known_total",
        "unknown_total",
        "generated_at",
    }
    assert body["known_total"] == 2
    assert body["unknown_total"] == 3
    assert body["known_items"][0] == {
        "id": str(KNOWN_ID),
        "relative_path": "10 Projects/Outcome.md",
        "event_kind": "outcome",
        "evidence_kind": "outcome_later_observation",
        "event_at": EVENT_AT.isoformat().replace("+00:00", "Z"),
        "precision": "exact",
        "domain": "work",
        "summary": "Результат выбора",
        "related_note_ids": [str(DECISION_ID)],
        "storage_created_at": STORAGE_CREATED.isoformat().replace("+00:00", "Z"),
        "storage_updated_at": STORAGE_UPDATED.isoformat().replace("+00:00", "Z"),
    }
    assert body["unknown_items"][0]["event_at"] == "unknown"
    assert body["unknown_items"][0]["precision"] == "unknown"
    assert body["unknown_items"][0]["storage_created_at"] != "unknown"
    assert "body" not in body["known_items"][0]
    assert "front_matter" not in body["known_items"][0]
    assert "absolute_path" not in body["known_items"][0]


def test_timeline_asc_is_passed_to_core_without_client_reordering() -> None:
    service = RecordingTimelineService()
    with TestClient(create_app(timeline_service=service), base_url=LOOPBACK_BASE_URL) as client:
        response = client.post(
            "/api/timeline",
            headers=TIMELINE_HEADERS,
            json={"order": "asc", "known_limit": 7, "unknown_limit": 9},
        )

    assert response.status_code == 200
    assert service.requests == [
        PersonalTimelineRequest(order="asc", known_limit=7, unknown_limit=9)
    ]


@pytest.mark.parametrize(
    "payload",
    [
        {"order": True},
        {"known_limit": True},
        {"unknown_limit": "100"},
        {"order": "sideways"},
        {"known_limit": 201},
        {"unknown_limit": -1},
        {"unexpected": "field"},
    ],
)
def test_timeline_invalid_request_is_400_and_never_partial(
    payload: dict[str, object],
) -> None:
    service = RecordingTimelineService()
    with TestClient(create_app(timeline_service=service), base_url=LOOPBACK_BASE_URL) as client:
        response = client.post("/api/timeline", headers=TIMELINE_HEADERS, json=payload)

    assert response.status_code == 400
    assert response.json() == {
        "error": {
            "code": "TIMELINE_INVALID_REQUEST",
            "message": "timeline request failed validation",
        }
    }
    assert "known_items" not in response.text


@pytest.mark.parametrize(
    ("error", "status", "code"),
    [
        (TimelineEvidenceInvalidError(), 409, "TIMELINE_EVIDENCE_INVALID"),
        (TimelineVaultUnavailableError(), 503, "TIMELINE_VAULT_UNAVAILABLE"),
        (TimelineInvalidClockError(), 500, "TIMELINE_INVALID_CLOCK"),
        (TimelineInvalidRequestError(), 400, "TIMELINE_INVALID_REQUEST"),
    ],
)
def test_timeline_core_errors_are_safe_and_have_no_partial_result(
    error: Exception,
    status: int,
    code: str,
) -> None:
    service = RecordingTimelineService(error=error)
    with TestClient(create_app(timeline_service=service), base_url=LOOPBACK_BASE_URL) as client:
        response = client.post("/api/timeline", headers=TIMELINE_HEADERS, json={})

    assert response.status_code == status
    assert response.headers["cache-control"] == "no-store"
    assert response.json()["error"]["code"] == code
    assert "absolute" not in response.text.lower()
    assert "exception" not in response.text.lower()
    assert "known_items" not in response.text


@pytest.mark.parametrize(
    "headers",
    [
        {},
        {TIMELINE_REQUEST_HEADER_NAME: "search-v1"},
        {TIMELINE_REQUEST_HEADER_NAME: TIMELINE_REQUEST_HEADER_VALUE, "host": "evil.test"},
        {
            TIMELINE_REQUEST_HEADER_NAME: TIMELINE_REQUEST_HEADER_VALUE,
            "origin": "https://evil.test",
        },
    ],
)
def test_timeline_boundary_rejects_wrong_purpose_host_origin_before_service(
    headers: dict[str, str],
) -> None:
    service = RecordingTimelineService()
    with TestClient(create_app(timeline_service=service), base_url=LOOPBACK_BASE_URL) as client:
        response = client.post("/api/timeline", headers=headers, json={})

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "TIMELINE_INVALID_REQUEST"
    assert service.requests == []


def test_timeline_boundary_requires_json_and_post() -> None:
    service = RecordingTimelineService()
    with TestClient(create_app(timeline_service=service), base_url=LOOPBACK_BASE_URL) as client:
        non_json = client.post(
            "/api/timeline",
            headers={**TIMELINE_HEADERS, "content-type": "text/plain"},
            content="{}",
        )
        get_response = client.get("/api/timeline")

    assert non_json.status_code == 400
    assert non_json.json()["error"]["code"] == "TIMELINE_INVALID_REQUEST"
    assert get_response.status_code == 405
    assert get_response.headers["cache-control"] == "no-store"
    assert service.requests == []


def test_timeline_boundary_accepts_same_origin_json_with_utf8_charset() -> None:
    service = RecordingTimelineService()
    with TestClient(create_app(timeline_service=service), base_url=LOOPBACK_BASE_URL) as client:
        response = client.post(
            "/api/timeline",
            headers={
                **TIMELINE_HEADERS,
                "content-type": "application/json; charset=utf-8",
                "origin": LOOPBACK_BASE_URL,
            },
            content="{}",
        )

    assert response.status_code == 200
    assert service.requests == [PersonalTimelineRequest()]


def test_timeline_declared_raw_body_cap_applies_before_fastapi_parser() -> None:
    service = RecordingTimelineService()
    headers = {
        "host": "127.0.0.1",
        "content-type": "application/json",
        TIMELINE_REQUEST_HEADER_NAME: TIMELINE_REQUEST_HEADER_VALUE,
        "content-length": str(MAX_RAW_TIMELINE_BODY_BYTES + 1),
    }
    status, response_headers, body, receive_calls = send_raw_asgi_request(
        create_app(timeline_service=service),
        path="/api/timeline",
        headers=headers,
        body_chunks=(b"{}",),
    )

    assert status == 413
    assert response_headers["cache-control"] == "no-store"
    assert b"TIMELINE_CONTENT_TOO_LARGE" in body
    assert receive_calls == 0
    assert service.requests == []


@pytest.mark.parametrize("content_length", [None, "1"])
def test_timeline_actual_raw_body_cap_rejects_missing_or_misleading_length(
    content_length: str | None,
) -> None:
    service = RecordingTimelineService()
    headers = {
        "host": "127.0.0.1",
        "content-type": "application/json",
        TIMELINE_REQUEST_HEADER_NAME: TIMELINE_REQUEST_HEADER_VALUE,
    }
    if content_length is not None:
        headers["content-length"] = content_length
    status, response_headers, body, receive_calls = send_raw_asgi_request(
        create_app(timeline_service=service),
        path="/api/timeline",
        headers=headers,
        body_chunks=(b"{}", b"x" * MAX_RAW_TIMELINE_BODY_BYTES),
    )

    assert status == 413
    assert response_headers["cache-control"] == "no-store"
    assert b"TIMELINE_CONTENT_TOO_LARGE" in body
    assert receive_calls == 2
    assert service.requests == []


def test_timeline_is_not_in_openapi_and_success_has_no_cors() -> None:
    with TestClient(
        create_app(timeline_service=RecordingTimelineService()),
        base_url=LOOPBACK_BASE_URL,
    ) as client:
        openapi = client.get("/openapi.json")
        response = client.post("/api/timeline", headers=TIMELINE_HEADERS, json={})

    assert openapi.status_code == 404
    assert response.status_code == 200
    assert "access-control-allow-origin" not in response.headers


def _personal_memory_note(note_id: str, *, evidence_at: str) -> str:
    """Return one minimal enrolled note for lazy current-vault rebuild tests."""

    return f"""---
id: {note_id}
type: zettel
created: 2026-09-05T18:00:00+03:00
tags: []
second_brain_personal_memory: 1
evidence_kind: user_statement
self_kind: memory
evidence_at: \"{evidence_at}\"
evidence_at_precision: exact
---
Canonical memory body.
"""


def test_lazy_service_resolves_config_only_on_request_and_rebuilds_current_vault(
    tmp_path: Path,
) -> None:
    vault = create_vault(tmp_path / "vault")
    write_note(
        vault,
        "10 Projects/First.md",
        _personal_memory_note(
            "0198f4c5-6a00-7000-8000-000000000020",
            evidence_at="2026-09-05T10:00:00Z",
        ),
    )
    before_startup = snapshot_tree(vault)
    service = LazyVaultTimelineService(vault_path_override=str(vault))
    app = create_app(vault_path_override=str(vault))
    assert snapshot_tree(vault) == before_startup

    with TestClient(app, base_url=LOOPBACK_BASE_URL) as client:
        first = client.post("/api/timeline", headers=TIMELINE_HEADERS, json={})
        assert snapshot_tree(vault) == before_startup
        write_note(
            vault,
            "10 Projects/Second.md",
            _personal_memory_note(
                "0198f4c5-6a00-7000-8000-000000000021",
                evidence_at="2026-09-05T11:00:00Z",
            ),
        )
        second = client.post("/api/timeline", headers=TIMELINE_HEADERS, json={})

    assert service.build(PersonalTimelineRequest()).known_total == 2
    assert first.status_code == 200
    assert first.json()["known_total"] == 1
    assert second.status_code == 200
    assert second.json()["known_total"] == 2
    assert snapshot_tree(vault) != before_startup


def test_timeline_ui_is_separate_safe_and_keeps_server_order() -> None:
    root = Path("src/second_brain/entrypoints/web/static")
    html = (root / "index.html").read_text(encoding="utf-8")
    javascript = (root / "timeline.js").read_text(encoding="utf-8")
    assert (
        html.index('id="decision-journal"')
        < html.index('id="timeline"')
        < html.index('id="search"')
    )
    assert "/static/timeline.js" in html
    assert "data-timeline-known-list" in html
    assert "data-timeline-unknown-list" in html
    assert "Время неизвестно" in html
    assert "textContent" in javascript
    assert ".sort(" not in javascript
    assert "innerHTML" not in javascript
    assert "localStorage" not in javascript
    assert "sessionStorage" not in javascript
    assert "indexedDB" not in javascript
    assert "setInterval" not in javascript
    assert 'X-Second-Brain-Request": "timeline-v1"' in javascript
