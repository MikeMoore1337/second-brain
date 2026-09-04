"""Изолированный zero-auth public GitHub REST adapter через фиксированный curl."""

from __future__ import annotations

import json
import math
import re
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import cast
from urllib.parse import urlsplit

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
    ProcessResult,
    ProcessRunner,
    _ProcessCancelled,
    _ProcessContentTooLarge,
    _ProcessExecutionError,
    _ProcessFailed,
    _ProcessTimedOut,
)

GITHUB_API_BASE_URL = "https://api.github.com"
GITHUB_API_VERSION = "2026-03-10"
GITHUB_BACKEND = "github-rest"
GITHUB_METADATA_ACCEPT = "application/vnd.github+json"
GITHUB_README_ACCEPT = "application/vnd.github.raw+json"
GITHUB_USER_AGENT = "Second-Brain-Public-Research-v1"
GITHUB_METADATA_MEDIA_TYPE = "text/plain"
GITHUB_README_MEDIA_TYPE = "text/markdown"

MAX_METADATA_BODY_BYTES = 256 * 1024
MAX_RESPONSE_HEADER_BYTES = 32 * 1024
MAX_METADATA_RESPONSE_BYTES = MAX_METADATA_BODY_BYTES + MAX_RESPONSE_HEADER_BYTES
MAX_RESPONSE_HEADER_LINES = 128
MAX_RESPONSE_HEADER_LINE_BYTES = 4 * 1024
MAX_METADATA_FIELD_BYTES = 16 * 1024
MAX_TOPICS = 100

_OWNER_SEGMENT = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,37}[A-Za-z0-9])?\Z")
_REPOSITORY_SEGMENT = re.compile(r"[A-Za-z0-9._-]{1,100}\Z")
_HTTP_STATUS_LINE = re.compile(rb"HTTP/[0-9]+(?:\.[0-9]+)?[ \t]+([0-9]{3})(?:[ \t].*)?\Z")
_HTTP_HEADER_NAME = re.compile(rb"[!#$%&'*+\-.^_`|~0-9A-Za-z]+\Z")
_MISSING = object()


@dataclass(frozen=True, slots=True)
class _RepositoryTarget:
    """Validated repository-root input and its safe API path segments."""

    uri: str
    owner: str
    repo: str


@dataclass(frozen=True, slots=True)
class _HttpResponse:
    """Minimal parsed response envelope; upstream headers are never published."""

    status_code: int
    body: bytes


@dataclass(frozen=True, slots=True)
class _RepositoryMetadata:
    """Only bounded metadata fields used by the normalized source."""

    full_name: str
    description: str
    default_branch: str
    language: str
    topics: tuple[str, ...]
    author: str | None
    upstream_id: str


@dataclass(frozen=True, slots=True)
class PublicGitHubAdapter:
    """Production port для одного public GitHub repository без authentication."""

    runner: ProcessRunner = field(default_factory=BoundedProcessRunner)
    clock: Callable[[], datetime] = field(default=lambda: datetime.now(UTC))
    monotonic: Callable[[], float] = field(default=time.monotonic)

    def read(
        self,
        request: ResearchRequest,
        *,
        cancellation: CancellationToken,
    ) -> ResearchSource:
        """Прочитать metadata и raw README максимум двумя serial GET."""

        if not isinstance(request, ResearchRequest):
            raise ResearchInvalidRequestError()
        if request.source_kind is not SourceKind.GITHUB:
            raise ResearchBackendUnavailableError()
        _raise_if_cancelled(cancellation)
        _validate_adapter_limits(request)
        target = _parse_repository_target(request.uri)
        deadline = _make_deadline(self.monotonic, request.timeout_seconds)

        metadata_result = _run_process(
            self.runner,
            _build_curl_argv(target, readme=False),
            timeout_seconds=_remaining(self.monotonic, deadline),
            max_stdout_bytes=MAX_METADATA_RESPONSE_BYTES,
            cancellation=cancellation,
        )
        _raise_if_cancelled(cancellation)
        _remaining(self.monotonic, deadline)
        metadata_response = _parse_http_response(metadata_result.stdout)
        _raise_if_cancelled(cancellation)
        _remaining(self.monotonic, deadline)
        if metadata_response.status_code != 200:
            raise ResearchUpstreamError()
        metadata = _parse_repository_metadata(metadata_response.body, target)
        _raise_if_cancelled(cancellation)
        _remaining(self.monotonic, deadline)

        _raise_if_cancelled(cancellation)
        _remaining(self.monotonic, deadline)

        readme_result = _run_process(
            self.runner,
            _build_curl_argv(target, readme=True),
            timeout_seconds=_remaining(self.monotonic, deadline),
            max_stdout_bytes=request.max_bytes + MAX_RESPONSE_HEADER_BYTES,
            cancellation=cancellation,
        )
        _raise_if_cancelled(cancellation)
        _remaining(self.monotonic, deadline)
        readme_response = _parse_http_response(readme_result.stdout)
        _raise_if_cancelled(cancellation)
        _remaining(self.monotonic, deadline)

        if readme_response.status_code == 404:
            readme: str | None = None
            media_type = GITHUB_METADATA_MEDIA_TYPE
        elif readme_response.status_code != 200:
            raise ResearchUpstreamError()
        else:
            if len(readme_response.body) > request.max_bytes:
                raise ResearchContentTooLargeError()
            readme = _normalize_readme(readme_response.body)
            media_type = GITHUB_README_MEDIA_TYPE

        _raise_if_cancelled(cancellation)
        _remaining(self.monotonic, deadline)
        content = _build_content(metadata, readme, request.max_bytes)
        _raise_if_cancelled(cancellation)
        _remaining(self.monotonic, deadline)
        try:
            retrieved_at = self.clock()
        except Exception:
            raise ResearchMalformedResultError() from None
        if not _has_explicit_offset(retrieved_at):
            raise ResearchMalformedResultError()
        _raise_if_cancelled(cancellation)
        _remaining(self.monotonic, deadline)
        return ResearchSource(
            uri=request.uri,
            source_kind=SourceKind.GITHUB,
            retrieved_at=retrieved_at,
            backend=GITHUB_BACKEND,
            content=content,
            title=metadata.full_name,
            author=metadata.author,
            media_type=media_type,
            upstream_id=metadata.upstream_id,
            published_at=None,
        )


def _validate_adapter_limits(request: ResearchRequest) -> None:
    """Не допустить unbounded direct-adapter invocation вне gateway."""

    if type(request.timeout_seconds) is not int or request.timeout_seconds <= 0:
        raise ResearchInvalidRequestError()
    if type(request.max_bytes) is not int or request.max_bytes <= 0:
        raise ResearchInvalidRequestError()


def _parse_repository_target(uri: object) -> _RepositoryTarget:
    """Принять только exact HTTPS GitHub repository-root URL."""

    if not isinstance(uri, str) or not uri or _contains_control_or_space(uri):
        raise ResearchInvalidRequestError()
    if "%" in uri or "\\" in uri or "?" in uri or "#" in uri:
        raise ResearchInvalidRequestError()
    try:
        parsed = urlsplit(uri)
        scheme = parsed.scheme.casefold()
        hostname = parsed.hostname
        port = parsed.port
    except ValueError, UnicodeError:
        raise ResearchInvalidRequestError() from None
    if scheme != "https" or not parsed.netloc or hostname is None:
        raise ResearchInvalidRequestError()
    if hostname.casefold() != "github.com":
        raise ResearchInvalidRequestError()
    if port is not None or parsed.username is not None or parsed.password is not None:
        raise ResearchInvalidRequestError()
    if "@" in parsed.netloc or parsed.path.endswith("/"):
        raise ResearchInvalidRequestError()
    segments = parsed.path.split("/")
    if len(segments) != 3 or segments[0] != "":
        raise ResearchInvalidRequestError()
    owner, repo = segments[1:]
    try:
        _validate_owner_segment(owner)
        _validate_repository_segment(repo)
    except ResearchMalformedResultError:
        raise ResearchInvalidRequestError() from None
    return _RepositoryTarget(uri=uri, owner=owner, repo=repo)


def _validate_owner_segment(value: object) -> str:
    """Проверить GitHub owner segment до использования в API path."""

    if not isinstance(value, str) or _OWNER_SEGMENT.fullmatch(value) is None:
        raise ResearchMalformedResultError()
    return value


def _validate_repository_segment(value: object) -> str:
    """Проверить bounded repository name, включая leading-dot names."""

    if not isinstance(value, str) or _REPOSITORY_SEGMENT.fullmatch(value) is None:
        raise ResearchMalformedResultError()
    if value in {".", ".."} or value.casefold().endswith(".git"):
        raise ResearchMalformedResultError()
    return value


def _build_curl_argv(target: _RepositoryTarget, *, readme: bool) -> tuple[str, ...]:
    """Собрать закрытый GET argv с fixed host, headers и no redirects."""

    api_path = f"/repos/{target.owner}/{target.repo}"
    if readme:
        api_path += "/readme"
    accept = GITHUB_README_ACCEPT if readme else GITHUB_METADATA_ACCEPT
    return (
        CURL_EXECUTABLE,
        "--disable",
        "--silent",
        "--show-error",
        "--noproxy",
        "*",
        "--proto",
        "=https",
        "--max-redirs",
        "0",
        "--request",
        "GET",
        "--include",
        "--header",
        f"Accept: {accept}",
        "--header",
        f"X-GitHub-Api-Version: {GITHUB_API_VERSION}",
        "--header",
        f"User-Agent: {GITHUB_USER_AGENT}",
        "--url",
        f"{GITHUB_API_BASE_URL}{api_path}",
    )


def _run_process(
    runner: ProcessRunner,
    argv: Sequence[str],
    *,
    timeout_seconds: float,
    max_stdout_bytes: int,
    cancellation: CancellationToken,
) -> ProcessResult:
    """Запустить только closed argv и скрыть process/upstream diagnostics."""

    try:
        result = runner.run(
            argv,
            timeout_seconds=timeout_seconds,
            max_stdout_bytes=max_stdout_bytes,
            cancellation=cancellation,
        )
    except _ProcessCancelled:
        raise ResearchCancelledError() from None
    except _ProcessTimedOut:
        raise ResearchTimeoutError() from None
    except _ProcessContentTooLarge:
        raise ResearchContentTooLargeError() from None
    except _ProcessFailed:
        raise ResearchUpstreamError() from None
    except TimeoutError:
        raise ResearchTimeoutError() from None
    except _ProcessExecutionError:
        raise ResearchBackendUnavailableError() from None
    except OSError:
        raise ResearchBackendUnavailableError() from None

    if not isinstance(result, ProcessResult):
        raise ResearchMalformedResultError()
    if result.returncode != 0:
        raise ResearchUpstreamError()
    if not isinstance(result.stdout, bytes):
        raise ResearchMalformedResultError()
    if len(result.stdout) > max_stdout_bytes:
        raise ResearchContentTooLargeError()
    return result


def _parse_http_response(raw_bytes: bytes) -> _HttpResponse:
    """Распарсить один bounded status/header block и оставить body bytes."""

    if not isinstance(raw_bytes, bytes):
        raise ResearchMalformedResultError()
    separator = _find_header_separator(raw_bytes)
    if separator is None:
        raise ResearchMalformedResultError()
    header_end, separator_size = separator
    header_bytes = raw_bytes[:header_end]
    body = raw_bytes[header_end + separator_size :]
    if len(header_bytes) > MAX_RESPONSE_HEADER_BYTES:
        raise ResearchContentTooLargeError()
    lines = _split_header_lines(header_bytes)
    if not lines or len(lines) > MAX_RESPONSE_HEADER_LINES:
        raise ResearchMalformedResultError()
    if len(lines[0]) > MAX_RESPONSE_HEADER_LINE_BYTES:
        raise ResearchMalformedResultError()
    if any((byte < 32 and byte != 9) or byte == 127 for byte in lines[0]):
        raise ResearchMalformedResultError()
    status_match = _HTTP_STATUS_LINE.fullmatch(lines[0])
    if status_match is None:
        raise ResearchMalformedResultError()
    try:
        status_code = int(status_match.group(1))
    except TypeError, ValueError:
        raise ResearchMalformedResultError() from None
    if not 100 <= status_code <= 599:
        raise ResearchMalformedResultError()
    for line in lines[1:]:
        if len(line) > MAX_RESPONSE_HEADER_LINE_BYTES or not _is_safe_header_line(line):
            raise ResearchMalformedResultError()
    return _HttpResponse(status_code=status_code, body=body)


def _find_header_separator(raw_bytes: bytes) -> tuple[int, int] | None:
    """Найти первый CRLF или LF header terminator, не сканируя body дальше."""

    crlf_index = raw_bytes.find(b"\r\n\r\n")
    lf_index = raw_bytes.find(b"\n\n")
    candidates = [
        (index, 4 if index == crlf_index else 2) for index in (crlf_index, lf_index) if index >= 0
    ]
    return min(candidates) if candidates else None


def _split_header_lines(header_bytes: bytes) -> list[bytes]:
    """Разделить только CRLF или только LF headers; mixed bare CR malformed."""

    if b"\r" in header_bytes.replace(b"\r\n", b""):
        raise ResearchMalformedResultError()
    return header_bytes.replace(b"\r\n", b"\n").split(b"\n")


def _is_safe_header_line(line: bytes) -> bool:
    """Проверить форму header без публикации его name/value и без URL follow."""

    if b"\x00" in line:
        return False
    name, separator, value = line.partition(b":")
    if not separator or _HTTP_HEADER_NAME.fullmatch(name) is None:
        return False
    return not any((byte < 32 and byte != 9) or byte == 127 for byte in value)


def _parse_repository_metadata(
    raw_bytes: bytes,
    target: _RepositoryTarget,
) -> _RepositoryMetadata:
    """Проверить bounded public metadata и response identity."""

    if not isinstance(raw_bytes, bytes):
        raise ResearchMalformedResultError()
    if len(raw_bytes) > MAX_METADATA_BODY_BYTES:
        raise ResearchContentTooLargeError()
    try:
        payload = json.loads(raw_bytes.decode("utf-8"))
    except UnicodeDecodeError, json.JSONDecodeError, RecursionError, ValueError:
        raise ResearchMalformedResultError() from None
    if not isinstance(payload, dict):
        raise ResearchMalformedResultError()
    metadata = cast(Mapping[str, object], payload)

    private = metadata.get("private", _MISSING)
    if type(private) is not bool or private:
        raise ResearchMalformedResultError()
    repository_id = metadata.get("id", _MISSING)
    if type(repository_id) is not int or repository_id <= 0:
        raise ResearchMalformedResultError()

    full_name = metadata.get("full_name", _MISSING)
    if not isinstance(full_name, str):
        raise ResearchMalformedResultError()
    canonical_owner, canonical_repo = _parse_metadata_identity(full_name)
    if not _ascii_equal(canonical_owner, target.owner) or not _ascii_equal(
        canonical_repo, target.repo
    ):
        raise ResearchMalformedResultError()

    author = _parse_metadata_author(metadata, target.owner)
    description = _metadata_scalar(metadata, "description")
    default_branch = _metadata_scalar(metadata, "default_branch")
    language = _metadata_scalar(metadata, "language")
    topics = _metadata_topics(metadata)
    return _RepositoryMetadata(
        full_name=full_name,
        description=description,
        default_branch=default_branch,
        language=language,
        topics=topics,
        author=author,
        upstream_id=str(repository_id),
    )


def _parse_metadata_identity(full_name: str) -> tuple[str, str]:
    """Проверить canonical full_name как ровно два safe GitHub segments."""

    if full_name.count("/") != 1:
        raise ResearchMalformedResultError()
    owner, repo = full_name.split("/")
    try:
        _validate_owner_segment(owner)
        _validate_repository_segment(repo)
    except ResearchMalformedResultError:
        raise ResearchMalformedResultError() from None
    return owner, repo


def _parse_metadata_author(
    metadata: Mapping[str, object],
    target_owner: str,
) -> str | None:
    """Проверить optional owner.login и сохранить canonical casing."""

    owner = metadata.get("owner", _MISSING)
    if owner is _MISSING:
        return None
    if not isinstance(owner, Mapping):
        raise ResearchMalformedResultError()
    login = owner.get("login", _MISSING)
    if login is _MISSING:
        return None
    if not isinstance(login, str):
        raise ResearchMalformedResultError()
    try:
        _validate_owner_segment(login)
    except ResearchMalformedResultError:
        raise ResearchMalformedResultError() from None
    if not _ascii_equal(login, target_owner):
        raise ResearchMalformedResultError()
    return login


def _metadata_scalar(metadata: Mapping[str, object], key: str) -> str:
    """Принять bounded nullable scalar без line/control injection."""

    value = metadata.get(key, _MISSING)
    if value is _MISSING or value is None:
        return ""
    if not isinstance(value, str):
        raise ResearchMalformedResultError()
    return _validate_metadata_text(value)


def _metadata_topics(metadata: Mapping[str, object]) -> tuple[str, ...]:
    """Принять bounded topic sequence и отсортировать её детерминированно."""

    value = metadata.get("topics", _MISSING)
    if value is _MISSING:
        return ()
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        raise ResearchMalformedResultError()
    if len(value) > MAX_TOPICS:
        raise ResearchContentTooLargeError()
    topics = tuple(_validate_metadata_text(item) for item in value)
    return tuple(sorted(topics, key=lambda item: (item.casefold(), item)))


def _validate_metadata_text(value: object) -> str:
    """Проверить untrusted metadata scalar и его encoded byte bound."""

    if not isinstance(value, str) or any(ord(char) < 32 or ord(char) == 127 for char in value):
        raise ResearchMalformedResultError()
    try:
        if len(value.encode("utf-8")) > MAX_METADATA_FIELD_BYTES:
            raise ResearchContentTooLargeError()
    except UnicodeEncodeError:
        raise ResearchMalformedResultError() from None
    return value


def _normalize_readme(raw_bytes: bytes) -> str:
    """Strictly decode bounded raw README and normalize only line endings."""

    try:
        text = raw_bytes.decode("utf-8")
    except AttributeError, UnicodeDecodeError:
        raise ResearchMalformedResultError() from None
    return text.replace("\r\n", "\n").replace("\r", "\n")


def _build_content(
    metadata: _RepositoryMetadata,
    readme: str | None,
    max_bytes: int,
) -> str:
    """Собрать fixed metadata block и проверить итоговый UTF-8 size."""

    topics = ", ".join(metadata.topics)
    content = "\n".join(
        [
            f"Repository: {metadata.full_name}",
            f"Description: {metadata.description}",
            f"Default branch: {metadata.default_branch}",
            f"Language: {metadata.language}",
            f"Topics: {topics}",
            "README:",
            "",
            readme if readme is not None else "(отсутствует)",
        ]
    )
    try:
        if len(content.encode("utf-8")) > max_bytes:
            raise ResearchContentTooLargeError()
    except UnicodeEncodeError:
        raise ResearchMalformedResultError() from None
    return content


def _ascii_equal(left: str, right: str) -> bool:
    """Сравнить уже ASCII-validated identity segments без Unicode folding."""

    return left.casefold() == right.casefold()


def _make_deadline(monotonic: Callable[[], float], timeout_seconds: int) -> float:
    """Создать общий deadline metadata + README + normalization workflow."""

    try:
        started_at = monotonic()
    except Exception:
        raise ResearchTimeoutError() from None
    if isinstance(started_at, bool) or not isinstance(started_at, (int, float)):
        raise ResearchTimeoutError()
    started = float(started_at)
    if not math.isfinite(started):
        raise ResearchTimeoutError()
    return started + timeout_seconds


def _remaining(monotonic: Callable[[], float], deadline: float) -> float:
    """Вернуть остаток общего deadline или безопасно сообщить timeout."""

    try:
        current = monotonic()
    except Exception:
        raise ResearchTimeoutError() from None
    if isinstance(current, bool) or not isinstance(current, (int, float)):
        raise ResearchTimeoutError()
    value = float(current)
    if not math.isfinite(value):
        raise ResearchTimeoutError()
    remaining = deadline - value
    if remaining <= 0 or not math.isfinite(remaining):
        raise ResearchTimeoutError()
    return remaining


def _raise_if_cancelled(cancellation: CancellationToken) -> None:
    """Проверить cancellation fail-closed на каждой workflow boundary."""

    checker = getattr(cancellation, "is_cancelled", None)
    if not callable(checker):
        raise ResearchInvalidRequestError()
    try:
        cancelled = checker()
    except Exception:
        raise ResearchInvalidRequestError() from None
    if type(cancelled) is not bool:
        raise ResearchInvalidRequestError()
    if cancelled:
        raise ResearchCancelledError()


def _has_explicit_offset(value: object) -> bool:
    """Принять только aware datetime с вычислимым явным UTC offset."""

    if not isinstance(value, datetime) or value.tzinfo is None:
        return False
    try:
        return value.utcoffset() is not None
    except TypeError, ValueError, OverflowError:
        return False


def _contains_control_or_space(value: str) -> bool:
    """Отклонить raw URI с whitespace/control characters."""

    return any(char.isspace() or ord(char) < 32 or ord(char) == 127 for char in value)


__all__ = [
    "CURL_EXECUTABLE",
    "GITHUB_API_BASE_URL",
    "GITHUB_API_VERSION",
    "GITHUB_BACKEND",
    "GITHUB_METADATA_ACCEPT",
    "GITHUB_METADATA_MEDIA_TYPE",
    "GITHUB_README_ACCEPT",
    "GITHUB_README_MEDIA_TYPE",
    "GITHUB_USER_AGENT",
    "MAX_METADATA_BODY_BYTES",
    "MAX_METADATA_FIELD_BYTES",
    "MAX_METADATA_RESPONSE_BYTES",
    "MAX_RESPONSE_HEADER_BYTES",
    "MAX_RESPONSE_HEADER_LINES",
    "MAX_RESPONSE_HEADER_LINE_BYTES",
    "BoundedProcessRunner",
    "PublicGitHubAdapter",
]
