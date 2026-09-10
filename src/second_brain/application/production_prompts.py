"""Shared production-operation prompt strategies."""

from __future__ import annotations

import re
import shlex
from dataclasses import dataclass
from enum import StrEnum


class ProductionOperation(StrEnum):
    """Supported owner-facing production operation types."""

    APPLICATION_DEPLOY = "application-deploy"
    VAULT_SYNC = "vault-sync"


@dataclass(frozen=True, slots=True)
class ProductionPromptContext:
    """Validated values inserted into one generated owner prompt."""

    target_sha: str
    target_repository: str
    production_path: str
    app_root: str | None = None
    backup_root: str | None = None
    lock_path: str | None = None
    expected_branch: str = "main"
    expected_remote: str | None = None

    def __post_init__(self) -> None:
        if re.fullmatch(r"[0-9a-f]{40}", self.target_sha) is None:
            raise ValueError("target_sha must be an exact lowercase 40-character Git SHA")
        _safe_text(self.target_repository, "target_repository")
        _safe_text(self.production_path, "production_path")
        for value, name in (
            (self.app_root, "app_root"),
            (self.backup_root, "backup_root"),
            (self.lock_path, "lock_path"),
        ):
            if value is not None:
                _safe_text(value, name)
        _safe_text(self.expected_branch, "expected_branch")
        if self.expected_remote is not None:
            _safe_text(self.expected_remote, "expected_remote")


def generate_production_prompt(
    operation: ProductionOperation | str,
    context: ProductionPromptContext,
) -> str:
    """Render one strategy without network, filesystem, or secret access."""

    try:
        selected = ProductionOperation(operation)
    except ValueError as exc:
        raise ValueError("unknown production operation") from exc
    if selected is ProductionOperation.VAULT_SYNC:
        return _render_vault_sync(context)
    return _render_application_deploy(context)


def _render_vault_sync(context: ProductionPromptContext) -> str:
    if context.app_root is None or context.backup_root is None or context.lock_path is None:
        raise ValueError("vault sync prompt requires explicit app, backup and lock paths")
    remote = context.expected_remote or context.target_repository
    command = " \\\n  ".join(
        (
            "uv run --python 3.14 --no-sync python -m second_brain.adapters.vault.sync",
            "--apply",
            f"--vault-root {shlex.quote(context.production_path)}",
            f"--backup-root {shlex.quote(context.backup_root)}",
            f"--lock-path {shlex.quote(context.lock_path)}",
            f"--app-root {shlex.quote(context.app_root)}",
            f"--target-sha {context.target_sha}",
            f"--expected-remote {shlex.quote(remote)}",
            f"--expected-branch {shlex.quote(context.expected_branch)}",
        )
    )
    return f"""# Vault production sync

Exact vault commit: `{context.target_sha}`
Target repository: `{context.target_repository}`
Production vault path: `{context.production_path}`
Operation: `vault sync`

Это sync persistent canonical vault, а не выпуск disposable application artifact.
Команда не выполняет build, restart, force checkout, автоматическое разрешение
конфликтов или bidirectional merge.

Перед запуском:

1. Получите exclusive lock `{context.lock_path}`; lock contention и небезопасный
   stale-lock state означают `STOP / HUMAN_REQUIRED`. Старый lock-файл не удаляйте.
2. Проверьте repository, branch `{context.expected_branch}`, expected remote,
   clean worktree и отсутствие незавершённого Git operation.
3. Команда сама выполнит bounded `git fetch`, проверит, что
   `origin/{context.expected_branch}` всё ещё
   равен exact SHA выше, и классифицирует relation local HEAD ↔ origin/main.
4. Перед единственной допустимой mutation будет создан local recoverable backup
   вне Git worktree в `{context.backup_root}`.

Разрешённый путь — только clean strictly-behind fast-forward-only sync. Equal
является no-op success. Local-ahead, dirty tracked/untracked state, diverged,
branch/remote/repository mismatch, conflict, target SHA drift, lock contention,
backup failure или неизвестное состояние означают `STOP / HUMAN_REQUIRED`.

Запустите из exact installed `second-brain` application checkout:

```bash
cd {shlex.quote(context.app_root)}
{command}
```

После fast-forward команда запускает `vault validate`, повторяет Git/tree
integrity checks и подтверждает доступность vault через production application
command. При validation failure sync не откатывается автоматически: сохраните
backup, остановите дальнейшие mutations и выполните documented manual recovery.

Никогда не теряйте локальные VPS изменения. Если Web / Safe Write создал dirty
или local-ahead state, owner сначала вручную сохраняет и разбирает эти изменения;
этот v1 не коммитит, не push-ит и не объединяет их с GitHub автоматически.
"""


def _render_application_deploy(context: ProductionPromptContext) -> str:
    return f"""# Application production deployment

Exact application commit: `{context.target_sha}`
Target repository: `{context.target_repository}`
Production application path: `{context.production_path}`

Это application release operation по существующему Web production runbook.
Проверьте exact tested SHA, clean control checkout, immutable candidate release,
locked dependency/frontend checks, `doctor`, `vault validate`, atomic `current`
activation, service restart, local/public health и bounded non-destructive rollback.

Не смешивайте этот operation с persistent vault sync: vault остаётся отдельным
sibling repository и не заменяется application release artifact.

Все preflight failures останавливают operation до activation. Сохраняйте current
known-good release для recovery; не используйте force operations и не меняйте
production credentials из prompt.

Канонический runbook: `docs/deployment/web-production.md`.
"""


def _safe_text(value: str, name: str) -> None:
    if (
        type(value) is not str
        or not value
        or len(value) > 500
        or any(ord(character) < 32 for character in value)
    ):
        raise ValueError(f"{name} must be a bounded single-line value")
