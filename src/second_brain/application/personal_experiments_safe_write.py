"""Stage 14B reviewed Safe Write for canonical Personal Experiment records.

This module is the only application boundary that may prepare or publish a
Stage 14 companion record.  It deliberately keeps the read-side records and
the derived evaluator separate: a caller supplies reviewed intent, the
service derives all canonical bindings from a fresh vault snapshot, and an
explicit owner confirmation is required before the existing atomic writer is
called.

There is no automatic capture, provider, network, telemetry, browser storage,
Goal mutation, Goal Progress mutation, or derived-result write in this module.
"""

from __future__ import annotations

import hashlib
import hmac
import re
import secrets
import threading
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from typing import Final, Protocol, cast
from uuid import UUID, uuid7

from second_brain.adapters.vault.frontmatter import parse_front_matter
from second_brain.application.goal_progress import (
    GOAL_PROGRESS_POLICY_FINGERPRINT,
    DefinitionRecordV1,
    ObservationRecordV1,
    validate_definition_chain,
    validate_goal_binding,
    validate_observation_against_definition,
    validate_observation_chain,
)
from second_brain.application.growth import (
    BuildGrowthGoalContext,
    GrowthEngineRequestV1,
    GrowthError,
    GrowthGoalContextV1,
    GrowthGoalIdentityV1,
)
from second_brain.application.personal_experiments import (
    MAX_PERSONAL_EXPERIMENT_OBSERVATIONS,
    MAX_PERSONAL_EXPERIMENT_TEXT_BYTES,
    PERSONAL_EXPERIMENT_DIAGNOSTIC_MESSAGES,
    PERSONAL_EXPERIMENT_POLICY_FINGERPRINT,
    PersonalExperimentBaselineStrategyV1,
    PersonalExperimentDefinitionRecordV1,
    PersonalExperimentDispositionV1,
    PersonalExperimentLifecycleEventV1,
    PersonalExperimentLifecycleRecordV1,
    PersonalExperimentLifecycleValidationV1,
    PersonalExperimentObservationRecordV1,
    PersonalExperimentReassessmentRecordV1,
    PersonalExperimentRecordError,
    PersonalExperimentRecordKindV1,
    PersonalExperimentRecordV1,
    personal_experiment_hash_json,
    validate_personal_experiment_definition_chain,
    validate_personal_experiment_lifecycle_chain,
    validate_personal_experiment_observation_chain,
    validate_personal_experiment_policy,
    validate_personal_experiment_reassessment_chain,
)
from second_brain.application.ports import VaultReader
from second_brain.application.reports import (
    Diagnostic,
    DiagnosticSeverity,
    ScanReport,
    VaultSnapshot,
)
from second_brain.application.self_model import DEFAULT_SELF_MODEL_POLICY, SelfModelPolicy
from second_brain.application.validation import build_report
from second_brain.application.writes import (
    CreateNotePlan,
    CreateStatus,
    WriteReceipt,
    WriteSafetyError,
)
from second_brain.domain.models import NoteType, VaultManifest, parse_rfc3339, parse_uuid7

_HASH_PATTERN: Final[re.Pattern[str]] = re.compile(r"sha256:[0-9a-f]{64}\Z", re.ASCII)
_PLAN_CONTRACT: Final[str] = "personal_experiment_safe_write_plan_v1"
_PLAN_OPERATION: Final[str] = "create"
_MAX_PLAN_BYTES: Final[int] = 128 * 1024
_MAX_OWNER_KEY_BYTES: Final[int] = 256
_MAX_REVIEW_TTL_SECONDS: Final[float] = 3600.0
_MAX_REVIEW_ENTRIES: Final[int] = 1024
_MAX_TITLE_BYTES: Final[int] = 256

PERSONAL_EXPERIMENT_SAFE_WRITE_PLAN_CONTRACT: Final[str] = _PLAN_CONTRACT
PERSONAL_EXPERIMENT_REVIEW_TTL_SECONDS: Final[float] = 300.0
PERSONAL_EXPERIMENT_REVIEW_MAX_ENTRIES: Final[int] = 256
PERSONAL_EXPERIMENT_REVIEW_MAX_PLAN_BYTES: Final[int] = _MAX_PLAN_BYTES

_DEFINITION_DRAFT_FIELDS: Final[frozenset[str]] = frozenset(
    {
        "goal_source_uuid",
        "goal_identity_fingerprint",
        "goal_progress_definition_id",
        "goal_progress_definition_fingerprint",
        "hypothesis",
        "intervention",
        "baseline_strategy",
        "baseline_observation_uuid",
        "baseline_observation_fingerprint",
        "supersedes_definition_id",
        "supersedes_definition_fingerprint",
    }
)
_LIFECYCLE_DRAFT_FIELDS: Final[frozenset[str]] = frozenset(
    {
        "experiment_definition_id",
        "experiment_definition_fingerprint",
        "lifecycle_event",
        "event_at",
        "supersedes_lifecycle_id",
        "supersedes_lifecycle_fingerprint",
    }
)
_OBSERVATION_DRAFT_FIELDS: Final[frozenset[str]] = frozenset(
    {
        "experiment_definition_id",
        "experiment_definition_fingerprint",
        "stage12_observation_id",
        "stage12_observation_fingerprint",
        "supersedes_observation_id",
        "supersedes_observation_fingerprint",
    }
)
_REASSESSMENT_DRAFT_FIELDS: Final[frozenset[str]] = frozenset(
    {
        "experiment_definition_id",
        "experiment_definition_fingerprint",
        "result_fingerprint",
        "evaluation_as_of",
        "evaluation_policy_fingerprint",
        "disposition",
        "rationale",
        "supersedes_reassessment_id",
        "supersedes_reassessment_fingerprint",
    }
)

CallableClock = Callable[[], datetime]
CallableMonotonicClock = Callable[[], float]


class PersonalExperimentSafeWriteError(ValueError):
    """Bounded application error carrying only a public machine code."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


class PersonalExperimentReviewError(RuntimeError):
    """The owner-bound one-time review token is absent, stale or invalid."""

    def __init__(self, code: str = "PERSONAL_EXPERIMENT_SAFE_WRITE_REVIEW_REQUIRED") -> None:
        self.code = code
        super().__init__(code)


@dataclass(frozen=True, slots=True)
class PersonalExperimentDefinitionDraftV1:
    """Reviewed definition intent without application-owned storage fields."""

    goal_source_uuid: UUID | str
    goal_identity_fingerprint: str
    goal_progress_definition_id: UUID | str
    goal_progress_definition_fingerprint: str
    hypothesis: str
    intervention: str
    baseline_strategy: PersonalExperimentBaselineStrategyV1 | str
    baseline_observation_uuid: UUID | str | None = None
    baseline_observation_fingerprint: str | None = None
    supersedes_definition_id: UUID | str | None = None
    supersedes_definition_fingerprint: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "goal_source_uuid", _parse_uuid(self.goal_source_uuid))
        object.__setattr__(
            self,
            "goal_identity_fingerprint",
            _parse_hash(self.goal_identity_fingerprint),
        )
        object.__setattr__(
            self,
            "goal_progress_definition_id",
            _parse_uuid(self.goal_progress_definition_id),
        )
        object.__setattr__(
            self,
            "goal_progress_definition_fingerprint",
            _parse_hash(self.goal_progress_definition_fingerprint),
        )
        object.__setattr__(self, "hypothesis", _parse_text(self.hypothesis))
        object.__setattr__(self, "intervention", _parse_text(self.intervention))
        object.__setattr__(
            self,
            "baseline_strategy",
            _parse_enum(self.baseline_strategy, PersonalExperimentBaselineStrategyV1),
        )
        baseline = _parse_pair(
            self.baseline_observation_uuid,
            self.baseline_observation_fingerprint,
        )
        supersedes = _parse_pair(
            self.supersedes_definition_id,
            self.supersedes_definition_fingerprint,
        )
        strategy = cast(PersonalExperimentBaselineStrategyV1, self.baseline_strategy)
        if strategy is PersonalExperimentBaselineStrategyV1.REVIEWED_PRE_ACTIVATION_OBSERVATION:
            if baseline is None:
                raise PersonalExperimentSafeWriteError("PERSONAL_EXPERIMENT_BASELINE_REQUIRED")
        elif baseline is not None:
            raise PersonalExperimentSafeWriteError("PERSONAL_EXPERIMENT_BASELINE_INVALID")
        object.__setattr__(self, "baseline_observation_uuid", baseline[0] if baseline else None)
        object.__setattr__(
            self,
            "baseline_observation_fingerprint",
            baseline[1] if baseline else None,
        )
        object.__setattr__(self, "supersedes_definition_id", supersedes[0] if supersedes else None)
        object.__setattr__(
            self,
            "supersedes_definition_fingerprint",
            supersedes[1] if supersedes else None,
        )

    @classmethod
    def from_dict(cls, value: object) -> PersonalExperimentDefinitionDraftV1:
        """Parse the exact review shape; raw storage fields are rejected."""

        if not isinstance(value, Mapping) or set(value) != _DEFINITION_DRAFT_FIELDS:
            raise PersonalExperimentSafeWriteError("PERSONAL_EXPERIMENT_INVALID_FIELD")
        try:
            return cls(
                goal_source_uuid=cast(UUID | str, value["goal_source_uuid"]),
                goal_identity_fingerprint=cast(str, value["goal_identity_fingerprint"]),
                goal_progress_definition_id=cast(
                    UUID | str,
                    value["goal_progress_definition_id"],
                ),
                goal_progress_definition_fingerprint=cast(
                    str,
                    value["goal_progress_definition_fingerprint"],
                ),
                hypothesis=cast(str, value["hypothesis"]),
                intervention=cast(str, value["intervention"]),
                baseline_strategy=cast(
                    PersonalExperimentBaselineStrategyV1 | str,
                    value["baseline_strategy"],
                ),
                baseline_observation_uuid=cast(
                    UUID | str | None,
                    value["baseline_observation_uuid"],
                ),
                baseline_observation_fingerprint=cast(
                    str | None,
                    value["baseline_observation_fingerprint"],
                ),
                supersedes_definition_id=cast(
                    UUID | str | None,
                    value["supersedes_definition_id"],
                ),
                supersedes_definition_fingerprint=cast(
                    str | None,
                    value["supersedes_definition_fingerprint"],
                ),
            )
        except PersonalExperimentSafeWriteError:
            raise
        except TypeError, ValueError, UnicodeError, OverflowError:
            raise PersonalExperimentSafeWriteError("PERSONAL_EXPERIMENT_INVALID_FIELD") from None

    def as_dict(self) -> dict[str, object]:
        return {
            "goal_source_uuid": str(self.goal_source_uuid),
            "goal_identity_fingerprint": self.goal_identity_fingerprint,
            "goal_progress_definition_id": str(self.goal_progress_definition_id),
            "goal_progress_definition_fingerprint": self.goal_progress_definition_fingerprint,
            "hypothesis": self.hypothesis,
            "intervention": self.intervention,
            "baseline_strategy": cast(
                PersonalExperimentBaselineStrategyV1,
                self.baseline_strategy,
            ).value,
            "baseline_observation_uuid": (
                str(self.baseline_observation_uuid)
                if self.baseline_observation_uuid is not None
                else None
            ),
            "baseline_observation_fingerprint": self.baseline_observation_fingerprint,
            "supersedes_definition_id": (
                str(self.supersedes_definition_id)
                if self.supersedes_definition_id is not None
                else None
            ),
            "supersedes_definition_fingerprint": self.supersedes_definition_fingerprint,
        }


@dataclass(frozen=True, slots=True)
class PersonalExperimentLifecycleDraftV1:
    """Reviewed lifecycle intent; review time and record id are server-owned."""

    experiment_definition_id: UUID | str
    experiment_definition_fingerprint: str
    lifecycle_event: PersonalExperimentLifecycleEventV1 | str
    event_at: datetime | str
    supersedes_lifecycle_id: UUID | str | None = None
    supersedes_lifecycle_fingerprint: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "experiment_definition_id", _parse_uuid(self.experiment_definition_id)
        )
        object.__setattr__(
            self,
            "experiment_definition_fingerprint",
            _parse_hash(self.experiment_definition_fingerprint),
        )
        object.__setattr__(
            self,
            "lifecycle_event",
            _parse_enum(self.lifecycle_event, PersonalExperimentLifecycleEventV1),
        )
        object.__setattr__(self, "event_at", _parse_utc(self.event_at))
        supersedes = _parse_pair(
            self.supersedes_lifecycle_id,
            self.supersedes_lifecycle_fingerprint,
        )
        object.__setattr__(self, "supersedes_lifecycle_id", supersedes[0] if supersedes else None)
        object.__setattr__(
            self,
            "supersedes_lifecycle_fingerprint",
            supersedes[1] if supersedes else None,
        )

    @classmethod
    def from_dict(cls, value: object) -> PersonalExperimentLifecycleDraftV1:
        if not isinstance(value, Mapping) or set(value) != _LIFECYCLE_DRAFT_FIELDS:
            raise PersonalExperimentSafeWriteError("PERSONAL_EXPERIMENT_INVALID_FIELD")
        try:
            return cls(
                experiment_definition_id=cast(UUID | str, value["experiment_definition_id"]),
                experiment_definition_fingerprint=cast(
                    str,
                    value["experiment_definition_fingerprint"],
                ),
                lifecycle_event=cast(
                    PersonalExperimentLifecycleEventV1 | str,
                    value["lifecycle_event"],
                ),
                event_at=cast(datetime | str, value["event_at"]),
                supersedes_lifecycle_id=cast(
                    UUID | str | None,
                    value["supersedes_lifecycle_id"],
                ),
                supersedes_lifecycle_fingerprint=cast(
                    str | None,
                    value["supersedes_lifecycle_fingerprint"],
                ),
            )
        except PersonalExperimentSafeWriteError:
            raise
        except TypeError, ValueError, UnicodeError, OverflowError:
            raise PersonalExperimentSafeWriteError("PERSONAL_EXPERIMENT_INVALID_FIELD") from None

    def as_dict(self) -> dict[str, object]:
        return {
            "experiment_definition_id": str(self.experiment_definition_id),
            "experiment_definition_fingerprint": self.experiment_definition_fingerprint,
            "lifecycle_event": cast(
                PersonalExperimentLifecycleEventV1,
                self.lifecycle_event,
            ).value,
            "event_at": cast(datetime, self.event_at).isoformat().replace("+00:00", "Z"),
            "supersedes_lifecycle_id": (
                str(self.supersedes_lifecycle_id)
                if self.supersedes_lifecycle_id is not None
                else None
            ),
            "supersedes_lifecycle_fingerprint": self.supersedes_lifecycle_fingerprint,
        }


@dataclass(frozen=True, slots=True)
class PersonalExperimentObservationDraftV1:
    """Reviewed enrollment intent for one exact Stage 12 observation."""

    experiment_definition_id: UUID | str
    experiment_definition_fingerprint: str
    stage12_observation_id: UUID | str
    stage12_observation_fingerprint: str
    supersedes_observation_id: UUID | str | None = None
    supersedes_observation_fingerprint: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "experiment_definition_id", _parse_uuid(self.experiment_definition_id)
        )
        object.__setattr__(
            self,
            "experiment_definition_fingerprint",
            _parse_hash(self.experiment_definition_fingerprint),
        )
        object.__setattr__(self, "stage12_observation_id", _parse_uuid(self.stage12_observation_id))
        object.__setattr__(
            self,
            "stage12_observation_fingerprint",
            _parse_hash(self.stage12_observation_fingerprint),
        )
        supersedes = _parse_pair(
            self.supersedes_observation_id,
            self.supersedes_observation_fingerprint,
        )
        object.__setattr__(self, "supersedes_observation_id", supersedes[0] if supersedes else None)
        object.__setattr__(
            self,
            "supersedes_observation_fingerprint",
            supersedes[1] if supersedes else None,
        )

    @classmethod
    def from_dict(cls, value: object) -> PersonalExperimentObservationDraftV1:
        if not isinstance(value, Mapping) or set(value) != _OBSERVATION_DRAFT_FIELDS:
            raise PersonalExperimentSafeWriteError("PERSONAL_EXPERIMENT_INVALID_FIELD")
        try:
            return cls(
                experiment_definition_id=cast(UUID | str, value["experiment_definition_id"]),
                experiment_definition_fingerprint=cast(
                    str,
                    value["experiment_definition_fingerprint"],
                ),
                stage12_observation_id=cast(UUID | str, value["stage12_observation_id"]),
                stage12_observation_fingerprint=cast(
                    str,
                    value["stage12_observation_fingerprint"],
                ),
                supersedes_observation_id=cast(
                    UUID | str | None,
                    value["supersedes_observation_id"],
                ),
                supersedes_observation_fingerprint=cast(
                    str | None,
                    value["supersedes_observation_fingerprint"],
                ),
            )
        except PersonalExperimentSafeWriteError:
            raise
        except TypeError, ValueError, UnicodeError, OverflowError:
            raise PersonalExperimentSafeWriteError("PERSONAL_EXPERIMENT_INVALID_FIELD") from None

    def as_dict(self) -> dict[str, object]:
        return {
            "experiment_definition_id": str(self.experiment_definition_id),
            "experiment_definition_fingerprint": self.experiment_definition_fingerprint,
            "stage12_observation_id": str(self.stage12_observation_id),
            "stage12_observation_fingerprint": self.stage12_observation_fingerprint,
            "supersedes_observation_id": (
                str(self.supersedes_observation_id)
                if self.supersedes_observation_id is not None
                else None
            ),
            "supersedes_observation_fingerprint": self.supersedes_observation_fingerprint,
        }


@dataclass(frozen=True, slots=True)
class PersonalExperimentReassessmentDraftV1:
    """Reviewed terminal reassessment intent; result bytes are never persisted."""

    experiment_definition_id: UUID | str
    experiment_definition_fingerprint: str
    result_fingerprint: str
    evaluation_as_of: datetime | str
    evaluation_policy_fingerprint: str
    disposition: PersonalExperimentDispositionV1 | str
    rationale: str
    supersedes_reassessment_id: UUID | str | None = None
    supersedes_reassessment_fingerprint: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "experiment_definition_id", _parse_uuid(self.experiment_definition_id)
        )
        object.__setattr__(
            self,
            "experiment_definition_fingerprint",
            _parse_hash(self.experiment_definition_fingerprint),
        )
        object.__setattr__(self, "result_fingerprint", _parse_hash(self.result_fingerprint))
        object.__setattr__(self, "evaluation_as_of", _parse_utc(self.evaluation_as_of))
        object.__setattr__(
            self,
            "evaluation_policy_fingerprint",
            _parse_hash(self.evaluation_policy_fingerprint),
        )
        object.__setattr__(
            self,
            "disposition",
            _parse_enum(self.disposition, PersonalExperimentDispositionV1),
        )
        object.__setattr__(self, "rationale", _parse_text(self.rationale))
        supersedes = _parse_pair(
            self.supersedes_reassessment_id,
            self.supersedes_reassessment_fingerprint,
        )
        object.__setattr__(
            self,
            "supersedes_reassessment_id",
            supersedes[0] if supersedes else None,
        )
        object.__setattr__(
            self,
            "supersedes_reassessment_fingerprint",
            supersedes[1] if supersedes else None,
        )

    @classmethod
    def from_dict(cls, value: object) -> PersonalExperimentReassessmentDraftV1:
        if not isinstance(value, Mapping) or set(value) != _REASSESSMENT_DRAFT_FIELDS:
            raise PersonalExperimentSafeWriteError("PERSONAL_EXPERIMENT_INVALID_FIELD")
        try:
            return cls(
                experiment_definition_id=cast(UUID | str, value["experiment_definition_id"]),
                experiment_definition_fingerprint=cast(
                    str,
                    value["experiment_definition_fingerprint"],
                ),
                result_fingerprint=cast(str, value["result_fingerprint"]),
                evaluation_as_of=cast(datetime | str, value["evaluation_as_of"]),
                evaluation_policy_fingerprint=cast(
                    str,
                    value["evaluation_policy_fingerprint"],
                ),
                disposition=cast(
                    PersonalExperimentDispositionV1 | str,
                    value["disposition"],
                ),
                rationale=cast(str, value["rationale"]),
                supersedes_reassessment_id=cast(
                    UUID | str | None,
                    value["supersedes_reassessment_id"],
                ),
                supersedes_reassessment_fingerprint=cast(
                    str | None,
                    value["supersedes_reassessment_fingerprint"],
                ),
            )
        except PersonalExperimentSafeWriteError:
            raise
        except TypeError, ValueError, UnicodeError, OverflowError:
            raise PersonalExperimentSafeWriteError("PERSONAL_EXPERIMENT_INVALID_FIELD") from None

    def as_dict(self) -> dict[str, object]:
        return {
            "experiment_definition_id": str(self.experiment_definition_id),
            "experiment_definition_fingerprint": self.experiment_definition_fingerprint,
            "result_fingerprint": self.result_fingerprint,
            "evaluation_as_of": cast(datetime, self.evaluation_as_of)
            .isoformat()
            .replace("+00:00", "Z"),
            "evaluation_policy_fingerprint": self.evaluation_policy_fingerprint,
            "disposition": cast(PersonalExperimentDispositionV1, self.disposition).value,
            "rationale": self.rationale,
            "supersedes_reassessment_id": (
                str(self.supersedes_reassessment_id)
                if self.supersedes_reassessment_id is not None
                else None
            ),
            "supersedes_reassessment_fingerprint": self.supersedes_reassessment_fingerprint,
        }


type PersonalExperimentSafeWriteDraftV1 = (
    PersonalExperimentDefinitionDraftV1
    | PersonalExperimentLifecycleDraftV1
    | PersonalExperimentObservationDraftV1
    | PersonalExperimentReassessmentDraftV1
)


@dataclass(frozen=True, slots=True)
class PersonalExperimentSafeWritePlanV1:
    """Immutable dry-run plan bound to exact source topology and bytes."""

    operation: str
    record_kind: PersonalExperimentRecordKindV1
    note_id: UUID
    created: datetime
    title: str
    relative_path: str
    target_root_relative: str
    record: PersonalExperimentRecordV1
    content: str
    source_fingerprint: str
    goal_source_uuid: UUID
    goal_identity_fingerprint: str
    experiment_definition_id: UUID
    experiment_definition_fingerprint: str
    supersedes_record_id: UUID | None
    filesystem_plan: CreateNotePlan
    plan_sha256: str

    def __post_init__(self) -> None:
        if self.operation != _PLAN_OPERATION:
            raise ValueError("Personal Experiment plan operation is invalid")
        kind = _parse_enum(self.record_kind, PersonalExperimentRecordKindV1)
        object.__setattr__(self, "record_kind", kind)
        note_id = _parse_uuid(self.note_id)
        object.__setattr__(self, "note_id", note_id)
        if not isinstance(self.created, datetime) or self.created.tzinfo is None:
            raise ValueError("Personal Experiment plan timestamp is invalid")
        if self.created.utcoffset() is None:
            raise ValueError("Personal Experiment plan timestamp is invalid")
        _validate_bounded_text(self.title, _MAX_TITLE_BYTES, allow_empty=False)
        _validate_relative_text(self.relative_path)
        _validate_relative_text(self.target_root_relative)
        goal_source_uuid = _parse_uuid(self.goal_source_uuid)
        object.__setattr__(self, "goal_source_uuid", goal_source_uuid)
        goal_identity_fingerprint = _parse_hash(self.goal_identity_fingerprint)
        object.__setattr__(self, "goal_identity_fingerprint", goal_identity_fingerprint)
        experiment_definition_id = _parse_uuid(self.experiment_definition_id)
        object.__setattr__(self, "experiment_definition_id", experiment_definition_id)
        experiment_definition_fingerprint = _parse_hash(self.experiment_definition_fingerprint)
        object.__setattr__(
            self,
            "experiment_definition_fingerprint",
            experiment_definition_fingerprint,
        )
        if type(self.record) not in {
            PersonalExperimentDefinitionRecordV1,
            PersonalExperimentLifecycleRecordV1,
            PersonalExperimentObservationRecordV1,
            PersonalExperimentReassessmentRecordV1,
        }:
            raise ValueError("Personal Experiment plan record is invalid")
        expected_kind = _record_kind(self.record)
        if kind is not expected_kind:
            raise ValueError("Personal Experiment plan kind is invalid")
        if self.record.id != note_id:
            raise ValueError("Personal Experiment plan record identity is invalid")
        if self.record.goal_source_uuid != self.goal_source_uuid:
            raise ValueError("Personal Experiment plan Goal identity is invalid")
        if self.record.goal_identity_fingerprint != self.goal_identity_fingerprint:
            raise ValueError("Personal Experiment plan Goal fingerprint is invalid")
        expected_definition_id, expected_definition_fingerprint = _experiment_identity(self.record)
        if self.experiment_definition_id != expected_definition_id:
            raise ValueError("Personal Experiment plan experiment identity is invalid")
        if self.experiment_definition_fingerprint != expected_definition_fingerprint:
            raise ValueError("Personal Experiment plan experiment fingerprint is invalid")
        expected_supersedes = _record_supersedes(self.record)
        if self.supersedes_record_id != expected_supersedes:
            raise ValueError("Personal Experiment plan supersession is invalid")
        if type(self.content) is not str:
            raise ValueError("Personal Experiment plan content is invalid")
        try:
            content_size = len(self.content.encode("utf-8"))
        except UnicodeError:
            raise ValueError("Personal Experiment plan content is invalid") from None
        if content_size > _MAX_PLAN_BYTES:
            raise ValueError("Personal Experiment plan is too large")
        _parse_hash(self.source_fingerprint)
        if type(self.filesystem_plan) is not CreateNotePlan:
            raise ValueError("Personal Experiment filesystem plan is invalid")
        if (
            self.filesystem_plan.note_type is not NoteType.ZETTEL
            or self.filesystem_plan.title != self.title
            or self.filesystem_plan.note_id != note_id
            or self.filesystem_plan.created != self.created
            or self.filesystem_plan.relative_path != self.relative_path
            or self.filesystem_plan.target_root_relative != self.target_root_relative
            or self.filesystem_plan.content != self.content
        ):
            raise ValueError("Personal Experiment filesystem plan is inconsistent")
        _parse_hash(self.plan_sha256)

    @property
    def content_sha256(self) -> str:
        return "sha256:" + hashlib.sha256(self.content.encode("utf-8")).hexdigest()

    def as_dict(self) -> dict[str, object]:
        return {
            "operation": self.operation,
            "record_kind": self.record_kind.value,
            "title": self.title,
            "relative_path": self.relative_path,
            "target_root_relative": self.target_root_relative,
            "id": str(self.note_id),
            "created": self.created.isoformat(timespec="seconds"),
            "payload": self.record.as_dict(),
            "source_fingerprint": self.source_fingerprint,
            "goal_source_uuid": str(self.goal_source_uuid),
            "goal_identity_fingerprint": self.goal_identity_fingerprint,
            "experiment_definition_id": str(self.experiment_definition_id),
            "experiment_definition_fingerprint": self.experiment_definition_fingerprint,
            "supersedes_record_id": (
                str(self.supersedes_record_id) if self.supersedes_record_id is not None else None
            ),
            "content": self.content,
            "content_sha256": self.content_sha256,
            "plan_sha256": self.plan_sha256,
        }


@dataclass(frozen=True, slots=True)
class PersonalExperimentSafeWriteResult:
    """Bounded result for prepare or explicit apply."""

    status: CreateStatus
    plan: PersonalExperimentSafeWritePlanV1 | None = None
    diagnostics: tuple[Diagnostic, ...] = ()
    validation_report: ScanReport | None = None
    rollback_succeeded: bool | None = None
    apply_requested: bool = False
    receipt: WriteReceipt | None = None

    @property
    def applied(self) -> bool:
        return self.status in {CreateStatus.CREATED, CreateStatus.ROLLED_BACK}

    @property
    def successful(self) -> bool:
        return self.status in {CreateStatus.DRY_RUN, CreateStatus.CREATED}

    def as_dict(self) -> dict[str, object]:
        return {
            "status": self.status.value,
            "mode": "apply" if self.apply_requested else "dry-run",
            "applied": self.applied,
            "plan": self.plan.as_dict() if self.plan is not None else None,
            "diagnostics": [item.as_dict() for item in self.diagnostics],
            "rollback": (
                "succeeded"
                if self.rollback_succeeded is True
                else "failed"
                if self.rollback_succeeded is False
                else "not-needed"
            ),
            **(
                {"post_write_validation": self.validation_report.as_dict()}
                if self.validation_report is not None
                else {}
            ),
        }


class PersonalExperimentNoteWriter(Protocol):
    """Dedicated adapter seam for one atomic managed-note create."""

    def prepare_personal_experiment(
        self,
        manifest: VaultManifest,
        record: PersonalExperimentRecordV1,
        title: str,
        note_id: UUID,
        created: datetime,
    ) -> CreateNotePlan:
        """Prepare bytes without mutating the vault."""
        ...

    def write(self, plan: CreateNotePlan) -> WriteReceipt:
        """Publish exactly one prepared plan without overwrite."""
        ...

    def rollback(self, receipt: WriteReceipt) -> bool:
        """Remove only the receipt-matching publication."""
        ...


@dataclass(frozen=True, slots=True)
class _SnapshotReader:
    snapshot: VaultSnapshot

    def scan(self) -> VaultSnapshot:
        return self.snapshot


@dataclass(frozen=True, slots=True)
class _CurrentVaultState:
    report: ScanReport
    goal_context: GrowthGoalContextV1 | None
    source_fingerprint: str | None

    @property
    def goals(self) -> tuple[GrowthGoalIdentityV1, ...]:
        return () if self.goal_context is None else self.goal_context.goals

    @property
    def available(self) -> bool:
        return (
            self.report.manifest is not None
            and self.report.error_count == 0
            and self.goal_context is not None
            and self.source_fingerprint is not None
        )


@dataclass(frozen=True, slots=True)
class PersonalExperimentSafeWrite:
    """Prepare and explicitly apply one reviewed Stage 14 record."""

    reader: VaultReader
    writer: PersonalExperimentNoteWriter
    clock: CallableClock = lambda: datetime.now(UTC)
    self_model_policy: SelfModelPolicy = DEFAULT_SELF_MODEL_POLICY

    def prepare(
        self,
        draft: PersonalExperimentSafeWriteDraftV1,
    ) -> PersonalExperimentSafeWriteResult:
        """Create a dry-run plan; no filesystem write is reachable here."""

        if type(draft) not in {
            PersonalExperimentDefinitionDraftV1,
            PersonalExperimentLifecycleDraftV1,
            PersonalExperimentObservationDraftV1,
            PersonalExperimentReassessmentDraftV1,
        }:
            return _rejected("PERSONAL_EXPERIMENT_REQUEST_INVALID")
        try:
            state = self._read_current_state()
        except PersonalExperimentSafeWriteError as exc:
            return _rejected(exc.code)
        if not state.available:
            return _rejected(
                "PERSONAL_EXPERIMENT_SOURCE_UNAVAILABLE",
                validation_report=state.report,
            )
        try:
            validate_personal_experiment_policy()
            record = self._build_candidate(state, draft)
            _validate_candidate(state, record)
            created = _clock_utc(self.clock)
            filesystem_plan = self.writer.prepare_personal_experiment(
                cast(VaultManifest, state.report.manifest),
                record,
                _title_for_record(record),
                cast(UUID, record.id),
                created,
            )
            _validate_filesystem_plan(filesystem_plan, record)
            plan = PersonalExperimentSafeWritePlanV1(
                operation=_PLAN_OPERATION,
                record_kind=_record_kind(record),
                note_id=cast(UUID, record.id),
                created=created,
                title=filesystem_plan.title,
                relative_path=filesystem_plan.relative_path,
                target_root_relative=filesystem_plan.target_root_relative,
                record=record,
                content=filesystem_plan.content,
                source_fingerprint=cast(str, state.source_fingerprint),
                goal_source_uuid=cast(UUID, record.goal_source_uuid),
                goal_identity_fingerprint=record.goal_identity_fingerprint,
                experiment_definition_id=_experiment_identity(record)[0],
                experiment_definition_fingerprint=_experiment_identity(record)[1],
                supersedes_record_id=_record_supersedes(record),
                filesystem_plan=filesystem_plan,
                plan_sha256="sha256:" + "0" * 64,
            )
            return PersonalExperimentSafeWriteResult(
                CreateStatus.DRY_RUN,
                plan=replace(plan, plan_sha256=_compute_plan_sha256(plan)),
            )
        except PersonalExperimentSafeWriteError as exc:
            return _rejected(exc.code)
        except PersonalExperimentRecordError as exc:
            return _rejected(exc.code)
        except WriteSafetyError as exc:
            return _rejected(_writer_error_code(exc.code), path=exc.path)
        except OSError, TypeError, ValueError, UnicodeError, OverflowError:
            return _rejected("PERSONAL_EXPERIMENT_INTERNAL")
        except Exception:
            return _rejected("PERSONAL_EXPERIMENT_INTERNAL")

    def prepare_definition(
        self,
        draft: PersonalExperimentDefinitionDraftV1,
    ) -> PersonalExperimentSafeWriteResult:
        return self.prepare(draft)

    def prepare_lifecycle(
        self,
        draft: PersonalExperimentLifecycleDraftV1,
    ) -> PersonalExperimentSafeWriteResult:
        return self.prepare(draft)

    def prepare_observation(
        self,
        draft: PersonalExperimentObservationDraftV1,
    ) -> PersonalExperimentSafeWriteResult:
        return self.prepare(draft)

    def prepare_reassessment(
        self,
        draft: PersonalExperimentReassessmentDraftV1,
    ) -> PersonalExperimentSafeWriteResult:
        return self.prepare(draft)

    def apply(
        self,
        plan: PersonalExperimentSafeWritePlanV1,
        accepted_plan_sha256: str,
    ) -> PersonalExperimentSafeWriteResult:
        """Apply only exact reviewed bytes after a fresh source read."""

        if type(plan) is not PersonalExperimentSafeWritePlanV1 or not _valid_hash(
            accepted_plan_sha256
        ):
            return _rejected(
                "PERSONAL_EXPERIMENT_SAFE_WRITE_REVIEW_REQUIRED",
                plan=plan if type(plan) is PersonalExperimentSafeWritePlanV1 else None,
                apply=True,
            )
        try:
            _revalidate_plan_shape(plan)
            expected_hash = _compute_plan_sha256(plan)
        except Exception:
            return _rejected(
                "PERSONAL_EXPERIMENT_SAFE_WRITE_REVIEW_REQUIRED",
                plan=plan,
                apply=True,
            )
        if (
            not _valid_hash(plan.plan_sha256)
            or not hmac.compare_digest(expected_hash, plan.plan_sha256)
            or not hmac.compare_digest(expected_hash, accepted_plan_sha256)
        ):
            return _rejected(
                "PERSONAL_EXPERIMENT_SAFE_WRITE_REVIEW_REQUIRED",
                plan=plan,
                apply=True,
            )
        try:
            state = self._read_current_state()
        except PersonalExperimentSafeWriteError as exc:
            return _rejected(exc.code, plan=plan, apply=True)
        if _plan_target_conflict(state.report, plan):
            return _rejected(
                "CREATE_TARGET_EXISTS",
                plan=plan,
                apply=True,
                path=plan.relative_path,
            )
        if not state.available:
            return _rejected(
                "PERSONAL_EXPERIMENT_SOURCE_UNAVAILABLE",
                plan=plan,
                apply=True,
                validation_report=state.report,
            )
        if state.source_fingerprint != plan.source_fingerprint:
            return _rejected(
                "PERSONAL_EXPERIMENT_VAULT_CHANGED",
                plan=plan,
                apply=True,
                path=plan.relative_path,
            )
        try:
            validate_personal_experiment_policy()
            _validate_candidate(state, plan.record)
            _validate_filesystem_plan(plan.filesystem_plan, plan.record)
        except PersonalExperimentSafeWriteError as exc:
            return _rejected(exc.code, plan=plan, apply=True, path=plan.relative_path)
        except PersonalExperimentRecordError, WriteSafetyError, ValueError, TypeError:
            return _rejected(
                "PERSONAL_EXPERIMENT_SAFE_WRITE_REVIEW_REQUIRED",
                plan=plan,
                apply=True,
                path=plan.relative_path,
            )
        try:
            receipt = self.writer.write(plan.filesystem_plan)
        except WriteSafetyError as exc:
            return _rejected(
                _writer_error_code(exc.code),
                plan=plan,
                apply=True,
                path=exc.path or plan.relative_path,
            )
        except OSError, TypeError, ValueError, UnicodeError:
            return _rejected(
                "PERSONAL_EXPERIMENT_INTERNAL",
                plan=plan,
                apply=True,
                path=plan.relative_path,
            )

        try:
            post_report = build_report(self.reader.scan())
        except Exception:
            return self._rollback_after_write(plan, receipt, None)
        if not _post_write_is_valid(post_report, plan):
            return self._rollback_after_write(plan, receipt, post_report)
        return PersonalExperimentSafeWriteResult(
            CreateStatus.CREATED,
            plan=plan,
            validation_report=post_report,
            receipt=receipt,
            apply_requested=True,
        )

    def apply_reviewed(
        self,
        owner_key: str,
        review_token: str,
        accepted_plan_sha256: str,
        review_store: PersonalExperimentReviewStore,
        *,
        confirmed: bool = False,
    ) -> PersonalExperimentSafeWriteResult:
        """Consume one owner-bound token and apply its exact plan once."""

        if type(confirmed) is not bool or not confirmed:
            return _rejected(
                "PERSONAL_EXPERIMENT_SAFE_WRITE_REVIEW_REQUIRED",
                apply=True,
            )
        try:
            plan = review_store.consume(owner_key, review_token, accepted_plan_sha256)
        except PersonalExperimentReviewError as exc:
            return _rejected(exc.code, apply=True)
        return self.apply(plan, accepted_plan_sha256)

    def _read_current_state(self) -> _CurrentVaultState:
        try:
            snapshot = self.reader.scan()
            report = build_report(snapshot)
        except Exception:
            raise PersonalExperimentSafeWriteError(
                "PERSONAL_EXPERIMENT_SOURCE_UNAVAILABLE"
            ) from None
        if type(report) is not ScanReport:
            raise PersonalExperimentSafeWriteError("PERSONAL_EXPERIMENT_SOURCE_UNAVAILABLE")
        context: GrowthGoalContextV1 | None = None
        if report.manifest is not None and report.error_count == 0:
            try:
                context = BuildGrowthGoalContext(
                    _SnapshotReader(snapshot),
                    policy=self.self_model_policy,
                    clock=self.clock,
                ).execute(GrowthEngineRequestV1())
            except GrowthError:
                context = None
            except Exception:
                context = None
        source_fingerprint = (
            _current_source_fingerprint(report, context) if context is not None else None
        )
        return _CurrentVaultState(report, context, source_fingerprint)

    def _build_candidate(
        self,
        state: _CurrentVaultState,
        draft: PersonalExperimentSafeWriteDraftV1,
    ) -> PersonalExperimentRecordV1:
        record_id = uuid7()
        review_time = _clock_utc(self.clock)
        if type(draft) is PersonalExperimentDefinitionDraftV1:
            stage12_definition = _active_stage12_definition_for(
                state,
                draft.goal_source_uuid,
                draft.goal_identity_fingerprint,
                expected_id=draft.goal_progress_definition_id,
                expected_fingerprint=draft.goal_progress_definition_fingerprint,
            )
            _validate_definition_baseline(state, draft, stage12_definition)
            return PersonalExperimentDefinitionRecordV1(
                id=record_id,
                experiment_policy_fingerprint=PERSONAL_EXPERIMENT_POLICY_FINGERPRINT,
                goal_source_uuid=draft.goal_source_uuid,
                goal_identity_fingerprint=draft.goal_identity_fingerprint,
                goal_progress_definition_id=stage12_definition.id,
                goal_progress_definition_fingerprint=stage12_definition.definition_fingerprint,
                goal_progress_policy_fingerprint=stage12_definition.goal_progress_policy_fingerprint,
                hypothesis=draft.hypothesis,
                intervention=draft.intervention,
                baseline_strategy=draft.baseline_strategy,
                definition_reviewed_at=review_time,
                baseline_observation_uuid=draft.baseline_observation_uuid,
                baseline_observation_fingerprint=draft.baseline_observation_fingerprint,
                supersedes_definition_id=draft.supersedes_definition_id,
                supersedes_definition_fingerprint=draft.supersedes_definition_fingerprint,
            )
        if type(draft) is PersonalExperimentLifecycleDraftV1:
            definition = _current_experiment_definition(
                state,
                draft.experiment_definition_id,
                draft.experiment_definition_fingerprint,
            )
            return PersonalExperimentLifecycleRecordV1(
                id=record_id,
                experiment_definition_id=definition.id,
                experiment_definition_fingerprint=definition.experiment_definition_fingerprint,
                goal_source_uuid=definition.goal_source_uuid,
                goal_identity_fingerprint=definition.goal_identity_fingerprint,
                lifecycle_event=draft.lifecycle_event,
                event_at=draft.event_at,
                lifecycle_reviewed_at=review_time,
                supersedes_lifecycle_id=draft.supersedes_lifecycle_id,
                supersedes_lifecycle_fingerprint=draft.supersedes_lifecycle_fingerprint,
            )
        if type(draft) is PersonalExperimentObservationDraftV1:
            definition = _current_experiment_definition(
                state,
                draft.experiment_definition_id,
                draft.experiment_definition_fingerprint,
            )
            stage12_observation = _stage12_observation_for(
                state,
                draft.stage12_observation_id,
                draft.stage12_observation_fingerprint,
                definition,
            )
            return PersonalExperimentObservationRecordV1(
                id=record_id,
                experiment_definition_id=definition.id,
                experiment_definition_fingerprint=definition.experiment_definition_fingerprint,
                goal_source_uuid=stage12_observation.goal_source_uuid,
                goal_identity_fingerprint=stage12_observation.goal_identity_fingerprint,
                goal_progress_definition_id=stage12_observation.progress_definition_id,
                goal_progress_definition_fingerprint=stage12_observation.definition_fingerprint,
                goal_progress_policy_fingerprint=stage12_observation.goal_progress_policy_fingerprint,
                stage12_observation_id=stage12_observation.id,
                stage12_observation_fingerprint=stage12_observation.observation_fingerprint,
                observation_reviewed_at=review_time,
                supersedes_observation_id=draft.supersedes_observation_id,
                supersedes_observation_fingerprint=draft.supersedes_observation_fingerprint,
            )
        reassessment = cast(PersonalExperimentReassessmentDraftV1, draft)
        definition = _current_experiment_definition(
            state,
            reassessment.experiment_definition_id,
            reassessment.experiment_definition_fingerprint,
        )
        return PersonalExperimentReassessmentRecordV1(
            id=record_id,
            experiment_definition_id=definition.id,
            experiment_definition_fingerprint=definition.experiment_definition_fingerprint,
            goal_source_uuid=definition.goal_source_uuid,
            goal_identity_fingerprint=definition.goal_identity_fingerprint,
            result_fingerprint=reassessment.result_fingerprint,
            evaluation_as_of=reassessment.evaluation_as_of,
            evaluation_policy_fingerprint=reassessment.evaluation_policy_fingerprint,
            disposition=reassessment.disposition,
            rationale=reassessment.rationale,
            reassessment_reviewed_at=review_time,
            supersedes_reassessment_id=reassessment.supersedes_reassessment_id,
            supersedes_reassessment_fingerprint=reassessment.supersedes_reassessment_fingerprint,
        )

    def _rollback_after_write(
        self,
        plan: PersonalExperimentSafeWritePlanV1,
        receipt: WriteReceipt,
        validation_report: ScanReport | None,
    ) -> PersonalExperimentSafeWriteResult:
        try:
            rollback_succeeded = self.writer.rollback(receipt)
        except Exception:
            rollback_succeeded = False
        diagnostics = [_diagnostic("PERSONAL_EXPERIMENT_SAFE_WRITE_VALIDATION_FAILED")]
        if not rollback_succeeded:
            diagnostics.append(
                _diagnostic(
                    "PERSONAL_EXPERIMENT_ROLLBACK_FAILED",
                    path=plan.relative_path,
                )
            )
        return PersonalExperimentSafeWriteResult(
            CreateStatus.ROLLED_BACK,
            plan=plan,
            diagnostics=tuple(diagnostics),
            validation_report=validation_report,
            rollback_succeeded=rollback_succeeded,
            apply_requested=True,
        )


@dataclass(frozen=True, slots=True)
class _ReviewEntry:
    owner_key: str
    plan: PersonalExperimentSafeWritePlanV1
    expires_at: float


class PersonalExperimentReviewStore:
    """Bounded process-local owner review plans and one-time tokens."""

    def __init__(
        self,
        *,
        ttl_seconds: float = PERSONAL_EXPERIMENT_REVIEW_TTL_SECONDS,
        max_entries: int = PERSONAL_EXPERIMENT_REVIEW_MAX_ENTRIES,
        max_plan_bytes: int = PERSONAL_EXPERIMENT_REVIEW_MAX_PLAN_BYTES,
        clock: CallableMonotonicClock = time.monotonic,
    ) -> None:
        if not 1 <= ttl_seconds <= _MAX_REVIEW_TTL_SECONDS:
            raise ValueError("review ttl is invalid")
        if not 1 <= max_entries <= _MAX_REVIEW_ENTRIES:
            raise ValueError("review entry bound is invalid")
        if not 1 <= max_plan_bytes <= _MAX_PLAN_BYTES:
            raise ValueError("review plan bound is invalid")
        self._ttl_seconds = ttl_seconds
        self._max_entries = max_entries
        self._max_plan_bytes = max_plan_bytes
        self._clock = clock
        self._lock = threading.Lock()
        self._entries: dict[str, _ReviewEntry] = {}

    def _prune(self, now: float) -> None:
        for token, entry in tuple(self._entries.items()):
            if entry.expires_at <= now:
                self._entries.pop(token, None)

    def issue(self, owner_key: str, plan: PersonalExperimentSafeWritePlanV1) -> str:
        _validate_owner_key(owner_key)
        if type(plan) is not PersonalExperimentSafeWritePlanV1:
            raise PersonalExperimentReviewError()
        try:
            if len(plan.content.encode("utf-8")) > self._max_plan_bytes:
                raise PersonalExperimentReviewError("PERSONAL_EXPERIMENT_RESULT_TOO_LARGE")
        except UnicodeError:
            raise PersonalExperimentReviewError("PERSONAL_EXPERIMENT_RESULT_TOO_LARGE") from None
        with self._lock:
            now = self._clock()
            self._prune(now)
            if len(self._entries) >= self._max_entries:
                raise PersonalExperimentReviewError("PERSONAL_EXPERIMENT_REVIEW_STORE_FULL")
            for _ in range(4):
                token = secrets.token_urlsafe(32)
                if token not in self._entries:
                    self._entries[token] = _ReviewEntry(
                        owner_key=owner_key,
                        plan=plan,
                        expires_at=now + self._ttl_seconds,
                    )
                    return token
        raise PersonalExperimentReviewError("PERSONAL_EXPERIMENT_REVIEW_STORE_FULL")

    def consume(
        self,
        owner_key: str,
        token: str,
        accepted_plan_sha256: str,
    ) -> PersonalExperimentSafeWritePlanV1:
        _validate_owner_key(owner_key)
        _validate_review_token(token)
        if not _valid_hash(accepted_plan_sha256):
            raise PersonalExperimentReviewError()
        with self._lock:
            now = self._clock()
            self._prune(now)
            entry = self._entries.get(token)
            if entry is None:
                raise PersonalExperimentReviewError()
            if not hmac.compare_digest(entry.owner_key, owner_key):
                raise PersonalExperimentReviewError()
            if not hmac.compare_digest(entry.plan.plan_sha256, accepted_plan_sha256):
                raise PersonalExperimentReviewError()
            self._entries.pop(token, None)
            return entry.plan


def _validate_candidate(
    state: _CurrentVaultState,
    record: PersonalExperimentRecordV1,
) -> None:
    validate_personal_experiment_policy()
    if record.experiment_policy_fingerprint != PERSONAL_EXPERIMENT_POLICY_FINGERPRINT:
        raise PersonalExperimentSafeWriteError("PERSONAL_EXPERIMENT_POLICY_MISMATCH")
    _validate_goal_reference(state, record.goal_source_uuid, record.goal_identity_fingerprint)
    all_ids = {note.note_id for note in state.report.notes if note.note_id is not None}
    if record.id in all_ids:
        raise PersonalExperimentSafeWriteError("CREATE_TARGET_EXISTS")
    if type(record) is PersonalExperimentDefinitionRecordV1:
        _validate_definition_candidate(state, record)
    elif type(record) is PersonalExperimentLifecycleRecordV1:
        _validate_lifecycle_candidate(state, record)
    elif type(record) is PersonalExperimentObservationRecordV1:
        _validate_observation_candidate(state, record)
    elif type(record) is PersonalExperimentReassessmentRecordV1:
        _validate_reassessment_candidate(state, record)
    else:
        raise PersonalExperimentSafeWriteError("PERSONAL_EXPERIMENT_REQUEST_INVALID")


def _validate_definition_candidate(
    state: _CurrentVaultState,
    candidate: PersonalExperimentDefinitionRecordV1,
) -> None:
    stage12 = _active_stage12_definition_for(
        state,
        candidate.goal_source_uuid,
        candidate.goal_identity_fingerprint,
        expected_id=candidate.goal_progress_definition_id,
        expected_fingerprint=candidate.goal_progress_definition_fingerprint,
    )
    _validate_definition_baseline(state, _draft_from_record(candidate), stage12)
    existing = tuple(
        item
        for item in state.report.personal_experiment_definitions
        if item.goal_source_uuid == candidate.goal_source_uuid
        and item.goal_identity_fingerprint == candidate.goal_identity_fingerprint
        and item.goal_progress_definition_id == candidate.goal_progress_definition_id
        and item.goal_progress_definition_fingerprint
        == candidate.goal_progress_definition_fingerprint
    )
    current = validate_personal_experiment_definition_chain(existing)
    if current.issues:
        raise PersonalExperimentSafeWriteError("PERSONAL_EXPERIMENT_DEFINITION_CONFLICT")
    predecessor = candidate.supersedes_definition_id
    if predecessor is None:
        if current.active_records:
            raise PersonalExperimentSafeWriteError("PERSONAL_EXPERIMENT_DEFINITION_CONFLICT")
    else:
        if len(current.active_records) != 1 or current.active_records[0].id != predecessor:
            raise PersonalExperimentSafeWriteError("PERSONAL_EXPERIMENT_DEFINITION_STALE")
        previous = cast(PersonalExperimentDefinitionRecordV1, current.active_records[0])
        lifecycle = _lifecycle_for(state, previous.id, previous.experiment_definition_fingerprint)
        if lifecycle.state != "planned" or lifecycle.issues:
            raise PersonalExperimentSafeWriteError("PERSONAL_EXPERIMENT_DEFINITION_STALE")
    combined = validate_personal_experiment_definition_chain((*existing, candidate))
    if combined.issues or len(combined.active_records) != 1:
        raise PersonalExperimentSafeWriteError("PERSONAL_EXPERIMENT_DEFINITION_CONFLICT")


def _validate_lifecycle_candidate(
    state: _CurrentVaultState,
    candidate: PersonalExperimentLifecycleRecordV1,
) -> None:
    definition = _current_experiment_definition(
        state,
        candidate.experiment_definition_id,
        candidate.experiment_definition_fingerprint,
    )
    if candidate.goal_source_uuid != definition.goal_source_uuid:
        raise PersonalExperimentSafeWriteError("PERSONAL_EXPERIMENT_BINDING_MISMATCH")
    current = _lifecycle_for(state, definition.id, definition.experiment_definition_fingerprint)
    if current.issues or current.state == "invalid":
        raise PersonalExperimentSafeWriteError("PERSONAL_EXPERIMENT_LIFECYCLE_CONFLICT")
    predecessor = candidate.supersedes_lifecycle_id
    event = cast(PersonalExperimentLifecycleEventV1, candidate.lifecycle_event)
    if predecessor is None:
        if event is PersonalExperimentLifecycleEventV1.ACTIVATION:
            if current.state != "planned":
                raise PersonalExperimentSafeWriteError("PERSONAL_EXPERIMENT_LIFECYCLE_CONFLICT")
            candidate_event_at = cast(datetime, candidate.event_at)
            if (
                cast(PersonalExperimentBaselineStrategyV1, definition.baseline_strategy)
                is PersonalExperimentBaselineStrategyV1.REVIEWED_PRE_ACTIVATION_OBSERVATION
            ):
                if (
                    definition.baseline_observation_uuid is None
                    or definition.baseline_observation_fingerprint is None
                ):
                    raise PersonalExperimentSafeWriteError("PERSONAL_EXPERIMENT_BASELINE_INVALID")
                baseline = _exact_stage12_observation(
                    state,
                    definition.baseline_observation_uuid,
                    definition.baseline_observation_fingerprint,
                )
                if (
                    baseline.observed_at == "unknown"
                    or cast(datetime, baseline.observed_at) >= candidate_event_at
                ):
                    raise PersonalExperimentSafeWriteError("PERSONAL_EXPERIMENT_BASELINE_INVALID")
            _reject_other_active_experiment(state, definition)
        else:
            if current.state != "active":
                raise PersonalExperimentSafeWriteError("PERSONAL_EXPERIMENT_LIFECYCLE_REQUIRED")
            activation = current.activation
            if activation is None or cast(datetime, candidate.event_at) <= cast(
                datetime,
                activation.event_at,
            ):
                raise PersonalExperimentSafeWriteError("PERSONAL_EXPERIMENT_LIFECYCLE_INVALID")
    else:
        previous = next(
            (
                item
                for item in current.active_records
                if item.id == predecessor
                and item.lifecycle_fingerprint == candidate.supersedes_lifecycle_fingerprint
            ),
            None,
        )
        if previous is None:
            raise PersonalExperimentSafeWriteError("PERSONAL_EXPERIMENT_LIFECYCLE_STALE")
        if previous.lifecycle_event is not event:
            raise PersonalExperimentSafeWriteError("PERSONAL_EXPERIMENT_LIFECYCLE_INVALID")
        if event is PersonalExperimentLifecycleEventV1.ACTIVATION:
            _reject_other_active_experiment(state, definition)
    # ``current.active_records`` intentionally excludes superseded history.  A
    # correction plan must still retain the rest of the active lifecycle facts.
    all_existing = tuple(
        item
        for item in state.report.personal_experiment_lifecycles
        if item.experiment_definition_id == definition.id
        and item.experiment_definition_fingerprint == definition.experiment_definition_fingerprint
    )
    combined = validate_personal_experiment_lifecycle_chain((*all_existing, candidate))
    if combined.issues or combined.state == "invalid":
        raise PersonalExperimentSafeWriteError("PERSONAL_EXPERIMENT_LIFECYCLE_CONFLICT")


def _validate_observation_candidate(
    state: _CurrentVaultState,
    candidate: PersonalExperimentObservationRecordV1,
) -> None:
    definition = _current_experiment_definition(
        state,
        candidate.experiment_definition_id,
        candidate.experiment_definition_fingerprint,
    )
    lifecycle = _lifecycle_for(state, definition.id, definition.experiment_definition_fingerprint)
    if lifecycle.issues or lifecycle.state not in {"active", "completed", "cancelled"}:
        raise PersonalExperimentSafeWriteError("PERSONAL_EXPERIMENT_LIFECYCLE_REQUIRED")
    if lifecycle.activation is None:
        raise PersonalExperimentSafeWriteError("PERSONAL_EXPERIMENT_LIFECYCLE_REQUIRED")
    stage12 = _stage12_observation_for(
        state,
        candidate.stage12_observation_id,
        candidate.stage12_observation_fingerprint,
        definition,
    )
    if stage12.observed_at == "unknown":
        raise PersonalExperimentSafeWriteError("PERSONAL_EXPERIMENT_OBSERVATION_TIME_REQUIRED")
    observed_at = cast(datetime, stage12.observed_at)
    activation_at = cast(datetime, lifecycle.activation.event_at)
    terminal_at = cast(datetime, lifecycle.terminal.event_at) if lifecycle.terminal else None
    upper_bound = (
        terminal_at
        if terminal_at is not None
        else cast(datetime, candidate.observation_reviewed_at)
    )
    if observed_at < activation_at or (
        observed_at >= upper_bound if terminal_at is not None else observed_at > upper_bound
    ):
        raise PersonalExperimentSafeWriteError("PERSONAL_EXPERIMENT_OBSERVATION_OUTSIDE_WINDOW")
    existing = tuple(
        item
        for item in state.report.personal_experiment_observations
        if item.experiment_definition_id == definition.id
        and item.experiment_definition_fingerprint == definition.experiment_definition_fingerprint
    )
    if len(existing) >= MAX_PERSONAL_EXPERIMENT_OBSERVATIONS:
        raise PersonalExperimentSafeWriteError("PERSONAL_EXPERIMENT_OBSERVATION_LIMIT_EXCEEDED")
    if lifecycle.state != "active" and candidate.supersedes_observation_id is None:
        raise PersonalExperimentSafeWriteError("PERSONAL_EXPERIMENT_LIFECYCLE_REQUIRED")
    current = validate_personal_experiment_observation_chain(existing)
    if current.issues:
        raise PersonalExperimentSafeWriteError("PERSONAL_EXPERIMENT_OBSERVATION_CONFLICT")
    predecessor = candidate.supersedes_observation_id
    if predecessor is not None:
        previous = next(
            (
                item
                for item in current.active_records
                if type(item) is PersonalExperimentObservationRecordV1 and item.id == predecessor
            ),
            None,
        )
        if (
            previous is None
            or previous.observation_fingerprint != candidate.supersedes_observation_fingerprint
        ):
            raise PersonalExperimentSafeWriteError("PERSONAL_EXPERIMENT_OBSERVATION_STALE")
        previous_observation = previous
        if (
            previous_observation.stage12_observation_id != candidate.stage12_observation_id
            or previous_observation.stage12_observation_fingerprint
            != candidate.stage12_observation_fingerprint
        ):
            raise PersonalExperimentSafeWriteError("PERSONAL_EXPERIMENT_OBSERVATION_INVALID")
    combined = validate_personal_experiment_observation_chain((*existing, candidate))
    if combined.issues:
        raise PersonalExperimentSafeWriteError("PERSONAL_EXPERIMENT_OBSERVATION_CONFLICT")


def _validate_reassessment_candidate(
    state: _CurrentVaultState,
    candidate: PersonalExperimentReassessmentRecordV1,
) -> None:
    if candidate.evaluation_policy_fingerprint != PERSONAL_EXPERIMENT_POLICY_FINGERPRINT:
        raise PersonalExperimentSafeWriteError("PERSONAL_EXPERIMENT_POLICY_MISMATCH")
    definition = _current_experiment_definition(
        state,
        candidate.experiment_definition_id,
        candidate.experiment_definition_fingerprint,
    )
    lifecycle = _lifecycle_for(state, definition.id, definition.experiment_definition_fingerprint)
    if lifecycle.issues or lifecycle.state not in {"completed", "cancelled"}:
        raise PersonalExperimentSafeWriteError("PERSONAL_EXPERIMENT_TERMINAL_REQUIRED")
    if lifecycle.activation is None or lifecycle.terminal is None:
        raise PersonalExperimentSafeWriteError("PERSONAL_EXPERIMENT_TERMINAL_REQUIRED")
    as_of = cast(datetime, candidate.evaluation_as_of)
    if as_of < cast(datetime, lifecycle.activation.event_at) or as_of > cast(
        datetime,
        lifecycle.terminal.event_at,
    ):
        raise PersonalExperimentSafeWriteError("PERSONAL_EXPERIMENT_EVALUATION_WINDOW_INVALID")
    existing = tuple(
        item
        for item in state.report.personal_experiment_reassessments
        if item.experiment_definition_id == definition.id
        and item.experiment_definition_fingerprint == definition.experiment_definition_fingerprint
    )
    current = validate_personal_experiment_reassessment_chain(existing)
    if current.issues:
        raise PersonalExperimentSafeWriteError("PERSONAL_EXPERIMENT_REASSESSMENT_CONFLICT")
    predecessor = candidate.supersedes_reassessment_id
    if predecessor is None:
        if current.active_records:
            raise PersonalExperimentSafeWriteError("PERSONAL_EXPERIMENT_REASSESSMENT_CONFLICT")
    elif len(current.active_records) != 1 or current.active_records[0].id != predecessor:
        raise PersonalExperimentSafeWriteError("PERSONAL_EXPERIMENT_REASSESSMENT_STALE")
    combined = validate_personal_experiment_reassessment_chain((*existing, candidate))
    if combined.issues or len(combined.active_records) != 1:
        raise PersonalExperimentSafeWriteError("PERSONAL_EXPERIMENT_REASSESSMENT_CONFLICT")


def _validate_goal_reference(
    state: _CurrentVaultState,
    source_uuid: UUID | str,
    identity_fingerprint: str,
) -> None:
    binding = validate_goal_binding(
        source_uuid,
        identity_fingerprint,
        current_goals=state.goals,
    )
    if binding.exact_current:
        return
    code_by_state = {
        "source_changed": "PERSONAL_EXPERIMENT_GOAL_SOURCE_CHANGED",
        "ambiguous": "PERSONAL_EXPERIMENT_GOAL_AMBIGUOUS",
        "non_current": "PERSONAL_EXPERIMENT_GOAL_REQUIRED",
        "source_missing": "PERSONAL_EXPERIMENT_GOAL_REQUIRED",
    }
    raise PersonalExperimentSafeWriteError(
        code_by_state.get(binding.state.value, "PERSONAL_EXPERIMENT_GOAL_REQUIRED")
    )


def _active_stage12_definition_for(
    state: _CurrentVaultState,
    source_uuid: UUID | str,
    identity_fingerprint: str,
    *,
    expected_id: UUID | str,
    expected_fingerprint: str,
) -> DefinitionRecordV1:
    _validate_goal_reference(state, source_uuid, identity_fingerprint)
    existing = tuple(
        item
        for item in state.report.goal_progress_definitions
        if item.goal_source_uuid == source_uuid
        and item.goal_identity_fingerprint == identity_fingerprint
    )
    if not existing:
        raise PersonalExperimentSafeWriteError("PERSONAL_EXPERIMENT_STAGE12_DEFINITION_REQUIRED")
    chain = validate_definition_chain(existing)
    if chain.issues or len(chain.active_records) != 1:
        raise PersonalExperimentSafeWriteError("PERSONAL_EXPERIMENT_STAGE12_DEFINITION_CONFLICT")
    active = cast(DefinitionRecordV1, chain.active_records[0])
    if active.id != expected_id or active.definition_fingerprint != expected_fingerprint:
        raise PersonalExperimentSafeWriteError("PERSONAL_EXPERIMENT_STAGE12_DEFINITION_CHANGED")
    if active.goal_progress_policy_fingerprint != GOAL_PROGRESS_POLICY_FINGERPRINT:
        raise PersonalExperimentSafeWriteError("PERSONAL_EXPERIMENT_POLICY_MISMATCH")
    return active


def _validate_definition_baseline(
    state: _CurrentVaultState,
    draft: PersonalExperimentDefinitionDraftV1,
    stage12_definition: DefinitionRecordV1,
) -> None:
    strategy = cast(PersonalExperimentBaselineStrategyV1, draft.baseline_strategy)
    if strategy is PersonalExperimentBaselineStrategyV1.STAGE12_DEFINITION_EXPLICIT:
        if stage12_definition.baseline is None:
            raise PersonalExperimentSafeWriteError("PERSONAL_EXPERIMENT_BASELINE_REQUIRED")
        return
    if draft.baseline_observation_uuid is None or draft.baseline_observation_fingerprint is None:
        raise PersonalExperimentSafeWriteError("PERSONAL_EXPERIMENT_BASELINE_REQUIRED")
    observation = _exact_stage12_observation(
        state,
        cast(UUID, draft.baseline_observation_uuid),
        draft.baseline_observation_fingerprint,
    )
    if observation.progress_definition_id != stage12_definition.id:
        raise PersonalExperimentSafeWriteError("PERSONAL_EXPERIMENT_BASELINE_INVALID")
    if observation.definition_fingerprint != stage12_definition.definition_fingerprint:
        raise PersonalExperimentSafeWriteError("PERSONAL_EXPERIMENT_BASELINE_INVALID")
    if validate_observation_against_definition(observation, stage12_definition):
        raise PersonalExperimentSafeWriteError("PERSONAL_EXPERIMENT_BASELINE_INVALID")
    if observation.observed_at == "unknown":
        raise PersonalExperimentSafeWriteError("PERSONAL_EXPERIMENT_BASELINE_TIME_REQUIRED")
    source_chain = validate_observation_chain(
        tuple(
            item
            for item in state.report.goal_progress_observations
            if item.goal_source_uuid == stage12_definition.goal_source_uuid
            and item.goal_identity_fingerprint == stage12_definition.goal_identity_fingerprint
            and item.progress_definition_id == stage12_definition.id
        )
    )
    if source_chain.issues or observation.id not in {
        item.id for item in source_chain.active_records
    }:
        raise PersonalExperimentSafeWriteError("PERSONAL_EXPERIMENT_BASELINE_INVALID")


def _current_experiment_definition(
    state: _CurrentVaultState,
    definition_id: UUID | str,
    definition_fingerprint: str,
) -> PersonalExperimentDefinitionRecordV1:
    matches = tuple(
        item for item in state.report.personal_experiment_definitions if item.id == definition_id
    )
    if not matches:
        raise PersonalExperimentSafeWriteError("PERSONAL_EXPERIMENT_DEFINITION_MISSING")
    if len(matches) != 1:
        raise PersonalExperimentSafeWriteError("PERSONAL_EXPERIMENT_DEFINITION_AMBIGUOUS")
    definition = matches[0]
    if definition.experiment_definition_fingerprint != definition_fingerprint:
        raise PersonalExperimentSafeWriteError("PERSONAL_EXPERIMENT_DEFINITION_CHANGED")
    group = tuple(
        item
        for item in state.report.personal_experiment_definitions
        if item.goal_source_uuid == definition.goal_source_uuid
        and item.goal_identity_fingerprint == definition.goal_identity_fingerprint
        and item.goal_progress_definition_id == definition.goal_progress_definition_id
        and item.goal_progress_definition_fingerprint
        == definition.goal_progress_definition_fingerprint
    )
    chain = validate_personal_experiment_definition_chain(group)
    if chain.issues or len(chain.active_records) != 1:
        raise PersonalExperimentSafeWriteError("PERSONAL_EXPERIMENT_DEFINITION_CONFLICT")
    if chain.active_records[0].id != definition.id:
        raise PersonalExperimentSafeWriteError("PERSONAL_EXPERIMENT_DEFINITION_STALE")
    _active_stage12_definition_for(
        state,
        definition.goal_source_uuid,
        definition.goal_identity_fingerprint,
        expected_id=definition.goal_progress_definition_id,
        expected_fingerprint=definition.goal_progress_definition_fingerprint,
    )
    return definition


def _lifecycle_for(
    state: _CurrentVaultState,
    definition_id: UUID | str,
    definition_fingerprint: str,
) -> PersonalExperimentLifecycleValidationV1:
    records = tuple(
        item
        for item in state.report.personal_experiment_lifecycles
        if item.experiment_definition_id == definition_id
        and item.experiment_definition_fingerprint == definition_fingerprint
    )
    if not records:
        # The read-side validator uses this same state for an empty lifecycle;
        # materialize a stable planned projection with the requested identity.
        return _planned_lifecycle(definition_id, definition_fingerprint)
    return validate_personal_experiment_lifecycle_chain(records)


def _planned_lifecycle(
    definition_id: UUID | str,
    definition_fingerprint: str,
) -> PersonalExperimentLifecycleValidationV1:
    definition_id = _parse_uuid(definition_id)
    return PersonalExperimentLifecycleValidationV1(
        definition_id,
        definition_fingerprint,
        "planned",
        None,
        None,
        (),
        (),
    )


def _stage12_observation_for(
    state: _CurrentVaultState,
    observation_id: UUID | str,
    observation_fingerprint: str,
    definition: PersonalExperimentDefinitionRecordV1,
) -> ObservationRecordV1:
    observation = _exact_stage12_observation(state, observation_id, observation_fingerprint)
    if (
        observation.goal_source_uuid != definition.goal_source_uuid
        or observation.goal_identity_fingerprint != definition.goal_identity_fingerprint
        or observation.progress_definition_id != definition.goal_progress_definition_id
        or observation.definition_fingerprint != definition.goal_progress_definition_fingerprint
        or observation.goal_progress_policy_fingerprint
        != definition.goal_progress_policy_fingerprint
    ):
        raise PersonalExperimentSafeWriteError("PERSONAL_EXPERIMENT_SOURCE_CHANGED")
    stage12_def = _active_stage12_definition_for(
        state,
        definition.goal_source_uuid,
        definition.goal_identity_fingerprint,
        expected_id=definition.goal_progress_definition_id,
        expected_fingerprint=definition.goal_progress_definition_fingerprint,
    )
    if validate_observation_against_definition(observation, stage12_def):
        raise PersonalExperimentSafeWriteError("PERSONAL_EXPERIMENT_SOURCE_CHANGED")
    current = validate_observation_chain(
        tuple(
            item
            for item in state.report.goal_progress_observations
            if item.goal_source_uuid == observation.goal_source_uuid
            and item.goal_identity_fingerprint == observation.goal_identity_fingerprint
            and item.progress_definition_id == observation.progress_definition_id
        )
    )
    if current.issues or observation.id not in {item.id for item in current.active_records}:
        raise PersonalExperimentSafeWriteError("PERSONAL_EXPERIMENT_SOURCE_CHANGED")
    return observation


def _exact_stage12_observation(
    state: _CurrentVaultState,
    observation_id: UUID | str,
    observation_fingerprint: str,
) -> ObservationRecordV1:
    matches = tuple(
        item for item in state.report.goal_progress_observations if item.id == observation_id
    )
    if not matches:
        raise PersonalExperimentSafeWriteError("PERSONAL_EXPERIMENT_SOURCE_MISSING")
    if len(matches) != 1:
        raise PersonalExperimentSafeWriteError("PERSONAL_EXPERIMENT_BINDING_MISMATCH")
    observation = matches[0]
    if observation.observation_fingerprint != observation_fingerprint:
        raise PersonalExperimentSafeWriteError("PERSONAL_EXPERIMENT_SOURCE_CHANGED")
    return observation


def _reject_other_active_experiment(
    state: _CurrentVaultState,
    candidate_definition: PersonalExperimentDefinitionRecordV1,
) -> None:
    for definition in state.report.personal_experiment_definitions:
        if (
            definition.id == candidate_definition.id
            or definition.goal_source_uuid != candidate_definition.goal_source_uuid
            or definition.goal_identity_fingerprint
            != candidate_definition.goal_identity_fingerprint
        ):
            continue
        lifecycle = _lifecycle_for(
            state,
            definition.id,
            definition.experiment_definition_fingerprint,
        )
        if lifecycle.issues:
            raise PersonalExperimentSafeWriteError("PERSONAL_EXPERIMENT_LIFECYCLE_CONFLICT")
        if lifecycle.state == "active":
            raise PersonalExperimentSafeWriteError("PERSONAL_EXPERIMENT_ONE_ACTIVE_PER_GOAL")


def _validate_filesystem_plan(
    filesystem_plan: CreateNotePlan,
    record: PersonalExperimentRecordV1,
) -> None:
    if type(filesystem_plan) is not CreateNotePlan:
        raise PersonalExperimentSafeWriteError("PERSONAL_EXPERIMENT_INTERNAL")
    if (
        filesystem_plan.note_type is not NoteType.ZETTEL
        or filesystem_plan.note_id != record.id
        or not filesystem_plan.relative_path
        or not filesystem_plan.target_root_relative
    ):
        raise PersonalExperimentSafeWriteError("PERSONAL_EXPERIMENT_INTERNAL")
    parsed = parse_front_matter(filesystem_plan.content)
    if parsed.error is not None or not parsed.has_front_matter:
        raise PersonalExperimentSafeWriteError("PERSONAL_EXPERIMENT_INTERNAL")
    try:
        from second_brain.application.personal_experiments import parse_personal_experiment_record

        parsed_record = parse_personal_experiment_record(
            parsed.data,
            note_id=filesystem_plan.note_id,
        )
    except Exception:
        raise PersonalExperimentSafeWriteError("PERSONAL_EXPERIMENT_INTERNAL") from None
    if parsed_record != record:
        raise PersonalExperimentSafeWriteError("PERSONAL_EXPERIMENT_INTERNAL")


def _post_write_is_valid(
    report: ScanReport,
    plan: PersonalExperimentSafeWritePlanV1,
) -> bool:
    if report.error_count:
        return False
    matches = [note for note in report.notes if note.relative_path == plan.relative_path]
    if len(matches) != 1:
        return False
    note = matches[0]
    if not (
        note.managed
        and note.note_type is NoteType.ZETTEL
        and note.note_id == plan.note_id
        and note.created == plan.created
    ):
        return False
    parsed_record: PersonalExperimentRecordV1 | None
    if type(plan.record) is PersonalExperimentDefinitionRecordV1:
        parsed_record = note.personal_experiment_definition
    elif type(plan.record) is PersonalExperimentLifecycleRecordV1:
        parsed_record = note.personal_experiment_lifecycle
    elif type(plan.record) is PersonalExperimentObservationRecordV1:
        parsed_record = note.personal_experiment_observation
    else:
        parsed_record = note.personal_experiment_reassessment
    return parsed_record == plan.record


def _plan_target_conflict(report: ScanReport, plan: PersonalExperimentSafeWritePlanV1) -> bool:
    return any(
        note.relative_path == plan.relative_path or note.note_id == plan.note_id
        for note in report.notes
    )


def _revalidate_plan_shape(plan: PersonalExperimentSafeWritePlanV1) -> None:
    PersonalExperimentSafeWritePlanV1(
        operation=plan.operation,
        record_kind=plan.record_kind,
        note_id=plan.note_id,
        created=plan.created,
        title=plan.title,
        relative_path=plan.relative_path,
        target_root_relative=plan.target_root_relative,
        record=plan.record,
        content=plan.content,
        source_fingerprint=plan.source_fingerprint,
        goal_source_uuid=plan.goal_source_uuid,
        goal_identity_fingerprint=plan.goal_identity_fingerprint,
        experiment_definition_id=plan.experiment_definition_id,
        experiment_definition_fingerprint=plan.experiment_definition_fingerprint,
        supersedes_record_id=plan.supersedes_record_id,
        filesystem_plan=plan.filesystem_plan,
        plan_sha256=plan.plan_sha256,
    )


def _current_source_fingerprint(
    report: ScanReport,
    context: GrowthGoalContextV1,
) -> str:
    if report.manifest is None:
        raise ValueError("manifest is unavailable")
    manifest = report.manifest
    personal_records = cast(
        tuple[PersonalExperimentRecordV1, ...],
        (
            *report.personal_experiment_definitions,
            *report.personal_experiment_lifecycles,
            *report.personal_experiment_observations,
            *report.personal_experiment_reassessments,
        ),
    )
    payload = {
        "contract": _PLAN_CONTRACT,
        "manifest": {
            "schema_version": manifest.schema_version,
            "vault_id": str(manifest.vault_id),
            "default_language": manifest.default_language,
            "paths": manifest.paths.as_dict(),
            "attachments": {
                "warning_size_bytes": manifest.attachments.warning_size_bytes,
                "max_size_bytes": manifest.attachments.max_size_bytes,
            },
        },
        "growth_policy_fingerprint": context.policy_fingerprint,
        "goals": [goal.as_dict() for goal in context.goals],
        "goal_progress_definitions": [item.as_dict() for item in report.goal_progress_definitions],
        "goal_progress_observations": [
            item.as_dict() for item in report.goal_progress_observations
        ],
        "personal_experiment_records": [item.as_dict() for item in personal_records],
    }
    return personal_experiment_hash_json(payload)


def _compute_plan_sha256(plan: PersonalExperimentSafeWritePlanV1) -> str:
    return personal_experiment_hash_json(
        {
            "contract": _PLAN_CONTRACT,
            "operation": plan.operation,
            "record_kind": plan.record_kind.value,
            "target": {
                "title": plan.title,
                "relative_path": plan.relative_path,
                "target_root_relative": plan.target_root_relative,
            },
            "identity": {
                "id": str(plan.note_id),
                "created": plan.created.isoformat(timespec="seconds"),
                "goal_source_uuid": str(plan.goal_source_uuid),
                "goal_identity_fingerprint": plan.goal_identity_fingerprint,
                "experiment_definition_id": str(plan.experiment_definition_id),
                "experiment_definition_fingerprint": plan.experiment_definition_fingerprint,
                "supersedes_record_id": (
                    str(plan.supersedes_record_id)
                    if plan.supersedes_record_id is not None
                    else None
                ),
            },
            "payload": plan.record.as_dict(),
            "source_fingerprint": plan.source_fingerprint,
            "physical": {
                "note_type": plan.filesystem_plan.note_type.value,
                "note_id": str(plan.filesystem_plan.note_id),
                "created": plan.filesystem_plan.created.isoformat(timespec="seconds"),
                "relative_path": plan.filesystem_plan.relative_path,
                "target_root_relative": plan.filesystem_plan.target_root_relative,
                "content_sha256": plan.content_sha256,
                "content": plan.content,
            },
        }
    )


def _record_kind(record: PersonalExperimentRecordV1) -> PersonalExperimentRecordKindV1:
    if isinstance(record, PersonalExperimentDefinitionRecordV1):
        return PersonalExperimentRecordKindV1.DEFINITION
    if isinstance(record, PersonalExperimentLifecycleRecordV1):
        return PersonalExperimentRecordKindV1.LIFECYCLE
    if isinstance(record, PersonalExperimentObservationRecordV1):
        return PersonalExperimentRecordKindV1.OBSERVATION
    if isinstance(record, PersonalExperimentReassessmentRecordV1):
        return PersonalExperimentRecordKindV1.REASSESSMENT
    raise ValueError("record kind is invalid")


def _experiment_identity(record: PersonalExperimentRecordV1) -> tuple[UUID, str]:
    if isinstance(record, PersonalExperimentDefinitionRecordV1):
        return cast(UUID, record.id), record.experiment_definition_fingerprint
    return (
        cast(UUID, record.experiment_definition_id),
        record.experiment_definition_fingerprint,
    )


def _record_supersedes(record: PersonalExperimentRecordV1) -> UUID | None:
    if isinstance(record, PersonalExperimentDefinitionRecordV1):
        return cast(UUID | None, record.supersedes_definition_id)
    if isinstance(record, PersonalExperimentLifecycleRecordV1):
        return cast(UUID | None, record.supersedes_lifecycle_id)
    if isinstance(record, PersonalExperimentObservationRecordV1):
        return cast(UUID | None, record.supersedes_observation_id)
    return cast(UUID | None, record.supersedes_reassessment_id)


def _title_for_record(record: PersonalExperimentRecordV1) -> str:
    return f"personal-experiment-{_record_kind(record).value}-{record.id}"


def _draft_from_record(
    record: PersonalExperimentDefinitionRecordV1,
) -> PersonalExperimentDefinitionDraftV1:
    return PersonalExperimentDefinitionDraftV1(
        goal_source_uuid=record.goal_source_uuid,
        goal_identity_fingerprint=record.goal_identity_fingerprint,
        goal_progress_definition_id=record.goal_progress_definition_id,
        goal_progress_definition_fingerprint=record.goal_progress_definition_fingerprint,
        hypothesis=record.hypothesis,
        intervention=record.intervention,
        baseline_strategy=record.baseline_strategy,
        baseline_observation_uuid=record.baseline_observation_uuid,
        baseline_observation_fingerprint=record.baseline_observation_fingerprint,
        supersedes_definition_id=record.supersedes_definition_id,
        supersedes_definition_fingerprint=record.supersedes_definition_fingerprint,
    )


def _parse_uuid(value: object) -> UUID:
    try:
        return parse_uuid7(value)
    except TypeError, ValueError, OverflowError:
        raise PersonalExperimentSafeWriteError("PERSONAL_EXPERIMENT_INVALID_FIELD") from None


def _parse_hash(value: object) -> str:
    if type(value) is not str or _HASH_PATTERN.fullmatch(value) is None:
        raise PersonalExperimentSafeWriteError("PERSONAL_EXPERIMENT_INVALID_FIELD")
    return value


def _valid_hash(value: object) -> bool:
    return type(value) is str and _HASH_PATTERN.fullmatch(value) is not None


def _parse_text(value: object) -> str:
    if type(value) is not str or not value:
        raise PersonalExperimentSafeWriteError("PERSONAL_EXPERIMENT_INVALID_FIELD")
    try:
        size = len(value.encode("utf-8"))
    except UnicodeError:
        raise PersonalExperimentSafeWriteError("PERSONAL_EXPERIMENT_INVALID_FIELD") from None
    if size > MAX_PERSONAL_EXPERIMENT_TEXT_BYTES or any(
        ord(char) < 0x20 or ord(char) == 0x7F or 0xD800 <= ord(char) <= 0xDFFF for char in value
    ):
        raise PersonalExperimentSafeWriteError("PERSONAL_EXPERIMENT_INVALID_FIELD")
    return value


def _validate_bounded_text(value: str, max_bytes: int, *, allow_empty: bool) -> None:
    if type(value) is not str or (not allow_empty and not value):
        raise ValueError("bounded text is invalid")
    try:
        size = len(value.encode("utf-8"))
    except UnicodeError:
        raise ValueError("bounded text is invalid") from None
    if size > max_bytes or any(ord(char) < 0x20 or ord(char) == 0x7F for char in value):
        raise ValueError("bounded text is invalid")


def _validate_relative_text(value: str) -> None:
    _validate_bounded_text(value, _MAX_PLAN_BYTES, allow_empty=False)
    if "\\" in value or value.startswith("/") or ".." in value.split("/"):
        raise ValueError("relative path is invalid")


def _parse_utc(value: object) -> datetime:
    try:
        parsed = parse_rfc3339(value)
    except TypeError, ValueError, OverflowError, UnicodeError:
        raise PersonalExperimentSafeWriteError("PERSONAL_EXPERIMENT_INVALID_FIELD") from None
    if parsed.utcoffset() != UTC.utcoffset(parsed):
        raise PersonalExperimentSafeWriteError("PERSONAL_EXPERIMENT_INVALID_FIELD")
    return parsed.astimezone(UTC)


def _clock_utc(clock: CallableClock) -> datetime:
    return _parse_utc(clock())


def _parse_enum(value: object, enum_type: type) -> object:
    if isinstance(value, enum_type):
        return value
    if type(value) is not str:
        raise PersonalExperimentSafeWriteError("PERSONAL_EXPERIMENT_INVALID_FIELD")
    try:
        return enum_type(value)
    except ValueError:
        raise PersonalExperimentSafeWriteError("PERSONAL_EXPERIMENT_INVALID_FIELD") from None


def _parse_pair(
    record_id: UUID | str | None,
    fingerprint: str | None,
) -> tuple[UUID, str] | None:
    if record_id is None and fingerprint is None:
        return None
    if record_id is None or fingerprint is None:
        raise PersonalExperimentSafeWriteError("PERSONAL_EXPERIMENT_INVALID_FIELD")
    return _parse_uuid(record_id), _parse_hash(fingerprint)


def _validate_owner_key(owner_key: object) -> None:
    if type(owner_key) is not str or not owner_key:
        raise PersonalExperimentReviewError()
    try:
        size = len(owner_key.encode("utf-8"))
    except UnicodeError:
        raise PersonalExperimentReviewError() from None
    if size > _MAX_OWNER_KEY_BYTES or any(ord(char) < 0x20 for char in owner_key):
        raise PersonalExperimentReviewError()


def _validate_review_token(token: object) -> None:
    if type(token) is not str or not 1 <= len(token) <= _MAX_OWNER_KEY_BYTES:
        raise PersonalExperimentReviewError()
    if any(ord(char) < 0x21 or ord(char) > 0x7E for char in token):
        raise PersonalExperimentReviewError()


def _diagnostic(code: str, *, path: str | None = None) -> Diagnostic:
    message = PERSONAL_EXPERIMENT_DIAGNOSTIC_MESSAGES.get(
        code, _SAFE_WRITE_MESSAGES.get(code, code)
    )
    return Diagnostic(code, message, DiagnosticSeverity.ERROR, path)


_SAFE_WRITE_MESSAGES: Final[dict[str, str]] = {
    "PERSONAL_EXPERIMENT_REQUEST_INVALID": (
        "Запрос Safe Write личного эксперимента недействителен"
    ),
    "PERSONAL_EXPERIMENT_SOURCE_UNAVAILABLE": (
        "Канонический источник личного эксперимента недоступен"
    ),
    "PERSONAL_EXPERIMENT_VAULT_CHANGED": "Канонический vault изменился после review",
    "PERSONAL_EXPERIMENT_SAFE_WRITE_REVIEW_REQUIRED": "Требуется явное подтверждение владельца",
    "PERSONAL_EXPERIMENT_SAFE_WRITE_VALIDATION_FAILED": "Полная проверка после записи не пройдена",
    "PERSONAL_EXPERIMENT_ROLLBACK_FAILED": "Не удалось безопасно откатить новую запись",
    "PERSONAL_EXPERIMENT_INTERNAL": "Операция Safe Write личного эксперимента не выполнена",
    "CREATE_TARGET_EXISTS": "Целевой файл или идентификатор уже занят",
    "PERSONAL_EXPERIMENT_BASELINE_REQUIRED": "Требуется явно выбранный baseline",
    "PERSONAL_EXPERIMENT_BASELINE_INVALID": "Явно выбранный baseline недействителен",
    "PERSONAL_EXPERIMENT_BASELINE_TIME_REQUIRED": "Для baseline требуется точное время наблюдения",
    "PERSONAL_EXPERIMENT_GOAL_REQUIRED": "Требуется точная текущая Goal",
    "PERSONAL_EXPERIMENT_GOAL_SOURCE_CHANGED": "Точная текущая Goal изменилась",
    "PERSONAL_EXPERIMENT_GOAL_AMBIGUOUS": "Точная текущая Goal неоднозначна",
    "PERSONAL_EXPERIMENT_STAGE12_DEFINITION_REQUIRED": (
        "Требуется точная активная Stage 12 definition"
    ),
    "PERSONAL_EXPERIMENT_STAGE12_DEFINITION_CHANGED": "Точная Stage 12 definition изменилась",
    "PERSONAL_EXPERIMENT_STAGE12_DEFINITION_CONFLICT": (
        "Цепочка Stage 12 definition содержит конфликт"
    ),
    "PERSONAL_EXPERIMENT_DEFINITION_MISSING": "Определение личного эксперимента не найдено",
    "PERSONAL_EXPERIMENT_DEFINITION_CHANGED": "Определение личного эксперимента изменилось",
    "PERSONAL_EXPERIMENT_DEFINITION_STALE": "Определение личного эксперимента устарело",
    "PERSONAL_EXPERIMENT_DEFINITION_CONFLICT": (
        "Цепочка определений личного эксперимента содержит конфликт"
    ),
    "PERSONAL_EXPERIMENT_LIFECYCLE_REQUIRED": "Требуется допустимое состояние жизненного цикла",
    "PERSONAL_EXPERIMENT_LIFECYCLE_INVALID": "Событие жизненного цикла недействительно",
    "PERSONAL_EXPERIMENT_LIFECYCLE_STALE": "Событие жизненного цикла устарело",
    "PERSONAL_EXPERIMENT_LIFECYCLE_CONFLICT": "Цепочка жизненного цикла содержит конфликт",
    "PERSONAL_EXPERIMENT_ONE_ACTIVE_PER_GOAL": (
        "Для одной точной Goal уже есть активный эксперимент"
    ),
    "PERSONAL_EXPERIMENT_SOURCE_MISSING": "Исходная запись Stage 12 не найдена",
    "PERSONAL_EXPERIMENT_SOURCE_CHANGED": "Исходная запись Stage 12 изменилась",
    "PERSONAL_EXPERIMENT_OBSERVATION_TIME_REQUIRED": (
        "Для enrollment требуется точное время наблюдения"
    ),
    "PERSONAL_EXPERIMENT_OBSERVATION_OUTSIDE_WINDOW": "Наблюдение находится вне окна эксперимента",
    "PERSONAL_EXPERIMENT_OBSERVATION_LIMIT_EXCEEDED": "Достигнут предел enrolled observations",
    "PERSONAL_EXPERIMENT_OBSERVATION_STALE": "Enrollment observation устарел",
    "PERSONAL_EXPERIMENT_OBSERVATION_INVALID": "Enrollment observation недействителен",
    "PERSONAL_EXPERIMENT_OBSERVATION_CONFLICT": "Цепочка enrollment observations содержит конфликт",
    "PERSONAL_EXPERIMENT_TERMINAL_REQUIRED": "Требуется завершённый или отменённый эксперимент",
    "PERSONAL_EXPERIMENT_EVALUATION_WINDOW_INVALID": (
        "Cutoff результата находится вне окна эксперимента"
    ),
    "PERSONAL_EXPERIMENT_REASSESSMENT_STALE": "Reassessment устарел",
    "PERSONAL_EXPERIMENT_REASSESSMENT_CONFLICT": "Цепочка reassessment содержит конфликт",
    "PERSONAL_EXPERIMENT_POLICY_MISMATCH": "Политика личного эксперимента недействительна",
    "PERSONAL_EXPERIMENT_INVALID_FIELD": "Поле Safe Write личного эксперимента недействительно",
    "PERSONAL_EXPERIMENT_RESULT_TOO_LARGE": "План Safe Write превышает допустимый размер",
    "PERSONAL_EXPERIMENT_REVIEW_STORE_FULL": "Временное хранилище review-планов переполнено",
}


def _rejected(
    code: str,
    *,
    plan: PersonalExperimentSafeWritePlanV1 | None = None,
    apply: bool = False,
    path: str | None = None,
    validation_report: ScanReport | None = None,
) -> PersonalExperimentSafeWriteResult:
    return PersonalExperimentSafeWriteResult(
        CreateStatus.REJECTED,
        plan=plan,
        diagnostics=(_diagnostic(code, path=path),),
        validation_report=validation_report,
        apply_requested=apply,
    )


def _writer_error_code(code: str) -> str:
    mapping = {
        "CREATE_TARGET_EXISTS": "CREATE_TARGET_EXISTS",
        "CREATE_LINKED_PATH": "PERSONAL_EXPERIMENT_INTERNAL",
        "CREATE_PATH_ESCAPE": "PERSONAL_EXPERIMENT_INTERNAL",
        "CREATE_TEMPLATE_MISSING": "PERSONAL_EXPERIMENT_INTERNAL",
        "CREATE_TEMPLATE_INVALID": "PERSONAL_EXPERIMENT_INTERNAL",
        "CREATE_INVALID_PLAN": "PERSONAL_EXPERIMENT_INTERNAL",
    }
    return mapping.get(code, "PERSONAL_EXPERIMENT_INTERNAL")


PersonalExperimentSafeWritePlan = PersonalExperimentSafeWritePlanV1
PersonalExperimentSafeWriteResultV1 = PersonalExperimentSafeWriteResult


__all__ = [
    "PERSONAL_EXPERIMENT_REVIEW_MAX_ENTRIES",
    "PERSONAL_EXPERIMENT_REVIEW_MAX_PLAN_BYTES",
    "PERSONAL_EXPERIMENT_REVIEW_TTL_SECONDS",
    "PERSONAL_EXPERIMENT_SAFE_WRITE_PLAN_CONTRACT",
    "PersonalExperimentDefinitionDraftV1",
    "PersonalExperimentLifecycleDraftV1",
    "PersonalExperimentNoteWriter",
    "PersonalExperimentObservationDraftV1",
    "PersonalExperimentReassessmentDraftV1",
    "PersonalExperimentReviewError",
    "PersonalExperimentReviewStore",
    "PersonalExperimentSafeWrite",
    "PersonalExperimentSafeWriteDraftV1",
    "PersonalExperimentSafeWriteError",
    "PersonalExperimentSafeWritePlan",
    "PersonalExperimentSafeWritePlanV1",
    "PersonalExperimentSafeWriteResult",
    "PersonalExperimentSafeWriteResultV1",
]
