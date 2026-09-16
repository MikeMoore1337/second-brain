"""Provider-free immutable planning context for Personal Planning v1.

Phase 17.1 composes only explicit owner inputs and exact accepted Stage 16
strategy provenance.  It deliberately has no provider, network, vault, Web,
or persistence dependency.  The resulting pack is a deterministic boundary
for the later explicit Planner operation.
"""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from enum import StrEnum
from itertools import pairwise
from typing import Final, cast
from uuid import UUID, uuid7
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from second_brain.application.assistant import (
    AssistantCancelledError,
    AssistantContextKind,
    AssistantError,
    AssistantExplicitContext,
    AssistantProviderFailureError,
    AssistantProviderUnavailableError,
    AssistantReasoningEnvelopeV1,
    AssistantRequest,
    AssistantResultEnvelopeV1,
    AssistantResultKind,
    AssistantTimeoutError,
    BuildAssistant,
    serialize_assistant_reasoning_envelope,
    serialize_assistant_result_envelope,
)
from second_brain.application.executive_strategy import (
    POLICY_FINGERPRINT as STAGE16_POLICY_FINGERPRINT,
)
from second_brain.application.executive_strategy import (
    POLICY_ID as STAGE16_POLICY_ID,
)
from second_brain.application.executive_strategy import (
    ReviewedActionV1,
    StrategySnapshotStateV1,
    StrategySnapshotV1,
    goal_identity_fingerprint,
)
from second_brain.application.growth import GrowthGoalIdentityV1
from second_brain.application.ports import AdvisorPort, CancellationToken
from second_brain.domain.models import parse_rfc3339, parse_uuid7

PlanningHashV1 = str

PLANNING_CONTRACT_VERSION: Final[str] = "personal-planning-v1"
PLANNING_PACK_VERSION: Final[str] = "1"
PLANNING_POLICY_ID: Final[str] = "stage17-personal-planning-v1"
PLANNING_POLICY_CANONICAL_JSON: Final[str] = (
    '{"contract_id":"personal-planning-v1","contract_version":"1",'
    '"item_kinds":["project","milestone","commitment","next_action","hold"],'
    '"result_states":["proposal","insufficient_strategy","stale_strategy",'
    '"source_changed","portfolio_conflict","capacity_missing","capacity_conflict",'
    '"planning_context_insufficient","not_comparable","provider_unavailable",'
    '"provider_abstained","hold_current_plan"],"max_goals":8,'
    '"max_horizon_local_days":31,"provider_policy_id":"stage17-personal-planning-v1",'
    '"source":"accepted-stage16-strategy-only"}'
)
PLANNING_POLICY_FINGERPRINT: Final[PlanningHashV1] = (
    "bb0c2e9ff39f9a2a4298faea703f38f57dedb72eff38cf13aee8c1b9ac7588ac"
)

MAX_PLANNING_GOALS: Final[int] = 8
MAX_PLANNING_HORIZON_DAYS: Final[int] = 31
MAX_PLANNING_CONSTRAINTS: Final[int] = 8
MAX_PLANNING_CONTEXT_BYTES: Final[int] = 4096
MAX_PLANNING_TEXT_BYTES: Final[int] = 4096
MAX_PLANNING_CAVEATS: Final[int] = 8
MAX_PLANNING_CAVEAT_BYTES: Final[int] = 512
MAX_PLANNING_WINDOWS: Final[int] = 16
MAX_PLANNING_WINDOW_TITLE_BYTES: Final[int] = 512
MAX_PLANNING_WINDOW_ID_BYTES: Final[int] = 64
MAX_PLANNING_CAPACITY_MINUTES: Final[int] = 1440
MAX_PLANNING_PACK_BYTES: Final[int] = 256 * 1024
MAX_PLANNING_ITEMS: Final[int] = 32
MAX_PLANNING_ITEM_ID_BYTES: Final[int] = 64
MAX_PLANNING_ITEM_TITLE_BYTES: Final[int] = 256
MAX_PLANNING_ITEM_DESCRIPTION_BYTES: Final[int] = 2048
MAX_PLANNING_ITEM_REFS: Final[int] = 8
MAX_PLANNING_DEPENDENCIES: Final[int] = 8
MAX_PLANNING_REASONS: Final[int] = 8
MAX_PLANNING_PROPOSAL_BYTES: Final[int] = 64 * 1024
MAX_PLANNING_PROVIDER_CONTEXT_BYTES: Final[int] = 16 * 1024
MAX_PLANNING_PROVIDER_CONTEXT_PART_BYTES: Final[int] = 900

_RAW_HASH_PATTERN: Final[re.Pattern[str]] = re.compile(r"[0-9a-f]{64}\Z", re.ASCII)
_GROWTH_HASH_PATTERN: Final[re.Pattern[str]] = re.compile(r"sha256:[0-9a-f]{64}\Z", re.ASCII)
_ACTION_ID_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"[A-Za-z0-9][A-Za-z0-9._:-]{0,63}\Z", re.ASCII
)
_LOCAL_DATE_PATTERN: Final[re.Pattern[str]] = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}\Z", re.ASCII)
_LOCAL_DATETIME_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}\Z", re.ASCII
)
_PROHIBITED_PLANNING_TEXT_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"(?:https?://|www\.|file://|[A-Za-z]:[\\/]|"
    r"\b(?:powershell|cmd(?:\.exe)?|bash|shell|curl|wget|python|git|npm|docker|ssh|"
    r"api[_ -]?key|access[_ -]?token|password|secret|credential|tool call|browser automation|"
    r"send email|отправить письмо|удалить репозиторий|изменить github)\b)",
    re.IGNORECASE,
)


class PersonalPlanningError(ValueError):
    """Base error for invalid Personal Planning v1 values."""


class PersonalPlanningInvalidError(PersonalPlanningError):
    """A bounded planning value failed the immutable contract."""


class PersonalPlanningPolicyMismatchError(PersonalPlanningError):
    """A value belongs to another Stage 17 or Stage 16 policy."""


class PlanningPackReadinessV1(StrEnum):
    """Closed source-readiness states for a planning pack."""

    EXACT_CURRENT = "exact_current"
    INCOMPLETE = "incomplete"
    CONFLICT = "conflict"
    SOURCE_CHANGED = "source_changed"
    STALE = "stale"
    NOT_COMPARABLE = "not_comparable"


PlanningPackReadiness = PlanningPackReadinessV1


class PlanningResultStateV1(StrEnum):
    """Closed later Planner result states kept with the Phase 17 contract."""

    PROPOSAL = "proposal"
    INSUFFICIENT_STRATEGY = "insufficient_strategy"
    STALE_STRATEGY = "stale_strategy"
    SOURCE_CHANGED = "source_changed"
    PORTFOLIO_CONFLICT = "portfolio_conflict"
    CAPACITY_MISSING = "capacity_missing"
    CAPACITY_CONFLICT = "capacity_conflict"
    PLANNING_CONTEXT_INSUFFICIENT = "planning_context_insufficient"
    NOT_COMPARABLE = "not_comparable"
    PROVIDER_UNAVAILABLE = "provider_unavailable"
    PROVIDER_ABSTAINED = "provider_abstained"
    HOLD_CURRENT_PLAN = "hold_current_plan"


class PlanningItemKindV1(StrEnum):
    """Closed non-executable planning item kinds."""

    PROJECT = "project"
    MILESTONE = "milestone"
    COMMITMENT = "commitment"
    NEXT_ACTION = "next_action"
    HOLD = "hold"


class PlanningWindowKindV1(StrEnum):
    """Owner-supplied planning-window kinds; neither is a Calendar event."""

    FIXED_COMMITMENT = "fixed_commitment"
    UNAVAILABLE = "unavailable"


class PlanningEffortSourceV1(StrEnum):
    """Effort authority marker for a derived provider proposal."""

    PROVIDER_PROPOSED = "provider_proposed"


class PlanningPlannerError(RuntimeError):
    """Base safe error at the explicit Stage 17 Planner boundary."""


class PlanningPlannerCancelledError(PlanningPlannerError):
    """The explicit planning operation was cancelled."""


class PlanningProviderUnavailableError(PlanningPlannerError):
    """The already-approved Advisor boundary cannot serve this plan."""


class PlanningProviderResultInvalidError(PlanningPlannerError):
    """The provider output cannot be bound to the closed planning schema."""


class PlanningHumanRequiredError(PlanningPlannerError):
    """The bounded existing Assistant boundary cannot carry this exact pack."""


def _invalid() -> PersonalPlanningInvalidError:
    return PersonalPlanningInvalidError("personal planning value failed validation")


def _policy_mismatch() -> PersonalPlanningPolicyMismatchError:
    return PersonalPlanningPolicyMismatchError("personal planning policy mismatch")


def _enum_value[EnumT: StrEnum](value: object, enum_type: type[EnumT]) -> EnumT:
    if type(value) is enum_type:
        return value
    if type(value) is str:
        try:
            return enum_type(value)
        except ValueError:
            pass
    raise _invalid()


def _has_forbidden_codepoint(value: str) -> bool:
    return any(
        ord(char) < 0x20 or 0x7F <= ord(char) <= 0x9F or unicodedata.category(char) == "Cf"
        for char in value
    )


def _text(value: object, *, limit: int, allow_empty: bool = False) -> str:
    if type(value) is not str or _has_forbidden_codepoint(value):
        raise _invalid()
    normalized = unicodedata.normalize("NFC", value)
    if normalized != value:
        raise _invalid()
    normalized = normalized.strip()
    if not normalized and not allow_empty:
        raise _invalid()
    if _has_forbidden_codepoint(normalized):
        raise _invalid()
    try:
        if len(normalized.encode("utf-8")) > limit:
            raise _invalid()
    except UnicodeEncodeError:
        raise _invalid() from None
    return normalized


def _tuple_texts(
    value: object,
    *,
    maximum: int,
    item_limit: int,
    total_limit: int | None = None,
) -> tuple[str, ...]:
    if type(value) is not tuple or len(value) > maximum:
        raise _invalid()
    normalized = tuple(_text(item, limit=item_limit) for item in value)
    if len(set(normalized)) != len(normalized):
        raise _invalid()
    if (
        total_limit is not None
        and sum(len(item.encode("utf-8")) for item in normalized) > total_limit
    ):
        raise _invalid()
    return normalized


def _uuid7(value: object) -> UUID:
    try:
        return parse_uuid7(value)
    except TypeError, ValueError:
        raise _invalid() from None


def _timestamp(value: object) -> datetime:
    try:
        parsed = parse_rfc3339(value)
    except TypeError, ValueError:
        raise _invalid() from None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise _invalid()
    return parsed.astimezone(UTC)


def _format_timestamp(value: datetime) -> str:
    rendered = value.astimezone(UTC).isoformat(
        timespec="microseconds" if value.microsecond else "seconds"
    )
    return rendered.removesuffix("+00:00") + "Z"


def _raw_hash(value: object) -> PlanningHashV1:
    try:
        encoded = json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
        ).encode("utf-8")
    except TypeError, UnicodeEncodeError, ValueError:
        raise _invalid() from None
    return hashlib.sha256(encoded).hexdigest()


def _raw_digest(value: object) -> PlanningHashV1:
    if type(value) is not str or _RAW_HASH_PATTERN.fullmatch(value) is None:
        raise _invalid()
    return value


def _growth_digest(value: object) -> str:
    if type(value) is not str or _GROWTH_HASH_PATTERN.fullmatch(value) is None:
        raise _invalid()
    return value


def _canonical_bytes(value: object) -> bytes:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
        ).encode("utf-8")
    except TypeError, UnicodeEncodeError, ValueError:
        raise _invalid() from None


def _action_id(value: object) -> str:
    normalized = _text(value, limit=64)
    if _ACTION_ID_PATTERN.fullmatch(normalized) is None:
        raise _invalid()
    return normalized


def _planning_text(value: object, *, limit: int) -> str:
    normalized = _text(value, limit=limit)
    if _PROHIBITED_PLANNING_TEXT_PATTERN.search(normalized) is not None:
        raise _invalid()
    return normalized


def _local_date(value: object) -> str:
    if type(value) is not str or _LOCAL_DATE_PATTERN.fullmatch(value) is None:
        raise _invalid()
    try:
        parsed = date.fromisoformat(value)
    except ValueError:
        raise _invalid() from None
    normalized = parsed.isoformat()
    if normalized != value:
        raise _invalid()
    return normalized


def _local_datetime(value: object) -> str:
    if type(value) is not str or _LOCAL_DATETIME_PATTERN.fullmatch(value) is None:
        raise _invalid()
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        raise _invalid() from None
    if parsed.tzinfo is not None or parsed.second or parsed.microsecond:
        raise _invalid()
    normalized = parsed.isoformat(timespec="minutes")
    if normalized != value:
        raise _invalid()
    return normalized


def _timezone(value: object) -> str:
    normalized = _text(value, limit=128)
    # ``UTC`` is an IANA zone key but is not exposed by the Windows standard
    # tzdata path on every supported runner.  Keep this one canonical key
    # available while requiring ZoneInfo for every regional zone.
    if normalized == "UTC":
        return normalized
    try:
        ZoneInfo(normalized)
    except ZoneInfoNotFoundError, ValueError:
        raise _invalid() from None
    return normalized


def _date_range(start_local: str, end_local: str) -> tuple[date, date]:
    start = date.fromisoformat(start_local)
    end = date.fromisoformat(end_local)
    if end < start or (end - start).days + 1 > MAX_PLANNING_HORIZON_DAYS:
        raise _invalid()
    return start, end


def _wire_list(value: object) -> list[object]:
    if type(value) is not list:
        raise _invalid()
    return value


def _wire_dict(value: object) -> dict[str, object]:
    if type(value) is not dict or any(type(key) is not str for key in value):
        raise _invalid()
    return cast(dict[str, object], value)


def _require_fields(value: object, expected: set[str]) -> dict[str, object]:
    data = _wire_dict(value)
    if set(data) != expected:
        raise _invalid()
    return data


def validate_planning_policy() -> PlanningHashV1:
    """Validate the compiled Stage 17 policy bytes and fingerprint."""

    payload = {
        "contract_id": PLANNING_CONTRACT_VERSION,
        "contract_version": PLANNING_PACK_VERSION,
        "item_kinds": [item.value for item in PlanningItemKindV1],
        "result_states": [state.value for state in PlanningResultStateV1],
        "max_goals": MAX_PLANNING_GOALS,
        "max_horizon_local_days": MAX_PLANNING_HORIZON_DAYS,
        "provider_policy_id": PLANNING_POLICY_ID,
        "source": "accepted-stage16-strategy-only",
    }
    if _canonical_bytes(payload).decode("utf-8") != PLANNING_POLICY_CANONICAL_JSON:
        raise _policy_mismatch()
    fingerprint = _raw_hash(payload)
    if fingerprint != PLANNING_POLICY_FINGERPRINT:
        raise _policy_mismatch()
    return fingerprint


def aggregate_planning_readiness(
    values: Sequence[PlanningPackReadinessV1 | str],
) -> PlanningPackReadinessV1:
    """Apply the contract's fail-closed source-readiness precedence."""

    precedence = (
        PlanningPackReadinessV1.SOURCE_CHANGED,
        PlanningPackReadinessV1.CONFLICT,
        PlanningPackReadinessV1.NOT_COMPARABLE,
        PlanningPackReadinessV1.STALE,
        PlanningPackReadinessV1.INCOMPLETE,
        PlanningPackReadinessV1.EXACT_CURRENT,
    )
    normalized = tuple(_enum_value(value, PlanningPackReadinessV1) for value in values)
    for candidate in precedence:
        if candidate in normalized:
            return candidate
    return PlanningPackReadinessV1.EXACT_CURRENT


@dataclass(frozen=True, slots=True)
class PlanningCapacityEntryV1:
    """One explicit owner-provided available-minute value for one local date."""

    local_date: str
    available_minutes: int

    def __post_init__(self) -> None:
        local_date = _local_date(self.local_date)
        if (
            type(self.available_minutes) is not int
            or isinstance(self.available_minutes, bool)
            or not 0 <= self.available_minutes <= MAX_PLANNING_CAPACITY_MINUTES
        ):
            raise _invalid()
        object.__setattr__(self, "local_date", local_date)

    @property
    def date(self) -> str:
        """Compatibility alias for callers using the shorter field name."""

        return self.local_date

    def as_dict(self) -> dict[str, object]:
        return {"date": self.local_date, "available_minutes": self.available_minutes}

    @classmethod
    def from_dict(cls, value: object) -> PlanningCapacityEntryV1:
        data = _require_fields(value, {"date", "available_minutes"})
        return cls(
            local_date=cast(str, data["date"]),
            available_minutes=cast(int, data["available_minutes"]),
        )


@dataclass(frozen=True, slots=True)
class PlanningWindowV1:
    """One bounded owner planning window using half-open local-time semantics."""

    window_id: str
    kind: PlanningWindowKindV1 | str
    title: str
    start_local: str
    end_local: str

    def __post_init__(self) -> None:
        window_id = _action_id(self.window_id)
        if len(window_id.encode("utf-8")) > MAX_PLANNING_WINDOW_ID_BYTES:
            raise _invalid()
        kind = _enum_value(self.kind, PlanningWindowKindV1)
        title = _text(self.title, limit=MAX_PLANNING_WINDOW_TITLE_BYTES)
        start_local = _local_datetime(self.start_local)
        end_local = _local_datetime(self.end_local)
        if datetime.fromisoformat(end_local) <= datetime.fromisoformat(start_local):
            raise _invalid()
        object.__setattr__(self, "window_id", window_id)
        object.__setattr__(self, "kind", kind)
        object.__setattr__(self, "title", title)
        object.__setattr__(self, "start_local", start_local)
        object.__setattr__(self, "end_local", end_local)

    def as_dict(self) -> dict[str, object]:
        return {
            "window_id": self.window_id,
            "kind": cast(PlanningWindowKindV1, self.kind).value,
            "title": self.title,
            "start_local": self.start_local,
            "end_local": self.end_local,
        }

    @classmethod
    def from_dict(cls, value: object) -> PlanningWindowV1:
        data = _require_fields(
            value,
            {"window_id", "kind", "title", "start_local", "end_local"},
        )
        return cls(
            window_id=cast(str, data["window_id"]),
            kind=cast(PlanningWindowKindV1 | str, data["kind"]),
            title=cast(str, data["title"]),
            start_local=cast(str, data["start_local"]),
            end_local=cast(str, data["end_local"]),
        )


def _validate_windows(
    windows: tuple[PlanningWindowV1, ...],
    *,
    start_local: str,
    end_local: str,
) -> tuple[PlanningWindowV1, ...]:
    if len(windows) > MAX_PLANNING_WINDOWS:
        raise _invalid()
    if any(type(window) is not PlanningWindowV1 for window in windows):
        raise _invalid()
    start_date, end_date = _date_range(start_local, end_local)
    seen: set[str] = set()
    normalized: list[PlanningWindowV1] = []
    for window in windows:
        if window.window_id in seen:
            raise _invalid()
        seen.add(window.window_id)
        window_start = datetime.fromisoformat(window.start_local)
        window_end = datetime.fromisoformat(window.end_local)
        if not start_date <= window_start.date() <= end_date:
            raise _invalid()
        if not start_date <= window_end.date() <= end_date:
            raise _invalid()
        normalized.append(window)
    ordered = sorted(
        normalized, key=lambda item: (item.start_local, item.end_local, item.window_id)
    )
    for previous, current in pairwise(ordered):
        if datetime.fromisoformat(current.start_local) < datetime.fromisoformat(previous.end_local):
            raise _invalid()
    return tuple(normalized)


def _validate_stage16_policy(policy_id: object, policy_fingerprint: object) -> None:
    if policy_id != STAGE16_POLICY_ID or policy_fingerprint != STAGE16_POLICY_FINGERPRINT:
        raise _policy_mismatch()


def _reviewed_action_fingerprint(action: ReviewedActionV1) -> PlanningHashV1:
    if type(action) is not ReviewedActionV1:
        raise _invalid()
    return _raw_hash(action.as_dict())


@dataclass(frozen=True, slots=True)
class PlanningActionRefV1:
    """Exact provenance binding for one reviewed Stage 16 action."""

    goal_source_uuid: UUID | str
    goal_identity_fingerprint: str
    strategy_snapshot_id: UUID | str
    strategy_snapshot_fingerprint: str
    reviewed_action_id: str
    reviewed_action_fingerprint: str
    stage16_policy_id: str
    stage16_policy_fingerprint: str
    reviewed_action: ReviewedActionV1

    def __post_init__(self) -> None:
        goal_uuid = _uuid7(self.goal_source_uuid)
        goal_fp = _growth_digest(self.goal_identity_fingerprint)
        snapshot_id = _uuid7(self.strategy_snapshot_id)
        snapshot_fp = _raw_digest(self.strategy_snapshot_fingerprint)
        action_id = _action_id(self.reviewed_action_id)
        action_fp = _raw_digest(self.reviewed_action_fingerprint)
        _validate_stage16_policy(self.stage16_policy_id, self.stage16_policy_fingerprint)
        if type(self.reviewed_action) is not ReviewedActionV1:
            raise _invalid()
        if self.reviewed_action.action_id != action_id:
            raise _invalid()
        if _reviewed_action_fingerprint(self.reviewed_action) != action_fp:
            raise _invalid()
        object.__setattr__(self, "goal_source_uuid", goal_uuid)
        object.__setattr__(self, "goal_identity_fingerprint", goal_fp)
        object.__setattr__(self, "strategy_snapshot_id", snapshot_id)
        object.__setattr__(self, "strategy_snapshot_fingerprint", snapshot_fp)
        object.__setattr__(self, "reviewed_action_id", action_id)
        object.__setattr__(self, "reviewed_action_fingerprint", action_fp)
        object.__setattr__(self, "stage16_policy_id", STAGE16_POLICY_ID)
        object.__setattr__(self, "stage16_policy_fingerprint", STAGE16_POLICY_FINGERPRINT)

    def as_dict(self) -> dict[str, object]:
        return {
            "goal_source_uuid": str(self.goal_source_uuid),
            "goal_identity_fingerprint": self.goal_identity_fingerprint,
            "strategy_snapshot_id": str(self.strategy_snapshot_id),
            "strategy_snapshot_fingerprint": self.strategy_snapshot_fingerprint,
            "reviewed_action_id": self.reviewed_action_id,
            "reviewed_action_fingerprint": self.reviewed_action_fingerprint,
            "stage16_policy_id": self.stage16_policy_id,
            "stage16_policy_fingerprint": self.stage16_policy_fingerprint,
            "reviewed_action": self.reviewed_action.as_dict(),
        }

    @classmethod
    def from_dict(cls, value: object) -> PlanningActionRefV1:
        data = _require_fields(
            value,
            {
                "goal_source_uuid",
                "goal_identity_fingerprint",
                "strategy_snapshot_id",
                "strategy_snapshot_fingerprint",
                "reviewed_action_id",
                "reviewed_action_fingerprint",
                "stage16_policy_id",
                "stage16_policy_fingerprint",
                "reviewed_action",
            },
        )
        return cls(
            goal_source_uuid=cast(UUID | str, data["goal_source_uuid"]),
            goal_identity_fingerprint=cast(str, data["goal_identity_fingerprint"]),
            strategy_snapshot_id=cast(UUID | str, data["strategy_snapshot_id"]),
            strategy_snapshot_fingerprint=cast(str, data["strategy_snapshot_fingerprint"]),
            reviewed_action_id=cast(str, data["reviewed_action_id"]),
            reviewed_action_fingerprint=cast(str, data["reviewed_action_fingerprint"]),
            stage16_policy_id=cast(str, data["stage16_policy_id"]),
            stage16_policy_fingerprint=cast(str, data["stage16_policy_fingerprint"]),
            reviewed_action=ReviewedActionV1.from_dict(data["reviewed_action"]),
        )


@dataclass(frozen=True, slots=True)
class PlanningGoalSelectionV1:
    """Explicit owner selection of one Goal, snapshot, and reviewed actions."""

    goal: GrowthGoalIdentityV1
    goal_text: str
    strategy_snapshot: StrategySnapshotV1
    selected_action_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        if type(self.goal) is not GrowthGoalIdentityV1:
            raise _invalid()
        if type(self.strategy_snapshot) is not StrategySnapshotV1:
            raise _invalid()
        goal_text = _text(self.goal_text, limit=MAX_PLANNING_TEXT_BYTES)
        selected_action_ids = tuple(_action_id(value) for value in self.selected_action_ids)
        if type(self.selected_action_ids) is not tuple or not selected_action_ids:
            raise _invalid()
        if len(selected_action_ids) > MAX_PLANNING_GOALS:
            raise _invalid()
        if len(set(selected_action_ids)) != len(selected_action_ids):
            raise _invalid()
        goal_fp = goal_identity_fingerprint(self.goal)
        snapshot = self.strategy_snapshot
        if snapshot.state is not StrategySnapshotStateV1.CURRENT:
            raise _invalid()
        if snapshot.goal_source_uuid != self.goal.source_note_uuid:
            raise _invalid()
        if snapshot.goal_identity_fingerprint != goal_fp:
            raise _invalid()
        available = {action.action_id for action in snapshot.selected_actions}
        if any(action_id not in available for action_id in selected_action_ids):
            raise _invalid()
        object.__setattr__(self, "goal_text", goal_text)
        object.__setattr__(self, "selected_action_ids", selected_action_ids)


@dataclass(frozen=True, slots=True)
class PlanningGoalBindingV1:
    """One ordered portfolio Goal and its exact accepted Stage 16 binding."""

    goal_source_uuid: UUID | str
    goal_identity_fingerprint: str
    goal_text: str
    strategy_snapshot_id: UUID | str
    strategy_snapshot_fingerprint: str
    strategy_sequence: int
    stage16_policy_id: str
    stage16_policy_fingerprint: str
    selected_reviewed_action_refs: tuple[PlanningActionRefV1, ...]

    def __post_init__(self) -> None:
        goal_uuid = _uuid7(self.goal_source_uuid)
        goal_fp = _growth_digest(self.goal_identity_fingerprint)
        goal_text = _text(self.goal_text, limit=MAX_PLANNING_TEXT_BYTES)
        snapshot_id = _uuid7(self.strategy_snapshot_id)
        snapshot_fp = _raw_digest(self.strategy_snapshot_fingerprint)
        if (
            type(self.strategy_sequence) is not int
            or isinstance(self.strategy_sequence, bool)
            or not 1 <= self.strategy_sequence <= (1 << 64) - 1
        ):
            raise _invalid()
        _validate_stage16_policy(self.stage16_policy_id, self.stage16_policy_fingerprint)
        if (
            type(self.selected_reviewed_action_refs) is not tuple
            or not self.selected_reviewed_action_refs
            or len(self.selected_reviewed_action_refs) > MAX_PLANNING_GOALS
            or any(
                type(ref) is not PlanningActionRefV1 for ref in self.selected_reviewed_action_refs
            )
        ):
            raise _invalid()
        refs = tuple(self.selected_reviewed_action_refs)
        if len({ref.reviewed_action_id for ref in refs}) != len(refs):
            raise _invalid()
        for ref in refs:
            if (
                ref.goal_source_uuid != goal_uuid
                or ref.goal_identity_fingerprint != goal_fp
                or ref.strategy_snapshot_id != snapshot_id
                or ref.strategy_snapshot_fingerprint != snapshot_fp
            ):
                raise _invalid()
        object.__setattr__(self, "goal_source_uuid", goal_uuid)
        object.__setattr__(self, "goal_identity_fingerprint", goal_fp)
        object.__setattr__(self, "goal_text", goal_text)
        object.__setattr__(self, "strategy_snapshot_id", snapshot_id)
        object.__setattr__(self, "strategy_snapshot_fingerprint", snapshot_fp)
        object.__setattr__(self, "stage16_policy_id", STAGE16_POLICY_ID)
        object.__setattr__(self, "stage16_policy_fingerprint", STAGE16_POLICY_FINGERPRINT)
        object.__setattr__(self, "selected_reviewed_action_refs", refs)

    def as_dict(self) -> dict[str, object]:
        return {
            "goal_source_uuid": str(self.goal_source_uuid),
            "goal_identity_fingerprint": self.goal_identity_fingerprint,
            "goal_text": self.goal_text,
            "strategy_snapshot_id": str(self.strategy_snapshot_id),
            "strategy_snapshot_fingerprint": self.strategy_snapshot_fingerprint,
            "strategy_sequence": self.strategy_sequence,
            "stage16_policy_id": self.stage16_policy_id,
            "stage16_policy_fingerprint": self.stage16_policy_fingerprint,
            "selected_reviewed_action_refs": [
                ref.as_dict() for ref in self.selected_reviewed_action_refs
            ],
        }

    @classmethod
    def from_dict(cls, value: object) -> PlanningGoalBindingV1:
        data = _require_fields(
            value,
            {
                "goal_source_uuid",
                "goal_identity_fingerprint",
                "goal_text",
                "strategy_snapshot_id",
                "strategy_snapshot_fingerprint",
                "strategy_sequence",
                "stage16_policy_id",
                "stage16_policy_fingerprint",
                "selected_reviewed_action_refs",
            },
        )
        refs = _wire_list(data["selected_reviewed_action_refs"])
        return cls(
            goal_source_uuid=cast(UUID | str, data["goal_source_uuid"]),
            goal_identity_fingerprint=cast(str, data["goal_identity_fingerprint"]),
            goal_text=cast(str, data["goal_text"]),
            strategy_snapshot_id=cast(UUID | str, data["strategy_snapshot_id"]),
            strategy_snapshot_fingerprint=cast(str, data["strategy_snapshot_fingerprint"]),
            strategy_sequence=cast(int, data["strategy_sequence"]),
            stage16_policy_id=cast(str, data["stage16_policy_id"]),
            stage16_policy_fingerprint=cast(str, data["stage16_policy_fingerprint"]),
            selected_reviewed_action_refs=tuple(
                PlanningActionRefV1.from_dict(item) for item in refs
            ),
        )


def _capacity_fingerprint(capacity: tuple[PlanningCapacityEntryV1, ...]) -> PlanningHashV1:
    return _raw_hash([entry.as_dict() for entry in capacity])


def _pack_core(pack: PlanningContextPackV1) -> dict[str, object]:
    return {
        "contract_version": pack.contract_version,
        "pack_version": pack.pack_version,
        "as_of": _format_timestamp(pack.as_of),
        "portfolio": [binding.as_dict() for binding in pack.portfolio],
        "portfolio_order": list(pack.portfolio_order),
        "start_local": pack.start_local,
        "end_local": pack.end_local,
        "timezone": pack.timezone,
        "capacity": [entry.as_dict() for entry in pack.capacity],
        "capacity_fingerprint": pack.capacity_fingerprint,
        "fixed_windows": [window.as_dict() for window in pack.fixed_windows],
        "planning_constraints": list(pack.planning_constraints),
        "planning_context": pack.planning_context,
        "readiness": cast(PlanningPackReadinessV1, pack.readiness).value,
        "pack_caveats": list(pack.pack_caveats),
        "policy_id": pack.policy_id,
        "policy_fingerprint": pack.policy_fingerprint,
    }


@dataclass(frozen=True, slots=True)
class PlanningContextPackV1:
    """Immutable provider-free planning source composition."""

    contract_version: str
    pack_version: str
    as_of: datetime
    portfolio: tuple[PlanningGoalBindingV1, ...]
    portfolio_order: tuple[str, ...]
    start_local: str
    end_local: str
    timezone: str
    capacity: tuple[PlanningCapacityEntryV1, ...]
    capacity_fingerprint: str
    fixed_windows: tuple[PlanningWindowV1, ...]
    planning_constraints: tuple[str, ...]
    planning_context: str
    readiness: PlanningPackReadinessV1 | str
    pack_caveats: tuple[str, ...]
    policy_id: str
    policy_fingerprint: str
    pack_fingerprint: str

    def __post_init__(self) -> None:
        if (
            self.contract_version != PLANNING_CONTRACT_VERSION
            or self.pack_version != PLANNING_PACK_VERSION
        ):
            raise _policy_mismatch()
        validate_planning_policy()
        as_of = _timestamp(self.as_of)
        if type(self.portfolio) is not tuple or not 1 <= len(self.portfolio) <= MAX_PLANNING_GOALS:
            raise _invalid()
        if any(type(binding) is not PlanningGoalBindingV1 for binding in self.portfolio):
            raise _invalid()
        portfolio = tuple(self.portfolio)
        portfolio_order = tuple(_uuid7(value) for value in self.portfolio_order)
        if portfolio_order != tuple(binding.goal_source_uuid for binding in portfolio):
            raise _invalid()
        if len(set(portfolio_order)) != len(portfolio_order):
            raise _invalid()
        start_local = _local_date(self.start_local)
        end_local = _local_date(self.end_local)
        start_date, end_date = _date_range(start_local, end_local)
        timezone = _timezone(self.timezone)
        if type(self.capacity) is not tuple or any(
            type(entry) is not PlanningCapacityEntryV1 for entry in self.capacity
        ):
            raise _invalid()
        capacity = tuple(self.capacity)
        expected_dates = tuple(
            (start_date + timedelta(days=offset)).isoformat()
            for offset in range((end_date - start_date).days + 1)
        )
        if tuple(entry.local_date for entry in capacity) != expected_dates:
            raise _invalid()
        capacity_fingerprint = _raw_digest(self.capacity_fingerprint)
        if capacity_fingerprint != _capacity_fingerprint(capacity):
            raise _invalid()
        if type(self.fixed_windows) is not tuple:
            raise _invalid()
        fixed_windows = _validate_windows(
            tuple(self.fixed_windows),
            start_local=start_local,
            end_local=end_local,
        )
        planning_constraints = _tuple_texts(
            self.planning_constraints,
            maximum=MAX_PLANNING_CONSTRAINTS,
            item_limit=MAX_PLANNING_TEXT_BYTES,
            total_limit=MAX_PLANNING_CONTEXT_BYTES,
        )
        planning_context = _text(
            self.planning_context,
            limit=MAX_PLANNING_CONTEXT_BYTES,
            allow_empty=True,
        )
        readiness = _enum_value(self.readiness, PlanningPackReadinessV1)
        pack_caveats = _tuple_texts(
            self.pack_caveats,
            maximum=MAX_PLANNING_CAVEATS,
            item_limit=MAX_PLANNING_CAVEAT_BYTES,
            total_limit=MAX_PLANNING_CONTEXT_BYTES,
        )
        if readiness is not PlanningPackReadinessV1.EXACT_CURRENT and not pack_caveats:
            raise _invalid()
        if (
            self.policy_id != PLANNING_POLICY_ID
            or self.policy_fingerprint != PLANNING_POLICY_FINGERPRINT
        ):
            raise _policy_mismatch()
        supplied_fingerprint = _raw_digest(self.pack_fingerprint)
        object.__setattr__(self, "as_of", as_of)
        object.__setattr__(self, "portfolio", portfolio)
        object.__setattr__(self, "portfolio_order", tuple(str(value) for value in portfolio_order))
        object.__setattr__(self, "start_local", start_local)
        object.__setattr__(self, "end_local", end_local)
        object.__setattr__(self, "timezone", timezone)
        object.__setattr__(self, "capacity", capacity)
        object.__setattr__(self, "capacity_fingerprint", capacity_fingerprint)
        object.__setattr__(self, "fixed_windows", fixed_windows)
        object.__setattr__(self, "planning_constraints", planning_constraints)
        object.__setattr__(self, "planning_context", planning_context)
        object.__setattr__(self, "readiness", readiness)
        object.__setattr__(self, "pack_caveats", pack_caveats)
        object.__setattr__(self, "policy_id", PLANNING_POLICY_ID)
        object.__setattr__(self, "policy_fingerprint", PLANNING_POLICY_FINGERPRINT)
        expected_fingerprint = _raw_hash(_pack_core(self))
        if supplied_fingerprint != expected_fingerprint:
            raise _invalid()
        object.__setattr__(self, "pack_fingerprint", expected_fingerprint)
        if len(_canonical_bytes(self.as_dict())) > MAX_PLANNING_PACK_BYTES:
            raise _invalid()

    @property
    def source_pack_fingerprint(self) -> PlanningHashV1:
        """Stage16-style alias used when the pack is a downstream source."""

        return self.pack_fingerprint

    def as_dict(self) -> dict[str, object]:
        return {**_pack_core(self), "pack_fingerprint": self.pack_fingerprint}

    def to_json(self) -> str:
        return _canonical_bytes(self.as_dict()).decode("utf-8")

    @classmethod
    def from_dict(cls, value: object) -> PlanningContextPackV1:
        data = _require_fields(
            value,
            {
                "contract_version",
                "pack_version",
                "as_of",
                "portfolio",
                "portfolio_order",
                "start_local",
                "end_local",
                "timezone",
                "capacity",
                "capacity_fingerprint",
                "fixed_windows",
                "planning_constraints",
                "planning_context",
                "readiness",
                "pack_caveats",
                "policy_id",
                "policy_fingerprint",
                "pack_fingerprint",
            },
        )
        portfolio = _wire_list(data["portfolio"])
        portfolio_order = _wire_list(data["portfolio_order"])
        capacity = _wire_list(data["capacity"])
        windows = _wire_list(data["fixed_windows"])
        constraints = _wire_list(data["planning_constraints"])
        caveats = _wire_list(data["pack_caveats"])
        return cls(
            contract_version=cast(str, data["contract_version"]),
            pack_version=cast(str, data["pack_version"]),
            as_of=cast(datetime, data["as_of"]),
            portfolio=tuple(PlanningGoalBindingV1.from_dict(item) for item in portfolio),
            portfolio_order=tuple(cast(str, item) for item in portfolio_order),
            start_local=cast(str, data["start_local"]),
            end_local=cast(str, data["end_local"]),
            timezone=cast(str, data["timezone"]),
            capacity=tuple(PlanningCapacityEntryV1.from_dict(item) for item in capacity),
            capacity_fingerprint=cast(str, data["capacity_fingerprint"]),
            fixed_windows=tuple(PlanningWindowV1.from_dict(item) for item in windows),
            planning_constraints=tuple(cast(str, item) for item in constraints),
            planning_context=cast(str, data["planning_context"]),
            readiness=cast(PlanningPackReadinessV1 | str, data["readiness"]),
            pack_caveats=tuple(cast(str, item) for item in caveats),
            policy_id=cast(str, data["policy_id"]),
            policy_fingerprint=cast(str, data["policy_fingerprint"]),
            pack_fingerprint=cast(str, data["pack_fingerprint"]),
        )

    @classmethod
    def from_json(cls, value: object) -> PlanningContextPackV1:
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
        except TypeError, ValueError, json.JSONDecodeError:
            raise _invalid() from None
        return cls.from_dict(decoded)


@dataclass(frozen=True, slots=True)
class PlanningGoalRefV1:
    """Compact exact Goal identity reference used inside provider proposals."""

    goal_source_uuid: UUID | str
    goal_identity_fingerprint: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "goal_source_uuid", _uuid7(self.goal_source_uuid))
        object.__setattr__(
            self,
            "goal_identity_fingerprint",
            _growth_digest(self.goal_identity_fingerprint),
        )

    def as_dict(self) -> dict[str, object]:
        return {
            "goal_source_uuid": str(self.goal_source_uuid),
            "goal_identity_fingerprint": self.goal_identity_fingerprint,
        }

    @classmethod
    def from_dict(cls, value: object) -> PlanningGoalRefV1:
        data = _require_fields(value, {"goal_source_uuid", "goal_identity_fingerprint"})
        return cls(
            goal_source_uuid=cast(UUID | str, data["goal_source_uuid"]),
            goal_identity_fingerprint=cast(str, data["goal_identity_fingerprint"]),
        )


@dataclass(frozen=True, slots=True)
class PlanningActionBindingV1:
    """Compact exact reviewed-action identity used inside a proposal item."""

    goal_source_uuid: UUID | str
    goal_identity_fingerprint: str
    strategy_snapshot_id: UUID | str
    strategy_snapshot_fingerprint: str
    reviewed_action_id: str
    reviewed_action_fingerprint: str
    stage16_policy_id: str
    stage16_policy_fingerprint: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "goal_source_uuid", _uuid7(self.goal_source_uuid))
        object.__setattr__(
            self,
            "goal_identity_fingerprint",
            _growth_digest(self.goal_identity_fingerprint),
        )
        object.__setattr__(self, "strategy_snapshot_id", _uuid7(self.strategy_snapshot_id))
        object.__setattr__(
            self,
            "strategy_snapshot_fingerprint",
            _raw_digest(self.strategy_snapshot_fingerprint),
        )
        object.__setattr__(self, "reviewed_action_id", _action_id(self.reviewed_action_id))
        object.__setattr__(
            self,
            "reviewed_action_fingerprint",
            _raw_digest(self.reviewed_action_fingerprint),
        )
        _validate_stage16_policy(self.stage16_policy_id, self.stage16_policy_fingerprint)
        object.__setattr__(self, "stage16_policy_id", STAGE16_POLICY_ID)
        object.__setattr__(self, "stage16_policy_fingerprint", STAGE16_POLICY_FINGERPRINT)

    @classmethod
    def from_action_ref(cls, ref: PlanningActionRefV1) -> PlanningActionBindingV1:
        if type(ref) is not PlanningActionRefV1:
            raise _invalid()
        return cls(
            goal_source_uuid=ref.goal_source_uuid,
            goal_identity_fingerprint=ref.goal_identity_fingerprint,
            strategy_snapshot_id=ref.strategy_snapshot_id,
            strategy_snapshot_fingerprint=ref.strategy_snapshot_fingerprint,
            reviewed_action_id=ref.reviewed_action_id,
            reviewed_action_fingerprint=ref.reviewed_action_fingerprint,
            stage16_policy_id=ref.stage16_policy_id,
            stage16_policy_fingerprint=ref.stage16_policy_fingerprint,
        )

    def identity_key(self) -> tuple[str, str, str, str, str, str]:
        return (
            str(self.goal_source_uuid),
            self.goal_identity_fingerprint,
            str(self.strategy_snapshot_id),
            self.strategy_snapshot_fingerprint,
            self.reviewed_action_id,
            self.reviewed_action_fingerprint,
        )

    def as_dict(self) -> dict[str, object]:
        return {
            "goal_source_uuid": str(self.goal_source_uuid),
            "goal_identity_fingerprint": self.goal_identity_fingerprint,
            "strategy_snapshot_id": str(self.strategy_snapshot_id),
            "strategy_snapshot_fingerprint": self.strategy_snapshot_fingerprint,
            "reviewed_action_id": self.reviewed_action_id,
            "reviewed_action_fingerprint": self.reviewed_action_fingerprint,
            "stage16_policy_id": self.stage16_policy_id,
            "stage16_policy_fingerprint": self.stage16_policy_fingerprint,
        }

    @classmethod
    def from_dict(cls, value: object) -> PlanningActionBindingV1:
        data = _require_fields(
            value,
            {
                "goal_source_uuid",
                "goal_identity_fingerprint",
                "strategy_snapshot_id",
                "strategy_snapshot_fingerprint",
                "reviewed_action_id",
                "reviewed_action_fingerprint",
                "stage16_policy_id",
                "stage16_policy_fingerprint",
            },
        )
        return cls(
            goal_source_uuid=cast(UUID | str, data["goal_source_uuid"]),
            goal_identity_fingerprint=cast(str, data["goal_identity_fingerprint"]),
            strategy_snapshot_id=cast(UUID | str, data["strategy_snapshot_id"]),
            strategy_snapshot_fingerprint=cast(str, data["strategy_snapshot_fingerprint"]),
            reviewed_action_id=cast(str, data["reviewed_action_id"]),
            reviewed_action_fingerprint=cast(str, data["reviewed_action_fingerprint"]),
            stage16_policy_id=cast(str, data["stage16_policy_id"]),
            stage16_policy_fingerprint=cast(str, data["stage16_policy_fingerprint"]),
        )


def _optional_local_datetime(value: object | None) -> str | None:
    if value is None:
        return None
    return _local_datetime(value)


@dataclass(frozen=True, slots=True)
class PlanningItemV1:
    """One bounded non-executable item proposed by the approved provider."""

    item_id: str
    kind: PlanningItemKindV1 | str
    title: str
    description: str
    goal_refs: tuple[PlanningGoalRefV1, ...]
    action_refs: tuple[PlanningActionBindingV1, ...]
    parent_item_id: str | None
    target_start_local: str | None
    target_end_local: str | None
    effort_minutes: int
    effort_source: PlanningEffortSourceV1 | str
    dependency_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        item_id = _action_id(self.item_id)
        if len(item_id.encode("utf-8")) > MAX_PLANNING_ITEM_ID_BYTES:
            raise _invalid()
        kind = _enum_value(self.kind, PlanningItemKindV1)
        title = _planning_text(self.title, limit=MAX_PLANNING_ITEM_TITLE_BYTES)
        description = _planning_text(self.description, limit=MAX_PLANNING_ITEM_DESCRIPTION_BYTES)
        if (
            type(self.goal_refs) is not tuple
            or not 1 <= len(self.goal_refs) <= MAX_PLANNING_ITEM_REFS
        ):
            raise _invalid()
        if any(type(ref) is not PlanningGoalRefV1 for ref in self.goal_refs):
            raise _invalid()
        goal_refs = tuple(self.goal_refs)
        goal_keys = {(ref.goal_source_uuid, ref.goal_identity_fingerprint) for ref in goal_refs}
        if len(goal_keys) != len(goal_refs):
            raise _invalid()
        if (
            type(self.action_refs) is not tuple
            or not 1 <= len(self.action_refs) <= MAX_PLANNING_ITEM_REFS
            or any(type(ref) is not PlanningActionBindingV1 for ref in self.action_refs)
        ):
            raise _invalid()
        action_refs = tuple(self.action_refs)
        if len({ref.identity_key() for ref in action_refs}) != len(action_refs):
            raise _invalid()
        parent_item_id = None if self.parent_item_id is None else _action_id(self.parent_item_id)
        target_start_local = _optional_local_datetime(self.target_start_local)
        target_end_local = _optional_local_datetime(self.target_end_local)
        if (target_start_local is None) != (target_end_local is None):
            raise _invalid()
        if (
            target_start_local is not None
            and target_end_local is not None
            and (
                datetime.fromisoformat(target_end_local)
                <= datetime.fromisoformat(target_start_local)
            )
        ):
            raise _invalid()
        if (
            type(self.effort_minutes) is not int
            or isinstance(self.effort_minutes, bool)
            or not 0 <= self.effort_minutes <= MAX_PLANNING_CAPACITY_MINUTES
        ):
            raise _invalid()
        effort_source = _enum_value(self.effort_source, PlanningEffortSourceV1)
        if (
            kind
            in {
                PlanningItemKindV1.PROJECT,
                PlanningItemKindV1.MILESTONE,
                PlanningItemKindV1.HOLD,
            }
            and self.effort_minutes != 0
        ):
            raise _invalid()
        if kind in {PlanningItemKindV1.COMMITMENT, PlanningItemKindV1.NEXT_ACTION} and not (
            1 <= self.effort_minutes <= MAX_PLANNING_CAPACITY_MINUTES
        ):
            raise _invalid()
        if (
            type(self.dependency_ids) is not tuple
            or len(self.dependency_ids) > MAX_PLANNING_DEPENDENCIES
        ):
            raise _invalid()
        dependency_ids = tuple(_action_id(value) for value in self.dependency_ids)
        if len(set(dependency_ids)) != len(dependency_ids) or item_id in dependency_ids:
            raise _invalid()
        object.__setattr__(self, "item_id", item_id)
        object.__setattr__(self, "kind", kind)
        object.__setattr__(self, "title", title)
        object.__setattr__(self, "description", description)
        object.__setattr__(self, "goal_refs", goal_refs)
        object.__setattr__(self, "action_refs", action_refs)
        object.__setattr__(self, "parent_item_id", parent_item_id)
        object.__setattr__(self, "target_start_local", target_start_local)
        object.__setattr__(self, "target_end_local", target_end_local)
        object.__setattr__(self, "effort_source", effort_source)
        object.__setattr__(self, "dependency_ids", dependency_ids)

    def as_dict(self) -> dict[str, object]:
        return {
            "item_id": self.item_id,
            "kind": cast(PlanningItemKindV1, self.kind).value,
            "title": self.title,
            "description": self.description,
            "goal_refs": [ref.as_dict() for ref in self.goal_refs],
            "action_refs": [ref.as_dict() for ref in self.action_refs],
            "parent_item_id": self.parent_item_id,
            "target_start_local": self.target_start_local,
            "target_end_local": self.target_end_local,
            "effort_minutes": self.effort_minutes,
            "effort_source": cast(PlanningEffortSourceV1, self.effort_source).value,
            "dependency_ids": list(self.dependency_ids),
        }

    @classmethod
    def from_dict(cls, value: object) -> PlanningItemV1:
        data = _require_fields(
            value,
            {
                "item_id",
                "kind",
                "title",
                "description",
                "goal_refs",
                "action_refs",
                "parent_item_id",
                "target_start_local",
                "target_end_local",
                "effort_minutes",
                "effort_source",
                "dependency_ids",
            },
        )
        goal_refs = _wire_list(data["goal_refs"])
        action_refs = _wire_list(data["action_refs"])
        dependency_ids = _wire_list(data["dependency_ids"])
        return cls(
            item_id=cast(str, data["item_id"]),
            kind=cast(PlanningItemKindV1 | str, data["kind"]),
            title=cast(str, data["title"]),
            description=cast(str, data["description"]),
            goal_refs=tuple(PlanningGoalRefV1.from_dict(item) for item in goal_refs),
            action_refs=tuple(PlanningActionBindingV1.from_dict(item) for item in action_refs),
            parent_item_id=cast(str | None, data["parent_item_id"]),
            target_start_local=cast(str | None, data["target_start_local"]),
            target_end_local=cast(str | None, data["target_end_local"]),
            effort_minutes=cast(int, data["effort_minutes"]),
            effort_source=cast(PlanningEffortSourceV1 | str, data["effort_source"]),
            dependency_ids=tuple(cast(str, item) for item in dependency_ids),
        )


def _planning_proposal_core(proposal: PlanningProposalV1) -> dict[str, object]:
    return {
        "proposal_id": str(proposal.proposal_id),
        "proposal_version": proposal.proposal_version,
        "result_state": cast(PlanningResultStateV1, proposal.result_state).value,
        "as_of": _format_timestamp(proposal.as_of),
        "source_pack_fingerprint": proposal.source_pack_fingerprint,
        "provider_envelope_fingerprint": proposal.provider_envelope_fingerprint,
        "provider_result_fingerprint": proposal.provider_result_fingerprint,
        "policy_id": proposal.policy_id,
        "policy_fingerprint": proposal.policy_fingerprint,
        "items": [item.as_dict() for item in proposal.items],
        "suggested_order": list(proposal.suggested_order),
        "reasons": list(proposal.reasons),
        "caveats": list(proposal.caveats),
    }


@dataclass(frozen=True, slots=True)
class PlanningProposalV1:
    """Bounded ephemeral provider proposal; never accepted owner state."""

    proposal_id: UUID | str
    proposal_version: str
    result_state: PlanningResultStateV1 | str
    as_of: datetime
    source_pack_fingerprint: str
    provider_envelope_fingerprint: str
    provider_result_fingerprint: str
    policy_id: str
    policy_fingerprint: str
    items: tuple[PlanningItemV1, ...]
    suggested_order: tuple[str, ...]
    reasons: tuple[str, ...]
    caveats: tuple[str, ...]
    proposal_fingerprint: str

    def __post_init__(self) -> None:
        proposal_id = _uuid7(self.proposal_id)
        if self.proposal_version != "1":
            raise _policy_mismatch()
        result_state = _enum_value(self.result_state, PlanningResultStateV1)
        as_of = _timestamp(self.as_of)
        source_pack_fingerprint = _raw_digest(self.source_pack_fingerprint)
        provider_envelope_fingerprint = _raw_digest(self.provider_envelope_fingerprint)
        provider_result_fingerprint = _raw_digest(self.provider_result_fingerprint)
        if (
            self.policy_id != PLANNING_POLICY_ID
            or self.policy_fingerprint != PLANNING_POLICY_FINGERPRINT
        ):
            raise _policy_mismatch()
        if type(self.items) is not tuple or len(self.items) > MAX_PLANNING_ITEMS:
            raise _invalid()
        if any(type(item) is not PlanningItemV1 for item in self.items):
            raise _invalid()
        items = tuple(self.items)
        item_ids = tuple(item.item_id for item in items)
        if len(set(item_ids)) != len(item_ids):
            raise _invalid()
        if (
            type(self.suggested_order) is not tuple
            or len(self.suggested_order) > MAX_PLANNING_ITEMS
        ):
            raise _invalid()
        suggested_order = tuple(_action_id(item_id) for item_id in self.suggested_order)
        if len(set(suggested_order)) != len(suggested_order):
            raise _invalid()
        if result_state is PlanningResultStateV1.PROPOSAL:
            if not items or set(suggested_order) != set(item_ids):
                raise _invalid()
        elif items or suggested_order:
            raise _invalid()
        if type(self.reasons) is not tuple or not 1 <= len(self.reasons) <= MAX_PLANNING_REASONS:
            raise _invalid()
        reasons = tuple(
            _planning_text(item, limit=MAX_PLANNING_ITEM_DESCRIPTION_BYTES) for item in self.reasons
        )
        if type(self.caveats) is not tuple or len(self.caveats) > MAX_PLANNING_REASONS:
            raise _invalid()
        caveats = tuple(
            _planning_text(item, limit=MAX_PLANNING_ITEM_DESCRIPTION_BYTES) for item in self.caveats
        )
        if len(set(reasons)) != len(reasons) or len(set(caveats)) != len(caveats):
            raise _invalid()
        supplied_fingerprint = _raw_digest(self.proposal_fingerprint)
        object.__setattr__(self, "proposal_id", proposal_id)
        object.__setattr__(self, "proposal_version", "1")
        object.__setattr__(self, "result_state", result_state)
        object.__setattr__(self, "as_of", as_of)
        object.__setattr__(self, "source_pack_fingerprint", source_pack_fingerprint)
        object.__setattr__(self, "provider_envelope_fingerprint", provider_envelope_fingerprint)
        object.__setattr__(self, "provider_result_fingerprint", provider_result_fingerprint)
        object.__setattr__(self, "policy_id", PLANNING_POLICY_ID)
        object.__setattr__(self, "policy_fingerprint", PLANNING_POLICY_FINGERPRINT)
        object.__setattr__(self, "items", items)
        object.__setattr__(self, "suggested_order", suggested_order)
        object.__setattr__(self, "reasons", reasons)
        object.__setattr__(self, "caveats", caveats)
        expected_fingerprint = _raw_hash(_planning_proposal_core(self))
        if supplied_fingerprint != expected_fingerprint:
            raise _invalid()
        object.__setattr__(self, "proposal_fingerprint", expected_fingerprint)
        if len(_canonical_bytes(self.as_dict())) > MAX_PLANNING_PROPOSAL_BYTES:
            raise _invalid()

    @property
    def provider_fingerprint(self) -> PlanningHashV1:
        """Compatibility alias for the Assistant result fingerprint."""

        return self.provider_result_fingerprint

    def as_dict(self) -> dict[str, object]:
        return {**_planning_proposal_core(self), "proposal_fingerprint": self.proposal_fingerprint}

    def to_json(self) -> str:
        return _canonical_bytes(self.as_dict()).decode("utf-8")

    @classmethod
    def from_dict(cls, value: object) -> PlanningProposalV1:
        data = _require_fields(
            value,
            {
                "proposal_id",
                "proposal_version",
                "result_state",
                "as_of",
                "source_pack_fingerprint",
                "provider_envelope_fingerprint",
                "provider_result_fingerprint",
                "policy_id",
                "policy_fingerprint",
                "items",
                "suggested_order",
                "reasons",
                "caveats",
                "proposal_fingerprint",
            },
        )
        items = _wire_list(data["items"])
        suggested_order = _wire_list(data["suggested_order"])
        reasons = _wire_list(data["reasons"])
        caveats = _wire_list(data["caveats"])
        return cls(
            proposal_id=cast(UUID | str, data["proposal_id"]),
            proposal_version=cast(str, data["proposal_version"]),
            result_state=cast(PlanningResultStateV1 | str, data["result_state"]),
            as_of=cast(datetime, data["as_of"]),
            source_pack_fingerprint=cast(str, data["source_pack_fingerprint"]),
            provider_envelope_fingerprint=cast(str, data["provider_envelope_fingerprint"]),
            provider_result_fingerprint=cast(str, data["provider_result_fingerprint"]),
            policy_id=cast(str, data["policy_id"]),
            policy_fingerprint=cast(str, data["policy_fingerprint"]),
            items=tuple(PlanningItemV1.from_dict(item) for item in items),
            suggested_order=tuple(cast(str, item) for item in suggested_order),
            reasons=tuple(cast(str, item) for item in reasons),
            caveats=tuple(cast(str, item) for item in caveats),
            proposal_fingerprint=cast(str, data["proposal_fingerprint"]),
        )

    @classmethod
    def from_json(cls, value: object) -> PlanningProposalV1:
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
        except TypeError, ValueError, json.JSONDecodeError:
            raise _invalid() from None
        return cls.from_dict(decoded)


def _load_json_object(value: object) -> dict[str, object]:
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
    except TypeError, ValueError, json.JSONDecodeError:
        raise _invalid() from None
    return _wire_dict(decoded)


def _proposal_action_key(ref: PlanningActionBindingV1) -> tuple[str, str, str, str, str, str]:
    return ref.identity_key()


def _planning_target_is_in_horizon(
    item: PlanningItemV1,
    *,
    start_local: str,
    end_local: str,
) -> None:
    if item.target_start_local is None or item.target_end_local is None:
        return
    start_date, end_date = _date_range(start_local, end_local)
    target_start = datetime.fromisoformat(item.target_start_local)
    target_end = datetime.fromisoformat(item.target_end_local)
    if not start_date <= target_start.date() <= end_date:
        raise _invalid()
    if not start_date <= target_end.date() <= end_date:
        raise _invalid()


def _validate_dependency_dag(items: tuple[PlanningItemV1, ...]) -> None:
    item_ids = {item.item_id for item in items}
    dependencies = {item.item_id: set(item.dependency_ids) for item in items}
    if any(not refs <= item_ids for refs in dependencies.values()):
        raise _invalid()
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(item_id: str) -> None:
        if item_id in visiting:
            raise _invalid()
        if item_id in visited:
            return
        visiting.add(item_id)
        for dependency_id in sorted(dependencies[item_id]):
            visit(dependency_id)
        visiting.remove(item_id)
        visited.add(item_id)

    for item_id in sorted(item_ids):
        visit(item_id)


def validate_planning_proposal(
    value: object,
    *,
    pack: PlanningContextPackV1,
) -> PlanningProposalV1:
    """Bind a proposal only to the exact pack Goal/action identities."""

    if type(value) is not PlanningProposalV1 or type(pack) is not PlanningContextPackV1:
        raise _invalid()
    proposal = PlanningProposalV1.from_dict(value.as_dict())
    validated_pack = validate_planning_context_pack(pack)
    if proposal.source_pack_fingerprint != validated_pack.pack_fingerprint:
        raise _invalid()
    goal_keys = {
        (str(binding.goal_source_uuid), binding.goal_identity_fingerprint)
        for binding in validated_pack.portfolio
    }
    action_keys = {
        _proposal_action_key(PlanningActionBindingV1.from_action_ref(ref))
        for binding in validated_pack.portfolio
        for ref in binding.selected_reviewed_action_refs
    }
    item_by_id = {item.item_id: item for item in proposal.items}
    for item in proposal.items:
        item_goal_keys = {
            (str(ref.goal_source_uuid), ref.goal_identity_fingerprint) for ref in item.goal_refs
        }
        if not item_goal_keys <= goal_keys:
            raise _invalid()
        item_action_keys = {_proposal_action_key(ref) for ref in item.action_refs}
        if not item_action_keys <= action_keys:
            raise _invalid()
        if any(
            (str(ref.goal_source_uuid), ref.goal_identity_fingerprint) not in item_goal_keys
            for ref in item.action_refs
        ):
            raise _invalid()
        if any(
            goal_key
            not in {
                (str(ref.goal_source_uuid), ref.goal_identity_fingerprint)
                for ref in item.action_refs
            }
            for goal_key in item_goal_keys
        ):
            raise _invalid()
        _planning_target_is_in_horizon(
            item,
            start_local=validated_pack.start_local,
            end_local=validated_pack.end_local,
        )
        if item.parent_item_id is not None:
            if item.parent_item_id not in item_by_id or item.parent_item_id == item.item_id:
                raise _invalid()
            parent = item_by_id[item.parent_item_id]
            kind = cast(PlanningItemKindV1, item.kind)
            parent_kind = cast(PlanningItemKindV1, parent.kind)
            if (
                kind is PlanningItemKindV1.MILESTONE
                and parent_kind is not PlanningItemKindV1.PROJECT
            ):
                raise _invalid()
            if kind in {
                PlanningItemKindV1.COMMITMENT,
                PlanningItemKindV1.NEXT_ACTION,
            } and parent_kind not in {
                PlanningItemKindV1.PROJECT,
                PlanningItemKindV1.MILESTONE,
            }:
                raise _invalid()
            if kind is PlanningItemKindV1.PROJECT:
                raise _invalid()
        elif cast(PlanningItemKindV1, item.kind) is PlanningItemKindV1.MILESTONE:
            raise _invalid()
    _validate_dependency_dag(proposal.items)
    return proposal


PLANNING_PROVIDER_TASK: Final[str] = (
    "Составь только ограниченное предложение личного плана по явным данным ниже. "
    "Не придумывай цели, действия, даты, зависимости или доступность. "
    "Верни только JSON с полями result_state, items, suggested_order, reasons и caveats; "
    "каждый item обязан сохранить точные ссылки на Goal и reviewed Stage16 action."
)


def _provider_projection(pack: PlanningContextPackV1) -> dict[str, object]:
    portfolio: list[dict[str, object]] = []
    for binding in pack.portfolio:
        actions: list[dict[str, object]] = []
        for ref in binding.selected_reviewed_action_refs:
            actions.append(
                {
                    "source_alias": "planning.stage16.reviewed_action",
                    "goal_source_uuid": str(ref.goal_source_uuid),
                    "goal_identity_fingerprint": ref.goal_identity_fingerprint,
                    "strategy_snapshot_id": str(ref.strategy_snapshot_id),
                    "strategy_snapshot_fingerprint": ref.strategy_snapshot_fingerprint,
                    "reviewed_action_id": ref.reviewed_action_id,
                    "reviewed_action_fingerprint": ref.reviewed_action_fingerprint,
                    "stage16_policy_id": ref.stage16_policy_id,
                    "stage16_policy_fingerprint": ref.stage16_policy_fingerprint,
                    "reviewed_action": ref.reviewed_action.reviewed.as_dict(),
                }
            )
        portfolio.append(
            {
                "source_alias": "planning.goal.current",
                "goal_source_uuid": str(binding.goal_source_uuid),
                "goal_identity_fingerprint": binding.goal_identity_fingerprint,
                "goal_text": binding.goal_text,
                "strategy_snapshot_id": str(binding.strategy_snapshot_id),
                "strategy_snapshot_fingerprint": binding.strategy_snapshot_fingerprint,
                "strategy_sequence": binding.strategy_sequence,
                "stage16_policy_id": binding.stage16_policy_id,
                "stage16_policy_fingerprint": binding.stage16_policy_fingerprint,
                "selected_reviewed_actions": actions,
            }
        )
    return {
        "source_alias": "planning.inputs.explicit",
        "contract_version": pack.contract_version,
        "pack_version": pack.pack_version,
        "policy_id": pack.policy_id,
        "policy_fingerprint": pack.policy_fingerprint,
        "pack_fingerprint": pack.pack_fingerprint,
        "readiness": cast(PlanningPackReadinessV1, pack.readiness).value,
        "portfolio": portfolio,
        "portfolio_order": list(pack.portfolio_order),
        "start_local": pack.start_local,
        "end_local": pack.end_local,
        "timezone": pack.timezone,
        "capacity": [entry.as_dict() for entry in pack.capacity],
        "capacity_fingerprint": pack.capacity_fingerprint,
        "fixed_windows": [window.as_dict() for window in pack.fixed_windows],
        "planning_constraints": list(pack.planning_constraints),
        "planning_context": pack.planning_context,
        "pack_caveats": list(pack.pack_caveats),
    }


def _chunk_utf8(value: str, *, maximum_bytes: int) -> tuple[str, ...]:
    chunks: list[str] = []
    current: list[str] = []
    current_bytes = 0
    for character in value:
        character_bytes = len(character.encode("utf-8"))
        if current and current_bytes + character_bytes > maximum_bytes:
            chunks.append("".join(current))
            current = []
            current_bytes = 0
        if character_bytes > maximum_bytes:
            raise PlanningHumanRequiredError("planning provider boundary is too small")
        current.append(character)
        current_bytes += character_bytes
    if current:
        chunks.append("".join(current))
    return tuple(chunks)


@dataclass(frozen=True, slots=True)
class PlanningProviderEnvelopeV1:
    """Exact minimized preview bytes shared with the AdvisorPort call."""

    source_pack_fingerprint: str
    assistant_envelope: AssistantReasoningEnvelopeV1
    provider_payload_json: str
    provider_visible_fingerprint: str

    def __post_init__(self) -> None:
        source_pack_fingerprint = _raw_digest(self.source_pack_fingerprint)
        if type(self.assistant_envelope) is not AssistantReasoningEnvelopeV1:
            raise _invalid()
        payload = _load_json_object(self.provider_payload_json)
        canonical_payload = _canonical_bytes(payload).decode("utf-8")
        if canonical_payload != self.provider_payload_json:
            raise _invalid()
        if payload.get("pack_fingerprint") != source_pack_fingerprint:
            raise _invalid()
        if payload.get("source_alias") != "planning.inputs.explicit":
            raise _invalid()
        portfolio = _wire_list(payload.get("portfolio"))
        portfolio_records = tuple(_wire_dict(item) for item in portfolio)
        explicit_goals = tuple(cast(str, record.get("goal_text")) for record in portfolio_records)
        if any(type(goal) is not str for goal in explicit_goals):
            raise _invalid()
        planning_constraints = _wire_list(payload.get("planning_constraints"))
        owner_constraints = tuple(
            f"planning.owner.constraint: {cast(str, constraint)}"
            for constraint in planning_constraints
        )
        if any(type(constraint) is not str for constraint in planning_constraints):
            raise _invalid()
        expected_constraints = (
            f"planning.policy_id: {PLANNING_POLICY_ID}",
            f"planning.pack_fingerprint: {source_pack_fingerprint}",
            *owner_constraints,
        )
        raw_parts = _chunk_utf8(
            self.provider_payload_json,
            maximum_bytes=MAX_PLANNING_PROVIDER_CONTEXT_PART_BYTES,
        )
        expected_context = tuple(
            AssistantExplicitContext(
                kind=AssistantContextKind.FACT,
                text=f"planning.payload.part.{index:02d}/{len(raw_parts):02d}: {part}",
            )
            for index, part in enumerate(raw_parts, start=1)
        )
        if (
            self.assistant_envelope.task != PLANNING_PROVIDER_TASK
            or self.assistant_envelope.options != ()
            or self.assistant_envelope.explicit_goals != explicit_goals
            or self.assistant_envelope.explicit_constraints != expected_constraints
            or self.assistant_envelope.explicit_context != expected_context
        ):
            raise _invalid()
        canonical_bytes = serialize_assistant_reasoning_envelope(self.assistant_envelope)
        supplied_fingerprint = _raw_digest(self.provider_visible_fingerprint)
        expected_fingerprint = hashlib.sha256(canonical_bytes).hexdigest()
        if supplied_fingerprint != expected_fingerprint:
            raise _invalid()
        if len(canonical_bytes) > MAX_PLANNING_PROVIDER_CONTEXT_BYTES + 16 * 1024:
            raise _invalid()
        object.__setattr__(self, "source_pack_fingerprint", source_pack_fingerprint)
        object.__setattr__(self, "provider_visible_fingerprint", expected_fingerprint)

    @property
    def canonical_bytes(self) -> bytes:
        return serialize_assistant_reasoning_envelope(self.assistant_envelope)

    @property
    def canonical_json(self) -> str:
        return self.canonical_bytes.decode("utf-8")

    @property
    def assistant_request(self) -> AssistantRequest:
        return AssistantRequest(
            task=self.assistant_envelope.task,
            options=self.assistant_envelope.options,
            explicit_constraints=self.assistant_envelope.explicit_constraints,
            explicit_goals=self.assistant_envelope.explicit_goals,
            explicit_context=self.assistant_envelope.explicit_context,
            max_context_bytes=64 * 1024,
            max_result_bytes=64 * 1024,
        )

    def as_dict(self) -> dict[str, object]:
        return {
            "source_pack_fingerprint": self.source_pack_fingerprint,
            "provider_payload": _load_json_object(self.provider_payload_json),
            "provider_visible_fingerprint": self.provider_visible_fingerprint,
            "assistant_envelope": json.loads(self.canonical_json),
        }


PlanningPreviewV1 = PlanningProviderEnvelopeV1


def build_planning_provider_envelope(
    pack: PlanningContextPackV1,
) -> PlanningProviderEnvelopeV1:
    """Build the exact owner-previewed bytes without calling or writing anywhere."""

    validated_pack = validate_planning_context_pack(pack)
    payload = _provider_projection(validated_pack)
    provider_payload_json = _canonical_bytes(payload).decode("utf-8")
    if _PROHIBITED_PLANNING_TEXT_PATTERN.search(provider_payload_json) is not None:
        raise PlanningHumanRequiredError("planning input cannot cross the provider boundary")
    if len(provider_payload_json.encode("utf-8")) > MAX_PLANNING_PROVIDER_CONTEXT_BYTES:
        raise PlanningHumanRequiredError("planning provider boundary is too small")
    try:
        explicit_goals = tuple(binding.goal_text for binding in validated_pack.portfolio)
        owner_constraints = tuple(
            f"planning.owner.constraint: {constraint}"
            for constraint in validated_pack.planning_constraints
        )
        explicit_constraints = (
            f"planning.policy_id: {PLANNING_POLICY_ID}",
            f"planning.pack_fingerprint: {validated_pack.pack_fingerprint}",
            *owner_constraints,
        )
        raw_parts = _chunk_utf8(
            provider_payload_json,
            maximum_bytes=MAX_PLANNING_PROVIDER_CONTEXT_PART_BYTES,
        )
        if not raw_parts or len(raw_parts) > 16:
            raise PlanningHumanRequiredError("planning provider context has too many parts")
        context = tuple(
            AssistantExplicitContext(
                kind=AssistantContextKind.FACT,
                text=f"planning.payload.part.{index:02d}/{len(raw_parts):02d}: {part}",
            )
            for index, part in enumerate(raw_parts, start=1)
        )
        request = AssistantRequest(
            task=PLANNING_PROVIDER_TASK,
            options=(),
            explicit_constraints=explicit_constraints,
            explicit_goals=explicit_goals,
            explicit_context=context,
            max_context_bytes=64 * 1024,
            max_result_bytes=64 * 1024,
        )
        assistant_envelope = AssistantReasoningEnvelopeV1(
            task=request.task,
            options=request.options,
            explicit_constraints=request.explicit_constraints,
            explicit_goals=request.explicit_goals,
            explicit_context=request.explicit_context,
        )
        canonical_bytes = serialize_assistant_reasoning_envelope(assistant_envelope)
    except PlanningHumanRequiredError:
        raise
    except AssistantError as error:
        raise PlanningHumanRequiredError(
            "approved Assistant boundary cannot carry this plan"
        ) from error
    except TypeError, ValueError, UnicodeError:
        raise PlanningHumanRequiredError(
            "approved Assistant boundary cannot carry this plan"
        ) from None
    return PlanningProviderEnvelopeV1(
        source_pack_fingerprint=validated_pack.pack_fingerprint,
        assistant_envelope=assistant_envelope,
        provider_payload_json=provider_payload_json,
        provider_visible_fingerprint=hashlib.sha256(canonical_bytes).hexdigest(),
    )


build_planning_preview = build_planning_provider_envelope


def _proposal_reasons(result: AssistantResultEnvelopeV1) -> tuple[str, ...]:
    values = tuple(result.rationale) or ("Провайдер не сформировал предложение.",)
    return values[:MAX_PLANNING_REASONS]


def _proposal_caveats(result: AssistantResultEnvelopeV1) -> tuple[str, ...]:
    return tuple(result.uncertainty)[:MAX_PLANNING_REASONS]


def _build_abstention_proposal(
    *,
    pack: PlanningContextPackV1,
    envelope: PlanningProviderEnvelopeV1,
    result: AssistantResultEnvelopeV1,
    provider_result_fingerprint: str,
    proposal_id: UUID,
    as_of: datetime,
) -> PlanningProposalV1:
    return PlanningProposalV1(
        proposal_id=proposal_id,
        proposal_version="1",
        result_state=PlanningResultStateV1.PROVIDER_ABSTAINED,
        as_of=as_of,
        source_pack_fingerprint=pack.pack_fingerprint,
        provider_envelope_fingerprint=envelope.provider_visible_fingerprint,
        provider_result_fingerprint=provider_result_fingerprint,
        policy_id=PLANNING_POLICY_ID,
        policy_fingerprint=PLANNING_POLICY_FINGERPRINT,
        items=(),
        suggested_order=(),
        reasons=_proposal_reasons(result),
        caveats=_proposal_caveats(result),
        proposal_fingerprint=_raw_hash(
            {
                "proposal_id": str(proposal_id),
                "proposal_version": "1",
                "result_state": PlanningResultStateV1.PROVIDER_ABSTAINED.value,
                "as_of": _format_timestamp(_timestamp(as_of)),
                "source_pack_fingerprint": pack.pack_fingerprint,
                "provider_envelope_fingerprint": envelope.provider_visible_fingerprint,
                "provider_result_fingerprint": provider_result_fingerprint,
                "policy_id": PLANNING_POLICY_ID,
                "policy_fingerprint": PLANNING_POLICY_FINGERPRINT,
                "items": [],
                "suggested_order": [],
                "reasons": list(_proposal_reasons(result)),
                "caveats": list(_proposal_caveats(result)),
            }
        ),
    )


class BuildPersonalPlanner:
    """Execute one explicit Planning operation through the existing AdvisorPort."""

    def __init__(self, advisor: AdvisorPort) -> None:
        self._advisor = advisor

    def execute(
        self,
        pack: PlanningContextPackV1,
        *,
        cancellation: CancellationToken,
        as_of: datetime,
        proposal_id: UUID | str | None = None,
    ) -> PlanningProposalV1:
        """Build one proposal; no provider call occurs before this method."""

        validated_pack = validate_planning_context_pack(pack)
        if validated_pack.readiness is not PlanningPackReadinessV1.EXACT_CURRENT:
            raise PlanningProviderResultInvalidError("planning source is not exact current")
        envelope = build_planning_provider_envelope(validated_pack)
        if cancellation.is_cancelled():
            raise PlanningPlannerCancelledError("planning generation was cancelled")
        try:
            result = BuildAssistant(self._advisor).execute(
                envelope.assistant_request,
                cancellation=cancellation,
            )
        except PlanningPlannerError:
            raise
        except (
            AssistantProviderUnavailableError,
            AssistantProviderFailureError,
            AssistantTimeoutError,
        ):
            raise PlanningProviderUnavailableError("planning advisor is unavailable") from None
        except AssistantCancelledError:
            raise PlanningPlannerCancelledError("planning generation was cancelled") from None
        except AssistantError:
            raise PlanningProviderResultInvalidError("planning advisor result is invalid") from None
        if cancellation.is_cancelled():
            raise PlanningPlannerCancelledError("planning generation was cancelled")
        provider_result_fingerprint = hashlib.sha256(
            serialize_assistant_result_envelope(result)
        ).hexdigest()
        identifier = uuid7() if proposal_id is None else _uuid7(proposal_id)
        if cast(AssistantResultKind, result.kind) is not AssistantResultKind.RECOMMENDATION:
            try:
                return _build_abstention_proposal(
                    pack=validated_pack,
                    envelope=envelope,
                    result=result,
                    provider_result_fingerprint=provider_result_fingerprint,
                    proposal_id=identifier,
                    as_of=as_of,
                )
            except PersonalPlanningError as error:
                raise PlanningProviderResultInvalidError(
                    "planning advisor result is invalid"
                ) from error
        if result.recommendation is None:
            raise PlanningProviderResultInvalidError("planning advisor result is invalid")
        try:
            draft = _load_json_object(result.recommendation)
            expected = {"result_state", "items", "suggested_order", "reasons", "caveats"}
            if set(draft) != expected:
                raise _invalid()
            items = _wire_list(draft["items"])
            suggested_order = _wire_list(draft["suggested_order"])
            reasons = _wire_list(draft["reasons"])
            caveats = _wire_list(draft["caveats"])
            draft_result_state = _enum_value(draft["result_state"], PlanningResultStateV1)
            if draft_result_state is not PlanningResultStateV1.PROPOSAL:
                raise _invalid()
            proposal_core = {
                "proposal_id": str(identifier),
                "proposal_version": "1",
                "result_state": draft_result_state.value,
                "as_of": _format_timestamp(_timestamp(as_of)),
                "source_pack_fingerprint": validated_pack.pack_fingerprint,
                "provider_envelope_fingerprint": envelope.provider_visible_fingerprint,
                "provider_result_fingerprint": provider_result_fingerprint,
                "policy_id": PLANNING_POLICY_ID,
                "policy_fingerprint": PLANNING_POLICY_FINGERPRINT,
                "items": [PlanningItemV1.from_dict(item).as_dict() for item in items],
                "suggested_order": [cast(str, item) for item in suggested_order],
                "reasons": [cast(str, item) for item in reasons],
                "caveats": [cast(str, item) for item in caveats],
            }
            proposal = PlanningProposalV1(
                proposal_id=identifier,
                proposal_version="1",
                result_state=draft_result_state,
                as_of=as_of,
                source_pack_fingerprint=validated_pack.pack_fingerprint,
                provider_envelope_fingerprint=envelope.provider_visible_fingerprint,
                provider_result_fingerprint=provider_result_fingerprint,
                policy_id=PLANNING_POLICY_ID,
                policy_fingerprint=PLANNING_POLICY_FINGERPRINT,
                items=tuple(PlanningItemV1.from_dict(item) for item in items),
                suggested_order=tuple(cast(str, item) for item in suggested_order),
                reasons=tuple(cast(str, item) for item in reasons),
                caveats=tuple(cast(str, item) for item in caveats),
                proposal_fingerprint=_raw_hash(proposal_core),
            )
            return validate_planning_proposal(proposal, pack=validated_pack)
        except PersonalPlanningError as error:
            raise PlanningProviderResultInvalidError(
                "planning advisor result is invalid"
            ) from error
        except TypeError, ValueError, UnicodeError:
            raise PlanningProviderResultInvalidError("planning advisor result is invalid") from None


PersonalPlanner = BuildPersonalPlanner


def _build_action_ref(
    *,
    goal: GrowthGoalIdentityV1,
    snapshot: StrategySnapshotV1,
    action: ReviewedActionV1,
) -> PlanningActionRefV1:
    return PlanningActionRefV1(
        goal_source_uuid=goal.source_note_uuid,
        goal_identity_fingerprint=goal_identity_fingerprint(goal),
        strategy_snapshot_id=snapshot.snapshot_id,
        strategy_snapshot_fingerprint=snapshot.snapshot_fingerprint,
        reviewed_action_id=action.action_id,
        reviewed_action_fingerprint=_reviewed_action_fingerprint(action),
        stage16_policy_id=STAGE16_POLICY_ID,
        stage16_policy_fingerprint=STAGE16_POLICY_FINGERPRINT,
        reviewed_action=action,
    )


def build_planning_goal_binding(selection: PlanningGoalSelectionV1) -> PlanningGoalBindingV1:
    """Build one exact Goal binding without reading any external source."""

    if type(selection) is not PlanningGoalSelectionV1:
        raise _invalid()
    selected_ids = set(selection.selected_action_ids)
    actions_by_id = {
        action.action_id: action for action in selection.strategy_snapshot.selected_actions
    }
    refs = tuple(
        _build_action_ref(
            goal=selection.goal,
            snapshot=selection.strategy_snapshot,
            action=actions_by_id[action_id],
        )
        for action_id in selection.selected_action_ids
    )
    if set(ref.reviewed_action_id for ref in refs) != selected_ids:
        raise _invalid()
    return PlanningGoalBindingV1(
        goal_source_uuid=selection.goal.source_note_uuid,
        goal_identity_fingerprint=goal_identity_fingerprint(selection.goal),
        goal_text=selection.goal_text,
        strategy_snapshot_id=selection.strategy_snapshot.snapshot_id,
        strategy_snapshot_fingerprint=selection.strategy_snapshot.snapshot_fingerprint,
        strategy_sequence=selection.strategy_snapshot.sequence,
        stage16_policy_id=STAGE16_POLICY_ID,
        stage16_policy_fingerprint=STAGE16_POLICY_FINGERPRINT,
        selected_reviewed_action_refs=refs,
    )


def _normalize_capacity(
    value: Mapping[str, int] | Sequence[PlanningCapacityEntryV1],
) -> tuple[PlanningCapacityEntryV1, ...]:
    if isinstance(value, Mapping):
        entries = tuple(
            PlanningCapacityEntryV1(local_date=local_date, available_minutes=minutes)
            for local_date, minutes in value.items()
        )
    else:
        entries = tuple(value)
    if any(type(entry) is not PlanningCapacityEntryV1 for entry in entries):
        raise _invalid()
    ordered = tuple(sorted(entries, key=lambda entry: entry.local_date))
    if len({entry.local_date for entry in ordered}) != len(ordered):
        raise _invalid()
    return ordered


def build_planning_context_pack(
    selections: tuple[PlanningGoalSelectionV1, ...],
    *,
    start_local: str,
    end_local: str,
    timezone: str,
    available_minutes_by_date: Mapping[str, int] | tuple[PlanningCapacityEntryV1, ...],
    fixed_windows: tuple[PlanningWindowV1, ...] = (),
    planning_constraints: tuple[str, ...] = (),
    planning_context: str = "",
    as_of: datetime | str,
    readiness: PlanningPackReadinessV1 | str = PlanningPackReadinessV1.EXACT_CURRENT,
    pack_caveats: tuple[str, ...] = (),
) -> PlanningContextPackV1:
    """Compose an exact deterministic planning pack from explicit inputs."""

    validate_planning_policy()
    if type(selections) is not tuple or not 1 <= len(selections) <= MAX_PLANNING_GOALS:
        raise _invalid()
    if any(type(selection) is not PlanningGoalSelectionV1 for selection in selections):
        raise _invalid()
    bindings = tuple(build_planning_goal_binding(selection) for selection in selections)
    if len({binding.goal_source_uuid for binding in bindings}) != len(bindings):
        raise _invalid()
    start_local = _local_date(start_local)
    end_local = _local_date(end_local)
    _date_range(start_local, end_local)
    timezone = _timezone(timezone)
    capacity = _normalize_capacity(available_minutes_by_date)
    expected_dates = tuple(
        (date.fromisoformat(start_local) + timedelta(days=offset)).isoformat()
        for offset in range(
            (date.fromisoformat(end_local) - date.fromisoformat(start_local)).days + 1
        )
    )
    if tuple(entry.local_date for entry in capacity) != expected_dates:
        raise _invalid()
    if type(fixed_windows) is not tuple:
        raise _invalid()
    fixed_windows = _validate_windows(
        fixed_windows,
        start_local=start_local,
        end_local=end_local,
    )
    normalized_as_of = _timestamp(as_of)
    normalized_readiness = _enum_value(readiness, PlanningPackReadinessV1)
    pack_caveats = tuple(pack_caveats)
    capacity_fingerprint = _capacity_fingerprint(capacity)
    core = {
        "contract_version": PLANNING_CONTRACT_VERSION,
        "pack_version": PLANNING_PACK_VERSION,
        "as_of": _format_timestamp(normalized_as_of),
        "portfolio": [binding.as_dict() for binding in bindings],
        "portfolio_order": [str(binding.goal_source_uuid) for binding in bindings],
        "start_local": start_local,
        "end_local": end_local,
        "timezone": timezone,
        "capacity": [entry.as_dict() for entry in capacity],
        "capacity_fingerprint": capacity_fingerprint,
        "fixed_windows": [window.as_dict() for window in fixed_windows],
        "planning_constraints": list(planning_constraints),
        "planning_context": planning_context,
        "readiness": normalized_readiness.value,
        "pack_caveats": list(pack_caveats),
        "policy_id": PLANNING_POLICY_ID,
        "policy_fingerprint": PLANNING_POLICY_FINGERPRINT,
    }
    return PlanningContextPackV1(
        contract_version=PLANNING_CONTRACT_VERSION,
        pack_version=PLANNING_PACK_VERSION,
        as_of=normalized_as_of,
        portfolio=bindings,
        portfolio_order=tuple(str(binding.goal_source_uuid) for binding in bindings),
        start_local=start_local,
        end_local=end_local,
        timezone=timezone,
        capacity=capacity,
        capacity_fingerprint=capacity_fingerprint,
        fixed_windows=fixed_windows,
        planning_constraints=tuple(planning_constraints),
        planning_context=planning_context,
        readiness=normalized_readiness,
        pack_caveats=pack_caveats,
        policy_id=PLANNING_POLICY_ID,
        policy_fingerprint=PLANNING_POLICY_FINGERPRINT,
        pack_fingerprint=_raw_hash(core),
    )


def validate_planning_context_pack(value: object) -> PlanningContextPackV1:
    """Revalidate an exact pack before a later provider or store boundary."""

    if type(value) is not PlanningContextPackV1:
        raise _invalid()
    try:
        return PlanningContextPackV1.from_dict(value.as_dict())
    except PersonalPlanningError:
        raise
    except Exception:
        raise _invalid() from None


def serialize_planning_context_pack(pack: PlanningContextPackV1) -> str:
    """Return the one canonical UTF-8 JSON representation of a pack."""

    return validate_planning_context_pack(pack).to_json()


__all__ = [
    "MAX_PLANNING_CAVEATS",
    "MAX_PLANNING_CONSTRAINTS",
    "MAX_PLANNING_CONTEXT_BYTES",
    "MAX_PLANNING_DEPENDENCIES",
    "MAX_PLANNING_GOALS",
    "MAX_PLANNING_HORIZON_DAYS",
    "MAX_PLANNING_ITEMS",
    "MAX_PLANNING_ITEM_DESCRIPTION_BYTES",
    "MAX_PLANNING_ITEM_ID_BYTES",
    "MAX_PLANNING_ITEM_REFS",
    "MAX_PLANNING_ITEM_TITLE_BYTES",
    "MAX_PLANNING_PACK_BYTES",
    "MAX_PLANNING_PROPOSAL_BYTES",
    "MAX_PLANNING_PROVIDER_CONTEXT_BYTES",
    "MAX_PLANNING_PROVIDER_CONTEXT_PART_BYTES",
    "PLANNING_CONTRACT_VERSION",
    "PLANNING_PACK_VERSION",
    "PLANNING_POLICY_CANONICAL_JSON",
    "PLANNING_POLICY_FINGERPRINT",
    "PLANNING_POLICY_ID",
    "BuildPersonalPlanner",
    "PersonalPlanner",
    "PersonalPlanningError",
    "PersonalPlanningInvalidError",
    "PersonalPlanningPolicyMismatchError",
    "PlanningActionBindingV1",
    "PlanningActionRefV1",
    "PlanningCapacityEntryV1",
    "PlanningContextPackV1",
    "PlanningEffortSourceV1",
    "PlanningGoalBindingV1",
    "PlanningGoalRefV1",
    "PlanningGoalSelectionV1",
    "PlanningHashV1",
    "PlanningHumanRequiredError",
    "PlanningItemKindV1",
    "PlanningItemV1",
    "PlanningPackReadiness",
    "PlanningPackReadinessV1",
    "PlanningPlannerCancelledError",
    "PlanningPlannerError",
    "PlanningPreviewV1",
    "PlanningProposalV1",
    "PlanningProviderEnvelopeV1",
    "PlanningProviderResultInvalidError",
    "PlanningProviderUnavailableError",
    "PlanningResultStateV1",
    "PlanningWindowKindV1",
    "PlanningWindowV1",
    "aggregate_planning_readiness",
    "build_planning_context_pack",
    "build_planning_goal_binding",
    "build_planning_preview",
    "build_planning_provider_envelope",
    "serialize_planning_context_pack",
    "validate_planning_context_pack",
    "validate_planning_policy",
    "validate_planning_proposal",
]
