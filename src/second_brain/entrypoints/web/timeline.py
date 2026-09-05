"""Lazy Web projection of the application-owned Personal Timeline read model."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Literal, Protocol
from uuid import UUID

from pydantic import BaseModel, ConfigDict, StrictInt, StrictStr

from second_brain.adapters.vault import FileSystemVaultReader
from second_brain.application.timeline import (
    BuildPersonalTimeline,
    PersonalTimelineRequest,
    PersonalTimelineResult,
    TimelineEvidenceInvalidError,
    TimelineInvalidClockError,
    TimelineItem,
)
from second_brain.config import load_config


class TimelineService(Protocol):
    """Minimal injectable seam for one current Timeline rebuild."""

    def build(self, request: PersonalTimelineRequest) -> PersonalTimelineResult:
        """Build the current Timeline through the application core."""


@dataclass(frozen=True, slots=True)
class LazyVaultTimelineService:
    """Resolve configuration and scan the vault only for an explicit request."""

    env_file: Path | None = None
    vault_path_override: str | None = None

    def build(self, request: PersonalTimelineRequest) -> PersonalTimelineResult:
        """Rebuild the current read model without cache, persistence, or Search."""

        config = load_config(
            env_file=self.env_file,
            vault_path_override=self.vault_path_override,
        )
        reader = FileSystemVaultReader(config.vault_path)
        return BuildPersonalTimeline(reader).execute(request)


def build_production_timeline_service(
    *,
    env_file: Path | None = None,
    vault_path_override: str | None = None,
) -> LazyVaultTimelineService:
    """Create a lazy Timeline service without loading config or touching the vault."""

    return LazyVaultTimelineService(
        env_file=env_file,
        vault_path_override=vault_path_override,
    )


class TimelineRequestPayload(BaseModel):
    """Strict JSON projection of the existing application Timeline request."""

    model_config = ConfigDict(extra="forbid", strict=True)

    order: StrictStr = "desc"
    known_limit: StrictInt = 100
    unknown_limit: StrictInt = 100


class TimelineItemPayload(BaseModel):
    """Safe bounded projection of one application Timeline item."""

    model_config = ConfigDict(extra="forbid", strict=True)

    id: StrictStr
    relative_path: StrictStr
    event_kind: StrictStr
    evidence_kind: StrictStr
    event_at: datetime | Literal["unknown"]
    precision: Literal["exact", "unknown"]
    domain: StrictStr | None
    summary: StrictStr
    related_note_ids: list[StrictStr]
    storage_created_at: datetime
    storage_updated_at: datetime | None


class TimelineResponse(BaseModel):
    """Safe response envelope with separate known and unknown groups."""

    model_config = ConfigDict(extra="forbid", strict=True)

    known_items: list[TimelineItemPayload]
    unknown_items: list[TimelineItemPayload]
    known_total: StrictInt
    unknown_total: StrictInt
    generated_at: datetime


def timeline_response(result: PersonalTimelineResult) -> TimelineResponse:
    """Serialize only the existing core result, preserving its order and times."""

    if not _is_aware(result.generated_at):
        raise TimelineInvalidClockError()
    return TimelineResponse(
        known_items=[_timeline_item_payload(item) for item in result.known_items],
        unknown_items=[_timeline_item_payload(item) for item in result.unknown_items],
        known_total=result.known_total,
        unknown_total=result.unknown_total,
        generated_at=result.generated_at,
    )


def _timeline_item_payload(item: TimelineItem) -> TimelineItemPayload:
    """Serialize an item without exposing body, front matter, or filesystem identity."""

    relative_path = _safe_relative_path(item.relative_path)
    if relative_path is None:
        raise TimelineEvidenceInvalidError()
    if type(item.note_id) is not UUID or item.note_id.version != 7:
        raise TimelineEvidenceInvalidError()
    if not _is_aware(item.storage_created_at):
        raise TimelineEvidenceInvalidError()
    if item.storage_updated_at is not None and not _is_aware(item.storage_updated_at):
        raise TimelineEvidenceInvalidError()

    event_at: datetime | Literal["unknown"]
    if item.event_at == "unknown":
        event_at = "unknown"
    elif isinstance(item.event_at, datetime) and _is_aware(item.event_at):
        event_at = item.event_at
    else:
        raise TimelineEvidenceInvalidError()

    event_kind = _enum_value(item.event_kind)
    evidence_kind = _enum_value(item.evidence_kind)
    precision = _enum_value(item.precision)
    if precision not in {"exact", "unknown"}:
        raise TimelineEvidenceInvalidError()
    precision_value: Literal["exact", "unknown"] = "exact" if precision == "exact" else "unknown"
    if type(item.domain) not in {str, type(None)}:
        raise TimelineEvidenceInvalidError()
    if type(item.summary) is not str:
        raise TimelineEvidenceInvalidError()
    related_note_ids = []
    for related_note_id in item.related_note_ids:
        if type(related_note_id) is not UUID or related_note_id.version != 7:
            raise TimelineEvidenceInvalidError()
        related_note_ids.append(str(related_note_id))

    return TimelineItemPayload(
        id=str(item.note_id),
        relative_path=relative_path,
        event_kind=event_kind,
        evidence_kind=evidence_kind,
        event_at=event_at,
        precision=precision_value,
        domain=item.domain,
        summary=item.summary,
        related_note_ids=related_note_ids,
        storage_created_at=item.storage_created_at,
        storage_updated_at=item.storage_updated_at,
    )


def _enum_value(value: object) -> str:
    raw = getattr(value, "value", None)
    if type(raw) is not str:
        raise TimelineEvidenceInvalidError()
    return raw


def _safe_relative_path(value: object) -> str | None:
    """Allow only normalized relative POSIX paths in the browser projection."""

    if type(value) is not str or not value or "\\" in value:
        return None
    posix_path = PurePosixPath(value)
    windows_path = PureWindowsPath(value)
    if (
        posix_path.is_absolute()
        or windows_path.is_absolute()
        or windows_path.drive
        or posix_path.as_posix() != value
        or any(part in {"", ".", ".."} for part in posix_path.parts)
    ):
        return None
    return value


def _is_aware(value: object) -> bool:
    return (
        isinstance(value, datetime) and value.tzinfo is not None and value.utcoffset() is not None
    )


__all__ = [
    "LazyVaultTimelineService",
    "TimelineItemPayload",
    "TimelineRequestPayload",
    "TimelineResponse",
    "TimelineService",
    "build_production_timeline_service",
    "timeline_response",
]
