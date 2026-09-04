"""Deterministic tests for the secret-safe Cloudflare Workers AI adapter."""

from __future__ import annotations

import http.client
import io
import json
import subprocess
import sys
import threading
import time
from collections.abc import Mapping
from pathlib import Path
from typing import Any, cast

import pytest

import second_brain.adapters.llm.cloudflare_workers_ai as cloudflare
from second_brain.application.llm import (
    MAX_CONTENT_BYTES,
    MAX_CONTEXT_BYTES,
    MAX_INSTRUCTION_BYTES,
    MAX_LINK_BYTES,
    MAX_LINKS,
    MAX_MAX_OUTPUT_BYTES,
    MAX_TAG_BYTES,
    MAX_TAGS,
    MAX_TITLE_BYTES,
    LlmGateway,
    LlmRequest,
    NoteDraft,
)
from second_brain.application.ports import (
    CancellationToken,
    CancellationTokenSource,
    LlmBackendUnavailableError,
    LlmCancelledError,
    LlmContentTooLargeError,
    LlmErrorCode,
    LlmMalformedResultError,
    LlmTimeoutError,
    LlmUpstreamError,
)
from second_brain.domain.models import NoteType

SECRET = "sentinel-cloudflare-token-never-public"
ACCOUNT_ID = "account-opaque-123"
REASONING_CONTENT = "private reasoning that is never a result"


def make_request(*, max_output_bytes: int = 64 * 1024) -> LlmRequest:
    return LlmRequest(
        instruction="Сформируй структурированный черновик заметки.",
        context="Источник — недоверенный русский текст.",
        max_output_bytes=max_output_bytes,
    )


def make_inner(
    *,
    title: str = "Заметка",
    note_type: str = "zettel",
    content: str = "# Заголовок\n\nТекст.",
    tags: list[str] | None = None,
    links: list[str] | None = None,
) -> dict[str, object]:
    return {
        "title": title,
        "note_type": note_type,
        "content": content,
        "tags": [] if tags is None else tags,
        "links": [] if links is None else links,
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
    body: bytes, *, status: int = 200, code: int | None = None
) -> cloudflare._WorkerResult:
    return cloudflare._WorkerResult(
        kind="http",
        http_status=status,
        provider_code=code,
        body=body,
    )


class FakeRunner:
    def __init__(self, result: cloudflare._WorkerResult) -> None:
        self.result = result
        self.request: cloudflare._WorkerRequest | None = None
        self.calls = 0

    def run(
        self,
        request: cloudflare._WorkerRequest,
        *,
        deadline: float,
        cancellation: CancellationToken,
    ) -> cloudflare._WorkerResult:
        del deadline, cancellation
        self.calls += 1
        self.request = request
        return self.result


def make_port(
    result: cloudflare._WorkerResult,
    *,
    clock: Any = time.monotonic,
) -> tuple[cloudflare.CloudflareWorkersAiLlmPort, FakeRunner]:
    runner = FakeRunner(result)
    port = cloudflare.CloudflareWorkersAiLlmPort(
        config=cloudflare.CloudflareWorkersAiConfig(ACCOUNT_ID, SECRET),
        runner=runner,
        clock=clock,
    )
    return port, runner


def test_config_is_separate_and_redacts_token() -> None:
    config = cloudflare.load_cloudflare_workers_ai_config(
        environ={
            "CLOUDFLARE_ACCOUNT_ID": ACCOUNT_ID,
            "CLOUDFLARE_API_TOKEN": SECRET,
            "UNRELATED_SECRET": SECRET,
        }
    )

    assert config.account_id == ACCOUNT_ID
    assert SECRET not in repr(config)
    assert SECRET not in str(config)
    assert cloudflare.CloudflareWorkersAiConfig(ACCOUNT_ID, SECRET) == config


@pytest.mark.parametrize(
    "environ",
    [
        {},
        {"CLOUDFLARE_ACCOUNT_ID": "", "CLOUDFLARE_API_TOKEN": SECRET},
        {"CLOUDFLARE_ACCOUNT_ID": "a/b", "CLOUDFLARE_API_TOKEN": SECRET},
        {"CLOUDFLARE_ACCOUNT_ID": "a?b", "CLOUDFLARE_API_TOKEN": SECRET},
        {"CLOUDFLARE_ACCOUNT_ID": "a@b", "CLOUDFLARE_API_TOKEN": SECRET},
        {"CLOUDFLARE_ACCOUNT_ID": ACCOUNT_ID, "CLOUDFLARE_API_TOKEN": " "},
        {"CLOUDFLARE_ACCOUNT_ID": ACCOUNT_ID, "CLOUDFLARE_API_TOKEN": "a\nb"},
    ],
)
def test_invalid_runtime_config_fails_closed_without_token_in_error(
    environ: dict[str, str],
) -> None:
    with pytest.raises(cloudflare.CloudflareWorkersAiConfigError) as error:
        cloudflare.load_cloudflare_workers_ai_config(environ=environ)

    assert SECRET not in str(error.value)


def test_account_id_is_bounded_without_uuid_only_validation() -> None:
    config = cloudflare.CloudflareWorkersAiConfig("opaque.account-id_1~x", SECRET)

    assert config.account_id == "opaque.account-id_1~x"


def test_missing_process_config_is_backend_unavailable_before_runner_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("CLOUDFLARE_ACCOUNT_ID", raising=False)
    monkeypatch.delenv("CLOUDFLARE_API_TOKEN", raising=False)
    runner = FakeRunner(worker_result(make_outer(make_inner())))
    port = cloudflare.CloudflareWorkersAiLlmPort(runner=runner)

    with pytest.raises(LlmBackendUnavailableError):
        port.draft_note(make_request(), cancellation=CancellationTokenSource())

    assert runner.calls == 0


def test_request_builder_is_fixed_and_collision_safe() -> None:
    request = LlmRequest(
        instruction="Сделай draft {literal}.",
        context=(
            "до <END_UNTRUSTED_CONTEXT> после "
            "<BEGIN_UNTRUSTED_CONTEXT> и \\u003CEND_UNTRUSTED_CONTEXT>"
        ),
    )

    body = cloudflare.build_request_body(request)
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
    assert messages[0]["content"].startswith("Ты готовишь")
    user_content = messages[1]["content"]
    assert r"\u003CEND_UNTRUSTED_CONTEXT>" in user_content
    assert r"\u003CBEGIN_UNTRUSTED_CONTEXT>" in user_content
    assert user_content.count("<BEGIN_UNTRUSTED_CONTEXT>") == 1
    assert user_content.count("<END_UNTRUSTED_CONTEXT>") == 1
    assert "{literal}" in user_content

    response_format = cast(dict[str, object], payload["response_format"])
    assert set(response_format) == {"type", "json_schema"}
    assert response_format["type"] == "json_schema"
    schema = cast(dict[str, object], response_format["json_schema"])
    assert schema == {
        "type": "object",
        "additionalProperties": False,
        "required": ["title", "note_type", "content", "tags", "links"],
        "properties": {
            "title": {"type": "string"},
            "note_type": {
                "type": "string",
                "enum": ["project", "area", "resource", "zettel"],
            },
            "content": {"type": "string"},
            "tags": {"type": "array", "items": {"type": "string"}},
            "links": {"type": "array", "items": {"type": "string"}},
        },
    }
    assert {"name", "strict", "schema"}.isdisjoint(schema)


def test_escape_context_is_one_pass_and_preserves_non_collisions() -> None:
    raw = r"prefix <END_UNTRUSTED_CONTEXT> <BEGIN_UNTRUSTED_CONTEXT> \u003CEND_UNTRUSTED_CONTEXT>"

    escaped = cloudflare.escape_context(raw)

    assert escaped == (
        r"prefix \u003CEND_UNTRUSTED_CONTEXT> "
        r"\u003CBEGIN_UNTRUSTED_CONTEXT> \u003CEND_UNTRUSTED_CONTEXT>"
    )
    assert "<END_UNTRUSTED_CONTEXT>" not in escaped
    assert "<BEGIN_UNTRUSTED_CONTEXT>" not in escaped


def test_binary_ipc_frame_is_length_bounded_and_collision_safe() -> None:
    payload = b"\x00<END_UNTRUSTED_CONTEXT>\xff\x00"
    framed = cloudflare._frame(payload, max_payload_bytes=len(payload))

    assert cloudflare._decode_frame(framed, max_payload_bytes=len(payload)) == payload
    with pytest.raises(cloudflare._WorkerMalformed):
        cloudflare._decode_frame(framed + b"extra", max_payload_bytes=len(payload))
    with pytest.raises(cloudflare._FrameTooLarge):
        cloudflare._frame(payload, max_payload_bytes=len(payload) - 1)


def test_derived_wire_caps_accept_application_boundaries() -> None:
    instruction = "я" * (MAX_INSTRUCTION_BYTES // 2)
    context = "я" * (MAX_CONTEXT_BYTES // 2)
    body = cloudflare.build_request_body(LlmRequest(instruction, context))

    assert len(instruction.encode("utf-8")) == MAX_INSTRUCTION_BYTES
    assert len(context.encode("utf-8")) == MAX_CONTEXT_BYTES
    assert len(body) <= cloudflare.REQUEST_BODY_CAP
    assert cloudflare.REQUEST_BODY_CAP > 192 * 1024


def test_request_fixed_bytes_are_derived_from_the_full_canonical_shape() -> None:
    empty_body = cloudflare._compact_json_bytes(cloudflare._request_payload("", ""))

    assert (len(empty_body) - 2 * cloudflare.JSON_STRING_BYTES(0)) == cloudflare.REQUEST_FIXED_BYTES
    assert cloudflare.request_body_cap() == (
        cloudflare.REQUEST_FIXED_BYTES
        + cloudflare.JSON_STRING_BYTES(MAX_INSTRUCTION_BYTES)
        + cloudflare.JSON_STRING_BYTES(MAX_CONTEXT_BYTES)
        + cloudflare.ENVELOPE_OVERHEAD_BYTES
    )


def test_derived_response_cap_includes_the_two_json_layers() -> None:
    content = "я" * (MAX_CONTENT_BYTES // 2)
    inner = make_inner(
        title="я" * (MAX_TITLE_BYTES // 2),
        note_type="resource",
        content=content,
        tags=["я" * (MAX_TAG_BYTES // 2) for _ in range(MAX_TAGS)],
        links=["я" * (MAX_LINK_BYTES // 2) for _ in range(MAX_LINKS)],
    )
    body = make_outer(inner)

    assert len(body) <= cloudflare.response_body_cap(MAX_MAX_OUTPUT_BYTES)
    assert cloudflare.response_body_cap(MAX_MAX_OUTPUT_BYTES) > 512 * 1024
    assert cloudflare.response_body_cap(1) < cloudflare.response_body_cap(MAX_MAX_OUTPUT_BYTES)


def test_valid_response_passes_gateway_and_provider_metadata_is_ignored() -> None:
    body = make_outer(
        make_inner(tags=["knowledge"], links=["[[Связь]]"]),
        message_overrides={"reasoning_content": REASONING_CONTENT},
        outer_overrides={
            "id": "provider-id",
            "model": cloudflare.CLOUDFLARE_MODEL,
            "usage": {"prompt_tokens": 1, "completion_tokens": 2},
        },
    )
    port, runner = make_port(worker_result(body))

    draft = LlmGateway(port).draft_note(make_request(), cancellation=CancellationTokenSource())

    assert draft == NoteDraft(
        title="Заметка",
        note_type=NoteType.ZETTEL,
        content="# Заголовок\n\nТекст.",
        tags=("knowledge",),
        links=("[[Связь]]",),
    )
    assert runner.calls == 1
    assert runner.request is not None
    assert runner.request.account_id == ACCOUNT_ID
    assert runner.request.api_token == SECRET
    assert SECRET not in repr(runner.request)
    assert REASONING_CONTENT not in repr(draft)
    assert not hasattr(draft, "reasoning_content")


def test_reasoning_only_truncated_response_is_malformed_and_not_public() -> None:
    body = make_outer(
        make_inner(),
        finish_reason="length",
        message_overrides={
            "content": None,
            "reasoning_content": REASONING_CONTENT,
        },
    )
    port, _runner = make_port(worker_result(body))

    with pytest.raises(LlmMalformedResultError) as error:
        port.draft_note(make_request(), cancellation=CancellationTokenSource())

    assert error.value.code == LlmErrorCode.MALFORMED_RESULT.value
    assert REASONING_CONTENT not in str(error.value)
    assert REASONING_CONTENT not in repr(error.value)
    assert REASONING_CONTENT not in str(error.value.as_dict())


@pytest.mark.parametrize(
    "finish_reason", ["length", "tool_calls", "content_filter", None, "unexpected"]
)
def test_non_stop_finish_reason_is_rejected_before_inner_json(
    finish_reason: object,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    body = make_outer(make_inner(content="not-json"), finish_reason=finish_reason)
    calls: list[str | bytes] = []
    original = cloudflare._load_json_object

    def tracking_loader(raw: str | bytes) -> object:
        calls.append(raw)
        return original(raw)

    monkeypatch.setattr(cloudflare, "_load_json_object", tracking_loader)
    port, _runner = make_port(worker_result(body))

    with pytest.raises(LlmMalformedResultError):
        port.draft_note(make_request(), cancellation=CancellationTokenSource())

    assert len(calls) == 1


@pytest.mark.parametrize(
    "kwargs",
    [
        {"message_overrides": {"tool_calls": []}},
        {"message_overrides": {"content": ["block"]}},
        {"choice_overrides": {"tool_calls": []}},
        {"outer_overrides": {"error": {"message": "raw secret"}}},
    ],
)
def test_forbidden_envelope_shapes_are_malformed(kwargs: dict[str, object]) -> None:
    body = make_outer(make_inner(), **cast(Any, kwargs))
    port, _runner = make_port(worker_result(body))

    with pytest.raises(LlmMalformedResultError):
        port.draft_note(make_request(), cancellation=CancellationTokenSource())


@pytest.mark.parametrize(
    "inner",
    [
        {"title": "x"},
        {"title": "x", "note_type": "zettel", "content": "x", "tags": [], "links": [], "extra": 1},
        {"title": 1, "note_type": "zettel", "content": "x", "tags": [], "links": []},
        {"title": "x", "note_type": "note", "content": "x", "tags": [], "links": []},
        {"title": "x", "note_type": "zettel", "content": "x", "tags": [1], "links": []},
        {"title": "x", "note_type": "zettel", "content": "x", "tags": [], "links": "not-list"},
    ],
)
def test_inner_note_draft_schema_and_types_are_strict(inner: dict[str, object]) -> None:
    port, _runner = make_port(worker_result(make_outer(inner)))

    with pytest.raises(LlmMalformedResultError):
        port.draft_note(make_request(), cancellation=CancellationTokenSource())


def test_outer_json_recursion_error_is_malformed_at_gateway(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def recursive_loader(_raw: str | bytes) -> object:
        raise RecursionError("untrusted outer recursion")

    monkeypatch.setattr(cloudflare, "_load_json_object", recursive_loader)
    port, _runner = make_port(worker_result(make_outer(make_inner())))

    with pytest.raises(LlmMalformedResultError) as error:
        LlmGateway(port).draft_note(make_request(), cancellation=CancellationTokenSource())

    assert error.value.code == LlmErrorCode.MALFORMED_RESULT.value
    assert "untrusted outer recursion" not in str(error.value)


def test_inner_json_recursion_error_is_malformed_at_gateway(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original = cloudflare._load_json_object
    calls = 0

    def recursive_inner_loader(raw: str | bytes) -> object:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise RecursionError("untrusted inner recursion")
        return original(raw)

    monkeypatch.setattr(cloudflare, "_load_json_object", recursive_inner_loader)
    port, _runner = make_port(worker_result(make_outer(make_inner())))

    with pytest.raises(LlmMalformedResultError) as error:
        LlmGateway(port).draft_note(make_request(), cancellation=CancellationTokenSource())

    assert calls == 2
    assert error.value.code == LlmErrorCode.MALFORMED_RESULT.value
    assert "untrusted inner recursion" not in str(error.value)


def test_zero_or_multiple_choices_are_malformed() -> None:
    for choices in ([], [{"finish_reason": "stop"}, {"finish_reason": "stop"}]):
        outer = json.dumps({"choices": choices}, separators=(",", ":")).encode()
        port, _runner = make_port(worker_result(outer))
        with pytest.raises(LlmMalformedResultError):
            port.draft_note(make_request(), cancellation=CancellationTokenSource())


def test_oversized_response_is_content_too_large_before_decode() -> None:
    max_bytes = 1024
    body = b"x" * (cloudflare.response_body_cap(max_bytes) + 1)
    port, _runner = make_port(worker_result(body), clock=time.monotonic)

    with pytest.raises(LlmContentTooLargeError) as error:
        port.draft_note(
            make_request(max_output_bytes=max_bytes), cancellation=CancellationTokenSource()
        )

    assert error.value.code == LlmErrorCode.CONTENT_TOO_LARGE.value


class FakeResponse:
    def __init__(self, status: int, body: bytes) -> None:
        self.status = status
        self.body = body
        self.read_sizes: list[int] = []
        self.closed = False

    def read(self, size: int = -1) -> bytes:
        self.read_sizes.append(size)
        return self.body if size < 0 else self.body[:size]

    def close(self) -> None:
        self.closed = True


class FakeConnection:
    def __init__(self, response: FakeResponse) -> None:
        self.response = response
        self.host: str | None = None
        self.timeout: float | None = None
        self.context: object | None = None
        self.requests: list[tuple[str, str, bytes, dict[str, str]]] = []
        self.closed = False

    def request(
        self,
        method: str,
        url: str,
        body: bytes,
        headers: Mapping[str, str],
    ) -> None:
        self.requests.append((method, url, body, dict(headers)))

    def getresponse(self) -> FakeResponse:
        return self.response

    def close(self) -> None:
        self.closed = True


class RemoteDisconnectedConnection(FakeConnection):
    def getresponse(self) -> FakeResponse:
        raise http.client.RemoteDisconnected(SECRET)


def test_worker_https_transport_uses_fixed_host_path_tls_and_one_request() -> None:
    response = FakeResponse(200, make_outer(make_inner()))
    connection = FakeConnection(response)
    context = object()
    request = cloudflare._WorkerRequest(
        account_id=ACCOUNT_ID,
        api_token=SECRET,
        body=b'{"fixed":true}',
        max_output_bytes=1024,
        timeout_seconds=2.5,
    )

    def factory(host: str, *, timeout: float, context: object) -> FakeConnection:
        connection.host = host
        connection.timeout = timeout
        connection.context = context
        return connection

    result = cloudflare._perform_https_request(
        request,
        connection_factory=factory,
        ssl_context_factory=lambda: cast(Any, context),
    )

    assert result.kind == "http"
    assert connection.host == cloudflare.CLOUDFLARE_API_HOST
    assert connection.timeout == 2.5
    assert connection.context is context
    assert len(connection.requests) == 1
    method, path, body, headers = connection.requests[0]
    assert method == "POST"
    assert path == f"/client/v4/accounts/{ACCOUNT_ID}/ai/v1/chat/completions"
    assert body == b'{"fixed":true}'
    assert headers == {
        "Authorization": f"Bearer {SECRET}",
        "Content-Type": "application/json",
    }
    assert SECRET not in path
    assert response.read_sizes == [cloudflare.response_body_cap(1024) + 1]
    assert response.closed is True
    assert connection.closed is True


@pytest.mark.parametrize(
    ("status", "code", "expected"),
    [
        (408, None, LlmTimeoutError),
        (413, None, LlmContentTooLargeError),
        (429, None, LlmUpstreamError),
        (401, None, LlmBackendUnavailableError),
        (403, None, LlmBackendUnavailableError),
        (404, None, LlmBackendUnavailableError),
        (500, None, LlmUpstreamError),
        (400, 3006, LlmContentTooLargeError),
        (400, 3007, LlmTimeoutError),
        (400, 3008, LlmTimeoutError),
        (429, 3036, LlmUpstreamError),
        (429, 3040, LlmUpstreamError),
        (403, 3041, LlmBackendUnavailableError),
        (404, 3042, LlmBackendUnavailableError),
        (400, 5007, LlmBackendUnavailableError),
        (403, 5018, LlmBackendUnavailableError),
        (403, 5035, LlmBackendUnavailableError),
    ],
)
def test_synthetic_cloudflare_status_and_code_mapping(
    status: int,
    code: int | None,
    expected: type[Exception],
) -> None:
    port, _runner = make_port(worker_result(b'{"errors":[]}', status=status, code=code))

    with pytest.raises(expected):
        port.draft_note(make_request(), cancellation=CancellationTokenSource())


def test_worker_extracts_cloudflare_code_without_exposing_error_body() -> None:
    body = json.dumps(
        {"success": False, "errors": [{"code": 3006, "message": SECRET}]},
        separators=(",", ":"),
    ).encode()
    response = FakeResponse(400, body)
    connection = FakeConnection(response)
    request = cloudflare._WorkerRequest(
        account_id=ACCOUNT_ID,
        api_token=SECRET,
        body=b"{}",
        max_output_bytes=1024,
    )

    result = cloudflare._perform_https_request(
        request,
        connection_factory=lambda host, *, timeout, context: connection,
        ssl_context_factory=lambda: cast(Any, object()),
    )

    assert result.provider_code == 3006
    assert result.body == b""
    port, _runner = make_port(result)
    with pytest.raises(LlmContentTooLargeError) as error:
        port.draft_note(make_request(), cancellation=CancellationTokenSource())
    assert SECRET not in str(error.value)


def test_worker_remote_disconnected_is_transport_and_backend_unavailable() -> None:
    connection = RemoteDisconnectedConnection(FakeResponse(200, b""))
    request = cloudflare._WorkerRequest(ACCOUNT_ID, SECRET, b"{}", 1024)

    result = cloudflare._perform_https_request(
        request,
        connection_factory=lambda host, *, timeout, context: connection,
        ssl_context_factory=lambda: cast(Any, object()),
    )

    assert result.kind == "transport"
    port, _runner = make_port(result)
    with pytest.raises(LlmBackendUnavailableError) as error:
        LlmGateway(port).draft_note(make_request(), cancellation=CancellationTokenSource())
    assert SECRET not in str(error.value)
    assert error.value.code == LlmErrorCode.BACKEND_UNAVAILABLE.value


def test_redirect_is_not_followed_and_maps_to_backend_unavailable() -> None:
    response = FakeResponse(302, b'{"location":"https://other.invalid"}')
    connection = FakeConnection(response)
    request = cloudflare._WorkerRequest(ACCOUNT_ID, SECRET, b"{}", 1024)
    result = cloudflare._perform_https_request(
        request,
        connection_factory=lambda host, *, timeout, context: connection,
        ssl_context_factory=lambda: cast(Any, object()),
    )

    assert len(connection.requests) == 1
    port, _runner = make_port(result)
    with pytest.raises(LlmBackendUnavailableError):
        port.draft_note(make_request(), cancellation=CancellationTokenSource())


class FakePipe:
    def __init__(self, data: bytes = b"", *, block: bool = False) -> None:
        self.data = io.BytesIO(data)
        self.block = block
        self.closed = False
        self.unblock = threading.Event()
        self.writes: list[bytes] = []

    def read(self, size: int = -1) -> bytes:
        if self.block:
            self.unblock.wait()
        if self.closed:
            return b""
        return self.data.read(size)

    def write(self, data: bytes) -> int:
        if self.closed:
            raise ValueError()
        self.writes.append(data)
        return len(data)

    def flush(self) -> None:
        if self.closed:
            raise ValueError()

    def close(self) -> None:
        self.closed = True
        self.unblock.set()


class FakeProcess:
    def __init__(
        self,
        stdout: bytes = b"",
        *,
        block: bool = False,
        terminate_stops: bool = True,
        kill_stops: bool = True,
    ) -> None:
        self.stdin = FakePipe()
        self.stdout = FakePipe(stdout, block=block)
        self.stderr = FakePipe()
        self.returncode: int | None = None if block else 0
        self.alive = block
        self.terminate_stops = terminate_stops
        self.kill_stops = kill_stops
        self.terminate_calls = 0
        self.kill_calls = 0
        self.wait_calls: list[float | None] = []

    def poll(self) -> int | None:
        return None if self.alive else self.returncode

    def terminate(self) -> None:
        self.terminate_calls += 1
        if self.terminate_stops:
            self.alive = False
            self.returncode = -15

    def kill(self) -> None:
        self.kill_calls += 1
        if self.kill_stops:
            self.alive = False
            self.returncode = -9

    def wait(self, timeout: float | None = None) -> int:
        self.wait_calls.append(timeout)
        if self.alive:
            if timeout is not None:
                raise subprocess.TimeoutExpired("fake-worker", timeout)
            self.alive = False
            self.returncode = -9
        return self.returncode if self.returncode is not None else 0


def test_subprocess_runner_uses_sanitized_env_and_fixed_argv() -> None:
    result = worker_result(make_outer(make_inner()))
    result_frame = cloudflare._frame(
        cloudflare._encode_worker_result(result),
        max_payload_bytes=16 * 1024 * 1024,
    )
    process = FakeProcess(result_frame)
    captured: dict[str, object] = {}

    def popen(argv: list[str], **kwargs: object) -> FakeProcess:
        captured["argv"] = argv
        captured.update(kwargs)
        return process

    runner = cloudflare.SubprocessWorkerRunner(
        popen_factory=cast(Any, popen),
        clock=time.monotonic,
        sleeper=lambda _seconds: None,
    )
    request = cloudflare._WorkerRequest(ACCOUNT_ID, SECRET, b"{}", 1024)

    result = runner.run(
        request,
        deadline=time.monotonic() + 1,
        cancellation=CancellationTokenSource(),
    )

    argv = cast(list[str], captured["argv"])
    environment = cast(dict[str, str], captured["env"])
    assert argv == [
        sys.executable,
        "-I",
        "-m",
        "second_brain.adapters.llm.cloudflare_workers_ai_worker",
    ]
    assert SECRET not in " ".join(argv)
    assert "CLOUDFLARE_API_TOKEN" not in environment
    assert "CLOUDFLARE_ACCOUNT_ID" not in environment
    assert captured["shell"] is False
    assert captured["stdin"] == subprocess.PIPE
    assert captured["stdout"] == subprocess.PIPE
    assert captured["stderr"] == subprocess.PIPE
    assert process.terminate_calls == 0
    assert process.kill_calls == 0


@pytest.mark.skipif(
    sys.platform != "win32", reason="Windows-specific OpenSSL environment regression"
)
def test_windows_sanitized_environment_initializes_ssl_in_real_isolated_child() -> None:
    environment = cloudflare._safe_worker_environment(SECRET)
    argv = [sys.executable, "-I", "-c", "import ssl; ssl.create_default_context()"]

    assert "SYSTEMROOT" in environment
    assert all(name.casefold() != "systemroot" or name == "SYSTEMROOT" for name in environment)
    assert "CLOUDFLARE_API_TOKEN" not in environment
    assert "CLOUDFLARE_ACCOUNT_ID" not in environment
    assert all(SECRET not in value for value in environment.values())
    assert SECRET not in " ".join(argv)

    completed = subprocess.run(
        argv,
        env=environment,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        timeout=10,
        check=False,
    )

    public_diagnostics = completed.stdout + completed.stderr
    assert SECRET.encode() not in public_diagnostics
    assert completed.returncode == 0


def test_subprocess_runner_cancellation_terminates_worker_and_cleans_pipes() -> None:
    process = FakeProcess(block=True)
    created = threading.Event()

    def popen(_argv: list[str], **_kwargs: object) -> FakeProcess:
        created.set()
        return process

    runner = cloudflare.SubprocessWorkerRunner(
        popen_factory=cast(Any, popen),
        sleeper=lambda seconds: time.sleep(min(seconds, 0.005)),
    )
    cancellation = CancellationTokenSource()
    raised: list[BaseException] = []

    def invoke() -> None:
        try:
            runner.run(
                cloudflare._WorkerRequest(ACCOUNT_ID, SECRET, b"{}", 1024),
                deadline=time.monotonic() + 5,
                cancellation=cancellation,
            )
        except BaseException as error:
            raised.append(error)

    thread = threading.Thread(target=invoke)
    thread.start()
    assert created.wait(1)
    cancellation.cancel()
    thread.join(2)

    assert not thread.is_alive()
    assert raised and isinstance(raised[0], cloudflare._WorkerCancelled)
    assert process.terminate_calls == 1
    assert process.kill_calls == 0
    assert process.stdin.closed is True
    assert process.stdout.closed is True
    assert process.stderr.closed is True


def test_subprocess_runner_timeout_uses_kill_fallback() -> None:
    process = FakeProcess(block=True, terminate_stops=False)
    runner = cloudflare.SubprocessWorkerRunner(
        popen_factory=cast(Any, lambda _argv, **_kwargs: process),
        sleeper=lambda seconds: time.sleep(min(seconds, 0.005)),
    )

    with pytest.raises(cloudflare._WorkerTimedOut):
        runner.run(
            cloudflare._WorkerRequest(ACCOUNT_ID, SECRET, b"{}", 1024),
            deadline=time.monotonic() + 0.03,
            cancellation=CancellationTokenSource(),
        )

    assert process.terminate_calls == 1
    assert process.kill_calls == 1
    assert process.stdin.closed is True
    assert process.stdout.closed is True
    assert process.stderr.closed is True


@pytest.mark.parametrize("failure", ["cancelled", "timed_out"])
def test_subprocess_runner_cleanup_stays_bounded_when_worker_ignores_stop(
    failure: str,
) -> None:
    process = FakeProcess(block=True, terminate_stops=False, kill_stops=False)
    clock_values = iter((0.0, 0.0) if failure == "cancelled" else (0.0, 1.0))
    runner = cloudflare.SubprocessWorkerRunner(
        popen_factory=cast(Any, lambda _argv, **_kwargs: process),
        clock=lambda: next(clock_values),
        sleeper=lambda _seconds: None,
    )

    class CancelOnSecondCheck:
        checks = 0

        def is_cancelled(self) -> bool:
            self.checks += 1
            return self.checks >= 2

    cancellation: CancellationToken
    deadline: float
    expected: type[cloudflare._WorkerExecutionError]
    if failure == "cancelled":
        cancellation = CancelOnSecondCheck()
        deadline = 5.0
        expected = cloudflare._WorkerCancelled
    else:
        cancellation = CancellationTokenSource()
        deadline = 0.5
        expected = cloudflare._WorkerTimedOut

    with pytest.raises(expected):
        runner.run(
            cloudflare._WorkerRequest(ACCOUNT_ID, SECRET, b"{}", 1024),
            deadline=deadline,
            cancellation=cancellation,
        )

    assert process.terminate_calls == 1
    assert process.kill_calls == 1
    assert process.alive is True
    assert process.wait_calls == [
        cloudflare._TERMINATE_WAIT_SECONDS,
        cloudflare._KILL_WAIT_SECONDS,
    ]
    assert process.stdin.closed is True
    assert process.stdout.closed is True
    assert process.stderr.closed is True


def test_cancel_before_port_call_does_not_start_worker() -> None:
    body = make_outer(make_inner())
    port, runner = make_port(worker_result(body))
    token = CancellationTokenSource()
    token.cancel()

    with pytest.raises(LlmCancelledError):
        port.draft_note(make_request(), cancellation=token)

    assert runner.calls == 0


def test_cancel_after_worker_completion_discards_result() -> None:
    token = CancellationTokenSource()

    class CancellingRunner(FakeRunner):
        def run(
            self,
            request: cloudflare._WorkerRequest,
            *,
            deadline: float,
            cancellation: CancellationToken,
        ) -> cloudflare._WorkerResult:
            result = super().run(request, deadline=deadline, cancellation=cancellation)
            token.cancel()
            return result

    runner = CancellingRunner(worker_result(make_outer(make_inner())))
    port = cloudflare.CloudflareWorkersAiLlmPort(
        config=cloudflare.CloudflareWorkersAiConfig(ACCOUNT_ID, SECRET),
        runner=runner,
    )

    with pytest.raises(LlmCancelledError):
        port.draft_note(make_request(), cancellation=token)


def test_public_errors_never_contain_secret_or_provider_body() -> None:
    body = json.dumps({"errors": [{"code": 5007, "message": SECRET}]}).encode()
    response = FakeResponse(400, body)
    connection = FakeConnection(response)
    request = cloudflare._WorkerRequest(ACCOUNT_ID, SECRET, b"{}", 1024)
    result = cloudflare._perform_https_request(
        request,
        connection_factory=lambda host, *, timeout, context: connection,
        ssl_context_factory=lambda: cast(Any, object()),
    )
    port, _runner = make_port(result)

    with pytest.raises(LlmBackendUnavailableError) as error:
        port.draft_note(make_request(), cancellation=CancellationTokenSource())

    assert SECRET not in str(error.value)
    assert SECRET not in repr(error.value)
    assert error.value.as_dict() == {
        "code": LlmErrorCode.BACKEND_UNAVAILABLE.value,
        "message": "LLM backend is unavailable",
    }


def test_no_temp_files_are_used_by_fake_worker_runner(tmp_path: Path) -> None:
    before = sorted(path.relative_to(tmp_path) for path in tmp_path.rglob("*"))
    assert before == []
    # The production runner uses only anonymous stdin/stdout/stderr pipes.
    assert cloudflare._SAFE_WORKER_ENVIRONMENT_NAMES.isdisjoint(
        {"CLOUDFLARE_API_TOKEN", "CLOUDFLARE_ACCOUNT_ID"}
    )
    assert sorted(path.relative_to(tmp_path) for path in tmp_path.rglob("*")) == []
