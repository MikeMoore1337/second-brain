"""Provider-free Executive Context Pack for Personal Strategy v1.

Phase 16.1 deliberately stops at a deterministic in-memory boundary.  This
module does not read the vault, call an advisor, write an operational store,
or select a Goal implicitly.  Callers provide one already validated current
``GrowthGoalIdentityV1`` and bounded projections of the other source families.
"""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import Final, cast
from uuid import UUID, uuid7

from second_brain.application.assistant import (
    AssistantAbstentionCode,
    AssistantContextKind,
    AssistantError,
    AssistantExplicitContext,
    AssistantReasoningEnvelopeV1,
    AssistantRequest,
    AssistantResultEnvelopeV1,
    AssistantResultKind,
    BuildAssistant,
    build_assistant_reasoning_envelope,
    serialize_assistant_reasoning_envelope,
    serialize_assistant_result_envelope,
)
from second_brain.application.growth import (
    GROWTH_POLICY_FINGERPRINT,
    GrowthGoalIdentityV1,
    growth_hash_json,
)
from second_brain.application.ports import AdvisorPort, CancellationToken
from second_brain.domain.models import parse_rfc3339, parse_uuid7

ExecutiveHashV1 = str

CONTRACT_VERSION: Final[str] = "executive-strategy-v1"
EXECUTIVE_CONTRACT_VERSION: Final[str] = CONTRACT_VERSION
PACK_VERSION: Final[str] = "1"
EXECUTIVE_PACK_VERSION: Final[str] = PACK_VERSION
POLICY_ID: Final[str] = "stage16-executive-strategy-v1"
EXECUTIVE_POLICY_ID: Final[str] = POLICY_ID

POLICY_CANONICAL_JSON: Final[str] = (
    '{"contract_id":"executive-strategy-v1","contract_version":"1",'
    '"policy_id":"stage16-executive-strategy-v1",'
    '"action_kinds":["act","investigate","clarify","experiment_candidate","hold"],'
    '"result_states":["proposal","insufficient_context","insufficient_evidence",'
    '"not_comparable","source_changed","conflicting_constraints",'
    '"hold_current_strategy","provider_unavailable","provider_abstained"],'
    '"source_aliases":["goal.current","growth.relation","progress.current",'
    '"behavior.relation","experiment.terminal","adaptive_profile.active",'
    '"calibration.caveat","caller.task","caller.constraints","caller.context"]}'
)
POLICY_FINGERPRINT: Final[ExecutiveHashV1] = (
    "5af1723247319830f87432d1827fd47a29288ea957c0a05bf51440cb5ab9b7aa"
)
EXECUTIVE_POLICY_CANONICAL_JSON: Final[str] = POLICY_CANONICAL_JSON
EXECUTIVE_POLICY_FINGERPRINT: Final[ExecutiveHashV1] = POLICY_FINGERPRINT

MAX_GOAL_TEXT_BYTES: Final[int] = 4096
MAX_TASK_BYTES: Final[int] = 2048
MAX_CONSTRAINTS: Final[int] = 8
MAX_CONSTRAINT_BYTES: Final[int] = 512
MAX_CONSTRAINTS_BYTES: Final[int] = 4096
MAX_CONTEXT_BYTES: Final[int] = 4096
MAX_SOURCE_SUMMARY_BYTES: Final[int] = 4096
MAX_SOURCE_REFERENCE_ID_BYTES: Final[int] = 256
MAX_SOURCE_POLICY_FINGERPRINTS: Final[int] = 8
MAX_SOURCE_POLICY_FINGERPRINT_BYTES: Final[int] = 128
MAX_PACK_CAVEATS: Final[int] = 8
MAX_CAVEAT_BYTES: Final[int] = 512
MAX_PACK_BYTES: Final[int] = 64 * 1024
MAX_ACTION_ID_BYTES: Final[int] = 64
MAX_ACTION_TITLE_BYTES: Final[int] = 256
MAX_ACTION_DESCRIPTION_BYTES: Final[int] = 2048
MAX_ACTION_GOAL_RELATION_BYTES: Final[int] = 512
MAX_ACTION_SIGNAL_BYTES: Final[int] = 1024
MAX_ACTION_LISTS: Final[int] = 8
MAX_PROPOSAL_REASONS: Final[int] = 8
MAX_PROPOSAL_CAVEATS: Final[int] = 8
MAX_PROPOSAL_BYTES: Final[int] = 64 * 1024
MAX_SNAPSHOT_SELECTED_ACTIONS: Final[int] = 8
MAX_SNAPSHOT_BYTES: Final[int] = 128 * 1024

_RAW_HASH_PATTERN: Final[re.Pattern[str]] = re.compile(r"[0-9a-f]{64}\Z", re.ASCII)
_GROWTH_HASH_PATTERN: Final[re.Pattern[str]] = re.compile(r"sha256:[0-9a-f]{64}\Z", re.ASCII)
_REFERENCE_PATTERN: Final[re.Pattern[str]] = re.compile(r"[^\x00-\x1f\x7f-\x9f]+\Z")
_ACTION_ID_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"[A-Za-z0-9][A-Za-z0-9._:-]{0,63}\Z", re.ASCII
)
_PROHIBITED_CANDIDATE_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"(?:`|https?://|www\.|file://|[A-Za-z]:[\\/]|"
    r"\b(?:powershell|cmd(?:\.exe)?|bash|shell|curl|wget|python|git|npm|docker|ssh|"
    r"api[_ -]?key|access[_ -]?token|password|secret|browser automation|tool call)\b)",
    re.IGNORECASE,
)


class ExecutiveContextError(ValueError):
    """Base error for invalid Stage 16.1 values."""


class ExecutiveContextInvalidError(ExecutiveContextError):
    """A bounded context value failed the immutable v1 contract."""


class ExecutivePolicyMismatchError(ExecutiveContextError):
    """A context value belongs to another policy or contract revision."""


class ExecutiveStrategyError(RuntimeError):
    """Safe error at the explicit Stage 16 strategy boundary."""


class ExecutiveStrategyCancelledError(ExecutiveStrategyError):
    """The one explicit strategy operation was cancelled."""


class ExecutiveProviderUnavailableError(ExecutiveStrategyError):
    """The already-approved Advisor boundary is unavailable."""


class ExecutiveProviderResultInvalidError(ExecutiveStrategyError):
    """The Advisor result failed the Assistant or Stage 16 mapping contract."""


class ExecutiveSourceAliasV1(StrEnum):
    """Closed source aliases and their normative provider-visible order."""

    GOAL_CURRENT = "goal.current"
    GROWTH_RELATION = "growth.relation"
    PROGRESS_CURRENT = "progress.current"
    BEHAVIOR_RELATION = "behavior.relation"
    EXPERIMENT_TERMINAL = "experiment.terminal"
    ADAPTIVE_PROFILE_ACTIVE = "adaptive_profile.active"
    CALIBRATION_CAVEAT = "calibration.caveat"
    CALLER_TASK = "caller.task"
    CALLER_CONSTRAINTS = "caller.constraints"
    CALLER_CONTEXT = "caller.context"


ExecutiveSourceAlias = ExecutiveSourceAliasV1

SOURCE_ALIASES: Final[tuple[ExecutiveSourceAliasV1, ...]] = tuple(ExecutiveSourceAliasV1)
EXECUTIVE_SOURCE_ALIASES: Final[tuple[ExecutiveSourceAliasV1, ...]] = SOURCE_ALIASES


class ExecutiveSourceReadinessV1(StrEnum):
    """Closed readiness vocabulary for one source family."""

    EXACT_CURRENT = "exact_current"
    MISSING = "missing"
    STALE = "stale"
    CONFLICT = "conflict"
    NOT_COMPARABLE = "not_comparable"
    POLICY_MISMATCH = "policy_mismatch"


ExecutiveSourceReadiness = ExecutiveSourceReadinessV1


class ExecutivePackReadinessV1(StrEnum):
    """Fail-closed aggregate readiness of one context pack."""

    EXACT_CURRENT = "exact_current"
    INCOMPLETE = "incomplete"
    CONFLICT = "conflict"
    SOURCE_CHANGED = "source_changed"


ExecutivePackReadiness = ExecutivePackReadinessV1


class ExecutiveActionKindV1(StrEnum):
    """Closed non-executable candidate kinds."""

    ACT = "act"
    INVESTIGATE = "investigate"
    CLARIFY = "clarify"
    EXPERIMENT_CANDIDATE = "experiment_candidate"
    HOLD = "hold"


ExecutiveActionKind = ExecutiveActionKindV1


class ExecutiveResultStateV1(StrEnum):
    """Closed proposal and abstention result vocabulary."""

    PROPOSAL = "proposal"
    INSUFFICIENT_CONTEXT = "insufficient_context"
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"
    NOT_COMPARABLE = "not_comparable"
    SOURCE_CHANGED = "source_changed"
    CONFLICTING_CONSTRAINTS = "conflicting_constraints"
    HOLD_CURRENT_STRATEGY = "hold_current_strategy"
    PROVIDER_UNAVAILABLE = "provider_unavailable"
    PROVIDER_ABSTAINED = "provider_abstained"


ExecutiveResultState = ExecutiveResultStateV1


def _invalid() -> ExecutiveContextInvalidError:
    return ExecutiveContextInvalidError("executive context failed validation")


def _policy_mismatch() -> ExecutivePolicyMismatchError:
    return ExecutivePolicyMismatchError("executive context policy mismatch")


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
    normalized = unicodedata.normalize("NFC", value).strip()
    if not allow_empty and not normalized:
        raise _invalid()
    if allow_empty and not normalized:
        return ""
    if _has_forbidden_codepoint(normalized):
        raise _invalid()
    try:
        if not 1 <= len(normalized.encode("utf-8")) <= limit:
            raise _invalid()
    except UnicodeEncodeError:
        raise _invalid() from None
    return normalized


def _optional_text(value: object | None, *, limit: int) -> str | None:
    if value is None:
        return None
    return _text(value, limit=limit)


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


def _hash(value: object, *, growth: bool = False) -> str:
    if type(value) is not str:
        raise _invalid()
    if growth:
        if _GROWTH_HASH_PATTERN.fullmatch(value) is None:
            raise _invalid()
    elif (
        _RAW_HASH_PATTERN.fullmatch(value) is None and _GROWTH_HASH_PATTERN.fullmatch(value) is None
    ):
        raise _invalid()
    return value


def _raw_digest(value: object) -> str:
    if type(value) is not str or _RAW_HASH_PATTERN.fullmatch(value) is None:
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


def _raw_hash(value: object) -> ExecutiveHashV1:
    return hashlib.sha256(_canonical_bytes(value)).hexdigest()


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


def _reference_id(value: object | None) -> str | None:
    if value is None:
        return None
    normalized = _text(value, limit=MAX_SOURCE_REFERENCE_ID_BYTES)
    if _REFERENCE_PATTERN.fullmatch(normalized) is None:
        raise _invalid()
    if "/" in normalized or "\\" in normalized or ".." in normalized:
        raise _invalid()
    return normalized


def _policy_fingerprints(value: object) -> tuple[str, ...]:
    if type(value) is not tuple or len(value) > MAX_SOURCE_POLICY_FINGERPRINTS:
        raise _invalid()
    normalized = tuple(_text(item, limit=MAX_SOURCE_POLICY_FINGERPRINT_BYTES) for item in value)
    if any(
        _RAW_HASH_PATTERN.fullmatch(item) is None and _GROWTH_HASH_PATTERN.fullmatch(item) is None
        for item in normalized
    ):
        raise _invalid()
    if len(set(normalized)) != len(normalized):
        raise _invalid()
    return normalized


@dataclass(frozen=True, slots=True)
class ExecutiveSourceItemV1:
    """Safe bounded projection of one Stage 16 source family."""

    alias: ExecutiveSourceAliasV1 | str
    readiness: ExecutiveSourceReadinessV1 | str
    reference_id: str | None
    reference_fingerprint: ExecutiveHashV1 | None
    summary: str
    policy_fingerprints: tuple[str, ...] = ()
    as_of: datetime | None = None

    def __post_init__(self) -> None:
        alias = _enum_value(self.alias, ExecutiveSourceAliasV1)
        readiness = _enum_value(self.readiness, ExecutiveSourceReadinessV1)
        reference_id = _reference_id(self.reference_id)
        reference_fingerprint = (
            None if self.reference_fingerprint is None else _hash(self.reference_fingerprint)
        )
        summary = _text(self.summary, limit=MAX_SOURCE_SUMMARY_BYTES)
        policies = _policy_fingerprints(self.policy_fingerprints)
        source_as_of = None if self.as_of is None else _timestamp(self.as_of)
        if readiness is ExecutiveSourceReadinessV1.EXACT_CURRENT and reference_id is None:
            raise _invalid()
        # An unavailable or conflicting source may retain a stable reference,
        # but a missing source must never look like an exact current binding.
        if readiness is ExecutiveSourceReadinessV1.MISSING and reference_id is not None:
            raise _invalid()
        object.__setattr__(self, "alias", alias)
        object.__setattr__(self, "readiness", readiness)
        object.__setattr__(self, "reference_id", reference_id)
        object.__setattr__(self, "reference_fingerprint", reference_fingerprint)
        object.__setattr__(self, "summary", summary)
        object.__setattr__(self, "policy_fingerprints", policies)
        object.__setattr__(self, "as_of", source_as_of)

    def as_dict(self) -> dict[str, object]:
        """Return the exact bounded wire projection."""

        return {
            "alias": cast(ExecutiveSourceAliasV1, self.alias).value,
            "readiness": cast(ExecutiveSourceReadinessV1, self.readiness).value,
            "reference_id": self.reference_id,
            "reference_fingerprint": self.reference_fingerprint,
            "summary": self.summary,
            "policy_fingerprints": list(self.policy_fingerprints),
            "as_of": None if self.as_of is None else _format_timestamp(self.as_of),
        }

    @classmethod
    def from_dict(cls, value: object) -> ExecutiveSourceItemV1:
        """Rebuild one source item from its closed canonical projection."""

        if type(value) is not dict:
            raise _invalid()
        expected = {
            "alias",
            "readiness",
            "reference_id",
            "reference_fingerprint",
            "summary",
            "policy_fingerprints",
            "as_of",
        }
        if set(value) != expected:
            raise _invalid()
        policies = value["policy_fingerprints"]
        if type(policies) is not list:
            raise _invalid()
        return cls(
            alias=value["alias"],
            readiness=value["readiness"],
            reference_id=value["reference_id"],
            reference_fingerprint=value["reference_fingerprint"],
            summary=value["summary"],
            policy_fingerprints=tuple(cast(list[str], policies)),
            as_of=value["as_of"],
        )


ExecutiveSourceProjectionV1 = ExecutiveSourceItemV1


def goal_identity_fingerprint(goal: GrowthGoalIdentityV1) -> ExecutiveHashV1:
    """Hash the exact Growth identity, never Goal text or a fuzzy projection."""

    if type(goal) is not GrowthGoalIdentityV1:
        raise _invalid()
    try:
        return growth_hash_json(goal.as_dict())
    except TypeError, ValueError, UnicodeError:
        raise _invalid() from None


compute_goal_identity_fingerprint = goal_identity_fingerprint


def _pack_core(pack: ExecutiveContextPackV1) -> dict[str, object]:
    """Build the ordered fingerprint payload without the fingerprint itself."""

    return {
        "contract_version": pack.contract_version,
        "pack_version": pack.pack_version,
        "as_of": _format_timestamp(pack.as_of),
        "goal_source_uuid": str(pack.goal_source_uuid),
        "goal_identity_fingerprint": pack.goal_identity_fingerprint,
        "goal_text": pack.goal_text,
        "task": pack.task,
        "constraints": list(pack.constraints),
        "current_context": pack.current_context,
        "sources": [item.as_dict() for item in pack.sources],
        "readiness": cast(ExecutivePackReadinessV1, pack.readiness).value,
        "pack_caveats": list(pack.pack_caveats),
        "policy_id": pack.policy_id,
        "policy_fingerprint": pack.policy_fingerprint,
    }


@dataclass(frozen=True, slots=True)
class ExecutiveContextPackV1:
    """Immutable, provider-free, exact-one-Goal Executive context."""

    contract_version: str
    pack_version: str
    as_of: datetime
    goal_source_uuid: UUID | str
    goal_identity_fingerprint: ExecutiveHashV1
    goal_text: str
    task: str
    constraints: tuple[str, ...]
    current_context: str
    sources: tuple[ExecutiveSourceItemV1, ...]
    readiness: ExecutivePackReadinessV1 | str
    pack_caveats: tuple[str, ...]
    policy_id: str
    policy_fingerprint: ExecutiveHashV1
    source_pack_fingerprint: ExecutiveHashV1

    def __post_init__(self) -> None:
        if self.contract_version != CONTRACT_VERSION or self.pack_version != PACK_VERSION:
            raise _policy_mismatch()
        as_of = _timestamp(self.as_of)
        goal_uuid = _uuid7(self.goal_source_uuid)
        goal_fp = _hash(self.goal_identity_fingerprint, growth=True)
        goal_text = _text(self.goal_text, limit=MAX_GOAL_TEXT_BYTES)
        task = _text(self.task, limit=MAX_TASK_BYTES)
        constraints = _tuple_texts(
            self.constraints,
            maximum=MAX_CONSTRAINTS,
            item_limit=MAX_CONSTRAINT_BYTES,
            total_limit=MAX_CONSTRAINTS_BYTES,
        )
        current_context = _text(
            self.current_context,
            limit=MAX_CONTEXT_BYTES,
            allow_empty=True,
        )
        if type(self.sources) is not tuple or len(self.sources) != len(SOURCE_ALIASES):
            raise _invalid()
        if any(type(item) is not ExecutiveSourceItemV1 for item in self.sources):
            raise _invalid()
        sources = tuple(self.sources)
        aliases = tuple(cast(ExecutiveSourceAliasV1, item.alias) for item in sources)
        if aliases != SOURCE_ALIASES:
            raise _invalid()
        readiness = _enum_value(self.readiness, ExecutivePackReadinessV1)
        if type(self.pack_caveats) is not tuple or len(self.pack_caveats) > MAX_PACK_CAVEATS:
            raise _invalid()
        caveats = tuple(_text(item, limit=MAX_CAVEAT_BYTES) for item in self.pack_caveats)
        if len(set(caveats)) != len(caveats):
            raise _invalid()
        if self.policy_id != POLICY_ID or self.policy_fingerprint != POLICY_FINGERPRINT:
            raise _policy_mismatch()
        supplied_fingerprint = _raw_digest(self.source_pack_fingerprint)
        object.__setattr__(self, "as_of", as_of)
        object.__setattr__(self, "goal_source_uuid", goal_uuid)
        object.__setattr__(self, "goal_identity_fingerprint", goal_fp)
        object.__setattr__(self, "goal_text", goal_text)
        object.__setattr__(self, "task", task)
        object.__setattr__(self, "constraints", constraints)
        object.__setattr__(self, "current_context", current_context)
        object.__setattr__(self, "sources", sources)
        object.__setattr__(self, "readiness", readiness)
        object.__setattr__(self, "pack_caveats", caveats)
        object.__setattr__(self, "policy_id", POLICY_ID)
        object.__setattr__(self, "policy_fingerprint", POLICY_FINGERPRINT)
        expected = _raw_hash(_pack_core(self))
        if supplied_fingerprint != expected:
            raise _invalid()
        object.__setattr__(self, "source_pack_fingerprint", expected)
        if len(_canonical_bytes(self.as_dict())) > MAX_PACK_BYTES:
            raise _invalid()

    def as_dict(self) -> dict[str, object]:
        """Return the bounded canonical projection including its fingerprint."""

        return {
            **_pack_core(self),
            "source_pack_fingerprint": self.source_pack_fingerprint,
        }

    def to_json(self) -> str:
        """Return compact canonical UTF-8 JSON as text."""

        return _canonical_bytes(self.as_dict()).decode("utf-8")

    @classmethod
    def from_dict(cls, value: object) -> ExecutiveContextPackV1:
        """Rebuild a pack only from its exact bounded JSON projection."""

        if type(value) is not dict:
            raise _invalid()
        expected = {
            "contract_version",
            "pack_version",
            "as_of",
            "goal_source_uuid",
            "goal_identity_fingerprint",
            "goal_text",
            "task",
            "constraints",
            "current_context",
            "sources",
            "readiness",
            "pack_caveats",
            "policy_id",
            "policy_fingerprint",
            "source_pack_fingerprint",
        }
        if set(value) != expected:
            raise _invalid()
        constraints = value["constraints"]
        sources = value["sources"]
        caveats = value["pack_caveats"]
        if type(constraints) is not list or type(sources) is not list or type(caveats) is not list:
            raise _invalid()
        return cls(
            contract_version=value["contract_version"],
            pack_version=value["pack_version"],
            as_of=value["as_of"],
            goal_source_uuid=value["goal_source_uuid"],
            goal_identity_fingerprint=value["goal_identity_fingerprint"],
            goal_text=value["goal_text"],
            task=value["task"],
            constraints=tuple(cast(list[str], constraints)),
            current_context=value["current_context"],
            sources=tuple(ExecutiveSourceItemV1.from_dict(item) for item in sources),
            readiness=value["readiness"],
            pack_caveats=tuple(cast(list[str], caveats)),
            policy_id=value["policy_id"],
            policy_fingerprint=value["policy_fingerprint"],
            source_pack_fingerprint=value["source_pack_fingerprint"],
        )


ExecutiveContextPack = ExecutiveContextPackV1


def _normalize_source_mapping(
    sources: Mapping[ExecutiveSourceAliasV1 | str, ExecutiveSourceItemV1]
    | Sequence[ExecutiveSourceItemV1]
    | None,
) -> dict[ExecutiveSourceAliasV1, ExecutiveSourceItemV1]:
    if sources is None:
        return {}
    if isinstance(sources, Mapping):
        pairs = tuple(sources.items())
    elif type(sources) in (tuple, list):
        pairs = tuple((item.alias, item) for item in sources)
    else:
        raise _invalid()
    normalized: dict[ExecutiveSourceAliasV1, ExecutiveSourceItemV1] = {}
    for key, item in pairs:
        if type(item) is not ExecutiveSourceItemV1:
            raise _invalid()
        alias = _enum_value(key, ExecutiveSourceAliasV1)
        item_alias = _enum_value(item.alias, ExecutiveSourceAliasV1)
        if alias is not item_alias or alias in normalized:
            raise _invalid()
        normalized[alias] = item
    return normalized


def _caller_source(
    alias: ExecutiveSourceAliasV1,
    summary: str,
    *,
    as_of: datetime,
) -> ExecutiveSourceItemV1:
    return ExecutiveSourceItemV1(
        alias=alias,
        readiness=ExecutiveSourceReadinessV1.EXACT_CURRENT,
        reference_id=f"caller:{alias.value}",
        reference_fingerprint=None,
        summary=summary,
        as_of=as_of,
    )


def _aggregate_readiness(
    sources: tuple[ExecutiveSourceItemV1, ...],
) -> ExecutivePackReadinessV1:
    states = {cast(ExecutiveSourceReadinessV1, item.readiness) for item in sources}
    if ExecutiveSourceReadinessV1.POLICY_MISMATCH in states:
        return ExecutivePackReadinessV1.CONFLICT
    if (
        ExecutiveSourceReadinessV1.CONFLICT in states
        or ExecutiveSourceReadinessV1.NOT_COMPARABLE in states
    ):
        return ExecutivePackReadinessV1.CONFLICT
    if ExecutiveSourceReadinessV1.MISSING in states or ExecutiveSourceReadinessV1.STALE in states:
        return ExecutivePackReadinessV1.INCOMPLETE
    return ExecutivePackReadinessV1.EXACT_CURRENT


def build_executive_context_pack(
    goal: GrowthGoalIdentityV1,
    *,
    goal_text: str,
    task: str,
    constraints: tuple[str, ...] = (),
    current_context: str = "",
    sources: Mapping[ExecutiveSourceAliasV1 | str, ExecutiveSourceItemV1]
    | Sequence[ExecutiveSourceItemV1]
    | None = None,
    pack_caveats: tuple[str, ...] = (),
    as_of: datetime | None = None,
    expected_goal_identity_fingerprint: ExecutiveHashV1 | None = None,
) -> ExecutiveContextPackV1:
    """Build one deterministic pack from explicit, already-read projections."""

    if type(goal) is not GrowthGoalIdentityV1:
        raise _invalid()
    goal_fp = goal_identity_fingerprint(goal)
    if (
        expected_goal_identity_fingerprint is not None
        and _hash(expected_goal_identity_fingerprint, growth=True) != goal_fp
    ):
        raise ExecutiveContextError("selected Goal source changed")
    pack_as_of = datetime.now(UTC) if as_of is None else _timestamp(as_of)
    provided = _normalize_source_mapping(sources)
    if any(alias in provided for alias in (ExecutiveSourceAliasV1.GOAL_CURRENT,)):
        supplied_goal = provided[ExecutiveSourceAliasV1.GOAL_CURRENT]
        if (
            supplied_goal.readiness is not ExecutiveSourceReadinessV1.EXACT_CURRENT
            or supplied_goal.reference_id != str(goal.source_note_uuid)
            or supplied_goal.reference_fingerprint != goal_fp
        ):
            raise ExecutiveContextError("selected Goal source changed")

    normalized_goal_text = _text(goal_text, limit=MAX_GOAL_TEXT_BYTES)
    normalized_task = _text(task, limit=MAX_TASK_BYTES)
    normalized_constraints = _tuple_texts(
        constraints,
        maximum=MAX_CONSTRAINTS,
        item_limit=MAX_CONSTRAINT_BYTES,
        total_limit=MAX_CONSTRAINTS_BYTES,
    )
    normalized_context = _text(current_context, limit=MAX_CONTEXT_BYTES, allow_empty=True)
    caller_items = {
        ExecutiveSourceAliasV1.GOAL_CURRENT: ExecutiveSourceItemV1(
            alias=ExecutiveSourceAliasV1.GOAL_CURRENT,
            readiness=ExecutiveSourceReadinessV1.EXACT_CURRENT,
            reference_id=str(goal.source_note_uuid),
            reference_fingerprint=goal_fp,
            summary=normalized_goal_text,
            policy_fingerprints=(GROWTH_POLICY_FINGERPRINT,),
            as_of=pack_as_of,
        ),
        ExecutiveSourceAliasV1.CALLER_TASK: _caller_source(
            ExecutiveSourceAliasV1.CALLER_TASK,
            normalized_task,
            as_of=pack_as_of,
        ),
        ExecutiveSourceAliasV1.CALLER_CONSTRAINTS: _caller_source(
            ExecutiveSourceAliasV1.CALLER_CONSTRAINTS,
            "\n".join(normalized_constraints) if normalized_constraints else "(none)",
            as_of=pack_as_of,
        ),
        ExecutiveSourceAliasV1.CALLER_CONTEXT: _caller_source(
            ExecutiveSourceAliasV1.CALLER_CONTEXT,
            normalized_context or "(none)",
            as_of=pack_as_of,
        ),
    }
    items: list[ExecutiveSourceItemV1] = []
    for alias in SOURCE_ALIASES:
        if alias in caller_items:
            items.append(caller_items[alias])
            continue
        provided_item = provided.get(alias)
        if provided_item is not None:
            items.append(provided_item)
        else:
            items.append(
                ExecutiveSourceItemV1(
                    alias=alias,
                    readiness=ExecutiveSourceReadinessV1.MISSING,
                    reference_id=None,
                    reference_fingerprint=None,
                    summary="source missing",
                    as_of=pack_as_of,
                )
            )
    normalized_items = tuple(items)
    readiness = _aggregate_readiness(normalized_items)
    normalized_caveats = _tuple_texts(
        pack_caveats,
        maximum=MAX_PACK_CAVEATS,
        item_limit=MAX_CAVEAT_BYTES,
    )
    return _new_pack(
        as_of=pack_as_of,
        goal_source_uuid=_uuid7(goal.source_note_uuid),
        goal_identity_fingerprint=goal_fp,
        goal_text=normalized_goal_text,
        task=normalized_task,
        constraints=normalized_constraints,
        current_context=normalized_context,
        sources=normalized_items,
        readiness=readiness,
        pack_caveats=normalized_caveats,
    )


def _new_pack(
    *,
    as_of: datetime,
    goal_source_uuid: UUID,
    goal_identity_fingerprint: ExecutiveHashV1,
    goal_text: str,
    task: str,
    constraints: tuple[str, ...],
    current_context: str,
    sources: tuple[ExecutiveSourceItemV1, ...],
    readiness: ExecutivePackReadinessV1,
    pack_caveats: tuple[str, ...],
) -> ExecutiveContextPackV1:
    core = {
        "contract_version": CONTRACT_VERSION,
        "pack_version": PACK_VERSION,
        "as_of": _format_timestamp(as_of),
        "goal_source_uuid": str(goal_source_uuid),
        "goal_identity_fingerprint": goal_identity_fingerprint,
        "goal_text": goal_text,
        "task": task,
        "constraints": list(constraints),
        "current_context": current_context,
        "sources": [item.as_dict() for item in sources],
        "readiness": readiness.value,
        "pack_caveats": list(pack_caveats),
        "policy_id": POLICY_ID,
        "policy_fingerprint": POLICY_FINGERPRINT,
    }
    fingerprint = _raw_hash(core)
    return ExecutiveContextPackV1(
        contract_version=CONTRACT_VERSION,
        pack_version=PACK_VERSION,
        as_of=as_of,
        goal_source_uuid=goal_source_uuid,
        goal_identity_fingerprint=goal_identity_fingerprint,
        goal_text=goal_text,
        task=task,
        constraints=constraints,
        current_context=current_context,
        sources=sources,
        readiness=readiness,
        pack_caveats=pack_caveats,
        policy_id=POLICY_ID,
        policy_fingerprint=POLICY_FINGERPRINT,
        source_pack_fingerprint=fingerprint,
    )


def validate_executive_context_pack(value: object) -> ExecutiveContextPackV1:
    """Validate an already materialized immutable pack."""

    if type(value) is not ExecutiveContextPackV1:
        raise _invalid()
    expected = _raw_hash(_pack_core(value))
    if expected != value.source_pack_fingerprint:
        raise _invalid()
    return value


def serialize_executive_context_pack(value: object) -> bytes:
    """Serialize a validated pack using the single Stage 16 canonical JSON."""

    return _canonical_bytes(validate_executive_context_pack(value).as_dict())


canonical_executive_context_pack_bytes = serialize_executive_context_pack


def _action_id(value: object) -> str:
    if type(value) is not str or _ACTION_ID_PATTERN.fullmatch(value) is None:
        raise _invalid()
    if len(value.encode("ascii")) > MAX_ACTION_ID_BYTES:
        raise _invalid()
    return value


def _candidate_text(value: object, *, limit: int) -> str:
    normalized = _text(value, limit=limit)
    if _PROHIBITED_CANDIDATE_PATTERN.search(normalized) is not None:
        raise _invalid()
    return normalized


def _candidate_texts(value: object) -> tuple[str, ...]:
    if type(value) is not tuple or len(value) > MAX_ACTION_LISTS:
        raise _invalid()
    normalized = tuple(_candidate_text(item, limit=MAX_CONSTRAINT_BYTES) for item in value)
    if len(set(normalized)) != len(normalized):
        raise _invalid()
    return normalized


def _candidate_aliases(value: object) -> tuple[ExecutiveSourceAliasV1, ...]:
    if type(value) is not tuple or not 1 <= len(value) <= MAX_ACTION_LISTS:
        raise _invalid()
    aliases = tuple(_enum_value(item, ExecutiveSourceAliasV1) for item in value)
    if len(set(aliases)) != len(aliases):
        raise _invalid()
    return aliases


def _wire_list(value: object) -> tuple[object, ...]:
    """Accept only JSON arrays when reconstructing a typed DTO."""

    if type(value) is not list:
        raise _invalid()
    return tuple(cast(list[object], value))


@dataclass(frozen=True, slots=True)
class ExecutiveActionCandidateV1:
    """One bounded, non-executable action candidate."""

    action_id: str
    kind: ExecutiveActionKindV1 | str
    title: str
    description: str
    basis_aliases: tuple[ExecutiveSourceAliasV1 | str, ...]
    goal_relation: str
    expected_observable_signal: str
    prerequisites: tuple[str, ...] = ()
    caveats: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        action_id = _action_id(self.action_id)
        kind = _enum_value(self.kind, ExecutiveActionKindV1)
        title = _candidate_text(self.title, limit=MAX_ACTION_TITLE_BYTES)
        description = _candidate_text(self.description, limit=MAX_ACTION_DESCRIPTION_BYTES)
        aliases = _candidate_aliases(self.basis_aliases)
        goal_relation = _candidate_text(self.goal_relation, limit=MAX_ACTION_GOAL_RELATION_BYTES)
        signal = _candidate_text(self.expected_observable_signal, limit=MAX_ACTION_SIGNAL_BYTES)
        prerequisites = _candidate_texts(self.prerequisites)
        caveats = _candidate_texts(self.caveats)
        object.__setattr__(self, "action_id", action_id)
        object.__setattr__(self, "kind", kind)
        object.__setattr__(self, "title", title)
        object.__setattr__(self, "description", description)
        object.__setattr__(self, "basis_aliases", aliases)
        object.__setattr__(self, "goal_relation", goal_relation)
        object.__setattr__(self, "expected_observable_signal", signal)
        object.__setattr__(self, "prerequisites", prerequisites)
        object.__setattr__(self, "caveats", caveats)

    def as_dict(self) -> dict[str, object]:
        """Return the exact candidate wire projection."""

        return {
            "action_id": self.action_id,
            "kind": cast(ExecutiveActionKindV1, self.kind).value,
            "title": self.title,
            "description": self.description,
            "basis_aliases": [
                cast(ExecutiveSourceAliasV1, alias).value for alias in self.basis_aliases
            ],
            "goal_relation": self.goal_relation,
            "expected_observable_signal": self.expected_observable_signal,
            "prerequisites": list(self.prerequisites),
            "caveats": list(self.caveats),
        }

    @classmethod
    def from_dict(cls, value: object) -> ExecutiveActionCandidateV1:
        """Rebuild a candidate from its closed canonical JSON projection."""

        if type(value) is not dict:
            raise _invalid()
        expected = {
            "action_id",
            "kind",
            "title",
            "description",
            "basis_aliases",
            "goal_relation",
            "expected_observable_signal",
            "prerequisites",
            "caveats",
        }
        if set(value) != expected:
            raise _invalid()
        return cls(
            action_id=value["action_id"],
            kind=value["kind"],
            title=value["title"],
            description=value["description"],
            basis_aliases=cast(
                tuple[ExecutiveSourceAliasV1 | str, ...], _wire_list(value["basis_aliases"])
            ),
            goal_relation=value["goal_relation"],
            expected_observable_signal=value["expected_observable_signal"],
            prerequisites=cast(tuple[str, ...], _wire_list(value["prerequisites"])),
            caveats=cast(tuple[str, ...], _wire_list(value["caveats"])),
        )


ActionCandidateV1 = ExecutiveActionCandidateV1


@dataclass(frozen=True, slots=True)
class ReviewedActionV1:
    """One owner-reviewed action preserving both generated and reviewed text."""

    action_id: str
    kind: ExecutiveActionKindV1 | str
    generated: ExecutiveActionCandidateV1
    reviewed: ExecutiveActionCandidateV1
    edited: bool

    def __post_init__(self) -> None:
        action_id = _action_id(self.action_id)
        kind = _enum_value(self.kind, ExecutiveActionKindV1)
        if (
            type(self.generated) is not ExecutiveActionCandidateV1
            or type(self.reviewed) is not ExecutiveActionCandidateV1
            or self.generated.action_id != action_id
            or self.reviewed.action_id != action_id
            or self.generated.kind is not kind
            or self.reviewed.kind is not kind
            or self.generated.basis_aliases != self.reviewed.basis_aliases
            or type(self.edited) is not bool
            or self.edited != (self.generated != self.reviewed)
        ):
            raise _invalid()
        object.__setattr__(self, "action_id", action_id)
        object.__setattr__(self, "kind", kind)

    @property
    def generated_candidate(self) -> ExecutiveActionCandidateV1:
        """Compatibility name for callers that prefer an explicit noun."""

        return self.generated

    @property
    def reviewed_candidate(self) -> ExecutiveActionCandidateV1:
        """Compatibility name for callers that prefer an explicit noun."""

        return self.reviewed

    def as_dict(self) -> dict[str, object]:
        return {
            "action_id": self.action_id,
            "kind": cast(ExecutiveActionKindV1, self.kind).value,
            "generated": self.generated.as_dict(),
            "reviewed": self.reviewed.as_dict(),
            "edited": self.edited,
        }

    @classmethod
    def from_dict(cls, value: object) -> ReviewedActionV1:
        if type(value) is not dict:
            raise _invalid()
        expected = {"action_id", "kind", "generated", "reviewed", "edited"}
        if set(value) != expected:
            raise _invalid()
        return cls(
            action_id=value["action_id"],
            kind=value["kind"],
            generated=ExecutiveActionCandidateV1.from_dict(value["generated"]),
            reviewed=ExecutiveActionCandidateV1.from_dict(value["reviewed"]),
            edited=value["edited"],
        )


ReviewedAction = ReviewedActionV1


def build_reviewed_action(
    candidate: ExecutiveActionCandidateV1,
    *,
    reviewed: ExecutiveActionCandidateV1 | None = None,
) -> ReviewedActionV1:
    """Bind an optional bounded owner edit to one exact generated candidate."""

    if type(candidate) is not ExecutiveActionCandidateV1:
        raise _invalid()
    reviewed_candidate = candidate if reviewed is None else reviewed
    if type(reviewed_candidate) is not ExecutiveActionCandidateV1:
        raise _invalid()
    return ReviewedActionV1(
        action_id=candidate.action_id,
        kind=candidate.kind,
        generated=candidate,
        reviewed=reviewed_candidate,
        edited=candidate != reviewed_candidate,
    )


class StrategySnapshotStateV1(StrEnum):
    """Closed lifecycle state for one accepted strategy version."""

    CURRENT = "current"
    SUPERSEDED = "superseded"
    DEACTIVATED = "deactivated"


StrategySnapshotState = StrategySnapshotStateV1


def _snapshot_core(snapshot: StrategySnapshotV1) -> dict[str, object]:
    """Build snapshot identity bytes without the self-referential fingerprint."""

    return {
        "snapshot_id": str(snapshot.snapshot_id),
        "sequence": snapshot.sequence,
        "state": cast(StrategySnapshotStateV1, snapshot.state).value,
        "goal_source_uuid": str(snapshot.goal_source_uuid),
        "goal_identity_fingerprint": snapshot.goal_identity_fingerprint,
        "source_pack_fingerprint": snapshot.source_pack_fingerprint,
        "proposal_fingerprint": snapshot.proposal_fingerprint,
        "selected_actions": [action.as_dict() for action in snapshot.selected_actions],
        "policy_id": snapshot.policy_id,
        "policy_fingerprint": snapshot.policy_fingerprint,
        "reviewed_at": _format_timestamp(snapshot.reviewed_at),
        "accepted_at": _format_timestamp(snapshot.accepted_at),
        "prior_snapshot_id": (
            str(snapshot.prior_snapshot_id) if snapshot.prior_snapshot_id is not None else None
        ),
        "prior_snapshot_fingerprint": snapshot.prior_snapshot_fingerprint,
    }


@dataclass(frozen=True, slots=True)
class StrategySnapshotV1:
    """Immutable, owner-accepted operational strategy state."""

    snapshot_id: UUID | str
    sequence: int
    state: StrategySnapshotStateV1 | str
    goal_source_uuid: UUID | str
    goal_identity_fingerprint: ExecutiveHashV1
    source_pack_fingerprint: ExecutiveHashV1
    proposal_fingerprint: ExecutiveHashV1
    selected_actions: tuple[ReviewedActionV1, ...]
    policy_id: str
    policy_fingerprint: ExecutiveHashV1
    reviewed_at: datetime
    accepted_at: datetime
    prior_snapshot_id: UUID | str | None
    prior_snapshot_fingerprint: ExecutiveHashV1 | None
    snapshot_fingerprint: ExecutiveHashV1

    def __post_init__(self) -> None:
        snapshot_id = _uuid7(self.snapshot_id)
        if (
            type(self.sequence) is not int
            or isinstance(self.sequence, bool)
            or not 1 <= self.sequence <= (1 << 64) - 1
        ):
            raise _invalid()
        state = _enum_value(self.state, StrategySnapshotStateV1)
        goal_uuid = _uuid7(self.goal_source_uuid)
        goal_fp = _hash(self.goal_identity_fingerprint, growth=True)
        source_fp = _raw_digest(self.source_pack_fingerprint)
        proposal_fp = _raw_digest(self.proposal_fingerprint)
        if self.policy_id != POLICY_ID or self.policy_fingerprint != POLICY_FINGERPRINT:
            raise _policy_mismatch()
        if type(self.selected_actions) is not tuple:
            raise _invalid()
        if len(self.selected_actions) > MAX_SNAPSHOT_SELECTED_ACTIONS:
            raise _invalid()
        if any(type(action) is not ReviewedActionV1 for action in self.selected_actions):
            raise _invalid()
        actions = tuple(self.selected_actions)
        if len({action.action_id for action in actions}) != len(actions):
            raise _invalid()
        reviewed_at = _timestamp(self.reviewed_at)
        accepted_at = _timestamp(self.accepted_at)
        if reviewed_at > accepted_at:
            raise _invalid()
        prior_id = None if self.prior_snapshot_id is None else _uuid7(self.prior_snapshot_id)
        prior_fp = (
            None
            if self.prior_snapshot_fingerprint is None
            else _raw_digest(self.prior_snapshot_fingerprint)
        )
        if (prior_id is None) != (prior_fp is None):
            raise _invalid()
        if prior_id == snapshot_id:
            raise _invalid()
        supplied_fp = _raw_digest(self.snapshot_fingerprint)
        object.__setattr__(self, "snapshot_id", snapshot_id)
        object.__setattr__(self, "sequence", self.sequence)
        object.__setattr__(self, "state", state)
        object.__setattr__(self, "goal_source_uuid", goal_uuid)
        object.__setattr__(self, "goal_identity_fingerprint", goal_fp)
        object.__setattr__(self, "source_pack_fingerprint", source_fp)
        object.__setattr__(self, "proposal_fingerprint", proposal_fp)
        object.__setattr__(self, "selected_actions", actions)
        object.__setattr__(self, "policy_id", POLICY_ID)
        object.__setattr__(self, "policy_fingerprint", POLICY_FINGERPRINT)
        object.__setattr__(self, "reviewed_at", reviewed_at)
        object.__setattr__(self, "accepted_at", accepted_at)
        object.__setattr__(self, "prior_snapshot_id", prior_id)
        object.__setattr__(self, "prior_snapshot_fingerprint", prior_fp)
        expected = _raw_hash(_snapshot_core(self))
        if supplied_fp != expected:
            raise _invalid()
        object.__setattr__(self, "snapshot_fingerprint", expected)
        if len(_canonical_bytes(self.as_dict())) > MAX_SNAPSHOT_BYTES:
            raise _invalid()

    def as_dict(self) -> dict[str, object]:
        return {**_snapshot_core(self), "snapshot_fingerprint": self.snapshot_fingerprint}

    def to_json(self) -> str:
        return _canonical_bytes(self.as_dict()).decode("utf-8")

    @classmethod
    def from_dict(cls, value: object) -> StrategySnapshotV1:
        if type(value) is not dict:
            raise _invalid()
        expected = {
            "snapshot_id",
            "sequence",
            "state",
            "goal_source_uuid",
            "goal_identity_fingerprint",
            "source_pack_fingerprint",
            "proposal_fingerprint",
            "selected_actions",
            "policy_id",
            "policy_fingerprint",
            "reviewed_at",
            "accepted_at",
            "prior_snapshot_id",
            "prior_snapshot_fingerprint",
            "snapshot_fingerprint",
        }
        if set(value) != expected:
            raise _invalid()
        selected = _wire_list(value["selected_actions"])
        return cls(
            snapshot_id=value["snapshot_id"],
            sequence=value["sequence"],
            state=value["state"],
            goal_source_uuid=value["goal_source_uuid"],
            goal_identity_fingerprint=value["goal_identity_fingerprint"],
            source_pack_fingerprint=value["source_pack_fingerprint"],
            proposal_fingerprint=value["proposal_fingerprint"],
            selected_actions=tuple(ReviewedActionV1.from_dict(item) for item in selected),
            policy_id=value["policy_id"],
            policy_fingerprint=value["policy_fingerprint"],
            reviewed_at=value["reviewed_at"],
            accepted_at=value["accepted_at"],
            prior_snapshot_id=value["prior_snapshot_id"],
            prior_snapshot_fingerprint=value["prior_snapshot_fingerprint"],
            snapshot_fingerprint=value["snapshot_fingerprint"],
        )


StrategySnapshot = StrategySnapshotV1


def _new_snapshot(
    *,
    snapshot_id: UUID,
    sequence: int,
    state: StrategySnapshotStateV1,
    proposal: StrategyProposalV1,
    selected_actions: tuple[ReviewedActionV1, ...],
    reviewed_at: datetime,
    accepted_at: datetime,
    prior_snapshot_id: UUID | None,
    prior_snapshot_fingerprint: ExecutiveHashV1 | None,
) -> StrategySnapshotV1:
    core = {
        "snapshot_id": str(snapshot_id),
        "sequence": sequence,
        "state": state.value,
        "goal_source_uuid": str(proposal.goal_source_uuid),
        "goal_identity_fingerprint": proposal.goal_identity_fingerprint,
        "source_pack_fingerprint": proposal.source_pack_fingerprint,
        "proposal_fingerprint": proposal.proposal_fingerprint,
        "selected_actions": [action.as_dict() for action in selected_actions],
        "policy_id": POLICY_ID,
        "policy_fingerprint": POLICY_FINGERPRINT,
        "reviewed_at": _format_timestamp(reviewed_at),
        "accepted_at": _format_timestamp(accepted_at),
        "prior_snapshot_id": str(prior_snapshot_id) if prior_snapshot_id is not None else None,
        "prior_snapshot_fingerprint": prior_snapshot_fingerprint,
    }
    return StrategySnapshotV1(
        snapshot_id=snapshot_id,
        sequence=sequence,
        state=state,
        goal_source_uuid=proposal.goal_source_uuid,
        goal_identity_fingerprint=proposal.goal_identity_fingerprint,
        source_pack_fingerprint=proposal.source_pack_fingerprint,
        proposal_fingerprint=proposal.proposal_fingerprint,
        selected_actions=selected_actions,
        policy_id=POLICY_ID,
        policy_fingerprint=POLICY_FINGERPRINT,
        reviewed_at=reviewed_at,
        accepted_at=accepted_at,
        prior_snapshot_id=prior_snapshot_id,
        prior_snapshot_fingerprint=prior_snapshot_fingerprint,
        snapshot_fingerprint=_raw_hash(core),
    )


def build_strategy_snapshot(
    proposal: StrategyProposalV1,
    selected_actions: tuple[ReviewedActionV1, ...],
    *,
    sequence: int,
    reviewed_at: datetime,
    accepted_at: datetime,
    prior_snapshot: StrategySnapshotV1 | None = None,
    snapshot_id: UUID | str | None = None,
) -> StrategySnapshotV1:
    """Build one accepted snapshot after exact proposal/action review checks."""

    validated = validate_strategy_proposal(proposal)
    if validated.result_state is not ExecutiveResultStateV1.PROPOSAL:
        raise _invalid()
    if type(selected_actions) is not tuple:
        raise _invalid()
    candidates = {candidate.action_id: candidate for candidate in validated.candidates}
    for action in selected_actions:
        if type(action) is not ReviewedActionV1 or action.action_id not in candidates:
            raise _invalid()
        if action.generated != candidates[action.action_id]:
            raise _invalid()
    if len({action.action_id for action in selected_actions}) != len(selected_actions):
        raise _invalid()
    if prior_snapshot is not None:
        validate_strategy_snapshot(prior_snapshot)
        if (
            prior_snapshot.state is not StrategySnapshotStateV1.CURRENT
            or prior_snapshot.goal_source_uuid != validated.goal_source_uuid
            or prior_snapshot.goal_identity_fingerprint != validated.goal_identity_fingerprint
            or prior_snapshot.policy_fingerprint != validated.policy_fingerprint
        ):
            raise _invalid()
    identifier = uuid7() if snapshot_id is None else _uuid7(snapshot_id)
    return _new_snapshot(
        snapshot_id=identifier,
        sequence=sequence,
        state=StrategySnapshotStateV1.CURRENT,
        proposal=validated,
        selected_actions=selected_actions,
        reviewed_at=reviewed_at,
        accepted_at=accepted_at,
        prior_snapshot_id=(
            None if prior_snapshot is None else cast(UUID, prior_snapshot.snapshot_id)
        ),
        prior_snapshot_fingerprint=(
            None if prior_snapshot is None else prior_snapshot.snapshot_fingerprint
        ),
    )


def validate_strategy_snapshot(value: object) -> StrategySnapshotV1:
    """Validate an immutable snapshot and its canonical fingerprint."""

    if type(value) is not StrategySnapshotV1:
        raise _invalid()
    expected = _raw_hash(_snapshot_core(value))
    if expected != value.snapshot_fingerprint:
        raise _invalid()
    return value


def serialize_strategy_snapshot(value: object) -> bytes:
    """Serialize one accepted snapshot as canonical UTF-8 JSON."""

    return _canonical_bytes(validate_strategy_snapshot(value).as_dict())


canonical_strategy_snapshot_bytes = serialize_strategy_snapshot


@dataclass(frozen=True, slots=True)
class StrategyReasoningEnvelopeV1:
    """Stage16 metadata around the exact Assistant v1 provider envelope."""

    source_pack_fingerprint: ExecutiveHashV1
    assistant_envelope: AssistantReasoningEnvelopeV1

    def __post_init__(self) -> None:
        _raw_digest(self.source_pack_fingerprint)
        if type(self.assistant_envelope) is not AssistantReasoningEnvelopeV1:
            raise _invalid()
        serialized = serialize_assistant_reasoning_envelope(self.assistant_envelope)
        if not serialized:
            raise _invalid()

    @property
    def canonical_bytes(self) -> bytes:
        """Return the same bytes used for preview and the AdvisorPort call."""

        return serialize_assistant_reasoning_envelope(self.assistant_envelope)

    def as_dict(self) -> dict[str, object]:
        """Return metadata plus the exact Assistant projection."""

        return {
            "source_pack_fingerprint": self.source_pack_fingerprint,
            "assistant_envelope": json.loads(self.canonical_bytes.decode("utf-8")),
        }


StrategyReasoningEnvelope = StrategyReasoningEnvelopeV1


def _source_context_kind(alias: ExecutiveSourceAliasV1) -> AssistantContextKind:
    if alias in {
        ExecutiveSourceAliasV1.GOAL_CURRENT,
        ExecutiveSourceAliasV1.CALLER_TASK,
        ExecutiveSourceAliasV1.CALLER_CONSTRAINTS,
        ExecutiveSourceAliasV1.CALLER_CONTEXT,
    }:
        return AssistantContextKind.FACT
    return AssistantContextKind.BACKGROUND


def build_strategy_reasoning_envelope(
    pack: ExecutiveContextPackV1,
) -> StrategyReasoningEnvelopeV1:
    """Project one pack to the existing Assistant canonical boundary."""

    validated = validate_executive_context_pack(pack)
    if len(validated.goal_text.encode("utf-8")) > 512:
        raise ExecutiveProviderUnavailableError(
            "approved Assistant boundary cannot carry the Goal projection"
        )
    try:
        contexts = tuple(
            AssistantExplicitContext(
                kind=_source_context_kind(cast(ExecutiveSourceAliasV1, item.alias)),
                text=_text(
                    f"{cast(ExecutiveSourceAliasV1, item.alias).value}: {item.summary}",
                    limit=1024,
                ),
            )
            for item in validated.sources
        )
        request = AssistantRequest(
            task=validated.task,
            options=(),
            explicit_constraints=validated.constraints,
            explicit_goals=(validated.goal_text,),
            explicit_context=contexts,
        )
        assistant = build_assistant_reasoning_envelope(request)
    except (AssistantError, ExecutiveContextError) as error:
        raise ExecutiveProviderUnavailableError(
            "approved Assistant boundary cannot carry the context pack"
        ) from error
    return StrategyReasoningEnvelopeV1(
        source_pack_fingerprint=validated.source_pack_fingerprint,
        assistant_envelope=assistant,
    )


def serialize_strategy_reasoning_envelope(value: object) -> bytes:
    """Serialize exactly the Assistant bytes exposed to the provider."""

    if type(value) is not StrategyReasoningEnvelopeV1:
        raise _invalid()
    return value.canonical_bytes


canonical_strategy_reasoning_bytes = serialize_strategy_reasoning_envelope


def _proposal_core(proposal: StrategyProposalV1) -> dict[str, object]:
    """Build the ordered proposal payload without its own fingerprint."""

    return {
        "proposal_id": str(proposal.proposal_id),
        "proposal_version": proposal.proposal_version,
        "result_state": cast(ExecutiveResultStateV1, proposal.result_state).value,
        "as_of": _format_timestamp(proposal.as_of),
        "goal_source_uuid": str(proposal.goal_source_uuid),
        "goal_identity_fingerprint": proposal.goal_identity_fingerprint,
        "source_pack_fingerprint": proposal.source_pack_fingerprint,
        "policy_id": proposal.policy_id,
        "policy_fingerprint": proposal.policy_fingerprint,
        "candidates": [candidate.as_dict() for candidate in proposal.candidates],
        "suggested_order": list(proposal.suggested_order),
        "reasons": list(proposal.reasons),
        "caveats": list(proposal.caveats),
        "provider_fingerprint": proposal.provider_fingerprint,
    }


@dataclass(frozen=True, slots=True)
class StrategyProposalV1:
    """Bounded derived proposal; it is not owner intent or executable state."""

    proposal_id: UUID | str
    proposal_version: str
    result_state: ExecutiveResultStateV1 | str
    as_of: datetime
    goal_source_uuid: UUID | str
    goal_identity_fingerprint: ExecutiveHashV1
    source_pack_fingerprint: ExecutiveHashV1
    policy_id: str
    policy_fingerprint: ExecutiveHashV1
    candidates: tuple[ExecutiveActionCandidateV1, ...]
    suggested_order: tuple[str, ...]
    reasons: tuple[str, ...]
    caveats: tuple[str, ...]
    provider_fingerprint: ExecutiveHashV1
    proposal_fingerprint: ExecutiveHashV1

    def __post_init__(self) -> None:
        proposal_id = _uuid7(self.proposal_id)
        proposal_version = _text(self.proposal_version, limit=16)
        if proposal_version != "1":
            raise _policy_mismatch()
        result_state = _enum_value(self.result_state, ExecutiveResultStateV1)
        as_of = _timestamp(self.as_of)
        goal_uuid = _uuid7(self.goal_source_uuid)
        goal_fp = _hash(self.goal_identity_fingerprint, growth=True)
        source_fp = _raw_digest(self.source_pack_fingerprint)
        if self.policy_id != POLICY_ID or self.policy_fingerprint != POLICY_FINGERPRINT:
            raise _policy_mismatch()
        if type(self.candidates) is not tuple or len(self.candidates) > MAX_ACTION_LISTS:
            raise _invalid()
        if any(type(candidate) is not ExecutiveActionCandidateV1 for candidate in self.candidates):
            raise _invalid()
        candidates = tuple(self.candidates)
        candidate_ids = tuple(candidate.action_id for candidate in candidates)
        if len(set(candidate_ids)) != len(candidate_ids):
            raise _invalid()
        if type(self.suggested_order) is not tuple or len(self.suggested_order) > MAX_ACTION_LISTS:
            raise _invalid()
        suggested_order = tuple(_action_id(item) for item in self.suggested_order)
        if len(set(suggested_order)) != len(suggested_order):
            raise _invalid()
        if any(item not in candidate_ids for item in suggested_order):
            raise _invalid()
        if result_state is ExecutiveResultStateV1.PROPOSAL:
            if not candidates or set(suggested_order) != set(candidate_ids):
                raise _invalid()
        elif candidates or suggested_order:
            raise _invalid()
        if type(self.reasons) is not tuple or not 1 <= len(self.reasons) <= MAX_PROPOSAL_REASONS:
            raise _invalid()
        reasons = tuple(_text(item, limit=MAX_CAVEAT_BYTES) for item in self.reasons)
        if type(self.caveats) is not tuple or len(self.caveats) > MAX_PROPOSAL_CAVEATS:
            raise _invalid()
        caveats = tuple(_text(item, limit=MAX_CAVEAT_BYTES) for item in self.caveats)
        if len(set(reasons)) != len(reasons) or len(set(caveats)) != len(caveats):
            raise _invalid()
        provider_fp = _raw_digest(self.provider_fingerprint)
        proposal_fp = _raw_digest(self.proposal_fingerprint)
        object.__setattr__(self, "proposal_id", proposal_id)
        object.__setattr__(self, "proposal_version", proposal_version)
        object.__setattr__(self, "result_state", result_state)
        object.__setattr__(self, "as_of", as_of)
        object.__setattr__(self, "goal_source_uuid", goal_uuid)
        object.__setattr__(self, "goal_identity_fingerprint", goal_fp)
        object.__setattr__(self, "source_pack_fingerprint", source_fp)
        object.__setattr__(self, "policy_id", POLICY_ID)
        object.__setattr__(self, "policy_fingerprint", POLICY_FINGERPRINT)
        object.__setattr__(self, "candidates", candidates)
        object.__setattr__(self, "suggested_order", suggested_order)
        object.__setattr__(self, "reasons", reasons)
        object.__setattr__(self, "caveats", caveats)
        object.__setattr__(self, "provider_fingerprint", provider_fp)
        expected = _raw_hash(_proposal_core(self))
        if proposal_fp != expected:
            raise _invalid()
        object.__setattr__(self, "proposal_fingerprint", expected)
        if len(_canonical_bytes(self.as_dict())) > MAX_PROPOSAL_BYTES:
            raise _invalid()

    def as_dict(self) -> dict[str, object]:
        """Return the complete bounded proposal projection."""

        return {
            **_proposal_core(self),
            "proposal_fingerprint": self.proposal_fingerprint,
        }

    def to_json(self) -> str:
        """Return compact canonical proposal JSON."""

        return _canonical_bytes(self.as_dict()).decode("utf-8")

    @classmethod
    def from_dict(cls, value: object) -> StrategyProposalV1:
        """Rebuild a proposal only from its exact bounded JSON projection."""

        if type(value) is not dict:
            raise _invalid()
        expected = {
            "proposal_id",
            "proposal_version",
            "result_state",
            "as_of",
            "goal_source_uuid",
            "goal_identity_fingerprint",
            "source_pack_fingerprint",
            "policy_id",
            "policy_fingerprint",
            "candidates",
            "suggested_order",
            "reasons",
            "caveats",
            "provider_fingerprint",
            "proposal_fingerprint",
        }
        if set(value) != expected:
            raise _invalid()
        list_fields = ("candidates", "suggested_order", "reasons", "caveats")
        if any(type(value[field]) is not list for field in list_fields):
            raise _invalid()
        return cls(
            proposal_id=value["proposal_id"],
            proposal_version=value["proposal_version"],
            result_state=value["result_state"],
            as_of=value["as_of"],
            goal_source_uuid=value["goal_source_uuid"],
            goal_identity_fingerprint=value["goal_identity_fingerprint"],
            source_pack_fingerprint=value["source_pack_fingerprint"],
            policy_id=value["policy_id"],
            policy_fingerprint=value["policy_fingerprint"],
            candidates=tuple(
                ExecutiveActionCandidateV1.from_dict(item) for item in value["candidates"]
            ),
            suggested_order=tuple(cast(list[str], value["suggested_order"])),
            reasons=tuple(cast(list[str], value["reasons"])),
            caveats=tuple(cast(list[str], value["caveats"])),
            provider_fingerprint=value["provider_fingerprint"],
            proposal_fingerprint=value["proposal_fingerprint"],
        )


StrategyProposal = StrategyProposalV1


def _new_proposal(
    *,
    proposal_id: UUID,
    as_of: datetime,
    result_state: ExecutiveResultStateV1,
    goal_source_uuid: UUID,
    goal_identity_fingerprint: ExecutiveHashV1,
    source_pack_fingerprint: ExecutiveHashV1,
    candidates: tuple[ExecutiveActionCandidateV1, ...],
    suggested_order: tuple[str, ...],
    reasons: tuple[str, ...],
    caveats: tuple[str, ...],
    provider_fingerprint: ExecutiveHashV1,
) -> StrategyProposalV1:
    core = {
        "proposal_id": str(proposal_id),
        "proposal_version": "1",
        "result_state": result_state.value,
        "as_of": _format_timestamp(as_of),
        "goal_source_uuid": str(goal_source_uuid),
        "goal_identity_fingerprint": goal_identity_fingerprint,
        "source_pack_fingerprint": source_pack_fingerprint,
        "policy_id": POLICY_ID,
        "policy_fingerprint": POLICY_FINGERPRINT,
        "candidates": [candidate.as_dict() for candidate in candidates],
        "suggested_order": list(suggested_order),
        "reasons": list(reasons),
        "caveats": list(caveats),
        "provider_fingerprint": provider_fingerprint,
    }
    proposal_fingerprint = _raw_hash(core)
    return StrategyProposalV1(
        proposal_id=proposal_id,
        proposal_version="1",
        result_state=result_state,
        as_of=as_of,
        goal_source_uuid=goal_source_uuid,
        goal_identity_fingerprint=goal_identity_fingerprint,
        source_pack_fingerprint=source_pack_fingerprint,
        policy_id=POLICY_ID,
        policy_fingerprint=POLICY_FINGERPRINT,
        candidates=candidates,
        suggested_order=suggested_order,
        reasons=reasons,
        caveats=caveats,
        provider_fingerprint=provider_fingerprint,
        proposal_fingerprint=proposal_fingerprint,
    )


def _no_provider_fingerprint(reason: str) -> ExecutiveHashV1:
    return hashlib.sha256(f"stage16-provider-not-called-v1:{reason}".encode("ascii")).hexdigest()


def _assistant_abstention_state(code: AssistantAbstentionCode | str) -> ExecutiveResultStateV1:
    normalized = _enum_value(code, AssistantAbstentionCode)
    return {
        AssistantAbstentionCode.INSUFFICIENT_BASIS: ExecutiveResultStateV1.INSUFFICIENT_EVIDENCE,
        AssistantAbstentionCode.CONFLICTING_EXPLICIT_CONSTRAINTS: (
            ExecutiveResultStateV1.CONFLICTING_CONSTRAINTS
        ),
        AssistantAbstentionCode.AMBIGUOUS_OR_INCOMPARABLE_OPTIONS: (
            ExecutiveResultStateV1.NOT_COMPARABLE
        ),
        AssistantAbstentionCode.UNSUPPORTED_TASK: ExecutiveResultStateV1.INSUFFICIENT_CONTEXT,
    }[normalized]


def _assistant_basis_aliases(
    result: AssistantResultEnvelopeV1,
    pack: ExecutiveContextPackV1,
) -> tuple[ExecutiveSourceAliasV1, ...]:
    aliases: list[ExecutiveSourceAliasV1] = []
    for ref in result.evidence_refs:
        ordinal = ref.ordinal
        if not 1 <= ordinal <= len(pack.sources):
            raise _invalid()
        alias = _enum_value(pack.sources[ordinal - 1].alias, ExecutiveSourceAliasV1)
        if alias not in aliases:
            aliases.append(alias)
    if not aliases:
        aliases.append(ExecutiveSourceAliasV1.GOAL_CURRENT)
    if len(aliases) > MAX_ACTION_LISTS:
        raise _invalid()
    return tuple(aliases)


def build_strategy_proposal_from_assistant_result(
    pack: ExecutiveContextPackV1,
    result: AssistantResultEnvelopeV1,
    *,
    proposal_id: UUID | str | None = None,
    as_of: datetime | None = None,
) -> StrategyProposalV1:
    """Map one validated Assistant result to a bounded Stage16 proposal."""

    validated_pack = validate_executive_context_pack(pack)
    if type(result) is not AssistantResultEnvelopeV1:
        raise ExecutiveProviderResultInvalidError("provider result is malformed")
    try:
        provider_bytes = serialize_assistant_result_envelope(result)
        provider_fingerprint = hashlib.sha256(provider_bytes).hexdigest()
        kind = _enum_value(result.kind, AssistantResultKind)
        proposal_time = datetime.now(UTC) if as_of is None else _timestamp(as_of)
        identifier = uuid7() if proposal_id is None else _uuid7(proposal_id)
        if kind is AssistantResultKind.RECOMMENDATION:
            if result.recommendation is None:
                raise _invalid()
            rationale = tuple(_text(item, limit=MAX_CAVEAT_BYTES) for item in result.rationale)
            signal = rationale[1] if len(rationale) > 1 else rationale[0]
            candidate = ExecutiveActionCandidateV1(
                action_id="candidate-1",
                kind=ExecutiveActionKindV1.INVESTIGATE,
                title="Рекомендация для рассмотрения",
                description=_candidate_text(
                    result.recommendation, limit=MAX_ACTION_DESCRIPTION_BYTES
                ),
                basis_aliases=_assistant_basis_aliases(result, validated_pack),
                goal_relation=rationale[0],
                expected_observable_signal=signal,
                caveats=tuple(
                    _candidate_text(item, limit=MAX_CONSTRAINT_BYTES) for item in result.uncertainty
                ),
            )
            return _new_proposal(
                proposal_id=identifier,
                as_of=proposal_time,
                result_state=ExecutiveResultStateV1.PROPOSAL,
                goal_source_uuid=_uuid7(validated_pack.goal_source_uuid),
                goal_identity_fingerprint=validated_pack.goal_identity_fingerprint,
                source_pack_fingerprint=validated_pack.source_pack_fingerprint,
                candidates=(candidate,),
                suggested_order=(candidate.action_id,),
                reasons=rationale,
                caveats=tuple(_text(item, limit=MAX_CAVEAT_BYTES) for item in result.uncertainty),
                provider_fingerprint=provider_fingerprint,
            )
        if kind is AssistantResultKind.ABSTENTION:
            if result.abstention_code is None:
                raise _invalid()
            state = _assistant_abstention_state(result.abstention_code)
        else:
            state = ExecutiveResultStateV1.PROVIDER_ABSTAINED
        return _new_proposal(
            proposal_id=identifier,
            as_of=proposal_time,
            result_state=state,
            goal_source_uuid=_uuid7(validated_pack.goal_source_uuid),
            goal_identity_fingerprint=validated_pack.goal_identity_fingerprint,
            source_pack_fingerprint=validated_pack.source_pack_fingerprint,
            candidates=(),
            suggested_order=(),
            reasons=tuple(_text(item, limit=MAX_CAVEAT_BYTES) for item in result.rationale),
            caveats=tuple(_text(item, limit=MAX_CAVEAT_BYTES) for item in result.uncertainty),
            provider_fingerprint=provider_fingerprint,
        )
    except ExecutiveStrategyError:
        raise
    except AssistantError, ExecutiveContextError, TypeError, ValueError, UnicodeError:
        raise ExecutiveProviderResultInvalidError(
            "provider result failed Stage16 validation"
        ) from None


def _abstention_proposal(
    pack: ExecutiveContextPackV1,
    *,
    state: ExecutiveResultStateV1,
    reason: str,
    proposal_id: UUID | str | None,
    as_of: datetime | None,
) -> StrategyProposalV1:
    proposal_time = datetime.now(UTC) if as_of is None else _timestamp(as_of)
    identifier = uuid7() if proposal_id is None else _uuid7(proposal_id)
    return _new_proposal(
        proposal_id=identifier,
        as_of=proposal_time,
        result_state=state,
        goal_source_uuid=_uuid7(pack.goal_source_uuid),
        goal_identity_fingerprint=pack.goal_identity_fingerprint,
        source_pack_fingerprint=pack.source_pack_fingerprint,
        candidates=(),
        suggested_order=(),
        reasons=(_text(reason, limit=MAX_CAVEAT_BYTES),),
        caveats=(),
        provider_fingerprint=_no_provider_fingerprint(state.value),
    )


class BuildExecutiveStrategy:
    """Execute one explicit Stage16 generation through the existing AdvisorPort."""

    def __init__(self, advisor: AdvisorPort) -> None:
        self._advisor = advisor

    def execute(
        self,
        pack: ExecutiveContextPackV1,
        *,
        cancellation: CancellationToken,
        proposal_id: UUID | str | None = None,
        as_of: datetime | None = None,
    ) -> StrategyProposalV1:
        """Build a proposal; no call occurs until this explicit method is invoked."""

        validated_pack = validate_executive_context_pack(pack)
        if cancellation.is_cancelled():
            raise ExecutiveStrategyCancelledError("strategy generation was cancelled")
        if validated_pack.readiness is not ExecutivePackReadinessV1.EXACT_CURRENT:
            state = (
                ExecutiveResultStateV1.CONFLICTING_CONSTRAINTS
                if validated_pack.readiness is ExecutivePackReadinessV1.CONFLICT
                else ExecutiveResultStateV1.INSUFFICIENT_CONTEXT
            )
            return _abstention_proposal(
                validated_pack,
                state=state,
                reason="Недостаточно сопоставимого текущего контекста для стратегии.",
                proposal_id=proposal_id,
                as_of=as_of,
            )
        try:
            envelope = build_strategy_reasoning_envelope(validated_pack)
        except ExecutiveProviderUnavailableError:
            return _abstention_proposal(
                validated_pack,
                state=ExecutiveResultStateV1.PROVIDER_UNAVAILABLE,
                reason="Одобренная граница независимого совета не поддерживает этот контекст.",
                proposal_id=proposal_id,
                as_of=as_of,
            )
        if cancellation.is_cancelled():
            raise ExecutiveStrategyCancelledError("strategy generation was cancelled")
        request = AssistantRequest(
            task=envelope.assistant_envelope.task,
            options=envelope.assistant_envelope.options,
            explicit_constraints=envelope.assistant_envelope.explicit_constraints,
            explicit_goals=envelope.assistant_envelope.explicit_goals,
            explicit_context=envelope.assistant_envelope.explicit_context,
        )
        try:
            result = BuildAssistant(self._advisor).execute(request, cancellation=cancellation)
        except AssistantError as error:
            if error.code == "ASSISTANT_CANCELLED":
                raise ExecutiveStrategyCancelledError("strategy generation was cancelled") from None
            if error.code in {
                "ASSISTANT_PROVIDER_UNAVAILABLE",
                "ASSISTANT_TIMEOUT",
                "ASSISTANT_PROVIDER_FAILURE",
                "ASSISTANT_INVALID_REQUEST",
            }:
                return _abstention_proposal(
                    validated_pack,
                    state=ExecutiveResultStateV1.PROVIDER_UNAVAILABLE,
                    reason="Независимый совет сейчас недоступен.",
                    proposal_id=proposal_id,
                    as_of=as_of,
                )
            raise ExecutiveProviderResultInvalidError(
                "provider result failed Stage16 validation"
            ) from None
        if cancellation.is_cancelled():
            raise ExecutiveStrategyCancelledError("strategy generation was cancelled")
        return build_strategy_proposal_from_assistant_result(
            validated_pack,
            result,
            proposal_id=proposal_id,
            as_of=as_of,
        )


ExecutiveStrategyGateway = BuildExecutiveStrategy


def validate_strategy_proposal(value: object) -> StrategyProposalV1:
    """Validate a proposal and its canonical fingerprint without provider I/O."""

    if type(value) is not StrategyProposalV1:
        raise _invalid()
    expected = _raw_hash(_proposal_core(value))
    if expected != value.proposal_fingerprint:
        raise _invalid()
    return value


def serialize_strategy_proposal(value: object) -> bytes:
    """Serialize a validated proposal as canonical UTF-8 JSON."""

    return _canonical_bytes(validate_strategy_proposal(value).as_dict())


canonical_strategy_proposal_bytes = serialize_strategy_proposal


__all__ = [
    "CONTRACT_VERSION",
    "EXECUTIVE_CONTRACT_VERSION",
    "EXECUTIVE_PACK_VERSION",
    "EXECUTIVE_POLICY_CANONICAL_JSON",
    "EXECUTIVE_POLICY_FINGERPRINT",
    "EXECUTIVE_POLICY_ID",
    "EXECUTIVE_SOURCE_ALIASES",
    "PACK_VERSION",
    "POLICY_CANONICAL_JSON",
    "POLICY_FINGERPRINT",
    "POLICY_ID",
    "SOURCE_ALIASES",
    "ActionCandidateV1",
    "BuildExecutiveStrategy",
    "ExecutiveActionCandidateV1",
    "ExecutiveActionKind",
    "ExecutiveActionKindV1",
    "ExecutiveContextError",
    "ExecutiveContextInvalidError",
    "ExecutiveContextPack",
    "ExecutiveContextPackV1",
    "ExecutiveHashV1",
    "ExecutivePackReadiness",
    "ExecutivePackReadinessV1",
    "ExecutivePolicyMismatchError",
    "ExecutiveProviderResultInvalidError",
    "ExecutiveProviderUnavailableError",
    "ExecutiveResultState",
    "ExecutiveResultStateV1",
    "ExecutiveSourceAlias",
    "ExecutiveSourceAliasV1",
    "ExecutiveSourceItemV1",
    "ExecutiveSourceProjectionV1",
    "ExecutiveSourceReadiness",
    "ExecutiveSourceReadinessV1",
    "ExecutiveStrategyCancelledError",
    "ExecutiveStrategyError",
    "ExecutiveStrategyGateway",
    "ReviewedAction",
    "ReviewedActionV1",
    "StrategyProposal",
    "StrategyProposalV1",
    "StrategyReasoningEnvelope",
    "StrategyReasoningEnvelopeV1",
    "StrategySnapshot",
    "StrategySnapshotState",
    "StrategySnapshotStateV1",
    "StrategySnapshotV1",
    "build_executive_context_pack",
    "build_reviewed_action",
    "build_strategy_proposal_from_assistant_result",
    "build_strategy_reasoning_envelope",
    "build_strategy_snapshot",
    "canonical_executive_context_pack_bytes",
    "canonical_strategy_proposal_bytes",
    "canonical_strategy_reasoning_bytes",
    "canonical_strategy_snapshot_bytes",
    "compute_goal_identity_fingerprint",
    "goal_identity_fingerprint",
    "serialize_executive_context_pack",
    "serialize_strategy_proposal",
    "serialize_strategy_reasoning_envelope",
    "serialize_strategy_snapshot",
    "validate_executive_context_pack",
    "validate_strategy_proposal",
    "validate_strategy_snapshot",
]
