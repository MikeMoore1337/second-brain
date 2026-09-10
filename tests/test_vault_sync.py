from __future__ import annotations

import json
import os
import subprocess
import sys
import tarfile
import uuid
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path

import pytest

from second_brain.adapters.commands import CommandResult
from second_brain.adapters.vault.operation_lock import VaultOperationLock
from second_brain.adapters.vault.sync import (
    BackupArtifact,
    BackupError,
    BackupStore,
    SyncRunner,
    VaultSync,
    VaultSyncConfig,
    VaultSyncResult,
    VaultSyncStatus,
    run_sync_command,
)
from second_brain.adapters.vault.writer import FileSystemVaultWriter
from second_brain.application.writes import CreateNotePlan, WriteSafetyError
from second_brain.domain.models import NoteType


def _run_git(cwd: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
        shell=False,
    )
    assert result.returncode == 0, result.stderr
    return result.stdout.strip()


def _private_directory(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    if os.name != "nt":
        path.chmod(0o700)


def _make_vault(tmp_path: Path) -> tuple[Path, Path, Path, Path]:
    seed = tmp_path / "seed"
    origin = tmp_path / "vault.git"
    clone = tmp_path / "vault"
    app_root = tmp_path / "app"
    seed.mkdir()
    _private_directory(app_root)
    _run_git(seed, "init", "-b", "main")
    _run_git(seed, "config", "user.name", "Sync Test")
    _run_git(seed, "config", "user.email", "sync@example.invalid")
    (seed / "vault.md").write_text("synthetic vault data\n", encoding="utf-8")
    (seed / ".env").write_text("SYNTHETIC_SECRET=must-not-enter-backup\n", encoding="utf-8")
    _run_git(seed, "add", "--", "vault.md")
    _run_git(seed, "add", "--force", ".env")
    _run_git(seed, "commit", "-m", "initial vault")
    _run_git(tmp_path, "init", "--bare", str(origin))
    _run_git(seed, "remote", "add", "origin", str(origin))
    _run_git(seed, "push", "origin", "main")
    _run_git(tmp_path, "--git-dir", str(origin), "symbolic-ref", "HEAD", "refs/heads/main")
    _run_git(tmp_path, "clone", str(origin), str(clone))
    return clone, origin, app_root, seed


def _advance_remote(origin: Path, tmp_path: Path) -> str:
    publisher = tmp_path / "publisher"
    _run_git(tmp_path, "clone", str(origin), str(publisher))
    _run_git(publisher, "config", "user.name", "Remote Publisher")
    _run_git(publisher, "config", "user.email", "publisher@example.invalid")
    (publisher / "remote.md").write_text("synthetic remote change\n", encoding="utf-8")
    _run_git(publisher, "add", "--", "remote.md")
    _run_git(publisher, "commit", "-m", "advance vault")
    _run_git(publisher, "push", "origin", "main")
    return _run_git(publisher, "rev-parse", "HEAD")


def _config(
    tmp_path: Path,
    vault: Path,
    origin: Path,
    app_root: Path,
    target_sha: str,
    *,
    validation_command: tuple[str, ...] | None = None,
    backup_store: BackupStore | None = None,
) -> tuple[VaultSyncConfig, BackupStore | None]:
    runtime = tmp_path / "runtime"
    _private_directory(runtime)
    backup_root = runtime / "vault-backups"
    config = VaultSyncConfig(
        vault_root=vault,
        backup_root=backup_root,
        lock_path=runtime / "vault-sync.lock",
        app_root=app_root,
        target_sha=target_sha,
        expected_remote=str(origin),
        validation_command=validation_command or (sys.executable, "-c", "raise SystemExit(0)"),
    )
    return config, backup_store


def _sync(
    config: VaultSyncConfig,
    *,
    backup_store: BackupStore | None = None,
    runner: SyncRunner = run_sync_command,
) -> VaultSyncResult:
    return VaultSync(config, runner=runner, backup_store=backup_store).execute()


def test_equal_is_a_validated_no_op_without_backup(tmp_path: Path) -> None:
    vault, origin, app_root, _ = _make_vault(tmp_path)
    target = _run_git(vault, "rev-parse", "HEAD")
    config, _ = _config(tmp_path, vault, origin, app_root, target)

    result = _sync(config)

    assert result.status is VaultSyncStatus.NO_OP
    assert result.code == "EQUAL_NO_OP"
    assert result.mutation_performed is False
    assert result.backup_path is None
    assert not config.backup_root.exists()
    assert _run_git(vault, "rev-parse", "HEAD") == target


def test_clean_behind_uses_backup_then_fast_forward_only(tmp_path: Path) -> None:
    vault, origin, app_root, _ = _make_vault(tmp_path)
    source = _run_git(vault, "rev-parse", "HEAD")
    target = _advance_remote(origin, tmp_path)
    config, _ = _config(tmp_path, vault, origin, app_root, target)

    result = _sync(config)

    assert result.status is VaultSyncStatus.SYNCED
    assert result.code == "FAST_FORWARD_SYNCED"
    assert result.mutation_performed is True
    assert _run_git(vault, "rev-parse", "HEAD") == target
    assert result.backup_path is not None
    assert result.backup_path.is_file()
    manifest_path = result.backup_path.with_name(
        result.backup_path.name.removesuffix(".tar.gz") + ".manifest.json"
    )
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["source_head"] == source
    assert manifest["target_origin_main"] == target
    assert len(manifest["archive_sha256"]) == 64
    assert manifest["excluded_external_credential_count"] == 1
    with tarfile.open(result.backup_path, "r:gz") as archive:
        names = {member.name for member in archive.getmembers()}
    assert "vault.md" in names
    assert ".env" not in names
    assert ".git" not in names


class _FailingBackupStore(BackupStore):
    def create(self, source_head: str, target_head: str) -> BackupArtifact:
        del source_head, target_head
        raise BackupError("synthetic backup failure")


def test_backup_failure_prevents_sync_mutation(tmp_path: Path) -> None:
    vault, origin, app_root, _ = _make_vault(tmp_path)
    source = _run_git(vault, "rev-parse", "HEAD")
    target = _advance_remote(origin, tmp_path)
    config, _ = _config(tmp_path, vault, origin, app_root, target)
    store = _FailingBackupStore(
        config.vault_root,
        config.backup_root,
        clock=lambda: datetime.now(UTC),
        retention_count=2,
    )

    result = _sync(config, backup_store=store)

    assert result.status is VaultSyncStatus.FAILED
    assert result.code == "BACKUP_FAILED"
    assert result.mutation_performed is False
    assert _run_git(vault, "rev-parse", "HEAD") == source


def test_local_ahead_is_human_required_without_loss(tmp_path: Path) -> None:
    vault, origin, app_root, _ = _make_vault(tmp_path)
    target = _run_git(vault, "rev-parse", "HEAD")
    _run_git(vault, "config", "user.name", "Local Writer")
    _run_git(vault, "config", "user.email", "local@example.invalid")
    (vault / "local.md").write_text("local-only\n", encoding="utf-8")
    _run_git(vault, "add", "--", "local.md")
    _run_git(vault, "commit", "-m", "local vault write")
    local_head = _run_git(vault, "rev-parse", "HEAD")
    config, _ = _config(tmp_path, vault, origin, app_root, target)

    result = _sync(config)

    assert result.status is VaultSyncStatus.HUMAN_REQUIRED
    assert result.code == "LOCAL_AHEAD"
    assert result.mutation_performed is False
    assert _run_git(vault, "rev-parse", "HEAD") == local_head
    assert (vault / "local.md").read_text(encoding="utf-8") == "local-only\n"


def test_diverged_is_human_required(tmp_path: Path) -> None:
    vault, origin, app_root, _ = _make_vault(tmp_path)
    source = _run_git(vault, "rev-parse", "HEAD")
    target = _advance_remote(origin, tmp_path)
    _run_git(vault, "config", "user.name", "Local Writer")
    _run_git(vault, "config", "user.email", "local@example.invalid")
    (vault / "local.md").write_text("local-only\n", encoding="utf-8")
    _run_git(vault, "add", "--", "local.md")
    _run_git(vault, "commit", "-m", "diverging local vault write")
    local_head = _run_git(vault, "rev-parse", "HEAD")
    config, _ = _config(tmp_path, vault, origin, app_root, target)

    result = _sync(config)

    assert result.status is VaultSyncStatus.HUMAN_REQUIRED
    assert result.code == "DIVERGED"
    assert result.mutation_performed is False
    assert _run_git(vault, "rev-parse", "HEAD") == local_head
    assert _run_git(vault, "rev-parse", "HEAD~1") == source


@pytest.mark.parametrize("tracked", [True, False])
def test_dirty_worktree_is_human_required(tmp_path: Path, tracked: bool) -> None:
    vault, origin, app_root, _ = _make_vault(tmp_path)
    target = _run_git(vault, "rev-parse", "HEAD")
    path = vault / ("vault.md" if tracked else "untracked.md")
    path.write_text("dirty content\n", encoding="utf-8")
    before = _run_git(vault, "rev-parse", "HEAD")
    config, _ = _config(tmp_path, vault, origin, app_root, target)

    result = _sync(config)

    assert result.status is VaultSyncStatus.HUMAN_REQUIRED
    assert result.code == "DIRTY_WORKTREE"
    assert result.mutation_performed is False
    assert _run_git(vault, "rev-parse", "HEAD") == before


def test_post_sync_validation_failure_keeps_backup_and_requires_recovery(tmp_path: Path) -> None:
    vault, origin, app_root, _ = _make_vault(tmp_path)
    target = _advance_remote(origin, tmp_path)
    config, _ = _config(
        tmp_path,
        vault,
        origin,
        app_root,
        target,
        validation_command=(sys.executable, "-c", "raise SystemExit(1)"),
    )

    result = _sync(config)

    assert result.status is VaultSyncStatus.HUMAN_REQUIRED
    assert result.code == "POST_SYNC_VALIDATION_FAILED"
    assert result.mutation_performed is True
    assert result.backup_path is not None and result.backup_path.is_file()
    assert _run_git(vault, "rev-parse", "HEAD") == target


def test_lock_contention_is_fail_closed(tmp_path: Path) -> None:
    vault, origin, app_root, _ = _make_vault(tmp_path)
    target = _run_git(vault, "rev-parse", "HEAD")
    config, _ = _config(tmp_path, vault, origin, app_root, target)

    with VaultOperationLock(config.lock_path, operation="test-holder"):
        result = _sync(config)

    assert result.status is VaultSyncStatus.HUMAN_REQUIRED
    assert result.code == "LOCK_CONTENTION"
    assert result.mutation_performed is False


def test_safe_write_respects_the_shared_operation_lock(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    vault.mkdir()
    (vault / "10 Projects").mkdir()
    runtime = tmp_path / "runtime"
    _private_directory(runtime)
    lock_path = runtime / "vault-sync.lock"
    writer = FileSystemVaultWriter(vault, operation_lock_path=lock_path)
    plan = CreateNotePlan(
        NoteType.PROJECT,
        "Busy",
        uuid.uuid7(),
        datetime.now(UTC),
        "10 Projects/Busy.md",
        "# Busy\n",
        "10 Projects",
    )

    with (
        VaultOperationLock(lock_path, operation="vault-sync"),
        pytest.raises(WriteSafetyError) as exc_info,
    ):
        writer.write(plan)

    assert exc_info.value.code == "CREATE_VAULT_OPERATION_BUSY"
    assert not (vault / "10 Projects/Busy.md").exists()


def test_stale_lock_metadata_is_replaced_without_deleting_lock_file(tmp_path: Path) -> None:
    runtime = tmp_path / "runtime"
    _private_directory(runtime)
    lock_path = runtime / "vault-sync.lock"
    lock_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "operation": "old-operation",
                "pid": 1,
                "started_at": "2000-01-01T00:00:00+00:00",
            }
        ),
        encoding="utf-8",
    )
    if os.name != "nt":
        lock_path.chmod(0o600)

    with VaultOperationLock(lock_path, operation="new-operation"):
        pass
    metadata = json.loads(lock_path.read_text(encoding="utf-8"))
    assert metadata["operation"] == "new-operation"
    assert metadata["pid"] == os.getpid()
    assert lock_path.is_file()


def test_repository_and_branch_mismatch_stop_before_fetch(tmp_path: Path) -> None:
    vault, origin, app_root, _ = _make_vault(tmp_path)
    target = _run_git(vault, "rev-parse", "HEAD")
    config, _ = _config(tmp_path, vault, origin, app_root, target)
    wrong_remote = VaultSyncConfig(
        vault_root=config.vault_root,
        backup_root=config.backup_root,
        lock_path=config.lock_path,
        app_root=config.app_root,
        target_sha=config.target_sha,
        expected_remote=str(tmp_path / "other.git"),
        validation_command=config.validation_command,
    )
    remote_result = _sync(wrong_remote)
    assert remote_result.code == "REMOTE_MISMATCH"

    _run_git(vault, "switch", "-c", "feature")
    branch_result = _sync(config)
    assert branch_result.code == "BRANCH_MISMATCH"


def test_git_lock_marker_stops_before_sync(tmp_path: Path) -> None:
    vault, origin, app_root, _ = _make_vault(tmp_path)
    target = _run_git(vault, "rev-parse", "HEAD")
    git_dir = Path(_run_git(vault, "rev-parse", "--git-dir"))
    if not git_dir.is_absolute():
        git_dir = (vault / git_dir).resolve()
    marker = git_dir / "index.lock"
    marker.write_bytes(b"synthetic active writer")
    config, _ = _config(tmp_path, vault, origin, app_root, target)

    result = _sync(config)

    assert result.status is VaultSyncStatus.HUMAN_REQUIRED
    assert result.code == "GIT_OPERATION_IN_PROGRESS"
    assert marker.is_file()


def test_subprocess_failure_does_not_leak_stderr_or_note_content(tmp_path: Path) -> None:
    vault, origin, app_root, _ = _make_vault(tmp_path)
    target = _run_git(vault, "rev-parse", "HEAD")
    config, _ = _config(tmp_path, vault, origin, app_root, target)
    secret = "PRIVATE_NOTE_CONTENT_7f26c0"

    def leaking_runner(argv: Sequence[str], cwd: Path, timeout: float) -> CommandResult:
        if tuple(argv[1:]) == ("remote", "get-url", "origin"):
            return CommandResult(1, stderr=f"{secret} /srv/private/.env")
        return run_sync_command(argv, cwd, timeout)

    result = _sync(config, runner=leaking_runner)

    assert result.status is VaultSyncStatus.FAILED
    assert result.code == "REMOTE_UNAVAILABLE"
    assert secret not in result.message
    assert secret not in json.dumps(result.as_dict())
