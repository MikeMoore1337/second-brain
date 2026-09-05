"""On-demand Personal Timeline read model над текущим canonical vault scan."""

from __future__ import annotations

import unicodedata
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import PurePosixPath
from typing import Final, Literal
from uuid import UUID

from second_brain.application.personal_memory import is_personal_memory_enrolled
from second_brain.application.ports import VaultReader
from second_brain.application.reports import ScanReport
from second_brain.application.validation import build_report
from second_brain.domain.models import (
    EvidenceAt,
    EvidenceAtPrecision,
    EvidenceKind,
    NoteRecord,
    PersonalMemoryMetadata,
    SelfKind,
)

type TimelineOrder = Literal["asc", "desc"]
type TimelineEventAt = EvidenceAt
type TimelineClock = Callable[[], datetime]

DEFAULT_TIMELINE_ORDER: Final[TimelineOrder] = "desc"
DEFAULT_TIMELINE_LIMIT: Final[int] = 100
MIN_TIMELINE_LIMIT: Final[int] = 0
MAX_TIMELINE_LIMIT: Final[int] = 200
MAX_TIMELINE_SUMMARY_BYTES: Final[int] = 512

_SAFE_RELATIVE_PATH_PARTS: Final[frozenset[str]] = frozenset({"", ".", ".."})
_BLOCKING_DIAGNOSTIC_CODES: Final[frozenset[str]] = frozenset(
    {
        "DUPLICATE_NOTE_ID",
        "DECISION_JOURNAL_INVALID_BODY",
        "OUTCOME_OBSERVATION_INVALID_BODY",
    }
)


class TimelineErrorCode(StrEnum):
    """Стабильные bounded-коды ошибок Personal Timeline boundary."""

    INVALID_REQUEST = "TIMELINE_INVALID_REQUEST"
    INVALID_CLOCK = "TIMELINE_INVALID_CLOCK"
    EVIDENCE_INVALID = "TIMELINE_EVIDENCE_INVALID"
    VAULT_UNAVAILABLE = "TIMELINE_VAULT_UNAVAILABLE"


_ERROR_MESSAGES: Final[dict[TimelineErrorCode, str]] = {
    TimelineErrorCode.INVALID_REQUEST: "personal timeline request failed validation",
    TimelineErrorCode.INVALID_CLOCK: "personal timeline clock must return an aware datetime",
    TimelineErrorCode.EVIDENCE_INVALID: "personal timeline evidence is invalid",
    TimelineErrorCode.VAULT_UNAVAILABLE: "personal timeline vault read is unavailable",
}


class TimelineError(RuntimeError):
    """Safe application error без raw vault paths или parser details."""

    def __init__(self, code: TimelineErrorCode | str) -> None:
        normalized = _normalize_error_code(code)
        self.code = normalized.value
        self.message = _ERROR_MESSAGES[normalized]
        super().__init__(self.message)

    def as_dict(self) -> dict[str, str]:
        """Вернуть стабильное JSON-совместимое представление ошибки."""

        return {"code": self.code, "message": self.message}


class TimelineInvalidRequestError(TimelineError):
    """Запрос нарушает bounded Timeline contract."""

    def __init__(self) -> None:
        super().__init__(TimelineErrorCode.INVALID_REQUEST)


class TimelineInvalidClockError(TimelineError):
    """Application clock вернул недопустимое значение."""

    def __init__(self) -> None:
        super().__init__(TimelineErrorCode.INVALID_CLOCK)


class TimelineEvidenceInvalidError(TimelineError):
    """Current canonical evidence не позволяет безопасно построить partial history."""

    def __init__(self) -> None:
        super().__init__(TimelineErrorCode.EVIDENCE_INVALID)


class TimelineVaultUnavailableError(TimelineError):
    """Current vault scan не может быть безопасно прочитан."""

    def __init__(self) -> None:
        super().__init__(TimelineErrorCode.VAULT_UNAVAILABLE)


@dataclass(frozen=True, slots=True)
class PersonalTimelineRequest:
    """Bounded in-memory запрос к Personal Timeline read model."""

    order: TimelineOrder = DEFAULT_TIMELINE_ORDER
    known_limit: int = DEFAULT_TIMELINE_LIMIT
    unknown_limit: int = DEFAULT_TIMELINE_LIMIT


@dataclass(frozen=True, slots=True)
class TimelineItem:
    """Immutable derived projection одной eligible canonical evidence note."""

    note_id: UUID
    relative_path: str
    event_kind: SelfKind
    evidence_kind: EvidenceKind
    event_at: TimelineEventAt
    precision: EvidenceAtPrecision
    domain: str | None
    summary: str
    related_note_ids: tuple[UUID, ...]
    storage_created_at: datetime
    storage_updated_at: datetime | None


@dataclass(frozen=True, slots=True)
class PersonalTimelineResult:
    """Две независимые deterministic группы результата без persistence state."""

    known_items: tuple[TimelineItem, ...]
    unknown_items: tuple[TimelineItem, ...]
    known_total: int
    unknown_total: int
    generated_at: datetime


@dataclass(frozen=True, slots=True)
class BuildPersonalTimeline:
    """Пересобрать Timeline из current ``VaultReader`` on every request."""

    reader: VaultReader
    clock: TimelineClock = lambda: datetime.now(UTC)

    def execute(self, request: PersonalTimelineRequest) -> PersonalTimelineResult:
        """Прочитать vault, построить report и вернуть bounded read model."""

        validate_personal_timeline_request(request)
        report = _read_report(self.reader)
        _validate_evidence_integrity(report)
        known, unknown = _project_timeline_items(report)

        known_items = _sort_known(known, request.order)
        unknown_items = _sort_unknown(unknown)
        generated_at = _read_clock(self.clock)

        return PersonalTimelineResult(
            known_items=known_items[: request.known_limit],
            unknown_items=unknown_items[: request.unknown_limit],
            known_total=len(known_items),
            unknown_total=len(unknown_items),
            generated_at=generated_at,
        )


def validate_personal_timeline_request(
    request: object,
) -> PersonalTimelineRequest:
    """Проверить exact request type, order и integer limits до scan."""

    if type(request) is not PersonalTimelineRequest:
        raise TimelineInvalidRequestError()
    if type(request.order) is not str or request.order not in {"asc", "desc"}:
        raise TimelineInvalidRequestError()
    if not _valid_limit(request.known_limit) or not _valid_limit(request.unknown_limit):
        raise TimelineInvalidRequestError()
    return request


def _normalize_error_code(code: TimelineErrorCode | str) -> TimelineErrorCode:
    if isinstance(code, TimelineErrorCode):
        return code
    try:
        return TimelineErrorCode(code)
    except TypeError, ValueError:
        return TimelineErrorCode.VAULT_UNAVAILABLE


def _valid_limit(value: object) -> bool:
    return type(value) is int and MIN_TIMELINE_LIMIT <= value <= MAX_TIMELINE_LIMIT


def _read_report(reader: VaultReader) -> ScanReport:
    """Пройти ровно через ``scan() -> build_report()`` без Search authority."""

    try:
        report = build_report(reader.scan())
    except TimelineError:
        raise
    except Exception:
        raise TimelineVaultUnavailableError() from None
    if report.manifest is None:
        raise TimelineVaultUnavailableError()
    return report


def _validate_evidence_integrity(report: ScanReport) -> None:
    """Остановить Timeline до projection при scoped canonical integrity errors."""

    if any(_is_blocking_diagnostic(item.code) for item in report.diagnostics):
        raise TimelineEvidenceInvalidError()

    for note in report.notes:
        if not is_personal_memory_enrolled(note.front_matter):
            continue
        if note.personal_memory is None or not _has_valid_storage_identity(note):
            raise TimelineEvidenceInvalidError()


def _is_blocking_diagnostic(code: str) -> bool:
    return code in _BLOCKING_DIAGNOSTIC_CODES or code.startswith(
        ("PERSONAL_MEMORY_", "OUTCOME_DECISION_")
    )


def _project_timeline_items(
    report: ScanReport,
) -> tuple[list[TimelineItem], list[TimelineItem]]:
    known: list[TimelineItem] = []
    unknown: list[TimelineItem] = []
    for note in report.notes:
        metadata = note.personal_memory
        if metadata is None:
            continue
        if not _has_valid_storage_identity(note):
            raise TimelineEvidenceInvalidError()
        item = _project_note(note, metadata)
        if item.event_at == "unknown":
            unknown.append(item)
        else:
            known.append(item)
    return known, unknown


def _project_note(note: NoteRecord, metadata: PersonalMemoryMetadata) -> TimelineItem:
    if not _is_safe_relative_path(note.relative_path):
        raise TimelineEvidenceInvalidError()
    if note.note_id is None or note.created is None:
        raise TimelineEvidenceInvalidError()
    if not _valid_event_time(metadata):
        raise TimelineEvidenceInvalidError()

    related_note_ids: tuple[UUID, ...] = ()
    if metadata.evidence_kind is EvidenceKind.OBSERVED_DECISION:
        if note.decision_journal is None or metadata.self_kind is not SelfKind.DECISION:
            raise TimelineEvidenceInvalidError()
        summary_source = note.decision_journal.chosen_option
    elif metadata.evidence_kind is EvidenceKind.OUTCOME_LATER_OBSERVATION:
        outcome = note.outcome_observation
        if (
            outcome is None
            or metadata.self_kind is not SelfKind.OUTCOME
            or metadata.decision_id is None
            or outcome.decision_id != metadata.decision_id
        ):
            raise TimelineEvidenceInvalidError()
        related_note_ids = (metadata.decision_id,)
        summary_source = outcome.actual_result or outcome.reassessment
    elif metadata.evidence_kind in {
        EvidenceKind.EXPLICIT_USER_FACT,
        EvidenceKind.USER_STATEMENT,
    }:
        if metadata.self_kind not in {
            SelfKind.MEMORY,
            SelfKind.PREFERENCE,
            SelfKind.BELIEF,
            SelfKind.GOAL,
        }:
            raise TimelineEvidenceInvalidError()
        summary_source = note.body
    else:
        raise TimelineEvidenceInvalidError()

    return TimelineItem(
        note_id=note.note_id,
        relative_path=note.relative_path,
        event_kind=metadata.self_kind,
        evidence_kind=metadata.evidence_kind,
        event_at=metadata.evidence_at,
        precision=metadata.evidence_at_precision,
        domain=metadata.domain,
        summary=_bounded_summary(summary_source),
        related_note_ids=related_note_ids,
        storage_created_at=note.created,
        storage_updated_at=note.updated,
    )


def _has_valid_storage_identity(note: NoteRecord) -> bool:
    return (
        note.managed is True
        and type(note.note_id) is UUID
        and note.note_id.version == 7
        and _is_aware(note.created)
        and (note.updated is None or _is_aware(note.updated))
    )


def _sort_known(items: list[TimelineItem], order: TimelineOrder) -> tuple[TimelineItem, ...]:
    """Сортировать по instant; non-temporal tie-break остаётся ascending."""

    by_tie_break = sorted(items, key=lambda item: (item.relative_path, str(item.note_id)))
    return tuple(
        sorted(
            by_tie_break,
            key=lambda item: _known_event_at(item).astimezone(UTC),
            reverse=order == "desc",
        )
    )


def _sort_unknown(items: list[TimelineItem]) -> tuple[TimelineItem, ...]:
    """Unknown ordering не использует ни один storage/generation timestamp."""

    return tuple(sorted(items, key=lambda item: (item.relative_path, str(item.note_id))))


def _known_event_at(item: TimelineItem) -> datetime:
    if not isinstance(item.event_at, datetime) or not _is_aware(item.event_at):
        raise TimelineEvidenceInvalidError()
    return item.event_at


def _valid_event_time(metadata: PersonalMemoryMetadata) -> bool:
    if metadata.evidence_at == "unknown":
        return metadata.evidence_at_precision is EvidenceAtPrecision.UNKNOWN
    return (
        _is_aware(metadata.evidence_at)
        and metadata.evidence_at_precision is EvidenceAtPrecision.EXACT
    )


def _read_clock(clock: TimelineClock) -> datetime:
    try:
        value = clock()
    except TimelineError:
        raise
    except Exception:
        raise TimelineInvalidClockError() from None
    if not _is_aware(value):
        raise TimelineInvalidClockError()
    return value


def _bounded_summary(value: str) -> str:
    if type(value) is not str:
        raise TimelineEvidenceInvalidError()
    sanitized = "".join(
        " " if unicodedata.category(char) in {"Cc", "Cf"} else char for char in value
    )
    normalized = " ".join(sanitized.split())
    try:
        encoded = normalized.encode("utf-8")
    except UnicodeEncodeError:
        raise TimelineEvidenceInvalidError() from None
    if len(encoded) <= MAX_TIMELINE_SUMMARY_BYTES:
        return normalized
    return encoded[:MAX_TIMELINE_SUMMARY_BYTES].decode("utf-8", errors="ignore")


def _is_aware(value: object) -> bool:
    return (
        isinstance(value, datetime) and value.tzinfo is not None and value.utcoffset() is not None
    )


def _is_safe_relative_path(value: object) -> bool:
    if type(value) is not str or not value or "\\" in value:
        return False
    path = PurePosixPath(value)
    return not (
        path.is_absolute()
        or path.as_posix() != value
        or any(part in _SAFE_RELATIVE_PATH_PARTS for part in path.parts)
    )


__all__ = [
    "DEFAULT_TIMELINE_LIMIT",
    "DEFAULT_TIMELINE_ORDER",
    "MAX_TIMELINE_LIMIT",
    "MAX_TIMELINE_SUMMARY_BYTES",
    "MIN_TIMELINE_LIMIT",
    "BuildPersonalTimeline",
    "PersonalTimelineRequest",
    "PersonalTimelineResult",
    "TimelineError",
    "TimelineErrorCode",
    "TimelineEvidenceInvalidError",
    "TimelineInvalidClockError",
    "TimelineInvalidRequestError",
    "TimelineItem",
    "TimelineOrder",
    "TimelineVaultUnavailableError",
    "validate_personal_timeline_request",
]
