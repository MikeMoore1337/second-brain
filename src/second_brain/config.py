"""Явная конфигурация окружения и детерминированное разрешение путей."""

from __future__ import annotations

from pathlib import Path

from pydantic import ValidationError
from pydantic_settings import BaseSettings, SettingsConfigDict


class ConfigurationError(ValueError):
    """Ошибка, когда конфигурация CLI не может получить безопасный путь vault."""


class EnvironmentSettings(BaseSettings):
    """Значения из process environment и явно выбранного env-файла."""

    vault_path: str | None = None

    model_config = SettingsConfigDict(
        env_prefix="SECOND_BRAIN_",
        env_file=None,
        extra="ignore",
    )


class AppConfig:
    """Проверенная runtime-конфигурация read-only команд."""

    def __init__(self, vault_path: Path, config_root: Path | None, env_file: Path | None) -> None:
        self.vault_path = vault_path
        self.config_root = config_root
        self.env_file = env_file


def load_config(
    *,
    env_file: Path | None = None,
    vault_path_override: str | None = None,
) -> AppConfig:
    """Загрузить настройки без неявного использования process cwd как базы vault."""

    selected_env: Path | None = None
    if env_file is not None:
        selected_env = env_file.resolve()
        if not selected_env.is_file():
            raise ConfigurationError(f"env file does not exist: {selected_env}")
    try:
        settings = EnvironmentSettings(_env_file=selected_env)  # type: ignore[call-arg]
    except ValidationError as exc:
        raise ConfigurationError(f"invalid environment configuration: {exc}") from exc

    raw_path = vault_path_override if vault_path_override is not None else settings.vault_path
    if raw_path is None or not raw_path.strip():
        raise ConfigurationError(
            "SECOND_BRAIN_VAULT_PATH is required; pass --vault-path or select an env file"
        )
    configured_path = Path(raw_path)
    config_root = selected_env.parent if selected_env is not None else None
    if configured_path.is_absolute():
        candidate = configured_path
    elif config_root is None:
        raise ConfigurationError(
            "relative SECOND_BRAIN_VAULT_PATH requires an explicit env file/config root; "
            "fallback to process cwd is disabled"
        )
    else:
        candidate = config_root / configured_path
    try:
        resolved = candidate.resolve(strict=True)
    except OSError as exc:
        raise ConfigurationError(f"vault path cannot be resolved: {candidate}: {exc}") from exc
    except ValueError:
        raise ConfigurationError("vault path cannot be resolved") from None
    if not resolved.is_dir():
        raise ConfigurationError(f"vault path is not a directory: {resolved}")
    return AppConfig(resolved, config_root, selected_env)
