"""Provider-free Stage 15.2 operational profile lifecycle.

The store is intentionally separate from the canonical vault.  It accepts
already validated Stage 15 DTOs, persists only bounded identity/profile
references, and fails closed on any integrity, path, permission, or state
machine violation.  It has no vault reader, provider, network, Web, or UI
capability.
"""

from __future__ import annotations

import errno
import hashlib
import json
import os
import re
import stat
import uuid
from collections.abc import Callable, Sequence
from contextlib import AbstractContextManager
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any, Final, cast
from uuid import UUID

from second_brain.application.adaptive_cognitive_twin import (
    ADAPTIVE_POLICY_FINGERPRINT,
    ADAPTIVE_PROFILE_POLICY_ID,
    AdaptiveCognitiveTwinInputError,
    AdaptiveHashV1,
    AdaptiveSufficiencyStateV1,
    Stage15AdaptiveProfileV1,
    Stage15CandidateV1,
    canonical_adaptive_json_bytes,
    validate_adaptive_candidate,
    validate_adaptive_hash,
    validate_adaptive_policy,
    validate_adaptive_source_snapshot,
)
from second_brain.domain.models import parse_rfc3339, parse_uuid7

ADAPTIVE_PROFILE_STORE_FORMAT_VERSION: Final[int] = 1
ADAPTIVE_PROFILE_STORE_POLICY_ID: Final[str] = ADAPTIVE_PROFILE_POLICY_ID
ADAPTIVE_PROFILE_STORE_POLICY_FINGERPRINT: Final[AdaptiveHashV1] = ADAPTIVE_POLICY_FINGERPRINT
ADAPTIVE_PROFILE_STORE_RECORD_FILE_NAME: Final[str] = "profiles.jsonl"
ADAPTIVE_PROFILE_STORE_MANIFEST_FILE_NAME: Final[str] = "manifest.json"
ADAPTIVE_PROFILE_STORE_LOCK_FILE_NAME: Final[str] = ".store.lock"
ADAPTIVE_PROFILE_STORE_DIRECTORY_NAME: Final[str] = "adaptive-cognitive-twin"
ADAPTIVE_PROFILE_STORE_PARENT_NAME: Final[str] = "prospective-audit"
ADAPTIVE_PROFILE_STORE_MAX_RECORD_BYTES: Final[int] = 16 * 1024
ADAPTIVE_PROFILE_STORE_MAX_RECORDS: Final[int] = 4096
ADAPTIVE_PROFILE_STORE_MAX_PROFILE_RECORDS_PER_GOAL: Final[int] = 128
ADAPTIVE_PROFILE_STORE_MAX_OPERATION_ID_BYTES: Final[int] = 256
ADAPTIVE_PROFILE_STORE_MAX_REASON_BYTES: Final[int] = 64

_HASH_PATTERN: Final[re.Pattern[str]] = re.compile(r"sha256:[0-9a-f]{64}\Z", re.ASCII)


class AdaptiveStoreEventTypeV1(StrEnum):
    """The only append-only lifecycle event types in Stage 15.2."""

    CANDIDATE_REVIEWED = "candidate_reviewed"
    CANDIDATE_REJECTED = "candidate_rejected"
    PROFILE_ACTIVATED = "profile_activated"
    PROFILE_SUPERSEDED = "profile_superseded"
    PROFILE_REVERTED = "profile_reverted"
    EVALUATION_RECORDED = "evaluation_recorded"


class AdaptiveStoreReasonV1(StrEnum):
    """Closed safe reasons retained by lifecycle events."""

    OWNER_REJECTED = "owner_rejected"


class AdaptiveStoreErrorCodeV1(StrEnum):
    """Safe operational error taxonomy; no paths or raw payloads are exposed."""

    INVALID_REQUEST = "ADAPTIVE_COGNITIVE_TWIN_INVALID_REQUEST"
    STORE_UNAVAILABLE = "ADAPTIVE_COGNITIVE_TWIN_STORE_UNAVAILABLE"
    STORE_CORRUPT = "ADAPTIVE_COGNITIVE_TWIN_STORE_CORRUPT"
    IDEMPOTENCY_CONFLICT = "ADAPTIVE_COGNITIVE_TWIN_IDEMPOTENCY_CONFLICT"
    SOURCE_CHANGED = "ADAPTIVE_COGNITIVE_TWIN_SOURCE_CHANGED"
    CANDIDATE_NOT_REVIEWED = "ADAPTIVE_COGNITIVE_TWIN_CANDIDATE_NOT_REVIEWED"
    ACTIVE_PROFILE_CONFLICT = "ADAPTIVE_COGNITIVE_TWIN_ACTIVE_PROFILE_CONFLICT"
    PROFILE_NOT_FOUND = "ADAPTIVE_COGNITIVE_TWIN_PROFILE_NOT_FOUND"
    STATE_CONFLICT = "ADAPTIVE_COGNITIVE_TWIN_STATE_CONFLICT"


_ERROR_MESSAGES: Final[dict[AdaptiveStoreErrorCodeV1, str]] = {
    AdaptiveStoreErrorCodeV1.INVALID_REQUEST: "adaptive cognitive twin request is invalid",
    AdaptiveStoreErrorCodeV1.STORE_UNAVAILABLE: "adaptive cognitive twin store is unavailable",
    AdaptiveStoreErrorCodeV1.STORE_CORRUPT: "adaptive cognitive twin store is corrupt",
    AdaptiveStoreErrorCodeV1.IDEMPOTENCY_CONFLICT: (
        "adaptive cognitive twin operation conflicts with existing record"
    ),
    AdaptiveStoreErrorCodeV1.SOURCE_CHANGED: "adaptive cognitive twin source changed",
    AdaptiveStoreErrorCodeV1.CANDIDATE_NOT_REVIEWED: (
        "adaptive cognitive twin candidate was not reviewed"
    ),
    AdaptiveStoreErrorCodeV1.ACTIVE_PROFILE_CONFLICT: (
        "adaptive cognitive twin active profile conflicts with the operation"
    ),
    AdaptiveStoreErrorCodeV1.PROFILE_NOT_FOUND: "adaptive cognitive twin profile was not found",
    AdaptiveStoreErrorCodeV1.STATE_CONFLICT: "adaptive cognitive twin state conflicts",
}


class AdaptiveCognitiveTwinStoreError(RuntimeError):
    """Fixed-code store error with no raw exception details."""

    def __init__(self, code: AdaptiveStoreErrorCodeV1 | str) -> None:
        try:
            normalized = AdaptiveStoreErrorCodeV1(code)
        except TypeError, ValueError:
            normalized = AdaptiveStoreErrorCodeV1.STORE_UNAVAILABLE
        self.code = normalized.value
        self.message = _ERROR_MESSAGES[normalized]
        super().__init__(self.message)

    def as_dict(self) -> dict[str, str]:
        return {"code": self.code, "message": self.message}


class AdaptiveCognitiveTwinStoreInvalidRequestError(AdaptiveCognitiveTwinStoreError):
    def __init__(self) -> None:
        super().__init__(AdaptiveStoreErrorCodeV1.INVALID_REQUEST)


class AdaptiveCognitiveTwinStoreUnavailableError(AdaptiveCognitiveTwinStoreError):
    def __init__(self) -> None:
        super().__init__(AdaptiveStoreErrorCodeV1.STORE_UNAVAILABLE)


class AdaptiveCognitiveTwinStoreCorruptError(AdaptiveCognitiveTwinStoreError):
    def __init__(self) -> None:
        super().__init__(AdaptiveStoreErrorCodeV1.STORE_CORRUPT)


class AdaptiveCognitiveTwinStoreIdempotencyConflictError(AdaptiveCognitiveTwinStoreError):
    def __init__(self) -> None:
        super().__init__(AdaptiveStoreErrorCodeV1.IDEMPOTENCY_CONFLICT)


class AdaptiveCognitiveTwinStoreSourceChangedError(AdaptiveCognitiveTwinStoreError):
    def __init__(self) -> None:
        super().__init__(AdaptiveStoreErrorCodeV1.SOURCE_CHANGED)


class AdaptiveCognitiveTwinStoreCandidateNotReviewedError(AdaptiveCognitiveTwinStoreError):
    def __init__(self) -> None:
        super().__init__(AdaptiveStoreErrorCodeV1.CANDIDATE_NOT_REVIEWED)


class AdaptiveCognitiveTwinStoreActiveProfileConflictError(AdaptiveCognitiveTwinStoreError):
    def __init__(self) -> None:
        super().__init__(AdaptiveStoreErrorCodeV1.ACTIVE_PROFILE_CONFLICT)


class AdaptiveCognitiveTwinStoreProfileNotFoundError(AdaptiveCognitiveTwinStoreError):
    def __init__(self) -> None:
        super().__init__(AdaptiveStoreErrorCodeV1.PROFILE_NOT_FOUND)


class AdaptiveCognitiveTwinStoreStateConflictError(AdaptiveCognitiveTwinStoreError):
    def __init__(self) -> None:
        super().__init__(AdaptiveStoreErrorCodeV1.STATE_CONFLICT)


def _canonical_json(value: object) -> str:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError, UnicodeError, OverflowError) as exc:
        raise AdaptiveCognitiveTwinStoreCorruptError() from exc


def adaptive_store_hash_json(value: object) -> AdaptiveHashV1:
    return "sha256:" + hashlib.sha256(canonical_adaptive_json_bytes(value)).hexdigest()


def fingerprint_adaptive_operation_id(value: object) -> AdaptiveHashV1:
    """Hash a bounded opaque operation id without retaining it."""

    if (
        type(value) is not str
        or not value
        or _utf8_size(value) > ADAPTIVE_PROFILE_STORE_MAX_OPERATION_ID_BYTES
    ):
        raise AdaptiveCognitiveTwinStoreInvalidRequestError()
    if any(ord(char) < 0x20 or ord(char) == 0x7F for char in value):
        raise AdaptiveCognitiveTwinStoreInvalidRequestError()
    try:
        encoded = value.encode("utf-8")
    except UnicodeError as exc:
        raise AdaptiveCognitiveTwinStoreInvalidRequestError() from exc
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def _utf8_size(value: str) -> int:
    try:
        return len(value.encode("utf-8"))
    except UnicodeError as exc:
        raise AdaptiveCognitiveTwinStoreInvalidRequestError() from exc


def _enum[EnumT: StrEnum](enum_type: type[EnumT], value: object, label: str) -> EnumT:
    if isinstance(value, enum_type):
        return value
    if type(value) is not str:
        raise AdaptiveCognitiveTwinStoreCorruptError()
    try:
        return enum_type(value)
    except TypeError, ValueError:
        del label
        raise AdaptiveCognitiveTwinStoreCorruptError() from None


def _uuid(value: object, label: str) -> UUID:
    try:
        return parse_uuid7(value)
    except TypeError, ValueError, OverflowError:
        del label
        raise AdaptiveCognitiveTwinStoreCorruptError() from None


def _timestamp(value: object, label: str) -> datetime:
    try:
        parsed = parse_rfc3339(value)
    except TypeError, ValueError, OverflowError:
        del label
        raise AdaptiveCognitiveTwinStoreCorruptError() from None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        del label
        raise AdaptiveCognitiveTwinStoreCorruptError()
    if type(value) is str and value != _format_timestamp(parsed):
        del label
        raise AdaptiveCognitiveTwinStoreCorruptError()
    return parsed.astimezone(UTC)


def _format_timestamp(value: datetime) -> str:
    rendered = value.astimezone(UTC).isoformat(
        timespec="microseconds" if value.microsecond else "seconds"
    )
    return rendered.removesuffix("+00:00") + "Z"


def _hash(value: object, label: str) -> AdaptiveHashV1:
    try:
        return validate_adaptive_hash(value)
    except AdaptiveCognitiveTwinInputError:
        del label
        raise AdaptiveCognitiveTwinStoreCorruptError() from None


def _optional_hash(value: object | None, label: str) -> AdaptiveHashV1 | None:
    if value is None:
        return None
    return _hash(value, label)


def _safe_reason(value: object | None) -> AdaptiveStoreReasonV1 | None:
    if value is None:
        return None
    reason = _enum(AdaptiveStoreReasonV1, value, "store reason")
    if len(reason.value.encode("utf-8")) > ADAPTIVE_PROFILE_STORE_MAX_REASON_BYTES:
        raise AdaptiveCognitiveTwinStoreCorruptError()
    return reason


def _profile_from_dict(value: object) -> Stage15AdaptiveProfileV1:
    if type(value) is not dict:
        raise AdaptiveCognitiveTwinStoreCorruptError()
    expected = {
        "profile_id",
        "contract_version",
        "profile_version",
        "goal_source_uuid",
        "goal_identity_fingerprint",
        "source_snapshot_fingerprint",
        "projection_focus",
        "interaction_mode",
        "evaluation_measure",
        "profile_policy_id",
        "profile_policy_fingerprint",
        "profile_fingerprint",
    }
    if set(value) != expected:
        raise AdaptiveCognitiveTwinStoreCorruptError()
    try:
        return Stage15AdaptiveProfileV1(
            profile_id=value["profile_id"],
            contract_version=value["contract_version"],
            profile_version=value["profile_version"],
            goal_source_uuid=value["goal_source_uuid"],
            goal_identity_fingerprint=value["goal_identity_fingerprint"],
            source_snapshot_fingerprint=value["source_snapshot_fingerprint"],
            projection_focus=value["projection_focus"],
            interaction_mode=value["interaction_mode"],
            evaluation_measure=value["evaluation_measure"],
            profile_policy_id=value["profile_policy_id"],
            profile_policy_fingerprint=value["profile_policy_fingerprint"],
            profile_fingerprint=value["profile_fingerprint"],
        )
    except (AdaptiveCognitiveTwinInputError, TypeError, ValueError, OverflowError) as exc:
        raise AdaptiveCognitiveTwinStoreCorruptError() from exc


@dataclass(frozen=True, slots=True)
class AdaptiveStoreEventV1:
    """One bounded typed lifecycle record; raw candidate/source bodies are absent."""

    event_id: UUID | str
    event_type: AdaptiveStoreEventTypeV1 | str
    operation_id_fingerprint: AdaptiveHashV1
    created_at: datetime | str
    goal_source_uuid: UUID | str
    goal_identity_fingerprint: AdaptiveHashV1
    source_snapshot_fingerprint: AdaptiveHashV1 | None = None
    candidate_fingerprint: AdaptiveHashV1 | None = None
    candidate_status: AdaptiveSufficiencyStateV1 | str | None = None
    profile: Stage15AdaptiveProfileV1 | None = None
    previous_profile_id: UUID | str | None = None
    previous_profile_fingerprint: AdaptiveHashV1 | None = None
    reason_code: AdaptiveStoreReasonV1 | str | None = None
    evaluation_fingerprint: AdaptiveHashV1 | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "event_id", _uuid(self.event_id, "event id"))
        object.__setattr__(
            self,
            "event_type",
            _enum(AdaptiveStoreEventTypeV1, self.event_type, "event type"),
        )
        object.__setattr__(
            self,
            "operation_id_fingerprint",
            _hash(self.operation_id_fingerprint, "operation fingerprint"),
        )
        object.__setattr__(self, "created_at", _timestamp(self.created_at, "created_at"))
        object.__setattr__(self, "goal_source_uuid", _uuid(self.goal_source_uuid, "Goal UUID"))
        object.__setattr__(
            self,
            "goal_identity_fingerprint",
            _hash(self.goal_identity_fingerprint, "Goal identity fingerprint"),
        )
        object.__setattr__(
            self,
            "source_snapshot_fingerprint",
            _optional_hash(self.source_snapshot_fingerprint, "source snapshot fingerprint"),
        )
        object.__setattr__(
            self,
            "candidate_fingerprint",
            _optional_hash(self.candidate_fingerprint, "candidate fingerprint"),
        )
        if self.candidate_status is not None:
            object.__setattr__(
                self,
                "candidate_status",
                _enum(AdaptiveSufficiencyStateV1, self.candidate_status, "candidate status"),
            )
        if self.profile is not None and type(self.profile) is not Stage15AdaptiveProfileV1:
            raise AdaptiveCognitiveTwinStoreCorruptError()
        if self.previous_profile_id is not None:
            object.__setattr__(
                self,
                "previous_profile_id",
                _uuid(self.previous_profile_id, "previous profile id"),
            )
        object.__setattr__(
            self,
            "previous_profile_fingerprint",
            _optional_hash(
                self.previous_profile_fingerprint,
                "previous profile fingerprint",
            ),
        )
        if (self.previous_profile_id is None) != (self.previous_profile_fingerprint is None):
            raise AdaptiveCognitiveTwinStoreCorruptError()
        object.__setattr__(self, "reason_code", _safe_reason(self.reason_code))
        object.__setattr__(
            self,
            "evaluation_fingerprint",
            _optional_hash(self.evaluation_fingerprint, "evaluation fingerprint"),
        )
        self._validate_shape()

    def _validate_shape(self) -> None:
        event_type = cast(AdaptiveStoreEventTypeV1, self.event_type)
        candidate_status = cast(AdaptiveSufficiencyStateV1 | None, self.candidate_status)
        if event_type is AdaptiveStoreEventTypeV1.CANDIDATE_REVIEWED:
            if (
                self.candidate_fingerprint is None
                or candidate_status is not AdaptiveSufficiencyStateV1.CANDIDATE
                or self.source_snapshot_fingerprint is None
                or self.profile is not None
                or self.reason_code is not None
                or self.evaluation_fingerprint is not None
                or self.previous_profile_id is not None
            ):
                raise AdaptiveCognitiveTwinStoreCorruptError()
        elif event_type is AdaptiveStoreEventTypeV1.CANDIDATE_REJECTED:
            if (
                self.candidate_fingerprint is None
                or candidate_status is not AdaptiveSufficiencyStateV1.CANDIDATE
                or self.source_snapshot_fingerprint is None
                or self.profile is not None
                or self.reason_code is not AdaptiveStoreReasonV1.OWNER_REJECTED
                or self.evaluation_fingerprint is not None
                or self.previous_profile_id is not None
            ):
                raise AdaptiveCognitiveTwinStoreCorruptError()
        elif event_type in {
            AdaptiveStoreEventTypeV1.PROFILE_ACTIVATED,
            AdaptiveStoreEventTypeV1.PROFILE_SUPERSEDED,
        }:
            if (
                self.candidate_fingerprint is None
                or candidate_status is not AdaptiveSufficiencyStateV1.CANDIDATE
                or self.source_snapshot_fingerprint is None
                or self.profile is None
                or self.reason_code is not None
                or self.evaluation_fingerprint is not None
            ):
                raise AdaptiveCognitiveTwinStoreCorruptError()
            if event_type is AdaptiveStoreEventTypeV1.PROFILE_ACTIVATED:
                if self.previous_profile_id is not None:
                    raise AdaptiveCognitiveTwinStoreCorruptError()
            elif self.previous_profile_id is None:
                raise AdaptiveCognitiveTwinStoreCorruptError()
        elif event_type is AdaptiveStoreEventTypeV1.PROFILE_REVERTED:
            if (
                self.profile is None
                or self.candidate_fingerprint is not None
                or self.candidate_status is not None
                or self.source_snapshot_fingerprint != self.profile.source_snapshot_fingerprint
                or self.reason_code is not None
                or self.evaluation_fingerprint is not None
                or self.previous_profile_id is None
            ):
                raise AdaptiveCognitiveTwinStoreCorruptError()
        elif event_type is AdaptiveStoreEventTypeV1.EVALUATION_RECORDED and (
            self.profile is None
            or self.evaluation_fingerprint is None
            or self.candidate_fingerprint is not None
            or self.candidate_status is not None
            or self.source_snapshot_fingerprint is not None
            or self.reason_code is not None
            or self.previous_profile_id is not None
        ):
            raise AdaptiveCognitiveTwinStoreCorruptError()

        if self.profile is not None and (
            self.profile.goal_source_uuid != self.goal_source_uuid
            or self.profile.goal_identity_fingerprint != self.goal_identity_fingerprint
        ):
            raise AdaptiveCognitiveTwinStoreCorruptError()

    def as_dict(self) -> dict[str, object]:
        return {
            "event_id": str(self.event_id),
            "event_type": cast(AdaptiveStoreEventTypeV1, self.event_type).value,
            "operation_id_fingerprint": self.operation_id_fingerprint,
            "created_at": _format_timestamp(cast(datetime, self.created_at)),
            "goal_source_uuid": str(self.goal_source_uuid),
            "goal_identity_fingerprint": self.goal_identity_fingerprint,
            "source_snapshot_fingerprint": self.source_snapshot_fingerprint,
            "candidate_fingerprint": self.candidate_fingerprint,
            "candidate_status": (
                cast(AdaptiveSufficiencyStateV1, self.candidate_status).value
                if self.candidate_status is not None
                else None
            ),
            "profile": self.profile.as_dict() if self.profile is not None else None,
            "previous_profile_id": (
                str(self.previous_profile_id) if self.previous_profile_id is not None else None
            ),
            "previous_profile_fingerprint": self.previous_profile_fingerprint,
            "reason_code": (
                cast(AdaptiveStoreReasonV1, self.reason_code).value
                if self.reason_code is not None
                else None
            ),
            "evaluation_fingerprint": self.evaluation_fingerprint,
        }


def validate_adaptive_store_event(value: object) -> AdaptiveStoreEventV1:
    if type(value) is not AdaptiveStoreEventV1:
        raise AdaptiveCognitiveTwinStoreInvalidRequestError()
    try:
        AdaptiveStoreEventV1(**cast(Any, value.as_dict()))
    except AdaptiveCognitiveTwinStoreError:
        raise
    except (TypeError, ValueError, OverflowError) as exc:
        raise AdaptiveCognitiveTwinStoreInvalidRequestError() from exc
    return value


@dataclass(frozen=True, slots=True)
class AdaptiveStoreEnvelopeV1:
    """Hash-chained immutable record envelope."""

    generation_id: UUID | str
    sequence: int
    record_type: AdaptiveStoreEventTypeV1 | str
    record: AdaptiveStoreEventV1
    previous_record_digest: AdaptiveHashV1 | None
    event_fingerprint: AdaptiveHashV1
    record_digest: AdaptiveHashV1

    def __post_init__(self) -> None:
        object.__setattr__(self, "generation_id", _uuid(self.generation_id, "generation id"))
        if (
            type(self.sequence) is not int
            or isinstance(self.sequence, bool)
            or not 1 <= self.sequence <= (1 << 64) - 1
        ):
            raise AdaptiveCognitiveTwinStoreCorruptError()
        if type(self.record) is not AdaptiveStoreEventV1:
            raise AdaptiveCognitiveTwinStoreCorruptError()
        object.__setattr__(
            self,
            "record_type",
            _enum(AdaptiveStoreEventTypeV1, self.record_type, "record type"),
        )
        if self.record_type is not self.record.event_type:
            raise AdaptiveCognitiveTwinStoreCorruptError()
        object.__setattr__(
            self,
            "previous_record_digest",
            _optional_hash(self.previous_record_digest, "previous record digest"),
        )
        object.__setattr__(
            self, "event_fingerprint", _hash(self.event_fingerprint, "event fingerprint")
        )
        object.__setattr__(self, "record_digest", _hash(self.record_digest, "record digest"))

    def unsigned_dict(self) -> dict[str, object]:
        return {
            "generation_id": str(self.generation_id),
            "sequence": self.sequence,
            "record_type": cast(AdaptiveStoreEventTypeV1, self.record_type).value,
            "record": self.record.as_dict(),
            "previous_record_digest": self.previous_record_digest,
            "event_fingerprint": self.event_fingerprint,
        }

    @property
    def expected_event_fingerprint(self) -> AdaptiveHashV1:
        return adaptive_store_hash_json(self.record.as_dict())

    @property
    def expected_record_digest(self) -> AdaptiveHashV1:
        return adaptive_store_hash_json(self.unsigned_dict())

    def as_dict(self) -> dict[str, object]:
        return {**self.unsigned_dict(), "record_digest": self.record_digest}


@dataclass(frozen=True, slots=True)
class AdaptiveStoreManifestV1:
    """Atomic manifest cross-checking the complete current generation."""

    format_version: int
    generation_id: UUID | str
    next_sequence: int
    record_count: int
    last_record_digest: AdaptiveHashV1 | None
    policy_id: str
    policy_fingerprint: AdaptiveHashV1

    def __post_init__(self) -> None:
        if (
            type(self.format_version) is not int
            or isinstance(self.format_version, bool)
            or self.format_version != ADAPTIVE_PROFILE_STORE_FORMAT_VERSION
        ):
            raise AdaptiveCognitiveTwinStoreCorruptError()
        object.__setattr__(self, "generation_id", _uuid(self.generation_id, "generation id"))
        if (
            type(self.next_sequence) is not int
            or isinstance(self.next_sequence, bool)
            or not 1 <= self.next_sequence <= (1 << 64) - 1
        ):
            raise AdaptiveCognitiveTwinStoreCorruptError()
        if (
            type(self.record_count) is not int
            or isinstance(self.record_count, bool)
            or not 0 <= self.record_count <= ADAPTIVE_PROFILE_STORE_MAX_RECORDS
            or self.next_sequence != self.record_count + 1
        ):
            raise AdaptiveCognitiveTwinStoreCorruptError()
        object.__setattr__(
            self,
            "last_record_digest",
            _optional_hash(self.last_record_digest, "last record digest"),
        )
        if (self.record_count == 0) != (self.last_record_digest is None):
            raise AdaptiveCognitiveTwinStoreCorruptError()
        if self.policy_id != ADAPTIVE_PROFILE_STORE_POLICY_ID or type(self.policy_id) is not str:
            raise AdaptiveCognitiveTwinStoreCorruptError()
        if self.policy_fingerprint != ADAPTIVE_PROFILE_STORE_POLICY_FINGERPRINT:
            raise AdaptiveCognitiveTwinStoreCorruptError()

    def as_dict(self) -> dict[str, object]:
        return {
            "format_version": self.format_version,
            "generation_id": str(self.generation_id),
            "next_sequence": self.next_sequence,
            "record_count": self.record_count,
            "last_record_digest": self.last_record_digest,
            "policy_id": self.policy_id,
            "policy_fingerprint": self.policy_fingerprint,
        }


@dataclass(frozen=True, slots=True)
class AdaptiveStoreVerifiedSnapshotV1:
    manifest: AdaptiveStoreManifestV1
    envelopes: tuple[AdaptiveStoreEnvelopeV1, ...]


@dataclass(frozen=True, slots=True)
class AdaptiveStoreStateV1:
    """Replayed verified state for owner-only lifecycle consumers."""

    generation_id: UUID
    active_profiles: tuple[Stage15AdaptiveProfileV1, ...]
    profile_history: tuple[Stage15AdaptiveProfileV1, ...]
    reviewed_candidate_fingerprints: tuple[AdaptiveHashV1, ...]
    rejected_candidate_fingerprints: tuple[AdaptiveHashV1, ...]

    def active_profile(
        self,
        goal_source_uuid: UUID | str,
        goal_identity_fingerprint: AdaptiveHashV1,
    ) -> Stage15AdaptiveProfileV1 | None:
        goal_id = _uuid(goal_source_uuid, "Goal UUID")
        goal_fp = _hash(goal_identity_fingerprint, "Goal identity fingerprint")
        return next(
            (
                profile
                for profile in self.active_profiles
                if profile.goal_source_uuid == goal_id
                and profile.goal_identity_fingerprint == goal_fp
            ),
            None,
        )

    def as_dict(self) -> dict[str, object]:
        return {
            "generation_id": str(self.generation_id),
            "active_profiles": [profile.as_dict() for profile in self.active_profiles],
            "profile_history": [profile.as_dict() for profile in self.profile_history],
            "reviewed_candidate_fingerprints": list(self.reviewed_candidate_fingerprints),
            "rejected_candidate_fingerprints": list(self.rejected_candidate_fingerprints),
        }


class _StoreLock(AbstractContextManager["_StoreLock"]):
    """Blocking cross-process exclusive lock for one store root."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._file: Any = None

    def __enter__(self) -> _StoreLock:
        if os.path.lexists(self.path) and self.path.is_symlink():
            raise OSError(errno.ELOOP, "lock path is a symlink")
        flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_BINARY", 0)
        if os.name != "nt":
            flags |= getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(self.path, flags, 0o600)
        self._file = os.fdopen(descriptor, "r+b", buffering=0)
        try:
            if os.name == "nt":
                import msvcrt

                msvcrt_module: Any = msvcrt
                self._file.seek(0, os.SEEK_END)
                if self._file.tell() == 0:
                    self._file.write(b"\0")
                    self._file.flush()
                self._file.seek(0)
                msvcrt_module.locking(self._file.fileno(), msvcrt_module.LK_LOCK, 1)
            else:
                import fcntl

                fcntl_module: Any = fcntl
                fcntl_module.flock(self._file.fileno(), fcntl_module.LOCK_EX)
        except BaseException:
            self._close()
            raise
        return self

    def __exit__(self, exc_type: object, exc_value: object, traceback: object) -> None:
        del exc_type, exc_value, traceback
        file = self._file
        self._file = None
        if file is None:
            return
        try:
            if os.name == "nt":
                import msvcrt

                msvcrt_module: Any = msvcrt
                file.seek(0)
                msvcrt_module.locking(file.fileno(), msvcrt_module.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl_module: Any = fcntl
                fcntl_module.flock(file.fileno(), fcntl_module.LOCK_UN)
        finally:
            file.close()

    def _close(self) -> None:
        file = self._file
        self._file = None
        if file is not None:
            file.close()


def _profile_key(profile: Stage15AdaptiveProfileV1) -> tuple[str, str, str, str]:
    return (
        str(profile.goal_source_uuid),
        profile.goal_identity_fingerprint,
        str(profile.profile_id),
        profile.profile_fingerprint,
    )


def _goal_key(
    goal_source_uuid: UUID | str, goal_identity_fingerprint: AdaptiveHashV1
) -> tuple[str, str]:
    return str(goal_source_uuid), goal_identity_fingerprint


def _event_intent_payload(event: AdaptiveStoreEventV1) -> dict[str, object]:
    payload = event.as_dict()
    payload.pop("event_id", None)
    payload.pop("created_at", None)
    if cast(AdaptiveStoreEventTypeV1, event.event_type) in {
        AdaptiveStoreEventTypeV1.PROFILE_ACTIVATED,
        AdaptiveStoreEventTypeV1.PROFILE_SUPERSEDED,
    } and isinstance(payload.get("profile"), dict):
        profile_payload = dict(cast(dict[str, object], payload["profile"]))
        profile_payload.pop("profile_id", None)
        profile_payload.pop("profile_fingerprint", None)
        payload["profile"] = profile_payload
    return payload


def _replay_state(envelopes: Sequence[AdaptiveStoreEnvelopeV1]) -> AdaptiveStoreStateV1:
    if not envelopes:
        raise AdaptiveCognitiveTwinStoreCorruptError()
    history: dict[tuple[str, str, str, str], Stage15AdaptiveProfileV1] = {}
    active: dict[tuple[str, str], Stage15AdaptiveProfileV1] = {}
    profile_ids: set[UUID] = set()
    profile_counts: dict[tuple[str, str], int] = {}
    reviewed: list[AdaptiveHashV1] = []
    rejected: list[AdaptiveHashV1] = []
    candidate_bindings: dict[AdaptiveHashV1, tuple[tuple[str, str], AdaptiveHashV1]] = {}
    activated_candidates: set[AdaptiveHashV1] = set()

    def check_candidate_binding(event: AdaptiveStoreEventV1) -> AdaptiveHashV1:
        if event.candidate_fingerprint is None or event.source_snapshot_fingerprint is None:
            raise AdaptiveCognitiveTwinStoreCorruptError()
        binding = candidate_bindings.get(event.candidate_fingerprint)
        if binding != (
            _goal_key(event.goal_source_uuid, event.goal_identity_fingerprint),
            event.source_snapshot_fingerprint,
        ):
            raise AdaptiveCognitiveTwinStoreCorruptError()
        return event.candidate_fingerprint

    def add_profile(event: AdaptiveStoreEventV1) -> None:
        if event.profile is None or event.source_snapshot_fingerprint is None:
            raise AdaptiveCognitiveTwinStoreCorruptError()
        if event.profile.source_snapshot_fingerprint != event.source_snapshot_fingerprint:
            raise AdaptiveCognitiveTwinStoreCorruptError()
        profile_key = _profile_key(event.profile)
        if profile_key in history or event.profile.profile_id in profile_ids:
            raise AdaptiveCognitiveTwinStoreCorruptError()
        if profile_counts.get(key, 0) >= ADAPTIVE_PROFILE_STORE_MAX_PROFILE_RECORDS_PER_GOAL:
            raise AdaptiveCognitiveTwinStoreCorruptError()
        history[profile_key] = event.profile
        profile_ids.add(cast(UUID, event.profile.profile_id))
        profile_counts[key] = profile_counts.get(key, 0) + 1

    for envelope in envelopes:
        event = envelope.record
        event_type = cast(AdaptiveStoreEventTypeV1, event.event_type)
        key = _goal_key(event.goal_source_uuid, event.goal_identity_fingerprint)
        if event_type is AdaptiveStoreEventTypeV1.CANDIDATE_REVIEWED:
            assert event.candidate_fingerprint is not None
            assert event.source_snapshot_fingerprint is not None
            if event.candidate_fingerprint in candidate_bindings:
                raise AdaptiveCognitiveTwinStoreCorruptError()
            candidate_bindings[event.candidate_fingerprint] = (
                key,
                event.source_snapshot_fingerprint,
            )
            reviewed.append(event.candidate_fingerprint)
        elif event_type is AdaptiveStoreEventTypeV1.CANDIDATE_REJECTED:
            candidate = check_candidate_binding(event)
            if (
                candidate not in reviewed
                or candidate in rejected
                or candidate in activated_candidates
            ):
                raise AdaptiveCognitiveTwinStoreCorruptError()
            rejected.append(candidate)
        elif event_type is AdaptiveStoreEventTypeV1.PROFILE_ACTIVATED:
            assert event.profile is not None
            candidate = check_candidate_binding(event)
            if candidate not in reviewed:
                raise AdaptiveCognitiveTwinStoreCorruptError()
            if candidate in rejected or candidate in activated_candidates or key in active:
                raise AdaptiveCognitiveTwinStoreCorruptError()
            add_profile(event)
            active[key] = event.profile
            activated_candidates.add(candidate)
        elif event_type is AdaptiveStoreEventTypeV1.PROFILE_SUPERSEDED:
            assert event.profile is not None
            assert event.previous_profile_id is not None
            assert event.previous_profile_fingerprint is not None
            candidate = check_candidate_binding(event)
            if (
                candidate not in reviewed
                or candidate in rejected
                or candidate in activated_candidates
            ):
                raise AdaptiveCognitiveTwinStoreCorruptError()
            current = active.get(key)
            if current is None or (
                current.profile_id != event.previous_profile_id
                or current.profile_fingerprint != event.previous_profile_fingerprint
            ):
                raise AdaptiveCognitiveTwinStoreCorruptError()
            add_profile(event)
            active[key] = event.profile
            activated_candidates.add(candidate)
        elif event_type is AdaptiveStoreEventTypeV1.PROFILE_REVERTED:
            assert event.profile is not None
            assert event.previous_profile_id is not None
            assert event.previous_profile_fingerprint is not None
            current = active.get(key)
            target_key = _profile_key(event.profile)
            if (
                current is None
                or current.profile_id != event.previous_profile_id
                or current.profile_fingerprint != event.previous_profile_fingerprint
                or target_key not in history
                or event.profile.profile_id == event.previous_profile_id
            ):
                raise AdaptiveCognitiveTwinStoreCorruptError()
            active[key] = history[target_key]
        else:
            assert event_type is AdaptiveStoreEventTypeV1.EVALUATION_RECORDED
            assert event.profile is not None
            if _profile_key(event.profile) not in history:
                raise AdaptiveCognitiveTwinStoreCorruptError()
    first = envelopes[0].generation_id
    active_profiles = tuple(
        active[key] for key in sorted(active, key=lambda item: (item[0], item[1]))
    )
    return AdaptiveStoreStateV1(
        generation_id=cast(UUID, first),
        active_profiles=active_profiles,
        profile_history=tuple(history.values()),
        reviewed_candidate_fingerprints=tuple(reviewed),
        rejected_candidate_fingerprints=tuple(rejected),
    )


class AdaptiveCognitiveTwinStore:
    """Strict append-only Stage 15 operational store."""

    def __init__(
        self,
        root: Path | os.PathLike[str],
        *,
        vault_root: Path | os.PathLike[str] | None = None,
        clock: Callable[[], datetime] | None = None,
        expected_owner_group: tuple[str, str] | None = None,
    ) -> None:
        if not isinstance(root, os.PathLike):
            raise AdaptiveCognitiveTwinStoreUnavailableError()
        if expected_owner_group is not None and (
            type(expected_owner_group) is not tuple
            or len(expected_owner_group) != 2
            or any(type(item) is not str or not item for item in expected_owner_group)
        ):
            raise AdaptiveCognitiveTwinStoreUnavailableError()
        self.root = Path(root)
        self._clock = clock or (lambda: datetime.now(UTC))
        self._expected_owner_group = expected_owner_group
        self._validate_root(vault_root)
        try:
            if self.root.exists():
                if self.root.is_symlink() or not self.root.is_dir():
                    raise ValueError("root type")
            else:
                parent = self.root.parent
                if not parent.exists() or parent.is_symlink() or not parent.is_dir():
                    raise ValueError("parent unavailable")
                self.root.mkdir(mode=0o700)
                if self.root.is_symlink() or not self.root.is_dir():
                    raise ValueError("root escaped")
            self._assert_owner_only_unlocked(require_payload=False)
            with _StoreLock(self.lock_path):
                self._assert_owner_only_unlocked(require_payload=False)
                self._initialize_unlocked()
                self._assert_owner_only_unlocked(require_payload=True)
                self._read_verified_unlocked()
        except AdaptiveCognitiveTwinStoreError:
            raise
        except (OSError, ValueError, UnicodeError, RecursionError) as exc:
            raise AdaptiveCognitiveTwinStoreUnavailableError() from exc

    @property
    def records_path(self) -> Path:
        return self.root / ADAPTIVE_PROFILE_STORE_RECORD_FILE_NAME

    @property
    def profiles_path(self) -> Path:
        return self.records_path

    @property
    def manifest_path(self) -> Path:
        return self.root / ADAPTIVE_PROFILE_STORE_MANIFEST_FILE_NAME

    @property
    def lock_path(self) -> Path:
        return self.root / ADAPTIVE_PROFILE_STORE_LOCK_FILE_NAME

    @property
    def manifest(self) -> AdaptiveStoreManifestV1:
        return self.read_manifest()

    def _validate_root(self, vault_root: Path | os.PathLike[str] | None) -> None:
        if self.root.name in {"", ".", ".."}:
            raise AdaptiveCognitiveTwinStoreUnavailableError()
        try:
            candidate = self.root.expanduser().resolve(strict=False)
            self._reject_symlink_components(self.root)
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
            raise AdaptiveCognitiveTwinStoreUnavailableError() from exc

    @staticmethod
    def _reject_symlink_components(path: Path) -> None:
        for item in (path, *path.parents):
            if item.is_symlink():
                raise ValueError("store path contains a symlink")

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
            generation_id = uuid.uuid7()
            self._write_bytes_atomic(self.records_path, b"")
            self._write_manifest_atomic(
                AdaptiveStoreManifestV1(
                    ADAPTIVE_PROFILE_STORE_FORMAT_VERSION,
                    generation_id,
                    1,
                    0,
                    None,
                    ADAPTIVE_PROFILE_STORE_POLICY_ID,
                    ADAPTIVE_PROFILE_STORE_POLICY_FINGERPRINT,
                )
            )
            return
        if not all(present):
            raise AdaptiveCognitiveTwinStoreCorruptError()
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
                    if required and not (path in payload_paths and not require_payload):
                        raise FileNotFoundError(path)
                    continue
                if path.is_symlink():
                    raise ValueError("store path is a symlink")
                item_stat = path.stat()
                if path == self.root:
                    if not stat.S_ISDIR(item_stat.st_mode):
                        raise ValueError("store root is not a directory")
                elif not stat.S_ISREG(item_stat.st_mode):
                    raise ValueError("store payload is not regular")
                if stat.S_IMODE(item_stat.st_mode) != expected_mode:
                    raise ValueError("store permissions")
                if expected_ids is not None and (
                    item_stat.st_uid != expected_ids[0] or item_stat.st_gid != expected_ids[1]
                ):
                    raise ValueError("store owner")
        except (KeyError, OSError) as exc:
            raise AdaptiveCognitiveTwinStoreUnavailableError() from exc

    def _read_manifest_unlocked(self) -> AdaptiveStoreManifestV1:
        try:
            validate_adaptive_policy()
            raw = self.manifest_path.read_bytes()
            data = json.loads(raw.decode("utf-8"))
            expected = {
                "format_version",
                "generation_id",
                "next_sequence",
                "record_count",
                "last_record_digest",
                "policy_id",
                "policy_fingerprint",
            }
            if type(data) is not dict or set(data) != expected:
                raise ValueError("manifest shape")
            if canonical_adaptive_json_bytes(data) != raw:
                raise ValueError("manifest canonical")
            return AdaptiveStoreManifestV1(**data)
        except AdaptiveCognitiveTwinStoreError:
            raise
        except (OSError, UnicodeError, ValueError, TypeError, json.JSONDecodeError) as exc:
            raise AdaptiveCognitiveTwinStoreCorruptError() from exc

    def _event_from_dict(self, value: object) -> AdaptiveStoreEventV1:
        if type(value) is not dict:
            raise ValueError("event shape")
        expected = {
            "event_id",
            "event_type",
            "operation_id_fingerprint",
            "created_at",
            "goal_source_uuid",
            "goal_identity_fingerprint",
            "source_snapshot_fingerprint",
            "candidate_fingerprint",
            "candidate_status",
            "profile",
            "previous_profile_id",
            "previous_profile_fingerprint",
            "reason_code",
            "evaluation_fingerprint",
        }
        if set(value) != expected:
            raise ValueError("event fields")
        data = dict(value)
        if data["profile"] is not None:
            data["profile"] = _profile_from_dict(data["profile"])
        return AdaptiveStoreEventV1(**data)

    def _envelope_from_dict(self, value: object) -> AdaptiveStoreEnvelopeV1:
        if type(value) is not dict:
            raise ValueError("envelope shape")
        expected = {
            "generation_id",
            "sequence",
            "record_type",
            "record",
            "previous_record_digest",
            "event_fingerprint",
            "record_digest",
        }
        if set(value) != expected:
            raise ValueError("envelope fields")
        data = dict(value)
        data["record"] = self._event_from_dict(data["record"])
        return AdaptiveStoreEnvelopeV1(**data)

    def _read_verified_unlocked(self) -> AdaptiveStoreVerifiedSnapshotV1:
        if self.root.is_symlink() or any(
            path.is_symlink() for path in (self.records_path, self.manifest_path)
        ):
            raise AdaptiveCognitiveTwinStoreCorruptError()
        self._assert_owner_only_unlocked()
        manifest = self._read_manifest_unlocked()
        try:
            raw = self.records_path.read_bytes()
        except FileNotFoundError as exc:
            raise AdaptiveCognitiveTwinStoreCorruptError() from exc
        except OSError as exc:
            raise AdaptiveCognitiveTwinStoreUnavailableError() from exc
        if not raw:
            if manifest.record_count != 0 or manifest.last_record_digest is not None:
                raise AdaptiveCognitiveTwinStoreCorruptError()
            return AdaptiveStoreVerifiedSnapshotV1(manifest, ())
        if raw.startswith(b"\xef\xbb\xbf") or not raw.endswith(b"\n"):
            raise AdaptiveCognitiveTwinStoreCorruptError()
        envelopes: list[AdaptiveStoreEnvelopeV1] = []
        previous: AdaptiveHashV1 | None = None
        seen_event_ids: set[UUID] = set()
        seen_operations: set[AdaptiveHashV1] = set()
        for line in raw.splitlines(keepends=True):
            if not line.endswith(b"\n") or line.endswith(b"\r\n"):
                raise AdaptiveCognitiveTwinStoreCorruptError()
            payload = line[:-1]
            if not payload or len(payload) > ADAPTIVE_PROFILE_STORE_MAX_RECORD_BYTES:
                raise AdaptiveCognitiveTwinStoreCorruptError()
            try:
                data = json.loads(payload.decode("utf-8"))
                envelope = self._envelope_from_dict(data)
                if canonical_adaptive_json_bytes(data) != payload:
                    raise ValueError("record canonical")
                if envelope.expected_event_fingerprint != envelope.event_fingerprint:
                    raise ValueError("event fingerprint")
                if envelope.expected_record_digest != envelope.record_digest:
                    raise ValueError("record digest")
                if envelope.generation_id != manifest.generation_id:
                    raise ValueError("generation")
                if envelope.sequence != len(envelopes) + 1:
                    raise ValueError("sequence")
                if envelope.previous_record_digest != previous:
                    raise ValueError("chain")
                event_id = cast(UUID, envelope.record.event_id)
                operation = envelope.record.operation_id_fingerprint
                if event_id in seen_event_ids or operation in seen_operations:
                    raise ValueError("duplicate identity")
            except AdaptiveCognitiveTwinStoreError:
                raise
            except (
                TypeError,
                ValueError,
                UnicodeError,
                OverflowError,
                json.JSONDecodeError,
            ) as exc:
                raise AdaptiveCognitiveTwinStoreCorruptError() from exc
            envelopes.append(envelope)
            seen_event_ids.add(event_id)
            seen_operations.add(operation)
            previous = envelope.record_digest
        if (
            manifest.record_count != len(envelopes)
            or manifest.next_sequence != len(envelopes) + 1
            or manifest.last_record_digest != previous
        ):
            raise AdaptiveCognitiveTwinStoreCorruptError()
        if len(envelopes) > ADAPTIVE_PROFILE_STORE_MAX_RECORDS:
            raise AdaptiveCognitiveTwinStoreCorruptError()
        return AdaptiveStoreVerifiedSnapshotV1(manifest, tuple(envelopes))

    def read_verified_snapshot(self) -> AdaptiveStoreVerifiedSnapshotV1:
        try:
            with _StoreLock(self.lock_path):
                return self._read_verified_unlocked()
        except AdaptiveCognitiveTwinStoreError:
            raise
        except (OSError, ValueError, UnicodeError, RecursionError) as exc:
            raise AdaptiveCognitiveTwinStoreUnavailableError() from exc

    verified_snapshot = read_verified_snapshot

    def read_events(self) -> tuple[AdaptiveStoreEnvelopeV1, ...]:
        return self.read_verified_snapshot().envelopes

    read_records = read_events

    def read_manifest(self) -> AdaptiveStoreManifestV1:
        return self.read_verified_snapshot().manifest

    def read_state(self) -> AdaptiveStoreStateV1:
        snapshot = self.read_verified_snapshot()
        if not snapshot.envelopes:
            return AdaptiveStoreStateV1(
                generation_id=cast(UUID, snapshot.manifest.generation_id),
                active_profiles=(),
                profile_history=(),
                reviewed_candidate_fingerprints=(),
                rejected_candidate_fingerprints=(),
            )
        return _replay_state(snapshot.envelopes)

    state = read_state

    def active_profile(
        self,
        goal_source_uuid: UUID | str,
        goal_identity_fingerprint: AdaptiveHashV1,
    ) -> Stage15AdaptiveProfileV1 | None:
        return self.read_state().active_profile(goal_source_uuid, goal_identity_fingerprint)

    read_active_profile = active_profile

    def _append_event_unlocked(
        self,
        snapshot: AdaptiveStoreVerifiedSnapshotV1,
        event: AdaptiveStoreEventV1,
    ) -> AdaptiveStoreEnvelopeV1:
        for existing in snapshot.envelopes:
            if existing.record.operation_id_fingerprint != event.operation_id_fingerprint:
                continue
            if _event_intent_payload(existing.record) == _event_intent_payload(event):
                return existing
            raise AdaptiveCognitiveTwinStoreIdempotencyConflictError()
        if snapshot.manifest.record_count >= ADAPTIVE_PROFILE_STORE_MAX_RECORDS:
            raise AdaptiveCognitiveTwinStoreUnavailableError()
        sequence = snapshot.manifest.next_sequence
        envelope_without_digest = {
            "generation_id": str(snapshot.manifest.generation_id),
            "sequence": sequence,
            "record_type": cast(AdaptiveStoreEventTypeV1, event.event_type).value,
            "record": event.as_dict(),
            "previous_record_digest": snapshot.manifest.last_record_digest,
            "event_fingerprint": adaptive_store_hash_json(event.as_dict()),
        }
        envelope = AdaptiveStoreEnvelopeV1(
            generation_id=snapshot.manifest.generation_id,
            sequence=sequence,
            record_type=event.event_type,
            record=event,
            previous_record_digest=snapshot.manifest.last_record_digest,
            event_fingerprint=cast(AdaptiveHashV1, envelope_without_digest["event_fingerprint"]),
            record_digest=adaptive_store_hash_json(envelope_without_digest),
        )
        line = canonical_adaptive_json_bytes(envelope.as_dict()) + b"\n"
        if len(line) > ADAPTIVE_PROFILE_STORE_MAX_RECORD_BYTES:
            raise AdaptiveCognitiveTwinStoreUnavailableError()
        self._append_bytes_durable(self.records_path, line)
        self._write_manifest_atomic(
            AdaptiveStoreManifestV1(
                ADAPTIVE_PROFILE_STORE_FORMAT_VERSION,
                snapshot.manifest.generation_id,
                sequence + 1,
                sequence,
                envelope.record_digest,
                ADAPTIVE_PROFILE_STORE_POLICY_ID,
                ADAPTIVE_PROFILE_STORE_POLICY_FINGERPRINT,
            )
        )
        verified = self._read_verified_unlocked()
        if not verified.envelopes or verified.envelopes[-1] != envelope:
            raise AdaptiveCognitiveTwinStoreUnavailableError()
        return envelope

    def _append_event(self, event: AdaptiveStoreEventV1) -> AdaptiveStoreEnvelopeV1:
        validate_adaptive_store_event(event)
        try:
            with _StoreLock(self.lock_path):
                return self._append_event_unlocked(self._read_verified_unlocked(), event)
        except AdaptiveCognitiveTwinStoreError:
            raise
        except (OSError, ValueError, UnicodeError, RecursionError) as exc:
            raise AdaptiveCognitiveTwinStoreUnavailableError() from exc

    def _find_operation_unlocked(
        self,
        snapshot: AdaptiveStoreVerifiedSnapshotV1,
        operation_id_fingerprint: AdaptiveHashV1,
    ) -> AdaptiveStoreEnvelopeV1 | None:
        return next(
            (
                envelope
                for envelope in snapshot.envelopes
                if envelope.record.operation_id_fingerprint == operation_id_fingerprint
            ),
            None,
        )

    @staticmethod
    def _state_from_snapshot(
        snapshot: AdaptiveStoreVerifiedSnapshotV1,
    ) -> AdaptiveStoreStateV1:
        if snapshot.envelopes:
            return _replay_state(snapshot.envelopes)
        return AdaptiveStoreStateV1(
            generation_id=cast(UUID, snapshot.manifest.generation_id),
            active_profiles=(),
            profile_history=(),
            reviewed_candidate_fingerprints=(),
            rejected_candidate_fingerprints=(),
        )

    @staticmethod
    def _candidate_was_applied_unlocked(
        snapshot: AdaptiveStoreVerifiedSnapshotV1,
        candidate_fingerprint: AdaptiveHashV1,
    ) -> bool:
        return any(
            envelope.record.candidate_fingerprint == candidate_fingerprint
            and envelope.record.event_type
            in {
                AdaptiveStoreEventTypeV1.PROFILE_ACTIVATED,
                AdaptiveStoreEventTypeV1.PROFILE_SUPERSEDED,
            }
            for envelope in snapshot.envelopes
        )

    @staticmethod
    def _candidate_operation_matches(
        existing: AdaptiveStoreEnvelopeV1,
        candidate: Stage15CandidateV1,
        *,
        operation_id_fingerprint: AdaptiveHashV1,
        supersede: bool,
    ) -> bool:
        if candidate.proposed_profile is None:
            return False
        record = existing.record
        expected_type = (
            AdaptiveStoreEventTypeV1.PROFILE_SUPERSEDED
            if supersede
            else AdaptiveStoreEventTypeV1.PROFILE_ACTIVATED
        )
        profile = record.profile
        return bool(
            record.operation_id_fingerprint == operation_id_fingerprint
            and record.event_type is expected_type
            and record.goal_source_uuid == candidate.goal_source_uuid
            and record.goal_identity_fingerprint == candidate.goal_identity_fingerprint
            and record.source_snapshot_fingerprint == candidate.source_snapshot_fingerprint
            and record.candidate_fingerprint == candidate.candidate_fingerprint
            and record.candidate_status is AdaptiveSufficiencyStateV1.CANDIDATE
            and profile is not None
            and profile.profile_shape_fingerprint
            == adaptive_store_hash_json(candidate.proposed_profile.fingerprint_payload())
            and (
                (
                    not supersede
                    and candidate.prior_profile_id is None
                    and candidate.prior_profile_fingerprint is None
                    and record.previous_profile_id is None
                    and record.previous_profile_fingerprint is None
                )
                or (
                    supersede
                    and candidate.prior_profile_id is not None
                    and candidate.prior_profile_fingerprint is not None
                    and record.previous_profile_id == candidate.prior_profile_id
                    and record.previous_profile_fingerprint == candidate.prior_profile_fingerprint
                )
            )
        )

    def _now(self) -> datetime:
        try:
            current = self._clock()
            if (
                type(current) is not datetime
                or current.tzinfo is None
                or current.utcoffset() is None
            ):
                raise ValueError("clock")
            return current.astimezone(UTC)
        except (OSError, TypeError, ValueError, OverflowError) as exc:
            raise AdaptiveCognitiveTwinStoreUnavailableError() from exc

    @staticmethod
    def _candidate_parts(
        candidate: Stage15CandidateV1,
    ) -> tuple[UUID, AdaptiveHashV1, AdaptiveHashV1]:
        validate_adaptive_candidate(candidate)
        if (
            candidate.candidate_status is not AdaptiveSufficiencyStateV1.CANDIDATE
            or candidate.proposed_profile is None
        ):
            raise AdaptiveCognitiveTwinStoreStateConflictError()
        return (
            cast(UUID, candidate.goal_source_uuid),
            candidate.goal_identity_fingerprint,
            candidate.source_snapshot_fingerprint,
        )

    @staticmethod
    def _validate_current_source(
        candidate: Stage15CandidateV1,
        current_source: object,
    ) -> tuple[UUID, AdaptiveHashV1]:
        try:
            validated = validate_adaptive_source_snapshot(current_source)
        except AdaptiveCognitiveTwinInputError as exc:
            raise AdaptiveCognitiveTwinStoreInvalidRequestError() from exc
        goal_uuid, goal_fp, source_fp = AdaptiveCognitiveTwinStore._candidate_parts(candidate)
        if (
            validated.goal.goal_source_uuid != goal_uuid
            or validated.goal.goal_identity_fingerprint != goal_fp
            or validated.source_snapshot_fingerprint != source_fp
        ):
            raise AdaptiveCognitiveTwinStoreSourceChangedError()
        return goal_uuid, goal_fp

    @staticmethod
    def _event_for_candidate(
        event_type: AdaptiveStoreEventTypeV1,
        candidate: Stage15CandidateV1,
        operation_id_fingerprint: AdaptiveHashV1,
        now: datetime,
        *,
        profile: Stage15AdaptiveProfileV1 | None = None,
        previous_profile: Stage15AdaptiveProfileV1 | None = None,
        reason_code: AdaptiveStoreReasonV1 | None = None,
    ) -> AdaptiveStoreEventV1:
        return AdaptiveStoreEventV1(
            event_id=uuid.uuid7(),
            event_type=event_type,
            operation_id_fingerprint=operation_id_fingerprint,
            created_at=now,
            goal_source_uuid=candidate.goal_source_uuid,
            goal_identity_fingerprint=candidate.goal_identity_fingerprint,
            source_snapshot_fingerprint=candidate.source_snapshot_fingerprint,
            candidate_fingerprint=candidate.candidate_fingerprint,
            candidate_status=candidate.candidate_status,
            profile=profile,
            previous_profile_id=(
                previous_profile.profile_id if previous_profile is not None else None
            ),
            previous_profile_fingerprint=(
                previous_profile.profile_fingerprint if previous_profile is not None else None
            ),
            reason_code=reason_code,
        )

    def review_candidate(
        self,
        candidate: Stage15CandidateV1,
        *,
        operation_id: object,
    ) -> AdaptiveStoreEnvelopeV1:
        self._candidate_parts(candidate)
        operation_fp = fingerprint_adaptive_operation_id(operation_id)
        event = self._event_for_candidate(
            AdaptiveStoreEventTypeV1.CANDIDATE_REVIEWED,
            candidate,
            operation_fp,
            self._now(),
        )
        try:
            with _StoreLock(self.lock_path):
                snapshot = self._read_verified_unlocked()
                existing = self._find_operation_unlocked(snapshot, operation_fp)
                if existing is not None:
                    return self._append_event_unlocked(snapshot, event)
                state = self._state_from_snapshot(snapshot)
                if (
                    candidate.candidate_fingerprint in state.reviewed_candidate_fingerprints
                    or candidate.candidate_fingerprint in state.rejected_candidate_fingerprints
                    or self._candidate_was_applied_unlocked(
                        snapshot, candidate.candidate_fingerprint
                    )
                ):
                    raise AdaptiveCognitiveTwinStoreStateConflictError()
                return self._append_event_unlocked(snapshot, event)
        except AdaptiveCognitiveTwinStoreError:
            raise
        except (OSError, ValueError, UnicodeError, RecursionError) as exc:
            raise AdaptiveCognitiveTwinStoreUnavailableError() from exc

    review = review_candidate

    def reject_candidate(
        self,
        candidate: Stage15CandidateV1,
        *,
        operation_id: object,
    ) -> AdaptiveStoreEnvelopeV1:
        self._candidate_parts(candidate)
        operation_fp = fingerprint_adaptive_operation_id(operation_id)
        event = self._event_for_candidate(
            AdaptiveStoreEventTypeV1.CANDIDATE_REJECTED,
            candidate,
            operation_fp,
            self._now(),
            reason_code=AdaptiveStoreReasonV1.OWNER_REJECTED,
        )
        try:
            with _StoreLock(self.lock_path):
                snapshot = self._read_verified_unlocked()
                existing = self._find_operation_unlocked(snapshot, operation_fp)
                if existing is not None:
                    return self._append_event_unlocked(snapshot, event)
                state = self._state_from_snapshot(snapshot)
                if candidate.candidate_fingerprint not in state.reviewed_candidate_fingerprints:
                    raise AdaptiveCognitiveTwinStoreCandidateNotReviewedError()
                if (
                    candidate.candidate_fingerprint in state.rejected_candidate_fingerprints
                    or self._candidate_was_applied_unlocked(
                        snapshot, candidate.candidate_fingerprint
                    )
                ):
                    raise AdaptiveCognitiveTwinStoreStateConflictError()
                return self._append_event_unlocked(snapshot, event)
        except AdaptiveCognitiveTwinStoreError:
            raise
        except (OSError, ValueError, UnicodeError, RecursionError) as exc:
            raise AdaptiveCognitiveTwinStoreUnavailableError() from exc

    reject = reject_candidate

    def _activate_or_supersede(
        self,
        candidate: Stage15CandidateV1,
        current_source: object,
        *,
        operation_id: object,
        supersede: bool,
    ) -> AdaptiveStoreEnvelopeV1:
        operation_fp = fingerprint_adaptive_operation_id(operation_id)
        goal_uuid, goal_fp = self._validate_current_source(candidate, current_source)
        assert candidate.proposed_profile is not None
        proposal = candidate.proposed_profile
        try:
            with _StoreLock(self.lock_path):
                snapshot = self._read_verified_unlocked()
                existing = self._find_operation_unlocked(snapshot, operation_fp)
                if existing is not None:
                    if not self._candidate_operation_matches(
                        existing,
                        candidate,
                        operation_id_fingerprint=operation_fp,
                        supersede=supersede,
                    ):
                        raise AdaptiveCognitiveTwinStoreIdempotencyConflictError()
                    return existing
                state = self._state_from_snapshot(snapshot)
                if candidate.candidate_fingerprint in state.rejected_candidate_fingerprints:
                    raise AdaptiveCognitiveTwinStoreStateConflictError()
                if candidate.candidate_fingerprint not in state.reviewed_candidate_fingerprints:
                    raise AdaptiveCognitiveTwinStoreCandidateNotReviewedError()
                if self._candidate_was_applied_unlocked(snapshot, candidate.candidate_fingerprint):
                    raise AdaptiveCognitiveTwinStoreStateConflictError()
                current = state.active_profile(goal_uuid, goal_fp)
                if supersede:
                    if current is None:
                        raise AdaptiveCognitiveTwinStoreActiveProfileConflictError()
                    if (
                        candidate.prior_profile_id != current.profile_id
                        or candidate.prior_profile_fingerprint != current.profile_fingerprint
                    ):
                        raise AdaptiveCognitiveTwinStoreStateConflictError()
                else:
                    if current is not None:
                        raise AdaptiveCognitiveTwinStoreActiveProfileConflictError()
                    if (
                        candidate.prior_profile_id is not None
                        or candidate.prior_profile_fingerprint is not None
                    ):
                        raise AdaptiveCognitiveTwinStoreStateConflictError()
                if (
                    sum(
                        1
                        for profile in state.profile_history
                        if profile.goal_source_uuid == goal_uuid
                        and profile.goal_identity_fingerprint == goal_fp
                    )
                    >= ADAPTIVE_PROFILE_STORE_MAX_PROFILE_RECORDS_PER_GOAL
                ):
                    raise AdaptiveCognitiveTwinStoreUnavailableError()
                profile = Stage15AdaptiveProfileV1.from_proposal(
                    proposal,
                    profile_id=uuid.uuid7(),
                )
                event = self._event_for_candidate(
                    AdaptiveStoreEventTypeV1.PROFILE_SUPERSEDED
                    if supersede
                    else AdaptiveStoreEventTypeV1.PROFILE_ACTIVATED,
                    candidate,
                    operation_fp,
                    self._now(),
                    profile=profile,
                    previous_profile=current if supersede else None,
                )
                return self._append_event_unlocked(snapshot, event)
        except AdaptiveCognitiveTwinStoreError:
            raise
        except (OSError, ValueError, UnicodeError, RecursionError) as exc:
            raise AdaptiveCognitiveTwinStoreUnavailableError() from exc

    def activate_candidate(
        self,
        candidate: Stage15CandidateV1,
        current_source: object,
        *,
        operation_id: object,
    ) -> AdaptiveStoreEnvelopeV1:
        return self._activate_or_supersede(
            candidate,
            current_source,
            operation_id=operation_id,
            supersede=False,
        )

    activate = activate_candidate

    def supersede_candidate(
        self,
        candidate: Stage15CandidateV1,
        current_source: object,
        *,
        operation_id: object,
    ) -> AdaptiveStoreEnvelopeV1:
        return self._activate_or_supersede(
            candidate,
            current_source,
            operation_id=operation_id,
            supersede=True,
        )

    supersede = supersede_candidate

    def revert_profile(
        self,
        *,
        goal_source_uuid: UUID | str,
        goal_identity_fingerprint: AdaptiveHashV1,
        target_profile_id: UUID | str,
        target_profile_fingerprint: AdaptiveHashV1,
        operation_id: object,
    ) -> AdaptiveStoreEnvelopeV1:
        goal_id = _uuid(goal_source_uuid, "Goal UUID")
        goal_fp = _hash(goal_identity_fingerprint, "Goal identity fingerprint")
        target_id = _uuid(target_profile_id, "target profile id")
        target_fp = _hash(target_profile_fingerprint, "target profile fingerprint")
        operation_fp = fingerprint_adaptive_operation_id(operation_id)
        try:
            with _StoreLock(self.lock_path):
                snapshot = self._read_verified_unlocked()
                existing = self._find_operation_unlocked(snapshot, operation_fp)
                if existing is not None:
                    existing_profile = existing.record.profile
                    if not (
                        existing.record.event_type is AdaptiveStoreEventTypeV1.PROFILE_REVERTED
                        and existing.record.goal_source_uuid == goal_id
                        and existing.record.goal_identity_fingerprint == goal_fp
                        and existing_profile is not None
                        and existing_profile.profile_id == target_id
                        and existing_profile.profile_fingerprint == target_fp
                    ):
                        raise AdaptiveCognitiveTwinStoreIdempotencyConflictError()
                    return existing
                state = self._state_from_snapshot(snapshot)
                current = state.active_profile(goal_id, goal_fp)
                if current is None:
                    raise AdaptiveCognitiveTwinStoreStateConflictError()
                target = next(
                    (
                        profile
                        for profile in state.profile_history
                        if profile.goal_source_uuid == goal_id
                        and profile.goal_identity_fingerprint == goal_fp
                        and profile.profile_id == target_id
                        and profile.profile_fingerprint == target_fp
                    ),
                    None,
                )
                if target is None or target.profile_id == current.profile_id:
                    raise AdaptiveCognitiveTwinStoreProfileNotFoundError()
                event = AdaptiveStoreEventV1(
                    event_id=uuid.uuid7(),
                    event_type=AdaptiveStoreEventTypeV1.PROFILE_REVERTED,
                    operation_id_fingerprint=operation_fp,
                    created_at=self._now(),
                    goal_source_uuid=goal_id,
                    goal_identity_fingerprint=goal_fp,
                    source_snapshot_fingerprint=target.source_snapshot_fingerprint,
                    profile=target,
                    previous_profile_id=current.profile_id,
                    previous_profile_fingerprint=current.profile_fingerprint,
                )
                return self._append_event_unlocked(snapshot, event)
        except AdaptiveCognitiveTwinStoreError:
            raise
        except (OSError, ValueError, UnicodeError, RecursionError) as exc:
            raise AdaptiveCognitiveTwinStoreUnavailableError() from exc

    revert = revert_profile

    def record_evaluation(
        self,
        profile: Stage15AdaptiveProfileV1,
        evaluation_fingerprint: AdaptiveHashV1,
        *,
        operation_id: object,
    ) -> AdaptiveStoreEnvelopeV1:
        if type(profile) is not Stage15AdaptiveProfileV1:
            raise AdaptiveCognitiveTwinStoreInvalidRequestError()
        profile_fp = _hash(evaluation_fingerprint, "evaluation fingerprint")
        operation_fp = fingerprint_adaptive_operation_id(operation_id)
        event = AdaptiveStoreEventV1(
            event_id=uuid.uuid7(),
            event_type=AdaptiveStoreEventTypeV1.EVALUATION_RECORDED,
            operation_id_fingerprint=operation_fp,
            created_at=self._now(),
            goal_source_uuid=profile.goal_source_uuid,
            goal_identity_fingerprint=profile.goal_identity_fingerprint,
            profile=profile,
            evaluation_fingerprint=profile_fp,
        )
        try:
            with _StoreLock(self.lock_path):
                snapshot = self._read_verified_unlocked()
                existing = self._find_operation_unlocked(snapshot, operation_fp)
                if existing is not None:
                    return self._append_event_unlocked(snapshot, event)
                state = self._state_from_snapshot(snapshot)
                current = state.active_profile(
                    profile.goal_source_uuid,
                    profile.goal_identity_fingerprint,
                )
                if current is None or _profile_key(current) != _profile_key(profile):
                    raise AdaptiveCognitiveTwinStoreProfileNotFoundError()
                return self._append_event_unlocked(snapshot, event)
        except AdaptiveCognitiveTwinStoreError:
            raise
        except (OSError, ValueError, UnicodeError, RecursionError) as exc:
            raise AdaptiveCognitiveTwinStoreUnavailableError() from exc

    append_evaluation = record_evaluation

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

    def _write_manifest_atomic(self, manifest: AdaptiveStoreManifestV1) -> None:
        self._write_bytes_atomic(
            self.manifest_path, canonical_adaptive_json_bytes(manifest.as_dict())
        )

    def _write_bytes_atomic(self, path: Path, payload: bytes) -> None:
        temporary = self.root / f".{path.name}.{uuid.uuid7()}.tmp"
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


def derive_adaptive_cognitive_twin_store_root(
    env_file: Path | os.PathLike[str] | None,
) -> Path | None:
    """Resolve the additive store only from an explicit existing env file."""

    if env_file is None:
        return None
    candidate = Path(env_file).expanduser()
    try:
        if candidate.is_symlink() or not candidate.is_file():
            return None
        selected = candidate.resolve(strict=True)
        parent = selected.parent / ADAPTIVE_PROFILE_STORE_PARENT_NAME
        if parent.is_symlink() or not parent.is_dir():
            return None
        root = parent / ADAPTIVE_PROFILE_STORE_DIRECTORY_NAME
        if root.is_symlink():
            return None
        return root
    except OSError, RuntimeError, ValueError:
        return None


AdaptiveProfileStoreV1 = AdaptiveCognitiveTwinStore
AdaptiveProfileStore = AdaptiveCognitiveTwinStore
AdaptiveStore = AdaptiveCognitiveTwinStore
AdaptiveStoreEvent = AdaptiveStoreEventV1
AdaptiveStoreEnvelope = AdaptiveStoreEnvelopeV1
AdaptiveStoreManifest = AdaptiveStoreManifestV1
AdaptiveStoreState = AdaptiveStoreStateV1


__all__ = [
    "ADAPTIVE_PROFILE_STORE_DIRECTORY_NAME",
    "ADAPTIVE_PROFILE_STORE_FORMAT_VERSION",
    "ADAPTIVE_PROFILE_STORE_LOCK_FILE_NAME",
    "ADAPTIVE_PROFILE_STORE_MANIFEST_FILE_NAME",
    "ADAPTIVE_PROFILE_STORE_MAX_OPERATION_ID_BYTES",
    "ADAPTIVE_PROFILE_STORE_MAX_PROFILE_RECORDS_PER_GOAL",
    "ADAPTIVE_PROFILE_STORE_MAX_RECORDS",
    "ADAPTIVE_PROFILE_STORE_MAX_RECORD_BYTES",
    "ADAPTIVE_PROFILE_STORE_PARENT_NAME",
    "ADAPTIVE_PROFILE_STORE_POLICY_FINGERPRINT",
    "ADAPTIVE_PROFILE_STORE_POLICY_ID",
    "ADAPTIVE_PROFILE_STORE_RECORD_FILE_NAME",
    "AdaptiveCognitiveTwinStore",
    "AdaptiveCognitiveTwinStoreActiveProfileConflictError",
    "AdaptiveCognitiveTwinStoreCandidateNotReviewedError",
    "AdaptiveCognitiveTwinStoreCorruptError",
    "AdaptiveCognitiveTwinStoreError",
    "AdaptiveCognitiveTwinStoreIdempotencyConflictError",
    "AdaptiveCognitiveTwinStoreInvalidRequestError",
    "AdaptiveCognitiveTwinStoreProfileNotFoundError",
    "AdaptiveCognitiveTwinStoreSourceChangedError",
    "AdaptiveCognitiveTwinStoreStateConflictError",
    "AdaptiveCognitiveTwinStoreUnavailableError",
    "AdaptiveProfileStore",
    "AdaptiveProfileStoreV1",
    "AdaptiveStore",
    "AdaptiveStoreEnvelope",
    "AdaptiveStoreEnvelopeV1",
    "AdaptiveStoreErrorCodeV1",
    "AdaptiveStoreEvent",
    "AdaptiveStoreEventTypeV1",
    "AdaptiveStoreEventV1",
    "AdaptiveStoreManifest",
    "AdaptiveStoreManifestV1",
    "AdaptiveStoreReasonV1",
    "AdaptiveStoreState",
    "AdaptiveStoreStateV1",
    "AdaptiveStoreVerifiedSnapshotV1",
    "adaptive_store_hash_json",
    "derive_adaptive_cognitive_twin_store_root",
    "fingerprint_adaptive_operation_id",
    "validate_adaptive_store_event",
]
