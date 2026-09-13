"""Explicit Stated-vs-Observed mapping and composition runtime v1.

This module is intentionally a separate operational boundary.  It consumes
the current Stage 4 and Stage 10A/10B read models, but it does not alter the
vault, reuse the Stage 9 store, call a provider, or expose a transport API.

The durable part is an append-only, locally locked JSONL store.  The review
projection is deliberately transient and is never accepted as an authority:
only server-rebuilt identities are persisted.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import tempfile
from collections.abc import Callable, Iterable, Iterator, Mapping
from contextlib import contextmanager, suppress
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any, Final, cast
from uuid import UUID, uuid7

from second_brain.application.behavioral_observation import (
    CONTRACT_VERSION as BEHAVIORAL_CONTRACT_VERSION,
)
from second_brain.application.behavioral_observation import (
    DEFAULT_BEHAVIORAL_OBSERVATION_POLICY,
    OBSERVATION_VERSION,
    BehavioralCohortBucketV1,
    BehavioralCohortIdentityV1,
    BehavioralObservationBuildResultV1,
    BehavioralObservationV1,
    BehavioralOptionIdentityV1,
    BuildBehavioralObservations,
)
from second_brain.application.behavioral_observation import (
    FULL_DERIVATION_VERSION as BEHAVIORAL_DERIVATION_VERSION,
)
from second_brain.application.behavioral_observation import (
    POLICY_FINGERPRINT as BEHAVIORAL_POLICY_FINGERPRINT,
)
from second_brain.application.behavioral_observation import (
    POLICY_ID as BEHAVIORAL_POLICY_ID,
)
from second_brain.application.behavioral_self_model import (
    DEFAULT_BEHAVIORAL_SELF_MODEL_POLICY,
    BehavioralPatternStateV1,
    BehavioralPatternTypeV1,
    BehavioralSelfModelError,
    BehavioralSelfModelResultV1,
    BuildBehavioralSelfModel,
)
from second_brain.application.ports import VaultReader
from second_brain.application.reports import ScanReport, VaultSnapshot
from second_brain.application.self_model import (
    DEFAULT_SELF_MODEL_POLICY,
    BuildSelfModel,
    SelfModelClaim,
    SelfModelError,
    SelfModelRequest,
    SelfModelResult,
    validate_self_model_policy,
)
from second_brain.application.validation import build_report
from second_brain.domain.models import (
    EvidenceAtPrecision,
    EvidenceKind,
    SelfKind,
    parse_rfc3339,
)

type MappingHashV1 = str
type StatedObservedMappingClock = Callable[[], datetime]

CONTRACT_VERSION: Final[str] = "stated-observed-mapping-v1"
MAPPING_POLICY_ID: Final[str] = "stated-observed-explicit-mapping-v1"
MAPPING_DERIVATION: Final[str] = "stated-observed-composition-derivation-v1"
MAPPING_BASIS: Final[str] = "owner-explicit-stated-behavior-v1"
CARDINALITY_POLICY: Final[str] = "one-stated-one-cohort-one-option-v1"
DIMENSION_POLICY: Final[str] = "preference-only-v1"
COMPARISON_POLICY: Final[str] = "current-exact-option-only-v1"
TEMPORAL_POLICY: Final[str] = "current-build-no-evidence-time-inference-v1"
PERSISTENCE_POLICY: Final[str] = "dedicated-operational-mapping-store-v1"
STORE_POLICY: Final[str] = "append-only-local-jsonl-v1"

MAPPING_POLICY_CANONICAL_JSON: Final[str] = (
    '{"basis":"owner-explicit-stated-behavior-v1",'
    '"cardinality":"one-stated-one-cohort-one-option-v1",'
    '"comparison":"current-exact-option-only-v1",'
    '"contract":"stated-observed-mapping-v1",'
    '"dimension":"preference-only-v1",'
    '"persistence":"dedicated-operational-mapping-store-v1",'
    '"store":"append-only-local-jsonl-v1",'
    '"temporal":"current-build-no-evidence-time-inference-v1",'
    '"version":"1"}'
)
MAPPING_POLICY_FINGERPRINT: Final[MappingHashV1] = (
    "sha256:ee9174fbbaf3d9913c4c5098e3e61d16f64abef4857b0f2716f1b4c4cd6e5e9d"
)

DERIVATION_VERSION: Final[str] = MAPPING_DERIVATION
POLICY_ID: Final[str] = MAPPING_POLICY_ID
POLICY_FINGERPRINT: Final[MappingHashV1] = MAPPING_POLICY_FINGERPRINT

MAX_REVIEW_PROJECTION_BYTES: Final[int] = 32_768
MAX_ACTIVE_MAPPINGS: Final[int] = 200
MAX_MAPPING_RECORD_BYTES: Final[int] = 16_384
MAX_MAPPING_DOMAIN_BYTES: Final[int] = 64

PRODUCTION_STATED_OBSERVED_MAPPING_STORE_ROOT: Final[Path] = Path(
    "/srv/second-brain/runtime/stated-observed-mapping"
)
PRODUCTION_STATED_OBSERVED_MAPPING_OWNER_GROUP: Final[tuple[str, str]] = (
    "second-brain",
    "second-brain",
)

_HASH_PATTERN: Final[re.Pattern[str]] = re.compile(r"sha256:[0-9a-f]{64}\Z", re.ASCII)
_RAW_HASH_PATTERN: Final[re.Pattern[str]] = re.compile(r"[0-9a-f]{64}\Z", re.ASCII)
_DOMAIN_PATTERN: Final[re.Pattern[str]] = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*\Z", re.ASCII)
_STORE_FORMAT_VERSION: Final[int] = 1
_RECORD_FILE_NAME: Final[str] = "mappings.jsonl"
_MANIFEST_FILE_NAME: Final[str] = "manifest.json"
_LOCK_FILE_NAME: Final[str] = ".store.lock"
_LIFECYCLE_REASON_CODES: Final[frozenset[str]] = frozenset(
    {
        "explicit_correction",
        "explicit_invalidation",
        "explicit_delete",
        "explicit_reset",
    }
)


class MappingLifecycleStateV1(StrEnum):
    """Closed append-only lifecycle projection."""

    ACTIVE = "active"
    SUPERSEDED = "superseded"
    INVALIDATED = "invalidated"
    DELETED = "deleted"


class StatedObservedCompositionStateV1(StrEnum):
    """Closed composition states; no implicit truth or personality verdict."""

    ALIGNED = "aligned"
    DIVERGENT = "divergent"
    STATED_EVIDENCE_MISSING = "stated_evidence_missing"
    BEHAVIORAL_EVIDENCE_INSUFFICIENT = "behavioral_evidence_insufficient"
    NOT_COMPARABLE = "not_comparable"


MappingCompositionStateV1 = StatedObservedCompositionStateV1


class StatedObservedMappingCaveatCodeV1(StrEnum):
    """Fixed temporal/comparison caveats from the Stage 10C contract."""

    CURRENT_SOURCE_REVALIDATED = "current_source_revalidated"
    REVIEW_TIME_IS_NOT_EVIDENCE_TIME = "review_time_is_not_evidence_time"
    TEMPORAL_ALIGNMENT_NOT_PROVEN = "temporal_alignment_not_proven"
    STATED_EVIDENCE_TIME_UNKNOWN = "stated_evidence_time_unknown"
    HISTORICAL_CONTEXT_NOT_BINARY = "historical_context_not_binary"


class StatedObservedMappingErrorCode(StrEnum):
    """Stable fixed error vocabulary; values never contain private details."""

    INVALID_REQUEST = "STATED_OBSERVED_MAPPING_INVALID_REQUEST"
    STATED_SOURCE_UNAVAILABLE = "STATED_OBSERVED_MAPPING_STATED_SOURCE_UNAVAILABLE"
    STATED_SOURCE_CHANGED = "STATED_OBSERVED_MAPPING_STATED_SOURCE_CHANGED"
    UNSUPPORTED_STATED_DIMENSION = "STATED_OBSERVED_MAPPING_UNSUPPORTED_STATED_DIMENSION"
    BEHAVIORAL_SOURCE_UNAVAILABLE = "STATED_OBSERVED_MAPPING_BEHAVIORAL_SOURCE_UNAVAILABLE"
    BEHAVIORAL_COHORT_CHANGED = "STATED_OBSERVED_MAPPING_BEHAVIORAL_COHORT_CHANGED"
    BEHAVIORAL_OPTION_CHANGED = "STATED_OBSERVED_MAPPING_BEHAVIORAL_OPTION_CHANGED"
    BEHAVIORAL_EVIDENCE_INSUFFICIENT = "STATED_OBSERVED_MAPPING_BEHAVIORAL_EVIDENCE_INSUFFICIENT"
    MISSING = "STATED_OBSERVED_MAPPING_MISSING"
    INVALID = "STATED_OBSERVED_MAPPING_INVALID"
    STALE = "STATED_OBSERVED_MAPPING_STALE"
    AMBIGUOUS = "STATED_OBSERVED_MAPPING_AMBIGUOUS"
    POLICY_MISMATCH = "STATED_OBSERVED_MAPPING_POLICY_MISMATCH"
    NOT_COMPARABLE = "STATED_OBSERVED_MAPPING_NOT_COMPARABLE"
    RESULT_TOO_LARGE = "STATED_OBSERVED_MAPPING_RESULT_TOO_LARGE"
    STORE_UNAVAILABLE = "STATED_OBSERVED_MAPPING_STORE_UNAVAILABLE"
    STORE_CORRUPT = "STATED_OBSERVED_MAPPING_STORE_CORRUPT"
    IDEMPOTENCY_CONFLICT = "STATED_OBSERVED_MAPPING_IDEMPOTENCY_CONFLICT"
    CONCURRENCY_CONFLICT = "STATED_OBSERVED_MAPPING_CONCURRENCY_CONFLICT"
    STATED_EVIDENCE_MISSING = "STATED_OBSERVED_MAPPING_STATED_EVIDENCE_MISSING"

    # Descriptive aliases used by callers that spell the composition reason.
    AMBIGUOUS_MAPPING = AMBIGUOUS
    MAPPING_INVALID = INVALID
    MAPPING_STALE = STALE


MappingErrorCode = StatedObservedMappingErrorCode


_ERROR_MESSAGES: Final[dict[StatedObservedMappingErrorCode, str]] = {
    StatedObservedMappingErrorCode.INVALID_REQUEST: (
        "the stated-observed mapping request is invalid"
    ),
    StatedObservedMappingErrorCode.STATED_SOURCE_UNAVAILABLE: (
        "the current stated source is unavailable"
    ),
    StatedObservedMappingErrorCode.STATED_SOURCE_CHANGED: "the current stated source changed",
    StatedObservedMappingErrorCode.UNSUPPORTED_STATED_DIMENSION: (
        "the stated dimension is unsupported"
    ),
    StatedObservedMappingErrorCode.BEHAVIORAL_SOURCE_UNAVAILABLE: (
        "the current behavioral source is unavailable"
    ),
    StatedObservedMappingErrorCode.BEHAVIORAL_COHORT_CHANGED: "the behavioral cohort changed",
    StatedObservedMappingErrorCode.BEHAVIORAL_OPTION_CHANGED: "the behavioral option changed",
    StatedObservedMappingErrorCode.BEHAVIORAL_EVIDENCE_INSUFFICIENT: (
        "behavioral evidence is insufficient"
    ),
    StatedObservedMappingErrorCode.MISSING: "no accepted stated-observed mapping exists",
    StatedObservedMappingErrorCode.INVALID: "the accepted stated-observed mapping is invalid",
    StatedObservedMappingErrorCode.STALE: "the accepted stated-observed mapping is stale",
    StatedObservedMappingErrorCode.AMBIGUOUS: "the accepted stated-observed mapping is ambiguous",
    StatedObservedMappingErrorCode.POLICY_MISMATCH: "the stated-observed mapping policy is invalid",
    StatedObservedMappingErrorCode.NOT_COMPARABLE: "the current sources are not comparable",
    StatedObservedMappingErrorCode.RESULT_TOO_LARGE: (
        "the stated-observed result exceeds its bounded limit"
    ),
    StatedObservedMappingErrorCode.STORE_UNAVAILABLE: (
        "the stated-observed mapping store is unavailable"
    ),
    StatedObservedMappingErrorCode.STORE_CORRUPT: "the stated-observed mapping store is corrupt",
    StatedObservedMappingErrorCode.IDEMPOTENCY_CONFLICT: (
        "the mapping operation conflicts with an earlier operation"
    ),
    StatedObservedMappingErrorCode.CONCURRENCY_CONFLICT: (
        "the mapping operation conflicts with current state"
    ),
    StatedObservedMappingErrorCode.STATED_EVIDENCE_MISSING: "the mapped stated evidence is missing",
}


class StatedObservedMappingError(RuntimeError):
    """Safe boundary error; the message contains no path, body, or UUID list."""

    def __init__(self, code: StatedObservedMappingErrorCode | str) -> None:
        try:
            normalized = (
                code
                if isinstance(code, StatedObservedMappingErrorCode)
                else StatedObservedMappingErrorCode(code)
            )
        except TypeError, ValueError:
            normalized = StatedObservedMappingErrorCode.INVALID
        self.code = normalized.value
        self.message = _ERROR_MESSAGES[normalized]
        super().__init__(self.message)

    def as_dict(self) -> dict[str, str]:
        return {"code": self.code, "message": self.message}


class StatedObservedMappingStoreUnavailableError(StatedObservedMappingError):
    def __init__(self) -> None:
        super().__init__(StatedObservedMappingErrorCode.STORE_UNAVAILABLE)


class StatedObservedMappingStoreCorruptError(StatedObservedMappingError):
    def __init__(self) -> None:
        super().__init__(StatedObservedMappingErrorCode.STORE_CORRUPT)


class StatedObservedMappingInvalidRequestError(StatedObservedMappingError):
    def __init__(self) -> None:
        super().__init__(StatedObservedMappingErrorCode.INVALID_REQUEST)


class StatedObservedMappingIdempotencyConflictError(StatedObservedMappingError):
    def __init__(self) -> None:
        super().__init__(StatedObservedMappingErrorCode.IDEMPOTENCY_CONFLICT)


class StatedObservedMappingConcurrencyConflictError(StatedObservedMappingError):
    def __init__(self) -> None:
        super().__init__(StatedObservedMappingErrorCode.CONCURRENCY_CONFLICT)


class StatedObservedMappingPolicyMismatchError(StatedObservedMappingError):
    def __init__(self) -> None:
        super().__init__(StatedObservedMappingErrorCode.POLICY_MISMATCH)


class StatedObservedMappingResultTooLargeError(StatedObservedMappingError):
    def __init__(self) -> None:
        super().__init__(StatedObservedMappingErrorCode.RESULT_TOO_LARGE)


def _canonical_json(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
        allow_nan=False,
    )


def mapping_hash_json(value: object) -> MappingHashV1:
    """Hash canonical UTF-8 JSON using the Stage 10C ``sha256:`` form."""

    try:
        encoded = _canonical_json(value).encode("utf-8")
    except TypeError, ValueError, UnicodeError:
        raise ValueError("mapping value is not canonical JSON") from None
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def mapping_hash_text(value: str) -> MappingHashV1:
    if type(value) is not str:
        raise ValueError("mapping text is invalid")
    try:
        encoded = value.encode("utf-8")
    except UnicodeEncodeError:
        raise ValueError("mapping text is invalid") from None
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def validate_mapping_hash(value: object) -> MappingHashV1:
    if type(value) is not str or _HASH_PATTERN.fullmatch(value) is None:
        raise ValueError("mapping hash is invalid")
    return value


def _validate_raw_policy_hash(value: object) -> str:
    if type(value) is not str or _RAW_HASH_PATTERN.fullmatch(value) is None:
        raise ValueError("policy fingerprint is invalid")
    return value


def _uuid7(value: object) -> UUID:
    if isinstance(value, UUID):
        result = value
    elif type(value) is str:
        try:
            result = UUID(value)
        except ValueError:
            raise ValueError("UUID is invalid") from None
        if str(result) != value:
            raise ValueError("UUID is invalid")
    else:
        raise ValueError("UUID is invalid")
    if result.version != 7:
        raise ValueError("UUID is invalid")
    return result


def _utc(value: object) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("timestamp is invalid")
    return value.astimezone(UTC)


def _timestamp(value: datetime) -> str:
    normalized = _utc(value)
    return normalized.isoformat(timespec="microseconds" if normalized.microsecond else "seconds")


def _parse_timestamp(value: object) -> datetime:
    if type(value) is not str:
        raise ValueError("timestamp is invalid")
    try:
        parsed = parse_rfc3339(value)
    except ValueError:
        raise ValueError("timestamp is invalid") from None
    normalized = _utc(parsed)
    if _timestamp(normalized) != _timestamp(parsed):
        raise ValueError("timestamp is invalid")
    return normalized


def _domain(value: object) -> str:
    if type(value) is not str or not value or len(value.encode("utf-8")) > MAX_MAPPING_DOMAIN_BYTES:
        raise ValueError("domain is invalid")
    if _DOMAIN_PATTERN.fullmatch(value) is None:
        raise ValueError("domain is invalid")
    return value


def _enum_value(value: object, enum_type: type[StrEnum], message: str) -> str:
    if isinstance(value, enum_type):
        return value.value
    if type(value) is str:
        try:
            return enum_type(value).value
        except ValueError:
            pass
    raise ValueError(message)


def _enum_member(value: object, enum_type: type[StrEnum], message: str) -> StrEnum:
    if isinstance(value, enum_type):
        return value
    if type(value) is str:
        try:
            return enum_type(value)
        except ValueError:
            pass
    raise ValueError(message)


def _enum_text(value: object) -> str:
    return value.value if isinstance(value, StrEnum) else cast(str, value)


def _mapping_policy_payload() -> dict[str, str]:
    return {
        "basis": MAPPING_BASIS,
        "cardinality": CARDINALITY_POLICY,
        "comparison": COMPARISON_POLICY,
        "contract": CONTRACT_VERSION,
        "dimension": DIMENSION_POLICY,
        "persistence": PERSISTENCE_POLICY,
        "store": STORE_POLICY,
        "temporal": TEMPORAL_POLICY,
        "version": "1",
    }


def validate_mapping_policy() -> str:
    """Validate the compiled policy binding and return its exact fingerprint."""

    if _canonical_json(_mapping_policy_payload()) != MAPPING_POLICY_CANONICAL_JSON:
        raise StatedObservedMappingPolicyMismatchError()
    if mapping_hash_json(_mapping_policy_payload()) != MAPPING_POLICY_FINGERPRINT:
        raise StatedObservedMappingPolicyMismatchError()
    return MAPPING_POLICY_FINGERPRINT


@dataclass(frozen=True, slots=True)
class StatedAssertionIdentityV1:
    """Current exact Stage 4 direct preference identity."""

    source_note_uuid: UUID
    dimension: str
    source_evidence_kind: str
    source_self_kind: str
    domain: str
    evidence_at: datetime | str
    evidence_at_precision: str
    source_contract_version: str
    source_derivation_version: str
    self_model_policy_fingerprint: str
    source_fingerprint: MappingHashV1
    claim_fingerprint: MappingHashV1

    def __post_init__(self) -> None:
        object.__setattr__(self, "source_note_uuid", _uuid7(self.source_note_uuid))
        if self.dimension != SelfKind.PREFERENCE.value:
            raise ValueError("stated dimension is invalid")
        object.__setattr__(
            self,
            "source_evidence_kind",
            _enum_value(self.source_evidence_kind, EvidenceKind, "stated evidence kind is invalid"),
        )
        if self.source_evidence_kind not in {
            EvidenceKind.EXPLICIT_USER_FACT.value,
            EvidenceKind.USER_STATEMENT.value,
        }:
            raise ValueError("stated evidence kind is invalid")
        if self.source_self_kind != SelfKind.PREFERENCE.value:
            raise ValueError("stated self kind is invalid")
        object.__setattr__(self, "domain", _domain(self.domain))
        if self.evidence_at == "unknown":
            if self.evidence_at_precision != EvidenceAtPrecision.UNKNOWN.value:
                raise ValueError("stated evidence precision is invalid")
        elif isinstance(self.evidence_at, datetime):
            object.__setattr__(self, "evidence_at", _utc(self.evidence_at))
            if self.evidence_at_precision != EvidenceAtPrecision.EXACT.value:
                raise ValueError("stated evidence precision is invalid")
        else:
            raise ValueError("stated evidence time is invalid")
        if self.source_contract_version != "self-model-v1":
            raise ValueError("stated source contract is invalid")
        if self.source_derivation_version != "self-model-derivation-v1":
            raise ValueError("stated source derivation is invalid")
        _validate_raw_policy_hash(self.self_model_policy_fingerprint)
        validate_mapping_hash(self.source_fingerprint)
        validate_mapping_hash(self.claim_fingerprint)

    def as_dict(self) -> dict[str, object]:
        return {
            "source_note_uuid": str(self.source_note_uuid),
            "dimension": self.dimension,
            "source_evidence_kind": self.source_evidence_kind,
            "source_self_kind": self.source_self_kind,
            "domain": self.domain,
            "evidence_at": _timestamp(self.evidence_at)
            if isinstance(self.evidence_at, datetime)
            else "unknown",
            "evidence_at_precision": self.evidence_at_precision,
            "source_contract_version": self.source_contract_version,
            "source_derivation_version": self.source_derivation_version,
            "self_model_policy_fingerprint": self.self_model_policy_fingerprint,
            "source_fingerprint": self.source_fingerprint,
            "claim_fingerprint": self.claim_fingerprint,
        }


@dataclass(frozen=True, slots=True)
class BehavioralComparisonIdentityV1:
    """Exact Stage 10A/10B identity accepted by the mapping boundary."""

    behavioral_contract_version: str
    behavioral_derivation_version: str
    observation_version: str
    policy_id: str
    policy_fingerprint: MappingHashV1
    cohort: BehavioralCohortIdentityV1
    option: BehavioralOptionIdentityV1
    comparison_subject: str
    pattern_type: BehavioralPatternTypeV1 | str
    pattern_state: BehavioralPatternStateV1 | str
    pattern_fingerprint: MappingHashV1
    source_fingerprint: MappingHashV1
    provenance_fingerprint: MappingHashV1
    source_count: int

    def __post_init__(self) -> None:
        if self.behavioral_contract_version != BEHAVIORAL_CONTRACT_VERSION:
            raise ValueError("behavioral contract is invalid")
        if self.behavioral_derivation_version != BEHAVIORAL_DERIVATION_VERSION:
            raise ValueError("behavioral derivation is invalid")
        if self.observation_version != OBSERVATION_VERSION:
            raise ValueError("behavioral observation version is invalid")
        if self.policy_id != BEHAVIORAL_POLICY_ID:
            raise ValueError("behavioral policy is invalid")
        validate_mapping_hash(self.policy_fingerprint)
        if self.policy_fingerprint != BEHAVIORAL_POLICY_FINGERPRINT:
            raise ValueError("behavioral policy fingerprint is invalid")
        if type(self.cohort) is not BehavioralCohortIdentityV1:
            raise ValueError("behavioral cohort is invalid")
        if type(self.option) is not BehavioralOptionIdentityV1:
            raise ValueError("behavioral option is invalid")
        if self.option.option_index >= len(
            _cohort_option_fingerprint_placeholder(self.cohort, self.option)
        ):
            # The helper returns a tuple with the selected fingerprint only for
            # backward-compatible construction; namespace membership is checked
            # again by the runtime using Stage 10A's live namespace.
            raise ValueError("behavioral option is invalid")
        if self.comparison_subject != "current-exact-option-v1":
            raise ValueError("behavioral comparison subject is invalid")
        object.__setattr__(
            self,
            "pattern_type",
            _enum_member(
                self.pattern_type, BehavioralPatternTypeV1, "behavioral pattern type is invalid"
            ),
        )
        object.__setattr__(
            self,
            "pattern_state",
            _enum_member(
                self.pattern_state, BehavioralPatternStateV1, "behavioral pattern state is invalid"
            ),
        )
        _validate_pattern_pair(
            cast(BehavioralPatternTypeV1, self.pattern_type),
            cast(BehavioralPatternStateV1, self.pattern_state),
        )
        for value in (
            self.pattern_fingerprint,
            self.source_fingerprint,
            self.provenance_fingerprint,
        ):
            validate_mapping_hash(value)
        if type(self.source_count) is not int or self.source_count < 0:
            raise ValueError("behavioral source count is invalid")

    def as_dict(self) -> dict[str, object]:
        return {
            "behavioral_contract_version": self.behavioral_contract_version,
            "behavioral_derivation_version": self.behavioral_derivation_version,
            "observation_version": self.observation_version,
            "policy_id": self.policy_id,
            "policy_fingerprint": self.policy_fingerprint,
            "cohort": self.cohort.as_dict(),
            "option": self.option.as_dict(),
            "comparison_subject": self.comparison_subject,
            "pattern_type": _enum_text(self.pattern_type),
            "pattern_state": _enum_text(self.pattern_state),
            "pattern_fingerprint": self.pattern_fingerprint,
            "source_fingerprint": self.source_fingerprint,
            "provenance_fingerprint": self.provenance_fingerprint,
            "source_count": self.source_count,
        }


def _cohort_option_fingerprint_placeholder(
    cohort: BehavioralCohortIdentityV1,
    option: BehavioralOptionIdentityV1,
) -> tuple[str, ...]:
    """Keep DTO construction independent of raw option labels.

    The cohort DTO intentionally carries only the option namespace digest, not
    its ordered labels.  Exact namespace membership is therefore a live Stage
    10A check, performed by ``_resolve_behavioral`` before construction.  This
    helper is only a bounded structural guard on option indices.
    """

    del cohort, option
    return tuple("_" for _ in range(20))


def _validate_pattern_pair(
    pattern_type: BehavioralPatternTypeV1,
    pattern_state: BehavioralPatternStateV1,
) -> None:
    expected: dict[BehavioralPatternTypeV1, frozenset[BehavioralPatternStateV1]] = {
        BehavioralPatternTypeV1.REPEATED_EXACT_CHOICE: frozenset(
            {BehavioralPatternStateV1.CURRENT, BehavioralPatternStateV1.HISTORICAL}
        ),
        BehavioralPatternTypeV1.MIXED_EXACT_CHOICES: frozenset({BehavioralPatternStateV1.MIXED}),
        BehavioralPatternTypeV1.STABLE_OVER_TIME: frozenset({BehavioralPatternStateV1.STABLE}),
        BehavioralPatternTypeV1.CHANGED_OVER_TIME: frozenset({BehavioralPatternStateV1.CHANGED}),
        BehavioralPatternTypeV1.INSUFFICIENT_EVIDENCE: frozenset(
            {BehavioralPatternStateV1.INSUFFICIENT}
        ),
        BehavioralPatternTypeV1.NOT_COMPARABLE: frozenset(
            {BehavioralPatternStateV1.NOT_COMPARABLE}
        ),
    }
    if pattern_state not in expected[pattern_type]:
        raise ValueError("behavioral pattern state is invalid")


def mapping_fingerprint_payload(
    stated: StatedAssertionIdentityV1,
    behavioral: BehavioralComparisonIdentityV1,
) -> dict[str, object]:
    return {
        "behavioral": {
            "behavioral_contract_version": behavioral.behavioral_contract_version,
            "behavioral_derivation_version": behavioral.behavioral_derivation_version,
            "cohort": _canonical_json(behavioral.cohort.as_dict()),
            "comparison_subject": behavioral.comparison_subject,
            "option": _canonical_json(behavioral.option.as_dict()),
            "pattern_fingerprint": behavioral.pattern_fingerprint,
            "pattern_state": _enum_text(behavioral.pattern_state),
            "pattern_type": _enum_text(behavioral.pattern_type),
            "policy_fingerprint": behavioral.policy_fingerprint,
            "provenance_fingerprint": behavioral.provenance_fingerprint,
            "source_count": behavioral.source_count,
            "source_fingerprint": behavioral.source_fingerprint,
        },
        "contract_version": CONTRACT_VERSION,
        "mapping_basis": MAPPING_BASIS,
        "mapping_policy_id": MAPPING_POLICY_ID,
        "stated": _canonical_json(stated.as_dict()),
    }


def compute_mapping_fingerprint(
    stated: StatedAssertionIdentityV1,
    behavioral: BehavioralComparisonIdentityV1,
) -> MappingHashV1:
    return mapping_hash_json(mapping_fingerprint_payload(stated, behavioral))


@dataclass(frozen=True, slots=True)
class StatedObservedMappingV1:
    """Immutable accepted mapping record; lifecycle is stored separately."""

    contract_version: str
    mapping_policy_id: str
    mapping_policy_fingerprint: MappingHashV1
    mapping_id: UUID
    acceptance_operation_id_fingerprint: MappingHashV1
    created_at: datetime
    reviewed_at: datetime
    mapping_basis: str
    stated: StatedAssertionIdentityV1
    behavioral: BehavioralComparisonIdentityV1
    mapping_fingerprint: MappingHashV1
    supersedes_mapping_id: UUID | None

    def __post_init__(self) -> None:
        if self.contract_version != CONTRACT_VERSION or self.mapping_policy_id != MAPPING_POLICY_ID:
            raise ValueError("mapping policy is invalid")
        validate_mapping_hash(self.mapping_policy_fingerprint)
        if self.mapping_policy_fingerprint != MAPPING_POLICY_FINGERPRINT:
            raise ValueError("mapping policy fingerprint is invalid")
        object.__setattr__(self, "mapping_id", _uuid7(self.mapping_id))
        validate_mapping_hash(self.acceptance_operation_id_fingerprint)
        object.__setattr__(self, "created_at", _utc(self.created_at))
        object.__setattr__(self, "reviewed_at", _utc(self.reviewed_at))
        if self.reviewed_at > self.created_at:
            raise ValueError("mapping timestamps are invalid")
        if self.mapping_basis != MAPPING_BASIS:
            raise ValueError("mapping basis is invalid")
        if (
            type(self.stated) is not StatedAssertionIdentityV1
            or type(self.behavioral) is not BehavioralComparisonIdentityV1
        ):
            raise ValueError("mapping identities are invalid")
        if self.stated.domain != self.behavioral.cohort.domain:
            raise ValueError("mapping domains are invalid")
        validate_mapping_hash(self.mapping_fingerprint)
        if self.mapping_fingerprint != compute_mapping_fingerprint(self.stated, self.behavioral):
            raise ValueError("mapping fingerprint is invalid")
        if self.supersedes_mapping_id is not None:
            object.__setattr__(self, "supersedes_mapping_id", _uuid7(self.supersedes_mapping_id))

    def as_dict(self) -> dict[str, object]:
        return {
            "contract_version": self.contract_version,
            "mapping_policy_id": self.mapping_policy_id,
            "mapping_policy_fingerprint": self.mapping_policy_fingerprint,
            "mapping_id": str(self.mapping_id),
            "acceptance_operation_id_fingerprint": self.acceptance_operation_id_fingerprint,
            "created_at": _timestamp(self.created_at),
            "reviewed_at": _timestamp(self.reviewed_at),
            "mapping_basis": self.mapping_basis,
            "stated": self.stated.as_dict(),
            "behavioral": self.behavioral.as_dict(),
            "mapping_fingerprint": self.mapping_fingerprint,
            "supersedes_mapping_id": (
                str(self.supersedes_mapping_id) if self.supersedes_mapping_id is not None else None
            ),
        }


@dataclass(frozen=True, slots=True)
class MappingLifecycleViewV1:
    mapping_id: UUID
    lifecycle_state: MappingLifecycleStateV1 | str
    record: StatedObservedMappingV1

    def __post_init__(self) -> None:
        object.__setattr__(self, "mapping_id", _uuid7(self.mapping_id))
        object.__setattr__(
            self,
            "lifecycle_state",
            _enum_member(
                self.lifecycle_state, MappingLifecycleStateV1, "lifecycle state is invalid"
            ),
        )
        if (
            type(self.record) is not StatedObservedMappingV1
            or self.mapping_id != self.record.mapping_id
        ):
            raise ValueError("lifecycle record is invalid")

    def as_dict(self) -> dict[str, object]:
        return {
            "mapping_id": str(self.mapping_id),
            "lifecycle_state": _enum_text(self.lifecycle_state),
            "record": self.record.as_dict(),
        }


@dataclass(frozen=True, slots=True)
class MappingLifecycleEventV1:
    """Append-only lifecycle event for one immutable accepted record."""

    event_id: UUID
    mapping_id: UUID
    lifecycle_state: MappingLifecycleStateV1 | str
    occurred_at: datetime
    operation_id_fingerprint: MappingHashV1
    reason_code: str | None = None
    replacement_mapping_id: UUID | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "event_id", _uuid7(self.event_id))
        object.__setattr__(self, "mapping_id", _uuid7(self.mapping_id))
        state = _enum_member(
            self.lifecycle_state, MappingLifecycleStateV1, "lifecycle state is invalid"
        )
        if state is MappingLifecycleStateV1.ACTIVE:
            raise ValueError("lifecycle event state is invalid")
        object.__setattr__(self, "lifecycle_state", state)
        object.__setattr__(self, "occurred_at", _utc(self.occurred_at))
        validate_mapping_hash(self.operation_id_fingerprint)
        if self.reason_code is not None and self.reason_code not in _LIFECYCLE_REASON_CODES:
            raise ValueError("lifecycle reason is invalid")
        if self.replacement_mapping_id is not None:
            object.__setattr__(self, "replacement_mapping_id", _uuid7(self.replacement_mapping_id))

    def as_dict(self) -> dict[str, object]:
        return {
            "event_id": str(self.event_id),
            "mapping_id": str(self.mapping_id),
            "lifecycle_state": _enum_text(self.lifecycle_state),
            "occurred_at": _timestamp(self.occurred_at),
            "operation_id_fingerprint": self.operation_id_fingerprint,
            "reason_code": self.reason_code,
            "replacement_mapping_id": (
                str(self.replacement_mapping_id)
                if self.replacement_mapping_id is not None
                else None
            ),
        }


@dataclass(frozen=True, slots=True)
class MappingStoreRecordEnvelopeV1:
    """Integrity envelope around a mapping or lifecycle record."""

    generation: UUID
    sequence: int
    record_type: str
    record: StatedObservedMappingV1 | MappingLifecycleEventV1
    previous_digest: MappingHashV1 | None
    current_digest: MappingHashV1

    def __post_init__(self) -> None:
        object.__setattr__(self, "generation", _uuid7(self.generation))
        if type(self.sequence) is not int or self.sequence < 1:
            raise ValueError("store sequence is invalid")
        if self.record_type not in {"accepted_mapping", "lifecycle_event"}:
            raise ValueError("store record type is invalid")
        if (
            self.record_type == "accepted_mapping"
            and type(self.record) is not StatedObservedMappingV1
        ):
            raise ValueError("store record is invalid")
        if (
            self.record_type == "lifecycle_event"
            and type(self.record) is not MappingLifecycleEventV1
        ):
            raise ValueError("store record is invalid")
        if self.previous_digest is not None:
            validate_mapping_hash(self.previous_digest)
        validate_mapping_hash(self.current_digest)

    def digest_payload(self) -> dict[str, object]:
        return {
            "generation": str(self.generation),
            "previous_digest": self.previous_digest,
            "record": self.record.as_dict(),
            "record_type": self.record_type,
            "sequence": self.sequence,
        }

    def as_dict(self) -> dict[str, object]:
        return {
            **self.digest_payload(),
            "current_digest": self.current_digest,
        }


@dataclass(frozen=True, slots=True)
class MappingStoreManifestV1:
    """Atomic manifest for the verified append-only stream."""

    format_version: int
    contract_version: str
    generation: UUID
    next_sequence: int
    record_count: int
    head_digest: MappingHashV1 | None
    active_mapping_count: int
    manifest_fingerprint: MappingHashV1

    def __post_init__(self) -> None:
        if (
            self.format_version != _STORE_FORMAT_VERSION
            or self.contract_version != CONTRACT_VERSION
        ):
            raise ValueError("store manifest is invalid")
        object.__setattr__(self, "generation", _uuid7(self.generation))
        if type(self.next_sequence) is not int or self.next_sequence < 1:
            raise ValueError("store manifest is invalid")
        if type(self.record_count) is not int or self.record_count < 0:
            raise ValueError("store manifest is invalid")
        if self.head_digest is not None:
            validate_mapping_hash(self.head_digest)
        if (
            type(self.active_mapping_count) is not int
            or not 0 <= self.active_mapping_count <= MAX_ACTIVE_MAPPINGS
        ):
            raise ValueError("store manifest is invalid")
        validate_mapping_hash(self.manifest_fingerprint)
        if self.manifest_fingerprint != mapping_hash_json(self.fingerprint_payload()):
            raise ValueError("store manifest is invalid")

    def fingerprint_payload(self) -> dict[str, object]:
        return {
            "active_mapping_count": self.active_mapping_count,
            "contract_version": self.contract_version,
            "format_version": self.format_version,
            "generation": str(self.generation),
            "head_digest": self.head_digest,
            "next_sequence": self.next_sequence,
            "record_count": self.record_count,
        }

    def as_dict(self) -> dict[str, object]:
        return {**self.fingerprint_payload(), "manifest_fingerprint": self.manifest_fingerprint}


@dataclass(frozen=True, slots=True)
class StatedObservedCompositionResultV1:
    """Bounded read-only result with no raw source text or labels."""

    contract_version: str
    derivation_version: str
    mapping_policy_id: str
    mapping_policy_fingerprint: MappingHashV1
    generated_at: datetime
    state: StatedObservedCompositionStateV1 | str
    reason_code: str | None
    mapping_id: UUID | None
    mapping_fingerprint: MappingHashV1 | None
    observed_option: BehavioralOptionIdentityV1 | None
    behavioral_pattern_type: BehavioralPatternTypeV1 | str | None
    behavioral_pattern_state: BehavioralPatternStateV1 | str | None
    caveats: tuple[StatedObservedMappingCaveatCodeV1 | str, ...]

    def __post_init__(self) -> None:
        if (
            self.contract_version != CONTRACT_VERSION
            or self.derivation_version != MAPPING_DERIVATION
            or self.mapping_policy_id != MAPPING_POLICY_ID
        ):
            raise ValueError("composition result policy is invalid")
        validate_mapping_hash(self.mapping_policy_fingerprint)
        if self.mapping_policy_fingerprint != MAPPING_POLICY_FINGERPRINT:
            raise ValueError("composition result policy fingerprint is invalid")
        object.__setattr__(self, "generated_at", _utc(self.generated_at))
        object.__setattr__(
            self,
            "state",
            _enum_member(
                self.state, StatedObservedCompositionStateV1, "composition state is invalid"
            ),
        )
        if self.reason_code is not None and (
            type(self.reason_code) is not str
            or not self.reason_code.startswith("STATED_OBSERVED_MAPPING_")
        ):
            raise ValueError("composition reason is invalid")
        if self.reason_code is not None:
            try:
                StatedObservedMappingErrorCode(self.reason_code)
            except ValueError:
                raise ValueError("composition reason is invalid") from None
        if self.mapping_id is not None:
            object.__setattr__(self, "mapping_id", _uuid7(self.mapping_id))
        if self.mapping_fingerprint is not None:
            validate_mapping_hash(self.mapping_fingerprint)
        if (self.mapping_id is None) != (self.mapping_fingerprint is None):
            raise ValueError("composition mapping identity is invalid")
        if (
            self.observed_option is not None
            and type(self.observed_option) is not BehavioralOptionIdentityV1
        ):
            raise ValueError("composition option is invalid")
        if self.behavioral_pattern_type is not None:
            object.__setattr__(
                self,
                "behavioral_pattern_type",
                _enum_member(
                    self.behavioral_pattern_type,
                    BehavioralPatternTypeV1,
                    "composition pattern type is invalid",
                ),
            )
        if self.behavioral_pattern_state is not None:
            object.__setattr__(
                self,
                "behavioral_pattern_state",
                _enum_member(
                    self.behavioral_pattern_state,
                    BehavioralPatternStateV1,
                    "composition pattern state is invalid",
                ),
            )
        if type(self.caveats) is not tuple:
            raise ValueError("composition caveats are invalid")
        normalized = tuple(
            _enum_value(item, StatedObservedMappingCaveatCodeV1, "composition caveat is invalid")
            for item in self.caveats
        )
        if len(normalized) != len(set(normalized)) or normalized != tuple(
            item.value for item in StatedObservedMappingCaveatCodeV1 if item.value in normalized
        ):
            raise ValueError("composition caveats are invalid")
        object.__setattr__(self, "caveats", normalized)

    def as_dict(self) -> dict[str, object]:
        return {
            "contract_version": self.contract_version,
            "derivation_version": self.derivation_version,
            "mapping_policy_id": self.mapping_policy_id,
            "mapping_policy_fingerprint": self.mapping_policy_fingerprint,
            "generated_at": _timestamp(self.generated_at),
            "state": _enum_text(self.state),
            "reason_code": self.reason_code,
            "mapping_id": str(self.mapping_id) if self.mapping_id is not None else None,
            "mapping_fingerprint": self.mapping_fingerprint,
            "observed_option": self.observed_option.as_dict()
            if self.observed_option is not None
            else None,
            "behavioral_pattern_type": (
                _enum_text(self.behavioral_pattern_type)
                if self.behavioral_pattern_type is not None
                else None
            ),
            "behavioral_pattern_state": (
                _enum_text(self.behavioral_pattern_state)
                if self.behavioral_pattern_state is not None
                else None
            ),
            "caveats": list(self.caveats),
        }


@dataclass(frozen=True, slots=True)
class StatedObservedMappingSelectorV1:
    """Client-transport selector; none of its values is source authority."""

    source_note_uuid: UUID
    behavioral_cohort_fingerprint: MappingHashV1
    behavioral_option_index: int
    behavioral_option_fingerprint: MappingHashV1

    def __post_init__(self) -> None:
        object.__setattr__(self, "source_note_uuid", _uuid7(self.source_note_uuid))
        validate_mapping_hash(self.behavioral_cohort_fingerprint)
        if (
            type(self.behavioral_option_index) is not int
            or not 0 <= self.behavioral_option_index <= 19
        ):
            raise ValueError("mapping option selector is invalid")
        validate_mapping_hash(self.behavioral_option_fingerprint)


@dataclass(frozen=True, slots=True)
class StatedObservedMappingReviewRequest:
    selector: StatedObservedMappingSelectorV1

    def __post_init__(self) -> None:
        if type(self.selector) is not StatedObservedMappingSelectorV1:
            raise ValueError("mapping review request is invalid")


@dataclass(frozen=True, slots=True)
class MappingReviewOptionV1:
    option_index: int
    option_fingerprint: MappingHashV1
    label: str

    def __post_init__(self) -> None:
        if type(self.option_index) is not int or not 0 <= self.option_index <= 19:
            raise ValueError("review option is invalid")
        validate_mapping_hash(self.option_fingerprint)
        if type(self.label) is not str or not self.label or len(self.label.encode("utf-8")) > 1024:
            raise ValueError("review option is invalid")

    def as_dict(self) -> dict[str, object]:
        return {
            "option_index": self.option_index,
            "option_fingerprint": self.option_fingerprint,
            "label": self.label,
        }


@dataclass(frozen=True, slots=True)
class StatedObservedMappingReviewProjectionV1:
    """Transient bounded human review projection; never store this object."""

    generated_at: datetime
    stated: StatedAssertionIdentityV1
    behavioral: BehavioralComparisonIdentityV1
    candidate_mapping_fingerprint: MappingHashV1
    claim_text: str
    cohort_domain: str
    situation: str
    information_known_at_decision_time: str
    criteria: tuple[str, ...]
    ordered_options: tuple[MappingReviewOptionV1, ...]
    pattern_type: BehavioralPatternTypeV1 | str
    pattern_state: BehavioralPatternStateV1 | str
    caveats: tuple[str, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "generated_at", _utc(self.generated_at))
        if (
            type(self.stated) is not StatedAssertionIdentityV1
            or type(self.behavioral) is not BehavioralComparisonIdentityV1
        ):
            raise ValueError("review projection is invalid")
        validate_mapping_hash(self.candidate_mapping_fingerprint)
        if self.candidate_mapping_fingerprint != compute_mapping_fingerprint(
            self.stated, self.behavioral
        ):
            raise ValueError("review projection is invalid")
        if type(self.claim_text) is not str or not self.claim_text:
            raise ValueError("review projection is invalid")
        object.__setattr__(self, "cohort_domain", _domain(self.cohort_domain))
        for value in (self.situation, self.information_known_at_decision_time, *self.criteria):
            if type(value) is not str:
                raise ValueError("review projection is invalid")
        if type(self.criteria) is not tuple or type(self.ordered_options) is not tuple:
            raise ValueError("review projection is invalid")
        if any(type(item) is not MappingReviewOptionV1 for item in self.ordered_options):
            raise ValueError("review projection is invalid")
        indexes = tuple(item.option_index for item in self.ordered_options)
        if indexes != tuple(range(len(indexes))):
            raise ValueError("review projection is invalid")
        object.__setattr__(
            self,
            "pattern_type",
            _enum_member(
                self.pattern_type, BehavioralPatternTypeV1, "review pattern type is invalid"
            ),
        )
        object.__setattr__(
            self,
            "pattern_state",
            _enum_member(
                self.pattern_state, BehavioralPatternStateV1, "review pattern state is invalid"
            ),
        )
        if type(self.caveats) is not tuple or any(type(item) is not str for item in self.caveats):
            raise ValueError("review projection is invalid")

    def as_dict(self) -> dict[str, object]:
        return {
            "generated_at": _timestamp(self.generated_at),
            "stated": self.stated.as_dict(),
            "behavioral": self.behavioral.as_dict(),
            "candidate_mapping_fingerprint": self.candidate_mapping_fingerprint,
            "claim_text": self.claim_text,
            "cohort_domain": self.cohort_domain,
            "situation": self.situation,
            "information_known_at_decision_time": self.information_known_at_decision_time,
            "criteria": list(self.criteria),
            "ordered_options": [item.as_dict() for item in self.ordered_options],
            "pattern_type": _enum_text(self.pattern_type),
            "pattern_state": _enum_text(self.pattern_state),
            "caveats": list(self.caveats),
        }

    def to_json(self) -> str:
        return _canonical_json(self.as_dict())


@dataclass(frozen=True, slots=True)
class StatedObservedMappingAcceptanceRequest:
    selector: StatedObservedMappingSelectorV1
    operation_id: UUID
    confirmed: bool
    review_projection: StatedObservedMappingReviewProjectionV1 | None
    supersedes_mapping_id: UUID | None = None

    def __post_init__(self) -> None:
        if type(self.selector) is not StatedObservedMappingSelectorV1:
            raise ValueError("mapping acceptance request is invalid")
        object.__setattr__(self, "operation_id", _uuid7(self.operation_id))
        if type(self.confirmed) is not bool:
            raise ValueError("mapping confirmation is invalid")
        if (
            self.review_projection is not None
            and type(self.review_projection) is not StatedObservedMappingReviewProjectionV1
        ):
            raise ValueError("mapping review projection is invalid")
        if self.supersedes_mapping_id is not None:
            object.__setattr__(self, "supersedes_mapping_id", _uuid7(self.supersedes_mapping_id))


# Concise aliases are useful to application callers while the versioned names
# remain the normative public DTOs.
MappingSelectorV1 = StatedObservedMappingSelectorV1
MappingReviewRequestV1 = StatedObservedMappingReviewRequest
MappingReviewProjectionV1 = StatedObservedMappingReviewProjectionV1
MappingAcceptanceRequestV1 = StatedObservedMappingAcceptanceRequest


def _strict_keys(value: Mapping[str, object], expected: set[str]) -> None:
    if set(value) != expected:
        raise ValueError("mapping record fields are invalid")


def _uuid7_or_none(value: object) -> UUID | None:
    if value is None:
        return None
    return _uuid7(value)


def _stated_from_dict(value: object) -> StatedAssertionIdentityV1:
    if not isinstance(value, Mapping):
        raise ValueError("stated identity is invalid")
    _strict_keys(
        value,
        {
            "source_note_uuid",
            "dimension",
            "source_evidence_kind",
            "source_self_kind",
            "domain",
            "evidence_at",
            "evidence_at_precision",
            "source_contract_version",
            "source_derivation_version",
            "self_model_policy_fingerprint",
            "source_fingerprint",
            "claim_fingerprint",
        },
    )
    evidence_at: datetime | str
    if value["evidence_at"] == "unknown":
        evidence_at = "unknown"
    else:
        evidence_at = _parse_timestamp(value["evidence_at"])
    return StatedAssertionIdentityV1(
        source_note_uuid=_uuid7(value["source_note_uuid"]),
        dimension=cast(str, value["dimension"]),
        source_evidence_kind=cast(str, value["source_evidence_kind"]),
        source_self_kind=cast(str, value["source_self_kind"]),
        domain=cast(str, value["domain"]),
        evidence_at=evidence_at,
        evidence_at_precision=cast(str, value["evidence_at_precision"]),
        source_contract_version=cast(str, value["source_contract_version"]),
        source_derivation_version=cast(str, value["source_derivation_version"]),
        self_model_policy_fingerprint=cast(str, value["self_model_policy_fingerprint"]),
        source_fingerprint=cast(str, value["source_fingerprint"]),
        claim_fingerprint=cast(str, value["claim_fingerprint"]),
    )


def _cohort_from_dict(value: object) -> BehavioralCohortIdentityV1:
    if not isinstance(value, Mapping):
        raise ValueError("behavioral cohort is invalid")
    _strict_keys(
        value,
        {
            "grouping_policy",
            "domain",
            "situation_fingerprint",
            "information_fingerprint",
            "option_namespace_fingerprint",
            "criteria_fingerprint",
            "cohort_fingerprint",
        },
    )
    return BehavioralCohortIdentityV1(
        grouping_policy=cast(str, value["grouping_policy"]),
        domain=cast(str, value["domain"]),
        situation_fingerprint=cast(str, value["situation_fingerprint"]),
        information_fingerprint=cast(str, value["information_fingerprint"]),
        option_namespace_fingerprint=cast(str, value["option_namespace_fingerprint"]),
        criteria_fingerprint=cast(str, value["criteria_fingerprint"]),
        cohort_fingerprint=cast(str, value["cohort_fingerprint"]),
    )


def _option_from_dict(value: object) -> BehavioralOptionIdentityV1:
    if not isinstance(value, Mapping):
        raise ValueError("behavioral option is invalid")
    _strict_keys(value, {"option_index", "option_fingerprint"})
    return BehavioralOptionIdentityV1(
        option_index=cast(int, value["option_index"]),
        option_fingerprint=cast(str, value["option_fingerprint"]),
    )


def _behavioral_from_dict(value: object) -> BehavioralComparisonIdentityV1:
    if not isinstance(value, Mapping):
        raise ValueError("behavioral identity is invalid")
    _strict_keys(
        value,
        {
            "behavioral_contract_version",
            "behavioral_derivation_version",
            "observation_version",
            "policy_id",
            "policy_fingerprint",
            "cohort",
            "option",
            "comparison_subject",
            "pattern_type",
            "pattern_state",
            "pattern_fingerprint",
            "source_fingerprint",
            "provenance_fingerprint",
            "source_count",
        },
    )
    return BehavioralComparisonIdentityV1(
        behavioral_contract_version=cast(str, value["behavioral_contract_version"]),
        behavioral_derivation_version=cast(str, value["behavioral_derivation_version"]),
        observation_version=cast(str, value["observation_version"]),
        policy_id=cast(str, value["policy_id"]),
        policy_fingerprint=cast(str, value["policy_fingerprint"]),
        cohort=_cohort_from_dict(value["cohort"]),
        option=_option_from_dict(value["option"]),
        comparison_subject=cast(str, value["comparison_subject"]),
        pattern_type=cast(str, value["pattern_type"]),
        pattern_state=cast(str, value["pattern_state"]),
        pattern_fingerprint=cast(str, value["pattern_fingerprint"]),
        source_fingerprint=cast(str, value["source_fingerprint"]),
        provenance_fingerprint=cast(str, value["provenance_fingerprint"]),
        source_count=cast(int, value["source_count"]),
    )


def _mapping_from_dict(value: object) -> StatedObservedMappingV1:
    if not isinstance(value, Mapping):
        raise ValueError("mapping is invalid")
    _strict_keys(
        value,
        {
            "contract_version",
            "mapping_policy_id",
            "mapping_policy_fingerprint",
            "mapping_id",
            "acceptance_operation_id_fingerprint",
            "created_at",
            "reviewed_at",
            "mapping_basis",
            "stated",
            "behavioral",
            "mapping_fingerprint",
            "supersedes_mapping_id",
        },
    )
    return StatedObservedMappingV1(
        contract_version=cast(str, value["contract_version"]),
        mapping_policy_id=cast(str, value["mapping_policy_id"]),
        mapping_policy_fingerprint=cast(str, value["mapping_policy_fingerprint"]),
        mapping_id=_uuid7(value["mapping_id"]),
        acceptance_operation_id_fingerprint=cast(str, value["acceptance_operation_id_fingerprint"]),
        created_at=_parse_timestamp(value["created_at"]),
        reviewed_at=_parse_timestamp(value["reviewed_at"]),
        mapping_basis=cast(str, value["mapping_basis"]),
        stated=_stated_from_dict(value["stated"]),
        behavioral=_behavioral_from_dict(value["behavioral"]),
        mapping_fingerprint=cast(str, value["mapping_fingerprint"]),
        supersedes_mapping_id=_uuid7_or_none(value["supersedes_mapping_id"]),
    )


def _lifecycle_event_from_dict(value: object) -> MappingLifecycleEventV1:
    if not isinstance(value, Mapping):
        raise ValueError("lifecycle event is invalid")
    _strict_keys(
        value,
        {
            "event_id",
            "mapping_id",
            "lifecycle_state",
            "occurred_at",
            "operation_id_fingerprint",
            "reason_code",
            "replacement_mapping_id",
        },
    )
    return MappingLifecycleEventV1(
        event_id=_uuid7(value["event_id"]),
        mapping_id=_uuid7(value["mapping_id"]),
        lifecycle_state=cast(str, value["lifecycle_state"]),
        occurred_at=_parse_timestamp(value["occurred_at"]),
        operation_id_fingerprint=cast(str, value["operation_id_fingerprint"]),
        reason_code=cast(str | None, value["reason_code"]),
        replacement_mapping_id=_uuid7_or_none(value["replacement_mapping_id"]),
    )


def _manifest_from_dict(value: object) -> MappingStoreManifestV1:
    if not isinstance(value, Mapping):
        raise ValueError("manifest is invalid")
    _strict_keys(
        value,
        {
            "format_version",
            "contract_version",
            "generation",
            "next_sequence",
            "record_count",
            "head_digest",
            "active_mapping_count",
            "manifest_fingerprint",
        },
    )
    return MappingStoreManifestV1(
        format_version=cast(int, value["format_version"]),
        contract_version=cast(str, value["contract_version"]),
        generation=_uuid7(value["generation"]),
        next_sequence=cast(int, value["next_sequence"]),
        record_count=cast(int, value["record_count"]),
        head_digest=cast(str | None, value["head_digest"]),
        active_mapping_count=cast(int, value["active_mapping_count"]),
        manifest_fingerprint=cast(str, value["manifest_fingerprint"]),
    )


def _envelope_from_dict(value: object) -> MappingStoreRecordEnvelopeV1:
    if not isinstance(value, Mapping):
        raise ValueError("store envelope is invalid")
    _strict_keys(
        value,
        {
            "generation",
            "sequence",
            "record_type",
            "record",
            "previous_digest",
            "current_digest",
        },
    )
    record_type = value["record_type"]
    record: StatedObservedMappingV1 | MappingLifecycleEventV1
    if record_type == "accepted_mapping":
        record = _mapping_from_dict(value["record"])
    elif record_type == "lifecycle_event":
        record = _lifecycle_event_from_dict(value["record"])
    else:
        raise ValueError("store record type is invalid")
    return MappingStoreRecordEnvelopeV1(
        generation=_uuid7(value["generation"]),
        sequence=cast(int, value["sequence"]),
        record_type=cast(str, record_type),
        record=record,
        previous_digest=cast(str | None, value["previous_digest"]),
        current_digest=cast(str, value["current_digest"]),
    )


def _envelope_digest(envelope: MappingStoreRecordEnvelopeV1) -> MappingHashV1:
    return mapping_hash_json(envelope.digest_payload())


@dataclass(frozen=True, slots=True)
class _VerifiedStoreState:
    manifest: MappingStoreManifestV1
    envelopes: tuple[MappingStoreRecordEnvelopeV1, ...]
    mappings: Mapping[UUID, StatedObservedMappingV1]
    lifecycle: Mapping[UUID, MappingLifecycleStateV1]


def _is_posix() -> bool:
    return os.name != "nt"


def _mode(path: Path) -> int:
    return stat.S_IMODE(path.stat().st_mode)


def _path_is_symlink(path: Path) -> bool:
    try:
        return path.is_symlink()
    except OSError:
        return True


def _safe_existing_directory(path: Path, *, expected_group: tuple[str, str] | None = None) -> None:
    try:
        info = path.lstat()
    except OSError:
        raise StatedObservedMappingStoreUnavailableError() from None
    if (
        stat.S_ISLNK(info.st_mode)
        or not stat.S_ISDIR(info.st_mode)
        or (_is_posix() and _mode(path) != 0o700)
    ):
        raise StatedObservedMappingStoreUnavailableError()
    if expected_group is not None and _is_posix():
        import grp
        import pwd

        try:
            owner = pwd.getpwuid(info.st_uid).pw_name  # type: ignore[attr-defined]
            group = grp.getgrgid(info.st_gid).gr_name  # type: ignore[attr-defined]
        except KeyError:
            raise StatedObservedMappingStoreUnavailableError() from None
        if (owner, group) != expected_group:
            raise StatedObservedMappingStoreUnavailableError()


def _safe_file(path: Path) -> None:
    try:
        info = path.lstat()
    except OSError:
        raise StatedObservedMappingStoreUnavailableError() from None
    if (
        stat.S_ISLNK(info.st_mode)
        or not stat.S_ISREG(info.st_mode)
        or (_is_posix() and _mode(path) != 0o600)
    ):
        raise StatedObservedMappingStoreUnavailableError()


class StatedObservedMappingStore:
    """Dedicated append-only mapping store with strict verified reads."""

    records_filename: Final[str] = _RECORD_FILE_NAME
    manifest_filename: Final[str] = _MANIFEST_FILE_NAME
    lock_filename: Final[str] = _LOCK_FILE_NAME

    def __init__(
        self,
        root: Path,
        *,
        expected_owner_group: tuple[str, str] | None = None,
        vault_root: Path | None = None,
        repository_root: Path | None = None,
        clock: StatedObservedMappingClock = lambda: datetime.now(UTC),
    ) -> None:
        if not isinstance(root, Path) or not root.is_absolute() or not callable(clock):
            raise StatedObservedMappingStoreUnavailableError()
        self.root = root
        self.expected_owner_group = (
            expected_owner_group
            if expected_owner_group is not None
            else (
                PRODUCTION_STATED_OBSERVED_MAPPING_OWNER_GROUP
                if root == PRODUCTION_STATED_OBSERVED_MAPPING_STORE_ROOT
                else None
            )
        )
        self.vault_root = vault_root
        self.repository_root = repository_root
        self.clock = clock
        self.records_path = root / _RECORD_FILE_NAME
        self.mappings_path = self.records_path
        self.events_path = self.records_path
        self.manifest_path = root / _MANIFEST_FILE_NAME
        self.lock_path = root / _LOCK_FILE_NAME
        self.lockfile_path = self.lock_path
        self._validate_root_path()

    def _validate_root_path(self) -> None:
        if _path_is_symlink(self.root):
            raise StatedObservedMappingStoreUnavailableError()
        existing = self.root if self.root.exists() else self.root.parent
        if _path_is_symlink(existing) or not existing.is_dir():
            raise StatedObservedMappingStoreUnavailableError()
        current = Path(self.root.anchor)
        for part in self.root.parts[1:]:
            current /= part
            if _path_is_symlink(current):
                raise StatedObservedMappingStoreUnavailableError()
        self._validate_containment()
        if self.root.exists():
            _safe_existing_directory(self.root, expected_group=self.expected_owner_group)
            for child in (self.records_path, self.manifest_path, self.lock_path):
                if _path_is_symlink(child):
                    raise StatedObservedMappingStoreUnavailableError()
                if child.exists():
                    _safe_file(child)

    def _validate_containment(self) -> None:
        root_resolved = self.root.resolve(strict=False)
        for forbidden in (self.vault_root, self.repository_root):
            if forbidden is None:
                continue
            forbidden_resolved = forbidden.resolve(strict=False)
            if (
                root_resolved == forbidden_resolved
                or forbidden_resolved in root_resolved.parents
                or root_resolved in forbidden_resolved.parents
            ):
                raise StatedObservedMappingStoreUnavailableError()

    def _create_root_if_missing(self) -> None:
        self._validate_root_path()
        if not self.root.exists():
            try:
                self.root.mkdir(mode=0o700)
            except FileExistsError:
                pass
            except OSError:
                raise StatedObservedMappingStoreUnavailableError() from None
            try:
                os.chmod(self.root, 0o700)
            except OSError:
                raise StatedObservedMappingStoreUnavailableError() from None
        _safe_existing_directory(self.root, expected_group=self.expected_owner_group)

    @contextmanager
    def _locked(self) -> Iterator[None]:
        self._create_root_if_missing()
        try:
            flags = os.O_RDWR | os.O_CREAT
            descriptor = os.open(self.lock_path, flags, 0o600)
            lock_file = os.fdopen(descriptor, "r+b", buffering=0)
        except OSError:
            raise StatedObservedMappingStoreUnavailableError() from None
        try:
            _safe_file(self.lock_path)
            if _is_posix():
                import fcntl

                try:
                    fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)  # type: ignore[attr-defined]
                except OSError:
                    raise StatedObservedMappingStoreUnavailableError() from None
            else:
                import msvcrt

                try:
                    if os.path.getsize(self.lock_path) == 0:
                        lock_file.write(b"0")
                        lock_file.flush()
                    lock_file.seek(0)
                    msvcrt.locking(lock_file.fileno(), msvcrt.LK_LOCK, 1)
                except OSError:
                    raise StatedObservedMappingStoreUnavailableError() from None
            yield
        finally:
            try:
                if _is_posix():
                    import fcntl

                    fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)  # type: ignore[attr-defined]
                else:
                    import msvcrt

                    lock_file.seek(0)
                    msvcrt.locking(lock_file.fileno(), msvcrt.LK_UNLCK, 1)
            except OSError:
                pass
            lock_file.close()

    def _ensure_initialized_locked(self) -> None:
        records_exists = self.records_path.exists()
        manifest_exists = self.manifest_path.exists()
        if records_exists != manifest_exists:
            raise StatedObservedMappingStoreCorruptError()
        if records_exists:
            _safe_file(self.records_path)
            _safe_file(self.manifest_path)
            return
        for path in (self.records_path, self.manifest_path):
            try:
                descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                os.close(descriptor)
            except FileExistsError:
                raise StatedObservedMappingStoreCorruptError() from None
            except OSError:
                raise StatedObservedMappingStoreUnavailableError() from None
        generation = uuid7()
        manifest = MappingStoreManifestV1(
            format_version=_STORE_FORMAT_VERSION,
            contract_version=CONTRACT_VERSION,
            generation=generation,
            next_sequence=1,
            record_count=0,
            head_digest=None,
            active_mapping_count=0,
            manifest_fingerprint=mapping_hash_json(
                {
                    "active_mapping_count": 0,
                    "contract_version": CONTRACT_VERSION,
                    "format_version": _STORE_FORMAT_VERSION,
                    "generation": str(generation),
                    "head_digest": None,
                    "next_sequence": 1,
                    "record_count": 0,
                }
            ),
        )
        self._atomic_manifest_write(manifest)

    def _atomic_manifest_write(self, manifest: MappingStoreManifestV1) -> None:
        directory = self.root
        temporary: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                dir=directory,
                prefix=".manifest.",
                suffix=".tmp",
                delete=False,
                newline="\n",
            ) as stream:
                temporary = Path(stream.name)
                stream.write(_canonical_json(manifest.as_dict()))
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
            os.chmod(temporary, 0o600)
            os.replace(temporary, self.manifest_path)
            if _is_posix():
                descriptor = os.open(directory, os.O_RDONLY)
                try:
                    os.fsync(descriptor)
                finally:
                    os.close(descriptor)
        except OSError:
            if temporary is not None:
                with suppress(OSError):
                    temporary.unlink(missing_ok=True)
            raise StatedObservedMappingStoreUnavailableError() from None

    def _read_manifest_locked(self) -> MappingStoreManifestV1:
        try:
            raw = self.manifest_path.read_bytes()
            if not raw.endswith(b"\n"):
                raise ValueError
            data = json.loads(raw.decode("utf-8"))
            return _manifest_from_dict(data)
        except StatedObservedMappingError:
            raise
        except OSError, UnicodeError, ValueError, TypeError, json.JSONDecodeError:
            raise StatedObservedMappingStoreCorruptError() from None

    def _read_verified_locked(self) -> _VerifiedStoreState:
        self._ensure_initialized_locked()
        manifest = self._read_manifest_locked()
        try:
            raw = self.records_path.read_bytes()
        except OSError:
            raise StatedObservedMappingStoreUnavailableError() from None
        if raw and not raw.endswith(b"\n"):
            raise StatedObservedMappingStoreCorruptError()
        envelopes: list[MappingStoreRecordEnvelopeV1] = []
        mappings: dict[UUID, StatedObservedMappingV1] = {}
        lifecycle: dict[UUID, MappingLifecycleStateV1] = {}
        event_ids: set[UUID] = set()
        previous_digest: MappingHashV1 | None = None
        expected_sequence = 1
        for line in raw.splitlines(keepends=True):
            if not line.endswith(b"\n") or len(line) > MAX_MAPPING_RECORD_BYTES:
                raise StatedObservedMappingStoreCorruptError()
            try:
                data = json.loads(line[:-1].decode("utf-8"))
                envelope = _envelope_from_dict(data)
            except UnicodeError, ValueError, TypeError, json.JSONDecodeError:
                raise StatedObservedMappingStoreCorruptError() from None
            if envelope.generation != manifest.generation or envelope.sequence != expected_sequence:
                raise StatedObservedMappingStoreCorruptError()
            if (
                envelope.previous_digest != previous_digest
                or _envelope_digest(envelope) != envelope.current_digest
            ):
                raise StatedObservedMappingStoreCorruptError()
            if envelope.record_type == "accepted_mapping":
                record = cast(StatedObservedMappingV1, envelope.record)
                if record.mapping_id in mappings:
                    raise StatedObservedMappingStoreCorruptError()
                mappings[record.mapping_id] = record
                lifecycle[record.mapping_id] = MappingLifecycleStateV1.ACTIVE
            else:
                event = cast(MappingLifecycleEventV1, envelope.record)
                if event.event_id in event_ids:
                    raise StatedObservedMappingStoreCorruptError()
                event_ids.add(event.event_id)
                if event.mapping_id not in mappings:
                    raise StatedObservedMappingStoreCorruptError()
                current = lifecycle[event.mapping_id]
                event_state = cast(MappingLifecycleStateV1, event.lifecycle_state)
                if current is not MappingLifecycleStateV1.ACTIVE and not (
                    event_state is MappingLifecycleStateV1.DELETED
                    and current
                    in {
                        MappingLifecycleStateV1.SUPERSEDED,
                        MappingLifecycleStateV1.INVALIDATED,
                    }
                ):
                    raise StatedObservedMappingStoreCorruptError()
                lifecycle[event.mapping_id] = event_state
            envelopes.append(envelope)
            previous_digest = envelope.current_digest
            expected_sequence += 1
        if (
            manifest.record_count != len(envelopes)
            or manifest.next_sequence != expected_sequence
            or manifest.head_digest != previous_digest
        ):
            raise StatedObservedMappingStoreCorruptError()
        active_count = sum(state is MappingLifecycleStateV1.ACTIVE for state in lifecycle.values())
        if manifest.active_mapping_count != active_count or active_count > MAX_ACTIVE_MAPPINGS:
            raise StatedObservedMappingStoreCorruptError()
        return _VerifiedStoreState(manifest, tuple(envelopes), mappings, lifecycle)

    def _read_state_if_present(self) -> _VerifiedStoreState | None:
        if not self.root.exists():
            return None
        self._validate_root_path()
        records_exists = self.records_path.exists()
        manifest_exists = self.manifest_path.exists()
        if not records_exists and not manifest_exists:
            # A directory created by deployment or an operator is not an
            # initialized mapping store.  Reads remain lazy and side-effect
            # free; only an explicit append creates metadata.
            return None
        if records_exists != manifest_exists:
            raise StatedObservedMappingStoreCorruptError()
        with self._locked():
            return self._read_verified_locked()

    def read_verified_snapshot(self) -> tuple[MappingLifecycleViewV1, ...]:
        state = self._read_state_if_present()
        if state is None:
            return ()
        return tuple(
            MappingLifecycleViewV1(mapping_id, state.lifecycle[mapping_id], record)
            for mapping_id, record in sorted(state.mappings.items(), key=lambda item: str(item[0]))
        )

    def read_active(self) -> tuple[MappingLifecycleViewV1, ...]:
        return tuple(
            item
            for item in self.read_verified_snapshot()
            if item.lifecycle_state == MappingLifecycleStateV1.ACTIVE
        )

    active_mappings = read_active
    list_active = read_active

    def get(self, mapping_id: UUID) -> MappingLifecycleViewV1 | None:
        try:
            identifier = _uuid7(mapping_id)
        except ValueError:
            raise StatedObservedMappingInvalidRequestError() from None
        return next(
            (item for item in self.read_verified_snapshot() if item.mapping_id == identifier), None
        )

    def verify(self) -> MappingStoreManifestV1 | None:
        state = self._read_state_if_present()
        return state.manifest if state is not None else None

    def _now(self) -> datetime:
        try:
            return _utc(self.clock())
        except Exception:
            raise StatedObservedMappingStoreUnavailableError() from None

    def _append_envelopes_locked(
        self,
        state: _VerifiedStoreState,
        records: tuple[tuple[str, StatedObservedMappingV1 | MappingLifecycleEventV1], ...],
    ) -> None:
        if not records:
            raise StatedObservedMappingStoreUnavailableError()
        previous = state.manifest.head_digest
        sequence = state.manifest.next_sequence
        envelopes: list[MappingStoreRecordEnvelopeV1] = []
        for record_type, record in records:
            candidate = MappingStoreRecordEnvelopeV1(
                generation=state.manifest.generation,
                sequence=sequence,
                record_type=record_type,
                record=record,
                previous_digest=previous,
                current_digest="sha256:" + "0" * 64,
            )
            digest = _envelope_digest(candidate)
            envelope = MappingStoreRecordEnvelopeV1(
                generation=candidate.generation,
                sequence=candidate.sequence,
                record_type=candidate.record_type,
                record=candidate.record,
                previous_digest=candidate.previous_digest,
                current_digest=digest,
            )
            line = (_canonical_json(envelope.as_dict()) + "\n").encode("utf-8")
            if len(line) > MAX_MAPPING_RECORD_BYTES:
                raise StatedObservedMappingResultTooLargeError()
            envelopes.append(envelope)
            previous = digest
            sequence += 1
        try:
            with self.records_path.open("ab") as stream:
                for envelope in envelopes:
                    stream.write((_canonical_json(envelope.as_dict()) + "\n").encode("utf-8"))
                stream.flush()
                os.fsync(stream.fileno())
        except OSError:
            raise StatedObservedMappingStoreUnavailableError() from None
        # Derive the next active count from the existing verified state and
        # appended operations instead of trusting caller-provided metadata.
        simulated = dict(state.lifecycle)
        simulated.update(
            {
                cast(StatedObservedMappingV1, record).mapping_id: MappingLifecycleStateV1.ACTIVE
                for record_type, record in records
                if record_type == "accepted_mapping"
            }
        )
        for record_type, record in records:
            if record_type == "lifecycle_event":
                event = cast(MappingLifecycleEventV1, record)
                simulated[event.mapping_id] = cast(MappingLifecycleStateV1, event.lifecycle_state)
        active_count = sum(value is MappingLifecycleStateV1.ACTIVE for value in simulated.values())
        manifest = MappingStoreManifestV1(
            format_version=_STORE_FORMAT_VERSION,
            contract_version=CONTRACT_VERSION,
            generation=state.manifest.generation,
            next_sequence=sequence,
            record_count=state.manifest.record_count + len(envelopes),
            head_digest=previous,
            active_mapping_count=active_count,
            manifest_fingerprint=mapping_hash_json(
                {
                    "active_mapping_count": active_count,
                    "contract_version": CONTRACT_VERSION,
                    "format_version": _STORE_FORMAT_VERSION,
                    "generation": str(state.manifest.generation),
                    "head_digest": previous,
                    "next_sequence": sequence,
                    "record_count": state.manifest.record_count + len(envelopes),
                }
            ),
        )
        self._atomic_manifest_write(manifest)
        reread = self._read_verified_locked()
        if reread.manifest != manifest:
            raise StatedObservedMappingStoreCorruptError()

    def _operation_fingerprint(self, operation_id: UUID) -> MappingHashV1:
        try:
            identifier = _uuid7(operation_id)
        except ValueError:
            raise StatedObservedMappingInvalidRequestError() from None
        return mapping_hash_text(str(identifier))

    def _find_operation(
        self,
        state: _VerifiedStoreState,
        operation_fingerprint: MappingHashV1,
    ) -> tuple[StatedObservedMappingV1, ...]:
        return tuple(
            record
            for record in state.mappings.values()
            if record.acceptance_operation_id_fingerprint == operation_fingerprint
        )

    def accept_mapping(
        self,
        *,
        stated: StatedAssertionIdentityV1,
        behavioral: BehavioralComparisonIdentityV1,
        operation_id: UUID,
        reviewed_at: datetime,
        supersedes_mapping_id: UUID | None = None,
    ) -> StatedObservedMappingV1:
        validate_mapping_policy()
        if (
            type(stated) is not StatedAssertionIdentityV1
            or type(behavioral) is not BehavioralComparisonIdentityV1
        ):
            raise StatedObservedMappingInvalidRequestError()
        operation_fingerprint = self._operation_fingerprint(operation_id)
        try:
            reviewed = _utc(reviewed_at)
        except ValueError:
            raise StatedObservedMappingInvalidRequestError() from None
        with self._locked():
            state = self._read_verified_locked()
            existing = self._find_operation(state, operation_fingerprint)
            expected_fingerprint = compute_mapping_fingerprint(stated, behavioral)
            if existing:
                if len(existing) != 1 or existing[0].mapping_fingerprint != expected_fingerprint:
                    raise StatedObservedMappingIdempotencyConflictError()
                return existing[0]
            active = {
                mapping_id: record
                for mapping_id, record in state.mappings.items()
                if state.lifecycle[mapping_id] is MappingLifecycleStateV1.ACTIVE
            }
            try:
                supersedes = (
                    _uuid7(supersedes_mapping_id) if supersedes_mapping_id is not None else None
                )
            except ValueError:
                raise StatedObservedMappingInvalidRequestError() from None
            if supersedes is not None:
                if supersedes not in active:
                    raise StatedObservedMappingConcurrencyConflictError()
                if active[supersedes].stated.source_note_uuid != stated.source_note_uuid:
                    raise StatedObservedMappingConcurrencyConflictError()
            for existing_mapping in active.values():
                if supersedes is not None and existing_mapping.mapping_id == supersedes:
                    continue
                if existing_mapping.stated.source_note_uuid == stated.source_note_uuid:
                    raise StatedObservedMappingConcurrencyConflictError()
                if (
                    existing_mapping.behavioral.cohort.cohort_fingerprint
                    == behavioral.cohort.cohort_fingerprint
                ):
                    raise StatedObservedMappingConcurrencyConflictError()
            active_count_after = len(active) - int(supersedes is not None) + 1
            if active_count_after > MAX_ACTIVE_MAPPINGS:
                raise StatedObservedMappingResultTooLargeError()
            mapping_id = uuid7()
            created_at = self._now()
            try:
                mapping = StatedObservedMappingV1(
                    contract_version=CONTRACT_VERSION,
                    mapping_policy_id=MAPPING_POLICY_ID,
                    mapping_policy_fingerprint=MAPPING_POLICY_FINGERPRINT,
                    mapping_id=mapping_id,
                    acceptance_operation_id_fingerprint=operation_fingerprint,
                    created_at=created_at,
                    reviewed_at=reviewed,
                    mapping_basis=MAPPING_BASIS,
                    stated=stated,
                    behavioral=behavioral,
                    mapping_fingerprint=expected_fingerprint,
                    supersedes_mapping_id=supersedes,
                )
            except ValueError:
                raise StatedObservedMappingInvalidRequestError() from None
            records: list[tuple[str, StatedObservedMappingV1 | MappingLifecycleEventV1]] = [
                ("accepted_mapping", mapping)
            ]
            if supersedes is not None:
                records.append(
                    (
                        "lifecycle_event",
                        MappingLifecycleEventV1(
                            event_id=uuid7(),
                            mapping_id=supersedes,
                            lifecycle_state=MappingLifecycleStateV1.SUPERSEDED,
                            occurred_at=created_at,
                            operation_id_fingerprint=operation_fingerprint,
                            reason_code="explicit_correction",
                            replacement_mapping_id=mapping_id,
                        ),
                    )
                )
            self._append_envelopes_locked(state, tuple(records))
            return mapping

    def append_mapping(self, mapping: StatedObservedMappingV1) -> StatedObservedMappingV1:
        """Append an already constructed immutable record for store-focused callers."""

        if type(mapping) is not StatedObservedMappingV1:
            raise StatedObservedMappingInvalidRequestError()
        with self._locked():
            state = self._read_verified_locked()
            if mapping.mapping_id in state.mappings:
                existing = state.mappings[mapping.mapping_id]
                if existing == mapping:
                    return existing
                raise StatedObservedMappingConcurrencyConflictError()
            operation_existing = self._find_operation(
                state, mapping.acceptance_operation_id_fingerprint
            )
            if operation_existing:
                if (
                    len(operation_existing) == 1
                    and operation_existing[0].mapping_fingerprint == mapping.mapping_fingerprint
                ):
                    return operation_existing[0]
                raise StatedObservedMappingIdempotencyConflictError()
            active = [
                record
                for identifier, record in state.mappings.items()
                if state.lifecycle[identifier] is MappingLifecycleStateV1.ACTIVE
            ]
            if any(
                record.stated.source_note_uuid == mapping.stated.source_note_uuid
                or record.behavioral.cohort.cohort_fingerprint
                == mapping.behavioral.cohort.cohort_fingerprint
                for record in active
            ):
                raise StatedObservedMappingConcurrencyConflictError()
            if len(active) >= MAX_ACTIVE_MAPPINGS:
                raise StatedObservedMappingResultTooLargeError()
            self._append_envelopes_locked(state, (("accepted_mapping", mapping),))
            return mapping

    def lifecycle_event(
        self,
        mapping_id: UUID,
        lifecycle_state: MappingLifecycleStateV1 | str,
        *,
        operation_id: UUID,
        reason_code: str | None = None,
        replacement_mapping_id: UUID | None = None,
    ) -> MappingLifecycleViewV1:
        try:
            identifier = _uuid7(mapping_id)
            state_value = _enum_value(
                lifecycle_state, MappingLifecycleStateV1, "lifecycle state is invalid"
            )
        except ValueError:
            raise StatedObservedMappingInvalidRequestError() from None
        if state_value == MappingLifecycleStateV1.ACTIVE.value:
            raise StatedObservedMappingInvalidRequestError()
        op_fp = self._operation_fingerprint(operation_id)
        with self._locked():
            state = self._read_verified_locked()
            if identifier not in state.mappings:
                raise StatedObservedMappingConcurrencyConflictError()
            matching_events = tuple(
                cast(MappingLifecycleEventV1, envelope.record)
                for envelope in state.envelopes
                if envelope.record_type == "lifecycle_event"
                and cast(MappingLifecycleEventV1, envelope.record).mapping_id == identifier
                and cast(MappingLifecycleEventV1, envelope.record).operation_id_fingerprint == op_fp
            )
            if matching_events:
                if (
                    len(matching_events) != 1
                    or _enum_text(matching_events[0].lifecycle_state) != state_value
                ):
                    raise StatedObservedMappingIdempotencyConflictError()
                return MappingLifecycleViewV1(
                    identifier, state.lifecycle[identifier], state.mappings[identifier]
                )
            current_state = state.lifecycle[identifier]
            if current_state is not MappingLifecycleStateV1.ACTIVE and not (
                state_value == MappingLifecycleStateV1.DELETED.value
                and current_state
                in {
                    MappingLifecycleStateV1.SUPERSEDED,
                    MappingLifecycleStateV1.INVALIDATED,
                }
            ):
                raise StatedObservedMappingConcurrencyConflictError()
            try:
                event = MappingLifecycleEventV1(
                    event_id=uuid7(),
                    mapping_id=identifier,
                    lifecycle_state=state_value,
                    occurred_at=self._now(),
                    operation_id_fingerprint=op_fp,
                    reason_code=reason_code,
                    replacement_mapping_id=replacement_mapping_id,
                )
            except ValueError:
                raise StatedObservedMappingInvalidRequestError() from None
            self._append_envelopes_locked(state, (("lifecycle_event", event),))
            return MappingLifecycleViewV1(
                identifier, cast(MappingLifecycleStateV1, state_value), state.mappings[identifier]
            )

    def invalidate_mapping(
        self, mapping_id: UUID, *, operation_id: UUID, reason_code: str = "explicit_invalidation"
    ) -> MappingLifecycleViewV1:
        return self.lifecycle_event(
            mapping_id,
            MappingLifecycleStateV1.INVALIDATED,
            operation_id=operation_id,
            reason_code=reason_code,
        )

    def delete_mapping(
        self, mapping_id: UUID, *, operation_id: UUID, reason_code: str = "explicit_delete"
    ) -> MappingLifecycleViewV1:
        return self.lifecycle_event(
            mapping_id,
            MappingLifecycleStateV1.DELETED,
            operation_id=operation_id,
            reason_code=reason_code,
        )

    def supersede_mapping(
        self, mapping_id: UUID, *, operation_id: UUID, replacement_mapping_id: UUID
    ) -> MappingLifecycleViewV1:
        return self.lifecycle_event(
            mapping_id,
            MappingLifecycleStateV1.SUPERSEDED,
            operation_id=operation_id,
            reason_code="explicit_correction",
            replacement_mapping_id=replacement_mapping_id,
        )

    def reset(self, *, operation_id: UUID) -> tuple[MappingLifecycleViewV1, ...]:
        """Append delete tombstones for every active mapping; never purge history."""

        active = self.read_active()
        result: list[MappingLifecycleViewV1] = []
        for item in active:
            result.append(
                self.delete_mapping(
                    item.mapping_id, operation_id=operation_id, reason_code="explicit_reset"
                )
            )
        return tuple(result)


def _source_time(value: object, precision: object) -> tuple[datetime | str, str]:
    normalized_precision = _enum_or_value(precision)
    if value == "unknown":
        if normalized_precision != EvidenceAtPrecision.UNKNOWN.value:
            raise ValueError("source evidence precision is invalid")
        return "unknown", normalized_precision
    if isinstance(value, datetime):
        if normalized_precision != EvidenceAtPrecision.EXACT.value:
            raise ValueError("source evidence precision is invalid")
        return _utc(value), normalized_precision
    raise ValueError("source evidence time is invalid")


def _enum_or_value(value: object) -> str:
    return value.value if isinstance(value, StrEnum) else cast(str, value)


def build_stated_assertion_identity(
    claim: SelfModelClaim,
    result: SelfModelResult,
) -> StatedAssertionIdentityV1:
    """Derive the exact Stated identity from one current Stage 4 claim."""

    if type(claim) is not SelfModelClaim or type(result) is not SelfModelResult:
        raise StatedObservedMappingInvalidRequestError()
    if claim.dimension != "preference" and _enum_or_value(claim.dimension) != "preference":
        raise StatedObservedMappingError(
            StatedObservedMappingErrorCode.UNSUPPORTED_STATED_DIMENSION
        )
    if (
        len(claim.supporting_evidence) != 1
        or claim.contradicting_evidence
        or claim.contextual_evidence
    ):
        raise StatedObservedMappingError(
            StatedObservedMappingErrorCode.UNSUPPORTED_STATED_DIMENSION
        )
    evidence = claim.supporting_evidence[0]
    if (
        evidence.self_kind != SelfKind.PREFERENCE
        and _enum_or_value(evidence.self_kind) != SelfKind.PREFERENCE.value
    ):
        raise StatedObservedMappingError(
            StatedObservedMappingErrorCode.UNSUPPORTED_STATED_DIMENSION
        )
    evidence_kind = _enum_or_value(evidence.evidence_kind)
    if evidence_kind not in {
        EvidenceKind.EXPLICIT_USER_FACT.value,
        EvidenceKind.USER_STATEMENT.value,
    }:
        raise StatedObservedMappingError(
            StatedObservedMappingErrorCode.UNSUPPORTED_STATED_DIMENSION
        )
    if evidence.domain is None or claim.domain != evidence.domain:
        raise StatedObservedMappingError(StatedObservedMappingErrorCode.STATED_SOURCE_CHANGED)
    try:
        evidence_at, precision = _source_time(
            evidence.evidence_at,
            evidence.evidence_at_precision,
        )
    except ValueError:
        raise StatedObservedMappingError(
            StatedObservedMappingErrorCode.STATED_SOURCE_CHANGED
        ) from None
    source_uuid = _uuid7(evidence.note_id)
    domain = _domain(evidence.domain)
    source_contract = "self-model-v1"
    source_derivation = "self-model-derivation-v1"
    claim_text_fingerprint = mapping_hash_text(claim.claim)
    claim_fingerprint = mapping_hash_json(
        {
            "claim_text_fingerprint": claim_text_fingerprint,
            "dimension": "preference",
            "domain": domain,
            "source_contract_version": source_contract,
            "source_derivation_version": source_derivation,
            "source_note_uuid": str(source_uuid),
        }
    )
    source_fingerprint = mapping_hash_json(
        {
            "claim_fingerprint": claim_fingerprint,
            "domain": domain,
            "evidence_at": _timestamp(evidence_at)
            if isinstance(evidence_at, datetime)
            else "unknown",
            "evidence_at_precision": precision,
            "evidence_kind": evidence_kind,
            "self_kind": SelfKind.PREFERENCE.value,
            "source_contract_version": source_contract,
            "source_note_uuid": str(source_uuid),
        }
    )
    try:
        policy_fingerprint = _validate_raw_policy_hash(result.policy_fingerprint)
        if (
            result.derivation_version != "self-model-derivation-v1"
            or policy_fingerprint != validate_self_model_policy(DEFAULT_SELF_MODEL_POLICY)
        ):
            raise ValueError
    except ValueError:
        raise StatedObservedMappingError(StatedObservedMappingErrorCode.POLICY_MISMATCH) from None
    return StatedAssertionIdentityV1(
        source_note_uuid=source_uuid,
        dimension="preference",
        source_evidence_kind=evidence_kind,
        source_self_kind=SelfKind.PREFERENCE.value,
        domain=domain,
        evidence_at=evidence_at,
        evidence_at_precision=precision,
        source_contract_version=source_contract,
        source_derivation_version=source_derivation,
        self_model_policy_fingerprint=policy_fingerprint,
        source_fingerprint=source_fingerprint,
        claim_fingerprint=claim_fingerprint,
    )


def behavioral_source_fingerprint(
    observations: Iterable[BehavioralObservationV1],
) -> MappingHashV1:
    pairs = tuple(
        sorted(
            (
                {
                    "journal_snapshot_fingerprint": item.journal_snapshot_fingerprint,
                    "source_journal_uuid": str(item.source_journal_uuid),
                }
                for item in observations
            ),
            key=lambda item: item["source_journal_uuid"],
        )
    )
    return mapping_hash_json({"observations": list(pairs)})


def behavioral_pattern_fingerprint(
    pattern: Any,
    *,
    source_fingerprint: MappingHashV1,
) -> MappingHashV1:
    """Hash the raw-label-free Stage 10B comparison projection."""

    if not hasattr(pattern, "cohort") or pattern.cohort is None:
        raise ValueError("behavioral pattern is invalid")
    validate_mapping_hash(source_fingerprint)
    return mapping_hash_json(
        {
            "behavioral_contract_version": pattern.contract_version,
            "behavioral_derivation_version": pattern.derivation_version,
            "cohort": pattern.cohort.as_dict(),
            "policy_id": pattern.policy_id,
            "policy_fingerprint": pattern.policy_fingerprint,
            "pattern_type": _enum_or_value(pattern.pattern_type),
            "pattern_state": _enum_or_value(pattern.state),
            "windows": [item.as_dict() for item in pattern.windows],
            "choice_support": [item.as_dict() for item in pattern.choice_support],
            "temporal_span": pattern.temporal_span.as_dict(),
            "source_fingerprint": source_fingerprint,
            "provenance_fingerprint": pattern.provenance.provenance_fingerprint,
        }
    )


def _bucket_namespace(bucket: BehavioralCohortBucketV1) -> tuple[tuple[int, MappingHashV1], ...]:
    if type(bucket) is not BehavioralCohortBucketV1 or not bucket.observations:
        raise ValueError("behavioral bucket is invalid")
    namespace = bucket.observations[0].option_namespace
    return tuple(enumerate(namespace.ordered_option_fingerprints))


def build_behavioral_comparison_identity(
    pattern: Any,
    bucket: BehavioralCohortBucketV1,
    *,
    option: BehavioralOptionIdentityV1 | None = None,
) -> BehavioralComparisonIdentityV1:
    """Build an exact accepted behavioral identity from current Stage 10A/B."""

    if (
        type(bucket) is not BehavioralCohortBucketV1
        or getattr(pattern, "cohort", None) != bucket.cohort
    ):
        raise StatedObservedMappingError(StatedObservedMappingErrorCode.BEHAVIORAL_COHORT_CHANGED)
    pattern_type = BehavioralPatternTypeV1(_enum_or_value(pattern.pattern_type))
    pattern_state = BehavioralPatternStateV1(_enum_or_value(pattern.state))
    allowed = (
        (
            pattern_type is BehavioralPatternTypeV1.REPEATED_EXACT_CHOICE
            and pattern_state is BehavioralPatternStateV1.CURRENT
        )
        or (
            pattern_type is BehavioralPatternTypeV1.STABLE_OVER_TIME
            and pattern_state is BehavioralPatternStateV1.STABLE
        )
        or (
            pattern_type is BehavioralPatternTypeV1.INSUFFICIENT_EVIDENCE
            and pattern_state is BehavioralPatternStateV1.INSUFFICIENT
        )
    )
    if not allowed:
        raise StatedObservedMappingError(StatedObservedMappingErrorCode.NOT_COMPARABLE)
    selected = option if option is not None else pattern.selected_option
    if selected is None:
        raise StatedObservedMappingError(StatedObservedMappingErrorCode.BEHAVIORAL_OPTION_CHANGED)
    if (
        pattern_type is not BehavioralPatternTypeV1.INSUFFICIENT_EVIDENCE
        and pattern.selected_option != selected
    ):
        raise StatedObservedMappingError(StatedObservedMappingErrorCode.BEHAVIORAL_OPTION_CHANGED)
    namespace = _bucket_namespace(bucket)
    if (
        selected.option_index >= len(namespace)
        or namespace[selected.option_index][1] != selected.option_fingerprint
    ):
        raise StatedObservedMappingError(StatedObservedMappingErrorCode.BEHAVIORAL_OPTION_CHANGED)
    source_fp = behavioral_source_fingerprint(bucket.observations)
    pattern_fp = behavioral_pattern_fingerprint(pattern, source_fingerprint=source_fp)
    try:
        return BehavioralComparisonIdentityV1(
            behavioral_contract_version=BEHAVIORAL_CONTRACT_VERSION,
            behavioral_derivation_version=BEHAVIORAL_DERIVATION_VERSION,
            observation_version=OBSERVATION_VERSION,
            policy_id=BEHAVIORAL_POLICY_ID,
            policy_fingerprint=BEHAVIORAL_POLICY_FINGERPRINT,
            cohort=bucket.cohort,
            option=selected,
            comparison_subject="current-exact-option-v1",
            pattern_type=pattern_type,
            pattern_state=pattern_state,
            pattern_fingerprint=pattern_fp,
            source_fingerprint=source_fp,
            provenance_fingerprint=pattern.provenance.provenance_fingerprint,
            source_count=pattern.provenance.source_count,
        )
    except ValueError:
        raise StatedObservedMappingError(StatedObservedMappingErrorCode.POLICY_MISMATCH) from None


@dataclass(frozen=True, slots=True)
class _SnapshotReader:
    snapshot: VaultSnapshot

    def scan(self) -> VaultSnapshot:
        return self.snapshot


@dataclass(frozen=True, slots=True)
class _CurrentBuild:
    snapshot: VaultSnapshot
    report: ScanReport
    stated: SelfModelResult
    behavioral_observations: BehavioralObservationBuildResultV1
    behavioral: BehavioralSelfModelResultV1


def _read_clock(clock: StatedObservedMappingClock) -> datetime:
    if not callable(clock):
        raise StatedObservedMappingInvalidRequestError()
    try:
        value = clock()
        return _utc(value)
    except Exception:
        raise StatedObservedMappingInvalidRequestError() from None


def _safe_current_build(reader: VaultReader, clock: StatedObservedMappingClock) -> _CurrentBuild:
    try:
        snapshot = reader.scan()
        if type(snapshot) is not VaultSnapshot:
            raise ValueError
        report = build_report(snapshot)
    except Exception:
        raise StatedObservedMappingError(
            StatedObservedMappingErrorCode.STATED_SOURCE_UNAVAILABLE
        ) from None
    generated_at = _read_clock(clock)
    snapshot_reader = _SnapshotReader(snapshot)
    try:
        stated = BuildSelfModel(
            snapshot_reader,
            policy=DEFAULT_SELF_MODEL_POLICY,
            clock=lambda: generated_at,
        ).execute(SelfModelRequest())
    except StatedObservedMappingError:
        raise
    except SelfModelError as error:
        if error.code == "SELF_MODEL_RESULT_TOO_LARGE":
            raise StatedObservedMappingError(
                StatedObservedMappingErrorCode.RESULT_TOO_LARGE
            ) from None
        if error.code == "SELF_MODEL_POLICY_UNAVAILABLE":
            raise StatedObservedMappingError(
                StatedObservedMappingErrorCode.POLICY_MISMATCH
            ) from None
        raise StatedObservedMappingError(
            StatedObservedMappingErrorCode.STATED_SOURCE_UNAVAILABLE
        ) from None
    except Exception:
        raise StatedObservedMappingError(
            StatedObservedMappingErrorCode.STATED_SOURCE_UNAVAILABLE
        ) from None
    try:
        observations = BuildBehavioralObservations(
            snapshot_reader,
            policy=DEFAULT_BEHAVIORAL_OBSERVATION_POLICY,
            clock=lambda: generated_at,
        ).execute()
        behavioral = BuildBehavioralSelfModel(
            snapshot_reader,
            policy=DEFAULT_BEHAVIORAL_SELF_MODEL_POLICY,
            clock=lambda: generated_at,
        ).execute()
    except BehavioralSelfModelError as error:
        if error.code.endswith("RESULT_TOO_LARGE"):
            raise StatedObservedMappingError(
                StatedObservedMappingErrorCode.RESULT_TOO_LARGE
            ) from None
        if error.code.endswith("POLICY_MISMATCH"):
            raise StatedObservedMappingError(
                StatedObservedMappingErrorCode.POLICY_MISMATCH
            ) from None
        raise StatedObservedMappingError(
            StatedObservedMappingErrorCode.BEHAVIORAL_SOURCE_UNAVAILABLE
        ) from None
    except Exception:
        raise StatedObservedMappingError(
            StatedObservedMappingErrorCode.BEHAVIORAL_SOURCE_UNAVAILABLE
        ) from None
    return _CurrentBuild(snapshot, report, stated, observations, behavioral)


def _claim_for_source(result: SelfModelResult, source_uuid: UUID) -> SelfModelClaim:
    matches = tuple(
        claim
        for claim in result.claims
        if len(claim.supporting_evidence) == 1
        and claim.supporting_evidence[0].note_id == source_uuid
    )
    if not matches:
        raise StatedObservedMappingError(StatedObservedMappingErrorCode.STATED_EVIDENCE_MISSING)
    if len(matches) != 1:
        raise StatedObservedMappingError(StatedObservedMappingErrorCode.AMBIGUOUS)
    return matches[0]


def _pattern_and_bucket(
    current: _CurrentBuild,
    selector: StatedObservedMappingSelectorV1,
) -> tuple[Any, BehavioralCohortBucketV1]:
    patterns = tuple(
        item
        for item in current.behavioral.patterns
        if item.cohort is not None
        and item.cohort.cohort_fingerprint == selector.behavioral_cohort_fingerprint
    )
    buckets = tuple(
        item
        for item in current.behavioral_observations.cohorts
        if item.cohort.cohort_fingerprint == selector.behavioral_cohort_fingerprint
    )
    if len(patterns) != 1 or len(buckets) != 1:
        raise StatedObservedMappingError(StatedObservedMappingErrorCode.BEHAVIORAL_COHORT_CHANGED)
    return patterns[0], buckets[0]


def _resolve_identities(
    current: _CurrentBuild,
    selector: StatedObservedMappingSelectorV1,
) -> tuple[
    StatedAssertionIdentityV1,
    BehavioralComparisonIdentityV1,
    Any,
    BehavioralCohortBucketV1,
    SelfModelClaim,
]:
    claim = _claim_for_source(current.stated, selector.source_note_uuid)
    stated = build_stated_assertion_identity(claim, current.stated)
    pattern, bucket = _pattern_and_bucket(current, selector)
    if stated.domain != bucket.cohort.domain:
        raise StatedObservedMappingError(StatedObservedMappingErrorCode.NOT_COMPARABLE)
    option = BehavioralOptionIdentityV1(
        selector.behavioral_option_index,
        selector.behavioral_option_fingerprint,
    )
    behavioral = build_behavioral_comparison_identity(pattern, bucket, option=option)
    if behavioral.cohort.cohort_fingerprint != selector.behavioral_cohort_fingerprint:
        raise StatedObservedMappingError(StatedObservedMappingErrorCode.BEHAVIORAL_COHORT_CHANGED)
    return stated, behavioral, pattern, bucket, claim


def _review_caveats(pattern: Any, stated: StatedAssertionIdentityV1) -> tuple[str, ...]:
    values = [
        StatedObservedMappingCaveatCodeV1.CURRENT_SOURCE_REVALIDATED.value,
        StatedObservedMappingCaveatCodeV1.REVIEW_TIME_IS_NOT_EVIDENCE_TIME.value,
        StatedObservedMappingCaveatCodeV1.TEMPORAL_ALIGNMENT_NOT_PROVEN.value,
    ]
    if stated.evidence_at == "unknown":
        values.append(StatedObservedMappingCaveatCodeV1.STATED_EVIDENCE_TIME_UNKNOWN.value)
    if _enum_or_value(pattern.state) == BehavioralPatternStateV1.STABLE.value:
        values.append(StatedObservedMappingCaveatCodeV1.HISTORICAL_CONTEXT_NOT_BINARY.value)
    return tuple(values)


class StatedObservedMappingService:
    """Application boundary for review, acceptance and composition."""

    def __init__(
        self,
        reader: VaultReader,
        store: StatedObservedMappingStore,
        *,
        clock: StatedObservedMappingClock = lambda: datetime.now(UTC),
    ) -> None:
        if (
            not callable(getattr(reader, "scan", None))
            or not isinstance(store, StatedObservedMappingStore)
            or not callable(clock)
        ):
            raise StatedObservedMappingInvalidRequestError()
        validate_mapping_policy()
        self.reader = reader
        self.store = store
        self.clock = clock

    def review(
        self,
        request: StatedObservedMappingReviewRequest | StatedObservedMappingSelectorV1,
    ) -> StatedObservedMappingReviewProjectionV1:
        validate_mapping_policy()
        selector = (
            request.selector if isinstance(request, StatedObservedMappingReviewRequest) else request
        )
        if type(selector) is not StatedObservedMappingSelectorV1:
            raise StatedObservedMappingInvalidRequestError()
        current = _safe_current_build(self.reader, self.clock)
        try:
            stated, behavioral, pattern, bucket, claim = _resolve_identities(current, selector)
        except StatedObservedMappingError:
            raise
        journal_note = next(
            (
                candidate
                for candidate in current.report.notes
                if candidate.note_id in {item.source_journal_uuid for item in bucket.observations}
                and candidate.decision_journal is not None
            ),
            None,
        )
        journal = journal_note.decision_journal if journal_note is not None else None
        labels = tuple(journal.available_options) if journal is not None else tuple()
        namespace = bucket.observations[0].option_namespace
        if len(labels) != namespace.option_count:
            labels = tuple(f"option-{index}" for index in range(namespace.option_count))
        options = tuple(
            MappingReviewOptionV1(
                index, namespace.ordered_option_fingerprints[index], labels[index]
            )
            for index in range(namespace.option_count)
        )
        projection = StatedObservedMappingReviewProjectionV1(
            generated_at=current.behavioral.generated_at,
            stated=stated,
            behavioral=behavioral,
            candidate_mapping_fingerprint=compute_mapping_fingerprint(stated, behavioral),
            claim_text=claim.claim,
            cohort_domain=bucket.cohort.domain,
            situation=journal.situation if journal is not None else "",
            information_known_at_decision_time=(
                journal.information_known_at_decision_time if journal is not None else ""
            ),
            criteria=journal.criteria if journal is not None else tuple(),
            ordered_options=options,
            pattern_type=pattern.pattern_type,
            pattern_state=pattern.state,
            caveats=_review_caveats(pattern, stated),
        )
        try:
            if len(projection.to_json().encode("utf-8")) > MAX_REVIEW_PROJECTION_BYTES:
                raise StatedObservedMappingResultTooLargeError()
        except StatedObservedMappingError:
            raise
        except UnicodeError, TypeError, ValueError:
            raise StatedObservedMappingResultTooLargeError() from None
        return projection

    def accept(self, request: StatedObservedMappingAcceptanceRequest) -> StatedObservedMappingV1:
        validate_mapping_policy()
        if type(request) is not StatedObservedMappingAcceptanceRequest or not request.confirmed:
            raise StatedObservedMappingInvalidRequestError()
        if request.review_projection is None:
            raise StatedObservedMappingInvalidRequestError()
        try:
            if (
                len(request.review_projection.to_json().encode("utf-8"))
                > MAX_REVIEW_PROJECTION_BYTES
            ):
                raise StatedObservedMappingResultTooLargeError()
        except StatedObservedMappingError:
            raise
        except UnicodeError, TypeError, ValueError:
            raise StatedObservedMappingInvalidRequestError() from None
        # This is the mandatory immediate second current build.  Client text,
        # labels, domains and fingerprints are not used as source authority.
        current = _safe_current_build(self.reader, self.clock)
        stated, behavioral, pattern, _bucket, _claim = _resolve_identities(
            current, request.selector
        )
        fresh_fingerprint = compute_mapping_fingerprint(stated, behavioral)
        projection = request.review_projection
        if (
            projection.stated != stated
            or projection.behavioral != behavioral
            or projection.candidate_mapping_fingerprint != fresh_fingerprint
        ):
            raise StatedObservedMappingError(StatedObservedMappingErrorCode.STALE)
        if _enum_or_value(pattern.pattern_type) not in {
            BehavioralPatternTypeV1.REPEATED_EXACT_CHOICE.value,
            BehavioralPatternTypeV1.STABLE_OVER_TIME.value,
            BehavioralPatternTypeV1.INSUFFICIENT_EVIDENCE.value,
        }:
            raise StatedObservedMappingError(StatedObservedMappingErrorCode.NOT_COMPARABLE)
        reviewed_at = _read_clock(self.clock)
        return self.store.accept_mapping(
            stated=stated,
            behavioral=behavioral,
            operation_id=request.operation_id,
            reviewed_at=reviewed_at,
            supersedes_mapping_id=request.supersedes_mapping_id,
        )

    def compose(
        self,
        source_note_uuid: UUID | None = None,
    ) -> StatedObservedCompositionResultV1:
        validate_mapping_policy()
        generated_at = _read_clock(self.clock)
        current = _safe_current_build(self.reader, lambda: generated_at)
        try:
            active = self.store.read_active()
        except StatedObservedMappingError:
            raise
        if source_note_uuid is not None:
            source_uuid = _uuid7(source_note_uuid)
            candidates = tuple(
                item for item in active if item.record.stated.source_note_uuid == source_uuid
            )
        else:
            candidates = active
        if not candidates:
            return self._result(
                generated_at,
                StatedObservedCompositionStateV1.NOT_COMPARABLE,
                StatedObservedMappingErrorCode.MISSING,
            )
        if len(candidates) != 1:
            raise StatedObservedMappingError(StatedObservedMappingErrorCode.AMBIGUOUS)
        view = candidates[0]
        mapping = view.record
        try:
            claim = _claim_for_source(current.stated, mapping.stated.source_note_uuid)
        except StatedObservedMappingError as error:
            if error.code == StatedObservedMappingErrorCode.STATED_EVIDENCE_MISSING.value:
                return self._result(
                    generated_at,
                    StatedObservedCompositionStateV1.STATED_EVIDENCE_MISSING,
                    StatedObservedMappingErrorCode.STATED_EVIDENCE_MISSING,
                    mapping=mapping,
                )
            raise
        try:
            current_stated = build_stated_assertion_identity(claim, current.stated)
        except StatedObservedMappingError:
            raise
        if current_stated != mapping.stated:
            return self._result(
                generated_at,
                StatedObservedCompositionStateV1.NOT_COMPARABLE,
                StatedObservedMappingErrorCode.STATED_SOURCE_CHANGED,
                mapping=mapping,
            )
        try:
            current_behavioral, pattern = self._current_behavioral_for_mapping(current, mapping)
        except StatedObservedMappingError as error:
            if error.code in {
                StatedObservedMappingErrorCode.BEHAVIORAL_COHORT_CHANGED.value,
                StatedObservedMappingErrorCode.BEHAVIORAL_OPTION_CHANGED.value,
                StatedObservedMappingErrorCode.STALE.value,
            }:
                return self._result(
                    generated_at,
                    StatedObservedCompositionStateV1.NOT_COMPARABLE,
                    error.code,
                    mapping=mapping,
                )
            raise
        if current_behavioral is None:
            return self._result(
                generated_at,
                StatedObservedCompositionStateV1.NOT_COMPARABLE,
                StatedObservedMappingErrorCode.STALE,
                mapping=mapping,
            )
        if current_behavioral != mapping.behavioral:
            reason = StatedObservedMappingErrorCode.STALE
            if current_behavioral.cohort != mapping.behavioral.cohort:
                reason = StatedObservedMappingErrorCode.BEHAVIORAL_COHORT_CHANGED
            elif current_behavioral.option != mapping.behavioral.option:
                reason = StatedObservedMappingErrorCode.BEHAVIORAL_OPTION_CHANGED
            return self._result(
                generated_at,
                StatedObservedCompositionStateV1.NOT_COMPARABLE,
                reason,
                mapping=mapping,
            )
        state = BehavioralPatternStateV1(_enum_or_value(pattern.state))
        ptype = BehavioralPatternTypeV1(_enum_or_value(pattern.pattern_type))
        if ptype is BehavioralPatternTypeV1.INSUFFICIENT_EVIDENCE:
            return self._result(
                generated_at,
                StatedObservedCompositionStateV1.BEHAVIORAL_EVIDENCE_INSUFFICIENT,
                StatedObservedMappingErrorCode.BEHAVIORAL_EVIDENCE_INSUFFICIENT,
                mapping=mapping,
                pattern=pattern,
            )
        if not (
            (
                ptype is BehavioralPatternTypeV1.REPEATED_EXACT_CHOICE
                and state is BehavioralPatternStateV1.CURRENT
            )
            or (
                ptype is BehavioralPatternTypeV1.STABLE_OVER_TIME
                and state is BehavioralPatternStateV1.STABLE
            )
        ):
            return self._result(
                generated_at,
                StatedObservedCompositionStateV1.NOT_COMPARABLE,
                StatedObservedMappingErrorCode.NOT_COMPARABLE,
                mapping=mapping,
                pattern=pattern,
            )
        observed = pattern.selected_option
        if observed is None:
            return self._result(
                generated_at,
                StatedObservedCompositionStateV1.NOT_COMPARABLE,
                StatedObservedMappingErrorCode.NOT_COMPARABLE,
                mapping=mapping,
                pattern=pattern,
            )
        result_state = (
            StatedObservedCompositionStateV1.ALIGNED
            if observed == mapping.behavioral.option
            else StatedObservedCompositionStateV1.DIVERGENT
        )
        return self._result(
            generated_at,
            result_state,
            None,
            mapping=mapping,
            pattern=pattern,
            observed_option=observed,
        )

    def _current_behavioral_for_mapping(
        self,
        current: _CurrentBuild,
        mapping: StatedObservedMappingV1,
    ) -> tuple[BehavioralComparisonIdentityV1 | None, Any]:
        selector = StatedObservedMappingSelectorV1(
            source_note_uuid=mapping.stated.source_note_uuid,
            behavioral_cohort_fingerprint=mapping.behavioral.cohort.cohort_fingerprint,
            behavioral_option_index=mapping.behavioral.option.option_index,
            behavioral_option_fingerprint=mapping.behavioral.option.option_fingerprint,
        )
        try:
            pattern, bucket = _pattern_and_bucket(current, selector)
        except StatedObservedMappingError:
            raise StatedObservedMappingError(
                StatedObservedMappingErrorCode.BEHAVIORAL_COHORT_CHANGED
            ) from None
        ptype = BehavioralPatternTypeV1(_enum_or_value(pattern.pattern_type))
        pstate = BehavioralPatternStateV1(_enum_or_value(pattern.state))
        if (
            ptype is BehavioralPatternTypeV1.INSUFFICIENT_EVIDENCE
            and pstate is BehavioralPatternStateV1.INSUFFICIENT
        ):
            identity = build_behavioral_comparison_identity(
                pattern, bucket, option=mapping.behavioral.option
            )
            return identity, pattern
        if ptype in {
            BehavioralPatternTypeV1.REPEATED_EXACT_CHOICE,
            BehavioralPatternTypeV1.STABLE_OVER_TIME,
        }:
            if pattern.selected_option is None:
                raise StatedObservedMappingError(StatedObservedMappingErrorCode.STALE)
            identity = build_behavioral_comparison_identity(pattern, bucket)
            return identity, pattern
        raise StatedObservedMappingError(StatedObservedMappingErrorCode.STALE)

    def _result(
        self,
        generated_at: datetime,
        state: StatedObservedCompositionStateV1,
        reason: StatedObservedMappingErrorCode | str | None,
        *,
        mapping: StatedObservedMappingV1 | None = None,
        pattern: Any | None = None,
        observed_option: BehavioralOptionIdentityV1 | None = None,
    ) -> StatedObservedCompositionResultV1:
        caveats = [
            StatedObservedMappingCaveatCodeV1.CURRENT_SOURCE_REVALIDATED.value,
            StatedObservedMappingCaveatCodeV1.REVIEW_TIME_IS_NOT_EVIDENCE_TIME.value,
            StatedObservedMappingCaveatCodeV1.TEMPORAL_ALIGNMENT_NOT_PROVEN.value,
        ]
        if mapping is not None and mapping.stated.evidence_at == "unknown":
            caveats.append(StatedObservedMappingCaveatCodeV1.STATED_EVIDENCE_TIME_UNKNOWN.value)
        if (
            pattern is not None
            and _enum_or_value(pattern.state) == BehavioralPatternStateV1.STABLE.value
        ):
            caveats.append(StatedObservedMappingCaveatCodeV1.HISTORICAL_CONTEXT_NOT_BINARY.value)
        normalized_reason = (
            reason.value if isinstance(reason, StatedObservedMappingErrorCode) else reason
        )
        return StatedObservedCompositionResultV1(
            contract_version=CONTRACT_VERSION,
            derivation_version=MAPPING_DERIVATION,
            mapping_policy_id=MAPPING_POLICY_ID,
            mapping_policy_fingerprint=MAPPING_POLICY_FINGERPRINT,
            generated_at=generated_at,
            state=state,
            reason_code=normalized_reason,
            mapping_id=mapping.mapping_id if mapping is not None else None,
            mapping_fingerprint=mapping.mapping_fingerprint if mapping is not None else None,
            observed_option=observed_option,
            behavioral_pattern_type=pattern.pattern_type if pattern is not None else None,
            behavioral_pattern_state=pattern.state if pattern is not None else None,
            caveats=tuple(caveats),
        )


def derive_stated_observed_mapping_store_root(env_file: Path) -> Path:
    """Derive the approved root only from an explicit existing env file."""

    if not isinstance(env_file, Path) or not env_file.is_absolute() or _path_is_symlink(env_file):
        raise StatedObservedMappingStoreUnavailableError()
    try:
        if not env_file.is_file():
            raise StatedObservedMappingStoreUnavailableError()
        parent = env_file.parent.resolve(strict=True)
    except StatedObservedMappingError:
        raise
    except OSError:
        raise StatedObservedMappingStoreUnavailableError() from None
    root = parent / "stated-observed-mapping"
    if _path_is_symlink(root):
        raise StatedObservedMappingStoreUnavailableError()
    return root


def build_stated_observed_mapping_service(
    reader: VaultReader,
    *,
    env_file: Path,
    vault_root: Path | None = None,
    repository_root: Path | None = None,
    clock: StatedObservedMappingClock = lambda: datetime.now(UTC),
) -> StatedObservedMappingService:
    """Build a lazy service; constructing it never initializes the store."""

    root = derive_stated_observed_mapping_store_root(env_file)
    store = StatedObservedMappingStore(
        root,
        expected_owner_group=(
            PRODUCTION_STATED_OBSERVED_MAPPING_OWNER_GROUP
            if root == PRODUCTION_STATED_OBSERVED_MAPPING_STORE_ROOT
            else None
        ),
        vault_root=vault_root,
        repository_root=repository_root,
        clock=clock,
    )
    return StatedObservedMappingService(reader, store, clock=clock)


# Builder/service aliases used by application composition callers.
BuildStatedObservedMapping = StatedObservedMappingService
StatedObservedMappingRuntime = StatedObservedMappingService
MappingStore = StatedObservedMappingStore
MappingStoreLifecycleEventV1 = MappingLifecycleEventV1
MappingStoreEnvelopeV1 = MappingStoreRecordEnvelopeV1
MappingStoreManifest = MappingStoreManifestV1
StatedObservedMappingLifecycleStateV1 = MappingLifecycleStateV1


__all__ = [
    "CARDINALITY_POLICY",
    "COMPARISON_POLICY",
    "CONTRACT_VERSION",
    "DERIVATION_VERSION",
    "DIMENSION_POLICY",
    "MAPPING_BASIS",
    "MAPPING_DERIVATION",
    "MAPPING_POLICY_CANONICAL_JSON",
    "MAPPING_POLICY_FINGERPRINT",
    "MAPPING_POLICY_ID",
    "MAX_ACTIVE_MAPPINGS",
    "MAX_MAPPING_RECORD_BYTES",
    "MAX_REVIEW_PROJECTION_BYTES",
    "PERSISTENCE_POLICY",
    "POLICY_FINGERPRINT",
    "POLICY_ID",
    "PRODUCTION_STATED_OBSERVED_MAPPING_OWNER_GROUP",
    "PRODUCTION_STATED_OBSERVED_MAPPING_STORE_ROOT",
    "STORE_POLICY",
    "TEMPORAL_POLICY",
    "BehavioralComparisonIdentityV1",
    "BuildStatedObservedMapping",
    "MappingAcceptanceRequestV1",
    "MappingCompositionStateV1",
    "MappingErrorCode",
    "MappingLifecycleEventV1",
    "MappingLifecycleStateV1",
    "MappingLifecycleViewV1",
    "MappingReviewOptionV1",
    "MappingReviewProjectionV1",
    "MappingReviewRequestV1",
    "MappingSelectorV1",
    "MappingStore",
    "MappingStoreEnvelopeV1",
    "MappingStoreLifecycleEventV1",
    "MappingStoreManifest",
    "MappingStoreManifestV1",
    "MappingStoreRecordEnvelopeV1",
    "StatedAssertionIdentityV1",
    "StatedObservedCompositionResultV1",
    "StatedObservedCompositionStateV1",
    "StatedObservedMappingAcceptanceRequest",
    "StatedObservedMappingCaveatCodeV1",
    "StatedObservedMappingConcurrencyConflictError",
    "StatedObservedMappingError",
    "StatedObservedMappingErrorCode",
    "StatedObservedMappingIdempotencyConflictError",
    "StatedObservedMappingInvalidRequestError",
    "StatedObservedMappingLifecycleStateV1",
    "StatedObservedMappingPolicyMismatchError",
    "StatedObservedMappingResultTooLargeError",
    "StatedObservedMappingReviewProjectionV1",
    "StatedObservedMappingReviewRequest",
    "StatedObservedMappingRuntime",
    "StatedObservedMappingSelectorV1",
    "StatedObservedMappingService",
    "StatedObservedMappingStore",
    "StatedObservedMappingStoreCorruptError",
    "StatedObservedMappingStoreUnavailableError",
    "StatedObservedMappingV1",
    "behavioral_pattern_fingerprint",
    "behavioral_source_fingerprint",
    "build_behavioral_comparison_identity",
    "build_stated_assertion_identity",
    "build_stated_observed_mapping_service",
    "compute_mapping_fingerprint",
    "derive_stated_observed_mapping_store_root",
    "mapping_hash_json",
    "mapping_hash_text",
    "validate_mapping_hash",
    "validate_mapping_policy",
]
