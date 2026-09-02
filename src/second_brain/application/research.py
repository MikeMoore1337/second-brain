"""Second Brain-native read-only contract для внешнего research."""

from __future__ import annotations

import ipaddress
import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from ipaddress import IPv4Address, IPv6Address
from urllib.parse import parse_qsl, urlsplit

from second_brain.application.ports import (
    CancellationToken,
    ExternalResearchPort,
    ResearchBackendUnavailableError,
    ResearchCancelledError,
    ResearchContentTooLargeError,
    ResearchError,
    ResearchErrorCode,
    ResearchInvalidRequestError,
    ResearchMalformedResultError,
    ResearchTimeoutError,
    ResearchUpstreamError,
)

DEFAULT_TIMEOUT_SECONDS = 30
MAX_TIMEOUT_SECONDS = 300
DEFAULT_MAX_BYTES = 5_000_000
MAX_MAX_BYTES = 5_000_000

_SAFE_BACKEND = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,63}\Z")
_CREDENTIAL_QUERY_KEYS = frozenset(
    {
        "api_key",
        "apikey",
        "auth",
        "authorization",
        "client_secret",
        "cookie",
        "credential",
        "credentials",
        "jwt",
        "password",
        "passwd",
        "secret",
        "session",
        "signature",
        "sig",
        "token",
        "access_token",
        "refresh_token",
    }
)
_CREDENTIAL_QUERY_KEYS_NORMALIZED = frozenset(
    re.sub(r"[^a-z0-9]", "", key.casefold()) for key in _CREDENTIAL_QUERY_KEYS
)
_LOCAL_HOSTNAMES = frozenset(
    {
        "localhost",
        "localhost.localdomain",
        "localdomain",
        "ip6-localhost",
        "ip6-loopback",
    }
)
_LOCAL_HOST_SUFFIXES = (
    ".localhost",
    ".local",
    ".internal",
    ".lan",
    ".home",
    ".corp",
    ".intranet",
    ".test",
    ".invalid",
    ".example",
)
_GITHUB_HOSTS = frozenset(
    {
        "github.com",
        "www.github.com",
        "api.github.com",
        "gist.github.com",
        "raw.githubusercontent.com",
    }
)
_YOUTUBE_HOSTS = frozenset(
    {
        "youtube.com",
        "www.youtube.com",
        "m.youtube.com",
        "music.youtube.com",
        "youtu.be",
        "www.youtu.be",
        "youtube-nocookie.com",
        "www.youtube-nocookie.com",
    }
)


class SourceKind(StrEnum):
    """Публичные source kinds, разрешённые research contract v1."""

    WEB = "web"
    GITHUB = "github"
    RSS = "rss"
    YOUTUBE = "youtube"


@dataclass(frozen=True, slots=True)
class ResearchRequest:
    """Ограниченный запрос чтения одного public HTTP(S) источника."""

    source_kind: SourceKind
    uri: str
    timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS
    max_bytes: int = DEFAULT_MAX_BYTES


@dataclass(frozen=True, slots=True)
class ResearchSource:
    """Normalized external source; весь ``content`` остаётся untrusted text."""

    uri: str
    source_kind: SourceKind
    retrieved_at: datetime
    backend: str
    content: str
    title: str | None = None
    author: str | None = None
    media_type: str | None = None
    upstream_id: str | None = None
    published_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class ResearchGateway:
    """Тонкий application orchestration layer без network и write capabilities."""

    port: ExternalResearchPort

    def read(
        self,
        request: ResearchRequest,
        *,
        cancellation: CancellationToken,
    ) -> ResearchSource:
        """Валидировать request, вызвать ровно один read и проверить его результат."""

        if _check_cancellation(cancellation):
            raise ResearchCancelledError()
        _validate_request(request)

        try:
            source = self.port.read(request, cancellation=cancellation)
        except Exception as exc:
            raise _map_port_exception(exc) from None

        if _check_cancellation(cancellation):
            raise ResearchCancelledError()
        _validate_source(source, request)
        return source


def _validate_request(request: ResearchRequest) -> None:
    """Fail-closed request policy до единственного port call."""

    if not isinstance(request, ResearchRequest):
        raise ResearchInvalidRequestError()
    if not isinstance(request.source_kind, SourceKind):
        raise ResearchInvalidRequestError()
    if type(request.timeout_seconds) is not int or not (
        1 <= request.timeout_seconds <= MAX_TIMEOUT_SECONDS
    ):
        raise ResearchInvalidRequestError()
    if type(request.max_bytes) is not int or not (1 <= request.max_bytes <= MAX_MAX_BYTES):
        raise ResearchInvalidRequestError()
    _validate_public_uri(request.uri, request.source_kind)


def _validate_public_uri(uri: object, source_kind: SourceKind) -> None:
    """Проверить URL syntax и очевидно non-public targets без DNS/network lookup."""

    if not isinstance(uri, str) or not uri or _contains_control_or_space(uri):
        raise ResearchInvalidRequestError()
    try:
        parsed = urlsplit(uri)
        scheme = parsed.scheme.casefold()
        hostname = parsed.hostname
        _ = parsed.port
    except ValueError:
        raise ResearchInvalidRequestError() from None
    if scheme not in {"http", "https"} or not parsed.netloc or not hostname:
        raise ResearchInvalidRequestError()
    if parsed.username is not None or parsed.password is not None or "@" in parsed.netloc:
        raise ResearchInvalidRequestError()
    if _contains_credentials(parsed.query) or _contains_credentials(parsed.fragment):
        raise ResearchInvalidRequestError()

    normalized_host = hostname.rstrip(".").casefold()
    if _is_local_or_internal_hostname(normalized_host):
        raise ResearchInvalidRequestError()
    _reject_non_public_literal_ip(normalized_host)
    if source_kind is SourceKind.GITHUB and normalized_host not in _GITHUB_HOSTS:
        raise ResearchInvalidRequestError()
    if source_kind is SourceKind.YOUTUBE and normalized_host not in _YOUTUBE_HOSTS:
        raise ResearchInvalidRequestError()


def _contains_credentials(value: str) -> bool:
    """Определить credential-like query/fragment key без вывода самого URI."""

    try:
        pairs = parse_qsl(value, keep_blank_values=True, strict_parsing=False)
    except ValueError:
        return True
    for key, _ in pairs:
        normalized_key = re.sub(r"[^a-z0-9]", "", key.casefold())
        if normalized_key in _CREDENTIAL_QUERY_KEYS_NORMALIZED or normalized_key.endswith(
            (
                "token",
                "secret",
                "password",
                "credential",
                "cookie",
                "session",
                "signature",
                "auth",
            )
        ):
            return True
    return False


def _contains_control_or_space(value: str) -> bool:
    """Отклонить raw URL с ASCII или Unicode whitespace/control characters."""

    return any(char.isspace() or ord(char) < 32 or ord(char) == 127 for char in value)


def _is_local_or_internal_hostname(hostname: str) -> bool:
    """Проверить локальные, reserved и single-label DNS names без резолвинга."""

    return (
        not hostname
        or hostname in _LOCAL_HOSTNAMES
        or any(hostname.endswith(suffix) for suffix in _LOCAL_HOST_SUFFIXES)
        or "." not in hostname
    )


def _reject_non_public_literal_ip(hostname: str) -> None:
    """Отклонить literal IP из private/loopback/reserved и иных non-global ranges."""

    if "%" in hostname:
        raise ResearchInvalidRequestError()
    try:
        address = ipaddress.ip_address(hostname)
    except ValueError:
        return
    if not isinstance(address, (IPv4Address, IPv6Address)) or (
        not address.is_global
        or address.is_loopback
        or address.is_private
        or address.is_link_local
        or address.is_multicast
        or address.is_unspecified
        or address.is_reserved
    ):
        raise ResearchInvalidRequestError()


def _check_cancellation(cancellation: CancellationToken) -> bool:
    """Проверить token fail-closed, не подменяя его и не запуская callbacks."""

    checker = getattr(cancellation, "is_cancelled", None)
    if not callable(checker):
        raise ResearchInvalidRequestError()
    try:
        value = checker()
    except Exception:
        raise ResearchInvalidRequestError() from None
    if type(value) is not bool:
        raise ResearchInvalidRequestError()
    return value


def _validate_source(source: object, request: ResearchRequest) -> None:
    """Проверить normalized result после adapter call и не переписать его content."""

    if not isinstance(source, ResearchSource):
        raise ResearchMalformedResultError()
    if source.source_kind is not request.source_kind:
        raise ResearchMalformedResultError()
    try:
        _validate_public_uri(source.uri, source.source_kind)
    except ResearchInvalidRequestError:
        raise ResearchMalformedResultError() from None
    if not _has_explicit_offset(source.retrieved_at):
        raise ResearchMalformedResultError()
    if source.published_at is not None and not _has_explicit_offset(source.published_at):
        raise ResearchMalformedResultError()
    if not isinstance(source.backend, str) or not _SAFE_BACKEND.fullmatch(source.backend):
        raise ResearchMalformedResultError()
    if not isinstance(source.content, str) or not source.content.strip():
        raise ResearchMalformedResultError()
    try:
        content_bytes = source.content.encode("utf-8")
    except UnicodeEncodeError:
        raise ResearchMalformedResultError() from None
    if len(content_bytes) > request.max_bytes:
        raise ResearchContentTooLargeError()
    for value in (
        source.title,
        source.author,
        source.media_type,
        source.upstream_id,
    ):
        if value is not None and (
            not isinstance(value, str) or "\x00" in value or any(ord(char) < 32 for char in value)
        ):
            raise ResearchMalformedResultError()


def _has_explicit_offset(value: object) -> bool:
    """Принять только aware datetime с вычислимым явным UTC offset."""

    if not isinstance(value, datetime) or value.tzinfo is None:
        return False
    try:
        return value.utcoffset() is not None
    except TypeError, ValueError, OverflowError:
        return False


def _map_port_exception(error: Exception) -> ResearchError:
    """Скрыть upstream details и свести fake/future adapter ошибки к taxonomy."""

    if isinstance(error, ResearchError):
        error_types: dict[str, Callable[[], ResearchError]] = {
            ResearchErrorCode.INVALID_REQUEST.value: ResearchInvalidRequestError,
            ResearchErrorCode.CANCELLED.value: ResearchCancelledError,
            ResearchErrorCode.TIMEOUT.value: ResearchTimeoutError,
            ResearchErrorCode.BACKEND_UNAVAILABLE.value: ResearchBackendUnavailableError,
            ResearchErrorCode.UPSTREAM_FAILURE.value: ResearchUpstreamError,
            ResearchErrorCode.MALFORMED_RESULT.value: ResearchMalformedResultError,
            ResearchErrorCode.CONTENT_TOO_LARGE.value: ResearchContentTooLargeError,
        }
        error_type = error_types.get(error.code, ResearchUpstreamError)
        return error_type()
    if isinstance(error, TimeoutError):
        return ResearchTimeoutError()
    if isinstance(error, (ConnectionError, OSError)):
        return ResearchBackendUnavailableError()
    return ResearchUpstreamError()


__all__ = [
    "DEFAULT_MAX_BYTES",
    "DEFAULT_TIMEOUT_SECONDS",
    "MAX_MAX_BYTES",
    "MAX_TIMEOUT_SECONDS",
    "CancellationToken",
    "ExternalResearchPort",
    "ResearchBackendUnavailableError",
    "ResearchCancelledError",
    "ResearchContentTooLargeError",
    "ResearchError",
    "ResearchErrorCode",
    "ResearchGateway",
    "ResearchInvalidRequestError",
    "ResearchMalformedResultError",
    "ResearchRequest",
    "ResearchSource",
    "ResearchTimeoutError",
    "ResearchUpstreamError",
    "SourceKind",
]
