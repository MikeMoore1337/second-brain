"""Deterministic temp-Git coverage for the safe worktree cleanup protocol."""

from __future__ import annotations

import os
import subprocess
from dataclasses import replace
from pathlib import Path

import pytest

from second_brain.application.night_shift import CleanupLifecycle
from second_brain.application.worktree_cleanup import (
    CleanupAction,
    CleanupManifest,
    CleanupTaskReceipt,
    GitCommandResult,
    GitRunner,
    WorktreeCleanup,
    WorktreeCleanupInputError,
    _run_git,
    load_cleanup_manifest,
)


def _git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        capture_output=True,
        text=True,
        shell=False,
    )
    return result.stdout.strip()


def _fixture(tmp_path: Path) -> tuple[Path, Path, str, str, str]:
    repo = tmp_path / "primary"
    repo.mkdir()
    _git(repo, "init", "--initial-branch=main")
    _git(repo, "config", "user.name", "Test User")
    _git(repo, "config", "user.email", "test@example.invalid")
    (repo / "README.md").write_text("base\n", encoding="utf-8")
    _git(repo, "add", "README.md")
    _git(repo, "commit", "-m", "base")

    task_path = tmp_path / "task worktree with spaces"
    branch = "refs/heads/feature/cleanup-test"
    _git(repo, "worktree", "add", "-b", branch.removeprefix("refs/heads/"), str(task_path))
    (task_path / "README.md").write_text("task\n", encoding="utf-8")
    _git(task_path, "add", "README.md")
    _git(task_path, "commit", "-m", "task")
    task_sha = _git(task_path, "rev-parse", "HEAD")

    (repo / "README.md").write_text("merged\n", encoding="utf-8")
    _git(repo, "add", "README.md")
    _git(repo, "commit", "-m", "squash task")
    main_sha = _git(repo, "rev-parse", "refs/heads/main")
    return repo, task_path, branch, task_sha, main_sha


def _receipt(
    repo: Path,
    task_path: Path,
    branch: str,
    task_sha: str,
    main_sha: str,
    **changes: object,
) -> CleanupManifest:
    task = CleanupTaskReceipt(
        task_id="#128-test",
        path=task_path,
        branch=branch,
        task_state="merged",
        pr_merged=True,
        issue_completed=True,
        branch_open_pr=False,
        merged_sha=main_sha,
        in_use=False,
    )
    if "task_state" in changes:
        task = replace(task, task_state=str(changes["task_state"]))
    if "pr_merged" in changes:
        task = replace(task, pr_merged=bool(changes["pr_merged"]))
    if "issue_completed" in changes:
        task = replace(task, issue_completed=bool(changes["issue_completed"]))
    if "branch_open_pr" in changes:
        task = replace(task, branch_open_pr=bool(changes["branch_open_pr"]))
    if "merged_sha" in changes:
        value = changes["merged_sha"]
        task = replace(task, merged_sha=None if value is None else str(value))
    if "in_use" in changes:
        task = replace(task, in_use=bool(changes["in_use"]))
    return CleanupManifest(
        lifecycle=CleanupLifecycle.VERIFIED_GREEN_MERGE,
        main_sha=main_sha,
        worktrees=(task,),
    )


def _cleaner(repo: Path, tmp_path: Path, runner: GitRunner | None = None) -> WorktreeCleanup:
    outside = tmp_path / "outside"
    outside.mkdir(exist_ok=True)
    return WorktreeCleanup(repo, current_cwd=outside, runner=runner)


def test_merged_clean_worktree_with_spaces_is_removed_and_pruned(tmp_path: Path) -> None:
    repo, task_path, branch, task_sha, main_sha = _fixture(tmp_path)

    summary = _cleaner(repo, tmp_path).run(_receipt(repo, task_path, branch, task_sha, main_sha))

    assert [item.action for item in summary.decisions] == [CleanupAction.REMOVE]
    assert summary.prune_attempted is True
    assert summary.prune_succeeded is True
    assert not task_path.exists()
    assert str(task_path) not in _git(repo, "worktree", "list", "--porcelain")
    assert _git(repo, "show-ref", "--verify", f"refs/heads/{branch.removeprefix('refs/heads/')}")


@pytest.mark.parametrize(
    ("changes", "reason"),
    (
        ({"task_state": "human_required"}, "task_is_not_merged"),
        ({"pr_merged": False}, "pr_is_not_merged"),
        ({"issue_completed": False}, "issue_is_not_completed"),
        ({"branch_open_pr": True}, "branch_has_open_pr"),
        ({"in_use": True}, "worktree_in_active_use"),
        ({"merged_sha": None}, "merged_sha_missing"),
    ),
)
def test_active_or_incomplete_state_is_kept(
    tmp_path: Path, changes: dict[str, object], reason: str
) -> None:
    repo, task_path, branch, task_sha, main_sha = _fixture(tmp_path)

    summary = _cleaner(repo, tmp_path).run(
        _receipt(repo, task_path, branch, task_sha, main_sha, **changes)
    )

    assert summary.decisions[0].action is CleanupAction.KEEP
    assert summary.decisions[0].reason == reason
    assert summary.prune_attempted is False
    assert task_path.exists()


def test_primary_worktree_is_never_removed(tmp_path: Path) -> None:
    repo, task_path, _branch, _task_sha, main_sha = _fixture(tmp_path)
    task = CleanupTaskReceipt(
        task_id="#128-primary",
        path=repo,
        branch="refs/heads/main",
        task_state="merged",
        pr_merged=True,
        issue_completed=True,
        branch_open_pr=False,
        merged_sha=main_sha,
        in_use=False,
    )

    summary = _cleaner(repo, tmp_path).run(
        CleanupManifest(CleanupLifecycle.HISTORICAL_ORPHAN_PASS, main_sha, (task,))
    )

    assert summary.decisions[0].reason == "primary_worktree"
    assert repo.exists()
    assert task_path.exists()


def test_vault_named_worktree_is_never_removed(tmp_path: Path) -> None:
    repo, _task_path, _branch, _task_sha, main_sha = _fixture(tmp_path)
    vault = tmp_path / "second-brain-vault"
    branch = "refs/heads/feature/vault-name"
    _git(repo, "worktree", "add", "-b", "feature/vault-name", str(vault), main_sha)
    task = CleanupTaskReceipt(
        task_id="#128-vault",
        path=vault,
        branch=branch,
        task_state="merged",
        pr_merged=True,
        issue_completed=True,
        branch_open_pr=False,
        merged_sha=main_sha,
        in_use=False,
    )

    summary = _cleaner(repo, tmp_path).run(
        CleanupManifest(CleanupLifecycle.HISTORICAL_ORPHAN_PASS, main_sha, (task,))
    )

    assert summary.decisions[0].reason == "protected_vault_worktree"
    assert vault.exists()


def test_current_cwd_inside_candidate_is_kept(tmp_path: Path) -> None:
    repo, task_path, branch, task_sha, main_sha = _fixture(tmp_path)
    cleaner = WorktreeCleanup(repo, current_cwd=task_path)

    summary = cleaner.run(_receipt(repo, task_path, branch, task_sha, main_sha))

    assert summary.decisions[0].reason == "current_cwd_in_worktree"
    assert task_path.exists()


def test_dirty_tracked_and_untracked_worktrees_are_kept(tmp_path: Path) -> None:
    for filename, expected in (
        ("README.md", "worktree_dirty"),
        ("untracked.txt", "worktree_dirty"),
    ):
        case = tmp_path / filename.replace(".", "-")
        case.mkdir()
        repo, task_path, branch, task_sha, main_sha = _fixture(case)
        (task_path / filename).write_text("dirty\n", encoding="utf-8")

        summary = _cleaner(repo, case).run(_receipt(repo, task_path, branch, task_sha, main_sha))

        assert summary.decisions[0].reason == expected
        assert summary.prune_attempted is False
        assert task_path.exists()


def test_detached_worktree_is_kept(tmp_path: Path) -> None:
    repo, _task_path, _branch, _task_sha, main_sha = _fixture(tmp_path)
    detached = tmp_path / "detached worktree"
    _git(repo, "worktree", "add", "--detach", str(detached), main_sha)
    task = CleanupTaskReceipt(
        task_id="#128-detached",
        path=detached,
        branch="refs/heads/feature/not-proven",
        task_state="merged",
        pr_merged=True,
        issue_completed=True,
        branch_open_pr=False,
        merged_sha=main_sha,
        in_use=False,
    )

    summary = _cleaner(repo, tmp_path).run(
        CleanupManifest(CleanupLifecycle.HISTORICAL_ORPHAN_PASS, main_sha, (task,))
    )

    assert summary.decisions[0].reason == "detached_or_unknown_worktree"
    assert detached.exists()


def test_dry_run_has_no_remove_or_prune_side_effect(tmp_path: Path) -> None:
    repo, task_path, branch, task_sha, main_sha = _fixture(tmp_path)
    calls: list[tuple[str, ...]] = []

    def recording_runner(args: tuple[str, ...], cwd: Path) -> GitCommandResult:
        calls.append(args)
        return _run_git(args, cwd)

    summary = _cleaner(repo, tmp_path, runner=recording_runner).run(
        _receipt(repo, task_path, branch, task_sha, main_sha), dry_run=True
    )

    assert summary.decisions[0].reason == "dry_run_eligible"
    assert summary.decisions[0].action is CleanupAction.REMOVE
    assert task_path.exists()
    assert not any(args[:2] == ("worktree", "remove") for args in calls)
    assert not any(args[:2] == ("worktree", "prune") for args in calls)


def test_remove_failure_keeps_worktree_without_force_or_raw_fallback(tmp_path: Path) -> None:
    repo, task_path, branch, task_sha, main_sha = _fixture(tmp_path)
    calls: list[tuple[str, ...]] = []

    def failing_runner(args: tuple[str, ...], cwd: Path) -> GitCommandResult:
        calls.append(args)
        if args[:2] == ("worktree", "remove"):
            return GitCommandResult(1, stderr="locked")
        return _run_git(args, cwd)

    summary = _cleaner(repo, tmp_path, runner=failing_runner).run(
        _receipt(repo, task_path, branch, task_sha, main_sha)
    )

    assert summary.decisions[0].action is CleanupAction.KEEP
    assert summary.decisions[0].reason == "cleanup_deferred:git_worktree_remove_failed"
    assert summary.prune_attempted is False
    assert "git_worktree_remove_failed" in summary.errors
    assert task_path.exists()
    assert all("--force" not in args for args in calls)
    assert all(args[:2] != ("rm", "-rf") for args in calls)


def test_path_normalization_does_not_use_prefix_matching(tmp_path: Path) -> None:
    repo, task_path, branch, task_sha, main_sha = _fixture(tmp_path)
    normalized = task_path.parent / "." / task_path.name / ".." / task_path.name

    summary = _cleaner(repo, tmp_path).run(_receipt(repo, normalized, branch, task_sha, main_sha))

    assert summary.decisions[0].action is CleanupAction.REMOVE
    assert not task_path.exists()


@pytest.mark.skipif(os.name != "nt", reason="case normalization is Windows-specific")
def test_case_normalization_matches_registered_windows_path(tmp_path: Path) -> None:
    repo, task_path, branch, task_sha, main_sha = _fixture(tmp_path)
    case_variant = Path(str(task_path).upper())

    summary = _cleaner(repo, tmp_path).run(_receipt(repo, case_variant, branch, task_sha, main_sha))

    assert summary.decisions[0].action is CleanupAction.REMOVE
    assert not task_path.exists()


def test_manifest_is_strict_and_bounded() -> None:
    with pytest.raises(WorktreeCleanupInputError, match="keys mismatch"):
        load_cleanup_manifest('{"schema_version": 1, "lifecycle": "verified_green_merge"}')
