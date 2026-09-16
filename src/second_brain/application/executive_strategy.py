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
from uuid import UUID

from second_brain.application.growth import (
    GROWTH_POLICY_FINGERPRINT,
    GrowthGoalIdentityV1,
    growth_hash_json,
)
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

_RAW_HASH_PATTERN: Final[re.Pattern[str]] = re.compile(r"[0-9a-f]{64}\Z", re.ASCII)
_GROWTH_HASH_PATTERN: Final[re.Pattern[str]] = re.compile(r"sha256:[0-9a-f]{64}\Z", re.ASCII)
_REFERENCE_PATTERN: Final[re.Pattern[str]] = re.compile(r"[^\x00-\x1f\x7f-\x9f]+\Z")


class ExecutiveContextError(ValueError):
    """Base error for invalid Stage 16.1 values."""


class ExecutiveContextInvalidError(ExecutiveContextError):
    """A bounded context value failed the immutable v1 contract."""


class ExecutivePolicyMismatchError(ExecutiveContextError):
    """A context value belongs to another policy or contract revision."""


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
    "ExecutiveContextError",
    "ExecutiveContextInvalidError",
    "ExecutiveContextPack",
    "ExecutiveContextPackV1",
    "ExecutiveHashV1",
    "ExecutivePackReadiness",
    "ExecutivePackReadinessV1",
    "ExecutivePolicyMismatchError",
    "ExecutiveSourceAlias",
    "ExecutiveSourceAliasV1",
    "ExecutiveSourceItemV1",
    "ExecutiveSourceProjectionV1",
    "ExecutiveSourceReadiness",
    "ExecutiveSourceReadinessV1",
    "build_executive_context_pack",
    "canonical_executive_context_pack_bytes",
    "compute_goal_identity_fingerprint",
    "goal_identity_fingerprint",
    "serialize_executive_context_pack",
    "validate_executive_context_pack",
]
