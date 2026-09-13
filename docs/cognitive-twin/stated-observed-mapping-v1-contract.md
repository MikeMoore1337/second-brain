# Stated-vs-Observed Explicit Mapping & Composition v1 — design contract

Статус документа: **DESIGN / NORMATIVE CONTRACT ONLY**. Это deliverable
Issue #252, Stage 10C0. Документ закрывает design gate для будущего
Stated-vs-Observed mapping/composition, но не включает runtime.

Документ читается поверх:

- [self-model-v1-contract.md](self-model-v1-contract.md);
- [behavioral-self-model-v1-contract.md](behavioral-self-model-v1-contract.md);
- [design-roadmap-v1.md](design-roadmap-v1.md);
- текущего Stage 4 `BuildSelfModel`;
- текущих Stage 10A `BuildBehavioralObservations` и Stage 10B
  `BuildBehavioralSelfModel`;
- текущей canonical границы `scan -> build_report`.

`ACCEPT` означает принятую нормативную границу этого документа, `CHANGE` —
необходимое изменение статуса или ссылки без переписывания исторической
semantics Stage 1–10B, `RISK` — известное ограничение, которое нельзя
маскировать эвристикой, `DEFER` — отдельный будущий owner-approved gate.

Никакая часть этого документа не разрешает автоматическое semantic matching,
изменение canonical vault, Stage 9 store, Simulate Me, provider boundary или
production runtime.

## 1. Purpose и non-goals

### 1.1. Purpose

Будущая capability должна позволять владельцу явно связать:

~~~text
current reviewed Stated Self Model direct claim
    +
current Behavioral Self Model exact cohort/option
    ->
owner-explicit mapping
    ->
current-source revalidation
    ->
deterministic composition
~~~

Результат описывает только relation между двумя derived projections:

~~~text
явно сопоставленное утверждение X
vs
наблюдаемый exact choice Y
в exact cohort и bounded current window
~~~

`aligned` и `divergent` являются derived relation states. Они не являются
вердиктом истины, психологическим выводом, диагнозом, оценкой характера или
доказательством «настоящей preference».

### 1.2. Non-goals

В Stage 10C0 не входят:

- mapping runtime, composition runtime или новый application service;
- mapping store, schema migration, DB или новый canonical field;
- Web/API/UI, authentication implementation или browser persistence;
- изменение Stage 4, Stage 10A, Stage 10B, Stage 9, Simulate Me или
  `second-brain-vault`;
- fuzzy, semantic, lexical-similarity, synonym, translation, embedding или
  LLM matching;
- automatic mapping по label equality, closest option/cohort или majority;
- превращение mapping в Personal Memory, user fact, preference или decision;
- automatic correction, Safe Write, write-back или mutation canonical notes;
- prediction, calibration, weighting, confidence, reward или hidden score;
- personality, sensitive-trait, manipulation-susceptibility или risk inference;
- Stage 10D, Stage 11+ и автоматическое создание следующего Issue.

## 2. Terminology

| Термин | Нормативное значение |
| --- | --- |
| `current stated claim` | Текущий direct assertion claim из `BuildSelfModel`, разрешённый этим contract только для `preference`. |
| `stated source` | Current enrolled Personal Memory note, на которую указывает supporting `SelfModelEvidenceRef.note_id`. |
| `stated identity` | Exact identity current claim/source: UUID, dimension, canonical metadata, evidence-time representation, derivation/policy и два source/claim fingerprint. |
| `behavioral cohort` | Exact `BehavioralCohortIdentityV1` из Stage 10A/10B; raw context не является частью mapping record. |
| `behavioral option identity` | Exact `option_index + option_fingerprint` внутри exact cohort option namespace. |
| `comparison subject` | Единственный exact observed option, который в текущем Behavioral pattern допускает binary comparison. |
| `accepted mapping` | Immutable owner-reviewed operational relation, принятая backend после current reread и immediate revalidation. |
| `mapping store` | Отдельное operational persistence boundary для accepted relations; это не vault, не Stage 9 store и не user evidence. |
| `mapping current` | Accepted mapping, чей lifecycle active и чьи все identity/policy/source fingerprints точно совпали с current rebuild. |
| `mapping stale` | Accepted mapping, которая больше не подтверждается current identity или policy; она не используется и не retarget-ится. |
| `reviewed_at` | Server/application time explicit owner confirmation; это не время Stated evidence и не время Decision Journal evidence. |
| `created_at` | Store-assigned time durable acceptance record; это не evidence time. |
| `mapping fingerprint` | Content integrity fingerprint exact mapping payload; он не заменяет store-owned `mapping_id`. |
| `not_comparable` | Safe default, когда relation не доказана exact accepted mapping или binary comparison не имеет deterministic subject. |

## 3. Authority matrix

| Source / object | Authority | Разрешённая роль | Запрещённое повышение authority |
| --- | --- | --- | --- |
| `second-brain-vault` через current `scan() -> build_report()` | Единственный canonical source user evidence | Current Personal Memory, Decision Journal и Outcome projections | Search, cache, browser payload или прошлый result не могут заменить current source. |
| Stage 4 `SelfModelClaim` и supporting current UUID | Derived stated projection | Exact stated-side input после backend revalidation | Claim не становится canonical fact и не получает hidden status. |
| Stage 10A `BehavioralObservationV1` / Stage 10B `BehavioralPatternV1` | Derived behavioral projection | Exact cohort, option, pattern и source fingerprints | Pattern не становится stated preference или canonical behavior fact. |
| Accepted mapping record | Operational relation authority | Только owner-confirmed relation между двумя exact derived identities | Mapping не переписывает ни stated, ни behavioral layer. |
| Mapping store | Canonical operational history mapping decisions | Read current accepted/superseded/invalidation lifecycle | Store не является vault evidence, Stage 9 history или source for Stage 10A/10B. |
| Stage 9 audit/link/calibration | Separate operational/model-evaluation layer | Никакой mapping authority | Prediction, match, mismatch, calibration или audit recency не создают и не подтверждают mapping. |
| Simulate Me / Assistant / Compare | Derived/ephemeral consumers | Не входят в source set | Их output не создаёт mapping и не меняет comparison state. |
| Browser/client | Transport и explicit owner action surface | Может передать bounded selector и operation identity | Body, labels, context, UUID claim или client fingerprint не являются truth. |
| `mapping_fingerprint` / policy fingerprint | Integrity binding | Revalidation и drift detection | Fingerprint не даёт semantic equivalence и не выбирает closest replacement. |

## 4. Normative policy identity

Stage 10C0 фиксирует следующие identifiers для будущего runtime:

~~~text
contract_version       = stated-observed-mapping-v1
mapping_policy_id      = stated-observed-explicit-mapping-v1
mapping_derivation     = stated-observed-composition-derivation-v1
mapping_basis          = owner-explicit-stated-behavior-v1
cardinality_policy     = one-stated-one-cohort-one-option-v1
dimension_policy       = preference-only-v1
comparison_policy      = current-exact-option-only-v1
temporal_policy        = current-build-no-evidence-time-inference-v1
persistence_policy     = dedicated-operational-mapping-store-v1
store_policy           = append-only-local-jsonl-v1
~~~

Canonical policy JSON v1:

~~~json
{"basis":"owner-explicit-stated-behavior-v1","cardinality":"one-stated-one-cohort-one-option-v1","comparison":"current-exact-option-only-v1","contract":"stated-observed-mapping-v1","dimension":"preference-only-v1","persistence":"dedicated-operational-mapping-store-v1","store":"append-only-local-jsonl-v1","temporal":"current-build-no-evidence-time-inference-v1","version":"1"}
~~~

Policy JSON сериализуется как UTF-8 JSON без whitespace, с
`ensure_ascii=false`, `separators=(",", ":")`, `sort_keys=true`, без BOM и
dynamic keys. Ожидаемый:

~~~text
mapping_policy_fingerprint =
sha256:ee9174fbbaf3d9913c4c5098e3e61d16f64abef4857b0f2716f1b4c4cd6e5e9d
~~~

Изменение mapping basis, cardinality, eligible dimension, comparison subject,
temporal или persistence semantics требует новой policy/derivation version.
Client не может выбрать policy или передать её как произвольное поле.

## 5. Exact stated-side identity

### 5.1. Eligibility

Stated side допускается только если backend заново построил current Stage 4
result через canonical `scan -> build_report` boundary и нашёл ровно один
current direct assertion claim, который одновременно:

1. имеет supporting current canonical UUIDv7;
2. относится к `explicit_user_fact` или `user_statement`;
3. имеет `self_kind = preference` и derived `dimension = preference`;
4. имеет exact non-null canonical `domain`;
5. проходит существующие Stage 4 metadata, storage identity, body и report
   integrity checks;
6. находится в current `SelfModelResult` с approved Stage 4 policy binding;
7. не является `memory`, `belief`, `goal`, `decision`, `outcome` или
   inferred/future dimension.

`current` означает присутствие в свежем current rebuild, а не отсутствие
`valid_until` и не близость `evidence_at` к `reviewed_at`.

### 5.2. StatedAssertionIdentityV1

Будущий exact DTO:

~~~text
StatedAssertionIdentityV1 {
  source_note_uuid:              UUIDv7
  dimension:                     "preference"
  source_evidence_kind:          "explicit_user_fact" | "user_statement"
  source_self_kind:              "preference"
  domain:                        string                 # exact non-null slug
  evidence_at:                   RFC3339 UTC | "unknown"
  evidence_at_precision:         "exact" | "unknown"
  source_contract_version:       "self-model-v1"
  source_derivation_version:     "self-model-derivation-v1"
  self_model_policy_fingerprint: 64 lowercase hex       # Stage 4 format
  source_fingerprint:            MappingHashV1
  claim_fingerprint:             MappingHashV1
}
~~~

`MappingHashV1` имеет ровно форму:

~~~text
sha256: + 64 lowercase hexadecimal characters
~~~

`source_note_uuid` и `dimension` являются structural identity. `claim_fingerprint`
и `source_fingerprint` являются current-content bindings. Raw claim body не
становится sole identity и не сохраняется в mapping.

### 5.3. Exact fingerprint inputs

Для всех новых mapping fingerprints используется функция `H(value)`, которая
считает `sha256:` от canonical UTF-8 JSON или UTF-8 text согласно указанному
payload.

`claim_fingerprint` вычисляется из exact Stage 4 claim projection. Его payload:

~~~json
{"claim_text_fingerprint":"<H(exact UTF-8 claim bytes)>","dimension":"preference","domain":"<exact domain>","source_contract_version":"self-model-v1","source_derivation_version":"self-model-derivation-v1","source_note_uuid":"<UUIDv7>"}
~~~

`claim_text_fingerprint` — digest exact emitted `SelfModelClaim.claim` после
существующей Stage 4 нормализации CRLF/CR -> LF. Он не означает semantic
similarity.

`source_fingerprint` вычисляется из current validated source projection:

~~~json
{"claim_fingerprint":"<claim fingerprint>","domain":"<exact domain>","evidence_at":"<canonical UTC or unknown>","evidence_at_precision":"<exact or unknown>","evidence_kind":"<explicit_user_fact or user_statement>","self_kind":"preference","source_contract_version":"self-model-v1","source_note_uuid":"<UUIDv7>"}
~~~

`created`, `updated`, mtime, path, filename и UUIDv7 embedded timestamp не
являются evidence или claim identity. Storage-only change, который не меняет
current Stage 4 source projection, не должен создавать ложный semantic drift.
Изменение body, enrolled metadata, dimension, domain, evidence time/precision
или Stage 4 derivation/policy меняет соответствующий fingerprint и
останавливает composition.

### 5.4. Revalidation

Перед принятием и перед каждым использованием mapping backend обязан:

1. прочитать current canonical source;
2. заново построить current `SelfModelResult`;
3. разрешить `source_note_uuid` без path/title/body fallback;
4. найти ровно один current claim;
5. заново вычислить `StatedAssertionIdentityV1`;
6. сравнить каждый identity field и fingerprint exact.

Новая note, похожий body, новая формулировка или новый claim не заменяют
старый UUID. При mismatch mapping не retarget-ится к «ближайшему» claim.

## 6. Exact behavioral-side identity

### 6.1. BehavioralComparisonIdentityV1

Behavioral side сохраняет только exact Stage 10 identity и bounded drift
bindings:

~~~text
BehavioralComparisonIdentityV1 {
  behavioral_contract_version:  "behavioral-self-model-v1"
  behavioral_derivation_version:"behavioral-self-model-derivation-v1"
  observation_version:          "behavioral-observation-v1"
  policy_id:                    "behavioral-self-model-exact-context-v1"
  policy_fingerprint:           MappingHashV1
  cohort:                       BehavioralCohortIdentityV1
  option:                       BehavioralOptionIdentityV1
  comparison_subject:           "current-exact-option-v1"
  pattern_type:                 closed Stage 10B pattern type
  pattern_state:                closed Stage 10B pattern state
  pattern_fingerprint:          MappingHashV1
  source_fingerprint:           MappingHashV1
  provenance_fingerprint:       MappingHashV1
  source_count:                 int >= 0
}
~~~

`cohort` должен быть exact current `BehavioralCohortIdentityV1`. Его
`cohort_fingerprint` и `option_namespace_fingerprint` обязательны и
сравниваются exact. `option` всегда означает `option_index` вместе с
`option_fingerprint`; index без fingerprint недостаточен.

Mapping record не хранит ordered option labels. Label namespace можно
временно reread-ить для owner review, но label equality не создаёт relation и
не является persisted authority.

### 6.2. Допустимый comparison subject

Owner может принять mapping только к exact cohort/option, для которого current
Stage 10B pattern на момент review имеет одну из следующих форм:

| Stage 10B pattern | Допустимо для mapping | Причина |
| --- | --- | --- |
| `repeated_exact_choice / current` | Да | Current window содержит deterministic exact subject. |
| `stable_over_time / stable` | Да | Current window содержит deterministic subject; historical window только контекст стабильности. |
| `insufficient_evidence / insufficient` | Да, если exact cohort и option namespace доказаны | Relation можно сохранить, но composition остаётся `behavioral_evidence_insufficient`. |
| `repeated_exact_choice / historical` | Нет | Нет current comparison subject. |
| `mixed_exact_choices / mixed` | Нет | Нет одного observed subject. |
| `changed_over_time / changed` | Нет | Два temporal subjects нельзя схлопнуть в binary relation. |
| `not_comparable / not_comparable` | Нет | Exact comparison не доказан. |

Для `insufficient_evidence` owner может выбрать любой exact option из current
namespace, но это не превращает один или два observations в aligned/divergent.
Если cohort или namespace нельзя разрешить exact, mapping не принимается.

### 6.3. Behavioral drift fingerprints

`provenance_fingerprint` сохраняется из Stage 10B
`BehavioralProvenanceV1`. `source_fingerprint` вычисляется отдельно как
canonical digest sorted по source UUID:

~~~json
{"observations":[{"journal_snapshot_fingerprint":"<Stage 10A snapshot fingerprint>","source_journal_uuid":"<UUIDv7>"}, "..."]}
~~~

В payload попадают только UUID и fingerprints, не bodies, labels или paths.
`journal_snapshot_fingerprint` уже является Stage 10A drift binding и включает
`outcome_presence`. Поэтому edit/delete Outcome может сделать mapping stale,
хотя outcome по-прежнему не меняет choice support или composition semantics;
после новой review outcome снова остаётся presence-only metadata.

`pattern_fingerprint` — digest raw-label-free comparison projection текущего
Stage 10B pattern: exact cohort identity, policy identity, pattern type/state,
current/historical window summaries, exact choice supports, temporal span,
`source_fingerprint` и `provenance_fingerprint`. `generated_at` и mapping
review time в него не входят. Fingerprint не используется как semantic match.

Изменение source set, snapshot, cohort, option namespace, pattern state/type
или policy делает accepted mapping stale; automatic closest replacement
запрещён.

## 7. Mapping cardinality

В v1 принята conservative injective rule:

~~~text
one current stated assertion
    -> one exact behavioral cohort
    -> one exact behavioral option
~~~

На active mapping set одновременно действуют:

1. у одного `source_note_uuid + claim_fingerprint` может быть не более одного
   active mapping;
2. один exact `cohort_fingerprint` может иметь не более одного active mapping;
3. один active mapping содержит ровно один option identity;
4. один mapping не может содержать tuple из нескольких cohorts/options;
5. superseded или invalidated records не освобождают право молча переписать
   старую relation: новая relation проходит explicit supersession.

Таким образом, v1 не допускает ни `one stated -> many cohorts`, ни
`many stated -> one cohort`, ни implicit many-to-many. Даже если labels
совпадают, namespace bridging не выполняется. Попытка создать две active
relations с конфликтующим source/cohort/option даёт
`AMBIGUOUS_MAPPING` или `CONCURRENCY_CONFLICT`, а не last-write-wins.

Поддержка bounded many-to-one/many-to-many требует новой cardinality policy,
conflict semantics и отдельного owner gate.

## 8. Stated dimension eligibility

### 8.1. Выбор v1

В v1 mapping допускает только `preference`.

Причины:

- Behavioral source фиксирует actual choice, а не belief или goal;
- `preference` имеет наиболее прямую deterministic relation с selected option;
- belief и goal могут объяснять выбор, конфликтовать с ним или быть
  independent scope, но current contracts не дают exact semantics, чтобы
  считать их равными option;
- implicit `goal == chosen option` и `belief == chosen option` создавали бы
  semantic inference.

`belief`, `goal`, `memory`, `decision_rule`, `behavioral_pattern` и любые
future dimensions получают `UNSUPPORTED_STATED_DIMENSION` и composition
`not_comparable`. Owner explicit review не превращает неподдерживаемую
dimension в v1 supported dimension.

### 8.2. Domain и time

`stated.domain` должен быть exact non-null current canonical slug и обязан
равняться `behavioral.cohort.domain`. Отсутствующий или изменившийся domain
не создаёт cross-domain mapping.

`evidence_at = unknown` допускается как literal current Stated metadata и не
получает invented timestamp. Owner может подтвердить mapping, но composition
добавляет fixed caveat `stated_evidence_time_unknown` и не утверждает
временное совпадение assertion с decision. В v1 unknown time сам по себе не
переписывается в `not_comparable`, если exact current source/relation
доказаны; отдельная temporal policy может ужесточить это в будущем.

## 9. Persistence decision

### 9.1. Рассмотренные варианты

| Вариант | Authority / lifecycle | Privacy / drift / deletion | Решение |
| --- | --- | --- | --- |
| A. Ephemeral mapping per request | Relation живёт только после одного confirmation и исчезает после ответа | Минимум retained data, но нет reusable accepted relation, current status, correction history или useful cross-request review | **DEFER как prototype; не выбран для usable v1** |
| B. Dedicated owner-reviewed operational state вне vault | Явная operational authority для accepted relation; source layers остаются canonical/derived и revalidated | Можно хранить только IDs/fingerprints, append-only correction, owner delete, bounded backup и fail-closed drift | **ACCEPT, выбранный вариант** |
| C. Новая canonical vault relation/field | Сделала бы mapping user evidence и потребовала бы schema/Safe Write/Vault Sync semantics | Смешивает asserted knowledge и service relation, увеличивает privacy и migration scope | **FORBIDDEN / DEFER** |
| D. Новая reviewed Personal Memory assertion | Выглядела бы как statement пользователя, хотя mapping — служебное отношение | Confuses relation with assertion и заставляет Safe Write менять canonical knowledge | **FORBIDDEN / DEFER** |
| E. Reuse Stage 9 operational store | Технически durable, но Stage 9 authority — prediction/audit/link/calibration | Разные owners, lifecycle, retention, deletion и meaning; создаёт feedback path | **FORBIDDEN** |

Ephemeral flow достаточен для демонстрации, но недостаточен для useful Stage
10C: owner confirmation нельзя переиспользовать, drift status нельзя увидеть
между запросами, а correction не имеет durable target. Поэтому принимается B.

Это решение не создаёт store сейчас. В Stage 10C логическая persistence
граница должна быть dedicated, bounded и вне vault/Stage 9; physical
implementation остаётся отдельным implementation gate.

### 9.2. Логический store contract

Будущий store использует отдельный application-owned operational data root:

- root явно передаётся application boundary и не имеет default внутри vault,
  repository, `web/static` или browser profile;
- store не входит в `second-brain-vault`, Git, Vault Sync или обычный vault
  backup;
- mapping accepted records и lifecycle events идут в отдельный append-only
  JSONL stream с atomic manifest, generation, sequence и record digest;
- один process-wide exclusive OS lock покрывает validation, sequence
  allocation, append, flush/fsync и read-back;
- current read строится из verified complete stream; malformed JSON, partial
  line, unknown field, duplicate mapping ID, sequence gap, generation/digest
  mismatch или manifest mismatch дают `MAPPING_STORE_CORRUPT`;
- heuristic salvage, silent tail truncation, automatic chain repair и
  last-write-wins запрещены;
- active mapping count и serialized record size bounded; v1 limits:
  `max_active_mappings = 200` и `max_mapping_record_bytes = 16384`;
- SQLite, vector DB, provider cache и network replication не являются частью
  v1 store design;
- multi-host/shared-filesystem concurrency не поддерживается без отдельного
  storage gate.

Accepted mapping history является non-rebuildable operational history: vault
не может восстановить, какую relation owner когда подтвердил. Composition
является rebuildable derived projection из verified mapping state и current
Stated/Behavioral source.

### 9.3. Lifecycle, retention, delete и backup

В v1 нет automatic TTL по `evidence_at`, `reviewed_at` или `created_at`.
Mapping active до explicit owner correction, supersession, invalidation или
delete. Stale state не удаляется и не становится current без exact
revalidation.

Любая correction/delete/reset:

1. append-ит fixed-shape lifecycle event;
2. не переписывает accepted mapping payload;
3. исключает superseded/invalidated/deleted mapping из active view;
4. может после verified tombstone выполнить bounded physical purge;
5. не меняет Personal Memory, Decision Journal, Outcome или Stage 9.

Operational backup не включается автоматически. Если owner отдельно включает
application backup, mapping data наследует owner-only access, retention,
delete/reset и no-public/no-cloud-by-default rules. Удаление не считается
завершённым, пока разрешённые local backup copies не обработаны или не
возвращён fixed safe error.

## 10. Exact mapping DTO

### 10.1. Immutable accepted record

~~~text
StatedObservedMappingV1 {
  contract_version:                  "stated-observed-mapping-v1"
  mapping_policy_id:                 "stated-observed-explicit-mapping-v1"
  mapping_policy_fingerprint:        MappingHashV1
  mapping_id:                        UUIDv7       # store-owned, never content-derived
  acceptance_operation_id_fingerprint: MappingHashV1
  created_at:                        RFC3339 aware UTC
  reviewed_at:                       RFC3339 aware UTC
  mapping_basis:                    "owner-explicit-stated-behavior-v1"
  stated:                            StatedAssertionIdentityV1
  behavioral:                        BehavioralComparisonIdentityV1
  mapping_fingerprint:               MappingHashV1
  supersedes_mapping_id:             UUIDv7 | null
}
~~~

`reviewed_at` — server-recorded time explicit confirmation. `created_at` —
server/store-recorded durable commit time. Client timestamps не принимаются
как evidence; implementation должен обеспечить `reviewed_at <= created_at`
либо fail closed при невозможном clock ordering.

`mapping_id` создаётся только store/backend после successful commit. Он не
вычисляется из body, labels, UUIDs, timestamps или fingerprint.

`acceptance_operation_id_fingerprint` поддерживает retry без хранения raw
operation token. Operation ID создаётся до acceptance, должен быть stable для
одной logical operation и не является mapping authority.

### 10.2. Mapping fingerprint

`mapping_fingerprint` вычисляется из canonical payload, не содержащего
`mapping_id`, operation fingerprint, `created_at`, `reviewed_at` или
`supersedes_mapping_id`:

~~~json
{"behavioral":{"behavioral_contract_version":"behavioral-self-model-v1","behavioral_derivation_version":"behavioral-self-model-derivation-v1","cohort":"<canonical BehavioralCohortIdentityV1>","comparison_subject":"current-exact-option-v1","option":"<canonical BehavioralOptionIdentityV1>","pattern_fingerprint":"<mapping hash>","pattern_state":"<Stage 10B state>","pattern_type":"<Stage 10B type>","policy_fingerprint":"<Stage 10B policy hash>","provenance_fingerprint":"<mapping hash>","source_count":0,"source_fingerprint":"<mapping hash>"},"contract_version":"stated-observed-mapping-v1","mapping_basis":"owner-explicit-stated-behavior-v1","mapping_policy_id":"stated-observed-explicit-mapping-v1","stated":"<canonical StatedAssertionIdentityV1>"}
~~~

`source_count` в реальном payload равен exact non-negative count, а не
placeholder. Canonical JSON rules совпадают с разделом 4. Mapping fingerprint
является integrity binding; same fingerprint не отменяет store-owned identity
и lifecycle.

### 10.3. Lifecycle projection

Accepted record immutable. Текущий lifecycle — derived store projection:

~~~text
MappingLifecycleViewV1 {
  mapping_id:       UUIDv7
  lifecycle_state:  "active" | "superseded" | "invalidated" | "deleted"
  record:           StatedObservedMappingV1
}
~~~

`lifecycle_state` не редактируется in place. Он получается из verified
append-only lifecycle events. A record with `lifecycle_state != active` не
может быть использован composition.

### 10.4. DTO invariants

- Все version/policy/basis values exact allowlisted strings.
- UUID fields — strict UUIDv7; mapping ID store-owned.
- All timestamps aware canonical UTC; evidence timestamps внутри stated/behavioral
  identity сохраняют `unknown` literally, где это разрешено source contract.
- `stated.dimension = preference`, `stated.domain == behavioral.cohort.domain`.
- `behavioral.option` принадлежит exact `behavioral.cohort` namespace по index
  и fingerprint.
- `mapping_fingerprint` соответствует exact content payload.
- `source_count` равен current accepted behavioral provenance count.
- No raw claim body, Journal body, Outcome body, option label, path, title,
  front matter, URL, Stage 9 payload, provider response or secret.
- Accepted mapping не имеет `save`, `update`, `apply` или arbitrary body
  mutation method.

## 11. Owner review flow

Будущий accepted mapping создаётся только следующей последовательностью:

1. Authenticated owner открывает bounded review operation.
2. Backend выполняет current canonical `scan -> build_report` и integrity gate.
3. Backend строит current Stage 4 result и current Stage 10B result с exact
   approved policies и одним bounded build context.
4. Backend разрешает current stated claim по source UUID и current exact
   Behavioral cohort/option по generated candidate; client values считаются
   selectors, а не authority.
5. Backend формирует bounded review projection.
6. Owner явно подтверждает ровно эту relation:

   ~~~text
   Я подтверждаю, что этот current Stated preference assertion
   относится к этому exact Behavioral cohort и этому exact option.
   ~~~

7. Непосредственно перед acceptance backend заново читает current source,
   перестраивает Stage 4/10B и сравнивает все stated/behavioral identity,
   policy и source fingerprints с review candidate.
8. Под dedicated store lock backend проверяет cardinality, lifecycle,
   operation idempotency и concurrency; только затем создаёт store-owned
   UUIDv7, append-ит immutable record, flush/fsync-ит, перечитывает и
   валидирует запись.
9. Только после successful durable validation возвращается accepted mapping.

Любой mismatch, unavailable source, changed policy, conflict, lost lock,
corruption или uncertain final reread означает отсутствие acceptance. Backend
не возвращает «примерно принятое» relation.

Browser не может:

- прислать body и объявить его current claim;
- прислать labels и объявить их option identity;
- прислать path, front matter, evidence или source timestamp;
- выбрать closest cohort;
- изменить `mapping_id`, policy, fingerprints или lifecycle;
- подтвердить mapping одним cached/replayed context без fresh backend review.

## 12. Human-readable review context

Projection для explicit current review может временно показать owner:

- exact Stated assertion text, dimension, domain и
  `evidence_at`/unknown marker;
- source UUID и bounded claim/source fingerprint;
- Behavioral cohort domain, cohort fingerprint и exact grouping summary;
- ordered option labels с их option index, exact option fingerprints и
  selected/mapped option;
- current/historical pattern type/state, support counts, total count и
  fixed caveats;
- indication, что labels показаны для human review, а не используются как
  automatic identity bridge.

Projection bounded existing limits Stage 4/Stage 10 и overall
`max_review_projection_bytes = 32768`. При превышении — `RESULT_TOO_LARGE`,
без truncation, silent omission или partial context.

Projection:

- существует только во время explicit owner review;
- не попадает в accepted mapping record, log, cache, browser local storage,
  provider payload или telemetry;
- не содержит full unrelated notes или Outcome bodies;
- не является authority даже после owner display;
- после review может быть отброшен, а relation сохраняется только как
  bounded identities/fingerprints.

## 13. Composition DTO и comparison input

### 13.1. Closed composition state

Ровно следующие states разрешены:

~~~text
aligned
divergent
stated_evidence_missing
behavioral_evidence_insufficient
not_comparable
~~~

### 13.2. StatedObservedCompositionResultV1

Будущий read-only result имеет bounded shape:

~~~text
StatedObservedCompositionResultV1 {
  contract_version:             "stated-observed-mapping-v1"
  derivation_version:           "stated-observed-composition-derivation-v1"
  mapping_policy_id:            "stated-observed-explicit-mapping-v1"
  mapping_policy_fingerprint:   MappingHashV1
  generated_at:                 RFC3339 aware UTC
  state:                        closed composition state
  reason_code:                  fixed safe code | null
  mapping_id:                   UUIDv7 | null
  mapping_fingerprint:          MappingHashV1 | null
  observed_option:              BehavioralOptionIdentityV1 | null
  behavioral_pattern_type:      Stage 10B pattern type | null
  behavioral_pattern_state:     Stage 10B pattern state | null
  caveats:                      tuple[fixed caveat code, ...]
}
~~~

Result не содержит raw Stated claim, raw Journal context или labels. Owner
может запросить bounded current review projection отдельно. `reason_code`
объясняет safe state, но не становится personality language или truth verdict.

### 13.3. Exact state meaning

| State | Exact condition | What it does not mean |
| --- | --- | --- |
| `aligned` | Active mapping current; Stated source current; Behavioral pattern has one current exact comparison subject; observed exact option identity equals mapped option identity. | Не означает objective truth, permanent preference или correct decision. |
| `divergent` | Active mapping current; same unambiguous comparison subject exists; observed exact option identity deterministically differs from mapped option identity. | Не означает lying, inconsistency, subconscious choice или personality change. |
| `stated_evidence_missing` | Full current scan succeeded, but approved mapping scope source UUID is absent; no replacement claim is selected. | Не означает, что у owner вообще нет stated assertions. |
| `behavioral_evidence_insufficient` | Active mapping and exact target remain valid/current, but current mapped cohort pattern is `insufficient_evidence`; binary relation is withheld. | Не означает absence of preference or negative behavior. |
| `not_comparable` | No active accepted mapping, unsupported dimension/scope, stale/changed identity, historical-only subject, mixed/changed ambiguity or any other unproven relation. | Не означает divergent или evidence of mismatch. |

## 14. Deterministic composition precedence

Composition evaluates one current build in this order:

| Priority | Condition | Behavior |
| --- | --- | --- |
| 0 | Invalid request, malformed clock or unknown mapping policy | Fixed error; no vault/store read beyond required validation. |
| 1 | Mapping store unavailable/corrupt or complete current canonical source unavailable | Fixed error; no composition result and no partial projection. |
| 2 | Current Stage 4/10B result violates policy, DTO or byte bounds | `POLICY_MISMATCH` or `RESULT_TOO_LARGE`; no state. |
| 3 | No active mapping exists | `not_comparable` with `STATED_OBSERVED_MAPPING_MISSING`; this is the default. |
| 4 | More than one active mapping violates injective cardinality | `AMBIGUOUS_MAPPING`; no mapping is selected. |
| 5 | Mapping record/lifecycle/fingerprint is malformed | `MAPPING_INVALID`; no composition. |
| 6 | Approved Stated source UUID is absent after a complete scan | `stated_evidence_missing` with `STATED_EVIDENCE_MISSING`; no retarget. |
| 7 | Source exists but stated identity, dimension, domain, claim/source fingerprint or Stage 4 policy changed | `not_comparable` with `STATED_SOURCE_CHANGED` or `MAPPING_STALE`. |
| 8 | Behavioral cohort, option namespace, option index/fingerprint, source/provenance or Stage 10 policy changed | `not_comparable` with exact drift reason; no closest replacement. |
| 9 | Current pattern is `insufficient_evidence` and exact mapping identity still matches | `behavioral_evidence_insufficient`; no binary verdict. |
| 10 | Current pattern is `mixed_exact_choices`, `changed_over_time`, historical-only repeated, or `not_comparable` | `not_comparable`; no majority and no temporal flattening. |
| 11 | Current pattern is `repeated_exact_choice/current` or `stable_over_time/stable` with one exact subject | Compare exact `(option_index, option_fingerprint)`: equal -> `aligned`, unequal -> `divergent`. |

Steps 6–8 are intentionally before binary comparison. A stale relation never
becomes `divergent` merely because a current label or option changed.

## 15. mixed_exact_choices semantics

`mixed_exact_choices` means that one exact cohort has at least two distinct
chosen option identities and does not prove one binary observed subject.

Therefore:

- `mixed != divergent`;
- support counts remain descriptive and option-index ordered;
- no majority, winner, dominant behavior, `mostly aligned` или `mostly
  divergent` is computed;
- a mapping accepted against a previous exact pattern becomes
  `not_comparable / MAPPING_STALE` if current source revalidation produces
  mixed pattern;
- a new mapping cannot be accepted to mixed pattern in v1;
- no label similarity can collapse mixed options.

## 16. changed_over_time semantics

`changed_over_time` contains exact different unanimous choices in the
historical and current windows. It is not a global statement about the user.

V1 policy:

- current Stated claim is compared only to a deterministic current subject;
- historical choice is shown only as bounded temporal context;
- a current pattern whose state is `changed` is not an accepted v1 comparison
  subject;
- a mapping that was accepted before this source/pattern transition becomes
  `not_comparable / MAPPING_STALE`, never automatic `divergent`;
- no majority over windows, no `mostly`, no trend score and no claim «ты
  изменился», «раньше был честнее» или «теперь делаешь не то, что хочешь»;
- resolving a changed pattern requires a new future temporal/composition
  policy or a fresh exact relation with semantics outside this v1 binary gate.

`stable_over_time` is different: if current and historical exact choices are
the same, current subject remains deterministic. `aligned/divergent` in that
case describes only the current exact option; it does not turn historical
stability into a personality verdict.

## 17. Temporal policy

### 17.1. Separate times

The contract keeps these times distinct:

| Time | Meaning | Can replace another time? |
| --- | --- | --- |
| `stated.evidence_at` | When the owner assertion was reported, exact or literal unknown | No |
| Behavioral observation `evidence_at` | When reviewed Decision Journal choice occurred | No |
| Stage 10B `generated_at` | Build clock used for current/historical window membership | No |
| Mapping `reviewed_at` | Server time explicit owner confirmation | No |
| Mapping `created_at` | Durable operational record time | No |

Mapping review/creation time никогда не backdates assertion or decision.
Unknown Stated evidence time never receives `created`, `updated`,
`reviewed_at`, UUID time или any invented timestamp.

### 17.2. Current comparison boundary

Stage 10B current window remains:

~~~text
[generated_at - 90 days, generated_at]
~~~

`aligned/divergent` сравнивают current Stated source с current Behavioral
subject only. They do not assert that the assertion and choice occurred at the
same instant or that one caused the other.

`stable_over_time` may use historical window only to prove the Stage 10B
stable state. `changed_over_time`, historical-only repeated evidence and
unknown/future/outside-horizon data cannot supply a binary current subject.

Fixed composition caveats:

~~~text
current_source_revalidated
review_time_is_not_evidence_time
temporal_alignment_not_proven
stated_evidence_time_unknown       # only when applicable
historical_context_not_binary       # changed/stable context when applicable
~~~

Adding or renaming temporal caveats is a policy/derivation change.

## 18. Drift and fail-closed behavior

| Drift event | Exact reaction |
| --- | --- |
| Stated source body or claim projection edited | `not_comparable / STATED_SOURCE_CHANGED`; no old body restore and no new claim retarget. |
| Stated source enrolled metadata, dimension, domain, evidence time/precision or policy changed | `not_comparable / STATED_SOURCE_CHANGED` or `UNSUPPORTED_STATED_DIMENSION`. |
| Stated source UUID deleted or unavailable after complete scan | `stated_evidence_missing`; no replacement UUID. |
| Stage 4 claim fingerprint changes | `not_comparable / STATED_SOURCE_CHANGED`. |
| Behavioral cohort fingerprint changes | `not_comparable / BEHAVIORAL_COHORT_CHANGED`; no closest cohort. |
| Behavioral option namespace, index or fingerprint changes | `not_comparable / BEHAVIORAL_OPTION_CHANGED`; no label bridge. |
| Behavioral policy, contract or derivation changes | `POLICY_MISMATCH` or `MAPPING_STALE`; no old policy reuse. |
| Journal source is edited, deleted, added to mapped cohort or current snapshot changes | `not_comparable / MAPPING_STALE`; re-review required. |
| Behavioral pattern changes to mixed/changed/historical-only | `not_comparable / MAPPING_STALE`; never divergent by temporal shortcut. |
| Outcome body/presence changes | Stage 10A snapshot drift can make mapping stale; after fresh review outcome remains presence-only and does not weight composition. |
| Search/index/cache/browser payload changes | Ignored as authority; current source is reread. |

Stale means «этот accepted record больше не подтверждён этим exact current
source». It does not mean that a replacement relation is inferred. A future
exact restoration may only pass ordinary exact revalidation; it is never a
retarget or similarity match, and an explicitly invalidated record never
resurrects.

## 19. Correction and invalidation

### 19.1. Mapping correction

Accepted mapping payload is immutable. To correct a relation:

1. owner starts a fresh review with current Stated/Behavioral rebuild;
2. backend requires explicit new confirmation;
3. backend appends lifecycle supersession/tombstone for old mapping;
4. backend creates a new store-owned `mapping_id`;
5. new mapping carries `supersedes_mapping_id` and its own fingerprints;
6. current composition uses only the new active record.

Old mapping is not silently rewritten, and its `mapping_fingerprint`,
`reviewed_at`, operation binding and source identity remain historical
operational data until owner delete/purge.

### 19.2. Source correction

Если owner считает wrong саму Stated assertion, correction выполняется только
через existing reviewed Personal Memory/Safe Write path. Mapping correction
сама по себе не изменяет assertion и не является canonical evidence.

Если owner считает wrong Behavioral evidence, correction идёт через current
reviewed Decision Journal/Outcome source rules. Composition не редактирует
Journal.

### 19.3. Explicit invalidation/delete

Owner disagreement with relation создаёт explicit mapping invalidation или
новую reviewed mapping; он не создаёт автоматически новую Personal Memory.
Owner delete/reset append-ит fixed lifecycle event, после чего возможен
verified bounded purge. Runtime read никогда не выполняет implicit delete,
reset или correction.

## 20. Idempotency and concurrency

Эти требования применяются потому, что выбран persistent operational store.

### 20.1. Idempotent acceptance

- `operation_id` создаётся до owner acceptance и имеет bounded UUIDv7 form;
- store сохраняет только `acceptance_operation_id_fingerprint`;
- retry с тем же operation fingerprint и byte-equivalent exact candidate,
  source/policy/mapping fingerprints возвращает тот же mapping ID;
- retry с тем же operation fingerprint и любым отличающимся payload даёт
  `IDEMPOTENCY_CONFLICT` и не создаёт новую запись;
- retry без исходного operation ID является новой operation;
- нельзя deduplicate по labels, domain, времени, body similarity, UUID
  similarity, mapping fingerprint alone или `same-looking` context.

### 20.2. Conflicting owner actions

- Read validation, active-cardinality check, sequence allocation, append,
  flush/fsync и read-back находятся под одним store lock.
- Одновременная попытка создать conflicting active relation для одного stated
  source или cohort даёт `CONCURRENCY_CONFLICT`/`AMBIGUOUS_MAPPING`; система
  не выбирает победителя по времени или request order.
- Supersession допускается только для exact known active mapping ID и после
  fresh owner review.
- Concurrent supersession/invalidation старого mapping ID с несовместимым
  operation fingerprint требует повторной review и fail closed.
- Multi-process same-host поддерживается только указанным OS lock.
  Multi-host/shared filesystem deferred.

Если backend не может связать final current source reread и durable append в
один safe acceptance boundary, он не принимает relation. Partial commit,
lost response и uncertain lock state дают safe retry/idempotency lookup либо
fixed store error, но не duplicated mapping.

## 21. Privacy, security и sensitive-inference safeguards

### 21.1. Persistent data minimization

В mapping store разрешены только:

- store-owned mapping UUIDv7;
- explicit operation fingerprint;
- server lifecycle times;
- exact version/policy/basis identifiers;
- one Stated source UUID, dimensions, domain и source/claim fingerprints;
- exact Behavioral cohort/option fingerprints/indices;
- pattern/source/provenance fingerprints и bounded count;
- supersession/lifecycle references и fixed reason code.

Не сохраняются:

- full Stated claim/body;
- full Decision Journal, Outcome или Journal section bodies;
- raw option/criteria labels;
- paths, filenames, titles, front matter, source URLs;
- raw evidence bundles, UUID lists beyond the required single stated UUID;
- Stage 9 payload, prediction, calibration, provider/LLM data;
- credentials, tokens, cookies, auth headers, absolute paths;
- personality, hidden score, diagnosis, risk, protected-trait or manipulation
  inference.

### 21.2. Review and transport

Human-readable labels/body projection допустимы только transient в bounded
owner-only review surface. Future API uses same-origin/authenticated,
`no-store`, bounded response; browser local storage не является authority.

Client может передать selector и explicit confirmation, но backend обязан
resolve/rebuild every source. Client-provided raw body, labels, context,
fingerprints, timestamps или paths ignored/rejected as authority.

Errors/logs/telemetry используют только fixed safe code/message. Raw body,
path, label, UUID inventory, exception repr, secret и provider detail наружу
не попадают.

### 21.3. Allowed wording

Разрешён только bounded description exact relation:

~~~text
explicitly mapped stated preference
vs
observed exact option
in the current exact cohort/window
~~~

Запрещены формулировки:

- «это твоя настоящая preference»;
- «ты на самом деле хочешь»;
- «ты себе врёшь»;
- «ты непоследователен»;
- «подсознательно выбираешь»;
- «твоя личность/характер/риск»;
- любые personality, diagnostic или manipulation conclusions.

## 22. Stage 9 boundary

Stage 9 остаётся отдельным model-evaluation layer:

- `ProspectiveAuditEventV1` хранит validated Simulate Me operation;
- `ProspectiveDecisionLinkV1` хранит explicit prediction-to-decision linkage;
- prospective calibration хранит operation counts/ratios;
- ни один Stage 9 object не является Stated assertion, Behavioral
  observation или mapping authority.

Stage 9 не может:

- создать accepted Stated-vs-Observed mapping;
- подтвердить owner review;
- выбрать cohort/option;
- изменить mapping weight, confidence, alignment или divergence;
- повысить или понизить authority Behavioral evidence.

Stage 10C не читает Stage 9 store для composition и не переиспользует его
storage. Два stores могут иметь похожие append-only mechanics, но имеют разные
authority, owners, lifecycle, deletion и policy fingerprints.

## 23. Simulate Me boundary

Stage 10C0 не меняет Simulate Me policy, input, output, calibration или
provider boundary.

В частности, запрещено:

~~~text
aligned/divergent -> Simulate Me input
~~~

`aligned`/`divergent` не могут автоматически менять prediction, option order,
prompt, confidence или abstention. Future one-way consumption потребует
отдельного versioned Simulate Me policy, explicit provenance и calibration
baseline/reset gate.

## 24. Future Web/API boundary

Это design direction, не implementation в #252. После отдельного Stage 10C/10D
approval возможны следующие owner-only operations:

1. `review current mapping`: backend rebuilds current Stated/Behavioral
   projections and returns bounded transient context;
2. `accept mapping`: client sends operation ID, exact candidate selectors and
   explicit confirmation; backend rereads/revalidates and durably commits;
3. `read composition`: backend reads active mapping, rebuilds current sources
   and returns `StatedObservedCompositionResultV1`;
4. `supersede/invalidate/delete`: explicit owner operation with new lifecycle
   event and no in-place update.

API не должен:

- принимать arbitrary body/context as truth;
- auto-suggest accepted mapping by label equality;
- хранить relation в browser;
- выдавать composition без current source revalidation;
- делать background mapping, automatic mapping or silent refresh;
- писать в vault, Stage 9 store или Personal Memory.

## 25. Error/state taxonomy

### 25.1. Fixed safe codes

Полный error vocabulary v1:

~~~text
STATED_OBSERVED_MAPPING_INVALID_REQUEST
STATED_OBSERVED_MAPPING_STATED_SOURCE_UNAVAILABLE
STATED_OBSERVED_MAPPING_STATED_SOURCE_CHANGED
STATED_OBSERVED_MAPPING_UNSUPPORTED_STATED_DIMENSION
STATED_OBSERVED_MAPPING_BEHAVIORAL_SOURCE_UNAVAILABLE
STATED_OBSERVED_MAPPING_BEHAVIORAL_COHORT_CHANGED
STATED_OBSERVED_MAPPING_BEHAVIORAL_OPTION_CHANGED
STATED_OBSERVED_MAPPING_BEHAVIORAL_EVIDENCE_INSUFFICIENT
STATED_OBSERVED_MAPPING_MISSING
STATED_OBSERVED_MAPPING_INVALID
STATED_OBSERVED_MAPPING_STALE
STATED_OBSERVED_MAPPING_AMBIGUOUS
STATED_OBSERVED_MAPPING_POLICY_MISMATCH
STATED_OBSERVED_MAPPING_NOT_COMPARABLE
STATED_OBSERVED_MAPPING_RESULT_TOO_LARGE
STATED_OBSERVED_MAPPING_STORE_UNAVAILABLE
STATED_OBSERVED_MAPPING_STORE_CORRUPT
STATED_OBSERVED_MAPPING_IDEMPOTENCY_CONFLICT
STATED_OBSERVED_MAPPING_CONCURRENCY_CONFLICT
STATED_OBSERVED_MAPPING_STATED_EVIDENCE_MISSING
~~~

Каждый error имеет только fixed `code` и safe bounded `message`. Message не
содержит raw path, body, label, UUID list, exception repr, secret или provider
detail. `BEHAVIORAL_EVIDENCE_INSUFFICIENT` и
`STATED_EVIDENCE_MISSING` являются reason/state vocabulary; для composition
они проецируются в соответствующие closed states.

### 25.2. Safe mapping of conditions

| Condition | Safe code/state |
| --- | --- |
| No accepted mapping | `STATED_OBSERVED_MAPPING_MISSING` -> `not_comparable` |
| Unsupported belief/goal/other dimension | `STATED_OBSERVED_MAPPING_UNSUPPORTED_STATED_DIMENSION` -> `not_comparable` |
| Complete scan cannot read source | `STATED_OBSERVED_MAPPING_STATED_SOURCE_UNAVAILABLE` -> no result |
| Stated source UUID absent after complete scan | `STATED_OBSERVED_MAPPING_STATED_EVIDENCE_MISSING` -> `stated_evidence_missing` |
| Stated identity changed | `STATED_OBSERVED_MAPPING_STATED_SOURCE_CHANGED` -> `not_comparable` |
| Behavioral source/rebuild unavailable | `STATED_OBSERVED_MAPPING_BEHAVIORAL_SOURCE_UNAVAILABLE` -> no result |
| Cohort identity changed | `STATED_OBSERVED_MAPPING_BEHAVIORAL_COHORT_CHANGED` -> `not_comparable` |
| Option namespace/index/fingerprint changed | `STATED_OBSERVED_MAPPING_BEHAVIORAL_OPTION_CHANGED` -> `not_comparable` |
| Current Stage 10B evidence insufficient under current identity | `STATED_OBSERVED_MAPPING_BEHAVIORAL_EVIDENCE_INSUFFICIENT` -> `behavioral_evidence_insufficient` |
| Pattern mixed/changed/historical-only | `STATED_OBSERVED_MAPPING_NOT_COMPARABLE` -> `not_comparable` |
| Accepted mapping fingerprint/source/policy no longer matches | `..._STALE` or `..._POLICY_MISMATCH` -> `not_comparable` / no result |
| Active cardinality conflict | `STATED_OBSERVED_MAPPING_AMBIGUOUS` -> no result |
| Store lock, flush, permission or availability failure | `..._STORE_UNAVAILABLE` -> no result |
| Store chain/schema/manifest corruption | `..._STORE_CORRUPT` -> no result |
| Same operation, different payload | `..._IDEMPOTENCY_CONFLICT` -> no new record |
| Concurrent conflicting action | `..._CONCURRENCY_CONFLICT` -> fresh review required |
| Complete result/review exceeds bound | `..._RESULT_TOO_LARGE` -> no truncation |

## 26. ACCEPT / CHANGE / RISK / DEFER

| Area | Verdict | Decision |
| --- | --- | --- |
| Canonical authority | **ACCEPT** | Vault current `scan -> build_report` remains the only user-evidence source. |
| Stated authority | **ACCEPT** | Current Stage 4 direct `preference` claim plus exact current source identity. |
| Behavioral authority | **ACCEPT** | Current Stage 10A/10B exact cohort/option/pattern and policy fingerprints. |
| Mapping authority | **ACCEPT** | Explicit owner-reviewed relation, committed only by backend after immediate revalidation. |
| Semantic/fuzzy matching | **ACCEPT / FORBIDDEN** | Never creates or validates a relation. |
| Cardinality | **ACCEPT** | Injective one stated -> one cohort -> one option; conflicts fail closed. |
| Eligible dimension | **ACCEPT** | `preference` only with exact non-null domain; belief/goal are unsupported. |
| Persistence | **ACCEPT** | Dedicated bounded operational relation store outside vault and Stage 9. |
| Physical storage | **DEFER** | Logical append-only local JSONL boundary is approved; concrete adapter/integration is Stage 10C implementation. |
| Canonical schema / vault | **DEFER / FORBIDDEN** | No new fields, notes, Safe Write or vault changes. |
| Owner review context | **ACCEPT** | Bounded transient context can show human text; it is not identity authority or persisted payload. |
| Composition states | **ACCEPT** | Exact five-state closed set and precedence; aligned/divergent are relation states only. |
| Mixed behavior | **ACCEPT** | Mixed is never divergent and never majority-resolved. |
| Changed-over-time | **ACCEPT** | Current-only comparison; changed pattern is not a binary subject in v1. |
| Temporal semantics | **ACCEPT** | Review/creation time never replaces evidence time; unknown remains unknown. |
| Drift | **ACCEPT** | Any current identity/policy/source mismatch fails closed; no automatic retarget. |
| Correction | **ACCEPT** | Append-only supersession/invalidation/new mapping ID; source correction remains Safe Write. |
| Idempotency/concurrency | **ACCEPT** | Explicit operation fingerprint, store lock and conflict fail-closed behavior. |
| Stage 9 | **ACCEPT** | Separate authority and storage; no prediction/calibration feedback path. |
| Simulate Me | **DEFER** | No Stage 10C input change; future consumer requires new policy/calibration gate. |
| Web/API/UI | **DEFER** | Future owner-only, no-store surface; no implementation in #252. |
| Provider/LLM/embedding | **DEFER / FORBIDDEN** | Not used for relation or composition. |
| Privacy | **ACCEPT** | Bounded UUID/fingerprint relation with only the exact domain needed for revalidation; raw context is transient and owner-only. |
| Runtime | **CHANGE** | Stage 10C0 contract is designed/approved; Stage 10C runtime remains not implemented. |
| Roadmap | **CHANGE** | Status must distinguish Stage 10A/B complete, Stage 10C0 design complete, runtime not started. |

### 26.1. Known risks

1. Exact fingerprints deliberately produce false negatives after harmless-looking
   source edits. This is safer than semantic retargeting and visible as stale.
2. Owner review is required again after current source/pattern drift; this costs
   interaction but prevents silent relation changes.
3. A persistent operational relation is sensitive metadata even without bodies;
   owner-only access, bounded retention/delete and separate backup are mandatory.
4. Stage 10B snapshot includes outcome presence, so an Outcome edit can require
   mapping re-review even though Outcome never affects choice support.
5. Current vault has no historical snapshot. The system can prove current
   source identity, not reconstruct old composition results after source edit.
6. Local JSONL operational storage needs correct OS locking, fsync, chain
   validation and corruption handling on every supported platform.

### 26.2. Deferred gates

Без `HUMAN_REQUIRED` в этой design-only scope остаются:

- physical store adapter, operational root configuration, backup/retention
  implementation and deployment permissions;
- authentication/authorization and Web/API/UI;
- any many-to-many, belief/goal, cross-domain or temporal-history semantics;
- canonical schema relation or new Personal Memory type;
- Stage 9/Simulate Me consumer or calibration use;
- provider, LLM, embedding or semantic candidate generation.

`DEFER` здесь означает отдельный future gate, а не скрытое разрешение
реализовать решение сейчас.

## 27. Exact next Stage 10C implementation scope

После отдельного implementation approval Stage 10C может реализовать только:

1. `StatedAssertionIdentityV1`, `BehavioralComparisonIdentityV1`,
   `StatedObservedMappingV1`, lifecycle view и exact validators;
2. canonical fingerprint functions и fixed policy binding из этого документа;
3. current Stage 4/10A/10B rebuild orchestration без изменения их parser,
   DTO, source authority или historical semantics;
4. owner-only review application boundary с bounded transient projection,
   explicit confirmation и backend immediate revalidation;
5. dedicated mapping operational store boundary: append-only local JSONL,
   manifest/chain, bounds, OS lock, flush/fsync/read-back, idempotency,
   supersession/tombstone, corruption and safe errors;
6. deterministic composition precedence and
   `StatedObservedCompositionResultV1`;
7. focused temporary-store/current-vault tests for:
   - exact stated identity and source/claim fingerprint drift;
   - exact cohort/option/policy/source/pattern drift;
   - one-to-one cardinality, missing/ambiguous mapping and conflicting retry;
   - explicit review requirement and client non-authority;
   - aligned/divergent only for current exact subject;
   - mixed, changed, historical-only and insufficient behavior;
   - unknown stated time without invented timestamp;
   - edit/delete/rebuild and append-only correction;
   - no raw bodies/labels/paths/provider/Stage 9 data in records/results;
   - no vault write, schema migration, Safe Write, Stage 9 or Simulate Me change.

Stage 10C must not implement:

- fuzzy/semantic/LLM/embedding matching;
- automatic mapping or label equality authority;
- belief/goal mapping;
- Web/API/UI unless separately allocated to Stage 10D;
- provider/network/credentials;
- new canonical fields/note kinds/schema;
- changes to Stage 2 parser, Stage 4, Stage 9, Simulate Me or
  `second-brain-vault`;
- Stage 10D, Stage 11+ or next-Issue creation.

## 28. Scope and acceptance checklist

- [x] Exact stated source/claim identity is source-anchored and versioned.
- [x] Exact behavioral cohort/option/policy/pattern/source identity is defined.
- [x] Raw body is not the sole identity and is not persistent mapping payload.
- [x] Accepted mapping requires explicit owner review and immediate backend
  current-source revalidation.
- [x] Cardinality is injective one stated -> one cohort -> one option.
- [x] Only preference with exact non-null domain is eligible in v1.
- [x] Persistence is dedicated operational state, not canonical vault or Stage 9.
- [x] `mapping_id` is store-owned UUIDv7 and is not content-derived.
- [x] Mapping fingerprint, operation idempotency and lifecycle correction are
  exact and append-only.
- [x] Default without accepted relation is `not_comparable`.
- [x] `aligned`/`divergent` require current exact deterministic subject.
- [x] Mixed and changed behavior never become automatic divergence.
- [x] Temporal review/evidence/generated times remain separate.
- [x] Drift, delete, correction and invalidation fail closed without retarget.
- [x] Privacy, no raw context persistence and manipulation safeguards are fixed.
- [x] Stage 9 and Simulate Me remain separate.
- [x] Future Web/API is owner-only design, not implementation.
- [x] Exact Stage 10C implementation scope is documented but not started.

~~~text
runtime mapping/composition: NOT IMPLEMENTED
mapping store: NOT IMPLEMENTED
Web/API/UI: NOT IMPLEMENTED
schema change: NO
provider/network change: NO
vault change: NO
Stage 10C runtime started: NO
HUMAN_REQUIRED: none
~~~
