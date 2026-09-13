# Behavioral Self Model v1 — design contract

Статус документа: **DESIGN / NORMATIVE CONTRACT ONLY**. Это deliverable
Issue [#246](https://github.com/MikeMoore1337/second-brain/issues/246), а не
runtime-реализация. Stage 10 runtime, Web/API, новые поля vault и изменения
`second-brain-vault` в этой задаче не создаются.

Контракт читается поверх текущего `main` и следующих authority:

- [design-roadmap-v1.md](design-roadmap-v1.md);
- [self-model-v1-contract.md](self-model-v1-contract.md);
- [prospective-audit-calibration-v1-contract.md](prospective-audit-calibration-v1-contract.md);
- [vault-contract-v1.md](../architecture/vault-contract-v1.md);
- current `src/second_brain/application/decision_journal.py` и
  `src/second_brain/application/self_model.py`.

`ACCEPT` означает нормативную границу этого contract, `CHANGE` — намеренное
изменение sequencing/status без изменения исторической semantics Stage 1–9,
`RISK` — известное ограничение, которое нельзя обходить молча, `DEFER` —
отдельный будущий owner-approved gate.

## 1. Purpose и non-goals

### Purpose

Behavioral Self Model v1 отвечает только на вопрос:

> Что пользователь фактически и повторяемо делает/выбирает в действительно
> сопоставимых reviewed Decision Journal contexts?

Нормативная цепочка:

```text
current reviewed Decision Journals
  -> exact deterministic behavioral observations
  -> exact comparable cohorts
  -> repeated-choice evidence with visible counts
  -> derived/rebuildable Behavioral Self Model read model
```

Это отдельный слой от существующего Stated Self Model:

```text
Stated Self Model
  = что пользователь явно утверждает, предпочитает, считает или хочет

Behavioral Self Model
  = что видно из повторяемых reviewed choices
    в exact comparable cohorts
```

Главный invariant:

- Behavioral Self Model — `derived`, `rebuildable`, `read-only`;
- он не является canonical user evidence;
- он не является Stage 9 operational state;
- он не является personality profile, diagnosis или hidden user score;
- он не меняет автоматически `Personal Memory`, `Decision Journal`,
  `Outcome Observation`, direct claims Stated Self Model, Stage 9 audit/link
  history или `second-brain-vault`.

### Non-goals

В Issue #246 не входят:

- Stage 10 runtime, application service, Web/API или UI;
- новый `NoteType`, новый `schema_version`, новые canonical note fields или
  schema migration;
- semantic grouping по LLM, provider, embedding, vector DB, ML или fuzzy
  similarity;
- personality, subconscious preference, risk appetite, impulsiveness,
  discipline, intelligence, psychological type или universal score;
- diagnosis и inference protected/sensitive traits;
- success/failure/regret/satisfaction/utility/reward из free-text outcome;
- automatic write-back, correction mutation, training или policy auto-tuning;
- изменение Simulate Me, Assistant, Compare, Active Learning, Growth Engine,
  Adaptive Cognitive Twin или Stage 11+;
- изменение `second-brain-vault`, Vault Sync, production environment или
  deployment runtime.

`Behavioral Self Model` не формулирует «какой пользователь на самом деле».
Он может вывести только bounded observed pattern с explicit provenance,
support и caveats.

Текущий Stage 4 `BuildSelfModel` и его `self-model-v1-contract.md` остаются
без изменений: они по-прежнему emit-ят только direct assertion claims
`preference`/`belief`/`goal`. Future-only seams `behavioral_pattern` и
`decision_rule` не активируются этим документом. Behavioral Self Model имеет
отдельный DTO и отдельную derivation policy.

## 2. Terminology

| Термин | Нормативное значение |
| --- | --- |
| `current valid Decision Journal` | Текущая managed note из `scan -> build_report` с exact enrollment marker, парой `observed_decision + decision`, валидным `parse_decision_journal_body` и без blocking diagnostics. |
| `behavioral observation` | Immutable in-memory derived projection ровно одного current valid Decision Journal. |
| `comparable observation` | Behavioral observation с exact evidence time и доказанным v1 cohort identity. |
| `cohort` | Группа comparable observations с полностью равным exact cohort key; это не topic, personality или inferred domain. |
| `option identity` | Внутренний ordered Stage 2 option index вместе с fingerprint exact normalized label; label identity действует только внутри Journal option namespace. |
| `support` | Целое число comparable observations, выбравших exact option identity. Support — descriptive count, не confidence и не probability. |
| `denominator` | Число distinct current comparable observations в том же cohort и выбранном temporal window. |
| `current window` | Inclusive UTC interval последних 90×24 часов до `generated_at`. |
| `historical window` | UTC interval от 180×24 часов до 90×24 часов до `generated_at`, нижняя граница inclusive, верхняя exclusive. |
| `stable` | Exact same choice в обеих bounded windows при нормативном minimum support; это не утверждение о личности пользователя. |
| `changed` | Exact cohort-local transition между двумя internally unanimous bounded windows с нормативным minimum support; это не фраза «пользователь изменился». |
| `mixed` | В одном comparable cohort есть разные exact chosen option identities, но строгий `changed_over_time` threshold не доказан. |
| `insufficient` | Comparable evidence меньше minimum support или после safe exclusions недостаточно для pattern. |
| `not_comparable` | Нет доказанного exact cohort identity/time или требуется unsupported semantic mapping. |

Для сравнения text fields используется только текущая Stage 2 minimal
normalization: `strip` и collapse runs of whitespace до одного U+0020
(эквивалент `" ".join(value.split())`). Она применяется только для exact
equality/fingerprints. Не применяются Unicode normalization, lower/casefold,
punctuation stripping, transliteration, alias, translation или synonym rules.

## 3. Authority/source matrix

| Source | Authority в v1 | Разрешённая роль | Что запрещено выводить |
| --- | --- | --- | --- |
| `second-brain-vault` через current `FileSystemVaultReader.scan() -> build_report()` | Единственный canonical источник user evidence | Current validated notes и Stage 2 projections | Нельзя читать старые snapshots, Search cache или path как замену current source. |
| `observed_decision + decision` | Единственная authority actual chosen option | Source behavioral observation, cohort и choice support | Один Journal не доказывает permanent preference или rule. |
| `outcome_later_observation + outcome` с current `decision_id` | Canonical later descriptive context | Только `outcome_presence` metadata | Outcome не меняет historical choice и не становится success, failure, reward или quality. |
| Stage 4 Stated Self Model direct claim | Separate derived projection explicit statement | Отдельный stated layer для future composition | Не является behavioral observation и не переписывает Behavioral Model. |
| Stage 9 audit event/link/calibration | Canonical operational history и derived operational report | Никакой behavioral evidence authority | Нельзя считать prediction, link, match, mismatch или calibration strength actual behavior. |
| Simulate Me, Assistant, Compare result | Derived/ephemeral process state | Не входит в source set | Prediction не становится observed choice. |
| Stage 8 question/ignore/reject/answer | Process/review state до Safe Write | Не входит в source set | Answer не становится Decision Journal choice без отдельного reviewed Journal. |
| SearchHit, snippet, FTS/index, browser payload, cache | Candidate/transport only | Не используется как source | Нельзя восстановить body, UUID relation или cohort из stale projection. |

`Outcome Observation` может подтвердить только наличие current later
observation. Он не заменяет actual-choice authority Journal и не входит в
choice support, temporal ordering или exact ratios.

## 4. Stage 2 input boundary и eligibility

Behavioral build в будущем обязан каждый раз выполнять:

```text
VaultReader.scan()
  -> build_report()
  -> current canonical/integrity gate
  -> exact Stage 2 Decision Journal projection
```

Eligible source Journal одновременно:

1. managed и имеет valid UUIDv7, unique в current report;
2. имеет exact YAML scalar `second_brain_personal_memory: 1`;
3. имеет `evidence_kind: observed_decision` и `self_kind: decision`;
4. имеет non-null `decision_journal`, созданный текущим exact parser;
5. проходит current storage identity, safe relative path и metadata checks;
6. не затронут `NOTE_READ_ERROR`, `NOTE_FRONT_MATTER_ERROR`,
   `PERSONAL_MEMORY_*`, `DECISION_JOURNAL_INVALID_BODY`,
   `OUTCOME_DECISION_*`, duplicate identity или другой diagnostic, который
   делает current canonical content/relation incomplete;
7. имеет `evidence_at_precision: exact` и aware `evidence_at` для behavioral
   observation.

Legacy notes без exact marker не являются behavioral evidence, даже если их
front matter или body похожи на Journal. Stage 10 не расширяет Stage 2 pair и
не добавляет новую canonical taxonomy.

`evidence_at: unknown` — допустимая Stage 2 canonical value, но не
comparable behavioral observation в v1. Такая note сохраняется в vault, не
получает искусственного времени и может быть отражена только как bounded
`unknown_time_excluded` caveat/state.

Если текущий scan не может доказать complete canonical source или обнаружена
ошибка enrolled Journal/relation, весь behavioral result отклоняется safe
error. Уменьшенный profile из оставшихся notes не выдаётся.

## 5. Comparability и cohort policy

### 5.1. Рассмотренные варианты

| Вариант | Verdict | Причина |
| --- | --- | --- |
| Exact `domain` + exact normalized Journal context/options/criteria | **ACCEPT** | Provider-free, deterministic, проверяемо по current Stage 2 projection и не требует новой schema. |
| Exact normalized option labels и criteria | **ACCEPT** | Составная часть exact cohort key; порядок сохраняется, fuzzy equality отсутствует. |
| Caller-supplied bounded behavioral lens | **DEFER** | Client input не может стать authority; нужен отдельный owner-reviewed lens policy и API contract. |
| Explicit owner-reviewed mapping между namespaces | **DEFER** | Может быть полезен для cross-context grouping, но требует отдельной review/mapping lifecycle и provenance. |
| Provider/embedding semantic grouping | **DEFER / NOT AUTHORITY** | Может быть только future candidate-generation aid с отдельным review; в v1 не влияет на grouping. |
| Новая canonical taxonomy/schema | **DEFER** | Полезный cross-context model нельзя вводить без нового schema/design/migration gate. |

### 5.2. Выбранное exact правило v1

Два Journal comparable только если у них полностью равен следующий key:

```text
cohort_key_v1 = (
    exact non-null current domain,
    N(Situation),
    N(Information known at decision time),
    ordered tuple[N(Available options[i])],
    ordered tuple[N(Criteria[i])],
    grouping_basis = "exact-reviewed-decision-context-v1",
)
```

Нормативные следствия:

- `domain` обязан быть exact existing lowercase ASCII slug; `None` не является
  domain identity и не создаёт cohort;
- `Situation` и `Information known at decision time` сравниваются exact после
  только whitespace normalization;
- `Available options` сравниваются как ordered tuple exact normalized labels;
  перестановка options делает notes non-comparable;
- `Criteria` сравниваются как ordered tuple exact normalized items;
  перестановка criteria делает notes non-comparable;
- option namespace и criteria representation являются частью context identity;
- `Chosen option` **не входит** в cohort key: он является observed value,
  который агрегируется внутри уже доказанного cohort;
- `Reasons`, `Confidence`, `Expected result`, storage `created`/`updated`,
  path, filename, title, UUID order и later Outcome body не входят в grouping;
- timestamp-only grouping, same filename, same title, similar text, same
  option count или same domain без exact context запрещены;
- cross-domain, missing-domain и cross-horizon assumptions запрещены;
- two distinct UUIDs с одинаковым body считаются двумя reviewed records, если
  current canonical scan не сообщает duplicate identity; content-based dedupe
  не выполняется.

Exact key сравнивается во время одного build по canonical normalized tuples.
Для bounded DTO и drift detection вычисляется `cohort_fingerprint`:

```json
{"basis":"exact-reviewed-decision-context-v1","criteria":["<normalized criterion ...>"],"domain":"<exact domain>","information":"<normalized information>","options":["<normalized option ...>"],"situation":"<normalized situation>"}
```

Это internal canonical JSON без whitespace, `sort_keys=true`, UTF-8,
`sha256:` + 64 lowercase hex. Fingerprint не даёт semantic equivalence и не
должен быть единственной authority при наличии исходных normalized tuples в
текущем build. Raw context не входит в emitted DTO или persistent cache.

### 5.3. Несопоставимость

Следующие cases дают `not_comparable`, а не guess:

- отсутствует non-null `domain`;
- нет exact temporal value;
- не удаётся построить полный exact key;
- caller просит lens/mapping, которого нет в approved v1 policy;
- comparison требует semantic equivalence, alias, translation, fuzzy,
  provider или embedding;
- current target/mapping становится ambiguous.

Если ambiguity является ошибкой current canonical source, а не просто
отсутствием v1 grouping key, build завершается top-level
`BEHAVIORAL_SELF_MODEL_AMBIGUOUS_GROUPING` без partial result.

## 6. Behavioral observation DTO

Observation существует только для одного eligible Journal, который прошёл
exact time/domain/cohort eligibility. Это immutable derived DTO в памяти, а не
новая note или database row.

### 6.1. Exact shape

```text
BehavioralHashV1 = "sha256:" + 64 lowercase hexadecimal characters

BehavioralOptionIdentityV1 {
  option_index:       int                 # 0..19, current Journal order
  option_fingerprint: BehavioralHashV1    # hash of N(option label)
}

BehavioralCohortIdentityV1 {
  grouping_policy:               "exact-reviewed-decision-context-v1"
  domain:                        string    # exact current non-null domain
  situation_fingerprint:         BehavioralHashV1
  information_fingerprint:      BehavioralHashV1
  option_namespace_fingerprint:  BehavioralHashV1
  criteria_fingerprint:          BehavioralHashV1
  cohort_fingerprint:             BehavioralHashV1
}

BehavioralOptionNamespaceV1 {
  option_count:                  int                 # 2..20
  ordered_option_fingerprints:   tuple[BehavioralHashV1, ...]
}

BehavioralCriteriaRepresentationV1 {
  criteria_count:                int                 # 1..20
  ordered_item_fingerprints:     tuple[BehavioralHashV1, ...]
}

BehavioralObservationV1 {
  contract_version:              "behavioral-self-model-v1"
  derivation_version:            "behavioral-observation-v1"
  source_journal_uuid:            UUIDv7
  evidence_at:                   RFC3339 aware datetime, canonical UTC
  evidence_at_precision:         "exact"
  cohort:                        BehavioralCohortIdentityV1
  option_namespace:              BehavioralOptionNamespaceV1
  criteria:                      BehavioralCriteriaRepresentationV1
  chosen_option:                 BehavioralOptionIdentityV1
  outcome_presence:              "absent" | "present"
  journal_snapshot_fingerprint:  BehavioralHashV1
}
```

Observation invariants:

- `source_journal_uuid` — current canonical Journal identity; path, filename,
  title и body в DTO не копируются;
- `evidence_at` — exact Stage 2 evidence time, normalized to UTC only as an
  equivalent representation of the same instant; `created`/`updated` и UUIDv7
  embedded time не являются fallback;
- `option_index` разрешается current parser по exact normalized chosen label;
  duplicate normalized options уже запрещены Stage 2;
- `option_fingerprint`, ordered option fingerprints и criteria fingerprints
  вычисляются из normalized values, но raw labels не входят в DTO;
- `journal_snapshot_fingerprint` покрывает current validated Stage 2
  pre-choice projection, metadata used by grouping, chosen option и
  `outcome_presence`; он нужен для drift detection, а не для semantic match;
- `outcome_presence = present` означает хотя бы один current valid Outcome
  Observation с `decision_id == source_journal_uuid`; multiple outcomes
  схлопываются в presence only;
- outcome bodies, outcome UUID list, `Actual result`, `Reassessment` и
  `Notes` не копируются;
- одна observation не становится pattern до cohort minimum support;
- DTO immutable, bounded и не имеет `save`, `update`, `confirm`, `apply` или
  write receipt.

Journal с missing domain, unknown time или unsupported relation не получает
`BehavioralObservationV1`; для него применяется safe `not_comparable` или
error semantics из раздела 15.

## 7. Closed set pattern types и states

### 7.1. Approved pattern types

В v1 закрытый set состоит ровно из:

| `pattern_type` | Exact доказательство | Allowed `state` |
| --- | --- | --- |
| `repeated_exact_choice` | Не менее 3 comparable observations в одном cohort, все выбрали один exact option; observations находятся только в одной bounded window. | `current` или `historical` |
| `mixed_exact_choices` | Не менее 3 comparable observations в одном cohort и минимум 2 distinct chosen option identities; строгий change threshold не выполнен. Majority не выбирается. | `mixed` |
| `stable_over_time` | Не менее 3 comparable observations, минимум 1 в current и 1 в historical window, все выбрали один exact option. | `stable` |
| `changed_over_time` | В historical и current window минимум по 2 observations; каждая window internally unanimous, а exact choice различается между windows. | `changed` |
| `insufficient_evidence` | Доказанный cohort имеет от 0 до 2 active comparable observations после exclusions. | `insufficient` |
| `not_comparable` | Exact cohort/time identity не доказан или semantic comparison deferred/unsupported. | `not_comparable` |

`recurring_exact_criteria` **не входит** в closed set как отдельный pattern:
criteria уже являются частью exact cohort key, поэтому такой claim был бы
либо тавтологией, либо незаметно расширил бы grouping до criteria-only
semantic policy. Future criteria-level observation требует отдельного
contract.

### 7.2. State semantics

`current` и `historical` описывают, в какой bounded window находится
доказанный exact repeated pattern. `stable`, `changed` и `mixed` — не
психологические labels, а exact states cohort-local aggregation.

Ни один pattern не может называться `user_changed`, `personality`,
`preference` или `decision_rule`. В user-facing wording разрешены только
«наблюдаемый повтор exact choice», «смешанный выбор», «стабильный exact
pattern в двух окнах», «разные exact choices в current и historical windows»
и «недостаточно comparable evidence».

## 8. Minimum support и exact count semantics

### 8.1. Fixed thresholds

```text
minimum_comparable_observations = 3 distinct current Journal UUIDs
temporal_window_days             = 90 UTC days
active_horizon_days              = 180 UTC days
minimum_observations_per_change_window = 2
```

Один или два exact choice не создают permanent preference, stable behavior,
changed behavior или decision rule. При `n < 3` допустим только
`insufficient_evidence` с descriptive counts; это не claim о выборе.

`changed_over_time` требует минимум 4 observations: по 2 internally unanimous
observations в current и historical windows. При mixed choices внутри одной
window или меньшей поддержке обеих сторон результат остаётся `mixed` либо
`insufficient`, но не `changed`.

### 8.2. Numerator/denominator

Для каждой exact choice support хранится как:

```text
support_count = exact number of distinct observations choosing this option
total_comparable_observations = exact number of distinct observations in cohort
support_ratio = {
  numerator: support_count,
  denominator: total_comparable_observations,
}
```

`support_ratio` — exact integer pair. Он не является probability, confidence,
quality, reward или ranking score. Float, percentage-only display, Bayesian
formula, learned weight и hidden score запрещены.

Для `mixed_exact_choices` выдаются counts всех exact chosen options,
отсортированные по `option_index`, а не по support. Никакой majority/winner
или `dominant preference` не вычисляется.

Для `changed_over_time` top-level single-option `support_count` и ratio равны
`null`, потому что единого supporting choice нет; exact per-window counts и
ratios остаются видимыми. Для `not_comparable` denominator равен 0, ratio
`null`. Ratio равен `null` ровно при `denominator == 0`.

### 8.3. Duplicate, edit, delete и recency

- distinct `source_journal_uuid` — distinct observation; повторный scan одного
  UUID не удваивает count;
- duplicate canonical UUID или conflicting identity — whole-result error;
- одинаковые bodies под разными valid UUID не deduplicate;
- current note edit пересобирает observation из нового current projection;
  прежний derived choice не сохраняется как history;
- deletion supporting Journal удаляет его из next read model и не оставляет
  tombstone в Behavioral Self Model;
- deletion/edit Outcome меняет только `outcome_presence` metadata, если current
  relation после scan валиден; choice support/ratio не меняются;
- derived cache (если когда-либо будет отдельно разрешён) invalidated по
  `journal_snapshot_fingerprint`, policy fingerprint и current source scan;
- recency не даёт strength/weight. Время используется только для membership
  в fixed windows, temporal span и state classification;
- observations старше 180 дней не удаляются из vault, но не входят в active
  Stage 10 v1 cohort denominator; они могут быть показаны только как
  `outside_horizon_excluded` caveat future read model. All-time behavior требует
  отдельного policy gate.

## 9. Temporal semantics

### 9.1. Exact time policy

`generated_at` — aware application clock value, canonical UTC и время сборки
read model. Он не является evidence time.

Для observations принимается только:

```text
evidence_at_precision == exact
evidence_at is aware RFC3339 instant
evidence_at <= generated_at
```

Future `evidence_at`, invalid timestamp или impossible temporal relation не
получают fallback к `created`, `updated`, filename или UUID. Valid Stage 2
Journal с literal `unknown` исключается из comparable population и получает
`unknown_time_excluded`; если comparable evidence не осталось, state —
`insufficient`/`not_comparable` по exact cohort result.

### 9.2. Fixed windows

Для каждого build, после UTC normalization:

```text
current    = [generated_at - 90d, generated_at]
historical = [generated_at - 180d, generated_at - 90d)
```

Boundary ровно в `generated_at - 90d` относится к `current`; boundary ровно в
`generated_at - 180d` относится к `historical`. Values older than 180d и
future values не входят в active aggregate. This is bounded inclusion policy,
не recency weighting.

### 9.3. Stable и changed

- `stable` означает exact same option identity в обеих windows при total
  `n >= 3` и минимум одной observation в каждой window;
- `changed` означает exact different unanimous option identity между current и
  historical при минимум двух observations в каждой window;
- если earlier/current window содержит multiple options, это `mixed`, а не
  `changed`;
- если одна window не достигает threshold, это `repeated_exact_choice`,
  `mixed_exact_choices` или `insufficient_evidence` по counts, но не `stable`
  и не `changed`;
- вывод описывает изменение observed choice в exact cohort, а не изменение
  пользователя как личности.

## 10. Stated vs observed composition

Composition не выполняет semantic text matching. Existing Stage 4 direct claims
имеют human-readable body и dimensions `preference`/`belief`/`goal`, но не
имеют canonical option mapping к Decision Journal. Нормативный design gate
для будущего explicit mapping зафиксирован в
[stated-observed-mapping-v1-contract.md](stated-observed-mapping-v1-contract.md);
текущий Stage 10A/10B runtime по-прежнему не строит composition и не
сохраняет mapping.

| Composition state | Deterministic condition | v1 behavior |
| --- | --- | --- |
| `aligned` | Explicit future reviewed mapping связывает stated assertion с тем же exact cohort/option, и observed pattern поддерживает тот же identity. | Не строится автоматически; DEFER в Stage 10C. |
| `divergent` | Тот же explicit mapping доказан, но observed exact choice не совпадает со stated option. | Не строится автоматически; не называется «настоящий пользователь». |
| `stated_evidence_missing` | Behavioral cohort доказан, но в том же explicit mapped scope нет current Stated claim. | Может быть safe future read-model state, но не inference о том, что statement отсутствует вообще. |
| `behavioral_evidence_insufficient` | Stated claim существует, но exact cohort имеет менее 3 comparable observations. | Показывается как insufficient, не как divergence. |
| `not_comparable` | Нет exact mapping, scope/context различается или relation требует semantic equivalence. | Default v1 state; никакой lexical/LLM guess. |

В v1 `aligned` и `divergent` не emit-ятся, потому что owner-reviewed mapping
и structured stated scope отсутствуют. Stated Self Model не переписывает
Behavioral Model, Behavioral Model не переписывает Stated claim. Если
пользователь не согласен с derived pattern, canonical correction возможна
только как новая reviewed assertion через existing Personal Memory + Safe
Write.

## 11. Outcome boundary

Current valid linked Outcome Observation разрешён только как:

```text
outcome_presence = absent | present
```

`present` означает, что current scan нашёл хотя бы одну valid Outcome note с
точным `decision_id`; отсутствие outcome не означает failure, regret,
dissatisfaction или bad decision.

Запрещено превращать free-text `Actual result`, `Reassessment` или `Notes` в:

- success/failure;
- regret/satisfaction;
- utility/reward/quality;
- good/bad decision;
- positive/negative sentiment;
- training label или numeric outcome score.

Outcome не входит в cohort key, choice support, temporal state или ratio
denominators. Его `evidence_at` не backdates и не extends Journal decision
window. Structured reviewed outcome taxonomy — отдельный future schema/design
gate и не часть Stage 10 v1.

## 12. Stage 9 и Simulate Me boundary

Stage 9 остаётся operational/model-evaluation layer:

- `ProspectiveAuditEventV1` фиксирует, что validated Simulate Me operation была
  записана в operational store;
- `ProspectiveDecisionLinkV1` фиксирует explicit owner link к current Journal;
- prospective calibration aggregate показывает counts/ratios operation history;
- ни один из этих объектов не является actual-choice authority и не становится
  Behavioral observation;
- prediction correctness, match/mismatch, calibration ratio и audit recency
  не увеличивают и не уменьшают support Behavioral Model;
- Stage 9 audit/link history не читается как hidden behavioral database.

Разрешённое будущее направление:

```text
canonical reviewed decisions
  -> Behavioral Self Model
  -> optional future Simulate Me consumer
```

Запрещённое направление:

```text
Simulate Me prediction/audit/calibration
  -> Behavioral Self Model
```

Если Behavioral Self Model позже станет input Simulate Me, понадобится новый
versioned Simulate Me policy/derivation binding, explicit provenance change и
review prospective calibration baseline/reset semantics. Это не происходит
в #246 и не меняет current Stage 6/Stage 9 contracts.

## 13. Privacy и sensitive-inference restrictions

Behavioral Self Model не выводит по косвенным choices:

- mental-health diagnosis или therapy conclusion;
- political или religious identity;
- sexual orientation или sex-life;
- criminal propensity;
- employability или creditworthiness;
- personality type, manipulation susceptibility или hidden risk score;
- любые иные protected/sensitive traits без отдельного explicit owner-reviewed
  policy gate; по умолчанию такие inference forbidden.

Exact `domain` и labels — caller-owned canonical context, а не permission на
классификацию. Модель может сохранить safe domain/fingerprint для exact cohort
identity, но не переименовывает его в sensitive category и не строит trait из
выбранных options.

В derived DTO не копируются full Journal bodies, titles, paths, URLs, front
matter, raw outcome text или raw option labels. Для provenance используются
UUIDs/fingerprints и bounded current reread. Raw private content не пишется в
logs, errors, cache, Stage 9 store, browser storage или provider payload.

## 14. Persistence и rebuildability

В v1 применяется:

```text
on-demand current-vault rebuildable read model
```

Каждый build заново проходит current scanner/report и не принимает прошлый
Behavioral result как authority. Запрещены:

- permanent behavioral database;
- SQLite/operational DB только для Behavioral Model;
- vector DB, embeddings или provider cache;
- Stage 9 audit store как Behavioral storage;
- browser/local-storage persistence;
- write-back в vault, Journal, Outcome или Stated Self Model;
- background watcher, scheduler, auto-training или hidden profile.

Optional disposable cache возможен только в отдельном future gate с explicit
TTL/deletion/version/invalidation policy; в Stage 10 v1 cache отсутствует.

Current edit/delete behavior fail-closed и rebuildable:

```text
current vault -> current valid Journals -> current observations/cohorts -> current result
```

История старого derived result не сохраняется. Если владельцу нужна история
decision, она остаётся в canonical reviewed Journals, а не в модели.

## 15. Behavioral Self Model DTO и safe abstention

### 15.1. Policy identity

Composition-owned v1 policy фиксирует следующие identifiers:

```text
contract_version       = behavioral-self-model-v1
policy_id              = behavioral-self-model-exact-context-v1
derivation_version     = behavioral-self-model-derivation-v1
observation_version    = behavioral-observation-v1
grouping_policy        = exact-reviewed-decision-context-v1
support_policy         = min-3-distinct-journals-v1
temporal_policy        = two-90-day-windows-180-day-horizon-v1
outcome_policy         = presence-only-v1
composition_policy     = explicit-mapping-only-v1
```

`policy_fingerprint` — `sha256:` + 64 lowercase hex от canonical UTF-8 JSON
object с lexicographic keys, `sort_keys=true`, separators `,`/`:`, без BOM,
whitespace и dynamic keys. Exact payload v1:

```json
{"composition":"explicit-mapping-only-v1","contract":"behavioral-self-model-v1","grouping":"exact-reviewed-decision-context-v1","observation":"behavioral-observation-v1","outcome":"presence-only-v1","support":"min-3-distinct-journals-v1","temporal":"two-90-day-windows-180-day-horizon-v1","version":"1"}
```

Expected v1 fingerprint:
`sha256:1fb9ffaab67835c30f29150999ee45044578d4cb5a3fbb32359574cd080c66c0`.

Fingerprint является policy identity/invalidation key, не model score и не
confidence.

### 15.2. Exact result shape

```text
BehavioralRatioV1 {
  numerator:                 int >= 0
  denominator:               int > 0
}

BehavioralChoiceSupportV1 {
  option:                    BehavioralOptionIdentityV1
  support_count:             int >= 0
  support_ratio:             BehavioralRatioV1 | null
}

BehavioralTemporalSpanV1 {
  earliest_evidence_at:      RFC3339 aware datetime | null
  latest_evidence_at:        RFC3339 aware datetime | null
}

BehavioralWindowSummaryV1 {
  window:                    "current" | "historical"
  observation_count:         int >= 0
  choice_support:            tuple[BehavioralChoiceSupportV1, ...]
}

BehavioralProvenanceV1 {
  source_journal_uuids:      tuple[UUIDv7, ...]       # max 200, sorted
  source_count:              int >= 0
  provenance_fingerprint:    BehavioralHashV1
}

BehavioralPatternV1 {
  contract_version:          "behavioral-self-model-v1"
  derivation_version:        "behavioral-self-model-derivation-v1"
  policy_id:                 "behavioral-self-model-exact-context-v1"
  policy_fingerprint:        BehavioralHashV1
  cohort:                    BehavioralCohortIdentityV1 | null
  pattern_type:              closed pattern type from section 7
  state:                     closed state from section 7
  selected_option:           BehavioralOptionIdentityV1 | null
  support_count:             int >= 0 | null
  total_comparable_observations: int >= 0
  support_ratio:             BehavioralRatioV1 | null
  choice_support:            tuple[BehavioralChoiceSupportV1, ...]
  temporal_span:             BehavioralTemporalSpanV1
  windows:                   tuple[BehavioralWindowSummaryV1, ...]
  outcome_presence:          {present: int >= 0, absent: int >= 0}
  provenance:                BehavioralProvenanceV1
  caveats:                   tuple[fixed caveat code, ...]
}

BehavioralSelfModelResultV1 {
  contract_version:          "behavioral-self-model-v1"
  derivation_version:        "behavioral-self-model-derivation-v1"
  policy_id:                 "behavioral-self-model-exact-context-v1"
  policy_fingerprint:        BehavioralHashV1
  generated_at:              RFC3339 aware datetime, canonical UTC
  patterns:                  tuple[BehavioralPatternV1, ...]   # max 200
  eligible_journal_count:    int >= 0
  comparable_observation_count: int >= 0
  excluded_unknown_time_count: int >= 0
  excluded_outside_horizon_count: int >= 0
  caveats:                   tuple[fixed caveat code, ...]
}
```

DTO invariants:

- exact integer counts; no float fields, confidence field, probability field,
  rank, weights или hidden score;
- `support_ratio == null` iff its denominator is zero; otherwise
  `0 <= numerator <= denominator` and both values are exact integers;
- `selected_option` и top-level `support_count` заполнены только когда один
  exact option действительно является pattern subject (`repeated`/`stable`);
  for `mixed` и `changed` they are `null`, and all supports remain in
  `choice_support`/`windows`;
- `choice_support` sorted by option index, never by count; `windows` ordered
  `historical`, `current`;
- every provenance UUID resolves to a current valid Journal used by this
  pattern; no path/body/snippet substitute;
- all `source_journal_uuids` are unique and no pattern emits more than 200
  source UUIDs; exceeding any pattern/result byte bound returns
  `BEHAVIORAL_SELF_MODEL_RESULT_TOO_LARGE`, never a silently truncated result;
- `patterns` sorted by exact `cohort_fingerprint`, then `pattern_type`, then
  `state`; filesystem enumeration and support size do not affect order;
- `outcome_presence` counts only current valid presence/absence metadata and
  never become quality/utility metrics;
- `caveats` use a fixed ordered vocabulary and contain no raw private text;
- full result is either complete and valid or safe error; partial convincing
  profile is not a valid result.

### 15.3. Fixed caveat vocabulary

Minimum fixed codes, emitted only when applicable:

```text
support_is_descriptive
current_vault_rebuild
unknown_time_excluded
future_or_invalid_time_excluded
outside_horizon_excluded
outcome_presence_only
mixed_no_winner
temporal_state_is_cohort_local
stated_observed_mapping_missing
insufficient_comparable_evidence
not_comparable_under_v1
```

Adding a caveat that changes semantics requires a new policy/derivation
version. Caveats никогда не превращаются в claim text.

### 15.4. Fixed error/abstention taxonomy

Errors expose only stable code/message and no path, UUID list, body, labels,
exception text, secret or provider detail:

| Code | Fixed safe meaning | Result behavior |
| --- | --- | --- |
| `BEHAVIORAL_SELF_MODEL_SOURCE_UNAVAILABLE` | Current vault source cannot be read safely. | No result; no partial rebuild. |
| `BEHAVIORAL_SELF_MODEL_INVALID_JOURNAL` | Current enrolled Decision Journal or its relation fails Stage 2 integrity. | No result; no partial rebuild. |
| `BEHAVIORAL_SELF_MODEL_INSUFFICIENT_COMPARABLE_DECISIONS` | Valid build has no cohort meeting minimum comparable evidence. | Safe abstention/result state with `insufficient`; no behavioral claim. |
| `BEHAVIORAL_SELF_MODEL_AMBIGUOUS_GROUPING` | Exact current cohort identity cannot be proven uniquely. | No result for the ambiguous build; no guessed grouping. |
| `BEHAVIORAL_SELF_MODEL_UNSUPPORTED_SEMANTIC_COMPARISON` | Request or future integration asks for semantic/lens/mapping comparison outside v1. | No fallback to fuzzy/LLM; safe abstention/error. |
| `BEHAVIORAL_SELF_MODEL_RESULT_TOO_LARGE` | Complete bounded result/provenance exceeds fixed limit. | No truncation and no partial result. |
| `BEHAVIORAL_SELF_MODEL_POLICY_MISMATCH` | Missing, unknown or changed policy/derivation binding or fingerprint. | No result. |
| `BEHAVIORAL_SELF_MODEL_INVALID_REQUEST` | Request type, bounds or clock contract invalid. | No vault read and no result. |

`insufficient` и `not_comparable` — safe model states, а не разрешение
invent behavioral claim. Unknown time, absent domain и semantic mismatch
никогда не превращаются в a repeated choice.

## 16. Future Web/API и UX boundary

Это только design direction; implementation не входит в #246.

Будущий read-only owner/authenticated surface может показать:

- exact observed pattern и cohort fingerprints;
- repeated choices с `support_count/total_comparable_observations`;
- exact temporal span и current/historical/stable/changed/mixed states;
- insufficient evidence, unknown/outside-horizon caveats;
- outcome presence только как «later observation exists / absent», без quality
  interpretation;
- stated-vs-observed comparison только при explicit deterministic mapping.

UI/API не должен использовать:

- «настоящий ты»;
- «твоя реальная личность»;
- «подсознательная preference»;
- «истинный характер»;
- «гарантированная склонность».

Source drill-down может заново прочитать current Journal по UUID в bounded
owner-authorized operation. Browser payload/cache не становится authority.
Correction derived model не редактирует его напрямую: пользовательская
коррекция — новая reviewed canonical assertion через existing Personal Memory
и Safe Write. API не добавляет write-back endpoint и не пишет в Stage 9 store.

## 17. ACCEPT / CHANGE / RISK / DEFER

| Area | Verdict | Normative decision |
| --- | --- | --- |
| Canonical authority | **ACCEPT** | Actual choice берётся только из current valid `observed_decision + decision` Journal. |
| Derived boundary | **ACCEPT** | On-demand immutable read model; no canonical write-back, profile DB или hidden state. |
| Comparability | **ACCEPT** | Exact non-null domain + exact normalized ordered Situation/information/options/criteria. |
| Semantic grouping | **DEFER / FORBIDDEN AS AUTHORITY** | LLM, provider, embedding, fuzzy, alias и translation не влияют на v1 cohort. |
| Caller lens / owner mapping | **CHANGE** | Design gate зафиксирован в [stated-observed-mapping-v1-contract.md](stated-observed-mapping-v1-contract.md); runtime mapping/review остаётся отдельным Stage 10C implementation gate. |
| New canonical schema/taxonomy | **DEFER** | Stage 10 reuses Stage 2 fields; no migration/new note fields. |
| Observation DTO | **ACCEPT** | One current Journal -> one exact immutable observation; raw bodies/labels excluded. |
| Pattern set | **ACCEPT** | Closed repeated/mixed/stable/changed/insufficient/not-comparable set; recurring criteria отдельным pattern не является. |
| Minimum support | **ACCEPT** | 3 distinct comparable Journal UUIDs; change requires 2 unanimous observations per window. |
| Count/ratio semantics | **ACCEPT** | Exact integer counts and numerator/denominator pairs; no confidence/probability/weights. |
| Temporal policy | **ACCEPT** | Exact evidence time only; current/historical 90-day windows in 180-day horizon; unknown excluded. |
| Stated vs observed | **ACCEPT** | Layers remain separate; v1 defaults to not-comparable without explicit mapping. |
| Outcome | **ACCEPT** | Presence-only descriptive metadata; free text never becomes reward/success/regret. |
| Stage 9 | **ACCEPT** | Operational audit/link/calibration is not behavioral evidence and never feeds support. |
| Simulate Me | **DEFER** | Future one-way consumer requires new versioned policy and calibration provenance gate. |
| Privacy | **ACCEPT** | No sensitive/personality/diagnostic/risk inference; no raw private text in derived DTO/logs. |
| Persistence | **ACCEPT** | Current-vault rebuild only; no DB/vector/browser/Stage 9 storage. |
| Runtime/Web/API/QA | **DEFER** | Separate Stage 10A–10D implementation/release gates. |
| Roadmap status | **CHANGE** | Stage 10 is DESIGN CONTRACT; runtime is NOT IMPLEMENTED; Stage 11+ NOT STARTED. |

### Risks

1. Exact full-context equality produces false negatives: semantically identical
   situations written differently will not group. This is intentional; it is
   safer than semantic guessing and is visible as `not_comparable`.
2. Current vault has no historical snapshot. Edit/delete changes the next
   rebuild and cannot reconstruct what the old derived model said.
3. The fixed local-clock windows are a bounded application view, not proof of
   real-world event order beyond reviewed timestamps.
4. A caller-owned domain/option label can itself be sensitive text. Exact
   fingerprinting does not grant permission to classify or expose it.
5. Outcome presence is not outcome quality. Absence is not failure.

## 18. Exact next implementation scope: Stage 10A

The next implementation issue, if separately approved, is only:

```text
Stage 10A — deterministic behavioral observation/cohort core
```

It may implement only:

1. current `scan -> build_report` read/integrity boundary;
2. exact Stage 2 Journal eligibility and safe exclusion/error mapping;
3. existing Stage 2 projection consumption without changing parser/schema;
4. whitespace-only normalization for exact comparison;
5. immutable `BehavioralObservationV1` construction from one Journal;
6. exact cohort key/fingerprint construction and deterministic bucket order;
7. exact current/historical window membership and integer count primitives;
8. presence-only current Outcome relation metadata;
9. bounded DTO validation, fixed safe errors, no raw private diagnostics;
10. synthetic temporary-vault tests for exact equality, order sensitivity,
    missing/unknown time, duplicate UUID, edit/delete rebuild, outcome
    presence-only and no provider/network/vault write.

Stage 10A must not implement pattern claims, Stated-vs-Observed composition,
Web/API, UI, provider/LLM/embedding, new fields, migration, DB/cache,
Simulate Me changes, Stage 9 changes, Safe Write, `second-brain-vault`,
production deployment or next-Issue creation.

Expected decomposition after this contract:

```text
Stage 10A  deterministic behavioral observation/cohort core
Stage 10B  closed Behavioral Self Model pattern builder/read model
Stage 10C0 design  stated-vs-observed explicit mapping contract (approved)
Stage 10C  stated-vs-observed composition/runtime, after implementation approval
Stage 10D  Web/API + integration QA/closeout
```

These Issues are not created automatically by #246. Stage 10C0 is design-only;
its contract does not start Stage 10C runtime or create the next Issue.

## 19. Scope/acceptance checklist

- [x] Stage 10 is Cognitive Twin v2 Behavioral Self Model, not retroactive Stage 9 scope.
- [x] Actual-choice authority is only current reviewed Decision Journal.
- [x] Behavioral observation is derived, immutable in memory and rebuildable.
- [x] Comparability is exact, provider-free and fail-closed.
- [x] One Journal cannot become a permanent preference.
- [x] Minimum support, duplicate handling and exact ratios are normative.
- [x] Temporal unknown/current/historical/stable/changed/mixed semantics are explicit.
- [x] Stated and Behavioral layers remain separate and default to not-comparable.
- [x] Outcome free text cannot become reward, success, regret or quality.
- [x] Stage 9 prediction/audit/calibration cannot become behavior evidence.
- [x] Sensitive/personality/diagnostic inference is forbidden.
- [x] DTO, provenance, caveats, safe errors and bounded result behavior are explicit.
- [x] Persistence is on-demand rebuild; no DB/vector/browser/Stage 9 storage.
- [x] Stage 10A scope is exact and runtime is not implemented.
- [x] No vault/schema/provider/dependency/API/UI changes are authorized here.

`HUMAN_REQUIRED: none` for this design boundary. Any future need for a
canonical schema, provider, external service, secret, semantic mapping or
architecture expansion must remain `DEFER` and become a separate owner gate.
