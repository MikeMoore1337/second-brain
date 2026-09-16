# Execution & Feedback v1 — контракт Stage 18

**Статус:** NORMATIVE CONTRACT / STAGE 18 IN PROGRESS  
**Контракт:** `execution-feedback-v1`  
**Владелец:** только owner, через приватные web/API поверхности  
**Дата фиксации:** 2026-09-16

Этот документ является нормативным контрактом Stage 18. Он описывает исполняемый
слой поверх принятого персонального плана Stage 17: явные события выполнения,
идемпотентность, коррекции, проекции состояния, агрегированный feedback и
описательную калибровку. Контракт не изменяет смысл, формат или политику Stage 17.

## 1. Границы и инварианты

Stage 18 принимает только тот план, который уже был принят owner в Stage 17.
План остаётся immutable portfolio snapshot. Execution layer не выбирает новые
задачи, не меняет `PlanningPlanV1`, не меняет цели, стратегию, commitments,
action bindings или capacity policy и не создаёт новый план.

В терминах Stage 18 имя `PlanningPortfolioSnapshotV1` означает существующий
`second_brain.application.personal_planning_store.PlanningPlanV1`. Это alias
внутри Stage 18, а не новый DTO и не изменение Stage 17.

Источники истины:

| Данные | Источник | Что разрешено Stage 18 |
| --- | --- | --- |
| принятый portfolio snapshot | Stage 17 `PersonalPlanningOperationalStore` | читать exact snapshot и выбранные items |
| goal refs | текущий Goals source + embedded accepted refs | проверять exact identity/fingerprint |
| action refs и Stage16 provenance | текущий Strategy/Stage16 source + embedded accepted refs | проверять exact identity/fingerprint |
| execution events | собственный append-only operational store | добавлять валидные события и void-correction |
| feedback/calibration | детерминированные projections из snapshot/event history | считать descriptive данные |
| vault/Git/LLM/provider | не являются execution storage | не трогать |

Ни одна новая запись не считается основанной на названии, поиске по тексту,
последнем похожем item, fuzzy match или неявном «текущем» snapshot. Сопоставление
всегда использует exact `plan_id`, `plan_fingerprint`, `item_id` и
`accepted_item_fingerprint`.

### 1.1. Policy identity

```text
policy_id = stage18-execution-feedback-v1
policy_fingerprint = 7a3af67e03089023c9d29f956f74111abf446b8cf71fef7409255ab97e34d3dc
```

Fingerprint вычисляется как SHA-256 UTF-8 canonical JSON без пробелов, с
отсортированными ключами и таким exact payload:

```json
{"actual_effort_max_minutes":1440,"calibration_max_items":1024,"calibration_max_plans":32,"event_future_skew_seconds":300,"event_kinds":["start","pause","resume","block","unblock","complete","abandon","void"],"event_note_max_bytes":2048,"executable_item_kinds":["commitment","next_action"],"max_events_per_item":256,"max_store_records":32768,"min_event_time":"2000-01-01T00:00:00Z","reason_code_max":3,"stage17_policy_id":"stage17-personal-planning-v1","stage18_contract_id":"execution-feedback-v1","stale_start":"fail_closed","terminal_dispositions":["as_planned","with_changes","partial","unknown"],"version":"1"}
```

## 2. Exact binding к принятому плану

Каждое событие содержит и при записи проверяет:

- `plan_id`, `plan_fingerprint`, `plan_revision`, `source_pack_fingerprint`,
  `proposal_fingerprint`, `policy_id`, `policy_fingerprint`;
- `item_id`, `item_kind`, полный immutable `accepted_item` и его
  `accepted_item_fingerprint`;
- exact `goal_refs` и `action_refs` из accepted item;
- исходное окно, timezone, capacity и fixed windows через embedded accepted
  item/plan identity, когда они нужны для projection.

`accepted_item_fingerprint` — SHA-256 canonical JSON `PlanningItemV1.as_dict()`.
Исполняемыми являются только `commitment` и `next_action`. `project`,
`milestone` и `hold` отклоняются с кодом `non_executable_item` даже если кто-то
попытался отправить их вручную.

Новая цепочка выполнения разрешена только для selected item текущего exact
accepted plan. Старые/superseded snapshots сохраняются для истории и
calibration, но не могут получить новый `start`.

## 3. DTO события

`ExecutionEventV1` — strict immutable DTO. Все unknown keys запрещены. JSON
сериализуется канонически и содержит следующие поля:

```text
execution_event_version: "1"
event_id: UUIDv7
event_type: start | pause | resume | block | unblock | complete | abandon | void
operation_id_fingerprint: sha256, без исходного operation id
occurred_at: RFC3339 с offset, после проверки нормализуется в UTC с суффиксом Z
planning_snapshot_id: UUIDv7
planning_snapshot_fingerprint: sha256
planning_plan_revision: положительное целое
planning_source_pack_fingerprint: sha256
planning_proposal_fingerprint: sha256
planning_policy_id: stage17 policy id
planning_policy_fingerprint: stage17 policy fingerprint
item_id: bounded opaque id
accepted_item_fingerprint: sha256
item_kind: commitment | next_action
accepted_item: полный canonical accepted PlanningItemV1
goal_refs: exact tuple accepted goal refs
action_refs: exact tuple accepted action bindings
actual_effort_minutes: integer 0..1440 или null
effort_precision: exact | unknown
actual_result_note: безопасный текст до 2048 UTF-8 bytes
reason_codes: 0..3 neutral codes
deviation_codes: 0..3 neutral codes
result_disposition: as_planned | with_changes | partial | unknown | null
void_target_event_id: UUIDv7 | null
void_target_event_fingerprint: sha256 | null
correction_reason: bounded safe text | null
```

`operation_id_fingerprint` вычисляется от caller-provided idempotency key. Сам
ключ никогда не сохраняется. `event_fingerprint` — SHA-256 canonical JSON DTO
без поля `event_fingerprint`; fingerprint envelope хранит отдельно.

`occurred_at` обязателен, должен иметь явный offset (`Z` допустим), не может
быть раньше `2000-01-01T00:00:00Z` или позже времени получения запроса более чем
на 300 секунд. Нельзя подставлять серверное время вместо пропущенного или
невалидного времени. В одной effective chain время не убывает; коррекция не
может иметь время раньше последнего effective event.

Фактическое усилие — отдельное owner-entered поле terminal event:
`exact` требует явного целого от 0 до 1440; `unknown` передаётся как `null` и
не трактуется как ноль. Время между событиями, число кликов и passive timer не
являются фактическим усилием.

### 3.1. Neutral code vocabulary

Разрешены только: `dependency`, `missing_information`, `capacity`,
`priority_change`, `scope_change`, `estimate_mismatch`, `technical_problem`,
`external_wait`, `context_change`, `other`, `unknown`. `block` требует минимум
один `reason_code`; `abandon` требует причину (код или note). `complete` может
содержать deviation/disposition. Коды не интерпретируются как reward, score
или recommendation.

## 4. Lifecycle и stale protection

Effective state вычисляется replay-ом событий после удаления только явно
voided events. Допустимые переходы:

```text
not_started --start--> in_progress
in_progress --pause--> paused
paused --resume--> in_progress
in_progress|paused --block--> blocked
blocked --unblock--> in_progress
not_started|in_progress|paused|blocked --complete--> completed
not_started|in_progress|paused|blocked --abandon--> abandoned
```

`complete`/`abandon` из `not_started` разрешены только как явный backfill: это
не создаёт fictional `start`. После terminal state никакой lifecycle event,
включая повторный terminal event, не принимается. `void` — correction event,
а не переход состояния. Любой double start, pause до start, resume не из
paused, block из `not_started`, unblock не из blocked или event для
non-executable item отклоняется fail-closed.

Перед новым `start` сервис проверяет, что snapshot одновременно является
текущим accepted plan и что embedded goal/action refs совпадают с текущими
exact sources (id, fingerprint, accepted action payload и Stage16 provenance).
Если snapshot superseded, source stale или source unavailable — новый start
отклоняется и не пишется. Уже начатая цепочка может быть завершена, paused,
blocked, resumed или abandoned по её exact identity; это не является
подтверждением свежести и не разрешает новый start. Старый `not_started` item
нельзя завершить после supersede.

## 5. Append-only correction

Void correction содержит exact target `event_id` и
`void_target_event_fingerprint`, непустой `correction_reason` и собственную
идемпотентность. Исходный event остаётся в журнале и auditable, но исключается
из effective replay. Перед append сервис строит полную effective chain с
учётом новой коррекции и принимает её только если lifecycle снова валиден.
Нельзя менять, удалять, truncat-ить или автоматически чинить старые записи.
Нельзя void-ить void event, отсутствующий event или target с несовпадающим
fingerprint.

## 6. Operational store и recovery

Хранилище находится вне репозитория, worktree, release и vault:

```text
<env-file-parent>/prospective-audit/execution-feedback/
  events.jsonl
  manifest.json
  .store.lock
```

`<env-file-parent>` — родитель production env file по существующему deployment
contract; нового env key нет. Используется существующий owner-only `_StoreLock`.
На POSIX root имеет mode `0700`, payload и lock — `0600`; на Windows проверка
mode no-op. Каждый JSONL record — LF, canonical JSON, не более 256 KiB; всего
не более 32768 records. Manifest version 1 содержит `sequence`, `record_count`,
`last_record_digest` и Stage18 policy identity. Запись: lock → reread и
integrity verification → validate → append+flush+fsync → atomic manifest
replace+fsync → reread verification. При конфликте или повреждении store
останавливаемся; truncate/delete/repair и silent recovery запрещены.

Operation idempotency: одинаковый operation fingerprint и exact intent
возвращает ранее записанное событие; тот же fingerprint с иным intent даёт
`idempotency_conflict`; другая операция не может обойти lock. Результат
ошибочного append никогда не маскируется как success.

## 7. Projections и feedback

`ExecutionItemStateV1` содержит exact identity snapshot/item, effective state,
first start, last event, terminal event, block reason, terminal effort and
precision, result note, disposition, reason/deviation codes, event count,
correction count, source status и caveats. History отдаётся в исходном
auditable порядке; voided event помечается и не участвует в effective state.

`ExecutionFeedbackReportV1` содержит:

- selected/executable/started/completed/abandoned/blocked counts;
- coverage: terminal exact effort, terminal unknown effort, missing terminal;
- planned-vs-actual rows per item and totals;
- `actual_minus_planned_minutes` для comparable rows;
- window relation (`within`, `before`, `after`, `no_window`, `unknown`),
  incomplete-window caveats;
- reason/deviation counts and data-quality caveats.

Плановая величина берётся только из accepted item `effort_minutes` для
executable selected item. Для comparable row одновременно нужны exact planned
effort и terminal `effort_precision=exact`; `unknown` не участвует в сумме.

```text
delta_i = actual_effort_i - planned_effort_i
planned_total = sum(planned_effort_i for comparable i)
actual_total = sum(actual_effort_i for comparable i)
delta_total = actual_total - planned_total
```

Нет ratio, productivity score, reward, punishment, ranking или automatic
recommendation. Half-open target window — `[start_local, end_local)`, с явным
timezone. Event outside window не переписывается и получает relation/caveat.

## 8. Calibration

`ExecutionCalibrationV1` строится только из exact historical
`snapshot_id + snapshot_fingerprint`; максимум 32 plans и 1024 selected
executable items. Нет fuzzy matching, latest substitution, deduplication по
названию или silent skipping invalid history. Для каждой plan/item возвращаются
counts, planned/actual/delta numerators, exact/unknown/missing denominators,
coverage, window relation и deviation/reason counts. При invalid history
calibration fail-closed или явно помечает bounded caveat; не удаляет данные.

Calibration descriptive only. Она не мутирует Stage17 plan, Goals, Strategy,
Stage16 source, policy, model, prompt, provider configuration или vault.

## 9. Private API

Все routes owner-only через существующий OAuth/session boundary, принимают
только правильные Host/Origin и purpose header
`X-Second-Brain-Request: execution-feedback-v1`, strict JSON с
`extra=forbid`, bounded body и `Cache-Control: no-store, private`:

```text
POST /api/execution-feedback/state
POST /api/execution-feedback/event
POST /api/execution-feedback/feedback
POST /api/execution-feedback/calibration
POST /api/execution-feedback/correction
```

State/feedback/calibration читают operational store и exact historical plans;
event/correction — единственные mutating operations. Ошибки используют
стабильные безопасные codes без secrets, filesystem paths, tokens или raw
exception text. Anonymous requests завершаются до service/store work.

## 10. Web surface

В semantic navigation добавляется один additive tool
`execution-feedback`, с русским label «Выполнение и обратная связь» и
recognizable play/action icon. Поверхность показывает текущий accepted plan,
selected executable items, planned effort, explicit window, current state,
visible «Сейчас», block/complete/abandon actions, actual effort precision,
reason/deviation codes, auditable history and provenance disclosure. Нет
запроса при mount: загрузка начинается явным действием owner.

Forms не имеют hidden timestamp fallback: поле времени либо заполнено owner
явным RFC3339 offset (кнопка «Сейчас» лишь видимо заполняет поле), либо запрос
отклоняется. Mobile widths 320/360/390/430/768/1024/1440 проверяются; primary,
secondary и destructive controls имеют keyboard-visible focus, минимум 44px,
не зависят от hover, не создают горизонтальный overflow. Нет localStorage,
sessionStorage, IndexedDB, Cache Storage, client-side private persistence,
analytics или private console logging. `/api/*` остаётся network-only в PWA.

Статусы и ошибки показываются по-русски; technical identifiers допустимы только
как раскрываемая provenance-информация с русским пояснением.

## 11. Delivery map

| Фаза | Содержание | Gate |
| --- | --- | --- |
| 18.0 | этот contract, roadmap, policy/fingerprint, design gate | docs + full repository gate |
| 18.1 | core DTO, exact binding, lifecycle validator, neutral codes | focused adversarial tests + full gate |
| 18.2 | append-only store, manifest/hash-chain, idempotency/concurrency/recovery | store/security tests + full gate |
| 18.3 | projections, feedback formulas, calibration | deterministic property/boundary tests + full gate |
| 18.4 | private API, auth/origin/host/body/error boundary | API security tests + frontend gate |
| 18.5 | Russian responsive UI, explicit actions, PWA rules and QA | Vitest/typecheck/build/PWA/Playwright/axe |
| 18.6 | exact-head delivery, production deploy and non-mutating smoke | CI/deploy/healthz/private boundary evidence |

Stage 18 завершён только после 18.6. Stage 19 не начинается автоматически.

## 12. Alternatives and rejected scope

| Вариант | Решение | Причина |
| --- | --- | --- |
| изменить Stage17 accepted plan во время выполнения | reject | ломает immutable owner-reviewed snapshot |
| хранить runtime events в vault или Git | reject | private operational data и secret boundary |
| SQLite/новый DB framework | reject | лишняя поверхность; JSONL достаточно для bounded v1 |
| passive timer / event timestamp как effort | reject | неявная метрика и плохая доказуемость |
| reward/progress/productivity score | reject | behavioral pressure и ложная точность |
| автоматический replan или auto-adjust estimates | reject | Stage18 descriptive, не decision/model mutation |
| provider/LLM/research call | reject | execution feedback не требует внешнего провайдера |
| local browser persistence | reject | private data остаётся в owner-protected API/store |

## 13. Environment and final status contract

Проверяется actual diff и deployment contract, а не наличие уже существующего
env value. Для Stage 18 expected report: `env change required: no`.
Production uses the existing env-file parent to derive the new sibling store;
release must not add, rotate or rename a production environment key. Если
deployment evidence обнаружит обратное, release блокируется до отдельного
owner-approved contract update.

Результат каждой фазы обязан содержать exact commit/PR/merge SHA, fresh
exact-head checks, full/focused test commands, frontend and deploy evidence,
store path impact, vault/Git/provider/no-action assertions and any blocker.
