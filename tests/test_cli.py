"""Проверки CLI и его стабильных exit codes."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from typer.testing import CliRunner

from second_brain.application.research import ResearchSource, SourceKind
from second_brain.entrypoints.cli.app import app
from tests.conftest import create_vault, managed_note, snapshot_tree, write_note

runner = CliRunner()


def test_doctor_supports_json_output_and_returns_success(tmp_path: Path) -> None:
    vault = create_vault(tmp_path / "vault")
    write_note(vault, "10 Projects/Note.md", managed_note())
    before = snapshot_tree(vault)
    env_file = tmp_path / "config" / ".env"
    env_file.parent.mkdir()
    env_file.write_text("SECOND_BRAIN_VAULT_PATH=../vault\n", encoding="utf-8")

    result = runner.invoke(app, ["--env-file", str(env_file), "doctor", "--format", "json"])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["errors"] == 0
    assert payload["notes"] == 1
    assert payload["manifest"]["schema_version"] == 1
    assert snapshot_tree(vault) == before


def test_validate_returns_one_for_validation_errors(tmp_path: Path) -> None:
    vault = create_vault(tmp_path / "vault")
    write_note(vault, "10 Projects/Note.md", "# Missing metadata\n")
    env_file = tmp_path / ".env"
    env_file.write_text(f"SECOND_BRAIN_VAULT_PATH={vault}\n", encoding="utf-8")

    result = runner.invoke(app, ["--env-file", str(env_file), "vault", "validate"])

    assert result.exit_code == 1
    assert "NOTE_MISSING_ID" in result.stdout


def test_cli_rejects_relative_process_environment_without_env_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Process environment path намеренно относительный и без config_root.
    assert tmp_path.exists()
    monkeypatch.setenv("SECOND_BRAIN_VAULT_PATH", "../vault")
    result = runner.invoke(app, ["doctor"])

    assert result.exit_code == 2
    assert "requires an explicit env file/config root" in result.stderr


def test_research_read_is_read_only_and_does_not_require_vault(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = ResearchSource(
        uri="https://example.com/article",
        source_kind=SourceKind.WEB,
        retrieved_at=datetime(2026, 9, 3, 12, 0, tzinfo=UTC),
        backend="jina-reader",
        content="Внешний текст без обработки.",
        media_type="text/markdown",
    )

    class FakeAdapter:
        def read(self, request: object, *, cancellation: object) -> ResearchSource:
            return source

    monkeypatch.setattr(
        "second_brain.entrypoints.cli.app.JinaReaderWebAdapter",
        lambda: FakeAdapter(),
    )

    result = runner.invoke(
        app,
        [
            "research",
            "read",
            "--type",
            "web",
            "--url",
            source.uri,
            "--format",
            "json",
        ],
    )

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["uri"] == source.uri
    assert payload["content"] == source.content
    assert payload["backend"] == "jina-reader"


def test_research_read_returns_one_for_research_validation_error_without_vault() -> None:
    result = runner.invoke(
        app,
        ["research", "read", "--type", "web", "--url", "http://localhost/article"],
    )

    assert result.exit_code == 1
    assert "RESEARCH_INVALID_REQUEST" in result.stderr
    assert "vault" not in result.stderr.casefold()


def test_research_read_supports_text_output_without_vault(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = ResearchSource(
        uri="https://example.com/article",
        source_kind=SourceKind.WEB,
        retrieved_at=datetime(2026, 9, 3, 12, 0, tzinfo=UTC),
        backend="jina-reader",
        content="Текст статьи.",
        media_type="text/markdown",
    )

    class FakeAdapter:
        def read(self, request: object, *, cancellation: object) -> ResearchSource:
            return source

    monkeypatch.setattr(
        "second_brain.entrypoints.cli.app.JinaReaderWebAdapter",
        lambda: FakeAdapter(),
    )

    result = runner.invoke(
        app,
        ["research", "read", "--url", source.uri, "--format", "text"],
    )

    assert result.exit_code == 0
    assert "Источник: https://example.com/article" in result.stdout
    assert "Content (untrusted external text):" in result.stdout
    assert source.content in result.stdout
