from __future__ import annotations

from pathlib import Path

REPOSITORY_ROOT = Path(__file__).parents[1]
DEPLOY_ROOT = REPOSITORY_ROOT / "deploy"


def test_bootstrap_script_has_strict_runtime_contract() -> None:
    script = (DEPLOY_ROOT / "bootstrap.sh").read_text(encoding="utf-8")

    assert script.startswith("#!/usr/bin/env bash\nset -euo pipefail")
    for required in (
        '"$(uname -s)" == "Linux"',
        '"$(id -u)" != "0"',
        "stat -c '%u'",
        "validate_env_file",
        "uv python find 3.14",
        "uv python install 3.14",
        "uv sync --locked --python 3.14",
        "uv run --python 3.14 second-brain",
        "doctor",
        "vault validate",
        "gh auth status",
        "--env-file",
    ):
        assert required in script


def test_deployment_script_has_no_destructive_or_out_of_scope_commands() -> None:
    script = (DEPLOY_ROOT / "bootstrap.sh").read_text(encoding="utf-8").lower()

    for forbidden in (
        "git reset --hard",
        "git clean -fd",
        "git pull",
        "git rebase",
        "force push",
        "sudo",
        "docker",
        "systemctl",
        "caddy",
        "nginx",
        "curl | sh",
        "curl | bash",
        "source ",
    ):
        assert forbidden not in script


def test_deployment_env_example_contains_only_non_secret_vault_setting() -> None:
    env_example = (DEPLOY_ROOT / "env.example").read_text(encoding="utf-8")
    assignments = [
        line.strip()
        for line in env_example.splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]

    assert assignments == ["SECOND_BRAIN_VAULT_PATH=../second-brain-vault"]
    assert "ghp_" not in env_example
    assert "github_pat_" not in env_example


def test_runbook_documents_sibling_update_and_stop_conditions() -> None:
    runbook = (REPOSITORY_ROOT / "docs" / "deployment" / "vps.md").read_text(encoding="utf-8")

    for required in (
        "second-brain-vault",
        "fetch --no-tags origin main",
        "merge-base --is-ancestor main origin/main",
        "merge --ff-only origin/main",
        "dirty",
        "diverged",
        "doctor",
        "vault validate",
        "systemd",
        "Caddy/Nginx",
        "Docker/Compose",
        "Agent Reach",
        "GH_TOKEN",
    ):
        assert required in runbook
