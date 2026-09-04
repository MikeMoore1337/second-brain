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


class VaultReader(Protocol):
    """Read-only граница реализации vault."""

    def scan(self) -> VaultSnapshot:
        """Прочитать vault без его изменения и вернуть raw DTO."""


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
