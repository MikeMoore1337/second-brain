"""Проверки offline NoteDraft -> Safe Write v1 без внешних вызовов."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from typer.main import get_command
from typer.testing import CliRunner

from second_brain.adapters.vault.frontmatter import parse_front_matter
from second_brain.application.draft_files import (
    MAX_NOTE_DRAFT_FILE_BYTES,
    DraftFileError,
    read_note_draft_file,
)
from second_brain.application.llm import (
    MAX_CONTENT_BYTES,
    MAX_LINK_BYTES,
    MAX_LINKS,
    MAX_TAG_BYTES,
    MAX_TAGS,
    MAX_TITLE_BYTES,
    NoteDraft,
)
from second_brain.application.services import CreateManagedNoteFromDraft
from second_brain.application.writes import (
    CreateManagedNoteFromDraftRequest,
    CreateStatus,
)
from second_brain.domain.models import NoteType, parse_rfc3339, parse_uuid7
from second_brain.entrypoints.cli.app import app
from tests.conftest import create_vault, snapshot_tree, write_note

runner = CliRunner()


def install_templates(vault: Path, project_template: str = "# Template body\n") -> None:
    """Добавить минимальные templates для всех managed типов."""

    templates = {
        "Project.md": project_template,
        "Area.md": "# Area template\n",
        "Resource.md": "# Resource template\n",
        "Zettel.md": "# Zettel template\n",
    }
    for filename, content in templates.items():
        (vault / "_templates" / filename).write_text(content, encoding="utf-8")


def make_payload(**overrides: object) -> dict[str, object]:
    """Собрать valid five-field JSON payload для теста."""

    payload: dict[str, object] = {
        "title": "Reviewed draft",
        "note_type": "project",
        "content": "## Exact draft body\n\nНе менять.",
        "tags": ["first", "second"],
        "links": ["[[Future note]]", "https://example.invalid/source"],
    }
    payload.update(overrides)
    return payload


def write_draft(path: Path, payload: dict[str, object]) -> None:
    """Записать review artifact как UTF-8 JSON."""

    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def invoke_create_from_draft(vault: Path, draft: Path, *arguments: str) -> Any:
    """Вызвать только offline draft command."""

    return runner.invoke(
        app,
        ["--vault-path", str(vault), "note", "create-from-draft", "--file", str(draft), *arguments],
    )


def test_strict_decoder_returns_one_typed_note_draft(tmp_path: Path) -> None:
    path = tmp_path / "draft.json"
    write_draft(path, make_payload())

    result = read_note_draft_file(path)

    assert result == NoteDraft(
        title="Reviewed draft",
        note_type=NoteType.PROJECT,
        content="## Exact draft body\n\nНе менять.",
        tags=("first", "second"),
        links=("[[Future note]]", "https://example.invalid/source"),
    )


@pytest.mark.parametrize(
    ("raw", "expected_code"),
    [
        (
            '{"title":"one","title":"two","note_type":"project",'
            '"content":"body","tags":[],"links":[]}',
            "DRAFT_JSON_INVALID",
        ),
        (
            '{"title":"one","note_type":"project","content":NaN,"tags":[],"links":[]}',
            "DRAFT_JSON_INVALID",
        ),
        ("[]", "DRAFT_SCHEMA_INVALID"),
        (
            json.dumps({"title": "one", "note_type": "project", "content": "body", "tags": []}),
            "DRAFT_SCHEMA_INVALID",
        ),
        (
            json.dumps({**make_payload(), "unknown": "field"}),
            "DRAFT_SCHEMA_INVALID",
        ),
        (json.dumps({**make_payload(), "title": 42}), "DRAFT_SCHEMA_INVALID"),
        (json.dumps({**make_payload(), "note_type": 42}), "DRAFT_SCHEMA_INVALID"),
        (json.dumps({**make_payload(), "content": None}), "DRAFT_SCHEMA_INVALID"),
        (json.dumps({**make_payload(), "tags": "not-a-list"}), "DRAFT_SCHEMA_INVALID"),
        (json.dumps({**make_payload(), "links": ["ok", 42]}), "DRAFT_SCHEMA_INVALID"),
        (json.dumps({**make_payload(), "note_type": "note"}), "DRAFT_SCHEMA_INVALID"),
        (json.dumps({**make_payload(), "note_type": "unsupported"}), "DRAFT_SCHEMA_INVALID"),
    ],
)
def test_strict_decoder_rejects_malformed_json_schema_and_types(
    tmp_path: Path,
    raw: str,
    expected_code: str,
) -> None:
    path = tmp_path / "draft.json"
    path.write_text(raw, encoding="utf-8")

    with pytest.raises(DraftFileError) as raised:
        read_note_draft_file(path)

    assert raised.value.code == expected_code
    assert "body" not in str(raised.value)


def test_decoder_rejects_non_utf8_and_bounded_file(tmp_path: Path) -> None:
    invalid_utf8 = tmp_path / "invalid.json"
    invalid_utf8.write_bytes(b"\xff\xfe")
    with pytest.raises(DraftFileError) as invalid_error:
        read_note_draft_file(invalid_utf8)
    assert invalid_error.value.code == "DRAFT_FILE_INVALID"

    oversized = tmp_path / "oversized.json"
    oversized.write_bytes(b"x" * (MAX_NOTE_DRAFT_FILE_BYTES + 1))
    with pytest.raises(DraftFileError) as oversized_error:
        read_note_draft_file(oversized)
    assert oversized_error.value.code == "DRAFT_FILE_INVALID"


@pytest.mark.parametrize(
    "payload",
    [
        make_payload(title="x" * (MAX_TITLE_BYTES + 1)),
        make_payload(content="x" * (MAX_CONTENT_BYTES + 1)),
        make_payload(tags=["x" * (MAX_TAG_BYTES + 1)]),
        make_payload(tags=[f"tag-{index}" for index in range(MAX_TAGS + 1)]),
        make_payload(tags=["Tag", "tag"]),
        make_payload(links=["x" * (MAX_LINK_BYTES + 1)]),
        make_payload(links=[f"[[link-{index}]]" for index in range(MAX_LINKS + 1)]),
    ],
)
def test_decoder_reuses_existing_note_draft_semantic_limits(
    tmp_path: Path,
    payload: dict[str, object],
) -> None:
    path = tmp_path / "draft.json"
    write_draft(path, payload)

    with pytest.raises(DraftFileError) as raised:
        read_note_draft_file(path)

    assert raised.value.code == "DRAFT_SCHEMA_INVALID"


def test_draft_cli_reports_invalid_input_without_raw_content_or_vault_access(
    tmp_path: Path,
) -> None:
    secret = "untrusted-body-secret-that-must-not-leak"
    draft = tmp_path / "draft.json"
    write_draft(draft, make_payload(title="", content=secret))

    result = runner.invoke(
        app,
        ["note", "create-from-draft", "--file", str(draft), "--format", "json"],
    )

    assert result.exit_code == 1
    assert result.stdout == ""
    assert secret not in result.stderr
    payload = json.loads(result.stderr)
    assert payload["error"]["code"] == "DRAFT_SCHEMA_INVALID"


def test_draft_dry_run_is_lossless_and_does_not_write_vault(tmp_path: Path) -> None:
    vault = create_vault(tmp_path / "vault")
    install_templates(
        vault,
        "---\n"
        "# Preserve template metadata\n"
        'custom_field: "retained"\n'
        "tags: [template-default]\n"
        "links: [template-default-link]\n"
        "---\n"
        "# Template body must be ignored\n",
    )
    draft = tmp_path / "draft.json"
    content = "## Exact draft body\r\n\r\nБез semantic rewrite."
    write_draft(
        draft,
        make_payload(
            title="Reviewed project",
            content=content,
            tags=["first", "second", "first with spaces"],
            links=["[[One]]", "https://example.invalid/two"],
        ),
    )
    before = snapshot_tree(vault)

    result = invoke_create_from_draft(vault, draft, "--format", "json")

    assert result.exit_code == 0, result.stdout + result.stderr
    payload = json.loads(result.stdout)
    assert payload["status"] == CreateStatus.DRY_RUN.value
    assert payload["mode"] == "dry-run"
    plan = payload["plan"]
    assert plan["relative_path"] == "10 Projects/Reviewed project.md"
    assert parse_uuid7(plan["id"]).version == 7
    assert parse_rfc3339(plan["created"]).utcoffset() is not None
    rendered = plan["content"]
    parsed = parse_front_matter(rendered)
    assert parsed.data["type"] == "project"
    assert parsed.data["custom_field"] == "retained"
    assert parsed.data["tags"] == ["first", "second", "first with spaces"]
    assert parsed.data["links"] == ["[[One]]", "https://example.invalid/two"]
    assert parsed.body == content
    assert "Template body must be ignored" not in rendered
    assert snapshot_tree(vault) == before


def test_explicit_apply_creates_exactly_one_valid_managed_note(tmp_path: Path) -> None:
    vault = create_vault(tmp_path / "vault")
    install_templates(vault, "# Template body must be ignored\n")
    draft = tmp_path / "draft.json"
    content = "# Reviewed content\n\nТело сохраняется буквально."
    write_draft(
        draft,
        make_payload(
            title="Applied project",
            content=content,
            tags=["zeta", "alpha", "zeta-like"],
            links=["[[Candidate B]]", "[[Candidate A]]"],
        ),
    )
    before = snapshot_tree(vault)

    result = invoke_create_from_draft(vault, draft, "--apply", "--format", "json")

    assert result.exit_code == 0, result.stdout + result.stderr
    payload = json.loads(result.stdout)
    assert payload["status"] == CreateStatus.CREATED.value
    assert payload["mode"] == "apply"
    assert payload["post_write_validation"]["errors"] == 0
    target = vault / "10 Projects" / "Applied project.md"
    assert target.exists()
    after = snapshot_tree(vault)
    assert set(after) - set(before) == {"10 Projects/Applied project.md"}
    assert {path: after[path] for path in before} == before
    parsed = parse_front_matter(target.read_text(encoding="utf-8"))
    assert parse_uuid7(parsed.data["id"]).version == 7
    assert parsed.data["type"] == "project"
    assert parse_rfc3339(parsed.data["created"]).utcoffset() is not None
    assert parsed.data["tags"] == ["zeta", "alpha", "zeta-like"]
    assert parsed.data["links"] == ["[[Candidate B]]", "[[Candidate A]]"]
    assert parsed.body == content
    assert not list((vault / "10 Projects").glob(".second-brain-*.tmp"))


def test_existing_target_is_rejected_without_overwrite(tmp_path: Path) -> None:
    vault = create_vault(tmp_path / "vault")
    install_templates(vault)
    target = write_note(vault, "10 Projects/Reviewed draft.md", "original\n")
    before = target.read_bytes()
    draft = tmp_path / "draft.json"
    write_draft(draft, make_payload())

    result = invoke_create_from_draft(vault, draft, "--apply")

    assert result.exit_code == 1
    assert "CREATE_TARGET_EXISTS" in result.stdout
    assert target.read_bytes() == before
    assert not list((vault / "10 Projects").glob(".second-brain-*.tmp"))


def test_failed_post_write_validation_rolls_back_draft_note(tmp_path: Path) -> None:
    vault = create_vault(tmp_path / "vault")
    install_templates(vault)
    draft = tmp_path / "draft.json"
    write_draft(draft, make_payload(content="# Body\n\n[[Missing from draft body]]\n"))

    result = invoke_create_from_draft(vault, draft, "--apply", "--format", "json")

    assert result.exit_code == 1
    payload = json.loads(result.stdout)
    assert payload["status"] == CreateStatus.ROLLED_BACK.value
    assert payload["rollback"] == "succeeded"
    assert "CREATE_POST_WRITE_VALIDATION_FAILED" in result.stdout
    assert not (vault / "10 Projects" / "Reviewed draft.md").exists()
    assert not list((vault / "10 Projects").glob(".second-brain-*.tmp"))


def test_draft_cli_does_not_construct_network_or_git_adapters(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    vault = create_vault(tmp_path / "vault")
    install_templates(vault)
    draft = tmp_path / "draft.json"
    write_draft(draft, make_payload())

    def fail_constructor(*args: object, **kwargs: object) -> object:
        del args, kwargs
        pytest.fail("create-from-draft must not construct network/research/Git adapters")

    for name in (
        "CloudflareWorkersAiLlmPort",
        "JinaReaderWebAdapter",
        "PublicRssAdapter",
        "PublicYouTubeAdapter",
        "PublicGitHubAdapter",
        "GitVersionControlAdapter",
        "GitHubPullRequestAdapter",
    ):
        monkeypatch.setattr(f"second_brain.entrypoints.cli.app.{name}", fail_constructor)

    result = invoke_create_from_draft(vault, draft)

    assert result.exit_code == 0, result.stdout + result.stderr
    assert "dry-run" in result.stdout


def test_draft_cli_has_exact_local_option_surface() -> None:
    root_command: Any = get_command(app)
    note_command: Any = root_command.commands["note"]
    draft_command: Any = note_command.commands["create-from-draft"]
    option_names = {
        option_name
        for parameter in draft_command.params
        for option_name in getattr(parameter, "opts", ())
    }

    assert option_names == {"--file", "--apply", "--format"}
    file_parameter = next(
        parameter for parameter in draft_command.params if "--file" in parameter.opts
    )
    apply_parameter = next(
        parameter for parameter in draft_command.params if "--apply" in parameter.opts
    )
    assert file_parameter.required is True
    assert apply_parameter.default is False
    assert "--url" not in option_names
    assert "--instruction" not in option_names
    assert "--type" not in option_names


def test_service_rejects_unvalidated_draft_before_any_vault_scan_or_write() -> None:
    class FailReader:
        def scan(self) -> object:
            pytest.fail("invalid draft must be rejected before vault scan")

    class FailWriter:
        def prepare_from_draft(self, *args: object, **kwargs: object) -> object:
            pytest.fail("invalid draft must be rejected before plan")

    result = CreateManagedNoteFromDraft(
        FailReader(),  # type: ignore[arg-type]
        FailWriter(),  # type: ignore[arg-type]
    ).execute(
        CreateManagedNoteFromDraftRequest(
            NoteDraft("bad\nname", NoteType.PROJECT, "body"),
            apply=True,
        )
    )

    assert result.status is CreateStatus.REJECTED
    assert result.diagnostics[0].code == "DRAFT_SCHEMA_INVALID"
