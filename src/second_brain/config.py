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
    vault_operation_lock_path: str | None = None

    model_config = SettingsConfigDict(
        env_prefix="SECOND_BRAIN_",
        env_file=None,
        extra="ignore",
    )


class AppConfig:
    """Проверенная runtime-конфигурация read-only команд."""

    def __init__(
        self,
        vault_path: Path,
        config_root: Path | None,
        env_file: Path | None,
        vault_operation_lock_path: Path | None = None,
    ) -> None:
        self.vault_path = vault_path
        self.config_root = config_root
        self.env_file = env_file
        self.vault_operation_lock_path = vault_operation_lock_path


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
    lock_path = _resolve_operation_lock_path(
        settings.vault_operation_lock_path,
        config_root=config_root,
        vault_path=resolved,
    )
    return AppConfig(resolved, config_root, selected_env, lock_path)


def _resolve_operation_lock_path(
    raw_path: str | None,
    *,
    config_root: Path | None,
    vault_path: Path,
) -> Path | None:
    """Разрешить optional shared Safe Write/sync lock вне vault."""

    if raw_path is None or not raw_path.strip():
        return None
    configured = Path(raw_path)
    if configured.is_absolute():
        candidate = configured
    elif config_root is None:
        raise ConfigurationError(
            "relative SECOND_BRAIN_VAULT_OPERATION_LOCK_PATH requires an explicit env file"
        )
    else:
        candidate = config_root / configured
    if candidate.is_symlink():
        raise ConfigurationError("SECOND_BRAIN_VAULT_OPERATION_LOCK_PATH must not be a symlink")
    try:
        parent = candidate.parent.resolve(strict=True)
        resolved = parent / candidate.name
    except (OSError, RuntimeError, ValueError) as exc:
        raise ConfigurationError("vault operation lock path cannot be resolved") from exc
    if not parent.is_dir() or resolved == vault_path or resolved.is_relative_to(vault_path):
        raise ConfigurationError("SECOND_BRAIN_VAULT_OPERATION_LOCK_PATH must be outside the vault")
    return resolved
