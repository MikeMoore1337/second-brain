"""Общий bounded subprocess runner для read-only research adapters."""

from __future__ import annotations

import os
import subprocess
import threading
import time
from collections.abc import Sequence
from dataclasses import dataclass, field
from io import BufferedReader
from typing import Protocol

from second_brain.application.ports import CancellationToken

CURL_EXECUTABLE = "curl"
MAX_STDERR_BYTES = 8_192

_READ_CHUNK_BYTES = 64 * 1024
_POLL_INTERVAL_SECONDS = 0.01
_TERMINATE_WAIT_SECONDS = 0.25
_KILL_WAIT_SECONDS = 1.0
_SAFE_ENVIRONMENT_NAMES = frozenset(
    {
        "PATH",
        "Path",
        "SystemRoot",
        "WINDIR",
        "TEMP",
        "TMP",
        "TMPDIR",
    }
)


class ProcessRunner(Protocol):
    """Минимальная injectable граница bounded process execution."""

    def run(
        self,
        argv: Sequence[str],
        *,
        timeout_seconds: float,
        max_stdout_bytes: int,
        cancellation: CancellationToken,
    ) -> ProcessResult:
        """Выполнить один процесс с bounded stdout/stderr и cleanup."""


@dataclass(frozen=True, slots=True)
class ProcessResult:
    """Внутренний bounded результат процесса без публикации stderr."""

    returncode: int
    stdout: bytes
    stderr: bytes


class _ProcessExecutionError(RuntimeError):
    """Внутренняя безопасная ошибка процесса без upstream details."""


class _ProcessCancelled(_ProcessExecutionError):
    """Процесс остановлен по cancellation token."""


class _ProcessTimedOut(_ProcessExecutionError):
    """Процесс остановлен по timeout."""


class _ProcessContentTooLarge(_ProcessExecutionError):
    """Процесс остановлен после превышения stdout limit."""


class _ProcessFailed(_ProcessExecutionError):
    """Процесс завершился ненулевым кодом или bounded stderr overflow."""


@dataclass(slots=True)
class _BoundedCapture:
    """Ограниченно накапливать только первые bytes одного pipe."""

    limit: int
    data: bytearray = field(default_factory=bytearray)
    exceeded: bool = False

    def read(self, stream: BufferedReader) -> None:
        """Читать pipe до EOF или первого байта сверх лимита."""

        try:
            while not self.exceeded:
                remaining = self.limit - len(self.data)
                chunk = stream.read(min(_READ_CHUNK_BYTES, remaining + 1))
                if not chunk:
                    return
                if len(chunk) > remaining:
                    self.exceeded = True
                    return
                self.data.extend(chunk)
        except OSError, ValueError:
            # The process may stop while the pipe is being read.
            return


class BoundedProcessRunner:
    """Кросс-platform runner одного процесса без shell и без unbounded pipes."""

    def run(
        self,
        argv: Sequence[str],
        *,
        timeout_seconds: float,
        max_stdout_bytes: int,
        cancellation: CancellationToken,
    ) -> ProcessResult:
        """Запустить argv и остановить его при timeout, cancellation или overflow."""

        try:
            process = subprocess.Popen(
                list(argv),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                shell=False,
                close_fds=True,
                bufsize=0,
                env=_safe_process_environment(),
            )
        except OSError as exc:
            raise _ProcessExecutionError() from exc

        if process.stdout is None or process.stderr is None:
            _stop_process(process)
            raise _ProcessFailed()

        stdout_capture = _BoundedCapture(max_stdout_bytes)
        stderr_capture = _BoundedCapture(MAX_STDERR_BYTES)
        stdout_thread = threading.Thread(
            target=stdout_capture.read,
            args=(process.stdout,),
            name="second-brain-research-stdout",
            daemon=True,
        )
        stderr_thread = threading.Thread(
            target=stderr_capture.read,
            args=(process.stderr,),
            name="second-brain-research-stderr",
            daemon=True,
        )
        stdout_thread.start()
        stderr_thread.start()

        failure: _ProcessExecutionError | None = None
        started_at = time.monotonic()
        try:
            while process.poll() is None:
                if cancellation.is_cancelled():
                    failure = _ProcessCancelled()
                    break
                if stdout_capture.exceeded:
                    failure = _ProcessContentTooLarge()
                    break
                if stderr_capture.exceeded:
                    failure = _ProcessFailed()
                    break
                if time.monotonic() - started_at >= timeout_seconds:
                    failure = _ProcessTimedOut()
                    break
                time.sleep(_POLL_INTERVAL_SECONDS)

            if failure is not None:
                _stop_process(process)
            elif stdout_capture.exceeded:
                _stop_process(process)
                failure = _ProcessContentTooLarge()
            elif stderr_capture.exceeded:
                _stop_process(process)
                failure = _ProcessFailed()
            elif cancellation.is_cancelled():
                _stop_process(process)
                failure = _ProcessCancelled()
            elif time.monotonic() - started_at >= timeout_seconds and process.poll() is None:
                _stop_process(process)
                failure = _ProcessTimedOut()
        finally:
            if process.poll() is None:
                _stop_process(process)
            stdout_thread.join()
            stderr_thread.join()
            process.stdout.close()
            process.stderr.close()

        if failure is None:
            if stdout_capture.exceeded:
                failure = _ProcessContentTooLarge()
            elif stderr_capture.exceeded:
                failure = _ProcessFailed()
        if failure is not None:
            raise failure

        return ProcessResult(
            returncode=process.returncode if process.returncode is not None else -1,
            stdout=bytes(stdout_capture.data),
            stderr=bytes(stderr_capture.data),
        )


def _safe_process_environment() -> dict[str, str]:
    """Передать curl только runtime PATH/SystemRoot и temp paths без secrets."""

    return {name: value for name, value in os.environ.items() if name in _SAFE_ENVIRONMENT_NAMES}


def _stop_process(process: subprocess.Popen[bytes]) -> None:
    """Terminate, затем kill только созданный текущим runner process."""

    if process.poll() is not None:
        return
    try:
        process.terminate()
        process.wait(timeout=_TERMINATE_WAIT_SECONDS)
        return
    except OSError, subprocess.TimeoutExpired:
        pass
    try:
        process.kill()
    except OSError:
        return
    try:
        process.wait(timeout=_KILL_WAIT_SECONDS)
    except OSError, subprocess.TimeoutExpired:
        # wait без timeout нужен для отсутствия оставшегося subprocess.
        process.wait()


__all__ = [
    "CURL_EXECUTABLE",
    "MAX_STDERR_BYTES",
    "BoundedProcessRunner",
    "ProcessResult",
    "ProcessRunner",
]
