"""Ports, используемые application use cases."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from threading import Event
from typing import TYPE_CHECKING, Protocol
from uuid import UUID

from second_brain.application.reports import VaultSnapshot
from second_brain.application.writes import (
    CreateManagedNoteRequest,
    CreateManagedNoteResult,
    CreateNotePlan,
    WriteReceipt,
)
from second_brain.domain.models import NoteType, VaultManifest

if TYPE_CHECKING:
    from second_brain.application.llm import LlmRequest, NoteDraft
    from second_brain.application.research import ResearchRequest, ResearchSource
    from second_brain.application.research_draft import ReviewedResearchDraft
    from second_brain.application.transcription import Transcript, TranscriptionRequest


class VaultReader(Protocol):
    """Read-only граница реализации vault."""

    def scan(self) -> VaultSnapshot:
        """Прочитать vault без его изменения и вернуть raw DTO."""


@dataclass(frozen=True, slots=True)
class SearchDocument:
    """Provider-neutral projection searchable managed note."""

    note_id: UUID
    note_type: NoteType
    relative_path: str
    title: str
    body: str
    tags: tuple[str, ...]
    created: datetime
    updated: datetime | None = None


@dataclass(frozen=True, slots=True)
class SearchRequest:
    """Обычный bounded text query, не raw FTS5 expression."""

    query: str
    limit: int = 20


@dataclass(frozen=True, slots=True)
class SearchHit:
    """Ranked search result without full note body or index implementation data."""

    note_id: UUID
    note_type: NoteType
    relative_path: str
    title: str
    tags: tuple[str, ...]
    created: datetime
    updated: datetime | None
    snippet: str


@dataclass(frozen=True, slots=True)
class RetrievedNote:
    """Current canonical note read again from the vault by stable UUID."""

    note_id: UUID
    note_type: NoteType
    relative_path: str
    title: str
    body: str
    tags: tuple[str, ...]
    created: datetime
    updated: datetime | None


class SearchIndexPort(Protocol):
    """Provider-neutral rebuildable search index boundary."""

    def rebuild(self, documents: tuple[SearchDocument, ...]) -> None:
        """Replace derived index state with the supplied current projection."""

    def search(self, request: SearchRequest) -> tuple[SearchHit, ...]:
        """Return bounded ranked hits for one validated user query."""


class ManagedNoteWriter(Protocol):
    """Граница единственной filesystem write-операции v1."""

    def prepare(
        self,
        manifest: VaultManifest,
        note_type: NoteType,
        title: str,
        note_id: UUID,
        created: datetime,
    ) -> CreateNotePlan:
        """Подготовить план, не изменяя vault."""

    def prepare_from_draft(
        self,
        manifest: VaultManifest,
        draft: NoteDraft,
        note_id: UUID,
        created: datetime,
    ) -> CreateNotePlan:
        """Подготовить draft-based plan без изменения vault."""

    def prepare_from_reviewed_research_draft(
        self,
        manifest: VaultManifest,
        reviewed_draft: ReviewedResearchDraft,
        note_id: UUID,
        created: datetime,
    ) -> CreateNotePlan:
        """Подготовить research-derived plan без network и изменения vault."""

    def write(self, plan: CreateNotePlan) -> WriteReceipt:
        """Опубликовать ранее подготовленный план без overwrite."""

    def rollback(self, receipt: WriteReceipt) -> bool:
        """Удалить только подтверждённо созданный этим writer файл."""


class ManagedNoteCreationPort(Protocol):
    """Application boundary для переиспользования Safe Write use case."""

    def execute(self, request: CreateManagedNoteRequest) -> CreateManagedNoteResult:
        """Создать note в dry-run или apply режиме."""

    def rollback(self, receipt: WriteReceipt) -> bool:
        """Безопасно откатить receipt созданной note."""


class CancellationToken(Protocol):
    """Минимальный сигнал отмены для bounded внешней операции."""

    def is_cancelled(self) -> bool:
        """Вернуть, была ли операция отменена владельцем запроса."""


class TranscriptionPort(Protocol):
    """Единственная provider-neutral операция speech-to-text."""

    def transcribe(
        self,
        request: TranscriptionRequest,
        *,
        cancellation: CancellationToken,
    ) -> Transcript:
        """Распознать один bounded audio request без write/network authority."""


@dataclass(slots=True)
class CancellationTokenSource:
    """Потокобезопасный источник минимального cancellation token."""

    _event: Event = field(default_factory=Event)

    def cancel(self) -> None:
        """Установить cancellation signal, не выполняя cleanup или I/O."""

        self._event.set()

    def is_cancelled(self) -> bool:
        """Позволить gateway и будущему adapter наблюдать signal."""

        return self._event.is_set()

    @property
    def token(self) -> CancellationToken:
        """Вернуть этот же объект через read-only protocol boundary."""

        return self


class TranscriptionErrorCode(StrEnum):
    """Стабильные application-коды ошибок transcription boundary."""

    INVALID_REQUEST = "TRANSCRIPTION_INVALID_REQUEST"
    CANCELLED = "TRANSCRIPTION_CANCELLED"
    TIMEOUT = "TRANSCRIPTION_TIMEOUT"
    BACKEND_UNAVAILABLE = "TRANSCRIPTION_BACKEND_UNAVAILABLE"
    UPSTREAM_FAILURE = "TRANSCRIPTION_UPSTREAM_FAILURE"
    MALFORMED_RESULT = "TRANSCRIPTION_MALFORMED_RESULT"
    CONTENT_TOO_LARGE = "TRANSCRIPTION_CONTENT_TOO_LARGE"


_TRANSCRIPTION_ERROR_MESSAGES = {
    TranscriptionErrorCode.INVALID_REQUEST: "transcription request failed validation",
    TranscriptionErrorCode.CANCELLED: "transcription request was cancelled",
    TranscriptionErrorCode.TIMEOUT: "transcription backend timed out",
    TranscriptionErrorCode.BACKEND_UNAVAILABLE: "transcription backend is unavailable",
    TranscriptionErrorCode.UPSTREAM_FAILURE: "transcription backend failed",
    TranscriptionErrorCode.MALFORMED_RESULT: "transcription backend returned an invalid result",
    TranscriptionErrorCode.CONTENT_TOO_LARGE: "transcription content exceeds the request limit",
}


class TranscriptionError(RuntimeError):
    """Безопасная application error boundary без provider details."""

    def __init__(self, code: TranscriptionErrorCode | str, message: str | None = None) -> None:
        """Создать ошибку только с фиксированным сообщением taxonomy."""

        del message
        normalized = _normalize_transcription_error_code(code)
        self.code = normalized.value
        self.message = _TRANSCRIPTION_ERROR_MESSAGES[normalized]
        super().__init__(self.message)

    def as_dict(self) -> dict[str, str]:
        """Вернуть safe machine-readable представление без upstream details."""

        return {"code": self.code, "message": self.message}


class TranscriptionInvalidRequestError(TranscriptionError):
    """Запрос нарушает bounded audio/media policy."""

    def __init__(self, message: str | None = None) -> None:
        super().__init__(TranscriptionErrorCode.INVALID_REQUEST, message)


class TranscriptionCancelledError(TranscriptionError):
    """Операция отменена до или после вызова transcription port."""

    def __init__(self, message: str | None = None) -> None:
        super().__init__(TranscriptionErrorCode.CANCELLED, message)


class TranscriptionTimeoutError(TranscriptionError):
    """Adapter сообщил bounded operation timeout."""

    def __init__(self, message: str | None = None) -> None:
        super().__init__(TranscriptionErrorCode.TIMEOUT, message)


class TranscriptionBackendUnavailableError(TranscriptionError):
    """Backend или его runtime недоступен для speech-to-text."""

    def __init__(self, message: str | None = None) -> None:
        super().__init__(TranscriptionErrorCode.BACKEND_UNAVAILABLE, message)


class TranscriptionUpstreamError(TranscriptionError):
    """Upstream failure без раскрытия provider diagnostics."""

    def __init__(self, message: str | None = None) -> None:
        super().__init__(TranscriptionErrorCode.UPSTREAM_FAILURE, message)


class TranscriptionMalformedResultError(TranscriptionError):
    """Port вернул результат вне минимального Transcript contract."""

    def __init__(self, message: str | None = None) -> None:
        super().__init__(TranscriptionErrorCode.MALFORMED_RESULT, message)


class TranscriptionContentTooLargeError(TranscriptionError):
    """Audio или transcript превышает bounded byte limit."""

    def __init__(self, message: str | None = None) -> None:
        super().__init__(TranscriptionErrorCode.CONTENT_TOO_LARGE, message)


def _normalize_transcription_error_code(
    code: TranscriptionErrorCode | str,
) -> TranscriptionErrorCode:
    """Свести enum или неизвестную строку к закрытой transcription taxonomy."""

    if isinstance(code, TranscriptionErrorCode):
        return code
    try:
        return TranscriptionErrorCode(code)
    except TypeError, ValueError:
        return TranscriptionErrorCode.UPSTREAM_FAILURE


class ResearchErrorCode(StrEnum):
    """Стабильные application-коды ошибок research boundary."""

    INVALID_REQUEST = "RESEARCH_INVALID_REQUEST"
    CANCELLED = "RESEARCH_CANCELLED"
    TIMEOUT = "RESEARCH_TIMEOUT"
    BACKEND_UNAVAILABLE = "RESEARCH_BACKEND_UNAVAILABLE"
    UPSTREAM_FAILURE = "RESEARCH_UPSTREAM_FAILURE"
    MALFORMED_RESULT = "RESEARCH_MALFORMED_RESULT"
    CONTENT_TOO_LARGE = "RESEARCH_CONTENT_TOO_LARGE"


_RESEARCH_ERROR_MESSAGES = {
    ResearchErrorCode.INVALID_REQUEST: "research request failed validation",
    ResearchErrorCode.CANCELLED: "research request was cancelled",
    ResearchErrorCode.TIMEOUT: "research backend timed out",
    ResearchErrorCode.BACKEND_UNAVAILABLE: "research backend is unavailable",
    ResearchErrorCode.UPSTREAM_FAILURE: "research backend failed",
    ResearchErrorCode.MALFORMED_RESULT: "research backend returned an invalid result",
    ResearchErrorCode.CONTENT_TOO_LARGE: "research content exceeds the request limit",
}


class ResearchError(RuntimeError):
    """Безопасная application error boundary, независимая от upstream tools."""

    def __init__(self, code: ResearchErrorCode | str, message: str | None = None) -> None:
        """Создать error с кодом taxonomy и заранее безопасным сообщением."""

        normalized = _normalize_research_error_code(code)
        self.code = normalized.value
        self.message = message or _RESEARCH_ERROR_MESSAGES[normalized]
        super().__init__(self.message)

    def as_dict(self) -> dict[str, str]:
        """Вернуть безопасное машинное представление без upstream details."""

        return {"code": self.code, "message": self.message}


class ResearchInvalidRequestError(ResearchError):
    """Запрос нарушает application или public-target policy."""

    def __init__(self, message: str | None = None) -> None:
        super().__init__(ResearchErrorCode.INVALID_REQUEST, message)


class ResearchCancelledError(ResearchError):
    """Операция отменена до или после вызова внешнего порта."""

    def __init__(self, message: str | None = None) -> None:
        super().__init__(ResearchErrorCode.CANCELLED, message)


class ResearchTimeoutError(ResearchError):
    """Future adapter сообщил bounded operation timeout."""

    def __init__(self, message: str | None = None) -> None:
        super().__init__(ResearchErrorCode.TIMEOUT, message)


class ResearchBackendUnavailableError(ResearchError):
    """Backend или его runtime недоступен для выполнения read."""

    def __init__(self, message: str | None = None) -> None:
        super().__init__(ResearchErrorCode.BACKEND_UNAVAILABLE, message)


class ResearchUpstreamError(ResearchError):
    """Upstream read завершился ошибкой без раскрытия raw diagnostics."""

    def __init__(self, message: str | None = None) -> None:
        super().__init__(ResearchErrorCode.UPSTREAM_FAILURE, message)


class ResearchMalformedResultError(ResearchError):
    """Port вернул результат, не соответствующий normalized DTO contract."""

    def __init__(self, message: str | None = None) -> None:
        super().__init__(ResearchErrorCode.MALFORMED_RESULT, message)


class ResearchContentTooLargeError(ResearchError):
    """UTF-8 content результата превышает request max_bytes."""

    def __init__(self, message: str | None = None) -> None:
        super().__init__(ResearchErrorCode.CONTENT_TOO_LARGE, message)


class ExternalResearchPort(Protocol):
    """Read-only application boundary для будущего public research adapter."""

    def read(
        self,
        request: ResearchRequest,
        *,
        cancellation: CancellationToken,
    ) -> ResearchSource:
        """Прочитать один внешний источник и вернуть normalized DTO."""


class LlmErrorCode(StrEnum):
    """Стабильные application-коды ошибок LLM boundary."""

    INVALID_REQUEST = "LLM_INVALID_REQUEST"
    CANCELLED = "LLM_CANCELLED"
    TIMEOUT = "LLM_TIMEOUT"
    BACKEND_UNAVAILABLE = "LLM_BACKEND_UNAVAILABLE"
    UPSTREAM_FAILURE = "LLM_UPSTREAM_FAILURE"
    MALFORMED_RESULT = "LLM_MALFORMED_RESULT"
    CONTENT_TOO_LARGE = "LLM_CONTENT_TOO_LARGE"


_LLM_ERROR_MESSAGES = {
    LlmErrorCode.INVALID_REQUEST: "LLM request failed validation",
    LlmErrorCode.CANCELLED: "LLM request was cancelled",
    LlmErrorCode.TIMEOUT: "LLM backend timed out",
    LlmErrorCode.BACKEND_UNAVAILABLE: "LLM backend is unavailable",
    LlmErrorCode.UPSTREAM_FAILURE: "LLM backend failed",
    LlmErrorCode.MALFORMED_RESULT: "LLM backend returned an invalid note draft",
    LlmErrorCode.CONTENT_TOO_LARGE: "LLM note draft exceeds the request limit",
}


class LlmError(RuntimeError):
    """Безопасная application error boundary без provider details."""

    def __init__(self, code: LlmErrorCode | str, message: str | None = None) -> None:
        """Создать ошибку с фиксированным сообщением taxonomy."""

        del message
        normalized = _normalize_llm_error_code(code)
        self.code = normalized.value
        self.message = _LLM_ERROR_MESSAGES[normalized]
        super().__init__(self.message)

    def as_dict(self) -> dict[str, str]:
        """Вернуть безопасное машинное представление без upstream details."""

        return {"code": self.code, "message": self.message}


class LlmInvalidRequestError(LlmError):
    """Запрос не соответствует bounded application policy."""

    def __init__(self, message: str | None = None) -> None:
        super().__init__(LlmErrorCode.INVALID_REQUEST, message)


class LlmCancelledError(LlmError):
    """Операция отменена до или после вызова LLM port."""

    def __init__(self, message: str | None = None) -> None:
        super().__init__(LlmErrorCode.CANCELLED, message)


class LlmTimeoutError(LlmError):
    """Future provider adapter сообщил bounded operation timeout."""

    def __init__(self, message: str | None = None) -> None:
        super().__init__(LlmErrorCode.TIMEOUT, message)


class LlmBackendUnavailableError(LlmError):
    """Backend или его runtime недоступен для structured draft generation."""

    def __init__(self, message: str | None = None) -> None:
        super().__init__(LlmErrorCode.BACKEND_UNAVAILABLE, message)


class LlmUpstreamError(LlmError):
    """Upstream failure без раскрытия provider diagnostics."""

    def __init__(self, message: str | None = None) -> None:
        super().__init__(LlmErrorCode.UPSTREAM_FAILURE, message)


class LlmMalformedResultError(LlmError):
    """Port вернул результат вне typed NoteDraft contract."""

    def __init__(self, message: str | None = None) -> None:
        super().__init__(LlmErrorCode.MALFORMED_RESULT, message)


class LlmContentTooLargeError(LlmError):
    """Размер одного поля или combined NoteDraft превышает bounded limit."""

    def __init__(self, message: str | None = None) -> None:
        super().__init__(LlmErrorCode.CONTENT_TOO_LARGE, message)


class LlmPort(Protocol):
    """Единственная provider-neutral операция structured note draft generation."""

    def draft_note(
        self,
        request: LlmRequest,
        *,
        cancellation: CancellationToken,
    ) -> NoteDraft:
        """Сгенерировать один semantic draft без write/network authority."""


class SearchErrorCode(StrEnum):
    """Стабильные application-коды ошибок Search/Retrieval boundary."""

    INVALID_REQUEST = "SEARCH_INVALID_REQUEST"
    BACKEND_UNAVAILABLE = "SEARCH_BACKEND_UNAVAILABLE"
    INDEX_FAILED = "SEARCH_INDEX_FAILED"
    QUERY_FAILED = "SEARCH_QUERY_FAILED"
    NOT_FOUND = "SEARCH_NOT_FOUND"
    IDENTITY_CONFLICT = "SEARCH_IDENTITY_CONFLICT"
    CONTENT_TOO_LARGE = "SEARCH_CONTENT_TOO_LARGE"


_SEARCH_ERROR_MESSAGES = {
    SearchErrorCode.INVALID_REQUEST: "search request failed validation",
    SearchErrorCode.BACKEND_UNAVAILABLE: "search backend is unavailable",
    SearchErrorCode.INDEX_FAILED: "search index could not be rebuilt",
    SearchErrorCode.QUERY_FAILED: "search query could not be completed",
    SearchErrorCode.NOT_FOUND: "the requested note was not found",
    SearchErrorCode.IDENTITY_CONFLICT: "the requested note identity is conflicted",
    SearchErrorCode.CONTENT_TOO_LARGE: "search request is too large",
}


class SearchError(RuntimeError):
    """Безопасная Search/Retrieval error boundary без vault/SQLite details."""

    def __init__(self, code: SearchErrorCode | str, message: str | None = None) -> None:
        """Создать ошибку с фиксированным сообщением закрытой taxonomy."""

        del message
        normalized = _normalize_search_error_code(code)
        self.code = normalized.value
        self.message = _SEARCH_ERROR_MESSAGES[normalized]
        super().__init__(self.message)

    def as_dict(self) -> dict[str, str]:
        """Вернуть только стабильные safe fields."""

        return {"code": self.code, "message": self.message}


class SearchInvalidRequestError(SearchError):
    """Запрос не соответствует bounded literal-query policy."""

    def __init__(self, message: str | None = None) -> None:
        super().__init__(SearchErrorCode.INVALID_REQUEST, message)


class SearchBackendUnavailableError(SearchError):
    """Vault или локальный FTS5 runtime недоступен."""

    def __init__(self, message: str | None = None) -> None:
        super().__init__(SearchErrorCode.BACKEND_UNAVAILABLE, message)


class SearchIndexFailedError(SearchError):
    """Derived index не удалось атомарно пересоздать."""

    def __init__(self, message: str | None = None) -> None:
        super().__init__(SearchErrorCode.INDEX_FAILED, message)


class SearchQueryFailedError(SearchError):
    """FTS query не удалось безопасно выполнить."""

    def __init__(self, message: str | None = None) -> None:
        super().__init__(SearchErrorCode.QUERY_FAILED, message)


class SearchNotFoundError(SearchError):
    """Canonical note UUID отсутствует в текущем vault."""

    def __init__(self, message: str | None = None) -> None:
        super().__init__(SearchErrorCode.NOT_FOUND, message)


class SearchIdentityConflictError(SearchError):
    """Несколько searchable notes используют один stable UUID."""

    def __init__(self, message: str | None = None) -> None:
        super().__init__(SearchErrorCode.IDENTITY_CONFLICT, message)


class SearchContentTooLargeError(SearchError):
    """Raw JSON body превышает scoped Web search cap."""

    def __init__(self, message: str | None = None) -> None:
        super().__init__(SearchErrorCode.CONTENT_TOO_LARGE, message)


def _normalize_research_error_code(code: ResearchErrorCode | str) -> ResearchErrorCode:
    """Свести enum или известную строку к закрытой taxonomy."""

    if isinstance(code, ResearchErrorCode):
        return code
    try:
        return ResearchErrorCode(code)
    except ValueError:
        return ResearchErrorCode.UPSTREAM_FAILURE


def _normalize_llm_error_code(code: LlmErrorCode | str) -> LlmErrorCode:
    """Свести enum или известную строку к закрытой LLM taxonomy."""

    if isinstance(code, LlmErrorCode):
        return code
    try:
        return LlmErrorCode(code)
    except TypeError, ValueError:
        return LlmErrorCode.UPSTREAM_FAILURE


def _normalize_search_error_code(code: SearchErrorCode | str) -> SearchErrorCode:
    """Свести enum или неизвестную строку к закрытой Search taxonomy."""

    if isinstance(code, SearchErrorCode):
        return code
    try:
        return SearchErrorCode(code)
    except TypeError, ValueError:
        return SearchErrorCode.QUERY_FAILED


class ProposalPortError(RuntimeError):
    """Безопасная ошибка Git/PR adapter с машинным diagnostic code."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class VersionControlPort(Protocol):
    """Минимальная граница Git-операций proposal workflow."""

    def preflight(
        self,
        branch_name: str,
        *,
        require_synced_main: bool,
        check_remote_branch: bool,
    ) -> None:
        """Проверить состояние без изменения refs, index или worktree."""

    def fetch_main(self) -> None:
        """Явно обновить только origin/main перед apply."""

    def create_branch(self, branch_name: str) -> None:
        """Создать новую локальную branch от текущего main без overwrite."""

    def changed_paths(self) -> tuple[str, ...]:
        """Вернуть paths из текущего worktree/index status."""

    def stage_exact_path(self, relative_path: str) -> None:
        """Добавить в index только переданный path."""

    def staged_paths(self) -> tuple[str, ...]:
        """Вернуть paths staged diff."""

    def unstage_exact_path(self, relative_path: str) -> None:
        """Убрать из index только переданный path при безопасном cleanup."""

    def head_sha(self) -> str:
        """Вернуть SHA текущего commit."""

    def commit_exact_path(self, relative_path: str, message: str) -> str:
        """Создать commit только для переданного path и вернуть его SHA."""

    def push(self, branch_name: str) -> None:
        """Опубликовать branch обычным non-force push."""

    def switch_to_main(self) -> None:
        """Вернуться на main только при безопасном pre-commit cleanup."""

    def delete_local_branch(self, branch_name: str) -> None:
        """Удалить только пустую созданную workflow branch."""


class PullRequestPort(Protocol):
    """Минимальная граница GitHub PR publication."""

    def check_auth(self) -> None:
        """Проверить доступность и авторизацию `gh`."""

    def create(self, *, title: str, base: str, head: str, body: str) -> str:
        """Создать PR и вернуть его URL без approve/merge операций."""
