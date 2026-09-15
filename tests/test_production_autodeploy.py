from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

REPOSITORY_ROOT = Path(__file__).parents[1]
AUTODEPLOY_SCRIPT = REPOSITORY_ROOT / "deploy" / "autodeploy.sh"
RELEASE_CONTROL = REPOSITORY_ROOT / "deploy" / "root" / "second-brain-release-control"
SYSTEMD_CONTRACT_CHECK = REPOSITORY_ROOT / "deploy" / "systemd-contract-check.sh"
RUNTIME_ENTRYPOINT_CHECK = REPOSITORY_ROOT / "deploy" / "runtime-entrypoint-check.sh"
AUTODEPLOY_WORKFLOW = REPOSITORY_ROOT / ".github" / "workflows" / "deploy-production.yml"


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


def test_autodeploy_script_has_strict_exact_sha_release_contract() -> None:
    script = AUTODEPLOY_SCRIPT.read_text(encoding="utf-8")

    assert script.startswith("#!/usr/bin/env bash\nset -Eeuo pipefail")
    for required in (
        '[[ "$TARGET_SHA" =~ ^[0-9a-f]{40}$ ]]',
        "flock -n 9",
        'run_git_fetch "$APP_ROOT" "second-brain"',
        'run_git_fetch "$VAULT_ROOT" "second-brain-vault"',
        'git -C "$path" fetch --no-tags origin main',
        "capture_filesystem_diagnostic",
        "df -Pk",
        "df -Pik",
        'merge-base --is-ancestor "$TARGET_SHA" "$ORIGIN_APP_SHA"',
        'git -C "$APP_ROOT" merge --ff-only origin/main',
        'git -C "$APP_ROOT" worktree add --detach',
        "uv sync --locked --python 3.14",
        "deploy/systemd-contract-check.sh",
        "deploy/runtime-entrypoint-check.sh",
        '"$CANDIDATE_ENTRYPOINT" --env-file "$RUNTIME_ENV" doctor',
        "npm ci",
        "npm run check",
        "npm run build",
        "npm run qa:pwa",
        "/usr/local/sbin/second-brain-release-control",
        '"$RELEASE_CONTROL" activate "$TARGET_SHA"',
        '"$RELEASE_CONTROL" rollback "$PREVIOUS_SHA"',
        "rollback_and_fail",
        "http://127.0.0.1:8123/healthz",
        "https://brain.mikemoore.top/healthz",
    ):
        assert required in script


def test_autodeploy_script_keeps_root_config_and_vault_fail_closed() -> None:
    script = AUTODEPLOY_SCRIPT.read_text(encoding="utf-8")

    assert "deploy/systemd deploy/caddy deploy/root" in script
    assert "SYSTEMD_CONTRACT_NOT_INTEGRATED / HUMAN_REQUIRED" in script
    assert "deploy/caddy deploy/root" in script
    assert "autodeploy его не обновляет" in script
    assert "первый production deploy выполняется owner-managed" in script
    assert '[[ -w "$SECOND_BRAIN_ROOT" ]]' not in script

    lowered = script.lower()
    for forbidden in (
        "git pull",
        "git reset --hard",
        "git clean",
        "git rebase",
        "rm -rf",
        "sudo install",
        "systemctl daemon-reload",
        "systemctl reload caddy",
        "caddy reload",
        "source ",
    ):
        assert forbidden not in lowered


def test_fetch_failure_diagnostic_is_bounded_read_only_and_precedes_candidate() -> None:
    script = AUTODEPLOY_SCRIPT.read_text(encoding="utf-8")

    wrapper = script.index("run_git_fetch()")
    app_fetch = script.index('run_git_fetch "$APP_ROOT" "second-brain"')
    vault_fetch = script.index('run_git_fetch "$VAULT_ROOT" "second-brain-vault"')
    candidate = script.index('CANDIDATE_RELEASE="$RELEASES_ROOT/$TARGET_SHA"')

    assert wrapper < app_fetch < vault_fetch < candidate
    for required in (
        "bounded_stderr=",
        "bounded_stdout=",
        "df_blocks=",
        "df_inodes=",
    ):
        assert required in script

    fetch_failure_start = script.index("else\n    fetch_exit=$?", wrapper)
    fetch_failure_end = script.index("\n}\n\nrun_git_status_probe", fetch_failure_start)
    failure_path = script[fetch_failure_start:fetch_failure_end]
    for forbidden in (
        "git gc",
        "git prune",
        "git reset",
        "git clean",
        "rm -rf",
        "worktree add",
        "activate",
    ):
        assert forbidden not in failure_path.casefold()


def test_release_control_is_narrow_root_owned_contract() -> None:
    helper = RELEASE_CONTROL.read_text(encoding="utf-8")

    assert helper.startswith("#!/usr/bin/env bash\nset -euo pipefail")
    for required in (
        "CONTRACT_VERSION=1",
        '[[ "$(id -u)" == "0" ]]',
        "activate|rollback",
        '[[ "$SHA" =~ ^[0-9a-f]{40}$ ]]',
        'TARGET="$RELEASES_ROOT/$SHA"',
        '/usr/bin/ln -s "releases/$SHA" "$NEXT_LINK"',
        '/usr/bin/mv -T -- "$NEXT_LINK" "$CURRENT_LINK"',
        '/usr/bin/systemctl restart "$SERVICE"',
    ):
        assert required in helper

    lowered = helper.lower()
    for forbidden in (
        "eval ",
        "bash -c",
        "sh -c",
        "git reset",
        "git clean",
        "rm -rf",
        "chmod",
        "chown",
        "caddy",
        "firewall",
    ):
        assert forbidden not in lowered


@pytest.mark.skipif(_bash_path() is None, reason="bash is required")
def test_deployment_shell_scripts_have_valid_bash_syntax() -> None:
    bash = _bash_path()
    assert bash is not None
    for script in (
        AUTODEPLOY_SCRIPT,
        RELEASE_CONTROL,
        SYSTEMD_CONTRACT_CHECK,
        RUNTIME_ENTRYPOINT_CHECK,
    ):
        completed = subprocess.run(
            [bash, "-n", str(script)],
            capture_output=True,
            text=True,
            check=False,
        )
        assert completed.returncode == 0, completed.stderr


def test_production_workflow_is_ci_gated_and_least_privilege() -> None:
    workflow = AUTODEPLOY_WORKFLOW.read_text(encoding="utf-8")

    for required in (
        'workflows: ["CI"]',
        "types: [completed]",
        "branches: [main]",
        "contents: read",
        "cancel-in-progress: false",
        "vars.PRODUCTION_DEPLOY_ENABLED == 'true'",
        "github.event.workflow_run.conclusion == 'success'",
        "github.event.workflow_run.event == 'push'",
        "github.event.workflow_run.head_branch == 'main'",
        "github.event.workflow_run.head_repository.full_name == github.repository",
        "environment: production",
        "persist-credentials: false",
        "StrictHostKeyChecking=yes",
        "\"bash -s -- --sha '$DEPLOY_SHA'\" < deploy/autodeploy.sh",
    ):
        assert required in workflow

    lowered = workflow.lower()
    assert "pull_request_target" not in lowered
    assert "ssh-keyscan" not in lowered
    assert "appleboy/" not in lowered
