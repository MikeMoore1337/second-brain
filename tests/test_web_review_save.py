"""Deterministic review-token, Markdown-preview, and Web Save tests."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
from datetime import UTC, datetime
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, cast

import pytest
from fastapi.testclient import TestClient

from second_brain.adapters.vault.frontmatter import parse_front_matter
from second_brain.application.llm import MAX_MAX_OUTPUT_BYTES, NoteDraft
from second_brain.application.research import (
    ResearchSource,
    SourceKind,
    SourceProvenance,
)
from second_brain.application.research_draft import ResearchDraftResult
from second_brain.application.writes import CreateManagedNoteResult, CreateNotePlan, CreateStatus
from second_brain.domain.models import NoteType, parse_uuid7
from second_brain.entrypoints.web.app import (
    DRAFT_REQUEST_HEADER_NAME,
    DRAFT_REQUEST_HEADER_VALUE,
    MAX_RAW_DRAFT_BODY_BYTES,
    create_app,
)
from second_brain.entrypoints.web.review import (
    MAX_CONFIRMATION_TOKEN_BYTES,
    MAX_REVIEW_TOKEN_BYTES,
    ReviewTokenCodec,
    ReviewTokenError,
    ReviewTokenMode,
)
from tests.conftest import create_vault, snapshot_tree

LOOPBACK_BASE_URL = "http://127.0.0.1"
DRAFT_REQUEST_HEADERS = {DRAFT_REQUEST_HEADER_NAME: DRAFT_REQUEST_HEADER_VALUE}


class FixedDraftService:
    """Return one deterministic draft/source without external adapters."""

    def __init__(self) -> None:
        self.text_calls = 0
        self.url_calls = 0

    def draft_text(self, text: str) -> NoteDraft:
        del text
        self.text_calls += 1
        return make_draft()

    def draft_url(self, url: str) -> ResearchDraftResult:
        del url
        self.url_calls += 1
        return ResearchDraftResult(
            draft=make_draft(),
            source=SourceProvenance.from_research_source(make_source()),
        )


class RecordingSaveService:
    """Capture both Safe Write phases while returning caller-selected results."""

    def __init__(
        self,
        prepare_result: CreateManagedNoteResult | None = None,
        apply_result: CreateManagedNoteResult | None = None,
    ) -> None:
        self.prepare_result = prepare_result or make_dry_run_result()
        self.apply_result = apply_result or make_created_result()
        self.prepare_text_calls: list[NoteDraft] = []
        self.prepare_research_calls: list[tuple[NoteDraft, SourceProvenance]] = []
        self.apply_text_calls: list[NoteDraft] = []
        self.apply_research_calls: list[tuple[NoteDraft, SourceProvenance]] = []

    def prepare_text(self, draft: NoteDraft) -> CreateManagedNoteResult:
        self.prepare_text_calls.append(draft)
        return self.prepare_result

    def prepare_research(
        self,
        draft: NoteDraft,
        source: SourceProvenance,
    ) -> CreateManagedNoteResult:
        self.prepare_research_calls.append((draft, source))
        return self.prepare_result

    def apply_text(self, draft: NoteDraft) -> CreateManagedNoteResult:
        self.apply_text_calls.append(draft)
        return self.apply_result

    def apply_research(
        self,
        draft: NoteDraft,
        source: SourceProvenance,
    ) -> CreateManagedNoteResult:
        self.apply_research_calls.append((draft, source))
        return self.apply_result

    @property
    def text_calls(self) -> list[NoteDraft]:
        """Keep a compact aggregate assertion for all text-phase calls."""

        return [*self.prepare_text_calls, *self.apply_text_calls]

    @property
    def research_calls(self) -> list[tuple[NoteDraft, SourceProvenance]]:
        """Keep a compact aggregate assertion for all research-phase calls."""

        return [*self.prepare_research_calls, *self.apply_research_calls]


class PreviewTagParser(HTMLParser):
    """Collect actual parsed tags/attributes, ignoring escaped attacker text."""

    def __init__(self) -> None:
        super().__init__()
        self.tags: list[str] = []
        self.attributes: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.tags.append(tag)
        self.attributes.extend(name for name, _value in attrs)


def make_draft() -> NoteDraft:
    """Return a valid bounded draft with editable fields."""

    return NoteDraft(
        title="Web review note",
        note_type=NoteType.RESOURCE,
        content="# Exact body\n\nKeep this content.",
        tags=("first", "second"),
        links=("[[Knowledge]]", "https://example.invalid/reference"),
    )


def make_source() -> ResearchSource:
    """Return source data whose non-provenance fields must never enter a token."""

    return ResearchSource(
        uri="https://example.com/article",
        source_kind=SourceKind.WEB,
        retrieved_at=datetime(2026, 9, 5, 9, 0, 1, 123456, tzinfo=UTC),
        backend="backend-secret-sentinel",
        content="raw-source-content-sentinel",
        title="Source title",
        author="Source author",
        media_type="text/markdown",
        upstream_id="upstream-id",
        published_at=datetime(2026, 9, 4, 9, 0, tzinfo=UTC),
    )


def install_templates(vault: Path) -> None:
    """Install minimal templates for all managed note types."""

    for filename in ("Project.md", "Area.md", "Resource.md", "Zettel.md"):
        (vault / "_templates" / filename).write_text(
            f"# {filename.removesuffix('.md')} template\n",
            encoding="utf-8",
        )


def make_created_result() -> CreateManagedNoteResult:
    """Return a safe fake success result for API response tests."""

    return CreateManagedNoteResult(
        status=CreateStatus.CREATED,
        plan=CreateNotePlan(
            note_type=NoteType.RESOURCE,
            title="Web review note",
            note_id=parse_uuid7("0198f4c5-6a00-7000-8000-000000000010"),
            created=datetime(2026, 9, 5, 12, 0, tzinfo=UTC),
            relative_path="30 Resources/Web review note.md",
            content="not returned",
        ),
        apply_requested=True,
    )


def make_dry_run_result() -> CreateManagedNoteResult:
    """Return a safe fake dry-run result with a complete proposed file."""

    return CreateManagedNoteResult(
        status=CreateStatus.DRY_RUN,
        plan=CreateNotePlan(
            note_type=NoteType.RESOURCE,
            title="Web review note",
            note_id=parse_uuid7("0198f4c5-6a00-7000-8000-000000000010"),
            created=datetime(2026, 9, 5, 12, 0, tzinfo=UTC),
            relative_path="30 Resources/Web review note.md",
            content=(
                "---\n"
                "id: 0198f4c5-6a00-7000-8000-000000000010\n"
                "type: resource\n"
                "created: 2026-09-05T12:00:00+00:00\n"
                "tags: []\n"
                "links: []\n"
                "---\n"
                "# Proposed body\n"
            ),
        ),
        apply_requested=False,
    )


def make_app(
    *,
    draft_service: FixedDraftService | None = None,
    save_service: Any | None = None,
    vault_path: Path | None = None,
) -> Any:
    """Build an app with deterministic generation and optional Save composition."""

    return create_app(
        draft_service=draft_service or FixedDraftService(),
        save_service=save_service,
        vault_path_override=str(vault_path) if vault_path is not None else None,
    )


def generate_draft(client: TestClient, path: str = "/api/drafts/text") -> dict[str, Any]:
    """Generate one draft and return its JSON response."""

    payload = {"text": "material"} if path.endswith("/text") else {"url": make_source().uri}
    response = client.post(path, json=payload, headers=DRAFT_REQUEST_HEADERS)
    assert response.status_code == 200, response.text
    return cast(dict[str, Any], response.json())


def prepare_payload(
    generated: dict[str, Any],
    *,
    draft: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build the exact dry-run request without client provenance."""

    return {
        "review_token": generated["review_token"],
        "draft": draft
        or {
            "title": generated["draft"]["title"],
            "note_type": generated["draft"]["note_type"],
            "content": generated["draft"]["content"],
            "tags": generated["draft"]["tags"],
            "links": generated["draft"]["links"],
        },
    }


def apply_payload(
    generated: dict[str, Any],
    confirmation_token: str,
    *,
    draft: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build the exact confirmed apply request without client provenance."""

    payload = prepare_payload(generated, draft=draft)
    payload["confirmation_token"] = confirmation_token
    return payload


def test_review_token_round_trip_binds_only_exact_public_provenance() -> None:
    secret = b"test-review-secret-that-is-at-least-32-bytes"
    codec = ReviewTokenCodec(secret)
    source = SourceProvenance.from_research_source(make_source())

    text_token = codec.issue_text()
    text_claims = codec.verify(text_token)
    assert text_claims.mode is ReviewTokenMode.TEXT
    assert text_claims.source is None

    research_token = codec.issue_research(source)
    research_claims = codec.verify(research_token)
    assert research_claims.mode is ReviewTokenMode.RESEARCH
    assert research_claims.source == source
    assert "raw-source-content-sentinel" not in research_token
    assert "backend-secret-sentinel" not in research_token
    assert "text/markdown" not in research_token
    assert "provider" not in research_token
    assert "credentials" not in research_token
    assert secret.decode("ascii") not in repr(codec)
    assert secret.decode("ascii") not in research_token


@pytest.mark.parametrize(
    "tampered",
    [
        "not-a-token",
        "v1..signature",
        "v2.payload.signature",
        "v1!!!.payload.signature",
    ],
)
def test_review_token_rejects_malformed_or_tampered_values(tampered: str) -> None:
    codec = ReviewTokenCodec(b"test-review-secret-that-is-at-least-32-bytes")
    with pytest.raises(ReviewTokenError):
        codec.verify(tampered)

    token = codec.issue_text()
    parts = token.split(".")
    parts[2] = ("A" if parts[2][0] != "A" else "B") + parts[2][1:]
    with pytest.raises(ReviewTokenError):
        codec.verify(".".join(parts))


def test_review_token_rejects_unknown_version_fields_oversize_and_other_secret() -> None:
    secret = b"test-review-secret-that-is-at-least-32-bytes"
    codec = ReviewTokenCodec(secret)
    other_codec = ReviewTokenCodec(b"another-review-secret-that-is-32-bytes")
    token = codec.issue_text()
    with pytest.raises(ReviewTokenError):
        other_codec.verify(token)
    with pytest.raises(ReviewTokenError):
        codec.verify("x" * (MAX_REVIEW_TOKEN_BYTES + 1))

    payload = json.dumps(
        {"extra": "reject", "mode": "text", "v": 1},
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    signature = hmac.new(secret, payload, hashlib.sha256).digest()
    signed_unknown = ".".join(
        (
            "v1",
            base64.urlsafe_b64encode(payload).rstrip(b"=").decode("ascii"),
            base64.urlsafe_b64encode(signature).rstrip(b"=").decode("ascii"),
        )
    )
    with pytest.raises(ReviewTokenError):
        codec.verify(signed_unknown)


def test_confirmation_token_binds_review_context_and_exact_edited_draft() -> None:
    secret = b"test-review-secret-that-is-at-least-32-bytes"
    codec = ReviewTokenCodec(secret)
    draft = make_draft()
    review_token = codec.issue_text()
    confirmation_token = codec.issue_confirmation(
        review_token=review_token,
        draft=draft,
        mode=ReviewTokenMode.TEXT,
    )

    claims = codec.verify_confirmation(
        confirmation_token,
        review_token=review_token,
        draft=draft,
        mode=ReviewTokenMode.TEXT,
    )
    assert claims.mode is ReviewTokenMode.TEXT
    assert len(claims.review_sha256) == 64
    assert len(claims.draft_sha256) == 64
    assert len(confirmation_token) <= MAX_CONFIRMATION_TOKEN_BYTES
    assert secret.decode("ascii") not in confirmation_token
    assert "vault" not in confirmation_token
    assert "git" not in confirmation_token.casefold()

    with pytest.raises(ReviewTokenError):
        codec.verify_confirmation(
            "not-a-confirmation-token",
            review_token=review_token,
            draft=draft,
            mode=ReviewTokenMode.TEXT,
        )
    tampered_parts = confirmation_token.split(".")
    tampered_parts[2] = ("A" if tampered_parts[2][0] != "A" else "B") + tampered_parts[2][1:]
    with pytest.raises(ReviewTokenError):
        codec.verify_confirmation(
            ".".join(tampered_parts),
            review_token=review_token,
            draft=draft,
            mode=ReviewTokenMode.TEXT,
        )
    confirmation_payload = json.loads(
        base64.urlsafe_b64decode(confirmation_token.split(".")[1] + "===")
    )
    confirmation_payload["extra"] = "reject"
    unknown_payload = json.dumps(
        confirmation_payload,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    unknown_signature = hmac.new(secret, unknown_payload, hashlib.sha256).digest()
    unknown_confirmation = ".".join(
        (
            "v1",
            base64.urlsafe_b64encode(unknown_payload).rstrip(b"=").decode("ascii"),
            base64.urlsafe_b64encode(unknown_signature).rstrip(b"=").decode("ascii"),
        )
    )
    with pytest.raises(ReviewTokenError):
        codec.verify_confirmation(
            unknown_confirmation,
            review_token=review_token,
            draft=draft,
            mode=ReviewTokenMode.TEXT,
        )

    with pytest.raises(ReviewTokenError):
        codec.verify_confirmation(
            confirmation_token,
            review_token=codec.issue_text(),
            draft=draft,
            mode=ReviewTokenMode.TEXT,
        )
    with pytest.raises(ReviewTokenError):
        codec.verify_confirmation(
            confirmation_token,
            review_token=review_token,
            draft=NoteDraft(
                title=draft.title,
                note_type=draft.note_type,
                content="changed",
                tags=draft.tags,
                links=draft.links,
            ),
            mode=ReviewTokenMode.TEXT,
        )
    with pytest.raises(ReviewTokenError):
        codec.verify_confirmation(
            confirmation_token,
            review_token=review_token,
            draft=draft,
            mode=ReviewTokenMode.RESEARCH,
        )
    with pytest.raises(ReviewTokenError):
        ReviewTokenCodec(b"another-review-secret-that-is-32-bytes").verify_confirmation(
            confirmation_token,
            review_token=review_token,
            draft=draft,
            mode=ReviewTokenMode.TEXT,
        )


def test_each_generated_text_review_has_a_distinct_context_token() -> None:
    codec = ReviewTokenCodec(b"test-review-secret-that-is-at-least-32-bytes")
    assert codec.issue_text() != codec.issue_text()


def test_existing_512k_save_boundary_covers_token_draft_and_json_overhead() -> None:
    json_overhead_budget = 4 * 1024
    assert (
        MAX_REVIEW_TOKEN_BYTES
        + MAX_CONFIRMATION_TOKEN_BYTES
        + MAX_MAX_OUTPUT_BYTES
        + json_overhead_budget
        < MAX_RAW_DRAFT_BODY_BYTES
    )


@pytest.mark.parametrize(
    "content",
    [
        "<script>alert(1)</script>",
        '<img src="https://evil.example/x" onerror="alert(1)">',
        '<iframe src="https://evil.example"></iframe>',
        '<form action="https://evil.example"><input></form>',
        "<style>body{background:url(https://evil.example)}</style>",
        "<svg><script>alert(1)</script></svg>",
        '<object data="https://evil.example"></object>',
        '<embed src="https://evil.example">',
        "<div onclick=alert(1)>event-handler string</div>",
        '```javascript" onmouseover="alert(1)\n<svg>\n```',
    ],
)
def test_preview_renders_untrusted_xss_matrix_as_inert_html(content: str) -> None:
    save_service = RecordingSaveService()
    with TestClient(make_app(save_service=save_service), base_url=LOOPBACK_BASE_URL) as client:
        response = client.post(
            "/api/drafts/preview",
            json={"content": content},
            headers=DRAFT_REQUEST_HEADERS,
        )

    assert response.status_code == 200, response.text
    html = response.json()["html"].casefold()
    assert "<script" not in html
    assert "<img" not in html
    assert "<a" not in html
    assert "<iframe" not in html
    assert "<form" not in html
    assert "<style" not in html
    assert "<svg" not in html
    assert "<object" not in html
    assert "<embed" not in html
    assert 'class="language-' not in html
    tags = PreviewTagParser()
    tags.feed(html)
    assert not {
        "a",
        "img",
        "iframe",
        "form",
        "style",
        "script",
        "svg",
        "object",
        "embed",
    }.intersection(tags.tags)
    assert not {"href", "src", "style", "onerror", "onclick"}.intersection(tags.attributes)
    assert save_service.text_calls == []
    assert save_service.research_calls == []


def test_preview_keeps_markdown_labels_but_never_active_links_or_images() -> None:
    with TestClient(make_app(), base_url=LOOPBACK_BASE_URL) as client:
        response = client.post(
            "/api/drafts/preview",
            json={
                "content": (
                    "[safe label](https://evil.example)\n\n"
                    "![image alt](https://evil.example/image.png)"
                )
            },
            headers=DRAFT_REQUEST_HEADERS,
        )

    assert response.status_code == 200
    html = response.json()["html"]
    assert '<span class="preview-link">safe label</span>' in html
    assert '<span class="preview-image">[Изображение: image alt]</span>' in html
    assert "evil.example" not in html
    assert "<a" not in html
    assert "<img" not in html


def test_preview_request_is_strict_and_has_no_store() -> None:
    with TestClient(make_app(), base_url=LOOPBACK_BASE_URL) as client:
        response = client.post(
            "/api/drafts/preview",
            json={"content": "ok", "sources": []},
            headers=DRAFT_REQUEST_HEADERS,
        )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "DRAFT_INVALID_REQUEST"
    assert response.headers["cache-control"] == "no-store"


def test_text_save_uses_exact_edited_fields_and_existing_safe_write(tmp_path: Path) -> None:
    vault = create_vault(tmp_path / "vault")
    install_templates(vault)
    service = FixedDraftService()
    before = snapshot_tree(vault)
    edited = {
        "title": "Edited resource",
        "note_type": "resource",
        "content": "## Edited exact body\n\nNo network.",
        "tags": ["edited", "ordered"],
        "links": ["[[Exact link]]"],
    }

    with TestClient(
        make_app(draft_service=service, vault_path=vault), base_url=LOOPBACK_BASE_URL
    ) as client:
        generated = generate_draft(client)
        prepare_response = client.post(
            "/api/drafts/save/prepare",
            json=prepare_payload(generated, draft=edited),
            headers=DRAFT_REQUEST_HEADERS,
        )
        assert prepare_response.status_code == 200, prepare_response.text
        assert prepare_response.headers["cache-control"] == "no-store"
        prepared = prepare_response.json()
        assert set(prepared) == {"status", "note", "diff", "confirmation_token"}
        assert prepared["status"] == "dry-run"
        assert set(prepared["note"]) == {"type", "relative_path"}
        assert prepared["note"] == {
            "type": "resource",
            "relative_path": "30 Resources/Edited resource.md",
        }
        assert prepared["diff"].startswith("--- /dev/null\n+++ 30 Resources/Edited resource.md\n@@")
        assert "id: " in prepared["diff"]
        assert "created: " in prepared["diff"]
        assert "type: resource" in prepared["diff"]
        assert "tags:" in prepared["diff"]
        assert "links:" in prepared["diff"]
        assert "+## Edited exact body\n+\n+No network." in prepared["diff"]
        assert str(vault) not in prepared["diff"]
        assert snapshot_tree(vault) == before

        response = client.post(
            "/api/drafts/save/apply",
            json=apply_payload(generated, prepared["confirmation_token"], draft=edited),
            headers=DRAFT_REQUEST_HEADERS,
        )

    assert response.status_code == 200, response.text
    assert response.headers["cache-control"] == "no-store"
    payload = response.json()
    assert set(payload) == {"status", "note"}
    assert payload["status"] == "created"
    assert payload["note"]["relative_path"] == "30 Resources/Edited resource.md"
    assert set(payload["note"]) == {"id", "type", "created", "relative_path"}
    target = vault / "30 Resources" / "Edited resource.md"
    assert target.exists()
    parsed = parse_front_matter(target.read_text(encoding="utf-8"))
    assert parsed.body == "## Edited exact body\n\nNo network."
    assert parsed.data["tags"] == ["edited", "ordered"]
    assert parsed.data["links"] == ["[[Exact link]]"]
    assert parsed.data["type"] == "resource"
    assert parse_uuid7(parsed.data["id"]).version == 7
    assert set(snapshot_tree(vault)) - set(before) == {"30 Resources/Edited resource.md"}
    assert service.text_calls == 1
    assert service.url_calls == 0
    assert "target_path" not in response.text
    assert "file_identity" not in response.text
    assert str(vault) not in response.text


def test_url_save_uses_signed_provenance_and_does_not_call_research_again(tmp_path: Path) -> None:
    vault = create_vault(tmp_path / "vault")
    install_templates(vault)
    service = FixedDraftService()
    edited = {
        "title": "Reviewed source",
        "note_type": "resource",
        "content": "Reviewed body.",
        "tags": ["source"],
        "links": [],
    }
    before = snapshot_tree(vault)

    with TestClient(
        make_app(draft_service=service, vault_path=vault), base_url=LOOPBACK_BASE_URL
    ) as client:
        generated = generate_draft(client, "/api/drafts/url")
        prepare_response = client.post(
            "/api/drafts/save/prepare",
            json=prepare_payload(generated, draft=edited),
            headers=DRAFT_REQUEST_HEADERS,
        )
        assert prepare_response.status_code == 200, prepare_response.text
        assert prepare_response.headers["cache-control"] == "no-store"
        prepared = prepare_response.json()
        assert prepared["status"] == "dry-run"
        assert "sources:" in prepared["diff"]
        assert snapshot_tree(vault) == before

        response = client.post(
            "/api/drafts/save/apply",
            json=apply_payload(generated, prepared["confirmation_token"], draft=edited),
            headers=DRAFT_REQUEST_HEADERS,
        )

    assert response.status_code == 200, response.text
    target = vault / "30 Resources" / "Reviewed source.md"
    parsed = parse_front_matter(target.read_text(encoding="utf-8"))
    assert parsed.body == "Reviewed body."
    assert parsed.data["tags"] == ["source"]
    assert parsed.data["links"] == []
    assert parsed.data["sources"] == [
        {
            "uri": "https://example.com/article",
            "kind": "web",
            "retrieved_at": "2026-09-05T09:00:01.123456+00:00",
            "title": "Source title",
            "author": "Source author",
            "published_at": "2026-09-04T09:00:00+00:00",
            "upstream_id": "upstream-id",
        }
    ]
    assert service.url_calls == 1
    assert service.text_calls == 0


def test_prepare_is_dry_run_only_and_apply_requires_confirmation() -> None:
    save_service = RecordingSaveService()
    with TestClient(make_app(save_service=save_service), base_url=LOOPBACK_BASE_URL) as client:
        generated = generate_draft(client)
        prepared = client.post(
            "/api/drafts/save/prepare",
            json=prepare_payload(generated),
            headers=DRAFT_REQUEST_HEADERS,
        )
        missing = prepare_payload(generated)
        rejected = client.post(
            "/api/drafts/save/apply",
            json=apply_payload(generated, "not-a-confirmation-token"),
            headers=DRAFT_REQUEST_HEADERS,
        )
        rejected_missing = client.post(
            "/api/drafts/save/apply",
            json=missing,
            headers=DRAFT_REQUEST_HEADERS,
        )

    assert prepared.status_code == 200, prepared.text
    assert prepared.json()["status"] == "dry-run"
    assert len(save_service.prepare_text_calls) == 1
    assert save_service.apply_text_calls == []
    assert rejected.status_code == 400
    assert rejected.json()["error"]["code"] == "SAVE_CONFIRMATION_INVALID"
    assert rejected_missing.status_code == 400
    assert save_service.apply_text_calls == []


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("title", "Changed after prepare"),
        ("note_type", "project"),
        ("content", "Changed after prepare"),
        ("tags", ["changed"]),
        ("links", ["https://example.invalid/changed"]),
    ],
)
def test_apply_rejects_every_edited_field_changed_after_prepare(
    field: str,
    value: object,
) -> None:
    save_service = RecordingSaveService()
    with TestClient(make_app(save_service=save_service), base_url=LOOPBACK_BASE_URL) as client:
        generated = generate_draft(client)
        prepared = client.post(
            "/api/drafts/save/prepare",
            json=prepare_payload(generated),
            headers=DRAFT_REQUEST_HEADERS,
        )
        assert prepared.status_code == 200, prepared.text
        changed_draft = dict(generated["draft"])
        changed_draft[field] = value
        response = client.post(
            "/api/drafts/save/apply",
            json=apply_payload(
                generated,
                prepared.json()["confirmation_token"],
                draft=changed_draft,
            ),
            headers=DRAFT_REQUEST_HEADERS,
        )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "SAVE_CONFIRMATION_INVALID"
    assert save_service.apply_text_calls == []


def test_apply_rejects_unknown_fields_without_running_safe_write() -> None:
    save_service = RecordingSaveService()
    with TestClient(make_app(save_service=save_service), base_url=LOOPBACK_BASE_URL) as client:
        generated = generate_draft(client)
        prepared = client.post(
            "/api/drafts/save/prepare",
            json=prepare_payload(generated),
            headers=DRAFT_REQUEST_HEADERS,
        )
        assert prepared.status_code == 200, prepared.text
        payload = apply_payload(generated, prepared.json()["confirmation_token"])
        payload["apply"] = True
        response = client.post(
            "/api/drafts/save/apply",
            json=payload,
            headers=DRAFT_REQUEST_HEADERS,
        )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "DRAFT_INVALID_REQUEST"
    assert save_service.apply_text_calls == []


def test_legacy_one_phase_save_endpoint_cannot_apply() -> None:
    save_service = RecordingSaveService()
    with TestClient(make_app(save_service=save_service), base_url=LOOPBACK_BASE_URL) as client:
        generated = generate_draft(client)
        response = client.post(
            "/api/drafts/save",
            json=prepare_payload(generated),
            headers=DRAFT_REQUEST_HEADERS,
        )

    assert response.status_code == 404
    assert save_service.text_calls == []
    assert save_service.research_calls == []


def test_save_target_exists_is_a_safe_conflict_without_overwrite(tmp_path: Path) -> None:
    vault = create_vault(tmp_path / "vault")
    install_templates(vault)
    target = vault / "30 Resources" / "Web review note.md"
    original = "existing content\n"
    target.write_text(original, encoding="utf-8")
    service = FixedDraftService()

    with TestClient(
        make_app(draft_service=service, vault_path=vault), base_url=LOOPBACK_BASE_URL
    ) as client:
        generated = generate_draft(client)
        response = client.post(
            "/api/drafts/save/prepare",
            json=prepare_payload(generated),
            headers=DRAFT_REQUEST_HEADERS,
        )

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "CREATE_TARGET_EXISTS"
    assert target.read_text(encoding="utf-8") == original
    assert "30 Resources" not in response.text


@pytest.mark.parametrize(
    ("location", "field", "value"),
    [
        ("top", "path", "../outside.md"),
        ("top", "apply", True),
        ("top", "vault_path", "D:/private-vault"),
        ("top", "git", {"commit": True}),
        ("draft", "relative_path", "../outside.md"),
        ("draft", "id", "0198f4c5-6a00-7000-8000-000000000010"),
        ("draft", "created", "2026-09-05T12:00:00+00:00"),
    ],
)
def test_save_rejects_browser_owned_fields(
    location: str,
    field: str,
    value: object,
) -> None:
    save_service = RecordingSaveService()
    with TestClient(make_app(save_service=save_service), base_url=LOOPBACK_BASE_URL) as client:
        generated = generate_draft(client)
        payload = prepare_payload(generated)
        target = payload if location == "top" else cast(dict[str, Any], payload["draft"])
        target[field] = value
        response = client.post(
            "/api/drafts/save/prepare",
            json=payload,
            headers=DRAFT_REQUEST_HEADERS,
        )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "DRAFT_INVALID_REQUEST"
    assert save_service.text_calls == []
    assert save_service.research_calls == []


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("title", " "),
        ("note_type", "invalid"),
        ("content", "\x00"),
        ("tags", ["duplicate", "duplicate"]),
        ("links", ["line\nbreak"]),
    ],
)
def test_save_rejects_invalid_edited_note_draft(field: str, value: object) -> None:
    save_service = RecordingSaveService()
    with TestClient(make_app(save_service=save_service), base_url=LOOPBACK_BASE_URL) as client:
        generated = generate_draft(client)
        payload = prepare_payload(generated)
        cast(dict[str, Any], payload["draft"])[field] = value
        response = client.post(
            "/api/drafts/save/prepare",
            json=payload,
            headers=DRAFT_REQUEST_HEADERS,
        )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "DRAFT_SCHEMA_INVALID"
    assert save_service.text_calls == []
    assert save_service.research_calls == []


def test_save_preflight_failure_is_safe_and_writes_nothing(tmp_path: Path) -> None:
    vault = create_vault(tmp_path / "vault")
    install_templates(vault)
    (vault / "_templates" / "Resource.md").unlink()
    before = snapshot_tree(vault)

    with TestClient(make_app(vault_path=vault), base_url=LOOPBACK_BASE_URL) as client:
        generated = generate_draft(client)
        response = client.post(
            "/api/drafts/save/prepare",
            json=prepare_payload(generated),
            headers=DRAFT_REQUEST_HEADERS,
        )

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "SAVE_PREFLIGHT_CONFLICT"
    assert snapshot_tree(vault) == before


def test_client_cannot_supply_sources_or_replace_signed_provenance(tmp_path: Path) -> None:
    vault = create_vault(tmp_path / "vault")
    install_templates(vault)
    save_service = RecordingSaveService()

    with TestClient(
        make_app(vault_path=vault, save_service=save_service), base_url=LOOPBACK_BASE_URL
    ) as client:
        generated = generate_draft(client, "/api/drafts/url")
        extra_sources = prepare_payload(generated)
        extra_sources["sources"] = []
        rejected_extra = client.post(
            "/api/drafts/save/prepare",
            json=extra_sources,
            headers=DRAFT_REQUEST_HEADERS,
        )
        tampered = prepare_payload(generated)
        parts = tampered["review_token"].split(".")
        parts[1] = ("A" if parts[1][0] != "A" else "B") + parts[1][1:]
        tampered["review_token"] = ".".join(parts)
        rejected_tamper = client.post(
            "/api/drafts/save/prepare",
            json=tampered,
            headers=DRAFT_REQUEST_HEADERS,
        )

    assert rejected_extra.status_code == 400
    assert rejected_extra.json()["error"]["code"] == "DRAFT_INVALID_REQUEST"
    assert rejected_tamper.status_code == 400
    assert rejected_tamper.json()["error"]["code"] == "REVIEW_TOKEN_INVALID"
    assert save_service.text_calls == []
    assert save_service.research_calls == []
    assert not any(
        any(path.glob("*.md"))
        for path in (
            vault / "00 Inbox",
            vault / "10 Projects",
            vault / "20 Areas",
            vault / "30 Resources",
            vault / "40 Zettelkasten",
        )
    )


@pytest.mark.parametrize(
    "path,payload",
    [
        ("/api/drafts/preview", {"content": "# preview"}),
        (
            "/api/drafts/save/prepare",
            {
                "review_token": "not-a-token",
                "draft": {
                    "title": "Safe title",
                    "note_type": "resource",
                    "content": "body",
                    "tags": [],
                    "links": [],
                },
            },
        ),
        (
            "/api/drafts/save/apply",
            {
                "review_token": "not-a-token",
                "confirmation_token": "not-a-confirmation-token",
                "draft": {
                    "title": "Safe title",
                    "note_type": "resource",
                    "content": "body",
                    "tags": [],
                    "links": [],
                },
            },
        ),
    ],
)
def test_review_routes_reject_foreign_origin_before_parser_or_service(
    path: str,
    payload: dict[str, Any],
) -> None:
    save_service = RecordingSaveService()
    with TestClient(make_app(save_service=save_service), base_url=LOOPBACK_BASE_URL) as client:
        response = client.post(
            path,
            json=payload,
            headers={**DRAFT_REQUEST_HEADERS, "origin": "https://evil.example"},
        )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "DRAFT_INVALID_REQUEST"
    assert response.headers["cache-control"] == "no-store"
    assert save_service.text_calls == []
    assert save_service.research_calls == []


@pytest.mark.parametrize(
    ("status", "rollback", "expected_status", "expected_code"),
    [
        (CreateStatus.ROLLED_BACK, True, 500, "SAVE_ROLLED_BACK"),
        (CreateStatus.ROLLED_BACK, False, 500, "SAVE_RECOVERY_REQUIRED"),
    ],
)
def test_save_maps_rollback_results_without_diagnostics_leak(
    status: CreateStatus,
    rollback: bool,
    expected_status: int,
    expected_code: str,
) -> None:
    result = CreateManagedNoteResult(
        status=status,
        rollback_succeeded=rollback,
        apply_requested=True,
    )
    save_service = RecordingSaveService(apply_result=result)
    with TestClient(make_app(save_service=save_service), base_url=LOOPBACK_BASE_URL) as client:
        generated = generate_draft(client)
        prepared = client.post(
            "/api/drafts/save/prepare",
            json=prepare_payload(generated),
            headers=DRAFT_REQUEST_HEADERS,
        )
        assert prepared.status_code == 200, prepared.text
        response = client.post(
            "/api/drafts/save/apply",
            json=apply_payload(generated, prepared.json()["confirmation_token"]),
            headers=DRAFT_REQUEST_HEADERS,
        )

    assert response.status_code == expected_status
    assert response.json()["error"]["code"] == expected_code
    assert "absolute" not in response.text.lower()
    assert "receipt" not in response.text.lower()


def test_save_config_is_lazy_and_invalid_config_makes_zero_writes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = 0

    def fail_load_config(*, env_file: Path | None, vault_path_override: str | None) -> object:
        del env_file, vault_path_override
        nonlocal calls
        calls += 1
        from second_brain.config import ConfigurationError

        raise ConfigurationError("absolute secret path details")

    monkeypatch.setattr("second_brain.entrypoints.web.saves.load_config", fail_load_config)
    application = make_app()
    with TestClient(application, base_url=LOOPBACK_BASE_URL) as client:
        assert client.get("/").status_code in {200, 503}
        assert client.get("/healthz").status_code == 200
        assert (
            client.post(
                "/api/drafts/preview",
                json={"content": "# preview"},
                headers=DRAFT_REQUEST_HEADERS,
            ).status_code
            == 200
        )
        generated = generate_draft(client)
        response = client.post(
            "/api/drafts/save/prepare",
            json=prepare_payload(generated),
            headers=DRAFT_REQUEST_HEADERS,
        )

    assert calls == 1
    assert response.status_code == 503
    assert response.json() == {
        "error": {
            "code": "VAULT_UNAVAILABLE",
            "message": "Хранилище недоступно для сохранения",
        }
    }
    assert str(tmp_path) not in response.text


@pytest.mark.parametrize(
    "path",
    [
        "/api/drafts/preview",
        "/api/drafts/save/prepare",
        "/api/drafts/save/apply",
    ],
)
def test_review_routes_use_existing_boundary_and_raw_body_cap(path: str) -> None:
    with TestClient(make_app(), base_url=LOOPBACK_BASE_URL) as client:
        missing_header = client.post(path, json={}, headers={})
    assert missing_header.status_code == 400
    assert missing_header.headers["cache-control"] == "no-store"

    from tests.test_web_drafts import send_raw_asgi_request

    status, headers, body, receive_calls = send_raw_asgi_request(
        make_app(),
        path=path,
        headers={
            **DRAFT_REQUEST_HEADERS,
            "content-type": "application/json",
            "content-length": str(MAX_RAW_DRAFT_BODY_BYTES + 1),
            "host": "127.0.0.1",
        },
        body_chunks=(b"{}",),
    )
    assert status == 413
    assert headers["cache-control"] == "no-store"
    assert json.loads(body)["error"]["code"] == "DRAFT_CONTENT_TOO_LARGE"
    assert receive_calls == 0
