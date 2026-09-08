"""Deterministic tests for the approved provider-free Compare v1 core."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from uuid import UUID

import pytest

from second_brain.application.assistant import (
    ASSISTANT_CONTRACT_VERSION,
    ASSISTANT_OUTPUT_LABEL,
    AssistantAbstentionCode,
    AssistantError,
    AssistantEvidenceRef,
    AssistantExplicitContext,
    AssistantOption,
    AssistantProviderFailureError,
    AssistantReasoningEnvelopeV1,
    AssistantResultEnvelopeV1,
    AssistantResultKind,
)
from second_brain.application.compare import (
    COMPARE_DERIVATION_VERSION,
    COMPARE_POLICY_ID,
    MIN_ASSISTANT_CONTEXT_BYTES_V1,
    MIN_MAX_RESULT_BYTES_V1,
    POLICY_FINGERPRINT,
    BuildCompare,
    CompareAssistantErrorBranchV1,
    CompareAssistantInputsV1,
    CompareAssistantResultBranchV1,
    CompareBranchErrorCodeV1,
    CompareBranchStateV1,
    CompareDeltaRelationV1,
    CompareErrorCodeV1,
    CompareErrorV1,
    CompareExecutionContextV1,
    CompareOptionV1,
    CompareRequestV1,
    CompareResultV1,
    CompareSimulateMeErrorBranchV1,
    CompareSimulateMeResultBranchV1,
    SimulateMeComparePort,
    minimum_compare_result_bytes,
    serialize_compare_result,
    validate_compare_policy,
    validate_compare_request,
    validate_compare_result,
)
from second_brain.application.ports import CancellationTokenSource
from second_brain.application.simulate_me import (
    DERIVATION_VERSION as SIMULATE_ME_DERIVATION_VERSION,
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
    SimulateMeEvidenceRef,
    SimulateMeOption,
    SimulateMeRequest,
    SimulateMeResult,
    SimulateMeResultKind,
    SimulateMeTemporalCaveat,
    SimulateMeTemporalCaveatCode,
)


def _uuid(index: int) -> UUID:
    return UUID(f"0198f4c5-6a00-7000-8000-{index:012d}")


def _request(
    *,
    max_result_bytes: int = 128 * 1024,
    options: tuple[CompareOptionV1, ...] = (CompareOptionV1("a", "A"),),
) -> CompareRequestV1:
    return CompareRequestV1(
        task="task",
        options=options,
        assistant=CompareAssistantInputsV1(
            explicit_constraints=("constraint",),
            explicit_goals=("goal",),
            explicit_context=(AssistantExplicitContext("fact", "reported"),),
        ),
        max_result_bytes=max_result_bytes,
    )


def _assistant_result(
    *,
    selected: str | None = "a",
    label: str = "A",
    kind: AssistantResultKind = AssistantResultKind.RECOMMENDATION,
    evidence: tuple[AssistantEvidenceRef, ...] = (),
) -> AssistantResultEnvelopeV1:
    option = None if selected is None else AssistantOption(selected, label)
    recommendation = "recommendation" if kind is AssistantResultKind.RECOMMENDATION else None
    abstention = (
        AssistantAbstentionCode.INSUFFICIENT_BASIS
        if kind is AssistantResultKind.ABSTENTION
        else None
    )
    return AssistantResultEnvelopeV1(
        output_label=ASSISTANT_OUTPUT_LABEL,
        kind=kind,
        recommendation=recommendation,
        selected_option=option,
        rationale=("because",),
        evidence_refs=evidence,
        constraints_used=(),
        objectives_used=(),
        uncertainty=(),
        abstention_code=abstention,
        contract_version=ASSISTANT_CONTRACT_VERSION,
    )


def _simulate_result(
    *,
    selected: str | None = "a",
    label: str = "A",
    evidence_refs: tuple[SimulateMeEvidenceRef, ...] = (),
    contextual_refs: tuple[SimulateMeContextualEvidenceRef, ...] = (),
    caveats: tuple[SimulateMeTemporalCaveat, ...] = (),
    kind: SimulateMeResultKind | None = None,
) -> SimulateMeResult:
    actual_kind = kind or (
        SimulateMeResultKind.ABSTENTION if selected is None else SimulateMeResultKind.PREDICTION
    )
    option = None if selected is None else SimulateMeOption(selected, label)
    abstention = (
        SimulateMeAbstentionCode.NO_MATCHING_EVIDENCE
        if actual_kind is SimulateMeResultKind.ABSTENTION
        else None
    )
    return SimulateMeResult(
        kind=actual_kind,
        selected_option=option,
        evidence_refs=evidence_refs,
        contextual_evidence_refs=contextual_refs,
        temporal_caveats=caveats,
        abstention_code=abstention,
        derivation_version=SIMULATE_ME_DERIVATION_VERSION,
        policy_id=SIMULATE_ME_POLICY_ID,
        policy_fingerprint=SIMULATE_ME_POLICY_FINGERPRINT,
    )


@dataclass
class _FakeAssistant:
    result: AssistantResultEnvelopeV1 | Exception
    calls: list[tuple[AssistantReasoningEnvelopeV1, CompareExecutionContextV1]]

    def __init__(self, result: AssistantResultEnvelopeV1 | Exception) -> None:
        self.result = result
        self.calls = []

    def advise(
        self,
        request: AssistantReasoningEnvelopeV1,
        *,
        execution: CompareExecutionContextV1,
    ) -> AssistantResultEnvelopeV1:
        self.calls.append((request, execution))
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


@dataclass
class _FakeSimulateMe(SimulateMeComparePort):
    result: SimulateMeResult | Exception
    calls: list[tuple[SimulateMeRequest, CompareExecutionContextV1]]

    def __init__(self, result: SimulateMeResult | Exception) -> None:
        self.result = result
        self.calls = []

    def execute(
        self,
        request: SimulateMeRequest,
        *,
        execution: CompareExecutionContextV1,
    ) -> SimulateMeResult:
        self.calls.append((request, execution))
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


def _service(
    *,
    assistant_result: AssistantResultEnvelopeV1 | Exception | None = None,
    simulate_result: SimulateMeResult | Exception | None = None,
    clock: Callable[[], float] | None = None,
) -> tuple[BuildCompare, _FakeAssistant, _FakeSimulateMe, CancellationTokenSource]:
    assistant = _FakeAssistant(assistant_result or _assistant_result())
    simulate_me = _FakeSimulateMe(simulate_result or _simulate_result())
    cancellation = CancellationTokenSource()
    service = BuildCompare(
        assistant,
        simulate_me,
        clock=clock or _clock_zero,
    )
    return service, assistant, simulate_me, cancellation


def _execute(
    service: BuildCompare,
    request: CompareRequestV1 | None = None,
    *,
    cancellation: CancellationTokenSource,
    deadline: float = 10.0,
) -> CompareResultV1 | CompareErrorV1:
    return service.execute(
        request or _request(),
        execution=CompareExecutionContextV1(cancellation=cancellation, deadline=deadline),
    )


def _clock_zero() -> float:
    return 0.0


def _top_error(result: CompareResultV1 | CompareErrorV1) -> CompareErrorV1:
    assert isinstance(result, CompareErrorV1)
    return result


def test_policy_identity_and_contract_floors() -> None:
    assert validate_compare_policy() == POLICY_FINGERPRINT
    assert COMPARE_DERIVATION_VERSION == "compare-v1"
    assert COMPARE_POLICY_ID == "compare-structural-delta-v1"
    assert MIN_ASSISTANT_CONTEXT_BYTES_V1 == 115
    assert MIN_MAX_RESULT_BYTES_V1 == 824
    assert minimum_compare_result_bytes(("a",)) == 824
    assert minimum_compare_result_bytes(("a", "b")) == 828


def test_request_is_normalized_once_and_branch_inputs_stay_independent() -> None:
    service, assistant, simulate_me, cancellation = _service()
    request = CompareRequestV1(
        task="  task  ",
        options=(CompareOptionV1("a", "  Cafe\u0301  "),),
        assistant=CompareAssistantInputsV1(
            explicit_constraints=("  constraint  ",),
            explicit_goals=(" goal ",),
            explicit_context=(AssistantExplicitContext("fact", " report "),),
        ),
    )

    result = _execute(service, request, cancellation=cancellation)

    assert isinstance(result, CompareResultV1)
    assistant_request, assistant_execution = assistant.calls[0]
    simulate_request, simulate_execution = simulate_me.calls[0]
    assert assistant_request.task == "task"
    assert assistant_request.options == (AssistantOption("a", "Café"),)
    assert assistant_request.explicit_constraints == ("constraint",)
    assert assistant_request.explicit_goals == ("goal",)
    assert assistant_request.explicit_context == (AssistantExplicitContext("fact", "report"),)
    assert simulate_request == SimulateMeRequest("task", (SimulateMeOption("a", "Café"),))
    assert assistant_execution is simulate_execution
    assert result.option_ids == ("a",)


@pytest.mark.parametrize(
    ("assistant_selected", "simulate_selected", "relation"),
    (
        ("a", "a", CompareDeltaRelationV1.SAME_SELECTED_OPTION),
        ("a", None, CompareDeltaRelationV1.ASSISTANT_ONLY_SELECTED),
        (None, "a", CompareDeltaRelationV1.SIMULATE_ME_ONLY_SELECTED),
        (None, None, CompareDeltaRelationV1.NEITHER_SELECTED),
    ),
)
def test_delta_uses_only_exact_request_local_ids(
    assistant_selected: str | None,
    simulate_selected: str | None,
    relation: CompareDeltaRelationV1,
) -> None:
    service, _assistant, _simulate, cancellation = _service(
        assistant_result=_assistant_result(selected=assistant_selected),
        simulate_result=_simulate_result(selected=simulate_selected),
    )

    result = _execute(service, cancellation=cancellation)

    assert isinstance(result, CompareResultV1)
    assert result.delta.relation is relation
    assert result.delta.explanation_template is relation
    assert result.delta.explanation


def test_different_ids_are_different_even_when_labels_are_equal() -> None:
    assistant = _FakeAssistant(_assistant_result(selected="a", label="same"))
    simulate_me = _FakeSimulateMe(_simulate_result(selected="b", label="same"))
    service = BuildCompare(assistant, simulate_me, clock=_clock_zero)
    cancellation = CancellationTokenSource()
    request = _request(options=(CompareOptionV1("a", "same"), CompareOptionV1("b", "same")))

    result = _execute(service, request, cancellation=cancellation)

    assert isinstance(result, CompareResultV1)
    assert result.delta.relation is CompareDeltaRelationV1.DIFFERENT_SELECTED_OPTIONS


def test_assistant_error_preserves_successful_simulate_me_branch() -> None:
    service, _assistant, _simulate, cancellation = _service(
        assistant_result=AssistantProviderFailureError(),
        simulate_result=_simulate_result(),
    )

    result = _execute(service, cancellation=cancellation)

    assert isinstance(result, CompareResultV1)
    assert result.delta.relation is CompareDeltaRelationV1.ASSISTANT_ERROR
    assert isinstance(result.assistant, CompareAssistantErrorBranchV1)
    assert result.assistant.state is CompareBranchStateV1.ERROR
    assert isinstance(result.simulate_me, CompareSimulateMeResultBranchV1)
    assert result.simulate_me.result.selected_option == SimulateMeOption("a", "A")
    assert result.assistant.error is not None
    assert result.assistant.error.code is CompareBranchErrorCodeV1.FAILURE
    assert "provider" not in result.assistant.error.message


def test_both_errors_are_bounded_structural_result() -> None:
    service, _assistant, _simulate, cancellation = _service(
        assistant_result=AssistantProviderFailureError(),
        simulate_result=RuntimeError("secret backend detail"),
    )

    result = _execute(service, cancellation=cancellation)

    assert isinstance(result, CompareResultV1)
    assert result.delta.relation is CompareDeltaRelationV1.BOTH_ERROR
    assert isinstance(result.assistant, CompareAssistantErrorBranchV1)
    assert result.assistant.error is not None
    assert isinstance(result.simulate_me, CompareSimulateMeErrorBranchV1)
    assert result.simulate_me.error is not None
    assert result.simulate_me.error.code is CompareBranchErrorCodeV1.FAILURE
    assert "secret" not in result.simulate_me.error.message


def test_invalid_request_is_rejected_before_any_branch_call() -> None:
    service, assistant, simulate_me, cancellation = _service()

    result = _execute(
        service,
        CompareRequestV1("task", (), CompareAssistantInputsV1()),
        cancellation=cancellation,
    )

    assert _top_error(result).code is CompareErrorCodeV1.INVALID_REQUEST
    assert assistant.calls == []
    assert simulate_me.calls == []


def test_outer_result_floor_rejects_request_before_branch_call() -> None:
    service, assistant, simulate_me, cancellation = _service()
    request = _request(max_result_bytes=MIN_MAX_RESULT_BYTES_V1 - 1)

    result = _execute(service, request, cancellation=cancellation)

    assert _top_error(result).code is CompareErrorCodeV1.INVALID_REQUEST
    assert assistant.calls == []
    assert simulate_me.calls == []


def test_deadline_before_branch_calls_returns_two_timeout_wrappers() -> None:
    service, assistant, simulate_me, cancellation = _service(clock=lambda: 10.0)

    result = _execute(service, cancellation=cancellation, deadline=10.0)

    assert isinstance(result, CompareResultV1)
    assert result.assistant.state is CompareBranchStateV1.ERROR
    assert result.simulate_me.state is CompareBranchStateV1.ERROR
    assert isinstance(result.assistant, CompareAssistantErrorBranchV1)
    assert result.assistant.error is not None
    assert result.assistant.error.code is CompareBranchErrorCodeV1.TIMEOUT
    assert isinstance(result.simulate_me, CompareSimulateMeErrorBranchV1)
    assert result.simulate_me.error is not None
    assert result.simulate_me.error.code is CompareBranchErrorCodeV1.TIMEOUT
    assert assistant.calls == []
    assert simulate_me.calls == []


def test_cancellation_before_execution_is_top_level_and_calls_nothing() -> None:
    service, assistant, simulate_me, cancellation = _service()
    cancellation.cancel()

    result = _execute(service, cancellation=cancellation)

    assert _top_error(result).code is CompareErrorCodeV1.CANCELLED
    assert assistant.calls == []
    assert simulate_me.calls == []


def test_cancellation_after_first_branch_discards_partial_result() -> None:
    cancellation = CancellationTokenSource()

    class CancellingAssistant(_FakeAssistant):
        def advise(
            self,
            request: AssistantReasoningEnvelopeV1,
            *,
            execution: CompareExecutionContextV1,
        ) -> AssistantResultEnvelopeV1:
            result = super().advise(request, execution=execution)
            cancellation.cancel()
            return result

    assistant = CancellingAssistant(_assistant_result())
    simulate_me = _FakeSimulateMe(_simulate_result())
    service = BuildCompare(assistant, simulate_me, clock=lambda: 0.0)

    result = _execute(service, cancellation=cancellation)

    assert _top_error(result).code is CompareErrorCodeV1.CANCELLED
    assert len(assistant.calls) == 1
    assert simulate_me.calls == []


def test_simulate_me_temporal_mismatch_becomes_branch_result_invalid() -> None:
    claim_id = _uuid(1)
    ref = SimulateMeEvidenceRef(
        claim_id=claim_id,
        dimension=SimulateMeDimension.PREFERENCE,
        note_ids=(claim_id, _uuid(2)),
        evidence_at="unknown",
    )
    invalid_result = _simulate_result(evidence_refs=(ref,))
    service, _assistant, _simulate, cancellation = _service(simulate_result=invalid_result)

    result = _execute(service, cancellation=cancellation)

    assert isinstance(result, CompareResultV1)
    assert isinstance(result.assistant, CompareAssistantResultBranchV1)
    assert result.delta.relation is CompareDeltaRelationV1.SIMULATE_ME_ERROR
    assert isinstance(result.simulate_me, CompareSimulateMeErrorBranchV1)
    assert result.simulate_me.error is not None
    assert result.simulate_me.error.code is CompareBranchErrorCodeV1.RESULT_INVALID


def test_simulate_me_evidence_shape_and_unknown_caveat_are_presence_only() -> None:
    claim_id = _uuid(3)
    ref = SimulateMeContextualEvidenceRef(
        claim_id=claim_id,
        dimension=SimulateMeDimension.BELIEF,
        note_ids=(claim_id, _uuid(4)),
        evidence_at="unknown",
    )
    caveat = SimulateMeTemporalCaveat(
        code=SimulateMeTemporalCaveatCode.EVIDENCE_AT_UNKNOWN,
        claim_id=claim_id,
    )
    service, _assistant, _simulate, cancellation = _service(
        simulate_result=_simulate_result(
            selected=None,
            contextual_refs=(ref,),
            caveats=(caveat,),
        )
    )

    result = _execute(service, cancellation=cancellation)

    assert isinstance(result, CompareResultV1)
    assert result.delta.simulate_me_evidence_shape.value == "contextual_only"
    assert result.delta.simulate_me_temporal_caveat is True


def test_canonical_result_is_ordered_direct_utf8_and_datetime_is_utc_fixed_width() -> None:
    evidence_at = datetime(2026, 9, 8, 12, 34, 56, 120000, tzinfo=timezone(timedelta(hours=3)))
    ref = SimulateMeEvidenceRef(
        claim_id=_uuid(5),
        dimension=SimulateMeDimension.PREFERENCE,
        note_ids=(_uuid(5), _uuid(6)),
        evidence_at=evidence_at,
    )
    service, _assistant, _simulate, cancellation = _service(
        simulate_result=_simulate_result(evidence_refs=(ref,))
    )
    result = _execute(service, cancellation=cancellation)
    assert isinstance(result, CompareResultV1)

    encoded = serialize_compare_result(result, request=_request())
    text = encoded.decode("utf-8")
    assert text.startswith('{"option_ids":')
    assert '"assistant":{"state":' in text
    assert '"simulate_me":{"state":' in text
    assert "2026-09-08T09:34:56.120000Z" in text
    assert "\n" not in text
    assert "\\u" not in text
    assert encoded == serialize_compare_result(
        validate_compare_result(result, request=_request()),
        request=_request(),
    )


def test_outer_result_size_is_exact_and_never_truncated() -> None:
    service, _assistant, _simulate, cancellation = _service()
    full = _execute(service, cancellation=cancellation)
    assert isinstance(full, CompareResultV1)
    encoded = serialize_compare_result(full, request=_request())
    exact_request = _request(max_result_bytes=len(encoded))
    exact_service, _a, _s, exact_cancellation = _service()
    exact = _execute(exact_service, exact_request, cancellation=exact_cancellation)
    assert isinstance(exact, CompareResultV1)

    small_service, _a2, _s2, small_cancellation = _service()
    too_small = _execute(
        small_service,
        _request(max_result_bytes=len(encoded) - 1),
        cancellation=small_cancellation,
    )
    assert _top_error(too_small).code is CompareErrorCodeV1.RESULT_TOO_LARGE


def test_validate_compare_request_returns_normalized_copy() -> None:
    normalized = validate_compare_request(
        CompareRequestV1(
            task="  Cafe\u0301 ",
            options=(CompareOptionV1("a", "  label  "),),
            assistant=CompareAssistantInputsV1(
                explicit_context=(AssistantExplicitContext("background", "  context  "),)
            ),
        )
    )

    assert normalized.task == "Café"
    assert normalized.options == (CompareOptionV1("a", "label"),)
    assert normalized.assistant.explicit_context == (
        AssistantExplicitContext("background", "context"),
    )


def test_assistant_error_is_safe_even_when_upstream_text_contains_private_data() -> None:
    secret = "provider=https://secret.example/api token=do-not-leak"

    class LeakingAssistant(_FakeAssistant):
        def advise(
            self,
            request: AssistantReasoningEnvelopeV1,
            *,
            execution: CompareExecutionContextV1,
        ) -> AssistantResultEnvelopeV1:
            raise AssistantError(secret)

    assistant = LeakingAssistant(_assistant_result())
    simulate_me = _FakeSimulateMe(_simulate_result(selected=None))
    cancellation = CancellationTokenSource()
    service = BuildCompare(assistant, simulate_me, clock=lambda: 0.0)

    result = _execute(service, cancellation=cancellation)

    assert isinstance(result, CompareResultV1)
    assert isinstance(result.assistant, CompareAssistantErrorBranchV1)
    assert result.assistant.error is not None
    assert secret not in result.assistant.error.message
