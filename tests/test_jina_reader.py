"""Детерминированные проверки Jina Reader adapter без live network."""

from __future__ import annotations

import io
import subprocess
import sys
import threading
import time
from collections.abc import Sequence
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

import pytest

from second_brain.adapters.research import process as research_process
from second_brain.adapters.research.jina_reader import (
    JINA_READER_BACKEND,
    JINA_READER_BASE_URL,
    BoundedProcessRunner,
    JinaReaderWebAdapter,
    ProcessResult,
    _ProcessCancelled,
    _ProcessContentTooLarge,
    _ProcessTimedOut,
)
from second_brain.application.ports import (
    CancellationToken,
    CancellationTokenSource,
    ResearchBackendUnavailableError,
    ResearchContentTooLargeError,
    ResearchMalformedResultError,
    ResearchUpstreamError,
)
from second_brain.application.research import ResearchGateway, ResearchRequest, SourceKind


class FakeRunner:
    """Детерминированный runner, записывающий единственный вызов adapter."""

    def __init__(self, result: ProcessResult | None = None) -> None:
        self.result = result or ProcessResult(0, "Текст статьи\n".encode(), b"")
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
        return self.result


def test_web_request_builds_fixed_jina_argv_and_normalizes_unicode() -> None:
    runner = FakeRunner(ProcessResult(0, "Русский текст\n".encode(), b"stderr secret"))
    retrieved_at = datetime(2026, 9, 3, 12, 0, tzinfo=UTC)
    adapter = JinaReaderWebAdapter(runner=runner, clock=lambda: retrieved_at)
    uri = "https://example.com/article?next=--header%20X"

    source = ResearchGateway(adapter).read(
        ResearchRequest(SourceKind.WEB, uri, timeout_seconds=7, max_bytes=1024),
        cancellation=CancellationTokenSource(),
    )

    argv, timeout, max_bytes, _ = runner.calls[0]
    assert argv[0] == "curl"
    assert argv[1] == "--disable"
    assert argv[-1] == f"{JINA_READER_BASE_URL}{uri}"
    assert len([item for item in argv if item == argv[-1]]) == 1
    assert "--location" not in argv
    assert "--insecure" not in argv
    assert "--header" not in argv
    assert "--cookie" not in argv
    assert "--netrc" not in argv
    assert timeout == 7
    assert max_bytes == 1024
    assert source.uri == uri
    assert source.backend == JINA_READER_BACKEND
    assert source.content == "Русский текст\n"
    assert source.media_type == "text/markdown"


def test_non_web_source_kind_fails_closed_without_process_launch() -> None:
    runner = FakeRunner()
    adapter = JinaReaderWebAdapter(runner=runner)

    with pytest.raises(ResearchBackendUnavailableError):
        adapter.read(
            ResearchRequest(SourceKind.GITHUB, "https://github.com/MikeMoore1337/second-brain"),
            cancellation=CancellationTokenSource(),
        )

    assert runner.calls == []


def test_missing_curl_is_backend_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    def missing_curl(*args: Any, **kwargs: Any) -> Any:
        raise FileNotFoundError("curl")

    monkeypatch.setattr(subprocess, "Popen", missing_curl)

    with pytest.raises(ResearchBackendUnavailableError):
        JinaReaderWebAdapter().read(
            ResearchRequest(SourceKind.WEB, "https://example.com/article"),
            cancellation=CancellationTokenSource(),
        )


@pytest.mark.parametrize(
    ("stdout", "expected_error"),
    [
        (b"", ResearchMalformedResultError),
        (b"\xff", ResearchMalformedResultError),
    ],
)
def test_empty_or_invalid_utf8_output_is_malformed(
    stdout: bytes,
    expected_error: type[Exception],
) -> None:
    adapter = JinaReaderWebAdapter(runner=FakeRunner(ProcessResult(0, stdout, b"")))

    with pytest.raises(expected_error):
        adapter.read(
            ResearchRequest(SourceKind.WEB, "https://example.com/article"),
            cancellation=CancellationTokenSource(),
        )


def test_nonzero_process_and_stderr_are_not_exposed() -> None:
    adapter = JinaReaderWebAdapter(
        runner=FakeRunner(ProcessResult(22, b"ignored", b"Authorization: secret-token"))
    )

    with pytest.raises(ResearchUpstreamError) as error:
        adapter.read(
            ResearchRequest(SourceKind.WEB, "https://example.com/article"),
            cancellation=CancellationTokenSource(),
        )

    assert "secret-token" not in str(error.value)
    assert "Authorization" not in str(error.value)


def test_adapter_rejects_runner_result_over_request_limit() -> None:
    adapter = JinaReaderWebAdapter(runner=FakeRunner(ProcessResult(0, b"12345", b"")))

    with pytest.raises(ResearchContentTooLargeError):
        adapter.read(
            ResearchRequest(SourceKind.WEB, "https://example.com/article", max_bytes=4),
            cancellation=CancellationTokenSource(),
        )


def test_runner_uses_no_shell_and_does_not_inherit_proxy_or_curl_credentials(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observed: dict[str, Any] = {}

    class CompletedProcess:
        stdout = io.BytesIO(b"ok")
        stderr = io.BytesIO(b"")
        returncode = 0

        def poll(self) -> int:
            return 0

        def wait(self, timeout: float | None = None) -> int:
            return 0

        def terminate(self) -> None:
            return None

        def kill(self) -> None:
            return None

    def fake_popen(argv: list[str], **kwargs: Any) -> CompletedProcess:
        observed["argv"] = argv
        observed.update(kwargs)
        return CompletedProcess()

    monkeypatch.setenv("HTTP_PROXY", "http://proxy.invalid:8080")
    monkeypatch.setenv("CURL_HOME", "C:\\secret-curl")
    monkeypatch.setattr(subprocess, "Popen", fake_popen)

    result = BoundedProcessRunner().run(
        ("curl", "--disable", "--url", "https://r.jina.ai/https://example.com"),
        timeout_seconds=1,
        max_stdout_bytes=16,
        cancellation=CancellationTokenSource(),
    )

    assert result.stdout == b"ok"
    assert observed["shell"] is False
    assert observed["stdin"] is subprocess.DEVNULL
    assert observed["close_fds"] is True
    assert "HTTP_PROXY" not in observed["env"]
    assert "CURL_HOME" not in observed["env"]
    assert "HOME" not in observed["env"]


@pytest.mark.parametrize("system_root_name", ["SystemRoot", "SYSTEMROOT", "sYsTeMrOoT"])
def test_windows_like_system_root_is_canonicalized_case_insensitively(
    monkeypatch: pytest.MonkeyPatch,
    system_root_name: str,
) -> None:
    source_environment = {
        system_root_name: r"C:\Windows",
        "PATH": r"C:\Windows\System32",
        "TEMP": r"C:\Temp",
    }
    monkeypatch.setattr(
        research_process,
        "os",
        SimpleNamespace(name="nt", environ=source_environment),
    )

    environment = research_process._safe_process_environment()

    assert environment["SYSTEMROOT"] == r"C:\Windows"
    assert [name for name in environment if name.casefold() == "systemroot"] == ["SYSTEMROOT"]


def test_windows_safe_environment_preserves_allowlist_and_excludes_secrets_proxy_and_auth(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source_environment = {
        "PATH": r"C:\Runtime",
        "Path": r"C:\Runtime-alt",
        "SystemRoot": r"C:\Windows",
        "WINDIR": r"C:\Windows",
        "TEMP": r"C:\Temp",
        "TMP": r"C:\Tmp",
        "TMPDIR": r"C:\TmpDir",
        "GH_TOKEN": "gh-secret",
        "GITHUB_TOKEN": "github-secret",
        "PAT": "pat-secret",
        "AUTHORIZATION": "Bearer secret",
        "HTTP_PROXY": "http://proxy.invalid:8080",
        "HTTPS_PROXY": "https://proxy.invalid:8443",
        "ALL_PROXY": "socks5://proxy.invalid:1080",
        "NO_PROXY": "example.com",
        "CURL_HOME": r"C:\secret-curl",
        "HOME": r"C:\Users\secret",
        "USERPROFILE": r"C:\Users\secret",
        "APPDATA": r"C:\Users\secret\AppData",
        "UNRELATED": "must-not-pass",
    }
    monkeypatch.setattr(
        research_process,
        "os",
        SimpleNamespace(name="nt", environ=source_environment),
    )

    environment = research_process._safe_process_environment()

    assert environment == {
        "PATH": r"C:\Runtime",
        "Path": r"C:\Runtime-alt",
        "WINDIR": r"C:\Windows",
        "TEMP": r"C:\Temp",
        "TMP": r"C:\Tmp",
        "TMPDIR": r"C:\TmpDir",
        "SYSTEMROOT": r"C:\Windows",
    }


@pytest.mark.skipif(
    sys.platform != "win32", reason="Windows-specific OpenSSL environment regression"
)
def test_windows_sanitized_research_environment_initializes_ssl_in_real_isolated_child() -> None:
    environment = research_process._safe_process_environment()
    argv = [sys.executable, "-I", "-c", "import ssl; ssl.create_default_context()"]

    assert "SYSTEMROOT" in environment
    assert all(name.casefold() != "systemroot" or name == "SYSTEMROOT" for name in environment)

    completed = subprocess.run(
        argv,
        env=environment,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        timeout=10,
        check=False,
    )

    assert completed.returncode == 0


@pytest.mark.skipif(sys.platform != "win32", reason="Windows-specific curl environment regression")
def test_windows_sanitized_research_environment_runs_curl_version_without_network() -> None:
    environment = research_process._safe_process_environment()

    completed = subprocess.run(
        [research_process.CURL_EXECUTABLE, "--version"],
        env=environment,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        timeout=10,
        check=False,
    )

    assert completed.returncode == 0


def _python_process(code: str) -> tuple[str, ...]:
    return (sys.executable, "-c", code)


def test_runner_terminates_process_on_timeout() -> None:
    started = time.monotonic()

    with pytest.raises(_ProcessTimedOut):
        BoundedProcessRunner().run(
            _python_process("import time; time.sleep(5)"),
            timeout_seconds=0.1,
            max_stdout_bytes=1024,
            cancellation=CancellationTokenSource(),
        )

    assert time.monotonic() - started < 2


def test_runner_terminates_process_on_cancellation() -> None:
    token = CancellationTokenSource()

    def cancel_later() -> None:
        time.sleep(0.1)
        token.cancel()

    cancel_thread = threading.Thread(target=cancel_later)
    cancel_thread.start()
    started = time.monotonic()

    with pytest.raises(_ProcessCancelled):
        BoundedProcessRunner().run(
            _python_process("import time; time.sleep(5)"),
            timeout_seconds=5,
            max_stdout_bytes=1024,
            cancellation=token,
        )

    cancel_thread.join()
    assert time.monotonic() - started < 2


def test_runner_stops_process_when_stdout_exceeds_hard_limit() -> None:
    started = time.monotonic()

    with pytest.raises(_ProcessContentTooLarge):
        BoundedProcessRunner().run(
            _python_process(
                "import sys, time; sys.stdout.write('x' * 100000); "
                "sys.stdout.flush(); time.sleep(5)"
            ),
            timeout_seconds=5,
            max_stdout_bytes=1024,
            cancellation=CancellationTokenSource(),
        )

    assert time.monotonic() - started < 2
