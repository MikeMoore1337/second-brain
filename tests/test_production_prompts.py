from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

import pytest

from second_brain.application.production_prompts import (
    ProductionOperation,
    ProductionPromptContext,
    generate_production_prompt,
)

SHA = "0123456789abcdef" * 2 + "01234567"


def test_vault_prompt_is_exact_sha_sync_not_application_deploy() -> None:
    context = ProductionPromptContext(
        target_sha=SHA,
        target_repository="https://github.com/MikeMoore1337/second-brain-vault.git",
        production_path="/srv/second-brain/second-brain-vault",
        app_root="/srv/second-brain/current",
        backup_root="/srv/second-brain/runtime/vault-backups",
        lock_path="/srv/second-brain/runtime/vault-sync.lock",
        expected_remote="https://github.com/MikeMoore1337/second-brain-vault.git",
    )

    prompt = generate_production_prompt(ProductionOperation.VAULT_SYNC, context)

    assert "Vault production sync" in prompt
    assert "vault sync" in prompt.casefold()
    assert SHA in prompt
    assert context.target_repository in prompt
    assert context.production_path in prompt
    assert "backup" in prompt.casefold()
    assert "fast-forward-only" in prompt
    assert "HUMAN_REQUIRED" in prompt
    assert "deployment" not in prompt.casefold()
    assert re.search(r"--target-sha\s+" + SHA, prompt)


def test_application_prompt_strategy_remains_available() -> None:
    context = ProductionPromptContext(
        target_sha=SHA,
        target_repository="https://github.com/MikeMoore1337/second-brain.git",
        production_path="/srv/second-brain/current",
    )

    prompt = generate_production_prompt(ProductionOperation.APPLICATION_DEPLOY, context)

    assert "Application production deployment" in prompt
    assert SHA in prompt
    assert "immutable candidate release" in prompt
    assert "vault sync" in prompt.casefold()


@pytest.mark.parametrize("value", ["A" * 40, "0" * 39, "0" * 41, "0" * 39 + "!\n"])
def test_prompt_rejects_non_exact_sha(value: str) -> None:
    with pytest.raises(ValueError, match="exact lowercase"):
        ProductionPromptContext(
            target_sha=value,
            target_repository="https://example.invalid/repository.git",
            production_path="/srv/second-brain/current",
        )


def test_prompt_context_rejects_control_characters() -> None:
    with pytest.raises(ValueError, match="single-line"):
        ProductionPromptContext(
            target_sha=SHA,
            target_repository="https://example.invalid/repository.git\nmalicious",
            production_path="/srv/second-brain/current",
        )


def test_prompt_script_can_resolve_current_ref_without_hardcoded_sha(tmp_path: Path) -> None:
    source = tmp_path / "vault"
    source.mkdir()
    subprocess.run(["git", "init", "-b", "main"], cwd=source, check=True, capture_output=True)
    subprocess.run(
        ["git", "config", "user.name", "Prompt Test"], cwd=source, check=True, capture_output=True
    )
    subprocess.run(
        ["git", "config", "user.email", "prompt@example.invalid"],
        cwd=source,
        check=True,
        capture_output=True,
    )
    (source / "README.md").write_text("synthetic\n", encoding="utf-8")
    subprocess.run(["git", "add", "README.md"], cwd=source, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "prompt source"], cwd=source, check=True, capture_output=True
    )
    target = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=source,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    script = Path(__file__).parents[1] / "scripts" / "generate_production_prompt.py"
    result = subprocess.run(
        [
            sys.executable,
            str(script),
            "--operation",
            "vault-sync",
            "--source-repo",
            str(source),
            "--ref",
            "HEAD",
            "--production-path",
            "/srv/second-brain/second-brain-vault",
            "--app-root",
            "/srv/second-brain/current",
            "--backup-root",
            "/srv/second-brain/runtime/vault-backups",
            "--lock-path",
            "/srv/second-brain/runtime/vault-sync.lock",
        ],
        cwd=script.parents[1],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    assert target in result.stdout
    assert "Vault production sync" in result.stdout
