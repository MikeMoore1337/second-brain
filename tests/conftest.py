"""Общие builders и границы изоляции тестов."""

from __future__ import annotations

import http.client
import ipaddress
import os
import shlex
import socket
import subprocess
import tempfile
import urllib.request
from collections.abc import Iterator, Sequence
from pathlib import Path
from typing import Any, Final, NoReturn

import pytest

from second_brain.adapters.vault.scanner import FileSystemVaultReader
from second_brain.adapters.vault.writer import FileSystemVaultWriter

ISOLATION_ERROR_PREFIX: Final = "TEST_ISOLATION_VIOLATION:"
LIVE_SMOKE_ENV: Final = "SECOND_BRAIN_ALLOW_LIVE_SMOKE"
_NETWORK_EXECUTABLES: Final = frozenset({"curl", "curl.exe", "yt-dlp", "yt-dlp.exe"})
_FORBIDDEN_INHERITED_ENVIRONMENT: Final = ("SECOND_BRAIN_VAULT_PATH",)
_SECRET_ENVIRONMENT_NAMES: Final = (
    "CLOUDFLARE_ACCOUNT_ID",
    "CLOUDFLARE_API_TOKEN",
    "OPENAI_API_KEY",
    "GH_TOKEN",
    "GITHUB_TOKEN",
    "HTTP_PROXY",
    "HTTPS_PROXY",
    "ALL_PROXY",
    "CURL_HOME",
)


class IsolationViolation(RuntimeError):
    """Безопасный и детерминированный отказ на границе тестовой изоляции."""


def assert_no_inherited_test_configuration() -> None:
    """Запретить обычным тестам унаследованную конфигурацию реального vault."""

    for name in _FORBIDDEN_INHERITED_ENVIRONMENT:
        if os.environ.get(name, "").strip():
            raise IsolationViolation(
                f"{ISOLATION_ERROR_PREFIX} inherited {name} is forbidden; "
                "use pytest tmp_path/create_vault"
            )


def _command_arguments(command: object) -> tuple[str, ...]:
    if command is None:
        return ()
    if isinstance(command, bytes):
        return (command.decode(errors="replace"),)
    if isinstance(command, str):
        try:
            return tuple(shlex.split(command, posix=os.name != "nt"))
        except ValueError:
            return (command,)
    if isinstance(command, os.PathLike):
        return (os.fspath(command).__str__(),)
    if isinstance(command, Sequence):
        arguments: list[str] = []
        for argument in command:
            if isinstance(argument, bytes):
                arguments.append(argument.decode(errors="replace"))
            else:
                arguments.append(str(argument))
        return tuple(arguments)
    return ()


def _executable_name(argument: str) -> str:
    return argument.replace("\\", "/").rsplit("/", 1)[-1].casefold()


def _is_forbidden_network_process(command: object) -> bool:
    arguments = _command_arguments(command)
    if not arguments or _executable_name(arguments[0]) not in _NETWORK_EXECUTABLES:
        return False
    return arguments[1:] != ("--version",)


def _blocked_network(*_args: object, **_kwargs: object) -> NoReturn:
    raise IsolationViolation(
        f"{ISOLATION_ERROR_PREFIX} live network is forbidden in ordinary tests; "
        "use a fake seam or an explicitly authorized live_smoke test"
    )


def _is_loopback_address(address: object) -> bool:
    if isinstance(address, tuple) and address:
        candidate = address[0]
    elif isinstance(address, str):
        candidate = address
    else:
        return False
    if candidate == "localhost":
        return True
    try:
        return ipaddress.ip_address(str(candidate)).is_loopback
    except ValueError:
        return False


_ORIGINAL_SOCKET_CONNECT: Any = socket.socket.connect
_ORIGINAL_SOCKET_CONNECT_EX: Any = socket.socket.connect_ex


def _guard_socket_connect(
    self: socket.socket, address: object, *args: object, **kwargs: object
) -> Any:
    if _is_loopback_address(address):
        return _ORIGINAL_SOCKET_CONNECT(self, address, *args, **kwargs)
    return _blocked_network()


def _guard_socket_connect_ex(
    self: socket.socket, address: object, *args: object, **kwargs: object
) -> Any:
    if _is_loopback_address(address):
        return _ORIGINAL_SOCKET_CONNECT_EX(self, address, *args, **kwargs)
    return _blocked_network()


def _ensure_temporary_vault(root: Path) -> None:
    try:
        resolved_root = root.expanduser().resolve(strict=False)
        temporary_root = Path(tempfile.gettempdir()).resolve(strict=False)
        resolved_root.relative_to(temporary_root)
    except (OSError, ValueError) as exc:
        raise IsolationViolation(
            f"{ISOLATION_ERROR_PREFIX} vault access requires a pytest temporary path; "
            "use tmp_path/create_vault"
        ) from exc


def _guard_vault_reader_init(self: FileSystemVaultReader, root: Path) -> None:
    _ensure_temporary_vault(root)
    _ORIGINAL_VAULT_READER_INIT(self, root)


def _guard_vault_writer_init(self: FileSystemVaultWriter, root: Path) -> None:
    _ensure_temporary_vault(root)
    _ORIGINAL_VAULT_WRITER_INIT(self, root)


_ORIGINAL_VAULT_READER_INIT = FileSystemVaultReader.__init__
_ORIGINAL_VAULT_WRITER_INIT = FileSystemVaultWriter.__init__


def _guard_subprocess_call(command: object) -> None:
    if _is_forbidden_network_process(command):
        raise IsolationViolation(
            f"{ISOLATION_ERROR_PREFIX} network-capable process is forbidden in ordinary tests; "
            "use a fake runner or an explicitly authorized live_smoke test"
        )


@pytest.fixture(autouse=True)
def _isolate_test_boundaries(
    request: pytest.FixtureRequest,
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[None]:
    """Закрыть реальные vault/network/provider boundaries для каждого теста."""

    assert_no_inherited_test_configuration()
    live_smoke_authorized = (
        request.node.get_closest_marker("live_smoke") is not None
        and os.environ.get(LIVE_SMOKE_ENV) == "1"
    )
    if not live_smoke_authorized:
        for name in _SECRET_ENVIRONMENT_NAMES:
            monkeypatch.delenv(name, raising=False)

        original_popen = subprocess.Popen
        original_run = subprocess.run

        def guarded_popen(*args: Any, **kwargs: Any) -> subprocess.Popen[Any]:
            command = args[0] if args else kwargs.get("args")
            _guard_subprocess_call(command)
            return original_popen(*args, **kwargs)

        def guarded_run(*args: Any, **kwargs: Any) -> subprocess.CompletedProcess[Any]:
            command = args[0] if args else kwargs.get("args")
            _guard_subprocess_call(command)
            return original_run(*args, **kwargs)

        monkeypatch.setattr(subprocess, "Popen", guarded_popen)
        monkeypatch.setattr(subprocess, "run", guarded_run)
        monkeypatch.setattr(socket.socket, "connect", _guard_socket_connect)
        monkeypatch.setattr(socket.socket, "connect_ex", _guard_socket_connect_ex)
        monkeypatch.setattr(socket, "create_connection", _blocked_network)
        monkeypatch.setattr(urllib.request, "urlopen", _blocked_network)
        monkeypatch.setattr(http.client.HTTPConnection, "connect", _blocked_network)
        monkeypatch.setattr(http.client.HTTPSConnection, "connect", _blocked_network)

    monkeypatch.setattr(FileSystemVaultReader, "__init__", _guard_vault_reader_init)
    monkeypatch.setattr(FileSystemVaultWriter, "__init__", _guard_vault_writer_init)
    yield


def pytest_configure(config: pytest.Config) -> None:
    """Зарегистрировать явный маркер для отдельного live-smoke запуска."""

    config.addinivalue_line(
        "markers",
        "live_smoke: explicitly authorized live network/provider smoke, excluded from ordinary CI",
    )


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    """Не запускать live smoke в обычном тестовом процессе."""

    del config
    if os.environ.get(LIVE_SMOKE_ENV) == "1":
        return
    reason = (
        f"{ISOLATION_ERROR_PREFIX} live_smoke requires "
        f"{LIVE_SMOKE_ENV}=1 and an explicit pytest -m live_smoke command"
    )
    skip_live_smoke = pytest.mark.skip(reason=reason)
    for item in items:
        if item.get_closest_marker("live_smoke") is not None:
            item.add_marker(skip_live_smoke)


VALID_VAULT_ID = "0198f4c5-6a00-7000-8000-000000000001"
VALID_NOTE_ID = "0198f4c5-6a00-7000-8000-000000000002"
SECOND_NOTE_ID = "0198f4c5-6a00-7000-8000-000000000003"

VAULT_DIRECTORIES = (
    "00 Inbox",
    "10 Projects",
    "20 Areas",
    "30 Resources",
    "40 Zettelkasten",
    "90 Archive",
    "_templates",
    "_attachments",
)


def create_vault(root: Path) -> Path:
    """Создать минимальный vault contract v1 в каталоге теста."""

    root.mkdir(parents=True, exist_ok=True)
    for directory in VAULT_DIRECTORIES:
        (root / directory).mkdir()
    (root / "second-brain.yaml").write_text(
        f"""schema_version: 1
vault_id: {VALID_VAULT_ID}
default_language: ru
paths:
  inbox: 00 Inbox
  projects: 10 Projects
  areas: 20 Areas
  resources: 30 Resources
  zettelkasten: 40 Zettelkasten
  archive: 90 Archive
  templates: _templates
  attachments: _attachments
attachments:
  warning_size_bytes: 10485760
  max_size_bytes: 52428800
""",
        encoding="utf-8",
    )
    return root


def write_note(root: Path, relative_path: str, text: str) -> Path:
    """Записать одну тестовую Markdown-запись."""

    path = root / relative_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def managed_note(note_id: str = VALID_NOTE_ID, note_type: str = "zettel") -> str:
    """Вернуть минимальную валидную managed note."""

    return f"""---
id: {note_id}
type: {note_type}
created: 2026-09-02T12:00:00+03:00
tags: []
---
# Тестовая заметка

Текст заметки.
"""


def snapshot_tree(root: Path) -> dict[str, bytes]:
    """Снять байтовый snapshot всех файлов fixture vault."""

    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }
