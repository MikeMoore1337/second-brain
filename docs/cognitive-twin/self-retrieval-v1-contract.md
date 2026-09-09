# Self Retrieval v1 — contract

Статус этого документа: **DESIGN / APPROVED FOR MECHANICAL CORE**. Он закрывает
issue [#88](https://github.com/MikeMoore1337/second-brain/issues/88) и задаёт
границу, достаточную для механической реализации issue #89. Core #89, local
Web #90 и CLI #91 уже merged в current `main`; этот contract остаётся их
нормативным source of truth. Историческая design base — `main` commit
`e1067be7ffa0c5cfc89b6317ffdc8f68cccdb3de`.

Этот документ не меняет canonical schema, `schema_version`, Search/Retrieval
DTO, Self Model policy, `second-brain-vault` или consumer semantics. В текущем
contract нет активного `HUMAN_REQUIRED`: все решения ограничены уже
существующими lexical Search, current UUID reread и merged Stage 4 Self Model.

## 1. Цель и инварианты

Self Retrieval v1 строит bounded read-only personal context для одного literal
query. Search возвращает только кандидатов. Каждый кандидат, который попадает
в финальный result, заново читается по UUID из current canonical vault.

Каноническим источником остаётся `second-brain-vault`. Search index,
`SearchHit`, snippet, ordinal rank и Self Retrieval result — disposable derived
state или audit metadata; они не становятся evidence truth.

Обязательные инварианты:

- используется только существующий lexical `SearchVault`/`SearchIndexPort`;
- query сохраняет literal `AND` semantics и существующие Search bounds;
- `SearchHit.snippet` никогда не становится final body;
- каждый final item строится из успешного current `RetrieveManagedNote` по
  exact UUID;
- изменившийся body принимается только из current reread; stale snippet не
  используется даже как fallback;
- missing/deleted candidate не resurrect из index, snippet или cache;
- exact Self Model association допускается только через
  `SelfModelEvidenceRef.note_id` в `supporting_evidence`;
- нет semantic aggregation, hidden relevance weight, confidence calculation,
  status/conflict/stale/supersede resolution или recommendation;
- результат полностью находится в памяти, не сохраняется и не пишет в vault;
- ошибка целостности даёт safe error, а не убедительный partial context.

`Assistant`, `Simulate Me` и `Compare` не являются режимами запроса Stage 5.
Они смогут потреблять явно labelled context позднее, но не получают policy
или authority из этого contract.

## 2. Границы и зависимости

Существующая read-only цепочка не меняется:

```text
FileSystemVaultReader.scan()
  -> build_report()
  -> SearchDocument projection
  -> SearchIndexPort.rebuild/search
  -> SearchHit candidate
  -> RetrieveManagedNote.execute(note_id)
  -> current RetrievedNote
```

Stage 5 добавляет application composition над этой цепочкой:

```text
SearchVault.execute(SearchRequest)
  -> ordered candidates
  -> RetrieveManagedNote.execute(UUID) for each candidate
  -> bounded SelfContextItem
  -> exact supporting-UUID claim links
```

`BuildSelfModel` вызывается один раз для того же request с его уже утверждённой
`DEFAULT_SELF_MODEL_POLICY`. Client не передаёт policy, derivation version,
fingerprint, evidence kind, self kind или UUID authority override. Empty valid
Self Model result допустим и даёт context без claim links.

Самостоятельный runtime не должен добавлять новые методы в
`SearchIndexPort`, менять `SearchHit`/`RetrievedNote`, читать SQLite напрямую,
добавлять второй scanner, network/provider adapter, cache, DB или persistence.

## 3. Точные application DTO

Ниже приведена normative форма для #89. Имена могут быть изменены только на
эквивалентные без изменения полей, bounds и semantics.

### 3.1 Request

```text
SelfContextRequest
  query: str
  limit: int = 20
  max_content_bytes: int = 65536
```

Валидация до любого чтения vault или вызова index:

| Поле | Bound / правило |
| --- | --- |
| `query` | ровно `str`, UTF-8 не больше `4096` bytes, не blank, без C0/C1/Unicode format controls; существующие `MAX_SEARCH_QUERY_TERMS = 32` и literal `AND` semantics сохраняются через Search validation |
| `limit` | ровно `int`, `1..50`; это существующие `MIN_SEARCH_LIMIT..MAX_SEARCH_LIMIT` |
| `max_content_bytes` | ровно `int`, `1..65536`; default `65536` |

`bool` не принимается как `int`. `max_content_bytes` разрешает только
уменьшить bounded context; значение выше `65536` отклоняется. Core передаёт
`query` и `limit` в существующий `SearchRequest`, а не реализует новый parser.

`max_content_bytes` считается по UTF-8 content projection каждого включённого
item: `title`, `body`, все `tags` и один `\n` между соседними content fields.
UUID, enum names, timestamps и JSON framing в этот personal-content budget не
входят. Core возвращает фактически использованный `content_bytes`, чтобы
consumer не принимал budget за точный размер wire response.

### 3.2 Exact current context item

```text
SelfContextItem
  note_id: UUIDv7
  note_type: NoteType
  title: str
  body: str
  tags: tuple[str, ...]
  created: aware datetime
  updated: aware datetime | None
  search_rank: int
  self_model_claims: tuple[SelfContextClaim, ...]
```

`search_rank` — только 1-based ordinal позиции в tuple, который вернул
`SearchIndexPort.search`. Это не score, confidence, relevance, priority или
truth indicator. Raw BM25 score по-прежнему не существует в public DTO.

`title`, `body`, `tags`, `created` и `updated` копируются из current
`RetrievedNote`, а не из `SearchHit`. `relative_path` намеренно не входит в
result: для Stage 5 context достаточно canonical UUID и безопасной content
projection; абсолютные пути, front matter и внутренние diagnostics не
экспортируются.

`body` не обрезается молча. Если очередной current item не помещается в
оставшийся budget, item не включается, а result явно помечает budget
truncation. Таким образом core не создаёт partial body, похожий на полный.

### 3.3 Exact Self Model link

```text
SelfContextClaim
  dimension: SelfModelDimension
  claim: str
  supporting_note_ids: tuple[UUIDv7, ...]
  derivation_version: str
  policy_fingerprint: str
```

Link создаётся только когда `item.note_id` равен одному из UUID в
`SelfModelClaim.supporting_evidence`. Нельзя связывать claim по title, body,
domain, query, ordinal, path, `contextual_evidence` или
`contradicting_evidence`. Claim остаётся отдельным уже построенным derived
claim; core не объединяет claims, не пересчитывает их text и не выводит score.

`supporting_note_ids` сохраняет только UUID из exact current claim и имеет
deterministic order. `derivation_version` и `policy_fingerprint` копируются
из validated `SelfModelResult`; consumer может проверить происхождение без
получения policy details. Confidence envelope, включая `score=None`, не
превращается в Self Retrieval relevance/confidence.

### 3.4 Result and exclusions

```text
SelfContextExclusion
  search_rank: int
  reason: SelfContextExclusionReason

SelfContextResult
  items: tuple[SelfContextItem, ...]
  candidate_count: int
  included_count: int
  excluded_count: int
  exclusions: tuple[SelfContextExclusion, ...]
  truncated: bool
  content_bytes: int
  self_model_derivation_version: str
  self_model_policy_fingerprint: str
```

`SelfContextExclusionReason` имеет только bounded значения:

```text
context_budget_exceeded
candidate_not_found
```

Deleted/missing current UUID получает `candidate_not_found` и не появляется в
`items`; UUID, snippet и path для exclusion не возвращаются. Если
`RetrieveManagedNote` сообщает identity conflict, backend failure или
несоответствие возвращённого UUID кандидату, это не обычное exclusion, а
safe error всего request: integrity failure не маскируется частичным result.

`candidate_count` равен числу Search hits после bounded Search call;
`included_count == len(items)`; `excluded_count == len(exclusions)`;
`candidate_count == included_count + excluded_count`. `exclusions`
сохраняют candidate order. `truncated` равен `true` только если budget
исключил хотя бы один candidate; missing candidate сам по себе не делает
result truncated.

При budget overflow core идёт в candidate order, не переставляет последующие
items и явно исключает все дальнейшие candidates как
`context_budget_exceeded`. Это audit-able truncation, а не reranking.

## 4. Execution contract

`BuildSelfContext.execute(request)` выполняет ровно следующие шаги:

1. Проверяет exact request type и все bounds. При ошибке не читает vault и не
   вызывает index, reader или Self Model.
2. Вызывает `SearchVault` с `SearchRequest(query=request.query,
   limit=request.limit)`. Search остаётся единственным lexical parser и
   возвращает ordered `SearchHit` candidates.
3. Однократно вызывает `BuildSelfModel` с default bounded request и
   owner-approved policy. Результат должен пройти существующую валидацию;
   policy/fingerprint не приходят от клиента.
4. Для каждого hit по его `note_id` вызывает `RetrieveManagedNote`. До
   inclusion проверяется, что returned `note_id` точно равен candidate UUID,
   DTO имеет current safe identity и content fields, а content budget не
   превышен.
5. Строит `SelfContextItem` только из current reread и сохраняет ordinal
   `search_rank`. `SearchHit.snippet` отбрасывается до формирования item.
6. Добавляет только exact supporting-UUID claim links из validated Self Model;
   отсутствие link является нормальным результатом и не запускает поиск
   «похожих» claims.
7. Применяет deterministic content budget в candidate order и формирует
   totals/exclusions. Возвращает полный bounded DTO или один safe error.

Search может увидеть snapshot A, а current reread — snapshot B. Snapshot B
всегда является authority для final body/metadata. Этот v1 contract не
обещает multi-read transaction и не вводит snapshot ID, cache или stale
status. Если current reread успешно вернул изменённую note, в result входит
её current body; старый snippet не сравнивается и не используется.

### Error taxonomy

Public application boundary использует закрытую taxonomy без raw exception,
path, body, YAML, SQL, provider или network detail:

| Code | Причина |
| --- | --- |
| `SELF_RETRIEVAL_INVALID_REQUEST` | request type, text, limit или content budget нарушает bounds |
| `SELF_RETRIEVAL_SEARCH_UNAVAILABLE` | Search index/current search не дал безопасный candidate result |
| `SELF_RETRIEVAL_CURRENT_READ_UNAVAILABLE` | current reread не может доказать identity/content integrity; not-found остаётся exclusion |
| `SELF_RETRIEVAL_SELF_MODEL_UNAVAILABLE` | current validated Self Model/policy/fingerprint недоступны |
| `SELF_RETRIEVAL_RESULT_INVALID` | assembled DTO нарушает exact shape, order, totals или budget |
| `SELF_RETRIEVAL_RESULT_TOO_LARGE` | bounded result cannot be represented within fixed application cap |

Ошибки underlying Search/Self Model могут быть сохранены только как внутренний
typed cause для тестирования; наружу всегда выходит эта safe taxonomy.

## 5. Determinism and privacy

Для одинаковых current canonical records, same query, same Search implementation
и same Self Model policy result имеет одинаковые item order, search ordinals,
claim order, totals, exclusions и byte count. `generated_at` в v1 не входит в
DTO, поэтому clock не является частью determinism contract.

В памяти операции разрешены только:

- current vault read через существующие reader/application boundaries;
- disposable lexical index через existing Search implementation;
- bounded DTO construction;
- exact current UUID validation.

Запрещены:

- `second-brain-vault` changes, Safe Write, Git proposal и any write-back;
- LLM, embeddings, vector DB, RAG, network, public/authenticated provider;
- SQLite persistence, cache, watcher, queue или background worker;
- browser storage, logs с query/body/private context или raw diagnostics;
- path/front matter/secret/provider detail в public context DTO;
- client-side reranking, grouping, inference или policy injection.

Если current canonical scan имеет integrity error, который мешает доказать
полноту relevant read boundary, core возвращает safe error. Он не пытается
собрать «достаточно убедительный» subset из уже найденных hits.

## 6. Historical build sequence for #89–#91

Этот раздел сохраняет исходный dependency gate; текущий merged status указан в
начале документа.

Порядок реализации остаётся серийным и dependency-driven:

1. **#89 Self Retrieval core:** новый additive
   `application/self_retrieval.py` с DTO, validation, composition,
   current-reread flow, safe errors и focused tests. Не менять Search ports и
   не трогать vault.
2. **#90 Web Self Retrieval:** thin local read-only projection exact DTO #89.
   Web не добавляет request fields, reranking, storage, polling или inference.
3. **#91 CLI Self Retrieval:** headless text/JSON projection exact DTO #89.
   CLI не получает path/body/UUID authority и не реализует core policy.
4. После каждого slice — focused tests, затем один final Python 3.14 suite,
   deterministic exact-head CI, aggregate GitHub status `checks` и применимые
   task-specific human/external gates. Отдельный LLM/code-review verdict не
   является частью release eligibility.

Web/CLI могут быть начаты только после merged #89 и без `HUMAN_REQUIRED`.

## 7. Core test matrix

### Request and bounds

- exact request type; `bool` rejected for integer fields;
- blank/control/non-UTF-8/oversized query rejected before vault/index;
- existing 32-term Search bound and `1..50` limit preserved;
- `max_content_bytes` accepts only `1..65536` and cannot be raised;
- invalid request performs no read, index, network, provider or write call.

### Candidate/current-reread integrity

- Search order becomes 1-based audit rank without reranking or raw score;
- stale Search snippet is never returned as body;
- changed current body/metadata comes from current reread;
- deleted/missing candidate is excluded and counted, never resurrected;
- returned UUID mismatch, duplicate identity and backend/read failure produce
  one safe error rather than convincing partial context;
- `relative_path`, raw front matter, secrets and diagnostics do not enter DTO.

### Self Model seam

- exact supporting UUID links are included;
- same title/body/domain or related/contextual/contradicting-only UUID does not
  create a link;
- claim text is not merged, scored, re-ranked or relabelled;
- policy fingerprint and derivation version come only from validated result;
- empty valid Self Model is deterministic; invalid policy/result is safe error.

### Bounds, order and safety

- total content budget, overflow exclusions, `truncated`, totals and byte count
  are exact and deterministic;
- item fields are current and bounded; no silent body truncation;
- all public errors contain only stable code/message;
- no persistence, cache, network, LLM/provider or write side effect;
- existing Search, Retrieve, Timeline, Self Model and Windows regressions remain
  green; fixtures use a temporary synthetic vault only.

## 8. ACCEPT / DEFER / HUMAN_REQUIRED

| Решение | В этом contract |
| --- | --- |
| **ACCEPT** | lexical Search only; SearchHit candidate; exact UUID current reread; current body; bounded deterministic context; ordinal audit rank; exact supporting UUID Self Model link; policy fingerprint; safe errors; no persistence/write |
| **DEFER** | embeddings/vector search/RAG; lexical-gap measurement; relevance/confidence scoring; contradiction/stale/supersede lifecycle; mode-specific prompt assembly; Web/CLI; cache/DB/queue; Stage 6 prediction and Stage 7 recommendation/calibration |
| **HUMAN_REQUIRED** | любое требование выбрать embedding/provider, hidden weighting, confidence/inference semantics, canonical migration/evidence kind/self kind, public/auth boundary, new privacy boundary, persistence architecture, Assistant-vs-Simulate policy, или изменение Search/Retrieval contract |

Нет активного `HUMAN_REQUIRED`, пока #89 остаётся механической реализацией
этой границы. Если implementation обнаружит, что полезный result невозможен
без deferred/red decision, нужно записать bounded decision memo (`Вопрос /
Известные факты / Варианты / Компромиссы / Рекомендация / Затронутые задачи`)
в GitHub issue/PR, оставить #89–#91 blocked и не выбирать решение автономно.

## 9. Compatibility checklist

- `schema_version` и canonical note format не меняются;
- `second-brain-vault` не открывается как отдельный worktree и не изменяется;
- `SearchDocument`, `SearchHit`, `SearchIndexPort`, `SearchRequest` и
  `RetrievedNote` остаются backwards-compatible;
- Stage 4 `SelfModelClaim`/`SelfModelResult` policy не расширяется;
- current result не является canonical evidence, recommendation или prediction;
- core, Web и CLI остаются read-only и local-only;
- dependency gate для #90/#91 — merged #89 без `HUMAN_REQUIRED`.
