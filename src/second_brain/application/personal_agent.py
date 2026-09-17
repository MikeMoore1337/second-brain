"""Provider-free Mission and Context Pack core for Stage 20.

The module deliberately depends only on exact Stage 17/18 DTOs and a safe
Stage 19 capability projection.  It does not import a provider adapter, a Web
entrypoint, a store, a GitHub connector, or any network-capable code.
"""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import Final, cast
from uuid import UUID

from second_brain.application.action_gateway import (
    ACTION_GATEWAY_CONNECTOR,
    ACTION_GATEWAY_POLICY_FINGERPRINT,
    ACTION_GATEWAY_POLICY_ID,
    ActionKindV1,
)
from second_brain.application.execution_feedback import (
    ExecutionEffortPrecisionV1,
    ExecutionSourceStatusV1,
    accepted_item_fingerprint,
)
from second_brain.application.execution_feedback_projection import (
    ExecutionItemStateV1,
    ExecutionLifecycleStateV1,
    ExecutionWindowRelationV1,
)
from second_brain.application.personal_planning import (
    MAX_PLANNING_ITEM_DESCRIPTION_BYTES,
    MAX_PLANNING_ITEM_REFS,
    MAX_PLANNING_ITEM_TITLE_BYTES,
    PLANNING_POLICY_FINGERPRINT,
    PLANNING_POLICY_ID,
    PlanningActionBindingV1,
    PlanningGoalRefV1,
    PlanningItemKindV1,
    PlanningItemV1,
)
from second_brain.application.personal_planning_store import PlanningPlanV1
from second_brain.domain.models import parse_rfc3339, parse_uuid7

AGENT_MISSION_CONTRACT_VERSION: Final[str] = "personal-agent-mission-v1"
AGENT_CONTEXT_PACK_CONTRACT_VERSION: Final[str] = "personal-agent-context-pack-v1"
AGENT_POLICY_ID: Final[str] = "stage20-personal-agent-v1"
AGENT_POLICY_CANONICAL_JSON: Final[str] = (
    '{"context_pack_max_bytes":262144,"contract":"personal-agent-v1",'
    '"executable_item_kinds":["commitment","next_action"],'
    '"max_constraints":16,"max_context_entries":8,"max_external_targets":16,'
    '"max_mission_task_bytes":4096,"max_selected_items":16,'
    '"max_stage18_caveats":16,"stage17_policy_id":"stage17-personal-planning-v1",'
    '"stage19_policy_id":"github-issues-v1","step_proposal_max":12,"version":"1"}'
)
AGENT_POLICY_FINGERPRINT: Final[str] = hashlib.sha256(
    AGENT_POLICY_CANONICAL_JSON.encode("utf-8")
).hexdigest()

MAX_AGENT_SELECTED_ITEMS: Final[int] = 16
MAX_AGENT_CONSTRAINTS: Final[int] = 16
MAX_AGENT_CONTEXT_ENTRIES: Final[int] = 8
MAX_AGENT_EXTERNAL_TARGETS: Final[int] = 16
MAX_AGENT_MISSION_TASK_BYTES: Final[int] = 4096
MAX_AGENT_CONTEXT_TEXT_BYTES: Final[int] = 4096
MAX_AGENT_CONTEXT_PACK_BYTES: Final[int] = 256 * 1024
MAX_AGENT_CAVEATS: Final[int] = 16
MAX_AGENT_CAVEAT_BYTES: Final[int] = 512
MAX_AGENT_REPOSITORIES: Final[int] = 32
MAX_AGENT_CATALOG_ACTIONS: Final[int] = 3

_HASH_PATTERN: Final[re.Pattern[str]] = re.compile(r"[0-9a-f]{64}\Z", re.ASCII)
_REPOSITORY_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"[A-Za-z0-9][A-Za-z0-9._-]{0,38}/[A-Za-z0-9][A-Za-z0-9._-]{0,99}\Z",
    re.ASCII,
)
_IDENTIFIER_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}\Z", re.ASCII
)

_EXECUTABLE_ITEM_KINDS: Final[frozenset[PlanningItemKindV1]] = frozenset(
    {PlanningItemKindV1.COMMITMENT, PlanningItemKindV1.NEXT_ACTION}
)
_STAGE19_ACTION_KINDS: Final[frozenset[ActionKindV1]] = frozenset(ActionKindV1)
_STAGE19_STATUS_VALUES: Final[frozenset[str]] = frozenset(
    {"disabled", "ready", "credential_unavailable"}
)
_STAGE19_REVERSIBILITY_VALUES: Final[frozenset[str]] = frozenset(
    {"supported", "compensation_only", "not_supported"}
)


class PersonalAgentError(ValueError):
    """Base safe error for the provider-free Stage 20 boundary."""

    code: str = "AGENT_INVALID_REQUEST"

    def __init__(self, message: str | None = None) -> None:
        super().__init__(message or self.code)


class AgentMissionInvalidError(PersonalAgentError):
    """Mission or one of its exact bounded fields is invalid."""

    code = "AGENT_INVALID_REQUEST"


class AgentSourceUnavailableError(PersonalAgentError):
    """The exact accepted Stage 17 source or required Stage 18 projection is missing."""

    code = "AGENT_SOURCE_UNAVAILABLE"


class AgentSourceMismatchError(PersonalAgentError):
    """An input attempted to bind a different exact plan or item."""

    code = "AGENT_SOURCE_MISMATCH"


class AgentSourceStaleError(PersonalAgentError):
    """The exact source exists but is no longer current enough for Stage 20."""

    code = "AGENT_SOURCE_STALE"


class AgentItemNotSelectedError(PersonalAgentError):
    """The requested item is not selected by the exact accepted Stage 17 plan."""

    code = "AGENT_ITEM_NOT_SELECTED"


class AgentNonExecutableItemError(PersonalAgentError):
    """The item kind is outside the Stage 20 executable vocabulary."""

    code = "AGENT_NON_EXECUTABLE_ITEM"


class AgentTargetNotAllowedError(PersonalAgentError):
    """An explicit external target is outside the safe Stage 19 projection."""

    code = "AGENT_TARGET_NOT_ALLOWED"


class AgentPolicyMismatchError(PersonalAgentError):
    """A source or capability projection belongs to another policy."""

    code = "AGENT_POLICY_MISMATCH"


class AgentCapabilityInvalidError(PersonalAgentError):
    """The supplied Stage 19 capability projection is not closed and safe."""

    code = "AGENT_CAPABILITY_INVALID"


class AgentContextPackInvalidError(PersonalAgentError):
    """A Context Pack failed its immutable fingerprint or bounded shape check."""

    code = "AGENT_CONTEXT_PACK_INVALID"


class AgentContextReadinessV1(StrEnum):
    """Closed readiness vocabulary for the deterministic Context Pack."""

    EXACT_CURRENT = "exact_current"
    CAPABILITY_UNAVAILABLE = "capability_unavailable"


class AgentStage19ActionCapabilityV1(StrEnum):
    """Closed Stage 19 action catalog projection marker."""

    GITHUB_ISSUE_CREATE = ActionKindV1.GITHUB_ISSUE_CREATE.value
    GITHUB_ISSUE_COMMENT = ActionKindV1.GITHUB_ISSUE_COMMENT.value
    GITHUB_ISSUE_SET_STATE = ActionKindV1.GITHUB_ISSUE_SET_STATE.value


class AgentStage19RiskV1(StrEnum):
    """Stage 19 risk vocabulary allowed in the Context Pack."""

    CONTROLLED_WRITE = "controlled_write"


class AgentStage19ReversibilityV1(StrEnum):
    """Stage 19 reversibility vocabulary allowed in the Context Pack."""

    SUPPORTED = "supported"
    COMPENSATION_ONLY = "compensation_only"
    NOT_SUPPORTED = "not_supported"


class AgentStage19StatusV1(StrEnum):
    """Safe readiness values exposed by the Stage 19 application boundary."""

    DISABLED = "disabled"
    READY = "ready"
    CREDENTIAL_UNAVAILABLE = "credential_unavailable"


def _canonical_bytes(value: object) -> bytes:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, UnicodeError, ValueError, OverflowError) as exc:
        raise AgentMissionInvalidError() from exc


def personal_agent_hash(value: object) -> str:
    """Return the deterministic SHA-256 used by Stage 20 identities."""

    return hashlib.sha256(_canonical_bytes(value)).hexdigest()


def _uuid7(value: object) -> UUID:
    try:
        return parse_uuid7(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise AgentMissionInvalidError() from exc


def _timestamp(value: object) -> datetime:
    try:
        parsed = parse_rfc3339(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise AgentMissionInvalidError() from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise AgentMissionInvalidError()
    return parsed.astimezone(UTC)


def _wire_timestamp(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _hash(value: object, *, error: type[PersonalAgentError] = AgentMissionInvalidError) -> str:
    if type(value) is not str or _HASH_PATTERN.fullmatch(value) is None:
        raise error()
    return value


def _text(
    value: object,
    *,
    max_bytes: int,
    allow_empty: bool = False,
    error: type[PersonalAgentError] = AgentMissionInvalidError,
) -> str:
    if type(value) is not str:
        raise error()
    normalized = unicodedata.normalize("NFC", value).strip()
    if not allow_empty and not normalized:
        raise error()
    if len(normalized.encode("utf-8")) > max_bytes:
        raise error()
    if any(
        ord(character) < 0x20
        or 0x7F <= ord(character) <= 0x9F
        or unicodedata.category(character) == "Cf"
        for character in normalized
    ):
        raise error()
    return normalized


def _identifier(
    value: object, *, error: type[PersonalAgentError] = AgentMissionInvalidError
) -> str:
    normalized = _text(value, max_bytes=128, error=error)
    if _IDENTIFIER_PATTERN.fullmatch(normalized) is None:
        raise error()
    return normalized


def _repository(
    value: object, *, error: type[PersonalAgentError] = AgentCapabilityInvalidError
) -> str:
    normalized = _text(value, max_bytes=200, error=error)
    if _REPOSITORY_PATTERN.fullmatch(normalized) is None:
        raise error()
    return normalized


def _enum_value[EnumT: StrEnum](
    value: object,
    enum_type: type[EnumT],
    *,
    error: type[PersonalAgentError] = AgentMissionInvalidError,
) -> EnumT:
    if isinstance(value, enum_type):
        return value
    if type(value) is str:
        try:
            return enum_type(value)
        except ValueError:
            pass
    raise error()


def _tuple_texts(
    value: object,
    *,
    max_count: int,
    max_bytes: int,
    error: type[PersonalAgentError] = AgentMissionInvalidError,
) -> tuple[str, ...]:
    if not isinstance(value, tuple):
        raise error()
    if len(value) > max_count:
        raise error()
    return tuple(_text(item, max_bytes=max_bytes, error=error) for item in value)


def _strict_mapping(value: object, allowed: set[str]) -> dict[str, object]:
    if not isinstance(value, Mapping) or any(type(key) is not str for key in value):
        raise AgentMissionInvalidError()
    data = dict(value)
    if set(data) != allowed:
        raise AgentMissionInvalidError()
    return data


@dataclass(frozen=True, slots=True)
class AgentMissionItemBindingV1:
    """Exact identity binding for one owner-selected Stage 17 item."""

    item_id: str
    accepted_item_fingerprint: str
    item_kind: PlanningItemKindV1 | str
    goal_refs: tuple[PlanningGoalRefV1, ...]
    action_refs: tuple[PlanningActionBindingV1, ...]

    def __post_init__(self) -> None:
        item_id = _identifier(self.item_id)
        fingerprint = _hash(self.accepted_item_fingerprint)
        item_kind = _enum_value(self.item_kind, PlanningItemKindV1)
        if item_kind not in _EXECUTABLE_ITEM_KINDS:
            raise AgentNonExecutableItemError()
        if type(self.goal_refs) is not tuple or not self.goal_refs:
            raise AgentMissionInvalidError()
        if type(self.action_refs) is not tuple or not self.action_refs:
            raise AgentMissionInvalidError()
        if any(type(ref) is not PlanningGoalRefV1 for ref in self.goal_refs):
            raise AgentMissionInvalidError()
        if any(type(ref) is not PlanningActionBindingV1 for ref in self.action_refs):
            raise AgentMissionInvalidError()
        if len(
            {(ref.goal_source_uuid, ref.goal_identity_fingerprint) for ref in self.goal_refs}
        ) != len(self.goal_refs):
            raise AgentMissionInvalidError()
        if len({ref.identity_key() for ref in self.action_refs}) != len(self.action_refs):
            raise AgentMissionInvalidError()
        object.__setattr__(self, "item_id", item_id)
        object.__setattr__(self, "accepted_item_fingerprint", fingerprint)
        object.__setattr__(self, "item_kind", item_kind)
        object.__setattr__(self, "goal_refs", tuple(self.goal_refs))
        object.__setattr__(self, "action_refs", tuple(self.action_refs))

    def as_dict(self) -> dict[str, object]:
        return {
            "item_id": self.item_id,
            "accepted_item_fingerprint": self.accepted_item_fingerprint,
            "item_kind": cast(PlanningItemKindV1, self.item_kind).value,
            "goal_refs": [ref.as_dict() for ref in self.goal_refs],
            "action_refs": [ref.as_dict() for ref in self.action_refs],
        }

    @classmethod
    def from_dict(cls, value: object) -> AgentMissionItemBindingV1:
        data = _strict_mapping(
            value,
            {"item_id", "accepted_item_fingerprint", "item_kind", "goal_refs", "action_refs"},
        )
        goals = data["goal_refs"]
        actions = data["action_refs"]
        if type(goals) is not list or type(actions) is not list:
            raise AgentMissionInvalidError()
        return cls(
            item_id=cast(str, data["item_id"]),
            accepted_item_fingerprint=cast(str, data["accepted_item_fingerprint"]),
            item_kind=cast(str, data["item_kind"]),
            goal_refs=tuple(PlanningGoalRefV1.from_dict(item) for item in goals),
            action_refs=tuple(PlanningActionBindingV1.from_dict(item) for item in actions),
        )


@dataclass(frozen=True, slots=True)
class AgentExternalTargetRefV1:
    """Explicit owner-supplied target authority for one Stage 19 action kind."""

    action_kind: AgentStage19ActionCapabilityV1 | str
    repository: str
    issue_number: int | None = None

    def __post_init__(self) -> None:
        action_kind = _enum_value(
            self.action_kind,
            AgentStage19ActionCapabilityV1,
            error=AgentMissionInvalidError,
        )
        repository = _repository(self.repository, error=AgentMissionInvalidError)
        issue_number = self.issue_number
        if issue_number is not None and (
            type(issue_number) is not int
            or isinstance(issue_number, bool)
            or not 1 <= issue_number <= 2**31 - 1
        ):
            raise AgentMissionInvalidError()
        requires_issue = action_kind in {
            AgentStage19ActionCapabilityV1.GITHUB_ISSUE_COMMENT,
            AgentStage19ActionCapabilityV1.GITHUB_ISSUE_SET_STATE,
        }
        if (issue_number is None) == requires_issue:
            raise AgentMissionInvalidError()
        object.__setattr__(self, "action_kind", action_kind)
        object.__setattr__(self, "repository", repository)

    def as_dict(self) -> dict[str, object]:
        return {
            "action_kind": cast(AgentStage19ActionCapabilityV1, self.action_kind).value,
            "repository": self.repository,
            "issue_number": self.issue_number,
        }

    @classmethod
    def from_dict(cls, value: object) -> AgentExternalTargetRefV1:
        data = _strict_mapping(value, {"action_kind", "repository", "issue_number"})
        return cls(
            action_kind=cast(str, data["action_kind"]),
            repository=cast(str, data["repository"]),
            issue_number=cast(int | None, data["issue_number"]),
        )


@dataclass(frozen=True, slots=True)
class AgentMissionV1:
    """Explicit owner mission bound to one exact accepted Stage 17 plan."""

    contract_version: str
    mission_id: UUID | str
    planning_snapshot_id: UUID | str
    planning_snapshot_fingerprint: str
    planning_policy_id: str
    planning_policy_fingerprint: str
    selected_items: tuple[AgentMissionItemBindingV1, ...]
    task: str
    constraints: tuple[str, ...]
    current_context: tuple[str, ...]
    external_targets: tuple[AgentExternalTargetRefV1, ...]
    created_at: datetime | str
    reviewed_at: datetime | str | None = None

    def __post_init__(self) -> None:
        if self.contract_version != AGENT_MISSION_CONTRACT_VERSION:
            raise AgentMissionInvalidError()
        mission_id = _uuid7(self.mission_id)
        snapshot_id = _uuid7(self.planning_snapshot_id)
        snapshot_fingerprint = _hash(self.planning_snapshot_fingerprint)
        if (
            self.planning_policy_id != PLANNING_POLICY_ID
            or self.planning_policy_fingerprint != PLANNING_POLICY_FINGERPRINT
        ):
            raise AgentPolicyMismatchError()
        if (
            type(self.selected_items) is not tuple
            or not 1 <= len(self.selected_items) <= MAX_AGENT_SELECTED_ITEMS
        ):
            raise AgentMissionInvalidError()
        if any(type(item) is not AgentMissionItemBindingV1 for item in self.selected_items):
            raise AgentMissionInvalidError()
        selected_items = tuple(self.selected_items)
        if len({item.item_id for item in selected_items}) != len(selected_items):
            raise AgentMissionInvalidError()
        task = _text(self.task, max_bytes=MAX_AGENT_MISSION_TASK_BYTES)
        constraints = _tuple_texts(
            self.constraints,
            max_count=MAX_AGENT_CONSTRAINTS,
            max_bytes=MAX_AGENT_CONTEXT_TEXT_BYTES,
        )
        current_context = _tuple_texts(
            self.current_context,
            max_count=MAX_AGENT_CONTEXT_ENTRIES,
            max_bytes=MAX_AGENT_CONTEXT_TEXT_BYTES,
        )
        if (
            type(self.external_targets) is not tuple
            or len(self.external_targets) > MAX_AGENT_EXTERNAL_TARGETS
        ):
            raise AgentMissionInvalidError()
        if any(type(target) is not AgentExternalTargetRefV1 for target in self.external_targets):
            raise AgentMissionInvalidError()
        external_targets = tuple(self.external_targets)
        target_keys = tuple(
            (
                cast(AgentStage19ActionCapabilityV1, target.action_kind).value,
                target.repository,
                target.issue_number,
            )
            for target in external_targets
        )
        if len(set(target_keys)) != len(target_keys):
            raise AgentMissionInvalidError()
        created_at = _timestamp(self.created_at)
        reviewed_at = None if self.reviewed_at is None else _timestamp(self.reviewed_at)
        if reviewed_at is not None and reviewed_at < created_at:
            raise AgentMissionInvalidError()
        object.__setattr__(self, "mission_id", mission_id)
        object.__setattr__(self, "planning_snapshot_id", snapshot_id)
        object.__setattr__(self, "planning_snapshot_fingerprint", snapshot_fingerprint)
        object.__setattr__(self, "selected_items", selected_items)
        object.__setattr__(self, "task", task)
        object.__setattr__(self, "constraints", constraints)
        object.__setattr__(self, "current_context", current_context)
        object.__setattr__(self, "external_targets", external_targets)
        object.__setattr__(self, "created_at", created_at)
        object.__setattr__(self, "reviewed_at", reviewed_at)

    def as_dict(self) -> dict[str, object]:
        return {
            "contract_version": self.contract_version,
            "mission_id": str(self.mission_id),
            "planning_snapshot_id": str(self.planning_snapshot_id),
            "planning_snapshot_fingerprint": self.planning_snapshot_fingerprint,
            "planning_policy_id": self.planning_policy_id,
            "planning_policy_fingerprint": self.planning_policy_fingerprint,
            "selected_items": [item.as_dict() for item in self.selected_items],
            "task": self.task,
            "constraints": list(self.constraints),
            "current_context": list(self.current_context),
            "external_targets": [target.as_dict() for target in self.external_targets],
            "created_at": _wire_timestamp(cast(datetime, self.created_at)),
            "reviewed_at": (
                None
                if self.reviewed_at is None
                else _wire_timestamp(cast(datetime, self.reviewed_at))
            ),
        }

    @property
    def fingerprint(self) -> str:
        return personal_agent_hash(self.as_dict())

    @classmethod
    def from_dict(cls, value: object) -> AgentMissionV1:
        data = _strict_mapping(
            value,
            {
                "contract_version",
                "mission_id",
                "planning_snapshot_id",
                "planning_snapshot_fingerprint",
                "planning_policy_id",
                "planning_policy_fingerprint",
                "selected_items",
                "task",
                "constraints",
                "current_context",
                "external_targets",
                "created_at",
                "reviewed_at",
            },
        )
        selected = data["selected_items"]
        constraints = data["constraints"]
        current_context = data["current_context"]
        targets = data["external_targets"]
        if not all(
            type(item) is list for item in (selected, constraints, current_context, targets)
        ):
            raise AgentMissionInvalidError()
        selected_values = cast(list[object], selected)
        constraint_values = cast(list[object], constraints)
        context_values = cast(list[object], current_context)
        target_values = cast(list[object], targets)
        return cls(
            contract_version=cast(str, data["contract_version"]),
            mission_id=cast(UUID | str, data["mission_id"]),
            planning_snapshot_id=cast(UUID | str, data["planning_snapshot_id"]),
            planning_snapshot_fingerprint=cast(str, data["planning_snapshot_fingerprint"]),
            planning_policy_id=cast(str, data["planning_policy_id"]),
            planning_policy_fingerprint=cast(str, data["planning_policy_fingerprint"]),
            selected_items=tuple(
                AgentMissionItemBindingV1.from_dict(item) for item in selected_values
            ),
            task=cast(str, data["task"]),
            constraints=tuple(cast(str, item) for item in constraint_values),
            current_context=tuple(cast(str, item) for item in context_values),
            external_targets=tuple(
                AgentExternalTargetRefV1.from_dict(item) for item in target_values
            ),
            created_at=cast(str, data["created_at"]),
            reviewed_at=cast(str | None, data["reviewed_at"]),
        )


@dataclass(frozen=True, slots=True)
class AgentStage19ActionCapabilityProjectionV1:
    """One safe action kind/risk/reversibility row from Stage 19."""

    action_kind: AgentStage19ActionCapabilityV1 | str
    risk: AgentStage19RiskV1 | str
    reversibility: AgentStage19ReversibilityV1 | str

    def __post_init__(self) -> None:
        action_kind = _enum_value(
            self.action_kind,
            AgentStage19ActionCapabilityV1,
            error=AgentCapabilityInvalidError,
        )
        risk = _enum_value(self.risk, AgentStage19RiskV1, error=AgentCapabilityInvalidError)
        reversibility = _enum_value(
            self.reversibility,
            AgentStage19ReversibilityV1,
            error=AgentCapabilityInvalidError,
        )
        object.__setattr__(self, "action_kind", action_kind)
        object.__setattr__(self, "risk", risk)
        object.__setattr__(self, "reversibility", reversibility)

    def as_dict(self) -> dict[str, str]:
        return {
            "action_kind": cast(AgentStage19ActionCapabilityV1, self.action_kind).value,
            "risk": cast(AgentStage19RiskV1, self.risk).value,
            "reversibility": cast(AgentStage19ReversibilityV1, self.reversibility).value,
        }

    @classmethod
    def from_dict(cls, value: object) -> AgentStage19ActionCapabilityProjectionV1:
        data = _strict_mapping(value, {"action_kind", "risk", "reversibility"})
        return cls(
            action_kind=cast(str, data["action_kind"]),
            risk=cast(str, data["risk"]),
            reversibility=cast(str, data["reversibility"]),
        )


def default_stage19_action_catalog() -> tuple[AgentStage19ActionCapabilityProjectionV1, ...]:
    """Return the closed catalog without importing the Stage 19 adapter."""

    return (
        AgentStage19ActionCapabilityProjectionV1(
            AgentStage19ActionCapabilityV1.GITHUB_ISSUE_CREATE,
            AgentStage19RiskV1.CONTROLLED_WRITE,
            AgentStage19ReversibilityV1.COMPENSATION_ONLY,
        ),
        AgentStage19ActionCapabilityProjectionV1(
            AgentStage19ActionCapabilityV1.GITHUB_ISSUE_COMMENT,
            AgentStage19RiskV1.CONTROLLED_WRITE,
            AgentStage19ReversibilityV1.NOT_SUPPORTED,
        ),
        AgentStage19ActionCapabilityProjectionV1(
            AgentStage19ActionCapabilityV1.GITHUB_ISSUE_SET_STATE,
            AgentStage19RiskV1.CONTROLLED_WRITE,
            AgentStage19ReversibilityV1.SUPPORTED,
        ),
    )


@dataclass(frozen=True, slots=True)
class AgentStage19CapabilityProjectionV1:
    """Credential-free readiness and catalog projection for Stage 20."""

    contract: str
    connector: str
    policy_id: str
    policy_fingerprint: str
    status: AgentStage19StatusV1 | str
    configured: bool
    repositories: tuple[str, ...]
    action_catalog: tuple[AgentStage19ActionCapabilityProjectionV1, ...]
    owner_confirmation_required: bool
    background_execution: bool

    def __post_init__(self) -> None:
        if self.contract != "action-gateway-v1" or self.connector != ACTION_GATEWAY_CONNECTOR:
            raise AgentCapabilityInvalidError()
        if (
            self.policy_id != ACTION_GATEWAY_POLICY_ID
            or self.policy_fingerprint != ACTION_GATEWAY_POLICY_FINGERPRINT
        ):
            raise AgentPolicyMismatchError()
        status = _enum_value(self.status, AgentStage19StatusV1, error=AgentCapabilityInvalidError)
        if type(self.configured) is not bool:
            raise AgentCapabilityInvalidError()
        if type(self.repositories) is not tuple or len(self.repositories) > MAX_AGENT_REPOSITORIES:
            raise AgentCapabilityInvalidError()
        repositories = tuple(_repository(item) for item in self.repositories)
        if len(set(item.casefold() for item in repositories)) != len(repositories):
            raise AgentCapabilityInvalidError()
        if (
            type(self.action_catalog) is not tuple
            or not 1 <= len(self.action_catalog) <= MAX_AGENT_CATALOG_ACTIONS
        ):
            raise AgentCapabilityInvalidError()
        catalog = tuple(self.action_catalog)
        if any(type(item) is not AgentStage19ActionCapabilityProjectionV1 for item in catalog):
            raise AgentCapabilityInvalidError()
        catalog_kinds = tuple(
            cast(AgentStage19ActionCapabilityV1, item.action_kind).value for item in catalog
        )
        if len(set(catalog_kinds)) != len(catalog_kinds) or set(catalog_kinds) != {
            item.value for item in AgentStage19ActionCapabilityV1
        }:
            raise AgentCapabilityInvalidError()
        if self.owner_confirmation_required is not True or self.background_execution is not False:
            raise AgentCapabilityInvalidError()
        object.__setattr__(self, "status", status)
        object.__setattr__(self, "repositories", repositories)
        object.__setattr__(self, "action_catalog", catalog)

    @property
    def ready(self) -> bool:
        return self.status is AgentStage19StatusV1.READY

    def as_dict(self) -> dict[str, object]:
        return {
            "contract": self.contract,
            "connector": self.connector,
            "policy_id": self.policy_id,
            "policy_fingerprint": self.policy_fingerprint,
            "status": cast(AgentStage19StatusV1, self.status).value,
            "configured": self.configured,
            "repositories": list(self.repositories),
            "action_catalog": [item.as_dict() for item in self.action_catalog],
            "owner_confirmation_required": self.owner_confirmation_required,
            "background_execution": self.background_execution,
        }

    @classmethod
    def from_safe_mapping(cls, value: object) -> AgentStage19CapabilityProjectionV1:
        if not isinstance(value, Mapping):
            raise AgentCapabilityInvalidError()
        data = dict(value)
        allowed = {
            "contract",
            "connector",
            "policy_id",
            "policy_fingerprint",
            "status",
            "configured",
            "repositories",
            "action_catalog",
            "owner_confirmation_required",
            "background_execution",
        }
        if set(data) - allowed:
            raise AgentCapabilityInvalidError()
        catalog = data.get("action_catalog")
        repositories = data.get("repositories")
        if type(catalog) is not list or type(repositories) is not list:
            raise AgentCapabilityInvalidError()
        return cls(
            contract=cast(str, data.get("contract")),
            connector=cast(str, data.get("connector")),
            policy_id=cast(str, data.get("policy_id")),
            policy_fingerprint=cast(str, data.get("policy_fingerprint")),
            status=cast(str, data.get("status")),
            configured=cast(bool, data.get("configured")),
            repositories=tuple(cast(str, item) for item in repositories),
            action_catalog=tuple(
                AgentStage19ActionCapabilityProjectionV1.from_dict(item) for item in catalog
            ),
            owner_confirmation_required=cast(bool, data.get("owner_confirmation_required")),
            background_execution=cast(bool, data.get("background_execution")),
        )


@dataclass(frozen=True, slots=True)
class AgentStage18ItemProjectionV1:
    """Neutral selected-item execution projection without history or free text."""

    planning_snapshot_id: UUID
    planning_snapshot_fingerprint: str
    planning_plan_revision: int
    planning_policy_id: str
    planning_policy_fingerprint: str
    item_id: str
    accepted_item_fingerprint: str
    item_kind: PlanningItemKindV1
    source_status: ExecutionSourceStatusV1
    state: ExecutionLifecycleStateV1
    planned_effort_minutes: int
    actual_effort_minutes: int | None
    effort_precision: ExecutionEffortPrecisionV1
    current_block_reasons: tuple[str, ...]
    terminal_disposition: str | None
    deviation_codes: tuple[str, ...]
    window_relation: ExecutionWindowRelationV1
    event_count: int
    caveats: tuple[str, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "planning_snapshot_id", _uuid7(self.planning_snapshot_id))
        object.__setattr__(
            self, "planning_snapshot_fingerprint", _hash(self.planning_snapshot_fingerprint)
        )
        if type(self.planning_plan_revision) is not int or self.planning_plan_revision < 1:
            raise AgentMissionInvalidError()
        if (
            self.planning_policy_id != PLANNING_POLICY_ID
            or self.planning_policy_fingerprint != PLANNING_POLICY_FINGERPRINT
        ):
            raise AgentPolicyMismatchError()
        object.__setattr__(self, "item_id", _identifier(self.item_id))
        object.__setattr__(self, "accepted_item_fingerprint", _hash(self.accepted_item_fingerprint))
        item_kind = _enum_value(self.item_kind, PlanningItemKindV1)
        object.__setattr__(self, "item_kind", item_kind)
        if item_kind not in _EXECUTABLE_ITEM_KINDS:
            raise AgentNonExecutableItemError()
        object.__setattr__(
            self, "source_status", _enum_value(self.source_status, ExecutionSourceStatusV1)
        )
        object.__setattr__(self, "state", _enum_value(self.state, ExecutionLifecycleStateV1))
        object.__setattr__(
            self, "effort_precision", _enum_value(self.effort_precision, ExecutionEffortPrecisionV1)
        )
        object.__setattr__(
            self, "window_relation", _enum_value(self.window_relation, ExecutionWindowRelationV1)
        )
        if (
            type(self.planned_effort_minutes) is not int
            or not 0 <= self.planned_effort_minutes <= 1440
        ):
            raise AgentMissionInvalidError()
        if self.actual_effort_minutes is not None and (
            type(self.actual_effort_minutes) is not int
            or not 0 <= self.actual_effort_minutes <= 1440
        ):
            raise AgentMissionInvalidError()
        if type(self.event_count) is not int or self.event_count < 0:
            raise AgentMissionInvalidError()
        reasons = _tuple_texts(
            self.current_block_reasons,
            max_count=MAX_AGENT_CAVEATS,
            max_bytes=64,
        )
        deviations = _tuple_texts(
            self.deviation_codes,
            max_count=MAX_AGENT_CAVEATS,
            max_bytes=64,
        )
        if type(self.caveats) is not tuple or len(self.caveats) > MAX_AGENT_CAVEATS:
            raise AgentMissionInvalidError()
        caveats = tuple(_text(code, max_bytes=MAX_AGENT_CAVEAT_BYTES) for code in self.caveats)
        disposition = (
            None
            if self.terminal_disposition is None
            else _text(self.terminal_disposition, max_bytes=64)
        )
        object.__setattr__(self, "current_block_reasons", reasons)
        object.__setattr__(self, "terminal_disposition", disposition)
        object.__setattr__(self, "deviation_codes", deviations)
        object.__setattr__(self, "caveats", caveats)

    @classmethod
    def from_execution_state(cls, state: ExecutionItemStateV1) -> AgentStage18ItemProjectionV1:
        if type(state) is not ExecutionItemStateV1:
            raise AgentSourceUnavailableError()
        terminal_feedback = state.terminal_feedback
        return cls(
            planning_snapshot_id=state.planning_snapshot_id,
            planning_snapshot_fingerprint=state.planning_snapshot_fingerprint,
            planning_plan_revision=state.planning_plan_revision,
            planning_policy_id=state.planning_policy_id,
            planning_policy_fingerprint=state.planning_policy_fingerprint,
            item_id=state.item_id,
            accepted_item_fingerprint=state.accepted_item_fingerprint,
            item_kind=state.item_kind,
            source_status=state.source_status,
            state=state.state,
            planned_effort_minutes=state.planned_effort_minutes,
            actual_effort_minutes=state.actual_effort_minutes,
            effort_precision=state.effort_precision,
            current_block_reasons=tuple(code.value for code in state.current_block_reasons),
            terminal_disposition=(
                None
                if terminal_feedback is None or terminal_feedback.result_disposition is None
                else terminal_feedback.result_disposition.value
            ),
            deviation_codes=(
                ()
                if terminal_feedback is None
                else tuple(code.value for code in terminal_feedback.deviation_codes)
            ),
            window_relation=state.window_relation,
            event_count=state.event_count,
            caveats=state.caveats,
        )

    def as_dict(self) -> dict[str, object]:
        return {
            "planning_snapshot_id": str(self.planning_snapshot_id),
            "planning_snapshot_fingerprint": self.planning_snapshot_fingerprint,
            "planning_plan_revision": self.planning_plan_revision,
            "planning_policy_id": self.planning_policy_id,
            "planning_policy_fingerprint": self.planning_policy_fingerprint,
            "item_id": self.item_id,
            "accepted_item_fingerprint": self.accepted_item_fingerprint,
            "item_kind": self.item_kind.value,
            "source_status": self.source_status.value,
            "state": self.state.value,
            "planned_effort_minutes": self.planned_effort_minutes,
            "actual_effort_minutes": self.actual_effort_minutes,
            "effort_precision": self.effort_precision.value,
            "current_block_reasons": list(self.current_block_reasons),
            "terminal_disposition": self.terminal_disposition,
            "deviation_codes": list(self.deviation_codes),
            "window_relation": self.window_relation.value,
            "event_count": self.event_count,
            "caveats": list(self.caveats),
        }


@dataclass(frozen=True, slots=True)
class AgentSelectedItemContextV1:
    """Bounded human-readable projection of one exact selected item."""

    item_id: str
    accepted_item_fingerprint: str
    item_kind: PlanningItemKindV1 | str
    title: str
    description: str
    goal_refs: tuple[PlanningGoalRefV1, ...]
    action_refs: tuple[PlanningActionBindingV1, ...]
    goal_aliases: tuple[str, ...]
    action_aliases: tuple[str, ...]
    parent_item_id: str | None
    target_start_local: str | None
    target_end_local: str | None
    effort_minutes: int
    execution: AgentStage18ItemProjectionV1

    def __post_init__(self) -> None:
        item_id = _identifier(self.item_id, error=AgentContextPackInvalidError)
        fingerprint = _hash(
            self.accepted_item_fingerprint,
            error=AgentContextPackInvalidError,
        )
        item_kind = _enum_value(
            self.item_kind,
            PlanningItemKindV1,
            error=AgentContextPackInvalidError,
        )
        if item_kind not in _EXECUTABLE_ITEM_KINDS:
            raise AgentContextPackInvalidError()
        if (
            type(self.goal_refs) is not tuple
            or not 1 <= len(self.goal_refs) <= MAX_PLANNING_ITEM_REFS
        ):
            raise AgentContextPackInvalidError()
        if (
            type(self.action_refs) is not tuple
            or not 1 <= len(self.action_refs) <= MAX_PLANNING_ITEM_REFS
        ):
            raise AgentContextPackInvalidError()
        if any(type(ref) is not PlanningGoalRefV1 for ref in self.goal_refs):
            raise AgentContextPackInvalidError()
        if any(type(ref) is not PlanningActionBindingV1 for ref in self.action_refs):
            raise AgentContextPackInvalidError()
        title = _text(
            self.title,
            max_bytes=MAX_PLANNING_ITEM_TITLE_BYTES,
            error=AgentContextPackInvalidError,
        )
        description = _text(
            self.description,
            max_bytes=MAX_PLANNING_ITEM_DESCRIPTION_BYTES,
            error=AgentContextPackInvalidError,
        )
        goal_aliases = _tuple_texts(
            self.goal_aliases,
            max_count=MAX_PLANNING_ITEM_REFS,
            max_bytes=128,
            error=AgentContextPackInvalidError,
        )
        action_aliases = _tuple_texts(
            self.action_aliases,
            max_count=MAX_PLANNING_ITEM_REFS,
            max_bytes=128,
            error=AgentContextPackInvalidError,
        )
        parent_item_id = (
            None
            if self.parent_item_id is None
            else _identifier(self.parent_item_id, error=AgentContextPackInvalidError)
        )
        target_start_local = (
            None
            if self.target_start_local is None
            else _text(self.target_start_local, max_bytes=64, error=AgentContextPackInvalidError)
        )
        target_end_local = (
            None
            if self.target_end_local is None
            else _text(self.target_end_local, max_bytes=64, error=AgentContextPackInvalidError)
        )
        if type(self.effort_minutes) is not int or not 0 <= self.effort_minutes <= 1440:
            raise AgentContextPackInvalidError()
        if type(self.execution) is not AgentStage18ItemProjectionV1:
            raise AgentContextPackInvalidError()
        if (
            self.execution.item_id != item_id
            or self.execution.accepted_item_fingerprint != fingerprint
            or self.execution.item_kind is not item_kind
        ):
            raise AgentContextPackInvalidError()
        object.__setattr__(self, "item_id", item_id)
        object.__setattr__(self, "accepted_item_fingerprint", fingerprint)
        object.__setattr__(self, "item_kind", item_kind)
        object.__setattr__(self, "title", title)
        object.__setattr__(self, "description", description)
        object.__setattr__(self, "goal_refs", tuple(self.goal_refs))
        object.__setattr__(self, "action_refs", tuple(self.action_refs))
        object.__setattr__(self, "goal_aliases", goal_aliases)
        object.__setattr__(self, "action_aliases", action_aliases)
        object.__setattr__(self, "parent_item_id", parent_item_id)
        object.__setattr__(self, "target_start_local", target_start_local)
        object.__setattr__(self, "target_end_local", target_end_local)

    def as_dict(self) -> dict[str, object]:
        return {
            "item_id": self.item_id,
            "accepted_item_fingerprint": self.accepted_item_fingerprint,
            "item_kind": cast(PlanningItemKindV1, self.item_kind).value,
            "title": self.title,
            "description": self.description,
            "goal_refs": [ref.as_dict() for ref in self.goal_refs],
            "action_refs": [ref.as_dict() for ref in self.action_refs],
            "goal_aliases": list(self.goal_aliases),
            "action_aliases": list(self.action_aliases),
            "parent_item_id": self.parent_item_id,
            "target_start_local": self.target_start_local,
            "target_end_local": self.target_end_local,
            "effort_minutes": self.effort_minutes,
            "execution": self.execution.as_dict(),
        }


@dataclass(frozen=True, slots=True)
class AgentContextPackV1:
    """Immutable provider-free exact context used by later Stage 20 phases."""

    contract_version: str
    policy_id: str
    policy_fingerprint: str
    mission: AgentMissionV1
    planning_snapshot_id: UUID
    planning_snapshot_fingerprint: str
    planning_policy_id: str
    planning_policy_fingerprint: str
    selected_items: tuple[AgentSelectedItemContextV1, ...]
    stage19: AgentStage19CapabilityProjectionV1
    readiness: AgentContextReadinessV1 | str
    caveats: tuple[str, ...]
    pack_fingerprint: str

    def __post_init__(self) -> None:
        if self.contract_version != AGENT_CONTEXT_PACK_CONTRACT_VERSION:
            raise AgentContextPackInvalidError()
        if self.policy_id != AGENT_POLICY_ID or self.policy_fingerprint != AGENT_POLICY_FINGERPRINT:
            raise AgentPolicyMismatchError()
        if (
            type(self.mission) is not AgentMissionV1
            or type(self.stage19) is not AgentStage19CapabilityProjectionV1
        ):
            raise AgentContextPackInvalidError()
        if self.planning_snapshot_id != self.mission.planning_snapshot_id:
            raise AgentContextPackInvalidError()
        if self.planning_snapshot_fingerprint != self.mission.planning_snapshot_fingerprint:
            raise AgentContextPackInvalidError()
        if (
            self.planning_policy_id != self.mission.planning_policy_id
            or self.planning_policy_fingerprint != self.mission.planning_policy_fingerprint
        ):
            raise AgentContextPackInvalidError()
        if (
            type(self.selected_items) is not tuple
            or not 1 <= len(self.selected_items) <= MAX_AGENT_SELECTED_ITEMS
        ):
            raise AgentContextPackInvalidError()
        if any(type(item) is not AgentSelectedItemContextV1 for item in self.selected_items):
            raise AgentContextPackInvalidError()
        if len({item.item_id for item in self.selected_items}) != len(self.selected_items):
            raise AgentContextPackInvalidError()
        if type(self.caveats) is not tuple or len(self.caveats) > MAX_AGENT_CAVEATS:
            raise AgentContextPackInvalidError()
        readiness = _enum_value(
            self.readiness, AgentContextReadinessV1, error=AgentContextPackInvalidError
        )
        caveats = tuple(
            _text(item, max_bytes=MAX_AGENT_CAVEAT_BYTES, error=AgentContextPackInvalidError)
            for item in self.caveats
        )
        expected_item_ids = tuple(binding.item_id for binding in self.mission.selected_items)
        actual_item_ids = tuple(item.item_id for item in self.selected_items)
        if actual_item_ids != expected_item_ids:
            raise AgentContextPackInvalidError()
        for binding, item in zip(self.mission.selected_items, self.selected_items, strict=True):
            if (
                item.accepted_item_fingerprint != binding.accepted_item_fingerprint
                or item.item_kind is not binding.item_kind
                or item.goal_refs != binding.goal_refs
                or item.action_refs != binding.action_refs
            ):
                raise AgentContextPackInvalidError()
            execution = item.execution
            if (
                execution.planning_snapshot_id != self.mission.planning_snapshot_id
                or execution.planning_snapshot_fingerprint
                != self.mission.planning_snapshot_fingerprint
                or execution.planning_plan_revision < 1
                or execution.planning_policy_id != self.mission.planning_policy_id
                or execution.planning_policy_fingerprint != self.mission.planning_policy_fingerprint
                or execution.source_status is not ExecutionSourceStatusV1.CURRENT
            ):
                raise AgentContextPackInvalidError()
        allowed_repositories = {repository.casefold() for repository in self.stage19.repositories}
        if any(
            target.repository.casefold() not in allowed_repositories
            for target in self.mission.external_targets
        ):
            raise AgentContextPackInvalidError()
        if self.stage19.ready and readiness is not AgentContextReadinessV1.EXACT_CURRENT:
            raise AgentContextPackInvalidError()
        if (
            not self.stage19.ready
            and readiness is not AgentContextReadinessV1.CAPABILITY_UNAVAILABLE
        ):
            raise AgentContextPackInvalidError()
        supplied = _hash(self.pack_fingerprint, error=AgentContextPackInvalidError)
        object.__setattr__(self, "readiness", readiness)
        object.__setattr__(self, "caveats", caveats)
        expected = personal_agent_hash(self._core_dict())
        if supplied != expected:
            raise AgentContextPackInvalidError()
        if len(_canonical_bytes(self.as_dict())) > MAX_AGENT_CONTEXT_PACK_BYTES:
            raise AgentContextPackInvalidError()

    def _core_dict(self) -> dict[str, object]:
        return {
            "contract_version": self.contract_version,
            "policy_id": self.policy_id,
            "policy_fingerprint": self.policy_fingerprint,
            "mission": self.mission.as_dict(),
            "planning_snapshot_id": str(self.planning_snapshot_id),
            "planning_snapshot_fingerprint": self.planning_snapshot_fingerprint,
            "planning_policy_id": self.planning_policy_id,
            "planning_policy_fingerprint": self.planning_policy_fingerprint,
            "selected_items": [item.as_dict() for item in self.selected_items],
            "stage19": self.stage19.as_dict(),
            "readiness": cast(AgentContextReadinessV1, self.readiness).value,
            "caveats": list(self.caveats),
        }

    def as_dict(self) -> dict[str, object]:
        return {**self._core_dict(), "pack_fingerprint": self.pack_fingerprint}

    @property
    def fingerprint(self) -> str:
        return self.pack_fingerprint


def _validate_plan_binding(
    mission: AgentMissionV1, plan: PlanningPlanV1
) -> tuple[PlanningItemV1, ...]:
    if (
        plan.plan_id != mission.planning_snapshot_id
        or plan.plan_fingerprint != mission.planning_snapshot_fingerprint
    ):
        raise AgentSourceMismatchError()
    if (
        plan.policy_id != mission.planning_policy_id
        or plan.policy_fingerprint != mission.planning_policy_fingerprint
    ):
        raise AgentPolicyMismatchError()
    items_by_id = {item.item_id: item for item in plan.items}
    selected_ids = set(plan.selected_item_ids)
    resolved: list[PlanningItemV1] = []
    for binding in mission.selected_items:
        item = items_by_id.get(binding.item_id)
        if item is None or binding.item_id not in selected_ids:
            raise AgentItemNotSelectedError()
        if item.kind not in _EXECUTABLE_ITEM_KINDS:
            raise AgentNonExecutableItemError()
        if (
            item.kind is not binding.item_kind
            or accepted_item_fingerprint(item) != binding.accepted_item_fingerprint
            or item.goal_refs != binding.goal_refs
            or item.action_refs != binding.action_refs
        ):
            raise AgentSourceMismatchError()
        resolved.append(item)
    return tuple(resolved)


def _validate_execution_binding(
    plan: PlanningPlanV1,
    item: PlanningItemV1,
    state: ExecutionItemStateV1,
) -> AgentStage18ItemProjectionV1:
    if (
        state.planning_snapshot_id != plan.plan_id
        or state.planning_snapshot_fingerprint != plan.plan_fingerprint
        or state.planning_plan_revision != plan.revision
        or state.planning_policy_id != plan.policy_id
        or state.planning_policy_fingerprint != plan.policy_fingerprint
        or state.item_id != item.item_id
        or state.item_kind is not item.kind
        or state.accepted_item_fingerprint != accepted_item_fingerprint(item)
    ):
        raise AgentSourceMismatchError()
    if state.source_status is ExecutionSourceStatusV1.UNAVAILABLE:
        raise AgentSourceUnavailableError()
    if state.source_status is not ExecutionSourceStatusV1.CURRENT:
        raise AgentSourceStaleError()
    return AgentStage18ItemProjectionV1.from_execution_state(state)


def _build_item_context(
    item: PlanningItemV1,
    execution: AgentStage18ItemProjectionV1,
) -> AgentSelectedItemContextV1:
    return AgentSelectedItemContextV1(
        item_id=item.item_id,
        accepted_item_fingerprint=accepted_item_fingerprint(item),
        item_kind=cast(PlanningItemKindV1, item.kind),
        title=item.title,
        description=item.description,
        goal_refs=item.goal_refs,
        action_refs=item.action_refs,
        goal_aliases=tuple(f"goal:{ref.goal_source_uuid}" for ref in item.goal_refs),
        action_aliases=tuple(f"action:{ref.reviewed_action_id}" for ref in item.action_refs),
        parent_item_id=item.parent_item_id,
        target_start_local=item.target_start_local,
        target_end_local=item.target_end_local,
        effort_minutes=item.effort_minutes,
        execution=execution,
    )


def build_agent_context_pack(
    mission: AgentMissionV1,
    planning_plan: PlanningPlanV1 | None,
    execution_projections: Mapping[str, ExecutionItemStateV1],
    stage19: AgentStage19CapabilityProjectionV1,
    *,
    current_planning_plan: PlanningPlanV1 | None = None,
) -> AgentContextPackV1:
    """Build one deterministic pack without provider, network, storage or writes."""

    if type(mission) is not AgentMissionV1:
        raise AgentMissionInvalidError()
    if planning_plan is None or current_planning_plan is None:
        raise AgentSourceUnavailableError()
    if (
        type(planning_plan) is not PlanningPlanV1
        or type(current_planning_plan) is not PlanningPlanV1
    ):
        raise AgentSourceUnavailableError()
    if type(stage19) is not AgentStage19CapabilityProjectionV1:
        raise AgentCapabilityInvalidError()
    if not isinstance(execution_projections, Mapping):
        raise AgentSourceUnavailableError()
    if (
        current_planning_plan.plan_id != planning_plan.plan_id
        or current_planning_plan.plan_fingerprint != planning_plan.plan_fingerprint
    ):
        raise AgentSourceStaleError()
    items = _validate_plan_binding(mission, planning_plan)
    context_items: list[AgentSelectedItemContextV1] = []
    for item in items:
        state = execution_projections.get(item.item_id)
        if type(state) is not ExecutionItemStateV1:
            raise AgentSourceUnavailableError()
        execution = _validate_execution_binding(planning_plan, item, state)
        context_items.append(_build_item_context(item, execution))
    allowed_repositories = {repository.casefold() for repository in stage19.repositories}
    for target in mission.external_targets:
        if target.repository.casefold() not in allowed_repositories:
            raise AgentTargetNotAllowedError()
    readiness = (
        AgentContextReadinessV1.EXACT_CURRENT
        if stage19.ready
        else AgentContextReadinessV1.CAPABILITY_UNAVAILABLE
    )
    caveats = () if stage19.ready else ("stage19_connector_not_ready",)
    core = {
        "contract_version": AGENT_CONTEXT_PACK_CONTRACT_VERSION,
        "policy_id": AGENT_POLICY_ID,
        "policy_fingerprint": AGENT_POLICY_FINGERPRINT,
        "mission": mission.as_dict(),
        "planning_snapshot_id": str(planning_plan.plan_id),
        "planning_snapshot_fingerprint": planning_plan.plan_fingerprint,
        "planning_policy_id": planning_plan.policy_id,
        "planning_policy_fingerprint": planning_plan.policy_fingerprint,
        "selected_items": [item.as_dict() for item in context_items],
        "stage19": stage19.as_dict(),
        "readiness": readiness.value,
        "caveats": list(caveats),
    }
    return AgentContextPackV1(
        contract_version=AGENT_CONTEXT_PACK_CONTRACT_VERSION,
        policy_id=AGENT_POLICY_ID,
        policy_fingerprint=AGENT_POLICY_FINGERPRINT,
        mission=mission,
        planning_snapshot_id=cast(UUID, planning_plan.plan_id),
        planning_snapshot_fingerprint=planning_plan.plan_fingerprint,
        planning_policy_id=planning_plan.policy_id,
        planning_policy_fingerprint=planning_plan.policy_fingerprint,
        selected_items=tuple(context_items),
        stage19=stage19,
        readiness=readiness,
        caveats=caveats,
        pack_fingerprint=personal_agent_hash(core),
    )


def serialize_agent_context_pack(pack: AgentContextPackV1) -> bytes:
    """Serialize only a validated pack for a later explicit preview boundary."""

    if type(pack) is not AgentContextPackV1:
        raise AgentContextPackInvalidError()
    return _canonical_bytes(pack.as_dict())


def validate_agent_context_pack(pack: AgentContextPackV1) -> AgentContextPackV1:
    """Validate the exact immutable pack object and return it unchanged."""

    if type(pack) is not AgentContextPackV1:
        raise AgentContextPackInvalidError()
    return pack


__all__ = [
    "AGENT_CONTEXT_PACK_CONTRACT_VERSION",
    "AGENT_MISSION_CONTRACT_VERSION",
    "AGENT_POLICY_CANONICAL_JSON",
    "AGENT_POLICY_FINGERPRINT",
    "AGENT_POLICY_ID",
    "AgentCapabilityInvalidError",
    "AgentContextPackInvalidError",
    "AgentContextPackV1",
    "AgentContextReadinessV1",
    "AgentExternalTargetRefV1",
    "AgentItemNotSelectedError",
    "AgentMissionInvalidError",
    "AgentMissionItemBindingV1",
    "AgentMissionV1",
    "AgentNonExecutableItemError",
    "AgentPolicyMismatchError",
    "AgentSelectedItemContextV1",
    "AgentSourceMismatchError",
    "AgentSourceStaleError",
    "AgentSourceUnavailableError",
    "AgentStage18ItemProjectionV1",
    "AgentStage19ActionCapabilityProjectionV1",
    "AgentStage19ActionCapabilityV1",
    "AgentStage19CapabilityProjectionV1",
    "AgentStage19ReversibilityV1",
    "AgentStage19RiskV1",
    "AgentStage19StatusV1",
    "AgentTargetNotAllowedError",
    "PersonalAgentError",
    "build_agent_context_pack",
    "default_stage19_action_catalog",
    "personal_agent_hash",
    "serialize_agent_context_pack",
    "validate_agent_context_pack",
]
