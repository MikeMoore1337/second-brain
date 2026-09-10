# Vault Git Sync & Backup v1

Этот runbook описывает отдельную production-операцию для persistent
`second-brain-vault`. Она не является application deployment и не заменяет
[Web production runbook](web-production.md).

## Границы

В production остаются два независимых repository и обычный Git worktree:

```text
/srv/second-brain/
├── second-brain/                         # application/control checkout
├── second-brain-vault/                   # persistent vault worktree, main
├── releases/                             # application release worktrees
├── current -> releases/<APPLICATION_SHA>
└── runtime/
    ├── web.env                            # вне обоих repositories, mode 600
    ├── vault-sync.lock                    # shared operation lock, mode 600
    └── vault-backups/                     # local recoverable snapshots, mode 700
```

`second-brain` отвечает за приложение, CLI, Safe Write и этот sync mechanism.
`second-brain-vault` остаётся canonical Markdown/YAML repository и не становится
submodule, subtree или каталогом application repository. Sync не запускает
build, `npm`, service restart, `current` activation, application rollback или
изменение production credentials.

## Exact-SHA owner prompt

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

## Backup and recovery

Snapshots are local, recoverable and outside both Git repositories. The default
directory is `/srv/second-brain/runtime/vault-backups`; no paid storage or
external backup service is required by v1. Each snapshot has:

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

Production `/srv/second-brain/runtime/web.env` must contain this non-secret
variable in addition to the existing Web settings:

```dotenv
SECOND_BRAIN_VAULT_OPERATION_LOCK_PATH=/srv/second-brain/runtime/vault-sync.lock
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
