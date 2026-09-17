"""Append-only operational storage for the reviewed Stage 20 Run.

The ledger stores only validated Run snapshots and safe lifecycle metadata.  It
does not retain a prepared Stage 19 action, a confirmation value, a credential,
or a provider response.  The store is outside the repository and vault, uses a
canonical JSONL hash chain, and permits exactly one non-terminal Run.
"""

from __future__ import annotations

import hashlib
import json
import os
import stat
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Final, cast
from uuid import UUID, uuid7

from second_brain.application.adaptive_cognitive_twin_store import _StoreLock
from second_brain.application.personal_agent import (
    AGENT_POLICY_FINGERPRINT,
    AGENT_POLICY_ID,
    PersonalAgentError,
    personal_agent_hash,
)
from second_brain.application.personal_agent_run import (
    AgentRunConflictError,
    AgentRunEventKindV1,
    AgentRunSnapshotInvalidError,
    AgentRunSnapshotV1,
    AgentRunStateV1,
    AgentRunStoreError,
)
from second_brain.domain.models import parse_rfc3339, parse_uuid7

PERSONAL_AGENT_RUN_STORE_PARENT_NAME: Final[str] = "prospective-audit"
PERSONAL_AGENT_RUN_STORE_DIRECTORY_NAME: Final[str] = "personal-agent"
PERSONAL_AGENT_RUN_STORE_RECORD_FILE_NAME: Final[str] = "runs.jsonl"
PERSONAL_AGENT_RUN_STORE_MANIFEST_FILE_NAME: Final[str] = "manifest.json"
PERSONAL_AGENT_RUN_STORE_LOCK_FILE_NAME: Final[str] = ".store.lock"
PERSONAL_AGENT_RUN_STORE_FORMAT_VERSION: Final[int] = 1
PERSONAL_AGENT_RUN_STORE_MAX_RECORDS: Final[int] = 4096
PERSONAL_AGENT_RUN_STORE_MAX_RECORD_BYTES: Final[int] = 256 * 1024
PERSONAL_AGENT_RUN_STORE_MAX_OPERATION_ID_BYTES: Final[int] = 256

_HASH_LENGTH: Final[int] = 64
_TERMINAL_STATES: Final[frozenset[AgentRunStateV1]] = frozenset(
    {
        AgentRunStateV1.COMPLETED,
        AgentRunStateV1.ABANDONED,
        AgentRunStateV1.SUPERSEDED,
    }
)


class PersonalAgentRunStoreError(AgentRunStoreError):
    """Base safe error for the Stage 20 operational store."""


class PersonalAgentRunStoreUnavailableError(PersonalAgentRunStoreError):
    """The store cannot be initialized or safely read/written."""


class PersonalAgentRunStoreCorruptError(PersonalAgentRunStoreError):
    """The manifest, JSONL chain, or replayed lifecycle is invalid."""


class PersonalAgentRunStoreIdempotencyConflictError(PersonalAgentRunStoreError):
    """An operation identifier was reused for another immutable intent."""


class PersonalAgentRunStoreStateConflictError(PersonalAgentRunStoreError):
    """The append would violate the one-current-Run invariant."""


class PersonalAgentRunStoreCapacityError(PersonalAgentRunStoreError):
    """The bounded operational ledger cannot accept another record."""


class PersonalAgentRunStoreRecordTypeV1(StrEnum):
    """Closed envelope record vocabulary."""

    RUN_EVENT = "run_event"


PersonalAgentRunStoreRecordType = PersonalAgentRunStoreRecordTypeV1


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
        raise PersonalAgentRunStoreCorruptError() from exc


def personal_agent_run_store_hash(value: object) -> str:
    """Return the canonical SHA-256 used by the operational ledger."""

    return hashlib.sha256(_canonical_bytes(value)).hexdigest()


def _raw_hash(value: object, *, error: type[PersonalAgentError]) -> str:
    if (
        type(value) is not str
        or len(value) != _HASH_LENGTH
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise error()
    return value


def _uuid7(value: object, *, error: type[PersonalAgentError]) -> UUID:
    try:
        return parse_uuid7(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise error() from exc


def _timestamp(value: object, *, error: type[PersonalAgentError]) -> datetime:
    try:
        parsed = value if isinstance(value, datetime) else parse_rfc3339(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise error() from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise error()
    return parsed.astimezone(UTC)


def _wire_dict(value: object, expected: set[str]) -> dict[str, object]:
    if type(value) is not dict:
        raise PersonalAgentRunStoreCorruptError()
    data = cast(dict[str, object], value)
    if any(type(key) is not str for key in data) or set(data) != expected:
        raise PersonalAgentRunStoreCorruptError()
    return data


def _enum_value[EnumT: StrEnum](
    value: object,
    enum_type: type[EnumT],
) -> EnumT:
    if isinstance(value, enum_type):
        return value
    if type(value) is str:
        try:
            return enum_type(value)
        except ValueError:
            pass
    raise PersonalAgentRunStoreCorruptError()


def _operation_fingerprint(value: object) -> str:
    if type(value) is not str:
        raise PersonalAgentRunStoreError()
    encoded = value.encode("utf-8")
    if (
        not encoded
        or len(encoded) > PERSONAL_AGENT_RUN_STORE_MAX_OPERATION_ID_BYTES
        or any(ord(character) < 0x20 or ord(character) == 0x7F for character in value)
    ):
        raise PersonalAgentRunStoreError()
    return hashlib.sha256(encoded).hexdigest()


@dataclass(frozen=True, slots=True)
class PersonalAgentRunStoreManifestV1:
    """Manifest cross-checking the complete immutable event generation."""

    format_version: int
    next_sequence: int
    record_count: int
    last_record_digest: str | None
    policy_id: str
    policy_fingerprint: str

    def __post_init__(self) -> None:
        if (
            self.format_version != PERSONAL_AGENT_RUN_STORE_FORMAT_VERSION
            or type(self.format_version) is not int
        ):
            raise PersonalAgentRunStoreCorruptError()
        if (
            type(self.next_sequence) is not int
            or isinstance(self.next_sequence, bool)
            or not 1 <= self.next_sequence <= 2**64 - 1
        ):
            raise PersonalAgentRunStoreCorruptError()
        if (
            type(self.record_count) is not int
            or isinstance(self.record_count, bool)
            or not 0 <= self.record_count <= PERSONAL_AGENT_RUN_STORE_MAX_RECORDS
            or self.next_sequence != self.record_count + 1
        ):
            raise PersonalAgentRunStoreCorruptError()
        digest = (
            None
            if self.last_record_digest is None
            else _raw_hash(self.last_record_digest, error=PersonalAgentRunStoreCorruptError)
        )
        if (self.record_count == 0) != (digest is None):
            raise PersonalAgentRunStoreCorruptError()
        if self.policy_id != AGENT_POLICY_ID or self.policy_fingerprint != AGENT_POLICY_FINGERPRINT:
            raise PersonalAgentRunStoreCorruptError()
        object.__setattr__(self, "last_record_digest", digest)

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
class PersonalAgentRunStoreEventV1:
    """One safe lifecycle event referencing an immutable Run snapshot."""

    event_id: UUID | str
    event_type: AgentRunEventKindV1 | str
    operation_id_fingerprint: str
    intent_fingerprint: str
    previous_snapshot_fingerprint: str | None
    snapshot: AgentRunSnapshotV1

    def __post_init__(self) -> None:
        event_id = _uuid7(self.event_id, error=PersonalAgentRunStoreCorruptError)
        event_type = _enum_value(self.event_type, AgentRunEventKindV1)
        operation = _raw_hash(
            self.operation_id_fingerprint,
            error=PersonalAgentRunStoreCorruptError,
        )
        intent = _raw_hash(self.intent_fingerprint, error=PersonalAgentRunStoreCorruptError)
        previous = (
            None
            if self.previous_snapshot_fingerprint is None
            else _raw_hash(
                self.previous_snapshot_fingerprint,
                error=PersonalAgentRunStoreCorruptError,
            )
        )
        if type(self.snapshot) is not AgentRunSnapshotV1:
            raise PersonalAgentRunStoreCorruptError()
        if event_type is AgentRunEventKindV1.ACCEPT:
            if previous is not None or self.snapshot.revision != 1:
                raise PersonalAgentRunStoreCorruptError()
        elif previous is None:
            raise PersonalAgentRunStoreCorruptError()
        object.__setattr__(self, "event_id", event_id)
        object.__setattr__(self, "event_type", event_type)
        object.__setattr__(self, "operation_id_fingerprint", operation)
        object.__setattr__(self, "intent_fingerprint", intent)
        object.__setattr__(self, "previous_snapshot_fingerprint", previous)

    def as_dict(self) -> dict[str, object]:
        return {
            "event_id": str(self.event_id),
            "event_type": cast(AgentRunEventKindV1, self.event_type).value,
            "operation_id_fingerprint": self.operation_id_fingerprint,
            "intent_fingerprint": self.intent_fingerprint,
            "previous_snapshot_fingerprint": self.previous_snapshot_fingerprint,
            "snapshot": self.snapshot.as_dict(),
        }


@dataclass(frozen=True, slots=True)
class PersonalAgentRunStoreEnvelopeV1:
    """One canonical hash-chained JSONL envelope."""

    sequence: int
    record_type: PersonalAgentRunStoreRecordTypeV1 | str
    record: PersonalAgentRunStoreEventV1
    previous_record_digest: str | None
    record_fingerprint: str
    record_digest: str

    def __post_init__(self) -> None:
        if (
            type(self.sequence) is not int
            or isinstance(self.sequence, bool)
            or not 1 <= self.sequence <= 2**64 - 1
            or type(self.record) is not PersonalAgentRunStoreEventV1
        ):
            raise PersonalAgentRunStoreCorruptError()
        record_type = _enum_value(self.record_type, PersonalAgentRunStoreRecordTypeV1)
        previous = (
            None
            if self.previous_record_digest is None
            else _raw_hash(self.previous_record_digest, error=PersonalAgentRunStoreCorruptError)
        )
        fingerprint = _raw_hash(
            self.record_fingerprint,
            error=PersonalAgentRunStoreCorruptError,
        )
        digest = _raw_hash(self.record_digest, error=PersonalAgentRunStoreCorruptError)
        object.__setattr__(self, "record_type", record_type)
        object.__setattr__(self, "previous_record_digest", previous)
        object.__setattr__(self, "record_fingerprint", fingerprint)
        object.__setattr__(self, "record_digest", digest)

    def unsigned_dict(self) -> dict[str, object]:
        return {
            "sequence": self.sequence,
            "record_type": PersonalAgentRunStoreRecordTypeV1.RUN_EVENT.value,
            "record": self.record.as_dict(),
            "previous_record_digest": self.previous_record_digest,
            "record_fingerprint": self.record_fingerprint,
        }

    @property
    def expected_record_fingerprint(self) -> str:
        return personal_agent_run_store_hash(self.record.as_dict())

    @property
    def expected_record_digest(self) -> str:
        return personal_agent_run_store_hash(self.unsigned_dict())

    def as_dict(self) -> dict[str, object]:
        return {**self.unsigned_dict(), "record_digest": self.record_digest}


@dataclass(frozen=True, slots=True)
class PersonalAgentRunStoreSnapshotV1:
    manifest: PersonalAgentRunStoreManifestV1
    envelopes: tuple[PersonalAgentRunStoreEnvelopeV1, ...]


def _replay(
    envelopes: tuple[PersonalAgentRunStoreEnvelopeV1, ...],
) -> dict[UUID, tuple[AgentRunSnapshotV1, ...]]:
    histories: dict[UUID, list[AgentRunSnapshotV1]] = {}
    for envelope in envelopes:
        event = envelope.record
        event_type = cast(AgentRunEventKindV1, event.event_type)
        snapshot = event.snapshot
        run_id = cast(UUID, snapshot.run_id)
        history = histories.setdefault(run_id, [])
        previous = history[-1] if history else None
        if event_type is AgentRunEventKindV1.ACCEPT:
            if previous is not None or snapshot.state is not AgentRunStateV1.ACCEPTED:
                raise PersonalAgentRunStoreCorruptError()
            if any(
                cast(AgentRunStateV1, item[-1].state) not in _TERMINAL_STATES
                for item in histories.values()
                if item
            ):
                raise PersonalAgentRunStoreCorruptError()
        else:
            if previous is None:
                raise PersonalAgentRunStoreCorruptError()
            if cast(AgentRunStateV1, previous.state) in _TERMINAL_STATES:
                raise PersonalAgentRunStoreCorruptError()
            if (
                snapshot.revision != previous.revision + 1
                or event.previous_snapshot_fingerprint != previous.fingerprint
                or snapshot.run_id != previous.run_id
            ):
                raise PersonalAgentRunStoreCorruptError()
            _validate_event_transition(event_type, previous, snapshot)
        history.append(snapshot)
    return {run_id: tuple(history) for run_id, history in histories.items()}


def _validate_event_transition(
    event_type: AgentRunEventKindV1,
    previous: AgentRunSnapshotV1,
    snapshot: AgentRunSnapshotV1,
) -> None:
    state = cast(AgentRunStateV1, snapshot.state)
    prior_state = cast(AgentRunStateV1, previous.state)
    expected: dict[AgentRunEventKindV1, frozenset[AgentRunStateV1]] = {
        AgentRunEventKindV1.START: frozenset(
            {AgentRunStateV1.ACTIVE, AgentRunStateV1.WAITING_OWNER}
        ),
        AgentRunEventKindV1.PAUSE: frozenset({AgentRunStateV1.PAUSED}),
        AgentRunEventKindV1.RESUME: frozenset(
            {AgentRunStateV1.ACTIVE, AgentRunStateV1.WAITING_OWNER}
        ),
        AgentRunEventKindV1.ANSWER: frozenset(
            {
                AgentRunStateV1.ACTIVE,
                AgentRunStateV1.WAITING_OWNER,
                AgentRunStateV1.WAITING_STAGE19,
                AgentRunStateV1.READY_TO_COMPLETE,
            }
        ),
        AgentRunEventKindV1.CONTINUE: frozenset(
            {
                AgentRunStateV1.ACTIVE,
                AgentRunStateV1.WAITING_OWNER,
                AgentRunStateV1.WAITING_STAGE19,
                AgentRunStateV1.READY_TO_COMPLETE,
            }
        ),
        AgentRunEventKindV1.SKIP: frozenset(
            {AgentRunStateV1.ACTIVE, AgentRunStateV1.READY_TO_COMPLETE}
        ),
        AgentRunEventKindV1.PREPARE_RESULT: frozenset({AgentRunStateV1.ACTIVE}),
        AgentRunEventKindV1.STAGE19_RESULT: frozenset({AgentRunStateV1.WAITING_STAGE19}),
        AgentRunEventKindV1.RECONCILE_RESULT: frozenset({AgentRunStateV1.WAITING_STAGE19}),
        AgentRunEventKindV1.COMPENSATION_RESULT: frozenset(
            {
                AgentRunStateV1.ACTIVE,
                AgentRunStateV1.WAITING_STAGE19,
                AgentRunStateV1.PAUSED,
            }
        ),
        AgentRunEventKindV1.SUPERSEDE: frozenset({AgentRunStateV1.SUPERSEDED}),
        AgentRunEventKindV1.ABANDON: frozenset({AgentRunStateV1.ABANDONED}),
        AgentRunEventKindV1.COMPLETE: frozenset({AgentRunStateV1.COMPLETED}),
    }
    allowed = expected.get(event_type)
    if allowed is None or state not in allowed:
        raise PersonalAgentRunStoreCorruptError()
    if event_type is AgentRunEventKindV1.START and prior_state is not AgentRunStateV1.ACCEPTED:
        raise PersonalAgentRunStoreCorruptError()
    if event_type is AgentRunEventKindV1.PAUSE and prior_state not in {
        AgentRunStateV1.ACTIVE,
        AgentRunStateV1.WAITING_OWNER,
        AgentRunStateV1.WAITING_STAGE19,
    }:
        raise PersonalAgentRunStoreCorruptError()
    if event_type is AgentRunEventKindV1.RESUME and prior_state is not AgentRunStateV1.PAUSED:
        raise PersonalAgentRunStoreCorruptError()
    if (
        event_type is AgentRunEventKindV1.COMPLETE
        and prior_state is not AgentRunStateV1.READY_TO_COMPLETE
    ):
        raise PersonalAgentRunStoreCorruptError()
    if event_type is AgentRunEventKindV1.SUPERSEDE and prior_state in _TERMINAL_STATES:
        raise PersonalAgentRunStoreCorruptError()
    if event_type is AgentRunEventKindV1.ABANDON and prior_state not in {
        AgentRunStateV1.ACTIVE,
        AgentRunStateV1.WAITING_OWNER,
        AgentRunStateV1.WAITING_STAGE19,
        AgentRunStateV1.PAUSED,
    }:
        raise PersonalAgentRunStoreCorruptError()


class PersonalAgentRunOperationalStore:
    """Owner-only JSONL store for one current Stage 20 Run."""

    def __init__(
        self,
        root: Path | os.PathLike[str],
        *,
        vault_root: Path | os.PathLike[str] | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        if not isinstance(root, os.PathLike):
            raise PersonalAgentRunStoreUnavailableError()
        self.root = Path(root).expanduser()
        self._clock = clock or (lambda: datetime.now(UTC))
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
            self._assert_owner_only(require_payload=False)
            with _StoreLock(self.lock_path):
                self._assert_owner_only(require_payload=False)
                self._initialize_unlocked()
                self._assert_owner_only()
                self._read_verified_unlocked()
        except PersonalAgentRunStoreError:
            raise
        except (OSError, ValueError, UnicodeError, RecursionError) as exc:
            raise PersonalAgentRunStoreUnavailableError() from exc

    @property
    def records_path(self) -> Path:
        return self.root / PERSONAL_AGENT_RUN_STORE_RECORD_FILE_NAME

    @property
    def runs_path(self) -> Path:
        return self.records_path

    @property
    def manifest_path(self) -> Path:
        return self.root / PERSONAL_AGENT_RUN_STORE_MANIFEST_FILE_NAME

    @property
    def lock_path(self) -> Path:
        return self.root / PERSONAL_AGENT_RUN_STORE_LOCK_FILE_NAME

    @property
    def manifest(self) -> PersonalAgentRunStoreManifestV1:
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
                raise ValueError("store in release or vault")
            if vault_root is not None:
                vault = Path(vault_root).expanduser().resolve(strict=True)
                if candidate == vault or candidate.is_relative_to(vault):
                    raise ValueError("store in vault")
            git_root = self._git_root(candidate)
            if git_root is not None and candidate.is_relative_to(git_root):
                raise ValueError("store in repository")
        except (OSError, RuntimeError, ValueError) as exc:
            raise PersonalAgentRunStoreUnavailableError() from exc

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

    def _assert_owner_only(self, *, require_payload: bool = True) -> None:
        if os.name == "nt":
            return
        paths = (
            (self.root, 0o700, True),
            (self.lock_path, 0o600, False),
            (self.records_path, 0o600, require_payload),
            (self.manifest_path, 0o600, require_payload),
        )
        try:
            for path, expected_mode, required in paths:
                if not os.path.lexists(path):
                    if required:
                        raise FileNotFoundError(path)
                    continue
                if path.is_symlink():
                    raise ValueError("store path is symlink")
                item_stat = path.stat()
                if stat.S_IMODE(item_stat.st_mode) != expected_mode:
                    raise ValueError("store permissions")
                if path == self.root and not stat.S_ISDIR(item_stat.st_mode):
                    raise ValueError("store root is not directory")
                if path != self.root and not stat.S_ISREG(item_stat.st_mode):
                    raise ValueError("store payload is not regular")
        except OSError as exc:
            raise PersonalAgentRunStoreUnavailableError() from exc

    def _initialize_unlocked(self) -> None:
        present = (self.records_path.exists(), self.manifest_path.exists())
        if not any(present):
            self._write_bytes_atomic(self.records_path, b"")
            self._write_manifest_atomic(
                PersonalAgentRunStoreManifestV1(
                    PERSONAL_AGENT_RUN_STORE_FORMAT_VERSION,
                    1,
                    0,
                    None,
                    AGENT_POLICY_ID,
                    AGENT_POLICY_FINGERPRINT,
                )
            )
        elif not all(present):
            raise PersonalAgentRunStoreCorruptError()
        else:
            self._read_verified_unlocked()

    @staticmethod
    def _reject_duplicate_pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, value in pairs:
            if key in result:
                raise PersonalAgentRunStoreCorruptError()
            result[key] = value
        return result

    def _read_manifest_unlocked(self) -> PersonalAgentRunStoreManifestV1:
        try:
            raw = self.manifest_path.read_bytes()
            decoded = json.loads(
                raw.decode("utf-8"),
                object_pairs_hook=self._reject_duplicate_pairs,
                parse_constant=lambda _: (_ for _ in ()).throw(ValueError()),
            )
            if _canonical_bytes(decoded) != raw:
                raise ValueError("manifest not canonical")
            data = _wire_dict(
                decoded,
                {
                    "format_version",
                    "next_sequence",
                    "record_count",
                    "last_record_digest",
                    "policy_id",
                    "policy_fingerprint",
                },
            )
            return PersonalAgentRunStoreManifestV1(
                cast(int, data["format_version"]),
                cast(int, data["next_sequence"]),
                cast(int, data["record_count"]),
                cast(str | None, data["last_record_digest"]),
                cast(str, data["policy_id"]),
                cast(str, data["policy_fingerprint"]),
            )
        except PersonalAgentRunStoreError:
            raise
        except (OSError, UnicodeError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise PersonalAgentRunStoreCorruptError() from exc

    @staticmethod
    def _event_from_dict(value: object) -> PersonalAgentRunStoreEventV1:
        data = _wire_dict(
            value,
            {
                "event_id",
                "event_type",
                "operation_id_fingerprint",
                "intent_fingerprint",
                "previous_snapshot_fingerprint",
                "snapshot",
            },
        )
        try:
            snapshot = AgentRunSnapshotV1.from_dict(data["snapshot"])
            return PersonalAgentRunStoreEventV1(
                event_id=cast(UUID | str, data["event_id"]),
                event_type=cast(AgentRunEventKindV1 | str, data["event_type"]),
                operation_id_fingerprint=cast(str, data["operation_id_fingerprint"]),
                intent_fingerprint=cast(str, data["intent_fingerprint"]),
                previous_snapshot_fingerprint=cast(
                    str | None, data["previous_snapshot_fingerprint"]
                ),
                snapshot=snapshot,
            )
        except (AgentRunStoreError, AgentRunSnapshotInvalidError, PersonalAgentError) as exc:
            raise PersonalAgentRunStoreCorruptError() from exc
        except (TypeError, ValueError, UnicodeError, OverflowError) as exc:
            raise PersonalAgentRunStoreCorruptError() from exc

    def _envelope_from_dict(self, value: object) -> PersonalAgentRunStoreEnvelopeV1:
        data = _wire_dict(
            value,
            {
                "sequence",
                "record_type",
                "record",
                "previous_record_digest",
                "record_fingerprint",
                "record_digest",
            },
        )
        return PersonalAgentRunStoreEnvelopeV1(
            sequence=cast(int, data["sequence"]),
            record_type=cast(PersonalAgentRunStoreRecordTypeV1 | str, data["record_type"]),
            record=self._event_from_dict(data["record"]),
            previous_record_digest=cast(str | None, data["previous_record_digest"]),
            record_fingerprint=cast(str, data["record_fingerprint"]),
            record_digest=cast(str, data["record_digest"]),
        )

    def _read_verified_unlocked(self) -> PersonalAgentRunStoreSnapshotV1:
        if self.root.is_symlink() or any(
            path.is_symlink() for path in (self.records_path, self.manifest_path)
        ):
            raise PersonalAgentRunStoreCorruptError()
        self._assert_owner_only()
        manifest = self._read_manifest_unlocked()
        try:
            raw = self.records_path.read_bytes()
        except FileNotFoundError as exc:
            raise PersonalAgentRunStoreCorruptError() from exc
        except OSError as exc:
            raise PersonalAgentRunStoreUnavailableError() from exc
        if not raw:
            if manifest.record_count != 0 or manifest.last_record_digest is not None:
                raise PersonalAgentRunStoreCorruptError()
            return PersonalAgentRunStoreSnapshotV1(manifest, ())
        if raw.startswith(b"\xef\xbb\xbf") or not raw.endswith(b"\n"):
            raise PersonalAgentRunStoreCorruptError()
        envelopes: list[PersonalAgentRunStoreEnvelopeV1] = []
        previous: str | None = None
        seen_event_ids: set[UUID] = set()
        seen_operations: dict[str, str] = {}
        for line in raw.splitlines(keepends=True):
            if line.endswith(b"\r\n") or not line.endswith(b"\n"):
                raise PersonalAgentRunStoreCorruptError()
            payload = line[:-1]
            if not payload or len(payload) > PERSONAL_AGENT_RUN_STORE_MAX_RECORD_BYTES:
                raise PersonalAgentRunStoreCorruptError()
            try:
                decoded = json.loads(
                    payload.decode("utf-8"),
                    object_pairs_hook=self._reject_duplicate_pairs,
                    parse_constant=lambda _: (_ for _ in ()).throw(ValueError()),
                )
                envelope = self._envelope_from_dict(decoded)
                if (
                    _canonical_bytes(decoded) != payload
                    or envelope.expected_record_fingerprint != envelope.record_fingerprint
                    or envelope.expected_record_digest != envelope.record_digest
                    or envelope.sequence != len(envelopes) + 1
                    or envelope.previous_record_digest != previous
                ):
                    raise ValueError("record chain")
                event_id = cast(UUID, envelope.record.event_id)
                if event_id in seen_event_ids:
                    raise ValueError("duplicate event")
                operation = envelope.record.operation_id_fingerprint
                prior_intent = seen_operations.get(operation)
                if prior_intent is not None and prior_intent != envelope.record.intent_fingerprint:
                    raise ValueError("operation intent conflict")
                seen_operations[operation] = envelope.record.intent_fingerprint
            except PersonalAgentRunStoreError:
                raise
            except (
                AgentRunStoreError,
                AgentRunSnapshotInvalidError,
                PersonalAgentError,
                TypeError,
                ValueError,
                UnicodeError,
                OverflowError,
                json.JSONDecodeError,
            ) as exc:
                raise PersonalAgentRunStoreCorruptError() from exc
            envelopes.append(envelope)
            seen_event_ids.add(event_id)
            previous = envelope.record_digest
        if (
            len(envelopes) > PERSONAL_AGENT_RUN_STORE_MAX_RECORDS
            or manifest.record_count != len(envelopes)
            or manifest.next_sequence != len(envelopes) + 1
            or manifest.last_record_digest != previous
        ):
            raise PersonalAgentRunStoreCorruptError()
        _replay(tuple(envelopes))
        return PersonalAgentRunStoreSnapshotV1(manifest, tuple(envelopes))

    def read_verified_snapshot(self) -> PersonalAgentRunStoreSnapshotV1:
        try:
            with _StoreLock(self.lock_path):
                return self._read_verified_unlocked()
        except PersonalAgentRunStoreError:
            raise
        except (OSError, ValueError, UnicodeError, RecursionError) as exc:
            raise PersonalAgentRunStoreUnavailableError() from exc

    verified_snapshot = read_verified_snapshot

    def read_events(self) -> tuple[PersonalAgentRunStoreEnvelopeV1, ...]:
        return self.read_verified_snapshot().envelopes

    read_records = read_events

    def read_manifest(self) -> PersonalAgentRunStoreManifestV1:
        return self.read_verified_snapshot().manifest

    def _append_unlocked(
        self,
        verified: PersonalAgentRunStoreSnapshotV1,
        event: PersonalAgentRunStoreEventV1,
    ) -> AgentRunSnapshotV1:
        for envelope in verified.envelopes:
            existing = envelope.record
            if existing.operation_id_fingerprint != event.operation_id_fingerprint:
                continue
            if existing.intent_fingerprint == event.intent_fingerprint:
                return existing.snapshot
            raise PersonalAgentRunStoreIdempotencyConflictError()
        if verified.manifest.record_count >= PERSONAL_AGENT_RUN_STORE_MAX_RECORDS:
            raise PersonalAgentRunStoreCapacityError()
        current = self._current_from_verified(verified)
        event_type = cast(AgentRunEventKindV1, event.event_type)
        if event_type is AgentRunEventKindV1.ACCEPT and current is not None:
            raise PersonalAgentRunStoreStateConflictError()
        sequence = verified.manifest.next_sequence
        record_fingerprint = personal_agent_run_store_hash(event.as_dict())
        unsigned = {
            "sequence": sequence,
            "record_type": PersonalAgentRunStoreRecordTypeV1.RUN_EVENT.value,
            "record": event.as_dict(),
            "previous_record_digest": verified.manifest.last_record_digest,
            "record_fingerprint": record_fingerprint,
        }
        envelope = PersonalAgentRunStoreEnvelopeV1(
            sequence=sequence,
            record_type=PersonalAgentRunStoreRecordTypeV1.RUN_EVENT,
            record=event,
            previous_record_digest=verified.manifest.last_record_digest,
            record_fingerprint=record_fingerprint,
            record_digest=personal_agent_run_store_hash(unsigned),
        )
        line = _canonical_bytes(envelope.as_dict()) + b"\n"
        if len(line) > PERSONAL_AGENT_RUN_STORE_MAX_RECORD_BYTES:
            raise PersonalAgentRunStoreCapacityError()
        self._append_bytes_durable(self.records_path, line)
        self._write_manifest_atomic(
            PersonalAgentRunStoreManifestV1(
                PERSONAL_AGENT_RUN_STORE_FORMAT_VERSION,
                sequence + 1,
                sequence,
                envelope.record_digest,
                AGENT_POLICY_ID,
                AGENT_POLICY_FINGERPRINT,
            )
        )
        reread = self._read_verified_unlocked()
        if not reread.envelopes or reread.envelopes[-1] != envelope:
            raise PersonalAgentRunStoreUnavailableError()
        return event.snapshot

    @staticmethod
    def _current_from_verified(
        verified: PersonalAgentRunStoreSnapshotV1,
    ) -> AgentRunSnapshotV1 | None:
        histories = _replay(verified.envelopes)
        current = tuple(
            history[-1]
            for history in histories.values()
            if history and cast(AgentRunStateV1, history[-1].state) not in _TERMINAL_STATES
        )
        if len(current) > 1:
            raise PersonalAgentRunStoreCorruptError()
        return current[0] if current else None

    def append(
        self,
        snapshot: AgentRunSnapshotV1,
        *,
        operation_id: str,
        event_kind: AgentRunEventKindV1,
    ) -> AgentRunSnapshotV1:
        if type(snapshot) is not AgentRunSnapshotV1 or type(event_kind) is not AgentRunEventKindV1:
            raise PersonalAgentRunStoreError()
        operation_fingerprint = _operation_fingerprint(operation_id)
        try:
            with _StoreLock(self.lock_path):
                verified = self._read_verified_unlocked()
                histories = _replay(verified.envelopes)
                prior = histories.get(cast(UUID, snapshot.run_id), ())
                existing = next(
                    (
                        envelope.record
                        for envelope in verified.envelopes
                        if envelope.record.operation_id_fingerprint == operation_fingerprint
                    ),
                    None,
                )
                previous = prior[-1] if prior else None
                previous_fingerprint = (
                    existing.previous_snapshot_fingerprint
                    if existing is not None
                    else None
                    if previous is None
                    else previous.fingerprint
                )
                intent = personal_agent_hash(
                    {
                        "event_type": event_kind.value,
                        "run_id": str(snapshot.run_id),
                        "revision": snapshot.revision,
                        "previous_snapshot_fingerprint": previous_fingerprint,
                        "snapshot_fingerprint": snapshot.fingerprint,
                    }
                )
                if existing is not None:
                    if existing.intent_fingerprint != intent:
                        raise PersonalAgentRunStoreIdempotencyConflictError()
                    return existing.snapshot
                event = PersonalAgentRunStoreEventV1(
                    event_id=uuid7(),
                    event_type=event_kind,
                    operation_id_fingerprint=operation_fingerprint,
                    intent_fingerprint=intent,
                    previous_snapshot_fingerprint=previous_fingerprint,
                    snapshot=snapshot,
                )
                if previous is not None and snapshot.revision != previous.revision + 1:
                    raise PersonalAgentRunStoreStateConflictError()
                if previous is not None and snapshot.fingerprint == previous.fingerprint:
                    raise PersonalAgentRunStoreStateConflictError()
                return self._append_unlocked(verified, event)
        except PersonalAgentRunStoreError:
            raise
        except (AgentRunConflictError, AgentRunSnapshotInvalidError, PersonalAgentError) as exc:
            raise PersonalAgentRunStoreError() from exc
        except (OSError, TypeError, ValueError, UnicodeError, RecursionError) as exc:
            raise PersonalAgentRunStoreUnavailableError() from exc

    def latest(self, run_id: UUID | str) -> AgentRunSnapshotV1 | None:
        parsed = _uuid7(run_id, error=PersonalAgentRunStoreError)
        histories = _replay(self.read_verified_snapshot().envelopes)
        history = histories.get(parsed)
        return None if not history else history[-1]

    def current(self) -> AgentRunSnapshotV1 | None:
        return self._current_from_verified(self.read_verified_snapshot())

    def history(self, run_id: UUID | str) -> tuple[AgentRunSnapshotV1, ...]:
        parsed = _uuid7(run_id, error=PersonalAgentRunStoreError)
        histories = _replay(self.read_verified_snapshot().envelopes)
        return histories.get(parsed, ())

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

    def _write_manifest_atomic(self, manifest: PersonalAgentRunStoreManifestV1) -> None:
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


def derive_personal_agent_run_store_root(
    env_file: Path | os.PathLike[str] | None,
) -> Path | None:
    """Derive the additive store beside one explicit existing env file."""

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
        prospective = parent / PERSONAL_AGENT_RUN_STORE_PARENT_NAME
        root = prospective / PERSONAL_AGENT_RUN_STORE_DIRECTORY_NAME
        if prospective.exists() and (prospective.is_symlink() or not prospective.is_dir()):
            return None
        if root.exists() and (root.is_symlink() or not root.is_dir()):
            return None
        return root
    except OSError, RuntimeError, ValueError:
        return None


PersonalAgentRunStore = PersonalAgentRunOperationalStore
PersonalAgentOperationalStore = PersonalAgentRunOperationalStore
derive_personal_agent_store_root = derive_personal_agent_run_store_root


__all__ = [
    "PERSONAL_AGENT_RUN_STORE_DIRECTORY_NAME",
    "PERSONAL_AGENT_RUN_STORE_FORMAT_VERSION",
    "PERSONAL_AGENT_RUN_STORE_LOCK_FILE_NAME",
    "PERSONAL_AGENT_RUN_STORE_MANIFEST_FILE_NAME",
    "PERSONAL_AGENT_RUN_STORE_MAX_OPERATION_ID_BYTES",
    "PERSONAL_AGENT_RUN_STORE_MAX_RECORDS",
    "PERSONAL_AGENT_RUN_STORE_MAX_RECORD_BYTES",
    "PERSONAL_AGENT_RUN_STORE_PARENT_NAME",
    "PERSONAL_AGENT_RUN_STORE_RECORD_FILE_NAME",
    "PersonalAgentOperationalStore",
    "PersonalAgentRunOperationalStore",
    "PersonalAgentRunStore",
    "PersonalAgentRunStoreCapacityError",
    "PersonalAgentRunStoreCorruptError",
    "PersonalAgentRunStoreEnvelopeV1",
    "PersonalAgentRunStoreError",
    "PersonalAgentRunStoreEventV1",
    "PersonalAgentRunStoreIdempotencyConflictError",
    "PersonalAgentRunStoreManifestV1",
    "PersonalAgentRunStoreRecordType",
    "PersonalAgentRunStoreRecordTypeV1",
    "PersonalAgentRunStoreSnapshotV1",
    "PersonalAgentRunStoreStateConflictError",
    "PersonalAgentRunStoreUnavailableError",
    "derive_personal_agent_run_store_root",
    "derive_personal_agent_store_root",
    "personal_agent_run_store_hash",
]
