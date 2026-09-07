# Assistant v1 — contract независимой рекомендации и analysis

Статус: **DESIGN / OWNER DECISION A APPROVED**. Для capability-boundary
решения `HUMAN_REQUIRED: none`; owner выбрал отдельный provider-neutral
`AdvisorPort`. Документ закрывает design-only часть
issue [#162](https://github.com/MikeMoore1337/second-brain/issues/162) и не
создаёт production runtime, provider integration, LLM operation, API или Web.

Контрольная база для этой редакции: `main`, commit
`30249d56d366c0688c451a45a7e0214114c7c197`.

Ключевой verdict: capability-boundary decision закрыт вариантом A. Это не
выбор нового provider и не разрешение credentials, network, retention,
privacy-provider integration или runtime. Любая такая integration остаётся
отдельным future approval gate; до него Assistant и зависящий от него Compare
runtime не начинают реализацию.

## 1. Purpose и non-goals

### Purpose

`Assistant` — независимая ветка recommendation / analysis. Она отвечает на
вопрос «какое действие или вариант лучше поддержан текущей задачей, явно
переданными ограничениями, явно принятыми целями и разрешёнными фактами», но
не пытается воспроизвести выбор владельца.

В этом документе слово «объективнее» означает только прозрачное рассуждение
относительно явно объявленных входов и границ. Оно не означает universal truth,
медицинскую, юридическую или финансовую certainty и не превращает личный
контекст в скрытую функцию полезности.

### Non-goals этой задачи и Assistant v1

В #162 и в последующем runtime до отдельного разрешения запрещены:

- production application code, provider adapter, LLM call, API и Web;
- изменение текущего `LlmPort`, `LlmRequest`, `NoteDraft` или их error contract;
- выбор нового provider, paid dependency, model, SDK или network route;
- передача `Simulate Me` prediction в Assistant;
- prediction владельца, фраза «так бы выбрал пользователь» и поведенческое
  копирование;
- hidden objective score из `preference`, habits, frequency, recency или
  observed decisions;
- `behavioral_pattern`, `decision_rule`, personality, diagnosis или sensitive
  trait inference;
- vault write, Safe Write, canonical evidence creation, Self Model mutation;
- conversation/history/recommendation persistence, embeddings, RAG, vector DB,
  cache, training и automatic calibration;
- unprojected raw private context, complete/substantial verbatim Stage 5 spans,
  absolute paths, secrets, provider payloads или raw exceptions в result/logs;
- самостоятельный runtime issue, #163, Compare runtime или изменение
  `second-brain-vault`.

## 2. Architectural invariant

Три режима остаются независимыми:

```text
Assistant   = independent recommendation / analysis
Simulate Me = likely owner choice prediction
Compare     = both outputs + explicit delta
```

Следствия:

1. `Simulate Me` result никогда не является input, evidence, hint, tie-break,
   constraint или objective для Assistant.
2. Assistant принимает нормативными только explicit constraints и explicit
   goals текущего request. Personal context из Self Model не получает
   normative authority автоматически.
3. `preference` и `belief` не входят автоматически в Stage 5 factual context;
   если caller сам передал bounded contextual information, она остаётся
   contextual information. `goal` становится objective только когда он явно
   передан и принят как цель в текущем request.
4. Каждый result должен иметь machine-readable label
   `independent_recommendation_analysis` и display label
   **Independent recommendation / analysis**. В Russian UI допустим текст
   **Независимая рекомендация / анализ**, но нельзя показывать его как
   `prediction`, `ПРОГНОЗ`, canonical fact, `best` или `optimal`.
5. `second-brain-vault` остаётся единственным canonical source of truth.
   Assistant result — ephemeral derived DTO, который можно удалить и получить
   заново из тех же current inputs.

## 3. Caller-owned Request DTO

Caller передаёт только bounded задачу и явные текущие inputs. Request не
позволяет caller объявить произвольный UUID, Self Model claim, policy или
prediction authoritative.

### 3.1. Proposed exact shape

```text
AssistantOption {
  id: string
  label: string
}

AssistantExplicitContext {
  kind: "fact" | "background"
  text: string
}

AssistantRequest {
  task: string
  options: tuple[AssistantOption, ...] = ()
  explicit_constraints: tuple[string, ...] = ()
  explicit_goals: tuple[string, ...] = ()
  explicit_context: tuple[AssistantExplicitContext, ...] = ()
  stage5_context_mode: "none" | "current_explicit_facts" = "none"
  stage5_query: string | null = null
  max_context_bytes: int = 65536
  max_result_bytes: int = 65536
}
```

`options` optional: открытый вопрос может получить текстовую recommendation без
`selected_option`. Если options переданы, они остаются caller-owned; Assistant
не создаёт semantic option identity и не пишет эти labels в vault.

### 3.2. Request bounds

Все integer-поля принимают exact `int`; `bool` отвергается. Text validation
делает strict UTF-8 encode, Unicode NFC, `strip()` только по краям и запрещает
C0/C1 controls, `DEL` и Unicode category `Cf`. Case folding, transliteration,
синонимы и semantic normalization не выполняются.

| Поле | Точная граница и semantics |
| --- | --- |
| `task` | required `str`, 1..4096 UTF-8 bytes после validation; blank запрещён |
| `options` | 0..8 items; caller order сохраняется |
| `AssistantOption.id` | 1..64 ASCII bytes, pattern `[A-Za-z0-9][A-Za-z0-9._:-]{0,63}`, unique только в request |
| `AssistantOption.label` | required non-blank `str`, 1..256 UTF-8 bytes |
| `explicit_constraints` | 0..16 strings, каждый 1..512 bytes; общий budget 8192 bytes |
| `explicit_goals` | 0..8 strings, каждый 1..512 bytes; общий budget 4096 bytes |
| `explicit_context` | 0..16 entries; `text` 1..1024 bytes; общий budget 16384 bytes |
| `stage5_context_mode` | только `none` или `current_explicit_facts`; default `none` |
| `stage5_query` | `null` при `none`; required при `current_explicit_facts`; current literal Stage 5 query bounds |
| `max_context_bytes` | exact `int`, 1..65536; default 65536; больше cap запрещено |
| `max_result_bytes` | exact `int`, `MIN_MAX_RESULT_BYTES_V1`..65536; default 65536; больше cap запрещено |

`MIN_MAX_RESULT_BYTES_V1` — не arbitrary safety number. Это contract-derived
constant из exact canonical `AssistantResultEnvelopeV1` serializer: берётся
максимальный UTF-8 byte size минимального valid abstention envelope для каждого
текущего значения `AssistantAbstentionCode` по формуле и таблице в §6.3. Для
текущей схемы доказанное значение — **304 bytes**. Поэтому
`max_result_bytes < MIN_MAX_RESULT_BYTES_V1` даёт
`ASSISTANT_INVALID_REQUEST` на request-validation boundary, до base/context
serialization, Stage 5, `AdvisorPort` или provider operation; такой запрос не
может просить физически невозможный обязательный abstention.

`stage5_context_mode` и `stage5_query` образуют exact pair:

- при `stage5_context_mode="none"` `stage5_query` **MUST** быть `null`, и
  Assistant не вызывает Stage 5;
- при `stage5_context_mode="current_explicit_facts"` `stage5_query`
  **MUST** быть non-null и проходит validation существующего
  `SelfContextRequest.query`: non-blank `str`, 1..4096 UTF-8 bytes, без C0/C1,
  `DEL` и Unicode `Cf`, с текущим literal Search bound в 32 terms;
- `stage5_query` передаётся в Stage 5 как literal query без генерации из
  `task`, hidden context, Self Model или LLM; case folding, fuzzy matching,
  synonym expansion и semantic query rewriting запрещены;
- candidate limit всегда равен существующему
  `DEFAULT_SELF_CONTEXT_LIMIT` Stage 5 (`20`), а не caller-owned option или
  implementation-specific value;
- единственный budget calculator — canonical
  `AssistantReasoningEnvelopeV1`, описанный в §3.3 ниже;
- `base_context_bytes` — byte length canonical serialization того же полного
  envelope, но с `stage5_facts=[]`;
- при `stage5_context_mode="none"` `base_context_bytes` одновременно является
  final context envelope; если он больше `max_context_bytes`, возвращается
  `ASSISTANT_INVALID_REQUEST` без Stage 5 call;
- при `stage5_context_mode="current_explicit_facts"`
  `remaining_context_bytes = max_context_bytes - base_context_bytes`; если
  `remaining_context_bytes <= 0`, возвращается `ASSISTANT_INVALID_REQUEST` до
  Stage 5 call, без silent dropping explicit inputs;
- при `stage5_context_mode="current_explicit_facts"` Stage 5 получает
  `max_content_bytes = min(remaining_context_bytes,
  MAX_MAX_CONTENT_BYTES)`; он не может превысить ни оставшийся Assistant budget,
  ни текущий Stage 5 cap `MAX_MAX_CONTENT_BYTES`. Это upstream conservative cap,
  но не доказательство final Assistant JSON budget;
- до любого Stage 5/provider call проверяется также нижняя граница
  `max_result_bytes >= MIN_MAX_RESULT_BYTES_V1`; значение ниже неё всегда
  является `ASSISTANT_INVALID_REQUEST`;
- после internal projection обязательно строится exact canonical
  `AssistantReasoningEnvelopeV1` и проверяется
  `canonical_context_bytes <= max_context_bytes`;
- если complete eligible factual projection нельзя безопасно представить в
  этом envelope, возвращается `ASSISTANT_CONTEXT_UNAVAILABLE`: нельзя молча
  обрезать factual text или выбирать arbitrary subset;
- если Stage 5/search/current-reread/allowlist pipeline успешно завершён,
  integrity validation прошла, но eligible factual items ровно ноль,
  результатом **всегда** является нормальный domain outcome
  `abstention/insufficient_current_context`, без heuristic body fallback.

### 3.3. Canonical reasoning-visible envelope и budget

`AssistantReasoningEnvelopeV1` — единственный provider/reasoning-visible
envelope и единственный источник расчёта `max_context_bytes`. Его логическая
структура и порядок ключей фиксированы:

```text
{
  "task": string,
  "options": [
    {"id": string, "label": string}
  ],
  "explicit_constraints": [string],
  "explicit_goals": [string],
  "explicit_context": [
    {"kind": "fact" | "background", "text": string}
  ],
  "stage5_facts": [
    {
      "ordinal": int,
      "role": "reported_fact",
      "text": string
    }
  ]
}
```

В envelope не входят orchestration-only controls и private metadata:
`stage5_context_mode`, `stage5_query`, `max_context_bytes`,
`max_result_bytes`, internal UUID, search rank, `evidence_kind`, `self_kind`,
paths, raw front matter и unprojected raw body. `stage5_facts` содержит только
bounded exact body-derived `factual_text` из validated `AssistantStage5Fact`.
Каждый reasoning-visible item имеет ровно форму
`{ordinal: int, role: "reported_fact", text: factual_text}`; `ordinal`
присваивается только после успешной projection, является integer, начиная с 1,
в текущем deterministic search order.

Canonical encoding:

1. JSON syntax согласно RFC 8259;
2. UTF-8, без BOM и без trailing newline;
3. strings уже прошли documented NFC validation;
4. non-ASCII символы сериализуются непосредственно в UTF-8, не через
   `\uXXXX`, если escaping не требуется JSON syntax;
5. каждая JSON string кодируется только общим
   `AssistantCanonicalJsonEncoderV1`, а не serializer-specific escaping;
6. insignificant spaces и newlines отсутствуют;
7. separators ровно `,` и `:`;
8. object key order ровно такой, как в структуре выше;
9. option key order ровно `id`, затем `label`;
10. explicit-context key order ровно `kind`, затем `text`;
11. Stage 5 fact key order ровно `ordinal`, затем `role`, затем `text`;
12. arrays сохраняют caller order либо current deterministic search order;
13. `context_bytes = len(canonical_json_utf8_bytes)`.

`AssistantCanonicalJsonEncoderV1` — единственный string encoder одновременно
для `AssistantReasoningEnvelopeV1` и `AssistantResultEnvelopeV1`. Его exact
escape table:

```text
U+0022 QUOTATION MARK  -> `\"`
U+005C REVERSE SOLIDUS -> `\\`
U+0008 BACKSPACE       -> `\b`
U+0009 TAB             -> `\t`
U+000A LINE FEED       -> `\n`
U+000C FORM FEED       -> `\f`
U+000D CARRIAGE RETURN -> `\r`
```

Для каждого другого code point U+0000..U+001F используется только
lowercase-hex форма `\u00xx`: например, U+0000 -> `\u0000`, U+000B ->
`\u000b`, U+001F -> `\u001f`. `U+002F /` не escape-ится. Запрещены
`\u000A`/`\u0009` вместо short escapes, uppercase hex, escaped `/` и
ненужный `\uXXXX` для обычного Unicode. Все остальные разрешённые non-ASCII
символы идут напрямую в UTF-8. Альтернативные RFC 8259 byte sequences не
являются canonical и не используются в context или result budget calculation.

`base_context_bytes` вычисляется canonical serialization с теми же validated
`task`, `options`, `explicit_constraints`, `explicit_goals` и
`explicit_context`, но с `"stage5_facts":[]`. При `none` это final envelope.
При `current_explicit_facts` только эта величина вычитается из
`max_context_bytes`; никаких query/mode/budget полей в расчёт не добавляется.
После добавления полного Stage 5 projection Assistant повторно сериализует
тот же canonical envelope и проверяет его полный byte length. Если complete
eligible projection не помещается, это `ASSISTANT_CONTEXT_UNAVAILABLE`, а не
silent truncation или выбор части фактов. Другого serializer или budget
calculator для этого контракта нет.

При успешно завершённых Stage 5/current-reread/allowlist шагах, когда eligible
facts нет, применяется ровно domain outcome из §7.1; это не context error.

### 3.4. Authority входов

- `explicit_constraints` — ограничения текущей задачи. Сам факт передачи в
  request делает их explicit, но не исправляет противоречия между ними.
- `explicit_goals` — цели, которые caller запрашивает или явно принимает для
  текущей задачи. Только они получают objective semantics. Само наличие
  похожего goal claim в Self Model недостаточно.
- `explicit_context` — caller-provided context. `kind="fact"` означает
  reported premise, а не externally verified truth; `kind="background"` может
  объяснять ситуацию, но не выбирает вариант.
- `stage5_context_mode="none"` запрещает чтение Stage 5 для этой операции.
  `current_explicit_facts` — явное разрешение caller получить только current,
  validated factual projection из exact allowlist `explicit_user_fact +
  memory`; это не разрешение искать привычки, preference, belief, goal,
  decision, outcome или prediction.
- `stage5_query` — единственный caller-owned literal query для разрешённой
  factual projection; Assistant не подменяет его `task` и не генерирует его
  сам.

Caller не может передать:

- `SelfContextResult`, `SearchHit`, `SearchRequest`, raw `NoteRecord`, body,
  front matter, absolute/relative path или canonical UUID как authority;
- `SelfModelClaim`, `SelfModelResult`, policy, fingerprint, confidence,
  `SimulateMeResult` или historical prediction;
- provider/model/temperature/prompt/route, arbitrary score, hidden objective,
  `decision_rule` или `behavioral_pattern`;
- `apply`, write receipt, cache key, history id или persistence instruction.

## 4. Allowed Stage 5 context и evidence authority

Stage 5 остаётся existing read-only composition:

```text
Search candidate -> current UUID reread -> bounded SelfContextItem
```

`SearchHit`, snippet, ordinal и прошлый cache — только retrieval candidates. В
Assistant input допускается исключительно validated current projection. Если
для `current_explicit_facts` нельзя доказать current UUID identity и exact
context role, operation не заменяет его stale snippet или raw search result.

Для `stage5_context_mode="current_explicit_facts"` future Assistant composition
обязана использовать fact-only Stage 5 source: bounded Search с literal
`stage5_query` и `DEFAULT_SELF_CONTEXT_LIMIT`, затем current UUID reread и
classification только для этого candidate window. Она **не вызывает** общий
`BuildSelfContext` как opaque composition, если тот перед этим безусловно
вызывает `_read_self_model()`/`BuildSelfModel` и сканирует preference, belief или
goal claims. Self Model derivation и такие claims не должны быть прочитаны или
обработаны до Assistant allowlist. Это design constraint будущего core, а не
изменение существующего Stage 5 source в #162. Если fact-only source нельзя
получить или нельзя доказать отсутствие такого Self Model read, возвращается
`ASSISTANT_CONTEXT_UNAVAILABLE`; post-filtering уже прочитанного запрещённого
материала не является эквивалентом fact-only path.

Fact-only source также обязан построить metadata-filtered candidate corpus
**до** body indexing и Search query: в него попадают только current managed
Personal Memory entries, для которых validated metadata допускает
`evidence_kind="explicit_user_fact"` и `self_kind="memory"`. Preference,
belief, goal, `user_statement`, historical и прочие forbidden entries должны
быть исключены до индексации и query; их body нельзя сначала индексировать, а
потом отфильтровать на Assistant boundary. Metadata filter не заменяет current
UUID reread и Personal Memory validation. Если существующий Search boundary не
умеет доказуемо использовать такой filtered corpus, `current_explicit_facts`
возвращает `ASSISTANT_CONTEXT_UNAVAILABLE` до factual projection; heuristic
classification по body запрещена.

### 4.1. Future application-internal typed factual projection

Текущий public `SelfContextItem` намеренно не расширяется в #162. Его полей
недостаточно, чтобы отличить `explicit_user_fact` от `user_statement`, поэтому
Assistant core не имеет права классифицировать item по body, title или
`SelfModelDimension` постфактум.

До передачи factual context future application composition строит internal-only
projection над той же approved authority `Search -> current UUID reread`:

```text
AssistantStage5Fact {
  canonical_note_id: UUIDv7       # internal validation only; never public
  evidence_kind: "explicit_user_fact"
  self_kind: "memory"
  factual_text: string            # exact current RetrievedNote.body projection; §4.1.1, 1..1024 UTF-8 bytes
  evidence_at: aware datetime | "unknown"
  stage5_search_rank: int         # deterministic current candidate order
}
```

`AssistantStage5Fact` не является public Stage 5 DTO, не меняет
`SelfContextResult` и не хранится. Он создаётся только после:

1. deterministic Stage 5 request из `stage5_query`,
   `DEFAULT_SELF_CONTEXT_LIMIT` и remaining budget;
2. fact-only Search/candidate composition без `BuildSelfModel` или
   `_read_self_model()` и без чтения Self Model claims;
3. успешного current canonical UUID reread;
4. exact Personal Memory validation с сохранением validated
   `evidence_kind == explicit_user_fact` и `self_kind == memory`;
5. построения полного bounded `factual_text` по exact projection из §4.1.1.
   Нельзя молча обрезать body так, чтобы partial text выглядел complete. Если
   complete eligible projection не может быть построен или помещён в
   upstream/final budget, это `ASSISTANT_CONTEXT_UNAVAILABLE`, а не исключение
   item или выбор arbitrary subset.

### 4.1.1. Exact `factual_text` projection

После последовательности `Search candidate -> current UUID reread -> managed-
note validation -> Personal Memory validation -> exact allowlist`
`AssistantStage5Fact.factual_text` строится **только** из exact current
`RetrievedNote.body`.

В него не входят `title`, `tags`, `note_type`, relative/absolute path, front
matter, UUID, search snippet, `Self Model` claim, source URL, metadata или
`stage5_search_rank`. Internal projection может хранить UUID, rank и validated
Personal Memory metadata только для integrity/provenance; reasoning-visible
`stage5_facts[].text` и private-echo guard используют только этот body-derived
text.

Трансформация строго последовательна:

1. CRLF нормализуется в LF;
2. оставшийся CR нормализуется в LF;
3. применяется Unicode NFC;
4. обрезается leading/trailing Unicode whitespace;
5. внутренний текст не изменяется: нет whitespace collapsing, Markdown
   stripping, HTML conversion, heading extraction, title prepend, tag append,
   punctuation rewrite, case normalization, LLM summarization или paraphrase;
6. LF и TAB допустимы как internal whitespace; другие C0/C1 controls, DEL и
   Unicode `Cf` дают `ASSISTANT_CONTEXT_UNAVAILABLE`;
7. после transformation text обязан быть non-empty и иметь 1..1024 UTF-8 bytes;
8. пустой после trim qualifying item считается non-usable/non-eligible; если
   после успешного pipeline usable eligible facts ровно ноль, применяется
   `abstention/insufficient_current_context`;
9. qualifying fact с body-derived text больше 1024 bytes или forbidden controls
   нельзя безопасно представить полностью: возвращается
   `ASSISTANT_CONTEXT_UNAVAILABLE`;
10. `factual_text` никогда не truncate-ится, не заменяется title/tags/snippet и
    не подменяется другим arbitrary fact. Нельзя скрыть unrepresentable
    qualifying fact выбором partial projection.

`canonical_note_id`, `evidence_at` и `stage5_search_rank` нужны только для
internal integrity/provenance validation. Public Assistant result возвращает
только ephemeral `stage5_current_context` ordinal и не раскрывает UUID, path,
front matter или raw body.

### 4.1.2. Stage 5 completeness gate

`BuildSelfContext` возвращает bounded `SelfContextResult`, в котором
`truncated` и `exclusions[].reason` являются частью integrity boundary. После
успешной валидации формы Stage 5 result, но **до** factual allowlist и до
решения «eligible facts ровно ноль», application composition обязана выполнить
этот gate:

- если `SelfContextResult.truncated == true`, вернуть
  `ASSISTANT_CONTEXT_UNAVAILABLE`;
- если хотя бы одна exclusion имеет
  `reason == "context_budget_exceeded"`, вернуть
  `ASSISTANT_CONTEXT_UNAVAILABLE`.

Это completeness/integrity failure, а не
`abstention/insufficient_current_context`: после budget overflow оставшиеся
Search candidates могли не пройти current UUID reread и classification, так что
по одному `included` нельзя доказать отсутствие eligible fact. Нельзя считать
только included items complete, игнорировать budget-excluded candidates,
выводить их body/metadata эвристически, rerank-ить, читать arbitrary
substitutes или передавать partial projection.

Exact factual allowlist применяется только если Stage 5 result valid,
`truncated == false`, нет exclusion с `context_budget_exceeded`, и current
reread/classification для всего возвращённого bounded candidate window
завершены (либо candidate получил только existing valid
`candidate_not_found` exclusion). Окно ограничено approved
`DEFAULT_SELF_CONTEXT_LIMIT` (`20`), а не
обещанием exhaustive vault-wide search. Existing `candidate_not_found`
semantics сохраняются: такая exclusion сама по себе не делает result
truncated и не расширяет scope этой задачи. Только после этого полного gate
zero usable eligible facts даёт нормальный abstention
`insufficient_current_context`; отсутствие heuristic fallback обязательно.

### 4.2. Classification

| Источник | Роль в Assistant v1 | Ограничение |
| --- | --- | --- |
| request `explicit_constraints` | normative constraint | только caller-owned текущий request |
| request `explicit_goals` | objective | goal relevant to this task только потому, что он явно принят/requested |
| request `explicit_context(kind=fact)` | contextual reported fact | может быть premise, не external truth и не hidden score |
| request `explicit_context(kind=background)` | contextual background | не может сам выбрать option |
| current Stage 5 enrolled `explicit_user_fact` + `self_kind=memory` | **only automatic factual context** | exact allowlist; current reread + Personal Memory validation + bounded `AssistantStage5Fact` обязательны |
| current Stage 5 `preference`, `belief` или `goal` | forbidden in `current_explicit_facts` | не передаются даже как factual context; future use требует отдельного named capability/mode |
| current Stage 5 `user_statement` | forbidden in `current_explicit_facts` | не заменяет `explicit_user_fact`, даже если body похож |
| reviewed `observed_decision` / `outcome_later_observation` | forbidden in `current_explicit_facts` | не извлекаются автоматически; caller может передать только свой bounded contextual input |
| ordinary `SelfContextItem` без exact typed role | forbidden in `current_explicit_facts` | нет heuristic classification или hidden background retrieval |
| `decision_rule`, `behavioral_pattern`, inferred habit/frequency/recency | forbidden | эти dimensions unavailable и не реконструируются |
| `Simulate Me` prediction | forbidden | никогда не input для Assistant |
| valid Stage 5 `candidate_not_found` exclusion | normal existing Stage 5 exclusion | сама по себе не `truncated` и не `ASSISTANT_CONTEXT_UNAVAILABLE`; после полного gate может вести к `insufficient_current_context` |
| missing/mismatched UUID in a purported `AssistantStage5Fact`, identity conflict, stale snippet/path/cache или raw diagnostics used as authority | forbidden | `ASSISTANT_CONTEXT_UNAVAILABLE`, не `insufficient_current_context` и не fallback |

`current_explicit_facts` не превращает общий Stage 5 result в prompt и не делает
post-filter уже прочитанных Self Model claims. Будущая composition обязана
получить candidates через fact-only source, проверить ровно
`explicit_user_fact + memory`, создать `AssistantStage5Fact` и передать только
его с role `reported_fact`. Если такой fact-only typed projection отсутствует
или pipeline не может доказать/построить полный projection, возвращается
`ASSISTANT_CONTEXT_UNAVAILABLE`. Если pipeline
успешен, integrity validation прошла, но после exact allowlist не осталось
facts, возвращается ровно `abstention/insufficient_current_context`; это
разные outcomes и они не взаимозаменяемы.

### 4.3. Как Self Model не становится authority

Self Model предоставляет explainability/evidence, а не policy выбора:

- `preference` — информация о предпочтении владельца; она не определяет
  «лучший» вариант автоматически;
- `belief` — evidence того, что владелец считает/утверждает; это не проверенная
  истина;
- `goal` — в текущем Stage 5 factual mode не передаётся; допустимый objective
  появляется только после explicit promotion в текущем request; stored claim
  сам себя не активирует;
- `memory` и обычные notes — контекст, не objective;
- `decision`/`outcome` — отдельные reviewed historical facts, не decision rule;
- `behavioral_pattern` и `decision_rule` остаются unavailable;
- `SelfModelConfidence` в current v1 — `not_assessed`, `score=None`; никакого
  веса или numeric confidence в Assistant нет.

Supporting UUID refs, derivation version и policy fingerprint из Stage 5/4
нужны для внутренней provenance validation. Они не являются score, не меняют
ranking и не должны попадать в public Assistant result как raw private context.

## 5. Recommendation semantics

### 5.1. Что означает recommendation

Recommendation — bounded conclusion независимого анализа по:

1. literal `task`;
2. caller-owned options, если они есть;
3. explicit current constraints;
4. explicit current goals;
5. разрешённым factual/contextual inputs.

Она не означает «владелец выберет это», «это канонический факт» или
«это доказанно оптимально». Result обязан сохранить label independent
recommendation / analysis даже если выбран один option.

Если options отсутствуют, `recommendation` может быть bounded action proposal.
Если options присутствуют, `selected_option` может быть только одной exact
caller-owned парой `{id, label}`. Assistant не добавляет option, не меняет
label и не делает hidden score table. При непреодолимой trade-off ситуации
допустим `analysis` без selected option или abstention.

### 5.2. Deterministic application boundary

Будущая application orchestration должна выполнять следующие явные шаги:

1. Validate exact `AssistantRequest`; invalid request не вызывает Stage 5,
   provider, network, log с context или write path.
2. Сформировать единственный canonical `AssistantReasoningEnvelopeV1` из
   `TASK`, `OPTIONS`, `EXPLICIT CONSTRAINTS`, `EXPLICIT OBJECTIVES`, `EXPLICIT
   CONTEXT` и, только при `stage5_context_mode="current_explicit_facts"`,
   `STAGE5 CURRENT FACTS` from `AssistantStage5Fact`; его exact JSON bytes —
   единственный input для context budget и reasoning boundary.
3. После успешной валидации формы Stage 5 result выполнить completeness gate:
   `truncated` обязан быть `false`, и ни одна exclusion не может иметь
   `reason="context_budget_exceeded"`; иначе вернуть
   `ASSISTANT_CONTEXT_UNAVAILABLE` до factual allowlist и zero-context
   decision. Этот bounded check относится к окну `DEFAULT_SELF_CONTEXT_LIMIT`,
   а не к exhaustive vault search.
4. Отдельно проверить current Stage 5 identity/role для полного возвращённого
   candidate window. Нельзя заменить current reread индексом, snippet,
   прошлым result или claim text.
5. Исключить все `Simulate Me` values и недоступные Self Model dimensions.
6. Выполнить только approved option A — отдельную typed provider-neutral
   `AdvisorPort` / equivalent boundary. Deterministic C-вариант не входит в
   текущий Assistant v1 execution path; если owner когда-либо выберет его
   отдельно, это потребует новой named capability, отдельного решения и
   отдельного контракта. Результат проходит exact Result DTO validation.
7. При отсутствии safe basis, конфликте hard constraints, high-stakes
   certainty или ambiguous options вернуть abstention, а не убедительный
   guess.

Вспомогательный ordering допускается только для стабильной сериализации
`evidence_refs` и input refs. Он не является relevance, preference или
confidence ranking.

### 5.3. Обязательный abstain

Assistant обязан abstain, если:

- task не даёт понятного bounded вопроса или action scope;
- explicit constraints противоречат друг другу и caller не указал способ
  разрешения конфликта;
- options невозможно безопасно сопоставить с вопросом или они genuinely
  incomparable без дополнительной цели;
- результат потребовал бы hidden preference, behavior inference, prediction,
  invented fact или claim о universal optimality;
- Stage 5 result имеет `truncated=true` либо exclusion с
  `reason="context_budget_exceeded"` — вернуть
  `ASSISTANT_CONTEXT_UNAVAILABLE` до factual allowlist, а не считать included
  facts полным контекстом;
- при успешно завершённых Stage 5/current-reread/allowlist шагах не осталось ни
  одного eligible factual item — вернуть ровно
  `abstention/insufficient_current_context`;
- запрос просит medical/legal/financial certainty, diagnosis или иной вывод,
  который нельзя дать в bounded ordinary-assistant safety boundary.

Failure получить или доказать requested current context (Search/Self Retrieval,
current UUID reread, integrity/classification, Stage 5 completeness или complete
bounded projection) не является abstention: он возвращается как
`ASSISTANT_CONTEXT_UNAVAILABLE`. В частности, `truncated=true` либо
`exclusions[].reason="context_budget_exceeded"` — это context error до
allowlist/zero-context decision, а zero search candidates после успешного
полного bounded pipeline — это `insufficient_current_context`, а не error.

Uncertainty/caveats описывают известные ограничения, но не заменяют
abstention. Numeric confidence, probability и automatic calibration запрещены.

## 6. Exact proposed Result DTO

```text
AssistantInputRef {
  source: "explicit_constraint" | "explicit_goal"
  ordinal: int              # 1-based ordinal inside the request field
}

AssistantEvidenceRef {
  source: "stage5_current_context" | "explicit_context"
  ordinal: int              # request-local/current-result-local only
  role: "reported_fact" | "background"
}

AssistantResultEnvelopeV1 {
  output_label: "independent_recommendation_analysis"
  kind: "recommendation" | "analysis" | "abstention"
  recommendation: string | null
  selected_option: AssistantOption | null
  rationale: tuple[string, ...]
  evidence_refs: tuple[AssistantEvidenceRef, ...]
  constraints_used: tuple[AssistantInputRef, ...]
  objectives_used: tuple[AssistantInputRef, ...]
  uncertainty: tuple[string, ...]
  abstention_code: AssistantAbstentionCode | null
  contract_version: "assistant-v1"
}
```

### 6.1. Result bounds

| Поле | Граница |
| --- | --- |
| `output_label` | exact fixed value; display text is Independent recommendation / analysis |
| `kind` | closed enum из трёх значений |
| `recommendation` | 1..2048 UTF-8 bytes только для `kind=recommendation`; иначе `null` |
| `selected_option` | `null` при no-options; иначе exact option из request или `null` |
| `rationale` | 1..8 items, каждый 1..1024 bytes; bounded total |
| `evidence_refs` | 0..32 unique source/ordinal refs; exact source/role binding по §6.2; no body, UUID, path or claim text |
| `constraints_used` | 0..16 refs; only valid explicit constraint ordinals |
| `objectives_used` | 0..8 refs; only valid explicit goal ordinals |
| `uncertainty` | 0..8 items, каждый 1..512 bytes; no numeric confidence |
| `abstention_code` | required only for `kind=abstention`; otherwise `null` |
| complete result | canonical `AssistantResultEnvelopeV1` must fit fixed `max_result_bytes`; no silent truncation |

`AssistantEvidenceRef.ordinal` — ephemeral ordinal into the current labelled
context, не canonical identity. Public result намеренно не содержит `note_id`,
`relative_path`, unprojected title/body, raw `SelfModelClaim.claim`, YAML, query
или provider metadata. A future UI can show a separately authorized current
context view; this DTO не становится history или access token. Body-derived
output text остаётся subject to `AssistantPrivateEchoGuardV1`.

Exact invariants:

- `kind=recommendation` требует non-null `recommendation`, valid rationale и
  `abstention_code=null`; `selected_option` обязателен только когда выбран
  discrete option, но может быть `null` при открытом action proposal.
- `kind=analysis` требует `recommendation=null`, `selected_option=null`,
  rationale и может описывать trade-offs без выбора.
- `kind=abstention` требует `recommendation=null`, `selected_option=null` и
  ровно один `abstention_code`.
- `selected_option`, если есть, байт-в-байт соответствует одной caller-owned
  option pair; server не создаёт ID.
- `constraints_used` и `objectives_used` не могут ссылаться на Stage 5,
  Self Model или Simulate Me; contextual evidence не повышается в objective
  через result serialization.
- `evidence_refs` проходят application-owned source/role validation по §6.2 до
  того, как result считается valid; provider/reasoner не может изменить эту
  связь.
- DTO не имеет полей `prediction`, `confidence`, `score`, `probability`,
  `best`, `optimal`, `canonical`, `provider`, `model` или `write_receipt`.

### 6.2. Exact evidence source/role binding

В Assistant v1 `AssistantEvidenceRef.role` — closed enum только из
`reported_fact` и `background`, но допустимость определяется не одной role, а
парой `source` + referenced item:

| `source` | Что именно referenced | Обязательная `role` | Любая другая role |
| --- | --- | --- | --- |
| `explicit_context` | request entry с `kind="fact"` | `reported_fact` | `ASSISTANT_RESULT_INVALID` |
| `explicit_context` | request entry с `kind="background"` | `background` | `ASSISTANT_RESULT_INVALID` |
| `stage5_current_context` | current typed `AssistantStage5Fact` | `reported_fact` | `ASSISTANT_RESULT_INVALID` |

`ordinal` обязан ссылаться на существующий request-local либо current-result-local
item соответствующего source. Provider/AdvisorPort не может повысить
`background` до `reported_fact`, понизить typed Stage 5 fact в `background` или
переименовать его в personal/history role. Значения
`contextual_preference`, `contextual_belief` и `historical_context` не являются
valid v1 enum values; их возможное будущее использование — отдельная named
capability и отдельный `DEFER`.

### 6.3. Canonical result serialization и validation order

`AssistantResultEnvelopeV1` — единственная canonical serialization для
`max_result_bytes`. Это exact provider-result semantic DTO; все поля всегда
присутствуют, а nullable fields сериализуются как JSON `null`, не опускаются:

```text
{
  "output_label": string,
  "kind": string,
  "recommendation": string | null,
  "selected_option": null | {
    "id": string,
    "label": string
  },
  "rationale": [string],
  "evidence_refs": [
    {
      "source": string,
      "ordinal": int,
      "role": string
    }
  ],
  "constraints_used": [
    {
      "source": "explicit_constraint",
      "ordinal": int
    }
  ],
  "objectives_used": [
    {
      "source": "explicit_goal",
      "ordinal": int
    }
  ],
  "uncertainty": [string],
  "abstention_code": string | null,
  "contract_version": "assistant-v1"
}
```

Root key order ровно такой, как в структуре выше. Nested key order также
фиксирован: `selected_option` — `id`, затем `label`; `evidence_ref` —
`source`, `ordinal`, `role`; `AssistantInputRef` — `source`, `ordinal`.
Arrays сохраняют validated DTO tuple order; canonical serializer не сортирует,
не удаляет и не переставляет items. Результат принимается при
`result_bytes <= request.max_result_bytes`; только строгое превышение даёт
`ASSISTANT_RESULT_TOO_LARGE`. `result_bytes` определяется ровно как:

```text
result_bytes = len(canonical_AssistantResultEnvelopeV1_utf8)
```

`MIN_MAX_RESULT_BYTES_V1` вычисляется из этой же exact serialization, а не
задаётся вручную:

```text
MIN_MAX_RESULT_BYTES_V1 = max(
  len(canonical_AssistantResultEnvelopeV1_utf8(minimal_abstention(code)))
  for code in AssistantAbstentionCode
) = 304
```

`minimal_abstention(code)` — это ровно следующий envelope с единственной
заменяемой строкой `abstention_code`; `rationale=["x"]` — shortest valid
rationale, все поля и их `null`/empty values обязательны:

```json
{"output_label":"independent_recommendation_analysis","kind":"abstention","recommendation":null,"selected_option":null,"rationale":["x"],"evidence_refs":[],"constraints_used":[],"objectives_used":[],"uncertainty":[],"abstention_code":"<current-code>","contract_version":"assistant-v1"}
```

| `AssistantAbstentionCode` | Canonical UTF-8 bytes |
| --- | ---: |
| `insufficient_basis` | 289 |
| `conflicting_explicit_constraints` | 303 |
| `ambiguous_or_incomparable_options` | **304** |
| `unsafe_high_stakes` | 289 |
| `unsupported_task` | 287 |
| `insufficient_current_context` | 299 |

Таким образом, текущий exact contract-derived lower bound равен **304 bytes**;
наиболее длинный обязательный abstention — `ambiguous_or_incomparable_options`.
При изменении schema, key order, required fields, rationale minimum или closed
enum это значение MUST быть пересчитано и задокументировано до изменения
request bound. `AssistantCanonicalJsonEncoderV1` из §3.3 — общий encoder для
обоих envelope; `ensure_ascii=True`, pretty JSON, incidental dict ordering,
alternate escaping и alternate result serializer не допускаются. Размер raw
transport response provider, если он когда-либо будет ограничен, является
отдельным future adapter cap и не заменяет этот semantic DTO budget.

Result validation выполняется строго в таком порядке:

1. Validate result object/type, closed enums и per-field bounds.
2. Validate `selected_option` и request-local input references.
3. Validate exact evidence `source`/`role` bindings по §6.2.
4. Run `AssistantPrivateEchoGuardV1` для всех validated
   `AssistantStage5Fact.factual_text` и provider-generated output fields,
   включая per-field и aggregate checks из §8.1.
5. Canonically serialize exact `AssistantResultEnvelopeV1`.
6. Compare `result_bytes` с `request.max_result_bytes`.
7. Если `result_bytes > max_result_bytes`, вернуть
   `ASSISTANT_RESULT_TOO_LARGE`.

Guard violation или любая другая invalid result semantics отклоняет весь
result с `ASSISTANT_RESULT_INVALID`; offending text не возвращается и не
логируется. Ни при guard violation, ни при oversized canonical result нельзя
делать truncation, dropping rationale/uncertainty/evidence refs, alternate
pretty/escaped serialization, retry или provider fallback.

## 7. Abstention и safe error taxonomy

### 7.1. Typed abstention codes

```text
AssistantAbstentionCode:
  insufficient_basis
  conflicting_explicit_constraints
  ambiguous_or_incomparable_options
  unsafe_high_stakes
  unsupported_task
  insufficient_current_context
```

Если `stage5_context_mode="current_explicit_facts"` и Stage 5/search/current
reread/allowlist pipeline успешно завершён, integrity validation прошла, но
eligible factual items ровно ноль (в том числе при zero search candidates),
результат всегда является нормальным domain outcome:

Здесь «успешно завершён» означает также, что пройден §4.1.2 completeness
gate: `truncated == false` и нет exclusion с
`reason="context_budget_exceeded"`. Иначе применяется
`ASSISTANT_CONTEXT_UNAVAILABLE`, даже если `items` пуст или среди included
items уже есть eligible fact.

```text
AssistantResultEnvelopeV1 {
  kind = "abstention"
  recommendation = null
  selected_option = null
  abstention_code = "insufficient_current_context"
}
```

Это не application error. Остальные поля результата обязаны соответствовать
общему Result DTO; raw context и fabricated recommendation не добавляются.

Это результат, а не exception: он не содержит raw error, path, provider,
private body или fabricated recommendation.

### 7.2. Typed application errors

```text
AssistantErrorCode:
  ASSISTANT_INVALID_REQUEST
  ASSISTANT_CONTEXT_UNAVAILABLE
  ASSISTANT_CANCELLED
  ASSISTANT_TIMEOUT
  ASSISTANT_PROVIDER_UNAVAILABLE
  ASSISTANT_PROVIDER_FAILURE
  ASSISTANT_MALFORMED_RESULT
  ASSISTANT_RESULT_TOO_LARGE
  ASSISTANT_RESULT_INVALID
```

Публичная error projection содержит только `{code, message}` с фиксированными
сообщениями:

| Code | Safe meaning |
| --- | --- |
| `ASSISTANT_INVALID_REQUEST` | request или base context envelope не прошли exact type/text/bounds/fit validation, включая `max_result_bytes < MIN_MAX_RESULT_BYTES_V1` |
| `ASSISTANT_CONTEXT_UNAVAILABLE` | requested current context нельзя получить, доказать или полностью представить, включая Stage 5 `truncated` или `context_budget_exceeded`; zero eligible facts после полного успешного pipeline сюда не относится |
| `ASSISTANT_CANCELLED` | operation отменена до безопасного завершения |
| `ASSISTANT_TIMEOUT` | bounded approved reasoning operation превысила deadline |
| `ASSISTANT_PROVIDER_UNAVAILABLE` | approved reasoning boundary недоступна |
| `ASSISTANT_PROVIDER_FAILURE` | approved reasoning boundary вернула failure |
| `ASSISTANT_MALFORMED_RESULT` | provider/deterministic operation не дал exact Assistant DTO |
| `ASSISTANT_RESULT_TOO_LARGE` | canonical `AssistantResultEnvelopeV1` превышает `max_result_bytes` |
| `ASSISTANT_RESULT_INVALID` | assembled result нарушает exact invariants, source/role binding, per-field или aggregate PrivateEchoGuard |

Provider errors описаны только как будущая public mapping для отдельно
одобренного reasoning boundary; production runtime #162 не реализуется. Если
будущая implementation после отдельного provider approval добавляет mapping,
raw provider exception всегда скрывается за этой taxonomy.
Не допускаются retry, fallback, automatic model replacement, raw upstream text
или network detail в public message.

## 8. Bounds, privacy и no-write boundary

Разрешённый in-memory state ограничен validated request, current read-only
Stage 5 projection, explicit reasoning result и disposable validation metadata.

Запрещено:

- открывать или изменять `second-brain-vault` через этот design slice;
- вызывать Safe Write, создавать canonical evidence или менять Self Model;
- сохранять task, prompt, raw context, recommendation, prediction, result или
  history в файл, DB, browser storage, cache, queue или telemetry;
- логировать raw task/body/claim, absolute path, UUID mapping, secret или
  provider response;
- выполнять hidden network/provider call, embeddings, RAG, vector search,
  training или background operation;
- возвращать unprojected raw private context, full canonical factual text или
  substantial verbatim span из защищённого Stage 5 projection; bounded
  paraphrase, вывод по факту и короткое atomic value могут быть допустимы по
  правилам `AssistantPrivateEchoGuardV1` ниже;

Если provider boundary когда-либо будет approved, personal context можно
передавать только по explicit request mode и после отдельной privacy review.
Текущий документ не даёт credentials, endpoint, model, retention policy или
разрешение на сеть.

### 8.1. `AssistantPrivateEchoGuardV1`

`AssistantPrivateEchoGuardV1` — application-owned deterministic post-provider
validator. Он защищает **только** каждый validated
`AssistantStage5Fact.factual_text`. `task`, `options`,
`explicit_constraints`, `explicit_goals` и `explicit_context` — caller-owned
inputs текущей operation и не являются canonical-vault protected text для этого
guard; отдельные no-log/no-persistence правила для них всё равно действуют.
Protected value — ровно тот же body-derived `factual_text` после §4.1.1, который
попадает в `stage5_facts[].text` и участвует в `AssistantReasoningEnvelopeV1`;
title, tags, metadata и unprojected raw body guard не получает.

Проверяются только provider-generated text fields:

- `recommendation`, если он non-null;
- каждый item `rationale`;
- каждый item `uncertainty`.

Guard не проверяет fixed labels/enums, `selected_option.id`,
`selected_option.label` (это exact caller-owned option), `evidence_refs` или
input refs.

Единственная canonical comparison form — `echo_compare(text)`:

1. Unicode NFC;
2. CRLF и CR нормализуются в LF;
3. каждая последовательность Unicode whitespace (Unicode `White_Space`
   code points: U+0009..U+000D, U+0020, U+0085, U+00A0, U+1680,
   U+2000..U+200A, U+2028, U+2029, U+202F, U+205F, U+3000) заменяется на один
   ASCII SPACE U+0020;
4. leading/trailing ASCII SPACE обрезаются;
5. casefold, lower, transliteration, stemming, punctuation stripping и
   semantic/fuzzy matching **не выполняются**.

Для каждой пары `protected factual_text` и проверяемого output field
вычисляется `echo_compare`. Guard fail closed, если выполняется хотя бы одно:

- output после `echo_compare` целиком равен protected fact после
  `echo_compare`, независимо от длины;
- существует exact contiguous common substring в двух normalized strings,
  для которого `common_substring_utf8_bytes = len(substring.encode("utf-8"))`
  после `echo_compare` не меньше 32 bytes. Substring сравнивается буквально
  как последовательность Unicode code points; никаких semantic similarity и
  fuzzy matching нет.

Per-field checks остаются обязательными, но их недостаточно. Для каждого
protected fact дополнительно строится один aggregate только из generated text
в exact DTO order:

```text
generated_parts =
  [recommendation if recommendation is not null]
  + rationale
  + uncertainty

generated_aggregate_raw = concatenate(generated_parts, separator="")
generated_aggregate = echo_compare(generated_aggregate_raw)
protected = echo_compare(AssistantStage5Fact.factual_text)
```

Между частями **не вставляется separator**: ни пробел, ни newline, ни другой
маркер. Для каждой пары `protected` и `generated_aggregate` применяются те же
два fail-closed правила: полное равенство независимо от длины либо exact
contiguous common substring длиной не менее 32 UTF-8 bytes после
`echo_compare`. Поэтому private fact, разбитый между `recommendation`,
`rationale` и/или `uncertainty`, не может обойти порог отдельных полей.

Empty-separator `generated_aggregate` выше остаётся обязательной primary
проверкой и не заменяется. Дополнительно для boundary case, когда внешний
рендер результата сам показывает whitespace между двумя полями, строится
отдельная validation-only view:

```text
generated_boundary_raw = concatenate(generated_parts, separator=" ")
generated_boundary = echo_compare(generated_boundary_raw)
```

Один ASCII SPACE в этой второй view моделирует только field-boundary delimiter;
он не вставляется в `generated_aggregate_raw`, canonical result или
reasoning-visible envelope. К `generated_boundary` применяются те же full-
equality и exact contiguous common-substring `>=32` bytes rules. Поэтому fact,
разделённый на два 31-byte spans с boundary whitespace, также отклоняется, но
primary aggregate сохраняет требуемую exact empty-separator semantics. Эта
view создаётся только на время проверки одного bounded result candidate и не
создаёт unbounded state.

Primary aggregate и boundary view используют actual DTO order. Дополнительно
guard выполняет bounded order-invariant reconstruction check, чтобы privacy
гарантия не зависела от provider-controlled перестановки generated fields:
для каждого protected fact он ищет, существует ли contiguous sequence из
одного или нескольких distinct `generated_parts` в **любом** порядке, чья
empty-separator либо one-ASCII-space concatenation после `echo_compare` равна
protected или содержит exact contiguous common substring длиной не менее 32
UTF-8 bytes. Это supplemental validation-only check: он не меняет normative
DTO order и не сортирует/переставляет result fields.

Matcher не перебирает unbounded text или состояние: schema ограничивает число
generated parts максимумом 17 (`recommendation` плюс 8 `rationale` и 8
`uncertainty`), protected `factual_text` — 1024 UTF-8 bytes, а candidate result
— текущим `max_result_bytes`. Реализация может использовать bounded
bitmask/backtracking и early exit, не сохраняя permutations или matched text
между проверками; при любом match применяется тот же whole-result
`ASSISTANT_RESULT_INVALID`.

`selected_option.id`, `selected_option.label`, `evidence_refs` и input refs не
входят в `generated_parts`: option text принадлежит caller, а refs не являются
generated prose. Aggregate — ephemeral derived view одного уже bounded
Assistant result candidate, ограниченный текущим `max_result_bytes` и
validated result-field bounds; он не накапливается между полями, запросами или
операциями и не хранится отдельно. Никакого unbounded buffer/state для guard
нет.

При violation весь Assistant result отклоняется с единственным safe error
`ASSISTANT_RESULT_INVALID`. Matched source text и offending output не входят в
error message и не логируются. Automatic redaction, retry, provider fallback и
partial result запрещены, потому что redaction может изменить смысл
recommendation.

Guard предотвращает complete/substantial verbatim echo canonical Stage 5
`factual_text`, но не является semantic DLP: он не гарантирует, что
model-generated paraphrase не раскроет смысл разрешённого факта. Использование
external provider с private context требует отдельного future
privacy/provider approval gate.

## 9. Provider/port analysis и owner decision record

### 9.1. Фактический current `LlmPort`

По current `main`:

```text
LlmRequest {
  instruction: str
  context: str = ""
  max_output_bytes: int = 65536
}

NoteDraft {
  title: str
  note_type: NoteType
  content: str
  tags: tuple[str, ...] = ()
  links: tuple[str, ...] = ()
}

LlmPort.draft_note(
  request: LlmRequest,
  *,
  cancellation: CancellationToken,
) -> NoteDraft
```

`LlmGateway` валидирует именно semantic `NoteDraft`, не даёт path, identity,
timestamp, approval или write receipt и маппит existing bounded
`LlmErrorCode`. Фактическая typed boundary находится в
[`ports.py`](../../src/second_brain/application/ports.py), а application
composition — в [`llm.py`](../../src/second_brain/application/llm.py). Этот port
не имеет operation для recommendation, rationale, evidence refs, abstention или
independent-advice policy.

Поэтому передача Assistant request через `draft_note` и разбор текста
`NoteDraft.content` были бы typed contract violation: исчезли бы result
semantics, safe abstention и граница между note drafting и reasoning. Молчаливо
добавлять поля в `NoteDraft` или менять meaning `context` запрещено.

### 9.2. Owner decision A — resolved

**Owner decision: A — ACCEPT.** Для будущего Assistant core выбран новый
provider-neutral `AdvisorPort` / equivalent с отдельными typed
`AdvisorRequest` и `AssistantResultEnvelopeV1` semantics.

`HUMAN_REQUIRED: none for the Assistant v1 capability-boundary decision`.
Решение разрешает только application-owned boundary и не разрешает provider,
credentials, network, model, retention, privacy-provider integration или
runtime. Любой такой следующий шаг остаётся отдельным explicit approval gate.

**Известные факты:** текущий `LlmPort` typed только для одной `draft_note ->
NoteDraft` operation; Assistant Result имеет другую семантику и требует
independent recommendation, evidence refs, abstention и privacy labeling.
Stage 5/4 дают read-only context, но не reasoning provider.

Будущая отдельная `AdvisorPort` boundary принимает только validated
`AssistantReasoningEnvelopeV1` и возвращает exact `AssistantResultEnvelopeV1`. Application
validator владеет canonical serialization, source/role binding,
`AssistantPrivateEchoGuardV1`, result-size check и abstention/error mapping до и
после provider call; provider/reasoner не может менять evidence roles, добавлять
personal metadata, bypass-ить echo guard или превращать
`ASSISTANT_CONTEXT_UNAVAILABLE` в `insufficient_current_context` либо обратно.

| Вариант | Плюсы | Минусы / complexity / coupling | Privacy impact | Рекомендация |
| --- | --- | --- | --- | --- |
| **A. Новый provider-neutral `AdvisorPort` / equivalent** с отдельными typed `AdvisorRequest` и `AssistantResultEnvelopeV1` semantics | Interface segregation; NoteDraft остаётся backwards-safe; отдельная cancellation/error/privacy boundary; existing configured adapter потенциально может реализовать его позже | отдельный port, validator, adapter capability и deterministic no-network tests; средняя/высокая implementation complexity; появляется новая reasoning surface | явная новая передача personal context через approved boundary; можно независимо запретить raw context, retention, fallback и network до approval | **ACCEPT — owner выбрал A** |
| **B. Второй operation в существующем `LlmPort`** (`advise(...)`) при сохранении `draft_note` | можно повторно использовать часть transport/config; меньше номинальных port types | port начинает объединять note drafting и advice; выше coupling adapters/callers/error semantics; backwards-safe только при явном capability segregation и отдельной typed operation; provider implementation всё равно нужна | те же новые privacy risks, но они легче скрываются внутри уже существующего LLM boundary | **DEFER / not selected for v1** |
| **C. Assistant без LLM/provider** | no network, no credentials, минимальный privacy risk; низкая complexity; полностью deterministic | полезен только как узкий constraint satisfiability/explicit trade-off analysis; не может честно обещать общий ответ «что объективно разумнее»; больше abstentions и no recommendation | минимальный: только request и approved in-memory context | **DEFER / fallback only if separately selected** |

Таким образом, A принят как capability boundary, B отклонён для v1, а C
остаётся deferred и не входит в текущий Assistant execution path. Его возможное
будущее использование потребует отдельного owner decision, named capability и
контракта. Existing `LlmPort`, provider implementation, config, dependencies и
privacy surface в #162 не меняются.

## 10. Testing strategy

В #162 production tests не добавляются. После фиксации A будущий core
должен иметь deterministic no-network matrix; C потребует отдельного сужения
scope, если owner когда-либо выберет этот fallback:

### Request и boundary

- exact request/result types; `bool`, extra fields, wrong scalar/container types
  и all over-limit values rejected;
- NFC/edge trim accepted only as documented; case/alias/fuzzy/semantic
  equivalence does not appear;
- options 0..8, unique IDs, caller order and exact selected option preserved;
- explicit constraints/goals/context budgets and max caps checked before any
  Stage 5/provider/read/write call;
- `stage5_context_mode=none` requires `stage5_query=null` and proves zero Stage 5
  read side effects;
- `stage5_context_mode=current_explicit_facts` requires a validated
  `stage5_query`, passes the current literal query unchanged, uses exactly
  `DEFAULT_SELF_CONTEXT_LIMIT`, and derives the remaining content cap with
  `min(remaining_context_bytes, MAX_MAX_CONTENT_BYTES)`;
- `max_result_bytes=MIN_MAX_RESULT_BYTES_V1-1` is rejected as
  `ASSISTANT_INVALID_REQUEST`, `max_result_bytes=MIN_MAX_RESULT_BYTES_V1` is
  accepted, every current minimal abstention fits at that bound, and a valid
  larger result can still produce `ASSISTANT_RESULT_TOO_LARGE`; exact `bool` is
  rejected and values above 65536 are rejected;
- `current_explicit_facts` accepts only internal `AssistantStage5Fact` with
  `evidence_kind=explicit_user_fact` and `self_kind=memory`; stale snippets,
  missing UUIDs, malformed roles, path leaks, unvalidated bodies and incomplete
  factual projections return `ASSISTANT_CONTEXT_UNAVAILABLE` without heuristic
  fallback;
- fact-only `current_explicit_facts` proves that `BuildSelfModel`/
  `_read_self_model()` and forbidden preference/belief/goal claims are not read
  before the allowlist; a general Stage 5 composition that cannot prove this is
  unavailable for the mode;
- fact-only Search is backed by a metadata-filtered corpus before indexing and
  query, so forbidden note bodies never enter the Search candidate set;
- after successful retrieval/reread/allowlist validation, zero candidates or
  zero eligible facts always produce the exact abstention
  `kind=abstention`, `recommendation=null`, `selected_option=null`,
  `abstention_code=insufficient_current_context`;
- `truncated=true` or any `context_budget_exceeded` exclusion produces
  `ASSISTANT_CONTEXT_UNAVAILABLE` before allowlist/zero-context classification;
  it is never treated as a complete empty result;
- explicit/base envelope overflow returns `ASSISTANT_INVALID_REQUEST` before
  Stage 5/provider call; a complete eligible projection that cannot fit returns
  `ASSISTANT_CONTEXT_UNAVAILABLE`, never a silent partial context or arbitrary
  subset.

### Canonical envelope and exact role tests

- canonical byte tests cover Cyrillic/non-ASCII, quotation marks, backslashes,
  exact `\b`, `\t`, `\n`, `\f`, `\r` and lowercase `\u00xx` control
  escapes for BS/TAB/LF/FF/CR/NUL/VT/U+001F, unescaped slash, empty arrays,
  one/multiple options,
  one/multiple explicit context entries and one/multiple Stage 5 facts;
- exact byte assertions reject `\u000A`/`\u0009`, uppercase hex, escaped `/`,
  unnecessary `\uXXXX` for ordinary Unicode and any other alternate string
  representation; the same `AssistantCanonicalJsonEncoderV1` is exercised for
  both reasoning and result envelopes;
- exact boundary `canonical_context_bytes == max_context_bytes` is accepted
  when the applicable mode permits it, while `+1` byte overflow is rejected;
- repeated serialization is stable regardless of incidental Python dict order;
  `ensure_ascii=True`, pretty JSON, extra spaces, newlines, BOM and alternate
  serializers are invalid;
- explicit fact + `reported_fact` is valid, explicit fact + `background` is
  invalid; explicit background + `background` is valid, explicit background +
  `reported_fact` is invalid;
- Stage 5 typed fact + `reported_fact` is valid, Stage 5 typed fact +
  `background` is invalid; `contextual_preference`, `contextual_belief` and
  `historical_context` are invalid v1 roles.

### Private echo, result budget и factual projection tests

- `AssistantPrivateEchoGuardV1` rejects a short full fact and a long full fact,
  any embedded exact contiguous verbatim span of at least 32 UTF-8 bytes, and a
  substantial Cyrillic span of the same size;
- the aggregate rejects a full fact split across four sub-32-byte `rationale`
  items, across `recommendation` plus `rationale`, and across `rationale` plus
  `uncertainty`; it catches split text across whitespace boundaries after
  `echo_compare`, including the validation-only one-space boundary view;
- the bounded order-invariant matcher rejects the same full/substantial fact
  when generated parts arrive in a different provider-controlled order, while
  unrelated permutations remain valid;
- per-field spans of at least 32 bytes remain invalid; unrelated generated
  fields whose empty-separator concatenation does not match the fact remain
  valid; caller-owned `selected_option.id`/`label` text is excluded from the
  aggregate;
- the guard catches CRLF/CR, Unicode-whitespace and edge-space variations after
  `echo_compare`; case-changed text and semantic paraphrase are not treated as
  verbatim by this guard;
- a short atomic fragment below the 32-byte threshold that is not part of an
  aggregate full/substantial match is allowed, as is repetition of a
  caller-owned `selected_option.label`;
- guard violation exposes only `ASSISTANT_RESULT_INVALID`, never matched source
  text or offending output, and never produces redaction, retry, fallback or a
  partial result;
- result-budget tests cover empty/null fields with every key present,
  recommendation, analysis, abstention, `selected_option` null/object,
  Cyrillic direct UTF-8, quote/backslash escaping and multiple evidence refs;
- result-budget tests cover every current abstention code in the minimal
  envelope, prove `ambiguous_or_incomparable_options` is exactly 304 bytes,
  prove `insufficient_current_context` fits the same minimum, and recompute the
  documented constant when the contract version/schema changes;
- exact `result_bytes == max_result_bytes` is accepted and `+1` yields
  `ASSISTANT_RESULT_TOO_LARGE`; `ensure_ascii=True`, pretty JSON, omitted nulls,
  incidental dict ordering and alternate result serializers are rejected;
- factual projection tests verify body-only input, CRLF/CR normalization, NFC,
  edge Unicode-whitespace trim, preserved internal whitespace/Markdown, and
  exclusion of title/tags/note_type/path/front matter/UUID/snippet/metadata;
- empty projected body produces the empty-set abstention, while forbidden
  controls or a body-derived projection over 1024 UTF-8 bytes produces
  `ASSISTANT_CONTEXT_UNAVAILABLE`; neither case truncates, substitutes or
  selects an arbitrary fact.
- Stage 5 tests cover `truncated=true` with zero included eligible facts and
  with some included eligible facts, an explicit
  `context_budget_exceeded` exclusion, and the non-truncated complete cases:
  zero eligible facts gives `insufficient_current_context`, eligible facts give
  normal projection, a valid `candidate_not_found`-only result preserves the
  same empty-set abstention semantics, and no heuristic fallback is allowed;
  the fact-only source also proves no Self Model read and excludes forbidden
  notes before Search indexing/query.

### Independence and Self Model safety

- injected `SimulateMeResult`/prediction is never requested, read or accepted;
- preference, belief and stored goal never enter the automatic Stage 5 factual
  projection; any future use requires a separately named capability/mode;
- caller-provided context remains contextual and cannot be treated as truth;
- stored goal does not become objective until explicit current goal is present;
- `behavioral_pattern`/`decision_rule`, frequency, recency and numeric
  confidence cannot enter reasoning input;
- explicit constraint/goal refs are the only normative refs in result;
- evidence order changes do not create score or hidden tie-break.

### Result/error safety

- exact `kind`/field invariants for recommendation, analysis and abstention;
- output label is always `independent_recommendation_analysis`; forbidden
  prediction/best/canonical fields are absent;
- rationale/uncertainty/result caps fail closed without partial raw context;
- all abstention/error codes are closed, typed and free of query/body/path/
  provider details;
- high-stakes, conflicting constraints, unsupported task and zero eligible
  current facts produce the documented abstentions;
- retrieval, current-reread, integrity, classification or complete-projection
  failures produce `ASSISTANT_CONTEXT_UNAVAILABLE`, not the empty-facts
  abstention;
- `AssistantEvidenceRef` source/role pairs are revalidated exactly as in §6.2;
  the provider cannot promote background or rename a typed Stage 5 fact;
- no vault, Safe Write, Self Model mutation, persistence, network, cache,
  embeddings, RAG or logging side effect.

### Port seam

- current `LlmPort.draft_note -> NoteDraft` remains source-compatible and
  semantically unchanged;
- option A uses a separate fake `AdvisorPort` with cancellation, bounded
  request/result and no real network; option B, if owner selects it, still has
  a separately typed operation and capability test;
- deterministic C tests prove exactly which mechanical cases are useful and
  abstain on general advice outside that reduced scope.

### Docs-only checks for this task

- relative links resolve to current Stage 4/5/6 contracts and roadmap;
- changed roadmap contains no new Stage 8 or speculative roadmap item;
- no production files, provider files, schemas, `second-brain.yaml` or vault
  files are changed;
- `git diff --check` and repository documentation/style checks remain green.

## 11. Dependencies for future Assistant core

Required current foundations:

1. Stage 4 [Self Model current direct-assertion contract](self-model-v1-contract.md)
   and its `not_assessed` confidence semantics;
2. Stage 5 [Self Retrieval current UUID reread contract](self-retrieval-v1-contract.md),
   bounded context, exact evidence links and safe errors;
3. Stage 6 [Simulate Me contract](simulate-me-v1-contract.md) only as a
   separate peer boundary, never as Assistant input;
4. existing `CancellationToken`/application safe-error conventions;
5. owner-approved separate typed `AdvisorPort` boundary; current `LlmPort`
   remains unchanged;
6. future application-internal `AssistantStage5Fact` projection over the same
   Stage 5 Search -> current UUID reread -> Personal Memory validation
   authority;
7. a dedicated implementation task covering the approved port, privacy
   boundary, provider/no-provider behavior and deterministic tests.

Не требуется и не разрешается добавлять в этот dependency list новый provider,
adapter, schema field, persistence, Web/API, `second-brain-vault` change или
Assistant runtime issue.

## 12. Explicit separation from Simulate Me

| Dimension | Assistant | Simulate Me |
| --- | --- | --- |
| Question | independent recommendation / analysis | likely owner choice |
| Normative inputs | explicit current constraints/goals only; exact typed `explicit_user_fact`/`memory` facts are contextual evidence | current direct `preference`/`goal` evidence under exact-match policy |
| Preference/belief | not automatic Stage 5 factual inputs; caller context remains contextual | belief contextual-only; preference may support exact option prediction |
| Goal | Stage 5 goal is not automatic input; objective only when explicit in current request | eligible direct evidence under Stage 6 policy, still prediction only |
| Options | optional; may recommend open action or selected caller option | caller-owned bounded options are required |
| Output label | `independent_recommendation_analysis` | `prediction` / `abstention`, display **ПРОГНОЗ** |
| Input from other mode | never reads Simulate Me result | does not read Assistant result |
| Confidence | no numeric confidence | no numeric confidence |
| Persistence | ephemeral, no history | ephemeral, no prediction history |

A future Compare invocation must run both branches from an independently
constructed pre-choice context. It may compare their outputs and expose an
explicit delta, but it must not call Assistant with the prediction as context,
rewrite one label as the other, or use prediction agreement as recommendation
evidence. Historical calibration must also mask actual choice and outcome from
the Simulate Me input.

## 13. ACCEPT / DEFER / HUMAN_REQUIRED matrix

| Area | Verdict in #162 |
| --- | --- |
| Caller-owned bounded `AssistantRequest` with optional options | **ACCEPT** |
| Explicit constraints/goals/context and request-local authority rules | **ACCEPT** |
| Stage 5 fact-only current UUID context with metadata-filtered corpus and exact typed `explicit_user_fact` + `memory` projection | **ACCEPT as design boundary** |
| Deterministic `stage5_query` / limit / remaining-budget mapping | **ACCEPT as design boundary** |
| Empty eligible Stage 5 facts vs context retrieval/projection failure outcomes | **ACCEPT as exact split** |
| Stage 5 `truncated` / `context_budget_exceeded` completeness gate before factual allowlist | **ACCEPT as exact design boundary** |
| Canonical `AssistantReasoningEnvelopeV1` JSON and sole context budget calculator | **ACCEPT as exact design boundary** |
| Shared `AssistantCanonicalJsonEncoderV1` exact control escapes for context and result | **ACCEPT as exact design boundary** |
| Exact body-only `factual_text` projection and reasoning-visible Stage 5 item shape | **ACCEPT as exact design boundary** |
| Internal `AssistantStage5Fact` over current reread + Personal Memory validation | **ACCEPT as future core dependency** |
| Exact `AssistantEvidenceRef` source/role binding | **ACCEPT as exact result invariant** |
| `contextual_preference` / `contextual_belief` / `historical_context` evidence roles | **DEFER / invalid in v1** |
| Self Model preference/belief/goal treatment without normative leakage | **ACCEPT** |
| Closed recommendation/analysis/abstention Result DTO and safe taxonomy | **ACCEPT as design** |
| Canonical `AssistantResultEnvelopeV1`, exact key order, minimum 304-byte bound and result byte budget | **ACCEPT as exact design boundary** |
| Result validation order, including source/role checks and `AssistantPrivateEchoGuardV1` | **ACCEPT as exact design boundary** |
| `AssistantPrivateEchoGuardV1` per-field plus no-separator aggregate and bounded boundary-view complete/substantial verbatim comparison | **ACCEPT as bounded guard; not semantic DLP** |
| Independent output label and no Simulate Me input | **ACCEPT** |
| Bounded privacy, no-write, no-persistence and no-hidden-network boundary | **ACCEPT** |
| Testing strategy and future dependency gate | **ACCEPT** |
| Separate typed `AdvisorPort` capability boundary | **ACCEPT — owner decision A** |
| Extension of existing `LlmPort` (B) | **DEFER / not selected** |
| Deterministic-only Assistant (C) | **DEFER / fallback only** |
| LLM/provider operation and production reasoning runtime | **DEFER / separate future implementation gate** |
| Web/API/CLI projection, Compare implementation and calibration | **DEFER** |
| A/B/C capability-boundary decision | **RESOLVED — `HUMAN_REQUIRED: none`** |
| New provider, credentials, paid dependency, privacy retention or network | **DEFER / separate explicit future approval gate** |

Итог: **Owner decision A**, `HUMAN_REQUIRED: none for the Assistant v1
capability-boundary decision`. Это не разрешение provider/network/privacy
integration и не блокирует независимые Stage 5/6 maintenance tasks; Assistant
core/runtime и любой Compare runtime, который требует Assistant output, остаются
отдельным future implementation scope.
