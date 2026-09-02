"""Проверки детерминированной конфигурации."""

from pathlib import Path

import pytest

from second_brain.config import ConfigurationError, load_config


def test_relative_vault_path_resolves_from_selected_env_parent(tmp_path: Path) -> None:
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    vault = tmp_path / "vault"
    vault.mkdir()
    env_file = config_dir / ".env"
    env_file.write_text("SECOND_BRAIN_VAULT_PATH=../vault\n", encoding="utf-8")

    config = load_config(env_file=env_file)

    assert config.vault_path == vault.resolve()
    assert config.config_root == config_dir.resolve()


def test_relative_path_without_config_root_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SECOND_BRAIN_VAULT_PATH", "../vault")

    with pytest.raises(ConfigurationError, match="requires an explicit env file/config root"):
        load_config()


def test_cli_override_keeps_selected_env_config_root(tmp_path: Path) -> None:
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    vault = config_dir / "chosen-vault"
    vault.mkdir()
    env_file = config_dir / ".env"
    env_file.write_text("SECOND_BRAIN_VAULT_PATH=wrong\n", encoding="utf-8")

    config = load_config(env_file=env_file, vault_path_override="chosen-vault")

    assert config.vault_path == vault.resolve()
