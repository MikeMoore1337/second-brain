"""Изолированный public WEB adapter через фиксированный Jina Reader и curl."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime

from second_brain.application.ports import (
    CancellationToken,
    ResearchBackendUnavailableError,
    ResearchCancelledError,
    ResearchContentTooLargeError,
    ResearchMalformedResultError,
    ResearchTimeoutError,
    ResearchUpstreamError,
)
from second_brain.application.research import ResearchRequest, ResearchSource, SourceKind

from .process import (
    CURL_EXECUTABLE,
    MAX_STDERR_BYTES,
    BoundedProcessRunner,
    ProcessResult,
    ProcessRunner,
    _ProcessCancelled,
    _ProcessContentTooLarge,
    _ProcessExecutionError,
    _ProcessTimedOut,
)

JINA_READER_BASE_URL = "https://r.jina.ai/"
JINA_READER_BACKEND = "jina-reader"
JINA_READER_MEDIA_TYPE = "text/markdown"


@dataclass(frozen=True, slots=True)
class JinaReaderWebAdapter:
    """Production WEB port implementation with fixed Jina Reader egress."""

    runner: ProcessRunner = field(default_factory=BoundedProcessRunner)
    clock: Callable[[], datetime] = field(default=lambda: datetime.now(UTC))

    def read(
        self,
        request: ResearchRequest,
        *,
        cancellation: CancellationToken,
    ) -> ResearchSource:
        """Прочитать только WEB request и вернуть normalized untrusted text."""

        if request.source_kind is not SourceKind.WEB:
            raise ResearchBackendUnavailableError()
        if cancellation.is_cancelled():
            raise ResearchCancelledError()

        try:
            result = self.runner.run(
                _build_curl_argv(request.uri),
                timeout_seconds=request.timeout_seconds,
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

        if result.returncode != 0:
            raise ResearchUpstreamError()
        if len(result.stdout) > request.max_bytes:
            raise ResearchContentTooLargeError()
        try:
            content = result.stdout.decode("utf-8")
        except UnicodeDecodeError:
            raise ResearchMalformedResultError() from None
        if not content.strip():
            raise ResearchMalformedResultError()

        retrieved_at = self.clock()
        try:
            offset = retrieved_at.utcoffset() if isinstance(retrieved_at, datetime) else None
        except TypeError, ValueError, OverflowError:
            offset = None
        if not isinstance(retrieved_at, datetime) or retrieved_at.tzinfo is None or offset is None:
            raise ResearchMalformedResultError()
        return ResearchSource(
            uri=request.uri,
            source_kind=SourceKind.WEB,
            retrieved_at=retrieved_at,
            backend=JINA_READER_BACKEND,
            content=content,
            media_type=JINA_READER_MEDIA_TYPE,
        )


def _build_curl_argv(source_uri: str) -> tuple[str, ...]:
    """Собрать закрытый argv; source URI остаётся одним значением."""

    return (
        CURL_EXECUTABLE,
        "--disable",
        "--silent",
        "--fail",
        "--noproxy",
        "*",
        "--proto",
        "=https",
        "--max-redirs",
        "0",
        "--url",
        f"{JINA_READER_BASE_URL}{source_uri}",
    )


__all__ = [
    "CURL_EXECUTABLE",
    "JINA_READER_BACKEND",
    "JINA_READER_BASE_URL",
    "JINA_READER_MEDIA_TYPE",
    "MAX_STDERR_BYTES",
    "BoundedProcessRunner",
    "JinaReaderWebAdapter",
    "ProcessResult",
    "ProcessRunner",
    "_ProcessCancelled",
    "_ProcessContentTooLarge",
    "_ProcessTimedOut",
]
