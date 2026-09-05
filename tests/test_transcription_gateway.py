"""Детерминированные проверки provider-neutral transcription boundary."""

from __future__ import annotations

from dataclasses import fields
from typing import Any, cast

import pytest

from second_brain.application.ports import (
    CancellationToken,
    CancellationTokenSource,
    TranscriptionBackendUnavailableError,
    TranscriptionCancelledError,
    TranscriptionContentTooLargeError,
    TranscriptionError,
    TranscriptionErrorCode,
    TranscriptionInvalidRequestError,
    TranscriptionMalformedResultError,
    TranscriptionTimeoutError,
    TranscriptionUpstreamError,
)
from second_brain.application.transcription import (
    MAX_AUDIO_BYTES,
    MAX_TRANSCRIPT_BYTES,
    Transcript,
    TranscriptionGateway,
    TranscriptionRequest,
    normalize_audio_media_type,
)


class FakeTranscriptionPort:
    """Единственный gateway collaborator без сети, filesystem или LLM."""

    def __init__(
        self,
        result: object = None,
        error: Exception | None = None,
        on_transcribe: Any = None,
    ) -> None:
        self.result = result
        self.error = error
        self.on_transcribe = on_transcribe
        self.calls: list[tuple[TranscriptionRequest, CancellationToken]] = []

    def transcribe(
        self,
        request: TranscriptionRequest,
        *,
        cancellation: CancellationToken,
    ) -> Transcript:
        self.calls.append((request, cancellation))
        if self.on_transcribe is not None:
            self.on_transcribe(cancellation)
        if self.error is not None:
            raise self.error
        return cast(Transcript, self.result)


def make_request(
    *,
    audio: bytes = b"\x00\x01exact-audio",
    media_type: str = "audio/wav",
) -> TranscriptionRequest:
    """Собрать bounded request с exact binary payload."""

    return TranscriptionRequest(audio=audio, media_type=media_type)


def transcribe_with(
    request: TranscriptionRequest,
    port: FakeTranscriptionPort,
    token: CancellationToken | None = None,
) -> Transcript:
    """Вызвать gateway с deterministic cancellation token."""

    return TranscriptionGateway(port).transcribe(
        request,
        cancellation=token if token is not None else CancellationTokenSource(),
    )


def test_valid_russian_transcript_and_exact_audio_reach_fake_port_once() -> None:
    request = make_request(audio=b"\x89WAV\x00\xff\x10")
    transcript = Transcript("Сегодня выбрал X — это русский текст.\nИ вторая строка.")
    port = FakeTranscriptionPort(transcript)

    result = transcribe_with(request, port)

    assert result is transcript
    assert len(port.calls) == 1
    assert port.calls[0][0] is request
    assert port.calls[0][0].audio == b"\x89WAV\x00\xff\x10"
    assert port.calls[0][0].media_type == "audio/wav"


def test_request_and_transcript_dtos_are_minimal() -> None:
    assert tuple(field.name for field in fields(TranscriptionRequest)) == ("audio", "media_type")
    assert tuple(field.name for field in fields(Transcript)) == ("text",)
    assert "exact-audio" not in repr(make_request())
    assert not hasattr(Transcript("text"), "word_count")
    assert not hasattr(Transcript("text"), "vtt")


@pytest.mark.parametrize(
    "transcription_request",
    [
        cast(TranscriptionRequest, object()),
        make_request(audio=cast(bytes, bytearray(b"not-bytes"))),
        make_request(audio=b""),
        make_request(media_type="text/plain"),
        make_request(media_type="audio/webm; charset=utf-8"),
        make_request(media_type="audio/webm;codecs=opus;profile=1"),
        make_request(media_type="audio/webm;codecs=pcm"),
    ],
)
def test_invalid_request_is_rejected_before_port_call(
    transcription_request: TranscriptionRequest,
) -> None:
    port = FakeTranscriptionPort(Transcript("valid"))

    with pytest.raises(TranscriptionInvalidRequestError) as error:
        transcribe_with(transcription_request, port)

    assert error.value.code == TranscriptionErrorCode.INVALID_REQUEST.value
    assert port.calls == []


def test_oversized_audio_is_rejected_before_port_call() -> None:
    port = FakeTranscriptionPort(Transcript("valid"))

    with pytest.raises(TranscriptionContentTooLargeError) as error:
        transcribe_with(make_request(audio=b"x" * (MAX_AUDIO_BYTES + 1)), port)

    assert error.value.code == TranscriptionErrorCode.CONTENT_TOO_LARGE.value
    assert port.calls == []


@pytest.mark.parametrize(
    ("media_type", "expected"),
    [
        ("audio/webm", "audio/webm"),
        ("audio/webm;codecs=opus", "audio/webm"),
        ('audio/ogg; codecs="opus"', "audio/ogg"),
        ("AUDIO/WAV", "audio/wav"),
        ("audio/x-wav", "audio/x-wav"),
        ("audio/mpeg", "audio/mpeg"),
        ("audio/mp4", "audio/mp4"),
        ("audio/x-m4a", "audio/x-m4a"),
    ],
)
def test_audio_media_type_allowlist_is_small_and_deterministic(
    media_type: str,
    expected: str,
) -> None:
    assert normalize_audio_media_type(media_type) == expected


@pytest.mark.parametrize(
    "text",
    [
        "",
        " \t\n",
        "contains\x00nul",
        "contains\x7fdel",
        "contains\u2028separator",
        "я" * (MAX_TRANSCRIPT_BYTES // 2 + 1),
    ],
    ids=["empty", "whitespace", "nul", "delete", "line-separator", "oversized"],
)
def test_malformed_or_oversized_transcript_is_rejected_after_port_call(text: str) -> None:
    port = FakeTranscriptionPort(Transcript(text))

    expected = (
        TranscriptionContentTooLargeError
        if len(text.encode("utf-8")) > MAX_TRANSCRIPT_BYTES
        else TranscriptionMalformedResultError
    )
    with pytest.raises(expected):
        transcribe_with(make_request(), port)

    assert len(port.calls) == 1


def test_wrong_transcript_result_type_is_rejected_without_coercion() -> None:
    port = FakeTranscriptionPort({"text": "not a DTO"})

    with pytest.raises(TranscriptionMalformedResultError):
        transcribe_with(make_request(), port)


def test_cancellation_before_port_call_is_deterministic() -> None:
    token = CancellationTokenSource()
    token.cancel()
    port = FakeTranscriptionPort(Transcript("valid"))

    with pytest.raises(TranscriptionCancelledError) as error:
        transcribe_with(make_request(), port, token)

    assert error.value.code == TranscriptionErrorCode.CANCELLED.value
    assert port.calls == []


def test_cancellation_after_port_call_discards_transcript() -> None:
    token = CancellationTokenSource()
    port = FakeTranscriptionPort(
        Transcript("valid"),
        on_transcribe=lambda cancellation: token.cancel(),
    )

    with pytest.raises(TranscriptionCancelledError):
        transcribe_with(make_request(), port, token)

    assert len(port.calls) == 1


@pytest.mark.parametrize(
    ("port_error", "expected_type", "expected_code"),
    [
        (
            TranscriptionTimeoutError("raw timeout secret"),
            TranscriptionTimeoutError,
            "TRANSCRIPTION_TIMEOUT",
        ),
        (TimeoutError("raw timeout secret"), TranscriptionTimeoutError, "TRANSCRIPTION_TIMEOUT"),
        (
            TranscriptionUpstreamError("raw provider secret"),
            TranscriptionUpstreamError,
            "TRANSCRIPTION_UPSTREAM_FAILURE",
        ),
        (
            ConnectionError("raw host secret"),
            TranscriptionBackendUnavailableError,
            "TRANSCRIPTION_BACKEND_UNAVAILABLE",
        ),
        (
            TranscriptionBackendUnavailableError("raw backend secret"),
            TranscriptionBackendUnavailableError,
            "TRANSCRIPTION_BACKEND_UNAVAILABLE",
        ),
        (
            ValueError("provider secret"),
            TranscriptionUpstreamError,
            "TRANSCRIPTION_UPSTREAM_FAILURE",
        ),
    ],
)
def test_port_errors_keep_stable_safe_taxonomy(
    port_error: Exception,
    expected_type: type[TranscriptionError],
    expected_code: str,
) -> None:
    port = FakeTranscriptionPort(error=port_error)

    with pytest.raises(expected_type) as raised:
        transcribe_with(make_request(), port)

    assert raised.value.code == expected_code
    assert "raw" not in str(raised.value)
    assert "secret" not in str(raised.value)
    assert "provider" not in str(raised.value)
    assert raised.value.as_dict() == {"code": expected_code, "message": raised.value.message}
