"""Provider-free Stage 15.3 projection and explicit evaluation.

This module is a read-side boundary around the immutable Stage 15.1 source
snapshot and candidate core.  It exposes only bounded, typed references and
does not read or write the operational store, touch the vault, call a
provider, or change any Stage 1-14 result.

The active profile stores one aggregate activation fingerprint in v1.  An
evaluation therefore reports source_pack when that aggregate differs; the
four source families remain individually visible in the projection readiness
list without inventing family-level attribution that v1 cannot prove.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import Final, cast
from uuid import UUID

from second_brain.application.adaptive_cognitive_twin import (
    ADAPTIVE_CANDIDATE_POLICY_ID,
    ADAPTIVE_CONTRACT_VERSION,
    ADAPTIVE_DERIVATION_VERSION,
    ADAPTIVE_EVALUATION_POLICY_ID,
    ADAPTIVE_POLICY_FINGERPRINT,
    ADAPTIVE_PROFILE_POLICY_ID,
    AdaptiveCognitiveTwinInputError,
    AdaptiveHashV1,
    AdaptiveSourceReadinessV1,
    AdaptiveSourceSnapshotV1,
    AdaptiveSufficiencyStateV1,
    Stage9CalibrationSnapshotV1,
    Stage10BehavioralSnapshotV1,
    Stage12ProgressSnapshotV1,
    Stage14ExperimentSnapshotV1,
    Stage15AdaptiveProfileV1,
    Stage15CandidateV1,
    Stage15CaveatV1,
    Stage15EvaluationPlanV1,
    Stage15InteractionModeV1,
    Stage15MeasureV1,
    Stage15ProfileProposalV1,
    Stage15ProjectionFocusV1,
    adaptive_hash_json,
    canonical_adaptive_json_bytes,
    derive_adaptive_candidate,
    validate_adaptive_candidate,
    validate_adaptive_hash,
    validate_adaptive_policy,
    validate_adaptive_source_snapshot,
)
from second_brain.domain.models import parse_rfc3339, parse_uuid7

ADAPTIVE_PROJECTION_VERSION: Final[str] = "1"
MAX_ADAPTIVE_PROJECTION_BYTES: Final[int] = 160 * 1024
MAX_ADAPTIVE_EVALUATION_RESULT_BYTES: Final[int] = 16 * 1024
MAX_ADAPTIVE_SOURCE_FAMILIES: Final[int] = 5

ADAPTIVE_NON_CAUSAL_PHRASE: Final[str] = (
    "Наблюдаемое изменение в выбранном периоде не является доказательством того, "
    "что профиль вызвал это изменение."
)


class Stage15SourceFamilyV1(StrEnum):
    """The four source families plus one honest aggregate evaluation marker."""

    STAGE9_CALIBRATION = "stage9_calibration"
    STAGE10_BEHAVIORAL = "stage10_behavioral"
    STAGE12_PROGRESS = "stage12_progress"
    STAGE14_EXPERIMENT = "stage14_experiment"
    SOURCE_PACK = "source_pack"


_PROJECTION_SOURCE_FAMILIES: Final[tuple[Stage15SourceFamilyV1, ...]] = (
    Stage15SourceFamilyV1.STAGE9_CALIBRATION,
    Stage15SourceFamilyV1.STAGE10_BEHAVIORAL,
    Stage15SourceFamilyV1.STAGE12_PROGRESS,
    Stage15SourceFamilyV1.STAGE14_EXPERIMENT,
)


class Stage15ProjectionLifecycleStateV1(StrEnum):
    """Closed lifecycle disclosure for the owner projection."""

    NO_ACTIVE_PROFILE = "no_active_profile"
    CANDIDATE_PENDING_REVIEW = "candidate_pending_review"
    ACTIVE_PROFILE_VALID = "active_profile_valid"
    ACTIVE_PROFILE_STALE = "active_profile_stale"


class Stage15ProfileValidityV1(StrEnum):
    """Whether the disclosed active profile matches the current source pack."""

    NONE = "none"
    VALID = "valid"
    STALE = "stale"


class Stage15ProfileFieldV1(StrEnum):
    """Only the closed profile fields that may differ in a projection."""

    PROJECTION_FOCUS = "projection_focus"
    INTERACTION_MODE = "interaction_mode"
    EVALUATION_MEASURE = "evaluation_measure"
    SOURCE_SNAPSHOT_FINGERPRINT = "source_snapshot_fingerprint"


class Stage15EvaluationStateV1(StrEnum):
    """Closed states for one later explicit comparison."""

    EVALUATED = "evaluated"
    INSUFFICIENT = "insufficient"
    NOT_COMPARABLE = "not_comparable"
    SOURCE_CHANGED = "source_changed"
    POLICY_MISMATCH = "policy_mismatch"


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
    except TypeError, ValueError, OverflowError:
        raise AdaptiveCognitiveTwinInputError(f"{label} is invalid") from None


def _timestamp(value: object, label: str) -> datetime:
    try:
        parsed = parse_rfc3339(value)
    except TypeError, ValueError, OverflowError:
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


def _tuple_enums[EnumT: StrEnum](
    values: object,
    enum_type: type[EnumT],
    label: str,
    maximum: int,
) -> tuple[EnumT, ...]:
    if type(values) is not tuple or len(values) > maximum:
        raise AdaptiveCognitiveTwinInputError(f"{label} is invalid")
    normalized = tuple(_enum(enum_type, value, label) for value in values)
    if len(set(normalized)) != len(normalized):
        raise AdaptiveCognitiveTwinInputError(f"{label} are duplicated")
    return normalized


def _tuple_enums_hashes(values: object) -> tuple[AdaptiveHashV1, ...]:
    if type(values) is not tuple or len(values) > 4:
        raise AdaptiveCognitiveTwinInputError("source policy fingerprints are invalid")
    result: list[AdaptiveHashV1] = []
    for value in values:
        normalized = validate_adaptive_hash(value)
        if normalized in result:
            raise AdaptiveCognitiveTwinInputError("source policy fingerprints are duplicated")
        result.append(normalized)
    return tuple(result)


def _safe_policy_fingerprints(
    values: Sequence[AdaptiveHashV1 | None],
) -> tuple[AdaptiveHashV1, ...]:
    result: list[AdaptiveHashV1] = []
    for value in values:
        if value is not None and value not in result:
            result.append(validate_adaptive_hash(value))
    return tuple(result)


@dataclass(frozen=True, slots=True)
class Stage15SourceReadinessItemV1:
    """Safe readiness and provenance projection for one source family."""

    family: Stage15SourceFamilyV1 | str
    readiness: AdaptiveSourceReadinessV1 | str
    source_fingerprint: AdaptiveHashV1 | None
    reference_id: UUID | str | None
    reference_fingerprint: AdaptiveHashV1 | None
    policy_fingerprints: tuple[AdaptiveHashV1, ...]
    as_of: datetime | str | None

    def __post_init__(self) -> None:
        family = _enum(Stage15SourceFamilyV1, self.family, "source family")
        if family is Stage15SourceFamilyV1.SOURCE_PACK:
            raise AdaptiveCognitiveTwinInputError("source pack is not a readiness family")
        object.__setattr__(self, "family", family)
        readiness = _enum(AdaptiveSourceReadinessV1, self.readiness, "source readiness")
        object.__setattr__(self, "readiness", readiness)
        object.__setattr__(
            self,
            "source_fingerprint",
            _hash_or_none(self.source_fingerprint, "source fingerprint"),
        )
        if self.reference_id is not None:
            object.__setattr__(
                self, "reference_id", _uuid(self.reference_id, "source reference id")
            )
        object.__setattr__(
            self,
            "reference_fingerprint",
            _hash_or_none(self.reference_fingerprint, "source reference fingerprint"),
        )
        object.__setattr__(
            self,
            "policy_fingerprints",
            _tuple_enums_hashes(self.policy_fingerprints),
        )
        if self.as_of is not None:
            object.__setattr__(self, "as_of", _timestamp(self.as_of, "source as_of"))
        if readiness is AdaptiveSourceReadinessV1.EXACT_CURRENT and (
            self.source_fingerprint is None or self.reference_fingerprint is None
        ):
            raise AdaptiveCognitiveTwinInputError("exact source readiness is incomplete")

    def as_dict(self) -> dict[str, object]:
        return {
            "family": cast(Stage15SourceFamilyV1, self.family).value,
            "readiness": cast(AdaptiveSourceReadinessV1, self.readiness).value,
            "source_fingerprint": self.source_fingerprint,
            "reference_id": str(self.reference_id) if self.reference_id is not None else None,
            "reference_fingerprint": self.reference_fingerprint,
            "policy_fingerprints": list(self.policy_fingerprints),
            "as_of": (
                _format_timestamp(cast(datetime, self.as_of)) if self.as_of is not None else None
            ),
        }


@dataclass(frozen=True, slots=True)
class Stage15ProfileDiffV1:
    """Bounded structural diff; it never labels a profile as better or worse."""

    changed_fields: tuple[Stage15ProfileFieldV1 | str, ...]
    current_projection_focus: Stage15ProjectionFocusV1 | None
    proposed_projection_focus: Stage15ProjectionFocusV1 | None
    current_interaction_mode: Stage15InteractionModeV1 | None
    proposed_interaction_mode: Stage15InteractionModeV1 | None
    current_evaluation_measure: Stage15MeasureV1 | None
    proposed_evaluation_measure: Stage15MeasureV1 | None
    current_source_snapshot_fingerprint: AdaptiveHashV1 | None
    proposed_source_snapshot_fingerprint: AdaptiveHashV1 | None

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "changed_fields",
            _tuple_enums(
                self.changed_fields,
                Stage15ProfileFieldV1,
                "profile diff fields",
                len(Stage15ProfileFieldV1),
            ),
        )
        for name, enum_type in (
            ("current_projection_focus", Stage15ProjectionFocusV1),
            ("proposed_projection_focus", Stage15ProjectionFocusV1),
            ("current_interaction_mode", Stage15InteractionModeV1),
            ("proposed_interaction_mode", Stage15InteractionModeV1),
            ("current_evaluation_measure", Stage15MeasureV1),
            ("proposed_evaluation_measure", Stage15MeasureV1),
        ):
            value = getattr(self, name)
            if value is not None:
                object.__setattr__(self, name, _enum(enum_type, value, name))
        for name in (
            "current_source_snapshot_fingerprint",
            "proposed_source_snapshot_fingerprint",
        ):
            object.__setattr__(self, name, _hash_or_none(getattr(self, name), name))

    def as_dict(self) -> dict[str, object]:
        def enum_value(value: StrEnum | None) -> str | None:
            return value.value if value is not None else None

        return {
            "changed_fields": [
                cast(Stage15ProfileFieldV1, item).value for item in self.changed_fields
            ],
            "current_projection_focus": enum_value(self.current_projection_focus),
            "proposed_projection_focus": enum_value(self.proposed_projection_focus),
            "current_interaction_mode": enum_value(self.current_interaction_mode),
            "proposed_interaction_mode": enum_value(self.proposed_interaction_mode),
            "current_evaluation_measure": enum_value(self.current_evaluation_measure),
            "proposed_evaluation_measure": enum_value(self.proposed_evaluation_measure),
            "current_source_snapshot_fingerprint": self.current_source_snapshot_fingerprint,
            "proposed_source_snapshot_fingerprint": self.proposed_source_snapshot_fingerprint,
        }


@dataclass(frozen=True, slots=True)
class Stage15ProjectionProvenanceV1:
    """Fixed policy and derivation references for progressive disclosure."""

    contract_version: str
    derivation_version: str
    source_snapshot_fingerprint: AdaptiveHashV1
    candidate_policy_id: str
    candidate_policy_fingerprint: AdaptiveHashV1
    profile_policy_id: str
    profile_policy_fingerprint: AdaptiveHashV1
    evaluation_policy_id: str
    evaluation_policy_fingerprint: AdaptiveHashV1

    def __post_init__(self) -> None:
        if (
            self.contract_version != ADAPTIVE_CONTRACT_VERSION
            or type(self.contract_version) is not str
        ):
            raise AdaptiveCognitiveTwinInputError("projection contract version is invalid")
        if (
            self.derivation_version != ADAPTIVE_DERIVATION_VERSION
            or type(self.derivation_version) is not str
        ):
            raise AdaptiveCognitiveTwinInputError("projection derivation version is invalid")
        object.__setattr__(
            self,
            "source_snapshot_fingerprint",
            validate_adaptive_hash(self.source_snapshot_fingerprint),
        )
        for name, expected in (
            ("candidate_policy_id", ADAPTIVE_CANDIDATE_POLICY_ID),
            ("profile_policy_id", ADAPTIVE_PROFILE_POLICY_ID),
            ("evaluation_policy_id", ADAPTIVE_EVALUATION_POLICY_ID),
        ):
            value = getattr(self, name)
            if value != expected or type(value) is not str:
                raise AdaptiveCognitiveTwinInputError(f"{name} is invalid")
        for name in (
            "candidate_policy_fingerprint",
            "profile_policy_fingerprint",
            "evaluation_policy_fingerprint",
        ):
            value = getattr(self, name)
            if value != ADAPTIVE_POLICY_FINGERPRINT:
                raise AdaptiveCognitiveTwinInputError(f"{name} is invalid")

    def as_dict(self) -> dict[str, object]:
        return {
            "contract_version": self.contract_version,
            "derivation_version": self.derivation_version,
            "source_snapshot_fingerprint": self.source_snapshot_fingerprint,
            "candidate_policy_id": self.candidate_policy_id,
            "candidate_policy_fingerprint": self.candidate_policy_fingerprint,
            "profile_policy_id": self.profile_policy_id,
            "profile_policy_fingerprint": self.profile_policy_fingerprint,
            "evaluation_policy_id": self.evaluation_policy_id,
            "evaluation_policy_fingerprint": self.evaluation_policy_fingerprint,
        }


@dataclass(frozen=True, slots=True)
class Stage15AdaptiveProjectionV1:
    """Complete bounded Stage15 read model for one exact source snapshot."""

    contract_version: str
    projection_version: str
    as_of: datetime | str
    goal_source_uuid: UUID | str
    goal_identity_fingerprint: AdaptiveHashV1
    goal_readiness: AdaptiveSourceReadinessV1 | str
    source_snapshot_fingerprint: AdaptiveHashV1
    source_readiness: tuple[Stage15SourceReadinessItemV1, ...]
    active_profile: Stage15AdaptiveProfileV1 | None
    lifecycle_state: Stage15ProjectionLifecycleStateV1 | str
    profile_validity: Stage15ProfileValidityV1 | str
    candidate: Stage15CandidateV1
    profile_diff: Stage15ProfileDiffV1 | None
    provenance: Stage15ProjectionProvenanceV1

    def __post_init__(self) -> None:
        if (
            self.contract_version != ADAPTIVE_CONTRACT_VERSION
            or type(self.contract_version) is not str
        ):
            raise AdaptiveCognitiveTwinInputError("projection contract version is invalid")
        if (
            self.projection_version != ADAPTIVE_PROJECTION_VERSION
            or type(self.projection_version) is not str
        ):
            raise AdaptiveCognitiveTwinInputError("projection version is invalid")
        object.__setattr__(self, "as_of", _timestamp(self.as_of, "projection as_of"))
        object.__setattr__(
            self, "goal_source_uuid", _uuid(self.goal_source_uuid, "projection Goal UUID")
        )
        object.__setattr__(
            self,
            "goal_identity_fingerprint",
            validate_adaptive_hash(self.goal_identity_fingerprint),
        )
        object.__setattr__(
            self,
            "goal_readiness",
            _enum(AdaptiveSourceReadinessV1, self.goal_readiness, "Goal readiness"),
        )
        object.__setattr__(
            self,
            "source_snapshot_fingerprint",
            validate_adaptive_hash(self.source_snapshot_fingerprint),
        )
        if type(self.source_readiness) is not tuple or len(self.source_readiness) != len(
            _PROJECTION_SOURCE_FAMILIES
        ):
            raise AdaptiveCognitiveTwinInputError("projection source readiness is invalid")
        for expected_family, item in zip(
            _PROJECTION_SOURCE_FAMILIES, self.source_readiness, strict=True
        ):
            if type(item) is not Stage15SourceReadinessItemV1 or item.family is not expected_family:
                raise AdaptiveCognitiveTwinInputError(
                    "projection source readiness order is invalid"
                )
        if self.active_profile is not None:
            if type(self.active_profile) is not Stage15AdaptiveProfileV1:
                raise AdaptiveCognitiveTwinInputError("projection active profile is invalid")
            if (
                self.active_profile.goal_source_uuid != self.goal_source_uuid
                or self.active_profile.goal_identity_fingerprint != self.goal_identity_fingerprint
            ):
                raise AdaptiveCognitiveTwinInputError("projection active profile Goal conflicts")
        object.__setattr__(
            self,
            "lifecycle_state",
            _enum(Stage15ProjectionLifecycleStateV1, self.lifecycle_state, "projection lifecycle"),
        )
        object.__setattr__(
            self,
            "profile_validity",
            _enum(Stage15ProfileValidityV1, self.profile_validity, "profile validity"),
        )
        if type(self.candidate) is not Stage15CandidateV1:
            raise AdaptiveCognitiveTwinInputError("projection candidate is invalid")
        validate_adaptive_candidate(self.candidate)
        if (
            self.candidate.goal_source_uuid != self.goal_source_uuid
            or self.candidate.goal_identity_fingerprint != self.goal_identity_fingerprint
            or self.candidate.source_snapshot_fingerprint != self.source_snapshot_fingerprint
        ):
            raise AdaptiveCognitiveTwinInputError("projection candidate binding is invalid")
        if self.active_profile is None:
            if self.profile_validity is not Stage15ProfileValidityV1.NONE:
                raise AdaptiveCognitiveTwinInputError("projection profile validity is invalid")
            if self.lifecycle_state not in {
                Stage15ProjectionLifecycleStateV1.NO_ACTIVE_PROFILE,
                Stage15ProjectionLifecycleStateV1.CANDIDATE_PENDING_REVIEW,
            }:
                raise AdaptiveCognitiveTwinInputError("projection lifecycle is invalid")
        elif self.active_profile.source_snapshot_fingerprint == self.source_snapshot_fingerprint:
            if self.profile_validity is not Stage15ProfileValidityV1.VALID:
                raise AdaptiveCognitiveTwinInputError("projection profile validity is invalid")
            if self.lifecycle_state is not Stage15ProjectionLifecycleStateV1.ACTIVE_PROFILE_VALID:
                raise AdaptiveCognitiveTwinInputError("projection lifecycle is invalid")
        else:
            if self.profile_validity is not Stage15ProfileValidityV1.STALE:
                raise AdaptiveCognitiveTwinInputError("projection profile validity is invalid")
            if self.lifecycle_state is not Stage15ProjectionLifecycleStateV1.ACTIVE_PROFILE_STALE:
                raise AdaptiveCognitiveTwinInputError("projection lifecycle is invalid")
        if self.profile_diff is not None and type(self.profile_diff) is not Stage15ProfileDiffV1:
            raise AdaptiveCognitiveTwinInputError("projection profile diff is invalid")
        if type(self.provenance) is not Stage15ProjectionProvenanceV1:
            raise AdaptiveCognitiveTwinInputError("projection provenance is invalid")
        if self.provenance.source_snapshot_fingerprint != self.source_snapshot_fingerprint:
            raise AdaptiveCognitiveTwinInputError("projection provenance binding is invalid")

    def as_dict(self) -> dict[str, object]:
        return {
            "contract_version": self.contract_version,
            "projection_version": self.projection_version,
            "as_of": _format_timestamp(cast(datetime, self.as_of)),
            "goal_source_uuid": str(self.goal_source_uuid),
            "goal_identity_fingerprint": self.goal_identity_fingerprint,
            "goal_readiness": cast(AdaptiveSourceReadinessV1, self.goal_readiness).value,
            "source_snapshot_fingerprint": self.source_snapshot_fingerprint,
            "source_readiness": [item.as_dict() for item in self.source_readiness],
            "active_profile": (
                self.active_profile.as_dict() if self.active_profile is not None else None
            ),
            "lifecycle_state": cast(Stage15ProjectionLifecycleStateV1, self.lifecycle_state).value,
            "profile_validity": cast(Stage15ProfileValidityV1, self.profile_validity).value,
            "candidate": self.candidate.as_dict(),
            "profile_diff": self.profile_diff.as_dict() if self.profile_diff is not None else None,
            "provenance": self.provenance.as_dict(),
        }

    def to_json(self) -> str:
        return _canonical_projection_json(self.as_dict())


def _canonical_projection_json(value: object) -> str:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError, UnicodeError, OverflowError) as exc:
        raise AdaptiveCognitiveTwinInputError("adaptive projection JSON is invalid") from exc


def validate_adaptive_projection(value: object) -> Stage15AdaptiveProjectionV1:
    """Validate one bounded projection without adding a read or write side effect."""

    if type(value) is not Stage15AdaptiveProjectionV1:
        raise AdaptiveCognitiveTwinInputError("adaptive projection type is invalid")
    validate_adaptive_policy()
    try:
        encoded = value.to_json().encode("utf-8")
    except TypeError, ValueError, UnicodeError, OverflowError:
        raise AdaptiveCognitiveTwinInputError("adaptive projection is invalid") from None
    if len(encoded) > MAX_ADAPTIVE_PROJECTION_BYTES:
        raise AdaptiveCognitiveTwinInputError("adaptive projection is too large")
    return value


def _source_projection(
    family: Stage15SourceFamilyV1,
    source: (
        Stage9CalibrationSnapshotV1
        | Stage10BehavioralSnapshotV1
        | Stage12ProgressSnapshotV1
        | Stage14ExperimentSnapshotV1
    ),
) -> Stage15SourceReadinessItemV1:
    source_fingerprint = adaptive_hash_json(source.as_dict())
    if family is Stage15SourceFamilyV1.STAGE9_CALIBRATION:
        assert isinstance(source, Stage9CalibrationSnapshotV1)
        return Stage15SourceReadinessItemV1(
            family=family,
            readiness=source.readiness,
            source_fingerprint=source_fingerprint,
            reference_id=source.generation_id,
            reference_fingerprint=source.result_fingerprint,
            policy_fingerprints=_safe_policy_fingerprints((source.policy_fingerprint,)),
            as_of=source.as_of,
        )
    if family is Stage15SourceFamilyV1.STAGE10_BEHAVIORAL:
        assert isinstance(source, Stage10BehavioralSnapshotV1)
        return Stage15SourceReadinessItemV1(
            family=family,
            readiness=source.readiness,
            source_fingerprint=source_fingerprint,
            reference_id=source.mapping_id,
            reference_fingerprint=source.mapping_fingerprint
            or source.behavioral_source_fingerprint,
            policy_fingerprints=_safe_policy_fingerprints(
                (
                    source.growth_policy_fingerprint,
                    source.behavioral_policy_fingerprint,
                    source.mapping_policy_fingerprint,
                )
            ),
            as_of=None,
        )
    if family is Stage15SourceFamilyV1.STAGE12_PROGRESS:
        assert isinstance(source, Stage12ProgressSnapshotV1)
        return Stage15SourceReadinessItemV1(
            family=family,
            readiness=source.readiness,
            source_fingerprint=source_fingerprint,
            reference_id=source.definition_id,
            reference_fingerprint=source.progress_result_fingerprint,
            policy_fingerprints=_safe_policy_fingerprints((source.progress_policy_fingerprint,)),
            as_of=source.progress_as_of,
        )
    assert isinstance(source, Stage14ExperimentSnapshotV1)
    return Stage15SourceReadinessItemV1(
        family=family,
        readiness=source.readiness,
        source_fingerprint=source_fingerprint,
        reference_id=source.experiment_definition_id or source.reassessment_id,
        reference_fingerprint=source.terminal_result_fingerprint or source.reassessment_fingerprint,
        policy_fingerprints=_safe_policy_fingerprints(
            (
                source.experiment_policy_fingerprint,
                source.reassessment_evaluation_policy_fingerprint,
            )
        ),
        as_of=source.reassessment_reviewed_at or source.terminal_result_as_of,
    )


def _build_source_readiness(
    snapshot: AdaptiveSourceSnapshotV1,
) -> tuple[Stage15SourceReadinessItemV1, ...]:
    return (
        _source_projection(
            Stage15SourceFamilyV1.STAGE9_CALIBRATION,
            snapshot.stage9_calibration,
        ),
        _source_projection(
            Stage15SourceFamilyV1.STAGE10_BEHAVIORAL,
            snapshot.stage10_behavioral,
        ),
        _source_projection(
            Stage15SourceFamilyV1.STAGE12_PROGRESS,
            snapshot.stage12_progress,
        ),
        _source_projection(
            Stage15SourceFamilyV1.STAGE14_EXPERIMENT,
            snapshot.stage14_experiment,
        ),
    )


def _profile_diff(
    active_profile: Stage15AdaptiveProfileV1 | None,
    proposal: Stage15ProfileProposalV1,
) -> Stage15ProfileDiffV1:
    current_focus = (
        cast(Stage15ProjectionFocusV1, active_profile.projection_focus)
        if active_profile is not None
        else None
    )
    current_mode = (
        cast(Stage15InteractionModeV1, active_profile.interaction_mode)
        if active_profile is not None
        else None
    )
    current_measure = (
        cast(Stage15MeasureV1, active_profile.evaluation_measure)
        if active_profile is not None
        else None
    )
    current_source_fingerprint = (
        active_profile.source_snapshot_fingerprint if active_profile is not None else None
    )
    proposed_focus = cast(Stage15ProjectionFocusV1, proposal.projection_focus)
    proposed_mode = cast(Stage15InteractionModeV1, proposal.interaction_mode)
    proposed_measure = cast(Stage15MeasureV1, proposal.evaluation_measure)
    changed: list[Stage15ProfileFieldV1] = []
    if current_focus != proposed_focus:
        changed.append(Stage15ProfileFieldV1.PROJECTION_FOCUS)
    if current_mode != proposed_mode:
        changed.append(Stage15ProfileFieldV1.INTERACTION_MODE)
    if current_measure != proposed_measure:
        changed.append(Stage15ProfileFieldV1.EVALUATION_MEASURE)
    if current_source_fingerprint != proposal.source_snapshot_fingerprint:
        changed.append(Stage15ProfileFieldV1.SOURCE_SNAPSHOT_FINGERPRINT)
    return Stage15ProfileDiffV1(
        changed_fields=tuple(changed),
        current_projection_focus=current_focus,
        proposed_projection_focus=proposed_focus,
        current_interaction_mode=current_mode,
        proposed_interaction_mode=proposed_mode,
        current_evaluation_measure=current_measure,
        proposed_evaluation_measure=proposed_measure,
        current_source_snapshot_fingerprint=current_source_fingerprint,
        proposed_source_snapshot_fingerprint=proposal.source_snapshot_fingerprint,
    )


def _projection_provenance(
    source_snapshot_fingerprint: AdaptiveHashV1,
) -> Stage15ProjectionProvenanceV1:
    return Stage15ProjectionProvenanceV1(
        contract_version=ADAPTIVE_CONTRACT_VERSION,
        derivation_version=ADAPTIVE_DERIVATION_VERSION,
        source_snapshot_fingerprint=source_snapshot_fingerprint,
        candidate_policy_id=ADAPTIVE_CANDIDATE_POLICY_ID,
        candidate_policy_fingerprint=ADAPTIVE_POLICY_FINGERPRINT,
        profile_policy_id=ADAPTIVE_PROFILE_POLICY_ID,
        profile_policy_fingerprint=ADAPTIVE_POLICY_FINGERPRINT,
        evaluation_policy_id=ADAPTIVE_EVALUATION_POLICY_ID,
        evaluation_policy_fingerprint=ADAPTIVE_POLICY_FINGERPRINT,
    )


def project_adaptive_cognitive_twin(
    snapshot: AdaptiveSourceSnapshotV1,
    *,
    as_of: datetime | str,
    active_profile: Stage15AdaptiveProfileV1 | None = None,
) -> Stage15AdaptiveProjectionV1:
    """Build one bounded read model from an exact source snapshot."""

    validate_adaptive_source_snapshot(snapshot)
    projection_as_of = _timestamp(as_of, "projection as_of")
    if active_profile is not None and type(active_profile) is not Stage15AdaptiveProfileV1:
        raise AdaptiveCognitiveTwinInputError("projection active profile type is invalid")
    candidate = derive_adaptive_candidate(
        snapshot,
        as_of=projection_as_of,
        active_profile=active_profile,
    )
    status = candidate.candidate_status
    if active_profile is None:
        validity = Stage15ProfileValidityV1.NONE
        lifecycle = (
            Stage15ProjectionLifecycleStateV1.CANDIDATE_PENDING_REVIEW
            if status is AdaptiveSufficiencyStateV1.CANDIDATE
            else Stage15ProjectionLifecycleStateV1.NO_ACTIVE_PROFILE
        )
    elif active_profile.source_snapshot_fingerprint == snapshot.source_snapshot_fingerprint:
        validity = Stage15ProfileValidityV1.VALID
        lifecycle = Stage15ProjectionLifecycleStateV1.ACTIVE_PROFILE_VALID
    else:
        validity = Stage15ProfileValidityV1.STALE
        lifecycle = Stage15ProjectionLifecycleStateV1.ACTIVE_PROFILE_STALE
    profile_diff = (
        _profile_diff(active_profile, candidate.proposed_profile)
        if candidate.proposed_profile is not None
        else None
    )
    return validate_adaptive_projection(
        Stage15AdaptiveProjectionV1(
            contract_version=ADAPTIVE_CONTRACT_VERSION,
            projection_version=ADAPTIVE_PROJECTION_VERSION,
            as_of=projection_as_of,
            goal_source_uuid=snapshot.goal.goal_source_uuid,
            goal_identity_fingerprint=cast(AdaptiveHashV1, snapshot.goal.goal_identity_fingerprint),
            goal_readiness=snapshot.goal.readiness,
            source_snapshot_fingerprint=snapshot.source_snapshot_fingerprint,
            source_readiness=_build_source_readiness(snapshot),
            active_profile=active_profile,
            lifecycle_state=lifecycle,
            profile_validity=validity,
            candidate=candidate,
            profile_diff=profile_diff,
            provenance=_projection_provenance(snapshot.source_snapshot_fingerprint),
        )
    )


def _evaluation_readiness(
    snapshot: AdaptiveSourceSnapshotV1,
) -> tuple[AdaptiveSourceReadinessV1, ...]:
    return (
        cast(AdaptiveSourceReadinessV1, snapshot.goal.readiness),
        cast(AdaptiveSourceReadinessV1, snapshot.stage9_calibration.readiness),
        cast(AdaptiveSourceReadinessV1, snapshot.stage10_behavioral.readiness),
        cast(AdaptiveSourceReadinessV1, snapshot.stage12_progress.readiness),
        cast(AdaptiveSourceReadinessV1, snapshot.stage14_experiment.readiness),
    )


def _evaluation_state(
    *,
    later_snapshot: AdaptiveSourceSnapshotV1,
    goal_matches: bool,
) -> Stage15EvaluationStateV1:
    if not goal_matches:
        return Stage15EvaluationStateV1.NOT_COMPARABLE
    readinesses = _evaluation_readiness(later_snapshot)
    if AdaptiveSourceReadinessV1.POLICY_MISMATCH in readinesses:
        return Stage15EvaluationStateV1.POLICY_MISMATCH
    if AdaptiveSourceReadinessV1.NOT_COMPARABLE in readinesses:
        return Stage15EvaluationStateV1.NOT_COMPARABLE
    if any(
        readiness in {AdaptiveSourceReadinessV1.SOURCE_CHANGED, AdaptiveSourceReadinessV1.STALE}
        for readiness in readinesses
    ):
        return Stage15EvaluationStateV1.SOURCE_CHANGED
    if any(
        readiness
        in {
            AdaptiveSourceReadinessV1.SOURCE_MISSING,
            AdaptiveSourceReadinessV1.UNAVAILABLE,
        }
        for readiness in readinesses
    ):
        return Stage15EvaluationStateV1.INSUFFICIENT
    return Stage15EvaluationStateV1.EVALUATED


def _aggregate_source_change(
    activation_source_snapshot_fingerprint: AdaptiveHashV1,
    later_snapshot_fingerprint: AdaptiveHashV1,
) -> tuple[tuple[Stage15SourceFamilyV1, ...], tuple[Stage15SourceFamilyV1, ...]]:
    if activation_source_snapshot_fingerprint == later_snapshot_fingerprint:
        return (), (Stage15SourceFamilyV1.SOURCE_PACK,)
    return (Stage15SourceFamilyV1.SOURCE_PACK,), ()


@dataclass(frozen=True, slots=True)
class Stage15EvaluationResultV1:
    """Bounded descriptive comparison result with no causal or reward field."""

    contract_version: str
    profile_id: UUID | str
    profile_fingerprint: AdaptiveHashV1
    goal_source_uuid: UUID | str
    goal_identity_fingerprint: AdaptiveHashV1
    activation_snapshot_fingerprint: AdaptiveHashV1
    later_snapshot_fingerprint: AdaptiveHashV1
    as_of: datetime | str
    state: Stage15EvaluationStateV1 | str
    changed_sources: tuple[Stage15SourceFamilyV1 | str, ...]
    unchanged_sources: tuple[Stage15SourceFamilyV1 | str, ...]
    caveats: tuple[Stage15CaveatV1 | str, ...]
    evaluation_fingerprint: AdaptiveHashV1 = ""

    def __post_init__(self) -> None:
        if (
            self.contract_version != ADAPTIVE_CONTRACT_VERSION
            or type(self.contract_version) is not str
        ):
            raise AdaptiveCognitiveTwinInputError("evaluation contract version is invalid")
        object.__setattr__(self, "profile_id", _uuid(self.profile_id, "evaluation profile id"))
        for name in (
            "profile_fingerprint",
            "goal_identity_fingerprint",
            "activation_snapshot_fingerprint",
            "later_snapshot_fingerprint",
        ):
            object.__setattr__(self, name, validate_adaptive_hash(getattr(self, name)))
        object.__setattr__(
            self, "goal_source_uuid", _uuid(self.goal_source_uuid, "evaluation Goal UUID")
        )
        object.__setattr__(self, "as_of", _timestamp(self.as_of, "evaluation as_of"))
        object.__setattr__(
            self,
            "state",
            _enum(Stage15EvaluationStateV1, self.state, "evaluation state"),
        )
        changed = _tuple_enums(
            self.changed_sources,
            Stage15SourceFamilyV1,
            "changed sources",
            MAX_ADAPTIVE_SOURCE_FAMILIES,
        )
        unchanged = _tuple_enums(
            self.unchanged_sources,
            Stage15SourceFamilyV1,
            "unchanged sources",
            MAX_ADAPTIVE_SOURCE_FAMILIES,
        )
        if set(changed) & set(unchanged):
            raise AdaptiveCognitiveTwinInputError("evaluation source lists overlap")
        object.__setattr__(self, "changed_sources", changed)
        object.__setattr__(self, "unchanged_sources", unchanged)
        caveats = _tuple_enums(
            self.caveats,
            Stage15CaveatV1,
            "evaluation caveats",
            len(Stage15CaveatV1),
        )
        if not caveats:
            raise AdaptiveCognitiveTwinInputError("evaluation caveats are empty")
        object.__setattr__(self, "caveats", caveats)
        expected = adaptive_hash_json(self.fingerprint_payload())
        if self.evaluation_fingerprint:
            validate_adaptive_hash(self.evaluation_fingerprint)
            if self.evaluation_fingerprint != expected:
                raise AdaptiveCognitiveTwinInputError("evaluation fingerprint mismatch")
        else:
            object.__setattr__(self, "evaluation_fingerprint", expected)
        if (
            len(canonical_adaptive_json_bytes(self.as_dict()))
            > MAX_ADAPTIVE_EVALUATION_RESULT_BYTES
        ):
            raise AdaptiveCognitiveTwinInputError("evaluation result is too large")

    def fingerprint_payload(self) -> dict[str, object]:
        return {
            "contract_version": self.contract_version,
            "profile_id": str(self.profile_id),
            "profile_fingerprint": self.profile_fingerprint,
            "goal_source_uuid": str(self.goal_source_uuid),
            "goal_identity_fingerprint": self.goal_identity_fingerprint,
            "activation_snapshot_fingerprint": self.activation_snapshot_fingerprint,
            "later_snapshot_fingerprint": self.later_snapshot_fingerprint,
            "as_of": _format_timestamp(cast(datetime, self.as_of)),
            "state": cast(Stage15EvaluationStateV1, self.state).value,
            "changed_sources": [
                cast(Stage15SourceFamilyV1, item).value for item in self.changed_sources
            ],
            "unchanged_sources": [
                cast(Stage15SourceFamilyV1, item).value for item in self.unchanged_sources
            ],
            "caveats": [cast(Stage15CaveatV1, item).value for item in self.caveats],
        }

    def as_dict(self) -> dict[str, object]:
        return {**self.fingerprint_payload(), "evaluation_fingerprint": self.evaluation_fingerprint}

    def to_json(self) -> str:
        return _canonical_projection_json(self.as_dict())

    @property
    def non_causal_phrase(self) -> str:
        """Return the only permitted user-facing interpretation sentence."""

        return ADAPTIVE_NON_CAUSAL_PHRASE


def validate_adaptive_evaluation_result(value: object) -> Stage15EvaluationResultV1:
    """Validate one bounded descriptive evaluation result."""

    if type(value) is not Stage15EvaluationResultV1:
        raise AdaptiveCognitiveTwinInputError("adaptive evaluation result type is invalid")
    validate_adaptive_policy()
    try:
        encoded = value.to_json().encode("utf-8")
    except TypeError, ValueError, UnicodeError, OverflowError:
        raise AdaptiveCognitiveTwinInputError("adaptive evaluation result is invalid") from None
    if len(encoded) > MAX_ADAPTIVE_EVALUATION_RESULT_BYTES:
        raise AdaptiveCognitiveTwinInputError("adaptive evaluation result is too large")
    return value


def evaluate_adaptive_profile(
    active_profile: Stage15AdaptiveProfileV1,
    *,
    later_snapshot: AdaptiveSourceSnapshotV1,
    as_of: datetime | str,
    activation_source_snapshot_fingerprint: AdaptiveHashV1,
    evaluation_plan: Stage15EvaluationPlanV1,
) -> Stage15EvaluationResultV1:
    """Evaluate one active profile against one explicit later source snapshot."""

    if type(active_profile) is not Stage15AdaptiveProfileV1:
        raise AdaptiveCognitiveTwinInputError("evaluation active profile type is invalid")
    validate_adaptive_source_snapshot(later_snapshot)
    if type(evaluation_plan) is not Stage15EvaluationPlanV1:
        raise AdaptiveCognitiveTwinInputError("evaluation plan type is invalid")
    evaluation_as_of = _timestamp(as_of, "evaluation as_of")
    activation_fingerprint = validate_adaptive_hash(activation_source_snapshot_fingerprint)
    if activation_fingerprint != active_profile.source_snapshot_fingerprint:
        raise AdaptiveCognitiveTwinInputError("evaluation activation fingerprint conflicts")
    if evaluation_plan.baseline_source_snapshot_fingerprint != activation_fingerprint:
        raise AdaptiveCognitiveTwinInputError("evaluation plan baseline conflicts")
    if cast(datetime, later_snapshot.as_of) > evaluation_as_of:
        raise AdaptiveCognitiveTwinInputError("later source is after evaluation cutoff")
    goal_matches = (
        later_snapshot.goal.goal_source_uuid == active_profile.goal_source_uuid
        and later_snapshot.goal.goal_identity_fingerprint
        == active_profile.goal_identity_fingerprint
    )
    state = _evaluation_state(later_snapshot=later_snapshot, goal_matches=goal_matches)
    changed_sources, unchanged_sources = _aggregate_source_change(
        activation_fingerprint,
        later_snapshot.source_snapshot_fingerprint,
    )
    return validate_adaptive_evaluation_result(
        Stage15EvaluationResultV1(
            contract_version=ADAPTIVE_CONTRACT_VERSION,
            profile_id=active_profile.profile_id,
            profile_fingerprint=active_profile.profile_fingerprint,
            goal_source_uuid=active_profile.goal_source_uuid,
            goal_identity_fingerprint=active_profile.goal_identity_fingerprint,
            activation_snapshot_fingerprint=activation_fingerprint,
            later_snapshot_fingerprint=later_snapshot.source_snapshot_fingerprint,
            as_of=evaluation_as_of,
            state=state,
            changed_sources=changed_sources,
            unchanged_sources=unchanged_sources,
            caveats=(
                Stage15CaveatV1.EXACT_SOURCE_SNAPSHOT,
                Stage15CaveatV1.OWNER_REVIEW_REQUIRED,
                Stage15CaveatV1.NO_AUTOMATIC_ACTIVATION,
                Stage15CaveatV1.DESCRIPTIVE_NON_CAUSAL,
            ),
        )
    )


AdaptiveProjection = Stage15AdaptiveProjectionV1
EvaluationResult = Stage15EvaluationResultV1
SourceReadinessItem = Stage15SourceReadinessItemV1

build_adaptive_projection = project_adaptive_cognitive_twin
evaluate_adaptive_cognitive_twin = evaluate_adaptive_profile


__all__ = [
    "ADAPTIVE_NON_CAUSAL_PHRASE",
    "ADAPTIVE_PROJECTION_VERSION",
    "AdaptiveProjection",
    "EvaluationResult",
    "SourceReadinessItem",
    "Stage15AdaptiveProjectionV1",
    "Stage15EvaluationResultV1",
    "Stage15EvaluationStateV1",
    "Stage15ProfileDiffV1",
    "Stage15ProfileFieldV1",
    "Stage15ProfileValidityV1",
    "Stage15ProjectionLifecycleStateV1",
    "Stage15ProjectionProvenanceV1",
    "Stage15SourceFamilyV1",
    "Stage15SourceReadinessItemV1",
    "build_adaptive_projection",
    "evaluate_adaptive_cognitive_twin",
    "evaluate_adaptive_profile",
    "project_adaptive_cognitive_twin",
    "validate_adaptive_evaluation_result",
    "validate_adaptive_projection",
]
