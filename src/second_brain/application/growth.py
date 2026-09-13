"""Provider-free Stage 11A/11B Growth read models.

Stage 11A composes the existing Stage 4 ``BuildSelfModel`` operation.  Stage
11B adds an explicit owner-reviewed Goal-to-choice relation and a dedicated
append-only operational mapping store.  Both read models rebuild from the
current validated vault boundary; no provider, transport, vault write, or
semantic inference is involved.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import tempfile
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager, suppress
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from pathlib import Path
from typing import Final, Literal, cast
from uuid import UUID, uuid7

from second_brain.application.behavioral_observation import (
    CONTRACT_VERSION as BEHAVIORAL_CONTRACT_VERSION,
)
from second_brain.application.behavioral_observation import (
    FULL_DERIVATION_VERSION as BEHAVIORAL_DERIVATION_VERSION,
)
from second_brain.application.behavioral_observation import (
    OBSERVATION_VERSION,
    TEMPORAL_WINDOW_DAYS,
    BehavioralCohortBucketV1,
    BehavioralCohortIdentityV1,
    BehavioralObservationBuildResultV1,
    BehavioralOptionIdentityV1,
    BuildBehavioralObservations,
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
    BehavioralPatternV1,
    BehavioralSelfModelError,
    BehavioralSelfModelResultV1,
    BuildBehavioralSelfModel,
    validate_behavioral_self_model_policy,
    validate_behavioral_self_model_result,
)
from second_brain.application.ports import VaultReader
from second_brain.application.reports import ScanReport, VaultSnapshot
from second_brain.application.self_model import (
    DEFAULT_SELF_MODEL_POLICY,
    MAX_SELF_MODEL_LIMIT,
    BuildSelfModel,
    SelfModelClaim,
    SelfModelError,
    SelfModelEvidenceInvalidError,
    SelfModelInvalidClockError,
    SelfModelInvalidRequestError,
    SelfModelPolicy,
    SelfModelPolicyUnavailableError,
    SelfModelRequest,
    SelfModelResult,
    SelfModelResultInvalidError,
    SelfModelResultTooLargeError,
    SelfModelVaultUnavailableError,
    validate_self_model_claim,
    validate_self_model_policy,
    validate_self_model_result,
)
from second_brain.application.validation import build_report
from second_brain.domain.models import (
    EvidenceAtPrecision,
    EvidenceKind,
    SelfKind,
    parse_rfc3339,
    parse_uuid7,
)

type GrowthHashV1 = str
type GrowthEvidenceAt = datetime | Literal["unknown"]
type GrowthClock = Callable[[], datetime]

GROWTH_CONTRACT_VERSION: Final[str] = "growth-engine-v1"
GROWTH_DERIVATION_VERSION: Final[str] = "growth-engine-derivation-v1"
GROWTH_POLICY_ID: Final[str] = "growth-engine-explicit-relation-v1"
GROWTH_POLICY_CANONICAL_JSON: Final[str] = (
    '{"advisor":"none-v1","behavior":"stage10-current-exact-subject-v1",'
    '"comparison":"none-v1","contract":"growth-engine-v1",'
    '"goal":"stage4-direct-goal-v1","mapping":"owner-explicit-goal-choice-v1",'
    '"persistence":"dedicated-operational-growth-mapping-v1",'
    '"relation":"supports-conflicts-neutral-v1",'
    '"selection":"owner-selected-or-per-goal-v1",'
    '"temporal":"separate-times-no-backfill-v1","version":"1"}'
)
GROWTH_POLICY_FINGERPRINT: Final[GrowthHashV1] = (
    "sha256:3fefc6d638b8bb0de3143cbef4ce51bebbae007ee5e95c5fe2aa81f02b4db182"
)

GROWTH_MIN_RESULTS: Final[int] = 1
GROWTH_MAX_RESULTS: Final[int] = 200
GROWTH_MIN_RESULT_BYTES: Final[int] = 1
GROWTH_MAX_RESULT_BYTES: Final[int] = 131_072

# Descriptive aliases follow the naming used by the other application cores.
CONTRACT_VERSION: Final[str] = GROWTH_CONTRACT_VERSION
DERIVATION_VERSION: Final[str] = GROWTH_DERIVATION_VERSION
POLICY_ID: Final[str] = GROWTH_POLICY_ID
POLICY_FINGERPRINT: Final[GrowthHashV1] = GROWTH_POLICY_FINGERPRINT

_GROWTH_HASH_PATTERN: Final[re.Pattern[str]] = re.compile(r"sha256:[0-9a-f]{64}\Z", re.ASCII)
_RAW_HASH_PATTERN: Final[re.Pattern[str]] = re.compile(r"[0-9a-f]{64}\Z", re.ASCII)
_DOMAIN_PATTERN: Final[re.Pattern[str]] = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*\Z", re.ASCII)
_GROWTH_POLICY_PAYLOAD: Final[dict[str, str]] = {
    "advisor": "none-v1",
    "behavior": "stage10-current-exact-subject-v1",
    "comparison": "none-v1",
    "contract": GROWTH_CONTRACT_VERSION,
    "goal": "stage4-direct-goal-v1",
    "mapping": "owner-explicit-goal-choice-v1",
    "persistence": "dedicated-operational-growth-mapping-v1",
    "relation": "supports-conflicts-neutral-v1",
    "selection": "owner-selected-or-per-goal-v1",
    "temporal": "separate-times-no-backfill-v1",
    "version": "1",
}

# Stage 11B deliberately has a second, narrower policy namespace.  The
# Stage 11A Growth policy remains the result policy; this policy fingerprints
# only the owner-reviewed Goal-to-choice mapping contract.
GROWTH_MAPPING_POLICY_ID: Final[str] = "growth-goal-choice-explicit-v1"
GROWTH_MAPPING_BASIS: Final[str] = "owner-explicit-goal-choice-relation-v1"
GROWTH_MAPPING_POLICY_CANONICAL_JSON: Final[str] = (
    '{"basis":"owner-explicit-goal-choice-relation-v1",'
    '"cardinality":"one-goal-one-target-per-record-v1",'
    '"contract":"growth-engine-v1",'
    '"domain":"exact-goal-domain-equals-cohort-domain-v1",'
    '"lifecycle":"append-only-owner-reviewed-v1",'
    '"persistence":"dedicated-operational-growth-mapping-v1",'
    '"relation":"supports-conflicts-neutral-v1",'
    '"target":"stage10-exact-cohort-option-v1",'
    '"temporal":"separate-times-no-backfill-v1",'
    '"version":"1"}'
)
GROWTH_MAPPING_POLICY_FINGERPRINT: Final[GrowthHashV1] = (
    "sha256:0c4d223c04dae58ff62204245e665b41e3069c01b72e0657e8278243b4e97f6c"
)
MAPPING_POLICY_ID: Final[str] = GROWTH_MAPPING_POLICY_ID
MAPPING_BASIS: Final[str] = GROWTH_MAPPING_BASIS
MAPPING_POLICY_FINGERPRINT: Final[GrowthHashV1] = GROWTH_MAPPING_POLICY_FINGERPRINT

GROWTH_MAPPING_STORE_FORMAT_VERSION: Final[int] = 1
GROWTH_MAPPING_RECORD_FILE_NAME: Final[str] = "mappings.jsonl"
GROWTH_MAPPING_MANIFEST_FILE_NAME: Final[str] = "manifest.json"
GROWTH_MAPPING_LOCK_FILE_NAME: Final[str] = ".store.lock"
GROWTH_MAPPING_MAX_ACTIVE: Final[int] = 200
GROWTH_MAPPING_MAX_RECORD_BYTES: Final[int] = 16_384
GROWTH_MAPPING_MAX_REVIEW_PROJECTION_BYTES: Final[int] = 32_768
GROWTH_MAPPING_STORE_ROOT: Final[Path] = Path("/srv/second-brain/runtime/growth-goal-mapping")
PRODUCTION_GROWTH_MAPPING_STORE_ROOT: Final[Path] = GROWTH_MAPPING_STORE_ROOT
PRODUCTION_GROWTH_MAPPING_OWNER_GROUP: Final[tuple[str, str]] = (
    "second-brain",
    "second-brain",
)
GROWTH_MAPPING_RUNTIME_ROOT: Final[Path] = Path("/srv/second-brain/runtime")

_GROWTH_STORE_RECORD_TYPES: Final[frozenset[str]] = frozenset(
    {"accepted_mapping", "lifecycle_event"}
)
_GROWTH_LIFECYCLE_REASON_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"[a-z0-9]+(?:[._-][a-z0-9]+)*\Z", re.ASCII
)
_GROWTH_LIFECYCLE_STATES: Final[frozenset[str]] = frozenset(
    {"active", "superseded", "invalidated", "deleted"}
)
_GROWTH_BINARY_PATTERN_TYPES: Final[frozenset[BehavioralPatternTypeV1]] = frozenset(
    {
        BehavioralPatternTypeV1.REPEATED_EXACT_CHOICE,
        BehavioralPatternTypeV1.STABLE_OVER_TIME,
    }
)


class GrowthGoalSelectionModeV1(StrEnum):
    """The only explicit Goal selection modes in Stage 11A."""

    SELECTED_GOAL = "selected_goal"
    EACH_CURRENT_GOAL = "each_current_goal"


GrowthGoalSelectionMode = GrowthGoalSelectionModeV1


class GrowthCaveatCodeV1(StrEnum):
    """Closed caveats that are safe to expose with a Goal context."""

    CURRENT_GOAL_REVALIDATED = "current_goal_revalidated"
    GOAL_EVIDENCE_TIME_UNKNOWN = "goal_evidence_time_unknown"
    BEHAVIORAL_RELATION_EXACT_ONLY = "behavioral_relation_exact_only"
    CURRENT_BEHAVIOR_REVALIDATED = "current_behavior_revalidated"
    MIXED_NO_WINNER = "mixed_no_winner"
    CHANGED_NOT_A_TRAIT = "changed_not_a_trait"
    INSUFFICIENT_NOT_CONFLICT = "insufficient_not_conflict"
    GOAL_MAPPING_IS_EXPLICIT = "goal_mapping_is_explicit"
    GOAL_MAPPING_MISSING_FOR_OBSERVED_OPTION = "goal_mapping_missing_for_observed_option"
    MAPPING_REVIEW_TIME_IS_NOT_EVIDENCE_TIME = "mapping_review_time_is_not_evidence_time"
    TEMPORAL_ALIGNMENT_NOT_PROVEN = "temporal_alignment_not_proven"
    OUTCOME_PRESENCE_ONLY = "outcome_presence_only"
    PREFERENCE_BRANCH_INDEPENDENT = "preference_branch_independent"
    NO_GROWTH_OPTIMAL_CLAIM = "no_growth_optimal_claim"
    ADVISOR_NOT_USED_V1 = "advisor_not_used_v1"


GrowthCaveatCode = GrowthCaveatCodeV1


class GrowthErrorCode(StrEnum):
    """Fixed safe Growth error vocabulary."""

    INVALID_REQUEST = "GROWTH_INVALID_REQUEST"
    GOAL_SOURCE_UNAVAILABLE = "GROWTH_GOAL_SOURCE_UNAVAILABLE"
    GOAL_MISSING = "GROWTH_GOAL_MISSING"
    GOAL_SOURCE_CHANGED = "GROWTH_GOAL_SOURCE_CHANGED"
    MULTIPLE_GOALS_AMBIGUOUS = "GROWTH_MULTIPLE_GOALS_AMBIGUOUS"
    GOAL_SELECTION_REQUIRED = "GROWTH_GOAL_SELECTION_REQUIRED"
    GOAL_MAPPING_MISSING = "GROWTH_GOAL_MAPPING_MISSING"
    GOAL_MAPPING_INVALID = "GROWTH_GOAL_MAPPING_INVALID"
    GOAL_MAPPING_STALE = "GROWTH_GOAL_MAPPING_STALE"
    BEHAVIORAL_SOURCE_UNAVAILABLE = "GROWTH_BEHAVIORAL_SOURCE_UNAVAILABLE"
    BEHAVIORAL_EVIDENCE_INSUFFICIENT = "GROWTH_BEHAVIORAL_EVIDENCE_INSUFFICIENT"
    BEHAVIORAL_STATE_NOT_COMPARABLE = "GROWTH_BEHAVIORAL_STATE_NOT_COMPARABLE"
    RECOMMENDATION_UNAVAILABLE = "GROWTH_RECOMMENDATION_UNAVAILABLE"
    UNSUPPORTED_SEMANTIC_COMPARISON = "GROWTH_UNSUPPORTED_SEMANTIC_COMPARISON"
    POLICY_MISMATCH = "GROWTH_POLICY_MISMATCH"
    RESULT_TOO_LARGE = "GROWTH_RESULT_TOO_LARGE"
    MAPPING_STORE_UNAVAILABLE = "GROWTH_MAPPING_STORE_UNAVAILABLE"
    MAPPING_STORE_CORRUPT = "GROWTH_MAPPING_STORE_CORRUPT"
    MAPPING_CONFLICT = "GROWTH_MAPPING_CONFLICT"
    IDEMPOTENCY_CONFLICT = "GROWTH_IDEMPOTENCY_CONFLICT"
    CONCURRENCY_CONFLICT = "GROWTH_CONCURRENCY_CONFLICT"


_ERROR_MESSAGES: Final[dict[GrowthErrorCode, str]] = {
    GrowthErrorCode.INVALID_REQUEST: "growth request failed validation",
    GrowthErrorCode.GOAL_SOURCE_UNAVAILABLE: "the current goal source is unavailable",
    GrowthErrorCode.GOAL_MISSING: "the requested goal is missing",
    GrowthErrorCode.GOAL_SOURCE_CHANGED: "the current goal source changed",
    GrowthErrorCode.MULTIPLE_GOALS_AMBIGUOUS: "multiple current goals require explicit selection",
    GrowthErrorCode.GOAL_SELECTION_REQUIRED: "an explicit goal selection is required",
    GrowthErrorCode.GOAL_MAPPING_MISSING: "the current goal mapping is missing",
    GrowthErrorCode.GOAL_MAPPING_INVALID: "the current goal mapping is invalid",
    GrowthErrorCode.GOAL_MAPPING_STALE: "the current goal mapping is stale",
    GrowthErrorCode.BEHAVIORAL_SOURCE_UNAVAILABLE: "the current behavioral source is unavailable",
    GrowthErrorCode.BEHAVIORAL_EVIDENCE_INSUFFICIENT: "behavioral evidence is insufficient",
    GrowthErrorCode.BEHAVIORAL_STATE_NOT_COMPARABLE: "the behavioral state is not comparable",
    GrowthErrorCode.RECOMMENDATION_UNAVAILABLE: "the independent recommendation is unavailable",
    GrowthErrorCode.UNSUPPORTED_SEMANTIC_COMPARISON: "the semantic comparison is unsupported",
    GrowthErrorCode.POLICY_MISMATCH: "the Growth policy binding is invalid",
    GrowthErrorCode.RESULT_TOO_LARGE: "the Growth result exceeds its bounded limit",
    GrowthErrorCode.MAPPING_STORE_UNAVAILABLE: "the Growth mapping store is unavailable",
    GrowthErrorCode.MAPPING_STORE_CORRUPT: "the Growth mapping store is corrupt",
    GrowthErrorCode.MAPPING_CONFLICT: "the Growth mapping is conflicting",
    GrowthErrorCode.IDEMPOTENCY_CONFLICT: (
        "the Growth operation conflicts with an earlier operation"
    ),
    GrowthErrorCode.CONCURRENCY_CONFLICT: "the Growth operation conflicts with current state",
}


class GrowthError(RuntimeError):
    """Safe application error containing only a fixed code and message."""

    def __init__(self, code: GrowthErrorCode | str) -> None:
        normalized = _normalize_error_code(code)
        self.code = normalized.value
        self.message = _ERROR_MESSAGES[normalized]
        super().__init__(self.message)

    def as_dict(self) -> dict[str, str]:
        """Return a bounded JSON-compatible error projection."""

        return {"code": self.code, "message": self.message}


class GrowthInvalidRequestError(GrowthError):
    """The request type or bounds are invalid."""

    def __init__(self) -> None:
        super().__init__(GrowthErrorCode.INVALID_REQUEST)


class GrowthGoalSourceUnavailableError(GrowthError):
    """The complete current canonical source cannot be proven."""

    def __init__(self) -> None:
        super().__init__(GrowthErrorCode.GOAL_SOURCE_UNAVAILABLE)


class GrowthGoalMissingError(GrowthError):
    """The explicitly requested UUID is not an eligible current Goal."""

    def __init__(self) -> None:
        super().__init__(GrowthErrorCode.GOAL_MISSING)


class GrowthGoalSourceChangedError(GrowthError):
    """The current Goal identity no longer matches the exact source contract."""

    def __init__(self) -> None:
        super().__init__(GrowthErrorCode.GOAL_SOURCE_CHANGED)


class GrowthMultipleGoalsAmbiguousError(GrowthError):
    """A consumer tried to use an ambiguous multi-goal scope."""

    def __init__(self) -> None:
        super().__init__(GrowthErrorCode.MULTIPLE_GOALS_AMBIGUOUS)


class GrowthGoalSelectionRequiredError(GrowthError):
    """An explicit Goal focus is required by the requested operation."""

    def __init__(self) -> None:
        super().__init__(GrowthErrorCode.GOAL_SELECTION_REQUIRED)


class GrowthPolicyMismatchError(GrowthError):
    """The approved Growth or Stage 4 policy binding cannot be proven."""

    def __init__(self) -> None:
        super().__init__(GrowthErrorCode.POLICY_MISMATCH)


class GrowthResultTooLargeError(GrowthError):
    """The complete result exceeds a declared bound and is never truncated."""

    def __init__(self) -> None:
        super().__init__(GrowthErrorCode.RESULT_TOO_LARGE)


class GrowthGoalMappingMissingError(GrowthError):
    """No explicit mapping exists for the observed exact current option."""

    def __init__(self) -> None:
        super().__init__(GrowthErrorCode.GOAL_MAPPING_MISSING)


class GrowthGoalMappingInvalidError(GrowthError):
    """A mapping request or durable mapping violates the v1 contract."""

    def __init__(self) -> None:
        super().__init__(GrowthErrorCode.GOAL_MAPPING_INVALID)


class GrowthGoalMappingStaleError(GrowthError):
    """A reviewed mapping no longer matches the current exact identities."""

    def __init__(self) -> None:
        super().__init__(GrowthErrorCode.GOAL_MAPPING_STALE)


class GrowthBehavioralSourceUnavailableError(GrowthError):
    """The current Stage 10 source cannot be rebuilt or verified."""

    def __init__(self) -> None:
        super().__init__(GrowthErrorCode.BEHAVIORAL_SOURCE_UNAVAILABLE)


class GrowthBehavioralEvidenceInsufficientError(GrowthError):
    """The exact current subject does not have binary eligible evidence."""

    def __init__(self) -> None:
        super().__init__(GrowthErrorCode.BEHAVIORAL_EVIDENCE_INSUFFICIENT)


class GrowthBehavioralStateNotComparableError(GrowthError):
    """The exact Stage 10 state is mixed, changed, historical, or incomparable."""

    def __init__(self) -> None:
        super().__init__(GrowthErrorCode.BEHAVIORAL_STATE_NOT_COMPARABLE)


class GrowthRecommendationUnavailableError(GrowthError):
    """The optional advisor branch is intentionally unavailable in v1."""

    def __init__(self) -> None:
        super().__init__(GrowthErrorCode.RECOMMENDATION_UNAVAILABLE)


class GrowthUnsupportedSemanticComparisonError(GrowthError):
    """A caller attempted a semantic comparison outside exact v1 identity."""

    def __init__(self) -> None:
        super().__init__(GrowthErrorCode.UNSUPPORTED_SEMANTIC_COMPARISON)


class GrowthMappingStoreUnavailableError(GrowthError):
    """The dedicated mapping store cannot be safely read or opened."""

    def __init__(self) -> None:
        super().__init__(GrowthErrorCode.MAPPING_STORE_UNAVAILABLE)


class GrowthMappingStoreCorruptError(GrowthError):
    """The dedicated append-only mapping store failed integrity validation."""

    def __init__(self) -> None:
        super().__init__(GrowthErrorCode.MAPPING_STORE_CORRUPT)


class GrowthMappingConflictError(GrowthError):
    """A second active mapping would violate exact tuple cardinality."""

    def __init__(self) -> None:
        super().__init__(GrowthErrorCode.MAPPING_CONFLICT)


class GrowthIdempotencyConflictError(GrowthError):
    """One operation token was reused with a different exact payload."""

    def __init__(self) -> None:
        super().__init__(GrowthErrorCode.IDEMPOTENCY_CONFLICT)


class GrowthConcurrencyConflictError(GrowthError):
    """A durable state changed incompatibly while an operation was prepared."""

    def __init__(self) -> None:
        super().__init__(GrowthErrorCode.CONCURRENCY_CONFLICT)


@dataclass(frozen=True, slots=True)
class GrowthGoalSelectionV1:
    """Explicit Goal selector revalidated against the current Self Model."""

    mode: GrowthGoalSelectionModeV1
    source_note_uuid: UUID | None

    def __post_init__(self) -> None:
        mode = _normalize_selection_mode(self.mode)
        source_note_uuid = _normalize_optional_uuid7(self.source_note_uuid)
        if mode is GrowthGoalSelectionModeV1.SELECTED_GOAL and source_note_uuid is None:
            raise ValueError("selected Goal requires a source UUID")
        if mode is GrowthGoalSelectionModeV1.EACH_CURRENT_GOAL and source_note_uuid is not None:
            raise ValueError("each-current-goal selection cannot carry a source UUID")
        object.__setattr__(self, "mode", mode)
        object.__setattr__(self, "source_note_uuid", source_note_uuid)

    @classmethod
    def from_dict(cls, value: object) -> GrowthGoalSelectionV1:
        """Parse an exact mapping without accepting unknown selector fields."""

        if not isinstance(value, Mapping) or set(value) != {"mode", "source_note_uuid"}:
            raise GrowthInvalidRequestError()
        try:
            return cls(value["mode"], value["source_note_uuid"])
        except TypeError, ValueError:
            raise GrowthInvalidRequestError() from None

    def as_dict(self) -> dict[str, str | None]:
        """Return the exact selector projection."""

        return {
            "mode": self.mode.value,
            "source_note_uuid": (
                str(self.source_note_uuid) if self.source_note_uuid is not None else None
            ),
        }


@dataclass(frozen=True, slots=True)
class GrowthEngineRequestV1:
    """Strict bounded request shared by the future Growth Engine contract."""

    contract_version: str = GROWTH_CONTRACT_VERSION
    selection: GrowthGoalSelectionV1 = field(
        default_factory=lambda: GrowthGoalSelectionV1(
            GrowthGoalSelectionModeV1.EACH_CURRENT_GOAL,
            None,
        )
    )
    max_results: int = GROWTH_MAX_RESULTS
    max_result_bytes: int = GROWTH_MAX_RESULT_BYTES

    def __post_init__(self) -> None:
        if (
            type(self.contract_version) is not str
            or self.contract_version != GROWTH_CONTRACT_VERSION
        ):
            raise ValueError("Growth contract version is invalid")
        if type(self.selection) is not GrowthGoalSelectionV1:
            raise ValueError("Growth selection is invalid")
        if not _valid_bound(self.max_results, GROWTH_MIN_RESULTS, GROWTH_MAX_RESULTS):
            raise ValueError("Growth result bound is invalid")
        if not _valid_bound(
            self.max_result_bytes,
            GROWTH_MIN_RESULT_BYTES,
            GROWTH_MAX_RESULT_BYTES,
        ):
            raise ValueError("Growth byte bound is invalid")

    @classmethod
    def from_dict(cls, value: object) -> GrowthEngineRequestV1:
        """Parse the exact request shape and reject unknown fields."""

        if not isinstance(value, Mapping) or set(value) != {
            "contract_version",
            "selection",
            "max_results",
            "max_result_bytes",
        }:
            raise GrowthInvalidRequestError()
        try:
            return cls(
                contract_version=value["contract_version"],
                selection=GrowthGoalSelectionV1.from_dict(value["selection"]),
                max_results=value["max_results"],
                max_result_bytes=value["max_result_bytes"],
            )
        except TypeError, ValueError, GrowthError:
            raise GrowthInvalidRequestError() from None

    def as_dict(self) -> dict[str, object]:
        """Return the exact request projection without raw private context."""

        return {
            "contract_version": self.contract_version,
            "selection": self.selection.as_dict(),
            "max_results": self.max_results,
            "max_result_bytes": self.max_result_bytes,
        }


class _DefaultGrowthRequest:
    __slots__ = ()


_DEFAULT_GROWTH_REQUEST: Final[_DefaultGrowthRequest] = _DefaultGrowthRequest()


@dataclass(frozen=True, slots=True)
class GrowthGoalIdentityV1:
    """Exact raw-body-free identity for one current Stage 4 Goal claim."""

    source_note_uuid: UUID | str
    dimension: str
    source_evidence_kind: str
    source_self_kind: str
    domain: str | None
    evidence_at: GrowthEvidenceAt
    evidence_at_precision: str
    source_contract_version: str
    source_derivation_version: str
    self_model_policy_fingerprint: str
    source_fingerprint: GrowthHashV1
    claim_fingerprint: GrowthHashV1

    def __post_init__(self) -> None:
        object.__setattr__(self, "source_note_uuid", _normalize_uuid7(self.source_note_uuid))
        if type(self.dimension) is not str or self.dimension != SelfKind.GOAL.value:
            raise ValueError("Goal dimension is invalid")
        object.__setattr__(
            self,
            "source_evidence_kind",
            _normalize_evidence_kind(self.source_evidence_kind),
        )
        object.__setattr__(self, "source_self_kind", _normalize_self_kind(self.source_self_kind))
        if self.source_self_kind != SelfKind.GOAL.value:
            raise ValueError("Goal self kind is invalid")
        object.__setattr__(self, "domain", _normalize_domain(self.domain))
        evidence_at, precision = _normalize_evidence_time(
            self.evidence_at,
            self.evidence_at_precision,
        )
        object.__setattr__(self, "evidence_at", evidence_at)
        object.__setattr__(self, "evidence_at_precision", precision)
        if (
            type(self.source_contract_version) is not str
            or self.source_contract_version != "self-model-v1"
        ):
            raise ValueError("Goal source contract is invalid")
        if (
            type(self.source_derivation_version) is not str
            or self.source_derivation_version != "self-model-derivation-v1"
        ):
            raise ValueError("Goal source derivation is invalid")
        _validate_raw_hash(self.self_model_policy_fingerprint)
        validate_growth_hash(self.source_fingerprint)
        validate_growth_hash(self.claim_fingerprint)

    def as_dict(self) -> dict[str, object]:
        """Return the exact identity fields; the raw Goal body is absent."""

        return {
            "source_note_uuid": str(self.source_note_uuid),
            "dimension": self.dimension,
            "source_evidence_kind": self.source_evidence_kind,
            "source_self_kind": self.source_self_kind,
            "domain": self.domain,
            "evidence_at": _format_evidence_at(self.evidence_at),
            "evidence_at_precision": self.evidence_at_precision,
            "source_contract_version": self.source_contract_version,
            "source_derivation_version": self.source_derivation_version,
            "self_model_policy_fingerprint": self.self_model_policy_fingerprint,
            "source_fingerprint": self.source_fingerprint,
            "claim_fingerprint": self.claim_fingerprint,
        }


@dataclass(frozen=True, slots=True)
class GrowthGoalContextV1:
    """Bounded in-memory current Goal context, not the full Growth result."""

    contract_version: str
    derivation_version: str
    policy_id: str
    policy_fingerprint: GrowthHashV1
    selection_mode: GrowthGoalSelectionModeV1
    selected_goal_source_uuid: UUID | None
    eligible_goal_count: int
    goals: tuple[GrowthGoalIdentityV1, ...]
    reason_codes: tuple[str, ...] = ()
    caveats: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if (
            type(self.contract_version) is not str
            or self.contract_version != GROWTH_CONTRACT_VERSION
        ):
            raise ValueError("Growth context contract is invalid")
        if (
            type(self.derivation_version) is not str
            or self.derivation_version != GROWTH_DERIVATION_VERSION
        ):
            raise ValueError("Growth context derivation is invalid")
        if type(self.policy_id) is not str or self.policy_id != GROWTH_POLICY_ID:
            raise ValueError("Growth context policy is invalid")
        validate_growth_hash(self.policy_fingerprint)
        if self.policy_fingerprint != GROWTH_POLICY_FINGERPRINT:
            raise ValueError("Growth context policy fingerprint is invalid")
        mode = _normalize_selection_mode(self.selection_mode)
        selected_uuid = _normalize_optional_uuid7(self.selected_goal_source_uuid)
        if not _valid_non_negative_int(self.eligible_goal_count):
            raise ValueError("Growth eligible Goal count is invalid")
        if type(self.goals) is not tuple or len(self.goals) > GROWTH_MAX_RESULTS:
            raise ValueError("Growth Goal identities are invalid")
        if any(type(goal) is not GrowthGoalIdentityV1 for goal in self.goals):
            raise ValueError("Growth Goal identities are invalid")
        goal_ids = tuple(str(goal.source_note_uuid) for goal in self.goals)
        if goal_ids != tuple(sorted(goal_ids)) or len(goal_ids) != len(set(goal_ids)):
            raise ValueError("Growth Goal identities are not deterministic")
        if self.eligible_goal_count < len(self.goals):
            raise ValueError("Growth Goal count is inconsistent")
        if mode is GrowthGoalSelectionModeV1.SELECTED_GOAL:
            if selected_uuid is None or len(self.goals) != 1:
                raise ValueError("selected Goal context is invalid")
            if self.goals[0].source_note_uuid != selected_uuid:
                raise ValueError("selected Goal context is invalid")
        elif selected_uuid is not None or self.eligible_goal_count != len(self.goals):
            raise ValueError("each-current-goal context is invalid")
        reason_codes = _normalize_reason_codes(self.reason_codes)
        caveats = _normalize_caveats(self.caveats)
        object.__setattr__(self, "selection_mode", mode)
        object.__setattr__(self, "selected_goal_source_uuid", selected_uuid)
        object.__setattr__(self, "reason_codes", reason_codes)
        object.__setattr__(self, "caveats", caveats)

    @property
    def goal_identities(self) -> tuple[GrowthGoalIdentityV1, ...]:
        """Descriptive alias for consumers that avoid the short ``goals`` name."""

        return self.goals

    def as_dict(self) -> dict[str, object]:
        """Return only the bounded identity/context projection."""

        return {
            "contract_version": self.contract_version,
            "derivation_version": self.derivation_version,
            "policy_id": self.policy_id,
            "policy_fingerprint": self.policy_fingerprint,
            "selection_mode": self.selection_mode.value,
            "selected_goal_source_uuid": (
                str(self.selected_goal_source_uuid)
                if self.selected_goal_source_uuid is not None
                else None
            ),
            "eligible_goal_count": self.eligible_goal_count,
            "goals": [goal.as_dict() for goal in self.goals],
            "reason_codes": list(self.reason_codes),
            "caveats": list(self.caveats),
        }

    def to_json(self) -> str:
        """Serialize the complete context with the normative JSON profile."""

        validate_growth_goal_context(self)
        return canonical_growth_json(self.as_dict())


# Names with an explicit result suffix are convenient for callers while
# remaining the same narrow DTO, not a GrowthEngineResult implementation.
GrowthGoalContextResultV1 = GrowthGoalContextV1
GrowthGoalContext = GrowthGoalContextV1


@dataclass(frozen=True, slots=True)
class BuildGrowthGoalContext:
    """Rebuild the bounded current Goal context from the current vault."""

    reader: VaultReader
    policy: SelfModelPolicy = DEFAULT_SELF_MODEL_POLICY
    clock: GrowthClock = lambda: datetime.now(UTC)

    def execute(
        self,
        request: GrowthEngineRequestV1 | _DefaultGrowthRequest = _DEFAULT_GROWTH_REQUEST,
    ) -> GrowthGoalContextV1:
        """Return a complete current Goal context or one fixed safe error."""

        if isinstance(request, _DefaultGrowthRequest):
            request = GrowthEngineRequestV1()
        request = validate_growth_engine_request(request)
        try:
            validate_growth_policy()
            self_model_policy_fingerprint = validate_self_model_policy(self.policy)
        except TypeError, ValueError, GrowthError, SelfModelError:
            raise GrowthPolicyMismatchError() from None

        try:
            self_model = BuildSelfModel(
                self.reader,
                policy=self.policy,
                clock=self.clock,
            ).execute(
                SelfModelRequest(
                    max_claims=MAX_SELF_MODEL_LIMIT,
                    max_evidence_refs_per_claim=MAX_SELF_MODEL_LIMIT,
                )
            )
        except SelfModelInvalidRequestError, SelfModelInvalidClockError:
            raise GrowthInvalidRequestError() from None
        except SelfModelPolicyUnavailableError:
            raise GrowthPolicyMismatchError() from None
        except SelfModelResultTooLargeError:
            raise GrowthResultTooLargeError() from None
        except (
            SelfModelVaultUnavailableError,
            SelfModelEvidenceInvalidError,
            SelfModelResultInvalidError,
            SelfModelError,
        ):
            raise GrowthGoalSourceUnavailableError() from None
        except Exception:
            raise GrowthGoalSourceUnavailableError() from None

        try:
            all_goals = tuple(
                sorted(
                    (
                        build_growth_goal_identity(
                            claim,
                            self_model,
                            policy=self.policy,
                            expected_self_model_policy_fingerprint=self_model_policy_fingerprint,
                        )
                        for claim in self_model.claims
                        if claim.dimension.value == SelfKind.GOAL.value
                    ),
                    key=lambda goal: str(goal.source_note_uuid),
                )
            )
        except GrowthError:
            raise
        except Exception:
            raise GrowthGoalSourceChangedError() from None

        selection = request.selection
        if selection.mode is GrowthGoalSelectionModeV1.SELECTED_GOAL:
            assert selection.source_note_uuid is not None
            selected = next(
                (goal for goal in all_goals if goal.source_note_uuid == selection.source_note_uuid),
                None,
            )
            if selected is None:
                raise GrowthGoalMissingError()
            selected_goals: tuple[GrowthGoalIdentityV1, ...] = (selected,)
        else:
            if len(all_goals) > request.max_results:
                raise GrowthResultTooLargeError()
            selected_goals = all_goals

        context = GrowthGoalContextV1(
            contract_version=GROWTH_CONTRACT_VERSION,
            derivation_version=GROWTH_DERIVATION_VERSION,
            policy_id=GROWTH_POLICY_ID,
            policy_fingerprint=GROWTH_POLICY_FINGERPRINT,
            selection_mode=selection.mode,
            selected_goal_source_uuid=(
                selection.source_note_uuid
                if selection.mode is GrowthGoalSelectionModeV1.SELECTED_GOAL
                else None
            ),
            eligible_goal_count=len(all_goals),
            goals=selected_goals,
            caveats=_context_caveats(selected_goals),
        )
        validate_growth_goal_context(context)
        if len(context.to_json().encode("utf-8")) > request.max_result_bytes:
            raise GrowthResultTooLargeError()
        return context


BuildGrowthGoalContextV1 = BuildGrowthGoalContext
BuildGrowthGoals = BuildGrowthGoalContext


def validate_growth_policy() -> GrowthHashV1:
    """Validate the compiled Growth policy against its normative payload."""

    if canonical_growth_json(_GROWTH_POLICY_PAYLOAD) != GROWTH_POLICY_CANONICAL_JSON:
        raise GrowthPolicyMismatchError()
    fingerprint = growth_hash_json(_GROWTH_POLICY_PAYLOAD)
    if fingerprint != GROWTH_POLICY_FINGERPRINT:
        raise GrowthPolicyMismatchError()
    return fingerprint


def validate_growth_engine_request(value: object) -> GrowthEngineRequestV1:
    """Validate request type, selector and bounds before any vault read."""

    if type(value) is not GrowthEngineRequestV1:
        raise GrowthInvalidRequestError()
    request = value
    try:
        if (
            type(request.contract_version) is not str
            or request.contract_version != GROWTH_CONTRACT_VERSION
            or type(request.selection) is not GrowthGoalSelectionV1
            or not _valid_bound(request.max_results, GROWTH_MIN_RESULTS, GROWTH_MAX_RESULTS)
            or not _valid_bound(
                request.max_result_bytes,
                GROWTH_MIN_RESULT_BYTES,
                GROWTH_MAX_RESULT_BYTES,
            )
        ):
            raise ValueError
        validate_growth_goal_selection(request.selection)
    except TypeError, ValueError:
        raise GrowthInvalidRequestError() from None
    return request


def validate_growth_goal_selection(value: object) -> GrowthGoalSelectionV1:
    """Validate the exact selected/every-current selector."""

    if type(value) is not GrowthGoalSelectionV1:
        raise GrowthInvalidRequestError()
    selection = value
    try:
        mode = _normalize_selection_mode(selection.mode)
        source_uuid = _normalize_optional_uuid7(selection.source_note_uuid)
        if mode is GrowthGoalSelectionModeV1.SELECTED_GOAL and source_uuid is None:
            raise ValueError
        if mode is GrowthGoalSelectionModeV1.EACH_CURRENT_GOAL and source_uuid is not None:
            raise ValueError
    except TypeError, ValueError:
        raise GrowthInvalidRequestError() from None
    return selection


def validate_growth_goal_identity(value: object) -> GrowthGoalIdentityV1:
    """Validate one strict raw-body-free Goal identity DTO."""

    if type(value) is not GrowthGoalIdentityV1:
        raise ValueError("Goal identity is invalid")
    identity = value
    try:
        GrowthGoalIdentityV1(
            source_note_uuid=identity.source_note_uuid,
            dimension=identity.dimension,
            source_evidence_kind=identity.source_evidence_kind,
            source_self_kind=identity.source_self_kind,
            domain=identity.domain,
            evidence_at=identity.evidence_at,
            evidence_at_precision=identity.evidence_at_precision,
            source_contract_version=identity.source_contract_version,
            source_derivation_version=identity.source_derivation_version,
            self_model_policy_fingerprint=identity.self_model_policy_fingerprint,
            source_fingerprint=identity.source_fingerprint,
            claim_fingerprint=identity.claim_fingerprint,
        )
    except TypeError, ValueError:
        raise ValueError("Goal identity is invalid") from None
    return identity


def validate_growth_goal_context(value: object) -> GrowthGoalContextV1:
    """Validate the complete bounded Goal context and deterministic order."""

    if type(value) is not GrowthGoalContextV1:
        raise GrowthGoalSourceChangedError()
    context = value
    try:
        if validate_growth_policy() != context.policy_fingerprint:
            raise ValueError
        if type(context.goals) is not tuple:
            raise ValueError
        for goal in context.goals:
            validate_growth_goal_identity(goal)
        GrowthGoalContextV1(
            contract_version=context.contract_version,
            derivation_version=context.derivation_version,
            policy_id=context.policy_id,
            policy_fingerprint=context.policy_fingerprint,
            selection_mode=context.selection_mode,
            selected_goal_source_uuid=context.selected_goal_source_uuid,
            eligible_goal_count=context.eligible_goal_count,
            goals=context.goals,
            reason_codes=context.reason_codes,
            caveats=context.caveats,
        )
    except GrowthError:
        raise
    except TypeError, ValueError:
        raise GrowthError(GrowthErrorCode.GOAL_SOURCE_CHANGED) from None
    return context


def build_growth_goal_identity(
    claim: SelfModelClaim,
    result: SelfModelResult,
    *,
    policy: SelfModelPolicy = DEFAULT_SELF_MODEL_POLICY,
    expected_self_model_policy_fingerprint: str | None = None,
) -> GrowthGoalIdentityV1:
    """Build one exact Goal identity from one current Stage 4 claim."""

    if type(claim) is not SelfModelClaim or type(result) is not SelfModelResult:
        raise GrowthGoalSourceChangedError()
    try:
        policy_fingerprint = validate_self_model_policy(policy)
    except TypeError, ValueError, SelfModelError:
        raise GrowthPolicyMismatchError() from None
    if expected_self_model_policy_fingerprint is not None:
        policy_fingerprint = expected_self_model_policy_fingerprint
    try:
        validate_self_model_result(
            result,
            request=SelfModelRequest(
                max_claims=MAX_SELF_MODEL_LIMIT,
                max_evidence_refs_per_claim=MAX_SELF_MODEL_LIMIT,
            ),
            policy=policy,
            expected_policy_fingerprint=policy_fingerprint,
        )
        validate_self_model_claim(
            claim,
            max_evidence_refs_per_claim=MAX_SELF_MODEL_LIMIT,
        )
    except SelfModelPolicyUnavailableError:
        raise GrowthPolicyMismatchError() from None
    except SelfModelError:
        raise GrowthGoalSourceChangedError() from None
    if claim not in result.claims:
        raise GrowthGoalSourceChangedError()
    if (
        type(result.policy_fingerprint) is not str
        or result.policy_fingerprint != policy_fingerprint
        or result.derivation_version != "self-model-derivation-v1"
        or claim.derivation_version != "self-model-derivation-v1"
        or claim.generated_at != result.generated_at
    ):
        raise GrowthPolicyMismatchError()
    if (
        claim.dimension.value != SelfKind.GOAL.value
        or len(claim.supporting_evidence) != 1
        or claim.contradicting_evidence != ()
        or claim.contextual_evidence != ()
        or claim.status is not None
    ):
        raise GrowthGoalSourceChangedError()
    evidence = claim.supporting_evidence[0]
    evidence_kind = _enum_value(evidence.evidence_kind)
    self_kind = _enum_value(evidence.self_kind)
    if (
        evidence_kind
        not in {
            EvidenceKind.EXPLICIT_USER_FACT.value,
            EvidenceKind.USER_STATEMENT.value,
        }
        or self_kind != SelfKind.GOAL.value
    ):
        raise GrowthGoalSourceChangedError()
    if claim.domain != evidence.domain:
        raise GrowthGoalSourceChangedError()
    try:
        source_uuid = _normalize_uuid7(evidence.note_id)
        domain = _normalize_domain(evidence.domain)
        evidence_at, precision = _normalize_evidence_time(
            evidence.evidence_at,
            evidence.evidence_at_precision,
        )
        claim_text_fingerprint = growth_hash_text(claim.claim)
        claim_fingerprint = growth_hash_json(
            {
                "claim_text_fingerprint": claim_text_fingerprint,
                "dimension": SelfKind.GOAL.value,
                "domain": domain,
                "source_contract_version": "self-model-v1",
                "source_derivation_version": "self-model-derivation-v1",
                "source_note_uuid": str(source_uuid),
            }
        )
        source_fingerprint = growth_hash_json(
            {
                "claim_fingerprint": claim_fingerprint,
                "domain": domain,
                "evidence_at": _format_evidence_at(evidence_at),
                "evidence_at_precision": precision,
                "evidence_kind": evidence_kind,
                "self_kind": SelfKind.GOAL.value,
                "source_contract_version": "self-model-v1",
                "source_note_uuid": str(source_uuid),
            }
        )
        return GrowthGoalIdentityV1(
            source_note_uuid=source_uuid,
            dimension=SelfKind.GOAL.value,
            source_evidence_kind=evidence_kind,
            source_self_kind=SelfKind.GOAL.value,
            domain=domain,
            evidence_at=evidence_at,
            evidence_at_precision=precision,
            source_contract_version="self-model-v1",
            source_derivation_version="self-model-derivation-v1",
            self_model_policy_fingerprint=policy_fingerprint,
            source_fingerprint=source_fingerprint,
            claim_fingerprint=claim_fingerprint,
        )
    except GrowthError:
        raise
    except TypeError, ValueError, UnicodeError:
        raise GrowthGoalSourceChangedError() from None


def canonical_growth_json(value: object) -> str:
    """Serialize the exact Growth canonical JSON profile."""

    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except TypeError, ValueError, UnicodeError:
        raise ValueError("value is not canonical Growth JSON") from None


def growth_hash_json(value: object) -> GrowthHashV1:
    """Hash canonical UTF-8 JSON using the normative ``sha256:`` form."""

    return "sha256:" + hashlib.sha256(canonical_growth_json(value).encode("utf-8")).hexdigest()


def growth_hash_text(value: str) -> GrowthHashV1:
    """Hash exact UTF-8 claim bytes without text normalization."""

    if type(value) is not str:
        raise ValueError("Growth text is invalid")
    try:
        encoded = value.encode("utf-8")
    except UnicodeEncodeError:
        raise ValueError("Growth text is invalid") from None
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def validate_growth_hash(value: object) -> GrowthHashV1:
    """Validate a complete ``GrowthHashV1`` value."""

    if type(value) is not str or _GROWTH_HASH_PATTERN.fullmatch(value) is None:
        raise ValueError("Growth hash is invalid")
    return value


def serialize_growth_goal_context(context: GrowthGoalContextV1) -> str:
    """Return canonical context JSON for the result byte bound."""

    validate_growth_goal_context(context)
    return context.to_json()


def _normalize_error_code(code: GrowthErrorCode | str) -> GrowthErrorCode:
    if isinstance(code, GrowthErrorCode):
        return code
    try:
        return GrowthErrorCode(code)
    except TypeError, ValueError:
        return GrowthErrorCode.INVALID_REQUEST


def _normalize_selection_mode(value: object) -> GrowthGoalSelectionModeV1:
    if type(value) is GrowthGoalSelectionModeV1:
        return value
    if type(value) is str:
        try:
            return GrowthGoalSelectionModeV1(value)
        except ValueError:
            pass
    raise ValueError("Growth selection mode is invalid")


def _normalize_uuid7(value: object) -> UUID:
    try:
        parsed = parse_uuid7(value)
    except TypeError, ValueError, OverflowError:
        raise ValueError("UUIDv7 is invalid") from None
    if type(parsed) is not UUID or parsed.version != 7:
        raise ValueError("UUIDv7 is invalid")
    return parsed


def _normalize_optional_uuid7(value: object) -> UUID | None:
    if value is None:
        return None
    return _normalize_uuid7(value)


def _normalize_evidence_kind(value: object) -> str:
    if type(value) is EvidenceKind:
        normalized = value.value
    elif type(value) is str:
        normalized = value
    else:
        raise ValueError("Goal evidence kind is invalid")
    if normalized not in {
        EvidenceKind.EXPLICIT_USER_FACT.value,
        EvidenceKind.USER_STATEMENT.value,
    }:
        raise ValueError("Goal evidence kind is invalid")
    return normalized


def _normalize_self_kind(value: object) -> str:
    if type(value) is SelfKind:
        normalized = value.value
    elif type(value) is str:
        normalized = value
    else:
        raise ValueError("Goal self kind is invalid")
    if normalized != SelfKind.GOAL.value:
        raise ValueError("Goal self kind is invalid")
    return normalized


def _enum_value(value: object) -> str:
    if isinstance(value, StrEnum):
        return value.value
    if type(value) is str:
        return value
    raise ValueError("enum value is invalid")


def _normalize_domain(value: object) -> str | None:
    if value is None:
        return None
    if type(value) is not str or not value:
        raise ValueError("Goal domain is invalid")
    try:
        if len(value.encode("utf-8")) > 64:
            raise ValueError("Goal domain is invalid")
    except UnicodeEncodeError:
        raise ValueError("Goal domain is invalid") from None
    if _DOMAIN_PATTERN.fullmatch(value) is None:
        raise ValueError("Goal domain is invalid")
    return value


def _normalize_evidence_time(
    value: object,
    precision: object,
) -> tuple[GrowthEvidenceAt, str]:
    if type(precision) is EvidenceAtPrecision:
        normalized_precision = precision.value
    elif type(precision) is str:
        normalized_precision = precision
    else:
        raise ValueError("Goal evidence precision is invalid")
    if normalized_precision not in {
        EvidenceAtPrecision.EXACT.value,
        EvidenceAtPrecision.UNKNOWN.value,
    }:
        raise ValueError("Goal evidence precision is invalid")
    if type(value) is str and value == "unknown":
        if normalized_precision != EvidenceAtPrecision.UNKNOWN.value:
            raise ValueError("Goal evidence precision is invalid")
        return "unknown", EvidenceAtPrecision.UNKNOWN.value
    if isinstance(value, datetime):
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("Goal evidence time is invalid")
        if normalized_precision != EvidenceAtPrecision.EXACT.value:
            raise ValueError("Goal evidence precision is invalid")
        return value.astimezone(UTC), EvidenceAtPrecision.EXACT.value
    if type(value) is str:
        try:
            parsed = parse_rfc3339(value)
        except TypeError, ValueError, OverflowError:
            raise ValueError("Goal evidence time is invalid") from None
        if normalized_precision != EvidenceAtPrecision.EXACT.value:
            raise ValueError("Goal evidence precision is invalid")
        return parsed.astimezone(UTC), EvidenceAtPrecision.EXACT.value
    raise ValueError("Goal evidence time is invalid")


def _format_evidence_at(value: GrowthEvidenceAt) -> str:
    if value == "unknown":
        return "unknown"
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("Goal evidence time is invalid")
    normalized = value.astimezone(UTC)
    rendered = normalized.isoformat(
        timespec="microseconds" if normalized.microsecond else "seconds"
    )
    return rendered.removesuffix("+00:00") + "Z"


def _validate_raw_hash(value: object) -> str:
    if type(value) is not str or _RAW_HASH_PATTERN.fullmatch(value) is None:
        raise ValueError("raw policy hash is invalid")
    return value


def _valid_bound(value: object, lower: int, upper: int) -> bool:
    return type(value) is int and lower <= value <= upper


def _valid_non_negative_int(value: object) -> bool:
    return type(value) is int and value >= 0


def _normalize_reason_codes(
    values: tuple[GrowthErrorCode | str, ...],
) -> tuple[str, ...]:
    if type(values) is not tuple:
        raise ValueError("Growth reason codes are invalid")
    normalized: list[str] = []
    for value in values:
        code = _normalize_error_code(value)
        if isinstance(value, str) and value not in {item.value for item in GrowthErrorCode}:
            raise ValueError("Growth reason code is invalid")
        normalized.append(code.value)
    if len(normalized) != len(set(normalized)):
        raise ValueError("Growth reason codes are invalid")
    return tuple(normalized)


def _normalize_caveats(
    values: tuple[GrowthCaveatCodeV1 | str, ...],
) -> tuple[str, ...]:
    if type(values) is not tuple:
        raise ValueError("Growth caveats are invalid")
    normalized: list[str] = []
    for value in values:
        if isinstance(value, GrowthCaveatCodeV1):
            normalized.append(value.value)
        elif type(value) is str and value in {item.value for item in GrowthCaveatCodeV1}:
            normalized.append(value)
        else:
            raise ValueError("Growth caveat is invalid")
    if len(normalized) != len(set(normalized)):
        raise ValueError("Growth caveats are invalid")
    order = {item.value: index for index, item in enumerate(GrowthCaveatCodeV1)}
    if normalized != sorted(normalized, key=order.__getitem__):
        raise ValueError("Growth caveats are not deterministic")
    return tuple(normalized)


def _context_caveats(
    goals: tuple[GrowthGoalIdentityV1, ...],
) -> tuple[GrowthCaveatCodeV1, ...]:
    caveats: list[GrowthCaveatCodeV1] = [GrowthCaveatCodeV1.CURRENT_GOAL_REVALIDATED]
    if any(goal.evidence_at == "unknown" for goal in goals):
        caveats.append(GrowthCaveatCodeV1.GOAL_EVIDENCE_TIME_UNKNOWN)
    return tuple(caveats)


class GrowthGoalRelationV1(StrEnum):
    """The only owner-reviewed relation meanings accepted by Stage 11B."""

    SUPPORTS_GOAL = "supports_goal"
    CONFLICTS_WITH_GOAL = "conflicts_with_goal"
    NEUTRAL_OR_UNKNOWN = "neutral_or_unknown"


GrowthGoalRelation = GrowthGoalRelationV1


class GrowthRelationStateV1(StrEnum):
    """Deterministic relation/read-model states; none is a score or winner."""

    SUPPORTS_GOAL = "supports_goal"
    CONFLICTS_WITH_GOAL = "conflicts_with_goal"
    NEUTRAL_OR_UNKNOWN = "neutral_or_unknown"
    MIXED_BEHAVIOR = "mixed_behavior"
    CHANGED_BEHAVIOR = "changed_behavior"
    BEHAVIORAL_EVIDENCE_INSUFFICIENT = "behavioral_evidence_insufficient"
    GOAL_MAPPING_MISSING = "goal_mapping_missing"
    GOAL_SOURCE_MISSING = "goal_source_missing"
    GOAL_SELECTION_REQUIRED = "goal_selection_required"
    NOT_COMPARABLE = "not_comparable"


GrowthRelationState = GrowthRelationStateV1
GrowthStateV1 = GrowthRelationStateV1
GrowthState = GrowthRelationStateV1


class GrowthMappingLifecycleStateV1(StrEnum):
    """Append-only lifecycle states for one immutable accepted mapping."""

    ACTIVE = "active"
    SUPERSEDED = "superseded"
    INVALIDATED = "invalidated"
    DELETED = "deleted"


GrowthMappingLifecycleState = GrowthMappingLifecycleStateV1


@dataclass(frozen=True, slots=True)
class GrowthGoalChoiceMappingSelectorV1:
    """Client selector; all semantic values are re-resolved from current source."""

    source_note_uuid: UUID
    behavioral_cohort_fingerprint: GrowthHashV1
    behavioral_option_index: int
    behavioral_option_fingerprint: GrowthHashV1

    def __post_init__(self) -> None:
        object.__setattr__(self, "source_note_uuid", _normalize_uuid7(self.source_note_uuid))
        validate_growth_hash(self.behavioral_cohort_fingerprint)
        if (
            type(self.behavioral_option_index) is not int
            or not 0 <= self.behavioral_option_index <= 19
        ):
            raise ValueError("Growth option selector is invalid")
        validate_growth_hash(self.behavioral_option_fingerprint)

    def as_dict(self) -> dict[str, object]:
        return {
            "source_note_uuid": str(self.source_note_uuid),
            "behavioral_cohort_fingerprint": self.behavioral_cohort_fingerprint,
            "behavioral_option_index": self.behavioral_option_index,
            "behavioral_option_fingerprint": self.behavioral_option_fingerprint,
        }


GrowthMappingSelectorV1 = GrowthGoalChoiceMappingSelectorV1
GrowthGoalChoiceSelectorV1 = GrowthGoalChoiceMappingSelectorV1


@dataclass(frozen=True, slots=True)
class GrowthBehavioralTargetIdentityV1:
    """Exact Stage 10 cohort/option identity accepted by the mapping boundary."""

    behavioral_contract_version: str
    behavioral_derivation_version: str
    observation_version: str
    policy_id: str
    policy_fingerprint: GrowthHashV1
    cohort: BehavioralCohortIdentityV1
    option: BehavioralOptionIdentityV1
    comparison_basis: str

    def __post_init__(self) -> None:
        if (
            type(self.behavioral_contract_version) is not str
            or self.behavioral_contract_version != BEHAVIORAL_CONTRACT_VERSION
            or type(self.behavioral_derivation_version) is not str
            or self.behavioral_derivation_version != BEHAVIORAL_DERIVATION_VERSION
            or type(self.observation_version) is not str
            or self.observation_version != OBSERVATION_VERSION
            or type(self.policy_id) is not str
            or self.policy_id != BEHAVIORAL_POLICY_ID
        ):
            raise ValueError("Growth behavioral target policy is invalid")
        validate_growth_hash(self.policy_fingerprint)
        if self.policy_fingerprint != BEHAVIORAL_POLICY_FINGERPRINT:
            raise ValueError("Growth behavioral target policy fingerprint is invalid")
        if type(self.cohort) is not BehavioralCohortIdentityV1:
            raise ValueError("Growth behavioral cohort is invalid")
        if type(self.option) is not BehavioralOptionIdentityV1:
            raise ValueError("Growth behavioral option is invalid")
        if self.comparison_basis != "current-exact-option-v1":
            raise ValueError("Growth comparison basis is invalid")

    def as_dict(self) -> dict[str, object]:
        return {
            "behavioral_contract_version": self.behavioral_contract_version,
            "behavioral_derivation_version": self.behavioral_derivation_version,
            "observation_version": self.observation_version,
            "policy_id": self.policy_id,
            "policy_fingerprint": self.policy_fingerprint,
            "cohort": self.cohort.as_dict(),
            "option": self.option.as_dict(),
            "comparison_basis": self.comparison_basis,
        }


GrowthBehavioralTargetV1 = GrowthBehavioralTargetIdentityV1


@dataclass(frozen=True, slots=True)
class GrowthGoalChoiceMappingV1:
    """Immutable accepted owner-reviewed Goal-to-choice relation."""

    contract_version: str
    mapping_policy_id: str
    mapping_policy_fingerprint: GrowthHashV1
    mapping_id: UUID
    acceptance_operation_id_fingerprint: GrowthHashV1
    created_at: datetime
    reviewed_at: datetime
    mapping_basis: str
    goal: GrowthGoalIdentityV1
    behavioral_target: GrowthBehavioralTargetIdentityV1
    relation: GrowthGoalRelationV1 | str
    mapping_fingerprint: GrowthHashV1
    supersedes_mapping_id: UUID | None

    def __post_init__(self) -> None:
        if (
            type(self.contract_version) is not str
            or self.contract_version != GROWTH_CONTRACT_VERSION
            or type(self.mapping_policy_id) is not str
            or self.mapping_policy_id != GROWTH_MAPPING_POLICY_ID
            or type(self.mapping_basis) is not str
            or self.mapping_basis != GROWTH_MAPPING_BASIS
        ):
            raise ValueError("Growth mapping policy is invalid")
        validate_growth_hash(self.mapping_policy_fingerprint)
        if self.mapping_policy_fingerprint != GROWTH_MAPPING_POLICY_FINGERPRINT:
            raise ValueError("Growth mapping policy fingerprint is invalid")
        object.__setattr__(self, "mapping_id", _normalize_uuid7(self.mapping_id))
        validate_growth_hash(self.acceptance_operation_id_fingerprint)
        created_at = _normalize_canonical_growth_timestamp(self.created_at)
        reviewed_at = _normalize_canonical_growth_timestamp(self.reviewed_at)
        if reviewed_at > created_at:
            raise ValueError("Growth mapping timestamps are invalid")
        object.__setattr__(self, "created_at", created_at)
        object.__setattr__(self, "reviewed_at", reviewed_at)
        if type(self.goal) is not GrowthGoalIdentityV1:
            raise ValueError("Growth mapping Goal is invalid")
        if type(self.behavioral_target) is not GrowthBehavioralTargetIdentityV1:
            raise ValueError("Growth mapping behavioral target is invalid")
        if self.goal.domain is None or self.goal.domain != self.behavioral_target.cohort.domain:
            raise ValueError("Growth mapping domains are invalid")
        relation = _normalize_growth_relation(self.relation)
        object.__setattr__(self, "relation", relation)
        validate_growth_hash(self.mapping_fingerprint)
        if self.mapping_fingerprint != compute_growth_goal_choice_mapping_fingerprint(
            self.goal, self.behavioral_target, relation
        ):
            raise ValueError("Growth mapping fingerprint is invalid")
        if self.supersedes_mapping_id is not None:
            object.__setattr__(
                self,
                "supersedes_mapping_id",
                _normalize_uuid7(self.supersedes_mapping_id),
            )

    def as_dict(self) -> dict[str, object]:
        return {
            "contract_version": self.contract_version,
            "mapping_policy_id": self.mapping_policy_id,
            "mapping_policy_fingerprint": self.mapping_policy_fingerprint,
            "mapping_id": str(self.mapping_id),
            "acceptance_operation_id_fingerprint": self.acceptance_operation_id_fingerprint,
            "created_at": _format_growth_timestamp(self.created_at),
            "reviewed_at": _format_growth_timestamp(self.reviewed_at),
            "mapping_basis": self.mapping_basis,
            "goal": self.goal.as_dict(),
            "behavioral_target": self.behavioral_target.as_dict(),
            "relation": cast(GrowthGoalRelationV1, self.relation).value,
            "mapping_fingerprint": self.mapping_fingerprint,
            "supersedes_mapping_id": (
                str(self.supersedes_mapping_id) if self.supersedes_mapping_id is not None else None
            ),
        }


GrowthMappingV1 = GrowthGoalChoiceMappingV1


@dataclass(frozen=True, slots=True)
class GrowthMappingLifecycleEventV1:
    """Append-only lifecycle event for one accepted mapping."""

    event_id: UUID
    mapping_id: UUID
    lifecycle_state: GrowthMappingLifecycleStateV1 | str
    occurred_at: datetime
    operation_id_fingerprint: GrowthHashV1
    reason_code: str | None = None
    replacement_mapping_id: UUID | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "event_id", _normalize_uuid7(self.event_id))
        object.__setattr__(self, "mapping_id", _normalize_uuid7(self.mapping_id))
        state = _normalize_lifecycle_state(self.lifecycle_state)
        if state is GrowthMappingLifecycleStateV1.ACTIVE:
            raise ValueError("Growth lifecycle event state is invalid")
        object.__setattr__(self, "lifecycle_state", state)
        object.__setattr__(
            self,
            "occurred_at",
            _normalize_canonical_growth_timestamp(self.occurred_at),
        )
        validate_growth_hash(self.operation_id_fingerprint)
        if self.reason_code is not None and (
            type(self.reason_code) is not str
            or not 1 <= len(self.reason_code.encode("utf-8")) <= 128
            or _GROWTH_LIFECYCLE_REASON_PATTERN.fullmatch(self.reason_code) is None
        ):
            raise ValueError("Growth lifecycle reason is invalid")
        if self.replacement_mapping_id is not None:
            object.__setattr__(
                self,
                "replacement_mapping_id",
                _normalize_uuid7(self.replacement_mapping_id),
            )
        if state is GrowthMappingLifecycleStateV1.SUPERSEDED:
            if self.replacement_mapping_id is None:
                raise ValueError("Growth supersession replacement is invalid")
        elif self.replacement_mapping_id is not None:
            raise ValueError("Growth lifecycle replacement is invalid")

    def as_dict(self) -> dict[str, object]:
        return {
            "event_id": str(self.event_id),
            "mapping_id": str(self.mapping_id),
            "lifecycle_state": cast(GrowthMappingLifecycleStateV1, self.lifecycle_state).value,
            "occurred_at": _format_growth_timestamp(self.occurred_at),
            "operation_id_fingerprint": self.operation_id_fingerprint,
            "reason_code": self.reason_code,
            "replacement_mapping_id": (
                str(self.replacement_mapping_id)
                if self.replacement_mapping_id is not None
                else None
            ),
        }


GrowthMappingLifecycleEvent = GrowthMappingLifecycleEventV1


@dataclass(frozen=True, slots=True)
class GrowthMappingLifecycleViewV1:
    """Verified current lifecycle view of one immutable accepted mapping."""

    mapping_id: UUID
    lifecycle_state: GrowthMappingLifecycleStateV1 | str
    mapping: GrowthGoalChoiceMappingV1

    def __post_init__(self) -> None:
        object.__setattr__(self, "mapping_id", _normalize_uuid7(self.mapping_id))
        object.__setattr__(
            self, "lifecycle_state", _normalize_lifecycle_state(self.lifecycle_state)
        )
        if (
            type(self.mapping) is not GrowthGoalChoiceMappingV1
            or self.mapping_id != self.mapping.mapping_id
        ):
            raise ValueError("Growth lifecycle view is invalid")

    @property
    def record(self) -> GrowthGoalChoiceMappingV1:
        """Compatibility alias for the mechanical Stage 10C precedent."""

        return self.mapping

    def as_dict(self) -> dict[str, object]:
        return {
            "mapping_id": str(self.mapping_id),
            "lifecycle_state": cast(GrowthMappingLifecycleStateV1, self.lifecycle_state).value,
            "mapping": self.mapping.as_dict(),
        }


@dataclass(frozen=True, slots=True)
class GrowthMappingStoreRecordEnvelopeV1:
    """Integrity envelope around an accepted mapping or lifecycle event."""

    generation: UUID
    sequence: int
    record_type: str
    record: GrowthGoalChoiceMappingV1 | GrowthMappingLifecycleEventV1
    previous_digest: GrowthHashV1 | None
    current_digest: GrowthHashV1

    def __post_init__(self) -> None:
        object.__setattr__(self, "generation", _normalize_uuid7(self.generation))
        if type(self.sequence) is not int or self.sequence < 1:
            raise ValueError("Growth store sequence is invalid")
        if self.record_type not in _GROWTH_STORE_RECORD_TYPES:
            raise ValueError("Growth store record type is invalid")
        if (
            self.record_type == "accepted_mapping"
            and type(self.record) is not GrowthGoalChoiceMappingV1
        ) or (
            self.record_type == "lifecycle_event"
            and type(self.record) is not GrowthMappingLifecycleEventV1
        ):
            raise ValueError("Growth store record is invalid")
        if self.previous_digest is not None:
            validate_growth_hash(self.previous_digest)
        validate_growth_hash(self.current_digest)

    def digest_payload(self) -> dict[str, object]:
        return {
            "generation": str(self.generation),
            "sequence": self.sequence,
            "record_type": self.record_type,
            "record": self.record.as_dict(),
            "previous_digest": self.previous_digest,
        }

    def as_dict(self) -> dict[str, object]:
        return {**self.digest_payload(), "current_digest": self.current_digest}


@dataclass(frozen=True, slots=True)
class GrowthMappingStoreManifestV1:
    """Atomic manifest for the verified Growth mapping stream."""

    format_version: int
    contract_version: str
    mapping_policy_id: str
    mapping_policy_fingerprint: GrowthHashV1
    generation: UUID
    next_sequence: int
    record_count: int
    head_digest: GrowthHashV1 | None
    active_mapping_count: int
    manifest_fingerprint: GrowthHashV1

    def __post_init__(self) -> None:
        if (
            type(self.format_version) is not int
            or self.format_version != GROWTH_MAPPING_STORE_FORMAT_VERSION
            or type(self.contract_version) is not str
            or self.contract_version != GROWTH_CONTRACT_VERSION
            or type(self.mapping_policy_id) is not str
            or self.mapping_policy_id != GROWTH_MAPPING_POLICY_ID
        ):
            raise ValueError("Growth store manifest is invalid")
        validate_growth_hash(self.mapping_policy_fingerprint)
        if self.mapping_policy_fingerprint != GROWTH_MAPPING_POLICY_FINGERPRINT:
            raise ValueError("Growth store manifest policy is invalid")
        object.__setattr__(self, "generation", _normalize_uuid7(self.generation))
        if type(self.next_sequence) is not int or self.next_sequence < 1:
            raise ValueError("Growth store manifest is invalid")
        if type(self.record_count) is not int or self.record_count < 0:
            raise ValueError("Growth store manifest is invalid")
        if self.head_digest is not None:
            validate_growth_hash(self.head_digest)
        if (
            type(self.active_mapping_count) is not int
            or not 0 <= self.active_mapping_count <= GROWTH_MAPPING_MAX_ACTIVE
        ):
            raise ValueError("Growth store manifest is invalid")
        validate_growth_hash(self.manifest_fingerprint)
        if self.manifest_fingerprint != growth_hash_json(self.fingerprint_payload()):
            raise ValueError("Growth store manifest fingerprint is invalid")

    def fingerprint_payload(self) -> dict[str, object]:
        return {
            "active_mapping_count": self.active_mapping_count,
            "contract_version": self.contract_version,
            "format_version": self.format_version,
            "generation": str(self.generation),
            "head_digest": self.head_digest,
            "mapping_policy_fingerprint": self.mapping_policy_fingerprint,
            "mapping_policy_id": self.mapping_policy_id,
            "next_sequence": self.next_sequence,
            "record_count": self.record_count,
        }

    def as_dict(self) -> dict[str, object]:
        return {**self.fingerprint_payload(), "manifest_fingerprint": self.manifest_fingerprint}


@dataclass(frozen=True, slots=True)
class GrowthBehavioralPatternRefV1:
    """Raw-label-free reference to the current exact Stage 10 pattern."""

    contract_version: str
    derivation_version: str
    policy_id: str
    policy_fingerprint: GrowthHashV1
    cohort_fingerprint: GrowthHashV1
    pattern_type: BehavioralPatternTypeV1 | str
    pattern_state: BehavioralPatternStateV1 | str
    provenance_fingerprint: GrowthHashV1
    source_count: int
    reference_fingerprint: GrowthHashV1
    current_option: BehavioralOptionIdentityV1 | None

    def __post_init__(self) -> None:
        if (
            type(self.contract_version) is not str
            or self.contract_version != BEHAVIORAL_CONTRACT_VERSION
            or type(self.derivation_version) is not str
            or self.derivation_version != BEHAVIORAL_DERIVATION_VERSION
            or type(self.policy_id) is not str
            or self.policy_id != BEHAVIORAL_POLICY_ID
        ):
            raise ValueError("Growth behavioral pattern reference policy is invalid")
        validate_growth_hash(self.policy_fingerprint)
        if self.policy_fingerprint != BEHAVIORAL_POLICY_FINGERPRINT:
            raise ValueError("Growth behavioral pattern reference policy is invalid")
        validate_growth_hash(self.cohort_fingerprint)
        pattern_type = _normalize_behavioral_pattern_type(self.pattern_type)
        pattern_state = _normalize_behavioral_pattern_state(self.pattern_state)
        object.__setattr__(self, "pattern_type", pattern_type)
        object.__setattr__(self, "pattern_state", pattern_state)
        if type(self.source_count) is not int or self.source_count < 0:
            raise ValueError("Growth behavioral source count is invalid")
        validate_growth_hash(self.provenance_fingerprint)
        if (
            self.current_option is not None
            and type(self.current_option) is not BehavioralOptionIdentityV1
        ):
            raise ValueError("Growth behavioral current option is invalid")
        validate_growth_hash(self.reference_fingerprint)

    @property
    def behavioral_contract_version(self) -> str:
        """Descriptive alias matching the target identity vocabulary."""

        return self.contract_version

    @property
    def behavioral_derivation_version(self) -> str:
        """Descriptive alias matching the target identity vocabulary."""

        return self.derivation_version

    def as_dict(self) -> dict[str, object]:
        return {
            "contract_version": self.contract_version,
            "derivation_version": self.derivation_version,
            "policy_id": self.policy_id,
            "policy_fingerprint": self.policy_fingerprint,
            "cohort_fingerprint": self.cohort_fingerprint,
            "pattern_type": cast(BehavioralPatternTypeV1, self.pattern_type).value,
            "pattern_state": cast(BehavioralPatternStateV1, self.pattern_state).value,
            "provenance_fingerprint": self.provenance_fingerprint,
            "source_count": self.source_count,
            "reference_fingerprint": self.reference_fingerprint,
            "current_option": (
                self.current_option.as_dict() if self.current_option is not None else None
            ),
        }


@dataclass(frozen=True, slots=True)
class GrowthMappingRefV1:
    """Safe result reference to one active explicit mapping."""

    mapping_id: UUID
    mapping_policy_id: str
    mapping_fingerprint: GrowthHashV1
    relation: GrowthGoalRelationV1 | str

    def __post_init__(self) -> None:
        object.__setattr__(self, "mapping_id", _normalize_uuid7(self.mapping_id))
        if self.mapping_policy_id != GROWTH_MAPPING_POLICY_ID:
            raise ValueError("Growth mapping reference policy is invalid")
        validate_growth_hash(self.mapping_fingerprint)
        object.__setattr__(self, "relation", _normalize_growth_relation(self.relation))

    def as_dict(self) -> dict[str, object]:
        return {
            "mapping_id": str(self.mapping_id),
            "mapping_policy_id": self.mapping_policy_id,
            "mapping_fingerprint": self.mapping_fingerprint,
            "relation": cast(GrowthGoalRelationV1, self.relation).value,
        }


@dataclass(frozen=True, slots=True)
class GrowthTemporalContextV1:
    """Separate evidence, derivation, review, and optional advisor times."""

    goal_evidence_at: GrowthEvidenceAt
    goal_evidence_at_precision: str
    behavioral_generated_at: datetime | None
    behavioral_current_window_start: datetime | None
    behavioral_current_window_end: datetime | None
    mapping_reviewed_at: datetime | None
    mapping_created_at: datetime | None
    advisor_requested_at: datetime | None

    def __post_init__(self) -> None:
        evidence_at, precision = _normalize_evidence_time(
            self.goal_evidence_at, self.goal_evidence_at_precision
        )
        object.__setattr__(self, "goal_evidence_at", evidence_at)
        object.__setattr__(self, "goal_evidence_at_precision", precision)
        object.__setattr__(
            self,
            "behavioral_generated_at",
            _normalize_optional_growth_timestamp(self.behavioral_generated_at),
        )
        start = _normalize_optional_growth_timestamp(self.behavioral_current_window_start)
        end = _normalize_optional_growth_timestamp(self.behavioral_current_window_end)
        if (start is None) != (end is None) or (
            start is not None and end is not None and start > end
        ):
            raise ValueError("Growth behavioral window is invalid")
        object.__setattr__(self, "behavioral_current_window_start", start)
        object.__setattr__(self, "behavioral_current_window_end", end)
        object.__setattr__(
            self,
            "mapping_reviewed_at",
            _normalize_optional_growth_timestamp(self.mapping_reviewed_at),
        )
        object.__setattr__(
            self,
            "mapping_created_at",
            _normalize_optional_growth_timestamp(self.mapping_created_at),
        )
        object.__setattr__(
            self,
            "advisor_requested_at",
            _normalize_optional_growth_timestamp(self.advisor_requested_at),
        )

    def as_dict(self) -> dict[str, object]:
        return {
            "goal_evidence_at": _format_evidence_at(self.goal_evidence_at),
            "goal_evidence_at_precision": self.goal_evidence_at_precision,
            "behavioral_generated_at": (
                _format_growth_timestamp(self.behavioral_generated_at)
                if self.behavioral_generated_at is not None
                else None
            ),
            "behavioral_current_window_start": (
                _format_growth_timestamp(self.behavioral_current_window_start)
                if self.behavioral_current_window_start is not None
                else None
            ),
            "behavioral_current_window_end": (
                _format_growth_timestamp(self.behavioral_current_window_end)
                if self.behavioral_current_window_end is not None
                else None
            ),
            "mapping_reviewed_at": (
                _format_growth_timestamp(self.mapping_reviewed_at)
                if self.mapping_reviewed_at is not None
                else None
            ),
            "mapping_created_at": (
                _format_growth_timestamp(self.mapping_created_at)
                if self.mapping_created_at is not None
                else None
            ),
            "advisor_requested_at": (
                _format_growth_timestamp(self.advisor_requested_at)
                if self.advisor_requested_at is not None
                else None
            ),
        }


@dataclass(frozen=True, slots=True)
class GrowthAdvisorResultRefV1:
    """Future advisor reference shape; Stage 11B never emits or stores it."""

    request_id_fingerprint: GrowthHashV1
    result_kind: str
    advisor_policy_id: str
    requested_at: datetime
    generated_at: datetime

    def __post_init__(self) -> None:
        validate_growth_hash(self.request_id_fingerprint)
        if (
            type(self.result_kind) is not str
            or self.result_kind != "independent_recommendation_analysis"
            or type(self.advisor_policy_id) is not str
            or not 1 <= len(self.advisor_policy_id.encode("utf-8")) <= 128
        ):
            raise ValueError("Growth advisor reference is invalid")
        object.__setattr__(
            self, "requested_at", _normalize_canonical_growth_timestamp(self.requested_at)
        )
        object.__setattr__(
            self, "generated_at", _normalize_canonical_growth_timestamp(self.generated_at)
        )

    def as_dict(self) -> dict[str, object]:
        return {
            "request_id_fingerprint": self.request_id_fingerprint,
            "result_kind": self.result_kind,
            "advisor_policy_id": self.advisor_policy_id,
            "requested_at": _format_growth_timestamp(self.requested_at),
            "generated_at": _format_growth_timestamp(self.generated_at),
        }


@dataclass(frozen=True, slots=True)
class GrowthGoalRelationResultV1:
    """One deterministic Goal/cohort relation result with no aggregate score."""

    goal: GrowthGoalIdentityV1 | None
    state: GrowthRelationStateV1 | str
    cohort_fingerprint: GrowthHashV1 | None
    behavioral_pattern: GrowthBehavioralPatternRefV1 | None
    behavioral_option: BehavioralOptionIdentityV1 | None
    mapping: GrowthMappingRefV1 | None
    reason_codes: tuple[GrowthErrorCode | str, ...]
    caveats: tuple[GrowthCaveatCodeV1 | str, ...]
    temporal: GrowthTemporalContextV1
    advisor: GrowthAdvisorResultRefV1 | None = None

    def __post_init__(self) -> None:
        if self.goal is not None and type(self.goal) is not GrowthGoalIdentityV1:
            raise ValueError("Growth relation Goal is invalid")
        state = _normalize_growth_relation_state(self.state)
        object.__setattr__(self, "state", state)
        if self.cohort_fingerprint is not None:
            validate_growth_hash(self.cohort_fingerprint)
        if (
            self.behavioral_pattern is not None
            and type(self.behavioral_pattern) is not GrowthBehavioralPatternRefV1
        ):
            raise ValueError("Growth behavioral pattern reference is invalid")
        if (
            self.behavioral_option is not None
            and type(self.behavioral_option) is not BehavioralOptionIdentityV1
        ):
            raise ValueError("Growth behavioral option reference is invalid")
        if self.mapping is not None and type(self.mapping) is not GrowthMappingRefV1:
            raise ValueError("Growth mapping reference is invalid")
        if type(self.temporal) is not GrowthTemporalContextV1:
            raise ValueError("Growth relation temporal/advisor fields are invalid")
        if self.advisor is not None and type(self.advisor) is not GrowthAdvisorResultRefV1:
            raise ValueError("Growth relation advisor field is invalid")
        reason_codes = _normalize_reason_codes(self.reason_codes)
        caveats = _normalize_caveats(self.caveats)
        object.__setattr__(self, "reason_codes", reason_codes)
        object.__setattr__(self, "caveats", caveats)
        if (
            self.mapping is not None
            and state
            in {
                GrowthRelationStateV1.SUPPORTS_GOAL,
                GrowthRelationStateV1.CONFLICTS_WITH_GOAL,
                GrowthRelationStateV1.NEUTRAL_OR_UNKNOWN,
            }
            and self.behavioral_option is None
        ):
            raise ValueError("Growth binary relation option is missing")

    def as_dict(self) -> dict[str, object]:
        return {
            "goal": self.goal.as_dict() if self.goal is not None else None,
            "state": cast(GrowthRelationStateV1, self.state).value,
            "cohort_fingerprint": self.cohort_fingerprint,
            "behavioral_pattern": (
                self.behavioral_pattern.as_dict() if self.behavioral_pattern is not None else None
            ),
            "behavioral_option": (
                self.behavioral_option.as_dict() if self.behavioral_option is not None else None
            ),
            "mapping": self.mapping.as_dict() if self.mapping is not None else None,
            "reason_codes": list(self.reason_codes),
            "caveats": list(self.caveats),
            "temporal": self.temporal.as_dict(),
            "advisor": self.advisor.as_dict() if self.advisor is not None else None,
        }


GrowthRelationResultV1 = GrowthGoalRelationResultV1
GrowthGoalRelationResult = GrowthGoalRelationResultV1


@dataclass(frozen=True, slots=True)
class GrowthEngineResultV1:
    """Complete Stage 11B result; every Goal relation remains separate."""

    contract_version: str
    derivation_version: str
    policy_id: str
    policy_fingerprint: GrowthHashV1
    generated_at: datetime
    selection_mode: GrowthGoalSelectionModeV1
    selected_goal_source_uuid: UUID | None
    eligible_goal_count: int
    goal_results: tuple[GrowthGoalRelationResultV1, ...]
    reason_codes: tuple[GrowthErrorCode | str, ...]
    caveats: tuple[GrowthCaveatCodeV1 | str, ...]

    def __post_init__(self) -> None:
        if (
            type(self.contract_version) is not str
            or self.contract_version != GROWTH_CONTRACT_VERSION
            or type(self.derivation_version) is not str
            or self.derivation_version != GROWTH_DERIVATION_VERSION
            or type(self.policy_id) is not str
            or self.policy_id != GROWTH_POLICY_ID
        ):
            raise ValueError("Growth engine result policy is invalid")
        validate_growth_hash(self.policy_fingerprint)
        if self.policy_fingerprint != GROWTH_POLICY_FINGERPRINT:
            raise ValueError("Growth engine result policy fingerprint is invalid")
        object.__setattr__(
            self, "generated_at", _normalize_canonical_growth_timestamp(self.generated_at)
        )
        mode = _normalize_selection_mode(self.selection_mode)
        selected = _normalize_optional_uuid7(self.selected_goal_source_uuid)
        if mode is GrowthGoalSelectionModeV1.SELECTED_GOAL and selected is None:
            raise ValueError("Growth selected Goal result is invalid")
        if mode is GrowthGoalSelectionModeV1.EACH_CURRENT_GOAL and selected is not None:
            raise ValueError("Growth each-current result is invalid")
        if (
            type(self.eligible_goal_count) is not int
            or not 0 <= self.eligible_goal_count <= GROWTH_MAX_RESULTS
        ):
            raise ValueError("Growth eligible Goal count is invalid")
        if type(self.goal_results) is not tuple or len(self.goal_results) > GROWTH_MAX_RESULTS:
            raise ValueError("Growth Goal relation results are invalid")
        if any(type(item) is not GrowthGoalRelationResultV1 for item in self.goal_results):
            raise ValueError("Growth Goal relation results are invalid")
        expected_order = tuple(sorted(self.goal_results, key=_growth_relation_result_sort_key))
        if self.goal_results != expected_order:
            raise ValueError("Growth Goal relation results are not deterministic")
        goal_ids = {
            result.goal.source_note_uuid for result in self.goal_results if result.goal is not None
        }
        if self.eligible_goal_count < len(goal_ids):
            raise ValueError("Growth eligible Goal count is invalid")
        if (
            mode is GrowthGoalSelectionModeV1.SELECTED_GOAL
            and goal_ids
            and selected not in goal_ids
        ):
            raise ValueError("Growth selected Goal result is invalid")
        if mode is GrowthGoalSelectionModeV1.SELECTED_GOAL and any(
            result.goal is not None and result.goal.source_note_uuid != selected
            for result in self.goal_results
        ):
            raise ValueError("Growth selected Goal result is invalid")
        object.__setattr__(self, "selection_mode", mode)
        object.__setattr__(self, "selected_goal_source_uuid", selected)
        object.__setattr__(self, "reason_codes", _normalize_reason_codes(self.reason_codes))
        object.__setattr__(self, "caveats", _normalize_caveats(self.caveats))

    @property
    def results(self) -> tuple[GrowthGoalRelationResultV1, ...]:
        """Descriptive alias for consumers that call relations ``results``."""

        return self.goal_results

    def as_dict(self) -> dict[str, object]:
        return {
            "contract_version": self.contract_version,
            "derivation_version": self.derivation_version,
            "policy_id": self.policy_id,
            "policy_fingerprint": self.policy_fingerprint,
            "generated_at": _format_growth_timestamp(self.generated_at),
            "selection_mode": self.selection_mode.value,
            "selected_goal_source_uuid": (
                str(self.selected_goal_source_uuid)
                if self.selected_goal_source_uuid is not None
                else None
            ),
            "eligible_goal_count": self.eligible_goal_count,
            "goal_results": [item.as_dict() for item in self.goal_results],
            "reason_codes": list(self.reason_codes),
            "caveats": list(self.caveats),
        }

    def to_json(self) -> str:
        return canonical_growth_json(self.as_dict())


GrowthEngineResult = GrowthEngineResultV1
GrowthResultV1 = GrowthEngineResultV1


@dataclass(frozen=True, slots=True)
class GrowthMappingReviewOptionV1:
    """Transient human-readable option projection; never persisted."""

    option_index: int
    option_fingerprint: GrowthHashV1
    label: str

    def __post_init__(self) -> None:
        if type(self.option_index) is not int or not 0 <= self.option_index <= 19:
            raise ValueError("Growth review option is invalid")
        validate_growth_hash(self.option_fingerprint)
        if type(self.label) is not str or not self.label or len(self.label.encode("utf-8")) > 1024:
            raise ValueError("Growth review option is invalid")

    def as_dict(self) -> dict[str, object]:
        return {
            "option_index": self.option_index,
            "option_fingerprint": self.option_fingerprint,
            "label": self.label,
        }


GrowthReviewOptionV1 = GrowthMappingReviewOptionV1


@dataclass(frozen=True, slots=True)
class GrowthGoalChoiceMappingReviewProjectionV1:
    """Bounded transient owner-review projection with raw text only in memory."""

    generated_at: datetime
    goal: GrowthGoalIdentityV1
    behavioral_target: GrowthBehavioralTargetIdentityV1
    candidate_mapping_fingerprint: GrowthHashV1 | None
    goal_text: str
    goal_domain: str
    goal_evidence_at: GrowthEvidenceAt
    goal_evidence_at_precision: str
    situation: str
    information_known_at_decision_time: str
    criteria: tuple[str, ...]
    ordered_options: tuple[GrowthMappingReviewOptionV1, ...]
    pattern_type: BehavioralPatternTypeV1 | str
    pattern_state: BehavioralPatternStateV1 | str
    selected_option: BehavioralOptionIdentityV1
    proposed_relation: GrowthGoalRelationV1 | str | None
    caveats: tuple[GrowthCaveatCodeV1 | str, ...]

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "generated_at", _normalize_canonical_growth_timestamp(self.generated_at)
        )
        if type(self.goal) is not GrowthGoalIdentityV1:
            raise ValueError("Growth review Goal is invalid")
        if type(self.behavioral_target) is not GrowthBehavioralTargetIdentityV1:
            raise ValueError("Growth review behavioral target is invalid")
        if self.goal.domain != self.goal_domain or self.goal.domain is None:
            raise ValueError("Growth review domain is invalid")
        if self.behavioral_target.cohort.domain != self.goal_domain:
            raise ValueError("Growth review domain is invalid")
        if self.candidate_mapping_fingerprint is not None:
            validate_growth_hash(self.candidate_mapping_fingerprint)
        if (
            type(self.goal_text) is not str
            or not self.goal_text
            or len(self.goal_text.encode("utf-8")) > 4096
        ):
            raise ValueError("Growth review Goal text is invalid")
        evidence_at, precision = _normalize_evidence_time(
            self.goal_evidence_at, self.goal_evidence_at_precision
        )
        if evidence_at != self.goal.evidence_at or precision != self.goal.evidence_at_precision:
            raise ValueError("Growth review Goal evidence is invalid")
        for value, limit in (
            (self.situation, 8192),
            (self.information_known_at_decision_time, 8192),
        ):
            if type(value) is not str or len(value.encode("utf-8")) > limit:
                raise ValueError("Growth review context is invalid")
        if type(self.criteria) is not tuple or any(
            type(value) is not str or len(value.encode("utf-8")) > 2048 for value in self.criteria
        ):
            raise ValueError("Growth review criteria are invalid")
        if type(self.ordered_options) is not tuple or not self.ordered_options:
            raise ValueError("Growth review options are invalid")
        if any(type(item) is not GrowthMappingReviewOptionV1 for item in self.ordered_options):
            raise ValueError("Growth review options are invalid")
        indexes = tuple(item.option_index for item in self.ordered_options)
        if indexes != tuple(range(len(indexes))):
            raise ValueError("Growth review options are invalid")
        if self.selected_option != self.behavioral_target.option:
            raise ValueError("Growth review selected option is invalid")
        pattern_type = _normalize_behavioral_pattern_type(self.pattern_type)
        pattern_state = _normalize_behavioral_pattern_state(self.pattern_state)
        object.__setattr__(self, "pattern_type", pattern_type)
        object.__setattr__(self, "pattern_state", pattern_state)
        relation = _normalize_optional_growth_relation(self.proposed_relation)
        object.__setattr__(self, "proposed_relation", relation)
        if relation is not None:
            expected = compute_growth_goal_choice_mapping_fingerprint(
                self.goal, self.behavioral_target, relation
            )
            if self.candidate_mapping_fingerprint != expected:
                raise ValueError("Growth review candidate fingerprint is invalid")
        elif self.candidate_mapping_fingerprint is not None:
            raise ValueError("Growth review candidate fingerprint is invalid")
        object.__setattr__(self, "caveats", _normalize_caveats(self.caveats))
        try:
            if (
                len(canonical_growth_json(self.as_dict()).encode("utf-8"))
                > GROWTH_MAPPING_MAX_REVIEW_PROJECTION_BYTES
            ):
                raise GrowthResultTooLargeError()
        except GrowthError:
            raise
        except TypeError, ValueError, UnicodeError:
            raise GrowthResultTooLargeError() from None

    @property
    def claim_text(self) -> str:
        """Compatibility alias for review clients using the Stage 10C term."""

        return self.goal_text

    @property
    def cohort_domain(self) -> str:
        """Compatibility alias for the exact behavioral domain."""

        return self.goal_domain

    def as_dict(self) -> dict[str, object]:
        return {
            "generated_at": _format_growth_timestamp(self.generated_at),
            "goal": self.goal.as_dict(),
            "behavioral_target": self.behavioral_target.as_dict(),
            "candidate_mapping_fingerprint": self.candidate_mapping_fingerprint,
            "goal_text": self.goal_text,
            "goal_domain": self.goal_domain,
            "goal_evidence_at": _format_evidence_at(self.goal_evidence_at),
            "goal_evidence_at_precision": self.goal_evidence_at_precision,
            "situation": self.situation,
            "information_known_at_decision_time": self.information_known_at_decision_time,
            "criteria": list(self.criteria),
            "ordered_options": [item.as_dict() for item in self.ordered_options],
            "pattern_type": cast(BehavioralPatternTypeV1, self.pattern_type).value,
            "pattern_state": cast(BehavioralPatternStateV1, self.pattern_state).value,
            "selected_option": self.selected_option.as_dict(),
            "proposed_relation": (
                cast(GrowthGoalRelationV1, self.proposed_relation).value
                if self.proposed_relation is not None
                else None
            ),
            "caveats": list(self.caveats),
        }

    def to_json(self) -> str:
        return canonical_growth_json(self.as_dict())


GrowthMappingReviewProjectionV1 = GrowthGoalChoiceMappingReviewProjectionV1
GrowthGoalChoiceReviewProjectionV1 = GrowthGoalChoiceMappingReviewProjectionV1


@dataclass(frozen=True, slots=True)
class GrowthMappingReviewRequestV1:
    """Exact selector plus an optional proposed relation for transient review."""

    selector: GrowthGoalChoiceMappingSelectorV1
    relation: GrowthGoalRelationV1 | str | None = None

    def __post_init__(self) -> None:
        if type(self.selector) is not GrowthGoalChoiceMappingSelectorV1:
            raise ValueError("Growth review request is invalid")
        object.__setattr__(self, "relation", _normalize_optional_growth_relation(self.relation))

    @property
    def proposed_relation(self) -> GrowthGoalRelationV1 | None:
        return cast(GrowthGoalRelationV1 | None, self.relation)


GrowthGoalChoiceMappingReviewRequestV1 = GrowthMappingReviewRequestV1
GrowthGoalChoiceReviewRequestV1 = GrowthMappingReviewRequestV1


@dataclass(frozen=True, slots=True)
class GrowthMappingAcceptanceRequestV1:
    """Explicit owner confirmation request; values are revalidated server-side."""

    selector: GrowthGoalChoiceMappingSelectorV1
    operation_id: UUID
    relation: GrowthGoalRelationV1 | str
    confirmed: bool
    review_projection: GrowthGoalChoiceMappingReviewProjectionV1 | None
    supersedes_mapping_id: UUID | None = None

    def __post_init__(self) -> None:
        if type(self.selector) is not GrowthGoalChoiceMappingSelectorV1:
            raise ValueError("Growth acceptance selector is invalid")
        object.__setattr__(self, "operation_id", _normalize_uuid7(self.operation_id))
        object.__setattr__(self, "relation", _normalize_growth_relation(self.relation))
        if type(self.confirmed) is not bool:
            raise ValueError("Growth confirmation is invalid")
        if (
            self.review_projection is not None
            and type(self.review_projection) is not GrowthGoalChoiceMappingReviewProjectionV1
        ):
            raise ValueError("Growth review projection is invalid")
        if self.supersedes_mapping_id is not None:
            object.__setattr__(
                self,
                "supersedes_mapping_id",
                _normalize_uuid7(self.supersedes_mapping_id),
            )


GrowthGoalChoiceMappingAcceptanceRequestV1 = GrowthMappingAcceptanceRequestV1
GrowthGoalChoiceAcceptanceRequestV1 = GrowthMappingAcceptanceRequestV1


def validate_growth_mapping_policy() -> GrowthHashV1:
    """Validate the exact Stage 11B mapping-policy fingerprint."""

    payload = {
        "basis": GROWTH_MAPPING_BASIS,
        "cardinality": "one-goal-one-target-per-record-v1",
        "contract": GROWTH_CONTRACT_VERSION,
        "domain": "exact-goal-domain-equals-cohort-domain-v1",
        "lifecycle": "append-only-owner-reviewed-v1",
        "persistence": "dedicated-operational-growth-mapping-v1",
        "relation": "supports-conflicts-neutral-v1",
        "target": "stage10-exact-cohort-option-v1",
        "temporal": "separate-times-no-backfill-v1",
        "version": "1",
    }
    if canonical_growth_json(payload) != GROWTH_MAPPING_POLICY_CANONICAL_JSON:
        raise GrowthPolicyMismatchError()
    fingerprint = growth_hash_json(payload)
    if fingerprint != GROWTH_MAPPING_POLICY_FINGERPRINT:
        raise GrowthPolicyMismatchError()
    return fingerprint


def _normalize_growth_relation(value: GrowthGoalRelationV1 | str) -> GrowthGoalRelationV1:
    if type(value) is GrowthGoalRelationV1:
        return value
    if type(value) is str:
        try:
            return GrowthGoalRelationV1(value)
        except ValueError:
            pass
    raise ValueError("Growth relation is invalid")


def _normalize_optional_growth_relation(
    value: GrowthGoalRelationV1 | str | None,
) -> GrowthGoalRelationV1 | None:
    if value is None:
        return None
    return _normalize_growth_relation(value)


def _normalize_growth_relation_state(value: GrowthRelationStateV1 | str) -> GrowthRelationStateV1:
    if type(value) is GrowthRelationStateV1:
        return value
    if type(value) is str:
        try:
            return GrowthRelationStateV1(value)
        except ValueError:
            pass
    raise ValueError("Growth relation state is invalid")


def _normalize_lifecycle_state(
    value: GrowthMappingLifecycleStateV1 | str,
) -> GrowthMappingLifecycleStateV1:
    if type(value) is GrowthMappingLifecycleStateV1:
        return value
    if type(value) is str:
        try:
            return GrowthMappingLifecycleStateV1(value)
        except ValueError:
            pass
    raise ValueError("Growth lifecycle state is invalid")


def _normalize_behavioral_pattern_type(
    value: BehavioralPatternTypeV1 | str,
) -> BehavioralPatternTypeV1:
    if type(value) is BehavioralPatternTypeV1:
        return value
    if type(value) is str:
        try:
            return BehavioralPatternTypeV1(value)
        except ValueError:
            pass
    raise ValueError("Growth behavioral pattern type is invalid")


def _normalize_behavioral_pattern_state(
    value: BehavioralPatternStateV1 | str,
) -> BehavioralPatternStateV1:
    if type(value) is BehavioralPatternStateV1:
        return value
    if type(value) is str:
        try:
            return BehavioralPatternStateV1(value)
        except ValueError:
            pass
    raise ValueError("Growth behavioral pattern state is invalid")


def _normalize_canonical_growth_timestamp(value: object) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("Growth timestamp is invalid")
    return value.astimezone(UTC)


def _normalize_optional_growth_timestamp(value: object) -> datetime | None:
    if value is None:
        return None
    return _normalize_canonical_growth_timestamp(value)


def _format_growth_timestamp(value: datetime) -> str:
    normalized = _normalize_canonical_growth_timestamp(value)
    rendered = normalized.isoformat(
        timespec="microseconds" if normalized.microsecond else "seconds"
    )
    return rendered.removesuffix("+00:00") + "Z"


def _parse_growth_timestamp(value: object) -> datetime:
    if isinstance(value, datetime):
        return _normalize_canonical_growth_timestamp(value)
    if type(value) is not str:
        raise ValueError("Growth timestamp is invalid")
    try:
        return _normalize_canonical_growth_timestamp(parse_rfc3339(value))
    except TypeError, ValueError, OverflowError:
        raise ValueError("Growth timestamp is invalid") from None


def growth_operation_id_fingerprint(operation_id: UUID) -> GrowthHashV1:
    """Derive the only durable representation of an acceptance operation id."""

    return growth_hash_text(str(_normalize_uuid7(operation_id)))


def compute_growth_goal_choice_mapping_fingerprint(
    goal: GrowthGoalIdentityV1,
    behavioral_target: GrowthBehavioralTargetIdentityV1,
    relation: GrowthGoalRelationV1 | str,
) -> GrowthHashV1:
    """Hash exact Goal/target/relation identity without labels or timestamps."""

    if (
        type(goal) is not GrowthGoalIdentityV1
        or type(behavioral_target) is not GrowthBehavioralTargetIdentityV1
    ):
        raise ValueError("Growth mapping identity is invalid")
    normalized_relation = _normalize_growth_relation(relation)
    if goal.domain is None or goal.domain != behavioral_target.cohort.domain:
        raise ValueError("Growth mapping domains are invalid")
    return growth_hash_json(
        {
            "behavioral_target": behavioral_target.as_dict(),
            "contract_version": GROWTH_CONTRACT_VERSION,
            "goal": goal.as_dict(),
            "mapping_basis": GROWTH_MAPPING_BASIS,
            "mapping_policy_fingerprint": GROWTH_MAPPING_POLICY_FINGERPRINT,
            "mapping_policy_id": GROWTH_MAPPING_POLICY_ID,
            "relation": normalized_relation.value,
        }
    )


growth_goal_choice_mapping_fingerprint = compute_growth_goal_choice_mapping_fingerprint
compute_growth_mapping_fingerprint = compute_growth_goal_choice_mapping_fingerprint


def compute_growth_behavioral_pattern_reference_fingerprint(
    pattern: BehavioralPatternV1,
    *,
    current_option: BehavioralOptionIdentityV1 | None = None,
) -> GrowthHashV1:
    """Hash the complete raw-label-free Stage 10 pattern reference."""

    if type(pattern) is not BehavioralPatternV1:
        raise ValueError("Growth behavioral pattern is invalid")
    option = current_option if current_option is not None else pattern.selected_option
    if option is not None and type(option) is not BehavioralOptionIdentityV1:
        raise ValueError("Growth behavioral option is invalid")
    return growth_hash_json(
        {
            "behavioral_pattern": pattern.as_dict(),
            "current_option": option.as_dict() if option is not None else None,
        }
    )


growth_behavioral_pattern_reference_fingerprint = (
    compute_growth_behavioral_pattern_reference_fingerprint
)


def _growth_pattern_ref(
    pattern: BehavioralPatternV1,
    *,
    current_option: BehavioralOptionIdentityV1 | None,
) -> GrowthBehavioralPatternRefV1:
    if pattern.cohort is None:
        raise ValueError("Growth behavioral pattern cohort is missing")
    return GrowthBehavioralPatternRefV1(
        contract_version=BEHAVIORAL_CONTRACT_VERSION,
        derivation_version=BEHAVIORAL_DERIVATION_VERSION,
        policy_id=BEHAVIORAL_POLICY_ID,
        policy_fingerprint=BEHAVIORAL_POLICY_FINGERPRINT,
        cohort_fingerprint=pattern.cohort.cohort_fingerprint,
        pattern_type=pattern.pattern_type,
        pattern_state=pattern.state,
        provenance_fingerprint=pattern.provenance.provenance_fingerprint,
        source_count=pattern.provenance.source_count,
        reference_fingerprint=compute_growth_behavioral_pattern_reference_fingerprint(
            pattern, current_option=current_option
        ),
        current_option=current_option,
    )


def _growth_relation_result_sort_key(
    result: GrowthGoalRelationResultV1,
) -> tuple[str, str, str, str]:
    return (
        str(result.goal.source_note_uuid) if result.goal is not None else "",
        result.cohort_fingerprint or "",
        cast(GrowthRelationStateV1, result.state).value,
        result.mapping.mapping_id.__str__() if result.mapping is not None else "",
    )


def _growth_strict_keys(value: Mapping[str, object], expected: set[str]) -> None:
    if set(value) != expected:
        raise ValueError("Growth record fields are invalid")


def _growth_goal_from_dict(value: object) -> GrowthGoalIdentityV1:
    if not isinstance(value, Mapping):
        raise ValueError("Growth Goal identity is invalid")
    _growth_strict_keys(
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
    return GrowthGoalIdentityV1(
        source_note_uuid=_normalize_uuid7(value["source_note_uuid"]),
        dimension=value["dimension"],
        source_evidence_kind=value["source_evidence_kind"],
        source_self_kind=value["source_self_kind"],
        domain=value["domain"],
        evidence_at=value["evidence_at"],
        evidence_at_precision=value["evidence_at_precision"],
        source_contract_version=value["source_contract_version"],
        source_derivation_version=value["source_derivation_version"],
        self_model_policy_fingerprint=value["self_model_policy_fingerprint"],
        source_fingerprint=value["source_fingerprint"],
        claim_fingerprint=value["claim_fingerprint"],
    )


def _growth_option_from_dict(value: object) -> BehavioralOptionIdentityV1:
    if not isinstance(value, Mapping):
        raise ValueError("Growth behavioral option is invalid")
    _growth_strict_keys(value, {"option_index", "option_fingerprint"})
    return BehavioralOptionIdentityV1(
        option_index=value["option_index"],
        option_fingerprint=value["option_fingerprint"],
    )


def _growth_cohort_from_dict(value: object) -> BehavioralCohortIdentityV1:
    if not isinstance(value, Mapping):
        raise ValueError("Growth behavioral cohort is invalid")
    _growth_strict_keys(
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
        grouping_policy=value["grouping_policy"],
        domain=value["domain"],
        situation_fingerprint=value["situation_fingerprint"],
        information_fingerprint=value["information_fingerprint"],
        option_namespace_fingerprint=value["option_namespace_fingerprint"],
        criteria_fingerprint=value["criteria_fingerprint"],
        cohort_fingerprint=value["cohort_fingerprint"],
    )


def _growth_target_from_dict(value: object) -> GrowthBehavioralTargetIdentityV1:
    if not isinstance(value, Mapping):
        raise ValueError("Growth behavioral target is invalid")
    _growth_strict_keys(
        value,
        {
            "behavioral_contract_version",
            "behavioral_derivation_version",
            "observation_version",
            "policy_id",
            "policy_fingerprint",
            "cohort",
            "option",
            "comparison_basis",
        },
    )
    return GrowthBehavioralTargetIdentityV1(
        behavioral_contract_version=value["behavioral_contract_version"],
        behavioral_derivation_version=value["behavioral_derivation_version"],
        observation_version=value["observation_version"],
        policy_id=value["policy_id"],
        policy_fingerprint=value["policy_fingerprint"],
        cohort=_growth_cohort_from_dict(value["cohort"]),
        option=_growth_option_from_dict(value["option"]),
        comparison_basis=value["comparison_basis"],
    )


def _growth_mapping_from_dict(value: object) -> GrowthGoalChoiceMappingV1:
    if not isinstance(value, Mapping):
        raise ValueError("Growth mapping is invalid")
    _growth_strict_keys(
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
            "goal",
            "behavioral_target",
            "relation",
            "mapping_fingerprint",
            "supersedes_mapping_id",
        },
    )
    return GrowthGoalChoiceMappingV1(
        contract_version=value["contract_version"],
        mapping_policy_id=value["mapping_policy_id"],
        mapping_policy_fingerprint=value["mapping_policy_fingerprint"],
        mapping_id=_normalize_uuid7(value["mapping_id"]),
        acceptance_operation_id_fingerprint=value["acceptance_operation_id_fingerprint"],
        created_at=_parse_growth_timestamp(value["created_at"]),
        reviewed_at=_parse_growth_timestamp(value["reviewed_at"]),
        mapping_basis=value["mapping_basis"],
        goal=_growth_goal_from_dict(value["goal"]),
        behavioral_target=_growth_target_from_dict(value["behavioral_target"]),
        relation=value["relation"],
        mapping_fingerprint=value["mapping_fingerprint"],
        supersedes_mapping_id=(
            _normalize_uuid7(value["supersedes_mapping_id"])
            if value["supersedes_mapping_id"] is not None
            else None
        ),
    )


def _growth_event_from_dict(value: object) -> GrowthMappingLifecycleEventV1:
    if not isinstance(value, Mapping):
        raise ValueError("Growth lifecycle event is invalid")
    _growth_strict_keys(
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
    return GrowthMappingLifecycleEventV1(
        event_id=_normalize_uuid7(value["event_id"]),
        mapping_id=_normalize_uuid7(value["mapping_id"]),
        lifecycle_state=value["lifecycle_state"],
        occurred_at=_parse_growth_timestamp(value["occurred_at"]),
        operation_id_fingerprint=value["operation_id_fingerprint"],
        reason_code=value["reason_code"],
        replacement_mapping_id=(
            _normalize_uuid7(value["replacement_mapping_id"])
            if value["replacement_mapping_id"] is not None
            else None
        ),
    )


def _growth_manifest_from_dict(value: object) -> GrowthMappingStoreManifestV1:
    if not isinstance(value, Mapping):
        raise ValueError("Growth store manifest is invalid")
    _growth_strict_keys(
        value,
        {
            "format_version",
            "contract_version",
            "mapping_policy_id",
            "mapping_policy_fingerprint",
            "generation",
            "next_sequence",
            "record_count",
            "head_digest",
            "active_mapping_count",
            "manifest_fingerprint",
        },
    )
    return GrowthMappingStoreManifestV1(
        format_version=value["format_version"],
        contract_version=value["contract_version"],
        mapping_policy_id=value["mapping_policy_id"],
        mapping_policy_fingerprint=value["mapping_policy_fingerprint"],
        generation=_normalize_uuid7(value["generation"]),
        next_sequence=value["next_sequence"],
        record_count=value["record_count"],
        head_digest=value["head_digest"],
        active_mapping_count=value["active_mapping_count"],
        manifest_fingerprint=value["manifest_fingerprint"],
    )


def _growth_envelope_from_dict(value: object) -> GrowthMappingStoreRecordEnvelopeV1:
    if not isinstance(value, Mapping):
        raise ValueError("Growth store envelope is invalid")
    _growth_strict_keys(
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
    if type(record_type) is not str:
        raise ValueError("Growth store record type is invalid")
    record: GrowthGoalChoiceMappingV1 | GrowthMappingLifecycleEventV1
    if record_type == "accepted_mapping":
        record = _growth_mapping_from_dict(value["record"])
    elif record_type == "lifecycle_event":
        record = _growth_event_from_dict(value["record"])
    else:
        raise ValueError("Growth store record type is invalid")
    return GrowthMappingStoreRecordEnvelopeV1(
        generation=_normalize_uuid7(value["generation"]),
        sequence=value["sequence"],
        record_type=record_type,
        record=record,
        previous_digest=value["previous_digest"],
        current_digest=value["current_digest"],
    )


def _growth_envelope_digest(envelope: GrowthMappingStoreRecordEnvelopeV1) -> GrowthHashV1:
    return growth_hash_json(envelope.digest_payload())


@dataclass(frozen=True, slots=True)
class _VerifiedGrowthMappingStoreState:
    manifest: GrowthMappingStoreManifestV1
    envelopes: tuple[GrowthMappingStoreRecordEnvelopeV1, ...]
    mappings: dict[UUID, GrowthGoalChoiceMappingV1]
    lifecycle: dict[UUID, GrowthMappingLifecycleStateV1]


def _growth_is_posix() -> bool:
    return os.name != "nt"


def _growth_mode(path: Path) -> int:
    return stat.S_IMODE(path.stat().st_mode)


def _growth_path_is_symlink(path: Path) -> bool:
    try:
        return path.is_symlink()
    except OSError:
        return True


def _growth_has_symlink_component(path: Path) -> bool:
    current = Path(path.anchor)
    for part in path.parts[1:]:
        current /= part
        if _growth_path_is_symlink(current):
            return True
    return False


def _growth_platform_attribute(module: object, name: str) -> object:
    return getattr(module, name)


def _growth_platform_call(module: object, name: str, argument: int) -> object:
    return cast(Callable[[int], object], _growth_platform_attribute(module, name))(argument)


def _growth_owner_group(
    info: os.stat_result,
) -> tuple[str, str]:
    try:
        import grp
        import pwd

        owner_name = "pw_name"
        group_name = "gr_name"
        owner = str(getattr(_growth_platform_call(pwd, "getpwuid", info.st_uid), owner_name))
        group = str(getattr(_growth_platform_call(grp, "getgrgid", info.st_gid), group_name))
    except KeyError, OSError, ImportError:
        raise GrowthMappingStoreUnavailableError() from None
    return owner, group


def _growth_safe_directory(path: Path, expected_owner_group: tuple[str, str] | None) -> None:
    try:
        info = path.lstat()
    except OSError:
        raise GrowthMappingStoreUnavailableError() from None
    if (
        stat.S_ISLNK(info.st_mode)
        or not stat.S_ISDIR(info.st_mode)
        or (_growth_is_posix() and _growth_mode(path) != 0o700)
    ):
        raise GrowthMappingStoreUnavailableError()
    if (
        expected_owner_group is not None
        and _growth_is_posix()
        and _growth_owner_group(info) != expected_owner_group
    ):
        raise GrowthMappingStoreUnavailableError()


def _growth_safe_file(
    path: Path,
    expected_owner_group: tuple[str, str] | None = None,
) -> None:
    try:
        info = path.lstat()
    except OSError:
        raise GrowthMappingStoreUnavailableError() from None
    if (
        stat.S_ISLNK(info.st_mode)
        or not stat.S_ISREG(info.st_mode)
        or (_growth_is_posix() and _growth_mode(path) != 0o600)
    ):
        raise GrowthMappingStoreUnavailableError()
    if (
        expected_owner_group is not None
        and _growth_is_posix()
        and _growth_owner_group(info) != expected_owner_group
    ):
        raise GrowthMappingStoreUnavailableError()


def _growth_mapping_key(
    mapping: GrowthGoalChoiceMappingV1,
) -> tuple[UUID, str, str, str, int, str]:
    return (
        cast(UUID, mapping.goal.source_note_uuid),
        mapping.goal.source_fingerprint,
        mapping.goal.claim_fingerprint,
        mapping.behavioral_target.cohort.cohort_fingerprint,
        mapping.behavioral_target.option.option_index,
        mapping.behavioral_target.option.option_fingerprint,
    )


def _growth_transition_is_valid(
    old: GrowthMappingLifecycleStateV1,
    new: GrowthMappingLifecycleStateV1,
) -> bool:
    return (
        old is GrowthMappingLifecycleStateV1.ACTIVE
        and new
        in {
            GrowthMappingLifecycleStateV1.SUPERSEDED,
            GrowthMappingLifecycleStateV1.INVALIDATED,
            GrowthMappingLifecycleStateV1.DELETED,
        }
    ) or (
        old
        in {
            GrowthMappingLifecycleStateV1.SUPERSEDED,
            GrowthMappingLifecycleStateV1.INVALIDATED,
        }
        and new is GrowthMappingLifecycleStateV1.DELETED
    )


class GrowthMappingStore:
    """Dedicated verified append-only store for explicit Growth mappings."""

    records_filename: Final[str] = GROWTH_MAPPING_RECORD_FILE_NAME
    manifest_filename: Final[str] = GROWTH_MAPPING_MANIFEST_FILE_NAME
    lock_filename: Final[str] = GROWTH_MAPPING_LOCK_FILE_NAME

    def __init__(
        self,
        root: Path,
        *,
        expected_owner_group: tuple[str, str] | None = None,
        vault_root: Path | None = None,
        repository_root: Path | None = None,
        clock: GrowthClock = lambda: datetime.now(UTC),
    ) -> None:
        if not isinstance(root, Path) or not root.is_absolute() or not callable(clock):
            raise GrowthMappingStoreUnavailableError()
        if root == Path(root.anchor):
            raise GrowthMappingStoreUnavailableError()
        if expected_owner_group is not None and (
            type(expected_owner_group) is not tuple
            or len(expected_owner_group) != 2
            or any(type(item) is not str or not item for item in expected_owner_group)
        ):
            raise GrowthMappingStoreUnavailableError()
        for boundary in (vault_root, repository_root):
            if boundary is not None and (
                not isinstance(boundary, Path) or not boundary.is_absolute()
            ):
                raise GrowthMappingStoreUnavailableError()
        self.root = root
        self.expected_owner_group = (
            expected_owner_group
            if expected_owner_group is not None
            else (
                PRODUCTION_GROWTH_MAPPING_OWNER_GROUP
                if root == PRODUCTION_GROWTH_MAPPING_STORE_ROOT
                else None
            )
        )
        self.vault_root = vault_root
        self.repository_root = repository_root
        self.clock = clock
        self.records_path = root / GROWTH_MAPPING_RECORD_FILE_NAME
        self.mappings_path = self.records_path
        self.events_path = self.records_path
        self.manifest_path = root / GROWTH_MAPPING_MANIFEST_FILE_NAME
        self.lock_path = root / GROWTH_MAPPING_LOCK_FILE_NAME
        self.lockfile_path = self.lock_path
        self._validate_root_path()

    def _validate_root_path(self) -> None:
        if _growth_path_is_symlink(self.root):
            raise GrowthMappingStoreUnavailableError()
        existing = self.root if self.root.exists() else self.root.parent
        if _growth_path_is_symlink(existing) or not existing.is_dir():
            raise GrowthMappingStoreUnavailableError()
        current = Path(self.root.anchor)
        for part in self.root.parts[1:]:
            current /= part
            if _growth_path_is_symlink(current):
                raise GrowthMappingStoreUnavailableError()
        self._validate_containment()
        if self.root == PRODUCTION_GROWTH_MAPPING_STORE_ROOT:
            if self.root.parent != GROWTH_MAPPING_RUNTIME_ROOT:
                raise GrowthMappingStoreUnavailableError()
            try:
                if self.root.parent.resolve(strict=True) != GROWTH_MAPPING_RUNTIME_ROOT.resolve(
                    strict=True
                ):
                    raise GrowthMappingStoreUnavailableError()
            except OSError:
                raise GrowthMappingStoreUnavailableError() from None
        if self.root.exists():
            _growth_safe_directory(self.root, self.expected_owner_group)
            for child in (self.records_path, self.manifest_path, self.lock_path):
                if _growth_path_is_symlink(child):
                    raise GrowthMappingStoreUnavailableError()
                if child.exists():
                    _growth_safe_file(child, self.expected_owner_group)
            try:
                entries = tuple(self.root.iterdir())
            except OSError:
                raise GrowthMappingStoreUnavailableError() from None
            allowed = {
                self.records_path.name,
                self.manifest_path.name,
                self.lock_path.name,
            }
            for entry in entries:
                if entry.name not in allowed:
                    raise GrowthMappingStoreUnavailableError()

    def _validate_containment(self) -> None:
        try:
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
                    raise GrowthMappingStoreUnavailableError()
        except GrowthError:
            raise
        except OSError:
            raise GrowthMappingStoreUnavailableError() from None

    def _create_root_if_missing(self) -> None:
        self._validate_root_path()
        if (
            not self.root.exists()
            and self.root == PRODUCTION_GROWTH_MAPPING_STORE_ROOT
            and (
                not self.root.parent.exists()
                or self.root.parent.resolve() != GROWTH_MAPPING_RUNTIME_ROOT.resolve()
            )
        ):
            raise GrowthMappingStoreUnavailableError()
        if not self.root.exists():
            try:
                self.root.mkdir(mode=0o700)
            except FileExistsError:
                pass
            except OSError:
                raise GrowthMappingStoreUnavailableError() from None
        _growth_safe_directory(self.root, self.expected_owner_group)

    @contextmanager
    def _locked(self, *, create: bool) -> Iterator[bool]:
        if create:
            self._create_root_if_missing()
        else:
            self._validate_root_path()
            if not self.root.exists():
                yield False
                return
            if not self.records_path.exists() and not self.manifest_path.exists():
                yield False
                return
            if self.records_path.exists() != self.manifest_path.exists():
                raise GrowthMappingStoreCorruptError()
        try:
            descriptor = os.open(self.lock_path, os.O_RDWR | os.O_CREAT, 0o600)
            lock_file = os.fdopen(descriptor, "r+b", buffering=0)
        except OSError:
            raise GrowthMappingStoreUnavailableError() from None
        try:
            _growth_safe_file(self.lock_path, self.expected_owner_group)
            if _growth_is_posix():
                import fcntl

                try:
                    cast(Callable[[int, int], object], _growth_platform_attribute(fcntl, "flock"))(
                        lock_file.fileno(),
                        cast(int, _growth_platform_attribute(fcntl, "LOCK_EX")),
                    )
                except OSError:
                    raise GrowthMappingStoreUnavailableError() from None
            else:
                import msvcrt

                try:
                    if os.path.getsize(self.lock_path) == 0:
                        lock_file.write(b"0")
                        lock_file.flush()
                    lock_file.seek(0)
                    cast(
                        Callable[[int, int, int], object],
                        _growth_platform_attribute(msvcrt, "locking"),
                    )(
                        lock_file.fileno(),
                        cast(int, _growth_platform_attribute(msvcrt, "LK_LOCK")),
                        1,
                    )
                except OSError:
                    raise GrowthMappingStoreUnavailableError() from None
            yield True
        finally:
            try:
                if _growth_is_posix():
                    import fcntl

                    cast(Callable[[int, int], object], _growth_platform_attribute(fcntl, "flock"))(
                        lock_file.fileno(),
                        cast(int, _growth_platform_attribute(fcntl, "LOCK_UN")),
                    )
                else:
                    import msvcrt

                    lock_file.seek(0)
                    cast(
                        Callable[[int, int, int], object],
                        _growth_platform_attribute(msvcrt, "locking"),
                    )(
                        lock_file.fileno(),
                        cast(int, _growth_platform_attribute(msvcrt, "LK_UNLCK")),
                        1,
                    )
            except OSError:
                pass
            lock_file.close()

    def _ensure_initialized_locked(self) -> None:
        records_exists = self.records_path.exists()
        manifest_exists = self.manifest_path.exists()
        if records_exists != manifest_exists:
            raise GrowthMappingStoreCorruptError()
        if records_exists:
            _growth_safe_file(self.records_path, self.expected_owner_group)
            _growth_safe_file(self.manifest_path, self.expected_owner_group)
            return
        for path in (self.records_path, self.manifest_path):
            try:
                descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                try:
                    os.fsync(descriptor)
                finally:
                    os.close(descriptor)
            except FileExistsError:
                raise GrowthMappingStoreCorruptError() from None
            except OSError:
                raise GrowthMappingStoreUnavailableError() from None
        generation = uuid7()
        empty_payload = {
            "active_mapping_count": 0,
            "contract_version": GROWTH_CONTRACT_VERSION,
            "format_version": GROWTH_MAPPING_STORE_FORMAT_VERSION,
            "generation": str(generation),
            "head_digest": None,
            "mapping_policy_fingerprint": GROWTH_MAPPING_POLICY_FINGERPRINT,
            "mapping_policy_id": GROWTH_MAPPING_POLICY_ID,
            "next_sequence": 1,
            "record_count": 0,
        }
        manifest = GrowthMappingStoreManifestV1(
            format_version=GROWTH_MAPPING_STORE_FORMAT_VERSION,
            contract_version=GROWTH_CONTRACT_VERSION,
            mapping_policy_id=GROWTH_MAPPING_POLICY_ID,
            mapping_policy_fingerprint=GROWTH_MAPPING_POLICY_FINGERPRINT,
            generation=generation,
            next_sequence=1,
            record_count=0,
            head_digest=None,
            active_mapping_count=0,
            manifest_fingerprint=growth_hash_json(empty_payload),
        )
        self._atomic_manifest_write(manifest)

    def _atomic_manifest_write(self, manifest: GrowthMappingStoreManifestV1) -> None:
        temporary: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                dir=self.root,
                prefix=".manifest.",
                suffix=".tmp",
                delete=False,
                newline="\n",
            ) as stream:
                temporary = Path(stream.name)
                stream.write(canonical_growth_json(manifest.as_dict()))
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
            _growth_safe_file(temporary, self.expected_owner_group)
            os.replace(temporary, self.manifest_path)
            _growth_safe_file(self.manifest_path, self.expected_owner_group)
            if _growth_is_posix():
                descriptor = os.open(self.root, os.O_RDONLY)
                try:
                    os.fsync(descriptor)
                finally:
                    os.close(descriptor)
        except GrowthError:
            if temporary is not None:
                with suppress(OSError):
                    temporary.unlink(missing_ok=True)
            raise
        except OSError, TypeError, ValueError, UnicodeError:
            if temporary is not None:
                with suppress(OSError):
                    temporary.unlink(missing_ok=True)
            raise GrowthMappingStoreUnavailableError() from None

    def _read_manifest_locked(self) -> GrowthMappingStoreManifestV1:
        try:
            raw = self.manifest_path.read_bytes()
            if not raw.endswith(b"\n") or len(raw) > GROWTH_MAPPING_MAX_RECORD_BYTES:
                raise ValueError
            data = json.loads(raw.decode("utf-8"))
            return _growth_manifest_from_dict(data)
        except GrowthError:
            raise
        except OSError, UnicodeError, ValueError, TypeError, json.JSONDecodeError:
            raise GrowthMappingStoreCorruptError() from None

    def _read_verified_locked(self) -> _VerifiedGrowthMappingStoreState:
        self._ensure_initialized_locked()
        _growth_safe_file(self.records_path, self.expected_owner_group)
        _growth_safe_file(self.manifest_path, self.expected_owner_group)
        manifest = self._read_manifest_locked()
        try:
            raw = self.records_path.read_bytes()
        except OSError:
            raise GrowthMappingStoreUnavailableError() from None
        if raw and not raw.endswith(b"\n"):
            raise GrowthMappingStoreCorruptError()
        envelopes: list[GrowthMappingStoreRecordEnvelopeV1] = []
        mappings: dict[UUID, GrowthGoalChoiceMappingV1] = {}
        lifecycle: dict[UUID, GrowthMappingLifecycleStateV1] = {}
        mapping_ids: set[UUID] = set()
        event_ids: set[UUID] = set()
        previous_digest: GrowthHashV1 | None = None
        expected_sequence = 1
        for line in raw.splitlines(keepends=True):
            if not line.endswith(b"\n") or len(line) > GROWTH_MAPPING_MAX_RECORD_BYTES:
                raise GrowthMappingStoreCorruptError()
            try:
                data = json.loads(line[:-1].decode("utf-8"))
                envelope = _growth_envelope_from_dict(data)
            except GrowthError:
                raise
            except UnicodeError, ValueError, TypeError, json.JSONDecodeError:
                raise GrowthMappingStoreCorruptError() from None
            if envelope.generation != manifest.generation:
                raise GrowthMappingStoreCorruptError()
            if envelope.sequence != expected_sequence:
                raise GrowthMappingStoreCorruptError()
            if envelope.previous_digest != previous_digest:
                raise GrowthMappingStoreCorruptError()
            if envelope.current_digest != _growth_envelope_digest(envelope):
                raise GrowthMappingStoreCorruptError()
            if envelope.record_type == "accepted_mapping":
                mapping = envelope.record
                assert isinstance(mapping, GrowthGoalChoiceMappingV1)
                if mapping.mapping_id in mapping_ids:
                    raise GrowthMappingStoreCorruptError()
                mapping_ids.add(mapping.mapping_id)
                mappings[mapping.mapping_id] = mapping
                lifecycle[mapping.mapping_id] = GrowthMappingLifecycleStateV1.ACTIVE
            else:
                event = envelope.record
                assert isinstance(event, GrowthMappingLifecycleEventV1)
                if event.event_id in event_ids:
                    raise GrowthMappingStoreCorruptError()
                event_ids.add(event.event_id)
                if event.mapping_id not in mappings:
                    raise GrowthMappingStoreCorruptError()
                old_state = lifecycle[event.mapping_id]
                new_state = cast(GrowthMappingLifecycleStateV1, event.lifecycle_state)
                if not _growth_transition_is_valid(old_state, new_state):
                    raise GrowthMappingStoreCorruptError()
                if new_state is GrowthMappingLifecycleStateV1.SUPERSEDED:
                    if event.replacement_mapping_id not in mappings:
                        raise GrowthMappingStoreCorruptError()
                    if (
                        lifecycle[event.replacement_mapping_id]
                        is not GrowthMappingLifecycleStateV1.ACTIVE
                    ):
                        raise GrowthMappingStoreCorruptError()
                lifecycle[event.mapping_id] = new_state
            envelopes.append(envelope)
            previous_digest = envelope.current_digest
            expected_sequence += 1
        active_count = sum(
            state is GrowthMappingLifecycleStateV1.ACTIVE for state in lifecycle.values()
        )
        if (
            manifest.next_sequence != expected_sequence
            or manifest.record_count != len(envelopes)
            or manifest.head_digest != previous_digest
            or manifest.active_mapping_count != active_count
        ):
            raise GrowthMappingStoreCorruptError()
        active_keys: set[tuple[UUID, str, str, str, int, str]] = set()
        for mapping_id, mapping in mappings.items():
            if lifecycle[mapping_id] is GrowthMappingLifecycleStateV1.ACTIVE:
                key = _growth_mapping_key(mapping)
                if key in active_keys:
                    raise GrowthMappingConflictError()
                active_keys.add(key)
        if len(mappings) > GROWTH_MAPPING_MAX_ACTIVE + len(envelopes):
            raise GrowthMappingStoreCorruptError()
        return _VerifiedGrowthMappingStoreState(
            manifest=manifest,
            envelopes=tuple(envelopes),
            mappings=mappings,
            lifecycle=lifecycle,
        )

    def _read_state_if_present(self) -> _VerifiedGrowthMappingStoreState | None:
        if not self.root.exists():
            return None
        self._validate_root_path()
        records_exists = self.records_path.exists()
        manifest_exists = self.manifest_path.exists()
        if not records_exists and not manifest_exists:
            return None
        if records_exists != manifest_exists:
            raise GrowthMappingStoreCorruptError()
        with self._locked(create=False):
            return self._read_verified_locked()

    def read_verified_snapshot(self) -> tuple[GrowthMappingLifecycleViewV1, ...]:
        state = self._read_state_if_present()
        if state is None:
            return ()
        return tuple(
            GrowthMappingLifecycleViewV1(mapping_id, state.lifecycle[mapping_id], mapping)
            for mapping_id, mapping in sorted(state.mappings.items(), key=lambda item: str(item[0]))
        )

    def read_active(self) -> tuple[GrowthMappingLifecycleViewV1, ...]:
        return tuple(
            item
            for item in self.read_verified_snapshot()
            if item.lifecycle_state is GrowthMappingLifecycleStateV1.ACTIVE
        )

    active_mappings = read_active
    list_active = read_active

    def get(self, mapping_id: UUID) -> GrowthMappingLifecycleViewV1 | None:
        try:
            identifier = _normalize_uuid7(mapping_id)
        except ValueError:
            raise GrowthMappingStoreUnavailableError() from None
        return next(
            (item for item in self.read_verified_snapshot() if item.mapping_id == identifier),
            None,
        )

    def verify(self) -> GrowthMappingStoreManifestV1 | None:
        state = self._read_state_if_present()
        return state.manifest if state is not None else None

    def _now(self) -> datetime:
        try:
            return _normalize_canonical_growth_timestamp(self.clock())
        except TypeError, ValueError, OverflowError, OSError:
            raise GrowthMappingStoreUnavailableError() from None

    def _append_envelopes_locked(
        self,
        state: _VerifiedGrowthMappingStoreState,
        records: tuple[tuple[str, GrowthGoalChoiceMappingV1 | GrowthMappingLifecycleEventV1], ...],
    ) -> None:
        if not records:
            raise GrowthMappingStoreUnavailableError()
        existing_event_ids = {
            envelope.record.event_id
            for envelope in state.envelopes
            if envelope.record_type == "lifecycle_event"
            and isinstance(envelope.record, GrowthMappingLifecycleEventV1)
        }
        pending_event_ids: set[UUID] = set()
        pending_mapping_ids: set[UUID] = set()
        for record_type, record in records:
            if record_type == "accepted_mapping":
                if not isinstance(record, GrowthGoalChoiceMappingV1):
                    raise GrowthMappingStoreUnavailableError()
                if record.mapping_id in state.mappings or record.mapping_id in pending_mapping_ids:
                    raise GrowthMappingStoreCorruptError()
                pending_mapping_ids.add(record.mapping_id)
            elif record_type == "lifecycle_event":
                if not isinstance(record, GrowthMappingLifecycleEventV1):
                    raise GrowthMappingStoreUnavailableError()
                if record.event_id in existing_event_ids or record.event_id in pending_event_ids:
                    raise GrowthMappingStoreCorruptError()
                pending_event_ids.add(record.event_id)
            else:
                raise GrowthMappingStoreUnavailableError()
        previous = state.manifest.head_digest
        sequence = state.manifest.next_sequence
        envelopes: list[GrowthMappingStoreRecordEnvelopeV1] = []
        for record_type, record in records:
            if record_type not in _GROWTH_STORE_RECORD_TYPES:
                raise GrowthMappingStoreUnavailableError()
            candidate = GrowthMappingStoreRecordEnvelopeV1(
                generation=state.manifest.generation,
                sequence=sequence,
                record_type=record_type,
                record=record,
                previous_digest=previous,
                current_digest="sha256:" + "0" * 64,
            )
            digest = _growth_envelope_digest(candidate)
            envelope = GrowthMappingStoreRecordEnvelopeV1(
                generation=candidate.generation,
                sequence=candidate.sequence,
                record_type=candidate.record_type,
                record=candidate.record,
                previous_digest=candidate.previous_digest,
                current_digest=digest,
            )
            line = (canonical_growth_json(envelope.as_dict()) + "\n").encode("utf-8")
            if len(line) > GROWTH_MAPPING_MAX_RECORD_BYTES:
                raise GrowthResultTooLargeError()
            envelopes.append(envelope)
            previous = digest
            sequence += 1
        simulated_mappings = dict(state.mappings)
        simulated_lifecycle = dict(state.lifecycle)
        self._simulate_records(simulated_mappings, simulated_lifecycle, records)
        active_count = sum(
            lifecycle is GrowthMappingLifecycleStateV1.ACTIVE
            for lifecycle in simulated_lifecycle.values()
        )
        if active_count > GROWTH_MAPPING_MAX_ACTIVE:
            raise GrowthResultTooLargeError()
        try:
            with self.records_path.open("ab") as stream:
                for envelope in envelopes:
                    stream.write((canonical_growth_json(envelope.as_dict()) + "\n").encode("utf-8"))
                stream.flush()
                os.fsync(stream.fileno())
        except OSError:
            raise GrowthMappingStoreUnavailableError() from None
        manifest = GrowthMappingStoreManifestV1(
            format_version=GROWTH_MAPPING_STORE_FORMAT_VERSION,
            contract_version=GROWTH_CONTRACT_VERSION,
            mapping_policy_id=GROWTH_MAPPING_POLICY_ID,
            mapping_policy_fingerprint=GROWTH_MAPPING_POLICY_FINGERPRINT,
            generation=state.manifest.generation,
            next_sequence=sequence,
            record_count=state.manifest.record_count + len(envelopes),
            head_digest=previous,
            active_mapping_count=active_count,
            manifest_fingerprint=growth_hash_json(
                {
                    "active_mapping_count": active_count,
                    "contract_version": GROWTH_CONTRACT_VERSION,
                    "format_version": GROWTH_MAPPING_STORE_FORMAT_VERSION,
                    "generation": str(state.manifest.generation),
                    "head_digest": previous,
                    "mapping_policy_fingerprint": GROWTH_MAPPING_POLICY_FINGERPRINT,
                    "mapping_policy_id": GROWTH_MAPPING_POLICY_ID,
                    "next_sequence": sequence,
                    "record_count": state.manifest.record_count + len(envelopes),
                }
            ),
        )
        self._atomic_manifest_write(manifest)
        reread = self._read_verified_locked()
        if reread.manifest != manifest:
            raise GrowthMappingStoreCorruptError()

    @staticmethod
    def _simulate_records(
        mappings: dict[UUID, GrowthGoalChoiceMappingV1],
        lifecycle: dict[UUID, GrowthMappingLifecycleStateV1],
        records: tuple[tuple[str, GrowthGoalChoiceMappingV1 | GrowthMappingLifecycleEventV1], ...],
    ) -> None:
        for record_type, record in records:
            if record_type == "accepted_mapping":
                if type(record) is not GrowthGoalChoiceMappingV1 or record.mapping_id in mappings:
                    raise GrowthMappingStoreCorruptError()
                mappings[record.mapping_id] = record
                lifecycle[record.mapping_id] = GrowthMappingLifecycleStateV1.ACTIVE
                continue
            if type(record) is not GrowthMappingLifecycleEventV1:
                raise GrowthMappingStoreCorruptError()
            event = record
            if event.mapping_id not in mappings:
                raise GrowthMappingStoreCorruptError()
            old_state = lifecycle[event.mapping_id]
            new_state = cast(GrowthMappingLifecycleStateV1, event.lifecycle_state)
            if not _growth_transition_is_valid(old_state, new_state):
                raise GrowthMappingStoreCorruptError()
            if new_state is GrowthMappingLifecycleStateV1.SUPERSEDED:
                if event.replacement_mapping_id not in mappings:
                    raise GrowthMappingStoreCorruptError()
                if (
                    lifecycle[event.replacement_mapping_id]
                    is not GrowthMappingLifecycleStateV1.ACTIVE
                ):
                    raise GrowthMappingStoreCorruptError()
            lifecycle[event.mapping_id] = new_state

    def append_mapping(self, mapping: GrowthGoalChoiceMappingV1) -> GrowthGoalChoiceMappingV1:
        """Append one already constructed mapping after exact integrity checks."""

        if type(mapping) is not GrowthGoalChoiceMappingV1:
            raise GrowthGoalMappingInvalidError()
        try:
            validate_growth_mapping_policy()
        except GrowthError:
            raise
        with self._locked(create=True):
            state = self._read_verified_locked()
            existing = state.mappings.get(mapping.mapping_id)
            if existing is not None:
                if existing == mapping:
                    return existing
                raise GrowthConcurrencyConflictError()
            by_operation = tuple(
                item
                for item in state.mappings.values()
                if item.acceptance_operation_id_fingerprint
                == mapping.acceptance_operation_id_fingerprint
            )
            if by_operation:
                if (
                    len(by_operation) == 1
                    and by_operation[0].mapping_fingerprint == mapping.mapping_fingerprint
                    and by_operation[0].supersedes_mapping_id == mapping.supersedes_mapping_id
                ):
                    return by_operation[0]
                raise GrowthIdempotencyConflictError()
            active = tuple(
                item
                for mapping_id, item in state.mappings.items()
                if state.lifecycle[mapping_id] is GrowthMappingLifecycleStateV1.ACTIVE
            )
            if any(_growth_mapping_key(item) == _growth_mapping_key(mapping) for item in active):
                raise GrowthMappingConflictError()
            self._append_envelopes_locked(state, (("accepted_mapping", mapping),))
            return mapping

    def accept_mapping(
        self,
        *,
        goal: GrowthGoalIdentityV1,
        behavioral_target: GrowthBehavioralTargetIdentityV1,
        relation: GrowthGoalRelationV1 | str,
        operation_id: UUID,
        reviewed_at: datetime,
        supersedes_mapping_id: UUID | None = None,
    ) -> GrowthGoalChoiceMappingV1:
        """Atomically accept one fresh exact mapping or recover an idempotent retry."""

        try:
            validate_growth_mapping_policy()
            if (
                type(goal) is not GrowthGoalIdentityV1
                or type(behavioral_target) is not GrowthBehavioralTargetIdentityV1
            ):
                raise ValueError
            normalized_relation = _normalize_growth_relation(relation)
            operation_fingerprint = growth_operation_id_fingerprint(operation_id)
            reviewed = _normalize_canonical_growth_timestamp(reviewed_at)
            supersedes = (
                _normalize_uuid7(supersedes_mapping_id)
                if supersedes_mapping_id is not None
                else None
            )
        except GrowthError:
            raise
        except TypeError, ValueError, OverflowError:
            raise GrowthGoalMappingInvalidError() from None
        expected_fingerprint = compute_growth_goal_choice_mapping_fingerprint(
            goal, behavioral_target, normalized_relation
        )
        with self._locked(create=True):
            state = self._read_verified_locked()
            existing = tuple(
                item
                for item in state.mappings.values()
                if item.acceptance_operation_id_fingerprint == operation_fingerprint
            )
            if existing:
                if (
                    len(existing) == 1
                    and existing[0].mapping_fingerprint == expected_fingerprint
                    and existing[0].goal == goal
                    and existing[0].behavioral_target == behavioral_target
                    and existing[0].relation is normalized_relation
                    and existing[0].supersedes_mapping_id == supersedes
                ):
                    return existing[0]
                raise GrowthIdempotencyConflictError()
            active = {
                mapping_id: mapping
                for mapping_id, mapping in state.mappings.items()
                if state.lifecycle[mapping_id] is GrowthMappingLifecycleStateV1.ACTIVE
            }
            if supersedes is not None and (
                supersedes not in active
                or _growth_mapping_key(active[supersedes])
                != (
                    goal.source_note_uuid,
                    goal.source_fingerprint,
                    goal.claim_fingerprint,
                    behavioral_target.cohort.cohort_fingerprint,
                    behavioral_target.option.option_index,
                    behavioral_target.option.option_fingerprint,
                )
            ):
                raise GrowthConcurrencyConflictError()
            mapping_key = (
                goal.source_note_uuid,
                goal.source_fingerprint,
                goal.claim_fingerprint,
                behavioral_target.cohort.cohort_fingerprint,
                behavioral_target.option.option_index,
                behavioral_target.option.option_fingerprint,
            )
            if any(
                _growth_mapping_key(item) == mapping_key
                and (supersedes is None or mapping_id != supersedes)
                for mapping_id, item in active.items()
            ):
                raise GrowthMappingConflictError()
            active_count_after = len(active) - int(supersedes is not None) + 1
            if active_count_after > GROWTH_MAPPING_MAX_ACTIVE:
                raise GrowthResultTooLargeError()
            created_at = self._now()
            try:
                mapping = GrowthGoalChoiceMappingV1(
                    contract_version=GROWTH_CONTRACT_VERSION,
                    mapping_policy_id=GROWTH_MAPPING_POLICY_ID,
                    mapping_policy_fingerprint=GROWTH_MAPPING_POLICY_FINGERPRINT,
                    mapping_id=uuid7(),
                    acceptance_operation_id_fingerprint=operation_fingerprint,
                    created_at=created_at,
                    reviewed_at=reviewed,
                    mapping_basis=GROWTH_MAPPING_BASIS,
                    goal=goal,
                    behavioral_target=behavioral_target,
                    relation=normalized_relation,
                    mapping_fingerprint=expected_fingerprint,
                    supersedes_mapping_id=supersedes,
                )
            except TypeError, ValueError:
                raise GrowthGoalMappingInvalidError() from None
            records: list[tuple[str, GrowthGoalChoiceMappingV1 | GrowthMappingLifecycleEventV1]] = [
                ("accepted_mapping", mapping)
            ]
            if supersedes is not None:
                records.append(
                    (
                        "lifecycle_event",
                        GrowthMappingLifecycleEventV1(
                            event_id=uuid7(),
                            mapping_id=supersedes,
                            lifecycle_state=GrowthMappingLifecycleStateV1.SUPERSEDED,
                            occurred_at=created_at,
                            operation_id_fingerprint=operation_fingerprint,
                            reason_code="explicit_correction",
                            replacement_mapping_id=mapping.mapping_id,
                        ),
                    )
                )
            self._append_envelopes_locked(state, tuple(records))
            return mapping

    def append_lifecycle_event(
        self,
        event: GrowthMappingLifecycleEventV1,
    ) -> GrowthMappingLifecycleEventV1:
        """Append one validated lifecycle transition under the store lock."""

        if type(event) is not GrowthMappingLifecycleEventV1:
            raise GrowthMappingStoreUnavailableError()
        with self._locked(create=True):
            state = self._read_verified_locked()
            if event.mapping_id not in state.mappings:
                raise GrowthConcurrencyConflictError()
            self._append_envelopes_locked(state, (("lifecycle_event", event),))
            return event

    def invalidate_mapping(
        self,
        mapping_id: UUID,
        *,
        operation_id: UUID,
        reason_code: str = "owner_invalidation",
    ) -> GrowthMappingLifecycleEventV1:
        return self._append_lifecycle_for_state(
            mapping_id,
            operation_id=operation_id,
            lifecycle_state=GrowthMappingLifecycleStateV1.INVALIDATED,
            reason_code=reason_code,
        )

    def delete_mapping(
        self,
        mapping_id: UUID,
        *,
        operation_id: UUID,
        reason_code: str = "owner_deletion",
    ) -> GrowthMappingLifecycleEventV1:
        return self._append_lifecycle_for_state(
            mapping_id,
            operation_id=operation_id,
            lifecycle_state=GrowthMappingLifecycleStateV1.DELETED,
            reason_code=reason_code,
        )

    invalidate = invalidate_mapping
    delete = delete_mapping

    def _append_lifecycle_for_state(
        self,
        mapping_id: UUID,
        *,
        operation_id: UUID,
        lifecycle_state: GrowthMappingLifecycleStateV1,
        reason_code: str,
    ) -> GrowthMappingLifecycleEventV1:
        try:
            identifier = _normalize_uuid7(mapping_id)
            operation_fingerprint = growth_operation_id_fingerprint(operation_id)
            occurred_at = self._now()
        except TypeError, ValueError, OverflowError, GrowthError:
            raise GrowthMappingStoreUnavailableError() from None
        with self._locked(create=True):
            state = self._read_verified_locked()
            if identifier not in state.mappings:
                raise GrowthConcurrencyConflictError()
            event = GrowthMappingLifecycleEventV1(
                event_id=uuid7(),
                mapping_id=identifier,
                lifecycle_state=lifecycle_state,
                occurred_at=occurred_at,
                operation_id_fingerprint=operation_fingerprint,
                reason_code=reason_code,
            )
            self._append_envelopes_locked(state, (("lifecycle_event", event),))
            return event


GrowthGoalChoiceMappingStore = GrowthMappingStore
GrowthMappingOperationalStore = GrowthMappingStore
GrowthMappingStoreV1 = GrowthMappingStore


@dataclass(frozen=True, slots=True)
class _GrowthSnapshotReader:
    """Read-only adapter that keeps one validated snapshot stable across builders."""

    snapshot: VaultSnapshot

    def scan(self) -> VaultSnapshot:
        return self.snapshot


@dataclass(frozen=True, slots=True)
class _GrowthCurrentBuild:
    snapshot: VaultSnapshot
    report: ScanReport
    self_model: SelfModelResult
    all_goals: tuple[GrowthGoalIdentityV1, ...]
    observations: BehavioralObservationBuildResultV1
    behavioral: BehavioralSelfModelResultV1
    generated_at: datetime


def _growth_now(clock: GrowthClock) -> datetime:
    try:
        return _normalize_canonical_growth_timestamp(clock())
    except TypeError, ValueError, OverflowError, OSError:
        raise GrowthInvalidRequestError() from None


def _build_growth_current(
    reader: VaultReader,
    *,
    policy: SelfModelPolicy,
    clock: GrowthClock,
) -> _GrowthCurrentBuild:
    try:
        generated_at = _growth_now(clock)
    except GrowthInvalidRequestError:
        raise
    try:
        snapshot = reader.scan()
        if type(snapshot) is not VaultSnapshot:
            raise ValueError
        report = build_report(snapshot)
    except GrowthError:
        raise
    except Exception:
        raise GrowthGoalSourceUnavailableError() from None
    stable_reader = _GrowthSnapshotReader(snapshot)
    try:
        self_model_policy_fingerprint = validate_self_model_policy(policy)
        self_model = BuildSelfModel(
            stable_reader,
            policy=policy,
            clock=lambda: generated_at,
        ).execute(
            SelfModelRequest(
                max_claims=MAX_SELF_MODEL_LIMIT,
                max_evidence_refs_per_claim=MAX_SELF_MODEL_LIMIT,
            )
        )
    except SelfModelInvalidRequestError, SelfModelInvalidClockError:
        raise GrowthInvalidRequestError() from None
    except SelfModelPolicyUnavailableError:
        raise GrowthPolicyMismatchError() from None
    except SelfModelResultTooLargeError:
        raise GrowthResultTooLargeError() from None
    except (
        SelfModelVaultUnavailableError,
        SelfModelEvidenceInvalidError,
        SelfModelResultInvalidError,
        SelfModelError,
    ):
        raise GrowthGoalSourceUnavailableError() from None
    except TypeError, ValueError, UnicodeError, OverflowError:
        raise GrowthGoalSourceChangedError() from None
    except Exception:
        raise GrowthGoalSourceUnavailableError() from None
    try:
        all_goals = tuple(
            sorted(
                (
                    build_growth_goal_identity(
                        claim,
                        self_model,
                        policy=policy,
                        expected_self_model_policy_fingerprint=self_model_policy_fingerprint,
                    )
                    for claim in self_model.claims
                    if claim.dimension.value == SelfKind.GOAL.value
                ),
                key=lambda goal: str(goal.source_note_uuid),
            )
        )
    except GrowthError:
        raise
    except TypeError, ValueError, UnicodeError:
        raise GrowthGoalSourceChangedError() from None
    try:
        observations = BuildBehavioralObservations(
            stable_reader,
            clock=lambda: generated_at,
        ).execute()
        validate_behavioral_self_model_policy(DEFAULT_BEHAVIORAL_SELF_MODEL_POLICY)
        behavioral = BuildBehavioralSelfModel(
            stable_reader,
            policy=DEFAULT_BEHAVIORAL_SELF_MODEL_POLICY,
            clock=lambda: generated_at,
        ).execute()
        validate_behavioral_self_model_result(behavioral)
    except GrowthError:
        raise
    except BehavioralSelfModelError as error:
        if error.code.endswith("RESULT_TOO_LARGE"):
            raise GrowthResultTooLargeError() from None
        if error.code.endswith("POLICY_MISMATCH"):
            raise GrowthPolicyMismatchError() from None
        raise GrowthBehavioralSourceUnavailableError() from None
    except TypeError, ValueError, UnicodeError, OverflowError:
        raise GrowthBehavioralSourceUnavailableError() from None
    except Exception:
        raise GrowthBehavioralSourceUnavailableError() from None
    return _GrowthCurrentBuild(
        snapshot=snapshot,
        report=report,
        self_model=self_model,
        all_goals=all_goals,
        observations=observations,
        behavioral=behavioral,
        generated_at=generated_at,
    )


def _growth_goal_for_uuid(
    current: _GrowthCurrentBuild,
    source_note_uuid: UUID,
) -> GrowthGoalIdentityV1:
    goal = next(
        (item for item in current.all_goals if item.source_note_uuid == source_note_uuid),
        None,
    )
    if goal is None:
        raise GrowthGoalMissingError()
    return goal


def _growth_pattern_and_bucket(
    current: _GrowthCurrentBuild,
    selector: GrowthGoalChoiceMappingSelectorV1,
) -> tuple[BehavioralPatternV1, BehavioralCohortBucketV1]:
    bucket = next(
        (
            item
            for item in current.observations.cohorts
            if item.cohort.cohort_fingerprint == selector.behavioral_cohort_fingerprint
        ),
        None,
    )
    pattern = next(
        (
            item
            for item in current.behavioral.patterns
            if item.cohort is not None
            and item.cohort.cohort_fingerprint == selector.behavioral_cohort_fingerprint
        ),
        None,
    )
    if bucket is None or pattern is None or pattern.cohort != bucket.cohort:
        raise GrowthGoalMappingStaleError()
    return pattern, bucket


def build_growth_behavioral_target_identity(
    pattern: BehavioralPatternV1,
    bucket: BehavioralCohortBucketV1,
    *,
    option: BehavioralOptionIdentityV1 | None = None,
) -> GrowthBehavioralTargetIdentityV1:
    """Resolve one exact eligible current Stage 10 cohort option."""

    if type(pattern) is not BehavioralPatternV1 or type(bucket) is not BehavioralCohortBucketV1:
        raise GrowthGoalMappingInvalidError()
    if pattern.cohort is None or pattern.cohort != bucket.cohort:
        raise GrowthGoalMappingStaleError()
    if pattern.pattern_type is BehavioralPatternTypeV1.INSUFFICIENT_EVIDENCE:
        raise GrowthBehavioralEvidenceInsufficientError()
    if pattern.pattern_type not in _GROWTH_BINARY_PATTERN_TYPES or pattern.state not in {
        BehavioralPatternStateV1.CURRENT,
        BehavioralPatternStateV1.STABLE,
    }:
        raise GrowthBehavioralStateNotComparableError()
    selected = option if option is not None else pattern.selected_option
    if selected is None or type(selected) is not BehavioralOptionIdentityV1:
        raise GrowthBehavioralStateNotComparableError()
    namespace = bucket.observations[0].option_namespace
    if selected.option_index >= namespace.option_count:
        raise GrowthGoalMappingStaleError()
    live_option = BehavioralOptionIdentityV1(
        selected.option_index,
        namespace.ordered_option_fingerprints[selected.option_index],
    )
    if live_option != selected or pattern.selected_option != selected:
        raise GrowthGoalMappingStaleError()
    return GrowthBehavioralTargetIdentityV1(
        behavioral_contract_version=BEHAVIORAL_CONTRACT_VERSION,
        behavioral_derivation_version=BEHAVIORAL_DERIVATION_VERSION,
        observation_version=OBSERVATION_VERSION,
        policy_id=BEHAVIORAL_POLICY_ID,
        policy_fingerprint=BEHAVIORAL_POLICY_FINGERPRINT,
        cohort=bucket.cohort,
        option=selected,
        comparison_basis="current-exact-option-v1",
    )


def _growth_resolve_selector(
    current: _GrowthCurrentBuild,
    selector: GrowthGoalChoiceMappingSelectorV1,
) -> tuple[
    GrowthGoalIdentityV1,
    GrowthBehavioralTargetIdentityV1,
    BehavioralPatternV1,
    BehavioralCohortBucketV1,
]:
    goal = _growth_goal_for_uuid(current, selector.source_note_uuid)
    if goal.domain is None:
        raise GrowthUnsupportedSemanticComparisonError()
    pattern, bucket = _growth_pattern_and_bucket(current, selector)
    try:
        option = BehavioralOptionIdentityV1(
            selector.behavioral_option_index,
            selector.behavioral_option_fingerprint,
        )
        target = build_growth_behavioral_target_identity(pattern, bucket, option=option)
    except GrowthError:
        raise
    except TypeError, ValueError:
        raise GrowthGoalMappingStaleError() from None
    if goal.domain != target.cohort.domain:
        raise GrowthUnsupportedSemanticComparisonError()
    return goal, target, pattern, bucket


def _growth_goal_text(current: _GrowthCurrentBuild, goal: GrowthGoalIdentityV1) -> str:
    claim = next(
        (
            item
            for item in current.self_model.claims
            if item.dimension.value == SelfKind.GOAL.value
            and item.supporting_evidence
            and item.supporting_evidence[0].note_id == goal.source_note_uuid
        ),
        None,
    )
    if claim is None or type(claim.claim) is not str or not claim.claim:
        raise GrowthGoalSourceChangedError()
    return claim.claim


def _growth_review_note(
    current: _GrowthCurrentBuild,
    bucket: BehavioralCohortBucketV1,
) -> object:
    source_ids = {item.source_journal_uuid for item in bucket.observations}
    for note in current.report.notes:
        if note.note_id in source_ids and note.decision_journal is not None:
            return note
    raise GrowthBehavioralSourceUnavailableError()


def _growth_review_caveats(goal: GrowthGoalIdentityV1) -> tuple[GrowthCaveatCodeV1, ...]:
    values = {
        GrowthCaveatCodeV1.CURRENT_GOAL_REVALIDATED,
        GrowthCaveatCodeV1.GOAL_EVIDENCE_TIME_UNKNOWN,
        GrowthCaveatCodeV1.BEHAVIORAL_RELATION_EXACT_ONLY,
        GrowthCaveatCodeV1.CURRENT_BEHAVIOR_REVALIDATED,
        GrowthCaveatCodeV1.GOAL_MAPPING_IS_EXPLICIT,
        GrowthCaveatCodeV1.OUTCOME_PRESENCE_ONLY,
        GrowthCaveatCodeV1.PREFERENCE_BRANCH_INDEPENDENT,
        GrowthCaveatCodeV1.NO_GROWTH_OPTIMAL_CLAIM,
        GrowthCaveatCodeV1.ADVISOR_NOT_USED_V1,
    }
    if goal.evidence_at != "unknown":
        values.discard(GrowthCaveatCodeV1.GOAL_EVIDENCE_TIME_UNKNOWN)
    return tuple(item for item in GrowthCaveatCodeV1 if item in values)


def _growth_result_caveats(
    goal: GrowthGoalIdentityV1,
    *,
    state: GrowthRelationStateV1,
    mapping: GrowthGoalChoiceMappingV1 | None,
) -> tuple[GrowthCaveatCodeV1, ...]:
    values = set(_growth_review_caveats(goal))
    if state is GrowthRelationStateV1.MIXED_BEHAVIOR:
        values.add(GrowthCaveatCodeV1.MIXED_NO_WINNER)
    if state is GrowthRelationStateV1.CHANGED_BEHAVIOR:
        values.add(GrowthCaveatCodeV1.CHANGED_NOT_A_TRAIT)
    if state in {
        GrowthRelationStateV1.BEHAVIORAL_EVIDENCE_INSUFFICIENT,
        GrowthRelationStateV1.NOT_COMPARABLE,
    }:
        values.add(GrowthCaveatCodeV1.INSUFFICIENT_NOT_CONFLICT)
    if state is GrowthRelationStateV1.GOAL_MAPPING_MISSING:
        values.add(GrowthCaveatCodeV1.GOAL_MAPPING_MISSING_FOR_OBSERVED_OPTION)
    if mapping is not None:
        values.add(GrowthCaveatCodeV1.MAPPING_REVIEW_TIME_IS_NOT_EVIDENCE_TIME)
    values.add(GrowthCaveatCodeV1.TEMPORAL_ALIGNMENT_NOT_PROVEN)
    return tuple(item for item in GrowthCaveatCodeV1 if item in values)


def _growth_temporal(
    goal: GrowthGoalIdentityV1,
    generated_at: datetime,
    pattern: BehavioralPatternV1,
    mapping: GrowthGoalChoiceMappingV1 | None,
) -> GrowthTemporalContextV1:
    current_window_start: datetime | None = None
    current_window_end: datetime | None = None
    if pattern.state in {BehavioralPatternStateV1.CURRENT, BehavioralPatternStateV1.STABLE}:
        current_window_start = generated_at - timedelta(days=TEMPORAL_WINDOW_DAYS)
        current_window_end = generated_at
    return GrowthTemporalContextV1(
        goal_evidence_at=goal.evidence_at,
        goal_evidence_at_precision=goal.evidence_at_precision,
        behavioral_generated_at=generated_at,
        behavioral_current_window_start=current_window_start,
        behavioral_current_window_end=current_window_end,
        mapping_reviewed_at=mapping.reviewed_at if mapping is not None else None,
        mapping_created_at=mapping.created_at if mapping is not None else None,
        advisor_requested_at=None,
    )


def _growth_mapping_for(
    active: tuple[GrowthMappingLifecycleViewV1, ...],
    goal: GrowthGoalIdentityV1,
    cohort: BehavioralCohortIdentityV1,
    option: BehavioralOptionIdentityV1 | None,
) -> tuple[GrowthGoalChoiceMappingV1 | None, bool]:
    drifted = tuple(
        item.mapping
        for item in active
        if item.mapping.goal.source_note_uuid == goal.source_note_uuid
        and item.mapping.behavioral_target.cohort.domain == cohort.domain
        and item.mapping.behavioral_target.cohort.situation_fingerprint
        == cohort.situation_fingerprint
        and item.mapping.behavioral_target.cohort.information_fingerprint
        == cohort.information_fingerprint
        and item.mapping.behavioral_target.cohort.criteria_fingerprint
        == cohort.criteria_fingerprint
        and item.mapping.behavioral_target.cohort.cohort_fingerprint != cohort.cohort_fingerprint
    )
    if drifted:
        return None, True
    related = tuple(
        item.mapping
        for item in active
        if item.mapping.goal.source_note_uuid == goal.source_note_uuid
        and item.mapping.behavioral_target.cohort.cohort_fingerprint == cohort.cohort_fingerprint
    )
    if any(item.goal != goal for item in related):
        return None, True
    if option is None:
        return None, False
    exact = tuple(item for item in related if item.behavioral_target.option == option)
    if len(exact) > 1:
        raise GrowthMappingStoreCorruptError()
    return (exact[0] if exact else None), False


def _growth_relation_result(
    goal: GrowthGoalIdentityV1,
    pattern: BehavioralPatternV1,
    *,
    active: tuple[GrowthMappingLifecycleViewV1, ...],
    generated_at: datetime,
) -> GrowthGoalRelationResultV1:
    if pattern.cohort is None:
        raise GrowthGoalMappingInvalidError()
    if goal.domain is None or goal.domain != pattern.cohort.domain:
        return GrowthGoalRelationResultV1(
            goal=goal,
            state=GrowthRelationStateV1.NOT_COMPARABLE,
            cohort_fingerprint=pattern.cohort.cohort_fingerprint,
            behavioral_pattern=_growth_pattern_ref(pattern, current_option=None),
            behavioral_option=None,
            mapping=None,
            reason_codes=(GrowthErrorCode.UNSUPPORTED_SEMANTIC_COMPARISON,),
            caveats=_growth_result_caveats(
                goal,
                state=GrowthRelationStateV1.NOT_COMPARABLE,
                mapping=None,
            ),
            temporal=_growth_temporal(goal, generated_at, pattern, None),
            advisor=None,
        )
    state: GrowthRelationStateV1
    reason: tuple[GrowthErrorCode, ...] = ()
    option: BehavioralOptionIdentityV1 | None = None
    if pattern.pattern_type in _GROWTH_BINARY_PATTERN_TYPES and pattern.state in {
        BehavioralPatternStateV1.CURRENT,
        BehavioralPatternStateV1.STABLE,
    }:
        option = pattern.selected_option
    mapping, stale = _growth_mapping_for(active, goal, pattern.cohort, option)
    if stale:
        state = GrowthRelationStateV1.NOT_COMPARABLE
        reason = (GrowthErrorCode.GOAL_MAPPING_STALE,)
    elif pattern.pattern_type is BehavioralPatternTypeV1.INSUFFICIENT_EVIDENCE:
        state = GrowthRelationStateV1.BEHAVIORAL_EVIDENCE_INSUFFICIENT
        reason = (GrowthErrorCode.BEHAVIORAL_EVIDENCE_INSUFFICIENT,)
    elif pattern.pattern_type is BehavioralPatternTypeV1.MIXED_EXACT_CHOICES:
        state = GrowthRelationStateV1.MIXED_BEHAVIOR
        reason = (GrowthErrorCode.BEHAVIORAL_STATE_NOT_COMPARABLE,)
    elif pattern.pattern_type is BehavioralPatternTypeV1.CHANGED_OVER_TIME:
        state = GrowthRelationStateV1.CHANGED_BEHAVIOR
        reason = (GrowthErrorCode.BEHAVIORAL_STATE_NOT_COMPARABLE,)
    elif (
        pattern.pattern_type is BehavioralPatternTypeV1.NOT_COMPARABLE
        or pattern.state is BehavioralPatternStateV1.HISTORICAL
    ) or option is None:
        state = GrowthRelationStateV1.NOT_COMPARABLE
        reason = (GrowthErrorCode.BEHAVIORAL_STATE_NOT_COMPARABLE,)
    elif mapping is None:
        state = GrowthRelationStateV1.GOAL_MAPPING_MISSING
        reason = (GrowthErrorCode.GOAL_MAPPING_MISSING,)
    else:
        state = GrowthRelationStateV1(cast(GrowthGoalRelationV1, mapping.relation).value)
    pattern_ref = _growth_pattern_ref(pattern, current_option=option)
    mapping_ref = (
        GrowthMappingRefV1(
            mapping_id=mapping.mapping_id,
            mapping_policy_id=mapping.mapping_policy_id,
            mapping_fingerprint=mapping.mapping_fingerprint,
            relation=mapping.relation,
        )
        if mapping is not None
        and state
        in {
            GrowthRelationStateV1.SUPPORTS_GOAL,
            GrowthRelationStateV1.CONFLICTS_WITH_GOAL,
            GrowthRelationStateV1.NEUTRAL_OR_UNKNOWN,
        }
        else None
    )
    return GrowthGoalRelationResultV1(
        goal=goal,
        state=state,
        cohort_fingerprint=pattern.cohort.cohort_fingerprint,
        behavioral_pattern=pattern_ref,
        behavioral_option=option,
        mapping=mapping_ref,
        reason_codes=reason,
        caveats=_growth_result_caveats(goal, state=state, mapping=mapping),
        temporal=_growth_temporal(goal, generated_at, pattern, mapping),
        advisor=None,
    )


def _growth_empty_relation_result(
    goal: GrowthGoalIdentityV1,
    *,
    generated_at: datetime,
) -> GrowthGoalRelationResultV1:
    """Represent an empty behavioral source without inventing a subject."""

    return GrowthGoalRelationResultV1(
        goal=goal,
        state=GrowthRelationStateV1.BEHAVIORAL_EVIDENCE_INSUFFICIENT,
        cohort_fingerprint=None,
        behavioral_pattern=None,
        behavioral_option=None,
        mapping=None,
        reason_codes=(GrowthErrorCode.BEHAVIORAL_EVIDENCE_INSUFFICIENT,),
        caveats=_growth_result_caveats(
            goal,
            state=GrowthRelationStateV1.BEHAVIORAL_EVIDENCE_INSUFFICIENT,
            mapping=None,
        ),
        temporal=GrowthTemporalContextV1(
            goal_evidence_at=goal.evidence_at,
            goal_evidence_at_precision=goal.evidence_at_precision,
            behavioral_generated_at=generated_at,
            behavioral_current_window_start=None,
            behavioral_current_window_end=None,
            mapping_reviewed_at=None,
            mapping_created_at=None,
            advisor_requested_at=None,
        ),
        advisor=None,
    )


@dataclass(frozen=True, slots=True)
class BuildGrowthEngine:
    """Rebuild the provider-free Stage 11B read model and review boundary."""

    reader: VaultReader
    store: GrowthMappingStore
    policy: SelfModelPolicy = DEFAULT_SELF_MODEL_POLICY
    clock: GrowthClock = lambda: datetime.now(UTC)

    def execute(
        self,
        request: GrowthEngineRequestV1 | _DefaultGrowthRequest = _DEFAULT_GROWTH_REQUEST,
    ) -> GrowthEngineResultV1:
        """Build every requested relation from current exact source identities."""

        if isinstance(request, _DefaultGrowthRequest):
            request = GrowthEngineRequestV1()
        request = validate_growth_engine_request(request)
        try:
            validate_growth_policy()
            validate_growth_mapping_policy()
            validate_self_model_policy(self.policy)
            validate_behavioral_self_model_policy(DEFAULT_BEHAVIORAL_SELF_MODEL_POLICY)
        except GrowthError, SelfModelError, BehavioralSelfModelError, TypeError, ValueError:
            raise GrowthPolicyMismatchError() from None
        current = _build_growth_current(self.reader, policy=self.policy, clock=self.clock)
        try:
            active = self.store.read_active()
        except GrowthError:
            raise
        except Exception:
            raise GrowthMappingStoreUnavailableError() from None
        goals: tuple[GrowthGoalIdentityV1, ...]
        if request.selection.mode is GrowthGoalSelectionModeV1.SELECTED_GOAL:
            assert request.selection.source_note_uuid is not None
            selected = _growth_goal_for_uuid(current, request.selection.source_note_uuid)
            goals = (selected,)
        else:
            goals = current.all_goals
        results: list[GrowthGoalRelationResultV1] = []
        for goal in goals:
            patterns = tuple(
                pattern for pattern in current.behavioral.patterns if pattern.cohort is not None
            )
            if not patterns:
                results.append(
                    _growth_empty_relation_result(goal, generated_at=current.generated_at)
                )
                continue
            for pattern in patterns:
                results.append(
                    _growth_relation_result(
                        goal,
                        pattern,
                        active=active,
                        generated_at=current.generated_at,
                    )
                )
                if len(results) > request.max_results:
                    raise GrowthResultTooLargeError()
        ordered_results = tuple(sorted(results, key=_growth_relation_result_sort_key))
        caveats = _growth_engine_caveats(goals, ordered_results)
        result = GrowthEngineResultV1(
            contract_version=GROWTH_CONTRACT_VERSION,
            derivation_version=GROWTH_DERIVATION_VERSION,
            policy_id=GROWTH_POLICY_ID,
            policy_fingerprint=GROWTH_POLICY_FINGERPRINT,
            generated_at=current.generated_at,
            selection_mode=request.selection.mode,
            selected_goal_source_uuid=(
                request.selection.source_note_uuid
                if request.selection.mode is GrowthGoalSelectionModeV1.SELECTED_GOAL
                else None
            ),
            eligible_goal_count=len(current.all_goals),
            goal_results=ordered_results,
            reason_codes=(),
            caveats=caveats,
        )
        try:
            validate_growth_engine_result(result)
            if len(result.to_json().encode("utf-8")) > request.max_result_bytes:
                raise GrowthResultTooLargeError()
        except GrowthError:
            raise
        except TypeError, ValueError, UnicodeError, OverflowError:
            raise GrowthResultTooLargeError() from None
        return result

    build = execute
    compose = execute

    def review(
        self,
        request: GrowthMappingReviewRequestV1 | GrowthGoalChoiceMappingSelectorV1,
    ) -> GrowthGoalChoiceMappingReviewProjectionV1:
        """Return a bounded transient projection for explicit owner review."""

        if type(request) is GrowthGoalChoiceMappingSelectorV1:
            selector = request
            proposed_relation = None
        elif type(request) is GrowthMappingReviewRequestV1:
            selector = request.selector
            proposed_relation = request.relation
        else:
            raise GrowthInvalidRequestError()
        current = _build_growth_current(self.reader, policy=self.policy, clock=self.clock)
        goal, target, pattern, bucket = _growth_resolve_selector(current, selector)
        note = _growth_review_note(current, bucket)
        journal = getattr(note, "decision_journal", None)
        if journal is None:
            raise GrowthBehavioralSourceUnavailableError()
        options = tuple(
            GrowthMappingReviewOptionV1(
                option_index=index,
                option_fingerprint=fingerprint,
                label=label,
            )
            for index, (fingerprint, label) in enumerate(
                zip(
                    bucket.observations[0].option_namespace.ordered_option_fingerprints,
                    journal.available_options,
                    strict=True,
                )
            )
        )
        candidate = (
            compute_growth_goal_choice_mapping_fingerprint(goal, target, proposed_relation)
            if proposed_relation is not None
            else None
        )
        projection = GrowthGoalChoiceMappingReviewProjectionV1(
            generated_at=current.generated_at,
            goal=goal,
            behavioral_target=target,
            candidate_mapping_fingerprint=candidate,
            goal_text=_growth_goal_text(current, goal),
            goal_domain=goal.domain or "",
            goal_evidence_at=goal.evidence_at,
            goal_evidence_at_precision=goal.evidence_at_precision,
            situation=journal.situation,
            information_known_at_decision_time=journal.information_known_at_decision_time,
            criteria=journal.criteria,
            ordered_options=options,
            pattern_type=pattern.pattern_type,
            pattern_state=pattern.state,
            selected_option=target.option,
            proposed_relation=proposed_relation,
            caveats=_growth_review_caveats(goal),
        )
        if len(projection.to_json().encode("utf-8")) > GROWTH_MAPPING_MAX_REVIEW_PROJECTION_BYTES:
            raise GrowthResultTooLargeError()
        return projection

    def accept(
        self,
        request: GrowthMappingAcceptanceRequestV1,
    ) -> GrowthGoalChoiceMappingV1:
        """Freshly revalidate an explicit confirmation, then append durably."""

        if type(request) is not GrowthMappingAcceptanceRequestV1:
            raise GrowthInvalidRequestError()
        if not request.confirmed or request.review_projection is None:
            raise GrowthInvalidRequestError()
        current = _build_growth_current(self.reader, policy=self.policy, clock=self.clock)
        goal, target, _pattern, _bucket = _growth_resolve_selector(current, request.selector)
        projection = request.review_projection
        if projection.goal != goal or projection.behavioral_target != target:
            raise GrowthGoalMappingStaleError()
        if (
            projection.proposed_relation is not None
            and projection.proposed_relation is not request.relation
        ):
            raise GrowthGoalMappingStaleError()
        reviewed_at = _growth_now_for_acceptance(self.clock)
        try:
            return self.store.accept_mapping(
                goal=goal,
                behavioral_target=target,
                relation=request.relation,
                operation_id=request.operation_id,
                reviewed_at=reviewed_at,
                supersedes_mapping_id=request.supersedes_mapping_id,
            )
        except GrowthError:
            raise
        except Exception:
            raise GrowthMappingStoreUnavailableError() from None


GrowthEngine = BuildGrowthEngine
GrowthMappingService = BuildGrowthEngine
GrowthApplicationService = BuildGrowthEngine
BuildGrowthEngineV1 = BuildGrowthEngine


def _growth_now_for_acceptance(clock: GrowthClock) -> datetime:
    try:
        return _normalize_canonical_growth_timestamp(clock())
    except TypeError, ValueError, OverflowError, OSError:
        raise GrowthInvalidRequestError() from None


def _growth_engine_caveats(
    goals: tuple[GrowthGoalIdentityV1, ...],
    results: tuple[GrowthGoalRelationResultV1, ...],
) -> tuple[GrowthCaveatCodeV1, ...]:
    values: set[GrowthCaveatCodeV1] = set()
    if goals:
        values.update(_growth_review_caveats(goals[0]))
    if any(item.state is GrowthRelationStateV1.MIXED_BEHAVIOR for item in results):
        values.add(GrowthCaveatCodeV1.MIXED_NO_WINNER)
    if any(item.state is GrowthRelationStateV1.CHANGED_BEHAVIOR for item in results):
        values.add(GrowthCaveatCodeV1.CHANGED_NOT_A_TRAIT)
    if any(
        item.state
        in {
            GrowthRelationStateV1.BEHAVIORAL_EVIDENCE_INSUFFICIENT,
            GrowthRelationStateV1.NOT_COMPARABLE,
        }
        for item in results
    ):
        values.add(GrowthCaveatCodeV1.INSUFFICIENT_NOT_CONFLICT)
    if any(item.state is GrowthRelationStateV1.GOAL_MAPPING_MISSING for item in results):
        values.add(GrowthCaveatCodeV1.GOAL_MAPPING_MISSING_FOR_OBSERVED_OPTION)
    if any(item.mapping is not None for item in results):
        values.add(GrowthCaveatCodeV1.MAPPING_REVIEW_TIME_IS_NOT_EVIDENCE_TIME)
    if goals:
        values.add(GrowthCaveatCodeV1.TEMPORAL_ALIGNMENT_NOT_PROVEN)
    return tuple(item for item in GrowthCaveatCodeV1 if item in values)


def validate_growth_engine_result(value: object) -> GrowthEngineResultV1:
    """Validate one complete bounded raw-label-free Growth result."""

    if type(value) is not GrowthEngineResultV1:
        raise ValueError("Growth engine result is invalid")
    if len(canonical_growth_json(value.as_dict()).encode("utf-8")) > GROWTH_MAX_RESULT_BYTES:
        raise GrowthResultTooLargeError()
    return value


def validate_growth_goal_choice_mapping(value: object) -> GrowthGoalChoiceMappingV1:
    """Validate one immutable accepted mapping DTO."""

    if type(value) is not GrowthGoalChoiceMappingV1:
        raise GrowthGoalMappingInvalidError()
    return value


validate_growth_mapping = validate_growth_goal_choice_mapping
validate_growth_result = validate_growth_engine_result


def derive_growth_mapping_store_root(env_file: Path) -> Path:
    """Derive the approved store root from the explicit production env file."""

    if not isinstance(env_file, Path) or not env_file.is_absolute():
        raise GrowthMappingStoreUnavailableError()
    if _growth_path_is_symlink(env_file) or _growth_has_symlink_component(env_file.parent):
        raise GrowthMappingStoreUnavailableError()
    try:
        info = env_file.lstat()
        if not stat.S_ISREG(info.st_mode):
            raise GrowthMappingStoreUnavailableError()
        parent = env_file.parent.resolve(strict=True)
    except GrowthError:
        raise
    except OSError:
        raise GrowthMappingStoreUnavailableError() from None
    root = parent / "growth-goal-mapping"
    if _growth_path_is_symlink(root):
        raise GrowthMappingStoreUnavailableError()
    if root == PRODUCTION_GROWTH_MAPPING_STORE_ROOT:
        try:
            if parent != GROWTH_MAPPING_RUNTIME_ROOT.resolve(strict=True):
                raise GrowthMappingStoreUnavailableError()
        except OSError:
            raise GrowthMappingStoreUnavailableError() from None
    return root


derive_growth_goal_mapping_store_root = derive_growth_mapping_store_root


def create_growth_mapping_store(
    env_file: Path,
    *,
    vault_root: Path | None = None,
    repository_root: Path | None = None,
    clock: GrowthClock = lambda: datetime.now(UTC),
) -> GrowthMappingStore:
    """Create a store with an explicit env-file-derived root and no side effects."""

    root = derive_growth_mapping_store_root(env_file)
    return GrowthMappingStore(
        root,
        expected_owner_group=(
            PRODUCTION_GROWTH_MAPPING_OWNER_GROUP
            if root == PRODUCTION_GROWTH_MAPPING_STORE_ROOT
            else None
        ),
        vault_root=vault_root,
        repository_root=repository_root,
        clock=clock,
    )


build_growth_mapping_store = create_growth_mapping_store


def create_growth_engine(
    reader: VaultReader,
    env_file: Path,
    *,
    policy: SelfModelPolicy = DEFAULT_SELF_MODEL_POLICY,
    clock: GrowthClock = lambda: datetime.now(UTC),
    vault_root: Path | None = None,
    repository_root: Path | None = None,
) -> BuildGrowthEngine:
    """Build the Stage 11B service from the approved explicit env-file root."""

    return BuildGrowthEngine(
        reader=reader,
        store=create_growth_mapping_store(
            env_file,
            vault_root=vault_root,
            repository_root=repository_root,
            clock=clock,
        ),
        policy=policy,
        clock=clock,
    )


build_growth_engine = create_growth_engine


__all__ = [
    "CONTRACT_VERSION",
    "DERIVATION_VERSION",
    "GROWTH_CONTRACT_VERSION",
    "GROWTH_DERIVATION_VERSION",
    "GROWTH_MAPPING_BASIS",
    "GROWTH_MAPPING_LOCK_FILE_NAME",
    "GROWTH_MAPPING_MANIFEST_FILE_NAME",
    "GROWTH_MAPPING_MAX_ACTIVE",
    "GROWTH_MAPPING_MAX_RECORD_BYTES",
    "GROWTH_MAPPING_MAX_REVIEW_PROJECTION_BYTES",
    "GROWTH_MAPPING_POLICY_CANONICAL_JSON",
    "GROWTH_MAPPING_POLICY_FINGERPRINT",
    "GROWTH_MAPPING_POLICY_ID",
    "GROWTH_MAPPING_RECORD_FILE_NAME",
    "GROWTH_MAPPING_RUNTIME_ROOT",
    "GROWTH_MAPPING_STORE_FORMAT_VERSION",
    "GROWTH_MAPPING_STORE_ROOT",
    "GROWTH_MAX_RESULTS",
    "GROWTH_MAX_RESULT_BYTES",
    "GROWTH_MIN_RESULTS",
    "GROWTH_MIN_RESULT_BYTES",
    "GROWTH_POLICY_CANONICAL_JSON",
    "GROWTH_POLICY_FINGERPRINT",
    "GROWTH_POLICY_ID",
    "MAPPING_BASIS",
    "MAPPING_POLICY_FINGERPRINT",
    "MAPPING_POLICY_ID",
    "POLICY_FINGERPRINT",
    "POLICY_ID",
    "PRODUCTION_GROWTH_MAPPING_OWNER_GROUP",
    "PRODUCTION_GROWTH_MAPPING_STORE_ROOT",
    "BuildGrowthEngine",
    "BuildGrowthEngineV1",
    "BuildGrowthGoalContext",
    "BuildGrowthGoalContextV1",
    "BuildGrowthGoals",
    "GrowthAdvisorResultRefV1",
    "GrowthApplicationService",
    "GrowthBehavioralEvidenceInsufficientError",
    "GrowthBehavioralPatternRefV1",
    "GrowthBehavioralSourceUnavailableError",
    "GrowthBehavioralStateNotComparableError",
    "GrowthBehavioralTargetIdentityV1",
    "GrowthBehavioralTargetV1",
    "GrowthCaveatCode",
    "GrowthCaveatCodeV1",
    "GrowthConcurrencyConflictError",
    "GrowthEngine",
    "GrowthEngineRequestV1",
    "GrowthEngineResult",
    "GrowthEngineResultV1",
    "GrowthError",
    "GrowthErrorCode",
    "GrowthGoalChoiceMappingAcceptanceRequestV1",
    "GrowthGoalChoiceMappingReviewProjectionV1",
    "GrowthGoalChoiceMappingReviewRequestV1",
    "GrowthGoalChoiceMappingSelectorV1",
    "GrowthGoalChoiceMappingStore",
    "GrowthGoalChoiceMappingV1",
    "GrowthGoalContext",
    "GrowthGoalContextResultV1",
    "GrowthGoalContextV1",
    "GrowthGoalIdentityV1",
    "GrowthGoalMappingInvalidError",
    "GrowthGoalMappingMissingError",
    "GrowthGoalMappingStaleError",
    "GrowthGoalMissingError",
    "GrowthGoalRelation",
    "GrowthGoalRelationResult",
    "GrowthGoalRelationResultV1",
    "GrowthGoalSelectionMode",
    "GrowthGoalSelectionModeV1",
    "GrowthGoalSelectionRequiredError",
    "GrowthGoalSelectionV1",
    "GrowthGoalSourceChangedError",
    "GrowthGoalSourceUnavailableError",
    "GrowthIdempotencyConflictError",
    "GrowthInvalidRequestError",
    "GrowthMappingAcceptanceRequestV1",
    "GrowthMappingConflictError",
    "GrowthMappingLifecycleEvent",
    "GrowthMappingLifecycleEventV1",
    "GrowthMappingLifecycleState",
    "GrowthMappingLifecycleStateV1",
    "GrowthMappingLifecycleViewV1",
    "GrowthMappingOperationalStore",
    "GrowthMappingRefV1",
    "GrowthMappingReviewOptionV1",
    "GrowthMappingReviewProjectionV1",
    "GrowthMappingReviewRequestV1",
    "GrowthMappingSelectorV1",
    "GrowthMappingService",
    "GrowthMappingStore",
    "GrowthMappingStoreCorruptError",
    "GrowthMappingStoreManifestV1",
    "GrowthMappingStoreRecordEnvelopeV1",
    "GrowthMappingStoreUnavailableError",
    "GrowthMappingStoreV1",
    "GrowthMappingV1",
    "GrowthMultipleGoalsAmbiguousError",
    "GrowthPolicyMismatchError",
    "GrowthRecommendationUnavailableError",
    "GrowthRelationResultV1",
    "GrowthRelationState",
    "GrowthRelationStateV1",
    "GrowthResultTooLargeError",
    "GrowthResultV1",
    "GrowthState",
    "GrowthStateV1",
    "GrowthUnsupportedSemanticComparisonError",
    "build_growth_behavioral_target_identity",
    "build_growth_engine",
    "build_growth_goal_identity",
    "build_growth_mapping_store",
    "canonical_growth_json",
    "compute_growth_behavioral_pattern_reference_fingerprint",
    "compute_growth_goal_choice_mapping_fingerprint",
    "compute_growth_mapping_fingerprint",
    "create_growth_engine",
    "create_growth_mapping_store",
    "derive_growth_goal_mapping_store_root",
    "derive_growth_mapping_store_root",
    "growth_behavioral_pattern_reference_fingerprint",
    "growth_goal_choice_mapping_fingerprint",
    "growth_hash_json",
    "growth_hash_text",
    "growth_operation_id_fingerprint",
    "serialize_growth_goal_context",
    "validate_growth_engine_request",
    "validate_growth_engine_result",
    "validate_growth_goal_choice_mapping",
    "validate_growth_goal_context",
    "validate_growth_goal_identity",
    "validate_growth_goal_selection",
    "validate_growth_hash",
    "validate_growth_mapping",
    "validate_growth_mapping_policy",
    "validate_growth_policy",
]
