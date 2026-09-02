"""DTO и результаты безопасного создания managed note."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Any
from uuid import UUID

from second_brain.application.reports import Diagnostic, ScanReport
from second_brain.domain.models import NoteType


class CreateStatus(StrEnum):
    """Состояние одного запуска use case создания заметки."""

    DRY_RUN = "dry-run"
    CREATED = "created"
    REJECTED = "rejected"
    ROLLED_BACK = "rolled-back"


class WriteSafetyError(ValueError):
    """Операция записи отклонена защитным precondition."""

    def __init__(self, code: str, message: str, path: str | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.path = path


@dataclass(frozen=True, slots=True)
class CreateManagedNoteRequest:
    """Входные данные единственного write use case v1."""

    note_type: NoteType
    title: str
    apply: bool = False
    now: datetime | None = None


@dataclass(frozen=True, slots=True)
class CreateNotePlan:
    """Полностью подготовленный, но ещё не опубликованный note."""

    note_type: NoteType
    title: str
    note_id: UUID
    created: datetime
    relative_path: str
    content: str
    target_root_relative: str = ""


@dataclass(frozen=True, slots=True)
class WriteReceipt:
    """Доказательства, необходимые для безопасного rollback созданного файла."""

    target_path: str
    relative_path: str
    content_sha256: str
    file_identity: tuple[int, int]


@dataclass(frozen=True, slots=True)
class CreateManagedNoteResult:
    """Итог dry-run/apply, пригодный для text и JSON CLI."""

    status: CreateStatus
    plan: CreateNotePlan | None = None
    diagnostics: tuple[Diagnostic, ...] = ()
    validation_report: ScanReport | None = None
    rollback_succeeded: bool | None = None
    apply_requested: bool = False
    receipt: WriteReceipt | None = None

    @property
    def applied(self) -> bool:
        """Показать, был ли запрошен реальный apply."""

        return self.status in {CreateStatus.CREATED, CreateStatus.ROLLED_BACK}

    @property
    def successful(self) -> bool:
        """Показать, завершился ли запуск приемлемым результатом."""

        return self.status in {CreateStatus.DRY_RUN, CreateStatus.CREATED}

    def as_dict(self) -> dict[str, Any]:
        """Вернуть стабильное JSON-представление результата."""

        plan: dict[str, Any] | None = None
        if self.plan is not None:
            plan = {
                "type": self.plan.note_type.value,
                "title": self.plan.title,
                "id": str(self.plan.note_id),
                "created": self.plan.created.isoformat(timespec="seconds"),
                "relative_path": self.plan.relative_path,
                "content": self.plan.content,
            }
        result: dict[str, Any] = {
            "status": self.status.value,
            "mode": "apply" if self.apply_requested else "dry-run",
            "applied": self.applied,
            "plan": plan,
            "diagnostics": [item.as_dict() for item in self.diagnostics],
            "rollback": (
                "succeeded"
                if self.rollback_succeeded is True
                else "failed"
                if self.rollback_succeeded is False
                else "not-needed"
            ),
        }
        if self.validation_report is not None:
            result["post_write_validation"] = self.validation_report.as_dict()
        return result
