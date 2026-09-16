"""Append-only operational storage for the owner-reviewed Personal Plan.

The store is deliberately separate from the vault and from provider execution.
It keeps one replayable current global plan, exact planning provenance, and
bounded owner edits.  Every mutation is serialized under the existing local
store lock, is idempotent by operation id, and is protected by a canonical
JSONL hash chain plus an atomic manifest.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from enum import StrEnum
from itertools import pairwise
from pathlib import Path
from typing import Any, Final, cast
from uuid import UUID, uuid7
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from second_brain.application.adaptive_cognitive_twin_store import _StoreLock
from second_brain.application.personal_planning import (
    MAX_PLANNING_HORIZON_DAYS,
    MAX_PLANNING_ITEMS,
    PLANNING_POLICY_FINGERPRINT,
    PLANNING_POLICY_ID,
    PlanningCapacityEntryV1,
    PlanningContextPackV1,
    PlanningItemKindV1,
    PlanningItemV1,
    PlanningProposalV1,
    PlanningWindowV1,
    validate_planning_context_pack,
    validate_planning_proposal,
)
from second_brain.domain.models import parse_rfc3339, parse_uuid7

PERSONAL_PLANNING_STORE_PARENT_NAME: Final[str] = "prospective-audit"
PERSONAL_PLANNING_STORE_DIRECTORY_NAME: Final[str] = "personal-planning"
PERSONAL_PLANNING_STORE_RECORD_FILE_NAME: Final[str] = "plans.jsonl"
PERSONAL_PLANNING_STORE_MANIFEST_FILE_NAME: Final[str] = "manifest.json"
PERSONAL_PLANNING_STORE_LOCK_FILE_NAME: Final[str] = ".store.lock"
PERSONAL_PLANNING_STORE_FORMAT_VERSION: Final[int] = 1
PERSONAL_PLANNING_STORE_MAX_RECORDS: Final[int] = 4096
PERSONAL_PLANNING_STORE_MAX_RECORD_BYTES: Final[int] = 256 * 1024
PERSONAL_PLANNING_STORE_MAX_OPERATION_ID_BYTES: Final[int] = 256
PERSONAL_PLANNING_STORE_MAX_PLAN_BYTES: Final[int] = 192 * 1024

_RAW_HASH_PATTERN: Final[re.Pattern[str]] = re.compile(r"[0-9a-f]{64}\Z", re.ASCII)
_ITEM_ID_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"[A-Za-z0-9][A-Za-z0-9._:-]{0,63}\Z", re.ASCII
)
_LOCAL_DATE_PATTERN: Final[re.Pattern[str]] = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}\Z", re.ASCII)
_LOCAL_DATETIME_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}\Z", re.ASCII
)


class PersonalPlanningStoreError(RuntimeError):
    """Base class for safe Personal Planning store errors."""


class PersonalPlanningStoreInvalidRequestError(PersonalPlanningStoreError):
    """The owner supplied an invalid plan mutation."""


class PersonalPlanningStoreUnavailableError(PersonalPlanningStoreError):
    """The operational store cannot be safely initialized or written."""


class PersonalPlanningStoreCorruptError(PersonalPlanningStoreError):
    """The append-only ledger or manifest failed integrity validation."""


class PersonalPlanningStoreIdempotencyConflictError(PersonalPlanningStoreError):
    """An operation id was reused with a different canonical intent."""


class PersonalPlanningStoreStateConflictError(PersonalPlanningStoreError):
    """The expected current global plan no longer matches under the lock."""


class PersonalPlanningStoreSourceChangedError(PersonalPlanningStoreError):
    """The proposal is not bound to the exact current Planning Context Pack."""


class PersonalPlanningStoreCapacityConflictError(PersonalPlanningStoreError):
    """The selected plan cannot fit its explicit capacity or fixed windows."""


class PersonalPlanningStoreRecordTypeV1(StrEnum):
    """Closed append-only lifecycle record vocabulary."""

    PLAN_ACCEPTED = "plan_accepted"
    PLAN_REPLACED = "plan_replaced"
    PLAN_EDITED = "plan_edited"


PersonalPlanningStoreRecordType = PersonalPlanningStoreRecordTypeV1


def _canonical_bytes(value: object) -> bytes:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, UnicodeError, ValueError, OverflowError) as exc:
        raise PersonalPlanningStoreCorruptError() from exc


def personal_planning_store_hash(value: object) -> str:
    """Return the raw SHA-256 used by store envelopes and the manifest."""

    return hashlib.sha256(_canonical_bytes(value)).hexdigest()


def _raw_hash(value: object, *, invalid: type[PersonalPlanningStoreError]) -> str:
    if type(value) is not str or _RAW_HASH_PATTERN.fullmatch(value) is None:
        raise invalid()
    return value


def _uuid7(value: object, *, invalid: type[PersonalPlanningStoreError]) -> UUID:
    try:
        return parse_uuid7(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise invalid() from exc


def _timestamp(value: object, *, invalid: type[PersonalPlanningStoreError]) -> datetime:
    try:
        parsed = parse_rfc3339(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise invalid() from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise invalid()
    return parsed.astimezone(UTC)


def _format_timestamp(value: datetime) -> str:
    rendered = value.astimezone(UTC).isoformat(
        timespec="microseconds" if value.microsecond else "seconds"
    )
    return rendered.removesuffix("+00:00") + "Z"


def _enum_value[EnumT: StrEnum](value: object, enum_type: type[EnumT]) -> EnumT:
    if type(value) is enum_type:
        return value
    if type(value) is str:
        try:
            return enum_type(value)
        except ValueError:
            pass
    raise PersonalPlanningStoreCorruptError()


def _wire_dict(value: object, expected: set[str]) -> dict[str, object]:
    if type(value) is not dict or set(value) != expected:
        raise PersonalPlanningStoreCorruptError()
    return cast(dict[str, object], value)


def _wire_list(value: object) -> list[object]:
    if type(value) is not list:
        raise PersonalPlanningStoreCorruptError()
    return value


def _operation_fingerprint(value: str | UUID) -> str:
    normalized = str(value)
    try:
        encoded = normalized.encode("utf-8")
    except UnicodeError as exc:
        raise PersonalPlanningStoreInvalidRequestError() from exc
    if (
        not normalized
        or len(encoded) > PERSONAL_PLANNING_STORE_MAX_OPERATION_ID_BYTES
        or any(ord(char) < 0x20 or ord(char) == 0x7F for char in normalized)
    ):
        raise PersonalPlanningStoreInvalidRequestError()
    return hashlib.sha256(encoded).hexdigest()


def _local_date(value: object) -> str:
    if type(value) is not str or _LOCAL_DATE_PATTERN.fullmatch(value) is None:
        raise PersonalPlanningStoreInvalidRequestError()
    try:
        parsed = date.fromisoformat(value)
    except ValueError as exc:
        raise PersonalPlanningStoreInvalidRequestError() from exc
    normalized = parsed.isoformat()
    if normalized != value:
        raise PersonalPlanningStoreInvalidRequestError()
    return normalized


def _local_datetime(value: object | None) -> str | None:
    if value is None:
        return None
    if type(value) is not str or _LOCAL_DATETIME_PATTERN.fullmatch(value) is None:
        raise PersonalPlanningStoreInvalidRequestError()
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise PersonalPlanningStoreInvalidRequestError() from exc
    if parsed.tzinfo is not None or parsed.second or parsed.microsecond:
        raise PersonalPlanningStoreInvalidRequestError()
    if parsed.isoformat(timespec="minutes") != value:
        raise PersonalPlanningStoreInvalidRequestError()
    return value


def _item_id(value: object) -> str:
    if type(value) is not str or _ITEM_ID_PATTERN.fullmatch(value) is None:
        raise PersonalPlanningStoreInvalidRequestError()
    return value


def _date_range(start_local: str, end_local: str) -> tuple[date, date]:
    start = date.fromisoformat(start_local)
    end = date.fromisoformat(end_local)
    if end < start or (end - start).days + 1 > MAX_PLANNING_HORIZON_DAYS:
        raise PersonalPlanningStoreInvalidRequestError()
    return start, end


def _timezone(value: object) -> str:
    if type(value) is not str or not value or len(value.encode("utf-8")) > 128:
        raise PersonalPlanningStoreInvalidRequestError()
    if value == "UTC":
        return value
    try:
        ZoneInfo(value)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise PersonalPlanningStoreInvalidRequestError() from exc
    return value


def _validate_windows(
    windows: tuple[PlanningWindowV1, ...],
    *,
    start_local: str,
    end_local: str,
) -> tuple[PlanningWindowV1, ...]:
    start_date, end_date = _date_range(start_local, end_local)
    if any(type(window) is not PlanningWindowV1 for window in windows):
        raise PersonalPlanningStoreInvalidRequestError()
    seen: set[str] = set()
    ordered = sorted(windows, key=lambda item: (item.start_local, item.end_local, item.window_id))
    for window in ordered:
        if window.window_id in seen:
            raise PersonalPlanningStoreInvalidRequestError()
        seen.add(window.window_id)
        window_start = datetime.fromisoformat(window.start_local)
        window_end = datetime.fromisoformat(window.end_local)
        if not start_date <= window_start.date() <= end_date:
            raise PersonalPlanningStoreInvalidRequestError()
        if not start_date <= window_end.date() <= end_date:
            raise PersonalPlanningStoreInvalidRequestError()
    for previous, current in pairwise(ordered):
        if datetime.fromisoformat(current.start_local) < datetime.fromisoformat(previous.end_local):
            raise PersonalPlanningStoreInvalidRequestError()
    return windows


def _plan_core(plan: PlanningPlanV1) -> dict[str, object]:
    return {
        "plan_version": plan.plan_version,
        "plan_id": str(plan.plan_id),
        "revision": plan.revision,
        "as_of": _format_timestamp(plan.as_of),
        "source_pack_fingerprint": plan.source_pack_fingerprint,
        "provider_envelope_fingerprint": plan.provider_envelope_fingerprint,
        "provider_result_fingerprint": plan.provider_result_fingerprint,
        "proposal_fingerprint": plan.proposal_fingerprint,
        "policy_id": plan.policy_id,
        "policy_fingerprint": plan.policy_fingerprint,
        "start_local": plan.start_local,
        "end_local": plan.end_local,
        "timezone": plan.timezone,
        "capacity": [entry.as_dict() for entry in plan.capacity],
        "fixed_windows": [window.as_dict() for window in plan.fixed_windows],
        "items": [item.as_dict() for item in plan.items],
        "selected_item_ids": list(plan.selected_item_ids),
        "item_order": list(plan.item_order),
    }


def _validate_dependencies_and_hierarchy(items: tuple[PlanningItemV1, ...]) -> None:
    item_by_id = {item.item_id: item for item in items}
    for item in items:
        if any(dependency not in item_by_id for dependency in item.dependency_ids):
            raise PersonalPlanningStoreInvalidRequestError()
        if item.parent_item_id is None:
            if item.kind is PlanningItemKindV1.MILESTONE:
                raise PersonalPlanningStoreInvalidRequestError()
            continue
        parent = item_by_id.get(item.parent_item_id)
        if parent is None or parent.item_id == item.item_id:
            raise PersonalPlanningStoreInvalidRequestError()
        kind = cast(PlanningItemKindV1, item.kind)
        parent_kind = cast(PlanningItemKindV1, parent.kind)
        if (
            kind is PlanningItemKindV1.PROJECT
            or (
                kind is PlanningItemKindV1.MILESTONE
                and parent_kind is not PlanningItemKindV1.PROJECT
            )
            or (
                kind in {PlanningItemKindV1.COMMITMENT, PlanningItemKindV1.NEXT_ACTION}
                and parent_kind not in {PlanningItemKindV1.PROJECT, PlanningItemKindV1.MILESTONE}
            )
        ):
            raise PersonalPlanningStoreInvalidRequestError()

    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(item_id: str) -> None:
        if item_id in visiting:
            raise PersonalPlanningStoreInvalidRequestError()
        if item_id in visited:
            return
        visiting.add(item_id)
        item = item_by_id[item_id]
        for dependency_id in sorted(item.dependency_ids):
            visit(dependency_id)
        visiting.remove(item_id)
        visited.add(item_id)

    for item_id in sorted(item_by_id):
        visit(item_id)


def _validate_capacity(
    *,
    items: tuple[PlanningItemV1, ...],
    selected_item_ids: tuple[str, ...],
    item_order: tuple[str, ...],
    capacity: tuple[PlanningCapacityEntryV1, ...],
    fixed_windows: tuple[PlanningWindowV1, ...],
    start_local: str,
    end_local: str,
) -> None:
    selected = set(selected_item_ids)
    positions = {item_id: index for index, item_id in enumerate(item_order)}
    by_id = {item.item_id: item for item in items}
    for item_id in selected:
        item = by_id[item_id]
        for dependency_id in item.dependency_ids:
            if dependency_id not in selected or positions[dependency_id] >= positions[item_id]:
                raise PersonalPlanningStoreCapacityConflictError()

    start_date, end_date = _date_range(start_local, end_local)
    capacity_by_date = {entry.local_date: entry.available_minutes for entry in capacity}
    if tuple(
        (start_date + timedelta(days=index)).isoformat()
        for index in range((end_date - start_date).days + 1)
    ) != tuple(capacity_by_date):
        raise PersonalPlanningStoreCapacityConflictError()

    used: dict[str, int] = {local_date: 0 for local_date in capacity_by_date}
    for item_id in selected_item_ids:
        item = by_id[item_id]
        if item.target_start_local is None or item.target_end_local is None:
            continue
        target_start = datetime.fromisoformat(item.target_start_local)
        target_end = datetime.fromisoformat(item.target_end_local)
        if not start_date <= target_start.date() <= end_date:
            raise PersonalPlanningStoreCapacityConflictError()
        if not start_date <= target_end.date() <= end_date:
            raise PersonalPlanningStoreCapacityConflictError()
        used[target_start.date().isoformat()] += item.effort_minutes
        for window in fixed_windows:
            window_start = datetime.fromisoformat(window.start_local)
            window_end = datetime.fromisoformat(window.end_local)
            if target_start < window_end and window_start < target_end:
                raise PersonalPlanningStoreCapacityConflictError()
    if any(used[local_date] > capacity_by_date[local_date] for local_date in used):
        raise PersonalPlanningStoreCapacityConflictError()


@dataclass(frozen=True, slots=True)
class PlanningPlanV1:
    """One owner-reviewed, non-executable current global plan."""

    plan_version: str
    plan_id: UUID | str
    revision: int
    as_of: datetime
    source_pack_fingerprint: str
    provider_envelope_fingerprint: str
    provider_result_fingerprint: str
    proposal_fingerprint: str
    policy_id: str
    policy_fingerprint: str
    start_local: str
    end_local: str
    timezone: str
    capacity: tuple[PlanningCapacityEntryV1, ...]
    fixed_windows: tuple[PlanningWindowV1, ...]
    items: tuple[PlanningItemV1, ...]
    selected_item_ids: tuple[str, ...]
    item_order: tuple[str, ...]
    plan_fingerprint: str

    def __post_init__(self) -> None:
        invalid = PersonalPlanningStoreInvalidRequestError
        if self.plan_version != "1":
            raise invalid()
        plan_id = _uuid7(self.plan_id, invalid=invalid)
        if (
            type(self.revision) is not int
            or isinstance(self.revision, bool)
            or not 1 <= self.revision <= (1 << 64) - 1
        ):
            raise invalid()
        as_of = _timestamp(self.as_of, invalid=invalid)
        source_pack_fingerprint = _raw_hash(self.source_pack_fingerprint, invalid=invalid)
        provider_envelope_fingerprint = _raw_hash(
            self.provider_envelope_fingerprint, invalid=invalid
        )
        provider_result_fingerprint = _raw_hash(self.provider_result_fingerprint, invalid=invalid)
        proposal_fingerprint = _raw_hash(self.proposal_fingerprint, invalid=invalid)
        if (
            self.policy_id != PLANNING_POLICY_ID
            or self.policy_fingerprint != PLANNING_POLICY_FINGERPRINT
        ):
            raise invalid()
        start_local = _local_date(self.start_local)
        end_local = _local_date(self.end_local)
        _date_range(start_local, end_local)
        timezone = _timezone(self.timezone)
        if type(self.capacity) is not tuple or any(
            type(entry) is not PlanningCapacityEntryV1 for entry in self.capacity
        ):
            raise invalid()
        capacity = tuple(self.capacity)
        start_date, end_date = _date_range(start_local, end_local)
        expected_dates = tuple(
            (start_date + timedelta(days=index)).isoformat()
            for index in range((end_date - start_date).days + 1)
        )
        if tuple(entry.local_date for entry in capacity) != expected_dates:
            raise invalid()
        if type(self.fixed_windows) is not tuple:
            raise invalid()
        fixed_windows = _validate_windows(
            tuple(self.fixed_windows), start_local=start_local, end_local=end_local
        )
        if type(self.items) is not tuple or not 1 <= len(self.items) <= MAX_PLANNING_ITEMS:
            raise invalid()
        items = tuple(self.items)
        if any(type(item) is not PlanningItemV1 for item in items):
            raise invalid()
        if len({item.item_id for item in items}) != len(items):
            raise invalid()
        _validate_dependencies_and_hierarchy(items)
        for item in items:
            if item.target_start_local is None or item.target_end_local is None:
                continue
            target_start = datetime.fromisoformat(item.target_start_local)
            target_end = datetime.fromisoformat(item.target_end_local)
            if (
                not start_date <= target_start.date() <= end_date
                or not start_date <= target_end.date() <= end_date
            ):
                raise invalid()
        if type(self.selected_item_ids) is not tuple or type(self.item_order) is not tuple:
            raise invalid()
        selected_item_ids = tuple(_item_id(item_id) for item_id in self.selected_item_ids)
        item_order = tuple(_item_id(item_id) for item_id in self.item_order)
        item_ids = {item.item_id for item in items}
        if (
            len(set(selected_item_ids)) != len(selected_item_ids)
            or len(set(item_order)) != len(item_order)
            or not set(selected_item_ids) <= item_ids
            or set(item_order) != set(selected_item_ids)
        ):
            raise invalid()
        try:
            _validate_capacity(
                items=items,
                selected_item_ids=selected_item_ids,
                item_order=item_order,
                capacity=capacity,
                fixed_windows=fixed_windows,
                start_local=start_local,
                end_local=end_local,
            )
        except PersonalPlanningStoreCapacityConflictError:
            raise
        except (KeyError, TypeError, ValueError) as exc:
            raise invalid() from exc
        object.__setattr__(self, "plan_version", "1")
        object.__setattr__(self, "plan_id", plan_id)
        object.__setattr__(self, "as_of", as_of)
        object.__setattr__(self, "source_pack_fingerprint", source_pack_fingerprint)
        object.__setattr__(self, "provider_envelope_fingerprint", provider_envelope_fingerprint)
        object.__setattr__(self, "provider_result_fingerprint", provider_result_fingerprint)
        object.__setattr__(self, "proposal_fingerprint", proposal_fingerprint)
        object.__setattr__(self, "policy_id", PLANNING_POLICY_ID)
        object.__setattr__(self, "policy_fingerprint", PLANNING_POLICY_FINGERPRINT)
        object.__setattr__(self, "start_local", start_local)
        object.__setattr__(self, "end_local", end_local)
        object.__setattr__(self, "timezone", timezone)
        object.__setattr__(self, "capacity", capacity)
        object.__setattr__(self, "fixed_windows", fixed_windows)
        object.__setattr__(self, "items", items)
        object.__setattr__(self, "selected_item_ids", selected_item_ids)
        object.__setattr__(self, "item_order", item_order)
        supplied_fingerprint = _raw_hash(self.plan_fingerprint, invalid=invalid)
        expected_fingerprint = personal_planning_store_hash(_plan_core(self))
        if supplied_fingerprint != expected_fingerprint:
            raise invalid()
        object.__setattr__(self, "plan_fingerprint", expected_fingerprint)
        if len(_canonical_bytes(self.as_dict())) > PERSONAL_PLANNING_STORE_MAX_PLAN_BYTES:
            raise invalid()

    def as_dict(self) -> dict[str, object]:
        return {**_plan_core(self), "plan_fingerprint": self.plan_fingerprint}

    def to_json(self) -> str:
        return _canonical_bytes(self.as_dict()).decode("utf-8")

    @classmethod
    def from_dict(cls, value: object) -> PlanningPlanV1:
        data = _wire_dict(
            value,
            {
                "plan_version",
                "plan_id",
                "revision",
                "as_of",
                "source_pack_fingerprint",
                "provider_envelope_fingerprint",
                "provider_result_fingerprint",
                "proposal_fingerprint",
                "policy_id",
                "policy_fingerprint",
                "start_local",
                "end_local",
                "timezone",
                "capacity",
                "fixed_windows",
                "items",
                "selected_item_ids",
                "item_order",
                "plan_fingerprint",
            },
        )
        capacity = _wire_list(data["capacity"])
        fixed_windows = _wire_list(data["fixed_windows"])
        items = _wire_list(data["items"])
        selected_item_ids = _wire_list(data["selected_item_ids"])
        item_order = _wire_list(data["item_order"])
        return cls(
            plan_version=cast(str, data["plan_version"]),
            plan_id=cast(UUID | str, data["plan_id"]),
            revision=cast(int, data["revision"]),
            as_of=cast(datetime, data["as_of"]),
            source_pack_fingerprint=cast(str, data["source_pack_fingerprint"]),
            provider_envelope_fingerprint=cast(str, data["provider_envelope_fingerprint"]),
            provider_result_fingerprint=cast(str, data["provider_result_fingerprint"]),
            proposal_fingerprint=cast(str, data["proposal_fingerprint"]),
            policy_id=cast(str, data["policy_id"]),
            policy_fingerprint=cast(str, data["policy_fingerprint"]),
            start_local=cast(str, data["start_local"]),
            end_local=cast(str, data["end_local"]),
            timezone=cast(str, data["timezone"]),
            capacity=tuple(PlanningCapacityEntryV1.from_dict(item) for item in capacity),
            fixed_windows=tuple(PlanningWindowV1.from_dict(item) for item in fixed_windows),
            items=tuple(PlanningItemV1.from_dict(item) for item in items),
            selected_item_ids=tuple(cast(str, item) for item in selected_item_ids),
            item_order=tuple(cast(str, item) for item in item_order),
            plan_fingerprint=cast(str, data["plan_fingerprint"]),
        )

    @classmethod
    def from_json(cls, value: object) -> PlanningPlanV1:
        if type(value) is not str:
            raise PersonalPlanningStoreInvalidRequestError()

        def reject_duplicate_pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
            result: dict[str, object] = {}
            for key, item in pairs:
                if key in result:
                    raise PersonalPlanningStoreInvalidRequestError()
                result[key] = item
            return result

        try:
            decoded = json.loads(
                value,
                object_pairs_hook=reject_duplicate_pairs,
                parse_constant=lambda _: (_ for _ in ()).throw(ValueError()),
            )
        except (TypeError, ValueError, UnicodeError, json.JSONDecodeError) as exc:
            raise PersonalPlanningStoreInvalidRequestError() from exc
        return cls.from_dict(decoded)


@dataclass(frozen=True, slots=True)
class PersonalPlanningStoreManifestV1:
    """Atomic manifest cross-checking the complete plan-event generation."""

    format_version: int
    next_sequence: int
    record_count: int
    last_record_digest: str | None
    policy_id: str
    policy_fingerprint: str

    def __post_init__(self) -> None:
        if (
            type(self.format_version) is not int
            or isinstance(self.format_version, bool)
            or self.format_version != PERSONAL_PLANNING_STORE_FORMAT_VERSION
        ):
            raise PersonalPlanningStoreCorruptError()
        if (
            type(self.next_sequence) is not int
            or isinstance(self.next_sequence, bool)
            or not 1 <= self.next_sequence <= (1 << 64) - 1
        ):
            raise PersonalPlanningStoreCorruptError()
        if (
            type(self.record_count) is not int
            or isinstance(self.record_count, bool)
            or not 0 <= self.record_count <= PERSONAL_PLANNING_STORE_MAX_RECORDS
            or self.next_sequence != self.record_count + 1
        ):
            raise PersonalPlanningStoreCorruptError()
        digest = (
            None
            if self.last_record_digest is None
            else _raw_hash(self.last_record_digest, invalid=PersonalPlanningStoreCorruptError)
        )
        if (self.record_count == 0) != (digest is None):
            raise PersonalPlanningStoreCorruptError()
        if (
            self.policy_id != PLANNING_POLICY_ID
            or self.policy_fingerprint != PLANNING_POLICY_FINGERPRINT
        ):
            raise PersonalPlanningStoreCorruptError()
        object.__setattr__(self, "last_record_digest", digest)
        object.__setattr__(self, "policy_id", PLANNING_POLICY_ID)
        object.__setattr__(self, "policy_fingerprint", PLANNING_POLICY_FINGERPRINT)

    def as_dict(self) -> dict[str, object]:
        return {
            "format_version": self.format_version,
            "next_sequence": self.next_sequence,
            "record_count": self.record_count,
            "last_record_digest": self.last_record_digest,
            "policy_id": self.policy_id,
            "policy_fingerprint": self.policy_fingerprint,
        }


@dataclass(frozen=True, slots=True)
class PersonalPlanningStoreEventV1:
    """One owner operation without context pack or provider payload retention."""

    event_id: UUID | str
    event_type: PersonalPlanningStoreRecordTypeV1 | str
    operation_id_fingerprint: str
    intent_fingerprint: str
    previous_plan_fingerprint: str | None
    plan: PlanningPlanV1

    def __post_init__(self) -> None:
        event_id = _uuid7(self.event_id, invalid=PersonalPlanningStoreCorruptError)
        event_type = _enum_value(self.event_type, PersonalPlanningStoreRecordTypeV1)
        operation = _raw_hash(
            self.operation_id_fingerprint, invalid=PersonalPlanningStoreCorruptError
        )
        intent = _raw_hash(self.intent_fingerprint, invalid=PersonalPlanningStoreCorruptError)
        previous = (
            None
            if self.previous_plan_fingerprint is None
            else _raw_hash(
                self.previous_plan_fingerprint, invalid=PersonalPlanningStoreCorruptError
            )
        )
        if type(self.plan) is not PlanningPlanV1:
            raise PersonalPlanningStoreCorruptError()
        if event_type is PersonalPlanningStoreRecordTypeV1.PLAN_ACCEPTED and previous is not None:
            raise PersonalPlanningStoreCorruptError()
        if event_type is not PersonalPlanningStoreRecordTypeV1.PLAN_ACCEPTED and previous is None:
            raise PersonalPlanningStoreCorruptError()
        object.__setattr__(self, "event_id", event_id)
        object.__setattr__(self, "event_type", event_type)
        object.__setattr__(self, "operation_id_fingerprint", operation)
        object.__setattr__(self, "intent_fingerprint", intent)
        object.__setattr__(self, "previous_plan_fingerprint", previous)

    def as_dict(self) -> dict[str, object]:
        return {
            "event_id": str(self.event_id),
            "event_type": cast(PersonalPlanningStoreRecordTypeV1, self.event_type).value,
            "operation_id_fingerprint": self.operation_id_fingerprint,
            "intent_fingerprint": self.intent_fingerprint,
            "previous_plan_fingerprint": self.previous_plan_fingerprint,
            "plan": self.plan.as_dict(),
        }


@dataclass(frozen=True, slots=True)
class PersonalPlanningStoreEnvelopeV1:
    """Hash-chained JSONL record envelope."""

    sequence: int
    record_type: PersonalPlanningStoreRecordTypeV1 | str
    record: PersonalPlanningStoreEventV1
    previous_record_digest: str | None
    event_fingerprint: str
    record_digest: str

    def __post_init__(self) -> None:
        if (
            type(self.sequence) is not int
            or isinstance(self.sequence, bool)
            or not 1 <= self.sequence <= (1 << 64) - 1
        ):
            raise PersonalPlanningStoreCorruptError()
        if type(self.record) is not PersonalPlanningStoreEventV1:
            raise PersonalPlanningStoreCorruptError()
        record_type = _enum_value(self.record_type, PersonalPlanningStoreRecordTypeV1)
        if record_type is not self.record.event_type:
            raise PersonalPlanningStoreCorruptError()
        previous = (
            None
            if self.previous_record_digest is None
            else _raw_hash(self.previous_record_digest, invalid=PersonalPlanningStoreCorruptError)
        )
        event_fingerprint = _raw_hash(
            self.event_fingerprint, invalid=PersonalPlanningStoreCorruptError
        )
        record_digest = _raw_hash(self.record_digest, invalid=PersonalPlanningStoreCorruptError)
        object.__setattr__(self, "record_type", record_type)
        object.__setattr__(self, "previous_record_digest", previous)
        object.__setattr__(self, "event_fingerprint", event_fingerprint)
        object.__setattr__(self, "record_digest", record_digest)

    def unsigned_dict(self) -> dict[str, object]:
        return {
            "sequence": self.sequence,
            "record_type": cast(PersonalPlanningStoreRecordTypeV1, self.record_type).value,
            "record": self.record.as_dict(),
            "previous_record_digest": self.previous_record_digest,
            "event_fingerprint": self.event_fingerprint,
        }

    @property
    def expected_event_fingerprint(self) -> str:
        return personal_planning_store_hash(self.record.as_dict())

    @property
    def expected_record_digest(self) -> str:
        return personal_planning_store_hash(self.unsigned_dict())

    def as_dict(self) -> dict[str, object]:
        return {**self.unsigned_dict(), "record_digest": self.record_digest}


@dataclass(frozen=True, slots=True)
class PersonalPlanningStoreVerifiedSnapshotV1:
    manifest: PersonalPlanningStoreManifestV1
    envelopes: tuple[PersonalPlanningStoreEnvelopeV1, ...]


@dataclass(frozen=True, slots=True)
class PersonalPlanningStoreStateV1:
    """Replayed history with exactly one current global plan."""

    plan_history: tuple[PlanningPlanV1, ...]
    current_plan: PlanningPlanV1 | None

    @property
    def current(self) -> PlanningPlanV1 | None:
        """Compatibility alias for callers using a shorter current-state name."""

        return self.current_plan


def _replay_state(
    envelopes: Sequence[PersonalPlanningStoreEnvelopeV1],
) -> PersonalPlanningStoreStateV1:
    history: list[PlanningPlanV1] = []
    current: PlanningPlanV1 | None = None
    for envelope in envelopes:
        event = envelope.record
        event_type = cast(PersonalPlanningStoreRecordTypeV1, event.event_type)
        if current is None:
            if event_type is not PersonalPlanningStoreRecordTypeV1.PLAN_ACCEPTED:
                raise PersonalPlanningStoreCorruptError()
            if event.previous_plan_fingerprint is not None or event.plan.revision != 1:
                raise PersonalPlanningStoreCorruptError()
        else:
            if event.previous_plan_fingerprint != current.plan_fingerprint:
                raise PersonalPlanningStoreCorruptError()
            if event_type is PersonalPlanningStoreRecordTypeV1.PLAN_EDITED:
                if (
                    event.plan.plan_id != current.plan_id
                    or event.plan.revision != current.revision + 1
                    or event.plan.source_pack_fingerprint != current.source_pack_fingerprint
                    or event.plan.proposal_fingerprint != current.proposal_fingerprint
                ):
                    raise PersonalPlanningStoreCorruptError()
            elif event_type is PersonalPlanningStoreRecordTypeV1.PLAN_REPLACED:
                if event.plan.revision != 1 or event.plan.plan_id == current.plan_id:
                    raise PersonalPlanningStoreCorruptError()
            else:
                raise PersonalPlanningStoreCorruptError()
        history.append(event.plan)
        current = event.plan
    return PersonalPlanningStoreStateV1(tuple(history), current)


class PersonalPlanningOperationalStore:
    """Strict JSONL store for one owner-reviewed global Personal Plan."""

    def __init__(
        self,
        root: Path | os.PathLike[str],
        *,
        vault_root: Path | os.PathLike[str] | None = None,
        clock: Callable[[], datetime] | None = None,
        expected_owner_group: tuple[str, str] | None = None,
    ) -> None:
        if not isinstance(root, os.PathLike):
            raise PersonalPlanningStoreUnavailableError()
        if expected_owner_group is not None and (
            type(expected_owner_group) is not tuple
            or len(expected_owner_group) != 2
            or any(type(item) is not str or not item for item in expected_owner_group)
        ):
            raise PersonalPlanningStoreUnavailableError()
        self.root = Path(root).expanduser()
        self._clock = clock or (lambda: datetime.now(UTC))
        self._expected_owner_group = expected_owner_group
        self._validate_root(vault_root)
        try:
            if not self.root.is_absolute():
                raise ValueError("relative root")
            if self.root.exists():
                if self.root.is_symlink() or not self.root.is_dir():
                    raise ValueError("root type")
            else:
                parent = self.root.parent
                if parent.is_symlink():
                    raise ValueError("parent symlink")
                parent.mkdir(parents=True, exist_ok=True)
                self._reject_symlink_components(parent)
                self.root.mkdir(mode=0o700)
                if self.root.is_symlink() or not self.root.is_dir():
                    raise ValueError("root escaped")
            self._assert_owner_only_unlocked(require_payload=False)
            with _StoreLock(self.lock_path):
                self._assert_owner_only_unlocked(require_payload=False)
                self._initialize_unlocked()
                self._assert_owner_only_unlocked(require_payload=True)
                self._read_verified_unlocked()
        except PersonalPlanningStoreError:
            raise
        except (OSError, ValueError, UnicodeError, RecursionError) as exc:
            raise PersonalPlanningStoreUnavailableError() from exc

    @property
    def records_path(self) -> Path:
        return self.root / PERSONAL_PLANNING_STORE_RECORD_FILE_NAME

    @property
    def plans_path(self) -> Path:
        return self.records_path

    @property
    def manifest_path(self) -> Path:
        return self.root / PERSONAL_PLANNING_STORE_MANIFEST_FILE_NAME

    @property
    def lock_path(self) -> Path:
        return self.root / PERSONAL_PLANNING_STORE_LOCK_FILE_NAME

    @property
    def manifest(self) -> PersonalPlanningStoreManifestV1:
        return self.read_manifest()

    def _validate_root(self, vault_root: Path | os.PathLike[str] | None) -> None:
        try:
            if not self.root.is_absolute():
                raise ValueError("relative root")
            candidate = self.root.resolve(strict=False)
            self._reject_symlink_components(self.root)
            if any(
                part.casefold() in {"release", "releases", "second-brain-vault"}
                for part in candidate.parts
            ):
                raise ValueError("store in release")
            if vault_root is not None:
                if not isinstance(vault_root, os.PathLike):
                    raise ValueError("vault root")
                vault = Path(vault_root).expanduser().resolve(strict=True)
                if candidate == vault or candidate.is_relative_to(vault):
                    raise ValueError("store in vault")
            git_root = self._git_root(candidate)
            if git_root is not None and candidate.is_relative_to(git_root):
                raise ValueError("store in repository")
        except (OSError, RuntimeError, ValueError) as exc:
            raise PersonalPlanningStoreUnavailableError() from exc

    @staticmethod
    def _reject_symlink_components(path: Path) -> None:
        for item in (path, *path.parents):
            if item.is_symlink():
                raise ValueError("store path contains symlink")

    @staticmethod
    def _git_root(path: Path) -> Path | None:
        for parent in (path, *path.parents):
            marker = parent / ".git"
            if marker.is_dir() or marker.is_file():
                return parent
        return None

    def _initialize_unlocked(self) -> None:
        paths = (self.records_path, self.manifest_path)
        present = tuple(path.exists() for path in paths)
        if not any(present):
            self._write_bytes_atomic(self.records_path, b"")
            self._write_manifest_atomic(
                PersonalPlanningStoreManifestV1(
                    PERSONAL_PLANNING_STORE_FORMAT_VERSION,
                    1,
                    0,
                    None,
                    PLANNING_POLICY_ID,
                    PLANNING_POLICY_FINGERPRINT,
                )
            )
            return
        if not all(present):
            raise PersonalPlanningStoreCorruptError()
        self._read_verified_unlocked()

    def _assert_owner_only_unlocked(self, *, require_payload: bool = True) -> None:
        if os.name == "nt":
            return
        try:
            payload_paths = (self.records_path, self.manifest_path)
            paths = (
                (self.root, 0o700, True),
                (self.lock_path, 0o600, False),
                *((path, 0o600, True) for path in payload_paths),
            )
            expected_ids: tuple[int, int] | None = None
            if self._expected_owner_group is not None:
                import grp
                import pwd

                owner_name, group_name = self._expected_owner_group
                pwd_api = cast(Any, pwd)
                grp_api = cast(Any, grp)
                expected_ids = (
                    pwd_api.getpwnam(owner_name).pw_uid,
                    grp_api.getgrnam(group_name).gr_gid,
                )
            for path, expected_mode, required in paths:
                if not os.path.lexists(path):
                    if required and require_payload:
                        raise FileNotFoundError(path)
                    continue
                if path.is_symlink():
                    raise ValueError("store path is symlink")
                item_stat = path.stat()
                if path == self.root:
                    if not stat.S_ISDIR(item_stat.st_mode):
                        raise ValueError("store root is not directory")
                elif not stat.S_ISREG(item_stat.st_mode):
                    raise ValueError("store payload is not regular")
                if stat.S_IMODE(item_stat.st_mode) != expected_mode:
                    raise ValueError("store permissions")
                if expected_ids is not None and (
                    item_stat.st_uid != expected_ids[0] or item_stat.st_gid != expected_ids[1]
                ):
                    raise ValueError("store owner")
        except (KeyError, OSError) as exc:
            raise PersonalPlanningStoreUnavailableError() from exc

    def _manifest_from_dict(self, value: object) -> PersonalPlanningStoreManifestV1:
        data = _wire_dict(
            value,
            {
                "format_version",
                "next_sequence",
                "record_count",
                "last_record_digest",
                "policy_id",
                "policy_fingerprint",
            },
        )
        return PersonalPlanningStoreManifestV1(
            format_version=cast(int, data["format_version"]),
            next_sequence=cast(int, data["next_sequence"]),
            record_count=cast(int, data["record_count"]),
            last_record_digest=cast(str | None, data["last_record_digest"]),
            policy_id=cast(str, data["policy_id"]),
            policy_fingerprint=cast(str, data["policy_fingerprint"]),
        )

    @staticmethod
    def _reject_duplicate_pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, item in pairs:
            if key in result:
                raise PersonalPlanningStoreCorruptError()
            result[key] = item
        return result

    def _read_manifest_unlocked(self) -> PersonalPlanningStoreManifestV1:
        try:
            raw = self.manifest_path.read_bytes()
            data = json.loads(raw.decode("utf-8"), object_pairs_hook=self._reject_duplicate_pairs)
            if _canonical_bytes(data) != raw:
                raise ValueError("manifest not canonical")
            return self._manifest_from_dict(data)
        except PersonalPlanningStoreError:
            raise
        except (OSError, UnicodeError, ValueError, TypeError, json.JSONDecodeError) as exc:
            raise PersonalPlanningStoreCorruptError() from exc

    def _event_from_dict(self, value: object) -> PersonalPlanningStoreEventV1:
        data = _wire_dict(
            value,
            {
                "event_id",
                "event_type",
                "operation_id_fingerprint",
                "intent_fingerprint",
                "previous_plan_fingerprint",
                "plan",
            },
        )
        try:
            plan = PlanningPlanV1.from_dict(data["plan"])
            return PersonalPlanningStoreEventV1(
                event_id=cast(UUID | str, data["event_id"]),
                event_type=cast(PersonalPlanningStoreRecordTypeV1 | str, data["event_type"]),
                operation_id_fingerprint=cast(str, data["operation_id_fingerprint"]),
                intent_fingerprint=cast(str, data["intent_fingerprint"]),
                previous_plan_fingerprint=cast(str | None, data["previous_plan_fingerprint"]),
                plan=plan,
            )
        except PersonalPlanningStoreCorruptError:
            raise
        except PersonalPlanningStoreError as exc:
            raise PersonalPlanningStoreCorruptError() from exc
        except (TypeError, ValueError, UnicodeError, OverflowError) as exc:
            raise PersonalPlanningStoreCorruptError() from exc

    def _envelope_from_dict(self, value: object) -> PersonalPlanningStoreEnvelopeV1:
        data = _wire_dict(
            value,
            {
                "sequence",
                "record_type",
                "record",
                "previous_record_digest",
                "event_fingerprint",
                "record_digest",
            },
        )
        record = self._event_from_dict(data["record"])
        return PersonalPlanningStoreEnvelopeV1(
            sequence=cast(int, data["sequence"]),
            record_type=cast(PersonalPlanningStoreRecordTypeV1 | str, data["record_type"]),
            record=record,
            previous_record_digest=cast(str | None, data["previous_record_digest"]),
            event_fingerprint=cast(str, data["event_fingerprint"]),
            record_digest=cast(str, data["record_digest"]),
        )

    def _read_verified_unlocked(self) -> PersonalPlanningStoreVerifiedSnapshotV1:
        if self.root.is_symlink() or any(
            path.is_symlink() for path in (self.records_path, self.manifest_path)
        ):
            raise PersonalPlanningStoreCorruptError()
        self._assert_owner_only_unlocked()
        manifest = self._read_manifest_unlocked()
        try:
            raw = self.records_path.read_bytes()
        except FileNotFoundError as exc:
            raise PersonalPlanningStoreCorruptError() from exc
        except OSError as exc:
            raise PersonalPlanningStoreUnavailableError() from exc
        if not raw:
            if manifest.record_count != 0 or manifest.last_record_digest is not None:
                raise PersonalPlanningStoreCorruptError()
            return PersonalPlanningStoreVerifiedSnapshotV1(manifest, ())
        if raw.startswith(b"\xef\xbb\xbf") or not raw.endswith(b"\n"):
            raise PersonalPlanningStoreCorruptError()
        envelopes: list[PersonalPlanningStoreEnvelopeV1] = []
        previous: str | None = None
        seen_event_ids: set[UUID] = set()
        seen_operations: set[str] = set()
        for line in raw.splitlines(keepends=True):
            if not line.endswith(b"\n") or line.endswith(b"\r\n"):
                raise PersonalPlanningStoreCorruptError()
            payload = line[:-1]
            if not payload or len(payload) > PERSONAL_PLANNING_STORE_MAX_RECORD_BYTES:
                raise PersonalPlanningStoreCorruptError()
            try:
                data = json.loads(
                    payload.decode("utf-8"),
                    object_pairs_hook=self._reject_duplicate_pairs,
                )
                envelope = self._envelope_from_dict(data)
                if _canonical_bytes(data) != payload:
                    raise ValueError("record not canonical")
                if envelope.expected_event_fingerprint != envelope.event_fingerprint:
                    raise ValueError("event fingerprint")
                if envelope.expected_record_digest != envelope.record_digest:
                    raise ValueError("record digest")
                if envelope.sequence != len(envelopes) + 1:
                    raise ValueError("sequence")
                if envelope.previous_record_digest != previous:
                    raise ValueError("chain")
                event_id = cast(UUID, envelope.record.event_id)
                operation = envelope.record.operation_id_fingerprint
                if event_id in seen_event_ids or operation in seen_operations:
                    raise ValueError("duplicate identity")
            except PersonalPlanningStoreError:
                raise
            except (
                TypeError,
                ValueError,
                UnicodeError,
                OverflowError,
                json.JSONDecodeError,
            ) as exc:
                raise PersonalPlanningStoreCorruptError() from exc
            envelopes.append(envelope)
            seen_event_ids.add(event_id)
            seen_operations.add(operation)
            previous = envelope.record_digest
        if (
            manifest.record_count != len(envelopes)
            or manifest.next_sequence != len(envelopes) + 1
            or manifest.last_record_digest != previous
        ):
            raise PersonalPlanningStoreCorruptError()
        if len(envelopes) > PERSONAL_PLANNING_STORE_MAX_RECORDS:
            raise PersonalPlanningStoreCorruptError()
        _replay_state(tuple(envelopes))
        return PersonalPlanningStoreVerifiedSnapshotV1(manifest, tuple(envelopes))

    def read_verified_snapshot(self) -> PersonalPlanningStoreVerifiedSnapshotV1:
        try:
            with _StoreLock(self.lock_path):
                return self._read_verified_unlocked()
        except PersonalPlanningStoreError:
            raise
        except (OSError, ValueError, UnicodeError, RecursionError) as exc:
            raise PersonalPlanningStoreUnavailableError() from exc

    verified_snapshot = read_verified_snapshot

    def read_events(self) -> tuple[PersonalPlanningStoreEnvelopeV1, ...]:
        return self.read_verified_snapshot().envelopes

    read_records = read_events

    def read_manifest(self) -> PersonalPlanningStoreManifestV1:
        return self.read_verified_snapshot().manifest

    def read_state(self) -> PersonalPlanningStoreStateV1:
        snapshot = self.read_verified_snapshot()
        return _replay_state(snapshot.envelopes)

    def current_plan(self) -> PlanningPlanV1 | None:
        return self.read_state().current_plan

    read_current = current_plan

    def _find_operation_unlocked(
        self,
        verified: PersonalPlanningStoreVerifiedSnapshotV1,
        operation_fingerprint: str,
    ) -> PersonalPlanningStoreEnvelopeV1 | None:
        return next(
            (
                envelope
                for envelope in verified.envelopes
                if envelope.record.operation_id_fingerprint == operation_fingerprint
            ),
            None,
        )

    def _append_event_unlocked(
        self,
        verified: PersonalPlanningStoreVerifiedSnapshotV1,
        event: PersonalPlanningStoreEventV1,
    ) -> PersonalPlanningStoreEnvelopeV1:
        for existing in verified.envelopes:
            if existing.record.operation_id_fingerprint != event.operation_id_fingerprint:
                continue
            if existing.record.intent_fingerprint == event.intent_fingerprint:
                return existing
            raise PersonalPlanningStoreIdempotencyConflictError()
        if verified.manifest.record_count >= PERSONAL_PLANNING_STORE_MAX_RECORDS:
            raise PersonalPlanningStoreUnavailableError()
        sequence = verified.manifest.next_sequence
        event_fingerprint = personal_planning_store_hash(event.as_dict())
        unsigned = {
            "sequence": sequence,
            "record_type": cast(PersonalPlanningStoreRecordTypeV1, event.event_type).value,
            "record": event.as_dict(),
            "previous_record_digest": verified.manifest.last_record_digest,
            "event_fingerprint": event_fingerprint,
        }
        envelope = PersonalPlanningStoreEnvelopeV1(
            sequence=sequence,
            record_type=event.event_type,
            record=event,
            previous_record_digest=verified.manifest.last_record_digest,
            event_fingerprint=event_fingerprint,
            record_digest=personal_planning_store_hash(unsigned),
        )
        line = _canonical_bytes(envelope.as_dict()) + b"\n"
        if len(line) > PERSONAL_PLANNING_STORE_MAX_RECORD_BYTES:
            raise PersonalPlanningStoreUnavailableError()
        self._append_bytes_durable(self.records_path, line)
        self._write_manifest_atomic(
            PersonalPlanningStoreManifestV1(
                PERSONAL_PLANNING_STORE_FORMAT_VERSION,
                sequence + 1,
                sequence,
                envelope.record_digest,
                PLANNING_POLICY_ID,
                PLANNING_POLICY_FINGERPRINT,
            )
        )
        reread = self._read_verified_unlocked()
        if not reread.envelopes or reread.envelopes[-1] != envelope:
            raise PersonalPlanningStoreUnavailableError()
        return envelope

    @staticmethod
    def _normalize_request_timestamp(value: datetime) -> datetime:
        if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
            raise PersonalPlanningStoreInvalidRequestError()
        return value.astimezone(UTC)

    @staticmethod
    def _selection(
        proposal: PlanningProposalV1,
        selected_item_ids: tuple[str, ...] | None,
        item_order: tuple[str, ...] | None,
    ) -> tuple[tuple[str, ...], tuple[str, ...]]:
        item_ids = tuple(item.item_id for item in proposal.items)
        selected = item_ids if selected_item_ids is None else selected_item_ids
        if type(selected) is not tuple:
            raise PersonalPlanningStoreInvalidRequestError()
        normalized_selected = tuple(_item_id(item_id) for item_id in selected)
        if len(set(normalized_selected)) != len(normalized_selected) or not set(
            normalized_selected
        ) <= set(item_ids):
            raise PersonalPlanningStoreInvalidRequestError()
        if item_order is None:
            normalized_order = tuple(
                item_id
                for item_id in proposal.suggested_order
                if item_id in set(normalized_selected)
            )
        else:
            if type(item_order) is not tuple:
                raise PersonalPlanningStoreInvalidRequestError()
            normalized_order = tuple(_item_id(item_id) for item_id in item_order)
        if set(normalized_order) != set(normalized_selected) or len(normalized_order) != len(
            normalized_selected
        ):
            raise PersonalPlanningStoreInvalidRequestError()
        return normalized_selected, normalized_order

    @staticmethod
    def _plan_for_proposal(
        proposal: PlanningProposalV1,
        pack: PlanningContextPackV1,
        *,
        plan_id: UUID,
        revision: int,
        as_of: datetime,
        selected_item_ids: tuple[str, ...],
        item_order: tuple[str, ...],
    ) -> PlanningPlanV1:
        core = {
            "plan_version": "1",
            "plan_id": str(plan_id),
            "revision": revision,
            "as_of": _format_timestamp(as_of),
            "source_pack_fingerprint": proposal.source_pack_fingerprint,
            "provider_envelope_fingerprint": proposal.provider_envelope_fingerprint,
            "provider_result_fingerprint": proposal.provider_result_fingerprint,
            "proposal_fingerprint": proposal.proposal_fingerprint,
            "policy_id": PLANNING_POLICY_ID,
            "policy_fingerprint": PLANNING_POLICY_FINGERPRINT,
            "start_local": pack.start_local,
            "end_local": pack.end_local,
            "timezone": pack.timezone,
            "capacity": [entry.as_dict() for entry in pack.capacity],
            "fixed_windows": [window.as_dict() for window in pack.fixed_windows],
            "items": [item.as_dict() for item in proposal.items],
            "selected_item_ids": list(selected_item_ids),
            "item_order": list(item_order),
        }
        return PlanningPlanV1(
            plan_version="1",
            plan_id=plan_id,
            revision=revision,
            as_of=as_of,
            source_pack_fingerprint=proposal.source_pack_fingerprint,
            provider_envelope_fingerprint=proposal.provider_envelope_fingerprint,
            provider_result_fingerprint=proposal.provider_result_fingerprint,
            proposal_fingerprint=proposal.proposal_fingerprint,
            policy_id=PLANNING_POLICY_ID,
            policy_fingerprint=PLANNING_POLICY_FINGERPRINT,
            start_local=pack.start_local,
            end_local=pack.end_local,
            timezone=pack.timezone,
            capacity=pack.capacity,
            fixed_windows=pack.fixed_windows,
            items=proposal.items,
            selected_item_ids=selected_item_ids,
            item_order=item_order,
            plan_fingerprint=personal_planning_store_hash(core),
        )

    @staticmethod
    def _replace_plan_items(
        current: PlanningPlanV1,
        items: tuple[PlanningItemV1, ...],
    ) -> tuple[PlanningItemV1, ...]:
        if type(items) is not tuple or len(items) != len(current.items):
            raise PersonalPlanningStoreInvalidRequestError()
        current_by_id = {item.item_id: item for item in current.items}
        replacement_by_id = {item.item_id: item for item in items}
        if set(current_by_id) != set(replacement_by_id):
            raise PersonalPlanningStoreInvalidRequestError()
        for item_id, replacement in replacement_by_id.items():
            original = current_by_id[item_id]
            if (
                replacement.kind != original.kind
                or replacement.goal_refs != original.goal_refs
                or replacement.action_refs != original.action_refs
            ):
                raise PersonalPlanningStoreSourceChangedError()
        return tuple(items)

    def accept(
        self,
        proposal: PlanningProposalV1,
        *,
        context_pack: PlanningContextPackV1,
        operation_id: str | UUID,
        selected_item_ids: tuple[str, ...] | None = None,
        item_order: tuple[str, ...] | None = None,
        accepted_at: datetime | None = None,
        expected_current_plan_fingerprint: str | None = None,
    ) -> PlanningPlanV1:
        """Accept or replace one proposal after exact owner selection/order."""

        if (
            type(proposal) is not PlanningProposalV1
            or type(context_pack) is not PlanningContextPackV1
        ):
            raise PersonalPlanningStoreInvalidRequestError()
        try:
            pack = validate_planning_context_pack(context_pack)
            if proposal.source_pack_fingerprint != pack.pack_fingerprint:
                raise PersonalPlanningStoreSourceChangedError()
            validated_proposal = validate_planning_proposal(proposal, pack=pack)
        except (TypeError, ValueError) as exc:
            raise PersonalPlanningStoreInvalidRequestError() from exc
        if str(validated_proposal.result_state) != "proposal":
            raise PersonalPlanningStoreSourceChangedError()
        selected, order = self._selection(validated_proposal, selected_item_ids, item_order)
        operation_fp = _operation_fingerprint(operation_id)
        accepted_time = (
            self._normalize_request_timestamp(self._clock())
            if accepted_at is None
            else self._normalize_request_timestamp(accepted_at)
        )
        expected_prior = (
            None
            if expected_current_plan_fingerprint is None
            else _raw_hash(
                expected_current_plan_fingerprint,
                invalid=PersonalPlanningStoreInvalidRequestError,
            )
        )
        request_intent = personal_planning_store_hash(
            {
                "operation": operation_fp,
                "kind": "accept",
                "source_pack_fingerprint": validated_proposal.source_pack_fingerprint,
                "proposal_fingerprint": validated_proposal.proposal_fingerprint,
                "selected_item_ids": list(selected),
                "item_order": list(order),
                "accepted_at": _format_timestamp(accepted_time),
                "expected_current_plan_fingerprint": expected_prior,
            }
        )
        try:
            with _StoreLock(self.lock_path):
                verified = self._read_verified_unlocked()
                existing = self._find_operation_unlocked(verified, operation_fp)
                if existing is not None:
                    if existing.record.intent_fingerprint != request_intent:
                        raise PersonalPlanningStoreIdempotencyConflictError()
                    return existing.record.plan
                current = _replay_state(verified.envelopes).current_plan
                actual_previous = None if current is None else current.plan_fingerprint
                if actual_previous != expected_prior:
                    raise PersonalPlanningStoreStateConflictError()
                plan = self._plan_for_proposal(
                    validated_proposal,
                    pack,
                    plan_id=uuid7(),
                    revision=1,
                    as_of=accepted_time,
                    selected_item_ids=selected,
                    item_order=order,
                )
                event_type = (
                    PersonalPlanningStoreRecordTypeV1.PLAN_ACCEPTED
                    if current is None
                    else PersonalPlanningStoreRecordTypeV1.PLAN_REPLACED
                )
                event = PersonalPlanningStoreEventV1(
                    event_id=uuid7(),
                    event_type=event_type,
                    operation_id_fingerprint=operation_fp,
                    intent_fingerprint=request_intent,
                    previous_plan_fingerprint=actual_previous,
                    plan=plan,
                )
                return self._append_event_unlocked(verified, event).record.plan
        except PersonalPlanningStoreError:
            raise
        except (OSError, ValueError, UnicodeError, RecursionError) as exc:
            raise PersonalPlanningStoreUnavailableError() from exc

    accept_plan = accept

    def edit(
        self,
        *,
        items: tuple[PlanningItemV1, ...],
        selected_item_ids: tuple[str, ...],
        item_order: tuple[str, ...],
        operation_id: str | UUID,
        expected_current_plan_fingerprint: str,
        edited_at: datetime | None = None,
    ) -> PlanningPlanV1:
        """Append one owner edit while preserving exact item provenance."""

        operation_fp = _operation_fingerprint(operation_id)
        expected_prior = _raw_hash(
            expected_current_plan_fingerprint,
            invalid=PersonalPlanningStoreInvalidRequestError,
        )
        if type(items) is not tuple or any(type(item) is not PlanningItemV1 for item in items):
            raise PersonalPlanningStoreInvalidRequestError()
        if type(selected_item_ids) is not tuple or type(item_order) is not tuple:
            raise PersonalPlanningStoreInvalidRequestError()
        selected = tuple(_item_id(item_id) for item_id in selected_item_ids)
        order = tuple(_item_id(item_id) for item_id in item_order)
        edited_time = (
            self._normalize_request_timestamp(self._clock())
            if edited_at is None
            else self._normalize_request_timestamp(edited_at)
        )
        request_intent = personal_planning_store_hash(
            {
                "operation": operation_fp,
                "kind": "edit",
                "previous_plan_fingerprint": expected_prior,
                "items": [item.as_dict() for item in items],
                "selected_item_ids": list(selected),
                "item_order": list(order),
                "edited_at": _format_timestamp(edited_time),
            }
        )
        try:
            with _StoreLock(self.lock_path):
                verified = self._read_verified_unlocked()
                existing = self._find_operation_unlocked(verified, operation_fp)
                if existing is not None:
                    if existing.record.intent_fingerprint != request_intent:
                        raise PersonalPlanningStoreIdempotencyConflictError()
                    return existing.record.plan
                current = _replay_state(verified.envelopes).current_plan
                if current is None or current.plan_fingerprint != expected_prior:
                    raise PersonalPlanningStoreStateConflictError()
                replacement_items = self._replace_plan_items(current, items)
                core = {
                    "plan_version": "1",
                    "plan_id": str(current.plan_id),
                    "revision": current.revision + 1,
                    "as_of": _format_timestamp(edited_time),
                    "source_pack_fingerprint": current.source_pack_fingerprint,
                    "provider_envelope_fingerprint": current.provider_envelope_fingerprint,
                    "provider_result_fingerprint": current.provider_result_fingerprint,
                    "proposal_fingerprint": current.proposal_fingerprint,
                    "policy_id": PLANNING_POLICY_ID,
                    "policy_fingerprint": PLANNING_POLICY_FINGERPRINT,
                    "start_local": current.start_local,
                    "end_local": current.end_local,
                    "timezone": current.timezone,
                    "capacity": [entry.as_dict() for entry in current.capacity],
                    "fixed_windows": [window.as_dict() for window in current.fixed_windows],
                    "items": [item.as_dict() for item in replacement_items],
                    "selected_item_ids": list(selected),
                    "item_order": list(order),
                }
                plan = PlanningPlanV1(
                    plan_version="1",
                    plan_id=current.plan_id,
                    revision=current.revision + 1,
                    as_of=edited_time,
                    source_pack_fingerprint=current.source_pack_fingerprint,
                    provider_envelope_fingerprint=current.provider_envelope_fingerprint,
                    provider_result_fingerprint=current.provider_result_fingerprint,
                    proposal_fingerprint=current.proposal_fingerprint,
                    policy_id=PLANNING_POLICY_ID,
                    policy_fingerprint=PLANNING_POLICY_FINGERPRINT,
                    start_local=current.start_local,
                    end_local=current.end_local,
                    timezone=current.timezone,
                    capacity=current.capacity,
                    fixed_windows=current.fixed_windows,
                    items=replacement_items,
                    selected_item_ids=selected,
                    item_order=order,
                    plan_fingerprint=personal_planning_store_hash(core),
                )
                event = PersonalPlanningStoreEventV1(
                    event_id=uuid7(),
                    event_type=PersonalPlanningStoreRecordTypeV1.PLAN_EDITED,
                    operation_id_fingerprint=operation_fp,
                    intent_fingerprint=request_intent,
                    previous_plan_fingerprint=current.plan_fingerprint,
                    plan=plan,
                )
                return self._append_event_unlocked(verified, event).record.plan
        except PersonalPlanningStoreError:
            raise
        except (OSError, ValueError, UnicodeError, RecursionError) as exc:
            raise PersonalPlanningStoreUnavailableError() from exc

    edit_plan = edit

    def _append_bytes_durable(self, path: Path, payload: bytes) -> None:
        flags = os.O_WRONLY | os.O_APPEND | os.O_CREAT | getattr(os, "O_BINARY", 0)
        if os.name != "nt":
            flags |= getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(path, flags, 0o600)
        try:
            view = memoryview(payload)
            while view:
                written = os.write(descriptor, view)
                if written <= 0:
                    raise OSError("append made no progress")
                view = view[written:]
            os.fsync(descriptor)
        finally:
            os.close(descriptor)

    def _write_manifest_atomic(self, manifest: PersonalPlanningStoreManifestV1) -> None:
        self._write_bytes_atomic(self.manifest_path, _canonical_bytes(manifest.as_dict()))

    def _write_bytes_atomic(self, path: Path, payload: bytes) -> None:
        temporary = self.root / f".{path.name}.{uuid7()}.tmp"
        descriptor: int | None = None
        try:
            descriptor = os.open(
                temporary,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0),
                0o600,
            )
            view = memoryview(payload)
            while view:
                written = os.write(descriptor, view)
                if written <= 0:
                    raise OSError("atomic write made no progress")
                view = view[written:]
            os.fsync(descriptor)
            os.close(descriptor)
            descriptor = None
            os.replace(temporary, path)
            os.chmod(path, 0o600)
            self._fsync_directory()
        finally:
            if descriptor is not None:
                os.close(descriptor)
            try:
                temporary.unlink()
            except FileNotFoundError:
                pass
            except OSError:
                pass

    def _fsync_directory(self) -> None:
        if os.name == "nt":
            return
        descriptor = os.open(self.root, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)


def derive_personal_planning_store_root(
    env_file: Path | os.PathLike[str] | None,
) -> Path | None:
    """Derive the additive operational store from an explicit existing env file."""

    if env_file is None:
        return None
    candidate = Path(env_file).expanduser()
    try:
        if not candidate.is_absolute() or candidate.is_symlink() or not candidate.is_file():
            return None
        selected = candidate.resolve(strict=True)
        parent = selected.parent
        for item in (parent, *parent.parents):
            if item.is_symlink():
                return None
        prospective = parent / PERSONAL_PLANNING_STORE_PARENT_NAME
        root = prospective / PERSONAL_PLANNING_STORE_DIRECTORY_NAME
        if prospective.exists() and (prospective.is_symlink() or not prospective.is_dir()):
            return None
        if root.exists() and (root.is_symlink() or not root.is_dir()):
            return None
        return root
    except OSError, RuntimeError, ValueError:
        return None


PersonalPlanningStore = PersonalPlanningOperationalStore
PlanningOperationalStore = PersonalPlanningOperationalStore
PlanningPlanStoreManifestV1 = PersonalPlanningStoreManifestV1
PlanningPlanStoreStateV1 = PersonalPlanningStoreStateV1
derive_planning_store_root = derive_personal_planning_store_root


__all__ = [
    "PERSONAL_PLANNING_STORE_DIRECTORY_NAME",
    "PERSONAL_PLANNING_STORE_FORMAT_VERSION",
    "PERSONAL_PLANNING_STORE_LOCK_FILE_NAME",
    "PERSONAL_PLANNING_STORE_MANIFEST_FILE_NAME",
    "PERSONAL_PLANNING_STORE_MAX_OPERATION_ID_BYTES",
    "PERSONAL_PLANNING_STORE_MAX_PLAN_BYTES",
    "PERSONAL_PLANNING_STORE_MAX_RECORDS",
    "PERSONAL_PLANNING_STORE_MAX_RECORD_BYTES",
    "PERSONAL_PLANNING_STORE_PARENT_NAME",
    "PERSONAL_PLANNING_STORE_RECORD_FILE_NAME",
    "PersonalPlanningOperationalStore",
    "PersonalPlanningStore",
    "PersonalPlanningStoreCapacityConflictError",
    "PersonalPlanningStoreCorruptError",
    "PersonalPlanningStoreEnvelopeV1",
    "PersonalPlanningStoreError",
    "PersonalPlanningStoreEventV1",
    "PersonalPlanningStoreIdempotencyConflictError",
    "PersonalPlanningStoreInvalidRequestError",
    "PersonalPlanningStoreManifestV1",
    "PersonalPlanningStoreRecordType",
    "PersonalPlanningStoreRecordTypeV1",
    "PersonalPlanningStoreSourceChangedError",
    "PersonalPlanningStoreStateConflictError",
    "PersonalPlanningStoreStateV1",
    "PersonalPlanningStoreUnavailableError",
    "PersonalPlanningStoreVerifiedSnapshotV1",
    "PlanningOperationalStore",
    "PlanningPlanStoreManifestV1",
    "PlanningPlanStoreStateV1",
    "PlanningPlanV1",
    "derive_personal_planning_store_root",
    "derive_planning_store_root",
    "personal_planning_store_hash",
]
