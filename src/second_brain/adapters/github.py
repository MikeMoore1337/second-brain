"""Изолированный `gh` adapter для создания одного GitHub PR."""

from __future__ import annotations

import re
from pathlib import Path
from urllib.parse import urlparse

from second_brain.adapters.commands import CommandResult, CommandRunner, run_command
from second_brain.application.ports import ProposalPortError

_GITHUB_PR_URL = re.compile(r"https://github\.com/[^\s/]+/[^\s/]+/pull/[1-9][0-9]*\Z")


class GitHubPullRequestAdapter:
    """Проверить auth и создать PR через explicit `gh` argv."""

    def __init__(self, root: Path, runner: CommandRunner = run_command) -> None:
        try:
            resolved_root = root.resolve(strict=True)
        except OSError as exc:
            raise ProposalPortError(
                "PROPOSAL_GH_REPOSITORY_REQUIRED",
                "vault path cannot be used as a GitHub working directory",
            ) from exc
        if not resolved_root.is_dir():
            raise ProposalPortError(
                "PROPOSAL_GH_REPOSITORY_REQUIRED",
                "vault path is not a directory",
            )
        self.root = resolved_root
        self._runner = runner

    def check_auth(self) -> None:
        """Проверить существующую auth-сессию `gh` без чтения credentials."""

        self._require_success(
            ("auth", "status"),
            "PROPOSAL_GH_AUTH_FAILED",
            "проверка авторизации gh завершилась ошибкой",
        )

    def create(self, *, title: str, base: str, head: str, body: str) -> str:
        """Создать PR base=main/head=automation branch и вернуть URL."""

        if base != "main" or not head.startswith("automation/"):
            raise ProposalPortError(
                "PROPOSAL_INVALID_PR_REF",
                "PR должен иметь base main и head automation/*",
            )
        if not title or "\x00" in title or "\r" in title or "\n" in title:
            raise ProposalPortError(
                "PROPOSAL_INVALID_PR_TITLE",
                "PR title должен быть непустым однострочным значением",
            )
        result = self._require_success(
            (
                "pr",
                "create",
                "--base",
                base,
                "--head",
                head,
                "--title",
                title,
                "--body",
                body,
            ),
            "PROPOSAL_PR_CREATE_FAILED",
            "создание PR завершилось ошибкой",
        )
        return parse_pull_request_url(result.stdout)

    def _require_success(
        self,
        args: tuple[str, ...],
        code: str,
        action: str,
    ) -> CommandResult:
        argv = ("gh", *args)
        try:
            result = self._runner(argv, self.root)
        except FileNotFoundError as exc:
            raise ProposalPortError("PROPOSAL_GH_UNAVAILABLE", "команда gh недоступна") from exc
        except OSError as exc:
            raise ProposalPortError("PROPOSAL_GH_UNAVAILABLE", "не удалось запустить gh") from exc
        if result.returncode != 0:
            detail = result.stderr.strip().replace(str(self.root), "<vault>")
            suffix = f": {detail}" if detail else f" (exit code {result.returncode})"
            raise ProposalPortError(code, f"{action}{suffix}")
        return result


def parse_pull_request_url(output: str) -> str:
    """Извлечь единственный GitHub PR URL из stdout `gh pr create`."""

    for line in output.splitlines():
        candidate = line.strip()
        parsed = urlparse(candidate)
        if (
            parsed.scheme == "https"
            and parsed.netloc == "github.com"
            and _GITHUB_PR_URL.fullmatch(candidate)
        ):
            return candidate
    raise ProposalPortError(
        "PROPOSAL_PR_URL_PARSE_FAILED",
        "gh pr create не вернул корректный GitHub PR URL",
    )


GitHubAdapter = GitHubPullRequestAdapter
