"""Ограниченный public RSS/Atom adapter с direct egress и DNS pinning."""

from __future__ import annotations

import ipaddress
import socket
import threading
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from html.parser import HTMLParser
from ipaddress import IPv4Address, IPv6Address
from typing import Protocol
from urllib.parse import urlsplit

import feedparser  # type: ignore[import-untyped]

from second_brain.application.ports import (
    CancellationToken,
    ResearchBackendUnavailableError,
    ResearchCancelledError,
    ResearchContentTooLargeError,
    ResearchInvalidRequestError,
    ResearchMalformedResultError,
    ResearchTimeoutError,
    ResearchUpstreamError,
)
from second_brain.application.research import ResearchRequest, ResearchSource, SourceKind

from .process import (
    CURL_EXECUTABLE,
    BoundedProcessRunner,
    ProcessRunner,
    _ProcessCancelled,
    _ProcessContentTooLarge,
    _ProcessExecutionError,
    _ProcessTimedOut,
)

RSS_BACKEND = "feedparser"
RSS_MEDIA_TYPE = "application/rss+xml"
ATOM_MEDIA_TYPE = "application/atom+xml"

_DNS_POLL_INTERVAL_SECONDS = 0.01
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
_NUMERIC_IPV4_LABEL = frozenset("0123456789abcdefABCDEFxX")
_BLOCK_TAGS = frozenset(
    {
        "address",
        "article",
        "aside",
        "blockquote",
        "br",
        "div",
        "dl",
        "dt",
        "dd",
        "fieldset",
        "figcaption",
        "figure",
        "footer",
        "form",
        "h1",
        "h2",
        "h3",
        "h4",
        "h5",
        "h6",
        "header",
        "hr",
        "li",
        "main",
        "nav",
        "ol",
        "p",
        "pre",
        "section",
        "table",
        "td",
        "th",
        "tr",
        "ul",
    }
)
_IGNORED_TAGS = frozenset(
    {
        "canvas",
        "embed",
        "iframe",
        "math",
        "noscript",
        "object",
        "script",
        "style",
        "svg",
        "template",
    }
)


class DnsResolver(Protocol):
    """Минимальная injectable граница adapter-level DNS resolution."""

    def resolve(self, hostname: str, port: int) -> Sequence[str]:
        """Вернуть все address candidates, найденные для hostname."""


class SocketDnsResolver:
    """Системный resolver без пользовательского DNS/config override."""

    def resolve(self, hostname: str, port: int) -> Sequence[str]:
        """Разрешить hostname только в TCP address candidates."""

        addresses: list[str] = []
        for _family, _socktype, _protocol, _canonname, sockaddr in socket.getaddrinfo(
            hostname,
            port,
            type=socket.SOCK_STREAM,
        ):
            address = sockaddr[0]
            if not isinstance(address, str):
                raise OSError()
            addresses.append(address)
        return tuple(addresses)


class _DnsResolutionError(RuntimeError):
    """Внутренняя ошибка DNS без публикации resolver details."""


class _DnsCancelled(_DnsResolutionError):
    """DNS operation остановлена cancellation token."""


class _DnsTimedOut(_DnsResolutionError):
    """DNS operation превысила общий request deadline."""


class _DnsUnavailable(_DnsResolutionError):
    """Системный resolver не вернул usable result."""


class _DnsPolicyRejected(_DnsResolutionError):
    """Хотя бы один DNS candidate нарушил public-IP policy."""


class _FeedNormalizationError(RuntimeError):
    """Feed нельзя безопасно привести к normalized source."""


@dataclass(frozen=True, slots=True)
class _RssTarget:
    """Разобранный target с сохранённым исходным URI для curl/TLS."""

    uri: str
    scheme: str
    hostname: str
    resolver_hostname: str
    port: int
    literal_ip: IPv4Address | IPv6Address | None = None


@dataclass(frozen=True, slots=True)
class PublicRssAdapter:
    """Production RSS port с bounded direct HTTP(S) egress."""

    resolver: DnsResolver = field(default_factory=SocketDnsResolver)
    runner: ProcessRunner = field(default_factory=BoundedProcessRunner)
    clock: Callable[[], datetime] = field(default=lambda: datetime.now(UTC))

    def read(
        self,
        request: ResearchRequest,
        *,
        cancellation: CancellationToken,
    ) -> ResearchSource:
        """Прочитать только RSS/Atom request и вернуть untrusted normalized text."""

        if request.source_kind is not SourceKind.RSS:
            raise ResearchBackendUnavailableError()
        if cancellation.is_cancelled():
            raise ResearchCancelledError()

        target = _parse_rss_target(request.uri)
        deadline = time.monotonic() + request.timeout_seconds
        try:
            pinned_ip = self._resolve_pinned_ip(target, deadline, cancellation)
        except _DnsCancelled:
            raise ResearchCancelledError() from None
        except _DnsTimedOut:
            raise ResearchTimeoutError() from None
        except _DnsPolicyRejected:
            raise ResearchInvalidRequestError() from None
        except _DnsUnavailable:
            raise ResearchBackendUnavailableError() from None

        if cancellation.is_cancelled():
            raise ResearchCancelledError()
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise ResearchTimeoutError()

        try:
            result = self.runner.run(
                _build_curl_argv(target, pinned_ip),
                timeout_seconds=remaining,
                max_stdout_bytes=request.max_bytes,
                cancellation=cancellation,
            )
        except _ProcessCancelled:
            raise ResearchCancelledError() from None
        except _ProcessTimedOut:
            raise ResearchTimeoutError() from None
        except _ProcessContentTooLarge:
            raise ResearchContentTooLargeError() from None
        except _ProcessExecutionError:
            raise ResearchBackendUnavailableError() from None
        except OSError:
            raise ResearchBackendUnavailableError() from None

        if cancellation.is_cancelled():
            raise ResearchCancelledError()
        if result.returncode != 0:
            raise ResearchUpstreamError()
        if not isinstance(result.stdout, bytes):
            raise ResearchMalformedResultError()
        if len(result.stdout) > request.max_bytes:
            raise ResearchContentTooLargeError()

        try:
            parsed = feedparser.parse(result.stdout)
            source = _normalize_feed(parsed, request, self.clock)
        except ResearchContentTooLargeError:
            raise
        except ResearchMalformedResultError, _FeedNormalizationError:
            raise ResearchMalformedResultError() from None
        except Exception:
            raise ResearchMalformedResultError() from None
        return source

    def _resolve_pinned_ip(
        self,
        target: _RssTarget,
        deadline: float,
        cancellation: CancellationToken,
    ) -> IPv4Address | IPv6Address:
        """Проверить все candidates и выбрать один public address детерминированно."""

        if target.literal_ip is not None:
            return target.literal_ip
        raw_addresses = _resolve_with_deadline(
            self.resolver,
            target.resolver_hostname,
            target.port,
            deadline,
            cancellation,
        )
        return _validated_pinned_ip(raw_addresses)


def _parse_rss_target(uri: str) -> _RssTarget:
    """Разобрать только HTTP(S) target, не ослабляя gateway policy."""

    if not isinstance(uri, str) or not uri or _contains_control_or_space(uri):
        raise ResearchInvalidRequestError()
    try:
        parsed = urlsplit(uri)
        scheme = parsed.scheme.casefold()
        hostname = parsed.hostname
        port = parsed.port
    except ValueError:
        raise ResearchInvalidRequestError() from None
    if scheme not in {"http", "https"} or not parsed.netloc or not hostname:
        raise ResearchInvalidRequestError()
    if parsed.username is not None or parsed.password is not None or "@" in parsed.netloc:
        raise ResearchInvalidRequestError()
    if "%" in hostname:
        raise ResearchInvalidRequestError()

    normalized_hostname = hostname.rstrip(".").casefold()
    if not normalized_hostname:
        raise ResearchInvalidRequestError()
    literal_ip = _parse_literal_ip(normalized_hostname)
    if literal_ip is not None:
        _require_public_ip(literal_ip)
        pinned_hostname = (
            f"[{literal_ip}]" if isinstance(literal_ip, IPv6Address) else str(literal_ip)
        )
        return _RssTarget(
            uri=uri,
            scheme=scheme,
            hostname=pinned_hostname,
            resolver_hostname=pinned_hostname,
            port=port or _default_port(scheme),
            literal_ip=literal_ip,
        )

    if _is_local_or_internal_hostname(normalized_hostname) or _looks_like_numeric_ipv4(
        normalized_hostname
    ):
        raise ResearchInvalidRequestError()
    try:
        resolver_hostname = normalized_hostname.encode("idna").decode("ascii").casefold()
        pinned_hostname = hostname.encode("idna").decode("ascii").casefold()
    except UnicodeError:
        raise ResearchInvalidRequestError() from None
    return _RssTarget(
        uri=uri,
        scheme=scheme,
        hostname=pinned_hostname,
        resolver_hostname=resolver_hostname,
        port=port or _default_port(scheme),
    )


def _default_port(scheme: str) -> int:
    """Вернуть только стандартный HTTP(S) port."""

    return 443 if scheme == "https" else 80


def _parse_literal_ip(hostname: str) -> IPv4Address | IPv6Address | None:
    """Распознать literal IP без принятия zone-id/mapped forms."""

    try:
        address = ipaddress.ip_address(hostname)
    except ValueError:
        return None
    if isinstance(address, IPv6Address) and address.ipv4_mapped is not None:
        raise ResearchInvalidRequestError()
    return address


def _require_public_ip(address: IPv4Address | IPv6Address) -> None:
    """Отклонить literal IP, не являющийся public global address."""

    if not _is_safe_public_ip(address):
        raise ResearchInvalidRequestError()


def _is_safe_public_ip(address: IPv4Address | IPv6Address) -> bool:
    """Проверить весь запретный IP-класс, а не только private flag."""

    return (
        address.is_global
        and not address.is_loopback
        and not address.is_private
        and not address.is_link_local
        and not address.is_multicast
        and not address.is_unspecified
        and not address.is_reserved
        and not (isinstance(address, IPv6Address) and address.ipv4_mapped is not None)
    )


def _validated_pinned_ip(raw_addresses: object) -> IPv4Address | IPv6Address:
    """Нормализовать все DNS candidates и fail closed на первом unsafe address."""

    if not isinstance(raw_addresses, Sequence) or isinstance(
        raw_addresses, (str, bytes, bytearray)
    ):
        raise _DnsPolicyRejected()
    addresses: set[IPv4Address | IPv6Address] = set()
    for raw_address in raw_addresses:
        if not isinstance(raw_address, str) or "%" in raw_address:
            raise _DnsPolicyRejected()
        try:
            address = ipaddress.ip_address(raw_address)
        except ValueError:
            raise _DnsPolicyRejected() from None
        if isinstance(address, IPv6Address) and address.ipv4_mapped is not None:
            raise _DnsPolicyRejected()
        if not isinstance(address, (IPv4Address, IPv6Address)) or not _is_safe_public_ip(address):
            raise _DnsPolicyRejected()
        addresses.add(address)
    if not addresses:
        raise _DnsUnavailable()
    return min(addresses, key=lambda address: (address.version, int(address)))


def _resolve_with_deadline(
    resolver: DnsResolver,
    hostname: str,
    port: int,
    deadline: float,
    cancellation: CancellationToken,
) -> object:
    """Ограничить неотменяемый stdlib DNS call daemon worker-ом."""

    result: list[object] = []
    error: list[Exception] = []

    def resolve() -> None:
        try:
            result.append(resolver.resolve(hostname, port))
        except Exception as exc:
            error.append(exc)

    thread = threading.Thread(
        target=resolve,
        name="second-brain-research-dns",
        daemon=True,
    )
    thread.start()
    while thread.is_alive():
        if cancellation.is_cancelled():
            raise _DnsCancelled()
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise _DnsTimedOut()
        thread.join(min(_DNS_POLL_INTERVAL_SECONDS, remaining))

    if cancellation.is_cancelled():
        raise _DnsCancelled()
    if time.monotonic() >= deadline:
        raise _DnsTimedOut()
    if error:
        if isinstance(error[0], TimeoutError):
            raise _DnsTimedOut() from None
        raise _DnsUnavailable() from None
    if not result:
        raise _DnsUnavailable()
    return result[0]


def _build_curl_argv(
    target: _RssTarget,
    pinned_ip: IPv4Address | IPv6Address,
) -> tuple[str, ...]:
    """Собрать закрытый curl argv с одним уже validated --resolve address."""

    resolve_address = f"[{pinned_ip}]" if isinstance(pinned_ip, IPv6Address) else str(pinned_ip)
    resolve_value = f"{target.hostname}:{target.port}:{resolve_address}"
    return (
        CURL_EXECUTABLE,
        "--disable",
        "--silent",
        "--fail",
        "--noproxy",
        "*",
        "--proto",
        f"={target.scheme}",
        "--max-redirs",
        "0",
        "--resolve",
        resolve_value,
        "--url",
        target.uri,
    )


def _normalize_feed(
    parsed: object,
    request: ResearchRequest,
    clock: Callable[[], datetime],
) -> ResearchSource:
    """Преобразовать только уже parsed bytes в один normalized ResearchSource."""

    if not isinstance(parsed, Mapping) or _mapping_value(parsed, "bozo"):
        raise _FeedNormalizationError()
    version = _single_line_text(_mapping_value(parsed, "version")).casefold()
    if version in {"rss20", "rss2"}:
        media_type = RSS_MEDIA_TYPE
    elif version.startswith("atom"):
        media_type = ATOM_MEDIA_TYPE
    else:
        raise _FeedNormalizationError()

    feed_object = _mapping_value(parsed, "feed")
    if not isinstance(feed_object, Mapping):
        raise _FeedNormalizationError()
    entries_object = _mapping_value(parsed, "entries")
    if entries_object is None:
        entries: tuple[object, ...] = ()
    elif isinstance(entries_object, Sequence) and not isinstance(
        entries_object, (str, bytes, bytearray)
    ):
        entries = tuple(entries_object)
    else:
        raise _FeedNormalizationError()

    feed_title = _single_line_text(_mapping_value(feed_object, "title")) or None
    feed_author = _feed_author(feed_object)
    content = _render_feed_content(feed_title, entries)
    if not content:
        raise _FeedNormalizationError()
    try:
        if len(content.encode("utf-8")) > request.max_bytes:
            raise ResearchContentTooLargeError()
    except UnicodeEncodeError:
        raise _FeedNormalizationError() from None

    retrieved_at = clock()
    if not _has_explicit_offset(retrieved_at):
        raise _FeedNormalizationError()
    return ResearchSource(
        uri=request.uri,
        source_kind=SourceKind.RSS,
        retrieved_at=retrieved_at,
        backend=RSS_BACKEND,
        content=content,
        title=feed_title,
        author=feed_author,
        media_type=media_type,
    )


def _feed_author(feed: Mapping[object, object]) -> str | None:
    """Взять только безопасный feed author/publisher text, без credentials."""

    for key in ("author", "publisher"):
        value = _single_line_text(_mapping_value(feed, key))
        if value:
            return value
    author_detail = _mapping_value(feed, "author_detail")
    if isinstance(author_detail, Mapping):
        value = _single_line_text(_mapping_value(author_detail, "name"))
        if value:
            return value
    return None


def _render_feed_content(feed_title: str | None, entries: Sequence[object]) -> str:
    """Сохранить feed order и убрать HTML/script из entry payload."""

    sections: list[str] = []
    if feed_title:
        sections.append(f"# {feed_title}")
    for entry_object in entries:
        if not isinstance(entry_object, Mapping):
            raise _FeedNormalizationError()
        title = _single_line_text(_mapping_value(entry_object, "title")) or "Entry"
        lines = [f"## {title}"]
        published = _entry_datetime(entry_object)
        if published is not None:
            lines.append(f"Published: {published.isoformat()}")
        link = _single_line_text(_mapping_value(entry_object, "link"))
        if link:
            lines.append(f"Link: {link}")
        body = _entry_body(entry_object)
        if body:
            lines.extend(("", body))
        sections.append("\n".join(lines))
    return "\n\n".join(sections).strip()


def _entry_body(entry: Mapping[object, object]) -> str:
    """Выбрать первый доступный summary/content без загрузки ресурсов."""

    content_object = _mapping_value(entry, "content")
    if isinstance(content_object, str):
        body = _multiline_text(content_object)
        if body:
            return body
    elif isinstance(content_object, Sequence) and not isinstance(
        content_object, (bytes, bytearray)
    ):
        for value_object in content_object:
            if not isinstance(value_object, Mapping):
                continue
            body = _multiline_text(_mapping_value(value_object, "value"))
            if body:
                return body
    for key in ("summary", "description"):
        body = _multiline_text(_mapping_value(entry, key))
        if body:
            return body
    return ""


def _entry_datetime(entry: Mapping[object, object]) -> datetime | None:
    """Использовать только parser-provided structured time, без timezone guess."""

    for key in ("published_parsed", "updated_parsed"):
        value = _mapping_value(entry, key)
        if not isinstance(value, time.struct_time) or len(value) < 6:
            continue
        try:
            return datetime(*value[:6], tzinfo=UTC)
        except TypeError, ValueError, OverflowError:
            continue
    return None


def _mapping_value(mapping: Mapping[object, object], key: str) -> object:
    """Безопасно получить одно поле FeedParserDict без dynamic attribute access."""

    return mapping.get(key)


def _single_line_text(value: object) -> str:
    """Извлечь HTML-safe single-line text из внешнего поля."""

    if not isinstance(value, str):
        if isinstance(value, Mapping):
            nested = _mapping_value(value, "value") or _mapping_value(value, "name")
            if not isinstance(nested, str):
                return ""
            value = nested
        else:
            return ""
    return " ".join(_plain_text(value).split())


def _multiline_text(value: object) -> str:
    """Извлечь plain text с детерминированными paragraph line breaks."""

    if not isinstance(value, str):
        return ""
    text = _plain_text(value)
    lines = [line.strip() for line in text.replace("\r\n", "\n").replace("\r", "\n").split("\n")]
    normalized: list[str] = []
    blank = False
    for line in lines:
        if line:
            normalized.append(line)
            blank = False
        elif normalized and not blank:
            normalized.append("")
            blank = True
    if normalized and normalized[-1] == "":
        normalized.pop()
    return "\n".join(normalized).strip()


class _SafeHTMLTextParser(HTMLParser):
    """HTMLParser-only sanitizer: tags are data, scripts/resources are ignored."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._ignored_depth = 0
        self._parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        del attrs
        normalized_tag = tag.casefold()
        if normalized_tag in _IGNORED_TAGS:
            self._ignored_depth += 1
            return
        if self._ignored_depth == 0 and normalized_tag in _BLOCK_TAGS:
            self._newline()

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        del attrs
        if self._ignored_depth == 0 and tag.casefold() in _BLOCK_TAGS:
            self._newline()

    def handle_endtag(self, tag: str) -> None:
        normalized_tag = tag.casefold()
        if normalized_tag in _IGNORED_TAGS and self._ignored_depth:
            self._ignored_depth -= 1
            return
        if self._ignored_depth == 0 and normalized_tag in _BLOCK_TAGS:
            self._newline()

    def handle_data(self, data: str) -> None:
        if self._ignored_depth == 0:
            self._parts.append(data)

    def text(self) -> str:
        """Вернуть собранный text; этот parser не делает network/I/O."""

        return "".join(self._parts)

    def _newline(self) -> None:
        if self._parts and not self._parts[-1].endswith("\n"):
            self._parts.append("\n")


def _plain_text(value: str) -> str:
    """Декодировать HTML entities и удалить control chars без execution."""

    parser = _SafeHTMLTextParser()
    try:
        parser.feed(value)
        parser.close()
    except AssertionError, ValueError:
        raise _FeedNormalizationError() from None
    return "".join(
        char
        for char in parser.text()
        if char in {"\n", "\t"} or (ord(char) >= 32 and ord(char) != 127)
    )


def _has_explicit_offset(value: object) -> bool:
    """Принять только aware datetime с вычислимым explicit UTC offset."""

    if not isinstance(value, datetime) or value.tzinfo is None:
        return False
    try:
        return value.utcoffset() is not None
    except TypeError, ValueError, OverflowError:
        return False


def _contains_control_or_space(value: str) -> bool:
    """Отклонить raw URI с whitespace/control characters."""

    return any(char.isspace() or ord(char) < 32 or ord(char) == 127 for char in value)


def _is_local_or_internal_hostname(hostname: str) -> bool:
    """Синхронно отфильтровать очевидные local/internal DNS names."""

    return (
        hostname in _LOCAL_HOSTNAMES
        or any(hostname.endswith(suffix) for suffix in _LOCAL_HOST_SUFFIXES)
        or "." not in hostname
    )


def _looks_like_numeric_ipv4(hostname: str) -> bool:
    """Отфильтровать numeric IPv4 aliases, которые не всегда понимает ipaddress."""

    labels = hostname.split(".")
    return 1 <= len(labels) <= 4 and all(
        label
        and all(char in _NUMERIC_IPV4_LABEL for char in label)
        and any(char.isdigit() for char in label)
        for label in labels
    )


__all__ = [
    "ATOM_MEDIA_TYPE",
    "RSS_BACKEND",
    "RSS_MEDIA_TYPE",
    "DnsResolver",
    "PublicRssAdapter",
    "SocketDnsResolver",
]
