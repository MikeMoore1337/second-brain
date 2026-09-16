# Controlled External Integrations & Action Gateway v1 — контракт Stage 19

**Статус:** NORMATIVE CONTRACT / STAGE 19 COMPLETE

**Контракт:** `action-gateway-v1`

**Политика:** `stage19-action-policy-v1`

**Владелец:** только owner, через приватные Web/API поверхности
**Дата design gate:** 2026-09-16
**Дата closeout:** 2026-09-17

Этот документ является нормативной границей Stage 19. Он разрешает только
явно подготовленные и отдельно подтверждённые действия владельца. Он не
создаёт автономного агента, не превращает производные результаты в authority и
не меняет контракты Stage 16, Stage 17 или Stage 18.

## 1. Назначение и переход

Stage 19 добавляет к цепочке Second Brain v4 отдельный контролируемый переход:

```text
explicit owner intent
  -> read-only target preflight
  -> immutable prepared action
  -> exact owner preview
  -> explicit confirmation
  -> fresh target validation
  -> at-most-once external mutation
  -> bounded receipt
  -> explicit reconciliation or compensation when needed
```

Это первая стадия с внешним побочным эффектом. Любое действие остаётся
`owner-controlled`: результат Cognitive Twin, профиль Stage 15, Strategy,
Planning, Stage 18, Advisor или LLM может быть только контекстом/provenance и
никогда не является разрешением на мутацию.

### 1.1. Не входит в Stage 19 v1

В этот контракт не входят Calendar runtime, Email runtime, фоновые действия,
цепочки действий, планировщик, LLM function/tool calling, произвольные
GitHub/HTTP endpoints, изменение содержимого Git, workflow/deploy/release,
merge/review pull request, настройки репозитория, vault write или новая БД.

Stage 20 — `Personal Agent / Chief of Staff` — остаётся
`PLANNED / NOT STARTED` и не может появиться как автоматическое продолжение
этой стадии.

## 2. Authority matrix

| Источник | Роль в Stage 19 | Может разрешить внешнюю мутацию |
| --- | --- | --- |
| Явный owner `ActionIntentV1` | точное намерение | нет, пока не пройдены следующие gates |
| Stage 16–18 и Cognitive Twin | точная provenance/контекст | нет |
| Advisor/LLM/provider output | непроверенная производная выдача | нет |
| `Prepare` | связывает intent с точной целью и preview | нет |
| Immutable `PreparedExternalActionV1` | фиксирует outgoing semantics | нет |
| owner-visible exact preview | позволяет владельцу увидеть side effect | нет |
| Stage19 confirmation token | доказательство подтверждения именно этого prepare | нет сам по себе |
| `Execute` + fresh revalidation | единственный путь к connector mutation | да, один раз для одного operation |
| Reconcile | read-only установление исхода | нет новой мутации |
| Compensation | новая явная ActionIntent с новой Prepare/Confirm | да, как отдельное действие |

Ни один endpoint не принимает provider prompt, executable command, credential,
arbitrary JSON payload или скрытую делегацию.

## 3. Closed risk vocabulary

Используется только следующий набор строковых значений:

```text
read_only
controlled_write
high_impact
prohibited
```

* `read_only` — connector readiness, exact metadata preflight и
  reconciliation; provider mutation запрещена.
* `controlled_write` — consequential mutation только после exact preview и
  отдельного подтверждения владельца. Все три GitHub Issues v1 mutations имеют
  именно этот риск.
* `high_impact` — известное consequential действие, запрещённое runtime v1;
  например merge PR, workflow dispatch или repository content mutation.
* `prohibited` — никогда не исполняется и не имеет override-кнопки.

Цветовая маркировка интерфейса не меняет политику: даже `controlled_write`
никогда не выполняется автоматически или в фоне.

## 4. Connector и закрытый каталог действий

В Stage 19 v1 существует один connector:

```text
connector: github_issues
connector_policy_id: github-issues-v1
credential_profile_id: github-actions-primary
```

Допустимы только следующие action kinds:

### `github.issue.create`

```json
{
  "action_kind": "github.issue.create",
  "connector": "github_issues",
  "repository": "owner/repository",
  "title": "bounded title",
  "body": "bounded body"
}
```

`title` имеет максимум 256 UTF-8 bytes, `body` — максимум 64 KiB. В outgoing
body добавляется только документированный технический marker. Assignee,
milestone, project, label, reaction и прочее metadata отсутствуют.

### `github.issue.comment`

```json
{
  "action_kind": "github.issue.comment",
  "connector": "github_issues",
  "repository": "owner/repository",
  "issue_number": 123,
  "comment": "bounded comment"
}
```

`issue_number` — точное положительное целое, `comment` — максимум 64 KiB.
Fuzzy lookup, поиск по title/body и выбор «первого совпадения» запрещены.
Locked issue и pull request, представленный через issues endpoint, отклоняются
до мутации.

### `github.issue.set_state`

```json
{
  "action_kind": "github.issue.set_state",
  "connector": "github_issues",
  "repository": "owner/repository",
  "issue_number": 123,
  "desired_state": "open"
}
```

`desired_state` — только `open` или `closed`. Labels, projects, milestones,
lock reason и любой другой update payload не разрешены. Если цель уже имеет
нужное состояние, connector возвращает `already_satisfied` и не отправляет
provider mutation.

Любой другой action kind, connector или поле — `INVALID_ACTION` до сети и до
receipt-store mutation.

## 5. Строгие DTO

### 5.1. `ActionIntentV1`

Wire DTO имеет только следующие поля:

```text
contract_version: "action-intent-v1"
operation_id: bounded non-empty owner operation string, <=256 UTF-8 bytes
action_kind: one of the three catalog values above
connector: "github_issues"
repository: exact configured owner/repository
issue_number: required only for comment/set_state
title: required only for create
body: required only for create
comment: required only for comment
desired_state: required only for set_state, open|closed
provenance: optional typed exact Stage17/18 references
```

`operation_id` не является секретом и не является credential. Он не содержит
команд и не используется как provider idempotency key. В durable receipts
хранится только `operation_id_fingerprint`.

`provenance` может содержать только точные bounded references:

```text
planning_snapshot_id + planning_snapshot_fingerprint
item_id + accepted_item_fingerprint
execution_context_id + execution_context_fingerprint
```

Каждая пара либо отсутствует, либо представлена полностью; произвольные
nested objects, Stage18 state transitions и тексты заметок запрещены.

### 5.2. `PreparedExternalActionV1`

Prepare создаёт immutable DTO, возвращаемый в page memory:

```text
prepared_action_id: UUIDv7
contract_version: "prepared-external-action-v1"
operation_id_fingerprint: sha256 hex
action_kind
risk: controlled_write
connector: github_issues
connector_policy_id: github-issues-v1
credential_profile_id: github-actions-primary
exact_target_identity
preflight_fingerprint: sha256 hex
semantic_payload
payload_fingerprint: sha256 hex
preview
preview_fingerprint: sha256 hex
prepared_at: RFC3339 UTC
expires_at: RFC3339 UTC, <=5 minutes after prepared_at
reversibility
provenance: optional exact references
```

`semantic_payload` содержит только action-specific fields и marker, если он
нужен. Секреты, Authorization header, cookie, raw provider response и private
vault body в DTO отсутствуют.

`exact_target_identity` для repository содержит configured `owner/repository`,
remote numeric `repository_id` и bounded `repository_node_id`. Для existing
issue дополнительно содержит exact `issue_number`, remote numeric `issue_id`,
`issue_node_id`, current `state` и `locked`; наличие `pull_request` приводит к
отказу. Remote display title не является identity.

### 5.3. `ExternalActionReceiptV1`

Receipt — bounded operational evidence:

```text
receipt_id: UUIDv7
receipt_kind: action | reconciliation | compensation
operation_id_fingerprint
prepared_action_id
intent_fingerprint
action_kind
risk
connector_policy_id
credential_profile_id
target_safe_identity
payload_fingerprint
state
attempt_started_at: optional RFC3339 UTC
sent_at: optional RFC3339 UTC
finished_at: optional RFC3339 UTC
remote_safe_identity: optional
remote_url: optional validated https://github.com/... URL
safe_error_code: optional closed error code
parent_receipt_id: optional UUIDv7
reconciliation_receipt_id: optional UUIDv7
compensation_receipt_id: optional UUIDv7
```

Durable receipt не содержит operation id plaintext, title/body/comment,
credential, Authorization, cookies, raw response, environment, absolute path,
vault content или exception text. Хэши используются вместо outgoing content.

Receipt state vocabulary:

```text
already_satisfied
execution_started
executed
failed_before_send
failed_confirmed_no_mutation
outcome_uncertain
reconciled_executed
reconciled_not_executed
reconciliation_ambiguous
compensation_prepared
```

`execution_started` — внутренний durable marker перед mutation; если процесс
упал после него, следующий запрос видит `outcome_uncertain` и не повторяет
mutation.

## 6. Canonical encoding, IDs и fingerprints

Для всех identity/fingerprint DTO используется один canonical JSON encoder:

```text
UTF-8
ensure_ascii=false
sort_keys=true
separators=(",", ":")
allow_nan=false
```

Каждый fingerprint — lowercase SHA-256 hex от этих байтов. Для intent
fingerprint `operation_id` исключается; для operation identity вычисляется
отдельный `sha256(operation_id.encode("utf-8"))`. UUID action/receipt — UUIDv7.
Изменение любого поля target, payload, policy, risk, expiry или provenance
создаёт другой fingerprint.

## 7. Target policy и allowlist

`SECOND_BRAIN_ACTION_GITHUB_REPOSITORIES` — bounded список, разделённый
запятыми или переводами строк, максимум 32 элемента. Каждый элемент обязан
соответствовать консервативному ASCII syntax:

```text
owner/repository
```

Owner и repository начинаются с ASCII alphanumeric; разрешены только GitHub
safe characters (`A-Z a-z 0-9 . _ -`), длины ограничены, `.git`, slash
fragments, whitespace, wildcard и regex запрещены. Пустые entries и duplicate
entries после ASCII `casefold()` отклоняются. Matching case-insensitive по
правилу GitHub, но exact canonical configured spelling сохраняется в preview.

Target не может быть выведен из checkout, Git remote, текущего проекта,
Stage17 text, LLM output или browser URL. Repository outside allowlist даёт
`TARGET_NOT_ALLOWED` до GitHub network и до mutation.

## 8. Отдельный credential boundary

Production config использует только server-side environment:

```text
SECOND_BRAIN_ACTION_GITHUB_ENABLED=false|true
SECOND_BRAIN_ACTION_GITHUB_TOKEN=<secret, server only>
SECOND_BRAIN_ACTION_GITHUB_REPOSITORIES=owner/repository[,owner/other]
```

Секрет читается только adapter/config boundary, не передаётся в browser,
session, localStorage, sessionStorage, IndexedDB, Cache Storage, receipt или
лог. При `enabled=false` connector disabled и token не используется. При
неполной/невалидной конфигурации приложение продолжает запуск, connector
имеет `disabled / credential_unavailable`, а mutation routes fail closed.

Рекомендуемый credential — GitHub fine-grained personal access token (или
отдельный GitHub App installation/user token при будущем отдельном contract),
ограниченный exact repositories. Для текущего REST catalog минимальная
документированная permission model — repository `Issues: read` для bounded
preflight/reconciliation и `Issues: write` для create/comment/state mutation;
`Metadata: read` нужен для repository metadata preflight. Не запрашиваются
`Contents`, `Workflows`, `Administration`, `Actions`, `Deployments`,
`Environments`, `Secrets`, `Variables`, `Webhooks`, collaborator/admin или
classic broad `repo` scopes. Актуальная матрица GitHub permissions:
[`Permissions required for fine-grained personal access tokens`](https://docs.github.com/en/rest/authentication/permissions-required-for-fine-grained-personal-access-tokens),
[`REST API endpoints for issues`](https://docs.github.com/en/rest/issues/issues),
[`REST API endpoints for repositories`](https://docs.github.com/en/rest/repos/repos).

Токен для текущего Web login OAuth не используется. OAuth code обменивается на
transient token только для `GET /user`, проверяется numeric owner ID, затем
токен отбрасывается; local signed session содержит только owner ID и expiry.
Stage 19 не меняет OAuth scopes, не сохраняет login token и не использует
deploy/checkout/GitHub Actions runtime credentials.

## 9. Fixed-host GitHub transport

Production adapter — узкий application-owned stdlib HTTP client, не SDK и не
generic proxy. Единственная origin:

```text
https://api.github.com
```

Вызовы строятся только из закрытого endpoint map:

```text
GET  /repos/{owner}/{repo}
GET  /repos/{owner}/{repo}/issues/{number}
GET  /repos/{owner}/{repo}/issues?state=all&per_page=100&page=1
GET  /repos/{owner}/{repo}/issues/{number}/comments?per_page=100&page=1
POST /repos/{owner}/{repo}/issues
POST /repos/{owner}/{repo}/issues/{number}/comments
PATCH /repos/{owner}/{repo}/issues/{number}
```

Path segments проходят только adapter-owned validation/percent-encoding; URL
из caller не принимается. HTTPS, TLS verification и fixed User-Agent
`Second-Brain-Action-Gateway-v1` обязательны. Добавляется
`Accept: application/vnd.github+json` и current
`X-GitHub-Api-Version` header. Redirects отклоняются. Proxy из process
environment не наследуется (`ProxyHandler({})`). Caller не может инъектировать
headers, Authorization, base URL, scheme или path. Body, response и timeout
ограничены; JSON decode и status mapping безопасны; raw provider body не
попадает в error.

Mutation request не повторяется автоматически. Ошибка после возможной отправки
классифицируется как `outcome_uncertain`, а не как повод для retry.

## 10. Prepare, preview и confirmation

`Prepare` валидирует strict intent, connector readiness, allowlist и target,
затем делает только bounded GET preflight. Он никогда не создаёт issue, не
пишет comment и не меняет state.

Exact owner preview на русском показывает:

```text
GitHub / тип действия
точный owner/repository
точный issue number и remote identity, если есть
точный outgoing title/body/comment semantics, включая marker
current state -> desired state, если это state action
risk = controlled_write
внешний побочный эффект: будет после отдельного подтверждения
reversibility/compensation: явно или «не поддерживается»
credential profile ID, но не token
prepared expiry
remote target state, требуемое для Execute
```

Фраза «GitHub dry-run» не используется: GitHub не выполняет mutation dry-run.
В UI используется «Предпросмотр действия».

Confirmation token — отдельный HMAC purpose `action-gateway-confirm-v1`, TTL
максимум 5 минут. Он связывает prepared action fingerprint, action kind,
target/payload fingerprints, policy/risk identity, expiry и purpose/version.
В token нет plaintext body и credential. Token не является credential; generic
Save/Stage14/Stage15 token не принимается. Tamper, expiry, cross-purpose,
cross-action и replay отклоняются. HMAC key создаётся process-local при
startup; после restart незавершённое prepare честно требует нового Prepare.

## 11. Execute, revalidation и at-most-once

Перед Execute application:

1. строго разбирает immutable prepared DTO;
2. проверяет confirmation purpose/signature/expiry;
3. проверяет exact prepared/payload/target fingerprints;
4. убеждается, что connector всё ещё enabled и allowlist не изменился;
5. загружает credential заново на server-side;
6. читает и связывает remote repository/issue identity и safety-relevant state;
7. под lock проверяет operation lifecycle;
8. для mutation пишет `execution_started`, затем делает не более одного
   provider mutation attempt;
9. пишет окончательный receipt после validated response.

Same `operation_id` + same canonical intent возвращает существующий lifecycle
и receipt. Same `operation_id` + different intent даёт `ACTION_CONFLICT`.
Операции не дедуплицируются по title, body, timestamp или близости issue
number. `failed_before_send`, `failed_confirmed_no_mutation` и
`outcome_uncertain` не разрешают повторный Execute с тем же operation.

Если target drift меняет repository/issue ID, issue number, locked flag,
current state или другой safety-relevant bound field, Execute закрывается
`TARGET_CHANGED` и требует нового Prepare. Для already-satisfied state mutation
provider write не отправляется.

### 11.1. Сетевые исходы

```text
failed_before_send            локальная ошибка до provider request
failed_confirmed_no_mutation  provider явно отверг request
outcome_uncertain              request мог достигнуть provider, response потерян
executed                       validated successful provider response
```

`outcome_uncertain` никогда не показывает кнопку «Повторить» и никогда не
запускает скрытый retry; доступно только explicit `Проверить результат`.

## 12. Marker и deterministic reconciliation

Для create/comment outgoing body получает один application-owned marker:

```html
<!-- second-brain-action:<prepared-action-id> -->
```

Marker не содержит secret, vault/private context и provider prompt. Он
стабилен для одного immutable action, виден в technical preview, включён в
payload fingerprint и добавляется ровно один раз в конец body/comment.

Reconcile — read-only и использует только exact marker:

* create: exact allowlisted repository, bounded first page of recent/all issues,
  exact marker occurrence; ровно один match = `reconciled_executed`, ноль =
  `reconciled_not_executed`, более одного или противоречивый result =
  `reconciliation_ambiguous`;
* comment: exact repository + exact issue identity, bounded first page of
  comments, exact marker occurrence; те же cardinality rules;
* set_state: exact repository + exact issue identity; desired state означает
  `reconciled_executed`, исходное bound state — `reconciled_not_executed`, иной
  drift — `reconciliation_ambiguous`.

Нет fuzzy title/body matching, unbounded pagination или blind retry. Если exact
reconciliation не даёт безопасного ответа, действие остаётся uncertain.

## 13. Compensation

Внешние side effects не transactional; интерфейс говорит «компенсация», а не
«rollback».

* executed `github.issue.set_state` может предложить inverse `open <-> closed`;
  inverse требует новой ActionIntent, нового operation ID, Prepare, exact
  preview и fresh Confirm;
* executed create может предложить `compensation: close created issue`,
  сохраняя историю; это новый `github.issue.set_state` receipt с точной
  созданной issue identity;
* comment deletion/edit в v1 не поддерживается;
* compensation никогда не запускается автоматически и получает отдельный
  receipt, связанный с `parent_receipt_id`.

Compensation target должен пройти fresh identity/state validation. Если он уже
изменён, подготовка закрывается без provider mutation.

## 14. Operational receipt store

Stage19 state хранится отдельно от vault, release directories, checkout и
worktrees:

```text
<explicit env-file parent>/prospective-audit/action-gateway/
  receipts.jsonl
  manifest.json
  .store.lock
```

Root path строится только из явного env-file и проверяется на абсолютность,
symlink/path escape, containment в repository/release/vault и owner-only
permissions. На POSIX root имеет `0700`, payload/lock — `0600`; на Windows
используются существующие repository-safe проверки.

Store использует bounded append-only JSONL, strict closed record envelope,
monotonic sequence, previous-record digest, manifest с record count/last digest,
advisory lock, temp+fsync atomic manifest, append+fsync, directory fsync,
read-back verification и fail-closed recovery. Maximum — 32768 records и
256 KiB на envelope. Torn append, reorder, manifest mismatch, digest tamper,
symlink, permission mismatch, lock failure и capacity overflow не
«восстанавливаются» вслепую: store становится unavailable/corrupt.

Receipt store — operational audit, не canonical autobiographical truth, не
Stage18 execution evidence, не Goal Progress, не Cognitive Twin training и не
Personal Memory/Decision Journal. Stage19 не пишет vault и не создаёт новую
NoteType/global schema version/DB/framework.

## 15. Web/API и browser boundary

Owner-only action surface использует русское название `Действия` и отдельные
purpose-separated routes:

```text
POST /api/action-gateway/status
POST /api/action-gateway/prepare
POST /api/action-gateway/execute
POST /api/action-gateway/reconcile
POST /api/action-gateway/compensation/prepare
POST /api/action-gateway/history
```

Каждый request требует existing owner session, trusted Host, same-origin
Origin, `X-Second-Brain-Request: action-gateway-v1`, POST, JSON content type,
bounded raw body и strict `extra=forbid`. Anonymous rejection происходит до
credential load, receipt-store access и GitHub network. Ответы имеют
`Cache-Control: no-store, private`, существующие CSP/nosniff/referrer/frame
headers и не используют permissive CORS.

`status` возвращает только safe readiness (`готов`, `не настроен`), exact
allowlisted repository names и action catalog. Token/profile secret и raw
provider data никогда не возвращаются.

Prepared/confirmation/private payloads живут только в page memory. Запрещены
localStorage, sessionStorage, IndexedDB, Cache Storage authority,
service-worker caching private action APIs, background sync, offline replay и
console logging body/token. PWA offline shell не получает action API cache.

Safe error codes включают:

```text
INVALID_ACTION
CONNECTOR_DISABLED
TARGET_NOT_ALLOWED
TARGET_NOT_FOUND
TARGET_CHANGED
CONFIRMATION_INVALID
CONFIRMATION_EXPIRED
ACTION_ALREADY_EXECUTED
ACTION_CONFLICT
PROVIDER_REJECTED
OUTCOME_UNCERTAIN
RECONCILIATION_NOT_FOUND
RECONCILIATION_AMBIGUOUS
STORE_UNAVAILABLE
```

Owner UI показывает bounded русское сообщение без token, Authorization, raw
GitHub body, request body, environment, absolute path, exception repr,
traceback, cookie или store root.

## 16. Future connector boundary

Calendar и Email остаются `DEFERRED`. Будущему adapter потребуется отдельный
credential profile, exact account/resource allowlist, fixed provider origin,
typed closed action catalog, read-only preflight, per-action preview/confirm,
at-most-once/idempotency semantics, provider-specific reconciliation и
compensation contract. Google OAuth, Gmail token storage и Calendar credential
machinery в Stage19 v1 не добавляются.

## 17. Alternatives register

| Решение | Отклонённая альтернатива | Причина |
| --- | --- | --- |
| Один GitHub Issues adapter | generic HTTP/REST/GraphQL executor | ломает closed catalog и SSRF/authority boundary |
| Separate action token | reuse login OAuth/deploy token | смешивает identity и mutation authority |
| Fixed `api.github.com` | caller base URL/proxy/inherited proxy | SSRF, redirect и header injection risk |
| Marker reconciliation | fuzzy title/body/time search | не доказывает identity исхода |
| `outcome_uncertain` + reconcile | automatic mutation retry | может создать duplicate side effect |
| Append-only JSONL | новая DB/framework | scope expansion и новая operational dependency |
| page-memory prepared state | browser storage/offline replay | credential/authority persistence risk |
| explicit compensation | magical rollback | external systems не transactional |
| no Stage18 handoff | action -> complete/progress automation | нарушает Stage18 authority и semantics |

## 18. Implementation and delivery map

| Phase | Scope | Required result |
| --- | --- | --- |
| 19.0 | этот contract + roadmap design gate | merged contract, exact post-merge CI, fresh main |
| 19.1 | provider-neutral core/store/confirmation | fake connector tests, no provider network |
| 19.2 | GitHub Issues adapter | fixed host, config, fake HTTP, exact catalog |
| 19.3 | orchestration | prepare/confirm/execute/reconcile/compensation |
| 19.4 | owner Web/API/UI | private routes, Russian UX, history/readiness |
| 19.5 | security/privacy/integration/E2E | boundary matrix, PWA/responsive/accessibility |
| 19.6 | release/closeout | final ledger, CI/deploy/non-mutating smoke |

Каждая фаза идёт отдельным PR из свежего isolated worktree. Следующая фаза не
начинается до merge предыдущей, exact-head required checks, post-merge CI,
обычного automatic deploy для production-facing изменений и fresh fetch
текущего `origin/main`. Только финальный closeout PR использует `Closes #366`;
промежуточные PR используют `Refs #366`.

## 19. Design-gate status (historical)

На design gate подтверждено:

```text
Cognitive Twin v3 = COMPLETE
Second Brain v4 = IN PROGRESS
Stage 16 = COMPLETE
Stage 17 = COMPLETE
Stage 18 = COMPLETE
Stage 19 = IN PROGRESS
Stage 20 = PLANNED / NOT STARTED
```

На design gate runtime implementation 19.1–19.6 не считалась начатой до
последовательного delivery. Этот contract не меняет Stage16–18 semantics,
second-brain-vault или реальные vault data.

## 20. Factual delivery status

После последовательного delivery Phase 19.0–19.6 фактический статус Stage 19:

```text
Cognitive Twin v3 = COMPLETE
Second Brain v4 = IN PROGRESS
Stage 16 = COMPLETE
Stage 17 = COMPLETE
Stage 18 = COMPLETE
Stage 19 = COMPLETE
Stage 20 = PLANNED / NOT STARTED
```

Нормативный contract, provider-neutral core, append-only receipt/audit store,
GitHub Issues connector, prepare/confirm/execute orchestration,
reconciliation, compensation boundary, owner-only Web/API/UI и security/E2E
gate доставлены и проверены. Production connector остаётся `disabled`, пока
owner не provision-ит отдельный GitHub action credential; это не расширяет
login OAuth и не блокирует безопасный runtime. Фактические PR, merge SHA,
exact-head CI, post-merge CI и deploy собраны в
[`stage19-delivery-ledger.md`](./stage19-delivery-ledger.md).
