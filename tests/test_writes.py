"""Точечные проверки единственного write use case v1."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid7

import pytest
from typer.testing import CliRunner

from second_brain.adapters.vault import FileSystemVaultReader, FileSystemVaultWriter
from second_brain.adapters.vault.frontmatter import parse_front_matter
from second_brain.application.writes import CreateStatus
from second_brain.domain.models import NoteType, parse_uuid7
from second_brain.entrypoints.cli.app import app
from tests.conftest import create_vault, snapshot_tree, write_note

runner = CliRunner()


def install_templates(vault: Path, *, broken_project_link: bool = False) -> None:
    """Добавить в fixture vault четыре внешних template-файла."""

    templates = {
        "Project.md": "# Project template\n",
        "Area.md": "# Area template\n",
        "Resource.md": "# Resource template\n",
        "Zettel.md": "# Zettel template\n",
    }
    if broken_project_link:
        templates["Project.md"] = "# Project template\n\n[[Missing from template]]\n"
    for filename, content in templates.items():
        (vault / "_templates" / filename).write_text(content, encoding="utf-8")


def create_command(vault: Path, *arguments: str) -> Any:
    """Вызвать CLI create с абсолютным vault path."""

    return runner.invoke(app, ["--vault-path", str(vault), "note", "create", *arguments])


def test_create_is_dry_run_by_default_and_emits_template_diff(tmp_path: Path) -> None:
    vault = create_vault(tmp_path / "vault")
    install_templates(vault)
    before = snapshot_tree(vault)

    result = create_command(vault, "--type", "project", "--title", "Roadmap")

    assert result.exit_code == 0
    assert "dry-run" in result.stdout
    assert "Diff:" in result.stdout
    assert not (vault / "10 Projects" / "Roadmap.md").exists()
    assert snapshot_tree(vault) == before


def test_apply_creates_managed_note_from_external_template_and_validates_it(
    tmp_path: Path,
) -> None:
    vault = create_vault(tmp_path / "vault")
    install_templates(vault)

    result = create_command(
        vault, "--type", "project", "--title", "Roadmap", "--apply", "--format", "json"
    )

    assert result.exit_code == 0, result.stdout
    payload = json.loads(result.stdout)
    assert payload["status"] == "created"
    assert payload["mode"] == "apply"
    assert payload["applied"] is True
    assert payload["rollback"] == "not-needed"
    assert payload["post_write_validation"]["errors"] == 0

    target = vault / "10 Projects" / "Roadmap.md"
    parsed = parse_front_matter(target.read_text(encoding="utf-8"))
    assert parsed.data["type"] == "project"
    assert parse_uuid7(parsed.data["id"]).version == 7
    assert parsed.data["created"].tzinfo is not None
    assert parsed.body == "# Project template\n"
    assert not list((vault / "10 Projects").glob(".second-brain-*.tmp"))


def test_template_front_matter_fields_are_preserved_when_metadata_is_added(
    tmp_path: Path,
) -> None:
    vault = create_vault(tmp_path / "vault")
    install_templates(vault)
    (vault / "_templates" / "Project.md").write_text(
        "---\ncustom_field: retained\ntags: [template]\n---\n# Custom template\n",
        encoding="utf-8",
    )

    result = create_command(vault, "project", "Custom", "--apply")

    assert result.exit_code == 0
    parsed = parse_front_matter((vault / "10 Projects" / "Custom.md").read_text(encoding="utf-8"))
    assert parsed.data["custom_field"] == "retained"
    assert parsed.data["tags"] == ["template"]
    assert parsed.body == "# Custom template\n"


@pytest.mark.parametrize(
    ("note_type", "relative_root", "template_name"),
    [
        ("project", "10 Projects", "Project.md"),
        ("area", "20 Areas", "Area.md"),
        ("resource", "30 Resources", "Resource.md"),
        ("zettel", "40 Zettelkasten", "Zettel.md"),
    ],
)
def test_supported_types_select_their_declared_root_and_template(
    tmp_path: Path,
    note_type: str,
    relative_root: str,
    template_name: str,
) -> None:
    vault = create_vault(tmp_path / "vault")
    install_templates(vault)

    result = create_command(vault, "--type", note_type, "--title", "Entry")

    assert result.exit_code == 0
    assert f"Путь: {relative_root}/Entry.md" in result.stdout
    assert f"+# {template_name.removesuffix('.md')} template" in result.stdout


def test_existing_file_is_never_overwritten_and_temp_is_cleaned(tmp_path: Path) -> None:
    vault = create_vault(tmp_path / "vault")
    install_templates(vault)
    target = write_note(vault, "10 Projects/Existing.md", "user content\n")
    before = target.read_bytes()

    result = create_command(vault, "--type", "project", "--title", "Existing", "--apply")

    assert result.exit_code == 1
    assert "CREATE_TARGET_EXISTS" in result.stdout
    assert target.read_bytes() == before
    assert not list((vault / "10 Projects").glob(".second-brain-*.tmp"))


def test_unsafe_title_is_rejected_without_writing_outside_vault(tmp_path: Path) -> None:
    vault = create_vault(tmp_path / "vault")
    install_templates(vault)
    outside = tmp_path / "escape.md"
    before = snapshot_tree(vault)

    result = create_command(vault, "--type", "project", "--title", "../escape", "--apply")

    assert result.exit_code == 1
    assert "CREATE_INVALID_TITLE" in result.stdout
    assert not outside.exists()
    assert snapshot_tree(vault) == before


def test_linked_target_root_is_rejected_before_any_write(tmp_path: Path) -> None:
    vault = create_vault(tmp_path / "vault")
    install_templates(vault)
    outside = tmp_path / "outside"
    outside.mkdir()
    project_root = vault / "10 Projects"
    project_root.rmdir()
    try:
        project_root.symlink_to(outside, target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"symlink creation unavailable: {exc}")

    result = create_command(vault, "--type", "project", "--title", "Linked")

    assert result.exit_code == 1
    assert "CREATE_LINKED_PATH" in result.stdout
    assert not (outside / "Linked.md").exists()


def test_post_write_validation_failure_rolls_back_created_note(tmp_path: Path) -> None:
    vault = create_vault(tmp_path / "vault")
    install_templates(vault, broken_project_link=True)

    result = create_command(
        vault,
        "--type",
        "project",
        "--title",
        "Broken",
        "--apply",
        "--format",
        "json",
    )

    assert result.exit_code == 1
    payload = json.loads(result.stdout)
    assert "CREATE_POST_WRITE_VALIDATION_FAILED" in result.stdout
    assert payload["status"] == CreateStatus.ROLLED_BACK.value
    assert payload["rollback"] == "succeeded"
    assert not (vault / "10 Projects" / "Broken.md").exists()
    assert not list((vault / "10 Projects").glob(".second-brain-*.tmp"))


def test_rollback_refuses_to_remove_changed_file(tmp_path: Path) -> None:
    vault = create_vault(tmp_path / "vault")
    install_templates(vault)
    reader = FileSystemVaultReader(vault)
    report = reader.scan()
    assert report.manifest is not None
    writer = FileSystemVaultWriter(vault)
    plan = writer.prepare(
        report.manifest,
        NoteType.PROJECT,
        "Receipt",
        uuid7(),
        datetime.now(UTC),
    )
    receipt = writer.write(plan)
    target = vault / plan.relative_path
    target.write_text("changed by user\n", encoding="utf-8")

    assert writer.rollback(receipt) is False
    assert target.read_text(encoding="utf-8") == "changed by user\n"
