"""Cross-platform advisory lock for coordinated vault operations."""

from __future__ import annotations

import errno
import json
import os
from contextlib import AbstractContextManager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, BinaryIO, Self


class VaultOperationLockError(RuntimeError):
    """The configured operation lock cannot be opened or released safely."""


class VaultOperationBusy(VaultOperationLockError):
    """Another process currently owns the advisory operation lock."""


class VaultOperationLock(AbstractContextManager["VaultOperationLock"]):
    """Hold one non-blocking process lock without deleting stale lock files.

    The file is deliberately retained after release.  Kernel lock ownership is
    the source of truth; the small JSON payload is operator context only and is
    replaced after a successful acquisition.  This makes an old payload safe
    to recover from without ever removing a lock file based on a guessed PID.
    """

    def __init__(self, path: Path, *, operation: str) -> None:
        if (
            type(operation) is not str
            or not operation
            or len(operation) > 64
            or any(ord(character) < 32 or character.isspace() for character in operation)
        ):
            raise VaultOperationLockError("operation lock name is invalid")
        self.path = path
        self.operation = operation
        self._file: BinaryIO | None = None

    def __enter__(self) -> Self:
        self._acquire()
        return self

    def __exit__(self, exc_type: object, exc_value: object, traceback: object) -> None:
        del exc_type, exc_value, traceback
        self._release()

    def _acquire(self) -> None:
        try:
            parent = self.path.parent.resolve(strict=True)
        except (OSError, RuntimeError, ValueError) as exc:
            raise VaultOperationLockError("operation lock parent is unavailable") from exc
        if not parent.is_dir() or self.path.name in {"", ".", ".."}:
            raise VaultOperationLockError("operation lock parent is not a directory")

        if os.path.lexists(self.path) and self.path.is_symlink():
            raise VaultOperationLockError("operation lock path must not be a symlink")

        flags = os.O_RDWR | os.O_CREAT
        if os.name != "nt":
            flags |= getattr(os, "O_NOFOLLOW", 0)
        try:
            descriptor = os.open(self.path, flags, 0o600)
            file = os.fdopen(descriptor, "r+b", buffering=0)
        except OSError as exc:
            raise VaultOperationLockError("operation lock file is unavailable") from exc

        self._file = file
        try:
            try:
                os.chmod(self.path, 0o600)
            except OSError as exc:
                raise VaultOperationLockError("operation lock permissions are unsafe") from exc
            self._try_kernel_lock()
            self._write_metadata()
        except BaseException:
            self._close_without_unlock()
            raise

    def _try_kernel_lock(self) -> None:
        assert self._file is not None
        if os.name == "nt":
            import msvcrt

            msvcrt_module: Any = msvcrt
            self._file.seek(0, os.SEEK_END)
            if self._file.tell() == 0:
                self._file.write(b"\0")
                self._file.flush()
            self._file.seek(0)
            try:
                msvcrt_module.locking(self._file.fileno(), msvcrt_module.LK_NBLCK, 1)
            except OSError as exc:
                raise VaultOperationBusy("another vault operation is active") from exc
            return

        import fcntl

        lock_module: Any = fcntl

        try:
            lock_module.flock(self._file.fileno(), lock_module.LOCK_EX | lock_module.LOCK_NB)
        except OSError as exc:
            if exc.errno in {errno.EACCES, errno.EAGAIN}:
                raise VaultOperationBusy("another vault operation is active") from exc
            raise VaultOperationLockError("kernel operation lock failed") from exc

    def _write_metadata(self) -> None:
        assert self._file is not None
        payload = json.dumps(
            {
                "schema_version": 1,
                "operation": self.operation,
                "pid": os.getpid(),
                "started_at": datetime.now(UTC).isoformat(timespec="seconds"),
            },
            ensure_ascii=True,
            sort_keys=True,
        ).encode("utf-8")
        self._file.seek(0)
        self._file.truncate()
        self._file.write(payload)
        self._file.flush()
        os.fsync(self._file.fileno())

    def _release(self) -> None:
        file = self._file
        self._file = None
        if file is None:
            return
        try:
            if os.name == "nt":
                import msvcrt

                msvcrt_module: Any = msvcrt
                file.seek(0)
                msvcrt_module.locking(file.fileno(), msvcrt_module.LK_UNLCK, 1)
            else:
                import fcntl

                lock_module: Any = fcntl

                lock_module.flock(file.fileno(), lock_module.LOCK_UN)
        except OSError as exc:
            raise VaultOperationLockError("operation lock release failed") from exc
        finally:
            file.close()

    def _close_without_unlock(self) -> None:
        file = self._file
        self._file = None
        if file is not None:
            file.close()
