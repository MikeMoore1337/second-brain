# Personal Planning v1 — нормативный контракт Stage 17

Статус: **DESIGN GATE / NORMATIVE CONTRACT / STAGE 17 IN PROGRESS**.

Контракт добавляет к Second Brain v4 bounded-слой личного планирования и
явных commitments. Он начинается только от exact owner-reviewed состояния
Stage 16 и не меняет semantics Stage 1–16, `second-brain-vault` или
`second-brain-vault`-совместимые canonical records.

## 1. Назначение и граница

Stage 17 отвечает на вопрос:

> Для каких явно выбранных целей и принятых стратегий владелец готов принять
> реалистичный bounded план с понятной ёмкостью и ограничениями сейчас?

Цикл Stage 17:

```text
accepted Stage16 Strategy Snapshots
 + explicitly selected Goals
 + local planning horizon/timezone
 + explicit capacity and unavailable windows
 + owner constraints/context
 -> provider-free Planning Context Pack
 -> exact provider-visible preview
 -> explicit Generate plan
 -> bounded Planning Proposal
 -> owner edit/select/reorder
 -> explicit accepted Planning Portfolio Snapshot
```

Принятый план означает только owner-reviewed operational intent. Он не
доказывает, что работа начата, выполнена, успешна, просрочена, заблокирована
или вообще произошла. Execution feedback относится к отдельному Stage 18.

Stage 17 не создаёт календарь, task manager, внешние события, сообщения,
GitHub mutations, shell/browser commands, агента, autopilot, таймер,
completion evidence или model-training evidence. Acceptance не меняет Goal,
Progress, Growth, Strategy, Cognitive Twin, Stage 15 profile или provider
weights и не является записью в `second-brain-vault`.

## 2. Authority matrix

| Слой | Stage 17 пример | Authority |
| --- | --- | --- |
| `canonical` | exact Goal и его `GrowthGoalIdentityV1` | текущий source contract Goal |
| `operational` | accepted Stage16 snapshot; accepted Stage17 snapshot | versioned owner state вне vault |
| `derived` | Context Pack, preview, Planning Proposal, capacity totals | deterministic exact bindings; пересобирается |
| `provider_output` | bounded proposed items/order/effort/windows | untrusted derived output до owner review |

Provider не получает authority над Goal identity, priority, dates, capacity,
dependencies или accepted order. Provider-suggested order и estimates всегда
остаются предложением.

## 3. Policy и canonical encoding

| Поле | Значение |
| --- | --- |
| `contract_id` | `personal-planning-v1` |
| `contract_version` | `1` |
| `policy_id` | `stage17-personal-planning-v1` |
| hash | SHA-256 canonical UTF-8 JSON без пробелов, `ensure_ascii=false` |
| `policy_fingerprint` | `bb0c2e9ff39f9a2a4298faea703f38f57dedb72eff38cf13aee8c1b9ac7588ac` |

Policy fingerprint считается по exact bytes:

```json
{"contract_id":"personal-planning-v1","contract_version":"1","item_kinds":["project","milestone","commitment","next_action","hold"],"result_states":["proposal","insufficient_strategy","stale_strategy","source_changed","portfolio_conflict","capacity_missing","capacity_conflict","planning_context_insufficient","not_comparable","provider_unavailable","provider_abstained","hold_current_plan"],"max_goals":8,"max_horizon_local_days":31,"provider_policy_id":"stage17-personal-planning-v1","source":"accepted-stage16-strategy-only"}
```

Все Stage 17 DTO immutable после строгой нормализации. Unknown fields,
duplicate JSON keys, control characters, non-NFC text, NaN, fuzzy references и
unbounded collections отклоняются.

## 4. Exact portfolio identity

Портфолио — ordered tuple из **1–8** `PlanningGoalBindingV1`. Каждый binding
содержит:

```text
goal_source_uuid
goal_identity_fingerprint        # GrowthGoalIdentityV1 exact hash
goal_text_projection             # bounded current projection
strategy_snapshot_id
strategy_snapshot_fingerprint
stage16_policy_id
stage16_policy_fingerprint
selected_reviewed_action_refs    # one or more exact actions
```

Selection полностью задаётся владельцем. Нельзя автоматически включать все
Goals, выбирать по recency/progress/vault order/notes/behavior/provider или
заменять Goal с тем же текстом. Порядок портфолио — owner input; scalar utility,
urgency, productivity, motivation, discipline и learned weights запрещены.

Для каждой Goal допускается только exact current accepted Stage16
`StrategySnapshotV1` и его exact `ReviewedActionV1`. Snapshot proposal,
rejected proposal, raw Advisor response, arbitrary note и `latest` fallback не
являются planning authority.

`reviewed_action_fingerprint` вычисляется по canonical `ReviewedActionV1`
payload. Весь action ref повторяет UUID Goal, Goal fingerprint, snapshot ID и
fingerprint, action ID и fingerprint, а также Stage16 policy identity. Fuzzy,
text-only и same-looking replacement запрещены.

## 5. Planning horizon, timezone и capacity

Каждая операция имеет explicit:

```text
start_local: YYYY-MM-DD
end_local:   YYYY-MM-DD, inclusive
timezone:    IANA timezone name
```

`end_local` не раньше `start_local`; горизонт от 1 до 31 локального дня.
Timezone проверяется через `zoneinfo.ZoneInfo`; server, filesystem, browser
timezone, `created_at` и `now` не являются fallback authority. `generated_at`
— только derived metadata.

Capacity — обязательная owner input map `available_minutes_by_date`, содержащая
ровно каждую дату горизонта. Значение — integer от 0 до 1440. Общая capacity —
детерминированная сумма; нулевая capacity при выбранных effort требует
`capacity_missing`/`capacity_conflict`, а не скрытого overbooking.

Fixed commitments и unavailable windows задаются только владельцем как
bounded `PlanningWindowV1` с exact ID, локальными датой/временем начала и
конца, title и kind. Они должны лежать в горизонте и не пересекаться между
собой. Это planning constraints, не импортированные Calendar events. Stage 17
не читает Calendar, email, telemetry, browser/system activity или historical
completion.

Owner может передать до 8 bounded `planning_constraints` и один bounded
`planning_context`; это явный текст владельца, а не скрытая память.

## 6. `PlanningContextPackV1`

Pack — immutable provider-free derived DTO. Он содержит ровно:

```text
contract_version / pack_version / policy identity
as_of
ordered selected Goal bindings
selected exact Stage16 snapshot/action projections
portfolio_order
start_local / end_local / timezone
capacity inputs and exact capacity fingerprint
fixed windows
planning constraints/context
readiness and bounded caveats
canonical pack_fingerprint
```

Pack не вызывает provider/network, не пишет store, не читает raw vault bodies и
не принимает источник, не перечисленный контрактом. Every source has exact ID,
fingerprint, policy identity, readiness и `as_of` where applicable.

Readiness precedence:

```text
source_changed > conflict > stale > missing/incomplete > exact_current
```

`exact_current` возможен только при совпадении всех exact Goal/Stage16/action
bindings и валидных planning inputs. Missing/stale/conflict не заменяется
похожим source; pack явно возвращает состояние и не позволяет безопасную
генерацию/acceptance, если контрактная операция требует exact current.

## 7. Provider-visible Planning Reasoning Envelope

Перед generate UI показывает exact canonical preview. В AdvisorPort разрешено
передать только ту же семантику и те же canonical bytes:

```text
bounded selected Goal projections
selected owner-reviewed Stage16 action projections
explicit owner portfolio order
planning horizon/timezone
capacity/availability
fixed commitments/unavailable windows
owner planning constraints/context
safe request-local aliases/fingerprints for result binding
```

Raw vault, Personal Memory, Decision Journal bodies, unrelated Goals,
unaccepted strategies, hidden Stage 10/15 data, Calendar/email/GitHub,
credentials, paths и store roots запрещены. No hidden suffix/context injection.

Policy reuse: `AdvisorPort` и текущий approved adapter; new provider, model,
secret и network route — **NO**. Stage17 operation имеет собственный
`stage17-personal-planning-v1` policy identity и не меняет Assistant v1 или
Stage16 semantics. Если existing boundary не может безопасно обслужить
bounded operation, результат — `provider_unavailable`/`HUMAN_REQUIRED`, без
скрытого добавления сервиса.

## 8. Closed Planning Proposal

`PlanningProposalV1` — bounded, ephemeral, derived, non-canonical,
non-executable DTO. Result states:

```text
proposal
insufficient_strategy | stale_strategy | source_changed
portfolio_conflict | capacity_missing | capacity_conflict
planning_context_insufficient | not_comparable
provider_unavailable | provider_abstained | hold_current_plan
```

В `proposal` разрешены только закрытые `PlanningItemV1` kinds:

```text
project       bounded outcome grouping, root
milestone     bounded checkpoint, child of project
commitment    owner-reviewable planned intent, child of milestone/project
next_action   bounded immediate planned item, child of milestone/project
hold          explicit non-plan/hold item, no execution semantics
```

Каждый item имеет bounded ID, kind, title, description, exact Goal refs, exact
Stage16 reviewed-action refs, optional exact parent ID, optional local target
window, integer `effort_minutes` и exact dependency IDs. `project` и
`milestone` имеют нулевой effort; `hold` не является исполняемым действием.
Every item binds one or more selected Goals and one or more reviewed actions;
cross-Goal item обязателен к явным refs.

Provider result не может содержать shell/browser command, tool invocation,
email/GitHub mutation, credential, external target или arbitrary nested JSON.
Unknown fields, unknown IDs, duplicate IDs, over-limit result и malformed
hierarchy reject; no silent truncation.

Dependencies — только exact item IDs из этого proposal; no self-reference,
dangling ref, cross-proposal ref, invalid parent or cycle. Graph must be a DAG.

Provider effort marker — `provider_proposed`. Target windows не являются
owner intent до review. Missing target не получает `now`.

## 9. Owner review и capacity validation

До acceptance владелец может reject весь proposal, выбрать/удалить items,
исправить bounded title/description/effort/window/dependencies, изменить
порядок и parent в разрешённой иерархии. Immutable остаются item ID, kind,
Goal refs, Stage16 action refs и proposal binding.

Accepted item хранит `generated_item_fingerprint`, owner-reviewed fields и
`effort_source=owner_reviewed`. Только owner-reviewed effort участвует в
capacity validation. Acceptance fail-closed, если сумма выбранных commitment и
next_action effort больше explicit capacity или если capacity missing/conflict.
В v1 нет implicit over-capacity acknowledgement.

Accepted windows используют те же local-date/timezone rules. Истёкшее окно
после acceptance получает только нейтральное derived состояние `window_elapsed`;
оно не означает failure, laziness, missed commitment, non-compliance или
completion outcome.

## 10. Accepted Planning Portfolio Snapshot

`PlanningPortfolioSnapshotV1` — operational append-only record вне vault и
release directories. Он содержит:

```text
planning_snapshot_id / sequence / state
policy identity
planning_context_pack_fingerprint
planning_proposal_fingerprint
exact selected Goal bindings
exact Stage16 snapshot/action refs
owner-reviewed items and order
owner-reviewed effort/windows/dependencies
start_local / end_local / timezone
capacity_inputs_fingerprint
reviewed_at / accepted_at
prior_snapshot_id / prior_snapshot_fingerprint
snapshot_fingerprint
```

Snapshot не содержит executable payload, execution status, actual duration или
completion checkbox. V1 допускает ровно один current global snapshot. Новое
acceptance явно supersedes предыдущий current snapshot, сохраняя историю.

Lifecycle:

```text
proposal -> reject
proposal -> explicit accept -> current snapshot
current snapshot -> fresh proposal/review -> explicit supersede
```

Нет automatic regeneration, midnight rollover, carry-over, schedule repair,
acceptance или supersession. Source drift после acceptance только показывает
`current`, `stale`, `source_changed` или `window_elapsed`; plan автоматически не
переписывается и не rebind-ится к новому Stage16 snapshot.

## 11. Operational store, integrity и recovery

Используется отдельный application-owned JSONL store по существующему Stage
15/16 pattern:

```text
<configured operational root>/prospective-audit/personal-planning/
  snapshots.jsonl
  manifest.json
  .store.lock
```

Store не хранит raw context pack или provider payload, только bounded reviewed
snapshot/event projections и exact fingerprints. Append под advisory lock,
atomic manifest, sequence/digest chain, fsync, path containment, safe
permissions и bounded records обязательны. Torn append, digest tamper,
reordered/missing sequence, symlink escape, unsafe permission, lock contention
и recovery ambiguity дают fail-closed error. No vault sync, backup side effect
или real canonical note mutation.

`operation_id` + canonical intent обеспечивают idempotent retry; повтор с иной
intent завершается conflict. `expected_prior_snapshot_id/fingerprint` защищает
concurrent accept. Under lock проверяются current state, source drift,
proposal binding, DAG и capacity; invariant — one current plan.

## 12. Private Web/API и browser privacy

Additive owner-only POST routes:

```text
/api/personal-planning/state
/api/personal-planning/context
/api/personal-planning/generate
/api/personal-planning/reject
/api/personal-planning/accept
```

Они используют trusted Host, same-origin Origin, exact request-purpose header,
strict JSON/content type, bounded raw body before parsing, Pydantic
`extra=forbid`, `Cache-Control: no-store`, текущие CSP/nosniff/referrer/frame
headers, no permissive CORS и safe bounded Russian errors. Нельзя возвращать
raw source bodies, paths, store roots, provider raw response, secrets,
exception repr или traceback.

Pack/proposal/snapshot живут только в памяти browser page. Запрещены
`localStorage`, `sessionStorage`, IndexedDB, Cache Storage authority,
service-worker cache private API, background sync, offline mutation replay и
console/logging private payloads.

## 13. UX/accessibility

Surface «Планирование» использует текущую cosmic/glass систему, mobile-first
layout и Russian copy. Основной путь: explicit Goal portfolio -> exact accepted
Stage16 snapshot/actions -> readiness -> horizon/timezone -> capacity/windows ->
constraints/context -> pack -> exact preview -> explicit Generate plan ->
review/edit/select/reorder -> capacity/dependencies -> Accept/Reject -> current
plan.

Все состояния понятны без технического жаргона; IDs/fingerprints доступны под
progressive disclosure. Нет completion checkbox, Start/Pause/Blocked/Timer,
Calendar/Gmail/GitHub/Telegram action, shell/browser execution, agent или
autopilot. Проверяются 320/360/390/430, tablet/desktop, keyboard-only,
visible focus, focus restoration, dialog/panel semantics, non-colour states,
reduced motion и отсутствие horizontal overflow. Native single-select controls
сохраняют видимый signal-фиолетовый chevron.

## 14. Alternatives register

| Вариант | Решение | Причина |
| --- | --- | --- |
| отдельная БД | отклонён | существующий JSONL store достаточен и безопаснее для v1 |
| external Calendar capacity | отклонён | authority ещё не создана; capacity вводится явно |
| все Goals автоматически | отклонён | нарушает owner agency и exact binding |
| hidden utility/priority score | отклонён | создаёт непрозрачную оптимизацию |
| arbitrary todo JSON | отклонён | невозможно строго проверить semantics и execution boundary |
| новый provider/model/secret | отклонён | reuse approved AdvisorPort; иначе fail closed |
| Stage17 writes в vault | отклонён | planning — operational intent, не canonical evidence |
| automatic carry-over/execution | отклонён | belongs to Stage 18/19 |

## 15. Implementation map

| Фаза | Результат |
| --- | --- |
| 17.0 | этот contract и roadmap status |
| 17.1 | immutable portfolio DTOs, exact Stage16 bindings, deterministic provider-free Context Pack |
| 17.2 | explicit Planner boundary, exact preview parity, strict Planning Proposal и abstention |
| 17.3 | append-only Planning Portfolio store, review/edit/select/order, capacity/DAG, accept/reject/supersede |
| 17.4 | owner-only API/UI, Russian accessibility copy, memory-only client flow |
| 17.5 | adversarial security/privacy/store/API/E2E and Stage 1–16 regression gate |
| 17.6 | factual ledger, exact-head full gate, standard autodeploy, non-mutating smoke, close #348 |

## 16. Delivery and final flags

Каждая фаза начинается со свежего exact `origin/main`, использует один
изолированный Stage17 worktree, проходит focused/repository gates, PR under
`Refs #348`, exact-head CI, merge, post-merge CI, standard automatic deploy if
triggered, non-mutating health/private smoke, cleanup и fresh fetch. Только
финальный closeout PR использует `Closes #348`.

Ожидаемые impact flags:

```text
new provider/model/secret: NO
Assistant v1 semantics changed: NO
Stage16 semantics changed: NO
Cognitive Twin Stage1-15 semantics changed: NO
new canonical vault family: NO
global schema_version changed: NO
new NoteType: NO
new DB/framework: NO
new operational Stage17 planning state: YES
second-brain-vault changed: NO
real vault data changed: NO
env change required: no
Stage 18 started: NO
```

After Stage 17.6:

```text
Second Brain v4 IN PROGRESS: YES
Stage 16 COMPLETE: YES
Stage 17 COMPLETE: YES
Stage 18–20 PLANNED / NOT STARTED
```
