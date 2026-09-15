"""Stage 14A canonical Personal Experiments records and pure projections.

This module is intentionally read-side only. It parses exact marker-enrolled
managed companion records, validates exact Goal/Stage 12 links and
append-only chains, and exposes deterministic immutable projections. It has no
writer, clock, network, provider, transport, or persistence capability.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections import defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Any, Final, cast
from uuid import UUID

from second_brain.application.goal_progress import (
    GOAL_PROGRESS_POLICY_FINGERPRINT,
    DefinitionRecordV1,
    GoalBindingValidationV1,
    GoalProgressBindingStateV1,
    ObservationRecordV1,
    validate_goal_binding,
)
from second_brain.domain.models import parse_rfc3339, parse_uuid7

PERSONAL_EXPERIMENT_MARKER: Final[str] = "second_brain_personal_experiment"
PERSONAL_EXPERIMENT_MARKER_VALUE: Final[int] = 1
PERSONAL_EXPERIMENT_POLICY_ID: Final[str] = "personal-experiment-v1"
PERSONAL_EXPERIMENT_DERIVATION_ID: Final[str] = "personal-experiment-derivation-v1"
PERSONAL_EXPERIMENT_POLICY_CANONICAL_JSON: Final[str] = (
    '{"baseline_strategies":["stage12_definition_explicit",'
    '"reviewed_pre_activation_observation"],'
    '"causality":"descriptive_non_causal_v1",'
    '"contract":"personal-experiment-v1",'
    '"evaluation_window":"activation_inclusive_terminal_exclusive_v1",'
    '"goal_binding":"growth-goal-identity-exact-v1",'
    '"lifecycle":"explicit_reviewed_activation_terminal_v1",'
    '"observation_enrollment":"explicit_reviewed_stage12_link_v1",'
    '"one_active_per_goal":"exact_goal_v1",'
    '"persistence":"reviewed_canonical_companion_records_v1",'
    '"progress_authority":"stage12-goal-progress-v1",'
    '"provider":"forbidden",'
    '"reassessment":"reviewed_terminal_only_v1",'
    '"version":1}'
)
PERSONAL_EXPERIMENT_POLICY_FINGERPRINT: Final[str] = (
    "sha256:84de7a118c2a17b21c0f002ccc1a542735593a26ca9dd4628808cc19b1438563"
)

MAX_PERSONAL_EXPERIMENT_TEXT_BYTES: Final[int] = 4 * 1024
MAX_PERSONAL_EXPERIMENT_RECORD_BYTES: Final[int] = 64 * 1024
MAX_PERSONAL_EXPERIMENT_OBSERVATIONS: Final[int] = 200
MAX_PERSONAL_EXPERIMENT_RECORDS: Final[int] = 200
_HASH_PATTERN: Final[re.Pattern[str]] = re.compile(r"sha256:[0-9a-f]{64}\Z", re.ASCII)
_POLICY_ID_PATTERN: Final[re.Pattern[str]] = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*\Z", re.ASCII)

_COMMON_FIELDS: Final[frozenset[str]] = frozenset(
    {"id", "type", "created", "updated", "tags", "links"}
)
_BASE_FIELDS: Final[frozenset[str]] = frozenset(
    {
        PERSONAL_EXPERIMENT_MARKER,
        "personal_experiment_kind",
        "experiment_policy_id",
        "experiment_policy_fingerprint",
        "experiment_definition_id",
        "experiment_definition_fingerprint",
        "goal_source_uuid",
        "goal_identity_fingerprint",
    }
)
_DEFINITION_FIELDS: Final[frozenset[str]] = (
    _COMMON_FIELDS
    | _BASE_FIELDS
    | frozenset(
        {
            "goal_progress_definition_id",
            "goal_progress_definition_fingerprint",
            "goal_progress_policy_fingerprint",
            "hypothesis",
            "intervention",
            "baseline_strategy",
            "baseline_observation_uuid",
            "baseline_observation_fingerprint",
            "definition_reviewed_at",
            "supersedes_definition_id",
            "supersedes_definition_fingerprint",
        }
    )
)
_LIFECYCLE_FIELDS: Final[frozenset[str]] = (
    _COMMON_FIELDS
    | _BASE_FIELDS
    | frozenset(
        {
            "lifecycle_event",
            "event_at",
            "lifecycle_reviewed_at",
            "supersedes_lifecycle_id",
            "supersedes_lifecycle_fingerprint",
        }
    )
)
_OBSERVATION_FIELDS: Final[frozenset[str]] = (
    _COMMON_FIELDS
    | _BASE_FIELDS
    | frozenset(
        {
            "goal_progress_definition_id",
            "goal_progress_definition_fingerprint",
            "goal_progress_policy_fingerprint",
            "stage12_observation_id",
            "stage12_observation_fingerprint",
            "observation_reviewed_at",
            "supersedes_observation_id",
            "supersedes_observation_fingerprint",
        }
    )
)
_REASSESSMENT_FIELDS: Final[frozenset[str]] = (
    _COMMON_FIELDS
    | _BASE_FIELDS
    | frozenset(
        {
            "result_fingerprint",
            "evaluation_as_of",
            "evaluation_policy_fingerprint",
            "disposition",
            "rationale",
            "reassessment_reviewed_at",
            "supersedes_reassessment_id",
            "supersedes_reassessment_fingerprint",
        }
    )
)


class PersonalExperimentRecordKindV1(StrEnum):
    """The four exact Stage 14 companion-record families."""

    DEFINITION = "definition"
    LIFECYCLE = "lifecycle"
    OBSERVATION = "observation"
    REASSESSMENT = "reassessment"


class PersonalExperimentBaselineStrategyV1(StrEnum):
    """The only explicit v1 baseline sources."""

    STAGE12_DEFINITION_EXPLICIT = "stage12_definition_explicit"
    REVIEWED_PRE_ACTIVATION_OBSERVATION = "reviewed_pre_activation_observation"


class PersonalExperimentLifecycleEventV1(StrEnum):
    """The reviewed lifecycle events allowed by v1."""

    ACTIVATION = "activation"
    COMPLETION = "completion"
    CANCELLATION = "cancellation"


class PersonalExperimentDispositionV1(StrEnum):
    """Non-adaptive owner disposition vocabulary."""

    CONTINUE = "continue"
    STOP = "stop"
    REPEAT_WITH_NEW_DEFINITION = "repeat_with_new_definition"
    HOLD = "hold"
    NOT_DECIDED = "not_decided"


class PersonalExperimentChainStateV1(StrEnum):
    """Deterministic active-leaf states for one exact chain."""

    ZERO_ACTIVE = "zero_active"
    ONE_ACTIVE = "one_active"
    MULTIPLE_ACTIVE = "multiple_active"
    CONFLICT = "conflict"


class PersonalExperimentSourceStateV1(StrEnum):
    """Exact source-link validation outcomes."""

    EXACT_CURRENT = "exact_current"
    SOURCE_MISSING = "source_missing"
    SOURCE_CHANGED = "source_changed"
    NON_CURRENT = "non_current"
    AMBIGUOUS = "ambiguous"
    POLICY_MISMATCH = "policy_mismatch"


class PersonalExperimentValidationError(ValueError):
    """Bounded validation error carrying only a safe machine-readable code."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


class PersonalExperimentRecordError(PersonalExperimentValidationError):
    """Invalid marker-enrolled record; no raw metadata is retained."""


PERSONAL_EXPERIMENT_DIAGNOSTIC_MESSAGES: Final[dict[str, str]] = {
    "PERSONAL_EXPERIMENT_INVALID_RECORD": "Запись личного эксперимента недействительна",
    "PERSONAL_EXPERIMENT_INVALID_KIND": "Тип записи личного эксперимента не поддерживается",
    "PERSONAL_EXPERIMENT_MISSING_FIELD": (
        "В записи личного эксперимента отсутствует обязательное поле"
    ),
    "PERSONAL_EXPERIMENT_INVALID_FIELD": ("Запись личного эксперимента содержит недопустимое поле"),
    "PERSONAL_EXPERIMENT_RECORD_TOO_LARGE": (
        "Размер записи личного эксперимента превышает допустимый предел"
    ),
    "PERSONAL_EXPERIMENT_POLICY_MISMATCH": (
        "Привязка записи к политике личного эксперимента недействительна"
    ),
    "PERSONAL_EXPERIMENT_DEFINITION_CHAIN_CONFLICT": (
        "Цепочка определений личного эксперимента содержит конфликт"
    ),
    "PERSONAL_EXPERIMENT_LIFECYCLE_CHAIN_CONFLICT": (
        "Цепочка жизненного цикла личного эксперимента содержит конфликт"
    ),
    "PERSONAL_EXPERIMENT_OBSERVATION_CHAIN_CONFLICT": (
        "Цепочка наблюдений личного эксперимента содержит конфликт"
    ),
    "PERSONAL_EXPERIMENT_REASSESSMENT_CHAIN_CONFLICT": (
        "Цепочка переоценок личного эксперимента содержит конфликт"
    ),
    "PERSONAL_EXPERIMENT_PREDECESSOR_MISSING": (
        "Предыдущая запись замены личного эксперимента не найдена"
    ),
    "PERSONAL_EXPERIMENT_SELF_REFERENCE": (
        "Замена личного эксперимента не может ссылаться на себя"
    ),
    "PERSONAL_EXPERIMENT_DUPLICATE_SUCCESSOR": (
        "У замены личного эксперимента обнаружены дублирующие продолжения"
    ),
    "PERSONAL_EXPERIMENT_CYCLE": "Цепочка замен личного эксперимента содержит цикл",
    "PERSONAL_EXPERIMENT_BINDING_MISMATCH": "Точная привязка личного эксперимента недействительна",
    "PERSONAL_EXPERIMENT_SOURCE_MISSING": "Исходная запись личного эксперимента не найдена",
    "PERSONAL_EXPERIMENT_SOURCE_CHANGED": "Исходная запись личного эксперимента изменилась",
    "PERSONAL_EXPERIMENT_DEFINITION_MISSING": (
        "Целевая запись определения личного эксперимента не найдена"
    ),
    "PERSONAL_EXPERIMENT_DEFINITION_AMBIGUOUS": (
        "Целевая запись определения личного эксперимента неоднозначна"
    ),
    "PERSONAL_EXPERIMENT_DEFINITION_CHANGED": (
        "Целевая запись определения личного эксперимента изменилась"
    ),
    "PERSONAL_EXPERIMENT_RECORD_LIMIT_EXCEEDED": (
        "Количество записей личных экспериментов превышает допустимый предел"
    ),
}


def canonical_personal_experiment_json(value: object) -> str:
    """Serialize bounded Stage 14 values with the repository canonical profile."""

    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except TypeError, ValueError, UnicodeError:
        raise ValueError("value is not canonical Personal Experiment JSON") from None


def personal_experiment_hash_json(value: object) -> str:
    """Hash canonical UTF-8 JSON in the repository sha256 form."""

    return (
        "sha256:"
        + hashlib.sha256(canonical_personal_experiment_json(value).encode("utf-8")).hexdigest()
    )


def validate_personal_experiment_policy() -> str:
    """Verify the fixed policy payload and return its exact fingerprint."""

    payload = json.loads(PERSONAL_EXPERIMENT_POLICY_CANONICAL_JSON)
    if (
        type(payload) is not dict
        or canonical_personal_experiment_json(payload) != PERSONAL_EXPERIMENT_POLICY_CANONICAL_JSON
        or personal_experiment_hash_json(payload) != PERSONAL_EXPERIMENT_POLICY_FINGERPRINT
    ):
        raise PersonalExperimentValidationError("PERSONAL_EXPERIMENT_POLICY_MISMATCH")
    return PERSONAL_EXPERIMENT_POLICY_FINGERPRINT


def is_personal_experiment_enrolled(front_matter: Mapping[str, object] | object) -> bool:
    """Return true only for the exact YAML scalar integer marker one."""

    if not isinstance(front_matter, Mapping):
        return False
    value = front_matter.get(PERSONAL_EXPERIMENT_MARKER)
    return type(value) is int and value == PERSONAL_EXPERIMENT_MARKER_VALUE


def _format_timestamp(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _parse_timestamp(value: object, field: str) -> datetime:
    try:
        parsed = parse_rfc3339(value)
    except TypeError, ValueError, OverflowError:
        raise PersonalExperimentRecordError("PERSONAL_EXPERIMENT_INVALID_FIELD") from None
    if parsed.utcoffset() != timedelta(0):
        raise PersonalExperimentRecordError("PERSONAL_EXPERIMENT_INVALID_FIELD")
    return parsed.astimezone(UTC)


def _parse_uuid(value: object) -> UUID:
    try:
        return parse_uuid7(value)
    except TypeError, ValueError, OverflowError:
        raise PersonalExperimentRecordError("PERSONAL_EXPERIMENT_INVALID_FIELD") from None


def _parse_hash(value: object, *, policy: bool = False) -> str:
    if type(value) is not str or _HASH_PATTERN.fullmatch(value) is None:
        raise PersonalExperimentRecordError("PERSONAL_EXPERIMENT_INVALID_FIELD")
    if policy and value != PERSONAL_EXPERIMENT_POLICY_FINGERPRINT:
        raise PersonalExperimentRecordError("PERSONAL_EXPERIMENT_POLICY_MISMATCH")
    return value


def _parse_policy_id(value: object) -> str:
    if (
        type(value) is not str
        or value != PERSONAL_EXPERIMENT_POLICY_ID
        or _POLICY_ID_PATTERN.fullmatch(value) is None
    ):
        raise PersonalExperimentRecordError("PERSONAL_EXPERIMENT_POLICY_MISMATCH")
    return value


def _parse_stage12_policy(value: object) -> str:
    if type(value) is not str or value != GOAL_PROGRESS_POLICY_FINGERPRINT:
        raise PersonalExperimentRecordError("PERSONAL_EXPERIMENT_POLICY_MISMATCH")
    return value


def _parse_text(value: object) -> str:
    if type(value) is not str or not value:
        raise PersonalExperimentRecordError("PERSONAL_EXPERIMENT_INVALID_FIELD")
    try:
        size = len(value.encode("utf-8"))
    except UnicodeError:
        raise PersonalExperimentRecordError("PERSONAL_EXPERIMENT_INVALID_FIELD") from None
    if size > MAX_PERSONAL_EXPERIMENT_TEXT_BYTES:
        raise PersonalExperimentRecordError("PERSONAL_EXPERIMENT_INVALID_FIELD")
    if any(ord(char) < 0x20 or ord(char) == 0x7F for char in value):
        raise PersonalExperimentRecordError("PERSONAL_EXPERIMENT_INVALID_FIELD")
    return value


def _parse_enum(value: object, enum_type: type[StrEnum]) -> StrEnum:
    if isinstance(value, enum_type):
        return value
    if type(value) is not str:
        raise PersonalExperimentRecordError("PERSONAL_EXPERIMENT_INVALID_FIELD")
    try:
        return enum_type(value)
    except ValueError:
        raise PersonalExperimentRecordError("PERSONAL_EXPERIMENT_INVALID_FIELD") from None


def _optional_uuid(data: Mapping[object, object], name: str) -> UUID | None:
    if name not in data or data[name] is None:
        return None
    return _parse_uuid(data[name])


def _optional_hash(data: Mapping[object, object], name: str) -> str | None:
    if name not in data or data[name] is None:
        return None
    return _parse_hash(data[name])


def _optional_pair(
    data: Mapping[object, object],
    id_name: str,
    fingerprint_name: str,
) -> tuple[UUID, str] | None:
    record_id = _optional_uuid(data, id_name)
    fingerprint = _optional_hash(data, fingerprint_name)
    if (record_id is None) != (fingerprint is None):
        raise PersonalExperimentRecordError("PERSONAL_EXPERIMENT_INVALID_FIELD")
    return None if record_id is None else (record_id, cast(str, fingerprint))


def _required(data: Mapping[object, object], name: str) -> object:
    if name not in data:
        raise PersonalExperimentRecordError("PERSONAL_EXPERIMENT_MISSING_FIELD")
    return data[name]


def _record_id(data: Mapping[object, object], note_id: UUID | str | None) -> UUID:
    parsed_note_id = _parse_uuid(note_id) if note_id is not None else None
    if "id" not in data:
        if parsed_note_id is None:
            raise PersonalExperimentRecordError("PERSONAL_EXPERIMENT_MISSING_FIELD")
        return parsed_note_id
    parsed_id = _parse_uuid(data["id"])
    if parsed_note_id is not None and parsed_id != parsed_note_id:
        raise PersonalExperimentRecordError("PERSONAL_EXPERIMENT_BINDING_MISMATCH")
    return parsed_id


def _reject_unknown_fields(data: Mapping[object, object], allowed: frozenset[str]) -> None:
    if any(type(key) is not str for key in data):
        raise PersonalExperimentRecordError("PERSONAL_EXPERIMENT_INVALID_FIELD")
    if set(cast(Mapping[str, object], data)) - allowed:
        raise PersonalExperimentRecordError("PERSONAL_EXPERIMENT_INVALID_FIELD")


@dataclass(frozen=True, slots=True)
class PersonalExperimentDefinitionRecordV1:
    """Immutable canonical definition record."""

    id: UUID | str
    experiment_policy_fingerprint: str
    goal_source_uuid: UUID | str
    goal_identity_fingerprint: str
    goal_progress_definition_id: UUID | str
    goal_progress_definition_fingerprint: str
    goal_progress_policy_fingerprint: str
    hypothesis: str
    intervention: str
    baseline_strategy: PersonalExperimentBaselineStrategyV1 | str
    definition_reviewed_at: datetime | str
    baseline_observation_uuid: UUID | str | None = None
    baseline_observation_fingerprint: str | None = None
    supersedes_definition_id: UUID | str | None = None
    supersedes_definition_fingerprint: str | None = None
    experiment_policy_id: str = PERSONAL_EXPERIMENT_POLICY_ID
    second_brain_personal_experiment: int = PERSONAL_EXPERIMENT_MARKER_VALUE
    personal_experiment_kind: PersonalExperimentRecordKindV1 | str = (
        PersonalExperimentRecordKindV1.DEFINITION
    )

    def __post_init__(self) -> None:
        object.__setattr__(self, "id", _parse_uuid(self.id))
        object.__setattr__(
            self, "experiment_policy_id", _parse_policy_id(self.experiment_policy_id)
        )
        object.__setattr__(
            self,
            "experiment_policy_fingerprint",
            _parse_hash(self.experiment_policy_fingerprint, policy=True),
        )
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
        object.__setattr__(
            self,
            "goal_progress_policy_fingerprint",
            _parse_stage12_policy(self.goal_progress_policy_fingerprint),
        )
        object.__setattr__(self, "hypothesis", _parse_text(self.hypothesis))
        object.__setattr__(self, "intervention", _parse_text(self.intervention))
        object.__setattr__(
            self,
            "baseline_strategy",
            _parse_enum(self.baseline_strategy, PersonalExperimentBaselineStrategyV1),
        )
        object.__setattr__(
            self,
            "definition_reviewed_at",
            _parse_timestamp(self.definition_reviewed_at, "definition_reviewed_at"),
        )
        baseline_id = _parse_optional_uuid_value(self.baseline_observation_uuid)
        baseline_hash = _parse_optional_hash_value(self.baseline_observation_fingerprint)
        if (baseline_id is None) != (baseline_hash is None):
            raise PersonalExperimentRecordError("PERSONAL_EXPERIMENT_INVALID_FIELD")
        strategy = cast(PersonalExperimentBaselineStrategyV1, self.baseline_strategy)
        if strategy is PersonalExperimentBaselineStrategyV1.REVIEWED_PRE_ACTIVATION_OBSERVATION:
            if baseline_id is None or baseline_hash is None:
                raise PersonalExperimentRecordError("PERSONAL_EXPERIMENT_INVALID_FIELD")
        elif baseline_id is not None or baseline_hash is not None:
            raise PersonalExperimentRecordError("PERSONAL_EXPERIMENT_INVALID_FIELD")
        object.__setattr__(self, "baseline_observation_uuid", baseline_id)
        object.__setattr__(self, "baseline_observation_fingerprint", baseline_hash)
        supersedes = _normalize_pair_values(
            self.supersedes_definition_id,
            self.supersedes_definition_fingerprint,
        )
        object.__setattr__(self, "supersedes_definition_id", supersedes[0])
        object.__setattr__(self, "supersedes_definition_fingerprint", supersedes[1])
        _validate_marker_kind(
            self.second_brain_personal_experiment,
            self.personal_experiment_kind,
            PersonalExperimentRecordKindV1.DEFINITION,
        )
        _validate_record_size(self)

    @property
    def experiment_definition_fingerprint(self) -> str:
        """Return the semantic identity fingerprint for this definition."""

        return personal_experiment_hash_json(self.fingerprint_payload())

    def fingerprint_payload(self) -> dict[str, object]:
        """Return semantic definition content without storage identity."""

        payload: dict[str, object] = {
            "baseline_strategy": cast(
                PersonalExperimentBaselineStrategyV1, self.baseline_strategy
            ).value,
            "contract": "personal-experiment-definition-v1",
            "experiment_policy_fingerprint": self.experiment_policy_fingerprint,
            "experiment_policy_id": self.experiment_policy_id,
            "goal_identity_fingerprint": self.goal_identity_fingerprint,
            "goal_progress_definition_fingerprint": self.goal_progress_definition_fingerprint,
            "goal_progress_definition_id": str(self.goal_progress_definition_id),
            "goal_progress_policy_fingerprint": self.goal_progress_policy_fingerprint,
            "goal_source_uuid": str(self.goal_source_uuid),
            "hypothesis": self.hypothesis,
            "intervention": self.intervention,
        }
        if self.baseline_observation_uuid is not None:
            payload["baseline_observation_fingerprint"] = self.baseline_observation_fingerprint
            payload["baseline_observation_uuid"] = str(self.baseline_observation_uuid)
        return payload

    def as_dict(self) -> dict[str, object]:
        """Return the deterministic semantic/read projection."""

        result: dict[str, object] = {
            "second_brain_personal_experiment": PERSONAL_EXPERIMENT_MARKER_VALUE,
            "personal_experiment_kind": PersonalExperimentRecordKindV1.DEFINITION.value,
            "experiment_policy_id": self.experiment_policy_id,
            "experiment_policy_fingerprint": self.experiment_policy_fingerprint,
            "id": str(self.id),
            "goal_source_uuid": str(self.goal_source_uuid),
            "goal_identity_fingerprint": self.goal_identity_fingerprint,
            "goal_progress_definition_id": str(self.goal_progress_definition_id),
            "goal_progress_definition_fingerprint": self.goal_progress_definition_fingerprint,
            "goal_progress_policy_fingerprint": self.goal_progress_policy_fingerprint,
            "hypothesis": self.hypothesis,
            "intervention": self.intervention,
            "baseline_strategy": cast(
                PersonalExperimentBaselineStrategyV1, self.baseline_strategy
            ).value,
            "definition_reviewed_at": _format_timestamp(
                cast(datetime, self.definition_reviewed_at)
            ),
        }
        if self.baseline_observation_uuid is not None:
            result["baseline_observation_uuid"] = str(self.baseline_observation_uuid)
            result["baseline_observation_fingerprint"] = self.baseline_observation_fingerprint
        if self.supersedes_definition_id is not None:
            result["supersedes_definition_id"] = str(self.supersedes_definition_id)
            result["supersedes_definition_fingerprint"] = self.supersedes_definition_fingerprint
        return result


@dataclass(frozen=True, slots=True)
class PersonalExperimentLifecycleRecordV1:
    """Immutable canonical lifecycle event record."""

    id: UUID | str
    experiment_definition_id: UUID | str
    experiment_definition_fingerprint: str
    goal_source_uuid: UUID | str
    goal_identity_fingerprint: str
    lifecycle_event: PersonalExperimentLifecycleEventV1 | str
    event_at: datetime | str
    lifecycle_reviewed_at: datetime | str
    supersedes_lifecycle_id: UUID | str | None = None
    supersedes_lifecycle_fingerprint: str | None = None
    experiment_policy_id: str = PERSONAL_EXPERIMENT_POLICY_ID
    experiment_policy_fingerprint: str = PERSONAL_EXPERIMENT_POLICY_FINGERPRINT
    second_brain_personal_experiment: int = PERSONAL_EXPERIMENT_MARKER_VALUE
    personal_experiment_kind: PersonalExperimentRecordKindV1 | str = (
        PersonalExperimentRecordKindV1.LIFECYCLE
    )

    def __post_init__(self) -> None:
        object.__setattr__(self, "id", _parse_uuid(self.id))
        object.__setattr__(
            self, "experiment_definition_id", _parse_uuid(self.experiment_definition_id)
        )
        object.__setattr__(
            self,
            "experiment_definition_fingerprint",
            _parse_hash(self.experiment_definition_fingerprint),
        )
        object.__setattr__(self, "goal_source_uuid", _parse_uuid(self.goal_source_uuid))
        object.__setattr__(
            self,
            "goal_identity_fingerprint",
            _parse_hash(self.goal_identity_fingerprint),
        )
        object.__setattr__(
            self,
            "lifecycle_event",
            _parse_enum(self.lifecycle_event, PersonalExperimentLifecycleEventV1),
        )
        object.__setattr__(self, "event_at", _parse_timestamp(self.event_at, "event_at"))
        object.__setattr__(
            self,
            "lifecycle_reviewed_at",
            _parse_timestamp(self.lifecycle_reviewed_at, "lifecycle_reviewed_at"),
        )
        object.__setattr__(
            self, "experiment_policy_id", _parse_policy_id(self.experiment_policy_id)
        )
        object.__setattr__(
            self,
            "experiment_policy_fingerprint",
            _parse_hash(self.experiment_policy_fingerprint, policy=True),
        )
        supersedes = _normalize_pair_values(
            self.supersedes_lifecycle_id,
            self.supersedes_lifecycle_fingerprint,
        )
        object.__setattr__(self, "supersedes_lifecycle_id", supersedes[0])
        object.__setattr__(self, "supersedes_lifecycle_fingerprint", supersedes[1])
        _validate_marker_kind(
            self.second_brain_personal_experiment,
            self.personal_experiment_kind,
            PersonalExperimentRecordKindV1.LIFECYCLE,
        )
        _validate_record_size(self)

    @property
    def lifecycle_fingerprint(self) -> str:
        """Return the semantic fingerprint of this event."""

        return personal_experiment_hash_json(self.fingerprint_payload())

    def fingerprint_payload(self) -> dict[str, object]:
        return {
            "contract": "personal-experiment-lifecycle-v1",
            "event_at": _format_timestamp(cast(datetime, self.event_at)),
            "experiment_definition_fingerprint": self.experiment_definition_fingerprint,
            "experiment_definition_id": str(self.experiment_definition_id),
            "experiment_policy_fingerprint": self.experiment_policy_fingerprint,
            "experiment_policy_id": self.experiment_policy_id,
            "goal_identity_fingerprint": self.goal_identity_fingerprint,
            "goal_source_uuid": str(self.goal_source_uuid),
            "lifecycle_event": cast(PersonalExperimentLifecycleEventV1, self.lifecycle_event).value,
        }

    def as_dict(self) -> dict[str, object]:
        return {
            "id": str(self.id),
            "second_brain_personal_experiment": PERSONAL_EXPERIMENT_MARKER_VALUE,
            "personal_experiment_kind": PersonalExperimentRecordKindV1.LIFECYCLE.value,
            "experiment_policy_id": self.experiment_policy_id,
            "experiment_policy_fingerprint": self.experiment_policy_fingerprint,
            "experiment_definition_id": str(self.experiment_definition_id),
            "experiment_definition_fingerprint": self.experiment_definition_fingerprint,
            "goal_source_uuid": str(self.goal_source_uuid),
            "goal_identity_fingerprint": self.goal_identity_fingerprint,
            "lifecycle_event": cast(PersonalExperimentLifecycleEventV1, self.lifecycle_event).value,
            "event_at": _format_timestamp(cast(datetime, self.event_at)),
            "lifecycle_reviewed_at": _format_timestamp(cast(datetime, self.lifecycle_reviewed_at)),
            **_pair_projection(
                cast(UUID | None, self.supersedes_lifecycle_id),
                self.supersedes_lifecycle_fingerprint,
                "supersedes_lifecycle_id",
                "supersedes_lifecycle_fingerprint",
            ),
        }


@dataclass(frozen=True, slots=True)
class PersonalExperimentObservationRecordV1:
    """Immutable canonical enrollment link to one Stage 12 observation."""

    id: UUID | str
    experiment_definition_id: UUID | str
    experiment_definition_fingerprint: str
    goal_source_uuid: UUID | str
    goal_identity_fingerprint: str
    goal_progress_definition_id: UUID | str
    goal_progress_definition_fingerprint: str
    goal_progress_policy_fingerprint: str
    stage12_observation_id: UUID | str
    stage12_observation_fingerprint: str
    observation_reviewed_at: datetime | str
    supersedes_observation_id: UUID | str | None = None
    supersedes_observation_fingerprint: str | None = None
    experiment_policy_id: str = PERSONAL_EXPERIMENT_POLICY_ID
    experiment_policy_fingerprint: str = PERSONAL_EXPERIMENT_POLICY_FINGERPRINT
    second_brain_personal_experiment: int = PERSONAL_EXPERIMENT_MARKER_VALUE
    personal_experiment_kind: PersonalExperimentRecordKindV1 | str = (
        PersonalExperimentRecordKindV1.OBSERVATION
    )

    def __post_init__(self) -> None:
        object.__setattr__(self, "id", _parse_uuid(self.id))
        object.__setattr__(
            self, "experiment_definition_id", _parse_uuid(self.experiment_definition_id)
        )
        object.__setattr__(
            self,
            "experiment_definition_fingerprint",
            _parse_hash(self.experiment_definition_fingerprint),
        )
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
        object.__setattr__(
            self,
            "goal_progress_policy_fingerprint",
            _parse_stage12_policy(self.goal_progress_policy_fingerprint),
        )
        object.__setattr__(self, "stage12_observation_id", _parse_uuid(self.stage12_observation_id))
        object.__setattr__(
            self,
            "stage12_observation_fingerprint",
            _parse_hash(self.stage12_observation_fingerprint),
        )
        object.__setattr__(
            self,
            "observation_reviewed_at",
            _parse_timestamp(self.observation_reviewed_at, "observation_reviewed_at"),
        )
        object.__setattr__(
            self, "experiment_policy_id", _parse_policy_id(self.experiment_policy_id)
        )
        object.__setattr__(
            self,
            "experiment_policy_fingerprint",
            _parse_hash(self.experiment_policy_fingerprint, policy=True),
        )
        supersedes = _normalize_pair_values(
            self.supersedes_observation_id,
            self.supersedes_observation_fingerprint,
        )
        object.__setattr__(self, "supersedes_observation_id", supersedes[0])
        object.__setattr__(self, "supersedes_observation_fingerprint", supersedes[1])
        _validate_marker_kind(
            self.second_brain_personal_experiment,
            self.personal_experiment_kind,
            PersonalExperimentRecordKindV1.OBSERVATION,
        )
        _validate_record_size(self)

    @property
    def observation_fingerprint(self) -> str:
        """Return the semantic fingerprint of this enrollment link."""

        return personal_experiment_hash_json(self.fingerprint_payload())

    def fingerprint_payload(self) -> dict[str, object]:
        return {
            "contract": "personal-experiment-observation-v1",
            "experiment_definition_fingerprint": self.experiment_definition_fingerprint,
            "experiment_definition_id": str(self.experiment_definition_id),
            "experiment_policy_fingerprint": self.experiment_policy_fingerprint,
            "experiment_policy_id": self.experiment_policy_id,
            "goal_identity_fingerprint": self.goal_identity_fingerprint,
            "goal_progress_definition_fingerprint": self.goal_progress_definition_fingerprint,
            "goal_progress_definition_id": str(self.goal_progress_definition_id),
            "goal_progress_policy_fingerprint": self.goal_progress_policy_fingerprint,
            "goal_source_uuid": str(self.goal_source_uuid),
            "stage12_observation_fingerprint": self.stage12_observation_fingerprint,
            "stage12_observation_id": str(self.stage12_observation_id),
        }

    def as_dict(self) -> dict[str, object]:
        return {
            "id": str(self.id),
            "second_brain_personal_experiment": PERSONAL_EXPERIMENT_MARKER_VALUE,
            "personal_experiment_kind": PersonalExperimentRecordKindV1.OBSERVATION.value,
            "experiment_policy_id": self.experiment_policy_id,
            "experiment_policy_fingerprint": self.experiment_policy_fingerprint,
            "experiment_definition_id": str(self.experiment_definition_id),
            "experiment_definition_fingerprint": self.experiment_definition_fingerprint,
            "goal_source_uuid": str(self.goal_source_uuid),
            "goal_identity_fingerprint": self.goal_identity_fingerprint,
            "goal_progress_definition_id": str(self.goal_progress_definition_id),
            "goal_progress_definition_fingerprint": self.goal_progress_definition_fingerprint,
            "goal_progress_policy_fingerprint": self.goal_progress_policy_fingerprint,
            "stage12_observation_id": str(self.stage12_observation_id),
            "stage12_observation_fingerprint": self.stage12_observation_fingerprint,
            "observation_reviewed_at": _format_timestamp(
                cast(datetime, self.observation_reviewed_at)
            ),
            **_pair_projection(
                cast(UUID | None, self.supersedes_observation_id),
                self.supersedes_observation_fingerprint,
                "supersedes_observation_id",
                "supersedes_observation_fingerprint",
            ),
        }


@dataclass(frozen=True, slots=True)
class PersonalExperimentReassessmentRecordV1:
    """Immutable canonical reviewed reassessment record."""

    id: UUID | str
    experiment_definition_id: UUID | str
    experiment_definition_fingerprint: str
    goal_source_uuid: UUID | str
    goal_identity_fingerprint: str
    result_fingerprint: str
    evaluation_as_of: datetime | str
    evaluation_policy_fingerprint: str
    disposition: PersonalExperimentDispositionV1 | str
    rationale: str
    reassessment_reviewed_at: datetime | str
    supersedes_reassessment_id: UUID | str | None = None
    supersedes_reassessment_fingerprint: str | None = None
    experiment_policy_id: str = PERSONAL_EXPERIMENT_POLICY_ID
    experiment_policy_fingerprint: str = PERSONAL_EXPERIMENT_POLICY_FINGERPRINT
    second_brain_personal_experiment: int = PERSONAL_EXPERIMENT_MARKER_VALUE
    personal_experiment_kind: PersonalExperimentRecordKindV1 | str = (
        PersonalExperimentRecordKindV1.REASSESSMENT
    )

    def __post_init__(self) -> None:
        object.__setattr__(self, "id", _parse_uuid(self.id))
        object.__setattr__(
            self, "experiment_definition_id", _parse_uuid(self.experiment_definition_id)
        )
        object.__setattr__(
            self,
            "experiment_definition_fingerprint",
            _parse_hash(self.experiment_definition_fingerprint),
        )
        object.__setattr__(self, "goal_source_uuid", _parse_uuid(self.goal_source_uuid))
        object.__setattr__(
            self,
            "goal_identity_fingerprint",
            _parse_hash(self.goal_identity_fingerprint),
        )
        object.__setattr__(self, "result_fingerprint", _parse_hash(self.result_fingerprint))
        object.__setattr__(
            self, "evaluation_as_of", _parse_timestamp(self.evaluation_as_of, "evaluation_as_of")
        )
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
        object.__setattr__(
            self,
            "reassessment_reviewed_at",
            _parse_timestamp(self.reassessment_reviewed_at, "reassessment_reviewed_at"),
        )
        object.__setattr__(
            self, "experiment_policy_id", _parse_policy_id(self.experiment_policy_id)
        )
        object.__setattr__(
            self,
            "experiment_policy_fingerprint",
            _parse_hash(self.experiment_policy_fingerprint, policy=True),
        )
        supersedes = _normalize_pair_values(
            self.supersedes_reassessment_id,
            self.supersedes_reassessment_fingerprint,
        )
        object.__setattr__(self, "supersedes_reassessment_id", supersedes[0])
        object.__setattr__(self, "supersedes_reassessment_fingerprint", supersedes[1])
        _validate_marker_kind(
            self.second_brain_personal_experiment,
            self.personal_experiment_kind,
            PersonalExperimentRecordKindV1.REASSESSMENT,
        )
        _validate_record_size(self)

    @property
    def reassessment_fingerprint(self) -> str:
        """Return the semantic fingerprint of this reassessment."""

        return personal_experiment_hash_json(self.fingerprint_payload())

    def fingerprint_payload(self) -> dict[str, object]:
        return {
            "contract": "personal-experiment-reassessment-v1",
            "disposition": cast(PersonalExperimentDispositionV1, self.disposition).value,
            "evaluation_as_of": _format_timestamp(cast(datetime, self.evaluation_as_of)),
            "evaluation_policy_fingerprint": self.evaluation_policy_fingerprint,
            "experiment_definition_fingerprint": self.experiment_definition_fingerprint,
            "experiment_definition_id": str(self.experiment_definition_id),
            "experiment_policy_fingerprint": self.experiment_policy_fingerprint,
            "experiment_policy_id": self.experiment_policy_id,
            "goal_identity_fingerprint": self.goal_identity_fingerprint,
            "goal_source_uuid": str(self.goal_source_uuid),
            "rationale": self.rationale,
            "result_fingerprint": self.result_fingerprint,
        }

    def as_dict(self) -> dict[str, object]:
        return {
            "id": str(self.id),
            "second_brain_personal_experiment": PERSONAL_EXPERIMENT_MARKER_VALUE,
            "personal_experiment_kind": PersonalExperimentRecordKindV1.REASSESSMENT.value,
            "experiment_policy_id": self.experiment_policy_id,
            "experiment_policy_fingerprint": self.experiment_policy_fingerprint,
            "experiment_definition_id": str(self.experiment_definition_id),
            "experiment_definition_fingerprint": self.experiment_definition_fingerprint,
            "goal_source_uuid": str(self.goal_source_uuid),
            "goal_identity_fingerprint": self.goal_identity_fingerprint,
            "result_fingerprint": self.result_fingerprint,
            "evaluation_as_of": _format_timestamp(cast(datetime, self.evaluation_as_of)),
            "evaluation_policy_fingerprint": self.evaluation_policy_fingerprint,
            "disposition": cast(PersonalExperimentDispositionV1, self.disposition).value,
            "rationale": self.rationale,
            "reassessment_reviewed_at": _format_timestamp(
                cast(datetime, self.reassessment_reviewed_at)
            ),
            **_pair_projection(
                cast(UUID | None, self.supersedes_reassessment_id),
                self.supersedes_reassessment_fingerprint,
                "supersedes_reassessment_id",
                "supersedes_reassessment_fingerprint",
            ),
        }


type PersonalExperimentRecordV1 = (
    PersonalExperimentDefinitionRecordV1
    | PersonalExperimentLifecycleRecordV1
    | PersonalExperimentObservationRecordV1
    | PersonalExperimentReassessmentRecordV1
)


def _record_kind_value(record: PersonalExperimentRecordV1) -> str:
    if isinstance(record, PersonalExperimentDefinitionRecordV1):
        return PersonalExperimentRecordKindV1.DEFINITION.value
    if isinstance(record, PersonalExperimentLifecycleRecordV1):
        return PersonalExperimentRecordKindV1.LIFECYCLE.value
    if isinstance(record, PersonalExperimentObservationRecordV1):
        return PersonalExperimentRecordKindV1.OBSERVATION.value
    return PersonalExperimentRecordKindV1.REASSESSMENT.value


@dataclass(frozen=True, slots=True)
class PersonalExperimentChainResultV1:
    """Generic deterministic chain result with safe issue codes."""

    record_kind: PersonalExperimentRecordKindV1
    state: PersonalExperimentChainStateV1
    active_records: tuple[PersonalExperimentRecordV1, ...]
    issues: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class PersonalExperimentLifecycleValidationV1:
    """Lifecycle read projection for one exact experiment identity."""

    experiment_definition_id: UUID
    experiment_definition_fingerprint: str
    state: str
    activation: PersonalExperimentLifecycleRecordV1 | None
    terminal: PersonalExperimentLifecycleRecordV1 | None
    active_records: tuple[PersonalExperimentLifecycleRecordV1, ...]
    issues: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class PersonalExperimentSourceValidationV1:
    """Exact Goal and Stage 12 source-link projection."""

    state: PersonalExperimentSourceStateV1
    goal: GoalBindingValidationV1
    stage12_definition: DefinitionRecordV1 | None
    issues: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class PersonalExperimentObservationSourceValidationV1:
    """Exact Stage 12 observation-link projection."""

    state: PersonalExperimentSourceStateV1
    observation: ObservationRecordV1 | None
    issues: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class PersonalExperimentReadProjectionV1:
    """Deterministic flat read projection for one vault scan."""

    definitions: tuple[PersonalExperimentDefinitionRecordV1, ...] = ()
    lifecycle: tuple[PersonalExperimentLifecycleRecordV1, ...] = ()
    observations: tuple[PersonalExperimentObservationRecordV1, ...] = ()
    reassessments: tuple[PersonalExperimentReassessmentRecordV1, ...] = ()
    definition_chains: tuple[PersonalExperimentChainResultV1, ...] = ()
    lifecycle_chains: tuple[PersonalExperimentLifecycleValidationV1, ...] = ()
    observation_chains: tuple[PersonalExperimentChainResultV1, ...] = ()
    reassessment_chains: tuple[PersonalExperimentChainResultV1, ...] = ()


def _normalize_optional_uuid_value(value: UUID | str | None) -> UUID | None:
    return None if value is None else _parse_uuid(value)


def _parse_optional_uuid_value(value: UUID | str | None) -> UUID | None:
    return _normalize_optional_uuid_value(value)


def _parse_optional_hash_value(value: str | None) -> str | None:
    return None if value is None else _parse_hash(value)


def _normalize_pair_values(
    record_id: UUID | str | None,
    fingerprint: str | None,
) -> tuple[UUID | None, str | None]:
    normalized_id = _normalize_optional_uuid_value(record_id)
    normalized_fingerprint = _parse_optional_hash_value(fingerprint)
    if (normalized_id is None) != (normalized_fingerprint is None):
        raise PersonalExperimentRecordError("PERSONAL_EXPERIMENT_INVALID_FIELD")
    return normalized_id, normalized_fingerprint


def _pair_projection(
    record_id: UUID | None,
    fingerprint: str | None,
    id_name: str,
    fingerprint_name: str,
) -> dict[str, str]:
    if record_id is None or fingerprint is None:
        return {}
    return {id_name: str(record_id), fingerprint_name: fingerprint}


def _validate_marker_kind(
    marker: int,
    kind: PersonalExperimentRecordKindV1 | str,
    expected: PersonalExperimentRecordKindV1,
) -> None:
    if type(marker) is not int or marker != PERSONAL_EXPERIMENT_MARKER_VALUE:
        raise PersonalExperimentRecordError("PERSONAL_EXPERIMENT_INVALID_RECORD")
    normalized = _parse_enum(kind, PersonalExperimentRecordKindV1)
    if normalized is not expected:
        raise PersonalExperimentRecordError("PERSONAL_EXPERIMENT_INVALID_KIND")


def _validate_record_size(record: PersonalExperimentRecordV1) -> None:
    try:
        size = len(canonical_personal_experiment_json(record.as_dict()).encode("utf-8"))
    except TypeError, ValueError, UnicodeError, OverflowError:
        raise PersonalExperimentRecordError("PERSONAL_EXPERIMENT_INVALID_RECORD") from None
    if size > MAX_PERSONAL_EXPERIMENT_RECORD_BYTES:
        raise PersonalExperimentRecordError("PERSONAL_EXPERIMENT_RECORD_TOO_LARGE")


def _pair_chain_issues(
    normalized: tuple[PersonalExperimentRecordV1, ...],
    predecessor_getter: Any,
    predecessor_fingerprint_getter: Any,
    record_fingerprint_getter: Any,
) -> tuple[str, ...]:
    issues: list[str] = []
    by_id: dict[UUID, PersonalExperimentRecordV1] = {}
    successor_counts: defaultdict[UUID, int] = defaultdict(int)
    for record in normalized:
        record_id = cast(UUID, record.id)
        if record_id in by_id:
            issues.append("PERSONAL_EXPERIMENT_DUPLICATE_SUCCESSOR")
        by_id[record_id] = record
    for record in normalized:
        record_id = cast(UUID, record.id)
        predecessor_id = predecessor_getter(record)
        if predecessor_id is None:
            continue
        if predecessor_id == record_id:
            issues.append("PERSONAL_EXPERIMENT_SELF_REFERENCE")
            continue
        predecessor = by_id.get(predecessor_id)
        if predecessor is None:
            issues.append("PERSONAL_EXPERIMENT_PREDECESSOR_MISSING")
            continue
        successor_counts[predecessor_id] += 1
        if predecessor_fingerprint_getter(record) != record_fingerprint_getter(predecessor):
            issues.append("PERSONAL_EXPERIMENT_BINDING_MISMATCH")
    issues.extend(_cycle_issues(normalized, predecessor_getter))
    if any(count > 1 for count in successor_counts.values()):
        issues.append("PERSONAL_EXPERIMENT_DUPLICATE_SUCCESSOR")
    return tuple(dict.fromkeys(issues))


def _cycle_issues(
    records: tuple[PersonalExperimentRecordV1, ...],
    predecessor_getter: Any,
) -> tuple[str, ...]:
    by_id = {cast(UUID, record.id): record for record in records}
    states: dict[UUID, int] = {}
    issues: list[str] = []

    def visit(record_id: UUID) -> None:
        state = states.get(record_id, 0)
        if state == 1:
            issues.append("PERSONAL_EXPERIMENT_CYCLE")
            return
        if state == 2:
            return
        states[record_id] = 1
        predecessor_id = predecessor_getter(by_id[record_id])
        if predecessor_id is not None and predecessor_id in by_id:
            visit(predecessor_id)
        states[record_id] = 2

    for record in records:
        visit(cast(UUID, record.id))
    return tuple(dict.fromkeys(issues))


def _chain_result(
    kind: PersonalExperimentRecordKindV1,
    normalized: tuple[PersonalExperimentRecordV1, ...],
    issues: tuple[str, ...],
) -> PersonalExperimentChainResultV1:
    if issues:
        return PersonalExperimentChainResultV1(
            kind,
            PersonalExperimentChainStateV1.CONFLICT,
            (),
            issues,
        )
    predecessor_ids = {
        predecessor for record in normalized if (predecessor := _predecessor_id(record)) is not None
    }
    active = tuple(record for record in normalized if cast(UUID, record.id) not in predecessor_ids)
    if len(active) == 0:
        state = PersonalExperimentChainStateV1.ZERO_ACTIVE
    elif len(active) == 1:
        state = PersonalExperimentChainStateV1.ONE_ACTIVE
    else:
        state = PersonalExperimentChainStateV1.MULTIPLE_ACTIVE
    return PersonalExperimentChainResultV1(kind, state, active)


def _predecessor_id(record: PersonalExperimentRecordV1) -> UUID | None:
    if isinstance(record, PersonalExperimentDefinitionRecordV1):
        return cast(UUID | None, record.supersedes_definition_id)
    if isinstance(record, PersonalExperimentLifecycleRecordV1):
        return cast(UUID | None, record.supersedes_lifecycle_id)
    if isinstance(record, PersonalExperimentObservationRecordV1):
        return cast(UUID | None, record.supersedes_observation_id)
    return cast(UUID | None, record.supersedes_reassessment_id)


def validate_personal_experiment_definition_chain(
    records: Iterable[PersonalExperimentDefinitionRecordV1],
) -> PersonalExperimentChainResultV1:
    """Validate one exact Goal/Stage 12 definition replacement chain."""

    normalized = tuple(sorted(records, key=lambda item: str(item.id)))
    if any(type(item) is not PersonalExperimentDefinitionRecordV1 for item in normalized):
        return PersonalExperimentChainResultV1(
            PersonalExperimentRecordKindV1.DEFINITION,
            PersonalExperimentChainStateV1.CONFLICT,
            (),
            ("PERSONAL_EXPERIMENT_INVALID_RECORD",),
        )
    issues: list[str] = []
    if normalized:
        first = normalized[0]
        for item in normalized[1:]:
            if (
                item.goal_source_uuid != first.goal_source_uuid
                or item.goal_identity_fingerprint != first.goal_identity_fingerprint
                or item.goal_progress_definition_id != first.goal_progress_definition_id
                or item.goal_progress_definition_fingerprint
                != first.goal_progress_definition_fingerprint
                or item.goal_progress_policy_fingerprint != first.goal_progress_policy_fingerprint
                or item.experiment_policy_fingerprint != first.experiment_policy_fingerprint
            ):
                issues.append("PERSONAL_EXPERIMENT_BINDING_MISMATCH")
    issues.extend(
        _pair_chain_issues(
            cast(tuple[PersonalExperimentRecordV1, ...], normalized),
            lambda item: cast(PersonalExperimentDefinitionRecordV1, item).supersedes_definition_id,
            lambda item: (
                cast(PersonalExperimentDefinitionRecordV1, item).supersedes_definition_fingerprint
            ),
            lambda item: (
                cast(PersonalExperimentDefinitionRecordV1, item).experiment_definition_fingerprint
            ),
        )
    )
    return _chain_result(
        PersonalExperimentRecordKindV1.DEFINITION,
        cast(tuple[PersonalExperimentRecordV1, ...], normalized),
        tuple(dict.fromkeys(issues)),
    )


def validate_personal_experiment_lifecycle_chain(
    records: Iterable[PersonalExperimentLifecycleRecordV1],
) -> PersonalExperimentLifecycleValidationV1:
    """Validate lifecycle corrections and derive one safe state."""

    normalized = tuple(sorted(records, key=lambda item: str(item.id)))
    if not normalized:
        return PersonalExperimentLifecycleValidationV1(
            UUID(int=0),
            "",
            "planned",
            None,
            None,
            (),
        )
    if any(type(item) is not PersonalExperimentLifecycleRecordV1 for item in normalized):
        first = normalized[0]
        return PersonalExperimentLifecycleValidationV1(
            cast(UUID, first.experiment_definition_id),
            first.experiment_definition_fingerprint,
            "invalid",
            None,
            None,
            (),
            ("PERSONAL_EXPERIMENT_INVALID_RECORD",),
        )
    first = normalized[0]
    issues: list[str] = []
    for item in normalized[1:]:
        if (
            item.experiment_definition_id != first.experiment_definition_id
            or item.experiment_definition_fingerprint != first.experiment_definition_fingerprint
            or item.goal_source_uuid != first.goal_source_uuid
            or item.goal_identity_fingerprint != first.goal_identity_fingerprint
            or item.experiment_policy_fingerprint != first.experiment_policy_fingerprint
        ):
            issues.append("PERSONAL_EXPERIMENT_BINDING_MISMATCH")
    issues.extend(
        _pair_chain_issues(
            cast(tuple[PersonalExperimentRecordV1, ...], normalized),
            lambda item: cast(PersonalExperimentLifecycleRecordV1, item).supersedes_lifecycle_id,
            lambda item: (
                cast(PersonalExperimentLifecycleRecordV1, item).supersedes_lifecycle_fingerprint
            ),
            lambda item: cast(PersonalExperimentLifecycleRecordV1, item).lifecycle_fingerprint,
        )
    )
    if issues:
        return PersonalExperimentLifecycleValidationV1(
            cast(UUID, first.experiment_definition_id),
            first.experiment_definition_fingerprint,
            "invalid",
            None,
            None,
            (),
            tuple(dict.fromkeys(issues)),
        )
    predecessor_ids = {
        cast(UUID, item.supersedes_lifecycle_id)
        for item in normalized
        if item.supersedes_lifecycle_id is not None
    }
    active = tuple(item for item in normalized if cast(UUID, item.id) not in predecessor_ids)
    active_activations = tuple(
        item
        for item in active
        if item.lifecycle_event is PersonalExperimentLifecycleEventV1.ACTIVATION
    )
    active_terminals = tuple(
        item
        for item in active
        if item.lifecycle_event
        in {
            PersonalExperimentLifecycleEventV1.COMPLETION,
            PersonalExperimentLifecycleEventV1.CANCELLATION,
        }
    )
    if len(active_activations) > 1 or len(active_terminals) > 1:
        return PersonalExperimentLifecycleValidationV1(
            cast(UUID, first.experiment_definition_id),
            first.experiment_definition_fingerprint,
            "invalid",
            None,
            None,
            active,
            ("PERSONAL_EXPERIMENT_LIFECYCLE_CHAIN_CONFLICT",),
        )
    activation = active_activations[0] if active_activations else None
    terminal = active_terminals[0] if active_terminals else None
    if terminal is not None and activation is None:
        issues.append("PERSONAL_EXPERIMENT_BINDING_MISMATCH")
    if (
        activation is not None
        and terminal is not None
        and cast(datetime, terminal.event_at) <= cast(datetime, activation.event_at)
    ):
        issues.append("PERSONAL_EXPERIMENT_BINDING_MISMATCH")
    state = (
        "planned"
        if activation is None
        else "active"
        if terminal is None
        else "completed"
        if terminal.lifecycle_event is PersonalExperimentLifecycleEventV1.COMPLETION
        else "cancelled"
    )
    if issues:
        state = "invalid"
    return PersonalExperimentLifecycleValidationV1(
        cast(UUID, first.experiment_definition_id),
        first.experiment_definition_fingerprint,
        state,
        activation,
        terminal,
        active,
        tuple(dict.fromkeys(issues)),
    )


def validate_personal_experiment_observation_chain(
    records: Iterable[PersonalExperimentObservationRecordV1],
) -> PersonalExperimentChainResultV1:
    """Validate explicit enrollment corrections and duplicate source leaves."""

    normalized = tuple(sorted(records, key=lambda item: str(item.id)))
    if any(type(item) is not PersonalExperimentObservationRecordV1 for item in normalized):
        return PersonalExperimentChainResultV1(
            PersonalExperimentRecordKindV1.OBSERVATION,
            PersonalExperimentChainStateV1.CONFLICT,
            (),
            ("PERSONAL_EXPERIMENT_INVALID_RECORD",),
        )
    issues: list[str] = []
    if normalized:
        first = normalized[0]
        for item in normalized[1:]:
            if (
                item.experiment_definition_id != first.experiment_definition_id
                or item.experiment_definition_fingerprint != first.experiment_definition_fingerprint
                or item.goal_source_uuid != first.goal_source_uuid
                or item.goal_identity_fingerprint != first.goal_identity_fingerprint
                or item.goal_progress_definition_id != first.goal_progress_definition_id
                or item.goal_progress_definition_fingerprint
                != first.goal_progress_definition_fingerprint
                or item.goal_progress_policy_fingerprint != first.goal_progress_policy_fingerprint
                or item.experiment_policy_fingerprint != first.experiment_policy_fingerprint
            ):
                issues.append("PERSONAL_EXPERIMENT_BINDING_MISMATCH")
    issues.extend(
        _pair_chain_issues(
            cast(tuple[PersonalExperimentRecordV1, ...], normalized),
            lambda item: (
                cast(PersonalExperimentObservationRecordV1, item).supersedes_observation_id
            ),
            lambda item: (
                cast(PersonalExperimentObservationRecordV1, item).supersedes_observation_fingerprint
            ),
            lambda item: cast(PersonalExperimentObservationRecordV1, item).observation_fingerprint,
        )
    )
    if not issues:
        predecessor_ids = {
            cast(UUID, item.supersedes_observation_id)
            for item in normalized
            if item.supersedes_observation_id is not None
        }
        active = tuple(item for item in normalized if cast(UUID, item.id) not in predecessor_ids)
        source_ids = tuple(item.stage12_observation_id for item in active)
        if len(source_ids) != len(set(source_ids)):
            issues.append("PERSONAL_EXPERIMENT_DUPLICATE_SUCCESSOR")
    return _chain_result(
        PersonalExperimentRecordKindV1.OBSERVATION,
        cast(tuple[PersonalExperimentRecordV1, ...], normalized),
        tuple(dict.fromkeys(issues)),
    )


def validate_personal_experiment_reassessment_chain(
    records: Iterable[PersonalExperimentReassessmentRecordV1],
) -> PersonalExperimentChainResultV1:
    """Validate one append-only reassessment chain."""

    normalized = tuple(sorted(records, key=lambda item: str(item.id)))
    if any(type(item) is not PersonalExperimentReassessmentRecordV1 for item in normalized):
        return PersonalExperimentChainResultV1(
            PersonalExperimentRecordKindV1.REASSESSMENT,
            PersonalExperimentChainStateV1.CONFLICT,
            (),
            ("PERSONAL_EXPERIMENT_INVALID_RECORD",),
        )
    issues: list[str] = []
    if normalized:
        first = normalized[0]
        for item in normalized[1:]:
            if (
                item.experiment_definition_id != first.experiment_definition_id
                or item.experiment_definition_fingerprint != first.experiment_definition_fingerprint
                or item.goal_source_uuid != first.goal_source_uuid
                or item.goal_identity_fingerprint != first.goal_identity_fingerprint
                or item.experiment_policy_fingerprint != first.experiment_policy_fingerprint
            ):
                issues.append("PERSONAL_EXPERIMENT_BINDING_MISMATCH")
    issues.extend(
        _pair_chain_issues(
            cast(tuple[PersonalExperimentRecordV1, ...], normalized),
            lambda item: (
                cast(PersonalExperimentReassessmentRecordV1, item).supersedes_reassessment_id
            ),
            lambda item: (
                cast(
                    PersonalExperimentReassessmentRecordV1, item
                ).supersedes_reassessment_fingerprint
            ),
            lambda item: (
                cast(PersonalExperimentReassessmentRecordV1, item).reassessment_fingerprint
            ),
        )
    )
    return _chain_result(
        PersonalExperimentRecordKindV1.REASSESSMENT,
        cast(tuple[PersonalExperimentRecordV1, ...], normalized),
        tuple(dict.fromkeys(issues)),
    )


def validate_personal_experiment_definition_binding(
    definition: PersonalExperimentDefinitionRecordV1,
    *,
    current_goals: Iterable[object],
    stage12_definitions: Iterable[DefinitionRecordV1],
    explicit_goal_source_uuid: UUID | str | None = None,
) -> PersonalExperimentSourceValidationV1:
    """Validate one definition against exact current Goal and Stage 12 sources."""

    goal = validate_goal_binding(
        definition.goal_source_uuid,
        definition.goal_identity_fingerprint,
        current_goals=current_goals,
        explicit_goal_source_uuid=explicit_goal_source_uuid,
    )
    matching = tuple(
        item for item in stage12_definitions if item.id == definition.goal_progress_definition_id
    )
    issues: list[str] = []
    stage12_definition = matching[0] if len(matching) == 1 else None
    if len(matching) > 1:
        state = PersonalExperimentSourceStateV1.AMBIGUOUS
        issues.append("PERSONAL_EXPERIMENT_BINDING_MISMATCH")
    elif stage12_definition is None:
        state = PersonalExperimentSourceStateV1.SOURCE_MISSING
        issues.append("PERSONAL_EXPERIMENT_SOURCE_MISSING")
    elif (
        stage12_definition.definition_fingerprint != definition.goal_progress_definition_fingerprint
        or stage12_definition.goal_source_uuid != definition.goal_source_uuid
        or stage12_definition.goal_identity_fingerprint != definition.goal_identity_fingerprint
        or stage12_definition.goal_progress_policy_fingerprint
        != definition.goal_progress_policy_fingerprint
    ):
        state = PersonalExperimentSourceStateV1.SOURCE_CHANGED
        issues.append("PERSONAL_EXPERIMENT_SOURCE_CHANGED")
    elif goal.state is GoalProgressBindingStateV1.EXACT_CURRENT:
        state = PersonalExperimentSourceStateV1.EXACT_CURRENT
    elif goal.state is GoalProgressBindingStateV1.SOURCE_CHANGED:
        state = PersonalExperimentSourceStateV1.SOURCE_CHANGED
        issues.append("PERSONAL_EXPERIMENT_SOURCE_CHANGED")
    elif goal.state is GoalProgressBindingStateV1.AMBIGUOUS:
        state = PersonalExperimentSourceStateV1.AMBIGUOUS
        issues.append("PERSONAL_EXPERIMENT_BINDING_MISMATCH")
    else:
        state = PersonalExperimentSourceStateV1.NON_CURRENT
        issues.append("PERSONAL_EXPERIMENT_SOURCE_MISSING")
    return PersonalExperimentSourceValidationV1(
        state,
        goal,
        stage12_definition,
        tuple(dict.fromkeys(issues)),
    )


def validate_personal_experiment_observation_binding(
    enrollment: PersonalExperimentObservationRecordV1,
    *,
    stage12_observations: Iterable[ObservationRecordV1],
) -> PersonalExperimentObservationSourceValidationV1:
    """Validate an enrollment against one exact Stage 12 observation."""

    matching = tuple(
        item for item in stage12_observations if item.id == enrollment.stage12_observation_id
    )
    if len(matching) > 1:
        return PersonalExperimentObservationSourceValidationV1(
            PersonalExperimentSourceStateV1.AMBIGUOUS,
            None,
            ("PERSONAL_EXPERIMENT_BINDING_MISMATCH",),
        )
    if not matching:
        return PersonalExperimentObservationSourceValidationV1(
            PersonalExperimentSourceStateV1.SOURCE_MISSING,
            None,
            ("PERSONAL_EXPERIMENT_SOURCE_MISSING",),
        )
    observation = matching[0]
    if (
        observation.observation_fingerprint != enrollment.stage12_observation_fingerprint
        or observation.progress_definition_id != enrollment.goal_progress_definition_id
        or observation.definition_fingerprint != enrollment.goal_progress_definition_fingerprint
        or observation.goal_source_uuid != enrollment.goal_source_uuid
        or observation.goal_identity_fingerprint != enrollment.goal_identity_fingerprint
        or observation.goal_progress_policy_fingerprint
        != enrollment.goal_progress_policy_fingerprint
    ):
        return PersonalExperimentObservationSourceValidationV1(
            PersonalExperimentSourceStateV1.SOURCE_CHANGED,
            observation,
            ("PERSONAL_EXPERIMENT_SOURCE_CHANGED",),
        )
    return PersonalExperimentObservationSourceValidationV1(
        PersonalExperimentSourceStateV1.EXACT_CURRENT,
        observation,
    )


def build_personal_experiment_read_projection(
    records: Iterable[PersonalExperimentRecordV1],
) -> PersonalExperimentReadProjectionV1:
    """Build a deterministic flat projection and all available chain views."""

    normalized = tuple(sorted(records, key=lambda item: (_record_kind_value(item), str(item.id))))
    definitions_list: list[PersonalExperimentDefinitionRecordV1] = []
    lifecycle_list: list[PersonalExperimentLifecycleRecordV1] = []
    observations_list: list[PersonalExperimentObservationRecordV1] = []
    reassessments_list: list[PersonalExperimentReassessmentRecordV1] = []
    for item in normalized:
        if isinstance(item, PersonalExperimentDefinitionRecordV1):
            definitions_list.append(item)
        elif isinstance(item, PersonalExperimentLifecycleRecordV1):
            lifecycle_list.append(item)
        elif isinstance(item, PersonalExperimentObservationRecordV1):
            observations_list.append(item)
        else:
            reassessments_list.append(item)
    definitions = tuple(definitions_list)
    lifecycle = tuple(lifecycle_list)
    observations = tuple(observations_list)
    reassessments = tuple(reassessments_list)
    definition_groups: dict[
        tuple[UUID, str, UUID, str], list[PersonalExperimentDefinitionRecordV1]
    ] = defaultdict(list)
    for item in definitions:
        definition_groups[
            (
                cast(UUID, item.goal_source_uuid),
                item.goal_identity_fingerprint,
                cast(UUID, item.goal_progress_definition_id),
                item.goal_progress_definition_fingerprint,
            )
        ].append(item)
    definition_chains = tuple(
        validate_personal_experiment_definition_chain(group)
        for _, group in sorted(definition_groups.items(), key=lambda pair: tuple(map(str, pair[0])))
    )
    lifecycle_groups: dict[tuple[UUID, str], list[PersonalExperimentLifecycleRecordV1]] = (
        defaultdict(list)
    )
    for item in lifecycle:
        lifecycle_groups[
            (
                cast(UUID, item.experiment_definition_id),
                item.experiment_definition_fingerprint,
            )
        ].append(item)
    lifecycle_chains = tuple(
        validate_personal_experiment_lifecycle_chain(group)
        for _, group in sorted(lifecycle_groups.items(), key=lambda pair: tuple(map(str, pair[0])))
    )
    observation_groups: dict[tuple[UUID, str], list[PersonalExperimentObservationRecordV1]] = (
        defaultdict(list)
    )
    for item in observations:
        observation_groups[
            (
                cast(UUID, item.experiment_definition_id),
                item.experiment_definition_fingerprint,
            )
        ].append(item)
    observation_chains = tuple(
        validate_personal_experiment_observation_chain(group)
        for _, group in sorted(
            observation_groups.items(), key=lambda pair: tuple(map(str, pair[0]))
        )
    )
    reassessment_groups: dict[tuple[UUID, str], list[PersonalExperimentReassessmentRecordV1]] = (
        defaultdict(list)
    )
    for item in reassessments:
        reassessment_groups[
            (
                cast(UUID, item.experiment_definition_id),
                item.experiment_definition_fingerprint,
            )
        ].append(item)
    reassessment_chains = tuple(
        validate_personal_experiment_reassessment_chain(group)
        for _, group in sorted(
            reassessment_groups.items(), key=lambda pair: tuple(map(str, pair[0]))
        )
    )
    return PersonalExperimentReadProjectionV1(
        definitions=definitions,
        lifecycle=lifecycle,
        observations=observations,
        reassessments=reassessments,
        definition_chains=definition_chains,
        lifecycle_chains=lifecycle_chains,
        observation_chains=observation_chains,
        reassessment_chains=reassessment_chains,
    )


def _parse_definition(
    data: Mapping[object, object],
    record_id: UUID,
) -> PersonalExperimentDefinitionRecordV1:
    return PersonalExperimentDefinitionRecordV1(
        id=record_id,
        experiment_policy_id=cast(str, _required(data, "experiment_policy_id")),
        experiment_policy_fingerprint=cast(str, _required(data, "experiment_policy_fingerprint")),
        goal_source_uuid=cast(UUID | str, _required(data, "goal_source_uuid")),
        goal_identity_fingerprint=cast(str, _required(data, "goal_identity_fingerprint")),
        goal_progress_definition_id=cast(
            UUID | str, _required(data, "goal_progress_definition_id")
        ),
        goal_progress_definition_fingerprint=cast(
            str, _required(data, "goal_progress_definition_fingerprint")
        ),
        goal_progress_policy_fingerprint=cast(
            str, _required(data, "goal_progress_policy_fingerprint")
        ),
        hypothesis=cast(str, _required(data, "hypothesis")),
        intervention=cast(str, _required(data, "intervention")),
        baseline_strategy=cast(
            PersonalExperimentBaselineStrategyV1 | str, _required(data, "baseline_strategy")
        ),
        definition_reviewed_at=cast(datetime | str, _required(data, "definition_reviewed_at")),
        baseline_observation_uuid=_optional_uuid(data, "baseline_observation_uuid"),
        baseline_observation_fingerprint=_optional_hash(data, "baseline_observation_fingerprint"),
        supersedes_definition_id=_optional_uuid(data, "supersedes_definition_id"),
        supersedes_definition_fingerprint=_optional_hash(data, "supersedes_definition_fingerprint"),
        second_brain_personal_experiment=cast(int, _required(data, PERSONAL_EXPERIMENT_MARKER)),
        personal_experiment_kind=cast(str, _required(data, "personal_experiment_kind")),
    )


def _parse_lifecycle(
    data: Mapping[object, object],
    record_id: UUID,
) -> PersonalExperimentLifecycleRecordV1:
    return PersonalExperimentLifecycleRecordV1(
        id=record_id,
        experiment_definition_id=cast(UUID | str, _required(data, "experiment_definition_id")),
        experiment_definition_fingerprint=cast(
            str, _required(data, "experiment_definition_fingerprint")
        ),
        goal_source_uuid=cast(UUID | str, _required(data, "goal_source_uuid")),
        goal_identity_fingerprint=cast(str, _required(data, "goal_identity_fingerprint")),
        lifecycle_event=cast(
            PersonalExperimentLifecycleEventV1 | str, _required(data, "lifecycle_event")
        ),
        event_at=cast(datetime | str, _required(data, "event_at")),
        lifecycle_reviewed_at=cast(datetime | str, _required(data, "lifecycle_reviewed_at")),
        supersedes_lifecycle_id=_optional_uuid(data, "supersedes_lifecycle_id"),
        supersedes_lifecycle_fingerprint=_optional_hash(data, "supersedes_lifecycle_fingerprint"),
        experiment_policy_id=cast(str, _required(data, "experiment_policy_id")),
        experiment_policy_fingerprint=cast(str, _required(data, "experiment_policy_fingerprint")),
        second_brain_personal_experiment=cast(int, _required(data, PERSONAL_EXPERIMENT_MARKER)),
        personal_experiment_kind=cast(str, _required(data, "personal_experiment_kind")),
    )


def _parse_observation(
    data: Mapping[object, object],
    record_id: UUID,
) -> PersonalExperimentObservationRecordV1:
    return PersonalExperimentObservationRecordV1(
        id=record_id,
        experiment_definition_id=cast(UUID | str, _required(data, "experiment_definition_id")),
        experiment_definition_fingerprint=cast(
            str, _required(data, "experiment_definition_fingerprint")
        ),
        goal_source_uuid=cast(UUID | str, _required(data, "goal_source_uuid")),
        goal_identity_fingerprint=cast(str, _required(data, "goal_identity_fingerprint")),
        goal_progress_definition_id=cast(
            UUID | str, _required(data, "goal_progress_definition_id")
        ),
        goal_progress_definition_fingerprint=cast(
            str, _required(data, "goal_progress_definition_fingerprint")
        ),
        goal_progress_policy_fingerprint=cast(
            str, _required(data, "goal_progress_policy_fingerprint")
        ),
        stage12_observation_id=cast(UUID | str, _required(data, "stage12_observation_id")),
        stage12_observation_fingerprint=cast(
            str, _required(data, "stage12_observation_fingerprint")
        ),
        observation_reviewed_at=cast(datetime | str, _required(data, "observation_reviewed_at")),
        supersedes_observation_id=_optional_uuid(data, "supersedes_observation_id"),
        supersedes_observation_fingerprint=_optional_hash(
            data, "supersedes_observation_fingerprint"
        ),
        experiment_policy_id=cast(str, _required(data, "experiment_policy_id")),
        experiment_policy_fingerprint=cast(str, _required(data, "experiment_policy_fingerprint")),
        second_brain_personal_experiment=cast(int, _required(data, PERSONAL_EXPERIMENT_MARKER)),
        personal_experiment_kind=cast(str, _required(data, "personal_experiment_kind")),
    )


def _parse_reassessment(
    data: Mapping[object, object],
    record_id: UUID,
) -> PersonalExperimentReassessmentRecordV1:
    return PersonalExperimentReassessmentRecordV1(
        id=record_id,
        experiment_definition_id=cast(UUID | str, _required(data, "experiment_definition_id")),
        experiment_definition_fingerprint=cast(
            str, _required(data, "experiment_definition_fingerprint")
        ),
        goal_source_uuid=cast(UUID | str, _required(data, "goal_source_uuid")),
        goal_identity_fingerprint=cast(str, _required(data, "goal_identity_fingerprint")),
        result_fingerprint=cast(str, _required(data, "result_fingerprint")),
        evaluation_as_of=cast(datetime | str, _required(data, "evaluation_as_of")),
        evaluation_policy_fingerprint=cast(str, _required(data, "evaluation_policy_fingerprint")),
        disposition=cast(PersonalExperimentDispositionV1 | str, _required(data, "disposition")),
        rationale=cast(str, _required(data, "rationale")),
        reassessment_reviewed_at=cast(datetime | str, _required(data, "reassessment_reviewed_at")),
        supersedes_reassessment_id=_optional_uuid(data, "supersedes_reassessment_id"),
        supersedes_reassessment_fingerprint=_optional_hash(
            data, "supersedes_reassessment_fingerprint"
        ),
        experiment_policy_id=cast(str, _required(data, "experiment_policy_id")),
        experiment_policy_fingerprint=cast(str, _required(data, "experiment_policy_fingerprint")),
        second_brain_personal_experiment=cast(int, _required(data, PERSONAL_EXPERIMENT_MARKER)),
        personal_experiment_kind=cast(str, _required(data, "personal_experiment_kind")),
    )


def parse_personal_experiment_record(
    front_matter: Mapping[str, object] | object,
    *,
    note_id: UUID | str | None = None,
) -> PersonalExperimentRecordV1 | None:
    """Parse one exact marker-enrolled record; ordinary notes return none."""

    if not is_personal_experiment_enrolled(front_matter):
        return None
    if not isinstance(front_matter, Mapping):
        raise PersonalExperimentRecordError("PERSONAL_EXPERIMENT_INVALID_RECORD")
    data = cast(Mapping[object, object], front_matter)
    kind_value = data.get("personal_experiment_kind")
    if type(kind_value) is not str:
        raise PersonalExperimentRecordError("PERSONAL_EXPERIMENT_INVALID_KIND")
    try:
        kind = PersonalExperimentRecordKindV1(kind_value)
    except ValueError:
        raise PersonalExperimentRecordError("PERSONAL_EXPERIMENT_INVALID_KIND") from None
    allowed = {
        PersonalExperimentRecordKindV1.DEFINITION: _DEFINITION_FIELDS,
        PersonalExperimentRecordKindV1.LIFECYCLE: _LIFECYCLE_FIELDS,
        PersonalExperimentRecordKindV1.OBSERVATION: _OBSERVATION_FIELDS,
        PersonalExperimentRecordKindV1.REASSESSMENT: _REASSESSMENT_FIELDS,
    }[kind]
    _reject_unknown_fields(data, allowed)
    record_id = _record_id(data, note_id)
    record: PersonalExperimentRecordV1
    try:
        if kind is PersonalExperimentRecordKindV1.DEFINITION:
            record = _parse_definition(data, record_id)
        elif kind is PersonalExperimentRecordKindV1.LIFECYCLE:
            record = _parse_lifecycle(data, record_id)
        elif kind is PersonalExperimentRecordKindV1.OBSERVATION:
            record = _parse_observation(data, record_id)
        else:
            record = _parse_reassessment(data, record_id)
    except PersonalExperimentValidationError:
        raise
    except TypeError, ValueError, UnicodeError, OverflowError:
        raise PersonalExperimentRecordError("PERSONAL_EXPERIMENT_INVALID_RECORD") from None
    return record


def parse_personal_experiment_definition(
    front_matter: Mapping[str, object] | object,
    *,
    note_id: UUID | str | None = None,
) -> PersonalExperimentDefinitionRecordV1 | None:
    """Parse a definition or return none for another family."""

    record = parse_personal_experiment_record(front_matter, note_id=note_id)
    if record is None:
        return None
    if type(record) is not PersonalExperimentDefinitionRecordV1:
        raise PersonalExperimentRecordError("PERSONAL_EXPERIMENT_INVALID_KIND")
    return record


def parse_personal_experiment_lifecycle(
    front_matter: Mapping[str, object] | object,
    *,
    note_id: UUID | str | None = None,
) -> PersonalExperimentLifecycleRecordV1 | None:
    """Parse a lifecycle record or return none for another family."""

    record = parse_personal_experiment_record(front_matter, note_id=note_id)
    if record is None:
        return None
    if type(record) is not PersonalExperimentLifecycleRecordV1:
        raise PersonalExperimentRecordError("PERSONAL_EXPERIMENT_INVALID_KIND")
    return record


def parse_personal_experiment_observation(
    front_matter: Mapping[str, object] | object,
    *,
    note_id: UUID | str | None = None,
) -> PersonalExperimentObservationRecordV1 | None:
    """Parse an observation enrollment or return none for another family."""

    record = parse_personal_experiment_record(front_matter, note_id=note_id)
    if record is None:
        return None
    if type(record) is not PersonalExperimentObservationRecordV1:
        raise PersonalExperimentRecordError("PERSONAL_EXPERIMENT_INVALID_KIND")
    return record


def parse_personal_experiment_reassessment(
    front_matter: Mapping[str, object] | object,
    *,
    note_id: UUID | str | None = None,
) -> PersonalExperimentReassessmentRecordV1 | None:
    """Parse a reassessment record or return none for another family."""

    record = parse_personal_experiment_record(front_matter, note_id=note_id)
    if record is None:
        return None
    if type(record) is not PersonalExperimentReassessmentRecordV1:
        raise PersonalExperimentRecordError("PERSONAL_EXPERIMENT_INVALID_KIND")
    return record


def _predecessor_fingerprint(record: PersonalExperimentRecordV1) -> str | None:
    if isinstance(record, PersonalExperimentDefinitionRecordV1):
        return record.supersedes_definition_fingerprint
    if isinstance(record, PersonalExperimentLifecycleRecordV1):
        return record.supersedes_lifecycle_fingerprint
    if isinstance(record, PersonalExperimentObservationRecordV1):
        return record.supersedes_observation_fingerprint
    return record.supersedes_reassessment_fingerprint


__all__ = [
    "MAX_PERSONAL_EXPERIMENT_OBSERVATIONS",
    "MAX_PERSONAL_EXPERIMENT_RECORDS",
    "MAX_PERSONAL_EXPERIMENT_RECORD_BYTES",
    "MAX_PERSONAL_EXPERIMENT_TEXT_BYTES",
    "PERSONAL_EXPERIMENT_DERIVATION_ID",
    "PERSONAL_EXPERIMENT_DIAGNOSTIC_MESSAGES",
    "PERSONAL_EXPERIMENT_MARKER",
    "PERSONAL_EXPERIMENT_MARKER_VALUE",
    "PERSONAL_EXPERIMENT_POLICY_CANONICAL_JSON",
    "PERSONAL_EXPERIMENT_POLICY_FINGERPRINT",
    "PERSONAL_EXPERIMENT_POLICY_ID",
    "PersonalExperimentBaselineStrategyV1",
    "PersonalExperimentChainResultV1",
    "PersonalExperimentChainStateV1",
    "PersonalExperimentDefinitionRecordV1",
    "PersonalExperimentDispositionV1",
    "PersonalExperimentLifecycleEventV1",
    "PersonalExperimentLifecycleRecordV1",
    "PersonalExperimentLifecycleValidationV1",
    "PersonalExperimentObservationRecordV1",
    "PersonalExperimentObservationSourceValidationV1",
    "PersonalExperimentReadProjectionV1",
    "PersonalExperimentReassessmentRecordV1",
    "PersonalExperimentRecordError",
    "PersonalExperimentRecordKindV1",
    "PersonalExperimentSourceStateV1",
    "PersonalExperimentSourceValidationV1",
    "PersonalExperimentValidationError",
    "build_personal_experiment_read_projection",
    "canonical_personal_experiment_json",
    "is_personal_experiment_enrolled",
    "parse_personal_experiment_definition",
    "parse_personal_experiment_lifecycle",
    "parse_personal_experiment_observation",
    "parse_personal_experiment_reassessment",
    "parse_personal_experiment_record",
    "personal_experiment_hash_json",
    "validate_personal_experiment_definition_binding",
    "validate_personal_experiment_definition_chain",
    "validate_personal_experiment_lifecycle_chain",
    "validate_personal_experiment_observation_binding",
    "validate_personal_experiment_observation_chain",
    "validate_personal_experiment_policy",
    "validate_personal_experiment_reassessment_chain",
]
