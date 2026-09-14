"""Stage 12A canonical Goal Progress records and pure validators.

This module is intentionally a read-side foundation.  It parses only the
exact opt-in companion-record marker, validates immutable record branches and
provides deterministic identity/chain primitives.  It has no writer, network,
provider, clock or persistence capability.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from typing import Any, Final, Literal, cast
from uuid import UUID

from second_brain.domain.models import parse_rfc3339, parse_uuid7

# The contract requires bounded values but deliberately leaves the transport
# surface out of Stage 12A.  These limits follow the repository's existing
# 64-byte slug/domain and 64 KiB reviewed-text conventions.  They are parser
# safety bounds, not progress semantics.
MAX_GOAL_PROGRESS_SLUG_BYTES: Final[int] = 64
MAX_GOAL_PROGRESS_UNIT_BYTES: Final[int] = 64
MAX_GOAL_PROGRESS_MILESTONES: Final[int] = 200
MAX_GOAL_PROGRESS_MILESTONE_LABEL_BYTES: Final[int] = 4096
MAX_GOAL_PROGRESS_DECIMAL_BYTES: Final[int] = 256
MAX_GOAL_PROGRESS_TIMESTAMP_BYTES: Final[int] = 64
MAX_GOAL_PROGRESS_RECORD_BYTES: Final[int] = 64 * 1024

GOAL_PROGRESS_MARKER: Final[str] = "second_brain_goal_progress"
GOAL_PROGRESS_MARKER_VALUE: Final[int] = 1
GOAL_PROGRESS_UNKNOWN_TIME: Literal["unknown"] = "unknown"

# Descriptive aliases keep the bounds discoverable without exposing mutable
# configuration or introducing a second policy namespace.
GOAL_PROGRESS_SLUG_BYTES: Final[int] = MAX_GOAL_PROGRESS_SLUG_BYTES
GOAL_PROGRESS_UNIT_BYTES: Final[int] = MAX_GOAL_PROGRESS_UNIT_BYTES
GOAL_PROGRESS_MILESTONE_LABEL_BYTES: Final[int] = MAX_GOAL_PROGRESS_MILESTONE_LABEL_BYTES
GOAL_PROGRESS_RECORD_BYTES: Final[int] = MAX_GOAL_PROGRESS_RECORD_BYTES

_HASH_PATTERN: Final[re.Pattern[str]] = re.compile(r"sha256:[0-9a-f]{64}\Z", re.ASCII)
_SLUG_PATTERN: Final[re.Pattern[str]] = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*\Z", re.ASCII)
_UNIT_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"[A-Za-z0-9](?:[A-Za-z0-9._/%-]*[A-Za-z0-9])?\Z", re.ASCII
)
_DECIMAL_PATTERN: Final[re.Pattern[str]] = re.compile(r"[+-]?(?:0|[0-9]+)(?:\.[0-9]+)?\Z", re.ASCII)


class GoalProgressRecordKindV1(StrEnum):
    """The two exact companion-record kinds enrolled by Stage 12A."""

    DEFINITION = "definition"
    OBSERVATION = "observation"


GoalProgressRecordKind = GoalProgressRecordKindV1


class ProgressModelV1(StrEnum):
    """Closed v1 progress models."""

    NUMERIC_TARGET = "numeric_target"
    MILESTONE_SET = "milestone_set"


ProgressModel = ProgressModelV1


class NumericDirectionV1(StrEnum):
    """Closed numeric target direction vocabulary."""

    INCREASE_TO = "increase_to"
    DECREASE_TO = "decrease_to"
    REACH_EXACT = "reach_exact"


NumericDirection = NumericDirectionV1


class MilestoneStateV1(StrEnum):
    """Closed state for one reviewed milestone observation."""

    COMPLETED = "completed"
    NOT_COMPLETED = "not_completed"


MilestoneState = MilestoneStateV1


class GoalProgressBindingStateV1(StrEnum):
    """Pure Goal binding outcomes used by future read builders."""

    EXACT_CURRENT = "exact_current"
    SOURCE_MISSING = "source_missing"
    SOURCE_CHANGED = "source_changed"
    NON_CURRENT = "non_current"
    AMBIGUOUS = "ambiguous"


GoalProgressBindingState = GoalProgressBindingStateV1


class SupersessionChainStateV1(StrEnum):
    """Deterministic active-leaf states for a supplied record set."""

    ZERO_ACTIVE = "zero_active"
    ONE_ACTIVE = "one_active"
    MULTIPLE_ACTIVE = "multiple_active"
    CONFLICT = "conflict"


SupersessionChainState = SupersessionChainStateV1


class ObservationEligibilityReasonV1(StrEnum):
    """Eligibility primitives, deliberately separate from Stage 12C result statuses."""

    ELIGIBLE = "eligible"
    UNKNOWN_TIME = "unknown_time"
    FUTURE_AS_OF = "future_as_of"
    BEFORE_DEFINITION_REVIEW = "before_definition_review"
    SUPERSEDED = "superseded"
    INVALID = "invalid"


ObservationEligibilityReason = ObservationEligibilityReasonV1


class GoalProgressValidationError(ValueError):
    """Bounded validation error carrying a safe machine-readable code."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


class GoalProgressRecordError(GoalProgressValidationError):
    """Invalid marker-enrolled record; no raw metadata is retained."""


GOAL_PROGRESS_DIAGNOSTIC_MESSAGES: Final[dict[str, str]] = {
    "GOAL_PROGRESS_INVALID_RECORD": "Goal Progress record is invalid",
    "GOAL_PROGRESS_INVALID_KIND": "Goal Progress record kind is not supported",
    "GOAL_PROGRESS_MISSING_FIELD": "Goal Progress record is missing a required field",
    "GOAL_PROGRESS_INVALID_FIELD": "Goal Progress record contains an invalid field",
    "GOAL_PROGRESS_RECORD_TOO_LARGE": "Goal Progress record exceeds the maximum allowed size",
    "GOAL_PROGRESS_INVALID_MODEL": "Goal Progress model is not supported",
    "GOAL_PROGRESS_POLICY_MISMATCH": "Goal Progress policy binding is invalid",
    "GOAL_PROGRESS_DEFINITION_NOT_FOUND": "Goal Progress definition was not found",
    "GOAL_PROGRESS_OBSERVATION_INVALID": "Goal Progress observation is invalid",
    "GOAL_PROGRESS_DEFINITION_CONFLICT": "Goal Progress definitions have a chain conflict",
    "GOAL_PROGRESS_OBSERVATION_CONFLICT": "Goal Progress observations have a chain conflict",
    "GOAL_PROGRESS_CHAIN_PREDECESSOR_MISSING": "Goal Progress replacement predecessor is missing",
    "GOAL_PROGRESS_CHAIN_SELF_REFERENCE": "Goal Progress replacement cannot reference itself",
    "GOAL_PROGRESS_CHAIN_CYCLE": "Goal Progress replacement chain contains a cycle",
    "GOAL_PROGRESS_CHAIN_DUPLICATE_SUCCESSOR": "Goal Progress replacement has duplicate successors",
    "GOAL_PROGRESS_CHAIN_BINDING_MISMATCH": "Goal Progress replacement binding is invalid",
    "GOAL_PROGRESS_CHAIN_EVENT_MISMATCH": "Goal Progress observation replacement event is invalid",
}


GOAL_PROGRESS_POLICY_PAYLOAD: Final[dict[str, object]] = {
    "contract": "goal_progress_policy_v1",
    "baseline": "explicit_definition_only",
    "models": ["milestone_set", "numeric_target"],
    "numeric_directions": ["decrease_to", "increase_to", "reach_exact"],
    "qualitative_progress": "deferred",
    "maintain_range": "deferred",
    "multiple_trackers": "deferred",
    "percentage": "forbidden",
    "forecast": "forbidden",
    "provider": "forbidden",
    "version": 1,
}
GOAL_PROGRESS_POLICY_CANONICAL_JSON: Final[str] = (
    '{"baseline":"explicit_definition_only","contract":"goal_progress_policy_v1",'
    '"forecast":"forbidden","maintain_range":"deferred",'
    '"models":["milestone_set","numeric_target"],"multiple_trackers":"deferred",'
    '"numeric_directions":["decrease_to","increase_to","reach_exact"],'
    '"percentage":"forbidden","provider":"forbidden",'
    '"qualitative_progress":"deferred","version":1}'
)
# Fixed regression value from the exact payload above.  It is intentionally
# not computed as the public constant so policy drift cannot silently update
# its own expected fingerprint.
GOAL_PROGRESS_POLICY_FINGERPRINT: Final[str] = (
    "sha256:842eeea99d7902f221d5aa96935140ab79fb3c1ca0b959e9ef97b2abd492465f"
)


@dataclass(frozen=True, slots=True)
class GoalProgressPolicyV1:
    """The one compiled Stage 12 policy identity."""

    contract: str = "goal_progress_policy_v1"
    baseline: str = "explicit_definition_only"
    models: tuple[str, ...] = ("milestone_set", "numeric_target")
    numeric_directions: tuple[str, ...] = (
        "decrease_to",
        "increase_to",
        "reach_exact",
    )
    qualitative_progress: str = "deferred"
    maintain_range: str = "deferred"
    multiple_trackers: str = "deferred"
    percentage: str = "forbidden"
    forecast: str = "forbidden"
    provider: str = "forbidden"
    version: int = 1

    def as_dict(self) -> dict[str, object]:
        """Return the exact canonical policy payload."""

        return {
            "contract": self.contract,
            "baseline": self.baseline,
            "models": list(self.models),
            "numeric_directions": list(self.numeric_directions),
            "qualitative_progress": self.qualitative_progress,
            "maintain_range": self.maintain_range,
            "multiple_trackers": self.multiple_trackers,
            "percentage": self.percentage,
            "forecast": self.forecast,
            "provider": self.provider,
            "version": self.version,
        }


GOAL_PROGRESS_POLICY: Final[GoalProgressPolicyV1] = GoalProgressPolicyV1()
DEFAULT_GOAL_PROGRESS_POLICY: Final[GoalProgressPolicyV1] = GOAL_PROGRESS_POLICY


@dataclass(frozen=True, slots=True)
class NumericTargetDefinitionV1:
    """Explicit numeric target branch of a definition record."""

    metric_id: str
    unit: str
    baseline: Decimal | str | int
    target: Decimal | str | int
    direction: NumericDirectionV1 | str
    lower_bound: Decimal | str | int | None = None
    upper_bound: Decimal | str | int | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "metric_id", _parse_slug(self.metric_id, "metric_id"))
        object.__setattr__(self, "unit", _parse_unit(self.unit))
        baseline = _parse_decimal(self.baseline)
        target = _parse_decimal(self.target)
        lower = _parse_optional_decimal(self.lower_bound)
        upper = _parse_optional_decimal(self.upper_bound)
        direction = _parse_enum(self.direction, NumericDirectionV1, "direction")
        if lower is not None and upper is not None and lower > upper:
            raise GoalProgressValidationError("GOAL_PROGRESS_INVALID_FIELD")
        if direction is NumericDirectionV1.INCREASE_TO and not target > baseline:
            raise GoalProgressValidationError("GOAL_PROGRESS_INVALID_FIELD")
        if direction is NumericDirectionV1.DECREASE_TO and not target < baseline:
            raise GoalProgressValidationError("GOAL_PROGRESS_INVALID_FIELD")
        if direction is NumericDirectionV1.REACH_EXACT and target == baseline:
            raise GoalProgressValidationError("GOAL_PROGRESS_INVALID_FIELD")
        object.__setattr__(self, "baseline", baseline)
        object.__setattr__(self, "target", target)
        object.__setattr__(self, "direction", direction)
        object.__setattr__(self, "lower_bound", lower)
        object.__setattr__(self, "upper_bound", upper)

    def as_dict(self) -> dict[str, object]:
        """Return the canonical numeric branch projection."""

        return {
            "metric_id": self.metric_id,
            "unit": self.unit,
            "baseline": _format_decimal(cast(Decimal, self.baseline)),
            "target": _format_decimal(cast(Decimal, self.target)),
            "direction": cast(NumericDirectionV1, self.direction).value,
            "lower_bound": (
                _format_decimal(cast(Decimal, self.lower_bound))
                if self.lower_bound is not None
                else None
            ),
            "upper_bound": (
                _format_decimal(cast(Decimal, self.upper_bound))
                if self.upper_bound is not None
                else None
            ),
        }


@dataclass(frozen=True, slots=True)
class MilestoneV1:
    """One owner-reviewed milestone in display order."""

    id: str
    label: str
    ordinal: int

    def __post_init__(self) -> None:
        object.__setattr__(self, "id", _parse_slug(self.id, "milestone_id"))
        object.__setattr__(self, "label", _parse_label(self.label))
        if type(self.ordinal) is not int or self.ordinal <= 0:
            raise GoalProgressValidationError("GOAL_PROGRESS_INVALID_FIELD")

    def as_dict(self) -> dict[str, object]:
        """Return the exact milestone payload."""

        return {"id": self.id, "label": self.label, "ordinal": self.ordinal}


@dataclass(frozen=True, slots=True)
class MilestoneSetDefinitionV1:
    """Explicit unweighted milestone-set branch."""

    ordering: str
    milestones: tuple[MilestoneV1, ...]

    def __post_init__(self) -> None:
        if type(self.ordering) is not str or self.ordering != "display_only_v1":
            raise GoalProgressValidationError("GOAL_PROGRESS_INVALID_FIELD")
        if type(self.milestones) is not tuple:
            raise GoalProgressValidationError("GOAL_PROGRESS_INVALID_FIELD")
        if not 1 <= len(self.milestones) <= MAX_GOAL_PROGRESS_MILESTONES:
            raise GoalProgressValidationError("GOAL_PROGRESS_INVALID_FIELD")
        if any(type(item) is not MilestoneV1 for item in self.milestones):
            raise GoalProgressValidationError("GOAL_PROGRESS_INVALID_FIELD")
        ids = tuple(item.id for item in self.milestones)
        ordinals = tuple(item.ordinal for item in self.milestones)
        if len(ids) != len(set(ids)) or len(ordinals) != len(set(ordinals)):
            raise GoalProgressValidationError("GOAL_PROGRESS_INVALID_FIELD")
        object.__setattr__(
            self, "milestones", tuple(sorted(self.milestones, key=lambda x: x.ordinal))
        )

    def as_dict(self) -> dict[str, object]:
        """Return milestones in deterministic ordinal order."""

        return {
            "ordering": self.ordering,
            "milestones": [item.as_dict() for item in self.milestones],
        }


@dataclass(frozen=True, slots=True)
class NumericObservationV1:
    """Exact numeric value carried by one observation record."""

    metric_id: str
    unit: str
    value: Decimal | str | int

    def __post_init__(self) -> None:
        object.__setattr__(self, "metric_id", _parse_slug(self.metric_id, "metric_id"))
        object.__setattr__(self, "unit", _parse_unit(self.unit))
        object.__setattr__(self, "value", _parse_decimal(self.value))

    def as_dict(self) -> dict[str, object]:
        """Return the exact structured numeric value."""

        return {
            "metric_id": self.metric_id,
            "unit": self.unit,
            "value": _format_decimal(cast(Decimal, self.value)),
        }


@dataclass(frozen=True, slots=True)
class MilestoneObservationV1:
    """Exact state of one milestone at one reviewed event time."""

    milestone_id: str
    state: MilestoneStateV1 | str

    def __post_init__(self) -> None:
        object.__setattr__(self, "milestone_id", _parse_slug(self.milestone_id, "milestone_id"))
        object.__setattr__(self, "state", _parse_enum(self.state, MilestoneStateV1, "state"))

    def as_dict(self) -> dict[str, object]:
        """Return the exact structured milestone value."""

        return {
            "milestone_id": self.milestone_id,
            "state": cast(MilestoneStateV1, self.state).value,
        }


@dataclass(frozen=True, slots=True)
class DefinitionRecordV1:
    """Immutable canonical companion record describing one progress model."""

    id: UUID | str
    goal_source_uuid: UUID | str
    goal_identity_fingerprint: str
    goal_progress_policy_fingerprint: str
    definition_reviewed_at: datetime | str
    progress_model: ProgressModelV1 | str
    numeric_target: NumericTargetDefinitionV1 | None = None
    milestone_set: MilestoneSetDefinitionV1 | None = None
    supersedes_definition_id: UUID | str | None = None
    second_brain_goal_progress: int = GOAL_PROGRESS_MARKER_VALUE
    goal_progress_kind: GoalProgressRecordKindV1 | str = GoalProgressRecordKindV1.DEFINITION

    def __post_init__(self) -> None:
        object.__setattr__(self, "id", _parse_uuid(self.id))
        object.__setattr__(self, "goal_source_uuid", _parse_uuid(self.goal_source_uuid))
        object.__setattr__(
            self,
            "goal_identity_fingerprint",
            _parse_hash(self.goal_identity_fingerprint),
        )
        object.__setattr__(
            self,
            "goal_progress_policy_fingerprint",
            _parse_policy_fingerprint(self.goal_progress_policy_fingerprint),
        )
        object.__setattr__(
            self,
            "definition_reviewed_at",
            _parse_reviewed_timestamp(self.definition_reviewed_at),
        )
        object.__setattr__(
            self,
            "progress_model",
            _parse_enum(self.progress_model, ProgressModelV1, "progress_model"),
        )
        _validate_marker_and_kind(
            self.second_brain_goal_progress,
            self.goal_progress_kind,
            GoalProgressRecordKindV1.DEFINITION,
        )
        if (self.numeric_target is None) == (self.milestone_set is None):
            raise GoalProgressValidationError("GOAL_PROGRESS_INVALID_MODEL")
        if (
            self.numeric_target is not None
            and type(self.numeric_target) is not NumericTargetDefinitionV1
        ):
            raise GoalProgressValidationError("GOAL_PROGRESS_INVALID_FIELD")
        if (
            self.milestone_set is not None
            and type(self.milestone_set) is not MilestoneSetDefinitionV1
        ):
            raise GoalProgressValidationError("GOAL_PROGRESS_INVALID_FIELD")
        if self.progress_model is ProgressModelV1.NUMERIC_TARGET and self.numeric_target is None:
            raise GoalProgressValidationError("GOAL_PROGRESS_INVALID_MODEL")
        if self.progress_model is ProgressModelV1.MILESTONE_SET and self.milestone_set is None:
            raise GoalProgressValidationError("GOAL_PROGRESS_INVALID_MODEL")
        if self.supersedes_definition_id is not None:
            object.__setattr__(
                self, "supersedes_definition_id", _parse_uuid(self.supersedes_definition_id)
            )

    @property
    def metric_id(self) -> str | None:
        """Return numeric metric identity without making milestone semantics implicit."""

        return self.numeric_target.metric_id if self.numeric_target is not None else None

    @property
    def unit(self) -> str | None:
        """Return numeric unit, if this is a numeric definition."""

        return self.numeric_target.unit if self.numeric_target is not None else None

    @property
    def baseline(self) -> Decimal | None:
        """Return explicit numeric baseline, never an observation-derived value."""

        return (
            cast(Decimal, self.numeric_target.baseline) if self.numeric_target is not None else None
        )

    @property
    def target(self) -> Decimal | None:
        """Return explicit numeric target."""

        return (
            cast(Decimal, self.numeric_target.target) if self.numeric_target is not None else None
        )

    @property
    def direction(self) -> NumericDirectionV1 | None:
        """Return numeric direction, if present."""

        return (
            cast(NumericDirectionV1, self.numeric_target.direction)
            if self.numeric_target is not None
            else None
        )

    @property
    def lower_bound(self) -> Decimal | None:
        """Return inclusive lower bound, if present."""

        return (
            cast(Decimal | None, self.numeric_target.lower_bound)
            if self.numeric_target is not None
            else None
        )

    @property
    def upper_bound(self) -> Decimal | None:
        """Return inclusive upper bound, if present."""

        return (
            cast(Decimal | None, self.numeric_target.upper_bound)
            if self.numeric_target is not None
            else None
        )

    @property
    def ordering(self) -> str | None:
        """Return milestone ordering, if present."""

        return self.milestone_set.ordering if self.milestone_set is not None else None

    @property
    def milestones(self) -> tuple[MilestoneV1, ...]:
        """Return ordinal-sorted milestones or an empty numeric branch tuple."""

        return self.milestone_set.milestones if self.milestone_set is not None else ()

    def as_dict(self) -> dict[str, object]:
        """Return the deterministic read projection without storage metadata."""

        result: dict[str, object] = {
            "id": str(self.id),
            "second_brain_goal_progress": GOAL_PROGRESS_MARKER_VALUE,
            "goal_progress_kind": GoalProgressRecordKindV1.DEFINITION.value,
            "goal_source_uuid": str(self.goal_source_uuid),
            "goal_identity_fingerprint": self.goal_identity_fingerprint,
            "goal_progress_policy_fingerprint": self.goal_progress_policy_fingerprint,
            "definition_reviewed_at": _format_timestamp(
                cast(datetime, self.definition_reviewed_at)
            ),
            "progress_model": cast(ProgressModelV1, self.progress_model).value,
        }
        if self.numeric_target is not None:
            numeric_payload = self.numeric_target.as_dict()
            result.update(
                {
                    key: value
                    for key, value in numeric_payload.items()
                    if key not in {"lower_bound", "upper_bound"} or value is not None
                }
            )
        if self.milestone_set is not None:
            result.update(self.milestone_set.as_dict())
        if self.supersedes_definition_id is not None:
            result["supersedes_definition_id"] = str(self.supersedes_definition_id)
        return result

    def fingerprint_payload(self) -> dict[str, object]:
        """Return the exact DefinitionProgressFingerprintV1 payload."""

        model: dict[str, object]
        if self.numeric_target is not None:
            model = self.numeric_target.as_dict()
        else:
            assert self.milestone_set is not None
            model = self.milestone_set.as_dict()
        return {
            "contract": "goal_progress_definition_v1",
            "goal_identity_fingerprint": self.goal_identity_fingerprint,
            "goal_source_uuid": str(self.goal_source_uuid),
            "model": model,
            "progress_model": cast(ProgressModelV1, self.progress_model).value,
        }

    @property
    def definition_fingerprint(self) -> str:
        """Compute the semantic definition fingerprint on demand."""

        return goal_progress_hash_json(self.fingerprint_payload())


@dataclass(frozen=True, slots=True)
class ObservationRecordV1:
    """Immutable canonical companion record containing one observed value/state."""

    id: UUID | str
    goal_source_uuid: UUID | str
    goal_identity_fingerprint: str
    goal_progress_policy_fingerprint: str
    progress_definition_id: UUID | str
    definition_fingerprint: str
    progress_model: ProgressModelV1 | str
    observed_at: datetime | str
    observed_at_precision: str
    observation_reviewed_at: datetime | str
    numeric_observation: NumericObservationV1 | None = None
    milestone_observation: MilestoneObservationV1 | None = None
    supersedes_observation_id: UUID | str | None = None
    second_brain_goal_progress: int = GOAL_PROGRESS_MARKER_VALUE
    goal_progress_kind: GoalProgressRecordKindV1 | str = GoalProgressRecordKindV1.OBSERVATION

    def __post_init__(self) -> None:
        object.__setattr__(self, "id", _parse_uuid(self.id))
        object.__setattr__(self, "goal_source_uuid", _parse_uuid(self.goal_source_uuid))
        object.__setattr__(self, "progress_definition_id", _parse_uuid(self.progress_definition_id))
        object.__setattr__(
            self, "goal_identity_fingerprint", _parse_hash(self.goal_identity_fingerprint)
        )
        object.__setattr__(
            self,
            "goal_progress_policy_fingerprint",
            _parse_policy_fingerprint(self.goal_progress_policy_fingerprint),
        )
        object.__setattr__(self, "definition_fingerprint", _parse_hash(self.definition_fingerprint))
        object.__setattr__(
            self,
            "progress_model",
            _parse_enum(self.progress_model, ProgressModelV1, "progress_model"),
        )
        observed_at, precision = _parse_observation_time(
            self.observed_at, self.observed_at_precision
        )
        object.__setattr__(self, "observed_at", observed_at)
        object.__setattr__(self, "observed_at_precision", precision)
        object.__setattr__(
            self,
            "observation_reviewed_at",
            _parse_reviewed_timestamp(self.observation_reviewed_at),
        )
        _validate_marker_and_kind(
            self.second_brain_goal_progress,
            self.goal_progress_kind,
            GoalProgressRecordKindV1.OBSERVATION,
        )
        if (self.numeric_observation is None) == (self.milestone_observation is None):
            raise GoalProgressValidationError("GOAL_PROGRESS_INVALID_MODEL")
        if (
            self.numeric_observation is not None
            and type(self.numeric_observation) is not NumericObservationV1
        ):
            raise GoalProgressValidationError("GOAL_PROGRESS_INVALID_FIELD")
        if (
            self.milestone_observation is not None
            and type(self.milestone_observation) is not MilestoneObservationV1
        ):
            raise GoalProgressValidationError("GOAL_PROGRESS_INVALID_FIELD")
        if (
            self.progress_model is ProgressModelV1.NUMERIC_TARGET
            and self.numeric_observation is None
        ):
            raise GoalProgressValidationError("GOAL_PROGRESS_INVALID_MODEL")
        if (
            self.progress_model is ProgressModelV1.MILESTONE_SET
            and self.milestone_observation is None
        ):
            raise GoalProgressValidationError("GOAL_PROGRESS_INVALID_MODEL")
        if self.supersedes_observation_id is not None:
            object.__setattr__(
                self, "supersedes_observation_id", _parse_uuid(self.supersedes_observation_id)
            )

    @property
    def metric_id(self) -> str | None:
        """Return the exact numeric metric identity, if present."""

        return self.numeric_observation.metric_id if self.numeric_observation is not None else None

    @property
    def unit(self) -> str | None:
        """Return the exact numeric unit, if present."""

        return self.numeric_observation.unit if self.numeric_observation is not None else None

    @property
    def value(self) -> Decimal | None:
        """Return the exact numeric value, if present."""

        return (
            cast(Decimal, self.numeric_observation.value)
            if self.numeric_observation is not None
            else None
        )

    @property
    def milestone_id(self) -> str | None:
        """Return the exact milestone identity, if present."""

        return (
            self.milestone_observation.milestone_id
            if self.milestone_observation is not None
            else None
        )

    @property
    def state(self) -> MilestoneStateV1 | None:
        """Return the closed milestone state, if present."""

        return (
            cast(MilestoneStateV1, self.milestone_observation.state)
            if self.milestone_observation is not None
            else None
        )

    def as_dict(self) -> dict[str, object]:
        """Return the deterministic observation projection."""

        result: dict[str, object] = {
            "id": str(self.id),
            "second_brain_goal_progress": GOAL_PROGRESS_MARKER_VALUE,
            "goal_progress_kind": GoalProgressRecordKindV1.OBSERVATION.value,
            "goal_source_uuid": str(self.goal_source_uuid),
            "goal_identity_fingerprint": self.goal_identity_fingerprint,
            "goal_progress_policy_fingerprint": self.goal_progress_policy_fingerprint,
            "progress_definition_id": str(self.progress_definition_id),
            "definition_fingerprint": self.definition_fingerprint,
            "progress_model": cast(ProgressModelV1, self.progress_model).value,
            "observed_at": (
                _format_timestamp(cast(datetime, self.observed_at))
                if self.observed_at != GOAL_PROGRESS_UNKNOWN_TIME
                else GOAL_PROGRESS_UNKNOWN_TIME
            ),
            "observed_at_precision": self.observed_at_precision,
            "observation_reviewed_at": _format_timestamp(
                cast(datetime, self.observation_reviewed_at)
            ),
        }
        if self.numeric_observation is not None:
            result.update(self.numeric_observation.as_dict())
        if self.milestone_observation is not None:
            result.update(self.milestone_observation.as_dict())
        if self.supersedes_observation_id is not None:
            result["supersedes_observation_id"] = str(self.supersedes_observation_id)
        return result

    def fingerprint_payload(self) -> dict[str, object]:
        """Return the exact binding/value/event payload for observation identity."""

        if self.numeric_observation is not None:
            value = self.numeric_observation.as_dict()
        else:
            assert self.milestone_observation is not None
            value = self.milestone_observation.as_dict()
        return {
            "definition_fingerprint": self.definition_fingerprint,
            "goal_identity_fingerprint": self.goal_identity_fingerprint,
            "goal_progress_policy_fingerprint": self.goal_progress_policy_fingerprint,
            "goal_source_uuid": str(self.goal_source_uuid),
            "observed_at": (
                _format_timestamp(cast(datetime, self.observed_at))
                if self.observed_at != GOAL_PROGRESS_UNKNOWN_TIME
                else GOAL_PROGRESS_UNKNOWN_TIME
            ),
            "observed_at_precision": self.observed_at_precision,
            "progress_definition_id": str(self.progress_definition_id),
            "progress_model": cast(ProgressModelV1, self.progress_model).value,
            "value": value,
        }

    @property
    def observation_fingerprint(self) -> str:
        """Compute the semantic observation fingerprint on demand."""

        return goal_progress_hash_json(self.fingerprint_payload())


@dataclass(frozen=True, slots=True)
class GoalBindingValidationV1:
    """Result of exact current Goal binding validation."""

    state: GoalProgressBindingStateV1
    source_note_uuid: UUID

    @property
    def exact_current(self) -> bool:
        """Whether the reference is eligible for a current read model."""

        return self.state is GoalProgressBindingStateV1.EXACT_CURRENT


@dataclass(frozen=True, slots=True)
class ObservationEligibilityV1:
    """Pure as-of/time eligibility result for one validated observation."""

    observation_id: UUID
    reason: ObservationEligibilityReasonV1

    @property
    def eligible(self) -> bool:
        """Return whether this primitive may enter a future comparison."""

        return self.reason is ObservationEligibilityReasonV1.ELIGIBLE


@dataclass(frozen=True, slots=True)
class SupersessionChainResultV1:
    """Deterministic chain validation and active-leaf projection."""

    record_kind: GoalProgressRecordKindV1
    state: SupersessionChainStateV1
    active_records: tuple[DefinitionRecordV1 | ObservationRecordV1, ...]
    issues: tuple[str, ...] = ()


def is_goal_progress_enrolled(front_matter: Mapping[str, object] | object) -> bool:
    """Return true only for the exact YAML scalar integer marker ``1``."""

    if not isinstance(front_matter, Mapping):
        return False
    value = front_matter.get(GOAL_PROGRESS_MARKER)
    return type(value) is int and value == GOAL_PROGRESS_MARKER_VALUE


def canonical_goal_progress_json(value: object) -> str:
    """Serialize Stage 12 JSON with the repository-wide canonical profile."""

    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except TypeError, ValueError, UnicodeError:
        raise ValueError("value is not canonical Goal Progress JSON") from None


def goal_progress_hash_json(value: object) -> str:
    """Hash canonical UTF-8 JSON in the repository ``sha256:`` form."""

    return (
        "sha256:" + hashlib.sha256(canonical_goal_progress_json(value).encode("utf-8")).hexdigest()
    )


def validate_goal_progress_policy(
    policy: GoalProgressPolicyV1 = DEFAULT_GOAL_PROGRESS_POLICY,
) -> str:
    """Verify the fixed policy payload and return its expected fingerprint."""

    if type(policy) is not GoalProgressPolicyV1:
        raise GoalProgressValidationError("GOAL_PROGRESS_POLICY_MISMATCH")
    payload = policy.as_dict()
    if canonical_goal_progress_json(payload) != GOAL_PROGRESS_POLICY_CANONICAL_JSON:
        raise GoalProgressValidationError("GOAL_PROGRESS_POLICY_MISMATCH")
    if goal_progress_hash_json(payload) != GOAL_PROGRESS_POLICY_FINGERPRINT:
        raise GoalProgressValidationError("GOAL_PROGRESS_POLICY_MISMATCH")
    return GOAL_PROGRESS_POLICY_FINGERPRINT


def parse_goal_progress_record(
    front_matter: Mapping[str, object] | object,
    *,
    note_id: UUID | str | None = None,
) -> DefinitionRecordV1 | ObservationRecordV1 | None:
    """Parse one exact marker-enrolled record; ordinary notes return ``None``."""

    if not is_goal_progress_enrolled(front_matter):
        return None
    if not isinstance(front_matter, Mapping):
        raise GoalProgressRecordError("GOAL_PROGRESS_INVALID_RECORD")
    data = cast(Mapping[object, object], front_matter)
    kind = data.get("goal_progress_kind")
    if type(kind) is not str or kind not in {item.value for item in GoalProgressRecordKindV1}:
        raise GoalProgressRecordError("GOAL_PROGRESS_INVALID_KIND")
    try:
        record_id = _record_id(data, note_id)
        record: DefinitionRecordV1 | ObservationRecordV1
        if kind == GoalProgressRecordKindV1.DEFINITION.value:
            record = _parse_definition(data, record_id)
        else:
            record = _parse_observation(data, record_id)
        _validate_record_size(record)
        return record
    except GoalProgressRecordError:
        raise
    except GoalProgressValidationError as exc:
        raise GoalProgressRecordError(exc.code) from None
    except TypeError, ValueError, UnicodeError, OverflowError:
        raise GoalProgressRecordError("GOAL_PROGRESS_INVALID_RECORD") from None


def parse_definition_record(
    front_matter: Mapping[str, object] | object,
    *,
    note_id: UUID | str | None = None,
) -> DefinitionRecordV1 | None:
    """Parse a definition record or return ``None`` for non-definition notes."""

    record = parse_goal_progress_record(front_matter, note_id=note_id)
    if record is None:
        return None
    if type(record) is not DefinitionRecordV1:
        raise GoalProgressRecordError("GOAL_PROGRESS_INVALID_KIND")
    return record


def parse_observation_record(
    front_matter: Mapping[str, object] | object,
    *,
    note_id: UUID | str | None = None,
) -> ObservationRecordV1 | None:
    """Parse an observation record or return ``None`` for non-observation notes."""

    record = parse_goal_progress_record(front_matter, note_id=note_id)
    if record is None:
        return None
    if type(record) is not ObservationRecordV1:
        raise GoalProgressRecordError("GOAL_PROGRESS_INVALID_KIND")
    return record


def validate_observation_against_definition(
    observation: ObservationRecordV1,
    definition: DefinitionRecordV1,
) -> tuple[str, ...]:
    """Validate exact cross-record binding, branch semantics and bounds."""

    issues: list[str] = []
    if type(observation) is not ObservationRecordV1 or type(definition) is not DefinitionRecordV1:
        return ("GOAL_PROGRESS_OBSERVATION_INVALID",)
    if observation.goal_source_uuid != definition.goal_source_uuid:
        issues.append("GOAL_PROGRESS_CHAIN_BINDING_MISMATCH")
    if observation.goal_identity_fingerprint != definition.goal_identity_fingerprint:
        issues.append("GOAL_PROGRESS_CHAIN_BINDING_MISMATCH")
    if observation.goal_progress_policy_fingerprint != definition.goal_progress_policy_fingerprint:
        issues.append("GOAL_PROGRESS_POLICY_MISMATCH")
    if observation.progress_definition_id != definition.id:
        issues.append("GOAL_PROGRESS_DEFINITION_NOT_FOUND")
    if observation.definition_fingerprint != definition.definition_fingerprint:
        issues.append("GOAL_PROGRESS_OBSERVATION_INVALID")
    if observation.progress_model is not definition.progress_model:
        issues.append("GOAL_PROGRESS_INVALID_MODEL")
    if observation.numeric_observation is not None:
        if definition.numeric_target is None:
            issues.append("GOAL_PROGRESS_INVALID_MODEL")
        else:
            numeric = observation.numeric_observation
            target = definition.numeric_target
            if numeric.metric_id != target.metric_id or numeric.unit != target.unit:
                issues.append("GOAL_PROGRESS_OBSERVATION_INVALID")
            value = cast(Decimal, numeric.value)
            lower_bound = cast(Decimal | None, target.lower_bound)
            upper_bound = cast(Decimal | None, target.upper_bound)
            if lower_bound is not None and value < lower_bound:
                issues.append("GOAL_PROGRESS_OBSERVATION_INVALID")
            if upper_bound is not None and value > upper_bound:
                issues.append("GOAL_PROGRESS_OBSERVATION_INVALID")
    if observation.milestone_observation is not None:
        if definition.milestone_set is None:
            issues.append("GOAL_PROGRESS_INVALID_MODEL")
        elif observation.milestone_observation.milestone_id not in {
            item.id for item in definition.milestone_set.milestones
        }:
            issues.append("GOAL_PROGRESS_OBSERVATION_INVALID")
    return tuple(dict.fromkeys(issues))


def validate_goal_binding(
    source_note_uuid: UUID | str,
    goal_identity_fingerprint: str,
    *,
    current_goals: Iterable[object],
    known_goals: Iterable[object] = (),
    explicit_goal_source_uuid: UUID | str | None = None,
) -> GoalBindingValidationV1:
    """Classify a record against exact current Stage 11A Goal identities.

    ``known_goals`` is optional historical/source availability context.  It is
    intentionally separate from current goals so missing, changed and
    non-current states cannot be silently collapsed into one fallback.
    """

    source_uuid = _parse_uuid(source_note_uuid)
    source_hash = _parse_hash(goal_identity_fingerprint)
    current = _validated_goal_identities(current_goals)
    known = _validated_goal_identities(known_goals)
    if explicit_goal_source_uuid is not None:
        selected_uuid = _parse_uuid(explicit_goal_source_uuid)
        if selected_uuid != source_uuid:
            return GoalBindingValidationV1(GoalProgressBindingStateV1.NON_CURRENT, source_uuid)
    else:
        selected_uuid = source_uuid
    matching_current = tuple(goal for goal in current if _goal_uuid(goal) == selected_uuid)
    if len(matching_current) > 1:
        return GoalBindingValidationV1(GoalProgressBindingStateV1.AMBIGUOUS, source_uuid)
    if not matching_current:
        matching_known = tuple(goal for goal in known if _goal_uuid(goal) == selected_uuid)
        if matching_known:
            return GoalBindingValidationV1(GoalProgressBindingStateV1.NON_CURRENT, source_uuid)
        if current and any(_goal_uuid(goal) != selected_uuid for goal in current):
            return GoalBindingValidationV1(GoalProgressBindingStateV1.NON_CURRENT, source_uuid)
        return GoalBindingValidationV1(GoalProgressBindingStateV1.SOURCE_MISSING, source_uuid)
    if _goal_hash(matching_current[0]) != source_hash:
        return GoalBindingValidationV1(GoalProgressBindingStateV1.SOURCE_CHANGED, source_uuid)
    return GoalBindingValidationV1(GoalProgressBindingStateV1.EXACT_CURRENT, source_uuid)


def validate_definition_binding(
    definition: DefinitionRecordV1,
    *,
    current_goals: Iterable[object],
    known_goals: Iterable[object] = (),
    explicit_goal_source_uuid: UUID | str | None = None,
) -> GoalBindingValidationV1:
    """Classify one definition's exact Goal binding."""

    return validate_goal_binding(
        definition.goal_source_uuid,
        definition.goal_identity_fingerprint,
        current_goals=current_goals,
        known_goals=known_goals,
        explicit_goal_source_uuid=explicit_goal_source_uuid,
    )


def validate_observation_binding(
    observation: ObservationRecordV1,
    *,
    current_goals: Iterable[object],
    known_goals: Iterable[object] = (),
    explicit_goal_source_uuid: UUID | str | None = None,
) -> GoalBindingValidationV1:
    """Classify one observation's exact Goal binding."""

    return validate_goal_binding(
        observation.goal_source_uuid,
        observation.goal_identity_fingerprint,
        current_goals=current_goals,
        known_goals=known_goals,
        explicit_goal_source_uuid=explicit_goal_source_uuid,
    )


def evaluate_observation_eligibility(
    observation: ObservationRecordV1,
    definition: DefinitionRecordV1,
    *,
    as_of: datetime | str,
    superseded: bool = False,
) -> ObservationEligibilityV1:
    """Return pure exact-time/as-of eligibility for a validated observation."""

    if type(observation) is not ObservationRecordV1 or type(definition) is not DefinitionRecordV1:
        raise GoalProgressValidationError("GOAL_PROGRESS_OBSERVATION_INVALID")
    cutoff = _parse_reviewed_timestamp(as_of)
    observation_id = cast(UUID, observation.id)
    if superseded:
        return ObservationEligibilityV1(
            observation_id,
            ObservationEligibilityReasonV1.SUPERSEDED,
        )
    if validate_observation_against_definition(observation, definition):
        return ObservationEligibilityV1(observation_id, ObservationEligibilityReasonV1.INVALID)
    if observation.observed_at == GOAL_PROGRESS_UNKNOWN_TIME:
        return ObservationEligibilityV1(
            observation_id,
            ObservationEligibilityReasonV1.UNKNOWN_TIME,
        )
    observed_at = cast(datetime, observation.observed_at)
    definition_reviewed_at = cast(datetime, definition.definition_reviewed_at)
    if observed_at > cutoff:
        return ObservationEligibilityV1(
            observation_id,
            ObservationEligibilityReasonV1.FUTURE_AS_OF,
        )
    if observed_at < definition_reviewed_at:
        return ObservationEligibilityV1(
            observation_id,
            ObservationEligibilityReasonV1.BEFORE_DEFINITION_REVIEW,
        )
    return ObservationEligibilityV1(observation_id, ObservationEligibilityReasonV1.ELIGIBLE)


def is_observation_eligible(
    observation: ObservationRecordV1,
    definition: DefinitionRecordV1,
    *,
    as_of: datetime | str,
    superseded: bool = False,
) -> bool:
    """Boolean convenience wrapper over :func:`evaluate_observation_eligibility`."""

    return evaluate_observation_eligibility(
        observation,
        definition,
        as_of=as_of,
        superseded=superseded,
    ).eligible


def validate_definition_chain(
    records: Iterable[DefinitionRecordV1],
) -> SupersessionChainResultV1:
    """Validate one exact-Goal definition replacement chain without tie-breaking."""

    normalized = tuple(sorted(records, key=lambda item: str(item.id)))
    issues: list[str] = []
    if any(type(item) is not DefinitionRecordV1 for item in normalized):
        return SupersessionChainResultV1(
            GoalProgressRecordKindV1.DEFINITION,
            SupersessionChainStateV1.CONFLICT,
            (),
            ("GOAL_PROGRESS_INVALID_RECORD",),
        )
    by_id: dict[UUID, DefinitionRecordV1] = {}
    for item in normalized:
        item_id = cast(UUID, item.id)
        if item_id in by_id:
            issues.append("GOAL_PROGRESS_CHAIN_DUPLICATE_SUCCESSOR")
        by_id[item_id] = item
    if normalized:
        first = normalized[0]
        if any(
            item.goal_source_uuid != first.goal_source_uuid
            or item.goal_identity_fingerprint != first.goal_identity_fingerprint
            or item.goal_progress_policy_fingerprint != first.goal_progress_policy_fingerprint
            for item in normalized[1:]
        ):
            issues.append("GOAL_PROGRESS_CHAIN_BINDING_MISMATCH")
    successor_counts: defaultdict[UUID, int] = defaultdict(int)
    for item in normalized:
        predecessor_id = cast(UUID | None, item.supersedes_definition_id)
        if predecessor_id is None:
            continue
        if predecessor_id == item.id:
            issues.append("GOAL_PROGRESS_CHAIN_SELF_REFERENCE")
        predecessor = by_id.get(predecessor_id)
        if predecessor is None:
            issues.append("GOAL_PROGRESS_CHAIN_PREDECESSOR_MISSING")
            continue
        successor_counts[predecessor_id] += 1
        if (
            item.goal_source_uuid != predecessor.goal_source_uuid
            or item.goal_identity_fingerprint != predecessor.goal_identity_fingerprint
            or item.goal_progress_policy_fingerprint != predecessor.goal_progress_policy_fingerprint
        ):
            issues.append("GOAL_PROGRESS_CHAIN_BINDING_MISMATCH")
    if any(count > 1 for count in successor_counts.values()):
        issues.append("GOAL_PROGRESS_CHAIN_DUPLICATE_SUCCESSOR")
    issues.extend(_cycle_issues(normalized, lambda item: item.supersedes_definition_id))
    issues = list(dict.fromkeys(issues))
    if issues:
        return SupersessionChainResultV1(
            GoalProgressRecordKindV1.DEFINITION,
            SupersessionChainStateV1.CONFLICT,
            (),
            tuple(issues),
        )
    active = tuple(item for item in normalized if cast(UUID, item.id) not in successor_counts)
    return SupersessionChainResultV1(
        GoalProgressRecordKindV1.DEFINITION,
        _chain_state(len(active)),
        active,
    )


def validate_observation_chain(
    records: Iterable[ObservationRecordV1],
) -> SupersessionChainResultV1:
    """Validate observation corrections while retaining distinct event leaves."""

    normalized = tuple(sorted(records, key=lambda item: str(item.id)))
    issues: list[str] = []
    if any(type(item) is not ObservationRecordV1 for item in normalized):
        return SupersessionChainResultV1(
            GoalProgressRecordKindV1.OBSERVATION,
            SupersessionChainStateV1.CONFLICT,
            (),
            ("GOAL_PROGRESS_INVALID_RECORD",),
        )
    if normalized:
        first = normalized[0]
        for item in normalized[1:]:
            if (
                item.goal_source_uuid != first.goal_source_uuid
                or item.goal_identity_fingerprint != first.goal_identity_fingerprint
                or item.goal_progress_policy_fingerprint != first.goal_progress_policy_fingerprint
            ):
                issues.append("GOAL_PROGRESS_CHAIN_BINDING_MISMATCH")
            if (
                item.progress_definition_id != first.progress_definition_id
                or item.definition_fingerprint != first.definition_fingerprint
                or item.progress_model is not first.progress_model
            ):
                issues.append("GOAL_PROGRESS_CHAIN_EVENT_MISMATCH")
    by_id: dict[UUID, ObservationRecordV1] = {}
    for item in normalized:
        item_id = cast(UUID, item.id)
        if item_id in by_id:
            issues.append("GOAL_PROGRESS_CHAIN_DUPLICATE_SUCCESSOR")
        by_id[item_id] = item
    successor_counts: defaultdict[UUID, int] = defaultdict(int)
    for item in normalized:
        predecessor_id = cast(UUID | None, item.supersedes_observation_id)
        if predecessor_id is None:
            continue
        if predecessor_id == item.id:
            issues.append("GOAL_PROGRESS_CHAIN_SELF_REFERENCE")
        predecessor = by_id.get(predecessor_id)
        if predecessor is None:
            issues.append("GOAL_PROGRESS_CHAIN_PREDECESSOR_MISSING")
            continue
        successor_counts[predecessor_id] += 1
        if (
            item.goal_source_uuid != predecessor.goal_source_uuid
            or item.goal_identity_fingerprint != predecessor.goal_identity_fingerprint
            or item.goal_progress_policy_fingerprint != predecessor.goal_progress_policy_fingerprint
            or item.progress_definition_id != predecessor.progress_definition_id
            or item.definition_fingerprint != predecessor.definition_fingerprint
            or item.progress_model is not predecessor.progress_model
            or item.observed_at != predecessor.observed_at
            or item.observed_at_precision != predecessor.observed_at_precision
        ):
            issues.append("GOAL_PROGRESS_CHAIN_EVENT_MISMATCH")
    if any(count > 1 for count in successor_counts.values()):
        issues.append("GOAL_PROGRESS_CHAIN_DUPLICATE_SUCCESSOR")
    issues.extend(_cycle_issues(normalized, lambda item: item.supersedes_observation_id))
    issues = list(dict.fromkeys(issues))
    if issues:
        return SupersessionChainResultV1(
            GoalProgressRecordKindV1.OBSERVATION,
            SupersessionChainStateV1.CONFLICT,
            (),
            tuple(issues),
        )
    active = tuple(item for item in normalized if cast(UUID, item.id) not in successor_counts)
    event_keys: defaultdict[tuple[object, ...], int] = defaultdict(int)
    for item in active:
        event_keys[_observation_event_key(item)] += 1
    if any(count > 1 for count in event_keys.values()):
        return SupersessionChainResultV1(
            GoalProgressRecordKindV1.OBSERVATION,
            SupersessionChainStateV1.CONFLICT,
            (),
            ("GOAL_PROGRESS_CHAIN_DUPLICATE_SUCCESSOR",),
        )
    return SupersessionChainResultV1(
        GoalProgressRecordKindV1.OBSERVATION,
        _chain_state(len(active)),
        active,
    )


def validate_replacement_chain(
    records: Iterable[DefinitionRecordV1 | ObservationRecordV1],
) -> SupersessionChainResultV1:
    """Dispatch chain validation without selecting a newest record."""

    values = tuple(records)
    if not values:
        raise GoalProgressValidationError("GOAL_PROGRESS_INVALID_RECORD")
    if all(type(item) is DefinitionRecordV1 for item in values):
        return validate_definition_chain(cast(Iterable[DefinitionRecordV1], values))
    if all(type(item) is ObservationRecordV1 for item in values):
        return validate_observation_chain(cast(Iterable[ObservationRecordV1], values))
    return SupersessionChainResultV1(
        GoalProgressRecordKindV1.DEFINITION,
        SupersessionChainStateV1.CONFLICT,
        (),
        ("GOAL_PROGRESS_INVALID_RECORD",),
    )


def _parse_definition(data: Mapping[object, object], record_id: UUID) -> DefinitionRecordV1:
    model = _required(data, "progress_model")
    if type(model) is not str:
        raise GoalProgressRecordError("GOAL_PROGRESS_INVALID_MODEL")
    if model == ProgressModelV1.NUMERIC_TARGET.value:
        _reject_mixed_branch_fields(data, _MILESTONE_FIELDS)
        numeric = NumericTargetDefinitionV1(
            metric_id=cast(str, _required(data, "metric_id")),
            unit=cast(str, _required(data, "unit")),
            baseline=cast(Decimal | str | int, _required(data, "baseline")),
            target=cast(Decimal | str | int, _required(data, "target")),
            direction=cast(
                NumericDirectionV1 | str,
                _required(data, "direction"),
            ),
            lower_bound=_optional_decimal_field(data, "lower_bound"),
            upper_bound=_optional_decimal_field(data, "upper_bound"),
        )
        return DefinitionRecordV1(
            id=record_id,
            goal_source_uuid=cast(UUID | str, _required(data, "goal_source_uuid")),
            goal_identity_fingerprint=cast(str, _required(data, "goal_identity_fingerprint")),
            goal_progress_policy_fingerprint=cast(
                str,
                _required(data, "goal_progress_policy_fingerprint"),
            ),
            definition_reviewed_at=cast(
                datetime | str,
                _required(data, "definition_reviewed_at"),
            ),
            progress_model=model,
            numeric_target=numeric,
            supersedes_definition_id=_optional_uuid_field(data, "supersedes_definition_id"),
        )
    if model == ProgressModelV1.MILESTONE_SET.value:
        _reject_mixed_branch_fields(data, _NUMERIC_FIELDS)
        milestones = _parse_milestones(_required(data, "milestones"))
        milestone_set = MilestoneSetDefinitionV1(
            ordering=cast(str, _required(data, "ordering")),
            milestones=milestones,
        )
        return DefinitionRecordV1(
            id=record_id,
            goal_source_uuid=cast(UUID | str, _required(data, "goal_source_uuid")),
            goal_identity_fingerprint=cast(str, _required(data, "goal_identity_fingerprint")),
            goal_progress_policy_fingerprint=cast(
                str,
                _required(data, "goal_progress_policy_fingerprint"),
            ),
            definition_reviewed_at=cast(
                datetime | str,
                _required(data, "definition_reviewed_at"),
            ),
            progress_model=model,
            milestone_set=milestone_set,
            supersedes_definition_id=_optional_uuid_field(data, "supersedes_definition_id"),
        )
    raise GoalProgressRecordError("GOAL_PROGRESS_INVALID_MODEL")


def _validate_record_size(record: DefinitionRecordV1 | ObservationRecordV1) -> None:
    try:
        record_size = len(canonical_goal_progress_json(record.as_dict()).encode("utf-8"))
    except TypeError, ValueError, UnicodeError, OverflowError:
        raise GoalProgressRecordError("GOAL_PROGRESS_INVALID_RECORD") from None
    if record_size > MAX_GOAL_PROGRESS_RECORD_BYTES:
        raise GoalProgressRecordError("GOAL_PROGRESS_RECORD_TOO_LARGE")


def _parse_observation(data: Mapping[object, object], record_id: UUID) -> ObservationRecordV1:
    model = _required(data, "progress_model")
    if type(model) is not str:
        raise GoalProgressRecordError("GOAL_PROGRESS_INVALID_MODEL")
    goal_source_uuid = cast(UUID | str, _required(data, "goal_source_uuid"))
    goal_identity_fingerprint = cast(str, _required(data, "goal_identity_fingerprint"))
    goal_progress_policy_fingerprint = cast(
        str,
        _required(data, "goal_progress_policy_fingerprint"),
    )
    progress_definition_id = cast(UUID | str, _required(data, "progress_definition_id"))
    definition_fingerprint = cast(str, _required(data, "definition_fingerprint"))
    observed_at = cast(datetime | str, _required(data, "observed_at"))
    observed_at_precision = cast(str, _required(data, "observed_at_precision"))
    observation_reviewed_at = cast(
        datetime | str,
        _required(data, "observation_reviewed_at"),
    )
    supersedes_observation_id = _optional_uuid_field(data, "supersedes_observation_id")
    if model == ProgressModelV1.NUMERIC_TARGET.value:
        _reject_mixed_branch_fields(data, _MILESTONE_OBSERVATION_FIELDS)
        numeric_observation = NumericObservationV1(
            metric_id=cast(str, _required(data, "metric_id")),
            unit=cast(str, _required(data, "unit")),
            value=cast(Decimal | str | int, _required(data, "value")),
        )
        return ObservationRecordV1(
            id=record_id,
            goal_source_uuid=goal_source_uuid,
            goal_identity_fingerprint=goal_identity_fingerprint,
            goal_progress_policy_fingerprint=goal_progress_policy_fingerprint,
            progress_definition_id=progress_definition_id,
            definition_fingerprint=definition_fingerprint,
            progress_model=model,
            observed_at=observed_at,
            observed_at_precision=observed_at_precision,
            observation_reviewed_at=observation_reviewed_at,
            numeric_observation=numeric_observation,
            supersedes_observation_id=supersedes_observation_id,
        )
    if model == ProgressModelV1.MILESTONE_SET.value:
        _reject_mixed_branch_fields(data, _NUMERIC_OBSERVATION_FIELDS)
        milestone_observation = MilestoneObservationV1(
            milestone_id=cast(str, _required(data, "milestone_id")),
            state=cast(
                MilestoneStateV1 | str,
                _required(data, "state"),
            ),
        )
        return ObservationRecordV1(
            id=record_id,
            goal_source_uuid=goal_source_uuid,
            goal_identity_fingerprint=goal_identity_fingerprint,
            goal_progress_policy_fingerprint=goal_progress_policy_fingerprint,
            progress_definition_id=progress_definition_id,
            definition_fingerprint=definition_fingerprint,
            progress_model=model,
            observed_at=observed_at,
            observed_at_precision=observed_at_precision,
            observation_reviewed_at=observation_reviewed_at,
            milestone_observation=milestone_observation,
            supersedes_observation_id=supersedes_observation_id,
        )
    raise GoalProgressRecordError("GOAL_PROGRESS_INVALID_MODEL")


_NUMERIC_FIELDS: Final[frozenset[str]] = frozenset(
    {"metric_id", "unit", "baseline", "target", "direction", "lower_bound", "upper_bound"}
)
_MILESTONE_FIELDS: Final[frozenset[str]] = frozenset({"ordering", "milestones"})
_NUMERIC_OBSERVATION_FIELDS: Final[frozenset[str]] = frozenset({"metric_id", "unit", "value"})
_MILESTONE_OBSERVATION_FIELDS: Final[frozenset[str]] = frozenset({"milestone_id", "state"})


def _required(data: Mapping[object, object], name: str) -> object:
    if name not in data or data[name] is None:
        raise GoalProgressRecordError("GOAL_PROGRESS_MISSING_FIELD")
    return data[name]


def _optional_decimal_field(data: Mapping[object, object], name: str) -> Decimal | str | int | None:
    if name not in data:
        return None
    if data[name] is None:
        raise GoalProgressRecordError("GOAL_PROGRESS_INVALID_FIELD")
    return cast(Decimal | str | int, data[name])


def _optional_uuid_field(data: Mapping[object, object], name: str) -> UUID | str | None:
    if name not in data:
        return None
    if data[name] is None:
        raise GoalProgressRecordError("GOAL_PROGRESS_INVALID_FIELD")
    return cast(UUID | str, data[name])


def _record_id(data: Mapping[object, object], note_id: UUID | str | None) -> UUID:
    if note_id is None:
        if "id" not in data:
            raise GoalProgressRecordError("GOAL_PROGRESS_MISSING_FIELD")
        raw_id = data["id"]
    else:
        raw_id = note_id
        if "id" in data:
            try:
                if _parse_uuid(data["id"]) != _parse_uuid(note_id):
                    raise GoalProgressRecordError("GOAL_PROGRESS_INVALID_FIELD")
            except GoalProgressValidationError:
                raise
    try:
        return _parse_uuid(raw_id)
    except GoalProgressValidationError:
        raise GoalProgressRecordError("GOAL_PROGRESS_INVALID_FIELD") from None


def _parse_milestones(value: object) -> tuple[MilestoneV1, ...]:
    if type(value) is not list:
        raise GoalProgressRecordError("GOAL_PROGRESS_INVALID_FIELD")
    parsed: list[MilestoneV1] = []
    for item in value:
        if not isinstance(item, Mapping) or set(item) != {"id", "label", "ordinal"}:
            raise GoalProgressRecordError("GOAL_PROGRESS_INVALID_FIELD")
        try:
            parsed.append(MilestoneV1(item["id"], item["label"], item["ordinal"]))
        except GoalProgressValidationError, TypeError, ValueError, UnicodeError:
            raise GoalProgressRecordError("GOAL_PROGRESS_INVALID_FIELD") from None
    try:
        return tuple(parsed)
    except TypeError, ValueError:
        raise GoalProgressRecordError("GOAL_PROGRESS_INVALID_FIELD") from None


def _reject_mixed_branch_fields(data: Mapping[object, object], fields: frozenset[str]) -> None:
    if any(field in data for field in fields):
        raise GoalProgressRecordError("GOAL_PROGRESS_INVALID_FIELD")


def _validate_marker_and_kind(
    value: object, kind: object, expected: GoalProgressRecordKindV1
) -> None:
    if type(value) is not int or value != GOAL_PROGRESS_MARKER_VALUE:
        raise GoalProgressValidationError("GOAL_PROGRESS_INVALID_FIELD")
    if _parse_enum(kind, GoalProgressRecordKindV1, "goal_progress_kind") is not expected:
        raise GoalProgressValidationError("GOAL_PROGRESS_INVALID_KIND")


def _parse_uuid(value: object) -> UUID:
    try:
        return parse_uuid7(value)
    except TypeError, ValueError, OverflowError:
        raise GoalProgressValidationError("GOAL_PROGRESS_INVALID_FIELD") from None


def _parse_hash(value: object) -> str:
    if type(value) is not str or _HASH_PATTERN.fullmatch(value) is None:
        raise GoalProgressValidationError("GOAL_PROGRESS_INVALID_FIELD")
    return value


def _parse_policy_fingerprint(value: object) -> str:
    parsed = _parse_hash(value)
    if parsed != GOAL_PROGRESS_POLICY_FINGERPRINT:
        raise GoalProgressValidationError("GOAL_PROGRESS_POLICY_MISMATCH")
    return parsed


def _parse_slug(value: object, field: str) -> str:
    if type(value) is not str:
        raise GoalProgressValidationError("GOAL_PROGRESS_INVALID_FIELD")
    try:
        if len(value.encode("ascii")) > MAX_GOAL_PROGRESS_SLUG_BYTES:
            raise GoalProgressValidationError("GOAL_PROGRESS_INVALID_FIELD")
    except UnicodeEncodeError:
        raise GoalProgressValidationError("GOAL_PROGRESS_INVALID_FIELD") from None
    if _SLUG_PATTERN.fullmatch(value) is None:
        raise GoalProgressValidationError("GOAL_PROGRESS_INVALID_FIELD")
    return value


def _parse_unit(value: object) -> str:
    if type(value) is not str:
        raise GoalProgressValidationError("GOAL_PROGRESS_INVALID_FIELD")
    try:
        encoded = value.encode("ascii")
    except UnicodeEncodeError:
        raise GoalProgressValidationError("GOAL_PROGRESS_INVALID_FIELD") from None
    if (
        not 1 <= len(encoded) <= MAX_GOAL_PROGRESS_UNIT_BYTES
        or _UNIT_PATTERN.fullmatch(value) is None
    ):
        raise GoalProgressValidationError("GOAL_PROGRESS_INVALID_FIELD")
    return value


def _parse_label(value: object) -> str:
    if type(value) is not str or not value.strip():
        raise GoalProgressValidationError("GOAL_PROGRESS_INVALID_FIELD")
    try:
        encoded = value.encode("utf-8")
    except UnicodeEncodeError:
        raise GoalProgressValidationError("GOAL_PROGRESS_INVALID_FIELD") from None
    if len(encoded) > MAX_GOAL_PROGRESS_MILESTONE_LABEL_BYTES:
        raise GoalProgressValidationError("GOAL_PROGRESS_INVALID_FIELD")
    if any(ord(char) < 0x20 or ord(char) == 0x7F for char in value):
        raise GoalProgressValidationError("GOAL_PROGRESS_INVALID_FIELD")
    return value


def _parse_decimal(value: object) -> Decimal:
    if type(value) is bool or isinstance(value, float):
        raise GoalProgressValidationError("GOAL_PROGRESS_INVALID_FIELD")
    if type(value) is int:
        text = str(value)
    elif type(value) is str:
        text = value
    elif type(value) is Decimal:
        if not value.is_finite():
            raise GoalProgressValidationError("GOAL_PROGRESS_INVALID_FIELD")
        text = format(value, "f")
    else:
        raise GoalProgressValidationError("GOAL_PROGRESS_INVALID_FIELD")
    try:
        text_bytes = text.encode("ascii", "strict")
    except UnicodeEncodeError:
        raise GoalProgressValidationError("GOAL_PROGRESS_INVALID_FIELD") from None
    if not text or len(text_bytes) > MAX_GOAL_PROGRESS_DECIMAL_BYTES:
        raise GoalProgressValidationError("GOAL_PROGRESS_INVALID_FIELD")
    if _DECIMAL_PATTERN.fullmatch(text) is None:
        raise GoalProgressValidationError("GOAL_PROGRESS_INVALID_FIELD")
    try:
        result = Decimal(text)
    except InvalidOperation, ValueError:
        raise GoalProgressValidationError("GOAL_PROGRESS_INVALID_FIELD") from None
    if not result.is_finite():
        raise GoalProgressValidationError("GOAL_PROGRESS_INVALID_FIELD")
    if len(_format_decimal(result).encode("ascii")) > MAX_GOAL_PROGRESS_DECIMAL_BYTES:
        raise GoalProgressValidationError("GOAL_PROGRESS_INVALID_FIELD")
    return result


def _parse_optional_decimal(value: object) -> Decimal | None:
    if value is None:
        return None
    return _parse_decimal(value)


def _format_decimal(value: Decimal) -> str:
    if not isinstance(value, Decimal) or not value.is_finite():
        raise GoalProgressValidationError("GOAL_PROGRESS_INVALID_FIELD")
    if value == 0:
        return "0"
    rendered = format(value, "f")
    if "." in rendered:
        rendered = rendered.rstrip("0").rstrip(".")
    if rendered in {"", "-0", "+0"}:
        return "0"
    return rendered


def _parse_reviewed_timestamp(value: object) -> datetime:
    if type(value) is str:
        try:
            if len(value.encode("utf-8")) > MAX_GOAL_PROGRESS_TIMESTAMP_BYTES:
                raise ValueError
            parsed = parse_rfc3339(value)
        except TypeError, ValueError, OverflowError, UnicodeError:
            raise GoalProgressValidationError("GOAL_PROGRESS_INVALID_FIELD") from None
    elif isinstance(value, datetime):
        parsed = value
    else:
        raise GoalProgressValidationError("GOAL_PROGRESS_INVALID_FIELD")
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise GoalProgressValidationError("GOAL_PROGRESS_INVALID_FIELD")
    return parsed.astimezone(UTC)


def _parse_observation_time(
    value: object, precision: object
) -> tuple[datetime | Literal["unknown"], str]:
    if type(precision) is not str or precision not in {"exact", "unknown"}:
        raise GoalProgressValidationError("GOAL_PROGRESS_INVALID_FIELD")
    if type(value) is str and value == GOAL_PROGRESS_UNKNOWN_TIME:
        if precision != "unknown":
            raise GoalProgressValidationError("GOAL_PROGRESS_INVALID_FIELD")
        return GOAL_PROGRESS_UNKNOWN_TIME, "unknown"
    if precision != "exact":
        raise GoalProgressValidationError("GOAL_PROGRESS_INVALID_FIELD")
    return _parse_reviewed_timestamp(value), "exact"


def _format_timestamp(value: datetime) -> str:
    normalized = _parse_reviewed_timestamp(value)
    rendered = normalized.isoformat(
        timespec="microseconds" if normalized.microsecond else "seconds"
    )
    return rendered.removesuffix("+00:00") + "Z"


def _parse_enum(value: object, enum_type: type[StrEnum], field: str) -> StrEnum:
    if isinstance(value, enum_type):
        return value
    if type(value) is not str:
        raise GoalProgressValidationError("GOAL_PROGRESS_INVALID_FIELD")
    try:
        return enum_type(value)
    except ValueError:
        raise GoalProgressValidationError("GOAL_PROGRESS_INVALID_FIELD") from None


def _validated_goal_identities(values: Iterable[object]) -> tuple[object, ...]:
    result: list[object] = []
    for value in values:
        try:
            from second_brain.application.growth import validate_growth_goal_identity

            result.append(validate_growth_goal_identity(value))
        except TypeError, ValueError:
            raise GoalProgressValidationError("GOAL_PROGRESS_INVALID_FIELD") from None
    return tuple(result)


def _goal_uuid(value: object) -> UUID:
    source = getattr(value, "source_note_uuid", None)
    return _parse_uuid(source)


def _goal_hash(value: object) -> str:
    as_dict = getattr(value, "as_dict", None)
    if not callable(as_dict):
        raise GoalProgressValidationError("GOAL_PROGRESS_INVALID_FIELD")
    try:
        return goal_progress_hash_json(as_dict())
    except TypeError, ValueError, UnicodeError:
        raise GoalProgressValidationError("GOAL_PROGRESS_INVALID_FIELD") from None


def _cycle_issues(
    records: Sequence[DefinitionRecordV1 | ObservationRecordV1],
    predecessor_getter: Any,
) -> tuple[str, ...]:
    by_id = {cast(UUID, item.id): item for item in records}
    visited: set[UUID] = set()
    active: set[UUID] = set()
    issues: set[str] = set()

    def visit(record_id: UUID) -> None:
        if record_id in active:
            issues.add("GOAL_PROGRESS_CHAIN_CYCLE")
            return
        if record_id in visited:
            return
        active.add(record_id)
        predecessor_id = predecessor_getter(by_id[record_id])
        if predecessor_id is not None and predecessor_id in by_id:
            visit(predecessor_id)
        active.remove(record_id)
        visited.add(record_id)

    for record_id in sorted(by_id, key=str):
        visit(record_id)
    return tuple(sorted(issues))


def _chain_state(active_count: int) -> SupersessionChainStateV1:
    if active_count == 0:
        return SupersessionChainStateV1.ZERO_ACTIVE
    if active_count == 1:
        return SupersessionChainStateV1.ONE_ACTIVE
    return SupersessionChainStateV1.MULTIPLE_ACTIVE


def _observation_event_key(observation: ObservationRecordV1) -> tuple[object, ...]:
    subject = (
        observation.milestone_id if observation.milestone_id is not None else observation.metric_id
    )
    event_time = (
        GOAL_PROGRESS_UNKNOWN_TIME
        if observation.observed_at == GOAL_PROGRESS_UNKNOWN_TIME
        else _format_timestamp(cast(datetime, observation.observed_at))
    )
    return cast(ProgressModelV1, observation.progress_model).value, subject, event_time


__all__ = [
    "DEFAULT_GOAL_PROGRESS_POLICY",
    "GOAL_PROGRESS_DIAGNOSTIC_MESSAGES",
    "GOAL_PROGRESS_MARKER",
    "GOAL_PROGRESS_MARKER_VALUE",
    "GOAL_PROGRESS_MILESTONE_LABEL_BYTES",
    "GOAL_PROGRESS_POLICY",
    "GOAL_PROGRESS_POLICY_CANONICAL_JSON",
    "GOAL_PROGRESS_POLICY_FINGERPRINT",
    "GOAL_PROGRESS_POLICY_PAYLOAD",
    "GOAL_PROGRESS_RECORD_BYTES",
    "GOAL_PROGRESS_SLUG_BYTES",
    "GOAL_PROGRESS_UNIT_BYTES",
    "GOAL_PROGRESS_UNKNOWN_TIME",
    "MAX_GOAL_PROGRESS_DECIMAL_BYTES",
    "MAX_GOAL_PROGRESS_MILESTONES",
    "MAX_GOAL_PROGRESS_MILESTONE_LABEL_BYTES",
    "MAX_GOAL_PROGRESS_RECORD_BYTES",
    "MAX_GOAL_PROGRESS_SLUG_BYTES",
    "MAX_GOAL_PROGRESS_TIMESTAMP_BYTES",
    "MAX_GOAL_PROGRESS_UNIT_BYTES",
    "GoalBindingValidationV1",
    "GoalProgressBindingState",
    "GoalProgressBindingStateV1",
    "GoalProgressPolicyV1",
    "GoalProgressRecordError",
    "GoalProgressRecordKind",
    "GoalProgressRecordKindV1",
    "GoalProgressValidationError",
    "MilestoneObservationV1",
    "MilestoneSetDefinitionV1",
    "MilestoneState",
    "MilestoneStateV1",
    "MilestoneV1",
    "NumericDirection",
    "NumericDirectionV1",
    "NumericObservationV1",
    "NumericTargetDefinitionV1",
    "ObservationEligibilityReason",
    "ObservationEligibilityReasonV1",
    "ObservationEligibilityV1",
    "ObservationRecordV1",
    "ProgressModel",
    "ProgressModelV1",
    "SupersessionChainResultV1",
    "SupersessionChainState",
    "SupersessionChainStateV1",
    "canonical_goal_progress_json",
    "evaluate_observation_eligibility",
    "goal_progress_hash_json",
    "is_goal_progress_enrolled",
    "is_observation_eligible",
    "parse_definition_record",
    "parse_goal_progress_record",
    "parse_observation_record",
    "validate_definition_binding",
    "validate_definition_chain",
    "validate_goal_binding",
    "validate_goal_progress_policy",
    "validate_observation_against_definition",
    "validate_observation_binding",
    "validate_observation_chain",
    "validate_replacement_chain",
]

# Stage 12C is kept in a separate read-model module so the Stage 12A parser
# remains dependency-light.  Lazy aliases preserve the natural public import
# path without introducing an import cycle during module initialization.
_STAGE12C_EXPORTS = frozenset(
    {
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
    }
)
__all__.extend(sorted(_STAGE12C_EXPORTS))


def __getattr__(name: str) -> object:
    """Resolve Stage 12C aliases only after Stage 12A initialization."""

    if name not in _STAGE12C_EXPORTS:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    from second_brain.application import goal_progress_read

    return cast(object, getattr(goal_progress_read, name))
