"""Provider-neutral Assistant v1 core with an explicit-context-only boundary."""

from __future__ import annotations

import json
import re
import unicodedata
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Final

from second_brain.application.ports import AdvisorPort, CancellationToken

ASSISTANT_OUTPUT_LABEL: Final[str] = "independent_recommendation_analysis"
ASSISTANT_CONTRACT_VERSION: Final[str] = "assistant-v1"
DEFAULT_MAX_CONTEXT_BYTES: Final[int] = 64 * 1024
DEFAULT_MAX_RESULT_BYTES: Final[int] = 64 * 1024
MAX_CONTEXT_BYTES: Final[int] = DEFAULT_MAX_CONTEXT_BYTES
MAX_RESULT_BYTES: Final[int] = DEFAULT_MAX_RESULT_BYTES
MIN_MAX_RESULT_BYTES_V1: Final[int] = 304

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
MAX_RECOMMENDATION_BYTES: Final[int] = 2048
MAX_RATIONALE: Final[int] = 8
MAX_RATIONALE_ITEM_BYTES: Final[int] = 1024
MAX_EVIDENCE_REFS: Final[int] = 32
MAX_INPUT_REFS: Final[int] = 16
MAX_OBJECTIVE_REFS: Final[int] = 8
MAX_UNCERTAINTY: Final[int] = 8
MAX_UNCERTAINTY_ITEM_BYTES: Final[int] = 512

_OPTION_ID_PATTERN: Final[re.Pattern[str]] = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,63}")


class AssistantContextKind(StrEnum):
    """Caller-declared context role; neither value is canonical authority."""

    FACT = "fact"
    BACKGROUND = "background"


class AssistantInputSource(StrEnum):
    """Request-local source names for constraint and goal references."""

    EXPLICIT_CONSTRAINT = "explicit_constraint"
    EXPLICIT_GOAL = "explicit_goal"


class AssistantEvidenceSource(StrEnum):
    """The only accepted result evidence source in Assistant v1."""

    EXPLICIT_CONTEXT = "explicit_context"


class AssistantEvidenceRole(StrEnum):
    """Closed role mapping for caller-provided context evidence."""

    REPORTED_FACT = "reported_fact"
    BACKGROUND = "background"


class AssistantResultKind(StrEnum):
    """Closed Assistant result kind."""

    RECOMMENDATION = "recommendation"
    ANALYSIS = "analysis"
    ABSTENTION = "abstention"


class AssistantAbstentionCode(StrEnum):
    """Bounded domain abstentions; no free-text classifier is implied."""

    INSUFFICIENT_BASIS = "insufficient_basis"
    CONFLICTING_EXPLICIT_CONSTRAINTS = "conflicting_explicit_constraints"
    AMBIGUOUS_OR_INCOMPARABLE_OPTIONS = "ambiguous_or_incomparable_options"
    UNSUPPORTED_TASK = "unsupported_task"


class AssistantErrorCode(StrEnum):
    """Safe application error taxonomy for the Assistant boundary."""

    INVALID_REQUEST = "ASSISTANT_INVALID_REQUEST"
    CANCELLED = "ASSISTANT_CANCELLED"
    TIMEOUT = "ASSISTANT_TIMEOUT"
    PROVIDER_UNAVAILABLE = "ASSISTANT_PROVIDER_UNAVAILABLE"
    PROVIDER_FAILURE = "ASSISTANT_PROVIDER_FAILURE"
    MALFORMED_RESULT = "ASSISTANT_MALFORMED_RESULT"
    RESULT_TOO_LARGE = "ASSISTANT_RESULT_TOO_LARGE"
    RESULT_INVALID = "ASSISTANT_RESULT_INVALID"


_ASSISTANT_ERROR_MESSAGES: Final[dict[AssistantErrorCode, str]] = {
    AssistantErrorCode.INVALID_REQUEST: "assistant request failed validation",
    AssistantErrorCode.CANCELLED: "assistant operation was cancelled",
    AssistantErrorCode.TIMEOUT: "assistant operation timed out",
    AssistantErrorCode.PROVIDER_UNAVAILABLE: "assistant advisor is unavailable",
    AssistantErrorCode.PROVIDER_FAILURE: "assistant advisor failed",
    AssistantErrorCode.MALFORMED_RESULT: "assistant advisor returned a malformed result",
    AssistantErrorCode.RESULT_TOO_LARGE: "assistant result exceeds the request limit",
    AssistantErrorCode.RESULT_INVALID: "assistant result failed semantic validation",
}


class AssistantError(RuntimeError):
    """Safe Assistant error without request, provider, or private-data details."""

    def __init__(self, code: AssistantErrorCode | str) -> None:
        """Create an error using only the closed public taxonomy."""

        normalized = _normalize_error_code(code)
        self.code = normalized.value
        self.message = _ASSISTANT_ERROR_MESSAGES[normalized]
        super().__init__(self.message)

    def as_dict(self) -> dict[str, str]:
        """Return the bounded public error projection."""

        return {"code": self.code, "message": self.message}


class AssistantInvalidRequestError(AssistantError):
    """The caller request or reasoning envelope is outside v1 bounds."""

    def __init__(self) -> None:
        super().__init__(AssistantErrorCode.INVALID_REQUEST)


class AssistantCancelledError(AssistantError):
    """The operation was cancelled before a safe result was accepted."""

    def __init__(self) -> None:
        super().__init__(AssistantErrorCode.CANCELLED)


class AssistantTimeoutError(AssistantError):
    """The approved Advisor operation exceeded its bounded deadline."""

    def __init__(self) -> None:
        super().__init__(AssistantErrorCode.TIMEOUT)


class AssistantProviderUnavailableError(AssistantError):
    """The provider-neutral Advisor boundary is unavailable."""

    def __init__(self) -> None:
        super().__init__(AssistantErrorCode.PROVIDER_UNAVAILABLE)


class AssistantProviderFailureError(AssistantError):
    """The Advisor failed without exposing upstream details."""

    def __init__(self) -> None:
        super().__init__(AssistantErrorCode.PROVIDER_FAILURE)


class AssistantMalformedResultError(AssistantError):
    """The Advisor result does not have the exact bounded DTO shape."""

    def __init__(self) -> None:
        super().__init__(AssistantErrorCode.MALFORMED_RESULT)


class AssistantResultTooLargeError(AssistantError):
    """The canonical result exceeds the request-owned byte budget."""

    def __init__(self) -> None:
        super().__init__(AssistantErrorCode.RESULT_TOO_LARGE)


class AssistantResultInvalidError(AssistantError):
    """A structurally valid result violates a semantic v1 invariant."""

    def __init__(self) -> None:
        super().__init__(AssistantErrorCode.RESULT_INVALID)


@dataclass(frozen=True, slots=True)
class AssistantOption:
    """Caller-owned bounded option; its identity is local to one request."""

    id: str
    label: str


@dataclass(frozen=True, slots=True)
class AssistantExplicitContext:
    """Caller-provided premise/background, never automatic private authority."""

    kind: AssistantContextKind | str
    text: str


@dataclass(frozen=True, slots=True)
class AssistantRequest:
    """Exact caller-owned Assistant v1 request DTO."""

    task: str
    options: tuple[AssistantOption, ...] = ()
    explicit_constraints: tuple[str, ...] = ()
    explicit_goals: tuple[str, ...] = ()
    explicit_context: tuple[AssistantExplicitContext, ...] = ()
    max_context_bytes: int = DEFAULT_MAX_CONTEXT_BYTES
    max_result_bytes: int = DEFAULT_MAX_RESULT_BYTES


@dataclass(frozen=True, slots=True)
class AssistantReasoningEnvelopeV1:
    """Only explicit fields visible at the separate AdvisorPort boundary."""

    task: str
    options: tuple[AssistantOption, ...]
    explicit_constraints: tuple[str, ...]
    explicit_goals: tuple[str, ...]
    explicit_context: tuple[AssistantExplicitContext, ...]


AdvisorRequest = AssistantReasoningEnvelopeV1


@dataclass(frozen=True, slots=True)
class AssistantInputRef:
    """One-based request-local constraint or goal reference."""

    source: AssistantInputSource | str
    ordinal: int


@dataclass(frozen=True, slots=True)
class AssistantEvidenceRef:
    """One-based request-local explicit-context reference."""

    source: AssistantEvidenceSource | str
    ordinal: int
    role: AssistantEvidenceRole | str


@dataclass(frozen=True, slots=True)
class AssistantResultEnvelopeV1:
    """Ephemeral independent recommendation/analysis result DTO."""

    output_label: str
    kind: AssistantResultKind | str
    recommendation: str | None
    selected_option: AssistantOption | None
    rationale: tuple[str, ...]
    evidence_refs: tuple[AssistantEvidenceRef, ...]
    constraints_used: tuple[AssistantInputRef, ...]
    objectives_used: tuple[AssistantInputRef, ...]
    uncertainty: tuple[str, ...]
    abstention_code: AssistantAbstentionCode | str | None
    contract_version: str


AssistantResult = AssistantResultEnvelopeV1


def _normalize_error_code(code: AssistantErrorCode | str) -> AssistantErrorCode:
    """Map unknown error labels to the safe provider-failure bucket."""

    if type(code) is AssistantErrorCode:
        return code
    if type(code) is str:
        try:
            return AssistantErrorCode(code)
        except ValueError:
            pass
    return AssistantErrorCode.PROVIDER_FAILURE


def _enum_value[EnumT: StrEnum](
    value: object,
    enum_type: type[EnumT],
    error: Callable[[], AssistantError],
) -> EnumT:
    """Accept only an exact enum member or its exact wire string."""

    if type(value) is enum_type:
        return value
    if type(value) is str:
        try:
            return enum_type(value)
        except ValueError:
            pass
    raise error()


def _contains_forbidden_codepoint(value: str) -> bool:
    """Reject C0/C1 controls, DEL, and Unicode format characters."""

    return any(
        ord(char) < 0x20 or 0x7F <= ord(char) <= 0x9F or unicodedata.category(char) == "Cf"
        for char in value
    )


def _normalize_text(
    value: object,
    *,
    max_bytes: int,
    error: Callable[[], AssistantError],
) -> str:
    """Validate strict UTF-8/NFC/edge-trim text and return its normalized form."""

    if type(value) is not str:
        raise error()
    if _contains_forbidden_codepoint(value):
        raise error()
    normalized = unicodedata.normalize("NFC", value).strip()
    if not normalized or _contains_forbidden_codepoint(normalized):
        raise error()
    try:
        byte_size = len(normalized.encode("utf-8"))
    except UnicodeEncodeError:
        raise error() from None
    if not 1 <= byte_size <= max_bytes:
        raise error()
    return normalized


def _normalize_option(
    value: object,
    *,
    error: Callable[[], AssistantError],
) -> AssistantOption:
    """Validate one bounded caller-owned option."""

    if type(value) is not AssistantOption:
        raise error()
    try:
        id_bytes = value.id.encode("ascii")
    except AttributeError, UnicodeEncodeError:
        raise error() from None
    if (
        type(value.id) is not str
        or not _OPTION_ID_PATTERN.fullmatch(value.id)
        or len(id_bytes) > MAX_OPTION_ID_BYTES
    ):
        raise error()
    label = _normalize_text(value.label, max_bytes=MAX_OPTION_LABEL_BYTES, error=error)
    return AssistantOption(id=value.id, label=label)


def _normalize_context(
    value: object,
    *,
    error: Callable[[], AssistantError],
) -> AssistantExplicitContext:
    """Validate one explicit context item and its closed kind."""

    if type(value) is not AssistantExplicitContext:
        raise error()
    kind = _enum_value(value.kind, AssistantContextKind, error)
    text = _normalize_text(value.text, max_bytes=MAX_CONTEXT_TEXT_BYTES, error=error)
    return AssistantExplicitContext(kind=kind, text=text)


def validate_assistant_request(request: object) -> AssistantRequest:
    """Validate and normalize an exact Assistant v1 request before the port call."""

    if type(request) is not AssistantRequest:
        raise AssistantInvalidRequestError()
    if type(request.options) is not tuple or len(request.options) > MAX_OPTIONS:
        raise AssistantInvalidRequestError()
    options = tuple(
        _normalize_option(option, error=AssistantInvalidRequestError) for option in request.options
    )
    if len({option.id for option in options}) != len(options):
        raise AssistantInvalidRequestError()

    task = _normalize_text(
        request.task,
        max_bytes=MAX_TASK_BYTES,
        error=AssistantInvalidRequestError,
    )

    if (
        type(request.explicit_constraints) is not tuple
        or len(request.explicit_constraints) > MAX_CONSTRAINTS
    ):
        raise AssistantInvalidRequestError()
    constraints = tuple(
        _normalize_text(item, max_bytes=MAX_CONSTRAINT_BYTES, error=AssistantInvalidRequestError)
        for item in request.explicit_constraints
    )
    if sum(len(item.encode("utf-8")) for item in constraints) > MAX_CONSTRAINTS_BYTES:
        raise AssistantInvalidRequestError()

    if type(request.explicit_goals) is not tuple or len(request.explicit_goals) > MAX_GOALS:
        raise AssistantInvalidRequestError()
    goals = tuple(
        _normalize_text(item, max_bytes=MAX_GOAL_BYTES, error=AssistantInvalidRequestError)
        for item in request.explicit_goals
    )
    if sum(len(item.encode("utf-8")) for item in goals) > MAX_GOALS_BYTES:
        raise AssistantInvalidRequestError()

    if (
        type(request.explicit_context) is not tuple
        or len(request.explicit_context) > MAX_CONTEXT_ENTRIES
    ):
        raise AssistantInvalidRequestError()
    context = tuple(
        _normalize_context(item, error=AssistantInvalidRequestError)
        for item in request.explicit_context
    )
    if sum(len(item.text.encode("utf-8")) for item in context) > MAX_CONTEXT_TEXTS_BYTES:
        raise AssistantInvalidRequestError()

    if type(request.max_context_bytes) is not int or not (
        1 <= request.max_context_bytes <= MAX_CONTEXT_BYTES
    ):
        raise AssistantInvalidRequestError()
    if type(request.max_result_bytes) is not int or not (
        MIN_MAX_RESULT_BYTES_V1 <= request.max_result_bytes <= MAX_RESULT_BYTES
    ):
        raise AssistantInvalidRequestError()

    return AssistantRequest(
        task=task,
        options=options,
        explicit_constraints=constraints,
        explicit_goals=goals,
        explicit_context=context,
        max_context_bytes=request.max_context_bytes,
        max_result_bytes=request.max_result_bytes,
    )


def build_assistant_reasoning_envelope(request: object) -> AssistantReasoningEnvelopeV1:
    """Build the only explicit-context-only payload visible to ``AdvisorPort``."""

    validated = validate_assistant_request(request)
    return AssistantReasoningEnvelopeV1(
        task=validated.task,
        options=validated.options,
        explicit_constraints=validated.explicit_constraints,
        explicit_goals=validated.explicit_goals,
        explicit_context=validated.explicit_context,
    )


build_reasoning_envelope = build_assistant_reasoning_envelope


class AssistantCanonicalJsonEncoderV1:
    """Canonical compact JSON encoder shared by reasoning and result envelopes."""

    @staticmethod
    def encode(payload: Mapping[str, object]) -> bytes:
        """Encode ordered payloads as direct UTF-8 without alternate escaping."""

        try:
            text = json.dumps(
                payload,
                ensure_ascii=False,
                allow_nan=False,
                separators=(",", ":"),
            )
            return text.encode("utf-8")
        except TypeError, UnicodeEncodeError, ValueError:
            raise AssistantInvalidRequestError() from None


def _reasoning_payload(envelope: AssistantReasoningEnvelopeV1) -> dict[str, object]:
    """Return the exact ordered reasoning envelope payload."""

    return {
        "task": envelope.task,
        "options": [{"id": option.id, "label": option.label} for option in envelope.options],
        "explicit_constraints": list(envelope.explicit_constraints),
        "explicit_goals": list(envelope.explicit_goals),
        "explicit_context": [
            {
                "kind": _enum_value(
                    item.kind,
                    AssistantContextKind,
                    AssistantInvalidRequestError,
                ).value,
                "text": item.text,
            }
            for item in envelope.explicit_context
        ],
    }


def _validated_reasoning_envelope(envelope: object) -> AssistantReasoningEnvelopeV1:
    """Validate direct envelope calls using the same request policy."""

    if type(envelope) is not AssistantReasoningEnvelopeV1:
        raise AssistantInvalidRequestError()
    return build_assistant_reasoning_envelope(
        AssistantRequest(
            task=envelope.task,
            options=envelope.options,
            explicit_constraints=envelope.explicit_constraints,
            explicit_goals=envelope.explicit_goals,
            explicit_context=envelope.explicit_context,
        )
    )


def serialize_assistant_reasoning_envelope(envelope: object) -> bytes:
    """Serialize a validated reasoning envelope and return canonical UTF-8 bytes."""

    normalized = _validated_reasoning_envelope(envelope)
    return AssistantCanonicalJsonEncoderV1.encode(_reasoning_payload(normalized))


canonical_assistant_reasoning_bytes = serialize_assistant_reasoning_envelope


def _normalize_input_ref(value: object) -> AssistantInputRef:
    """Structurally validate one constraint/goal reference."""

    if type(value) is not AssistantInputRef:
        raise AssistantMalformedResultError()
    source = _enum_value(value.source, AssistantInputSource, AssistantMalformedResultError)
    if type(value.ordinal) is not int or value.ordinal < 1:
        raise AssistantMalformedResultError()
    return AssistantInputRef(source=source, ordinal=value.ordinal)


def _normalize_evidence_ref(value: object) -> AssistantEvidenceRef:
    """Structurally validate one explicit-context evidence reference."""

    if type(value) is not AssistantEvidenceRef:
        raise AssistantMalformedResultError()
    source = _enum_value(value.source, AssistantEvidenceSource, AssistantMalformedResultError)
    role = _enum_value(value.role, AssistantEvidenceRole, AssistantMalformedResultError)
    if type(value.ordinal) is not int or value.ordinal < 1:
        raise AssistantMalformedResultError()
    return AssistantEvidenceRef(source=source, ordinal=value.ordinal, role=role)


def _normalize_result_structure(result: object) -> AssistantResultEnvelopeV1:
    """Perform phase-one structural validation and normalize result strings."""

    if type(result) is not AssistantResultEnvelopeV1:
        raise AssistantMalformedResultError()
    if (
        type(result.output_label) is not str
        or result.output_label != ASSISTANT_OUTPUT_LABEL
        or type(result.contract_version) is not str
        or result.contract_version != ASSISTANT_CONTRACT_VERSION
    ):
        raise AssistantMalformedResultError()
    kind = _enum_value(result.kind, AssistantResultKind, AssistantMalformedResultError)
    if result.recommendation is not None and type(result.recommendation) is not str:
        raise AssistantMalformedResultError()
    recommendation = (
        None
        if result.recommendation is None
        else _normalize_text(
            result.recommendation,
            max_bytes=MAX_RECOMMENDATION_BYTES,
            error=AssistantMalformedResultError,
        )
    )

    selected_option = None
    if result.selected_option is not None:
        selected_option = _normalize_option(
            result.selected_option,
            error=AssistantMalformedResultError,
        )

    if type(result.rationale) is not tuple or not 1 <= len(result.rationale) <= MAX_RATIONALE:
        raise AssistantMalformedResultError()
    rationale = tuple(
        _normalize_text(
            item,
            max_bytes=MAX_RATIONALE_ITEM_BYTES,
            error=AssistantMalformedResultError,
        )
        for item in result.rationale
    )

    if type(result.evidence_refs) is not tuple or len(result.evidence_refs) > MAX_EVIDENCE_REFS:
        raise AssistantMalformedResultError()
    evidence_refs = tuple(_normalize_evidence_ref(item) for item in result.evidence_refs)

    if type(result.constraints_used) is not tuple or len(result.constraints_used) > MAX_INPUT_REFS:
        raise AssistantMalformedResultError()
    constraints_used = tuple(_normalize_input_ref(item) for item in result.constraints_used)

    if (
        type(result.objectives_used) is not tuple
        or len(result.objectives_used) > MAX_OBJECTIVE_REFS
    ):
        raise AssistantMalformedResultError()
    objectives_used = tuple(_normalize_input_ref(item) for item in result.objectives_used)

    if type(result.uncertainty) is not tuple or len(result.uncertainty) > MAX_UNCERTAINTY:
        raise AssistantMalformedResultError()
    uncertainty = tuple(
        _normalize_text(
            item,
            max_bytes=MAX_UNCERTAINTY_ITEM_BYTES,
            error=AssistantMalformedResultError,
        )
        for item in result.uncertainty
    )

    abstention_code = None
    if result.abstention_code is not None:
        abstention_code = _enum_value(
            result.abstention_code,
            AssistantAbstentionCode,
            AssistantMalformedResultError,
        )
    return AssistantResultEnvelopeV1(
        output_label=ASSISTANT_OUTPUT_LABEL,
        kind=kind,
        recommendation=recommendation,
        selected_option=selected_option,
        rationale=rationale,
        evidence_refs=evidence_refs,
        constraints_used=constraints_used,
        objectives_used=objectives_used,
        uncertainty=uncertainty,
        abstention_code=abstention_code,
        contract_version=ASSISTANT_CONTRACT_VERSION,
    )


def _validate_result_semantics(
    result: AssistantResultEnvelopeV1,
    request: AssistantRequest,
) -> None:
    """Perform phase-two invariants and phase-three source binding."""

    kind = _enum_value(result.kind, AssistantResultKind, AssistantResultInvalidError)
    if kind is AssistantResultKind.RECOMMENDATION:
        if result.recommendation is None or result.abstention_code is not None:
            raise AssistantResultInvalidError()
    elif kind is AssistantResultKind.ANALYSIS:
        if result.recommendation is not None or result.selected_option is not None:
            raise AssistantResultInvalidError()
        if result.abstention_code is not None:
            raise AssistantResultInvalidError()
    elif kind is AssistantResultKind.ABSTENTION and (
        result.recommendation is not None
        or result.selected_option is not None
        or result.abstention_code is None
    ):
        raise AssistantResultInvalidError()

    if result.selected_option is not None and result.selected_option not in request.options:
        raise AssistantResultInvalidError()
    if not request.options and result.selected_option is not None:
        raise AssistantResultInvalidError()

    seen_evidence: set[tuple[AssistantEvidenceSource, int, AssistantEvidenceRole]] = set()
    for ref in result.evidence_refs:
        source = _enum_value(ref.source, AssistantEvidenceSource, AssistantResultInvalidError)
        role = _enum_value(ref.role, AssistantEvidenceRole, AssistantResultInvalidError)
        if ref.ordinal > len(request.explicit_context):
            raise AssistantResultInvalidError()
        context = request.explicit_context[ref.ordinal - 1]
        context_kind = _enum_value(
            context.kind,
            AssistantContextKind,
            AssistantResultInvalidError,
        )
        expected_role = (
            AssistantEvidenceRole.REPORTED_FACT
            if context_kind is AssistantContextKind.FACT
            else AssistantEvidenceRole.BACKGROUND
        )
        if source is not AssistantEvidenceSource.EXPLICIT_CONTEXT or role is not expected_role:
            raise AssistantResultInvalidError()
        key = (source, ref.ordinal, role)
        if key in seen_evidence:
            raise AssistantResultInvalidError()
        seen_evidence.add(key)

    _validate_input_refs(
        result.constraints_used,
        source=AssistantInputSource.EXPLICIT_CONSTRAINT,
        size=len(request.explicit_constraints),
        limit=MAX_INPUT_REFS,
    )
    _validate_input_refs(
        result.objectives_used,
        source=AssistantInputSource.EXPLICIT_GOAL,
        size=len(request.explicit_goals),
        limit=MAX_OBJECTIVE_REFS,
    )


def _validate_input_refs(
    refs: tuple[AssistantInputRef, ...],
    *,
    source: AssistantInputSource,
    size: int,
    limit: int,
) -> None:
    """Check exact source, one-based bounds, and uniqueness of input refs."""

    if len(refs) > limit:
        raise AssistantResultInvalidError()
    seen: set[tuple[AssistantInputSource, int]] = set()
    for ref in refs:
        actual_source = _enum_value(ref.source, AssistantInputSource, AssistantResultInvalidError)
        if actual_source is not source or ref.ordinal > size:
            raise AssistantResultInvalidError()
        key = (actual_source, ref.ordinal)
        if key in seen:
            raise AssistantResultInvalidError()
        seen.add(key)


def _result_payload(result: AssistantResultEnvelopeV1) -> dict[str, object]:
    """Return the exact ordered result envelope payload."""

    kind = _enum_value(result.kind, AssistantResultKind, AssistantMalformedResultError)
    return {
        "output_label": ASSISTANT_OUTPUT_LABEL,
        "kind": kind.value,
        "recommendation": result.recommendation,
        "selected_option": (
            None
            if result.selected_option is None
            else {"id": result.selected_option.id, "label": result.selected_option.label}
        ),
        "rationale": list(result.rationale),
        "evidence_refs": [
            {
                "source": _enum_value(
                    ref.source,
                    AssistantEvidenceSource,
                    AssistantMalformedResultError,
                ).value,
                "ordinal": ref.ordinal,
                "role": _enum_value(
                    ref.role,
                    AssistantEvidenceRole,
                    AssistantMalformedResultError,
                ).value,
            }
            for ref in result.evidence_refs
        ],
        "constraints_used": [
            {
                "source": _enum_value(
                    ref.source,
                    AssistantInputSource,
                    AssistantMalformedResultError,
                ).value,
                "ordinal": ref.ordinal,
            }
            for ref in result.constraints_used
        ],
        "objectives_used": [
            {
                "source": _enum_value(
                    ref.source,
                    AssistantInputSource,
                    AssistantMalformedResultError,
                ).value,
                "ordinal": ref.ordinal,
            }
            for ref in result.objectives_used
        ],
        "uncertainty": list(result.uncertainty),
        "abstention_code": (
            None
            if result.abstention_code is None
            else _enum_value(
                result.abstention_code,
                AssistantAbstentionCode,
                AssistantMalformedResultError,
            ).value
        ),
        "contract_version": ASSISTANT_CONTRACT_VERSION,
    }


def serialize_assistant_result_envelope(result: object) -> bytes:
    """Serialize a structurally valid result using exact canonical UTF-8 bytes."""

    normalized = _normalize_result_structure(result)
    return AssistantCanonicalJsonEncoderV1.encode(_result_payload(normalized))


canonical_assistant_result_bytes = serialize_assistant_result_envelope


def minimum_assistant_result_bytes() -> int:
    """Return the contract-derived largest minimal-abstention envelope size."""

    sizes: list[int] = []
    for code in AssistantAbstentionCode:
        result = AssistantResultEnvelopeV1(
            output_label=ASSISTANT_OUTPUT_LABEL,
            kind=AssistantResultKind.ABSTENTION,
            recommendation=None,
            selected_option=None,
            rationale=("x",),
            evidence_refs=(),
            constraints_used=(),
            objectives_used=(),
            uncertainty=(),
            abstention_code=code,
            contract_version=ASSISTANT_CONTRACT_VERSION,
        )
        sizes.append(len(serialize_assistant_result_envelope(result)))
    return max(sizes)


def validate_assistant_result(
    result: object,
    *,
    request: object,
) -> AssistantResultEnvelopeV1:
    """Validate result structure, semantics, references, and request byte budget."""

    validated_request = validate_assistant_request(request)
    normalized = _normalize_result_structure(result)
    _validate_result_semantics(normalized, validated_request)
    result_bytes = AssistantCanonicalJsonEncoderV1.encode(_result_payload(normalized))
    if len(result_bytes) > validated_request.max_result_bytes:
        raise AssistantResultTooLargeError()
    return normalized


class BuildAssistant:
    """Execute one bounded explicit-only Assistant operation through an AdvisorPort."""

    def __init__(self, advisor: AdvisorPort) -> None:
        self._advisor = advisor

    def execute(
        self,
        request: AssistantRequest,
        *,
        cancellation: CancellationToken,
    ) -> AssistantResultEnvelopeV1:
        """Validate, call the Advisor at most once, and validate its result."""

        validated_request = validate_assistant_request(request)
        if cancellation.is_cancelled():
            raise AssistantCancelledError()
        envelope = build_assistant_reasoning_envelope(validated_request)
        context_bytes = AssistantCanonicalJsonEncoderV1.encode(_reasoning_payload(envelope))
        if len(context_bytes) > validated_request.max_context_bytes:
            raise AssistantInvalidRequestError()
        try:
            result = self._advisor.advise(envelope, cancellation=cancellation)
        except AssistantError:
            raise
        except TimeoutError:
            raise AssistantTimeoutError() from None
        except Exception:
            raise AssistantProviderFailureError() from None
        if cancellation.is_cancelled():
            raise AssistantCancelledError()
        return validate_assistant_result(result, request=validated_request)


AssistantGateway = BuildAssistant
