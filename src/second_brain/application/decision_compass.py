"""Cognitive Twin v3 / Stage 13 Decision Compass.

This module is an additive, transient read/composition boundary.  It keeps the
existing Compare v1, Stage 10, Stage 11 and Stage 12D DTOs authoritative.  The
normal composition remains provider-free; the separate explicit Advisor
handoff reuses the existing Growth Advisor boundary without adding provider
payload fields, persistence or write capability.
"""

from __future__ import annotations

import hashlib
import json
import re
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from enum import StrEnum
from typing import Final, Protocol, cast
from uuid import UUID

from second_brain.application.assistant import AssistantExplicitContext, AssistantOption
from second_brain.application.behavioral_self_model import (
    DEFAULT_BEHAVIORAL_SELF_MODEL_REQUEST,
    BehavioralPatternTypeV1,
    BehavioralPatternV1,
    BehavioralSelfModelError,
    BehavioralSelfModelErrorCode,
    BehavioralSelfModelRequest,
    BehavioralSelfModelResultV1,
    BuildBehavioralSelfModel,
    validate_behavioral_self_model_result,
)
from second_brain.application.compare import (
    DEFAULT_ASSISTANT_MAX_CONTEXT_BYTES,
    DEFAULT_ASSISTANT_MAX_RESULT_BYTES,
    DEFAULT_MAX_RESULT_BYTES,
    MIN_MAX_RESULT_BYTES_V1,
    CompareAssistantInputsV1,
    CompareBranchErrorCodeV1,
    CompareBranchErrorV1,
    CompareBranchStateV1,
    CompareExecutionContextV1,
    CompareInvalidRequestError,
    CompareOptionV1,
    CompareRequestV1,
    CompareSimulateMeAbstentionBranchV1,
    CompareSimulateMeBranchV1,
    CompareSimulateMeErrorBranchV1,
    CompareSimulateMePort,
    CompareSimulateMeResultBranchV1,
    validate_compare_request,
)
from second_brain.application.growth import (
    BuildGrowthGoalContext,
    GrowthEngineRequestV1,
    GrowthError,
    GrowthErrorCode,
    GrowthGoalIdentityV1,
    GrowthGoalMissingError,
    GrowthGoalSelectionModeV1,
    GrowthGoalSelectionV1,
    GrowthGoalSourceChangedError,
    GrowthMappingStore,
    GrowthPolicyMismatchError,
    growth_hash_json,
    validate_growth_goal_context,
)
from second_brain.application.growth_advisor import (
    GROWTH_ADVISOR_CONTRACT_VERSION,
    GrowthAdvisorBranchStateV1,
    GrowthAdvisorBranchV1,
    GrowthAdvisorError,
    GrowthAdvisorErrorCode,
    GrowthAdvisorGoalPreviewV1,
    GrowthAdvisorRequestV1,
    validate_growth_advisor_branch,
)
from second_brain.application.growth_goal_progress_composition import (
    COMPOSITION_POLICY_FINGERPRINT,
    BuildGrowthGoalProgressCompositionV1,
    GrowthGoalProgressCompositionError,
    GrowthGoalProgressCompositionErrorCodeV1,
    GrowthGoalProgressCompositionRequestV1,
    GrowthGoalProgressCompositionResultV1,
    validate_growth_goal_progress_composition_request,
    validate_growth_goal_progress_composition_result,
)
from second_brain.application.ports import CancellationToken, VaultReader
from second_brain.application.simulate_me import (
    POLICY_FINGERPRINT as SIMULATE_ME_POLICY_FINGERPRINT,
)
from second_brain.application.simulate_me import (
    POLICY_ID as SIMULATE_ME_POLICY_ID,
)
from second_brain.application.simulate_me import (
    BuildSimulateMe,
    SimulateMeContextualEvidenceRef,
    SimulateMeError,
    SimulateMeErrorCode,
    SimulateMeEvidenceRef,
    SimulateMeOption,
    SimulateMeRequest,
    SimulateMeResult,
    SimulateMeResultKind,
    validate_simulate_me_request,
    validate_simulate_me_result,
)
from second_brain.domain.models import parse_rfc3339, parse_uuid7

CONTRACT_VERSION: Final[str] = "growth-compare-v1"
DERIVATION_VERSION: Final[str] = "compare-v2"
POLICY_ID: Final[str] = "growth-compare-decision-compass-v1"

DECISION_COMPASS_CONTRACT_VERSION: Final[str] = CONTRACT_VERSION
DECISION_COMPASS_DERIVATION_VERSION: Final[str] = DERIVATION_VERSION
DECISION_COMPASS_POLICY_ID: Final[str] = POLICY_ID
DECISION_COMPASS_ADVISOR_PROVENANCE: Final[str] = "growth-advisor-v1-explicit"

MAX_RESULT_BYTES: Final[int] = DEFAULT_MAX_RESULT_BYTES
MAX_OPTIONS: Final[int] = 8
MAX_CRITERIA: Final[int] = 8
MAX_TASK_BYTES: Final[int] = 4096
MAX_OPTION_ID_BYTES: Final[int] = 64
MAX_OPTION_LABEL_BYTES: Final[int] = 256
MAX_CRITERION_DESCRIPTION_BYTES: Final[int] = 512
MAX_CRITERIA_BYTES: Final[int] = 8192
MAX_CONSTRAINTS: Final[int] = 16
MAX_CONSTRAINT_BYTES: Final[int] = 512
MAX_CONSTRAINTS_BYTES: Final[int] = 8192
MAX_CONTEXT_ENTRIES: Final[int] = 16
MAX_CONTEXT_TEXT_BYTES: Final[int] = 1024
MAX_CONTEXT_TEXTS_BYTES: Final[int] = 16384
MAX_BEHAVIORAL_OPTION_INDEX: Final[int] = 19

POLICY_PAYLOAD: Final[dict[str, object]] = {
    "advisor": "growth-advisor-v1-explicit-optional",
    "behavior_scope": "one-explicit-current-cohort-or-not-selected-v1",
    "behavior_option_binding": "exact-request-option-to-stage10-option-or-unbound-v1",
    "bounds": "compare-v1-option-bounds-plus-bounded-criteria-v1",
    "causal_inference": "forbidden",
    "criteria": "explicit-non-scored-v1",
    "cross_branch_inference": "structural-exact-identity-only-v1",
    "goal": "exact-selected-goal-uuid-and-growth-identity-v1",
    "growth": "stage11-owner-reviewed-relation-v1",
    "progress": "stage12d-goal-level-context-explicit-as-of-v1",
    "provider": "base-provider-free-advisor-explicit-only-v1",
    "simulate_me": "simulate-me-v1-independent-prediction-v1",
    "winner": "forbidden",
    "write": "forbidden",
    "version": "1",
}
POLICY_CANONICAL_JSON: Final[str] = json.dumps(
    POLICY_PAYLOAD,
    ensure_ascii=False,
    sort_keys=True,
    separators=(",", ":"),
    allow_nan=False,
)
POLICY_FINGERPRINT: Final[str] = (
    "sha256:" + hashlib.sha256(POLICY_CANONICAL_JSON.encode("utf-8")).hexdigest()
)

_ID_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"[A-Za-z0-9][A-Za-z0-9._:-]{0,63}\Z",
    re.ASCII,
)
_HASH_PATTERN: Final[re.Pattern[str]] = re.compile(r"sha256:[0-9a-f]{64}\Z", re.ASCII)


def _utc_now() -> datetime:
    """Return an aware UTC timestamp for default lower-layer clocks."""

    return datetime.now(UTC)


class DecisionCompassErrorCodeV1(StrEnum):
    """Safe top-level Stage 13A error vocabulary."""

    INVALID_REQUEST = "DECISION_COMPASS_INVALID_REQUEST"
    GOAL_REQUIRED = "DECISION_COMPASS_GOAL_REQUIRED"
    GOAL_UNAVAILABLE = "DECISION_COMPASS_GOAL_UNAVAILABLE"
    GOAL_SOURCE_CHANGED = "DECISION_COMPASS_GOAL_SOURCE_CHANGED"
    BEHAVIORAL_SCOPE_INVALID = "DECISION_COMPASS_BEHAVIORAL_SCOPE_INVALID"
    OPTION_BINDING_INVALID = "DECISION_COMPASS_OPTION_BINDING_INVALID"
    SOURCE_UNAVAILABLE = "DECISION_COMPASS_SOURCE_UNAVAILABLE"
    SOURCE_CHANGED = "DECISION_COMPASS_SOURCE_CHANGED"
    POLICY_MISMATCH = "DECISION_COMPASS_POLICY_MISMATCH"
    RESULT_TOO_LARGE = "DECISION_COMPASS_RESULT_TOO_LARGE"
    CANCELLED = "DECISION_COMPASS_CANCELLED"
    TIMEOUT = "DECISION_COMPASS_TIMEOUT"
    INTERNAL = "DECISION_COMPASS_INTERNAL"


DecisionCompassErrorCode = DecisionCompassErrorCodeV1

_ERROR_MESSAGES: Final[dict[DecisionCompassErrorCodeV1, str]] = {
    DecisionCompassErrorCodeV1.INVALID_REQUEST: "Запрос Decision Compass некорректен.",
    DecisionCompassErrorCodeV1.GOAL_REQUIRED: "Требуется явная текущая Цель.",
    DecisionCompassErrorCodeV1.GOAL_UNAVAILABLE: "Выбранная Цель недоступна.",
    DecisionCompassErrorCodeV1.GOAL_SOURCE_CHANGED: "Источник выбранной Цели изменился.",
    DecisionCompassErrorCodeV1.BEHAVIORAL_SCOPE_INVALID: (
        "Выбранный поведенческий контекст некорректен."
    ),
    DecisionCompassErrorCodeV1.OPTION_BINDING_INVALID: (
        "Связка варианта и поведенческого контекста некорректна."
    ),
    DecisionCompassErrorCodeV1.SOURCE_UNAVAILABLE: "Источник Decision Compass недоступен.",
    DecisionCompassErrorCodeV1.SOURCE_CHANGED: "Источник изменился во время сборки.",
    DecisionCompassErrorCodeV1.POLICY_MISMATCH: "Политика Decision Compass не подтверждена.",
    DecisionCompassErrorCodeV1.RESULT_TOO_LARGE: (
        "Результат Decision Compass превышает допустимый размер."
    ),
    DecisionCompassErrorCodeV1.CANCELLED: "Сборка Decision Compass отменена.",
    DecisionCompassErrorCodeV1.TIMEOUT: "Сборка Decision Compass превысила срок.",
    DecisionCompassErrorCodeV1.INTERNAL: "Decision Compass временно недоступен.",
}


class DecisionCompassBranchErrorCodeV1(StrEnum):
    """Bounded branch-local error identifiers."""

    SIMULATE_ME_INVALID_REQUEST = "DECISION_COMPASS_SIMULATE_ME_INVALID_REQUEST"
    SIMULATE_ME_TIMEOUT = "DECISION_COMPASS_SIMULATE_ME_TIMEOUT"
    SIMULATE_ME_UNAVAILABLE = "DECISION_COMPASS_SIMULATE_ME_UNAVAILABLE"
    SIMULATE_ME_FAILURE = "DECISION_COMPASS_SIMULATE_ME_FAILURE"
    SIMULATE_ME_RESULT_INVALID = "DECISION_COMPASS_SIMULATE_ME_RESULT_INVALID"
    BEHAVIORAL_UNAVAILABLE = "DECISION_COMPASS_BEHAVIORAL_UNAVAILABLE"
    BEHAVIORAL_INVALID = "DECISION_COMPASS_BEHAVIORAL_INVALID"
    BEHAVIORAL_POLICY_MISMATCH = "DECISION_COMPASS_BEHAVIORAL_POLICY_MISMATCH"
    BEHAVIORAL_RESULT_TOO_LARGE = "DECISION_COMPASS_BEHAVIORAL_RESULT_TOO_LARGE"
    BEHAVIORAL_INTERNAL = "DECISION_COMPASS_BEHAVIORAL_INTERNAL"
    ADVISOR_INVALID_REQUEST = "DECISION_COMPASS_ADVISOR_INVALID_REQUEST"
    ADVISOR_GOAL_UNAVAILABLE = "DECISION_COMPASS_ADVISOR_GOAL_UNAVAILABLE"
    ADVISOR_GOAL_MISSING = "DECISION_COMPASS_ADVISOR_GOAL_MISSING"
    ADVISOR_GOAL_CHANGED = "DECISION_COMPASS_ADVISOR_GOAL_CHANGED"
    ADVISOR_GOAL_TEXT_UNSUPPORTED = "DECISION_COMPASS_ADVISOR_GOAL_TEXT_UNSUPPORTED"
    ADVISOR_GOAL_TEXT_TOO_LARGE = "DECISION_COMPASS_ADVISOR_GOAL_TEXT_TOO_LARGE"
    ADVISOR_CONTEXT_TOO_LARGE = "DECISION_COMPASS_ADVISOR_CONTEXT_TOO_LARGE"
    ADVISOR_RESULT_TOO_LARGE = "DECISION_COMPASS_ADVISOR_RESULT_TOO_LARGE"
    ADVISOR_UNAVAILABLE = "DECISION_COMPASS_ADVISOR_UNAVAILABLE"
    ADVISOR_CANCELLED = "DECISION_COMPASS_ADVISOR_CANCELLED"
    ADVISOR_TIMEOUT = "DECISION_COMPASS_ADVISOR_TIMEOUT"
    ADVISOR_FAILURE = "DECISION_COMPASS_ADVISOR_FAILURE"
    ADVISOR_RESULT_INVALID = "DECISION_COMPASS_ADVISOR_RESULT_INVALID"
    ADVISOR_POLICY_MISMATCH = "DECISION_COMPASS_ADVISOR_POLICY_MISMATCH"


_BRANCH_ERROR_MESSAGES: Final[dict[DecisionCompassBranchErrorCodeV1, str]] = {
    DecisionCompassBranchErrorCodeV1.SIMULATE_ME_INVALID_REQUEST: (
        "Ветка Simulate Me отклонила запрос."
    ),
    DecisionCompassBranchErrorCodeV1.SIMULATE_ME_TIMEOUT: "Ветка Simulate Me превысила срок.",
    DecisionCompassBranchErrorCodeV1.SIMULATE_ME_UNAVAILABLE: "Ветка Simulate Me недоступна.",
    DecisionCompassBranchErrorCodeV1.SIMULATE_ME_FAILURE: "Ветка Simulate Me завершилась ошибкой.",
    DecisionCompassBranchErrorCodeV1.SIMULATE_ME_RESULT_INVALID: (
        "Результат ветки Simulate Me некорректен."
    ),
    DecisionCompassBranchErrorCodeV1.BEHAVIORAL_UNAVAILABLE: (
        "Выбранный поведенческий источник недоступен."
    ),
    DecisionCompassBranchErrorCodeV1.BEHAVIORAL_INVALID: (
        "Результат поведенческой ветки некорректен."
    ),
    DecisionCompassBranchErrorCodeV1.BEHAVIORAL_POLICY_MISMATCH: (
        "Политика поведенческой ветки не подтверждена."
    ),
    DecisionCompassBranchErrorCodeV1.BEHAVIORAL_RESULT_TOO_LARGE: (
        "Результат поведенческой ветки превышает допустимый размер."
    ),
    DecisionCompassBranchErrorCodeV1.BEHAVIORAL_INTERNAL: (
        "Поведенческая ветка временно недоступна."
    ),
    DecisionCompassBranchErrorCodeV1.ADVISOR_INVALID_REQUEST: ("Запрос ветки Advisor некорректен."),
    DecisionCompassBranchErrorCodeV1.ADVISOR_GOAL_UNAVAILABLE: (
        "Источник Цели для ветки Advisor недоступен."
    ),
    DecisionCompassBranchErrorCodeV1.ADVISOR_GOAL_MISSING: (
        "Выбранная Цель для ветки Advisor не найдена."
    ),
    DecisionCompassBranchErrorCodeV1.ADVISOR_GOAL_CHANGED: (
        "Источник Цели для ветки Advisor изменился."
    ),
    DecisionCompassBranchErrorCodeV1.ADVISOR_GOAL_TEXT_UNSUPPORTED: (
        "Проекция Цели для ветки Advisor некорректна."
    ),
    DecisionCompassBranchErrorCodeV1.ADVISOR_GOAL_TEXT_TOO_LARGE: (
        "Проекция Цели для ветки Advisor слишком велика."
    ),
    DecisionCompassBranchErrorCodeV1.ADVISOR_CONTEXT_TOO_LARGE: (
        "Контекст ветки Advisor превышает допустимый размер."
    ),
    DecisionCompassBranchErrorCodeV1.ADVISOR_RESULT_TOO_LARGE: (
        "Результат ветки Advisor превышает допустимый размер."
    ),
    DecisionCompassBranchErrorCodeV1.ADVISOR_UNAVAILABLE: "Ветка Advisor недоступна.",
    DecisionCompassBranchErrorCodeV1.ADVISOR_CANCELLED: "Ветка Advisor отменена.",
    DecisionCompassBranchErrorCodeV1.ADVISOR_TIMEOUT: "Ветка Advisor превысила срок.",
    DecisionCompassBranchErrorCodeV1.ADVISOR_FAILURE: "Ветка Advisor завершилась ошибкой.",
    DecisionCompassBranchErrorCodeV1.ADVISOR_RESULT_INVALID: (
        "Результат ветки Advisor некорректен."
    ),
    DecisionCompassBranchErrorCodeV1.ADVISOR_POLICY_MISMATCH: (
        "Политика ветки Advisor не подтверждена."
    ),
}


class DecisionCompassBehavioralStateV1(StrEnum):
    """Closed Stage 13A Behavioral branch states."""

    NOT_SELECTED = "not_selected"
    RESULT = "result"
    INSUFFICIENT = "insufficient"
    NOT_COMPARABLE = "not_comparable"
    UNAVAILABLE = "unavailable"
    ERROR = "error"


DecisionCompassBehaviorStateV1 = DecisionCompassBehavioralStateV1
DecisionCompassBehavioralState = DecisionCompassBehavioralStateV1


class DecisionCompassAdvisorStateV1(StrEnum):
    """Transient explicit Growth Advisor branch states."""

    NOT_REQUESTED = "not_requested"
    RESULT = "result"
    ABSTENTION = "abstention"
    ERROR = "error"


class DecisionCompassStructuralRelationCodeV1(StrEnum):
    """Closed exact-identity relation vocabulary from the Stage 13 contract."""

    SIMULATE_ADVISOR_SAME_OPTION = "simulate_advisor_same_option"
    SIMULATE_ADVISOR_DIFFERENT_OPTIONS = "simulate_advisor_different_options"
    SIMULATE_ADVISOR_NOT_COMPARABLE = "simulate_advisor_not_comparable"
    SIMULATE_BEHAVIOR_SAME_OPTION = "simulate_behavior_same_option"
    SIMULATE_BEHAVIOR_DIFFERENT_OPTIONS = "simulate_behavior_different_options"
    SIMULATE_BEHAVIOR_NOT_COMPARABLE = "simulate_behavior_not_comparable"
    ADVISOR_BEHAVIOR_SAME_OPTION = "advisor_behavior_same_option"
    ADVISOR_BEHAVIOR_DIFFERENT_OPTIONS = "advisor_behavior_different_options"
    ADVISOR_BEHAVIOR_NOT_COMPARABLE = "advisor_behavior_not_comparable"
    BEHAVIOR_BINDING_MISSING = "behavior_binding_missing"
    BEHAVIOR_SCOPE_NOT_SELECTED = "behavior_scope_not_selected"
    ADVISOR_NOT_REQUESTED = "advisor_not_requested"


DecisionCompassStructuralRelationCode = DecisionCompassStructuralRelationCodeV1
DecisionCompassRelationCodeV1 = DecisionCompassStructuralRelationCodeV1


class DecisionCompassStructuralBranchV1(StrEnum):
    """Bounded branch labels carried by a structural relation."""

    SIMULATE_ME = "simulate_me"
    BEHAVIORAL = "behavioral"
    ADVISOR = "advisor"
    DECISION_COMPASS = "decision_compass"


class DecisionCompassCaveatV1(StrEnum):
    """Small deterministic Stage 13 caveat vocabulary."""

    ADVISOR_NOT_REQUESTED = "advisor_not_requested"
    BEHAVIORAL_SCOPE_NOT_SELECTED = "behavioral_scope_not_selected"
    BEHAVIORAL_BINDING_MISSING = "behavioral_binding_missing"
    BEHAVIORAL_BINDING_STALE = "behavioral_binding_stale"
    NO_HIDDEN_WINNER = "no_hidden_winner"
    NO_CAUSAL_CLAIM = "no_causal_claim"


DecisionCompassCaveat = DecisionCompassCaveatV1


class DecisionCompassValidationError(ValueError):
    """Base class for safe request/result validation failures."""

    code: DecisionCompassErrorCodeV1

    def __init__(self, code: DecisionCompassErrorCodeV1) -> None:
        self.code = code
        super().__init__(_ERROR_MESSAGES[code])


class DecisionCompassInvalidRequestError(DecisionCompassValidationError):
    """The caller-owned request is outside the exact bounded contract."""

    def __init__(self) -> None:
        super().__init__(DecisionCompassErrorCodeV1.INVALID_REQUEST)


class DecisionCompassGoalRequiredError(DecisionCompassValidationError):
    """The request did not carry one exact Goal selector."""

    def __init__(self) -> None:
        super().__init__(DecisionCompassErrorCodeV1.GOAL_REQUIRED)


class DecisionCompassGoalUnavailableError(DecisionCompassValidationError):
    """The exact selected Goal cannot be read from the current authority."""

    def __init__(self) -> None:
        super().__init__(DecisionCompassErrorCodeV1.GOAL_UNAVAILABLE)


class DecisionCompassGoalSourceChangedError(DecisionCompassValidationError):
    """The exact selected Goal identity changed or no longer binds."""

    def __init__(self) -> None:
        super().__init__(DecisionCompassErrorCodeV1.GOAL_SOURCE_CHANGED)


class DecisionCompassBehavioralScopeInvalidError(DecisionCompassValidationError):
    """The optional exact Behavioral cohort selector is malformed."""

    def __init__(self) -> None:
        super().__init__(DecisionCompassErrorCodeV1.BEHAVIORAL_SCOPE_INVALID)


class DecisionCompassOptionBindingInvalidError(DecisionCompassValidationError):
    """The request-local Behavioral option bridge is malformed."""

    def __init__(self) -> None:
        super().__init__(DecisionCompassErrorCodeV1.OPTION_BINDING_INVALID)


class DecisionCompassPolicyMismatchError(DecisionCompassValidationError):
    """A compiled or nested authoritative policy cannot be proven."""

    def __init__(self) -> None:
        super().__init__(DecisionCompassErrorCodeV1.POLICY_MISMATCH)


class DecisionCompassResultTooLargeError(DecisionCompassValidationError):
    """The complete result exceeds the requested or fixed outer cap."""

    def __init__(self) -> None:
        super().__init__(DecisionCompassErrorCodeV1.RESULT_TOO_LARGE)


class DecisionCompassCompositionInvalidError(DecisionCompassValidationError):
    """A typed branch or exact structural composition is malformed."""

    def __init__(self) -> None:
        super().__init__(DecisionCompassErrorCodeV1.INTERNAL)


class _DecisionCompassCancelled(RuntimeError):
    """Internal control signal; no partial result is returned."""


class _DecisionCompassTimeout(RuntimeError):
    """Internal control signal; no partial result is returned."""


@dataclass(frozen=True, slots=True)
class DecisionCompassErrorV1:
    """Bounded public top-level error projection."""

    code: DecisionCompassErrorCodeV1 | str
    message: str

    def __post_init__(self) -> None:
        code = _parse_enum(self.code, DecisionCompassErrorCodeV1)
        if self.message != _ERROR_MESSAGES[code]:
            raise ValueError("Decision Compass error message is invalid")
        object.__setattr__(self, "code", code)

    def as_dict(self) -> dict[str, str]:
        return {"code": cast(DecisionCompassErrorCodeV1, self.code).value, "message": self.message}


DecisionCompassError = DecisionCompassErrorV1


@dataclass(frozen=True, slots=True)
class DecisionCompassBranchErrorV1:
    """Bounded branch-local error projection without exception details."""

    code: DecisionCompassBranchErrorCodeV1 | str
    message: str

    def __post_init__(self) -> None:
        code = _parse_enum(self.code, DecisionCompassBranchErrorCodeV1)
        if self.message != _BRANCH_ERROR_MESSAGES[code]:
            raise ValueError("Decision Compass branch error message is invalid")
        object.__setattr__(self, "code", code)

    def as_dict(self) -> dict[str, str]:
        return {
            "code": cast(DecisionCompassBranchErrorCodeV1, self.code).value,
            "message": self.message,
        }


@dataclass(frozen=True, slots=True)
class DecisionCompassOptionV1:
    """Request-local exact option, compatible with Compare v1 semantics."""

    id: str
    label: str

    @classmethod
    def from_dict(cls, value: object) -> DecisionCompassOptionV1:
        if not isinstance(value, Mapping) or set(value) != {"id", "label"}:
            raise DecisionCompassInvalidRequestError()
        return cls(value["id"], value["label"])

    def as_dict(self) -> dict[str, str]:
        return {"id": self.id, "label": self.label}


DecisionCompassOption = DecisionCompassOptionV1


@dataclass(frozen=True, slots=True)
class DecisionCompassCriterionV1:
    """Request-local inert presentation/reference criterion."""

    id: str
    label: str
    description: str | None = None

    @classmethod
    def from_dict(cls, value: object) -> DecisionCompassCriterionV1:
        if not isinstance(value, Mapping) or set(value) != {"id", "label", "description"}:
            raise DecisionCompassInvalidRequestError()
        return cls(value["id"], value["label"], value["description"])

    def as_dict(self) -> dict[str, str | None]:
        return {"id": self.id, "label": self.label, "description": self.description}


DecisionCompassCriterion = DecisionCompassCriterionV1


@dataclass(frozen=True, slots=True)
class DecisionCompassGoalSelectorV1:
    """Exact Stage 11 Goal source UUID and Growth identity fingerprint."""

    source_uuid: UUID | str
    identity_fingerprint: str

    @classmethod
    def from_dict(cls, value: object) -> DecisionCompassGoalSelectorV1:
        if not isinstance(value, Mapping) or set(value) != {"source_uuid", "identity_fingerprint"}:
            raise DecisionCompassInvalidRequestError()
        return cls(value["source_uuid"], value["identity_fingerprint"])

    def as_dict(self) -> dict[str, str]:
        return {
            "source_uuid": str(self.source_uuid),
            "identity_fingerprint": self.identity_fingerprint,
        }


DecisionCompassGoalSelector = DecisionCompassGoalSelectorV1


@dataclass(frozen=True, slots=True)
class DecisionCompassBehaviorScopeV1:
    """One optional exact current Stage 10 cohort selector."""

    behavioral_cohort_fingerprint: str

    @classmethod
    def from_dict(cls, value: object) -> DecisionCompassBehaviorScopeV1:
        if not isinstance(value, Mapping) or set(value) != {"behavioral_cohort_fingerprint"}:
            raise DecisionCompassBehavioralScopeInvalidError()
        return cls(value["behavioral_cohort_fingerprint"])

    def as_dict(self) -> dict[str, str]:
        return {"behavioral_cohort_fingerprint": self.behavioral_cohort_fingerprint}


DecisionCompassBehaviorScope = DecisionCompassBehaviorScopeV1


@dataclass(frozen=True, slots=True)
class DecisionCompassBehaviorOptionBindingV1:
    """Ephemeral exact bridge from one request option to one Stage 10 option."""

    request_option_id: str
    behavioral_cohort_fingerprint: str
    behavioral_option_index: int
    behavioral_option_fingerprint: str

    @classmethod
    def from_dict(cls, value: object) -> DecisionCompassBehaviorOptionBindingV1:
        fields = {
            "request_option_id",
            "behavioral_cohort_fingerprint",
            "behavioral_option_index",
            "behavioral_option_fingerprint",
        }
        if not isinstance(value, Mapping) or set(value) != fields:
            raise DecisionCompassOptionBindingInvalidError()
        return cls(
            value["request_option_id"],
            value["behavioral_cohort_fingerprint"],
            value["behavioral_option_index"],
            value["behavioral_option_fingerprint"],
        )

    def as_dict(self) -> dict[str, object]:
        return {
            "request_option_id": self.request_option_id,
            "behavioral_cohort_fingerprint": self.behavioral_cohort_fingerprint,
            "behavioral_option_index": self.behavioral_option_index,
            "behavioral_option_fingerprint": self.behavioral_option_fingerprint,
        }


DecisionCompassBehaviorOptionBinding = DecisionCompassBehaviorOptionBindingV1
DecisionCompassOptionBinding = DecisionCompassBehaviorOptionBindingV1


@dataclass(frozen=True, slots=True)
class DecisionCompassRequestV1:
    """Immutable bounded provider-free Decision Compass request."""

    contract_version: str = CONTRACT_VERSION
    task: str = ""
    options: tuple[DecisionCompassOptionV1, ...] = ()
    selected_goal: DecisionCompassGoalSelectorV1 | None = None
    criteria: tuple[DecisionCompassCriterionV1, ...] = ()
    explicit_constraints: tuple[str, ...] = ()
    explicit_context: tuple[AssistantExplicitContext, ...] = ()
    progress_as_of: datetime | str | None = None
    behavioral_scope: DecisionCompassBehaviorScopeV1 | None = None
    behavioral_option_binding: DecisionCompassBehaviorOptionBindingV1 | None = None
    max_result_bytes: int = DEFAULT_MAX_RESULT_BYTES

    @classmethod
    def from_dict(cls, value: object) -> DecisionCompassRequestV1:
        """Parse the strict wire shape, with optional caller fields defaulted."""

        allowed = {
            "contract_version",
            "task",
            "options",
            "selected_goal",
            "criteria",
            "explicit_constraints",
            "explicit_context",
            "progress_as_of",
            "behavioral_scope",
            "behavioral_option_binding",
            "max_result_bytes",
        }
        required = {"task", "options", "selected_goal", "progress_as_of"}
        if (
            not isinstance(value, Mapping)
            or not set(value) <= allowed
            or not required <= set(value)
        ):
            raise DecisionCompassInvalidRequestError()
        try:
            raw_options = value["options"]
            if not isinstance(raw_options, (tuple, list)):
                raise DecisionCompassInvalidRequestError()
            options = tuple(
                item
                if type(item) is DecisionCompassOptionV1
                else DecisionCompassOptionV1.from_dict(item)
                for item in raw_options
            )

            raw_criteria = value.get("criteria", ())
            if not isinstance(raw_criteria, (tuple, list)):
                raise DecisionCompassInvalidRequestError()
            criteria = tuple(
                item
                if type(item) is DecisionCompassCriterionV1
                else DecisionCompassCriterionV1.from_dict(item)
                for item in raw_criteria
            )

            raw_constraints = value.get("explicit_constraints", ())
            if not isinstance(raw_constraints, (tuple, list)):
                raise DecisionCompassInvalidRequestError()
            constraints = tuple(raw_constraints)

            raw_context = value.get("explicit_context", ())
            if not isinstance(raw_context, (tuple, list)):
                raise DecisionCompassInvalidRequestError()
            context = tuple(_context_from_dict(item) for item in raw_context)

            selected_goal = value["selected_goal"]
            if (
                selected_goal is not None
                and type(selected_goal) is not DecisionCompassGoalSelectorV1
            ):
                selected_goal = DecisionCompassGoalSelectorV1.from_dict(selected_goal)

            scope = value.get("behavioral_scope")
            if scope is not None and type(scope) is not DecisionCompassBehaviorScopeV1:
                scope = DecisionCompassBehaviorScopeV1.from_dict(scope)

            binding = value.get("behavioral_option_binding")
            if binding is not None and type(binding) is not DecisionCompassBehaviorOptionBindingV1:
                binding = DecisionCompassBehaviorOptionBindingV1.from_dict(binding)
        except DecisionCompassValidationError:
            raise
        except KeyError, TypeError, ValueError:
            raise DecisionCompassInvalidRequestError() from None
        return cls(
            contract_version=value.get("contract_version", CONTRACT_VERSION),
            task=value["task"],
            options=options,
            selected_goal=selected_goal,
            criteria=criteria,
            explicit_constraints=constraints,
            explicit_context=context,
            progress_as_of=value["progress_as_of"],
            behavioral_scope=scope,
            behavioral_option_binding=binding,
            max_result_bytes=value.get("max_result_bytes", DEFAULT_MAX_RESULT_BYTES),
        )

    def as_dict(self) -> dict[str, object]:
        return {
            "contract_version": self.contract_version,
            "task": self.task,
            "options": [item.as_dict() for item in self.options],
            "selected_goal": self.selected_goal.as_dict() if self.selected_goal else None,
            "criteria": [item.as_dict() for item in self.criteria],
            "explicit_constraints": list(self.explicit_constraints),
            "explicit_context": [
                {"kind": str(item.kind), "text": item.text} for item in self.explicit_context
            ],
            "progress_as_of": _timestamp_projection(self.progress_as_of),
            "behavioral_scope": (
                self.behavioral_scope.as_dict() if self.behavioral_scope is not None else None
            ),
            "behavioral_option_binding": (
                self.behavioral_option_binding.as_dict()
                if self.behavioral_option_binding is not None
                else None
            ),
            "max_result_bytes": self.max_result_bytes,
        }


DecisionCompassRequest = DecisionCompassRequestV1
GrowthCompareRequestV1 = DecisionCompassRequestV1


@dataclass(frozen=True, slots=True)
class DecisionCompassBehaviorBranchV1:
    """Typed projection of one exact Stage 10 pattern or limitation."""

    state: DecisionCompassBehavioralStateV1 | str
    pattern: BehavioralPatternV1 | None = None
    error: DecisionCompassBranchErrorV1 | None = None

    def __post_init__(self) -> None:
        state = _parse_enum(self.state, DecisionCompassBehavioralStateV1)
        if self.pattern is not None and type(self.pattern) is not BehavioralPatternV1:
            raise ValueError("Decision Compass Behavioral pattern is invalid")
        if self.error is not None and type(self.error) is not DecisionCompassBranchErrorV1:
            raise ValueError("Decision Compass Behavioral error is invalid")
        if state is DecisionCompassBehavioralStateV1.ERROR:
            if self.pattern is not None or self.error is None:
                raise ValueError("Decision Compass Behavioral error state is invalid")
        elif self.error is not None:
            raise ValueError("Decision Compass Behavioral branch error is invalid")
        if state is DecisionCompassBehavioralStateV1.NOT_SELECTED and self.pattern is not None:
            raise ValueError("Decision Compass Behavioral not-selected state is invalid")
        if state is DecisionCompassBehavioralStateV1.UNAVAILABLE and self.pattern is not None:
            raise ValueError("Decision Compass Behavioral unavailable state is invalid")
        object.__setattr__(self, "state", state)

    @property
    def result(self) -> BehavioralPatternV1 | None:
        """Compatibility spelling for the nested typed Stage 10 pattern."""

        return self.pattern

    def as_dict(self) -> dict[str, object]:
        return {
            "state": cast(DecisionCompassBehavioralStateV1, self.state).value,
            "pattern": self.pattern.as_dict() if self.pattern is not None else None,
            "error": self.error.as_dict() if self.error is not None else None,
        }


DecisionCompassBehavioralBranchV1 = DecisionCompassBehaviorBranchV1
DecisionCompassBehaviorBranch = DecisionCompassBehaviorBranchV1


@dataclass(frozen=True, slots=True)
class DecisionCompassAdvisorBranchV1:
    """Transient wrapper retaining the existing Growth Advisor branch exactly."""

    state: DecisionCompassAdvisorStateV1 | str = DecisionCompassAdvisorStateV1.NOT_REQUESTED
    result: object | None = None
    error: DecisionCompassBranchErrorV1 | None = None

    def __post_init__(self) -> None:
        state = _parse_enum(self.state, DecisionCompassAdvisorStateV1)
        if self.error is not None and type(self.error) is not DecisionCompassBranchErrorV1:
            raise ValueError("Decision Compass Advisor error is invalid")
        if state is DecisionCompassAdvisorStateV1.NOT_REQUESTED and (
            self.result is not None or self.error is not None
        ):
            raise ValueError("Decision Compass Advisor not-requested state is invalid")
        if state in {
            DecisionCompassAdvisorStateV1.RESULT,
            DecisionCompassAdvisorStateV1.ABSTENTION,
        } and (self.result is None or self.error is not None):
            raise ValueError("Decision Compass Advisor result branch is invalid")
        if state is DecisionCompassAdvisorStateV1.ERROR and (
            self.result is not None or self.error is None
        ):
            raise ValueError("Decision Compass Advisor error branch is invalid")
        object.__setattr__(self, "state", state)

    def as_dict(self) -> dict[str, object]:
        result = self.result
        if result is not None:
            serializer = getattr(result, "as_dict", None)
            if callable(serializer):
                result = cast(Callable[[], object], serializer)()
            elif not isinstance(result, Mapping):
                raise DecisionCompassCompositionInvalidError()
        return {
            "state": cast(DecisionCompassAdvisorStateV1, self.state).value,
            "result": result,
            "error": self.error.as_dict() if self.error is not None else None,
        }

    @property
    def growth_advisor_branch(self) -> GrowthAdvisorBranchV1 | None:
        """Return the retained existing Growth Advisor branch, when present."""

        return self.result if type(self.result) is GrowthAdvisorBranchV1 else None


DecisionCompassAdvisorBranch = DecisionCompassAdvisorBranchV1


@dataclass(frozen=True, slots=True)
class DecisionCompassStructuralRelationV1:
    """Strict mechanical relation; it contains no explanation or interpretation."""

    code: DecisionCompassStructuralRelationCodeV1 | str
    left_branch: DecisionCompassStructuralBranchV1 | str
    right_branch: DecisionCompassStructuralBranchV1 | str
    left_state: str
    right_state: str
    left_option_id: str | None = None
    right_option_id: str | None = None

    def __post_init__(self) -> None:
        code = _parse_enum(self.code, DecisionCompassStructuralRelationCodeV1)
        left_branch = _parse_enum(self.left_branch, DecisionCompassStructuralBranchV1)
        right_branch = _parse_enum(self.right_branch, DecisionCompassStructuralBranchV1)
        if type(self.left_state) is not str or type(self.right_state) is not str:
            raise ValueError("Decision Compass relation state is invalid")
        for option_id in (self.left_option_id, self.right_option_id):
            if option_id is not None and (
                type(option_id) is not str or _ID_PATTERN.fullmatch(option_id) is None
            ):
                raise ValueError("Decision Compass relation option ID is invalid")
        _validate_relation_shape(
            code,
            left_branch,
            right_branch,
            self.left_state,
            self.right_state,
            self.left_option_id,
            self.right_option_id,
        )
        object.__setattr__(self, "code", code)
        object.__setattr__(self, "left_branch", left_branch)
        object.__setattr__(self, "right_branch", right_branch)

    def as_dict(self) -> dict[str, object]:
        return {
            "code": cast(DecisionCompassStructuralRelationCodeV1, self.code).value,
            "left_branch": cast(DecisionCompassStructuralBranchV1, self.left_branch).value,
            "right_branch": cast(DecisionCompassStructuralBranchV1, self.right_branch).value,
            "left_state": self.left_state,
            "right_state": self.right_state,
            "left_option_id": self.left_option_id,
            "right_option_id": self.right_option_id,
        }


DecisionCompassStructuralRelation = DecisionCompassStructuralRelationV1
GrowthCompareStructuralRelationV1 = DecisionCompassStructuralRelationV1


@dataclass(frozen=True, slots=True)
class DecisionCompassProvenanceV1:
    """Bounded source/policy identities and explicit no-side-effect facts."""

    selected_goal_source_uuid: UUID | str
    selected_goal_identity_fingerprint: str
    decision_compass_policy_fingerprint: str
    simulate_me_policy_id: str
    simulate_me_policy_fingerprint: str
    behavioral_policy_fingerprint: str | None
    growth_progress_policy_fingerprint: str
    progress_as_of: datetime | str
    provider: str = "none"
    network: str = "none"
    write: str = "none"
    persistence: str = "none"
    advisor: str = "not_requested"

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "selected_goal_source_uuid", _parse_uuid(self.selected_goal_source_uuid)
        )
        object.__setattr__(
            self,
            "selected_goal_identity_fingerprint",
            _parse_hash(self.selected_goal_identity_fingerprint),
        )
        object.__setattr__(
            self,
            "decision_compass_policy_fingerprint",
            _parse_hash(self.decision_compass_policy_fingerprint),
        )
        if self.simulate_me_policy_id != SIMULATE_ME_POLICY_ID:
            raise ValueError("Decision Compass Simulate Me provenance is invalid")
        if self.simulate_me_policy_fingerprint != SIMULATE_ME_POLICY_FINGERPRINT:
            raise ValueError("Decision Compass Simulate Me provenance is invalid")
        if self.behavioral_policy_fingerprint is not None:
            _parse_hash(self.behavioral_policy_fingerprint)
        if self.growth_progress_policy_fingerprint != COMPOSITION_POLICY_FINGERPRINT:
            raise ValueError("Decision Compass Growth Progress provenance is invalid")
        object.__setattr__(self, "progress_as_of", _parse_exact_utc(self.progress_as_of))
        if (
            self.provider != "none"
            or self.network != "none"
            or self.write != "none"
            or self.persistence != "none"
            or self.advisor not in {"not_requested", DECISION_COMPASS_ADVISOR_PROVENANCE}
        ):
            raise ValueError("Decision Compass side-effect provenance is invalid")

    def as_dict(self) -> dict[str, object]:
        return {
            "selected_goal_source_uuid": str(self.selected_goal_source_uuid),
            "selected_goal_identity_fingerprint": self.selected_goal_identity_fingerprint,
            "decision_compass_policy_fingerprint": self.decision_compass_policy_fingerprint,
            "simulate_me_policy_id": self.simulate_me_policy_id,
            "simulate_me_policy_fingerprint": self.simulate_me_policy_fingerprint,
            "behavioral_policy_fingerprint": self.behavioral_policy_fingerprint,
            "growth_progress_policy_fingerprint": self.growth_progress_policy_fingerprint,
            "progress_as_of": _format_timestamp(cast(datetime, self.progress_as_of)),
            "provider": self.provider,
            "network": self.network,
            "write": self.write,
            "persistence": self.persistence,
            "advisor": self.advisor,
        }


DecisionCompassProvenance = DecisionCompassProvenanceV1
GrowthCompareProvenanceV1 = DecisionCompassProvenanceV1


@dataclass(frozen=True, slots=True)
class DecisionCompassResultV1:
    """Complete immutable transient Stage 13A composition."""

    contract_version: str
    derivation_version: str
    policy_id: str
    policy_fingerprint: str
    selected_goal: DecisionCompassGoalSelectorV1
    request: DecisionCompassRequestV1
    simulate_me: CompareSimulateMeBranchV1
    behavioral: DecisionCompassBehaviorBranchV1
    growth_progress: GrowthGoalProgressCompositionResultV1
    advisor: DecisionCompassAdvisorBranchV1
    structural_relations: tuple[DecisionCompassStructuralRelationV1, ...]
    caveats: tuple[DecisionCompassCaveatV1 | str, ...]
    provenance: DecisionCompassProvenanceV1

    def as_dict(self) -> dict[str, object]:
        return _result_payload(self)

    def to_json(self) -> str:
        return serialize_decision_compass_result(self).decode("utf-8")


GrowthCompareResultV1 = DecisionCompassResultV1
DecisionCompassResult = DecisionCompassResultV1


class DecisionCompassGrowthProgressBuilder(Protocol):
    """Existing Stage 12D execution seam."""

    def execute(
        self,
        request: GrowthGoalProgressCompositionRequestV1,
    ) -> GrowthGoalProgressCompositionResultV1:
        """Build one exact Goal/Progress composition."""


class DecisionCompassBehavioralBuilder(Protocol):
    """Existing Stage 10B read-model seam."""

    def execute(
        self, request: BehavioralSelfModelRequest = DEFAULT_BEHAVIORAL_SELF_MODEL_REQUEST
    ) -> BehavioralSelfModelResultV1:
        """Build one current bounded Behavioral result."""


class DecisionCompassGoalContextBuilder(Protocol):
    """Narrow Stage 11A current Goal revalidation seam."""

    def execute(self, request: GrowthEngineRequestV1) -> object:
        """Read one exact current Goal context."""


class DecisionCompassGrowthAdvisorBuilder(Protocol):
    """Existing explicit Growth Advisor preview/execute boundary."""

    def preview(
        self,
        request: object,
        *,
        cancellation: CancellationToken | None = None,
    ) -> GrowthAdvisorGoalPreviewV1:
        """Rebuild one exact current Goal without invoking a provider."""

    def execute(
        self,
        request: object,
        preview: object,
        *,
        confirmed: bool = True,
        cancellation: CancellationToken | None = None,
    ) -> GrowthAdvisorBranchV1:
        """Run one explicitly confirmed Growth Advisor operation."""


DecisionCompassSimulateMePort = CompareSimulateMePort


class _SynchronousSimulateMeAdapter:
    """Adapt the existing provider-free builder to the Compare execution seam."""

    def __init__(self, service: BuildSimulateMe, clock: Callable[[], float]) -> None:
        self._service = service
        self._clock = clock

    def execute(
        self,
        request: SimulateMeRequest,
        *,
        execution: CompareExecutionContextV1,
    ) -> SimulateMeResult:
        if execution.cancellation.is_cancelled() or self._clock() >= execution.deadline:
            raise TimeoutError
        result = self._service.execute(request)
        if execution.cancellation.is_cancelled() or self._clock() >= execution.deadline:
            raise TimeoutError
        return result


def validate_decision_compass_policy() -> str:
    """Recompute the exact policy fingerprint before exposing it."""

    canonical = json.dumps(
        POLICY_PAYLOAD,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    fingerprint = "sha256:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    if canonical != POLICY_CANONICAL_JSON or fingerprint != POLICY_FINGERPRINT:
        raise DecisionCompassPolicyMismatchError()
    return fingerprint


validate_growth_compare_policy = validate_decision_compass_policy


def validate_decision_compass_request(value: object) -> DecisionCompassRequestV1:
    """Validate and normalize every caller-owned field before source reads."""

    if type(value) is not DecisionCompassRequestV1:
        raise DecisionCompassInvalidRequestError()
    request = value
    if type(request.contract_version) is not str or request.contract_version != CONTRACT_VERSION:
        raise DecisionCompassInvalidRequestError()

    selector = request.selected_goal
    if selector is None:
        raise DecisionCompassGoalRequiredError()
    if type(selector) is not DecisionCompassGoalSelectorV1:
        raise DecisionCompassInvalidRequestError()
    try:
        goal_uuid = _parse_uuid(selector.source_uuid)
        goal_fingerprint = _parse_hash(selector.identity_fingerprint)
    except TypeError, ValueError, OverflowError:
        raise DecisionCompassInvalidRequestError() from None

    if type(request.max_result_bytes) is not int or not (
        MIN_MAX_RESULT_BYTES_V1 <= request.max_result_bytes <= MAX_RESULT_BYTES
    ):
        raise DecisionCompassInvalidRequestError()

    if type(request.options) is not tuple:
        raise DecisionCompassInvalidRequestError()
    compare_options = tuple(
        CompareOptionV1(option.id, option.label)
        for option in request.options
        if type(option) is DecisionCompassOptionV1
    )
    if len(compare_options) != len(request.options):
        raise DecisionCompassInvalidRequestError()
    try:
        normalized_compare = validate_compare_request(
            CompareRequestV1(
                task=request.task,
                options=compare_options,
                assistant=CompareAssistantInputsV1(
                    explicit_constraints=request.explicit_constraints,
                    explicit_goals=(),
                    explicit_context=request.explicit_context,
                    max_context_bytes=DEFAULT_ASSISTANT_MAX_CONTEXT_BYTES,
                    max_result_bytes=DEFAULT_ASSISTANT_MAX_RESULT_BYTES,
                ),
                max_result_bytes=request.max_result_bytes,
            )
        )
    except CompareInvalidRequestError, TypeError, ValueError, UnicodeError:
        raise DecisionCompassInvalidRequestError() from None

    normalized_options = tuple(
        DecisionCompassOptionV1(option.id, normalized_compare.options[index].label)
        for index, option in enumerate(request.options)
    )

    try:
        if type(request.criteria) is not tuple or len(request.criteria) > MAX_CRITERIA:
            raise ValueError
        normalized_criteria = tuple(_normalize_criterion(item) for item in request.criteria)
        if len({item.id for item in normalized_criteria}) != len(normalized_criteria):
            raise ValueError
        criteria_bytes = sum(
            len(item.id.encode("utf-8"))
            + len(item.label.encode("utf-8"))
            + (len(item.description.encode("utf-8")) if item.description is not None else 0)
            for item in normalized_criteria
        )
        if criteria_bytes > MAX_CRITERIA_BYTES:
            raise ValueError
    except TypeError, ValueError, UnicodeError, OverflowError:
        raise DecisionCompassInvalidRequestError() from None

    try:
        progress_request = validate_growth_goal_progress_composition_request(
            GrowthGoalProgressCompositionRequestV1(goal_uuid, request.progress_as_of)
        )
    except GrowthGoalProgressCompositionError, TypeError, ValueError, OverflowError:
        raise DecisionCompassInvalidRequestError() from None

    scope = request.behavioral_scope
    if scope is not None:
        if type(scope) is not DecisionCompassBehaviorScopeV1:
            raise DecisionCompassBehavioralScopeInvalidError()
        try:
            scope = DecisionCompassBehaviorScopeV1(_parse_hash(scope.behavioral_cohort_fingerprint))
        except TypeError, ValueError, OverflowError:
            raise DecisionCompassBehavioralScopeInvalidError() from None

    binding = request.behavioral_option_binding
    if binding is not None:
        if type(binding) is not DecisionCompassBehaviorOptionBindingV1:
            raise DecisionCompassOptionBindingInvalidError()
        try:
            binding = DecisionCompassBehaviorOptionBindingV1(
                request_option_id=binding.request_option_id,
                behavioral_cohort_fingerprint=_parse_hash(binding.behavioral_cohort_fingerprint),
                behavioral_option_index=binding.behavioral_option_index,
                behavioral_option_fingerprint=_parse_hash(binding.behavioral_option_fingerprint),
            )
            if _ID_PATTERN.fullmatch(binding.request_option_id) is None:
                raise ValueError
            if not 0 <= binding.behavioral_option_index <= MAX_BEHAVIORAL_OPTION_INDEX:
                raise ValueError
            if binding.request_option_id not in {item.id for item in normalized_options}:
                raise ValueError
            if scope is None or (
                binding.behavioral_cohort_fingerprint != scope.behavioral_cohort_fingerprint
            ):
                raise ValueError
        except TypeError, ValueError, OverflowError:
            raise DecisionCompassOptionBindingInvalidError() from None

    normalized_selector = DecisionCompassGoalSelectorV1(goal_uuid, goal_fingerprint)
    normalized = DecisionCompassRequestV1(
        contract_version=CONTRACT_VERSION,
        task=normalized_compare.task,
        options=normalized_options,
        selected_goal=normalized_selector,
        criteria=normalized_criteria,
        explicit_constraints=normalized_compare.assistant.explicit_constraints,
        explicit_context=normalized_compare.assistant.explicit_context,
        progress_as_of=progress_request.progress_as_of,
        behavioral_scope=scope,
        behavioral_option_binding=binding,
        max_result_bytes=request.max_result_bytes,
    )
    return normalized


validate_growth_compare_request = validate_decision_compass_request


def validate_decision_compass_result(
    value: object,
    *,
    request: DecisionCompassRequestV1 | None = None,
) -> DecisionCompassResultV1:
    """Validate nested typed branches, exact relations and the outer byte cap."""

    if type(value) is not DecisionCompassResultV1:
        raise DecisionCompassCompositionInvalidError()
    result = value
    try:
        validate_decision_compass_policy()
        if (
            result.contract_version != CONTRACT_VERSION
            or result.derivation_version != DERIVATION_VERSION
            or result.policy_id != POLICY_ID
            or result.policy_fingerprint != POLICY_FINGERPRINT
        ):
            raise DecisionCompassPolicyMismatchError()
        normalized_request = validate_decision_compass_request(
            result.request if request is None else request
        )
        selector = cast(DecisionCompassGoalSelectorV1, normalized_request.selected_goal)
        if result.request != normalized_request:
            raise DecisionCompassCompositionInvalidError()
        if type(result.selected_goal) is not DecisionCompassGoalSelectorV1:
            raise DecisionCompassCompositionInvalidError()
        if result.selected_goal != normalized_request.selected_goal:
            raise DecisionCompassCompositionInvalidError()
        simulate_request = _simulate_request(normalized_request)
        simulate_branch = _validate_simulate_branch(result.simulate_me, simulate_request)
        behavioral_branch = _validate_behavioral_branch(result.behavioral, normalized_request)
        if type(result.growth_progress) is not GrowthGoalProgressCompositionResultV1:
            raise DecisionCompassCompositionInvalidError()
        validate_growth_goal_progress_composition_result(result.growth_progress)
        if (
            result.growth_progress.selected_goal_source_uuid != selector.source_uuid
            or result.growth_progress.current_goal_identity_fingerprint
            != selector.identity_fingerprint
            or result.growth_progress.policy_fingerprint != COMPOSITION_POLICY_FINGERPRINT
        ):
            raise DecisionCompassGoalSourceChangedError()
        if type(result.advisor) is not DecisionCompassAdvisorBranchV1:
            raise DecisionCompassCompositionInvalidError()
        advisor_branch = _validate_advisor_branch(result.advisor, normalized_request)
        if type(result.structural_relations) is not tuple:
            raise DecisionCompassCompositionInvalidError()
        relations = tuple(_validate_relation(item) for item in result.structural_relations)
        expected_relations = _build_structural_relations(
            simulate_branch, behavioral_branch, normalized_request, advisor_branch
        )
        if relations != expected_relations:
            raise DecisionCompassCompositionInvalidError()
        if type(result.caveats) is not tuple:
            raise DecisionCompassCompositionInvalidError()
        caveats = _normalize_caveats(result.caveats)
        expected_caveats = _build_caveats(normalized_request, behavioral_branch, advisor_branch)
        if caveats != expected_caveats:
            raise DecisionCompassCompositionInvalidError()
        if type(result.provenance) is not DecisionCompassProvenanceV1:
            raise DecisionCompassCompositionInvalidError()
        _validate_provenance(
            result.provenance,
            normalized_request,
            result.growth_progress,
            behavioral_branch,
            advisor_branch,
        )
        normalized = DecisionCompassResultV1(
            contract_version=CONTRACT_VERSION,
            derivation_version=DERIVATION_VERSION,
            policy_id=POLICY_ID,
            policy_fingerprint=POLICY_FINGERPRINT,
            selected_goal=selector,
            request=normalized_request,
            simulate_me=simulate_branch,
            behavioral=behavioral_branch,
            growth_progress=result.growth_progress,
            advisor=advisor_branch,
            structural_relations=relations,
            caveats=caveats,
            provenance=result.provenance,
        )
        encoded = _canonical_result_bytes(normalized)
        if len(encoded) > normalized_request.max_result_bytes or len(encoded) > MAX_RESULT_BYTES:
            raise DecisionCompassResultTooLargeError()
        return normalized
    except (
        DecisionCompassValidationError,
        DecisionCompassCompositionInvalidError,
        GrowthGoalProgressCompositionError,
        SimulateMeError,
        ValueError,
        TypeError,
        UnicodeError,
        OverflowError,
    ):
        raise


validate_growth_compare_result = validate_decision_compass_result


def canonical_decision_compass_json(value: object) -> str:
    """Encode a JSON-compatible payload with the repository canonical profile."""

    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except TypeError, ValueError, OverflowError:
        raise DecisionCompassCompositionInvalidError() from None


def decision_compass_hash_json(value: object) -> str:
    return (
        "sha256:"
        + hashlib.sha256(canonical_decision_compass_json(value).encode("utf-8")).hexdigest()
    )


def serialize_decision_compass_result(
    value: object,
    *,
    request: DecisionCompassRequestV1 | None = None,
) -> bytes:
    """Serialize one validated result without truncation."""

    normalized = validate_decision_compass_result(value, request=request)
    return _canonical_result_bytes(normalized)


canonical_decision_compass_result_bytes = serialize_decision_compass_result
serialize_growth_compare_result = serialize_decision_compass_result


class BuildDecisionCompass:
    """Execute one bounded provider-free Stage 13A composition."""

    def __init__(
        self,
        reader: VaultReader | None = None,
        store: GrowthMappingStore | None = None,
        *,
        growth_progress: DecisionCompassGrowthProgressBuilder | None = None,
        growth_goal_progress: DecisionCompassGrowthProgressBuilder | None = None,
        behavioral: DecisionCompassBehavioralBuilder | None = None,
        behavioral_self_model: DecisionCompassBehavioralBuilder | None = None,
        simulate_me: CompareSimulateMePort | BuildSimulateMe | None = None,
        goal_context: DecisionCompassGoalContextBuilder | None = None,
        current_goal: DecisionCompassGoalContextBuilder | None = None,
        growth_advisor: DecisionCompassGrowthAdvisorBuilder | None = None,
        policy: object | None = None,
        growth_clock: Callable[[], datetime] | None = None,
        behavioral_clock: Callable[[], datetime] | None = None,
        goal_clock: Callable[[], datetime] | None = None,
        monotonic_clock: Callable[[], float] | None = None,
    ) -> None:
        """Create the additive boundary using existing lower-layer builders."""

        if growth_progress is not None and growth_goal_progress is not None:
            raise ValueError("only one Growth Progress builder may be supplied")
        if behavioral is not None and behavioral_self_model is not None:
            raise ValueError("only one Behavioral builder may be supplied")
        if goal_context is not None and current_goal is not None:
            raise ValueError("only one Goal context builder may be supplied")
        self._monotonic_clock = monotonic_clock or time.monotonic
        if not callable(self._monotonic_clock):
            raise ValueError("monotonic clock is invalid")
        self._growth_clock = growth_clock or _utc_now
        self._behavioral_clock = behavioral_clock or self._growth_clock
        self._goal_clock = goal_clock or self._growth_clock
        if (
            not callable(self._growth_clock)
            or not callable(self._behavioral_clock)
            or not callable(self._goal_clock)
        ):
            raise ValueError("Decision Compass clock is invalid")
        self._policy = policy

        self._growth_progress = growth_progress or growth_goal_progress
        if self._growth_progress is None and reader is not None and store is not None:
            self._growth_progress = BuildGrowthGoalProgressCompositionV1(
                reader=reader,
                store=store,
                growth_clock=self._growth_clock,
            )

        supplied_behavioral = behavioral or behavioral_self_model
        if supplied_behavioral is None and reader is not None:
            supplied_behavioral = cast(
                DecisionCompassBehavioralBuilder,
                BuildBehavioralSelfModel(
                    reader=reader,
                    clock=self._behavioral_clock,
                ),
            )
        self._behavioral: DecisionCompassBehavioralBuilder | None = supplied_behavioral

        supplied_goal = goal_context or current_goal
        if supplied_goal is None and reader is not None:
            supplied_goal = BuildGrowthGoalContext(reader=reader, clock=self._goal_clock)
        self._goal_context = supplied_goal
        if growth_advisor is not None and not (
            callable(getattr(growth_advisor, "preview", None))
            and callable(getattr(growth_advisor, "execute", None))
        ):
            raise ValueError("Growth Advisor builder is invalid")
        self._growth_advisor = growth_advisor

        if isinstance(simulate_me, BuildSimulateMe):
            self._simulate_me: CompareSimulateMePort | None = _SynchronousSimulateMeAdapter(
                simulate_me,
                self._monotonic_clock,
            )
        elif simulate_me is not None:
            self._simulate_me = simulate_me
        elif reader is not None:
            self._simulate_me = _SynchronousSimulateMeAdapter(
                BuildSimulateMe(reader=reader, clock=self._behavioral_clock),
                self._monotonic_clock,
            )
        else:
            self._simulate_me = None

    def execute(
        self,
        request: DecisionCompassRequestV1,
        *,
        execution: CompareExecutionContextV1,
    ) -> DecisionCompassResultV1 | DecisionCompassErrorV1:
        """Validate, compose and return either a complete result or safe error."""

        control_error = self._control_error(execution)
        if control_error is not None:
            return control_error
        try:
            normalized_request = validate_decision_compass_request(request)
        except DecisionCompassValidationError as error:
            return _top_error(error.code)
        except TypeError, ValueError, UnicodeError, OverflowError:
            return _top_error(DecisionCompassErrorCodeV1.INVALID_REQUEST)
        selector = cast(DecisionCompassGoalSelectorV1, normalized_request.selected_goal)
        progress_as_of = cast(datetime, normalized_request.progress_as_of)

        control_error = self._control_error(execution)
        if control_error is not None:
            return control_error
        try:
            validate_decision_compass_policy()
        except DecisionCompassValidationError as error:
            return _top_error(error.code)
        if self._policy is not None:
            try:
                if canonical_decision_compass_json(self._policy) != POLICY_CANONICAL_JSON:
                    return _top_error(DecisionCompassErrorCodeV1.POLICY_MISMATCH)
            except TypeError, ValueError, OverflowError:
                return _top_error(DecisionCompassErrorCodeV1.POLICY_MISMATCH)

        if self._growth_progress is None:
            return _top_error(DecisionCompassErrorCodeV1.SOURCE_UNAVAILABLE)
        try:
            growth_progress = self._growth_progress.execute(
                GrowthGoalProgressCompositionRequestV1(
                    selector.source_uuid,
                    progress_as_of,
                )
            )
            validate_growth_goal_progress_composition_result(growth_progress)
        except GrowthGoalProgressCompositionError as error:
            return _top_error(_map_growth_progress_error(error))
        except GrowthError as error:
            return _top_error(_map_growth_error(error))
        except TypeError, ValueError, UnicodeError, OverflowError:
            return _top_error(DecisionCompassErrorCodeV1.INTERNAL)
        except Exception:
            return _top_error(DecisionCompassErrorCodeV1.INTERNAL)

        control_error = self._control_error(execution)
        if control_error is not None:
            return control_error
        if not _growth_progress_matches_request(growth_progress, normalized_request):
            return _top_error(DecisionCompassErrorCodeV1.GOAL_SOURCE_CHANGED)

        control_error = self._control_error(execution)
        if control_error is not None:
            return control_error
        simulate_branch = self._run_simulate_me(normalized_request, execution)
        control_error = self._control_error(execution)
        if control_error is not None:
            return control_error

        if normalized_request.behavioral_scope is None:
            behavioral_branch = DecisionCompassBehaviorBranchV1(
                DecisionCompassBehavioralStateV1.NOT_SELECTED
            )
        else:
            control_error = self._control_error(execution)
            if control_error is not None:
                return control_error
            # A final exact read is authoritative for the transient output and
            # prevents a stale first pattern from being returned as current.
            behavioral_branch = self._run_behavioral(normalized_request)
            control_error = self._control_error(execution)
            if control_error is not None:
                return control_error

        control_error = self._control_error(execution)
        if control_error is not None:
            return control_error
        goal_error = self._revalidate_goal(normalized_request, growth_progress)
        if goal_error is not None:
            return goal_error
        control_error = self._control_error(execution)
        if control_error is not None:
            return control_error

        try:
            relations = _build_structural_relations(
                simulate_branch,
                behavioral_branch,
                normalized_request,
            )
            caveats = _build_caveats(normalized_request, behavioral_branch)
            result = DecisionCompassResultV1(
                contract_version=CONTRACT_VERSION,
                derivation_version=DERIVATION_VERSION,
                policy_id=POLICY_ID,
                policy_fingerprint=validate_decision_compass_policy(),
                selected_goal=selector,
                request=normalized_request,
                simulate_me=simulate_branch,
                behavioral=behavioral_branch,
                growth_progress=growth_progress,
                advisor=DecisionCompassAdvisorBranchV1(),
                structural_relations=relations,
                caveats=caveats,
                provenance=DecisionCompassProvenanceV1(
                    selected_goal_source_uuid=selector.source_uuid,
                    selected_goal_identity_fingerprint=(selector.identity_fingerprint),
                    decision_compass_policy_fingerprint=POLICY_FINGERPRINT,
                    simulate_me_policy_id=SIMULATE_ME_POLICY_ID,
                    simulate_me_policy_fingerprint=SIMULATE_ME_POLICY_FINGERPRINT,
                    behavioral_policy_fingerprint=(
                        behavioral_branch.pattern.policy_fingerprint
                        if behavioral_branch.pattern is not None
                        else None
                    ),
                    growth_progress_policy_fingerprint=growth_progress.policy_fingerprint,
                    progress_as_of=progress_as_of,
                ),
            )
            normalized_result = validate_decision_compass_result(result)
            _canonical_result_bytes(normalized_result)
        except DecisionCompassResultTooLargeError:
            return _top_error(DecisionCompassErrorCodeV1.RESULT_TOO_LARGE)
        except DecisionCompassPolicyMismatchError:
            return _top_error(DecisionCompassErrorCodeV1.POLICY_MISMATCH)
        except DecisionCompassGoalSourceChangedError:
            return _top_error(DecisionCompassErrorCodeV1.GOAL_SOURCE_CHANGED)
        except DecisionCompassValidationError, GrowthGoalProgressCompositionError:
            return _top_error(DecisionCompassErrorCodeV1.INTERNAL)
        except TypeError, ValueError, UnicodeError, OverflowError:
            return _top_error(DecisionCompassErrorCodeV1.INTERNAL)
        except Exception:
            return _top_error(DecisionCompassErrorCodeV1.INTERNAL)

        control_error = self._control_error(execution)
        if control_error is not None:
            return control_error
        return normalized_result

    build = execute
    compose = execute

    def build_growth_advisor_request(
        self,
        request: object,
    ) -> GrowthAdvisorRequestV1:
        """Project only the Compass request fields allowed by Growth Advisor."""

        return build_growth_advisor_request(request)

    def preview_advisor(
        self,
        request: object,
        *,
        cancellation: CancellationToken | None = None,
        growth_advisor: DecisionCompassGrowthAdvisorBuilder | None = None,
    ) -> GrowthAdvisorGoalPreviewV1:
        """Run the existing owner-facing Growth Advisor preview only."""

        builder = growth_advisor or self._growth_advisor
        if builder is None:
            raise ValueError("Growth Advisor builder is unavailable")
        advisor_request = self.build_growth_advisor_request(request)
        return builder.preview(advisor_request, cancellation=cancellation)

    def execute_advisor(
        self,
        request: object,
        base_result: object,
        preview: object,
        *,
        execution: CompareExecutionContextV1,
        confirmed: bool = True,
        growth_advisor: DecisionCompassGrowthAdvisorBuilder | None = None,
    ) -> DecisionCompassResultV1 | DecisionCompassErrorV1:
        """Attach one explicit Growth Advisor result to a valid base result.

        The base result is never rebuilt here.  The supplied preview and
        confirmation are passed through the existing Growth Advisor boundary;
        all other Compass branches remain the already validated transient
        siblings.
        """

        control_error = self._control_error(execution)
        if control_error is not None:
            return control_error
        try:
            normalized_request = validate_decision_compass_request(request)
        except DecisionCompassValidationError as error:
            return _top_error(error.code)
        except TypeError, ValueError, UnicodeError, OverflowError:
            return _top_error(DecisionCompassErrorCodeV1.INVALID_REQUEST)

        try:
            normalized_base = validate_decision_compass_result(
                base_result,
                request=normalized_request,
            )
            if normalized_base.advisor.state is not DecisionCompassAdvisorStateV1.NOT_REQUESTED:
                return _top_error(DecisionCompassErrorCodeV1.INVALID_REQUEST)
        except DecisionCompassGoalSourceChangedError:
            return _top_error(DecisionCompassErrorCodeV1.GOAL_SOURCE_CHANGED)
        except DecisionCompassValidationError as error:
            return _top_error(error.code)
        except TypeError, ValueError, UnicodeError, OverflowError:
            return _top_error(DecisionCompassErrorCodeV1.INVALID_REQUEST)

        builder = growth_advisor or self._growth_advisor
        if builder is None:
            return self._compose_advisor_branch(
                normalized_base,
                normalized_request,
                _advisor_error_branch(DecisionCompassBranchErrorCodeV1.ADVISOR_UNAVAILABLE),
            )

        try:
            advisor_request = self.build_growth_advisor_request(normalized_request)
        except DecisionCompassValidationError:
            return self._compose_advisor_branch(
                normalized_base,
                normalized_request,
                _advisor_error_branch(DecisionCompassBranchErrorCodeV1.ADVISOR_INVALID_REQUEST),
            )
        except TypeError, ValueError, UnicodeError, OverflowError:
            return self._compose_advisor_branch(
                normalized_base,
                normalized_request,
                _advisor_error_branch(DecisionCompassBranchErrorCodeV1.ADVISOR_INVALID_REQUEST),
            )

        goal_error = self._revalidate_goal(
            normalized_request,
            normalized_base.growth_progress,
        )
        if goal_error is not None:
            return goal_error
        control_error = self._control_error(execution)
        if control_error is not None:
            return control_error

        try:
            growth_branch = builder.execute(
                advisor_request,
                preview,
                confirmed=confirmed,
                cancellation=execution.cancellation,
            )
            advisor_branch = _advisor_branch_from_growth(growth_branch, normalized_request)
        except DecisionCompassGoalSourceChangedError:
            return _top_error(DecisionCompassErrorCodeV1.GOAL_SOURCE_CHANGED)
        except DecisionCompassCompositionInvalidError:
            advisor_branch = _advisor_error_branch(
                DecisionCompassBranchErrorCodeV1.ADVISOR_RESULT_INVALID
            )
        except GrowthAdvisorError as error:
            if _is_growth_goal_error(error):
                return _top_error(_map_growth_goal_top_error(error))
            advisor_branch = _advisor_error_branch(_map_growth_advisor_error(error.code))
        except TypeError, ValueError, UnicodeError, OverflowError:
            advisor_branch = _advisor_error_branch(
                DecisionCompassBranchErrorCodeV1.ADVISOR_RESULT_INVALID
            )
        except Exception:
            advisor_branch = _advisor_error_branch(DecisionCompassBranchErrorCodeV1.ADVISOR_FAILURE)

        control_error = self._control_error(execution)
        if control_error is not None:
            return control_error
        goal_error = self._revalidate_goal(
            normalized_request,
            normalized_base.growth_progress,
        )
        if goal_error is not None:
            return goal_error
        return self._compose_advisor_branch(
            normalized_base,
            normalized_request,
            advisor_branch,
        )

    execute_with_advisor = execute_advisor
    handoff_advisor = execute_advisor

    def _compose_advisor_branch(
        self,
        base_result: DecisionCompassResultV1,
        request: DecisionCompassRequestV1,
        advisor: DecisionCompassAdvisorBranchV1,
    ) -> DecisionCompassResultV1 | DecisionCompassErrorV1:
        """Compose and validate a transient branch without changing siblings."""

        try:
            provenance = replace(
                base_result.provenance,
                advisor=(
                    "not_requested"
                    if advisor.state is DecisionCompassAdvisorStateV1.NOT_REQUESTED
                    else DECISION_COMPASS_ADVISOR_PROVENANCE
                ),
            )
            result = replace(
                base_result,
                request=request,
                selected_goal=cast(DecisionCompassGoalSelectorV1, request.selected_goal),
                advisor=advisor,
                structural_relations=_build_structural_relations(
                    base_result.simulate_me,
                    base_result.behavioral,
                    request,
                    advisor,
                ),
                caveats=_build_caveats(request, base_result.behavioral, advisor),
                provenance=provenance,
            )
            normalized = validate_decision_compass_result(result)
            _canonical_result_bytes(normalized)
            return normalized
        except DecisionCompassResultTooLargeError:
            return _top_error(DecisionCompassErrorCodeV1.RESULT_TOO_LARGE)
        except DecisionCompassPolicyMismatchError:
            return _top_error(DecisionCompassErrorCodeV1.POLICY_MISMATCH)
        except DecisionCompassGoalSourceChangedError:
            return _top_error(DecisionCompassErrorCodeV1.GOAL_SOURCE_CHANGED)
        except DecisionCompassValidationError, GrowthGoalProgressCompositionError:
            return _top_error(DecisionCompassErrorCodeV1.INTERNAL)
        except TypeError, ValueError, UnicodeError, OverflowError:
            return _top_error(DecisionCompassErrorCodeV1.INTERNAL)
        except Exception:
            return _top_error(DecisionCompassErrorCodeV1.INTERNAL)

    def _control_error(
        self,
        execution: CompareExecutionContextV1,
    ) -> DecisionCompassErrorV1 | None:
        if type(execution) is not CompareExecutionContextV1:
            return _top_error(DecisionCompassErrorCodeV1.INVALID_REQUEST)
        try:
            if execution.cancellation.is_cancelled():
                return _top_error(DecisionCompassErrorCodeV1.CANCELLED)
            if self._monotonic_clock() >= execution.deadline:
                return _top_error(DecisionCompassErrorCodeV1.TIMEOUT)
        except Exception:
            return _top_error(DecisionCompassErrorCodeV1.INTERNAL)
        return None

    def _run_simulate_me(
        self,
        request: DecisionCompassRequestV1,
        execution: CompareExecutionContextV1,
    ) -> CompareSimulateMeBranchV1:
        if self._simulate_me is None:
            return _simulate_error_branch(DecisionCompassBranchErrorCodeV1.SIMULATE_ME_UNAVAILABLE)
        simulate_request = _simulate_request(request)
        try:
            raw_result = self._simulate_me.execute(simulate_request, execution=execution)
        except SimulateMeError as error:
            return _simulate_error_branch(_map_simulate_error(error))
        except TimeoutError:
            return _simulate_error_branch(DecisionCompassBranchErrorCodeV1.SIMULATE_ME_TIMEOUT)
        except Exception:
            return _simulate_error_branch(DecisionCompassBranchErrorCodeV1.SIMULATE_ME_FAILURE)
        try:
            result = validate_simulate_me_result(raw_result, request=simulate_request)
            _validate_simulate_me_temporal_correspondence(result)
        except SimulateMeError:
            return _simulate_error_branch(
                DecisionCompassBranchErrorCodeV1.SIMULATE_ME_RESULT_INVALID
            )
        except Exception:
            return _simulate_error_branch(
                DecisionCompassBranchErrorCodeV1.SIMULATE_ME_RESULT_INVALID
            )
        if result.kind is SimulateMeResultKind.ABSTENTION:
            return _simulate_abstention_branch(result)
        return _simulate_result_branch(result)

    def _run_behavioral(self, request: DecisionCompassRequestV1) -> DecisionCompassBehaviorBranchV1:
        if self._behavioral is None:
            return DecisionCompassBehaviorBranchV1(DecisionCompassBehavioralStateV1.UNAVAILABLE)
        try:
            raw_result = self._behavioral.execute(DEFAULT_BEHAVIORAL_SELF_MODEL_REQUEST)
            result = validate_behavioral_self_model_result(raw_result)
        except BehavioralSelfModelError as error:
            return _behavioral_error_branch(_map_behavioral_error(error))
        except TypeError, ValueError, UnicodeError, OverflowError:
            return _behavioral_error_branch(DecisionCompassBranchErrorCodeV1.BEHAVIORAL_INVALID)
        except Exception:
            return _behavioral_error_branch(DecisionCompassBranchErrorCodeV1.BEHAVIORAL_INTERNAL)

        scope = cast(DecisionCompassBehaviorScopeV1, request.behavioral_scope)
        matches = tuple(
            pattern
            for pattern in result.patterns
            if pattern.cohort is not None
            and pattern.cohort.cohort_fingerprint == scope.behavioral_cohort_fingerprint
        )
        if len(matches) != 1:
            return DecisionCompassBehaviorBranchV1(DecisionCompassBehavioralStateV1.UNAVAILABLE)
        pattern = matches[0]
        if pattern.pattern_type is BehavioralPatternTypeV1.INSUFFICIENT_EVIDENCE:
            return DecisionCompassBehaviorBranchV1(
                DecisionCompassBehavioralStateV1.INSUFFICIENT,
                pattern=pattern,
            )
        if pattern.pattern_type is BehavioralPatternTypeV1.NOT_COMPARABLE:
            return DecisionCompassBehaviorBranchV1(
                DecisionCompassBehavioralStateV1.NOT_COMPARABLE,
                pattern=pattern,
            )
        binding = request.behavioral_option_binding
        if binding is not None:
            selected = pattern.selected_option
            if selected is not None and (
                selected.option_index != binding.behavioral_option_index
                or selected.option_fingerprint != binding.behavioral_option_fingerprint
            ):
                return DecisionCompassBehaviorBranchV1(
                    DecisionCompassBehavioralStateV1.NOT_COMPARABLE,
                    pattern=pattern,
                )
        return DecisionCompassBehaviorBranchV1(
            DecisionCompassBehavioralStateV1.RESULT,
            pattern=pattern,
        )

    def _revalidate_goal(
        self,
        request: DecisionCompassRequestV1,
        growth_progress: GrowthGoalProgressCompositionResultV1,
    ) -> DecisionCompassErrorV1 | None:
        if self._goal_context is None:
            # A final narrow Goal read is part of the Stage 13A trust boundary;
            # without it an injected composition result cannot be trusted.
            return _top_error(DecisionCompassErrorCodeV1.SOURCE_UNAVAILABLE)
        selector = cast(DecisionCompassGoalSelectorV1, request.selected_goal)
        goal_uuid = cast(UUID, selector.source_uuid)
        try:
            context = self._goal_context.execute(
                GrowthEngineRequestV1(
                    selection=GrowthGoalSelectionV1(
                        GrowthGoalSelectionModeV1.SELECTED_GOAL,
                        goal_uuid,
                    )
                )
            )
            validate_growth_goal_context(context)
            if not hasattr(context, "goals") or len(context.goals) != 1:
                return _top_error(DecisionCompassErrorCodeV1.GOAL_UNAVAILABLE)
            current_goal = context.goals[0]
            if type(current_goal) is not GrowthGoalIdentityV1:
                return _top_error(DecisionCompassErrorCodeV1.GOAL_UNAVAILABLE)
            current_fingerprint = growth_hash_json(current_goal.as_dict())
            if (
                current_goal.source_note_uuid != selector.source_uuid
                or current_fingerprint != selector.identity_fingerprint
                or growth_progress.current_goal_identity_fingerprint != current_fingerprint
            ):
                return _top_error(DecisionCompassErrorCodeV1.GOAL_SOURCE_CHANGED)
        except GrowthGoalMissingError:
            return _top_error(DecisionCompassErrorCodeV1.GOAL_UNAVAILABLE)
        except GrowthGoalSourceChangedError:
            return _top_error(DecisionCompassErrorCodeV1.GOAL_SOURCE_CHANGED)
        except GrowthPolicyMismatchError:
            return _top_error(DecisionCompassErrorCodeV1.POLICY_MISMATCH)
        except GrowthError:
            return _top_error(DecisionCompassErrorCodeV1.GOAL_UNAVAILABLE)
        except TypeError, ValueError, UnicodeError, OverflowError:
            return _top_error(DecisionCompassErrorCodeV1.SOURCE_CHANGED)
        except Exception:
            return _top_error(DecisionCompassErrorCodeV1.GOAL_UNAVAILABLE)
        return None


BuildDecisionCompassV1 = BuildDecisionCompass
DecisionCompassGateway = BuildDecisionCompass
DecisionCompassExecutorV1 = BuildDecisionCompass
BuildGrowthCompare = BuildDecisionCompass


def build_decision_compass(
    reader: VaultReader | None,
    store: GrowthMappingStore | None,
    request: DecisionCompassRequestV1,
    *,
    execution: CompareExecutionContextV1,
    growth_progress: DecisionCompassGrowthProgressBuilder | None = None,
    behavioral: DecisionCompassBehavioralBuilder | None = None,
    simulate_me: CompareSimulateMePort | BuildSimulateMe | None = None,
    goal_context: DecisionCompassGoalContextBuilder | None = None,
    growth_advisor: DecisionCompassGrowthAdvisorBuilder | None = None,
    growth_clock: Callable[[], datetime] | None = None,
    behavioral_clock: Callable[[], datetime] | None = None,
    goal_clock: Callable[[], datetime] | None = None,
    monotonic_clock: Callable[[], float] | None = None,
    policy: object | None = None,
) -> DecisionCompassResultV1 | DecisionCompassErrorV1:
    """Functional Stage 13A entry point over existing lower-layer sources."""

    return BuildDecisionCompass(
        reader=reader,
        store=store,
        growth_progress=growth_progress,
        behavioral=behavioral,
        simulate_me=simulate_me,
        goal_context=goal_context,
        growth_advisor=growth_advisor,
        growth_clock=growth_clock,
        behavioral_clock=behavioral_clock,
        goal_clock=goal_clock,
        monotonic_clock=monotonic_clock,
        policy=policy,
    ).execute(request, execution=execution)


build_growth_compare = build_decision_compass


def build_growth_advisor_request(request: object) -> GrowthAdvisorRequestV1:
    """Build the existing Growth Advisor request without Compass context union."""

    normalized = validate_decision_compass_request(request)
    selector = cast(DecisionCompassGoalSelectorV1, normalized.selected_goal)
    try:
        return GrowthAdvisorRequestV1(
            contract_version=GROWTH_ADVISOR_CONTRACT_VERSION,
            goal_source_uuid=selector.source_uuid,
            goal_identity_fingerprint=selector.identity_fingerprint,
            task=normalized.task,
            options=tuple(AssistantOption(item.id, item.label) for item in normalized.options),
            explicit_constraints=normalized.explicit_constraints,
            explicit_context=normalized.explicit_context,
            max_context_bytes=DEFAULT_ASSISTANT_MAX_CONTEXT_BYTES,
            max_result_bytes=min(
                DEFAULT_ASSISTANT_MAX_RESULT_BYTES,
                normalized.max_result_bytes,
            ),
        )
    except TypeError, ValueError, UnicodeError, OverflowError:
        raise DecisionCompassInvalidRequestError() from None


build_decision_compass_advisor_request = build_growth_advisor_request


def build_decision_compass_advisor(
    request: object,
    base_result: object,
    preview: object,
    *,
    execution: CompareExecutionContextV1,
    growth_advisor: DecisionCompassGrowthAdvisorBuilder,
    reader: VaultReader | None = None,
    store: GrowthMappingStore | None = None,
    growth_progress: DecisionCompassGrowthProgressBuilder | None = None,
    behavioral: DecisionCompassBehavioralBuilder | None = None,
    simulate_me: CompareSimulateMePort | BuildSimulateMe | None = None,
    goal_context: DecisionCompassGoalContextBuilder | None = None,
    growth_clock: Callable[[], datetime] | None = None,
    behavioral_clock: Callable[[], datetime] | None = None,
    goal_clock: Callable[[], datetime] | None = None,
    monotonic_clock: Callable[[], float] | None = None,
    policy: object | None = None,
    confirmed: bool = True,
) -> DecisionCompassResultV1 | DecisionCompassErrorV1:
    """Function form of the explicit, transient Growth Advisor handoff."""

    return BuildDecisionCompass(
        reader=reader,
        store=store,
        growth_progress=growth_progress,
        behavioral=behavioral,
        simulate_me=simulate_me,
        goal_context=goal_context,
        growth_advisor=growth_advisor,
        growth_clock=growth_clock,
        behavioral_clock=behavioral_clock,
        goal_clock=goal_clock,
        monotonic_clock=monotonic_clock,
        policy=policy,
    ).execute_advisor(
        request,
        base_result,
        preview,
        execution=execution,
        confirmed=confirmed,
    )


build_growth_compare_advisor = build_decision_compass_advisor


def _context_from_dict(value: object) -> AssistantExplicitContext:
    if type(value) is AssistantExplicitContext:
        return value
    if not isinstance(value, Mapping) or set(value) != {"kind", "text"}:
        raise DecisionCompassInvalidRequestError()
    return AssistantExplicitContext(value["kind"], value["text"])


def _normalize_criterion(value: object) -> DecisionCompassCriterionV1:
    if type(value) is not DecisionCompassCriterionV1:
        raise ValueError
    criterion = value
    if type(criterion.id) is not str or _ID_PATTERN.fullmatch(criterion.id) is None:
        raise ValueError
    if criterion.description is not None and type(criterion.description) is not str:
        raise ValueError
    try:
        normalized = validate_compare_request(
            CompareRequestV1(
                task="criterion",
                options=(CompareOptionV1(criterion.id, criterion.label),),
                assistant=CompareAssistantInputsV1(
                    explicit_constraints=(criterion.description,)
                    if criterion.description is not None
                    else (),
                    explicit_goals=(),
                    explicit_context=(),
                    max_context_bytes=DEFAULT_ASSISTANT_MAX_CONTEXT_BYTES,
                    max_result_bytes=DEFAULT_ASSISTANT_MAX_RESULT_BYTES,
                ),
                max_result_bytes=DEFAULT_MAX_RESULT_BYTES,
            )
        )
    except CompareInvalidRequestError, TypeError, ValueError, UnicodeError:
        raise ValueError from None
    description = (
        normalized.assistant.explicit_constraints[0] if criterion.description is not None else None
    )
    if (
        description is not None
        and len(description.encode("utf-8")) > MAX_CRITERION_DESCRIPTION_BYTES
    ):
        raise ValueError
    return DecisionCompassCriterionV1(
        normalized.options[0].id, normalized.options[0].label, description
    )


def _simulate_request(request: DecisionCompassRequestV1) -> SimulateMeRequest:
    result = SimulateMeRequest(
        query=request.task,
        options=tuple(SimulateMeOption(item.id, item.label) for item in request.options),
    )
    try:
        return validate_simulate_me_request(result)
    except SimulateMeError:
        raise DecisionCompassCompositionInvalidError() from None


def _simulate_result_branch(result: SimulateMeResult) -> CompareSimulateMeResultBranchV1:
    return CompareSimulateMeResultBranchV1(
        state=CompareBranchStateV1.RESULT,
        result=result,
    )


def _simulate_abstention_branch(
    result: SimulateMeResult,
) -> CompareSimulateMeAbstentionBranchV1:
    return CompareSimulateMeAbstentionBranchV1(
        state=CompareBranchStateV1.ABSTENTION,
        result=result,
    )


def _simulate_error_branch(
    code: DecisionCompassBranchErrorCodeV1,
) -> CompareSimulateMeErrorBranchV1:
    compare_code = {
        DecisionCompassBranchErrorCodeV1.SIMULATE_ME_INVALID_REQUEST: (
            CompareBranchErrorCodeV1.INVALID_REQUEST
        ),
        DecisionCompassBranchErrorCodeV1.SIMULATE_ME_TIMEOUT: (CompareBranchErrorCodeV1.TIMEOUT),
        DecisionCompassBranchErrorCodeV1.SIMULATE_ME_UNAVAILABLE: (
            CompareBranchErrorCodeV1.UNAVAILABLE
        ),
        DecisionCompassBranchErrorCodeV1.SIMULATE_ME_FAILURE: CompareBranchErrorCodeV1.FAILURE,
        DecisionCompassBranchErrorCodeV1.SIMULATE_ME_RESULT_INVALID: (
            CompareBranchErrorCodeV1.RESULT_INVALID
        ),
    }[code]
    messages = {
        CompareBranchErrorCodeV1.INVALID_REQUEST: "compare branch request failed validation",
        CompareBranchErrorCodeV1.CANCELLED: "compare branch operation cancelled",
        CompareBranchErrorCodeV1.TIMEOUT: "compare branch operation timed out",
        CompareBranchErrorCodeV1.UNAVAILABLE: "compare branch unavailable",
        CompareBranchErrorCodeV1.FAILURE: "compare branch failed",
        CompareBranchErrorCodeV1.MALFORMED_RESULT: "compare branch result is malformed",
        CompareBranchErrorCodeV1.RESULT_TOO_LARGE: "compare branch result exceeds byte budget",
        CompareBranchErrorCodeV1.RESULT_INVALID: "compare branch result failed validation",
    }
    return CompareSimulateMeErrorBranchV1(
        state=CompareBranchStateV1.ERROR,
        error=CompareBranchErrorV1(compare_code, messages[compare_code]),
    )


def _behavioral_error_branch(
    code: DecisionCompassBranchErrorCodeV1,
) -> DecisionCompassBehaviorBranchV1:
    return DecisionCompassBehaviorBranchV1(
        DecisionCompassBehavioralStateV1.ERROR,
        error=DecisionCompassBranchErrorV1(code, _BRANCH_ERROR_MESSAGES[code]),
    )


def _map_simulate_error(error: SimulateMeError) -> DecisionCompassBranchErrorCodeV1:
    code = str(error.code)
    return {
        SimulateMeErrorCode.INVALID_REQUEST.value: (
            DecisionCompassBranchErrorCodeV1.SIMULATE_ME_INVALID_REQUEST
        ),
        SimulateMeErrorCode.RESULT_INVALID.value: (
            DecisionCompassBranchErrorCodeV1.SIMULATE_ME_RESULT_INVALID
        ),
    }.get(code, DecisionCompassBranchErrorCodeV1.SIMULATE_ME_FAILURE)


def _map_behavioral_error(error: BehavioralSelfModelError) -> DecisionCompassBranchErrorCodeV1:
    code = str(error.code)
    if code == BehavioralSelfModelErrorCode.SOURCE_UNAVAILABLE.value:
        return DecisionCompassBranchErrorCodeV1.BEHAVIORAL_UNAVAILABLE
    if code == BehavioralSelfModelErrorCode.POLICY_MISMATCH.value:
        return DecisionCompassBranchErrorCodeV1.BEHAVIORAL_POLICY_MISMATCH
    if code == BehavioralSelfModelErrorCode.RESULT_TOO_LARGE.value:
        return DecisionCompassBranchErrorCodeV1.BEHAVIORAL_RESULT_TOO_LARGE
    return DecisionCompassBranchErrorCodeV1.BEHAVIORAL_INVALID


def _growth_progress_matches_request(
    result: GrowthGoalProgressCompositionResultV1,
    request: DecisionCompassRequestV1,
) -> bool:
    selector = cast(DecisionCompassGoalSelectorV1, request.selected_goal)
    return (
        result.selected_goal_source_uuid == selector.source_uuid
        and result.current_goal_identity_fingerprint == selector.identity_fingerprint
        and result.policy_fingerprint == COMPOSITION_POLICY_FINGERPRINT
    )


def _selected_simulate_id(branch: CompareSimulateMeBranchV1) -> str | None:
    if type(branch) is CompareSimulateMeResultBranchV1:
        selected = branch.result.selected_option
        return None if selected is None else selected.id
    if type(branch) is CompareSimulateMeAbstentionBranchV1:
        selected = branch.result.selected_option
        return None if selected is None else selected.id
    return None


def _behavior_selected_id(
    branch: DecisionCompassBehaviorBranchV1,
    request: DecisionCompassRequestV1,
) -> str | None:
    binding = request.behavioral_option_binding
    if (
        binding is None
        or branch.state is not DecisionCompassBehavioralStateV1.RESULT
        or branch.pattern is None
        or branch.pattern.selected_option is None
        or branch.pattern.selected_option.option_index != binding.behavioral_option_index
        or branch.pattern.selected_option.option_fingerprint
        != binding.behavioral_option_fingerprint
    ):
        return None
    return binding.request_option_id


def _advisor_error_branch(
    code: DecisionCompassBranchErrorCodeV1,
) -> DecisionCompassAdvisorBranchV1:
    """Create a fixed safe branch-local Advisor error."""

    return DecisionCompassAdvisorBranchV1(
        state=DecisionCompassAdvisorStateV1.ERROR,
        error=DecisionCompassBranchErrorV1(code, _BRANCH_ERROR_MESSAGES[code]),
    )


def _map_growth_advisor_error(
    code: GrowthAdvisorErrorCode | str,
) -> DecisionCompassBranchErrorCodeV1:
    """Map existing Growth Advisor failures to Compass' fixed safe vocabulary."""

    value = code.value if isinstance(code, GrowthAdvisorErrorCode) else str(code)
    return {
        GrowthAdvisorErrorCode.INVALID_REQUEST.value: (
            DecisionCompassBranchErrorCodeV1.ADVISOR_INVALID_REQUEST
        ),
        GrowthAdvisorErrorCode.GOAL_UNAVAILABLE.value: (
            DecisionCompassBranchErrorCodeV1.ADVISOR_GOAL_UNAVAILABLE
        ),
        GrowthAdvisorErrorCode.GOAL_MISSING.value: (
            DecisionCompassBranchErrorCodeV1.ADVISOR_GOAL_MISSING
        ),
        GrowthAdvisorErrorCode.GOAL_CHANGED.value: (
            DecisionCompassBranchErrorCodeV1.ADVISOR_GOAL_CHANGED
        ),
        GrowthAdvisorErrorCode.GOAL_TEXT_UNSUPPORTED.value: (
            DecisionCompassBranchErrorCodeV1.ADVISOR_GOAL_TEXT_UNSUPPORTED
        ),
        GrowthAdvisorErrorCode.GOAL_TEXT_TOO_LARGE.value: (
            DecisionCompassBranchErrorCodeV1.ADVISOR_GOAL_TEXT_TOO_LARGE
        ),
        GrowthAdvisorErrorCode.CONTEXT_TOO_LARGE.value: (
            DecisionCompassBranchErrorCodeV1.ADVISOR_CONTEXT_TOO_LARGE
        ),
        GrowthAdvisorErrorCode.RESULT_TOO_LARGE.value: (
            DecisionCompassBranchErrorCodeV1.ADVISOR_RESULT_TOO_LARGE
        ),
        GrowthAdvisorErrorCode.RECOMMENDATION_UNAVAILABLE.value: (
            DecisionCompassBranchErrorCodeV1.ADVISOR_UNAVAILABLE
        ),
        GrowthAdvisorErrorCode.CANCELLED.value: DecisionCompassBranchErrorCodeV1.ADVISOR_CANCELLED,
        GrowthAdvisorErrorCode.TIMEOUT.value: DecisionCompassBranchErrorCodeV1.ADVISOR_TIMEOUT,
        GrowthAdvisorErrorCode.FAILURE.value: DecisionCompassBranchErrorCodeV1.ADVISOR_FAILURE,
        GrowthAdvisorErrorCode.INVALID_RESULT.value: (
            DecisionCompassBranchErrorCodeV1.ADVISOR_RESULT_INVALID
        ),
        GrowthAdvisorErrorCode.POLICY_MISMATCH.value: (
            DecisionCompassBranchErrorCodeV1.ADVISOR_POLICY_MISMATCH
        ),
    }.get(value, DecisionCompassBranchErrorCodeV1.ADVISOR_FAILURE)


def _is_growth_goal_error(error: GrowthAdvisorError) -> bool:
    return str(error.code) in {
        GrowthAdvisorErrorCode.GOAL_MISSING.value,
        GrowthAdvisorErrorCode.GOAL_UNAVAILABLE.value,
        GrowthAdvisorErrorCode.GOAL_CHANGED.value,
    }


def _map_growth_goal_top_error(error: GrowthAdvisorError) -> DecisionCompassErrorCodeV1:
    if str(error.code) == GrowthAdvisorErrorCode.GOAL_CHANGED.value:
        return DecisionCompassErrorCodeV1.GOAL_SOURCE_CHANGED
    return DecisionCompassErrorCodeV1.GOAL_UNAVAILABLE


def _advisor_branch_from_growth(
    value: object,
    request: DecisionCompassRequestV1,
) -> DecisionCompassAdvisorBranchV1:
    """Normalize the existing Growth Advisor branch into the Compass wrapper."""

    if type(value) is not GrowthAdvisorBranchV1:
        return _advisor_error_branch(DecisionCompassBranchErrorCodeV1.ADVISOR_RESULT_INVALID)
    growth_branch = validate_growth_advisor_branch(value)
    if growth_branch.state is GrowthAdvisorBranchStateV1.ERROR:
        if growth_branch.error is None:
            return _advisor_error_branch(DecisionCompassBranchErrorCodeV1.ADVISOR_RESULT_INVALID)
        code = _map_growth_advisor_error(growth_branch.error.code)
        if code in {
            DecisionCompassBranchErrorCodeV1.ADVISOR_GOAL_CHANGED,
            DecisionCompassBranchErrorCodeV1.ADVISOR_GOAL_MISSING,
            DecisionCompassBranchErrorCodeV1.ADVISOR_GOAL_UNAVAILABLE,
        }:
            # The caller handles these as top-level Goal/source drift when the
            # existing Growth Advisor returns its own bounded error branch.
            raise GrowthAdvisorError(
                {
                    DecisionCompassBranchErrorCodeV1.ADVISOR_GOAL_CHANGED: (
                        GrowthAdvisorErrorCode.GOAL_CHANGED
                    ),
                    DecisionCompassBranchErrorCodeV1.ADVISOR_GOAL_MISSING: (
                        GrowthAdvisorErrorCode.GOAL_MISSING
                    ),
                    DecisionCompassBranchErrorCodeV1.ADVISOR_GOAL_UNAVAILABLE: (
                        GrowthAdvisorErrorCode.GOAL_UNAVAILABLE
                    ),
                }[code]
            )
        return _advisor_error_branch(code)
    state = (
        DecisionCompassAdvisorStateV1.ABSTENTION
        if growth_branch.state is GrowthAdvisorBranchStateV1.ABSTENTION
        else DecisionCompassAdvisorStateV1.RESULT
    )
    return _validate_advisor_branch(
        DecisionCompassAdvisorBranchV1(state=state, result=growth_branch),
        request,
    )


def _advisor_selected_id(
    branch: DecisionCompassAdvisorBranchV1,
    request: DecisionCompassRequestV1,
) -> str | None:
    """Return only an exact request-local option selected by Growth Advisor."""

    growth_branch = branch.growth_advisor_branch
    if growth_branch is None or growth_branch.assistant_result is None:
        return None
    assistant_result = growth_branch.assistant_result
    if assistant_result is None:
        raise DecisionCompassCompositionInvalidError()
    selected = assistant_result.selected_option
    if selected is None:
        return None
    if selected not in tuple(AssistantOption(item.id, item.label) for item in request.options):
        return None
    return selected.id


def _validate_advisor_branch(
    value: object,
    request: DecisionCompassRequestV1,
) -> DecisionCompassAdvisorBranchV1:
    """Validate explicit Growth Advisor state and preserve its typed branch."""

    if type(value) is not DecisionCompassAdvisorBranchV1:
        raise DecisionCompassCompositionInvalidError()
    branch = value
    try:
        state = _parse_enum(branch.state, DecisionCompassAdvisorStateV1)
    except ValueError:
        raise DecisionCompassCompositionInvalidError() from None
    if state is DecisionCompassAdvisorStateV1.NOT_REQUESTED:
        if branch.result is not None or branch.error is not None:
            raise DecisionCompassCompositionInvalidError()
        return DecisionCompassAdvisorBranchV1()
    if state is DecisionCompassAdvisorStateV1.ERROR:
        if branch.result is not None or type(branch.error) is not DecisionCompassBranchErrorV1:
            raise DecisionCompassCompositionInvalidError()
        if cast(DecisionCompassBranchErrorCodeV1, branch.error.code).name not in {
            code.name
            for code in DecisionCompassBranchErrorCodeV1
            if code.name.startswith("ADVISOR_")
        }:
            raise DecisionCompassCompositionInvalidError()
        return DecisionCompassAdvisorBranchV1(state=state, error=branch.error)
    if type(branch.result) is not GrowthAdvisorBranchV1 or branch.error is not None:
        raise DecisionCompassCompositionInvalidError()
    try:
        growth_branch = validate_growth_advisor_branch(branch.result)
    except GrowthAdvisorError:
        raise DecisionCompassCompositionInvalidError() from None
    expected_state = (
        DecisionCompassAdvisorStateV1.ABSTENTION
        if growth_branch.state is GrowthAdvisorBranchStateV1.ABSTENTION
        else DecisionCompassAdvisorStateV1.RESULT
    )
    if state is not expected_state:
        raise DecisionCompassCompositionInvalidError()
    provenance = growth_branch.provenance
    selector = cast(DecisionCompassGoalSelectorV1, request.selected_goal)
    if (
        provenance is None
        or provenance.goal_source_uuid != selector.source_uuid
        or provenance.goal_identity_fingerprint != selector.identity_fingerprint
    ):
        raise DecisionCompassGoalSourceChangedError()
    assistant_result = growth_branch.assistant_result
    if assistant_result is None:
        raise DecisionCompassCompositionInvalidError()
    selected = assistant_result.selected_option
    if selected is not None and selected not in tuple(
        AssistantOption(item.id, item.label) for item in request.options
    ):
        raise DecisionCompassCompositionInvalidError()
    return DecisionCompassAdvisorBranchV1(state=state, result=growth_branch)


def _build_structural_relations(
    simulate_branch: CompareSimulateMeBranchV1,
    behavioral_branch: DecisionCompassBehaviorBranchV1,
    request: DecisionCompassRequestV1,
    advisor_branch: DecisionCompassAdvisorBranchV1 | None = None,
) -> tuple[DecisionCompassStructuralRelationV1, ...]:
    advisor = advisor_branch or DecisionCompassAdvisorBranchV1()
    relations: list[DecisionCompassStructuralRelationV1] = []
    if advisor.state is DecisionCompassAdvisorStateV1.NOT_REQUESTED:
        relations.append(
            DecisionCompassStructuralRelationV1(
                code=DecisionCompassStructuralRelationCodeV1.ADVISOR_NOT_REQUESTED,
                left_branch=DecisionCompassStructuralBranchV1.ADVISOR,
                right_branch=DecisionCompassStructuralBranchV1.DECISION_COMPASS,
                left_state=DecisionCompassAdvisorStateV1.NOT_REQUESTED.value,
                right_state=DecisionCompassAdvisorStateV1.NOT_REQUESTED.value,
            )
        )
    else:
        simulate_id = _selected_simulate_id(simulate_branch)
        advisor_id = _advisor_selected_id(advisor, request)
        if simulate_id is not None and advisor_id is not None:
            code = (
                DecisionCompassStructuralRelationCodeV1.SIMULATE_ADVISOR_SAME_OPTION
                if simulate_id == advisor_id
                else DecisionCompassStructuralRelationCodeV1.SIMULATE_ADVISOR_DIFFERENT_OPTIONS
            )
        else:
            code = DecisionCompassStructuralRelationCodeV1.SIMULATE_ADVISOR_NOT_COMPARABLE
        relations.append(
            DecisionCompassStructuralRelationV1(
                code=code,
                left_branch=DecisionCompassStructuralBranchV1.SIMULATE_ME,
                right_branch=DecisionCompassStructuralBranchV1.ADVISOR,
                left_state=_simulate_state_value(simulate_branch),
                right_state=cast(DecisionCompassAdvisorStateV1, advisor.state).value,
                left_option_id=simulate_id,
                right_option_id=advisor_id,
            )
        )
    scope = request.behavioral_scope
    if scope is None:
        relations.append(
            DecisionCompassStructuralRelationV1(
                code=DecisionCompassStructuralRelationCodeV1.BEHAVIOR_SCOPE_NOT_SELECTED,
                left_branch=DecisionCompassStructuralBranchV1.BEHAVIORAL,
                right_branch=DecisionCompassStructuralBranchV1.DECISION_COMPASS,
                left_state=DecisionCompassBehavioralStateV1.NOT_SELECTED.value,
                right_state=DecisionCompassBehavioralStateV1.NOT_SELECTED.value,
            )
        )
    elif request.behavioral_option_binding is None:
        relations.append(
            DecisionCompassStructuralRelationV1(
                code=DecisionCompassStructuralRelationCodeV1.BEHAVIOR_BINDING_MISSING,
                left_branch=DecisionCompassStructuralBranchV1.SIMULATE_ME,
                right_branch=DecisionCompassStructuralBranchV1.BEHAVIORAL,
                left_state=_simulate_state_value(simulate_branch),
                right_state=cast(DecisionCompassBehavioralStateV1, behavioral_branch.state).value,
                left_option_id=_selected_simulate_id(simulate_branch),
            )
        )
    else:
        simulate_id = _selected_simulate_id(simulate_branch)
        behavior_id = _behavior_selected_id(behavioral_branch, request)
        if simulate_id is not None and behavior_id is not None:
            code = (
                DecisionCompassStructuralRelationCodeV1.SIMULATE_BEHAVIOR_SAME_OPTION
                if simulate_id == behavior_id
                else DecisionCompassStructuralRelationCodeV1.SIMULATE_BEHAVIOR_DIFFERENT_OPTIONS
            )
        else:
            code = DecisionCompassStructuralRelationCodeV1.SIMULATE_BEHAVIOR_NOT_COMPARABLE
        relations.append(
            DecisionCompassStructuralRelationV1(
                code=code,
                left_branch=DecisionCompassStructuralBranchV1.SIMULATE_ME,
                right_branch=DecisionCompassStructuralBranchV1.BEHAVIORAL,
                left_state=_simulate_state_value(simulate_branch),
                right_state=cast(DecisionCompassBehavioralStateV1, behavioral_branch.state).value,
                left_option_id=simulate_id,
                right_option_id=behavior_id,
            )
        )
        if advisor.state is not DecisionCompassAdvisorStateV1.NOT_REQUESTED:
            advisor_id = _advisor_selected_id(advisor, request)
            if advisor_id is not None and behavior_id is not None:
                advisor_behavior_code = (
                    DecisionCompassStructuralRelationCodeV1.ADVISOR_BEHAVIOR_SAME_OPTION
                    if advisor_id == behavior_id
                    else DecisionCompassStructuralRelationCodeV1.ADVISOR_BEHAVIOR_DIFFERENT_OPTIONS
                )
            else:
                advisor_behavior_code = (
                    DecisionCompassStructuralRelationCodeV1.ADVISOR_BEHAVIOR_NOT_COMPARABLE
                )
            relations.append(
                DecisionCompassStructuralRelationV1(
                    code=advisor_behavior_code,
                    left_branch=DecisionCompassStructuralBranchV1.ADVISOR,
                    right_branch=DecisionCompassStructuralBranchV1.BEHAVIORAL,
                    left_state=cast(DecisionCompassAdvisorStateV1, advisor.state).value,
                    right_state=cast(
                        DecisionCompassBehavioralStateV1,
                        behavioral_branch.state,
                    ).value,
                    left_option_id=advisor_id,
                    right_option_id=behavior_id,
                )
            )
    return tuple(sorted(relations, key=_relation_sort_key))


def _build_caveats(
    request: DecisionCompassRequestV1,
    behavioral_branch: DecisionCompassBehaviorBranchV1,
    advisor_branch: DecisionCompassAdvisorBranchV1 | None = None,
) -> tuple[DecisionCompassCaveatV1, ...]:
    advisor = advisor_branch or DecisionCompassAdvisorBranchV1()
    values: set[DecisionCompassCaveatV1] = {
        DecisionCompassCaveatV1.NO_HIDDEN_WINNER,
        DecisionCompassCaveatV1.NO_CAUSAL_CLAIM,
    }
    if advisor.state is DecisionCompassAdvisorStateV1.NOT_REQUESTED:
        values.add(DecisionCompassCaveatV1.ADVISOR_NOT_REQUESTED)
    if request.behavioral_scope is None:
        values.add(DecisionCompassCaveatV1.BEHAVIORAL_SCOPE_NOT_SELECTED)
    elif request.behavioral_option_binding is None:
        values.add(DecisionCompassCaveatV1.BEHAVIORAL_BINDING_MISSING)
    elif behavioral_branch.state in {
        DecisionCompassBehavioralStateV1.NOT_COMPARABLE,
        DecisionCompassBehavioralStateV1.UNAVAILABLE,
    }:
        values.add(DecisionCompassCaveatV1.BEHAVIORAL_BINDING_STALE)
    return tuple(sorted(values, key=_caveat_sort_key))


def _simulate_state_value(branch: CompareSimulateMeBranchV1) -> str:
    if type(branch) is CompareSimulateMeResultBranchV1:
        return CompareBranchStateV1.RESULT.value
    if type(branch) is CompareSimulateMeAbstentionBranchV1:
        return CompareBranchStateV1.ABSTENTION.value
    if type(branch) is CompareSimulateMeErrorBranchV1:
        return CompareBranchStateV1.ERROR.value
    raise DecisionCompassCompositionInvalidError()


def _validate_relation(value: object) -> DecisionCompassStructuralRelationV1:
    if type(value) is not DecisionCompassStructuralRelationV1:
        raise DecisionCompassCompositionInvalidError()
    try:
        return DecisionCompassStructuralRelationV1(
            code=value.code,
            left_branch=value.left_branch,
            right_branch=value.right_branch,
            left_state=value.left_state,
            right_state=value.right_state,
            left_option_id=value.left_option_id,
            right_option_id=value.right_option_id,
        )
    except TypeError, ValueError:
        raise DecisionCompassCompositionInvalidError() from None


def _validate_simulate_branch(
    value: object,
    request: SimulateMeRequest,
) -> CompareSimulateMeBranchV1:
    if type(value) is CompareSimulateMeErrorBranchV1:
        error_branch = value
        if error_branch.state not in (
            CompareBranchStateV1.ERROR,
            CompareBranchStateV1.ERROR.value,
        ):
            raise DecisionCompassCompositionInvalidError()
        if error_branch.result is not None or type(error_branch.error) is not CompareBranchErrorV1:
            raise DecisionCompassCompositionInvalidError()
        _validate_compare_branch_error(error_branch.error)
        return CompareSimulateMeErrorBranchV1(
            state=CompareBranchStateV1.ERROR,
            error=error_branch.error,
        )
    if type(value) not in (CompareSimulateMeResultBranchV1, CompareSimulateMeAbstentionBranchV1):
        raise DecisionCompassCompositionInvalidError()
    if type(value) is CompareSimulateMeResultBranchV1:
        result_branch = value
        if result_branch.error is not None or type(result_branch.result) is not SimulateMeResult:
            raise DecisionCompassCompositionInvalidError()
        raw_result = result_branch.result
    else:
        abstention_branch = cast(CompareSimulateMeAbstentionBranchV1, value)
        if (
            abstention_branch.error is not None
            or type(abstention_branch.result) is not SimulateMeResult
        ):
            raise DecisionCompassCompositionInvalidError()
        raw_result = abstention_branch.result
    try:
        result = validate_simulate_me_result(raw_result, request=request)
        _validate_simulate_me_temporal_correspondence(result)
    except SimulateMeError:
        raise DecisionCompassCompositionInvalidError() from None
    if type(value) is CompareSimulateMeResultBranchV1:
        typed_result_branch = value
        if typed_result_branch.state not in (
            CompareBranchStateV1.RESULT,
            CompareBranchStateV1.RESULT.value,
        ):
            raise DecisionCompassCompositionInvalidError()
        if result.kind is SimulateMeResultKind.ABSTENTION:
            raise DecisionCompassCompositionInvalidError()
        return CompareSimulateMeResultBranchV1(state=CompareBranchStateV1.RESULT, result=result)
    typed_abstention_branch = cast(CompareSimulateMeAbstentionBranchV1, value)
    if typed_abstention_branch.state not in (
        CompareBranchStateV1.ABSTENTION,
        CompareBranchStateV1.ABSTENTION.value,
    ):
        raise DecisionCompassCompositionInvalidError()
    if result.kind is not SimulateMeResultKind.ABSTENTION:
        raise DecisionCompassCompositionInvalidError()
    return CompareSimulateMeAbstentionBranchV1(
        state=CompareBranchStateV1.ABSTENTION,
        result=result,
    )


def _validate_compare_branch_error(value: CompareBranchErrorV1) -> None:
    expected = {
        CompareBranchErrorCodeV1.INVALID_REQUEST: "compare branch request failed validation",
        CompareBranchErrorCodeV1.CANCELLED: "compare branch operation cancelled",
        CompareBranchErrorCodeV1.TIMEOUT: "compare branch operation timed out",
        CompareBranchErrorCodeV1.UNAVAILABLE: "compare branch unavailable",
        CompareBranchErrorCodeV1.FAILURE: "compare branch failed",
        CompareBranchErrorCodeV1.MALFORMED_RESULT: "compare branch result is malformed",
        CompareBranchErrorCodeV1.RESULT_TOO_LARGE: "compare branch result exceeds byte budget",
        CompareBranchErrorCodeV1.RESULT_INVALID: "compare branch result failed validation",
    }
    try:
        code = _parse_enum(value.code, CompareBranchErrorCodeV1)
    except ValueError:
        raise DecisionCompassCompositionInvalidError() from None
    if value.message != expected[code]:
        raise DecisionCompassCompositionInvalidError()


def _validate_behavioral_branch(
    value: object,
    request: DecisionCompassRequestV1,
) -> DecisionCompassBehaviorBranchV1:
    if type(value) is not DecisionCompassBehaviorBranchV1:
        raise DecisionCompassCompositionInvalidError()
    branch = value
    try:
        state = _parse_enum(branch.state, DecisionCompassBehavioralStateV1)
    except ValueError:
        raise DecisionCompassCompositionInvalidError() from None
    if branch.pattern is not None:
        if type(branch.pattern) is not BehavioralPatternV1:
            raise DecisionCompassCompositionInvalidError()
        if request.behavioral_scope is None or branch.pattern.cohort is None:
            raise DecisionCompassCompositionInvalidError()
        if (
            branch.pattern.cohort.cohort_fingerprint
            != request.behavioral_scope.behavioral_cohort_fingerprint
        ):
            raise DecisionCompassCompositionInvalidError()
    if state is DecisionCompassBehavioralStateV1.ERROR:
        if branch.pattern is not None or type(branch.error) is not DecisionCompassBranchErrorV1:
            raise DecisionCompassCompositionInvalidError()
        return branch
    if branch.error is not None:
        raise DecisionCompassCompositionInvalidError()
    if request.behavioral_scope is None:
        if state is not DecisionCompassBehavioralStateV1.NOT_SELECTED or branch.pattern is not None:
            raise DecisionCompassCompositionInvalidError()
    elif state is DecisionCompassBehavioralStateV1.NOT_SELECTED:
        raise DecisionCompassCompositionInvalidError()
    if state is DecisionCompassBehavioralStateV1.UNAVAILABLE and branch.pattern is not None:
        raise DecisionCompassCompositionInvalidError()
    if state is DecisionCompassBehavioralStateV1.INSUFFICIENT and (
        branch.pattern is None
        or branch.pattern.pattern_type is not BehavioralPatternTypeV1.INSUFFICIENT_EVIDENCE
    ):
        raise DecisionCompassCompositionInvalidError()
    if state is DecisionCompassBehavioralStateV1.RESULT and branch.pattern is None:
        raise DecisionCompassCompositionInvalidError()
    if (
        state is DecisionCompassBehavioralStateV1.RESULT
        and branch.pattern is not None
        and (
            branch.pattern.pattern_type is BehavioralPatternTypeV1.INSUFFICIENT_EVIDENCE
            or branch.pattern.pattern_type is BehavioralPatternTypeV1.NOT_COMPARABLE
        )
    ):
        raise DecisionCompassCompositionInvalidError()
    if state is DecisionCompassBehavioralStateV1.NOT_COMPARABLE and (
        branch.pattern is not None
        and branch.pattern.pattern_type is BehavioralPatternTypeV1.INSUFFICIENT_EVIDENCE
    ):
        raise DecisionCompassCompositionInvalidError()
    if (
        request.behavioral_option_binding is not None
        and branch.pattern is not None
        and branch.pattern.selected_option is not None
    ):
        selected = branch.pattern.selected_option
        binding = request.behavioral_option_binding
        exact = (
            selected.option_index == binding.behavioral_option_index
            and selected.option_fingerprint == binding.behavioral_option_fingerprint
        )
        if (state is DecisionCompassBehavioralStateV1.RESULT) != exact:
            raise DecisionCompassCompositionInvalidError()
    return DecisionCompassBehaviorBranchV1(state=state, pattern=branch.pattern)


def _normalize_caveats(
    values: tuple[DecisionCompassCaveatV1 | str, ...],
) -> tuple[DecisionCompassCaveatV1, ...]:
    if len(values) > len(DecisionCompassCaveatV1):
        raise DecisionCompassCompositionInvalidError()
    try:
        parsed = tuple(_parse_enum(value, DecisionCompassCaveatV1) for value in values)
    except ValueError:
        raise DecisionCompassCompositionInvalidError() from None
    if len(set(parsed)) != len(parsed):
        raise DecisionCompassCompositionInvalidError()
    ordered = tuple(sorted(parsed, key=_caveat_sort_key))
    if parsed != ordered:
        raise DecisionCompassCompositionInvalidError()
    return ordered


def _validate_provenance(
    provenance: DecisionCompassProvenanceV1,
    request: DecisionCompassRequestV1,
    growth_progress: GrowthGoalProgressCompositionResultV1,
    behavioral: DecisionCompassBehaviorBranchV1,
    advisor: DecisionCompassAdvisorBranchV1,
) -> None:
    selector = cast(DecisionCompassGoalSelectorV1, request.selected_goal)
    progress_as_of = cast(datetime, request.progress_as_of)
    if (
        provenance.selected_goal_source_uuid != selector.source_uuid
        or provenance.selected_goal_identity_fingerprint != selector.identity_fingerprint
        or provenance.decision_compass_policy_fingerprint != POLICY_FINGERPRINT
        or provenance.growth_progress_policy_fingerprint != growth_progress.policy_fingerprint
        or provenance.progress_as_of != progress_as_of
        or provenance.simulate_me_policy_id != SIMULATE_ME_POLICY_ID
        or provenance.simulate_me_policy_fingerprint != SIMULATE_ME_POLICY_FINGERPRINT
    ):
        raise DecisionCompassCompositionInvalidError()
    expected_behavioral_fp = (
        behavioral.pattern.policy_fingerprint if behavioral.pattern is not None else None
    )
    if provenance.behavioral_policy_fingerprint != expected_behavioral_fp:
        raise DecisionCompassCompositionInvalidError()
    expected_advisor_provenance = (
        "not_requested"
        if advisor.state is DecisionCompassAdvisorStateV1.NOT_REQUESTED
        else DECISION_COMPASS_ADVISOR_PROVENANCE
    )
    if provenance.advisor != expected_advisor_provenance:
        raise DecisionCompassCompositionInvalidError()


def _result_payload(result: DecisionCompassResultV1) -> dict[str, object]:
    simulate_branch = result.simulate_me
    if type(simulate_branch) is CompareSimulateMeErrorBranchV1:
        if simulate_branch.error is None:
            raise DecisionCompassCompositionInvalidError()
        simulate_payload: dict[str, object] = {
            "state": simulate_branch.state.value,
            "result": None,
            "error": simulate_branch.error.as_dict(),
        }
    elif isinstance(
        simulate_branch,
        (CompareSimulateMeResultBranchV1, CompareSimulateMeAbstentionBranchV1),
    ):
        simulate_payload = _simulate_success_payload(simulate_branch)
    else:
        raise DecisionCompassCompositionInvalidError()
    return {
        "contract_version": result.contract_version,
        "derivation_version": result.derivation_version,
        "policy_id": result.policy_id,
        "policy_fingerprint": result.policy_fingerprint,
        "selected_goal": result.selected_goal.as_dict(),
        "request": result.request.as_dict(),
        "simulate_me": simulate_payload,
        "behavioral": result.behavioral.as_dict(),
        "growth_progress": result.growth_progress.as_dict(),
        "advisor": result.advisor.as_dict(),
        "structural_relations": [item.as_dict() for item in result.structural_relations],
        "caveats": [cast(DecisionCompassCaveatV1, item).value for item in result.caveats],
        "provenance": result.provenance.as_dict(),
    }


def _simulate_result_payload(result: SimulateMeResult) -> dict[str, object]:
    """Serialize the existing Simulate Me DTO without flattening it."""

    selected = (
        {"id": result.selected_option.id, "label": result.selected_option.label}
        if result.selected_option is not None
        else None
    )
    return {
        "kind": result.kind.value,
        "selected_option": selected,
        "evidence_refs": [
            {
                "claim_id": str(item.claim_id),
                "dimension": item.dimension.value,
                "note_ids": [str(note_id) for note_id in item.note_ids],
                "evidence_at": _evidence_at_projection(item.evidence_at),
            }
            for item in result.evidence_refs
        ],
        "contextual_evidence_refs": [
            {
                "claim_id": str(item.claim_id),
                "dimension": item.dimension.value,
                "note_ids": [str(note_id) for note_id in item.note_ids],
                "evidence_at": _evidence_at_projection(item.evidence_at),
            }
            for item in result.contextual_evidence_refs
        ],
        "temporal_caveats": [
            {"code": item.code.value, "claim_id": str(item.claim_id)}
            for item in result.temporal_caveats
        ],
        "abstention_code": (
            result.abstention_code.value if result.abstention_code is not None else None
        ),
        "derivation_version": result.derivation_version,
        "policy_id": result.policy_id,
        "policy_fingerprint": result.policy_fingerprint,
    }


def _validate_simulate_me_temporal_correspondence(result: SimulateMeResult) -> None:
    """Preserve Compare v1's exact unknown-evidence caveat invariant."""

    refs: tuple[SimulateMeEvidenceRef | SimulateMeContextualEvidenceRef, ...] = (
        *result.evidence_refs,
        *result.contextual_evidence_refs,
    )
    unknown_claim_ids = {ref.claim_id for ref in refs if ref.evidence_at == "unknown"}
    caveat_claim_ids = {caveat.claim_id for caveat in result.temporal_caveats}
    if unknown_claim_ids != caveat_claim_ids:
        raise DecisionCompassCompositionInvalidError()


def _simulate_success_payload(
    branch: CompareSimulateMeResultBranchV1 | CompareSimulateMeAbstentionBranchV1,
) -> dict[str, object]:
    """Serialize either existing Compare success wrapper without flattening."""

    return {
        "state": branch.state.value,
        "result": _simulate_result_payload(branch.result),
        "error": None,
    }


def _canonical_result_bytes(result: DecisionCompassResultV1) -> bytes:
    return canonical_decision_compass_json(_result_payload(result)).encode("utf-8")


def _top_error(code: DecisionCompassErrorCodeV1) -> DecisionCompassErrorV1:
    return DecisionCompassErrorV1(code=code, message=_ERROR_MESSAGES[code])


def _map_growth_progress_error(
    error: GrowthGoalProgressCompositionError,
) -> DecisionCompassErrorCodeV1:
    code = str(error.code)
    if code == GrowthGoalProgressCompositionErrorCodeV1.INVALID_REQUEST.value:
        return DecisionCompassErrorCodeV1.INVALID_REQUEST
    if code == GrowthGoalProgressCompositionErrorCodeV1.POLICY_MISMATCH.value:
        return DecisionCompassErrorCodeV1.POLICY_MISMATCH
    if code == GrowthGoalProgressCompositionErrorCodeV1.RESULT_TOO_LARGE.value:
        return DecisionCompassErrorCodeV1.RESULT_TOO_LARGE
    if code == GrowthGoalProgressCompositionErrorCodeV1.SOURCE_CHANGED.value:
        return DecisionCompassErrorCodeV1.GOAL_SOURCE_CHANGED
    if code == GrowthGoalProgressCompositionErrorCodeV1.SOURCE_UNAVAILABLE.value:
        return DecisionCompassErrorCodeV1.SOURCE_UNAVAILABLE
    if code == GrowthGoalProgressCompositionErrorCodeV1.GOAL_BINDING_MISMATCH.value:
        return DecisionCompassErrorCodeV1.GOAL_SOURCE_CHANGED
    return DecisionCompassErrorCodeV1.INTERNAL


def _map_growth_error(error: GrowthError) -> DecisionCompassErrorCodeV1:
    """Map an unwrapped authoritative Growth failure to a safe outer code."""

    code = str(error.code)
    if code == GrowthErrorCode.GOAL_MISSING.value:
        return DecisionCompassErrorCodeV1.GOAL_UNAVAILABLE
    if code == GrowthErrorCode.GOAL_SOURCE_CHANGED.value:
        return DecisionCompassErrorCodeV1.GOAL_SOURCE_CHANGED
    if code == GrowthErrorCode.POLICY_MISMATCH.value:
        return DecisionCompassErrorCodeV1.POLICY_MISMATCH
    if code == GrowthErrorCode.RESULT_TOO_LARGE.value:
        return DecisionCompassErrorCodeV1.RESULT_TOO_LARGE
    if code in {
        GrowthErrorCode.GOAL_SOURCE_UNAVAILABLE.value,
        GrowthErrorCode.BEHAVIORAL_SOURCE_UNAVAILABLE.value,
        GrowthErrorCode.MAPPING_STORE_UNAVAILABLE.value,
        GrowthErrorCode.MAPPING_STORE_CORRUPT.value,
    }:
        return DecisionCompassErrorCodeV1.SOURCE_UNAVAILABLE
    return DecisionCompassErrorCodeV1.INTERNAL


def _parse_enum[EnumT: StrEnum](value: object, enum_type: type[EnumT]) -> EnumT:
    if type(value) is enum_type:
        return value
    if type(value) is str:
        try:
            return enum_type(value)
        except ValueError:
            pass
    raise ValueError("Decision Compass enum is invalid")


def _parse_uuid(value: object) -> UUID:
    try:
        return parse_uuid7(value)
    except TypeError, ValueError, OverflowError:
        raise ValueError("Decision Compass UUID is invalid") from None


def _parse_hash(value: object) -> str:
    if type(value) is not str or _HASH_PATTERN.fullmatch(value) is None:
        raise ValueError("Decision Compass hash is invalid")
    return value


def _parse_exact_utc(value: object) -> datetime:
    if type(value) is str:
        try:
            parsed = parse_rfc3339(value)
        except TypeError, ValueError, OverflowError:
            raise ValueError("Decision Compass timestamp is invalid") from None
    elif isinstance(value, datetime):
        parsed = value
    else:
        raise ValueError("Decision Compass timestamp is invalid")
    if (
        parsed.tzinfo is None
        or parsed.utcoffset() is None
        or parsed.utcoffset() != UTC.utcoffset(parsed)
    ):
        raise ValueError("Decision Compass timestamp is invalid")
    return parsed.astimezone(UTC)


def _format_timestamp(value: datetime) -> str:
    normalized = _parse_exact_utc(value)
    rendered = normalized.isoformat(
        timespec="microseconds" if normalized.microsecond else "seconds"
    )
    return rendered.removesuffix("+00:00") + "Z"


def _timestamp_projection(value: object) -> str | None:
    if value is None:
        return None
    try:
        return _format_timestamp(_parse_exact_utc(value))
    except TypeError, ValueError, OverflowError:
        return None


def _evidence_at_projection(value: object) -> object:
    if value == "unknown":
        return value
    if isinstance(value, datetime):
        return _format_timestamp(value.astimezone(UTC))
    raise DecisionCompassCompositionInvalidError()


def _relation_sort_key(value: DecisionCompassStructuralRelationV1) -> int:
    return tuple(DecisionCompassStructuralRelationCodeV1).index(
        cast(DecisionCompassStructuralRelationCodeV1, value.code)
    )


def _caveat_sort_key(value: DecisionCompassCaveatV1) -> int:
    return tuple(DecisionCompassCaveatV1).index(value)


def _validate_relation_shape(
    code: DecisionCompassStructuralRelationCodeV1,
    left_branch: DecisionCompassStructuralBranchV1,
    right_branch: DecisionCompassStructuralBranchV1,
    left_state: str,
    right_state: str,
    left_option_id: str | None,
    right_option_id: str | None,
) -> None:
    if code is DecisionCompassStructuralRelationCodeV1.ADVISOR_NOT_REQUESTED:
        if (
            left_branch is not DecisionCompassStructuralBranchV1.ADVISOR
            or right_branch is not DecisionCompassStructuralBranchV1.DECISION_COMPASS
            or left_state != DecisionCompassAdvisorStateV1.NOT_REQUESTED.value
            or right_state != DecisionCompassAdvisorStateV1.NOT_REQUESTED.value
            or left_option_id is not None
            or right_option_id is not None
        ):
            raise ValueError("Decision Compass Advisor relation is invalid")
        return
    if code is DecisionCompassStructuralRelationCodeV1.BEHAVIOR_SCOPE_NOT_SELECTED:
        if (
            left_branch is not DecisionCompassStructuralBranchV1.BEHAVIORAL
            or right_branch is not DecisionCompassStructuralBranchV1.DECISION_COMPASS
            or left_state != DecisionCompassBehavioralStateV1.NOT_SELECTED.value
            or right_state != DecisionCompassBehavioralStateV1.NOT_SELECTED.value
            or left_option_id is not None
            or right_option_id is not None
        ):
            raise ValueError("Decision Compass scope relation is invalid")
        return
    if code is DecisionCompassStructuralRelationCodeV1.BEHAVIOR_BINDING_MISSING:
        if (
            left_branch is not DecisionCompassStructuralBranchV1.SIMULATE_ME
            or right_branch is not DecisionCompassStructuralBranchV1.BEHAVIORAL
            or right_option_id is not None
        ):
            raise ValueError("Decision Compass binding relation is invalid")
        _validate_simulate_state(left_state)
        _validate_behavior_state(right_state)
        return
    if code in {
        DecisionCompassStructuralRelationCodeV1.SIMULATE_BEHAVIOR_SAME_OPTION,
        DecisionCompassStructuralRelationCodeV1.SIMULATE_BEHAVIOR_DIFFERENT_OPTIONS,
        DecisionCompassStructuralRelationCodeV1.SIMULATE_BEHAVIOR_NOT_COMPARABLE,
    }:
        if (
            left_branch is not DecisionCompassStructuralBranchV1.SIMULATE_ME
            or right_branch is not DecisionCompassStructuralBranchV1.BEHAVIORAL
        ):
            raise ValueError("Decision Compass Behavioral relation is invalid")
        _validate_simulate_state(left_state)
        _validate_behavior_state(right_state)
        _validate_option_relation(code, left_option_id, right_option_id)
        return
    if code in {
        DecisionCompassStructuralRelationCodeV1.SIMULATE_ADVISOR_SAME_OPTION,
        DecisionCompassStructuralRelationCodeV1.SIMULATE_ADVISOR_DIFFERENT_OPTIONS,
        DecisionCompassStructuralRelationCodeV1.SIMULATE_ADVISOR_NOT_COMPARABLE,
    }:
        if (
            left_branch is not DecisionCompassStructuralBranchV1.SIMULATE_ME
            or right_branch is not DecisionCompassStructuralBranchV1.ADVISOR
        ):
            raise ValueError("Decision Compass Simulate Me/Advisor relation is invalid")
        _validate_simulate_state(left_state)
        _validate_advisor_state(right_state)
        _validate_option_relation(code, left_option_id, right_option_id)
        return
    if code in {
        DecisionCompassStructuralRelationCodeV1.ADVISOR_BEHAVIOR_SAME_OPTION,
        DecisionCompassStructuralRelationCodeV1.ADVISOR_BEHAVIOR_DIFFERENT_OPTIONS,
        DecisionCompassStructuralRelationCodeV1.ADVISOR_BEHAVIOR_NOT_COMPARABLE,
    }:
        if (
            left_branch is not DecisionCompassStructuralBranchV1.ADVISOR
            or right_branch is not DecisionCompassStructuralBranchV1.BEHAVIORAL
        ):
            raise ValueError("Decision Compass Advisor/Behavior relation is invalid")
        _validate_advisor_state(left_state)
        _validate_behavior_state(right_state)
        _validate_option_relation(code, left_option_id, right_option_id)
        return
    raise ValueError("Decision Compass relation code is invalid")


def _validate_option_relation(
    code: DecisionCompassStructuralRelationCodeV1,
    left_option_id: str | None,
    right_option_id: str | None,
) -> None:
    """Validate exact-ID relation payloads for future-compatible pairs."""

    if code in {
        DecisionCompassStructuralRelationCodeV1.SIMULATE_ADVISOR_SAME_OPTION,
        DecisionCompassStructuralRelationCodeV1.SIMULATE_BEHAVIOR_SAME_OPTION,
        DecisionCompassStructuralRelationCodeV1.ADVISOR_BEHAVIOR_SAME_OPTION,
    }:
        if left_option_id is None or right_option_id is None or left_option_id != right_option_id:
            raise ValueError("Decision Compass same-option relation is invalid")
    elif code in {
        DecisionCompassStructuralRelationCodeV1.SIMULATE_ADVISOR_DIFFERENT_OPTIONS,
        DecisionCompassStructuralRelationCodeV1.SIMULATE_BEHAVIOR_DIFFERENT_OPTIONS,
        DecisionCompassStructuralRelationCodeV1.ADVISOR_BEHAVIOR_DIFFERENT_OPTIONS,
    }:
        if left_option_id is None or right_option_id is None or left_option_id == right_option_id:
            raise ValueError("Decision Compass different-option relation is invalid")
    elif left_option_id is not None and right_option_id is not None:
        raise ValueError("Decision Compass not-comparable relation is invalid")


def _validate_simulate_state(value: str) -> None:
    if value not in {
        CompareBranchStateV1.RESULT.value,
        CompareBranchStateV1.ABSTENTION.value,
        CompareBranchStateV1.ERROR.value,
    }:
        raise ValueError("Decision Compass Simulate Me state is invalid")


def _validate_advisor_state(value: str) -> None:
    if value not in {
        DecisionCompassAdvisorStateV1.RESULT.value,
        DecisionCompassAdvisorStateV1.ABSTENTION.value,
        DecisionCompassAdvisorStateV1.ERROR.value,
        DecisionCompassAdvisorStateV1.NOT_REQUESTED.value,
    }:
        raise ValueError("Decision Compass Advisor state is invalid")


def _validate_behavior_state(value: str) -> None:
    if value not in {item.value for item in DecisionCompassBehavioralStateV1}:
        raise ValueError("Decision Compass Behavioral state is invalid")


__all__ = [
    "CONTRACT_VERSION",
    "DECISION_COMPASS_ADVISOR_PROVENANCE",
    "DECISION_COMPASS_CONTRACT_VERSION",
    "DECISION_COMPASS_DERIVATION_VERSION",
    "DECISION_COMPASS_POLICY_ID",
    "DERIVATION_VERSION",
    "MAX_BEHAVIORAL_OPTION_INDEX",
    "MAX_CONSTRAINTS",
    "MAX_CONSTRAINTS_BYTES",
    "MAX_CONSTRAINT_BYTES",
    "MAX_CONTEXT_ENTRIES",
    "MAX_CONTEXT_TEXTS_BYTES",
    "MAX_CONTEXT_TEXT_BYTES",
    "MAX_CRITERIA",
    "MAX_CRITERIA_BYTES",
    "MAX_OPTIONS",
    "MAX_OPTION_ID_BYTES",
    "MAX_OPTION_LABEL_BYTES",
    "MAX_RESULT_BYTES",
    "MAX_TASK_BYTES",
    "MIN_MAX_RESULT_BYTES_V1",
    "POLICY_CANONICAL_JSON",
    "POLICY_FINGERPRINT",
    "POLICY_ID",
    "POLICY_PAYLOAD",
    "BuildDecisionCompass",
    "BuildDecisionCompassV1",
    "BuildGrowthCompare",
    "DecisionCompassAdvisorBranch",
    "DecisionCompassAdvisorBranchV1",
    "DecisionCompassAdvisorStateV1",
    "DecisionCompassBehaviorBranch",
    "DecisionCompassBehaviorBranchV1",
    "DecisionCompassBehaviorOptionBinding",
    "DecisionCompassBehaviorOptionBindingV1",
    "DecisionCompassBehaviorScope",
    "DecisionCompassBehaviorScopeV1",
    "DecisionCompassBehaviorStateV1",
    "DecisionCompassBehavioralBranchV1",
    "DecisionCompassBehavioralBuilder",
    "DecisionCompassBehavioralState",
    "DecisionCompassBehavioralStateV1",
    "DecisionCompassBranchErrorCodeV1",
    "DecisionCompassBranchErrorV1",
    "DecisionCompassCaveat",
    "DecisionCompassCaveatV1",
    "DecisionCompassCompositionInvalidError",
    "DecisionCompassCriterion",
    "DecisionCompassCriterionV1",
    "DecisionCompassError",
    "DecisionCompassErrorCode",
    "DecisionCompassErrorCodeV1",
    "DecisionCompassErrorV1",
    "DecisionCompassExecutorV1",
    "DecisionCompassGoalContextBuilder",
    "DecisionCompassGoalSelector",
    "DecisionCompassGoalSelectorV1",
    "DecisionCompassGrowthAdvisorBuilder",
    "DecisionCompassGrowthProgressBuilder",
    "DecisionCompassInvalidRequestError",
    "DecisionCompassOption",
    "DecisionCompassOptionBinding",
    "DecisionCompassOptionBindingInvalidError",
    "DecisionCompassOptionV1",
    "DecisionCompassPolicyMismatchError",
    "DecisionCompassProvenance",
    "DecisionCompassProvenanceV1",
    "DecisionCompassRelationCodeV1",
    "DecisionCompassRequest",
    "DecisionCompassRequestV1",
    "DecisionCompassResult",
    "DecisionCompassResultTooLargeError",
    "DecisionCompassResultV1",
    "DecisionCompassSimulateMePort",
    "DecisionCompassStructuralBranchV1",
    "DecisionCompassStructuralRelation",
    "DecisionCompassStructuralRelationCode",
    "DecisionCompassStructuralRelationCodeV1",
    "DecisionCompassStructuralRelationV1",
    "DecisionCompassValidationError",
    "GrowthCompareProvenanceV1",
    "GrowthCompareRequestV1",
    "GrowthCompareResultV1",
    "GrowthCompareStructuralRelationV1",
    "build_decision_compass",
    "build_decision_compass_advisor",
    "build_decision_compass_advisor_request",
    "build_growth_advisor_request",
    "build_growth_compare",
    "build_growth_compare_advisor",
    "canonical_decision_compass_json",
    "canonical_decision_compass_result_bytes",
    "decision_compass_hash_json",
    "serialize_decision_compass_result",
    "serialize_growth_compare_result",
    "validate_decision_compass_policy",
    "validate_decision_compass_request",
    "validate_decision_compass_result",
    "validate_growth_compare_policy",
    "validate_growth_compare_request",
    "validate_growth_compare_result",
]
