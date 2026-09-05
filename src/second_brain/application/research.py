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
MAX_SOURCE_URI_BYTES = 8 * 1024
MAX_PROVENANCE_FIELD_BYTES = 16 * 1024

_SAFE_BACKEND = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,63}\Z")
_NUMERIC_IPV4_LABEL = re.compile(r"(?:0[xX][0-9a-fA-F]+|[0-9]+)\Z")
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
class SourceProvenance:
    """Минимальная provider-neutral provenance без content и transport details."""

    uri: str
    source_kind: SourceKind
    retrieved_at: datetime
    published_at: datetime | None = None
    title: str | None = None
    author: str | None = None
    upstream_id: str | None = None

    def __post_init__(self) -> None:
        """Проверить bounded metadata до передачи в reviewed write boundary."""

        if type(self.source_kind) is not SourceKind:
            raise ValueError("source_kind must be a supported SourceKind")
        try:
            _validate_public_uri(self.uri, self.source_kind)
        except ResearchInvalidRequestError:
            raise ValueError("uri must be a valid public source URI") from None
        if not _has_explicit_offset(self.retrieved_at):
            raise ValueError("retrieved_at must include an explicit UTC offset")
        if self.published_at is not None and not _has_explicit_offset(self.published_at):
            raise ValueError("published_at must include an explicit UTC offset")
        for field, value in (
            ("title", self.title),
            ("author", self.author),
            ("upstream_id", self.upstream_id),
        ):
            _validate_provenance_text(value, field)

    @classmethod
    def from_research_source(cls, source: ResearchSource) -> SourceProvenance:
        """Извлечь только metadata из уже normalized ``ResearchSource``."""

        if not isinstance(source, ResearchSource):
            raise ValueError("source must be a ResearchSource")
        return cls(
            uri=source.uri,
            source_kind=source.source_kind,
            retrieved_at=source.retrieved_at,
            published_at=source.published_at,
            title=source.title,
            author=source.author,
            upstream_id=source.upstream_id,
        )


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
        if len(uri.encode("utf-8")) > MAX_SOURCE_URI_BYTES:
            raise ResearchInvalidRequestError()
    except UnicodeEncodeError:
        raise ResearchInvalidRequestError() from None
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
    if _is_local_or_internal_hostname(normalized_host) and not (
        source_kind is SourceKind.RSS and _is_ipv6_literal(normalized_host)
    ):
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


def _validate_provenance_text(value: object, field: str) -> None:
    """Проверить bounded scalar metadata без YAML control/newline injection."""

    if value is None:
        return
    if type(value) is not str:
        raise ValueError(f"{field} must be a string")
    if any(
        ord(char) < 32 or 0x7F <= ord(char) <= 0x9F or char in {"\u2028", "\u2029"}
        for char in value
    ):
        raise ValueError(f"{field} contains a control character")
    try:
        if len(value.encode("utf-8")) > MAX_PROVENANCE_FIELD_BYTES:
            raise ValueError(f"{field} exceeds its byte limit")
    except UnicodeEncodeError:
        raise ValueError(f"{field} is not valid UTF-8") from None


def _is_local_or_internal_hostname(hostname: str) -> bool:
    """Проверить локальные, reserved и single-label DNS names без резолвинга."""

    return (
        not hostname
        or hostname in _LOCAL_HOSTNAMES
        or any(hostname.endswith(suffix) for suffix in _LOCAL_HOST_SUFFIXES)
        or "." not in hostname
    )


def _is_ipv6_literal(hostname: str) -> bool:
    """Распознать IPv6 literal только для RSS public-IP validation path."""

    try:
        return isinstance(ipaddress.ip_address(hostname), IPv6Address)
    except ValueError:
        return False


def _reject_non_public_literal_ip(hostname: str) -> None:
    """Отклонить literal IP из private/loopback/reserved и иных non-global ranges."""

    if "%" in hostname:
        raise ResearchInvalidRequestError()
    try:
        address = ipaddress.ip_address(hostname)
    except ValueError:
        if _looks_like_numeric_ipv4(hostname):
            raise ResearchInvalidRequestError() from None
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


def _looks_like_numeric_ipv4(hostname: str) -> bool:
    """Распознать dotted decimal/octal/hex IPv4 aliases без DNS lookup."""

    labels = hostname.split(".")
    return 1 <= len(labels) <= 4 and all(
        _NUMERIC_IPV4_LABEL.fullmatch(label) is not None for label in labels
    )


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
    try:
        SourceProvenance.from_research_source(source)
    except ValueError:
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
    for value in (source.media_type,):
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
    "MAX_PROVENANCE_FIELD_BYTES",
    "MAX_SOURCE_URI_BYTES",
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
    "SourceProvenance",
]
