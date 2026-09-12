# Active Personal Learning v1 — нормативный contract и implementation boundary

Статус документа: **DESIGN / NORMATIVE CONTRACT ONLY**. Это deliverable
issue [#223](https://github.com/MikeMoore1337/second-brain/issues/223).
Runtime Stage 8, Web/API, provider, persistence и UI этим документом не
реализуются.

Контракт читается поверх current `main` после завершённого Stage 7 QA (#222).
Исторические SHA из предыдущих задач не являются authority: перед
implementation должен использоваться свежий `origin/main` и текущие
validators/read models.

Source of truth для этой границы:

- [design-roadmap-v1.md](design-roadmap-v1.md);
- [self-model-v1-contract.md](self-model-v1-contract.md);
- [self-retrieval-v1-contract.md](self-retrieval-v1-contract.md);
- [simulate-me-v1-contract.md](simulate-me-v1-contract.md);
- [compare-v1-contract.md](compare-v1-contract.md);
- [retrospective-calibration-v1-contract.md](retrospective-calibration-v1-contract.md);
- current application DTOs и validators в `main`.

В документе `ACCEPT` означает принятую нормативную границу, `CHANGE` —
уточнение существующего roadmap, `RISK` — известный риск без разрешения его
обходить, `DEFER` — отдельную будущую задачу. Эти слова не дают разрешения на
runtime-изменение в #223.

## 1. Цель

Active Personal Learning v1 может предложить владельцу один необязательный
foreground-вопрос, когда текущий provider-free `Simulate Me` operation не может
сделать однозначный выбор из-за bounded gap в evidence.

В v1 поддерживается только узкий choice-gap surface:

```text
явный foreground operation
  -> current validated Self Model / Simulate Me read models
  -> zero or one derived question candidate
  -> explicit user control: ignore / reject / answer
  -> unreviewed answer capture
  -> editable reviewed draft
  -> existing Personal Memory review + Safe Write
  -> current Self Model rebuild
```

Вопрос — UX candidate, а не evidence, событие, confidence update или
canonical fact. Его можно полностью удалить и пересобрать из current inputs.

## 2. Непереговорные invariants

1. `second-brain-vault` остаётся единственным canonical source of truth для
   пользовательского знания.
2. Question candidate — только derived/rebuildable in-memory state. Он не
   создаёт canonical record и не становится authority для следующего build.
3. Сам вопрос, его reason, `candidate_id`, `basis_fingerprint`, показ,
   ignore/reject и незавершённый answer не являются evidence.
4. Ответ не может напрямую менять Self Model, confidence, status, conflict,
   stale или supersede state.
5. Canonical evidence появляется только после explicit user review, metadata
   review, existing Safe Write dry-run/diff, explicit confirmation и
   post-write validation.
6. Пользователь может ignore или reject вопрос. Ни один из этих действий не
   трактуется как отрицательный fact, preference или consent на будущий опрос.
7. Нет background questions, scheduler, watcher, polling, silent consent или
   automatic re-prompting.
8. Нет automatic answer-to-fact conversion. Даже выбранный caller option не
   получает автоматически `evidence_kind`, `self_kind`, `domain` или
   `evidence_at`.
9. Первый runtime core должен быть provider-free, deterministic, local/read-only
   и не требовать network, credentials, database, cache, browser storage или
   persistence.
10. В этот Stage 8 contract не переносятся Stage 9+ growth, adaptive
    self-training, behavioral profiling, prospective audit, embeddings/RAG,
    provider policy или durable learning history.

## 3. Purpose и non-goals

### Purpose

Контракт должен безопасно ответить на вопрос: «Можно ли в текущем явно
запрошенном choice context показать ровно один нейтральный вопрос, который
поможет владельцу явно выбрать один из уже предложенных вариантов?»

Question candidate может появиться только из current typed read-model result,
который уже применил собственную authority policy. Stage 8 не повторяет
semantic matching, не анализирует raw note body и не пытается самостоятельно
оценить truth.

### Non-goals

В этот v1 не входят:

- numeric/qualitative confidence, probability, score или «low confidence»;
- вывод вопроса только из `SelfModelConfidence.state == NOT_ASSESSED`;
- automatic lexical/semantic contradiction detection в Self Model;
- generic open-ended interview, personality/trait diagnosis или therapy advice;
- background questioning, durable opt-in, user ranking, telemetry или learning
  history;
- Assistant rationale, Compare Delta или retrospective aggregate как источник
  вопроса;
- direct Self Model mutation endpoint;
- automatic Personal Memory metadata assignment или automatic Safe Write;
- новый `NoteType`, YAML/front matter field, schema migration или vault change;
- LLM, provider, network, embeddings, vector DB, RAG, retry/fallback или
  external research;
- production Web/API/CLI integration, deployment и `second-brain-vault` sync.

## 4. Canonical и derived state

| Представление | Статус | Authority / место | Правило Stage 8 |
| --- | --- | --- | --- |
| Reviewed managed note с exact `second_brain_personal_memory: 1`, typed metadata и reviewed body | **Canonical** | `second-brain-vault` | Только такая note может стать evidence после existing Safe Write. |
| Current `NoteRecord`, `SelfModelResult`, `SelfContextResult`, `SimulateMeResult`, Compare result или Calibration result | **Derived read model** | Existing current application boundary | Disposable, валидируется своим contract, не становится canonical truth. |
| `QuestionCandidateV1` | **Derived UX state** | Process/page memory | Rebuildable; удаление не меняет vault. |
| Question reason, prompt, source refs, candidate/fingerprint и expiry | **Derived metadata** | Process/page memory | Не evidence, не confidence и не audit history. |
| `QuestionResolutionV1` и `AnswerCaptureV1` до review | **Ephemeral candidate state** | Request/page memory | Не Personal Memory и не Self Model input authority. |
| `NoteDraft` после explicit answer capture | **Ephemeral reviewed candidate** | Existing capture/review boundary | Не evidence до metadata review и Safe Write. |
| `PersonalMemoryDraft` и dry-run/confirmation token | **Process state** | Existing Safe Write boundary | Не canonical до successful explicit apply и post-write validation. |
| Новая reviewed Personal Memory note после Safe Write | **Canonical** | `second-brain-vault` | Self Model увидит её только при следующем current rebuild. |

Derived projection не переписывает canonical note. Если пользователь не
согласен с вопросом или Self Model claim, он создаёт новую reviewed assertion
через existing capture/Safe Write либо ничего не сохраняет.

## 5. Authority и допустимые inputs

### 5.1. Единственный canonical pipeline

Canonical evidence для Stage 8 приходит только через уже существующую цепочку:

```text
second-brain-vault
  -> FileSystemVaultReader.scan()
  -> build_report()
  -> validated NoteRecord
  -> BuildSelfModel
  -> BuildSimulateMe
```

Stage 8 не открывает `second-brain-vault` отдельным способом и не вызывает
Search/LLM/provider для добычи evidence.

### 5.2. Allowed current read-model bundle

Следующий provider-free core получает application-owned bundle, построенный
одной foreground composition:

```text
ActiveLearningSourceV1 {
  self_model: SelfModelResult
  simulate_me_request: SimulateMeRequest
  simulate_me_result: SimulateMeResult
}
```

Это внутренний server-owned input, а не client JSON authority. Production
composition обязана построить и валидировать все три значения на current
operation; client не может передать свои claims, evidence refs, policy или
result и тем самым получить вопрос.

Допустимо использовать только:

- current validated `SelfModelResult` с exact `self-model-derivation-v1`,
  `unassessed-v1`, `NOT_ASSESSED`, bounded claims и policy fingerprint;
- exact normalized `SimulateMeRequest` с caller-owned `query` и options;
- current validated `SimulateMeResult` с exact Stage 6 identity:
  `simulate-me-v1`, `simulate-me-direct-exact-v1` и текущим approved
  fingerprint
  `sha256:07aa1d0d57fdd2d009087c05423fc4eb9304da70e87f32b1790fbd4753f21c3a`;
- UUID-only evidence refs, уже разрешённые current Self Model/Simulate Me
  validator. Их можно использовать для derived explainability и invalidation,
  но не как новую authority.

`SelfModelResult` нужен для current-source integrity и basis fingerprint. Stage 8
не извлекает из него новый claim, не меняет его confidence policy и не
переинтерпретирует его `claim` text.

### 5.3. Неиспользуемые или запрещённые inputs

В v1 candidate builder не использует как trigger:

- `SearchHit`, snippet, lexical rank, cached body, stale retrieval или old
  result;
- raw Markdown/body/front matter, path, title, source URL, storage timestamps
  или filesystem enumeration;
- `SelfContextResult` без server-owned Simulate Me boundary;
- `CompareResult`, Assistant rationale/evidence и human-readable Delta;
- `RetrospectiveCalibrationResult`, per-case target/outcome, caveats или
  aggregate accuracy;
- LLM output, transcript до review, draft, preview, answer или Safe Write
  receipt;
- client-supplied UUID, note body, claim text, reason, policy, fingerprint,
  `apply` flag или metadata.

Stage 5/7 read models и Calibration остаются архитектурными dependencies и
будущими источниками отдельных typed gaps, но не смешиваются с первым
choice-gap core. Если будущему consumer нужен такой источник, он получает
отдельный versioned contract, а не расширяет этот input молча.

## 6. Exact DTO boundary

Имена ниже — нормативная application shape. Реализация может выбрать
эквивалентные Python types, но не менять поля, closed values, bounds или
семантику.

### 6.1. Question option

```text
QuestionOptionV1 {
  id:    string  # exact request-local Simulate Me id
  label: string  # exact normalized caller-owned label
}
```

`id` и `label` не являются canonical identity. Вопрос сохраняет caller order;
сервер не сортирует, не переводит, не исправляет и не добавляет options.

### 6.2. Question candidate

```text
QuestionCandidateV1 {
  contract_version:            "active-personal-learning-v1"
  candidate_id:                "apl1:" + 64 lowercase hex chars
  kind:                        "choice"
  reason_code:                "missing_evidence"
                              | "conflicting_evidence"
                              | "insufficient_evidence"
  task:                        string
  question:                    string
  options:                     tuple[QuestionOptionV1, ...]
  source:                      "simulate-me-abstention-v1"
  source_derivation_version:   "simulate-me-v1"
  source_policy_id:            "simulate-me-direct-exact-v1"
  source_policy_fingerprint:   "sha256:07aa1d0d57fdd2d009087c05423fc4eb9304da70e87f32b1790fbd4753f21c3a"
  evidence_note_ids:           tuple[UUIDv7, ...]
  basis_fingerprint:           "sha256:" + 64 lowercase hex chars
  issued_at:                   aware datetime in UTC
  expires_at:                  aware datetime in UTC
}
```

DTO invariants:

- `task` проходит exact Stage 6 text normalization и имеет `1..4096` UTF-8
  bytes;
- `options` содержит `1..8` items; ID unique, ASCII и request-local по
  существующему Stage 6 pattern; label имеет `1..256` UTF-8 bytes;
- `question` — один из exact templates §8, максимум `512` UTF-8 bytes;
- `evidence_note_ids` содержит `0..20` unique strict UUIDv7 values, только из
  current validated source refs; path, body, claim text и `claim_id` в него не
  копируются;
- `candidate_id` и `basis_fingerprint` вычисляются application, не принимаются
  как client authority;
- `issued_at` и `expires_at` — aware UTC values, `expires_at > issued_at` и
  разница ровно `600` секунд;
- canonical candidate serialization имеет максимум `16384` UTF-8 bytes;
  truncation, partial options и silent ref dropping запрещены;
- candidate никогда не содержит `confidence`, `score`, `probability`,
  `recommendation`, `best`, `optimal`, `canonical`, `provider`, `model`,
  `path`, raw body, secret или `apply`.

### 6.3. Build result

```text
ActiveLearningResultV1 {
  status:             "candidate" | "no_candidate"
  candidate:          QuestionCandidateV1 | null
  no_candidate_code:  "questions_disabled"
                    | "no_actionable_gap"
                    | "rate_limited"
                    | null
}
```

`status == "candidate"` требует `candidate != null` и
`no_candidate_code == null`. `status == "no_candidate"` требует
`candidate == null` и ровно один bounded `no_candidate_code`.

`no_candidate` — нормальный result, а не error:

- `questions_disabled` — пользователь не включил explicit foreground question
  для этой операции;
- `no_actionable_gap` — Simulate Me дал valid prediction, либо текущий result
  не просит owner clarification;
- `rate_limited` — в том же foreground operation уже был показан или
  terminally resolved один candidate.

Invalid/unavailable source не маскируется `no_candidate`; он даёт safe error
из §12.

### 6.4. Explicit resolution

```text
QuestionResolutionV1 {
  candidate_id:       string
  disposition:        "ignore" | "reject" | "answer"
  selected_option_id: string | null
}
```

Strict invariants:

- `ignore` и `reject` требуют `selected_option_id == null`;
- `answer` требует ровно один ID из `candidate.options` и не принимает новый
  label, свободный text, UUID, metadata или `apply` flag;
- resolution сверяется с server/page-memory candidate и current basis; один
  `candidate_id` нельзя разрешить дважды;
- resolution — не evidence и не write request. Это bounded control message.

Choice-only `AnswerCaptureV1` после валидного `answer` может содержать только
точную пару текущего `task` и выбранного `QuestionOptionV1`. Это ещё не
`NoteDraft`, `PersonalMemoryDraft` или Self Model update.

## 7. Closed reason codes и deterministic semantics

### 7.1. Reason code vocabulary

`QuestionReasonCodeV1` имеет ровно три значения:

| Code | Deterministic meaning | Что это не означает |
| --- | --- | --- |
| `missing_evidence` | В active option set нет ни одного matching direct `preference`/`goal` support ref и нет contextual-only ref. Это точное следствие `SimulateMeAbstentionCode.NO_MATCHING_EVIDENCE` при пустых `evidence_refs` и `contextual_evidence_refs`. | Не означает, что в vault нет любых notes или что пользователь ничего не знает. |
| `conflicting_evidence` | `SimulateMeAbstentionCode.MULTIPLE_OPTIONS_SUPPORTED`: exact whole-label policy получила direct support для двух или более distinct request-local options. | Не является automatic semantic contradiction, majority vote, truth conflict или доказательством, что один option лучше. |
| `insufficient_evidence` | Direct option support отсутствует, но valid current result содержит один или более `contextual_evidence_refs` (например, belief-only context), который не имеет права выбрать option. | Не является numeric low confidence; не выводится из `NOT_ASSESSED`, sample size, age, count или claim wording. |

У `missing_evidence` и `insufficient_evidence` нет hidden fallback на
`created`, `updated`, UUID order, recency, confidence или storage state.

### 7.2. Eligibility mapping

Candidate eligible только когда одновременно выполнены все условия:

1. операция вызвана explicit foreground action и `questions_enabled == true`;
2. source bundle создан server-owned current composition и прошёл exact
   validators Self Model и Simulate Me;
3. source policy/version/fingerprint совпадают с approved v1 identities;
4. `simulate_me_result.kind == "abstention"`;
5. result не является `INSUFFICIENT_OR_INVALID_CURRENT_CONTEXT`;
6. exact cross-field mapping из таблицы ниже даёт один closed reason;
7. request options и source refs помещаются в bounds без truncation;
8. нет действующего candidate в том же foreground operation.

| Current validated Simulate Me result | Дополнительное условие | Stage 8 outcome |
| --- | --- | --- |
| `prediction` | любое | `no_candidate/no_actionable_gap` |
| `abstention/no_matching_evidence` | оба ref списка пусты | candidate `missing_evidence` |
| `abstention/no_matching_evidence` | `evidence_refs` пуст, `contextual_evidence_refs` непуст | candidate `insufficient_evidence` |
| `abstention/multiple_options_supported` | direct `evidence_refs` непусты и result validator подтверждает distinct option support | candidate `conflicting_evidence` |
| `abstention/insufficient_or_invalid_current_context` | любое | safe `SOURCE_UNAVAILABLE`; вопрос не предлагается |
| Любая другая shape, policy mismatch, impossible refs или overflow | любое | safe `SOURCE_INVALID`/`RESULT_TOO_LARGE`; partial candidate запрещён |

В Stage 6 current result `belief` может идти как contextual evidence, но
никогда не поддерживает выбор. Поэтому contextual-only result — это
`insufficient_evidence`, а не скрытый prediction и не automatic conflict.

### 7.3. Что считается conflict

Единственным conflict trigger v1 является already-approved Stage 6 structural
ambiguity: два или более distinct request-local option IDs поддержаны exact
matching direct claims. Это сохраняет Self Model policy
`contradiction_policy == "explicit-only-v1"` и не добавляет canonical conflict
relation.

Следующие случаи не дают `conflicting_evidence`:

- два opposite-looking body texts без Stage 6 exact support ambiguity;
- different domains, dates, `evidence_kind`, `created` или `updated`;
- новый UUID, более новый timestamp или большее число notes;
- Compare disagreement, Assistant recommendation или Calibration metric;
- human interpretation, fuzzy similarity, aliases, synonyms, LLM output.

## 8. Deterministic wording policy v1

Вопросы v1 — только fixed Russian templates. Никакой LLM, paraphrase,
translation, sentiment/psychology label или dynamic insertion из private body.
`task` и options отображаются отдельными bounded fields, а не склеиваются в
непроверенный HTML или в текст вопроса.

| `reason_code` | Exact `question` |
| --- | --- |
| `missing_evidence` | `Какой вариант лучше всего описывает ваш текущий выбор?` |
| `conflicting_evidence` | `Какой вариант лучше всего описывает ваш текущий выбор сейчас?` |
| `insufficient_evidence` | `Какой вариант лучше всего описывает ваш текущий выбор в этой ситуации?` |

Wording policy v1:

- не говорит «система считает», «обычно вы выбираете», «правильный вариант»,
  «лучший вариант», «у вас конфликт», «низкая уверенность» или «докажите»;
- не добавляет recommendation, score, confidence, urgency, diagnosis или
  negative framing;
- не выделяет и не ранжирует options; сохраняет их exact caller order;
- не заставляет пользователя отвечать: visible controls `ignore` и `reject`
  равноправны с `answer`;
- не вставляет в `question` note body, source URL, path, UUID, claim text,
  Assistant rationale, Compare text или Calibration details;
- изменение любого template требует новой `contract_version`/policy identity.

## 9. Candidate identity, ordering и bounds

### 9.1. Basis и deterministic identity

`basis_fingerprint` — lowercase SHA-256 digest UTF-8 canonical serialization
следующего application-owned snapshot:

```text
contract_version
kind
reason_code
normalized task
options in caller order: id + label
source
source_derivation_version
source_policy_id
source_policy_fingerprint
all current source evidence refs: UUIDs, dimensions, evidence_at and
  exact bounded ref metadata
current SelfModelResult claim text and ref metadata, excluding generated_at
```

Raw claim/body values могут участвовать только в process-local hash input;
кандидат, log и error не возвращают их. `generated_at`/wall-clock не входят в
basis, иначе одинаковый current snapshot не был бы deterministic. Current
source rebuild обязан пересчитать basis из current validated read models.

`candidate_id` получается из того же canonical digest как
`apl1:<64 lowercase hex>`. ID не является secret, auth token, UUID note или
persistence key.

### 9.2. Ordering

- `options` сохраняют порядок `SimulateMeRequest.options`; alphabetic label
  sorting, confidence ranking и hidden recommended option запрещены;
- `evidence_note_ids` unique и сортируются по lowercase canonical UUID string;
- при невозможности ограничиться одним candidate внутренний deterministic
  priority такой: `conflicting_evidence`, `missing_evidence`,
  `insufficient_evidence`; затем source, normalized task, option IDs и UUID
  refs. В v1 наружу всё равно возвращается не более одного candidate;
- source operation order, filesystem order, UUIDv7 embedded timestamp и map
  iteration order не влияют на reason или candidate selection.

### 9.3. Fixed bounds

| Limit | Exact v1 value |
| --- | ---: |
| candidates per explicit operation | `1` |
| active candidates in one page/process memory | `1` |
| Simulate Me options | existing `1..8` |
| task | existing `1..4096` UTF-8 bytes |
| option ID | existing ASCII `1..64` bytes/pattern |
| option label | existing `1..256` UTF-8 bytes |
| source evidence UUIDs in candidate | `0..20` unique UUIDv7 |
| question | `<=512` UTF-8 bytes |
| full candidate serialization | `<=16384` UTF-8 bytes |
| candidate TTL | exactly `600` seconds |

Overflow, invalid controls, unknown fields, duplicate IDs and invalid Unicode
дают safe error. Silent truncation, sampling, reranking и partial result не
допускаются.

## 10. Rate, expiry и lifecycle

### 10.1. Explicit rate boundary

`questions_enabled` должен быть explicit per-operation opt-in. Значение по
умолчанию — disabled. В v1:

- один foreground operation получает максимум один candidate;
- после show, ignore, reject или answer второй candidate в том же operation
  не строится;
- retry/fallback не создают новый candidate;
- server-wide/user-wide durable rate counter отсутствует, потому что
  persistence запрещена;
- повторный explicit operation после окончания page-memory state может снова
  дать candidate, если gap всё ещё current. Это не background re-prompt.

`rate_limited` — bounded `no_candidate_code`, а не evidence и не наказание
пользователю.

### 10.2. Expiry

`issued_at` и `expires_at` вычисляет application clock; client не выбирает их.
Через ровно 600 секунд candidate становится expired. Expired candidate нельзя
answer/ignore/reject; нужно выполнить новый explicit current operation.

Expiry — disposable UX guard, не evidence freshness, не stale policy Self
Model и не canonical timestamp.

### 10.3. Dispositions

| Control | Exact semantics | Canonical effect |
| --- | --- | --- |
| `show` | Render candidate once in current page/process state. | None. Show не означает consent, fact или attention evidence. |
| `ignore` | User откладывает вопрос без ответа. | None. Не создаёт negative preference и не требует объяснения. |
| `reject` | User считает вопрос неуместным или не хочет на него отвечать. | None. Rejection не становится «пользователь не выбирает option». |
| `answer` | User выбирает ровно один existing option ID. | Только ephemeral `AnswerCaptureV1`; никакого direct Self Model/Safe Write. |

Каждое disposition terminally закрывает candidate в текущем page/process state.
Оно не сохраняется в DB, vault, browser storage или telemetry.

## 11. Invalidation и stale questions

Candidate обязан быть отклонён до любого answer handoff, если выполняется хотя
бы одно условие:

- прошли 600 секунд;
- current source rebuild дал другой `basis_fingerprint`;
- изменилась normalized task или options, их order/ID/label;
- изменился current Self Model claim/ref/policy, от которого построен source
  snapshot;
- source UUID исчез, перестал быть unique/current или больше не проходит
  validator;
- current Simulate Me policy/derivation/fingerprint изменился;
- candidate уже имеет terminal disposition;
- candidate отсутствует в server/page-memory state или его fields были
  изменены client.

Один и тот же semantic gap может оставаться после rebuild, но новый operation
обязан выдать новый current candidate/basis. Старый candidate нельзя
«освежить» сохранением старого ID, fallback на Search/cache или переносом
старого answer в новый context.

Если current rebuild упал, Stage 8 возвращает safe stale/source error и не
пытается решить вопрос по прошлому result. Это fail-closed правило, а не
просьба к пользователю подтвердить устаревший prompt.

## 12. Answer -> reviewed Personal Memory -> Safe Write

### 12.1. Answer не является fact

`QuestionResolutionV1(disposition="answer")` сохраняет только точный
request-local option и active task. Система не выводит из него автоматически:

- `evidence_kind`;
- `self_kind`;
- `domain`;
- `evidence_at` или `evidence_at_precision`;
- title, tags, links, canonical UUID или note path;
- утверждение, что выбранный вариант является устойчивой preference, fact,
  goal или objective truth.

Answer может быть отброшен без следа. Если пользователь хочет сохранить его,
он должен перейти в existing reviewed capture flow. UI может показать
предзаполненный draft как editable suggestion, но этот draft обязан быть
явно изменяемым и не считается evidence.

### 12.2. Нормативный handoff

Единственный допустимый write path:

```text
QuestionResolution(answer)
  -> explicit AnswerCaptureV1
  -> user-authored/editable NoteDraft
  -> explicit Personal Memory metadata review
  -> PersonalMemoryDraft validation
  -> existing prepare_personal_memory(apply=False)
  -> full diff / explicit confirmation
  -> existing apply_personal_memory(apply=True)
  -> post-write validation / existing receipt
  -> current Self Model rebuild
```

Current implementation names для будущего adapter:

- `PersonalMemoryDraft` и `validate_personal_memory_draft`;
- `CreateManagedNoteFromPersonalMemoryDraft`;
- `CreateManagedNoteFromPersonalMemoryDraftRequest`;
- `DraftSaveService.prepare_personal_memory` и
  `DraftSaveService.apply_personal_memory`;
- существующие Web purpose-separated review/confirmation tokens и routes
  `/api/drafts/personal-memory/save/prepare` и
  `/api/drafts/personal-memory/save/apply`.

Stage 8 не добавляет альтернативный writer, metadata shortcut, update existing
note, direct `SelfModel` mutation или server-controlled `apply`. Existing
Personal Memory Safe Write сам эмитит exact marker и проверяет controlled
metadata только после review. Если prepare/apply не завершился успешно,
canonical evidence не существует; raw error не должен попадать в candidate.

### 12.3. Metadata review rule

Пользователь явно подтверждает edited body и каждое применимое поле
`evidence_kind`, `self_kind`, `evidence_at`, `evidence_at_precision`,
optional `domain`. Answer не может молча выбрать `user_statement` или
`preference`. Unknown time допускается только через existing validator и не
становится временем вопроса/показа.

Voice/free-text answer может использовать существующий Transcript/NoteDraft
review boundary в будущей integration-задаче; transcript до review также не
является evidence. Это не расширяет provider-free core #223.

## 13. Privacy, security и error boundary

### 13.1. Privacy rules

Обязательно:

- local/offline read-only candidate build без network, LLM, credentials и
  write capability;
- strict DTO validation: exact types, UTF-8 bounds, controls, unknown-field
  rejection и request-local option identity;
- server-owned current source/policy; client не может подменить evidence или
  fingerprint;
- no raw note body, front matter, path, source URL, secret, provider payload,
  exception repr или private diagnostics в candidate, logs или errors;
- evidence refs — только bounded current UUIDs; они не дают permission читать
  arbitrary vault path;
- no browser storage, cache, DB, audit log, telemetry, queue, watcher или
  background task;
- answer and disposition не отправляются external provider и не становятся
  canonical history;
- UI обязан использовать inert text rendering и existing same-origin/no-store
  boundary, если отдельный future Web issue покажет candidate.

Запрещено считать candidate privacy-safe только потому, что он derived: task,
option labels и answer могут содержать sensitive user input и должны иметь тот
же bounded/no-log treatment, что и caller-owned Stage 6 input.

### 13.2. Safe error taxonomy

Public/application boundary использует только закрытые codes и fixed messages:

| Code | Fixed message |
| --- | --- |
| `ACTIVE_LEARNING_INVALID_REQUEST` | `active personal learning request failed validation` |
| `ACTIVE_LEARNING_SOURCE_INVALID` | `active personal learning source is invalid` |
| `ACTIVE_LEARNING_SOURCE_UNAVAILABLE` | `active personal learning source is unavailable` |
| `ACTIVE_LEARNING_CANDIDATE_STALE` | `active personal learning question is stale` |
| `ACTIVE_LEARNING_CANDIDATE_EXPIRED` | `active personal learning question has expired` |
| `ACTIVE_LEARNING_CANDIDATE_ALREADY_RESOLVED` | `active personal learning question is already resolved` |
| `ACTIVE_LEARNING_INVALID_ANSWER` | `active personal learning answer failed validation` |
| `ACTIVE_LEARNING_RESULT_TOO_LARGE` | `active personal learning result exceeds its byte limit` |

`questions_disabled`, `no_actionable_gap` и `rate_limited` остаются normal
bounded result codes, не exceptions. Error message не содержит task, labels,
UUID list, body, path, YAML, source details, timing internals, secrets или
provider/network detail. Underlying typed errors могут быть сохранены только
внутри test/application boundary и не публикуются.

## 14. Deterministic algorithm для следующего core

Нормативная последовательность provider-free implementation:

```text
BuildActiveLearningQuestion(source, questions_enabled, now)
  1. validate exact source bundle, injected aware clock и bounds;
  2. if questions_enabled is false, return no_candidate/questions_disabled;
  3. validate current Self Model and Simulate Me policy/fingerprint;
  4. validate Simulate Me request/result cross-field invariants;
  5. map only the closed abstention table from §7.2;
  6. if prediction, return no_candidate/no_actionable_gap;
  7. build fixed Russian template and preserve caller option order;
  8. collect bounded UUID-only refs; overflow is a safe error;
  9. compute basis_fingerprint and candidate_id without generated_at;
 10. set issued_at=now, expires_at=now+600 seconds;
 11. validate full candidate bytes and return exactly one candidate;
 12. do not write, persist, retry, call provider or mutate Self Model.
```

Answer resolution выполняется отдельной pure/application operation: validates
current candidate, expiry, basis and selected option; returns ephemeral
`AnswerCaptureV1` or one fixed error. It не вызывает Safe Write. Existing
reviewed capture/write adapter remains a separate explicit boundary.

## 15. Exact next provider-free implementation slice

После merge #223 следующий отдельный implementation issue должен быть ограничен
одним core vertical slice и не должен автоматически стартовать в этой задаче.

### Разрешённый change set

- новый `src/second_brain/application/active_personal_learning.py`;
- focused `tests/test_active_personal_learning.py` на synthetic validated DTOs;
- минимальный import/export change только если он необходим для application
  navigation, без изменения existing ports/DTOs.

Новый module реализует только:

1. immutable DTOs/closed enums для source bundle, reason, candidate, result и
   resolution;
2. strict validation, exact mapping `SimulateMeResult -> reason_code`;
3. fixed wording, caller-order options, bounds, basis/candidate hash,
   one-candidate rate и 600-second expiry;
4. stale candidate validation и pure `ignore`/`reject`/`answer` transition;
5. ephemeral `AnswerCaptureV1` handoff payload без `NoteDraft` write, Safe Write
   call или metadata auto-fill.

### Обязательные tests

- exact type/unknown-field/bool-as-int/text-control/UTF-8/bound rejection;
- `questions_enabled=false`, valid prediction и repeated operation controls;
- all three deterministic reason mappings;
- `NOT_ASSESSED` alone never creates a question;
- opposite-looking text, different dates/domains, newer UUID и Compare/Calibration
  output never create implicit conflict;
- invalid/mismatched Stage 6 policy, invalid current context, malformed refs и
  source overflow fail closed without a candidate;
- fixed Russian wording, no dynamic private-body insertion, exact option order;
- deterministic candidate ID/basis with fixed source and clock;
- expiry at exact boundary, stale basis after changed source, deleted/mismatched
  UUID, already-resolved candidate и invalid selected option;
- ignore/reject never create evidence; answer returns only request-local option;
- no calls to network/provider/LLM/reader writer, no DB/cache/browser storage,
  no `second-brain-vault` access and no Self Model mutation;
- full existing suite remains green after the focused tests.

### Explicitly запрещено в следующем slice

- Web/API/CLI/React route, background job, scheduler, polling или browser state;
- direct `VaultReader` second scan, Search expansion, new persistence или
  source snapshot store;
- changes to `self_model.py`, `self_retrieval.py`, `simulate_me.py`,
  `compare.py`, `retrospective_calibration.py`, canonical YAML, Safe Write,
  `second-brain-vault` или production deployment;
- LLM/provider/network, new credentials, dependency or paid service;
- automatic `PersonalMemoryDraft`, metadata defaults, confirmation or apply;
- Compare/Calibration-triggered questions, open-ended interview, behavioral
  pattern, growth engine, prospective history or Stage 9+ semantics.

Если current composition не может передать one-snapshot server-owned bundle или
доказать current policy/identity, implementation возвращает safe source error и
останавливается; hidden fallback к stale/cache/full unfiltered context
запрещён.

## 16. Test and acceptance matrix для Stage 8 boundary

| Area | Required result |
| --- | --- |
| Canonical authority | Только reviewed Personal Memory Safe Write может создать canonical evidence; vault unchanged до этого. |
| Derived state | Candidate, reason, hash, expiry и disposition rebuildable/disposable. |
| Missing | Empty direct/context refs + `no_matching_evidence` → one neutral `missing_evidence` candidate. |
| Conflicting | Stage 6 exact multi-option support → `conflicting_evidence`; no winner/ranking. |
| Insufficient | Contextual-only refs + `no_matching_evidence` → `insufficient_evidence`; no numeric confidence. |
| Invalid current context | `insufficient_or_invalid_current_context` → safe source error; no question. |
| User control | Ignore and reject are available and have no canonical effect; answer is explicit one-option capture. |
| Stale | Any basis/policy/current UUID/expiry change rejects old candidate; no fallback. |
| Wording | Exact bounded Russian templates; no LLM, interpolation from private body or leading labels. |
| Rate/expiry | At most one candidate per explicit operation; exactly 600-second ephemeral TTL; no durable rate/history. |
| Handoff | Answer goes only to editable reviewed capture → existing Personal Memory metadata review → existing Safe Write. |
| No-side-effects | No provider/network/LLM/persistence/cache/browser storage/background task/vault change. |
| Scope | No Stage 9+ behavior, schema, provider, production or `second-brain-vault` change. |

## 17. ACCEPT / CHANGE / RISK / DEFER decision record

| Тема | Verdict | Нормативное решение |
| --- | --- | --- |
| Canonical authority | **ACCEPT** | `second-brain-vault` и existing reviewed Safe Write остаются единственной write/evidence boundary. |
| Question status | **ACCEPT** | Candidate — derived/rebuildable UX state, не evidence. |
| Low confidence wording | **CHANGE** | Roadmap phrase «low confidence» в v1 означает только typed missing/conflicting/insufficient gap; `NOT_ASSESSED` не превращается в score или question. |
| Missing/conflict semantics | **ACCEPT** | Closed mapping идёт из validated Stage 6 result; semantic contradiction и majority vote запрещены. |
| Insufficient semantics | **ACCEPT** | Contextual-only non-selecting evidence даёт bounded `insufficient_evidence`; invalid source даёт error, не вопрос. |
| Wording | **ACCEPT** | Fixed Russian templates, caller-owned task/options отдельными fields, no private-body interpolation. |
| Candidate count/rate | **ACCEPT** | One foreground candidate; no durable global/user rate state. |
| Expiry | **ACCEPT** | 600-second process/page-memory TTL; expiry не является evidence freshness. |
| Answer | **ACCEPT** | Только explicit request-local option capture; ignore/reject do nothing canonical. |
| Personal Memory handoff | **ACCEPT** | Reviewed editable draft → explicit metadata review → existing Prepare/confirmation/Apply Safe Write. |
| Self Model mutation | **REJECT** | Ни resolution, ни candidate не имеют mutation authority. |
| Compare/Calibration trigger | **DEFER** | Separate versioned gap contracts only; их current outputs не являются question authority в first core. |
| Open-ended/voice interview | **DEFER** | Сначала choice-only deterministic core; voice/free text остаются existing capture concerns. |
| Numeric information gain | **DEFER** | Не вводить expected information gain, score, threshold или optimization. |
| Durable rate/history/consent | **DEFER** | Требуют отдельного persistence/privacy/retention gate. |
| Provider/network/LLM | **DEFER / FORBIDDEN HERE** | Отдельный owner-approved provider/privacy contract; Stage 8 v1 core остаётся local/provider-free. |
| Schema/vault/production | **DEFER / FORBIDDEN HERE** | Нет migration, note type, vault write, deploy, sync или runtime change в #223. |

`HUMAN_REQUIRED: none` для зафиксированной contract-only границы. Любая
попытка расширить источник, разрешить automatic conversion, durable history,
provider/private context или новый canonical field должна остановить
implementation и получить отдельный owner decision.

## 18. Итог текущей задачи

Этот issue изменяет только нормативную документацию:

- добавляет настоящий Stage 8 contract;
- минимально связывает roadmap со статусом и ссылкой;
- не добавляет `src/` runtime, tests, dependency, schema, provider, network,
  persistence, environment setting или vault change.

Следующий core issue описан в §15, но не начат в #223.
