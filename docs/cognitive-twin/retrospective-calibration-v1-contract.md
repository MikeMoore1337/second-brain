# Retrospective Calibration v1 — pre-choice replay contract

Статус этого документа: **DESIGN ONLY / BOUNDED CONTRACT**. Он описывает
rebuildable retrospective calibration для Stage 7 и не создаёт runtime,
prospective logging, audit store, training pipeline или policy update.

Цель — on-demand проверить, как approved Stage 6 Simulate Me policy
воспроизводит canonical reviewed Decision Journal choices, передавая в replay
только информацию, которая допустима до исторического выбора. Важное
ограничение: текущий vault не хранит исторические snapshots. Поэтому этот
contract не называет current-vault projection настоящей исторической копией;
это bounded replay из текущего canonical состояния с explicit temporal
cut-off и fail-closed exclusions.

## 1. Scope, dependencies и safety boundary

Retrospective Calibration v1 зависит только от следующих уже существующих
границ:

- Stage 2 canonical Decision Journal и Outcome Observation contracts;
- current canonical scan/report и typed note projections;
- Stage 4/5 current Self Model context boundary;
- approved provider-free Stage 6 Simulate Me request, result validator,
  exact-match policy и fixed policy fingerprint.

Calibration не является частью Assistant или Compare. Assistant input,
recommendation, rationale и Compare Delta в этот replay не входят. Calibration
не вызывает Decision Journal как prediction authority: Journal даёт только
исторический target и разрешённые pre-choice поля для конкретного case.

Разрешённый результат — ephemeral bounded aggregate метрик. Он может быть
удалён и заново построен из current vault. Никакие prediction, calibration
result, target choice, outcome, caveat, UUID списка или derived claim не
записываются в vault, database, cache, journal, audit log или новую schema.

В v1 явно отсутствуют:

- prospective prediction logging и operational audit history;
- true historical vault snapshots, event sourcing или immutable evidence
  archive;
- LLM, provider, network, embeddings, RAG, vector DB и Search/Retrieval как
  authority;
- confidence score, probability, score, Brier, ECE, calibration gap, bins,
  tuning или threshold fitting;
- recency/frequency/majority/evidence weighting и learning behavioral
  patterns;
- policy auto-update, model training, goal update, outcome inference или
  write-back;
- ranking users/domains или small-sample personal trait claims.

`HUMAN_REQUIRED: none` в этой revision: conservative unknown-time exclusion и
current-vault limitation фиксируются как safe default. Запрос на настоящий
historical snapshot, новый canonical history field, prospective audit или
temporal inference beyond existing `evidence_at` выходит за этот contract и
требует отдельного owner decision.

## 2. Термины и authority

| Термин | Нормативное значение |
| --- | --- |
| Decision Journal case | Текущая managed note с exact enrollment marker, Stage 2 `observed_decision`/`decision`, валидным structured body и known exact decision `evidence_at`. |
| decision time | `evidence_at` именно Decision Journal note; только aware datetime с `evidence_at_precision=exact`; `created` и `updated` не являются заменой. |
| historical target | Request-local ID option, однозначно полученный из Journal `chosen_option` после построения options; target не передаётся Simulate Me. |
| pre-choice context | Только allowed Journal sections и current canonical Self Model evidence, чей `evidence_at` не позже decision time и чья current identity безопасно проверена. |
| current-vault projection | Bounded in-memory projection current scan, отфильтрованный для одного decision time; не historical snapshot и не claim о прежнем содержимом note. |
| excluded evidence | Unknown-time, later-than-cutoff, post-cutoff-edited, missing, duplicate или otherwise unverifiable current evidence; оно не получает silent fallback. |
| eligible decision | Case, прошедший base eligibility §3. Его replay всё ещё может стать `unavailable` или `invalid`; это не prediction и не abstention. |

Единственный canonical source — current vault через существующий scanner и
`build_report`/typed projections. Search index, filename, path, UUID order,
`created`, `updated`, cache и Web projection не являются самостоятельной
authority для исторического содержимого.

## 3. Exact eligibility of a Decision Journal case

Calibration сканирует current canonical report и рассматривает только notes,
которые marker-gated распознаются как Stage 2 Decision Journal:

1. note managed, имеет exact YAML scalar
   `second_brain_personal_memory: 1`;
2. metadata содержит ровно supported pair
   `evidence_kind=observed_decision` и `self_kind=decision`;
3. current note body проходит existing `parse_decision_journal_body`;
4. `evidence_at` — aware RFC3339 datetime с
   `evidence_at_precision=exact`; `unknown` не становится replay case;
5. current note identity (UUID, path и uniqueness) валидна;
6. `available_options` содержит от 2 до 8 items. Stage 2 разрешает до 20,
   но Stage 6 Simulate Me принимает максимум 8; options не выбираются,
   сортируются или усекaются для fit;
7. `chosen_option` после exact Stage 2 whitespace comparison соответствует
   ровно одному item `available_options`;
8. нет malformed/duplicate section, hidden extra body или current diagnostic,
   делающего эту Journal note недостоверной.

Case с unknown decision time, invalid body, invalid choice mapping, duplicate
identity или 9–20 options не запускает Simulate Me. Он учитывается в
`decision_notes_seen` и соответствующей `excluded_decisions` category, а не
как mismatch.

Presence of a linked Outcome Observation не делает case prediction input и не
делает target более trustworthy. Оно не исключает иначе valid Journal case:
Outcome всегда обрабатывается как post-choice data и полностью игнорируется
для replay. Malformed Journal body или malformed Journal identity, наоборот,
делают сам case ineligible.

Классификация исключений является взаимоисключающей: одна classified Journal
note увеличивает не более одного counter в `excluded_decisions`. До replay
проверки выполняются в таком first-match order:
`decision_identity_invalid_or_duplicate`, `decision_body_invalid`,
`decision_time_unknown`, `decision_time_not_exact_or_invalid`,
`decision_option_count_unsupported`, затем `decision_option_identity_invalid`.
Побеждает первая failing check; последующие applicable failures не считаются.
Note, прошедшая все шесть checks, становится eligible, а eligible case никогда
не получает code из `excluded_decisions`.

Чтобы operation оставалась bounded, одна run обрабатывает не более
`MAX_DECISION_CASES_V1 = 512` classified Journal cases. Если current scan
содержит больше, чем этот cap, run возвращает top-level
`RETROSPECTIVE_CALIBRATION_TOO_LARGE` без частичного aggregate и без silent
sampling. Result budget — `MAX_RESULT_BYTES_V1 = 65536` canonical UTF-8 bytes;
truncation запрещена.

## 4. Mandatory pre-choice masking matrix

Для case, уже прошедшего всю §3 eligibility, masking выполняется до построения
`SimulateMeRequest` и до любого current context read, зависящего от case.
Содержимое masked section не может попасть
через alternate field, linked note, Search hit, UUID relation или derived
summary.

| Decision Journal section / relation | Replay disposition | Причина и разрешённое использование |
| --- | --- | --- |
| `Situation` | **ALLOW** | Входит в deterministic query как pre-choice task context. |
| `Available options` | **ALLOW** | Строит общий request-local options tuple; labels сохраняют Journal order и exact text. |
| `Information known at decision time` | **ALLOW** | Входит в deterministic query; не расширяется current knowledge после cutoff. |
| `Criteria` | **ALLOW** | Входит в deterministic query как явные pre-choice criteria. |
| `Chosen option` | **MASK** | Сохраняется отдельно как observed target только после replay; в query/options authority кроме derived option list не передаётся и в current context не читается. |
| `Reasons` | **MASK** | Даже если часть reasons могла быть записана до выбора, v1 не классифицирует её и исключает как possible post-choice explanation. |
| `Confidence` | **MASK** | Это субъективная choice-time field, не Stage 6 confidence; она не нужна для exact prediction и может раскрывать target. |
| `Expected result` | **MASK** | Всегда исключается: section может отражать выбранный option и future expectation. Нет conditional inspection. |
| `Actual result` | **MASK / INVALID IF PRESENT IN INITIAL JOURNAL** | Existing initial Journal validator требует пустую section. Никакой later result не участвует в replay. |
| `Reassessment` | **MASK / INVALID IF PRESENT IN INITIAL JOURNAL** | Existing initial Journal validator требует пустую section. Более поздний пересмотр не является pre-choice input. |
| Linked Outcome Observation (`actual_result`, `reassessment`, `notes`) | **MASK** | Не передаётся в query, options, Self Model context или scoring; target — только Journal `chosen_option`. |
| Any evidence with `evidence_at > decision_at` | **EXCLUDE** | Исключается до context projection, без recency or future-data fallback. |
| Any evidence with `evidence_at = unknown` | **EXCLUDE + CAVEAT** | Не предполагается, что evidence существовало до выбора; default safe policy — exclude. |
| Any derived context whose source time/identity is not proven | **EXCLUDE / UNAVAILABLE** | Не заменяется `created`, `updated`, Search result или current unfiltered context. |

Для case, уже прошедшего §3 eligibility, `chosen_option`, `reasons`,
`confidence`, `expected_result`, outcome и post-choice evidence не могут влиять
ни на selection, ни на branch availability, ни на metrics category. Изменение
masked section, которое до этой границы делает Journal body невалидным,
меняет eligibility и переводит case в fixed excluded category; это не является
leakage и не может считаться mismatch. Наличие masked data в current storage
не является разрешением на retrospective leakage.

## 5. Temporal cutoff и current-vault limitation

### 5.1 Exact cutoff

Для case с decision time `D`:

- каждое known aware evidence time сначала переводится в UTC;
- evidence разрешается только при `evidence_at <= D` (граница inclusive);
- одинаковый instant с разными explicit offsets сравнивается после UTC
  conversion;
- `evidence_at=unknown` никогда не трактуется как раннее, текущее или
  eligible time;
- `created` и `updated` никогда не подставляются вместо `evidence_at` и не
  меняют canonical evidence time.

Current Self Model claim используется для replay только если его supporting и
contextual source refs можно заново сопоставить с текущими canonical notes и
проверить temporal predicate. Если у одного derived claim есть хотя бы один
source ref, который unknown, later, deleted, duplicate или edited after the
cutoff, claim целиком исключается; refs не вырезаются из claim частично, потому
что текущая derivation могла зависеть от всей совокупности.

После исключения unsafe claims допустим bounded empty context. Тогда
Simulate Me может честно вернуть `abstention/no_matching_evidence`. Если же
невозможно построить или валидировать сам filtered context boundary, case
получает `unavailable`, а не искусственный abstention или prediction.

### 5.2 Current edits and deletions

| Current state of source note | Exact behavior |
| --- | --- |
| Current canonical note valid; `evidence_at` exact and `<= D`; no known later mutation | Может войти в current-vault temporal projection; result всё равно помечен mode `current-vault-temporal-projection-v1`. |
| `evidence_at` unknown | Note не входит; increment `unknown_evidence_excluded` caveat. |
| `evidence_at > D` | Note не входит; increment `later_evidence_excluded` caveat. |
| `updated > D` | Note считается известным post-choice edit и не входит; increment `edited_after_cutoff_excluded` caveat. `updated` не используется как evidence time. |
| Note deleted or UUID no longer resolves uniquely | Missing source не восстанавливается Search/path/created; affected claim исключается. Если resulting filtered context boundary нельзя построить или revalidate, используется `prechoice_context_unavailable` по §8.3. |
| Current note body/metadata changed and scanner no longer validates it | Current invalid source не используется и не чинится эвристикой; affected claim исключается. Если сама context boundary из-за этого невалидна, используется `prechoice_context_unavailable` по §8.3. |
| `updated` отсутствует или не позже `D` | Это только отсутствие известного later mutation; не доказательство historical bytes. `created` по-прежнему не является eligibility timestamp. |

### 5.2.1 Детерминированный подсчёт temporal caveats

Единица подсчёта для всех `temporal_caveats` — один eligible Decision Journal
case, а не source note, claim или отдельный reference. Для каждого eligible
case каждый applicable code увеличивается не более одного раза: наличие одного
или нескольких соответствующих sources даёт один increment. Conditions
считаются независимо и могут co-occur: например, один source с
`evidence_at > D` и `updated > D` увеличивает и
`later_evidence_excluded`, и `edited_after_cutoff_excluded`, по одному разу.
`unknown_evidence_excluded` увеличивается, если хотя бы один candidate source
имеет `evidence_at=unknown`; аналогично `later_evidence_excluded` — если есть
source с `evidence_at > D`, а `edited_after_cutoff_excluded` — если есть
source с `updated > D`. У excluded до §3 cases temporal caveats не считаются:
их состояние представляется через `excluded_decisions`.

Если текущий vault не позволяет доказать нужную source identity, contract не
делает вид, что восстановил прошлое. В v1 допускается только current valid
projection с этой explicit limitation; requirement на точные historical bytes
или edit history — отдельный HUMAN_REQUIRED design.

### 5.3 Context authority

Future implementation обязан получить current Stage 4/5 claims через existing
application boundary и построить per-case in-memory filtered projection. Он не
может вызвать обычный unfiltered current `BuildSimulateMe` и назвать его
historical replay. Если текущий Stage 6 implementation не имеет safe seam для
передачи такого filtered context, case получает
`prechoice_context_unavailable` по §8.3; hidden fallback к full current context
запрещён.

Разрешены только существующие Stage 6 semantics:

- direct `preference` и `goal` могут поддержать option;
- `belief` может остаться contextual-only;
- `decision_rule` и `behavioral_pattern` не являются eligible dimensions;
- exact whole-label matching, без weights, aliases, recency, count или
  semantic inference.

## 6. Exact option identity и Simulate Me request

### 6.1 Request-local IDs

Для Journal options в исходном order `1..N` создаются только ephemeral IDs:

```text
o1, o2, ... oN
```

`oi` — ASCII request-local identifier, полученный только из Journal order; он
не является UUID, hash, canonical identity или persistence key. `label` каждой
`SimulateMeOption` равен exact current Journal `available_options[i]` text.
Не создаются aliases, synonyms, translations, fuzzy matches или LLM mapping.

Observed target mapping выполняется отдельно тем же exact Stage 2 comparison,
который разрешил Journal body: after Stage 2 whitespace normalization,
`chosen_option` должен correspond to one and only one available option. The
target ID is retained in ephemeral case state only after request options are
constructed; it is never sent as query content, Self Model input, option
authority or branch metadata.

If Simulate Me returns a prediction, score сравнивает только predicted
request-local ID и target request-local ID byte-for-byte. Labels, case, inner
whitespace, reasons, evidence, UUIDs и outcome не участвуют в match.

### 6.2 Deterministic query

Query строится только из allowed Journal fields и не содержит chosen/reasons/
confidence/expected/actual/reassessment/outcome. Сначала для каждого field
применяется обратимое кодирование `E(value)`: исходные UTF-8 байты value идут
последовательно; байты из `[A-Za-z0-9._~-]` остаются как ASCII, каждый другой
байт заменяется на `%` и две uppercase hex digits. Это не смысловая переработка,
а инъективная сериализация, поэтому многострочный текст, Unicode и разделители
восстанавливаются однозначно и не становятся control code points. Канонический
шаблон не содержит trailing newline и занимает одну физическую строку:

```text
Situation={E(Situation)}|InformationKnownAtDecisionTime={E(Information known at decision time)}|Criteria={E(criterion 1)};{E(criterion 2)};...
```

`Situation` может быть empty только если existing Journal parser это допустил;
information и criteria должны оставаться valid по Stage 2. Values берутся как
точный current body text без смысловой переработки, затем кодируются только
через `E`; criteria сохраняют Journal order. Итоговый query проходит existing
Simulate Me strict UTF-8/NFC/edge-strip/control validation и `1..4096` UTF-8 byte
bound. Если percent-encoded query слишком велик, он получает
`query_too_large_or_invalid` → `prechoice_request_invalid` и не запускает
branch. Truncation запрещена, а внутренняя validation не переписывает query.

Итоговый request имеет ровно этот shape:

```text
SimulateMeRequest {
  query:   retrospective_query
  options: tuple[SimulateMeOption(id="o1", label=...), ...]
}
```

Нет отдельного caller-provided evidence list, chosen field, target field,
historical timestamp field, prediction field или outcome field. Filtered
current context передаётся только через approved application context seam §5.3.

## 7. Bounded execution algorithm

Для одной run порядок следующий:

1. Выполнить один current canonical scan; не читать Search index и не писать
   vault. Classified Journal cases ограничить `MAX_DECISION_CASES_V1` без
   sampling.
2. Отсортировать cases по `(decision_at UTC, lowercase canonical note UUID,
   safe relative path)`; unknown-time и ineligible cases не входят в replay
   order, но остаются в exclusion counts.
3. Для каждого eligible case проверить §4 mask, построить options и query,
   определить target отдельно и проверить exact request bounds.
4. Построить filtered current-vault context по §5; каждый current UUID и
   metadata проверить заново. Unknown/later/edited source claims exclude;
   untrusted context boundary даёт `unavailable`.
5. Выполнить ровно одну independent provider-free Simulate Me attempt. Нет
   retry, fallback, alternate model, short-circuit или call к unfiltered
   current context.
6. Валидировать branch result existing
   `validate_simulate_me_result` и exact Stage 6 policy identity/fingerprint.
7. Классифицировать только validated terminal result:
   `prediction` → `predicted`; `abstention` с
   `no_matching_evidence`/`multiple_options_supported` → `abstention`;
   `insufficient_or_invalid_current_context` → `unavailable` с exact code
   `prechoice_context_unavailable`, потому что pre-choice context не был
   получен; malformed/wrong-policy result → exact `replay_invalid` code по
   §8.3.
8. Сравнить predicted request-local ID с retained target ID, обновить bounded
   in-memory counters и temporal caveat counters.
9. Собрать один canonical aggregate. Если canonical bytes превышают
   `MAX_RESULT_BYTES_V1`, вернуть top-level result-too-large без truncation.

Global cancellation до завершения aggregate возвращает
`RETROSPECTIVE_CALIBRATION_CANCELLED`; partial counters наружу не выдаются.
Любая exception вне safe typed boundary не превращается в prediction. Она
классифицируется по exact phase mapping §8.3 и не раскрывает exception, path,
body или provider detail.

## 8. Result DTO и exact metrics

### 8.1 DTO

```text
RetrospectiveCalibrationCountV1 {
  code:  fixed enum string
  count: int >= 0
}

RetrospectiveCalibrationRatioV1 {
  numerator:   int >= 0
  denominator: int > 0
}

RetrospectiveCalibrationMetricsV1 {
  decision_notes_seen:          int >= 0
  eligible_decisions:           int >= 0
  predicted_decisions:          int >= 0
  abstentions:                  int >= 0
  exact_option_match_count:     int >= 0
  mismatch_count:               int >= 0
  unavailable_count:            int >= 0
  invalid_count:                int >= 0
  coverage:                     RetrospectiveCalibrationRatioV1 | null
  accuracy_non_abstained:       RetrospectiveCalibrationRatioV1 | null
}

RetrospectiveCalibrationResultV1 {
  derivation_version: "retrospective-calibration-v1"
  policy_id:           "retrospective-simulate-me-exact-cutoff-v1"
  policy_fingerprint:  "sha256:" + 64 lowercase hex chars
  reconstruction_mode: "current-vault-temporal-projection-v1"
  metrics:             RetrospectiveCalibrationMetricsV1
  excluded_decisions:  tuple[RetrospectiveCalibrationCountV1, ...]
  replay_unavailable:  tuple[RetrospectiveCalibrationCountV1, ...]
  replay_invalid:      tuple[RetrospectiveCalibrationCountV1, ...]
  temporal_caveats:    tuple[RetrospectiveCalibrationCountV1, ...]
}
```

Aggregate result намеренно не содержит Journal text, labels, chosen option,
actual result, UUID, path, note title, domain, per-user row или per-case body.
Internal test-only case state может иметь request-local `case_index` и fixed
codes, но не публикуется и не сохраняется.

### 8.2 Metric meaning и invariants

- `decision_notes_seen` — number of current classified Stage 2 Journal cases,
  including cases excluded before replay.
- `eligible_decisions` — cases, прошедшие §3 и для которых replay attempt was
  allowed. Это denominator для coverage даже если attempt стал unavailable or
  invalid.
- `predicted_decisions` — only validated Stage 6 `prediction` results whose
  selected option belongs to the request-local option namespace.
- `abstentions` — only validated `no_matching_evidence` or
  `multiple_options_supported`; Stage 6 insufficient-context is not silently
  counted here.
- `exact_option_match_count` and `mismatch_count` compare only target and
  predicted request-local IDs.
- `coverage = predicted_decisions / eligible_decisions`, represented as exact
  numerator/denominator; null when denominator is zero.
- `accuracy_non_abstained = exact_option_match_count /
  predicted_decisions`, represented as exact numerator/denominator; null when
  no validated prediction exists. Unavailable/invalid cases do not become
  predictions or abstentions.

Required invariants:

```text
decision_notes_seen = excluded_decisions_total + eligible_decisions
eligible_decisions = predicted_decisions + abstentions
                     + unavailable_count + invalid_count
predicted_decisions = exact_option_match_count + mismatch_count
coverage.numerator = predicted_decisions
coverage.denominator = eligible_decisions
accuracy_non_abstained.numerator = exact_option_match_count
accuracy_non_abstained.denominator = predicted_decisions
```

Уравнения для `coverage` применяются только когда `coverage` не равен `null`;
`coverage` равен `null` ровно при `eligible_decisions == 0`. Уравнения для
`accuracy_non_abstained` применяются только когда этот ratio не равен `null`;
он равен `null` ровно при `predicted_decisions == 0`. У `null` ratios нет полей
для dereference, и это не ослабляет count equalities выше.

`coverage` и `accuracy_non_abstained` не являются confidence, probability,
quality guarantee или validated personal trait. No minimum sample claim is
made; `eligible_decisions` and exact denominators remain visible.

### 8.3 Fixed diagnostic code sets

Каждый array содержит все свои codes ровно один раз в указанном порядке,
включая zero counts. Unknown code, dynamic map key или user text запрещены.

`excluded_decisions`:

```text
decision_time_unknown
decision_time_not_exact_or_invalid
decision_body_invalid
decision_option_count_unsupported
decision_option_identity_invalid
decision_identity_invalid_or_duplicate
```

`replay_unavailable`:

```text
prechoice_context_unavailable
historical_context_unreconstructable
simulate_me_unavailable
```

`replay_invalid`:

```text
prechoice_request_invalid
simulate_me_result_invalid
simulate_me_policy_mismatch
calibration_composition_invalid
```

`temporal_caveats`:

```text
unknown_evidence_excluded
later_evidence_excluded
edited_after_cutoff_excluded
historical_snapshot_unavailable
```

Для eligible case после §3 применяется ровно одна first-match mapping из
следующей таблицы; вторичные причины не создают второй code:

| Условие first-match | Exact code | Counter и effect на branch |
| --- | --- | --- |
| `SimulateMeRequest` нельзя построить или он не проходит existing strict request validation, включая byte bound percent-encoded query | `prechoice_request_invalid` | `invalid_count += 1`; context не читается, branch не вызывается. |
| Approved Stage 6 policy ID или fingerprint не совпадает с exact v1 identity на request/branch boundary, либо returned policy identity отличается | `simulate_me_policy_mismatch` | `invalid_count += 1`; scoring и fallback запрещены. |
| Approved context seam сообщает, что для решения pre-choice eligibility нужны historical bytes или edit history, но v1 не имеет historical snapshot input | `historical_context_unreconstructable` | `unavailable_count += 1`; historical fallback и branch invocation запрещены. Простое исключение later/unknown evidence по §5 не является этим code. |
| Approved filtered current context нельзя построить или revalidate из-за сбоя current report, projection, type, metadata или trust boundary; либо validated branch result равен `insufficient_or_invalid_current_context` | `prechoice_context_unavailable` | `unavailable_count += 1`; prediction и artificial abstention запрещены. |
| Единственная approved Simulate Me attempt не достигает terminal result из-за unavailable operation, timeout или cancellation на case boundary | `simulate_me_unavailable` | `unavailable_count += 1`; sibling cases продолжают. Global cancellation использует top-level code из §7. |
| Returned result object не проходит `validate_simulate_me_result`, включая impossible selected option или result invariant | `simulate_me_result_invalid` | `invalid_count += 1`; mismatch не выставляется. |
| Validated branch result нельзя согласовать с retained target/options или exact composition invariants aggregate | `calibration_composition_invalid` | `invalid_count += 1`; alternate composition и retry запрещены. |

Mapping упорядочена по фазам и является exclusive: rows проверяются сверху
вниз, и при первом applicable row обработка останавливается. Fixed arrays всё
равно содержат каждый code ровно один раз, включая zero counts; их суммы
являются нормативными:

```text
excluded_decisions_total = sum(count(code) for code in excluded_decisions)
unavailable_count = sum(count(code) for code in replay_unavailable)
invalid_count = sum(count(code) for code in replay_invalid)
```

`query_too_large_or_invalid` является deterministic pre-branch request failure и
поэтому считается только как `prechoice_request_invalid`; Simulate Me не
вызывается. `historical_snapshot_unavailable` увеличивается один раз на
eligible case, чтобы показать limitation отсутствия snapshot; это temporal
caveat, который не меняет selection или unavailable/invalid counts.

## 9. Error and partial-result taxonomy

Top-level errors return only fixed `{code, message}` and no aggregate:

| Code | Fixed message | When |
| --- | --- | --- |
| `RETROSPECTIVE_CALIBRATION_INVALID_REQUEST` | `retrospective calibration request failed validation` | Invalid bounded operation input or unsupported configuration before scan. |
| `RETROSPECTIVE_CALIBRATION_CANCELLED` | `retrospective calibration operation cancelled` | Global cancellation before aggregate completion. |
| `RETROSPECTIVE_CALIBRATION_SOURCE_UNAVAILABLE` | `retrospective calibration source unavailable` | Current canonical scan cannot produce a trusted report at all. |
| `RETROSPECTIVE_CALIBRATION_TOO_LARGE` | `retrospective calibration input exceeds its case limit` | More than 512 classified cases; no sampling or partial result. |
| `RETROSPECTIVE_CALIBRATION_RESULT_TOO_LARGE` | `retrospective calibration result exceeds its byte limit` | Full canonical aggregate exceeds 65536 bytes; no truncation. |

Per-case semantics preserve progress of other cases inside the final aggregate:

| Case condition | Classification | Other cases |
| --- | --- | --- |
| Missing/unknown/non-exact decision time or malformed Journal | excluded before replay | remain eligible for processing and counted by code |
| Safe empty filtered context; no exact support | validated abstention | preserved |
| Multiple distinct supported options | validated abstention | preserved |
| Current context cannot be safely projected | `prechoice_context_unavailable` per §8.3 | preserved |
| Simulate Me operation unavailable/timeout/cancelled at case boundary | `simulate_me_unavailable` per §8.3 | preserved unless global cancellation aborts run |
| Wrong DTO, wrong policy, impossible selected option or result invariant | exact `replay_invalid` code per §8.3 | preserved |
| Valid prediction | predicted then exact match/mismatch | preserved |

No invalid or unavailable case is called `mismatch`; no excluded case is
called `abstention`; no post-choice target is used to rescue a failed replay.

## 10. Policy identity and fingerprint

Normative identities:

```text
derivation_version = "retrospective-calibration-v1"
policy_id = "retrospective-simulate-me-exact-cutoff-v1"
reconstruction_mode = "current-vault-temporal-projection-v1"
```

Fingerprint input is exactly this one-line ASCII JSON, encoded as UTF-8 with
`sort_keys=true`, separators `,` and `:`, no BOM and no trailing newline:

```json
{"decision_eligibility":"current-valid-stage2-journal-exact-time-v1","diagnostics":"exclusive-phase-mapped-code-sums-v1","evidence_cutoff":"exact-aware-inclusive-utc;unknown-excluded-v1","execution":"one-provider-free-simulate-me-replay-per-eligible-decision-v1","leakage":"mask-choice-reasons-confidence-expectation-outcome-later-context-eligible-only-v2","metrics":"bounded-counts-and-exact-ratios-no-confidence-v1","option_identity":"journal-order-exact-label-request-local-id-v1","query_serialization":"utf8-byte-percent-encode-unreserved-v1","source_authority":"current-vault-only-no-historical-snapshot-v1","storage_metadata":"created-updated-never-evidence-time-v1","temporal_caveat_counting":"per-eligible-case-independent-codes-v1","unknown_time":"exclude-and-report-caveat-v1","version":"1"}
```

Expected fingerprint:

```text
sha256:4e353355521919b0d5253cb76dd6baec27d3f94467b940068c9be7603112439c
```

Fingerprint changes when any eligibility, masking, cutoff, execution, option
identity, metrics or source-authority rule changes. It is not a model score and
does not authorize policy optimization.

## 11. Canonical serialization

When a result is produced, serialization is deterministic:

1. JSON UTF-8, no BOM, no trailing newline, direct non-ASCII, no `NaN`/`Infinity`
   and no floats;
2. separators exactly `,` and `:`, insignificant spaces absent;
3. root key order exactly:
   `derivation_version`, `policy_id`, `policy_fingerprint`,
   `reconstruction_mode`, `metrics`, `excluded_decisions`,
   `replay_unavailable`, `replay_invalid`, `temporal_caveats`;
4. metrics key order exactly:
   `decision_notes_seen`, `eligible_decisions`, `predicted_decisions`,
   `abstentions`, `exact_option_match_count`, `mismatch_count`,
   `unavailable_count`, `invalid_count`, `coverage`,
   `accuracy_non_abstained`;
5. ratio key order exactly `numerator`, `denominator`; null ratios remain JSON
   `null`;
6. count object key order exactly `code`, `count`; arrays preserve the fixed
   code order from §8.3;
7. all integers are JSON integers and bool is not accepted as an integer;
8. `result_bytes` is the exact length of canonical UTF-8 bytes and must be
   `<= MAX_RESULT_BYTES_V1`.

The aggregate contains no per-case array, so note order cannot leak through
result rows. Processing order remains normative for deterministic diagnostics
and test fixtures: decision UTC instant, lowercase UUID string, then safe
relative path. No dynamic sorting, map key or locale collation is allowed.

## 12. Privacy and no-side-effect boundary

The operation may hold in memory only bounded current scan data, one case's
masked request, filtered typed context, validated Stage 6 result and aggregate
counters. It must:

- never read `second-brain-vault` directly outside the existing reader boundary;
- never use Search/Retrieval as evidence authority or fallback;
- never send private context, Journal body, target, outcome or UUIDs to a
  provider or network;
- never persist request, selected option, prediction, evidence, metrics or
  audit event;
- never mutate Journal, Outcome, Personal Memory, Self Model, schema or policy;
- never emit absolute paths, note bodies, titles, exception text, secrets or
  provider details in errors;
- never convert aggregate accuracy into recommendation, confidence, personality
  claim or behavioral rule.

The only canonical data that remains authoritative after the run is the
original user-reviewed vault. Calibration is derived, ephemeral and
rebuildable; it is not a new source of truth.

## 13. Synthetic validation matrix

Future implementation tests use synthetic/temp managed-vault fixtures only.
They must not use the real vault, network, credentials, provider, persistent
database or write path.

| Case | Required assertion |
| --- | --- |
| Correct prediction from pre-choice evidence | exact request-local target/prediction IDs match; `predicted=1`, `match=1`. |
| Multiline Journal fields | Обратимое `E` encoding сохраняет exact UTF-8 field bytes, не удаляет supported Stage 2 content и даёт strict-valid single-line query, если не превышен bounded query byte cap. |
| Valid no-match | Stage 6 `no_matching_evidence` is `abstentions=1`, not unavailable or mismatch. |
| Valid multiple-support ambiguity | `multiple_options_supported` is abstention; no count/recency tie-break. |
| Mismatch | Valid prediction with different request-local ID increments mismatch only. |
| Linked Outcome present | `actual_result`, `reassessment`, `notes` never enter query/context/metrics. |
| Later evidence | `evidence_at > decision_at` is excluded; `later_evidence_excluded` is counted once per eligible case if any such source exists, and it cannot make a prediction. |
| Unknown-time evidence | `unknown` is excluded; `unknown_evidence_excluded` is counted once per eligible case if any such source exists, with no assumption that it pre-existed choice. |
| Chosen/reasons/expected-result leakage | For a case already accepted by §3, changing masked sections cannot change request options/query/context, branch availability or selection; a change that breaks §3 is an eligibility exclusion, not a mismatch. |
| Current edited evidence | `updated > decision_at` source is excluded; `edited_after_cutoff_excluded` is counted once per eligible case if any such source exists, with no historical body reconstruction or fallback. |
| Deleted/missing evidence | UUID miss is excluded/unavailable; Search/path/created does not recover it. |
| Journal with 9–20 options | excluded as unsupported Stage 6 option count; no truncation or option selection. |
| Invalid choice, body, duplicate identity or time | excluded with fixed code; never scored as mismatch. |
| Exact cutoff boundary | evidence at exactly `decision_at` is included; later instant is excluded after UTC conversion. |
| Malformed filtered context | unavailable/invalid safe category; never unfiltered current prediction. |
| Stage 6 wrong policy or malformed result | invalid; sibling cases remain in aggregate. |
| Aggregate arithmetic | all §8.2 invariants, null ratio rules and fixed count arrays hold. |
| Deterministic rebuild | same synthetic vault and policy produce byte-identical canonical result. |
| No provider/network/write/persistence | seams are not called; filesystem snapshot is unchanged. |

Tests must include current-only limitation explicitly: a valid current note with
no known later mutation is a projection input, not proof of the note's former
bytes. A fixture requiring true snapshot reconstruction must be marked DEFERRED,
not made to pass through heuristic metadata.

## 14. ACCEPT / DEFER / HUMAN_REQUIRED matrix

| Area | Decision |
| --- | --- |
| Stage 2 exact Journal eligibility and observed target | **ACCEPT** |
| Allowed/masked section matrix and unconditional chosen/reasons/expected masking | **ACCEPT** |
| Inclusive exact UTC cutoff; unknown-time exclusion with visible caveat | **ACCEPT** |
| Current-vault temporal projection without historical snapshot claim | **ACCEPT** |
| Current edit/deletion safe exclusion and no `created`/`updated` fallback | **ACCEPT** |
| Journal-order `o1..oN` request-local IDs and exact target comparison | **ACCEPT** |
| Existing provider-free Stage 6 exact prediction/abstention semantics | **ACCEPT** |
| Bounded counts, exact numerator/denominator ratios and coverage | **ACCEPT** |
| Domain breakdown, per-user rows or small-sample trait interpretation | **DEFER** |
| Confidence, Brier/ECE, probabilistic calibration, bins or tuning | **DEFER** |
| Recency/frequency/weighting, behavioral learning or policy optimization | **DEFER** |
| Historical snapshot store, immutable evidence archive or new canonical field | **HUMAN_REQUIRED** if requested |
| Prospective audit/logging, prediction persistence or operational calibration DB | **HUMAN_REQUIRED** if requested |
| New temporal inference, outcome-derived canonical inference or provider input | **HUMAN_REQUIRED** if requested |
| Runtime implementation, Web/CLI projection and production rollout | **DEFER to separate implementation/release task** |

В этой revision active owner choice отсутствует: default conservative policy
принята внутри design scope. Любая попытка расширить его за отмеченные
boundaries должна остановить implementation и открыть новый reviewed issue.

## 15. Future implementation boundary

Этот issue не добавляет `src/` code, tests, dependencies, vault writes,
provider adapters, persistence или UI. Следующий implementation task, если
contract merged, должен оставаться provider-free и использовать существующие
application contracts:

- current scanner/report и typed Decision Journal projections;
- existing Stage 4/5 current context validation;
- existing Stage 6 `SimulateMeRequest`, `BuildSimulateMe` seam and result
  validator;
- in-memory aggregate with the exact DTO, codes, order and fingerprint above.

Если implementation не может сохранить pre-choice filtered-context boundary,
он обязан вернуть safe `unavailable`/DEFER, а не читать full current context.
Prospective calibration остаётся out of scope до отдельного operational audit
design.
