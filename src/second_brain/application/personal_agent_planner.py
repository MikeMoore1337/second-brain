"""Explicit Advisor boundary and strict linear Run Proposal for Stage 20.

This module consumes the provider-free Context Pack from ``personal_agent``
and reuses only the existing ``AdvisorPort``/Assistant v1 seam.  Provider
output is parsed as untrusted bounded JSON and can produce only a typed,
non-executable linear proposal.  There is no store, Stage 19 prepare call,
connector import, shell, browser, or network authority here.
"""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Final, cast
from uuid import UUID, uuid7

from second_brain.application.action_gateway import IssueStateV1
from second_brain.application.assistant import (
    AssistantAbstentionCode,
    AssistantCancelledError,
    AssistantContextKind,
    AssistantError,
    AssistantExplicitContext,
    AssistantInvalidRequestError,
    AssistantMalformedResultError,
    AssistantProviderFailureError,
    AssistantProviderUnavailableError,
    AssistantReasoningEnvelopeV1,
    AssistantRequest,
    AssistantResultEnvelopeV1,
    AssistantResultInvalidError,
    AssistantResultKind,
    AssistantResultTooLargeError,
    AssistantTimeoutError,
    BuildAssistant,
    build_assistant_reasoning_envelope,
    serialize_assistant_reasoning_envelope,
    serialize_assistant_result_envelope,
)
from second_brain.application.personal_agent import (
    AGENT_POLICY_FINGERPRINT,
    AGENT_POLICY_ID,
    AgentContextPackInvalidError,
    AgentContextPackV1,
    AgentSelectedItemContextV1,
    AgentStage19ActionCapabilityV1,
    AgentStage19ReversibilityV1,
    AgentStage19RiskV1,
    PersonalAgentError,
    personal_agent_hash,
    validate_agent_context_pack,
)
from second_brain.application.personal_planning import PlanningItemKindV1
from second_brain.application.ports import AdvisorPort, CancellationToken
from second_brain.domain.models import parse_uuid7

AGENT_REASONING_ENVELOPE_CONTRACT_VERSION: Final[str] = "personal-agent-reasoning-envelope-v1"
AGENT_RUN_PROPOSAL_CONTRACT_VERSION: Final[str] = "personal-agent-run-proposal-v1"
MAX_AGENT_PROPOSAL_STEPS: Final[int] = 12
MAX_AGENT_PROPOSAL_CAVEATS: Final[int] = 16
MAX_AGENT_PROPOSAL_CAVEAT_BYTES: Final[int] = 512
MAX_AGENT_STEP_ID_BYTES: Final[int] = 64
MAX_AGENT_STEP_TEXT_BYTES: Final[int] = 2048
MAX_AGENT_ANSWER_SHAPE_BYTES: Final[int] = 512
MAX_AGENT_ACTION_TITLE_BYTES: Final[int] = 256
MAX_AGENT_ACTION_BODY_BYTES: Final[int] = 64 * 1024
MAX_AGENT_ACTION_COMMENT_BYTES: Final[int] = 64 * 1024
MAX_AGENT_REASONING_CONTEXT_BYTES: Final[int] = 64 * 1024
MAX_AGENT_PROVIDER_PROPOSAL_BYTES: Final[int] = 64 * 1024

_HASH_PATTERN: Final[re.Pattern[str]] = re.compile(r"[0-9a-f]{64}\Z", re.ASCII)
_STEP_ID_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"[A-Za-z0-9][A-Za-z0-9._:-]{0,63}\Z", re.ASCII
)
_REPOSITORY_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"[A-Za-z0-9][A-Za-z0-9._-]{0,38}/[A-Za-z0-9][A-Za-z0-9._-]{0,99}\Z",
    re.ASCII,
)


class AgentPlannerError(PersonalAgentError):
    """Base safe error for the explicit Stage 20 Advisor boundary."""

    code = "AGENT_PLANNER_INVALID_REQUEST"


class AgentReasoningEnvelopeInvalidError(AgentPlannerError):
    """The exact provider-visible projection cannot be represented safely."""

    code = "AGENT_REASONING_ENVELOPE_INVALID"


class AgentReasoningEnvelopeTooLargeError(AgentPlannerError):
    """The exact provider-visible projection exceeds the existing Assistant cap."""

    code = "AGENT_REASONING_ENVELOPE_TOO_LARGE"


class AgentRunProposalInvalidError(AgentPlannerError):
    """Untrusted provider output failed the closed Run Proposal contract."""

    code = "AGENT_RUN_PROPOSAL_INVALID"


class AgentPlannerCancelledError(AgentPlannerError):
    """The explicit foreground Advisor operation was cancelled."""

    code = "AGENT_PLANNER_CANCELLED"


class AgentPlannerProviderUnavailableError(AgentPlannerError):
    """The existing Advisor boundary is unavailable without leaking details."""

    code = "AGENT_ADVISOR_UNAVAILABLE"


class AgentPlannerProviderResultInvalidError(AgentPlannerError):
    """The existing Advisor returned no acceptable Assistant result."""

    code = "AGENT_ADVISOR_RESULT_INVALID"


class AgentRunStepKindV1(StrEnum):
    """Closed linear Stage 20 step vocabulary."""

    CLARIFY = "clarify"
    CHECKPOINT = "checkpoint"
    STAGE19_ACTION = "stage19_action"
    HOLD = "hold"


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
        raise AgentPlannerError() from exc


def _hash(value: object, *, error: type[PersonalAgentError]) -> str:
    if type(value) is not str or _HASH_PATTERN.fullmatch(value) is None:
        raise error()
    return value


def _uuid7(value: object, *, error: type[PersonalAgentError]) -> UUID:
    try:
        return parse_uuid7(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise error() from exc


def _text(
    value: object,
    *,
    max_bytes: int,
    error: type[PersonalAgentError],
    allow_empty: bool = False,
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


def _step_id(value: object) -> str:
    normalized = _text(value, max_bytes=MAX_AGENT_STEP_ID_BYTES, error=AgentRunProposalInvalidError)
    if _STEP_ID_PATTERN.fullmatch(normalized) is None:
        raise AgentRunProposalInvalidError()
    return normalized


def _repository(value: object, *, error: type[PersonalAgentError]) -> str:
    normalized = _text(value, max_bytes=200, error=error)
    if _REPOSITORY_PATTERN.fullmatch(normalized) is None:
        raise error()
    return normalized


def _enum_value[EnumT: StrEnum](
    value: object,
    enum_type: type[EnumT],
    *,
    error: type[PersonalAgentError],
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
    maximum: int,
    max_bytes: int,
    error: type[PersonalAgentError],
) -> tuple[str, ...]:
    if type(value) is not tuple or len(value) > maximum:
        raise error()
    return tuple(_text(item, max_bytes=max_bytes, error=error) for item in value)


def _strict_mapping(
    value: object, fields: set[str], *, error: type[PersonalAgentError]
) -> dict[str, object]:
    if not isinstance(value, Mapping) or any(type(key) is not str for key in value):
        raise error()
    data = dict(value)
    if set(data) != fields:
        raise error()
    return data


@dataclass(frozen=True, slots=True)
class AgentReasoningItemV1:
    """Provider-visible neutral projection of one selected item."""

    item_alias: str
    item_kind: str
    title: str
    description: str
    goal_aliases: tuple[str, ...]
    action_aliases: tuple[str, ...]
    source_status: str
    execution_state: str
    planned_effort_minutes: int
    actual_effort_minutes: int | None
    effort_precision: str
    current_block_reasons: tuple[str, ...]
    terminal_disposition: str | None
    deviation_codes: tuple[str, ...]
    window_relation: str
    event_count: int
    caveats: tuple[str, ...]

    def __post_init__(self) -> None:
        alias = _text(self.item_alias, max_bytes=128, error=AgentReasoningEnvelopeInvalidError)
        item_kind = _text(self.item_kind, max_bytes=64, error=AgentReasoningEnvelopeInvalidError)
        title = _text(
            self.title,
            max_bytes=256,
            error=AgentReasoningEnvelopeInvalidError,
        )
        description = _text(
            self.description,
            max_bytes=2048,
            error=AgentReasoningEnvelopeInvalidError,
        )
        goal_aliases = _tuple_texts(
            self.goal_aliases,
            maximum=8,
            max_bytes=128,
            error=AgentReasoningEnvelopeInvalidError,
        )
        action_aliases = _tuple_texts(
            self.action_aliases,
            maximum=8,
            max_bytes=128,
            error=AgentReasoningEnvelopeInvalidError,
        )
        source_status = _text(
            self.source_status,
            max_bytes=64,
            error=AgentReasoningEnvelopeInvalidError,
        )
        execution_state = _text(
            self.execution_state,
            max_bytes=64,
            error=AgentReasoningEnvelopeInvalidError,
        )
        effort_precision = _text(
            self.effort_precision,
            max_bytes=64,
            error=AgentReasoningEnvelopeInvalidError,
        )
        window_relation = _text(
            self.window_relation,
            max_bytes=64,
            error=AgentReasoningEnvelopeInvalidError,
        )
        current_block_reasons = _tuple_texts(
            self.current_block_reasons,
            maximum=16,
            max_bytes=64,
            error=AgentReasoningEnvelopeInvalidError,
        )
        deviation_codes = _tuple_texts(
            self.deviation_codes,
            maximum=16,
            max_bytes=64,
            error=AgentReasoningEnvelopeInvalidError,
        )
        caveats = _tuple_texts(
            self.caveats,
            maximum=16,
            max_bytes=512,
            error=AgentReasoningEnvelopeInvalidError,
        )
        terminal_disposition = (
            None
            if self.terminal_disposition is None
            else _text(
                self.terminal_disposition,
                max_bytes=64,
                error=AgentReasoningEnvelopeInvalidError,
            )
        )
        if (
            type(self.planned_effort_minutes) is not int
            or not 0 <= self.planned_effort_minutes <= 1440
        ):
            raise AgentReasoningEnvelopeInvalidError()
        if self.actual_effort_minutes is not None and (
            type(self.actual_effort_minutes) is not int
            or not 0 <= self.actual_effort_minutes <= 1440
        ):
            raise AgentReasoningEnvelopeInvalidError()
        if type(self.event_count) is not int or not 0 <= self.event_count <= 32768:
            raise AgentReasoningEnvelopeInvalidError()
        object.__setattr__(self, "item_alias", alias)
        object.__setattr__(self, "item_kind", item_kind)
        object.__setattr__(self, "title", title)
        object.__setattr__(self, "description", description)
        object.__setattr__(self, "goal_aliases", goal_aliases)
        object.__setattr__(self, "action_aliases", action_aliases)
        object.__setattr__(self, "source_status", source_status)
        object.__setattr__(self, "execution_state", execution_state)
        object.__setattr__(self, "effort_precision", effort_precision)
        object.__setattr__(self, "current_block_reasons", current_block_reasons)
        object.__setattr__(self, "terminal_disposition", terminal_disposition)
        object.__setattr__(self, "deviation_codes", deviation_codes)
        object.__setattr__(self, "window_relation", window_relation)
        object.__setattr__(self, "caveats", caveats)

    def as_dict(self) -> dict[str, object]:
        return {
            "item_alias": self.item_alias,
            "item_kind": self.item_kind,
            "title": self.title,
            "description": self.description,
            "goal_aliases": list(self.goal_aliases),
            "action_aliases": list(self.action_aliases),
            "source_status": self.source_status,
            "execution_state": self.execution_state,
            "planned_effort_minutes": self.planned_effort_minutes,
            "actual_effort_minutes": self.actual_effort_minutes,
            "effort_precision": self.effort_precision,
            "current_block_reasons": list(self.current_block_reasons),
            "terminal_disposition": self.terminal_disposition,
            "deviation_codes": list(self.deviation_codes),
            "window_relation": self.window_relation,
            "event_count": self.event_count,
            "caveats": list(self.caveats),
        }


@dataclass(frozen=True, slots=True)
class AgentReasoningActionV1:
    """Provider-visible Stage 19 catalog row without authority or credentials."""

    action_kind: AgentStage19ActionCapabilityV1 | str
    risk: AgentStage19RiskV1 | str
    reversibility: AgentStage19ReversibilityV1 | str

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "action_kind",
            _enum_value(
                self.action_kind,
                AgentStage19ActionCapabilityV1,
                error=AgentReasoningEnvelopeInvalidError,
            ),
        )
        object.__setattr__(
            self,
            "risk",
            _enum_value(
                self.risk,
                AgentStage19RiskV1,
                error=AgentReasoningEnvelopeInvalidError,
            ),
        )
        object.__setattr__(
            self,
            "reversibility",
            _enum_value(
                self.reversibility,
                AgentStage19ReversibilityV1,
                error=AgentReasoningEnvelopeInvalidError,
            ),
        )

    def as_dict(self) -> dict[str, str]:
        return {
            "action_kind": cast(AgentStage19ActionCapabilityV1, self.action_kind).value,
            "risk": cast(AgentStage19RiskV1, self.risk).value,
            "reversibility": cast(AgentStage19ReversibilityV1, self.reversibility).value,
        }


@dataclass(frozen=True, slots=True)
class AgentReasoningTargetV1:
    """Provider-visible explicit owner target alias."""

    action_kind: AgentStage19ActionCapabilityV1 | str
    repository: str
    issue_number: int | None

    def __post_init__(self) -> None:
        action_kind = _enum_value(
            self.action_kind,
            AgentStage19ActionCapabilityV1,
            error=AgentReasoningEnvelopeInvalidError,
        )
        repository = _repository(self.repository, error=AgentReasoningEnvelopeInvalidError)
        if self.issue_number is not None and (
            type(self.issue_number) is not int
            or isinstance(self.issue_number, bool)
            or not 1 <= self.issue_number <= 2**31 - 1
        ):
            raise AgentReasoningEnvelopeInvalidError()
        requires_issue = action_kind in {
            AgentStage19ActionCapabilityV1.GITHUB_ISSUE_COMMENT,
            AgentStage19ActionCapabilityV1.GITHUB_ISSUE_SET_STATE,
        }
        if (self.issue_number is None) == requires_issue:
            raise AgentReasoningEnvelopeInvalidError()
        object.__setattr__(self, "action_kind", action_kind)
        object.__setattr__(self, "repository", repository)

    def as_dict(self) -> dict[str, object]:
        return {
            "action_kind": cast(AgentStage19ActionCapabilityV1, self.action_kind).value,
            "repository": self.repository,
            "issue_number": self.issue_number,
        }


@dataclass(frozen=True, slots=True)
class AgentReasoningEnvelopeV1:
    """Exact bounded projection used both for preview and the Advisor call."""

    contract_version: str
    mission_fingerprint: str
    context_pack_fingerprint: str
    task: str
    constraints: tuple[str, ...]
    current_context: tuple[str, ...]
    selected_items: tuple[AgentReasoningItemV1, ...]
    external_targets: tuple[AgentReasoningTargetV1, ...]
    stage19_actions: tuple[AgentReasoningActionV1, ...]
    allowed_repositories: tuple[str, ...]

    def __post_init__(self) -> None:
        if self.contract_version != AGENT_REASONING_ENVELOPE_CONTRACT_VERSION:
            raise AgentReasoningEnvelopeInvalidError()
        mission_fingerprint = _hash(
            self.mission_fingerprint,
            error=AgentReasoningEnvelopeInvalidError,
        )
        context_pack_fingerprint = _hash(
            self.context_pack_fingerprint,
            error=AgentReasoningEnvelopeInvalidError,
        )
        task = _text(
            self.task,
            max_bytes=4096,
            error=AgentReasoningEnvelopeInvalidError,
        )
        constraints = _tuple_texts(
            self.constraints,
            maximum=16,
            max_bytes=4096,
            error=AgentReasoningEnvelopeInvalidError,
        )
        current_context = _tuple_texts(
            self.current_context,
            maximum=8,
            max_bytes=4096,
            error=AgentReasoningEnvelopeInvalidError,
        )
        if type(self.selected_items) is not tuple or not 1 <= len(self.selected_items) <= 16:
            raise AgentReasoningEnvelopeInvalidError()
        if any(type(item) is not AgentReasoningItemV1 for item in self.selected_items):
            raise AgentReasoningEnvelopeInvalidError()
        selected_items = tuple(self.selected_items)
        if len({item.item_alias for item in selected_items}) != len(selected_items):
            raise AgentReasoningEnvelopeInvalidError()
        if type(self.external_targets) is not tuple or len(self.external_targets) > 16:
            raise AgentReasoningEnvelopeInvalidError()
        if any(type(item) is not AgentReasoningTargetV1 for item in self.external_targets):
            raise AgentReasoningEnvelopeInvalidError()
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
            raise AgentReasoningEnvelopeInvalidError()
        if type(self.stage19_actions) is not tuple or not 1 <= len(self.stage19_actions) <= 3:
            raise AgentReasoningEnvelopeInvalidError()
        if any(type(item) is not AgentReasoningActionV1 for item in self.stage19_actions):
            raise AgentReasoningEnvelopeInvalidError()
        stage19_actions = tuple(self.stage19_actions)
        if len({item.action_kind for item in stage19_actions}) != len(stage19_actions):
            raise AgentReasoningEnvelopeInvalidError()
        if type(self.allowed_repositories) is not tuple or len(self.allowed_repositories) > 32:
            raise AgentReasoningEnvelopeInvalidError()
        allowed_repositories = tuple(
            _repository(item, error=AgentReasoningEnvelopeInvalidError)
            for item in self.allowed_repositories
        )
        if len({item.casefold() for item in allowed_repositories}) != len(allowed_repositories):
            raise AgentReasoningEnvelopeInvalidError()
        object.__setattr__(self, "mission_fingerprint", mission_fingerprint)
        object.__setattr__(self, "context_pack_fingerprint", context_pack_fingerprint)
        object.__setattr__(self, "task", task)
        object.__setattr__(self, "constraints", constraints)
        object.__setattr__(self, "current_context", current_context)
        object.__setattr__(self, "selected_items", selected_items)
        object.__setattr__(self, "external_targets", external_targets)
        object.__setattr__(self, "stage19_actions", stage19_actions)
        object.__setattr__(self, "allowed_repositories", allowed_repositories)

    @classmethod
    def from_context_pack(cls, pack: AgentContextPackV1) -> AgentReasoningEnvelopeV1:
        if type(pack) is not AgentContextPackV1:
            raise AgentContextPackInvalidError()
        validated = validate_agent_context_pack(pack)
        items = tuple(_reasoning_item(item) for item in validated.selected_items)
        targets = tuple(
            AgentReasoningTargetV1(
                action_kind=target.action_kind,
                repository=target.repository,
                issue_number=target.issue_number,
            )
            for target in validated.mission.external_targets
        )
        actions = tuple(
            AgentReasoningActionV1(
                action_kind=action.action_kind,
                risk=action.risk,
                reversibility=action.reversibility,
            )
            for action in validated.stage19.action_catalog
        )
        return cls(
            contract_version=AGENT_REASONING_ENVELOPE_CONTRACT_VERSION,
            mission_fingerprint=validated.mission.fingerprint,
            context_pack_fingerprint=validated.fingerprint,
            task=validated.mission.task,
            constraints=validated.mission.constraints,
            current_context=validated.mission.current_context,
            selected_items=items,
            external_targets=targets,
            stage19_actions=actions,
            allowed_repositories=validated.stage19.repositories,
        )

    def as_dict(self) -> dict[str, object]:
        return {
            "contract_version": self.contract_version,
            "mission_fingerprint": self.mission_fingerprint,
            "context_pack_fingerprint": self.context_pack_fingerprint,
            "task": self.task,
            "constraints": list(self.constraints),
            "current_context": list(self.current_context),
            "selected_items": [item.as_dict() for item in self.selected_items],
            "external_targets": [target.as_dict() for target in self.external_targets],
            "stage19_actions": [action.as_dict() for action in self.stage19_actions],
            "allowed_repositories": list(self.allowed_repositories),
        }

    def to_assistant_request(self) -> AssistantRequest:
        contexts: list[AssistantExplicitContext] = [
            AssistantExplicitContext(AssistantContextKind.FACT, value)
            for value in self.current_context
        ]
        contexts.extend(
            AssistantExplicitContext(
                AssistantContextKind.FACT,
                _json_bytes(item.as_dict()).decode("utf-8"),
            )
            for item in self.selected_items
        )
        capability_projection = {
            "allowed_repositories": list(self.allowed_repositories),
            "stage19_actions": [action.as_dict() for action in self.stage19_actions],
            "external_targets": [target.as_dict() for target in self.external_targets],
        }
        contexts.append(
            AssistantExplicitContext(
                AssistantContextKind.BACKGROUND,
                _json_bytes(capability_projection).decode("utf-8"),
            )
        )
        if len(contexts) > 16 or any(len(item.text.encode("utf-8")) > 1024 for item in contexts):
            raise AgentReasoningEnvelopeTooLargeError()
        request = AssistantRequest(
            task=self.task,
            options=(),
            explicit_constraints=self.constraints,
            explicit_goals=(),
            explicit_context=tuple(contexts),
        )
        try:
            return request
        except (TypeError, ValueError, UnicodeError) as exc:
            raise AgentReasoningEnvelopeInvalidError() from exc

    def to_assistant_envelope(self) -> AssistantReasoningEnvelopeV1:
        try:
            return build_assistant_reasoning_envelope(self.to_assistant_request())
        except AssistantError as exc:
            if exc.code == "ASSISTANT_INVALID_REQUEST":
                raise AgentReasoningEnvelopeInvalidError() from exc
            raise AgentReasoningEnvelopeInvalidError() from exc

    @property
    def canonical_bytes(self) -> bytes:
        try:
            encoded = serialize_assistant_reasoning_envelope(self.to_assistant_envelope())
        except AssistantError as exc:
            raise AgentReasoningEnvelopeInvalidError() from exc
        if len(encoded) > MAX_AGENT_REASONING_CONTEXT_BYTES:
            raise AgentReasoningEnvelopeTooLargeError()
        return encoded


def _json_bytes(value: object) -> bytes:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, UnicodeError, ValueError, OverflowError) as exc:
        raise AgentReasoningEnvelopeInvalidError() from exc


def _reasoning_item(item: object) -> AgentReasoningItemV1:
    if type(item) is not AgentSelectedItemContextV1:
        raise AgentReasoningEnvelopeInvalidError()
    execution = item.execution
    return AgentReasoningItemV1(
        item_alias=f"item:{item.item_id}",
        item_kind=cast(PlanningItemKindV1, item.item_kind).value,
        title=item.title,
        description=item.description,
        goal_aliases=item.goal_aliases,
        action_aliases=item.action_aliases,
        source_status=execution.source_status.value,
        execution_state=execution.state.value,
        planned_effort_minutes=execution.planned_effort_minutes,
        actual_effort_minutes=execution.actual_effort_minutes,
        effort_precision=execution.effort_precision.value,
        current_block_reasons=execution.current_block_reasons,
        terminal_disposition=execution.terminal_disposition,
        deviation_codes=execution.deviation_codes,
        window_relation=execution.window_relation.value,
        event_count=execution.event_count,
        caveats=execution.caveats,
    )


@dataclass(frozen=True, slots=True)
class AgentStage19ActionCandidateV1:
    """Strict non-executable semantic candidate for one Stage 19 action."""

    action_kind: AgentStage19ActionCapabilityV1 | str
    repository: str
    issue_number: int | None = None
    title: str | None = None
    body: str | None = None
    comment: str | None = None
    desired_state: IssueStateV1 | str | None = None

    def __post_init__(self) -> None:
        action_kind = _enum_value(
            self.action_kind,
            AgentStage19ActionCapabilityV1,
            error=AgentRunProposalInvalidError,
        )
        repository = _repository(self.repository, error=AgentRunProposalInvalidError)
        if self.issue_number is not None and (
            type(self.issue_number) is not int
            or isinstance(self.issue_number, bool)
            or not 1 <= self.issue_number <= 2**31 - 1
        ):
            raise AgentRunProposalInvalidError()
        title = self.title
        body = self.body
        comment = self.comment
        desired_state = self.desired_state
        if action_kind is AgentStage19ActionCapabilityV1.GITHUB_ISSUE_CREATE:
            if self.issue_number is not None or title is None or body is None:
                raise AgentRunProposalInvalidError()
            if comment is not None or desired_state is not None:
                raise AgentRunProposalInvalidError()
            title = _text(
                title, max_bytes=MAX_AGENT_ACTION_TITLE_BYTES, error=AgentRunProposalInvalidError
            )
            body = _text(
                body, max_bytes=MAX_AGENT_ACTION_BODY_BYTES, error=AgentRunProposalInvalidError
            )
        elif action_kind is AgentStage19ActionCapabilityV1.GITHUB_ISSUE_COMMENT:
            if self.issue_number is None or comment is None:
                raise AgentRunProposalInvalidError()
            if title is not None or body is not None or desired_state is not None:
                raise AgentRunProposalInvalidError()
            comment = _text(
                comment,
                max_bytes=MAX_AGENT_ACTION_COMMENT_BYTES,
                error=AgentRunProposalInvalidError,
            )
        else:
            if self.issue_number is None or desired_state is None:
                raise AgentRunProposalInvalidError()
            if title is not None or body is not None or comment is not None:
                raise AgentRunProposalInvalidError()
            desired_state = _enum_value(
                desired_state,
                IssueStateV1,
                error=AgentRunProposalInvalidError,
            )
        object.__setattr__(self, "action_kind", action_kind)
        object.__setattr__(self, "repository", repository)
        object.__setattr__(self, "title", title)
        object.__setattr__(self, "body", body)
        object.__setattr__(self, "comment", comment)
        object.__setattr__(self, "desired_state", desired_state)

    def as_dict(self) -> dict[str, object]:
        action_kind = cast(AgentStage19ActionCapabilityV1, self.action_kind)
        if action_kind is AgentStage19ActionCapabilityV1.GITHUB_ISSUE_CREATE:
            return {
                "action_kind": action_kind.value,
                "repository": self.repository,
                "title": self.title,
                "body": self.body,
            }
        if action_kind is AgentStage19ActionCapabilityV1.GITHUB_ISSUE_COMMENT:
            return {
                "action_kind": action_kind.value,
                "repository": self.repository,
                "issue_number": self.issue_number,
                "comment": self.comment,
            }
        return {
            "action_kind": action_kind.value,
            "repository": self.repository,
            "issue_number": self.issue_number,
            "desired_state": cast(IssueStateV1, self.desired_state).value,
        }

    @classmethod
    def from_dict(cls, value: object) -> AgentStage19ActionCandidateV1:
        if not isinstance(value, Mapping):
            raise AgentRunProposalInvalidError()
        raw = dict(value)
        action_kind = raw.get("action_kind")
        if type(action_kind) is not str:
            raise AgentRunProposalInvalidError()
        try:
            normalized = AgentStage19ActionCapabilityV1(action_kind)
        except ValueError as exc:
            raise AgentRunProposalInvalidError() from exc
        if normalized is AgentStage19ActionCapabilityV1.GITHUB_ISSUE_CREATE:
            data = _strict_mapping(
                raw,
                {"action_kind", "repository", "title", "body"},
                error=AgentRunProposalInvalidError,
            )
            return cls(
                action_kind=cast(str, data["action_kind"]),
                repository=cast(str, data["repository"]),
                title=cast(str, data["title"]),
                body=cast(str, data["body"]),
            )
        if normalized is AgentStage19ActionCapabilityV1.GITHUB_ISSUE_COMMENT:
            data = _strict_mapping(
                raw,
                {"action_kind", "repository", "issue_number", "comment"},
                error=AgentRunProposalInvalidError,
            )
            return cls(
                action_kind=cast(str, data["action_kind"]),
                repository=cast(str, data["repository"]),
                issue_number=cast(int, data["issue_number"]),
                comment=cast(str, data["comment"]),
            )
        data = _strict_mapping(
            raw,
            {"action_kind", "repository", "issue_number", "desired_state"},
            error=AgentRunProposalInvalidError,
        )
        return cls(
            action_kind=cast(str, data["action_kind"]),
            repository=cast(str, data["repository"]),
            issue_number=cast(int, data["issue_number"]),
            desired_state=cast(str, data["desired_state"]),
        )


@dataclass(frozen=True, slots=True)
class AgentClarifyStepV1:
    """One neutral clarification step requiring owner input."""

    step_id: str
    position: int
    question: str
    reason: str
    answer_shape: str | None = None

    @property
    def kind(self) -> AgentRunStepKindV1:
        return AgentRunStepKindV1.CLARIFY

    def __post_init__(self) -> None:
        _validate_step_position(self.position)
        object.__setattr__(self, "step_id", _step_id(self.step_id))
        object.__setattr__(
            self,
            "question",
            _text(
                self.question,
                max_bytes=MAX_AGENT_STEP_TEXT_BYTES,
                error=AgentRunProposalInvalidError,
            ),
        )
        object.__setattr__(
            self,
            "reason",
            _text(
                self.reason, max_bytes=MAX_AGENT_STEP_TEXT_BYTES, error=AgentRunProposalInvalidError
            ),
        )
        object.__setattr__(
            self,
            "answer_shape",
            None
            if self.answer_shape is None
            else _text(
                self.answer_shape,
                max_bytes=MAX_AGENT_ANSWER_SHAPE_BYTES,
                error=AgentRunProposalInvalidError,
            ),
        )

    def as_dict(self) -> dict[str, object]:
        return {
            "step_id": self.step_id,
            "position": self.position,
            "kind": self.kind.value,
            "question": self.question,
            "reason": self.reason,
            "answer_shape": self.answer_shape,
        }


@dataclass(frozen=True, slots=True)
class AgentCheckpointStepV1:
    """One bounded checkpoint requiring explicit owner Continue."""

    step_id: str
    position: int
    summary: str

    @property
    def kind(self) -> AgentRunStepKindV1:
        return AgentRunStepKindV1.CHECKPOINT

    def __post_init__(self) -> None:
        _validate_step_position(self.position)
        object.__setattr__(self, "step_id", _step_id(self.step_id))
        object.__setattr__(
            self,
            "summary",
            _text(
                self.summary,
                max_bytes=MAX_AGENT_STEP_TEXT_BYTES,
                error=AgentRunProposalInvalidError,
            ),
        )

    def as_dict(self) -> dict[str, object]:
        return {
            "step_id": self.step_id,
            "position": self.position,
            "kind": self.kind.value,
            "summary": self.summary,
        }


@dataclass(frozen=True, slots=True)
class AgentHoldStepV1:
    """One bounded abstention/hold step with no automatic workaround."""

    step_id: str
    position: int
    reason: str

    @property
    def kind(self) -> AgentRunStepKindV1:
        return AgentRunStepKindV1.HOLD

    def __post_init__(self) -> None:
        _validate_step_position(self.position)
        object.__setattr__(self, "step_id", _step_id(self.step_id))
        object.__setattr__(
            self,
            "reason",
            _text(
                self.reason, max_bytes=MAX_AGENT_STEP_TEXT_BYTES, error=AgentRunProposalInvalidError
            ),
        )

    def as_dict(self) -> dict[str, object]:
        return {
            "step_id": self.step_id,
            "position": self.position,
            "kind": self.kind.value,
            "reason": self.reason,
        }


@dataclass(frozen=True, slots=True)
class AgentStage19ActionStepV1:
    """One non-executable Stage 19 candidate requiring later owner review."""

    step_id: str
    position: int
    action: AgentStage19ActionCandidateV1

    @property
    def kind(self) -> AgentRunStepKindV1:
        return AgentRunStepKindV1.STAGE19_ACTION

    def __post_init__(self) -> None:
        _validate_step_position(self.position)
        object.__setattr__(self, "step_id", _step_id(self.step_id))
        if type(self.action) is not AgentStage19ActionCandidateV1:
            raise AgentRunProposalInvalidError()

    def as_dict(self) -> dict[str, object]:
        return {
            "step_id": self.step_id,
            "position": self.position,
            "kind": self.kind.value,
            "action": self.action.as_dict(),
        }


AgentRunStepV1 = (
    AgentClarifyStepV1 | AgentCheckpointStepV1 | AgentHoldStepV1 | AgentStage19ActionStepV1
)


def _validate_step_position(value: object) -> None:
    if type(value) is not int or isinstance(value, bool) or value < 1:
        raise AgentRunProposalInvalidError()


def _step_from_dict(value: object) -> AgentRunStepV1:
    if not isinstance(value, Mapping):
        raise AgentRunProposalInvalidError()
    raw = dict(value)
    kind = raw.get("kind")
    if type(kind) is not str:
        raise AgentRunProposalInvalidError()
    try:
        normalized = AgentRunStepKindV1(kind)
    except ValueError as exc:
        raise AgentRunProposalInvalidError() from exc
    common = {"step_id", "position", "kind"}
    if normalized is AgentRunStepKindV1.CLARIFY:
        data = _strict_mapping(
            raw,
            common | {"question", "reason", "answer_shape"},
            error=AgentRunProposalInvalidError,
        )
        answer_shape = data["answer_shape"]
        if answer_shape is not None and type(answer_shape) is not str:
            raise AgentRunProposalInvalidError()
        return AgentClarifyStepV1(
            step_id=cast(str, data["step_id"]),
            position=cast(int, data["position"]),
            question=cast(str, data["question"]),
            reason=cast(str, data["reason"]),
            answer_shape=answer_shape,
        )
    if normalized is AgentRunStepKindV1.CHECKPOINT:
        data = _strict_mapping(
            raw,
            common | {"summary"},
            error=AgentRunProposalInvalidError,
        )
        return AgentCheckpointStepV1(
            step_id=cast(str, data["step_id"]),
            position=cast(int, data["position"]),
            summary=cast(str, data["summary"]),
        )
    if normalized is AgentRunStepKindV1.HOLD:
        data = _strict_mapping(
            raw,
            common | {"reason"},
            error=AgentRunProposalInvalidError,
        )
        return AgentHoldStepV1(
            step_id=cast(str, data["step_id"]),
            position=cast(int, data["position"]),
            reason=cast(str, data["reason"]),
        )
    data = _strict_mapping(
        raw,
        common | {"action"},
        error=AgentRunProposalInvalidError,
    )
    return AgentStage19ActionStepV1(
        step_id=cast(str, data["step_id"]),
        position=cast(int, data["position"]),
        action=AgentStage19ActionCandidateV1.from_dict(data["action"]),
    )


def _proposal_core(proposal: AgentRunProposalV1) -> dict[str, object]:
    return {
        "contract_version": proposal.contract_version,
        "proposal_id": str(proposal.proposal_id),
        "mission_fingerprint": proposal.mission_fingerprint,
        "context_pack_fingerprint": proposal.context_pack_fingerprint,
        "steps": [step.as_dict() for step in proposal.steps],
        "caveats": list(proposal.caveats),
        "provider_policy_id": proposal.provider_policy_id,
        "provider_policy_fingerprint": proposal.provider_policy_fingerprint,
        "provider_fingerprint": proposal.provider_fingerprint,
    }


@dataclass(frozen=True, slots=True)
class AgentRunProposalV1:
    """Strict derived linear proposal; it is not accepted or executable state."""

    contract_version: str
    proposal_id: UUID | str
    mission_fingerprint: str
    context_pack_fingerprint: str
    steps: tuple[AgentRunStepV1, ...]
    caveats: tuple[str, ...]
    provider_policy_id: str
    provider_policy_fingerprint: str
    provider_fingerprint: str
    proposal_fingerprint: str

    def __post_init__(self) -> None:
        if self.contract_version != AGENT_RUN_PROPOSAL_CONTRACT_VERSION:
            raise AgentRunProposalInvalidError()
        proposal_id = _uuid7(self.proposal_id, error=AgentRunProposalInvalidError)
        mission_fingerprint = _hash(
            self.mission_fingerprint,
            error=AgentRunProposalInvalidError,
        )
        context_pack_fingerprint = _hash(
            self.context_pack_fingerprint,
            error=AgentRunProposalInvalidError,
        )
        provider_fingerprint = _hash(
            self.provider_fingerprint,
            error=AgentRunProposalInvalidError,
        )
        if (
            self.provider_policy_id != AGENT_POLICY_ID
            or self.provider_policy_fingerprint != AGENT_POLICY_FINGERPRINT
        ):
            raise AgentRunProposalInvalidError()
        if type(self.steps) is not tuple or not 1 <= len(self.steps) <= MAX_AGENT_PROPOSAL_STEPS:
            raise AgentRunProposalInvalidError()
        if any(
            type(step)
            not in {
                AgentClarifyStepV1,
                AgentCheckpointStepV1,
                AgentHoldStepV1,
                AgentStage19ActionStepV1,
            }
            for step in self.steps
        ):
            raise AgentRunProposalInvalidError()
        steps = tuple(self.steps)
        if tuple(step.position for step in steps) != tuple(range(1, len(steps) + 1)):
            raise AgentRunProposalInvalidError()
        if len({step.step_id for step in steps}) != len(steps):
            raise AgentRunProposalInvalidError()
        caveats = _tuple_texts(
            self.caveats,
            maximum=MAX_AGENT_PROPOSAL_CAVEATS,
            max_bytes=MAX_AGENT_PROPOSAL_CAVEAT_BYTES,
            error=AgentRunProposalInvalidError,
        )
        object.__setattr__(self, "proposal_id", proposal_id)
        object.__setattr__(self, "mission_fingerprint", mission_fingerprint)
        object.__setattr__(self, "context_pack_fingerprint", context_pack_fingerprint)
        object.__setattr__(self, "provider_fingerprint", provider_fingerprint)
        object.__setattr__(self, "steps", steps)
        object.__setattr__(self, "caveats", caveats)
        expected = personal_agent_hash(_proposal_core(self))
        if _hash(self.proposal_fingerprint, error=AgentRunProposalInvalidError) != expected:
            raise AgentRunProposalInvalidError()
        if len(_canonical_bytes(self.as_dict())) > MAX_AGENT_PROVIDER_PROPOSAL_BYTES:
            raise AgentRunProposalInvalidError()

    def _core_dict(self) -> dict[str, object]:
        return _proposal_core(self)

    def as_dict(self) -> dict[str, object]:
        return {**self._core_dict(), "proposal_fingerprint": self.proposal_fingerprint}

    @property
    def fingerprint(self) -> str:
        return self.proposal_fingerprint

    @classmethod
    def from_dict(cls, value: object) -> AgentRunProposalV1:
        data = _strict_mapping(
            value,
            {
                "contract_version",
                "proposal_id",
                "mission_fingerprint",
                "context_pack_fingerprint",
                "steps",
                "caveats",
                "provider_policy_id",
                "provider_policy_fingerprint",
                "provider_fingerprint",
                "proposal_fingerprint",
            },
            error=AgentRunProposalInvalidError,
        )
        steps = data["steps"]
        caveats = data["caveats"]
        if type(steps) is not list or type(caveats) is not list:
            raise AgentRunProposalInvalidError()
        return cls(
            contract_version=cast(str, data["contract_version"]),
            proposal_id=cast(UUID | str, data["proposal_id"]),
            mission_fingerprint=cast(str, data["mission_fingerprint"]),
            context_pack_fingerprint=cast(str, data["context_pack_fingerprint"]),
            steps=tuple(_step_from_dict(item) for item in steps),
            caveats=tuple(cast(str, item) for item in caveats),
            provider_policy_id=cast(str, data["provider_policy_id"]),
            provider_policy_fingerprint=cast(str, data["provider_policy_fingerprint"]),
            provider_fingerprint=cast(str, data["provider_fingerprint"]),
            proposal_fingerprint=cast(str, data["proposal_fingerprint"]),
        )


def _provider_fingerprint(result: AssistantResultEnvelopeV1) -> str:
    return hashlib.sha256(serialize_assistant_result_envelope(result)).hexdigest()


def _parse_provider_recommendation(
    value: str,
) -> tuple[tuple[AgentRunStepV1, ...], tuple[str, ...]]:
    try:
        decoded = json.loads(
            value,
            object_pairs_hook=lambda pairs: _reject_duplicate_pairs(pairs),
            parse_constant=lambda _: (_ for _ in ()).throw(ValueError()),
        )
    except (TypeError, UnicodeDecodeError, ValueError, json.JSONDecodeError, RecursionError) as exc:
        raise AgentRunProposalInvalidError() from exc
    data = _strict_mapping(
        decoded,
        {"steps", "caveats"},
        error=AgentRunProposalInvalidError,
    )
    raw_steps = data["steps"]
    raw_caveats = data["caveats"]
    if type(raw_steps) is not list or type(raw_caveats) is not list:
        raise AgentRunProposalInvalidError()
    if any(type(item) is not str for item in raw_caveats):
        raise AgentRunProposalInvalidError()
    steps = tuple(_step_from_dict(item) for item in raw_steps)
    caveats = tuple(
        _text(
            item,
            max_bytes=MAX_AGENT_PROPOSAL_CAVEAT_BYTES,
            error=AgentRunProposalInvalidError,
        )
        for item in raw_caveats
    )
    return steps, caveats


def _reject_duplicate_pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError()
        result[key] = value
    return result


def _abstention_reason(code: object) -> str:
    reasons = {
        AssistantAbstentionCode.INSUFFICIENT_BASIS: (
            "Провайдер не располагает достаточным основанием для Run."
        ),
        AssistantAbstentionCode.CONFLICTING_EXPLICIT_CONSTRAINTS: (
            "Явные ограничения Mission конфликтуют."
        ),
        AssistantAbstentionCode.AMBIGUOUS_OR_INCOMPARABLE_OPTIONS: (
            "Доступный контекст неоднозначен для Run."
        ),
        AssistantAbstentionCode.UNSUPPORTED_TASK: "Задача Mission не поддерживается этим Advisor.",
    }
    normalized = _enum_value(
        code,
        AssistantAbstentionCode,
        error=AgentRunProposalInvalidError,
    )
    return reasons[normalized]


def _validate_action_targets(
    pack: AgentContextPackV1,
    steps: tuple[AgentRunStepV1, ...],
) -> None:
    allowed_repositories = {repository.casefold() for repository in pack.stage19.repositories}
    catalog = {
        cast(AgentStage19ActionCapabilityV1, item.action_kind)
        for item in pack.stage19.action_catalog
    }
    explicit_targets = {
        (
            cast(AgentStage19ActionCapabilityV1, target.action_kind),
            target.repository.casefold(),
            target.issue_number,
        )
        for target in pack.mission.external_targets
    }
    for step in steps:
        if type(step) is not AgentStage19ActionStepV1:
            continue
        action = step.action
        action_kind = cast(AgentStage19ActionCapabilityV1, action.action_kind)
        if action_kind not in catalog or action.repository.casefold() not in allowed_repositories:
            raise AgentRunProposalInvalidError()
        if (
            action_kind,
            action.repository.casefold(),
            action.issue_number,
        ) not in explicit_targets:
            raise AgentRunProposalInvalidError()


def build_agent_run_proposal_from_advisor_result(
    pack: AgentContextPackV1,
    result: AssistantResultEnvelopeV1,
    *,
    proposal_id: UUID | str | None = None,
) -> AgentRunProposalV1:
    """Convert exactly one validated Assistant result into a strict proposal."""

    if type(pack) is not AgentContextPackV1:
        raise AgentContextPackInvalidError()
    validated_pack = validate_agent_context_pack(pack)
    if type(result) is not AssistantResultEnvelopeV1:
        raise AgentRunProposalInvalidError()
    provider_fingerprint = _provider_fingerprint(result)
    kind = _enum_value(result.kind, AssistantResultKind, error=AgentRunProposalInvalidError)
    steps: tuple[AgentRunStepV1, ...]
    caveats: tuple[str, ...]
    if kind is AssistantResultKind.ABSTENTION:
        steps = (AgentHoldStepV1("hold-1", 1, _abstention_reason(result.abstention_code)),)
        caveats = ("advisor_abstention",)
    elif kind is AssistantResultKind.RECOMMENDATION and result.recommendation is not None:
        if len(result.recommendation.encode("utf-8")) > MAX_AGENT_PROVIDER_PROPOSAL_BYTES:
            raise AgentRunProposalInvalidError()
        steps, caveats = _parse_provider_recommendation(result.recommendation)
        _validate_action_targets(validated_pack, steps)
    else:
        raise AgentRunProposalInvalidError()
    resolved_proposal_id = (
        uuid7()
        if proposal_id is None
        else _uuid7(
            proposal_id,
            error=AgentRunProposalInvalidError,
        )
    )
    proposal_core = {
        "contract_version": AGENT_RUN_PROPOSAL_CONTRACT_VERSION,
        "proposal_id": str(resolved_proposal_id),
        "mission_fingerprint": validated_pack.mission.fingerprint,
        "context_pack_fingerprint": validated_pack.fingerprint,
        "steps": [step.as_dict() for step in steps],
        "caveats": list(caveats),
        "provider_policy_id": AGENT_POLICY_ID,
        "provider_policy_fingerprint": AGENT_POLICY_FINGERPRINT,
        "provider_fingerprint": provider_fingerprint,
    }
    return AgentRunProposalV1(
        contract_version=AGENT_RUN_PROPOSAL_CONTRACT_VERSION,
        proposal_id=resolved_proposal_id,
        mission_fingerprint=validated_pack.mission.fingerprint,
        context_pack_fingerprint=validated_pack.fingerprint,
        steps=steps,
        caveats=caveats,
        provider_policy_id=AGENT_POLICY_ID,
        provider_policy_fingerprint=AGENT_POLICY_FINGERPRINT,
        provider_fingerprint=provider_fingerprint,
        proposal_fingerprint=personal_agent_hash(proposal_core),
    )


def parse_agent_run_step(value: object) -> AgentRunStepV1:
    """Parse one owner-reviewed step through the closed proposal vocabulary."""

    return _step_from_dict(value)


class BuildPersonalAgentRun:
    """Build one proposal only after an explicit foreground caller invokes it."""

    def __init__(self, advisor: AdvisorPort) -> None:
        self._advisor = advisor

    def execute(
        self,
        pack: AgentContextPackV1,
        *,
        cancellation: CancellationToken,
        proposal_id: UUID | str | None = None,
    ) -> AgentRunProposalV1:
        """Call the existing Advisor once and return no executable authority."""

        if cancellation.is_cancelled():
            raise AgentPlannerCancelledError()
        envelope = build_agent_reasoning_envelope(pack)
        request = envelope.to_assistant_request()
        try:
            result = BuildAssistant(self._advisor).execute(request, cancellation=cancellation)
        except AssistantCancelledError as exc:
            raise AgentPlannerCancelledError() from exc
        except AssistantInvalidRequestError as exc:
            raise AgentReasoningEnvelopeInvalidError() from exc
        except (
            AssistantProviderUnavailableError,
            AssistantProviderFailureError,
            AssistantTimeoutError,
        ) as exc:
            raise AgentPlannerProviderUnavailableError() from exc
        except (
            AssistantMalformedResultError,
            AssistantResultTooLargeError,
            AssistantResultInvalidError,
        ) as exc:
            raise AgentPlannerProviderResultInvalidError() from exc
        except AssistantError as exc:
            raise AgentPlannerProviderResultInvalidError() from exc
        return build_agent_run_proposal_from_advisor_result(
            pack,
            result,
            proposal_id=proposal_id,
        )

    def build(
        self,
        pack: AgentContextPackV1,
        *,
        cancellation: CancellationToken,
        proposal_id: UUID | str | None = None,
    ) -> AgentRunProposalV1:
        """Named convenience alias for the explicit Build run action."""

        return self.execute(pack, cancellation=cancellation, proposal_id=proposal_id)


def build_agent_reasoning_envelope(pack: AgentContextPackV1) -> AgentReasoningEnvelopeV1:
    """Build the exact preview without calling a provider or mutating state."""

    try:
        return AgentReasoningEnvelopeV1.from_context_pack(pack)
    except AgentContextPackInvalidError:
        raise
    except PersonalAgentError:
        raise
    except (TypeError, ValueError, UnicodeError) as exc:
        raise AgentReasoningEnvelopeInvalidError() from exc


def validate_agent_run_proposal(value: object) -> AgentRunProposalV1:
    """Revalidate one immutable proposal fingerprint without provider I/O."""

    if type(value) is not AgentRunProposalV1:
        raise AgentRunProposalInvalidError()
    expected = personal_agent_hash(_proposal_core(value))
    if expected != value.proposal_fingerprint:
        raise AgentRunProposalInvalidError()
    return value


def serialize_agent_reasoning_envelope(value: object) -> bytes:
    """Return the exact canonical bytes passed to the existing AdvisorPort."""

    if type(value) is not AgentReasoningEnvelopeV1:
        raise AgentReasoningEnvelopeInvalidError()
    return value.canonical_bytes


def serialize_agent_run_proposal(value: object) -> bytes:
    """Serialize one validated proposal without raw provider output."""

    return _canonical_bytes(validate_agent_run_proposal(value).as_dict())


__all__ = [
    "AGENT_REASONING_ENVELOPE_CONTRACT_VERSION",
    "AGENT_RUN_PROPOSAL_CONTRACT_VERSION",
    "MAX_AGENT_PROPOSAL_STEPS",
    "AgentCheckpointStepV1",
    "AgentClarifyStepV1",
    "AgentHoldStepV1",
    "AgentPlannerCancelledError",
    "AgentPlannerError",
    "AgentPlannerProviderResultInvalidError",
    "AgentPlannerProviderUnavailableError",
    "AgentReasoningActionV1",
    "AgentReasoningEnvelopeInvalidError",
    "AgentReasoningEnvelopeTooLargeError",
    "AgentReasoningEnvelopeV1",
    "AgentReasoningItemV1",
    "AgentReasoningTargetV1",
    "AgentRunProposalInvalidError",
    "AgentRunProposalV1",
    "AgentRunStepKindV1",
    "AgentStage19ActionCandidateV1",
    "AgentStage19ActionStepV1",
    "BuildPersonalAgentRun",
    "build_agent_reasoning_envelope",
    "build_agent_run_proposal_from_advisor_result",
    "parse_agent_run_step",
    "serialize_agent_reasoning_envelope",
    "serialize_agent_run_proposal",
    "validate_agent_run_proposal",
]
