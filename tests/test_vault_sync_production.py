"""Contract tests for the owner-triggered production vault sync operation."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any, cast

import pytest
from ruamel.yaml import YAML

PROJECT_ROOT = Path(__file__).resolve().parents[1]
WORKFLOW_PATH = PROJECT_ROOT / ".github" / "workflows" / "vault-sync-production.yml"
WRAPPER_PATH = PROJECT_ROOT / "deploy" / "vault-sync-production.sh"


def _bash_path() -> str | None:
    if os.name != "nt":
        return shutil.which("bash")
    for candidate in (
        Path("C:/Program Files/Git/bin/bash.exe"),
        Path("C:/Program Files/Git/usr/bin/bash.exe"),
    ):
        if candidate.is_file():
            return str(candidate)
    return None


def _run_bash(script: Path, *args: str) -> subprocess.CompletedProcess[str]:
    bash = _bash_path()
    if bash is None:
        pytest.skip("bash is required for shell wrapper tests")
    return subprocess.run(
        [bash, str(script), *args],
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        text=True,
        check=False,
    )


def _workflow_document() -> dict[str, Any]:
    parser = YAML(typ="safe")
    parser.version = (1, 2)
    loaded = parser.load(WORKFLOW_PATH.read_text(encoding="utf-8"))
    assert isinstance(loaded, dict)
    return cast(dict[str, Any], loaded)


def test_workflow_is_dispatch_only_with_a_secret_free_preflight() -> None:
    workflow_text = WORKFLOW_PATH.read_text(encoding="utf-8")
    workflow = _workflow_document()
    trigger = workflow["on"]
    assert isinstance(trigger, dict)
    assert set(trigger) == {"workflow_dispatch"}
    assert not re.search(
        r"^\s+(push|schedule|workflow_run|repository_dispatch):", workflow_text, re.MULTILINE
    )

    inputs = trigger["workflow_dispatch"]["inputs"]
    assert inputs["vault_sha"]["required"] is True
    assert inputs["vault_sha"]["type"] == "string"
    assert inputs["apply"]["required"] is True
    assert inputs["apply"]["default"] is False
    assert inputs["apply"]["type"] == "boolean"

    jobs = workflow["jobs"]
    preflight = jobs["preflight"]
    sync = jobs["sync"]
    assert "environment" not in preflight
    assert sync["environment"] == "production"
    assert sync["needs"] == "preflight"
    assert sync["if"] == "${{ needs.preflight.result == 'success' }}"

    preflight_text = workflow_text[
        workflow_text.index("  preflight:") : workflow_text.index("  sync:")
    ]
    assert "secrets." not in preflight_text
    assert "environment: production" not in preflight_text


def test_workflow_guards_inputs_before_ssh_and_reuses_existing_production_material() -> None:
    workflow = WORKFLOW_PATH.read_text(encoding="utf-8")

    for required in (
        'test "$GITHUB_EVENT_NAME" = "workflow_dispatch"',
        'test "$GITHUB_REPOSITORY" = "MikeMoore1337/second-brain"',
        'test "$GITHUB_REF" = "refs/heads/main"',
        (
            'test "$GITHUB_WORKFLOW_REF" = '
            '"MikeMoore1337/second-brain/.github/workflows/'
            'vault-sync-production.yml@refs/heads/main"'
        ),
        '[[ "$VAULT_SHA" =~ ^[0-9a-f]{40}$ ]]',
        'test "$APPLY" = "true"',
        'test "$PRODUCTION_VAULT_SYNC_ENABLED" = "true"',
        '[[ "$PRODUCTION_SSH_HOST" =~ ^[A-Za-z0-9._:-]+$ ]]',
        '[[ "$PRODUCTION_SSH_USER" =~ ^[A-Za-z_][A-Za-z0-9_-]*$ ]]',
        '[[ "$PRODUCTION_SSH_PORT" =~ ^[0-9]{1,5}$ ]]',
        "port_value=$((10#$PRODUCTION_SSH_PORT))",
        "group: second-brain-production",
        "cancel-in-progress: false",
        "vars.PRODUCTION_SSH_HOST",
        "vars.PRODUCTION_SSH_PORT",
        "vars.PRODUCTION_SSH_USER",
        "secrets.PRODUCTION_SSH_PRIVATE_KEY",
        "secrets.PRODUCTION_SSH_KNOWN_HOSTS",
        "persist-credentials: false",
        "BatchMode=yes",
        "IdentitiesOnly=yes",
        "StrictHostKeyChecking=yes",
        "UserKnownHostsFile=",
        "ConnectTimeout=15",
        "ServerAliveInterval=15",
        "ServerAliveCountMax=3",
        'bash -s -- --target-sha "$VAULT_SHA" < deploy/vault-sync-production.sh',
        "if: always()",
        'rm -f -- "$SSH_DIR/production_sync_key" "$SSH_DIR/known_hosts"',
    ):
        assert required in workflow

    assert workflow.index('test "$GITHUB_EVENT_NAME" = "workflow_dispatch"') < workflow.index(
        "Invoke exact-SHA vault sync over pinned SSH"
    )
    assert "PRODUCTION_DEPLOY_ENABLED" not in workflow
    assert "pull_request_target" not in workflow
    assert "github.token" not in workflow
    for forbidden in ("autodeploy.sh", "release-control", "systemctl", "caddy", "npm", "web.env"):
        assert forbidden not in workflow.casefold()


def test_wrapper_has_fixed_non_root_exact_sha_contract() -> None:
    wrapper = WRAPPER_PATH.read_text(encoding="utf-8")

    for required in (
        'readonly VAULT_ROOT="$SECOND_BRAIN_ROOT/second-brain-vault"',
        'readonly APP_ROOT="$SECOND_BRAIN_ROOT/current"',
        'readonly BACKUP_ROOT="$RUNTIME_ROOT/vault-backups"',
        'readonly LOCK_PATH="$RUNTIME_ROOT/vault-sync.lock"',
        'readonly PYTHON="$APP_ROOT/.venv/bin/python"',
        'readonly EXPECTED_BRANCH="main"',
        'readonly EXPECTED_REMOTE="https://github.com/MikeMoore1337/second-brain-vault.git"',
        '[[ "$TARGET_SHA" =~ ^[0-9a-f]{40}$ ]]',
        '[[ "$(uname -s)" == "Linux" ]]',
        '[[ "$(id -u)" != "0" ]]',
        "assert_production_layout",
        'exec "$PYTHON" \\',
        "-m second_brain.adapters.vault.sync",
        '--target-sha "$TARGET_SHA"',
        "--apply",
        "--format json",
    ):
        assert required in wrapper

    assert wrapper.index('[[ "$TARGET_SHA" =~ ^[0-9a-f]{40}$ ]]') < wrapper.rindex(
        "assert_production_layout"
    )
    lowered = wrapper.casefold()
    for forbidden in (
        "sudo",
        "systemctl",
        "autodeploy",
        "release-control",
        "web.env",
        "npm",
        "source ",
        "eval ",
        "git reset",
        "git clean",
        "git rebase",
        "ln -s",
        " mv ",
    ):
        assert forbidden not in lowered


@pytest.mark.skipif(_bash_path() is None, reason="bash is required")
@pytest.mark.parametrize("invalid_sha", ("A" * 40, "0" * 39, "g" * 40))
def test_wrapper_rejects_invalid_sha_before_any_fixed_path_check(invalid_sha: str) -> None:
    result = _run_bash(WRAPPER_PATH, "--target-sha", invalid_sha)

    assert result.returncode != 0
    assert "exact 40-character lowercase" in result.stderr
    assert "/srv/second-brain" not in result.stdout + result.stderr


@pytest.mark.skipif(_bash_path() is None, reason="bash is required")
def test_wrapper_has_valid_bash_syntax() -> None:
    bash = _bash_path()
    assert bash is not None
    result = subprocess.run(
        [bash, "-n", str(WRAPPER_PATH)],
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        text=True,
        check=False,
    )

    assert result.returncode == 0


def test_vault_sync_runbook_documents_owner_operation_and_recovery_contract() -> None:
    runbook = (PROJECT_ROOT / "docs" / "deployment" / "vault-sync.md").read_text(encoding="utf-8")

    for required in (
        ".github/workflows/vault-sync-production.yml",
        "workflow_dispatch",
        "vault_sha",
        "apply",
        "PRODUCTION_VAULT_SYNC_ENABLED",
        "PRODUCTION_DEPLOY_ENABLED",
        "NO_OP",
        "SYNCED",
        "HUMAN_REQUIRED",
        "FAILED",
        "second-brain-production",
        "/srv/second-brain/runtime/vault-sync.lock",
        "/srv/second-brain/runtime/vault-backups",
        "remote.origin.url",
        "insteadOf",
        "merge implementation PR #217 ничего не синхронизирует",
        "Manual exact-SHA owner prompt fallback",
    ):
        assert required in runbook
