"""Fail-closed append-only operational receipts for Stage 19."""

from __future__ import annotations

import json
import os
import stat
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from pathlib import Path
from typing import Final, cast

from second_brain.application.action_gateway import (
    ACTION_GATEWAY_POLICY_FINGERPRINT,
    ACTION_GATEWAY_POLICY_ID,
    ActionGatewayConflictError,
    ActionGatewayError,
    ActionGatewayInvalidRequestError,
    ActionReceiptKindV1,
    ActionReceiptStateV1,
    ActionReceiptV1,
    PreparedExternalActionV1,
    action_gateway_hash,
)
from second_brain.application.adaptive_cognitive_twin_store import _StoreLock

ACTION_GATEWAY_STORE_PARENT_NAME: Final[str] = "prospective-audit"
ACTION_GATEWAY_STORE_DIRECTORY_NAME: Final[str] = "action-gateway"
ACTION_GATEWAY_STORE_RECORD_FILE_NAME: Final[str] = "receipts.jsonl"
ACTION_GATEWAY_STORE_MANIFEST_FILE_NAME: Final[str] = "manifest.json"
ACTION_GATEWAY_STORE_LOCK_FILE_NAME: Final[str] = ".store.lock"
ACTION_GATEWAY_STORE_FORMAT_VERSION: Final[int] = 1
ACTION_GATEWAY_STORE_MAX_RECORDS: Final[int] = 32768
ACTION_GATEWAY_STORE_MAX_RECORD_BYTES: Final[int] = 256 * 1024


class ActionGatewayStoreError(RuntimeError):
    """Base safe error for the operational receipt store."""

    code: str = "action_gateway_store_error"

    def __init__(self, message: str | None = None) -> None:
        super().__init__(message or self.code)


class ActionGatewayStoreUnavailableError(ActionGatewayStoreError):
    """The store cannot be safely initialized or used."""

    code = "store_unavailable"


class ActionGatewayStoreCorruptError(ActionGatewayStoreError):
    """The JSONL chain or manifest failed integrity validation."""

    code = "store_corrupt"


class ActionGatewayStoreCapacityError(ActionGatewayStoreError):
    """The bounded receipt count or envelope size was exceeded."""

    code = "store_capacity"


class ActionGatewayStoreRecordTypeV1(StrEnum):
    """Closed envelope record type."""

    RECEIPT = "action_receipt"


def _canonical_bytes(value: object) -> bytes:
    try:
        return json.dumps(
            value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
        ).encode("utf-8")
    except (TypeError, UnicodeError, ValueError, OverflowError) as exc:
        raise ActionGatewayStoreCorruptError() from exc


def _raw_hash(value: object) -> str:
    if (
        type(value) is not str
        or len(value) != 64
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise ActionGatewayStoreCorruptError()
    return value


def _wire_dict(value: object, expected: set[str]) -> dict[str, object]:
    if type(value) is not dict or any(
        type(key) is not str for key in cast(dict[object, object], value)
    ):
        raise ActionGatewayStoreCorruptError()
    data = cast(dict[str, object], value)
    if set(data) != expected:
        raise ActionGatewayStoreCorruptError()
    return data


def _reject_duplicate_pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ActionGatewayStoreCorruptError()
        result[key] = value
    return result


@dataclass(frozen=True, slots=True)
class ActionGatewayStoreManifestV1:
    """Manifest for the complete hash-chained receipt generation."""

    format_version: int
    next_sequence: int
    record_count: int
    last_record_digest: str | None
    policy_id: str
    policy_fingerprint: str

    def __post_init__(self) -> None:
        if (
            self.format_version != ACTION_GATEWAY_STORE_FORMAT_VERSION
            or type(self.format_version) is not int
            or isinstance(self.format_version, bool)
        ):
            raise ActionGatewayStoreCorruptError()
        if (
            type(self.next_sequence) is not int
            or isinstance(self.next_sequence, bool)
            or not 1 <= self.next_sequence <= 2**64 - 1
        ):
            raise ActionGatewayStoreCorruptError()
        if (
            type(self.record_count) is not int
            or isinstance(self.record_count, bool)
            or not 0 <= self.record_count <= ACTION_GATEWAY_STORE_MAX_RECORDS
            or self.next_sequence != self.record_count + 1
        ):
            raise ActionGatewayStoreCorruptError()
        digest = None if self.last_record_digest is None else _raw_hash(self.last_record_digest)
        if (self.record_count == 0) != (digest is None):
            raise ActionGatewayStoreCorruptError()
        if (
            self.policy_id != ACTION_GATEWAY_POLICY_ID
            or self.policy_fingerprint != ACTION_GATEWAY_POLICY_FINGERPRINT
        ):
            raise ActionGatewayStoreCorruptError()
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
class ActionGatewayStoreEnvelopeV1:
    """One canonical, hash-chained JSONL envelope."""

    sequence: int
    record_type: ActionGatewayStoreRecordTypeV1 | str
    record: ActionReceiptV1
    previous_record_digest: str | None
    record_fingerprint: str
    record_digest: str

    def __post_init__(self) -> None:
        if (
            type(self.sequence) is not int
            or isinstance(self.sequence, bool)
            or not 1 <= self.sequence <= 2**64 - 1
        ):
            raise ActionGatewayStoreCorruptError()
        if (
            self.record_type
            not in {
                ActionGatewayStoreRecordTypeV1.RECEIPT,
                ActionGatewayStoreRecordTypeV1.RECEIPT.value,
            }
            or type(self.record) is not ActionReceiptV1
        ):
            raise ActionGatewayStoreCorruptError()
        previous = (
            None if self.previous_record_digest is None else _raw_hash(self.previous_record_digest)
        )
        fingerprint = _raw_hash(self.record_fingerprint)
        digest = _raw_hash(self.record_digest)
        object.__setattr__(self, "record_type", ActionGatewayStoreRecordTypeV1.RECEIPT)
        object.__setattr__(self, "previous_record_digest", previous)
        object.__setattr__(self, "record_fingerprint", fingerprint)
        object.__setattr__(self, "record_digest", digest)

    def unsigned_dict(self) -> dict[str, object]:
        return {
            "sequence": self.sequence,
            "record_type": ActionGatewayStoreRecordTypeV1.RECEIPT.value,
            "record": self.record.as_dict(),
            "previous_record_digest": self.previous_record_digest,
            "record_fingerprint": self.record_fingerprint,
        }

    @property
    def expected_record_fingerprint(self) -> str:
        return action_gateway_hash(self.record.as_dict())

    @property
    def expected_record_digest(self) -> str:
        return action_gateway_hash(self.unsigned_dict())

    def as_dict(self) -> dict[str, object]:
        return {**self.unsigned_dict(), "record_digest": self.record_digest}


@dataclass(frozen=True, slots=True)
class ActionGatewayStoreSnapshotV1:
    manifest: ActionGatewayStoreManifestV1
    envelopes: tuple[ActionGatewayStoreEnvelopeV1, ...]


class ActionGatewayOperationalStore:
    """Owner-only JSONL receipts outside vault, repository, and releases."""

    def __init__(
        self, root: Path | os.PathLike[str], *, vault_root: Path | os.PathLike[str] | None = None
    ) -> None:
        if not isinstance(root, os.PathLike):
            raise ActionGatewayStoreUnavailableError()
        self.root = Path(root).expanduser()
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
        except ActionGatewayStoreError:
            raise
        except (OSError, ValueError, UnicodeError, RecursionError) as exc:
            raise ActionGatewayStoreUnavailableError() from exc

    @property
    def records_path(self) -> Path:
        return self.root / ACTION_GATEWAY_STORE_RECORD_FILE_NAME

    @property
    def manifest_path(self) -> Path:
        return self.root / ACTION_GATEWAY_STORE_MANIFEST_FILE_NAME

    @property
    def lock_path(self) -> Path:
        return self.root / ACTION_GATEWAY_STORE_LOCK_FILE_NAME

    @property
    def manifest(self) -> ActionGatewayStoreManifestV1:
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
            raise ActionGatewayStoreUnavailableError() from exc

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
                if path.is_symlink() or stat.S_IMODE(path.stat().st_mode) != expected_mode:
                    raise ValueError("store permissions or symlink")
                if path != self.root and not stat.S_ISREG(path.stat().st_mode):
                    raise ValueError("store payload is not regular")
                if path == self.root and not stat.S_ISDIR(path.stat().st_mode):
                    raise ValueError("store root is not directory")
        except OSError as exc:
            raise ActionGatewayStoreUnavailableError() from exc

    def _initialize_unlocked(self) -> None:
        present = (self.records_path.exists(), self.manifest_path.exists())
        if not any(present):
            self._write_bytes_atomic(self.records_path, b"")
            self._write_manifest_atomic(
                ActionGatewayStoreManifestV1(
                    1, 1, 0, None, ACTION_GATEWAY_POLICY_ID, ACTION_GATEWAY_POLICY_FINGERPRINT
                )
            )
        elif not all(present):
            raise ActionGatewayStoreCorruptError()
        else:
            self._read_verified_unlocked()

    def _read_manifest_unlocked(self) -> ActionGatewayStoreManifestV1:
        try:
            raw = self.manifest_path.read_bytes()
            decoded = json.loads(
                raw.decode("utf-8"),
                object_pairs_hook=_reject_duplicate_pairs,
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
            return ActionGatewayStoreManifestV1(
                cast(int, data["format_version"]),
                cast(int, data["next_sequence"]),
                cast(int, data["record_count"]),
                cast(str | None, data["last_record_digest"]),
                cast(str, data["policy_id"]),
                cast(str, data["policy_fingerprint"]),
            )
        except ActionGatewayStoreError:
            raise
        except (OSError, UnicodeError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise ActionGatewayStoreCorruptError() from exc

    def _read_verified_unlocked(self) -> ActionGatewayStoreSnapshotV1:
        self._assert_owner_only()
        manifest = self._read_manifest_unlocked()
        try:
            raw = self.records_path.read_bytes()
        except OSError as exc:
            raise ActionGatewayStoreUnavailableError() from exc
        if not raw:
            if manifest.record_count != 0 or manifest.last_record_digest is not None:
                raise ActionGatewayStoreCorruptError()
            return ActionGatewayStoreSnapshotV1(manifest, ())
        if raw.startswith(b"\xef\xbb\xbf") or not raw.endswith(b"\n"):
            raise ActionGatewayStoreCorruptError()
        envelopes: list[ActionGatewayStoreEnvelopeV1] = []
        previous: str | None = None
        receipt_ids: set[str] = set()
        operations: dict[str, str] = {}
        for line in raw.splitlines(keepends=True):
            if line.endswith(b"\r\n") or not line.endswith(b"\n"):
                raise ActionGatewayStoreCorruptError()
            payload = line[:-1]
            if not payload or len(payload) > ACTION_GATEWAY_STORE_MAX_RECORD_BYTES:
                raise ActionGatewayStoreCorruptError()
            try:
                decoded = json.loads(
                    payload.decode("utf-8"),
                    object_pairs_hook=_reject_duplicate_pairs,
                    parse_constant=lambda _: (_ for _ in ()).throw(ValueError()),
                )
                data = _wire_dict(
                    decoded,
                    {
                        "sequence",
                        "record_type",
                        "record",
                        "previous_record_digest",
                        "record_fingerprint",
                        "record_digest",
                    },
                )
                envelope = ActionGatewayStoreEnvelopeV1(
                    cast(int, data["sequence"]),
                    cast(str, data["record_type"]),
                    ActionReceiptV1.from_dict(data["record"]),
                    cast(str | None, data["previous_record_digest"]),
                    cast(str, data["record_fingerprint"]),
                    cast(str, data["record_digest"]),
                )
                if (
                    _canonical_bytes(decoded) != payload
                    or envelope.expected_record_fingerprint != envelope.record_fingerprint
                    or envelope.expected_record_digest != envelope.record_digest
                    or envelope.sequence != len(envelopes) + 1
                    or envelope.previous_record_digest != previous
                ):
                    raise ValueError("receipt chain")
                receipt_key = str(envelope.record.receipt_id)
                if receipt_key in receipt_ids:
                    raise ValueError("duplicate receipt")
                operation = envelope.record.operation_id_fingerprint
                prior_intent = operations.get(operation)
                if prior_intent is not None and prior_intent != envelope.record.intent_fingerprint:
                    raise ValueError("operation intent conflict")
                operations[operation] = envelope.record.intent_fingerprint
            except ActionGatewayStoreError:
                raise
            except (
                ActionGatewayError,
                TypeError,
                ValueError,
                UnicodeError,
                OverflowError,
                json.JSONDecodeError,
            ) as exc:
                raise ActionGatewayStoreCorruptError() from exc
            envelopes.append(envelope)
            receipt_ids.add(receipt_key)
            previous = envelope.record_digest
        if (
            len(envelopes) > ACTION_GATEWAY_STORE_MAX_RECORDS
            or manifest.record_count != len(envelopes)
            or manifest.next_sequence != len(envelopes) + 1
            or manifest.last_record_digest != previous
        ):
            raise ActionGatewayStoreCorruptError()
        return ActionGatewayStoreSnapshotV1(manifest, tuple(envelopes))

    def read_verified_snapshot(self) -> ActionGatewayStoreSnapshotV1:
        try:
            with _StoreLock(self.lock_path):
                return self._read_verified_unlocked()
        except ActionGatewayStoreError:
            raise
        except (OSError, ValueError, UnicodeError, RecursionError) as exc:
            raise ActionGatewayStoreUnavailableError() from exc

    verified_snapshot = read_verified_snapshot

    def read_receipts(self) -> tuple[ActionReceiptV1, ...]:
        return tuple(item.record for item in self.read_verified_snapshot().envelopes)

    def read_manifest(self) -> ActionGatewayStoreManifestV1:
        return self.read_verified_snapshot().manifest

    def find_operation(self, operation_id_fingerprint: str) -> ActionReceiptV1 | None:
        if type(operation_id_fingerprint) is not str or len(operation_id_fingerprint) != 64:
            raise ActionGatewayInvalidRequestError()
        return next(
            (
                receipt
                for receipt in reversed(self.read_receipts())
                if receipt.operation_id_fingerprint == operation_id_fingerprint
            ),
            None,
        )

    def begin_execution(
        self, prepared: PreparedExternalActionV1, *, now: datetime
    ) -> tuple[ActionReceiptV1, bool]:
        if type(prepared) is not PreparedExternalActionV1:
            raise ActionGatewayInvalidRequestError()
        try:
            with _StoreLock(self.lock_path):
                snapshot = self._read_verified_unlocked()
                existing = self._find_operation(snapshot, prepared.operation_id_fingerprint)
                if existing is not None:
                    if existing.intent_fingerprint != _intent_fingerprint_from_prepared(prepared):
                        raise ActionGatewayConflictError()
                    return existing, False
                receipt = _started_receipt(prepared, now=now)
                return self._append_unlocked(snapshot, receipt), True
        except ActionGatewayStoreError, ActionGatewayError:
            raise
        except (OSError, ValueError, UnicodeError, RecursionError) as exc:
            raise ActionGatewayStoreUnavailableError() from exc

    def append(self, receipt: ActionReceiptV1) -> ActionReceiptV1:
        if type(receipt) is not ActionReceiptV1:
            raise ActionGatewayInvalidRequestError()
        try:
            with _StoreLock(self.lock_path):
                snapshot = self._read_verified_unlocked()
                return self._append_unlocked(snapshot, receipt)
        except ActionGatewayStoreError, ActionGatewayError:
            raise
        except (OSError, ValueError, UnicodeError, RecursionError) as exc:
            raise ActionGatewayStoreUnavailableError() from exc

    def _find_operation(
        self, snapshot: ActionGatewayStoreSnapshotV1, operation: str
    ) -> ActionReceiptV1 | None:
        return next(
            (
                item.record
                for item in reversed(snapshot.envelopes)
                if item.record.operation_id_fingerprint == operation
            ),
            None,
        )

    def _append_unlocked(
        self, snapshot: ActionGatewayStoreSnapshotV1, receipt: ActionReceiptV1
    ) -> ActionReceiptV1:
        existing = self._find_operation(snapshot, receipt.operation_id_fingerprint)
        if existing is not None and existing.intent_fingerprint != receipt.intent_fingerprint:
            raise ActionGatewayConflictError()
        if (
            existing is not None
            and receipt.state is ActionReceiptStateV1.EXECUTION_STARTED
            and existing.state is ActionReceiptStateV1.EXECUTION_STARTED
        ):
            return existing
        if snapshot.manifest.record_count >= ACTION_GATEWAY_STORE_MAX_RECORDS:
            raise ActionGatewayStoreCapacityError()
        sequence = snapshot.manifest.next_sequence
        unsigned = {
            "sequence": sequence,
            "record_type": ActionGatewayStoreRecordTypeV1.RECEIPT.value,
            "record": receipt.as_dict(),
            "previous_record_digest": snapshot.manifest.last_record_digest,
            "record_fingerprint": action_gateway_hash(receipt.as_dict()),
        }
        envelope = ActionGatewayStoreEnvelopeV1(
            sequence,
            ActionGatewayStoreRecordTypeV1.RECEIPT,
            receipt,
            snapshot.manifest.last_record_digest,
            cast(str, unsigned["record_fingerprint"]),
            action_gateway_hash(unsigned),
        )
        line = _canonical_bytes(envelope.as_dict()) + b"\n"
        if len(line) > ACTION_GATEWAY_STORE_MAX_RECORD_BYTES:
            raise ActionGatewayStoreCapacityError()
        self._append_bytes_durable(self.records_path, line)
        self._write_manifest_atomic(
            ActionGatewayStoreManifestV1(
                1,
                sequence + 1,
                sequence,
                envelope.record_digest,
                ACTION_GATEWAY_POLICY_ID,
                ACTION_GATEWAY_POLICY_FINGERPRINT,
            )
        )
        reread = self._read_verified_unlocked()
        if not reread.envelopes or reread.envelopes[-1] != envelope:
            raise ActionGatewayStoreUnavailableError()
        return receipt

    @staticmethod
    def _append_bytes_durable(path: Path, payload: bytes) -> None:
        try:
            descriptor = os.open(
                path, os.O_WRONLY | os.O_APPEND | os.O_CREAT | getattr(os, "O_BINARY", 0), 0o600
            )
            try:
                written = os.write(descriptor, payload)
                if written != len(payload):
                    raise OSError("short append")
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
        except OSError as exc:
            raise ActionGatewayStoreUnavailableError() from exc

    def _write_manifest_atomic(self, manifest: ActionGatewayStoreManifestV1) -> None:
        temporary = self.root / f".{self.manifest_path.name}.tmp"
        descriptor: int | None = None
        try:
            descriptor = os.open(
                temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0), 0o600
            )
            payload = _canonical_bytes(manifest.as_dict())
            if os.write(descriptor, payload) != len(payload):
                raise OSError("short manifest")
            os.fsync(descriptor)
            os.close(descriptor)
            descriptor = None
            os.replace(temporary, self.manifest_path)
            os.chmod(self.manifest_path, 0o600)
            self._fsync_directory()
        except OSError as exc:
            raise ActionGatewayStoreUnavailableError() from exc
        finally:
            if descriptor is not None:
                os.close(descriptor)
            try:
                temporary.unlink()
            except FileNotFoundError:
                pass
            except OSError:
                pass

    def _write_bytes_atomic(self, path: Path, payload: bytes) -> None:
        temporary = self.root / f".{path.name}.tmp"
        descriptor: int | None = None
        try:
            descriptor = os.open(
                temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0), 0o600
            )
            if os.write(descriptor, payload) != len(payload):
                raise OSError("short payload")
            os.fsync(descriptor)
            os.close(descriptor)
            descriptor = None
            os.replace(temporary, path)
            os.chmod(path, 0o600)
            self._fsync_directory()
        except OSError as exc:
            raise ActionGatewayStoreUnavailableError() from exc
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


def _intent_fingerprint_from_prepared(prepared: PreparedExternalActionV1) -> str:
    payload = {
        "contract_version": "action-intent-v1",
        "action_kind": str(prepared.action_kind),
        "connector": prepared.connector,
        "repository": prepared.semantic_payload["repository"],
    }
    if prepared.provenance is not None:
        payload["provenance"] = prepared.provenance.as_dict()
    action = str(prepared.action_kind)
    if action == "github.issue.create":
        marker = cast(str, prepared.semantic_payload["marker"])
        payload.update(
            {
                "title": prepared.semantic_payload["title"],
                "body": cast(str, prepared.semantic_payload["body"])
                .removesuffix("\n" + marker)
                .removesuffix(marker),
            }
        )
    elif action == "github.issue.comment":
        marker = cast(str, prepared.semantic_payload["marker"])
        payload.update(
            {
                "issue_number": prepared.semantic_payload["issue_number"],
                "comment": cast(str, prepared.semantic_payload["comment"])
                .removesuffix("\n" + marker)
                .removesuffix(marker),
            }
        )
    else:
        payload.update(
            {
                "issue_number": prepared.semantic_payload["issue_number"],
                "desired_state": prepared.semantic_payload["desired_state"],
            }
        )
    return action_gateway_hash(payload)


def _started_receipt(prepared: PreparedExternalActionV1, *, now: object) -> ActionReceiptV1:
    from second_brain.application.action_gateway import _utc

    started = _utc(now)
    return ActionReceiptV1(
        receipt_id=__import__("uuid").uuid7(),
        receipt_kind=ActionReceiptKindV1.ACTION,
        operation_id_fingerprint=prepared.operation_id_fingerprint,
        prepared_action_id=prepared.prepared_action_id,
        intent_fingerprint=_intent_fingerprint_from_prepared(prepared),
        action_kind=prepared.action_kind,
        risk=prepared.risk,
        connector_policy_id=prepared.connector_policy_id,
        credential_profile_id=prepared.credential_profile_id,
        target_safe_identity=prepared.exact_target_identity.safe_identity(),
        payload_fingerprint=prepared.payload_fingerprint,
        state=ActionReceiptStateV1.EXECUTION_STARTED,
        attempt_started_at=started,
    )


def derive_action_gateway_store_root(env_file: Path | os.PathLike[str] | None) -> Path | None:
    """Derive the operational store beside one explicit deployment env file."""

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
        prospective = parent / ACTION_GATEWAY_STORE_PARENT_NAME
        root = prospective / ACTION_GATEWAY_STORE_DIRECTORY_NAME
        if prospective.exists() and (prospective.is_symlink() or not prospective.is_dir()):
            return None
        if root.exists() and (root.is_symlink() or not root.is_dir()):
            return None
        return root
    except OSError, RuntimeError, ValueError:
        return None


ActionGatewayStore = ActionGatewayOperationalStore
ActionGatewayStoreManifest = ActionGatewayStoreManifestV1
ActionGatewayStoreEnvelope = ActionGatewayStoreEnvelopeV1
derive_action_store_root = derive_action_gateway_store_root


__all__ = [
    "ACTION_GATEWAY_STORE_DIRECTORY_NAME",
    "ACTION_GATEWAY_STORE_FORMAT_VERSION",
    "ACTION_GATEWAY_STORE_LOCK_FILE_NAME",
    "ACTION_GATEWAY_STORE_MANIFEST_FILE_NAME",
    "ACTION_GATEWAY_STORE_MAX_RECORDS",
    "ACTION_GATEWAY_STORE_MAX_RECORD_BYTES",
    "ACTION_GATEWAY_STORE_PARENT_NAME",
    "ACTION_GATEWAY_STORE_RECORD_FILE_NAME",
    "ActionGatewayOperationalStore",
    "ActionGatewayStore",
    "ActionGatewayStoreCapacityError",
    "ActionGatewayStoreCorruptError",
    "ActionGatewayStoreEnvelope",
    "ActionGatewayStoreEnvelopeV1",
    "ActionGatewayStoreError",
    "ActionGatewayStoreManifest",
    "ActionGatewayStoreManifestV1",
    "ActionGatewayStoreRecordTypeV1",
    "ActionGatewayStoreSnapshotV1",
    "ActionGatewayStoreUnavailableError",
    "derive_action_gateway_store_root",
    "derive_action_store_root",
]
