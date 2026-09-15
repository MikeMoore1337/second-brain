# Personal Cognitive Twin — design & roadmap v1

Статус этого документа: **DESIGN / ROADMAP**. Personal Memory Contract v1,
Stage 2 Decision Journal v1 core и Stage 3 Personal Timeline v1 core
реализованы в текущем репозитории. Stage 4 Self Model core реализован в
текущем `main`, а local Web Self Model остаётся read-only projection. Stage 5
Self Retrieval также реализован bounded core/Web/CLI slices из #89, #90 и #91
поверх approved contract issue #88; он остаётся read-only и current-UUID based.
Owner-approved v1 contracts фиксируют conservative direct-assertion и
current-UUID retrieval policies. Stage 6 Simulate Me получил approved
mechanical design contract в #100 и provider-free exact-match
prediction/abstention runtime/core и Web projection; confidence, provider
runtime и persistence отсутствуют. Для Stage 7 boundary issue #162 зафиксирован design-only
contract [Assistant v1](assistant-v1-contract.md): independent recommendation /
analysis отделён от Simulate Me; owner выбрал A — отдельный provider-neutral
`AdvisorPort` с explicit-context-only payload. Automatic Personal Memory
context, provider/network/privacy integration и real Advisor runtime пока не
разрешены. Issue #163 получает отдельный mechanical
[Compare v1 contract](compare-v1-contract.md): три зоны остаются раздельными,
а Delta сравнивает только typed terminal states и exact request-local option
IDs.
Retrospective Calibration v1 остаётся отдельным current-vault и
pre-choice-only contract; provider-free bounded core реализован в issue #175,
а user-facing Web/API/CLI и persistence остаются future gates.

Stage 14 Personal Experiments v1 получил нормативный design contract в Issue
[#304](https://github.com/MikeMoore1337/second-brain/issues/304) и
[personal-experiments-v1-contract.md](personal-experiments-v1-contract.md).
На design gate зафиксированы exact Goal/Stage 12 binding, reviewed companion
records, explicit lifecycle и enrollment, provider-free descriptive result,
Safe Write boundary и запрет автоматической адаптации. Phase 14.1 read-side
records, Phase 14.2 reviewed Safe Write, Phase 14.3 provider-free evaluator,
Phase 14.4 owner-only Web/API/UI, Phase 14.5 adversarial security/E2E gate и
Phase 14.6 final release/closeout завершены serial PRs #322, #323, #325, #327,
#328 и финальным release PR с `Closes #304`; точная evidence-карта находится в
нормативном контракте. Stage 14 находится в production; Phase 15.0 contract
по Issue #331 complete, Phase 15.1 exact source/candidate core complete (PR
#333, merged/deployed as `e233f61feee15b27adc1bc84c43e544eff3fa62f`), Phase
15.2 operational store complete; 15.3 projection/evaluation complete; Phase
15.4 private Web/API/UI complete (PR #336, merged/deployed as
`7304488355661d99af1d79f3aac676a328597434`), а Phase 15.5
security/privacy/integration/E2E находится в delivery; Phase 15.6 ещё не
начата.

По текущему status после merged Stage 8 QA Cognitive Twin v1 / Stages 1–8
завершены и находятся в production. Stage 9 — это следующий уровень
**Cognitive Twin v2**; его prospective audit/calibration contract фиксируется
отдельно в [prospective-audit-calibration-v1-contract.md](prospective-audit-calibration-v1-contract.md).
Stage 9A/9B/9C и Stage 9D Web/API с integration QA реализованы в отдельных
implementation slices; после закрытия Issue #244 весь Stage 9 находится в
production. Операционный store выводится из явно переданного `web.env`,
остаётся вне repository/vault/release и не требует нового env key или изменения
systemd. Stage 10 Behavioral Self Model v1 и explicit Stated-vs-Observed
mapping реализованы отдельным Stage 10A–10D slice; Stage 10 остаётся
derived/read-only для vault и не меняет canonical schema. Stage 11A и Stage
11B реализованы как bounded current Goal identity/context и explicit
Goal-to-choice/friction slices; Stage 11C0 получил отдельный design contract,
а Stage 11C Growth Advisor runtime реализован в PR #268. Stage 11D0 получил
отдельный Growth Learning / Question design contract; Stage 11D runtime и
Stage 11E Web/API, integration QA и closeout реализованы в Issue #274.

Точный status snapshot перед #175 implementation — canonical `main`:
`a461b0b08d4e396561a869d7348700d5d86934bc`.

Issue #66 задаёт исходную архитектурную границу roadmap. Для текущего Stage 4
Self Model scope и contract source of truth — issue #81 и
[self-model-v1-contract.md](self-model-v1-contract.md). Stage 5 contract source
of truth — issue #88 и [self-retrieval-v1-contract.md](self-retrieval-v1-contract.md).
Этот roadmap описывает status и sequencing поверх существующих contracts и не
заменяет их.

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
    REPORT --> JOURNAL[Canonical Decision Journal\npre-choice information]
    CTX --> ASSIST[Assistant\nindependent analysis]
    CTX --> SIM[Simulate Me\nlikely user choice]
    CTX --> COMPARE[Compare\nSimulate Me + Assistant + delta]
    SIM --> SESSIONPRED[session prediction\nderived / ephemeral]
    JOURNAL --> BACKTEST[retrospective replay\nmask chosen option and outcome]
    BACKTEST --> HISTPRED[current Simulate Me\nhistorical prediction]
    HISTPRED --> CAL[rebuildable calibration]
    SESSIONPRED -.-> FUTUREAUDIT[future policy-governed\nsystem-operation audit]
    COMPARE --> GROWTH[Growth boundary\n"куда хочет прийти"]

    DERIVED[(all derived state\nrebuildable / disposable)]
    SEARCH -.-> DERIVED
    EVIDENCE -.-> DERIVED
    TIMELINE -.-> DERIVED
    SELF -.-> DERIVED
    SESSIONPRED -.-> DERIVED
    HISTPRED -.-> DERIVED
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
| Enrolled reviewed evidence record: exact `second_brain_personal_memory: 1` + `evidence_kind` + `self_kind` + temporal fields + optional `domain` + reviewed body | Canonical | Existing YAML metadata и Markdown body | Нет | Evidence class и semantic subject фиксируются пользователем, а не выводятся моделью |
| Any managed note without the exact Personal Memory marker, включая coincidentally named fields | Canonical note, но не typed Cognitive Twin evidence | `second-brain-vault` | Нет | Старые notes valid для vault/Search/Retrieval; Personal Memory validator не запускается, classification и semantics не добавляются задним числом |
| Persisted source provenance | Canonical, если пользователь её принял в Safe Write | Existing YAML `sources` | Нет | Источник относится к note, а не является Cognitive Twin state |
| Decision Journal record | Canonical | Одна reviewed managed note и при необходимости связанные notes | Нет | История выбора и outcome принадлежат пользователю |
| Transcript до review, `NoteDraft`, preview, review token, confirmation token, Safe Write receipt | Ephemeral candidate/process state | Память процесса/UI | Да | Не evidence до review и публикации |
| Search projection, FTS index, future embedding index | Derived | Disposable adapter state | Да | Только candidate retrieval; body не authoritative |
| Evidence refs и evidence edges | Derived | Compact DTO/in-memory или disposable cache | Да | Ссылки всегда разрешаются к текущим UUID |
| Personal Timeline | Derived read model | On-demand first; optional disposable cache later | Да | Не новый журнал событий |
| Self Model direct claims, descriptive counts, unassessed confidence, temporal context and policy fingerprint | Derived | On-demand first; optional disposable cache later | Да | Не записываются обратно в note; stale/conflict/supersede/status inference disabled in v1 |
| Retrospective calibration aggregate | Derived | Recomputed read model | Да | Полностью пересобирается из canonical historical journals и текущей derivation policy |
| Prospective Simulate Me prediction/session result | Derived ephemeral state | Runtime/result DTO | Да | После удаления не восстанавливается как историческое наблюдение; v1 не обещает prospective calibration |
| Future system-operation audit record | Canonical operational/audit state, не user evidence | Отдельная policy-governed boundary будущего | Нет без потери audit history | Нужен только для будущей prospective calibration; в #66 не создаётся |
| Active Learning question | Derived UX state | Runtime | Да | Сам вопрос не создаёт fact |

Если derived projection противоречит note, исправляется projection или её
алгоритм; canonical note не переписывается автоматически. Если пользователь
исправил inference, это создаёт новую reviewed canonical assertion, а не
"подтверждает" старую модельную запись.

## 5. Evidence model

У enrolled canonical reviewed record есть две независимые axes:

- `evidence_kind` — provenance/class: что именно было зафиксировано и как мы
  это знаем;
- `self_kind` — semantic subject: о чём пользовательская информация.

`domain` — отдельный optional context label. `evidence_kind` не отвечает на
вопрос «насколько это истинно», а `self_kind` не отвечает на вопрос «было ли
это реально сделано». Универсальной шкалы, в которой observed decision всегда
сильнее explicit statement, нет: сила зависит от claim, контекста и времени.

Эти axes и temporal companion fields получают Cognitive Twin semantics только
после exact per-note opt-in: `second_brain_personal_memory: 1`. Enrollment не
выводится из наличия, имени, shape или значения любого companion field. Без
exact marker note остаётся обычной managed note, а совпадающие
`evidence_kind`, `self_kind`, `domain`, `evidence_at` и
`evidence_at_precision` остаются ordinary unknown front matter без Personal
Memory validation, diagnostics или typed evidence semantics.

Canonical `evidence_kind` v1 имеет ровно четыре значения:

- `explicit_user_fact` — пользователь после review явно сообщает факт о себе
  или своей ситуации;
- `user_statement` — пользователь после review формулирует мнение, preference,
  belief, goal, rule или объяснение;
- `observed_decision` — пользователь после review фиксирует реально выбранный
  вариант и доступные ему варианты;
- `outcome_later_observation` — пользователь после review фиксирует поздний
  результат, реакцию или reassessment.

Названия намеренно описывают происхождение evidence, а не объективную truth.
`model_inference` не входит в этот enum и никогда не является canonical
`evidence_kind`.

| Evidence kind | Смысл | Canonical source | Что можно выводить |
| --- | --- | --- | --- |
| `explicit_user_fact` | Reviewed explicit user fact | Typed canonical note с отдельным `self_kind` | Факт пользовательского сообщения с временным контекстом; не внешняя независимая verification |
| `user_statement` | Reviewed user statement | Typed canonical note с отдельным `self_kind` | Что пользователь это утверждает/считает/хочет, но не что утверждение объективно истинно |
| `observed_decision` | Reviewed observation of a real choice | Stage 2 Decision Journal note | Наблюдение поведения; один выбор не доказывает постоянную preference |
| `outcome_later_observation` | Reviewed later outcome/observation | Stage 2 linked outcome note или reviewed Journal observation | Что произошло позже; не следует подменять ожидание результатом |
| `model_inference` | Derived hypothesis, pattern или prediction | Только derived Self Model/retrieval/prediction state; не canonical enum value | Гипотезу с confidence и audit refs; не canonical fact |

### Непереговорное правило inference

`model_inference` **никогда автоматически не становится canonical fact** — даже
если confidence высок, evidence повторяется, LLM сформулировала claim уверенно
или prediction совпала с выбором. Чтобы claim стал пользовательским знанием,
пользователь должен явно подтвердить его через reviewed capture; тогда в vault
появляется новая `explicit_user_fact` или `user_statement`, а исходный
inference остаётся отдельным derived артефактом или исчезает при rebuild.

Нельзя смешивать в одном поле формулировку пользователя и формулировку модели.
Если note содержит текст «мне кажется, я выбираю X», это canonical
`evidence_kind: user_statement` с подходящим `self_kind`; если система выводит
«пользователь обычно выбирает X», это `model_inference` с отдельными supporting
references.

Если у старой note отсутствует exact marker, это не разрешение приложению
угадывать enrollment по body или metadata. Такая note остаётся canonical для
vault и обычного Search/Retrieval, даже если в ней случайно уже есть одно или
несколько совпадающих имён полей; typed Cognitive Twin evidence появляется
только через reviewed capture, который явно пишет marker.

## 6. Каноническая taxonomy и metadata verdict

### 6.1. Нужна ли большая taxonomy

- **ACCEPT:** две маленькие независимые scalar axes (`evidence_kind` и
  `self_kind`) позволяют не смешивать provenance с semantic subject и не
  требуют отдельного graph schema.
- **CHANGE:** `evidence_kind` должен быть bounded allowlist provenance classes,
  `self_kind` — bounded semantic label, а `domain` — bounded slug без
  обязательного глобального справочника.
- **RISK:** большая taxonomy с подтипами, facets, ontology и domain registry
  быстро станет вторым schema contract, который нужно мигрировать вместе с
  vault.
- **DEFER:** иерархию типов, multi-domain, controlled vocabulary доменов,
  evidence subtypes и автоматическую классификацию оставляем будущим стадиям.

Итог: отдельную canonical taxonomy ради taxonomy не создаём. На первом этапе
достаточно exact per-note enrollment marker, двух bounded axes и optional
`domain`; evidence kind больше не живёт только в derived interpretation body.

### 6.2. Verdict по enrollment marker, `evidence_kind`, `self_kind` и `domain`

Предлагаемые additive fields:

```yaml
second_brain_personal_memory: 1
evidence_kind: user_statement
self_kind: preference
domain: career
```

`second_brain_personal_memory: 1` — exact project-namespaced scalar marker
enrollment для Personal Memory Contract v1. Это per-note opt-in, а не global
`schema_version`; его эмитит только будущий reviewed Personal Memory Safe Write
path. Exact marker означает YAML scalar numeric `1`; отсутствие marker, строка
`"1"`, `true`, `1.0` и любое другое значение не включают Personal Memory
validation.

`evidence_kind` — ровно четыре значения, перечисленные в разделе 5. Оно
обязательно для нового enrolled Personal Memory record. `self_kind`, temporal
fields и optional `domain` имеют смысл Personal Memory только в той же exact
enrollment boundary; отсутствие marker не классифицируется автоматически.

`self_kind` — один из минимального полного набора:
`memory`, `preference`, `belief`, `goal`, `decision`, `outcome`. Это label
semantic subject, а не утверждение, что содержание истинно. В Stage 1 реально
разрешаются только `memory`, `preference`, `belief`, `goal`; `decision` и
`outcome` резервируются за Stage 2 вместе с более строгим Decision Journal /
outcome contract. Так Stage 1 не создаёт records, которые Stage 2 может
ошибочно принять за полноценный Journal.

`domain` — optional один lowercase ASCII slug длиной не более 64 bytes, без
пробелов, `/`, `\\`, control characters и YAML collection. `career` — пример,
но не начало обязательного domain registry. Значения вроде `career/backend`
или массивы доменов в v1 не принимаются.

Verdict: marker и companion fields — additive per-note metadata; старые notes
не переклассифицируются и остаются valid. Только exact marker включает closed
Personal Memory validator и делает обязательными Stage 1 `evidence_kind`,
совместимый `self_kind`, `evidence_at` и согласованную precision; `domain`
остаётся optional. Stage 2 добавляет event evidence/self kinds и stricter body
contract также только для явно enrolled reviewed records, без превращения их в
inference.

Не добавляем в canonical front matter `confidence`, `supporting_evidence`,
`contradicting_evidence`, `generated_at`, `embedding`, `model_version`,
`stale`, `superseded` или `calibration`. Это derived/model metadata и они
создали бы ложное впечатление, что модельный snapshot является пользовательским
источником.

### 6.3. Schema version verdict

`schema_version` bump не нужен: marker и companion fields — additive scalar
metadata, а global `schema_version` остаётся `1`. Текущая policy уже сохраняет
unknown front matter fields при round-trip write; следующая
implementation-задача сначала проверяет exact marker и только затем применяет
явную валидацию `evidence_kind`, `self_kind`, `domain`, `evidence_at` и
`evidence_at_precision`. Без marker эти имена не становятся частью Personal
Memory Contract и не дают новых diagnostics.

Schema bump потребовался бы только при изменении обязательных полей, смысла
существующих полей, shape существующего `sources`/`tags`/`links` или при
необратимой миграции старых notes. Ни один из этих случаев не нужен.

## 7. Temporal semantics

Существующие `created` и `updated` — storage lifecycle timestamps. Они не должны
молча использоваться как время события, решения, assertion или начала
preference.

### Минимальная canonical temporal foundation

Выбор для v1 — **A: небольшой additive canonical metadata contract**. Foundation
нужен уже в Stage 1, потому что даже generic fact/statement должен иметь
deterministic assertion time или явно сохранённое `unknown`, если он позже
попадёт в Timeline.

Новые enrolled records получают два scalar fields:

```yaml
evidence_at: "2026-09-05T12:00:00+03:00"
evidence_at_precision: exact
```

Допустим также явный неизвестный момент:

```yaml
evidence_at: unknown
evidence_at_precision: unknown
```

В v1 разрешены только `exact` и `unknown`; approximate `day`, `month` и
`period` откладываются. Смысл `evidence_at` определяется canonical
`evidence_kind`:

| `evidence_kind` | Смысл `evidence_at` |
| --- | --- |
| `explicit_user_fact` / `user_statement` | Когда пользователь это сообщил (`asserted_at`) |
| `observed_decision` | Когда пользователь сделал/зафиксировал выбор (`decision_at`) |
| `outcome_later_observation` | Когда был замечен outcome (`outcome_at`) |

Validator требует согласованную пару: RFC 3339 с явным offset + `exact` либо
`unknown` + `unknown`. LLM не угадывает дату, body не сканируется heuristically,
а `created` никогда не используется как fallback. Более богатая precision и
validity interval остаются будущим additive extension.

Эти temporal fields интерпретируются и валидируются как Cognitive Twin state
только для note с exact `second_brain_personal_memory: 1`. В legacy note без
marker даже совпадающие `evidence_at`/`evidence_at_precision` остаются unknown
front matter и не превращают storage note в timeline evidence.

В будущем temporal context должен различать:

- **asserted at** — когда пользователь сообщил assertion;
- **observed/event at** — когда произошло decision или outcome;
- **valid from / valid until** — период применимости preference, belief, goal
  или rule; интервал трактуется как `[from, until)`;
- **generated at** — когда derived claim/retrieval/prediction был пересчитан;
- **storage created/updated** — когда note была создана или изменена в vault.

Все машинные timestamps используют существующее правило RFC 3339 с явным UTC
offset. Неизвестное время остаётся неизвестным; его нельзя подменять
`created`.

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
preference могут быть одновременно валидны.

## 8. Personal Memory Contract v1 — proposal

Personal Memory Contract — это минимальное соглашение о том, как reviewed
пользовательское знание попадает в существующий vault. Это не отдельная БД и не
ontology для всех будущих claims.

### Семантика

1. Canonical memory — это существующая managed Markdown note со stable UUIDv7.
2. Note становится enrolled typed evidence только при exact
   `second_brain_personal_memory: 1`, пользовательском review и Safe Write.
   Marker эмитит только этот future reviewed write path; приложение не
   угадывает enrollment по body или companion fields.
3. `evidence_kind` отвечает за provenance/class, `self_kind` — за semantic
   subject, `domain` — за optional context; они не добавляют truth/confidence
   semantics и не интерпретируются без marker.
4. `evidence_at` и `evidence_at_precision` — единственный Stage 1 machine
   source для времени evidence enrolled record; narrative dates в body не
   заменяют их.
5. Human-readable meaning, context, reasons и uncertainty остаются в body, где
   пользователь может их прочитать и исправить.
6. Derived evidence refs всегда ссылаются на UUID canonical note; они не
   копируют body в отдельное authoritative storage.
7. Удаление индекса или Self Model не требует восстановления из него
   пользовательских данных.

### Минимальная форма

Существующие обязательные поля не меняются:

```yaml
---
id: 019...
type: zettel
created: 2026-09-05T12:00:00+03:00
updated: 2026-09-05T12:00:00+03:00
second_brain_personal_memory: 1
self_kind: preference
evidence_kind: user_statement
evidence_at: "2026-09-05T12:00:00+03:00"
evidence_at_precision: exact
domain: career
tags: []
links: []
---
```

Exact `second_brain_personal_memory: 1`, `evidence_kind`, `self_kind`,
`evidence_at` и `evidence_at_precision` обязательны для нового enrolled Stage 1
Personal Memory record; `domain` optional. Note без exact marker, включая note с
coincidentally named fields, остаётся valid ordinary managed note: fields не
валидируются как Personal Memory, не получают Cognitive Twin semantics и не
создают diagnostics только из-за shape/value. `type` остаётся существующим
`NoteType`, а не `personal_memory`.

Минимальный body должен быть читаемым без приложения. В зависимости от
`self_kind` достаточно следующих смысловых секций:

```markdown
## Assertion

Что пользователь сообщает, считает, хочет или вспоминает.

## Context

К каким обстоятельствам это относится.

## Time context

Человеческое пояснение к canonical `evidence_at`; machine-readable timestamp и
precision находятся в YAML. При неизвестном времени здесь явно написано, что
оно неизвестно.

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
| Personal Memory enrollment | Да, exact `second_brain_personal_memory: 1` | Не выводится из body |
| Provenance class `evidence_kind` | Да, bounded scalar только после enrollment | Не выводится из body |
| Узкий semantic label `self_kind` | Да, bounded scalar только после enrollment | Можно повторить словами, но не нужно |
| Один bounded `domain` | Да, optional только после enrollment | Можно описать контекст подробнее |
| Evidence timestamp и precision | Да, `evidence_at` + `evidence_at_precision` только после enrollment | Можно дать human explanation |
| Ситуация, варианты, причины, criteria, expected/actual result | Нет | Да |
| Human wording assertion и uncertainty | Нет | Да |
| Дополнительные temporal contexts одной decision | Нет в Stage 1 | Да, narrative only; typed extensions — позже |
| Supporting/contradicting evidence, confidence, stale status, generated time | Нет | Не canonical body автоматически; это derived DTO |

Не строим YAML ontology ради индекса. Metadata остаётся только там,
где она нужна для стабильной identity/маршрутизации и bounded validation.

## 9. Decision Journal v1 — proposal

Decision Journal — canonical human-readable record одной существенной decision,
а не лог всех кликов и не inferred personality profile. В Stage 2 это
явно enrolled существующая managed note с
`second_brain_personal_memory: 1`, `evidence_kind: observed_decision`,
`self_kind: decision`, обязательным `evidence_at` (точное время decision либо
явный `unknown`), optional `domain` и stricter structured Markdown body.

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
машинно typed evidence поздний результат фиксируется отдельной reviewed note с
`evidence_kind: outcome_later_observation`, `self_kind: outcome` и своим
`evidence_at` как observed time; note связывается с Decision Journal через
existing `links`/UUID-aware evidence relation. Journal может содержать
human-readable summary или ссылку на outcome, но body update сам по себе не
переклассифицируется приложением. Точный update use case — отдельная
implementation boundary, не часть #66.

Таким образом, `decision_at` хранится как `evidence_at` в Journal note, а
`outcome_at` — как `evidence_at` в linked outcome observation. Оба значения
могут быть явно `unknown`; storage `created` не используется вместо них.

Важное разделение:

- decision journal фиксирует выбор и reasoning пользователя;
- Self Model позже может вывести pattern из нескольких журналов;
- один journal не подтверждает permanent preference или decision rule;
- actual choice для calibration должен быть canonical/reviewed, а не только
  inferred из системной telemetry.

## 10. Personal Timeline v1 — derived/read model

Personal Timeline — это read model текущих enrolled typed canonical notes, а не
новый event store. Legacy notes без exact Personal Memory marker не становятся
Timeline evidence только из-за совпадающих front matter names. Timeline
собирает события и assertions через deterministic mapping
`evidence_kind -> evidence_at`, без LLM date extraction:

```text
canonical notes -> evidence extraction -> timeline items -> sorted read model
```

Концептуальный `TimelineItem` содержит:

- `event_kind` (`decision`, `goal`, `statement`, `outcome` или другой узкий
  reviewed kind), выведенный из canonical evidence/self axes;
- `event_at = evidence_at` и `precision = evidence_at_precision`, либо явную
  отметку `unknown`;
- короткий summary без подмены полного body;
- supporting canonical note UUIDs;
- derived status (`current`, `stale`, `conflicted`, `superseded`, если применимо);
- `generated_at` read model.

Сначала timeline пересобирается on demand из `FileSystemVaultReader ->
build_report`. Persistent timeline DB, watcher и incremental event sourcing не
нужны. Если `evidence_at: unknown`, item сохраняет `event_at = unknown` и
попадает в отдельную неопределённую группу/порядок. Storage `created` никогда
не используется как время события.

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
  status: optional future seam; always None in #82
  generated_at
  derivation_version
```

Roadmap values `current`, `stale`, `conflicted`, `superseded` и `unresolved`
не используются в Self Model v1. Точная форма, owner-approved policy binding
и decision memo находятся в
[self-model-v1-contract.md](self-model-v1-contract.md).

`category / dimension` enum может содержать `preference`, `belief`, `goal`,
`decision_rule`, `behavioral_pattern` для future compatibility, но #82 emit
только direct `preference`, `belief` и `goal` claims. Список dimensions не
становится YAML ontology в #66.

`confidence` — envelope именно derived claim, в v1 explicitly unassessed, а не
факт и не пользовательская уверенность из Decision Journal. Для audit claim
обязан быть able to answer:
«какие current notes его поддерживают и есть ли explicitly approved conflict
relation?» Отсутствие supporting refs делает claim недопустимым для personal
context; automatic contradiction в v1 отсутствует.

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

Approved bounded v1 contract для этой границы находится в
[self-retrieval-v1-contract.md](self-retrieval-v1-contract.md). Следующий
параграф сохраняет общую roadmap rationale; normative DTO, bounds, error
taxonomy и build sequence определяются contract document.

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

Историческая rationale ниже описывает semantic filtering/evidence assembly
после этой границы; текущий merged Self Retrieval contract и его exact DTO
остаются нормативными:

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
| `Simulate Me` | Prediction того, что пользователь вероятнее всего выбрал бы сам сейчас | Predicted choice, typed evidence refs и temporal caveats либо abstention | Не называет prediction объективно лучшим решением |
| `Compare` | Одновременная композиция двух независимых outputs | Assistant zone + Simulate Me zone + structural Delta | Не схлопывает disagreement, evidence или prediction в один ответ |

Independent `Assistant` branch не получает уже готовый predicted choice как
скрытую подсказку и не обучается на результате `Simulate Me` внутри того же
запроса. Если personal context нужен для анализа, он подаётся как явно
помеченная evidence, а criteria независимой рекомендации остаются видимыми.

`Compare` v1 должен показать только typed branch states, exact request-local
option-ID relation и branch-local evidence shapes. Preference против stated goal,
stale/fresh interpretation, competing meanings одного набора notes, confidence,
behavioral explanation и growth-optimal semantic reasoning не входят в
mechanical Delta и остаются deferred.

Это anti-echo-chamber boundary: «похоже на прошлое поведение» не означает
«надо так же советовать», а «independent recommendation отличается» не означает
«модель ошиблась».

## 15. Prediction & Calibration v1

В v1 calibration — **только retrospective/rebuildable**. ML training pipeline
не нужен. Нормативные masking, cutoff, current-vault limitation и aggregate
metrics зафиксированы в
[retrospective-calibration-v1-contract.md](retrospective-calibration-v1-contract.md).
Нужен lifecycle:

```text
current canonical Decision Journal
  -> current-vault temporal projection (not a historical snapshot)
  -> current Simulate Me derivation without chosen option/outcome
  -> predicted choice or bounded abstention
  -> compare with canonical observed decision
  -> rebuildable calibration aggregate
```

Для каждого eligible Journal строится bounded current-vault pre-choice
projection (не historical snapshot) из:

- `Situation`;
- `Available options`;
- `Information known at decision time`;
- `Criteria`;
- canonical evidence с known exact `evidence_at`, не позже decision; unknown,
  later и post-cutoff-edited evidence исключаются с caveat.

В Simulate Me input **не входят** `Chosen option`, `Reasons`, `Expected result`,
`Actual result`, `Reassessment`, поздние notes или сам expected answer. Это
explicit anti-leakage boundary. После replay predicted choice сравнивается с
canonical `observed_decision` choice из Journal. Поэтому calibration отвечает
на вопрос: «насколько текущая версия Cognitive Twin моделирует исторические
решения пользователя?», а не «насколько система когда-то предсказала их в
production».

Derived replay state существует только во время rebuild и содержит:

- validated predicted choice или abstention;
- current-vault reconstruction mode и decision cutoff;
- bounded internal refs только до завершения case, без публикации UUID/body;
- exact `derivation_version` и policy fingerprint.

Actual choice берётся только после replay из reviewed `observed_decision`.
Если Journal не проходит exact eligibility, sample исключается с fixed code, а
не получает неправильный prediction. Valid no-match/multiple-support остаются
bounded Simulate Me abstention; unavailable и invalid replay не маскируются под
mismatch.

Calibration v1 показывает только bounded counts, exact option match/mismatch,
predicted/abstained/unavailable/invalid, coverage и exact non-abstained
accuracy numerator/denominator. Confidence, probability, Brier/ECE, bins,
calibration gap, tuning и small-sample personal trait claims не входят.

Retrospective replay и calibration полностью derived: удаление derived state не
теряет никаких данных, а aggregate снова вычисляется из current vault той же
или новой derivation policy. При смене `derivation_version` результат честно
является метрикой новой версии; current-vault projection не притворяется
historical snapshot или production prediction.

Prospective extension (`prediction now -> actual choice later`) в v1 не входит.
Чтобы такой режим когда-либо имел rebuildable historical calibration, сначала
потребуется policy-governed immutable canonical audit record факта операции:

```text
system predicted X at T with confidence C using derivation V
```

Это не user fact, не `evidence_kind`, не подтверждение inference и не claim о
пользователе. Такой audit record нельзя подменить текущим Self Model после
удаления derived state. Его storage, retention и privacy policy потребуют
отдельного design; новую DB/event store для этого сейчас не добавляем.

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
| Personal Memory enrollment | Exact per-note `second_brain_personal_memory: 1` даёт явную authority boundary | Не выводить enrollment из coincidental field names или body | Legacy unknown metadata может выглядеть знакомо | Migration/classification без reviewed opt-in |
| Отдельная canonical taxonomy | Минимальный marker и routing labels полезны | `evidence_kind`/`self_kind` сделать bounded scalar только внутри enrollment | Большая ontology станет миграционным контрактом | Nested taxonomy, facets и domain registry |
| `self_kind` | `memory/preference/belief/goal/decision/outcome` достаточно для первой маршрутизации enrolled notes | Не трактовать label как truth или evidence kind; Stage 1 ограничить allowlist | Один label может не описать смешанную note | Multi-label и custom kinds |
| `domain` | Один bounded slug помогает фильтрации enrolled notes | Не вводить обязательный vocabulary и не валидировать legacy arrays | Свободные значения могут расползтись | Taxonomy/aliases/registry |
| Timestamps | Existing storage times сохраняем | Не использовать `created` как event time | Ложная точность разрушит timeline | Общие `valid_from/until` metadata до доказанной потребности |
| Outcome/reassessment | Хранить рядом с Decision Journal для audit | Для длинных историй разрешить linked observation notes | Mutation semantics Safe Write ещё не определена | Отдельный outcome event store |
| Superseded preferences | Сохранять старые assertions и derived relation | Ограничивать relation контекстом и временем | Автоматический supersede может стереть nuance | Global identity merge |
| Assertion против inference | Разделять evidence kinds строго | Confirmation создаёт новую assertion | LLM может звучать как факт | Automatic inference write-back |
| Confidence | Нужен в derived claim и prediction | Не класть в canonical metadata | Число может создать ложную объективность | ML calibration training |
| Contradiction | Показывать supporting и contradicting refs | Не голосовать большинством без context | False conflict при разных domains/horizons | Автоматический conflict resolver |
| Derived Self Model storage | На старте rebuild-on-demand | Cache только disposable и versioned | Persistent state может стать скрытым source of truth | Durable model DB |
| Embeddings | Возможны только после измеренной lexical gap | Сначала текущий Search/Retrieval | Privacy, deletion и stale vectors | Provider/vector DB/RAG |

## 20. Revised roadmap: Cognitive Twin v1 Stages 1–8 + Stage 9 v2

Последовательность сохраняется: каждая стадия зависит от canonical semantics
предыдущей. Перестановка не нужна.

### Stage 1 — Personal Memory Contract v1

- **Цель:** добавить минимальный reviewed contract с exact per-note enrollment
  marker и двумя независимыми axes: provenance (`evidence_kind`) и semantic
  subject (`self_kind`), не создавая новую persistence model.
- **Входные зависимости:** UUIDv7 `NoteRecord`, front matter round-trip,
  `FileSystemVaultReader -> build_report` с marker gating, Safe Write и текущие
  bounded draft boundaries.
- **Canonical changes:** exact `second_brain_personal_memory: 1` плюс
  `evidence_kind`, `self_kind`, `domain`, `evidence_at` и
  `evidence_at_precision`; старые notes валидны без marker и companion fields.
  Новый Stage 1 Personal Memory draft обязан иметь exact marker,
  `evidence_kind`, Stage 1-compatible `self_kind` и explicit exact/unknown
  evidence time.
- **Stage 1 allowlist:** `evidence_kind` — только `explicit_user_fact` или
  `user_statement`; `self_kind` — только `memory`, `preference`, `belief` или
  `goal`. `observed_decision`, `outcome_later_observation`, `decision` и
  `outcome` не создаются на этой стадии.
- **Derived state:** только чтение/валидация; Self Model, graph и embeddings
  отсутствуют.
- **Public/application contracts:** отдельный reviewed personal-memory wrapper
  поверх существующего `NoteDraft` допустим в следующей implementation task;
  `NoteDraft`, Search, LLM и transcription contracts не переопределяются.
- **Risks:** преждевременная taxonomy, arbitrary YAML, implicit evidence
  classification/enrollment и accidental inference write-back.
- **Explicit out-of-scope:** `decision/outcome` self semantics, Decision Journal
  runtime, Journal outcome links, validity intervals, timeline, Self Model,
  retrieval modes, updates existing notes.
- **Acceptance boundary:** новые records имеют exact reviewed enrollment,
  explicit evidence class и exact/unknown `evidence_at`; старые notes остаются
  valid ordinary notes независимо от coincidental field names; Stage 1 не может
  создать record, который выглядит как полноценный Decision Journal.

### Stage 2 — Decision Journal v1

- **Цель:** canonical, reviewed и human-readable запись выбора с expected и
  later outcome.
- **Входные зависимости:** Stage 1 typed evidence metadata, existing Safe Write,
  reviewed Text/URL/Voice capture и deterministic `evidence_at` foundation.
- **Canonical changes:** explicitly enrolled Decision Journal note с
  `second_brain_personal_memory: 1`, `evidence_kind: observed_decision`,
  `self_kind: decision`, `evidence_at` как decision time и stricter body.
  Поздний результат — отдельная linked note с
  `evidence_kind: outcome_later_observation`, `self_kind: outcome` и своим
  `evidence_at` как outcome time; Journal может содержать reviewed summary/link.
- **Derived state:** optional extraction candidates, не canonical; никакой
  automatic journal completion.
- **Public/application contracts:** отдельный Journal draft/use case и позже
  reviewed update boundary; current `NoteDraft` остаётся semantic base.
- **Risks:** backfilling information, которого не было известно при decision,
  путаница user confidence и model confidence, implicit outcome promotion.
- **Explicit out-of-scope:** automatic action telemetry, personality profile,
  validity intervals, Timeline runtime, retrospective calibration и
  recommendation.
- **Acceptance boundary:** пользователь видит и подтверждает все journal
  sections; `evidence_at` не выводится из `created`; expected/actual и
  decision/outcome evidence остаются различимыми.

### Stage 3 — Personal Timeline v1

- **Цель:** derived chronological view canonical assertions, decisions и
  outcomes.
- **Входные зависимости:** enrolled Stage 1 `evidence_at` для facts/statements,
  Stage 2 deterministic decision/outcome records, current scan/report.
- **Canonical changes:** нет; только reviewed notes из предыдущих стадий.
- **Derived state:** `TimelineItem` через deterministic
  `event_at = evidence_at` и `precision = evidence_at_precision`, UUID refs,
  status и `generated_at`; on-demand first.
- **Public/application contracts:** read-only bounded Timeline query/result;
  no new event-store write port.
- **Risks:** принять storage time за event time, схлопнуть conflicting dates,
  потерять explicit unknown precision.
- **Explicit out-of-scope:** background watcher, event sourcing, timeline DB и
  automatic date/LLM extraction, approximate precision и fallback к `created`.
- **Acceptance boundary:** timeline честно различает event time, storage time
  и unknown; rebuild из vault даёт current result без heuristic date guessing.

### Stage 4 — Self Model v1

- **Статус:** merged Self Model core и local read-only Web projection. Точный
  owner-approved conservative direct-assertion policy и application contract
  зафиксированы в [self-model-v1-contract.md](self-model-v1-contract.md).
- **Цель:** построить evidence-backed direct assertion claims о reviewed
  preferences, beliefs и goals; behavioral patterns и decision rules deferred.
- **Входные зависимости:** Stage 1 evidence semantics, Stage 2 journal и
  Stage 3 temporal read model.
- **Canonical changes:** нет; inferred claims не пишутся в vault.
- **Derived state:** `SelfModelClaim` с category, deterministic body projection,
  pairwise-disjoint support/contradict/context UUIDs, explicitly unassessed
  confidence, temporal context, optional `None` status и generated time;
  result carries complete policy fingerprint.
- **Public/application contracts:** bounded read-only Self Model DTO с
  explainability refs; no generic profile write API.
- **Risks:** misleading claim wording, future stale/contradictory semantics,
  diagnosis-like language, false confidence; v1 fails closed and does not
  infer these states.
- **Explicit out-of-scope:** automatic profile mutation, LLM fact insertion,
  psychological diagnosis, durable model DB.
- **Acceptance boundary:** каждый claim объясним current evidence и disappears
  safely after derived-state deletion/rebuild.

### Stage 5 — Self Retrieval v1

- **Статус:** core, local Web и CLI projections из #89/#90/#91 merged в
  current `main`; approved normative contract issue #88 остаётся источником
  границ и DTO.
  Normative DTO, current-reread rules, bounds, safe errors и test matrix — в
  [self-retrieval-v1-contract.md](self-retrieval-v1-contract.md).
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

- **Статус:** approved mechanical design и provider-free application/Web
  runtime/core; provider, confidence и persistence отсутствуют.
  Нормативный contract находится в
  [simulate-me-v1-contract.md](simulate-me-v1-contract.md),
  `HUMAN_REQUIRED = none`.
- **Цель:** predict likely user choice from current evidence, without calling it
  recommendation.
- **Входные зависимости:** Stage 4 current direct Self Model dimensions and
  Stage 5 current-UUID context; historical Decision Journal не является
  selection authority.
- **Canonical changes:** none; actual choices remain canonical only when
  reviewed, prediction itself is never canonical.
- **Derived state:** prediction или deterministic abstention, bounded evidence
  refs, temporal caveats, exact derivation version и policy fingerprint; numeric
  confidence отсутствует.
- **Public/application contracts:** caller-owned bounded `{id,label}` options;
  exact whole-label matching; result только `prediction` или `abstention`.
- **Risks:** hidden semantic matching, recommendation leakage, stale/conflict
  inference и provider/persistence expansion.
- **Explicit out-of-scope:** aliases/synonyms/fuzzy matching, confidence,
  ranking, recency, recommendation, Compare, calibration, background
  predictions и canonical write-back.
- **Acceptance boundary:** response явно labelled **ПРОГНОЗ** либо abstention;
  `preference`/`goal` могут выбрать ровно один distinct option, а missing или
  ambiguous evidence даёт bounded abstention; canonical write отсутствует.

### Stage 7 — Assistant v1; Compare v1 + Prediction & Calibration v1

- **Статус:** Assistant v1 design-only contract зафиксирован в
  [assistant-v1-contract.md](assistant-v1-contract.md), Compare v1 mechanical
  composition contract — в [compare-v1-contract.md](compare-v1-contract.md), а retrospective
   calibration design-only contract — в
   [retrospective-calibration-v1-contract.md](retrospective-calibration-v1-contract.md).
  Assistant задаёт отдельную
  explicit-context-only independent recommendation / analysis branch и не
  принимает Simulate Me prediction как input. Compare сохраняет оба typed
  wrappers и structural Delta по exact request-local option IDs. Owner decision A
  принят; `HUMAN_REQUIRED: none` для capability-boundary, а automatic Personal
  Memory, provider/network/privacy integration и runtime остаются отдельными
  future gates. В #171 реализуется provider-free Assistant application core:
  typed explicit-only DTO/validation и отдельная `AdvisorPort` boundary без
  provider call, private context или persistence. В #173 реализуется
  provider-free Compare application core: typed branch wrappers, shared
  execution controls и structural Delta без provider call, private context или
  persistence. В #175 реализован provider-free Retrospective Calibration
  application core: bounded pre-choice replay, filtered current context и
  deterministic aggregate; user-facing Web/API/CLI, provider runtime и
  persistence не входят в этот slice.
- **Цель:** сначала определить независимый bounded advice contract, затем в
  будущем сопоставить его с likely user choice и измерить, насколько текущая
  derivation policy воспроизводит исторические decisions.
- **Входные зависимости:** caller-owned Assistant request с explicit task,
  options, constraints, goals и context; Stage 6 Simulate Me derivation и,
  для Calibration, canonical Stage 2 Decision Journal с pre-choice
  information и actual `observed_decision`. Compare получает только
  caller-owned branch inputs и не читает Decision Journal. Automatic Stage
  5/Personal Memory context в Assistant v1 не входит.
- **Canonical changes:** only user-reviewed actual decisions/outcomes; no
  calibration fields or prediction history in user notes.
- **Derived state:** в будущем две independent outputs, structural comparisons,
  retrospective pre-choice predictions и rebuildable calibration aggregates with
  bounded counts/coverage; Assistant result сам остаётся ephemeral.
- **Public/application contracts:** Compare result always contains both branch
  wrappers и deterministic structural Delta; Assistant result явно labelled
  independent recommendation / analysis; Calibration result reports replay
  sample/unknowns and derivation version.
- **Risks:** recommendation contamination by prediction, actual-choice leakage
  into backtest, outcome selection bias, false precision from tiny sample.
- **Explicit out-of-scope:** Assistant/Compare runtime before отдельной
  implementation/privacy approval, automatic Stage 5/Personal Memory context,
  private-context transmission, ML training pipeline, automatic goal changes,
  prospective calibration, policy-governed prediction audit record,
  optimization against a hidden reward and universal user score.
- **Acceptance boundary:** Assistant receives only caller-explicit inputs and
  never receives prediction or automatic private context; Compare keeps both
  branches and an exact structural Delta without semantic cross-branch
  inference; chosen option, reasons and later outcome are masked from
  historical Simulate Me input; calibration can be deleted and rebuilt from
  current vault without losing user data.

### Stage 8 — Active Personal Learning v1

- **Статус:** **COMPLETE**; нормативный [Active Personal Learning v1 contract](active-personal-learning-v1-contract.md) зафиксирован в #223, provider-free core, Web/API surface и Stage 8 integration QA merged в current `main` и находятся в production.
- **Цель:** optional questions only where evidence is weak, conflicting or
  missing.
- **Входные зависимости:** Stage 4 confidence/conflict, Stage 5 retrieval,
  Stage 7 comparison and optional retrospective calibration diagnostics; no
  prospective prediction history is required.
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

### Stage 9 — Cognitive Twin v2: Prospective Operation Audit & Calibration v1

- **Статус:** **COMPLETE**; normative boundary зафиксирована в
  [prospective-audit-calibration-v1-contract.md](prospective-audit-calibration-v1-contract.md)
  для Issue #236, а Stage 9A/9B/9C и Stage 9D Web/API, integration QA и
  production closeout завершены в staged implementation issues, включая #244.
- **Цель:** зафиксировать validated Simulate Me prediction/abstention до
  решения, durable policy-governed operational audit state, позже explicit
  reviewed Decision Journal linkage и deterministic prospective calibration.
- **Canonical boundary:** audit record является canonical operational/audit
  state вне `second-brain-vault`; он не является Personal Memory, Decision
  Journal, Outcome Observation, Self Model evidence или user fact.
- **Scope v1:** только explicit foreground Simulate Me terminal operations;
  Assistant, Search, Retrieval, Active Learning, generic telemetry и provider
  calls не аудируются.
- **Implementation sequence:** Stage 9A audit store/core, 9B explicit reviewed
  linkage, 9C prospective aggregate и 9D Web/API + integration QA выполнены.
  События и связи остаются в verified operational store; user-facing UI не
  меняет Journal schema и не выполняет automatic linkage.
- **Explicit out-of-scope:** new env keys, systemd changes, backup/cloud export,
  automatic linkage, confidence/probability, ML/tuning, providers, new
  dependencies, `second-brain-vault` changes и Stage 10+.

### Stage 10 — Cognitive Twin v2: Behavioral Self Model и explicit mapping

- **Статус:** Stage 10A Behavioral Observation/Cohort, Stage 10B Behavioral
  Self Model, Stage 10C runtime и Stage 10D Web/API с integration QA/closeout
  завершены в current `main` после Issue #256.
- **Цель:** сохранить раздельные Stated и Behavioral layers и определить
  только explicit owner-reviewed relation между current Stage 4 direct
  `preference` assertion и exact current Stage 10 cohort/option.
- **Canonical boundary:** vault `scan -> build_report` остаётся authority для
  user evidence; Stage 4 и Stage 10A/10B остаются derived/read-only. Stage
  10C0 не добавляет vault fields, note kinds, Safe Write, schema или provider.
- **Design gate:** [stated-observed-mapping-v1-contract.md](stated-observed-mapping-v1-contract.md)
  фиксирует preference-only exact identity, injective cardinality, explicit
  review, fail-closed drift/correction, bounded dedicated operational
  persistence вне vault и Stage 9, а также closed composition states.
- **Implementation boundary:** Stage 10C реализует validators, current
  rebuild/review orchestration, dedicated mapping store и deterministic
  composition; Stage 10D закрывает bounded Web/API, owner UI и integration
  QA/closeout. Следующий Stage 11 отдельно не начинать автоматически.
- **Explicit out-of-scope:** fuzzy/semantic/LLM/embedding matching,
  automatic mapping, belief/goal mapping, many-to-many/history semantics,
  Stage 9/Simulate Me consumers, provider/network/credentials, canonical
  schema/vault changes и создание следующего Issue.

### Stage 11 — Cognitive Twin v2: Growth Engine v1

- **Статус:** **DESIGN CONTRACTS** в
  [growth-engine-v1-contract.md](growth-engine-v1-contract.md) и
  [growth-learning-v1-contract.md](growth-learning-v1-contract.md); Stage 11A
  deterministic current Goal identity/context core реализован в Issue #260,
  Stage 11B Goal-to-choice relation/friction read model реализован в Issue
  #262/PR #263, Stage 11C0 privacy/payload/provenance contract зафиксирован в
  [growth-advisor-v1-contract.md](growth-advisor-v1-contract.md), а Stage 11C
  Growth Advisor runtime реализован в PR #268. Stage 11D0 Growth Learning /
  Question contract зафиксирован в Issue #269, а bounded Learning runtime и
  owner Web/API/UI реализованы в Issue #274.
- **Цель:** связать current reviewed Stage 4 goal с current exact behavioral
  choice только через отдельную explicit owner-reviewed Goal-to-choice
  relation; не определять «настоящую цель», optimality, personality или
  progress.
- **Authority boundary:** Goal, observed behavior, likely self-choice и
  independent recommendation остаются независимыми слоями. Stage 10C
  preference mapping не переиспользуется как Goal mapping.
- **Implementation boundary:** merged Stage 11A сохраняет current scan →
  `build_report` → existing `BuildSelfModel` → direct Goal identities и
  explicit selection; merged Stage 11B добавляет только explicit relation,
  mapping store, current Stage 10 reference и friction read model. Stage 11C
  runtime добавляет только explicit Advisor preview/confirmation/revalidation
  flow по отдельной policy boundary. Stage 11D/11E добавляют только
  provider-free Learning question flow, owner Web/API/UI, integration QA и
  closeout по отдельным policy boundaries.
- **Explicit out-of-scope:** Learning store/history beyond page/request memory,
  Advisor/provider chaining, semantic mapping, schema/vault/Safe Write mutation,
  Goal Progress, Stage 9, Stage 10 и Simulate Me changes.
  Следующий Issue автоматически не создаётся.

# COMPLETED IMPLEMENTATION SCOPE (HISTORICAL):

## Personal Memory Contract v1 (Stage 1, completed)

Этот раздел сохраняет исходный implementation contract Stage 1 для истории.
Stage 1, Stage 2 и Stage 3 уже находятся в current main; этот раздел не задаёт
новую работу и не должен использоваться как trigger следующей задачи.

### Минимальные semantics

1. Ввести понятие reviewed Personal Memory note поверх уже существующей managed
   note.
2. Разрешить exact per-note marker `second_brain_personal_memory: 1` и пять
   companion scalar fields: `evidence_kind`, `self_kind`, optional `domain`,
   `evidence_at` и `evidence_at_precision`.
3. Для нового Stage 1 Personal Memory input требовать exact marker,
   `evidence_kind: explicit_user_fact | user_statement`,
   `self_kind: memory | preference | belief | goal` и
   `evidence_at: <RFC3339 with offset> | unknown` с согласованной precision.
4. `observed_decision`, `outcome_later_observation`, `decision` и `outcome` не
   входят в Stage 1 allowlist; они появляются только в Stage 2 вместе со
   stricter Decision Journal/outcome contract.
5. Ограничить `domain` одним lowercase ASCII slug до 64 bytes; domain registry
   не вводить.
6. Считать body canonical только после user review и existing Safe Write.
7. Не интерпретировать ни одну canonical field как confidence, truth или
   inferred profile; `model_inference` не допускается как `evidence_kind`.

### Файлы и слои

Минимальный будущий change set должен ограничиться следующими слоями:

- `src/second_brain/domain/models.py` — маленькие typed values/enums для
  Stage 1 `EvidenceKind`, `SelfKind` и bounded temporal precision, если они
  нужны для общего domain validation; новый `NoteType` не добавлять.
- `src/second_brain/application/validation.py` — в `build_report` сначала
  проверять exact enrollment marker; только для enrolled notes проверять
  companion fields и согласованную RFC3339/`unknown` temporal pair, сохраняя
  существующее поведение для notes без marker.
- `src/second_brain/application/personal_memory.py` — новый reviewed DTO и
  bounded use case/wrapper, не меняющий смысл `NoteDraft` и не создающий
  Decision Journal records.
- `src/second_brain/application/ports.py`, `writes.py` и
  `src/second_brain/adapters/vault/writer.py` — только additive dedicated path
  для reviewed Personal Memory plan, который единственным scoped path пишет
  exact enrollment marker и переиспользует существующие manifest,
  dry-run, diff, no-overwrite, post-write validation, receipt и rollback. Не
  превращать generic Safe Write в произвольный metadata map.
- `tests/test_scanner.py`, `tests/test_writes.py` и узкий Search regression
  test — только проверки новой metadata boundary и совместимости.
- `docs/architecture/vault-contract-v1.md` — обновить контракт после
  реализации, если review согласует fields и validation.

`NoteDraft`, `SearchIndexPort`, Search DTO, `TranscriptionPort`, `LlmPort`,
существующие research provenance и public Web/API contracts не менять. Если
добавляется application wrapper, он принимает существующий `NoteDraft` как
внутренний semantic content и добавляет только reviewed Stage 1 Personal Memory
metadata. Stage 2 Decision Journal и outcome records в этот slice не входят.

### Backward compatibility и schema

- `schema_version` остаётся `1`; `second_brain_personal_memory` — не global
  schema/contract version, а project-namespaced per-note enrollment marker.
- Notes без exact marker не мигрируются и продолжают проходить scan, Search и
  Retrieval как раньше, даже если в них уже есть поля с именами
  `evidence_kind`, `self_kind`, `domain`, `evidence_at` или
  `evidence_at_precision`.
- Unknown front matter fields по-прежнему сохраняются round-trip. Без exact
  marker совпадающие fields остаются ordinary unknown metadata: они не
  валидируются Personal Memory validator, не создают diagnostics из-за shape или
  value и не получают Cognitive Twin semantics.
- Только exact scalar `second_brain_personal_memory: 1` включает closed
  Personal Memory validation. Для такой note missing/malformed companion field
  даёт bounded diagnostic и не превращается в silent coercion.
- `evidence_at` принимает только RFC3339 с явным offset или literal `unknown`;
  `evidence_at_precision` согласованно принимает только `exact` или `unknown`.
- Новый path не меняет существующие note roots и не создаёт
  `NoteType.PERSONAL_MEMORY`.

#### `build_report` gating

Будущая реализация должна сохранять deterministic порядок:

1. Прочитать existing front matter и body текущим способом.
2. Проверить, равен ли `second_brain_personal_memory` exact YAML scalar `1`.
3. Если marker отсутствует или имеет любое другое type/value, не запускать
   Personal Memory field validation: note остаётся обычной managed note, все
   совпадающие names сохраняются как unknown metadata, а new diagnostics из-за
   их shape/value не создаются.
4. Если marker exact `1`, применить closed Personal Memory v1 validation и
   потребовать companion fields; missing/malformed fields дают bounded
   diagnostics.

Никакой эвристики по наличию `self_kind`, знакомому `evidence_kind`, body или
domain не допускается. Exact marker и reviewed Safe Write — единственная
   authority boundary enrollment.

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
candidate semantics не меняются. `evidence_kind`/`self_kind`/`domain`/temporal
fields не добавляются в Search DTO и не становятся hidden filter. Existing
Search продолжает находить terms в title/body/tags. Для legacy notes без exact
marker Search/Retrieval semantics остаются полностью прежними; Self Retrieval и
evidence filtering — отдельные Stage 5.

### Validation и tests

Обязательные regression cases будущего slice:

1. **LEGACY:** note без marker с `domain: [work, home]` остаётся valid ordinary
   unknown front matter.
2. **LEGACY:** note без marker с `self_kind: memory` не становится Cognitive
   Twin evidence.
3. **LEGACY:** note без marker с `evidence_kind: user_statement` не становится
   enrolled evidence.
4. **LEGACY:** note без exact marker (включая absent/wrong-type/wrong-value
   marker) со всеми coincidentally named companion fields не получает Personal
   Memory semantics и не получает diagnostics из-за их shape/value.
5. **ENROLLED:** exact `second_brain_personal_memory: 1` с валидными required
   fields проходит Personal Memory v1 validation.
6. **ENROLLED:** exact marker без `evidence_kind` даёт bounded diagnostic.
7. **ENROLLED:** exact marker с invalid `self_kind` даёт bounded diagnostic.
8. **ENROLLED:** exact marker с invalid temporal pair даёт bounded diagnostic.
9. **ENROLLED:** exact marker с valid optional lowercase `domain` принимается.
10. **ENROLLED:** exact marker с invalid `domain` даёт bounded diagnostic.
11. **ROUND-TRIP:** legacy unknown fields сохраняются unchanged.
12. **ROUND-TRIP:** Personal Memory marker и companion fields сохраняются
    unchanged.
13. **SEARCH:** existing Search/Retrieval не меняют semantics для legacy notes.

Также сохраняются проверки allowlists, reject list/map/bool/empty/oversized и
control-character values внутри enrolled contract, reject uppercase/path-like
domain values, YAML round-trip с comments и existing wikilinks, dry-run/no-overwrite,
containment/symlink/rollback invariants, а также отсутствие implicit write
capability у LLM/transcription/Web paths. Во время implementation — targeted
tests, затем один финальный Python 3.14 `ruff format --check`, `ruff check`,
`mypy src tests` и `pytest` согласно `AGENTS.md`.

### Security

- exact `second_brain_personal_memory: 1` — единственный opt-in; marker
  project-namespaced, scalar, user-reviewable и эмитится только reviewed Safe
  Write path;
- сначала проверять enrollment, затем allowlist companion scalar fields; без
  exact marker никаких arbitrary legacy shapes не валидировать как Personal
  Memory mappings;
- bounded UTF-8/ASCII sizes и отказ от control characters;
- `evidence_at` не принимается как непроверенная дата, а `unknown` не заменяется
  storage `created`;
- отсутствие marker не запускает body scan, field-name heuristic, migration или
  retroactive classification;
- никакого network, LLM, credential или background inference в validator/write
  path;
- user review остаётся authority boundary, а inference и transcript считаются
  untrusted candidates;
- test fixtures используют отдельный temporary vault; `second-brain-vault`
  не читается и не меняется;
- safe errors не раскрывают absolute paths, secrets или provider details.

### Что НЕ входит

Не входят Personal Memory Contract v2, Stage 2 `decision/outcome` evidence,
Decision Journal runtime и update workflow, outcome observation records, Timeline,
Self Model, evidence graph persistence, Self Retrieval, RAG, embeddings, vector
DB, Simulate Me, Compare, Calibration, Active Learning, automatic inference
write-back, schema bump, new dependencies, new note type, domain registry,
psychological profiling, live smoke, production deployment, issue creation и
любые изменения `second-brain-vault`.

## Current implementation status after Stage 6 (historical snapshot)

Stage 1–3 core, Stage 4 Self Model core/Web projection и Stage 5
Self Retrieval core/Web/CLI (#89/#90/#91) находятся в current `main`.
Stage 5 сохраняет lexical candidates, current UUID reread, bounded context DTO,
exact supporting UUID links и safe errors; он не добавляет embeddings, RAG,
prediction, inference write-back, canonical fields или изменения
`second-brain-vault`.

Stage 6 contract approved, а provider-free application/Web runtime и core
находятся в current `main`; confidence, provider runtime и persistence
отсутствуют. Assistant v1 explicit-context-only application core (#171),
Compare v1 core (#173) и provider-free Retrospective Calibration core (#175)
реализованы в current `main`. Calibration остаётся bounded current-vault
temporal projection: user-facing Web/API/CLI, provider runtime, persistence и
prospective calibration не входят в этот slice. `HUMAN_REQUIRED: none`
относится только к capability-boundary; новые provider/privacy/schema решения
отложены на отдельные future gates. Этот status sync не объявляет Stage 8
runtime scope, не создаёт Stage 8 item и не меняет product semantics.

## Current status after Stage 11E closeout

Cognitive Twin v1 / Stages 1–8 are **COMPLETE** in current `main` and
production. Cognitive Twin v2 / Stage 9A–9D, Stage 10A–10D and Stage 11A are
**COMPLETE** after their staged implementations and closeouts: Stage 10 adds the bounded
behavioral read model, explicit mapping review/confirmation, dedicated
operational mapping history, deterministic composition and private owner-only
Web/API/UI, while Stage 11A adds deterministic current Goal identity/context
and Stage 11B adds explicit Goal-to-choice/friction read model. Stage 11C0
adds the Growth Advisor privacy/payload/provenance design contract and Stage 11C
runtime is complete in PR #268. Stage 11D0 adds the provider-free Growth
Learning / Question contract; its bounded runtime and Stage 11E owner
Web/API/integration closeout are complete in Issue #274. All derived
stores remain outside the
vault/repository/release directories; no new environment variable or systemd
change is part of these slices.

Current next-stage status:

```text
Cognitive Twin v1 / Stage 1–8 = COMPLETE
Cognitive Twin v2 / Stage 9 = COMPLETE
Stage 10A Behavioral Observation/Cohort = COMPLETE
Stage 10B Behavioral Self Model = COMPLETE
Stage 10C0 Stated-vs-Observed mapping design = COMPLETE (DESIGN CONTRACT)
Stage 10C runtime = COMPLETE
Stage 10D Web/API + integration QA/closeout = COMPLETE
Stage 10 = COMPLETE
Stage 11A Current Goal identity/context core = COMPLETE
Stage 11B Goal-to-choice relation + friction read model = COMPLETE
Stage 11C0 Growth Advisor privacy/payload/provenance design = COMPLETE
Stage 11C Growth Advisor runtime = COMPLETE
Stage 13 Decision Compass / Compare v2 / GrowthCompare v1 = COMPLETE: design contract Issue #296 / PR #298 / merge `452a51fda49fd7ed30e116b55f73fe457ae3d4c7`; 13A Issue #299 / PR #305 / merge `9444a0936ed0df6ecf3a84fb65eb8bbd6d910e6f`; 13B Issue #300 / PR #307 / merge `cadc01b41b9c766056d45ef6b1b4d753027a236e`; 13C Issue #301 / PR #315 / merge `ab61a4f35d9ad8196b82bffef36170a889f8fc2a`; 13D Issue #302 / PR #316 / merge `7426c079bc95a2fed9f33b7f2b497a1fd5364c40`; 13E Issue #303 final release/closeout. Final runtime/deployed SHA `7426c079bc95a2fed9f33b7f2b497a1fd5364c40`, post-merge CI `34949012024`, automatic deploy `34949240222`, `env change required: no`
Stage 11D0 Growth Learning / Question design = COMPLETE
Stage 11D Growth Learning runtime = COMPLETE
Stage 11E Web/API + integration QA/closeout = COMPLETE
Stage 12A canonical records/validators = COMPLETE (Issue #279, PR #280, merged/deployed)
Stage 12B reviewed Safe Write = COMPLETE (Issue #284, PR #285, merged/deployed)
Stage 12C private read surface = COMPLETE (Issue #287, PR #288; provider-free, no route)
Stage 12D Growth + Goal Progress composition = COMPLETE (Issue #290, PR #291, merged/deployed)
Stage 12E Web/API + UI + integration QA/closeout = COMPLETE (Issue #293, PR #294, merge SHA `6068e1298ea3637d413498b72f397690a0278172`, post-merge CI #34886130422, production deploy #34886348462)
Stage 13 design gate = COMPLETE (Issue #296); Stage 13A = COMPLETE (Issue #299); Stage 13B = COMPLETE (Issue #300); Stage 13C = COMPLETE (Issue #301); Stage 13D = COMPLETE (Issue #302); Stage 13E = COMPLETE (Issue #303)
```

## Cognitive Twin v3 design status after Stage 13E implementation and closeout

Issue #277 defines the design boundary, Issue #279 authorized Stage 12A, Issue
#284 authorized the Stage 12B implementation slice, and Issue #290 authorized
the Stage 12D Growth + Goal Progress composition. The normative contracts are
[goal-progress-v1-contract.md](goal-progress-v1-contract.md) and
[growth-goal-progress-composition-v1-contract.md](growth-goal-progress-composition-v1-contract.md);
the high-level sequence is captured in
[cognitive-twin-v3-roadmap.md](cognitive-twin-v3-roadmap.md).

Cognitive Twin v3 = IN PROGRESS: Stage 12A–12D runtimes, the bounded Stage
12E Web/API/UI/integration slice and the complete Stage 13 Decision Compass /
Compare v2 sequence are complete and deployed. Stage 12E is in PR
#294, merged/deployed as `6068e1298ea3637d413498b72f397690a0278172` after
post-merge CI #34886130422 and production deploy #34886348462. The production
smoke was non-mutating; no new environment key, dependency, schema/NoteType,
systemd change or vault write was required (`env change required: no`). Stage
13 final runtime/deploy is `7426c079bc95a2fed9f33b7f2b497a1fd5364c40`, with
post-merge CI `34949012024`, automatic deploy `34949240222`, HTTP 200 health
and an anonymous private-boundary 401 smoke. Stage 14 design, read-side
records, reviewed Safe Write and provider-free evaluator are complete; its
Web/API/UI and release gates remain separate. Stage 15 contract and Phase 15.1
are complete; Phase 15.2 operational store is complete (PR #334,
merged/deployed as `f0d3f8a543c602d28ec3913281adf399c1c9e5b7`); Phase 15.3
projection and evaluation are in delivery.

The Stage 13 normative design gate is recorded in
[compare-v2-contract.md](compare-v2-contract.md) under Issue #296. It keeps
Compare v1 additive and unchanged, composes one exact selected Goal with
independent Simulate Me, Behavioral, Growth, Goal Progress and explicitly
requested Advisor branches, and permits only exact request-local structural
relations. It adds no runtime, provider payload, persistence, Web/API/UI or
vault behavior. The separately authorized Stage 13A–13D implementation gates
and the Stage 13E release/closeout gate are complete; their final runtime and
deployment evidence is recorded above.

The Stage 12 design decision is to use separate reviewed companion records
for an explicit progress definition and explicit progress observations. They
bind to the exact Stage 11A Goal UUID and GrowthGoalIdentityV1 fingerprint.
V1 supports bounded numeric targets and milestone sets, requires an explicit
baseline and exact event time, and produces only a deterministic descriptive
read model. Existing Goal and Stage 2 Outcome authority remain unchanged.

The design gate remains separate from the Stage 12A and Stage 12B
implementation gates:

~~~text
Stage 12 design contract = COMPLETE after Issue #277 merge
Stage 12A canonical records/validators = COMPLETE (PR #280, merge SHA `bdde50f2e3de3953dddf1ba58b4e9b371ea050ad`)
Stage 12B Safe Write = COMPLETE (Issue #284, PR #285, merge SHA `1832dc66de2ea25b41bb0d613ea77aae4dcd1fe2`, deployed)
Stage 12C private read surface = COMPLETE (Issue #287, PR #288; no provider/network/write)
Stage 12D Growth + Goal Progress composition = COMPLETE (Issue #290, PR #291, merge SHA `d78da72fef9f080bedfbd3ad1c87d40e49d227e8`, deployed)
Stage 12E Web/API QA + closeout = COMPLETE (Issue #293, PR #294, merged/deployed)
Stage 13 Decision Compass / Compare v2 / GrowthCompare v1 = COMPLETE (design Issue #296 / PR #298 / merge `452a51fda49fd7ed30e116b55f73fe457ae3d4c7`; 13A Issue #299 / PR #305 / merge `9444a0936ed0df6ecf3a84fb65eb8bbd6d910e6f`; 13B Issue #300 / PR #307 / merge `cadc01b41b9c766056d45ef6b1b4d753027a236e`; 13C Issue #301 / PR #315 / merge `ab61a4f35d9ad8196b82bffef36170a889f8fc2a`; 13D Issue #302 / PR #316 / merge `7426c079bc95a2fed9f33b7f2b497a1fd5364c40`; 13E Issue #303 closeout; post-merge CI `34949012024`; automatic deploy `34949240222`)
Stage 14 Personal Experiments = COMPLETE (PHASES 14.0–14.6; production closeout complete)
Stage 15 Adaptive Twin = PHASE 15.0 CONTRACT COMPLETE; PHASE 15.1 EXACT
SOURCE/CANDIDATE CORE COMPLETE (PR #333, merged/deployed as
`e233f61feee15b27adc1bc84c43e544eff3fa62f`); PHASE 15.2 OPERATIONAL STORE
COMPLETE (PR #334, merged/deployed as
`f0d3f8a543c602d28ec3913281adf399c1c9e5b7`); PHASE 15.3 PROJECTION AND
EVALUATION COMPLETE (PR #335, merged/deployed as
`64c995a294cd7f25977b69e38e592d271a19bed2`); PHASE 15.4 PRIVATE WEB/API/UI
COMPLETE (PR #336, merged/deployed as
`7304488355661d99af1d79f3aac676a328597434`); PHASE 15.5
SECURITY/PRIVACY/INTEGRATION/E2E IN DELIVERY; runtime phase 15.6 = NOT STARTED
~~~

Stage 12A–12E and Stage 13 add no provider, telemetry, background watcher,
automatic write-back, vault change, new environment variable, systemd change,
Codex Review request or Vault Sync. Stage 14 is complete and deployed through
its provider-free evaluator, owner-only Web/API/UI, adversarial gate and final
release/closeout; its production smoke was non-mutating and its exact evidence
is recorded in [Personal Experiments v1 contract](personal-experiments-v1-contract.md).
Stage 15 Phase 15.0 contract is complete under Issue #331; Phase 15.1 exact
source/candidate core, Phase 15.2 operational store, Phase 15.3
projection/evaluation and Phase 15.4 private Web/API/UI are complete; Phase
15.5 security/privacy/integration/E2E is in delivery; Phase 15.6 remains not
started.
