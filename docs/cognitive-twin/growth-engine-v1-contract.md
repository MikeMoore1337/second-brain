# Cognitive Twin v2 / Stage 11 — Growth Engine v1

Статус документа: **DESIGN / NORMATIVE CONTRACT ONLY**. Этот документ
закрывает design gate Issue #258 и остаётся нормативной основой для
детерминированного Growth Engine. Stage 11A и Stage 11B runtime, mapping store
и friction read model уже реализованы отдельными merged slices (#260 и #263);
этим документом не создаются новые runtime, Web/API/UI, provider integration,
schema changes или изменения second-brain-vault. Privacy/payload/provenance
boundary будущего Advisor зафиксирована отдельно в
[growth-advisor-v1-contract.md](growth-advisor-v1-contract.md).

Контракт читается поверх:

- [design-roadmap-v1.md](design-roadmap-v1.md);
- [self-model-v1-contract.md](self-model-v1-contract.md);
- [behavioral-self-model-v1-contract.md](behavioral-self-model-v1-contract.md);
- [stated-observed-mapping-v1-contract.md](stated-observed-mapping-v1-contract.md);
- [assistant-v1-contract.md](assistant-v1-contract.md);
- [compare-v1-contract.md](compare-v1-contract.md);
- текущих Stage 4, Stage 7 и Stage 10 application contracts/runtime.

При расхождении merged contract имеет приоритет над этим Issue. Любое
изменение принятой Stage 1–10 semantics требует отдельного contract gate.

В этом документе:

- **ACCEPT** — нормативно принято для Growth Engine v1;
- **CHANGE** — минимальная status/roadmap коррекция без переписывания
  исторической semantics;
- **RISK** — известное ограничение, которое нельзя скрывать эвристикой;
- **DEFER** — отдельный будущий owner-approved gate;
- **HUMAN_REQUIRED** — только реальный внешний блокер, который нельзя безопасно
  оставить в DEFER.

## 1. Purpose и non-goals

### Purpose

Growth Engine отвечает только на вопрос:

~~~text
Куда пользователь явно хочет прийти
и как его current reviewed choices / observed behavior /
optional independent recommendation относятся к этой явно заданной цели?
~~~

V1 строится как раздельная цепочка:

~~~text
current Stage 4 reviewed goal
  -> exact Goal identity
  -> optional owner-reviewed exact Goal-to-choice relation
  -> current Stage 10 behavioral pattern
  -> bounded relation state
~~~

Эта цепочка описывает provenance и exact relation. Она не выносит моральный,
психологический или объективный verdict.

### Non-goals

Growth Engine v1 не отвечает:

- чего пользователь на самом деле должен хотеть;
- какой человек пользователь и какова его «настоящая сущность»;
- хорошо или плохо пользователь себя ведёт;
- насколько пользователь дисциплинирован, мотивирован или силён волей;
- является ли привычка правильной, успешной, полезной или вредной;
- какой вариант объективно лучший или growth-optimal;
- был ли outcome успехом, failure, reward, regret, benefit или harm;
- какой hidden decision rule, trait или vulnerability стоит за behavior.

В scope этого design gate не входят:

- Stage 11 runtime, mapping runtime/store и Growth read model;
- Web/API/UI, background job, scheduler или automatic provider call;
- LLM/embedding/semantic/fuzzy goal mapping;
- automatic private-goal injection в Assistant;
- новая canonical note taxonomy, field, schema migration или Safe Write;
- Goal Progress/structured Outcome schema;
- изменения Stage 9, Stage 10, Simulate Me, Compare или Active Learning;
- изменения second-brain-vault;
- автоматическое создание следующих Issues.

## 2. Terminology и главный invariant

| Layer | Exact meaning | Authority |
| --- | --- | --- |
| Goal | Current explicit reviewed stated goal | Stage 4 direct claim с current canonical source |
| Observed / habitual | Exact current Behavioral Self Model evidence | Stage 10A/10B current rebuild |
| Likely self-choice | Что пользователь, вероятно, выбрал бы | Simulate Me, только если capability отдельно вызвана |
| Independent recommendation | Отдельная recommendation/analysis ветка | Assistant/Advisor policy, только при explicit request |
| Goal-to-choice mapping | Owner-reviewed relation для конкретной goal и exact option | Отдельная Growth operational policy |
| Growth relation | Bounded relation state из exact current sources | Derived result, не canonical fact |

Ни один layer не переписывает другой. В частности, запрещены следующие
автоматические тождества:

~~~text
habitual choice = good
habitual choice = bad
goal = objectively correct
Assistant recommendation = objective truth
Simulate Me prediction = goal-optimal choice
behavioral divergence = weakness / failure
~~~

## 3. Authority matrix

| Source / object | Authority в Growth v1 | Разрешённая роль | Запрещённое повышение authority |
| --- | --- | --- | --- |
| second-brain-vault через current scan -> build_report | Единственный canonical source user evidence | Current enrolled notes и validated Stage 2 projections | Search, cache, browser payload или прошлый result не заменяют source |
| Stage 4 SelfModelClaim dimension=goal | Canonical-derived goal projection | Goal identity после current reread и policy validation | Claim не становится objective truth и не выбирается по newest/majority |
| Stage 10 BehavioralPattern | Current behavioral observation | Exact cohort, option, pattern state и provenance | Pattern не становится preference, goal, recommendation или trait |
| Explicit Growth mapping | Operational relation authority | Owner-reviewed relation для exact goal/cohort/option | Mapping не меняет goal, Journal или Behavioral Model |
| Stage 10C mapping | Отдельная preference-only authority | Может существовать рядом, но не участвует автоматически | Preference mapping нельзя молча считать Goal mapping |
| Simulate Me | Independent derived prediction | Optional future branch reference | Prediction не является desirable choice или Growth authority |
| Assistant / Advisor | Independent recommendation branch | Optional future, explicit request only | Output не canonical truth, не user evidence и не growth-optimal |
| Stage 9 audit/calibration | Отдельный model-evaluation layer | Никакой Growth relation authority | Accuracy, match, mismatch и calibration не меняют Growth relation |
| Outcome Observation | Descriptive metadata | Только outcome presence, если оно уже есть в Stage 10 ref | Free text не становится progress/reward/utility |
| Client/browser | Transport и explicit owner action | Bounded selector и confirmation | Body, labels, timestamps и fingerprints клиента не являются truth |

## 4. Goal authority и current semantics

### 4.1. Допустимый источник Goal

Goal принимается только из current Stage 4 direct assertion claim, который
одновременно:

1. построен из current canonical scan -> build_report;
2. имеет current supporting UUIDv7;
3. имеет evidence_kind=explicit_user_fact или user_statement;
4. имеет self_kind=goal и derived dimension=goal;
5. прошёл текущие Stage 4 metadata, body и report integrity checks;
6. находится в SelfModelResult с approved Stage 4 policy binding;
7. не является SearchHit, cache, prediction, Assistant output, mapping или
   model inference.

Goal не выводится из observed choices, preference mapping, Stage 9, outcome,
Assistant или Simulate Me.

### 4.2. Что значит current

Current означает: source существует в свежем полном current scan и claim
можно заново построить по current Stage 4 policy. Это не означает:

- самый новый evidence_at;
- самый новый created/updated;
- «самую важную» цель;
- automatic supersession другой goal;
- отсутствие competing goals.

Stage 4 v1 не вводит automatic stale, supersede, precedence или recency
authority. Growth Engine не добавляет эти semantics задним числом.

Edit, delete, invalid metadata или changed Stage 4 policy обрабатываются
fail-closed:

- удалённый source не заменяется похожей note;
- изменённый source не retarget-ится на новый claim;
- fingerprint drift не становится conflict;
- result либо содержит fixed missing/changed state, либо возвращает safe error
  при недоступности полного source.

### 4.3. evidence_at

Stage 4 разрешает literal evidence_at=unknown с
evidence_at_precision=unknown. Growth identity сохраняет unknown буквально.
Запрещено подставлять created, updated, UUIDv7 timestamp, mapping time или
generated_at.

Unknown evidence time не блокирует существование Goal identity. Он блокирует
только temporal claim о совпадении времени goal и behavior; result обязан
добавить fixed caveat goal_evidence_time_unknown.

## 5. GrowthGoalIdentityV1

### 5.1. Exact shape

~~~text
GrowthHashV1 = "sha256:" + 64 lowercase hexadecimal characters

GrowthGoalIdentityV1 {
  source_note_uuid:              UUIDv7
  dimension:                     "goal"
  source_evidence_kind:          "explicit_user_fact" | "user_statement"
  source_self_kind:              "goal"
  domain:                        string | null
  evidence_at:                   RFC3339 UTC | "unknown"
  evidence_at_precision:         "exact" | "unknown"
  source_contract_version:       "self-model-v1"
  source_derivation_version:     "self-model-derivation-v1"
  self_model_policy_fingerprint: 64 lowercase hexadecimal characters
  source_fingerprint:            GrowthHashV1
  claim_fingerprint:             GrowthHashV1
}
~~~

Все UUID, hashes, policy values и temporal values проходят strict validation.
Raw goal body не входит в identity. Human-readable goal text разрешён только
как bounded transient owner-facing projection во время explicit review.

### 5.2. Fingerprint binding

Новые Growth fingerprints используют canonical UTF-8 JSON:

- ensure_ascii=false;
- separators ровно comma и colon;
- sort_keys=true;
- без BOM, whitespace и dynamic keys;
- SHA-256 в форме GrowthHashV1.

claim_fingerprint строится из exact Stage 4 claim projection:

~~~json
{"claim_text_fingerprint":"<H(exact UTF-8 claim bytes)>","dimension":"goal","domain":"<string or null>","source_contract_version":"self-model-v1","source_derivation_version":"self-model-derivation-v1","source_note_uuid":"<UUIDv7>"}
~~~

source_fingerprint строится из current validated source projection:

~~~json
{"claim_fingerprint":"<claim fingerprint>","domain":"<string or null>","evidence_at":"<canonical UTC or unknown>","evidence_at_precision":"<exact or unknown>","evidence_kind":"<explicit_user_fact or user_statement>","self_kind":"goal","source_contract_version":"self-model-v1","source_note_uuid":"<UUIDv7>"}
~~~

Storage-only fields created, updated, path, filename и UUID embedded timestamp
не являются evidence или identity. Изменение body, enrollment, domain,
evidence time/precision, Stage 4 derivation или policy меняет binding.

## 6. Multiple goals и selection policy

Simultaneous current goals разрешены. V1 не вводит hidden ranking, weight,
priority, scalar utility, average fit или overall life score.

Будущий bounded request имеет только два режима:

~~~text
GrowthGoalSelectionV1 {
  mode: "selected_goal" | "each_current_goal"
  source_note_uuid: UUIDv7 | null
}
~~~

- selected_goal требует explicit source_note_uuid; backend заново разрешает
  именно этот UUID;
- each_current_goal возвращает отдельный result на каждую eligible goal в
  deterministic UUID order, без aggregate;
- client selector не является authority: source UUID и claim заново проверяются
  по current scan;
- duplicate/same-domain goals не deduplicate-ятся по text или domain;
- conflicting goals остаются отдельными results;
- если owner не выбирает focus, consumer получает per-goal result либо
  fixed GOAL_SELECTION_REQUIRED, но не implicit priority;
- если один option supports одну goal и conflicts с другой, эти results не
  схлопываются.

## 7. Goal-to-choice relation policy

### 7.1. Closed relation vocabulary

V1 принимает ровно три relation values:

~~~text
supports_goal
conflicts_with_goal
neutral_or_unknown
~~~

Их meaning ограничен explicit reviewed mapping:

| Relation | Нормативный meaning | Что relation не означает |
| --- | --- | --- |
| supports_goal | Owner явно подтвердил, что этот exact option относится к выбранной goal как поддерживающий в данном exact scope | Не optimal, не necessary, не success и не objective benefit |
| conflicts_with_goal | Owner явно подтвердил, что этот exact option относится к выбранной goal как конфликтующий в данном exact scope | Не failure, self-sabotage, weakness, bad choice или trait |
| neutral_or_unknown | Owner явно оставил exact relation нейтральной или неизвестной | Не отсутствие goal и не доказательство безопасности/вреда |

Не допускаются relation names good_choice, bad_choice, success_choice,
self_sabotage, disciplined_choice, weak_choice или их эквиваленты.

### 7.2. Exact target

Mapping всегда связывает:

~~~text
one exact current Goal identity
  -> one exact Stage 10 behavioral cohort
  -> one exact option identity
  -> one explicit relation
~~~

Не разрешены label equality, lexical similarity, alias, translation, fuzzy
matching, embedding, LLM semantic mapping или closest replacement. Option
index принимается только вместе с exact option fingerprint внутри exact option
namespace.

Для accepted Goal mapping domain обязан быть exact non-null и равняться
behavioral_target.cohort.domain. Goal identity с domain=null остаётся valid
Goal evidence, но не получает Goal-to-choice relation в v1; cross-domain и
missing-domain bridging дают not_comparable.

Если current observed option отличается от mapped option, отсутствие mapping
для observed option не является conflicts_with_goal. Для conflict нужна
отдельная explicit relation именно к observed option. Отсутствие relation
проецируется в goal_mapping_missing.

### 7.3. Owner review boundary

Accepted relation возможна только после:

1. current canonical scan и Stage 4 goal rebuild;
2. current Stage 10 exact cohort/option rebuild;
3. bounded transient review, где owner видит exact goal projection и option;
4. explicit confirmation именно этой relation;
5. непосредственной повторной revalidation всех source/policy identities;
6. атомарной проверки mapping lifecycle, cardinality, idempotency и lock.

Browser/body/label/fingerprint клиента не может заменить backend reread.

## 8. GrowthGoalChoiceMappingV1 и cardinality

### 8.1. Behavioral target identity

~~~text
GrowthBehavioralTargetIdentityV1 {
  behavioral_contract_version:   "behavioral-self-model-v1"
  behavioral_derivation_version: "behavioral-self-model-derivation-v1"
  observation_version:            "behavioral-observation-v1"
  policy_id:                      "behavioral-self-model-exact-context-v1"
  policy_fingerprint:             GrowthHashV1
  cohort:                         BehavioralCohortIdentityV1
  option:                         BehavioralOptionIdentityV1
  comparison_basis:               "current-exact-option-v1"
}
~~~

Target хранит exact raw-label-free Stage 10 identity. Mapping не сохраняет
Journal body, option label, criteria, path, title или provenance UUID list.
Target остаётся relation target; он не превращается в Behavioral evidence.

В отличие от Stage 10C preference mapping, Growth mapping не копирует
Stage 10B pattern/provenance как authority relation. Новые observations могут
изменить pattern state без автоматического изменения owner-reviewed target.
Если cohort/namespace/option identity или policy drift-ит, mapping становится
stale и требует нового review.

### 8.2. Exact mapping DTO

~~~text
GrowthGoalChoiceMappingV1 {
  contract_version:                    "growth-engine-v1"
  mapping_policy_id:                   "growth-goal-choice-explicit-v1"
  mapping_policy_fingerprint:          GrowthHashV1
  mapping_id:                          UUIDv7
  acceptance_operation_id_fingerprint: GrowthHashV1
  created_at:                          RFC3339 aware UTC
  reviewed_at:                         RFC3339 aware UTC
  mapping_basis:                       "owner-explicit-goal-choice-relation-v1"
  goal:                                GrowthGoalIdentityV1
  behavioral_target:                   GrowthBehavioralTargetIdentityV1
  relation:                             supports_goal | conflicts_with_goal | neutral_or_unknown
  mapping_fingerprint:                 GrowthHashV1
  supersedes_mapping_id:               UUIDv7 | null
}
~~~

mapping_id создаётся store/backend, не вычисляется из content. reviewed_at и
created_at не заменяют evidence time. Accepted record immutable.

### 8.3. Mapping policy identity

~~~text
mapping_policy_id = "growth-goal-choice-explicit-v1"
~~~

Canonical mapping policy JSON:

~~~json
{"basis":"owner-explicit-goal-choice-relation-v1","cardinality":"one-goal-one-target-per-record-v1","contract":"growth-engine-v1","domain":"exact-goal-domain-equals-cohort-domain-v1","lifecycle":"append-only-owner-reviewed-v1","persistence":"dedicated-operational-growth-mapping-v1","relation":"supports-conflicts-neutral-v1","target":"stage10-exact-cohort-option-v1","temporal":"separate-times-no-backfill-v1","version":"1"}
~~~

Canonical serialization совпадает с общей Growth policy. Нормативный
mapping_policy_fingerprint:

~~~text
sha256:0c4d223c04dae58ff62204245e665b41e3069c01b72e0657e8278243b4e97f6c
~~~

Будущий store принимает не более 200 active Growth mappings и не более 16384
UTF-8 bytes на immutable mapping record. Review projection ограничивается
32768 UTF-8 bytes. Overflow даёт fixed RESULT_TOO_LARGE без truncation.

### 8.4. Cardinality

V1 не использует Stage 10C injective one-stated-one-cohort-one-option rule.
Growth relation имеет другой exact rule:

- один record содержит ровно одну goal, один cohort, один option и одну
  relation;
- для одного exact tuple goal identity + cohort identity + option identity
  допускается не более одной active relation;
- одна goal может иметь несколько explicit mappings к разным cohorts/options;
- один option может быть explicit mapped к нескольким goals;
- несколько mappings не образуют winner, rank, default option или aggregate;
- conflicting active records для одного exact tuple дают ambiguous/concurrency
  failure, а не last-write-wins;
- bounded active mapping count и result byte limit обязательны, но concrete
  operational root и adapter остаются future implementation gate.

Many-to-many здесь означает набор независимых owner-reviewed exact relations,
а не implicit semantic graph. Расширение mapping semantics, cross-domain
bridging или temporal history требует новой policy.

## 9. Mapping persistence, lifecycle и rebuildability

### 9.1. Persistence decision

Принято: dedicated bounded operational mapping state вне vault и вне Stage 9.
Ephemeral relation недостаточна для reusable owner review, drift, correction и
delete semantics; canonical vault relation была бы schema/Safe Write change и
запрещена.

Будущий logical store:

- получает explicit application-owned root без default внутри vault, repository,
  web/static или browser profile;
- хранит только bounded IDs, fingerprints, exact policy values, relation и
  lifecycle metadata;
- имеет отдельную namespace/policy/manifest от Stage 10C;
- может переиспользовать mechanical lock/hash/append primitives только если
  storage authority, namespace, limits, deletion и policy остаются отдельными;
- не использует Stage 9 store, Stage 10C records, browser storage, DB/vector DB
  или network replication как Growth database;
- malformed stream, partial line, unknown field, duplicate ID, sequence/digest
  mismatch или uncertain commit дают fixed store error; salvage и silent repair
  запрещены.
- max_active_mappings=200, max_mapping_record_bytes=16384 и
  max_review_projection_bytes=32768 являются policy limits, а не permission
  silently truncate.

Конкретный physical adapter, operational root, backup и retention не
реализуются в Issue #258.

### 9.2. Lifecycle

Lifecycle projection имеет closed states:

~~~text
active | superseded | invalidated | deleted
~~~

Accepted payload не редактируется in place. Supersession, invalidation и
delete выполняются только explicit owner operation через append-only lifecycle
event. Stale mapping не становится active автоматически и не retarget-ится.
Automatic TTL по evidence_at, reviewed_at или created_at отсутствует.

Retry использует bounded operation fingerprint:

- byte-equivalent retry той же operation возвращает тот же mapping identity;
- другая payload при том же operation даёт IDEMPOTENCY_CONFLICT;
- active cardinality check, sequence allocation, append, flush/fsync и
  read-back находятся в одной lock boundary;
- uncertain final read не создаёт duplicate и требует safe retry/lookup;
- multi-host/shared-filesystem concurrency deferred.

### 9.3. Rebuildability

| Representation | Kind | Rebuild/delete rule |
| --- | --- | --- |
| Goal evidence | Canonical | Только current vault; не удаляется Growth rebuild |
| Accepted mapping history | Operational | Не восстанавливается из vault; сохраняется отдельной lifecycle policy |
| Growth relation result | Derived | Всегда пересобирается из current vault + verified mapping state |
| Advisor output/session | Ephemeral | Не является history или evidence |
| Cache/index | Не предусмотрен v1 | Любой future cache disposable, versioned и не-authoritative |

Если mapping store недоступен или corrupt, Growth не выдаёт convincing partial
result. Удаление derived result не меняет goal notes или mapping history.

## 10. Behavioral authority и eligibility

Growth читает только current Stage 10 Behavioral Self Model. Exact mapping
Stage 10C preference semantics не подставляется.

| Current Stage 10 pattern | Growth interpretation | Binary relation allowed |
| --- | --- | --- |
| repeated_exact_choice / current | Один deterministic current exact option | Да, только при exact mapping этого option |
| stable_over_time / stable | Current exact option deterministic; historical window только context | Да, только для current option |
| repeated_exact_choice / historical | Current subject отсутствует | Нет |
| mixed_exact_choices / mixed | Несколько exact choices, winner не выбирается | Нет |
| changed_over_time / changed | Два temporal subjects, не схлопываются | Нет |
| insufficient_evidence / insufficient | Evidence недостаточно для behavioral claim | Нет |
| not_comparable / not_comparable | Exact cohort/time не доказан | Нет |

Дополнительно:

- unknown evidence_at Decision Journal не получает invented time и не становится
  comparable;
- Stage 10 minimum support, exact 90-day current window и 180-day horizon
  остаются authority Stage 10;
- mixed не становится conflict, changed не становится conflict,
  insufficient не становится conflict;
- majority, winner, dominant behavior, percentage-only score, float,
  probability и hidden weight запрещены;
- outcome_presence может быть только metadata в behavioral reference;
- Stage 9 prediction/audit/calibration не увеличивает и не уменьшает behavior
  support.

## 11. Goal-vs-behavior states и deterministic evaluation

### 11.1. Closed state set

Growth relation result использует ровно следующие states:

~~~text
supports_goal
conflicts_with_goal
neutral_or_unknown
mixed_behavior
changed_behavior
behavioral_evidence_insufficient
goal_mapping_missing
goal_source_missing
goal_selection_required
not_comparable
~~~

### 11.2. State semantics

| State | Exact condition | Запрещённый вывод |
| --- | --- | --- |
| supports_goal | Current deterministic exact option совпал с active mapping этой goal/cohort/option, relation=supports_goal | Не optimal, не success и не objective truth |
| conflicts_with_goal | Current deterministic exact option совпал с active mapping этой goal/cohort/option, relation=conflicts_with_goal | Не failure, self-sabotage или weakness |
| neutral_or_unknown | Current deterministic exact option совпал с active mapping, relation=neutral_or_unknown | Не отсутствие цели и не безопасность |
| mixed_behavior | Current pattern=mixed_exact_choices/mixed | Не conflict и не dominant option |
| changed_behavior | Current pattern=changed_over_time/changed | Не trait, regression или personal change |
| behavioral_evidence_insufficient | Current pattern=insufficient_evidence/insufficient | Не absence of goal, weakness или conflict |
| goal_mapping_missing | Goal и exact current deterministic subject есть, но relation именно для observed option не принята | Не conflict из отсутствия mapping |
| goal_source_missing | Requested goal UUID отсутствует после complete current scan | Не доказательство, что у owner вообще нет goals |
| goal_selection_required | Для selected scope не задан exact focus при ambiguous multiple goals | Не priority между goals |
| not_comparable | Mapping/source/policy drift, historical-only, not-comparable cohort, missing exact domain или иная недоказанная relation | Не divergent и не failure |

### 11.3. Precedence

Порядок оценки одного current build:

1. invalid request/clock/policy/store/source -> fixed safe error, без partial;
2. explicit goal selection и current Goal identity;
3. mapping lifecycle, source identity и policy drift;
4. current Stage 10 pattern state;
5. mixed/changed/insufficient/historical/not-comparable проецируются в safe
   non-binary state;
6. для repeated/current или stable/stable берётся только exact current option;
7. backend ищет active mapping именно для goal + cohort + observed option;
8. missing relation -> goal_mapping_missing;
9. exact relation support/conflict/neutral -> соответствующий relation state.

Никогда не выполняется правило «mapped option A supports, значит любой другой
option conflicts». Каждая negative relation также требует отдельного explicit
review.

## 12. Decision-rule boundary

Roadmap wording «decision rules, мешающие stated goal» в Growth v1 не
активируется как inference.

Допустимы только:

- будущая explicit reviewed user rule, если отдельный canonical contract
  добавит такую authority;
- existing exact owner-reviewed Goal-to-choice relation как relation, но не как
  hidden rule;
- descriptive repeated exact pattern без rule wording.

Повторяемые choices, mixed/changed pattern, Stage 10 divergence и outcome text
не создают statements:

~~~text
«ты всегда избегаешь риска»
«ты саботируешь цель»
«у тебя есть правило X»
~~~

Если rule нельзя доказать exact reviewed authority без semantic
interpretation, результат DEFER/NOT_COMPARABLE. LLM может только предложить
кандидат в будущем explicit review; candidate не становится fact.

## 13. Growth-optimal authority

В Growth Engine v1 нет поля, state или claim growth_optimal,
growth_directed или optimal_choice.

| Candidate authority | Verdict | Reason |
| --- | --- | --- |
| Owner maps exact option as supports_goal | ACCEPT для relation only | Это explicit reviewed relation, не universal optimality |
| Structured deterministic goal-action relation | DEFER | Требуется отдельная schema/policy и не нужна для provider-free v1 |
| Assistant/Advisor recommendation | DEFER to Stage 11C | Recommendation remains independent and non-canonical |
| Free-form LLM reasoning | FORBIDDEN as silent authority | Semantic output не является reviewed relation |
| Historical majority behavior | FORBIDDEN | Observation не является normative goal direction |
| Outcome text/reward inference | FORBIDDEN | Free text не является progress/utility |

Поэтому v1 может сказать только «explicitly mapped exact relation» и никогда
не говорит «это growth-optimal choice».

## 14. Preference vs Goal

Preference и Goal — разные dimensions и разные authority layers.

Stage 10C relation:

~~~text
preference -> cohort -> option
~~~

не переиспользуется автоматически как:

~~~text
goal -> cohort -> option
~~~

Отдельный design vocabulary для composition states:

~~~text
goal_exists_preference_missing
preference_exists_goal_missing
goal_and_preference_not_comparable
mapped_preference_supports_goal
mapped_preference_conflicts_with_goal
behavior_relation_independent_from_preference_relation
~~~

В текущем Growth v1:

- goal_exists_preference_missing, preference_exists_goal_missing и
  goal_and_preference_not_comparable остаются explicit context states;
- behavior_relation_independent_from_preference_relation — обязательная
  boundary, если consumer показывает обе ветки;
- mapped_preference_supports_goal и mapped_preference_conflicts_with_goal не
  эмитятся: для них нужен отдельный explicit Goal-Preference relation gate;
- Stage 10C aligned/divergent никогда не заполняет эти два mapped states;
- Growth v1 не читает Stage 10C mapping store как Growth database и не
  строит preference/goal aggregate.

Goal может существовать без preference mapping. Preference mapping может
существовать без Goal. Ни одна из веток не получает скрытый priority.

## 15. Assistant / Advisor policy и privacy boundary

Current Assistant v1 принимает только caller-owned task, options,
explicit_constraints, explicit_goals и explicit_context. Automatic read из
Stage 4, Personal Memory, Stage 10, Stage 9, vault или Search не разрешён.

В Growth v1:

- Advisor не вызывается автоматически, в фоне, при rebuild или при показе
  обычного Growth result;
- provider/model/credentials/configuration не выбираются и не меняются;
- current private Goal не инжектируется в Assistant silently;
- Growth relation не передаётся в Advisor как evidence или objective;
- recommendation не становится canonical Goal, relation или growth-optimal;
- recommendation result/session не сохраняется в Growth mapping store.

Stage 11C0 отдельно разрешает только design contract для будущего explicit
owner action. Его точный preview → confirmation → immediate revalidation flow,
ровно один current Goal в explicit_goals, reuse existing AdvisorPort,
provider-visible allowlist, retention/logging boundary, fixed errors и
transient provenance описаны в
[growth-advisor-v1-contract.md](growth-advisor-v1-contract.md). В текущем
Stage 11B runtime Advisor не вызывается и advisor reference остаётся null;
Stage 11C runtime не реализован.

## 16. Compare integration

Growth Engine v1 не меняет Compare v1 и не добавляет GrowthCompareV1 runtime.

Current branches остаются независимыми:

~~~text
Habitual observed choice = Stage 10 behavior
Likely self-choice      = Simulate Me prediction
Independent advice      = Assistant/Advisor recommendation
Goal relation           = Growth exact owner-reviewed relation
~~~

Эти outputs нельзя смешивать в один score, confidence, probability, delta или
overall fit. Compare v1 не получает Growth relation автоматически, а Growth не
читает structural Delta как evidence.

Будущий GrowthCompareV1/Compare v2 может быть отдельной composition boundary с
provenance на каждой branch. Он обязан сохранять branch namespaces и не делать
hidden semantic delta. Это Stage 11C DEFER.

## 17. Temporal semantics

| Time | Meaning | Может заменить другую time |
| --- | --- | --- |
| goal.evidence_at | Когда reviewed Goal был сообщён | Нет |
| Behavioral evidence_at | Когда reviewed Decision Journal choice произошёл | Нет |
| Stage 10 generated_at | Clock конкретного behavioral rebuild и windows | Нет |
| mapping.reviewed_at | Server time explicit owner confirmation | Нет |
| mapping.created_at | Durable operational commit time | Нет |
| Growth generated_at | Clock result build | Нет |
| Advisor requested_at | Время explicit recommendation request | Нет |

Unknown остаётся unknown. Нельзя backfill-ить время из note created/updated,
UUID timestamp, mapping time или result generation.

Current behavior relation не заявляет, что Goal и choice произошли одновременно
или что одно вызвало другое. Goal evidence time unknown добавляет caveat, но не
создаёт invented alignment.

Mapping drift:

- Goal source/body/metadata/policy fingerprint changed -> source changed/stale;
- Goal UUID удалён -> goal_source_missing;
- cohort/domain/option namespace/index/fingerprint/policy changed -> mapping
  stale/not_comparable;
- новая observation, которая меняет pattern на mixed или changed, не создаёт
  conflict; pattern state проецируется в mixed_behavior/changed_behavior;
- historical-only subject не становится current из mapping time.

## 18. Outcome / progress boundary

Current Outcome Observation может присутствовать только как bounded
outcome_presence metadata внутри Stage 10 behavioral reference.

Growth не выводит из free-text Actual result, Reassessment или Notes:

~~~text
progress, success, failure, reward, regret, benefit, harm, utility,
quality, satisfaction или causal effect
~~~

Goal progress требует отдельного structured reviewed Goal Progress / Outcome
design gate. В текущий Goal identity, mapping и Growth DTO progress fields не
добавляются.

## 19. Personalized learning boundary

V1 допускает только безопасные derived directions:

- показать exact repeated friction рядом с explicit Goal;
- показать, что exact mapping для observed option отсутствует;
- спросить owner о clarification/review;
- предложить user-controlled experiment/hypothesis;
- показать mixed/changed/insufficient evidence без verdict.

Growth signal или question — derived UX state, не fact и не canonical write.

Запрещены:

- automatic psychological coaching;
- hidden persuasion optimization;
- addiction/compulsion nudging;
- motivational/discipline profiling;
- automatic reinforcement learning;
- background questioning;
- canonical write без existing reviewed capture/Safe Write;
- превращение answer/ignore/reject в Goal или relation автоматически.

Stage 11D, если будет одобрен, использует bounded question -> explicit owner
answer -> existing reviewed write path only if owner chooses. Active Learning
integration не начинается в Issue #258.

## 20. Privacy и sensitive-inference restrictions

Запрещены inference:

- mental-health diagnosis;
- discipline, willpower или motivation score;
- personality, hidden trait или manipulation susceptibility;
- political/religious persuasion targeting;
- sexual orientation/sex-life inference;
- criminal propensity;
- employability или creditworthiness;
- addiction susceptibility;
- risk profile, vulnerability или protected trait из divergence.

Persistent/ordinary DTO не содержит raw goal body, Journal body, option label,
criteria, path, title, source URL, UUID inventory, raw outcome text, provider
payload, secret, token, cookie или exception repr. Raw human-readable
projection допускается только transient в owner-only review surface и bounded
response.

Errors и logs используют fixed safe code/message без raw private details.
Browser local storage не является authority. Any future provider payload
requires separate owner privacy/data-handling gate.

## 21. Growth request/result DTO

### 21.1. Request

~~~text
GrowthEngineRequestV1 {
  contract_version:       "growth-engine-v1"
  selection:              GrowthGoalSelectionV1
  max_results:            int in 1..200
  max_result_bytes:       int in 1..131072
}
~~~

Unknown fields, bool вместо int, client policy/fingerprint, raw goal body,
labels, timestamps и arbitrary context отвергаются до source read. Request не
имеет advisor/provider flag: optional recommendation — отдельный explicit
Stage 11C operation, не hidden branch этого request.

### 21.2. Exact reference DTOs

~~~text
GrowthBehavioralPatternRefV1 {
  contract_version:               "behavioral-self-model-v1"
  derivation_version:             "behavioral-self-model-derivation-v1"
  policy_id:                      "behavioral-self-model-exact-context-v1"
  policy_fingerprint:             GrowthHashV1
  cohort_fingerprint:             GrowthHashV1
  pattern_type:                   Stage 10 closed pattern type
  pattern_state:                  Stage 10 closed pattern state
  provenance_fingerprint:         GrowthHashV1
  source_count:                   int >= 0
  reference_fingerprint:          GrowthHashV1
  current_option:                 BehavioralOptionIdentityV1 | null
}

GrowthMappingRefV1 {
  mapping_id:                     UUIDv7
  mapping_policy_id:               "growth-goal-choice-explicit-v1"
  mapping_fingerprint:             GrowthHashV1
  relation:                        supports_goal | conflicts_with_goal | neutral_or_unknown
}

GrowthTemporalContextV1 {
  goal_evidence_at:                RFC3339 UTC | "unknown"
  goal_evidence_at_precision:      "exact" | "unknown"
  behavioral_generated_at:         RFC3339 UTC | null
  behavioral_current_window_start: RFC3339 UTC | null
  behavioral_current_window_end:   RFC3339 UTC | null
  mapping_reviewed_at:             RFC3339 UTC | null
  mapping_created_at:              RFC3339 UTC | null
  advisor_requested_at:            RFC3339 UTC | null
}

Legacy placeholder GrowthAdvisorResultRefV1 в текущем Stage 11B contract:
  request_id_fingerprint:          GrowthHashV1
  result_kind:                     "independent_recommendation_analysis"
  advisor_policy_id:               bounded versioned string
  requested_at:                    RFC3339 UTC
  generated_at:                    RFC3339 UTC
}
~~~

Этот nullable placeholder не входит в текущий Stage 11B execution и должен
быть null. Exact future GrowthAdvisorResultRefV1 с Assistant/Goal policy
binding и branch provenance определён в
[growth-advisor-v1-contract.md](growth-advisor-v1-contract.md).
Recommendation text, rationale и selected option не копируются в ordinary
Growth result; owner-facing transient Advisor projection является отдельной
policy.

### 21.3. Exact result shape

~~~text
GrowthGoalRelationResultV1 {
  goal:                            GrowthGoalIdentityV1 | null
  state:                           closed Growth relation state
  cohort_fingerprint:              GrowthHashV1 | null
  behavioral_pattern:              GrowthBehavioralPatternRefV1 | null
  behavioral_option:               BehavioralOptionIdentityV1 | null
  mapping:                         GrowthMappingRefV1 | null
  reason_codes:                    tuple[fixed Growth reason code, ...]
  caveats:                         tuple[fixed Growth caveat code, ...]
  temporal:                        GrowthTemporalContextV1
  advisor:                         GrowthAdvisorResultRefV1 | null
}

GrowthEngineResultV1 {
  contract_version:                "growth-engine-v1"
  derivation_version:              "growth-engine-derivation-v1"
  policy_id:                       "growth-engine-explicit-relation-v1"
  policy_fingerprint:              GrowthHashV1
  generated_at:                    RFC3339 UTC
  selection_mode:                  "selected_goal" | "each_current_goal"
  selected_goal_source_uuid:       UUIDv7 | null
  eligible_goal_count:             int >= 0
  goal_results:                    tuple[GrowthGoalRelationResultV1, ...] # max 200
  reason_codes:                    tuple[fixed Growth reason code, ...]
  caveats:                         tuple[fixed Growth caveat code, ...]
}
~~~

DTO invariants:

- complete result or safe error; silent truncation и convincing partial profile
  запрещены;
- raw bodies/labels/paths не присутствуют;
- no float, confidence, probability, score, ranking, weight или personality
  field;
- every non-null Goal/Behavioral/Mapping reference revalidated against current
  policy;
- selected_goal_source_uuid — selector/provenance, не скрытая priority;
- goal_results sorted deterministic по goal UUID, cohort fingerprint, state;
- no aggregate across goals, options or cohorts;
- relation state support/conflict/neutral появляется только при exact current
  deterministic option и exact active relation;
- mixed/changed/insufficient/historical result никогда не заполняется
  binary support/conflict;
- advisor в Stage 11 v1 всегда null;
- max result bytes checked against canonical serialization; overflow даёт
  fixed RESULT_TOO_LARGE без alternate serializer.

### 21.4. Policy identity

~~~text
derivation_version = "growth-engine-derivation-v1"
policy_id          = "growth-engine-explicit-relation-v1"
~~~

Canonical policy JSON:

~~~json
{"advisor":"none-v1","behavior":"stage10-current-exact-subject-v1","comparison":"none-v1","contract":"growth-engine-v1","goal":"stage4-direct-goal-v1","mapping":"owner-explicit-goal-choice-v1","persistence":"dedicated-operational-growth-mapping-v1","relation":"supports-conflicts-neutral-v1","selection":"owner-selected-or-per-goal-v1","temporal":"separate-times-no-backfill-v1","version":"1"}
~~~

Serialization: UTF-8, ensure_ascii=false, sort_keys=true, separators comma
and colon, no BOM/whitespace/dynamic keys. Нормативный fingerprint:

~~~text
sha256:3fefc6d638b8bb0de3143cbef4ce51bebbae007ee5e95c5fe2aa81f02b4db182
~~~

Изменение любой policy semantics требует новой policy/derivation version.

## 22. Fixed errors, reasons и abstentions

### 22.1. Fixed vocabulary

~~~text
GROWTH_INVALID_REQUEST
GROWTH_GOAL_SOURCE_UNAVAILABLE
GROWTH_GOAL_MISSING
GROWTH_GOAL_SOURCE_CHANGED
GROWTH_MULTIPLE_GOALS_AMBIGUOUS
GROWTH_GOAL_SELECTION_REQUIRED
GROWTH_GOAL_MAPPING_MISSING
GROWTH_GOAL_MAPPING_INVALID
GROWTH_GOAL_MAPPING_STALE
GROWTH_BEHAVIORAL_SOURCE_UNAVAILABLE
GROWTH_BEHAVIORAL_EVIDENCE_INSUFFICIENT
GROWTH_BEHAVIORAL_STATE_NOT_COMPARABLE
GROWTH_RECOMMENDATION_UNAVAILABLE
GROWTH_UNSUPPORTED_SEMANTIC_COMPARISON
GROWTH_POLICY_MISMATCH
GROWTH_RESULT_TOO_LARGE
GROWTH_MAPPING_STORE_UNAVAILABLE
GROWTH_MAPPING_STORE_CORRUPT
GROWTH_MAPPING_CONFLICT
GROWTH_IDEMPOTENCY_CONFLICT
GROWTH_CONCURRENCY_CONFLICT
~~~

Source unavailable, policy mismatch, store unavailable/corrupt, invalid
request, mapping corruption/conflict и result overflow are aborting safe
errors. Missing mapping, insufficient evidence, mixed/changed and
not-comparable are bounded result states/reasons when the complete current
source itself is valid. Recommendation unavailable is scoped only to the
future optional Advisor branch.

Каждый public error имеет только fixed code и fixed message. В message не
попадают body, label, path, UUID list, source text, exception repr, secret,
provider или endpoint detail. Unknown exception never becomes user-facing
prose.

### 22.2. Fixed caveats

Минимальный closed caveat vocabulary:

~~~text
current_goal_revalidated
goal_evidence_time_unknown
behavioral_relation_exact_only
current_behavior_revalidated
mixed_no_winner
changed_not_a_trait
insufficient_not_conflict
goal_mapping_is_explicit
goal_mapping_missing_for_observed_option
mapping_review_time_is_not_evidence_time
temporal_alignment_not_proven
outcome_presence_only
preference_branch_independent
no_growth_optimal_claim
advisor_not_used_v1
~~~

Добавление caveat, меняющее semantics, требует новой policy/derivation
version. Caveat не превращается в claim text.

### 22.3. Safe projection table

| Condition | Fixed code | Safe result |
| --- | --- | --- |
| Request/clock invalid | GROWTH_INVALID_REQUEST | Abort, no vault/store read beyond validation |
| Complete current canonical source unavailable | GROWTH_GOAL_SOURCE_UNAVAILABLE or GROWTH_BEHAVIORAL_SOURCE_UNAVAILABLE | Abort, no partial result |
| Selected Goal UUID absent after complete scan | GROWTH_GOAL_MISSING | goal_source_missing |
| Ambiguous unscoped multiple-goal request | GROWTH_MULTIPLE_GOALS_AMBIGUOUS or GROWTH_GOAL_SELECTION_REQUIRED | Require explicit goal focus |
| Goal source identity changed | GROWTH_GOAL_SOURCE_CHANGED | not_comparable, no retarget |
| Goal domain missing or differs from cohort domain | GROWTH_UNSUPPORTED_SEMANTIC_COMPARISON | not_comparable |
| No active relation for current observed exact option | GROWTH_GOAL_MAPPING_MISSING | goal_mapping_missing, never conflict |
| Mapping malformed or conflicting | GROWTH_GOAL_MAPPING_INVALID or GROWTH_MAPPING_CONFLICT | Abort, no winner |
| Mapping identity/policy drifted | GROWTH_GOAL_MAPPING_STALE or GROWTH_POLICY_MISMATCH | not_comparable or abort for whole invalid build |
| Stage 10 pattern has insufficient evidence | GROWTH_BEHAVIORAL_EVIDENCE_INSUFFICIENT | behavioral_evidence_insufficient |
| Stage 10 pattern is mixed, changed, historical-only or not comparable | GROWTH_BEHAVIORAL_STATE_NOT_COMPARABLE | mixed_behavior, changed_behavior or not_comparable |
| Semantic/fuzzy/provider comparison requested | GROWTH_UNSUPPORTED_SEMANTIC_COMPARISON | Abort, no fuzzy fallback |
| Optional future Advisor unavailable | GROWTH_RECOMMENDATION_UNAVAILABLE | Advisor branch absent; deterministic Growth result preserved |
| Store unavailable/corrupt | GROWTH_MAPPING_STORE_UNAVAILABLE or GROWTH_MAPPING_STORE_CORRUPT | Abort, no salvage |
| Complete result/review exceeds bound | GROWTH_RESULT_TOO_LARGE | Abort, no truncation |

Коды и messages фиксированы composition policy. Ни один mapping/code path не
возвращает raw body, labels, path, UUID inventory или exception details.


## 23. Future Web/API/UX

Это design direction, не implementation.

Owner-facing progressive structure:

~~~text
Goal
Куда я явно хочу прийти

Observed
Что я регулярно выбираю в exact comparable context

Growth relation
Как конкретный owner-mapped choice относится к конкретной goal

Independent recommendation
Отдельная рекомендация, только если я её явно запросил
~~~

Technical details (policy, fingerprints, UUIDs, provenance, caveats) идут в
progressive disclosure. Primary UI не является technical dashboard.

Future owner-only API может иметь review, accept, read current relation и
supersede/invalidate/delete operations. Backend обязан заново строить sources
и не принимать client body/labels/fingerprints as truth. Response no-store,
bounded, без browser authority. Background refresh, automatic mapping,
automatic Advisor и hidden private context запрещены.

Запрещён copy:

- «ты проваливаешь цель»;
- «ты саботируешь себя»;
- «твоя настоящая цель»;
- «ты ленив»;
- «тебе не хватает силы воли».

## 24. Stage 9, Stage 10 и Simulate Me boundaries

### Stage 9

Stage 9 остаётся prediction audit/calibration. Accuracy, match/mismatch,
audit linkage и calibration aggregate не являются Goal, behavior или mapping
authority. Growth не читает Stage 9 store и не меняет его persistence.

### Stage 10

Stage 10 остаётся behavioral observation + explicit preference mapping:

- Stage 10A/10B exact cohort/pattern rules остаются unchanged;
- Stage 10C mapping records и aligned/divergent states остаются preference-only;
- Growth mapping имеет отдельные relation names, policy fingerprint, store
  namespace и lifecycle;
- Stage 10C divergence не становится Growth conflict автоматически;
- Growth не пишет в Stage 10 store и не меняет Stage 10 DTO.

### Simulate Me

Simulate Me predicts likely owner choice, not desirable or goal-optimal choice.
Growth result не подаётся в Simulate Me input, prompt, option order,
confidence или abstention. Любая future one-way input requires new Simulate Me
policy, provenance and calibration baseline/reset gate.

## 25. ACCEPT / CHANGE / RISK / DEFER

| Area | Verdict | Decision |
| --- | --- | --- |
| Goal authority | ACCEPT | Current Stage 4 direct goal claim + exact current source identity |
| Goal currentness | ACCEPT | Current scan/policy presence only; no recency/supersede inference |
| evidence_at=unknown | ACCEPT | Preserve literal unknown; no invented temporal alignment |
| Multiple goals | ACCEPT | Owner-selected focus or separate per-goal results; no ranking/weights |
| Relation vocabulary | ACCEPT | supports_goal, conflicts_with_goal, neutral_or_unknown only |
| Goal conflict authority | ACCEPT | Explicit owner-reviewed exact relation for exact observed option |
| Semantic/LLM mapping | FORBIDDEN | Never silent authority; future candidate requires review gate |
| Behavioral authority | ACCEPT | Current Stage 10 exact pattern; mixed/changed/insufficient stay safe |
| Goal mapping cardinality | ACCEPT | One exact target per record; independent bounded many-record set |
| Mapping persistence | ACCEPT | Dedicated operational state outside vault, Stage 9 and Stage 10C |
| Mapping store runtime | DEFER | Physical adapter, root, backup, retention and deployment gate |
| Canonical schema / vault | FORBIDDEN | No new fields, notes, Safe Write or vault change |
| Preference reuse | FORBIDDEN | Stage 10C semantics/store/namespace remain separate |
| Decision-rule inference | DEFER | No hidden rule from repeated behavior; future reviewed authority only |
| Growth-optimal claim | DEFER / not emitted | V1 has no optimality authority or field |
| Assistant private Goal injection | FORBIDDEN in v1 | Current Assistant remains explicit caller-owned only |
| Advisor branch | DEFER | Stage 11C0 contract is complete; Stage 11C explicit owner runtime remains deferred |
| Compare integration | DEFER | No GrowthCompareV1 in current v1; Advisor branch stays independent |
| Compare/Stage 9/Simulate Me mutation | FORBIDDEN | No feedback path or retroactive semantics |
| Outcome/progress | DEFER | Separate structured reviewed Goal Progress design gate |
| Personalized learning | ACCEPT bounded | Derived question/friction only; no hidden persuasion or write |
| Sensitive inference | ACCEPT prohibition | Protected traits, diagnosis, scores and vulnerability forbidden |
| Runtime/schema/dependencies | CHANGE | Status documents design only; no implementation/schema/dependency changes |
| Roadmap status | CHANGE | Add Stage 11 design-contract status, runtime not implemented |
| HUMAN_REQUIRED | ACCEPT | None for this design-only gate |

### Known risks

1. Exact identity deliberately produces false negatives after harmless-looking
   edits; this is safer than silent retargeting.
2. Owner review cost grows with multiple goals and exact mappings; no hidden
   ranking is the intended trade-off.
3. Operational relation metadata is sensitive even without raw bodies and needs
   owner-only access, bounded retention and explicit delete/backup behavior.
4. A mixed or changed pattern can leave a useful explicit mapping without a
   binary current relation; the safe result is not_comparable/mixed/changed.
5. Goal progress remains unavailable until a separate reviewed outcome contract.

## 26. Exact future decomposition и next implementation slice

### Stage 11A — deterministic current Goal identity/context core (COMPLETE)

Stage 11A реализован в Issue #260 и находится в current main. Реализация
сохраняет exact current scan → build_report → existing BuildSelfModel,
direct Goal identity, explicit selection, unknown evidence time, source
revalidation, bounded read-only context и fixed safe errors. Его deterministic
tests и Python 3.14 gate являются историей этого merged slice.

Stage 11A не реализует Goal-to-choice mapping, mapping store, behavior relation,
Growth result comparison, Web/API/UI, Advisor, Compare, Active Learning или
canonical write. Existing Stage 4 code/DTO/policy is not changed.

### Stage 11B — explicit Goal-to-choice relation + friction read model (COMPLETE)

Stage 11B реализован в Issue #262/PR #263 и находится в current main:

- separate Growth mapping validator/store namespace;
- explicit review and immediate revalidation;
- relation vocabulary and lifecycle;
- current Stage 10 behavioral reference;
- deterministic relation states and no negative inference from missing mapping.

### Stage 11C0 — Growth Advisor privacy/payload/provenance design (COMPLETE)

Issue #264 фиксирует отдельный [Growth Advisor v1 contract](growth-advisor-v1-contract.md):
explicit preview/confirmation, exact current Goal projection, reuse existing
Assistant/Advisor boundary, no hidden Growth context, transient full result и
compact provenance. Runtime/provider call, Web/API/UI, persistence и Compare
composition не реализованы.

### Stage 11C — optional Advisor / Growth Compare runtime

Future implementation gate after #264. It may implement only the exact
GrowthAdvisor v1 contract and must preserve deterministic Stage 11A/11B
semantics. GrowthCompare/Compare v2 remains a separate future contract.

### Stage 11D — personalized learning/question boundary

Future gate for bounded derived questions/experiments. No automatic write,
motivation optimization or reinforcement.

### Stage 11E — Web/API, integration QA и Stage 11 closeout

Future owner-only transport/UI, no-store behavior, integration tests, exact
production gates and status closeout. Issue #258 was the design gate; Stage
11A/11B were implemented later, while Stage 11C0 is design-only in #264.
Implementation Issues are not created automatically.

## 27. Contract acceptance flags

~~~text
runtime changed: NO
dependencies changed: NO
schema changed: NO
provider/network changed: NO
env changed: NO
env change required: NO
second-brain-vault changed: NO
Stage 11A started: YES / COMPLETE
Stage 11B started: YES / COMPLETE
Stage 11C0 design: COMPLETE
Stage 11C runtime: NOT IMPLEMENTED
Stage 11D/E started: NO
Stage 12+ started: NO
next Issue created: NO
HUMAN_REQUIRED: none
~~~

Итоговый normative status:

~~~text
Cognitive Twin v1 / Stages 1-8 = COMPLETE
Stage 9 = COMPLETE
Stage 10 Behavioral Self Model = COMPLETE
Stage 11A = COMPLETE
Stage 11B = COMPLETE
Stage 11C0 Growth Advisor design = COMPLETE
Stage 11C runtime = NOT IMPLEMENTED
Stage 12+ = NOT STARTED
~~~
