"""Изолированный Git adapter для proposal workflow."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path, PurePosixPath

from second_brain.adapters.commands import CommandResult, CommandRunner, run_command
from second_brain.application.ports import ProposalPortError

_AUTOMATION_PREFIX = "automation/"
_OPERATION_MARKERS = (
    "MERGE_HEAD",
    "CHERRY_PICK_HEAD",
    "REVERT_HEAD",
    "REBASE_HEAD",
    "rebase-merge",
    "rebase-apply",
    "BISECT_LOG",
)


class GitVersionControlAdapter:
    """Выполнять только необходимые Git argv в каталоге vault."""

    def __init__(self, root: Path, runner: CommandRunner = run_command) -> None:
        try:
            resolved_root = root.resolve(strict=True)
        except OSError as exc:
            raise ProposalPortError(
                "PROPOSAL_GIT_REPOSITORY_REQUIRED",
                "vault path cannot be used as a Git worktree",
            ) from exc
        if not resolved_root.is_dir():
            raise ProposalPortError(
                "PROPOSAL_GIT_REPOSITORY_REQUIRED",
                "vault path is not a directory",
            )
        self.root = resolved_root
        self._runner = runner

    def preflight(
        self,
        branch_name: str,
        *,
        require_synced_main: bool,
        check_remote_branch: bool,
    ) -> None:
        """Проверить Git state без fetch и любых mutating команд."""

        self._check_automation_branch(branch_name)
        self._require_success(
            ("check-ref-format", "--branch", branch_name),
            "PROPOSAL_INVALID_BRANCH",
            "проверка automation branch завершилась ошибкой",
        )

        inside_worktree = self._execute(("rev-parse", "--is-inside-work-tree"))
        if inside_worktree.returncode != 0 or inside_worktree.stdout.strip().lower() != "true":
            raise ProposalPortError(
                "PROPOSAL_GIT_REPOSITORY_REQUIRED",
                "vault не является Git worktree",
            )
        bare = self._require_success(
            ("rev-parse", "--is-bare-repository"),
            "PROPOSAL_GIT_STATE_FAILED",
            "не удалось определить bare/worktree state",
        )
        if bare.stdout.strip().lower() == "true":
            raise ProposalPortError(
                "PROPOSAL_BARE_REPOSITORY",
                "bare repository не может использоваться как vault worktree",
            )

        self._require_success(
            ("remote", "get-url", "origin"),
            "PROPOSAL_ORIGIN_MISSING",
            "remote origin не настроен",
        )
        branch = self._require_success(
            ("symbolic-ref", "--quiet", "--short", "HEAD"),
            "PROPOSAL_MAIN_REQUIRED",
            "текущий HEAD не является branch",
        ).stdout.strip()
        if branch != "main":
            raise ProposalPortError(
                "PROPOSAL_MAIN_REQUIRED",
                "proposal apply разрешён только из branch main",
            )

        status = self._require_success(
            ("status", "--porcelain=v1", "--untracked-files=all", "-z"),
            "PROPOSAL_STATUS_FAILED",
            "не удалось проверить чистоту worktree",
        )
        if status.stdout:
            raise ProposalPortError(
                "PROPOSAL_DIRTY_WORKTREE",
                "worktree или index не чисты; автоматическая очистка запрещена",
            )
        self._check_operation_state()

        local_main = self._ref_sha(
            "refs/heads/main",
            "PROPOSAL_LOCAL_MAIN_MISSING",
            "local main недоступен",
        )
        origin_main = self._ref_sha(
            "refs/remotes/origin/main",
            "PROPOSAL_ORIGIN_MAIN_MISSING",
            "origin/main недоступен; для apply требуется явный fetch",
        )
        if require_synced_main and local_main != origin_main:
            raise ProposalPortError(
                "PROPOSAL_MAIN_NOT_SYNCED",
                "local main и origin/main не указывают на один commit",
            )

        local_ref = f"refs/heads/{branch_name}"
        if self._ref_exists(local_ref):
            raise ProposalPortError(
                "PROPOSAL_BRANCH_EXISTS",
                "локальная automation branch уже существует",
            )
        if check_remote_branch:
            if self._remote_branch_exists(branch_name):
                raise ProposalPortError(
                    "PROPOSAL_BRANCH_EXISTS",
                    "remote automation branch уже существует",
                )
        elif self._ref_exists(f"refs/remotes/origin/{branch_name}"):
            raise ProposalPortError(
                "PROPOSAL_BRANCH_EXISTS",
                "известная remote-tracking automation branch уже существует",
            )

    def fetch_main(self) -> None:
        """Обновить только origin/main перед повторным apply preflight."""

        self._require_success(
            ("fetch", "origin", "main"),
            "PROPOSAL_FETCH_FAILED",
            "git fetch origin main завершился ошибкой",
        )

    def create_branch(self, branch_name: str) -> None:
        """Создать branch без force/reset/перезаписи существующей branch."""

        self._check_automation_branch(branch_name)
        self._require_success(
            ("switch", "--create", branch_name),
            "PROPOSAL_BRANCH_CREATE_FAILED",
            "создание automation branch завершилось ошибкой",
        )

    def changed_paths(self) -> tuple[str, ...]:
        """Прочитать полный changed pathset из worktree и index."""

        result = self._require_success(
            ("status", "--porcelain=v1", "--untracked-files=all", "-z"),
            "PROPOSAL_STATUS_FAILED",
            "не удалось получить changed paths",
        )
        return _parse_status_paths(result.stdout)

    def stage_exact_path(self, relative_path: str) -> None:
        """Stage только один переданный relative path."""

        _validate_relative_path(relative_path)
        self._require_success(
            ("add", "--", relative_path),
            "PROPOSAL_STAGE_FAILED",
            "stage exact path завершился ошибкой",
        )

    def staged_paths(self) -> tuple[str, ...]:
        """Прочитать имена paths из staged diff."""

        result = self._require_success(
            ("diff", "--cached", "--name-only", "-z", "--"),
            "PROPOSAL_STAGED_STATUS_FAILED",
            "не удалось проверить staged pathset",
        )
        return _parse_name_only_paths(result.stdout)

    def unstage_exact_path(self, relative_path: str) -> None:
        """Убрать из index только path текущей note при pre-commit cleanup."""

        _validate_relative_path(relative_path)
        self._require_success(
            ("restore", "--staged", "--", relative_path),
            "PROPOSAL_UNSTAGE_FAILED",
            "очистка exact staged path завершилась ошибкой",
        )

    def head_sha(self) -> str:
        """Вернуть SHA текущего commit."""

        return self._require_success(
            ("rev-parse", "--verify", "HEAD^{commit}"),
            "PROPOSAL_HEAD_SHA_FAILED",
            "не удалось получить HEAD SHA",
        ).stdout.strip()

    def commit_exact_path(self, relative_path: str, message: str) -> str:
        """Создать commit с `--only` для одного exact path."""

        _validate_relative_path(relative_path)
        if not message or "\x00" in message or "\r" in message or "\n" in message:
            raise ProposalPortError(
                "PROPOSAL_INVALID_COMMIT_MESSAGE",
                "commit message должен быть непустым однострочным значением",
            )
        self._require_success(
            ("commit", "--only", "--message", message, "--", relative_path),
            "PROPOSAL_COMMIT_FAILED",
            "commit exact path завершился ошибкой",
        )
        return self.head_sha()

    def push(self, branch_name: str) -> None:
        """Опубликовать branch обычным non-force push."""

        self._check_automation_branch(branch_name)
        self._require_success(
            ("push", "--set-upstream", "origin", branch_name),
            "PROPOSAL_PUSH_FAILED",
            "push automation branch завершился ошибкой",
        )

    def switch_to_main(self) -> None:
        """Переключиться на main только после подтверждённого cleanup."""

        self._require_success(
            ("switch", "main"),
            "PROPOSAL_BRANCH_CLEANUP_FAILED",
            "возврат на main завершился ошибкой",
        )

    def delete_local_branch(self, branch_name: str) -> None:
        """Удалить только branch без commit после безопасного rollback."""

        self._check_automation_branch(branch_name)
        self._require_success(
            ("branch", "--delete", branch_name),
            "PROPOSAL_BRANCH_CLEANUP_FAILED",
            "удаление пустой automation branch завершилось ошибкой",
        )

    def _check_operation_state(self) -> None:
        for marker in _OPERATION_MARKERS:
            result = self._require_success(
                ("rev-parse", "--git-path", marker),
                "PROPOSAL_OPERATION_STATE_FAILED",
                "не удалось проверить состояние Git operation",
            )
            marker_path = Path(result.stdout.strip())
            if not marker_path.is_absolute():
                marker_path = self.root / marker_path
            try:
                present = marker_path.exists()
            except OSError as exc:
                raise ProposalPortError(
                    "PROPOSAL_OPERATION_STATE_FAILED",
                    "не удалось проверить marker Git operation",
                ) from exc
            if present:
                raise ProposalPortError(
                    "PROPOSAL_OPERATION_IN_PROGRESS",
                    f"Git operation уже выполняется: {marker}",
                )

    def _ref_sha(self, ref: str, code: str, message: str) -> str:
        result = self._execute(("rev-parse", "--verify", f"{ref}^{{commit}}"))
        if result.returncode != 0:
            raise ProposalPortError(code, message)
        sha = result.stdout.strip()
        if not sha:
            raise ProposalPortError(code, message)
        return sha

    def _ref_exists(self, ref: str) -> bool:
        result = self._execute(("show-ref", "--verify", "--quiet", ref))
        if result.returncode == 0:
            return True
        if result.returncode == 1:
            return False
        raise ProposalPortError(
            "PROPOSAL_BRANCH_CHECK_FAILED",
            "не удалось проверить существование automation branch",
        )

    def _remote_branch_exists(self, branch_name: str) -> bool:
        result = self._require_success(
            ("ls-remote", "--heads", "origin", f"refs/heads/{branch_name}"),
            "PROPOSAL_REMOTE_BRANCH_CHECK_FAILED",
            "не удалось проверить remote automation branch",
        )
        wanted = f"refs/heads/{branch_name}"
        return any(line.rstrip().endswith(f"\t{wanted}") for line in result.stdout.splitlines())

    def _execute(self, args: Sequence[str]) -> CommandResult:
        argv = ("git", *args)
        try:
            return self._runner(argv, self.root)
        except FileNotFoundError as exc:
            raise ProposalPortError("PROPOSAL_GIT_UNAVAILABLE", "команда git недоступна") from exc
        except OSError as exc:
            raise ProposalPortError("PROPOSAL_GIT_UNAVAILABLE", "не удалось запустить git") from exc

    def _require_success(self, args: Sequence[str], code: str, action: str) -> CommandResult:
        result = self._execute(args)
        if result.returncode != 0:
            detail = result.stderr.strip().replace(str(self.root), "<vault>")
            suffix = f": {detail}" if detail else f" (exit code {result.returncode})"
            raise ProposalPortError(code, f"{action}{suffix}")
        return result

    @staticmethod
    def _check_automation_branch(branch_name: str) -> None:
        if (
            not isinstance(branch_name, str)
            or not branch_name.startswith(_AUTOMATION_PREFIX)
            or not branch_name.removeprefix(_AUTOMATION_PREFIX)
            or ".." in branch_name
            or any(char.isspace() or ord(char) < 32 for char in branch_name)
        ):
            raise ProposalPortError(
                "PROPOSAL_INVALID_BRANCH",
                "automation branch должна быть безопасным Git ref с префиксом automation/",
            )


GitAdapter = GitVersionControlAdapter


def _validate_relative_path(relative_path: str) -> None:
    path = PurePosixPath(relative_path)
    if (
        not relative_path
        or path.is_absolute()
        or "\\" in relative_path
        or any(part in {"", ".", ".."} for part in path.parts)
        or path.as_posix() != relative_path
    ):
        raise ProposalPortError(
            "PROPOSAL_INVALID_PATH",
            "Git path должен быть non-empty vault-relative POSIX path",
        )


def _parse_status_paths(payload: str) -> tuple[str, ...]:
    records = [record for record in payload.split("\x00") if record]
    paths: list[str] = []
    index = 0
    while index < len(records):
        record = records[index]
        if len(record) < 4 or record[2] != " ":
            raise ProposalPortError(
                "PROPOSAL_STATUS_PARSE_FAILED",
                "Git status вернул неожиданный porcelain формат",
            )
        status = record[:2]
        paths.append(record[3:])
        index += 1
        if status[0] in {"R", "C"} or status[1] in {"R", "C"}:
            if index >= len(records):
                raise ProposalPortError(
                    "PROPOSAL_STATUS_PARSE_FAILED",
                    "Git status не содержит вторую часть rename path",
                )
            paths.append(records[index])
            index += 1
    return tuple(paths)


def _parse_name_only_paths(payload: str) -> tuple[str, ...]:
    return tuple(record for record in payload.split("\x00") if record)
