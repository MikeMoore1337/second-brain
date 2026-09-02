"""Typer CLI для Foundation read-only use cases."""

from __future__ import annotations

import json
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Annotated

import typer

from second_brain.adapters.vault import FileSystemVaultReader
from second_brain.application.reports import ScanReport
from second_brain.application.services import DoctorVault, ValidateVault
from second_brain.config import ConfigurationError, load_config


class OutputFormat(StrEnum):
    """Поддерживаемые варианты отображения diagnostics."""

    TEXT = "text"
    JSON = "json"


@dataclass(frozen=True, slots=True)
class CliOptions:
    """Глобальные options, передаваемые вложенным Typer-командам."""

    env_file: Path | None
    vault_path: str | None


app = typer.Typer(
    no_args_is_help=True,
    help="Проверка Second Brain vault без изменения файлов.",
)
vault_app = typer.Typer(help="Команды для внешнего vault.")
app.add_typer(vault_app, name="vault")


@app.callback()
def callback(
    ctx: typer.Context,
    env_file: Annotated[
        Path | None,
        typer.Option("--env-file", help="Явно выбранный .env; его каталог становится config_root."),
    ] = None,
    vault_path: Annotated[
        str | None,
        typer.Option("--vault-path", help="Переопределить SECOND_BRAIN_VAULT_PATH."),
    ] = None,
) -> None:
    """Настроить источник конфигурации до выполнения команды."""

    ctx.obj = CliOptions(env_file=env_file, vault_path=vault_path)


@app.command()
def doctor(
    ctx: typer.Context,
    output_format: Annotated[
        OutputFormat,
        typer.Option("--format", help="Формат diagnostics: text или json."),
    ] = OutputFormat.TEXT,
) -> None:
    """Проверить конфигурацию и весь внешний vault."""

    _run(ctx, output_format, use_doctor=True)


@vault_app.command("validate")
def validate(
    ctx: typer.Context,
    output_format: Annotated[
        OutputFormat,
        typer.Option("--format", help="Формат diagnostics: text или json."),
    ] = OutputFormat.TEXT,
) -> None:
    """Провалидировать manifest, notes, links и attachments vault."""

    _run(ctx, output_format, use_doctor=False)


def _run(ctx: typer.Context, output_format: OutputFormat, *, use_doctor: bool) -> None:
    options = _root_options(ctx)
    try:
        config = load_config(env_file=options.env_file, vault_path_override=options.vault_path)
        reader = FileSystemVaultReader(config.vault_path)
        report = (DoctorVault(reader) if use_doctor else ValidateVault(reader)).execute()
    except ConfigurationError as exc:
        typer.echo(f"Ошибка конфигурации: {exc}", err=True)
        raise typer.Exit(code=2) from exc
    except OSError as exc:
        typer.echo(f"Ошибка выполнения: {exc}", err=True)
        raise typer.Exit(code=2) from exc

    if output_format is OutputFormat.JSON:
        typer.echo(json.dumps(report.as_dict(), ensure_ascii=False, indent=2))
    else:
        typer.echo(_render_text(report))
    raise typer.Exit(code=1 if report.error_count else 0)


def _root_options(ctx: typer.Context) -> CliOptions:
    current: object | None = ctx
    while current is not None:
        options = getattr(current, "obj", None)
        if isinstance(options, CliOptions):
            return options
        current = getattr(current, "parent", None)
    raise ConfigurationError("CLI options context is unavailable")


def _render_text(report: ScanReport) -> str:
    manifest_state = "загружен" if report.manifest is not None else "недоступен"
    lines = [
        f"Путь vault: {report.vault_path}",
        f"Manifest: {manifest_state}",
        f"Заметки: {len(report.notes)}",
        f"Wikilinks/embeds: {len(report.links)}",
        f"Вложения: {len(report.attachments)} ({report.attachment_bytes} bytes)",
        f"Ошибки: {report.error_count}; предупреждения: {report.warning_count}",
    ]
    if report.diagnostics:
        lines.append("Диагностика:")
        for diagnostic in report.diagnostics:
            location = diagnostic.path or "-"
            if diagnostic.line is not None:
                location = f"{location}:{diagnostic.line}"
            lines.append(
                f"  [{diagnostic.severity.value.upper()}] {diagnostic.code} {location} - "
                f"{diagnostic.message}"
            )
    result = "ОШИБКА" if report.error_count else "УСПЕХ"
    lines.append(f"Результат: {result}")
    return "\n".join(lines)


def main() -> None:
    """Запустить CLI entrypoint."""

    app()
