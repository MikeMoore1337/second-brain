# Executive Strategy v1 — Stage 16 normative contract

Статус после Phase 16.6: **NORMATIVE CONTRACT / RUNTIME COMPLETE / CLOSEOUT VERIFIED**.

Контракт задаёт только Personal Strategy / Executive Layer v1. Cognitive Twin
v3 и контракты Stage 1–15 остаются authority для собственных семантик.

## 1. Назначение и non-goals

Stage 16 отвечает на bounded вопрос:

> Для одного явно выбранного текущего Goal, его текущего прогресса,
> наблюдаемого поведения, отношения Growth, выбранного результата Experiment,
> адаптивного профиля и явно заданных ограничений — какие конкретные следующие
> действия стоит рассмотреть сейчас?

Результат — стратегия и кандидаты действий, а не исполнение.

Stage 16 не создаёт календарь, планировщик, task/project/commitment system,
email/message sender, GitHub mutator, shell/browser executor, Action Gateway,
автономного агента или automatic learning loop. Он не стартует эксперименты и
не меняет Goal, Progress, Growth, Behavioral Model или Stage 15 profile.

## 2. Fixed identities, policy и canonical encoding

| Поле | Значение |
| --- | --- |
| `contract_id` | `executive-strategy-v1` |
| `contract_version` | `1` |
| `policy_id` | `stage16-executive-strategy-v1` |
| hash algorithm | SHA-256 над canonical UTF-8 JSON без пробелов, `ensure_ascii=false` |
| `policy_fingerprint` | `5af1723247319830f87432d1827fd47a29288ea957c0a05bf51440cb5ab9b7aa` |

Policy fingerprint вычисляется по exact bytes:

```json
{"contract_id":"executive-strategy-v1","contract_version":"1","policy_id":"stage16-executive-strategy-v1","action_kinds":["act","investigate","clarify","experiment_candidate","hold"],"result_states":["proposal","insufficient_context","insufficient_evidence","not_comparable","source_changed","conflicting_constraints","hold_current_strategy","provider_unavailable","provider_abstained"],"source_aliases":["goal.current","growth.relation","progress.current","behavior.relation","experiment.terminal","adaptive_profile.active","calibration.caveat","caller.task","caller.constraints","caller.context"]}
```

Все DTO immutable после нормализации. JSON fingerprints используют одну
canonical encoding; неизвестные поля, duplicate aliases, не-NFC строки,
control characters, NaN и unbounded collections отклоняются.

## 3. Exact one-Goal binding

Каждый request обязан содержать:

```text
goal_source_uuid
goal_identity_fingerprint
```

`goal_source_uuid` — UUID exact current Goal. `goal_identity_fingerprint` —
fingerprint exact `GrowthGoalIdentityV1.as_dict()` по Growth canonical hash
policy. Goal считается тем же только при совпадении обеих частей и текущего
`GROWTH_POLICY_FINGERPRINT`.

Запрещены fuzzy match, match по тексту, newest Goal fallback, implicit
selection, hidden multi-goal ranking и silent rebind. Different UUID с тем же
текстом — другой Goal и fail-closed `source_changed`/`not_comparable`.

## 4. Authority/source matrix

| Alias | Источник и exact binding | Authority | В provider |
| --- | --- | --- | --- |
| `goal.current` | current `GrowthGoalIdentityV1`, UUID + identity/source fingerprints + Growth policy | canonical | только bounded Goal text projection и alias |
| `growth.relation` | current Stage 11 relation for the same Goal UUID/fingerprint, Growth policy/mapping fingerprints | derived from canonical/operational Growth | bounded relation summary |
| `progress.current` | current Stage 12 result for same Goal identity, result/definition/policy fingerprints | derived from canonical Progress | bounded status/progress summary |
| `behavior.relation` | exact Stage 10 relation/cohort/pattern/mapping for selected Goal | derived | bounded relation summary |
| `experiment.terminal` | explicitly selected exact Stage 14 terminal result + reviewed reassessment | canonical/derived Stage 14 | bounded result/caveat summary |
| `adaptive_profile.active` | current exact Stage 15 active profile/projection for same Goal | operational Stage 15 | bounded profile/projection summary |
| `calibration.caveat` | optional exact Stage 9 calibration snapshot used only as model-quality caveat | operational Stage 9 | bounded caveat; never action authority |
| `caller.task` | current owner request | explicit caller input | exact bounded text |
| `caller.constraints` | current owner constraints | explicit caller input | exact bounded list |
| `caller.context` | current owner context | explicit caller input | exact bounded text |

Raw Personal Memory, arbitrary retrieval/search hits, Decision Journal bodies,
note bodies, YAML/front matter, filesystem paths, all Goals, unrelated history,
secrets and operational-store paths are never automatic Stage 16 sources.

## 5. Source availability, precedence и stale rules

Каждый source item имеет closed `readiness`:

```text
exact_current | missing | stale | conflict | not_comparable | policy_mismatch
```

Precedence is fail-closed and deterministic:

1. invalid policy/contract -> `policy_mismatch`;
2. selected Goal identity mismatch or changed source -> `source_changed`;
3. conflicting exact bindings -> `conflict`;
4. incompatible shape/cutoff -> `not_comparable`;
5. unavailable/missing optional source -> `missing`;
6. outdated fingerprint/as-of -> `stale`;
7. otherwise `exact_current`.

Required inputs are exact Goal, non-empty caller task and caller envelope. The
other families may be missing only when the pack explicitly records that fact;
there is no guessed replacement. A missing source may force a safe abstention
when the evidence gate cannot support reasoning.

Any source drift after an accepted snapshot makes that snapshot `stale` in a
fresh read. It never rewrites or silently supersedes the snapshot.

## 6. `ExecutiveContextPackV1`

The provider-free immutable pack contains exactly:

```text
contract_version: "executive-strategy-v1"
pack_version: "1"
as_of: UTC timestamp
goal_source_uuid: UUIDv7
goal_identity_fingerprint: SHA-256
goal_text: 1..4096 UTF-8 bytes, owner-facing bounded projection
task: 1..2048 UTF-8 bytes
constraints: 0..8 items, each <=512 bytes, total <=4096 bytes
current_context: 0..4096 UTF-8 bytes
sources: exactly the fixed source-alias order from policy
readiness: exact_current | incomplete | conflict | source_changed
pack_caveats: <=8 bounded strings
policy_id: exact policy id
policy_fingerprint: exact policy fingerprint
source_pack_fingerprint: SHA-256
```

Each source item contains only `alias`, `readiness`, `reference_id` (or null),
`reference_fingerprint` (or null), bounded `summary` (or safe missing reason),
`policy_fingerprints`, and `as_of`. The pack is rebuilt from fresh server reads,
never persisted and never calls provider/network.

`source_pack_fingerprint` covers all exact IDs, fingerprints, readiness,
summaries, caller inputs, policy identity and canonical field order. A second
build over unchanged exact inputs has identical bytes/fingerprint.

## 7. Strategy Reasoning Envelope and preview

The only provider-visible payload is the canonical `StrategyReasoningEnvelopeV1`
projection of the pack:

```text
task
explicit_goals: [selected bounded Goal text]
explicit_constraints: caller constraints
explicit_context: ordered items with kind=fact/background and text consisting of
  bounded source summaries plus safe aliases
options: empty unless a future contract explicitly adds owner options
```

The envelope is serialized once with the existing Assistant v1 canonical
encoding. The owner-visible preview returns those exact UTF-8 bytes rendered as
safe JSON/text; the provider adapter receives exactly the same bytes and no
additional suffix, prompt fragment or hidden context. The preview also shows
source aliases and readiness, but those technical labels are not silently
added to provider payload beyond the canonical envelope.

No provider call occurs on page load, Goal selection, pack build, preview,
refresh, source-drift read, accepted-snapshot display, polling or timer.
Generation is a POST initiated by an explicit owner action and executes at
most one bounded Advisor operation with cancellation/deadline handling.

Stage 16 reuses the existing provider-neutral `AdvisorPort` and approved
production provider adapter through a Stage16-owned mapping seam. This adds no
provider, model, secret or network route. Assistant v1 request/result semantics
remain unchanged; the adapter receives only the mapped canonical envelope.

If the approved boundary cannot safely handle the envelope, return
`provider_unavailable` and require no new provider by default.

## 8. `StrategyProposalV1`

The proposal is derived, bounded and ephemeral until explicit acceptance:

```text
proposal_id: UUIDv7
proposal_version: "1"
result_state: one closed result vocabulary
as_of: UTC timestamp
goal_source_uuid
goal_identity_fingerprint
source_pack_fingerprint
policy_id
policy_fingerprint
candidates: 0..8 typed ActionCandidateV1 values
suggested_order: candidate action_id values, each at most once
reasons: 1..8 bounded strings
caveats: 0..8 bounded strings
provider_fingerprint: SHA-256 of validated provider result
proposal_fingerprint: SHA-256 of the complete proposal
```

`ActionCandidateV1` has only these fields:

```text
action_id: request-local bounded identifier
kind: act | investigate | clarify | experiment_candidate | hold
title: 1..256 UTF-8 bytes
description: 1..2048 UTF-8 bytes
basis_aliases: 1..8 values from the fixed source alias set
goal_relation: 1..512 UTF-8 bytes
expected_observable_signal: 1..1024 UTF-8 bytes
prerequisites: 0..8 strings, each <=512 bytes
caveats: 0..8 strings, each <=512 bytes
```

Unknown fields and arbitrary nested JSON are rejected. Candidate text cannot be
executable code, shell/browser instructions, tool calls, secrets, paths or an
external action command. There is no probability, confidence, universal score
or provider-controlled canonical priority. `suggested_order` is presentation
only.

## 9. Closed result, abstention и error vocabulary

Valid no-candidate results are first-class:

```text
insufficient_context
insufficient_evidence
not_comparable
source_changed
conflicting_constraints
hold_current_strategy
provider_unavailable
provider_abstained
```

`proposal` is valid only with validated candidates or a contract-approved
empty hold. No action is manufactured to satisfy UI shape. Transport errors
use a separate fixed taxonomy with safe Russian messages and never include raw
body, source values, provider response, path, secret or traceback.

## 10. Provider result validation

Validation order is: bounded raw result -> exact JSON/object shape -> unknown
field rejection -> string normalization and byte limits -> closed kind/state
vocabulary -> source alias binding -> candidate cardinality/order -> prohibited
content checks -> canonical provider/proposal fingerprints. A malformed,
oversized, unknown-reference, replayed or semantically invalid result is
rejected without persistence; the intact context pack remains available.

Provider failure never becomes an accepted strategy and never mutates any
canonical or operational source.

## 11. Owner review semantics

The UI keeps the proposal in page memory and permits:

* reject the entire proposal;
* edit bounded candidate wording/content;
* select zero or more candidates;
* reorder selected candidates only if their IDs remain unique and exact;
* explicitly accept a reviewed Strategy Snapshot.

Provider output is not owner intent. Every accepted action preserves generated
source text/fingerprint plus the owner-reviewed text and an `edited` marker.
An acceptance request revalidates Goal, current source pack and proposal
fingerprints server-side immediately before the store commit.

## 12. Accepted Strategy Snapshot

`StrategySnapshotV1` is operational, owner-controlled state outside
`second-brain-vault` and release directories:

```text
snapshot_id: UUIDv7
sequence: positive integer
state: current | superseded | deactivated
goal_source_uuid
goal_identity_fingerprint
source_pack_fingerprint
proposal_fingerprint
selected_actions: 0..8 ReviewedActionV1 values
policy_id
policy_fingerprint
reviewed_at: UTC timestamp
accepted_at: UTC timestamp
prior_snapshot_id: UUIDv7 or null
prior_snapshot_fingerprint: SHA-256 or null
snapshot_fingerprint: SHA-256
```

`ReviewedActionV1` contains exact candidate ID/kind, generated bounded fields,
reviewed bounded fields and `edited`. It cannot contain executable instructions
or external target credentials. At most one `current` snapshot exists per exact
Goal. The snapshot is not a task list, calendar, commitment, evidence record or
execution queue.

## 13. Operational store и lifecycle

The preferred root is derived from the explicit env-file parent as:

```text
<env-file-parent>/prospective-audit/executive-strategy/
```

It must not resolve inside the vault, checkout, release directory or symlink
escape. The store reuses Stage 9/15 integrity conventions: bounded JSONL
append-only records, manifest, SHA-256 digest, monotonic sequence, fsync,
read-back verification, owner-group/permission checks, advisory lock and
fail-closed recovery. No database/framework/dependency is added.

Lifecycle:

```text
proposal -> reject
proposal -> explicit accept -> current snapshot
current snapshot -> fresh proposal -> explicit supersede
current snapshot -> explicit deactivate/clear, only if implemented by contract
```

No hidden in-place rewrite, automatic refresh, automatic supersede or automatic
acceptance. Retry with the same `operation_id` and identical canonical payload
is idempotent; the same ID with different payload is a conflict. Concurrent
acceptance rereads and validates under one lock. Corrupt digest, torn append,
missing sequence, reordered record, unsafe root or permission mismatch fails
closed.

## 14. Web/API boundary

Phase 16.4 adds only owner-only additive routes:

```text
POST /api/personal-strategy/state
POST /api/personal-strategy/context
POST /api/personal-strategy/generate
POST /api/personal-strategy/reject
POST /api/personal-strategy/accept
```

Every route requires authenticated owner session, trusted Host, same-origin
Origin, exact `X-Second-Brain-Request: executive-strategy-v1`, strict POST and
JSON content type, bounded raw body before parse, Pydantic `extra="forbid"`,
`Cache-Control: no-store`, current CSP/nosniff/referrer/frame protections and
no permissive CORS. Anonymous requests fail `AUTH_REQUIRED` before source/store
work. Responses never disclose raw private source values or operational paths.

No real owner strategy data is created in smoke. Production checks use health,
anonymous boundary and read-only fixtures only.

## 15. Browser, PWA, logging и accessibility

Private pack, envelope, proposal and snapshot remain page-memory values. No
browser storage, service-worker private caching, background synchronization or
console logging is allowed.

The additive surface reuses cosmic/glass, current typography and mobile-first
controls. It must be keyboard complete, have visible focus, semantic labels,
Russian loading/error/empty/status copy, live status announcements, explicit
non-colour states and `prefers-reduced-motion`. Technical provenance is
progressively disclosed. Widths 320/360/390/430 must not hide the exact Goal,
primary action, review controls or stale state.

## 16. No automatic learning or execution

Accept, reject, edit, select and reorder are operational UI actions only. They
do not retrain a provider, Cognitive Twin, Stage 15 profile or hidden weights;
do not create Personal Memory/Decision Journal/Growth evidence; do not start
experiments; and do not create tasks, commitments, calendar events or external
actions.

## 17. Alternatives register

| Вариант | Решение | Причина |
| --- | --- | --- |
| менять Assistant v1 in place | REJECT | сохраняем caller-explicit boundary |
| raw vault/RAG/private history in provider payload | REJECT | минимизация и provenance |
| fuzzy Goal or same-text rebinding | REJECT | exact identity is authoritative |
| new provider/model/secret/route | REJECT | existing Advisor seam is sufficient; otherwise HUMAN_REQUIRED |
| automatic refresh/accept/learning | REJECT | owner agency and versioned review |
| write strategy to vault | REJECT | strategy is operational, not canonical evidence |
| new database/framework/global schema/NoteType | REJECT | existing bounded JSONL store is sufficient |
| one current snapshot per exact Goal | ACCEPT | bounded, reversible v1 lifecycle |
| safe hold/abstention | ACCEPT | weak inference is worse than explicit uncertainty |

## 18. Exact implementation map

### 16.1 — Provider-free Executive Context Pack

Добавить immutable DTOs, exact source loaders/readiness, fingerprints,
bounded summaries and deterministic pack builder. No provider, network, write,
store or UI.

### 16.2 — Explicit Strategy Advisor / Proposal core

Добавить preview bytes, explicit execute operation, existing Advisor mapping,
strict provider result validation and proposal DTO. Provider doubles only;
no persistence and no Assistant v1 semantic change.

### 16.3 — Reviewed operational Strategy Snapshot

Добавить append-only store, integrity/lock/idempotency/concurrency, reject,
accept and supersede lifecycle with exact pack/proposal revalidation. No vault
write or execution.

### 16.4 — Owner-only Web/API/UI

Добавить private routes, state/read/preview/generate/review/accept UI, Russian
accessibility copy, mobile/reduced-motion behavior and memory-only private data.

### 16.5 — Security/privacy/integration/E2E

Добавить adversarial identity, stale/tamper/provider/store/auth/privacy and
Stage 1–15 regression coverage; fix only Stage16 boundary defects.

### 16.6 — Final release/closeout

Фактически выполнено: собран delivery ledger, пройдены exact-head full Python
3.14/frontend/security gates, CI и standard automatic deploy, проверены
`/healthz`, anonymous route protection и no-vault/no-real-strategy smoke,
выполнен scoped cleanup и закрыт Issue #339.

## 19. Acceptance boundary

До final closeout необходимо доказать exact one-Goal scope, deterministic
provider-free pack, exact preview/provider bytes, explicit-only provider call,
strict bounded proposal and abstention, owner review before acceptance, exact
versioned current snapshot, visible source drift, operational-store integrity,
no private browser persistence/log leakage, unchanged Assistant/Stage 1–15
semantics, zero automatic task/external action/experiment/training and no
vault mutation.

## 20. Delivery status fields

After the Phase 16.6 closeout the following status is authoritative:

```text
Stage 16 v4 design gate = COMPLETE (Phase 16.0)
Executive Context Pack = COMPLETE (Phase 16.1)
explicit Strategy reasoning boundary = COMPLETE (Phase 16.2)
Strategy Proposal = COMPLETE (Phase 16.2)
reviewed operational Strategy Snapshot = COMPLETE (Phase 16.3)
owner-only Web/API/UI = COMPLETE (Phase 16.4)
security/E2E gate = COMPLETE (Phase 16.5)
release/production/closeout = COMPLETE (Phase 16.6)
```

Stage 17 remains planned/not started.

## 21. Factual delivery ledger

The ledger records the final exact-head checks for runtime phases 16.0–16.5.
The Phase 16.6 closeout PR and final report record its own merge/CI/deploy
identifiers after those workflows complete. Deploy is the standard automatic
workflow from the corresponding merge SHA.

| Phase | PR | Merge SHA | PR CI | Post-merge CI | Deploy |
| --- | --- | --- | --- | --- | --- |
| 16.0 | [#340](https://github.com/MikeMoore1337/second-brain/pull/340) | `8b3e8ab7fe4e7294294be44c36a02eb4c49dc86e` | `35057554104` | `35057756065` | `35057913595` |
| 16.1 | [#341](https://github.com/MikeMoore1337/second-brain/pull/341) | `b12d8fa5379cf3ca0160b9aa071ffec5c04c52d2` | `35058866284` | `35059072965` | `35059241332` |
| 16.2 | [#342](https://github.com/MikeMoore1337/second-brain/pull/342) | `91491b5b79f460a871b9921478b2ebb3c03e5912` | `35060300561` | `35060514564` | `35060713890` |
| 16.3 | [#343](https://github.com/MikeMoore1337/second-brain/pull/343) | `05a30aa687e9cdb5aceff7690c0ffcde1fbc9373` | `35062757876` | `35063009719` | `35063238859` |
| 16.4 | [#344](https://github.com/MikeMoore1337/second-brain/pull/344) | `b15da49213224a23b2aad6899c50634faaee7f91` | `35067551979` | `35067848504` | `35068068955` |
| 16.5 | [#345](https://github.com/MikeMoore1337/second-brain/pull/345) | `e9eee57d5e14b37aecb3fd0d6794637571b84117` | `35069788511` | `35070002528` | `35070279603` |
Intermediate PRs use `Refs #339`; only the final closeout PR uses `Closes #339`.
