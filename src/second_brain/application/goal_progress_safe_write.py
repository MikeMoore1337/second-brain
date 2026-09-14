"""Stage 12B reviewed Safe Write for Goal Progress companion records.

The module is deliberately a separate application boundary from the pure
Stage 12A parser.  It accepts only typed, owner-reviewed definition and
observation fields, prepares an immutable dry-run plan, and applies that exact
plan only after a second current-vault read.  It has no transport, provider,
watcher, scheduler or automatic-capture capability.
"""

from __future__ import annotations

import hashlib
import hmac
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from typing import Any, Final, Literal, Protocol, cast
from uuid import UUID, uuid7

from second_brain.adapters.vault.frontmatter import parse_front_matter
from second_brain.application.goal_progress import (
    DEFAULT_GOAL_PROGRESS_POLICY,
    GOAL_PROGRESS_POLICY_FINGERPRINT,
    GOAL_PROGRESS_UNKNOWN_TIME,
    DefinitionRecordV1,
    GoalProgressRecordKindV1,
    GoalProgressValidationError,
    MilestoneObservationV1,
    MilestoneSetDefinitionV1,
    MilestoneStateV1,
    MilestoneV1,
    NumericDirectionV1,
    NumericObservationV1,
    NumericTargetDefinitionV1,
    ObservationRecordV1,
    ProgressModelV1,
    SupersessionChainStateV1,
    goal_progress_hash_json,
    parse_goal_progress_record,
    validate_definition_chain,
    validate_goal_binding,
    validate_goal_progress_policy,
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
from second_brain.application.ports import VaultReader
from second_brain.application.reports import (
    Diagnostic,
    DiagnosticSeverity,
    ScanReport,
    VaultSnapshot,
)
from second_brain.application.self_model import (
    DEFAULT_SELF_MODEL_POLICY,
    SelfModelPolicy,
)
from second_brain.application.validation import build_report
from second_brain.application.writes import (
    CreateNotePlan,
    CreateStatus,
    WriteReceipt,
    WriteSafetyError,
)
from second_brain.domain.models import NoteType, VaultManifest, parse_rfc3339, parse_uuid7

_HASH_PATTERN: Final[re.Pattern[str]] = re.compile(r"sha256:[0-9a-f]{64}\Z", re.ASCII)
_PLAN_OPERATION: Final[str] = "create"
_PLAN_CONTRACT: Final[str] = "goal_progress_safe_write_plan_v1"
_DRAFT_DEFINITION_FIELDS: Final[frozenset[str]] = frozenset(
    {
        "goal_source_uuid",
        "goal_identity_fingerprint",
        "definition_reviewed_at",
        "progress_model",
        "numeric_target",
        "milestone_set",
        "supersedes_definition_id",
    }
)
_DRAFT_OBSERVATION_FIELDS: Final[frozenset[str]] = frozenset(
    {
        "goal_source_uuid",
        "goal_identity_fingerprint",
        "progress_definition_id",
        "progress_model",
        "observed_at",
        "observed_at_precision",
        "observation_reviewed_at",
        "numeric_observation",
        "milestone_observation",
        "supersedes_observation_id",
    }
)
CallableClock = Callable[[], datetime]


class _SafeWriteFailure(Exception):
    """Internal control flow carrying only a public safe-write code."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


@dataclass(frozen=True, slots=True)
class GoalProgressDefinitionDraftV1:
    """Typed reviewed input for one definition record.

    Storage identity, policy marker, ``created`` and record UUID are owned by
    the application and intentionally cannot be supplied by this DTO.
    """

    goal_source_uuid: UUID | str
    goal_identity_fingerprint: str
    definition_reviewed_at: datetime | str
    progress_model: ProgressModelV1 | str
    numeric_target: NumericTargetDefinitionV1 | None = None
    milestone_set: MilestoneSetDefinitionV1 | None = None
    supersedes_definition_id: UUID | str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "goal_source_uuid", _parse_uuid_or_fail(self.goal_source_uuid))
        object.__setattr__(
            self,
            "goal_identity_fingerprint",
            _parse_hash_or_fail(self.goal_identity_fingerprint),
        )
        object.__setattr__(
            self,
            "definition_reviewed_at",
            _parse_reviewed_time_or_fail(self.definition_reviewed_at),
        )
        model = _parse_enum_or_fail(self.progress_model, ProgressModelV1)
        object.__setattr__(self, "progress_model", model)
        _validate_definition_branches(model, self.numeric_target, self.milestone_set)
        if self.supersedes_definition_id is not None:
            object.__setattr__(
                self,
                "supersedes_definition_id",
                _parse_uuid_or_fail(self.supersedes_definition_id),
            )

    @classmethod
    def from_dict(cls, value: object) -> GoalProgressDefinitionDraftV1:
        """Parse only the exact typed draft shape; reject storage/raw fields."""

        if not isinstance(value, Mapping) or set(value) != _DRAFT_DEFINITION_FIELDS:
            raise GoalProgressValidationError("GOAL_PROGRESS_INVALID_FIELD")
        try:
            numeric_target = _numeric_definition_from_dict(value["numeric_target"])
            milestone_set = _milestone_set_from_dict(value["milestone_set"])
            return cls(
                goal_source_uuid=cast(UUID | str, value["goal_source_uuid"]),
                goal_identity_fingerprint=cast(str, value["goal_identity_fingerprint"]),
                definition_reviewed_at=cast(
                    datetime | str,
                    value["definition_reviewed_at"],
                ),
                progress_model=cast(ProgressModelV1 | str, value["progress_model"]),
                numeric_target=numeric_target,
                milestone_set=milestone_set,
                supersedes_definition_id=cast(
                    UUID | str | None,
                    value["supersedes_definition_id"],
                ),
            )
        except GoalProgressValidationError:
            raise
        except TypeError, ValueError, UnicodeError, OverflowError:
            raise GoalProgressValidationError("GOAL_PROGRESS_INVALID_FIELD") from None

    def as_dict(self) -> dict[str, object]:
        """Return the bounded reviewed input without storage metadata."""

        return {
            "goal_source_uuid": str(self.goal_source_uuid),
            "goal_identity_fingerprint": self.goal_identity_fingerprint,
            "definition_reviewed_at": _format_datetime(cast(datetime, self.definition_reviewed_at)),
            "progress_model": cast(ProgressModelV1, self.progress_model).value,
            "numeric_target": (
                self.numeric_target.as_dict() if self.numeric_target is not None else None
            ),
            "milestone_set": (
                self.milestone_set.as_dict() if self.milestone_set is not None else None
            ),
            "supersedes_definition_id": (
                str(self.supersedes_definition_id)
                if self.supersedes_definition_id is not None
                else None
            ),
        }

    def to_record(self, record_id: UUID | str) -> DefinitionRecordV1:
        """Materialize the immutable Stage 12A record with an app-owned UUID."""

        return DefinitionRecordV1(
            id=record_id,
            goal_source_uuid=self.goal_source_uuid,
            goal_identity_fingerprint=self.goal_identity_fingerprint,
            goal_progress_policy_fingerprint=GOAL_PROGRESS_POLICY_FINGERPRINT,
            definition_reviewed_at=self.definition_reviewed_at,
            progress_model=cast(ProgressModelV1, self.progress_model),
            numeric_target=self.numeric_target,
            milestone_set=self.milestone_set,
            supersedes_definition_id=self.supersedes_definition_id,
        )


@dataclass(frozen=True, slots=True)
class GoalProgressObservationDraftV1:
    """Typed reviewed input for one observation or correction record."""

    goal_source_uuid: UUID | str
    goal_identity_fingerprint: str
    progress_definition_id: UUID | str
    progress_model: ProgressModelV1 | str
    observed_at: datetime | str
    observed_at_precision: str
    observation_reviewed_at: datetime | str
    numeric_observation: NumericObservationV1 | None = None
    milestone_observation: MilestoneObservationV1 | None = None
    supersedes_observation_id: UUID | str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "goal_source_uuid", _parse_uuid_or_fail(self.goal_source_uuid))
        object.__setattr__(
            self,
            "goal_identity_fingerprint",
            _parse_hash_or_fail(self.goal_identity_fingerprint),
        )
        object.__setattr__(
            self,
            "progress_definition_id",
            _parse_uuid_or_fail(self.progress_definition_id),
        )
        model = _parse_enum_or_fail(self.progress_model, ProgressModelV1)
        object.__setattr__(self, "progress_model", model)
        observed_at = _parse_observation_time_or_fail(
            self.observed_at,
            self.observed_at_precision,
        )
        object.__setattr__(self, "observed_at", observed_at)
        object.__setattr__(
            self,
            "observation_reviewed_at",
            _parse_reviewed_time_or_fail(self.observation_reviewed_at),
        )
        _validate_observation_branches(
            model,
            self.numeric_observation,
            self.milestone_observation,
        )
        if self.supersedes_observation_id is not None:
            object.__setattr__(
                self,
                "supersedes_observation_id",
                _parse_uuid_or_fail(self.supersedes_observation_id),
            )

    @classmethod
    def from_dict(cls, value: object) -> GoalProgressObservationDraftV1:
        """Parse an exact observation draft and reject fingerprint overrides."""

        if not isinstance(value, Mapping) or set(value) != _DRAFT_OBSERVATION_FIELDS:
            raise GoalProgressValidationError("GOAL_PROGRESS_INVALID_FIELD")
        try:
            numeric_observation = _numeric_observation_from_dict(value["numeric_observation"])
            milestone_observation = _milestone_observation_from_dict(value["milestone_observation"])
            return cls(
                goal_source_uuid=cast(UUID | str, value["goal_source_uuid"]),
                goal_identity_fingerprint=cast(str, value["goal_identity_fingerprint"]),
                progress_definition_id=cast(UUID | str, value["progress_definition_id"]),
                progress_model=cast(ProgressModelV1 | str, value["progress_model"]),
                observed_at=cast(datetime | str, value["observed_at"]),
                observed_at_precision=cast(str, value["observed_at_precision"]),
                observation_reviewed_at=cast(
                    datetime | str,
                    value["observation_reviewed_at"],
                ),
                numeric_observation=numeric_observation,
                milestone_observation=milestone_observation,
                supersedes_observation_id=cast(
                    UUID | str | None,
                    value["supersedes_observation_id"],
                ),
            )
        except GoalProgressValidationError:
            raise
        except TypeError, ValueError, UnicodeError, OverflowError:
            raise GoalProgressValidationError("GOAL_PROGRESS_INVALID_FIELD") from None

    def as_dict(self) -> dict[str, object]:
        """Return the bounded reviewed input without a derived definition hash."""

        return {
            "goal_source_uuid": str(self.goal_source_uuid),
            "goal_identity_fingerprint": self.goal_identity_fingerprint,
            "progress_definition_id": str(self.progress_definition_id),
            "progress_model": cast(ProgressModelV1, self.progress_model).value,
            "observed_at": (
                self.observed_at
                if self.observed_at == GOAL_PROGRESS_UNKNOWN_TIME
                else _format_datetime(cast(datetime, self.observed_at))
            ),
            "observed_at_precision": self.observed_at_precision,
            "observation_reviewed_at": _format_datetime(
                cast(datetime, self.observation_reviewed_at)
            ),
            "numeric_observation": (
                self.numeric_observation.as_dict() if self.numeric_observation is not None else None
            ),
            "milestone_observation": (
                self.milestone_observation.as_dict()
                if self.milestone_observation is not None
                else None
            ),
            "supersedes_observation_id": (
                str(self.supersedes_observation_id)
                if self.supersedes_observation_id is not None
                else None
            ),
        }

    def to_record(
        self,
        record_id: UUID | str,
        *,
        definition_fingerprint: str,
    ) -> ObservationRecordV1:
        """Materialize an observation with the active definition hash derived by the app."""

        return ObservationRecordV1(
            id=record_id,
            goal_source_uuid=self.goal_source_uuid,
            goal_identity_fingerprint=self.goal_identity_fingerprint,
            goal_progress_policy_fingerprint=GOAL_PROGRESS_POLICY_FINGERPRINT,
            progress_definition_id=self.progress_definition_id,
            definition_fingerprint=definition_fingerprint,
            progress_model=cast(ProgressModelV1, self.progress_model),
            observed_at=self.observed_at,
            observed_at_precision=self.observed_at_precision,
            observation_reviewed_at=self.observation_reviewed_at,
            numeric_observation=self.numeric_observation,
            milestone_observation=self.milestone_observation,
            supersedes_observation_id=self.supersedes_observation_id,
        )


@dataclass(frozen=True, slots=True)
class GoalProgressSafeWritePlanV1:
    """Immutable reviewed plan bound to exact source, payload and target bytes."""

    operation: str
    record_kind: GoalProgressRecordKindV1
    note_id: UUID
    created: datetime
    title: str
    relative_path: str
    target_root_relative: str
    record: DefinitionRecordV1 | ObservationRecordV1
    content: str
    source_fingerprint: str
    goal_source_uuid: UUID
    goal_identity_fingerprint: str
    goal_progress_policy_fingerprint: str
    definition_fingerprint: str | None
    supersedes_record_id: UUID | None
    filesystem_plan: CreateNotePlan
    plan_sha256: str

    def __post_init__(self) -> None:
        if self.operation != _PLAN_OPERATION:
            raise ValueError("Goal Progress plan operation is invalid")
        record_kind = _parse_enum_or_fail(self.record_kind, GoalProgressRecordKindV1)
        object.__setattr__(self, "record_kind", record_kind)
        note_id = _parse_uuid_or_fail(self.note_id)
        object.__setattr__(self, "note_id", note_id)
        if not isinstance(self.created, datetime) or self.created.tzinfo is None:
            raise ValueError("Goal Progress plan timestamp is invalid")
        if self.created.utcoffset() is None:
            raise ValueError("Goal Progress plan timestamp is invalid")
        if type(self.title) is not str or not self.title:
            raise ValueError("Goal Progress plan title is invalid")
        if type(self.relative_path) is not str or not self.relative_path:
            raise ValueError("Goal Progress plan path is invalid")
        if type(self.target_root_relative) is not str or not self.target_root_relative:
            raise ValueError("Goal Progress plan target root is invalid")
        if type(self.content) is not str:
            raise ValueError("Goal Progress plan content is invalid")
        if type(self.record) not in {DefinitionRecordV1, ObservationRecordV1}:
            raise ValueError("Goal Progress plan record is invalid")
        expected_record_kind = (
            GoalProgressRecordKindV1.DEFINITION
            if type(self.record) is DefinitionRecordV1
            else GoalProgressRecordKindV1.OBSERVATION
        )
        if record_kind is not expected_record_kind:
            raise ValueError("Goal Progress plan record kind is invalid")
        if self.record.id != note_id:
            raise ValueError("Goal Progress plan record identity is invalid")
        if parse_uuid7(self.goal_source_uuid) != self.record.goal_source_uuid:
            raise ValueError("Goal Progress plan Goal identity is invalid")
        if _parse_hash_or_fail(self.goal_identity_fingerprint) != (
            self.record.goal_identity_fingerprint
        ):
            raise ValueError("Goal Progress plan Goal fingerprint is invalid")
        if _parse_hash_or_fail(self.goal_progress_policy_fingerprint) != (
            self.record.goal_progress_policy_fingerprint
        ):
            raise ValueError("Goal Progress plan policy is invalid")
        _parse_hash_or_fail(self.source_fingerprint)
        expected_definition_fingerprint = (
            self.record.definition_fingerprint if type(self.record) is ObservationRecordV1 else None
        )
        if self.definition_fingerprint != expected_definition_fingerprint:
            raise ValueError("Goal Progress plan definition fingerprint is invalid")
        if type(self.record) is DefinitionRecordV1:
            expected_supersedes = self.record.supersedes_definition_id
        else:
            observation_record = cast(ObservationRecordV1, self.record)
            expected_supersedes = observation_record.supersedes_observation_id
        if self.supersedes_record_id != expected_supersedes:
            raise ValueError("Goal Progress plan supersession is invalid")
        if type(self.filesystem_plan) is not CreateNotePlan:
            raise ValueError("Goal Progress filesystem plan is invalid")
        if (
            self.filesystem_plan.note_type is not NoteType.ZETTEL
            or self.filesystem_plan.title != self.title
            or self.filesystem_plan.note_id != note_id
            or self.filesystem_plan.created != self.created
            or self.filesystem_plan.relative_path != self.relative_path
            or self.filesystem_plan.target_root_relative != self.target_root_relative
            or self.filesystem_plan.content != self.content
        ):
            raise ValueError("Goal Progress filesystem plan is inconsistent")
        if type(self.plan_sha256) is not str or _HASH_PATTERN.fullmatch(self.plan_sha256) is None:
            raise ValueError("Goal Progress plan fingerprint is invalid")

    @property
    def content_sha256(self) -> str:
        """Return the exact UTF-8 hash of the physical payload."""

        return _sha256(self.content.encode("utf-8"))

    def as_dict(self) -> dict[str, object]:
        """Return the exact dry-run projection used for owner review."""

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
            "goal_progress_policy_fingerprint": self.goal_progress_policy_fingerprint,
            "definition_fingerprint": self.definition_fingerprint,
            "supersedes_record_id": (
                str(self.supersedes_record_id) if self.supersedes_record_id is not None else None
            ),
            "content": self.content,
            "content_sha256": self.content_sha256,
            "plan_sha256": self.plan_sha256,
        }


@dataclass(frozen=True, slots=True)
class GoalProgressSafeWriteResult:
    """Result of one 12B prepare or explicit apply operation."""

    status: CreateStatus
    plan: GoalProgressSafeWritePlanV1 | None = None
    diagnostics: tuple[Diagnostic, ...] = ()
    validation_report: ScanReport | None = None
    rollback_succeeded: bool | None = None
    apply_requested: bool = False
    receipt: WriteReceipt | None = None

    @property
    def applied(self) -> bool:
        """Whether an apply path published or attempted the planned file."""

        return self.status in {CreateStatus.CREATED, CreateStatus.ROLLED_BACK}

    @property
    def successful(self) -> bool:
        """Whether prepare or apply ended without a rejected/rolled-back write."""

        return self.status in {CreateStatus.DRY_RUN, CreateStatus.CREATED}

    def as_dict(self) -> dict[str, object]:
        """Return a bounded JSON-compatible result without absolute vault paths."""

        return {
            "status": self.status.value,
            "mode": "apply" if self.apply_requested else "dry-run",
            "applied": self.applied,
            "plan": self.plan.as_dict() if self.plan is not None else None,
            "diagnostics": [
                {
                    "code": item.code,
                    "severity": item.severity.value,
                    **({"path": item.path} if item.path is not None else {}),
                }
                for item in self.diagnostics
            ],
            "rollback": (
                "succeeded"
                if self.rollback_succeeded is True
                else "failed"
                if self.rollback_succeeded is False
                else "not-needed"
            ),
            **(
                {"post_write_validation": _safe_report_projection(self.validation_report)}
                if self.validation_report is not None
                else {}
            ),
        }


class GoalProgressNoteWriter(Protocol):
    """Adapter seam that reuses the existing atomic managed-note writer."""

    def prepare_goal_progress(
        self,
        manifest: VaultManifest,
        record: DefinitionRecordV1 | ObservationRecordV1,
        title: str,
        note_id: UUID,
        created: datetime,
    ) -> CreateNotePlan:
        """Build a filesystem plan without changing the vault."""

    def write(self, plan: CreateNotePlan) -> WriteReceipt:
        """Publish one prepared plan using the adapter Safe Write primitives."""

    def rollback(self, receipt: WriteReceipt) -> bool:
        """Remove only a receipt-matching file."""


@dataclass(frozen=True, slots=True)
class _SnapshotReader:
    snapshot: VaultSnapshot

    def scan(self) -> VaultSnapshot:
        """Return the one snapshot used for a consistent current-state read."""

        return self.snapshot


@dataclass(frozen=True, slots=True)
class _CurrentVaultState:
    report: ScanReport
    goal_context: GrowthGoalContextV1 | None
    source_fingerprint: str | None

    @property
    def goals(self) -> tuple[GrowthGoalIdentityV1, ...]:
        """Return current exact Goal identities or an empty unavailable projection."""

        return () if self.goal_context is None else self.goal_context.goals

    @property
    def available(self) -> bool:
        """Whether the current snapshot and Goal source passed all read gates."""

        return (
            self.report.manifest is not None
            and self.report.error_count == 0
            and self.goal_context is not None
            and self.source_fingerprint is not None
        )


@dataclass(frozen=True, slots=True)
class GoalProgressSafeWrite:
    """Prepare and explicitly apply one reviewed Goal Progress record."""

    reader: VaultReader
    writer: GoalProgressNoteWriter
    clock: CallableClock = lambda: datetime.now(UTC).astimezone()
    self_model_policy: SelfModelPolicy = DEFAULT_SELF_MODEL_POLICY

    def prepare(
        self,
        draft: GoalProgressDefinitionDraftV1 | GoalProgressObservationDraftV1,
    ) -> GoalProgressSafeWriteResult:
        """Create a dry-run plan; this method never publishes a file."""

        if type(draft) not in {
            GoalProgressDefinitionDraftV1,
            GoalProgressObservationDraftV1,
        }:
            return _result_rejected("GOAL_PROGRESS_REQUEST_INVALID")
        try:
            state = self._read_current_state()
        except _SafeWriteFailure as exc:
            return _result_rejected(exc.code)
        if not state.available:
            return _result_rejected(
                "GOAL_PROGRESS_SOURCE_UNAVAILABLE",
                validation_report=state.report,
            )
        try:
            validate_goal_progress_policy(DEFAULT_GOAL_PROGRESS_POLICY)
            record = self._build_candidate(state, draft)
            _validate_candidate(state, record)
            created = _read_created_time(self.clock)
            note_id = cast(UUID, record.id)
            title = _title_for_record(record)
            manifest = cast(VaultManifest, state.report.manifest)
            filesystem_plan = self.writer.prepare_goal_progress(
                manifest,
                record,
                title,
                note_id,
                created,
            )
            _validate_filesystem_plan(filesystem_plan, record, title, created)
            plan = GoalProgressSafeWritePlanV1(
                operation=_PLAN_OPERATION,
                record_kind=(
                    GoalProgressRecordKindV1.DEFINITION
                    if type(record) is DefinitionRecordV1
                    else GoalProgressRecordKindV1.OBSERVATION
                ),
                note_id=note_id,
                created=created,
                title=title,
                relative_path=filesystem_plan.relative_path,
                target_root_relative=filesystem_plan.target_root_relative,
                record=record,
                content=filesystem_plan.content,
                source_fingerprint=cast(str, state.source_fingerprint),
                goal_source_uuid=cast(UUID, record.goal_source_uuid),
                goal_identity_fingerprint=record.goal_identity_fingerprint,
                goal_progress_policy_fingerprint=record.goal_progress_policy_fingerprint,
                definition_fingerprint=(
                    record.definition_fingerprint if type(record) is ObservationRecordV1 else None
                ),
                supersedes_record_id=_record_supersedes_id(record),
                filesystem_plan=filesystem_plan,
                plan_sha256="sha256:" + "0" * 64,
            )
            return GoalProgressSafeWriteResult(
                CreateStatus.DRY_RUN,
                plan=replace(plan, plan_sha256=_compute_plan_sha256(plan)),
            )
        except _SafeWriteFailure as exc:
            return _result_rejected(exc.code)
        except GoalProgressValidationError as exc:
            return _result_rejected(_draft_error_code(type(draft), exc.code))
        except WriteSafetyError as exc:
            return _result_rejected(_writer_error_code(exc.code))
        except OSError, ValueError, TypeError, UnicodeError, OverflowError:
            return _result_rejected("GOAL_PROGRESS_INTERNAL")
        except Exception:
            return _result_rejected("GOAL_PROGRESS_INTERNAL")

    def prepare_definition(
        self,
        draft: GoalProgressDefinitionDraftV1,
    ) -> GoalProgressSafeWriteResult:
        """Typed convenience boundary for definition dry-run planning."""

        return self.prepare(draft)

    def prepare_observation(
        self,
        draft: GoalProgressObservationDraftV1,
    ) -> GoalProgressSafeWriteResult:
        """Typed convenience boundary for observation dry-run planning."""

        return self.prepare(draft)

    def apply(
        self,
        plan: GoalProgressSafeWritePlanV1,
        accepted_plan_sha256: str,
    ) -> GoalProgressSafeWriteResult:
        """Apply only the exact reviewed plan after a fresh source/topology read."""

        if type(plan) is not GoalProgressSafeWritePlanV1 or not _valid_hash(accepted_plan_sha256):
            return _result_rejected("GOAL_PROGRESS_SAFE_WRITE_REVIEW_REQUIRED", apply=True)
        try:
            _revalidate_plan_shape(plan)
            expected_hash = _compute_plan_sha256(plan)
        except Exception:
            return _result_rejected(
                "GOAL_PROGRESS_SAFE_WRITE_REVIEW_REQUIRED",
                plan=plan,
                apply=True,
            )
        if (
            not _valid_hash(plan.plan_sha256)
            or not hmac.compare_digest(
                expected_hash,
                plan.plan_sha256,
            )
            or not hmac.compare_digest(expected_hash, accepted_plan_sha256)
        ):
            return _result_rejected(
                "GOAL_PROGRESS_SAFE_WRITE_REVIEW_REQUIRED",
                plan=plan,
                apply=True,
            )
        try:
            state = self._read_current_state()
        except _SafeWriteFailure as exc:
            return _result_rejected(exc.code, plan=plan, apply=True)
        if _plan_already_present(state.report, plan):
            return _result_rejected(
                "CREATE_TARGET_EXISTS",
                plan=plan,
                apply=True,
                path=plan.relative_path,
            )
        if not state.available:
            return _result_rejected(
                "GOAL_PROGRESS_SOURCE_UNAVAILABLE",
                plan=plan,
                apply=True,
                validation_report=state.report,
            )
        if state.source_fingerprint != plan.source_fingerprint:
            return _result_rejected(
                "GOAL_PROGRESS_VAULT_CHANGED",
                plan=plan,
                apply=True,
                path=plan.relative_path,
            )
        try:
            validate_goal_progress_policy(DEFAULT_GOAL_PROGRESS_POLICY)
            _validate_candidate(state, plan.record)
        except _SafeWriteFailure as exc:
            return _result_rejected(exc.code, plan=plan, apply=True, path=plan.relative_path)
        except GoalProgressValidationError as exc:
            return _result_rejected(
                _draft_error_code(
                    GoalProgressDefinitionDraftV1
                    if type(plan.record) is DefinitionRecordV1
                    else GoalProgressObservationDraftV1,
                    exc.code,
                ),
                plan=plan,
                apply=True,
                path=plan.relative_path,
            )
        try:
            receipt = self.writer.write(plan.filesystem_plan)
        except WriteSafetyError as exc:
            return _result_rejected(
                _writer_error_code(exc.code),
                plan=plan,
                apply=True,
                path=plan.relative_path,
            )
        except OSError, ValueError, TypeError, UnicodeError:
            return _result_rejected(
                "GOAL_PROGRESS_INTERNAL",
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
        return GoalProgressSafeWriteResult(
            CreateStatus.CREATED,
            plan=plan,
            validation_report=post_report,
            receipt=receipt,
            apply_requested=True,
        )

    def _rollback_after_write(
        self,
        plan: GoalProgressSafeWritePlanV1,
        receipt: WriteReceipt,
        validation_report: ScanReport | None,
    ) -> GoalProgressSafeWriteResult:
        """Rollback only the new receipt and retain the post-write evidence."""

        try:
            rollback_succeeded = self.writer.rollback(receipt)
        except Exception:
            rollback_succeeded = False
        diagnostics = [_diagnostic("GOAL_PROGRESS_SAFE_WRITE_VALIDATION_FAILED")]
        if not rollback_succeeded:
            diagnostics.append(
                _diagnostic(
                    "GOAL_PROGRESS_ROLLBACK_FAILED",
                    path=plan.relative_path,
                )
            )
        return GoalProgressSafeWriteResult(
            CreateStatus.ROLLED_BACK,
            plan=plan,
            diagnostics=tuple(diagnostics),
            validation_report=validation_report,
            rollback_succeeded=rollback_succeeded,
            apply_requested=True,
        )

    def _read_current_state(self) -> _CurrentVaultState:
        """Read and validate one snapshot, then derive current Goal identities from it."""

        try:
            snapshot = self.reader.scan()
            report = build_report(snapshot)
        except Exception:
            raise _SafeWriteFailure("GOAL_PROGRESS_SOURCE_UNAVAILABLE") from None
        if type(report) is not ScanReport:
            raise _SafeWriteFailure("GOAL_PROGRESS_SOURCE_UNAVAILABLE")
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
        draft: GoalProgressDefinitionDraftV1 | GoalProgressObservationDraftV1,
    ) -> DefinitionRecordV1 | ObservationRecordV1:
        """Build a candidate without accepting any derived observation hash."""

        record_id = uuid7()
        if type(draft) is GoalProgressDefinitionDraftV1:
            return draft.to_record(record_id)
        _validate_goal_reference(
            draft.goal_source_uuid,
            draft.goal_identity_fingerprint,
            state.goals,
        )
        active_definition = _active_definition_for(
            state,
            cast(UUID, draft.goal_source_uuid),
            draft.goal_identity_fingerprint,
        )
        observation_draft = cast(GoalProgressObservationDraftV1, draft)
        return observation_draft.to_record(
            record_id,
            definition_fingerprint=active_definition.definition_fingerprint,
        )


def _validate_candidate(
    state: _CurrentVaultState,
    record: DefinitionRecordV1 | ObservationRecordV1,
) -> None:
    """Validate exact Goal/policy/topology rules immediately before planning/apply."""

    validate_goal_progress_policy(DEFAULT_GOAL_PROGRESS_POLICY)
    if record.goal_progress_policy_fingerprint != GOAL_PROGRESS_POLICY_FINGERPRINT:
        raise _SafeWriteFailure("GOAL_PROGRESS_POLICY_MISMATCH")
    _validate_goal_reference(
        record.goal_source_uuid,
        record.goal_identity_fingerprint,
        state.goals,
    )
    all_note_ids = {note.note_id for note in state.report.notes if note.note_id is not None}
    if record.id in all_note_ids:
        raise _SafeWriteFailure("CREATE_TARGET_EXISTS")
    if type(record) is DefinitionRecordV1:
        _validate_definition_candidate(state, record)
    else:
        _validate_observation_candidate(state, cast(ObservationRecordV1, record))


def _validate_definition_candidate(
    state: _CurrentVaultState,
    candidate: DefinitionRecordV1,
) -> None:
    existing = _definitions_for(state, candidate)
    current_chain = validate_definition_chain(existing)
    if current_chain.issues or current_chain.state is SupersessionChainStateV1.CONFLICT:
        raise _SafeWriteFailure("GOAL_PROGRESS_DEFINITION_CONFLICT")
    active = current_chain.active_records
    predecessor = candidate.supersedes_definition_id
    if predecessor is None:
        if active:
            raise _SafeWriteFailure("GOAL_PROGRESS_DEFINITION_CONFLICT")
    elif len(active) != 1 or active[0].id != predecessor:
        raise _SafeWriteFailure("GOAL_PROGRESS_DEFINITION_STALE")
    combined = validate_definition_chain((*existing, candidate))
    if combined.issues or combined.state is not SupersessionChainStateV1.ONE_ACTIVE:
        raise _SafeWriteFailure("GOAL_PROGRESS_DEFINITION_CONFLICT")


def _validate_observation_candidate(
    state: _CurrentVaultState,
    candidate: ObservationRecordV1,
) -> None:
    active_definition = _active_definition_for(
        state,
        cast(UUID, candidate.goal_source_uuid),
        candidate.goal_identity_fingerprint,
    )
    if candidate.progress_definition_id != active_definition.id:
        raise _SafeWriteFailure("GOAL_PROGRESS_DEFINITION_STALE")
    if validate_observation_against_definition(candidate, active_definition):
        raise _SafeWriteFailure("GOAL_PROGRESS_OBSERVATION_INVALID")
    existing = _observations_for(state, candidate)
    current_chain = validate_observation_chain(existing)
    if current_chain.issues:
        raise _SafeWriteFailure("GOAL_PROGRESS_OBSERVATION_CONFLICT")
    predecessor = candidate.supersedes_observation_id
    if predecessor is not None:
        predecessor_record = next((item for item in existing if item.id == predecessor), None)
        if predecessor_record is None:
            raise _SafeWriteFailure("GOAL_PROGRESS_OBSERVATION_MISSING")
        if predecessor not in {item.id for item in current_chain.active_records}:
            raise _SafeWriteFailure("GOAL_PROGRESS_OBSERVATION_CONFLICT")
    combined = validate_observation_chain((*existing, candidate))
    if combined.issues:
        raise _SafeWriteFailure("GOAL_PROGRESS_OBSERVATION_CONFLICT")


def _validate_goal_reference(
    source_uuid: UUID | str,
    identity_fingerprint: str,
    current_goals: tuple[GrowthGoalIdentityV1, ...],
) -> None:
    """Require exact current Stage 11A Goal identity with no retargeting."""

    binding = validate_goal_binding(
        source_uuid,
        identity_fingerprint,
        current_goals=current_goals,
    )
    if binding.exact_current:
        return
    code_by_state = {
        "source_changed": "GOAL_PROGRESS_GOAL_SOURCE_CHANGED",
        "ambiguous": "GOAL_PROGRESS_GOAL_AMBIGUOUS",
        "non_current": "GOAL_PROGRESS_GOAL_REQUIRED",
        "source_missing": "GOAL_PROGRESS_GOAL_REQUIRED",
    }
    raise _SafeWriteFailure(code_by_state.get(binding.state.value, "GOAL_PROGRESS_GOAL_REQUIRED"))


def _active_definition_for(
    state: _CurrentVaultState,
    source_uuid: UUID,
    identity_fingerprint: str,
) -> DefinitionRecordV1:
    existing = tuple(
        item
        for item in state.report.goal_progress_definitions
        if item.goal_source_uuid == source_uuid
        and item.goal_identity_fingerprint == identity_fingerprint
    )
    if not existing:
        raise _SafeWriteFailure("GOAL_PROGRESS_DEFINITION_MISSING")
    chain = validate_definition_chain(existing)
    if chain.issues or chain.state is not SupersessionChainStateV1.ONE_ACTIVE:
        raise _SafeWriteFailure("GOAL_PROGRESS_DEFINITION_CONFLICT")
    active = chain.active_records[0]
    if type(active) is not DefinitionRecordV1:
        raise _SafeWriteFailure("GOAL_PROGRESS_DEFINITION_CONFLICT")
    return active


def _definitions_for(
    state: _CurrentVaultState,
    record: DefinitionRecordV1,
) -> tuple[DefinitionRecordV1, ...]:
    return tuple(
        item
        for item in state.report.goal_progress_definitions
        if item.goal_source_uuid == record.goal_source_uuid
        and item.goal_identity_fingerprint == record.goal_identity_fingerprint
    )


def _observations_for(
    state: _CurrentVaultState,
    record: ObservationRecordV1,
) -> tuple[ObservationRecordV1, ...]:
    return tuple(
        item
        for item in state.report.goal_progress_observations
        if item.goal_source_uuid == record.goal_source_uuid
        and item.goal_identity_fingerprint == record.goal_identity_fingerprint
        and item.progress_definition_id == record.progress_definition_id
    )


def _validate_filesystem_plan(
    filesystem_plan: CreateNotePlan,
    record: DefinitionRecordV1 | ObservationRecordV1,
    title: str,
    created: datetime,
) -> None:
    """Verify adapter output before it becomes an apply-capable 12B plan."""

    if type(filesystem_plan) is not CreateNotePlan:
        raise _SafeWriteFailure("GOAL_PROGRESS_INTERNAL")
    if (
        filesystem_plan.note_type is not NoteType.ZETTEL
        or filesystem_plan.title != title
        or filesystem_plan.note_id != record.id
        or filesystem_plan.created != created
        or not filesystem_plan.relative_path
        or not filesystem_plan.target_root_relative
    ):
        raise _SafeWriteFailure("GOAL_PROGRESS_INTERNAL")
    parsed = parse_front_matter(filesystem_plan.content)
    if parsed.error is not None or not parsed.has_front_matter:
        raise _SafeWriteFailure("GOAL_PROGRESS_INTERNAL")
    try:
        parsed_record = parse_goal_progress_record(parsed.data, note_id=filesystem_plan.note_id)
    except Exception:
        raise _SafeWriteFailure("GOAL_PROGRESS_INTERNAL") from None
    if parsed_record != record:
        raise _SafeWriteFailure("GOAL_PROGRESS_INTERNAL")


def _revalidate_plan_shape(plan: GoalProgressSafeWritePlanV1) -> None:
    """Re-run frozen DTO invariants in case an untrusted caller mutated an object."""

    GoalProgressSafeWritePlanV1(
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
        goal_progress_policy_fingerprint=plan.goal_progress_policy_fingerprint,
        definition_fingerprint=plan.definition_fingerprint,
        supersedes_record_id=plan.supersedes_record_id,
        filesystem_plan=plan.filesystem_plan,
        plan_sha256=plan.plan_sha256,
    )


def _post_write_is_valid(
    report: ScanReport,
    plan: GoalProgressSafeWritePlanV1,
) -> bool:
    """Require a clean full-vault report and exact record identity after publish."""

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
    return (
        note.goal_progress_definition == plan.record
        if type(plan.record) is DefinitionRecordV1
        else note.goal_progress_observation == plan.record
    )


def _plan_already_present(report: ScanReport, plan: GoalProgressSafeWritePlanV1) -> bool:
    matches = [note for note in report.notes if note.relative_path == plan.relative_path]
    if len(matches) != 1:
        return False
    note = matches[0]
    return note.note_id == plan.note_id and (
        note.goal_progress_definition == plan.record
        if type(plan.record) is DefinitionRecordV1
        else note.goal_progress_observation == plan.record
    )


def _current_source_fingerprint(
    report: ScanReport,
    context: GrowthGoalContextV1,
) -> str:
    """Fingerprint current Goal/policy/record topology, never note bodies."""

    if report.manifest is None:
        raise ValueError("manifest is unavailable")
    payload = {
        "contract": _PLAN_CONTRACT,
        "vault_path": report.vault_path,
        "manifest": {
            "schema_version": report.manifest.schema_version,
            "vault_id": str(report.manifest.vault_id),
            "default_language": report.manifest.default_language,
            "paths": report.manifest.paths.as_dict(),
            "attachments": {
                "warning_size_bytes": report.manifest.attachments.warning_size_bytes,
                "max_size_bytes": report.manifest.attachments.max_size_bytes,
            },
        },
        "goal_policy_fingerprint": GOAL_PROGRESS_POLICY_FINGERPRINT,
        "growth_policy_fingerprint": context.policy_fingerprint,
        "goals": [goal.as_dict() for goal in context.goals],
        "definitions": [item.as_dict() for item in report.goal_progress_definitions],
        "observations": [item.as_dict() for item in report.goal_progress_observations],
    }
    return goal_progress_hash_json(payload)


def _compute_plan_sha256(plan: GoalProgressSafeWritePlanV1) -> str:
    """Bind operation, target, identity, source, payload and exact bytes."""

    return goal_progress_hash_json(
        {
            "contract": _PLAN_CONTRACT,
            "operation": plan.operation,
            "record_kind": plan.record_kind.value,
            "target": {
                "relative_path": plan.relative_path,
                "target_root_relative": plan.target_root_relative,
                "title": plan.title,
            },
            "identity": {
                "id": str(plan.note_id),
                "created": plan.created.isoformat(timespec="seconds"),
            },
            "payload": plan.record.as_dict(),
            "source": {
                "source_fingerprint": plan.source_fingerprint,
                "goal_source_uuid": str(plan.goal_source_uuid),
                "goal_identity_fingerprint": plan.goal_identity_fingerprint,
                "goal_progress_policy_fingerprint": plan.goal_progress_policy_fingerprint,
                "definition_fingerprint": plan.definition_fingerprint,
                "supersedes_record_id": (
                    str(plan.supersedes_record_id)
                    if plan.supersedes_record_id is not None
                    else None
                ),
            },
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


def _record_supersedes_id(
    record: DefinitionRecordV1 | ObservationRecordV1,
) -> UUID | None:
    if type(record) is DefinitionRecordV1:
        return cast(UUID | None, record.supersedes_definition_id)
    return cast(UUID | None, cast(ObservationRecordV1, record).supersedes_observation_id)


def _title_for_record(record: DefinitionRecordV1 | ObservationRecordV1) -> str:
    kind = "definition" if type(record) is DefinitionRecordV1 else "observation"
    return f"goal-progress-{kind}-{record.id}"


def _parse_uuid_or_fail(value: object) -> UUID:
    try:
        return parse_uuid7(value)
    except TypeError, ValueError, OverflowError:
        raise GoalProgressValidationError("GOAL_PROGRESS_INVALID_FIELD") from None


def _parse_hash_or_fail(value: object) -> str:
    if type(value) is not str or _HASH_PATTERN.fullmatch(value) is None:
        raise GoalProgressValidationError("GOAL_PROGRESS_INVALID_FIELD")
    return value


def _valid_hash(value: object) -> bool:
    return type(value) is str and _HASH_PATTERN.fullmatch(value) is not None


def _parse_reviewed_time_or_fail(value: object) -> datetime:
    try:
        parsed = parse_rfc3339(value)
    except TypeError, ValueError, OverflowError, UnicodeError:
        raise GoalProgressValidationError("GOAL_PROGRESS_INVALID_FIELD") from None
    return parsed.astimezone(UTC)


def _parse_observation_time_or_fail(
    value: object,
    precision: object,
) -> datetime | Literal["unknown"]:
    if type(precision) is not str or precision not in {"exact", "unknown"}:
        raise GoalProgressValidationError("GOAL_PROGRESS_INVALID_FIELD")
    if value == GOAL_PROGRESS_UNKNOWN_TIME:
        if precision != "unknown":
            raise GoalProgressValidationError("GOAL_PROGRESS_INVALID_FIELD")
        return GOAL_PROGRESS_UNKNOWN_TIME
    if precision != "exact":
        raise GoalProgressValidationError("GOAL_PROGRESS_INVALID_FIELD")
    return _parse_reviewed_time_or_fail(value)


def _parse_enum_or_fail(value: object, enum_type: type[Any]) -> Any:
    if isinstance(value, enum_type):
        return value
    if type(value) is not str:
        raise GoalProgressValidationError("GOAL_PROGRESS_INVALID_FIELD")
    try:
        return enum_type(value)
    except ValueError:
        raise GoalProgressValidationError("GOAL_PROGRESS_INVALID_FIELD") from None


def _validate_definition_branches(
    model: ProgressModelV1,
    numeric_target: NumericTargetDefinitionV1 | None,
    milestone_set: MilestoneSetDefinitionV1 | None,
) -> None:
    if (numeric_target is None) == (milestone_set is None):
        raise GoalProgressValidationError("GOAL_PROGRESS_INVALID_MODEL")
    if numeric_target is not None and type(numeric_target) is not NumericTargetDefinitionV1:
        raise GoalProgressValidationError("GOAL_PROGRESS_INVALID_FIELD")
    if milestone_set is not None and type(milestone_set) is not MilestoneSetDefinitionV1:
        raise GoalProgressValidationError("GOAL_PROGRESS_INVALID_FIELD")
    if model is ProgressModelV1.NUMERIC_TARGET and numeric_target is None:
        raise GoalProgressValidationError("GOAL_PROGRESS_INVALID_MODEL")
    if model is ProgressModelV1.MILESTONE_SET and milestone_set is None:
        raise GoalProgressValidationError("GOAL_PROGRESS_INVALID_MODEL")


def _validate_observation_branches(
    model: ProgressModelV1,
    numeric_observation: NumericObservationV1 | None,
    milestone_observation: MilestoneObservationV1 | None,
) -> None:
    if (numeric_observation is None) == (milestone_observation is None):
        raise GoalProgressValidationError("GOAL_PROGRESS_INVALID_MODEL")
    if numeric_observation is not None and type(numeric_observation) is not NumericObservationV1:
        raise GoalProgressValidationError("GOAL_PROGRESS_INVALID_FIELD")
    if (
        milestone_observation is not None
        and type(milestone_observation) is not MilestoneObservationV1
    ):
        raise GoalProgressValidationError("GOAL_PROGRESS_INVALID_FIELD")
    if model is ProgressModelV1.NUMERIC_TARGET and numeric_observation is None:
        raise GoalProgressValidationError("GOAL_PROGRESS_INVALID_MODEL")
    if model is ProgressModelV1.MILESTONE_SET and milestone_observation is None:
        raise GoalProgressValidationError("GOAL_PROGRESS_INVALID_MODEL")


def _numeric_definition_from_dict(value: object) -> NumericTargetDefinitionV1 | None:
    if value is None:
        return None
    if not isinstance(value, Mapping):
        raise GoalProgressValidationError("GOAL_PROGRESS_INVALID_FIELD")
    fields = {
        "metric_id",
        "unit",
        "baseline",
        "target",
        "direction",
        "lower_bound",
        "upper_bound",
    }
    if set(value) != fields:
        raise GoalProgressValidationError("GOAL_PROGRESS_INVALID_FIELD")
    try:
        return NumericTargetDefinitionV1(
            metric_id=cast(str, value["metric_id"]),
            unit=cast(str, value["unit"]),
            baseline=cast(Any, value["baseline"]),
            target=cast(Any, value["target"]),
            direction=cast(NumericDirectionV1 | str, value["direction"]),
            lower_bound=cast(Any, value["lower_bound"]),
            upper_bound=cast(Any, value["upper_bound"]),
        )
    except TypeError, ValueError, GoalProgressValidationError:
        raise GoalProgressValidationError("GOAL_PROGRESS_INVALID_FIELD") from None


def _milestone_set_from_dict(value: object) -> MilestoneSetDefinitionV1 | None:
    if value is None:
        return None
    if not isinstance(value, Mapping) or set(value) != {"ordering", "milestones"}:
        raise GoalProgressValidationError("GOAL_PROGRESS_INVALID_FIELD")
    milestones_value = value["milestones"]
    if not isinstance(milestones_value, (list, tuple)):
        raise GoalProgressValidationError("GOAL_PROGRESS_INVALID_FIELD")
    milestones: list[MilestoneV1] = []
    try:
        for item in milestones_value:
            if not isinstance(item, Mapping) or set(item) != {"id", "label", "ordinal"}:
                raise GoalProgressValidationError("GOAL_PROGRESS_INVALID_FIELD")
            milestones.append(
                MilestoneV1(
                    cast(str, item["id"]),
                    cast(str, item["label"]),
                    cast(int, item["ordinal"]),
                )
            )
        return MilestoneSetDefinitionV1(
            ordering=cast(str, value["ordering"]),
            milestones=tuple(milestones),
        )
    except TypeError, ValueError, GoalProgressValidationError:
        raise GoalProgressValidationError("GOAL_PROGRESS_INVALID_FIELD") from None


def _numeric_observation_from_dict(value: object) -> NumericObservationV1 | None:
    if value is None:
        return None
    if not isinstance(value, Mapping) or set(value) != {"metric_id", "unit", "value"}:
        raise GoalProgressValidationError("GOAL_PROGRESS_INVALID_FIELD")
    try:
        return NumericObservationV1(
            metric_id=cast(str, value["metric_id"]),
            unit=cast(str, value["unit"]),
            value=cast(Any, value["value"]),
        )
    except TypeError, ValueError, GoalProgressValidationError:
        raise GoalProgressValidationError("GOAL_PROGRESS_INVALID_FIELD") from None


def _milestone_observation_from_dict(value: object) -> MilestoneObservationV1 | None:
    if value is None:
        return None
    if not isinstance(value, Mapping) or set(value) != {"milestone_id", "state"}:
        raise GoalProgressValidationError("GOAL_PROGRESS_INVALID_FIELD")
    try:
        return MilestoneObservationV1(
            milestone_id=cast(str, value["milestone_id"]),
            state=cast(MilestoneStateV1 | str, value["state"]),
        )
    except TypeError, ValueError, GoalProgressValidationError:
        raise GoalProgressValidationError("GOAL_PROGRESS_INVALID_FIELD") from None


def _read_created_time(clock: CallableClock) -> datetime:
    try:
        value = clock()
    except Exception:
        raise _SafeWriteFailure("GOAL_PROGRESS_REQUEST_INVALID") from None
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise _SafeWriteFailure("GOAL_PROGRESS_REQUEST_INVALID")
    return value.replace(microsecond=0)


def _format_datetime(value: datetime) -> str:
    normalized = value.astimezone(UTC)
    rendered = normalized.isoformat(
        timespec="microseconds" if normalized.microsecond else "seconds"
    )
    return rendered.removesuffix("+00:00") + "Z"


def _draft_error_code(draft_type: type[object], code: str) -> str:
    if code == "GOAL_PROGRESS_POLICY_MISMATCH":
        return "GOAL_PROGRESS_POLICY_MISMATCH"
    if code == "GOAL_PROGRESS_INVALID_MODEL":
        return "GOAL_PROGRESS_MODEL_UNSUPPORTED"
    if draft_type is GoalProgressObservationDraftV1:
        return "GOAL_PROGRESS_OBSERVATION_INVALID"
    return "GOAL_PROGRESS_DEFINITION_INVALID"


_WRITER_ERROR_MESSAGES: Final[dict[str, str]] = {
    "CREATE_TARGET_EXISTS": "Целевой файл уже существует; существующие файлы не изменяются.",
    "CREATE_LINKED_PATH": "Целевой путь или его каталог защищён как linked path.",
    "CREATE_PATH_ESCAPE": "Целевой путь выходит за границы vault.",
    "CREATE_ROOT_MISSING": "Объявленный каталог записи недоступен.",
    "CREATE_TEMPLATE_MISSING": "Шаблон zettel недоступен.",
    "CREATE_TEMPLATE_INVALID": "Шаблон zettel не прошёл проверку.",
    "CREATE_VAULT_OPERATION_BUSY": "Другая операция записи vault уже выполняется.",
    "CREATE_VAULT_LOCK_FAILED": "Общий lock операции vault недоступен.",
    "CREATE_WRITE_FAILED": "Файл не удалось безопасно опубликовать.",
    "CREATE_INVALID_PLAN": "План записи некорректен.",
}


def _writer_error_code(code: str) -> str:
    if code in _WRITER_ERROR_MESSAGES:
        return code
    return "GOAL_PROGRESS_INTERNAL"


_SAFE_MESSAGES: Final[dict[str, str]] = {
    "GOAL_PROGRESS_REQUEST_INVALID": "Проверенный draft запроса не соответствует контракту.",
    "GOAL_PROGRESS_GOAL_REQUIRED": "Текущая цель не найдена в exact Goal source.",
    "GOAL_PROGRESS_GOAL_AMBIGUOUS": "Для записи обнаружено несколько неоднозначных Goal identity.",
    "GOAL_PROGRESS_GOAL_SOURCE_CHANGED": "Текущий Goal source изменился; запись остановлена.",
    "GOAL_PROGRESS_SOURCE_UNAVAILABLE": "Текущий vault или Goal source не прошёл полную проверку.",
    "GOAL_PROGRESS_POLICY_MISMATCH": "Политика Goal Progress не совпадает с утверждённой v1.",
    "GOAL_PROGRESS_DEFINITION_INVALID": "Definition draft не соответствует контракту.",
    "GOAL_PROGRESS_DEFINITION_MISSING": "Для этой цели ещё нет reviewed progress definition.",
    "GOAL_PROGRESS_DEFINITION_STALE": "Definition topology или predecessor больше не актуальны.",
    "GOAL_PROGRESS_DEFINITION_CONFLICT": "Definition chain имеет конфликт или второй active root.",
    "GOAL_PROGRESS_MODEL_UNSUPPORTED": "Выбранная progress model не поддерживается в v1.",
    "GOAL_PROGRESS_OBSERVATION_INVALID": "Observation draft не соответствует active definition.",
    "GOAL_PROGRESS_OBSERVATION_MISSING": "Указанный predecessor observation не найден.",
    "GOAL_PROGRESS_OBSERVATION_CONFLICT": (
        "Observation lineage имеет конфликт или неоднозначного successor."
    ),
    "GOAL_PROGRESS_SAFE_WRITE_REVIEW_REQUIRED": (
        "Требуется повторное owner review точного plan hash."
    ),
    "GOAL_PROGRESS_VAULT_CHANGED": "Vault изменился после подготовки plan; запись не выполнена.",
    "GOAL_PROGRESS_SAFE_WRITE_VALIDATION_FAILED": "После записи full-vault validation не пройдена.",
    "GOAL_PROGRESS_ROLLBACK_FAILED": "Безопасный rollback не подтверждён из-за изменения файла.",
    "GOAL_PROGRESS_INTERNAL": "Safe Write остановлен внутренней защитой; данные не раскрыты.",
}


def _result_rejected(
    code: str,
    *,
    plan: GoalProgressSafeWritePlanV1 | None = None,
    apply: bool = False,
    validation_report: ScanReport | None = None,
    path: str | None = None,
) -> GoalProgressSafeWriteResult:
    return GoalProgressSafeWriteResult(
        CreateStatus.REJECTED,
        plan=plan,
        diagnostics=(_diagnostic(code, path=path),),
        validation_report=validation_report,
        apply_requested=apply,
    )


def _diagnostic(code: str, *, path: str | None = None) -> Diagnostic:
    message = _SAFE_MESSAGES.get(code, _WRITER_ERROR_MESSAGES.get(code, "Safe Write отклонён."))
    return Diagnostic(code, message, DiagnosticSeverity.ERROR, path)


def _safe_report_projection(report: ScanReport | None) -> dict[str, object] | None:
    if report is None:
        return None
    return {
        "errors": report.error_count,
        "warnings": report.warning_count,
        "notes": len(report.notes),
        "goal_progress_definitions": len(report.goal_progress_definitions),
        "goal_progress_observations": len(report.goal_progress_observations),
        "diagnostics": [
            {
                "code": diagnostic.code,
                "severity": diagnostic.severity.value,
            }
            for diagnostic in report.diagnostics
        ],
    }


def _sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


GoalProgressSafeWritePlan = GoalProgressSafeWritePlanV1
GoalProgressSafeWriteResultV1 = GoalProgressSafeWriteResult


__all__ = [
    "GoalProgressDefinitionDraftV1",
    "GoalProgressNoteWriter",
    "GoalProgressObservationDraftV1",
    "GoalProgressSafeWrite",
    "GoalProgressSafeWritePlan",
    "GoalProgressSafeWritePlanV1",
    "GoalProgressSafeWriteResult",
    "GoalProgressSafeWriteResultV1",
]
