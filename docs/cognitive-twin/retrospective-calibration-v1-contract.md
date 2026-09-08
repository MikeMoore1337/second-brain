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
5. storage timestamps самой Journal note не противоречат decision cutoff
   `D = evidence_at` этой note: если валидный `created > D`, note считается
   созданной после выбора и немедленно исключается с code
   `decision_body_created_after_cutoff`; если валидный `updated > D`, current
   body считается изменённым после выбора и исключается с code
   `decision_body_edited_after_cutoff`. Валидные `created <= D` и `updated <= D`
   не доказывают historical bytes, а отсутствующий/невалидный storage
   timestamp обрабатывается через `decision_note_metadata_invalid` по шагу 9;
6. current note identity (UUID, path и uniqueness) валидна;
7. `available_options` содержит от 2 до 8 items. Stage 2 разрешает до 20,
   но Stage 6 Simulate Me принимает максимум 8; options не выбираются,
   сортируются или усекaются для fit;
8. `chosen_option` после exact Stage 2 whitespace comparison соответствует
   ровно одному item `available_options`;
9. нет malformed/duplicate section, hidden extra body или qualifying canonical
   note diagnostic. В v1 exact qualifying diagnostic set для candidate Journal
   note — `NOTE_MISSING_TYPE`, `NOTE_INVALID_TYPE`,
   `NOTE_MISSING_TIMESTAMP`, `NOTE_INVALID_TIMESTAMP`, `NOTE_INVALID_TAGS`,
   `NOTE_INVALID_SOURCES`, `NOTE_INVALID_SOURCE_COUNT`,
   `NOTE_INVALID_SOURCE_RECORD`, `NOTE_SOURCE_MISSING_URI`,
   `NOTE_SOURCE_MISSING_KIND`, `NOTE_SOURCE_MISSING_RETRIEVED_AT`,
   `NOTE_SOURCE_INVALID_KIND`, `NOTE_SOURCE_INVALID_RETRIEVED_AT`,
   `NOTE_SOURCE_INVALID_PUBLISHED_AT`, `NOTE_SOURCE_INVALID_URI`,
   `NOTE_SOURCE_INVALID_METADATA` и `NOTE_INVALID_SOURCE`. Любой такой
   diagnostic даёт fixed exclusion code `decision_note_metadata_invalid`, если
   более ранняя specific check не сработала; invalid `created`/`updated` не
   трактируется как отсутствие timestamp. Для note с exact enrollment marker и
   raw metadata pair `evidence_kind=observed_decision` + `self_kind=decision`
   typed projection не является обязательным условием preclassification:
   текущие `PERSONAL_MEMORY_*` diagnostics также проверяются до projection.
   `PERSONAL_MEMORY_MISSING_EVIDENCE_AT`,
   `PERSONAL_MEMORY_INVALID_EVIDENCE_AT`,
   `PERSONAL_MEMORY_MISSING_EVIDENCE_AT_PRECISION`,
   `PERSONAL_MEMORY_INVALID_EVIDENCE_AT_PRECISION` и
   `PERSONAL_MEMORY_INVALID_EVIDENCE_AT_PAIR` дают соответствующий
   `decision_time_unknown` или `decision_time_not_exact_or_invalid`; остальные
   текущие `PERSONAL_MEMORY_*` diagnostics —
   `PERSONAL_MEMORY_INVALID_RECORD`, `PERSONAL_MEMORY_MISSING_EVIDENCE_KIND`,
   `PERSONAL_MEMORY_INVALID_EVIDENCE_KIND`,
   `PERSONAL_MEMORY_MISSING_SELF_KIND`, `PERSONAL_MEMORY_INVALID_SELF_KIND`,
   `PERSONAL_MEMORY_INVALID_KIND_PAIR` и `PERSONAL_MEMORY_INVALID_DOMAIN` —
   дают `decision_note_metadata_invalid`. Такой candidate увеличивает
   `decision_notes_seen` и ровно один excluded counter; invalid domain не может
   silently исчезнуть из denominator accounting. `NOTE_MISSING_ID`/`NOTE_INVALID_ID`
   относятся к `decision_identity_invalid_or_duplicate`, а
   content-root `NOTE_FRONT_MATTER_ERROR` и `NOTE_READ_ERROR` останавливают
   всю run на scan-completeness gate §7.

Case с unknown decision time, invalid body, invalid choice mapping, duplicate
identity или 9–20 options не запускает Simulate Me. Он учитывается в
`decision_notes_seen` и соответствующей `excluded_decisions` category, а не
как mismatch.

Ошибки Stage 2 parser классифицируются до generic `decision_body_invalid` по
конкретной section: `list_too_few`/`list_too_many` в `Available options` дают
`decision_option_count_unsupported`; `list_duplicate` в этой же section и
`chosen_missing` дают `decision_option_identity_invalid`. Те же list errors в
`Criteria` и любые другие parser reasons дают `decision_body_invalid`.
Implementation обязана сохранить exact Stage 2 whitespace comparison и не
выводить section или reason эвристически из user text; допустим только
детерминированный parser/probe с теми же section boundaries.

Presence of a linked Outcome Observation не делает case prediction input и не
делает target более trustworthy. Оно не исключает иначе valid Journal case:
Outcome всегда обрабатывается как post-choice data и полностью игнорируется
для replay. Malformed Journal body или malformed Journal identity, наоборот,
делают сам case ineligible.

Проверка `created > D` и `updated > D` относится к самой Decision Journal
note, а не только к Self Model source context: такой case не получает target,
не строит `E` query и не читает current context. Это fail-closed защита от
принятия current body, который был создан или изменён уже после выбора.

Классификация исключений является взаимоисключающей: одна classified Journal
note увеличивает не более одного counter в `excluded_decisions`. До replay
проверки выполняются в таком first-match order:
`decision_identity_invalid_or_duplicate`, `decision_time_unknown`,
`decision_time_not_exact_or_invalid`, `decision_body_created_after_cutoff`,
`decision_body_edited_after_cutoff`, `decision_note_metadata_invalid`,
`decision_option_count_unsupported`, `decision_option_identity_invalid`, затем
`decision_body_invalid`.
Побеждает первая failing check; последующие applicable failures не считаются.
Note, прошедшая все девять checks, становится eligible, а eligible case никогда
не получает code из `excluded_decisions`.

Чтобы operation оставалась bounded, одна run обрабатывает не более
`MAX_DECISION_CASES_V1 = 512` classified Journal cases. Если current scan
содержит больше, чем этот cap, run возвращает top-level
`RETROSPECTIVE_CALIBRATION_TOO_LARGE` без частичного aggregate и без silent
sampling. Этот cap не заменяет resource budget самого scan. До materialization
полного `ScanReport` canonical scan обязан соблюдать все три fixed limits:

- `MAX_SCAN_ENTRIES_V1 = 16384` filesystem entries, inspected across declared
  roots;
- `MAX_SCAN_DOCUMENTS_V1 = 4096` content-root Markdown documents, whose
  front matter/body may be materialized;
- `MAX_SCAN_BYTES_V1 = 16777216` cumulative raw UTF-8 bytes of those
  content-root Markdown documents.

Entry/document counters и raw byte size проверяются на scan boundary до
добавления entry/document в materialized snapshot; file size должен быть
проверен до чтения body и не может обходить оставшийся byte budget. Если любой
limit превышен, bounded scanner останавливается и возвращает только
`RETROSPECTIVE_CALIBRATION_TOO_LARGE`; partial snapshot не передаётся в
`build_report`, partial aggregate не выдаётся. Streaming implementation,
которая не materializes полный snapshot, может использовать ту же boundary и
те же limits. Result budget — `MAX_RESULT_BYTES_V1 = 65536` canonical UTF-8
bytes; truncation запрещена.

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
меняет eligibility и переводит case в соответствующий fixed excluded code;
`created > D` получает `decision_body_created_after_cutoff`, а `updated > D`
получает `decision_body_edited_after_cutoff`. Это не является leakage и не
может считаться mismatch. Наличие masked data в current storage не является
разрешением на retrospective leakage.

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
| Decision Journal note `created > D` | Journal считается созданным после выбора и исключается с `decision_body_created_after_cutoff`; target/query/context не строятся, temporal caveats не увеличиваются. `created` по-прежнему не является evidence time. |
| Decision Journal note `updated > D` | Journal считается изменённым после выбора и исключается с `decision_body_edited_after_cutoff`; target/query/context не строятся, temporal caveats не увеличиваются. `updated` не используется как evidence time. |
| `updated > D` | Note считается известным post-choice edit и не входит; increment `edited_after_cutoff_excluded` caveat. `updated` не используется как evidence time. |
| Note deleted or UUID no longer resolves uniquely | Missing source не восстанавливается Search/path/created; affected claim исключается. Если resulting filtered context boundary нельзя построить или revalidate, используется `prechoice_context_unavailable` по §8.3. |
| Current note body/metadata changed and scanner no longer validates it | Current invalid source не используется и не чинится эвристикой; affected claim исключается. Если сама context boundary из-за этого невалидна, используется `prechoice_context_unavailable` по §8.3. |
| `updated` отсутствует или не позже `D` | Это только отсутствие известного later mutation; не доказательство historical bytes. `created` по-прежнему не является eligibility timestamp. |

### 5.2.1 Детерминированный подсчёт temporal caveats

Единица подсчёта для всех `temporal_caveats` — один eligible Decision Journal
case, который достиг deterministic boundary проверки current context sources
(см. §7, шаг 4), а не source note, claim или отдельный reference. Eligible
case, завершившийся до этой boundary, получает нулевые temporal caveats. Это
включает `prechoice_request_invalid` (в том числе слишком большой или
невалидный query) и любой другой terminal failure, назначенный до начала
проверки context sources; его состояние всё равно представляется через
соответствующий `replay_invalid` или `replay_unavailable` code. Для такого
case operation не читает current context только ради подсчёта caveats.

Для eligible case, достигшего context-source boundary, каждый applicable code
увеличивается не более одного раза: наличие одного или нескольких
соответствующих sources даёт один increment. Conditions считаются независимо и
могут co-occur: например, один source с `evidence_at > D` и `updated > D`
увеличивает и `later_evidence_excluded`, и `edited_after_cutoff_excluded`, по
одному разу. `unknown_evidence_excluded` увеличивается, если хотя бы один
candidate source имеет `evidence_at=unknown`; аналогично
`later_evidence_excluded` — если есть source с `evidence_at > D`, а
`edited_after_cutoff_excluded` — если есть source с `updated > D`.
`historical_snapshot_unavailable` также увеличивается не более одного раза за
такой case, когда context-source boundary достигнута. У excluded до §3 cases
temporal caveats не считаются: их состояние представляется через
`excluded_decisions`.

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

Filtered-context boundary обязана принять только результат существующей Self
Model policy с exact identity:

```text
self_model_derivation_version = "self-model-derivation-v1"
self_model_policy_fingerprint = "d7969ba732665c0406736b0e669a9123ccd0ee7b12de36f57899f59f94282cd3"
```

Implementation валидирует эту пару существующим Self Model result/policy
validator до context projection. Missing, malformed или mismatched identity
делает context boundary недоверенной и даёт `prechoice_context_unavailable`;
fallback к другой Self Model policy запрещён.

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

1. Выполнить один bounded current canonical scan через pre-materialization
   boundary §3; не читать Search index и не писать vault. Если scan превышает
   `MAX_SCAN_ENTRIES_V1`, `MAX_SCAN_DOCUMENTS_V1` или `MAX_SCAN_BYTES_V1`,
   немедленно вернуть `RETROSPECTIVE_CALIBRATION_TOO_LARGE` без
   `build_report`, `decision_notes_seen` или partial aggregate. Только после
   успешного bounded scan построить typed `ScanReport`, затем до любой
   классификации Journal cases применить scan-completeness gate существующей
   report boundary: manifest должен быть валиден, а
   `report.content_scan_complete` должен быть true. Gate fail-closed
   срабатывает при любом content-affecting completeness diagnostic, включая
   `NOTE_READ_ERROR`, `VAULT_DIRECTORY_READ_ERROR`,
   `VAULT_ENTRY_RESOLVE_ERROR`, `VAULT_LINKED_DIRECTORY`,
   `VAULT_OVERLAPPING_ROOTS`, `VAULT_PATH_ESCAPE`, `VAULT_ROOT_MISSING`,
   `VAULT_ROOT_NOT_DIRECTORY` или `NOTE_FRONT_MATTER_ERROR`, когда diagnostic
   относится к content root. При false gate вернуть
   `RETROSPECTIVE_CALIBRATION_SOURCE_UNAVAILABLE` без `decision_notes_seen`,
   partial aggregate или silent omission unreadable/malformed documents.
   Classified Journal cases ограничить `MAX_DECISION_CASES_V1` без sampling.
2. Отсортировать cases по `(decision_at UTC, lowercase canonical note UUID,
   safe relative path)`; unknown-time и ineligible cases не входят в replay
   order, но остаются в exclusion counts.
3. Для каждого eligible case проверить §4 mask, построить options и query,
   определить target отдельно и проверить exact request bounds. Если request
   validation завершается до context-source boundary, case получает mapping из
   §8.3 и temporal caveats для него не считаются.
4. Построить filtered current-vault context по §5; каждый current UUID и
   metadata проверить заново. С началом проверки context sources достигается
   temporal-caveat boundary §5.2.1. Unknown/later/edited source claims
   exclude; untrusted context boundary даёт `unavailable`. Если boundary не
   достигнута из-за pre-context failure, temporal caveats остаются нулевыми.
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
decision_identity_invalid_or_duplicate
decision_time_unknown
decision_time_not_exact_or_invalid
decision_body_created_after_cutoff
decision_body_edited_after_cutoff
decision_note_metadata_invalid
decision_option_count_unsupported
decision_option_identity_invalid
decision_body_invalid
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
| `SimulateMeRequest` нельзя построить или он не проходит existing strict request validation, включая byte bound percent-encoded query | `prechoice_request_invalid` | `invalid_count += 1`; context не читается, branch не вызывается, temporal caveats для case равны нулю. |
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
поэтому считается только как `prechoice_request_invalid`; Simulate Me и
context-source inspection не вызываются, а temporal caveats для case не
увеличиваются. То же правило применяется к любому terminal failure до
context-source boundary. Для eligible case, достигшего этой boundary,
`historical_snapshot_unavailable` увеличивается не более одного раза, чтобы
показать limitation отсутствия snapshot; это temporal caveat, который не
меняет selection или unavailable/invalid counts.

## 9. Error and partial-result taxonomy

Top-level errors return only fixed `{code, message}` and no aggregate:

| Code | Fixed message | When |
| --- | --- | --- |
| `RETROSPECTIVE_CALIBRATION_INVALID_REQUEST` | `retrospective calibration request failed validation` | Invalid bounded operation input or unsupported configuration before scan. |
| `RETROSPECTIVE_CALIBRATION_CANCELLED` | `retrospective calibration operation cancelled` | Global cancellation before aggregate completion. |
| `RETROSPECTIVE_CALIBRATION_SOURCE_UNAVAILABLE` | `retrospective calibration source unavailable` | Scan/build_report не создал валидный manifest/report или content-completeness gate обнаружил content-affecting diagnostic (например, `NOTE_READ_ERROR`); aggregate не строится, unreadable document нельзя молча пропустить. |
| `RETROSPECTIVE_CALIBRATION_TOO_LARGE` | `retrospective calibration input exceeds its bounded limit` | More than 512 classified cases или превышен любой pre-materialization scan limit; no sampling, `build_report` of partial input or partial result. |
| `RETROSPECTIVE_CALIBRATION_RESULT_TOO_LARGE` | `retrospective calibration result exceeds its byte limit` | Full canonical aggregate exceeds 65536 bytes; no truncation. |

Per-case semantics preserve progress of other cases inside the final aggregate:

| Case condition | Classification | Other cases |
| --- | --- | --- |
| Missing/unknown/non-exact decision time or malformed Journal | excluded before replay | remain eligible for processing and counted by code |
| Journal `updated > decision_at` | `decision_body_edited_after_cutoff` before target/query/context construction | case остаётся в `decision_notes_seen` и учитывается своим code |
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

Calibration обязана принимать только exact approved Stage 6 identity из
существующего Simulate Me contract:

```text
simulate_me_derivation_version = "simulate-me-v1"
simulate_me_policy_id = "simulate-me-direct-exact-v1"
simulate_me_policy_fingerprint = "sha256:07aa1d0d57fdd2d009087c05423fc4eb9304da70e87f32b1790fbd4753f21c3a"
```

Эти три значения проверяются на request/branch boundary и входят в
calibration fingerprint; отсутствие или mismatch любого значения даёт
`simulate_me_policy_mismatch`, а не silent fallback к другой Stage 6 policy.

Fingerprint input is exactly this one-line ASCII JSON, encoded as UTF-8 with
`sort_keys=true`, separators `,` and `:`, no BOM and no trailing newline:

```json
{"decision_eligibility":"current-valid-stage2-journal-exact-time-v1","decision_note_metadata":"all-canonical-note-and-personal-memory-errors-excluded-v3","diagnostics":"exclusive-phase-mapped-code-sums-v2","evidence_cutoff":"exact-aware-inclusive-utc;unknown-excluded-v1","execution":"one-provider-free-simulate-me-replay-per-eligible-decision-v1","journal_body_cutoff":"updated-after-decision-excluded-v1","journal_creation_cutoff":"created-after-decision-excluded-v1","leakage":"mask-choice-reasons-confidence-expectation-outcome-later-context-eligible-only-v2","metrics":"bounded-counts-and-exact-ratios-no-confidence-v1","option_failure_mapping":"available-options-before-generic-body-v1","option_identity":"journal-order-exact-label-request-local-id-v1","query_serialization":"utf8-byte-percent-encode-unreserved-v1","result_size_guard":"internal-canonical-utf8-byte-length-v1","scan_completeness":"content-affecting-diagnostics-abort-before-classification-v2","scan_limits":"entries-16384;documents-4096;bytes-16777216-v1","simulate_me_derivation_version":"simulate-me-v1","simulate_me_policy_fingerprint":"sha256:07aa1d0d57fdd2d009087c05423fc4eb9304da70e87f32b1790fbd4753f21c3a","simulate_me_policy_id":"simulate-me-direct-exact-v1","self_model_derivation_version":"self-model-derivation-v1","self_model_policy_fingerprint":"d7969ba732665c0406736b0e669a9123ccd0ee7b12de36f57899f59f94282cd3","source_authority":"current-vault-only-no-historical-snapshot-v1","storage_metadata":"created-updated-never-evidence-time-v1","temporal_caveat_counting":"per-eligible-case-independent-codes-v1","temporal_caveat_scope":"after-request-validation-context-source-inspection-v1","unknown_time":"exclude-and-report-caveat-v1","version":"1"}
```

Expected fingerprint:

```text
sha256:4daf20f43e211e12af3587eb88ea2cef363ef3abbe385c414cf0d6f9f9684446
```

Fingerprint changes when any eligibility, masking, cutoff, execution, scan
limit, option identity, metrics, source-authority, bound Self Model identity or
bound Stage 6 identity rule changes. It is not a model score and does not
authorize policy optimization.

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
8. После serialization проверяется exact length canonical UTF-8 bytes; она должна
   быть `<= MAX_RESULT_BYTES_V1`. Это internal measurement для size guard, а не
   поле DTO и не ключ опубликованного JSON.

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
| Создание/изменение Journal после cutoff | `created > decision_at` даёт `decision_body_created_after_cutoff`, `updated > decision_at` даёт `decision_body_edited_after_cutoff`; target/query/context не строятся, это никогда не mismatch или temporal caveat. |
| Диагностика canonical metadata note | Любая exact qualifying `NOTE_*` storage/provenance или `PERSONAL_MEMORY_*` diagnostic, включая `NOTE_INVALID_TYPE`, `PERSONAL_MEMORY_INVALID_DOMAIN`, `NOTE_MISSING_TIMESTAMP` и `NOTE_INVALID_TIMESTAMP`, даёт fixed exclusion mapping, если более ранняя specific exclusion не победила; note не становится eligible. |
| Linked Outcome present | `actual_result`, `reassessment`, `notes` never enter query/context/metrics. |
| Later evidence | `evidence_at > decision_at` is excluded; `later_evidence_excluded` is counted once per eligible case if any such source exists, and it cannot make a prediction. |
| Unknown-time evidence | `unknown` is excluded; `unknown_evidence_excluded` is counted once per eligible case if any such source exists, with no assumption that it pre-existed choice. |
| Chosen/reasons/expected-result leakage | For a case already accepted by §3, changing masked sections cannot change request options/query/context, branch availability or selection; a change that breaks §3 is an eligibility exclusion, not a mismatch. |
| Current edited evidence | `updated > decision_at` source is excluded; `edited_after_cutoff_excluded` is counted once per eligible case if any such source exists, with no historical body reconstruction or fallback. |
| Deleted/missing evidence | UUID miss is excluded/unavailable; Search/path/created does not recover it. |
| Journal with 9–20 options | excluded as unsupported Stage 6 option count; no truncation or option selection. |
| Chosen option missing or whitespace-duplicate options | `decision_option_identity_invalid` before generic `decision_body_invalid`; never scored as mismatch. |
| Invalid choice, body, duplicate identity or time | excluded with fixed code; never scored as mismatch. |
| Неполный content scan, включая `NOTE_READ_ERROR` или `NOTE_FRONT_MATTER_ERROR` | top-level `RETROSPECTIVE_CALIBRATION_SOURCE_UNAVAILABLE`; aggregate и partial counters не выдаются, unreadable/malformed document нельзя молча пропустить. |
| Scan превышает `MAX_SCAN_ENTRIES_V1`, `MAX_SCAN_DOCUMENTS_V1` или `MAX_SCAN_BYTES_V1` | top-level `RETROSPECTIVE_CALIBRATION_TOO_LARGE` до `build_report`; partial snapshot и aggregate не выдаются. |
| Invalid/too-large request до context inspection | `prechoice_request_invalid`; context и branch не вызываются, temporal caveats для case остаются нулевыми. |
| Exact cutoff boundary | evidence at exactly `decision_at` is included; later instant is excluded after UTC conversion. |
| Malformed filtered context | unavailable/invalid safe category; never unfiltered current prediction. |
| Self Model derivation/fingerprint mismatch at context boundary | `prechoice_context_unavailable`; no alternate Self Model policy or unfiltered context fallback. |
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
| Exact Stage 6 identity in calibration request boundary and fingerprint | **ACCEPT** |
| Exact Self Model identity at filtered-context boundary and in fingerprint | **ACCEPT** |
| Bounded counts, exact numerator/denominator ratios and coverage | **ACCEPT** |
| Pre-materialization scan limits with fail-closed overrun | **ACCEPT** |
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
- bounded current scan с pre-materialization limits §3; unbounded
  `VaultReader.scan()` нельзя использовать как calibration boundary;
- existing Stage 4/5 current context validation;
- existing Stage 6 `SimulateMeRequest`, `BuildSimulateMe` seam and result
  validator;
- in-memory aggregate with the exact DTO, codes, order and fingerprint above.

Если implementation не может сохранить pre-choice filtered-context boundary,
он обязан вернуть safe `unavailable`/DEFER, а не читать full current context.
Prospective calibration остаётся out of scope до отдельного operational audit
design.
