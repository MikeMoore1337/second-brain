"""Typer CLI для Foundation read-only use cases."""

from __future__ import annotations

import json
from dataclasses import dataclass
from difflib import unified_diff
from enum import StrEnum
from pathlib import Path
from typing import Annotated

import typer

from second_brain.adapters.vault import FileSystemVaultReader, FileSystemVaultWriter
from second_brain.application.reports import ScanReport
from second_brain.application.services import CreateManagedNote, DoctorVault, ValidateVault
from second_brain.application.writes import (
    CreateManagedNoteRequest,
    CreateManagedNoteResult,
    CreateStatus,
    WriteSafetyError,
)
from second_brain.config import ConfigurationError, load_config
from second_brain.domain.models import NoteType


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
    help="Проверка vault и безопасное создание managed note.",
)
vault_app = typer.Typer(help="Команды для внешнего vault.")
note_app = typer.Typer(help="Команды для managed note.")
app.add_typer(vault_app, name="vault")
app.add_typer(note_app, name="note")


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


@note_app.command("create")
def create(
    ctx: typer.Context,
    note_type_argument: Annotated[
        str | None,
        typer.Argument(help="Тип заметки: project, area, resource или zettel."),
    ] = None,
    title_argument: Annotated[
        str | None,
        typer.Argument(help="Безопасное имя создаваемого Markdown-файла."),
    ] = None,
    note_type_option: Annotated[
        str | None,
        typer.Option("--type", help="Тип заметки: project, area, resource или zettel."),
    ] = None,
    title_option: Annotated[
        str | None,
        typer.Option("--title", "--name", help="Безопасное имя создаваемого Markdown-файла."),
    ] = None,
    apply: Annotated[
        bool,
        typer.Option("--apply", help="Подтвердить реальную запись в vault."),
    ] = False,
    output_format: Annotated[
        OutputFormat,
        typer.Option("--format", help="Формат результата: text или json."),
    ] = OutputFormat.TEXT,
) -> None:
    """Подготовить или безопасно создать одну managed note."""

    try:
        note_type = _create_note_type(note_type_argument, note_type_option)
        title = _create_title(title_argument, title_option)
        options = _root_options(ctx)
        config = load_config(env_file=options.env_file, vault_path_override=options.vault_path)
        result = CreateManagedNote(
            FileSystemVaultReader(config.vault_path),
            FileSystemVaultWriter(config.vault_path),
        ).execute(CreateManagedNoteRequest(note_type, title, apply=apply))
    except (ConfigurationError, WriteSafetyError) as exc:
        typer.echo(f"Ошибка конфигурации записи: {exc}", err=True)
        raise typer.Exit(code=2) from exc
    except OSError as exc:
        typer.echo(f"Ошибка выполнения записи: {exc}", err=True)
        raise typer.Exit(code=2) from exc

    if output_format is OutputFormat.JSON:
        typer.echo(json.dumps(result.as_dict(), ensure_ascii=False, indent=2))
    else:
        typer.echo(_render_create_text(result))
    raise typer.Exit(code=0 if result.successful else 1)


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


def _create_note_type(argument: str | None, option: str | None) -> NoteType:
    value = _coalesce_argument(argument, option, "type")
    if value is None or not value.strip():
        raise typer.BadParameter("укажите тип через аргумент или --type")
    try:
        note_type = NoteType(value.strip().casefold())
    except ValueError as exc:
        raise typer.BadParameter(
            "тип должен быть одним из: project, area, resource, zettel"
        ) from exc
    if note_type is NoteType.NOTE:
        raise typer.BadParameter("тип note не входит в write scope v1")
    return note_type


def _create_title(argument: str | None, option: str | None) -> str:
    value = _coalesce_argument(argument, option, "title")
    if value is None or not value.strip():
        raise typer.BadParameter("укажите имя через аргумент или --title")
    return value


def _coalesce_argument(argument: str | None, option: str | None, name: str) -> str | None:
    if argument is not None and option is not None and argument != option:
        raise typer.BadParameter(f"нельзя одновременно задавать аргумент и --{name}")
    return option if option is not None else argument


def _render_create_text(result: CreateManagedNoteResult) -> str:
    lines = [
        f"Результат: {result.status.value}",
        "Режим: apply (--apply)"
        if result.apply_requested
        else "Режим: dry-run (по умолчанию; файл не изменён)",
    ]
    if result.plan is not None:
        lines.extend(
            [
                f"Тип: {result.plan.note_type.value}",
                f"Имя: {result.plan.title}",
                f"Путь: {result.plan.relative_path}",
                f"id: {result.plan.note_id}",
                f"created: {result.plan.created.isoformat(timespec='seconds')}",
            ]
        )
        if result.status is CreateStatus.DRY_RUN:
            lines.append("Diff:")
            lines.extend(
                unified_diff(
                    [],
                    result.plan.content.splitlines(),
                    fromfile="/dev/null",
                    tofile=result.plan.relative_path,
                    lineterm="",
                )
            )
    if result.validation_report is not None or result.status is CreateStatus.ROLLED_BACK:
        lines.append(
            "Post-write validation: "
            + ("OK" if result.status is CreateStatus.CREATED else "FAILED")
        )
    if result.rollback_succeeded is True:
        lines.append("Rollback: выполнен безопасно")
    elif result.rollback_succeeded is False:
        lines.append("Rollback: не выполнен; файл не удалён из-за несовпадения receipt")
    if result.diagnostics:
        lines.append("Диагностика:")
        for diagnostic in result.diagnostics:
            location = diagnostic.path or "-"
            lines.append(
                f"  [{diagnostic.severity.value.upper()}] {diagnostic.code} {location} - "
                f"{diagnostic.message}"
            )
    return "\n".join(lines)


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
