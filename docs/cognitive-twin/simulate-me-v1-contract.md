# Simulate Me v1 — approved mechanical design contract

Статус этого документа: **DESIGN / APPROVED MECHANICAL CONTRACT**. Он закрывает
design часть issue [#100](https://github.com/MikeMoore1337/second-brain/issues/100) и
фиксирует ровно один provider-free Stage 6 policy. Runtime implementation,
Web/CLI projection и issue [#101](https://github.com/MikeMoore1337/second-brain/issues/101)
этим документом не создаются.

Точный status snapshot: current `main` commit
`3cc06db5b3490a0af9ebb9a1e14aac659b6e44f9`. Stage 4 Self Model и Stage 5
Self Retrieval уже merged (#82/#85 и #89/#90/#91). Stage 5 current UUID
reread остаётся authority для контекста; этот contract не расширяет Stage 4/5
semantics.

## 1. Цель и safety boundary

Simulate Me v1 может вернуть только производный **ПРОГНОЗ** выбора caller-owned
option или bounded abstention. Результат не является рекомендацией, советом,
«лучшим» или «оптимальным» вариантом и никогда не записывается обратно в
canonical vault.

В v1 нет:

- LLM, provider, внешней сети, embeddings, RAG или vector DB;
- cache, persistent model, prediction history или нового canonical field;
- frequency/majority/recency/evidence-kind weighting, hidden tie-break или
  behavioral inference;
- confidence score, процента, порога, calibration или числовой вероятности;
- stale/currentness/superseded/contradiction inference и validity intervals;
- recommendation branch, automatic write-back или background prediction.

Stage 5 current UUID reread является единственным authority для personal
context. Любой missing/changed/malformed current context даёт abstention, а не
убедительный prediction.

## 2. Caller-owned request DTO и точные bounds

Caller передаёт bounded literal task и явный список candidate options. Server не
создаёт semantic option identity и не сохраняет `id`.

```text
SimulateMeOption {
  id:    string  # ASCII [A-Za-z0-9][A-Za-z0-9._:-]{0,63}; unique in request
  label: string # 1..256 UTF-8 bytes after validation/normalization
}

SimulateMeRequest {
  query:   string                  # 1..4096 UTF-8 bytes after normalization
  options: list[SimulateMeOption]  # 1..8 items, caller order preserved
}
```

Request rules:

1. `id` обязателен, проверяется как ASCII allowlist и уникален только внутри
   текущего request. Он request-local, non-canonical и не появляется в vault.
2. `label` и `query` должны быть strings, не blank после normalization и не
   превышать указанный UTF-8 byte bound. Extra fields, missing fields, list/map
   вместо scalar и неверные types отклоняются.
3. В text fields запрещаются C0/C1 controls, `DEL` и Unicode category `Cf`.
   Это transport validation, а не semantic matching.
4. Deterministic normalization ровно такая: strict UTF-8 decode; Unicode NFC;
   `strip()` Unicode whitespace по краям; внутренние whitespace не меняются.
   После этого label/query не должны быть blank. Нет lower/casefold, locale
   equivalence, transliteration, punctuation rewriting, whitespace collapse или
   другой неявной нормализации.
5. Дубликаты normalized labels не исправляются и не выбираются скрытым
   tie-break: если они поддерживают разные option ids, результат —
   `multiple_options_supported`.

`query` ограничивает и фиксирует literal task для DTO boundary, но не даёт
дополнительного matching authority: option selection строится только из
описанного current Self Model evidence.

## 3. Server-owned context и eligibility

Server строит context через существующий Stage 5 flow и заново проверяет
current canonical UUIDs. Caller не может передать evidence refs, claim text,
policy/version, confidence, derivation или option authority.

Только current direct Self Model dimensions `preference` и `goal` имеют право
поддержать option. Claim должен быть прямым current Self Model claim и пройти
существующие Stage 4/5 validation.

`belief` может быть показан как contextual evidence, но сам по себе никогда не
выбирает option и не увеличивает support. `decision_rule` и
`behavioral_pattern` не являются eligible dimensions и не участвуют в
selection. Никакое evidence kind не получает больший вес.

`evidence_at: unknown` не является automatic abstention. Если такой evidence
иначе даёт ровно один поддержанный option, допускается `prediction` с явной
temporal caveat. Более новое evidence не сильнее старого автоматически.

## 4. Exact matching и deterministic algorithm

Для каждого caller option вычисляется `normalized_label` по разделу 2. Для
каждого eligible current direct claim вычисляется та же normalization. Support
возникает только при exact whole-string equality:

```text
normalize(claim.text) == normalize(option.label)
```

Запрещены substring/prefix matching, fuzzy matching, aliases, synonyms,
stemming, embeddings, semantic parser, LLM matching и locale semantic
equivalence. Exact matching для разных options не разрешается ranking-ом.

Алгоритм Stage 6:

1. Проверить request bounds, unique ids и server-owned current-context
   integrity. При invalid/incomplete current context вернуть abstention
   `insufficient_or_invalid_current_context`.
2. Выполнить exact whole-label matching только по direct `preference`/`goal`.
   Собрать support refs по option id. Несколько canonical refs, совпавших с
   одним label, образуют один distinct option.
3. Если distinct supported option ровно один, вернуть `kind=prediction` с
   этим caller option.
4. Если distinct supported options равен нулю, вернуть abstention
   `no_matching_evidence`.
5. Если distinct supported options два или больше, вернуть abstention
   `multiple_options_supported`.
6. Не применять count/frequency/recency/evidence-kind веса, hidden tie-break,
   behavioral inference или confidence threshold. Сортировка evidence refs
   для deterministic serialization выполняется только по canonical UUID
   string; она не меняет selection.

Integrity failure не маскируется как prediction. Для любого output соблюдаются
exact invariants: `prediction` имеет ровно один `selected_option` и
`abstention_code=null`; `abstention` имеет `selected_option=null` и ровно один
abstention code.

## 5. Result DTO

```text
SimulateMeEvidenceRef {
  claim_id:       UUID7 string
  dimension:      "preference" | "goal"
  note_ids:       list[UUID7 string] # 1..20, canonical current refs
  evidence_at:    ISO-8601 aware datetime | "unknown"
}

SimulateMeContextualEvidenceRef {
  claim_id:       UUID7 string
  dimension:      "belief"
  note_ids:       list[UUID7 string] # 1..20, canonical current refs
  evidence_at:    ISO-8601 aware datetime | "unknown"
}

SimulateMeTemporalCaveat {
  code:           "evidence_at_unknown"
  claim_id:       UUID7 string
}

SimulateMeResult {
  kind:                         "prediction" | "abstention"
  selected_option:              SimulateMeOption | null
  evidence_refs:                list[SimulateMeEvidenceRef] # 0..20
  contextual_evidence_refs:     list[SimulateMeContextualEvidenceRef] # 0..20
  temporal_caveats:             list[SimulateMeTemporalCaveat] # 0..20
  abstention_code:              "no_matching_evidence"
                                | "multiple_options_supported"
                                | "insufficient_or_invalid_current_context"
                                | null
  derivation_version:           "simulate-me-v1"
  policy_id:                    "simulate-me-direct-exact-v1"
  policy_fingerprint:           "sha256:" + 64 lowercase hex chars
}
```

`evidence_refs` содержит только refs, реально поддерживающие один или несколько
matched options. `contextual_evidence_refs` может содержать только `belief` и
никогда не участвует в algorithm. Все три lists ограничены 20 entries; каждая
`note_ids` list ограничена 20. Duplicate UUIDs внутри одного ref запрещены.

Для `kind=prediction` `selected_option` является точной caller-owned парой
`{id,label}` из request; server не редактирует label и не выдаёт новый id.
Для `kind=abstention` selected option отсутствует. Result не содержит поля
`confidence`, `score`, `probability`, `recommendation`, `advice`, `best` или
`optimal`.

### Policy identity and fingerprint

Policy identifiers фиксированы: `derivation_version=simulate-me-v1` и
`policy_id=simulate-me-direct-exact-v1`. Fingerprint вычисляется как SHA-256
UTF-8 bytes следующего exact canonical JSON (`sort_keys=true`, separators
`,`/`:`, без завершающего newline):

```json
{"abstention_codes":["no_matching_evidence","multiple_options_supported","insufficient_or_invalid_current_context"],"eligible_dimensions":["preference","goal"],"evidence_at_unknown":"temporal_caveat","matching":"exact-whole-label-v1","non_eligible_dimensions":["belief","decision_rule","behavioral_pattern"],"policy_id":"simulate-me-direct-exact-v1","recency":"disabled","selection":"one-distinct-option-or-abstain-v1","version":"1"}
```

Ожидаемый fingerprint:
`sha256:07aa1d0d57fdd2d009087c05423fc4eb9304da70e87f32b1790fbd4753f21c3a`.

## 6. Temporal and conflict policy

- `evidence_at: unknown` сохраняется в DTO и даёт только
  `evidence_at_unknown` caveat.
- Более новый timestamp не получает precedence.
- Нет stale/currentness/superseded inference, contradiction inference,
  validity interval, freshness score или conflict ranking.
- Если exact-match current evidence поддерживает разные candidate options,
  результат всегда `multiple_options_supported`.
- Если current UUID reread missing, changed, malformed, invalid или не может
  быть безопасно представлен bounded DTO, результат всегда
  `insufficient_or_invalid_current_context`.

## 7. No-side-effect boundary

Stage 6 implementation может читать только server-owned Stage 5 current context
и direct Self Model projection через существующие boundaries. Он не вызывает
LLM/provider/network, не строит embeddings/RAG/vector index, не пишет vault,
не создаёт cache/DB/model/prediction history и не меняет canonical schema или
notes. Result является ephemeral derived DTO и rebuildable из current vault.

UI, если появится позднее, обязан показывать термин **ПРОГНОЗ** и сохранять
отдельное abstention state; он не может преобразовывать его в recommendation,
advice, best или optimal choice.

## 8. Deterministic test matrix

| Case | Expected mechanical result |
| --- | --- |
| valid 1..8 options, unique bounded ids | request accepted; ids remain request-local |
| missing/extra field, wrong type, blank text, bound overflow, bad id/control | safe request rejection; no context read |
| NFC/edge-trim equivalent text | exact match after documented normalization |
| case-only, substring, fuzzy, alias, synonym, stemming or internal whitespace change | no match; no semantic equivalence |
| one preference or goal exact match | `prediction`, that caller option |
| multiple refs supporting the same option | `prediction`, one distinct option |
| belief-only, decision_rule or behavioral_pattern evidence | cannot select; contextual-only or no match |
| no matched option | `abstention/no_matching_evidence` |
| two or more distinct matched options | `abstention/multiple_options_supported` |
| unknown evidence time with one match | prediction plus `evidence_at_unknown` caveat |
| current UUID missing/changed/malformed/integrity failure | `abstention/insufficient_or_invalid_current_context` |
| reorder evidence, newer/older timestamps, duplicate support refs | same selection; deterministic UUID ordering only |
| numeric confidence or recommendation-shaped output requested | field absent; contract remains prediction/abstention only |
| provider/network/LLM/embedding/cache/vault-write seam | no call and no side effect |
| policy serialization | exact version, id and fingerprint above |

## 9. ACCEPT / DEFER matrix

| Area | Status in Simulate Me v1 |
| --- | --- |
| explicit caller `{id,label}` options; request-local non-canonical ids | **ACCEPT** |
| exact whole-label matching with only NFC/edge-trim/control validation | **ACCEPT** |
| direct current Self Model `preference`/`goal` selection | **ACCEPT** |
| belief contextual-only; decision_rule/behavioral_pattern excluded | **ACCEPT** |
| one distinct option prediction; zero/multiple deterministic abstention | **ACCEPT** |
| unknown evidence time as caveat; no recency | **ACCEPT** |
| no confidence, ranking, stale/conflict/supersede inference or writes | **ACCEPT** |
| aliases, synonyms, fuzzy/semantic matching, LLM/provider/network | **DEFER** |
| majority/frequency/recency/evidence weighting or behavioral inference | **DEFER** |
| numeric confidence, calibration, prediction history | **DEFER** |
| recommendation, Compare, Active Learning or automatic write-back | **DEFER** |
| runtime/Web/CLI implementation | **DEFER to #101+** |
| additional canonical schema, persistence or external adapter | **DEFER** |

**HUMAN_REQUIRED: none.** This contract contains no unresolved owner choice.
Future semantic expansion requires a new explicit design decision and must not
be inferred by implementation.
