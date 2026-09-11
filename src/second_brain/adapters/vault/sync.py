"""Fail-closed production sync for the persistent second-brain-vault."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path, PurePosixPath
from typing import NoReturn, Protocol

from second_brain.adapters.commands import CommandResult
from second_brain.adapters.vault.operation_lock import (
    VaultOperationBusy,
    VaultOperationLock,
    VaultOperationLockError,
)

DEFAULT_EXPECTED_REMOTE = "https://github.com/MikeMoore1337/second-brain-vault.git"
DEFAULT_RETENTION_COUNT = 7
DEFAULT_TIMEOUT_SECONDS = 120.0
_SHA_PATTERN = re.compile(r"[0-9a-f]{40}")
_BACKUP_PREFIX = "vault-sync-"
_ARCHIVE_SUFFIX = ".tar.gz"
_MANIFEST_SUFFIX = ".manifest.json"
_CHUNK_SIZE = 1024 * 1024


class VaultSyncStatus(StrEnum):
    """Owner-facing result classes."""

    NO_OP = "no-op"
    SYNCED = "synced"
    HUMAN_REQUIRED = "human-required"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class VaultSyncConfig:
    """Explicit production paths and exact target contract."""

    vault_root: Path
    backup_root: Path
    lock_path: Path
    app_root: Path
    target_sha: str
    expected_remote: str = DEFAULT_EXPECTED_REMOTE
    expected_branch: str = "main"
    retention_count: int = DEFAULT_RETENTION_COUNT
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS
    validation_command: tuple[str, ...] | None = None

    def __post_init__(self) -> None:
        if _SHA_PATTERN.fullmatch(self.target_sha) is None:
            raise ValueError("target_sha must be an exact lowercase 40-character Git SHA")
        for value, name in (
            (self.expected_remote, "expected_remote"),
            (self.expected_branch, "expected_branch"),
        ):
            _validate_single_line(value, name)
        if self.retention_count < 2:
            raise ValueError("retention_count must keep at least two backups")
        if self.timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        if self.validation_command is not None and not self.validation_command:
            raise ValueError("validation_command must not be empty")


@dataclass(frozen=True, slots=True)
class VaultSyncResult:
    """Bounded result that never contains Git stderr or vault contents."""

    status: VaultSyncStatus
    code: str
    message: str
    source_head: str | None = None
    target_head: str | None = None
    backup_path: Path | None = None
    mutation_performed: bool = False
    warning: str | None = None

    def as_dict(self) -> dict[str, object]:
        """Return a stable operator/automation representation."""

        return {
            "status": self.status.value,
            "code": self.code,
            "message": self.message,
            "source_head": self.source_head,
            "target_head": self.target_head,
            "backup_path": str(self.backup_path) if self.backup_path is not None else None,
            "mutation_performed": self.mutation_performed,
            "warning": self.warning,
        }


class SyncRunner(Protocol):
    """Explicit argv seam for deterministic sync tests."""

    def __call__(self, argv: Sequence[str], cwd: Path, timeout: float) -> CommandResult:
        """Run one bounded command without a shell."""


def run_sync_command(argv: Sequence[str], cwd: Path, timeout: float) -> CommandResult:
    """Run Git/application validation with bounded time and hidden diagnostics."""

    environment = os.environ.copy()
    environment["GIT_TERMINAL_PROMPT"] = "0"
    try:
        completed = subprocess.run(
            list(argv),
            cwd=cwd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
            shell=False,
            timeout=timeout,
            env=environment,
        )
    except subprocess.TimeoutExpired:
        return CommandResult(124)
    except OSError:
        return CommandResult(127)
    return CommandResult(completed.returncode, completed.stdout, completed.stderr)


@dataclass(frozen=True, slots=True)
class BackupArtifact:
    """Published archive and its verified metadata."""

    archive_path: Path
    manifest_path: Path
    archive_sha256: str
    file_count: int
    excluded_count: int


class BackupError(RuntimeError):
    """A recoverable snapshot could not be published."""


@dataclass(frozen=True, slots=True)
class _SnapshotEntry:
    relative_path: str
    absolute_path: Path
    is_directory: bool
    size_bytes: int = 0
    sha256: str = ""


class BackupStore:
    """Create verified private tar.gz snapshots and bounded retention."""

    def __init__(
        self,
        vault_root: Path,
        backup_root: Path,
        *,
        clock: Callable[[], datetime],
        retention_count: int,
    ) -> None:
        self.vault_root = vault_root
        self.backup_root = backup_root
        self.clock = clock
        self.retention_count = retention_count

    def create(self, source_head: str, target_head: str) -> BackupArtifact:
        """Publish one archive only after archive members and hashes verify."""

        self._ensure_backup_root()
        entries, excluded_count = _snapshot_entries(self.vault_root)
        if any(entry.relative_path == "manifest.json" for entry in entries):
            raise BackupError("backup metadata path collides with vault content")
        created = self.clock().astimezone(UTC)
        created_at = _timestamp(created)
        stem = f"{_BACKUP_PREFIX}{created_at}-{source_head[:12]}-{target_head[:12]}-{os.getpid()}"
        file_count = sum(not entry.is_directory for entry in entries)
        try:
            temporary_root = Path(tempfile.mkdtemp(prefix=".vault-sync-", dir=self.backup_root))
        except OSError as exc:
            raise BackupError("backup temporary directory could not be created") from exc
        try:
            _chmod_private(temporary_root)
            manifest = {
                "schema_version": 1,
                "format": "tar.gz",
                "created_at": created.isoformat(timespec="seconds"),
                "source_head": source_head,
                "target_origin_main": target_head,
                "file_count": file_count,
                "excluded_external_credential_count": excluded_count,
                "files": [
                    {
                        "path": entry.relative_path,
                        "size_bytes": entry.size_bytes,
                        "sha256": entry.sha256,
                    }
                    for entry in entries
                    if not entry.is_directory
                ],
            }
            manifest_path = temporary_root / "manifest.json"
            _write_private_json(manifest_path, manifest)
            temporary_archive = temporary_root / f"{stem}{_ARCHIVE_SUFFIX}"
            with tarfile.open(temporary_archive, mode="w:gz") as archive:
                for entry in entries:
                    archive.add(
                        entry.absolute_path,
                        arcname=entry.relative_path,
                        recursive=False,
                    )
                archive.add(manifest_path, arcname="manifest.json", recursive=False)
            _verify_archive(temporary_archive, manifest)
            archive_sha256 = _sha256_file(temporary_archive)

            final_archive = self.backup_root / f"{stem}{_ARCHIVE_SUFFIX}"
            final_manifest = self.backup_root / f"{stem}{_MANIFEST_SUFFIX}"
            if final_archive.exists() or final_manifest.exists():
                raise BackupError("backup filename collision")
            sidecar = {
                **manifest,
                "archive_filename": final_archive.name,
                "archive_sha256": archive_sha256,
            }
            temporary_sidecar = temporary_root / f"{stem}{_MANIFEST_SUFFIX}"
            _write_private_json(temporary_sidecar, sidecar)
            os.replace(temporary_archive, final_archive)
            os.replace(temporary_sidecar, final_manifest)
            _chmod_private(final_archive)
            _chmod_private(final_manifest)
            _fsync_directory(self.backup_root)
            return BackupArtifact(
                final_archive,
                final_manifest,
                archive_sha256,
                file_count,
                excluded_count,
            )
        except BackupError:
            raise
        except (OSError, UnicodeError, tarfile.TarError, ValueError) as exc:
            raise BackupError("backup snapshot could not be created") from exc
        finally:
            shutil.rmtree(temporary_root, ignore_errors=True)

    def prune(self) -> None:
        """Remove only old, paired snapshots and always retain two newest."""

        self._ensure_backup_root()
        pairs: list[tuple[Path, Path]] = []
        for archive in self.backup_root.glob(f"{_BACKUP_PREFIX}*{_ARCHIVE_SUFFIX}"):
            if archive.is_symlink() or not archive.is_file():
                continue
            stem = archive.name[: -len(_ARCHIVE_SUFFIX)]
            manifest = self.backup_root / f"{stem}{_MANIFEST_SUFFIX}"
            if manifest.is_symlink() or not manifest.is_file():
                continue
            pairs.append((archive, manifest))
        pairs.sort(key=lambda pair: pair[0].name, reverse=True)
        for archive, manifest in pairs[self.retention_count :]:
            try:
                manifest.unlink()
                archive.unlink()
            except OSError as exc:
                raise BackupError("backup retention cleanup failed") from exc

    def _ensure_backup_root(self) -> None:
        if self.backup_root.exists():
            if self.backup_root.is_symlink() or not self.backup_root.is_dir():
                raise BackupError("backup root is not a private directory")
        else:
            parent = self.backup_root.parent
            if not parent.is_dir() or parent.is_symlink():
                raise BackupError("backup root parent is unavailable")
            try:
                self.backup_root.mkdir(mode=0o700)
            except OSError as exc:
                raise BackupError("backup root could not be created") from exc
        try:
            _chmod_private(self.backup_root)
        except OSError as exc:
            raise BackupError("backup root permissions are unsafe") from exc


class _SyncStop(Exception):
    def __init__(
        self,
        status: VaultSyncStatus,
        code: str,
        message: str,
        *,
        mutation_performed: bool = False,
    ) -> None:
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message
        self.mutation_performed = mutation_performed


class VaultSync:
    """Run the exact-target, backup-before-fast-forward protocol."""

    def __init__(
        self,
        config: VaultSyncConfig,
        *,
        runner: SyncRunner = run_sync_command,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        backup_store: BackupStore | None = None,
    ) -> None:
        self.config = config
        self.runner = runner
        self.clock = clock
        self._backup_store = backup_store
        self._vault_root = config.vault_root
        self._backup_root = config.backup_root
        self._lock_path = config.lock_path
        self._app_root = config.app_root

    def execute(self) -> VaultSyncResult:
        """Return a safe bounded verdict and never print subprocess output."""

        try:
            self._resolve_and_validate_paths()
            with VaultOperationLock(self._lock_path, operation="vault-sync"):
                return self._execute_locked()
        except VaultOperationBusy:
            return VaultSyncResult(
                VaultSyncStatus.HUMAN_REQUIRED,
                "LOCK_CONTENTION",
                "another vault sync/write operation is active; retry after owner review",
            )
        except VaultOperationLockError:
            return VaultSyncResult(
                VaultSyncStatus.FAILED,
                "LOCK_UNAVAILABLE",
                "exclusive vault operation lock could not be acquired safely",
            )
        except _SyncStop as stop:
            return VaultSyncResult(
                stop.status,
                stop.code,
                stop.message,
                mutation_performed=stop.mutation_performed,
            )
        except OSError, RuntimeError, UnicodeError, ValueError:
            return VaultSyncResult(
                VaultSyncStatus.FAILED,
                "PREFLIGHT_FAILED",
                "vault sync preflight failed closed; no automatic repair was attempted",
            )

    def _execute_locked(self) -> VaultSyncResult:
        backup: BackupArtifact | None = None
        mutation_performed = False
        source_head: str | None = None
        target_head: str | None = None
        try:
            self._ensure_vault_tree_safe()
            self._inspect_repository()
            self._require_clean_worktree()
            source_head = self._git_output(
                ("rev-parse", "--verify", "HEAD^{commit}"),
                "GIT_HEAD_UNAVAILABLE",
                "repository HEAD could not be read",
            )
            self._git_success(
                ("fetch", "--no-tags", "origin", self.config.expected_branch),
                "GIT_FETCH_FAILED",
            )
            target_head = self._git_output(
                (
                    "rev-parse",
                    "--verify",
                    f"refs/remotes/origin/{self.config.expected_branch}^{{commit}}",
                ),
                "ORIGIN_MAIN_UNAVAILABLE",
                "origin expected branch could not be read after fetch",
            )
            if target_head != self.config.target_sha:
                raise _SyncStop(
                    VaultSyncStatus.HUMAN_REQUIRED,
                    "TARGET_SHA_DRIFTED",
                    "origin expected branch no longer matches the exact SHA in this sync prompt",
                )
            self._inspect_repository()
            self._require_target_unchanged(target_head)
            self._require_clean_worktree()
            self._ensure_vault_tree_safe()
            self._require_head_unchanged(source_head)
            relation = self._relation(source_head, target_head)
            if relation == "equal":
                self._run_validation()
                return VaultSyncResult(
                    VaultSyncStatus.NO_OP,
                    "EQUAL_NO_OP",
                    f"local HEAD already equals origin/{self.config.expected_branch}; "
                    "no vault mutation was needed",
                    source_head,
                    target_head,
                )
            if relation == "ahead":
                raise _SyncStop(
                    VaultSyncStatus.HUMAN_REQUIRED,
                    "LOCAL_AHEAD",
                    f"local vault contains commits absent from "
                    f"origin/{self.config.expected_branch}; "
                    "no changes were lost",
                )
            if relation == "diverged":
                raise _SyncStop(
                    VaultSyncStatus.HUMAN_REQUIRED,
                    "DIVERGED",
                    f"local vault and origin/{self.config.expected_branch} diverged; "
                    "manual reconciliation is required",
                )

            store = self._backup_store or BackupStore(
                self._vault_root,
                self._backup_root,
                clock=self.clock,
                retention_count=self.config.retention_count,
            )
            try:
                backup = store.create(source_head, target_head)
            except BackupError as exc:
                raise _SyncStop(
                    VaultSyncStatus.FAILED,
                    "BACKUP_FAILED",
                    "recoverable backup failed; fast-forward sync was not started",
                ) from exc
            self._inspect_repository()
            self._require_target_unchanged(target_head)
            self._require_clean_worktree()
            self._ensure_vault_tree_safe()
            self._require_head_unchanged(source_head)
            mutation_performed = True
            self._git_success(
                (
                    "merge",
                    "--ff-only",
                    "--no-edit",
                    "--no-overwrite-ignore",
                    target_head,
                ),
                "FAST_FORWARD_FAILED",
            )
            mutation_performed = True
            final_head = self._git_output(
                ("rev-parse", "--verify", "HEAD^{commit}"),
                "POST_SYNC_HEAD_UNAVAILABLE",
                "post-sync HEAD could not be read",
            )
            if final_head != target_head:
                raise _SyncStop(
                    VaultSyncStatus.HUMAN_REQUIRED,
                    "POST_SYNC_HEAD_MISMATCH",
                    f"post-sync HEAD does not equal the requested "
                    f"origin/{self.config.expected_branch} SHA",
                    mutation_performed=True,
                )
            self._require_clean_worktree()
            self._ensure_vault_tree_safe()
            self._run_validation()
            warning: str | None = None
            try:
                store.prune()
            except BackupError, OSError:
                warning = "backup retention cleanup was skipped; existing backups were preserved"
            return VaultSyncResult(
                VaultSyncStatus.SYNCED,
                "FAST_FORWARD_SYNCED",
                "vault fast-forwarded safely and passed application validation",
                source_head,
                target_head,
                backup.archive_path,
                mutation_performed=True,
                warning=warning,
            )
        except _SyncStop as stop:
            return VaultSyncResult(
                stop.status,
                stop.code,
                stop.message,
                source_head,
                target_head,
                backup.archive_path if backup is not None else None,
                mutation_performed or stop.mutation_performed,
            )

    def _resolve_and_validate_paths(self) -> None:
        for path, name in (
            (self.config.vault_root, "vault_root"),
            (self.config.backup_root, "backup_root"),
            (self.config.lock_path, "lock_path"),
            (self.config.app_root, "app_root"),
        ):
            if not path.is_absolute():
                raise _SyncStop(
                    VaultSyncStatus.FAILED,
                    "PATH_NOT_ABSOLUTE",
                    f"{name} must be an absolute configured path",
                )
        if self.config.vault_root.is_symlink():
            raise _SyncStop(
                VaultSyncStatus.FAILED,
                "VAULT_PATH_UNSAFE",
                "vault root must not be a symlink",
            )
        try:
            self._vault_root = self.config.vault_root.resolve(strict=True)
            self._app_root = self.config.app_root.resolve(strict=True)
            backup_root = self.config.backup_root.resolve(strict=False)
            lock_parent = self.config.lock_path.parent.resolve(strict=True)
            self._backup_root = backup_root
            self._lock_path = lock_parent / self.config.lock_path.name
        except (OSError, RuntimeError, ValueError) as exc:
            raise _SyncStop(
                VaultSyncStatus.FAILED,
                "PATH_RESOLUTION_FAILED",
                "configured production path could not be resolved safely",
            ) from exc
        if not self._vault_root.is_dir() or not self._app_root.is_dir():
            raise _SyncStop(
                VaultSyncStatus.FAILED,
                "PATH_NOT_DIRECTORY",
                "vault root or application root is not a directory",
            )
        if self.config.backup_root.is_symlink():
            raise _SyncStop(
                VaultSyncStatus.FAILED,
                "BACKUP_PATH_UNSAFE",
                "backup root must not be a symlink",
            )
        if self.config.lock_path.is_symlink():
            raise _SyncStop(
                VaultSyncStatus.FAILED,
                "LOCK_PATH_UNSAFE",
                "lock path must not be a symlink",
            )
        if _paths_overlap(self._vault_root, self._backup_root) or _paths_overlap(
            self._vault_root, self._lock_path
        ):
            raise _SyncStop(
                VaultSyncStatus.FAILED,
                "EXTERNAL_PATH_INSIDE_VAULT",
                "backup and lock paths must remain outside the Git worktree",
            )
        if not self._is_private_directory(self._lock_path.parent):
            raise _SyncStop(
                VaultSyncStatus.FAILED,
                "LOCK_PARENT_PERMISSIONS",
                "lock parent must have restrictive permissions",
            )
        if self._backup_root.exists() and not self._is_private_directory(self._backup_root):
            raise _SyncStop(
                VaultSyncStatus.FAILED,
                "BACKUP_PERMISSIONS",
                "backup root must have restrictive permissions",
            )

    def _inspect_repository(self) -> None:
        top = self._git_output(
            ("rev-parse", "--show-toplevel"),
            "REPOSITORY_UNAVAILABLE",
            "vault root is not a usable Git worktree",
        )
        try:
            if Path(top).resolve(strict=True) != self._vault_root:
                raise _SyncStop(
                    VaultSyncStatus.HUMAN_REQUIRED,
                    "REPOSITORY_PATH_MISMATCH",
                    "Git top-level does not equal configured vault root",
                )
        except (OSError, RuntimeError, ValueError) as exc:
            raise _SyncStop(
                VaultSyncStatus.FAILED,
                "REPOSITORY_PATH_MISMATCH",
                "Git top-level could not be verified against configured vault root",
            ) from exc
        if (
            self._git_output(
                ("rev-parse", "--is-inside-work-tree"),
                "REPOSITORY_STATE_FAILED",
                "Git worktree state could not be verified",
            ).casefold()
            != "true"
        ):
            raise _SyncStop(
                VaultSyncStatus.HUMAN_REQUIRED,
                "REPOSITORY_STATE_MISMATCH",
                "configured vault is not an ordinary Git worktree",
            )
        if (
            self._git_output(
                ("rev-parse", "--is-bare-repository"),
                "REPOSITORY_STATE_FAILED",
                "Git bare/worktree state could not be verified",
            ).casefold()
            == "true"
        ):
            raise _SyncStop(
                VaultSyncStatus.HUMAN_REQUIRED,
                "BARE_REPOSITORY",
                "bare repository cannot be used as production vault",
            )
        superproject = self._git_optional_output(
            ("rev-parse", "--show-superproject-working-tree"),
            "REPOSITORY_STATE_FAILED",
            "submodule state could not be verified",
        )
        if superproject:
            raise _SyncStop(
                VaultSyncStatus.HUMAN_REQUIRED,
                "SUBMODULE_REPOSITORY",
                "production vault must remain an independent repository",
            )
        # ``remote get-url`` applies Git's ``url.*.insteadOf`` transport
        # rewriting.  It is useful for fetch, but it is not the configured
        # repository identity.  Trust only the raw local origin value and
        # require a single URL so a mutable multi-URL remote cannot broaden
        # the production trust boundary.
        configured_origin = self._git_output(
            ("config", "--local", "--get-all", "remote.origin.url"),
            "REMOTE_UNAVAILABLE",
            "expected raw origin remote could not be read",
        )
        if (
            "\n" in configured_origin
            or "\r" in configured_origin
            or _normalize_remote(configured_origin)
            != _normalize_remote(self.config.expected_remote)
        ):
            raise _SyncStop(
                VaultSyncStatus.HUMAN_REQUIRED,
                "REMOTE_MISMATCH",
                "configured raw origin remote does not match the expected vault repository",
            )
        branch = self._git_output(
            ("symbolic-ref", "--quiet", "--short", "HEAD"),
            "BRANCH_UNAVAILABLE",
            "current vault HEAD is detached or branch could not be read",
        )
        if branch != self.config.expected_branch:
            raise _SyncStop(
                VaultSyncStatus.HUMAN_REQUIRED,
                "BRANCH_MISMATCH",
                "production vault is not on the expected branch",
            )
        self._check_operation_markers()

    def _require_clean_worktree(self) -> None:
        result = self._git(
            ("status", "--porcelain=v1", "--untracked-files=all", "--ignore-submodules=none")
        )
        if result is None or result.returncode != 0:
            raise _SyncStop(
                VaultSyncStatus.FAILED,
                "STATUS_UNAVAILABLE",
                "vault worktree status could not be verified",
            )
        if result.stdout:
            raise _SyncStop(
                VaultSyncStatus.HUMAN_REQUIRED,
                "DIRTY_WORKTREE",
                "tracked or untracked vault changes exist; automatic overwrite is forbidden",
            )

    def _require_head_unchanged(self, expected_head: str) -> None:
        current_head = self._git_output(
            ("rev-parse", "--verify", "HEAD^{commit}"),
            "GIT_HEAD_UNAVAILABLE",
            "repository HEAD could not be rechecked safely",
        )
        if current_head != expected_head:
            raise _SyncStop(
                VaultSyncStatus.HUMAN_REQUIRED,
                "LOCAL_HEAD_CHANGED",
                "local vault HEAD changed during sync preflight; no automatic merge was attempted",
            )

    def _require_target_unchanged(self, expected_target: str) -> None:
        current_target = self._git_output(
            (
                "rev-parse",
                "--verify",
                f"refs/remotes/origin/{self.config.expected_branch}^{{commit}}",
            ),
            "ORIGIN_MAIN_UNAVAILABLE",
            "origin expected branch could not be rechecked safely",
        )
        if current_target != expected_target or current_target != self.config.target_sha:
            raise _SyncStop(
                VaultSyncStatus.HUMAN_REQUIRED,
                "TARGET_SHA_DRIFTED",
                "origin expected branch changed during sync preflight",
            )

    def _check_operation_markers(self) -> None:
        markers = (
            "MERGE_HEAD",
            "CHERRY_PICK_HEAD",
            "REVERT_HEAD",
            "REBASE_HEAD",
            "rebase-merge",
            "rebase-apply",
            "BISECT_LOG",
            "index.lock",
            "HEAD.lock",
            "config.lock",
            "packed-refs.lock",
            "shallow.lock",
            f"refs/heads/{self.config.expected_branch}.lock",
            f"refs/remotes/origin/{self.config.expected_branch}.lock",
        )
        for marker in markers:
            path_text = self._git_output(
                ("rev-parse", "--git-path", marker),
                "GIT_OPERATION_STATE_FAILED",
                "Git operation state could not be inspected",
            )
            marker_path = Path(path_text)
            if not marker_path.is_absolute():
                marker_path = self._vault_root / marker_path
            if marker_path.exists() or marker_path.is_symlink():
                raise _SyncStop(
                    VaultSyncStatus.HUMAN_REQUIRED,
                    "GIT_OPERATION_IN_PROGRESS",
                    "an unfinished Git operation is active in the vault",
                )

    def _relation(self, source_head: str, target_head: str) -> str:
        if source_head == target_head:
            return "equal"
        first = self._git(("merge-base", "--is-ancestor", source_head, target_head))
        if first is None or first.returncode not in {0, 1}:
            raise _SyncStop(
                VaultSyncStatus.FAILED,
                "RELATION_FAILED",
                f"Git relation between local HEAD and origin/{self.config.expected_branch} "
                "could not be classified",
            )
        if first.returncode == 0:
            return "behind"
        second = self._git(("merge-base", "--is-ancestor", target_head, source_head))
        if second is None or second.returncode not in {0, 1}:
            raise _SyncStop(
                VaultSyncStatus.FAILED,
                "RELATION_FAILED",
                f"Git relation between local HEAD and origin/{self.config.expected_branch} "
                "could not be classified",
            )
        if second.returncode == 0:
            return "ahead"
        return "diverged"

    def _run_validation(self) -> None:
        command = self.config.validation_command or (
            "uv",
            "run",
            "--python",
            "3.14",
            "--no-sync",
            "second-brain",
            "--vault-path",
            str(self._vault_root),
            "vault",
            "validate",
            "--format",
            "json",
        )
        try:
            result = self.runner(command, self._app_root, self.config.timeout_seconds)
        except OSError, subprocess.TimeoutExpired, ValueError:
            raise _SyncStop(
                VaultSyncStatus.HUMAN_REQUIRED,
                "POST_SYNC_VALIDATION_FAILED",
                "vault validate could not be completed; preserve the backup and follow manual "
                "recovery",
            ) from None
        if result.returncode != 0:
            raise _SyncStop(
                VaultSyncStatus.HUMAN_REQUIRED,
                "POST_SYNC_VALIDATION_FAILED",
                "vault validate failed; preserve the backup and follow manual recovery",
                mutation_performed=False,
            )

    def _ensure_vault_tree_safe(self) -> None:
        try:
            _snapshot_entries(self._vault_root)
        except BackupError as exc:
            raise _SyncStop(
                VaultSyncStatus.HUMAN_REQUIRED,
                "VAULT_TREE_UNSAFE",
                "vault contains an unsafe path or changed during integrity inspection",
            ) from exc

    def _git(self, args: Sequence[str]) -> CommandResult | None:
        try:
            return self.runner(("git", *args), self._vault_root, self.config.timeout_seconds)
        except OSError, subprocess.TimeoutExpired, ValueError:
            return None

    def _git_output(self, args: Sequence[str], code: str, message: str) -> str:
        result = self._git(args)
        if result is None or result.returncode != 0:
            raise _SyncStop(VaultSyncStatus.FAILED, code, message)
        output = result.stdout.strip()
        if not output:
            raise _SyncStop(VaultSyncStatus.FAILED, code, message)
        return output

    def _git_optional_output(self, args: Sequence[str], code: str, message: str) -> str:
        result = self._git(args)
        if result is None or result.returncode != 0:
            raise _SyncStop(VaultSyncStatus.FAILED, code, message)
        return result.stdout.strip()

    def _git_success(self, args: Sequence[str], code: str) -> None:
        result = self._git(args)
        if result is None or result.returncode != 0:
            raise _SyncStop(
                VaultSyncStatus.HUMAN_REQUIRED,
                code,
                "Git mutation did not complete safely; preserve any backup and inspect manually",
                mutation_performed=True,
            )

    @staticmethod
    def _is_private_directory(path: Path) -> bool:
        if os.name == "nt":
            return True
        try:
            return stat.S_IMODE(path.stat().st_mode) & 0o077 == 0
        except OSError:
            return False


def _snapshot_entries(root: Path) -> tuple[list[_SnapshotEntry], int]:
    """Enumerate only contained regular files/directories without reading content to logs."""

    if not root.is_dir() or root.is_symlink():
        raise BackupError("vault root is not a safe directory")
    entries: list[_SnapshotEntry] = []
    excluded_count = 0
    for current, directories, files in os.walk(
        root,
        topdown=True,
        onerror=_raise_walk_error,
        followlinks=False,
    ):
        current_path = Path(current)
        directories.sort()
        files.sort()
        retained_directories: list[str] = []
        for name in directories:
            path = current_path / name
            if name == ".git":
                if path.is_symlink():
                    raise BackupError("Git metadata path must not be a symlink")
                continue
            if path.is_symlink() or not path.is_dir():
                raise BackupError("vault contains an unsafe linked or special directory")
            retained_directories.append(name)
            entries.append(_SnapshotEntry(_relative_posix(root, path), path, is_directory=True))
        directories[:] = retained_directories
        for name in files:
            path = current_path / name
            if name == ".git":
                if path.is_symlink():
                    raise BackupError("Git metadata path must not be a symlink")
                continue
            if path.is_symlink():
                raise BackupError("vault contains an unsafe symlink")
            if _is_external_credential(path):
                excluded_count += 1
                continue
            if not path.is_file():
                raise BackupError("vault contains an unsupported special file")
            size_before = path.stat().st_size
            digest = _sha256_file(path)
            size_after = path.stat().st_size
            if size_before != size_after:
                raise BackupError("vault changed while the backup manifest was created")
            entries.append(
                _SnapshotEntry(
                    _relative_posix(root, path),
                    path,
                    is_directory=False,
                    size_bytes=size_after,
                    sha256=digest,
                )
            )
    entries.sort(key=lambda entry: entry.relative_path)
    return entries, excluded_count


def _raise_walk_error(error: OSError) -> NoReturn:
    raise BackupError("vault tree could not be read safely") from error


def _verify_archive(archive_path: Path, manifest: dict[str, object]) -> None:
    files = manifest.get("files")
    if not isinstance(files, list):
        raise BackupError("backup manifest has no file list")
    expected: dict[str, tuple[int, str]] = {}
    for item in files:
        if not isinstance(item, dict):
            raise BackupError("backup manifest entry is invalid")
        path = item.get("path")
        size = item.get("size_bytes")
        digest = item.get("sha256")
        if (
            not isinstance(path, str)
            or not isinstance(size, int)
            or not isinstance(digest, str)
            or not _safe_archive_name(path)
        ):
            raise BackupError("backup manifest entry is unsafe")
        expected[path] = (size, digest)
    seen: set[str] = set()
    try:
        with tarfile.open(archive_path, mode="r:gz") as opened:
            for member in opened.getmembers():
                if not _safe_archive_name(member.name) or member.issym() or member.islnk():
                    raise BackupError("backup archive contains an unsafe member")
                if member.name == "manifest.json":
                    continue
                if member.name in expected and member.isfile():
                    extracted = opened.extractfile(member)
                    if extracted is None:
                        raise BackupError("backup archive file cannot be read")
                    digest = hashlib.sha256()
                    size = 0
                    while chunk := extracted.read(_CHUNK_SIZE):
                        size += len(chunk)
                        digest.update(chunk)
                    wanted_size, wanted_digest = expected[member.name]
                    if size != wanted_size or digest.hexdigest() != wanted_digest:
                        raise BackupError("backup archive checksum verification failed")
                    seen.add(member.name)
            if seen != set(expected):
                raise BackupError("backup archive does not contain every manifest file")
    except (OSError, tarfile.TarError) as exc:
        raise BackupError("backup archive could not be verified") from exc


def _is_external_credential(path: Path) -> bool:
    name = path.name.casefold()
    return (
        name == ".env"
        or name.startswith(".env.")
        or name in {"id_rsa", "id_ed25519", "authorized_keys"}
        or name.endswith((".pem", ".key", ".p12", ".pfx", ".kdbx"))
    )


def _relative_posix(root: Path, path: Path) -> str:
    try:
        relative = path.relative_to(root)
    except ValueError as exc:
        raise BackupError("vault path escaped configured root") from exc
    value = PurePosixPath(*relative.parts).as_posix()
    if not _safe_archive_name(value):
        raise BackupError("vault relative path is unsafe")
    return value


def _safe_archive_name(value: str) -> bool:
    path = PurePosixPath(value)
    return (
        bool(value)
        and not path.is_absolute()
        and "\\" not in value
        and all(part not in {"", ".", ".."} for part in path.parts)
    )


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(_CHUNK_SIZE):
            digest.update(chunk)
    return digest.hexdigest()


def _write_private_json(path: Path, payload: dict[str, object]) -> None:
    path.write_text(
        json.dumps(payload, ensure_ascii=True, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    _chmod_private(path)


def _chmod_private(path: Path) -> None:
    os.chmod(path, 0o700 if path.is_dir() else 0o600)


def _fsync_directory(path: Path) -> None:
    if os.name == "nt":
        return
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _timestamp(value: datetime) -> str:
    return value.astimezone(UTC).strftime("%Y%m%dT%H%M%S%fZ")


def _normalize_remote(value: str) -> str:
    return value.rstrip("/").removesuffix(".git")


def _paths_overlap(first: Path, second: Path) -> bool:
    return first == second or first.is_relative_to(second) or second.is_relative_to(first)


def _validate_single_line(value: str, name: str) -> None:
    if (
        type(value) is not str
        or not value
        or len(value) > 500
        or any(ord(character) < 32 for character in value)
    ):
        raise ValueError(f"{name} must be a bounded single-line value")


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Fail-closed persistent second-brain-vault production sync."
    )
    parser.add_argument("--vault-root", type=Path, required=True)
    parser.add_argument("--backup-root", type=Path, required=True)
    parser.add_argument("--lock-path", type=Path, required=True)
    parser.add_argument("--app-root", type=Path, required=True)
    parser.add_argument("--target-sha", required=True)
    parser.add_argument("--expected-remote", default=DEFAULT_EXPECTED_REMOTE)
    parser.add_argument("--expected-branch", default="main")
    parser.add_argument("--retention-count", type=int, default=DEFAULT_RETENTION_COUNT)
    parser.add_argument("--timeout-seconds", type=float, default=DEFAULT_TIMEOUT_SECONDS)
    parser.add_argument(
        "--apply",
        action="store_true",
        help="allow verified backup and fast-forward mutation; default is non-mutating",
    )
    parser.add_argument("--format", choices=("text", "json"), default="text")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """CLI entry point used by the generated owner prompt."""

    args = _build_parser().parse_args(argv)
    if not args.apply:
        print(
            "Vault production sync STOP: explicit --apply is required; "
            "no backup or merge was attempted",
            file=sys.stderr,
        )
        return 2
    try:
        config = VaultSyncConfig(
            vault_root=args.vault_root,
            backup_root=args.backup_root,
            lock_path=args.lock_path,
            app_root=args.app_root,
            target_sha=args.target_sha,
            expected_remote=args.expected_remote,
            expected_branch=args.expected_branch,
            retention_count=args.retention_count,
            timeout_seconds=args.timeout_seconds,
        )
    except ValueError:
        print("Vault production sync FAILED: invalid bounded configuration", file=sys.stderr)
        return 2

    result = VaultSync(config).execute()
    if args.format == "json":
        print(json.dumps(result.as_dict(), ensure_ascii=True, indent=2))
    else:
        print(f"Vault production sync: {result.status.value} ({result.code}) — {result.message}")
        if result.backup_path is not None:
            print(f"Backup: {result.backup_path}")
        if result.warning is not None:
            print(f"Warning: {result.warning}")
    if result.status in {VaultSyncStatus.NO_OP, VaultSyncStatus.SYNCED}:
        return 0
    if result.status is VaultSyncStatus.HUMAN_REQUIRED:
        return 2
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
