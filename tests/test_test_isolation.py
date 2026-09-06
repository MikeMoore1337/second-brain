"""Regression guards for real-vault and live-network test isolation."""

from __future__ import annotations

import http.client
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest

import second_brain.adapters.llm.cloudflare_workers_ai as cloudflare
from second_brain.adapters.vault.scanner import FileSystemVaultReader
from second_brain.adapters.vault.writer import FileSystemVaultWriter
from second_brain.application.llm import LlmRequest
from second_brain.application.ports import CancellationTokenSource, LlmBackendUnavailableError
from tests.conftest import (
    IsolationViolation,
    assert_no_inherited_test_configuration,
    create_vault,
    write_note,
)


def test_real_vault_path_is_rejected_without_touching_files() -> None:
    """A repository path must fail before either vault adapter touches it."""

    real_vault_candidate = Path(__file__).resolve().parents[1] / (
        ".test-real-vault-sentinel-never-created"
    )

    with pytest.raises(IsolationViolation, match=r"TEST_ISOLATION_VIOLATION:.*temporary"):
        FileSystemVaultReader(real_vault_candidate)
    with pytest.raises(IsolationViolation, match=r"TEST_ISOLATION_VIOLATION:.*temporary"):
        FileSystemVaultWriter(real_vault_candidate)
    assert not real_vault_candidate.exists()


def test_system_temp_path_outside_current_pytest_run_is_rejected(
    tmp_path_factory: pytest.TempPathFactory,
) -> None:
    outside_run_candidate = tmp_path_factory.getbasetemp().parent / (
        ".test-system-temp-sentinel-never-created"
    )

    with pytest.raises(IsolationViolation, match=r"TEST_ISOLATION_VIOLATION:.*current pytest"):
        FileSystemVaultReader(outside_run_candidate)
    assert not outside_run_candidate.exists()


def test_fixture_write_helper_rejects_parent_escape_before_writing(tmp_path: Path) -> None:
    vault = tmp_path / "vault"

    with pytest.raises(IsolationViolation, match=r"TEST_ISOLATION_VIOLATION:.*escapes"):
        write_note(vault, "../outside.md", "must not be written\n")
    assert not (tmp_path / "outside.md").exists()


def test_create_vault_stays_under_the_current_pytest_run(tmp_path: Path) -> None:
    vault = create_vault(tmp_path / "vault")
    assert vault.is_dir()
    assert (vault / "second-brain.yaml").is_file()


def test_inherited_vault_configuration_is_rejected_deterministically(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SECOND_BRAIN_VAULT_PATH", "D:/real-vault")

    with pytest.raises(
        IsolationViolation, match=r"TEST_ISOLATION_VIOLATION:.*SECOND_BRAIN_VAULT_PATH"
    ):
        assert_no_inherited_test_configuration()


def test_live_socket_attempt_is_rejected_before_dns_or_connect() -> None:
    with pytest.raises(IsolationViolation, match=r"TEST_ISOLATION_VIOLATION:.*live network"):
        socket.create_connection(("example.invalid", 443))


def test_external_dns_resolution_is_rejected_before_getaddrinfo() -> None:
    with pytest.raises(IsolationViolation, match=r"TEST_ISOLATION_VIOLATION:.*live network"):
        socket.getaddrinfo("example.invalid", 443)


def test_loopback_dns_and_high_level_connections_remain_available() -> None:
    records = socket.getaddrinfo("127.0.0.1", 0)
    assert records

    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    try:
        address = listener.getsockname()
        client = socket.create_connection(address, timeout=1)
        accepted, _peer = listener.accept()
        client.close()
        accepted.close()

        connection = http.client.HTTPConnection("127.0.0.1", address[1], timeout=1)
        connection.connect()
        accepted, _peer = listener.accept()
        connection.close()
        accepted.close()
    finally:
        listener.close()


def test_external_http_connection_is_rejected_before_dns() -> None:
    with pytest.raises(IsolationViolation, match=r"TEST_ISOLATION_VIOLATION:.*live network"):
        http.client.HTTPConnection("example.invalid", 443).connect()


def test_network_process_attempt_is_rejected_before_launch() -> None:
    with pytest.raises(IsolationViolation, match=r"TEST_ISOLATION_VIOLATION:.*network-capable"):
        subprocess.Popen(["curl", "https://example.invalid"], stdout=subprocess.PIPE)


@pytest.mark.parametrize(
    "command",
    [
        ["git", "fetch", "https://example.invalid/repository.git"],
        ["git", "ls-remote", "https://example.invalid/repository.git"],
        ["git", "push", "https://example.invalid/repository.git", "main"],
        ["gh", "pr", "create"],
    ],
    ids=["git-fetch", "git-ls-remote", "git-push", "gh-pr-create"],
)
def test_networked_git_and_github_processes_are_rejected_before_launch(
    command: list[str],
) -> None:
    with pytest.raises(IsolationViolation, match=r"TEST_ISOLATION_VIOLATION:.*network-capable"):
        subprocess.run(command, check=False)


def test_production_provider_runner_cannot_launch_worker_in_ordinary_tests() -> None:
    port = cloudflare.CloudflareWorkersAiLlmPort(
        config=cloudflare.CloudflareWorkersAiConfig("account", "token")
    )
    assert isinstance(port.runner, cloudflare.SubprocessWorkerRunner)

    with pytest.raises(LlmBackendUnavailableError):
        port.draft_note(
            LlmRequest(instruction="Сформируй bounded fixture."),
            cancellation=CancellationTokenSource(),
        )


def test_production_worker_runner_seam_is_guarded_before_process_launch() -> None:
    runner = cloudflare.SubprocessWorkerRunner()

    with pytest.raises(IsolationViolation, match=r"TEST_ISOLATION_VIOLATION:.*network-capable"):
        runner.run(
            cloudflare._WorkerRequest("account", "token", b"{}", 1024),
            deadline=time.monotonic() + 1,
            cancellation=CancellationTokenSource(),
        )


def test_local_process_remains_available_to_unit_tests() -> None:
    result = subprocess.run(
        [sys.executable, "-c", "print('local test process')"],
        capture_output=True,
        check=True,
        text=True,
    )
    assert result.returncode == 0
