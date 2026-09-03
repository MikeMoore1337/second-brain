"""Killable in-process-isolated parser worker для RSS/Atom bytes."""

from __future__ import annotations

import multiprocessing
import time
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass, field
from multiprocessing.connection import Connection
from multiprocessing.process import BaseProcess
from threading import Event
from typing import Protocol

import feedparser  # type: ignore[import-untyped]

from second_brain.application.ports import (
    CancellationToken,
    ResearchContentTooLargeError,
)

_POLL_INTERVAL_SECONDS = 0.01
_TERMINATE_WAIT_SECONDS = 0.25
_KILL_WAIT_SECONDS = 1.0


ParserTarget = Callable[[Connection, bytes, int], None]


class _ReceiveConnection(Protocol):
    """Минимальные операции, используемые parent-side receive pipe."""

    def poll(self, timeout: float | None = None) -> bool:
        """Проверить наличие сообщения в bounded IPC pipe."""

    def recv(self) -> object:
        """Прочитать одно typed IPC message."""

    def close(self) -> None:
        """Закрыть parent-side pipe."""


@dataclass(frozen=True, slots=True)
class NormalizedFeed:
    """Только bounded normalized text и безопасные feed metadata fields."""

    content: str
    title: str | None
    author: str | None
    media_type: str


class ParserWorker(Protocol):
    """Минимальная injectable граница bounded parser process."""

    def run(
        self,
        raw_bytes: bytes,
        *,
        max_bytes: int,
        deadline: float,
        cancellation: CancellationToken,
    ) -> NormalizedFeed:
        """Разобрать bytes в bounded DTO или остановить worker."""


class _ParserWorkerError(RuntimeError):
    """Внутренняя ошибка worker без публикации child details."""


class _ParserCancelled(_ParserWorkerError):
    """Worker остановлен cancellation token."""


class _ParserTimedOut(_ParserWorkerError):
    """Worker остановлен по общему request deadline."""


class _ParserUnavailable(_ParserWorkerError):
    """Worker не удалось запустить."""


class _ParserMalformed(_ParserWorkerError):
    """Worker не вернул безопасный normalized result."""


class _ParserContentTooLarge(_ParserWorkerError):
    """Worker отклонил normalized result сверх request limit."""


@dataclass(frozen=True, slots=True)
class _ParserMessage:
    """Закрытый bounded IPC message без raw exception/stderr."""

    status: str
    result: NormalizedFeed | None = None


def _parse_worker_entry(
    send_conn: Connection,
    raw_bytes: bytes,
    max_bytes: int,
) -> None:
    """Разобрать только переданные bytes; этот fixed entrypoint не делает network I/O."""

    try:
        # The worker deliberately receives bytes, never a URL.
        parsed = feedparser.parse(raw_bytes)
        from .rss import _normalize_feed

        result = _normalize_feed(parsed, max_bytes)
        message = _ParserMessage(status="ok", result=result)
    except ResearchContentTooLargeError:
        message = _ParserMessage(status="too_large")
    except BaseException:
        # Never send parser exception text or a traceback through the boundary.
        message = _ParserMessage(status="malformed")

    try:
        send_conn.send(message)
    except BaseException:
        # The parent may have terminated the worker at its deadline.
        pass
    finally:
        with suppress(OSError, ValueError):
            send_conn.close()


def _parser_process_entry(
    target: ParserTarget,
    send_conn: Connection,
    raw_bytes: bytes,
    max_bytes: int,
) -> None:
    """Запустить только code-level target и скрыть неожиданный child traceback."""

    try:
        target(send_conn, raw_bytes, max_bytes)
    except BaseException:
        try:
            send_conn.send(_ParserMessage(status="malformed"))
        except BaseException:
            pass
        finally:
            with suppress(OSError, ValueError):
                send_conn.close()


@dataclass(slots=True)
class MultiprocessingParserWorker:
    """Запустить fixed parser entrypoint в отдельном killable spawn process.

    ``target`` существует только как deterministic test seam; production default
    всегда `_parse_worker_entry`, без user-controlled executable или command argv.
    """

    target: ParserTarget = field(default=_parse_worker_entry, repr=False)
    last_process: BaseProcess | None = field(default=None, init=False, repr=False)
    started: Event = field(default_factory=Event, init=False, repr=False)

    def run(
        self,
        raw_bytes: bytes,
        *,
        max_bytes: int,
        deadline: float,
        cancellation: CancellationToken,
    ) -> NormalizedFeed:
        """Вернуть normalized result или остановить child при deadline/cancellation."""

        if cancellation.is_cancelled():
            raise _ParserCancelled()

        context = multiprocessing.get_context("spawn")
        recv_conn, send_conn = context.Pipe(duplex=False)
        process = context.Process(
            target=_parser_process_entry,
            args=(self.target, send_conn, raw_bytes, max_bytes),
            daemon=True,
        )
        self.last_process = process
        self.started.clear()
        started = False
        try:
            try:
                process.start()
                started = True
                self.started.set()
            except OSError, RuntimeError:
                raise _ParserUnavailable() from None
            finally:
                send_conn.close()

            while True:
                if cancellation.is_cancelled():
                    raise _ParserCancelled()
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise _ParserTimedOut()

                if recv_conn.poll(min(_POLL_INTERVAL_SECONDS, remaining)):
                    if cancellation.is_cancelled():
                        raise _ParserCancelled()
                    if time.monotonic() >= deadline:
                        raise _ParserTimedOut()
                    message = _receive_message(recv_conn)
                    result = _message_result(message)
                    if process.is_alive():
                        _stop_process(process)
                    return result

                if not process.is_alive():
                    if recv_conn.poll(0):
                        continue
                    raise _ParserMalformed()
        finally:
            recv_conn.close()
            if started:
                if process.is_alive():
                    _stop_process(process)
                else:
                    process.join()


def _receive_message(recv_conn: _ReceiveConnection) -> _ParserMessage:
    """Прочитать только ожидаемый typed message и скрыть IPC failures."""

    try:
        message = recv_conn.recv()
    except EOFError, OSError, ValueError:
        raise _ParserMalformed() from None
    if not isinstance(message, _ParserMessage):
        raise _ParserMalformed()
    return message


def _message_result(message: _ParserMessage) -> NormalizedFeed:
    """Преобразовать закрытый worker status в безопасную application boundary error."""

    if message.status == "too_large":
        raise _ParserContentTooLarge()
    if message.status != "ok" or not isinstance(message.result, NormalizedFeed):
        raise _ParserMalformed()
    return message.result


def _stop_process(process: BaseProcess) -> None:
    """Terminate, затем kill только созданный этим parser worker child."""

    if not process.is_alive():
        process.join()
        return
    with suppress(OSError, ValueError):
        process.terminate()
    process.join(timeout=_TERMINATE_WAIT_SECONDS)
    if process.is_alive():
        with suppress(OSError, ValueError):
            process.kill()
        process.join(timeout=_KILL_WAIT_SECONDS)
    if process.is_alive():
        process.join()


__all__ = [
    "MultiprocessingParserWorker",
]
