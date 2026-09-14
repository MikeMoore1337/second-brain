"""Domain-значения для read-only use cases проверки vault."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime
from enum import StrEnum
from pathlib import PurePosixPath
from typing import TYPE_CHECKING, Any, Literal
from uuid import UUID

if TYPE_CHECKING:
    from second_brain.application.goal_progress import DefinitionRecordV1, ObservationRecordV1

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


class EvidenceKind(StrEnum):
    """Закрытые provenance-классы canonical Personal Memory v1."""

    EXPLICIT_USER_FACT = "explicit_user_fact"
    USER_STATEMENT = "user_statement"
    OBSERVED_DECISION = "observed_decision"
    OUTCOME_LATER_OBSERVATION = "outcome_later_observation"


class SelfKind(StrEnum):
    """Закрытые semantic subjects canonical Personal Memory v1."""

    MEMORY = "memory"
    PREFERENCE = "preference"
    BELIEF = "belief"
    GOAL = "goal"
    DECISION = "decision"
    OUTCOME = "outcome"


class EvidenceAtPrecision(StrEnum):
    """Точность canonical evidence time в Personal Memory Contract v1."""

    EXACT = "exact"
    UNKNOWN = "unknown"


# Алиасы сохраняют нейтральное имя temporal value для application callers.
TemporalPrecision = EvidenceAtPrecision
EvidenceTimePrecision = EvidenceAtPrecision

EvidenceAt = datetime | Literal["unknown"]


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
class MarkdownDocument:
    """Прочитанный adapter Markdown-документ до application validation."""

    relative_path: str
    front_matter: Mapping[str, Any]
    body: str
    in_inbox: bool


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
    personal_memory: PersonalMemoryMetadata | None = None
    decision_journal: DecisionJournalRecord | None = None
    outcome_observation: OutcomeObservationRecord | None = None
    goal_progress_definition: DefinitionRecordV1 | None = None
    goal_progress_observation: ObservationRecordV1 | None = None

    @property
    def personal_memory_metadata(self) -> PersonalMemoryMetadata | None:
        """Вернуть additive projection под явным application-friendly именем."""

        return self.personal_memory


@dataclass(frozen=True, slots=True)
class PersonalMemoryMetadata:
    """Проверенная typed metadata projection enrolled managed note."""

    evidence_kind: EvidenceKind
    self_kind: SelfKind
    evidence_at: EvidenceAt
    evidence_at_precision: EvidenceAtPrecision
    domain: str | None = None
    decision_id: UUID | None = None


@dataclass(frozen=True, slots=True)
class DecisionJournalRecord:
    """Минимальная typed projection валидного Decision Journal body."""

    situation: str
    available_options: tuple[str, ...]
    information_known_at_decision_time: str
    criteria: tuple[str, ...]
    chosen_option: str
    reasons: str
    confidence: str
    expected_result: str


@dataclass(frozen=True, slots=True)
class OutcomeObservationRecord:
    """Минимальная typed projection валидного Outcome Observation body."""

    decision_id: UUID
    actual_result: str
    reassessment: str
    notes: str


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
        value = value.isoformat()
    elif isinstance(value, date):
        raise ValueError("timestamp must include time")

    if isinstance(value, str):
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
