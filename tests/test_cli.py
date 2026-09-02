"""Проверки CLI и его стабильных exit codes."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

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
