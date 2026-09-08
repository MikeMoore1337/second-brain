"""Provider-free Compare v1 composition with a structural-only Delta."""

from __future__ import annotations

import hashlib
import json
import math
import re
import time
import unicodedata
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import Final, Protocol, cast
from uuid import UUID

from second_brain.application.assistant import (
    AssistantCanonicalJsonEncoderV1,
    AssistantError,
    AssistantErrorCode,
    AssistantExplicitContext,
    AssistantOption,
    AssistantReasoningEnvelopeV1,
    AssistantRequest,
    AssistantResultEnvelopeV1,
    AssistantResultKind,
    build_assistant_reasoning_envelope,
    serialize_assistant_reasoning_envelope,
    serialize_assistant_result_envelope,
    validate_assistant_request,
    validate_assistant_result,
)
from second_brain.application.ports import CancellationToken
from second_brain.application.simulate_me import (
    DERIVATION_VERSION as SIMULATE_ME_DERIVATION_VERSION,
)
from second_brain.application.simulate_me import (
    MAX_NOTE_IDS_PER_REF as MAX_SIMULATE_ME_NOTE_IDS_PER_REF,
)
from second_brain.application.simulate_me import (
    MAX_RESULT_REFS as MAX_SIMULATE_ME_RESULT_REFS,
)
from second_brain.application.simulate_me import (
    POLICY_FINGERPRINT as SIMULATE_ME_POLICY_FINGERPRINT,
)
from second_brain.application.simulate_me import (
    POLICY_ID as SIMULATE_ME_POLICY_ID,
)
from second_brain.application.simulate_me import (
    SimulateMeAbstentionCode,
    SimulateMeContextualEvidenceRef,
    SimulateMeDimension,
    SimulateMeError,
    SimulateMeErrorCode,
    SimulateMeEvidenceRef,
    SimulateMeOption,
    SimulateMeRequest,
    SimulateMeResult,
    SimulateMeResultKind,
    SimulateMeTemporalCaveat,
    SimulateMeTemporalCaveatCode,
    validate_simulate_me_request,
    validate_simulate_me_result,
)

DERIVATION_VERSION: Final[str] = "compare-v1"
POLICY_ID: Final[str] = "compare-structural-delta-v1"
COMPARE_DERIVATION_VERSION: Final[str] = DERIVATION_VERSION
COMPARE_POLICY_ID: Final[str] = POLICY_ID

DEFAULT_ASSISTANT_MAX_CONTEXT_BYTES: Final[int] = 64 * 1024
DEFAULT_ASSISTANT_MAX_RESULT_BYTES: Final[int] = 64 * 1024
DEFAULT_MAX_RESULT_BYTES: Final[int] = 128 * 1024
MAX_ASSISTANT_CONTEXT_BYTES: Final[int] = DEFAULT_ASSISTANT_MAX_CONTEXT_BYTES
MAX_ASSISTANT_RESULT_BYTES: Final[int] = DEFAULT_ASSISTANT_MAX_RESULT_BYTES
MAX_RESULT_BYTES: Final[int] = DEFAULT_MAX_RESULT_BYTES
MIN_ASSISTANT_CONTEXT_BYTES_V1: Final[int] = 115
MIN_MAX_RESULT_BYTES_V1: Final[int] = 824

MAX_TASK_BYTES: Final[int] = 4096
MAX_OPTIONS: Final[int] = 8
MAX_OPTION_ID_BYTES: Final[int] = 64
MAX_OPTION_LABEL_BYTES: Final[int] = 256
MAX_CONSTRAINTS: Final[int] = 16
MAX_CONSTRAINT_BYTES: Final[int] = 512
MAX_CONSTRAINTS_BYTES: Final[int] = 8192
MAX_GOALS: Final[int] = 8
MAX_GOAL_BYTES: Final[int] = 512
MAX_GOALS_BYTES: Final[int] = 4096
MAX_CONTEXT_ENTRIES: Final[int] = 16
MAX_CONTEXT_TEXT_BYTES: Final[int] = 1024
MAX_CONTEXT_TEXTS_BYTES: Final[int] = 16384

POLICY_CANONICAL_JSON: Final[str] = (
    '{"branch_inputs":"shared-task-options-only-v1",'
    '"branch_invocation":"one-independent-attempt-each-unless-operation-cancelled-or-'
    'deadline-expires-v2",'
    '"branch_output":"typed-result-abstention-error-v1",'
    '"delta_evidence":"preserve-namespaces-no-cross-comparison-v1",'
    '"delta_human_text":"fixed-templates-v1",'
    '"delta_option_equality":"exact-request-local-id-v1",'
    '"delta_relation_codes":["same_selected_option","different_selected_options",'
    '"assistant_only_selected","simulate_me_only_selected","neither_selected",'
    '"assistant_error","simulate_me_error","both_error"],'
    '"execution_context":"assistant-explicit-only;simulate-me-current-approved-context-only",'
    '"execution_control":"shared-monotonic-deadline-and-cancellation-token-v1",'
    '"no_side_effects":"ephemeral-read-only-v1","version":"1"}'
)
POLICY_FINGERPRINT: Final[str] = (
    "sha256:518da5bb49968fb22ba956b9291588c6e90fb32cd0d4458f6c17f6f26a40694e"
)

_ID_PATTERN: Final[re.Pattern[str]] = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,63}\Z", re.ASCII)


class CompareBranchStateV1(StrEnum):
    """Closed wrapper states for one Compare branch."""

    RESULT = "result"
    ABSTENTION = "abstention"
    ERROR = "error"


CompareBranchState = CompareBranchStateV1


class CompareBranchErrorCodeV1(StrEnum):
    """Safe error taxonomy for a failed Compare branch."""

    INVALID_REQUEST = "COMPARE_BRANCH_INVALID_REQUEST"
    CANCELLED = "COMPARE_BRANCH_CANCELLED"
    TIMEOUT = "COMPARE_BRANCH_TIMEOUT"
    UNAVAILABLE = "COMPARE_BRANCH_UNAVAILABLE"
    FAILURE = "COMPARE_BRANCH_FAILURE"
    MALFORMED_RESULT = "COMPARE_BRANCH_MALFORMED_RESULT"
    RESULT_TOO_LARGE = "COMPARE_BRANCH_RESULT_TOO_LARGE"
    RESULT_INVALID = "COMPARE_BRANCH_RESULT_INVALID"


CompareBranchErrorCode = CompareBranchErrorCodeV1


class CompareErrorCodeV1(StrEnum):
    """Closed top-level Compare error taxonomy."""

    INVALID_REQUEST = "COMPARE_INVALID_REQUEST"
    CANCELLED = "COMPARE_CANCELLED"
    COMPOSITION_INVALID = "COMPARE_COMPOSITION_INVALID"
    RESULT_TOO_LARGE = "COMPARE_RESULT_TOO_LARGE"


CompareErrorCode = CompareErrorCodeV1


class CompareDeltaRelationV1(StrEnum):
    """Structural relation based only on terminal branch states and IDs."""

    SAME_SELECTED_OPTION = "same_selected_option"
    DIFFERENT_SELECTED_OPTIONS = "different_selected_options"
    ASSISTANT_ONLY_SELECTED = "assistant_only_selected"
    SIMULATE_ME_ONLY_SELECTED = "simulate_me_only_selected"
    NEITHER_SELECTED = "neither_selected"
    ASSISTANT_ERROR = "assistant_error"
    SIMULATE_ME_ERROR = "simulate_me_error"
    BOTH_ERROR = "both_error"


CompareDeltaRelation = CompareDeltaRelationV1


class CompareEvidenceShapeV1(StrEnum):
    """Presence-only branch-local evidence projection."""

    UNAVAILABLE = "unavailable"
    EMPTY = "empty"
    PRESENT = "present"
    SUPPORTING_ONLY = "supporting_only"
    CONTEXTUAL_ONLY = "contextual_only"
    SUPPORTING_AND_CONTEXTUAL = "supporting_and_contextual"


_BRANCH_ERROR_MESSAGES: Final[dict[CompareBranchErrorCodeV1, str]] = {
    CompareBranchErrorCodeV1.INVALID_REQUEST: "compare branch request failed validation",
    CompareBranchErrorCodeV1.CANCELLED: "compare branch operation cancelled",
    CompareBranchErrorCodeV1.TIMEOUT: "compare branch operation timed out",
    CompareBranchErrorCodeV1.UNAVAILABLE: "compare branch unavailable",
    CompareBranchErrorCodeV1.FAILURE: "compare branch failed",
    CompareBranchErrorCodeV1.MALFORMED_RESULT: "compare branch result is malformed",
    CompareBranchErrorCodeV1.RESULT_TOO_LARGE: "compare branch result exceeds byte budget",
    CompareBranchErrorCodeV1.RESULT_INVALID: "compare branch result failed validation",
}
_COMPARE_ERROR_MESSAGES: Final[dict[CompareErrorCodeV1, str]] = {
    CompareErrorCodeV1.INVALID_REQUEST: "compare request failed validation",
    CompareErrorCodeV1.CANCELLED: "compare operation cancelled",
    CompareErrorCodeV1.COMPOSITION_INVALID: "compare composition failed validation",
    CompareErrorCodeV1.RESULT_TOO_LARGE: "compare result exceeds requested byte budget",
}
_DELTA_EXPLANATIONS: Final[dict[CompareDeltaRelationV1, str]] = {
    CompareDeltaRelationV1.SAME_SELECTED_OPTION: "Обе ветки выбрали один и тот же вариант.",
    CompareDeltaRelationV1.DIFFERENT_SELECTED_OPTIONS: "Ветки выбрали разные варианты.",
    CompareDeltaRelationV1.ASSISTANT_ONLY_SELECTED: (
        "Assistant выбрал вариант, а Simulate Me не выбрал вариант."
    ),
    CompareDeltaRelationV1.SIMULATE_ME_ONLY_SELECTED: (
        "Simulate Me выбрал вариант, а Assistant не выбрал вариант."
    ),
    CompareDeltaRelationV1.NEITHER_SELECTED: "Ни одна ветка не вернула выбранный вариант.",
    CompareDeltaRelationV1.ASSISTANT_ERROR: (
        "Assistant завершился ошибкой; результат Simulate Me сохранён отдельно."
    ),
    CompareDeltaRelationV1.SIMULATE_ME_ERROR: (
        "Simulate Me завершился ошибкой; результат Assistant сохранён отдельно."
    ),
    CompareDeltaRelationV1.BOTH_ERROR: "Обе ветки завершились ошибкой.",
}


class CompareValidationError(ValueError):
    """Base class for safe application-side Compare validation failures."""


class CompareInvalidRequestError(CompareValidationError):
    """The caller-owned Compare request is outside the approved bounds."""


class CompareCompositionInvalidError(CompareValidationError):
    """The Compare-owned wrapper or structural composition is invalid."""


class CompareResultTooLargeError(CompareValidationError):
    """The canonical Compare result exceeds the request-owned outer budget."""


class CompareCancelledError(RuntimeError):
    """Internal signal that global cancellation discards the partial result."""


@dataclass(frozen=True, slots=True)
class CompareOptionV1:
    """Caller-owned option in the shared request-local namespace."""

    id: str
    label: str


@dataclass(frozen=True, slots=True)
class CompareAssistantInputsV1:
    """Explicit-only Assistant inputs carried by one Compare request."""

    explicit_constraints: tuple[str, ...] = ()
    explicit_goals: tuple[str, ...] = ()
    explicit_context: tuple[AssistantExplicitContext, ...] = ()
    max_context_bytes: int = DEFAULT_ASSISTANT_MAX_CONTEXT_BYTES
    max_result_bytes: int = DEFAULT_ASSISTANT_MAX_RESULT_BYTES


@dataclass(frozen=True, slots=True)
class CompareRequestV1:
    """Exact immutable Compare request with one shared task/options namespace."""

    task: str
    options: tuple[CompareOptionV1, ...]
    assistant: CompareAssistantInputsV1
    max_result_bytes: int = DEFAULT_MAX_RESULT_BYTES


@dataclass(frozen=True, slots=True)
class CompareExecutionContextV1:
    """Shared cancellation and finite monotonic deadline for one operation."""

    cancellation: CancellationToken
    deadline: float

    def __post_init__(self) -> None:
        """Reject unbounded or non-numeric execution control values."""

        if type(self.deadline) is not float or not math.isfinite(self.deadline):
            raise ValueError("Compare deadline must be a finite float")
        if not callable(getattr(self.cancellation, "is_cancelled", None)):
            raise ValueError("Compare cancellation token is invalid")


@dataclass(frozen=True, slots=True)
class CompareErrorV1:
    """Bounded public top-level error projection."""

    code: CompareErrorCodeV1
    message: str

    def as_dict(self) -> dict[str, str]:
        """Return the safe code/message projection."""

        return {"code": self.code.value, "message": self.message}


CompareError = CompareErrorV1


@dataclass(frozen=True, slots=True)
class CompareBranchErrorV1:
    """Bounded safe error wrapper for one branch."""

    code: CompareBranchErrorCodeV1
    message: str

    def as_dict(self) -> dict[str, str]:
        """Return the safe branch code/message projection."""

        return {"code": self.code.value, "message": self.message}


@dataclass(frozen=True, slots=True)
class CompareAssistantResultBranchV1:
    """Successful Assistant recommendation/analysis wrapper."""

    state: CompareBranchStateV1
    result: AssistantResultEnvelopeV1
    error: None = None


@dataclass(frozen=True, slots=True)
class CompareAssistantAbstentionBranchV1:
    """Typed Assistant abstention wrapper; abstention is not an error."""

    state: CompareBranchStateV1
    result: AssistantResultEnvelopeV1
    error: None = None


@dataclass(frozen=True, slots=True)
class CompareAssistantErrorBranchV1:
    """Safe Assistant error wrapper with no partial raw result."""

    state: CompareBranchStateV1
    result: None = None
    error: CompareBranchErrorV1 | None = None


type CompareAssistantBranchV1 = (
    CompareAssistantResultBranchV1
    | CompareAssistantAbstentionBranchV1
    | CompareAssistantErrorBranchV1
)


@dataclass(frozen=True, slots=True)
class CompareSimulateMeResultBranchV1:
    """Successful Simulate Me prediction wrapper."""

    state: CompareBranchStateV1
    result: SimulateMeResult
    error: None = None


@dataclass(frozen=True, slots=True)
class CompareSimulateMeAbstentionBranchV1:
    """Typed Simulate Me abstention wrapper."""

    state: CompareBranchStateV1
    result: SimulateMeResult
    error: None = None


@dataclass(frozen=True, slots=True)
class CompareSimulateMeErrorBranchV1:
    """Safe Simulate Me error wrapper."""

    state: CompareBranchStateV1
    result: None = None
    error: CompareBranchErrorV1 | None = None


type CompareSimulateMeBranchV1 = (
    CompareSimulateMeResultBranchV1
    | CompareSimulateMeAbstentionBranchV1
    | CompareSimulateMeErrorBranchV1
)


@dataclass(frozen=True, slots=True)
class CompareDeltaV1:
    """Structural Delta that never compares branch semantics or text."""

    relation: CompareDeltaRelationV1
    assistant_state: CompareBranchStateV1
    simulate_me_state: CompareBranchStateV1
    assistant_selected_option_id: str | None
    simulate_me_selected_option_id: str | None
    assistant_evidence_shape: CompareEvidenceShapeV1
    simulate_me_evidence_shape: CompareEvidenceShapeV1
    simulate_me_temporal_caveat: bool
    explanation_template: CompareDeltaRelationV1
    explanation: str


@dataclass(frozen=True, slots=True)
class CompareResultV1:
    """Complete or partial ephemeral Compare result."""

    option_ids: tuple[str, ...]
    assistant: CompareAssistantBranchV1
    simulate_me: CompareSimulateMeBranchV1
    delta: CompareDeltaV1
    derivation_version: str
    policy_id: str
    policy_fingerprint: str


CompareResult = CompareResultV1


class CompareAssistantPort(Protocol):
    """Context-aware provider-neutral Assistant branch seam."""

    def advise(
        self,
        request: AssistantReasoningEnvelopeV1,
        *,
        execution: CompareExecutionContextV1,
    ) -> AssistantResultEnvelopeV1:
        """Attempt one explicit-only Assistant operation."""


class CompareSimulateMePort(Protocol):
    """Context-aware application seam for the existing Simulate Me core."""

    def execute(
        self,
        request: SimulateMeRequest,
        *,
        execution: CompareExecutionContextV1,
    ) -> SimulateMeResult:
        """Attempt one current-context Simulate Me operation."""


AssistantComparePort = CompareAssistantPort
SimulateMeComparePort = CompareSimulateMePort


@dataclass(frozen=True, slots=True)
class CompareBranchRequestsV1:
    """The two independent, immutable requests built during Compare preflight."""

    assistant: AssistantReasoningEnvelopeV1
    simulate_me: SimulateMeRequest


@dataclass(frozen=True, slots=True)
class _PreparedCompare:
    request: CompareRequestV1
    assistant_request: AssistantRequest
    branch_requests: CompareBranchRequestsV1


def _normalize_text(value: object, max_bytes: int) -> str:
    """Apply the approved strict UTF-8/NFC/edge-trim text normalization."""

    if type(value) is not str:
        raise CompareInvalidRequestError()
    if any(
        ord(char) < 0x20 or 0x7F <= ord(char) <= 0x9F or unicodedata.category(char) == "Cf"
        for char in value
    ):
        raise CompareInvalidRequestError()
    normalized = unicodedata.normalize("NFC", value).strip()
    if not normalized:
        raise CompareInvalidRequestError()
    try:
        size = len(normalized.encode("utf-8"))
    except UnicodeEncodeError:
        raise CompareInvalidRequestError() from None
    if not 1 <= size <= max_bytes:
        raise CompareInvalidRequestError()
    return normalized


def _normalize_option(value: object) -> CompareOptionV1:
    """Validate one shared request-local option without changing its ID."""

    if type(value) is not CompareOptionV1:
        raise CompareInvalidRequestError()
    if type(value.id) is not str or _ID_PATTERN.fullmatch(value.id) is None:
        raise CompareInvalidRequestError()
    try:
        if len(value.id.encode("ascii")) > MAX_OPTION_ID_BYTES:
            raise CompareInvalidRequestError()
    except UnicodeEncodeError:
        raise CompareInvalidRequestError() from None
    return CompareOptionV1(
        id=value.id,
        label=_normalize_text(value.label, MAX_OPTION_LABEL_BYTES),
    )


def _normalize_compare_request(request: object) -> CompareRequestV1:
    """Validate and normalize all caller-owned Compare fields before branch calls."""

    if type(request) is not CompareRequestV1:
        raise CompareInvalidRequestError()
    task = _normalize_text(request.task, MAX_TASK_BYTES)
    if type(request.options) is not tuple or not 1 <= len(request.options) <= MAX_OPTIONS:
        raise CompareInvalidRequestError()
    options = tuple(_normalize_option(option) for option in request.options)
    if len({option.id for option in options}) != len(options):
        raise CompareInvalidRequestError()

    if type(request.assistant) is not CompareAssistantInputsV1:
        raise CompareInvalidRequestError()
    if type(request.assistant.max_context_bytes) is not int or not (
        MIN_ASSISTANT_CONTEXT_BYTES_V1
        <= request.assistant.max_context_bytes
        <= MAX_ASSISTANT_CONTEXT_BYTES
    ):
        raise CompareInvalidRequestError()
    if type(request.assistant.max_result_bytes) is not int or not (
        304 <= request.assistant.max_result_bytes <= MAX_ASSISTANT_RESULT_BYTES
    ):
        raise CompareInvalidRequestError()
    if type(request.max_result_bytes) is not int or not (
        MIN_MAX_RESULT_BYTES_V1 <= request.max_result_bytes <= MAX_RESULT_BYTES
    ):
        raise CompareInvalidRequestError()

    assistant_request = AssistantRequest(
        task=task,
        options=tuple(AssistantOption(id=option.id, label=option.label) for option in options),
        explicit_constraints=request.assistant.explicit_constraints,
        explicit_goals=request.assistant.explicit_goals,
        explicit_context=request.assistant.explicit_context,
        max_context_bytes=request.assistant.max_context_bytes,
        max_result_bytes=request.assistant.max_result_bytes,
    )
    try:
        validated_assistant = validate_assistant_request(assistant_request)
    except AssistantError:
        raise CompareInvalidRequestError() from None

    normalized = CompareRequestV1(
        task=validated_assistant.task,
        options=tuple(
            CompareOptionV1(id=option.id, label=option.label)
            for option in validated_assistant.options
        ),
        assistant=CompareAssistantInputsV1(
            explicit_constraints=validated_assistant.explicit_constraints,
            explicit_goals=validated_assistant.explicit_goals,
            explicit_context=validated_assistant.explicit_context,
            max_context_bytes=validated_assistant.max_context_bytes,
            max_result_bytes=validated_assistant.max_result_bytes,
        ),
        max_result_bytes=request.max_result_bytes,
    )
    if normalized.max_result_bytes < minimum_compare_result_bytes(
        normalized_option_ids(normalized)
    ):
        raise CompareInvalidRequestError()
    return normalized


def normalized_option_ids(request: CompareRequestV1) -> tuple[str, ...]:
    """Return the shared request-local IDs in caller order."""

    if type(request) is not CompareRequestV1:
        raise CompareInvalidRequestError()
    return tuple(option.id for option in request.options)


def _enum_value[EnumT: StrEnum](enum_type: type[EnumT], candidate: object) -> EnumT:
    """Accept one exact Compare enum member or its exact wire string."""

    if type(candidate) is enum_type:
        return candidate
    if type(candidate) is str:
        try:
            return enum_type(candidate)
        except ValueError:
            pass
    raise CompareCompositionInvalidError()


def validate_compare_request(request: object) -> CompareRequestV1:
    """Public request validator returning normalized immutable copies."""

    return _normalize_compare_request(request)


def build_compare_branch_requests(request: object) -> CompareBranchRequestsV1:
    """Build both independent branch requests after Compare preflight."""

    normalized = _normalize_compare_request(request)
    assistant_request = AssistantRequest(
        task=normalized.task,
        options=tuple(
            AssistantOption(id=option.id, label=option.label) for option in normalized.options
        ),
        explicit_constraints=normalized.assistant.explicit_constraints,
        explicit_goals=normalized.assistant.explicit_goals,
        explicit_context=normalized.assistant.explicit_context,
        max_context_bytes=normalized.assistant.max_context_bytes,
        max_result_bytes=normalized.assistant.max_result_bytes,
    )
    try:
        validated_assistant = validate_assistant_request(assistant_request)
        assistant_envelope = build_assistant_reasoning_envelope(validated_assistant)
        context_bytes = serialize_assistant_reasoning_envelope(assistant_envelope)
    except AssistantError:
        raise CompareInvalidRequestError() from None
    if len(context_bytes) > normalized.assistant.max_context_bytes:
        raise CompareInvalidRequestError()
    simulate_request = SimulateMeRequest(
        query=normalized.task,
        options=tuple(
            SimulateMeOption(id=option.id, label=option.label) for option in normalized.options
        ),
    )
    try:
        validate_simulate_me_request(simulate_request)
    except SimulateMeError:
        raise CompareInvalidRequestError() from None
    return CompareBranchRequestsV1(
        assistant=assistant_envelope,
        simulate_me=simulate_request,
    )


def _branch_error(code: CompareBranchErrorCodeV1) -> CompareBranchErrorV1:
    """Create a branch error with its fixed safe message."""

    return CompareBranchErrorV1(code=code, message=_BRANCH_ERROR_MESSAGES[code])


def _top_error(code: CompareErrorCodeV1) -> CompareErrorV1:
    """Create a top-level error with its fixed safe message."""

    return CompareErrorV1(code=code, message=_COMPARE_ERROR_MESSAGES[code])


def _assistant_branch_error(error: AssistantError) -> CompareAssistantErrorBranchV1:
    """Map one Assistant application error without exposing its details."""

    mapping = {
        AssistantErrorCode.INVALID_REQUEST.value: CompareBranchErrorCodeV1.INVALID_REQUEST,
        AssistantErrorCode.CANCELLED.value: CompareBranchErrorCodeV1.CANCELLED,
        AssistantErrorCode.TIMEOUT.value: CompareBranchErrorCodeV1.TIMEOUT,
        AssistantErrorCode.PROVIDER_UNAVAILABLE.value: CompareBranchErrorCodeV1.UNAVAILABLE,
        AssistantErrorCode.PROVIDER_FAILURE.value: CompareBranchErrorCodeV1.FAILURE,
        AssistantErrorCode.MALFORMED_RESULT.value: CompareBranchErrorCodeV1.MALFORMED_RESULT,
        AssistantErrorCode.RESULT_TOO_LARGE.value: CompareBranchErrorCodeV1.RESULT_TOO_LARGE,
        AssistantErrorCode.RESULT_INVALID.value: CompareBranchErrorCodeV1.RESULT_INVALID,
    }
    code = mapping.get(error.code, CompareBranchErrorCodeV1.FAILURE)
    return CompareAssistantErrorBranchV1(
        state=CompareBranchStateV1.ERROR,
        error=_branch_error(code),
    )


def _simulate_me_branch_error(error: SimulateMeError) -> CompareSimulateMeErrorBranchV1:
    """Map current Simulate Me application errors to the Compare branch taxonomy."""

    mapping = {
        SimulateMeErrorCode.INVALID_REQUEST.value: CompareBranchErrorCodeV1.INVALID_REQUEST,
        SimulateMeErrorCode.RESULT_INVALID.value: CompareBranchErrorCodeV1.RESULT_INVALID,
    }
    code = mapping.get(error.code, CompareBranchErrorCodeV1.FAILURE)
    return CompareSimulateMeErrorBranchV1(
        state=CompareBranchStateV1.ERROR,
        error=_branch_error(code),
    )


def _assistant_result_branch(
    result: AssistantResultEnvelopeV1,
) -> CompareAssistantResultBranchV1 | CompareAssistantAbstentionBranchV1:
    """Wrap a validated Assistant result without changing its semantics."""

    if result.kind is AssistantResultKind.ABSTENTION:
        return CompareAssistantAbstentionBranchV1(
            state=CompareBranchStateV1.ABSTENTION,
            result=result,
        )
    return CompareAssistantResultBranchV1(
        state=CompareBranchStateV1.RESULT,
        result=result,
    )


def _simulate_me_result_branch(
    result: SimulateMeResult,
) -> CompareSimulateMeResultBranchV1 | CompareSimulateMeAbstentionBranchV1:
    """Wrap a validated Simulate Me result without changing its namespace."""

    if result.kind is SimulateMeResultKind.ABSTENTION:
        return CompareSimulateMeAbstentionBranchV1(
            state=CompareBranchStateV1.ABSTENTION,
            result=result,
        )
    return CompareSimulateMeResultBranchV1(
        state=CompareBranchStateV1.RESULT,
        result=result,
    )


def _wire_evidence_at(value: object) -> str:
    """Serialize one approved evidence time or reject an unrepresentable value."""

    if type(value) is str and value == "unknown":
        return value
    if not isinstance(value, datetime):
        raise CompareCompositionInvalidError()
    try:
        if value.utcoffset() is None:
            raise CompareCompositionInvalidError()
        utc_value = value.astimezone(UTC)
    except OverflowError, TypeError, ValueError:
        raise CompareCompositionInvalidError() from None
    if not 1 <= utc_value.year <= 9999:
        raise CompareCompositionInvalidError()
    return (
        f"{utc_value.year:04d}-{utc_value.month:02d}-{utc_value.day:02d}"
        f"T{utc_value.hour:02d}:{utc_value.minute:02d}:{utc_value.second:02d}."
        f"{utc_value.microsecond:06d}Z"
    )


def _simulate_me_result_payload(result: SimulateMeResult) -> dict[str, object]:
    """Return the exact ordered canonical payload for one Simulate Me result."""

    if type(result) is not SimulateMeResult:
        raise CompareCompositionInvalidError()
    if type(result.kind) is not SimulateMeResultKind:
        raise CompareCompositionInvalidError()
    if (
        type(result.evidence_refs) is not tuple
        or type(result.contextual_evidence_refs) is not tuple
        or type(result.temporal_caveats) is not tuple
    ):
        raise CompareCompositionInvalidError()
    if (
        result.derivation_version != SIMULATE_ME_DERIVATION_VERSION
        or result.policy_id != SIMULATE_ME_POLICY_ID
        or result.policy_fingerprint != SIMULATE_ME_POLICY_FINGERPRINT
    ):
        raise CompareCompositionInvalidError()
    if result.kind is SimulateMeResultKind.PREDICTION:
        if result.selected_option is None or result.abstention_code is not None:
            raise CompareCompositionInvalidError()
    elif (
        result.selected_option is not None
        or type(result.abstention_code) is not SimulateMeAbstentionCode
    ):
        raise CompareCompositionInvalidError()
    selected = result.selected_option
    if selected is not None:
        if type(selected) is not SimulateMeOption:
            raise CompareCompositionInvalidError()
        selected_payload: dict[str, object] | None = {
            "id": selected.id,
            "label": selected.label,
        }
    else:
        selected_payload = None

    def evidence_payload(
        ref: SimulateMeEvidenceRef | SimulateMeContextualEvidenceRef,
    ) -> dict[str, object]:
        if type(ref) not in (SimulateMeEvidenceRef, SimulateMeContextualEvidenceRef):
            raise CompareCompositionInvalidError()
        if type(ref.claim_id) is not UUID or ref.claim_id.version != 7:
            raise CompareCompositionInvalidError()
        if type(ref.dimension) is not SimulateMeDimension:
            raise CompareCompositionInvalidError()
        if type(ref) is SimulateMeEvidenceRef and ref.dimension not in (
            SimulateMeDimension.PREFERENCE,
            SimulateMeDimension.GOAL,
        ):
            raise CompareCompositionInvalidError()
        if type(ref) is SimulateMeContextualEvidenceRef and (
            ref.dimension is not SimulateMeDimension.BELIEF
        ):
            raise CompareCompositionInvalidError()
        if (
            type(ref.note_ids) is not tuple
            or not 1 <= len(ref.note_ids) <= MAX_SIMULATE_ME_NOTE_IDS_PER_REF
            or len(set(ref.note_ids)) != len(ref.note_ids)
            or ref.claim_id not in ref.note_ids
            or any(type(note_id) is not UUID or note_id.version != 7 for note_id in ref.note_ids)
        ):
            raise CompareCompositionInvalidError()
        return {
            "claim_id": str(ref.claim_id),
            "dimension": ref.dimension.value,
            "note_ids": [str(note_id) for note_id in ref.note_ids],
            "evidence_at": _wire_evidence_at(ref.evidence_at),
        }

    if (
        len(result.evidence_refs) > MAX_SIMULATE_ME_RESULT_REFS
        or len(result.contextual_evidence_refs) > MAX_SIMULATE_ME_RESULT_REFS
        or len(result.temporal_caveats) > MAX_SIMULATE_ME_RESULT_REFS
    ):
        raise CompareCompositionInvalidError()
    evidence_refs = tuple(evidence_payload(ref) for ref in result.evidence_refs)
    contextual_refs = tuple(evidence_payload(ref) for ref in result.contextual_evidence_refs)
    caveats: list[dict[str, object]] = []
    for caveat in result.temporal_caveats:
        if (
            type(caveat) is not SimulateMeTemporalCaveat
            or type(caveat.claim_id) is not UUID
            or caveat.claim_id.version != 7
        ):
            raise CompareCompositionInvalidError()
        if type(caveat.code) is not SimulateMeTemporalCaveatCode:
            raise CompareCompositionInvalidError()
        caveats.append({"code": caveat.code.value, "claim_id": str(caveat.claim_id)})
    abstention = None if result.abstention_code is None else result.abstention_code.value
    return {
        "kind": result.kind.value,
        "selected_option": selected_payload,
        "evidence_refs": list(evidence_refs),
        "contextual_evidence_refs": list(contextual_refs),
        "temporal_caveats": caveats,
        "abstention_code": abstention,
        "derivation_version": result.derivation_version,
        "policy_id": result.policy_id,
        "policy_fingerprint": result.policy_fingerprint,
    }


def _check_simulate_me_temporal_correspondence(result: SimulateMeResult) -> None:
    """Require exact unknown-evidence to temporal-caveat correspondence."""

    refs: tuple[SimulateMeEvidenceRef | SimulateMeContextualEvidenceRef, ...] = (
        *result.evidence_refs,
        *result.contextual_evidence_refs,
    )
    unknown_claim_ids = {ref.claim_id for ref in refs if ref.evidence_at == "unknown"}
    caveat_claim_ids = {caveat.claim_id for caveat in result.temporal_caveats}
    if unknown_claim_ids != caveat_claim_ids:
        raise CompareCompositionInvalidError()


def _assistant_selected_id(branch: CompareAssistantBranchV1) -> str | None:
    """Read only a validated Assistant selected option ID."""

    if isinstance(branch, (CompareAssistantResultBranchV1, CompareAssistantAbstentionBranchV1)):
        selected = branch.result.selected_option
        return None if selected is None else selected.id
    return None


def _simulate_me_selected_id(branch: CompareSimulateMeBranchV1) -> str | None:
    """Read only a validated Simulate Me selected option ID."""

    if isinstance(branch, (CompareSimulateMeResultBranchV1, CompareSimulateMeAbstentionBranchV1)):
        selected = branch.result.selected_option
        return None if selected is None else selected.id
    return None


def _assistant_state(branch: CompareAssistantBranchV1) -> CompareBranchStateV1:
    """Return the normalized Assistant wrapper state."""

    return branch.state


def _simulate_me_state(branch: CompareSimulateMeBranchV1) -> CompareBranchStateV1:
    """Return the normalized Simulate Me wrapper state."""

    return branch.state


def _build_delta(
    assistant: CompareAssistantBranchV1,
    simulate_me: CompareSimulateMeBranchV1,
) -> CompareDeltaV1:
    """Build the closed structural Delta and fixed explanation template."""

    assistant_state = _assistant_state(assistant)
    simulate_state = _simulate_me_state(simulate_me)
    assistant_selected = _assistant_selected_id(assistant)
    simulate_selected = _simulate_me_selected_id(simulate_me)
    if (
        assistant_state is CompareBranchStateV1.ERROR
        and simulate_state is CompareBranchStateV1.ERROR
    ):
        relation = CompareDeltaRelationV1.BOTH_ERROR
    elif assistant_state is CompareBranchStateV1.ERROR:
        relation = CompareDeltaRelationV1.ASSISTANT_ERROR
    elif simulate_state is CompareBranchStateV1.ERROR:
        relation = CompareDeltaRelationV1.SIMULATE_ME_ERROR
    elif assistant_selected is not None and simulate_selected is not None:
        relation = (
            CompareDeltaRelationV1.SAME_SELECTED_OPTION
            if assistant_selected == simulate_selected
            else CompareDeltaRelationV1.DIFFERENT_SELECTED_OPTIONS
        )
    elif assistant_selected is not None:
        relation = CompareDeltaRelationV1.ASSISTANT_ONLY_SELECTED
    elif simulate_selected is not None:
        relation = CompareDeltaRelationV1.SIMULATE_ME_ONLY_SELECTED
    else:
        relation = CompareDeltaRelationV1.NEITHER_SELECTED

    if assistant_state is CompareBranchStateV1.ERROR:
        assistant_shape = CompareEvidenceShapeV1.UNAVAILABLE
    else:
        assistant_result = cast(
            CompareAssistantResultBranchV1 | CompareAssistantAbstentionBranchV1,
            assistant,
        ).result
        if len(assistant_result.evidence_refs) == 0:
            assistant_shape = CompareEvidenceShapeV1.EMPTY
        else:
            assistant_shape = CompareEvidenceShapeV1.PRESENT

    if simulate_state is CompareBranchStateV1.ERROR:
        simulate_shape = CompareEvidenceShapeV1.UNAVAILABLE
        temporal_caveat = False
    else:
        simulate_result = cast(
            CompareSimulateMeResultBranchV1 | CompareSimulateMeAbstentionBranchV1,
            simulate_me,
        ).result
        supporting = len(simulate_result.evidence_refs) > 0
        contextual = len(simulate_result.contextual_evidence_refs) > 0
        if supporting and contextual:
            simulate_shape = CompareEvidenceShapeV1.SUPPORTING_AND_CONTEXTUAL
        elif supporting:
            simulate_shape = CompareEvidenceShapeV1.SUPPORTING_ONLY
        elif contextual:
            simulate_shape = CompareEvidenceShapeV1.CONTEXTUAL_ONLY
        else:
            simulate_shape = CompareEvidenceShapeV1.EMPTY
        temporal_caveat = bool(simulate_result.temporal_caveats)

    return CompareDeltaV1(
        relation=relation,
        assistant_state=assistant_state,
        simulate_me_state=simulate_state,
        assistant_selected_option_id=assistant_selected,
        simulate_me_selected_option_id=simulate_selected,
        assistant_evidence_shape=assistant_shape,
        simulate_me_evidence_shape=simulate_shape,
        simulate_me_temporal_caveat=temporal_caveat,
        explanation_template=relation,
        explanation=_DELTA_EXPLANATIONS[relation],
    )


def _validate_branch_error(value: object) -> CompareBranchErrorV1:
    """Validate a fixed-message branch error and return its normalized copy."""

    if type(value) is not CompareBranchErrorV1:
        raise CompareCompositionInvalidError()
    code = value.code
    if type(code) is str:
        try:
            code = CompareBranchErrorCodeV1(code)
        except ValueError:
            raise CompareCompositionInvalidError() from None
    if type(code) is not CompareBranchErrorCodeV1:
        raise CompareCompositionInvalidError()
    if type(value.message) is not str or value.message != _BRANCH_ERROR_MESSAGES[code]:
        raise CompareCompositionInvalidError()
    return CompareBranchErrorV1(code=code, message=value.message)


def _validate_assistant_branch(
    value: object,
    *,
    request: AssistantRequest | None,
) -> CompareAssistantBranchV1:
    """Validate one Assistant wrapper and its typed result/error distinction."""

    if type(value) is CompareAssistantErrorBranchV1:
        if value.state not in (CompareBranchStateV1.ERROR, "error"):
            raise CompareCompositionInvalidError()
        if value.result is not None or value.error is None:
            raise CompareCompositionInvalidError()
        error = _validate_branch_error(value.error)
        return CompareAssistantErrorBranchV1(
            state=CompareBranchStateV1.ERROR,
            error=error,
        )
    if type(value) not in (
        CompareAssistantResultBranchV1,
        CompareAssistantAbstentionBranchV1,
    ):
        raise CompareCompositionInvalidError()
    result_branch = cast(
        CompareAssistantResultBranchV1 | CompareAssistantAbstentionBranchV1,
        value,
    )
    if (
        result_branch.error is not None
        or type(result_branch.result) is not AssistantResultEnvelopeV1
    ):
        raise CompareCompositionInvalidError()
    try:
        result = (
            validate_assistant_result(result_branch.result, request=request)
            if request is not None
            else result_branch.result
        )
        # This also validates the bounded structural DTO if no request was supplied.
        serialize_assistant_result_envelope(result)
    except AssistantError:
        raise CompareCompositionInvalidError() from None
    kind = result.kind
    if type(kind) is str:
        try:
            kind = AssistantResultKind(kind)
        except ValueError:
            raise CompareCompositionInvalidError() from None
    if type(kind) is not AssistantResultKind:
        raise CompareCompositionInvalidError()
    if request is None:
        if kind is AssistantResultKind.RECOMMENDATION and (
            result.recommendation is None or result.abstention_code is not None
        ):
            raise CompareCompositionInvalidError()
        if kind is AssistantResultKind.ANALYSIS and (
            result.recommendation is not None
            or result.selected_option is not None
            or result.abstention_code is not None
        ):
            raise CompareCompositionInvalidError()
        if kind is AssistantResultKind.ABSTENTION and (
            result.recommendation is not None
            or result.selected_option is not None
            or result.abstention_code is None
        ):
            raise CompareCompositionInvalidError()
    if type(result_branch) is CompareAssistantAbstentionBranchV1:
        if result_branch.state not in (CompareBranchStateV1.ABSTENTION, "abstention"):
            raise CompareCompositionInvalidError()
        if kind is not AssistantResultKind.ABSTENTION:
            raise CompareCompositionInvalidError()
        return CompareAssistantAbstentionBranchV1(
            state=CompareBranchStateV1.ABSTENTION,
            result=result,
        )
    if result_branch.state not in (CompareBranchStateV1.RESULT, "result"):
        raise CompareCompositionInvalidError()
    if kind is AssistantResultKind.ABSTENTION:
        raise CompareCompositionInvalidError()
    return CompareAssistantResultBranchV1(
        state=CompareBranchStateV1.RESULT,
        result=result,
    )


def _validate_simulate_me_structure(value: SimulateMeResult) -> SimulateMeResult:
    """Validate enough Simulate Me shape for request-free canonical serialization."""

    if type(value) is not SimulateMeResult or type(value.kind) is not SimulateMeResultKind:
        raise CompareCompositionInvalidError()
    if type(value.evidence_refs) is not tuple or type(value.contextual_evidence_refs) is not tuple:
        raise CompareCompositionInvalidError()
    if type(value.temporal_caveats) is not tuple:
        raise CompareCompositionInvalidError()
    if value.selected_option is not None and type(value.selected_option) is not SimulateMeOption:
        raise CompareCompositionInvalidError()
    refs: tuple[SimulateMeEvidenceRef | SimulateMeContextualEvidenceRef, ...] = (
        *value.evidence_refs,
        *value.contextual_evidence_refs,
    )
    for ref in refs:
        if type(ref) not in (SimulateMeEvidenceRef, SimulateMeContextualEvidenceRef):
            raise CompareCompositionInvalidError()
        if type(ref.claim_id) is not UUID or ref.claim_id.version != 7:
            raise CompareCompositionInvalidError()
        if type(ref.note_ids) is not tuple:
            raise CompareCompositionInvalidError()
        if any(type(note_id) is not UUID or note_id.version != 7 for note_id in ref.note_ids):
            raise CompareCompositionInvalidError()
        _wire_evidence_at(ref.evidence_at)
    for caveat in value.temporal_caveats:
        if type(caveat) is not SimulateMeTemporalCaveat or type(caveat.claim_id) is not UUID:
            raise CompareCompositionInvalidError()
        if type(caveat.code) is not SimulateMeTemporalCaveatCode:
            raise CompareCompositionInvalidError()
    _check_simulate_me_temporal_correspondence(value)
    _simulate_me_result_payload(value)
    return value


def _validate_simulate_me_branch(
    value: object,
    *,
    request: SimulateMeRequest | None,
) -> CompareSimulateMeBranchV1:
    """Validate one Simulate Me wrapper and temporal correspondence."""

    if type(value) is CompareSimulateMeErrorBranchV1:
        if value.state not in (CompareBranchStateV1.ERROR, "error"):
            raise CompareCompositionInvalidError()
        if value.result is not None or value.error is None:
            raise CompareCompositionInvalidError()
        error = _validate_branch_error(value.error)
        return CompareSimulateMeErrorBranchV1(
            state=CompareBranchStateV1.ERROR,
            error=error,
        )
    if type(value) not in (
        CompareSimulateMeResultBranchV1,
        CompareSimulateMeAbstentionBranchV1,
    ):
        raise CompareCompositionInvalidError()
    result_branch = cast(
        CompareSimulateMeResultBranchV1 | CompareSimulateMeAbstentionBranchV1,
        value,
    )
    if result_branch.error is not None or type(result_branch.result) is not SimulateMeResult:
        raise CompareCompositionInvalidError()
    try:
        result = (
            validate_simulate_me_result(result_branch.result, request=request)
            if request is not None
            else _validate_simulate_me_structure(result_branch.result)
        )
        _check_simulate_me_temporal_correspondence(result)
        _simulate_me_result_payload(result)
    except SimulateMeError:
        raise CompareCompositionInvalidError() from None
    if type(result_branch) is CompareSimulateMeAbstentionBranchV1:
        if result_branch.state not in (CompareBranchStateV1.ABSTENTION, "abstention"):
            raise CompareCompositionInvalidError()
        if result.kind is not SimulateMeResultKind.ABSTENTION:
            raise CompareCompositionInvalidError()
        return CompareSimulateMeAbstentionBranchV1(
            state=CompareBranchStateV1.ABSTENTION,
            result=result,
        )
    if result_branch.state not in (CompareBranchStateV1.RESULT, "result"):
        raise CompareCompositionInvalidError()
    if result.kind is SimulateMeResultKind.ABSTENTION:
        raise CompareCompositionInvalidError()
    return CompareSimulateMeResultBranchV1(
        state=CompareBranchStateV1.RESULT,
        result=result,
    )


def _validate_delta(value: object) -> CompareDeltaV1:
    """Validate closed Delta fields before comparing them with recomputation."""

    if type(value) is not CompareDeltaV1:
        raise CompareCompositionInvalidError()

    relation = _enum_value(CompareDeltaRelationV1, value.relation)
    assistant_state = _enum_value(CompareBranchStateV1, value.assistant_state)
    simulate_state = _enum_value(CompareBranchStateV1, value.simulate_me_state)
    assistant_shape = _enum_value(CompareEvidenceShapeV1, value.assistant_evidence_shape)
    simulate_shape = _enum_value(CompareEvidenceShapeV1, value.simulate_me_evidence_shape)
    template = _enum_value(CompareDeltaRelationV1, value.explanation_template)
    for selected_id in (value.assistant_selected_option_id, value.simulate_me_selected_option_id):
        if selected_id is not None and type(selected_id) is not str:
            raise CompareCompositionInvalidError()
    if type(value.simulate_me_temporal_caveat) is not bool:
        raise CompareCompositionInvalidError()
    if type(value.explanation) is not str:
        raise CompareCompositionInvalidError()
    return CompareDeltaV1(
        relation=relation,
        assistant_state=assistant_state,
        simulate_me_state=simulate_state,
        assistant_selected_option_id=value.assistant_selected_option_id,
        simulate_me_selected_option_id=value.simulate_me_selected_option_id,
        assistant_evidence_shape=assistant_shape,
        simulate_me_evidence_shape=simulate_shape,
        simulate_me_temporal_caveat=value.simulate_me_temporal_caveat,
        explanation_template=template,
        explanation=value.explanation,
    )


def _normalize_compare_result(
    result: object,
    *,
    request: CompareRequestV1 | None,
) -> CompareResultV1:
    """Validate a complete composition and recompute its structural Delta."""

    if type(result) is not CompareResultV1:
        raise CompareCompositionInvalidError()
    if type(result.option_ids) is not tuple or not 1 <= len(result.option_ids) <= MAX_OPTIONS:
        raise CompareCompositionInvalidError()
    if any(
        type(option_id) is not str or _ID_PATTERN.fullmatch(option_id) is None
        for option_id in result.option_ids
    ):
        raise CompareCompositionInvalidError()
    if len(set(result.option_ids)) != len(result.option_ids):
        raise CompareCompositionInvalidError()
    if (
        type(result.derivation_version) is not str
        or result.derivation_version != DERIVATION_VERSION
        or type(result.policy_id) is not str
        or result.policy_id != POLICY_ID
        or type(result.policy_fingerprint) is not str
        or result.policy_fingerprint != POLICY_FINGERPRINT
    ):
        raise CompareCompositionInvalidError()
    if request is not None:
        normalized_request = _normalize_compare_request(request)
        if result.option_ids != normalized_option_ids(normalized_request):
            raise CompareCompositionInvalidError()
        assistant_request = AssistantRequest(
            task=normalized_request.task,
            options=tuple(
                AssistantOption(id=option.id, label=option.label)
                for option in normalized_request.options
            ),
            explicit_constraints=normalized_request.assistant.explicit_constraints,
            explicit_goals=normalized_request.assistant.explicit_goals,
            explicit_context=normalized_request.assistant.explicit_context,
            max_context_bytes=normalized_request.assistant.max_context_bytes,
            max_result_bytes=normalized_request.assistant.max_result_bytes,
        )
        simulate_request = SimulateMeRequest(
            query=normalized_request.task,
            options=tuple(
                SimulateMeOption(id=option.id, label=option.label)
                for option in normalized_request.options
            ),
        )
    else:
        assistant_request = None
        simulate_request = None
    assistant = _validate_assistant_branch(result.assistant, request=assistant_request)
    simulate_me = _validate_simulate_me_branch(result.simulate_me, request=simulate_request)
    if _assistant_selected_id(assistant) not in (None, *result.option_ids):
        raise CompareCompositionInvalidError()
    if _simulate_me_selected_id(simulate_me) not in (None, *result.option_ids):
        raise CompareCompositionInvalidError()
    delta = _validate_delta(result.delta)
    expected_delta = _build_delta(assistant, simulate_me)
    if delta != expected_delta:
        raise CompareCompositionInvalidError()
    normalized = CompareResultV1(
        option_ids=result.option_ids,
        assistant=assistant,
        simulate_me=simulate_me,
        delta=expected_delta,
        derivation_version=DERIVATION_VERSION,
        policy_id=POLICY_ID,
        policy_fingerprint=POLICY_FINGERPRINT,
    )
    if request is not None:
        try:
            result_bytes = _compare_result_bytes(normalized)
        except CompareCompositionInvalidError:
            raise
        normalized_request = _normalize_compare_request(request)
        if len(result_bytes) > normalized_request.max_result_bytes:
            raise CompareResultTooLargeError()
    return normalized


def validate_compare_result(
    result: object,
    *,
    request: CompareRequestV1 | None = None,
) -> CompareResultV1:
    """Validate a Compare result, binding branch selections when request is supplied."""

    return _normalize_compare_result(result, request=request)


def _assistant_branch_payload(branch: CompareAssistantBranchV1) -> dict[str, object]:
    """Serialize one Assistant wrapper in the approved key order."""

    if type(branch) is CompareAssistantErrorBranchV1:
        if branch.error is None:
            raise CompareCompositionInvalidError()
        error: dict[str, object] | None = {
            "code": branch.error.code.value,
            "message": branch.error.message,
        }
        result: object | None = None
        state = CompareBranchStateV1.ERROR.value
    else:
        error = None
        result = json.loads(serialize_assistant_result_envelope(branch.result).decode("utf-8"))
        state = branch.state.value
    return {"state": state, "result": result, "error": error}


def _simulate_me_branch_payload(branch: CompareSimulateMeBranchV1) -> dict[str, object]:
    """Serialize one Simulate Me wrapper in the approved key order."""

    if type(branch) is CompareSimulateMeErrorBranchV1:
        if branch.error is None:
            raise CompareCompositionInvalidError()
        error: dict[str, object] | None = {
            "code": branch.error.code.value,
            "message": branch.error.message,
        }
        result: object | None = None
        state = CompareBranchStateV1.ERROR.value
    else:
        error = None
        result_branch = cast(
            CompareSimulateMeResultBranchV1 | CompareSimulateMeAbstentionBranchV1,
            branch,
        )
        result = _simulate_me_result_payload(result_branch.result)
        state = branch.state.value
    return {"state": state, "result": result, "error": error}


def _delta_payload(delta: CompareDeltaV1) -> dict[str, object]:
    """Serialize the exact ordered Delta fields."""

    return {
        "relation": delta.relation.value,
        "assistant_state": delta.assistant_state.value,
        "simulate_me_state": delta.simulate_me_state.value,
        "assistant_selected_option_id": delta.assistant_selected_option_id,
        "simulate_me_selected_option_id": delta.simulate_me_selected_option_id,
        "assistant_evidence_shape": delta.assistant_evidence_shape.value,
        "simulate_me_evidence_shape": delta.simulate_me_evidence_shape.value,
        "simulate_me_temporal_caveat": delta.simulate_me_temporal_caveat,
        "explanation_template": delta.explanation_template.value,
        "explanation": delta.explanation,
    }


def _result_payload(result: CompareResultV1) -> dict[str, object]:
    """Return the exact ordered Compare root payload."""

    return {
        "option_ids": list(result.option_ids),
        "assistant": _assistant_branch_payload(result.assistant),
        "simulate_me": _simulate_me_branch_payload(result.simulate_me),
        "delta": _delta_payload(result.delta),
        "derivation_version": DERIVATION_VERSION,
        "policy_id": POLICY_ID,
        "policy_fingerprint": POLICY_FINGERPRINT,
    }


class CompareCanonicalJsonEncoderV1:
    """Canonical compact UTF-8 encoder used for Compare output."""

    @staticmethod
    def encode(payload: Mapping[str, object]) -> bytes:
        """Encode ordered JSON without ASCII substitution or trailing newline."""

        try:
            return AssistantCanonicalJsonEncoderV1.encode(payload)
        except AssistantError:
            raise CompareCompositionInvalidError() from None


def _compare_result_bytes(result: CompareResultV1) -> bytes:
    """Encode a normalized result without performing request binding again."""

    return CompareCanonicalJsonEncoderV1.encode(_result_payload(result))


def serialize_compare_result(
    result: object,
    *,
    request: CompareRequestV1 | None = None,
) -> bytes:
    """Serialize a structurally valid Compare result using canonical UTF-8 bytes."""

    normalized = _normalize_compare_result(result, request=request)
    return _compare_result_bytes(normalized)


canonical_compare_result_bytes = serialize_compare_result


def _minimum_compare_result(option_ids: tuple[str, ...]) -> CompareResultV1:
    """Build the contract's minimal two-error structural fixture."""

    assistant = CompareAssistantErrorBranchV1(
        state=CompareBranchStateV1.ERROR,
        error=_branch_error(CompareBranchErrorCodeV1.FAILURE),
    )
    simulate_me = CompareSimulateMeErrorBranchV1(
        state=CompareBranchStateV1.ERROR,
        error=_branch_error(CompareBranchErrorCodeV1.FAILURE),
    )
    return CompareResultV1(
        option_ids=option_ids,
        assistant=assistant,
        simulate_me=simulate_me,
        delta=_build_delta(assistant, simulate_me),
        derivation_version=DERIVATION_VERSION,
        policy_id=POLICY_ID,
        policy_fingerprint=POLICY_FINGERPRINT,
    )


def minimum_compare_result_bytes(option_ids: Sequence[str]) -> int:
    """Return the request-specific minimum for a valid structural result."""

    normalized_ids = tuple(option_ids)
    if not 1 <= len(normalized_ids) <= MAX_OPTIONS:
        raise CompareInvalidRequestError()
    if any(
        type(option_id) is not str or _ID_PATTERN.fullmatch(option_id) is None
        for option_id in normalized_ids
    ):
        raise CompareInvalidRequestError()
    if len(set(normalized_ids)) != len(normalized_ids):
        raise CompareInvalidRequestError()
    return len(_compare_result_bytes(_minimum_compare_result(normalized_ids)))


def validate_compare_policy() -> str:
    """Verify the fixed Compare policy fingerprint before exposing it."""

    digest = hashlib.sha256(POLICY_CANONICAL_JSON.encode("utf-8")).hexdigest()
    if f"sha256:{digest}" != POLICY_FINGERPRINT:
        raise CompareCompositionInvalidError()
    return POLICY_FINGERPRINT


class BuildCompare:
    """Execute one bounded independent Assistant/Simulate Me composition."""

    def __init__(
        self,
        assistant: CompareAssistantPort,
        simulate_me: CompareSimulateMePort,
        *,
        clock: Callable[[], float] | None = None,
    ) -> None:
        """Create an injected provider-free composition boundary."""

        self._assistant = assistant
        self._simulate_me = simulate_me
        self._clock = clock or time.monotonic

    def _expired(self, execution: CompareExecutionContextV1) -> bool:
        """Read the one injected monotonic clock at a control checkpoint."""

        return self._clock() >= execution.deadline

    def _post_adapter_checkpoint(self, execution: CompareExecutionContextV1) -> bool:
        """Prioritize cancellation, then deadline, after every adapter exit."""

        if execution.cancellation.is_cancelled():
            raise CompareCancelledError()
        return self._expired(execution)

    def _run_assistant(
        self,
        prepared: _PreparedCompare,
        execution: CompareExecutionContextV1,
    ) -> CompareAssistantBranchV1:
        """Make at most one Assistant call and map all outcomes safely."""

        if execution.cancellation.is_cancelled():
            raise CompareCancelledError()
        if self._expired(execution):
            return CompareAssistantErrorBranchV1(
                state=CompareBranchStateV1.ERROR,
                error=_branch_error(CompareBranchErrorCodeV1.TIMEOUT),
            )
        try:
            raw_result = self._assistant.advise(
                prepared.branch_requests.assistant,
                execution=execution,
            )
        except AssistantError as error:
            if self._post_adapter_checkpoint(execution):
                return CompareAssistantErrorBranchV1(
                    state=CompareBranchStateV1.ERROR,
                    error=_branch_error(CompareBranchErrorCodeV1.TIMEOUT),
                )
            return _assistant_branch_error(error)
        except TimeoutError:
            if self._post_adapter_checkpoint(execution):
                return CompareAssistantErrorBranchV1(
                    state=CompareBranchStateV1.ERROR,
                    error=_branch_error(CompareBranchErrorCodeV1.TIMEOUT),
                )
            return CompareAssistantErrorBranchV1(
                state=CompareBranchStateV1.ERROR,
                error=_branch_error(CompareBranchErrorCodeV1.TIMEOUT),
            )
        except Exception:
            if self._post_adapter_checkpoint(execution):
                return CompareAssistantErrorBranchV1(
                    state=CompareBranchStateV1.ERROR,
                    error=_branch_error(CompareBranchErrorCodeV1.TIMEOUT),
                )
            return CompareAssistantErrorBranchV1(
                state=CompareBranchStateV1.ERROR,
                error=_branch_error(CompareBranchErrorCodeV1.FAILURE),
            )
        if self._post_adapter_checkpoint(execution):
            return CompareAssistantErrorBranchV1(
                state=CompareBranchStateV1.ERROR,
                error=_branch_error(CompareBranchErrorCodeV1.TIMEOUT),
            )
        try:
            result = validate_assistant_result(
                raw_result,
                request=prepared.assistant_request,
            )
        except AssistantError as error:
            return _assistant_branch_error(error)
        except Exception:
            return CompareAssistantErrorBranchV1(
                state=CompareBranchStateV1.ERROR,
                error=_branch_error(CompareBranchErrorCodeV1.RESULT_INVALID),
            )
        return _assistant_result_branch(result)

    def _run_simulate_me(
        self,
        prepared: _PreparedCompare,
        execution: CompareExecutionContextV1,
    ) -> CompareSimulateMeBranchV1:
        """Make at most one Simulate Me call and preserve its typed namespace."""

        if execution.cancellation.is_cancelled():
            raise CompareCancelledError()
        if self._expired(execution):
            return CompareSimulateMeErrorBranchV1(
                state=CompareBranchStateV1.ERROR,
                error=_branch_error(CompareBranchErrorCodeV1.TIMEOUT),
            )
        try:
            raw_result = self._simulate_me.execute(
                prepared.branch_requests.simulate_me,
                execution=execution,
            )
        except SimulateMeError as error:
            if self._post_adapter_checkpoint(execution):
                return CompareSimulateMeErrorBranchV1(
                    state=CompareBranchStateV1.ERROR,
                    error=_branch_error(CompareBranchErrorCodeV1.TIMEOUT),
                )
            return _simulate_me_branch_error(error)
        except TimeoutError:
            if self._post_adapter_checkpoint(execution):
                return CompareSimulateMeErrorBranchV1(
                    state=CompareBranchStateV1.ERROR,
                    error=_branch_error(CompareBranchErrorCodeV1.TIMEOUT),
                )
            return CompareSimulateMeErrorBranchV1(
                state=CompareBranchStateV1.ERROR,
                error=_branch_error(CompareBranchErrorCodeV1.TIMEOUT),
            )
        except Exception:
            if self._post_adapter_checkpoint(execution):
                return CompareSimulateMeErrorBranchV1(
                    state=CompareBranchStateV1.ERROR,
                    error=_branch_error(CompareBranchErrorCodeV1.TIMEOUT),
                )
            return CompareSimulateMeErrorBranchV1(
                state=CompareBranchStateV1.ERROR,
                error=_branch_error(CompareBranchErrorCodeV1.FAILURE),
            )
        if self._post_adapter_checkpoint(execution):
            return CompareSimulateMeErrorBranchV1(
                state=CompareBranchStateV1.ERROR,
                error=_branch_error(CompareBranchErrorCodeV1.TIMEOUT),
            )
        try:
            result = validate_simulate_me_result(
                raw_result,
                request=prepared.branch_requests.simulate_me,
            )
            _check_simulate_me_temporal_correspondence(result)
            _simulate_me_result_payload(result)
        except SimulateMeError as error:
            return _simulate_me_branch_error(error)
        except Exception:
            return CompareSimulateMeErrorBranchV1(
                state=CompareBranchStateV1.ERROR,
                error=_branch_error(CompareBranchErrorCodeV1.RESULT_INVALID),
            )
        return _simulate_me_result_branch(result)

    def execute(
        self,
        request: CompareRequestV1,
        *,
        execution: CompareExecutionContextV1,
    ) -> CompareResultV1 | CompareErrorV1:
        """Run preflight, two independent attempts, structural Delta and size gate."""

        if execution.cancellation.is_cancelled():
            return _top_error(CompareErrorCodeV1.CANCELLED)
        deadline_expired_before_preflight = self._expired(execution)
        try:
            normalized_request = _normalize_compare_request(request)
            branch_requests = build_compare_branch_requests(normalized_request)
            assistant_request = AssistantRequest(
                task=normalized_request.task,
                options=tuple(
                    AssistantOption(id=option.id, label=option.label)
                    for option in normalized_request.options
                ),
                explicit_constraints=normalized_request.assistant.explicit_constraints,
                explicit_goals=normalized_request.assistant.explicit_goals,
                explicit_context=normalized_request.assistant.explicit_context,
                max_context_bytes=normalized_request.assistant.max_context_bytes,
                max_result_bytes=normalized_request.assistant.max_result_bytes,
            )
            assistant_request = validate_assistant_request(assistant_request)
        except CompareInvalidRequestError, AssistantError:
            return _top_error(CompareErrorCodeV1.INVALID_REQUEST)
        prepared = _PreparedCompare(
            request=normalized_request,
            assistant_request=assistant_request,
            branch_requests=branch_requests,
        )
        if execution.cancellation.is_cancelled():
            return _top_error(CompareErrorCodeV1.CANCELLED)
        try:
            if deadline_expired_before_preflight or self._expired(execution):
                assistant_branch: CompareAssistantBranchV1 = CompareAssistantErrorBranchV1(
                    state=CompareBranchStateV1.ERROR,
                    error=_branch_error(CompareBranchErrorCodeV1.TIMEOUT),
                )
                simulate_me_branch: CompareSimulateMeBranchV1 = CompareSimulateMeErrorBranchV1(
                    state=CompareBranchStateV1.ERROR,
                    error=_branch_error(CompareBranchErrorCodeV1.TIMEOUT),
                )
            else:
                assistant_branch = self._run_assistant(prepared, execution)
                if execution.cancellation.is_cancelled():
                    return _top_error(CompareErrorCodeV1.CANCELLED)
                simulate_me_branch = self._run_simulate_me(prepared, execution)
        except CompareCancelledError:
            return _top_error(CompareErrorCodeV1.CANCELLED)
        if execution.cancellation.is_cancelled():
            return _top_error(CompareErrorCodeV1.CANCELLED)
        try:
            result = CompareResultV1(
                option_ids=normalized_option_ids(normalized_request),
                assistant=assistant_branch,
                simulate_me=simulate_me_branch,
                delta=_build_delta(assistant_branch, simulate_me_branch),
                derivation_version=DERIVATION_VERSION,
                policy_id=POLICY_ID,
                policy_fingerprint=validate_compare_policy(),
            )
            result = _normalize_compare_result(result, request=normalized_request)
            result_bytes = _compare_result_bytes(result)
        except CompareResultTooLargeError:
            return _top_error(CompareErrorCodeV1.RESULT_TOO_LARGE)
        except CompareCompositionInvalidError:
            return _top_error(CompareErrorCodeV1.COMPOSITION_INVALID)
        if len(result_bytes) > normalized_request.max_result_bytes:
            return _top_error(CompareErrorCodeV1.RESULT_TOO_LARGE)
        return result


CompareGateway = BuildCompare
CompareExecutorV1 = BuildCompare
CompareOption = CompareOptionV1
CompareRequest = CompareRequestV1
build_compare_requests = build_compare_branch_requests


__all__ = [
    "COMPARE_DERIVATION_VERSION",
    "COMPARE_POLICY_ID",
    "DEFAULT_ASSISTANT_MAX_CONTEXT_BYTES",
    "DEFAULT_ASSISTANT_MAX_RESULT_BYTES",
    "DEFAULT_MAX_RESULT_BYTES",
    "DERIVATION_VERSION",
    "MAX_OPTIONS",
    "MIN_ASSISTANT_CONTEXT_BYTES_V1",
    "MIN_MAX_RESULT_BYTES_V1",
    "POLICY_CANONICAL_JSON",
    "POLICY_FINGERPRINT",
    "POLICY_ID",
    "AssistantComparePort",
    "BuildCompare",
    "CompareAssistantAbstentionBranchV1",
    "CompareAssistantBranchV1",
    "CompareAssistantErrorBranchV1",
    "CompareAssistantInputsV1",
    "CompareAssistantPort",
    "CompareAssistantResultBranchV1",
    "CompareBranchErrorCode",
    "CompareBranchErrorCodeV1",
    "CompareBranchErrorV1",
    "CompareBranchState",
    "CompareBranchStateV1",
    "CompareCanonicalJsonEncoderV1",
    "CompareCompositionInvalidError",
    "CompareDeltaRelation",
    "CompareDeltaRelationV1",
    "CompareDeltaV1",
    "CompareError",
    "CompareErrorCode",
    "CompareErrorCodeV1",
    "CompareErrorV1",
    "CompareEvidenceShapeV1",
    "CompareExecutionContextV1",
    "CompareExecutorV1",
    "CompareInvalidRequestError",
    "CompareOption",
    "CompareOptionV1",
    "CompareRequest",
    "CompareRequestV1",
    "CompareResult",
    "CompareResultTooLargeError",
    "CompareResultV1",
    "CompareSimulateMeAbstentionBranchV1",
    "CompareSimulateMeBranchV1",
    "CompareSimulateMeErrorBranchV1",
    "CompareSimulateMePort",
    "CompareSimulateMeResultBranchV1",
    "SimulateMeComparePort",
    "build_compare_branch_requests",
    "build_compare_requests",
    "canonical_compare_result_bytes",
    "minimum_compare_result_bytes",
    "normalized_option_ids",
    "serialize_compare_result",
    "validate_compare_policy",
    "validate_compare_request",
    "validate_compare_result",
]
