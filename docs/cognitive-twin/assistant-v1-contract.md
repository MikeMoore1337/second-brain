# Assistant v1 — contract независимой рекомендации и analysis

Статус: **DESIGN / OWNER DECISION A APPROVED / EXPLICIT-CONTEXT-ONLY**. Для
capability-boundary решения `HUMAN_REQUIRED: none`; owner выбрал отдельный
provider-neutral `AdvisorPort`. Этот документ закрывает design-only часть
issue [#162](https://github.com/MikeMoore1337/second-brain/issues/162) и не
создаёт production runtime, provider integration, LLM operation, API или Web.

Контрольная база для этой редакции: `main`, commit
`30249d56d366c0688c451a45a7e0214114c7c197`.

Главный v1 verdict: Advisor получает только caller-explicit request inputs.
Automatic Stage 5, Self Model и Personal Memory context полностью
**DEFERRED**. Поэтому accepted Assistant v1 не передаёт через `AdvisorPort`
canonical/private note text и не требует reconstructive private-echo DLP.

## 1. Purpose и non-goals

### Purpose

`Assistant` — независимая ветка recommendation / analysis. Она отвечает на
вопрос «какое действие или вариант лучше поддержан текущей задачей, явно
переданными ограничениями, явно принятыми целями и caller-provided context»,
но не пытается воспроизвести выбор владельца.

В этом документе слово «объективнее» означает только прозрачное рассуждение
относительно явно объявленных входов и границ. Оно не означает universal truth,
медицинскую, юридическую или финансовую certainty и не превращает личный
контекст в скрытую функцию полезности.

### Non-goals этой задачи и Assistant v1

В #162 и в accepted Assistant v1 runtime запрещены:

- production application code, provider adapter, LLM call, API и Web;
- изменение текущего `LlmPort`, `LlmRequest`, `NoteDraft` или их error contract;
- выбор provider, paid dependency, model, SDK, credentials или network route;
- automatic Stage 5/Search/Self Model/vault read для Assistant request;
- automatic Personal Memory, preference, belief, goal, decision или outcome
  retrieval;
- передача `AssistantStage5Fact.factual_text`, note body, title, tags, front
  matter, path, UUID, Self Model claim text или Search snippet в `AdvisorPort`;
- передача `Simulate Me` prediction в Assistant;
- prediction владельца, фраза «так бы выбрал пользователь» и поведенческое
  копирование;
- hidden objective score из habits, frequency, recency или observed decisions;
- `behavioral_pattern`, `decision_rule`, personality, diagnosis или sensitive
  trait inference;
- vault write, Safe Write, canonical evidence creation, Self Model mutation;
- conversation/history/recommendation persistence, embeddings, RAG, vector DB,
  cache, training и automatic calibration;
- raw exceptions, provider payloads, request context или private caller text в
  logs и public errors;
- самостоятельный runtime issue, #163, Compare runtime или изменение
  `second-brain-vault`.

## 2. Architectural invariant

Три режима остаются независимыми:

```text
Assistant   = independent recommendation / analysis
Simulate Me = likely owner choice prediction
Compare     = both outputs + explicit delta
```

Accepted Assistant v1 имеет следующие invariants:

1. `Simulate Me` result никогда не является input, evidence, hint, tie-break,
   constraint или objective для Assistant.
2. Единственные reasoning inputs — `task`, caller-owned `options`,
   `explicit_constraints`, `explicit_goals` и `explicit_context` текущего
   request.
3. `explicit_context` остаётся caller-provided premise/background. Даже если
   caller сам передал текст о preference или belief, он не становится
   automatically retrieved canonical authority.
4. Goal получает objective semantics только если caller явно передал его в
   текущем `explicit_goals`. Stored goal никогда не активируется сам.
5. Каждый result имеет machine-readable label
   `independent_recommendation_analysis` и display label
   **Independent recommendation / analysis**. В Russian UI допустим текст
   **Независимая рекомендация / анализ**, но нельзя показывать его как
   `prediction`, `ПРОГНОЗ`, canonical fact, `best` или `optimal`.
6. Result — ephemeral bounded DTO. Он не является history, canonical evidence,
   access token или заменой `second-brain-vault`.

## 3. Accepted caller-owned Request DTO

Caller передаёт только bounded задачу и явные текущие inputs. Unknown fields
отвергаются. В частности, `stage5_context_mode`, `stage5_query` и любые
`stage5_facts` **не являются полями accepted Assistant v1 request**; их
наличие даёт `ASSISTANT_INVALID_REQUEST` до Advisor/read/write path.

### 3.1. Exact v1 shape

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
  max_context_bytes: int = 65536
  max_result_bytes: int = 65536
}
```

`options` optional: открытый вопрос может получить текстовую recommendation без
`selected_option`. Если options переданы, они остаются caller-owned; Assistant
не создаёт semantic option identity и не пишет эти labels в vault.

### 3.2. Exact request bounds

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
| `AssistantExplicitContext.kind` | только `fact` или `background`; это caller declaration, не vault authority |
| `max_context_bytes` | exact `int`, 1..65536; default 65536; больше cap запрещено |
| `max_result_bytes` | exact `int`, `MIN_MAX_RESULT_BYTES_V1`..65536; default 65536; больше cap запрещено |
| unknown fields | запрещены; старые `stage5_context_mode`, `stage5_query` и `stage5_facts` не принимаются |

`explicit_context(kind="fact")` — caller-reported premise, а не externally
verified truth. `explicit_context(kind="background")` может объяснять
ситуацию, но не получает normative authority и не выбирает option сам.

`MIN_MAX_RESULT_BYTES_V1` — не arbitrary safety number. Это contract-derived
constant из exact canonical `AssistantResultEnvelopeV1` serializer: берётся
максимальный UTF-8 byte size минимального valid abstention envelope для каждого
accepted `AssistantAbstentionCode` по формуле и таблице в §6.2. Для текущей
accepted схемы доказанное значение — **304 bytes**. Поэтому
`max_result_bytes < MIN_MAX_RESULT_BYTES_V1` даёт
`ASSISTANT_INVALID_REQUEST` на request-validation boundary, до canonical
serialization и `AdvisorPort`; такой запрос не может просить физически
невозможный обязательный abstention.

### 3.3. Canonical reasoning-visible envelope и context budget

`AssistantReasoningEnvelopeV1` — единственный provider/reasoning-visible
envelope и единственный источник расчёта `max_context_bytes`. В accepted v1 он
содержит только caller-explicit values:

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
  ]
}
```

В envelope нет `stage5_facts`, `AssistantStage5Fact`, Stage 5 controls, Search
metadata, internal UUID, title, tags, front matter, path, raw note body, Self
Model claim, prediction или provider metadata. `explicit_context` попадает в
envelope только потому, что caller сам передал его в текущем request.

Canonical encoding:

1. JSON syntax согласно RFC 8259;
2. UTF-8, без BOM и без trailing newline;
3. strings уже прошли documented NFC validation;
4. non-ASCII символы сериализуются непосредственно в UTF-8, не через
   `\uXXXX`, если escaping не требуется JSON syntax;
5. каждая JSON string кодируется только общим
   `AssistantCanonicalJsonEncoderV1`;
6. insignificant spaces и newlines отсутствуют;
7. separators ровно `,` и `:`;
8. root key order ровно `task`, `options`, `explicit_constraints`,
   `explicit_goals`, `explicit_context`;
9. option key order ровно `id`, затем `label`;
10. explicit-context key order ровно `kind`, затем `text`;
11. arrays сохраняют caller order;
12. `context_bytes = len(canonical_json_utf8_bytes)`.

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
являются canonical и не используются в budget calculation.

`canonical_context_bytes` вычисляется ровно для полного envelope из
validated request. Если он больше `max_context_bytes`, возвращается
`ASSISTANT_INVALID_REQUEST` без Advisor/provider/read/write call. Нет remaining
private-context budget, hidden Stage 5 cap или silent dropping explicit inputs.
Exact boundary принимается; overflow отвергается.

### 3.4. Authority входов

- `explicit_constraints` — ограничения текущей задачи. Передача в request делает
  их explicit, но не исправляет противоречия между ними.
- `explicit_goals` — цели, которые caller запрашивает или явно принимает для
  текущей задачи. Только они получают objective semantics.
- `explicit_context` — caller-provided context. `fact` означает reported
  premise, `background` — contextual background; ни один kind не становится
  automatically verified canonical truth.
- caller может явно передать bounded information о preference или belief через
  `explicit_context`, но это не разрешает чтение или реконструкцию соответствующих
  Self Model claims.
- Assistant не принимает `SelfContextResult`, `SearchHit`, `SearchRequest`, raw
  `NoteRecord`, `SelfModelClaim`, `SelfModelResult`, prediction, UUID/path или
  provider controls как authority.

## 4. Context authority и Self Model separation

### 4.1. Accepted Assistant v1 context = caller-explicit only

В accepted v1 automatic context для reasoning равен **NONE**:

| Источник | Accepted в Assistant v1 | Семантика |
| --- | --- | --- |
| `task` | да | literal caller-owned question/action scope |
| `options` | да | caller-owned bounded alternatives |
| `explicit_constraints` | да | current normative constraints |
| `explicit_goals` | да | current objective только по явному caller input |
| `explicit_context` | да | caller-reported fact/background, без automatic authority |
| Stage 5 Search/Retrieval | нет | zero read; future private capability only |
| Personal Memory `memory` | нет | не передаётся автоматически |
| Self Model `preference`, `belief`, `goal` | нет | не читается и не проецируется |
| Self Model `decision`, `outcome`, `behavioral_pattern`, `decision_rule` | нет | недоступно current Assistant v1 |
| `Simulate Me` result | нет | никогда не input |

Accepted runtime не вызывает Stage 5, Search, Self Model builder, vault reader
или `second-brain-vault`. Никакой post-filter уже прочитанного private
материала не допускается: zero read — часть capability boundary, а не
оптимизация.

### 4.2. Simulate Me и Compare

`Simulate Me` отвечает на другой вопрос — что владелец, вероятно, выбрал бы
сам. Его prediction, evidence и derived state никогда не передаются в
Assistant. Caller может передать собственный текст в `explicit_context`, но
это новый caller input, а не импорт результата Simulate Me.

Будущий Compare должен строить обе ветки из independently supplied/requested
inputs, сохранять две разные labels и показывать explicit delta. Compare не
может использовать prediction как Assistant context, tie-break, evidence или
objective. Compare runtime в #162 не создаётся.

## 5. Deterministic application boundary

Будущая application orchestration accepted v1 должна выполнять только такие
шаги:

1. Validate exact `AssistantRequest`, включая unknown-field rejection, все text
   bounds, `max_context_bytes` и `max_result_bytes >= MIN_MAX_RESULT_BYTES_V1`.
   Любая ошибка возвращается до Advisor, provider, Stage 5, Search, Self Model,
   network, log с payload или write path.
2. Сформировать единственный полный canonical
   `AssistantReasoningEnvelopeV1` только из `task`, `options`, explicit
   constraints/goals/context и проверить exact `canonical_context_bytes <=
   max_context_bytes`. Никакого Stage 5/private augmentation после этой
   проверки нет.
3. Выполнить только approved option A — отдельную typed provider-neutral
   `AdvisorPort` / equivalent boundary. Его `AdvisorRequest` содержит ровно
   validated explicit-only reasoning envelope; он не получает note body,
   `AssistantStage5Fact`, Self Model claim, Search snippet, path, UUID или
   `Simulate Me` output. Реальный provider/network runtime остаётся отдельным
   future approval gate.
4. Провести application-owned exact Result DTO validation, canonical
   serialization и result-byte check. Provider не может добавить private
   context, prediction fields, hidden score, raw metadata или write receipt.
5. При отсутствии safe basis, конфликте hard constraints, high-stakes
   certainty или genuinely incomparable options вернуть typed abstention, а не
   убедительный guess.

Deterministic C-вариант не входит в текущий Assistant v1 execution path; его
возможное будущее использование требует нового named capability, owner
decision и отдельного контракта. Existing `LlmPort` не используется как
скрытый substitute для Advisor.

### 5.1. Recommendation semantics

Recommendation — bounded conclusion независимого анализа по:

1. literal `task`;
2. caller-owned options, если они есть;
3. explicit current constraints;
4. explicit current goals;
5. caller-provided `explicit_context`.

Она не означает «владелец выберет это», «это канонический факт» или
«это доказанно оптимально». Если options отсутствуют, recommendation может
быть bounded action proposal. Если options присутствуют, `selected_option`
может быть только exact caller-owned парой `{id, label}`.

### 5.2. Обязательный abstain

Assistant обязан abstain, если:

- task не даёт понятного bounded вопроса или action scope;
- explicit constraints противоречат друг другу и caller не указал способ
  разрешения конфликта;
- options невозможно безопасно сопоставить с вопросом или они genuinely
  incomparable без дополнительной цели;
- результат потребовал бы hidden preference, behavior inference, prediction,
  invented fact или claim о universal optimality;
- запрос просит medical/legal/financial certainty, diagnosis или иной вывод,
  который нельзя дать в bounded ordinary-assistant safety boundary.

`insufficient_current_context` из прежнего private-context draft не является
accepted Assistant v1 abstention code: current v1 не запрашивает automatic
current context. Его возможное возвращение относится только к будущей
privacy-gated capability и потребует versioned contract.

## 6. Exact proposed Result DTO

```text
AssistantInputRef {
  source: "explicit_constraint" | "explicit_goal"
  ordinal: int              # 1-based ordinal inside the request field
}

AssistantEvidenceRef {
  source: "explicit_context"
  ordinal: int              # request-local ordinal
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

### 6.1. Result bounds и invariants

| Поле | Граница |
| --- | --- |
| `output_label` | exact fixed value; display text is Independent recommendation / analysis |
| `kind` | closed enum из трёх значений |
| `recommendation` | 1..2048 UTF-8 bytes только для `kind=recommendation`; иначе `null` |
| `selected_option` | `null` при no-options; иначе exact option из request или `null` |
| `rationale` | 1..8 items, каждый 1..1024 bytes; bounded total |
| `evidence_refs` | 0..32 unique request-local refs; exact source/role binding; no body, UUID, path or claim text |
| `constraints_used` | 0..16 refs; only valid explicit constraint ordinals |
| `objectives_used` | 0..8 refs; only valid explicit goal ordinals |
| `uncertainty` | 0..8 items, каждый 1..512 bytes; no numeric confidence |
| `abstention_code` | required only for `kind=abstention`; otherwise `null` |
| complete result | canonical `AssistantResultEnvelopeV1` must fit fixed `max_result_bytes`; no silent truncation |

Exact invariants:

- `kind=recommendation` требует non-null `recommendation`, valid rationale и
  `abstention_code=null`; `selected_option` может быть `null` для open action.
- `kind=analysis` требует `recommendation=null`, `selected_option=null`, valid
  rationale и может описывать trade-offs без выбора.
- `kind=abstention` требует `recommendation=null`, `selected_option=null` и
  ровно один accepted `abstention_code`.
- `selected_option`, если есть, байт-в-байт соответствует одной caller-owned
  option pair; server не создаёт ID.
- `constraints_used` и `objectives_used` ссылаются только на соответствующие
  explicit request fields. Caller context не повышается в objective через
  result serialization.
- `evidence_refs` могут ссылаться только на `explicit_context`; provider не
  может добавить другой source или изменить source/role binding.
- DTO не имеет полей `prediction`, `confidence`, `score`, `probability`,
  `best`, `optimal`, `canonical`, `provider`, `model`, `stage5_facts` или
  `write_receipt`.

### 6.2. Abstention codes и minimum result bound

```text
AssistantAbstentionCode:
  insufficient_basis
  conflicting_explicit_constraints
  ambiguous_or_incomparable_options
  unsafe_high_stakes
  unsupported_task
```

`AssistantResultEnvelopeV1` — единственная canonical serialization для
`max_result_bytes`. Все поля всегда присутствуют, nullable fields — JSON
`null`, arrays сохраняют validated DTO order:

```json
{"output_label":"independent_recommendation_analysis","kind":"abstention","recommendation":null,"selected_option":null,"rationale":["x"],"evidence_refs":[],"constraints_used":[],"objectives_used":[],"uncertainty":[],"abstention_code":"<accepted-code>","contract_version":"assistant-v1"}
```

Root key order ровно такой, как в DTO. Nested key order фиксирован:
`selected_option` — `id`, затем `label`; `evidence_ref` — `source`, `ordinal`,
`role`; `AssistantInputRef` — `source`, `ordinal`. Canonical serializer не
сортирует, не удаляет и не переставляет items. Размер определяется ровно как:

```text
result_bytes = len(canonical_AssistantResultEnvelopeV1_utf8)

MIN_MAX_RESULT_BYTES_V1 = max(
  len(canonical_AssistantResultEnvelopeV1_utf8(minimal_abstention(code)))
  for code in AssistantAbstentionCode
) = 304
```

`rationale=["x"]` — shortest valid rationale, все поля и их `null`/empty
values обязательны:

| `AssistantAbstentionCode` | Canonical UTF-8 bytes |
| --- | ---: |
| `insufficient_basis` | 289 |
| `conflicting_explicit_constraints` | 303 |
| `ambiguous_or_incomparable_options` | **304** |
| `unsafe_high_stakes` | 289 |
| `unsupported_task` | 287 |

Таким образом, текущий exact contract-derived lower bound равен **304 bytes**.
При изменении schema, key order, required fields, rationale minimum или closed
enum это значение MUST быть пересчитано и задокументировано до изменения
request bound. `AssistantCanonicalJsonEncoderV1` — общий encoder для context и
result; `ensure_ascii=True`, pretty JSON, incidental dict ordering, alternate
escaping и alternate result serializer не допускаются.

### 6.3. Exact evidence binding

В Assistant v1 `AssistantEvidenceRef` ссылается только на request-local
`explicit_context`:

| `source` | Referenced item | Обязательная `role` | Любая другая role/source |
| --- | --- | --- | --- |
| `explicit_context` | request entry с `kind="fact"` | `reported_fact` | `ASSISTANT_RESULT_INVALID` |
| `explicit_context` | request entry с `kind="background"` | `background` | `ASSISTANT_RESULT_INVALID` |

`ordinal` обязан ссылаться на существующий item соответствующего source.
Provider/AdvisorPort не может повысить background до reported fact, создать
automatic personal role или добавить Self Model/Stage 5 source. Значения
`contextual_preference`, `contextual_belief` и `historical_context` не являются
valid v1 roles; caller может передать bounded text о такой теме только как
обычный `explicit_context` и не получает от этого canonical authority.

### 6.4. Result validation order

Result validation выполняется строго в таком порядке:

1. Validate result object/type, closed enums и per-field bounds.
2. Validate `selected_option` и request-local input references.
3. Validate exact evidence source/role bindings по §6.3.
4. Canonically serialize exact `AssistantResultEnvelopeV1` общим
   `AssistantCanonicalJsonEncoderV1`.
5. Compare `result_bytes` с `request.max_result_bytes`; exact boundary accepted,
   strict overflow даёт `ASSISTANT_RESULT_TOO_LARGE`.

Любая invalid result semantics даёт `ASSISTANT_RESULT_INVALID`; offending text
не возвращается и не логируется. Нельзя делать truncation, dropping rationale,
uncertainty или refs, alternate serialization, retry, automatic model
replacement или hidden provider fallback.

## 7. Typed abstention и safe error taxonomy

### 7.1. Domain abstention

Accepted v1 использует только пять codes из §6.2. Abstention — обычный domain
result, а не exception; raw provider, request или caller context в него не
добавляются. `insufficient_current_context` намеренно отсутствует: отсутствие
automatic Stage 5 read не является ошибкой и не создаёт empty-context branch.

### 7.2. Typed application errors

```text
AssistantErrorCode:
  ASSISTANT_INVALID_REQUEST
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
| `ASSISTANT_INVALID_REQUEST` | request/envelope не прошли exact type, unknown-field, text, bounds или context-fit validation |
| `ASSISTANT_CANCELLED` | operation отменена до безопасного завершения |
| `ASSISTANT_TIMEOUT` | bounded approved reasoning operation превысила deadline |
| `ASSISTANT_PROVIDER_UNAVAILABLE` | approved Advisor boundary недоступна |
| `ASSISTANT_PROVIDER_FAILURE` | approved Advisor boundary вернула failure |
| `ASSISTANT_MALFORMED_RESULT` | Advisor/provider не дал exact Assistant DTO |
| `ASSISTANT_RESULT_TOO_LARGE` | canonical result превышает `max_result_bytes` |
| `ASSISTANT_RESULT_INVALID` | result нарушает exact DTO, source/role, budget или safety invariant |

`ASSISTANT_CONTEXT_UNAVAILABLE` из прежнего private-context draft не является
ошибкой accepted explicit-only v1. Он может появиться только в отдельной
future private-context capability после version bump и privacy/provider
approval. Нельзя использовать его для скрытого Stage 5 read или для
постфактум объяснения automatic retrieval.

Provider errors описаны только как будущая public mapping для отдельно
одобренного reasoning boundary; production runtime #162 не реализуется. Raw
provider exception, network detail, credentials, endpoint и model никогда не
попадают в public message.

## 8. Bounds, privacy и no-write boundary

Разрешённый in-memory state accepted v1 ограничен validated caller request,
canonical explicit-only reasoning envelope, explicit reasoning result и
disposable validation metadata.

Запрещено:

- открывать или изменять `second-brain-vault` через этот design slice;
- вызывать Safe Write, создавать canonical evidence или менять Self Model;
- выполнять Stage 5/Search/Retrieval/Self Model/vault read для Assistant;
- сохранять task, explicit context, prompt, recommendation, result или history
  в файл, DB, browser storage, cache, queue или telemetry;
- логировать raw task, caller context, claim, absolute path, UUID mapping,
  secret или provider response;
- выполнять hidden network/provider call, embeddings, RAG, vector search,
  training или background operation;
- возвращать raw provider payload, raw exception, hidden score, prediction или
  private canonical Stage 5 text.

Caller-explicit context не считается hidden canonical retrieval: caller сам
передал его в operation. Это не отменяет no-log, no-persistence, bounded DTO,
no-write и future provider/privacy approval requirements.

### 8.1. DEFERRED / NON-NORMATIVE FUTURE PRIVATE-CONTEXT NOTES

Следующие пункты сохраняют полезный анализ прежнего draft, но **не являются
частью accepted Assistant v1 runtime, Request DTO, AdvisorRequest, Result
validation order или dependency gate**. Они не дают разрешения на Stage 5 read,
private transmission или provider integration.

#### Future private-context retrieval checklist

Отдельная versioned capability когда-либо должна заново решить, нужны ли ей:

1. fact-only Search corpus, отфильтрованный по validated metadata **до** body
   indexing/query;
2. Search candidate -> current UUID reread -> managed-note/Personal Memory
   validation authority;
3. exact body-only `factual_text` projection без title, tags, front matter,
   path, UUID, snippet, metadata, hidden Self Model claim, truncation или
   substitution;
4. complete bounded window semantics, включая отдельный outcome для
   `truncated`, `context_budget_exceeded`, malformed/incomplete projection и
   valid `candidate_not_found`;
5. exact private-context envelope, content budget, minimization, retention,
   logging policy и transmission boundary;
6. privacy leak/output policy, redaction semantics, retry/fallback policy и
   dedicated deterministic tests.

До принятия всех этих решений automatic Personal Memory и любой canonical
private text остаются deferred. `insufficient_current_context` и
`ASSISTANT_CONTEXT_UNAVAILABLE` также относятся к этой будущей capability, а
не к current explicit-only v1.

#### Rejected experimental private echo design

Предыдущая версия предлагала `AssistantPrivateEchoGuardV1` с per-field,
empty-separator aggregate, one-space boundary probe, order-independent
coverage, punctuation handling и multiplicity state. Эта reconstructive
механика **REJECTED / DEFERRED**, а не accepted security boundary:

- wrapped fragments внутри generated commentary показывают, что whole-field
  exact matching не обнаруживает все disclosure forms;
- multiplicity и repeated source occurrences показывают, что naive coverage
  может дать false positive или потребовать всё более сложного state;
- exact substring/coverage matching не является semantic DLP и не обещает
  защиту от paraphrase, reassembly, wrappers или другой reconstruction;
- accepted v1 не передаёт canonical/private Stage 5 text в AdvisorPort, поэтому
  такой guard не нужен как runtime gate и не должен усложняться в #162.

Будущая private capability должна сначала выбрать principled privacy boundary:
например, запретить private payload полностью либо пройти отдельное owner
privacy/provider decision с доказуемой output policy. Простое добавление ещё
одного matcher к этому документу не считается решением.

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

`LlmGateway` валидирует semantic `NoteDraft`, не даёт result semantics для
recommendation, rationale, evidence refs, abstention или independent-advice
policy. Фактическая typed boundary находится в
[`ports.py`](../../src/second_brain/application/ports.py), а application
composition — в [`llm.py`](../../src/second_brain/application/llm.py).

Передача Assistant request через `draft_note` и разбор `NoteDraft.content`
были бы typed contract violation: исчезли бы Assistant result semantics и
отдельная capability boundary. Молчаливо добавлять fields в `NoteDraft` или
менять meaning `context` запрещено.

### 9.2. Owner decision A — resolved

**Owner decision: A — ACCEPT.** Для будущего Assistant core выбран новый
provider-neutral `AdvisorPort` / equivalent с отдельными typed
`AdvisorRequest` и `AssistantResultEnvelopeV1` semantics.

`HUMAN_REQUIRED: none for the Assistant v1 capability-boundary decision`.
Это решение разрешает только application-owned explicit-context boundary. Оно
не разрешает provider, credentials, network, model, retention,
privacy-provider integration или runtime. Любой такой следующий шаг остаётся
отдельным explicit approval gate.

Будущая `AdvisorPort` boundary принимает только validated
`AssistantReasoningEnvelopeV1` из caller-explicit полей и возвращает exact
`AssistantResultEnvelopeV1`. Она не получает automatic Personal Memory,
`AssistantStage5Fact`, note body, Self Model claim, Search snippet или
Simulate Me output. Application validator владеет canonical serialization,
source/role binding, result-size check, abstention/error mapping и no-log
boundary до и после provider call.

| Вариант | Плюсы | Минусы / complexity / coupling | Privacy impact | Рекомендация |
| --- | --- | --- | --- | --- |
| **A. Новый provider-neutral `AdvisorPort` / equivalent** с отдельными typed `AdvisorRequest` и `AssistantResultEnvelopeV1` semantics | Interface segregation; `NoteDraft` остаётся backwards-safe; отдельная cancellation/error boundary; Advisor получает только explicit caller payload | отдельный port, validator, adapter capability и deterministic no-network tests; implementation complexity | private canonical Stage 5 text не входит в v1 payload; provider/data-handling всё равно требуют отдельного future approval | **ACCEPT — owner выбрал A** |
| **B. Второй operation в существующем `LlmPort`** (`advise(...)`) при сохранении `draft_note` | можно повторно использовать часть transport/config | port объединяет note drafting и advice; выше coupling и риск скрытого context/provider semantics | сложнее доказать отдельную privacy boundary | **DEFER / not selected for v1** |
| **C. Assistant без LLM/provider** | no network, no credentials, минимальный privacy risk | полезен только для узкого deterministic constraint analysis, не для общего advice | минимальный | **DEFER / separate decision only** |

Итог: A принят как capability boundary, но automatic private context, provider
integration и runtime остаются deferred. Existing `LlmPort`, provider
implementation, config, dependencies и privacy surface в #162 не меняются.

## 10. Testing strategy

В #162 production tests не добавляются. Будущий core должен иметь
deterministic no-network matrix для explicit-only boundary; private-context
tests относятся только к отдельной future capability.

### Request и no-read boundary

- exact `AssistantRequest` shape; unknown fields, включая
  `stage5_context_mode`, `stage5_query` и `stage5_facts`, rejected;
- `bool`, extra fields, wrong scalar/container types и all over-limit values
  rejected before Advisor/provider/read/write;
- NFC/edge trim accepted only as documented; case/alias/fuzzy/semantic
  equivalence does not appear;
- options 0..8, unique IDs, caller order and exact selected option preserved;
- explicit constraints/goals/context budgets и both max caps checked before
  Advisor call;
- prove ZERO Stage 5/Search/SelfModel/vault reads for every accepted request;
- prove no `Simulate Me` read or result import;
- prove no vault write, persistence, cache, history, telemetry or hidden
  provider fallback;
- caller text about preference/belief remains ordinary explicit context and
  never becomes automatic Self Model authority;
- `max_result_bytes=MIN_MAX_RESULT_BYTES_V1-1` rejected as
  `ASSISTANT_INVALID_REQUEST`, exact 304 accepted, values above 65536 rejected;
- canonical context overflow returns `ASSISTANT_INVALID_REQUEST` before
  Advisor, with no truncation or silent field dropping.

### Canonical envelope, result and exact bytes

- canonical byte tests cover Cyrillic/non-ASCII, quotation marks, backslashes,
  exact `\b`, `\t`, `\n`, `\f`, `\r` and lowercase `\u00xx` control escapes
  for NUL/VT/U+001F, unescaped slash, empty arrays, options and explicit
  context;
- exact byte assertions reject `\u000A`/`\u0009`, uppercase hex, escaped `/`,
  unnecessary `\uXXXX` for ordinary Unicode, pretty JSON, BOM, trailing
  newline and alternate serializers;
- the same `AssistantCanonicalJsonEncoderV1` is exercised for reasoning and
  result envelopes; repeated serialization is stable regardless of incidental
  dict order;
- exact context boundary equals `max_context_bytes`, strict `+1` overflows;
- all five accepted abstention codes are recomputed from the minimal envelope,
  with `ambiguous_or_incomparable_options` exactly 304 bytes;
- exact result boundary equals `max_result_bytes`, strict `+1` yields
  `ASSISTANT_RESULT_TOO_LARGE`; nullable fields remain present as JSON `null`;
- `selected_option.id`/`label` is caller-owned and exact; input refs and
  explicit-context source/role pairs cannot be promoted or renamed;
- no result test expects `stage5_current_context`, `AssistantStage5Fact`,
  `insufficient_current_context` or `AssistantPrivateEchoGuardV1` in accepted
  v1.

### Abstention, errors and port seam

- recommendation, analysis and abstention invariants are closed and typed;
- high-stakes, conflicting constraints, unsupported task and ambiguous options
  produce the documented accepted abstentions;
- raw body/path/UUID/provider details never appear in errors or logs;
- option A uses a fake `AdvisorPort` with cancellation, bounded explicit-only
  request/result and no real network;
- current `LlmPort.draft_note -> NoteDraft` remains source-compatible and
  semantically unchanged;
- any future private-context retrieval, projection, DLP or
  `ASSISTANT_CONTEXT_UNAVAILABLE` tests are isolated under a separately named,
  versioned capability and cannot become implicit v1 coverage.

## 11. Dependencies and future capability gate

Accepted explicit-only Assistant v1 depends only on:

1. exact caller-owned `AssistantRequest` and Result DTO validation;
2. shared `AssistantCanonicalJsonEncoderV1` and deterministic byte budget;
3. existing `CancellationToken`/application safe-error conventions;
4. owner-approved separate typed `AdvisorPort` boundary;
5. deterministic no-network tests and no-write/no-persistence boundary.

Stage 4 Self Model, Stage 5 Self Retrieval, Personal Memory, Search, current
UUID reread, `AssistantStage5Fact`, `factual_text` projection and Stage 6
Simulate Me are **not current Assistant v1 dependencies**. They remain separate
peer contracts or future inputs for a new private-context capability only.

That future capability must have a separately named/versioned request mode and
contract plus all of the following gates:

- owner privacy decision;
- provider data-handling/transmission decision;
- exact minimization and canonical private-payload contract;
- retention/logging review;
- private-context output/DLP policy;
- dedicated security, deterministic and regression tests.

No new provider, adapter, schema field, persistence, Web/API, vault change or
Assistant runtime issue is created by #162.

## 12. Explicit separation from Simulate Me

| Dimension | Assistant | Simulate Me |
| --- | --- | --- |
| Question | independent recommendation / analysis | likely owner choice |
| Normative inputs | explicit current constraints/goals; caller context remains contextual | current direct `preference`/`goal` evidence under its own contract |
| Preference/belief | only caller-explicit text in `explicit_context`; no automatic claim read | separate prediction semantics |
| Goal | objective only when caller explicitly supplies `explicit_goals` | eligible direct evidence under Stage 6 policy |
| Options | optional; may recommend open action or selected caller option | caller-owned bounded options required |
| Output label | `independent_recommendation_analysis` | `prediction` / `abstention`, display **ПРОГНОЗ** |
| Input from other mode | never reads Simulate Me result | does not read Assistant result |
| Automatic private context | NONE in v1 | governed only by Simulate Me contract |
| Confidence | no numeric confidence | no numeric confidence |
| Persistence | ephemeral, no history | ephemeral, no prediction history |

A future Compare invocation must run both branches from independently supplied
pre-choice inputs. It may compare their outputs and expose an explicit delta, but
it must not call Assistant with the prediction as context, rewrite one label as
the other, use prediction agreement as recommendation evidence or silently add
Stage 5 private context. Historical calibration must mask actual choice and
outcome from the Simulate Me input.

## 13. ACCEPT / DEFER / HUMAN_REQUIRED matrix

| Area | Verdict in #162 |
| --- | --- |
| Caller-owned bounded `AssistantRequest`: task/options/constraints/goals/context | **ACCEPT** |
| Exact rejection of `stage5_context_mode`, `stage5_query` and `stage5_facts` in v1 request | **ACCEPT as explicit-only boundary** |
| Independent recommendation / analysis semantics | **ACCEPT** |
| Separate typed provider-neutral `AdvisorPort` capability boundary | **ACCEPT — owner decision A** |
| Bounded deterministic Request/Result DTO validation | **ACCEPT** |
| Canonical `AssistantReasoningEnvelopeV1` with explicit-only fields | **ACCEPT as exact design boundary** |
| Shared `AssistantCanonicalJsonEncoderV1` exact control escapes | **ACCEPT as exact design boundary** |
| Canonical `AssistantResultEnvelopeV1`, exact key order and 304-byte minimum result bound | **ACCEPT as exact design boundary** |
| Exact caller-owned option and explicit-context evidence/source/role invariants | **ACCEPT as exact design boundary** |
| No Stage 5/Search/Self Model/vault reads in Assistant v1 | **ACCEPT as exact privacy boundary** |
| No persistence, history, vault write, hidden fallback or Simulate Me contamination | **ACCEPT** |
| Automatic Stage 5 private context | **DEFER / non-normative future capability only** |
| Automatic Personal Memory retrieval into advice | **DEFER / non-normative future capability only** |
| Provider transmission of canonical/private text | **DEFER / separate provider/privacy approval** |
| `AssistantStage5Fact` and exact body-only `factual_text` projection | **DEFER / future private-context contract only** |
| Stage 5 completeness, truncation and `candidate_not_found` semantics | **DEFER / future private-context contract only** |
| `AssistantPrivateEchoGuardV1` per-field/aggregate/permutation/coverage/multiplicity design | **REJECTED / DEFERRED; not a v1 security boundary** |
| Private-context DLP, redaction and semantic reconstruction protection | **DEFER / separate privacy decision** |
| Deterministic-only Assistant (C) | **DEFER / separate decision only** |
| LLM/provider operation and production reasoning runtime | **DEFER / separate implementation and provider gate** |
| Web/API/CLI projection, Compare implementation and calibration | **DEFER** |
| A/B/C capability-boundary decision | **RESOLVED — `HUMAN_REQUIRED: none`** |
| New provider, credentials, paid dependency, retention, privacy integration or network | **DEFER / separate explicit future approval gate** |

Итог: **Owner decision A**, `HUMAN_REQUIRED: none for the Assistant v1
capability-boundary decision`. Accepted v1 — explicit-context-only independent
advice without automatic private retrieval. Stage 5/Personal Memory/private
transmission и любой privacy/DLP mechanism остаются clearly deferred; #162 не
создаёт их runtime и не блокирует независимые Stage 5/6 maintenance tasks.
