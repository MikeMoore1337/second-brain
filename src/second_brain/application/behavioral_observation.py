"""Deterministic Stage 10A behavioral observation/cohort read model.

The module is deliberately read-only and provider-free.  Every build starts
from the current ``VaultReader.scan() -> build_report()`` boundary and emits
only bounded, immutable derived DTOs.  It does not create behavioral claims,
write to the vault, or retain state between builds.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections import defaultdict
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from pathlib import PurePosixPath
from typing import Final, cast
from uuid import UUID

from second_brain.application.decision_journal import (
    DecisionJournalBodyError,
    parse_decision_journal_body,
    parse_outcome_observation_body,
)
from second_brain.application.personal_memory import (
    is_personal_memory_enrolled,
    validate_canonical_personal_memory_fields,
)
from second_brain.application.ports import VaultReader
from second_brain.application.reports import (
    DiagnosticSeverity,
    ScanReport,
    VaultSnapshot,
    diagnostic_affects_content,
)
from second_brain.application.validation import build_report
from second_brain.domain.models import (
    DecisionJournalRecord,
    EvidenceAtPrecision,
    EvidenceKind,
    NoteRecord,
    NoteType,
    OutcomeObservationRecord,
    PersonalMemoryMetadata,
    SelfKind,
    VaultManifest,
)

type BehavioralHashV1 = str
type BehavioralObservationClock = Callable[[], datetime]

CONTRACT_VERSION: Final[str] = "behavioral-self-model-v1"
DERIVATION_VERSION: Final[str] = "behavioral-observation-v1"
FULL_DERIVATION_VERSION: Final[str] = "behavioral-self-model-derivation-v1"
POLICY_ID: Final[str] = "behavioral-self-model-exact-context-v1"
GROUPING_POLICY: Final[str] = "exact-reviewed-decision-context-v1"
SUPPORT_POLICY: Final[str] = "min-3-distinct-journals-v1"
TEMPORAL_POLICY: Final[str] = "two-90-day-windows-180-day-horizon-v1"
OUTCOME_POLICY: Final[str] = "presence-only-v1"
COMPOSITION_POLICY: Final[str] = "explicit-mapping-only-v1"
OBSERVATION_VERSION: Final[str] = DERIVATION_VERSION

POLICY_CANONICAL_JSON: Final[str] = (
    '{"composition":"explicit-mapping-only-v1","contract":"behavioral-self-model-v1",'
    '"grouping":"exact-reviewed-decision-context-v1","observation":"behavioral-observation-v1",'
    '"outcome":"presence-only-v1","support":"min-3-distinct-journals-v1",'
    '"temporal":"two-90-day-windows-180-day-horizon-v1","version":"1"}'
)
POLICY_FINGERPRINT: Final[str] = (
    "sha256:1fb9ffaab67835c30f29150999ee45044578d4cb5a3fbb32359574cd080c66c0"
)

TEMPORAL_WINDOW_DAYS: Final[int] = 90
ACTIVE_HORIZON_DAYS: Final[int] = 180
MINIMUM_COMPARABLE_OBSERVATIONS: Final[int] = 3
MINIMUM_OBSERVATIONS_PER_CHANGE_WINDOW: Final[int] = 2
MAX_BEHAVIORAL_OBSERVATIONS: Final[int] = 200
MAX_BEHAVIORAL_COHORTS: Final[int] = 200
MAX_BEHAVIORAL_RESULT_BYTES: Final[int] = 65_536
MAX_DOMAIN_BYTES: Final[int] = 64

_HASH_PATTERN: Final[re.Pattern[str]] = re.compile(r"sha256:[0-9a-f]{64}\Z", re.ASCII)
_DOMAIN_PATTERN: Final[re.Pattern[str]] = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*\Z", re.ASCII)
_SAFE_RELATIVE_PATH_PARTS: Final[frozenset[str]] = frozenset({"", ".", ".."})

_SOURCE_COMPLETENESS_CODES: Final[frozenset[str]] = frozenset(
    {
        "NOTE_READ_ERROR",
        "VAULT_DIRECTORY_READ_ERROR",
        "VAULT_ENTRY_RESOLVE_ERROR",
        "VAULT_LINKED_DIRECTORY",
        "VAULT_OVERLAPPING_ROOTS",
        "VAULT_PATH_ESCAPE",
        "VAULT_ROOT_MISSING",
        "VAULT_ROOT_NOT_DIRECTORY",
    }
)
_JOURNAL_INTEGRITY_CODES: Final[frozenset[str]] = frozenset(
    {
        "DUPLICATE_NOTE_ID",
        "DECISION_JOURNAL_INVALID_BODY",
        "OUTCOME_OBSERVATION_INVALID_BODY",
    }
)
_RELATION_DIAGNOSTIC_PREFIXES: Final[tuple[str, ...]] = (
    "PERSONAL_MEMORY_",
    "OUTCOME_DECISION_",
)


class BehavioralSelfModelErrorCode(StrEnum):
    """Fixed safe Stage 10 error taxonomy."""

    SOURCE_UNAVAILABLE = "BEHAVIORAL_SELF_MODEL_SOURCE_UNAVAILABLE"
    INVALID_JOURNAL = "BEHAVIORAL_SELF_MODEL_INVALID_JOURNAL"
    INSUFFICIENT_COMPARABLE_DECISIONS = "BEHAVIORAL_SELF_MODEL_INSUFFICIENT_COMPARABLE_DECISIONS"
    AMBIGUOUS_GROUPING = "BEHAVIORAL_SELF_MODEL_AMBIGUOUS_GROUPING"
    UNSUPPORTED_SEMANTIC_COMPARISON = "BEHAVIORAL_SELF_MODEL_UNSUPPORTED_SEMANTIC_COMPARISON"
    RESULT_TOO_LARGE = "BEHAVIORAL_SELF_MODEL_RESULT_TOO_LARGE"
    POLICY_MISMATCH = "BEHAVIORAL_SELF_MODEL_POLICY_MISMATCH"
    INVALID_REQUEST = "BEHAVIORAL_SELF_MODEL_INVALID_REQUEST"


_ERROR_MESSAGES: Final[dict[BehavioralSelfModelErrorCode, str]] = {
    BehavioralSelfModelErrorCode.SOURCE_UNAVAILABLE: (
        "the current behavioral source is unavailable"
    ),
    BehavioralSelfModelErrorCode.INVALID_JOURNAL: (
        "the current enrolled Decision Journal is invalid"
    ),
    BehavioralSelfModelErrorCode.INSUFFICIENT_COMPARABLE_DECISIONS: (
        "the current source has insufficient comparable decisions"
    ),
    BehavioralSelfModelErrorCode.AMBIGUOUS_GROUPING: (
        "the current behavioral cohort identity is ambiguous"
    ),
    BehavioralSelfModelErrorCode.UNSUPPORTED_SEMANTIC_COMPARISON: (
        "behavioral comparison requires unsupported semantic mapping"
    ),
    BehavioralSelfModelErrorCode.RESULT_TOO_LARGE: (
        "the behavioral result exceeds its bounded limit"
    ),
    BehavioralSelfModelErrorCode.POLICY_MISMATCH: "the behavioral policy binding is invalid",
    BehavioralSelfModelErrorCode.INVALID_REQUEST: "the behavioral observation request is invalid",
}


class BehavioralSelfModelError(RuntimeError):
    """Safe error without private bodies, paths, UUID inventories, or causes."""

    def __init__(self, code: BehavioralSelfModelErrorCode | str) -> None:
        normalized = _normalize_error_code(code)
        self.code = normalized.value
        self.message = _ERROR_MESSAGES[normalized]
        super().__init__(self.message)

    def as_dict(self) -> dict[str, str]:
        """Return a stable public error projection."""

        return {"code": self.code, "message": self.message}


class BehavioralSelfModelSourceUnavailableError(BehavioralSelfModelError):
    """The current canonical vault source cannot be read safely."""

    def __init__(self) -> None:
        super().__init__(BehavioralSelfModelErrorCode.SOURCE_UNAVAILABLE)


class BehavioralSelfModelInvalidJournalError(BehavioralSelfModelError):
    """An enrolled current Journal or its canonical relation is invalid."""

    def __init__(self) -> None:
        super().__init__(BehavioralSelfModelErrorCode.INVALID_JOURNAL)


class BehavioralSelfModelInsufficientComparableDecisionsError(BehavioralSelfModelError):
    """The current source has no cohort with enough evidence."""

    def __init__(self) -> None:
        super().__init__(BehavioralSelfModelErrorCode.INSUFFICIENT_COMPARABLE_DECISIONS)


class BehavioralSelfModelAmbiguousGroupingError(BehavioralSelfModelError):
    """Exact cohort identity cannot be proven uniquely."""

    def __init__(self) -> None:
        super().__init__(BehavioralSelfModelErrorCode.AMBIGUOUS_GROUPING)


class BehavioralSelfModelUnsupportedSemanticComparisonError(BehavioralSelfModelError):
    """A caller requested semantic grouping outside the exact v1 policy."""

    def __init__(self) -> None:
        super().__init__(BehavioralSelfModelErrorCode.UNSUPPORTED_SEMANTIC_COMPARISON)


class BehavioralSelfModelResultTooLargeError(BehavioralSelfModelError):
    """The complete bounded derived result cannot be emitted."""

    def __init__(self) -> None:
        super().__init__(BehavioralSelfModelErrorCode.RESULT_TOO_LARGE)


class BehavioralSelfModelPolicyMismatchError(BehavioralSelfModelError):
    """The exact approved Stage 10 policy binding is not present."""

    def __init__(self) -> None:
        super().__init__(BehavioralSelfModelErrorCode.POLICY_MISMATCH)


class BehavioralSelfModelInvalidRequestError(BehavioralSelfModelError):
    """The request type or bounded limits are invalid."""

    def __init__(self) -> None:
        super().__init__(BehavioralSelfModelErrorCode.INVALID_REQUEST)


@dataclass(frozen=True, slots=True)
class BehavioralObservationPolicy:
    """Composition-owned exact v1 policy binding."""

    contract_version: str
    policy_id: str
    derivation_version: str
    observation_version: str
    grouping_policy: str
    support_policy: str
    temporal_policy: str
    outcome_policy: str
    composition_policy: str


DEFAULT_BEHAVIORAL_OBSERVATION_POLICY: Final[BehavioralObservationPolicy] = (
    BehavioralObservationPolicy(
        contract_version=CONTRACT_VERSION,
        policy_id=POLICY_ID,
        derivation_version=FULL_DERIVATION_VERSION,
        observation_version=OBSERVATION_VERSION,
        grouping_policy=GROUPING_POLICY,
        support_policy=SUPPORT_POLICY,
        temporal_policy=TEMPORAL_POLICY,
        outcome_policy=OUTCOME_POLICY,
        composition_policy=COMPOSITION_POLICY,
    )
)


@dataclass(frozen=True, slots=True)
class BehavioralObservationRequest:
    """Bounded in-memory request for one current-vault rebuild."""

    max_observations: int = MAX_BEHAVIORAL_OBSERVATIONS
    max_cohorts: int = MAX_BEHAVIORAL_COHORTS


DEFAULT_BEHAVIORAL_OBSERVATION_REQUEST: Final[BehavioralObservationRequest] = (
    BehavioralObservationRequest()
)


class BehavioralOutcomePresenceV1(StrEnum):
    """Only the allowed later-observation metadata values."""

    ABSENT = "absent"
    PRESENT = "present"


class BehavioralTemporalWindowV1(StrEnum):
    """Internal deterministic membership result for Stage 10A windows."""

    CURRENT = "current"
    HISTORICAL = "historical"
    OUTSIDE_HORIZON = "outside_horizon"
    FUTURE = "future"
    UNKNOWN = "unknown"
    INVALID = "invalid"


@dataclass(frozen=True, slots=True)
class BehavioralOptionIdentityV1:
    """Ordered option identity local to one Journal option namespace."""

    option_index: int
    option_fingerprint: BehavioralHashV1

    def __post_init__(self) -> None:
        if type(self.option_index) is not int or not 0 <= self.option_index <= 19:
            raise ValueError("behavioral option identity is invalid")
        validate_behavioral_hash(self.option_fingerprint)

    def as_dict(self) -> dict[str, object]:
        """Return the raw-label-free DTO projection."""

        return {
            "option_index": self.option_index,
            "option_fingerprint": self.option_fingerprint,
        }


@dataclass(frozen=True, slots=True)
class BehavioralOptionNamespaceV1:
    """Ordered bounded fingerprints for a Journal's available options."""

    option_count: int
    ordered_option_fingerprints: tuple[BehavioralHashV1, ...]

    def __post_init__(self) -> None:
        if type(self.option_count) is not int or not 2 <= self.option_count <= 20:
            raise ValueError("behavioral option namespace is invalid")
        if type(self.ordered_option_fingerprints) is not tuple:
            raise ValueError("behavioral option namespace is invalid")
        if len(self.ordered_option_fingerprints) != self.option_count:
            raise ValueError("behavioral option namespace is invalid")
        for fingerprint in self.ordered_option_fingerprints:
            validate_behavioral_hash(fingerprint)
        if len(set(self.ordered_option_fingerprints)) != self.option_count:
            raise ValueError("behavioral option namespace is invalid")

    def as_dict(self) -> dict[str, object]:
        """Return the raw-label-free DTO projection."""

        return {
            "option_count": self.option_count,
            "ordered_option_fingerprints": list(self.ordered_option_fingerprints),
        }


@dataclass(frozen=True, slots=True)
class BehavioralCriteriaRepresentationV1:
    """Ordered bounded fingerprints for a Journal's criteria."""

    criteria_count: int
    ordered_item_fingerprints: tuple[BehavioralHashV1, ...]

    def __post_init__(self) -> None:
        if type(self.criteria_count) is not int or not 1 <= self.criteria_count <= 20:
            raise ValueError("behavioral criteria representation is invalid")
        if type(self.ordered_item_fingerprints) is not tuple:
            raise ValueError("behavioral criteria representation is invalid")
        if len(self.ordered_item_fingerprints) != self.criteria_count:
            raise ValueError("behavioral criteria representation is invalid")
        for fingerprint in self.ordered_item_fingerprints:
            validate_behavioral_hash(fingerprint)
        if len(set(self.ordered_item_fingerprints)) != self.criteria_count:
            raise ValueError("behavioral criteria representation is invalid")

    def as_dict(self) -> dict[str, object]:
        """Return the raw-label-free DTO projection."""

        return {
            "criteria_count": self.criteria_count,
            "ordered_item_fingerprints": list(self.ordered_item_fingerprints),
        }


@dataclass(frozen=True, slots=True)
class BehavioralCohortIdentityV1:
    """Exact current context identity without raw private labels."""

    grouping_policy: str
    domain: str
    situation_fingerprint: BehavioralHashV1
    information_fingerprint: BehavioralHashV1
    option_namespace_fingerprint: BehavioralHashV1
    criteria_fingerprint: BehavioralHashV1
    cohort_fingerprint: BehavioralHashV1

    def __post_init__(self) -> None:
        if self.grouping_policy != GROUPING_POLICY or type(self.grouping_policy) is not str:
            raise ValueError("behavioral cohort identity is invalid")
        if not _valid_domain(self.domain, allow_none=False):
            raise ValueError("behavioral cohort identity is invalid")
        for fingerprint in (
            self.situation_fingerprint,
            self.information_fingerprint,
            self.option_namespace_fingerprint,
            self.criteria_fingerprint,
            self.cohort_fingerprint,
        ):
            validate_behavioral_hash(fingerprint)

    def as_dict(self) -> dict[str, object]:
        """Return the raw-label-free DTO projection."""

        return {
            "grouping_policy": self.grouping_policy,
            "domain": self.domain,
            "situation_fingerprint": self.situation_fingerprint,
            "information_fingerprint": self.information_fingerprint,
            "option_namespace_fingerprint": self.option_namespace_fingerprint,
            "criteria_fingerprint": self.criteria_fingerprint,
            "cohort_fingerprint": self.cohort_fingerprint,
        }


@dataclass(frozen=True, slots=True)
class BehavioralObservationV1:
    """Immutable, one-Journal derived behavioral observation."""

    contract_version: str
    derivation_version: str
    source_journal_uuid: UUID
    evidence_at: datetime
    evidence_at_precision: str
    cohort: BehavioralCohortIdentityV1
    option_namespace: BehavioralOptionNamespaceV1
    criteria: BehavioralCriteriaRepresentationV1
    chosen_option: BehavioralOptionIdentityV1
    outcome_presence: BehavioralOutcomePresenceV1 | str
    journal_snapshot_fingerprint: BehavioralHashV1

    def __post_init__(self) -> None:
        if self.contract_version != CONTRACT_VERSION or type(self.contract_version) is not str:
            raise ValueError("behavioral observation is invalid")
        if (
            self.derivation_version != DERIVATION_VERSION
            or type(self.derivation_version) is not str
        ):
            raise ValueError("behavioral observation is invalid")
        if type(self.source_journal_uuid) is not UUID or self.source_journal_uuid.version != 7:
            raise ValueError("behavioral observation is invalid")
        if not _is_canonical_utc(self.evidence_at):
            raise ValueError("behavioral observation is invalid")
        if self.evidence_at_precision != EvidenceAtPrecision.EXACT.value:
            raise ValueError("behavioral observation is invalid")
        if type(self.cohort) is not BehavioralCohortIdentityV1:
            raise ValueError("behavioral observation is invalid")
        if type(self.option_namespace) is not BehavioralOptionNamespaceV1:
            raise ValueError("behavioral observation is invalid")
        if type(self.criteria) is not BehavioralCriteriaRepresentationV1:
            raise ValueError("behavioral observation is invalid")
        if type(self.chosen_option) is not BehavioralOptionIdentityV1:
            raise ValueError("behavioral observation is invalid")
        if self.chosen_option.option_index >= self.option_namespace.option_count:
            raise ValueError("behavioral observation is invalid")
        if (
            self.option_namespace.ordered_option_fingerprints[self.chosen_option.option_index]
            != self.chosen_option.option_fingerprint
        ):
            raise ValueError("behavioral observation is invalid")
        presence = self.outcome_presence
        if type(presence) is str:
            try:
                presence = BehavioralOutcomePresenceV1(presence)
            except ValueError:
                raise ValueError("behavioral observation is invalid") from None
            object.__setattr__(self, "outcome_presence", presence)
        if type(presence) is not BehavioralOutcomePresenceV1:
            raise ValueError("behavioral observation is invalid")
        validate_behavioral_hash(self.journal_snapshot_fingerprint)

    def as_dict(self) -> dict[str, object]:
        """Return a complete JSON-compatible DTO without raw private text."""

        return {
            "contract_version": self.contract_version,
            "derivation_version": self.derivation_version,
            "source_journal_uuid": str(self.source_journal_uuid),
            "evidence_at": _format_utc(self.evidence_at),
            "evidence_at_precision": self.evidence_at_precision,
            "cohort": self.cohort.as_dict(),
            "option_namespace": self.option_namespace.as_dict(),
            "criteria": self.criteria.as_dict(),
            "chosen_option": self.chosen_option.as_dict(),
            "outcome_presence": _normalize_outcome_presence(self.outcome_presence).value,
            "journal_snapshot_fingerprint": self.journal_snapshot_fingerprint,
        }

    def to_json(self) -> str:
        """Serialize the DTO with stable canonical JSON settings."""

        return _canonical_json(self.as_dict())


@dataclass(frozen=True, slots=True)
class BehavioralOptionCountV1:
    """Exact integer count for one local option identity."""

    option: BehavioralOptionIdentityV1
    count: int

    def __post_init__(self) -> None:
        if type(self.option) is not BehavioralOptionIdentityV1:
            raise ValueError("behavioral option count is invalid")
        if type(self.count) is not int or self.count < 0:
            raise ValueError("behavioral option count is invalid")

    def as_dict(self) -> dict[str, object]:
        """Return only exact identity and integer count."""

        return {"option": self.option.as_dict(), "count": self.count}


@dataclass(frozen=True, slots=True)
class BehavioralWindowCountsV1:
    """Stage 10A temporal/count primitive, not a behavioral pattern claim."""

    window: BehavioralTemporalWindowV1
    observation_count: int
    choice_counts: tuple[BehavioralOptionCountV1, ...]

    def __post_init__(self) -> None:
        if type(self.window) is not BehavioralTemporalWindowV1:
            raise ValueError("behavioral window counts are invalid")
        if self.window not in {
            BehavioralTemporalWindowV1.CURRENT,
            BehavioralTemporalWindowV1.HISTORICAL,
        }:
            raise ValueError("behavioral window counts are invalid")
        if type(self.observation_count) is not int or self.observation_count < 0:
            raise ValueError("behavioral window counts are invalid")
        if type(self.choice_counts) is not tuple:
            raise ValueError("behavioral window counts are invalid")
        if any(type(item) is not BehavioralOptionCountV1 for item in self.choice_counts):
            raise ValueError("behavioral window counts are invalid")
        indexes = tuple(item.option.option_index for item in self.choice_counts)
        if indexes != tuple(sorted(indexes)) or len(indexes) != len(set(indexes)):
            raise ValueError("behavioral window counts are invalid")
        if sum(item.count for item in self.choice_counts) != self.observation_count:
            raise ValueError("behavioral window counts are invalid")

    def as_dict(self) -> dict[str, object]:
        """Return exact counts without any claim/state language."""

        return {
            "window": self.window.value,
            "observation_count": self.observation_count,
            "choice_counts": [item.as_dict() for item in self.choice_counts],
        }


@dataclass(frozen=True, slots=True)
class BehavioralCohortBucketV1:
    """Deterministic exact-cohort bucket for future Stage 10B consumers."""

    cohort: BehavioralCohortIdentityV1
    observations: tuple[BehavioralObservationV1, ...]

    def __post_init__(self) -> None:
        if type(self.cohort) is not BehavioralCohortIdentityV1:
            raise ValueError("behavioral cohort bucket is invalid")
        if type(self.observations) is not tuple or not self.observations:
            raise ValueError("behavioral cohort bucket is invalid")
        if any(type(item) is not BehavioralObservationV1 for item in self.observations):
            raise ValueError("behavioral cohort bucket is invalid")
        if any(item.cohort != self.cohort for item in self.observations):
            raise ValueError("behavioral cohort bucket is invalid")
        ids = tuple(item.source_journal_uuid for item in self.observations)
        if len(ids) != len(set(ids)):
            raise ValueError("behavioral cohort bucket is invalid")
        expected_order = tuple(
            sorted(
                self.observations,
                key=lambda item: (item.evidence_at, str(item.source_journal_uuid)),
            )
        )
        if self.observations != expected_order:
            raise ValueError("behavioral cohort bucket is invalid")

    def observations_in_window(
        self,
        window: BehavioralTemporalWindowV1,
        *,
        generated_at: datetime,
    ) -> tuple[BehavioralObservationV1, ...]:
        """Return observations in one fixed window in bucket order."""

        if window not in {
            BehavioralTemporalWindowV1.CURRENT,
            BehavioralTemporalWindowV1.HISTORICAL,
        }:
            raise ValueError("behavioral window is invalid")
        return tuple(
            observation
            for observation in self.observations
            if classify_behavioral_window(observation.evidence_at, generated_at) is window
        )

    def window_counts(
        self,
        window: BehavioralTemporalWindowV1,
        *,
        generated_at: datetime,
    ) -> BehavioralWindowCountsV1:
        """Return exact integer membership and per-option counts."""

        observations = self.observations_in_window(window, generated_at=generated_at)
        by_option: defaultdict[tuple[int, str], int] = defaultdict(int)
        for observation in observations:
            key = (
                observation.chosen_option.option_index,
                observation.chosen_option.option_fingerprint,
            )
            by_option[key] += 1
        counts = tuple(
            BehavioralOptionCountV1(
                option=BehavioralOptionIdentityV1(index, fingerprint),
                count=count,
            )
            for (index, fingerprint), count in sorted(by_option.items())
        )
        return BehavioralWindowCountsV1(window, len(observations), counts)


@dataclass(frozen=True, slots=True)
class BehavioralObservationBuildResultV1:
    """Complete disposable Stage 10A observations/cohorts projection."""

    contract_version: str
    derivation_version: str
    policy_id: str
    policy_fingerprint: BehavioralHashV1
    generated_at: datetime
    observations: tuple[BehavioralObservationV1, ...]
    cohorts: tuple[BehavioralCohortBucketV1, ...]
    eligible_journal_count: int
    comparable_observation_count: int
    excluded_unknown_time_count: int
    excluded_missing_domain_count: int
    excluded_future_or_invalid_time_count: int
    excluded_outside_horizon_count: int

    def __post_init__(self) -> None:
        if self.contract_version != CONTRACT_VERSION or type(self.contract_version) is not str:
            raise ValueError("behavioral observation result is invalid")
        if (
            self.derivation_version != DERIVATION_VERSION
            or type(self.derivation_version) is not str
        ):
            raise ValueError("behavioral observation result is invalid")
        if self.policy_id != POLICY_ID or type(self.policy_id) is not str:
            raise ValueError("behavioral observation result is invalid")
        validate_behavioral_hash(self.policy_fingerprint)
        if self.policy_fingerprint != POLICY_FINGERPRINT:
            raise ValueError("behavioral observation result is invalid")
        if not _is_canonical_utc(self.generated_at):
            raise ValueError("behavioral observation result is invalid")
        if type(self.observations) is not tuple or type(self.cohorts) is not tuple:
            raise ValueError("behavioral observation result is invalid")
        if (
            len(self.observations) > MAX_BEHAVIORAL_OBSERVATIONS
            or len(self.cohorts) > MAX_BEHAVIORAL_COHORTS
        ):
            raise ValueError("behavioral observation result is invalid")
        if any(type(item) is not BehavioralObservationV1 for item in self.observations):
            raise ValueError("behavioral observation result is invalid")
        if any(type(item) is not BehavioralCohortBucketV1 for item in self.cohorts):
            raise ValueError("behavioral observation result is invalid")
        source_ids = tuple(item.source_journal_uuid for item in self.observations)
        if len(source_ids) != len(set(source_ids)):
            raise ValueError("behavioral observation result is invalid")
        expected_observation_order = tuple(
            sorted(
                self.observations,
                key=lambda item: (
                    item.cohort.cohort_fingerprint,
                    item.evidence_at,
                    str(item.source_journal_uuid),
                ),
            )
        )
        if self.observations != expected_observation_order:
            raise ValueError("behavioral observation result is invalid")
        expected_bucket_order = tuple(
            sorted(self.cohorts, key=lambda item: item.cohort.cohort_fingerprint)
        )
        if self.cohorts != expected_bucket_order:
            raise ValueError("behavioral observation result is invalid")
        flattened_observations = tuple(
            observation for bucket in self.cohorts for observation in bucket.observations
        )
        if flattened_observations != self.observations:
            raise ValueError("behavioral observation result is invalid")
        if type(self.eligible_journal_count) is not int or self.eligible_journal_count < 0:
            raise ValueError("behavioral observation result is invalid")
        if self.comparable_observation_count != len(self.observations):
            raise ValueError("behavioral observation result is invalid")
        for value in (
            self.excluded_unknown_time_count,
            self.excluded_missing_domain_count,
            self.excluded_future_or_invalid_time_count,
            self.excluded_outside_horizon_count,
        ):
            if type(value) is not int or value < 0:
                raise ValueError("behavioral observation result is invalid")
        if self.eligible_journal_count != (
            self.comparable_observation_count
            + self.excluded_unknown_time_count
            + self.excluded_missing_domain_count
            + self.excluded_future_or_invalid_time_count
        ):
            raise ValueError("behavioral observation result is invalid")

    @property
    def active_observation_count(self) -> int:
        """Return current plus historical observations in the active horizon."""

        return sum(
            len(bucket.observations_in_window(window, generated_at=self.generated_at))
            for bucket in self.cohorts
            for window in (
                BehavioralTemporalWindowV1.CURRENT,
                BehavioralTemporalWindowV1.HISTORICAL,
            )
        )

    def as_dict(self) -> dict[str, object]:
        """Return only bounded derived DTO data and integer primitives."""

        return {
            "contract_version": self.contract_version,
            "derivation_version": self.derivation_version,
            "policy_id": self.policy_id,
            "policy_fingerprint": self.policy_fingerprint,
            "generated_at": _format_utc(self.generated_at),
            "observations": [item.as_dict() for item in self.observations],
            "cohorts": [
                {
                    "cohort": bucket.cohort.as_dict(),
                    "source_journal_uuids": [
                        str(item.source_journal_uuid) for item in bucket.observations
                    ],
                    "current": bucket.window_counts(
                        BehavioralTemporalWindowV1.CURRENT,
                        generated_at=self.generated_at,
                    ).as_dict(),
                    "historical": bucket.window_counts(
                        BehavioralTemporalWindowV1.HISTORICAL,
                        generated_at=self.generated_at,
                    ).as_dict(),
                }
                for bucket in self.cohorts
            ],
            "eligible_journal_count": self.eligible_journal_count,
            "comparable_observation_count": self.comparable_observation_count,
            "active_observation_count": self.active_observation_count,
            "excluded_unknown_time_count": self.excluded_unknown_time_count,
            "excluded_missing_domain_count": self.excluded_missing_domain_count,
            "excluded_future_or_invalid_time_count": self.excluded_future_or_invalid_time_count,
            "excluded_outside_horizon_count": self.excluded_outside_horizon_count,
        }

    def to_json(self) -> str:
        """Serialize the rebuild result with stable canonical JSON settings."""

        return _canonical_json(self.as_dict())


BehavioralObservationResultV1 = BehavioralObservationBuildResultV1


@dataclass(frozen=True, slots=True)
class _CohortKey:
    domain: str
    situation: str
    information: str
    options: tuple[str, ...]
    criteria: tuple[str, ...]
    grouping_policy: str = GROUPING_POLICY


@dataclass(frozen=True, slots=True)
class _ValidatedJournal:
    note: NoteRecord
    metadata: PersonalMemoryMetadata
    journal: DecisionJournalRecord


@dataclass(frozen=True, slots=True)
class BuildBehavioralObservations:
    """Rebuild Stage 10A from the current canonical vault on every call."""

    reader: VaultReader
    policy: BehavioralObservationPolicy = DEFAULT_BEHAVIORAL_OBSERVATION_POLICY
    clock: BehavioralObservationClock = lambda: datetime.now(UTC)

    def execute(
        self,
        request: BehavioralObservationRequest = DEFAULT_BEHAVIORAL_OBSERVATION_REQUEST,
    ) -> BehavioralObservationBuildResultV1:
        """Return a complete derived projection or one fixed safe error."""

        validate_behavioral_observation_request(request)
        policy_fingerprint = validate_behavioral_observation_policy(self.policy)
        generated_at = _read_clock(self.clock)
        report = _read_report(self.reader)
        journals, outcome_ids = _collect_current_stage2_records(report)

        observations: list[BehavioralObservationV1] = []
        excluded_unknown_time = 0
        excluded_missing_domain = 0
        excluded_future_or_invalid_time = 0
        excluded_outside_horizon = 0
        for item in journals:
            outcome_presence = (
                BehavioralOutcomePresenceV1.PRESENT
                if item.note.note_id in outcome_ids
                else BehavioralOutcomePresenceV1.ABSENT
            )
            try:
                observation, exclusion = _build_observation(
                    item,
                    generated_at=generated_at,
                    outcome_presence=outcome_presence,
                )
            except UnicodeError, ValueError, OverflowError:
                raise BehavioralSelfModelInvalidJournalError() from None
            if observation is None:
                if exclusion == BehavioralTemporalWindowV1.UNKNOWN:
                    excluded_unknown_time += 1
                elif exclusion == "missing_domain":
                    excluded_missing_domain += 1
                elif exclusion == BehavioralTemporalWindowV1.FUTURE:
                    excluded_future_or_invalid_time += 1
                elif exclusion == BehavioralTemporalWindowV1.OUTSIDE_HORIZON:
                    excluded_outside_horizon += 1
                else:
                    excluded_future_or_invalid_time += 1
                continue
            observations.append(observation)
            if exclusion == BehavioralTemporalWindowV1.OUTSIDE_HORIZON:
                excluded_outside_horizon += 1

        if len(observations) > request.max_observations:
            raise BehavioralSelfModelResultTooLargeError()
        ordered_observations = tuple(
            sorted(
                observations,
                key=lambda item: (
                    item.cohort.cohort_fingerprint,
                    item.evidence_at,
                    str(item.source_journal_uuid),
                ),
            )
        )
        grouped: defaultdict[str, list[BehavioralObservationV1]] = defaultdict(list)
        for observation in ordered_observations:
            grouped[observation.cohort.cohort_fingerprint].append(observation)
        if len(grouped) > request.max_cohorts:
            raise BehavioralSelfModelResultTooLargeError()
        cohorts = tuple(
            BehavioralCohortBucketV1(
                cohort=items[0].cohort,
                observations=tuple(items),
            )
            for _, items in sorted(grouped.items())
        )
        result = BehavioralObservationBuildResultV1(
            contract_version=CONTRACT_VERSION,
            derivation_version=DERIVATION_VERSION,
            policy_id=POLICY_ID,
            policy_fingerprint=policy_fingerprint,
            generated_at=generated_at,
            observations=ordered_observations,
            cohorts=cohorts,
            eligible_journal_count=len(journals),
            comparable_observation_count=len(ordered_observations),
            excluded_unknown_time_count=excluded_unknown_time,
            excluded_missing_domain_count=excluded_missing_domain,
            excluded_future_or_invalid_time_count=excluded_future_or_invalid_time,
            excluded_outside_horizon_count=excluded_outside_horizon,
        )
        try:
            if len(result.to_json().encode("utf-8")) > MAX_BEHAVIORAL_RESULT_BYTES:
                raise BehavioralSelfModelResultTooLargeError()
        except BehavioralSelfModelError:
            raise
        except UnicodeError, ValueError, OverflowError:
            raise BehavioralSelfModelResultTooLargeError() from None
        return result


BuildBehavioralObservation = BuildBehavioralObservations


def validate_behavioral_hash(value: object) -> BehavioralHashV1:
    """Validate and return one ``sha256:`` fingerprint."""

    if type(value) is not str or _HASH_PATTERN.fullmatch(value) is None:
        raise ValueError("behavioral hash is invalid")
    return value


def normalize_behavioral_text(value: str) -> str:
    """Apply only the normative ``N(value) = ' '.join(value.split())``."""

    if type(value) is not str:
        raise ValueError("behavioral text is invalid")
    return " ".join(value.split())


normalize_behavioral_value = normalize_behavioral_text


def behavioral_text_fingerprint(value: str) -> BehavioralHashV1:
    """Hash one normalized text value without any semantic normalization."""

    return _hash_text(normalize_behavioral_text(value))


def behavioral_option_namespace_fingerprint(values: Iterable[str]) -> BehavioralHashV1:
    """Hash the ordered normalized option-label sequence."""

    normalized = tuple(normalize_behavioral_text(value) for value in values)
    return _hash_json(list(normalized))


def behavioral_criteria_fingerprint(values: Iterable[str]) -> BehavioralHashV1:
    """Hash the ordered normalized criteria sequence."""

    normalized = tuple(normalize_behavioral_text(value) for value in values)
    return _hash_json(list(normalized))


def behavioral_cohort_fingerprint(
    *,
    domain: str,
    situation: str,
    information: str,
    options: Iterable[str],
    criteria: Iterable[str],
) -> BehavioralHashV1:
    """Build the exact contract cohort fingerprint from normalized values."""

    key = _make_cohort_key(domain, situation, information, options, criteria)
    return _cohort_identity(key).cohort_fingerprint


def journal_snapshot_fingerprint(
    *,
    cohort: BehavioralCohortIdentityV1,
    normalized_situation: str,
    normalized_information: str,
    normalized_options: Iterable[str],
    normalized_criteria: Iterable[str],
    chosen_option: BehavioralOptionIdentityV1,
    outcome_presence: BehavioralOutcomePresenceV1 | str,
) -> BehavioralHashV1:
    """Hash the exact current projection, chosen option, and outcome presence."""

    if type(cohort) is not BehavioralCohortIdentityV1:
        raise ValueError("behavioral cohort identity is invalid")
    if type(chosen_option) is not BehavioralOptionIdentityV1:
        raise ValueError("behavioral option identity is invalid")
    presence = _normalize_outcome_presence(outcome_presence)
    payload = {
        "basis": cohort.grouping_policy,
        "chosen_option": {
            "fingerprint": chosen_option.option_fingerprint,
            "index": chosen_option.option_index,
        },
        "criteria": [normalize_behavioral_text(value) for value in normalized_criteria],
        "domain": cohort.domain,
        "information": normalize_behavioral_text(normalized_information),
        "options": [normalize_behavioral_text(value) for value in normalized_options],
        "outcome_presence": presence.value,
        "situation": normalize_behavioral_text(normalized_situation),
    }
    return _hash_json(payload)


def classify_behavioral_window(
    evidence_at: object,
    generated_at: datetime,
) -> BehavioralTemporalWindowV1:
    """Classify exact UTC membership using the normative inclusive/exclusive edges."""

    if not _is_aware(generated_at):
        return BehavioralTemporalWindowV1.INVALID
    if evidence_at == "unknown":
        return BehavioralTemporalWindowV1.UNKNOWN
    if not _is_aware(evidence_at):
        return BehavioralTemporalWindowV1.INVALID
    generated_utc = generated_at.astimezone(UTC)
    evidence_utc = cast(datetime, evidence_at).astimezone(UTC)
    if evidence_utc > generated_utc:
        return BehavioralTemporalWindowV1.FUTURE
    current_lower = generated_utc - timedelta(days=TEMPORAL_WINDOW_DAYS)
    horizon_lower = generated_utc - timedelta(days=ACTIVE_HORIZON_DAYS)
    if evidence_utc < horizon_lower:
        return BehavioralTemporalWindowV1.OUTSIDE_HORIZON
    if evidence_utc < current_lower:
        return BehavioralTemporalWindowV1.HISTORICAL
    return BehavioralTemporalWindowV1.CURRENT


behavioral_window_membership = classify_behavioral_window
classify_temporal_window = classify_behavioral_window


def validate_behavioral_observation_request(
    request: object,
) -> BehavioralObservationRequest:
    """Validate strict bounded request values before reading the vault."""

    if type(request) is not BehavioralObservationRequest:
        raise BehavioralSelfModelInvalidRequestError()
    if not _valid_limit(request.max_observations) or not _valid_limit(request.max_cohorts):
        raise BehavioralSelfModelInvalidRequestError()
    return request


def validate_behavioral_observation_policy(
    policy: object,
) -> BehavioralHashV1:
    """Validate the complete approved policy and return its fixed fingerprint."""

    if type(policy) is not BehavioralObservationPolicy:
        raise BehavioralSelfModelPolicyMismatchError()
    expected = DEFAULT_BEHAVIORAL_OBSERVATION_POLICY
    for field in (
        "contract_version",
        "policy_id",
        "derivation_version",
        "observation_version",
        "grouping_policy",
        "support_policy",
        "temporal_policy",
        "outcome_policy",
        "composition_policy",
    ):
        if type(getattr(policy, field, None)) is not str or getattr(policy, field) != getattr(
            expected, field
        ):
            raise BehavioralSelfModelPolicyMismatchError()
    if (
        _hash_json(
            {
                "composition": policy.composition_policy,
                "contract": policy.contract_version,
                "grouping": policy.grouping_policy,
                "observation": policy.observation_version,
                "outcome": policy.outcome_policy,
                "support": policy.support_policy,
                "temporal": policy.temporal_policy,
                "version": "1",
            }
        )
        != POLICY_FINGERPRINT
    ):
        raise BehavioralSelfModelPolicyMismatchError()
    return POLICY_FINGERPRINT


def validate_behavioral_observation(observation: object) -> BehavioralObservationV1:
    """Validate one exact emitted observation DTO."""

    if type(observation) is not BehavioralObservationV1:
        raise ValueError("behavioral observation is invalid")
    return observation


def validate_behavioral_observation_result(
    result: object,
) -> BehavioralObservationBuildResultV1:
    """Validate one complete bounded Stage 10A result DTO."""

    if type(result) is not BehavioralObservationBuildResultV1:
        raise ValueError("behavioral observation result is invalid")
    return result


def _read_clock(clock: BehavioralObservationClock) -> datetime:
    if not callable(clock):
        raise BehavioralSelfModelInvalidRequestError()
    try:
        value = clock()
    except Exception:
        raise BehavioralSelfModelInvalidRequestError() from None
    if not _is_aware(value):
        raise BehavioralSelfModelInvalidRequestError()
    return value.astimezone(UTC)


def _read_report(reader: VaultReader) -> ScanReport:
    """Read exactly once through the canonical scan/report boundary."""

    try:
        snapshot = reader.scan()
        if type(snapshot) is not VaultSnapshot:
            raise BehavioralSelfModelSourceUnavailableError()
        report = build_report(snapshot)
    except BehavioralSelfModelError:
        raise
    except Exception:
        raise BehavioralSelfModelSourceUnavailableError() from None
    if type(report) is not ScanReport or type(report.manifest) is not VaultManifest:
        raise BehavioralSelfModelSourceUnavailableError()
    for diagnostic in report.diagnostics:
        if (
            diagnostic.code in _SOURCE_COMPLETENESS_CODES and diagnostic_affects_content(diagnostic)
        ) or (
            diagnostic.code.startswith("MANIFEST_")
            and diagnostic.severity is DiagnosticSeverity.ERROR
        ):
            raise BehavioralSelfModelSourceUnavailableError()
    _validate_report_integrity(report)
    return report


def _validate_report_integrity(report: ScanReport) -> None:
    """Reject incomplete enrolled Stage 2 source without exposing diagnostics."""

    for diagnostic in report.diagnostics:
        if diagnostic.code == "NOTE_FRONT_MATTER_ERROR":
            if _diagnostic_targets_enrolled_note(report, diagnostic.path):
                raise BehavioralSelfModelInvalidJournalError()
            if diagnostic_affects_content(diagnostic):
                raise BehavioralSelfModelSourceUnavailableError()
        if diagnostic.code in _JOURNAL_INTEGRITY_CODES:
            raise BehavioralSelfModelInvalidJournalError()
        if diagnostic.code.startswith(_RELATION_DIAGNOSTIC_PREFIXES) and (
            _diagnostic_targets_enrolled_note(report, diagnostic.path)
        ):
            raise BehavioralSelfModelInvalidJournalError()
    for note in report.notes:
        if not is_personal_memory_enrolled(note.front_matter):
            continue
        try:
            metadata, issues = validate_canonical_personal_memory_fields(note.front_matter)
        except Exception:
            raise BehavioralSelfModelInvalidJournalError() from None
        if issues or metadata is None or metadata != note.personal_memory:
            raise BehavioralSelfModelInvalidJournalError()
        if not _has_valid_storage_identity(note) or not _is_safe_relative_path(note.relative_path):
            raise BehavioralSelfModelInvalidJournalError()
        try:
            if metadata.evidence_kind is EvidenceKind.OBSERVED_DECISION:
                if type(note.decision_journal) is not DecisionJournalRecord:
                    raise BehavioralSelfModelInvalidJournalError()
                parsed = parse_decision_journal_body(note.body)
                if parsed != note.decision_journal:
                    raise BehavioralSelfModelInvalidJournalError()
            elif metadata.evidence_kind is EvidenceKind.OUTCOME_LATER_OBSERVATION:
                if (
                    metadata.decision_id is None
                    or type(note.outcome_observation) is not OutcomeObservationRecord
                    or note.outcome_observation.decision_id != metadata.decision_id
                ):
                    raise BehavioralSelfModelInvalidJournalError()
                parsed_outcome = parse_outcome_observation_body(note.body, metadata.decision_id)
                if parsed_outcome != note.outcome_observation:
                    raise BehavioralSelfModelInvalidJournalError()
        except BehavioralSelfModelError:
            raise
        except DecisionJournalBodyError, ValueError, TypeError, UnicodeError:
            raise BehavioralSelfModelInvalidJournalError() from None


def _collect_current_stage2_records(
    report: ScanReport,
) -> tuple[tuple[_ValidatedJournal, ...], frozenset[UUID]]:
    """Return valid current Journals and exact current linked Outcome IDs."""

    journals: list[_ValidatedJournal] = []
    outcome_ids: set[UUID] = set()
    journal_ids: set[UUID] = set()
    for note in report.notes:
        if not is_personal_memory_enrolled(note.front_matter):
            continue
        metadata = note.personal_memory
        if metadata is None:
            raise BehavioralSelfModelInvalidJournalError()
        if metadata.evidence_kind is EvidenceKind.OBSERVED_DECISION:
            if (
                metadata.self_kind is not SelfKind.DECISION
                or note.note_id is None
                or note.decision_journal is None
            ):
                raise BehavioralSelfModelInvalidJournalError()
            if note.note_id in journal_ids:
                raise BehavioralSelfModelInvalidJournalError()
            journal_ids.add(note.note_id)
            journals.append(_ValidatedJournal(note, metadata, note.decision_journal))
        elif metadata.evidence_kind is EvidenceKind.OUTCOME_LATER_OBSERVATION:
            if metadata.decision_id is None or note.outcome_observation is None:
                raise BehavioralSelfModelInvalidJournalError()
            outcome_ids.add(metadata.decision_id)

    valid_journal_ids = frozenset(journal_ids)
    if not outcome_ids.issubset(valid_journal_ids):
        raise BehavioralSelfModelInvalidJournalError()
    return tuple(sorted(journals, key=_journal_sort_key)), frozenset(outcome_ids)


def _build_observation(
    item: _ValidatedJournal,
    *,
    generated_at: datetime,
    outcome_presence: BehavioralOutcomePresenceV1,
) -> tuple[BehavioralObservationV1 | None, BehavioralTemporalWindowV1 | str | None]:
    metadata = item.metadata
    if metadata.domain is None:
        return None, "missing_domain"
    if metadata.evidence_at == "unknown":
        return None, BehavioralTemporalWindowV1.UNKNOWN
    if not _is_aware(metadata.evidence_at):
        return None, BehavioralTemporalWindowV1.INVALID
    window = classify_behavioral_window(metadata.evidence_at, generated_at)
    if window in {
        BehavioralTemporalWindowV1.FUTURE,
        BehavioralTemporalWindowV1.INVALID,
    }:
        return None, window
    key = _make_cohort_key(
        metadata.domain,
        item.journal.situation,
        item.journal.information_known_at_decision_time,
        item.journal.available_options,
        item.journal.criteria,
    )
    cohort = _cohort_identity(key)
    normalized_options = key.options
    normalized_chosen = normalize_behavioral_text(item.journal.chosen_option)
    matching_indexes = tuple(
        index for index, option in enumerate(normalized_options) if option == normalized_chosen
    )
    if len(matching_indexes) != 1:
        raise ValueError("chosen option identity is invalid")
    chosen_index = matching_indexes[0]
    option_namespace = BehavioralOptionNamespaceV1(
        option_count=len(normalized_options),
        ordered_option_fingerprints=tuple(_hash_text(option) for option in normalized_options),
    )
    chosen_option = BehavioralOptionIdentityV1(
        option_index=chosen_index,
        option_fingerprint=option_namespace.ordered_option_fingerprints[chosen_index],
    )
    criteria = BehavioralCriteriaRepresentationV1(
        criteria_count=len(key.criteria),
        ordered_item_fingerprints=tuple(_hash_text(value) for value in key.criteria),
    )
    assert item.note.note_id is not None
    snapshot = journal_snapshot_fingerprint(
        cohort=cohort,
        normalized_situation=key.situation,
        normalized_information=key.information,
        normalized_options=key.options,
        normalized_criteria=key.criteria,
        chosen_option=chosen_option,
        outcome_presence=outcome_presence,
    )
    observation = BehavioralObservationV1(
        contract_version=CONTRACT_VERSION,
        derivation_version=DERIVATION_VERSION,
        source_journal_uuid=item.note.note_id,
        evidence_at=metadata.evidence_at.astimezone(UTC),
        evidence_at_precision=EvidenceAtPrecision.EXACT.value,
        cohort=cohort,
        option_namespace=option_namespace,
        criteria=criteria,
        chosen_option=chosen_option,
        outcome_presence=outcome_presence,
        journal_snapshot_fingerprint=snapshot,
    )
    return observation, window if window is BehavioralTemporalWindowV1.OUTSIDE_HORIZON else None


def _make_cohort_key(
    domain: str,
    situation: str,
    information: str,
    options: Iterable[str],
    criteria: Iterable[str],
) -> _CohortKey:
    if not _valid_domain(domain, allow_none=False):
        raise ValueError("behavioral domain is invalid")
    normalized_options = tuple(normalize_behavioral_text(value) for value in options)
    normalized_criteria = tuple(normalize_behavioral_text(value) for value in criteria)
    if not 2 <= len(normalized_options) <= 20 or not 1 <= len(normalized_criteria) <= 20:
        raise ValueError("behavioral cohort key is invalid")
    if len(set(normalized_options)) != len(normalized_options):
        raise ValueError("behavioral cohort key is invalid")
    if len(set(normalized_criteria)) != len(normalized_criteria):
        raise ValueError("behavioral cohort key is invalid")
    return _CohortKey(
        domain=domain,
        situation=normalize_behavioral_text(situation),
        information=normalize_behavioral_text(information),
        options=normalized_options,
        criteria=normalized_criteria,
    )


def _cohort_identity(key: _CohortKey) -> BehavioralCohortIdentityV1:
    option_namespace = _hash_json(list(key.options))
    criteria = _hash_json(list(key.criteria))
    cohort_payload = {
        "basis": key.grouping_policy,
        "criteria": list(key.criteria),
        "domain": key.domain,
        "information": key.information,
        "options": list(key.options),
        "situation": key.situation,
    }
    return BehavioralCohortIdentityV1(
        grouping_policy=key.grouping_policy,
        domain=key.domain,
        situation_fingerprint=_hash_text(key.situation),
        information_fingerprint=_hash_text(key.information),
        option_namespace_fingerprint=option_namespace,
        criteria_fingerprint=criteria,
        cohort_fingerprint=_hash_json(cohort_payload),
    )


def _diagnostic_targets_enrolled_note(report: ScanReport, path: str | None) -> bool:
    if path is None:
        return True
    return any(
        note.relative_path == path and is_personal_memory_enrolled(note.front_matter)
        for note in report.notes
    )


def _has_valid_storage_identity(note: NoteRecord) -> bool:
    return (
        note.managed is True
        and type(note.note_id) is UUID
        and note.note_id.version == 7
        and type(note.note_type) is NoteType
        and _is_aware(note.created)
        and (note.updated is None or _is_aware(note.updated))
    )


def _is_safe_relative_path(value: object) -> bool:
    if type(value) is not str or not value or "\\" in value:
        return False
    path = PurePosixPath(value)
    return not (
        path.is_absolute()
        or path.as_posix() != value
        or any(part in _SAFE_RELATIVE_PATH_PARTS for part in path.parts)
    )


def _valid_domain(value: object, *, allow_none: bool) -> bool:
    if value is None:
        return allow_none
    if type(value) is not str:
        return False
    try:
        encoded = value.encode("ascii")
    except UnicodeEncodeError:
        return False
    return len(encoded) <= MAX_DOMAIN_BYTES and bool(_DOMAIN_PATTERN.fullmatch(value))


def _normalize_outcome_presence(
    value: BehavioralOutcomePresenceV1 | str,
) -> BehavioralOutcomePresenceV1:
    if type(value) is BehavioralOutcomePresenceV1:
        return value
    if type(value) is str:
        try:
            return BehavioralOutcomePresenceV1(value)
        except ValueError:
            pass
    raise ValueError("behavioral outcome presence is invalid")


def _hash_text(value: str) -> BehavioralHashV1:
    return "sha256:" + hashlib.sha256(value.encode("utf-8")).hexdigest()


def _hash_json(value: object) -> BehavioralHashV1:
    return "sha256:" + hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _canonical_json(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def _format_utc(value: datetime) -> str:
    normalized = value.astimezone(UTC)
    rendered = normalized.isoformat(
        timespec="microseconds" if normalized.microsecond else "seconds"
    )
    return rendered.removesuffix("+00:00") + "Z"


def _is_canonical_utc(value: object) -> bool:
    return _is_aware(value) and cast(datetime, value).tzinfo is UTC


def _is_aware(value: object) -> bool:
    return (
        isinstance(value, datetime) and value.tzinfo is not None and value.utcoffset() is not None
    )


def _valid_limit(value: object) -> bool:
    return type(value) is int and 1 <= value <= MAX_BEHAVIORAL_OBSERVATIONS


def _journal_sort_key(item: _ValidatedJournal) -> tuple[str, str]:
    assert item.note.note_id is not None
    return str(item.note.note_id), item.note.relative_path


def _normalize_error_code(code: BehavioralSelfModelErrorCode | str) -> BehavioralSelfModelErrorCode:
    if type(code) is BehavioralSelfModelErrorCode:
        return code
    if type(code) is str:
        try:
            return BehavioralSelfModelErrorCode(code)
        except ValueError:
            pass
    raise ValueError("behavioral error code is invalid")


__all__ = [
    "ACTIVE_HORIZON_DAYS",
    "COMPOSITION_POLICY",
    "CONTRACT_VERSION",
    "DEFAULT_BEHAVIORAL_OBSERVATION_POLICY",
    "DEFAULT_BEHAVIORAL_OBSERVATION_REQUEST",
    "DERIVATION_VERSION",
    "FULL_DERIVATION_VERSION",
    "GROUPING_POLICY",
    "MAX_BEHAVIORAL_COHORTS",
    "MAX_BEHAVIORAL_OBSERVATIONS",
    "MAX_BEHAVIORAL_RESULT_BYTES",
    "MINIMUM_COMPARABLE_OBSERVATIONS",
    "MINIMUM_OBSERVATIONS_PER_CHANGE_WINDOW",
    "OBSERVATION_VERSION",
    "OUTCOME_POLICY",
    "POLICY_CANONICAL_JSON",
    "POLICY_FINGERPRINT",
    "POLICY_ID",
    "SUPPORT_POLICY",
    "TEMPORAL_POLICY",
    "TEMPORAL_WINDOW_DAYS",
    "BehavioralCohortBucketV1",
    "BehavioralCohortIdentityV1",
    "BehavioralCriteriaRepresentationV1",
    "BehavioralHashV1",
    "BehavioralObservationBuildResultV1",
    "BehavioralObservationPolicy",
    "BehavioralObservationRequest",
    "BehavioralObservationResultV1",
    "BehavioralObservationV1",
    "BehavioralOptionCountV1",
    "BehavioralOptionIdentityV1",
    "BehavioralOptionNamespaceV1",
    "BehavioralOutcomePresenceV1",
    "BehavioralSelfModelAmbiguousGroupingError",
    "BehavioralSelfModelError",
    "BehavioralSelfModelErrorCode",
    "BehavioralSelfModelInsufficientComparableDecisionsError",
    "BehavioralSelfModelInvalidJournalError",
    "BehavioralSelfModelInvalidRequestError",
    "BehavioralSelfModelPolicyMismatchError",
    "BehavioralSelfModelResultTooLargeError",
    "BehavioralSelfModelSourceUnavailableError",
    "BehavioralSelfModelUnsupportedSemanticComparisonError",
    "BehavioralTemporalWindowV1",
    "BehavioralWindowCountsV1",
    "BuildBehavioralObservation",
    "BuildBehavioralObservations",
    "behavioral_cohort_fingerprint",
    "behavioral_criteria_fingerprint",
    "behavioral_option_namespace_fingerprint",
    "behavioral_text_fingerprint",
    "behavioral_window_membership",
    "classify_behavioral_window",
    "classify_temporal_window",
    "journal_snapshot_fingerprint",
    "normalize_behavioral_text",
    "normalize_behavioral_value",
    "validate_behavioral_hash",
    "validate_behavioral_observation",
    "validate_behavioral_observation_policy",
    "validate_behavioral_observation_request",
    "validate_behavioral_observation_result",
]
