"""Application workflow для публикации новой managed note как Git proposal."""

from __future__ import annotations

import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Any, TypedDict

from second_brain.application.ports import (
    ManagedNoteCreationPort,
    ProposalPortError,
    PullRequestPort,
    VersionControlPort,
)
from second_brain.application.reports import Diagnostic, DiagnosticSeverity
from second_brain.application.writes import (
    CreateManagedNoteRequest,
    CreateManagedNoteResult,
    CreateNotePlan,
    CreateStatus,
)
from second_brain.domain.models import NoteType

_MAIN_BRANCH = "main"
_AUTOMATION_PREFIX = "automation/"


class _ProposalFields(TypedDict):
    branch: str
    commit_message: str
    pr_title: str
    pr_base: str
    pr_head: str
    apply_requested: bool


class ProposalStatus(StrEnum):
    """Состояние proposal workflow."""

    DRY_RUN = "dry-run"
    CREATED_PR = "created-pr"
    REJECTED = "rejected"
    PARTIAL = "partial"


@dataclass(frozen=True, slots=True)
class CreateNoteProposalRequest:
    """Входные данные proposal создания одной managed note."""

    note_type: NoteType
    title: str
    apply: bool = False
    now: datetime | None = None


@dataclass(frozen=True, slots=True)
class CreateNoteProposalResult:
    """Итог proposal workflow для text и JSON CLI."""

    status: ProposalStatus
    branch: str
    commit_message: str
    pr_title: str
    pr_base: str
    pr_head: str
    note: CreateNotePlan | None = None
    commit_sha: str | None = None
    pr_url: str | None = None
    diagnostics: tuple[Diagnostic, ...] = ()
    rollback_succeeded: bool | None = None
    remote_branch_pushed: bool = False
    apply_requested: bool = False

    @property
    def successful(self) -> bool:
        """Показать, завершился ли workflow полностью приемлемым результатом."""

        return self.status in {ProposalStatus.DRY_RUN, ProposalStatus.CREATED_PR}

    def as_dict(self) -> dict[str, Any]:
        """Вернуть минимальное стабильное JSON-представление proposal."""

        return {
            "status": self.status.value,
            "mode": "apply" if self.apply_requested else "dry-run",
            "branch": self.branch,
            "commit_message": self.commit_message,
            "pr_title": self.pr_title,
            "pr_base": self.pr_base,
            "pr_head": self.pr_head,
            "commit_sha": self.commit_sha,
            "pr_url": self.pr_url,
            "remote_branch_pushed": self.remote_branch_pushed,
            "note": _plan_as_dict(self.note),
            "rollback": (
                "succeeded"
                if self.rollback_succeeded is True
                else "failed"
                if self.rollback_succeeded is False
                else "not-needed"
            ),
            "diagnostics": [item.as_dict() for item in self.diagnostics],
        }


def new_automation_branch() -> str:
    """Сгенерировать безопасное collision-resistant имя automation branch."""

    return f"{_AUTOMATION_PREFIX}note-{uuid.uuid7().hex}"


@dataclass(frozen=True, slots=True)
class CreateNoteProposal:
    """Оркестрировать Safe Write, Git branch/commit/push и создание PR."""

    note_creator: ManagedNoteCreationPort
    version_control: VersionControlPort
    pull_request: PullRequestPort
    branch_factory: Callable[[], str] = new_automation_branch

    def execute(self, request: CreateNoteProposalRequest) -> CreateNoteProposalResult:
        """Выполнить изолированный dry-run либо apply proposal workflow."""

        branch = self.branch_factory()
        commit_message = _commit_message(request.note_type, request.title)
        common: _ProposalFields = {
            "branch": branch,
            "commit_message": commit_message,
            "pr_title": commit_message,
            "pr_base": _MAIN_BRANCH,
            "pr_head": branch,
            "apply_requested": request.apply,
        }
        invalid_branch = _validate_branch_name(branch)
        if invalid_branch is not None:
            return CreateNoteProposalResult(
                ProposalStatus.REJECTED,
                diagnostics=(_diagnostic("PROPOSAL_INVALID_BRANCH", invalid_branch),),
                **common,
            )

        if not request.apply:
            try:
                self.version_control.preflight(
                    branch,
                    require_synced_main=True,
                    check_remote_branch=False,
                )
            except ProposalPortError as exc:
                return CreateNoteProposalResult(
                    ProposalStatus.REJECTED,
                    diagnostics=(_diagnostic(exc.code, str(exc)),),
                    **common,
                )
            except (OSError, ValueError) as exc:
                return CreateNoteProposalResult(
                    ProposalStatus.REJECTED,
                    diagnostics=(_diagnostic("PROPOSAL_PREFLIGHT_FAILED", str(exc)),),
                    **common,
                )
            return self._create_note_for_dry_run(request, common)

        try:
            # До fetch проверяем только локальные safety conditions. Fetch допустим
            # исключительно после того, как main и worktree уже признаны безопасными.
            self.version_control.preflight(
                branch,
                require_synced_main=False,
                check_remote_branch=False,
            )
            self.version_control.fetch_main()
            self.version_control.preflight(
                branch,
                require_synced_main=True,
                check_remote_branch=True,
            )
            self.pull_request.check_auth()
        except ProposalPortError as exc:
            return CreateNoteProposalResult(
                ProposalStatus.REJECTED,
                diagnostics=(_diagnostic(exc.code, str(exc)),),
                **common,
            )
        except (OSError, ValueError) as exc:
            return CreateNoteProposalResult(
                ProposalStatus.REJECTED,
                diagnostics=(_diagnostic("PROPOSAL_PREFLIGHT_FAILED", str(exc)),),
                **common,
            )

        try:
            self.version_control.create_branch(branch)
        except ProposalPortError as exc:
            return CreateNoteProposalResult(
                ProposalStatus.REJECTED,
                diagnostics=(_diagnostic(exc.code, str(exc)),),
                **common,
            )
        except (OSError, ValueError) as exc:
            return CreateNoteProposalResult(
                ProposalStatus.REJECTED,
                diagnostics=(_diagnostic("PROPOSAL_BRANCH_CREATE_FAILED", str(exc)),),
                **common,
            )

        try:
            note_result = self.note_creator.execute(
                CreateManagedNoteRequest(
                    request.note_type,
                    request.title,
                    apply=True,
                    now=request.now,
                )
            )
        except (OSError, ValueError) as exc:
            return CreateNoteProposalResult(
                ProposalStatus.REJECTED,
                diagnostics=(_diagnostic("PROPOSAL_NOTE_CREATE_FAILED", str(exc)),),
                **common,
            )

        if note_result.status is not CreateStatus.CREATED:
            diagnostics = list(note_result.diagnostics)
            diagnostics.append(
                _diagnostic(
                    "PROPOSAL_NOTE_CREATE_FAILED",
                    "Safe Write не создал подтверждённую note; commit/push/PR не выполнялись",
                )
            )
            return CreateNoteProposalResult(
                ProposalStatus.REJECTED,
                note=note_result.plan,
                diagnostics=tuple(diagnostics),
                rollback_succeeded=note_result.rollback_succeeded,
                **common,
            )

        if note_result.plan is None:
            return CreateNoteProposalResult(
                ProposalStatus.REJECTED,
                diagnostics=(
                    _diagnostic(
                        "PROPOSAL_NOTE_PLAN_MISSING",
                        "Safe Write вернул CREATED без note plan; commit/push/PR не выполнялись",
                    ),
                ),
                **common,
            )
        if note_result.receipt is None:
            return CreateNoteProposalResult(
                ProposalStatus.REJECTED,
                note=note_result.plan,
                diagnostics=(
                    _diagnostic(
                        "PROPOSAL_NOTE_RECEIPT_MISSING",
                        "Safe Write не вернул receipt; note и automation branch "
                        "сохранены для recovery",
                        note_result.plan.relative_path,
                    ),
                ),
                **common,
            )

        return self._publish_created_note(
            branch=branch,
            commit_message=commit_message,
            note_result=note_result,
            common=common,
        )

    def _create_note_for_dry_run(
        self,
        request: CreateNoteProposalRequest,
        common: _ProposalFields,
    ) -> CreateNoteProposalResult:
        try:
            note_result = self.note_creator.execute(
                CreateManagedNoteRequest(
                    request.note_type,
                    request.title,
                    apply=False,
                    now=request.now,
                )
            )
        except (OSError, ValueError) as exc:
            return CreateNoteProposalResult(
                ProposalStatus.REJECTED,
                diagnostics=(_diagnostic("PROPOSAL_NOTE_PLAN_FAILED", str(exc)),),
                **common,
            )
        if note_result.status is not CreateStatus.DRY_RUN:
            diagnostics = list(note_result.diagnostics)
            diagnostics.append(
                _diagnostic(
                    "PROPOSAL_NOTE_PLAN_FAILED",
                    "Safe Write не вернул dry-run plan; Git mutation не выполнялась",
                )
            )
            return CreateNoteProposalResult(
                ProposalStatus.REJECTED,
                note=note_result.plan,
                diagnostics=tuple(diagnostics),
                **common,
            )
        return CreateNoteProposalResult(
            ProposalStatus.DRY_RUN,
            note=note_result.plan,
            diagnostics=note_result.diagnostics,
            **common,
        )

    def _publish_created_note(
        self,
        *,
        branch: str,
        commit_message: str,
        note_result: CreateManagedNoteResult,
        common: _ProposalFields,
    ) -> CreateNoteProposalResult:
        assert note_result.plan is not None
        assert note_result.receipt is not None
        note = note_result.plan
        expected_path = note.relative_path

        try:
            changed_paths = self.version_control.changed_paths()
        except ProposalPortError as exc:
            return self._recover_before_commit(
                branch=branch,
                note_result=note_result,
                diagnostics=[_diagnostic(exc.code, str(exc))],
                common=common,
                staging_attempted=False,
            )
        except (OSError, ValueError) as exc:
            return self._recover_before_commit(
                branch=branch,
                note_result=note_result,
                diagnostics=[_diagnostic("PROPOSAL_STATUS_FAILED", str(exc))],
                common=common,
                staging_attempted=False,
            )
        if changed_paths != (expected_path,):
            return self._recover_before_commit(
                branch=branch,
                note_result=note_result,
                diagnostics=[
                    _diagnostic(
                        "PROPOSAL_UNEXPECTED_CHANGES",
                        "после создания note разрешён только один changed path: "
                        f"{expected_path}; получено: {_paths_text(changed_paths)}",
                    )
                ],
                common=common,
                staging_attempted=False,
            )

        try:
            self.version_control.stage_exact_path(expected_path)
            staged_paths = self.version_control.staged_paths()
        except ProposalPortError as exc:
            return self._recover_before_commit(
                branch=branch,
                note_result=note_result,
                diagnostics=[_diagnostic(exc.code, str(exc), expected_path)],
                common=common,
                staging_attempted=True,
            )
        except (OSError, ValueError) as exc:
            return self._recover_before_commit(
                branch=branch,
                note_result=note_result,
                diagnostics=[_diagnostic("PROPOSAL_STAGE_FAILED", str(exc), expected_path)],
                common=common,
                staging_attempted=True,
            )
        if staged_paths != (expected_path,):
            return self._recover_before_commit(
                branch=branch,
                note_result=note_result,
                diagnostics=[
                    _diagnostic(
                        "PROPOSAL_UNEXPECTED_STAGED_PATHS",
                        "в index разрешён только exact created path; получено: "
                        f"{_paths_text(staged_paths)}",
                        expected_path,
                    )
                ],
                common=common,
                staging_attempted=True,
            )

        pre_commit_head: str | None = None
        try:
            pre_commit_head = self.version_control.head_sha()
            commit_sha = self.version_control.commit_exact_path(expected_path, commit_message)
        except ProposalPortError as exc:
            return self._handle_commit_failure(
                branch=branch,
                note_result=note_result,
                common=common,
                pre_commit_head=pre_commit_head,
                diagnostics=[_diagnostic(exc.code, str(exc), expected_path)],
            )
        except (OSError, ValueError) as exc:
            return self._handle_commit_failure(
                branch=branch,
                note_result=note_result,
                common=common,
                pre_commit_head=pre_commit_head,
                diagnostics=[_diagnostic("PROPOSAL_COMMIT_FAILED", str(exc), expected_path)],
            )

        try:
            self.version_control.push(branch)
        except ProposalPortError as exc:
            return CreateNoteProposalResult(
                ProposalStatus.REJECTED,
                note=note,
                commit_sha=commit_sha,
                diagnostics=(_diagnostic(exc.code, str(exc), branch),),
                **common,
            )
        except (OSError, ValueError) as exc:
            return CreateNoteProposalResult(
                ProposalStatus.REJECTED,
                note=note,
                commit_sha=commit_sha,
                diagnostics=(_diagnostic("PROPOSAL_PUSH_FAILED", str(exc), branch),),
                **common,
            )

        body = _pull_request_body(note)
        try:
            pr_url = self.pull_request.create(
                title=common["pr_title"],
                base=common["pr_base"],
                head=common["pr_head"],
                body=body,
            )
        except ProposalPortError as exc:
            return CreateNoteProposalResult(
                ProposalStatus.PARTIAL,
                note=note,
                commit_sha=commit_sha,
                diagnostics=(_diagnostic(exc.code, str(exc)),),
                remote_branch_pushed=True,
                **common,
            )
        except (OSError, ValueError) as exc:
            return CreateNoteProposalResult(
                ProposalStatus.PARTIAL,
                note=note,
                commit_sha=commit_sha,
                diagnostics=(_diagnostic("PROPOSAL_PR_CREATE_FAILED", str(exc)),),
                remote_branch_pushed=True,
                **common,
            )

        return CreateNoteProposalResult(
            ProposalStatus.CREATED_PR,
            note=note,
            commit_sha=commit_sha,
            pr_url=pr_url,
            remote_branch_pushed=True,
            **common,
        )

    def _handle_commit_failure(
        self,
        *,
        branch: str,
        note_result: CreateManagedNoteResult,
        common: _ProposalFields,
        pre_commit_head: str | None,
        diagnostics: list[Diagnostic],
    ) -> CreateNoteProposalResult:
        """Откатывать note только если проверка доказывает, что commit не появился."""

        if not isinstance(pre_commit_head, str):
            diagnostics.append(
                _diagnostic(
                    "PROPOSAL_COMMIT_STATE_UNKNOWN",
                    "не удалось подтвердить состояние HEAD после ошибки commit; branch сохранена",
                )
            )
            return CreateNoteProposalResult(
                ProposalStatus.REJECTED,
                note=note_result.plan,
                diagnostics=tuple(diagnostics),
                **common,
            )
        try:
            current_head = self.version_control.head_sha()
        except (ProposalPortError, OSError, ValueError) as exc:
            diagnostics.append(
                _diagnostic(
                    "PROPOSAL_COMMIT_STATE_UNKNOWN",
                    "не удалось подтвердить состояние HEAD после ошибки commit: "
                    f"{exc}; branch сохранена",
                )
            )
            return CreateNoteProposalResult(
                ProposalStatus.REJECTED,
                note=note_result.plan,
                diagnostics=tuple(diagnostics),
                **common,
            )
        if current_head != pre_commit_head:
            diagnostics.append(
                _diagnostic(
                    "PROPOSAL_COMMIT_CREATED_BEFORE_FAILURE",
                    f"commit уже существует ({current_head}); branch сохранена для recovery",
                )
            )
            return CreateNoteProposalResult(
                ProposalStatus.REJECTED,
                note=note_result.plan,
                commit_sha=current_head,
                diagnostics=tuple(diagnostics),
                **common,
            )
        return self._recover_before_commit(
            branch=branch,
            note_result=note_result,
            diagnostics=diagnostics,
            common=common,
            staging_attempted=True,
        )

    def _recover_before_commit(
        self,
        *,
        branch: str,
        note_result: CreateManagedNoteResult,
        diagnostics: list[Diagnostic],
        common: _ProposalFields,
        staging_attempted: bool,
    ) -> CreateNoteProposalResult:
        """Попытаться удалить только наш note и пустую branch до commit."""

        plan = note_result.plan
        receipt = note_result.receipt
        if plan is None or receipt is None:
            diagnostics.append(
                _diagnostic(
                    "PROPOSAL_RECOVERY_UNAVAILABLE",
                    "без Safe Write receipt автоматический rollback не выполнялся; "
                    "branch сохранена",
                )
            )
            return CreateNoteProposalResult(
                ProposalStatus.REJECTED,
                note=plan,
                diagnostics=tuple(diagnostics),
                **common,
            )

        if staging_attempted:
            try:
                self.version_control.unstage_exact_path(plan.relative_path)
            except (ProposalPortError, OSError, ValueError) as exc:
                diagnostics.append(
                    _diagnostic(
                        "PROPOSAL_UNSTAGE_FAILED",
                        f"не удалось убрать exact path из index; branch сохранена: {exc}",
                        plan.relative_path,
                    )
                )

        try:
            rollback_succeeded = self.note_creator.rollback(receipt)
        except (OSError, ValueError) as exc:
            rollback_succeeded = False
            diagnostics.append(
                _diagnostic("PROPOSAL_NOTE_ROLLBACK_FAILED", str(exc), plan.relative_path)
            )
        if not rollback_succeeded:
            diagnostics.append(
                _diagnostic(
                    "PROPOSAL_NOTE_ROLLBACK_FAILED",
                    "Safe Write receipt не подтвердил безопасное удаление созданной note; "
                    "branch сохранена",
                    plan.relative_path,
                )
            )
            return CreateNoteProposalResult(
                ProposalStatus.REJECTED,
                note=plan,
                diagnostics=tuple(diagnostics),
                rollback_succeeded=False,
                **common,
            )

        try:
            remaining_paths = self.version_control.changed_paths()
        except (ProposalPortError, OSError, ValueError) as exc:
            diagnostics.append(
                _diagnostic(
                    "PROPOSAL_CLEANUP_STATUS_FAILED",
                    f"после rollback не удалось проверить worktree; branch сохранена: {exc}",
                )
            )
            return CreateNoteProposalResult(
                ProposalStatus.REJECTED,
                note=plan,
                diagnostics=tuple(diagnostics),
                rollback_succeeded=True,
                **common,
            )
        if remaining_paths:
            diagnostics.append(
                _diagnostic(
                    "PROPOSAL_CLEANUP_BLOCKED",
                    "после rollback остались изменения; branch сохранена: "
                    f"{_paths_text(remaining_paths)}",
                )
            )
            return CreateNoteProposalResult(
                ProposalStatus.REJECTED,
                note=plan,
                diagnostics=tuple(diagnostics),
                rollback_succeeded=True,
                **common,
            )

        try:
            self.version_control.switch_to_main()
            self.version_control.delete_local_branch(branch)
        except ProposalPortError as exc:
            diagnostics.append(_diagnostic("PROPOSAL_BRANCH_CLEANUP_FAILED", str(exc), branch))
        except (OSError, ValueError) as exc:
            diagnostics.append(_diagnostic("PROPOSAL_BRANCH_CLEANUP_FAILED", str(exc), branch))
        return CreateNoteProposalResult(
            ProposalStatus.REJECTED,
            note=plan,
            diagnostics=tuple(diagnostics),
            rollback_succeeded=True,
            **common,
        )


def _commit_message(note_type: NoteType, title: str) -> str:
    """Построить детерминированный commit/PR title без shell interpolation."""

    return f"note: add {note_type.value} {title}"


def _pull_request_body(plan: CreateNotePlan) -> str:
    """Построить PR body только из безопасных относительных данных plan."""

    return (
        "Автоматическое предложение для новой managed note.\n\n"
        f"- Тип: `{plan.note_type.value}`\n"
        f"- Путь: `{plan.relative_path}`\n"
    )


def _plan_as_dict(plan: CreateNotePlan | None) -> dict[str, Any] | None:
    if plan is None:
        return None
    return {
        "type": plan.note_type.value,
        "title": plan.title,
        "id": str(plan.note_id),
        "created": plan.created.isoformat(timespec="seconds"),
        "relative_path": plan.relative_path,
        "content": plan.content,
    }


def _validate_branch_name(branch: str) -> str | None:
    if not isinstance(branch, str) or not branch.startswith(_AUTOMATION_PREFIX):
        return "automation branch должна начинаться с automation/"
    if (
        not branch.removeprefix(_AUTOMATION_PREFIX)
        or branch.endswith("/")
        or ".." in branch
        or any(char.isspace() or ord(char) < 32 for char in branch)
    ):
        return "automation branch содержит небезопасные Git ref characters"
    return None


def _paths_text(paths: tuple[str, ...]) -> str:
    return ", ".join(paths) if paths else "(нет)"


def _diagnostic(code: str, message: str, path: str | None = None) -> Diagnostic:
    return Diagnostic(code, message, DiagnosticSeverity.ERROR, path)
