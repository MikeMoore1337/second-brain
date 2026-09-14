"""Deterministic Stage 12C Goal Progress read model.

This module is deliberately additive to the Stage 12A record/validator module
and the Stage 12B Safe Write boundary.  It reads one current vault snapshot,
reuses the existing scan projections and Stage 11A Goal authority, and emits a
bounded immutable derived result.  It has no write, provider, network, clock,
cache or transport capability.
"""

from __future__ import annotations

import re
from collections import defaultdict
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from enum import StrEnum
from types import MappingProxyType
from typing import Final, cast
from uuid import UUID

from second_brain.application.goal_progress import (
    DEFAULT_GOAL_PROGRESS_POLICY,
    GOAL_PROGRESS_POLICY_FINGERPRINT,
    GOAL_PROGRESS_UNKNOWN_TIME,
    DefinitionRecordV1,
    GoalProgressRecordKindV1,
    GoalProgressValidationError,
    MilestoneStateV1,
    NumericDirectionV1,
    ObservationEligibilityReasonV1,
    ObservationRecordV1,
    ProgressModelV1,
    SupersessionChainStateV1,
    canonical_goal_progress_json,
    evaluate_observation_eligibility,
    goal_progress_hash_json,
    is_goal_progress_enrolled,
    validate_definition_chain,
    validate_goal_progress_policy,
    validate_observation_against_definition,
    validate_observation_chain,
)
from second_brain.application.growth import (
    BuildGrowthGoalContext,
    GrowthEngineRequestV1,
    GrowthError,
    GrowthGoalIdentityV1,
    GrowthGoalMissingError,
    GrowthGoalSelectionModeV1,
    GrowthGoalSelectionV1,
    GrowthPolicyMismatchError,
)
from second_brain.application.ports import VaultReader
from second_brain.application.reports import ScanReport, VaultSnapshot
from second_brain.application.self_model import DEFAULT_SELF_MODEL_POLICY, SelfModelPolicy
from second_brain.application.validation import build_report
from second_brain.domain.models import NoteRecord, parse_rfc3339, parse_uuid7

MAX_GOAL_PROGRESS_RESULT_BYTES: Final[int] = 64 * 1024
MAX_GOAL_PROGRESS_EXCLUDED_REFERENCES: Final[int] = 4096
GOAL_PROGRESS_RESULT_CONTRACT: Final[str] = "goal_progress_result_v1"
_HASH_PATTERN: Final[re.Pattern[str]] = re.compile(r"sha256:[0-9a-f]{64}\Z", re.ASCII)


class GoalProgressStatusV1(StrEnum):
    """Closed descriptive status vocabulary from the Stage 12 contract."""

    TARGET_MET = "target_met"
    TOWARD_TARGET = "toward_target"
    AWAY_FROM_TARGET = "away_from_target"
    UNCHANGED = "unchanged"
    MILESTONE_OBSERVATIONS_AVAILABLE = "milestone_observations_available"
    INSUFFICIENT_OBSERVATIONS = "insufficient_observations"
    DEFINITION_MISSING = "definition_missing"
    GOAL_SOURCE_CHANGED = "goal_source_changed"
    NOT_COMPARABLE = "not_comparable"


GoalProgressStatus = GoalProgressStatusV1


class GoalProgressExclusionReasonV1(StrEnum):
    """Bounded machine-readable reasons for records outside the result."""

    UNKNOWN_TIME = "unknown_time"
    FUTURE_AS_OF = "future_as_of"
    BEFORE_DEFINITION_REVIEW = "before_definition_review"
    SUPERSEDED = "superseded"
    INVALID = "invalid"
    GOAL_SOURCE_CHANGED = "goal_source_changed"
    DEFINITION_MISMATCH = "definition_mismatch"
    DEFINITION_FINGERPRINT_MISMATCH = "definition_fingerprint_mismatch"
    MODEL_MISMATCH = "model_mismatch"
    POLICY_MISMATCH = "policy_mismatch"
    CHAIN_CONFLICT = "chain_conflict"


GoalProgressExclusionReason = GoalProgressExclusionReasonV1


class GoalProgressErrorCode(StrEnum):
    """Safe application errors allowed by the Stage 12 contract."""

    REQUEST_INVALID = "GOAL_PROGRESS_REQUEST_INVALID"
    GOAL_REQUIRED = "GOAL_PROGRESS_GOAL_REQUIRED"
    GOAL_AMBIGUOUS = "GOAL_PROGRESS_GOAL_AMBIGUOUS"
    GOAL_SOURCE_CHANGED = "GOAL_PROGRESS_GOAL_SOURCE_CHANGED"
    DEFINITION_MISSING = "GOAL_PROGRESS_DEFINITION_MISSING"
    DEFINITION_INVALID = "GOAL_PROGRESS_DEFINITION_INVALID"
    DEFINITION_STALE = "GOAL_PROGRESS_DEFINITION_STALE"
    DEFINITION_CONFLICT = "GOAL_PROGRESS_DEFINITION_CONFLICT"
    OBSERVATION_INVALID = "GOAL_PROGRESS_OBSERVATION_INVALID"
    OBSERVATION_MISSING = "GOAL_PROGRESS_OBSERVATION_MISSING"
    OBSERVATION_SOURCE_CHANGED = "GOAL_PROGRESS_OBSERVATION_SOURCE_CHANGED"
    UNIT_INCOMPATIBLE = "GOAL_PROGRESS_UNIT_INCOMPATIBLE"
    MODEL_UNSUPPORTED = "GOAL_PROGRESS_MODEL_UNSUPPORTED"
    POLICY_MISMATCH = "GOAL_PROGRESS_POLICY_MISMATCH"
    NOT_COMPARABLE = "GOAL_PROGRESS_NOT_COMPARABLE"
    RESULT_TOO_LARGE = "GOAL_PROGRESS_RESULT_TOO_LARGE"
    SOURCE_UNAVAILABLE = "GOAL_PROGRESS_SOURCE_UNAVAILABLE"
    COMPARISON_UNSUPPORTED = "GOAL_PROGRESS_COMPARISON_UNSUPPORTED"
    INTERNAL = "GOAL_PROGRESS_INTERNAL"


_ERROR_MESSAGES: Final[dict[GoalProgressErrorCode, str]] = {
    GoalProgressErrorCode.REQUEST_INVALID: "goal progress request failed validation",
    GoalProgressErrorCode.GOAL_REQUIRED: "an explicit Goal UUID is required",
    GoalProgressErrorCode.GOAL_AMBIGUOUS: "the explicit Goal selection is ambiguous",
    GoalProgressErrorCode.GOAL_SOURCE_CHANGED: "the current Goal source changed",
    GoalProgressErrorCode.DEFINITION_MISSING: "the current Goal has no active progress definition",
    GoalProgressErrorCode.DEFINITION_INVALID: "the current progress definition is invalid",
    GoalProgressErrorCode.DEFINITION_STALE: "the current progress definition is stale",
    GoalProgressErrorCode.DEFINITION_CONFLICT: "the current progress definition is conflicting",
    GoalProgressErrorCode.OBSERVATION_INVALID: "a Goal Progress observation is invalid",
    GoalProgressErrorCode.OBSERVATION_MISSING: "a Goal Progress observation is missing",
    GoalProgressErrorCode.OBSERVATION_SOURCE_CHANGED: "an observation source changed",
    GoalProgressErrorCode.UNIT_INCOMPATIBLE: "the observation unit is incompatible",
    GoalProgressErrorCode.MODEL_UNSUPPORTED: "the Goal Progress model is unsupported",
    GoalProgressErrorCode.POLICY_MISMATCH: "the Goal Progress policy binding is invalid",
    GoalProgressErrorCode.NOT_COMPARABLE: "the current Goal Progress records are not comparable",
    GoalProgressErrorCode.RESULT_TOO_LARGE: "the Goal Progress result exceeds its bounded limit",
    GoalProgressErrorCode.SOURCE_UNAVAILABLE: "the current Goal Progress source is unavailable",
    GoalProgressErrorCode.COMPARISON_UNSUPPORTED: (
        "the requested Goal Progress comparison is unsupported"
    ),
    GoalProgressErrorCode.INTERNAL: "the Goal Progress read model is unavailable",
}


class GoalProgressError(RuntimeError):
    """Safe application error with no path, body or exception representation."""

    def __init__(self, code: GoalProgressErrorCode | str) -> None:
        normalized = _normalize_error_code(code)
        self.code = normalized.value
        self.message = _ERROR_MESSAGES[normalized]
        super().__init__(self.message)

    def as_dict(self) -> dict[str, str]:
        """Return the stable safe error projection."""

        return {"code": self.code, "message": self.message}


class GoalProgressInvalidRequestError(GoalProgressError):
    """The request is absent, malformed or not canonical UTC."""

    def __init__(self) -> None:
        super().__init__(GoalProgressErrorCode.REQUEST_INVALID)


class GoalProgressGoalRequiredError(GoalProgressError):
    """The read model requires one explicit Goal UUID."""

    def __init__(self) -> None:
        super().__init__(GoalProgressErrorCode.GOAL_REQUIRED)


class GoalProgressGoalAmbiguousError(GoalProgressError):
    """The requested Goal cannot be resolved uniquely."""

    def __init__(self) -> None:
        super().__init__(GoalProgressErrorCode.GOAL_AMBIGUOUS)


class GoalProgressGoalSourceChangedError(GoalProgressError):
    """The caller or source cannot prove the exact Goal binding."""

    def __init__(self) -> None:
        super().__init__(GoalProgressErrorCode.GOAL_SOURCE_CHANGED)


class GoalProgressSourceUnavailableError(GoalProgressError):
    """The current vault or Stage 11A source cannot be safely reread."""

    def __init__(self) -> None:
        super().__init__(GoalProgressErrorCode.SOURCE_UNAVAILABLE)


class GoalProgressPolicyMismatchError(GoalProgressError):
    """The compiled Stage 12 policy cannot be proven."""

    def __init__(self) -> None:
        super().__init__(GoalProgressErrorCode.POLICY_MISMATCH)


class GoalProgressResultTooLargeError(GoalProgressError):
    """The complete result cannot be emitted within the approved bound."""

    def __init__(self) -> None:
        super().__init__(GoalProgressErrorCode.RESULT_TOO_LARGE)


@dataclass(frozen=True, slots=True)
class GoalProgressRequestV1:
    """Explicit immutable request for one Goal and one UTC cutoff."""

    goal_source_uuid: UUID | str | None = None
    as_of: datetime | str | None = None

    @classmethod
    def from_dict(cls, value: object) -> GoalProgressRequestV1:
        """Parse exactly ``goal_source_uuid`` and ``as_of`` fields."""

        if not isinstance(value, Mapping) or set(value) != {"goal_source_uuid", "as_of"}:
            raise GoalProgressInvalidRequestError()
        return cls(value["goal_source_uuid"], value["as_of"])

    def as_dict(self) -> dict[str, object]:
        """Return a bounded request projection without generated values."""

        return {
            "goal_source_uuid": (
                str(self.goal_source_uuid) if self.goal_source_uuid is not None else None
            ),
            "as_of": _timestamp_projection(self.as_of),
        }


GoalProgressRequest = GoalProgressRequestV1


@dataclass(frozen=True, slots=True)
class GoalProgressExcludedObservationV1:
    """One bounded excluded observation reference."""

    observation_id: UUID | str | None
    reason: GoalProgressExclusionReasonV1 | str

    def __post_init__(self) -> None:
        if self.observation_id is not None:
            object.__setattr__(self, "observation_id", _parse_uuid(self.observation_id))
        object.__setattr__(self, "reason", _parse_enum(self.reason, GoalProgressExclusionReasonV1))

    @property
    def record_id(self) -> UUID | None:
        """Compatibility alias for the canonical record reference."""

        return cast(UUID | None, self.observation_id)

    @property
    def reference(self) -> UUID | None:
        """Compatibility alias used by bounded provenance consumers."""

        return self.record_id

    def as_dict(self) -> dict[str, object]:
        """Return only an ID and a closed reason code."""

        return {
            "observation_id": str(self.observation_id) if self.observation_id is not None else None,
            "reason": cast(GoalProgressExclusionReasonV1, self.reason).value,
        }


GoalProgressExcludedObservation = GoalProgressExcludedObservationV1


@dataclass(frozen=True, slots=True)
class GoalProgressProvenanceV1:
    """Proof of the read boundary and exact selected source."""

    source: str
    provider: str
    network: str
    write: str
    as_of: datetime
    goal_source_uuid: UUID
    goal_identity_fingerprint: str | None
    policy_fingerprint: str
    definition_uuid: UUID | None = None
    definition_fingerprint: str | None = None

    def __post_init__(self) -> None:
        if self.source != "current_vault" or self.provider != "none":
            raise ValueError("Goal Progress provenance source is invalid")
        if self.network != "none" or self.write != "none":
            raise ValueError("Goal Progress provenance side effects are invalid")
        object.__setattr__(self, "as_of", _parse_exact_utc(self.as_of))
        object.__setattr__(self, "goal_source_uuid", _parse_uuid(self.goal_source_uuid))
        if self.goal_identity_fingerprint is not None:
            _parse_hash(self.goal_identity_fingerprint)
        if self.policy_fingerprint != GOAL_PROGRESS_POLICY_FINGERPRINT:
            raise ValueError("Goal Progress provenance policy is invalid")
        if self.definition_uuid is not None:
            object.__setattr__(self, "definition_uuid", _parse_uuid(self.definition_uuid))
        if self.definition_fingerprint is not None:
            _parse_hash(self.definition_fingerprint)

    @property
    def goal_progress_policy_fingerprint(self) -> str:
        """Compatibility spelling matching the record DTOs."""

        return self.policy_fingerprint

    def as_dict(self) -> dict[str, object]:
        """Return semantic provenance only; no generated clock or path."""

        return {
            "source": self.source,
            "provider": self.provider,
            "network": self.network,
            "write": self.write,
            "as_of": _format_timestamp(self.as_of),
            "goal_source_uuid": str(self.goal_source_uuid),
            "goal_identity_fingerprint": self.goal_identity_fingerprint,
            "policy_fingerprint": self.policy_fingerprint,
            "definition_uuid": (
                str(self.definition_uuid) if self.definition_uuid is not None else None
            ),
            "definition_fingerprint": self.definition_fingerprint,
        }


GoalProgressProvenance = GoalProgressProvenanceV1


@dataclass(frozen=True, slots=True)
class GoalProgressResultV1:
    """Immutable, bounded derived result for one exact Goal query."""

    selected_goal_source_uuid: UUID | str
    current_goal_identity_fingerprint: str | None
    goal_progress_policy_fingerprint: str
    active_definition_uuid: UUID | str | None
    definition_fingerprint: str | None
    as_of: datetime | str
    progress_model: ProgressModelV1 | str | None
    status: GoalProgressStatusV1 | str
    current_observation_uuids: tuple[UUID | str, ...] = ()
    excluded_observations: tuple[GoalProgressExcludedObservationV1, ...] = ()
    eligible_count: int = 0
    unknown_time_count: int = 0
    superseded_count: int = 0
    invalid_count: int = 0
    explanation: Mapping[str, object] = MappingProxyType({})
    provenance: GoalProgressProvenanceV1 | None = None
    completed_milestone_ids: tuple[str, ...] = ()
    not_completed_milestone_ids: tuple[str, ...] = ()
    missing_milestone_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "selected_goal_source_uuid", _parse_uuid(self.selected_goal_source_uuid)
        )
        object.__setattr__(self, "as_of", _parse_exact_utc(self.as_of))
        if self.current_goal_identity_fingerprint is not None:
            _parse_hash(self.current_goal_identity_fingerprint)
        if self.goal_progress_policy_fingerprint != GOAL_PROGRESS_POLICY_FINGERPRINT:
            raise ValueError("Goal Progress result policy is invalid")
        if self.active_definition_uuid is not None:
            object.__setattr__(
                self, "active_definition_uuid", _parse_uuid(self.active_definition_uuid)
            )
        if self.definition_fingerprint is not None:
            _parse_hash(self.definition_fingerprint)
        if self.progress_model is not None:
            object.__setattr__(
                self, "progress_model", _parse_enum(self.progress_model, ProgressModelV1)
            )
        object.__setattr__(self, "status", _parse_enum(self.status, GoalProgressStatusV1))
        object.__setattr__(
            self,
            "current_observation_uuids",
            tuple(_parse_uuid(value) for value in self.current_observation_uuids),
        )
        if any(
            type(item) is not GoalProgressExcludedObservationV1
            for item in self.excluded_observations
        ):
            raise ValueError("Goal Progress excluded observations are invalid")
        object.__setattr__(self, "excluded_observations", tuple(self.excluded_observations))
        for name in ("eligible_count", "unknown_time_count", "superseded_count", "invalid_count"):
            value = getattr(self, name)
            if type(value) is not int or value < 0:
                raise ValueError("Goal Progress result count is invalid")
        if not isinstance(self.explanation, Mapping):
            raise ValueError("Goal Progress explanation is invalid")
        object.__setattr__(self, "explanation", MappingProxyType(dict(self.explanation)))
        if self.provenance is None:
            provenance = GoalProgressProvenanceV1(
                source="current_vault",
                provider="none",
                network="none",
                write="none",
                as_of=cast(datetime, self.as_of),
                goal_source_uuid=cast(UUID, self.selected_goal_source_uuid),
                goal_identity_fingerprint=self.current_goal_identity_fingerprint,
                policy_fingerprint=self.goal_progress_policy_fingerprint,
                definition_uuid=cast(UUID | None, self.active_definition_uuid),
                definition_fingerprint=self.definition_fingerprint,
            )
            object.__setattr__(self, "provenance", provenance)
        elif type(self.provenance) is not GoalProgressProvenanceV1:
            raise ValueError("Goal Progress provenance is invalid")
        for name in (
            "completed_milestone_ids",
            "not_completed_milestone_ids",
            "missing_milestone_ids",
        ):
            values = getattr(self, name)
            if type(values) is not tuple or any(type(value) is not str for value in values):
                raise ValueError("Goal Progress milestone IDs are invalid")
            if len(values) != len(set(values)):
                raise ValueError("Goal Progress milestone IDs are duplicated")

    @property
    def goal_source_uuid(self) -> UUID:
        """Compatibility alias for the selected exact Goal UUID."""

        return cast(UUID, self.selected_goal_source_uuid)

    @property
    def selected_goal_identity_fingerprint(self) -> str | None:
        """Compatibility alias for current Goal identity."""

        return self.current_goal_identity_fingerprint

    @property
    def active_definition_id(self) -> UUID | None:
        """Compatibility alias for the active definition UUID."""

        return cast(UUID | None, self.active_definition_uuid)

    @property
    def excluded_observation_references(self) -> tuple[GoalProgressExcludedObservationV1, ...]:
        """Bounded excluded-record projection."""

        return self.excluded_observations

    @property
    def goal_progress_policy(self) -> str:
        """Compatibility alias for the fixed Stage 12 policy fingerprint."""

        return self.goal_progress_policy_fingerprint

    def as_dict(self) -> dict[str, object]:
        """Return the deterministic JSON-compatible result projection."""

        return {
            "contract": GOAL_PROGRESS_RESULT_CONTRACT,
            "selected_goal_source_uuid": str(self.selected_goal_source_uuid),
            "current_goal_identity_fingerprint": self.current_goal_identity_fingerprint,
            "goal_progress_policy_fingerprint": self.goal_progress_policy_fingerprint,
            "active_definition_uuid": (
                str(self.active_definition_uuid)
                if self.active_definition_uuid is not None
                else None
            ),
            "definition_fingerprint": self.definition_fingerprint,
            "as_of": _format_timestamp(cast(datetime, self.as_of)),
            "progress_model": (
                cast(ProgressModelV1, self.progress_model).value
                if self.progress_model is not None
                else None
            ),
            "status": cast(GoalProgressStatusV1, self.status).value,
            "current_observation_uuids": [str(value) for value in self.current_observation_uuids],
            "excluded_observations": [item.as_dict() for item in self.excluded_observations],
            "eligible_count": self.eligible_count,
            "unknown_time_count": self.unknown_time_count,
            "superseded_count": self.superseded_count,
            "invalid_count": self.invalid_count,
            "explanation": _json_safe(self.explanation),
            "provenance": cast(GoalProgressProvenanceV1, self.provenance).as_dict(),
            "completed_milestone_ids": list(self.completed_milestone_ids),
            "not_completed_milestone_ids": list(self.not_completed_milestone_ids),
            "missing_milestone_ids": list(self.missing_milestone_ids),
        }

    def to_json(self) -> str:
        """Serialize the complete result under the repository JSON profile."""

        return canonical_goal_progress_json(self.as_dict())


GoalProgressResult = GoalProgressResultV1


@dataclass(frozen=True, slots=True)
class _SnapshotReader:
    """Return one already captured raw snapshot to Stage 11A builders."""

    snapshot: VaultSnapshot

    def scan(self) -> VaultSnapshot:
        return self.snapshot


@dataclass(frozen=True, slots=True)
class _ObservationProjection:
    observations: tuple[ObservationRecordV1, ...]
    active: tuple[ObservationRecordV1, ...]
    eligible: tuple[ObservationRecordV1, ...]
    excluded: tuple[GoalProgressExcludedObservationV1, ...]
    eligible_count: int
    unknown_time_count: int
    superseded_count: int
    invalid_count: int
    chain_conflict: bool


@dataclass(frozen=True, slots=True)
class BuildGoalProgress:
    """Build one current-vault Goal Progress result without side effects."""

    reader: VaultReader
    policy: SelfModelPolicy = DEFAULT_SELF_MODEL_POLICY

    def execute(self, request: GoalProgressRequestV1) -> GoalProgressResultV1:
        """Validate, reread, resolve and deterministically evaluate one Goal."""

        validated_request = validate_goal_progress_request(request)
        try:
            validate_goal_progress_policy(DEFAULT_GOAL_PROGRESS_POLICY)
        except GoalProgressValidationError, TypeError, ValueError:
            raise GoalProgressPolicyMismatchError() from None

        snapshot, report = _read_current_report(self.reader)
        goal_source_uuid = cast(UUID, validated_request.goal_source_uuid)
        as_of = cast(datetime, validated_request.as_of)
        _raise_relevant_policy_mismatch(report, goal_source_uuid)
        current_goal = _resolve_current_goal(
            snapshot,
            goal_source_uuid,
            as_of,
            self.policy,
        )
        current_goal_hash = (
            goal_progress_hash_json(current_goal.as_dict()) if current_goal is not None else None
        )

        if current_goal is None:
            result = _result(
                validated_request,
                status=GoalProgressStatusV1.GOAL_SOURCE_CHANGED,
                current_goal_identity_fingerprint=None,
                excluded=_historical_observation_exclusions(report, goal_source_uuid),
            )
            return _ensure_bounded_result(result)

        assert current_goal_hash is not None
        if _has_goal_identity_drift(
            report,
            goal_source_uuid,
            current_goal_hash,
        ):
            result = _result(
                validated_request,
                status=GoalProgressStatusV1.GOAL_SOURCE_CHANGED,
                current_goal_identity_fingerprint=current_goal_hash,
                excluded=_historical_observation_exclusions(
                    report,
                    goal_source_uuid,
                    current_goal_hash=current_goal_hash,
                ),
            )
            return _ensure_bounded_result(result)

        definitions = tuple(
            item
            for item in report.goal_progress_definitions
            if item.goal_source_uuid == goal_source_uuid
            and item.goal_identity_fingerprint == current_goal_hash
            and item.goal_progress_policy_fingerprint == GOAL_PROGRESS_POLICY_FINGERPRINT
        )
        chain = validate_definition_chain(definitions) if definitions else None
        if chain is None or chain.state is SupersessionChainStateV1.ZERO_ACTIVE:
            result = _result(
                validated_request,
                status=GoalProgressStatusV1.DEFINITION_MISSING,
                current_goal_identity_fingerprint=current_goal_hash,
            )
            return _ensure_bounded_result(result)
        if chain.issues or chain.state is not SupersessionChainStateV1.ONE_ACTIVE:
            result = _result(
                validated_request,
                status=GoalProgressStatusV1.NOT_COMPARABLE,
                current_goal_identity_fingerprint=current_goal_hash,
            )
            return _ensure_bounded_result(result)
        active_definition = chain.active_records[0]
        if type(active_definition) is not DefinitionRecordV1:
            raise GoalProgressError(GoalProgressErrorCode.DEFINITION_INVALID)

        # The property recomputes the semantic fingerprint from the actual DTO;
        # no persisted observation value is trusted as definition authority.
        definition_fingerprint = active_definition.definition_fingerprint
        projection = _classify_observations(
            report,
            goal_source_uuid=goal_source_uuid,
            goal_identity_fingerprint=current_goal_hash,
            definition=active_definition,
            as_of=as_of,
        )
        if projection.chain_conflict:
            result = _result(
                validated_request,
                status=GoalProgressStatusV1.NOT_COMPARABLE,
                current_goal_identity_fingerprint=current_goal_hash,
                active_definition=active_definition,
                projection=projection,
            )
            return _ensure_bounded_result(result)
        if not projection.eligible:
            result = _result(
                validated_request,
                status=GoalProgressStatusV1.INSUFFICIENT_OBSERVATIONS,
                current_goal_identity_fingerprint=current_goal_hash,
                active_definition=active_definition,
                projection=projection,
            )
            return _ensure_bounded_result(result)

        if active_definition.progress_model is ProgressModelV1.NUMERIC_TARGET:
            result = _evaluate_numeric(
                validated_request,
                current_goal_hash=current_goal_hash,
                definition=active_definition,
                definition_fingerprint=definition_fingerprint,
                projection=projection,
            )
        elif active_definition.progress_model is ProgressModelV1.MILESTONE_SET:
            result = _evaluate_milestones(
                validated_request,
                current_goal_hash=current_goal_hash,
                definition=active_definition,
                definition_fingerprint=definition_fingerprint,
                projection=projection,
            )
        else:
            raise GoalProgressError(GoalProgressErrorCode.MODEL_UNSUPPORTED)
        return _ensure_bounded_result(result)


BuildGoalProgressV1 = BuildGoalProgress
GoalProgressBuilder = BuildGoalProgress


def build_goal_progress(
    reader: VaultReader,
    request: GoalProgressRequestV1,
    *,
    policy: SelfModelPolicy = DEFAULT_SELF_MODEL_POLICY,
) -> GoalProgressResultV1:
    """Functional application entry point for the private read model."""

    return BuildGoalProgress(reader, policy=policy).execute(request)


def validate_goal_progress_request(value: object) -> GoalProgressRequestV1:
    """Validate exact Goal UUID and canonical UTC ``as_of`` before any scan."""

    if type(value) is not GoalProgressRequestV1:
        raise GoalProgressInvalidRequestError()
    request = value
    if request.goal_source_uuid is None:
        raise GoalProgressGoalRequiredError()
    if request.as_of is None:
        raise GoalProgressInvalidRequestError()
    try:
        goal_uuid = _parse_uuid(request.goal_source_uuid)
        as_of = _parse_exact_utc(request.as_of)
    except TypeError, ValueError, OverflowError:
        raise GoalProgressInvalidRequestError() from None
    return GoalProgressRequestV1(goal_uuid, as_of)


def validate_goal_progress_result(value: object) -> GoalProgressResultV1:
    """Validate one immutable result and enforce its complete byte bound."""

    if type(value) is not GoalProgressResultV1:
        raise GoalProgressError(GoalProgressErrorCode.INTERNAL)
    result = value
    try:
        encoded = result.to_json().encode("utf-8")
    except TypeError, ValueError, UnicodeError, OverflowError:
        raise GoalProgressError(GoalProgressErrorCode.INTERNAL) from None
    if len(encoded) > MAX_GOAL_PROGRESS_RESULT_BYTES:
        raise GoalProgressResultTooLargeError()
    return result


def _read_current_report(reader: VaultReader) -> tuple[VaultSnapshot, ScanReport]:
    try:
        snapshot = reader.scan()
        report = build_report(snapshot)
    except Exception:
        raise GoalProgressSourceUnavailableError() from None
    if type(snapshot) is not VaultSnapshot or type(report) is not ScanReport:
        raise GoalProgressSourceUnavailableError()
    if report.manifest is None:
        raise GoalProgressSourceUnavailableError()
    return snapshot, report


def _resolve_current_goal(
    snapshot: VaultSnapshot,
    goal_source_uuid: UUID,
    as_of: datetime,
    policy: SelfModelPolicy,
) -> GrowthGoalIdentityV1 | None:
    """Use the existing Stage 11A current Goal authority on one snapshot."""

    selection = GrowthGoalSelectionV1(
        GrowthGoalSelectionModeV1.SELECTED_GOAL,
        goal_source_uuid,
    )
    try:
        context = BuildGrowthGoalContext(
            _SnapshotReader(snapshot),
            policy=policy,
            # Stage 11A needs a generation instant for validation; using the
            # explicit query cutoff keeps this builder free of current-clock
            # authority and does not add that instant to Goal identity.
            clock=lambda: as_of,
        ).execute(GrowthEngineRequestV1(selection=selection))
    except GrowthGoalMissingError:
        return None
    except GrowthPolicyMismatchError:
        raise GoalProgressPolicyMismatchError() from None
    except GrowthError:
        raise GoalProgressSourceUnavailableError() from None
    except Exception:
        raise GoalProgressSourceUnavailableError() from None
    if len(context.goals) != 1:
        raise GoalProgressGoalAmbiguousError()
    return context.goals[0]


def _has_goal_identity_drift(
    report: ScanReport,
    goal_source_uuid: UUID,
    current_goal_hash: str,
) -> bool:
    """Detect historical records bound to this UUID but another identity."""

    bindings: list[str] = []
    for record in report.goal_progress_definitions:
        if record.goal_source_uuid == goal_source_uuid:
            bindings.append(record.goal_identity_fingerprint)
    for observation_record in report.goal_progress_observations:
        if observation_record.goal_source_uuid == goal_source_uuid:
            bindings.append(observation_record.goal_identity_fingerprint)
    for note in report.notes:
        if not is_goal_progress_enrolled(note.front_matter):
            continue
        raw_uuid = _safe_uuid(_raw_field(note, "goal_source_uuid"))
        raw_hash = _safe_hash(_raw_field(note, "goal_identity_fingerprint"))
        if raw_uuid == goal_source_uuid and raw_hash is not None:
            bindings.append(raw_hash)
    return bool(bindings) and current_goal_hash not in bindings


def _raise_relevant_policy_mismatch(report: ScanReport, goal_source_uuid: UUID) -> None:
    """Fail closed for a selected Goal's explicitly incompatible raw policy."""

    for note in report.notes:
        if not is_goal_progress_enrolled(note.front_matter):
            continue
        if _safe_uuid(_raw_field(note, "goal_source_uuid")) != goal_source_uuid:
            continue
        raw_policy = _raw_field(note, "goal_progress_policy_fingerprint")
        if raw_policy is not None and raw_policy != GOAL_PROGRESS_POLICY_FINGERPRINT:
            raise GoalProgressPolicyMismatchError()


def _historical_observation_exclusions(
    report: ScanReport,
    goal_source_uuid: UUID,
    *,
    current_goal_hash: str | None = None,
) -> tuple[GoalProgressExcludedObservationV1, ...]:
    values: list[GoalProgressExcludedObservationV1] = []
    for observation in report.goal_progress_observations:
        if observation.goal_source_uuid != goal_source_uuid:
            continue
        if current_goal_hash is None or observation.goal_identity_fingerprint != current_goal_hash:
            values.append(
                GoalProgressExcludedObservationV1(
                    observation.id,
                    GoalProgressExclusionReasonV1.GOAL_SOURCE_CHANGED,
                )
            )
    return _sort_excluded(values)


def _classify_observations(
    report: ScanReport,
    *,
    goal_source_uuid: UUID,
    goal_identity_fingerprint: str,
    definition: DefinitionRecordV1,
    as_of: datetime,
) -> _ObservationProjection:
    """Filter typed projections and safely account for invalid raw records."""

    selected: list[ObservationRecordV1] = []
    excluded: list[GoalProgressExcludedObservationV1] = []
    invalid_count = 0
    typed_ids: set[UUID] = set()

    for observation in report.goal_progress_observations:
        if observation.goal_source_uuid != goal_source_uuid:
            continue
        typed_ids.add(cast(UUID, observation.id))
        if observation.goal_identity_fingerprint != goal_identity_fingerprint:
            excluded.append(
                GoalProgressExcludedObservationV1(
                    observation.id,
                    GoalProgressExclusionReasonV1.GOAL_SOURCE_CHANGED,
                )
            )
            continue
        if observation.goal_progress_policy_fingerprint != GOAL_PROGRESS_POLICY_FINGERPRINT:
            raise GoalProgressPolicyMismatchError()
        if observation.progress_definition_id != definition.id:
            excluded.append(
                GoalProgressExcludedObservationV1(
                    observation.id,
                    GoalProgressExclusionReasonV1.DEFINITION_MISMATCH,
                )
            )
            continue
        if observation.definition_fingerprint != definition.definition_fingerprint:
            invalid_count += 1
            excluded.append(
                GoalProgressExcludedObservationV1(
                    observation.id,
                    GoalProgressExclusionReasonV1.DEFINITION_FINGERPRINT_MISMATCH,
                )
            )
            continue
        issues = validate_observation_against_definition(observation, definition)
        if issues:
            invalid_count += 1
            excluded.append(
                GoalProgressExcludedObservationV1(
                    observation.id,
                    _reason_for_observation_issues(issues),
                )
            )
            continue
        selected.append(observation)

    # A malformed marker-enrolled observation is absent from the typed scan
    # projection.  The scanner remains the parser authority; this bounded raw
    # inspection only links a safely available UUID to the invalid count and
    # never reparses YAML or copies body/path data.
    for note in report.notes:
        if not is_goal_progress_enrolled(note.front_matter):
            continue
        if _raw_field(note, "goal_progress_kind") != GoalProgressRecordKindV1.OBSERVATION.value:
            continue
        if _safe_uuid(_raw_field(note, "goal_source_uuid")) != goal_source_uuid:
            continue
        note_id = note.note_id
        if note_id is not None and note_id in typed_ids:
            continue
        invalid_count += 1
        excluded.append(
            GoalProgressExcludedObservationV1(note_id, GoalProgressExclusionReasonV1.INVALID)
        )

    # Stage 12A validates one correction chain at a time.  Distinct event
    # times remain separate observations, so partition before invoking that
    # validator instead of treating historical measurements as one chain.
    event_groups: defaultdict[tuple[object, ...], list[ObservationRecordV1]] = defaultdict(list)
    for observation in selected:
        event_groups[_observation_event_key(observation)].append(observation)

    chain_conflict = False
    active_values: list[ObservationRecordV1] = []
    for event_key in sorted(event_groups, key=str):
        chain = validate_observation_chain(event_groups[event_key])
        if chain.issues:
            chain_conflict = True
            continue
        active_values.extend(cast(ObservationRecordV1, item) for item in chain.active_records)

    selected_ids = {cast(UUID, observation.id) for observation in selected}
    superseded_ids = {
        cast(UUID, observation.supersedes_observation_id)
        for observation in selected
        if observation.supersedes_observation_id is not None
        and cast(UUID, observation.supersedes_observation_id) in selected_ids
    }
    active = tuple(active_values)

    eligible: list[ObservationRecordV1] = []
    unknown_time_count = 0
    for observation in selected:
        observation_id = cast(UUID, observation.id)
        if observation_id in superseded_ids:
            excluded.append(
                GoalProgressExcludedObservationV1(
                    observation.id,
                    GoalProgressExclusionReasonV1.SUPERSEDED,
                )
            )
            continue
        eligibility = evaluate_observation_eligibility(
            observation,
            definition,
            as_of=as_of,
        )
        if eligibility.reason is ObservationEligibilityReasonV1.ELIGIBLE:
            eligible.append(observation)
        elif eligibility.reason is ObservationEligibilityReasonV1.UNKNOWN_TIME:
            unknown_time_count += 1
            excluded.append(
                GoalProgressExcludedObservationV1(
                    observation.id,
                    GoalProgressExclusionReasonV1.UNKNOWN_TIME,
                )
            )
        elif eligibility.reason is ObservationEligibilityReasonV1.FUTURE_AS_OF:
            excluded.append(
                GoalProgressExcludedObservationV1(
                    observation.id,
                    GoalProgressExclusionReasonV1.FUTURE_AS_OF,
                )
            )
        elif eligibility.reason is ObservationEligibilityReasonV1.BEFORE_DEFINITION_REVIEW:
            excluded.append(
                GoalProgressExcludedObservationV1(
                    observation.id,
                    GoalProgressExclusionReasonV1.BEFORE_DEFINITION_REVIEW,
                )
            )
        else:
            invalid_count += 1
            excluded.append(
                GoalProgressExcludedObservationV1(
                    observation.id,
                    GoalProgressExclusionReasonV1.INVALID,
                )
            )
    # ``active`` is intentionally retained as a typed projection in the
    # helper even when every current observation is excluded by time.  It is
    # useful for deterministic diagnostics and makes no storage authority.
    return _ObservationProjection(
        observations=tuple(sorted(selected, key=lambda item: str(item.id))),
        active=tuple(sorted(active, key=lambda item: str(item.id))) if not chain_conflict else (),
        eligible=tuple(
            sorted(eligible, key=lambda item: (cast(datetime, item.observed_at), str(item.id)))
        ),
        excluded=_sort_excluded(excluded),
        eligible_count=len(eligible),
        unknown_time_count=unknown_time_count,
        superseded_count=len(superseded_ids),
        invalid_count=invalid_count,
        chain_conflict=chain_conflict or _has_same_time_conflict(tuple(eligible), definition),
    )


def _evaluate_numeric(
    request: GoalProgressRequestV1,
    *,
    current_goal_hash: str,
    definition: DefinitionRecordV1,
    definition_fingerprint: str,
    projection: _ObservationProjection,
) -> GoalProgressResultV1:
    numeric = definition.numeric_target
    if numeric is None:
        raise GoalProgressError(GoalProgressErrorCode.MODEL_UNSUPPORTED)
    eligible = projection.eligible
    if any(item.numeric_observation is None for item in eligible):
        raise GoalProgressError(GoalProgressErrorCode.MODEL_UNSUPPORTED)
    latest_time = max(cast(datetime, item.observed_at) for item in eligible)
    latest = tuple(item for item in eligible if cast(datetime, item.observed_at) == latest_time)
    if len(latest) != 1:
        return _result(
            request,
            status=GoalProgressStatusV1.NOT_COMPARABLE,
            current_goal_identity_fingerprint=current_goal_hash,
            active_definition=definition,
            projection=projection,
            definition_fingerprint=definition_fingerprint,
        )
    observation = latest[0]
    current_value = cast(Decimal, observation.value)
    baseline = cast(Decimal, numeric.baseline)
    target = cast(Decimal, numeric.target)
    target_met = (
        current_value >= target
        if cast(NumericDirectionV1, numeric.direction).value == "increase_to"
        else current_value <= target
        if cast(NumericDirectionV1, numeric.direction).value == "decrease_to"
        else current_value == target
    )
    baseline_distance = abs(target - baseline)
    current_distance = abs(target - current_value)
    if target_met:
        status = GoalProgressStatusV1.TARGET_MET
    elif current_distance < baseline_distance:
        status = GoalProgressStatusV1.TOWARD_TARGET
    elif current_distance > baseline_distance:
        status = GoalProgressStatusV1.AWAY_FROM_TARGET
    else:
        status = GoalProgressStatusV1.UNCHANGED
    model = numeric.as_dict()
    explanation: dict[str, object] = {
        "metric_id": model["metric_id"],
        "unit": model["unit"],
        "direction": model["direction"],
        "baseline": model["baseline"],
        "target": model["target"],
        "current_value": observation.numeric_observation.as_dict()["value"]
        if observation.numeric_observation is not None
        else None,
        "latest_observation_uuid": str(observation.id),
        "latest_observed_at": _format_timestamp(cast(datetime, observation.observed_at)),
        "baseline_distance": _decimal_text(baseline_distance),
        "current_distance": _decimal_text(current_distance),
    }
    return _result(
        request,
        status=status,
        current_goal_identity_fingerprint=current_goal_hash,
        active_definition=definition,
        projection=projection,
        definition_fingerprint=definition_fingerprint,
        current_observation_uuids=(cast(UUID, observation.id),),
        explanation=explanation,
    )


def _evaluate_milestones(
    request: GoalProgressRequestV1,
    *,
    current_goal_hash: str,
    definition: DefinitionRecordV1,
    definition_fingerprint: str,
    projection: _ObservationProjection,
) -> GoalProgressResultV1:
    milestone_set = definition.milestone_set
    if milestone_set is None:
        raise GoalProgressError(GoalProgressErrorCode.MODEL_UNSUPPORTED)
    by_milestone: defaultdict[str, list[ObservationRecordV1]] = defaultdict(list)
    for observation in projection.eligible:
        if observation.milestone_id is not None:
            by_milestone[observation.milestone_id].append(observation)
    current: dict[str, ObservationRecordV1] = {}
    reversal_ids: list[UUID] = []
    for milestone in milestone_set.milestones:
        values = by_milestone.get(milestone.id, [])
        if not values:
            continue
        latest_time = max(cast(datetime, item.observed_at) for item in values)
        latest = tuple(item for item in values if cast(datetime, item.observed_at) == latest_time)
        if len(latest) != 1:
            return _result(
                request,
                status=GoalProgressStatusV1.NOT_COMPARABLE,
                current_goal_identity_fingerprint=current_goal_hash,
                active_definition=definition,
                projection=projection,
                definition_fingerprint=definition_fingerprint,
            )
        current[milestone.id] = latest[0]
        if latest[0].state is MilestoneStateV1.NOT_COMPLETED and any(
            item.state is MilestoneStateV1.COMPLETED
            and cast(datetime, item.observed_at) < latest_time
            for item in values
        ):
            reversal_ids.append(cast(UUID, latest[0].id))
    completed = tuple(
        milestone.id
        for milestone in milestone_set.milestones
        if milestone.id in current and current[milestone.id].state is MilestoneStateV1.COMPLETED
    )
    not_completed = tuple(
        milestone.id
        for milestone in milestone_set.milestones
        if milestone.id in current and current[milestone.id].state is MilestoneStateV1.NOT_COMPLETED
    )
    missing = tuple(
        milestone.id for milestone in milestone_set.milestones if milestone.id not in current
    )
    if len(completed) == len(milestone_set.milestones):
        status = GoalProgressStatusV1.TARGET_MET
    elif reversal_ids:
        status = GoalProgressStatusV1.AWAY_FROM_TARGET
    elif not missing and not completed:
        status = GoalProgressStatusV1.UNCHANGED
    else:
        status = GoalProgressStatusV1.MILESTONE_OBSERVATIONS_AVAILABLE
    explanation: dict[str, object] = {
        "completed_milestone_ids": completed,
        "not_completed_milestone_ids": not_completed,
        "missing_milestone_ids": missing,
    }
    if reversal_ids:
        explanation["reversal_observation_uuids"] = tuple(
            str(value) for value in sorted(reversal_ids, key=str)
        )
    current_ids = tuple(
        cast(UUID, current[milestone.id].id)
        for milestone in milestone_set.milestones
        if milestone.id in current
    )
    return _result(
        request,
        status=status,
        current_goal_identity_fingerprint=current_goal_hash,
        active_definition=definition,
        projection=projection,
        definition_fingerprint=definition_fingerprint,
        current_observation_uuids=current_ids,
        explanation=explanation,
        completed_milestone_ids=completed,
        not_completed_milestone_ids=not_completed,
        missing_milestone_ids=missing,
    )


def _result(
    request: GoalProgressRequestV1,
    *,
    status: GoalProgressStatusV1,
    current_goal_identity_fingerprint: str | None,
    active_definition: DefinitionRecordV1 | None = None,
    projection: _ObservationProjection | None = None,
    excluded: tuple[GoalProgressExcludedObservationV1, ...] = (),
    definition_fingerprint: str | None = None,
    current_observation_uuids: tuple[UUID, ...] = (),
    explanation: Mapping[str, object] | None = None,
    completed_milestone_ids: tuple[str, ...] = (),
    not_completed_milestone_ids: tuple[str, ...] = (),
    missing_milestone_ids: tuple[str, ...] = (),
) -> GoalProgressResultV1:
    definition = active_definition
    return GoalProgressResultV1(
        selected_goal_source_uuid=cast(UUID, request.goal_source_uuid),
        current_goal_identity_fingerprint=current_goal_identity_fingerprint,
        goal_progress_policy_fingerprint=GOAL_PROGRESS_POLICY_FINGERPRINT,
        active_definition_uuid=cast(UUID | None, definition.id) if definition is not None else None,
        definition_fingerprint=(
            definition_fingerprint
            if definition_fingerprint is not None
            else definition.definition_fingerprint
            if definition is not None
            else None
        ),
        as_of=cast(datetime, request.as_of),
        progress_model=definition.progress_model if definition is not None else None,
        status=status,
        current_observation_uuids=current_observation_uuids,
        excluded_observations=projection.excluded if projection is not None else excluded,
        eligible_count=projection.eligible_count if projection is not None else 0,
        unknown_time_count=projection.unknown_time_count if projection is not None else 0,
        superseded_count=projection.superseded_count if projection is not None else 0,
        invalid_count=projection.invalid_count if projection is not None else 0,
        explanation=explanation or {},
        completed_milestone_ids=completed_milestone_ids,
        not_completed_milestone_ids=not_completed_milestone_ids,
        missing_milestone_ids=missing_milestone_ids,
    )


def _ensure_bounded_result(result: GoalProgressResultV1) -> GoalProgressResultV1:
    if len(result.excluded_observations) > MAX_GOAL_PROGRESS_EXCLUDED_REFERENCES:
        raise GoalProgressResultTooLargeError()
    return validate_goal_progress_result(result)


def _reason_for_observation_issues(issues: tuple[str, ...]) -> GoalProgressExclusionReasonV1:
    if "GOAL_PROGRESS_POLICY_MISMATCH" in issues:
        raise GoalProgressPolicyMismatchError()
    if "GOAL_PROGRESS_DEFINITION_NOT_FOUND" in issues:
        return GoalProgressExclusionReasonV1.DEFINITION_MISMATCH
    if "GOAL_PROGRESS_CHAIN_BINDING_MISMATCH" in issues:
        return GoalProgressExclusionReasonV1.GOAL_SOURCE_CHANGED
    if "GOAL_PROGRESS_INVALID_MODEL" in issues:
        return GoalProgressExclusionReasonV1.MODEL_MISMATCH
    return GoalProgressExclusionReasonV1.INVALID


def _observation_event_key(observation: ObservationRecordV1) -> tuple[object, ...]:
    """Return the exact event identity used to partition correction chains."""

    subject = (
        observation.milestone_id if observation.milestone_id is not None else observation.metric_id
    )
    event_time = (
        GOAL_PROGRESS_UNKNOWN_TIME
        if observation.observed_at == GOAL_PROGRESS_UNKNOWN_TIME
        else _format_timestamp(cast(datetime, observation.observed_at))
    )
    return cast(ProgressModelV1, observation.progress_model).value, subject, event_time


def _has_same_time_conflict(
    eligible: tuple[ObservationRecordV1, ...],
    definition: DefinitionRecordV1,
) -> bool:
    keys: defaultdict[tuple[object, ...], int] = defaultdict(int)
    for observation in eligible:
        if observation.observed_at == GOAL_PROGRESS_UNKNOWN_TIME:
            continue
        subject = (
            observation.metric_id
            if definition.progress_model is ProgressModelV1.NUMERIC_TARGET
            else observation.milestone_id
        )
        keys[(subject, cast(datetime, observation.observed_at))] += 1
    return any(value > 1 for value in keys.values())


def _sort_excluded(
    values: list[GoalProgressExcludedObservationV1],
) -> tuple[GoalProgressExcludedObservationV1, ...]:
    unique: dict[tuple[str | None, str], GoalProgressExcludedObservationV1] = {}
    for value in values:
        key = (
            str(value.observation_id) if value.observation_id is not None else None,
            cast(GoalProgressExclusionReasonV1, value.reason).value,
        )
        unique.setdefault(key, value)
    return tuple(unique[key] for key in sorted(unique, key=lambda item: (item[1], item[0] or "")))


def _normalize_error_code(code: GoalProgressErrorCode | str) -> GoalProgressErrorCode:
    if isinstance(code, GoalProgressErrorCode):
        return code
    try:
        return GoalProgressErrorCode(code)
    except TypeError, ValueError:
        return GoalProgressErrorCode.INTERNAL


def _parse_uuid(value: object) -> UUID:
    try:
        return parse_uuid7(value)
    except TypeError, ValueError, OverflowError:
        raise ValueError("GOAL_PROGRESS_REQUEST_INVALID") from None


def _parse_hash(value: object) -> str:
    if type(value) is not str or _HASH_PATTERN.fullmatch(value) is None:
        raise ValueError("GOAL_PROGRESS_INVALID_FIELD")
    return value


def _parse_enum(value: object, enum_type: type[StrEnum]) -> StrEnum:
    if isinstance(value, enum_type):
        return value
    if type(value) is not str:
        raise ValueError("GOAL_PROGRESS_INVALID_FIELD")
    try:
        return enum_type(value)
    except ValueError:
        raise ValueError("GOAL_PROGRESS_INVALID_FIELD") from None


def _parse_exact_utc(value: object) -> datetime:
    if type(value) is str:
        try:
            parsed = parse_rfc3339(value)
        except TypeError, ValueError, OverflowError:
            raise ValueError("GOAL_PROGRESS_REQUEST_INVALID") from None
    elif isinstance(value, datetime):
        parsed = value
    else:
        raise ValueError("GOAL_PROGRESS_REQUEST_INVALID")
    if (
        parsed.tzinfo is None
        or parsed.utcoffset() is None
        or parsed.utcoffset() != UTC.utcoffset(parsed)
    ):
        raise ValueError("GOAL_PROGRESS_REQUEST_INVALID")
    return parsed.astimezone(UTC)


def _format_timestamp(value: datetime) -> str:
    normalized = _parse_exact_utc(value)
    rendered = normalized.isoformat(
        timespec="microseconds" if normalized.microsecond else "seconds"
    )
    return rendered.removesuffix("+00:00") + "Z"


def _timestamp_projection(value: object) -> str | None:
    if value is None:
        return None
    try:
        return _format_timestamp(_parse_exact_utc(value))
    except TypeError, ValueError, OverflowError:
        return None


def _decimal_text(value: Decimal) -> str:
    rendered = format(value, "f")
    if rendered == "-0" or value == 0:
        return "0"
    if "." in rendered:
        rendered = rendered.rstrip("0").rstrip(".")
    return rendered


def _json_safe(value: object) -> object:
    if isinstance(value, Mapping):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_json_safe(item) for item in value]
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, datetime):
        return _format_timestamp(value)
    if isinstance(value, StrEnum):
        return value.value
    if isinstance(value, Decimal):
        return _decimal_text(value)
    return value


def _raw_field(note: NoteRecord, name: str) -> object:
    try:
        return note.front_matter.get(name)
    except AttributeError, TypeError:
        return None


def _safe_uuid(value: object) -> UUID | None:
    if value is None:
        return None
    try:
        return parse_uuid7(value)
    except TypeError, ValueError, OverflowError:
        return None


def _safe_hash(value: object) -> str | None:
    return value if type(value) is str and _HASH_PATTERN.fullmatch(value) else None


__all__ = [
    "GOAL_PROGRESS_RESULT_CONTRACT",
    "MAX_GOAL_PROGRESS_EXCLUDED_REFERENCES",
    "MAX_GOAL_PROGRESS_RESULT_BYTES",
    "BuildGoalProgress",
    "BuildGoalProgressV1",
    "GoalProgressBuilder",
    "GoalProgressError",
    "GoalProgressErrorCode",
    "GoalProgressExcludedObservation",
    "GoalProgressExcludedObservationV1",
    "GoalProgressExclusionReason",
    "GoalProgressExclusionReasonV1",
    "GoalProgressGoalAmbiguousError",
    "GoalProgressGoalRequiredError",
    "GoalProgressGoalSourceChangedError",
    "GoalProgressInvalidRequestError",
    "GoalProgressPolicyMismatchError",
    "GoalProgressProvenance",
    "GoalProgressProvenanceV1",
    "GoalProgressRequest",
    "GoalProgressRequestV1",
    "GoalProgressResult",
    "GoalProgressResultTooLargeError",
    "GoalProgressResultV1",
    "GoalProgressSourceUnavailableError",
    "GoalProgressStatus",
    "GoalProgressStatusV1",
    "build_goal_progress",
    "validate_goal_progress_request",
    "validate_goal_progress_result",
]
