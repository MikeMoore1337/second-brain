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


class VaultRootRole(StrEnum):
    """Declared vault-root role carried by scanner diagnostics."""

    INBOX = "inbox"
    PROJECTS = "projects"
    AREAS = "areas"
    RESOURCES = "resources"
    ZETTELKASTEN = "zettelkasten"
    ARCHIVE = "archive"
    TEMPLATES = "templates"
    ATTACHMENTS = "attachments"
    GLOBAL = "global"
    # Compatibility spelling for callers that describe manifest diagnostics
    # explicitly rather than using the shorter ``GLOBAL`` name.
    MANIFEST_OR_GLOBAL = "global"


CONTENT_ROOT_ROLES = frozenset(
    {
        VaultRootRole.INBOX,
        VaultRootRole.PROJECTS,
        VaultRootRole.AREAS,
        VaultRootRole.RESOURCES,
        VaultRootRole.ZETTELKASTEN,
        VaultRootRole.ARCHIVE,
    }
)
SUPPORT_ROOT_ROLES = frozenset({VaultRootRole.TEMPLATES, VaultRootRole.ATTACHMENTS})
_CONTENT_COMPLETENESS_CODES = frozenset(
    {
        "NOTE_READ_ERROR",
        "VAULT_DIRECTORY_READ_ERROR",
        "VAULT_ENTRY_RESOLVE_ERROR",
        "VAULT_LINKED_DIRECTORY",
        "VAULT_OVERLAPPING_ROOTS",
        "VAULT_PATH_ESCAPE",
        "VAULT_ROOT_MISSING",
        "VAULT_ROOT_NOT_DIRECTORY",
    }
)
_ATTACHMENT_COMPLETENESS_CODES = frozenset(
    {
        "ATTACHMENT_DIRECTORY_READ_ERROR",
        "ATTACHMENT_STAT_ERROR",
        "VAULT_ENTRY_RESOLVE_ERROR",
        "VAULT_LINKED_DIRECTORY",
        "VAULT_LINKED_ENTRY",
        "VAULT_OVERLAPPING_ROOTS",
        "VAULT_PATH_ESCAPE",
        "VAULT_ROOT_MISSING",
        "VAULT_ROOT_NOT_DIRECTORY",
    }
)


@dataclass(frozen=True, slots=True)
class Diagnostic:
    """Детерминированная diagnostic, предназначенная пользователю."""

    code: str
    message: str
    severity: DiagnosticSeverity
    path: str | None = None
    line: int | None = None
    root_role: VaultRootRole = VaultRootRole.GLOBAL
    involved_root_roles: tuple[VaultRootRole, ...] = ()

    def __post_init__(self) -> None:
        """Keep provenance explicit, typed, unique and bounded."""

        if type(self.root_role) is not VaultRootRole:
            raise TypeError("diagnostic root_role must be a VaultRootRole")
        if type(self.involved_root_roles) is not tuple:
            raise TypeError("diagnostic involved_root_roles must be a tuple")
        if any(type(role) is not VaultRootRole for role in self.involved_root_roles):
            raise TypeError("diagnostic involved_root_roles must contain VaultRootRole values")
        if len(self.involved_root_roles) > 2:
            raise ValueError("diagnostic involved_root_roles must contain at most two roles")
        if len(set(self.involved_root_roles)) != len(self.involved_root_roles):
            raise ValueError("diagnostic involved_root_roles must be unique")
        if VaultRootRole.GLOBAL in self.involved_root_roles:
            raise ValueError("global is not a declared involved root role")
        if self.involved_root_roles and self.root_role is not VaultRootRole.GLOBAL:
            raise ValueError("multi-root diagnostics must use global root_role")

    @property
    def originating_root_role(self) -> VaultRootRole:
        """Return the exact single origin or explicit global provenance."""

        return self.root_role

    @property
    def root_roles(self) -> tuple[VaultRootRole, ...]:
        """Return the bounded roles implicated by this diagnostic."""

        if self.involved_root_roles:
            return self.involved_root_roles
        if self.root_role is VaultRootRole.GLOBAL:
            return ()
        return (self.root_role,)

    def as_dict(self) -> dict[str, Any]:
        """Вернуть JSON-совместимое представление diagnostic."""

        result: dict[str, Any] = {
            "code": self.code,
            "message": self.message,
            "severity": self.severity.value,
            "root_role": self.root_role.value,
        }
        if self.path is not None:
            result["path"] = self.path
        if self.line is not None:
            result["line"] = self.line
        if self.involved_root_roles:
            result["involved_root_roles"] = [role.value for role in self.involved_root_roles]
        return result


def diagnostic_affects_content(diagnostic: Diagnostic) -> bool:
    """Classify a diagnostic by typed provenance, never by its path spelling."""

    if diagnostic.involved_root_roles:
        return bool(CONTENT_ROOT_ROLES.intersection(diagnostic.involved_root_roles))
    if diagnostic.root_role is VaultRootRole.GLOBAL:
        return True
    return diagnostic.root_role in CONTENT_ROOT_ROLES


def diagnostic_affects_attachments(diagnostic: Diagnostic) -> bool:
    """Classify attachment availability from the declared attachment role only."""

    if diagnostic.involved_root_roles:
        return VaultRootRole.ATTACHMENTS in diagnostic.involved_root_roles
    if diagnostic.root_role is VaultRootRole.GLOBAL:
        return True
    return diagnostic.root_role is VaultRootRole.ATTACHMENTS


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
    def content_scan_complete(self) -> bool:
        """Whether every declared content root was traversed safely."""

        return self.manifest is not None and not any(
            item.code in _CONTENT_COMPLETENESS_CODES and diagnostic_affects_content(item)
            for item in self.diagnostics
        )

    @property
    def attachments_scan_complete(self) -> bool:
        """Whether the declared attachment root was traversed safely."""

        return self.manifest is not None and not any(
            item.code in _ATTACHMENT_COMPLETENESS_CODES and diagnostic_affects_attachments(item)
            for item in self.diagnostics
        )

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
