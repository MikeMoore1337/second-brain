# Personal Agent / Chief of Staff v1 — нормативный контракт Stage 20

**Статус:** NORMATIVE CONTRACT / STAGE 20 IN PROGRESS
**Контракт:** `personal-agent-v1`
**Владелец:** только owner, через явные foreground-действия
**Дата фиксации:** 2026-09-17

Этот документ является source of truth для Stage 20 и всех последующих фаз
20.1–20.6. Он добавляет bounded orchestration layer поверх принятых
Stage 17–19. Он не изменяет семантику Stage 16, Stage 17, Stage 18, Stage 19,
Assistant v1 или `second-brain-vault`.

## 1. Назначение и границы

Stages 16–19 дают цепочку:

```text
UNDERSTAND -> DECIDE -> PLAN -> OBSERVE EXECUTION -> CONTROLLED EXTERNAL ACTION
```

Stage 20 добавляет только owner-controlled coordination:

```text
OWNER MISSION
  -> EXACT CONTEXT PACK
  -> REVIEWED RUN PROPOSAL
  -> ACCEPT
  -> EXPLICIT START
  -> ONE CURRENT STEP
  -> STAGE19 PREPARE / PREVIEW / CONFIRM / EXECUTE
  -> RECEIPT OR OWNER INPUT
  -> EXPLICIT CONTINUE / REVISE / COMPLETE
```

Stage 20 не является direct tool authority, hidden function calling,
unattended autopilot, background worker, scheduler, cron agent, browser
automation framework, shell agent, arbitrary HTTP client или provider-controlled
tool runner.

В v1 запрещены background loop, polling, timer-driven progression, push-driven
execution, service-worker execution, offline replay, timer-driven action,
automatic overnight action, automatic `continue` и любое продолжение после
закрытия страницы. Каждое значимое изменение состояния требует отдельного
foreground-действия owner.

## 2. Digital Self и агентность

Stage 20 увеличивает functional action continuity: система может связать
память, reasoning, preferences, reviewed planning и способы действия owner в
ограниченном operational run. Это не перенос и не воспроизведение
субъективного сознания.

Operational Mission, Context Pack, Proposal и Run не являются автоматически
канонической autobiographical truth, Personal Memory, Decision Journal,
Goal, Goal Progress или Stage 18 evidence. В проекте нет единого
«процента владельца»: fidelity остаётся набором независимых измерений
происхождения, текущего состояния, предпочтений, reasoning и action continuity.

## 3. Authority matrix

| Слой | Роль в Stage 20 | Может сам разрешить внешнюю запись |
| --- | --- | --- |
| Accepted Stage 17 `PlanningPlanV1` | единственный источник mission scope | нет |
| Exact selected Stage 17 items | bounded executable subjects | нет |
| Stage 18 exact projections | текущая execution evidence projection | нет |
| Stage 19 safe readiness/catalog | capability metadata | нет |
| Явный owner Mission и target refs | operational intent | нет |
| Provider result | derived/untrusted proposal candidate | нет |
| Owner-reviewed accepted Run | operational coordination state | нет |
| Stage 19 `Prepare` + exact preview + confirmation | mutation authority | да, только для одного действия |
| Stage 19 receipt/reconciliation | external-action evidence | нет |

Единственный разрешённый внешний путь:

```text
accepted Stage20 action step
 -> Stage19 ActionIntentV1
 -> Stage19 Prepare and remote preflight
 -> PreparedExternalActionV1
 -> exact Stage19 owner preview
 -> explicit Stage19 confirmation
 -> Stage19 Execute
 -> Stage19 receipt
 -> bounded Stage20 observation
```

Stage 20 не получает GitHub credential, OAuth token, Authorization header,
confirmation token или прямой доступ к `GitHubIssuesActionConnectorV1`.
Stage 20 не вызывает connector, не копирует HTTP mutation code и не создаёт
второй Action Gateway.

## 4. Точная область Stage 17

Mission обязана ссылаться на один exact accepted Stage 17 portfolio snapshot:

```text
planning_snapshot_id
planning_snapshot_fingerprint
planning_policy_id
planning_policy_fingerprint
```

Owner явно выбирает от 1 до 16 items из этого snapshot. В v1 executable kinds
только `commitment` и `next_action`. `project`, `milestone` и `hold` не могут
быть subjects Run. Выбранный item связывается всеми immutable полями:

```text
item_id
accepted_item_fingerprint
item_kind
accepted bounded item projection
exact goal_refs
exact Stage16 action_refs already bound by Stage17
```

Нельзя выбирать item по title, text similarity, «последнему плану», Goal name
или fuzzy match. Нельзя молча добавить остальные items. Текущая версия,
missing snapshot, superseded snapshot, неправильный fingerprint, duplicate
identity, cross-plan или cross-Goal reference дают fail-closed результат.

## 5. `AgentMissionV1`

Mission — строгий bounded immutable request, не запись vault:

```text
contract_version: "personal-agent-mission-v1"
mission_id: UUIDv7
planning_snapshot_id: UUIDv7
planning_snapshot_fingerprint: sha256 hex
planning_policy_id: exact Stage17 policy id
planning_policy_fingerprint: sha256 hex
selected_items: 1..16 exact item bindings
task: owner-entered bounded text
constraints: 0..16 owner-entered bounded texts
current_context: 0..8 owner-entered bounded texts
external_targets: 0..16 exact owner-entered target refs
created_at: RFC3339 UTC
reviewed_at: RFC3339 UTC or null
```

Task, constraints и context являются operational input. Они не записываются
автоматически в vault, Personal Memory, Decision Journal, Goal или Stage 18.
`mission_id` — operation identity, а не provider idempotency key и не secret.

`ExternalTargetRefV1` допускает только exact repository alias и, для
`github.issue.comment`/`github.issue.set_state`, exact positive issue number.
Для `github.issue.create` issue number отсутствует. URL, endpoint, headers,
token, search query, title lookup и «latest issue» запрещены. Stage 19 remote
preflight остаётся final target authority.

## 6. `AgentContextPackV1`

До любого provider call application строит provider-free immutable pack.
Pack создаётся только из exact accepted Stage 17 snapshot, выбранных items,
current Stage 18 projections, safe Stage 19 readiness/catalog и explicit
Mission. Pack содержит:

```text
pack_contract_version
exact planning identity and policy
selected bounded item aliases/text, goal aliases and Stage16 refs
current neutral Stage18 state/feedback summary per selected item
Stage19 readiness, closed action catalog, repository aliases
risk/reversibility vocabulary
explicit mission, constraints, context and target aliases
source readiness, stale/caveat codes
pack_fingerprint
```

Pack не содержит raw vault notes, all Personal Memory, Decision Journal bodies,
unrelated Goals, unselected items, raw Stage16 proposals, raw Stage19
receipts/history, GitHub issue bodies/comments, credentials, filesystem paths,
environment, store roots или browser session values. Pack construction не
вызывает provider/network и не пишет state.

Exact source drift не чинится через newest-source rebind. При изменении
snapshot/item/policy, отсутствии Stage18 projection или недоступности Stage19
pack получает bounded stale/unavailable status и Build завершается безопасно.

## 7. Provider-visible reasoning boundary

Provider переиспользуется только через существующий provider-neutral
`AdvisorPort` и approved adapter. Новый provider, model, credential, secret
или network route не добавляется.

Перед `Build run`/`Revise run` owner должен увидеть exact canonical
`AgentReasoningEnvelopeV1`. В envelope разрешены только проекции pack:

```text
mission task, constraints and explicit context
selected item aliases and reviewed bounded text
required Goal/source aliases
neutral current Stage18 summaries
explicit external target aliases
Stage19 action kinds, repository aliases, risk and reversibility
```

Canonical preview bytes должны быть byte-for-byte equivalent payload, переданному
в `AdvisorPort`. Никакой скрытой suffix/context injection нет. В payload не
попадают token, cookie, raw receipt store, raw vault text, all history,
filesystem path, environment или provider credentials.

Provider вызывается только после явного owner `Build run` или `Revise run`.
Page load, selection, refresh, run start/continue, Prepare, Execute, receipt,
pause, complete и browser reconnect не вызывают provider. Provider output не
может вызвать tool, function, MCP, shell, HTTP, browser action или GitHub
mutation.

## 8. `AgentRunProposalV1`

Provider result — derived/untrusted и проходит строгую валидацию. Proposal:

```text
contract_version: "personal-agent-run-proposal-v1"
proposal_id: UUIDv7
mission_fingerprint: sha256 hex
context_pack_fingerprint: sha256 hex
steps: 1..12 ordered unique steps
caveats: 0..16 bounded neutral texts
provider_policy_id/fingerprint
```

Run linear, не DAG и не workflow engine. Каждый step имеет unique local
`step_id`, position, closed `kind` и bounded display fields. Допускаются только:

### `clarify`

Нейтральный bounded question, причина необходимости и optional structured
answer shape. Ответ owner остаётся operational input и не становится memory.

### `checkpoint`

Bounded summary/condition, после которой требуется отдельный owner `Continue`.
Внешнего эффекта нет.

### `stage19_action`

Один candidate для одного Stage 19 catalog action. Для create допустимы только
`repository`, `title`, `body`; для comment — `repository`, exact `issue_number`,
`comment`; для set-state — `repository`, exact `issue_number`, `desired_state`
(`open|closed`). Никаких arbitrary GitHub fields, JSON, URL, endpoint, header,
token или provider payload.

### `hold`

Рекомендация остановить progression из-за недостаточного, stale, конфликтного
или намеренно deferred context. Automatic workaround отсутствует.

Unknown kind, arbitrary tool/command/http/shell/browser, executable code,
arbitrary URL, unsupported action, nested free-form payload, unapproved target,
duplicate step, proposal >12 или malformed JSON отклоняются fail closed; UI
может предложить owner создать новый bounded proposal.

## 9. Review и принятие Run

Proposal не активен автоматически. Owner видит Mission, exact planning source,
selected items, Stage18 summaries, step order/kinds/text, action semantics,
holds/checkpoints, caveats и provider-visible preview/provenance.

До acceptance owner может reject proposal, удалить/reorder steps, исправить
bounded human-readable fields, изменить action content внутри закрытого
Stage19 schema или преобразовать action в hold. Нельзя изменить immutable
mission/source/item identity, добавить kind или target authority.

Server заново валидирует полный reviewed proposal, source fingerprints,
step bounds, target refs и current readiness. Только explicit `Accept run`
создаёт `AgentRunSnapshotV1`; accept не означает start и не является Stage19
authorization.

Accepted snapshot связывает:

```text
run_id and revision
mission identity/fingerprint
context pack fingerprint
reviewed proposal fingerprint
exact Stage17 identity and item bindings
Stage18 context fingerprints
Stage19 policy/catalog identity
Stage20 policy/version
accepted_at
provider provenance
owner edits
reviewed steps
```

Raw provider answer, token, credential и raw history не сохраняются. Snapshot
immutable; correction/revision создаёт новую versioned record, старую не меняет.

## 10. One-current-run и lifecycle

Глобально допускается не более одного current active Stage20 Run. Historical
runs auditable. Другой Run нельзя принять/запустить, пока текущий явно не
`complete`, `abandon` или не superseded по bounded revision contract.

Run state vocabulary:

```text
proposal
rejected
accepted
active
waiting_owner
waiting_stage19
paused
ready_to_complete
completed
abandoned
superseded
```

Разрешённые переходы:

```text
proposal -> rejected | accepted
accepted -> active                  (explicit Start)
active -> waiting_owner             (clarify/checkpoint)
active -> waiting_stage19           (current action prepared)
active -> paused                    (explicit Pause)
waiting_owner -> active             (answer/Continue)
waiting_stage19 -> active           (receipt observed + explicit Continue)
paused -> active                    (explicit Resume)
active|paused|waiting_owner -> abandoned (explicit Abandon)
active|paused|waiting_owner -> ready_to_complete (all steps resolved)
ready_to_complete -> completed     (explicit Complete)
accepted|active|paused -> superseded (explicit bounded revision)
```

Нет automatic final completion, timeout transition, silent success или
provider-directed progression. `Start`, `Pause`, `Resume`, `Answer`,
`Continue`, `Skip`, `Revise`, `Abandon` и `Complete` — отдельные owner actions.

## 11. Step lifecycle и foreground progression

Accepted steps используют закрытые состояния:

```text
pending -> current -> waiting_owner | waiting_stage19 | completed | skipped | blocked
```

Только один step может быть `current`. Нельзя concurrently prepare/execute
несколько external mutations, batch-confirm steps или pre-authorize future
actions. Accept не продвигает step; Start делает первый step current. После
каждого resolved step следующий становится current только отдельным явным
`Continue` либо соответствующим owner action.

`clarify` ждёт explicit answer; `checkpoint` ждёт explicit Continue; `hold`
остаётся blocked/held до explicit revision/resolution. `Skip` требует owner
действия и оставляет audit reason. `blocked` не означает моральную оценку,
failure, laziness или productivity score.

## 12. Stage 19 bridge и результаты

Stage20 зависит от narrow application port, который принимает typed
Stage19 semantics и возвращает safe prepared/result projections. Bridge не
импортирует GitHub adapter и не читает credential. Он:

1. проверяет, что Run accepted/active, step current и source не drifted;
2. строит ровно один typed `ActionIntentV1` из закрытого action step;
3. вызывает Stage19 `Prepare` и показывает Stage19 exact preview;
4. сохраняет prepared action и confirmation token только в page memory;
5. передаёт explicit confirmation обратно Stage19;
6. наблюдает только safe receipt/result projection.

Каждое действие отдельно проходит Stage19 Prepare и confirmation. Нет Stage20
`Approve all`, `Run remaining actions`, blanket delegation или automatic second
action. Stage19 сохраняет at-most-once, remote preflight, revalidation,
reconciliation и compensation authority.

Результаты `executed`, `already_satisfied`, `failed_before_send`,
`failed_confirmed_no_mutation`, `outcome_uncertain`,
`reconciled_executed`, `reconciled_not_executed` и
`reconciliation_ambiguous` отображаются нейтрально. `outcome_uncertain` не
запускает blind retry. Reconciliation — отдельное read-only Stage19 действие;
compensation — отдельный Stage19 intent с новым Prepare/Confirm.

Успешный receipt никогда автоматически не создаёт Stage18 `complete`,
`actual_effort`, `block`, `abandon`, Goal Progress или vault evidence.

## 13. Source и capability drift

Перед Build, Accept, Start, Prepare и Continue проверяется нужная exact
identity. Изменение Stage17 current plan/item, Stage18 projection, Stage19
readiness/catalog/policy или allowlist не вызывает newest-source rebind.
Run получает bounded stale/conflict/unavailable status и ждёт явной Revision,
Pause, Abandon или другого owner решения.

Connector disabled/unavailable блокирует только соответствующий action step и
не создаёт обходной HTTP path. GitHub permission expansion, новый connector,
Calendar или Email не являются скрытым fallback.

## 14. Operational store

Accepted Run и lifecycle events живут вне repository, worktree, release и
vault, рядом с существующими operational stores:

```text
<env-file-parent>/prospective-audit/personal-agent/
  runs.jsonl
  manifest.json
  .store.lock
```

Новый production env key не нужен: root выводится из существующего explicit
env-file parent. Store использует existing lock/integrity/recovery conventions:
bounded canonical JSONL, append-only records, sequence/digest chain, atomic
manifest, fsync, safe permissions, path containment, symlink rejection и
fail-closed recovery. Torn append, corruption, reordered/missing sequence,
lock contention и unsafe permissions не чинятся автоматически.

Store persist-ит только bounded operational projections: fingerprints,
identities, reviewed step semantics, lifecycle transitions и safe Stage19
receipt references после result/reconciliation. Нельзя сохранять confirmation
token, credential, OAuth/browser session, Authorization header, raw provider
payload, raw vault/Goal body, raw GitHub response, filesystem path,
environment или exception text.

Под lock повтор операции с тем же idempotency fingerprint и тем же exact
intent возвращает безопасный existing result; другой intent даёт conflict.
Один current Run и один current step проверяются атомарно. Store не становится
generic provider history database.

## 15. No automatic learning, inference или persuasion

Mission answers, Run choices, receipts и timing не retrain provider, Cognitive
Twin или hidden weights. Stage20 не создаёт psychological/productivity score,
reward/punishment, persuasion optimization, personality inference или claim о
consciousness. Любое сохранение в canonical memory требует отдельного уже
существующего reviewed Safe Write workflow.

## 16. Owner-only API и browser privacy

Stage20 routes additive и используют текущую границу private API:

```text
authenticated owner session
trusted Host
same-origin Origin
exact Stage20 request-purpose header
strict HTTP method and JSON content type
bounded raw body before parse
strict extra=forbid DTOs
Cache-Control: no-store
current CSP, nosniff, referrer and frame protections
no permissive CORS
bounded safe Russian errors
```

Anonymous requests отклоняются до provider call, Stage20 store access, Stage19
Prepare или network. Private Mission/Pack/Proposal/prepared action/
confirmation state остаётся memory-only в page. Запрещены
`localStorage`, `sessionStorage`, IndexedDB, Cache Storage authority,
service-worker private API caching, background sync, offline progression,
offline replay и provider payload в console logs.

## 17. UX, responsive и accessibility

Owner surface называется `Chief of Staff` или естественным русским эквивалентом
`Агент` в текущей информационной архитектуре. Он additive и не перегружает
Strategy/Planning/Execution screens. Primary view отвечает на вопросы:

```text
какова миссия?
какой выбран план и работа?
где находится Run?
какой текущий шаг?
что требуется от меня?
```

Technical identities, fingerprints, policy, receipt IDs и provenance скрыты в
progressive disclosure. Action card явно сообщает, что Run сам по себе не
меняет GitHub; отдельный Stage19 preview/confirmation обязателен.

Проверяются widths 320, 360, 390, 430, 768, 1024, 1440 и 1920: нет horizontal
overflow, keyboard-only путь complete, visible focus, semantic step status,
accessible inputs/preview, focus restoration, `aria-live`, `aria-busy`, risk
не кодируется только цветом, reduced-motion parity и нет hover-only controls.
Пользовательский текст production UI, ошибки, empty/loading states,
`aria-label` и `placeholder` — естественный русский.

## 18. Ошибки и status vocabulary

Application/API использует bounded machine codes и безопасные русские тексты:

```text
AUTH_REQUIRED
INVALID_REQUEST
SOURCE_UNAVAILABLE
SOURCE_STALE
SOURCE_MISMATCH
ITEM_NOT_SELECTED
NON_EXECUTABLE_ITEM
MISSION_LIMIT_EXCEEDED
PROPOSAL_INVALID
PROPOSAL_TOO_LARGE
PROVIDER_UNAVAILABLE
RUN_CONFLICT
RUN_NOT_CURRENT
STEP_NOT_CURRENT
INVALID_TRANSITION
ACTION_NOT_ALLOWED
ACTION_PREPARE_REQUIRED
STAGE19_UNAVAILABLE
RECEIPT_UNCERTAIN
RECONCILIATION_REQUIRED
STORE_UNAVAILABLE
STORE_CORRUPT
```

Raw exception/provider/GitHub/path/secret details никогда не возвращаются
owner UI/API или логам.

## 19. Alternatives register

| Решение | Статус | Причина |
| --- | --- | --- |
| provider function/tool calling | REJECT | provider не получает authority |
| arbitrary workflow graph/DAG | REJECT | linear 1..12 проще проверять и безопаснее |
| direct GitHub adapter from Stage20 | REJECT | Stage19 — единственная mutation boundary |
| new connector, provider, model or secret | REJECT | существующие seams достаточны |
| background agent/scheduler/polling | REJECT | v1 foreground owner control |
| raw vault/history in context | REJECT | least context и provenance |
| fuzzy/latest/text source binding | REJECT | exact identity обязательна |
| Stage20 completion as Stage18 event | REJECT | execution evidence остаётся owner authority |
| new DB/framework/global schema/NoteType | REJECT | existing JSONL operational pattern достаточен |
| batch confirmation / blanket approval | REJECT | per-action Stage19 consent |
| automatic learning/score/persuasion | REJECT | agency не равна behavioral optimization |

## 20. Impact contract

```text
new provider/model/secret = NO
provider network path = existing AdvisorPort only
new provider payload boundary = YES, explicit Build/Revise only
Assistant v1 semantics changed = NO
Stage16/17/18/19 semantics changed = NO
Stage19 GitHub permissions expanded = NO
new external connector = NO
new Stage20 operational state = YES
new DB/framework = NO
global schema_version changed = NO
new NoteType = NO
second-brain-vault changed = NO
real vault data changed by release verification = NO
background agent/scheduler/polling = NO
autonomous external action = NO
Stage19 bypass = NO
Calendar = DEFERRED
Email = DEFERRED
env change required = no
```

Каждая реализационная фаза обязана перепроверить этот список по фактическому
diff и deployment contract. Нельзя выводить env change из наличия уже
существующего значения; при обнаружении действительно обязательного ключа
его имя, режим и release-blocker status фиксируются отдельно.

## 21. Delivery map 20.0–20.6

| Фаза | Обязательный результат |
| --- | --- |
| 20.0 | этот нормативный contract, roadmap status, exact-head CI, merge и post-merge deploy |
| 20.1 | exact Mission bindings, provider-free Context Pack, source/readiness projections и tests |
| 20.2 | explicit previewed Advisor boundary, strict linear Proposal, provider doubles и tests |
| 20.3 | append-only reviewed Run lifecycle, one-current invariants и narrow Stage19 bridge |
| 20.4 | owner-only Russian Web/API/UI, foreground controls, memory-only browser state |
| 20.5 | adversarial security/privacy/integration/E2E, responsive/accessibility и regressions |
| 20.6 | factual ledger, final full gates, production smoke, closeout `Closes #378` |

Intermediate PRs используют `Refs #378`; только Phase 20.6 использует
`Closes #378`. Каждая фаза начинается с fresh `origin/main`, использует
isolated worktree, проходит focused tests и repository-required gates, затем
exact-head CI, merge, post-merge CI, standard automatic deploy и
non-mutating production smoke. Никаких manual deploy, Vault Sync или stale
head evidence.

## 22. Граница завершения v4

Stage 20 — последняя определённая стадия Second Brain v4. Только после
успешной Phase 20.6, фактического full gate и production closeout разрешено
зафиксировать:

```text
Cognitive Twin v3 = COMPLETE
Second Brain v4 = COMPLETE
Stage 16 = COMPLETE
Stage 17 = COMPLETE
Stage 18 = COMPLETE
Stage 19 = COMPLETE
Stage 20 = COMPLETE
```

Это означает существование доказуемой цепочки:

```text
REMEMBER -> UNDERSTAND -> DECIDE -> PLAN -> EXECUTION FEEDBACK
-> CONTROLLED EXTERNAL ACTION -> OWNER-CONTROLLED AGENT ORCHESTRATION
```

Это не означает copied subjective consciousness, perfect life model, fully
autonomous AI, unlimited tools или отсутствие owner control. Stage 21 и Second
Brain v5 не создаются автоматически.
