"""Детерминированные проверки public GitHub adapter без live network."""

from __future__ import annotations

import json
import subprocess
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any

import pytest

from second_brain.adapters.research.github import (
    GITHUB_API_BASE_URL,
    GITHUB_API_VERSION,
    GITHUB_BACKEND,
    GITHUB_METADATA_ACCEPT,
    GITHUB_README_ACCEPT,
    GITHUB_USER_AGENT,
    MAX_METADATA_RESPONSE_BYTES,
    MAX_RESPONSE_HEADER_BYTES,
    PublicGitHubAdapter,
)
from second_brain.adapters.research.process import ProcessResult
from second_brain.application.ports import (
    CancellationToken,
    CancellationTokenSource,
    ResearchBackendUnavailableError,
    ResearchCancelledError,
    ResearchContentTooLargeError,
    ResearchInvalidRequestError,
    ResearchMalformedResultError,
    ResearchTimeoutError,
    ResearchUpstreamError,
)
from second_brain.application.research import (
    ResearchGateway,
    ResearchRequest,
    ResearchSource,
    SourceKind,
)

METADATA = {
    "id": 123456,
    "private": False,
    "full_name": "github/.github",
    "description": "Public project",
    "default_branch": "main",
    "language": "Markdown",
    "topics": ["zeta", "Research"],
    "owner": {"login": "github"},
    "html_url": "https://evil.example/not-followed",
    "download_url": "https://evil.example/not-followed",
}
REPOSITORY_URI = "https://github.com/GitHub/.GitHub"


def _response(status: int, body: bytes = b"", *, reason: str = "OK") -> bytes:
    return (
        f"HTTP/2 {status} {reason}\r\n"
        "Content-Type: application/json\r\n"
        "X-RateLimit-Remaining: 59\r\n"
        "\r\n"
    ).encode("ascii") + body


def _metadata_response(**changes: object) -> bytes:
    payload = dict(METADATA)
    payload.update(changes)
    return _response(200, json.dumps(payload, ensure_ascii=False).encode("utf-8"))


class FakeRunner:
    """Process seam, который записывает argv и возвращает bounded fixtures."""

    def __init__(self, responses: Sequence[ProcessResult]) -> None:
        self.responses = list(responses)
        self.calls: list[tuple[tuple[str, ...], float, int, CancellationToken]] = []

    def run(
        self,
        argv: Sequence[str],
        *,
        timeout_seconds: float,
        max_stdout_bytes: int,
        cancellation: CancellationToken,
    ) -> ProcessResult:
        self.calls.append((tuple(argv), timeout_seconds, max_stdout_bytes, cancellation))
        return self.responses.pop(0)


def _read(
    runner: FakeRunner,
    *,
    uri: str = REPOSITORY_URI,
    max_bytes: int = 5_000_000,
    timeout_seconds: int = 30,
    token: CancellationToken | None = None,
    monotonic: object | None = None,
) -> ResearchSource:
    adapter = PublicGitHubAdapter(
        runner=runner,
        clock=lambda: datetime(2026, 9, 4, 12, 0, tzinfo=UTC),
        monotonic=monotonic if callable(monotonic) else (lambda: 0.0),
    )
    return ResearchGateway(adapter).read(
        ResearchRequest(
            SourceKind.GITHUB,
            uri,
            timeout_seconds=timeout_seconds,
            max_bytes=max_bytes,
        ),
        cancellation=token or CancellationTokenSource(),
    )


def test_github_uses_two_fixed_gets_and_normalizes_canonical_metadata_and_readme() -> None:
    readme = b"# README\r\n\r\nRaw public text.\r"
    runner = FakeRunner(
        [
            ProcessResult(0, _metadata_response(), b"metadata secret"),
            ProcessResult(0, _response(200, readme), b"README secret"),
        ]
    )

    source = _read(runner)

    assert source.uri == REPOSITORY_URI
    assert source.source_kind is SourceKind.GITHUB
    assert source.backend == GITHUB_BACKEND
    assert source.title == "github/.github"
    assert source.author == "github"
    assert source.upstream_id == "123456"
    assert source.media_type == "text/markdown"
    assert source.published_at is None
    assert source.content == (
        "Repository: github/.github\n"
        "Description: Public project\n"
        "Default branch: main\n"
        "Language: Markdown\n"
        "Topics: Research, zeta\n"
        "README:\n"
        "\n"
        "# README\n\nRaw public text.\n"
    )

    metadata_argv, metadata_timeout, metadata_limit, _ = runner.calls[0]
    readme_argv, readme_timeout, readme_limit, _ = runner.calls[1]
    assert len(runner.calls) == 2
    assert metadata_argv[0] == "curl"
    assert metadata_argv[metadata_argv.index("--request") + 1] == "GET"
    assert metadata_argv[metadata_argv.index("--url") + 1] == (
        f"{GITHUB_API_BASE_URL}/repos/GitHub/.GitHub"
    )
    assert readme_argv[readme_argv.index("--url") + 1] == (
        f"{GITHUB_API_BASE_URL}/repos/GitHub/.GitHub/readme"
    )
    assert metadata_argv[metadata_argv.index("--header") + 1] == (
        f"Accept: {GITHUB_METADATA_ACCEPT}"
    )
    assert readme_argv[readme_argv.index("--header") + 1] == f"Accept: {GITHUB_README_ACCEPT}"
    for argv in (metadata_argv, readme_argv):
        assert "--disable" in argv
        assert argv[argv.index("--noproxy") + 1] == "*"
        assert argv[argv.index("--proto") + 1] == "=https"
        assert argv[argv.index("--max-redirs") + 1] == "0"
        assert "--include" in argv
        assert f"X-GitHub-Api-Version: {GITHUB_API_VERSION}" in argv
        assert f"User-Agent: {GITHUB_USER_AGENT}" in argv
        assert "--location" not in argv
        assert "--fail" not in argv
        assert "--insecure" not in argv
        assert "--cookie" not in argv
        assert "--netrc" not in argv
        assert "--proxy" not in argv
        assert "Authorization" not in " ".join(argv)
        assert "GH_TOKEN" not in " ".join(argv)
        assert "GITHUB_TOKEN" not in " ".join(argv)
    assert metadata_timeout == 30
    assert metadata_limit == MAX_METADATA_RESPONSE_BYTES
    assert readme_timeout == 30
    assert readme_limit == 5_000_000 + MAX_RESPONSE_HEADER_BYTES


def test_github_readme_404_returns_metadata_only_success() -> None:
    runner = FakeRunner(
        [
            ProcessResult(0, _metadata_response(), b""),
            ProcessResult(0, _response(404, b'{"message":"Not Found"}', reason="Not Found"), b""),
        ]
    )

    source = _read(runner)

    assert source.media_type == "text/plain"
    assert source.content.endswith("README:\n\n(отсутствует)")
    assert len(runner.calls) == 2


@pytest.mark.parametrize("status", [403, 429, 500])
def test_github_readme_unexpected_status_is_safe_upstream_error_without_fallback(
    status: int,
) -> None:
    runner = FakeRunner(
        [
            ProcessResult(0, _metadata_response(), b""),
            ProcessResult(
                0, _response(status, b"secret upstream body", reason="Error"), b"stderr secret"
            ),
        ]
    )

    with pytest.raises(ResearchUpstreamError) as error:
        _read(runner)

    assert error.value.code == "RESEARCH_UPSTREAM_FAILURE"
    assert "secret" not in str(error.value)
    assert len(runner.calls) == 2


@pytest.mark.parametrize("status", [301, 401, 403, 404, 429, 500])
def test_github_metadata_unexpected_status_stops_before_readme(status: int) -> None:
    runner = FakeRunner([ProcessResult(0, _response(status, b"upstream secret"), b"stderr secret")])

    with pytest.raises(ResearchUpstreamError):
        _read(runner)

    assert len(runner.calls) == 1


@pytest.mark.parametrize(
    "uri",
    [
        "http://github.com/owner/repo",
        "https://www.github.com/owner/repo",
        "https://api.github.com/repos/owner/repo",
        "https://gist.github.com/owner/repo",
        "https://raw.githubusercontent.com/owner/repo",
        "https://github.example/owner/repo",
        "https://github.com/owner/repo/",
        "https://github.com/owner/repo.git",
        "https://github.com/owner/repo.GIT",
        "https://github.com/owner/.",
        "https://github.com/owner/..",
        "https://github.com/owner/repo/issues",
        "https://github.com/owner/repo/tree/main",
        "https://github.com/owner/repo?tab=readme",
        "https://github.com/owner/repo#readme",
        "https://github.com:443/owner/repo",
        "https://user:password@github.com/owner/repo",
        "https://github.com/ow%6Eer/repo",
        "https://github.com/owner/repo\\name",
        "https://github.com/owner/repo with-space",
        "https://github.com/-owner/repo",
        "https://github.com/owner-/repo",
        f"https://github.com/{'o' * 40}/repo",
        f"https://github.com/owner/{'r' * 101}",
    ],
)
def test_github_invalid_repository_root_is_rejected_before_process(uri: str) -> None:
    runner = FakeRunner([])

    with pytest.raises(ResearchInvalidRequestError):
        _read(runner, uri=uri)

    assert runner.calls == []


def test_github_leading_dot_repository_is_valid() -> None:
    metadata = dict(METADATA, full_name="github/.github", owner={"login": "github"})
    runner = FakeRunner(
        [
            ProcessResult(0, _response(200, json.dumps(metadata).encode()), b""),
            ProcessResult(0, _response(404), b""),
        ]
    )

    source = _read(runner, uri="https://github.com/github/.github")

    assert source.title == "github/.github"


@pytest.mark.parametrize(
    "metadata",
    [
        {**METADATA, "full_name": "other/.github"},
        {**METADATA, "full_name": "github/other"},
        {**METADATA, "owner": {"login": "other"}},
        {**METADATA, "private": True},
        {**METADATA, "id": 0},
        {**METADATA, "id": True},
        {**METADATA, "full_name": "github"},
        {**METADATA, "description": "line\nbreak"},
        {**METADATA, "topics": "not-a-sequence"},
    ],
)
def test_github_malformed_or_mismatched_metadata_is_rejected(metadata: dict[str, object]) -> None:
    runner = FakeRunner(
        [
            ProcessResult(0, _response(200, json.dumps(metadata).encode()), b""),
        ]
    )

    with pytest.raises(ResearchMalformedResultError):
        _read(runner)

    assert len(runner.calls) == 1


@pytest.mark.parametrize("body", [b"", b"not utf-8: \xff"])
def test_github_empty_or_invalid_utf8_readme_is_malformed(body: bytes) -> None:
    runner = FakeRunner(
        [
            ProcessResult(0, _metadata_response(), b""),
            ProcessResult(0, _response(200, body), b""),
        ]
    )

    with pytest.raises(ResearchMalformedResultError):
        _read(runner)


def test_github_total_content_limit_is_not_silently_truncated() -> None:
    runner = FakeRunner(
        [
            ProcessResult(0, _metadata_response(), b""),
            ProcessResult(0, _response(200, b"x" * 200), b""),
        ]
    )

    with pytest.raises(ResearchContentTooLargeError):
        _read(runner, max_bytes=180)

    assert len(runner.calls) == 2


def test_github_process_failure_does_not_publish_stderr() -> None:
    runner = FakeRunner([ProcessResult(42, b"", b"Authorization: secret-token")])

    with pytest.raises(ResearchUpstreamError) as error:
        _read(runner)

    assert "secret-token" not in str(error.value)


@pytest.mark.parametrize(
    "raw_response",
    [b"", b"not an HTTP response", b"HTTP/2 200\r\nbad header\r\n\r\n{}"],
)
def test_github_malformed_http_envelope_is_rejected(raw_response: bytes) -> None:
    runner = FakeRunner([ProcessResult(0, raw_response, b"")])

    with pytest.raises(ResearchMalformedResultError):
        _read(runner)

    assert len(runner.calls) == 1


def test_github_missing_curl_is_backend_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    def missing_executable(*args: Any, **kwargs: Any) -> object:
        raise FileNotFoundError("curl")

    monkeypatch.setattr(subprocess, "Popen", missing_executable)

    with pytest.raises(ResearchBackendUnavailableError):
        PublicGitHubAdapter().read(
            ResearchRequest(SourceKind.GITHUB, "https://github.com/github/.github"),
            cancellation=CancellationTokenSource(),
        )


def test_github_shared_timeout_is_used_for_both_serial_processes() -> None:
    class AdvancingMonotonic:
        def __init__(self) -> None:
            self.values = iter([0.0] * 6 + [6.0] * 8)

        def __call__(self) -> float:
            return next(self.values)

    runner = FakeRunner(
        [
            ProcessResult(0, _metadata_response(), b""),
            ProcessResult(0, _response(404), b""),
        ]
    )

    _read(runner, timeout_seconds=10, monotonic=AdvancingMonotonic())

    assert runner.calls[0][1] == 10
    assert runner.calls[1][1] == 4


def test_github_cancellation_between_phases_stops_before_readme() -> None:
    token = CancellationTokenSource()

    class CancellingRunner(FakeRunner):
        def run(
            self,
            argv: Sequence[str],
            *,
            timeout_seconds: float,
            max_stdout_bytes: int,
            cancellation: CancellationToken,
        ) -> ProcessResult:
            result = super().run(
                argv,
                timeout_seconds=timeout_seconds,
                max_stdout_bytes=max_stdout_bytes,
                cancellation=cancellation,
            )
            token.cancel()
            return result

    runner = CancellingRunner([ProcessResult(0, _metadata_response(), b"")])

    with pytest.raises(ResearchCancelledError):
        _read(runner, token=token)

    assert len(runner.calls) == 1


def test_github_cancellation_before_read_is_not_a_process_call() -> None:
    token = CancellationTokenSource()
    token.cancel()
    runner = FakeRunner([])

    with pytest.raises(ResearchCancelledError):
        _read(runner, token=token)

    assert runner.calls == []


def test_github_timeout_before_first_process_is_safe() -> None:
    runner = FakeRunner([])
    monotonic_values = iter([2.0, 3.0])

    with pytest.raises(ResearchTimeoutError):
        _read(runner, timeout_seconds=1, monotonic=lambda: next(monotonic_values))

    assert runner.calls == []


def test_non_github_source_kind_fails_closed_without_process() -> None:
    runner = FakeRunner([])
    adapter = PublicGitHubAdapter(runner=runner)

    with pytest.raises(ResearchBackendUnavailableError):
        adapter.read(
            ResearchRequest(SourceKind.RSS, "https://feeds.example.org/feed.xml"),
            cancellation=CancellationTokenSource(),
        )

    assert runner.calls == []
