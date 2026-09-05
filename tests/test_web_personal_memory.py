"""Deterministic Web Personal Memory projection and confirmation-boundary tests."""

from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from typing import Any, cast

import pytest
from fastapi.testclient import TestClient

from second_brain.adapters.vault.frontmatter import parse_front_matter
from second_brain.application.personal_memory import PersonalMemoryDraft
from second_brain.application.research import SourceProvenance
from second_brain.application.writes import CreateManagedNoteResult
from second_brain.domain.models import EvidenceAtPrecision, EvidenceKind, SelfKind
from second_brain.entrypoints.web.review import (
    ReviewTokenCodec,
    ReviewTokenError,
    ReviewTokenMode,
)
from tests.conftest import create_vault, snapshot_tree
from tests.test_web_review_save import (
    DRAFT_REQUEST_HEADERS,
    LOOPBACK_BASE_URL,
    FixedDraftService,
    RecordingSaveService,
    generate_draft,
    install_templates,
    make_app,
    make_draft,
    make_source,
)


def make_personal_memory_payload(
    *,
    evidence_kind: str = "explicit_user_fact",
    self_kind: str = "memory",
    evidence_at: str = "2026-09-05T15:30:00Z",
    evidence_at_precision: str = "exact",
    domain: str | None = "health",
) -> dict[str, object]:
    """Build only the five client-owned Stage 1 Personal Memory fields."""

    return {
        "evidence_kind": evidence_kind,
        "self_kind": self_kind,
        "evidence_at": evidence_at,
        "evidence_at_precision": evidence_at_precision,
        "domain": domain,
    }


def make_personal_memory_draft() -> PersonalMemoryDraft:
    """Build one valid normalized-boundary candidate for codec tests."""

    return PersonalMemoryDraft(
        draft=make_draft(),
        evidence_kind=EvidenceKind.EXPLICIT_USER_FACT,
        self_kind=SelfKind.MEMORY,
        evidence_at="2026-09-05T15:30:00Z",
        evidence_at_precision=EvidenceAtPrecision.EXACT,
        domain="health",
    )


def prepare_personal_memory_payload(
    generated: dict[str, Any],
    *,
    personal_memory: dict[str, object] | None = None,
    draft: dict[str, object] | None = None,
) -> dict[str, object]:
    """Build the exact additive PM prepare request."""

    return {
        "review_token": generated["review_token"],
        "draft": deepcopy(draft or generated["draft"]),
        "personal_memory": deepcopy(personal_memory or make_personal_memory_payload()),
    }


def apply_personal_memory_payload(
    generated: dict[str, Any],
    confirmation_token: str,
    *,
    personal_memory: dict[str, object] | None = None,
    draft: dict[str, object] | None = None,
) -> dict[str, object]:
    """Build the exact additive PM apply request."""

    payload = prepare_personal_memory_payload(
        generated,
        personal_memory=personal_memory,
        draft=draft,
    )
    payload["confirmation_token"] = confirmation_token
    return payload


class RecordingPersonalMemorySaveService(RecordingSaveService):
    """Record PM service calls while retaining generic Save regression behavior."""

    def __init__(
        self,
        prepare_result: CreateManagedNoteResult | None = None,
        apply_result: CreateManagedNoteResult | None = None,
    ) -> None:
        super().__init__(prepare_result=prepare_result, apply_result=apply_result)
        self.prepare_personal_memory_calls: list[PersonalMemoryDraft] = []
        self.apply_personal_memory_calls: list[PersonalMemoryDraft] = []

    def prepare_personal_memory(self, draft: PersonalMemoryDraft) -> CreateManagedNoteResult:
        self.prepare_personal_memory_calls.append(draft)
        return self.prepare_result

    def apply_personal_memory(self, draft: PersonalMemoryDraft) -> CreateManagedNoteResult:
        self.apply_personal_memory_calls.append(draft)
        return self.apply_result


def test_personal_memory_confirmation_has_separate_purpose_and_metadata_binding() -> None:
    """PM and generic confirmation tokens remain non-interchangeable and exact-bound."""

    codec = ReviewTokenCodec(b"test-review-secret-that-is-at-least-32-bytes")
    reviewed = make_personal_memory_draft()
    review_token = codec.issue_text()
    metadata = reviewed.metadata
    confirmation_token = codec.issue_personal_memory_confirmation(
        review_token=review_token,
        draft=reviewed.draft,
        metadata=metadata,
    )

    claims = codec.verify_personal_memory_confirmation(
        confirmation_token,
        review_token=review_token,
        draft=reviewed.draft,
        metadata=metadata,
    )
    assert claims.mode is ReviewTokenMode.TEXT
    assert len(claims.review_sha256) == 64
    assert len(claims.draft_sha256) == 64
    assert len(claims.personal_memory_sha256) == 64
    assert "personal-memory-save-confirmation" not in confirmation_token

    generic_token = codec.issue_confirmation(
        review_token=review_token,
        draft=reviewed.draft,
        mode=ReviewTokenMode.TEXT,
    )
    with pytest.raises(ReviewTokenError):
        codec.verify_confirmation(
            confirmation_token,
            review_token=review_token,
            draft=reviewed.draft,
            mode=ReviewTokenMode.TEXT,
        )
    with pytest.raises(ReviewTokenError):
        codec.verify_personal_memory_confirmation(
            generic_token,
            review_token=review_token,
            draft=reviewed.draft,
            metadata=metadata,
        )
    with pytest.raises(ReviewTokenError):
        codec.verify_personal_memory_confirmation(
            confirmation_token,
            review_token=review_token,
            draft=reviewed.draft,
            metadata=PersonalMemoryDraft(
                draft=reviewed.draft,
                evidence_kind=reviewed.evidence_kind,
                self_kind=reviewed.self_kind,
                evidence_at="unknown",
                evidence_at_precision=EvidenceAtPrecision.UNKNOWN,
                domain=reviewed.domain,
            ).metadata,
        )
    with pytest.raises(ReviewTokenError):
        codec.issue_personal_memory_confirmation(
            review_token=codec.issue_research(SourceProvenance.from_research_source(make_source())),
            draft=reviewed.draft,
            metadata=metadata,
        )


def test_personal_memory_save_uses_existing_safe_write_and_exact_diff(tmp_path: Path) -> None:
    """The PM Web path produces a dry-run first and writes only after exact apply."""

    vault = create_vault(tmp_path / "vault")
    install_templates(vault)
    before = snapshot_tree(vault)
    edited: dict[str, object] = {
        "title": "Reviewed personal memory",
        "note_type": "resource",
        "content": "Я предпочитаю ясные границы.\n",
        "tags": ["reviewed", "memory"],
        "links": ["[[Context]]"],
    }

    with TestClient(
        make_app(draft_service=FixedDraftService(), vault_path=vault),
        base_url=LOOPBACK_BASE_URL,
    ) as client:
        generated = generate_draft(client)
        prepared_response = client.post(
            "/api/drafts/personal-memory/save/prepare",
            json=prepare_personal_memory_payload(generated, draft=edited),
            headers=DRAFT_REQUEST_HEADERS,
        )
        assert prepared_response.status_code == 200, prepared_response.text
        prepared = prepared_response.json()
        assert set(prepared) == {"status", "note", "diff", "confirmation_token"}
        assert prepared["status"] == "dry-run"
        assert prepared["diff"].startswith(
            "--- /dev/null\n+++ 30 Resources/Reviewed personal memory.md\n@@"
        )
        assert "+second_brain_personal_memory: 1" in prepared["diff"]
        assert "+evidence_kind: explicit_user_fact" in prepared["diff"]
        assert "+self_kind: memory" in prepared["diff"]
        assert "+evidence_at:" in prepared["diff"]
        assert "2026-09-05T15:30:00+00:00" in prepared["diff"]
        assert "+evidence_at_precision: exact" in prepared["diff"]
        assert "+domain: health" in prepared["diff"]
        assert snapshot_tree(vault) == before

        response = client.post(
            "/api/drafts/personal-memory/save/apply",
            json=apply_personal_memory_payload(
                generated,
                prepared["confirmation_token"],
                draft=edited,
            ),
            headers=DRAFT_REQUEST_HEADERS,
        )

    assert response.status_code == 200, response.text
    assert response.json()["status"] == "created"
    target = vault / "30 Resources" / "Reviewed personal memory.md"
    parsed = parse_front_matter(target.read_text(encoding="utf-8"))
    assert parsed.body == edited["content"]
    assert parsed.data["second_brain_personal_memory"] == 1
    assert parsed.data["evidence_kind"] == "explicit_user_fact"
    assert parsed.data["self_kind"] == "memory"
    assert parsed.data["evidence_at"] == "2026-09-05T15:30:00+00:00"
    assert parsed.data["evidence_at_precision"] == "exact"
    assert parsed.data["domain"] == "health"
    assert parsed.data["tags"] == edited["tags"]
    assert parsed.data["links"] == edited["links"]
    assert set(snapshot_tree(vault)) - set(before) == {"30 Resources/Reviewed personal memory.md"}
    assert "absolute" not in response.text.lower()
    assert "receipt" not in response.text.lower()
    assert str(vault) not in response.text


def test_research_review_token_is_rejected_before_pm_save_service() -> None:
    """Signed research context cannot cross the Personal Memory write boundary."""

    save_service = RecordingPersonalMemorySaveService()
    with TestClient(make_app(save_service=save_service), base_url=LOOPBACK_BASE_URL) as client:
        generated = generate_draft(client, "/api/drafts/url")
        prepare_response = client.post(
            "/api/drafts/personal-memory/save/prepare",
            json=prepare_personal_memory_payload(generated),
            headers=DRAFT_REQUEST_HEADERS,
        )
        apply_response = client.post(
            "/api/drafts/personal-memory/save/apply",
            json=apply_personal_memory_payload(generated, "not-a-confirmation-token"),
            headers=DRAFT_REQUEST_HEADERS,
        )

    assert prepare_response.status_code == 400
    assert prepare_response.json()["error"]["code"] == "PERSONAL_MEMORY_CONTEXT_INVALID"
    assert apply_response.status_code == 400
    assert apply_response.json()["error"]["code"] == "PERSONAL_MEMORY_CONTEXT_INVALID"
    assert save_service.prepare_personal_memory_calls == []
    assert save_service.apply_personal_memory_calls == []


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("title", "Changed title"),
        ("note_type", "project"),
        ("content", "Changed body"),
        ("tags", ["changed"]),
        ("links", ["[[Changed]]"]),
    ],
)
def test_pm_apply_rejects_each_note_draft_field_after_prepare(field: str, value: object) -> None:
    """The PM token binds all five existing NoteDraft fields."""

    save_service = RecordingPersonalMemorySaveService()
    with TestClient(make_app(save_service=save_service), base_url=LOOPBACK_BASE_URL) as client:
        generated = generate_draft(client)
        prepared = client.post(
            "/api/drafts/personal-memory/save/prepare",
            json=prepare_personal_memory_payload(generated),
            headers=DRAFT_REQUEST_HEADERS,
        )
        assert prepared.status_code == 200, prepared.text
        changed_draft = deepcopy(generated["draft"])
        changed_draft[field] = value
        response = client.post(
            "/api/drafts/personal-memory/save/apply",
            json=apply_personal_memory_payload(
                generated,
                prepared.json()["confirmation_token"],
                draft=changed_draft,
            ),
            headers=DRAFT_REQUEST_HEADERS,
        )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "SAVE_CONFIRMATION_INVALID"
    assert save_service.apply_personal_memory_calls == []


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("evidence_kind", "user_statement"),
        ("self_kind", "preference"),
        ("evidence_at", "2026-09-06T15:30:00Z"),
        ("evidence_at_precision", "unknown"),
        ("domain", None),
    ],
)
def test_pm_apply_rejects_each_personal_memory_field_after_prepare(
    field: str,
    value: object,
) -> None:
    """The PM token binds every normalized metadata field, including None/time mode."""

    save_service = RecordingPersonalMemorySaveService()
    with TestClient(make_app(save_service=save_service), base_url=LOOPBACK_BASE_URL) as client:
        generated = generate_draft(client)
        prepared = client.post(
            "/api/drafts/personal-memory/save/prepare",
            json=prepare_personal_memory_payload(generated),
            headers=DRAFT_REQUEST_HEADERS,
        )
        assert prepared.status_code == 200, prepared.text
        changed_metadata = make_personal_memory_payload()
        if field == "evidence_at_precision":
            changed_metadata["evidence_at"] = "unknown"
        changed_metadata[field] = value
        response = client.post(
            "/api/drafts/personal-memory/save/apply",
            json=apply_personal_memory_payload(
                generated,
                prepared.json()["confirmation_token"],
                personal_memory=changed_metadata,
            ),
            headers=DRAFT_REQUEST_HEADERS,
        )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "SAVE_CONFIRMATION_INVALID"
    assert save_service.apply_personal_memory_calls == []


@pytest.mark.parametrize(
    "personal_memory",
    [
        {**make_personal_memory_payload(), "evidence_kind": "decision"},
        {**make_personal_memory_payload(), "self_kind": "outcome"},
        {**make_personal_memory_payload(), "evidence_at": "2026-09-05T15:30:00"},
        {**make_personal_memory_payload(), "evidence_at_precision": "unknown"},
        {**make_personal_memory_payload(), "domain": "[health, work]"},
        {**make_personal_memory_payload(), "domain": True},
        {**make_personal_memory_payload(), "evidence_kind": 1},
    ],
)
def test_pm_prepare_rejects_invalid_controlled_metadata(
    personal_memory: dict[str, object],
) -> None:
    """Stage 1 validation remains the single source of metadata semantics."""

    save_service = RecordingPersonalMemorySaveService()
    with TestClient(make_app(save_service=save_service), base_url=LOOPBACK_BASE_URL) as client:
        generated = generate_draft(client)
        response = client.post(
            "/api/drafts/personal-memory/save/prepare",
            json=prepare_personal_memory_payload(generated, personal_memory=personal_memory),
            headers=DRAFT_REQUEST_HEADERS,
        )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "PERSONAL_MEMORY_INVALID_REQUEST"
    assert save_service.prepare_personal_memory_calls == []


@pytest.mark.parametrize(
    "extra",
    [
        {"second_brain_personal_memory": 1},
        {"front_matter": {}},
        {"path": "30 Resources/unsafe.md"},
        {"id": "0198f4c5-6a00-7000-8000-000000000010"},
        {"created": "2026-09-05T15:30:00+00:00"},
        {"metadata": {"arbitrary": True}},
    ],
)
def test_pm_http_dto_rejects_application_owned_or_arbitrary_fields(
    extra: dict[str, object],
) -> None:
    """The client can submit only the additive strict projection."""

    save_service = RecordingPersonalMemorySaveService()
    with TestClient(make_app(save_service=save_service), base_url=LOOPBACK_BASE_URL) as client:
        generated = generate_draft(client)
        payload = prepare_personal_memory_payload(generated)
        payload.update(extra)
        response = client.post(
            "/api/drafts/personal-memory/save/prepare",
            json=payload,
            headers=DRAFT_REQUEST_HEADERS,
        )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "PERSONAL_MEMORY_INVALID_REQUEST"
    assert save_service.prepare_personal_memory_calls == []


def test_pm_routes_use_draft_v1_boundary_and_raw_body_cap() -> None:
    """Both PM routes stay inside the existing loopback JSON/body boundary."""

    paths = (
        "/api/drafts/personal-memory/save/prepare",
        "/api/drafts/personal-memory/save/apply",
    )
    with TestClient(make_app(), base_url=LOOPBACK_BASE_URL) as client:
        for path in paths:
            missing_header = client.post(path, json={}, headers={})
            assert missing_header.status_code == 400
            assert missing_header.json()["error"]["code"] == "PERSONAL_MEMORY_INVALID_REQUEST"
            assert missing_header.headers["cache-control"] == "no-store"

    from tests.test_web_drafts import send_raw_asgi_request

    status, headers, body, receive_calls = send_raw_asgi_request(
        make_app(),
        path=paths[0],
        headers={
            **DRAFT_REQUEST_HEADERS,
            "content-type": "application/json",
            "content-length": "524289",
            "host": "127.0.0.1",
        },
        body_chunks=(b"{}",),
    )
    assert status == 413
    assert headers["cache-control"] == "no-store"
    assert cast(dict[str, Any], json.loads(body))["error"]["code"] == ("DRAFT_CONTENT_TOO_LARGE")
    assert receive_calls == 0


@pytest.mark.parametrize(
    "path", ("/api/drafts/personal-memory/save/prepare", "/api/drafts/personal-memory/save/apply")
)
@pytest.mark.parametrize(
    "headers, content",
    [
        ({**DRAFT_REQUEST_HEADERS, "origin": "https://evil.example"}, b"{}"),
        ({**DRAFT_REQUEST_HEADERS, "host": "unexpected.example"}, b"{}"),
        (DRAFT_REQUEST_HEADERS, b"{}"),
        ({**DRAFT_REQUEST_HEADERS, "content-type": "text/plain"}, b"{}"),
    ],
)
def test_pm_routes_reject_untrusted_http_boundary_before_parser(
    path: str,
    headers: dict[str, str],
    content: bytes,
) -> None:
    """PM endpoints inherit the existing Host, Origin and JSON gate without service calls."""

    save_service = RecordingPersonalMemorySaveService()
    with TestClient(make_app(save_service=save_service), base_url=LOOPBACK_BASE_URL) as client:
        response = client.post(path, content=content, headers=headers)

    assert response.status_code == 400
    assert response.headers["cache-control"] == "no-store"
    assert "access-control-allow-origin" not in response.headers
    assert save_service.prepare_personal_memory_calls == []
    assert save_service.apply_personal_memory_calls == []


def test_pm_confirmation_cannot_be_used_by_generic_apply() -> None:
    """The two apply endpoints reject each other's confirmation purpose."""

    save_service = RecordingPersonalMemorySaveService()
    with TestClient(make_app(save_service=save_service), base_url=LOOPBACK_BASE_URL) as client:
        generated = generate_draft(client)
        generic_prepared = client.post(
            "/api/drafts/save/prepare",
            json={"review_token": generated["review_token"], "draft": generated["draft"]},
            headers=DRAFT_REQUEST_HEADERS,
        )
        pm_prepared = client.post(
            "/api/drafts/personal-memory/save/prepare",
            json=prepare_personal_memory_payload(generated),
            headers=DRAFT_REQUEST_HEADERS,
        )
        assert generic_prepared.status_code == 200, generic_prepared.text
        assert pm_prepared.status_code == 200, pm_prepared.text

        generic_response = client.post(
            "/api/drafts/save/apply",
            json={
                "review_token": generated["review_token"],
                "confirmation_token": pm_prepared.json()["confirmation_token"],
                "draft": generated["draft"],
            },
            headers=DRAFT_REQUEST_HEADERS,
        )
        pm_response = client.post(
            "/api/drafts/personal-memory/save/apply",
            json=apply_personal_memory_payload(
                generated,
                generic_prepared.json()["confirmation_token"],
            ),
            headers=DRAFT_REQUEST_HEADERS,
        )

    assert generic_response.status_code == 400
    assert generic_response.json()["error"]["code"] == "SAVE_CONFIRMATION_INVALID"
    assert pm_response.status_code == 400
    assert pm_response.json()["error"]["code"] == "SAVE_CONFIRMATION_INVALID"
    assert save_service.apply_text_calls == []
    assert save_service.apply_personal_memory_calls == []


def test_pm_create_app_and_shell_remain_lazy_without_vault_config(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """PM configuration is resolved only after explicit PM Save prepare."""

    calls = 0

    def fail_load_config(*, env_file: Path | None, vault_path_override: str | None) -> object:
        del env_file, vault_path_override
        nonlocal calls
        calls += 1
        from second_brain.config import ConfigurationError

        raise ConfigurationError("secret path details")

    monkeypatch.setattr("second_brain.entrypoints.web.saves.load_config", fail_load_config)
    with TestClient(make_app(), base_url=LOOPBACK_BASE_URL) as client:
        assert client.get("/").status_code == 200
        assert client.get("/healthz").status_code == 200
        generated = generate_draft(client)
        response = client.post(
            "/api/drafts/personal-memory/save/prepare",
            json=prepare_personal_memory_payload(generated),
            headers=DRAFT_REQUEST_HEADERS,
        )

    assert calls == 1
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "VAULT_UNAVAILABLE"
    assert "secret path details" not in response.text


def test_personal_memory_ui_is_explicit_source_free_and_storage_free() -> None:
    """Static GUI wiring keeps PM opt-in and page-memory-only state visible in code."""

    app_js = Path("src/second_brain/entrypoints/web/static/app.js").read_text(encoding="utf-8")
    app_css = (
        Path("src/second_brain/entrypoints/web/static/app.css")
        .read_text(encoding="utf-8")
        .replace("\r\n", "\n")
    )
    assert "/api/drafts/personal-memory/save/prepare" in app_js
    assert "/api/drafts/personal-memory/save/apply" in app_js
    assert 'personalMemoryToggle.type = "checkbox"' in app_js
    assert "sourceFreeText" in app_js
    assert "personalMemoryEnabled" in app_js
    assert "new Date().toISOString()" in app_js
    for value in (
        "explicit_user_fact",
        "user_statement",
        "memory",
        "preference",
        "belief",
        "goal",
        "exact",
        "unknown",
    ):
        assert value in app_js
    assert "decision" not in app_js
    assert "outcome" not in app_js
    assert "localStorage" not in app_js
    assert "sessionStorage" not in app_js
    assert app_js.count("innerHTML") == 1
    assert ".personal-memory-fields {\n  display: grid;" in app_css
    assert ".personal-memory-time-field {\n  display: grid;" in app_css
    assert (
        ".personal-memory-fields[hidden],\n"
        ".personal-memory-time-field[hidden] {\n"
        "  display: none !important;\n"
        "}"
    ) in app_css
