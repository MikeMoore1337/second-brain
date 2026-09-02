from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

REPOSITORY_ROOT = Path(__file__).parents[1]
DEPLOY_ROOT = REPOSITORY_ROOT / "deploy"


def _run_git(cwd: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    return completed.stdout.strip()


def _make_clone(tmp_path: Path, root: Path, name: str) -> tuple[Path, Path]:
    seed = tmp_path / f"{name}-seed"
    seed.mkdir()
    origin = tmp_path / f"{name}.git"
    _run_git(seed, "init", "-b", "main")
    _run_git(seed, "config", "user.name", "Bootstrap Test")
    _run_git(seed, "config", "user.email", "bootstrap@example.invalid")
    (seed / "seed.txt").write_text(f"{name}\n", encoding="utf-8")
    _run_git(seed, "add", "--", "seed.txt")
    _run_git(seed, "commit", "-m", "initial")
    _run_git(tmp_path, "init", "--bare", str(origin))
    _run_git(seed, "remote", "add", "origin", str(origin))
    _run_git(seed, "push", "origin", "main")

    clone = root / name
    _run_git(root, "clone", str(origin), str(clone))
    return clone, origin


def _advance_remote(origin: Path, tmp_path: Path, name: str) -> str:
    publisher = tmp_path / f"{name}-publisher"
    _run_git(tmp_path, "clone", str(origin), str(publisher))
    _run_git(publisher, "config", "user.name", "Bootstrap Publisher")
    _run_git(publisher, "config", "user.email", "publisher@example.invalid")
    (publisher / "remote.txt").write_text("remote advanced\n", encoding="utf-8")
    _run_git(publisher, "add", "--", "remote.txt")
    _run_git(publisher, "commit", "-m", "advance remote main")
    _run_git(publisher, "push", "origin", "main")
    return _run_git(publisher, "rev-parse", "HEAD")


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
        'git -C "$path" fetch --no-tags origin main',
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


@pytest.mark.skipif(os.name == "nt", reason="bootstrap requires Linux")
def test_bootstrap_refreshes_stale_origin_before_sync(tmp_path: Path) -> None:
    bash = shutil.which("bash")
    if bash is None or shutil.which("uv") is None:
        pytest.skip("bash and uv are required for the bootstrap integration test")
    if hasattr(os, "geteuid") and os.geteuid() == 0:
        pytest.skip("bootstrap intentionally refuses root")

    root = tmp_path / "layout"
    root.mkdir()
    brain, brain_origin = _make_clone(tmp_path, root, "second-brain")
    vault, _ = _make_clone(tmp_path, root, "second-brain-vault")
    vault.chmod(0o700)
    runtime = root / "runtime"
    runtime.mkdir(mode=0o700)
    runtime.chmod(0o700)
    env_file = runtime / ".env"
    env_file.write_text("SECOND_BRAIN_VAULT_PATH=../second-brain-vault\n", encoding="utf-8")
    env_file.chmod(0o600)

    stale_origin = _run_git(brain, "rev-parse", "refs/remotes/origin/main")
    remote_head = _advance_remote(brain_origin, tmp_path, "second-brain")
    assert stale_origin != remote_head

    result = subprocess.run(
        [bash, str(DEPLOY_ROOT / "bootstrap.sh"), "--root", str(root)],
        cwd=REPOSITORY_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode != 0
    assert "local main" in result.stderr
    assert "origin/main" in result.stderr
    assert _run_git(brain, "rev-parse", "refs/remotes/origin/main") == remote_head


def test_runbook_documents_sibling_update_and_stop_conditions() -> None:
    runbook = (REPOSITORY_ROOT / "docs" / "deployment" / "vps.md").read_text(encoding="utf-8")

    for required in (
        "second-brain-vault",
        "$HOME/.local/share/second-brain",
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
