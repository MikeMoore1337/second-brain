"""Provider-free execution events and lifecycle validation for Stage 18.

This module is intentionally independent of Web, vault, Git, providers, and
the execution store.  It binds every event to an immutable Stage 17 accepted
plan and exposes deterministic replay helpers for the later store and API
phases.
"""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Final, cast
from uuid import UUID, uuid7

from second_brain.application.personal_planning import (
    PLANNING_POLICY_FINGERPRINT,
    PLANNING_POLICY_ID,
    PersonalPlanningError,
    PlanningActionBindingV1,
    PlanningGoalRefV1,
    PlanningItemKindV1,
    PlanningItemV1,
)
from second_brain.application.personal_planning_store import (
    PlanningPlanV1,
    personal_planning_store_hash,
)
from second_brain.domain.models import parse_rfc3339, parse_uuid7

EXECUTION_FEEDBACK_CONTRACT_VERSION: Final[str] = "execution-feedback-v1"
EXECUTION_EVENT_VERSION: Final[str] = "1"
EXECUTION_POLICY_ID: Final[str] = "stage18-execution-feedback-v1"
EXECUTION_POLICY_CANONICAL_JSON: Final[str] = (
    '{"actual_effort_max_minutes":1440,"calibration_max_items":1024,'
    '"calibration_max_plans":32,"event_future_skew_seconds":300,'
    '"event_kinds":["start","pause","resume","block","unblock","complete",'
    '"abandon","void"],"event_note_max_bytes":2048,'
    '"executable_item_kinds":["commitment","next_action"],"max_events_per_item":256,'
    '"max_store_records":32768,"min_event_time":"2000-01-01T00:00:00Z",'
    '"reason_code_max":3,"stage17_policy_id":"stage17-personal-planning-v1",'
    '"stage18_contract_id":"execution-feedback-v1","stale_start":"fail_closed",'
    '"terminal_dispositions":["as_planned","with_changes","partial","unknown"],'
    '"version":"1"}'
)
EXECUTION_POLICY_FINGERPRINT: Final[str] = (
    "7a3af67e03089023c9d29f956f74111abf446b8cf71fef7409255ab97e34d3dc"
)

MAX_EXECUTION_ACTUAL_EFFORT_MINUTES: Final[int] = 1440
MAX_EXECUTION_EVENT_NOTE_BYTES: Final[int] = 2048
MAX_EXECUTION_REASON_CODES: Final[int] = 3
MAX_EXECUTION_EVENTS_PER_ITEM: Final[int] = 256
MAX_EXECUTION_STORE_RECORDS: Final[int] = 32768
EXECUTION_EVENT_FUTURE_SKEW_SECONDS: Final[int] = 300
MIN_EXECUTION_EVENT_TIME: Final[datetime] = datetime(2000, 1, 1, tzinfo=UTC)

_RAW_HASH_PATTERN: Final[re.Pattern[str]] = re.compile(r"[0-9a-f]{64}\Z", re.ASCII)
_ITEM_ID_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"[A-Za-z0-9][A-Za-z0-9._:-]{0,63}\Z", re.ASCII
)


class ExecutionFeedbackError(ValueError):
    """Base safe error for the Stage 18 core boundary."""

    code: str = "execution_feedback_invalid"

    def __init__(self, message: str | None = None) -> None:
        super().__init__(message or self.code)


class ExecutionFeedbackInvalidError(ExecutionFeedbackError):
    """A bounded event or binding value failed validation."""

    code = "invalid_event"


class ExecutionFeedbackPolicyMismatchError(ExecutionFeedbackError):
    """An event is bound to a policy other than the closed Stage 17 policy."""

    code = "policy_mismatch"


class ExecutionFeedbackSnapshotMismatchError(ExecutionFeedbackError):
    """The event does not identify the exact accepted plan."""

    code = "snapshot_mismatch"


class ExecutionFeedbackItemMismatchError(ExecutionFeedbackError):
    """The event does not identify the exact accepted item."""

    code = "item_mismatch"


class ExecutionFeedbackNonExecutableError(ExecutionFeedbackError):
    """The selected item kind is not executable in Stage 18."""

    code = "non_executable_item"


class ExecutionFeedbackLifecycleError(ExecutionFeedbackError):
    """The event cannot be applied to the current effective lifecycle state."""

    code = "illegal_transition"


class ExecutionFeedbackStaleError(ExecutionFeedbackError):
    """A new execution start is based on a superseded or stale source."""

    code = "stale_snapshot"


class ExecutionFeedbackCorrectionError(ExecutionFeedbackError):
    """A void correction cannot produce a valid effective history."""

    code = "invalid_correction"


class ExecutionEventTypeV1(StrEnum):
    """Closed append-only event vocabulary."""

    START = "start"
    PAUSE = "pause"
    RESUME = "resume"
    BLOCK = "block"
    UNBLOCK = "unblock"
    COMPLETE = "complete"
    ABANDON = "abandon"
    VOID = "void"


class ExecutionEffortPrecisionV1(StrEnum):
    """Whether terminal effort was entered as an exact value."""

    EXACT = "exact"
    UNKNOWN = "unknown"


class ExecutionResultDispositionV1(StrEnum):
    """Owner-provided neutral terminal disposition."""

    AS_PLANNED = "as_planned"
    WITH_CHANGES = "with_changes"
    PARTIAL = "partial"
    UNKNOWN = "unknown"


class ExecutionReasonCodeV1(StrEnum):
    """Neutral bounded reasons for a block or abandonment."""

    DEPENDENCY = "dependency"
    MISSING_INFORMATION = "missing_information"
    CAPACITY = "capacity"
    PRIORITY_CHANGE = "priority_change"
    SCOPE_CHANGE = "scope_change"
    ESTIMATE_MISMATCH = "estimate_mismatch"
    TECHNICAL_PROBLEM = "technical_problem"
    EXTERNAL_WAIT = "external_wait"
    CONTEXT_CHANGE = "context_change"
    OTHER = "other"
    UNKNOWN = "unknown"


class ExecutionDeviationCodeV1(StrEnum):
    """Neutral bounded deviations recorded on a terminal event."""

    DEPENDENCY = "dependency"
    MISSING_INFORMATION = "missing_information"
    CAPACITY = "capacity"
    PRIORITY_CHANGE = "priority_change"
    SCOPE_CHANGE = "scope_change"
    ESTIMATE_MISMATCH = "estimate_mismatch"
    TECHNICAL_PROBLEM = "technical_problem"
    EXTERNAL_WAIT = "external_wait"
    CONTEXT_CHANGE = "context_change"
    OTHER = "other"
    UNKNOWN = "unknown"


class ExecutionLifecycleStateV1(StrEnum):
    """Effective state of one selected executable item."""

    NOT_STARTED = "not_started"
    IN_PROGRESS = "in_progress"
    PAUSED = "paused"
    BLOCKED = "blocked"
    COMPLETED = "completed"
    ABANDONED = "abandoned"


class ExecutionSourceStatusV1(StrEnum):
    """Freshness status supplied by the later source-aware service."""

    CURRENT = "current"
    STALE = "stale"
    UNAVAILABLE = "unavailable"
    SUPERSEDED = "superseded"


ExecutionEventType = ExecutionEventTypeV1
ExecutionLifecycleState = ExecutionLifecycleStateV1
ExecutionSourceStatus = ExecutionSourceStatusV1
PlanningPortfolioSnapshotV1 = PlanningPlanV1


def _invalid(message: str | None = None) -> ExecutionFeedbackInvalidError:
    return ExecutionFeedbackInvalidError(message)


def _canonical_bytes(value: object) -> bytes:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except TypeError, UnicodeError, ValueError, OverflowError:
        raise _invalid() from None


def execution_feedback_hash(value: object) -> str:
    """Return the canonical SHA-256 used by Stage 18 identities."""

    return hashlib.sha256(_canonical_bytes(value)).hexdigest()


def _raw_hash(value: object) -> str:
    if type(value) is not str or _RAW_HASH_PATTERN.fullmatch(value) is None:
        raise _invalid()
    return value


def _uuid7(value: object) -> UUID:
    try:
        return parse_uuid7(value)
    except TypeError, ValueError, OverflowError:
        raise _invalid() from None


def _timestamp(value: object) -> datetime:
    try:
        parsed = parse_rfc3339(value)
    except TypeError, ValueError, OverflowError:
        raise _invalid() from None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise _invalid()
    return parsed.astimezone(UTC)


def format_execution_timestamp(value: datetime) -> str:
    """Render a normalized event time without dropping microsecond precision."""

    rendered = value.astimezone(UTC).isoformat(
        timespec="microseconds" if value.microsecond else "seconds"
    )
    return rendered.removesuffix("+00:00") + "Z"


def validate_execution_event_time(
    value: datetime | str,
    *,
    received_at: datetime | str,
) -> datetime:
    """Normalize and bound an explicit owner-supplied timestamp."""

    occurred_at = _timestamp(value)
    received = _timestamp(received_at)
    if occurred_at < MIN_EXECUTION_EVENT_TIME:
        raise _invalid("event time is before the supported lower bound")
    if occurred_at > received + timedelta(seconds=EXECUTION_EVENT_FUTURE_SKEW_SECONDS):
        raise _invalid("event time is too far in the future")
    return occurred_at


def operation_id_fingerprint(value: str | UUID) -> str:
    """Hash an idempotency key without retaining the caller-provided value."""

    normalized = str(value)
    try:
        encoded = normalized.encode("utf-8")
    except UnicodeError:
        raise _invalid() from None
    if (
        not normalized
        or len(encoded) > 256
        or any(ord(char) < 0x20 or ord(char) == 0x7F for char in normalized)
    ):
        raise _invalid()
    return hashlib.sha256(encoded).hexdigest()


def accepted_item_fingerprint(item: PlanningItemV1) -> str:
    """Hash the complete immutable Stage 17 item, not its title."""

    if type(item) is not PlanningItemV1:
        raise _invalid()
    return personal_planning_store_hash(item.as_dict())


def validate_execution_policy() -> str:
    """Verify the compiled Stage 18 policy bytes and fingerprint."""

    payload = {
        "actual_effort_max_minutes": MAX_EXECUTION_ACTUAL_EFFORT_MINUTES,
        "calibration_max_items": 1024,
        "calibration_max_plans": 32,
        "event_future_skew_seconds": EXECUTION_EVENT_FUTURE_SKEW_SECONDS,
        "event_kinds": [event.value for event in ExecutionEventTypeV1],
        "event_note_max_bytes": MAX_EXECUTION_EVENT_NOTE_BYTES,
        "executable_item_kinds": [
            PlanningItemKindV1.COMMITMENT.value,
            PlanningItemKindV1.NEXT_ACTION.value,
        ],
        "max_events_per_item": MAX_EXECUTION_EVENTS_PER_ITEM,
        "max_store_records": MAX_EXECUTION_STORE_RECORDS,
        "min_event_time": "2000-01-01T00:00:00Z",
        "reason_code_max": MAX_EXECUTION_REASON_CODES,
        "stage17_policy_id": PLANNING_POLICY_ID,
        "stage18_contract_id": EXECUTION_FEEDBACK_CONTRACT_VERSION,
        "stale_start": "fail_closed",
        "terminal_dispositions": [item.value for item in ExecutionResultDispositionV1],
        "version": EXECUTION_EVENT_VERSION,
    }
    if _canonical_bytes(payload).decode("utf-8") != EXECUTION_POLICY_CANONICAL_JSON:
        raise ExecutionFeedbackPolicyMismatchError()
    fingerprint = execution_feedback_hash(payload)
    if fingerprint != EXECUTION_POLICY_FINGERPRINT:
        raise ExecutionFeedbackPolicyMismatchError()
    return fingerprint


def _enum_value[EnumT: StrEnum](value: object, enum_type: type[EnumT]) -> EnumT:
    if type(value) is enum_type:
        return value
    if type(value) is str:
        try:
            return enum_type(value)
        except ValueError:
            pass
    raise _invalid()


def _safe_text(value: object, *, limit: int, allow_empty: bool = True) -> str:
    if type(value) is not str:
        raise _invalid()
    if any(
        ord(char) < 0x20 or 0x7F <= ord(char) <= 0x9F or unicodedata.category(char) == "Cf"
        for char in value
    ):
        raise _invalid()
    normalized = unicodedata.normalize("NFC", value)
    if normalized != value:
        raise _invalid()
    normalized = normalized.strip()
    try:
        size = len(normalized.encode("utf-8"))
    except UnicodeEncodeError:
        raise _invalid() from None
    if size > limit or (not allow_empty and not normalized):
        raise _invalid()
    return normalized


def _item_id(value: object) -> str:
    normalized = _safe_text(value, limit=64, allow_empty=False)
    if _ITEM_ID_PATTERN.fullmatch(normalized) is None:
        raise _invalid()
    return normalized


def _wire_dict(value: object) -> dict[str, object]:
    if type(value) is not dict or any(type(key) is not str for key in value):
        raise _invalid()
    return cast(dict[str, object], value)


def _require_fields(value: object, expected: set[str]) -> dict[str, object]:
    data = _wire_dict(value)
    if set(data) != expected:
        raise _invalid()
    return data


def _wire_list(value: object) -> list[object]:
    if type(value) is not list:
        raise _invalid()
    return cast(list[object], value)


def _code_tuple[EnumT: StrEnum](
    value: object,
    enum_type: type[EnumT],
) -> tuple[EnumT, ...]:
    if type(value) is not tuple or len(value) > MAX_EXECUTION_REASON_CODES:
        raise _invalid()
    result = tuple(_enum_value(item, enum_type) for item in value)
    if len(set(result)) != len(result):
        raise _invalid()
    return result


_TERMINAL_EVENTS: Final[frozenset[ExecutionEventTypeV1]] = frozenset(
    {ExecutionEventTypeV1.COMPLETE, ExecutionEventTypeV1.ABANDON}
)
_EXECUTABLE_KINDS: Final[frozenset[PlanningItemKindV1]] = frozenset(
    {PlanningItemKindV1.COMMITMENT, PlanningItemKindV1.NEXT_ACTION}
)


@dataclass(frozen=True, slots=True)
class ExecutionEventV1:
    """One exact, append-only Stage 18 execution event."""

    execution_event_version: str
    event_id: UUID | str
    event_type: ExecutionEventTypeV1 | str
    operation_id_fingerprint: str
    occurred_at: datetime | str
    planning_snapshot_id: UUID | str
    planning_snapshot_fingerprint: str
    planning_plan_revision: int
    planning_source_pack_fingerprint: str
    planning_proposal_fingerprint: str
    planning_policy_id: str
    planning_policy_fingerprint: str
    item_id: str
    accepted_item_fingerprint: str
    item_kind: PlanningItemKindV1 | str
    accepted_item: PlanningItemV1
    goal_refs: tuple[PlanningGoalRefV1, ...]
    action_refs: tuple[PlanningActionBindingV1, ...]
    actual_effort_minutes: int | None = None
    effort_precision: ExecutionEffortPrecisionV1 | str = ExecutionEffortPrecisionV1.UNKNOWN
    actual_result_note: str = ""
    reason_codes: tuple[ExecutionReasonCodeV1 | str, ...] = ()
    deviation_codes: tuple[ExecutionDeviationCodeV1 | str, ...] = ()
    result_disposition: ExecutionResultDispositionV1 | str | None = None
    void_target_event_id: UUID | str | None = None
    void_target_event_fingerprint: str | None = None
    correction_reason: str | None = None

    def __post_init__(self) -> None:
        if self.execution_event_version != EXECUTION_EVENT_VERSION:
            raise _invalid()
        event_id = _uuid7(self.event_id)
        event_type = _enum_value(self.event_type, ExecutionEventTypeV1)
        operation = _raw_hash(self.operation_id_fingerprint)
        occurred_at = _timestamp(self.occurred_at)
        if occurred_at < MIN_EXECUTION_EVENT_TIME:
            raise _invalid()
        snapshot_id = _uuid7(self.planning_snapshot_id)
        snapshot_fingerprint = _raw_hash(self.planning_snapshot_fingerprint)
        if (
            type(self.planning_plan_revision) is not int
            or isinstance(self.planning_plan_revision, bool)
            or not 1 <= self.planning_plan_revision <= (1 << 64) - 1
        ):
            raise _invalid()
        source_fingerprint = _raw_hash(self.planning_source_pack_fingerprint)
        proposal_fingerprint = _raw_hash(self.planning_proposal_fingerprint)
        if (
            self.planning_policy_id != PLANNING_POLICY_ID
            or self.planning_policy_fingerprint != PLANNING_POLICY_FINGERPRINT
        ):
            raise ExecutionFeedbackPolicyMismatchError()
        item_id = _item_id(self.item_id)
        item_fingerprint = _raw_hash(self.accepted_item_fingerprint)
        if type(self.accepted_item) is not PlanningItemV1:
            raise _invalid()
        item_kind = _enum_value(self.item_kind, PlanningItemKindV1)
        if item_kind not in _EXECUTABLE_KINDS or self.accepted_item.kind not in _EXECUTABLE_KINDS:
            raise ExecutionFeedbackNonExecutableError()
        if (
            item_id != self.accepted_item.item_id
            or item_kind is not self.accepted_item.kind
            or item_fingerprint != accepted_item_fingerprint(self.accepted_item)
        ):
            raise ExecutionFeedbackItemMismatchError()
        if type(self.goal_refs) is not tuple or any(
            type(ref) is not PlanningGoalRefV1 for ref in self.goal_refs
        ):
            raise _invalid()
        if type(self.action_refs) is not tuple or any(
            type(ref) is not PlanningActionBindingV1 for ref in self.action_refs
        ):
            raise _invalid()
        goal_refs = tuple(self.goal_refs)
        action_refs = tuple(self.action_refs)
        if (
            goal_refs != self.accepted_item.goal_refs
            or action_refs != self.accepted_item.action_refs
        ):
            raise ExecutionFeedbackItemMismatchError()
        precision = _enum_value(self.effort_precision, ExecutionEffortPrecisionV1)
        if precision is ExecutionEffortPrecisionV1.EXACT:
            if event_type not in _TERMINAL_EVENTS:
                raise _invalid()
            if (
                type(self.actual_effort_minutes) is not int
                or isinstance(self.actual_effort_minutes, bool)
                or not 0 <= self.actual_effort_minutes <= MAX_EXECUTION_ACTUAL_EFFORT_MINUTES
            ):
                raise _invalid()
        elif self.actual_effort_minutes is not None:
            raise _invalid()
        if (
            event_type not in _TERMINAL_EVENTS
            and precision is not ExecutionEffortPrecisionV1.UNKNOWN
        ):
            raise _invalid()
        note = _safe_text(
            self.actual_result_note,
            limit=MAX_EXECUTION_EVENT_NOTE_BYTES,
        )
        reasons = _code_tuple(self.reason_codes, ExecutionReasonCodeV1)
        deviations = _code_tuple(self.deviation_codes, ExecutionDeviationCodeV1)
        if deviations and event_type not in _TERMINAL_EVENTS:
            raise _invalid()
        disposition = (
            None
            if self.result_disposition is None
            else _enum_value(self.result_disposition, ExecutionResultDispositionV1)
        )
        if disposition is not None and event_type not in _TERMINAL_EVENTS:
            raise _invalid()
        if event_type is ExecutionEventTypeV1.BLOCK and not reasons:
            raise _invalid()
        if event_type is ExecutionEventTypeV1.ABANDON and not reasons and not note:
            raise _invalid()
        target_id: UUID | None
        target_fingerprint: str | None
        correction_reason: str | None
        if event_type is ExecutionEventTypeV1.VOID:
            if precision is not ExecutionEffortPrecisionV1.UNKNOWN or note or reasons or deviations:
                raise _invalid()
            if disposition is not None:
                raise _invalid()
            if self.void_target_event_id is None or self.void_target_event_fingerprint is None:
                raise ExecutionFeedbackCorrectionError()
            target_id = _uuid7(self.void_target_event_id)
            target_fingerprint = _raw_hash(self.void_target_event_fingerprint)
            correction_reason = _safe_text(
                self.correction_reason,
                limit=MAX_EXECUTION_EVENT_NOTE_BYTES,
                allow_empty=False,
            )
        else:
            if (
                self.void_target_event_id is not None
                or self.void_target_event_fingerprint is not None
                or self.correction_reason is not None
            ):
                raise _invalid()
            target_id = None
            target_fingerprint = None
            correction_reason = None
        object.__setattr__(self, "execution_event_version", EXECUTION_EVENT_VERSION)
        object.__setattr__(self, "event_id", event_id)
        object.__setattr__(self, "event_type", event_type)
        object.__setattr__(self, "operation_id_fingerprint", operation)
        object.__setattr__(self, "occurred_at", occurred_at)
        object.__setattr__(self, "planning_snapshot_id", snapshot_id)
        object.__setattr__(self, "planning_snapshot_fingerprint", snapshot_fingerprint)
        object.__setattr__(self, "planning_plan_revision", self.planning_plan_revision)
        object.__setattr__(self, "planning_source_pack_fingerprint", source_fingerprint)
        object.__setattr__(self, "planning_proposal_fingerprint", proposal_fingerprint)
        object.__setattr__(self, "planning_policy_id", PLANNING_POLICY_ID)
        object.__setattr__(self, "planning_policy_fingerprint", PLANNING_POLICY_FINGERPRINT)
        object.__setattr__(self, "item_id", item_id)
        object.__setattr__(self, "accepted_item_fingerprint", item_fingerprint)
        object.__setattr__(self, "item_kind", item_kind)
        object.__setattr__(self, "goal_refs", goal_refs)
        object.__setattr__(self, "action_refs", action_refs)
        object.__setattr__(self, "effort_precision", precision)
        object.__setattr__(self, "actual_result_note", note)
        object.__setattr__(self, "reason_codes", reasons)
        object.__setattr__(self, "deviation_codes", deviations)
        object.__setattr__(self, "result_disposition", disposition)
        object.__setattr__(self, "void_target_event_id", target_id)
        object.__setattr__(self, "void_target_event_fingerprint", target_fingerprint)
        object.__setattr__(self, "correction_reason", correction_reason)

    def as_dict(self) -> dict[str, object]:
        """Return the strict wire representation used for event fingerprints."""

        return {
            "execution_event_version": self.execution_event_version,
            "event_id": str(self.event_id),
            "event_type": cast(ExecutionEventTypeV1, self.event_type).value,
            "operation_id_fingerprint": self.operation_id_fingerprint,
            "occurred_at": format_execution_timestamp(cast(datetime, self.occurred_at)),
            "planning_snapshot_id": str(self.planning_snapshot_id),
            "planning_snapshot_fingerprint": self.planning_snapshot_fingerprint,
            "planning_plan_revision": self.planning_plan_revision,
            "planning_source_pack_fingerprint": self.planning_source_pack_fingerprint,
            "planning_proposal_fingerprint": self.planning_proposal_fingerprint,
            "planning_policy_id": self.planning_policy_id,
            "planning_policy_fingerprint": self.planning_policy_fingerprint,
            "item_id": self.item_id,
            "accepted_item_fingerprint": self.accepted_item_fingerprint,
            "item_kind": cast(PlanningItemKindV1, self.item_kind).value,
            "accepted_item": self.accepted_item.as_dict(),
            "goal_refs": [ref.as_dict() for ref in self.goal_refs],
            "action_refs": [ref.as_dict() for ref in self.action_refs],
            "actual_effort_minutes": self.actual_effort_minutes,
            "effort_precision": cast(ExecutionEffortPrecisionV1, self.effort_precision).value,
            "actual_result_note": self.actual_result_note,
            "reason_codes": [cast(ExecutionReasonCodeV1, code).value for code in self.reason_codes],
            "deviation_codes": [
                cast(ExecutionDeviationCodeV1, code).value for code in self.deviation_codes
            ],
            "result_disposition": (
                None
                if self.result_disposition is None
                else cast(ExecutionResultDispositionV1, self.result_disposition).value
            ),
            "void_target_event_id": (
                None if self.void_target_event_id is None else str(self.void_target_event_id)
            ),
            "void_target_event_fingerprint": self.void_target_event_fingerprint,
            "correction_reason": self.correction_reason,
        }

    def to_json(self) -> str:
        return _canonical_bytes(self.as_dict()).decode("utf-8")

    @classmethod
    def from_dict(cls, value: object) -> ExecutionEventV1:
        data = _require_fields(
            value,
            {
                "execution_event_version",
                "event_id",
                "event_type",
                "operation_id_fingerprint",
                "occurred_at",
                "planning_snapshot_id",
                "planning_snapshot_fingerprint",
                "planning_plan_revision",
                "planning_source_pack_fingerprint",
                "planning_proposal_fingerprint",
                "planning_policy_id",
                "planning_policy_fingerprint",
                "item_id",
                "accepted_item_fingerprint",
                "item_kind",
                "accepted_item",
                "goal_refs",
                "action_refs",
                "actual_effort_minutes",
                "effort_precision",
                "actual_result_note",
                "reason_codes",
                "deviation_codes",
                "result_disposition",
                "void_target_event_id",
                "void_target_event_fingerprint",
                "correction_reason",
            },
        )
        try:
            accepted_item = PlanningItemV1.from_dict(data["accepted_item"])
            goal_refs = tuple(
                PlanningGoalRefV1.from_dict(item) for item in _wire_list(data["goal_refs"])
            )
            action_refs = tuple(
                PlanningActionBindingV1.from_dict(item) for item in _wire_list(data["action_refs"])
            )
            reason_codes = tuple(cast(str, item) for item in _wire_list(data["reason_codes"]))
            deviation_codes = tuple(cast(str, item) for item in _wire_list(data["deviation_codes"]))
            return cls(
                execution_event_version=cast(str, data["execution_event_version"]),
                event_id=cast(UUID | str, data["event_id"]),
                event_type=cast(ExecutionEventTypeV1 | str, data["event_type"]),
                operation_id_fingerprint=cast(str, data["operation_id_fingerprint"]),
                occurred_at=cast(str, data["occurred_at"]),
                planning_snapshot_id=cast(UUID | str, data["planning_snapshot_id"]),
                planning_snapshot_fingerprint=cast(str, data["planning_snapshot_fingerprint"]),
                planning_plan_revision=cast(int, data["planning_plan_revision"]),
                planning_source_pack_fingerprint=cast(
                    str, data["planning_source_pack_fingerprint"]
                ),
                planning_proposal_fingerprint=cast(str, data["planning_proposal_fingerprint"]),
                planning_policy_id=cast(str, data["planning_policy_id"]),
                planning_policy_fingerprint=cast(str, data["planning_policy_fingerprint"]),
                item_id=cast(str, data["item_id"]),
                accepted_item_fingerprint=cast(str, data["accepted_item_fingerprint"]),
                item_kind=cast(PlanningItemKindV1 | str, data["item_kind"]),
                accepted_item=accepted_item,
                goal_refs=goal_refs,
                action_refs=action_refs,
                actual_effort_minutes=cast(int | None, data["actual_effort_minutes"]),
                effort_precision=cast(ExecutionEffortPrecisionV1 | str, data["effort_precision"]),
                actual_result_note=cast(str, data["actual_result_note"]),
                reason_codes=reason_codes,
                deviation_codes=deviation_codes,
                result_disposition=cast(
                    ExecutionResultDispositionV1 | str | None,
                    data["result_disposition"],
                ),
                void_target_event_id=cast(UUID | str | None, data["void_target_event_id"]),
                void_target_event_fingerprint=cast(
                    str | None,
                    data["void_target_event_fingerprint"],
                ),
                correction_reason=cast(str | None, data["correction_reason"]),
            )
        except ExecutionFeedbackError:
            raise
        except PersonalPlanningError:
            raise _invalid() from None
        except TypeError, ValueError, KeyError, UnicodeError, OverflowError:
            raise _invalid() from None

    @classmethod
    def from_json(cls, value: object) -> ExecutionEventV1:
        if type(value) is not str:
            raise _invalid()

        def reject_duplicate_pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
            result: dict[str, object] = {}
            for key, item in pairs:
                if key in result:
                    raise _invalid()
                result[key] = item
            return result

        try:
            decoded = json.loads(
                value,
                object_pairs_hook=reject_duplicate_pairs,
                parse_constant=lambda _: (_ for _ in ()).throw(ValueError()),
            )
        except TypeError, ValueError, UnicodeError, json.JSONDecodeError:
            raise _invalid() from None
        return cls.from_dict(decoded)


def execution_event_fingerprint(event: ExecutionEventV1) -> str:
    """Hash the event DTO without adding a self-referential fingerprint field."""

    if type(event) is not ExecutionEventV1:
        raise _invalid()
    return execution_feedback_hash(event.as_dict())


def bind_execution_event_to_plan(
    event: ExecutionEventV1,
    plan: PlanningPlanV1,
) -> PlanningItemV1:
    """Require exact snapshot, selected item, item DTO, and provenance binding."""

    if type(event) is not ExecutionEventV1 or type(plan) is not PlanningPlanV1:
        raise ExecutionFeedbackSnapshotMismatchError()
    if (
        event.planning_snapshot_id != plan.plan_id
        or event.planning_snapshot_fingerprint != plan.plan_fingerprint
        or event.planning_plan_revision != plan.revision
        or event.planning_source_pack_fingerprint != plan.source_pack_fingerprint
        or event.planning_proposal_fingerprint != plan.proposal_fingerprint
        or event.planning_policy_id != plan.policy_id
        or event.planning_policy_fingerprint != plan.policy_fingerprint
    ):
        raise ExecutionFeedbackSnapshotMismatchError()
    item_by_id = {item.item_id: item for item in plan.items}
    item = item_by_id.get(event.item_id)
    if item is None or event.item_id not in plan.selected_item_ids:
        raise ExecutionFeedbackItemMismatchError()
    if item.kind not in _EXECUTABLE_KINDS:
        raise ExecutionFeedbackNonExecutableError()
    if (
        event.accepted_item_fingerprint != accepted_item_fingerprint(item)
        or event.accepted_item.as_dict() != item.as_dict()
        or event.item_kind is not item.kind
        or event.goal_refs != item.goal_refs
        or event.action_refs != item.action_refs
    ):
        raise ExecutionFeedbackItemMismatchError()
    return item


validate_execution_event_binding = bind_execution_event_to_plan


def create_execution_event(
    plan: PlanningPlanV1,
    *,
    item_id: str,
    event_type: ExecutionEventTypeV1 | str,
    operation_id: str | UUID,
    occurred_at: datetime | str,
    received_at: datetime | str | None = None,
    event_id: UUID | str | None = None,
    actual_effort_minutes: int | None = None,
    effort_precision: ExecutionEffortPrecisionV1 | str = ExecutionEffortPrecisionV1.UNKNOWN,
    actual_result_note: str = "",
    reason_codes: tuple[ExecutionReasonCodeV1 | str, ...] = (),
    deviation_codes: tuple[ExecutionDeviationCodeV1 | str, ...] = (),
    result_disposition: ExecutionResultDispositionV1 | str | None = None,
    void_target_event_id: UUID | str | None = None,
    void_target_event_fingerprint: str | None = None,
    correction_reason: str | None = None,
) -> ExecutionEventV1:
    """Build an event from exact plan data without accepting caller-supplied item text."""

    if type(plan) is not PlanningPlanV1:
        raise ExecutionFeedbackSnapshotMismatchError()
    normalized_item_id = _item_id(item_id)
    item = next(
        (candidate for candidate in plan.items if candidate.item_id == normalized_item_id), None
    )
    if item is None or item.item_id not in plan.selected_item_ids:
        raise ExecutionFeedbackItemMismatchError()
    if item.kind not in _EXECUTABLE_KINDS:
        raise ExecutionFeedbackNonExecutableError()
    event = ExecutionEventV1(
        execution_event_version=EXECUTION_EVENT_VERSION,
        event_id=uuid7() if event_id is None else event_id,
        event_type=event_type,
        operation_id_fingerprint=operation_id_fingerprint(operation_id),
        occurred_at=occurred_at,
        planning_snapshot_id=plan.plan_id,
        planning_snapshot_fingerprint=plan.plan_fingerprint,
        planning_plan_revision=plan.revision,
        planning_source_pack_fingerprint=plan.source_pack_fingerprint,
        planning_proposal_fingerprint=plan.proposal_fingerprint,
        planning_policy_id=plan.policy_id,
        planning_policy_fingerprint=plan.policy_fingerprint,
        item_id=item.item_id,
        accepted_item_fingerprint=accepted_item_fingerprint(item),
        item_kind=cast(PlanningItemKindV1, item.kind),
        accepted_item=item,
        goal_refs=item.goal_refs,
        action_refs=item.action_refs,
        actual_effort_minutes=actual_effort_minutes,
        effort_precision=effort_precision,
        actual_result_note=actual_result_note,
        reason_codes=reason_codes,
        deviation_codes=deviation_codes,
        result_disposition=result_disposition,
        void_target_event_id=void_target_event_id,
        void_target_event_fingerprint=void_target_event_fingerprint,
        correction_reason=correction_reason,
    )
    if received_at is not None:
        validate_execution_event_time(event.occurred_at, received_at=received_at)
    bind_execution_event_to_plan(event, plan)
    return event


def apply_execution_transition(
    state: ExecutionLifecycleStateV1 | str,
    event_type: ExecutionEventTypeV1 | str,
) -> ExecutionLifecycleStateV1:
    """Apply one non-correction event to a closed lifecycle state."""

    current = _enum_value(state, ExecutionLifecycleStateV1)
    event = _enum_value(event_type, ExecutionEventTypeV1)
    transitions: dict[
        tuple[ExecutionLifecycleStateV1, ExecutionEventTypeV1], ExecutionLifecycleStateV1
    ] = {
        (
            ExecutionLifecycleStateV1.NOT_STARTED,
            ExecutionEventTypeV1.START,
        ): ExecutionLifecycleStateV1.IN_PROGRESS,
        (
            ExecutionLifecycleStateV1.IN_PROGRESS,
            ExecutionEventTypeV1.PAUSE,
        ): ExecutionLifecycleStateV1.PAUSED,
        (
            ExecutionLifecycleStateV1.PAUSED,
            ExecutionEventTypeV1.RESUME,
        ): ExecutionLifecycleStateV1.IN_PROGRESS,
        (
            ExecutionLifecycleStateV1.IN_PROGRESS,
            ExecutionEventTypeV1.BLOCK,
        ): ExecutionLifecycleStateV1.BLOCKED,
        (
            ExecutionLifecycleStateV1.PAUSED,
            ExecutionEventTypeV1.BLOCK,
        ): ExecutionLifecycleStateV1.BLOCKED,
        (
            ExecutionLifecycleStateV1.BLOCKED,
            ExecutionEventTypeV1.UNBLOCK,
        ): ExecutionLifecycleStateV1.IN_PROGRESS,
    }
    if event in _TERMINAL_EVENTS and current not in {
        ExecutionLifecycleStateV1.COMPLETED,
        ExecutionLifecycleStateV1.ABANDONED,
    }:
        return (
            ExecutionLifecycleStateV1.COMPLETED
            if event is ExecutionEventTypeV1.COMPLETE
            else ExecutionLifecycleStateV1.ABANDONED
        )
    if event is ExecutionEventTypeV1.VOID:
        raise ExecutionFeedbackLifecycleError()
    try:
        return transitions[(current, event)]
    except KeyError:
        raise ExecutionFeedbackLifecycleError() from None


@dataclass(frozen=True, slots=True)
class ExecutionReplayV1:
    """Deterministic effective replay result for one exact item chain."""

    item_id: str
    state: ExecutionLifecycleStateV1
    effective_events: tuple[ExecutionEventV1, ...]
    voided_event_ids: tuple[UUID, ...]
    correction_events: tuple[ExecutionEventV1, ...]
    latest_event: ExecutionEventV1 | None
    terminal_event: ExecutionEventV1 | None


def replay_execution_events(
    events: tuple[ExecutionEventV1, ...],
    *,
    item_id: str | None = None,
) -> ExecutionReplayV1:
    """Replay append order and validate correction targets and lifecycle rules."""

    if type(events) is not tuple or len(events) > MAX_EXECUTION_EVENTS_PER_ITEM:
        raise ExecutionFeedbackLifecycleError()
    if any(type(event) is not ExecutionEventV1 for event in events):
        raise ExecutionFeedbackLifecycleError()
    normalized_item_id = None if item_id is None else _item_id(item_id)
    if not events:
        return ExecutionReplayV1(
            item_id=normalized_item_id or "",
            state=ExecutionLifecycleStateV1.NOT_STARTED,
            effective_events=(),
            voided_event_ids=(),
            correction_events=(),
            latest_event=None,
            terminal_event=None,
        )
    first = events[0]
    chain_key = (
        first.planning_snapshot_id,
        first.planning_snapshot_fingerprint,
        first.item_id,
        first.accepted_item_fingerprint,
    )
    if normalized_item_id is not None and normalized_item_id != first.item_id:
        raise ExecutionFeedbackLifecycleError()
    seen_ids: set[UUID] = set()
    by_id: dict[UUID, ExecutionEventV1] = {}
    voided_ids: set[UUID] = set()
    corrections: list[ExecutionEventV1] = []
    latest_appended_at: datetime | None = None
    for _index, event in enumerate(events):
        event_key = (
            event.planning_snapshot_id,
            event.planning_snapshot_fingerprint,
            event.item_id,
            event.accepted_item_fingerprint,
        )
        if event_key != chain_key or event.event_id in seen_ids:
            raise ExecutionFeedbackLifecycleError()
        event_id = cast(UUID, event.event_id)
        seen_ids.add(event_id)
        by_id[event_id] = event
        occurred_at = cast(datetime, event.occurred_at)
        if latest_appended_at is not None and occurred_at < latest_appended_at:
            raise ExecutionFeedbackLifecycleError()
        latest_appended_at = occurred_at
        if event.event_type is not ExecutionEventTypeV1.VOID:
            continue
        target_id = event.void_target_event_id
        if target_id is None or cast(UUID, target_id) not in by_id:
            raise ExecutionFeedbackCorrectionError()
        target = by_id[cast(UUID, target_id)]
        if target.event_type is ExecutionEventTypeV1.VOID:
            raise ExecutionFeedbackCorrectionError()
        if event.void_target_event_fingerprint != execution_event_fingerprint(target):
            raise ExecutionFeedbackCorrectionError()
        if cast(UUID, target_id) in voided_ids:
            raise ExecutionFeedbackCorrectionError()
        voided_ids.add(cast(UUID, target_id))
        corrections.append(event)
    effective_events = tuple(
        event
        for event in events
        if event.event_type is not ExecutionEventTypeV1.VOID and event.event_id not in voided_ids
    )
    state = ExecutionLifecycleStateV1.NOT_STARTED
    previous_at: datetime | None = None
    terminal_event: ExecutionEventV1 | None = None
    for event in effective_events:
        occurred_at = cast(datetime, event.occurred_at)
        if previous_at is not None and occurred_at < previous_at:
            raise ExecutionFeedbackLifecycleError()
        previous_at = occurred_at
        state = apply_execution_transition(state, cast(ExecutionEventTypeV1, event.event_type))
        if event.event_type in _TERMINAL_EVENTS:
            terminal_event = event
    return ExecutionReplayV1(
        item_id=first.item_id,
        state=state,
        effective_events=effective_events,
        voided_event_ids=tuple(sorted(voided_ids, key=str)),
        correction_events=tuple(corrections),
        latest_event=effective_events[-1] if effective_events else None,
        terminal_event=terminal_event,
    )


validate_execution_lifecycle = replay_execution_events


def validate_execution_event_for_plan(
    event: ExecutionEventV1,
    plan: PlanningPlanV1,
    *,
    current_plan: PlanningPlanV1 | None = None,
    source_status: ExecutionSourceStatusV1 | str = ExecutionSourceStatusV1.CURRENT,
) -> PlanningItemV1:
    """Bind an event and fail closed for a new start from stale source data."""

    item = bind_execution_event_to_plan(event, plan)
    if event.event_type is not ExecutionEventTypeV1.START:
        return item
    status = _enum_value(source_status, ExecutionSourceStatusV1)
    if (
        status is not ExecutionSourceStatusV1.CURRENT
        or type(current_plan) is not PlanningPlanV1
        or current_plan != plan
    ):
        raise ExecutionFeedbackStaleError()
    return item


validate_execution_start = validate_execution_event_for_plan


__all__ = [
    "EXECUTION_EVENT_FUTURE_SKEW_SECONDS",
    "EXECUTION_EVENT_VERSION",
    "EXECUTION_FEEDBACK_CONTRACT_VERSION",
    "EXECUTION_POLICY_CANONICAL_JSON",
    "EXECUTION_POLICY_FINGERPRINT",
    "EXECUTION_POLICY_ID",
    "MAX_EXECUTION_ACTUAL_EFFORT_MINUTES",
    "MAX_EXECUTION_EVENTS_PER_ITEM",
    "MAX_EXECUTION_EVENT_NOTE_BYTES",
    "MAX_EXECUTION_REASON_CODES",
    "MAX_EXECUTION_STORE_RECORDS",
    "MIN_EXECUTION_EVENT_TIME",
    "ExecutionDeviationCodeV1",
    "ExecutionEffortPrecisionV1",
    "ExecutionEventType",
    "ExecutionEventTypeV1",
    "ExecutionEventV1",
    "ExecutionFeedbackCorrectionError",
    "ExecutionFeedbackError",
    "ExecutionFeedbackInvalidError",
    "ExecutionFeedbackItemMismatchError",
    "ExecutionFeedbackLifecycleError",
    "ExecutionFeedbackNonExecutableError",
    "ExecutionFeedbackPolicyMismatchError",
    "ExecutionFeedbackSnapshotMismatchError",
    "ExecutionFeedbackStaleError",
    "ExecutionLifecycleState",
    "ExecutionLifecycleStateV1",
    "ExecutionReasonCodeV1",
    "ExecutionReplayV1",
    "ExecutionResultDispositionV1",
    "ExecutionSourceStatus",
    "ExecutionSourceStatusV1",
    "PlanningPortfolioSnapshotV1",
    "accepted_item_fingerprint",
    "apply_execution_transition",
    "bind_execution_event_to_plan",
    "create_execution_event",
    "execution_event_fingerprint",
    "execution_feedback_hash",
    "format_execution_timestamp",
    "operation_id_fingerprint",
    "replay_execution_events",
    "validate_execution_event_binding",
    "validate_execution_event_for_plan",
    "validate_execution_event_time",
    "validate_execution_lifecycle",
    "validate_execution_policy",
    "validate_execution_start",
]
