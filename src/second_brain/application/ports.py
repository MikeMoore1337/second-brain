"""Ports, используемые application use cases."""

from __future__ import annotations

from datetime import datetime
from typing import Protocol
from uuid import UUID

from second_brain.application.reports import VaultSnapshot
from second_brain.application.writes import CreateNotePlan, WriteReceipt
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
