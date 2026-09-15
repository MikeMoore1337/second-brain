"""Provider-free Stage 15.1 source binding and candidate core.

This module deliberately stops at immutable source snapshots and deterministic
candidate derivation.  It does not read or write an operational store, expose
HTTP routes, touch the vault, call a provider, or schedule background work.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import Final, cast
from uuid import UUID

from second_brain.application.behavioral_self_model import (
    POLICY_FINGERPRINT as BEHAVIORAL_POLICY_FINGERPRINT,
)
from second_brain.application.behavioral_self_model import (
    BehavioralPatternStateV1,
    BehavioralPatternTypeV1,
)
from second_brain.application.goal_progress import GOAL_PROGRESS_POLICY_FINGERPRINT
from second_brain.application.goal_progress_read import (
    GoalProgressResultV1,
    GoalProgressStatusV1,
    validate_goal_progress_result,
)
from second_brain.application.growth import (
    GROWTH_MAPPING_POLICY_FINGERPRINT,
    GROWTH_POLICY_FINGERPRINT,
    GrowthEngineResultV1,
    GrowthGoalIdentityV1,
    GrowthGoalRelationResultV1,
    GrowthRelationStateV1,
    growth_hash_json,
)
from second_brain.application.personal_experiments import (
    PERSONAL_EXPERIMENT_POLICY_FINGERPRINT,
    PersonalExperimentDispositionV1,
    PersonalExperimentReassessmentRecordV1,
)
from second_brain.application.personal_experiments_evaluator import (
    PersonalExperimentEvaluationResultV1,
    PersonalExperimentResultStatusV1,
    validate_personal_experiment_result,
)
from second_brain.application.prospective_audit import (
    PROSPECTIVE_CALIBRATION_POLICY_FINGERPRINT,
    PROSPECTIVE_CALIBRATION_POLICY_ID,
    ProspectiveCalibrationResultV1,
    serialize_prospective_calibration_result,
    validate_prospective_calibration_result,
)
from second_brain.domain.models import parse_rfc3339, parse_uuid7

type AdaptiveHashV1 = str

ADAPTIVE_CONTRACT_ID: Final[str] = "adaptive-cognitive-twin-v1"
ADAPTIVE_CONTRACT_VERSION: Final[str] = ADAPTIVE_CONTRACT_ID
ADAPTIVE_SNAPSHOT_VERSION: Final[str] = "1"
ADAPTIVE_DERIVATION_VERSION: Final[str] = "adaptive-cognitive-twin-derivation-v1"
ADAPTIVE_CANDIDATE_POLICY_ID: Final[str] = "adaptive-candidate-exact-source-pack-v1"
ADAPTIVE_PROFILE_POLICY_ID: Final[str] = "stage15-adaptive-profile-v1"
ADAPTIVE_EVALUATION_POLICY_ID: Final[str] = "stage15-descriptive-evaluation-v1"
ADAPTIVE_STORE_FORMAT_VERSION: Final[int] = 1

ADAPTIVE_POLICY_CANONICAL_JSON: Final[str] = (
    '{"adaptation_catalog":"closed-stage15-owned-projection-v1",'
    '"candidate":"exact-source-pack-deterministic-v1",'
    '"causality":"descriptive-non-causal-v1",'
    '"contract":"adaptive-cognitive-twin-v1",'
    '"evaluation":"explicit-later-comparison-v1",'
    '"goal":"growth-goal-identity-exact-v1",'
    '"persistence":"append-only-operational-outside-vault-v1",'
    '"provider":"forbidden",'
    '"source_stage10":"growth-exact-behavioral-cohort-mapping-v1",'
    '"source_stage12":"goal-progress-exact-result-v1",'
    '"source_stage14":"terminal-experiment-reviewed-reassessment-v1",'
    '"source_stage9":"prospective-calibration-exact-result-v1",'
    '"version":1}'
)
ADAPTIVE_POLICY_FINGERPRINT: Final[AdaptiveHashV1] = (
    "sha256:14d5e0844bbab502854307b0513fae5e8a9785c1b5ec051e5a0bb51427878920"
)
ADAPTIVE_NON_CAUSAL_LANGUAGE: Final[str] = "observed-change-not-causation-v1"

MAX_ADAPTIVE_SOURCE_PACK_BYTES: Final[int] = 128 * 1024
MAX_ADAPTIVE_CANDIDATE_BYTES: Final[int] = 64 * 1024
MAX_ADAPTIVE_PROFILE_BYTES: Final[int] = 16 * 1024
MAX_ADAPTIVE_REASONS: Final[int] = 16
MAX_ADAPTIVE_CAVEATS: Final[int] = 16

_HASH_PATTERN: Final[re.Pattern[str]] = re.compile(r"sha256:[0-9a-f]{64}\Z", re.ASCII)


class AdaptiveSourceReadinessV1(StrEnum):
    """Readiness of one exact source family."""

    EXACT_CURRENT = "exact_current"
    SOURCE_MISSING = "source_missing"
    UNAVAILABLE = "unavailable"
    STALE = "stale"
    SOURCE_CHANGED = "source_changed"
    NOT_COMPARABLE = "not_comparable"
    POLICY_MISMATCH = "policy_mismatch"


class AdaptiveSufficiencyStateV1(StrEnum):
    """Closed overall gate states."""

    CANDIDATE = "candidate"
    HOLD = "hold"
    INSUFFICIENT = "insufficient"
    NOT_COMPARABLE = "not_comparable"
    SOURCE_CHANGED = "source_changed"
    POLICY_MISMATCH = "policy_mismatch"


class AdaptiveCandidateReasonV1(StrEnum):
    """Closed explanation vocabulary for deterministic candidate output."""

    STAGE9_CALIBRATION_AVAILABLE = "stage9_calibration_available"
    STAGE9_CALIBRATION_NOT_EVALUABLE = "stage9_calibration_not_evaluable"
    STAGE10_SUPPORTS_GOAL = "stage10_supports_goal"
    STAGE10_CONFLICTS_WITH_GOAL = "stage10_conflicts_with_goal"
    STAGE10_STATE_NOT_COMPARABLE = "stage10_state_not_comparable"
    STAGE12_TARGET_MET = "stage12_target_met"
    STAGE12_TOWARD_TARGET = "stage12_toward_target"
    STAGE12_AWAY_FROM_TARGET = "stage12_away_from_target"
    STAGE12_STATE_NOT_COMPARABLE = "stage12_state_not_comparable"
    STAGE14_TERMINAL_REVIEW_AVAILABLE = "stage14_terminal_review_available"
    STAGE14_RESULT_NOT_COMPARABLE = "stage14_result_not_comparable"
    STAGE14_OWNER_HOLD = "stage14_owner_hold"
    SOURCES_AGREE_ON_FOCUS = "sources_agree_on_focus"
    SOURCES_CONFLICT = "sources_conflict"
    NO_SAFE_DELTA = "no_safe_delta"
    MISSING_EXACT_SOURCE = "missing_exact_source"
    SOURCE_DRIFT = "source_drift"
    POLICY_MISMATCH = "policy_mismatch"


class Stage15ProjectionFocusV1(StrEnum):
    """Closed Stage15-owned projection focus catalog."""

    HOLD_CURRENT_PROFILE = "hold_current_profile"
    PROGRESS_CONTEXT = "progress_context"
    BEHAVIORAL_CONTEXT = "behavioral_context"
    TRADEOFF_CONTEXT = "tradeoff_context"
    EXPERIMENT_CONTEXT = "experiment_context"
    CALIBRATION_CONTEXT = "calibration_context"


class Stage15InteractionModeV1(StrEnum):
    """Closed interaction modes; none is a prompt or executable policy."""

    BALANCED_EVIDENCE = "balanced_evidence"
    EVIDENCE_SEQUENCE = "evidence_sequence"
    EXPLICIT_TRADEOFF = "explicit_tradeoff"
    FOREGROUND_REVIEW = "foreground_review"


class Stage15MeasureV1(StrEnum):
    """Closed descriptive measures for a later explicit comparison."""

    PROGRESS_STATE = "progress_state"
    BEHAVIORAL_STATE = "behavioral_state"
    EXPERIMENT_STATE = "experiment_state"
    CALIBRATION_LINKAGE = "calibration_linkage"


class Stage15CaveatV1(StrEnum):
    """Fixed safe caveats carried by every candidate."""

    EXACT_SOURCE_SNAPSHOT = "exact_source_snapshot"
    OWNER_REVIEW_REQUIRED = "owner_review_required"
    NO_AUTOMATIC_ACTIVATION = "no_automatic_activation"
    DESCRIPTIVE_NON_CAUSAL = "descriptive_non_causal"


class AdaptiveCognitiveTwinInputError(ValueError):
    """A source or candidate violated the bounded Stage15 input contract."""


def _canonical_json(value: object) -> str:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError, UnicodeError, OverflowError) as exc:
        raise AdaptiveCognitiveTwinInputError("adaptive JSON is invalid") from exc


def canonical_adaptive_json_bytes(value: object) -> bytes:
    """Serialize bounded JSON as canonical UTF-8 bytes."""

    return _canonical_json(value).encode("utf-8")


def adaptive_hash_json(value: object) -> AdaptiveHashV1:
    """Hash canonical UTF-8 JSON without exposing source bodies."""

    return "sha256:" + hashlib.sha256(canonical_adaptive_json_bytes(value)).hexdigest()


def adaptive_hash_bytes(value: bytes) -> AdaptiveHashV1:
    """Hash already-canonical bytes from an existing source serializer."""

    if type(value) is not bytes:
        raise AdaptiveCognitiveTwinInputError("adaptive bytes are invalid")
    return "sha256:" + hashlib.sha256(value).hexdigest()


def validate_adaptive_hash(value: object) -> AdaptiveHashV1:
    """Validate the exact Stage15 hash syntax."""

    if type(value) is not str or _HASH_PATTERN.fullmatch(value) is None:
        raise AdaptiveCognitiveTwinInputError("adaptive hash is invalid")
    return value


def _enum[EnumT: StrEnum](enum_type: type[EnumT], value: object, label: str) -> EnumT:
    if isinstance(value, enum_type):
        return value
    if type(value) is not str:
        raise AdaptiveCognitiveTwinInputError(f"{label} is invalid")
    try:
        return enum_type(value)
    except TypeError, ValueError:
        raise AdaptiveCognitiveTwinInputError(f"{label} is invalid") from None


def _uuid(value: object, label: str) -> UUID:
    try:
        return parse_uuid7(value)
    except TypeError, ValueError:
        raise AdaptiveCognitiveTwinInputError(f"{label} is invalid") from None


def _timestamp(value: object, label: str) -> datetime:
    try:
        parsed = parse_rfc3339(value)
    except TypeError, ValueError:
        raise AdaptiveCognitiveTwinInputError(f"{label} is invalid") from None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise AdaptiveCognitiveTwinInputError(f"{label} is invalid")
    return parsed.astimezone(UTC)


def _format_timestamp(value: datetime) -> str:
    rendered = value.astimezone(UTC).isoformat(
        timespec="microseconds" if value.microsecond else "seconds"
    )
    return rendered.removesuffix("+00:00") + "Z"


def _hash_or_none(value: object | None, label: str) -> AdaptiveHashV1 | None:
    if value is None:
        return None
    try:
        return validate_adaptive_hash(value)
    except AdaptiveCognitiveTwinInputError:
        raise AdaptiveCognitiveTwinInputError(f"{label} is invalid") from None


def _non_negative_int_or_none(value: object | None, label: str) -> int | None:
    if value is None:
        return None
    if type(value) is not int or value < 0:
        raise AdaptiveCognitiveTwinInputError(f"{label} is invalid")
    return value


def _tuple_enums(
    values: object,
    enum_type: type[StrEnum],
    label: str,
    maximum: int,
) -> tuple[StrEnum, ...]:
    if type(values) is not tuple or len(values) > maximum:
        raise AdaptiveCognitiveTwinInputError(f"{label} is invalid")
    normalized = tuple(_enum(enum_type, value, label) for value in values)
    if len(set(normalized)) != len(normalized):
        raise AdaptiveCognitiveTwinInputError(f"{label} are duplicated")
    return normalized


def _ordered_unique[EnumT: StrEnum](values: Sequence[EnumT]) -> tuple[EnumT, ...]:
    seen: set[EnumT] = set()
    result: list[EnumT] = []
    for value in values:
        if value not in seen:
            result.append(value)
            seen.add(value)
    if len(result) > MAX_ADAPTIVE_REASONS:
        raise AdaptiveCognitiveTwinInputError("adaptive reasons are too large")
    return tuple(result)


def validate_adaptive_policy() -> AdaptiveHashV1:
    """Recompute the single Stage15 policy fingerprint."""

    if adaptive_hash_bytes(ADAPTIVE_POLICY_CANONICAL_JSON.encode("utf-8")) != (
        ADAPTIVE_POLICY_FINGERPRINT
    ):
        raise AdaptiveCognitiveTwinInputError("adaptive policy fingerprint mismatch")
    return ADAPTIVE_POLICY_FINGERPRINT


@dataclass(frozen=True, slots=True)
class GoalSourceSnapshotV1:
    """Exact current Goal identity without Goal body or labels."""

    readiness: AdaptiveSourceReadinessV1 | str
    goal_source_uuid: UUID | str
    goal_identity_fingerprint: AdaptiveHashV1 | None
    growth_policy_fingerprint: AdaptiveHashV1 | None
    source_fingerprint: AdaptiveHashV1 | None

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "readiness", _enum(AdaptiveSourceReadinessV1, self.readiness, "Goal readiness")
        )
        object.__setattr__(self, "goal_source_uuid", _uuid(self.goal_source_uuid, "Goal UUID"))
        object.__setattr__(
            self,
            "goal_identity_fingerprint",
            _hash_or_none(self.goal_identity_fingerprint, "Goal identity fingerprint"),
        )
        object.__setattr__(
            self,
            "growth_policy_fingerprint",
            _hash_or_none(self.growth_policy_fingerprint, "Growth policy fingerprint"),
        )
        object.__setattr__(
            self,
            "source_fingerprint",
            _hash_or_none(self.source_fingerprint, "Goal source fingerprint"),
        )
        if self.readiness is AdaptiveSourceReadinessV1.EXACT_CURRENT and (
            self.goal_identity_fingerprint is None
            or self.growth_policy_fingerprint != GROWTH_POLICY_FINGERPRINT
            or self.source_fingerprint is None
        ):
            raise AdaptiveCognitiveTwinInputError("exact Goal snapshot is incomplete")

    def as_dict(self) -> dict[str, object]:
        return {
            "readiness": cast(AdaptiveSourceReadinessV1, self.readiness).value,
            "goal_source_uuid": str(self.goal_source_uuid),
            "goal_identity_fingerprint": self.goal_identity_fingerprint,
            "growth_policy_fingerprint": self.growth_policy_fingerprint,
            "source_fingerprint": self.source_fingerprint,
        }


@dataclass(frozen=True, slots=True)
class Stage9CalibrationSnapshotV1:
    """Exact Stage9 aggregate reference plus its verified generation."""

    readiness: AdaptiveSourceReadinessV1 | str
    generation_id: UUID | str | None
    as_of: datetime | str
    result_fingerprint: AdaptiveHashV1 | None
    policy_id: str | None
    policy_fingerprint: AdaptiveHashV1 | None
    audited_operations: int | None
    linked_actual_decisions: int | None
    evaluated_predictions: int | None

    def __post_init__(self) -> None:
        readiness = _enum(AdaptiveSourceReadinessV1, self.readiness, "Stage9 readiness")
        object.__setattr__(self, "readiness", readiness)
        if self.generation_id is not None:
            object.__setattr__(
                self, "generation_id", _uuid(self.generation_id, "Stage9 generation")
            )
        object.__setattr__(self, "as_of", _timestamp(self.as_of, "Stage9 as_of"))
        object.__setattr__(
            self,
            "result_fingerprint",
            _hash_or_none(self.result_fingerprint, "Stage9 result fingerprint"),
        )
        object.__setattr__(
            self,
            "policy_fingerprint",
            _hash_or_none(self.policy_fingerprint, "Stage9 policy fingerprint"),
        )
        for name in ("audited_operations", "linked_actual_decisions", "evaluated_predictions"):
            object.__setattr__(
                self, name, _non_negative_int_or_none(getattr(self, name), f"Stage9 {name}")
            )
        if self.policy_id is not None and type(self.policy_id) is not str:
            raise AdaptiveCognitiveTwinInputError("Stage9 policy id is invalid")
        if readiness is AdaptiveSourceReadinessV1.EXACT_CURRENT and (
            self.generation_id is None
            or self.result_fingerprint is None
            or self.policy_id != PROSPECTIVE_CALIBRATION_POLICY_ID
            or self.policy_fingerprint != PROSPECTIVE_CALIBRATION_POLICY_FINGERPRINT
            or self.audited_operations is None
            or self.linked_actual_decisions is None
            or self.evaluated_predictions is None
        ):
            raise AdaptiveCognitiveTwinInputError("exact Stage9 snapshot is incomplete")

    def as_dict(self) -> dict[str, object]:
        return {
            "readiness": cast(AdaptiveSourceReadinessV1, self.readiness).value,
            "generation_id": str(self.generation_id) if self.generation_id is not None else None,
            "as_of": _format_timestamp(cast(datetime, self.as_of)),
            "result_fingerprint": self.result_fingerprint,
            "policy_id": self.policy_id,
            "policy_fingerprint": self.policy_fingerprint,
            "audited_operations": self.audited_operations,
            "linked_actual_decisions": self.linked_actual_decisions,
            "evaluated_predictions": self.evaluated_predictions,
        }


@dataclass(frozen=True, slots=True)
class Stage10BehavioralSnapshotV1:
    """Exact Stage10/Growth relation reference for one Goal."""

    readiness: AdaptiveSourceReadinessV1 | str
    goal_source_uuid: UUID | str
    goal_identity_fingerprint: AdaptiveHashV1
    growth_policy_fingerprint: AdaptiveHashV1 | None
    behavioral_policy_fingerprint: AdaptiveHashV1 | None
    cohort_fingerprint: AdaptiveHashV1 | None
    pattern_fingerprint: AdaptiveHashV1 | None
    behavioral_source_fingerprint: AdaptiveHashV1 | None
    behavioral_provenance_fingerprint: AdaptiveHashV1 | None
    pattern_type: BehavioralPatternTypeV1 | str | None
    pattern_state: BehavioralPatternStateV1 | str | None
    mapping_id: UUID | str | None
    mapping_fingerprint: AdaptiveHashV1 | None
    mapping_policy_fingerprint: AdaptiveHashV1 | None
    relation_state: GrowthRelationStateV1 | str | None

    def __post_init__(self) -> None:
        readiness = _enum(AdaptiveSourceReadinessV1, self.readiness, "Stage10 readiness")
        object.__setattr__(self, "readiness", readiness)
        object.__setattr__(
            self, "goal_source_uuid", _uuid(self.goal_source_uuid, "Stage10 Goal UUID")
        )
        object.__setattr__(
            self,
            "goal_identity_fingerprint",
            validate_adaptive_hash(self.goal_identity_fingerprint),
        )
        for name in (
            "growth_policy_fingerprint",
            "behavioral_policy_fingerprint",
            "cohort_fingerprint",
            "pattern_fingerprint",
            "behavioral_source_fingerprint",
            "behavioral_provenance_fingerprint",
            "mapping_fingerprint",
            "mapping_policy_fingerprint",
        ):
            object.__setattr__(self, name, _hash_or_none(getattr(self, name), f"Stage10 {name}"))
        if self.mapping_id is not None:
            object.__setattr__(self, "mapping_id", _uuid(self.mapping_id, "Stage10 mapping id"))
        if self.pattern_type is not None:
            object.__setattr__(
                self,
                "pattern_type",
                _enum(BehavioralPatternTypeV1, self.pattern_type, "Stage10 pattern type"),
            )
        if self.pattern_state is not None:
            object.__setattr__(
                self,
                "pattern_state",
                _enum(BehavioralPatternStateV1, self.pattern_state, "Stage10 pattern state"),
            )
        if self.relation_state is not None:
            object.__setattr__(
                self,
                "relation_state",
                _enum(GrowthRelationStateV1, self.relation_state, "Stage10 relation state"),
            )
        if readiness is AdaptiveSourceReadinessV1.EXACT_CURRENT and (
            self.growth_policy_fingerprint != GROWTH_POLICY_FINGERPRINT
            or self.behavioral_policy_fingerprint != BEHAVIORAL_POLICY_FINGERPRINT
            or self.relation_state is None
        ):
            raise AdaptiveCognitiveTwinInputError("exact Stage10 snapshot is incomplete")
        if self.relation_state in {
            GrowthRelationStateV1.SUPPORTS_GOAL,
            GrowthRelationStateV1.CONFLICTS_WITH_GOAL,
            GrowthRelationStateV1.NEUTRAL_OR_UNKNOWN,
        } and (
            self.mapping_id is None
            or self.mapping_fingerprint is None
            or self.mapping_policy_fingerprint != GROWTH_MAPPING_POLICY_FINGERPRINT
        ):
            raise AdaptiveCognitiveTwinInputError("binary Stage10 relation is missing mapping")

    def as_dict(self) -> dict[str, object]:
        def enum_value(value: StrEnum | None) -> str | None:
            return value.value if value is not None else None

        return {
            "readiness": cast(AdaptiveSourceReadinessV1, self.readiness).value,
            "goal_source_uuid": str(self.goal_source_uuid),
            "goal_identity_fingerprint": self.goal_identity_fingerprint,
            "growth_policy_fingerprint": self.growth_policy_fingerprint,
            "behavioral_policy_fingerprint": self.behavioral_policy_fingerprint,
            "cohort_fingerprint": self.cohort_fingerprint,
            "pattern_fingerprint": self.pattern_fingerprint,
            "behavioral_source_fingerprint": self.behavioral_source_fingerprint,
            "behavioral_provenance_fingerprint": self.behavioral_provenance_fingerprint,
            "pattern_type": enum_value(cast(StrEnum | None, self.pattern_type)),
            "pattern_state": enum_value(cast(StrEnum | None, self.pattern_state)),
            "mapping_id": str(self.mapping_id) if self.mapping_id is not None else None,
            "mapping_fingerprint": self.mapping_fingerprint,
            "mapping_policy_fingerprint": self.mapping_policy_fingerprint,
            "relation_state": enum_value(cast(StrEnum | None, self.relation_state)),
        }


@dataclass(frozen=True, slots=True)
class Stage12ProgressSnapshotV1:
    """Exact Stage12 result/definition reference for one Goal."""

    readiness: AdaptiveSourceReadinessV1 | str
    goal_source_uuid: UUID | str
    goal_identity_fingerprint: AdaptiveHashV1
    progress_result_fingerprint: AdaptiveHashV1 | None
    progress_policy_fingerprint: AdaptiveHashV1 | None
    progress_as_of: datetime | str
    definition_id: UUID | str | None
    definition_fingerprint: AdaptiveHashV1 | None
    status: GoalProgressStatusV1 | str | None

    def __post_init__(self) -> None:
        readiness = _enum(AdaptiveSourceReadinessV1, self.readiness, "Stage12 readiness")
        object.__setattr__(self, "readiness", readiness)
        object.__setattr__(
            self, "goal_source_uuid", _uuid(self.goal_source_uuid, "Stage12 Goal UUID")
        )
        object.__setattr__(
            self,
            "goal_identity_fingerprint",
            validate_adaptive_hash(self.goal_identity_fingerprint),
        )
        for name in (
            "progress_result_fingerprint",
            "progress_policy_fingerprint",
            "definition_fingerprint",
        ):
            object.__setattr__(self, name, _hash_or_none(getattr(self, name), f"Stage12 {name}"))
        object.__setattr__(self, "progress_as_of", _timestamp(self.progress_as_of, "Stage12 as_of"))
        if self.definition_id is not None:
            object.__setattr__(
                self, "definition_id", _uuid(self.definition_id, "Stage12 definition id")
            )
        if self.status is not None:
            object.__setattr__(
                self, "status", _enum(GoalProgressStatusV1, self.status, "Stage12 status")
            )
        if readiness is AdaptiveSourceReadinessV1.EXACT_CURRENT and (
            self.progress_result_fingerprint is None
            or self.progress_policy_fingerprint != GOAL_PROGRESS_POLICY_FINGERPRINT
            or self.status is None
        ):
            raise AdaptiveCognitiveTwinInputError("exact Stage12 snapshot is incomplete")

    def as_dict(self) -> dict[str, object]:
        return {
            "readiness": cast(AdaptiveSourceReadinessV1, self.readiness).value,
            "goal_source_uuid": str(self.goal_source_uuid),
            "goal_identity_fingerprint": self.goal_identity_fingerprint,
            "progress_result_fingerprint": self.progress_result_fingerprint,
            "progress_policy_fingerprint": self.progress_policy_fingerprint,
            "progress_as_of": _format_timestamp(cast(datetime, self.progress_as_of)),
            "definition_id": str(self.definition_id) if self.definition_id is not None else None,
            "definition_fingerprint": self.definition_fingerprint,
            "status": (
                cast(GoalProgressStatusV1, self.status).value if self.status is not None else None
            ),
        }


@dataclass(frozen=True, slots=True)
class Stage14ExperimentSelectorV1:
    """Explicit experiment selector; there is no latest/timestamp fallback."""

    experiment_definition_id: UUID | str
    experiment_definition_fingerprint: AdaptiveHashV1

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "experiment_definition_id",
            _uuid(self.experiment_definition_id, "Stage14 experiment id"),
        )
        object.__setattr__(
            self,
            "experiment_definition_fingerprint",
            validate_adaptive_hash(self.experiment_definition_fingerprint),
        )

    def as_dict(self) -> dict[str, object]:
        return {
            "experiment_definition_id": str(self.experiment_definition_id),
            "experiment_definition_fingerprint": self.experiment_definition_fingerprint,
        }


@dataclass(frozen=True, slots=True)
class Stage14ExperimentSnapshotV1:
    """Exact terminal Stage14 result and one reviewed reassessment."""

    readiness: AdaptiveSourceReadinessV1 | str
    goal_source_uuid: UUID | str
    goal_identity_fingerprint: AdaptiveHashV1
    experiment_definition_id: UUID | str | None
    experiment_definition_fingerprint: AdaptiveHashV1 | None
    terminal_result_fingerprint: AdaptiveHashV1 | None
    terminal_result_as_of: datetime | str | None
    terminal_status: PersonalExperimentResultStatusV1 | str | None
    experiment_policy_fingerprint: AdaptiveHashV1 | None
    reassessment_id: UUID | str | None
    reassessment_fingerprint: AdaptiveHashV1 | None
    reassessment_evaluation_policy_fingerprint: AdaptiveHashV1 | None
    disposition: PersonalExperimentDispositionV1 | str | None
    reassessment_reviewed_at: datetime | str | None

    def __post_init__(self) -> None:
        readiness = _enum(AdaptiveSourceReadinessV1, self.readiness, "Stage14 readiness")
        object.__setattr__(self, "readiness", readiness)
        object.__setattr__(
            self, "goal_source_uuid", _uuid(self.goal_source_uuid, "Stage14 Goal UUID")
        )
        object.__setattr__(
            self,
            "goal_identity_fingerprint",
            validate_adaptive_hash(self.goal_identity_fingerprint),
        )
        for name in (
            "experiment_definition_fingerprint",
            "terminal_result_fingerprint",
            "experiment_policy_fingerprint",
            "reassessment_fingerprint",
            "reassessment_evaluation_policy_fingerprint",
        ):
            object.__setattr__(self, name, _hash_or_none(getattr(self, name), f"Stage14 {name}"))
        for name in ("experiment_definition_id", "reassessment_id"):
            value = getattr(self, name)
            if value is not None:
                object.__setattr__(self, name, _uuid(value, f"Stage14 {name}"))
        if self.terminal_result_as_of is not None:
            object.__setattr__(
                self,
                "terminal_result_as_of",
                _timestamp(self.terminal_result_as_of, "Stage14 result as_of"),
            )
        if self.reassessment_reviewed_at is not None:
            object.__setattr__(
                self,
                "reassessment_reviewed_at",
                _timestamp(self.reassessment_reviewed_at, "Stage14 reviewed_at"),
            )
        if self.terminal_status is not None:
            object.__setattr__(
                self,
                "terminal_status",
                _enum(
                    PersonalExperimentResultStatusV1,
                    self.terminal_status,
                    "Stage14 terminal status",
                ),
            )
        if self.disposition is not None:
            object.__setattr__(
                self,
                "disposition",
                _enum(PersonalExperimentDispositionV1, self.disposition, "Stage14 disposition"),
            )
        if readiness is AdaptiveSourceReadinessV1.EXACT_CURRENT and (
            self.experiment_definition_id is None
            or self.experiment_definition_fingerprint is None
            or self.terminal_result_fingerprint is None
            or self.terminal_result_as_of is None
            or self.terminal_status is None
            or self.experiment_policy_fingerprint != PERSONAL_EXPERIMENT_POLICY_FINGERPRINT
            or self.reassessment_id is None
            or self.reassessment_fingerprint is None
            or self.reassessment_evaluation_policy_fingerprint is None
            or self.disposition is None
            or self.reassessment_reviewed_at is None
        ):
            raise AdaptiveCognitiveTwinInputError("exact Stage14 snapshot is incomplete")

    def as_dict(self) -> dict[str, object]:
        return {
            "readiness": cast(AdaptiveSourceReadinessV1, self.readiness).value,
            "goal_source_uuid": str(self.goal_source_uuid),
            "goal_identity_fingerprint": self.goal_identity_fingerprint,
            "experiment_definition_id": str(self.experiment_definition_id)
            if self.experiment_definition_id is not None
            else None,
            "experiment_definition_fingerprint": self.experiment_definition_fingerprint,
            "terminal_result_fingerprint": self.terminal_result_fingerprint,
            "terminal_result_as_of": (
                _format_timestamp(cast(datetime, self.terminal_result_as_of))
                if self.terminal_result_as_of is not None
                else None
            ),
            "terminal_status": (
                cast(PersonalExperimentResultStatusV1, self.terminal_status).value
                if self.terminal_status is not None
                else None
            ),
            "experiment_policy_fingerprint": self.experiment_policy_fingerprint,
            "reassessment_id": str(self.reassessment_id)
            if self.reassessment_id is not None
            else None,
            "reassessment_fingerprint": self.reassessment_fingerprint,
            "reassessment_evaluation_policy_fingerprint": (
                self.reassessment_evaluation_policy_fingerprint
            ),
            "disposition": (
                cast(PersonalExperimentDispositionV1, self.disposition).value
                if self.disposition is not None
                else None
            ),
            "reassessment_reviewed_at": (
                _format_timestamp(cast(datetime, self.reassessment_reviewed_at))
                if self.reassessment_reviewed_at is not None
                else None
            ),
        }


@dataclass(frozen=True, slots=True)
class AdaptiveSourceSnapshotV1:
    """Bounded immutable tuple of exact Stage9/10/12/14 source references."""

    contract_version: str
    snapshot_version: str
    goal: GoalSourceSnapshotV1
    stage9_calibration: Stage9CalibrationSnapshotV1
    stage10_behavioral: Stage10BehavioralSnapshotV1
    stage12_progress: Stage12ProgressSnapshotV1
    stage14_experiment: Stage14ExperimentSnapshotV1
    as_of: datetime | str
    source_snapshot_fingerprint: AdaptiveHashV1 = ""

    def __post_init__(self) -> None:
        if (
            self.contract_version != ADAPTIVE_CONTRACT_VERSION
            or type(self.contract_version) is not str
        ):
            raise AdaptiveCognitiveTwinInputError("adaptive contract version is invalid")
        if (
            self.snapshot_version != ADAPTIVE_SNAPSHOT_VERSION
            or type(self.snapshot_version) is not str
        ):
            raise AdaptiveCognitiveTwinInputError("adaptive snapshot version is invalid")
        for name, expected_type in (
            ("goal", GoalSourceSnapshotV1),
            ("stage9_calibration", Stage9CalibrationSnapshotV1),
            ("stage10_behavioral", Stage10BehavioralSnapshotV1),
            ("stage12_progress", Stage12ProgressSnapshotV1),
            ("stage14_experiment", Stage14ExperimentSnapshotV1),
        ):
            if type(getattr(self, name)) is not expected_type:
                raise AdaptiveCognitiveTwinInputError(f"adaptive {name} is invalid")
        object.__setattr__(self, "as_of", _timestamp(self.as_of, "adaptive snapshot as_of"))
        goal_uuid = self.goal.goal_source_uuid
        goal_fp = self.goal.goal_identity_fingerprint
        if goal_fp is None:
            raise AdaptiveCognitiveTwinInputError("adaptive Goal identity is missing")
        for source in (self.stage10_behavioral, self.stage12_progress, self.stage14_experiment):
            if source.goal_source_uuid != goal_uuid or source.goal_identity_fingerprint != goal_fp:
                raise AdaptiveCognitiveTwinInputError(
                    "adaptive source Goal identity does not match"
                )
        fingerprint = adaptive_hash_json(self.fingerprint_payload())
        if self.source_snapshot_fingerprint:
            validate_adaptive_hash(self.source_snapshot_fingerprint)
            if self.source_snapshot_fingerprint != fingerprint:
                raise AdaptiveCognitiveTwinInputError("adaptive source fingerprint mismatch")
        else:
            object.__setattr__(self, "source_snapshot_fingerprint", fingerprint)
        if len(canonical_adaptive_json_bytes(self.as_dict())) > MAX_ADAPTIVE_SOURCE_PACK_BYTES:
            raise AdaptiveCognitiveTwinInputError("adaptive source pack is too large")

    def fingerprint_payload(self) -> dict[str, object]:
        return {
            "contract_version": self.contract_version,
            "snapshot_version": self.snapshot_version,
            "goal": self.goal.as_dict(),
            "stage9_calibration": self.stage9_calibration.as_dict(),
            "stage10_behavioral": self.stage10_behavioral.as_dict(),
            "stage12_progress": self.stage12_progress.as_dict(),
            "stage14_experiment": self.stage14_experiment.as_dict(),
            "as_of": _format_timestamp(cast(datetime, self.as_of)),
        }

    def as_dict(self) -> dict[str, object]:
        return {
            **self.fingerprint_payload(),
            "source_snapshot_fingerprint": self.source_snapshot_fingerprint,
        }

    def to_json(self) -> str:
        return _canonical_json(self.as_dict())


def validate_adaptive_source_snapshot(value: object) -> AdaptiveSourceSnapshotV1:
    """Validate one complete immutable source snapshot and its byte bound."""

    if type(value) is not AdaptiveSourceSnapshotV1:
        raise AdaptiveCognitiveTwinInputError("adaptive source snapshot type is invalid")
    validate_adaptive_policy()
    try:
        encoded = value.to_json().encode("utf-8")
    except TypeError, ValueError, UnicodeError, OverflowError:
        raise AdaptiveCognitiveTwinInputError("adaptive source snapshot is invalid") from None
    if len(encoded) > MAX_ADAPTIVE_SOURCE_PACK_BYTES:
        raise AdaptiveCognitiveTwinInputError("adaptive source pack is too large")
    return value


def _goal_fingerprint(goal: GrowthGoalIdentityV1) -> AdaptiveHashV1:
    return growth_hash_json(goal.as_dict())


def _missing_stage9(as_of: datetime | str) -> Stage9CalibrationSnapshotV1:
    return Stage9CalibrationSnapshotV1(
        readiness=AdaptiveSourceReadinessV1.SOURCE_MISSING,
        generation_id=None,
        as_of=as_of,
        result_fingerprint=None,
        policy_id=None,
        policy_fingerprint=None,
        audited_operations=None,
        linked_actual_decisions=None,
        evaluated_predictions=None,
    )


def load_stage9_calibration_snapshot(
    result: ProspectiveCalibrationResultV1 | None,
    *,
    generation_id: UUID | str | None,
    as_of: datetime | str,
) -> Stage9CalibrationSnapshotV1:
    """Load a verified Stage9 result without copying its raw event stream."""

    if result is None:
        return _missing_stage9(as_of)
    if generation_id is None:
        raise AdaptiveCognitiveTwinInputError("Stage9 generation is required")
    if type(result) is not ProspectiveCalibrationResultV1:
        raise AdaptiveCognitiveTwinInputError("Stage9 result type is invalid")
    try:
        validate_prospective_calibration_result(result)
        encoded = serialize_prospective_calibration_result(result)
    except Exception as exc:
        raise AdaptiveCognitiveTwinInputError("Stage9 result is invalid") from exc
    metrics = result.metrics
    return Stage9CalibrationSnapshotV1(
        readiness=AdaptiveSourceReadinessV1.EXACT_CURRENT,
        generation_id=generation_id,
        as_of=as_of,
        result_fingerprint=adaptive_hash_bytes(encoded),
        policy_id=result.policy_id,
        policy_fingerprint=result.policy_fingerprint,
        audited_operations=metrics.audited_operations,
        linked_actual_decisions=metrics.linked_actual_decisions,
        evaluated_predictions=metrics.exact_option_matches + metrics.mismatches,
    )


def _not_comparable_stage10(goal: GrowthGoalIdentityV1) -> Stage10BehavioralSnapshotV1:
    return Stage10BehavioralSnapshotV1(
        readiness=AdaptiveSourceReadinessV1.NOT_COMPARABLE,
        goal_source_uuid=goal.source_note_uuid,
        goal_identity_fingerprint=_goal_fingerprint(goal),
        growth_policy_fingerprint=GROWTH_POLICY_FINGERPRINT,
        behavioral_policy_fingerprint=BEHAVIORAL_POLICY_FINGERPRINT,
        cohort_fingerprint=None,
        pattern_fingerprint=None,
        behavioral_source_fingerprint=None,
        behavioral_provenance_fingerprint=None,
        pattern_type=None,
        pattern_state=None,
        mapping_id=None,
        mapping_fingerprint=None,
        mapping_policy_fingerprint=None,
        relation_state=GrowthRelationStateV1.NOT_COMPARABLE,
    )


def _relation_from_growth_source(
    source: GrowthGoalRelationResultV1 | GrowthEngineResultV1,
    goal: GrowthGoalIdentityV1,
) -> GrowthGoalRelationResultV1 | None:
    if type(source) is GrowthGoalRelationResultV1:
        return source
    if type(source) is not GrowthEngineResultV1:
        raise AdaptiveCognitiveTwinInputError("Stage10 source type is invalid")
    if source.selected_goal_source_uuid != goal.source_note_uuid:
        raise AdaptiveCognitiveTwinInputError("Stage10 source Goal identity conflicts")
    matching = tuple(
        item
        for item in source.goal_results
        if item.goal is None or item.goal.source_note_uuid == goal.source_note_uuid
    )
    if len(matching) != 1:
        return None
    return matching[0]


def load_stage10_behavioral_snapshot(
    source: GrowthGoalRelationResultV1 | GrowthEngineResultV1 | None,
    *,
    goal: GrowthGoalIdentityV1,
) -> Stage10BehavioralSnapshotV1:
    """Load one exact current Growth relation for the selected Goal."""

    if type(goal) is not GrowthGoalIdentityV1:
        raise AdaptiveCognitiveTwinInputError("Stage10 Goal type is invalid")
    if source is None:
        return Stage10BehavioralSnapshotV1(
            readiness=AdaptiveSourceReadinessV1.SOURCE_MISSING,
            goal_source_uuid=goal.source_note_uuid,
            goal_identity_fingerprint=_goal_fingerprint(goal),
            growth_policy_fingerprint=GROWTH_POLICY_FINGERPRINT,
            behavioral_policy_fingerprint=BEHAVIORAL_POLICY_FINGERPRINT,
            cohort_fingerprint=None,
            pattern_fingerprint=None,
            behavioral_source_fingerprint=None,
            behavioral_provenance_fingerprint=None,
            pattern_type=None,
            pattern_state=None,
            mapping_id=None,
            mapping_fingerprint=None,
            mapping_policy_fingerprint=None,
            relation_state=GrowthRelationStateV1.GOAL_SOURCE_MISSING,
        )
    relation = _relation_from_growth_source(source, goal)
    if relation is None:
        return _not_comparable_stage10(goal)
    goal_fp = _goal_fingerprint(goal)
    if relation.goal is None:
        if relation.state in {
            GrowthRelationStateV1.SUPPORTS_GOAL,
            GrowthRelationStateV1.CONFLICTS_WITH_GOAL,
            GrowthRelationStateV1.NEUTRAL_OR_UNKNOWN,
        }:
            return _not_comparable_stage10(goal)
        readiness = (
            AdaptiveSourceReadinessV1.SOURCE_MISSING
            if relation.state is GrowthRelationStateV1.GOAL_SOURCE_MISSING
            else AdaptiveSourceReadinessV1.NOT_COMPARABLE
        )
        return Stage10BehavioralSnapshotV1(
            readiness=readiness,
            goal_source_uuid=goal.source_note_uuid,
            goal_identity_fingerprint=goal_fp,
            growth_policy_fingerprint=GROWTH_POLICY_FINGERPRINT,
            behavioral_policy_fingerprint=BEHAVIORAL_POLICY_FINGERPRINT,
            cohort_fingerprint=None,
            pattern_fingerprint=None,
            behavioral_source_fingerprint=None,
            behavioral_provenance_fingerprint=None,
            pattern_type=None,
            pattern_state=None,
            mapping_id=None,
            mapping_fingerprint=None,
            mapping_policy_fingerprint=None,
            relation_state=relation.state,
        )
    if relation.goal != goal or _goal_fingerprint(relation.goal) != goal_fp:
        raise AdaptiveCognitiveTwinInputError("Stage10 relation Goal identity conflicts")
    if relation.state is GrowthRelationStateV1.GOAL_MAPPING_MISSING:
        return Stage10BehavioralSnapshotV1(
            readiness=AdaptiveSourceReadinessV1.SOURCE_MISSING,
            goal_source_uuid=goal.source_note_uuid,
            goal_identity_fingerprint=goal_fp,
            growth_policy_fingerprint=GROWTH_POLICY_FINGERPRINT,
            behavioral_policy_fingerprint=BEHAVIORAL_POLICY_FINGERPRINT,
            cohort_fingerprint=relation.cohort_fingerprint,
            pattern_fingerprint=None,
            behavioral_source_fingerprint=None,
            behavioral_provenance_fingerprint=None,
            pattern_type=None,
            pattern_state=None,
            mapping_id=None,
            mapping_fingerprint=None,
            mapping_policy_fingerprint=None,
            relation_state=relation.state,
        )
    pattern = relation.behavioral_pattern
    mapping = relation.mapping
    binary = relation.state in {
        GrowthRelationStateV1.SUPPORTS_GOAL,
        GrowthRelationStateV1.CONFLICTS_WITH_GOAL,
        GrowthRelationStateV1.NEUTRAL_OR_UNKNOWN,
    }
    if binary and (pattern is None or mapping is None):
        return _not_comparable_stage10(goal)
    return Stage10BehavioralSnapshotV1(
        readiness=AdaptiveSourceReadinessV1.EXACT_CURRENT,
        goal_source_uuid=goal.source_note_uuid,
        goal_identity_fingerprint=goal_fp,
        growth_policy_fingerprint=GROWTH_POLICY_FINGERPRINT,
        behavioral_policy_fingerprint=BEHAVIORAL_POLICY_FINGERPRINT,
        cohort_fingerprint=relation.cohort_fingerprint,
        pattern_fingerprint=pattern.reference_fingerprint if pattern is not None else None,
        behavioral_source_fingerprint=pattern.reference_fingerprint
        if pattern is not None
        else None,
        behavioral_provenance_fingerprint=pattern.provenance_fingerprint
        if pattern is not None
        else None,
        pattern_type=pattern.pattern_type if pattern is not None else None,
        pattern_state=pattern.pattern_state if pattern is not None else None,
        mapping_id=mapping.mapping_id if mapping is not None else None,
        mapping_fingerprint=mapping.mapping_fingerprint if mapping is not None else None,
        mapping_policy_fingerprint=(
            GROWTH_MAPPING_POLICY_FINGERPRINT if mapping is not None else None
        ),
        relation_state=relation.state,
    )


def _missing_stage12(
    goal: GrowthGoalIdentityV1,
    as_of: datetime | str,
) -> Stage12ProgressSnapshotV1:
    return Stage12ProgressSnapshotV1(
        readiness=AdaptiveSourceReadinessV1.SOURCE_MISSING,
        goal_source_uuid=goal.source_note_uuid,
        goal_identity_fingerprint=_goal_fingerprint(goal),
        progress_result_fingerprint=None,
        progress_policy_fingerprint=None,
        progress_as_of=as_of,
        definition_id=None,
        definition_fingerprint=None,
        status=None,
    )


def load_stage12_progress_snapshot(
    result: GoalProgressResultV1 | None,
    *,
    goal: GrowthGoalIdentityV1,
    as_of: datetime | str,
) -> Stage12ProgressSnapshotV1:
    """Load exact Goal Progress refs; Stage12 remains math authority."""

    if type(goal) is not GrowthGoalIdentityV1:
        raise AdaptiveCognitiveTwinInputError("Stage12 Goal type is invalid")
    if result is None:
        return _missing_stage12(goal, as_of)
    if type(result) is not GoalProgressResultV1:
        raise AdaptiveCognitiveTwinInputError("Stage12 result type is invalid")
    try:
        validate_goal_progress_result(result)
        result_bytes = result.to_json().encode("utf-8")
    except Exception as exc:
        raise AdaptiveCognitiveTwinInputError("Stage12 result is invalid") from exc
    goal_fp = _goal_fingerprint(goal)
    if result.selected_goal_source_uuid != goal.source_note_uuid:
        raise AdaptiveCognitiveTwinInputError("Stage12 result Goal identity conflicts")
    if (
        result.current_goal_identity_fingerprint is not None
        and result.current_goal_identity_fingerprint != goal_fp
    ):
        raise AdaptiveCognitiveTwinInputError("Stage12 result Goal fingerprint conflicts")
    if result.current_goal_identity_fingerprint is None:
        readiness = AdaptiveSourceReadinessV1.NOT_COMPARABLE
    else:
        readiness = (
            AdaptiveSourceReadinessV1.SOURCE_CHANGED
            if result.status is GoalProgressStatusV1.GOAL_SOURCE_CHANGED
            else AdaptiveSourceReadinessV1.NOT_COMPARABLE
            if result.status is GoalProgressStatusV1.NOT_COMPARABLE
            else AdaptiveSourceReadinessV1.EXACT_CURRENT
        )
    return Stage12ProgressSnapshotV1(
        readiness=readiness,
        goal_source_uuid=goal.source_note_uuid,
        goal_identity_fingerprint=goal_fp,
        progress_result_fingerprint=adaptive_hash_bytes(result_bytes),
        progress_policy_fingerprint=result.goal_progress_policy_fingerprint,
        progress_as_of=result.as_of,
        definition_id=result.active_definition_uuid,
        definition_fingerprint=result.definition_fingerprint,
        status=result.status,
    )


def _missing_stage14(
    goal: GrowthGoalIdentityV1,
    as_of: object,
) -> Stage14ExperimentSnapshotV1:
    return Stage14ExperimentSnapshotV1(
        readiness=AdaptiveSourceReadinessV1.SOURCE_MISSING,
        goal_source_uuid=goal.source_note_uuid,
        goal_identity_fingerprint=_goal_fingerprint(goal),
        experiment_definition_id=None,
        experiment_definition_fingerprint=None,
        terminal_result_fingerprint=None,
        terminal_result_as_of=None,
        terminal_status=None,
        experiment_policy_fingerprint=None,
        reassessment_id=None,
        reassessment_fingerprint=None,
        reassessment_evaluation_policy_fingerprint=None,
        disposition=None,
        reassessment_reviewed_at=None,
    )


def load_stage14_experiment_snapshot(
    selector: Stage14ExperimentSelectorV1 | None,
    *,
    result: PersonalExperimentEvaluationResultV1 | None,
    reassessment: PersonalExperimentReassessmentRecordV1 | None,
    goal: GrowthGoalIdentityV1,
    as_of: datetime | str,
) -> Stage14ExperimentSnapshotV1:
    """Load one explicitly selected terminal Stage14 result and review."""

    if type(goal) is not GrowthGoalIdentityV1:
        raise AdaptiveCognitiveTwinInputError("Stage14 Goal type is invalid")
    if selector is None or result is None or reassessment is None:
        return _missing_stage14(goal, as_of)
    if type(selector) is not Stage14ExperimentSelectorV1:
        raise AdaptiveCognitiveTwinInputError("Stage14 selector type is invalid")
    if type(result) is not PersonalExperimentEvaluationResultV1:
        raise AdaptiveCognitiveTwinInputError("Stage14 result type is invalid")
    if type(reassessment) is not PersonalExperimentReassessmentRecordV1:
        raise AdaptiveCognitiveTwinInputError("Stage14 reassessment type is invalid")
    try:
        validate_personal_experiment_result(result)
    except Exception as exc:
        raise AdaptiveCognitiveTwinInputError("Stage14 result is invalid") from exc
    result_goal_uuid = result.goal_source_uuid
    result_goal_fp = result.goal_identity_fingerprint
    if result_goal_uuid is None or result_goal_fp is None:
        return _missing_stage14(goal, as_of)
    if (
        result_goal_uuid != goal.source_note_uuid
        or result_goal_fp != _goal_fingerprint(goal)
        or reassessment.goal_source_uuid != goal.source_note_uuid
        or reassessment.goal_identity_fingerprint != _goal_fingerprint(goal)
    ):
        raise AdaptiveCognitiveTwinInputError("Stage14 source Goal identity conflicts")
    if (
        result.experiment_definition_id != selector.experiment_definition_id
        or result.experiment_definition_fingerprint != selector.experiment_definition_fingerprint
        or reassessment.experiment_definition_id != selector.experiment_definition_id
        or reassessment.experiment_definition_fingerprint
        != selector.experiment_definition_fingerprint
    ):
        raise AdaptiveCognitiveTwinInputError("Stage14 experiment selector conflicts")
    if reassessment.result_fingerprint != result.result_fingerprint:
        raise AdaptiveCognitiveTwinInputError("Stage14 reassessment result conflicts")
    if _timestamp(reassessment.evaluation_as_of, "Stage14 evaluation cutoff") != _timestamp(
        result.as_of, "Stage14 result as_of"
    ):
        raise AdaptiveCognitiveTwinInputError("Stage14 evaluation cutoff conflicts")
    if result.lifecycle_state not in {"completed", "cancelled"}:
        return _missing_stage14(goal, as_of)
    if result.terminal_record_id is None or result.terminal_at is None:
        return _missing_stage14(goal, as_of)
    readiness = (
        AdaptiveSourceReadinessV1.SOURCE_CHANGED
        if result.status is PersonalExperimentResultStatusV1.SOURCE_CHANGED
        else AdaptiveSourceReadinessV1.NOT_COMPARABLE
        if result.status is PersonalExperimentResultStatusV1.NOT_COMPARABLE
        else AdaptiveSourceReadinessV1.EXACT_CURRENT
    )
    return Stage14ExperimentSnapshotV1(
        readiness=readiness,
        goal_source_uuid=goal.source_note_uuid,
        goal_identity_fingerprint=_goal_fingerprint(goal),
        experiment_definition_id=selector.experiment_definition_id,
        experiment_definition_fingerprint=selector.experiment_definition_fingerprint,
        terminal_result_fingerprint=result.result_fingerprint,
        terminal_result_as_of=result.as_of,
        terminal_status=result.status,
        experiment_policy_fingerprint=result.experiment_policy_fingerprint,
        reassessment_id=reassessment.id,
        reassessment_fingerprint=reassessment.reassessment_fingerprint,
        reassessment_evaluation_policy_fingerprint=reassessment.evaluation_policy_fingerprint,
        disposition=reassessment.disposition,
        reassessment_reviewed_at=reassessment.reassessment_reviewed_at,
    )


def load_adaptive_source_snapshot(
    *,
    goal: GrowthGoalIdentityV1,
    stage9_result: ProspectiveCalibrationResultV1 | None,
    stage9_generation_id: UUID | str | None,
    stage9_as_of: datetime | str | None = None,
    stage10_source: GrowthGoalRelationResultV1 | GrowthEngineResultV1 | None,
    stage12_result: GoalProgressResultV1 | None,
    stage14_selector: Stage14ExperimentSelectorV1 | None,
    stage14_result: PersonalExperimentEvaluationResultV1 | None,
    stage14_reassessment: PersonalExperimentReassessmentRecordV1 | None,
    as_of: datetime | str,
) -> AdaptiveSourceSnapshotV1:
    """Build the complete immutable Stage15 source pack from typed inputs."""

    if type(goal) is not GrowthGoalIdentityV1:
        raise AdaptiveCognitiveTwinInputError("adaptive Goal type is invalid")
    goal_fp = _goal_fingerprint(goal)
    goal_snapshot = GoalSourceSnapshotV1(
        readiness=AdaptiveSourceReadinessV1.EXACT_CURRENT,
        goal_source_uuid=goal.source_note_uuid,
        goal_identity_fingerprint=goal_fp,
        growth_policy_fingerprint=GROWTH_POLICY_FINGERPRINT,
        source_fingerprint=goal.source_fingerprint,
    )
    snapshot_time = _timestamp(as_of, "adaptive snapshot as_of")
    stage9 = load_stage9_calibration_snapshot(
        stage9_result,
        generation_id=stage9_generation_id,
        as_of=stage9_as_of if stage9_as_of is not None else snapshot_time,
    )
    stage10 = load_stage10_behavioral_snapshot(stage10_source, goal=goal)
    stage12 = load_stage12_progress_snapshot(stage12_result, goal=goal, as_of=snapshot_time)
    stage14 = load_stage14_experiment_snapshot(
        stage14_selector,
        result=stage14_result,
        reassessment=stage14_reassessment,
        goal=goal,
        as_of=snapshot_time,
    )
    return validate_adaptive_source_snapshot(
        AdaptiveSourceSnapshotV1(
            contract_version=ADAPTIVE_CONTRACT_VERSION,
            snapshot_version=ADAPTIVE_SNAPSHOT_VERSION,
            goal=goal_snapshot,
            stage9_calibration=stage9,
            stage10_behavioral=stage10,
            stage12_progress=stage12,
            stage14_experiment=stage14,
            as_of=snapshot_time,
        )
    )


@dataclass(frozen=True, slots=True)
class AdaptiveSufficiencyDecisionV1:
    """Result of the source-first sufficiency gate."""

    state: AdaptiveSufficiencyStateV1 | str
    reasons: tuple[AdaptiveCandidateReasonV1 | str, ...]
    source_snapshot_fingerprint: AdaptiveHashV1

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "state", _enum(AdaptiveSufficiencyStateV1, self.state, "sufficiency state")
        )
        normalized = _tuple_enums(
            self.reasons,
            AdaptiveCandidateReasonV1,
            "sufficiency reasons",
            MAX_ADAPTIVE_REASONS,
        )
        object.__setattr__(self, "reasons", normalized)
        object.__setattr__(
            self,
            "source_snapshot_fingerprint",
            validate_adaptive_hash(self.source_snapshot_fingerprint),
        )

    def as_dict(self) -> dict[str, object]:
        return {
            "state": cast(AdaptiveSufficiencyStateV1, self.state).value,
            "reasons": [cast(AdaptiveCandidateReasonV1, item).value for item in self.reasons],
            "source_snapshot_fingerprint": self.source_snapshot_fingerprint,
        }


def _source_readinesses(
    snapshot: AdaptiveSourceSnapshotV1,
) -> tuple[AdaptiveSourceReadinessV1, ...]:
    return tuple(
        cast(AdaptiveSourceReadinessV1, source.readiness)
        for source in (
            snapshot.goal,
            snapshot.stage9_calibration,
            snapshot.stage10_behavioral,
            snapshot.stage12_progress,
            snapshot.stage14_experiment,
        )
    )


def evaluate_adaptive_sufficiency(
    snapshot: AdaptiveSourceSnapshotV1,
    *,
    safe_delta: bool = False,
) -> AdaptiveSufficiencyDecisionV1:
    """Apply the normative precedence before any candidate rule runs."""

    validate_adaptive_source_snapshot(snapshot)
    readinesses = _source_readinesses(snapshot)
    reasons: tuple[AdaptiveCandidateReasonV1, ...]
    if AdaptiveSourceReadinessV1.UNAVAILABLE in readinesses:
        state = AdaptiveSufficiencyStateV1.INSUFFICIENT
        reasons = (AdaptiveCandidateReasonV1.MISSING_EXACT_SOURCE,)
    elif AdaptiveSourceReadinessV1.POLICY_MISMATCH in readinesses:
        state = AdaptiveSufficiencyStateV1.POLICY_MISMATCH
        reasons = (AdaptiveCandidateReasonV1.POLICY_MISMATCH,)
    elif AdaptiveSourceReadinessV1.NOT_COMPARABLE in readinesses:
        state = AdaptiveSufficiencyStateV1.NOT_COMPARABLE
        if snapshot.stage10_behavioral.readiness is AdaptiveSourceReadinessV1.NOT_COMPARABLE:
            reasons = (AdaptiveCandidateReasonV1.STAGE10_STATE_NOT_COMPARABLE,)
        elif snapshot.stage12_progress.readiness is AdaptiveSourceReadinessV1.NOT_COMPARABLE:
            reasons = (AdaptiveCandidateReasonV1.STAGE12_STATE_NOT_COMPARABLE,)
        else:
            reasons = (AdaptiveCandidateReasonV1.STAGE14_RESULT_NOT_COMPARABLE,)
    elif any(
        item in {AdaptiveSourceReadinessV1.SOURCE_CHANGED, AdaptiveSourceReadinessV1.STALE}
        for item in readinesses
    ):
        state = AdaptiveSufficiencyStateV1.SOURCE_CHANGED
        reasons = (AdaptiveCandidateReasonV1.SOURCE_DRIFT,)
    elif (
        AdaptiveSourceReadinessV1.SOURCE_MISSING in readinesses
        or snapshot.stage12_progress.status
        in {
            GoalProgressStatusV1.INSUFFICIENT_OBSERVATIONS,
            GoalProgressStatusV1.DEFINITION_MISSING,
        }
        or snapshot.stage10_behavioral.relation_state
        in {
            GrowthRelationStateV1.BEHAVIORAL_EVIDENCE_INSUFFICIENT,
            GrowthRelationStateV1.GOAL_SOURCE_MISSING,
        }
    ):
        state = AdaptiveSufficiencyStateV1.INSUFFICIENT
        reasons = (AdaptiveCandidateReasonV1.MISSING_EXACT_SOURCE,)
    else:
        state = (
            AdaptiveSufficiencyStateV1.CANDIDATE if safe_delta else AdaptiveSufficiencyStateV1.HOLD
        )
        reasons = () if safe_delta else (AdaptiveCandidateReasonV1.NO_SAFE_DELTA,)
    return AdaptiveSufficiencyDecisionV1(
        state=state,
        reasons=reasons,
        source_snapshot_fingerprint=snapshot.source_snapshot_fingerprint,
    )


@dataclass(frozen=True, slots=True)
class Stage15EvaluationPlanV1:
    """Fixed plan for a later explicit descriptive comparison."""

    later_source_required: bool
    explicit_as_of_required: bool
    baseline_source_snapshot_fingerprint: AdaptiveHashV1
    measures: tuple[Stage15MeasureV1 | str, ...]
    evaluation_policy_id: str
    evaluation_policy_fingerprint: AdaptiveHashV1
    non_causal_wording_id: str

    def __post_init__(self) -> None:
        if (
            type(self.later_source_required) is not bool
            or type(self.explicit_as_of_required) is not bool
        ):
            raise AdaptiveCognitiveTwinInputError("evaluation plan requirement is invalid")
        if not self.later_source_required or not self.explicit_as_of_required:
            raise AdaptiveCognitiveTwinInputError(
                "evaluation plan must require later source and as_of"
            )
        object.__setattr__(
            self,
            "baseline_source_snapshot_fingerprint",
            validate_adaptive_hash(self.baseline_source_snapshot_fingerprint),
        )
        measures = _tuple_enums(
            self.measures,
            Stage15MeasureV1,
            "evaluation measures",
            len(Stage15MeasureV1),
        )
        if not measures:
            raise AdaptiveCognitiveTwinInputError("evaluation measures are empty")
        object.__setattr__(self, "measures", measures)
        if (
            self.evaluation_policy_id != ADAPTIVE_EVALUATION_POLICY_ID
            or type(self.evaluation_policy_id) is not str
        ):
            raise AdaptiveCognitiveTwinInputError("evaluation policy id is invalid")
        if self.evaluation_policy_fingerprint != ADAPTIVE_POLICY_FINGERPRINT:
            raise AdaptiveCognitiveTwinInputError("evaluation policy fingerprint is invalid")
        if (
            self.non_causal_wording_id != ADAPTIVE_NON_CAUSAL_LANGUAGE
            or type(self.non_causal_wording_id) is not str
        ):
            raise AdaptiveCognitiveTwinInputError("evaluation language is invalid")

    def as_dict(self) -> dict[str, object]:
        return {
            "later_source_required": self.later_source_required,
            "explicit_as_of_required": self.explicit_as_of_required,
            "baseline_source_snapshot_fingerprint": self.baseline_source_snapshot_fingerprint,
            "measures": [cast(Stage15MeasureV1, measure).value for measure in self.measures],
            "evaluation_policy_id": self.evaluation_policy_id,
            "evaluation_policy_fingerprint": self.evaluation_policy_fingerprint,
            "non_causal_wording_id": self.non_causal_wording_id,
        }

    @property
    def measure(self) -> Stage15MeasureV1:
        """Return the first closed measure for callers with a single focus."""

        return cast(Stage15MeasureV1, self.measures[0])

    @property
    def later_as_of_required(self) -> bool:
        """Compatibility alias for the normative explicit cutoff field."""

        return self.explicit_as_of_required

    @property
    def causality_language(self) -> str:
        """Compatibility alias for the normative wording identifier."""

        return self.non_causal_wording_id


def _profile_shape_payload(
    *,
    contract_version: str,
    profile_version: str,
    goal_source_uuid: UUID,
    goal_identity_fingerprint: AdaptiveHashV1,
    source_snapshot_fingerprint: AdaptiveHashV1,
    projection_focus: Stage15ProjectionFocusV1,
    interaction_mode: Stage15InteractionModeV1,
    evaluation_measure: Stage15MeasureV1,
    profile_policy_id: str,
    profile_policy_fingerprint: AdaptiveHashV1,
) -> dict[str, object]:
    return {
        "contract_version": contract_version,
        "profile_version": profile_version,
        "goal_source_uuid": str(goal_source_uuid),
        "goal_identity_fingerprint": goal_identity_fingerprint,
        "source_snapshot_fingerprint": source_snapshot_fingerprint,
        "projection_focus": projection_focus.value,
        "interaction_mode": interaction_mode.value,
        "evaluation_measure": evaluation_measure.value,
        "profile_policy_id": profile_policy_id,
        "profile_policy_fingerprint": profile_policy_fingerprint,
    }


@dataclass(frozen=True, slots=True)
class Stage15ProfileProposalV1:
    """Store-independent closed profile shape proposed by the candidate core."""

    contract_version: str
    profile_version: str
    goal_source_uuid: UUID | str
    goal_identity_fingerprint: AdaptiveHashV1
    source_snapshot_fingerprint: AdaptiveHashV1
    projection_focus: Stage15ProjectionFocusV1 | str
    interaction_mode: Stage15InteractionModeV1 | str
    evaluation_measure: Stage15MeasureV1 | str
    profile_policy_id: str
    profile_policy_fingerprint: AdaptiveHashV1
    profile_fingerprint: AdaptiveHashV1 = ""

    def __post_init__(self) -> None:
        if (
            self.contract_version != ADAPTIVE_CONTRACT_VERSION
            or type(self.contract_version) is not str
        ):
            raise AdaptiveCognitiveTwinInputError("profile contract version is invalid")
        if self.profile_version != "1" or type(self.profile_version) is not str:
            raise AdaptiveCognitiveTwinInputError("profile version is invalid")
        object.__setattr__(
            self, "goal_source_uuid", _uuid(self.goal_source_uuid, "profile Goal UUID")
        )
        object.__setattr__(
            self,
            "goal_identity_fingerprint",
            validate_adaptive_hash(self.goal_identity_fingerprint),
        )
        object.__setattr__(
            self,
            "source_snapshot_fingerprint",
            validate_adaptive_hash(self.source_snapshot_fingerprint),
        )
        object.__setattr__(
            self,
            "projection_focus",
            _enum(Stage15ProjectionFocusV1, self.projection_focus, "profile focus"),
        )
        object.__setattr__(
            self,
            "interaction_mode",
            _enum(Stage15InteractionModeV1, self.interaction_mode, "profile interaction mode"),
        )
        object.__setattr__(
            self,
            "evaluation_measure",
            _enum(Stage15MeasureV1, self.evaluation_measure, "profile measure"),
        )
        if (
            self.profile_policy_id != ADAPTIVE_PROFILE_POLICY_ID
            or type(self.profile_policy_id) is not str
        ):
            raise AdaptiveCognitiveTwinInputError("profile policy id is invalid")
        if self.profile_policy_fingerprint != ADAPTIVE_POLICY_FINGERPRINT:
            raise AdaptiveCognitiveTwinInputError("profile policy fingerprint is invalid")
        expected = adaptive_hash_json(self.fingerprint_payload())
        if self.profile_fingerprint:
            validate_adaptive_hash(self.profile_fingerprint)
            if self.profile_fingerprint != expected:
                raise AdaptiveCognitiveTwinInputError("profile proposal fingerprint mismatch")
        else:
            object.__setattr__(self, "profile_fingerprint", expected)
        if len(canonical_adaptive_json_bytes(self.as_dict())) > MAX_ADAPTIVE_PROFILE_BYTES:
            raise AdaptiveCognitiveTwinInputError("profile proposal is too large")

    def fingerprint_payload(self) -> dict[str, object]:
        return _profile_shape_payload(
            contract_version=self.contract_version,
            profile_version=self.profile_version,
            goal_source_uuid=cast(UUID, self.goal_source_uuid),
            goal_identity_fingerprint=self.goal_identity_fingerprint,
            source_snapshot_fingerprint=self.source_snapshot_fingerprint,
            projection_focus=cast(Stage15ProjectionFocusV1, self.projection_focus),
            interaction_mode=cast(Stage15InteractionModeV1, self.interaction_mode),
            evaluation_measure=cast(Stage15MeasureV1, self.evaluation_measure),
            profile_policy_id=self.profile_policy_id,
            profile_policy_fingerprint=self.profile_policy_fingerprint,
        )

    def as_dict(self) -> dict[str, object]:
        return {**self.fingerprint_payload(), "profile_fingerprint": self.profile_fingerprint}


@dataclass(frozen=True, slots=True)
class Stage15AdaptiveProfileV1:
    """Store-owned active profile with an exact identity."""

    profile_id: UUID | str
    contract_version: str
    profile_version: str
    goal_source_uuid: UUID | str
    goal_identity_fingerprint: AdaptiveHashV1
    source_snapshot_fingerprint: AdaptiveHashV1
    projection_focus: Stage15ProjectionFocusV1 | str
    interaction_mode: Stage15InteractionModeV1 | str
    evaluation_measure: Stage15MeasureV1 | str
    profile_policy_id: str
    profile_policy_fingerprint: AdaptiveHashV1
    profile_fingerprint: AdaptiveHashV1 = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "profile_id", _uuid(self.profile_id, "profile id"))
        proposal = Stage15ProfileProposalV1(
            contract_version=self.contract_version,
            profile_version=self.profile_version,
            goal_source_uuid=self.goal_source_uuid,
            goal_identity_fingerprint=self.goal_identity_fingerprint,
            source_snapshot_fingerprint=self.source_snapshot_fingerprint,
            projection_focus=self.projection_focus,
            interaction_mode=self.interaction_mode,
            evaluation_measure=self.evaluation_measure,
            profile_policy_id=self.profile_policy_id,
            profile_policy_fingerprint=self.profile_policy_fingerprint,
        )
        for name in (
            "contract_version",
            "profile_version",
            "goal_source_uuid",
            "goal_identity_fingerprint",
            "source_snapshot_fingerprint",
            "projection_focus",
            "interaction_mode",
            "evaluation_measure",
            "profile_policy_id",
            "profile_policy_fingerprint",
        ):
            object.__setattr__(self, name, getattr(proposal, name))
        expected = adaptive_hash_json(self.fingerprint_payload())
        if self.profile_fingerprint:
            validate_adaptive_hash(self.profile_fingerprint)
            if self.profile_fingerprint != expected:
                raise AdaptiveCognitiveTwinInputError("active profile fingerprint mismatch")
        else:
            object.__setattr__(self, "profile_fingerprint", expected)
        if len(canonical_adaptive_json_bytes(self.as_dict())) > MAX_ADAPTIVE_PROFILE_BYTES:
            raise AdaptiveCognitiveTwinInputError("active profile is too large")

    @classmethod
    def from_proposal(
        cls,
        proposal: Stage15ProfileProposalV1,
        *,
        profile_id: UUID | str,
    ) -> Stage15AdaptiveProfileV1:
        if type(proposal) is not Stage15ProfileProposalV1:
            raise AdaptiveCognitiveTwinInputError("profile proposal type is invalid")
        return cls(
            profile_id=profile_id,
            contract_version=proposal.contract_version,
            profile_version=proposal.profile_version,
            goal_source_uuid=proposal.goal_source_uuid,
            goal_identity_fingerprint=proposal.goal_identity_fingerprint,
            source_snapshot_fingerprint=proposal.source_snapshot_fingerprint,
            projection_focus=proposal.projection_focus,
            interaction_mode=proposal.interaction_mode,
            evaluation_measure=proposal.evaluation_measure,
            profile_policy_id=proposal.profile_policy_id,
            profile_policy_fingerprint=proposal.profile_policy_fingerprint,
        )

    def fingerprint_payload(self) -> dict[str, object]:
        return {
            "profile_id": str(self.profile_id),
            **_profile_shape_payload(
                contract_version=self.contract_version,
                profile_version=self.profile_version,
                goal_source_uuid=cast(UUID, self.goal_source_uuid),
                goal_identity_fingerprint=self.goal_identity_fingerprint,
                source_snapshot_fingerprint=self.source_snapshot_fingerprint,
                projection_focus=cast(Stage15ProjectionFocusV1, self.projection_focus),
                interaction_mode=cast(Stage15InteractionModeV1, self.interaction_mode),
                evaluation_measure=cast(Stage15MeasureV1, self.evaluation_measure),
                profile_policy_id=self.profile_policy_id,
                profile_policy_fingerprint=self.profile_policy_fingerprint,
            ),
        }

    @property
    def profile_shape_fingerprint(self) -> AdaptiveHashV1:
        return adaptive_hash_json(
            {key: value for key, value in self.fingerprint_payload().items() if key != "profile_id"}
        )

    def as_dict(self) -> dict[str, object]:
        return {**self.fingerprint_payload(), "profile_fingerprint": self.profile_fingerprint}


def build_adaptive_profile(
    proposal: Stage15ProfileProposalV1,
    *,
    profile_id: UUID | str,
) -> Stage15AdaptiveProfileV1:
    """Assign a store-owned UUID without changing the closed profile shape."""

    return Stage15AdaptiveProfileV1.from_proposal(proposal, profile_id=profile_id)


@dataclass(frozen=True, slots=True)
class Stage15CandidateV1:
    """Deterministic candidate; it is never active by itself."""

    contract_version: str
    candidate_version: str
    status: AdaptiveSufficiencyStateV1 | str
    as_of: datetime | str
    goal_source_uuid: UUID | str
    goal_identity_fingerprint: AdaptiveHashV1
    source_snapshot_fingerprint: AdaptiveHashV1
    prior_profile_id: UUID | str | None
    prior_profile_fingerprint: AdaptiveHashV1 | None
    proposed_profile: Stage15ProfileProposalV1 | None
    reasons: tuple[AdaptiveCandidateReasonV1 | str, ...]
    evaluation_plan: Stage15EvaluationPlanV1
    caveats: tuple[Stage15CaveatV1 | str, ...]
    policy_id: str
    policy_fingerprint: AdaptiveHashV1
    candidate_fingerprint: AdaptiveHashV1 = ""

    def __post_init__(self) -> None:
        if (
            self.contract_version != ADAPTIVE_CONTRACT_VERSION
            or type(self.contract_version) is not str
        ):
            raise AdaptiveCognitiveTwinInputError("candidate contract version is invalid")
        if self.candidate_version != "1" or type(self.candidate_version) is not str:
            raise AdaptiveCognitiveTwinInputError("candidate version is invalid")
        object.__setattr__(
            self, "status", _enum(AdaptiveSufficiencyStateV1, self.status, "candidate status")
        )
        object.__setattr__(self, "as_of", _timestamp(self.as_of, "candidate as_of"))
        object.__setattr__(
            self, "goal_source_uuid", _uuid(self.goal_source_uuid, "candidate Goal UUID")
        )
        object.__setattr__(
            self,
            "goal_identity_fingerprint",
            validate_adaptive_hash(self.goal_identity_fingerprint),
        )
        object.__setattr__(
            self,
            "source_snapshot_fingerprint",
            validate_adaptive_hash(self.source_snapshot_fingerprint),
        )
        if self.prior_profile_id is not None:
            object.__setattr__(
                self, "prior_profile_id", _uuid(self.prior_profile_id, "prior profile id")
            )
        object.__setattr__(
            self,
            "prior_profile_fingerprint",
            _hash_or_none(self.prior_profile_fingerprint, "prior profile fingerprint"),
        )
        if (
            self.proposed_profile is not None
            and type(self.proposed_profile) is not Stage15ProfileProposalV1
        ):
            raise AdaptiveCognitiveTwinInputError("candidate proposed profile is invalid")
        if type(self.evaluation_plan) is not Stage15EvaluationPlanV1:
            raise AdaptiveCognitiveTwinInputError("candidate evaluation plan is invalid")
        reasons = _tuple_enums(
            self.reasons, AdaptiveCandidateReasonV1, "candidate reasons", MAX_ADAPTIVE_REASONS
        )
        caveats = _tuple_enums(
            self.caveats, Stage15CaveatV1, "candidate caveats", MAX_ADAPTIVE_CAVEATS
        )
        object.__setattr__(self, "reasons", reasons)
        object.__setattr__(self, "caveats", caveats)
        if self.policy_id != ADAPTIVE_CANDIDATE_POLICY_ID or type(self.policy_id) is not str:
            raise AdaptiveCognitiveTwinInputError("candidate policy id is invalid")
        if self.policy_fingerprint != ADAPTIVE_POLICY_FINGERPRINT:
            raise AdaptiveCognitiveTwinInputError("candidate policy fingerprint is invalid")
        status = cast(AdaptiveSufficiencyStateV1, self.status)
        if status is AdaptiveSufficiencyStateV1.CANDIDATE:
            if self.proposed_profile is None:
                raise AdaptiveCognitiveTwinInputError("candidate status has no proposed profile")
            if (
                self.proposed_profile.goal_source_uuid != self.goal_source_uuid
                or self.proposed_profile.goal_identity_fingerprint != self.goal_identity_fingerprint
                or self.proposed_profile.source_snapshot_fingerprint
                != self.source_snapshot_fingerprint
            ):
                raise AdaptiveCognitiveTwinInputError("candidate profile binding is invalid")
        elif self.proposed_profile is not None:
            raise AdaptiveCognitiveTwinInputError("non-candidate status cannot propose a profile")
        expected = adaptive_hash_json(self.fingerprint_payload())
        if self.candidate_fingerprint:
            validate_adaptive_hash(self.candidate_fingerprint)
            if self.candidate_fingerprint != expected:
                raise AdaptiveCognitiveTwinInputError("candidate fingerprint mismatch")
        else:
            object.__setattr__(self, "candidate_fingerprint", expected)
        if len(canonical_adaptive_json_bytes(self.as_dict())) > MAX_ADAPTIVE_CANDIDATE_BYTES:
            raise AdaptiveCognitiveTwinInputError("candidate is too large")

    @property
    def candidate_status(self) -> AdaptiveSufficiencyStateV1:
        return cast(AdaptiveSufficiencyStateV1, self.status)

    def fingerprint_payload(self) -> dict[str, object]:
        return {
            "contract_version": self.contract_version,
            "candidate_version": self.candidate_version,
            "status": cast(AdaptiveSufficiencyStateV1, self.status).value,
            "as_of": _format_timestamp(cast(datetime, self.as_of)),
            "goal_source_uuid": str(self.goal_source_uuid),
            "goal_identity_fingerprint": self.goal_identity_fingerprint,
            "source_snapshot_fingerprint": self.source_snapshot_fingerprint,
            "prior_profile_id": str(self.prior_profile_id)
            if self.prior_profile_id is not None
            else None,
            "prior_profile_fingerprint": self.prior_profile_fingerprint,
            "proposed_profile": self.proposed_profile.as_dict()
            if self.proposed_profile is not None
            else None,
            "reasons": [cast(AdaptiveCandidateReasonV1, item).value for item in self.reasons],
            "evaluation_plan": self.evaluation_plan.as_dict(),
            "caveats": [cast(Stage15CaveatV1, item).value for item in self.caveats],
            "policy_id": self.policy_id,
            "policy_fingerprint": self.policy_fingerprint,
        }

    def as_dict(self) -> dict[str, object]:
        return {**self.fingerprint_payload(), "candidate_fingerprint": self.candidate_fingerprint}

    def to_json(self) -> str:
        return _canonical_json(self.as_dict())


def validate_adaptive_candidate(value: object) -> Stage15CandidateV1:
    """Validate one deterministic candidate and its fixed byte bound."""

    if type(value) is not Stage15CandidateV1:
        raise AdaptiveCognitiveTwinInputError("adaptive candidate type is invalid")
    validate_adaptive_policy()
    try:
        size = len(value.to_json().encode("utf-8"))
    except TypeError, ValueError, UnicodeError, OverflowError:
        raise AdaptiveCognitiveTwinInputError("adaptive candidate is invalid") from None
    if size > MAX_ADAPTIVE_CANDIDATE_BYTES:
        raise AdaptiveCognitiveTwinInputError("adaptive candidate is too large")
    return value


def _stage15_evaluation_plan(
    source_snapshot_fingerprint: AdaptiveHashV1,
    measure: Stage15MeasureV1,
) -> Stage15EvaluationPlanV1:
    return Stage15EvaluationPlanV1(
        later_source_required=True,
        explicit_as_of_required=True,
        baseline_source_snapshot_fingerprint=source_snapshot_fingerprint,
        measures=(measure,),
        evaluation_policy_id=ADAPTIVE_EVALUATION_POLICY_ID,
        evaluation_policy_fingerprint=ADAPTIVE_POLICY_FINGERPRINT,
        non_causal_wording_id=ADAPTIVE_NON_CAUSAL_LANGUAGE,
    )


def _profile_shape_for_focus(
    focus: Stage15ProjectionFocusV1,
) -> tuple[Stage15InteractionModeV1, Stage15MeasureV1]:
    if focus is Stage15ProjectionFocusV1.EXPERIMENT_CONTEXT:
        return Stage15InteractionModeV1.FOREGROUND_REVIEW, Stage15MeasureV1.EXPERIMENT_STATE
    if focus is Stage15ProjectionFocusV1.TRADEOFF_CONTEXT:
        return Stage15InteractionModeV1.EXPLICIT_TRADEOFF, Stage15MeasureV1.PROGRESS_STATE
    if focus is Stage15ProjectionFocusV1.BEHAVIORAL_CONTEXT:
        return Stage15InteractionModeV1.EVIDENCE_SEQUENCE, Stage15MeasureV1.BEHAVIORAL_STATE
    if focus is Stage15ProjectionFocusV1.CALIBRATION_CONTEXT:
        return Stage15InteractionModeV1.BALANCED_EVIDENCE, Stage15MeasureV1.CALIBRATION_LINKAGE
    return Stage15InteractionModeV1.BALANCED_EVIDENCE, Stage15MeasureV1.PROGRESS_STATE


def _build_candidate(
    snapshot: AdaptiveSourceSnapshotV1,
    *,
    as_of: datetime | str,
    status: AdaptiveSufficiencyStateV1,
    reasons: Sequence[AdaptiveCandidateReasonV1],
    prior_profile: Stage15AdaptiveProfileV1 | None,
    proposed_profile: Stage15ProfileProposalV1 | None,
    focus: Stage15ProjectionFocusV1,
) -> Stage15CandidateV1:
    _interaction_mode, measure = _profile_shape_for_focus(focus)
    return validate_adaptive_candidate(
        Stage15CandidateV1(
            contract_version=ADAPTIVE_CONTRACT_VERSION,
            candidate_version="1",
            status=status,
            as_of=as_of,
            goal_source_uuid=snapshot.goal.goal_source_uuid,
            goal_identity_fingerprint=cast(AdaptiveHashV1, snapshot.goal.goal_identity_fingerprint),
            source_snapshot_fingerprint=snapshot.source_snapshot_fingerprint,
            prior_profile_id=prior_profile.profile_id if prior_profile is not None else None,
            prior_profile_fingerprint=(
                prior_profile.profile_fingerprint if prior_profile is not None else None
            ),
            proposed_profile=proposed_profile,
            reasons=tuple(reasons),
            evaluation_plan=_stage15_evaluation_plan(
                snapshot.source_snapshot_fingerprint,
                measure,
            ),
            caveats=(
                Stage15CaveatV1.EXACT_SOURCE_SNAPSHOT,
                Stage15CaveatV1.OWNER_REVIEW_REQUIRED,
                Stage15CaveatV1.NO_AUTOMATIC_ACTIVATION,
                Stage15CaveatV1.DESCRIPTIVE_NON_CAUSAL,
            ),
            policy_id=ADAPTIVE_CANDIDATE_POLICY_ID,
            policy_fingerprint=ADAPTIVE_POLICY_FINGERPRINT,
        )
    )


def derive_adaptive_candidate(
    snapshot: AdaptiveSourceSnapshotV1,
    *,
    as_of: datetime | str,
    active_profile: Stage15AdaptiveProfileV1 | None = None,
) -> Stage15CandidateV1:
    """Derive one closed candidate without persistence or side effects.

    The source gate runs first.  Only exact sources reach the closed signal
    rules; every returned candidate remains an ephemeral review proposal.
    """

    validate_adaptive_source_snapshot(snapshot)
    if active_profile is not None:
        if type(active_profile) is not Stage15AdaptiveProfileV1:
            raise AdaptiveCognitiveTwinInputError("active profile type is invalid")
        if (
            active_profile.goal_source_uuid != snapshot.goal.goal_source_uuid
            or active_profile.goal_identity_fingerprint != snapshot.goal.goal_identity_fingerprint
        ):
            raise AdaptiveCognitiveTwinInputError("active profile Goal identity conflicts")

    gate = evaluate_adaptive_sufficiency(snapshot, safe_delta=True)
    gate_state = cast(AdaptiveSufficiencyStateV1, gate.state)
    if gate_state is not AdaptiveSufficiencyStateV1.CANDIDATE:
        return _build_candidate(
            snapshot,
            as_of=as_of,
            status=gate_state,
            reasons=tuple(cast(AdaptiveCandidateReasonV1, item) for item in gate.reasons),
            prior_profile=active_profile,
            proposed_profile=None,
            focus=Stage15ProjectionFocusV1.HOLD_CURRENT_PROFILE,
        )

    reasons: list[AdaptiveCandidateReasonV1] = []
    actionable: list[Stage15ProjectionFocusV1] = []
    stage9 = snapshot.stage9_calibration
    if stage9.evaluated_predictions:
        reasons.append(AdaptiveCandidateReasonV1.STAGE9_CALIBRATION_AVAILABLE)
        actionable.append(Stage15ProjectionFocusV1.CALIBRATION_CONTEXT)
    else:
        reasons.append(AdaptiveCandidateReasonV1.STAGE9_CALIBRATION_NOT_EVALUABLE)

    stage10_state = snapshot.stage10_behavioral.relation_state
    if stage10_state is GrowthRelationStateV1.SUPPORTS_GOAL:
        reasons.append(AdaptiveCandidateReasonV1.STAGE10_SUPPORTS_GOAL)
        actionable.append(Stage15ProjectionFocusV1.BEHAVIORAL_CONTEXT)
    elif stage10_state is GrowthRelationStateV1.CONFLICTS_WITH_GOAL:
        reasons.append(AdaptiveCandidateReasonV1.STAGE10_CONFLICTS_WITH_GOAL)
        actionable.append(Stage15ProjectionFocusV1.TRADEOFF_CONTEXT)
    elif stage10_state is not None:
        reasons.append(AdaptiveCandidateReasonV1.STAGE10_STATE_NOT_COMPARABLE)

    stage12_status = snapshot.stage12_progress.status
    if stage12_status is GoalProgressStatusV1.TARGET_MET:
        reasons.append(AdaptiveCandidateReasonV1.STAGE12_TARGET_MET)
        actionable.append(Stage15ProjectionFocusV1.PROGRESS_CONTEXT)
    elif stage12_status is GoalProgressStatusV1.TOWARD_TARGET:
        reasons.append(AdaptiveCandidateReasonV1.STAGE12_TOWARD_TARGET)
        actionable.append(Stage15ProjectionFocusV1.PROGRESS_CONTEXT)
    elif stage12_status is GoalProgressStatusV1.AWAY_FROM_TARGET:
        reasons.append(AdaptiveCandidateReasonV1.STAGE12_AWAY_FROM_TARGET)
        actionable.append(Stage15ProjectionFocusV1.TRADEOFF_CONTEXT)
    elif stage12_status in {
        GoalProgressStatusV1.GOAL_SOURCE_CHANGED,
        GoalProgressStatusV1.NOT_COMPARABLE,
    }:
        reasons.append(AdaptiveCandidateReasonV1.STAGE12_STATE_NOT_COMPARABLE)

    stage14 = snapshot.stage14_experiment
    if stage14.disposition is PersonalExperimentDispositionV1.CONTINUE:
        reasons.append(AdaptiveCandidateReasonV1.STAGE14_TERMINAL_REVIEW_AVAILABLE)
        actionable.append(Stage15ProjectionFocusV1.EXPERIMENT_CONTEXT)
    elif stage14.disposition in {
        PersonalExperimentDispositionV1.HOLD,
        PersonalExperimentDispositionV1.NOT_DECIDED,
    }:
        reasons.append(AdaptiveCandidateReasonV1.STAGE14_OWNER_HOLD)
    elif stage14.terminal_status in {
        PersonalExperimentResultStatusV1.NOT_COMPARABLE,
        PersonalExperimentResultStatusV1.SOURCE_CHANGED,
    }:
        reasons.append(AdaptiveCandidateReasonV1.STAGE14_RESULT_NOT_COMPARABLE)

    tradeoff = Stage15ProjectionFocusV1.TRADEOFF_CONTEXT in actionable
    positive = any(
        focus
        in {
            Stage15ProjectionFocusV1.EXPERIMENT_CONTEXT,
            Stage15ProjectionFocusV1.PROGRESS_CONTEXT,
            Stage15ProjectionFocusV1.BEHAVIORAL_CONTEXT,
            Stage15ProjectionFocusV1.CALIBRATION_CONTEXT,
        }
        for focus in actionable
    )
    if tradeoff and positive:
        reasons.append(AdaptiveCandidateReasonV1.SOURCES_CONFLICT)
        return _build_candidate(
            snapshot,
            as_of=as_of,
            status=AdaptiveSufficiencyStateV1.HOLD,
            reasons=_ordered_unique(reasons),
            prior_profile=active_profile,
            proposed_profile=None,
            focus=Stage15ProjectionFocusV1.HOLD_CURRENT_PROFILE,
        )

    if not actionable or stage14.disposition in {
        PersonalExperimentDispositionV1.HOLD,
        PersonalExperimentDispositionV1.NOT_DECIDED,
    }:
        reasons.append(AdaptiveCandidateReasonV1.NO_SAFE_DELTA)
        return _build_candidate(
            snapshot,
            as_of=as_of,
            status=AdaptiveSufficiencyStateV1.HOLD,
            reasons=_ordered_unique(reasons),
            prior_profile=active_profile,
            proposed_profile=None,
            focus=Stage15ProjectionFocusV1.HOLD_CURRENT_PROFILE,
        )

    focus_order = (
        Stage15ProjectionFocusV1.EXPERIMENT_CONTEXT,
        Stage15ProjectionFocusV1.PROGRESS_CONTEXT,
        Stage15ProjectionFocusV1.BEHAVIORAL_CONTEXT,
        Stage15ProjectionFocusV1.TRADEOFF_CONTEXT,
        Stage15ProjectionFocusV1.CALIBRATION_CONTEXT,
    )
    focus = next(item for item in focus_order if item in actionable)
    if len(actionable) > 1 and len(set(actionable)) == 1:
        reasons.append(AdaptiveCandidateReasonV1.SOURCES_AGREE_ON_FOCUS)
    interaction_mode, measure = _profile_shape_for_focus(focus)
    proposal = Stage15ProfileProposalV1(
        contract_version=ADAPTIVE_CONTRACT_VERSION,
        profile_version="1",
        goal_source_uuid=snapshot.goal.goal_source_uuid,
        goal_identity_fingerprint=cast(AdaptiveHashV1, snapshot.goal.goal_identity_fingerprint),
        source_snapshot_fingerprint=snapshot.source_snapshot_fingerprint,
        projection_focus=focus,
        interaction_mode=interaction_mode,
        evaluation_measure=measure,
        profile_policy_id=ADAPTIVE_PROFILE_POLICY_ID,
        profile_policy_fingerprint=ADAPTIVE_POLICY_FINGERPRINT,
    )
    if active_profile is not None and active_profile.profile_shape_fingerprint == (
        adaptive_hash_json(proposal.fingerprint_payload())
    ):
        reasons.append(AdaptiveCandidateReasonV1.NO_SAFE_DELTA)
        return _build_candidate(
            snapshot,
            as_of=as_of,
            status=AdaptiveSufficiencyStateV1.HOLD,
            reasons=_ordered_unique(reasons),
            prior_profile=active_profile,
            proposed_profile=None,
            focus=Stage15ProjectionFocusV1.HOLD_CURRENT_PROFILE,
        )
    return _build_candidate(
        snapshot,
        as_of=as_of,
        status=AdaptiveSufficiencyStateV1.CANDIDATE,
        reasons=_ordered_unique(reasons),
        prior_profile=active_profile,
        proposed_profile=proposal,
        focus=focus,
    )


AdaptiveSourceSnapshot = AdaptiveSourceSnapshotV1
AdaptiveCandidate = Stage15CandidateV1
AdaptiveProfileProposal = Stage15ProfileProposalV1
AdaptiveProfile = Stage15AdaptiveProfileV1


__all__ = [
    "ADAPTIVE_CANDIDATE_POLICY_ID",
    "ADAPTIVE_CONTRACT_ID",
    "ADAPTIVE_CONTRACT_VERSION",
    "ADAPTIVE_DERIVATION_VERSION",
    "ADAPTIVE_EVALUATION_POLICY_ID",
    "ADAPTIVE_NON_CAUSAL_LANGUAGE",
    "ADAPTIVE_POLICY_CANONICAL_JSON",
    "ADAPTIVE_POLICY_FINGERPRINT",
    "ADAPTIVE_PROFILE_POLICY_ID",
    "ADAPTIVE_SNAPSHOT_VERSION",
    "ADAPTIVE_STORE_FORMAT_VERSION",
    "AdaptiveCandidate",
    "AdaptiveCandidateReasonV1",
    "AdaptiveCognitiveTwinInputError",
    "AdaptiveProfile",
    "AdaptiveProfileProposal",
    "AdaptiveSourceReadinessV1",
    "AdaptiveSourceSnapshot",
    "AdaptiveSourceSnapshotV1",
    "AdaptiveSufficiencyDecisionV1",
    "AdaptiveSufficiencyStateV1",
    "GoalSourceSnapshotV1",
    "Stage9CalibrationSnapshotV1",
    "Stage10BehavioralSnapshotV1",
    "Stage12ProgressSnapshotV1",
    "Stage14ExperimentSelectorV1",
    "Stage14ExperimentSnapshotV1",
    "Stage15AdaptiveProfileV1",
    "Stage15CandidateV1",
    "Stage15CaveatV1",
    "Stage15EvaluationPlanV1",
    "Stage15InteractionModeV1",
    "Stage15MeasureV1",
    "Stage15ProfileProposalV1",
    "Stage15ProjectionFocusV1",
    "adaptive_hash_bytes",
    "adaptive_hash_json",
    "build_adaptive_profile",
    "canonical_adaptive_json_bytes",
    "derive_adaptive_candidate",
    "evaluate_adaptive_sufficiency",
    "load_adaptive_source_snapshot",
    "load_stage9_calibration_snapshot",
    "load_stage10_behavioral_snapshot",
    "load_stage12_progress_snapshot",
    "load_stage14_experiment_snapshot",
    "validate_adaptive_candidate",
    "validate_adaptive_hash",
    "validate_adaptive_policy",
    "validate_adaptive_source_snapshot",
]
