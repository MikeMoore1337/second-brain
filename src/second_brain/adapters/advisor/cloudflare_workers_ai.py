"""Secret-safe Cloudflare Workers AI adapter for explicit-only Assistant v1."""

from __future__ import annotations

import json
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import cast

from second_brain.adapters.llm.cloudflare_workers_ai import (
    CLOUDFLARE_ACCOUNT_ID_ENV,
    CLOUDFLARE_API_HOST,
    CLOUDFLARE_API_TOKEN_ENV,
    CLOUDFLARE_CHAT_COMPLETIONS_PATH,
    CLOUDFLARE_MODEL,
    CLOUDFLARE_PROVIDER,
    ENVELOPE_OVERHEAD_BYTES,
    TOTAL_DEADLINE_SECONDS,
    CloudflareWorkersAiConfig,
    CloudflareWorkersAiConfigError,
    SubprocessWorkerRunner,
    WorkerRunner,
    _cloudflare_result_category,
    _has_non_null_forbidden_field,
    _load_json_object,
    _WorkerCancelled,
    _WorkerContentTooLarge,
    _WorkerMalformed,
    _WorkerRequest,
    _WorkerResult,
    _WorkerTimedOut,
    _WorkerUnavailable,
    load_cloudflare_workers_ai_config,
)
from second_brain.application.assistant import (
    ASSISTANT_CONTRACT_VERSION,
    ASSISTANT_OUTPUT_LABEL,
    DEFAULT_MAX_CONTEXT_BYTES,
    MAX_CONSTRAINT_BYTES,
    MAX_CONSTRAINTS,
    MAX_CONTEXT_BYTES,
    MAX_CONTEXT_ENTRIES,
    MAX_CONTEXT_TEXT_BYTES,
    MAX_EVIDENCE_REFS,
    MAX_GOAL_BYTES,
    MAX_GOALS,
    MAX_INPUT_REFS,
    MAX_OBJECTIVE_REFS,
    MAX_OPTION_ID_BYTES,
    MAX_OPTION_LABEL_BYTES,
    MAX_OPTIONS,
    MAX_RATIONALE,
    MAX_RATIONALE_ITEM_BYTES,
    MAX_RECOMMENDATION_BYTES,
    MAX_RESULT_BYTES,
    MAX_TASK_BYTES,
    MAX_UNCERTAINTY,
    MAX_UNCERTAINTY_ITEM_BYTES,
    AssistantAbstentionCode,
    AssistantCancelledError,
    AssistantContextKind,
    AssistantError,
    AssistantEvidenceRef,
    AssistantEvidenceRole,
    AssistantEvidenceSource,
    AssistantExplicitContext,
    AssistantInputRef,
    AssistantInputSource,
    AssistantInvalidRequestError,
    AssistantMalformedResultError,
    AssistantOption,
    AssistantProviderFailureError,
    AssistantProviderUnavailableError,
    AssistantReasoningEnvelopeV1,
    AssistantResultEnvelopeV1,
    AssistantResultKind,
    AssistantResultTooLargeError,
    AssistantTimeoutError,
    normalize_assistant_result_structure,
    serialize_assistant_reasoning_envelope,
    serialize_assistant_result_envelope,
)
from second_brain.application.ports import CancellationToken

CLOUDFLARE_ADVISOR_PROVIDER = CLOUDFLARE_PROVIDER
CLOUDFLARE_ADVISOR_MODEL = CLOUDFLARE_MODEL
CLOUDFLARE_ADVISOR_API_HOST = CLOUDFLARE_API_HOST
CLOUDFLARE_ADVISOR_PATH = CLOUDFLARE_CHAT_COMPLETIONS_PATH

ADVISOR_DEFAULT_MAX_CONTEXT_BYTES = DEFAULT_MAX_CONTEXT_BYTES
ADVISOR_TOTAL_DEADLINE_SECONDS = TOTAL_DEADLINE_SECONDS

_ADVISOR_BEGIN_MARKER = "<BEGIN_ASSISTANT_EXPLICIT_ENVELOPE>"
_ADVISOR_END_MARKER = "<END_ASSISTANT_EXPLICIT_ENVELOPE>"
_ADVISOR_ESCAPED_BEGIN_MARKER = r"\u003CBEGIN_ASSISTANT_EXPLICIT_ENVELOPE>"
_ADVISOR_ESCAPED_END_MARKER = r"\u003CEND_ASSISTANT_EXPLICIT_ENVELOPE>"
_ADVISOR_SYSTEM_MESSAGE = (
    "Ты выполняешь независимую рекомендацию или анализ, а не Simulate Me и не "
    "прогноз выбора пользователя. Используй только текущий переданный explicit "
    "Assistant envelope. Не утверждай, что знаешь пользователя сверх envelope; "
    "не выдумывай hidden goals, preferences или history; не считай background "
    "проверенным фактом. Верни только один JSON-объект по exact Assistant v1 "
    "schema. Не используй tools, write или history."
)
_ADVISOR_RESULT_FIELDS = frozenset(
    {
        "output_label",
        "kind",
        "recommendation",
        "selected_option",
        "rationale",
        "evidence_refs",
        "constraints_used",
        "objectives_used",
        "uncertainty",
        "abstention_code",
        "contract_version",
    }
)
_ADVISOR_SELECTED_OPTION_FIELDS = frozenset({"id", "label"})
_ADVISOR_EVIDENCE_REF_FIELDS = frozenset({"source", "ordinal", "role"})
_ADVISOR_INPUT_REF_FIELDS = frozenset({"source", "ordinal"})


def _compact_json_bytes(payload: object) -> bytes:
    """Serialize provider JSON with deterministic key order and bounded UTF-8 bytes."""

    return json.dumps(
        payload,
        ensure_ascii=True,
        allow_nan=False,
        separators=(",", ":"),
    ).encode("utf-8")


def escape_advisor_envelope(raw: str) -> str:
    """Escape only exact advisor framing markers in one left-to-right pass."""

    pieces: list[str] = []
    index = 0
    while index < len(raw):
        if raw.startswith(_ADVISOR_BEGIN_MARKER, index):
            pieces.append(_ADVISOR_ESCAPED_BEGIN_MARKER)
            index += len(_ADVISOR_BEGIN_MARKER)
        elif raw.startswith(_ADVISOR_END_MARKER, index):
            pieces.append(_ADVISOR_ESCAPED_END_MARKER)
            index += len(_ADVISOR_END_MARKER)
        else:
            pieces.append(raw[index])
            index += 1
    return "".join(pieces)


def _advisor_result_schema() -> dict[str, object]:
    """Return the closed provider schema; application validation remains final."""

    return {
        "type": "object",
        "additionalProperties": False,
        "required": [
            "output_label",
            "kind",
            "recommendation",
            "selected_option",
            "rationale",
            "evidence_refs",
            "constraints_used",
            "objectives_used",
            "uncertainty",
            "abstention_code",
            "contract_version",
        ],
        "properties": {
            "output_label": {"type": "string", "enum": [ASSISTANT_OUTPUT_LABEL]},
            "kind": {
                "type": "string",
                "enum": [member.value for member in AssistantResultKind],
            },
            "recommendation": {"type": ["string", "null"]},
            "selected_option": {
                "type": ["object", "null"],
                "additionalProperties": False,
                "required": ["id", "label"],
                "properties": {"id": {"type": "string"}, "label": {"type": "string"}},
            },
            "rationale": {
                "type": "array",
                "minItems": 1,
                "maxItems": MAX_RATIONALE,
                "items": {"type": "string"},
            },
            "evidence_refs": {
                "type": "array",
                "maxItems": MAX_EVIDENCE_REFS,
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["source", "ordinal", "role"],
                    "properties": {
                        "source": {"type": "string", "enum": ["explicit_context"]},
                        "ordinal": {
                            "type": "integer",
                            "minimum": 1,
                            "maximum": MAX_CONTEXT_ENTRIES,
                        },
                        "role": {
                            "type": "string",
                            "enum": ["reported_fact", "background"],
                        },
                    },
                },
            },
            "constraints_used": {
                "type": "array",
                "maxItems": MAX_INPUT_REFS,
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["source", "ordinal"],
                    "properties": {
                        "source": {"type": "string", "enum": ["explicit_constraint"]},
                        "ordinal": {"type": "integer", "minimum": 1, "maximum": MAX_CONSTRAINTS},
                    },
                },
            },
            "objectives_used": {
                "type": "array",
                "maxItems": MAX_OBJECTIVE_REFS,
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["source", "ordinal"],
                    "properties": {
                        "source": {"type": "string", "enum": ["explicit_goal"]},
                        "ordinal": {"type": "integer", "minimum": 1, "maximum": MAX_GOALS},
                    },
                },
            },
            "uncertainty": {
                "type": "array",
                "maxItems": MAX_UNCERTAINTY,
                "items": {"type": "string"},
            },
            "abstention_code": {
                "type": ["string", "null"],
                "enum": [member.value for member in AssistantAbstentionCode] + [None],
            },
            "contract_version": {
                "type": "string",
                "enum": [ASSISTANT_CONTRACT_VERSION],
            },
        },
    }


def _advisor_request_payload(canonical_envelope: bytes) -> dict[str, object]:
    """Build the one fixed request shape around canonical explicit envelope bytes."""

    try:
        envelope_text = canonical_envelope.decode("utf-8")
    except AttributeError, UnicodeDecodeError:
        raise AssistantInvalidRequestError() from None
    user_message = (
        "Канонический explicit Assistant envelope v1 — единственный источник "
        "входных данных:\n"
        f"{_ADVISOR_BEGIN_MARKER}\n"
        f"{escape_advisor_envelope(envelope_text)}\n"
        f"{_ADVISOR_END_MARKER}"
    )
    return {
        "model": CLOUDFLARE_MODEL,
        "messages": [
            {"role": "system", "content": _ADVISOR_SYSTEM_MESSAGE},
            {"role": "user", "content": user_message},
        ],
        "response_format": {"type": "json_schema", "json_schema": _advisor_result_schema()},
        "stream": False,
        "temperature": 0,
        "reasoning_effort": None,
        "chat_template_kwargs": {"enable_thinking": False},
    }


def _maximum_reasoning_envelope() -> AssistantReasoningEnvelopeV1:
    """Construct the global-max envelope used to derive the request wire cap."""

    return AssistantReasoningEnvelopeV1(
        task="я" * (MAX_TASK_BYTES // 2),
        options=tuple(
            AssistantOption(
                f"{index:02d}" + "a" * (MAX_OPTION_ID_BYTES - 2),
                "я" * (MAX_OPTION_LABEL_BYTES // 2),
            )
            for index in range(MAX_OPTIONS)
        ),
        explicit_constraints=tuple(
            "я" * (MAX_CONSTRAINT_BYTES // 2) for _ in range(MAX_CONSTRAINTS)
        ),
        explicit_goals=tuple("я" * (MAX_GOAL_BYTES // 2) for _ in range(MAX_GOALS)),
        explicit_context=tuple(
            AssistantExplicitContext(
                AssistantContextKind.FACT,
                "я" * (MAX_CONTEXT_TEXT_BYTES // 2),
            )
            for _ in range(MAX_CONTEXT_ENTRIES)
        ),
    )


def _maximum_result_envelope() -> AssistantResultEnvelopeV1:
    """Construct the largest structurally valid result for response-cap derivation."""

    return AssistantResultEnvelopeV1(
        output_label=ASSISTANT_OUTPUT_LABEL,
        kind=AssistantResultKind.RECOMMENDATION,
        recommendation="я" * (MAX_RECOMMENDATION_BYTES // 2),
        selected_option=AssistantOption(
            "a" * MAX_OPTION_ID_BYTES,
            "я" * (MAX_OPTION_LABEL_BYTES // 2),
        ),
        rationale=tuple("я" * (MAX_RATIONALE_ITEM_BYTES // 2) for _ in range(MAX_RATIONALE)),
        evidence_refs=tuple(
            AssistantEvidenceRef(
                AssistantEvidenceSource.EXPLICIT_CONTEXT,
                (ordinal - 1) % MAX_CONTEXT_ENTRIES + 1,
                AssistantEvidenceRole.REPORTED_FACT,
            )
            for ordinal in range(1, MAX_EVIDENCE_REFS + 1)
        ),
        constraints_used=tuple(
            AssistantInputRef(AssistantInputSource.EXPLICIT_CONSTRAINT, ordinal)
            for ordinal in range(1, MAX_INPUT_REFS + 1)
        ),
        objectives_used=tuple(
            AssistantInputRef(AssistantInputSource.EXPLICIT_GOAL, ordinal)
            for ordinal in range(1, MAX_OBJECTIVE_REFS + 1)
        ),
        uncertainty=tuple("я" * (MAX_UNCERTAINTY_ITEM_BYTES // 2) for _ in range(MAX_UNCERTAINTY)),
        abstention_code=None,
        contract_version=ASSISTANT_CONTRACT_VERSION,
    )


_MAX_REASONING_ENVELOPE_BYTES = len(
    serialize_assistant_reasoning_envelope(_maximum_reasoning_envelope())
)
_MAX_RESULT_ENVELOPE_BYTES = len(serialize_assistant_result_envelope(_maximum_result_envelope()))
_ADVISOR_MARKER_EXPANSION_BYTES = 5 * (
    _MAX_REASONING_ENVELOPE_BYTES // min(len(_ADVISOR_BEGIN_MARKER), len(_ADVISOR_END_MARKER))
)
ADVISOR_REQUEST_BODY_CAP = (
    len(
        _compact_json_bytes(
            _advisor_request_payload(
                serialize_assistant_reasoning_envelope(_maximum_reasoning_envelope())
            )
        )
    )
    + _ADVISOR_MARKER_EXPANSION_BYTES
)
ADVISOR_RESULT_ENVELOPE_BYTES = _MAX_RESULT_ENVELOPE_BYTES
ADVISOR_RESPONSE_BODY_CAP = (
    len(
        _compact_json_bytes(
            {
                "choices": [
                    {
                        "index": 0,
                        "message": {
                            "role": "assistant",
                            "content": serialize_assistant_result_envelope(
                                _maximum_result_envelope()
                            ).decode("utf-8"),
                        },
                        "finish_reason": "stop",
                    }
                ]
            }
        )
    )
    + ENVELOPE_OVERHEAD_BYTES
)


def build_advisor_request_body(request: object) -> bytes:
    """Validate the exact envelope and build one bounded Cloudflare request body."""

    canonical_envelope = serialize_assistant_reasoning_envelope(request)
    if len(canonical_envelope) > MAX_CONTEXT_BYTES:
        raise AssistantInvalidRequestError()
    try:
        body = _compact_json_bytes(_advisor_request_payload(canonical_envelope))
    except TypeError, UnicodeEncodeError, ValueError:
        raise AssistantInvalidRequestError() from None
    if len(body) > ADVISOR_REQUEST_BODY_CAP:
        raise AssistantInvalidRequestError()
    return body


def _is_cancelled(cancellation: CancellationToken) -> bool:
    """Read cancellation through the Assistant-safe signal boundary."""

    checker = getattr(cancellation, "is_cancelled", None)
    if not callable(checker):
        raise AssistantInvalidRequestError()
    try:
        value = checker()
    except Exception:
        raise AssistantInvalidRequestError() from None
    if type(value) is not bool:
        raise AssistantInvalidRequestError()
    return value


def _raise_worker_result_error(result: _WorkerResult) -> None:
    """Map shared Cloudflare worker outcomes to the Assistant taxonomy."""

    category = _cloudflare_result_category(result)
    if category == "too_large":
        raise AssistantResultTooLargeError()
    if category == "timeout":
        raise AssistantTimeoutError()
    if category == "unavailable":
        raise AssistantProviderUnavailableError()
    if category == "malformed":
        raise AssistantMalformedResultError()
    if category == "upstream":
        raise AssistantProviderFailureError()
    raise AssistantMalformedResultError()


def _exact_mapping(value: object, fields: frozenset[str]) -> dict[str, object]:
    """Require an exact JSON object before converting it into a typed DTO."""

    if type(value) is not dict or set(value) != fields:
        raise AssistantMalformedResultError()
    return cast(dict[str, object], value)


def _string_array(payload: Mapping[str, object], field_name: str) -> tuple[str, ...]:
    """Decode a bounded string array without repair or item dropping."""

    values = payload.get(field_name)
    if type(values) is not list or any(type(value) is not str for value in values):
        raise AssistantMalformedResultError()
    return tuple(cast(str, value) for value in values)


def _decode_selected_option(value: object) -> AssistantOption | None:
    """Decode the caller-option-shaped result field without accepting extra fields."""

    if value is None:
        return None
    selected = _exact_mapping(value, _ADVISOR_SELECTED_OPTION_FIELDS)
    option_id = selected.get("id")
    label = selected.get("label")
    if type(option_id) is not str or type(label) is not str:
        raise AssistantMalformedResultError()
    return AssistantOption(option_id, label)


def _decode_evidence_refs(value: object) -> tuple[AssistantEvidenceRef, ...]:
    """Decode exact explicit-context evidence reference objects."""

    if type(value) is not list:
        raise AssistantMalformedResultError()
    refs: list[AssistantEvidenceRef] = []
    for item in value:
        raw = _exact_mapping(item, _ADVISOR_EVIDENCE_REF_FIELDS)
        source = raw.get("source")
        ordinal = raw.get("ordinal")
        role = raw.get("role")
        if type(source) is not str or type(ordinal) is not int or type(role) is not str:
            raise AssistantMalformedResultError()
        refs.append(AssistantEvidenceRef(source, ordinal, role))
    return tuple(refs)


def _decode_input_refs(value: object) -> tuple[AssistantInputRef, ...]:
    """Decode exact constraint/goal reference objects."""

    if type(value) is not list:
        raise AssistantMalformedResultError()
    refs: list[AssistantInputRef] = []
    for item in value:
        raw = _exact_mapping(item, _ADVISOR_INPUT_REF_FIELDS)
        source = raw.get("source")
        ordinal = raw.get("ordinal")
        if type(source) is not str or type(ordinal) is not int:
            raise AssistantMalformedResultError()
        refs.append(AssistantInputRef(source, ordinal))
    return tuple(refs)


def _decode_result_payload(payload: object) -> AssistantResultEnvelopeV1:
    """Decode exact inner Assistant JSON, then reuse structural normalization."""

    result = _exact_mapping(payload, _ADVISOR_RESULT_FIELDS)
    output_label = result.get("output_label")
    kind = result.get("kind")
    recommendation = result.get("recommendation")
    abstention_code = result.get("abstention_code")
    contract_version = result.get("contract_version")
    if (
        type(output_label) is not str
        or type(kind) is not str
        or (recommendation is not None and type(recommendation) is not str)
        or (abstention_code is not None and type(abstention_code) is not str)
        or type(contract_version) is not str
    ):
        raise AssistantMalformedResultError()
    decoded = AssistantResultEnvelopeV1(
        output_label=output_label,
        kind=kind,
        recommendation=recommendation,
        selected_option=_decode_selected_option(result.get("selected_option")),
        rationale=_string_array(result, "rationale"),
        evidence_refs=_decode_evidence_refs(result.get("evidence_refs")),
        constraints_used=_decode_input_refs(result.get("constraints_used")),
        objectives_used=_decode_input_refs(result.get("objectives_used")),
        uncertainty=_string_array(result, "uncertainty"),
        abstention_code=abstention_code,
        contract_version=contract_version,
    )
    normalized = normalize_assistant_result_structure(decoded)
    if len(serialize_assistant_result_envelope(normalized)) > MAX_RESULT_BYTES:
        raise AssistantResultTooLargeError()
    return normalized


def _decode_assistant_result(result: _WorkerResult) -> AssistantResultEnvelopeV1:
    """Decode one bounded OpenAI-compatible response and its exact inner DTO."""

    if result.kind != "http":
        _raise_worker_result_error(result)
    if result.http_status != 200:
        _raise_worker_result_error(result)
    if type(result.body) is not bytes:
        raise AssistantMalformedResultError()
    if len(result.body) > ADVISOR_RESPONSE_BODY_CAP:
        raise AssistantResultTooLargeError()
    try:
        outer = _load_json_object(result.body)
    except UnicodeDecodeError, json.JSONDecodeError, RecursionError, TypeError, ValueError:
        raise AssistantMalformedResultError() from None
    if type(outer) is not dict or "error" in outer:
        raise AssistantMalformedResultError()
    choices = outer.get("choices")
    if type(choices) is not list or len(choices) != 1:
        raise AssistantMalformedResultError()
    choice = choices[0]
    if type(choice) is not dict:
        raise AssistantMalformedResultError()
    if choice.get("finish_reason") != "stop" or _has_non_null_forbidden_field(choice):
        raise AssistantMalformedResultError()
    message = choice.get("message")
    if type(message) is not dict or _has_non_null_forbidden_field(message):
        raise AssistantMalformedResultError()
    content = message.get("content")
    if type(content) is not str:
        raise AssistantMalformedResultError()
    try:
        inner = _load_json_object(content)
    except UnicodeDecodeError, json.JSONDecodeError, RecursionError, TypeError, ValueError:
        raise AssistantMalformedResultError() from None
    return _decode_result_payload(inner)


@dataclass(slots=True)
class CloudflareWorkersAiAdvisorPort:
    """Independent Assistant advisor using the existing Cloudflare worker boundary."""

    config: CloudflareWorkersAiConfig | None = field(default=None, repr=False)
    runner: WorkerRunner = field(default_factory=SubprocessWorkerRunner, repr=False)
    clock: Callable[[], float] = field(default=time.monotonic, repr=False)

    def advise(
        self,
        request: AssistantReasoningEnvelopeV1,
        *,
        cancellation: CancellationToken,
    ) -> AssistantResultEnvelopeV1:
        """Perform one explicit-only provider call and return one typed result."""

        if _is_cancelled(cancellation):
            raise AssistantCancelledError()
        body = build_advisor_request_body(request)
        try:
            config = self.config if self.config is not None else load_cloudflare_workers_ai_config()
            deadline = self.clock() + ADVISOR_TOTAL_DEADLINE_SECONDS
        except CloudflareWorkersAiConfigError:
            raise AssistantProviderUnavailableError() from None
        except OSError, TypeError, ValueError:
            raise AssistantProviderUnavailableError() from None
        if type(config) is not CloudflareWorkersAiConfig:
            raise AssistantProviderUnavailableError()
        if _is_cancelled(cancellation):
            raise AssistantCancelledError()
        worker_request = _WorkerRequest(
            account_id=config.account_id,
            api_token=config.api_token,
            body=body,
            max_output_bytes=1,
            response_cap=ADVISOR_RESPONSE_BODY_CAP,
        )
        try:
            result = self.runner.run(
                worker_request,
                deadline=deadline,
                cancellation=cancellation,
            )
        except _WorkerCancelled:
            raise AssistantCancelledError() from None
        except _WorkerTimedOut:
            raise AssistantTimeoutError() from None
        except _WorkerContentTooLarge:
            raise AssistantResultTooLargeError() from None
        except _WorkerMalformed, _WorkerUnavailable:
            raise AssistantProviderUnavailableError() from None
        except AssistantError:
            raise
        except TimeoutError:
            raise AssistantTimeoutError() from None
        except Exception:
            raise AssistantProviderUnavailableError() from None
        if _is_cancelled(cancellation):
            raise AssistantCancelledError()
        if self.clock() >= deadline:
            raise AssistantTimeoutError()
        decoded = _decode_assistant_result(result)
        if _is_cancelled(cancellation):
            raise AssistantCancelledError()
        if self.clock() >= deadline:
            raise AssistantTimeoutError()
        return decoded


__all__ = [
    "ADVISOR_DEFAULT_MAX_CONTEXT_BYTES",
    "ADVISOR_REQUEST_BODY_CAP",
    "ADVISOR_RESPONSE_BODY_CAP",
    "ADVISOR_RESULT_ENVELOPE_BYTES",
    "ADVISOR_TOTAL_DEADLINE_SECONDS",
    "CLOUDFLARE_ACCOUNT_ID_ENV",
    "CLOUDFLARE_ADVISOR_API_HOST",
    "CLOUDFLARE_ADVISOR_MODEL",
    "CLOUDFLARE_ADVISOR_PATH",
    "CLOUDFLARE_ADVISOR_PROVIDER",
    "CLOUDFLARE_API_HOST",
    "CLOUDFLARE_API_TOKEN_ENV",
    "CLOUDFLARE_MODEL",
    "CLOUDFLARE_PROVIDER",
    "CloudflareWorkersAiAdvisorPort",
    "CloudflareWorkersAiConfig",
    "CloudflareWorkersAiConfigError",
    "build_advisor_request_body",
    "escape_advisor_envelope",
]
