"""Provider-neutral application contract для bounded speech-to-text."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import cast

from second_brain.application.ports import (
    CancellationToken,
    TranscriptionBackendUnavailableError,
    TranscriptionCancelledError,
    TranscriptionContentTooLargeError,
    TranscriptionError,
    TranscriptionErrorCode,
    TranscriptionInvalidRequestError,
    TranscriptionMalformedResultError,
    TranscriptionPort,
    TranscriptionTimeoutError,
    TranscriptionUpstreamError,
)

MAX_TRANSCRIPTION_AUDIO_BYTES = 15 * 1024 * 1024
MAX_AUDIO_BYTES = MAX_TRANSCRIPTION_AUDIO_BYTES
MAX_TRANSCRIPT_BYTES = 128 * 1024
MAX_AUDIO_MEDIA_TYPE_BYTES = 256

_ALLOWED_AUDIO_MEDIA_TYPES = frozenset(
    {
        "audio/webm",
        "audio/ogg",
        "audio/wav",
        "audio/x-wav",
        "audio/mpeg",
        "audio/mp4",
        "audio/x-m4a",
    }
)
_CODEC_PARAMETER_MEDIA_TYPES = frozenset({"audio/webm", "audio/ogg"})
_ALLOWED_TRANSCRIPTION_CONTROLS = frozenset({"\t", "\n", "\r"})


@dataclass(frozen=True, slots=True)
class TranscriptionRequest:
    """Один bounded binary audio request без path, filename или provider fields."""

    audio: bytes = field(repr=False)
    media_type: str


@dataclass(frozen=True, slots=True)
class Transcript:
    """Минимальный speech-to-text result; provider metadata намеренно отсутствует."""

    text: str


@dataclass(frozen=True, slots=True)
class TranscriptionGateway:
    """Тонкая application boundary без provider, Web, LLM или write capabilities."""

    port: TranscriptionPort

    def transcribe(
        self,
        request: TranscriptionRequest,
        *,
        cancellation: CancellationToken,
    ) -> Transcript:
        """Проверить request, вызвать один port и вернуть validated Transcript."""

        if _check_cancellation(cancellation):
            raise TranscriptionCancelledError()
        _validate_request(request)

        try:
            transcript = self.port.transcribe(request, cancellation=cancellation)
        except Exception as exc:
            raise _map_port_exception(exc) from None

        if _check_cancellation(cancellation):
            raise TranscriptionCancelledError()
        _validate_transcript(transcript)
        return transcript


def normalize_audio_media_type(value: object) -> str:
    """Разрешить только небольшой audio MIME allowlist и один browser codec parameter."""

    if type(value) is not str or not value:
        raise TranscriptionInvalidRequestError()
    try:
        if len(value.encode("ascii")) > MAX_AUDIO_MEDIA_TYPE_BYTES:
            raise TranscriptionInvalidRequestError()
    except UnicodeEncodeError:
        raise TranscriptionInvalidRequestError() from None
    if any(ord(char) < 32 or ord(char) == 127 for char in value):
        raise TranscriptionInvalidRequestError()

    parts = value.split(";")
    media_type = parts[0].strip().casefold()
    if media_type not in _ALLOWED_AUDIO_MEDIA_TYPES:
        raise TranscriptionInvalidRequestError()
    if len(parts) == 1:
        return media_type
    if len(parts) != 2 or media_type not in _CODEC_PARAMETER_MEDIA_TYPES:
        raise TranscriptionInvalidRequestError()
    name, separator, parameter = parts[1].partition("=")
    if separator == "" or name.strip().casefold() != "codecs":
        raise TranscriptionInvalidRequestError()
    normalized_parameter = parameter.strip()
    if (
        len(normalized_parameter) >= 2
        and normalized_parameter[0] == normalized_parameter[-1] == '"'
    ):
        normalized_parameter = normalized_parameter[1:-1]
    if (
        normalized_parameter.casefold() != "opus"
        or not normalized_parameter
        or any(char.isspace() or char in {'"', "\\", ";"} for char in normalized_parameter)
    ):
        raise TranscriptionInvalidRequestError()
    return media_type


def validate_transcript(transcript: object) -> Transcript:
    """Проверить provider result тем же bounded validator, что использует gateway."""

    _validate_transcript(transcript)
    return cast(Transcript, transcript)


def _validate_request(request: object) -> None:
    """Fail-closed request policy до единственного port call."""

    if type(request) is not TranscriptionRequest:
        raise TranscriptionInvalidRequestError()
    if type(request.audio) is not bytes:
        raise TranscriptionInvalidRequestError()
    if not request.audio:
        raise TranscriptionInvalidRequestError()
    if len(request.audio) > MAX_TRANSCRIPTION_AUDIO_BYTES:
        raise TranscriptionContentTooLargeError()
    normalize_audio_media_type(request.media_type)


def _validate_transcript(transcript: object) -> None:
    """Проверить только untrusted plain text и его UTF-8 byte budget."""

    if type(transcript) is not Transcript or type(transcript.text) is not str:
        raise TranscriptionMalformedResultError()
    if not transcript.text.strip() or _contains_forbidden_control(transcript.text):
        raise TranscriptionMalformedResultError()
    try:
        encoded = transcript.text.encode("utf-8")
    except UnicodeEncodeError:
        raise TranscriptionMalformedResultError() from None
    if len(encoded) > MAX_TRANSCRIPT_BYTES:
        raise TranscriptionContentTooLargeError()


def _contains_forbidden_control(value: str) -> bool:
    """Разрешить обычные line breaks, но не управляющие или format separators."""

    return any(
        (ord(char) < 32 and char not in _ALLOWED_TRANSCRIPTION_CONTROLS)
        or 0x7F <= ord(char) <= 0x9F
        or char in {"\u2028", "\u2029"}
        for char in value
    )


def _check_cancellation(cancellation: CancellationToken) -> bool:
    """Проверить cancellation token без callback, I/O или provider knowledge."""

    checker = getattr(cancellation, "is_cancelled", None)
    if not callable(checker):
        raise TranscriptionInvalidRequestError()
    try:
        value = checker()
    except Exception:
        raise TranscriptionInvalidRequestError() from None
    if type(value) is not bool:
        raise TranscriptionInvalidRequestError()
    return value


def _map_port_exception(error: Exception) -> TranscriptionError:
    """Скрыть adapter details и свести port exception к закрытой taxonomy."""

    if isinstance(error, TranscriptionError):
        error_types: dict[str, Callable[[], TranscriptionError]] = {
            TranscriptionErrorCode.INVALID_REQUEST.value: TranscriptionInvalidRequestError,
            TranscriptionErrorCode.CANCELLED.value: TranscriptionCancelledError,
            TranscriptionErrorCode.TIMEOUT.value: TranscriptionTimeoutError,
            TranscriptionErrorCode.BACKEND_UNAVAILABLE.value: TranscriptionBackendUnavailableError,
            TranscriptionErrorCode.UPSTREAM_FAILURE.value: TranscriptionUpstreamError,
            TranscriptionErrorCode.MALFORMED_RESULT.value: TranscriptionMalformedResultError,
            TranscriptionErrorCode.CONTENT_TOO_LARGE.value: TranscriptionContentTooLargeError,
        }
        return error_types.get(error.code, TranscriptionUpstreamError)()
    if isinstance(error, TimeoutError):
        return TranscriptionTimeoutError()
    if isinstance(error, (ConnectionError, OSError)):
        return TranscriptionBackendUnavailableError()
    return TranscriptionUpstreamError()


__all__ = [
    "MAX_AUDIO_BYTES",
    "MAX_AUDIO_MEDIA_TYPE_BYTES",
    "MAX_TRANSCRIPTION_AUDIO_BYTES",
    "MAX_TRANSCRIPT_BYTES",
    "Transcript",
    "TranscriptionBackendUnavailableError",
    "TranscriptionCancelledError",
    "TranscriptionContentTooLargeError",
    "TranscriptionError",
    "TranscriptionErrorCode",
    "TranscriptionGateway",
    "TranscriptionInvalidRequestError",
    "TranscriptionMalformedResultError",
    "TranscriptionPort",
    "TranscriptionRequest",
    "TranscriptionTimeoutError",
    "TranscriptionUpstreamError",
    "normalize_audio_media_type",
    "validate_transcript",
]
