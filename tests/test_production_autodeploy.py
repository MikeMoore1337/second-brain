from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

REPOSITORY_ROOT = Path(__file__).parents[1]
AUTODEPLOY_SCRIPT = REPOSITORY_ROOT / "deploy" / "autodeploy.sh"
AUTODEPLOY_WORKFLOW = REPOSITORY_ROOT / ".github" / "workflows" / "deploy-production.yml"


def test_autodeploy_script_has_strict_exact_sha_release_contract() -> None:
    script = AUTODEPLOY_SCRIPT.read_text(encoding="utf-8")

    assert script.startswith("#!/usr/bin/env bash\nset -Eeuo pipefail")
    for required in (
        '[[ "$TARGET_SHA" =~ ^[0-9a-f]{40}$ ]]',
        "flock -n 9",
        'git -C "$APP_ROOT" fetch --no-tags origin main',
        'git -C "$VAULT_ROOT" fetch --no-tags origin main',
        'merge-base --is-ancestor "$TARGET_SHA" "$ORIGIN_APP_SHA"',
        'git -C "$APP_ROOT" merge --ff-only origin/main',
        'git -C "$APP_ROOT" worktree add --detach',
        "uv sync --locked --python 3.14",
        "uv run --python 3.14 --no-sync second-brain",
        "npm ci",
        "npm run check",
        "npm run build",
        'mv -T -- "$NEXT_LINK" "$CURRENT_LINK"',
        "/usr/bin/sudo -n /usr/bin/systemctl restart second-brain-web.service",
        "rollback_and_fail",
        "http://127.0.0.1:8123/healthz",
        "https://brain.mikemoore.top/healthz",
    ):
        assert required in script


def test_autodeploy_script_keeps_root_config_and_vault_fail_closed() -> None:
    script = AUTODEPLOY_SCRIPT.read_text(encoding="utf-8")

    assert 'diff --quiet "$PREVIOUS_SHA" "$TARGET_SHA" -- deploy/systemd deploy/caddy' in script
    assert "autodeploy его не обновляет" in script
    assert "первый production deploy выполняется owner-managed" in script

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


@pytest.mark.skipif(shutil.which("bash") is None, reason="bash is required")
def test_autodeploy_script_has_valid_bash_syntax() -> None:
    completed = subprocess.run(
        ["bash", "-n", str(AUTODEPLOY_SCRIPT)],
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
