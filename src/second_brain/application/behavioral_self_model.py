"""Deterministic Stage 10B Behavioral Self Model read model.

The module consumes only the current Stage 10A observation/cohort projection.
It is a bounded, immutable, provider-free read model: every call rebuilds from
the current canonical vault and no result is persisted or written back.
"""

from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from types import MappingProxyType
from typing import Final, cast
from uuid import UUID

from second_brain.application.behavioral_observation import (
    ACTIVE_HORIZON_DAYS,
    COMPOSITION_POLICY,
    CONTRACT_VERSION,
    DEFAULT_BEHAVIORAL_OBSERVATION_POLICY,
    DERIVATION_VERSION,
    FULL_DERIVATION_VERSION,
    GROUPING_POLICY,
    MAX_BEHAVIORAL_RESULT_BYTES,
    MINIMUM_COMPARABLE_OBSERVATIONS,
    MINIMUM_OBSERVATIONS_PER_CHANGE_WINDOW,
    OBSERVATION_VERSION,
    OUTCOME_POLICY,
    POLICY_FINGERPRINT,
    POLICY_ID,
    SUPPORT_POLICY,
    TEMPORAL_POLICY,
    BehavioralCohortBucketV1,
    BehavioralCohortIdentityV1,
    BehavioralObservationBuildResultV1,
    BehavioralObservationPolicy,
    BehavioralObservationV1,
    BehavioralOptionIdentityV1,
    BehavioralOutcomePresenceV1,
    BehavioralSelfModelAmbiguousGroupingError,
    BehavioralSelfModelError,
    BehavioralSelfModelErrorCode,
    BehavioralSelfModelInsufficientComparableDecisionsError,
    BehavioralSelfModelInvalidJournalError,
    BehavioralSelfModelInvalidRequestError,
    BehavioralSelfModelPolicyMismatchError,
    BehavioralSelfModelResultTooLargeError,
    BehavioralSelfModelSourceUnavailableError,
    BehavioralSelfModelUnsupportedSemanticComparisonError,
    BehavioralTemporalWindowV1,
    BuildBehavioralObservations,
    validate_behavioral_hash,
    validate_behavioral_observation_policy,
    validate_behavioral_observation_result,
)
from second_brain.application.ports import VaultReader

type BehavioralSelfModelClock = Callable[[], datetime]
type BehavioralOutcomePresenceCounts = Mapping[str, int]

MAX_BEHAVIORAL_PATTERNS: Final[int] = 200
MAX_BEHAVIORAL_PROVENANCE_UUIDS: Final[int] = 200


class BehavioralPatternTypeV1(StrEnum):
    """The closed Stage 10B pattern set."""

    REPEATED_EXACT_CHOICE = "repeated_exact_choice"
    MIXED_EXACT_CHOICES = "mixed_exact_choices"
    STABLE_OVER_TIME = "stable_over_time"
    CHANGED_OVER_TIME = "changed_over_time"
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"
    NOT_COMPARABLE = "not_comparable"


class BehavioralPatternStateV1(StrEnum):
    """The only states allowed for the corresponding closed patterns."""

    CURRENT = "current"
    HISTORICAL = "historical"
    MIXED = "mixed"
    STABLE = "stable"
    CHANGED = "changed"
    INSUFFICIENT = "insufficient"
    NOT_COMPARABLE = "not_comparable"


class BehavioralCaveatCodeV1(StrEnum):
    """Fixed, non-interpretive caveat vocabulary."""

    SUPPORT_IS_DESCRIPTIVE = "support_is_descriptive"
    CURRENT_VAULT_REBUILD = "current_vault_rebuild"
    UNKNOWN_TIME_EXCLUDED = "unknown_time_excluded"
    FUTURE_OR_INVALID_TIME_EXCLUDED = "future_or_invalid_time_excluded"
    OUTSIDE_HORIZON_EXCLUDED = "outside_horizon_excluded"
    OUTCOME_PRESENCE_ONLY = "outcome_presence_only"
    MIXED_NO_WINNER = "mixed_no_winner"
    TEMPORAL_STATE_IS_COHORT_LOCAL = "temporal_state_is_cohort_local"
    STATED_OBSERVED_MAPPING_MISSING = "stated_observed_mapping_missing"
    INSUFFICIENT_COMPARABLE_EVIDENCE = "insufficient_comparable_evidence"
    NOT_COMPARABLE_UNDER_V1 = "not_comparable_under_v1"


# Short aliases make the closed supporting enums usable without creating a
# second set of values or changing the emitted DTO shape.
BehavioralPatternType = BehavioralPatternTypeV1
BehavioralPatternState = BehavioralPatternStateV1
BehavioralCaveatCode = BehavioralCaveatCodeV1

BehavioralSelfModelPolicy = BehavioralObservationPolicy
DEFAULT_BEHAVIORAL_SELF_MODEL_POLICY: Final[BehavioralSelfModelPolicy] = (
    DEFAULT_BEHAVIORAL_OBSERVATION_POLICY
)


@dataclass(frozen=True, slots=True)
class BehavioralSelfModelRequest:
    """Bounded in-memory request for one current-vault rebuild."""

    max_patterns: int = MAX_BEHAVIORAL_PATTERNS


DEFAULT_BEHAVIORAL_SELF_MODEL_REQUEST: Final[BehavioralSelfModelRequest] = (
    BehavioralSelfModelRequest()
)


@dataclass(frozen=True, slots=True)
class BehavioralRatioV1:
    """Exact integer numerator/denominator pair."""

    numerator: int
    denominator: int

    def __post_init__(self) -> None:
        if (
            type(self.numerator) is not int
            or type(self.denominator) is not int
            or self.numerator < 0
            or self.denominator <= 0
            or self.numerator > self.denominator
        ):
            raise ValueError("behavioral ratio is invalid")

    def as_dict(self) -> dict[str, int]:
        """Return the exact integer ratio."""

        return {"numerator": self.numerator, "denominator": self.denominator}


@dataclass(frozen=True, slots=True)
class BehavioralChoiceSupportV1:
    """Exact support count for one option identity."""

    option: BehavioralOptionIdentityV1
    support_count: int
    support_ratio: BehavioralRatioV1 | None

    def __post_init__(self) -> None:
        if type(self.option) is not BehavioralOptionIdentityV1:
            raise ValueError("behavioral choice support is invalid")
        if type(self.support_count) is not int or self.support_count < 0:
            raise ValueError("behavioral choice support is invalid")
        if self.support_ratio is not None:
            if type(self.support_ratio) is not BehavioralRatioV1:
                raise ValueError("behavioral choice support is invalid")
            if (
                self.support_ratio.numerator != self.support_count
                or self.support_count > self.support_ratio.denominator
            ):
                raise ValueError("behavioral choice support is invalid")
        elif self.support_count != 0:
            raise ValueError("behavioral choice support is invalid")

    def as_dict(self) -> dict[str, object]:
        """Return only exact option identity and integer support."""

        return {
            "option": self.option.as_dict(),
            "support_count": self.support_count,
            "support_ratio": self.support_ratio.as_dict() if self.support_ratio else None,
        }


@dataclass(frozen=True, slots=True)
class BehavioralTemporalSpanV1:
    """Earliest and latest exact evidence times in the active population."""

    earliest_evidence_at: datetime | None
    latest_evidence_at: datetime | None

    def __post_init__(self) -> None:
        for value in (self.earliest_evidence_at, self.latest_evidence_at):
            if value is not None and not _is_canonical_utc(value):
                raise ValueError("behavioral temporal span is invalid")
        if (self.earliest_evidence_at is None) != (self.latest_evidence_at is None):
            raise ValueError("behavioral temporal span is invalid")
        if (
            self.earliest_evidence_at is not None
            and self.latest_evidence_at is not None
            and self.earliest_evidence_at > self.latest_evidence_at
        ):
            raise ValueError("behavioral temporal span is invalid")

    def as_dict(self) -> dict[str, str | None]:
        """Return canonical UTC timestamps or null for an empty population."""

        return {
            "earliest_evidence_at": (
                _format_utc(self.earliest_evidence_at)
                if self.earliest_evidence_at is not None
                else None
            ),
            "latest_evidence_at": (
                _format_utc(self.latest_evidence_at)
                if self.latest_evidence_at is not None
                else None
            ),
        }


@dataclass(frozen=True, slots=True)
class BehavioralWindowSummaryV1:
    """Exact count/support summary for one bounded temporal window."""

    window: BehavioralTemporalWindowV1 | str
    observation_count: int
    choice_support: tuple[BehavioralChoiceSupportV1, ...]

    def __post_init__(self) -> None:
        window = _normalize_window(self.window)
        object.__setattr__(self, "window", window)
        if type(self.observation_count) is not int or self.observation_count < 0:
            raise ValueError("behavioral window summary is invalid")
        if type(self.choice_support) is not tuple or any(
            type(item) is not BehavioralChoiceSupportV1 for item in self.choice_support
        ):
            raise ValueError("behavioral window summary is invalid")
        indexes = tuple(item.option.option_index for item in self.choice_support)
        if indexes != tuple(sorted(indexes)) or len(indexes) != len(set(indexes)):
            raise ValueError("behavioral window summary is invalid")
        if sum(item.support_count for item in self.choice_support) != self.observation_count:
            raise ValueError("behavioral window summary is invalid")
        if self.observation_count == 0:
            if self.choice_support:
                raise ValueError("behavioral window summary is invalid")
        else:
            for item in self.choice_support:
                ratio = item.support_ratio
                if (
                    ratio is None
                    or ratio.denominator != self.observation_count
                    or ratio.numerator != item.support_count
                ):
                    raise ValueError("behavioral window summary is invalid")

    def as_dict(self) -> dict[str, object]:
        """Return the exact window count projection."""

        return {
            "window": cast(BehavioralTemporalWindowV1, self.window).value,
            "observation_count": self.observation_count,
            "choice_support": [item.as_dict() for item in self.choice_support],
        }


@dataclass(frozen=True, slots=True)
class BehavioralProvenanceV1:
    """Bounded source Journal UUID provenance for one pattern."""

    source_journal_uuids: tuple[UUID, ...]
    source_count: int
    provenance_fingerprint: str

    def __post_init__(self) -> None:
        if type(self.source_journal_uuids) is not tuple:
            raise ValueError("behavioral provenance is invalid")
        if len(self.source_journal_uuids) > MAX_BEHAVIORAL_PROVENANCE_UUIDS:
            raise ValueError("behavioral provenance is invalid")
        if any(
            type(value) is not UUID or value.version != 7 for value in self.source_journal_uuids
        ):
            raise ValueError("behavioral provenance is invalid")
        if self.source_journal_uuids != tuple(sorted(self.source_journal_uuids, key=str)):
            raise ValueError("behavioral provenance is invalid")
        if len(set(self.source_journal_uuids)) != len(self.source_journal_uuids):
            raise ValueError("behavioral provenance is invalid")
        if type(self.source_count) is not int or self.source_count != len(
            self.source_journal_uuids
        ):
            raise ValueError("behavioral provenance is invalid")
        validate_behavioral_hash(self.provenance_fingerprint)
        if self.provenance_fingerprint != behavioral_provenance_fingerprint(
            self.source_journal_uuids
        ):
            raise ValueError("behavioral provenance is invalid")

    def as_dict(self) -> dict[str, object]:
        """Return UUID-only bounded provenance."""

        return {
            "source_journal_uuids": [str(value) for value in self.source_journal_uuids],
            "source_count": self.source_count,
            "provenance_fingerprint": self.provenance_fingerprint,
        }


@dataclass(frozen=True, slots=True)
class BehavioralPatternV1:
    """One complete exact pattern for one proven cohort."""

    contract_version: str
    derivation_version: str
    policy_id: str
    policy_fingerprint: str
    cohort: BehavioralCohortIdentityV1 | None
    pattern_type: BehavioralPatternTypeV1 | str
    state: BehavioralPatternStateV1 | str
    selected_option: BehavioralOptionIdentityV1 | None
    support_count: int | None
    total_comparable_observations: int
    support_ratio: BehavioralRatioV1 | None
    choice_support: tuple[BehavioralChoiceSupportV1, ...]
    temporal_span: BehavioralTemporalSpanV1
    windows: tuple[BehavioralWindowSummaryV1, ...]
    outcome_presence: BehavioralOutcomePresenceCounts
    provenance: BehavioralProvenanceV1
    caveats: tuple[BehavioralCaveatCodeV1 | str, ...]

    def __post_init__(self) -> None:
        if self.contract_version != CONTRACT_VERSION or type(self.contract_version) is not str:
            raise ValueError("behavioral pattern is invalid")
        if (
            self.derivation_version != FULL_DERIVATION_VERSION
            or type(self.derivation_version) is not str
        ):
            raise ValueError("behavioral pattern is invalid")
        if self.policy_id != POLICY_ID or type(self.policy_id) is not str:
            raise ValueError("behavioral pattern is invalid")
        validate_behavioral_hash(self.policy_fingerprint)
        if self.policy_fingerprint != POLICY_FINGERPRINT:
            raise ValueError("behavioral pattern is invalid")
        if self.cohort is not None and type(self.cohort) is not BehavioralCohortIdentityV1:
            raise ValueError("behavioral pattern is invalid")
        pattern_type = _normalize_pattern_type(self.pattern_type)
        state = _normalize_pattern_state(self.state)
        object.__setattr__(self, "pattern_type", pattern_type)
        object.__setattr__(self, "state", state)
        if state not in _EXPECTED_PATTERN_STATES[pattern_type]:
            raise ValueError("behavioral pattern is invalid")
        if (
            self.selected_option is not None
            and type(self.selected_option) is not BehavioralOptionIdentityV1
        ):
            raise ValueError("behavioral pattern is invalid")
        if self.support_count is not None and (
            type(self.support_count) is not int or self.support_count < 0
        ):
            raise ValueError("behavioral pattern is invalid")
        if (
            type(self.total_comparable_observations) is not int
            or self.total_comparable_observations < 0
        ):
            raise ValueError("behavioral pattern is invalid")
        if (
            self.support_count is not None
            and self.support_count > self.total_comparable_observations
        ):
            raise ValueError("behavioral pattern is invalid")
        if self.support_ratio is not None:
            if type(self.support_ratio) is not BehavioralRatioV1:
                raise ValueError("behavioral pattern is invalid")
            if self.support_count is None:
                raise ValueError("behavioral pattern is invalid")
            if (
                self.support_ratio.numerator != self.support_count
                or self.support_ratio.denominator != self.total_comparable_observations
            ):
                raise ValueError("behavioral pattern is invalid")
        elif self.support_count is not None or self.total_comparable_observations != 0:
            if pattern_type in {
                BehavioralPatternTypeV1.REPEATED_EXACT_CHOICE,
                BehavioralPatternTypeV1.STABLE_OVER_TIME,
            }:
                raise ValueError("behavioral pattern is invalid")
        if type(self.choice_support) is not tuple or any(
            type(item) is not BehavioralChoiceSupportV1 for item in self.choice_support
        ):
            raise ValueError("behavioral pattern is invalid")
        choice_keys = tuple(
            (item.option.option_index, item.option.option_fingerprint)
            for item in self.choice_support
        )
        if tuple(item.option.option_index for item in self.choice_support) != tuple(
            sorted(item.option.option_index for item in self.choice_support)
        ) or len(choice_keys) != len(set(choice_keys)):
            raise ValueError("behavioral pattern is invalid")
        if (
            sum(item.support_count for item in self.choice_support)
            != self.total_comparable_observations
        ):
            raise ValueError("behavioral pattern is invalid")
        if self.total_comparable_observations == 0 and self.choice_support:
            raise ValueError("behavioral pattern is invalid")
        if self.total_comparable_observations > 0:
            for item in self.choice_support:
                ratio = item.support_ratio
                if (
                    ratio is None
                    or ratio.numerator != item.support_count
                    or ratio.denominator != self.total_comparable_observations
                ):
                    raise ValueError("behavioral pattern is invalid")
        if type(self.temporal_span) is not BehavioralTemporalSpanV1:
            raise ValueError("behavioral pattern is invalid")
        if (
            type(self.windows) is not tuple
            or len(self.windows) != 2
            or any(type(item) is not BehavioralWindowSummaryV1 for item in self.windows)
        ):
            raise ValueError("behavioral pattern is invalid")
        if tuple(item.window for item in self.windows) != (
            BehavioralTemporalWindowV1.HISTORICAL,
            BehavioralTemporalWindowV1.CURRENT,
        ):
            raise ValueError("behavioral pattern is invalid")
        if (
            sum(item.observation_count for item in self.windows)
            != self.total_comparable_observations
        ):
            raise ValueError("behavioral pattern is invalid")
        outcome_presence = _normalize_outcome_presence_counts(self.outcome_presence)
        object.__setattr__(self, "outcome_presence", outcome_presence)
        if sum(outcome_presence.values()) != self.total_comparable_observations:
            raise ValueError("behavioral pattern is invalid")
        if type(self.provenance) is not BehavioralProvenanceV1:
            raise ValueError("behavioral pattern is invalid")
        if self.provenance.source_count != self.total_comparable_observations:
            raise ValueError("behavioral pattern is invalid")
        caveats = _normalize_caveats(self.caveats)
        object.__setattr__(self, "caveats", caveats)

        if pattern_type is BehavioralPatternTypeV1.NOT_COMPARABLE:
            if (
                self.cohort is not None
                or self.total_comparable_observations != 0
                or self.selected_option is not None
                or self.support_count is not None
                or self.support_ratio is not None
                or self.choice_support
                or self.temporal_span.earliest_evidence_at is not None
                or self.provenance.source_count != 0
            ):
                raise ValueError("behavioral pattern is invalid")
            return

        if self.cohort is None:
            raise ValueError("behavioral pattern is invalid")
        if pattern_type is BehavioralPatternTypeV1.INSUFFICIENT_EVIDENCE:
            if self.total_comparable_observations > MINIMUM_COMPARABLE_OBSERVATIONS - 1:
                raise ValueError("behavioral pattern is invalid")
            if self.selected_option is not None or self.support_count is not None:
                raise ValueError("behavioral pattern is invalid")
            return
        if self.total_comparable_observations < MINIMUM_COMPARABLE_OBSERVATIONS:
            raise ValueError("behavioral pattern is invalid")
        if pattern_type in {
            BehavioralPatternTypeV1.MIXED_EXACT_CHOICES,
            BehavioralPatternTypeV1.CHANGED_OVER_TIME,
        }:
            if (
                self.selected_option is not None
                or self.support_count is not None
                or self.support_ratio is not None
            ):
                raise ValueError("behavioral pattern is invalid")
        else:
            if (
                self.selected_option is None
                or self.support_count != self.total_comparable_observations
            ):
                raise ValueError("behavioral pattern is invalid")
            if self.support_ratio is None or self.support_ratio != BehavioralRatioV1(
                self.total_comparable_observations, self.total_comparable_observations
            ):
                raise ValueError("behavioral pattern is invalid")
            if (
                len(self.choice_support) != 1
                or self.choice_support[0].option != self.selected_option
            ):
                raise ValueError("behavioral pattern is invalid")
        historical, current = self.windows
        historical_choice = _unanimous_choice_key(historical.choice_support)
        current_choice = _unanimous_choice_key(current.choice_support)
        if pattern_type is BehavioralPatternTypeV1.REPEATED_EXACT_CHOICE:
            if (historical.observation_count == 0) == (current.observation_count == 0):
                raise ValueError("behavioral pattern is invalid")
            if historical.observation_count and historical_choice is None:
                raise ValueError("behavioral pattern is invalid")
            if current.observation_count and current_choice is None:
                raise ValueError("behavioral pattern is invalid")
        elif pattern_type is BehavioralPatternTypeV1.STABLE_OVER_TIME:
            if not historical.observation_count or not current.observation_count:
                raise ValueError("behavioral pattern is invalid")
            if historical_choice is None or historical_choice != current_choice:
                raise ValueError("behavioral pattern is invalid")
        elif pattern_type is BehavioralPatternTypeV1.CHANGED_OVER_TIME:
            if (
                historical.observation_count < MINIMUM_OBSERVATIONS_PER_CHANGE_WINDOW
                or current.observation_count < MINIMUM_OBSERVATIONS_PER_CHANGE_WINDOW
                or historical_choice is None
                or current_choice is None
                or historical_choice == current_choice
            ):
                raise ValueError("behavioral pattern is invalid")
        elif pattern_type is BehavioralPatternTypeV1.MIXED_EXACT_CHOICES:
            if len(self.choice_support) < 2:
                raise ValueError("behavioral pattern is invalid")
            if (
                historical.observation_count >= MINIMUM_OBSERVATIONS_PER_CHANGE_WINDOW
                and current.observation_count >= MINIMUM_OBSERVATIONS_PER_CHANGE_WINDOW
                and historical_choice is not None
                and current_choice is not None
                and historical_choice != current_choice
            ):
                raise ValueError("behavioral pattern is invalid")

    def as_dict(self) -> dict[str, object]:
        """Return the exact raw-label-free pattern DTO."""

        return {
            "contract_version": self.contract_version,
            "derivation_version": self.derivation_version,
            "policy_id": self.policy_id,
            "policy_fingerprint": self.policy_fingerprint,
            "cohort": self.cohort.as_dict() if self.cohort is not None else None,
            "pattern_type": cast(BehavioralPatternTypeV1, self.pattern_type).value,
            "state": cast(BehavioralPatternStateV1, self.state).value,
            "selected_option": (
                self.selected_option.as_dict() if self.selected_option is not None else None
            ),
            "support_count": self.support_count,
            "total_comparable_observations": self.total_comparable_observations,
            "support_ratio": self.support_ratio.as_dict() if self.support_ratio else None,
            "choice_support": [item.as_dict() for item in self.choice_support],
            "temporal_span": self.temporal_span.as_dict(),
            "windows": [item.as_dict() for item in self.windows],
            "outcome_presence": dict(self.outcome_presence),
            "provenance": self.provenance.as_dict(),
            "caveats": [cast(BehavioralCaveatCodeV1, item).value for item in self.caveats],
        }


@dataclass(frozen=True, slots=True)
class BehavioralSelfModelResultV1:
    """Complete bounded Stage 10B result for one current-vault rebuild."""

    contract_version: str
    derivation_version: str
    policy_id: str
    policy_fingerprint: str
    generated_at: datetime
    patterns: tuple[BehavioralPatternV1, ...]
    eligible_journal_count: int
    comparable_observation_count: int
    excluded_unknown_time_count: int
    excluded_outside_horizon_count: int
    caveats: tuple[BehavioralCaveatCodeV1 | str, ...]

    def __post_init__(self) -> None:
        if self.contract_version != CONTRACT_VERSION or type(self.contract_version) is not str:
            raise ValueError("behavioral self model result is invalid")
        if (
            self.derivation_version != FULL_DERIVATION_VERSION
            or type(self.derivation_version) is not str
        ):
            raise ValueError("behavioral self model result is invalid")
        if self.policy_id != POLICY_ID or type(self.policy_id) is not str:
            raise ValueError("behavioral self model result is invalid")
        validate_behavioral_hash(self.policy_fingerprint)
        if self.policy_fingerprint != POLICY_FINGERPRINT:
            raise ValueError("behavioral self model result is invalid")
        if not _is_canonical_utc(self.generated_at):
            raise ValueError("behavioral self model result is invalid")
        if type(self.patterns) is not tuple or len(self.patterns) > MAX_BEHAVIORAL_PATTERNS:
            raise ValueError("behavioral self model result is invalid")
        if any(type(item) is not BehavioralPatternV1 for item in self.patterns):
            raise ValueError("behavioral self model result is invalid")
        if self.patterns != tuple(sorted(self.patterns, key=_pattern_sort_key)):
            raise ValueError("behavioral self model result is invalid")
        pattern_keys = tuple(
            item.cohort.cohort_fingerprint if item.cohort is not None else None
            for item in self.patterns
        )
        if len(pattern_keys) != len(set(pattern_keys)):
            raise ValueError("behavioral self model result is invalid")
        for value in (
            self.eligible_journal_count,
            self.comparable_observation_count,
            self.excluded_unknown_time_count,
            self.excluded_outside_horizon_count,
        ):
            if type(value) is not int or value < 0:
                raise ValueError("behavioral self model result is invalid")
        represented = sum(
            item.total_comparable_observations
            for item in self.patterns
            if item.pattern_type is not BehavioralPatternTypeV1.NOT_COMPARABLE
        )
        if represented != self.comparable_observation_count:
            raise ValueError("behavioral self model result is invalid")
        if self.eligible_journal_count < (
            self.comparable_observation_count
            + self.excluded_unknown_time_count
            + self.excluded_outside_horizon_count
        ):
            raise ValueError("behavioral self model result is invalid")
        caveats = _normalize_caveats(self.caveats)
        object.__setattr__(self, "caveats", caveats)

    def as_dict(self) -> dict[str, object]:
        """Return the exact result DTO without raw note content."""

        return {
            "contract_version": self.contract_version,
            "derivation_version": self.derivation_version,
            "policy_id": self.policy_id,
            "policy_fingerprint": self.policy_fingerprint,
            "generated_at": _format_utc(self.generated_at),
            "patterns": [item.as_dict() for item in self.patterns],
            "eligible_journal_count": self.eligible_journal_count,
            "comparable_observation_count": self.comparable_observation_count,
            "excluded_unknown_time_count": self.excluded_unknown_time_count,
            "excluded_outside_horizon_count": self.excluded_outside_horizon_count,
            "caveats": [cast(BehavioralCaveatCodeV1, item).value for item in self.caveats],
        }

    def to_json(self) -> str:
        """Serialize the complete read model deterministically."""

        return _canonical_json(self.as_dict())


@dataclass(frozen=True, slots=True)
class BuildBehavioralSelfModel:
    """Rebuild Stage 10B from the current canonical vault on every call."""

    reader: VaultReader
    policy: BehavioralSelfModelPolicy = DEFAULT_BEHAVIORAL_SELF_MODEL_POLICY
    clock: BehavioralSelfModelClock = lambda: datetime.now(UTC)

    def execute(
        self,
        request: BehavioralSelfModelRequest = DEFAULT_BEHAVIORAL_SELF_MODEL_REQUEST,
    ) -> BehavioralSelfModelResultV1:
        """Return one complete result or one fixed safe error."""

        validate_behavioral_self_model_request(request)
        policy_fingerprint = validate_behavioral_self_model_policy(self.policy)
        generated_at = _read_clock(self.clock)
        try:
            observation_result = BuildBehavioralObservations(
                self.reader,
                policy=self.policy,
                clock=lambda: generated_at,
            ).execute()
            validate_behavioral_observation_result(observation_result)
            _validate_observation_binding(observation_result, policy_fingerprint, generated_at)
            patterns = _build_patterns(observation_result)
            if len(patterns) > request.max_patterns:
                raise BehavioralSelfModelResultTooLargeError()
            result = BehavioralSelfModelResultV1(
                contract_version=CONTRACT_VERSION,
                derivation_version=FULL_DERIVATION_VERSION,
                policy_id=POLICY_ID,
                policy_fingerprint=policy_fingerprint,
                generated_at=generated_at,
                patterns=patterns,
                eligible_journal_count=observation_result.eligible_journal_count,
                comparable_observation_count=sum(
                    item.total_comparable_observations
                    for item in patterns
                    if item.pattern_type is not BehavioralPatternTypeV1.NOT_COMPARABLE
                ),
                excluded_unknown_time_count=observation_result.excluded_unknown_time_count,
                excluded_outside_horizon_count=observation_result.excluded_outside_horizon_count,
                caveats=_result_caveats(observation_result, patterns),
            )
            validate_behavioral_self_model_result(result)
            if len(result.to_json().encode("utf-8")) > MAX_BEHAVIORAL_RESULT_BYTES:
                raise BehavioralSelfModelResultTooLargeError()
            return result
        except BehavioralSelfModelError:
            raise
        except UnicodeError, ValueError, TypeError, OverflowError:
            raise BehavioralSelfModelInvalidJournalError() from None
        except Exception:
            raise BehavioralSelfModelSourceUnavailableError() from None


BuildBehavioralSelfModelResult = BuildBehavioralSelfModel
BuildBehavioralSelfModelV1 = BuildBehavioralSelfModel


def validate_behavioral_self_model_request(
    request: object,
) -> BehavioralSelfModelRequest:
    """Validate bounded request values before reading the vault."""

    if type(request) is not BehavioralSelfModelRequest:
        raise BehavioralSelfModelInvalidRequestError()
    if (
        type(request.max_patterns) is not int
        or not 1 <= request.max_patterns <= MAX_BEHAVIORAL_PATTERNS
    ):
        raise BehavioralSelfModelInvalidRequestError()
    return request


def validate_behavioral_self_model_policy(
    policy: object,
) -> str:
    """Validate and return the fixed Stage 10 policy fingerprint."""

    return validate_behavioral_observation_policy(policy)


def validate_behavioral_self_model_result(
    result: object,
) -> BehavioralSelfModelResultV1:
    """Validate one complete bounded Stage 10B result DTO."""

    if type(result) is not BehavioralSelfModelResultV1:
        raise ValueError("behavioral self model result is invalid")
    if len(result.to_json().encode("utf-8")) > MAX_BEHAVIORAL_RESULT_BYTES:
        raise BehavioralSelfModelResultTooLargeError()
    return result


def behavioral_provenance_fingerprint(
    source_journal_uuids: Iterable[UUID],
) -> str:
    """Build a deterministic fingerprint over sorted source Journal UUIDs."""

    values = tuple(sorted(str(value) for value in source_journal_uuids))
    return _hash_json({"source_journal_uuids": list(values)})


def classify_behavioral_pattern(
    bucket: BehavioralCohortBucketV1,
    *,
    generated_at: datetime,
) -> BehavioralPatternV1:
    """Classify one exact Stage 10A cohort deterministically."""

    if type(bucket) is not BehavioralCohortBucketV1 or not _is_canonical_utc(generated_at):
        raise ValueError("behavioral cohort bucket is invalid")
    return _build_pattern(bucket, generated_at=generated_at)


classify_behavioral_self_model_pattern = classify_behavioral_pattern
build_behavioral_pattern = classify_behavioral_pattern


def _build_patterns(
    observation_result: BehavioralObservationBuildResultV1,
) -> tuple[BehavioralPatternV1, ...]:
    patterns = [
        _build_pattern(bucket, generated_at=observation_result.generated_at)
        for bucket in observation_result.cohorts
    ]
    if (
        not patterns
        and observation_result.eligible_journal_count > 0
        and observation_result.comparable_observation_count == 0
        and (
            observation_result.excluded_unknown_time_count > 0
            or observation_result.excluded_future_or_invalid_time_count > 0
            or observation_result.excluded_missing_domain_count > 0
        )
    ):
        patterns.append(_build_not_comparable_pattern())
    return tuple(sorted(patterns, key=_pattern_sort_key))


def _build_pattern(
    bucket: BehavioralCohortBucketV1,
    *,
    generated_at: datetime,
) -> BehavioralPatternV1:
    historical = bucket.observations_in_window(
        BehavioralTemporalWindowV1.HISTORICAL,
        generated_at=generated_at,
    )
    current = bucket.observations_in_window(
        BehavioralTemporalWindowV1.CURRENT,
        generated_at=generated_at,
    )
    pattern_type, state = _classify_type_and_state(historical, current)
    total = len(historical) + len(current)
    choice_support = _choice_support(historical + current, denominator=total)
    windows = (
        _window_summary(BehavioralTemporalWindowV1.HISTORICAL, historical),
        _window_summary(BehavioralTemporalWindowV1.CURRENT, current),
    )
    selected_option = None
    support_count = None
    support_ratio = None
    if pattern_type in {
        BehavioralPatternTypeV1.REPEATED_EXACT_CHOICE,
        BehavioralPatternTypeV1.STABLE_OVER_TIME,
    }:
        if len(choice_support) != 1:
            raise ValueError("behavioral pattern choice is invalid")
        selected_option = choice_support[0].option
        support_count = total
        support_ratio = BehavioralRatioV1(total, total)
    active = historical + current
    return BehavioralPatternV1(
        contract_version=CONTRACT_VERSION,
        derivation_version=FULL_DERIVATION_VERSION,
        policy_id=POLICY_ID,
        policy_fingerprint=POLICY_FINGERPRINT,
        cohort=bucket.cohort,
        pattern_type=pattern_type,
        state=state,
        selected_option=selected_option,
        support_count=support_count,
        total_comparable_observations=total,
        support_ratio=support_ratio,
        choice_support=choice_support,
        temporal_span=_temporal_span(active),
        windows=windows,
        outcome_presence=_outcome_presence_counts(active),
        provenance=_provenance(active),
        caveats=_pattern_caveats(pattern_type),
    )


def _build_not_comparable_pattern() -> BehavioralPatternV1:
    empty_window = BehavioralWindowSummaryV1(
        window=BehavioralTemporalWindowV1.HISTORICAL,
        observation_count=0,
        choice_support=(),
    )
    current_window = BehavioralWindowSummaryV1(
        window=BehavioralTemporalWindowV1.CURRENT,
        observation_count=0,
        choice_support=(),
    )
    return BehavioralPatternV1(
        contract_version=CONTRACT_VERSION,
        derivation_version=FULL_DERIVATION_VERSION,
        policy_id=POLICY_ID,
        policy_fingerprint=POLICY_FINGERPRINT,
        cohort=None,
        pattern_type=BehavioralPatternTypeV1.NOT_COMPARABLE,
        state=BehavioralPatternStateV1.NOT_COMPARABLE,
        selected_option=None,
        support_count=None,
        total_comparable_observations=0,
        support_ratio=None,
        choice_support=(),
        temporal_span=BehavioralTemporalSpanV1(None, None),
        windows=(empty_window, current_window),
        outcome_presence={"present": 0, "absent": 0},
        provenance=_provenance(()),
        caveats=(BehavioralCaveatCodeV1.NOT_COMPARABLE_UNDER_V1,),
    )


def _classify_type_and_state(
    historical: tuple[BehavioralObservationV1, ...],
    current: tuple[BehavioralObservationV1, ...],
) -> tuple[BehavioralPatternTypeV1, BehavioralPatternStateV1]:
    total = len(historical) + len(current)
    if total < MINIMUM_COMPARABLE_OBSERVATIONS:
        return BehavioralPatternTypeV1.INSUFFICIENT_EVIDENCE, BehavioralPatternStateV1.INSUFFICIENT
    historical_choice = _unanimous_observation_key(historical)
    current_choice = _unanimous_observation_key(current)
    if (
        len(historical) >= MINIMUM_OBSERVATIONS_PER_CHANGE_WINDOW
        and len(current) >= MINIMUM_OBSERVATIONS_PER_CHANGE_WINDOW
        and historical_choice is not None
        and current_choice is not None
        and historical_choice != current_choice
    ):
        return BehavioralPatternTypeV1.CHANGED_OVER_TIME, BehavioralPatternStateV1.CHANGED
    if (
        historical
        and current
        and historical_choice is not None
        and historical_choice == current_choice
    ):
        return BehavioralPatternTypeV1.STABLE_OVER_TIME, BehavioralPatternStateV1.STABLE
    active_keys = {
        (item.chosen_option.option_index, item.chosen_option.option_fingerprint)
        for item in historical + current
    }
    if (not historical or not current) and len(active_keys) == 1:
        state = BehavioralPatternStateV1.CURRENT if current else BehavioralPatternStateV1.HISTORICAL
        return BehavioralPatternTypeV1.REPEATED_EXACT_CHOICE, state
    if len(active_keys) > 1:
        return BehavioralPatternTypeV1.MIXED_EXACT_CHOICES, BehavioralPatternStateV1.MIXED
    raise ValueError("behavioral pattern classification is invalid")


def _choice_support(
    observations: tuple[BehavioralObservationV1, ...],
    *,
    denominator: int,
) -> tuple[BehavioralChoiceSupportV1, ...]:

    options: dict[tuple[int, str], BehavioralOptionIdentityV1] = {}
    counts: defaultdict[tuple[int, str], int] = defaultdict(int)
    for observation in observations:
        option = observation.chosen_option
        key = (option.option_index, option.option_fingerprint)
        existing = options.get(key)
        if existing is not None and existing != option:
            raise ValueError("behavioral choice support is invalid")
        if any(
            item[0] == option.option_index and item[1] != option.option_fingerprint
            for item in options
        ):
            raise ValueError("behavioral choice support is invalid")
        options[key] = option
        counts[key] += 1
    return tuple(
        BehavioralChoiceSupportV1(
            option=options[key],
            support_count=counts[key],
            support_ratio=BehavioralRatioV1(counts[key], denominator) if denominator else None,
        )
        for key in sorted(options, key=lambda item: item[0])
    )


def _window_summary(
    window: BehavioralTemporalWindowV1,
    observations: tuple[BehavioralObservationV1, ...],
) -> BehavioralWindowSummaryV1:
    return BehavioralWindowSummaryV1(
        window=window,
        observation_count=len(observations),
        choice_support=_choice_support(observations, denominator=len(observations)),
    )


def _temporal_span(
    observations: tuple[BehavioralObservationV1, ...],
) -> BehavioralTemporalSpanV1:
    if not observations:
        return BehavioralTemporalSpanV1(None, None)
    times = tuple(item.evidence_at for item in observations)
    return BehavioralTemporalSpanV1(min(times), max(times))


def _outcome_presence_counts(
    observations: tuple[BehavioralObservationV1, ...],
) -> dict[str, int]:
    present = sum(
        cast(BehavioralOutcomePresenceV1, item.outcome_presence).value == "present"
        for item in observations
    )
    return {"present": present, "absent": len(observations) - present}


def _provenance(
    observations: tuple[BehavioralObservationV1, ...],
) -> BehavioralProvenanceV1:
    source_ids = tuple(sorted((item.source_journal_uuid for item in observations), key=str))
    return BehavioralProvenanceV1(
        source_journal_uuids=source_ids,
        source_count=len(source_ids),
        provenance_fingerprint=behavioral_provenance_fingerprint(source_ids),
    )


def _pattern_caveats(
    pattern_type: BehavioralPatternTypeV1,
) -> tuple[BehavioralCaveatCodeV1, ...]:
    values: set[BehavioralCaveatCodeV1] = set()
    if pattern_type is BehavioralPatternTypeV1.NOT_COMPARABLE:
        values.add(BehavioralCaveatCodeV1.NOT_COMPARABLE_UNDER_V1)
    else:
        values.update(
            {
                BehavioralCaveatCodeV1.SUPPORT_IS_DESCRIPTIVE,
                BehavioralCaveatCodeV1.OUTCOME_PRESENCE_ONLY,
                BehavioralCaveatCodeV1.TEMPORAL_STATE_IS_COHORT_LOCAL,
            }
        )
        if pattern_type is BehavioralPatternTypeV1.MIXED_EXACT_CHOICES:
            values.add(BehavioralCaveatCodeV1.MIXED_NO_WINNER)
        if pattern_type is BehavioralPatternTypeV1.INSUFFICIENT_EVIDENCE:
            values.add(BehavioralCaveatCodeV1.INSUFFICIENT_COMPARABLE_EVIDENCE)
    return _ordered_caveats(values)


def _result_caveats(
    observation_result: BehavioralObservationBuildResultV1,
    patterns: tuple[BehavioralPatternV1, ...],
) -> tuple[BehavioralCaveatCodeV1, ...]:
    values: set[BehavioralCaveatCodeV1] = {
        BehavioralCaveatCodeV1.CURRENT_VAULT_REBUILD,
        BehavioralCaveatCodeV1.STATED_OBSERVED_MAPPING_MISSING,
    }
    if observation_result.excluded_unknown_time_count:
        values.add(BehavioralCaveatCodeV1.UNKNOWN_TIME_EXCLUDED)
    if observation_result.excluded_future_or_invalid_time_count:
        values.add(BehavioralCaveatCodeV1.FUTURE_OR_INVALID_TIME_EXCLUDED)
    if observation_result.excluded_outside_horizon_count:
        values.add(BehavioralCaveatCodeV1.OUTSIDE_HORIZON_EXCLUDED)
    if observation_result.excluded_missing_domain_count:
        values.add(BehavioralCaveatCodeV1.NOT_COMPARABLE_UNDER_V1)
    if any(item.pattern_type is BehavioralPatternTypeV1.INSUFFICIENT_EVIDENCE for item in patterns):
        values.add(BehavioralCaveatCodeV1.INSUFFICIENT_COMPARABLE_EVIDENCE)
    if any(item.pattern_type is BehavioralPatternTypeV1.NOT_COMPARABLE for item in patterns):
        values.add(BehavioralCaveatCodeV1.NOT_COMPARABLE_UNDER_V1)
    if not patterns and observation_result.eligible_journal_count == 0:
        values.add(BehavioralCaveatCodeV1.INSUFFICIENT_COMPARABLE_EVIDENCE)
    return _ordered_caveats(values)


def _validate_observation_binding(
    result: BehavioralObservationBuildResultV1,
    policy_fingerprint: str,
    generated_at: datetime,
) -> None:
    if (
        result.policy_fingerprint != policy_fingerprint
        or result.generated_at != generated_at
        or result.contract_version != CONTRACT_VERSION
        or result.policy_id != POLICY_ID
        or result.derivation_version != DERIVATION_VERSION
    ):
        raise BehavioralSelfModelPolicyMismatchError()


def _unanimous_observation_key(
    observations: tuple[BehavioralObservationV1, ...],
) -> tuple[int, str] | None:
    if not observations:
        return None
    keys = {
        (item.chosen_option.option_index, item.chosen_option.option_fingerprint)
        for item in observations
    }
    return next(iter(keys)) if len(keys) == 1 else None


def _unanimous_choice_key(
    supports: tuple[BehavioralChoiceSupportV1, ...],
) -> tuple[int, str] | None:
    if len(supports) != 1:
        return None
    item = supports[0]
    return item.option.option_index, item.option.option_fingerprint


def _normalize_pattern_type(value: object) -> BehavioralPatternTypeV1:
    if type(value) is BehavioralPatternTypeV1:
        return value
    if type(value) is str:
        try:
            return BehavioralPatternTypeV1(value)
        except ValueError:
            pass
    raise ValueError("behavioral pattern type is invalid")


def _normalize_pattern_state(value: object) -> BehavioralPatternStateV1:
    if type(value) is BehavioralPatternStateV1:
        return value
    if type(value) is str:
        try:
            return BehavioralPatternStateV1(value)
        except ValueError:
            pass
    raise ValueError("behavioral pattern state is invalid")


def _normalize_window(value: object) -> BehavioralTemporalWindowV1:
    if type(value) is BehavioralTemporalWindowV1:
        normalized = value
    elif type(value) is str:
        try:
            normalized = BehavioralTemporalWindowV1(value)
        except ValueError:
            raise ValueError("behavioral window is invalid") from None
    else:
        raise ValueError("behavioral window is invalid")
    if normalized not in {
        BehavioralTemporalWindowV1.HISTORICAL,
        BehavioralTemporalWindowV1.CURRENT,
    }:
        raise ValueError("behavioral window is invalid")
    return normalized


def _normalize_caveat(value: object) -> BehavioralCaveatCodeV1:
    if type(value) is BehavioralCaveatCodeV1:
        return value
    if type(value) is str:
        try:
            return BehavioralCaveatCodeV1(value)
        except ValueError:
            pass
    raise ValueError("behavioral caveat is invalid")


def _normalize_caveats(
    values: object,
) -> tuple[BehavioralCaveatCodeV1, ...]:
    if type(values) is not tuple:
        raise ValueError("behavioral caveats are invalid")
    normalized = tuple(_normalize_caveat(value) for value in values)
    if len(normalized) != len(set(normalized)) or normalized != _ordered_caveats(normalized):
        raise ValueError("behavioral caveats are invalid")
    return normalized


_CAVEAT_ORDER: Final[tuple[BehavioralCaveatCodeV1, ...]] = tuple(BehavioralCaveatCodeV1)


def _ordered_caveats(
    values: Iterable[BehavioralCaveatCodeV1],
) -> tuple[BehavioralCaveatCodeV1, ...]:
    unique = set(values)
    return tuple(item for item in _CAVEAT_ORDER if item in unique)


_EXPECTED_PATTERN_STATES: Final[
    dict[BehavioralPatternTypeV1, frozenset[BehavioralPatternStateV1]]
] = {
    BehavioralPatternTypeV1.REPEATED_EXACT_CHOICE: frozenset(
        {BehavioralPatternStateV1.CURRENT, BehavioralPatternStateV1.HISTORICAL}
    ),
    BehavioralPatternTypeV1.MIXED_EXACT_CHOICES: frozenset({BehavioralPatternStateV1.MIXED}),
    BehavioralPatternTypeV1.STABLE_OVER_TIME: frozenset({BehavioralPatternStateV1.STABLE}),
    BehavioralPatternTypeV1.CHANGED_OVER_TIME: frozenset({BehavioralPatternStateV1.CHANGED}),
    BehavioralPatternTypeV1.INSUFFICIENT_EVIDENCE: frozenset(
        {BehavioralPatternStateV1.INSUFFICIENT}
    ),
    BehavioralPatternTypeV1.NOT_COMPARABLE: frozenset({BehavioralPatternStateV1.NOT_COMPARABLE}),
}


def _pattern_sort_key(
    pattern: BehavioralPatternV1,
) -> tuple[str, str, str]:
    return (
        pattern.cohort.cohort_fingerprint if pattern.cohort is not None else "",
        cast(BehavioralPatternTypeV1, pattern.pattern_type).value,
        cast(BehavioralPatternStateV1, pattern.state).value,
    )


def _read_clock(clock: BehavioralSelfModelClock) -> datetime:
    if not callable(clock):
        raise BehavioralSelfModelInvalidRequestError()
    try:
        value = clock()
    except Exception:
        raise BehavioralSelfModelInvalidRequestError() from None
    if not _is_aware(value):
        raise BehavioralSelfModelInvalidRequestError()
    return value.astimezone(UTC)


def _normalize_outcome_presence_counts(
    value: object,
) -> BehavioralOutcomePresenceCounts:
    if not isinstance(value, Mapping):
        raise ValueError("behavioral outcome presence is invalid")
    data = dict(value)
    if set(data) != {"present", "absent"}:
        raise ValueError("behavioral outcome presence is invalid")
    if any(type(item) is not int or item < 0 for item in data.values()):
        raise ValueError("behavioral outcome presence is invalid")
    return cast(
        BehavioralOutcomePresenceCounts,
        MappingProxyType({"present": data["present"], "absent": data["absent"]}),
    )


def _canonical_json(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def _hash_json(value: object) -> str:
    return "sha256:" + hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


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


__all__ = [
    "ACTIVE_HORIZON_DAYS",
    "COMPOSITION_POLICY",
    "CONTRACT_VERSION",
    "DEFAULT_BEHAVIORAL_SELF_MODEL_POLICY",
    "DEFAULT_BEHAVIORAL_SELF_MODEL_REQUEST",
    "DERIVATION_VERSION",
    "FULL_DERIVATION_VERSION",
    "GROUPING_POLICY",
    "MAX_BEHAVIORAL_PATTERNS",
    "MAX_BEHAVIORAL_PROVENANCE_UUIDS",
    "MAX_BEHAVIORAL_RESULT_BYTES",
    "MINIMUM_COMPARABLE_OBSERVATIONS",
    "MINIMUM_OBSERVATIONS_PER_CHANGE_WINDOW",
    "OBSERVATION_VERSION",
    "OUTCOME_POLICY",
    "POLICY_FINGERPRINT",
    "POLICY_ID",
    "SUPPORT_POLICY",
    "TEMPORAL_POLICY",
    "BehavioralCaveatCode",
    "BehavioralCaveatCodeV1",
    "BehavioralChoiceSupportV1",
    "BehavioralOutcomePresenceCounts",
    "BehavioralPatternState",
    "BehavioralPatternStateV1",
    "BehavioralPatternType",
    "BehavioralPatternTypeV1",
    "BehavioralProvenanceV1",
    "BehavioralRatioV1",
    "BehavioralSelfModelAmbiguousGroupingError",
    "BehavioralSelfModelError",
    "BehavioralSelfModelErrorCode",
    "BehavioralSelfModelInsufficientComparableDecisionsError",
    "BehavioralSelfModelInvalidJournalError",
    "BehavioralSelfModelInvalidRequestError",
    "BehavioralSelfModelPolicy",
    "BehavioralSelfModelPolicyMismatchError",
    "BehavioralSelfModelRequest",
    "BehavioralSelfModelResultTooLargeError",
    "BehavioralSelfModelResultV1",
    "BehavioralSelfModelSourceUnavailableError",
    "BehavioralSelfModelUnsupportedSemanticComparisonError",
    "BehavioralTemporalSpanV1",
    "BehavioralWindowSummaryV1",
    "BuildBehavioralSelfModel",
    "BuildBehavioralSelfModelResult",
    "BuildBehavioralSelfModelV1",
    "behavioral_provenance_fingerprint",
    "build_behavioral_pattern",
    "classify_behavioral_pattern",
    "classify_behavioral_self_model_pattern",
    "validate_behavioral_self_model_policy",
    "validate_behavioral_self_model_request",
    "validate_behavioral_self_model_result",
]
