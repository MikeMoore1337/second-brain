# Vault Git Sync & Backup v1

Этот runbook описывает отдельную production-операцию для persistent
`second-brain-vault`. Она не является application deployment и не заменяет
[Web production runbook](web-production.md).

## Границы

В production остаются два независимых repository и обычный Git worktree:

```text
<PRODUCTION_ROOT>/
├── second-brain/                         # application/control checkout
├── second-brain-vault/                   # persistent vault worktree, main
├── releases/                             # application release worktrees
├── current -> releases/<APPLICATION_SHA>
└── runtime/
    ├── web.env                            # вне обоих repositories, mode 600
    ├── vault-sync.lock                    # shared operation lock, mode 600
    └── vault-backups/                     # local recoverable snapshots, mode 700
```

`PRODUCTION_ROOT` — bounded absolute path из trusted non-secret repository
variable с тем же именем. Он не является `workflow_dispatch` input и не
принимается от произвольного пользователя; после preflight workflow передаёт
его wrapper-у явно. Значение production-конфигурации может быть, например,
`/srv/second-brain`, но этот machine-specific путь не зашит в code contract.
Wrapper производит только фиксированные дочерние paths из этой layout model и
проверяет их containment.

`second-brain` отвечает за приложение, CLI, Safe Write и этот sync mechanism.
`second-brain-vault` остаётся canonical Markdown/YAML repository и не становится
submodule, subtree или каталогом application repository. Sync не запускает
build, `npm`, service restart, `current` activation, application rollback или
изменение production credentials.

## Owner-triggered GitHub Actions workflow

Для production operation v1 добавлен отдельный workflow
`.github/workflows/vault-sync-production.yml`. Он запускается только через
`workflow_dispatch` из trusted `second-brain` `main`; `push`, `schedule` и
`workflow_run` trigger-ы для него не используются. Workflow не является частью
application autodeploy.

Owner указывает два явных input-а:

- `vault_sha` — exact lowercase SHA из `second-brain-vault/main`, ровно 40
  hexadecimal символов;
- `apply` — отдельное boolean-подтверждение операции, по умолчанию `false`.

Owner также заранее задаёт в repository variables bounded non-secret
`PRODUCTION_ROOT`. Это конфигурация доверенного deployment environment, а не
новый secret и не dispatch input. Workflow не позволяет подменить root во время
запуска.

До любого SSH workflow bounded fail-closed preflight проверяет
`workflow_dispatch`, repository `MikeMoore1337/second-brain`,
`refs/heads/main`, exact SHA, `apply=true`, отдельную repository variable
`PRODUCTION_VAULT_SYNC_ENABLED=true`, bounded `PRODUCTION_ROOT` и bounded
значения `PRODUCTION_SSH_HOST`, `PRODUCTION_SSH_PORT`, `PRODUCTION_SSH_USER`.
Ошибка
любого guard-а — failed workflow, а не silent green skip.

`PRODUCTION_VAULT_SYNC_ENABLED` — отдельный owner opt-in. Его следует включить
в repository variables отдельно после merge, когда owner действительно хочет
разрешить production vault operation и проверил prerequisites. Он не выводится
из `PRODUCTION_DEPLOY_ENABLED` и не требует изменения production `web.env`.

Только после успешного preflight mutation job получает environment `production`
и повторно проверяет trusted exact workflow SHA. Непосредственно перед
SSH setup он заново проверяет `PRODUCTION_VAULT_SYNC_ENABLED=true`; если
owner выключил gate, queued job завершается failed до SSH и до production
mutation. Он использует существующие настройки и trusted repository variable:

- variables: `PRODUCTION_SSH_HOST`, `PRODUCTION_SSH_PORT`,
  `PRODUCTION_SSH_USER`, `PRODUCTION_ROOT`;
- secrets: `PRODUCTION_SSH_PRIVATE_KEY`, `PRODUCTION_SSH_KNOWN_HOSTS`.

Новые production credentials, PAT, cross-repository token, vault-specific
secret или новый SSH key не нужны. Private key и known-hosts создаются только
на runner с pinned filenames, `BatchMode=yes`, `IdentitiesOnly=yes`,
`StrictHostKeyChecking=yes`, explicit `UserKnownHostsFile`, bounded
`ConnectTimeout`/keepalive и удаляются в `always()` cleanup.

Workflow использует тот же GitHub concurrency domain, что application deploy:
`group: second-brain-production`, `cancel-in-progress: false`. Это сериализует
две GitHub production operations, но не заменяет kernel
`<PRODUCTION_ROOT>/runtime/vault-sync.lock`: тот же lock по-прежнему защищает
sync от Safe Write и других VPS writers.

Mutation job передаёт по SSH explicit `--production-root` из trusted
repository variable и explicit `--target-sha` в
`deploy/vault-sync-production.sh`. Wrapper проверяет bounded root, Linux,
non-root, containment всех derived paths и ожидаемую production layout model,
после чего запускает direct `$PRODUCTION_ROOT/current/.venv/bin/python` с
canonical remote, branch `main`, `--apply` и JSON output. Bash не дублирует
VaultSync logic и не вызывает `autodeploy.sh`, `release-control`, systemd,
Caddy, application build/restart или `web.env`.

Exit code sync остаётся authoritative: `NO_OP` и `SYNCED` дают success,
`HUMAN_REQUIRED` и `FAILED` дают non-success. Результат bounded и может
показывать exact requested SHA, но не выводит secrets, Git diagnostics,
credentials или vault content.

## Как выбрать exact SHA

После push в `second-brain-vault` сначала получите и независимо проверьте
конкретный commit, а не branch name:

```bash
git -C /path/to/second-brain-vault fetch --no-tags origin main
VAULT_SHA="$(git -C /path/to/second-brain-vault rev-parse --verify refs/remotes/origin/main^{commit})"
git -C /path/to/second-brain-vault show -s --format='%H %s' "$VAULT_SHA"
```

В GitHub Actions вставьте напечатанный 40-character lowercase SHA в
`vault_sha` и включите `apply` только для этого exact owner-approved target.
Workflow снова fetch-ит `origin main` на VPS и останавливается при target drift;
он никогда не выбирает текущий `HEAD` или новый SHA самостоятельно.

Сам merge implementation PR #217 ничего не синхронизирует: он не запускает
этот manual workflow, не делает SSH и не меняет production. Owner должен
отдельно включить variable и отдельно вручную dispatch-нуть exact SHA.

## Manual exact-SHA owner prompt fallback

После push в `second-brain-vault` в `main` owner сначала получает exact commit
из локального control checkout. Генератор не обращается к сети и не принимает
непроверенный floating SHA:

```bash
cd /srv/second-brain/second-brain
uv run --python 3.14 --no-sync python scripts/generate_production_prompt.py \
  --operation vault-sync \
  --source-repo /srv/second-brain/second-brain-vault \
  --ref origin/main \
  --target-repository https://github.com/MikeMoore1337/second-brain-vault.git \
  --production-path /srv/second-brain/second-brain-vault \
  --app-root /srv/second-brain/current \
  --backup-root /srv/second-brain/runtime/vault-backups \
  --lock-path /srv/second-brain/runtime/vault-sync.lock \
  --expected-branch main \
  --expected-remote https://github.com/MikeMoore1337/second-brain-vault.git
```

Для уже подтверждённого SHA можно передать `--sha <40 lowercase hex chars>`.
Полученный prompt содержит exact SHA, repository, production path, lock,
backup path и explicit preflight. Во время самой операции remote branch снова
fetch-ится и обязан всё ещё указывать на этот exact SHA; target drift означает
`STOP / HUMAN_REQUIRED`.

Application prompt остаётся отдельной стратегией `--operation
application-deploy` и продолжает ссылаться на
`web-production.md`. Нельзя подставлять vault prompt в application release
workflow или считать vault sync deploy-ом.

## Protocol

Обычная application/user-facing Safe Write остаётся отдельной операцией для
одной managed note и сохраняет dry-run/no-overwrite/rollback boundary из
`AGENTS.md`. Этот workflow — узкий explicitly owner-authorized
repository-level production Vault Git Sync protocol для Issue #217; он не
является Safe Write и не является общим разрешением записи в vault. Он
разрешён только при exact-SHA, explicit
`--apply`, отдельном `PRODUCTION_VAULT_SYNC_ENABLED=true`, shared lock, clean
worktree, exact repository/branch, backup-before-FF, FF-only и post-sync
validation. Любой gate или invariant завершается fail-closed
`HUMAN_REQUIRED`; reset, rebase, force и auto-conflict-resolution запрещены.

`python -m second_brain.adapters.vault.sync` выполняет bounded explicit-argv
операцию с `shell=False`, `GIT_TERMINAL_PROMPT=0` и без вывода Git stderr,
stdout или содержимого заметок. Без явного `--apply` CLI является
неизменяющим режимом и останавливается до backup и merge; generated owner
prompt добавляет `--apply` только для уже подтверждённой операции.

Порядок действий:

1. Resolve только explicit absolute paths; vault root не может быть symlink,
   backup и lock обязаны находиться вне worktree, private directories имеют
   restrictive permissions.
2. Acquire shared non-blocking kernel lock. Lock contention — stop condition.
   Lock-файл не удаляется: JSON metadata внутри него является только подсказкой,
   а kernel ownership — источником истины. Старое metadata безопасно
   заменяется только после успешного acquisition.
3. Проверить ordinary non-bare Git worktree, top-level path, independent
   repository, `origin`, branch `main`, отсутствие незавершённых Git operations,
   clean tracked/untracked state и unsafe symlink/special-file paths.
4. Выполнить `git fetch --no-tags origin main`, затем убедиться, что fetched
   `origin/main` равен exact SHA из prompt. Fetch обновляет только Git
   remote-tracking metadata; до единственной рабочей tree mutation backup ещё
   не считается завершённым.
5. Классифицировать relation local `HEAD` к fetched `origin/main`.
6. Для clean strictly-behind состояния создать и проверить backup до
   fast-forward mutation. Equal state — validated no-op без backup.
7. Выполнить только `git merge --ff-only --no-edit --no-overwrite-ignore
   origin/main`, после чего проверить exact final SHA, clean tree, path
   integrity и application `vault validate`. Ignored local credential collision
   останавливает merge до перезаписи.
8. При успехе вернуть bounded `NO_OP` или `SYNCED`. Retention cleanup удаляет
   только собственные paired snapshots и всегда сохраняет минимум два
   последних известных backup; ошибка cleanup не откатывает и не удаляет
   существующие snapshots.

| State | Result | Mutation |
| --- | --- | --- |
| local == origin/main | `NO_OP` | validation only |
| clean local strictly behind | `SYNCED` | verified backup, then FF-only |
| local ahead | `HUMAN_REQUIRED` | none |
| dirty tracked/untracked | `HUMAN_REQUIRED` | none |
| diverged | `HUMAN_REQUIRED` | none |
| branch/remote/repository mismatch | `HUMAN_REQUIRED` | none |
| lock/Git operation/unsafe tree problem | `HUMAN_REQUIRED` or `FAILED` | none |
| target SHA drift, backup failure or validation failure | `HUMAN_REQUIRED`/`FAILED` | no merge if pre-sync; backup retained after post-sync failure |

Local VPS changes are never resolved by a bidirectional merge. Web Safe Write
uses the same lock when configured. Obsidian, an operator shell and other
external writers must be paused before sync; a clean-state check is repeated
before merge and after it, but no program can atomically lock an unmanaged
editor. If any such writer leaves tracked or untracked changes, sync stops.

## Remote identity и `insteadOf`

Проверка identity не доверяет transport URL, который Git может показать после
`url.*.insteadOf` rewriting. Sync читает ровно один raw configured value через
`git config --local --get-all remote.origin.url` и сравнивает его с
canonical:

```text
https://github.com/MikeMoore1337/second-brain-vault.git
```

`git remote get-url origin` не используется как authority для этой проверки:
он может показать переписанный SSH/file transport. Trusted `insteadOf` остаётся
разрешённым для самого `git fetch origin main`, но не расширяет список
разрешённых repositories и не меняет canonical expected remote. Если raw
configured origin указывает на другой repository, отсутствует или содержит
несколько URL, результат — `REMOTE_MISMATCH`/`HUMAN_REQUIRED` до fetch и без
repair. Expected repository не читается из mutable production checkout.

## Backup and recovery

Snapshots are local, recoverable and outside both Git repositories. The
directory is `<PRODUCTION_ROOT>/runtime/vault-backups` (for example,
`/srv/second-brain/runtime/vault-backups`); no paid storage or external backup
service is required by v1. Each snapshot has:

- private `tar.gz` archive and paired private `.manifest.json` sidecar;
- creation timestamp, source HEAD, requested origin target, file count and
  excluded credential count;
- per-file relative path, size and SHA-256, plus archive SHA-256;
- archive member/checksum verification before publication.

The snapshot excludes `.git`, `.env` variants and known credential/SSH-key
suffixes or names. It rejects symlinks and special files instead of following
them. Backup failure happens before fast-forward, so the worktree and refs are
left for manual review. Retention is bounded only over paired snapshots and
does not remove the last known-good backup.

If post-sync validation fails, the command does not guess whether to roll back:
it reports `POST_SYNC_VALIDATION_FAILED`, leaves the verified backup in place,
and stops further mutation. Owner reviews the backup manifest and current Git
state in a staging location, compares the exact files, and chooses a documented
manual recovery. Never use force operations, automatic conflict resolution,
blind overwrite, or a destructive cleanup to make the check green.

## Shared Safe Write lock

Production `<PRODUCTION_ROOT>/runtime/web.env` must contain this non-secret
variable in addition to the existing Web settings:

```dotenv
SECOND_BRAIN_VAULT_OPERATION_LOCK_PATH=<PRODUCTION_ROOT>/runtime/vault-sync.lock
```

The parent `runtime` directory is private and `web.env` remains mode `600`.
CLI and Web Safe Write pass the configured path to the filesystem writer; a
busy sync returns a bounded `CREATE_VAULT_OPERATION_BUSY` safety error rather
than writing. A missing variable keeps local development backward-compatible,
but it is not an acceptable production configuration for concurrent sync/write
coordination.

## STOP / HUMAN_REQUIRED

Stop and preserve state for owner review on any of these conditions:

- dirty or local-ahead vault, divergent history, detached/wrong branch,
  unexpected remote or non-independent repository;
- `index.lock`/rebase/merge/cherry-pick/revert/bisect state or lock contention;
- missing/unsafe paths, symlink or special file, target SHA drift, fetch or
  relation uncertainty;
- backup creation/checksum/publication failure;
- fast-forward or post-sync validation failure.

The operator must first preserve the current worktree and existing backups,
inspect `git status --porcelain=v1`, `git log`, branch/remote identity and the
bounded result, then perform any reconciliation manually. This mechanism never
resets, cleans, force-checks out, force-pushes, deletes notes/attachments or
auto-merges local VPS work.

Production sync is intentionally **not performed** by repository CI, this
implementation task or the application release workflow. It requires a fresh
owner authorization with an exact SHA and the separate vault prompt.
