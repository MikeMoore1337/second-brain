# Personal Cognitive Twin — design & roadmap v1

Статус этого документа: **DESIGN / ROADMAP ONLY**. Production Cognitive Twin,
Personal Memory Contract, Decision Journal, Self Model, RAG, embeddings и
prediction runtime в рамках этой задачи не реализуются.

Базовая точка design — merged `main`:
`5936482b8a2903cf8cecbe5c412393a1eccdfab3`.

Issue #66 является source of truth для границ работы. Этот документ описывает
будущую архитектуру поверх существующих контрактов; он не заменяет их и не
изменяет их в рамках #66.

## 1. Цель, инварианты и границы

Personal Cognitive Twin должен отвечать на два разных вопроса:

1. что пользователь явно сообщил, сделал или позже наблюдал;
2. какую текущую гипотезу о его выборе можно построить из этих материалов.

Это не один и тот же слой. Главный invariant:

> `second-brain-vault` — единственный canonical source of truth. Self Model,
> evidence graph, indexes, embeddings, preference estimates, confidence,
> calibration и любые другие model representations — rebuildable derived state.

Следствия:

- удаление любого derived state не удаляет и не изменяет пользовательские
  Markdown/YAML-знания;
- пользовательская заметка является canonical только после обычного reviewed
  capture и Safe Write;
- draft, transcript до review, поисковый hit, LLM output и inference не
  являются фактом только потому, что они выглядят убедительно;
- ни один derived claim не записывается автоматически обратно в canonical
  note;
- новые semantic labels не создают новый `NoteType`, новую базу или новый
  способ обхода существующего Safe Write.

В #66 запрещены production code для Personal Memory Contract, Decision Journal,
Personal Timeline, Self Model, Self Retrieval, RAG, Simulate Me, Compare,
Calibration и Active Personal Learning. Также запрещены embeddings/vector DB,
новые provider/framework dependencies, schema migration, изменения
`NoteDraft`, `SearchIndexPort`, Search DTO, `TranscriptionPort`, `LlmPort`,
Safe Write, Web/API behavior и любые изменения `second-brain-vault`.

## 2. От чего строится Cognitive Twin

Уже существующий pipeline остаётся единственным входом:

```text
private second-brain-vault
    -> FileSystemVaultReader.scan()
    -> build_report()
    -> validated NoteRecord
```

Вокруг него уже есть:

- UUIDv7 identity note, managed Markdown/YAML и provenance;
- `NoteDraft` как semantic draft без identity, path, timestamp и write receipt;
- reviewed Text/URL/Voice capture;
- Safe Write с dry-run, diff, no-overwrite, post-write validation и rollback;
- Search/Retrieval v1, где индекс получает `SearchDocument`, а exact current
  body повторно читается из vault по UUID.

Cognitive Twin не создаёт параллельный `MemoryStore`. Он добавляет будущие
read-only projections поверх current `NoteRecord` и использует существующий
reviewed write path для новых canonical assertions.

## 3. Revised architecture

На диаграмме пунктирные блоки — design boundaries будущих стадий, а не код,
добавленный в #66.

```mermaid
flowchart TB
    subgraph CAP[Existing reviewed capture]
        TEXT[Text / URL]
        VOICE[Voice / audio]
        TRANS[TranscriptionPort -> reviewed transcript]
        DRAFT[NoteDraft / future reviewed semantic draft]
        SAFE[Existing Safe Write\n+reviewed semantics]
        TEXT --> DRAFT
        VOICE --> TRANS --> DRAFT
        DRAFT -->|user review| SAFE
    end

    SAFE --> VAULT[(second-brain-vault\ncanonical Markdown/YAML)]

    subgraph READ[Canonical read boundary]
        READER[FileSystemVaultReader.scan]
        REPORT[build_report -> current NoteRecord]
        VAULT --> READER --> REPORT
    end

    REPORT --> SEARCH[Search projection\nSearchIndexPort]
    SEARCH --> HIT[SearchHit = candidate]
    HIT --> RETRIEVE[RetrieveManagedNote\nread current canonical note again]
    RETRIEVE --> CTX[bounded personal context]

    REPORT --> EVIDENCE[typed evidence refs\ncompact in-memory DTO]
    EVIDENCE --> TIMELINE[Personal Timeline\nderived read model]
    EVIDENCE --> SELF[Self Model\nderived claims]
    CTX --> ASSIST[Assistant\nindependent analysis]
    CTX --> SIM[Simulate Me\nlikely user choice]
    CTX --> COMPARE[Compare\nSimulate Me + Assistant + delta]
    SIM --> PRED[derived prediction]
    PRED --> CAL[comparison / calibration]
    COMPARE --> GROWTH[Growth boundary\n"куда хочет прийти"]

    DERIVED[(all derived state\nrebuildable / disposable)]
    SEARCH -.-> DERIVED
    EVIDENCE -.-> DERIVED
    TIMELINE -.-> DERIVED
    SELF -.-> DERIVED
    PRED -.-> DERIVED
    CAL -.-> DERIVED
```

Ключевая граница retrieval: Search или будущий embedding index может только
сократить пространство кандидатов. Финальный context pack всегда строится из
текущих canonical records, повторно прочитанных из vault. Stale index entry,
изменённый body или удалённая note не могут попасть в финальный personal
context как будто они всё ещё canonical.

## 4. Canonical state и derived state

| Представление | Статус | Где живёт | Можно пересоздать/удалить | Правило |
| --- | --- | --- | --- | --- |
| Managed Markdown note: UUIDv7, `type`, `created`, optional `updated`, `tags`, `links`, body | Canonical | `second-brain-vault` | Нет без потери user data | Единственная пользовательская запись знания |
| Reviewed explicit fact, statement, goal, preference, belief, decision или outcome | Canonical | Body и минимальные reviewed metadata note | Нет | Это assertion/observation пользователя, а не оценка модели |
| Persisted source provenance | Canonical, если пользователь её принял в Safe Write | Existing YAML `sources` | Нет | Источник относится к note, а не является Cognitive Twin state |
| Decision Journal record | Canonical | Одна reviewed managed note и при необходимости связанные notes | Нет | История выбора и outcome принадлежат пользователю |
| Transcript до review, `NoteDraft`, preview, review token, confirmation token, Safe Write receipt | Ephemeral candidate/process state | Память процесса/UI | Да | Не evidence до review и публикации |
| Search projection, FTS index, future embedding index | Derived | Disposable adapter state | Да | Только candidate retrieval; body не authoritative |
| Evidence refs и evidence edges | Derived | Compact DTO/in-memory или disposable cache | Да | Ссылки всегда разрешаются к текущим UUID |
| Personal Timeline | Derived read model | On-demand first; optional disposable cache later | Да | Не новый журнал событий |
| Self Model claims, preference estimates, confidence, stale/conflict status | Derived | On-demand first; optional disposable cache later | Да | Не записываются обратно в note |
| Simulate Me prediction, Compare result, calibration aggregates | Derived | Runtime/result store only if later needed | Да | Удаление сбрасывает историю модели, не actual user decisions |
| Active Learning question | Derived UX state | Runtime | Да | Сам вопрос не создаёт fact |

Если derived projection противоречит note, исправляется projection или её
алгоритм; canonical note не переписывается автоматически. Если пользователь
исправил inference, это создаёт новую reviewed canonical assertion, а не
"подтверждает" старую модельную запись.

## 5. Evidence model

Evidence kind отвечает на вопрос «какого типа материал мы видим», а не «насколько
он истинен вообще». Универсальной шкалы, в которой observed decision всегда
сильнее explicit statement, нет: сила зависит от claim, контекста и времени.

| Evidence kind | Смысл | Canonical source | Что можно выводить |
| --- | --- | --- | --- |
| `explicit_user_fact` | Пользователь после review сообщает конкретный факт о себе или своей ситуации | Reviewed note/body | Факт пользовательского сообщения с его временным контекстом; не внешняя независимая verification |
| `user_statement` | Пользователь сообщает preference, belief, goal, rule или объяснение | Reviewed note/body | Что пользователь это утверждает/считает/хочет, но не что утверждение объективно истинно |
| `observed_decision` | Зафиксирован реально выбранный вариант и контекст доступных вариантов | Decision Journal или другой reviewed canonical record | Наблюдение поведения; один выбор не доказывает постоянную preference |
| `outcome_later_observation` | Позднее зафиксирован фактический результат, реакция или reassessment | Обновлённый reviewed Journal или связанная observation note | Что произошло позже; не следует подменять ожидание результатом |
| `model_inference` | Derived hypothesis, pattern или prediction, построенный из evidence | Только derived Self Model/retrieval/prediction state | Гипотезу с confidence и audit refs; не canonical fact |

### Непереговорное правило inference

`model_inference` **никогда автоматически не становится canonical fact** — даже
если confidence высок, evidence повторяется, LLM сформулировала claim уверенно
или prediction совпала с выбором. Чтобы claim стал пользовательским знанием,
пользователь должен явно подтвердить его через reviewed capture; тогда в vault
появляется новая `explicit_user_fact` или `user_statement`, а исходный
inference остаётся отдельным derived артефактом или исчезает при rebuild.

Нельзя смешивать в одном поле формулировку пользователя и формулировку модели.
Если note содержит текст «мне кажется, я выбираю X», это canonical
`user_statement`; если система выводит «пользователь обычно выбирает X», это
`model_inference` с отдельными supporting references.

## 6. Каноническая taxonomy и metadata verdict

### 6.1. Нужна ли большая taxonomy

- **ACCEPT:** очень маленький optional routing label помогает отличать decision
  note от обычной заметки и не требует отдельного graph schema.
- **CHANGE:** `self_kind` должен быть ограниченным scalar label, а не свободным
  YAML-документом; `domain` должен быть bounded slug без обязательного
  глобального справочника.
- **RISK:** большая taxonomy с подтипами, facets, ontology и domain registry
  быстро станет вторым schema contract, который нужно мигрировать вместе с
  vault.
- **DEFER:** иерархию типов, multi-domain, controlled vocabulary доменов,
  evidence subtypes и автоматическую классификацию оставляем будущим стадиям.

Итог: отдельную canonical taxonomy ради taxonomy не создаём. На первом этапе
достаточно опциональных `self_kind` и `domain`, а точное evidence kind живёт в
derived evidence model и в смысле reviewed body.

### 6.2. Verdict по `self_kind` и `domain`

Предлагаемые additive fields:

```yaml
self_kind: decision
domain: career
```

`self_kind` — один из минимального начального набора:
`memory`, `preference`, `belief`, `goal`, `decision`, `outcome`. Это label
формы canonical note, а не утверждение, что её содержание истинно. Например,
explicit user fact может быть записан в note с `self_kind: memory`.

`domain` — optional один lowercase ASCII slug длиной не более 64 bytes, без
пробелов, `/`, `\\`, control characters и YAML collection. `career` — пример,
но не начало обязательного domain registry. Значения вроде `career/backend`
или массивы доменов в v1 не принимаются.

Verdict: оба поля допустимы как optional additive metadata без обязательной
переклассификации старых notes. Для нового Personal Memory draft
`self_kind` должен быть задан, а `domain` остаётся optional; обычные notes и
существующий `NoteDraft` могут продолжать жить без обоих полей.

Не добавляем в canonical front matter `confidence`, `supporting_evidence`,
`contradicting_evidence`, `generated_at`, `embedding`, `model_version`,
`stale`, `superseded` или `calibration`. Это derived/model metadata и они
создали бы ложное впечатление, что модельный snapshot является пользовательским
источником.

### 6.3. Schema version verdict

`schema_version` bump не нужен, если поля остаются optional, scalar, additive и
старые notes без них полностью валидны. Текущая policy уже сохраняет unknown
front matter fields при round-trip write; следующая implementation-задача
добавит явную валидацию именно этих двух fields, не делая остальные unknown
fields частью Personal Memory Contract.

Schema bump потребовался бы только при изменении обязательных полей, смысла
существующих полей, shape существующего `sources`/`tags`/`links` или при
необратимой миграции старых notes. Ни один из этих случаев не нужен.

## 7. Temporal semantics

Существующие `created` и `updated` — storage lifecycle timestamps. Они не должны
молча использоваться как время события, решения или начала preference.

В будущем temporal context должен различать:

- **asserted at** — когда пользователь сообщил assertion;
- **observed/event at** — когда произошло decision или outcome;
- **valid from / valid until** — период применимости preference, belief, goal
  или rule; интервал трактуется как `[from, until)`;
- **generated at** — когда derived claim/retrieval/prediction был пересчитан;
- **storage created/updated** — когда note была создана или изменена в vault.

Все машинные timestamps используют существующее правило RFC 3339 с явным UTC
offset. Неизвестное время остаётся неизвестным; его нельзя подменять
`created`. Приблизительное время должно хранить precision (`day`, `month`,
`period` или `unknown`) в будущей semantic модели, а не придумывать точный
момент.

### Семантика по типам

| Объект | Правило времени |
| --- | --- |
| Preference | Новая explicit statement начинает новый claim в указанном контексте. `valid_until` появляется только при явном supersede/end или подтверждённом прекращении применимости. Старую preference не удаляем. |
| Belief | Разделяем время, когда пользователь так считал, и время/период, к которому относится содержание belief. Поздняя contradiction не стирает старую assertion. |
| Goal | Goal имеет lifecycle: active, achieved, abandoned или replaced; переход подтверждается canonical statement/decision/outcome, а не Self Model. |
| Decision rule | Rule действует только в заявленном контексте и периоде. Одна decision не превращается автоматически в правило. Исключения и условия должны быть явно записаны. |
| Outcome/later observation | Outcome имеет собственное observed time и может быть позднее исходной decision. `actual result` не переписывает `expected result`. |
| Stale evidence | Stale — derived status по claim-specific freshness policy или отсутствию свежего подтверждения; note остаётся canonical и не удаляется. |
| Conflicting evidence | Conflict — одновременно поддерживаемые несовместимые evidence в релевантном контексте; система показывает conflict, а не выбирает большинство молча. |

Superseded preference означает, что новый canonical evidence ограничил или
заменил применимость старого claim. Это не физическое удаление старой note и не
универсальный вывод о том, что пользователь «изменился навсегда». Для разных
доменов, горизонтов и обстоятельств две на первый взгляд конфликтующие
могут быть одновременно валидны.

## 8. Personal Memory Contract v1 — proposal

Personal Memory Contract — это минимальное соглашение о том, как reviewed
пользовательское знание попадает в существующий vault. Это не отдельная БД и не
ontology для всех будущих claims.

### Семантика

1. Canonical memory — это существующая managed Markdown note со stable UUIDv7.
2. Note становится memory только после пользовательского review и Safe Write.
3. `self_kind` и optional `domain` помогают маршрутизации и фильтрации, но не
   добавляют truth/confidence semantics.
4. Human-readable meaning, context, reasons, dates и uncertainty остаются в
   body, где пользователь может их прочитать и исправить.
5. Derived evidence refs всегда ссылаются на UUID canonical note; они не
   копируют body в отдельное authoritative storage.
6. Удаление индекса или Self Model не требует восстановления из него
   пользовательских данных.

### Минимальная форма

Существующие обязательные поля не меняются:

```yaml
---
id: 019...
type: zettel
created: 2026-09-05T12:00:00+03:00
updated: 2026-09-05T12:00:00+03:00
self_kind: memory
domain: career
tags: []
links: []
---
```

`self_kind` и `domain` optional для backward compatibility; пример показывает
их присутствие в новом reviewed Personal Memory record. `type` остаётся
существующим `NoteType`, а не `personal_memory`.

Минимальный body должен быть читаемым без приложения. В зависимости от
`self_kind` достаточно следующих смысловых секций:

```markdown
## Assertion

Что пользователь сообщает, считает, хочет или вспоминает.

## Context

К каким обстоятельствам это относится.

## Time

Известный период, дата события или явная отметка, что время неизвестно.

## Notes

Оговорки, причины, ссылки и детали; optional.
```

Это не требование к существующим Markdown notes и не повод отклонять обычный
Markdown. Будущий semantic draft может использовать template, но body остаётся
human-readable и проходит тот же review.

### YAML metadata против structured Markdown body

| Смысл | YAML metadata | Structured Markdown body |
| --- | --- | --- |
| Stable UUID, existing note type, storage timestamps | Да | Нет |
| Узкий routing label `self_kind` | Да, optional | Можно повторить словами, но не нужно |
| Один bounded `domain` | Да, optional | Можно описать контекст подробнее |
| Ситуация, варианты, причины, criteria, expected/actual result | Нет | Да |
| Human wording assertion и uncertainty | Нет | Да |
| Несколько temporal contexts одной decision | Нет | Да, пока не появится доказанная потребность в typed fields |
| Supporting/contradicting evidence, confidence, stale status, generated time | Нет | Не canonical body автоматически; это derived DTO |

Не строим YAML ontology ради индекса. Metadata остаётся только там,
где она нужна для стабильной identity/маршрутизации и bounded validation.

## 9. Decision Journal v1 — proposal

Decision Journal — canonical human-readable record одной существенной decision,
а не лог всех кликов и не inferred personality profile. В v1 это существующая
managed note с `self_kind: decision`, optional `domain` и structured Markdown
body.

Минимальный lifecycle:

```text
situation
  -> available options
  -> information known at decision time
  -> criteria
  -> chosen option
  -> reasons
  -> confidence
  -> expected result
  -> actual result
  -> reassessment
```

Рекомендуемый body template:

```markdown
## Situation

Что требовало решения и в каком контексте.

## Available options

Какие варианты реально были доступны в момент решения.

## Information known at decision time

Что было известно тогда; будущие сведения сюда задним числом не подмешиваются.

## Criteria

Какие критерии и ограничения использовались.

## Chosen option

Выбранный вариант.

## Reasons

Почему он был выбран.

## Confidence

Субъективная уверенность пользователя в момент решения; это не Self Model
confidence.

## Expected result

Что пользователь ожидал получить и в какой срок.

## Actual result

Что произошло позже; initially может быть пусто.

## Reassessment

Что пользователь теперь считает иначе, сохранил бы выбор или изменил бы правило.
```

`actual result` и `reassessment` не заполняются моделью автоматически. Для
короткой decision они могут быть добавлены в ту же note после reviewed update;
для длинного периода или нескольких наблюдений допустима отдельная canonical
observation note, явно связанная с decision. Точный update use case — отдельная
implementation boundary, не часть #66.

Важное разделение:

- decision journal фиксирует выбор и reasoning пользователя;
- Self Model позже может вывести pattern из нескольких журналов;
- один journal не подтверждает permanent preference или decision rule;
- actual choice для calibration должен быть canonical/reviewed, а не только
  inferred из системной telemetry.

## 10. Personal Timeline v1 — derived/read model

Personal Timeline — это read model текущих canonical notes, а не новый event
store. Он собирает события и assertions с известным temporal context:

```text
canonical notes -> evidence extraction -> timeline items -> sorted read model
```

Концептуальный `TimelineItem` содержит:

- `event_kind` (`decision`, `goal`, `statement`, `outcome` или другой узкий
  reviewed kind);
- `event_at` и temporal precision, либо явную отметку `unknown`;
- короткий summary без подмены полного body;
- supporting canonical note UUIDs;
- derived status (`current`, `stale`, `conflicted`, `superseded`, если применимо);
- `generated_at` read model.

Сначала timeline пересобирается on demand из `FileSystemVaultReader ->
build_report`. Persistent timeline DB, watcher и incremental event sourcing не
нужны. Если explicit event time отсутствует, item может быть показан по
storage `created` только с честной подписью «время сохранения», а не как время
события.

Удаление timeline не теряет ни одной note. Несколько conflicting dates не
схлопываются в одну «правильную» дату без показа неопределённости.

## 11. Self Model v1 — proposal

Self Model описывает «какой пользователь сейчас» как набор проверяемых derived
claims, а не как один profile JSON или психологический диагноз.

### Минимальный claim DTO

Каждый derived claim концептуально содержит:

```text
SelfModelClaim
  category / dimension
  claim
  supporting_canonical_evidence_uuids
  contradicting_canonical_evidence_uuids (optional, empty when absent)
  confidence
  temporal_context
  status: current | stale | conflicted | superseded | unresolved
  generated_at
  derivation_version
```

`category / dimension` может быть `preference`, `belief`, `goal`,
`decision_rule`, `behavioral_pattern` или другим explicitly supported
dimension. Список dimensions не становится YAML ontology в #66.

`confidence` — оценка именно derived claim, а не факт и не пользовательская
уверенность из Decision Journal. Для audit claim обязан быть able to answer:
«какие current notes его поддерживают и какие с ним спорят?» Отсутствие
supporting refs делает claim недопустимым для personal context.

### Rebuildability

Self Model полностью пересобирается из current canonical vault records и
фиксированной версии derivation policy. Первый implementation boundary должен
быть on-demand; optional cache допустим только как disposable projection с
привязкой к snapshot/fingerprint и `derivation_version`.

Derived state не должен быть единственным местом для пользовательского
утверждения, prediction или outcome. Если Self Model удалён, останутся все
исходные notes, и следующий rebuild может снова вывести другой claim из них.
Модель не выполняет automatic inference write-back, background user scoring или
diagnosis.

## 12. Evidence graph без graph DB

Graph здесь — термин для ссылок и relations в памяти, а не требование Neo4j,
RDF, OWL или graph framework.

Минимальные DTO:

```text
EvidenceRef
  note_id: UUIDv7
  evidence_kind
  locator: optional section/block hint
  temporal_context

EvidenceEdge
  source_note_id: UUIDv7
  target_note_id: UUIDv7
  relation: supports | contradicts | supersedes | outcome_of | motivates
  generated_at
```

Практические правила:

- node identity — существующий note UUID, не path и не embedding id;
- `locator` не заменяет UUID и не должен быть единственной ссылкой, потому что
  body может измениться;
- existing Obsidian wikilink — полезный signal, но semantic edge должен быть
  провалидирован к current notes;
- missing/deleted UUID даёт unresolved edge, а не восстановление из stale index;
- graph строится из current scan в обычных collections и может быть полностью
  удалён;
- relations не объявляют truth, они объясняют происхождение derived claim.

## 13. Self Retrieval поверх Search/Retrieval v1

Существующая граница остаётся такой:

```text
FileSystemVaultReader
  -> build_report
  -> SearchDocument projection
  -> SearchIndexPort
  -> SearchHit
  -> RetrieveManagedNote(note_id)
  -> current canonical RetrievedNote
```

Будущий Self Retrieval добавляет semantic filtering/evidence assembly после
этой границы:

1. принять bounded query и режим (`Assistant`, `Simulate Me` или `Compare`);
2. получить Search/будущие semantic candidates;
3. рассматривать каждый hit как candidate, а не как evidence truth;
4. по UUID повторно получить current canonical note через существующий
   `RetrieveManagedNote`/reader;
5. построить typed evidence refs, проверить temporal status, contradiction и
   stale conditions;
6. собрать bounded context pack с claim, evidence kind, UUID и uncertainty;
7. передать разные части pack в выбранный режим с явной boundary.

Embedding hit, BM25 rank, cached snippet или LLM-selected paragraph не могут
заменить шаг 4. При удалении/изменении note старый candidate отбрасывается или
разрешается заново к current record.

Embeddings оправданы только после доказанного failure mode lexical Search/Retrieval
на реальных bounded use cases и отдельного решения о privacy, retention,
rebuild, deletion и provider boundary. До этого используется текущий Search;
vector DB, embedding provider и RAG в #66 не добавляются.

## 14. Три режима и Anti-echo-chamber boundary

| Режим | Что он оптимизирует | Выход | Чего он не делает |
| --- | --- | --- | --- |
| `Assistant` | Независимый рекомендательный анализ задачи, constraints и явно заданных criteria | Recommendation, rationale, uncertainty и trade-offs | Не подменяет recommendation наиболее привычным выбором пользователя |
| `Simulate Me` | Prediction того, что пользователь вероятнее всего выбрал бы сам сейчас | Predicted choice, confidence, supporting/contradicting evidence и temporal caveats | Не называет prediction объективно лучшим решением |
| `Compare` | Одновременное сопоставление двух независимых outputs | Вероятный пользовательский выбор + independent recommendation + reasons for divergence | Не схлопывает disagreement в один ответ |

Independent `Assistant` branch не получает уже готовый predicted choice как
скрытую подсказку и не обучается на результате `Simulate Me` внутри того же
запроса. Если personal context нужен для анализа, он подаётся как явно
помеченная evidence, а criteria независимой рекомендации остаются видимыми.

`Compare` должен уметь показать:

- preference против stated goal;
- stale evidence против свежего утверждения;
- weak confidence и маленький sample;
- competing interpretations одного набора notes;
- disagreement между habitual choice и growth-optimal/independent
  recommendation;
- missing evidence, из-за которого не следует делать сильный вывод.

Это anti-echo-chamber boundary: «похоже на прошлое поведение» не означает
«надо так же советовать», а «independent recommendation отличается» не означает
«модель ошиблась».

## 15. Prediction & Calibration v1

В v1 не нужен ML training pipeline. Нужен audit-friendly lifecycle:

```text
prediction
  -> predicted choice + confidence
  -> canonical real choice
  -> comparison
  -> calibration aggregate
```

Derived prediction должна сохранять в своём runtime/derived DTO:

- predicted choice и bounded confidence;
- temporal/context fingerprint запроса;
- supporting и contradicting canonical UUIDs;
- `generated_at` и `derivation_version`.

Actual choice приходит из reviewed Decision Journal, explicit statement или
другого явно разрешённого canonical capture. Отсутствие actual choice — это
`unobserved`, а не неправильный prediction. Comparison различает как минимум
`match`, `mismatch`, `partial/ambiguous` и `unobserved`.

Calibration v1 показывает количество наблюдаемых predictions, confidence bins,
empirical hit rate и calibration gap. Brier score или другая proper scoring
metric может быть добавлена только если choice space и оценка outcome достаточно
определены; отсутствие достаточного sample должно показываться явно. Нельзя
выдавать маленькую выборку за validated personal trait.

Prediction и calibration остаются derived. После удаления derived state
история prediction может быть сброшена, но canonical actual choices, reasons и
outcomes не теряются.

## 16. Active Personal Learning v1

Система может в будущем показать optional question только когда есть хотя бы
одно из условий:

- low confidence;
- conflicting evidence;
- missing evidence, без которого режим вынужден бы угадывать.

Question — candidate для пользователя, не событие и не fact. Ответ проходит
обычный путь:

```text
optional question -> user answer -> reviewed capture -> NoteDraft/transcript
-> user review -> Safe Write -> canonical vault
```

Вопрос не может сам создать preference, исправить Self Model или повысить
confidence. Никакого background scoring, постоянного опроса, скрытого consent
или психологической диагностики. Частотные ограничения и пользовательское
включение learning mode понадобятся при реализации, но не входят в #66.

## 17. Voice compatibility

Voice остаётся provider-neutral и не знает о Cognitive Twin:

```text
voice/audio
  -> TranscriptionPort
  -> reviewed transcript
  -> future Decision Journal semantic draft
  -> user review
  -> existing Safe Write
```

`TranscriptionPort`, `TranscriptionRequest` и `Transcript` не получают
`self_kind`, decision schema, Self Model или personal context. Транскрипция —
только кандидат plain text; она не означает, что пользователь подтвердил
сформулированный inference. Semantic routing происходит после review на
application boundary будущего Decision Journal/Personal Memory stage.

## 18. Growth Engine relationship

Границы двух моделей:

```text
Cognitive Twin -> кто пользователь сейчас и как обычно выбирает
Growth         -> куда пользователь хочет прийти
```

Growth implementation не изменяется. В будущем Cognitive Twin может supply
только derived, evidence-backed context для use cases:

- goal conflict detection;
- decision rules, мешающие stated goal;
- personalized learning;
- Compare habitual choice vs growth-optimal choice.

При конфликте preference и goal система не переписывает goal и не объявляет
один из них «настоящим». Она показывает обе canonical assertions, temporal
context и derived explanation divergence.

## 19. Спорные решения в формате ACCEPT / CHANGE / RISK / DEFER

| Тема | ACCEPT | CHANGE | RISK | DEFER |
| --- | --- | --- | --- | --- |
| Отдельная canonical taxonomy | Минимальные routing labels полезны | `self_kind` сделать узким и optional | Большая ontology станет миграционным контрактом | Nested taxonomy, facets и domain registry |
| `self_kind` | `memory/preference/belief/goal/decision/outcome` достаточно для первой маршрутизации | Не трактовать label как truth или evidence kind | Один label может не описать смешанную note | Multi-label и custom kinds |
| `domain` | Один bounded slug помогает фильтрации | Не вводить обязательный vocabulary | Свободные значения могут расползтись | Taxonomy/aliases/registry |
| Timestamps | Existing storage times сохраняем | Не использовать `created` как event time | Ложная точность разрушит timeline | Общие `valid_from/until` metadata до доказанной потребности |
| Outcome/reassessment | Хранить рядом с Decision Journal для audit | Для длинных историй разрешить linked observation notes | Mutation semantics Safe Write ещё не определена | Отдельный outcome event store |
| Superseded preferences | Сохранять старые assertions и derived relation | Ограничивать relation контекстом и временем | Автоматический supersede может стереть nuance | Global identity merge |
| Assertion против inference | Разделять evidence kinds строго | Confirmation создаёт новую assertion | LLM может звучать как факт | Automatic inference write-back |
| Confidence | Нужен в derived claim и prediction | Не класть в canonical metadata | Число может создать ложную объективность | ML calibration training |
| Contradiction | Показывать supporting и contradicting refs | Не голосовать большинством без context | False conflict при разных domains/horizons | Автоматический conflict resolver |
| Derived Self Model storage | На старте rebuild-on-demand | Cache только disposable и versioned | Persistent state может стать скрытым source of truth | Durable model DB |
| Embeddings | Возможны только после измеренной lexical gap | Сначала текущий Search/Retrieval | Privacy, deletion и stale vectors | Provider/vector DB/RAG |

## 20. Revised 8-stage roadmap

Последовательность сохраняется: каждая стадия зависит от canonical semantics
предыдущей. Перестановка не нужна.

### Stage 1 — Personal Memory Contract v1

- **Цель:** добавить минимальные reviewed semantics поверх existing managed note,
  не создавая новую persistence model.
- **Входные зависимости:** UUIDv7 `NoteRecord`, front matter round-trip,
  `FileSystemVaultReader -> build_report`, Safe Write и текущие bounded draft
  boundaries.
- **Canonical changes:** optional `self_kind` и optional `domain`; старые notes
  валидны без них; новый Personal Memory draft обязан иметь `self_kind`.
- **Derived state:** только чтение/валидация; Self Model, graph и embeddings
  отсутствуют.
- **Public/application contracts:** отдельный reviewed personal-memory wrapper
  поверх существующего `NoteDraft` допустим в следующей implementation task;
  `NoteDraft`, Search, LLM и transcription contracts не переопределяются.
- **Risks:** преждевременная taxonomy, arbitrary YAML и accidental inference
  write-back.
- **Explicit out-of-scope:** Decision Journal runtime, temporal engine,
  timeline, Self Model, retrieval modes, updates existing notes.
- **Acceptance boundary:** optional fields проходят bounded validation и
  Safe Write/rebuild без schema bump; никаких новых derived claims.

### Stage 2 — Decision Journal v1

- **Цель:** canonical, reviewed и human-readable запись выбора с expected и
  later outcome.
- **Входные зависимости:** Stage 1 labels, existing Safe Write, reviewed
  Text/URL/Voice capture.
- **Canonical changes:** notes с `self_kind: decision`, structured body и
  explicit options/reasons/criteria; actual/reassessment появляются только
  через reviewed update или linked observation.
- **Derived state:** optional extraction candidates, не canonical; никакой
  automatic journal completion.
- **Public/application contracts:** отдельный Journal draft/use case и позже
  reviewed update boundary; current `NoteDraft` остаётся semantic base.
- **Risks:** backfilling information, которого не было известно при decision;
  путаница user confidence и model confidence.
- **Explicit out-of-scope:** automatic action telemetry, personality profile,
  calibration и recommendation.
- **Acceptance boundary:** пользователь видит и подтверждает все journal
  sections; expected/actual остаются различимыми.

### Stage 3 — Personal Timeline v1

- **Цель:** derived chronological view canonical assertions, decisions и
  outcomes.
- **Входные зависимости:** Stage 1 semantics и Stage 2 decision/outcome
  records; current scan/report.
- **Canonical changes:** нет; только reviewed notes из предыдущих стадий.
- **Derived state:** `TimelineItem` с event time/precision, UUID refs, status и
  `generated_at`; on-demand first.
- **Public/application contracts:** read-only bounded Timeline query/result;
  no new event-store write port.
- **Risks:** принять storage time за event time, схлопнуть conflicting dates,
  потерять unknown precision.
- **Explicit out-of-scope:** background watcher, event sourcing, timeline DB и
  automatic date extraction без provenance.
- **Acceptance boundary:** timeline честно различает event time, storage time
  и unknown; rebuild из vault даёт current result.

### Stage 4 — Self Model v1

- **Цель:** построить evidence-backed derived claims о текущих patterns,
  preferences, beliefs, goals и decision rules.
- **Входные зависимости:** Stage 1 evidence semantics, Stage 2 journal и
  Stage 3 temporal read model.
- **Canonical changes:** нет; inferred claims не пишутся в vault.
- **Derived state:** `SelfModelClaim` с category, claim, support/contradict
  UUIDs, confidence, temporal context, status и generated time.
- **Public/application contracts:** bounded read-only Self Model DTO с
  explainability refs; no generic profile write API.
- **Risks:** overfitting, stale/contradictory evidence, diagnosis-like
  language, false confidence.
- **Explicit out-of-scope:** automatic profile mutation, LLM fact insertion,
  psychological diagnosis, durable model DB.
- **Acceptance boundary:** каждый claim объясним current evidence и disappears
  safely after derived-state deletion/rebuild.

### Stage 5 — Self Retrieval v1

- **Цель:** собрать personal context поверх current Search/Retrieval без
  превращения index hit в truth.
- **Входные зависимости:** existing `SearchIndexPort`/`RetrieveManagedNote`,
  Stage 4 claims/evidence.
- **Canonical changes:** нет.
- **Derived state:** candidate list, evidence graph DTO и bounded context pack.
- **Public/application contracts:** read-only Self Retrieval request/result;
  existing Search DTO and index port remain compatible.
- **Risks:** stale snippets, leaking unrelated private context, unbounded
  prompt assembly, retrieval echo chamber.
- **Explicit out-of-scope:** embeddings, vector DB, RAG framework, provider
  expansion and auto-write.
- **Acceptance boundary:** every final context item re-read by current UUID;
  missing/changed notes are excluded or explicitly marked.

### Stage 6 — Simulate Me v1

- **Цель:** predict likely user choice from current evidence, without calling it
  recommendation.
- **Входные зависимости:** Stage 4 Self Model and Stage 5 current context;
  Stage 2 journal provides choice examples.
- **Canonical changes:** actual choices remain canonical only when reviewed;
  prediction itself is not canonical.
- **Derived state:** predicted choice, bounded confidence, evidence refs,
  temporal caveats, `generated_at` and derivation version.
- **Public/application contracts:** explicit Simulate Me request/result with
  uncertainty and candidate evidence.
- **Risks:** prediction becomes imperative, stale habitual patterns, small
  sample, hidden recommendation leakage.
- **Explicit out-of-scope:** independent recommendation, Compare, calibration
  training and background predictions.
- **Acceptance boundary:** response is labelled prediction and can say
  `insufficient evidence`; no canonical write occurs.

### Stage 7 — Compare v1 + Prediction & Calibration v1

- **Цель:** сопоставить likely user choice с independent recommendation и
  измерять observed prediction quality.
- **Входные зависимости:** Stage 5 context, Stage 6 prediction, canonical
  actual choice/outcome from Stage 2.
- **Canonical changes:** only user-reviewed actual decisions/outcomes; no
  calibration fields in notes.
- **Derived state:** two independent outputs, divergence reasons, comparisons,
  confidence buckets and calibration aggregates.
- **Public/application contracts:** Compare result always contains both branches;
  Calibration result reports sample/unknowns and avoids overclaiming.
- **Risks:** recommendation contamination by prediction, outcome selection bias,
  false precision from tiny sample.
- **Explicit out-of-scope:** ML training pipeline, automatic goal changes,
  optimization against a hidden reward and universal user score.
- **Acceptance boundary:** disagreement is visible; unobserved outcomes are not
  counted as failures or successes.

### Stage 8 — Active Personal Learning v1

- **Цель:** optional questions only where evidence is weak, conflicting or
  missing.
- **Входные зависимости:** Stage 4 confidence/conflict, Stage 5 retrieval,
  Stage 7 comparison/calibration signals.
- **Canonical changes:** only reviewed user answers through existing capture and
  Safe Write.
- **Derived state:** question candidate, reason, expiry/rate state and expected
  information gain if needed.
- **Public/application contracts:** optional question/result with explicit user
  control; no direct model mutation endpoint.
- **Risks:** annoying interrogation, leading questions, privacy pressure,
  question treated as evidence.
- **Explicit out-of-scope:** background scoring, autonomous profiling,
  diagnosis, silent consent and automatic answer-to-fact conversion.
- **Acceptance boundary:** user can ignore/reject a question; only reviewed
  answer can become canonical evidence.

# EXACT NEXT IMPLEMENTATION SCOPE:

## Personal Memory Contract v1

Это следующий implementation slice после review этого design. Он не является
частью текущего PR и не должен начинаться автоматически.

### Минимальные semantics

1. Ввести понятие reviewed Personal Memory note поверх уже существующей managed
   note.
2. Разрешить два optional additive front matter fields:
   `self_kind` и `domain`.
3. Для нового Personal Memory input требовать `self_kind`; для старых и
   обычных notes отсутствие обоих fields остаётся valid.
4. Ограничить `self_kind` значениями `memory`, `preference`, `belief`, `goal`,
   `decision`, `outcome`.
5. Ограничить `domain` одним lowercase ASCII slug до 64 bytes; domain registry
   не вводить.
6. Считать body canonical только после user review и existing Safe Write.
7. Не интерпретировать `self_kind` как evidence kind, confidence, truth или
   inferred profile.

### Файлы и слои

Минимальный будущий change set должен ограничиться следующими слоями:

- `src/second_brain/domain/models.py` — маленький typed value/enum для
  разрешённых `self_kind`, если он нужен для общего domain validation; новый
  `NoteType` не добавлять.
- `src/second_brain/application/validation.py` — распознавать и проверять
  optional fields при `build_report`, сохраняя существующее поведение для
  notes без них.
- `src/second_brain/application/personal_memory.py` — новый reviewed DTO и
  bounded use case/wrapper, не меняющий смысл `NoteDraft`.
- `src/second_brain/application/ports.py`, `writes.py` и
  `src/second_brain/adapters/vault/writer.py` — только additive dedicated path
  для Personal Memory plan, который переиспользует существующие manifest,
  dry-run, diff, no-overwrite, post-write validation, receipt и rollback. Не
  превращать generic Safe Write в произвольный metadata map.
- `tests/test_scanner.py`, `tests/test_writes.py` и узкий Search regression
  test — только проверки новой metadata boundary и совместимости.
- `docs/architecture/vault-contract-v1.md` — обновить контракт после
  реализации, если review согласует fields и validation.

`NoteDraft`, `SearchIndexPort`, Search DTO, `TranscriptionPort`, `LlmPort`,
существующие research provenance и public Web/API contracts не менять. Если
добавляется application wrapper, он принимает существующий `NoteDraft` как
внутренний semantic content и добавляет только reviewed Personal Memory
metadata.

### Backward compatibility и schema

- `schema_version` остаётся `1`.
- Notes без `self_kind`/`domain` не мигрируются и продолжают проходить scan,
  Search и Retrieval.
- Unknown front matter fields по-прежнему сохраняются round-trip; только два
  согласованных поля получают explicit validation.
- Неправильный type/shape нового поля даёт bounded diagnostic и не должен
  превращаться в silent coercion.
- Новый path не меняет существующие note roots и не создаёт
  `NoteType.PERSONAL_MEMORY`.

### Safe Write interaction

Personal Memory input обязан пройти:

```text
reviewed PersonalMemoryDraft
  -> existing Prepare(dry-run)
  -> exact diff / explicit confirmation
  -> existing Apply
  -> post-write scan
```

По умолчанию остаётся dry-run. Apply не должен принимать LLM inference,
непроверенный transcript, arbitrary YAML или client-controlled path. Existing
no-overwrite и rollback invariants сохраняются. Изменение уже существующей
note, supersede workflow и append-only outcome update не входят в эту первую
implementation task; сначала реализуется безопасное создание reviewed record.

### Search projection interaction

На следующем slice `SearchDocument`, `SearchHit`, `SearchIndexPort` и текущая
candidate semantics не меняются. `self_kind`/`domain` не добавляются в Search
DTO и не становятся hidden filter. Existing Search продолжает находить terms в
title/body/tags; Self Retrieval и evidence filtering — отдельные Stage 5.

### Validation и tests

Обязательные проверки будущего slice:

- accepted optional `self_kind` values и canonical lowercase `domain`;
- missing fields на старых notes;
- reject list/map/bool/empty/oversized/control-character values;
- reject uppercase or path-like domain values;
- YAML round-trip с unknown fields, comments и existing wikilinks;
- dry-run не меняет vault, apply создаёт ровно одну managed note, post-write
  scan видит поля;
- no-overwrite, containment, symlink и rollback regressions не ослаблены;
- existing Search projection и Retrieval не меняют DTO/результат для notes без
  новых fields;
- LLM/transcription/web paths не получают implicit personal-memory write
  capability;
- targeted tests during implementation, затем один финальный Python 3.14
  `ruff format --check`, `ruff check`, `mypy src tests` и `pytest` согласно
  `AGENTS.md`.

### Security

- allowlist только для двух fields; никаких arbitrary front matter mappings;
- bounded UTF-8/ASCII sizes и отказ от control characters;
- никакого network, LLM, credential или background inference в validator/write
  path;
- user review остаётся authority boundary, а inference и transcript считаются
  untrusted candidates;
- test fixtures используют отдельный temporary vault; `second-brain-vault`
  не читается и не меняется;
- safe errors не раскрывают absolute paths, secrets или provider details.

### Что НЕ входит

Не входят Personal Memory Contract v2, Decision Journal runtime и update
workflow, Timeline, Self Model, evidence graph persistence, Self Retrieval,
RAG, embeddings, vector DB, Simulate Me, Compare, Calibration, Active Learning,
automatic inference write-back, schema bump, new dependencies, new note type,
domain registry, psychological profiling, live smoke, production deployment,
issue creation и любые изменения `second-brain-vault`.
