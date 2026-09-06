"""Fail-safe cleanup of completed Git worktrees.

The GitHub/issue/PR lookup remains an orchestrator responsibility.  This
module accepts a bounded receipt containing that already-verified state and
proves the local Git and filesystem conditions before invoking Git's native
worktree lifecycle.  It never removes a directory directly.
"""

from __future__ import annotations

import json
import os
import re
import stat
import subprocess
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Final, cast

from second_brain.application.night_shift import (
    CleanupLifecycle,
    PostTaskCleanupEvidence,
    evaluate_post_task_cleanup,
)


class WorktreeCleanupInputError(ValueError):
    """The cleanup receipt is not a valid bounded v1 document."""


class WorktreeCleanupExecutionError(RuntimeError):
    """Git discovery or a required local proof command failed."""


class CleanupAction(StrEnum):
    """Action reported for one registered worktree."""

    REMOVE = "remove"
    KEEP = "keep"


WORKTREE_CLEANUP_SCHEMA_VERSION: Final[int] = 1
MAX_RECEIPT_WORKTREES: Final[int] = 256
MAX_TEXT_FIELD_BYTES: Final[int] = 512
GIT_COMMAND_TIMEOUT_SECONDS: Final[float] = 15.0
_SHA_PATTERN: Final[re.Pattern[str]] = re.compile(r"[0-9a-fA-F]{40}")
_BRANCH_PATTERN: Final[re.Pattern[str]] = re.compile(r"refs/heads/[A-Za-z0-9._/-]+")
_TASK_STATE_VALUES: Final[frozenset[str]] = frozenset(
    {
        "queued",
        "in_progress",
        "pr_open",
        "fix_required",
        "ci_wait",
        "merge_ready",
        "merged",
        "human_required",
        "blocked",
        "deferred",
        "unknown",
    }
)


@dataclass(frozen=True, slots=True)
class GitCommandResult:
    """Bounded result from one argv-only Git invocation."""

    returncode: int
    stdout: str = ""
    stderr: str = ""


GitRunner = Callable[[tuple[str, ...], Path], GitCommandResult]


@dataclass(frozen=True, slots=True)
class RegisteredWorktree:
    """One entry returned by ``git worktree list --porcelain``."""

    path: Path
    head: str
    branch: str | None
    detached: bool
    locked: bool
    prunable: bool


@dataclass(frozen=True, slots=True)
class CleanupTaskReceipt:
    """Already-resolved task/PR state supplied by the orchestrator."""

    task_id: str
    path: Path
    branch: str
    task_state: str
    pr_merged: bool
    issue_completed: bool
    branch_open_pr: bool
    merged_sha: str | None
    in_use: bool


@dataclass(frozen=True, slots=True)
class CleanupManifest:
    """Bounded input for one post-merge or historical cleanup pass."""

    lifecycle: CleanupLifecycle
    main_sha: str
    worktrees: tuple[CleanupTaskReceipt, ...]


@dataclass(frozen=True, slots=True)
class CleanupDecision:
    """Decision and evidence summary for one requested worktree."""

    task_id: str
    path: Path
    action: CleanupAction
    reason: str


@dataclass(frozen=True, slots=True)
class CleanupSummary:
    """Machine-readable and bounded result of a cleanup pass."""

    repo: Path
    lifecycle: CleanupLifecycle
    main_sha: str
    dry_run: bool
    decisions: tuple[CleanupDecision, ...]
    prune_attempted: bool
    prune_succeeded: bool | None
    errors: tuple[str, ...]

    @property
    def removed(self) -> tuple[CleanupDecision, ...]:
        return tuple(item for item in self.decisions if item.action is CleanupAction.REMOVE)

    @property
    def kept(self) -> tuple[CleanupDecision, ...]:
        return tuple(item for item in self.decisions if item.action is CleanupAction.KEEP)

    def as_dict(self) -> dict[str, object]:
        """Return stable JSON-compatible output without command details."""

        return {
            "schema_version": WORKTREE_CLEANUP_SCHEMA_VERSION,
            "repo": str(self.repo),
            "lifecycle": self.lifecycle.value,
            "main_sha": self.main_sha,
            "dry_run": self.dry_run,
            "removed": [
                {"task_id": item.task_id, "path": str(item.path), "reason": item.reason}
                for item in self.removed
            ],
            "kept": [
                {"task_id": item.task_id, "path": str(item.path), "reason": item.reason}
                for item in self.kept
            ],
            "prune": (
                "not_run"
                if not self.prune_attempted
                else "success"
                if self.prune_succeeded
                else "failed"
            ),
            "errors": list(self.errors),
        }

    def render_text(self) -> str:
        """Render a bounded human-readable handoff summary."""

        lines = [
            f"lifecycle={self.lifecycle.value} dry_run={str(self.dry_run).lower()}",
            f"removed={len(self.removed)} kept={len(self.kept)} prune={self.as_dict()['prune']}",
        ]
        for item in self.decisions:
            lines.append(
                f"{item.action.value} task={_bounded(item.task_id)} "
                f"path={_bounded(str(item.path))} reason={_bounded(item.reason)}"
            )
        for error in self.errors:
            lines.append(f"error={_bounded(error)}")
        return "\n".join(lines)


def load_cleanup_manifest(source: str) -> CleanupManifest:
    """Parse one strict JSON receipt from a string."""

    try:
        raw: object = json.loads(source)
    except json.JSONDecodeError as exc:
        raise WorktreeCleanupInputError(f"invalid cleanup JSON: {exc.msg}") from None
    return _parse_manifest(raw)


def parse_worktree_porcelain(text: str) -> tuple[RegisteredWorktree, ...]:
    """Parse Git's porcelain worktree listing without filesystem discovery."""

    entries: list[RegisteredWorktree] = []
    current: dict[str, object] | None = None

    def flush() -> None:
        nonlocal current
        if current is None:
            return
        path = current.get("path")
        head = current.get("head")
        if not isinstance(path, str) or not path:
            raise WorktreeCleanupExecutionError("Git worktree listing has no path")
        if not isinstance(head, str) or _SHA_PATTERN.fullmatch(head) is None:
            raise WorktreeCleanupExecutionError("Git worktree listing has an invalid HEAD")
        entries.append(
            RegisteredWorktree(
                path=Path(path),
                head=head,
                branch=cast(str | None, current.get("branch")),
                detached=bool(current.get("detached", False)),
                locked=bool(current.get("locked", False)),
                prunable=bool(current.get("prunable", False)),
            )
        )
        current = None

    for line in text.splitlines():
        if not line:
            flush()
            continue
        key, separator, value = line.partition(" ")
        if key == "worktree":
            flush()
            current = {"path": value if separator else ""}
        elif current is None:
            raise WorktreeCleanupExecutionError(
                "Git worktree listing starts with an unknown record"
            )
        elif key == "HEAD":
            current["head"] = value if separator else ""
        elif key == "branch":
            current["branch"] = value if separator else ""
        elif key in {"detached", "bare"}:
            current["detached"] = True
        elif key == "locked":
            current["locked"] = True
        elif key == "prunable":
            current["prunable"] = True
        elif key in {"reason", "extension"}:
            continue
        else:
            raise WorktreeCleanupExecutionError(f"unknown Git worktree record: {key}")
    flush()
    return tuple(entries)


class WorktreeCleanup:
    """Run only bounded, Git-native cleanup against one repository."""

    def __init__(
        self,
        repo: Path,
        *,
        vault_roots: Sequence[Path] = (),
        current_cwd: Path | None = None,
        runner: GitRunner | None = None,
    ) -> None:
        self.repo = _absolute_lexical(repo)
        default_vault = self.repo.parent / "second-brain-vault"
        self.vault_roots = tuple(_absolute_lexical(root) for root in (default_vault, *vault_roots))
        self.current_cwd = _absolute_lexical(current_cwd or Path.cwd())
        self.runner = runner or _run_git

    def run(self, manifest: CleanupManifest, *, dry_run: bool = False) -> CleanupSummary:
        """Discover, classify, and optionally remove explicitly eligible entries."""

        decisions: list[CleanupDecision] = []
        errors: list[str] = []
        prune_attempted = False
        prune_succeeded: bool | None = None

        try:
            registered = self._discover()
            current_main_sha = self._current_main_sha()
        except WorktreeCleanupExecutionError as exc:
            errors.append(_bounded(str(exc)))
            return CleanupSummary(
                repo=self.repo,
                lifecycle=manifest.lifecycle,
                main_sha=manifest.main_sha,
                dry_run=dry_run,
                decisions=tuple(
                    CleanupDecision(
                        item.task_id,
                        _manifest_path(self.repo, item.path),
                        CleanupAction.KEEP,
                        "discovery_failed",
                    )
                    for item in manifest.worktrees
                ),
                prune_attempted=False,
                prune_succeeded=None,
                errors=tuple(errors),
            )

        by_path: dict[str, RegisteredWorktree] = {}
        duplicate_paths: set[str] = set()
        for item in registered:
            key = _path_key(_absolute_lexical(item.path))
            if key in by_path:
                duplicate_paths.add(key)
            by_path[key] = item

        main_sha_is_current = current_main_sha == manifest.main_sha
        for task in manifest.worktrees:
            path = _manifest_path(self.repo, task.path)
            decision = self._classify(
                manifest,
                task,
                path,
                by_path,
                duplicate_paths,
                main_sha_is_current=main_sha_is_current,
            )
            if decision.reason == "eligible" and not dry_run:
                result = self._git(("worktree", "remove", str(path)))
                if result.returncode == 0:
                    decisions.append(
                        CleanupDecision(task.task_id, path, CleanupAction.REMOVE, "removed")
                    )
                else:
                    decisions.append(
                        CleanupDecision(
                            task.task_id,
                            path,
                            CleanupAction.KEEP,
                            "cleanup_deferred:git_worktree_remove_failed",
                        )
                    )
                    errors.append("git_worktree_remove_failed")
                continue
            decisions.append(
                CleanupDecision(
                    task.task_id,
                    path,
                    CleanupAction.KEEP if decision.reason != "eligible" else CleanupAction.REMOVE,
                    "dry_run_eligible" if decision.reason == "eligible" else decision.reason,
                )
            )

        if not dry_run and any(item.action is CleanupAction.REMOVE for item in decisions):
            preflight = self._git(("worktree", "prune", "--dry-run", "--verbose"))
            if preflight.returncode != 0:
                errors.append("cleanup_deferred:git_worktree_prune_preflight_failed")
            elif preflight.stdout or preflight.stderr:
                # A registered entry proposed by prune is not part of the
                # verified, non-prunable cleanup set above.  Keep it rather
                # than trying to correlate opaque Git admin paths with an
                # unverified registration.
                errors.append("cleanup_deferred:unrelated_prune_registration")
            else:
                prune_attempted = True
                result = self._git(("worktree", "prune"))
                prune_succeeded = result.returncode == 0
                if not prune_succeeded:
                    errors.append("git_worktree_prune_failed")

        return CleanupSummary(
            repo=self.repo,
            lifecycle=manifest.lifecycle,
            main_sha=manifest.main_sha,
            dry_run=dry_run,
            decisions=tuple(decisions),
            prune_attempted=prune_attempted,
            prune_succeeded=prune_succeeded,
            errors=tuple(errors),
        )

    def _discover(self) -> tuple[RegisteredWorktree, ...]:
        result = self._git(("worktree", "list", "--porcelain"))
        if result.returncode != 0:
            raise WorktreeCleanupExecutionError("git worktree list failed")
        return parse_worktree_porcelain(result.stdout)

    def _current_main_sha(self) -> str:
        result = self._git(("rev-parse", "refs/heads/main"))
        if result.returncode != 0:
            raise WorktreeCleanupExecutionError("current main SHA could not be proven")
        sha = result.stdout.strip()
        if _SHA_PATTERN.fullmatch(sha) is None:
            raise WorktreeCleanupExecutionError("current main SHA is invalid")
        return sha

    def _git(self, args: tuple[str, ...], *, cwd: Path | None = None) -> GitCommandResult:
        return self.runner(args, cwd or self.repo)

    def _classify(
        self,
        manifest: CleanupManifest,
        task: CleanupTaskReceipt,
        path: Path,
        by_path: Mapping[str, RegisteredWorktree],
        duplicate_paths: set[str],
        *,
        main_sha_is_current: bool,
    ) -> CleanupDecision:
        def keep(reason: str) -> CleanupDecision:
            return CleanupDecision(task.task_id, path, CleanupAction.KEEP, reason)

        key = _path_key(path)
        if key in duplicate_paths:
            return keep("duplicate_registered_path")
        registered = by_path.get(key)
        if registered is None:
            return keep("worktree_not_registered")
        if _same_path(path, self.repo):
            return keep("primary_worktree")
        if self._is_vault_path(path):
            return keep("protected_vault_worktree")
        if registered.detached or registered.branch is None:
            return keep("detached_or_unknown_worktree")
        if task.branch != registered.branch:
            return keep("branch_mapping_mismatch")
        if registered.locked:
            return keep("worktree_locked")
        if registered.prunable:
            return keep("worktree_prunable")
        if _contains_reparse_point(path):
            return keep("reparse_or_symlink_path")
        if not path.exists():
            return keep("worktree_path_missing")
        if _is_within(self.current_cwd, path):
            return keep("current_cwd_in_worktree")
        if task.in_use:
            return keep("worktree_in_active_use")
        if task.branch_open_pr:
            return keep("branch_has_open_pr")

        evidence = evaluate_post_task_cleanup(
            PostTaskCleanupEvidence(
                lifecycle=manifest.lifecycle,
                task_state=task.task_state,
                pr_merged=task.pr_merged,
                issue_completed=task.issue_completed,
                main_sha=manifest.main_sha,
                worktree_registered=True,
            )
        )
        if evidence.status.value != "ready":
            return keep(evidence.reasons[0])
        if not main_sha_is_current:
            return keep("main_sha_not_current")
        if task.merged_sha is None:
            return keep("merged_sha_missing")
        if not self._merged_sha_is_reachable(task.merged_sha, manifest.main_sha):
            return keep("merged_history_not_proven")

        status = self._git(
            ("status", "--porcelain", "--ignored", "--untracked-files=all"), cwd=path
        )
        if status.returncode != 0:
            return keep("worktree_status_failed")
        if status.stdout:
            return keep("worktree_dirty")
        return CleanupDecision(task.task_id, path, CleanupAction.KEEP, "eligible")

    def _merged_sha_is_reachable(self, merged_sha: str, main_sha: str) -> bool:
        result = self._git(("merge-base", "--is-ancestor", merged_sha, main_sha))
        return result.returncode == 0

    def _is_vault_path(self, path: Path) -> bool:
        if path.name.casefold() == "second-brain-vault":
            return True
        return any(_is_within(path, root) for root in self.vault_roots)


def _parse_manifest(raw: object) -> CleanupManifest:
    data = _mapping(raw, "cleanup receipt")
    _exact_keys(data, {"schema_version", "lifecycle", "main_sha", "worktrees"}, "cleanup receipt")
    schema = _integer(data["schema_version"], "schema_version")
    if schema != WORKTREE_CLEANUP_SCHEMA_VERSION:
        raise WorktreeCleanupInputError(f"unsupported cleanup schema_version: {schema}")
    lifecycle_raw = _string(data["lifecycle"], "lifecycle")
    try:
        lifecycle = CleanupLifecycle(lifecycle_raw)
    except ValueError:
        raise WorktreeCleanupInputError(f"unsupported cleanup lifecycle: {lifecycle_raw}") from None
    main_sha = _sha(data["main_sha"], "main_sha")
    raw_items = data["worktrees"]
    if not isinstance(raw_items, list):
        raise WorktreeCleanupInputError("worktrees must be a JSON array")
    if len(raw_items) > MAX_RECEIPT_WORKTREES:
        raise WorktreeCleanupInputError("worktrees exceeds bounded receipt limit")
    items = tuple(_parse_task(item, index) for index, item in enumerate(raw_items))
    keys = [_path_key(item.path) for item in items]
    if len(keys) != len(set(keys)):
        raise WorktreeCleanupInputError("worktrees contains duplicate paths")
    return CleanupManifest(lifecycle=lifecycle, main_sha=main_sha, worktrees=items)


def _parse_task(raw: object, index: int) -> CleanupTaskReceipt:
    data = _mapping(raw, f"worktrees[{index}]")
    _exact_keys(
        data,
        {
            "task_id",
            "path",
            "branch",
            "task_state",
            "pr_merged",
            "issue_completed",
            "branch_open_pr",
            "merged_sha",
            "in_use",
        },
        f"worktrees[{index}]",
    )
    task_state = _string(data["task_state"], f"worktrees[{index}].task_state")
    if task_state not in _TASK_STATE_VALUES:
        raise WorktreeCleanupInputError(f"unsupported task state: {task_state}")
    merged_raw = data["merged_sha"]
    merged_sha = None if merged_raw is None else _sha(merged_raw, f"worktrees[{index}].merged_sha")
    return CleanupTaskReceipt(
        task_id=_bounded_field(data["task_id"], f"worktrees[{index}].task_id"),
        path=Path(_bounded_field(data["path"], f"worktrees[{index}].path")),
        branch=_branch(data["branch"], f"worktrees[{index}].branch"),
        task_state=task_state,
        pr_merged=_boolean(data["pr_merged"], f"worktrees[{index}].pr_merged"),
        issue_completed=_boolean(data["issue_completed"], f"worktrees[{index}].issue_completed"),
        branch_open_pr=_boolean(data["branch_open_pr"], f"worktrees[{index}].branch_open_pr"),
        merged_sha=merged_sha,
        in_use=_boolean(data["in_use"], f"worktrees[{index}].in_use"),
    )


def _run_git(args: tuple[str, ...], cwd: Path) -> GitCommandResult:
    try:
        completed = subprocess.run(
            ["git", "-C", str(cwd), *args],
            capture_output=True,
            check=False,
            shell=False,
            text=True,
            timeout=GIT_COMMAND_TIMEOUT_SECONDS,
        )
    except OSError, subprocess.TimeoutExpired:
        return GitCommandResult(returncode=124, stderr="bounded git command failure")
    return GitCommandResult(
        returncode=completed.returncode,
        stdout=completed.stdout,
        stderr=_bounded(completed.stderr),
    )


def _manifest_path(repo: Path, path: Path) -> Path:
    return _absolute_lexical(path if path.is_absolute() else repo / path)


def _absolute_lexical(path: Path) -> Path:
    return Path(os.path.abspath(os.fspath(path)))


def _path_key(path: Path) -> str:
    return os.path.normcase(os.path.normpath(os.path.abspath(os.fspath(path))))


def _same_path(left: Path, right: Path) -> bool:
    return _path_key(left) == _path_key(right)


def _is_within(path: Path, root: Path) -> bool:
    child = _path_key(path)
    parent = _path_key(root).rstrip("\\/")
    return child == parent or child.startswith(parent + os.sep)


def _contains_reparse_point(path: Path) -> bool:
    current = path
    while True:
        try:
            info = os.lstat(current)
        except OSError:
            return True
        if stat.S_ISLNK(info.st_mode):
            return True
        if getattr(info, "st_file_attributes", 0) & getattr(
            stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400
        ):
            return True
        if current.parent == current:
            return False
        current = current.parent


def _mapping(value: object, label: str) -> Mapping[str, object]:
    if not isinstance(value, dict):
        raise WorktreeCleanupInputError(f"{label} must be an object")
    return cast(Mapping[str, object], value)


def _exact_keys(data: Mapping[str, object], expected: set[str], label: str) -> None:
    actual = set(data)
    if actual != expected:
        missing = sorted(expected - actual)
        extra = sorted(actual - expected)
        detail = f"missing={missing}" if missing else f"extra={extra}"
        raise WorktreeCleanupInputError(f"{label} keys mismatch: {detail}")


def _string(value: object, label: str) -> str:
    if not isinstance(value, str) or not value or "\x00" in value:
        raise WorktreeCleanupInputError(f"{label} must be a non-empty string")
    return value


def _bounded_field(value: object, label: str) -> str:
    text = _string(value, label)
    if len(text.encode("utf-8")) > MAX_TEXT_FIELD_BYTES:
        raise WorktreeCleanupInputError(f"{label} exceeds bounded text limit")
    return text


def _boolean(value: object, label: str) -> bool:
    if type(value) is not bool:
        raise WorktreeCleanupInputError(f"{label} must be boolean")
    return value


def _integer(value: object, label: str) -> int:
    if type(value) is not int:
        raise WorktreeCleanupInputError(f"{label} must be integer")
    return value


def _sha(value: object, label: str) -> str:
    text = _string(value, label)
    if _SHA_PATTERN.fullmatch(text) is None:
        raise WorktreeCleanupInputError(f"{label} must be a full Git SHA")
    return text.lower()


def _branch(value: object, label: str) -> str:
    text = _bounded_field(value, label)
    if _BRANCH_PATTERN.fullmatch(text) is None:
        raise WorktreeCleanupInputError(f"{label} must be a refs/heads branch")
    return text


def _bounded(value: str, limit: int = MAX_TEXT_FIELD_BYTES) -> str:
    encoded = value.encode("utf-8", errors="replace")
    if len(encoded) <= limit:
        return value
    return encoded[:limit].decode("utf-8", errors="ignore") + "..."
