"""Append-only operational store for accepted Executive Strategy snapshots.

The store deliberately persists only bounded reviewed action projections and
their exact provenance fingerprints.  It never writes the vault, creates
tasks, or retains the context pack/provider payload.  Every mutation is an
explicit owner operation and is committed as one canonical JSONL record under
the same advisory lock used by the Stage 15 operational store.
"""

from __future__ import annotations

import hashlib
import json
import os
import stat
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any, Final, cast
from uuid import UUID, uuid7

from second_brain.application.adaptive_cognitive_twin_store import _StoreLock
from second_brain.application.executive_strategy import (
    POLICY_FINGERPRINT,
    POLICY_ID,
    ExecutiveContextPackV1,
    ExecutiveHashV1,
    ExecutivePackReadinessV1,
    ExecutiveResultStateV1,
    ReviewedActionV1,
    StrategyProposalV1,
    StrategySnapshotStateV1,
    StrategySnapshotV1,
    build_strategy_snapshot,
    validate_executive_context_pack,
    validate_strategy_proposal,
    validate_strategy_snapshot,
)
from second_brain.domain.models import parse_rfc3339, parse_uuid7

EXECUTIVE_STRATEGY_STORE_PARENT_NAME: Final[str] = "prospective-audit"
EXECUTIVE_STRATEGY_STORE_DIRECTORY_NAME: Final[str] = "executive-strategy"
EXECUTIVE_STRATEGY_STORE_RECORD_FILE_NAME: Final[str] = "snapshots.jsonl"
EXECUTIVE_STRATEGY_STORE_MANIFEST_FILE_NAME: Final[str] = "manifest.json"
EXECUTIVE_STRATEGY_STORE_LOCK_FILE_NAME: Final[str] = ".store.lock"
EXECUTIVE_STRATEGY_STORE_FORMAT_VERSION: Final[int] = 1
EXECUTIVE_STRATEGY_STORE_MAX_RECORDS: Final[int] = 4096
EXECUTIVE_STRATEGY_STORE_MAX_RECORD_BYTES: Final[int] = 256 * 1024
EXECUTIVE_STRATEGY_STORE_MAX_OPERATION_ID_BYTES: Final[int] = 256
EXECUTIVE_STRATEGY_STORE_MAX_REASON_BYTES: Final[int] = 512
_RAW_HASH_LENGTH: Final[int] = 64


class ExecutiveStrategyStoreError(RuntimeError):
    """Base class for safe operational-store errors."""


class ExecutiveStrategyStoreInvalidRequestError(ExecutiveStrategyStoreError):
    """The caller supplied an invalid or stale store request."""


class ExecutiveStrategyStoreUnavailableError(ExecutiveStrategyStoreError):
    """The store cannot be safely initialized or written."""


class ExecutiveStrategyStoreCorruptError(ExecutiveStrategyStoreError):
    """The append-only ledger or manifest failed integrity checks."""


class ExecutiveStrategyStoreIdempotencyConflictError(ExecutiveStrategyStoreError):
    """An operation id was reused with a different canonical intent."""


class ExecutiveStrategyStoreStateConflictError(ExecutiveStrategyStoreError):
    """The expected current snapshot no longer matches under the lock."""


class ExecutiveStrategyStoreSourceChangedError(ExecutiveStrategyStoreError):
    """The proposal or context pack is not bound to the same exact source."""


class ExecutiveStrategyStoreRecordTypeV1(StrEnum):
    """Closed append-only lifecycle record vocabulary."""

    ACCEPTED_SNAPSHOT = "accepted_snapshot"
    PROPOSAL_REJECTED = "proposal_rejected"


ExecutiveStrategyStoreRecordType = ExecutiveStrategyStoreRecordTypeV1


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
        raise ExecutiveStrategyStoreCorruptError() from exc


def executive_strategy_store_hash(value: object) -> str:
    """Return the raw SHA-256 used by store envelopes and the manifest."""

    return hashlib.sha256(_canonical_bytes(value)).hexdigest()


def _raw_hash(value: object) -> str:
    if type(value) is not str or len(value) != _RAW_HASH_LENGTH:
        raise ExecutiveStrategyStoreCorruptError()
    if any(char not in "0123456789abcdef" for char in value):
        raise ExecutiveStrategyStoreCorruptError()
    return value


def _growth_hash(value: object) -> str:
    if (
        type(value) is not str
        or len(value) != 71
        or not value.startswith("sha256:")
        or any(char not in "0123456789abcdef" for char in value[7:])
    ):
        raise ExecutiveStrategyStoreCorruptError()
    return value


def _uuid7(value: object) -> UUID:
    try:
        return parse_uuid7(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ExecutiveStrategyStoreCorruptError() from exc


def _timestamp(value: object) -> datetime:
    try:
        parsed = parse_rfc3339(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ExecutiveStrategyStoreCorruptError() from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ExecutiveStrategyStoreCorruptError()
    return parsed.astimezone(UTC)


def _format_timestamp(value: datetime) -> str:
    rendered = value.astimezone(UTC).isoformat(
        timespec="microseconds" if value.microsecond else "seconds"
    )
    return rendered.removesuffix("+00:00") + "Z"


def _operation_fingerprint(value: object) -> str:
    if isinstance(value, UUID):
        value = str(value)
    if type(value) is not str or not value:
        raise ExecutiveStrategyStoreInvalidRequestError()
    try:
        encoded = value.encode("utf-8")
    except UnicodeError as exc:
        raise ExecutiveStrategyStoreInvalidRequestError() from exc
    if len(encoded) > EXECUTIVE_STRATEGY_STORE_MAX_OPERATION_ID_BYTES:
        raise ExecutiveStrategyStoreInvalidRequestError()
    if any(ord(char) < 0x20 or ord(char) == 0x7F for char in value):
        raise ExecutiveStrategyStoreInvalidRequestError()
    return hashlib.sha256(encoded).hexdigest()


def _reason(value: object) -> str:
    if type(value) is not str:
        raise ExecutiveStrategyStoreInvalidRequestError()
    normalized = value.strip()
    if (
        not normalized
        or len(normalized.encode("utf-8")) > EXECUTIVE_STRATEGY_STORE_MAX_REASON_BYTES
    ):
        raise ExecutiveStrategyStoreInvalidRequestError()
    if any(ord(char) < 0x20 or 0x7F <= ord(char) <= 0x9F for char in normalized):
        raise ExecutiveStrategyStoreInvalidRequestError()
    return normalized


def _enum_value[EnumT: StrEnum](enum_type: type[EnumT], value: object) -> EnumT:
    if isinstance(value, enum_type):
        return value
    if type(value) is not str:
        raise ExecutiveStrategyStoreCorruptError()
    try:
        return enum_type(value)
    except ValueError as exc:
        raise ExecutiveStrategyStoreCorruptError() from exc


def _wire_dict(value: object, expected: set[str]) -> dict[str, object]:
    if type(value) is not dict or set(value) != expected:
        raise ExecutiveStrategyStoreCorruptError()
    return cast(dict[str, object], value)


@dataclass(frozen=True, slots=True)
class ExecutiveStrategyStoreManifestV1:
    """Atomic manifest cross-checking the complete JSONL generation."""

    format_version: int
    next_sequence: int
    record_count: int
    last_record_digest: str | None
    policy_id: str
    policy_fingerprint: ExecutiveHashV1

    def __post_init__(self) -> None:
        if (
            type(self.format_version) is not int
            or isinstance(self.format_version, bool)
            or self.format_version != EXECUTIVE_STRATEGY_STORE_FORMAT_VERSION
        ):
            raise ExecutiveStrategyStoreCorruptError()
        if (
            type(self.next_sequence) is not int
            or isinstance(self.next_sequence, bool)
            or not 1 <= self.next_sequence <= (1 << 64) - 1
        ):
            raise ExecutiveStrategyStoreCorruptError()
        if (
            type(self.record_count) is not int
            or isinstance(self.record_count, bool)
            or not 0 <= self.record_count <= EXECUTIVE_STRATEGY_STORE_MAX_RECORDS
            or self.next_sequence != self.record_count + 1
        ):
            raise ExecutiveStrategyStoreCorruptError()
        digest = None if self.last_record_digest is None else _raw_hash(self.last_record_digest)
        if (self.record_count == 0) != (digest is None):
            raise ExecutiveStrategyStoreCorruptError()
        if self.policy_id != POLICY_ID or self.policy_fingerprint != POLICY_FINGERPRINT:
            raise ExecutiveStrategyStoreCorruptError()
        object.__setattr__(self, "last_record_digest", digest)
        object.__setattr__(self, "policy_id", POLICY_ID)
        object.__setattr__(self, "policy_fingerprint", POLICY_FINGERPRINT)

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
class ExecutiveStrategyStoreEventV1:
    """One bounded operation with no raw context/proposal/provider payload."""

    event_id: UUID | str
    event_type: ExecutiveStrategyStoreRecordTypeV1 | str
    operation_id_fingerprint: str
    intent_fingerprint: str
    goal_source_uuid: UUID | str
    goal_identity_fingerprint: ExecutiveHashV1
    source_pack_fingerprint: ExecutiveHashV1
    proposal_fingerprint: ExecutiveHashV1
    snapshot: StrategySnapshotV1 | None
    reason: str | None

    def __post_init__(self) -> None:
        event_id = _uuid7(self.event_id)
        event_type = _enum_value(ExecutiveStrategyStoreRecordTypeV1, self.event_type)
        operation = _raw_hash(self.operation_id_fingerprint)
        intent = _raw_hash(self.intent_fingerprint)
        goal_uuid = _uuid7(self.goal_source_uuid)
        goal_fp = _growth_hash(self.goal_identity_fingerprint)
        source_fp = _raw_hash(self.source_pack_fingerprint)
        proposal_fp = _raw_hash(self.proposal_fingerprint)
        expected_snapshot_type = StrategySnapshotV1 if self.snapshot is not None else type(None)
        if type(self.snapshot) is not expected_snapshot_type:
            raise ExecutiveStrategyStoreCorruptError()
        if event_type is ExecutiveStrategyStoreRecordTypeV1.ACCEPTED_SNAPSHOT:
            if self.snapshot is None or self.reason is not None:
                raise ExecutiveStrategyStoreCorruptError()
            validate_strategy_snapshot(self.snapshot)
            if (
                self.snapshot.state is not StrategySnapshotStateV1.CURRENT
                or self.snapshot.goal_source_uuid != goal_uuid
                or self.snapshot.goal_identity_fingerprint != goal_fp
                or self.snapshot.source_pack_fingerprint != source_fp
                or self.snapshot.proposal_fingerprint != proposal_fp
            ):
                raise ExecutiveStrategyStoreCorruptError()
        else:
            if self.snapshot is not None or self.reason is None:
                raise ExecutiveStrategyStoreCorruptError()
            if self.reason != _reason(self.reason):
                raise ExecutiveStrategyStoreCorruptError()
        object.__setattr__(self, "event_id", event_id)
        object.__setattr__(self, "event_type", event_type)
        object.__setattr__(self, "operation_id_fingerprint", operation)
        object.__setattr__(self, "intent_fingerprint", intent)
        object.__setattr__(self, "goal_source_uuid", goal_uuid)
        object.__setattr__(self, "source_pack_fingerprint", source_fp)
        object.__setattr__(self, "proposal_fingerprint", proposal_fp)

    def as_dict(self) -> dict[str, object]:
        return {
            "event_id": str(self.event_id),
            "event_type": cast(ExecutiveStrategyStoreRecordTypeV1, self.event_type).value,
            "operation_id_fingerprint": self.operation_id_fingerprint,
            "intent_fingerprint": self.intent_fingerprint,
            "goal_source_uuid": str(self.goal_source_uuid),
            "goal_identity_fingerprint": self.goal_identity_fingerprint,
            "source_pack_fingerprint": self.source_pack_fingerprint,
            "proposal_fingerprint": self.proposal_fingerprint,
            "snapshot": None if self.snapshot is None else self.snapshot.as_dict(),
            "reason": self.reason,
        }


@dataclass(frozen=True, slots=True)
class ExecutiveStrategyStoreEnvelopeV1:
    """Hash-chained JSONL envelope."""

    sequence: int
    record_type: ExecutiveStrategyStoreRecordTypeV1 | str
    record: ExecutiveStrategyStoreEventV1
    previous_record_digest: str | None
    event_fingerprint: str
    record_digest: str

    def __post_init__(self) -> None:
        if (
            type(self.sequence) is not int
            or isinstance(self.sequence, bool)
            or not 1 <= self.sequence <= (1 << 64) - 1
        ):
            raise ExecutiveStrategyStoreCorruptError()
        if type(self.record) is not ExecutiveStrategyStoreEventV1:
            raise ExecutiveStrategyStoreCorruptError()
        record_type = _enum_value(ExecutiveStrategyStoreRecordTypeV1, self.record_type)
        if record_type is not self.record.event_type:
            raise ExecutiveStrategyStoreCorruptError()
        previous = (
            None if self.previous_record_digest is None else _raw_hash(self.previous_record_digest)
        )
        event_fingerprint = _raw_hash(self.event_fingerprint)
        record_digest = _raw_hash(self.record_digest)
        object.__setattr__(self, "record_type", record_type)
        object.__setattr__(self, "previous_record_digest", previous)
        object.__setattr__(self, "event_fingerprint", event_fingerprint)
        object.__setattr__(self, "record_digest", record_digest)

    def unsigned_dict(self) -> dict[str, object]:
        return {
            "sequence": self.sequence,
            "record_type": cast(ExecutiveStrategyStoreRecordTypeV1, self.record_type).value,
            "record": self.record.as_dict(),
            "previous_record_digest": self.previous_record_digest,
            "event_fingerprint": self.event_fingerprint,
        }

    @property
    def expected_event_fingerprint(self) -> str:
        return executive_strategy_store_hash(self.record.as_dict())

    @property
    def expected_record_digest(self) -> str:
        return executive_strategy_store_hash(self.unsigned_dict())

    def as_dict(self) -> dict[str, object]:
        return {**self.unsigned_dict(), "record_digest": self.record_digest}


@dataclass(frozen=True, slots=True)
class ExecutiveStrategyStoreVerifiedSnapshotV1:
    manifest: ExecutiveStrategyStoreManifestV1
    envelopes: tuple[ExecutiveStrategyStoreEnvelopeV1, ...]


@dataclass(frozen=True, slots=True)
class ExecutiveStrategyStoreStateV1:
    """Replayed owner-visible state; accepted versions remain append-only."""

    accepted_snapshots: tuple[StrategySnapshotV1, ...]
    current_snapshots: tuple[StrategySnapshotV1, ...]
    rejected_proposal_fingerprints: tuple[ExecutiveHashV1, ...]

    def current_snapshot(
        self,
        goal_source_uuid: UUID | str,
        goal_identity_fingerprint: ExecutiveHashV1,
    ) -> StrategySnapshotV1 | None:
        try:
            goal_uuid = parse_uuid7(goal_source_uuid)
        except (TypeError, ValueError, OverflowError) as exc:
            raise ExecutiveStrategyStoreInvalidRequestError() from exc
        try:
            goal_fp = _growth_hash(goal_identity_fingerprint)
        except ExecutiveStrategyStoreCorruptError as exc:
            raise ExecutiveStrategyStoreInvalidRequestError() from exc
        return next(
            (
                snapshot
                for snapshot in self.current_snapshots
                if snapshot.goal_source_uuid == goal_uuid
                and snapshot.goal_identity_fingerprint == goal_fp
            ),
            None,
        )


def _goal_key(snapshot: StrategySnapshotV1) -> tuple[str, str]:
    return str(snapshot.goal_source_uuid), snapshot.goal_identity_fingerprint


def _event_intent_payload(event: ExecutiveStrategyStoreEventV1) -> dict[str, object]:
    data = event.as_dict()
    data.pop("event_id")
    return data


def _replay_state(
    envelopes: Sequence[ExecutiveStrategyStoreEnvelopeV1],
) -> ExecutiveStrategyStoreStateV1:
    accepted: list[StrategySnapshotV1] = []
    current: dict[tuple[str, str], StrategySnapshotV1] = {}
    by_id: dict[UUID, StrategySnapshotV1] = {}
    rejected: list[str] = []
    for envelope in envelopes:
        event = envelope.record
        if event.event_type is ExecutiveStrategyStoreRecordTypeV1.PROPOSAL_REJECTED:
            rejected.append(event.proposal_fingerprint)
            continue
        snapshot = event.snapshot
        if snapshot is None:
            raise ExecutiveStrategyStoreCorruptError()
        snapshot_id = cast(UUID, snapshot.snapshot_id)
        if snapshot_id in by_id:
            raise ExecutiveStrategyStoreCorruptError()
        key = _goal_key(snapshot)
        previous = current.get(key)
        if snapshot.prior_snapshot_id is None:
            if previous is not None or snapshot.sequence != 1:
                raise ExecutiveStrategyStoreCorruptError()
        else:
            prior_id = cast(UUID, snapshot.prior_snapshot_id)
            prior = by_id.get(prior_id)
            if (
                prior is None
                or previous is None
                or previous.snapshot_id != prior_id
                or prior.snapshot_fingerprint != snapshot.prior_snapshot_fingerprint
                or prior.goal_source_uuid != snapshot.goal_source_uuid
                or prior.goal_identity_fingerprint != snapshot.goal_identity_fingerprint
                or snapshot.sequence != prior.sequence + 1
            ):
                raise ExecutiveStrategyStoreCorruptError()
        accepted.append(snapshot)
        by_id[snapshot_id] = snapshot
        current[key] = snapshot
    return ExecutiveStrategyStoreStateV1(
        accepted_snapshots=tuple(accepted),
        current_snapshots=tuple(current.values()),
        rejected_proposal_fingerprints=tuple(rejected),
    )


class ExecutiveStrategySnapshotStore:
    """Strict JSONL store for explicitly accepted Stage 16 snapshots."""

    def __init__(
        self,
        root: Path | os.PathLike[str],
        *,
        vault_root: Path | os.PathLike[str] | None = None,
        clock: Callable[[], datetime] | None = None,
        expected_owner_group: tuple[str, str] | None = None,
    ) -> None:
        if not isinstance(root, os.PathLike):
            raise ExecutiveStrategyStoreUnavailableError()
        if expected_owner_group is not None and (
            type(expected_owner_group) is not tuple
            or len(expected_owner_group) != 2
            or any(type(item) is not str or not item for item in expected_owner_group)
        ):
            raise ExecutiveStrategyStoreUnavailableError()
        self.root = Path(root).expanduser()
        self._clock = clock or (lambda: datetime.now(UTC))
        self._expected_owner_group = expected_owner_group
        self._validate_root(vault_root)
        try:
            if not self.root.is_absolute():
                raise ValueError("root must be absolute")
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
        except ExecutiveStrategyStoreError:
            raise
        except (OSError, ValueError, UnicodeError, RecursionError) as exc:
            raise ExecutiveStrategyStoreUnavailableError() from exc

    @property
    def records_path(self) -> Path:
        return self.root / EXECUTIVE_STRATEGY_STORE_RECORD_FILE_NAME

    @property
    def snapshots_path(self) -> Path:
        return self.records_path

    @property
    def manifest_path(self) -> Path:
        return self.root / EXECUTIVE_STRATEGY_STORE_MANIFEST_FILE_NAME

    @property
    def lock_path(self) -> Path:
        return self.root / EXECUTIVE_STRATEGY_STORE_LOCK_FILE_NAME

    @property
    def manifest(self) -> ExecutiveStrategyStoreManifestV1:
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
            raise ExecutiveStrategyStoreUnavailableError() from exc

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
                ExecutiveStrategyStoreManifestV1(
                    EXECUTIVE_STRATEGY_STORE_FORMAT_VERSION,
                    1,
                    0,
                    None,
                    POLICY_ID,
                    POLICY_FINGERPRINT,
                )
            )
            return
        if not all(present):
            raise ExecutiveStrategyStoreCorruptError()
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
            raise ExecutiveStrategyStoreUnavailableError() from exc

    def _manifest_from_dict(self, value: object) -> ExecutiveStrategyStoreManifestV1:
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
        return ExecutiveStrategyStoreManifestV1(
            format_version=cast(int, data["format_version"]),
            next_sequence=cast(int, data["next_sequence"]),
            record_count=cast(int, data["record_count"]),
            last_record_digest=cast(str | None, data["last_record_digest"]),
            policy_id=cast(str, data["policy_id"]),
            policy_fingerprint=cast(str, data["policy_fingerprint"]),
        )

    def _read_manifest_unlocked(self) -> ExecutiveStrategyStoreManifestV1:
        try:
            raw = self.manifest_path.read_bytes()
            data = json.loads(raw.decode("utf-8"))
            if _canonical_bytes(data) != raw:
                raise ValueError("manifest not canonical")
            return self._manifest_from_dict(data)
        except ExecutiveStrategyStoreError:
            raise
        except (OSError, UnicodeError, ValueError, TypeError, json.JSONDecodeError) as exc:
            raise ExecutiveStrategyStoreCorruptError() from exc

    def _event_from_dict(self, value: object) -> ExecutiveStrategyStoreEventV1:
        data = dict(
            _wire_dict(
                value,
                {
                    "event_id",
                    "event_type",
                    "operation_id_fingerprint",
                    "intent_fingerprint",
                    "goal_source_uuid",
                    "goal_identity_fingerprint",
                    "source_pack_fingerprint",
                    "proposal_fingerprint",
                    "snapshot",
                    "reason",
                },
            )
        )
        snapshot_value = data["snapshot"]
        snapshot = None if snapshot_value is None else StrategySnapshotV1.from_dict(snapshot_value)
        data["snapshot"] = snapshot
        return ExecutiveStrategyStoreEventV1(
            event_id=cast(UUID | str, data["event_id"]),
            event_type=cast(ExecutiveStrategyStoreRecordTypeV1 | str, data["event_type"]),
            operation_id_fingerprint=cast(str, data["operation_id_fingerprint"]),
            intent_fingerprint=cast(str, data["intent_fingerprint"]),
            goal_source_uuid=cast(UUID | str, data["goal_source_uuid"]),
            goal_identity_fingerprint=cast(str, data["goal_identity_fingerprint"]),
            source_pack_fingerprint=cast(str, data["source_pack_fingerprint"]),
            proposal_fingerprint=cast(str, data["proposal_fingerprint"]),
            snapshot=cast(StrategySnapshotV1 | None, data["snapshot"]),
            reason=cast(str | None, data["reason"]),
        )

    def _envelope_from_dict(self, value: object) -> ExecutiveStrategyStoreEnvelopeV1:
        data = dict(
            _wire_dict(
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
        )
        data["record"] = self._event_from_dict(data["record"])
        return ExecutiveStrategyStoreEnvelopeV1(
            sequence=cast(int, data["sequence"]),
            record_type=cast(ExecutiveStrategyStoreRecordTypeV1 | str, data["record_type"]),
            record=cast(ExecutiveStrategyStoreEventV1, data["record"]),
            previous_record_digest=cast(str | None, data["previous_record_digest"]),
            event_fingerprint=cast(str, data["event_fingerprint"]),
            record_digest=cast(str, data["record_digest"]),
        )

    def _read_verified_unlocked(self) -> ExecutiveStrategyStoreVerifiedSnapshotV1:
        if self.root.is_symlink() or any(
            path.is_symlink() for path in (self.records_path, self.manifest_path)
        ):
            raise ExecutiveStrategyStoreCorruptError()
        self._assert_owner_only_unlocked()
        manifest = self._read_manifest_unlocked()
        try:
            raw = self.records_path.read_bytes()
        except FileNotFoundError as exc:
            raise ExecutiveStrategyStoreCorruptError() from exc
        except OSError as exc:
            raise ExecutiveStrategyStoreUnavailableError() from exc
        if not raw:
            if manifest.record_count != 0 or manifest.last_record_digest is not None:
                raise ExecutiveStrategyStoreCorruptError()
            return ExecutiveStrategyStoreVerifiedSnapshotV1(manifest, ())
        if raw.startswith(b"\xef\xbb\xbf") or not raw.endswith(b"\n"):
            raise ExecutiveStrategyStoreCorruptError()
        envelopes: list[ExecutiveStrategyStoreEnvelopeV1] = []
        previous: str | None = None
        seen_event_ids: set[UUID] = set()
        seen_operations: set[str] = set()
        for line in raw.splitlines(keepends=True):
            if not line.endswith(b"\n") or line.endswith(b"\r\n"):
                raise ExecutiveStrategyStoreCorruptError()
            payload = line[:-1]
            if not payload or len(payload) > EXECUTIVE_STRATEGY_STORE_MAX_RECORD_BYTES:
                raise ExecutiveStrategyStoreCorruptError()
            try:
                data = json.loads(payload.decode("utf-8"))
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
            except ExecutiveStrategyStoreError:
                raise
            except (
                TypeError,
                ValueError,
                UnicodeError,
                OverflowError,
                json.JSONDecodeError,
            ) as exc:
                raise ExecutiveStrategyStoreCorruptError() from exc
            envelopes.append(envelope)
            seen_event_ids.add(event_id)
            seen_operations.add(operation)
            previous = envelope.record_digest
        if (
            manifest.record_count != len(envelopes)
            or manifest.next_sequence != len(envelopes) + 1
            or manifest.last_record_digest != previous
        ):
            raise ExecutiveStrategyStoreCorruptError()
        if len(envelopes) > EXECUTIVE_STRATEGY_STORE_MAX_RECORDS:
            raise ExecutiveStrategyStoreCorruptError()
        _replay_state(tuple(envelopes))
        return ExecutiveStrategyStoreVerifiedSnapshotV1(manifest, tuple(envelopes))

    def read_verified_snapshot(self) -> ExecutiveStrategyStoreVerifiedSnapshotV1:
        try:
            with _StoreLock(self.lock_path):
                return self._read_verified_unlocked()
        except ExecutiveStrategyStoreError:
            raise
        except (OSError, ValueError, UnicodeError, RecursionError) as exc:
            raise ExecutiveStrategyStoreUnavailableError() from exc

    verified_snapshot = read_verified_snapshot

    def read_events(self) -> tuple[ExecutiveStrategyStoreEnvelopeV1, ...]:
        return self.read_verified_snapshot().envelopes

    read_records = read_events

    def read_manifest(self) -> ExecutiveStrategyStoreManifestV1:
        return self.read_verified_snapshot().manifest

    def read_state(self) -> ExecutiveStrategyStoreStateV1:
        snapshot = self.read_verified_snapshot()
        return _replay_state(snapshot.envelopes)

    def read_snapshots(self) -> tuple[StrategySnapshotV1, ...]:
        return self.read_state().accepted_snapshots

    def current_snapshot(
        self,
        goal_source_uuid: UUID | str,
        goal_identity_fingerprint: ExecutiveHashV1,
    ) -> StrategySnapshotV1 | None:
        return self.read_state().current_snapshot(goal_source_uuid, goal_identity_fingerprint)

    read_current = current_snapshot

    def _append_event_unlocked(
        self,
        verified: ExecutiveStrategyStoreVerifiedSnapshotV1,
        event: ExecutiveStrategyStoreEventV1,
    ) -> ExecutiveStrategyStoreEnvelopeV1:
        for existing in verified.envelopes:
            if existing.record.operation_id_fingerprint != event.operation_id_fingerprint:
                continue
            if _event_intent_payload(existing.record) == _event_intent_payload(event):
                return existing
            raise ExecutiveStrategyStoreIdempotencyConflictError()
        if verified.manifest.record_count >= EXECUTIVE_STRATEGY_STORE_MAX_RECORDS:
            raise ExecutiveStrategyStoreUnavailableError()
        sequence = verified.manifest.next_sequence
        event_fingerprint = executive_strategy_store_hash(event.as_dict())
        unsigned = {
            "sequence": sequence,
            "record_type": cast(ExecutiveStrategyStoreRecordTypeV1, event.event_type).value,
            "record": event.as_dict(),
            "previous_record_digest": verified.manifest.last_record_digest,
            "event_fingerprint": event_fingerprint,
        }
        envelope = ExecutiveStrategyStoreEnvelopeV1(
            sequence=sequence,
            record_type=event.event_type,
            record=event,
            previous_record_digest=verified.manifest.last_record_digest,
            event_fingerprint=event_fingerprint,
            record_digest=executive_strategy_store_hash(unsigned),
        )
        line = _canonical_bytes(envelope.as_dict()) + b"\n"
        if len(line) > EXECUTIVE_STRATEGY_STORE_MAX_RECORD_BYTES:
            raise ExecutiveStrategyStoreUnavailableError()
        self._append_bytes_durable(self.records_path, line)
        self._write_manifest_atomic(
            ExecutiveStrategyStoreManifestV1(
                EXECUTIVE_STRATEGY_STORE_FORMAT_VERSION,
                sequence + 1,
                sequence,
                envelope.record_digest,
                POLICY_ID,
                POLICY_FINGERPRINT,
            )
        )
        reread = self._read_verified_unlocked()
        if not reread.envelopes or reread.envelopes[-1] != envelope:
            raise ExecutiveStrategyStoreUnavailableError()
        return envelope

    def _append_event(
        self, event: ExecutiveStrategyStoreEventV1
    ) -> ExecutiveStrategyStoreEnvelopeV1:
        try:
            with _StoreLock(self.lock_path):
                return self._append_event_unlocked(self._read_verified_unlocked(), event)
        except ExecutiveStrategyStoreError:
            raise
        except (OSError, ValueError, UnicodeError, RecursionError) as exc:
            raise ExecutiveStrategyStoreUnavailableError() from exc

    def accept(
        self,
        proposal: StrategyProposalV1,
        selected_actions: tuple[ReviewedActionV1, ...],
        *,
        context_pack: ExecutiveContextPackV1 | None = None,
        current_pack: ExecutiveContextPackV1 | None = None,
        operation_id: str | UUID,
        reviewed_at: datetime,
        accepted_at: datetime | None = None,
        expected_prior_snapshot_id: UUID | str | None = None,
        expected_prior_snapshot_fingerprint: ExecutiveHashV1 | None = None,
        snapshot_id: UUID | str | None = None,
    ) -> StrategySnapshotV1:
        """Revalidate and append one explicit accepted snapshot.

        ``expected_prior_snapshot_*`` is required for supersession.  This
        turns concurrent fresh acceptances into a visible state conflict
        instead of silently deciding which owner action wins.
        """

        if context_pack is not None and current_pack is not None:
            raise ExecutiveStrategyStoreInvalidRequestError()
        pack = context_pack if context_pack is not None else current_pack
        if pack is None:
            raise ExecutiveStrategyStoreInvalidRequestError()
        try:
            validated_pack = validate_executive_context_pack(pack)
            validated_proposal = validate_strategy_proposal(proposal)
        except (TypeError, ValueError) as exc:
            raise ExecutiveStrategyStoreInvalidRequestError() from exc
        if (
            cast(ExecutivePackReadinessV1, validated_pack.readiness)
            is not ExecutivePackReadinessV1.EXACT_CURRENT
            or validated_proposal.result_state is not ExecutiveResultStateV1.PROPOSAL
            or validated_pack.goal_source_uuid != validated_proposal.goal_source_uuid
            or validated_pack.goal_identity_fingerprint
            != validated_proposal.goal_identity_fingerprint
            or validated_pack.source_pack_fingerprint != validated_proposal.source_pack_fingerprint
        ):
            raise ExecutiveStrategyStoreSourceChangedError()
        if type(selected_actions) is not tuple:
            raise ExecutiveStrategyStoreInvalidRequestError()
        reviewed_time = self._normalize_request_timestamp(reviewed_at)
        accepted_time = (
            reviewed_time if accepted_at is None else self._normalize_request_timestamp(accepted_at)
        )
        if reviewed_time > accepted_time:
            raise ExecutiveStrategyStoreInvalidRequestError()
        operation_fp = _operation_fingerprint(operation_id)
        expected_prior_id = (
            None
            if expected_prior_snapshot_id is None
            else _uuid7_request(expected_prior_snapshot_id)
        )
        if (expected_prior_id is None) != (expected_prior_snapshot_fingerprint is None):
            raise ExecutiveStrategyStoreInvalidRequestError()
        expected_prior_fp = (
            None
            if expected_prior_snapshot_fingerprint is None
            else _raw_hash_request(expected_prior_snapshot_fingerprint)
        )
        intent_payload = {
            "operation": operation_fp,
            "kind": "accept",
            "goal_source_uuid": str(validated_pack.goal_source_uuid),
            "goal_identity_fingerprint": validated_pack.goal_identity_fingerprint,
            "source_pack_fingerprint": validated_pack.source_pack_fingerprint,
            "proposal_fingerprint": validated_proposal.proposal_fingerprint,
            "selected_actions": [action.as_dict() for action in selected_actions],
            "reviewed_at": _format_timestamp(reviewed_time),
            "accepted_at": _format_timestamp(accepted_time),
            "expected_prior_snapshot_id": (
                str(expected_prior_id) if expected_prior_id is not None else None
            ),
            "expected_prior_snapshot_fingerprint": expected_prior_fp,
            "snapshot_id": None if snapshot_id is None else str(_uuid7_request(snapshot_id)),
        }
        intent_fp = executive_strategy_store_hash(intent_payload)
        try:
            with _StoreLock(self.lock_path):
                verified = self._read_verified_unlocked()
                existing = self._find_operation_unlocked(verified, operation_fp)
                if existing is not None:
                    if existing.record.intent_fingerprint != intent_fp:
                        raise ExecutiveStrategyStoreIdempotencyConflictError()
                    if existing.record.snapshot is None:
                        raise ExecutiveStrategyStoreCorruptError()
                    return existing.record.snapshot
                state = _replay_state(verified.envelopes)
                current = state.current_snapshot(
                    validated_pack.goal_source_uuid,
                    validated_pack.goal_identity_fingerprint,
                )
                if current is None:
                    if expected_prior_id is not None:
                        raise ExecutiveStrategyStoreStateConflictError()
                elif (
                    expected_prior_id is None
                    or current.snapshot_id != expected_prior_id
                    or current.snapshot_fingerprint != expected_prior_fp
                ):
                    raise ExecutiveStrategyStoreStateConflictError()
                try:
                    snapshot = build_strategy_snapshot(
                        validated_proposal,
                        selected_actions,
                        sequence=1 if current is None else current.sequence + 1,
                        reviewed_at=reviewed_time,
                        accepted_at=accepted_time,
                        prior_snapshot=current,
                        snapshot_id=(None if snapshot_id is None else _uuid7_request(snapshot_id)),
                    )
                except (TypeError, ValueError) as exc:
                    raise ExecutiveStrategyStoreInvalidRequestError() from exc
                event = ExecutiveStrategyStoreEventV1(
                    event_id=uuid7(),
                    event_type=ExecutiveStrategyStoreRecordTypeV1.ACCEPTED_SNAPSHOT,
                    operation_id_fingerprint=operation_fp,
                    intent_fingerprint=intent_fp,
                    goal_source_uuid=snapshot.goal_source_uuid,
                    goal_identity_fingerprint=snapshot.goal_identity_fingerprint,
                    source_pack_fingerprint=snapshot.source_pack_fingerprint,
                    proposal_fingerprint=snapshot.proposal_fingerprint,
                    snapshot=snapshot,
                    reason=None,
                )
                return self._append_event_unlocked(verified, event).record.snapshot or snapshot
        except ExecutiveStrategyStoreError:
            raise
        except (OSError, ValueError, UnicodeError, RecursionError) as exc:
            raise ExecutiveStrategyStoreUnavailableError() from exc

    accept_snapshot = accept

    def reject(
        self,
        proposal: StrategyProposalV1,
        *,
        operation_id: str | UUID,
        reason: str = "owner_rejected",
    ) -> ExecutiveStrategyStoreEnvelopeV1:
        """Append an explicit rejection without retaining provider payload."""

        try:
            validated = validate_strategy_proposal(proposal)
        except (TypeError, ValueError) as exc:
            raise ExecutiveStrategyStoreInvalidRequestError() from exc
        operation_fp = _operation_fingerprint(operation_id)
        normalized_reason = _reason(reason)
        intent_fp = executive_strategy_store_hash(
            {
                "operation": operation_fp,
                "kind": "reject",
                "goal_source_uuid": str(validated.goal_source_uuid),
                "goal_identity_fingerprint": validated.goal_identity_fingerprint,
                "source_pack_fingerprint": validated.source_pack_fingerprint,
                "proposal_fingerprint": validated.proposal_fingerprint,
                "reason": normalized_reason,
            }
        )
        event = ExecutiveStrategyStoreEventV1(
            event_id=uuid7(),
            event_type=ExecutiveStrategyStoreRecordTypeV1.PROPOSAL_REJECTED,
            operation_id_fingerprint=operation_fp,
            intent_fingerprint=intent_fp,
            goal_source_uuid=validated.goal_source_uuid,
            goal_identity_fingerprint=validated.goal_identity_fingerprint,
            source_pack_fingerprint=validated.source_pack_fingerprint,
            proposal_fingerprint=validated.proposal_fingerprint,
            snapshot=None,
            reason=normalized_reason,
        )
        return self._append_event(event)

    reject_proposal = reject

    def _find_operation_unlocked(
        self,
        verified: ExecutiveStrategyStoreVerifiedSnapshotV1,
        operation_fingerprint: str,
    ) -> ExecutiveStrategyStoreEnvelopeV1 | None:
        return next(
            (
                envelope
                for envelope in verified.envelopes
                if envelope.record.operation_id_fingerprint == operation_fingerprint
            ),
            None,
        )

    @staticmethod
    def _normalize_request_timestamp(value: datetime) -> datetime:
        if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
            raise ExecutiveStrategyStoreInvalidRequestError()
        return value.astimezone(UTC)

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

    def _write_manifest_atomic(self, manifest: ExecutiveStrategyStoreManifestV1) -> None:
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


def _uuid7_request(value: object) -> UUID:
    try:
        return parse_uuid7(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ExecutiveStrategyStoreInvalidRequestError() from exc


def _raw_hash_request(value: object) -> str:
    try:
        return _raw_hash(value)
    except ExecutiveStrategyStoreCorruptError as exc:
        raise ExecutiveStrategyStoreInvalidRequestError() from exc


def derive_executive_strategy_store_root(
    env_file: Path | os.PathLike[str] | None,
) -> Path | None:
    """Derive the additive store only from an explicit existing env file."""

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
        prospective = parent / EXECUTIVE_STRATEGY_STORE_PARENT_NAME
        root = prospective / EXECUTIVE_STRATEGY_STORE_DIRECTORY_NAME
        if prospective.exists() and (prospective.is_symlink() or not prospective.is_dir()):
            return None
        if root.exists() and (root.is_symlink() or not root.is_dir()):
            return None
        return root
    except OSError, RuntimeError, ValueError:
        return None


ExecutiveStrategyStore = ExecutiveStrategySnapshotStore
StrategySnapshotStore = ExecutiveStrategySnapshotStore
ExecutiveStrategySnapshotStoreManifestV1 = ExecutiveStrategyStoreManifestV1
ExecutiveStrategySnapshotStoreStateV1 = ExecutiveStrategyStoreStateV1
StrategySnapshotStoreManifestV1 = ExecutiveStrategyStoreManifestV1
StrategySnapshotStoreStateV1 = ExecutiveStrategyStoreStateV1
derive_strategy_snapshot_store_root = derive_executive_strategy_store_root


__all__ = [
    "EXECUTIVE_STRATEGY_STORE_DIRECTORY_NAME",
    "EXECUTIVE_STRATEGY_STORE_FORMAT_VERSION",
    "EXECUTIVE_STRATEGY_STORE_LOCK_FILE_NAME",
    "EXECUTIVE_STRATEGY_STORE_MANIFEST_FILE_NAME",
    "EXECUTIVE_STRATEGY_STORE_MAX_OPERATION_ID_BYTES",
    "EXECUTIVE_STRATEGY_STORE_MAX_REASON_BYTES",
    "EXECUTIVE_STRATEGY_STORE_MAX_RECORDS",
    "EXECUTIVE_STRATEGY_STORE_MAX_RECORD_BYTES",
    "EXECUTIVE_STRATEGY_STORE_PARENT_NAME",
    "EXECUTIVE_STRATEGY_STORE_RECORD_FILE_NAME",
    "ExecutiveStrategySnapshotStore",
    "ExecutiveStrategySnapshotStoreManifestV1",
    "ExecutiveStrategySnapshotStoreStateV1",
    "ExecutiveStrategyStore",
    "ExecutiveStrategyStoreCorruptError",
    "ExecutiveStrategyStoreEnvelopeV1",
    "ExecutiveStrategyStoreError",
    "ExecutiveStrategyStoreEventV1",
    "ExecutiveStrategyStoreIdempotencyConflictError",
    "ExecutiveStrategyStoreInvalidRequestError",
    "ExecutiveStrategyStoreManifestV1",
    "ExecutiveStrategyStoreRecordType",
    "ExecutiveStrategyStoreRecordTypeV1",
    "ExecutiveStrategyStoreSourceChangedError",
    "ExecutiveStrategyStoreStateConflictError",
    "ExecutiveStrategyStoreStateV1",
    "ExecutiveStrategyStoreUnavailableError",
    "ExecutiveStrategyStoreVerifiedSnapshotV1",
    "StrategySnapshotStore",
    "StrategySnapshotStoreManifestV1",
    "StrategySnapshotStoreStateV1",
    "derive_executive_strategy_store_root",
    "derive_strategy_snapshot_store_root",
    "executive_strategy_store_hash",
]
