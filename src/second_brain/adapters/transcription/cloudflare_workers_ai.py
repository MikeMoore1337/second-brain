"""Cloudflare Workers AI Whisper adapter для provider-neutral transcription port."""

from __future__ import annotations

import http.client
import json
import math
import ssl
import time
from collections.abc import Callable, Mapping
from contextlib import suppress
from dataclasses import dataclass, field
from typing import Protocol, cast

from second_brain.adapters.llm.cloudflare_workers_ai import (
    CLOUDFLARE_ACCOUNT_ID_ENV,
    CLOUDFLARE_API_HOST,
    CLOUDFLARE_API_TOKEN_ENV,
    CloudflareWorkersAiConfig,
    CloudflareWorkersAiConfigError,
    load_cloudflare_workers_ai_config,
)
from second_brain.application.ports import (
    CancellationToken,
    TranscriptionBackendUnavailableError,
    TranscriptionCancelledError,
    TranscriptionContentTooLargeError,
    TranscriptionError,
    TranscriptionInvalidRequestError,
    TranscriptionMalformedResultError,
    TranscriptionTimeoutError,
    TranscriptionUpstreamError,
)
from second_brain.application.transcription import (
    MAX_TRANSCRIPTION_AUDIO_BYTES,
    Transcript,
    TranscriptionRequest,
    _validate_request,
    validate_transcript,
)

CLOUDFLARE_TRANSCRIPTION_MODEL = "@cf/openai/whisper"
CLOUDFLARE_WHISPER_MODEL = CLOUDFLARE_TRANSCRIPTION_MODEL
CLOUDFLARE_TRANSCRIPTION_PATH = "/client/v4/accounts/{account_id}/ai/run/@cf/openai/whisper"
CLOUDFLARE_AI_RUN_PATH = "/client/v4/accounts/{account_id}/ai/run/{model_name}"

TRANSCRIPTION_TIMEOUT_SECONDS = 30.0
MAX_TRANSCRIPTION_RESPONSE_BYTES = 1024 * 1024
MAX_RESPONSE_BYTES = MAX_TRANSCRIPTION_RESPONSE_BYTES

_ALLOWED_RESPONSE_FIELDS = frozenset({"result", "success", "errors", "messages"})
_ALLOWED_RESULT_FIELDS = frozenset({"text", "vtt", "word_count", "words"})
_TIMEOUT_PROVIDER_CODES = frozenset({3007, 3008})
_BACKEND_PROVIDER_CODES = frozenset({3041, 3042, 5007, 5018, 5035})


class _HttpResponse(Protocol):
    status: int

    def read(self, size: int = -1) -> bytes:
        """Прочитать bounded response bytes."""

    def close(self) -> None:
        """Закрыть response."""


class _HttpsConnection(Protocol):
    def request(
        self,
        method: str,
        url: str,
        body: bytes,
        headers: Mapping[str, str],
    ) -> None:
        """Выполнить единственный HTTPS request."""

    def getresponse(self) -> _HttpResponse:
        """Получить единственный HTTP response."""

    def close(self) -> None:
        """Закрыть connection."""


ConnectionFactory = Callable[..., _HttpsConnection]
SslContextFactory = Callable[[], ssl.SSLContext]


@dataclass(frozen=True, slots=True)
class _ProviderResult:
    """Внутренний bounded result; provider body не попадает в repr или errors."""

    kind: str
    http_status: int = 0
    provider_code: int | None = None
    body: bytes = field(default=b"", repr=False, compare=False)


def _new_https_connection(
    host: str,
    *,
    timeout: float,
    context: ssl.SSLContext,
) -> _HttpsConnection:
    return cast(
        _HttpsConnection,
        http.client.HTTPSConnection(host, timeout=timeout, context=context),
    )


def _endpoint_path(account_id: str) -> str:
    """Собрать fixed Cloudflare AI Run path из уже проверенного account ID."""

    if type(account_id) is not str or not account_id or account_id in {".", ".."}:
        raise ValueError()
    if any(
        ord(char) < 0x20 or ord(char) == 0x7F or char.isspace() or char in "/?#\\%@:"
        for char in account_id
    ):
        raise ValueError()
    try:
        account_id.encode("ascii")
    except UnicodeEncodeError:
        raise ValueError() from None
    return CLOUDFLARE_TRANSCRIPTION_PATH.format(account_id=account_id)


def _perform_https_request(
    request: TranscriptionRequest,
    config: CloudflareWorkersAiConfig,
    *,
    timeout_seconds: float,
    connection_factory: ConnectionFactory | None = None,
    ssl_context_factory: SslContextFactory | None = None,
) -> _ProviderResult:
    """Выполнить ровно один verified HTTPS POST с exact binary audio body."""

    make_connection = _new_https_connection if connection_factory is None else connection_factory
    make_context = (
        ssl.create_default_context if ssl_context_factory is None else ssl_context_factory
    )
    connection: _HttpsConnection | None = None
    response: _HttpResponse | None = None
    try:
        context = make_context()
        connection = make_connection(
            CLOUDFLARE_API_HOST,
            timeout=timeout_seconds,
            context=context,
        )
        connection.request(
            "POST",
            _endpoint_path(config.account_id),
            request.audio,
            {
                "Accept": "application/json",
                "Authorization": f"Bearer {config.api_token}",
                "Content-Type": request.media_type,
            },
        )
        response = connection.getresponse()
        status = response.status
        if type(status) is not int or not 100 <= status <= 599:
            return _ProviderResult(kind="malformed")
        body = response.read(MAX_TRANSCRIPTION_RESPONSE_BYTES + 1)
        if type(body) is not bytes:
            return _ProviderResult(kind="malformed")
        if len(body) > MAX_TRANSCRIPTION_RESPONSE_BYTES:
            return _ProviderResult(kind="too_large")
        return _ProviderResult(
            kind="http",
            http_status=status,
            provider_code=_extract_provider_code(body) if status != 200 else None,
            body=body,
        )
    except TimeoutError:
        return _ProviderResult(kind="timeout")
    except http.client.RemoteDisconnected:
        return _ProviderResult(kind="transport")
    except http.client.HTTPException:
        return _ProviderResult(kind="transport")
    except OSError, TypeError, ValueError, ssl.SSLError:
        return _ProviderResult(kind="transport")
    except Exception:
        return _ProviderResult(kind="transport")
    finally:
        if response is not None:
            with suppress(OSError, ValueError):
                response.close()
        if connection is not None:
            with suppress(OSError, ValueError):
                connection.close()


class _DuplicateJsonKey(ValueError):
    """Отклонить duplicate keys в untrusted provider JSON."""


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise _DuplicateJsonKey()
        result[key] = value
    return result


def _reject_json_constant(value: str) -> object:
    del value
    raise ValueError()


def _load_json_object(raw: bytes) -> object:
    return json.loads(
        raw,
        object_pairs_hook=_unique_object,
        parse_constant=_reject_json_constant,
    )


def _extract_provider_code(body: bytes) -> int | None:
    """Извлечь только bounded numeric error code без хранения provider message."""

    try:
        payload = _load_json_object(body)
    except UnicodeDecodeError, json.JSONDecodeError, RecursionError, TypeError, ValueError:
        return None
    if type(payload) is not dict:
        return None
    candidates: list[object] = [payload.get("code")]
    errors = payload.get("errors")
    if type(errors) is list:
        candidates.extend(item.get("code") for item in errors[:8] if type(item) is dict)
    error = payload.get("error")
    if type(error) is dict:
        candidates.append(error.get("code"))
    for candidate in candidates:
        if type(candidate) is int and not isinstance(candidate, bool) and candidate >= 0:
            return candidate
    return None


def _raise_http_mapping(result: _ProviderResult) -> None:
    """Свести HTTP/provider code к стабильной transcription taxonomy."""

    code = result.provider_code
    if type(code) is int and not isinstance(code, bool):
        if code == 3006:
            raise TranscriptionContentTooLargeError()
        if code in _TIMEOUT_PROVIDER_CODES:
            raise TranscriptionTimeoutError()
        if code in _BACKEND_PROVIDER_CODES:
            raise TranscriptionBackendUnavailableError()
    status = result.http_status
    if status in {408, 504}:
        raise TranscriptionTimeoutError()
    if status == 413:
        raise TranscriptionContentTooLargeError()
    if status in {401, 403, 404}:
        raise TranscriptionBackendUnavailableError()
    if 300 <= status < 400:
        raise TranscriptionBackendUnavailableError()
    raise TranscriptionUpstreamError()


def _validate_provider_metadata(result: Mapping[str, object]) -> None:
    """Проверить известные metadata shape, не превращая их в application DTO."""

    if "vtt" in result and type(result["vtt"]) is not str:
        raise TranscriptionMalformedResultError()
    if "word_count" in result:
        word_count = result["word_count"]
        if type(word_count) is int:
            numeric_word_count = float(word_count)
        elif type(word_count) is float:
            numeric_word_count = word_count
        else:
            raise TranscriptionMalformedResultError()
        if not math.isfinite(numeric_word_count) or numeric_word_count < 0:
            raise TranscriptionMalformedResultError()
    if "words" in result and type(result["words"]) is not list:
        raise TranscriptionMalformedResultError()


def _validate_provider_message_list(value: object) -> None:
    """Проверить envelope error/message list без сохранения provider diagnostics."""

    if type(value) is not list or any(type(item) is not dict for item in value):
        raise TranscriptionMalformedResultError()


def _decode_transcript(result: _ProviderResult) -> Transcript:
    """Strictly decode Cloudflare envelope and expose only ``result.text``."""

    if result.kind != "http":
        if result.kind == "too_large":
            raise TranscriptionContentTooLargeError()
        if result.kind == "timeout":
            raise TranscriptionTimeoutError()
        if result.kind == "transport":
            raise TranscriptionBackendUnavailableError()
        raise TranscriptionMalformedResultError()
    if result.http_status != 200:
        _raise_http_mapping(result)
    if type(result.body) is not bytes:
        raise TranscriptionMalformedResultError()
    if len(result.body) > MAX_TRANSCRIPTION_RESPONSE_BYTES:
        raise TranscriptionContentTooLargeError()
    try:
        payload = _load_json_object(result.body)
    except UnicodeDecodeError, json.JSONDecodeError, RecursionError, TypeError, ValueError:
        raise TranscriptionMalformedResultError() from None
    if type(payload) is not dict or set(payload) - _ALLOWED_RESPONSE_FIELDS:
        raise TranscriptionMalformedResultError()
    if type(payload.get("success")) is not bool:
        raise TranscriptionMalformedResultError()
    errors = payload.get("errors", [])
    messages = payload.get("messages", [])
    _validate_provider_message_list(errors)
    _validate_provider_message_list(messages)
    if payload["success"] is not True or errors:
        raise TranscriptionUpstreamError()
    provider_result = payload.get("result")
    if type(provider_result) is not dict or set(provider_result) - _ALLOWED_RESULT_FIELDS:
        raise TranscriptionMalformedResultError()
    text = provider_result.get("text")
    if type(text) is not str:
        raise TranscriptionMalformedResultError()
    _validate_provider_metadata(provider_result)
    return validate_transcript(Transcript(text=text))


def _cancellation_requested(cancellation: CancellationToken) -> bool:
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


@dataclass(slots=True)
class CloudflareWorkersAiTranscriptionPort:
    """Один bounded Cloudflare Whisper call без retry, fallback или persistence."""

    config: CloudflareWorkersAiConfig | None = field(default=None, repr=False)
    connection_factory: ConnectionFactory | None = field(default=None, repr=False)
    ssl_context_factory: SslContextFactory | None = field(default=None, repr=False)
    clock: Callable[[], float] = field(default=time.monotonic, repr=False)

    def transcribe(
        self,
        request: TranscriptionRequest,
        *,
        cancellation: CancellationToken,
    ) -> Transcript:
        """Проверить bounded request, выполнить один HTTPS call и вернуть Transcript."""

        if _cancellation_requested(cancellation):
            raise TranscriptionCancelledError()
        _validate_request(request)
        try:
            config = self.config if self.config is not None else load_cloudflare_workers_ai_config()
            started = self.clock()
            deadline = started + TRANSCRIPTION_TIMEOUT_SECONDS
        except CloudflareWorkersAiConfigError:
            raise TranscriptionBackendUnavailableError() from None
        except TranscriptionError:
            raise
        except OSError, TypeError, ValueError:
            raise TranscriptionBackendUnavailableError() from None
        if type(config) is not CloudflareWorkersAiConfig:
            raise TranscriptionBackendUnavailableError()
        if _cancellation_requested(cancellation):
            raise TranscriptionCancelledError()
        remaining = deadline - self.clock()
        if remaining <= 0:
            raise TranscriptionTimeoutError()
        result = _perform_https_request(
            request,
            config,
            timeout_seconds=min(TRANSCRIPTION_TIMEOUT_SECONDS, remaining),
            connection_factory=self.connection_factory,
            ssl_context_factory=self.ssl_context_factory,
        )
        if _cancellation_requested(cancellation):
            raise TranscriptionCancelledError()
        if self.clock() >= deadline:
            raise TranscriptionTimeoutError()
        return _decode_transcript(result)


__all__ = [
    "CLOUDFLARE_ACCOUNT_ID_ENV",
    "CLOUDFLARE_AI_RUN_PATH",
    "CLOUDFLARE_API_HOST",
    "CLOUDFLARE_API_TOKEN_ENV",
    "CLOUDFLARE_TRANSCRIPTION_MODEL",
    "CLOUDFLARE_TRANSCRIPTION_PATH",
    "CLOUDFLARE_WHISPER_MODEL",
    "MAX_RESPONSE_BYTES",
    "MAX_TRANSCRIPTION_AUDIO_BYTES",
    "MAX_TRANSCRIPTION_RESPONSE_BYTES",
    "TRANSCRIPTION_TIMEOUT_SECONDS",
    "CloudflareWorkersAiTranscriptionPort",
]
