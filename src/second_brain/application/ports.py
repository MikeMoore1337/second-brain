"""Ports, используемые application use cases."""

from __future__ import annotations

from datetime import datetime
from typing import Protocol
from uuid import UUID

from second_brain.application.reports import VaultSnapshot
from second_brain.application.writes import (
    CreateManagedNoteRequest,
    CreateManagedNoteResult,
    CreateNotePlan,
    WriteReceipt,
)
from second_brain.domain.models import NoteType, VaultManifest


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
