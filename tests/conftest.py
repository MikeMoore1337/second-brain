"""Общие builders и границы изоляции тестов."""

from __future__ import annotations

import http.client
import ipaddress
import os
import shlex
import socket
import subprocess
import sys
import tempfile
import urllib.parse
import urllib.request
from collections.abc import Iterator, Sequence
from contextvars import ContextVar
from pathlib import Path
from typing import Any, Final, NoReturn

import pytest

from second_brain.adapters.llm.cloudflare_workers_ai import SubprocessWorkerRunner
from second_brain.adapters.vault.scanner import FileSystemVaultReader
from second_brain.adapters.vault.writer import FileSystemVaultWriter

ISOLATION_ERROR_PREFIX: Final = "TEST_ISOLATION_VIOLATION:"
LIVE_SMOKE_ENV: Final = "SECOND_BRAIN_ALLOW_LIVE_SMOKE"
_NETWORK_EXECUTABLES: Final = frozenset({"curl", "curl.exe", "yt-dlp", "yt-dlp.exe"})
_NETWORKED_GIT_COMMANDS: Final = frozenset(
    {"clone", "fetch", "ls-remote", "pull", "push", "submodule"}
)
_PROVIDER_WORKER_MODULE: Final = "second_brain.adapters.llm.cloudflare_workers_ai_worker"
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
_ACTIVE_PYTEST_TEMP_ROOT: ContextVar[Path | None] = ContextVar(
    "second_brain_active_pytest_temp_root", default=None
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


def _git_config_remote_urls(cwd: object) -> tuple[str, ...]:
    """Read local Git remote URLs without invoking Git itself."""

    root = Path.cwd() if cwd is None else Path(str(cwd))
    git_path = root / ".git"
    if not git_path.is_dir():
        return ()
    config_path = git_path / "config"
    try:
        lines = config_path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return ()
    urls: list[str] = []
    in_remote = False
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("["):
            in_remote = stripped.casefold().startswith('[remote "')
        elif in_remote and stripped.casefold().startswith("url") and "=" in stripped:
            urls.append(stripped.split("=", 1)[1].strip())
    return tuple(urls)


def _is_local_git_reference(reference: str, *, cwd: object) -> bool:
    """Allow only filesystem Git remotes in ordinary tests."""

    value = reference.strip()
    is_windows_path = len(value) >= 3 and value[1] == ":" and value[2] in {"/", "\\"}
    parsed = urllib.parse.urlsplit("") if is_windows_path else urllib.parse.urlsplit(value)
    if parsed.scheme and parsed.scheme.casefold() != "file":
        return False
    if value.startswith(("git@", "ssh:")):
        return False
    if parsed.scheme.casefold() == "file":
        candidate = Path(urllib.request.url2pathname(parsed.path))
    else:
        candidate = Path(value)
    if not candidate.is_absolute():
        candidate = (Path.cwd() if cwd is None else Path(str(cwd))) / candidate
    try:
        return candidate.expanduser().resolve(strict=False).exists()
    except OSError:
        return False


def _is_local_git_command(arguments: tuple[str, ...], *, cwd: object) -> bool:
    """Permit local fixture repositories while blocking credentialed Git network use."""

    try:
        command_index = next(
            index
            for index, argument in enumerate(arguments[1:], start=1)
            if argument.casefold() in _NETWORKED_GIT_COMMANDS
        )
    except StopIteration:
        return True
    command = arguments[command_index].casefold()
    following = [
        argument for argument in arguments[command_index + 1 :] if not argument.startswith("-")
    ]
    if command == "clone":
        return bool(following) and _is_local_git_reference(following[0], cwd=cwd)
    for reference in following:
        if (
            "://" in reference
            or reference.startswith(("git@", "ssh:"))
            or (len(reference) >= 3 and reference[1] == ":" and reference[2] in {"/", "\\"})
        ) and not _is_local_git_reference(reference, cwd=cwd):
            return False
    remote_urls = _git_config_remote_urls(cwd)
    if not remote_urls:
        return False
    return all(_is_local_git_reference(url, cwd=cwd) for url in remote_urls)


def _is_forbidden_network_process(command: object, *, cwd: object = None) -> bool:
    arguments = _command_arguments(command)
    if not arguments:
        return False
    executable = _executable_name(arguments[0])
    if executable in _NETWORK_EXECUTABLES:
        return arguments[1:] != ("--version",)
    if executable in {"gh", "gh.exe"}:
        return arguments[1:] != ("--version",)
    if executable in {"git", "git.exe"}:
        return not _is_local_git_command(arguments, cwd=cwd)
    return False


def _is_provider_worker_process(command: object) -> bool:
    """Recognize the real provider worker before it can perform HTTPS egress."""

    arguments = _command_arguments(command)
    return (
        any(argument == _PROVIDER_WORKER_MODULE for argument in arguments[1:]) and "-m" in arguments
    )


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
    if isinstance(candidate, bytes):
        candidate = candidate.decode(errors="replace")
    if candidate == "localhost":
        return True
    try:
        return ipaddress.ip_address(str(candidate)).is_loopback
    except ValueError:
        return False


_ORIGINAL_SOCKET_CONNECT: Any = socket.socket.connect
_ORIGINAL_SOCKET_CONNECT_EX: Any = socket.socket.connect_ex
_ORIGINAL_SOCKET_GETADDRINFO: Any = socket.getaddrinfo
_ORIGINAL_SOCKET_CREATE_CONNECTION: Any = socket.create_connection
_ORIGINAL_URLOPEN: Any = urllib.request.urlopen
_ORIGINAL_HTTP_CONNECT: Any = http.client.HTTPConnection.connect
_ORIGINAL_HTTPS_CONNECT: Any = http.client.HTTPSConnection.connect


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


def _guard_getaddrinfo(
    host: object,
    port: object,
    *args: object,
    **kwargs: object,
) -> Any:
    if host is None or _is_loopback_address(host):
        return _ORIGINAL_SOCKET_GETADDRINFO(host, port, *args, **kwargs)
    return _blocked_network()


def _guard_create_connection(address: object, *args: object, **kwargs: object) -> Any:
    if _is_loopback_address(address):
        return _ORIGINAL_SOCKET_CREATE_CONNECTION(address, *args, **kwargs)
    return _blocked_network()


def _guard_http_connect(self: http.client.HTTPConnection, *args: object, **kwargs: object) -> Any:
    if _is_loopback_address((self.host, self.port)):
        return _ORIGINAL_HTTP_CONNECT(self, *args, **kwargs)
    return _blocked_network()


def _guard_https_connect(self: http.client.HTTPSConnection, *args: object, **kwargs: object) -> Any:
    if _is_loopback_address((self.host, self.port)):
        return _ORIGINAL_HTTPS_CONNECT(self, *args, **kwargs)
    return _blocked_network()


def _urlopen_host(url: object) -> str | None:
    raw_url = url.full_url if isinstance(url, urllib.request.Request) else url
    try:
        return urllib.parse.urlsplit(str(raw_url)).hostname
    except ValueError:
        return None


def _guard_urlopen(url: object, *args: object, **kwargs: object) -> Any:
    if _is_loopback_address(_urlopen_host(url)):
        return _ORIGINAL_URLOPEN(url, *args, **kwargs)
    return _blocked_network()


def _ensure_temporary_vault(root: Path) -> Path:
    temporary_root = _ACTIVE_PYTEST_TEMP_ROOT.get()
    if temporary_root is None:
        raise IsolationViolation(
            f"{ISOLATION_ERROR_PREFIX} vault access requires an active pytest temporary path"
        )
    try:
        resolved_root = root.expanduser().resolve(strict=False)
        resolved_root.relative_to(temporary_root)
    except (OSError, ValueError) as exc:
        raise IsolationViolation(
            f"{ISOLATION_ERROR_PREFIX} vault access requires the current pytest temporary root; "
            "use tmp_path/create_vault"
        ) from exc
    return resolved_root


def _ensure_fixture_child(root: Path, relative_path: str) -> Path:
    resolved_root = _ensure_temporary_vault(root)
    try:
        candidate = (resolved_root / relative_path).resolve(strict=False)
        candidate.relative_to(resolved_root)
    except (OSError, ValueError) as exc:
        raise IsolationViolation(
            f"{ISOLATION_ERROR_PREFIX} fixture path escapes its pytest vault root"
        ) from exc
    return candidate


def _guard_vault_reader_init(self: FileSystemVaultReader, root: Path) -> None:
    _ensure_temporary_vault(root)
    _ORIGINAL_VAULT_READER_INIT(self, root)


def _guard_vault_writer_init(self: FileSystemVaultWriter, root: Path) -> None:
    _ensure_temporary_vault(root)
    _ORIGINAL_VAULT_WRITER_INIT(self, root)


_ORIGINAL_VAULT_READER_INIT = FileSystemVaultReader.__init__
_ORIGINAL_VAULT_WRITER_INIT = FileSystemVaultWriter.__init__


def _guard_subprocess_call(command: object, *, cwd: object = None) -> None:
    if _is_forbidden_network_process(command, cwd=cwd) or _is_provider_worker_process(command):
        raise IsolationViolation(
            f"{ISOLATION_ERROR_PREFIX} network-capable process is forbidden in ordinary tests; "
            "use a fake runner or an explicitly authorized live_smoke test"
        )


def _explicit_live_smoke_selector(config: pytest.Config) -> bool:
    """Require the exact ``pytest -m live_smoke`` selector, not env alone."""

    mark_expression = str(getattr(config.option, "markexpr", "") or "").strip()
    return mark_expression == "live_smoke"


@pytest.fixture(autouse=True)
def _isolate_test_boundaries(
    request: pytest.FixtureRequest,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path_factory: pytest.TempPathFactory,
) -> Iterator[None]:
    """Закрыть реальные vault/network/provider boundaries для каждого теста."""

    temporary_root = tmp_path_factory.getbasetemp().expanduser().resolve(strict=False)
    temp_root_token = _ACTIVE_PYTEST_TEMP_ROOT.set(temporary_root)
    try:
        assert_no_inherited_test_configuration()
        live_smoke_authorized = (
            request.node.get_closest_marker("live_smoke") is not None
            and os.environ.get(LIVE_SMOKE_ENV) == "1"
            and _explicit_live_smoke_selector(request.config)
        )
        if not live_smoke_authorized:
            for name in _SECRET_ENVIRONMENT_NAMES:
                monkeypatch.delenv(name, raising=False)

            original_popen = subprocess.Popen
            original_run = subprocess.run
            original_temporary_directory = tempfile.TemporaryDirectory

            def guarded_temporary_directory(*args: Any, **kwargs: Any) -> Any:
                kwargs.setdefault("dir", temporary_root)
                return original_temporary_directory(*args, **kwargs)

            def guarded_popen(*args: Any, **kwargs: Any) -> subprocess.Popen[Any]:
                command = args[0] if args else kwargs.get("args")
                _guard_subprocess_call(command, cwd=kwargs.get("cwd"))
                return original_popen(*args, **kwargs)

            def guarded_run(*args: Any, **kwargs: Any) -> subprocess.CompletedProcess[Any]:
                command = args[0] if args else kwargs.get("args")
                _guard_subprocess_call(command, cwd=kwargs.get("cwd"))
                return original_run(*args, **kwargs)

            original_worker_runner_init = SubprocessWorkerRunner.__init__

            def guarded_worker_runner_init(
                self: SubprocessWorkerRunner,
                *args: Any,
                **kwargs: Any,
            ) -> None:
                """Route production runner defaults through the ordinary-test guard."""

                if args and args[0] is original_popen:
                    args = (guarded_popen, *args[1:])
                elif kwargs.get("popen_factory") is original_popen or (
                    not args and "popen_factory" not in kwargs
                ):
                    kwargs["popen_factory"] = guarded_popen
                original_worker_runner_init(self, *args, **kwargs)

            monkeypatch.setattr(subprocess, "Popen", guarded_popen)
            monkeypatch.setattr(subprocess, "run", guarded_run)
            monkeypatch.setattr(tempfile, "TemporaryDirectory", guarded_temporary_directory)
            for benchmark_module_name in (
                "second_brain.benchmarks.lexical_gap_v1",
                "second_brain.benchmarks.rebuild_cost_v1",
            ):
                benchmark_module = sys.modules.get(benchmark_module_name)
                if benchmark_module is not None:
                    monkeypatch.setattr(
                        benchmark_module,
                        "TemporaryDirectory",
                        guarded_temporary_directory,
                    )
            monkeypatch.setattr(SubprocessWorkerRunner, "__init__", guarded_worker_runner_init)
            monkeypatch.setattr(socket.socket, "connect", _guard_socket_connect)
            monkeypatch.setattr(socket.socket, "connect_ex", _guard_socket_connect_ex)
            monkeypatch.setattr(socket, "getaddrinfo", _guard_getaddrinfo)
            monkeypatch.setattr(socket, "create_connection", _guard_create_connection)
            monkeypatch.setattr(urllib.request, "urlopen", _guard_urlopen)
            monkeypatch.setattr(http.client.HTTPConnection, "connect", _guard_http_connect)
            monkeypatch.setattr(http.client.HTTPSConnection, "connect", _guard_https_connect)

        monkeypatch.setattr(FileSystemVaultReader, "__init__", _guard_vault_reader_init)
        monkeypatch.setattr(FileSystemVaultWriter, "__init__", _guard_vault_writer_init)
        yield
    finally:
        _ACTIVE_PYTEST_TEMP_ROOT.reset(temp_root_token)


def pytest_configure(config: pytest.Config) -> None:
    """Зарегистрировать явный маркер для отдельного live-smoke запуска."""

    config.addinivalue_line(
        "markers",
        "live_smoke: explicitly authorized live network/provider smoke, excluded from ordinary CI",
    )


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    """Не запускать live smoke в обычном тестовом процессе."""

    live_smoke_selected = os.environ.get(LIVE_SMOKE_ENV) == "1" and _explicit_live_smoke_selector(
        config
    )
    if live_smoke_selected:
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

    root = _ensure_temporary_vault(root)
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

    path = _ensure_fixture_child(root, relative_path)
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

    root = _ensure_temporary_vault(root)
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }
