"""Stage 9A prospective Simulate Me audit core.

This module deliberately contains only the operational audit boundary.  It does
not read or write the vault, expose a Web route, call a provider, or infer a
Decision Journal outcome.  The store is an explicitly supplied local path and
the only text retained from a request is the bounded, normalized option label
allowed by the Stage 9 contract.
"""

from __future__ import annotations

import errno
import hashlib
import json
import os
import re
import stat
import unicodedata
import uuid
from collections.abc import Callable, Mapping, Sequence
from contextlib import AbstractContextManager
from dataclasses import dataclass, fields, is_dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from pathlib import Path
from typing import Any, Final, NoReturn, Protocol, cast
from uuid import UUID

from second_brain.application.simulate_me import (
    DERIVATION_VERSION as SIMULATE_ME_DERIVATION_VERSION,
)
from second_brain.application.simulate_me import (
    MAX_LABEL_BYTES,
    MAX_QUERY_BYTES,
    MAX_RESULT_REFS,
    SimulateMeContextualEvidenceRef,
    SimulateMeEvidenceRef,
    SimulateMeOption,
    SimulateMeRequest,
    SimulateMeResult,
    SimulateMeTemporalCaveat,
    normalize_simulate_me_text,
    validate_simulate_me_policy,
    validate_simulate_me_request,
    validate_simulate_me_result,
)
from second_brain.application.simulate_me import (
    POLICY_FINGERPRINT as SIMULATE_ME_POLICY_FINGERPRINT,
)
from second_brain.application.simulate_me import (
    POLICY_ID as SIMULATE_ME_POLICY_ID,
)

type AuditHashV1 = str

CONTRACT_VERSION: Final[str] = "prospective-audit-calibration-v1"
EVENT_VERSION: Final[str] = "1"
EVENT_TYPE: Final[str] = "simulate_me_terminal_result"
CAPTURE_MODE: Final[str] = "explicit-foreground-simulate-me-v1"
DERIVATION_VERSION: Final[str] = SIMULATE_ME_DERIVATION_VERSION
POLICY_ID: Final[str] = SIMULATE_ME_POLICY_ID
POLICY_FINGERPRINT: Final[str] = SIMULATE_ME_POLICY_FINGERPRINT
RETENTION_POLICY: Final[str] = "prospective-audit-retention-180d-v1"
RETENTION: Final[timedelta] = timedelta(days=180)
STORE_FORMAT_VERSION: Final[str] = "prospective-audit-store-v1"

# Explicit names make the boundary easy to consume without duplicating the
# normative identities at another application layer.
PROSPECTIVE_AUDIT_CONTRACT_VERSION: Final[str] = CONTRACT_VERSION
PROSPECTIVE_AUDIT_EVENT_TYPE: Final[str] = EVENT_TYPE
PROSPECTIVE_AUDIT_CAPTURE_MODE: Final[str] = CAPTURE_MODE
PROSPECTIVE_AUDIT_DERIVATION_VERSION: Final[str] = DERIVATION_VERSION
PROSPECTIVE_AUDIT_POLICY_ID: Final[str] = POLICY_ID
PROSPECTIVE_AUDIT_POLICY_FINGERPRINT: Final[str] = POLICY_FINGERPRINT
PROSPECTIVE_AUDIT_RETENTION_POLICY: Final[str] = RETENTION_POLICY

MIN_OPTIONS: Final[int] = 1
MAX_OPTIONS: Final[int] = 8
MAX_OPERATION_ID_BYTES: Final[int] = 256
MAX_EVENT_BYTES: Final[int] = 262_144
MAX_UINT64: Final[int] = (1 << 64) - 1

EVENTS_FILE_NAME: Final[str] = "events.jsonl"
LINKS_FILE_NAME: Final[str] = "links.jsonl"
MANIFEST_FILE_NAME: Final[str] = "manifest.json"
LOCK_FILE_NAME: Final[str] = ".store.lock"

_HASH_PATTERN: Final[re.Pattern[str]] = re.compile(r"sha256:[0-9a-f]{64}\Z", re.ASCII)
_ID_PATTERN: Final[re.Pattern[str]] = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,63}\Z", re.ASCII)
_CANONICAL_TIME_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?Z\Z"
)


class ProspectiveAuditResultKind(StrEnum):
    """Terminal Stage 6 result kinds retained by the event."""

    PREDICTION = "prediction"
    ABSTENTION = "abstention"


class ProspectiveAuditAbstentionCode(StrEnum):
    """The closed Stage 6 abstention vocabulary."""

    NO_MATCHING_EVIDENCE = "no_matching_evidence"
    MULTIPLE_OPTIONS_SUPPORTED = "multiple_options_supported"
    INSUFFICIENT_OR_INVALID_CURRENT_CONTEXT = "insufficient_or_invalid_current_context"


class ProspectiveAuditErrorCode(StrEnum):
    """Fixed safe application/store error taxonomy."""

    INVALID_REQUEST = "PROSPECTIVE_AUDIT_INVALID_REQUEST"
    RESULT_INVALID = "PROSPECTIVE_AUDIT_RESULT_INVALID"
    STALE_OR_REPLAYED = "PROSPECTIVE_AUDIT_STALE_OR_REPLAYED"
    IDEMPOTENCY_CONFLICT = "PROSPECTIVE_AUDIT_IDEMPOTENCY_CONFLICT"
    STORE_UNAVAILABLE = "PROSPECTIVE_AUDIT_STORE_UNAVAILABLE"
    STORE_CORRUPT = "PROSPECTIVE_AUDIT_STORE_CORRUPT"
    CANCELLED = "PROSPECTIVE_AUDIT_CANCELLED"


_ERROR_MESSAGES: Final[dict[ProspectiveAuditErrorCode, str]] = {
    ProspectiveAuditErrorCode.INVALID_REQUEST: "prospective audit request failed validation",
    ProspectiveAuditErrorCode.RESULT_INVALID: "prospective audit result failed validation",
    ProspectiveAuditErrorCode.STALE_OR_REPLAYED: "prospective audit result is stale or replayed",
    ProspectiveAuditErrorCode.IDEMPOTENCY_CONFLICT: (
        "prospective audit operation conflicts with existing record"
    ),
    ProspectiveAuditErrorCode.STORE_UNAVAILABLE: "prospective audit store is unavailable",
    ProspectiveAuditErrorCode.STORE_CORRUPT: "prospective audit store is corrupt",
    ProspectiveAuditErrorCode.CANCELLED: "prospective audit operation cancelled",
}


class ProspectiveAuditError(RuntimeError):
    """Public error containing only a fixed code and fixed message."""

    def __init__(self, code: ProspectiveAuditErrorCode | str) -> None:
        try:
            normalized = ProspectiveAuditErrorCode(code)
        except TypeError, ValueError:
            normalized = ProspectiveAuditErrorCode.STORE_UNAVAILABLE
        self.code = normalized.value
        self.message = _ERROR_MESSAGES[normalized]
        super().__init__(self.message)

    def as_dict(self) -> dict[str, str]:
        """Return the bounded error projection."""

        return {"code": self.code, "message": self.message}


class ProspectiveAuditInvalidRequestError(ProspectiveAuditError):
    def __init__(self) -> None:
        super().__init__(ProspectiveAuditErrorCode.INVALID_REQUEST)


class ProspectiveAuditResultInvalidError(ProspectiveAuditError):
    def __init__(self) -> None:
        super().__init__(ProspectiveAuditErrorCode.RESULT_INVALID)


class ProspectiveAuditStaleOrReplayedError(ProspectiveAuditError):
    def __init__(self) -> None:
        super().__init__(ProspectiveAuditErrorCode.STALE_OR_REPLAYED)


class ProspectiveAuditIdempotencyConflictError(ProspectiveAuditError):
    def __init__(self) -> None:
        super().__init__(ProspectiveAuditErrorCode.IDEMPOTENCY_CONFLICT)


class ProspectiveAuditStoreUnavailableError(ProspectiveAuditError):
    def __init__(self) -> None:
        super().__init__(ProspectiveAuditErrorCode.STORE_UNAVAILABLE)


class ProspectiveAuditStoreCorruptError(ProspectiveAuditError):
    def __init__(self) -> None:
        super().__init__(ProspectiveAuditErrorCode.STORE_CORRUPT)


class ProspectiveAuditCancelledError(ProspectiveAuditError):
    def __init__(self) -> None:
        super().__init__(ProspectiveAuditErrorCode.CANCELLED)


def _valid_hash(value: object) -> bool:
    return type(value) is str and _HASH_PATTERN.fullmatch(value) is not None


def _require_hash(value: object) -> AuditHashV1:
    if not _valid_hash(value):
        raise ValueError("invalid audit hash")
    return cast(str, value)


def _hash_bytes(value: bytes) -> AuditHashV1:
    return f"sha256:{hashlib.sha256(value).hexdigest()}"


def _normalize_bounded_text(value: object, *, max_bytes: int) -> str:
    if type(value) is not str:
        raise ValueError("text must be a string")
    try:
        value.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise ValueError("text is not valid UTF-8") from exc
    if any(
        ord(character) < 0x20
        or 0x7F <= ord(character) <= 0x9F
        or unicodedata.category(character) == "Cf"
        for character in value
    ):
        raise ValueError("text contains a control character")
    normalized = unicodedata.normalize("NFC", value).strip()
    if not normalized:
        raise ValueError("text must not be empty")
    try:
        size = len(normalized.encode("utf-8"))
    except UnicodeEncodeError as exc:
        raise ValueError("text is not valid UTF-8") from exc
    if size > max_bytes:
        raise ValueError("text exceeds its byte bound")
    return normalized


def normalize_operation_id(value: object) -> str:
    """Normalize one explicit opaque operation token before hashing it."""

    try:
        return _normalize_bounded_text(value, max_bytes=MAX_OPERATION_ID_BYTES)
    except ValueError as exc:
        raise ProspectiveAuditInvalidRequestError() from exc


def fingerprint_text(value: object, *, max_bytes: int | None = None) -> AuditHashV1:
    """Hash normalized UTF-8 text without retaining the text."""

    if max_bytes is None:
        try:
            normalized = _normalize_bounded_text(value, max_bytes=MAX_OPERATION_ID_BYTES)
        except ValueError as exc:
            raise ProspectiveAuditInvalidRequestError() from exc
    else:
        try:
            normalized = _normalize_bounded_text(value, max_bytes=max_bytes)
        except ValueError as exc:
            raise ProspectiveAuditInvalidRequestError() from exc
    return _hash_bytes(normalized.encode("utf-8"))


def fingerprint_operation_id(operation_id: object) -> AuditHashV1:
    """Return the exact operation-id fingerprint used for idempotency."""

    return fingerprint_text(normalize_operation_id(operation_id), max_bytes=MAX_OPERATION_ID_BYTES)


def fingerprint_query(query: object) -> AuditHashV1:
    """Fingerprint a normalized Stage 6 query without retaining its body."""

    try:
        if type(query) is not str:
            raise ValueError("query must be a string")
        normalized = normalize_simulate_me_text(query, MAX_QUERY_BYTES)
    except Exception as exc:
        raise ProspectiveAuditInvalidRequestError() from exc
    return _hash_bytes(normalized.encode("utf-8"))


def fingerprint_option_label(label: object) -> AuditHashV1:
    """Fingerprint one normalized caller-owned option label."""

    try:
        if type(label) is not str:
            raise ValueError("label must be a string")
        normalized = normalize_simulate_me_text(label, MAX_LABEL_BYTES)
    except Exception as exc:
        raise ProspectiveAuditInvalidRequestError() from exc
    return _hash_bytes(normalized.encode("utf-8"))


def _canonical_datetime(value: datetime) -> str:
    if type(value) is not datetime or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("timestamp must be aware")
    normalized = value.astimezone(UTC)
    text = normalized.strftime("%Y-%m-%dT%H:%M:%S")
    if normalized.microsecond:
        text += f".{normalized.microsecond:06d}".rstrip("0")
    text += "Z"
    if not _CANONICAL_TIME_PATTERN.fullmatch(text):
        raise ValueError("timestamp is not canonical RFC3339 UTC")
    return text


def _parse_canonical_datetime(value: object) -> datetime:
    if type(value) is not str or _CANONICAL_TIME_PATTERN.fullmatch(value) is None:
        raise ValueError("timestamp is not canonical RFC3339 UTC")
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as exc:
        raise ValueError("timestamp is not canonical RFC3339 UTC") from exc
    if _canonical_datetime(parsed) != value:
        raise ValueError("timestamp is not canonical RFC3339 UTC")
    return parsed


def canonical_json_bytes(value: object) -> bytes:
    """Serialize JSON using the contract's exact UTF-8 byte rules."""

    try:
        normalized = _jsonable(value)
        text = json.dumps(
            normalized,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        return text.encode("utf-8")
    except (TypeError, ValueError, OverflowError, RecursionError, UnicodeError) as exc:
        raise ValueError("value cannot be canonically serialized") from exc


def canonical_json(value: object) -> str:
    """Return canonical JSON text without a trailing newline."""

    return canonical_json_bytes(value).decode("utf-8")


def fingerprint_json(value: object) -> AuditHashV1:
    return _hash_bytes(canonical_json_bytes(value))


def _jsonable(value: object) -> object:
    if value is None or type(value) in {str, int, bool}:
        return value
    if type(value) is float:
        raise ValueError("floats are not permitted")
    if isinstance(value, StrEnum):
        return value.value
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, datetime):
        return _canonical_datetime(value)
    if isinstance(value, Mapping):
        result: dict[str, object] = {}
        for key, item in value.items():
            if type(key) is not str:
                raise ValueError("JSON keys must be strings")
            result[key] = _jsonable(item)
        return result
    if isinstance(value, (tuple, list)):
        return [_jsonable(item) for item in value]
    if is_dataclass(value):
        return {field.name: _jsonable(getattr(value, field.name)) for field in fields(value)}
    raise TypeError("unsupported JSON value")


def _validate_uuid7_string(value: object) -> str:
    if type(value) is not str:
        raise ValueError("UUID must be a string")
    try:
        parsed = UUID(value)
    except ValueError as exc:
        raise ValueError("UUID is invalid") from exc
    if value != str(parsed) or parsed.version != 7:
        raise ValueError("UUID must be lowercase canonical UUIDv7")
    return value


def _option_payload(option: SimulateMeOption) -> dict[str, str]:
    return {"id": option.id, "label": option.label}


def _evidence_payload(ref: SimulateMeEvidenceRef) -> dict[str, object]:
    return {
        "claim_id": str(ref.claim_id),
        "dimension": ref.dimension.value,
        "note_ids": [str(note_id) for note_id in ref.note_ids],
        "evidence_at": (
            "unknown" if ref.evidence_at == "unknown" else _canonical_datetime(ref.evidence_at)
        ),
    }


def _contextual_payload(ref: SimulateMeContextualEvidenceRef) -> dict[str, object]:
    return {
        "claim_id": str(ref.claim_id),
        "dimension": ref.dimension.value,
        "note_ids": [str(note_id) for note_id in ref.note_ids],
        "evidence_at": (
            "unknown" if ref.evidence_at == "unknown" else _canonical_datetime(ref.evidence_at)
        ),
    }


def _caveat_payload(caveat: SimulateMeTemporalCaveat) -> dict[str, str]:
    return {"code": caveat.code.value, "claim_id": str(caveat.claim_id)}


def _stage6_result_payload(result: SimulateMeResult) -> dict[str, object]:
    return {
        "kind": result.kind.value,
        "selected_option": (
            None if result.selected_option is None else _option_payload(result.selected_option)
        ),
        "evidence_refs": [_evidence_payload(ref) for ref in result.evidence_refs],
        "contextual_evidence_refs": [
            _contextual_payload(ref) for ref in result.contextual_evidence_refs
        ],
        "temporal_caveats": [_caveat_payload(caveat) for caveat in result.temporal_caveats],
        "abstention_code": (
            None if result.abstention_code is None else result.abstention_code.value
        ),
        "derivation_version": result.derivation_version,
        "policy_id": result.policy_id,
        "policy_fingerprint": result.policy_fingerprint,
    }


def fingerprint_simulate_me_result(result: SimulateMeResult) -> AuditHashV1:
    """Fingerprint a complete validated Stage 6 result."""

    return fingerprint_json(_stage6_result_payload(result))


def fingerprint_source_refs(result: SimulateMeResult) -> AuditHashV1:
    """Fingerprint refs/caveats while keeping raw refs out of the event."""

    return fingerprint_json(
        {
            "evidence_refs": [_evidence_payload(ref) for ref in result.evidence_refs],
            "contextual_evidence_refs": [
                _contextual_payload(ref) for ref in result.contextual_evidence_refs
            ],
            "temporal_caveats": [_caveat_payload(caveat) for caveat in result.temporal_caveats],
        }
    )


@dataclass(frozen=True, slots=True)
class ProspectiveAuditOptionV1:
    id: str
    ordinal: int
    label: str
    label_fingerprint: AuditHashV1

    def __post_init__(self) -> None:
        if type(self.id) is not str or _ID_PATTERN.fullmatch(self.id) is None:
            raise ValueError("option id is invalid")
        if (
            type(self.ordinal) is not int
            or isinstance(self.ordinal, bool)
            or not 0 <= self.ordinal < MAX_OPTIONS
        ):
            raise ValueError("option ordinal is invalid")
        normalized = _normalize_bounded_text(self.label, max_bytes=MAX_LABEL_BYTES)
        if normalized != self.label:
            raise ValueError("option label must be normalized")
        if not _valid_hash(self.label_fingerprint) or self.label_fingerprint != fingerprint_text(
            normalized, max_bytes=MAX_LABEL_BYTES
        ):
            raise ValueError("option label fingerprint is invalid")

    def as_dict(self) -> dict[str, object]:
        return {
            "id": self.id,
            "ordinal": self.ordinal,
            "label": self.label,
            "label_fingerprint": self.label_fingerprint,
        }


@dataclass(frozen=True, slots=True)
class ProspectiveAuditRequestV1:
    query_fingerprint: AuditHashV1
    options: tuple[ProspectiveAuditOptionV1, ...]
    options_fingerprint: AuditHashV1
    request_fingerprint: AuditHashV1

    def __post_init__(self) -> None:
        _require_hash(self.query_fingerprint)
        if type(self.options) is not tuple or not MIN_OPTIONS <= len(self.options) <= MAX_OPTIONS:
            raise ValueError("request options are invalid")
        ids: set[str] = set()
        for index, option in enumerate(self.options):
            if type(option) is not ProspectiveAuditOptionV1 or option.ordinal != index:
                raise ValueError("request options are invalid")
            if option.id in ids:
                raise ValueError("request option ids are duplicated")
            ids.add(option.id)
        expected_options = fingerprint_json(
            [
                {
                    "id": option.id,
                    "ordinal": option.ordinal,
                    "label_fingerprint": option.label_fingerprint,
                }
                for option in self.options
            ]
        )
        if self.options_fingerprint != expected_options:
            raise ValueError("options fingerprint is invalid")
        expected_request = fingerprint_json(
            {
                "query_fingerprint": self.query_fingerprint,
                "options_fingerprint": self.options_fingerprint,
            }
        )
        if self.request_fingerprint != expected_request:
            raise ValueError("request fingerprint is invalid")

    def as_dict(self) -> dict[str, object]:
        return {
            "query_fingerprint": self.query_fingerprint,
            "options": [option.as_dict() for option in self.options],
            "options_fingerprint": self.options_fingerprint,
            "request_fingerprint": self.request_fingerprint,
        }


@dataclass(frozen=True, slots=True)
class ProspectiveAuditResultV1:
    kind: ProspectiveAuditResultKind
    predicted_option_id: str | None
    abstention_code: ProspectiveAuditAbstentionCode | None

    def __post_init__(self) -> None:
        if type(self.kind) is not ProspectiveAuditResultKind:
            raise ValueError("result kind is invalid")
        if self.kind is ProspectiveAuditResultKind.PREDICTION:
            if (
                type(self.predicted_option_id) is not str
                or _ID_PATTERN.fullmatch(self.predicted_option_id) is None
                or self.abstention_code is not None
            ):
                raise ValueError("prediction result is invalid")
        else:
            if (
                self.predicted_option_id is not None
                or type(self.abstention_code) is not ProspectiveAuditAbstentionCode
            ):
                raise ValueError("abstention result is invalid")

    def as_dict(self) -> dict[str, object]:
        return {
            "kind": self.kind.value,
            "predicted_option_id": self.predicted_option_id,
            "abstention_code": (
                None if self.abstention_code is None else self.abstention_code.value
            ),
        }


@dataclass(frozen=True, slots=True)
class ProspectiveAuditSourceV1:
    result_fingerprint: AuditHashV1
    source_refs_fingerprint: AuditHashV1
    evidence_ref_count: int
    contextual_ref_count: int
    temporal_caveat_count: int

    def __post_init__(self) -> None:
        _require_hash(self.result_fingerprint)
        _require_hash(self.source_refs_fingerprint)
        for count in (
            self.evidence_ref_count,
            self.contextual_ref_count,
            self.temporal_caveat_count,
        ):
            if (
                type(count) is not int
                or isinstance(count, bool)
                or not 0 <= count <= MAX_RESULT_REFS
            ):
                raise ValueError("source count is invalid")

    def as_dict(self) -> dict[str, object]:
        return {
            "result_fingerprint": self.result_fingerprint,
            "source_refs_fingerprint": self.source_refs_fingerprint,
            "evidence_ref_count": self.evidence_ref_count,
            "contextual_ref_count": self.contextual_ref_count,
            "temporal_caveat_count": self.temporal_caveat_count,
        }


@dataclass(frozen=True, slots=True)
class ProspectiveAuditEventV1:
    contract_version: str
    version: str
    event_type: str
    event_id: str
    operation_id_fingerprint: AuditHashV1
    created_at: datetime
    capture_mode: str
    derivation_version: str
    policy_id: str
    policy_fingerprint: AuditHashV1
    request: ProspectiveAuditRequestV1
    result: ProspectiveAuditResultV1
    source: ProspectiveAuditSourceV1

    def __post_init__(self) -> None:
        if (
            type(self.contract_version) is not str
            or type(self.version) is not str
            or self.contract_version != CONTRACT_VERSION
            or self.version != EVENT_VERSION
        ):
            raise ValueError("event contract identity is invalid")
        if (
            type(self.event_type) is not str
            or type(self.capture_mode) is not str
            or self.event_type != EVENT_TYPE
            or self.capture_mode != CAPTURE_MODE
        ):
            raise ValueError("event type identity is invalid")
        _validate_uuid7_string(self.event_id)
        _require_hash(self.operation_id_fingerprint)
        _canonical_datetime(self.created_at)
        if self.created_at.utcoffset() != timedelta(0):
            raise ValueError("event timestamp must be UTC")
        if (
            type(self.derivation_version) is not str
            or type(self.policy_id) is not str
            or self.derivation_version != DERIVATION_VERSION
            or self.policy_id != POLICY_ID
        ):
            raise ValueError("Stage 6 policy identity is invalid")
        if self.policy_fingerprint != POLICY_FINGERPRINT:
            raise ValueError("Stage 6 policy fingerprint is invalid")
        if type(self.request) is not ProspectiveAuditRequestV1:
            raise ValueError("event request is invalid")
        if type(self.result) is not ProspectiveAuditResultV1:
            raise ValueError("event result is invalid")
        if type(self.source) is not ProspectiveAuditSourceV1:
            raise ValueError("event source is invalid")
        if self.result.kind is ProspectiveAuditResultKind.PREDICTION:
            if self.result.predicted_option_id not in {
                option.id for option in self.request.options
            }:
                raise ValueError("predicted option is not in request")
        elif self.result.predicted_option_id is not None:
            raise ValueError("abstention contains a prediction")

    def as_dict(self) -> dict[str, object]:
        return {
            "contract_version": self.contract_version,
            "version": self.version,
            "event_type": self.event_type,
            "event_id": self.event_id,
            "operation_id_fingerprint": self.operation_id_fingerprint,
            "created_at": _canonical_datetime(self.created_at),
            "capture_mode": self.capture_mode,
            "derivation_version": self.derivation_version,
            "policy_id": self.policy_id,
            "policy_fingerprint": self.policy_fingerprint,
            "request": self.request.as_dict(),
            "result": self.result.as_dict(),
            "source": self.source.as_dict(),
        }


@dataclass(frozen=True, slots=True)
class AuditLogEnvelopeV1:
    generation_id: str
    sequence: int
    event: ProspectiveAuditEventV1
    previous_record_digest: AuditHashV1 | None
    record_digest: AuditHashV1

    def __post_init__(self) -> None:
        _validate_uuid7_string(self.generation_id)
        if (
            type(self.sequence) is not int
            or isinstance(self.sequence, bool)
            or not 1 <= self.sequence <= MAX_UINT64
        ):
            raise ValueError("envelope sequence is invalid")
        if type(self.event) is not ProspectiveAuditEventV1:
            raise ValueError("envelope event is invalid")
        if self.previous_record_digest is not None:
            _require_hash(self.previous_record_digest)
        _require_hash(self.record_digest)

    def unsigned_dict(self) -> dict[str, object]:
        return {
            "generation_id": self.generation_id,
            "sequence": self.sequence,
            "event": self.event.as_dict(),
            "previous_record_digest": self.previous_record_digest,
        }

    def as_dict(self) -> dict[str, object]:
        result = self.unsigned_dict()
        result["record_digest"] = self.record_digest
        return result

    @property
    def expected_record_digest(self) -> AuditHashV1:
        """Recompute the chain digest without trusting the stored digest."""

        return _hash_bytes(canonical_json_bytes(self.unsigned_dict()))


@dataclass(frozen=True, slots=True)
class AuditStoreManifestV1:
    format_version: str
    generation_id: str
    last_sequence: int
    last_record_digest: AuditHashV1 | None

    def __post_init__(self) -> None:
        if type(self.format_version) is not str or self.format_version != STORE_FORMAT_VERSION:
            raise ValueError("manifest format version is invalid")
        _validate_uuid7_string(self.generation_id)
        if (
            type(self.last_sequence) is not int
            or isinstance(self.last_sequence, bool)
            or not 0 <= self.last_sequence <= MAX_UINT64
        ):
            raise ValueError("manifest sequence is invalid")
        if self.last_record_digest is not None:
            _require_hash(self.last_record_digest)
        if (self.last_sequence == 0) != (self.last_record_digest is None):
            raise ValueError("manifest empty state is invalid")

    def as_dict(self) -> dict[str, object]:
        return {
            "format_version": self.format_version,
            "generation_id": self.generation_id,
            "last_sequence": self.last_sequence,
            "last_record_digest": self.last_record_digest,
        }


# A descriptive alias used by callers that refer to the generation metadata.
AuditGenerationV1 = AuditStoreManifestV1


def build_prospective_audit_event(
    request: SimulateMeRequest,
    result: SimulateMeResult,
    operation_id: object,
    *,
    created_at: datetime | None = None,
    event_id: str | None = None,
) -> ProspectiveAuditEventV1:
    """Build one event from a freshly validated Stage 6 terminal result."""

    try:
        validated_request = validate_simulate_me_request(request)
    except Exception as exc:
        raise ProspectiveAuditInvalidRequestError() from exc
    try:
        validate_simulate_me_policy()
        validated_result = validate_simulate_me_result(result, request=validated_request)
    except Exception as exc:
        raise ProspectiveAuditResultInvalidError() from exc
    normalized_operation_id = normalize_operation_id(operation_id)
    normalized_query = normalize_simulate_me_text(validated_request.query, MAX_QUERY_BYTES)
    options = tuple(
        ProspectiveAuditOptionV1(
            id=option.id,
            ordinal=index,
            label=normalize_simulate_me_text(option.label, MAX_LABEL_BYTES),
            label_fingerprint=fingerprint_option_label(option.label),
        )
        for index, option in enumerate(validated_request.options)
    )
    query_fingerprint = fingerprint_query(normalized_query)
    options_fingerprint = fingerprint_json(
        [
            {
                "id": option.id,
                "ordinal": option.ordinal,
                "label_fingerprint": option.label_fingerprint,
            }
            for option in options
        ]
    )
    request_dto = ProspectiveAuditRequestV1(
        query_fingerprint=query_fingerprint,
        options=options,
        options_fingerprint=options_fingerprint,
        request_fingerprint=fingerprint_json(
            {
                "query_fingerprint": query_fingerprint,
                "options_fingerprint": options_fingerprint,
            }
        ),
    )
    result_dto = ProspectiveAuditResultV1(
        kind=ProspectiveAuditResultKind(validated_result.kind.value),
        predicted_option_id=(
            None
            if validated_result.selected_option is None
            else validated_result.selected_option.id
        ),
        abstention_code=(
            None
            if validated_result.abstention_code is None
            else ProspectiveAuditAbstentionCode(validated_result.abstention_code.value)
        ),
    )
    source = ProspectiveAuditSourceV1(
        result_fingerprint=fingerprint_simulate_me_result(validated_result),
        source_refs_fingerprint=fingerprint_source_refs(validated_result),
        evidence_ref_count=len(validated_result.evidence_refs),
        contextual_ref_count=len(validated_result.contextual_evidence_refs),
        temporal_caveat_count=len(validated_result.temporal_caveats),
    )
    if created_at is None:
        timestamp = datetime.now(UTC)
    elif (
        type(created_at) is datetime
        and created_at.tzinfo is not None
        and created_at.utcoffset() is not None
    ):
        timestamp = created_at.astimezone(UTC)
    else:
        raise ProspectiveAuditResultInvalidError()
    return ProspectiveAuditEventV1(
        contract_version=CONTRACT_VERSION,
        version=EVENT_VERSION,
        event_type=EVENT_TYPE,
        event_id=_validate_uuid7_string(event_id or str(uuid.uuid7())),
        operation_id_fingerprint=fingerprint_operation_id(normalized_operation_id),
        created_at=timestamp,
        capture_mode=CAPTURE_MODE,
        derivation_version=DERIVATION_VERSION,
        policy_id=POLICY_ID,
        policy_fingerprint=POLICY_FINGERPRINT,
        request=request_dto,
        result=result_dto,
        source=source,
    )


def validate_prospective_audit_event(event: object) -> ProspectiveAuditEventV1:
    if type(event) is not ProspectiveAuditEventV1:
        raise ProspectiveAuditResultInvalidError()
    try:
        ProspectiveAuditEventV1(
            **{field.name: getattr(event, field.name) for field in fields(event)}
        )
    except (TypeError, ValueError) as exc:
        raise ProspectiveAuditResultInvalidError() from exc
    return event


def serialize_prospective_audit_event(event: ProspectiveAuditEventV1) -> bytes:
    """Return the exact canonical event JSON bytes (without a newline)."""

    return canonical_json_bytes(validate_prospective_audit_event(event).as_dict())


def serialize_audit_envelope(envelope: AuditLogEnvelopeV1) -> bytes:
    """Return the exact canonical envelope JSON bytes (without a newline)."""

    if type(envelope) is not AuditLogEnvelopeV1:
        raise ProspectiveAuditResultInvalidError()
    try:
        AuditLogEnvelopeV1(
            **{field.name: getattr(envelope, field.name) for field in fields(envelope)}
        )
    except (TypeError, ValueError) as exc:
        raise ProspectiveAuditResultInvalidError() from exc
    return canonical_json_bytes(envelope.as_dict())


def compute_record_digest(
    *,
    generation_id: str,
    sequence: int,
    event: ProspectiveAuditEventV1,
    previous_record_digest: AuditHashV1 | None,
) -> AuditHashV1:
    """Compute a record digest from the unsigned envelope fields."""

    unsigned = {
        "generation_id": generation_id,
        "sequence": sequence,
        "event": event.as_dict(),
        "previous_record_digest": previous_record_digest,
    }
    return _hash_bytes(canonical_json_bytes(unsigned))


class _StoreLock(AbstractContextManager["_StoreLock"]):
    """Blocking cross-process lock for one operational store."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._file: Any = None

    def __enter__(self) -> _StoreLock:
        if os.path.lexists(self.path) and self.path.is_symlink():
            raise OSError(errno.ELOOP, "lock path is a symlink")
        flags = os.O_RDWR | os.O_CREAT
        flags |= getattr(os, "O_BINARY", 0)
        if os.name != "nt":
            flags |= getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(self.path, flags, 0o600)
        self._file = os.fdopen(descriptor, "r+b", buffering=0)
        try:
            os.chmod(self.path, 0o600)
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


@dataclass(frozen=True, slots=True)
class _VerifiedSnapshot:
    manifest: AuditStoreManifestV1
    envelopes: tuple[AuditLogEnvelopeV1, ...]


class ProspectiveAuditStore:
    """Owner-only append-only JSONL event store with verified chain state."""

    def __init__(
        self,
        root: Path | os.PathLike[str],
        *,
        vault_root: Path | os.PathLike[str] | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        if not isinstance(root, os.PathLike):
            raise ProspectiveAuditStoreUnavailableError()
        self.root = Path(root)
        self._clock = clock or (lambda: datetime.now(UTC))
        self._validate_root(vault_root)
        try:
            self.root.mkdir(parents=True, exist_ok=True)
            os.chmod(self.root, 0o700)
        except OSError as exc:
            raise ProspectiveAuditStoreUnavailableError() from exc
        try:
            with _StoreLock(self.root / LOCK_FILE_NAME):
                self._initialize_unlocked()
        except ProspectiveAuditError:
            raise
        except (OSError, ValueError, UnicodeError, RecursionError) as exc:
            raise ProspectiveAuditStoreUnavailableError() from exc

    @property
    def events_path(self) -> Path:
        return self.root / EVENTS_FILE_NAME

    @property
    def links_path(self) -> Path:
        return self.root / LINKS_FILE_NAME

    @property
    def manifest_path(self) -> Path:
        return self.root / MANIFEST_FILE_NAME

    @property
    def lock_path(self) -> Path:
        return self.root / LOCK_FILE_NAME

    @property
    def manifest(self) -> AuditStoreManifestV1:
        """Return the currently verified manifest."""

        return self.read_manifest()

    def _validate_root(self, vault_root: Path | os.PathLike[str] | None) -> None:
        if self.root.name in {"", ".", ".."}:
            raise ProspectiveAuditStoreUnavailableError()
        try:
            candidate = self.root.expanduser().resolve(strict=False)
            self._reject_symlink_components(self.root)
            if vault_root is not None:
                if not isinstance(vault_root, os.PathLike):
                    raise ValueError("vault root must be a Path")
                vault = Path(vault_root).expanduser().resolve(strict=True)
                if candidate == vault or candidate.is_relative_to(vault):
                    raise ValueError("store must be outside vault")
            git_root = self._git_root(candidate)
            if git_root is not None and candidate.is_relative_to(git_root):
                raise ValueError("store must be outside repository")
        except (OSError, RuntimeError, ValueError) as exc:
            raise ProspectiveAuditStoreUnavailableError() from exc

    @staticmethod
    def _reject_symlink_components(path: Path) -> None:
        for item in (path, *path.parents):
            if item.is_symlink():
                raise ValueError("store path contains a symlink")

    @staticmethod
    def _git_root(path: Path) -> Path | None:
        current = path
        for parent in (current, *current.parents):
            marker = parent / ".git"
            if marker.is_dir() or marker.is_file():
                return parent
        return None

    def _initialize_unlocked(self) -> None:
        paths = (self.events_path, self.links_path, self.manifest_path)
        present = tuple(path.exists() for path in paths)
        if not any(present):
            generation_id = str(uuid.uuid7())
            self._write_bytes_atomic(self.events_path, b"")
            self._write_bytes_atomic(self.links_path, b"")
            self._write_manifest_atomic(
                AuditStoreManifestV1(STORE_FORMAT_VERSION, generation_id, 0, None)
            )
            return
        if not all(present):
            raise ProspectiveAuditStoreCorruptError()
        self._read_verified_unlocked()

    def _read_manifest_unlocked(self) -> AuditStoreManifestV1:
        try:
            raw = self.manifest_path.read_bytes()
            data = _loads_json(raw)
            if type(data) is not dict or set(data) != {
                "format_version",
                "generation_id",
                "last_sequence",
                "last_record_digest",
            }:
                raise ValueError("manifest shape")
            canonical = canonical_json_bytes(data)
            if canonical != raw:
                raise ValueError("manifest is not canonical")
            return AuditStoreManifestV1(
                format_version=data["format_version"],
                generation_id=data["generation_id"],
                last_sequence=data["last_sequence"],
                last_record_digest=data["last_record_digest"],
            )
        except (OSError, UnicodeError, ValueError, TypeError, RecursionError) as exc:
            raise ProspectiveAuditStoreCorruptError() from exc

    def _read_verified_unlocked(self) -> _VerifiedSnapshot:
        if self.root.is_symlink() or any(
            path.is_symlink() for path in (self.events_path, self.links_path, self.manifest_path)
        ):
            raise ProspectiveAuditStoreCorruptError()
        self._assert_owner_only_unlocked()
        manifest = self._read_manifest_unlocked()
        try:
            raw = self.events_path.read_bytes()
            links = self.links_path.read_bytes()
        except FileNotFoundError as exc:
            raise ProspectiveAuditStoreCorruptError() from exc
        except OSError as exc:
            raise ProspectiveAuditStoreUnavailableError() from exc
        if links:
            # Link stream semantics belong to Stage 9B; an unknown non-empty
            # stream cannot be safely included in a Stage 9A snapshot.
            raise ProspectiveAuditStoreCorruptError()
        if not raw:
            if manifest.last_sequence != 0 or manifest.last_record_digest is not None:
                raise ProspectiveAuditStoreCorruptError()
            return _VerifiedSnapshot(manifest, ())
        if raw.startswith(b"\xef\xbb\xbf") or not raw.endswith(b"\n"):
            raise ProspectiveAuditStoreCorruptError()
        envelopes: list[AuditLogEnvelopeV1] = []
        previous: AuditHashV1 | None = None
        seen_ids: set[str] = set()
        for line in raw.splitlines(keepends=True):
            if not line.endswith(b"\n") or line.endswith(b"\r\n"):
                raise ProspectiveAuditStoreCorruptError()
            payload = line[:-1]
            if not payload or len(payload) > MAX_EVENT_BYTES:
                raise ProspectiveAuditStoreCorruptError()
            try:
                data = _loads_json(payload)
                envelope = _envelope_from_dict(data)
                if canonical_json_bytes(data) != payload:
                    raise ValueError("record is not canonical")
                expected_digest = envelope.expected_record_digest
                if envelope.record_digest != expected_digest:
                    raise ValueError("record digest mismatch")
                if envelope.generation_id != manifest.generation_id:
                    raise ValueError("generation mismatch")
                if envelope.sequence != len(envelopes) + 1:
                    raise ValueError("sequence gap")
                if envelope.previous_record_digest != previous:
                    raise ValueError("chain mismatch")
                if envelope.event.event_id in seen_ids:
                    raise ValueError("duplicate event id")
            except (TypeError, ValueError, UnicodeError, RecursionError) as exc:
                raise ProspectiveAuditStoreCorruptError() from exc
            envelopes.append(envelope)
            seen_ids.add(envelope.event.event_id)
            previous = envelope.record_digest
        if manifest.last_sequence != len(envelopes) or manifest.last_record_digest != previous:
            raise ProspectiveAuditStoreCorruptError()
        return _VerifiedSnapshot(manifest, tuple(envelopes))

    def _assert_owner_only_unlocked(self) -> None:
        if os.name == "nt":
            return
        try:
            paths = (self.root, self.events_path, self.links_path, self.manifest_path)
            if any(stat.S_IMODE(path.stat().st_mode) & 0o077 for path in paths):
                raise ValueError("store permissions are too broad")
        except OSError as exc:
            raise ProspectiveAuditStoreUnavailableError() from exc

    def read_events(self) -> tuple[AuditLogEnvelopeV1, ...]:
        """Return a stable fully verified snapshot of the event stream."""

        try:
            with _StoreLock(self.lock_path):
                return self._read_verified_unlocked().envelopes
        except ProspectiveAuditError:
            raise
        except (OSError, ValueError, UnicodeError, RecursionError) as exc:
            raise ProspectiveAuditStoreUnavailableError() from exc

    read_event_envelopes = read_events
    verify = read_events
    validate = read_events
    check_integrity = read_events
    snapshot = read_events

    def read_manifest(self) -> AuditStoreManifestV1:
        try:
            with _StoreLock(self.lock_path):
                snapshot = self._read_verified_unlocked()
                return snapshot.manifest
        except ProspectiveAuditError:
            raise
        except (OSError, ValueError, UnicodeError, RecursionError) as exc:
            raise ProspectiveAuditStoreUnavailableError() from exc

    def append(self, event: ProspectiveAuditEventV1) -> ProspectiveAuditEventV1:
        """Append one event or return its exact idempotent prior event."""

        try:
            validate_prospective_audit_event(event)
        except ProspectiveAuditError:
            raise
        except Exception as exc:
            raise ProspectiveAuditResultInvalidError() from exc
        try:
            with _StoreLock(self.lock_path):
                snapshot = self._read_verified_unlocked()
                for existing in snapshot.envelopes:
                    if existing.event.operation_id_fingerprint != event.operation_id_fingerprint:
                        continue
                    if _same_operation_content(existing.event, event):
                        return existing.event
                    raise ProspectiveAuditIdempotencyConflictError()
                if any(
                    existing.event.event_id == event.event_id for existing in snapshot.envelopes
                ):
                    raise ProspectiveAuditStoreCorruptError()
                sequence = snapshot.manifest.last_sequence + 1
                if sequence > MAX_UINT64:
                    raise ProspectiveAuditStoreUnavailableError()
                envelope = AuditLogEnvelopeV1(
                    generation_id=snapshot.manifest.generation_id,
                    sequence=sequence,
                    event=event,
                    previous_record_digest=snapshot.manifest.last_record_digest,
                    record_digest=compute_record_digest(
                        generation_id=snapshot.manifest.generation_id,
                        sequence=sequence,
                        event=event,
                        previous_record_digest=snapshot.manifest.last_record_digest,
                    ),
                )
                line = canonical_json_bytes(envelope.as_dict()) + b"\n"
                if len(line) > MAX_EVENT_BYTES:
                    raise ProspectiveAuditStoreUnavailableError()
                self._append_bytes_durable(self.events_path, line)
                self._write_manifest_atomic(
                    AuditStoreManifestV1(
                        STORE_FORMAT_VERSION,
                        snapshot.manifest.generation_id,
                        sequence,
                        envelope.record_digest,
                    )
                )
                verified = self._read_verified_unlocked()
                if not verified.envelopes or verified.envelopes[-1] != envelope:
                    raise ProspectiveAuditStoreUnavailableError()
                return event
        except ProspectiveAuditError:
            raise
        except (OSError, ValueError, UnicodeError, RecursionError) as exc:
            raise ProspectiveAuditStoreUnavailableError() from exc

    append_event = append
    append_record = append

    def append_result(self, result: SimulateMeResult, *args: object, **kwargs: object) -> NoReturn:
        """Reject caller-supplied/replayed Stage 6 results at the store boundary."""

        del result, args, kwargs
        raise ProspectiveAuditStaleOrReplayedError()

    def active_events(self, *, now: datetime | None = None) -> tuple[AuditLogEnvelopeV1, ...]:
        """Return events strictly before their 180-day retention deadline."""

        current = _as_utc(now or self._clock())
        return tuple(
            envelope
            for envelope in self.read_events()
            if current < envelope.event.created_at + RETENTION
        )

    def purge_expired(self, *, now: datetime | None = None) -> int:
        """Rotate to a verified generation containing only active events."""

        current = _as_utc(now or self._clock())
        try:
            with _StoreLock(self.lock_path):
                snapshot = self._read_verified_unlocked()
                kept = tuple(
                    envelope.event
                    for envelope in snapshot.envelopes
                    if current < envelope.event.created_at + RETENTION
                )
                removed = len(snapshot.envelopes) - len(kept)
                if removed == 0:
                    return 0
                self._rotate_generation_unlocked(kept)
                self._read_verified_unlocked()
                return removed
        except ProspectiveAuditError:
            raise
        except (OSError, ValueError, UnicodeError, RecursionError) as exc:
            raise ProspectiveAuditStoreUnavailableError() from exc

    expire = purge_expired
    compact = purge_expired

    def reset(self) -> AuditStoreManifestV1:
        """Owner-controlled full reset to a new empty generation."""

        try:
            with _StoreLock(self.lock_path):
                generation_id = str(uuid.uuid7())
                self._write_bytes_atomic(self.events_path, b"")
                self._write_bytes_atomic(self.links_path, b"")
                self._write_manifest_atomic(
                    AuditStoreManifestV1(STORE_FORMAT_VERSION, generation_id, 0, None)
                )
                return self._read_verified_unlocked().manifest
        except ProspectiveAuditError:
            raise
        except (OSError, ValueError, UnicodeError, RecursionError) as exc:
            raise ProspectiveAuditStoreUnavailableError() from exc

    def _rotate_generation_unlocked(self, events: Sequence[ProspectiveAuditEventV1]) -> None:
        generation_id = str(uuid.uuid7())
        lines: list[bytes] = []
        previous: AuditHashV1 | None = None
        for sequence, event in enumerate(events, start=1):
            envelope = AuditLogEnvelopeV1(
                generation_id,
                sequence,
                event,
                previous,
                compute_record_digest(
                    generation_id=generation_id,
                    sequence=sequence,
                    event=event,
                    previous_record_digest=previous,
                ),
            )
            lines.append(canonical_json_bytes(envelope.as_dict()) + b"\n")
            previous = envelope.record_digest
        self._write_bytes_atomic(self.events_path, b"".join(lines))
        self._write_manifest_atomic(
            AuditStoreManifestV1(STORE_FORMAT_VERSION, generation_id, len(events), previous)
        )

    def _append_bytes_durable(self, path: Path, payload: bytes) -> None:
        flags = os.O_WRONLY | os.O_APPEND | os.O_CREAT | getattr(os, "O_BINARY", 0)
        if os.name != "nt":
            flags |= getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(
            path,
            flags,
            0o600,
        )
        try:
            os.chmod(path, 0o600)
            view = memoryview(payload)
            while view:
                written = os.write(descriptor, view)
                if written <= 0:
                    raise OSError("event append made no progress")
                view = view[written:]
            os.fsync(descriptor)
        finally:
            os.close(descriptor)

    def _write_manifest_atomic(self, manifest: AuditStoreManifestV1) -> None:
        self._write_bytes_atomic(self.manifest_path, canonical_json_bytes(manifest.as_dict()))

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


class SimulateMeExecutor(Protocol):
    def execute(self, request: SimulateMeRequest) -> SimulateMeResult:
        """Execute one fresh Stage 6 operation."""


class CancellationLike(Protocol):
    def is_cancelled(self) -> bool:
        """Return whether the foreground operation was cancelled."""


class BuildProspectiveAudit:
    """Explicit foreground wrapper around one current Stage 6 execution."""

    def __init__(
        self,
        simulate_me: SimulateMeExecutor,
        store: ProspectiveAuditStore,
        *,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._simulate_me = simulate_me
        self._store = store
        self._clock = clock or (lambda: datetime.now(UTC))

    def execute(
        self,
        request: SimulateMeRequest,
        operation_id: object,
        *,
        cancellation: CancellationLike | None = None,
    ) -> SimulateMeResult:
        """Validate, execute Stage 6 once, durably append, then return result."""

        try:
            validated_request = validate_simulate_me_request(request)
            normalize_operation_id(operation_id)
        except Exception as exc:
            raise ProspectiveAuditInvalidRequestError() from exc
        if cancellation is not None and cancellation.is_cancelled():
            raise ProspectiveAuditCancelledError()
        try:
            raw_result = self._simulate_me.execute(validated_request)
        except ProspectiveAuditError:
            raise
        except Exception as exc:
            raise ProspectiveAuditResultInvalidError() from exc
        if cancellation is not None and cancellation.is_cancelled():
            raise ProspectiveAuditCancelledError()
        try:
            result = validate_simulate_me_result(raw_result, request=validated_request)
            validate_simulate_me_policy()
        except Exception as exc:
            raise ProspectiveAuditResultInvalidError() from exc
        if cancellation is not None and cancellation.is_cancelled():
            raise ProspectiveAuditCancelledError()
        event = build_prospective_audit_event(
            validated_request,
            result,
            operation_id,
            created_at=_as_utc(self._clock()),
        )
        self._store.append(event)
        if cancellation is not None and cancellation.is_cancelled():
            # The event is already durably committed.  Do not pretend the
            # cancellation produced a successful new response.
            raise ProspectiveAuditCancelledError()
        return result

    capture = execute


ProspectiveAuditCapture = BuildProspectiveAudit
AuditStore = ProspectiveAuditStore
build_audit_event = build_prospective_audit_event
canonical_audit_json_bytes = canonical_json_bytes
validate_audit_hash = _require_hash

# V1 aliases follow the naming convention used by the other application cores.
ProspectiveAuditOption = ProspectiveAuditOptionV1
ProspectiveAuditRequest = ProspectiveAuditRequestV1
ProspectiveAuditResult = ProspectiveAuditResultV1
ProspectiveAuditSource = ProspectiveAuditSourceV1
ProspectiveAuditEvent = ProspectiveAuditEventV1
ProspectiveAuditStoreV1 = ProspectiveAuditStore
BuildProspectiveAuditV1 = BuildProspectiveAudit
ProspectiveAuditErrorCodeV1 = ProspectiveAuditErrorCode
ProspectiveAuditResultKindV1 = ProspectiveAuditResultKind
ProspectiveAuditAbstentionCodeV1 = ProspectiveAuditAbstentionCode


def _same_operation_content(left: ProspectiveAuditEventV1, right: ProspectiveAuditEventV1) -> bool:
    return (
        left.request.request_fingerprint == right.request.request_fingerprint
        and left.source.result_fingerprint == right.source.result_fingerprint
        and left.source.source_refs_fingerprint == right.source.source_refs_fingerprint
        and left.policy_id == right.policy_id
        and left.policy_fingerprint == right.policy_fingerprint
        and left.derivation_version == right.derivation_version
    )


def _as_utc(value: datetime) -> datetime:
    if type(value) is not datetime or value.tzinfo is None or value.utcoffset() is None:
        raise ProspectiveAuditStoreUnavailableError()
    return value.astimezone(UTC)


def _loads_json(raw: bytes) -> object:
    def reject_duplicates(pairs: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate JSON key")
            result[key] = value
        return result

    def reject_constant(value: str) -> object:
        raise ValueError(f"invalid JSON constant {value}")

    return json.loads(
        raw.decode("utf-8"),
        object_pairs_hook=reject_duplicates,
        parse_constant=reject_constant,
    )


def _envelope_from_dict(data: object) -> AuditLogEnvelopeV1:
    if type(data) is not dict or set(data) != {
        "generation_id",
        "sequence",
        "event",
        "previous_record_digest",
        "record_digest",
    }:
        raise ValueError("envelope shape")
    event_data = data["event"]
    event = _event_from_dict(event_data)
    return AuditLogEnvelopeV1(
        generation_id=data["generation_id"],
        sequence=data["sequence"],
        event=event,
        previous_record_digest=data["previous_record_digest"],
        record_digest=data["record_digest"],
    )


def _event_from_dict(data: object) -> ProspectiveAuditEventV1:
    if type(data) is not dict or set(data) != {
        "contract_version",
        "version",
        "event_type",
        "event_id",
        "operation_id_fingerprint",
        "created_at",
        "capture_mode",
        "derivation_version",
        "policy_id",
        "policy_fingerprint",
        "request",
        "result",
        "source",
    }:
        raise ValueError("event shape")
    request_data = data["request"]
    if type(request_data) is not dict or set(request_data) != {
        "query_fingerprint",
        "options",
        "options_fingerprint",
        "request_fingerprint",
    }:
        raise ValueError("request shape")
    options_data = request_data["options"]
    if type(options_data) is not list:
        raise ValueError("options shape")
    options: list[ProspectiveAuditOptionV1] = []
    for option_data in options_data:
        if type(option_data) is not dict or set(option_data) != {
            "id",
            "ordinal",
            "label",
            "label_fingerprint",
        }:
            raise ValueError("option shape")
        options.append(ProspectiveAuditOptionV1(**option_data))
    request = ProspectiveAuditRequestV1(
        query_fingerprint=request_data["query_fingerprint"],
        options=tuple(options),
        options_fingerprint=request_data["options_fingerprint"],
        request_fingerprint=request_data["request_fingerprint"],
    )
    result_data = data["result"]
    if type(result_data) is not dict or set(result_data) != {
        "kind",
        "predicted_option_id",
        "abstention_code",
    }:
        raise ValueError("result shape")
    result = ProspectiveAuditResultV1(
        kind=ProspectiveAuditResultKind(result_data["kind"]),
        predicted_option_id=result_data["predicted_option_id"],
        abstention_code=(
            None
            if result_data["abstention_code"] is None
            else ProspectiveAuditAbstentionCode(result_data["abstention_code"])
        ),
    )
    source_data = data["source"]
    if type(source_data) is not dict or set(source_data) != {
        "result_fingerprint",
        "source_refs_fingerprint",
        "evidence_ref_count",
        "contextual_ref_count",
        "temporal_caveat_count",
    }:
        raise ValueError("source shape")
    source = ProspectiveAuditSourceV1(**source_data)
    return ProspectiveAuditEventV1(
        contract_version=data["contract_version"],
        version=data["version"],
        event_type=data["event_type"],
        event_id=data["event_id"],
        operation_id_fingerprint=data["operation_id_fingerprint"],
        created_at=_parse_canonical_datetime(data["created_at"]),
        capture_mode=data["capture_mode"],
        derivation_version=data["derivation_version"],
        policy_id=data["policy_id"],
        policy_fingerprint=data["policy_fingerprint"],
        request=request,
        result=result,
        source=source,
    )


__all__ = [
    "CAPTURE_MODE",
    "CONTRACT_VERSION",
    "DERIVATION_VERSION",
    "EVENT_TYPE",
    "EVENT_VERSION",
    "LINKS_FILE_NAME",
    "MANIFEST_FILE_NAME",
    "MAX_LABEL_BYTES",
    "MAX_OPERATION_ID_BYTES",
    "MAX_OPTIONS",
    "MAX_QUERY_BYTES",
    "MAX_RESULT_REFS",
    "MIN_OPTIONS",
    "POLICY_FINGERPRINT",
    "POLICY_ID",
    "PROSPECTIVE_AUDIT_CAPTURE_MODE",
    "PROSPECTIVE_AUDIT_CONTRACT_VERSION",
    "PROSPECTIVE_AUDIT_DERIVATION_VERSION",
    "PROSPECTIVE_AUDIT_EVENT_TYPE",
    "PROSPECTIVE_AUDIT_POLICY_FINGERPRINT",
    "PROSPECTIVE_AUDIT_POLICY_ID",
    "PROSPECTIVE_AUDIT_RETENTION_POLICY",
    "RETENTION",
    "RETENTION_POLICY",
    "STORE_FORMAT_VERSION",
    "AuditGenerationV1",
    "AuditHashV1",
    "AuditLogEnvelopeV1",
    "AuditStore",
    "AuditStoreManifestV1",
    "BuildProspectiveAudit",
    "BuildProspectiveAuditV1",
    "ProspectiveAuditAbstentionCode",
    "ProspectiveAuditAbstentionCodeV1",
    "ProspectiveAuditCancelledError",
    "ProspectiveAuditCapture",
    "ProspectiveAuditError",
    "ProspectiveAuditErrorCode",
    "ProspectiveAuditErrorCodeV1",
    "ProspectiveAuditEvent",
    "ProspectiveAuditEventV1",
    "ProspectiveAuditIdempotencyConflictError",
    "ProspectiveAuditInvalidRequestError",
    "ProspectiveAuditOption",
    "ProspectiveAuditOptionV1",
    "ProspectiveAuditRequest",
    "ProspectiveAuditRequestV1",
    "ProspectiveAuditResult",
    "ProspectiveAuditResultInvalidError",
    "ProspectiveAuditResultKind",
    "ProspectiveAuditResultKindV1",
    "ProspectiveAuditResultV1",
    "ProspectiveAuditSource",
    "ProspectiveAuditSourceV1",
    "ProspectiveAuditStaleOrReplayedError",
    "ProspectiveAuditStore",
    "ProspectiveAuditStoreCorruptError",
    "ProspectiveAuditStoreUnavailableError",
    "ProspectiveAuditStoreV1",
    "build_audit_event",
    "build_prospective_audit_event",
    "canonical_audit_json_bytes",
    "canonical_json",
    "canonical_json_bytes",
    "compute_record_digest",
    "fingerprint_json",
    "fingerprint_operation_id",
    "fingerprint_option_label",
    "fingerprint_query",
    "fingerprint_simulate_me_result",
    "fingerprint_source_refs",
    "fingerprint_text",
    "normalize_operation_id",
    "serialize_audit_envelope",
    "serialize_prospective_audit_event",
    "validate_audit_hash",
    "validate_prospective_audit_event",
]
