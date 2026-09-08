"""Deterministic tests for the explicit-context-only Assistant v1 core."""

from __future__ import annotations

from dataclasses import replace
from typing import Any, cast

import pytest

from second_brain.application.assistant import (
    ASSISTANT_CONTRACT_VERSION,
    ASSISTANT_OUTPUT_LABEL,
    MAX_CONTEXT_BYTES,
    MIN_MAX_RESULT_BYTES_V1,
    AssistantAbstentionCode,
    AssistantCancelledError,
    AssistantCanonicalJsonEncoderV1,
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
    AssistantReasoningEnvelopeV1,
    AssistantRequest,
    AssistantResultEnvelopeV1,
    AssistantResultInvalidError,
    AssistantResultKind,
    AssistantResultTooLargeError,
    BuildAssistant,
    build_assistant_reasoning_envelope,
    minimum_assistant_result_bytes,
    serialize_assistant_reasoning_envelope,
    serialize_assistant_result_envelope,
    validate_assistant_request,
    validate_assistant_result,
)
from second_brain.application.ports import CancellationToken, CancellationTokenSource


def _request(**changes: object) -> AssistantRequest:
    base = AssistantRequest(
        task="Выбрать следующий шаг",
        options=(AssistantOption("a", "Первый вариант"), AssistantOption("b", "Второй вариант")),
        explicit_constraints=("Не превышать бюджет",),
        explicit_goals=("Сохранить качество",),
        explicit_context=(AssistantExplicitContext("fact", "У меня есть два часа"),),
    )
    return replace(base, **cast(Any, changes))


def _abstention(
    code: AssistantAbstentionCode = AssistantAbstentionCode.INSUFFICIENT_BASIS,
) -> AssistantResultEnvelopeV1:
    return AssistantResultEnvelopeV1(
        output_label=ASSISTANT_OUTPUT_LABEL,
        kind=AssistantResultKind.ABSTENTION,
        recommendation=None,
        selected_option=None,
        rationale=("Недостаточно явных оснований",),
        evidence_refs=(),
        constraints_used=(),
        objectives_used=(),
        uncertainty=(),
        abstention_code=code,
        contract_version=ASSISTANT_CONTRACT_VERSION,
    )


def _recommendation(**changes: object) -> AssistantResultEnvelopeV1:
    base = AssistantResultEnvelopeV1(
        output_label=ASSISTANT_OUTPUT_LABEL,
        kind=AssistantResultKind.RECOMMENDATION,
        recommendation="Выбрать первый вариант",
        selected_option=AssistantOption("a", "Первый вариант"),
        rationale=("Он соответствует явной цели",),
        evidence_refs=(
            AssistantEvidenceRef(
                AssistantEvidenceSource.EXPLICIT_CONTEXT,
                1,
                AssistantEvidenceRole.REPORTED_FACT,
            ),
        ),
        constraints_used=(AssistantInputRef(AssistantInputSource.EXPLICIT_CONSTRAINT, 1),),
        objectives_used=(AssistantInputRef(AssistantInputSource.EXPLICIT_GOAL, 1),),
        uncertainty=("Контекст ограничен текущим запросом",),
        abstention_code=None,
        contract_version=ASSISTANT_CONTRACT_VERSION,
    )
    return replace(base, **cast(Any, changes))


class _FakeAdvisorPort:
    """Deterministic test double that records no runtime state in production."""

    def __init__(self, result: AssistantResultEnvelopeV1 | Exception) -> None:
        self.result = result
        self.calls = 0
        self.requests: list[AssistantReasoningEnvelopeV1] = []

    def advise(
        self,
        request: AssistantReasoningEnvelopeV1,
        *,
        cancellation: CancellationToken,
    ) -> AssistantResultEnvelopeV1:
        del cancellation
        self.calls += 1
        self.requests.append(request)
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


def test_request_is_normalized_but_only_explicit_fields_reach_advisor() -> None:
    request = _request(
        task="  Задача e\u0301  ",
        explicit_context=(AssistantExplicitContext("fact", "  Явленный факт  "),),
    )
    validated = validate_assistant_request(request)
    assert validated.task == "Задача é"
    assert validated.explicit_context == (
        AssistantExplicitContext(AssistantContextKind.FACT, "Явленный факт"),
    )

    fake = _FakeAdvisorPort(_abstention())
    result = BuildAssistant(fake).execute(request, cancellation=CancellationTokenSource().token)

    assert result.kind is AssistantResultKind.ABSTENTION
    assert fake.calls == 1
    assert fake.requests[0] == AssistantReasoningEnvelopeV1(
        task="Задача é",
        options=request.options,
        explicit_constraints=request.explicit_constraints,
        explicit_goals=request.explicit_goals,
        explicit_context=(AssistantExplicitContext(AssistantContextKind.FACT, "Явленный факт"),),
    )
    assert not hasattr(fake.requests[0], "stage5_facts")
    assert not hasattr(fake.requests[0], "prediction")


@pytest.mark.parametrize(
    "candidate",
    (
        {"task": "task"},
        AssistantRequest("\nnot accepted"),
        AssistantRequest("task", options=tuple(AssistantOption(str(i), "x") for i in range(9))),
        AssistantRequest("task", options=(AssistantOption("bad id", "x"),)),
        AssistantRequest("task", options=(AssistantOption("a", "x"), AssistantOption("a", "y"))),
        AssistantRequest("task", explicit_constraints=(True,)),  # type: ignore[arg-type]
        AssistantRequest("task", max_context_bytes=True),
        AssistantRequest("task", max_result_bytes=MIN_MAX_RESULT_BYTES_V1 - 1),
    ),
)
def test_invalid_request_is_rejected_before_advisor_call(candidate: object) -> None:
    fake = _FakeAdvisorPort(_abstention())

    with pytest.raises(AssistantInvalidRequestError):
        BuildAssistant(fake).execute(candidate, cancellation=CancellationTokenSource().token)  # type: ignore[arg-type]

    assert fake.calls == 0


def test_bounds_and_closed_context_kind_are_enforced() -> None:
    with pytest.raises(AssistantInvalidRequestError):
        validate_assistant_request(
            _request(explicit_context=(AssistantExplicitContext("unsupported", "x"),))
        )
    with pytest.raises(AssistantInvalidRequestError):
        validate_assistant_request(_request(task="x\u200b"))
    with pytest.raises(AssistantInvalidRequestError):
        validate_assistant_request(_request(explicit_goals=tuple("x" for _ in range(9))))
    with pytest.raises(AssistantInvalidRequestError):
        validate_assistant_request(_request(explicit_constraints=("x" * 513,)))


def test_canonical_encoder_is_compact_ordered_direct_utf8_and_exactly_escapes_controls() -> None:
    encoded = AssistantCanonicalJsonEncoderV1.encode(
        {"text": 'Ё / " \\ \b \t \n \f \r \x00 \x0b \x1f'}
    )

    assert encoded.decode("utf-8") == r'{"text":"Ё / \" \\ \b \t \n \f \r \u0000 \u000b \u001f"}'
    assert b"\\u0411" not in encoded
    assert b"\\/" not in encoded


def test_reasoning_envelope_has_stable_root_and_nested_key_order() -> None:
    envelope = build_assistant_reasoning_envelope(_request())

    assert serialize_assistant_reasoning_envelope(envelope).decode("utf-8") == (
        '{"task":"Выбрать следующий шаг","options":[{"id":"a","label":"Первый вариант"},'
        '{"id":"b","label":"Второй вариант"}],"explicit_constraints":["Не превышать бюджет"],'
        '"explicit_goals":["Сохранить качество"],"explicit_context":[{"kind":"fact",'
        '"text":"У меня есть два часа"}]}'
    )


def test_minimum_result_bound_is_the_contract_derived_304_bytes() -> None:
    assert minimum_assistant_result_bytes() == MIN_MAX_RESULT_BYTES_V1 == 304
    assert len(serialize_assistant_result_envelope(_abstention())) > 304


def test_valid_recommendation_binds_options_inputs_and_context_roles() -> None:
    request = _request()
    fake = _FakeAdvisorPort(_recommendation())

    result = BuildAssistant(fake).execute(request, cancellation=CancellationTokenSource().token)

    assert result == _recommendation()
    assert fake.calls == 1
    assert fake.requests[0].explicit_context[0].kind is AssistantContextKind.FACT


@pytest.mark.parametrize(
    "change",
    (
        {"kind": AssistantResultKind.ANALYSIS, "recommendation": "not allowed"},
        {"kind": AssistantResultKind.ABSTENTION, "abstention_code": None},
        {"selected_option": AssistantOption("other", "Other")},
        {
            "evidence_refs": (
                AssistantEvidenceRef(
                    AssistantEvidenceSource.EXPLICIT_CONTEXT,
                    1,
                    AssistantEvidenceRole.BACKGROUND,
                ),
            )
        },
        {"constraints_used": (AssistantInputRef(AssistantInputSource.EXPLICIT_GOAL, 1),)},
    ),
)
def test_semantic_or_source_binding_failures_are_result_invalid(change: dict[str, object]) -> None:
    with pytest.raises(AssistantResultInvalidError):
        validate_assistant_result(
            replace(_recommendation(), **cast(Any, change)),
            request=_request(),
        )


@pytest.mark.parametrize(
    "change",
    (
        {"kind": "unknown"},
        {"rationale": ()},
        {"uncertainty": ("\u200b",)},
        {"evidence_refs": (object(),)},
        {"constraints_used": (AssistantInputRef("explicit_constraint", True),)},
    ),
)
def test_structural_result_failures_are_malformed(change: dict[str, object]) -> None:
    with pytest.raises(AssistantMalformedResultError):
        validate_assistant_result(
            replace(_recommendation(), **cast(Any, change)),
            request=_request(),
        )


def test_result_size_is_checked_after_semantic_validation_without_truncation() -> None:
    request = _request(max_result_bytes=MIN_MAX_RESULT_BYTES_V1)
    fake = _FakeAdvisorPort(_recommendation())

    with pytest.raises(AssistantResultTooLargeError):
        BuildAssistant(fake).execute(request, cancellation=CancellationTokenSource().token)

    assert fake.calls == 1
    assert "Выбрать" not in str(AssistantResultTooLargeError())


def test_context_budget_accepts_exact_boundary_and_rejects_plus_one_before_advisor() -> None:
    draft = _request()
    envelope = build_assistant_reasoning_envelope(draft)
    exact = len(serialize_assistant_reasoning_envelope(envelope))
    assert exact <= MAX_CONTEXT_BYTES

    accepted_request = replace(draft, max_context_bytes=exact)
    accepted_fake = _FakeAdvisorPort(_abstention())
    BuildAssistant(accepted_fake).execute(
        accepted_request,
        cancellation=CancellationTokenSource().token,
    )
    assert accepted_fake.calls == 1

    rejected_fake = _FakeAdvisorPort(_abstention())
    with pytest.raises(AssistantInvalidRequestError):
        BuildAssistant(rejected_fake).execute(
            replace(draft, max_context_bytes=exact - 1),
            cancellation=CancellationTokenSource().token,
        )
    assert rejected_fake.calls == 0


def test_cancellation_before_and_after_port_call_is_safe_and_at_most_once() -> None:
    before = CancellationTokenSource()
    before.cancel()
    fake = _FakeAdvisorPort(_abstention())
    with pytest.raises(AssistantCancelledError):
        BuildAssistant(fake).execute(_request(), cancellation=before.token)
    assert fake.calls == 0

    class _CancellingAdvisor:
        def __init__(self) -> None:
            self.calls = 0

        def advise(
            self,
            request: AssistantReasoningEnvelopeV1,
            *,
            cancellation: CancellationToken,
        ) -> AssistantResultEnvelopeV1:
            del request
            self.calls += 1
            assert isinstance(cancellation, CancellationTokenSource)
            cancellation.cancel()
            return _abstention()

    after_source = CancellationTokenSource()
    advisor = _CancellingAdvisor()
    with pytest.raises(AssistantCancelledError):
        BuildAssistant(advisor).execute(_request(), cancellation=after_source.token)
    assert advisor.calls == 1


def test_advisor_errors_are_mapped_without_leaking_exception_details() -> None:
    fake = _FakeAdvisorPort(ValueError("private prompt and /secret/path"))

    with pytest.raises(AssistantProviderFailureError) as caught:
        BuildAssistant(fake).execute(_request(), cancellation=CancellationTokenSource().token)

    assert caught.value.as_dict() == {
        "code": "ASSISTANT_PROVIDER_FAILURE",
        "message": "assistant advisor failed",
    }
    assert "private" not in str(caught.value)
    assert isinstance(caught.value, AssistantError)
