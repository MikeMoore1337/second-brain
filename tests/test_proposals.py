"""Проверки Git proposal workflow без live GitHub/network."""

from __future__ import annotations

import hashlib
import json
import subprocess
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid7

import pytest
from typer.testing import CliRunner

from second_brain.adapters.commands import CommandResult
from second_brain.adapters.git import GitVersionControlAdapter
from second_brain.adapters.github import GitHubPullRequestAdapter, parse_pull_request_url
from second_brain.application.ports import ProposalPortError
from second_brain.application.proposals import (
    CreateNoteProposal,
    CreateNoteProposalRequest,
    ProposalStatus,
)
from second_brain.application.reports import Diagnostic, DiagnosticSeverity
from second_brain.application.writes import (
    CreateManagedNoteRequest,
    CreateManagedNoteResult,
    CreateNotePlan,
    CreateStatus,
    WriteReceipt,
)
from second_brain.domain.models import NoteType
from second_brain.entrypoints.cli.app import app
from tests.conftest import create_vault, snapshot_tree

cli_runner = CliRunner()


class FakeNoteCreator:
    def __init__(
        self,
        result: CreateManagedNoteResult,
        events: list[str],
        rollback_result: bool = True,
    ) -> None:
        self.result = result
        self.events = events
        self.rollback_result = rollback_result
        self.requests: list[CreateManagedNoteRequest] = []

    def execute(self, request: CreateManagedNoteRequest) -> CreateManagedNoteResult:
        self.events.append("note.execute")
        self.requests.append(request)
        return self.result

    def rollback(self, receipt: WriteReceipt) -> bool:
        assert receipt is self.result.receipt
        self.events.append("note.rollback")
        return self.rollback_result


class FakeVersionControl:
    def __init__(
        self,
        events: list[str],
        *,
        changed_paths: tuple[str, ...] = ("10 Projects/Proposal.md",),
        staged_paths: tuple[str, ...] = ("10 Projects/Proposal.md",),
        preflight_error: ProposalPortError | None = None,
        cleanup_paths: tuple[str, ...] = (),
        commit_error: ProposalPortError | None = None,
        push_error: ProposalPortError | None = None,
    ) -> None:
        self.events = events
        self._changed_paths = changed_paths
        self._staged_paths = staged_paths
        self.preflight_error = preflight_error
        self.cleanup_paths = cleanup_paths
        self.commit_error = commit_error
        self.push_error = push_error
        self.stage_paths: list[str] = []
        self.commit_paths: list[str] = []
        self.commit_messages: list[str] = []
        self.preflight_options: list[tuple[bool, bool]] = []

    def preflight(
        self,
        branch_name: str,
        *,
        require_synced_main: bool,
        check_remote_branch: bool,
    ) -> None:
        self.events.append("git.preflight")
        self.preflight_options.append((require_synced_main, check_remote_branch))
        if self.preflight_error is not None:
            raise self.preflight_error

    def fetch_main(self) -> None:
        self.events.append("git.fetch")

    def create_branch(self, branch_name: str) -> None:
        self.events.append("git.branch")

    def changed_paths(self) -> tuple[str, ...]:
        self.events.append("git.changed")
        if "git.rollback-status" in self.events:
            return self.cleanup_paths
        return self._changed_paths

    def stage_exact_path(self, relative_path: str) -> None:
        self.events.append("git.stage")
        self.stage_paths.append(relative_path)

    def staged_paths(self) -> tuple[str, ...]:
        self.events.append("git.staged")
        return self._staged_paths

    def unstage_exact_path(self, relative_path: str) -> None:
        self.events.append("git.unstage")

    def head_sha(self) -> str:
        self.events.append("git.head")
        return "0" * 40

    def commit_exact_path(self, relative_path: str, message: str) -> str:
        self.events.append("git.commit")
        self.commit_paths.append(relative_path)
        self.commit_messages.append(message)
        if self.commit_error is not None:
            raise self.commit_error
        return "1" * 40

    def push(self, branch_name: str) -> None:
        self.events.append("git.push")
        if self.push_error is not None:
            raise self.push_error

    def switch_to_main(self) -> None:
        self.events.append("git.switch-main")

    def delete_local_branch(self, branch_name: str) -> None:
        self.events.append("git.delete-branch")


class FakePullRequest:
    def __init__(
        self,
        events: list[str],
        *,
        create_error: ProposalPortError | None = None,
    ) -> None:
        self.events = events
        self.create_error = create_error
        self.requests: list[dict[str, str]] = []

    def check_auth(self) -> None:
        self.events.append("pr.auth")

    def create(self, *, title: str, base: str, head: str, body: str) -> str:
        self.events.append("pr.create")
        self.requests.append({"title": title, "base": base, "head": head, "body": body})
        if self.create_error is not None:
            raise self.create_error
        return "https://github.com/MikeMoore1337/second-brain/pull/123"


def make_note_result(status: CreateStatus = CreateStatus.CREATED) -> CreateManagedNoteResult:
    plan = CreateNotePlan(
        NoteType.PROJECT,
        "Proposal",
        uuid7(),
        datetime(2026, 9, 2, 15, 4, 5, tzinfo=UTC),
        "10 Projects/Proposal.md",
        "---\nid: proposal\n---\n# Proposal\n",
        "10 Projects",
    )
    content_sha256 = hashlib.sha256(plan.content.encode()).hexdigest()
    receipt = WriteReceipt("target", plan.relative_path, content_sha256, (1, 1))
    return CreateManagedNoteResult(
        status,
        plan=plan,
        apply_requested=status is CreateStatus.CREATED,
        receipt=receipt if status is CreateStatus.CREATED else None,
        diagnostics=(
            Diagnostic(
                "CREATE_FAILED",
                "fake Safe Write failure",
                DiagnosticSeverity.ERROR,
            ),
        )
        if status is not CreateStatus.CREATED
        else (),
    )


def make_workflow(
    note_result: CreateManagedNoteResult,
    events: list[str],
    *,
    git: FakeVersionControl | None = None,
    pull_request: FakePullRequest | None = None,
) -> tuple[CreateNoteProposal, FakeNoteCreator, FakeVersionControl, FakePullRequest]:
    note_creator = FakeNoteCreator(note_result, events)
    version_control = git or FakeVersionControl(events)
    pr = pull_request or FakePullRequest(events)
    workflow = CreateNoteProposal(
        note_creator,
        version_control,
        pr,
        branch_factory=lambda: "automation/note-test",
    )
    return workflow, note_creator, version_control, pr


def test_dry_run_isolated_and_does_not_call_mutating_or_pr_methods() -> None:
    events: list[str] = []
    workflow, note_creator, git, pr = make_workflow(
        make_note_result(CreateStatus.DRY_RUN),
        events,
    )

    result = workflow.execute(
        CreateNoteProposalRequest(
            NoteType.PROJECT, "Proposal", now=datetime(2026, 9, 2, tzinfo=UTC)
        )
    )

    assert result.status is ProposalStatus.DRY_RUN
    assert result.commit_sha is None
    assert result.pr_url is None
    assert events == ["git.preflight", "note.execute"]
    assert git.preflight_options == [(True, False)]
    assert note_creator.requests[0].apply is False
    assert pr.requests == []


@pytest.mark.parametrize(
    "error",
    [
        ProposalPortError("PROPOSAL_DIRTY_WORKTREE", "dirty"),
        ProposalPortError("PROPOSAL_MAIN_REQUIRED", "not main"),
        ProposalPortError("PROPOSAL_MAIN_NOT_SYNCED", "not synced"),
    ],
)
def test_apply_rejects_git_preflight_without_note_or_mutation(
    error: ProposalPortError,
) -> None:
    events: list[str] = []
    git = FakeVersionControl(events, preflight_error=error)
    workflow, note_creator, _, pr = make_workflow(make_note_result(), events, git=git)

    result = workflow.execute(CreateNoteProposalRequest(NoteType.PROJECT, "Proposal", apply=True))

    assert result.status is ProposalStatus.REJECTED
    assert result.diagnostics[0].code == error.code
    assert events == ["git.preflight"]
    assert note_creator.requests == []
    assert pr.requests == []


def test_note_creation_failure_stops_before_commit_push_and_pr() -> None:
    events: list[str] = []
    workflow, _, git, pr = make_workflow(
        make_note_result(CreateStatus.REJECTED),
        events,
    )

    result = workflow.execute(CreateNoteProposalRequest(NoteType.PROJECT, "Proposal", apply=True))

    assert result.status is ProposalStatus.REJECTED
    assert "PROPOSAL_NOTE_CREATE_FAILED" in {item.code for item in result.diagnostics}
    assert events == [
        "git.preflight",
        "git.fetch",
        "git.preflight",
        "pr.auth",
        "git.branch",
        "note.execute",
    ]
    assert git.commit_paths == []
    assert pr.requests == []


def test_extra_changed_path_is_rejected_and_only_safe_note_is_rolled_back() -> None:
    events: list[str] = []
    git = FakeVersionControl(
        events,
        changed_paths=("10 Projects/Proposal.md", "other.md"),
        cleanup_paths=(),
    )
    workflow, note_creator, _, pr = make_workflow(make_note_result(), events, git=git)

    def mark_rollback(receipt: WriteReceipt) -> bool:
        events.append("note.rollback")
        events.append("git.rollback-status")
        return True

    note_creator.rollback = mark_rollback  # type: ignore[method-assign]
    result = workflow.execute(CreateNoteProposalRequest(NoteType.PROJECT, "Proposal", apply=True))

    assert result.status is ProposalStatus.REJECTED
    assert any(item.code == "PROPOSAL_UNEXPECTED_CHANGES" for item in result.diagnostics)
    assert events[-5:] == [
        "note.rollback",
        "git.rollback-status",
        "git.changed",
        "git.switch-main",
        "git.delete-branch",
    ]
    assert "git.stage" not in events
    assert git.commit_paths == []
    assert pr.requests == []


def test_success_order_stages_and_commits_only_exact_created_path() -> None:
    events: list[str] = []
    workflow, _, git, pr = make_workflow(make_note_result(), events)

    result = workflow.execute(CreateNoteProposalRequest(NoteType.PROJECT, "Proposal", apply=True))

    assert result.status is ProposalStatus.CREATED_PR
    assert result.commit_sha == "1" * 40
    assert result.pr_url == "https://github.com/MikeMoore1337/second-brain/pull/123"
    assert events == [
        "git.preflight",
        "git.fetch",
        "git.preflight",
        "pr.auth",
        "git.branch",
        "note.execute",
        "git.changed",
        "git.stage",
        "git.staged",
        "git.head",
        "git.commit",
        "git.push",
        "pr.create",
    ]
    assert git.stage_paths == ["10 Projects/Proposal.md"]
    assert git.commit_paths == ["10 Projects/Proposal.md"]
    assert git.commit_messages == ["note: add project Proposal"]
    assert pr.requests[0]["base"] == "main"
    assert pr.requests[0]["head"] == "automation/note-test"


def test_pr_failure_is_partial_and_keeps_pushed_state() -> None:
    events: list[str] = []
    pr = FakePullRequest(
        events,
        create_error=ProposalPortError("PROPOSAL_PR_CREATE_FAILED", "gh failed"),
    )
    workflow, note_creator, git, _ = make_workflow(make_note_result(), events, pull_request=pr)

    result = workflow.execute(CreateNoteProposalRequest(NoteType.PROJECT, "Proposal", apply=True))

    assert result.status is ProposalStatus.PARTIAL
    assert result.remote_branch_pushed is True
    assert result.commit_sha == "1" * 40
    assert result.pr_url is None
    assert any(item.code == "PROPOSAL_PR_CREATE_FAILED" for item in result.diagnostics)
    assert "note.rollback" not in events
    assert "git.delete-branch" not in events
    assert git.commit_paths == ["10 Projects/Proposal.md"]
    assert note_creator.requests[0].apply is True


class RecordingRunner:
    def __init__(self, results: list[CommandResult]) -> None:
        self.results = results
        self.calls: list[tuple[tuple[str, ...], Path]] = []

    def __call__(self, argv: Sequence[str], cwd: Path) -> CommandResult:
        self.calls.append((tuple(argv), cwd))
        return self.results.pop(0)


def test_github_adapter_uses_explicit_argv_and_never_merge(tmp_path: Path) -> None:
    root = tmp_path
    runner = RecordingRunner(
        [
            CommandResult(0, "Logged in", ""),
            CommandResult(0, "https://github.com/MikeMoore1337/second-brain/pull/42\n", ""),
        ]
    )
    adapter = GitHubPullRequestAdapter(root, runner)

    adapter.check_auth()
    url = adapter.create(
        title="note: add project Proposal",
        base="main",
        head="automation/note-test",
        body="body",
    )

    assert url.endswith("/pull/42")
    assert runner.calls[0][0] == ("gh", "auth", "status")
    assert runner.calls[1][0] == (
        "gh",
        "pr",
        "create",
        "--base",
        "main",
        "--head",
        "automation/note-test",
        "--title",
        "note: add project Proposal",
        "--body",
        "body",
    )
    assert all("merge" not in call[0] for call in runner.calls)


def test_github_adapter_maps_stderr_and_rejects_invalid_pr_url(tmp_path: Path) -> None:
    root = tmp_path
    auth_runner = RecordingRunner([CommandResult(1, "", "not authenticated")])
    with pytest.raises(ProposalPortError, match="not authenticated") as auth_error:
        GitHubPullRequestAdapter(root, auth_runner).check_auth()
    assert auth_error.value.code == "PROPOSAL_GH_AUTH_FAILED"

    with pytest.raises(ProposalPortError) as url_error:
        parse_pull_request_url("created\nhttps://example.com/pull/1\n")
    assert url_error.value.code == "PROPOSAL_PR_URL_PARSE_FAILED"


def _run_git(cwd: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    return completed.stdout


def _git_repository_with_local_origin(tmp_path: Path) -> tuple[Path, Path]:
    repository = tmp_path / "repo"
    remote = tmp_path / "origin.git"
    repository.mkdir()
    _run_git(repository, "init", "-b", "main")
    _run_git(repository, "config", "user.name", "Proposal Test")
    _run_git(repository, "config", "user.email", "proposal@example.invalid")
    (repository / "seed.txt").write_text("seed\n", encoding="utf-8")
    _run_git(repository, "add", "--", "seed.txt")
    _run_git(repository, "commit", "-m", "initial")
    _run_git(remote.parent, "init", "--bare", str(remote))
    _run_git(repository, "remote", "add", "origin", str(remote))
    _run_git(repository, "push", "--set-upstream", "origin", "main")
    return repository, remote


def _git_vault_with_local_origin(tmp_path: Path) -> Path:
    vault = create_vault(tmp_path / "vault")
    for filename, content in {
        "Project.md": "# Project template\n",
        "Area.md": "# Area template\n",
        "Resource.md": "# Resource template\n",
        "Zettel.md": "# Zettel template\n",
    }.items():
        (vault / "_templates" / filename).write_text(content, encoding="utf-8")
    remote = tmp_path / "origin.git"
    _run_git(vault, "init", "-b", "main")
    _run_git(vault, "config", "user.name", "Proposal CLI Test")
    _run_git(vault, "config", "user.email", "proposal-cli@example.invalid")
    _run_git(vault, "add", "--", "second-brain.yaml", "_templates")
    _run_git(vault, "commit", "-m", "initial")
    _run_git(remote.parent, "init", "--bare", str(remote))
    _run_git(vault, "remote", "add", "origin", str(remote))
    _run_git(vault, "push", "--set-upstream", "origin", "main")
    return vault


def test_cli_proposal_dry_run_keeps_git_and_vault_unchanged(tmp_path: Path) -> None:
    vault = _git_vault_with_local_origin(tmp_path)
    before = snapshot_tree(vault)
    before_head = _run_git(vault, "rev-parse", "HEAD").strip()

    result = cli_runner.invoke(
        app,
        [
            "--vault-path",
            str(vault),
            "proposal",
            "note",
            "create",
            "--type",
            "project",
            "--title",
            "Dry proposal",
            "--format",
            "json",
        ],
    )

    assert result.exit_code == 0, result.stdout or result.stderr
    payload = json.loads(result.stdout)
    assert payload["status"] == "dry-run"
    assert payload["branch"].startswith("automation/note-")
    assert payload["commit_sha"] is None
    assert payload["pr_url"] is None
    assert payload["note"]["relative_path"] == "10 Projects/Dry proposal.md"
    assert snapshot_tree(vault) == before
    assert _run_git(vault, "rev-parse", "HEAD").strip() == before_head
    assert _run_git(vault, "branch", "--format=%(refname:short)").splitlines() == ["main"]


def test_git_adapter_local_remote_exact_path_and_non_force_push(tmp_path: Path) -> None:
    repository, remote = _git_repository_with_local_origin(tmp_path)
    adapter = GitVersionControlAdapter(repository)
    branch = "automation/note-integration"

    adapter.preflight(branch, require_synced_main=True, check_remote_branch=False)
    adapter.create_branch(branch)
    with pytest.raises(ProposalPortError, match="already exists"):
        adapter.create_branch(branch)

    (repository / "proposal.md").write_text("proposal\n", encoding="utf-8")
    assert adapter.changed_paths() == ("proposal.md",)
    adapter.stage_exact_path("proposal.md")
    assert adapter.staged_paths() == ("proposal.md",)
    commit_sha = adapter.commit_exact_path("proposal.md", "note: add project Proposal")
    assert len(commit_sha) == 40
    assert _run_git(repository, "show", "--format=", "--name-only", "HEAD").splitlines() == [
        "proposal.md"
    ]
    adapter.push(branch)
    remote_ref = _run_git(repository, "ls-remote", str(remote), f"refs/heads/{branch}")
    assert commit_sha in remote_ref


def test_git_adapter_rejects_dirty_worktree_and_unsynced_main(tmp_path: Path) -> None:
    repository, _ = _git_repository_with_local_origin(tmp_path)
    adapter = GitVersionControlAdapter(repository)

    (repository / "dirty.txt").write_text("dirty\n", encoding="utf-8")
    with pytest.raises(ProposalPortError) as dirty_error:
        adapter.preflight(
            "automation/note-dirty", require_synced_main=True, check_remote_branch=False
        )
    assert dirty_error.value.code == "PROPOSAL_DIRTY_WORKTREE"
    (repository / "dirty.txt").unlink()

    _run_git(repository, "add", "--", "seed.txt")
    _run_git(repository, "commit", "--allow-empty", "-m", "local only")
    with pytest.raises(ProposalPortError) as sync_error:
        adapter.preflight(
            "automation/note-unsynced", require_synced_main=True, check_remote_branch=False
        )
    assert sync_error.value.code == "PROPOSAL_MAIN_NOT_SYNCED"
