"""Deterministic Cloudflare Workers AI Whisper adapter tests without live calls."""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any, cast

import pytest

import second_brain.adapters.transcription.cloudflare_workers_ai as cloudflare
from second_brain.adapters.llm.cloudflare_workers_ai import CloudflareWorkersAiConfig
from second_brain.application.ports import (
    CancellationTokenSource,
    TranscriptionBackendUnavailableError,
    TranscriptionCancelledError,
    TranscriptionContentTooLargeError,
    TranscriptionMalformedResultError,
    TranscriptionTimeoutError,
    TranscriptionUpstreamError,
)
from second_brain.application.transcription import (
    Transcript,
    TranscriptionRequest,
)

SECRET = "sentinel-cloudflare-token-never-public"
ACCOUNT_ID = "account-opaque-123"
AUDIO = b"\x00\x01binary-audio-never-public"


def make_request(*, media_type: str = "audio/wav", audio: bytes = AUDIO) -> TranscriptionRequest:
    return TranscriptionRequest(audio=audio, media_type=media_type)


def make_body(
    text: str = "Русская расшифровка",
    *,
    include_metadata: bool = True,
    success: bool = True,
    errors: list[object] | None = None,
) -> bytes:
    result: dict[str, object] = {"text": text}
    if include_metadata:
        result.update(
            {
                "word_count": 2,
                "words": [{"word": "Русская"}, {"word": "расшифровка"}],
                "vtt": "WEBVTT\n\n00:00.000 --> 00:01.000\nРусская расшифровка",
            }
        )
    payload = {
        "result": result,
        "success": success,
        "errors": [] if errors is None else errors,
        "messages": [],
    }
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


class FakeResponse:
    def __init__(self, status: int, body: bytes) -> None:
        self.status = status
        self.body = body
        self.read_sizes: list[int] = []
        self.closed = False

    def read(self, size: int = -1) -> bytes:
        self.read_sizes.append(size)
        return self.body

    def close(self) -> None:
        self.closed = True


class FakeConnection:
    def __init__(self, response: FakeResponse, on_request: Any = None) -> None:
        self.response = response
        self.on_request = on_request
        self.requests: list[tuple[str, str, bytes, dict[str, str]]] = []
        self.closed = False

    def request(self, method: str, url: str, body: bytes, headers: dict[str, str]) -> None:
        self.requests.append((method, url, body, headers))
        if self.on_request is not None:
            self.on_request()

    def getresponse(self) -> FakeResponse:
        return self.response

    def close(self) -> None:
        self.closed = True


def make_port(
    body: bytes,
    *,
    status: int = 200,
    on_request: Any = None,
    clock: Callable[[], float] | None = None,
) -> tuple[cloudflare.CloudflareWorkersAiTranscriptionPort, FakeConnection, FakeResponse]:
    response = FakeResponse(status, body)
    connection = FakeConnection(response, on_request=on_request)
    connection_factory = cast(Any, lambda host, *, timeout, context: connection)
    ssl_context_factory = cast(Any, lambda: object())
    config = CloudflareWorkersAiConfig(ACCOUNT_ID, SECRET)
    if clock is None:
        port = cloudflare.CloudflareWorkersAiTranscriptionPort(
            config=config,
            connection_factory=connection_factory,
            ssl_context_factory=ssl_context_factory,
        )
    else:
        port = cloudflare.CloudflareWorkersAiTranscriptionPort(
            config=config,
            connection_factory=connection_factory,
            ssl_context_factory=ssl_context_factory,
            clock=clock,
        )
    return port, connection, response


def test_whisper_call_uses_exact_binary_body_fixed_model_path_and_one_request() -> None:
    port, connection, response = make_port(make_body())

    transcript = port.transcribe(make_request(), cancellation=CancellationTokenSource())

    assert transcript == Transcript("Русская расшифровка")
    assert len(connection.requests) == 1
    method, path, body, headers = connection.requests[0]
    assert method == "POST"
    assert path == ("/client/v4/accounts/account-opaque-123/ai/run/@cf/openai/whisper")
    assert body is AUDIO
    assert headers == {
        "Accept": "application/json",
        "Authorization": f"Bearer {SECRET}",
        "Content-Type": "audio/wav",
    }
    assert response.read_sizes == [cloudflare.MAX_TRANSCRIPTION_RESPONSE_BYTES + 1]
    assert response.closed is True
    assert connection.closed is True


def test_provider_metadata_is_ignored_and_never_enters_transcript_or_repr() -> None:
    port, _connection, _response = make_port(make_body(include_metadata=True))

    transcript = port.transcribe(make_request(), cancellation=CancellationTokenSource())

    assert transcript.text == "Русская расшифровка"
    assert not hasattr(transcript, "vtt")
    assert not hasattr(transcript, "word_count")
    assert "word_count" not in repr(transcript)
    assert "vtt" not in repr(transcript)


def test_missing_process_config_is_backend_unavailable_before_connection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("CLOUDFLARE_ACCOUNT_ID", raising=False)
    monkeypatch.delenv("CLOUDFLARE_API_TOKEN", raising=False)
    calls = 0

    def connection_factory(*_args: object, **_kwargs: object) -> Any:
        nonlocal calls
        calls += 1
        return None

    port = cloudflare.CloudflareWorkersAiTranscriptionPort(
        connection_factory=cast(Any, connection_factory),
        ssl_context_factory=cast(Any, lambda: object()),
    )

    with pytest.raises(TranscriptionBackendUnavailableError):
        port.transcribe(make_request(), cancellation=CancellationTokenSource())

    assert calls == 0


@pytest.mark.parametrize(
    ("status", "body", "expected"),
    [
        (
            401,
            b'{"errors":[{"code":10001,"message":"secret"}]}',
            TranscriptionBackendUnavailableError,
        ),
        (
            403,
            b'{"errors":[{"code":5035,"message":"secret"}]}',
            TranscriptionBackendUnavailableError,
        ),
        (408, b"timeout", TranscriptionTimeoutError),
        (413, b"too large", TranscriptionContentTooLargeError),
        (429, b"rate limited", TranscriptionUpstreamError),
        (500, b"upstream", TranscriptionUpstreamError),
        (302, b"redirect", TranscriptionBackendUnavailableError),
    ],
)
def test_http_and_provider_failures_map_to_safe_taxonomy(
    status: int,
    body: bytes,
    expected: type[Exception],
) -> None:
    port, _connection, _response = make_port(body, status=status)

    with pytest.raises(expected):
        port.transcribe(make_request(), cancellation=CancellationTokenSource())


def test_timeout_and_connection_failures_are_mapped_without_raw_details() -> None:
    def timeout_factory(*_args: object, **_kwargs: object) -> Any:
        raise TimeoutError("provider timeout secret")

    timeout_port = cloudflare.CloudflareWorkersAiTranscriptionPort(
        config=CloudflareWorkersAiConfig(ACCOUNT_ID, SECRET),
        connection_factory=cast(Any, timeout_factory),
        ssl_context_factory=cast(Any, lambda: object()),
    )
    with pytest.raises(TranscriptionTimeoutError) as timeout_error:
        timeout_port.transcribe(make_request(), cancellation=CancellationTokenSource())
    assert "secret" not in str(timeout_error.value)

    def connection_factory(*_args: object, **_kwargs: object) -> Any:
        raise ConnectionError("provider host secret")

    connection_port = cloudflare.CloudflareWorkersAiTranscriptionPort(
        config=CloudflareWorkersAiConfig(ACCOUNT_ID, SECRET),
        connection_factory=cast(Any, connection_factory),
        ssl_context_factory=cast(Any, lambda: object()),
    )
    with pytest.raises(TranscriptionBackendUnavailableError) as connection_error:
        connection_port.transcribe(make_request(), cancellation=CancellationTokenSource())
    assert "secret" not in str(connection_error.value)


def test_cancel_before_request_makes_zero_connection_calls() -> None:
    calls = 0

    def connection_factory(*_args: object, **_kwargs: object) -> Any:
        nonlocal calls
        calls += 1
        return None

    token = CancellationTokenSource()
    token.cancel()
    port = cloudflare.CloudflareWorkersAiTranscriptionPort(
        config=CloudflareWorkersAiConfig(ACCOUNT_ID, SECRET),
        connection_factory=cast(Any, connection_factory),
        ssl_context_factory=cast(Any, lambda: object()),
    )

    with pytest.raises(TranscriptionCancelledError):
        port.transcribe(make_request(), cancellation=token)

    assert calls == 0


def test_cancel_after_response_discards_result() -> None:
    token = CancellationTokenSource()
    port, _connection, _response = make_port(
        make_body(),
        on_request=token.cancel,
    )

    with pytest.raises(TranscriptionCancelledError):
        port.transcribe(make_request(), cancellation=token)


@pytest.mark.parametrize(
    "body",
    [
        b"not-json",
        b'{"success":true,"errors":[],"messages":[],"result":{}}',
        b'{"success":true,"errors":[],"messages":[],"result":{"text":123}}',
        b'{"success":true,"errors":[],"messages":[],"result":{"text":"ok","extra":1}}',
        b'{"success":true,"errors":[],"messages":[],"result":{"text":"ok","words":{}}}',
        b'{"success":true,"errors":[],"messages":[],"result":{"text":"ok","word_count":-1}}',
        b'{"success":true,"errors":["malformed"],"messages":[],"result":{"text":"ok"}}',
        b'{"success":true,"errors":[],"messages":["malformed"],"result":{"text":"ok"}}',
        b'{"success":true,"errors":[],"messages":[],"result":{"text":"ok"},"result":{}}',
    ],
)
def test_malformed_success_envelope_is_rejected(body: bytes) -> None:
    port, _connection, _response = make_port(body)

    with pytest.raises(TranscriptionMalformedResultError):
        port.transcribe(make_request(), cancellation=CancellationTokenSource())


def test_provider_success_false_maps_to_upstream_failure() -> None:
    port, _connection, _response = make_port(
        make_body(success=False, errors=[{"code": 5007, "message": SECRET}])
    )

    with pytest.raises(TranscriptionUpstreamError) as error:
        port.transcribe(make_request(), cancellation=CancellationTokenSource())

    assert SECRET not in str(error.value)
    assert SECRET not in repr(error.value)


def test_oversized_response_is_rejected_before_json_parsing() -> None:
    port, _connection, response = make_port(
        b"x" * (cloudflare.MAX_TRANSCRIPTION_RESPONSE_BYTES + 1)
    )

    with pytest.raises(TranscriptionContentTooLargeError):
        port.transcribe(make_request(), cancellation=CancellationTokenSource())

    assert response.read_sizes == [cloudflare.MAX_TRANSCRIPTION_RESPONSE_BYTES + 1]


def test_audio_secret_response_and_provider_body_never_enter_public_repr_or_error() -> None:
    body = json.dumps(
        {
            "errors": [{"code": 9999, "message": SECRET}],
            "secret_audio": AUDIO.decode("latin-1"),
        }
    ).encode("utf-8")
    port, _connection, _response = make_port(body, status=400)

    with pytest.raises(TranscriptionUpstreamError) as error:
        port.transcribe(make_request(), cancellation=CancellationTokenSource())

    assert SECRET not in str(error.value)
    assert AUDIO.decode("latin-1") not in str(error.value)
    assert SECRET not in repr(port)
    assert AUDIO.decode("latin-1") not in repr(make_request(audio=AUDIO))
