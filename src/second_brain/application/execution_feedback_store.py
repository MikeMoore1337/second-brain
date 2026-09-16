"""Bounded append-only operational storage for Stage 18 execution events."""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any, Final, cast
from uuid import UUID

from second_brain.application.adaptive_cognitive_twin_store import _StoreLock
from second_brain.application.execution_feedback import (
    EXECUTION_POLICY_FINGERPRINT,
    EXECUTION_POLICY_ID,
    ExecutionEventTypeV1,
    ExecutionEventV1,
    ExecutionFeedbackError,
    ExecutionFeedbackLifecycleError,
    ExecutionFeedbackStaleError,
    ExecutionSourceStatusV1,
    bind_execution_event_to_plan,
    execution_event_fingerprint,
    replay_execution_events,
    validate_execution_event_for_plan,
)
from second_brain.application.personal_planning_store import PlanningPlanV1

EXECUTION_FEEDBACK_STORE_PARENT_NAME: Final[str] = "prospective-audit"
EXECUTION_FEEDBACK_STORE_DIRECTORY_NAME: Final[str] = "execution-feedback"
EXECUTION_FEEDBACK_STORE_RECORD_FILE_NAME: Final[str] = "events.jsonl"
EXECUTION_FEEDBACK_STORE_MANIFEST_FILE_NAME: Final[str] = "manifest.json"
EXECUTION_FEEDBACK_STORE_LOCK_FILE_NAME: Final[str] = ".store.lock"
EXECUTION_FEEDBACK_STORE_FORMAT_VERSION: Final[int] = 1
EXECUTION_FEEDBACK_STORE_MAX_RECORDS: Final[int] = 32768
EXECUTION_FEEDBACK_STORE_MAX_RECORD_BYTES: Final[int] = 256 * 1024
EXECUTION_FEEDBACK_STORE_MAX_EVENT_BYTES: Final[int] = 192 * 1024

_RAW_HASH_PATTERN: Final[re.Pattern[str]] = re.compile(r"[0-9a-f]{64}\Z", re.ASCII)


class ExecutionFeedbackStoreError(RuntimeError):
    """Base safe error for the Stage 18 operational store."""

    code: str = "execution_feedback_store_error"

    def __init__(self, message: str | None = None) -> None:
        super().__init__(message or self.code)


class ExecutionFeedbackStoreUnavailableError(ExecutionFeedbackStoreError):
    """The store cannot be safely initialized or mutated."""

    code = "store_unavailable"


class ExecutionFeedbackStoreCorruptError(ExecutionFeedbackStoreError):
    """The JSONL ledger or manifest failed integrity validation."""

    code = "store_corrupt"


class ExecutionFeedbackStoreIdempotencyConflictError(ExecutionFeedbackStoreError):
    """An operation fingerprint was reused with a different intent."""

    code = "idempotency_conflict"


class ExecutionFeedbackStoreCapacityError(ExecutionFeedbackStoreError):
    """The bounded record or per-item event limit was reached."""

    code = "store_capacity"


class ExecutionFeedbackStoreRecordTypeV1(StrEnum):
    """Closed envelope record type."""

    EXECUTION_EVENT = "execution_event"


ExecutionFeedbackStoreRecordType = ExecutionFeedbackStoreRecordTypeV1


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
        raise ExecutionFeedbackStoreCorruptError() from exc


def execution_feedback_store_hash(value: object) -> str:
    """Hash canonical JSON for store envelopes and intent records."""

    return hashlib.sha256(_canonical_bytes(value)).hexdigest()


def _raw_hash(value: object, *, error: type[ExecutionFeedbackStoreError]) -> str:
    if type(value) is not str or _RAW_HASH_PATTERN.fullmatch(value) is None:
        raise error()
    return value


def _wire_dict(value: object, expected: set[str]) -> dict[str, object]:
    if type(value) is not dict or any(type(key) is not str for key in value):
        raise ExecutionFeedbackStoreCorruptError()
    data = cast(dict[str, object], value)
    if set(data) != expected:
        raise ExecutionFeedbackStoreCorruptError()
    return data


def _wire_list(value: object) -> list[object]:
    if type(value) is not list:
        raise ExecutionFeedbackStoreCorruptError()
    return cast(list[object], value)


def _reject_duplicate_pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ExecutionFeedbackStoreCorruptError()
        result[key] = value
    return result


def execution_event_intent_fingerprint(event: ExecutionEventV1) -> str:
    """Hash an event intent without making generated event id part of retry identity."""

    if type(event) is not ExecutionEventV1:
        raise ExecutionFeedbackStoreCorruptError()
    payload = event.as_dict()
    payload.pop("event_id", None)
    return execution_feedback_store_hash(payload)


@dataclass(frozen=True, slots=True)
class ExecutionFeedbackStoreManifestV1:
    """Atomic manifest for the complete event generation."""

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
            or self.format_version != EXECUTION_FEEDBACK_STORE_FORMAT_VERSION
        ):
            raise ExecutionFeedbackStoreCorruptError()
        if (
            type(self.next_sequence) is not int
            or isinstance(self.next_sequence, bool)
            or not 1 <= self.next_sequence <= (1 << 64) - 1
        ):
            raise ExecutionFeedbackStoreCorruptError()
        if (
            type(self.record_count) is not int
            or isinstance(self.record_count, bool)
            or not 0 <= self.record_count <= EXECUTION_FEEDBACK_STORE_MAX_RECORDS
            or self.next_sequence != self.record_count + 1
        ):
            raise ExecutionFeedbackStoreCorruptError()
        digest = (
            None
            if self.last_record_digest is None
            else _raw_hash(self.last_record_digest, error=ExecutionFeedbackStoreCorruptError)
        )
        if (self.record_count == 0) != (digest is None):
            raise ExecutionFeedbackStoreCorruptError()
        if (
            self.policy_id != EXECUTION_POLICY_ID
            or self.policy_fingerprint != EXECUTION_POLICY_FINGERPRINT
        ):
            raise ExecutionFeedbackStoreCorruptError()
        object.__setattr__(self, "last_record_digest", digest)
        object.__setattr__(self, "policy_id", EXECUTION_POLICY_ID)
        object.__setattr__(self, "policy_fingerprint", EXECUTION_POLICY_FINGERPRINT)

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
class ExecutionFeedbackStoreEnvelopeV1:
    """One canonical hash-chained JSONL envelope."""

    sequence: int
    record_type: ExecutionFeedbackStoreRecordTypeV1 | str
    record: ExecutionEventV1
    intent_fingerprint: str
    previous_record_digest: str | None
    event_fingerprint: str
    record_digest: str

    def __post_init__(self) -> None:
        if (
            type(self.sequence) is not int
            or isinstance(self.sequence, bool)
            or not 1 <= self.sequence <= (1 << 64) - 1
        ):
            raise ExecutionFeedbackStoreCorruptError()
        if type(self.record_type) is not ExecutionFeedbackStoreRecordTypeV1:
            if type(self.record_type) is not str or self.record_type != "execution_event":
                raise ExecutionFeedbackStoreCorruptError()
            record_type = ExecutionFeedbackStoreRecordTypeV1.EXECUTION_EVENT
        else:
            record_type = self.record_type
        if type(self.record) is not ExecutionEventV1:
            raise ExecutionFeedbackStoreCorruptError()
        intent = _raw_hash(self.intent_fingerprint, error=ExecutionFeedbackStoreCorruptError)
        previous = (
            None
            if self.previous_record_digest is None
            else _raw_hash(self.previous_record_digest, error=ExecutionFeedbackStoreCorruptError)
        )
        event_fingerprint = _raw_hash(
            self.event_fingerprint, error=ExecutionFeedbackStoreCorruptError
        )
        record_digest = _raw_hash(self.record_digest, error=ExecutionFeedbackStoreCorruptError)
        object.__setattr__(self, "record_type", record_type)
        object.__setattr__(self, "intent_fingerprint", intent)
        object.__setattr__(self, "previous_record_digest", previous)
        object.__setattr__(self, "event_fingerprint", event_fingerprint)
        object.__setattr__(self, "record_digest", record_digest)

    def unsigned_dict(self) -> dict[str, object]:
        return {
            "sequence": self.sequence,
            "record_type": cast(ExecutionFeedbackStoreRecordTypeV1, self.record_type).value,
            "record": self.record.as_dict(),
            "intent_fingerprint": self.intent_fingerprint,
            "previous_record_digest": self.previous_record_digest,
            "event_fingerprint": self.event_fingerprint,
        }

    @property
    def expected_event_fingerprint(self) -> str:
        return execution_event_fingerprint(self.record)

    @property
    def expected_intent_fingerprint(self) -> str:
        return execution_event_intent_fingerprint(self.record)

    @property
    def expected_record_digest(self) -> str:
        return execution_feedback_store_hash(self.unsigned_dict())

    def as_dict(self) -> dict[str, object]:
        return {**self.unsigned_dict(), "record_digest": self.record_digest}


@dataclass(frozen=True, slots=True)
class ExecutionFeedbackStoreVerifiedSnapshotV1:
    manifest: ExecutionFeedbackStoreManifestV1
    envelopes: tuple[ExecutionFeedbackStoreEnvelopeV1, ...]


def _chain_key(event: ExecutionEventV1) -> tuple[UUID, str, str, str]:
    return (
        cast(UUID, event.planning_snapshot_id),
        event.planning_snapshot_fingerprint,
        event.item_id,
        event.accepted_item_fingerprint,
    )


class ExecutionFeedbackOperationalStore:
    """Strict owner-only JSONL storage for Stage 18 events."""

    def __init__(
        self,
        root: Path | os.PathLike[str],
        *,
        vault_root: Path | os.PathLike[str] | None = None,
        expected_owner_group: tuple[str, str] | None = None,
    ) -> None:
        if not isinstance(root, os.PathLike):
            raise ExecutionFeedbackStoreUnavailableError()
        if expected_owner_group is not None and (
            type(expected_owner_group) is not tuple
            or len(expected_owner_group) != 2
            or any(type(item) is not str or not item for item in expected_owner_group)
        ):
            raise ExecutionFeedbackStoreUnavailableError()
        self.root = Path(root).expanduser()
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
        except ExecutionFeedbackStoreError:
            raise
        except (OSError, ValueError, UnicodeError, RecursionError) as exc:
            raise ExecutionFeedbackStoreUnavailableError() from exc

    @property
    def records_path(self) -> Path:
        return self.root / EXECUTION_FEEDBACK_STORE_RECORD_FILE_NAME

    @property
    def events_path(self) -> Path:
        return self.records_path

    @property
    def manifest_path(self) -> Path:
        return self.root / EXECUTION_FEEDBACK_STORE_MANIFEST_FILE_NAME

    @property
    def lock_path(self) -> Path:
        return self.root / EXECUTION_FEEDBACK_STORE_LOCK_FILE_NAME

    @property
    def manifest(self) -> ExecutionFeedbackStoreManifestV1:
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
                if not isinstance(vault_root, os.PathLike):
                    raise ValueError("vault root")
                vault = Path(vault_root).expanduser().resolve(strict=True)
                if candidate == vault or candidate.is_relative_to(vault):
                    raise ValueError("store in vault")
            git_root = self._git_root(candidate)
            if git_root is not None and candidate.is_relative_to(git_root):
                raise ValueError("store in repository")
        except (OSError, RuntimeError, ValueError) as exc:
            raise ExecutionFeedbackStoreUnavailableError() from exc

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
        present = (self.records_path.exists(), self.manifest_path.exists())
        if not any(present):
            self._write_bytes_atomic(self.records_path, b"")
            self._write_manifest_atomic(
                ExecutionFeedbackStoreManifestV1(
                    EXECUTION_FEEDBACK_STORE_FORMAT_VERSION,
                    1,
                    0,
                    None,
                    EXECUTION_POLICY_ID,
                    EXECUTION_POLICY_FINGERPRINT,
                )
            )
            return
        if not all(present):
            raise ExecutionFeedbackStoreCorruptError()
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
            raise ExecutionFeedbackStoreUnavailableError() from exc

    @staticmethod
    def _manifest_from_dict(value: object) -> ExecutionFeedbackStoreManifestV1:
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
        return ExecutionFeedbackStoreManifestV1(
            format_version=cast(int, data["format_version"]),
            next_sequence=cast(int, data["next_sequence"]),
            record_count=cast(int, data["record_count"]),
            last_record_digest=cast(str | None, data["last_record_digest"]),
            policy_id=cast(str, data["policy_id"]),
            policy_fingerprint=cast(str, data["policy_fingerprint"]),
        )

    @staticmethod
    def _event_from_dict(value: object) -> ExecutionEventV1:
        try:
            return ExecutionEventV1.from_dict(value)
        except ExecutionFeedbackError as exc:
            raise ExecutionFeedbackStoreCorruptError() from exc
        except (TypeError, ValueError, UnicodeError, OverflowError) as exc:
            raise ExecutionFeedbackStoreCorruptError() from exc

    @classmethod
    def _envelope_from_dict(cls, value: object) -> ExecutionFeedbackStoreEnvelopeV1:
        data = _wire_dict(
            value,
            {
                "sequence",
                "record_type",
                "record",
                "intent_fingerprint",
                "previous_record_digest",
                "event_fingerprint",
                "record_digest",
            },
        )
        return ExecutionFeedbackStoreEnvelopeV1(
            sequence=cast(int, data["sequence"]),
            record_type=cast(str, data["record_type"]),
            record=cls._event_from_dict(data["record"]),
            intent_fingerprint=cast(str, data["intent_fingerprint"]),
            previous_record_digest=cast(str | None, data["previous_record_digest"]),
            event_fingerprint=cast(str, data["event_fingerprint"]),
            record_digest=cast(str, data["record_digest"]),
        )

    def _read_manifest_unlocked(self) -> ExecutionFeedbackStoreManifestV1:
        try:
            raw = self.manifest_path.read_bytes()
            data = json.loads(raw.decode("utf-8"), object_pairs_hook=_reject_duplicate_pairs)
            if _canonical_bytes(data) != raw:
                raise ValueError("manifest not canonical")
            return self._manifest_from_dict(data)
        except ExecutionFeedbackStoreError:
            raise
        except (OSError, UnicodeError, ValueError, TypeError, json.JSONDecodeError) as exc:
            raise ExecutionFeedbackStoreCorruptError() from exc

    def _read_verified_unlocked(self) -> ExecutionFeedbackStoreVerifiedSnapshotV1:
        if self.root.is_symlink() or any(
            path.is_symlink() for path in (self.records_path, self.manifest_path)
        ):
            raise ExecutionFeedbackStoreCorruptError()
        self._assert_owner_only_unlocked()
        manifest = self._read_manifest_unlocked()
        try:
            raw = self.records_path.read_bytes()
        except FileNotFoundError as exc:
            raise ExecutionFeedbackStoreCorruptError() from exc
        except OSError as exc:
            raise ExecutionFeedbackStoreUnavailableError() from exc
        if not raw:
            if manifest.record_count != 0 or manifest.last_record_digest is not None:
                raise ExecutionFeedbackStoreCorruptError()
            return ExecutionFeedbackStoreVerifiedSnapshotV1(manifest, ())
        if raw.startswith(b"\xef\xbb\xbf") or not raw.endswith(b"\n"):
            raise ExecutionFeedbackStoreCorruptError()
        envelopes: list[ExecutionFeedbackStoreEnvelopeV1] = []
        previous: str | None = None
        seen_event_ids: set[UUID] = set()
        seen_operations: set[str] = set()
        for line in raw.splitlines(keepends=True):
            if not line.endswith(b"\n") or line.endswith(b"\r\n"):
                raise ExecutionFeedbackStoreCorruptError()
            payload = line[:-1]
            if not payload or len(payload) > EXECUTION_FEEDBACK_STORE_MAX_RECORD_BYTES:
                raise ExecutionFeedbackStoreCorruptError()
            try:
                data = json.loads(
                    payload.decode("utf-8"),
                    object_pairs_hook=_reject_duplicate_pairs,
                    parse_constant=lambda _: (_ for _ in ()).throw(ValueError()),
                )
                envelope = self._envelope_from_dict(data)
                if _canonical_bytes(data) != payload:
                    raise ValueError("record not canonical")
                if envelope.expected_event_fingerprint != envelope.event_fingerprint:
                    raise ValueError("event fingerprint")
                if envelope.expected_intent_fingerprint != envelope.intent_fingerprint:
                    raise ValueError("intent fingerprint")
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
                if (
                    len(_canonical_bytes(envelope.record.as_dict()))
                    > EXECUTION_FEEDBACK_STORE_MAX_EVENT_BYTES
                ):
                    raise ValueError("event too large")
            except ExecutionFeedbackStoreError:
                raise
            except (
                TypeError,
                ValueError,
                UnicodeError,
                OverflowError,
                json.JSONDecodeError,
            ) as exc:
                raise ExecutionFeedbackStoreCorruptError() from exc
            envelopes.append(envelope)
            seen_event_ids.add(event_id)
            seen_operations.add(operation)
            previous = envelope.record_digest
        if (
            manifest.record_count != len(envelopes)
            or manifest.next_sequence != len(envelopes) + 1
            or manifest.last_record_digest != previous
        ):
            raise ExecutionFeedbackStoreCorruptError()
        if len(envelopes) > EXECUTION_FEEDBACK_STORE_MAX_RECORDS:
            raise ExecutionFeedbackStoreCorruptError()
        self._validate_all_chains(tuple(envelopes))
        return ExecutionFeedbackStoreVerifiedSnapshotV1(manifest, tuple(envelopes))

    @staticmethod
    def _validate_all_chains(envelopes: tuple[ExecutionFeedbackStoreEnvelopeV1, ...]) -> None:
        grouped: dict[tuple[UUID, str, str, str], list[ExecutionEventV1]] = {}
        for envelope in envelopes:
            grouped.setdefault(_chain_key(envelope.record), []).append(envelope.record)
        try:
            for events in grouped.values():
                replay_execution_events(tuple(events))
        except (ExecutionFeedbackError, ExecutionFeedbackLifecycleError) as exc:
            raise ExecutionFeedbackStoreCorruptError() from exc

    def read_verified_snapshot(self) -> ExecutionFeedbackStoreVerifiedSnapshotV1:
        try:
            with _StoreLock(self.lock_path):
                return self._read_verified_unlocked()
        except ExecutionFeedbackStoreError:
            raise
        except (OSError, ValueError, UnicodeError, RecursionError) as exc:
            raise ExecutionFeedbackStoreUnavailableError() from exc

    verified_snapshot = read_verified_snapshot

    def read_events(self) -> tuple[ExecutionFeedbackStoreEnvelopeV1, ...]:
        return self.read_verified_snapshot().envelopes

    read_records = read_events

    def read_manifest(self) -> ExecutionFeedbackStoreManifestV1:
        return self.read_verified_snapshot().manifest

    def read_event_records(self) -> tuple[ExecutionEventV1, ...]:
        return tuple(envelope.record for envelope in self.read_events())

    def events_for_item(
        self,
        *,
        planning_snapshot_id: UUID | str,
        planning_snapshot_fingerprint: str,
        item_id: str,
        accepted_item_fingerprint: str,
    ) -> tuple[ExecutionFeedbackStoreEnvelopeV1, ...]:
        snapshot_id = planning_snapshot_id
        try:
            from second_brain.domain.models import parse_uuid7

            snapshot_id = parse_uuid7(planning_snapshot_id)
        except (TypeError, ValueError, OverflowError) as exc:
            raise ExecutionFeedbackStoreCorruptError() from exc
        _raw_hash(planning_snapshot_fingerprint, error=ExecutionFeedbackStoreInvalidRequestError)
        _raw_hash(accepted_item_fingerprint, error=ExecutionFeedbackStoreInvalidRequestError)
        if type(item_id) is not str or not item_id:
            raise ExecutionFeedbackStoreInvalidRequestError()
        return tuple(
            envelope
            for envelope in self.read_events()
            if _chain_key(envelope.record)
            == (snapshot_id, planning_snapshot_fingerprint, item_id, accepted_item_fingerprint)
        )

    def _find_operation_unlocked(
        self,
        verified: ExecutionFeedbackStoreVerifiedSnapshotV1,
        operation_fingerprint: str,
    ) -> ExecutionFeedbackStoreEnvelopeV1 | None:
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
        verified: ExecutionFeedbackStoreVerifiedSnapshotV1,
        event: ExecutionEventV1,
    ) -> ExecutionFeedbackStoreEnvelopeV1:
        operation = event.operation_id_fingerprint
        intent = execution_event_intent_fingerprint(event)
        existing = self._find_operation_unlocked(verified, operation)
        if existing is not None:
            if existing.intent_fingerprint == intent:
                return existing
            raise ExecutionFeedbackStoreIdempotencyConflictError()
        if verified.manifest.record_count >= EXECUTION_FEEDBACK_STORE_MAX_RECORDS:
            raise ExecutionFeedbackStoreCapacityError()
        chain_events = (
            *(
                envelope.record
                for envelope in verified.envelopes
                if _chain_key(envelope.record) == _chain_key(event)
            ),
            event,
        )
        try:
            replay_execution_events(chain_events)
        except ExecutionFeedbackError as exc:
            raise ExecutionFeedbackStoreCorruptError() from exc
        sequence = verified.manifest.next_sequence
        event_fingerprint = execution_event_fingerprint(event)
        unsigned = {
            "sequence": sequence,
            "record_type": ExecutionFeedbackStoreRecordTypeV1.EXECUTION_EVENT.value,
            "record": event.as_dict(),
            "intent_fingerprint": intent,
            "previous_record_digest": verified.manifest.last_record_digest,
            "event_fingerprint": event_fingerprint,
        }
        envelope = ExecutionFeedbackStoreEnvelopeV1(
            sequence=sequence,
            record_type=ExecutionFeedbackStoreRecordTypeV1.EXECUTION_EVENT,
            record=event,
            intent_fingerprint=intent,
            previous_record_digest=verified.manifest.last_record_digest,
            event_fingerprint=event_fingerprint,
            record_digest=execution_feedback_store_hash(unsigned),
        )
        line = _canonical_bytes(envelope.as_dict()) + b"\n"
        if len(line) > EXECUTION_FEEDBACK_STORE_MAX_RECORD_BYTES:
            raise ExecutionFeedbackStoreCapacityError()
        self._append_bytes_durable(self.records_path, line)
        self._write_manifest_atomic(
            ExecutionFeedbackStoreManifestV1(
                EXECUTION_FEEDBACK_STORE_FORMAT_VERSION,
                sequence + 1,
                sequence,
                envelope.record_digest,
                EXECUTION_POLICY_ID,
                EXECUTION_POLICY_FINGERPRINT,
            )
        )
        reread = self._read_verified_unlocked()
        if not reread.envelopes or reread.envelopes[-1] != envelope:
            raise ExecutionFeedbackStoreUnavailableError()
        return envelope

    def append_event(
        self,
        event: ExecutionEventV1,
        *,
        plan: PlanningPlanV1 | None = None,
        current_plan: PlanningPlanV1 | None = None,
        source_status: ExecutionSourceStatusV1 | str = ExecutionSourceStatusV1.CURRENT,
    ) -> ExecutionFeedbackStoreEnvelopeV1:
        """Append one event after exact binding and lifecycle validation."""

        if type(event) is not ExecutionEventV1:
            raise ExecutionFeedbackStoreInvalidRequestError()
        try:
            with _StoreLock(self.lock_path):
                verified = self._read_verified_unlocked()
                existing = self._find_operation_unlocked(
                    verified,
                    event.operation_id_fingerprint,
                )
                if existing is not None:
                    if existing.intent_fingerprint == execution_event_intent_fingerprint(event):
                        return existing
                    raise ExecutionFeedbackStoreIdempotencyConflictError()
                if plan is not None:
                    try:
                        if event.event_type is ExecutionEventTypeV1.START:
                            validate_execution_event_for_plan(
                                event,
                                plan,
                                current_plan=current_plan,
                                source_status=source_status,
                            )
                        else:
                            bind_execution_event_to_plan(event, plan)
                    except ExecutionFeedbackStaleError:
                        raise
                    except ExecutionFeedbackError as exc:
                        raise ExecutionFeedbackStoreInvalidRequestError() from exc
                elif event.event_type is ExecutionEventTypeV1.START:
                    raise ExecutionFeedbackStaleError()
                return self._append_event_unlocked(verified, event)
        except ExecutionFeedbackStoreError, ExecutionFeedbackStaleError:
            raise
        except (OSError, ValueError, UnicodeError, RecursionError) as exc:
            raise ExecutionFeedbackStoreUnavailableError() from exc

    append = append_event

    @staticmethod
    def _append_bytes_durable(path: Path, payload: bytes) -> None:
        try:
            descriptor = os.open(
                path,
                os.O_WRONLY | os.O_APPEND | os.O_CREAT | getattr(os, "O_BINARY", 0),
                0o600,
            )
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
            os.chmod(path, 0o600)
        except OSError as exc:
            raise ExecutionFeedbackStoreUnavailableError() from exc

    def _write_manifest_atomic(self, manifest: ExecutionFeedbackStoreManifestV1) -> None:
        self._write_bytes_atomic(self.manifest_path, _canonical_bytes(manifest.as_dict()))

    def _write_bytes_atomic(self, path: Path, payload: bytes) -> None:
        temporary = self.root / f".{path.name}.{os.getpid()}.tmp"
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
        except OSError as exc:
            raise ExecutionFeedbackStoreUnavailableError() from exc
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


class ExecutionFeedbackStoreInvalidRequestError(ExecutionFeedbackStoreError):
    """The caller supplied an invalid store query or mutation."""

    code = "invalid_store_request"


def derive_execution_feedback_store_root(
    env_file: Path | os.PathLike[str] | None,
) -> Path | None:
    """Derive the Stage 18 store beside the existing deployment env file."""

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
        prospective = parent / EXECUTION_FEEDBACK_STORE_PARENT_NAME
        root = prospective / EXECUTION_FEEDBACK_STORE_DIRECTORY_NAME
        if prospective.exists() and (prospective.is_symlink() or not prospective.is_dir()):
            return None
        if root.exists() and (root.is_symlink() or not root.is_dir()):
            return None
        return root
    except OSError, RuntimeError, ValueError:
        return None


ExecutionFeedbackStore = ExecutionFeedbackOperationalStore
ExecutionFeedbackStoreManifest = ExecutionFeedbackStoreManifestV1
ExecutionFeedbackStoreEnvelope = ExecutionFeedbackStoreEnvelopeV1
derive_execution_store_root = derive_execution_feedback_store_root


__all__ = [
    "EXECUTION_FEEDBACK_STORE_DIRECTORY_NAME",
    "EXECUTION_FEEDBACK_STORE_FORMAT_VERSION",
    "EXECUTION_FEEDBACK_STORE_LOCK_FILE_NAME",
    "EXECUTION_FEEDBACK_STORE_MANIFEST_FILE_NAME",
    "EXECUTION_FEEDBACK_STORE_MAX_EVENT_BYTES",
    "EXECUTION_FEEDBACK_STORE_MAX_RECORDS",
    "EXECUTION_FEEDBACK_STORE_MAX_RECORD_BYTES",
    "EXECUTION_FEEDBACK_STORE_PARENT_NAME",
    "EXECUTION_FEEDBACK_STORE_RECORD_FILE_NAME",
    "ExecutionFeedbackOperationalStore",
    "ExecutionFeedbackStore",
    "ExecutionFeedbackStoreCapacityError",
    "ExecutionFeedbackStoreCorruptError",
    "ExecutionFeedbackStoreEnvelope",
    "ExecutionFeedbackStoreEnvelopeV1",
    "ExecutionFeedbackStoreError",
    "ExecutionFeedbackStoreIdempotencyConflictError",
    "ExecutionFeedbackStoreInvalidRequestError",
    "ExecutionFeedbackStoreManifest",
    "ExecutionFeedbackStoreManifestV1",
    "ExecutionFeedbackStoreRecordType",
    "ExecutionFeedbackStoreRecordTypeV1",
    "ExecutionFeedbackStoreUnavailableError",
    "ExecutionFeedbackStoreVerifiedSnapshotV1",
    "derive_execution_feedback_store_root",
    "derive_execution_store_root",
    "execution_event_intent_fingerprint",
    "execution_feedback_store_hash",
]
