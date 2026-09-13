"""Explicit-owner Growth Advisor v1 runtime.

Stage 11C is deliberately a small coordinator around the existing Assistant
and AdvisorPort boundaries.  It rebuilds one current Stage 4 Goal, exposes a
transient normalized preview, and injects that one Goal only after an explicit
owner confirmation and an immediate identity revalidation.  No Growth
relation, Behavioral model, persistence or provider-specific state is part of
this module.
"""

from __future__ import annotations

import json
import math
import time
import unicodedata
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import Final, cast
from uuid import UUID, uuid7

from second_brain.application.assistant import (
    ASSISTANT_CONTRACT_VERSION,
    MAX_GOAL_BYTES,
    AssistantError,
    AssistantExplicitContext,
    AssistantInvalidRequestError,
    AssistantOption,
    AssistantRequest,
    AssistantResultEnvelopeV1,
    AssistantResultKind,
    BuildAssistant,
    build_assistant_reasoning_envelope,
    normalize_assistant_result_structure,
    serialize_assistant_reasoning_envelope,
    serialize_assistant_result_envelope,
    validate_assistant_request,
)
from second_brain.application.growth import (
    GrowthError,
    GrowthGoalIdentityV1,
    GrowthGoalSourceChangedError,
    GrowthGoalSourceUnavailableError,
    GrowthPolicyMismatchError,
    GrowthResultTooLargeError,
    build_growth_goal_identity,
    canonical_growth_json,
    growth_hash_json,
    growth_hash_text,
    validate_growth_hash,
    validate_growth_policy,
)
from second_brain.application.ports import (
    AdvisorPort,
    CancellationToken,
    CancellationTokenSource,
    VaultReader,
)
from second_brain.application.reports import ScanReport, VaultSnapshot
from second_brain.application.self_model import (
    DEFAULT_SELF_MODEL_POLICY,
    MAX_SELF_MODEL_LIMIT,
    BuildSelfModel,
    SelfModelClaim,
    SelfModelError,
    SelfModelInvalidClockError,
    SelfModelInvalidRequestError,
    SelfModelPolicy,
    SelfModelPolicyUnavailableError,
    SelfModelRequest,
    SelfModelResult,
    SelfModelResultInvalidError,
    SelfModelResultTooLargeError,
    SelfModelVaultUnavailableError,
    validate_self_model_policy,
)
from second_brain.application.validation import build_report
from second_brain.domain.models import SelfKind, parse_rfc3339, parse_uuid7

type GrowthAdvisorHashV1 = str
type GrowthAdvisorClock = Callable[[], datetime]
type GrowthAdvisorMonotonicClock = Callable[[], float]
type GrowthAdvisorRequestIdFactory = Callable[[], UUID]

GROWTH_ADVISOR_CONTRACT_VERSION: Final[str] = "growth-advisor-v1"
GROWTH_ADVISOR_POLICY_ID: Final[str] = "growth-advisor-owner-explicit-goal-v1"
GROWTH_ADVISOR_POLICY_CANONICAL_JSON: Final[str] = (
    '{"assistant_contract":"assistant-v1","automatic_behavioral_context":"none-v1",'
    '"automatic_growth_context":"none-v1","contract":"growth-advisor-v1",'
    '"goal_source":"stage11-current-selected-goal-v1",'
    '"owner_action":"explicit-preview-confirm-request-v1",'
    '"payload":"one-owner-previewed-current-goal-as-explicit-goals-v1",'
    '"persistence":"ephemeral-no-application-persistence-v1",'
    '"provider":"existing-advisor-port-provider-neutral-v1",'
    '"result":"independent-recommendation-analysis-transient-v1",'
    '"transport":"assistant-request-through-advisor-port-v1","version":"1"}'
)
GROWTH_ADVISOR_POLICY_FINGERPRINT: Final[GrowthAdvisorHashV1] = (
    "sha256:78a651c2450f4c0c698e0ea4f51786ed846a80f32c2a582b6e9fda3965918c0b"
)
GROWTH_ADVISOR_ASSISTANT_CONTEXT_POLICY: Final[str] = "explicit-context-only-v1"
GROWTH_ADVISOR_RESULT_KIND: Final[str] = "independent_recommendation_analysis"
GROWTH_ADVISOR_TOTAL_DEADLINE_SECONDS: Final[float] = 30.0

_GROWTH_ADVISOR_POLICY_PAYLOAD: Final[dict[str, str]] = {
    "assistant_contract": ASSISTANT_CONTRACT_VERSION,
    "automatic_behavioral_context": "none-v1",
    "automatic_growth_context": "none-v1",
    "contract": GROWTH_ADVISOR_CONTRACT_VERSION,
    "goal_source": "stage11-current-selected-goal-v1",
    "owner_action": "explicit-preview-confirm-request-v1",
    "payload": "one-owner-previewed-current-goal-as-explicit-goals-v1",
    "persistence": "ephemeral-no-application-persistence-v1",
    "provider": "existing-advisor-port-provider-neutral-v1",
    "result": "independent-recommendation-analysis-transient-v1",
    "transport": "assistant-request-through-advisor-port-v1",
    "version": "1",
}
_GROWTH_ADVISOR_PROJECTION_TASK: Final[str] = "Growth Advisor Goal projection"


class GrowthAdvisorErrorCode(StrEnum):
    """Closed safe error vocabulary for the explicit Advisor branch."""

    INVALID_REQUEST = "GROWTH_ADVISOR_INVALID_REQUEST"
    GOAL_UNAVAILABLE = "GROWTH_ADVISOR_GOAL_UNAVAILABLE"
    GOAL_MISSING = "GROWTH_ADVISOR_GOAL_MISSING"
    GOAL_CHANGED = "GROWTH_ADVISOR_GOAL_CHANGED"
    GOAL_TEXT_UNSUPPORTED = "GROWTH_ADVISOR_GOAL_TEXT_UNSUPPORTED"
    GOAL_TEXT_TOO_LARGE = "GROWTH_ADVISOR_GOAL_TEXT_TOO_LARGE"
    CONTEXT_TOO_LARGE = "GROWTH_ADVISOR_CONTEXT_TOO_LARGE"
    RESULT_TOO_LARGE = "GROWTH_ADVISOR_RESULT_TOO_LARGE"
    RECOMMENDATION_UNAVAILABLE = "GROWTH_RECOMMENDATION_UNAVAILABLE"
    CANCELLED = "GROWTH_ADVISOR_CANCELLED"
    TIMEOUT = "GROWTH_ADVISOR_TIMEOUT"
    FAILURE = "GROWTH_ADVISOR_FAILURE"
    INVALID_RESULT = "GROWTH_ADVISOR_INVALID_RESULT"
    POLICY_MISMATCH = "GROWTH_ADVISOR_POLICY_MISMATCH"


GrowthAdvisorErrorCodeV1 = GrowthAdvisorErrorCode

_GROWTH_ADVISOR_ERROR_MESSAGES: Final[dict[GrowthAdvisorErrorCode, str]] = {
    GrowthAdvisorErrorCode.INVALID_REQUEST: "growth advisor request failed validation",
    GrowthAdvisorErrorCode.GOAL_UNAVAILABLE: "the current goal source is unavailable",
    GrowthAdvisorErrorCode.GOAL_MISSING: "the requested current goal is missing",
    GrowthAdvisorErrorCode.GOAL_CHANGED: "the current goal preview is stale",
    GrowthAdvisorErrorCode.GOAL_TEXT_UNSUPPORTED: "the current goal text is unsupported",
    GrowthAdvisorErrorCode.GOAL_TEXT_TOO_LARGE: "the current goal text exceeds the Assistant limit",
    GrowthAdvisorErrorCode.CONTEXT_TOO_LARGE: "the Assistant context exceeds the request limit",
    GrowthAdvisorErrorCode.RESULT_TOO_LARGE: "the Assistant result exceeds the request limit",
    GrowthAdvisorErrorCode.RECOMMENDATION_UNAVAILABLE: (
        "the independent recommendation is unavailable"
    ),
    GrowthAdvisorErrorCode.CANCELLED: "the independent recommendation was cancelled",
    GrowthAdvisorErrorCode.TIMEOUT: "the independent recommendation timed out",
    GrowthAdvisorErrorCode.FAILURE: "the independent recommendation failed",
    GrowthAdvisorErrorCode.INVALID_RESULT: (
        "the independent recommendation result failed validation"
    ),
    GrowthAdvisorErrorCode.POLICY_MISMATCH: "the Growth Advisor policy binding is invalid",
}


class GrowthAdvisorError(RuntimeError):
    """Safe application error containing only one fixed code and message."""

    def __init__(self, code: GrowthAdvisorErrorCode | str) -> None:
        normalized = _normalize_error_code(code)
        self.code = normalized.value
        self.message = _GROWTH_ADVISOR_ERROR_MESSAGES[normalized]
        super().__init__(self.message)

    def as_dict(self) -> dict[str, str]:
        """Return the exact public error projection."""

        return {"code": self.code, "message": self.message}


class GrowthAdvisorInvalidRequestError(GrowthAdvisorError):
    """The request or explicit confirmation is outside the exact contract."""

    def __init__(self) -> None:
        super().__init__(GrowthAdvisorErrorCode.INVALID_REQUEST)


class GrowthAdvisorGoalUnavailableError(GrowthAdvisorError):
    """The complete current Goal source cannot be rebuilt safely."""

    def __init__(self) -> None:
        super().__init__(GrowthAdvisorErrorCode.GOAL_UNAVAILABLE)


class GrowthAdvisorGoalMissingError(GrowthAdvisorError):
    """The requested UUID is not one current direct Goal."""

    def __init__(self) -> None:
        super().__init__(GrowthAdvisorErrorCode.GOAL_MISSING)


class GrowthAdvisorGoalChangedError(GrowthAdvisorError):
    """The current Goal no longer matches the owner preview binding."""

    def __init__(self) -> None:
        super().__init__(GrowthAdvisorErrorCode.GOAL_CHANGED)


class GrowthAdvisorGoalTextUnsupportedError(GrowthAdvisorError):
    """The current claim cannot pass existing Assistant text validation."""

    def __init__(self) -> None:
        super().__init__(GrowthAdvisorErrorCode.GOAL_TEXT_UNSUPPORTED)


class GrowthAdvisorGoalTextTooLargeError(GrowthAdvisorError):
    """The normalized Goal exceeds Assistant's one-value limit."""

    def __init__(self) -> None:
        super().__init__(GrowthAdvisorErrorCode.GOAL_TEXT_TOO_LARGE)


class GrowthAdvisorContextTooLargeError(GrowthAdvisorError):
    """The complete explicit Assistant envelope exceeds its request budget."""

    def __init__(self) -> None:
        super().__init__(GrowthAdvisorErrorCode.CONTEXT_TOO_LARGE)


class GrowthAdvisorResultTooLargeError(GrowthAdvisorError):
    """The validated Assistant result exceeds the caller-owned result budget."""

    def __init__(self) -> None:
        super().__init__(GrowthAdvisorErrorCode.RESULT_TOO_LARGE)


class GrowthRecommendationUnavailableError(GrowthAdvisorError):
    """The optional independent Advisor branch is unavailable."""

    def __init__(self) -> None:
        super().__init__(GrowthAdvisorErrorCode.RECOMMENDATION_UNAVAILABLE)


class GrowthAdvisorCancelledError(GrowthAdvisorError):
    """The owner operation was cancelled at a safe boundary."""

    def __init__(self) -> None:
        super().__init__(GrowthAdvisorErrorCode.CANCELLED)


class GrowthAdvisorTimeoutError(GrowthAdvisorError):
    """The fixed internal Advisor deadline elapsed."""

    def __init__(self) -> None:
        super().__init__(GrowthAdvisorErrorCode.TIMEOUT)


class GrowthAdvisorFailureError(GrowthAdvisorError):
    """The Advisor failed without a safe, convincing partial result."""

    def __init__(self) -> None:
        super().__init__(GrowthAdvisorErrorCode.FAILURE)


class GrowthAdvisorInvalidResultError(GrowthAdvisorError):
    """The Assistant result failed the existing typed result contract."""

    def __init__(self) -> None:
        super().__init__(GrowthAdvisorErrorCode.INVALID_RESULT)


class GrowthAdvisorPolicyMismatchError(GrowthAdvisorError):
    """The exact Growth Advisor or Stage 4 policy binding is invalid."""

    def __init__(self) -> None:
        super().__init__(GrowthAdvisorErrorCode.POLICY_MISMATCH)


def _normalize_error_code(code: GrowthAdvisorErrorCode | str) -> GrowthAdvisorErrorCode:
    if type(code) is GrowthAdvisorErrorCode:
        return code
    if type(code) is str:
        try:
            return GrowthAdvisorErrorCode(code)
        except ValueError:
            pass
    return GrowthAdvisorErrorCode.FAILURE


def validate_growth_advisor_policy() -> GrowthAdvisorHashV1:
    """Compute and verify the normative policy fingerprint from canonical JSON."""

    try:
        canonical = canonical_growth_json(_GROWTH_ADVISOR_POLICY_PAYLOAD)
        fingerprint = growth_hash_json(_GROWTH_ADVISOR_POLICY_PAYLOAD)
    except TypeError, ValueError, UnicodeError:
        raise GrowthAdvisorPolicyMismatchError() from None
    if (
        canonical != GROWTH_ADVISOR_POLICY_CANONICAL_JSON
        or fingerprint != GROWTH_ADVISOR_POLICY_FINGERPRINT
    ):
        raise GrowthAdvisorPolicyMismatchError()
    return fingerprint


def _normalize_assistant_goal(value: object) -> str:
    """Use the existing Assistant validator for one Goal projection."""

    if type(value) is not str:
        raise AssistantInvalidRequestError()
    validated = validate_assistant_request(
        AssistantRequest(
            task=_GROWTH_ADVISOR_PROJECTION_TASK,
            explicit_goals=(value,),
        )
    )
    return validated.explicit_goals[0]


def _contains_forbidden_codepoint(value: str) -> bool:
    return any(
        ord(character) < 0x20
        or 0x7F <= ord(character) <= 0x9F
        or unicodedata.category(character) == "Cf"
        for character in value
    )


def _project_goal_text(value: object) -> str:
    """Project claim text using only the Assistant normalization contract."""

    if type(value) is not str:
        raise GrowthAdvisorGoalTextUnsupportedError()
    if _contains_forbidden_codepoint(value):
        raise GrowthAdvisorGoalTextUnsupportedError()
    normalized = unicodedata.normalize("NFC", value).strip()
    if not normalized or _contains_forbidden_codepoint(normalized):
        raise GrowthAdvisorGoalTextUnsupportedError()
    try:
        normalized_bytes = normalized.encode("utf-8")
    except UnicodeEncodeError:
        raise GrowthAdvisorGoalTextUnsupportedError() from None
    if len(normalized_bytes) > MAX_GOAL_BYTES:
        raise GrowthAdvisorGoalTextTooLargeError()
    try:
        projected = _normalize_assistant_goal(value)
    except AssistantInvalidRequestError:
        raise GrowthAdvisorGoalTextUnsupportedError() from None
    if projected != normalized:
        raise GrowthAdvisorGoalTextUnsupportedError()
    if len(projected.encode("utf-8")) > MAX_GOAL_BYTES:
        raise GrowthAdvisorGoalTextTooLargeError()
    return projected


@dataclass(frozen=True, slots=True)
class GrowthAdvisorRequestV1:
    """Exact caller-owned request; current Goal text is intentionally absent."""

    contract_version: str
    goal_source_uuid: UUID | str
    goal_identity_fingerprint: GrowthAdvisorHashV1
    task: str
    options: tuple[AssistantOption, ...] = ()
    explicit_constraints: tuple[str, ...] = ()
    explicit_context: tuple[AssistantExplicitContext, ...] = ()
    max_context_bytes: int = 64 * 1024
    max_result_bytes: int = 64 * 1024

    def __post_init__(self) -> None:
        if type(self.contract_version) is not str:
            raise ValueError("Growth Advisor contract version is invalid")
        if self.contract_version != GROWTH_ADVISOR_CONTRACT_VERSION:
            raise ValueError("Growth Advisor contract version is invalid")
        try:
            source_uuid = parse_uuid7(self.goal_source_uuid)
            validate_growth_hash(self.goal_identity_fingerprint)
            validated = validate_assistant_request(
                AssistantRequest(
                    task=self.task,
                    options=self.options,
                    explicit_constraints=self.explicit_constraints,
                    explicit_context=self.explicit_context,
                    max_context_bytes=self.max_context_bytes,
                    max_result_bytes=self.max_result_bytes,
                )
            )
        except AssistantError, TypeError, ValueError, UnicodeError:
            raise ValueError("Growth Advisor request is invalid") from None
        object.__setattr__(self, "goal_source_uuid", source_uuid)
        object.__setattr__(self, "task", validated.task)
        object.__setattr__(self, "options", validated.options)
        object.__setattr__(self, "explicit_constraints", validated.explicit_constraints)
        object.__setattr__(self, "explicit_context", validated.explicit_context)
        object.__setattr__(self, "max_context_bytes", validated.max_context_bytes)
        object.__setattr__(self, "max_result_bytes", validated.max_result_bytes)

    @classmethod
    def from_dict(cls, value: object) -> GrowthAdvisorRequestV1:
        """Parse the exact JSON shape and reject unknown or duplicate fields."""

        fields = {
            "contract_version",
            "goal_source_uuid",
            "goal_identity_fingerprint",
            "task",
            "options",
            "explicit_constraints",
            "explicit_context",
            "max_context_bytes",
            "max_result_bytes",
        }
        try:
            if not isinstance(value, Mapping) or set(value) != fields:
                raise ValueError
            options_value = value["options"]
            constraints_value = value["explicit_constraints"]
            context_value = value["explicit_context"]
            if (
                type(options_value) is not list
                or type(constraints_value) is not list
                or type(context_value) is not list
            ):
                raise ValueError
            options: list[AssistantOption] = []
            for item in options_value:
                if not isinstance(item, Mapping) or set(item) != {"id", "label"}:
                    raise ValueError
                if type(item["id"]) is not str or type(item["label"]) is not str:
                    raise ValueError
                options.append(AssistantOption(id=item["id"], label=item["label"]))
            constraints: list[str] = []
            for item in constraints_value:
                if type(item) is not str:
                    raise ValueError
                constraints.append(item)
            context: list[AssistantExplicitContext] = []
            for item in context_value:
                if not isinstance(item, Mapping) or set(item) != {"kind", "text"}:
                    raise ValueError
                if type(item["kind"]) is not str or type(item["text"]) is not str:
                    raise ValueError
                context.append(AssistantExplicitContext(kind=item["kind"], text=item["text"]))
            for key in (
                "contract_version",
                "goal_source_uuid",
                "goal_identity_fingerprint",
                "task",
            ):
                if type(value[key]) is not str:
                    raise ValueError
            if (
                type(value["max_context_bytes"]) is not int
                or type(value["max_result_bytes"]) is not int
            ):
                raise ValueError
            return cls(
                contract_version=value["contract_version"],
                goal_source_uuid=value["goal_source_uuid"],
                goal_identity_fingerprint=value["goal_identity_fingerprint"],
                task=value["task"],
                options=tuple(options),
                explicit_constraints=tuple(constraints),
                explicit_context=tuple(context),
                max_context_bytes=value["max_context_bytes"],
                max_result_bytes=value["max_result_bytes"],
            )
        except KeyError, TypeError, ValueError, UnicodeError:
            raise GrowthAdvisorInvalidRequestError() from None

    def as_dict(self) -> dict[str, object]:
        """Return the exact transport projection without server Goal authority."""

        return {
            "contract_version": self.contract_version,
            "goal_source_uuid": str(self.goal_source_uuid),
            "goal_identity_fingerprint": self.goal_identity_fingerprint,
            "task": self.task,
            "options": [{"id": item.id, "label": item.label} for item in self.options],
            "explicit_constraints": list(self.explicit_constraints),
            "explicit_context": [
                {
                    "kind": item.kind.value if isinstance(item.kind, StrEnum) else item.kind,
                    "text": item.text,
                }
                for item in self.explicit_context
            ],
            "max_context_bytes": self.max_context_bytes,
            "max_result_bytes": self.max_result_bytes,
        }

    def to_json(self) -> str:
        """Serialize only the bounded caller request for deterministic tests."""

        return canonical_growth_json(self.as_dict())


def validate_growth_advisor_request(value: object) -> GrowthAdvisorRequestV1:
    """Revalidate an exact in-memory request before every operation."""

    if type(value) is not GrowthAdvisorRequestV1:
        raise GrowthAdvisorInvalidRequestError()
    try:
        return GrowthAdvisorRequestV1(
            contract_version=value.contract_version,
            goal_source_uuid=value.goal_source_uuid,
            goal_identity_fingerprint=value.goal_identity_fingerprint,
            task=value.task,
            options=value.options,
            explicit_constraints=value.explicit_constraints,
            explicit_context=value.explicit_context,
            max_context_bytes=value.max_context_bytes,
            max_result_bytes=value.max_result_bytes,
        )
    except TypeError, ValueError, UnicodeError:
        raise GrowthAdvisorInvalidRequestError() from None


@dataclass(frozen=True, slots=True)
class GrowthAdvisorGoalPreviewV1:
    """Transient owner-facing preview of the exact Assistant Goal value."""

    contract_version: str
    goal_source_uuid: UUID | str
    goal_identity_fingerprint: GrowthAdvisorHashV1
    assistant_contract_version: str
    advisor_policy_id: str
    goal_text: str
    goal_text_utf8_bytes: int

    def __post_init__(self) -> None:
        try:
            source_uuid = parse_uuid7(self.goal_source_uuid)
            validate_growth_hash(self.goal_identity_fingerprint)
            if (
                type(self.contract_version) is not str
                or self.contract_version != GROWTH_ADVISOR_CONTRACT_VERSION
                or type(self.assistant_contract_version) is not str
                or self.assistant_contract_version != ASSISTANT_CONTRACT_VERSION
                or type(self.advisor_policy_id) is not str
                or self.advisor_policy_id != GROWTH_ADVISOR_POLICY_ID
                or type(self.goal_text_utf8_bytes) is not int
            ):
                raise ValueError
            projected = _normalize_assistant_goal(self.goal_text)
            if projected != self.goal_text:
                raise ValueError
            encoded_size = len(projected.encode("utf-8"))
            if encoded_size != self.goal_text_utf8_bytes or not 1 <= encoded_size <= MAX_GOAL_BYTES:
                raise ValueError
        except AssistantError, TypeError, ValueError, UnicodeError:
            raise ValueError("Growth Advisor preview is invalid") from None
        object.__setattr__(self, "goal_source_uuid", source_uuid)

    @classmethod
    def from_dict(cls, value: object) -> GrowthAdvisorGoalPreviewV1:
        """Parse an exact transient preview projection."""

        fields = {
            "contract_version",
            "goal_source_uuid",
            "goal_identity_fingerprint",
            "assistant_contract_version",
            "advisor_policy_id",
            "goal_text",
            "goal_text_utf8_bytes",
        }
        try:
            if not isinstance(value, Mapping) or set(value) != fields:
                raise ValueError
            string_fields = (
                "contract_version",
                "goal_source_uuid",
                "goal_identity_fingerprint",
                "assistant_contract_version",
                "advisor_policy_id",
                "goal_text",
            )
            if any(type(value[key]) is not str for key in string_fields):
                raise ValueError
            if type(value["goal_text_utf8_bytes"]) is not int:
                raise ValueError
            return cls(
                contract_version=value["contract_version"],
                goal_source_uuid=value["goal_source_uuid"],
                goal_identity_fingerprint=value["goal_identity_fingerprint"],
                assistant_contract_version=value["assistant_contract_version"],
                advisor_policy_id=value["advisor_policy_id"],
                goal_text=value["goal_text"],
                goal_text_utf8_bytes=value["goal_text_utf8_bytes"],
            )
        except KeyError, TypeError, ValueError, UnicodeError:
            raise GrowthAdvisorInvalidRequestError() from None

    def as_dict(self) -> dict[str, object]:
        """Return the exact transient preview shape."""

        return {
            "contract_version": self.contract_version,
            "goal_source_uuid": str(self.goal_source_uuid),
            "goal_identity_fingerprint": self.goal_identity_fingerprint,
            "assistant_contract_version": self.assistant_contract_version,
            "advisor_policy_id": self.advisor_policy_id,
            "goal_text": self.goal_text,
            "goal_text_utf8_bytes": self.goal_text_utf8_bytes,
        }


def validate_growth_advisor_preview(value: object) -> GrowthAdvisorGoalPreviewV1:
    """Revalidate an exact in-memory preview before execution."""

    if type(value) is not GrowthAdvisorGoalPreviewV1:
        raise GrowthAdvisorInvalidRequestError()
    try:
        return GrowthAdvisorGoalPreviewV1(
            contract_version=value.contract_version,
            goal_source_uuid=value.goal_source_uuid,
            goal_identity_fingerprint=value.goal_identity_fingerprint,
            assistant_contract_version=value.assistant_contract_version,
            advisor_policy_id=value.advisor_policy_id,
            goal_text=value.goal_text,
            goal_text_utf8_bytes=value.goal_text_utf8_bytes,
        )
    except TypeError, ValueError, UnicodeError:
        raise GrowthAdvisorInvalidRequestError() from None


def growth_goal_identity_fingerprint(identity: GrowthGoalIdentityV1) -> GrowthAdvisorHashV1:
    """Fingerprint the canonical raw-body-free Goal identity object."""

    if type(identity) is not GrowthGoalIdentityV1:
        raise ValueError("Goal identity is invalid")
    return growth_hash_json(identity.as_dict())


@dataclass(frozen=True, slots=True)
class GrowthAdvisorResultRefV1:
    """Compact transient provenance without raw Goal, prompt or result data."""

    request_id_fingerprint: GrowthAdvisorHashV1
    result_kind: str
    advisor_policy_id: str
    advisor_policy_fingerprint: GrowthAdvisorHashV1
    assistant_contract_version: str
    assistant_context_policy: str
    goal_source_uuid: UUID | str
    goal_identity_fingerprint: GrowthAdvisorHashV1
    requested_at: datetime
    generated_at: datetime

    def __post_init__(self) -> None:
        try:
            validate_growth_hash(self.request_id_fingerprint)
            validate_growth_hash(self.advisor_policy_fingerprint)
            validate_growth_hash(self.goal_identity_fingerprint)
            source_uuid = parse_uuid7(self.goal_source_uuid)
            if (
                type(self.result_kind) is not str
                or self.result_kind != GROWTH_ADVISOR_RESULT_KIND
                or type(self.advisor_policy_id) is not str
                or self.advisor_policy_id != GROWTH_ADVISOR_POLICY_ID
                or type(self.assistant_contract_version) is not str
                or self.assistant_contract_version != ASSISTANT_CONTRACT_VERSION
                or type(self.assistant_context_policy) is not str
                or self.assistant_context_policy != GROWTH_ADVISOR_ASSISTANT_CONTEXT_POLICY
                or self.advisor_policy_fingerprint != GROWTH_ADVISOR_POLICY_FINGERPRINT
            ):
                raise ValueError
            requested_at = _normalize_datetime(self.requested_at)
            generated_at = _normalize_datetime(self.generated_at)
            if generated_at < requested_at:
                raise ValueError
        except TypeError, ValueError, UnicodeError:
            raise ValueError("Growth Advisor provenance is invalid") from None
        object.__setattr__(self, "goal_source_uuid", source_uuid)
        object.__setattr__(self, "requested_at", requested_at)
        object.__setattr__(self, "generated_at", generated_at)

    @classmethod
    def from_dict(cls, value: object) -> GrowthAdvisorResultRefV1:
        """Parse the exact compact provenance shape."""

        fields = {
            "request_id_fingerprint",
            "result_kind",
            "advisor_policy_id",
            "advisor_policy_fingerprint",
            "assistant_contract_version",
            "assistant_context_policy",
            "goal_source_uuid",
            "goal_identity_fingerprint",
            "requested_at",
            "generated_at",
        }
        try:
            if not isinstance(value, Mapping) or set(value) != fields:
                raise ValueError
            if any(type(value[key]) is not str for key in fields):
                raise ValueError
            return cls(
                request_id_fingerprint=value["request_id_fingerprint"],
                result_kind=value["result_kind"],
                advisor_policy_id=value["advisor_policy_id"],
                advisor_policy_fingerprint=value["advisor_policy_fingerprint"],
                assistant_contract_version=value["assistant_contract_version"],
                assistant_context_policy=value["assistant_context_policy"],
                goal_source_uuid=value["goal_source_uuid"],
                goal_identity_fingerprint=value["goal_identity_fingerprint"],
                requested_at=parse_rfc3339(value["requested_at"]),
                generated_at=parse_rfc3339(value["generated_at"]),
            )
        except KeyError, TypeError, ValueError, UnicodeError:
            raise GrowthAdvisorInvalidRequestError() from None

    def as_dict(self) -> dict[str, object]:
        """Return only the bounded provenance reference."""

        return {
            "request_id_fingerprint": self.request_id_fingerprint,
            "result_kind": self.result_kind,
            "advisor_policy_id": self.advisor_policy_id,
            "advisor_policy_fingerprint": self.advisor_policy_fingerprint,
            "assistant_contract_version": self.assistant_contract_version,
            "assistant_context_policy": self.assistant_context_policy,
            "goal_source_uuid": str(self.goal_source_uuid),
            "goal_identity_fingerprint": self.goal_identity_fingerprint,
            "requested_at": _format_datetime(self.requested_at),
            "generated_at": _format_datetime(self.generated_at),
        }


@dataclass(frozen=True, slots=True)
class GrowthAdvisorErrorV1:
    """Exact fixed safe error DTO used by the transient error branch."""

    code: GrowthAdvisorErrorCode | str
    message: str | None = None

    def __post_init__(self) -> None:
        normalized = _normalize_error_code(self.code)
        if type(self.code) is not GrowthAdvisorErrorCode and type(self.code) is not str:
            raise ValueError("Growth Advisor error code is invalid")
        object.__setattr__(self, "code", normalized)
        object.__setattr__(self, "message", _GROWTH_ADVISOR_ERROR_MESSAGES[normalized])

    def as_dict(self) -> dict[str, str]:
        """Return the fixed public error shape."""

        code = _normalize_error_code(self.code)
        return {"code": code.value, "message": _GROWTH_ADVISOR_ERROR_MESSAGES[code]}


class GrowthAdvisorBranchStateV1(StrEnum):
    """Closed state set for the independent transient branch."""

    RESULT = "result"
    ABSTENTION = "abstention"
    ERROR = "error"


GrowthAdvisorBranchState = GrowthAdvisorBranchStateV1


@dataclass(frozen=True, slots=True)
class GrowthAdvisorBranchV1:
    """Full ephemeral Advisor branch kept separate from deterministic Growth."""

    branch: str
    state: GrowthAdvisorBranchStateV1 | str
    assistant_result: AssistantResultEnvelopeV1 | None
    error: GrowthAdvisorErrorV1 | None
    provenance: GrowthAdvisorResultRefV1 | None

    def __post_init__(self) -> None:
        if type(self.branch) is not str or self.branch != "advisor":
            raise ValueError("Growth Advisor branch is invalid")
        if type(self.state) is GrowthAdvisorBranchStateV1:
            state = self.state
        elif type(self.state) is str:
            try:
                state = GrowthAdvisorBranchStateV1(self.state)
            except ValueError:
                raise ValueError("Growth Advisor branch state is invalid") from None
        else:
            raise ValueError("Growth Advisor branch state is invalid")
        result = self.assistant_result
        if result is not None:
            try:
                result = normalize_assistant_result_structure(result)
            except AssistantError:
                raise ValueError("Growth Advisor result is invalid") from None
        if self.error is not None and type(self.error) is not GrowthAdvisorErrorV1:
            raise ValueError("Growth Advisor error is invalid")
        if self.provenance is not None and type(self.provenance) is not GrowthAdvisorResultRefV1:
            raise ValueError("Growth Advisor provenance is invalid")
        if state is GrowthAdvisorBranchStateV1.ERROR:
            if result is not None or self.error is None or self.provenance is not None:
                raise ValueError("Growth Advisor error branch is inconsistent")
        else:
            if result is None or self.error is not None or self.provenance is None:
                raise ValueError("Growth Advisor result branch is incomplete")
            result_kind = result.kind.value if isinstance(result.kind, StrEnum) else result.kind
            expected = (
                GrowthAdvisorBranchStateV1.ABSTENTION
                if result_kind == AssistantResultKind.ABSTENTION.value
                else GrowthAdvisorBranchStateV1.RESULT
            )
            if state is not expected:
                raise ValueError("Growth Advisor result branch state is inconsistent")
        object.__setattr__(self, "state", state)
        object.__setattr__(self, "assistant_result", result)

    def as_dict(self) -> dict[str, object]:
        """Return the exact full transient branch projection."""

        assistant_result: dict[str, object] | None = None
        if self.assistant_result is not None:
            decoded = json.loads(
                serialize_assistant_result_envelope(self.assistant_result).decode("utf-8")
            )
            if type(decoded) is not dict:
                raise ValueError("Assistant result serializer returned a non-object")
            assistant_result = decoded
        return {
            "branch": self.branch,
            "state": cast(GrowthAdvisorBranchStateV1, self.state).value,
            "assistant_result": assistant_result,
            "error": self.error.as_dict() if self.error is not None else None,
            "provenance": self.provenance.as_dict() if self.provenance is not None else None,
        }

    def to_json(self) -> str:
        """Serialize the branch without adding any transport or persistence state."""

        return canonical_growth_json(self.as_dict())


def validate_growth_advisor_branch(value: object) -> GrowthAdvisorBranchV1:
    """Revalidate a transient branch before it crosses the Web boundary."""

    if type(value) is not GrowthAdvisorBranchV1:
        raise GrowthAdvisorInvalidResultError()
    try:
        return GrowthAdvisorBranchV1(
            branch=value.branch,
            state=value.state,
            assistant_result=value.assistant_result,
            error=value.error,
            provenance=value.provenance,
        )
    except TypeError, ValueError, AssistantError:
        raise GrowthAdvisorInvalidResultError() from None


def serialize_growth_advisor_branch(value: object) -> bytes:
    """Serialize one validated branch as compact UTF-8 JSON bytes."""

    validated = validate_growth_advisor_branch(value)
    try:
        return canonical_growth_json(validated.as_dict()).encode("utf-8")
    except TypeError, ValueError, UnicodeError:
        raise GrowthAdvisorInvalidResultError() from None


@dataclass(frozen=True, slots=True)
class GrowthAdvisorExecutionContextV1:
    """Non-serialized cancellation/deadline control passed only in memory."""

    cancellation: CancellationToken
    deadline: float

    def __post_init__(self) -> None:
        if not callable(getattr(self.cancellation, "is_cancelled", None)):
            raise ValueError("Growth Advisor cancellation token is invalid")
        if type(self.deadline) is not float or not math.isfinite(self.deadline):
            raise ValueError("Growth Advisor deadline is invalid")


@dataclass(frozen=True, slots=True)
class _CurrentGoal:
    identity: GrowthGoalIdentityV1
    claim_text: str


@dataclass(frozen=True, slots=True)
class _SnapshotReader:
    snapshot: VaultSnapshot

    def scan(self) -> VaultSnapshot:
        return self.snapshot


def _normalize_datetime(value: object) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("timestamp must be aware")
    return value.astimezone(UTC)


def _format_datetime(value: datetime) -> str:
    return _normalize_datetime(value).isoformat().replace("+00:00", "Z")


def _assistant_request(
    request: GrowthAdvisorRequestV1,
    goal_text: str,
) -> AssistantRequest:
    """Build the ordinary Assistant request with exactly one server Goal."""

    return AssistantRequest(
        task=request.task,
        options=request.options,
        explicit_constraints=request.explicit_constraints,
        explicit_goals=(goal_text,),
        explicit_context=request.explicit_context,
        max_context_bytes=request.max_context_bytes,
        max_result_bytes=request.max_result_bytes,
    )


def _assistant_error(error: AssistantError) -> GrowthAdvisorError:
    mapping: dict[str, Callable[[], GrowthAdvisorError]] = {
        "ASSISTANT_INVALID_REQUEST": GrowthAdvisorInvalidRequestError,
        "ASSISTANT_CANCELLED": GrowthAdvisorCancelledError,
        "ASSISTANT_TIMEOUT": GrowthAdvisorTimeoutError,
        "ASSISTANT_PROVIDER_UNAVAILABLE": GrowthRecommendationUnavailableError,
        "ASSISTANT_PROVIDER_FAILURE": GrowthAdvisorFailureError,
        "ASSISTANT_MALFORMED_RESULT": GrowthAdvisorInvalidResultError,
        "ASSISTANT_RESULT_TOO_LARGE": GrowthAdvisorResultTooLargeError,
        "ASSISTANT_RESULT_INVALID": GrowthAdvisorInvalidResultError,
    }
    error_type = mapping.get(error.code, GrowthAdvisorFailureError)
    return error_type()


def _error_branch(error: GrowthAdvisorError) -> GrowthAdvisorBranchV1:
    return GrowthAdvisorBranchV1(
        branch="advisor",
        state=GrowthAdvisorBranchStateV1.ERROR,
        assistant_result=None,
        error=GrowthAdvisorErrorV1(error.code),
        provenance=None,
    )


@dataclass(slots=True)
class BuildGrowthAdvisor:
    """Execute one explicit-owner Growth Advisor operation."""

    reader: VaultReader
    advisor: AdvisorPort
    policy: SelfModelPolicy = DEFAULT_SELF_MODEL_POLICY
    clock: GrowthAdvisorClock = lambda: datetime.now(UTC)
    monotonic_clock: GrowthAdvisorMonotonicClock = time.monotonic
    request_id_factory: GrowthAdvisorRequestIdFactory = uuid7

    def preview(
        self,
        request: object,
        *,
        cancellation: CancellationToken | None = None,
    ) -> GrowthAdvisorGoalPreviewV1:
        """Rebuild one current Goal without invoking the Advisor provider."""

        validated_request = validate_growth_advisor_request(request)
        token = cancellation or CancellationTokenSource().token
        if token.is_cancelled():
            raise GrowthAdvisorCancelledError()
        self._validate_policies()
        current = self._current_goal(parse_uuid7(validated_request.goal_source_uuid))
        current_fingerprint = growth_goal_identity_fingerprint(current.identity)
        if validated_request.goal_identity_fingerprint != current_fingerprint:
            raise GrowthAdvisorGoalChangedError()
        if token.is_cancelled():
            raise GrowthAdvisorCancelledError()
        goal_text = _project_goal_text(current.claim_text)
        return GrowthAdvisorGoalPreviewV1(
            contract_version=GROWTH_ADVISOR_CONTRACT_VERSION,
            goal_source_uuid=current.identity.source_note_uuid,
            goal_identity_fingerprint=current_fingerprint,
            assistant_contract_version=ASSISTANT_CONTRACT_VERSION,
            advisor_policy_id=GROWTH_ADVISOR_POLICY_ID,
            goal_text=goal_text,
            goal_text_utf8_bytes=len(goal_text.encode("utf-8")),
        )

    build_preview = preview

    def execute(
        self,
        request: object,
        preview: object,
        *,
        confirmed: bool = True,
        cancellation: CancellationToken | None = None,
    ) -> GrowthAdvisorBranchV1:
        """Execute one confirmed operation and return only a transient branch."""

        try:
            return self._execute(
                request,
                preview,
                confirmed=confirmed,
                cancellation=cancellation,
            )
        except GrowthAdvisorError as error:
            return _error_branch(error)
        except Exception:
            return _error_branch(GrowthAdvisorFailureError())

    build = execute

    def _execute(
        self,
        request: object,
        preview: object,
        *,
        confirmed: bool,
        cancellation: CancellationToken | None,
    ) -> GrowthAdvisorBranchV1:
        validated_request = validate_growth_advisor_request(request)
        validated_preview = validate_growth_advisor_preview(preview)
        if type(confirmed) is not bool or not confirmed:
            raise GrowthAdvisorInvalidRequestError()
        token = cancellation or CancellationTokenSource().token
        if token.is_cancelled():
            raise GrowthAdvisorCancelledError()
        try:
            start = float(self.monotonic_clock())
        except TypeError, ValueError, OverflowError:
            raise GrowthAdvisorFailureError() from None
        if not math.isfinite(start):
            raise GrowthAdvisorFailureError()
        execution = GrowthAdvisorExecutionContextV1(
            cancellation=token,
            deadline=start + GROWTH_ADVISOR_TOTAL_DEADLINE_SECONDS,
        )
        requested_at = self._read_datetime()
        self._check_execution(execution)
        self._validate_policies()
        self._check_execution(execution)
        current = self._current_goal(parse_uuid7(validated_request.goal_source_uuid))
        current_fingerprint = growth_goal_identity_fingerprint(current.identity)
        if (
            validated_preview.goal_source_uuid != validated_request.goal_source_uuid
            or validated_preview.goal_identity_fingerprint
            != validated_request.goal_identity_fingerprint
            or validated_request.goal_identity_fingerprint != current_fingerprint
            or validated_preview.goal_identity_fingerprint != current_fingerprint
        ):
            raise GrowthAdvisorGoalChangedError()
        if (
            validated_preview.assistant_contract_version != ASSISTANT_CONTRACT_VERSION
            or validated_preview.advisor_policy_id != GROWTH_ADVISOR_POLICY_ID
        ):
            raise GrowthAdvisorPolicyMismatchError()
        self._check_execution(execution)
        goal_text = _project_goal_text(current.claim_text)
        if goal_text != validated_preview.goal_text:
            raise GrowthAdvisorGoalChangedError()
        assistant_request = self._build_assistant_request(validated_request, goal_text)
        self._check_execution(execution)
        try:
            result = BuildAssistant(self.advisor).execute(
                assistant_request,
                cancellation=execution.cancellation,
            )
        except AssistantError as error:
            if execution.cancellation.is_cancelled():
                raise GrowthAdvisorCancelledError() from None
            self._check_deadline_only(execution)
            raise _assistant_error(error) from None
        self._check_execution(execution)
        generated_at = self._read_datetime()
        request_id = self.request_id_factory()
        if type(request_id) is not UUID or request_id.version != 7:
            raise GrowthAdvisorFailureError()
        request_id_fingerprint = growth_hash_text(str(request_id))
        provenance = GrowthAdvisorResultRefV1(
            request_id_fingerprint=request_id_fingerprint,
            result_kind=GROWTH_ADVISOR_RESULT_KIND,
            advisor_policy_id=GROWTH_ADVISOR_POLICY_ID,
            advisor_policy_fingerprint=GROWTH_ADVISOR_POLICY_FINGERPRINT,
            assistant_contract_version=ASSISTANT_CONTRACT_VERSION,
            assistant_context_policy=GROWTH_ADVISOR_ASSISTANT_CONTEXT_POLICY,
            goal_source_uuid=current.identity.source_note_uuid,
            goal_identity_fingerprint=current_fingerprint,
            requested_at=requested_at,
            generated_at=generated_at,
        )
        state = (
            GrowthAdvisorBranchStateV1.ABSTENTION
            if result.kind is AssistantResultKind.ABSTENTION
            else GrowthAdvisorBranchStateV1.RESULT
        )
        branch = GrowthAdvisorBranchV1(
            branch="advisor",
            state=state,
            assistant_result=result,
            error=None,
            provenance=provenance,
        )
        self._check_execution(execution)
        return branch

    def _validate_policies(self) -> None:
        try:
            validate_growth_advisor_policy()
            validate_growth_policy()
            validate_self_model_policy(self.policy)
        except GrowthError, SelfModelError, TypeError, ValueError:
            raise GrowthAdvisorPolicyMismatchError() from None

    def _current_goal(self, source_uuid: UUID) -> _CurrentGoal:
        try:
            policy_fingerprint = validate_self_model_policy(self.policy)
            snapshot = self.reader.scan()
            if type(snapshot) is not VaultSnapshot:
                raise GrowthAdvisorGoalUnavailableError()
            report = build_report(snapshot)
            if type(report) is not ScanReport or report.manifest is None:
                raise GrowthAdvisorGoalUnavailableError()
            self_model = BuildSelfModel(
                _SnapshotReader(snapshot),
                policy=self.policy,
                clock=self.clock,
            ).execute(
                SelfModelRequest(
                    max_claims=MAX_SELF_MODEL_LIMIT,
                    max_evidence_refs_per_claim=MAX_SELF_MODEL_LIMIT,
                )
            )
            if type(self_model) is not SelfModelResult:
                raise GrowthAdvisorGoalUnavailableError()
            identities: list[tuple[GrowthGoalIdentityV1, SelfModelClaim]] = []
            for claim in self_model.claims:
                if claim.dimension.value != SelfKind.GOAL.value:
                    continue
                identity = build_growth_goal_identity(
                    claim,
                    self_model,
                    policy=self.policy,
                    expected_self_model_policy_fingerprint=policy_fingerprint,
                )
                identities.append((identity, claim))
        except GrowthAdvisorError:
            raise
        except SelfModelInvalidRequestError, SelfModelInvalidClockError:
            raise GrowthAdvisorInvalidRequestError() from None
        except SelfModelPolicyUnavailableError:
            raise GrowthAdvisorPolicyMismatchError() from None
        except GrowthGoalSourceChangedError:
            raise GrowthAdvisorGoalChangedError() from None
        except GrowthPolicyMismatchError:
            raise GrowthAdvisorPolicyMismatchError() from None
        except GrowthGoalSourceUnavailableError, GrowthResultTooLargeError:
            raise GrowthAdvisorGoalUnavailableError() from None
        except (
            SelfModelVaultUnavailableError,
            SelfModelResultInvalidError,
            SelfModelResultTooLargeError,
            SelfModelError,
        ):
            raise GrowthAdvisorGoalUnavailableError() from None
        except GrowthError, TypeError, ValueError, UnicodeError:
            raise GrowthAdvisorGoalChangedError() from None
        matches = [item for item in identities if item[0].source_note_uuid == source_uuid]
        if len(matches) == 0:
            raise GrowthAdvisorGoalMissingError()
        if len(matches) != 1:
            raise GrowthAdvisorGoalChangedError()
        identity, claim = matches[0]
        if type(claim.claim) is not str:
            raise GrowthAdvisorGoalChangedError()
        return _CurrentGoal(identity=identity, claim_text=claim.claim)

    def _build_assistant_request(
        self,
        request: GrowthAdvisorRequestV1,
        goal_text: str,
    ) -> AssistantRequest:
        try:
            assistant_request = validate_assistant_request(_assistant_request(request, goal_text))
            envelope = build_assistant_reasoning_envelope(assistant_request)
            if len(serialize_assistant_reasoning_envelope(envelope)) > request.max_context_bytes:
                raise GrowthAdvisorContextTooLargeError()
            return assistant_request
        except GrowthAdvisorError:
            raise
        except AssistantInvalidRequestError:
            raise GrowthAdvisorInvalidRequestError() from None
        except TypeError, ValueError, UnicodeError:
            raise GrowthAdvisorInvalidRequestError() from None

    def _read_datetime(self) -> datetime:
        try:
            return _normalize_datetime(self.clock())
        except TypeError, ValueError, UnicodeError:
            raise GrowthAdvisorFailureError() from None

    def _check_deadline_only(self, execution: GrowthAdvisorExecutionContextV1) -> None:
        try:
            current = float(self.monotonic_clock())
        except TypeError, ValueError, OverflowError:
            raise GrowthAdvisorTimeoutError() from None
        if not math.isfinite(current) or current >= execution.deadline:
            raise GrowthAdvisorTimeoutError()

    def _check_execution(self, execution: GrowthAdvisorExecutionContextV1) -> None:
        if execution.cancellation.is_cancelled():
            raise GrowthAdvisorCancelledError()
        self._check_deadline_only(execution)


GrowthAdvisorRuntime = BuildGrowthAdvisor


__all__ = [
    "GROWTH_ADVISOR_ASSISTANT_CONTEXT_POLICY",
    "GROWTH_ADVISOR_CONTRACT_VERSION",
    "GROWTH_ADVISOR_POLICY_CANONICAL_JSON",
    "GROWTH_ADVISOR_POLICY_FINGERPRINT",
    "GROWTH_ADVISOR_POLICY_ID",
    "GROWTH_ADVISOR_RESULT_KIND",
    "GROWTH_ADVISOR_TOTAL_DEADLINE_SECONDS",
    "BuildGrowthAdvisor",
    "GrowthAdvisorBranchState",
    "GrowthAdvisorBranchStateV1",
    "GrowthAdvisorBranchV1",
    "GrowthAdvisorCancelledError",
    "GrowthAdvisorContextTooLargeError",
    "GrowthAdvisorError",
    "GrowthAdvisorErrorCode",
    "GrowthAdvisorErrorCodeV1",
    "GrowthAdvisorErrorV1",
    "GrowthAdvisorExecutionContextV1",
    "GrowthAdvisorFailureError",
    "GrowthAdvisorGoalChangedError",
    "GrowthAdvisorGoalMissingError",
    "GrowthAdvisorGoalPreviewV1",
    "GrowthAdvisorGoalTextTooLargeError",
    "GrowthAdvisorGoalTextUnsupportedError",
    "GrowthAdvisorGoalUnavailableError",
    "GrowthAdvisorInvalidRequestError",
    "GrowthAdvisorInvalidResultError",
    "GrowthAdvisorPolicyMismatchError",
    "GrowthAdvisorRequestV1",
    "GrowthAdvisorResultRefV1",
    "GrowthAdvisorResultTooLargeError",
    "GrowthAdvisorRuntime",
    "GrowthAdvisorTimeoutError",
    "GrowthRecommendationUnavailableError",
    "growth_goal_identity_fingerprint",
    "serialize_growth_advisor_branch",
    "validate_growth_advisor_branch",
    "validate_growth_advisor_policy",
    "validate_growth_advisor_preview",
    "validate_growth_advisor_request",
]
