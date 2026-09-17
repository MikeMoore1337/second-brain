# Stage 19 — factual delivery ledger

Дата closeout: 2026-09-17. Все промежуточные PR используют `Refs #366`; только
финальный closeout PR использует `Closes #366`. Идентификаторы ниже относятся
к exact-head PR CI, post-merge CI и standard automatic production deploy
соответствующего merge SHA.

| Фаза | PR / implementation commit | Merge SHA | Exact-head PR CI | Post-merge CI | Automatic deploy |
| --- | --- | --- | --- | --- | --- |
| 19.0 | [#368](https://github.com/MikeMoore1337/second-brain/pull/368) / `cfabf70571b4108334344d2d229acc0d50264864` | `3051401f8b1a5629d319fef42afbb95ff632dffd` | [35135404813](https://github.com/MikeMoore1337/second-brain/actions/runs/35135404813) | [35135807214](https://github.com/MikeMoore1337/second-brain/actions/runs/35135807214) | [35136126286](https://github.com/MikeMoore1337/second-brain/actions/runs/35136126286) |
| 19.1 | [#369](https://github.com/MikeMoore1337/second-brain/pull/369) / `e5e554b5854928a15089e172ea0de14ee585dac3` | `df2e703ed91e7c968f54483bbb146f39ff77de59` | [35138854724](https://github.com/MikeMoore1337/second-brain/actions/runs/35138854724) | [35139359632](https://github.com/MikeMoore1337/second-brain/actions/runs/35139359632) | [35139717349](https://github.com/MikeMoore1337/second-brain/actions/runs/35139717349) |
| 19.2 | [#370](https://github.com/MikeMoore1337/second-brain/pull/370) / `104819ddf6f533de74a7ec96f92b9349ee6f6955` | `249718211fe5388cf58b838b4640b1c71a212d87` | [35141539021](https://github.com/MikeMoore1337/second-brain/actions/runs/35141539021) | [35142024500](https://github.com/MikeMoore1337/second-brain/actions/runs/35142024500) | [35142345897](https://github.com/MikeMoore1337/second-brain/actions/runs/35142345897) |
| 19.3 | [#371](https://github.com/MikeMoore1337/second-brain/pull/371) / `4347ff220927dd0699124fb441dbe42e47118e0f` | `2a9f6fb86880d786c5ff0b5744e68d3e799ad3f6` | [35146011077](https://github.com/MikeMoore1337/second-brain/actions/runs/35146011077) | [35146563724](https://github.com/MikeMoore1337/second-brain/actions/runs/35146563724) | [35146858642](https://github.com/MikeMoore1337/second-brain/actions/runs/35146858642) |
| 19.4 | [#372](https://github.com/MikeMoore1337/second-brain/pull/372) / `478c7277555c2e20232b3912b2d8fd9d88701bae` | `b8f6c99a3512fd3e57a0894d1a6db3a52d783e0d` | [35151380468](https://github.com/MikeMoore1337/second-brain/actions/runs/35151380468) | [35151897843](https://github.com/MikeMoore1337/second-brain/actions/runs/35151897843) | [35152118065](https://github.com/MikeMoore1337/second-brain/actions/runs/35152118065) |
| 19.5 | [#373](https://github.com/MikeMoore1337/second-brain/pull/373) / `79eff31e8cfc30cb06ffac4bfe94c735bf988828` | `e5d913201c5e1d3bcbf4d705966df0da6558778e` | [35154595502](https://github.com/MikeMoore1337/second-brain/actions/runs/35154595502) | [35155080232](https://github.com/MikeMoore1337/second-brain/actions/runs/35155080232) | [35155305666](https://github.com/MikeMoore1337/second-brain/actions/runs/35155305666) |
| 19.6 | финальный closeout PR этого документа (`Closes #366`) | final closeout evidence | final PR exact-head evidence | final merge post-merge CI evidence | final merge automatic deploy evidence |

## Closeout assertions

- Action Gateway v1 принимает только явный owner intent и per-action
  confirmation. Automatic external action, background scheduler, multi-step
  orchestration и Stage20 authority отсутствуют.
- Единственный production connector — закрытый GitHub Issues catalog:
  `github.issue.create`, `github.issue.comment`, `github.issue.set_state` для
  exact repository `MikeMoore1337/second-brain`; PR merge/review, workflow
  dispatch, content/admin/settings/secrets actions и generic HTTP executor
  запрещены.
- Login GitHub OAuth остаётся login-only: `/user` используется для проверки
  owner identity, access token transient и не становится action credential.
  Action connector использует отдельный token, fixed target
  `https://api.github.com`, exact allowlist, bounded HTTPS/TLS request и
  отсутствие blind retry.
- Prepare/preview/confirm/execute/reconcile/compensation и append-only
  receipt/audit store доставлены; `outcome_uncertain` требует explicit
  read-only reconciliation и не предлагает mutation retry. Комментарии не
  компенсируются удалением; inverse state/create compensation требует нового
  Prepare + Confirm.
- Store располагается вне vault, repository, release и worktree по explicit
  env-file parent: `prospective-audit/action-gateway/`. Production smoke не
  создавал receipt или внешний side effect.
- Full local gate: Python 3.14 `ruff format --check`, Ruff, mypy и
  `4062 passed, 69 skipped`; frontend typecheck, 26 Vitest files / 143 tests,
  production build, PWA QA; Playwright/Axe 320–1920 px без overflow и с Axe
  pass на 390/1440.
- Exact deployed SHA `e5d913201c5e1d3bcbf4d705966df0da6558778e` прошёл
  post-merge CI `35155080232` и deploy `35155305666`. Production `/healthz`
  вернул 200. Anonymous Stage19 status POST вернул 401 `AUTH_REQUIRED`,
  `Cache-Control: no-store` и safe Russian error; GitHub mutation не выполнялся.
- Production action connector сейчас `disabled`: optional keys объявлены в
  versioned preflight contract, но production values не менялись.
  `disabled runtime env change required = no`. Для будущего owner-enabled
  режима потребуются exact keys: `SECOND_BRAIN_ACTION_GITHUB_ENABLED=true`,
  protected `SECOND_BRAIN_ACTION_GITHUB_TOKEN` и
  `SECOND_BRAIN_ACTION_GITHUB_REPOSITORIES=MikeMoore1337/second-brain`.
- Stage 16, Stage 17 и Stage 18 не получили completion/progress/effort/event
  writes; LLM/provider outputs не обладают action authority. Calendar и Email
  остаются `DEFERRED`; Stage20 — `PLANNED / NOT STARTED`.
- `second-brain-vault` и real vault data не изменялись. Cleanup после каждой
  verified green merge выполнялся bounded dry-run; активный текущий worktree
  сохранялся, unknown/dirty/active paths не удалялись.

At the original PR #374 closeout, the recorded operational state was:

```text
Stage 19 implementation COMPLETE: YES
GitHub action connector code COMPLETE: YES
GitHub live connector enabled: NO
HUMAN_REQUIRED: GitHub action credential provisioning
Stage 19 full live closeout: PENDING
Stage 20 started: NO
```

## Post-closeout live activation follow-up (Issue #375)

Это отдельное фактическое продолжение после исторического завершения
реализации 19.0–19.6. Оно не является Phase 19.7, не переопределяет Issue
#366 и не изменяет семантику Stage 19 runtime.

Issue: [#375](https://github.com/MikeMoore1337/second-brain/issues/375)

Сведения о delivery этого продолжения заполняются только фактическими данными
после создания, merge и post-merge lifecycle:

| Поле | Фактическое значение |
| --- | --- |
| PR | [#376](https://github.com/MikeMoore1337/second-brain/pull/376) |
| Merge SHA | `ad7b7ff1d8636936afc3ae8e8a20613c5f79873f` |
| Exact-head CI | [35181370694](https://github.com/MikeMoore1337/second-brain/actions/runs/35181370694) |
| Post-merge CI | [35181618549](https://github.com/MikeMoore1337/second-brain/actions/runs/35181618549) |
| Automatic deploy | [35181833618](https://github.com/MikeMoore1337/second-brain/actions/runs/35181833618) |

### Production-свидетельства, подтверждённые владельцем — 2026-09-17

После перезапуска production-сервиса владелец подтвердил
`second-brain-web.service`:
`ActiveState=active`, `SubState=running`; Uvicorn слушал `127.0.0.1:8123`, а
`/healthz` вернул `HTTP 200` и `{"status":"ok"}` с существующими security
headers. Первый неуспешный curl был startup race до начала прослушивания
порта; последующие service status/logs показали штатный запуск.

Защищённая production-конфигурация была проверена без вывода секрета:

```text
SECOND_BRAIN_ACTION_GITHUB_ENABLED=true
SECOND_BRAIN_ACTION_GITHUB_TOKEN=<owner-managed secret; value never recorded>
SECOND_BRAIN_ACTION_GITHUB_REPOSITORIES=MikeMoore1337/second-brain
/srv/second-brain/runtime/web.env = second-brain:second-brain, mode 600
TOKEN_SET=yes
```

Владелец настроил fine-grained GitHub token только с правами `Metadata:
Read-only` и `Issues: Read and write` для `MikeMoore1337/second-brain`. Не
документируются и не подразумеваются permissions для `Contents`, `Pull
requests`, `Actions`, `Workflows`, `Administration`, `Deployments`, `Secrets`,
`Variables`, `Environments`, `Webhooks`, repository administration или
classic broad repo scope.

Статус Safe Action Gateway получен вызовом production-кода под пользователем
`second-brain` с production env file:

```text
contract = action-gateway-v1
connector = github_issues
policy_id = github-issues-v1
credential_profile_id = github-actions-primary
status = ready
configured = true
ready = true
repositories = MikeMoore1337/second-brain
store_status = not_checked (gateway status probe)
owner_confirmation_required = true
background_execution = false
```

Закрытый каталог действий остался прежним:

| Action kind | Risk | Reversibility |
| --- | --- | --- |
| `github.issue.create` | `controlled_write` | `compensation_only` |
| `github.issue.comment` | `controlled_write` | `not_supported` |
| `github.issue.set_state` | `controlled_write` | `supported` |

Реальный production-адаптер GitHub выполнил тот же read-only preflight, что и
`prepare()`, без вызова `execute()` и без `POST`/`PATCH` mutation:

```text
credential_status = ready
repository = MikeMoore1337/second-brain
repository_id = 1354056312
repository_node_id = R_kgDOULVCeA
issue_number = null
issue_id = null
issue_node_id = null
current_state = null
locked = null
preflight_ok = true
mutation_executed = false
```

Operational store Action Gateway был инициализирован и проверен реальным
production service code вне vault, repository, release и worktree:

```text
store_root = /srv/second-brain/runtime/prospective-audit/action-gateway
store_exists = true
receipt_count = 0
.store.lock = mode 0600, size 0
manifest.json = mode 0600, size 200
receipts.jsonl = mode 0600, size 0
github_mutation_executed = false
```

Инициализация store не является внешним GitHub action. При проверке активации не
создавались GitHub issue/comment/state mutation и новый Stage19 receipt;
мутация GitHub action не выполнялась. Значение token ни разу
не записывалось и не раскрывалось в Git, docs, Issue, PR, CI, logs, receipts,
vault или browser output. Web login OAuth не использовался как action
credential; Stage 18 data и `second-brain-vault` не менялись.

Итоговый фактический status:

```text
Stage 19 implementation COMPLETE: YES
GitHub action connector code COMPLETE: YES
GitHub live connector enabled: YES
GitHub live credential configured: YES
GitHub credential read-only preflight verified: YES
exact repository allowlist verified: YES
Action Gateway operational store ready: YES
owner confirmation required for every controlled write: YES
background execution: NO
autonomous external action: NO
external mutation during credential activation validation: NO
receipt created during activation validation: NO
login OAuth reused for actions: NO
Stage18 auto-completion from actions: NO
vault changed by activation: NO
second-brain-vault repository changed: NO
Calendar runtime: DEFERRED
Email runtime: DEFERRED
HUMAN_REQUIRED: NO
Stage 19 full live closeout: COMPLETE
Stage 20 started: NO
```
