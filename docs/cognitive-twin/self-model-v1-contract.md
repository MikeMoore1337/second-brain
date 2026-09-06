# Self Model v1 — contract

Статус: **DESIGN / CONTRACT**, issue [#81](https://github.com/MikeMoore1337/second-brain/issues/81).
Этот документ не добавляет Stage 4 runtime и не является разрешением начинать
issue [#82](https://github.com/MikeMoore1337/second-brain/issues/82). Он фиксирует
границы и proposed application contract, чтобы после принятия всех
`HUMAN_REQUIRED` решений #82 можно было реализовать механически.

Исходная точка design: `origin/main`
`c9443a5b6ab9a2876130b9d103a63827733c80ff`.

Issue #81 остаётся source of truth по scope и dependency gate. Если этот
документ расходится с уже merged контрактом Stage 1-3, приоритет имеет merged
контракт. Если здесь есть `HUMAN_REQUIRED`, это proposal, а не silently approved
product semantics.

## 1. Purpose и non-goals

### Purpose

Self Model v1 — bounded read model, который на каждом build пересобирает
объяснимые claims о пользовательских предпочтениях, beliefs, goals и
наблюдаемых patterns из текущих валидных canonical evidence. Он нужен для
будущих read-only consumers, которым требуется:

- увидеть, на каких canonical UUID основан claim;
- отдельно увидеть evidence, которое claim поддерживает и ему противоречит;
- сохранить различие между временем evidence и временем построения модели;
- показать uncertainty вместо убедительного, но неподтверждённого profile;
- удалить или пересобрать derived state без потери пользовательских знаний.

### Non-goals

В #81 и в первом implementation slice запрещены:

- Self Model runtime, Web Self Model и durable profile file;
- Self Retrieval, embeddings, RAG, vector/graph DB и Redis;
- background daemon, watcher, event store или persistent model DB;
- automatic inference write-back в vault;
- изменение `schema_version`, `evidence_kind`, `self_kind` или Safe Write;
- изменение Stage 1-3 semantics и Search/Retrieval semantics;
- автоматическая запись claims, confidence, stale/conflict/supersede или
  calibration в canonical YAML/Markdown;
- psychological diagnosis, hidden personality score и inference sensitive
  traits;
- превращение habitual behavior в recommendation;
- новый external provider или silent transmission Self Model;
- Simulate Me, Compare, Calibration и Active Learning runtime.

Self Model не отвечает на вопрос «какое решение объективно лучше». Он может
сохранить derived hypothesis о том, какой выбор пользователь, вероятно, сделал
бы сам, но такая hypothesis не является recommendation и не становится fact.

## 2. Canonical и derived boundary

### 2.1. Единственный canonical source of truth

Canonical source остаётся независимый `second-brain-vault`. Для Self Model
каноническим evidence считается только текущий результат существующего read
pipeline:

```text
second-brain-vault
  -> FileSystemVaultReader.scan()
  -> build_report()
  -> validated NoteRecord
```

Typed evidence допускается только для managed note с exact YAML scalar
`second_brain_personal_memory: 1`. Для неё текущие merged validators проверяют
`evidence_kind`, `self_kind`, `evidence_at`,
`evidence_at_precision`, optional `domain`, а для Stage 2 — body и
`decision_id` relation.

Без exact marker note остаётся обычной canonical managed note. Совпадающие
имена `evidence_kind`, `self_kind`, `domain`, `evidence_at` или
`evidence_at_precision` в legacy front matter не дают Personal Memory или Self
Model semantics.

### 2.2. Что является derived

К derived state относятся:

- `SelfModelEvidenceRef`, relation classification и temporal aggregation;
- `SelfModelClaim`, его confidence и status;
- `generated_at`, `derivation_version` и policy fingerprints;
- любые disposable indexes, caches, candidate lists и evidence graphs.

Derived state может существовать только в памяти результата или в будущем
disposable cache, который можно полностью удалить и снова получить из vault.
Ни один derived объект не является authority для следующего build.

### 2.3. Запрещённое повышение authority

Следующие значения никогда не становятся canonical evidence сами по себе:

| Candidate | Почему не authority |
| --- | --- |
| `SearchHit` / snippet / rank | Это candidate retrieval projection; он может быть stale и не содержит current body |
| cache или snapshot прошлого build | Он может исчезнуть, быть устаревшим или построенным другой policy |
| LLM output / transcript до review | Это untrusted candidate, а не reviewed user assertion |
| один inferred claim | Высокая уверенность не превращает inference в fact |
| filesystem `created`/`updated` | Это storage lifecycle time, не evidence/event time |
| Obsidian link без current UUID resolution | Link — signal, а не проверенная semantic relation |

Если пользователь не согласен с derived claim, исправление проходит обычный
reviewed capture и Safe Write и создаёт новую canonical assertion. Старый claim
не редактируется приложением.

## 3. Compatibility с merged Stage 1-3

### 3.1. Existing fields остаются без изменений

Self Model только читает существующие значения:

- `NoteRecord.note_id` — strict UUIDv7 identity;
- `NoteRecord.personal_memory` — typed projection после exact marker;
- `EvidenceKind` — текущие четыре canonical provenance classes:
  `explicit_user_fact`, `user_statement`, `observed_decision`,
  `outcome_later_observation`;
- `SelfKind` — текущие `memory`, `preference`, `belief`, `goal`, `decision`,
  `outcome`;
- `PersonalMemoryMetadata.decision_id` — current UUIDv7 relation для Outcome;
- `DecisionJournalRecord` — reviewed pre-choice fields и user-entered
  `confidence` как текстовая секция body;
- `OutcomeObservationRecord` — reviewed `actual_result`, `reassessment` и
  `notes`;
- `evidence_at` + `evidence_at_precision` — canonical evidence time;
- `created`/`updated` — только storage audit metadata существующего note.

В `domain.models`, `NoteDraft`, `SearchIndexPort`, Search DTO, Safe Write и
canonical YAML contracts в рамках #81 ничего нового не добавляется.

### 3.2. Semantic distinction

| Existing value | Что Self Model может считать из него | Чего он не может утверждать |
| --- | --- | --- |
| `explicit_user_fact` | пользователь после review сообщил fact/assertion | что это внешне verified objective truth |
| `user_statement` | пользователь после review выразил belief, preference, goal или explanation | что модель доказала это утверждение |
| `observed_decision` | пользовательский reviewed record фактически выбранного option | что один choice является постоянной preference или rule |
| `outcome_later_observation` | позднее reviewed observation, связанное с current decision UUID | что outcome переписывает expected result или decision |
| Decision Journal `Confidence` | text evidence о субъективной уверенности в момент выбора | что это `SelfModelConfidence` или numeric model score |

## 4. Exact proposed application DTOs

Ниже описывается additive application contract. Это не Python code в #81 и не
новая canonical schema. В реализации #82 DTOs могут находиться в
`src/second_brain/application/self_model.py`; существующие domain DTOs менять не
нужно.

### 4.1. Derived dimension taxonomy

```python
class SelfModelDimension(StrEnum):
    PREFERENCE = "preference"
    BELIEF = "belief"
    GOAL = "goal"
    DECISION_RULE = "decision_rule"
    BEHAVIORAL_PATTERN = "behavioral_pattern"
```

Это derived taxonomy, не `self_kind` и не YAML ontology. В v1 не вводятся
`risk_profile`, `communication_style`, `knowledge_context`, personality или
какие-либо sensitive/diagnostic dimensions.

Mapping является ограниченным:

- enrolled `preference`, `belief`, `goal` могут быть direct assertion inputs;
- enrolled `decision` и `outcome` могут участвовать в policy-approved
  `decision_rule`/`behavioral_pattern` derivation;
- `memory` может быть contextual evidence, но сам по себе не создаёт profile
  dimension;
- `evidence_kind` не является dimension и не определяет truth strength.

Direct mapping и pattern aggregation — разные операции. Наличие canonical
`decision` не означает автоматическое создание `preference` или
recommendation.

### 4.2. Evidence reference

```python
@dataclass(frozen=True, slots=True)
class SelfModelEvidenceRef:
    note_id: UUID
    evidence_kind: EvidenceKind
    self_kind: SelfKind
    domain: str | None
    evidence_at: EvidenceAt
    evidence_at_precision: EvidenceAtPrecision
    related_note_ids: tuple[UUID, ...] = ()
```

`note_id` — canonical UUIDv7 конкретной current note. Body, absolute/relative
path, snippet, storage timestamp и source URL в ref не копируются. `related_note_ids`
допускается только для уже проверенных current canonical relations; в v1
главный случай — `OutcomeObservationRecord.decision_id`.

Invariant:

- ref создаётся только из current `NoteRecord` после scan/report validation;
- все UUID в `note_id` и `related_note_ids` — strict UUIDv7;
- один `note_id` не дублируется в одной role collection;
- `EvidenceKind`/`SelfKind` — existing enums, а не новые model values;
- `model_inference` невозможен как `evidence_kind`;
- `evidence_at == "unknown"` сохраняется буквально и не заменяется другим
  timestamp.

В JSON-адаптере можно получить удобную projection
`supporting_canonical_evidence_uuids` из `supporting_evidence[*].note_id`, но
такая projection не создаёт вторую authority и не хранится отдельно.

### 4.3. Temporal context

```python
@dataclass(frozen=True, slots=True)
class SelfModelTemporalContext:
    earliest_known_evidence_at: datetime | None
    latest_known_evidence_at: datetime | None
    known_evidence_count: int
    unknown_evidence_count: int
```

Это агрегат только по `evidence_at` refs конкретного claim:

- `earliest_known_evidence_at` и `latest_known_evidence_at` считаются только
  среди aware RFC3339 values;
- если known values нет, оба поля равны `None`, а
  `unknown_evidence_count` сохраняет факт неизвестного времени;
- unknown time не является ранним, поздним или текущим временем;
- validity interval (`valid_from`/`valid_until`) в этот DTO не добавляется;
- `generated_at` хранится отдельно и никогда не попадает в evidence range.

Таким образом, range с known values не скрывает additional evidence с
`unknown` time: consumer обязан смотреть на оба счётчика и refs.

### 4.4. Confidence representation

```python
class SelfModelConfidenceState(StrEnum):
    NOT_ASSESSED = "not_assessed"
    ASSESSED = "assessed"


@dataclass(frozen=True, slots=True)
class SelfModelConfidence:
    state: SelfModelConfidenceState
    score: float | None
    policy_version: str | None
    supporting_evidence_count: int
    contradicting_evidence_count: int
    unknown_time_count: int
```

Это envelope, а не formula:

- `score` допускается только как finite value в диапазоне `0.0..1.0` и только
  при `state == ASSESSED` и non-empty approved `policy_version`;
- `state == NOT_ASSESSED` означает `score is None` и `policy_version is None`;
- representation не утверждает, что score является probability, calibration
  или truth likelihood;
- counts — descriptive audit counters и сами по себе не являются weights;
- numeric formula, evidence weights, recency weights, thresholds и calibration
  interpretation не определяются этим документом;
- user-entered Decision Journal `Confidence` не копируется, не парсится и не
  преобразуется в этот объект.

Если human-approved policy будет qualitative-only, она может вернуть
`state == ASSESSED` с `score is None` и собственным policy id. В DTO не
добавляется неподтверждённая шкала `low/medium/high`.

### 4.5. Status representation

```python
@dataclass(frozen=True, slots=True)
class SelfModelStatus:
    code: str
    policy_version: str
```

`code` — policy-bound derived token, не canonical field и не `self_kind`.
Этот документ намеренно не закрывает его allowlist: финальная status taxonomy
является `HUMAN_REQUIRED`. Пока status policy не утверждена, builder не
подставляет `current`, `stale`, `conflicted`, `superseded`, `unresolved` или
любое другое значение по умолчанию, а завершается safe
`SELF_MODEL_POLICY_UNAVAILABLE`.

### 4.6. Claim и result

```python
@dataclass(frozen=True, slots=True)
class SelfModelClaim:
    dimension: SelfModelDimension
    claim: str
    domain: str | None
    supporting_evidence: tuple[SelfModelEvidenceRef, ...]
    contradicting_evidence: tuple[SelfModelEvidenceRef, ...]
    contextual_evidence: tuple[SelfModelEvidenceRef, ...]
    confidence: SelfModelConfidence
    temporal_context: SelfModelTemporalContext
    status: SelfModelStatus
    generated_at: datetime
    derivation_version: str


@dataclass(frozen=True, slots=True)
class SelfModelRequest:
    max_claims: int = 200
    max_evidence_refs_per_claim: int = 200


@dataclass(frozen=True, slots=True)
class SelfModelResult:
    claims: tuple[SelfModelClaim, ...]
    eligible_evidence_count: int
    represented_evidence_count: int
    generated_at: datetime
    derivation_version: str
```

DTO invariants:

- `claim` — non-empty bounded UTF-8 string; proposed application cap — 4096
  bytes. Это resource limit, а не разрешение копировать весь body;
- `domain` сохраняет existing optional slug semantics; новый domain registry не
  создаётся;
- `supporting_evidence` не пустой для каждого emitted claim;
- UUID одного claim не может одновременно находиться в supporting и
  contradicting collections; contextual refs не считаются supporting;
- все role collections имеют deterministic order;
- `generated_at` — aware application clock value и одинаков для result/claims;
- `derivation_version` в result и claim совпадает и является bounded non-empty
  version identifier;
- `represented_evidence_count` считает distinct current refs, реально включённые
  хотя бы в одну claim role. Evidence, не ставшее claim, не превращается молча
  в claim;
- `max_claims` и `max_evidence_refs_per_claim` — strict integers в диапазоне
  `1..200`. Если полный result или отдельная claim превышает соответствующий
  лимит, builder возвращает safe `SELF_MODEL_RESULT_TOO_LARGE`, а не тихо
  выдаёт partial profile;
- `SelfModelResult` не содержит `vault_path`, raw body, `created`, `updated`,
  credentials, provider fields или write receipt.

### 4.7. Policy binding и application API

Human decisions не должны скрываться в `if`/constant внутри implementation.
Composition может inject policy binding, недоступный client request:

```python
@dataclass(frozen=True, slots=True)
class SelfModelPolicy:
    derivation_version: str
    claim_generation_policy: str
    confidence_policy: str
    evidence_weight_policy: str
    recency_policy: str
    contradiction_policy: str
    stale_policy: str
    supersede_policy: str
    status_policy: str
```

Все policy identifiers — versioned application configuration, а не поля vault.
Они не извлекаются из note body, SearchHit, LLM или client input. Каждое
обязательное значение должно быть явно approved; отсутствие или неизвестный
identifier — fail closed.

Будущий application use case:

```python
BuildSelfModel(
    reader: VaultReader,
    policy: SelfModelPolicy,
    clock: Callable[[], datetime],
).execute(request: SelfModelRequest) -> SelfModelResult
```

С точки зрения application boundary это read-only операция
`BuildSelfModel(request) -> SelfModelResult`; `reader`, approved `policy` и
clock принадлежат composition, не пользователю. У use case нет `save`,
`update`, `confirm`, `apply` или profile mutation operation.

Proposed safe error taxonomy:

```text
SELF_MODEL_INVALID_REQUEST
SELF_MODEL_INVALID_CLOCK
SELF_MODEL_POLICY_UNAVAILABLE
SELF_MODEL_VAULT_UNAVAILABLE
SELF_MODEL_EVIDENCE_INVALID
SELF_MODEL_RESULT_INVALID
SELF_MODEL_RESULT_TOO_LARGE
```

Ошибки не раскрывают raw path, body, YAML, UUID list, secret, exception repr
или provider detail наружу.

## 5. Evidence input eligibility

### 5.1. Accepted pipeline

`#82` должен использовать существующий `VaultReader` и проходить ровно через
`scan() -> build_report()`. Self Model не вызывает Search, LLM, writer или
external adapter для получения evidence.

После integrity gate выбираются только current notes, у которых одновременно:

1. note managed;
2. `note_id` — valid UUIDv7 и identity unique в current report;
3. exact marker уже распознан scanner;
4. `note.personal_memory` — non-null typed metadata;
5. Stage 2 body/relation projection valid, если note относится к Decision или
   Outcome;
6. note не исключена current report diagnostic, влияющей на её canonical
   content или relation.

### 5.2. Eligible evidence classes

| Canonical pair | Input role |
| --- | --- |
| `explicit_user_fact` + `memory/preference/belief/goal` | reviewed user assertion; `memory` обычно contextual, остальные могут быть direct input |
| `user_statement` + `memory/preference/belief/goal` | reviewed user statement; не объективная verification |
| `observed_decision` + `decision` | reviewed behavior observation; option/reasons доступны через typed Journal projection |
| `outcome_later_observation` + `outcome` | reviewed later observation; `decision_id` должен разрешаться в current valid Journal |

`self_kind`/`evidence_kind` pair validation остаётся existing Stage 1-3
contract. Self Model не добавляет новый pair.

### 5.3. Explicit exclusions

Не являются Self Model evidence:

- legacy note без exact marker, включая note с coincidental fields;
- unmanaged Inbox document, template, attachment или arbitrary front matter;
- `SearchHit`, FTS snippet, lexical rank и cached search body;
- timeline item как самостоятельный source — Timeline лишь derived projection
  тех же canonical notes;
- `NoteDraft`, transcript до review, preview, review/confirmation token или
  Safe Write receipt;
- LLM-generated claim, paraphrase, embedding, external source или provider
  output без reviewed canonical user assertion;
- filesystem `created`/`updated` как evidence time.

Source provenance (`sources`) может оставаться canonical provenance note, но
само наличие внешнего URL не доказывает пользовательскую preference, belief,
goal или personality. Если пользователь явно enrolled reviewed assertion,
Self Model видит её canonical metadata/body в пределах существующего contract,
а не повышает её authority из-за URL.

## 6. Supporting, contradicting и contextual evidence

### 6.1. Representation

В каждом claim role collections независимы:

```text
supporting_evidence   -> refs, которые policy считает поддерживающими claim
contradicting_evidence -> refs, которые policy считает несовместимыми claim
contextual_evidence   -> refs, использованные для контекста, но не объявленные
                           ни поддержкой, ни contradiction
```

Пустой `contradicting_evidence` означает только отсутствие evidence,
классифицированного как contradiction в данном result; это не означает,
что claim доказан. Пустой `contextual_evidence` также не означает отсутствия
других canonical notes в vault.

Каждый ref обязан разрешаться к current canonical UUID. Missing/deleted UUID
не восстанавливается по path, basename, snippet, body similarity или stale
index. Если missing ref должен быть частью claim, build либо исключает claim,
либо завершается `SELF_MODEL_EVIDENCE_INVALID` согласно integrity gate; partial
claim без обязательного support запрещён.

### 6.2. Relation boundary

Role classification — derived explanation, не truth field в vault. Для одного
claim:

- один ref не может быть одновременно supporting и contradicting;
- majority vote не является default resolver;
- одна observed decision не становится автоматически rule/preference;
- user confidence не становится relation weight;
- contradiction не выводится только из different words, different dates или
  different domains;
- `supersedes` не выводится из `updated`, `created`, UUID order или более нового
  `evidence_at`.

Если policy не может безопасно классифицировать ref, он остаётся contextual или
не участвует в emitted claim. Его нельзя silently считать support.

### 6.3. Почему нет graph DB

Для v1 достаточно immutable tuples refs в памяти. Отдельная
`EvidenceEdge`/graph persistence не нужна. Existing Outcome relation читается
из validated `decision_id`; derived support/contradiction role живёт только в
claim result и исчезает при удалении derived state.

## 7. Temporal semantics

### 7.1. Canonical evidence time

Self Model наследует Stage 1-3 rule:

| Evidence | Canonical time |
| --- | --- |
| `explicit_user_fact` / `user_statement` | когда пользователь сообщил assertion (`evidence_at`) |
| `observed_decision` | когда был сделан/зафиксирован choice (`evidence_at`) |
| `outcome_later_observation` | когда был замечен outcome (`evidence_at`) |

Для known value требуется RFC3339 с explicit UTC offset и `exact`. Для
unknown используется только literal `unknown` + `unknown` precision. Unknown
остаётся unknown на всех уровнях: ref, temporal aggregate, sorting и output.

### 7.2. Storage и generation times

- `created` и `updated` могут быть полезны существующему audit/read contract,
  но не участвуют в evidence weighting, event ordering, stale calculation или
  supersede decision;
- `generated_at` — время текущего build, а не время claim/evidence;
- UUIDv7 может использоваться как stable identity/tie-break только при
  deterministic ordering; его embedded timestamp не используется как fallback
  evidence time;
- validity intervals и approximate `day/month/period` semantics не вводятся в
  canonical schema или Self Model v1 DTO.

### 7.3. Sorting

Filesystem enumeration не является deterministic source. Proposed ordering:

1. role collections сортируются по `str(note_id)`, затем по bounded enum/domain
   tie-break;
2. claims сортируются по `dimension.value`, `domain or ""`, первым support
   UUID и bounded claim text;
3. unknown timestamps не участвуют в chronological ordering;
4. порядок никогда не означает «сильнее», «новее» или «current».

Если human policy требует временного weighting, это отдельная decision и не
следует из этого sorting rule.

## 8. Confidence без непринятой formula

### 8.1. Что уже известно

- Self Model confidence — оценка derived claim, не canonical fact;
- Decision Journal confidence — subjective user text в pre-choice record;
- число notes не является автоматически probability;
- supporting и contradicting counts надо показывать раздельно;
- unknown time и small sample должны быть видны;
- confidence нельзя записывать в vault как metadata.

### 8.2. Что намеренно не утверждается

Этот документ не выбирает:

- numeric formula;
- per-`evidence_kind` weights;
- recency decay/weight;
- confidence thresholds или bins;
- calibration/probability interpretation;
- minimum sample для pattern claim.

`SelfModelConfidence` поэтому задаёт только safe representation и policy
binding. Builder не имеет `default_score`, `newer_is_stronger`,
`observed_decision=1.0` или похожего скрытого fallback.

## 9. Conflict, stale и supersede boundary

### 9.1. Structural rules independent of human policy

Независимо от будущих decisions Self Model обязан:

- сохранять старые canonical notes и никогда не удалять их для разрешения
  конфликта;
- не схлопывать incompatible evidence в одну canonical truth;
- не скрывать contradicting refs за итоговым score;
- не использовать `created`/`updated` или UUID order как supersede signal;
- пересобирать result из current vault после note edit/delete;
- fail closed, если required current evidence/relation не может быть доказана.

### 9.2. Unresolved product semantics

Следующие predicates требуют human decision и не следуют однозначно из merged
Stage 1-3:

- что именно считается contradiction;
- что является stale и какой threshold применять;
- что означает superseded и может ли это быть выведено из existing body/links;
- сильнее ли новое evidence старого и в каких context/horizon;
- какой status code выдавать при conflict/staleness/insufficient evidence.

До approval этих predicates нет default policy. `HUMAN_REQUIRED` — это не
status claim в vault; это gate реализации.

### 9.3. Safe handling before policy

Если policy отсутствует или не может доказать relation:

- Self Model не выбирает «победивший» assertion;
- evidence остаётся unclassified/contextual либо claim не emitted;
- status/confidence policy error возвращается bounded safe error;
- никакая note не меняется;
- #82 не начинает runtime до разрешения gate из issue #82.

## 10. Deterministic/rebuild algorithm

Ниже разделяет уже accepted mechanics и policy-dependent steps.

### 10.1. Build sequence

```text
BuildSelfModel(request)
  1. validate exact request type, bounds и injected clock
  2. read current VaultReader.scan()
  3. build_report(snapshot)
  4. apply canonical scan/integrity gate
  5. collect current eligible NoteRecord evidence
  6. create immutable SelfModelEvidenceRef values
  7. form claim candidates using approved claim_generation_policy
  8. classify supporting/contradicting/contextual refs using approved policies
  9. derive temporal aggregate from evidence_at only
 10. derive confidence/status using explicitly bound approved policies
 11. validate every emitted claim/result invariant
 12. return result in deterministic order; do not persist it
```

### 10.2. Candidate generation

Mechanically accepted part:

- direct Stage 1 assertion candidate may originate only from current enrolled
  `preference`, `belief` or `goal` evidence;
- decision/outcome candidate must originate from current typed Stage 2 DTOs;
- one canonical decision is not enough to label a permanent preference or rule;
- `memory` is not silently promoted to profile dimension;
- no candidate can be emitted without at least one supporting canonical UUID.

Policy-dependent part:

- how canonical body becomes bounded `claim` text;
- how multiple assertions are grouped into one claim;
- whether exact normalized chosen options may form a pattern;
- minimum sample and scope/domain rules;
- whether decision/outcome evidence can support direct preference/belief/goal
  claims;
- whether cross-domain or cross-horizon evidence may be combined.

These choices are in `claim_generation_policy`; implementation must not hide a
choice behind an arbitrary string normalizer, majority vote, LLM call or
recency fallback.

### 10.3. Rebuild and deletion behavior

The source snapshot is always current. Therefore:

- adding a reviewed eligible note can add or change derived candidates;
- editing a canonical body/metadata can change or invalidate a claim;
- deleting a supporting note removes that support and may remove the claim;
- deleting a contradiction can change relation output only under approved policy;
- deleting all derived state loses no user data and the next build recomputes it;
- a stale cache is ignored rather than used as evidence authority.

No incremental event log, background watcher or write-back is required.

### 10.4. Determinism

For the same canonical snapshot, same approved policy, same input request and
same `generated_at`, repeated builds return byte/field-equivalent DTOs. The
clock is injected so tests can compare results without using wall-clock
uncertainty. `generated_at` itself is allowed to differ between real builds;
that does not make it evidence.

## 11. Fail-closed integrity behavior

### 11.1. Whole-result rule

Self Model не возвращает convincing partial profile, если current canonical
evidence integrity не доказана. Ошибка одного required enrolled record или
relation не должна маскироваться уменьшенным confidence.

### 11.2. Blocking conditions

Builder должен вернуть safe error при:

- missing/invalid manifest или недоступном current content root;
- `NOTE_READ_ERROR`, `NOTE_FRONT_MATTER_ERROR`, duplicate managed UUID или
  иной diagnostic, которая делает current canonical content incomplete;
- `PERSONAL_MEMORY_*`, `DECISION_JOURNAL_INVALID_BODY` или
  `OUTCOME_OBSERVATION_INVALID_BODY` для enrolled evidence;
- missing/duplicate/invalid `OUTCOME_DECISION_*` relation;
- invalid managed identity, note type, storage timestamps или unsafe relative
  path у note, которую нужно использовать;
- отсутствующем или неизвестном обязательном `SelfModelPolicy` identifier;
- result, claim, ref, clock или bound, не удовлетворяющем DTO invariant.

Диагностика, которая относится только к template/attachment root и не может
изменить eligible content scope, может оставаться non-blocking по существующей
Timeline scope policy. Нельзя применять такое исключение к ошибке enrolled
note или current relation.

### 11.3. No stale resurrection

При missing UUID или deleted note запрещено восстанавливать body из Search,
cache, previous result, path или LLM summary. В зависимости от конкретной
integrity boundary результат либо rebuilds без claim, потерявшего required
support, либо полностью отклоняется safe error. Никогда не возвращается
claim, который выглядит current, но опирается только на stale derived data.

## 12. Privacy и security constraints

Self Model является особо чувствительным derived layer, даже если canonical
notes уже приватны.

### Запрещено

- диагнозы, therapy/mental-health conclusions, protected-trait inference,
  hidden personality vectors или universal user score;
- silently передавать claims, body или evidence refs новому provider;
- писать derived claim/confidence/status/relations в vault;
- логировать raw claim/body, private note content, full evidence bundles,
  secrets или absolute paths;
- расширять scope из внешнего URL/source provenance без reviewed user
  assertion;
- использовать Self Model как скрытый recommendation prompt для Assistant;
- принимать client-supplied UUID/path/body как authority вместо current scan.

### Обязательно

- local/offline read-only flow без network, LLM и write capability;
- explicit current UUID refs и no body duplication в evidence ref;
- bounded request/result/claim sizes и strict type validation;
- safe stable errors без raw exception details;
- отдельные role collections supporting/contradicting/contextual;
- no-store boundary для будущего Web projection, если она когда-либо будет
  отдельно approved;
- temporary-vault fixtures и отсутствие чтения/изменения
  `second-brain-vault` в tests/CI.

## 13. Future application API

### 13.1. Proposed read-only operation

Каноническое имя — `BuildSelfModel`. Его внешний смысл:

```text
BuildSelfModel(SelfModelRequest) -> SelfModelResult
```

Он:

- читает текущий configured vault через existing `VaultReader`;
- строит только in-memory derived result;
- может вернуть только bounded DTOs из раздела 4;
- не имеет mutation endpoint и не возвращает Safe Write receipt;
- не принимает policy, confidence formula, evidence weights или arbitrary
  metadata от client.

Ошибки — только bounded `SELF_MODEL_*` codes. Web/CLI serialization и
authentication/privacy boundary являются отдельными будущими задачами и не
добавляются в #81.

### 13.2. Необходимая policy gate

Внутренний composition передаёт approved `SelfModelPolicy`. Нельзя считать
отсутствующие policy fields «нейтральными defaults»: это скрыло бы product
decision и сделало #82 необратимо неоднозначным. До решения policy operation
должна fail closed с `SELF_MODEL_POLICY_UNAVAILABLE`.

## 14. Compatibility с Assistant / Simulate Me / Compare

Self Model только supplies labelled personal context. Он не меняет semantics
будущих режимов:

| Consumer | Что получает | Boundary |
| --- | --- | --- |
| `Assistant` | task, constraints и при явном запросе derived claims с UUID/evidence context | строит independent recommendation; habitual claim не является recommendation |
| `Simulate Me` | current evidence/claims для prediction likely user choice | output labelled prediction, может abstain при insufficient/conflict; prediction не fact и не advice |
| `Compare` | два независимых branch outputs и Self Model evidence context | Assistant branch не видит hidden Simulate output; disagreement показывается, не схлопывается |

Особые правила:

- `user likely prefers X` не означает `system should recommend X`;
- `observed_decision` не переписывает goal и не создаёт imperative;
- preference против stated goal показываются одновременно с temporal/evidence
  context;
- fresh-looking или high-confidence claim не получает priority над
  independent recommendation автоматически;
- Self Model не принимает output одного consumer как canonical evidence.

Реализация этих consumers, retrieval, prompt assembly и cross-branch isolation
не входят в #81.

## 15. ACCEPT / DEFER / HUMAN_REQUIRED matrix

| Area | Verdict | Contract boundary |
| --- | --- | --- |
| Vault canonical authority | `ACCEPT` | Только current validated enrolled notes из `scan -> build_report`; vault остаётся единственным source of truth |
| Derived/rebuildable model | `ACCEPT` | On-demand immutable DTO; no write-back, profile file, daemon или durable model DB |
| Enrollment/Stage 1-3 eligibility | `ACCEPT` | Existing exact marker, pairs, typed Journal/Outcome and current relation validation; no new fields/enums |
| UUID explainability | `ACCEPT` | Every emitted claim has non-empty supporting current canonical UUID refs |
| Supporting vs contradicting | `ACCEPT` structurally | Separate role collections, no overlap, no silent majority resolver; relation predicate itself is human-owned |
| Temporal input | `ACCEPT` | `evidence_at`/precision only; `unknown` remains unknown; `created` is never fallback |
| Derived dimensions | `ACCEPT` as proposed taxonomy | Five bounded derived dimensions; no YAML ontology, diagnosis or personality dimensions |
| Confidence shape | `ACCEPT` as envelope | Null/unassessed or policy-bound score; no formula, weight or threshold selected |
| Search/LLM/cache authority | `ACCEPT` | Candidate-only/non-canonical; #82 reads current report and does not rely on them |
| Fail-closed result | `ACCEPT` | Invalid/incomplete eligible evidence or missing policy rejects whole unsafe result |
| Read-only API name | `ACCEPT` as proposal | `BuildSelfModel(SelfModelRequest) -> SelfModelResult`; no write API |
| Claim wording/aggregation | `HUMAN_REQUIRED` | Free-text assertions lack a merged deterministic aggregation contract; blocks pattern/rule part of #82 |
| Numeric confidence formula | `HUMAN_REQUIRED` | Formula and interpretation are not in merged contracts; blocks assessed confidence implementation in #82 |
| Evidence weights | `HUMAN_REQUIRED` | Provenance classes intentionally do not define universal strength; blocks scoring/comparison |
| Recency weights | `HUMAN_REQUIRED` | No merged decay/recency policy; blocks time-weighted confidence/status |
| Contradiction definition | `HUMAN_REQUIRED` | Existing contracts do not define incompatible assertions; blocks relation classification |
| Stale threshold | `HUMAN_REQUIRED` | No claim-specific/global freshness threshold exists; blocks stale status |
| Supersede semantics | `HUMAN_REQUIRED` | Existing canonical schema has no supersede field; blocks superseded status/relation |
| Newer evidence rule | `HUMAN_REQUIRED` | `evidence_at` is time, not universal truth strength; blocks automatic precedence |
| Final status taxonomy | `HUMAN_REQUIRED` | Roadmap examples are proposals, not merged allowlist; blocks status field semantics |
| Persistent cache/Web/retrieval | `DEFER` | Separate future slices after measured need and privacy/deletion policy |
| Simulate Me/Compare/Calibration/Active Learning | `DEFER` | Consumers and calibration are later roadmap stages |
| External provider/LLM/embeddings | `DEFER`/`RED` | No provider or new personal-data boundary in #81/#82 |

`HUMAN_REQUIRED` rows are not resolved by the recommendation text below. They
remain explicit gates.

## 16. Exact implementation slice for #82

`#82` может стартовать только после того, как #81 merged/closed, все
`HUMAN_REQUIRED` решения resolved, exact policy versions доступны и current
main содержит этот contract. До этого #82 = `BLOCKED`.

### Allowed change set

Механический implementation slice должен быть ограничен:

- новым `src/second_brain/application/self_model.py` с DTO/use case/error
  boundary из раздела 4;
- focused `tests/test_self_model.py` на temporary vault fixtures;
- минимальным export/navigation change, если он нужен для application import.

Изменять существующие `domain.models`, `personal_memory.py`, `decision_journal.py`,
`timeline.py`, `search.py`, `ports.py`, `writes.py`, `Safe Write`, Web routes,
Search DTO или canonical YAML contract нельзя без отдельной approved scope.

### Required mechanics

1. Strictly validate `SelfModelRequest`, clock, policy identifiers и bounds до
   expensive work.
2. Пройти `VaultReader.scan() -> build_report()` на каждом execute.
3. Применить fail-closed integrity boundary, а не строить partial profile из
   incomplete report.
4. Собрать refs только из current eligible enrolled notes.
5. Не вызывать `SearchIndexPort`, `SearchHit`, LLM, transcription, writer,
   network или external provider.
6. Выполнить exact approved claim-generation/relation/confidence/status policy;
   никакого hidden fallback для unresolved RED semantics.
7. Требовать хотя бы один supporting UUID у каждого claim и валидировать
   раздельные evidence roles.
8. Сохранить exact known/unknown time semantics и не читать `created` как
   evidence time.
9. Возвратить complete bounded result или safe error; partial result должен
   быть невозможен.
10. Не создавать файл, cache, DB, profile, write receipt или vault mutation.

### Explicitly not part of #82

- Web Self Model/API, authentication or deployment;
- Self Retrieval and Search semantic expansion;
- embeddings/RAG/vector/graph DB/Redis;
- LLM claim generation or external provider integration;
- canonical note update, supersede workflow or inference write-back;
- Simulate Me, Assistant recommendation, Compare, calibration or learning UI.

## 17. Test matrix for #82

В #81 не создаётся искусственный runtime только ради тестов. После approval
будущая focused suite должна покрыть:

| Area | Scenario | Expected |
| --- | --- | --- |
| Request boundary | wrong DTO type, bool-as-int, `max_claims`/`max_evidence_refs_per_claim` outside `1..200` | safe invalid request; vault/policy not called |
| Clock/policy | naive clock, missing/unknown policy identifier | `SELF_MODEL_INVALID_CLOCK` или `SELF_MODEL_POLICY_UNAVAILABLE`; no result |
| On-demand | two builds after canonical note change | second result reflects current vault; no stale prior result |
| Legacy gate | no marker, wrong marker type/value, coincidental companion fields | note remains ordinary and never appears as evidence |
| Stage 1 eligibility | valid enrolled fact/statement for memory/preference/belief/goal | typed ref follows existing metadata; direct claim only under approved generation policy |
| Stage 2 eligibility | valid Journal and linked Outcome with current UUIDv7 target | decision/outcome refs retain distinct kinds and relation |
| Invalid evidence | malformed enrolled metadata/body, duplicate UUID, broken Outcome target | whole build fails closed; no partial profile |
| Explainability | every emitted claim has at least one current supporting UUID | missing support rejects claim/result; no path/snippet substitute |
| Role separation | same candidate appears in support and contradiction, ambiguous relation | result invalid or relation policy rejects; no overlap/majority fallback |
| Temporal exact | known RFC3339 `evidence_at`, differing `created` | output uses evidence time only; storage time cannot alter claim temporal context |
| Temporal unknown | `evidence_at: unknown` | literal unknown preserved; no created/UUID fallback; unknown count is visible |
| User confidence | Journal `Confidence` text changes while evidence is same | model confidence is not parsed or copied; policy-owned model value only |
| Contradiction/stale | policy-approved conflicting/freshness fixtures | exact approved policy result; no hidden threshold/recency behavior |
| Rebuild deletion | remove supporting canonical note, remove contradiction, delete derived result | claims/refs change from current scan; user note data is not deleted |
| Determinism | same snapshot, fixed clock, same policy, different filesystem enumeration | equivalent DTOs and stable ordering |
| Bounds | oversized claim/body/result and excessive refs | bounded safe error; no truncation that pretends completeness |
| Authority isolation | fake SearchHit/cache/LLM output presents stronger-looking claim | ignored/not accepted as evidence; no provider/network call |
| Privacy | raw exception, body/path/secret in failure | stable safe error without sensitive diagnostic details |
| Anti-echo-chamber | habitual evidence says X, independent recommendation says Y | future consumer contract preserves both; Self Model never emits recommendation |
| Regression | existing Stage 1-3 and Search/Retrieval suites | unchanged current semantics and full suite green |

No test may use the real `second-brain-vault`, network credentials, live provider,
or a fake persistent Self Model database.

## 18. HUMAN_REQUIRED decision memo

Ниже перечислены решения, которые не следуют однозначно из merged contracts.
Рекомендации являются proposals для human review, не approvals. Для старта
#82 каждое решение должно получить explicit owner decision и versioned policy
identifier.

### 18.1. Numeric confidence formula

**Вопрос**

Как вычислять `SelfModelConfidence.score`, если numeric score вообще нужен?

**Известные факты**

- merged contracts разделяют user confidence и model confidence;
- evidence kinds описывают provenance, а не universal truth strength;
- один claim может иметь supporting, contradicting и unknown-time refs;
- малый sample нельзя представлять как objective probability.

**Вариант A — qualitative-only**

Хранить counts и policy-bound qualitative assessment; `score` всегда `None`.

**Вариант B — bounded evidence score**

Ввести human-approved numeric score из support/contradict/time inputs с
explicit bounds и `confidence_policy` version.

**Вариант C — calibrated probabilistic score**

Показывать probability-like value только после отдельной calibration/sample
policy и обозначать её как model estimate, не truth.

**Компромиссы**

- A минимизирует ложную точность, но ограничивает будущие consumers;
- B удобен для сортировки, но легко создаёт иллюзию объективной вероятности;
- C семантически богаче, но требует исторических samples, calibration и
  дополнительных lifecycle decisions.

**Рекомендация**

Начать с A или с B только при явном numeric interpretation disclaimer;
не считать numeric score обязательным до evidence/calibration policy.

**Какие части #82 блокируются**

`SelfModelConfidence` assessed state, score/bins, sorting по confidence,
threshold-based output и любые consumers, которые сравнивают score.

### 18.2. Evidence weights

**Вопрос**

Должны ли `explicit_user_fact`, `user_statement`, `observed_decision` и
`outcome_later_observation` иметь разные weights?

**Известные факты**

Evidence kind сейчас сообщает происхождение записи, а не truth. Reviewed
decision — observation поведения, но не permanent preference. User statement
может быть самым прямым выражением текущего намерения.

**Вариант A — equal descriptive counts**

Не присваивать kinds numeric weights; показывать role/counts.

**Вариант B — fixed provenance weights**

Назначить глобальные веса evidence kinds для всех dimensions.

**Вариант C — claim/context-specific weights**

Вес зависит от dimension, domain, decision/outcome context и policy version.

**Компромиссы**

A прост и прозрачен, но не различает observation и statement; B прост в коде,
но превращает provenance label в скрытую ontology; C гибок, но труден для
объяснения и требует больше product policy.

**Рекомендация**

Не вводить универсальное «observed всегда сильнее statement»; если weighting
нужен, выбрать explicit claim/context-specific policy и показывать refs.

**Какие части #82 блокируются**

Score, conflict resolution, pattern aggregation и порядок/фильтрация claims.

### 18.3. Recency weights

**Вопрос**

Как, если вообще, `evidence_at` должен влиять на силу текущего claim?

**Известные факты**

- known `evidence_at` различает событие/утверждение по времени;
- unknown time должен остаться unknown;
- `created` не является fallback;
- merged contracts не задают decay, horizon или validity interval.

**Вариант A — no recency weighting**

Время показывается в temporal context, но не меняет score.

**Вариант B — one global decay**

Одна фиксированная decay function для всех claim dimensions.

**Вариант C — claim-specific freshness**

Отдельная policy по dimension/domain/horizon, где unknown time не получает
искусственную дату.

**Компромиссы**

A прозрачен, но старые и новые assertions равны; B предсказуем, но
неправдоподобен для goals/beliefs разного lifecycle; C точнее, но сложнее и
может стать скрытой validity ontology.

**Рекомендация**

До отдельной freshness policy использовать time как explanation only и не
делать «newer is stronger» default.

**Какие части #82 блокируются**

Recency score, freshness ordering, stale detection и status assignment.

### 18.4. Contradiction definition

**Вопрос**

Какие два canonical evidence считать contradicting в одном claim?

**Известные факты**

- existing free-text body не содержит universal contradiction marker;
- разные domains, situations и horizons могут быть одновременно valid;
- `evidence_kind` не является polarity;
- majority vote не утверждён.

**Вариант A — semantic incompatibility in same scope**

Считать contradiction только при явно несовместимых assertions в одинаковом
dimension/domain/context/horizon.

**Вариант B — opposite normalized value**

Считать contradiction при разных normalized options/phrases по deterministic
rule.

**Вариант C — explicit user-confirmed conflict only**

Признавать contradiction только если canonical reviewed content или отдельная
утверждённая relation явно это сообщает.

**Компромиссы**

A лучше отражает смысл, но требует scope/parser semantics; B механичен, но
даёт false conflicts на синонимах и контексте; C safest, но пропускает
неявные contradictions.

**Рекомендация**

Conservative same-scope explicit incompatibility; lexical difference сама по
себе не должна становиться contradiction.

**Какие части #82 блокируются**

Наполнение `contradicting_evidence`, conflict status, confidence и Compare
explanation.

### 18.5. Stale threshold

**Вопрос**

Когда claim/evidence становится stale?

**Известные факты**

- existing notes не имеют Self Model `valid_until`;
- `updated` — storage change и не доказывает изменение смысла;
- preference, belief и goal имеют разные natural lifecycles;
- roadmap examples `stale` — design proposal, не merged enum.

**Вариант A — no automatic stale in v1**

Показывать evidence time и unknown time, но не выдавать stale status.

**Вариант B — global duration**

Один threshold от latest known `evidence_at` до build time.

**Вариант C — claim-specific threshold**

Разные freshness policies по dimension/domain/context с explicit unknown
handling.

**Компромиссы**

A безопасен, но не сообщает obsolescence; B прост, но создаёт false stale;
C богаче, но требует product lifecycle decisions и может быть трудно
объясним.

**Рекомендация**

Не помечать автоматически stale без explicit reviewed lifecycle/freshness
policy; unknown time нельзя считать stale только из-за отсутствия даты.

**Какие части #82 блокируются**

Freshness status, stale filters, thresholded confidence и Active Learning
triggers.

### 18.6. Supersede semantics

**Вопрос**

Когда новая assertion supersedes старую и где это relation хранится?

**Известные факты**

- canonical schema не имеет `supersedes` field;
- Stage 2 `decision_id` связывает Outcome с Decision, но не supersedes
  preference/belief/goal;
- old notes нельзя удалять;
- `created`, `updated` и newer `evidence_at` не определяют supersede сами.

**Вариант A — explicit canonical relation**

Добавить в будущем отдельный reviewed relation contract.

**Вариант B — newer same-scope assertion auto-supersedes**

Выводить relation из date/domain/self_kind/body.

**Вариант C — no supersede in Self Model v1**

Сохранять competing assertions и не выдавать superseded status.

**Компромиссы**

A требует отдельного canonical design/schema decision; B скрывает product
meaning и рискует стереть nuance; C safest и сохраняет evidence, но ограничивает
currentness semantics.

**Рекомендация**

Не делать automatic supersede в #82; explicit relation требует отдельного
approved contract и не должен появиться скрыто в Self Model.

**Какие части #82 блокируются**

Superseded relation/status, removal of old claims from current context и
future correction workflow.

### 18.7. Правило «новое evidence сильнее старого»

**Вопрос**

Должно ли более новое known `evidence_at` всегда иметь больший authority?

**Известные факты**

- `evidence_at` говорит, когда evidence относится к assertion/observation;
- newer goal может coexist с older preference в другом context;
- unknown time не может быть ordered;
- merged contracts не задают universal precedence.

**Вариант A — always newer wins**

Простое recency precedence для одного dimension.

**Вариант B — never newer wins automatically**

Время — explanation only; competing evidence сохраняется.

**Вариант C — conditional precedence**

Новизна учитывается только при explicit same-scope/context policy.

**Компромиссы**

A легко реализовать, но опасно для nuance; B fail-safe, но не моделирует
очевидные updates; C реалистичнее, но требует контекстной policy.

**Рекомендация**

Не вводить universal newer-wins rule; при необходимости выбрать explicit
conditional policy с explainable refs.

**Какие части #82 блокируются**

Claim grouping, contradiction/supersede resolution, recency weighting и
currentness status.

### 18.8. Final status taxonomy

**Вопрос**

Какие bounded status values должен возвращать `SelfModelStatus.code`?

**Известные факты**

- roadmap перечисляет `current`, `stale`, `conflicted`, `superseded`,
  `unresolved` как proposal;
- ни один из этих values не является current canonical `EvidenceKind` или
  `SelfKind`;
- status зависит от unresolved contradiction/stale/supersede semantics;
- status не должен выглядеть как psychological label.

**Вариант A — roadmap lifecycle statuses**

`current | stale | conflicted | superseded | unresolved`.

**Вариант B — evidence assessment statuses**

`supported | contested | insufficient | unresolved`, без обещания
«currentness».

**Вариант C — no status taxonomy in v1**

Возвращать только evidence roles, temporal context и policy-bound confidence;
status field остаётся `not_assessed`/отсутствует до отдельного slice.

**Компромиссы**

A совместим с roadmap, но звучит как lifecycle truth; B лучше отделяет
evidence assessment, но всё ещё требует contradiction rules; C наиболее
честен, но усложняет будущий consumer contract.

**Рекомендация**

Не использовать `current` как молчаливый default. Предпочтителен
evidence-assessment vocabulary вроде B, если owner явно примет его и свяжет
с конкретными predicates.

**Какие части #82 блокируются**

`SelfModelStatus` allowlist, serialization, filtering, Assistant/Simulate Me
consumer behavior и API compatibility.

### 18.9. Claim wording, aggregation и minimum sample

**Вопрос**

Как из existing human-readable body и Stage 2 options получить bounded claim
text, pattern/rule и minimum evidence sample?

**Известные факты**

- Stage 1 body не имеет отдельного machine claim field;
- Stage 2 has structured options/chosen option, но один decision не доказывает
  habit;
- generic LLM output не canonical authority и external provider запрещён в
  этом slice;
- free-text normalization не равна semantic equivalence.

**Вариант A — direct assertion projection**

Для preference/belief/goal сохранять bounded human wording как explanation;
behavioral pattern и decision rule defer.

**Вариант B — deterministic exact grouping**

Группировать только exact normalized canonical options/labels при human-approved
minimum sample и same-scope policy.

**Вариант C — semantic parser/LLM**

Использовать semantic extraction, но с отдельными provider, privacy, prompt,
validation и non-canonical boundaries.

**Компромиссы**

A безопаснее и объяснимее, но ограничивает pattern coverage; B mechanical,
но может дать false equivalence; C богаче, но существенно расширяет risk и
не является mechanical no-provider #82.

**Рекомендация**

Для v1 начать с A и явно не утверждать behavioral pattern/decision rule без
approved B-like policy; C не входит в #82.

**Какие части #82 блокируются**

Claim text, candidate grouping, minimum sample, dimensions
`behavioral_pattern`/`decision_rule` и `represented_evidence_count`.

## Decision gate summary

На текущем design этапе разрешены и зафиксированы: canonical/derived boundary,
eligible input boundary, UUID explainability, separate evidence roles,
temporal unknown semantics, bounded DTO shape, fail-closed behavior, privacy
constraints и future read-only API. Не утверждены автоматически: formula,
weights, recency, contradiction, stale, supersede, newer-wins, status и claim
aggregation semantics из раздела 18.

До owner resolution этих вопросов #82 остаётся blocked согласно его issue
contract. Этот документ не реализует runtime и не меняет canonical vault.
