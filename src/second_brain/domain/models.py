"""Domain-значения для read-only use cases проверки vault."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime
from enum import StrEnum
from pathlib import PurePosixPath
from typing import Any
from uuid import UUID

_RFC3339_PATTERN = re.compile(
    r"\d{4}-\d{2}-\d{2}[Tt]\d{2}:\d{2}:\d{2}"
    r"(?:\.\d+)?(?:[Zz]|[+-]\d{2}:\d{2})"
)
_RFC3339_WITHOUT_OFFSET_PATTERN = re.compile(r"\d{4}-\d{2}-\d{2}[Tt]\d{2}:\d{2}:\d{2}(?:\.\d+)?")


class NoteType(StrEnum):
    """Семантические типы заметок; location/lifecycle намеренно не являются type."""

    NOTE = "note"
    ZETTEL = "zettel"
    PROJECT = "project"
    AREA = "area"
    RESOURCE = "resource"


class DiagnosticSeverity(StrEnum):
    """Severity для стабильных CLI- и JSON-diagnostics."""

    INFO = "info"
    WARNING = "warning"
    ERROR = "error"


@dataclass(frozen=True, slots=True)
class Diagnostic:
    """Детерминированная validation diagnostic для пользователя."""

    code: str
    message: str
    severity: DiagnosticSeverity
    path: str | None = None
    line: int | None = None

    def as_dict(self) -> dict[str, Any]:
        """Вернуть JSON-совместимое представление."""

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
class AttachmentPolicy:
    """Пороговые размеры одного attachment в bytes."""

    warning_size_bytes: int
    max_size_bytes: int

    def __post_init__(self) -> None:
        if self.warning_size_bytes <= 0:
            raise ValueError("attachment warning size must be positive")
        if self.max_size_bytes < self.warning_size_bytes:
            raise ValueError("attachment max size must be at least the warning size")


@dataclass(frozen=True, slots=True)
class VaultPaths:
    """Относительные к vault roots, объявленные manifest."""

    inbox: PurePosixPath
    projects: PurePosixPath
    areas: PurePosixPath
    resources: PurePosixPath
    zettelkasten: PurePosixPath
    archive: PurePosixPath
    templates: PurePosixPath
    attachments: PurePosixPath

    def as_dict(self) -> dict[str, str]:
        """Вернуть paths в порядке ключей manifest."""

        return {
            "inbox": self.inbox.as_posix(),
            "projects": self.projects.as_posix(),
            "areas": self.areas.as_posix(),
            "resources": self.resources.as_posix(),
            "zettelkasten": self.zettelkasten.as_posix(),
            "archive": self.archive.as_posix(),
            "templates": self.templates.as_posix(),
            "attachments": self.attachments.as_posix(),
        }


@dataclass(frozen=True, slots=True)
class VaultManifest:
    """Проверенный корневой vault contract."""

    schema_version: int
    vault_id: UUID
    default_language: str
    paths: VaultPaths
    attachments: AttachmentPolicy


@dataclass(frozen=True, slots=True)
class NoteRecord:
    """Markdown note в представлении read-only scan."""

    relative_path: str
    front_matter: Mapping[str, Any]
    body: str
    managed: bool
    note_id: UUID | None = None
    note_type: NoteType | None = None
    created: datetime | None = None
    updated: datetime | None = None
    tags: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class LinkReference:
    """Базовый Obsidian wikilink или embed из Markdown-текста."""

    source_path: str
    raw: str
    target: str
    fragment: str | None
    fragment_kind: str | None
    is_embed: bool


@dataclass(frozen=True, slots=True)
class AttachmentRecord:
    """Attachment, найденный под объявленным attachments root."""

    relative_path: str
    size_bytes: int


@dataclass(frozen=True, slots=True)
class ScanReport:
    """Полный неизменяемый результат vault scan."""

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

    def as_dict(self) -> dict[str, Any]:
        """Вернуть JSON-совместимый report."""

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


def parse_uuid7(value: object) -> UUID:
    """Разобрать каноническое значение, совместимое с UUIDv7."""

    if isinstance(value, UUID):
        result = value
    elif isinstance(value, str):
        try:
            result = UUID(value)
        except ValueError as exc:
            raise ValueError("value is not a valid UUID") from exc
        if value != str(result):
            raise ValueError("UUID must use lowercase canonical form with hyphens")
    else:
        raise ValueError("value must be a UUID string")
    if result.version != 7:
        raise ValueError("UUID version must be 7")
    return result


def parse_rfc3339(value: object) -> datetime:
    """Разобрать RFC 3339 timestamp и потребовать явный UTC offset."""

    if isinstance(value, datetime):
        result = value
    elif isinstance(value, date):
        raise ValueError("timestamp must include time")
    elif isinstance(value, str):
        if not _RFC3339_PATTERN.fullmatch(value):
            if _RFC3339_WITHOUT_OFFSET_PATTERN.fullmatch(value):
                raise ValueError("timestamp must include an explicit UTC offset")
            raise ValueError("value is not a valid RFC 3339 timestamp")
        normalized = value[:-1] + "+00:00" if value.endswith(("Z", "z")) else value
        try:
            result = datetime.fromisoformat(normalized)
        except ValueError as exc:
            raise ValueError("value is not a valid RFC 3339 timestamp") from exc
    else:
        raise ValueError("timestamp must be an RFC 3339 string")
    if result.tzinfo is None or result.utcoffset() is None:
        raise ValueError("timestamp must include an explicit UTC offset")
    return result
