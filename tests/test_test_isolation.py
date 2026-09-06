"""Regression guards for real-vault and live-network test isolation."""

from __future__ import annotations

import socket
import subprocess
import sys
from pathlib import Path

import pytest

from second_brain.adapters.vault.scanner import FileSystemVaultReader
from second_brain.adapters.vault.writer import FileSystemVaultWriter
from tests.conftest import IsolationViolation, assert_no_inherited_test_configuration


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


def test_network_process_attempt_is_rejected_before_launch() -> None:
    with pytest.raises(IsolationViolation, match=r"TEST_ISOLATION_VIOLATION:.*network-capable"):
        subprocess.Popen(["curl", "https://example.invalid"], stdout=subprocess.PIPE)


def test_local_process_remains_available_to_unit_tests() -> None:
    result = subprocess.run(
        [sys.executable, "-c", "print('local test process')"],
        capture_output=True,
        check=True,
        text=True,
    )
    assert result.returncode == 0
