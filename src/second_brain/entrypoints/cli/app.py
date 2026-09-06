"""Typer CLI для Foundation read-only use cases."""

from __future__ import annotations

import json
from dataclasses import dataclass
from difflib import unified_diff
from enum import StrEnum
from pathlib import Path
from typing import Annotated

import typer
import uvicorn

from second_brain.adapters.git import GitVersionControlAdapter
from second_brain.adapters.github import GitHubPullRequestAdapter
from second_brain.adapters.llm.cloudflare_workers_ai import CloudflareWorkersAiLlmPort
from second_brain.adapters.research.github import PublicGitHubAdapter
from second_brain.adapters.research.jina_reader import JinaReaderWebAdapter
from second_brain.adapters.research.rss import PublicRssAdapter
from second_brain.adapters.research.youtube import PublicYouTubeAdapter
from second_brain.adapters.search import SqliteFts5SearchIndex
from second_brain.adapters.vault import FileSystemVaultReader, FileSystemVaultWriter
from second_brain.application.draft_files import DraftFileError, read_note_draft_file
from second_brain.application.llm import (
    DEFAULT_MAX_OUTPUT_BYTES,
    MAX_CONTEXT_BYTES,
    LlmGateway,
    LlmRequest,
    NoteDraft,
)
from second_brain.application.ports import (
    CancellationTokenSource,
    LlmError,
    LlmErrorCode,
    ProposalPortError,
    ResearchError,
    SearchBackendUnavailableError,
    SearchError,
    SearchHit,
)
from second_brain.application.proposals import (
    CreateNoteProposal,
    CreateNoteProposalRequest,
    CreateNoteProposalResult,
    ProposalStatus,
)
from second_brain.application.reports import ScanReport
from second_brain.application.research import (
    DEFAULT_MAX_BYTES,
    DEFAULT_TIMEOUT_SECONDS,
    ResearchGateway,
    ResearchInvalidRequestError,
    ResearchRequest,
    ResearchSource,
    SourceKind,
)
from second_brain.application.research_draft import ResearchDraftGateway, ResearchDraftRequest
from second_brain.application.search import DEFAULT_SEARCH_LIMIT, SearchRequest, SearchVault
from second_brain.application.self_retrieval import (
    DEFAULT_MAX_CONTENT_BYTES,
    DEFAULT_SELF_CONTEXT_LIMIT,
    SelfContextRequest,
    SelfRetrievalError,
    SelfRetrievalResultInvalidError,
    SelfRetrievalSearchUnavailableError,
    validate_self_context_request,
)
from second_brain.application.services import (
    CreateManagedNote,
    CreateManagedNoteFromDraft,
    DoctorVault,
    ValidateVault,
)
from second_brain.application.writes import (
    CreateManagedNoteFromDraftRequest,
    CreateManagedNoteRequest,
    CreateManagedNoteResult,
    CreateStatus,
    WriteSafetyError,
)
from second_brain.config import ConfigurationError, load_config
from second_brain.domain.models import NoteType
from second_brain.entrypoints.cli.self_retrieval import (
    build_production_self_retrieval_service,
    render_self_retrieval_text,
    self_retrieval_as_dict,
    self_retrieval_error_message,
)
from second_brain.entrypoints.web.app import create_app


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
    help="Read-only команды Second Brain и безопасное создание managed note.",
)
vault_app = typer.Typer(help="Команды для внешнего vault.")
note_app = typer.Typer(help="Команды для managed note.")
proposal_app = typer.Typer(help="Git proposal workflow для новой managed note.")
proposal_note_app = typer.Typer(help="Proposal-команды для managed note.")
research_app = typer.Typer(help="Read-only чтение внешних research sources.")
llm_app = typer.Typer(help="Networked read-only команды для LLM note drafts.")
web_app = typer.Typer(help="Локальный Web GUI shell.")
app.add_typer(vault_app, name="vault")
app.add_typer(note_app, name="note")
app.add_typer(proposal_app, name="proposal")
app.add_typer(research_app, name="research")
app.add_typer(llm_app, name="llm")
app.add_typer(web_app, name="web")
proposal_app.add_typer(proposal_note_app, name="note")

WEB_HOST = "127.0.0.1"
DEFAULT_WEB_PORT = 8123
MIN_WEB_PORT = 1
MAX_WEB_PORT = 65535


def _validate_web_port(value: int) -> int:
    """Проверить безопасный диапазон TCP-порта для local-only Web GUI."""

    if not MIN_WEB_PORT <= value <= MAX_WEB_PORT:
        raise typer.BadParameter(f"порт должен быть от {MIN_WEB_PORT} до {MAX_WEB_PORT}")
    return value


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


@web_app.command("serve")
def web_serve(
    ctx: typer.Context,
    port: Annotated[
        int,
        typer.Option(
            help=f"Loopback-порт Web GUI ({MIN_WEB_PORT}-{MAX_WEB_PORT}).",
            callback=_validate_web_port,
        ),
    ] = DEFAULT_WEB_PORT,
) -> None:
    """Запустить локальный Web GUI только на 127.0.0.1."""

    options = _root_options(ctx)
    application = create_app(
        env_file=options.env_file,
        vault_path_override=options.vault_path,
    )
    uvicorn.run(
        application,
        host=WEB_HOST,
        port=port,
    )


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


@app.command()
def search(
    ctx: typer.Context,
    query: Annotated[str, typer.Argument(help="Обычный текстовый запрос по заметкам.")],
    limit: Annotated[
        int,
        typer.Option("--limit", help="Максимальное число результатов (1-50)."),
    ] = DEFAULT_SEARCH_LIMIT,
    output_format: Annotated[
        OutputFormat,
        typer.Option("--format", help="Формат результатов: text или json."),
    ] = OutputFormat.TEXT,
) -> None:
    """Найти managed notes в текущем vault без записи, Git, LLM или сети."""

    try:
        options = _root_options(ctx)
        config = load_config(env_file=options.env_file, vault_path_override=options.vault_path)
        index = SqliteFts5SearchIndex()
        try:
            hits = SearchVault(FileSystemVaultReader(config.vault_path), index).execute(
                SearchRequest(query=query, limit=limit)
            )
        finally:
            index.close()
    except ConfigurationError:
        _echo_search_error(SearchBackendUnavailableError(), output_format)
        raise typer.Exit(code=2) from None
    except SearchError as exc:
        _echo_search_error(exc, output_format)
        raise typer.Exit(code=1) from None
    except OSError:
        _echo_search_error(SearchBackendUnavailableError(), output_format)
        raise typer.Exit(code=2) from None

    if output_format is OutputFormat.JSON:
        typer.echo(
            json.dumps(
                {"hits": [_search_hit_as_dict(hit) for hit in hits]}, ensure_ascii=False, indent=2
            )
        )
    else:
        typer.echo(_render_search_text(hits))
    raise typer.Exit(code=0)


@app.command()
def self_retrieval(
    ctx: typer.Context,
    query: Annotated[str, typer.Argument(help="Буквальный bounded запрос по текущим заметкам.")],
    limit: Annotated[
        int,
        typer.Option(help="Максимальное число кандидатов (1-50)."),
    ] = DEFAULT_SELF_CONTEXT_LIMIT,
    max_content_bytes: Annotated[
        int,
        typer.Option("--max-content-bytes", help="Лимит body/title/tags в UTF-8 bytes."),
    ] = DEFAULT_MAX_CONTENT_BYTES,
    output_format: Annotated[
        OutputFormat,
        typer.Option("--format", help="Формат результата: text или json."),
    ] = OutputFormat.TEXT,
) -> None:
    """Собрать bounded current Self Retrieval context без записи, сети или LLM."""

    request = SelfContextRequest(
        query=query,
        limit=limit,
        max_content_bytes=max_content_bytes,
    )
    try:
        validate_self_context_request(request)
    except SelfRetrievalError as exc:
        _echo_self_retrieval_error(exc, output_format)
        raise typer.Exit(code=2) from None

    try:
        options = _root_options(ctx)
        service = build_production_self_retrieval_service(
            env_file=options.env_file,
            vault_path_override=options.vault_path,
        )
        result = service.build(request)
        if output_format is OutputFormat.JSON:
            typer.echo(
                json.dumps(self_retrieval_as_dict(result, request), ensure_ascii=False, indent=2)
            )
        else:
            typer.echo(render_self_retrieval_text(result, request))
    except SelfRetrievalError as exc:
        _echo_self_retrieval_error(exc, output_format)
        raise typer.Exit(code=1) from None
    except ConfigurationError, OSError:
        _echo_self_retrieval_error(SelfRetrievalSearchUnavailableError(), output_format)
        raise typer.Exit(code=2) from None
    except Exception:
        _echo_self_retrieval_error(SelfRetrievalResultInvalidError(), output_format)
        raise typer.Exit(code=2) from None
    raise typer.Exit(code=0)


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


@research_app.command("read")
def research_read(
    source_type: Annotated[
        str,
        typer.Option("--type", help="Тип источника: web, rss, youtube или github."),
    ] = SourceKind.WEB.value,
    url: Annotated[
        str,
        typer.Option(
            "--url",
            help="Публичный URL web-страницы, RSS/Atom feed, YouTube video или GitHub repository.",
        ),
    ] = "",
    timeout: Annotated[
        int,
        typer.Option("--timeout", help="Лимит операции в секундах."),
    ] = DEFAULT_TIMEOUT_SECONDS,
    max_bytes: Annotated[
        int,
        typer.Option("--max-bytes", help="Жёсткий лимит UTF-8 content в bytes."),
    ] = DEFAULT_MAX_BYTES,
    output_format: Annotated[
        OutputFormat,
        typer.Option("--format", help="Формат результата: text или json."),
    ] = OutputFormat.TEXT,
) -> None:
    """Прочитать один публичный источник без vault и записи."""

    try:
        source_kind = _research_source_kind(source_type)
        source = ResearchGateway(_research_adapter(source_kind)).read(
            ResearchRequest(
                source_kind=source_kind,
                uri=url,
                timeout_seconds=timeout,
                max_bytes=max_bytes,
            ),
            cancellation=CancellationTokenSource(),
        )
    except ResearchError as exc:
        _echo_research_error(exc, output_format)
        raise typer.Exit(code=1) from None
    except OSError:
        # Runtime failure is intentionally separated from research taxonomy.
        typer.echo("Ошибка runtime CLI: не удалось запустить research backend.", err=True)
        raise typer.Exit(code=2) from None

    if output_format is OutputFormat.JSON:
        typer.echo(json.dumps(_research_source_as_dict(source), ensure_ascii=False, indent=2))
    else:
        typer.echo(_render_research_text(source))
    raise typer.Exit(code=0)


@research_app.command("draft")
def research_draft(
    instruction: Annotated[
        str,
        typer.Option("--instruction", help="Обязательная пользовательская инструкция для draft."),
    ],
    source_type: Annotated[
        str,
        typer.Option("--type", help="Тип источника: web, rss, youtube или github."),
    ] = SourceKind.WEB.value,
    url: Annotated[
        str,
        typer.Option(
            "--url",
            help="Публичный URL web-страницы, RSS/Atom feed, YouTube video или GitHub repository.",
        ),
    ] = "",
    timeout: Annotated[
        int,
        typer.Option("--timeout", help="Лимит research-операции в секундах."),
    ] = DEFAULT_TIMEOUT_SECONDS,
    max_source_bytes: Annotated[
        int,
        typer.Option("--max-source-bytes", help="Жёсткий лимит source content в bytes."),
    ] = MAX_CONTEXT_BYTES,
    max_output_bytes: Annotated[
        int,
        typer.Option("--max-output-bytes", help="Максимальный размер NoteDraft в bytes."),
    ] = DEFAULT_MAX_OUTPUT_BYTES,
    output_format: Annotated[
        OutputFormat,
        typer.Option("--format", help="Формат результата: text или json."),
    ] = OutputFormat.TEXT,
) -> None:
    """Networked read-only: one research read + at most one LLM draft; без vault/Git writes."""

    try:
        source_kind = _research_source_kind(source_type)
        research_result = ResearchDraftGateway(
            ResearchGateway(_research_adapter(source_kind)),
            LlmGateway(CloudflareWorkersAiLlmPort()),
        ).draft_note(
            ResearchDraftRequest(
                source_kind=source_kind,
                uri=url,
                instruction=instruction,
                research_timeout_seconds=timeout,
                max_source_bytes=max_source_bytes,
                max_output_bytes=max_output_bytes,
            ),
            cancellation=CancellationTokenSource(),
        )
    except ResearchError as exc:
        _echo_research_error(exc, output_format)
        raise typer.Exit(code=1) from None
    except LlmError as exc:
        _echo_llm_error(exc, output_format)
        raise typer.Exit(code=1) from None
    except Exception:
        _echo_research_draft_runtime_error(output_format)
        raise typer.Exit(code=2) from None

    draft = research_result.draft
    if output_format is OutputFormat.JSON:
        typer.echo(json.dumps(_llm_draft_as_dict(draft), ensure_ascii=False, indent=2))
    else:
        typer.echo(_render_llm_text(draft))
    raise typer.Exit(code=0)


@llm_app.command("draft")
def llm_draft(
    instruction: Annotated[
        str,
        typer.Option("--instruction", help="Семантическая инструкция для draft generation."),
    ],
    context: Annotated[
        str,
        typer.Option("--context", help="Необязательный контекст для draft generation."),
    ] = "",
    max_output_bytes: Annotated[
        int,
        typer.Option("--max-output-bytes", help="Максимальный размер NoteDraft в bytes."),
    ] = DEFAULT_MAX_OUTPUT_BYTES,
    output_format: Annotated[
        OutputFormat,
        typer.Option("--format", help="Формат результата: text или json."),
    ] = OutputFormat.TEXT,
) -> None:
    """Выполнить одну networked read-only LLM draft operation без записи."""

    try:
        draft = LlmGateway(CloudflareWorkersAiLlmPort()).draft_note(
            LlmRequest(
                instruction=instruction,
                context=context,
                max_output_bytes=max_output_bytes,
            ),
            cancellation=CancellationTokenSource(),
        )
    except LlmError as exc:
        _echo_llm_error(exc, output_format)
        raise typer.Exit(code=1) from None
    except Exception:
        _echo_llm_runtime_error(output_format)
        raise typer.Exit(code=2) from None

    if output_format is OutputFormat.JSON:
        typer.echo(json.dumps(_llm_draft_as_dict(draft), ensure_ascii=False, indent=2))
    else:
        typer.echo(_render_llm_text(draft))
    raise typer.Exit(code=0)


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


@note_app.command("create-from-draft")
def create_from_draft(
    ctx: typer.Context,
    draft_file: Annotated[
        Path,
        typer.Option("--file", help="UTF-8 JSON-файл с одним reviewed NoteDraft."),
    ],
    apply: Annotated[
        bool,
        typer.Option("--apply", help="Подтвердить реальную запись в vault."),
    ] = False,
    output_format: Annotated[
        OutputFormat,
        typer.Option("--format", help="Формат результата: text или json."),
    ] = OutputFormat.TEXT,
) -> None:
    """Offline Safe Write из reviewed NoteDraft; dry-run по умолчанию."""

    try:
        draft = read_note_draft_file(draft_file)
    except DraftFileError as exc:
        _echo_draft_error(exc, output_format)
        raise typer.Exit(code=1) from None

    try:
        options = _root_options(ctx)
        config = load_config(env_file=options.env_file, vault_path_override=options.vault_path)
        result = CreateManagedNoteFromDraft(
            FileSystemVaultReader(config.vault_path),
            FileSystemVaultWriter(config.vault_path),
        ).execute(CreateManagedNoteFromDraftRequest(draft, apply=apply))
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


@proposal_note_app.command("create")
def proposal_create(
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
        typer.Option("--apply", help="Создать branch, note, commit, push и PR."),
    ] = False,
    output_format: Annotated[
        OutputFormat,
        typer.Option("--format", help="Формат результата: text или json."),
    ] = OutputFormat.TEXT,
) -> None:
    """Предложить создание одной managed note через automation/* и PR."""

    try:
        note_type = _create_note_type(note_type_argument, note_type_option)
        title = _create_title(title_argument, title_option)
        options = _root_options(ctx)
        config = load_config(env_file=options.env_file, vault_path_override=options.vault_path)
        note_creator = CreateManagedNote(
            FileSystemVaultReader(config.vault_path),
            FileSystemVaultWriter(config.vault_path),
        )
        result = CreateNoteProposal(
            note_creator,
            GitVersionControlAdapter(config.vault_path),
            GitHubPullRequestAdapter(config.vault_path),
        ).execute(CreateNoteProposalRequest(note_type, title, apply=apply))
    except (ConfigurationError, WriteSafetyError, ProposalPortError) as exc:
        typer.echo(f"Ошибка конфигурации proposal: {exc}", err=True)
        raise typer.Exit(code=2) from exc
    except OSError as exc:
        typer.echo(f"Ошибка выполнения proposal: {exc}", err=True)
        raise typer.Exit(code=2) from exc

    if output_format is OutputFormat.JSON:
        typer.echo(json.dumps(result.as_dict(), ensure_ascii=False, indent=2))
    else:
        typer.echo(_render_proposal_text(result))
    raise typer.Exit(code=0 if result.successful else 1)


def _research_source_kind(value: str) -> SourceKind:
    """Разобрать закрытый CLI source kind и не включать unsupported adapters."""

    try:
        source_kind = SourceKind(value.strip().casefold())
    except ValueError:
        raise ResearchInvalidRequestError() from None
    if source_kind not in {
        SourceKind.WEB,
        SourceKind.RSS,
        SourceKind.YOUTUBE,
        SourceKind.GITHUB,
    }:
        raise ResearchInvalidRequestError()
    return source_kind


def _research_adapter(
    source_kind: SourceKind,
) -> JinaReaderWebAdapter | PublicRssAdapter | PublicYouTubeAdapter | PublicGitHubAdapter:
    """Явно сопоставить поддержанные CLI-типы с production adapters."""

    if source_kind is SourceKind.WEB:
        return JinaReaderWebAdapter()
    if source_kind is SourceKind.RSS:
        return PublicRssAdapter()
    if source_kind is SourceKind.YOUTUBE:
        return PublicYouTubeAdapter()
    if source_kind is SourceKind.GITHUB:
        return PublicGitHubAdapter()
    raise ResearchInvalidRequestError()


def _echo_research_error(error: ResearchError, output_format: OutputFormat) -> None:
    """Вывести безопасную русскую диагностику без upstream stderr/details."""

    message = _research_error_message(error.code)
    if output_format is OutputFormat.JSON:
        typer.echo(
            json.dumps(
                {"error": {"code": error.code, "message": message}},
                ensure_ascii=False,
                indent=2,
            ),
            err=True,
        )
    else:
        typer.echo(f"Ошибка research: {error.code} — {message}", err=True)


def _echo_search_error(error: SearchError, output_format: OutputFormat) -> None:
    """Вывести safe Search diagnostic без absolute path или SQLite details."""

    code = error.code if error.code in _SEARCH_ERROR_MESSAGES else "SEARCH_QUERY_FAILED"
    message = _search_error_message(code)
    if output_format is OutputFormat.JSON:
        typer.echo(
            json.dumps(
                {"error": {"code": code, "message": message}},
                ensure_ascii=False,
                indent=2,
            ),
            err=True,
        )
    else:
        typer.echo(f"Ошибка поиска: {code} — {message}", err=True)


def _echo_self_retrieval_error(
    error: SelfRetrievalError,
    output_format: OutputFormat,
) -> None:
    """Вывести закрытую safe Self Retrieval taxonomy без backend details."""

    code, message = self_retrieval_error_message(error)
    if output_format is OutputFormat.JSON:
        typer.echo(
            json.dumps(
                {"error": {"code": code, "message": message}},
                ensure_ascii=False,
                indent=2,
            ),
            err=True,
        )
    else:
        typer.echo(f"Ошибка self retrieval: {code} — {message}", err=True)


_SEARCH_ERROR_MESSAGES = {
    "SEARCH_INVALID_REQUEST": "запрос не прошёл проверку",
    "SEARCH_CONTENT_TOO_LARGE": "запрос слишком большой",
    "SEARCH_BACKEND_UNAVAILABLE": "vault или локальный search backend недоступен",
    "SEARCH_INDEX_FAILED": "не удалось пересобрать локальный search index",
    "SEARCH_QUERY_FAILED": "не удалось выполнить search query",
    "SEARCH_NOT_FOUND": "заметка не найдена в текущем vault",
    "SEARCH_IDENTITY_CONFLICT": "identity заметки конфликтует в текущем vault",
}


def _search_error_message(code: str) -> str:
    """Вернуть короткое русское сообщение закрытой Search taxonomy."""

    return _SEARCH_ERROR_MESSAGES.get(code, "операция поиска завершилась ошибкой")


def _search_hit_as_dict(hit: SearchHit) -> dict[str, object]:
    """Сериализовать только stable metadata и bounded snippet."""

    return {
        "id": str(hit.note_id),
        "type": hit.note_type.value,
        "title": hit.title,
        "relative_path": hit.relative_path,
        "tags": list(hit.tags),
        "created": hit.created.isoformat(),
        "updated": hit.updated.isoformat() if hit.updated is not None else None,
        "snippet": hit.snippet,
    }


def _render_search_text(hits: tuple[SearchHit, ...]) -> str:
    """Показать ranked SearchHit без full body и absolute vault path."""

    if not hits:
        return "Ничего не найдено."
    blocks: list[str] = []
    for hit in hits:
        blocks.append(
            "\n".join(
                [
                    f"Title: {hit.title}",
                    f"Type: {hit.note_type.value}",
                    f"Path: {hit.relative_path}",
                    f"Tags: {', '.join(hit.tags) or '-'}",
                    f"Snippet: {hit.snippet or '-'}",
                ]
            )
        )
    return "\n\n".join(blocks)


def _echo_llm_error(error: LlmError, output_format: OutputFormat) -> None:
    """Вывести безопасную русскую LLM-диагностику без provider details."""

    code = (
        error.code
        if isinstance(error.code, str) and error.code in _LLM_ERROR_MESSAGES
        else (LlmErrorCode.UPSTREAM_FAILURE.value)
    )
    message = _llm_error_message(code)
    if output_format is OutputFormat.JSON:
        typer.echo(
            json.dumps(
                {"error": {"code": code, "message": message}},
                ensure_ascii=False,
                indent=2,
            ),
            err=True,
        )
    else:
        typer.echo(f"Ошибка LLM: {code} — {message}", err=True)


def _echo_draft_error(error: DraftFileError, output_format: OutputFormat) -> None:
    """Вывести безопасную диагностику strict draft decoder без raw content."""

    code = error.code
    message = error.message
    if output_format is OutputFormat.JSON:
        typer.echo(
            json.dumps(
                {"error": {"code": code, "message": message}},
                ensure_ascii=False,
                indent=2,
            ),
            err=True,
        )
    else:
        typer.echo(f"Ошибка draft: {code} — {message}", err=True)


def _llm_error_message(code: str) -> str:
    """Сопоставить закрытый LLM application code с русским safe diagnostic."""

    return _LLM_ERROR_MESSAGES.get(code, "операция LLM завершилась ошибкой")


def _echo_llm_runtime_error(output_format: OutputFormat) -> None:
    """Скрыть неожиданную локальную ошибку и не печатать exception details."""

    del output_format
    typer.echo("Ошибка runtime CLI: не удалось выполнить LLM draft.", err=True)


def _echo_research_draft_runtime_error(output_format: OutputFormat) -> None:
    """Скрыть неожиданную локальную ошибку composition и provider details."""

    del output_format
    typer.echo("Ошибка runtime CLI: не удалось выполнить research draft.", err=True)


def _llm_draft_as_dict(draft: NoteDraft) -> dict[str, object]:
    """Сериализовать только пять semantic полей NoteDraft."""

    return {
        "title": draft.title,
        "note_type": draft.note_type.value,
        "content": draft.content,
        "tags": list(draft.tags),
        "links": list(draft.links),
    }


def _render_llm_text(draft: NoteDraft) -> str:
    """Показать NoteDraft без нормализации или дополнения generated content."""

    return "\n".join(
        [
            f"Title: {draft.title}",
            f"Type: {draft.note_type.value}",
            f"Tags: {', '.join(draft.tags) or '-'}",
            f"Links: {', '.join(draft.links) or '-'}",
            "Content:",
            draft.content,
        ]
    )


_LLM_ERROR_MESSAGES = {
    LlmErrorCode.INVALID_REQUEST.value: "запрос не прошёл проверку",
    LlmErrorCode.CANCELLED.value: "операция отменена",
    LlmErrorCode.TIMEOUT.value: "LLM backend превысил лимит времени",
    LlmErrorCode.BACKEND_UNAVAILABLE.value: "LLM backend недоступен",
    LlmErrorCode.UPSTREAM_FAILURE.value: "LLM backend вернул ошибку",
    LlmErrorCode.MALFORMED_RESULT.value: "LLM backend вернул некорректный NoteDraft",
    LlmErrorCode.CONTENT_TOO_LARGE.value: "NoteDraft превышает заданный лимит размера",
}


def _research_error_message(code: str) -> str:
    """Сопоставить закрытый application code с коротким human diagnostic."""

    messages = {
        "RESEARCH_INVALID_REQUEST": (
            "запрос не прошёл проверку публичного web/RSS/YouTube/GitHub-источника"
        ),
        "RESEARCH_CANCELLED": "чтение отменено",
        "RESEARCH_TIMEOUT": "research backend превысил лимит времени",
        "RESEARCH_BACKEND_UNAVAILABLE": (
            "research backend недоступен; проверьте системный curl или yt-dlp"
        ),
        "RESEARCH_UPSTREAM_FAILURE": "research backend вернул ошибку",
        "RESEARCH_MALFORMED_RESULT": "research backend вернул некорректный результат",
        "RESEARCH_CONTENT_TOO_LARGE": "ответ превышает заданный лимит размера",
    }
    return messages.get(code, "операция research завершилась ошибкой")


def _research_source_as_dict(source: ResearchSource) -> dict[str, object]:
    """Сериализовать normalized source без интерпретации external content."""

    return {
        "uri": source.uri,
        "source_kind": source.source_kind.value,
        "retrieved_at": source.retrieved_at.isoformat(),
        "backend": source.backend,
        "content": source.content,
        "title": source.title,
        "author": source.author,
        "media_type": source.media_type,
        "upstream_id": source.upstream_id,
        "published_at": source.published_at.isoformat() if source.published_at else None,
    }


def _render_research_text(source: ResearchSource) -> str:
    """Показать metadata и исходный untrusted content без последующей обработки."""

    return "\n".join(
        [
            f"Источник: {source.uri}",
            f"Тип: {source.source_kind.value}",
            f"Backend: {source.backend}",
            f"Получено: {source.retrieved_at.isoformat()}",
            f"Media type: {source.media_type or '-'}",
            "Content (untrusted external text):",
            source.content,
        ]
    )


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


def _render_proposal_text(result: CreateNoteProposalResult) -> str:
    """Показать план и recovery state proposal на русском языке."""

    lines = [
        f"Результат: {result.status.value}",
        "Режим: apply (--apply)"
        if result.apply_requested
        else "Режим: dry-run (по умолчанию; Git и vault не изменены)",
        f"Automation branch: {result.branch}",
        f"Commit message: {result.commit_message}",
        f"PR: {result.pr_base} <- {result.pr_head}",
        f"PR title: {result.pr_title}",
    ]
    if result.note is not None:
        lines.extend(
            [
                f"Тип: {result.note.note_type.value}",
                f"Имя: {result.note.title}",
                f"Путь: {result.note.relative_path}",
                f"id: {result.note.note_id}",
                f"created: {result.note.created.isoformat(timespec='seconds')}",
            ]
        )
        if result.status is ProposalStatus.DRY_RUN:
            lines.append("Diff:")
            lines.extend(
                unified_diff(
                    [],
                    result.note.content.splitlines(),
                    fromfile="/dev/null",
                    tofile=result.note.relative_path,
                    lineterm="",
                )
            )
    if result.commit_sha is not None:
        lines.append(f"Head commit: {result.commit_sha}")
    if result.pr_url is not None:
        lines.append(f"PR URL: {result.pr_url}")
    elif result.remote_branch_pushed:
        lines.append("PR URL: не создан; remote branch и commit сохранены для recovery")
    if result.rollback_succeeded is True:
        lines.append("Rollback note: выполнен безопасно")
    elif result.rollback_succeeded is False:
        lines.append("Rollback note: не выполнен; branch сохранена")
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
