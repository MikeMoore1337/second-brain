"""Application DTO и отчёты диагностики vault."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from second_brain.domain.models import (
    AttachmentRecord,
    DecisionJournalRecord,
    LinkReference,
    MarkdownDocument,
    NoteRecord,
    OutcomeObservationRecord,
    VaultManifest,
)


class DiagnosticSeverity(StrEnum):
    """Уровень стабильной CLI- и JSON-diagnostic."""

    INFO = "info"
    WARNING = "warning"
    ERROR = "error"


@dataclass(frozen=True, slots=True)
class Diagnostic:
    """Детерминированная diagnostic, предназначенная пользователю."""

    code: str
    message: str
    severity: DiagnosticSeverity
    path: str | None = None
    line: int | None = None

    def as_dict(self) -> dict[str, Any]:
        """Вернуть JSON-совместимое представление diagnostic."""

        result: dict[str, Any] = {
            "code": self.code,
            "message": self.message,
            "severity": self.severity.value,
        }
        if self.path is not None:
            result["path"] = self.path
        if self.line is not None:
            result["line"] = self.line
        return result


@dataclass(frozen=True, slots=True)
class VaultSnapshot:
    """Raw read-only DTO с read/parsing diagnostics от adapter."""

    vault_path: str
    manifest: VaultManifest | None
    documents: tuple[MarkdownDocument, ...] = ()
    links: tuple[LinkReference, ...] = ()
    attachments: tuple[AttachmentRecord, ...] = ()
    diagnostics: tuple[Diagnostic, ...] = ()


@dataclass(frozen=True, slots=True)
class ScanReport:
    """Итоговый application report после проверки всех записей и связей."""

    vault_path: str
    manifest: VaultManifest | None
    notes: tuple[NoteRecord, ...] = ()
    links: tuple[LinkReference, ...] = ()
    attachments: tuple[AttachmentRecord, ...] = ()
    diagnostics: tuple[Diagnostic, ...] = ()

    @property
    def error_count(self) -> int:
        """Вернуть число error diagnostics."""

        return sum(item.severity is DiagnosticSeverity.ERROR for item in self.diagnostics)

    @property
    def warning_count(self) -> int:
        """Вернуть число warning diagnostics."""

        return sum(item.severity is DiagnosticSeverity.WARNING for item in self.diagnostics)

    @property
    def attachment_bytes(self) -> int:
        """Вернуть общий размер просканированных attachments."""

        return sum(item.size_bytes for item in self.attachments)

    @property
    def decision_journals(self) -> tuple[DecisionJournalRecord, ...]:
        """Вернуть valid typed Decision Journal projections текущего scan."""

        return tuple(
            note.decision_journal for note in self.notes if note.decision_journal is not None
        )

    @property
    def outcome_observations(self) -> tuple[OutcomeObservationRecord, ...]:
        """Вернуть valid typed Outcome Observation projections текущего scan."""

        return tuple(
            note.outcome_observation for note in self.notes if note.outcome_observation is not None
        )

    def as_dict(self) -> dict[str, Any]:
        """Вернуть JSON-совместимый application report."""

        manifest: dict[str, Any] | None = None
        if self.manifest is not None:
            manifest = {
                "schema_version": self.manifest.schema_version,
                "vault_id": str(self.manifest.vault_id),
                "default_language": self.manifest.default_language,
                "paths": self.manifest.paths.as_dict(),
                "attachments": {
                    "warning_size_bytes": self.manifest.attachments.warning_size_bytes,
                    "max_size_bytes": self.manifest.attachments.max_size_bytes,
                },
            }
        return {
            "vault_path": self.vault_path,
            "manifest": manifest,
            "notes": len(self.notes),
            "links": len(self.links),
            "attachments": len(self.attachments),
            "attachment_bytes": self.attachment_bytes,
            "errors": self.error_count,
            "warnings": self.warning_count,
            "diagnostics": [item.as_dict() for item in self.diagnostics],
        }
