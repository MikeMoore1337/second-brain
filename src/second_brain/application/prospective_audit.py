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

from second_brain.application.ports import VaultReader
from second_brain.application.reports import ScanReport, VaultSnapshot
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
from second_brain.application.validation import build_report
from second_brain.domain.models import (
    EvidenceAtPrecision,
    EvidenceKind,
    NoteRecord,
    SelfKind,
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
MAX_JOURNAL_OPTIONS: Final[int] = 20
MAX_JOURNAL_OPTION_BYTES: Final[int] = 16 * 1024
MAX_LINK_BYTES: Final[int] = MAX_EVENT_BYTES

EVENTS_FILE_NAME: Final[str] = "events.jsonl"
LINKS_FILE_NAME: Final[str] = "links.jsonl"
MANIFEST_FILE_NAME: Final[str] = "manifest.json"
LOCK_FILE_NAME: Final[str] = ".store.lock"
LINK_RECORD_TYPE: Final[str] = "link"
LINK_TOMBSTONE_RECORD_TYPE: Final[str] = "tombstone"
LINK_MAPPING_BASIS: Final[str] = "owner-explicit-v1"

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
    LINK_INVALID = "PROSPECTIVE_AUDIT_LINK_INVALID"
    LINK_UNAVAILABLE = "PROSPECTIVE_AUDIT_LINK_UNAVAILABLE"
    CANCELLED = "PROSPECTIVE_AUDIT_CANCELLED"


class ProspectiveAuditLinkReasonCode(StrEnum):
    """Fixed per-link validation reasons used by the internal Stage 9B core."""

    DECISION_TARGET_INVALID = "decision_target_invalid"
    DECISION_IDENTITY_CONFLICT = "decision_identity_conflict"
    DECISION_TIME_INVALID = "decision_time_invalid"
    DECISION_PRECEDES_PREDICTION = "decision_precedes_prediction"
    DECISION_NOTE_CREATED_BEFORE_PREDICTION = "decision_note_created_before_prediction"
    DECISION_RECORD_CHANGED = "decision_record_changed"
    OPTION_MAPPING_INVALID = "option_mapping_invalid"
    CHOSEN_OPTION_UNMAPPED = "chosen_option_unmapped"
    LINK_FINGERPRINT_MISMATCH = "link_fingerprint_mismatch"
    AUDIT_EVENT_MISSING_OR_DELETED = "audit_event_missing_or_deleted"
    DECISION_TARGET_UNAVAILABLE = "decision_target_unavailable"
    CANONICAL_SCAN_UNAVAILABLE = "canonical_scan_unavailable"
    DECISION_TARGET_EXPIRED = "decision_target_expired"
    LINK_OPERATION_CONFLICT = "link_operation_conflict"


class ProspectiveDecisionLinkStateV1(StrEnum):
    """Typed current state of one Stage 9B relation."""

    ACTIVE_UNLINKED = "ACTIVE_UNLINKED"
    LINKED_VALID = "LINKED_VALID"
    LINK_INVALID = "LINK_INVALID"
    LINK_UNAVAILABLE = "LINK_UNAVAILABLE"
    EXPIRED = "EXPIRED"
    TOMBSTONED = "TOMBSTONED"


_ERROR_MESSAGES: Final[dict[ProspectiveAuditErrorCode, str]] = {
    ProspectiveAuditErrorCode.INVALID_REQUEST: "prospective audit request failed validation",
    ProspectiveAuditErrorCode.RESULT_INVALID: "prospective audit result failed validation",
    ProspectiveAuditErrorCode.STALE_OR_REPLAYED: "prospective audit result is stale or replayed",
    ProspectiveAuditErrorCode.IDEMPOTENCY_CONFLICT: (
        "prospective audit operation conflicts with existing record"
    ),
    ProspectiveAuditErrorCode.STORE_UNAVAILABLE: "prospective audit store is unavailable",
    ProspectiveAuditErrorCode.STORE_CORRUPT: "prospective audit store is corrupt",
    ProspectiveAuditErrorCode.LINK_INVALID: "prospective audit link failed validation",
    ProspectiveAuditErrorCode.LINK_UNAVAILABLE: "prospective audit link source is unavailable",
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


class ProspectiveAuditLinkError(ProspectiveAuditError):
    """Fixed safe error for one invalid or unavailable link operation."""

    def __init__(
        self,
        reason: ProspectiveAuditLinkReasonCode | str,
        *,
        unavailable: bool = False,
    ) -> None:
        try:
            normalized = ProspectiveAuditLinkReasonCode(reason)
        except TypeError, ValueError:
            normalized = (
                ProspectiveAuditLinkReasonCode.CANONICAL_SCAN_UNAVAILABLE
                if unavailable
                else ProspectiveAuditLinkReasonCode.DECISION_TARGET_INVALID
            )
        self.reason_code = normalized.value
        self.reason = normalized.value
        super().__init__(
            ProspectiveAuditErrorCode.LINK_UNAVAILABLE
            if unavailable
            else ProspectiveAuditErrorCode.LINK_INVALID
        )

    def as_dict(self) -> dict[str, str]:
        """Return only fixed safe error fields."""

        result = super().as_dict()
        result["reason"] = self.reason_code
        return result


class ProspectiveAuditLinkInvalidError(ProspectiveAuditLinkError):
    """The explicit target, mapping or temporal proof is invalid."""

    def __init__(self, reason: ProspectiveAuditLinkReasonCode | str) -> None:
        super().__init__(reason, unavailable=False)


class ProspectiveAuditLinkUnavailableError(ProspectiveAuditLinkError):
    """The exact audit/Decision Journal source cannot currently be read."""

    def __init__(self, reason: ProspectiveAuditLinkReasonCode | str) -> None:
        super().__init__(reason, unavailable=True)


class ProspectiveAuditLinkConflictError(ProspectiveAuditLinkInvalidError):
    """An event already has a different active owner-reviewed relation."""

    def __init__(self) -> None:
        super().__init__(ProspectiveAuditLinkReasonCode.LINK_OPERATION_CONFLICT)


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


def _normalize_decision_option(value: object) -> str:
    """Normalize one current Journal option using Stage 2 comparison rules."""

    if type(value) is not str:
        raise ValueError("decision option must be text")
    try:
        normalized = unicodedata.normalize("NFC", value)
        normalized = " ".join(normalized.split())
        size = len(normalized.encode("utf-8"))
    except (UnicodeError, ValueError) as exc:
        raise ValueError("decision option is invalid") from exc
    if not normalized or size > MAX_JOURNAL_OPTION_BYTES:
        raise ValueError("decision option is invalid")
    return normalized


def fingerprint_decision_option(value: object) -> AuditHashV1:
    """Fingerprint a normalized current Decision Journal option value."""

    return _hash_bytes(_normalize_decision_option(value).encode("utf-8"))


def fingerprint_decision_record(
    decision_id: object,
    evidence_at: object,
    available_options: Sequence[object],
    chosen_option: object,
) -> AuditHashV1:
    """Fingerprint the exact current Journal identity/time/choice snapshot."""

    decision_id_text = _validate_uuid7_string(
        str(decision_id) if isinstance(decision_id, UUID) else decision_id
    )
    if type(evidence_at) is not datetime:
        raise ValueError("decision time is invalid")
    evidence_text = _canonical_datetime(evidence_at)
    if not isinstance(available_options, (tuple, list)):
        raise ValueError("decision options are invalid")
    normalized_options = tuple(_normalize_decision_option(item) for item in available_options)
    if not 2 <= len(normalized_options) <= MAX_JOURNAL_OPTIONS:
        raise ValueError("decision options are invalid")
    if len(set(normalized_options)) != len(normalized_options):
        raise ValueError("decision options are duplicated")
    normalized_chosen = _normalize_decision_option(chosen_option)
    if normalized_options.count(normalized_chosen) != 1:
        raise ValueError("chosen decision option is invalid")
    return fingerprint_json(
        {
            "decision_id": decision_id_text,
            "evidence_at": evidence_text,
            "available_options": list(normalized_options),
            "chosen_option": normalized_chosen,
        }
    )


# Descriptive aliases keep the exact target fingerprint boundary discoverable.
fingerprint_decision_journal_option = fingerprint_decision_option
fingerprint_decision_journal_record = fingerprint_decision_record


@dataclass(frozen=True, slots=True)
class ProspectiveOptionMappingV1:
    """One owner-supplied edge between two distinct option namespaces."""

    audit_option_id: str
    decision_option_index: int
    decision_option_fingerprint: AuditHashV1

    def __post_init__(self) -> None:
        if (
            type(self.audit_option_id) is not str
            or _ID_PATTERN.fullmatch(self.audit_option_id) is None
        ):
            raise ValueError("audit option id is invalid")
        if (
            type(self.decision_option_index) is not int
            or isinstance(self.decision_option_index, bool)
            or not 0 <= self.decision_option_index < MAX_JOURNAL_OPTIONS
        ):
            raise ValueError("decision option index is invalid")
        _require_hash(self.decision_option_fingerprint)

    def as_dict(self) -> dict[str, object]:
        return {
            "audit_option_id": self.audit_option_id,
            "decision_option_index": self.decision_option_index,
            "decision_option_fingerprint": self.decision_option_fingerprint,
        }


@dataclass(frozen=True, slots=True)
class ProspectiveDecisionLinkV1:
    """Exact immutable explicit relation persisted by Stage 9B."""

    version: str
    link_id: str
    audit_event_id: str
    decision_id: str
    linked_at: datetime
    decision_record_fingerprint: AuditHashV1
    actual_chosen_option_index: int
    mapping: tuple[ProspectiveOptionMappingV1, ...]
    mapping_basis: str

    def __post_init__(self) -> None:
        if type(self.version) is not str or self.version != EVENT_VERSION:
            raise ValueError("link version is invalid")
        _validate_uuid7_string(self.link_id)
        _validate_uuid7_string(self.audit_event_id)
        _validate_uuid7_string(self.decision_id)
        _canonical_datetime(self.linked_at)
        if self.linked_at.utcoffset() != timedelta(0):
            raise ValueError("link timestamp must be UTC")
        _require_hash(self.decision_record_fingerprint)
        if (
            type(self.actual_chosen_option_index) is not int
            or isinstance(self.actual_chosen_option_index, bool)
            or not 0 <= self.actual_chosen_option_index < MAX_JOURNAL_OPTIONS
        ):
            raise ValueError("actual option index is invalid")
        if type(self.mapping) is not tuple or len(self.mapping) > MAX_JOURNAL_OPTIONS:
            raise ValueError("link mapping is invalid")
        audit_ids: set[str] = set()
        decision_indexes: set[int] = set()
        for item in self.mapping:
            if type(item) is not ProspectiveOptionMappingV1:
                raise ValueError("link mapping is invalid")
            if item.audit_option_id in audit_ids:
                raise ValueError("link mapping contains duplicate audit option ids")
            if item.decision_option_index in decision_indexes:
                raise ValueError("link mapping contains duplicate decision indexes")
            audit_ids.add(item.audit_option_id)
            decision_indexes.add(item.decision_option_index)
        if type(self.mapping_basis) is not str or self.mapping_basis != LINK_MAPPING_BASIS:
            raise ValueError("link mapping basis is invalid")

    def as_dict(self) -> dict[str, object]:
        return {
            "version": self.version,
            "link_id": self.link_id,
            "audit_event_id": self.audit_event_id,
            "decision_id": self.decision_id,
            "linked_at": _canonical_datetime(self.linked_at),
            "decision_record_fingerprint": self.decision_record_fingerprint,
            "actual_chosen_option_index": self.actual_chosen_option_index,
            "mapping": [item.as_dict() for item in self.mapping],
            "mapping_basis": self.mapping_basis,
        }


class ProspectiveDecisionLinkTombstoneReasonV1(StrEnum):
    """Closed reasons for append-only correction/invalidation records."""

    OWNER_INVALIDATE = "owner_invalidate"
    OWNER_SUPERSEDE = "owner_supersede"
    OWNER_DELETE = "owner_delete"


@dataclass(frozen=True, slots=True)
class ProspectiveDecisionLinkTombstoneV1:
    """Fixed-shape operational tombstone; it never edits a prior link."""

    version: str
    tombstone_id: str
    audit_event_id: str
    supersedes_link_id: str
    tombstoned_at: datetime
    reason: ProspectiveDecisionLinkTombstoneReasonV1

    def __post_init__(self) -> None:
        if type(self.version) is not str or self.version != EVENT_VERSION:
            raise ValueError("tombstone version is invalid")
        _validate_uuid7_string(self.tombstone_id)
        _validate_uuid7_string(self.audit_event_id)
        _validate_uuid7_string(self.supersedes_link_id)
        _canonical_datetime(self.tombstoned_at)
        if self.tombstoned_at.utcoffset() != timedelta(0):
            raise ValueError("tombstone timestamp must be UTC")
        if type(self.reason) is not ProspectiveDecisionLinkTombstoneReasonV1:
            raise ValueError("tombstone reason is invalid")

    def as_dict(self) -> dict[str, object]:
        return {
            "version": self.version,
            "tombstone_id": self.tombstone_id,
            "audit_event_id": self.audit_event_id,
            "supersedes_link_id": self.supersedes_link_id,
            "tombstoned_at": _canonical_datetime(self.tombstoned_at),
            "reason": self.reason.value,
        }


@dataclass(frozen=True, slots=True)
class ProspectiveLinkLogEnvelopeV1:
    """Integrity envelope for a link or fixed-shape tombstone record."""

    generation_id: str
    sequence: int
    record: ProspectiveDecisionLinkV1 | ProspectiveDecisionLinkTombstoneV1
    previous_record_digest: AuditHashV1 | None
    record_digest: AuditHashV1

    def __post_init__(self) -> None:
        _validate_uuid7_string(self.generation_id)
        if (
            type(self.sequence) is not int
            or isinstance(self.sequence, bool)
            or not 1 <= self.sequence <= MAX_UINT64
        ):
            raise ValueError("link sequence is invalid")
        if type(self.record) not in {
            ProspectiveDecisionLinkV1,
            ProspectiveDecisionLinkTombstoneV1,
        }:
            raise ValueError("link record is invalid")
        if self.previous_record_digest is not None:
            _require_hash(self.previous_record_digest)
        _require_hash(self.record_digest)

    @property
    def record_type(self) -> str:
        return (
            LINK_RECORD_TYPE
            if type(self.record) is ProspectiveDecisionLinkV1
            else LINK_TOMBSTONE_RECORD_TYPE
        )

    @property
    def link(self) -> ProspectiveDecisionLinkV1 | None:
        return self.record if type(self.record) is ProspectiveDecisionLinkV1 else None

    @property
    def tombstone(self) -> ProspectiveDecisionLinkTombstoneV1 | None:
        return self.record if type(self.record) is ProspectiveDecisionLinkTombstoneV1 else None

    def unsigned_dict(self) -> dict[str, object]:
        return {
            "generation_id": self.generation_id,
            "sequence": self.sequence,
            "record_type": self.record_type,
            "record": self.record.as_dict(),
            "previous_record_digest": self.previous_record_digest,
        }

    def as_dict(self) -> dict[str, object]:
        result = self.unsigned_dict()
        result["record_digest"] = self.record_digest
        return result

    @property
    def expected_record_digest(self) -> AuditHashV1:
        return _hash_bytes(canonical_json_bytes(self.unsigned_dict()))


# Names used by callers that describe the same envelope as an audit record.
AuditLinkLogEnvelopeV1 = ProspectiveLinkLogEnvelopeV1
ProspectiveDecisionLinkEnvelopeV1 = ProspectiveLinkLogEnvelopeV1


@dataclass(frozen=True, slots=True)
class DecisionJournalTargetV1:
    """Minimal in-memory projection of one current canonical Journal target."""

    decision_id: str
    evidence_at: datetime
    evidence_at_precision: EvidenceAtPrecision
    created: datetime
    available_options: tuple[str, ...]
    chosen_option: str
    chosen_option_index: int
    option_fingerprints: tuple[AuditHashV1, ...]
    decision_record_fingerprint: AuditHashV1

    def __post_init__(self) -> None:
        _validate_uuid7_string(self.decision_id)
        if type(self.evidence_at_precision) is not EvidenceAtPrecision:
            raise ValueError("decision precision is invalid")
        if self.evidence_at_precision is not EvidenceAtPrecision.EXACT:
            raise ValueError("decision precision must be exact")
        _canonical_datetime(self.evidence_at)
        _canonical_datetime(self.created)
        if (
            type(self.available_options) is not tuple
            or not 2 <= len(self.available_options) <= MAX_JOURNAL_OPTIONS
        ):
            raise ValueError("decision options are invalid")
        normalized_options = tuple(
            _normalize_decision_option(option) for option in self.available_options
        )
        if normalized_options != self.available_options:
            raise ValueError("decision options must be normalized")
        if len(set(normalized_options)) != len(normalized_options):
            raise ValueError("decision options are duplicated")
        normalized_chosen = _normalize_decision_option(self.chosen_option)
        if normalized_chosen != self.chosen_option:
            raise ValueError("chosen option must be normalized")
        if normalized_options.count(normalized_chosen) != 1:
            raise ValueError("chosen option is invalid")
        if (
            type(self.chosen_option_index) is not int
            or isinstance(self.chosen_option_index, bool)
            or self.chosen_option_index != normalized_options.index(normalized_chosen)
        ):
            raise ValueError("chosen option index is invalid")
        if type(self.option_fingerprints) is not tuple or len(self.option_fingerprints) != len(
            normalized_options
        ):
            raise ValueError("decision option fingerprints are invalid")
        for option, fingerprint in zip(normalized_options, self.option_fingerprints, strict=True):
            if fingerprint != fingerprint_decision_option(option):
                raise ValueError("decision option fingerprint is invalid")
        expected = fingerprint_decision_record(
            self.decision_id,
            self.evidence_at,
            normalized_options,
            normalized_chosen,
        )
        if self.decision_record_fingerprint != expected:
            raise ValueError("decision record fingerprint is invalid")


@dataclass(frozen=True, slots=True)
class ProspectiveDecisionLinkVerificationV1:
    """Typed internal state returned by current-link revalidation."""

    state: ProspectiveDecisionLinkStateV1
    link: ProspectiveDecisionLinkV1 | None = None
    reason_code: str | None = None

    def __post_init__(self) -> None:
        if type(self.state) is not ProspectiveDecisionLinkStateV1:
            raise ValueError("link state is invalid")
        if self.link is not None and type(self.link) is not ProspectiveDecisionLinkV1:
            raise ValueError("verification link is invalid")
        if self.reason_code is not None:
            ProspectiveAuditLinkReasonCode(self.reason_code)
        if self.state is ProspectiveDecisionLinkStateV1.LINKED_VALID and self.link is None:
            raise ValueError("valid state requires a link")
        if self.state is not ProspectiveDecisionLinkStateV1.LINKED_VALID and self.link is not None:
            raise ValueError("non-valid state cannot expose a link")

    @property
    def code(self) -> str | None:
        return self.reason_code

    def as_dict(self) -> dict[str, object]:
        return {
            "state": self.state.value,
            "link": None if self.link is None else self.link.as_dict(),
            "reason_code": self.reason_code,
        }


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


def compute_link_record_digest(
    *,
    generation_id: str,
    sequence: int,
    record: ProspectiveDecisionLinkV1 | ProspectiveDecisionLinkTombstoneV1,
    previous_record_digest: AuditHashV1 | None,
) -> AuditHashV1:
    """Compute one link/tombstone envelope digest without trusting its digest."""

    record_type = (
        LINK_RECORD_TYPE
        if type(record) is ProspectiveDecisionLinkV1
        else LINK_TOMBSTONE_RECORD_TYPE
    )
    unsigned = {
        "generation_id": generation_id,
        "sequence": sequence,
        "record_type": record_type,
        "record": record.as_dict(),
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
    link_envelopes: tuple[ProspectiveLinkLogEnvelopeV1, ...] = ()


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
            raw_links = self.links_path.read_bytes()
        except FileNotFoundError as exc:
            raise ProspectiveAuditStoreCorruptError() from exc
        except OSError as exc:
            raise ProspectiveAuditStoreUnavailableError() from exc
        if not raw and (manifest.last_sequence != 0 or manifest.last_record_digest is not None):
            raise ProspectiveAuditStoreCorruptError()
        envelopes = self._read_event_stream_unlocked(raw, manifest)
        link_envelopes = self._read_link_stream_unlocked(raw_links, manifest, envelopes)
        return _VerifiedSnapshot(manifest, envelopes, link_envelopes)

    @staticmethod
    def _read_event_stream_unlocked(
        raw: bytes,
        manifest: AuditStoreManifestV1,
    ) -> tuple[AuditLogEnvelopeV1, ...]:
        if not raw:
            return ()
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
        return tuple(envelopes)

    @staticmethod
    def _read_link_stream_unlocked(
        raw: bytes,
        manifest: AuditStoreManifestV1,
        event_envelopes: tuple[AuditLogEnvelopeV1, ...],
    ) -> tuple[ProspectiveLinkLogEnvelopeV1, ...]:
        if not raw:
            return ()
        if raw.startswith(b"\xef\xbb\xbf") or not raw.endswith(b"\n"):
            raise ProspectiveAuditStoreCorruptError()
        event_sequences = {
            envelope.event.event_id: envelope.sequence for envelope in event_envelopes
        }
        records: list[ProspectiveLinkLogEnvelopeV1] = []
        previous: AuditHashV1 | None = None
        previous_sequence = 0
        seen_record_ids: set[str] = set()
        links_by_id: dict[str, ProspectiveDecisionLinkV1] = {}
        active_by_event: dict[str, ProspectiveDecisionLinkV1] = {}
        for line in raw.splitlines(keepends=True):
            if not line.endswith(b"\n") or line.endswith(b"\r\n"):
                raise ProspectiveAuditStoreCorruptError()
            payload = line[:-1]
            if not payload or len(payload) > MAX_LINK_BYTES:
                raise ProspectiveAuditStoreCorruptError()
            try:
                data = _loads_json(payload)
                envelope = _link_envelope_from_dict(data)
                if canonical_json_bytes(data) != payload:
                    raise ValueError("link record is not canonical")
                if envelope.expected_record_digest != envelope.record_digest:
                    raise ValueError("link record digest mismatch")
                if envelope.generation_id != manifest.generation_id:
                    raise ValueError("link generation mismatch")
                if envelope.sequence <= previous_sequence:
                    raise ValueError("link sequence is not monotonic")
                if envelope.previous_record_digest != previous:
                    raise ValueError("link chain mismatch")
                if envelope.record_type == LINK_RECORD_TYPE:
                    assert envelope.link is not None
                    record_id = envelope.link.link_id
                    event_sequence = event_sequences.get(envelope.link.audit_event_id)
                    if event_sequence is None or not event_sequence < envelope.sequence:
                        raise ValueError("link event ordering is invalid")
                    if record_id in seen_record_ids:
                        raise ValueError("duplicate link id")
                    if envelope.link.audit_event_id in active_by_event:
                        raise ValueError("multiple active links for event")
                    links_by_id[record_id] = envelope.link
                    active_by_event[envelope.link.audit_event_id] = envelope.link
                else:
                    assert envelope.tombstone is not None
                    tombstone = envelope.tombstone
                    event_sequence = event_sequences.get(tombstone.audit_event_id)
                    if event_sequence is None or not event_sequence < envelope.sequence:
                        raise ValueError("tombstone event ordering is invalid")
                    record_id = tombstone.tombstone_id
                    if record_id in seen_record_ids:
                        raise ValueError("duplicate tombstone id")
                    target = links_by_id.get(tombstone.supersedes_link_id)
                    if target is None or target.audit_event_id != tombstone.audit_event_id:
                        raise ValueError("tombstone target is invalid")
                    if active_by_event.get(tombstone.audit_event_id) != target:
                        raise ValueError("tombstone target is not active")
                    del active_by_event[tombstone.audit_event_id]
                seen_record_ids.add(record_id)
            except (TypeError, ValueError, UnicodeError, RecursionError) as exc:
                raise ProspectiveAuditStoreCorruptError() from exc
            records.append(envelope)
            previous = envelope.record_digest
            previous_sequence = envelope.sequence
        return tuple(records)

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

    def read_link_envelopes(self) -> tuple[ProspectiveLinkLogEnvelopeV1, ...]:
        """Return a stable fully verified snapshot of the link stream."""

        try:
            with _StoreLock(self.lock_path):
                return self._read_verified_unlocked().link_envelopes
        except ProspectiveAuditError:
            raise
        except (OSError, ValueError, UnicodeError, RecursionError) as exc:
            raise ProspectiveAuditStoreUnavailableError() from exc

    read_links = read_link_envelopes
    verify_links = read_link_envelopes
    read_link_records = read_link_envelopes

    def read_verified_snapshot(self) -> _VerifiedSnapshot:
        """Return one verified events+links snapshot under the store lock."""

        try:
            with _StoreLock(self.lock_path):
                return self._read_verified_unlocked()
        except ProspectiveAuditError:
            raise
        except (OSError, ValueError, UnicodeError, RecursionError) as exc:
            raise ProspectiveAuditStoreUnavailableError() from exc

    # Stage 9C must not compose two independently locked reads: both streams
    # have to come from the same verified generation snapshot.
    read_stable_snapshot = read_verified_snapshot
    verified_snapshot = read_verified_snapshot

    def read_event(self, event_id: str) -> AuditLogEnvelopeV1 | None:
        """Read one verified event by its exact immutable UUID."""

        try:
            _validate_uuid7_string(event_id)
        except ValueError as exc:
            raise ProspectiveAuditLinkInvalidError(
                ProspectiveAuditLinkReasonCode.DECISION_TARGET_INVALID
            ) from exc
        try:
            with _StoreLock(self.lock_path):
                snapshot = self._read_verified_unlocked()
                return next(
                    (
                        envelope
                        for envelope in snapshot.envelopes
                        if envelope.event.event_id == event_id
                    ),
                    None,
                )
        except ProspectiveAuditError:
            raise
        except (OSError, ValueError, UnicodeError, RecursionError) as exc:
            raise ProspectiveAuditStoreUnavailableError() from exc

    get_event = read_event
    read_event_by_id = read_event

    def current_link_state(self, event_id: str) -> ProspectiveDecisionLinkVerificationV1:
        """Return the verified operational state without rereading the vault."""

        try:
            _validate_uuid7_string(event_id)
        except ValueError:
            return ProspectiveDecisionLinkVerificationV1(
                ProspectiveDecisionLinkStateV1.LINK_UNAVAILABLE,
                reason_code=ProspectiveAuditLinkReasonCode.AUDIT_EVENT_MISSING_OR_DELETED.value,
            )
        try:
            with _StoreLock(self.lock_path):
                snapshot = self._read_verified_unlocked()
                event = next(
                    (
                        envelope
                        for envelope in snapshot.envelopes
                        if envelope.event.event_id == event_id
                    ),
                    None,
                )
                if event is None:
                    return ProspectiveDecisionLinkVerificationV1(
                        ProspectiveDecisionLinkStateV1.LINK_UNAVAILABLE,
                        reason_code=(
                            ProspectiveAuditLinkReasonCode.AUDIT_EVENT_MISSING_OR_DELETED.value
                        ),
                    )
                now = _as_utc(self._clock())
                if now >= event.event.created_at + RETENTION:
                    return ProspectiveDecisionLinkVerificationV1(
                        ProspectiveDecisionLinkStateV1.EXPIRED,
                        reason_code=ProspectiveAuditLinkReasonCode.DECISION_TARGET_EXPIRED.value,
                    )
                active, tombstoned = _current_links_for_event(snapshot.link_envelopes, event_id)
                if active is not None:
                    return ProspectiveDecisionLinkVerificationV1(
                        ProspectiveDecisionLinkStateV1.LINKED_VALID,
                        link=active,
                    )
                if tombstoned:
                    return ProspectiveDecisionLinkVerificationV1(
                        ProspectiveDecisionLinkStateV1.TOMBSTONED
                    )
                return ProspectiveDecisionLinkVerificationV1(
                    ProspectiveDecisionLinkStateV1.ACTIVE_UNLINKED
                )
        except ProspectiveAuditError:
            raise
        except (OSError, ValueError, UnicodeError, RecursionError) as exc:
            raise ProspectiveAuditStoreUnavailableError() from exc

    get_current_link_state = current_link_state

    def append_link(
        self,
        *,
        audit_event_id: str,
        decision_id: str,
        decision_record_fingerprint: AuditHashV1,
        actual_chosen_option_index: int,
        mapping: tuple[ProspectiveOptionMappingV1, ...],
        decision_evidence_at: datetime,
        decision_created: datetime,
    ) -> ProspectiveDecisionLinkV1:
        """Append one explicit link under the common Stage 9 store lock."""

        return self._append_link_operation(
            audit_event_id=audit_event_id,
            decision_id=decision_id,
            decision_record_fingerprint=decision_record_fingerprint,
            actual_chosen_option_index=actual_chosen_option_index,
            mapping=mapping,
            decision_evidence_at=decision_evidence_at,
            decision_created=decision_created,
            expected_link_id=None,
            supersede=False,
        )

    append_decision_link = append_link

    def supersede_link(
        self,
        *,
        audit_event_id: str,
        expected_link_id: str,
        decision_id: str,
        decision_record_fingerprint: AuditHashV1,
        actual_chosen_option_index: int,
        mapping: tuple[ProspectiveOptionMappingV1, ...],
        decision_evidence_at: datetime,
        decision_created: datetime,
    ) -> ProspectiveDecisionLinkV1:
        """Append a tombstone and a new link atomically as one correction."""

        return self._append_link_operation(
            audit_event_id=audit_event_id,
            decision_id=decision_id,
            decision_record_fingerprint=decision_record_fingerprint,
            actual_chosen_option_index=actual_chosen_option_index,
            mapping=mapping,
            decision_evidence_at=decision_evidence_at,
            decision_created=decision_created,
            expected_link_id=expected_link_id,
            supersede=True,
        )

    correct_link = supersede_link

    def tombstone_link(
        self,
        *,
        audit_event_id: str,
        expected_link_id: str,
        reason: ProspectiveDecisionLinkTombstoneReasonV1 = (
            ProspectiveDecisionLinkTombstoneReasonV1.OWNER_INVALIDATE
        ),
    ) -> ProspectiveDecisionLinkTombstoneV1:
        """Append an explicit owner invalidation without editing history."""

        try:
            _validate_uuid7_string(audit_event_id)
            _validate_uuid7_string(expected_link_id)
            if type(reason) is not ProspectiveDecisionLinkTombstoneReasonV1:
                raise ValueError("tombstone reason is invalid")
        except ValueError as exc:
            raise ProspectiveAuditLinkInvalidError(
                ProspectiveAuditLinkReasonCode.LINK_OPERATION_CONFLICT
            ) from exc
        try:
            with _StoreLock(self.lock_path):
                snapshot = self._read_verified_unlocked()
                event = _event_from_snapshot(snapshot, audit_event_id)
                if event is None:
                    raise ProspectiveAuditLinkUnavailableError(
                        ProspectiveAuditLinkReasonCode.AUDIT_EVENT_MISSING_OR_DELETED
                    )
                active, _ = _current_links_for_event(snapshot.link_envelopes, audit_event_id)
                if active is None or active.link_id != expected_link_id:
                    raise ProspectiveAuditLinkConflictError()
                tombstoned_at = _as_utc(self._clock())
                sequence = _next_link_sequence(snapshot)
                if event.sequence >= sequence:
                    raise ProspectiveAuditLinkInvalidError(
                        ProspectiveAuditLinkReasonCode.LINK_FINGERPRINT_MISMATCH
                    )
                tombstone = ProspectiveDecisionLinkTombstoneV1(
                    version=EVENT_VERSION,
                    tombstone_id=str(uuid.uuid7()),
                    audit_event_id=audit_event_id,
                    supersedes_link_id=expected_link_id,
                    tombstoned_at=tombstoned_at,
                    reason=reason,
                )
                envelope = _build_link_envelope(
                    generation_id=snapshot.manifest.generation_id,
                    sequence=sequence,
                    record=tombstone,
                    previous_record_digest=_last_link_digest(snapshot.link_envelopes),
                )
                self._append_link_envelopes_durable((envelope,))
                verified = self._read_verified_unlocked()
                if not verified.link_envelopes or verified.link_envelopes[-1] != envelope:
                    raise ProspectiveAuditStoreUnavailableError()
                return tombstone
        except ProspectiveAuditError:
            raise
        except (OSError, ValueError, UnicodeError, RecursionError) as exc:
            raise ProspectiveAuditStoreUnavailableError() from exc

    invalidate_link = tombstone_link

    def _append_link_operation(
        self,
        *,
        audit_event_id: str,
        decision_id: str,
        decision_record_fingerprint: AuditHashV1,
        actual_chosen_option_index: int,
        mapping: tuple[ProspectiveOptionMappingV1, ...],
        decision_evidence_at: datetime,
        decision_created: datetime,
        expected_link_id: str | None,
        supersede: bool,
        linked_at_override: datetime | None = None,
    ) -> ProspectiveDecisionLinkV1:
        """Validate and durably append one link operation under one lock."""

        try:
            _validate_uuid7_string(audit_event_id)
            _validate_uuid7_string(decision_id)
            _require_hash(decision_record_fingerprint)
            _validate_link_mapping_shape(mapping)
            if (
                type(actual_chosen_option_index) is not int
                or isinstance(actual_chosen_option_index, bool)
                or not 0 <= actual_chosen_option_index < MAX_JOURNAL_OPTIONS
            ):
                raise ValueError("actual option index is invalid")
            _canonical_datetime(decision_evidence_at)
            _canonical_datetime(decision_created)
            if expected_link_id is not None:
                _validate_uuid7_string(expected_link_id)
        except ProspectiveAuditLinkError:
            raise
        except (TypeError, ValueError) as exc:
            raise ProspectiveAuditLinkInvalidError(
                ProspectiveAuditLinkReasonCode.OPTION_MAPPING_INVALID
            ) from exc

        try:
            with _StoreLock(self.lock_path):
                snapshot = self._read_verified_unlocked()
                event = _event_from_snapshot(snapshot, audit_event_id)
                if event is None:
                    raise ProspectiveAuditLinkUnavailableError(
                        ProspectiveAuditLinkReasonCode.AUDIT_EVENT_MISSING_OR_DELETED
                    )
                linked_at = _as_utc(linked_at_override or self._clock())
                if linked_at >= event.event.created_at + RETENTION:
                    raise ProspectiveAuditLinkUnavailableError(
                        ProspectiveAuditLinkReasonCode.DECISION_TARGET_EXPIRED
                    )
                active, _ = _current_links_for_event(snapshot.link_envelopes, audit_event_id)
                if not supersede and active is not None:
                    if _same_link_intent(
                        active,
                        audit_event_id=audit_event_id,
                        decision_id=decision_id,
                        decision_record_fingerprint=decision_record_fingerprint,
                        actual_chosen_option_index=actual_chosen_option_index,
                        mapping=mapping,
                    ):
                        return active
                    raise ProspectiveAuditLinkConflictError()
                if supersede:
                    if active is None or expected_link_id != active.link_id:
                        raise ProspectiveAuditLinkConflictError()
                    if _same_link_intent(
                        active,
                        audit_event_id=audit_event_id,
                        decision_id=decision_id,
                        decision_record_fingerprint=decision_record_fingerprint,
                        actual_chosen_option_index=actual_chosen_option_index,
                        mapping=mapping,
                    ):
                        raise ProspectiveAuditLinkConflictError()
                sequence = _next_link_sequence(snapshot)
                link_sequence = sequence + 1 if supersede else sequence
                if link_sequence > MAX_UINT64 or event.sequence >= link_sequence:
                    raise ProspectiveAuditLinkInvalidError(
                        ProspectiveAuditLinkReasonCode.LINK_FINGERPRINT_MISMATCH
                    )
                _validate_link_temporal_order(
                    event.event.created_at,
                    decision_evidence_at,
                    decision_created,
                    linked_at,
                )
                link = ProspectiveDecisionLinkV1(
                    version=EVENT_VERSION,
                    link_id=str(uuid.uuid7()),
                    audit_event_id=audit_event_id,
                    decision_id=decision_id,
                    linked_at=linked_at,
                    decision_record_fingerprint=decision_record_fingerprint,
                    actual_chosen_option_index=actual_chosen_option_index,
                    mapping=mapping,
                    mapping_basis=LINK_MAPPING_BASIS,
                )
                records: list[ProspectiveLinkLogEnvelopeV1] = []
                previous_digest = _last_link_digest(snapshot.link_envelopes)
                if supersede:
                    assert active is not None
                    tombstone = ProspectiveDecisionLinkTombstoneV1(
                        version=EVENT_VERSION,
                        tombstone_id=str(uuid.uuid7()),
                        audit_event_id=audit_event_id,
                        supersedes_link_id=active.link_id,
                        tombstoned_at=linked_at,
                        reason=ProspectiveDecisionLinkTombstoneReasonV1.OWNER_SUPERSEDE,
                    )
                    tombstone_envelope = _build_link_envelope(
                        generation_id=snapshot.manifest.generation_id,
                        sequence=sequence,
                        record=tombstone,
                        previous_record_digest=previous_digest,
                    )
                    records.append(tombstone_envelope)
                    previous_digest = tombstone_envelope.record_digest
                link_envelope = _build_link_envelope(
                    generation_id=snapshot.manifest.generation_id,
                    sequence=link_sequence,
                    record=link,
                    previous_record_digest=previous_digest,
                )
                records.append(link_envelope)
                self._append_link_envelopes_durable(tuple(records))
                verified = self._read_verified_unlocked()
                if not verified.link_envelopes or verified.link_envelopes[-1] != link_envelope:
                    raise ProspectiveAuditStoreUnavailableError()
                return link
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
                kept_ids = {event.event_id for event in kept}
                kept_links = tuple(
                    envelope
                    for envelope in snapshot.link_envelopes
                    if (envelope.link is not None and envelope.link.audit_event_id in kept_ids)
                    or (
                        envelope.tombstone is not None
                        and envelope.tombstone.audit_event_id in kept_ids
                    )
                )
                self._rotate_generation_unlocked(kept, kept_links)
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

    def _rotate_generation_unlocked(
        self,
        events: Sequence[ProspectiveAuditEventV1],
        links: Sequence[ProspectiveLinkLogEnvelopeV1] = (),
    ) -> None:
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
        link_lines: list[bytes] = []
        link_previous: AuditHashV1 | None = None
        event_count = len(events)
        for offset, old_envelope in enumerate(links, start=1):
            link_sequence = event_count + offset
            rebuilt = _build_link_envelope(
                generation_id=generation_id,
                sequence=link_sequence,
                record=old_envelope.record,
                previous_record_digest=link_previous,
            )
            link_lines.append(canonical_json_bytes(rebuilt.as_dict()) + b"\n")
            link_previous = rebuilt.record_digest
        self._write_bytes_atomic(self.links_path, b"".join(link_lines))
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

    def _append_link_envelopes_durable(
        self,
        envelopes: tuple[ProspectiveLinkLogEnvelopeV1, ...],
    ) -> None:
        payloads: list[bytes] = []
        for envelope in envelopes:
            line = canonical_json_bytes(envelope.as_dict()) + b"\n"
            if len(line) > MAX_LINK_BYTES:
                raise ProspectiveAuditStoreUnavailableError()
            payloads.append(line)
        self._append_bytes_durable(self.links_path, b"".join(payloads))

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


def resolve_current_decision_journal(
    reader: VaultReader | Callable[[], VaultSnapshot | ScanReport],
    decision_id: str,
) -> DecisionJournalTargetV1:
    """Reread the canonical vault and return one exact Journal projection."""

    try:
        _validate_uuid7_string(decision_id)
    except ValueError as exc:
        raise ProspectiveAuditLinkInvalidError(
            ProspectiveAuditLinkReasonCode.DECISION_TARGET_INVALID
        ) from exc
    try:
        scanned = reader() if callable(reader) and not hasattr(reader, "scan") else reader.scan()
        if type(scanned) is ScanReport:
            report = scanned
        elif type(scanned) is VaultSnapshot:
            report = build_report(scanned)
        else:
            raise ValueError("canonical scan result is invalid")
    except ProspectiveAuditLinkError:
        raise
    except Exception as exc:
        raise ProspectiveAuditLinkUnavailableError(
            ProspectiveAuditLinkReasonCode.CANONICAL_SCAN_UNAVAILABLE
        ) from exc
    if report.manifest is None or not report.content_scan_complete:
        raise ProspectiveAuditLinkUnavailableError(
            ProspectiveAuditLinkReasonCode.CANONICAL_SCAN_UNAVAILABLE
        )

    matches = [
        note
        for note in report.notes
        if note.note_id is not None and str(note.note_id) == decision_id
    ]
    raw_identity_matches = [
        note
        for note in report.notes
        if isinstance(note.front_matter.get("id"), str)
        and note.front_matter.get("id") == decision_id
    ]
    if len(matches) > 1 or len(raw_identity_matches) > 1:
        raise ProspectiveAuditLinkInvalidError(
            ProspectiveAuditLinkReasonCode.DECISION_IDENTITY_CONFLICT
        )
    if not matches:
        if raw_identity_matches:
            raise ProspectiveAuditLinkInvalidError(
                ProspectiveAuditLinkReasonCode.DECISION_TARGET_INVALID
            )
        raise ProspectiveAuditLinkUnavailableError(
            ProspectiveAuditLinkReasonCode.DECISION_TARGET_UNAVAILABLE
        )
    note = matches[0]
    if _raw_decision_journal_time_is_invalid(note):
        raise ProspectiveAuditLinkInvalidError(ProspectiveAuditLinkReasonCode.DECISION_TIME_INVALID)
    if not _is_valid_decision_journal_note(note):
        raise ProspectiveAuditLinkInvalidError(
            ProspectiveAuditLinkReasonCode.DECISION_TARGET_INVALID
        )
    assert note.personal_memory is not None
    assert note.decision_journal is not None
    metadata = note.personal_memory
    if (
        metadata.evidence_at_precision is not EvidenceAtPrecision.EXACT
        or type(metadata.evidence_at) is not datetime
    ):
        raise ProspectiveAuditLinkInvalidError(ProspectiveAuditLinkReasonCode.DECISION_TIME_INVALID)
    if (
        type(note.created) is not datetime
        or note.created.tzinfo is None
        or note.created.utcoffset() is None
        or metadata.evidence_at.tzinfo is None
        or metadata.evidence_at.utcoffset() is None
    ):
        raise ProspectiveAuditLinkInvalidError(ProspectiveAuditLinkReasonCode.DECISION_TIME_INVALID)
    try:
        evidence_at = metadata.evidence_at.astimezone(UTC)
        created = note.created.astimezone(UTC)
        options = tuple(
            _normalize_decision_option(item) for item in note.decision_journal.available_options
        )
        chosen = _normalize_decision_option(note.decision_journal.chosen_option)
        chosen_index = options.index(chosen)
        option_fingerprints = tuple(fingerprint_decision_option(item) for item in options)
        record_fingerprint = fingerprint_decision_record(
            decision_id,
            evidence_at,
            options,
            chosen,
        )
        return DecisionJournalTargetV1(
            decision_id=decision_id,
            evidence_at=evidence_at,
            evidence_at_precision=metadata.evidence_at_precision,
            created=created,
            available_options=options,
            chosen_option=chosen,
            chosen_option_index=chosen_index,
            option_fingerprints=option_fingerprints,
            decision_record_fingerprint=record_fingerprint,
        )
    except (AttributeError, IndexError, TypeError, ValueError, UnicodeError) as exc:
        raise ProspectiveAuditLinkInvalidError(
            ProspectiveAuditLinkReasonCode.DECISION_TARGET_INVALID
        ) from exc


def _raw_decision_journal_time_is_invalid(note: NoteRecord) -> bool:
    """Classify a discovered Stage 2 target with unusable temporal metadata."""

    metadata = note.front_matter
    if (
        metadata.get("evidence_kind") != EvidenceKind.OBSERVED_DECISION.value
        or metadata.get("self_kind") != SelfKind.DECISION.value
    ):
        return False
    if metadata.get("evidence_at_precision") != EvidenceAtPrecision.EXACT.value:
        return True
    evidence_at = metadata.get("evidence_at")
    return not (
        type(evidence_at) is datetime
        and evidence_at.tzinfo is not None
        and evidence_at.utcoffset() is not None
    )


def _is_valid_decision_journal_note(note: NoteRecord) -> bool:
    metadata = note.personal_memory
    return bool(
        note.managed
        and note.note_id is not None
        and metadata is not None
        and metadata.evidence_kind is EvidenceKind.OBSERVED_DECISION
        and metadata.self_kind is SelfKind.DECISION
        and note.decision_journal is not None
    )


def _validate_explicit_mapping(
    event: ProspectiveAuditEventV1,
    target: DecisionJournalTargetV1,
    mapping: object,
) -> tuple[ProspectiveOptionMappingV1, ...]:
    try:
        _validate_link_mapping_shape(mapping)
    except (TypeError, ValueError) as exc:
        raise ProspectiveAuditLinkInvalidError(
            ProspectiveAuditLinkReasonCode.OPTION_MAPPING_INVALID
        ) from exc
    assert isinstance(mapping, tuple)
    event_option_ids = {option.id for option in event.request.options}
    mapped_ids = {item.audit_option_id for item in mapping}
    mapped_indexes = {item.decision_option_index for item in mapping}
    if any(item.audit_option_id not in event_option_ids for item in mapping):
        raise ProspectiveAuditLinkInvalidError(
            ProspectiveAuditLinkReasonCode.OPTION_MAPPING_INVALID
        )
    if any(item.decision_option_index >= len(target.available_options) for item in mapping):
        raise ProspectiveAuditLinkInvalidError(
            ProspectiveAuditLinkReasonCode.OPTION_MAPPING_INVALID
        )
    for item in mapping:
        if (
            target.option_fingerprints[item.decision_option_index]
            != item.decision_option_fingerprint
        ):
            raise ProspectiveAuditLinkInvalidError(
                ProspectiveAuditLinkReasonCode.LINK_FINGERPRINT_MISMATCH
            )
    if event.result.kind is ProspectiveAuditResultKind.ABSTENTION:
        if mapping:
            raise ProspectiveAuditLinkInvalidError(
                ProspectiveAuditLinkReasonCode.OPTION_MAPPING_INVALID
            )
        return mapping
    predicted_option_id = event.result.predicted_option_id
    if predicted_option_id is None or predicted_option_id not in mapped_ids:
        raise ProspectiveAuditLinkInvalidError(
            ProspectiveAuditLinkReasonCode.OPTION_MAPPING_INVALID
        )
    if target.chosen_option_index not in mapped_indexes:
        raise ProspectiveAuditLinkInvalidError(
            ProspectiveAuditLinkReasonCode.CHOSEN_OPTION_UNMAPPED
        )
    return mapping


class BuildProspectiveDecisionLink:
    """Create and revalidate explicit Stage 9B links."""

    def __init__(
        self,
        store: ProspectiveAuditStore,
        reader: VaultReader | Callable[[], VaultSnapshot | ScanReport],
        *,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._store = store
        self._reader = reader
        self._clock = clock

    def execute(
        self,
        audit_event_id: str,
        decision_id: str,
        mapping: tuple[ProspectiveOptionMappingV1, ...],
    ) -> ProspectiveDecisionLinkV1:
        """Reread the current Journal, validate the mapping, then append."""

        event_envelope = self._store.read_event(audit_event_id)
        if event_envelope is None:
            raise ProspectiveAuditLinkUnavailableError(
                ProspectiveAuditLinkReasonCode.AUDIT_EVENT_MISSING_OR_DELETED
            )
        target = resolve_current_decision_journal(self._reader, decision_id)
        validated_mapping = _validate_explicit_mapping(event_envelope.event, target, mapping)
        return self._store._append_link_operation(
            audit_event_id=audit_event_id,
            decision_id=decision_id,
            decision_record_fingerprint=target.decision_record_fingerprint,
            actual_chosen_option_index=target.chosen_option_index,
            mapping=validated_mapping,
            decision_evidence_at=target.evidence_at,
            decision_created=target.created,
            expected_link_id=None,
            supersede=False,
            linked_at_override=None if self._clock is None else self._clock(),
        )

    link = execute
    create = execute

    def correct(
        self,
        audit_event_id: str,
        decision_id: str,
        mapping: tuple[ProspectiveOptionMappingV1, ...],
    ) -> ProspectiveDecisionLinkV1:
        """Supersede the current active link with a fresh owner-reviewed link."""

        state = self._store.current_link_state(audit_event_id)
        if state.link is None:
            raise ProspectiveAuditLinkConflictError()
        event_envelope = self._store.read_event(audit_event_id)
        if event_envelope is None:
            raise ProspectiveAuditLinkUnavailableError(
                ProspectiveAuditLinkReasonCode.AUDIT_EVENT_MISSING_OR_DELETED
            )
        target = resolve_current_decision_journal(self._reader, decision_id)
        validated_mapping = _validate_explicit_mapping(event_envelope.event, target, mapping)
        return self._store._append_link_operation(
            audit_event_id=audit_event_id,
            decision_id=decision_id,
            decision_record_fingerprint=target.decision_record_fingerprint,
            actual_chosen_option_index=target.chosen_option_index,
            mapping=validated_mapping,
            decision_evidence_at=target.evidence_at,
            decision_created=target.created,
            expected_link_id=state.link.link_id,
            supersede=True,
            linked_at_override=None if self._clock is None else self._clock(),
        )

    correct_link = correct

    def verify(self, audit_event_id: str) -> ProspectiveDecisionLinkVerificationV1:
        """Revalidate an accepted link against the current canonical Journal."""

        state = self._store.current_link_state(audit_event_id)
        if state.state is not ProspectiveDecisionLinkStateV1.LINKED_VALID:
            return state
        assert state.link is not None
        event_envelope = self._store.read_event(audit_event_id)
        if event_envelope is None:
            return ProspectiveDecisionLinkVerificationV1(
                ProspectiveDecisionLinkStateV1.LINK_UNAVAILABLE,
                reason_code=ProspectiveAuditLinkReasonCode.AUDIT_EVENT_MISSING_OR_DELETED.value,
            )
        try:
            target = resolve_current_decision_journal(self._reader, state.link.decision_id)
        except ProspectiveAuditLinkUnavailableError as exc:
            return ProspectiveDecisionLinkVerificationV1(
                ProspectiveDecisionLinkStateV1.LINK_UNAVAILABLE,
                reason_code=exc.reason_code,
            )
        except ProspectiveAuditLinkInvalidError as exc:
            return ProspectiveDecisionLinkVerificationV1(
                ProspectiveDecisionLinkStateV1.LINK_INVALID,
                reason_code=exc.reason_code,
            )
        if target.decision_record_fingerprint != state.link.decision_record_fingerprint:
            return ProspectiveDecisionLinkVerificationV1(
                ProspectiveDecisionLinkStateV1.LINK_INVALID,
                reason_code=ProspectiveAuditLinkReasonCode.DECISION_RECORD_CHANGED.value,
            )
        if target.chosen_option_index != state.link.actual_chosen_option_index:
            return ProspectiveDecisionLinkVerificationV1(
                ProspectiveDecisionLinkStateV1.LINK_INVALID,
                reason_code=ProspectiveAuditLinkReasonCode.DECISION_RECORD_CHANGED.value,
            )
        try:
            _validate_explicit_mapping(event_envelope.event, target, state.link.mapping)
            _validate_link_temporal_order(
                event_envelope.event.created_at,
                target.evidence_at,
                target.created,
                state.link.linked_at,
            )
        except ProspectiveAuditLinkUnavailableError as exc:
            return ProspectiveDecisionLinkVerificationV1(
                ProspectiveDecisionLinkStateV1.LINK_UNAVAILABLE,
                reason_code=exc.reason_code,
            )
        except ProspectiveAuditLinkInvalidError as exc:
            return ProspectiveDecisionLinkVerificationV1(
                ProspectiveDecisionLinkStateV1.LINK_INVALID,
                reason_code=exc.reason_code,
            )
        return state

    verify_current = verify
    validate_current_link = verify

    def invalidate(self, audit_event_id: str) -> ProspectiveDecisionLinkTombstoneV1:
        state = self._store.current_link_state(audit_event_id)
        if state.link is None:
            raise ProspectiveAuditLinkConflictError()
        return self._store.tombstone_link(
            audit_event_id=audit_event_id,
            expected_link_id=state.link.link_id,
        )


BuildProspectiveDecisionLinkV1 = BuildProspectiveDecisionLink
ProspectiveDecisionLinker = BuildProspectiveDecisionLink


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
ProspectiveOptionMapping = ProspectiveOptionMappingV1
ProspectiveDecisionLink = ProspectiveDecisionLinkV1
ProspectiveDecisionLinkState = ProspectiveDecisionLinkStateV1
ProspectiveDecisionLinkVerification = ProspectiveDecisionLinkVerificationV1
ProspectiveAuditErrorCodeV1 = ProspectiveAuditErrorCode
ProspectiveAuditResultKindV1 = ProspectiveAuditResultKind
ProspectiveAuditAbstentionCodeV1 = ProspectiveAuditAbstentionCode


def _validate_link_mapping_shape(mapping: object) -> None:
    if type(mapping) is not tuple or len(mapping) > MAX_JOURNAL_OPTIONS:
        raise ValueError("link mapping is invalid")
    audit_ids: set[str] = set()
    decision_indexes: set[int] = set()
    for item in mapping:
        if type(item) is not ProspectiveOptionMappingV1:
            raise ValueError("link mapping is invalid")
        if item.audit_option_id in audit_ids or item.decision_option_index in decision_indexes:
            raise ValueError("link mapping is not injective")
        audit_ids.add(item.audit_option_id)
        decision_indexes.add(item.decision_option_index)


def _build_link_envelope(
    *,
    generation_id: str,
    sequence: int,
    record: ProspectiveDecisionLinkV1 | ProspectiveDecisionLinkTombstoneV1,
    previous_record_digest: AuditHashV1 | None,
) -> ProspectiveLinkLogEnvelopeV1:
    return ProspectiveLinkLogEnvelopeV1(
        generation_id=generation_id,
        sequence=sequence,
        record=record,
        previous_record_digest=previous_record_digest,
        record_digest=compute_link_record_digest(
            generation_id=generation_id,
            sequence=sequence,
            record=record,
            previous_record_digest=previous_record_digest,
        ),
    )


def _last_link_digest(
    envelopes: tuple[ProspectiveLinkLogEnvelopeV1, ...],
) -> AuditHashV1 | None:
    return None if not envelopes else envelopes[-1].record_digest


def _next_link_sequence(snapshot: _VerifiedSnapshot) -> int:
    previous = snapshot.link_envelopes[-1].sequence if snapshot.link_envelopes else 0
    sequence = max(snapshot.manifest.last_sequence, previous) + 1
    if sequence > MAX_UINT64:
        raise ProspectiveAuditStoreUnavailableError()
    return sequence


def _event_from_snapshot(
    snapshot: _VerifiedSnapshot,
    event_id: str,
) -> AuditLogEnvelopeV1 | None:
    return next(
        (envelope for envelope in snapshot.envelopes if envelope.event.event_id == event_id),
        None,
    )


def _current_links_for_event(
    envelopes: tuple[ProspectiveLinkLogEnvelopeV1, ...],
    event_id: str,
) -> tuple[ProspectiveDecisionLinkV1 | None, bool]:
    active: ProspectiveDecisionLinkV1 | None = None
    tombstoned = False
    for envelope in envelopes:
        if envelope.link is not None and envelope.link.audit_event_id == event_id:
            active = envelope.link
            tombstoned = False
        elif envelope.tombstone is not None and envelope.tombstone.audit_event_id == event_id:
            active = None
            tombstoned = True
    return active, tombstoned


def _same_link_intent(
    link: ProspectiveDecisionLinkV1,
    *,
    audit_event_id: str,
    decision_id: str,
    decision_record_fingerprint: AuditHashV1,
    actual_chosen_option_index: int,
    mapping: tuple[ProspectiveOptionMappingV1, ...],
) -> bool:
    return (
        link.audit_event_id == audit_event_id
        and link.decision_id == decision_id
        and link.decision_record_fingerprint == decision_record_fingerprint
        and link.actual_chosen_option_index == actual_chosen_option_index
        and link.mapping == mapping
        and link.mapping_basis == LINK_MAPPING_BASIS
    )


def _validate_link_temporal_order(
    audit_created_at: datetime,
    decision_evidence_at: datetime,
    decision_created: datetime,
    linked_at: datetime,
) -> None:
    try:
        audit_time = _as_utc_for_link(audit_created_at)
        evidence_time = _as_utc_for_link(decision_evidence_at)
        created_time = _as_utc_for_link(decision_created)
        linked_time = _as_utc_for_link(linked_at)
    except ValueError as exc:
        raise ProspectiveAuditLinkInvalidError(
            ProspectiveAuditLinkReasonCode.DECISION_TIME_INVALID
        ) from exc
    if not audit_time < evidence_time:
        raise ProspectiveAuditLinkInvalidError(
            ProspectiveAuditLinkReasonCode.DECISION_PRECEDES_PREDICTION
        )
    if not audit_time < created_time:
        raise ProspectiveAuditLinkInvalidError(
            ProspectiveAuditLinkReasonCode.DECISION_NOTE_CREATED_BEFORE_PREDICTION
        )
    if evidence_time > linked_time or created_time > linked_time:
        raise ProspectiveAuditLinkInvalidError(ProspectiveAuditLinkReasonCode.DECISION_TIME_INVALID)


def _as_utc_for_link(value: object) -> datetime:
    if type(value) is not datetime or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("link timestamp must be aware")
    _canonical_datetime(value)
    return value.astimezone(UTC)


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


def _link_envelope_from_dict(data: object) -> ProspectiveLinkLogEnvelopeV1:
    if type(data) is not dict or set(data) != {
        "generation_id",
        "sequence",
        "record_type",
        "record",
        "previous_record_digest",
        "record_digest",
    }:
        raise ValueError("link envelope shape")
    record_type = data["record_type"]
    record_data = data["record"]
    if record_type == LINK_RECORD_TYPE:
        if type(record_data) is not dict or set(record_data) != {
            "version",
            "link_id",
            "audit_event_id",
            "decision_id",
            "linked_at",
            "decision_record_fingerprint",
            "actual_chosen_option_index",
            "mapping",
            "mapping_basis",
        }:
            raise ValueError("link shape")
        mapping_data = record_data["mapping"]
        if type(mapping_data) is not list:
            raise ValueError("link mapping shape")
        mapping: list[ProspectiveOptionMappingV1] = []
        for item in mapping_data:
            if type(item) is not dict or set(item) != {
                "audit_option_id",
                "decision_option_index",
                "decision_option_fingerprint",
            }:
                raise ValueError("link mapping item shape")
            mapping.append(ProspectiveOptionMappingV1(**item))
        record: ProspectiveDecisionLinkV1 | ProspectiveDecisionLinkTombstoneV1 = (
            ProspectiveDecisionLinkV1(
                version=record_data["version"],
                link_id=record_data["link_id"],
                audit_event_id=record_data["audit_event_id"],
                decision_id=record_data["decision_id"],
                linked_at=_parse_canonical_datetime(record_data["linked_at"]),
                decision_record_fingerprint=record_data["decision_record_fingerprint"],
                actual_chosen_option_index=record_data["actual_chosen_option_index"],
                mapping=tuple(mapping),
                mapping_basis=record_data["mapping_basis"],
            )
        )
    elif record_type == LINK_TOMBSTONE_RECORD_TYPE:
        if type(record_data) is not dict or set(record_data) != {
            "version",
            "tombstone_id",
            "audit_event_id",
            "supersedes_link_id",
            "tombstoned_at",
            "reason",
        }:
            raise ValueError("tombstone shape")
        record = ProspectiveDecisionLinkTombstoneV1(
            version=record_data["version"],
            tombstone_id=record_data["tombstone_id"],
            audit_event_id=record_data["audit_event_id"],
            supersedes_link_id=record_data["supersedes_link_id"],
            tombstoned_at=_parse_canonical_datetime(record_data["tombstoned_at"]),
            reason=ProspectiveDecisionLinkTombstoneReasonV1(record_data["reason"]),
        )
    else:
        raise ValueError("unknown link record type")
    return ProspectiveLinkLogEnvelopeV1(
        generation_id=data["generation_id"],
        sequence=data["sequence"],
        record=record,
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
    "LINK_MAPPING_BASIS",
    "LINK_RECORD_TYPE",
    "LINK_TOMBSTONE_RECORD_TYPE",
    "MANIFEST_FILE_NAME",
    "MAX_JOURNAL_OPTIONS",
    "MAX_JOURNAL_OPTION_BYTES",
    "MAX_LABEL_BYTES",
    "MAX_LINK_BYTES",
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
    "AuditLinkLogEnvelopeV1",
    "AuditLogEnvelopeV1",
    "AuditStore",
    "AuditStoreManifestV1",
    "BuildProspectiveAudit",
    "BuildProspectiveAuditV1",
    "BuildProspectiveDecisionLink",
    "BuildProspectiveDecisionLinkV1",
    "DecisionJournalTargetV1",
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
    "ProspectiveAuditLinkConflictError",
    "ProspectiveAuditLinkError",
    "ProspectiveAuditLinkInvalidError",
    "ProspectiveAuditLinkReasonCode",
    "ProspectiveAuditLinkUnavailableError",
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
    "ProspectiveDecisionLink",
    "ProspectiveDecisionLinkEnvelopeV1",
    "ProspectiveDecisionLinkState",
    "ProspectiveDecisionLinkStateV1",
    "ProspectiveDecisionLinkTombstoneReasonV1",
    "ProspectiveDecisionLinkTombstoneV1",
    "ProspectiveDecisionLinkV1",
    "ProspectiveDecisionLinkVerification",
    "ProspectiveDecisionLinkVerificationV1",
    "ProspectiveDecisionLinker",
    "ProspectiveLinkLogEnvelopeV1",
    "ProspectiveOptionMapping",
    "ProspectiveOptionMappingV1",
    "build_audit_event",
    "build_prospective_audit_event",
    "canonical_audit_json_bytes",
    "canonical_json",
    "canonical_json_bytes",
    "compute_link_record_digest",
    "compute_record_digest",
    "fingerprint_decision_journal_option",
    "fingerprint_decision_journal_record",
    "fingerprint_decision_option",
    "fingerprint_decision_record",
    "fingerprint_json",
    "fingerprint_operation_id",
    "fingerprint_option_label",
    "fingerprint_query",
    "fingerprint_simulate_me_result",
    "fingerprint_source_refs",
    "fingerprint_text",
    "normalize_operation_id",
    "resolve_current_decision_journal",
    "serialize_audit_envelope",
    "serialize_prospective_audit_event",
    "validate_audit_hash",
    "validate_prospective_audit_event",
]


# ---------------------------------------------------------------------------
# Stage 9C: prospective calibration aggregate
# ---------------------------------------------------------------------------

PROSPECTIVE_CALIBRATION_DERIVATION_VERSION: Final[str] = "prospective-calibration-v1"
PROSPECTIVE_CALIBRATION_POLICY_ID: Final[str] = "prospective-simulate-me-explicit-link-v1"
PROSPECTIVE_CALIBRATION_RETENTION_POLICY: Final[str] = RETENTION_POLICY
PROSPECTIVE_CALIBRATION_MAX_RESULT_BYTES_V1: Final[int] = 65_536
MAX_RESULT_BYTES_V1: Final[int] = PROSPECTIVE_CALIBRATION_MAX_RESULT_BYTES_V1

# This is the normative ASCII policy serialization from §14.4 of the merged
# Stage 9 contract.  It is deliberately kept separate from the Stage 6 policy
# identity above: prospective calibration is a different capability.
PROSPECTIVE_CALIBRATION_POLICY_CANONICAL_JSON: Final[str] = (
    '{"actual_target":"stage2-decision-journal-explicit-link-v1",'
    '"coverage":"prediction-over-audited-operations-v1",'
    '"linkage":"latest-explicit-state-exclusive-v1",'
    '"metrics":"counts-and-integer-ratios-no-confidence-v1",'
    '"option_mapping":"owner-explicit-injective-index-v1",'
    '"retention":"prospective-audit-retention-180d-v1",'
    '"temporal":"audit-created-before-decision-evidence-and-note-created-v1",'
    '"version":"1"}'
)
PROSPECTIVE_CALIBRATION_POLICY_FINGERPRINT: Final[str] = (
    "sha256:f6a3229ecdc547bb16f9426d1b78d6e5d06e2355954ca30a37be90bdaec1bbc1"
)

# Short aliases mirror the naming style of the existing Stage 7 aggregate and
# make the policy boundary easy to discover without changing the exact DTO.
CALIBRATION_DERIVATION_VERSION: Final[str] = PROSPECTIVE_CALIBRATION_DERIVATION_VERSION
CALIBRATION_POLICY_ID: Final[str] = PROSPECTIVE_CALIBRATION_POLICY_ID
CALIBRATION_POLICY_FINGERPRINT: Final[str] = PROSPECTIVE_CALIBRATION_POLICY_FINGERPRINT
CALIBRATION_POLICY_CANONICAL_JSON: Final[str] = PROSPECTIVE_CALIBRATION_POLICY_CANONICAL_JSON


class ProspectiveCalibrationInvalidLinkageCodeV1(StrEnum):
    """Fixed invalid-linkage code vocabulary and serialization order."""

    DECISION_TARGET_INVALID = "decision_target_invalid"
    DECISION_IDENTITY_CONFLICT = "decision_identity_conflict"
    DECISION_TIME_INVALID = "decision_time_invalid"
    DECISION_PRECEDES_PREDICTION = "decision_precedes_prediction"
    DECISION_NOTE_CREATED_BEFORE_PREDICTION = "decision_note_created_before_prediction"
    DECISION_RECORD_CHANGED = "decision_record_changed"
    OPTION_MAPPING_INVALID = "option_mapping_invalid"
    CHOSEN_OPTION_UNMAPPED = "chosen_option_unmapped"
    LINK_FINGERPRINT_MISMATCH = "link_fingerprint_mismatch"


class ProspectiveCalibrationUnavailableLinkageCodeV1(StrEnum):
    """Fixed unavailable-linkage code vocabulary and serialization order."""

    AUDIT_EVENT_MISSING_OR_DELETED = "audit_event_missing_or_deleted"
    DECISION_TARGET_UNAVAILABLE = "decision_target_unavailable"
    CANONICAL_SCAN_UNAVAILABLE = "canonical_scan_unavailable"
    DECISION_TARGET_EXPIRED = "decision_target_expired"


class ProspectiveCalibrationErrorCodeV1(StrEnum):
    """Top-level safe errors for the derived Stage 9C read model."""

    UNAVAILABLE = "PROSPECTIVE_CALIBRATION_UNAVAILABLE"
    RESULT_TOO_LARGE = "PROSPECTIVE_CALIBRATION_RESULT_TOO_LARGE"


PROSPECTIVE_CALIBRATION_INVALID_LINKAGE_CODES_V1: Final[tuple[str, ...]] = tuple(
    code.value for code in ProspectiveCalibrationInvalidLinkageCodeV1
)
PROSPECTIVE_CALIBRATION_UNAVAILABLE_LINKAGE_CODES_V1: Final[tuple[str, ...]] = tuple(
    code.value for code in ProspectiveCalibrationUnavailableLinkageCodeV1
)


_PROSPECTIVE_CALIBRATION_ERROR_MESSAGES: Final[dict[ProspectiveCalibrationErrorCodeV1, str]] = {
    ProspectiveCalibrationErrorCodeV1.UNAVAILABLE: "prospective calibration source is unavailable",
    ProspectiveCalibrationErrorCodeV1.RESULT_TOO_LARGE: (
        "prospective calibration result exceeds its byte limit"
    ),
}


class ProspectiveCalibrationError(RuntimeError):
    """Public Stage 9C error with only a fixed code and fixed message."""

    def __init__(self, code: ProspectiveCalibrationErrorCodeV1 | str) -> None:
        try:
            normalized = ProspectiveCalibrationErrorCodeV1(code)
        except TypeError, ValueError:
            normalized = ProspectiveCalibrationErrorCodeV1.UNAVAILABLE
        self.code = normalized.value
        self.message = _PROSPECTIVE_CALIBRATION_ERROR_MESSAGES[normalized]
        super().__init__(self.message)

    def as_dict(self) -> dict[str, str]:
        return {"code": self.code, "message": self.message}


class ProspectiveCalibrationUnavailableError(ProspectiveCalibrationError):
    """No verified stable event/link snapshot can support an aggregate."""

    def __init__(self) -> None:
        super().__init__(ProspectiveCalibrationErrorCodeV1.UNAVAILABLE)


class ProspectiveCalibrationResultTooLargeError(ProspectiveCalibrationError):
    """The complete canonical result crossed its fixed byte bound."""

    def __init__(self) -> None:
        super().__init__(ProspectiveCalibrationErrorCodeV1.RESULT_TOO_LARGE)


# Descriptive aliases for callers that use the Stage 7 ``SourceUnavailable``
# naming convention or omit the ``Linkage`` word from the fixed code enum.
ProspectiveCalibrationSourceUnavailableError = ProspectiveCalibrationUnavailableError
ProspectiveCalibrationInvalidCodeV1 = ProspectiveCalibrationInvalidLinkageCodeV1
ProspectiveCalibrationUnavailableCodeV1 = ProspectiveCalibrationUnavailableLinkageCodeV1
ProspectiveCalibrationErrorCode = ProspectiveCalibrationErrorCodeV1


def _calibration_non_negative_int(value: object) -> bool:
    return type(value) is int and not isinstance(value, bool) and value >= 0


@dataclass(frozen=True, slots=True)
class ProspectiveCalibrationRequestV1:
    """Empty request: Stage 9C v1 has no caller-configurable policy knobs."""


@dataclass(frozen=True, slots=True)
class ProspectiveCalibrationRatioV1:
    """Exact integer numerator/denominator ratio; never a float or percentage."""

    numerator: int
    denominator: int

    def __post_init__(self) -> None:
        if not _calibration_non_negative_int(self.numerator):
            raise ValueError("ratio numerator is invalid")
        if (
            type(self.denominator) is not int
            or isinstance(self.denominator, bool)
            or self.denominator <= 0
        ):
            raise ValueError("ratio denominator is invalid")


@dataclass(frozen=True, slots=True)
class ProspectiveCalibrationMetricsV1:
    """Exact bounded metric fields prescribed by the prospective contract."""

    audited_operations: int
    predictions: int
    abstentions: int
    linked_actual_decisions: int
    pending_unlinked_events: int
    invalid_linkage_events: int
    unavailable_linkage_events: int
    exact_option_matches: int
    mismatches: int
    coverage: ProspectiveCalibrationRatioV1 | None
    actual_linkage_coverage: ProspectiveCalibrationRatioV1 | None
    evaluated_prediction_coverage: ProspectiveCalibrationRatioV1 | None
    accuracy_non_abstained: ProspectiveCalibrationRatioV1 | None

    def __post_init__(self) -> None:
        values = (
            self.audited_operations,
            self.predictions,
            self.abstentions,
            self.linked_actual_decisions,
            self.pending_unlinked_events,
            self.invalid_linkage_events,
            self.unavailable_linkage_events,
            self.exact_option_matches,
            self.mismatches,
        )
        if any(not _calibration_non_negative_int(value) for value in values):
            raise ValueError("calibration metric count is invalid")
        ratios = (
            self.coverage,
            self.actual_linkage_coverage,
            self.evaluated_prediction_coverage,
            self.accuracy_non_abstained,
        )
        if any(
            ratio is not None and type(ratio) is not ProspectiveCalibrationRatioV1
            for ratio in ratios
        ):
            raise ValueError("calibration ratio is invalid")


@dataclass(frozen=True, slots=True)
class ProspectiveCalibrationCountV1:
    """One fixed-code counter in the canonical aggregate."""

    code: str
    count: int

    def __post_init__(self) -> None:
        if not isinstance(self.code, str) or not _calibration_non_negative_int(self.count):
            raise ValueError("calibration count is invalid")


@dataclass(frozen=True, slots=True)
class ProspectiveCalibrationResultV1:
    """Exact privacy-safe derived DTO; no event or Journal identity is exposed."""

    contract_version: str
    derivation_version: str
    policy_id: str
    policy_fingerprint: AuditHashV1
    retention_policy: str
    metrics: ProspectiveCalibrationMetricsV1
    invalid_linkage_by_code: tuple[ProspectiveCalibrationCountV1, ...]
    unavailable_linkage_by_code: tuple[ProspectiveCalibrationCountV1, ...]

    def __post_init__(self) -> None:
        if (
            type(self.contract_version) is not str
            or type(self.derivation_version) is not str
            or type(self.policy_id) is not str
            or type(self.retention_policy) is not str
            or type(self.metrics) is not ProspectiveCalibrationMetricsV1
            or type(self.invalid_linkage_by_code) is not tuple
            or type(self.unavailable_linkage_by_code) is not tuple
            or any(
                type(item) is not ProspectiveCalibrationCountV1
                for item in self.invalid_linkage_by_code
            )
            or any(
                type(item) is not ProspectiveCalibrationCountV1
                for item in self.unavailable_linkage_by_code
            )
        ):
            raise ValueError("calibration result shape is invalid")


def validate_prospective_calibration_policy() -> str:
    """Recompute the exact v1 policy fingerprint and Stage 6 dependency identity."""

    try:
        if not PROSPECTIVE_CALIBRATION_POLICY_CANONICAL_JSON.isascii():
            raise ValueError("policy serialization is not ASCII")
        digest = hashlib.sha256(
            PROSPECTIVE_CALIBRATION_POLICY_CANONICAL_JSON.encode("ascii")
        ).hexdigest()
        if f"sha256:{digest}" != PROSPECTIVE_CALIBRATION_POLICY_FINGERPRINT:
            raise ValueError("prospective policy fingerprint mismatch")
        if (
            DERIVATION_VERSION != "simulate-me-v1"
            or POLICY_ID != "simulate-me-direct-exact-v1"
            or validate_simulate_me_policy() != POLICY_FINGERPRINT
        ):
            raise ValueError("Stage 6 policy identity mismatch")
    except Exception:
        raise ProspectiveCalibrationUnavailableError() from None
    return PROSPECTIVE_CALIBRATION_POLICY_FINGERPRINT


def _validate_prospective_calibration_count_array(
    counts: object,
    expected_codes: tuple[str, ...],
) -> None:
    if type(counts) is not tuple or len(counts) != len(expected_codes):
        raise ValueError("fixed calibration count array is invalid")
    for item, expected_code in zip(counts, expected_codes, strict=True):
        if (
            type(item) is not ProspectiveCalibrationCountV1
            or item.code != expected_code
            or not _calibration_non_negative_int(item.count)
        ):
            raise ValueError("fixed calibration count item is invalid")


def _validate_prospective_calibration_ratio(
    ratio: ProspectiveCalibrationRatioV1 | None,
    *,
    numerator: int,
    denominator: int,
) -> None:
    if denominator == 0:
        if ratio is not None:
            raise ValueError("zero-denominator ratio must be null")
        return
    if (
        type(ratio) is not ProspectiveCalibrationRatioV1
        or ratio.numerator != numerator
        or ratio.denominator != denominator
    ):
        raise ValueError("calibration ratio is invalid")


def validate_prospective_calibration_result(
    result: object,
) -> ProspectiveCalibrationResultV1:
    """Validate exact DTO identity, fixed arrays, arithmetic and ratio rules."""

    if type(result) is not ProspectiveCalibrationResultV1:
        raise ValueError("result is not the exact prospective calibration DTO")
    if (
        result.contract_version != CONTRACT_VERSION
        or result.derivation_version != PROSPECTIVE_CALIBRATION_DERIVATION_VERSION
        or result.policy_id != PROSPECTIVE_CALIBRATION_POLICY_ID
        or result.policy_fingerprint != PROSPECTIVE_CALIBRATION_POLICY_FINGERPRINT
        or result.retention_policy != PROSPECTIVE_CALIBRATION_RETENTION_POLICY
    ):
        raise ValueError("prospective calibration identity is invalid")
    validate_prospective_calibration_policy()
    metrics = result.metrics
    values = (
        metrics.audited_operations,
        metrics.predictions,
        metrics.abstentions,
        metrics.linked_actual_decisions,
        metrics.pending_unlinked_events,
        metrics.invalid_linkage_events,
        metrics.unavailable_linkage_events,
        metrics.exact_option_matches,
        metrics.mismatches,
    )
    if any(not _calibration_non_negative_int(value) for value in values):
        raise ValueError("calibration metric count is invalid")
    _validate_prospective_calibration_count_array(
        result.invalid_linkage_by_code,
        PROSPECTIVE_CALIBRATION_INVALID_LINKAGE_CODES_V1,
    )
    _validate_prospective_calibration_count_array(
        result.unavailable_linkage_by_code,
        PROSPECTIVE_CALIBRATION_UNAVAILABLE_LINKAGE_CODES_V1,
    )

    linked_predictions = metrics.exact_option_matches + metrics.mismatches
    linked_abstentions = metrics.linked_actual_decisions - linked_predictions
    invalid_count = sum(item.count for item in result.invalid_linkage_by_code)
    unavailable_count = sum(item.count for item in result.unavailable_linkage_by_code)
    if (
        metrics.audited_operations != metrics.predictions + metrics.abstentions
        or metrics.predictions < linked_predictions
        or metrics.linked_actual_decisions < linked_predictions
        or linked_abstentions < 0
        or linked_abstentions > metrics.abstentions
        or metrics.linked_actual_decisions
        + metrics.pending_unlinked_events
        + metrics.invalid_linkage_events
        + metrics.unavailable_linkage_events
        != metrics.audited_operations
        or metrics.invalid_linkage_events != invalid_count
        or metrics.unavailable_linkage_events != unavailable_count
        or linked_predictions != metrics.exact_option_matches + metrics.mismatches
    ):
        raise ValueError("prospective calibration count identity is invalid")
    _validate_prospective_calibration_ratio(
        metrics.coverage,
        numerator=metrics.predictions,
        denominator=metrics.audited_operations,
    )
    _validate_prospective_calibration_ratio(
        metrics.actual_linkage_coverage,
        numerator=metrics.linked_actual_decisions,
        denominator=metrics.audited_operations,
    )
    _validate_prospective_calibration_ratio(
        metrics.evaluated_prediction_coverage,
        numerator=linked_predictions,
        denominator=metrics.predictions,
    )
    _validate_prospective_calibration_ratio(
        metrics.accuracy_non_abstained,
        numerator=metrics.exact_option_matches,
        denominator=linked_predictions,
    )
    return result


_CALIBRATION_MAX_SAFE_INT_BITS: Final[int] = (MAX_RESULT_BYTES_V1 + 4_096) * 1_000 // 301 + 2


def _prospective_calibration_json_int(value: int) -> str:
    """Convert a bounded non-negative count without Python's digit cap."""

    if value.bit_length() > _CALIBRATION_MAX_SAFE_INT_BITS:
        raise ProspectiveCalibrationResultTooLargeError()
    if value < 1_000_000_000:
        return str(value)
    chunks: list[str] = []
    remaining = value
    while remaining >= 1_000_000_000:
        remaining, chunk = divmod(remaining, 1_000_000_000)
        chunks.append(f"{chunk:09d}")
    chunks.append(str(remaining))
    return "".join(reversed(chunks))


def _prospective_calibration_json_string(value: str) -> str:
    return json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":"))


def _prospective_calibration_json_ratio(
    ratio: ProspectiveCalibrationRatioV1 | None,
) -> str:
    if ratio is None:
        return "null"
    return (
        '{"numerator":'
        + _prospective_calibration_json_int(ratio.numerator)
        + ',"denominator":'
        + _prospective_calibration_json_int(ratio.denominator)
        + "}"
    )


def _prospective_calibration_json_metrics(
    metrics: ProspectiveCalibrationMetricsV1,
) -> str:
    return (
        '{"audited_operations":'
        + _prospective_calibration_json_int(metrics.audited_operations)
        + ',"predictions":'
        + _prospective_calibration_json_int(metrics.predictions)
        + ',"abstentions":'
        + _prospective_calibration_json_int(metrics.abstentions)
        + ',"linked_actual_decisions":'
        + _prospective_calibration_json_int(metrics.linked_actual_decisions)
        + ',"pending_unlinked_events":'
        + _prospective_calibration_json_int(metrics.pending_unlinked_events)
        + ',"invalid_linkage_events":'
        + _prospective_calibration_json_int(metrics.invalid_linkage_events)
        + ',"unavailable_linkage_events":'
        + _prospective_calibration_json_int(metrics.unavailable_linkage_events)
        + ',"exact_option_matches":'
        + _prospective_calibration_json_int(metrics.exact_option_matches)
        + ',"mismatches":'
        + _prospective_calibration_json_int(metrics.mismatches)
        + ',"coverage":'
        + _prospective_calibration_json_ratio(metrics.coverage)
        + ',"actual_linkage_coverage":'
        + _prospective_calibration_json_ratio(metrics.actual_linkage_coverage)
        + ',"evaluated_prediction_coverage":'
        + _prospective_calibration_json_ratio(metrics.evaluated_prediction_coverage)
        + ',"accuracy_non_abstained":'
        + _prospective_calibration_json_ratio(metrics.accuracy_non_abstained)
        + "}"
    )


def _prospective_calibration_json_counts(
    counts: tuple[ProspectiveCalibrationCountV1, ...],
) -> str:
    return (
        "["
        + ",".join(
            '{"code":'
            + _prospective_calibration_json_string(item.code)
            + ',"count":'
            + _prospective_calibration_json_int(item.count)
            + "}"
            for item in counts
        )
        + "]"
    )


def _prospective_calibration_json_result(result: ProspectiveCalibrationResultV1) -> str:
    return (
        '{"contract_version":'
        + _prospective_calibration_json_string(result.contract_version)
        + ',"derivation_version":'
        + _prospective_calibration_json_string(result.derivation_version)
        + ',"policy_id":'
        + _prospective_calibration_json_string(result.policy_id)
        + ',"policy_fingerprint":'
        + _prospective_calibration_json_string(result.policy_fingerprint)
        + ',"retention_policy":'
        + _prospective_calibration_json_string(result.retention_policy)
        + ',"metrics":'
        + _prospective_calibration_json_metrics(result.metrics)
        + ',"invalid_linkage_by_code":'
        + _prospective_calibration_json_counts(result.invalid_linkage_by_code)
        + ',"unavailable_linkage_by_code":'
        + _prospective_calibration_json_counts(result.unavailable_linkage_by_code)
        + "}"
    )


def serialize_prospective_calibration_result(
    result: ProspectiveCalibrationResultV1,
) -> bytes:
    """Serialize the complete exact DTO as bounded canonical UTF-8 JSON."""

    try:
        validated = validate_prospective_calibration_result(result)
        encoded = _prospective_calibration_json_result(validated).encode("utf-8")
    except ProspectiveCalibrationResultTooLargeError:
        raise
    except ProspectiveCalibrationError:
        raise
    except Exception:
        raise ProspectiveCalibrationUnavailableError() from None
    if len(encoded) > MAX_RESULT_BYTES_V1:
        raise ProspectiveCalibrationResultTooLargeError()
    return encoded


serialize_prospective_calibration_result_v1 = serialize_prospective_calibration_result


def _validate_calibration_snapshot(snapshot: object) -> _VerifiedSnapshot:
    """Defensively validate the same stable snapshot shape used by the store."""

    if type(snapshot) is not _VerifiedSnapshot:
        raise ValueError("snapshot shape is invalid")
    if (
        type(snapshot.manifest) is not AuditStoreManifestV1
        or type(snapshot.envelopes) is not tuple
        or type(snapshot.link_envelopes) is not tuple
    ):
        raise ValueError("snapshot shape is invalid")

    event_sequences: dict[str, int] = {}
    previous_event_digest: AuditHashV1 | None = None
    for expected_sequence, envelope in enumerate(snapshot.envelopes, start=1):
        if (
            type(envelope) is not AuditLogEnvelopeV1
            or envelope.generation_id != snapshot.manifest.generation_id
            or envelope.sequence != expected_sequence
            or envelope.previous_record_digest != previous_event_digest
            or envelope.record_digest != envelope.expected_record_digest
        ):
            raise ValueError("event snapshot integrity is invalid")
        validate_prospective_audit_event(envelope.event)
        if envelope.event.event_id in event_sequences:
            raise ValueError("event snapshot contains a duplicate")
        event_sequences[envelope.event.event_id] = envelope.sequence
        previous_event_digest = envelope.record_digest
    if (
        snapshot.manifest.last_sequence != len(snapshot.envelopes)
        or snapshot.manifest.last_record_digest != previous_event_digest
    ):
        raise ValueError("event manifest is invalid")

    previous_link_digest: AuditHashV1 | None = None
    previous_link_sequence = 0
    seen_record_ids: set[str] = set()
    links_by_id: dict[str, ProspectiveDecisionLinkV1] = {}
    active_by_event: dict[str, ProspectiveDecisionLinkV1] = {}
    for link_envelope in snapshot.link_envelopes:
        if (
            type(link_envelope) is not ProspectiveLinkLogEnvelopeV1
            or link_envelope.generation_id != snapshot.manifest.generation_id
            or link_envelope.sequence <= previous_link_sequence
            or link_envelope.previous_record_digest != previous_link_digest
            or link_envelope.record_digest != link_envelope.expected_record_digest
        ):
            raise ValueError("link snapshot integrity is invalid")
        record_id: str
        if link_envelope.link is not None:
            link = link_envelope.link
            event_sequence = event_sequences.get(link.audit_event_id)
            if event_sequence is None or event_sequence >= link_envelope.sequence:
                raise ValueError("link event ordering is invalid")
            record_id = link.link_id
            if record_id in seen_record_ids or link.audit_event_id in active_by_event:
                raise ValueError("link snapshot has a conflicting active state")
            links_by_id[record_id] = link
            active_by_event[link.audit_event_id] = link
        else:
            tombstone = link_envelope.tombstone
            if tombstone is None:
                raise ValueError("link record is invalid")
            event_sequence = event_sequences.get(tombstone.audit_event_id)
            if event_sequence is None or event_sequence >= link_envelope.sequence:
                raise ValueError("tombstone event ordering is invalid")
            record_id = tombstone.tombstone_id
            target = links_by_id.get(tombstone.supersedes_link_id)
            if (
                record_id in seen_record_ids
                or target is None
                or target.audit_event_id != tombstone.audit_event_id
                or active_by_event.get(tombstone.audit_event_id) != target
            ):
                raise ValueError("tombstone state is invalid")
            del active_by_event[tombstone.audit_event_id]
        seen_record_ids.add(record_id)
        previous_link_digest = link_envelope.record_digest
        previous_link_sequence = link_envelope.sequence
    return snapshot


def _calibration_now(clock: Callable[[], datetime], now: datetime | None) -> datetime:
    try:
        return _as_utc(clock() if now is None else now)
    except Exception:
        raise ProspectiveCalibrationUnavailableError() from None


def _calibration_is_cancelled(cancellation: object | None) -> bool:
    if cancellation is None:
        return False
    try:
        checker = cancellation.is_cancelled  # type: ignore[attr-defined]
        value = checker()
    except Exception:
        raise ProspectiveCalibrationUnavailableError() from None
    if type(value) is not bool:
        raise ProspectiveCalibrationUnavailableError()
    return value


def _revalidate_calibration_link(
    event_envelope: AuditLogEnvelopeV1,
    link: ProspectiveDecisionLinkV1,
    reader: VaultReader | Callable[[], VaultSnapshot | ScanReport] | None,
) -> ProspectiveDecisionLinkVerificationV1:
    """Apply Stage 9B current-target rules to one link from a stable snapshot."""

    event = event_envelope.event
    if link.audit_event_id != event.event_id:
        return ProspectiveDecisionLinkVerificationV1(
            ProspectiveDecisionLinkStateV1.LINK_UNAVAILABLE,
            reason_code=ProspectiveAuditLinkReasonCode.AUDIT_EVENT_MISSING_OR_DELETED.value,
        )
    try:
        linked_at = _as_utc_for_link(link.linked_at)
    except ValueError:
        return ProspectiveDecisionLinkVerificationV1(
            ProspectiveDecisionLinkStateV1.LINK_INVALID,
            reason_code=ProspectiveAuditLinkReasonCode.DECISION_TIME_INVALID.value,
        )
    if linked_at >= event.created_at + RETENTION:
        return ProspectiveDecisionLinkVerificationV1(
            ProspectiveDecisionLinkStateV1.LINK_UNAVAILABLE,
            reason_code=ProspectiveAuditLinkReasonCode.DECISION_TARGET_EXPIRED.value,
        )
    if reader is None:
        return ProspectiveDecisionLinkVerificationV1(
            ProspectiveDecisionLinkStateV1.LINK_UNAVAILABLE,
            reason_code=ProspectiveAuditLinkReasonCode.CANONICAL_SCAN_UNAVAILABLE.value,
        )
    try:
        target = resolve_current_decision_journal(reader, link.decision_id)
    except ProspectiveAuditLinkUnavailableError as exc:
        return ProspectiveDecisionLinkVerificationV1(
            ProspectiveDecisionLinkStateV1.LINK_UNAVAILABLE,
            reason_code=exc.reason_code,
        )
    except ProspectiveAuditLinkInvalidError as exc:
        return ProspectiveDecisionLinkVerificationV1(
            ProspectiveDecisionLinkStateV1.LINK_INVALID,
            reason_code=exc.reason_code,
        )
    except Exception:
        return ProspectiveDecisionLinkVerificationV1(
            ProspectiveDecisionLinkStateV1.LINK_UNAVAILABLE,
            reason_code=ProspectiveAuditLinkReasonCode.CANONICAL_SCAN_UNAVAILABLE.value,
        )

    if target.decision_record_fingerprint != link.decision_record_fingerprint:
        return ProspectiveDecisionLinkVerificationV1(
            ProspectiveDecisionLinkStateV1.LINK_INVALID,
            reason_code=ProspectiveAuditLinkReasonCode.DECISION_RECORD_CHANGED.value,
        )
    if target.chosen_option_index != link.actual_chosen_option_index:
        return ProspectiveDecisionLinkVerificationV1(
            ProspectiveDecisionLinkStateV1.LINK_INVALID,
            reason_code=ProspectiveAuditLinkReasonCode.DECISION_RECORD_CHANGED.value,
        )
    try:
        _validate_explicit_mapping(event, target, link.mapping)
        _validate_link_temporal_order(
            event.created_at,
            target.evidence_at,
            target.created,
            linked_at,
        )
    except ProspectiveAuditLinkUnavailableError as exc:
        return ProspectiveDecisionLinkVerificationV1(
            ProspectiveDecisionLinkStateV1.LINK_UNAVAILABLE,
            reason_code=exc.reason_code,
        )
    except ProspectiveAuditLinkInvalidError as exc:
        return ProspectiveDecisionLinkVerificationV1(
            ProspectiveDecisionLinkStateV1.LINK_INVALID,
            reason_code=exc.reason_code,
        )
    except Exception:
        return ProspectiveDecisionLinkVerificationV1(
            ProspectiveDecisionLinkStateV1.LINK_INVALID,
            reason_code=ProspectiveAuditLinkReasonCode.OPTION_MAPPING_INVALID.value,
        )
    return ProspectiveDecisionLinkVerificationV1(
        ProspectiveDecisionLinkStateV1.LINKED_VALID,
        link=link,
    )


def _prospective_calibration_result(
    *,
    audited_operations: int,
    predictions: int,
    abstentions: int,
    linked_actual_decisions: int,
    pending_unlinked_events: int,
    invalid_linkage_events: int,
    unavailable_linkage_events: int,
    exact_option_matches: int,
    mismatches: int,
    invalid_counts: dict[str, int],
    unavailable_counts: dict[str, int],
) -> ProspectiveCalibrationResultV1:
    linked_predictions = exact_option_matches + mismatches
    metrics = ProspectiveCalibrationMetricsV1(
        audited_operations=audited_operations,
        predictions=predictions,
        abstentions=abstentions,
        linked_actual_decisions=linked_actual_decisions,
        pending_unlinked_events=pending_unlinked_events,
        invalid_linkage_events=invalid_linkage_events,
        unavailable_linkage_events=unavailable_linkage_events,
        exact_option_matches=exact_option_matches,
        mismatches=mismatches,
        coverage=(
            ProspectiveCalibrationRatioV1(predictions, audited_operations)
            if audited_operations
            else None
        ),
        actual_linkage_coverage=(
            ProspectiveCalibrationRatioV1(linked_actual_decisions, audited_operations)
            if audited_operations
            else None
        ),
        evaluated_prediction_coverage=(
            ProspectiveCalibrationRatioV1(linked_predictions, predictions) if predictions else None
        ),
        accuracy_non_abstained=(
            ProspectiveCalibrationRatioV1(exact_option_matches, linked_predictions)
            if linked_predictions
            else None
        ),
    )
    result = ProspectiveCalibrationResultV1(
        contract_version=CONTRACT_VERSION,
        derivation_version=PROSPECTIVE_CALIBRATION_DERIVATION_VERSION,
        policy_id=PROSPECTIVE_CALIBRATION_POLICY_ID,
        policy_fingerprint=PROSPECTIVE_CALIBRATION_POLICY_FINGERPRINT,
        retention_policy=PROSPECTIVE_CALIBRATION_RETENTION_POLICY,
        metrics=metrics,
        invalid_linkage_by_code=tuple(
            ProspectiveCalibrationCountV1(code, invalid_counts.get(code, 0))
            for code in PROSPECTIVE_CALIBRATION_INVALID_LINKAGE_CODES_V1
        ),
        unavailable_linkage_by_code=tuple(
            ProspectiveCalibrationCountV1(code, unavailable_counts.get(code, 0))
            for code in PROSPECTIVE_CALIBRATION_UNAVAILABLE_LINKAGE_CODES_V1
        ),
    )
    try:
        return validate_prospective_calibration_result(result)
    except ProspectiveCalibrationError:
        raise
    except Exception:
        raise ProspectiveCalibrationUnavailableError() from None


class BuildProspectiveCalibration:
    """Rebuild one complete Stage 9C aggregate from a verified stable snapshot."""

    def __init__(
        self,
        store: ProspectiveAuditStore,
        reader: VaultReader | Callable[[], VaultSnapshot | ScanReport] | None = None,
        *,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._store = store
        self._reader = reader
        if clock is not None:
            self._clock = clock
        else:
            store_clock = getattr(store, "_clock", None)
            self._clock = store_clock if callable(store_clock) else lambda: datetime.now(UTC)

    def execute(
        self,
        request: ProspectiveCalibrationRequestV1 | None = None,
        *,
        now: datetime | None = None,
        cancellation: object | None = None,
    ) -> ProspectiveCalibrationResultV1:
        """Read only the current generation and return its exact aggregate."""

        if request is not None and type(request) is not ProspectiveCalibrationRequestV1:
            raise ProspectiveCalibrationUnavailableError()
        if _calibration_is_cancelled(cancellation):
            raise ProspectiveCalibrationUnavailableError()
        validate_prospective_calibration_policy()
        current = _calibration_now(self._clock, now)
        try:
            reader_method = getattr(self._store, "read_verified_snapshot", None)
            if not callable(reader_method):
                reader_method = getattr(self._store, "read_stable_snapshot", None)
            if not callable(reader_method):
                raise ValueError("verified snapshot boundary is unavailable")
            snapshot = _validate_calibration_snapshot(reader_method())
        except Exception:
            raise ProspectiveCalibrationUnavailableError() from None

        active_events = tuple(
            envelope
            for envelope in snapshot.envelopes
            if current < envelope.event.created_at + RETENTION
        )
        invalid_counts = {code: 0 for code in PROSPECTIVE_CALIBRATION_INVALID_LINKAGE_CODES_V1}
        unavailable_counts = {
            code: 0 for code in PROSPECTIVE_CALIBRATION_UNAVAILABLE_LINKAGE_CODES_V1
        }
        predictions = 0
        abstentions = 0
        linked_actual_decisions = 0
        pending_unlinked_events = 0
        invalid_linkage_events = 0
        unavailable_linkage_events = 0
        exact_option_matches = 0
        mismatches = 0

        ordered_events = tuple(
            sorted(
                active_events,
                key=lambda item: (
                    item.event.created_at,
                    item.event.event_id,
                    item.sequence,
                ),
            )
        )
        for event_envelope in ordered_events:
            if _calibration_is_cancelled(cancellation):
                raise ProspectiveCalibrationUnavailableError()
            event = event_envelope.event
            if event.result.kind is ProspectiveAuditResultKind.PREDICTION:
                predictions += 1
            elif event.result.kind is ProspectiveAuditResultKind.ABSTENTION:
                abstentions += 1
            else:
                raise ProspectiveCalibrationUnavailableError()

            link, _tombstoned = _current_links_for_event(
                snapshot.link_envelopes,
                event.event_id,
            )
            if link is None:
                pending_unlinked_events += 1
                continue

            verification = _revalidate_calibration_link(event_envelope, link, self._reader)
            if verification.state is ProspectiveDecisionLinkStateV1.LINKED_VALID:
                if verification.link is None:
                    raise ProspectiveCalibrationUnavailableError()
                linked_actual_decisions += 1
                if event.result.kind is ProspectiveAuditResultKind.PREDICTION:
                    predicted_option_id = event.result.predicted_option_id
                    if predicted_option_id is None:
                        raise ProspectiveCalibrationUnavailableError()
                    mapped_indexes = tuple(
                        item.decision_option_index
                        for item in verification.link.mapping
                        if item.audit_option_id == predicted_option_id
                    )
                    if len(mapped_indexes) != 1:
                        raise ProspectiveCalibrationUnavailableError()
                    if mapped_indexes[0] == verification.link.actual_chosen_option_index:
                        exact_option_matches += 1
                    else:
                        mismatches += 1
                continue

            reason_code = verification.reason_code
            if reason_code is None:
                raise ProspectiveCalibrationUnavailableError()
            if verification.state is ProspectiveDecisionLinkStateV1.LINK_INVALID:
                if reason_code not in invalid_counts:
                    raise ProspectiveCalibrationUnavailableError()
                invalid_counts[reason_code] += 1
                invalid_linkage_events += 1
            elif verification.state is ProspectiveDecisionLinkStateV1.LINK_UNAVAILABLE:
                if reason_code not in unavailable_counts:
                    raise ProspectiveCalibrationUnavailableError()
                unavailable_counts[reason_code] += 1
                unavailable_linkage_events += 1
            else:
                raise ProspectiveCalibrationUnavailableError()

        if _calibration_is_cancelled(cancellation):
            raise ProspectiveCalibrationUnavailableError()
        result = _prospective_calibration_result(
            audited_operations=len(ordered_events),
            predictions=predictions,
            abstentions=abstentions,
            linked_actual_decisions=linked_actual_decisions,
            pending_unlinked_events=pending_unlinked_events,
            invalid_linkage_events=invalid_linkage_events,
            unavailable_linkage_events=unavailable_linkage_events,
            exact_option_matches=exact_option_matches,
            mismatches=mismatches,
            invalid_counts=invalid_counts,
            unavailable_counts=unavailable_counts,
        )
        serialize_prospective_calibration_result(result)
        return result


BuildProspectiveCalibrationV1 = BuildProspectiveCalibration
BuildProspectiveCalibrationAggregate = BuildProspectiveCalibration
BuildProspectiveCalibrationAggregateV1 = BuildProspectiveCalibration
ProspectiveCalibrationBuilder = BuildProspectiveCalibration


def build_prospective_calibration(
    store: ProspectiveAuditStore,
    reader: VaultReader | Callable[[], VaultSnapshot | ScanReport] | None = None,
    *,
    clock: Callable[[], datetime] | None = None,
    now: datetime | None = None,
    cancellation: object | None = None,
) -> ProspectiveCalibrationResultV1:
    """Functional facade for the provider-free Stage 9C aggregate core."""

    return BuildProspectiveCalibration(store, reader, clock=clock).execute(
        now=now,
        cancellation=cancellation,
    )


build_prospective_calibration_result = build_prospective_calibration
serialize_prospective_calibration = serialize_prospective_calibration_result


__all__.extend(
    [
        "CALIBRATION_DERIVATION_VERSION",
        "CALIBRATION_POLICY_CANONICAL_JSON",
        "CALIBRATION_POLICY_FINGERPRINT",
        "CALIBRATION_POLICY_ID",
        "MAX_RESULT_BYTES_V1",
        "PROSPECTIVE_CALIBRATION_DERIVATION_VERSION",
        "PROSPECTIVE_CALIBRATION_INVALID_LINKAGE_CODES_V1",
        "PROSPECTIVE_CALIBRATION_MAX_RESULT_BYTES_V1",
        "PROSPECTIVE_CALIBRATION_POLICY_CANONICAL_JSON",
        "PROSPECTIVE_CALIBRATION_POLICY_FINGERPRINT",
        "PROSPECTIVE_CALIBRATION_POLICY_ID",
        "PROSPECTIVE_CALIBRATION_RETENTION_POLICY",
        "PROSPECTIVE_CALIBRATION_UNAVAILABLE_LINKAGE_CODES_V1",
        "BuildProspectiveCalibration",
        "BuildProspectiveCalibrationAggregate",
        "BuildProspectiveCalibrationAggregateV1",
        "BuildProspectiveCalibrationV1",
        "ProspectiveCalibrationBuilder",
        "ProspectiveCalibrationCountV1",
        "ProspectiveCalibrationError",
        "ProspectiveCalibrationErrorCode",
        "ProspectiveCalibrationErrorCodeV1",
        "ProspectiveCalibrationInvalidCodeV1",
        "ProspectiveCalibrationInvalidLinkageCodeV1",
        "ProspectiveCalibrationMetricsV1",
        "ProspectiveCalibrationRatioV1",
        "ProspectiveCalibrationRequestV1",
        "ProspectiveCalibrationResultTooLargeError",
        "ProspectiveCalibrationResultV1",
        "ProspectiveCalibrationSourceUnavailableError",
        "ProspectiveCalibrationUnavailableCodeV1",
        "ProspectiveCalibrationUnavailableError",
        "ProspectiveCalibrationUnavailableLinkageCodeV1",
        "build_prospective_calibration",
        "build_prospective_calibration_result",
        "serialize_prospective_calibration",
        "serialize_prospective_calibration_result",
        "serialize_prospective_calibration_result_v1",
        "validate_prospective_calibration_policy",
        "validate_prospective_calibration_result",
    ]
)
