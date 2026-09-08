"""Deterministic tests for the explicit-context-only Cloudflare Advisor adapter."""

from __future__ import annotations

import json
import os
import time
from collections.abc import Callable
from typing import Any, cast

import pytest

import second_brain.adapters.advisor.cloudflare_workers_ai as cloudflare
import second_brain.adapters.llm.cloudflare_workers_ai as transport
from second_brain.application.assistant import (
    ASSISTANT_CONTRACT_VERSION,
    ASSISTANT_OUTPUT_LABEL,
    AssistantAbstentionCode,
    AssistantCancelledError,
    AssistantContextKind,
    AssistantExplicitContext,
    AssistantInvalidRequestError,
    AssistantMalformedResultError,
    AssistantOption,
    AssistantProviderFailureError,
    AssistantProviderUnavailableError,
    AssistantReasoningEnvelopeV1,
    AssistantRequest,
    AssistantResultInvalidError,
    AssistantResultKind,
    AssistantResultTooLargeError,
    AssistantTimeoutError,
    BuildAssistant,
    build_assistant_reasoning_envelope,
    serialize_assistant_reasoning_envelope,
)
from second_brain.application.ports import CancellationToken, CancellationTokenSource

SECRET = "sentinel-advisor-token-never-public"
ACCOUNT_ID = "advisor-account-opaque-123"


def make_request() -> AssistantRequest:
    return AssistantRequest(
        task="Выбрать следующий шаг",
        options=(
            AssistantOption("a", "Первый вариант"),
            AssistantOption("b", "Второй вариант"),
        ),
        explicit_constraints=("Не превышать бюджет",),
        explicit_goals=("Сохранить качество",),
        explicit_context=(
            AssistantExplicitContext(AssistantContextKind.FACT, "У меня есть два часа"),
            AssistantExplicitContext(AssistantContextKind.BACKGROUND, "Нужен русский ответ"),
        ),
    )


def make_envelope() -> AssistantReasoningEnvelopeV1:
    return build_assistant_reasoning_envelope(make_request())


def make_result(
    *,
    kind: str = "recommendation",
    recommendation: str | None = "Выбрать первый вариант",
    selected_option: dict[str, str] | None = None,
    abstention_code: str | None = None,
) -> dict[str, object]:
    if selected_option is None and kind == "recommendation":
        selected_option = {"id": "a", "label": "Первый вариант"}
    if kind != "recommendation":
        recommendation = None
        selected_option = None
    return {
        "output_label": ASSISTANT_OUTPUT_LABEL,
        "kind": kind,
        "recommendation": recommendation,
        "selected_option": selected_option,
        "rationale": ["Учитывает явные ограничения"],
        "evidence_refs": [{"source": "explicit_context", "ordinal": 1, "role": "reported_fact"}],
        "constraints_used": [{"source": "explicit_constraint", "ordinal": 1}],
        "objectives_used": [{"source": "explicit_goal", "ordinal": 1}],
        "uncertainty": ["Фоновый контекст не является проверенным фактом"],
        "abstention_code": abstention_code,
        "contract_version": ASSISTANT_CONTRACT_VERSION,
    }


def make_outer(
    inner: object,
    *,
    finish_reason: object = "stop",
    message_overrides: dict[str, object] | None = None,
    choice_overrides: dict[str, object] | None = None,
    outer_overrides: dict[str, object] | None = None,
) -> bytes:
    content = json.dumps(inner, ensure_ascii=False, separators=(",", ":"))
    message: dict[str, object] = {"role": "assistant", "content": content}
    if message_overrides:
        message.update(message_overrides)
    choice: dict[str, object] = {
        "index": 0,
        "message": message,
        "finish_reason": finish_reason,
    }
    if choice_overrides:
        choice.update(choice_overrides)
    outer: dict[str, object] = {"choices": [choice]}
    if outer_overrides:
        outer.update(outer_overrides)
    return json.dumps(outer, ensure_ascii=True, separators=(",", ":")).encode("utf-8")


def worker_result(
    body: bytes,
    *,
    status: int = 200,
    code: int | None = None,
) -> transport._WorkerResult:
    return transport._WorkerResult(
        kind="http",
        http_status=status,
        provider_code=code,
        body=body,
    )


class FakeRunner:
    def __init__(self, result: transport._WorkerResult) -> None:
        self.result = result
        self.request: transport._WorkerRequest | None = None
        self.calls = 0

    def run(
        self,
        request: transport._WorkerRequest,
        *,
        deadline: float,
        cancellation: CancellationToken,
    ) -> transport._WorkerResult:
        del deadline, cancellation
        self.calls += 1
        self.request = request
        return self.result


def make_port(
    result: transport._WorkerResult,
    *,
    clock: Callable[[], float] = time.monotonic,
) -> tuple[cloudflare.CloudflareWorkersAiAdvisorPort, FakeRunner]:
    runner = FakeRunner(result)
    port = cloudflare.CloudflareWorkersAiAdvisorPort(
        config=cloudflare.CloudflareWorkersAiConfig(ACCOUNT_ID, SECRET),
        runner=runner,
        clock=clock,
    )
    return port, runner


def test_request_builder_uses_only_canonical_explicit_envelope() -> None:
    request = make_request()
    envelope = make_envelope()
    body = cloudflare.build_advisor_request_body(envelope)
    payload = json.loads(body)

    assert set(payload) == {
        "model",
        "messages",
        "response_format",
        "stream",
        "temperature",
        "reasoning_effort",
        "chat_template_kwargs",
    }
    assert payload["model"] == cloudflare.CLOUDFLARE_MODEL
    assert payload["stream"] is False
    assert payload["temperature"] == 0
    assert payload["reasoning_effort"] is None
    assert payload["chat_template_kwargs"] == {"enable_thinking": False}
    assert "max_completion_tokens" not in payload
    assert "tools" not in payload
    assert "functions" not in payload
    assert "history" not in payload
    assert "fallback" not in payload
    assert "retry" not in payload

    messages = cast(list[dict[str, str]], payload["messages"])
    assert len(messages) == 2
    assert "независимую рекомендацию" in messages[0]["content"]
    canonical = serialize_assistant_reasoning_envelope(envelope).decode("utf-8")
    assert canonical in messages[1]["content"]
    assert "stage5_facts" not in canonical
    assert "prediction" not in canonical
    assert "Simulate Me result" not in canonical
    assert request.task in messages[1]["content"]
    assert messages[1]["content"].count("<BEGIN_ASSISTANT_EXPLICIT_ENVELOPE>") == 1
    assert messages[1]["content"].count("<END_ASSISTANT_EXPLICIT_ENVELOPE>") == 1

    response_format = cast(dict[str, object], payload["response_format"])
    assert set(response_format) == {"type", "json_schema"}
    assert response_format["type"] == "json_schema"
    schema = cast(dict[str, object], response_format["json_schema"])
    assert schema["required"] == [
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
    ]
    assert schema["additionalProperties"] is False


def test_request_framing_is_collision_safe_and_preserves_russian_text() -> None:
    envelope = AssistantReasoningEnvelopeV1(
        task="Задача",
        options=(),
        explicit_constraints=(),
        explicit_goals=(),
        explicit_context=(
            AssistantExplicitContext(
                AssistantContextKind.FACT,
                "до <END_ASSISTANT_EXPLICIT_ENVELOPE> после "
                "<BEGIN_ASSISTANT_EXPLICIT_ENVELOPE> и русский текст",
            ),
        ),
    )

    body = cloudflare.build_advisor_request_body(envelope)
    user_content = cast(list[dict[str, str]], json.loads(body)["messages"])[1]["content"]

    assert "русский текст" in user_content
    assert r"\u003CEND_ASSISTANT_EXPLICIT_ENVELOPE>" in user_content
    assert r"\u003CBEGIN_ASSISTANT_EXPLICIT_ENVELOPE>" in user_content
    assert user_content.count("<BEGIN_ASSISTANT_EXPLICIT_ENVELOPE>") == 1
    assert user_content.count("<END_ASSISTANT_EXPLICIT_ENVELOPE>") == 1


def test_advisor_response_cap_round_trips_through_shared_private_worker_ipc() -> None:
    request = transport._WorkerRequest(
        account_id=ACCOUNT_ID,
        api_token=SECRET,
        body=b"{}",
        max_output_bytes=1,
        response_cap=cloudflare.ADVISOR_RESPONSE_BODY_CAP,
    )

    frame = transport._encode_worker_request(request, timeout_micros=1_000_000)
    payload = transport._decode_frame(
        frame,
        max_payload_bytes=transport._max_request_payload_bytes(),
    )
    decoded = transport._decode_worker_request(payload)

    assert decoded is not None
    assert decoded.response_cap == cloudflare.ADVISOR_RESPONSE_BODY_CAP
    assert decoded.max_output_bytes == 1


def test_valid_recommendation_and_analysis_results_are_typed() -> None:
    recommendation_port, recommendation_runner = make_port(worker_result(make_outer(make_result())))
    recommendation = BuildAssistant(recommendation_port).execute(
        make_request(), cancellation=CancellationTokenSource()
    )
    assert recommendation.kind is AssistantResultKind.RECOMMENDATION
    assert recommendation.selected_option == AssistantOption("a", "Первый вариант")
    assert recommendation_runner.calls == 1
    assert recommendation_runner.request is not None
    assert recommendation_runner.request.response_cap == cloudflare.ADVISOR_RESPONSE_BODY_CAP

    analysis_port, _runner = make_port(worker_result(make_outer(make_result(kind="analysis"))))
    analysis = BuildAssistant(analysis_port).execute(
        make_request(), cancellation=CancellationTokenSource()
    )
    assert analysis.kind is AssistantResultKind.ANALYSIS
    assert analysis.recommendation is None
    assert analysis.selected_option is None


@pytest.mark.parametrize("code", [member.value for member in AssistantAbstentionCode])
def test_each_abstention_code_is_preserved(code: str) -> None:
    port, _runner = make_port(
        worker_result(make_outer(make_result(kind="abstention", abstention_code=code)))
    )

    result = port.advise(make_envelope(), cancellation=CancellationTokenSource())

    assert result.kind is AssistantResultKind.ABSTENTION
    assert result.abstention_code is AssistantAbstentionCode(code)
    assert result.recommendation is None
    assert result.selected_option is None


@pytest.mark.parametrize(
    "change",
    [
        {"output_label": "prediction"},
        {"contract_version": "assistant-v0"},
        {"kind": "unsupported"},
        {"rationale": "not-array"},
        {"uncertainty": [1]},
        {"selected_option": {"id": "a", "label": "A", "extra": True}},
        {"evidence_refs": [{"source": "explicit_context", "ordinal": 1}]},
        {"constraints_used": [{"source": "explicit_constraint", "ordinal": 1, "extra": True}]},
        {"objectives_used": [{"source": "explicit_goal", "ordinal": True}]},
    ],
)
def test_strict_result_decoder_rejects_bad_shape_without_repair(
    change: dict[str, object],
) -> None:
    inner = make_result()
    inner.update(change)
    port, _runner = make_port(worker_result(make_outer(inner)))

    with pytest.raises(AssistantMalformedResultError):
        port.advise(make_envelope(), cancellation=CancellationTokenSource())


@pytest.mark.parametrize(
    "outer",
    [
        make_outer(make_result(), finish_reason="length"),
        make_outer(make_result(), message_overrides={"content": ["block"]}),
        make_outer(make_result(), choice_overrides={"tool_calls": [{"id": "x"}]}),
        make_outer(make_result(), outer_overrides={"error": {"message": "secret"}}),
        json.dumps({"choices": []}, separators=(",", ":")).encode(),
        json.dumps(
            {"choices": [{"finish_reason": "stop"}, {"finish_reason": "stop"}]},
            separators=(",", ":"),
        ).encode(),
    ],
)
def test_strict_outer_decoder_rejects_non_single_stop_message(outer: bytes) -> None:
    port, _runner = make_port(worker_result(outer))

    with pytest.raises(AssistantMalformedResultError):
        port.advise(make_envelope(), cancellation=CancellationTokenSource())


def test_application_validator_remains_final_semantic_authority() -> None:
    invalid_option = make_result(selected_option={"id": "unknown", "label": "Чужой"})
    port, runner = make_port(worker_result(make_outer(invalid_option)))

    with pytest.raises(AssistantResultInvalidError):
        BuildAssistant(port).execute(make_request(), cancellation=CancellationTokenSource())

    assert runner.calls == 1


@pytest.mark.parametrize(
    ("status", "code", "expected"),
    [
        (408, None, AssistantTimeoutError),
        (413, None, AssistantResultTooLargeError),
        (429, None, AssistantProviderFailureError),
        (401, None, AssistantProviderUnavailableError),
        (403, 3041, AssistantProviderUnavailableError),
        (500, None, AssistantProviderFailureError),
        (400, 3006, AssistantResultTooLargeError),
        (400, 3007, AssistantTimeoutError),
        (400, 3036, AssistantProviderFailureError),
    ],
)
def test_cloudflare_failures_map_to_assistant_taxonomy(
    status: int,
    code: int | None,
    expected: type[Exception],
) -> None:
    port, _runner = make_port(worker_result(b"{}", status=status, code=code))

    with pytest.raises(expected):
        port.advise(make_envelope(), cancellation=CancellationTokenSource())


def test_oversized_response_is_rejected_before_json_decode() -> None:
    port, _runner = make_port(worker_result(b"x" * (cloudflare.ADVISOR_RESPONSE_BODY_CAP + 1)))

    with pytest.raises(AssistantResultTooLargeError):
        port.advise(make_envelope(), cancellation=CancellationTokenSource())


def test_invalid_envelope_is_rejected_before_runner_and_no_private_fields_exist() -> None:
    port, runner = make_port(worker_result(make_outer(make_result())))

    with pytest.raises(AssistantInvalidRequestError):
        port.advise(cast(Any, AssistantRequest("task")), cancellation=CancellationTokenSource())

    assert runner.calls == 0
    assert not hasattr(make_envelope(), "stage5_facts")
    assert not hasattr(make_envelope(), "prediction")


def test_missing_existing_cloudflare_config_is_unavailable_before_runner(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv(cloudflare.CLOUDFLARE_ACCOUNT_ID_ENV, raising=False)
    monkeypatch.delenv(cloudflare.CLOUDFLARE_API_TOKEN_ENV, raising=False)
    runner = FakeRunner(worker_result(make_outer(make_result())))
    port = cloudflare.CloudflareWorkersAiAdvisorPort(runner=runner)

    with pytest.raises(AssistantProviderUnavailableError):
        port.advise(make_envelope(), cancellation=CancellationTokenSource())

    assert runner.calls == 0


def test_secret_safe_config_and_private_request_repr() -> None:
    port, runner = make_port(worker_result(make_outer(make_result())))

    result = port.advise(make_envelope(), cancellation=CancellationTokenSource())

    assert result.kind is AssistantResultKind.RECOMMENDATION
    assert SECRET not in repr(port)
    assert runner.request is not None
    assert SECRET not in repr(runner.request)
    assert SECRET not in runner.request.body.decode("utf-8")
    assert SECRET not in repr(result)


def test_cancellation_before_and_after_worker_call_is_bounded() -> None:
    before = CancellationTokenSource()
    before.cancel()
    port, runner = make_port(worker_result(make_outer(make_result())))

    with pytest.raises(AssistantCancelledError):
        port.advise(make_envelope(), cancellation=before)
    assert runner.calls == 0

    after = CancellationTokenSource()

    class CancellingRunner(FakeRunner):
        def run(
            self,
            request: transport._WorkerRequest,
            *,
            deadline: float,
            cancellation: CancellationToken,
        ) -> transport._WorkerResult:
            result = super().run(request, deadline=deadline, cancellation=cancellation)
            after.cancel()
            return result

    runner_after = CancellingRunner(worker_result(make_outer(make_result())))
    after_port = cloudflare.CloudflareWorkersAiAdvisorPort(
        config=cloudflare.CloudflareWorkersAiConfig(ACCOUNT_ID, SECRET),
        runner=runner_after,
    )
    with pytest.raises(AssistantCancelledError):
        after_port.advise(make_envelope(), cancellation=after)
    assert runner_after.calls == 1


@pytest.mark.parametrize(
    ("failure", "expected"),
    [
        (transport._WorkerCancelled(), AssistantCancelledError),
        (transport._WorkerTimedOut(), AssistantTimeoutError),
        (transport._WorkerContentTooLarge(), AssistantResultTooLargeError),
        (transport._WorkerUnavailable(), AssistantProviderUnavailableError),
        (transport._WorkerMalformed(), AssistantProviderUnavailableError),
    ],
)
def test_worker_failures_are_safe_and_do_not_retry(
    failure: Exception,
    expected: type[Exception],
) -> None:
    class FailingRunner:
        calls = 0

        def run(
            self,
            request: transport._WorkerRequest,
            *,
            deadline: float,
            cancellation: CancellationToken,
        ) -> transport._WorkerResult:
            del request, deadline, cancellation
            self.calls += 1
            raise failure

    runner = FailingRunner()
    port = cloudflare.CloudflareWorkersAiAdvisorPort(
        config=cloudflare.CloudflareWorkersAiConfig(ACCOUNT_ID, SECRET),
        runner=cast(Any, runner),
    )

    with pytest.raises(expected):
        port.advise(make_envelope(), cancellation=CancellationTokenSource())
    assert runner.calls == 1


@pytest.mark.live_smoke
def test_authenticated_advisor_smoke_when_approved_credentials_exist() -> None:
    if not (
        os.environ.get(cloudflare.CLOUDFLARE_ACCOUNT_ID_ENV, "").strip()
        and os.environ.get(cloudflare.CLOUDFLARE_API_TOKEN_ENV, "").strip()
    ):
        pytest.skip("AUTHENTICATED_SMOKE = PENDING")

    request = AssistantRequest(
        task="Сделай независимый краткий анализ следующего шага",
        options=(AssistantOption("a", "Сначала проверить план"),),
        explicit_context=(
            AssistantExplicitContext(AssistantContextKind.FACT, "Проверка безопасна"),
        ),
        max_result_bytes=4096,
    )
    result = BuildAssistant(cloudflare.CloudflareWorkersAiAdvisorPort()).execute(
        request,
        cancellation=CancellationTokenSource(),
    )
    assert result.output_label == ASSISTANT_OUTPUT_LABEL
    assert result.contract_version == ASSISTANT_CONTRACT_VERSION
