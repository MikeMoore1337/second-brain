"""Stage 14C provider-free deterministic Personal Experiment evaluation.

The evaluator is a read-only application boundary.  It accepts one exact
experiment identity and one explicit UTC cutoff, rereads the canonical vault,
validates every Goal/Stage 12/experiment binding, and wraps the existing Stage
12 evaluator around only explicitly enrolled observations.  It has no writer,
provider, network, clock, browser storage, telemetry, or automatic-action
capability.
"""

from __future__ import annotations

import re
from collections import defaultdict
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import Final, cast
from uuid import UUID

from second_brain.application.goal_progress import (
    GOAL_PROGRESS_MARKER,
    GOAL_PROGRESS_MARKER_VALUE,
    GOAL_PROGRESS_POLICY_FINGERPRINT,
    DefinitionRecordV1,
    GoalProgressRecordKindV1,
    GoalProgressValidationError,
    ObservationRecordV1,
    ProgressModelV1,
    SupersessionChainStateV1,
    goal_progress_hash_json,
    validate_definition_chain,
    validate_goal_progress_policy,
    validate_observation_against_definition,
    validate_observation_chain,
)
from second_brain.application.goal_progress_read import (
    BuildGoalProgress,
    GoalProgressError,
    GoalProgressRequestV1,
    GoalProgressStatusV1,
    validate_goal_progress_result,
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
from second_brain.application.personal_experiments import (
    MAX_PERSONAL_EXPERIMENT_OBSERVATIONS,
    MAX_PERSONAL_EXPERIMENT_RECORDS,
    PERSONAL_EXPERIMENT_DERIVATION_ID,
    PERSONAL_EXPERIMENT_POLICY_FINGERPRINT,
    PersonalExperimentBaselineStrategyV1,
    PersonalExperimentChainStateV1,
    PersonalExperimentDefinitionRecordV1,
    PersonalExperimentLifecycleEventV1,
    PersonalExperimentLifecycleRecordV1,
    PersonalExperimentLifecycleValidationV1,
    PersonalExperimentObservationRecordV1,
    canonical_personal_experiment_json,
    personal_experiment_hash_json,
    validate_personal_experiment_definition_chain,
    validate_personal_experiment_lifecycle_chain,
    validate_personal_experiment_observation_chain,
    validate_personal_experiment_policy,
)
from second_brain.application.ports import VaultReader
from second_brain.application.reports import ScanReport, VaultSnapshot
from second_brain.application.self_model import DEFAULT_SELF_MODEL_POLICY, SelfModelPolicy
from second_brain.application.validation import build_report
from second_brain.domain.models import MarkdownDocument, parse_rfc3339, parse_uuid7

PERSONAL_EXPERIMENT_RESULT_CONTRACT: Final[str] = "personal_experiment_result_v1"
PERSONAL_EXPERIMENT_CONTRACT_ID: Final[str] = "personal-experiments-v1"
PERSONAL_EXPERIMENT_RESULT_DERIVATION_ID: Final[str] = PERSONAL_EXPERIMENT_DERIVATION_ID
MAX_PERSONAL_EXPERIMENT_RESULT_BYTES: Final[int] = 128 * 1024
MAX_PERSONAL_EXPERIMENT_RESULT_REFERENCES: Final[int] = MAX_PERSONAL_EXPERIMENT_OBSERVATIONS
MAX_PERSONAL_EXPERIMENT_RESULT_REASONS: Final[int] = 16
MAX_PERSONAL_EXPERIMENT_RESULT_TEXT_BYTES: Final[int] = 256
_HASH_PATTERN: Final[re.Pattern[str]] = re.compile(r"sha256:[0-9a-f]{64}\Z", re.ASCII)
_RESULT_LIFECYCLE_STATES: Final[frozenset[str]] = frozenset(
    {"unknown", "planned", "active", "completed", "cancelled"}
)
_RESULT_REASONS: Final[frozenset[str]] = frozenset(
    {
        "source_changed",
        "source_unavailable",
        "exact_source_missing",
        "policy_mismatch",
        "chain_conflict",
        "no_activation",
        "activation_after_as_of",
        "baseline_unavailable",
        "no_eligible_enrollment",
        "observations_excluded",
        "cancelled_by_owner",
        "stage12_not_comparable",
        "stage12_definition_missing",
    }
)


class PersonalExperimentResultStatusV1(StrEnum):
    """Closed descriptive result vocabulary from the Stage 14 contract."""

    INSUFFICIENT_EVIDENCE = "insufficient_evidence"
    OBSERVED_TOWARD_TARGET = "observed_toward_target"
    OBSERVED_AWAY_FROM_TARGET = "observed_away_from_target"
    OBSERVED_NO_CLEAR_CHANGE = "observed_no_clear_change"
    TARGET_MET = "target_met"
    NOT_COMPARABLE = "not_comparable"
    SOURCE_CHANGED = "source_changed"
    CANCELLED = "cancelled"
    NOT_EVALUATED = "not_evaluated"


PersonalExperimentResultStatus = PersonalExperimentResultStatusV1


class PersonalExperimentEvaluationErrorCodeV1(StrEnum):
    """Safe errors where a typed result cannot be produced."""

    REQUEST_INVALID = "PERSONAL_EXPERIMENT_EVALUATION_REQUEST_INVALID"
    SOURCE_UNAVAILABLE = "PERSONAL_EXPERIMENT_EVALUATION_SOURCE_UNAVAILABLE"
    POLICY_MISMATCH = "PERSONAL_EXPERIMENT_EVALUATION_POLICY_MISMATCH"
    RESULT_TOO_LARGE = "PERSONAL_EXPERIMENT_EVALUATION_RESULT_TOO_LARGE"
    INTERNAL = "PERSONAL_EXPERIMENT_EVALUATION_INTERNAL"


PersonalExperimentEvaluationErrorCode = PersonalExperimentEvaluationErrorCodeV1


_EVALUATION_ERROR_MESSAGES: Final[dict[PersonalExperimentEvaluationErrorCodeV1, str]] = {
    PersonalExperimentEvaluationErrorCodeV1.REQUEST_INVALID: (
        "Запрос оценки личного эксперимента недействителен"
    ),
    PersonalExperimentEvaluationErrorCodeV1.SOURCE_UNAVAILABLE: (
        "Канонический источник оценки личного эксперимента недоступен"
    ),
    PersonalExperimentEvaluationErrorCodeV1.POLICY_MISMATCH: (
        "Политика оценки личного эксперимента недействительна"
    ),
    PersonalExperimentEvaluationErrorCodeV1.RESULT_TOO_LARGE: (
        "Результат оценки личного эксперимента превышает допустимый размер"
    ),
    PersonalExperimentEvaluationErrorCodeV1.INTERNAL: ("Оценка личного эксперимента не выполнена"),
}


class PersonalExperimentEvaluationError(RuntimeError):
    """Bounded public evaluation error without raw source details."""

    def __init__(self, code: PersonalExperimentEvaluationErrorCodeV1 | str) -> None:
        normalized = _parse_error_code(code)
        self.code = normalized.value
        self.message = _EVALUATION_ERROR_MESSAGES[normalized]
        super().__init__(self.message)

    def as_dict(self) -> dict[str, str]:
        return {"code": self.code, "message": self.message}


class PersonalExperimentEvaluationRequestInvalidError(PersonalExperimentEvaluationError):
    """The exact identity or UTC cutoff is malformed."""

    def __init__(self) -> None:
        super().__init__(PersonalExperimentEvaluationErrorCodeV1.REQUEST_INVALID)


class PersonalExperimentEvaluationSourceUnavailableError(PersonalExperimentEvaluationError):
    """The vault cannot be reread safely."""

    def __init__(self) -> None:
        super().__init__(PersonalExperimentEvaluationErrorCodeV1.SOURCE_UNAVAILABLE)


class PersonalExperimentEvaluationPolicyMismatchError(PersonalExperimentEvaluationError):
    """A compiled Stage 12 or Stage 14 policy cannot be proven."""

    def __init__(self) -> None:
        super().__init__(PersonalExperimentEvaluationErrorCodeV1.POLICY_MISMATCH)


class PersonalExperimentEvaluationResultTooLargeError(PersonalExperimentEvaluationError):
    """The complete result cannot be emitted within its bound."""

    def __init__(self) -> None:
        super().__init__(PersonalExperimentEvaluationErrorCodeV1.RESULT_TOO_LARGE)


@dataclass(frozen=True, slots=True)
class PersonalExperimentEvaluationRequestV1:
    """Exact immutable evaluation identity and explicit UTC cutoff."""

    experiment_definition_id: UUID | str
    experiment_definition_fingerprint: str
    as_of: datetime | str

    def __post_init__(self) -> None:
        try:
            object.__setattr__(
                self, "experiment_definition_id", parse_uuid7(self.experiment_definition_id)
            )
            object.__setattr__(
                self,
                "experiment_definition_fingerprint",
                _parse_hash(self.experiment_definition_fingerprint),
            )
            object.__setattr__(self, "as_of", _parse_exact_utc(self.as_of))
        except TypeError, ValueError, OverflowError, UnicodeError:
            raise PersonalExperimentEvaluationRequestInvalidError() from None

    @classmethod
    def from_dict(cls, value: object) -> PersonalExperimentEvaluationRequestV1:
        """Parse exactly the three request fields; no implicit current time."""

        if not isinstance(value, Mapping) or set(value) != {
            "experiment_definition_id",
            "experiment_definition_fingerprint",
            "as_of",
        }:
            raise PersonalExperimentEvaluationRequestInvalidError()
        try:
            return cls(
                value["experiment_definition_id"],
                cast(str, value["experiment_definition_fingerprint"]),
                value["as_of"],
            )
        except PersonalExperimentEvaluationError:
            raise
        except TypeError, ValueError, OverflowError, UnicodeError:
            raise PersonalExperimentEvaluationRequestInvalidError() from None

    def as_dict(self) -> dict[str, object]:
        return {
            "experiment_definition_id": str(self.experiment_definition_id),
            "experiment_definition_fingerprint": self.experiment_definition_fingerprint,
            "as_of": _format_timestamp(cast(datetime, self.as_of)),
        }


PersonalExperimentEvaluationRequest = PersonalExperimentEvaluationRequestV1


class PersonalExperimentBaselineSourceKindV1(StrEnum):
    """The two exact baseline sources permitted by Stage 14."""

    STAGE12_DEFINITION = "stage12_definition"
    STAGE12_OBSERVATION = "stage12_observation"


@dataclass(frozen=True, slots=True)
class PersonalExperimentBaselineProvenanceV1:
    """Typed baseline provenance without copying Stage 12 values or body."""

    strategy: PersonalExperimentBaselineStrategyV1 | str
    source_kind: PersonalExperimentBaselineSourceKindV1 | str
    stage12_definition_id: UUID | str
    stage12_definition_fingerprint: str
    stage12_observation_id: UUID | str | None = None
    stage12_observation_fingerprint: str | None = None
    observed_at: datetime | str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "strategy",
            _parse_enum(self.strategy, PersonalExperimentBaselineStrategyV1),
        )
        object.__setattr__(
            self,
            "source_kind",
            _parse_enum(self.source_kind, PersonalExperimentBaselineSourceKindV1),
        )
        object.__setattr__(self, "stage12_definition_id", parse_uuid7(self.stage12_definition_id))
        object.__setattr__(
            self,
            "stage12_definition_fingerprint",
            _parse_hash(self.stage12_definition_fingerprint),
        )
        if self.stage12_observation_id is None:
            if self.stage12_observation_fingerprint is not None or self.observed_at is not None:
                raise ValueError("baseline observation provenance is incomplete")
        else:
            if self.stage12_observation_fingerprint is None or self.observed_at is None:
                raise ValueError("baseline observation provenance is incomplete")
            object.__setattr__(
                self, "stage12_observation_id", parse_uuid7(self.stage12_observation_id)
            )
            object.__setattr__(
                self,
                "stage12_observation_fingerprint",
                _parse_hash(self.stage12_observation_fingerprint),
            )
            object.__setattr__(self, "observed_at", _parse_observation_time(self.observed_at))
        source_kind = cast(PersonalExperimentBaselineSourceKindV1, self.source_kind)
        if source_kind is PersonalExperimentBaselineSourceKindV1.STAGE12_DEFINITION:
            if self.stage12_observation_id is not None:
                raise ValueError("definition baseline cannot contain an observation")
        elif self.stage12_observation_id is None:
            raise ValueError("observation baseline requires an observation")

    def as_dict(self) -> dict[str, object]:
        return {
            "strategy": cast(PersonalExperimentBaselineStrategyV1, self.strategy).value,
            "source_kind": cast(PersonalExperimentBaselineSourceKindV1, self.source_kind).value,
            "stage12_definition_id": str(self.stage12_definition_id),
            "stage12_definition_fingerprint": self.stage12_definition_fingerprint,
            "stage12_observation_id": (
                str(self.stage12_observation_id)
                if self.stage12_observation_id is not None
                else None
            ),
            "stage12_observation_fingerprint": self.stage12_observation_fingerprint,
            "observed_at": (
                _format_timestamp(self.observed_at)
                if isinstance(self.observed_at, datetime)
                else self.observed_at
            ),
        }


PersonalExperimentBaselineProvenance = PersonalExperimentBaselineProvenanceV1


@dataclass(frozen=True, slots=True)
class PersonalExperimentObservationReferenceV1:
    """Exact Stage 14 enrollment and Stage 12 source reference."""

    enrollment_id: UUID | str
    stage12_observation_id: UUID | str
    stage12_observation_fingerprint: str
    observed_at: datetime | str
    observation_reviewed_at: datetime | str

    def __post_init__(self) -> None:
        object.__setattr__(self, "enrollment_id", parse_uuid7(self.enrollment_id))
        object.__setattr__(self, "stage12_observation_id", parse_uuid7(self.stage12_observation_id))
        object.__setattr__(
            self,
            "stage12_observation_fingerprint",
            _parse_hash(self.stage12_observation_fingerprint),
        )
        object.__setattr__(self, "observed_at", _parse_observation_time(self.observed_at))
        object.__setattr__(
            self,
            "observation_reviewed_at",
            _parse_exact_utc(self.observation_reviewed_at),
        )

    def as_dict(self) -> dict[str, object]:
        return {
            "enrollment_id": str(self.enrollment_id),
            "stage12_observation_id": str(self.stage12_observation_id),
            "stage12_observation_fingerprint": self.stage12_observation_fingerprint,
            "observed_at": (
                _format_timestamp(self.observed_at)
                if isinstance(self.observed_at, datetime)
                else self.observed_at
            ),
            "observation_reviewed_at": _format_timestamp(
                cast(datetime, self.observation_reviewed_at)
            ),
        }


PersonalExperimentObservationReference = PersonalExperimentObservationReferenceV1


class PersonalExperimentObservationExclusionReasonV1(StrEnum):
    """Bounded reasons for an explicit enrollment outside the result."""

    SUPERSEDED = "superseded"
    UNKNOWN_TIME = "unknown_time"
    REVIEWED_AFTER_AS_OF = "reviewed_after_as_of"
    FUTURE_AS_OF = "future_as_of"
    OUTSIDE_EXPERIMENT_WINDOW = "outside_experiment_window"


@dataclass(frozen=True, slots=True)
class PersonalExperimentExcludedObservationV1:
    """One excluded explicit enrollment with no raw source data."""

    reference: PersonalExperimentObservationReferenceV1
    reason: PersonalExperimentObservationExclusionReasonV1 | str

    def __post_init__(self) -> None:
        if type(self.reference) is not PersonalExperimentObservationReferenceV1:
            raise ValueError("excluded observation reference is invalid")
        object.__setattr__(
            self,
            "reason",
            _parse_enum(self.reason, PersonalExperimentObservationExclusionReasonV1),
        )

    @property
    def enrollment_id(self) -> UUID:
        return cast(UUID, self.reference.enrollment_id)

    @property
    def stage12_observation_id(self) -> UUID:
        return cast(UUID, self.reference.stage12_observation_id)

    def as_dict(self) -> dict[str, object]:
        return {
            **self.reference.as_dict(),
            "reason": cast(PersonalExperimentObservationExclusionReasonV1, self.reason).value,
        }


PersonalExperimentExcludedObservation = PersonalExperimentExcludedObservationV1


class PersonalExperimentCaveatV1(StrEnum):
    """Mandatory language boundary carried by every typed result."""

    OBSERVED_CHANGE_IS_NOT_PROOF_OF_CAUSATION = "observed_change_is_not_proof_of_causation"
    NO_AUTOMATIC_ADAPTATION = "no_automatic_adaptation"


@dataclass(frozen=True, slots=True)
class PersonalExperimentProvenanceV1:
    """Typed proof of the exact read-only source boundary."""

    source: str
    provider: str
    network: str
    write: str
    as_of: datetime | str
    experiment_definition_id: UUID | str
    experiment_definition_fingerprint: str
    goal_source_uuid: UUID | str | None = None
    goal_identity_fingerprint: str | None = None
    stage12_definition_id: UUID | str | None = None
    stage12_definition_fingerprint: str | None = None
    activation_record_id: UUID | str | None = None
    terminal_record_id: UUID | str | None = None
    included_enrollment_ids: tuple[UUID | str, ...] = ()
    excluded_enrollment_ids: tuple[UUID | str, ...] = ()

    def __post_init__(self) -> None:
        if (self.source, self.provider, self.network, self.write) != (
            "current_vault",
            "none",
            "none",
            "none",
        ):
            raise ValueError("Personal Experiment provenance side effects are invalid")
        object.__setattr__(self, "as_of", _parse_exact_utc(self.as_of))
        object.__setattr__(
            self, "experiment_definition_id", parse_uuid7(self.experiment_definition_id)
        )
        object.__setattr__(
            self,
            "experiment_definition_fingerprint",
            _parse_hash(self.experiment_definition_fingerprint),
        )
        for field in (
            "goal_source_uuid",
            "stage12_definition_id",
            "activation_record_id",
            "terminal_record_id",
        ):
            value = getattr(self, field)
            if value is not None:
                object.__setattr__(self, field, parse_uuid7(value))
        if self.goal_identity_fingerprint is not None:
            _parse_hash(self.goal_identity_fingerprint)
        if self.stage12_definition_fingerprint is not None:
            _parse_hash(self.stage12_definition_fingerprint)
        for name in ("included_enrollment_ids", "excluded_enrollment_ids"):
            values = getattr(self, name)
            if type(values) is not tuple or len(values) > MAX_PERSONAL_EXPERIMENT_RESULT_REFERENCES:
                raise ValueError("Personal Experiment provenance references are invalid")
            object.__setattr__(self, name, tuple(parse_uuid7(value) for value in values))

    def as_dict(self) -> dict[str, object]:
        return {
            "source": self.source,
            "provider": self.provider,
            "network": self.network,
            "write": self.write,
            "as_of": _format_timestamp(cast(datetime, self.as_of)),
            "experiment_definition_id": str(self.experiment_definition_id),
            "experiment_definition_fingerprint": self.experiment_definition_fingerprint,
            "goal_source_uuid": str(self.goal_source_uuid) if self.goal_source_uuid else None,
            "goal_identity_fingerprint": self.goal_identity_fingerprint,
            "stage12_definition_id": (
                str(self.stage12_definition_id) if self.stage12_definition_id else None
            ),
            "stage12_definition_fingerprint": self.stage12_definition_fingerprint,
            "activation_record_id": (
                str(self.activation_record_id) if self.activation_record_id else None
            ),
            "terminal_record_id": (
                str(self.terminal_record_id) if self.terminal_record_id else None
            ),
            "included_enrollment_ids": [str(value) for value in self.included_enrollment_ids],
            "excluded_enrollment_ids": [str(value) for value in self.excluded_enrollment_ids],
        }


PersonalExperimentProvenance = PersonalExperimentProvenanceV1


@dataclass(frozen=True, slots=True)
class PersonalExperimentEvaluationResultV1:
    """Immutable descriptive result; never a causal or adaptive claim."""

    experiment_definition_id: UUID | str
    experiment_definition_fingerprint: str
    status: PersonalExperimentResultStatusV1 | str
    as_of: datetime | str
    goal_source_uuid: UUID | str | None = None
    goal_identity_fingerprint: str | None = None
    stage12_definition_id: UUID | str | None = None
    stage12_definition_fingerprint: str | None = None
    stage12_policy_fingerprint: str = GOAL_PROGRESS_POLICY_FINGERPRINT
    experiment_policy_fingerprint: str = PERSONAL_EXPERIMENT_POLICY_FINGERPRINT
    lifecycle_state: str = "unknown"
    activation_record_id: UUID | str | None = None
    activation_at: datetime | str | None = None
    terminal_record_id: UUID | str | None = None
    terminal_at: datetime | str | None = None
    baseline: PersonalExperimentBaselineProvenanceV1 | None = None
    stage12_status: GoalProgressStatusV1 | str | None = None
    stage12_current_observation_ids: tuple[UUID | str, ...] = ()
    included_observations: tuple[PersonalExperimentObservationReferenceV1, ...] = ()
    excluded_observations: tuple[PersonalExperimentExcludedObservationV1, ...] = ()
    reasons: tuple[str, ...] = ()
    caveats: tuple[PersonalExperimentCaveatV1 | str, ...] = (
        PersonalExperimentCaveatV1.OBSERVED_CHANGE_IS_NOT_PROOF_OF_CAUSATION,
        PersonalExperimentCaveatV1.NO_AUTOMATIC_ADAPTATION,
    )
    provenance: PersonalExperimentProvenanceV1 | None = None
    result_fingerprint: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "experiment_definition_id", parse_uuid7(self.experiment_definition_id)
        )
        object.__setattr__(
            self,
            "experiment_definition_fingerprint",
            _parse_hash(self.experiment_definition_fingerprint),
        )
        object.__setattr__(
            self, "status", _parse_enum(self.status, PersonalExperimentResultStatusV1)
        )
        object.__setattr__(self, "as_of", _parse_exact_utc(self.as_of))
        if self.goal_source_uuid is not None:
            object.__setattr__(self, "goal_source_uuid", parse_uuid7(self.goal_source_uuid))
        if self.goal_identity_fingerprint is not None:
            _parse_hash(self.goal_identity_fingerprint)
        if self.stage12_definition_id is not None:
            object.__setattr__(
                self, "stage12_definition_id", parse_uuid7(self.stage12_definition_id)
            )
        if self.stage12_definition_fingerprint is not None:
            _parse_hash(self.stage12_definition_fingerprint)
        if self.stage12_policy_fingerprint != GOAL_PROGRESS_POLICY_FINGERPRINT:
            raise ValueError("Stage 12 result policy is invalid")
        if self.experiment_policy_fingerprint != PERSONAL_EXPERIMENT_POLICY_FINGERPRINT:
            raise ValueError("Personal Experiment result policy is invalid")
        if self.lifecycle_state not in _RESULT_LIFECYCLE_STATES:
            raise ValueError("Personal Experiment lifecycle result state is invalid")
        for field in ("activation_record_id", "terminal_record_id"):
            value = getattr(self, field)
            if value is not None:
                object.__setattr__(self, field, parse_uuid7(value))
        for field in ("activation_at", "terminal_at"):
            value = getattr(self, field)
            if value is not None:
                object.__setattr__(self, field, _parse_exact_utc(value))
        if (
            self.baseline is not None
            and type(self.baseline) is not PersonalExperimentBaselineProvenanceV1
        ):
            raise ValueError("Personal Experiment baseline provenance is invalid")
        if self.stage12_status is not None:
            object.__setattr__(
                self, "stage12_status", _parse_enum(self.stage12_status, GoalProgressStatusV1)
            )
        if type(self.stage12_current_observation_ids) is not tuple:
            raise ValueError("Stage 12 result references are invalid")
        if len(self.stage12_current_observation_ids) > MAX_PERSONAL_EXPERIMENT_RESULT_REFERENCES:
            raise ValueError("Stage 12 result references are too large")
        object.__setattr__(
            self,
            "stage12_current_observation_ids",
            tuple(parse_uuid7(value) for value in self.stage12_current_observation_ids),
        )
        if (
            type(self.included_observations) is not tuple
            or type(self.excluded_observations) is not tuple
        ):
            raise ValueError("Personal Experiment result references are invalid")
        if len(self.included_observations) + len(self.excluded_observations) > (
            MAX_PERSONAL_EXPERIMENT_RESULT_REFERENCES
        ):
            raise ValueError("Personal Experiment result references are too large")
        if any(
            type(item) is not PersonalExperimentObservationReferenceV1
            for item in self.included_observations
        ):
            raise ValueError("included Personal Experiment references are invalid")
        if any(
            type(item) is not PersonalExperimentExcludedObservationV1
            for item in self.excluded_observations
        ):
            raise ValueError("excluded Personal Experiment references are invalid")
        object.__setattr__(self, "included_observations", tuple(self.included_observations))
        object.__setattr__(self, "excluded_observations", tuple(self.excluded_observations))
        if (
            type(self.reasons) is not tuple
            or len(self.reasons) > MAX_PERSONAL_EXPERIMENT_RESULT_REASONS
        ):
            raise ValueError("Personal Experiment result reasons are invalid")
        for reason in self.reasons:
            if type(reason) is not str or reason not in _RESULT_REASONS:
                raise ValueError("Personal Experiment result reason is invalid")
        if type(self.caveats) is not tuple:
            raise ValueError("Personal Experiment result caveats are invalid")
        caveats = tuple(_parse_enum(item, PersonalExperimentCaveatV1) for item in self.caveats)
        if set(caveats) != set(PersonalExperimentCaveatV1):
            raise ValueError("Personal Experiment result caveats are incomplete")
        object.__setattr__(self, "caveats", caveats)
        if self.provenance is None:
            object.__setattr__(
                self,
                "provenance",
                PersonalExperimentProvenanceV1(
                    source="current_vault",
                    provider="none",
                    network="none",
                    write="none",
                    as_of=cast(datetime, self.as_of),
                    experiment_definition_id=cast(UUID, self.experiment_definition_id),
                    experiment_definition_fingerprint=self.experiment_definition_fingerprint,
                    goal_source_uuid=cast(UUID | None, self.goal_source_uuid),
                    goal_identity_fingerprint=self.goal_identity_fingerprint,
                    stage12_definition_id=cast(UUID | None, self.stage12_definition_id),
                    stage12_definition_fingerprint=self.stage12_definition_fingerprint,
                    activation_record_id=cast(UUID | None, self.activation_record_id),
                    terminal_record_id=cast(UUID | None, self.terminal_record_id),
                    included_enrollment_ids=tuple(
                        item.enrollment_id for item in self.included_observations
                    ),
                    excluded_enrollment_ids=tuple(
                        item.enrollment_id for item in self.excluded_observations
                    ),
                ),
            )
        elif type(self.provenance) is not PersonalExperimentProvenanceV1:
            raise ValueError("Personal Experiment provenance is invalid")
        expected_fingerprint = personal_experiment_hash_json(self._fingerprint_payload())
        if self.result_fingerprint:
            if _parse_hash(self.result_fingerprint) != expected_fingerprint:
                raise ValueError("Personal Experiment result fingerprint is invalid")
        else:
            object.__setattr__(self, "result_fingerprint", expected_fingerprint)
        if len(self.to_json().encode("utf-8")) > MAX_PERSONAL_EXPERIMENT_RESULT_BYTES:
            raise PersonalExperimentEvaluationResultTooLargeError()

    def _fingerprint_payload(self) -> dict[str, object]:
        payload = self.as_dict(include_fingerprint=False)
        return {"contract": PERSONAL_EXPERIMENT_RESULT_CONTRACT, **payload}

    def as_dict(self, *, include_fingerprint: bool = True) -> dict[str, object]:
        result: dict[str, object] = {
            "contract": PERSONAL_EXPERIMENT_RESULT_CONTRACT,
            "contract_id": PERSONAL_EXPERIMENT_CONTRACT_ID,
            "derivation_id": PERSONAL_EXPERIMENT_RESULT_DERIVATION_ID,
            "experiment_definition_id": str(self.experiment_definition_id),
            "experiment_definition_fingerprint": self.experiment_definition_fingerprint,
            "status": cast(PersonalExperimentResultStatusV1, self.status).value,
            "as_of": _format_timestamp(cast(datetime, self.as_of)),
            "goal_source_uuid": str(self.goal_source_uuid) if self.goal_source_uuid else None,
            "goal_identity_fingerprint": self.goal_identity_fingerprint,
            "stage12_definition_id": (
                str(self.stage12_definition_id) if self.stage12_definition_id else None
            ),
            "stage12_definition_fingerprint": self.stage12_definition_fingerprint,
            "stage12_policy_fingerprint": self.stage12_policy_fingerprint,
            "experiment_policy_fingerprint": self.experiment_policy_fingerprint,
            "lifecycle_state": self.lifecycle_state,
            "activation_record_id": (
                str(self.activation_record_id) if self.activation_record_id else None
            ),
            "activation_at": (
                _format_timestamp(cast(datetime, self.activation_at))
                if self.activation_at is not None
                else None
            ),
            "terminal_record_id": str(self.terminal_record_id) if self.terminal_record_id else None,
            "terminal_at": (
                _format_timestamp(cast(datetime, self.terminal_at))
                if self.terminal_at is not None
                else None
            ),
            "baseline": self.baseline.as_dict() if self.baseline is not None else None,
            "stage12_status": (
                cast(GoalProgressStatusV1, self.stage12_status).value
                if self.stage12_status is not None
                else None
            ),
            "stage12_current_observation_ids": [
                str(value) for value in self.stage12_current_observation_ids
            ],
            "included_observations": [item.as_dict() for item in self.included_observations],
            "excluded_observations": [item.as_dict() for item in self.excluded_observations],
            "reasons": list(self.reasons),
            "caveats": [cast(PersonalExperimentCaveatV1, item).value for item in self.caveats],
            "provenance": cast(PersonalExperimentProvenanceV1, self.provenance).as_dict(),
        }
        if include_fingerprint:
            result["result_fingerprint"] = self.result_fingerprint
        return result

    def to_json(self) -> str:
        return canonical_personal_experiment_json(self.as_dict())

    @property
    def contract_id(self) -> str:
        """Return the normative Stage 14 contract identifier."""

        return PERSONAL_EXPERIMENT_CONTRACT_ID

    @property
    def derivation_id(self) -> str:
        """Return the fixed result derivation identifier."""

        return PERSONAL_EXPERIMENT_RESULT_DERIVATION_ID


PersonalExperimentEvaluationResult = PersonalExperimentEvaluationResultV1
PersonalExperimentResultV1 = PersonalExperimentEvaluationResultV1
PersonalExperimentResult = PersonalExperimentEvaluationResultV1


@dataclass(frozen=True, slots=True)
class _SnapshotReader:
    snapshot: VaultSnapshot

    def scan(self) -> VaultSnapshot:
        return self.snapshot


@dataclass(frozen=True, slots=True)
class _ObservationCollection:
    included: tuple[PersonalExperimentObservationReferenceV1, ...]
    excluded: tuple[PersonalExperimentExcludedObservationV1, ...]
    stage12_ids: tuple[UUID, ...]


@dataclass(frozen=True, slots=True)
class _EffectiveLifecycle:
    state: str
    activation: PersonalExperimentLifecycleRecordV1 | None
    terminal: PersonalExperimentLifecycleRecordV1 | None


@dataclass(frozen=True, slots=True)
class BuildPersonalExperimentEvaluation:
    """Build one deterministic current-vault Personal Experiment result."""

    reader: VaultReader
    policy: SelfModelPolicy = DEFAULT_SELF_MODEL_POLICY

    def execute(
        self,
        request: PersonalExperimentEvaluationRequestV1,
    ) -> PersonalExperimentEvaluationResultV1:
        validated_request = validate_personal_experiment_evaluation_request(request)
        try:
            validate_personal_experiment_policy()
            validate_goal_progress_policy()
        except GoalProgressValidationError, ValueError, TypeError:
            raise PersonalExperimentEvaluationPolicyMismatchError() from None

        snapshot, report = _read_current_report(self.reader)
        all_records = (
            *report.personal_experiment_definitions,
            *report.personal_experiment_lifecycles,
            *report.personal_experiment_observations,
            *report.personal_experiment_reassessments,
        )
        if len(all_records) > MAX_PERSONAL_EXPERIMENT_RECORDS:
            raise PersonalExperimentEvaluationResultTooLargeError()

        definition_matches = tuple(
            item
            for item in report.personal_experiment_definitions
            if item.id == validated_request.experiment_definition_id
        )
        if not definition_matches:
            return _result(
                validated_request,
                status=PersonalExperimentResultStatusV1.NOT_COMPARABLE,
                reasons=("exact_source_missing",),
            )
        if len(definition_matches) != 1:
            return _result(
                validated_request,
                status=PersonalExperimentResultStatusV1.NOT_COMPARABLE,
                reasons=("chain_conflict",),
            )
        definition = definition_matches[0]
        if definition.experiment_definition_fingerprint != (
            validated_request.experiment_definition_fingerprint
        ):
            return _result(
                validated_request,
                status=PersonalExperimentResultStatusV1.SOURCE_CHANGED,
                definition=definition,
                reasons=("source_changed",),
            )

        current_goal = _resolve_current_goal(
            snapshot,
            cast(UUID, definition.goal_source_uuid),
            cast(datetime, validated_request.as_of),
            self.policy,
        )
        current_goal_hash = (
            goal_progress_hash_json(current_goal.as_dict()) if current_goal is not None else None
        )
        if current_goal is None:
            return _result(
                validated_request,
                status=PersonalExperimentResultStatusV1.SOURCE_CHANGED,
                definition=definition,
                reasons=("source_changed",),
            )
        if definition.goal_identity_fingerprint != current_goal_hash or _has_goal_identity_drift(
            report,
            cast(UUID, definition.goal_source_uuid),
            current_goal_hash,
        ):
            return _result(
                validated_request,
                status=PersonalExperimentResultStatusV1.SOURCE_CHANGED,
                definition=definition,
                current_goal=current_goal,
                reasons=("source_changed",),
            )

        stage12_definition, stage12_problem = _resolve_stage12_definition(
            report,
            definition,
            current_goal_hash,
        )
        if stage12_problem is not None:
            return _result(
                validated_request,
                status=stage12_problem,
                definition=definition,
                current_goal=current_goal,
                stage12_definition=stage12_definition,
                reasons=(
                    "source_changed"
                    if stage12_problem is PersonalExperimentResultStatusV1.SOURCE_CHANGED
                    else "stage12_not_comparable",
                ),
            )
        assert stage12_definition is not None
        if definition.experiment_policy_fingerprint != PERSONAL_EXPERIMENT_POLICY_FINGERPRINT:
            return _result(
                validated_request,
                status=PersonalExperimentResultStatusV1.NOT_COMPARABLE,
                definition=definition,
                current_goal=current_goal,
                stage12_definition=stage12_definition,
                reasons=("policy_mismatch",),
            )
        if definition.goal_progress_policy_fingerprint != GOAL_PROGRESS_POLICY_FINGERPRINT:
            return _result(
                validated_request,
                status=PersonalExperimentResultStatusV1.NOT_COMPARABLE,
                definition=definition,
                current_goal=current_goal,
                stage12_definition=stage12_definition,
                reasons=("policy_mismatch",),
            )

        definition_group = tuple(
            item
            for item in report.personal_experiment_definitions
            if item.goal_source_uuid == definition.goal_source_uuid
            and item.goal_identity_fingerprint == definition.goal_identity_fingerprint
            and item.goal_progress_definition_id == definition.goal_progress_definition_id
            and item.goal_progress_definition_fingerprint
            == definition.goal_progress_definition_fingerprint
        )
        definition_chain = validate_personal_experiment_definition_chain(definition_group)
        if (
            definition_chain.issues
            or definition_chain.state is not PersonalExperimentChainStateV1.ONE_ACTIVE
        ):
            return _result(
                validated_request,
                status=PersonalExperimentResultStatusV1.NOT_COMPARABLE,
                definition=definition,
                current_goal=current_goal,
                stage12_definition=stage12_definition,
                reasons=("chain_conflict",),
            )
        active_definition = definition_chain.active_records[0]
        if (
            type(active_definition) is not PersonalExperimentDefinitionRecordV1
            or active_definition.id != definition.id
            or active_definition.experiment_definition_fingerprint
            != definition.experiment_definition_fingerprint
        ):
            return _result(
                validated_request,
                status=PersonalExperimentResultStatusV1.SOURCE_CHANGED,
                definition=definition,
                current_goal=current_goal,
                stage12_definition=stage12_definition,
                reasons=("source_changed",),
            )

        if _has_duplicate_active_experiment(
            report,
            goal_source_uuid=cast(UUID, definition.goal_source_uuid),
            goal_identity_fingerprint=definition.goal_identity_fingerprint,
        ):
            return _result(
                validated_request,
                status=PersonalExperimentResultStatusV1.NOT_COMPARABLE,
                definition=definition,
                current_goal=current_goal,
                stage12_definition=stage12_definition,
                reasons=("chain_conflict",),
            )

        lifecycle_records = tuple(
            item
            for item in report.personal_experiment_lifecycles
            if item.experiment_definition_id == definition.id
            and item.experiment_definition_fingerprint
            == definition.experiment_definition_fingerprint
        )
        lifecycle = validate_personal_experiment_lifecycle_chain(lifecycle_records)
        if lifecycle.issues or lifecycle.state == "invalid":
            return _result(
                validated_request,
                status=PersonalExperimentResultStatusV1.NOT_COMPARABLE,
                definition=definition,
                current_goal=current_goal,
                stage12_definition=stage12_definition,
                reasons=("chain_conflict",),
            )

        try:
            observations = _collect_observations(
                report,
                definition=definition,
                stage12_definition=stage12_definition,
                lifecycle=lifecycle,
                as_of=cast(datetime, validated_request.as_of),
            )
        except _EvaluationSourceProblem as problem:
            return _result(
                validated_request,
                status=problem.status,
                definition=definition,
                current_goal=current_goal,
                stage12_definition=stage12_definition,
                lifecycle=lifecycle,
                included_observations=problem.included,
                excluded_observations=problem.excluded,
                reasons=(problem.reason,),
            )

        effective = _effective_lifecycle(lifecycle, cast(datetime, validated_request.as_of))
        if effective.activation is None:
            return _result(
                validated_request,
                status=PersonalExperimentResultStatusV1.NOT_EVALUATED,
                definition=definition,
                current_goal=current_goal,
                stage12_definition=stage12_definition,
                lifecycle=lifecycle,
                effective=effective,
                included_observations=observations.included,
                excluded_observations=observations.excluded,
                reasons=("no_activation",),
            )
        if cast(datetime, effective.activation.event_at) > cast(
            datetime,
            validated_request.as_of,
        ):
            return _result(
                validated_request,
                status=PersonalExperimentResultStatusV1.NOT_EVALUATED,
                definition=definition,
                current_goal=current_goal,
                stage12_definition=stage12_definition,
                lifecycle=lifecycle,
                effective=effective,
                included_observations=observations.included,
                excluded_observations=observations.excluded,
                reasons=("activation_after_as_of",),
            )

        if cast(datetime, definition.definition_reviewed_at) > cast(
            datetime,
            validated_request.as_of,
        ):
            return _result(
                validated_request,
                status=PersonalExperimentResultStatusV1.NOT_EVALUATED,
                definition=definition,
                current_goal=current_goal,
                stage12_definition=stage12_definition,
                lifecycle=lifecycle,
                effective=effective,
                included_observations=observations.included,
                excluded_observations=observations.excluded,
                reasons=("no_activation",),
            )

        if effective.state == "cancelled":
            return _result(
                validated_request,
                status=PersonalExperimentResultStatusV1.CANCELLED,
                definition=definition,
                current_goal=current_goal,
                stage12_definition=stage12_definition,
                lifecycle=lifecycle,
                effective=effective,
                included_observations=observations.included,
                excluded_observations=observations.excluded,
                reasons=("cancelled_by_owner",),
            )

        baseline, baseline_problem = _resolve_baseline(
            report,
            definition=definition,
            stage12_definition=stage12_definition,
            activation_at=cast(datetime, effective.activation.event_at),
        )
        if baseline_problem is not None:
            return _result(
                validated_request,
                status=baseline_problem,
                definition=definition,
                current_goal=current_goal,
                stage12_definition=stage12_definition,
                lifecycle=lifecycle,
                effective=effective,
                included_observations=observations.included,
                excluded_observations=observations.excluded,
                reasons=(
                    "source_changed"
                    if baseline_problem is PersonalExperimentResultStatusV1.SOURCE_CHANGED
                    else "baseline_unavailable",
                ),
            )

        if not observations.included:
            return _result(
                validated_request,
                status=PersonalExperimentResultStatusV1.INSUFFICIENT_EVIDENCE,
                definition=definition,
                current_goal=current_goal,
                stage12_definition=stage12_definition,
                lifecycle=lifecycle,
                effective=effective,
                baseline=baseline,
                included_observations=observations.included,
                excluded_observations=observations.excluded,
                reasons=("no_eligible_enrollment",),
            )

        try:
            stage12_result = BuildGoalProgress(
                _SnapshotReader(
                    _filtered_snapshot(
                        snapshot,
                        report,
                        included_stage12_ids=set(observations.stage12_ids),
                    )
                ),
                policy=self.policy,
            ).execute(
                GoalProgressRequestV1(
                    goal_source_uuid=cast(UUID, definition.goal_source_uuid),
                    as_of=cast(datetime, validated_request.as_of),
                )
            )
            validate_goal_progress_result(stage12_result)
        except GoalProgressError as exc:
            status, reason = _map_stage12_error(exc)
            return _result(
                validated_request,
                status=status,
                definition=definition,
                current_goal=current_goal,
                stage12_definition=stage12_definition,
                lifecycle=lifecycle,
                effective=effective,
                baseline=baseline,
                included_observations=observations.included,
                excluded_observations=observations.excluded,
                reasons=(reason,),
            )
        except TypeError, ValueError, UnicodeError, OverflowError:
            raise PersonalExperimentEvaluationError(
                PersonalExperimentEvaluationErrorCodeV1.INTERNAL
            ) from None
        return _result(
            validated_request,
            status=_map_stage12_status(stage12_result.status),
            definition=definition,
            current_goal=current_goal,
            stage12_definition=stage12_definition,
            lifecycle=lifecycle,
            effective=effective,
            baseline=baseline,
            stage12_status=stage12_result.status,
            stage12_current_observation_ids=stage12_result.current_observation_uuids,
            included_observations=observations.included,
            excluded_observations=observations.excluded,
            reasons=(
                ()
                if stage12_result.status
                not in {
                    GoalProgressStatusV1.INSUFFICIENT_OBSERVATIONS,
                    GoalProgressStatusV1.MILESTONE_OBSERVATIONS_AVAILABLE,
                }
                else ("no_eligible_enrollment",)
            ),
        )


BuildPersonalExperimentEvaluationV1 = BuildPersonalExperimentEvaluation
PersonalExperimentEvaluator = BuildPersonalExperimentEvaluation
PersonalExperimentEvaluationBuilder = BuildPersonalExperimentEvaluation


def evaluate_personal_experiment(
    reader: VaultReader,
    request: PersonalExperimentEvaluationRequestV1,
    *,
    policy: SelfModelPolicy = DEFAULT_SELF_MODEL_POLICY,
) -> PersonalExperimentEvaluationResultV1:
    """Functional provider-free evaluation entry point."""

    return BuildPersonalExperimentEvaluation(reader, policy=policy).execute(request)


def validate_personal_experiment_evaluation_request(
    value: object,
) -> PersonalExperimentEvaluationRequestV1:
    if type(value) is not PersonalExperimentEvaluationRequestV1:
        raise PersonalExperimentEvaluationRequestInvalidError()
    request = value
    try:
        return PersonalExperimentEvaluationRequestV1(
            request.experiment_definition_id,
            request.experiment_definition_fingerprint,
            request.as_of,
        )
    except PersonalExperimentEvaluationError:
        raise
    except TypeError, ValueError, OverflowError, UnicodeError:
        raise PersonalExperimentEvaluationRequestInvalidError() from None


def validate_personal_experiment_result(
    value: object,
) -> PersonalExperimentEvaluationResultV1:
    if type(value) is not PersonalExperimentEvaluationResultV1:
        raise PersonalExperimentEvaluationError(PersonalExperimentEvaluationErrorCodeV1.INTERNAL)
    result = value
    try:
        encoded = result.to_json().encode("utf-8")
    except TypeError, ValueError, UnicodeError, OverflowError:
        raise PersonalExperimentEvaluationError(
            PersonalExperimentEvaluationErrorCodeV1.INTERNAL
        ) from None
    if len(encoded) > MAX_PERSONAL_EXPERIMENT_RESULT_BYTES:
        raise PersonalExperimentEvaluationResultTooLargeError()
    return result


def _read_current_report(reader: VaultReader) -> tuple[VaultSnapshot, ScanReport]:
    try:
        snapshot = reader.scan()
        report = build_report(snapshot)
    except Exception:
        raise PersonalExperimentEvaluationSourceUnavailableError() from None
    if type(snapshot) is not VaultSnapshot or type(report) is not ScanReport:
        raise PersonalExperimentEvaluationSourceUnavailableError()
    if report.manifest is None or not report.content_scan_complete:
        raise PersonalExperimentEvaluationSourceUnavailableError()
    return snapshot, report


def _resolve_current_goal(
    snapshot: VaultSnapshot,
    goal_source_uuid: UUID,
    as_of: datetime,
    policy: SelfModelPolicy,
) -> GrowthGoalIdentityV1 | None:
    selection = GrowthGoalSelectionV1(GrowthGoalSelectionModeV1.SELECTED_GOAL, goal_source_uuid)
    try:
        context = BuildGrowthGoalContext(
            _SnapshotReader(snapshot),
            policy=policy,
            clock=lambda: as_of,
        ).execute(GrowthEngineRequestV1(selection=selection))
    except GrowthGoalMissingError:
        return None
    except GrowthPolicyMismatchError:
        raise PersonalExperimentEvaluationPolicyMismatchError() from None
    except GrowthError:
        raise PersonalExperimentEvaluationSourceUnavailableError() from None
    except Exception:
        raise PersonalExperimentEvaluationSourceUnavailableError() from None
    if len(context.goals) != 1:
        return None
    return context.goals[0]


def _resolve_stage12_definition(
    report: ScanReport,
    definition: PersonalExperimentDefinitionRecordV1,
    current_goal_hash: str,
) -> tuple[DefinitionRecordV1 | None, PersonalExperimentResultStatusV1 | None]:
    matching = tuple(
        item
        for item in report.goal_progress_definitions
        if item.goal_source_uuid == definition.goal_source_uuid
        and item.goal_identity_fingerprint == current_goal_hash
    )
    if not matching:
        return None, PersonalExperimentResultStatusV1.NOT_COMPARABLE
    chain = validate_definition_chain(matching)
    if chain.issues or chain.state is not SupersessionChainStateV1.ONE_ACTIVE:
        return None, PersonalExperimentResultStatusV1.NOT_COMPARABLE
    active = cast(DefinitionRecordV1, chain.active_records[0])
    if (
        active.id != definition.goal_progress_definition_id
        or active.definition_fingerprint != definition.goal_progress_definition_fingerprint
    ):
        return active, PersonalExperimentResultStatusV1.SOURCE_CHANGED
    if active.goal_progress_policy_fingerprint != GOAL_PROGRESS_POLICY_FINGERPRINT:
        return active, PersonalExperimentResultStatusV1.NOT_COMPARABLE
    return active, None


def _has_goal_identity_drift(
    report: ScanReport,
    goal_source_uuid: UUID,
    current_goal_hash: str,
) -> bool:
    bindings: list[str] = []
    records = (
        report.goal_progress_definitions,
        report.goal_progress_observations,
        report.personal_experiment_definitions,
        report.personal_experiment_lifecycles,
        report.personal_experiment_observations,
        report.personal_experiment_reassessments,
    )
    for group in records:
        for record in group:
            if record.goal_source_uuid == goal_source_uuid:
                bindings.append(record.goal_identity_fingerprint)
    return bool(bindings) and any(value != current_goal_hash for value in bindings)


def _has_duplicate_active_experiment(
    report: ScanReport,
    *,
    goal_source_uuid: UUID,
    goal_identity_fingerprint: str,
) -> bool:
    """Fail closed when one exact Goal has more than one active experiment."""

    groups: dict[tuple[UUID, str, UUID, str], list[PersonalExperimentDefinitionRecordV1]] = {}
    for candidate in report.personal_experiment_definitions:
        if (
            candidate.goal_source_uuid != goal_source_uuid
            or candidate.goal_identity_fingerprint != goal_identity_fingerprint
        ):
            continue
        key = (
            cast(UUID, candidate.goal_source_uuid),
            candidate.goal_identity_fingerprint,
            cast(UUID, candidate.goal_progress_definition_id),
            candidate.goal_progress_definition_fingerprint,
        )
        groups.setdefault(key, []).append(candidate)

    active_count = 0
    for candidates in groups.values():
        definition_chain = validate_personal_experiment_definition_chain(candidates)
        if (
            definition_chain.issues
            or definition_chain.state is not PersonalExperimentChainStateV1.ONE_ACTIVE
        ):
            return True
        active_definition = definition_chain.active_records[0]
        if type(active_definition) is not PersonalExperimentDefinitionRecordV1:
            return True
        lifecycle_records = tuple(
            item
            for item in report.personal_experiment_lifecycles
            if item.experiment_definition_id == active_definition.id
            and item.experiment_definition_fingerprint
            == active_definition.experiment_definition_fingerprint
        )
        lifecycle = validate_personal_experiment_lifecycle_chain(lifecycle_records)
        if lifecycle.issues or lifecycle.state == "invalid":
            return True
        if lifecycle.state == "active":
            active_count += 1
            if active_count > 1:
                return True
    return False


def _collect_observations(
    report: ScanReport,
    *,
    definition: PersonalExperimentDefinitionRecordV1,
    stage12_definition: DefinitionRecordV1,
    lifecycle: PersonalExperimentLifecycleValidationV1,
    as_of: datetime,
) -> _ObservationCollection:
    experiment_observations = tuple(
        item
        for item in report.personal_experiment_observations
        if item.experiment_definition_id == definition.id
        and item.experiment_definition_fingerprint == definition.experiment_definition_fingerprint
    )
    if len(experiment_observations) > MAX_PERSONAL_EXPERIMENT_OBSERVATIONS:
        raise PersonalExperimentEvaluationResultTooLargeError()
    chain = validate_personal_experiment_observation_chain(experiment_observations)
    if chain.issues:
        raise _EvaluationSourceProblem(
            PersonalExperimentResultStatusV1.NOT_COMPARABLE,
            "chain_conflict",
        )

    stage12_observations = tuple(
        item
        for item in report.goal_progress_observations
        if item.goal_source_uuid == definition.goal_source_uuid
        and item.goal_identity_fingerprint == definition.goal_identity_fingerprint
        and item.progress_definition_id == stage12_definition.id
        and item.definition_fingerprint == stage12_definition.definition_fingerprint
    )
    stage12_active_ids = _stage12_active_observation_ids(stage12_observations)
    stage12_by_id: dict[UUID, tuple[ObservationRecordV1, ...]] = defaultdict(tuple)
    for observation in report.goal_progress_observations:
        if observation.id is None:
            continue
        stage12_by_id[cast(UUID, observation.id)] += (observation,)

    all_ids = {cast(UUID, item.id) for item in chain.active_records}
    included: list[PersonalExperimentObservationReferenceV1] = []
    excluded: list[PersonalExperimentExcludedObservationV1] = []
    activation = lifecycle.activation
    terminal = lifecycle.terminal
    activation_at = cast(datetime, activation.event_at) if activation is not None else None
    terminal_at = cast(datetime, terminal.event_at) if terminal is not None else None
    for record in experiment_observations:
        source_matches = stage12_by_id.get(cast(UUID, record.stage12_observation_id), ())
        if not source_matches:
            if cast(UUID, record.id) in all_ids:
                raise _EvaluationSourceProblem(
                    PersonalExperimentResultStatusV1.NOT_COMPARABLE,
                    "exact_source_missing",
                    tuple(included),
                    tuple(excluded),
                )
            reference = _reference_from_record(record, observed_at="unknown")
            excluded.append(
                PersonalExperimentExcludedObservationV1(
                    reference,
                    PersonalExperimentObservationExclusionReasonV1.SUPERSEDED,
                )
            )
            continue
        source = source_matches[0]
        reference = _reference_from_record(record, observed_at=source.observed_at)
        if cast(UUID, record.id) not in all_ids:
            excluded.append(
                PersonalExperimentExcludedObservationV1(
                    reference,
                    PersonalExperimentObservationExclusionReasonV1.SUPERSEDED,
                )
            )
            continue
        if len(source_matches) != 1:
            raise _EvaluationSourceProblem(
                PersonalExperimentResultStatusV1.NOT_COMPARABLE,
                "exact_source_missing",
                tuple(included),
                tuple(excluded),
            )
        if source.observation_fingerprint != record.stage12_observation_fingerprint:
            raise _EvaluationSourceProblem(
                PersonalExperimentResultStatusV1.SOURCE_CHANGED,
                "source_changed",
                tuple(included),
                tuple(excluded),
            )
        if cast(UUID, source.id) not in stage12_active_ids:
            raise _EvaluationSourceProblem(
                PersonalExperimentResultStatusV1.SOURCE_CHANGED,
                "source_changed",
                tuple(included),
                tuple(excluded),
            )
        if source.goal_progress_policy_fingerprint != GOAL_PROGRESS_POLICY_FINGERPRINT:
            raise _EvaluationSourceProblem(
                PersonalExperimentResultStatusV1.NOT_COMPARABLE,
                "policy_mismatch",
                tuple(included),
                tuple(excluded),
            )
        if (
            source.goal_source_uuid != definition.goal_source_uuid
            or source.goal_identity_fingerprint != definition.goal_identity_fingerprint
            or source.progress_definition_id != stage12_definition.id
            or source.definition_fingerprint != stage12_definition.definition_fingerprint
        ):
            raise _EvaluationSourceProblem(
                PersonalExperimentResultStatusV1.SOURCE_CHANGED,
                "source_changed",
                tuple(included),
                tuple(excluded),
            )
        if validate_observation_against_definition(source, stage12_definition):
            raise _EvaluationSourceProblem(
                PersonalExperimentResultStatusV1.NOT_COMPARABLE,
                "stage12_not_comparable",
                tuple(included),
                tuple(excluded),
            )
        source_time = source.observed_at
        if cast(datetime, record.observation_reviewed_at) > as_of:
            excluded.append(
                PersonalExperimentExcludedObservationV1(
                    reference,
                    PersonalExperimentObservationExclusionReasonV1.REVIEWED_AFTER_AS_OF,
                )
            )
        elif not isinstance(source_time, datetime):
            excluded.append(
                PersonalExperimentExcludedObservationV1(
                    reference,
                    PersonalExperimentObservationExclusionReasonV1.UNKNOWN_TIME,
                )
            )
        elif source_time > as_of:
            excluded.append(
                PersonalExperimentExcludedObservationV1(
                    reference,
                    PersonalExperimentObservationExclusionReasonV1.FUTURE_AS_OF,
                )
            )
        elif (
            activation_at is None
            or source_time < activation_at
            or (terminal_at is not None and source_time >= terminal_at)
        ):
            excluded.append(
                PersonalExperimentExcludedObservationV1(
                    reference,
                    PersonalExperimentObservationExclusionReasonV1.OUTSIDE_EXPERIMENT_WINDOW,
                )
            )
        else:
            included.append(reference)

    if len(included) > MAX_PERSONAL_EXPERIMENT_RESULT_REFERENCES:
        raise PersonalExperimentEvaluationResultTooLargeError()
    included.sort(key=lambda item: (str(item.stage12_observation_id), str(item.enrollment_id)))
    excluded.sort(key=lambda item: (str(item.stage12_observation_id), str(item.enrollment_id)))
    return _ObservationCollection(
        tuple(included),
        tuple(excluded),
        tuple(cast(UUID, item.stage12_observation_id) for item in included),
    )


def _stage12_active_observation_ids(observations: tuple[ObservationRecordV1, ...]) -> set[UUID]:
    groups: defaultdict[tuple[object, ...], list[ObservationRecordV1]] = defaultdict(list)
    for observation in observations:
        subject = (
            observation.milestone_id
            if observation.milestone_id is not None
            else observation.metric_id
        )
        event_time = (
            "unknown"
            if observation.observed_at == "unknown"
            else _format_timestamp(cast(datetime, observation.observed_at))
        )
        groups[
            (cast(ProgressModelV1, observation.progress_model).value, subject, event_time)
        ].append(observation)
    active: set[UUID] = set()
    for group in groups.values():
        chain = validate_observation_chain(group)
        if chain.issues:
            raise _EvaluationSourceProblem(
                PersonalExperimentResultStatusV1.NOT_COMPARABLE,
                "stage12_not_comparable",
            )
        active.update(cast(UUID, item.id) for item in chain.active_records)
    return active


def _resolve_baseline(
    report: ScanReport,
    *,
    definition: PersonalExperimentDefinitionRecordV1,
    stage12_definition: DefinitionRecordV1,
    activation_at: datetime,
) -> tuple[PersonalExperimentBaselineProvenanceV1 | None, PersonalExperimentResultStatusV1 | None]:
    strategy = cast(PersonalExperimentBaselineStrategyV1, definition.baseline_strategy)
    if strategy is PersonalExperimentBaselineStrategyV1.STAGE12_DEFINITION_EXPLICIT:
        if stage12_definition.baseline is None:
            return None, PersonalExperimentResultStatusV1.NOT_EVALUATED
        return (
            PersonalExperimentBaselineProvenanceV1(
                strategy=strategy,
                source_kind=PersonalExperimentBaselineSourceKindV1.STAGE12_DEFINITION,
                stage12_definition_id=stage12_definition.id,
                stage12_definition_fingerprint=stage12_definition.definition_fingerprint,
            ),
            None,
        )
    if (
        definition.baseline_observation_uuid is None
        or definition.baseline_observation_fingerprint is None
    ):
        return None, PersonalExperimentResultStatusV1.NOT_EVALUATED
    matches = tuple(
        item
        for item in report.goal_progress_observations
        if item.id == definition.baseline_observation_uuid
    )
    if not matches:
        return None, PersonalExperimentResultStatusV1.NOT_COMPARABLE
    if len(matches) != 1:
        return None, PersonalExperimentResultStatusV1.NOT_COMPARABLE
    observation = matches[0]
    if observation.observation_fingerprint != definition.baseline_observation_fingerprint:
        return None, PersonalExperimentResultStatusV1.SOURCE_CHANGED
    if (
        observation.goal_source_uuid != definition.goal_source_uuid
        or observation.goal_identity_fingerprint != definition.goal_identity_fingerprint
        or observation.progress_definition_id != stage12_definition.id
        or observation.definition_fingerprint != stage12_definition.definition_fingerprint
        or observation.id is None
    ):
        return None, PersonalExperimentResultStatusV1.SOURCE_CHANGED
    if observation.goal_progress_policy_fingerprint != GOAL_PROGRESS_POLICY_FINGERPRINT:
        return None, PersonalExperimentResultStatusV1.NOT_COMPARABLE
    if cast(UUID, observation.id) not in _stage12_active_observation_ids(
        tuple(
            item
            for item in report.goal_progress_observations
            if item.goal_source_uuid == definition.goal_source_uuid
            and item.goal_identity_fingerprint == definition.goal_identity_fingerprint
            and item.progress_definition_id == stage12_definition.id
            and item.definition_fingerprint == stage12_definition.definition_fingerprint
        )
    ):
        return None, PersonalExperimentResultStatusV1.SOURCE_CHANGED
    if observation.observed_at == "unknown":
        return None, PersonalExperimentResultStatusV1.NOT_EVALUATED
    if cast(datetime, observation.observed_at) >= activation_at:
        return None, PersonalExperimentResultStatusV1.NOT_EVALUATED
    return (
        PersonalExperimentBaselineProvenanceV1(
            strategy=strategy,
            source_kind=PersonalExperimentBaselineSourceKindV1.STAGE12_OBSERVATION,
            stage12_definition_id=stage12_definition.id,
            stage12_definition_fingerprint=stage12_definition.definition_fingerprint,
            stage12_observation_id=observation.id,
            stage12_observation_fingerprint=observation.observation_fingerprint,
            observed_at=observation.observed_at,
        ),
        None,
    )


def _effective_lifecycle(
    lifecycle: PersonalExperimentLifecycleValidationV1,
    as_of: datetime,
) -> _EffectiveLifecycle:
    activation = lifecycle.activation
    terminal = lifecycle.terminal
    if activation is None:
        return _EffectiveLifecycle("planned", None, None)
    if terminal is None or cast(datetime, terminal.event_at) > as_of:
        return _EffectiveLifecycle("active", activation, None)
    state = (
        "completed"
        if terminal.lifecycle_event is PersonalExperimentLifecycleEventV1.COMPLETION
        else "cancelled"
    )
    return _EffectiveLifecycle(state, activation, terminal)


def _filtered_snapshot(
    snapshot: VaultSnapshot,
    report: ScanReport,
    *,
    included_stage12_ids: set[UUID],
) -> VaultSnapshot:
    notes_by_path = {note.relative_path: note for note in report.notes}
    documents: list[MarkdownDocument] = []
    for document in snapshot.documents:
        note = notes_by_path.get(document.relative_path)
        if _is_stage12_observation(document.front_matter):
            if note is None or note.goal_progress_observation is None:
                continue
            if cast(UUID, note.goal_progress_observation.id) not in included_stage12_ids:
                continue
        documents.append(document)
    return VaultSnapshot(
        vault_path=snapshot.vault_path,
        manifest=snapshot.manifest,
        documents=tuple(documents),
        links=snapshot.links,
        attachments=snapshot.attachments,
        diagnostics=snapshot.diagnostics,
    )


def _is_stage12_observation(front_matter: Mapping[str, object]) -> bool:
    return (
        type(front_matter.get(GOAL_PROGRESS_MARKER)) is int
        and front_matter.get(GOAL_PROGRESS_MARKER) == GOAL_PROGRESS_MARKER_VALUE
        and front_matter.get("goal_progress_kind") == GoalProgressRecordKindV1.OBSERVATION.value
    )


def _reference_from_record(
    record: PersonalExperimentObservationRecordV1,
    *,
    observed_at: datetime | str,
) -> PersonalExperimentObservationReferenceV1:
    return PersonalExperimentObservationReferenceV1(
        enrollment_id=record.id,
        stage12_observation_id=record.stage12_observation_id,
        stage12_observation_fingerprint=record.stage12_observation_fingerprint,
        observed_at=observed_at,
        observation_reviewed_at=record.observation_reviewed_at,
    )


def _result(
    request: PersonalExperimentEvaluationRequestV1,
    *,
    status: PersonalExperimentResultStatusV1,
    definition: PersonalExperimentDefinitionRecordV1 | None = None,
    current_goal: GrowthGoalIdentityV1 | None = None,
    stage12_definition: DefinitionRecordV1 | None = None,
    lifecycle: object | None = None,
    effective: _EffectiveLifecycle | None = None,
    baseline: PersonalExperimentBaselineProvenanceV1 | None = None,
    stage12_status: GoalProgressStatusV1 | str | None = None,
    stage12_current_observation_ids: tuple[UUID | str, ...] = (),
    included_observations: tuple[PersonalExperimentObservationReferenceV1, ...] = (),
    excluded_observations: tuple[PersonalExperimentExcludedObservationV1, ...] = (),
    reasons: tuple[str, ...] = (),
) -> PersonalExperimentEvaluationResultV1:
    activation = effective.activation if effective is not None else None
    terminal = effective.terminal if effective is not None else None
    goal_source_uuid = cast(UUID, definition.goal_source_uuid) if definition is not None else None
    goal_identity_fingerprint = (
        definition.goal_identity_fingerprint if definition is not None else None
    )
    if current_goal is not None:
        goal_source_uuid = cast(UUID, current_goal.source_note_uuid)
        goal_identity_fingerprint = goal_progress_hash_json(current_goal.as_dict())
    result = PersonalExperimentEvaluationResultV1(
        experiment_definition_id=request.experiment_definition_id,
        experiment_definition_fingerprint=request.experiment_definition_fingerprint,
        status=status,
        as_of=request.as_of,
        goal_source_uuid=goal_source_uuid,
        goal_identity_fingerprint=goal_identity_fingerprint,
        stage12_definition_id=stage12_definition.id if stage12_definition is not None else None,
        stage12_definition_fingerprint=(
            stage12_definition.definition_fingerprint if stage12_definition is not None else None
        ),
        lifecycle_state=(effective.state if effective is not None else "unknown"),
        activation_record_id=activation.id if activation is not None else None,
        activation_at=activation.event_at if activation is not None else None,
        terminal_record_id=terminal.id if terminal is not None else None,
        terminal_at=terminal.event_at if terminal is not None else None,
        baseline=baseline,
        stage12_status=stage12_status,
        stage12_current_observation_ids=stage12_current_observation_ids,
        included_observations=included_observations,
        excluded_observations=excluded_observations,
        reasons=reasons,
    )
    return validate_personal_experiment_result(result)


class _EvaluationSourceProblem(Exception):
    """Internal bounded branch for a typed result status."""

    def __init__(
        self,
        status: PersonalExperimentResultStatusV1,
        reason: str,
        included: tuple[PersonalExperimentObservationReferenceV1, ...] = (),
        excluded: tuple[PersonalExperimentExcludedObservationV1, ...] = (),
    ) -> None:
        self.status = status
        self.reason = reason
        self.included = included
        self.excluded = excluded
        super().__init__(reason)


def _map_stage12_status(status: GoalProgressStatusV1 | str) -> PersonalExperimentResultStatusV1:
    normalized = GoalProgressStatusV1(status)
    mapping = {
        GoalProgressStatusV1.TARGET_MET: PersonalExperimentResultStatusV1.TARGET_MET,
        GoalProgressStatusV1.TOWARD_TARGET: PersonalExperimentResultStatusV1.OBSERVED_TOWARD_TARGET,
        GoalProgressStatusV1.AWAY_FROM_TARGET: (
            PersonalExperimentResultStatusV1.OBSERVED_AWAY_FROM_TARGET
        ),
        GoalProgressStatusV1.UNCHANGED: PersonalExperimentResultStatusV1.OBSERVED_NO_CLEAR_CHANGE,
        GoalProgressStatusV1.MILESTONE_OBSERVATIONS_AVAILABLE: (
            PersonalExperimentResultStatusV1.INSUFFICIENT_EVIDENCE
        ),
        GoalProgressStatusV1.INSUFFICIENT_OBSERVATIONS: (
            PersonalExperimentResultStatusV1.INSUFFICIENT_EVIDENCE
        ),
        GoalProgressStatusV1.DEFINITION_MISSING: PersonalExperimentResultStatusV1.NOT_EVALUATED,
        GoalProgressStatusV1.GOAL_SOURCE_CHANGED: PersonalExperimentResultStatusV1.SOURCE_CHANGED,
        GoalProgressStatusV1.NOT_COMPARABLE: PersonalExperimentResultStatusV1.NOT_COMPARABLE,
    }
    return mapping[normalized]


def _map_stage12_error(
    error: GoalProgressError,
) -> tuple[PersonalExperimentResultStatusV1, str]:
    code = error.code
    if code in {"GOAL_PROGRESS_GOAL_SOURCE_CHANGED", "GOAL_PROGRESS_OBSERVATION_SOURCE_CHANGED"}:
        return PersonalExperimentResultStatusV1.SOURCE_CHANGED, "source_changed"
    if code == "GOAL_PROGRESS_POLICY_MISMATCH":
        return PersonalExperimentResultStatusV1.NOT_COMPARABLE, "policy_mismatch"
    if code == "GOAL_PROGRESS_DEFINITION_MISSING":
        return PersonalExperimentResultStatusV1.NOT_EVALUATED, "stage12_definition_missing"
    return PersonalExperimentResultStatusV1.NOT_COMPARABLE, "stage12_not_comparable"


def _reference_observation_time(value: object) -> datetime | str:
    if value == "unknown":
        return "unknown"
    return _parse_exact_utc(value)


def _parse_observation_time(value: object) -> datetime | str:
    return _reference_observation_time(value)


def _parse_error_code(
    value: PersonalExperimentEvaluationErrorCodeV1 | str,
) -> PersonalExperimentEvaluationErrorCodeV1:
    if isinstance(value, PersonalExperimentEvaluationErrorCodeV1):
        return value
    try:
        return PersonalExperimentEvaluationErrorCodeV1(value)
    except ValueError:
        return PersonalExperimentEvaluationErrorCodeV1.INTERNAL


def _parse_enum(value: object, enum_type: type[StrEnum]) -> StrEnum:
    if isinstance(value, enum_type):
        return value
    if type(value) is not str:
        raise ValueError("enum value is invalid")
    try:
        return enum_type(value)
    except ValueError:
        raise ValueError("enum value is invalid") from None


def _parse_hash(value: object) -> str:
    if type(value) is not str or _HASH_PATTERN.fullmatch(value) is None:
        raise ValueError("hash is invalid")
    return value


def _parse_exact_utc(value: object) -> datetime:
    parsed = parse_rfc3339(value)
    if parsed.utcoffset() != UTC.utcoffset(parsed):
        raise ValueError("UTC timestamp is required")
    return parsed.astimezone(UTC)


def _format_timestamp(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


__all__ = [
    "MAX_PERSONAL_EXPERIMENT_RESULT_BYTES",
    "MAX_PERSONAL_EXPERIMENT_RESULT_REFERENCES",
    "PERSONAL_EXPERIMENT_CONTRACT_ID",
    "BuildPersonalExperimentEvaluation",
    "BuildPersonalExperimentEvaluationV1",
    "PersonalExperimentBaselineProvenance",
    "PersonalExperimentBaselineProvenanceV1",
    "PersonalExperimentBaselineSourceKindV1",
    "PersonalExperimentCaveatV1",
    "PersonalExperimentEvaluationBuilder",
    "PersonalExperimentEvaluationError",
    "PersonalExperimentEvaluationErrorCode",
    "PersonalExperimentEvaluationErrorCodeV1",
    "PersonalExperimentEvaluationPolicyMismatchError",
    "PersonalExperimentEvaluationRequest",
    "PersonalExperimentEvaluationRequestInvalidError",
    "PersonalExperimentEvaluationRequestV1",
    "PersonalExperimentEvaluationResult",
    "PersonalExperimentEvaluationResultTooLargeError",
    "PersonalExperimentEvaluationResultV1",
    "PersonalExperimentEvaluationSourceUnavailableError",
    "PersonalExperimentEvaluator",
    "PersonalExperimentExcludedObservation",
    "PersonalExperimentExcludedObservationV1",
    "PersonalExperimentObservationExclusionReasonV1",
    "PersonalExperimentObservationReference",
    "PersonalExperimentObservationReferenceV1",
    "PersonalExperimentProvenance",
    "PersonalExperimentProvenanceV1",
    "PersonalExperimentResult",
    "PersonalExperimentResultStatus",
    "PersonalExperimentResultStatusV1",
    "PersonalExperimentResultV1",
    "evaluate_personal_experiment",
    "validate_personal_experiment_evaluation_request",
    "validate_personal_experiment_result",
]
