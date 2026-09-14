# Cognitive Twin: Growth Learning / Question v1

Статус: **DESIGN COMPLETE — Stage 11D0**.

Этот документ закрывает design/contract-only срез Issue #269. Он не добавляет runtime, HTTP API, UI, схему хранения, provider, фоновый процесс или запись в vault. Нормативными являются этот контракт, текущие реализованные контракты Stage 11A/11B/11C и их merged-код; при расхождении старой формулировки с merged-кодом приоритет имеет merged-код.

## 1. Решение и граница Stage 11D0

Growth Learning / Question v1 — это provider-free, детерминированная, owner-only проекция свежего текущего Growth-результата:

```text
fresh current Growth result
        │
        ▼
deterministic actionable Growth gap
        │
        ▼
zero or one derived question candidate
        │
        ▼
explicit owner action: ignore / reject / review / answer
        │
        ├─ optional approved mapping review handoff
        └─ optional approved Personal Memory review handoff
```

Candidate — это производное UX-состояние страницы. Он не является Goal, Memory, evidence, mapping, fact, belief, preference, rule или психологическим утверждением. Сам кандидат не имеет права записи.

В Stage 11D0 фиксируются только:

- источник authority, eligibility, closed taxonomy и безопасный приоритет;
- отдельная policy identity, canonical hashing, candidate identity и TTL;
- границы foreground-only UX, page/request memory и anti-annoyance;
- owner controls и явные handoff boundaries;
- будущие API/UI/security/test boundaries.

Не фиксируются и не реализуются runtime-классы, endpoint, экран, durable model, scheduler, telemetry, LLM/provider call или новый persistence layer.

## 2. Нормативные версии и флаги

| Item | Value |
|---|---|
| Learning contract | `growth-learning-v1` |
| Learning derivation version | `growth-learning-derivation-v1` |
| Learning policy id | `growth-learning-foreground-question-v1` |
| Learning policy fingerprint | `sha256:9b20c981ee4137b340ee2b183e32faec616dd258789dc84329a900bab0f8e829` |
| Growth source contract | `growth-engine-v1` |
| Growth source derivation | `growth-engine-derivation-v1` |
| Growth source policy | `growth-engine-explicit-relation-v1` |
| Candidate limit | `0/1` per explicit foreground request |
| Question TTL | `600` seconds, mechanically reused from Stage 8 |
| Wording | fixed provider-free Russian templates |
| Persistence | page/request memory only |
| Experiments | deferred |
| Runtime | not implemented |

The policy identity is the tuple `contract_version`, `policy_id`,
`derivation_version`, `policy_fingerprint`. The fingerprint is SHA-256 of
the policy definition below (the fingerprint is not self-included) as this
exact compact UTF-8 canonical JSON string, with sorted keys and no trailing
newline:

```json
{"answer":"explicit-owner-editable-ephemeral-v1","anti_annoyance":"foreground-one-candidate-v1","candidate_limit":1,"contract_version":"growth-learning-v1","dedupe":"basis-fingerprint-page-memory-v1","derivation_version":"growth-learning-derivation-v1","experiments":"deferred-v1","foreground":"explicit-owner-action-v1","invalidation":"current-growth-source-exact-v1","persistence":"ephemeral-no-store-v1","policy_id":"growth-learning-foreground-question-v1","source":"growth-engine-current-result-v1","ttl_seconds":600,"triggers":"mapping-conflict-mixed-changed-insufficient-v1","version":"1","wording":"fixed-provider-free-russian-v1"}
```

Fingerprint format everywhere in this contract is `sha256:<64 lowercase hex characters>`. Hashing is deterministic SHA-256 over UTF-8 canonical JSON: `sort_keys=true`, compact separators, no generated timestamp, no request id, no raw private value.

## 3. Source authority

### 3.1 Allowed authority

The only authoritative inputs for a candidate are:

1. a fresh, server-validated current Goal from Stage 11A;
2. a fresh, deterministic `GrowthEngineResultV1` / `GrowthGoalRelationResultV1` from Stage 11B;
3. the exact current `GrowthMappingRefV1` carried by that Growth relation, only when the mapping is current and valid.

The runtime MUST obtain the current sources itself for an explicit request. A browser-provided Growth DTO, stale page snapshot, label, raw note, or client-selected relation is not authority.

### 3.2 Forbidden authority

The following can never create or justify a Growth Learning candidate:

| Source | Why it is not authority |
|---|---|
| Growth Advisor recommendation, rationale, selected option or wording | Advisor is a separate explicit goal branch, not a relation/evidence source |
| Stage 9 / Stage 10C output | Outside the Growth relation contract |
| Stage 8 Simulate Me result | Separate choice-gap contract |
| Search, raw Journal, raw Outcome or raw note body | Not a validated current Growth source |
| historical-only or stale browser DTO | Cannot establish current authority |
| question text, prior answer or user sentiment | A candidate cannot become its own evidence |

No candidate may retain raw bodies, paths, front matter, provider output, Advisor wording, or a private source excerpt.

### 3.3 Current Goal and selection boundary

The request must identify one explicit foreground Goal source UUID, or the caller must explicitly select one before derivation. If several current Goals exist and no Goal is selected, the result is ordinary `no_candidate` with `GOAL_SELECTION_REQUIRED`; no hidden ranking, best-goal choice, or global interview is allowed. A missing or invalid Goal source is `GOAL_SOURCE_MISSING` / `GROWTH_SOURCE_UNAVAILABLE` as appropriate, never a generic question.

## 4. Growth states and actionable-gap taxonomy

The input state is the closed `GrowthRelationStateV1` set from Stage 11B:

```text
supports_goal
conflicts_with_goal
neutral_or_unknown
mixed_behavior
changed_behavior
behavioral_evidence_insufficient
goal_mapping_missing
goal_source_missing
goal_selection_required
not_comparable
```

Only five current, source-backed states can produce a candidate. The state itself is not a severity score and does not imply a diagnosis.

| Growth state | Candidate kind | Reason code | Required source condition | v1 result |
|---|---|---|---|---|
| `goal_mapping_missing` | `relation_review` | `missing_goal_mapping` | Current Goal, current comparable cohort and current behavioral option are present; no valid exact mapping exists | one candidate |
| `conflicts_with_goal` | `reflection` | `explicit_goal_conflict` | Current exact relation includes a current valid explicit mapping with conflict relation | one candidate |
| `mixed_behavior` | `context_clarification` | `mixed_behavior` | Current comparable behavioral reference is present and mixed state is deterministic | one candidate |
| `changed_behavior` | `context_clarification` | `changed_behavior` | Current and comparison references are present and changed state is deterministic | one candidate |
| `behavioral_evidence_insufficient` | `evidence_clarification` | `behavioral_evidence_insufficient` | Current Goal and the bounded Growth source explain that comparable behavioral evidence is insufficient | one candidate |
| `supports_goal` | — | — | No actionable gap | no candidate |
| `neutral_or_unknown` | — | — | No actionable gap | no candidate |
| `not_comparable` | — | — | Broad or unsafe comparison cannot justify a question | no candidate |
| `goal_source_missing` | — | — | Ordinary source/Goal UI requirement | no candidate |
| `goal_selection_required` | — | — | Ordinary explicit selection UI requirement | no candidate |

`goal_mapping_missing` is not `conflicts_with_goal`. Missing relation is always a relation-review opportunity and never evidence of conflict. `supports_goal`, `neutral_or_unknown`, and broad `not_comparable` must not be inflated into a question merely to fill an empty state.

### 4.1 Closed question kinds

The only v1 `kind` values are:

```text
relation_review
reflection
context_clarification
evidence_clarification
```

The following words and concepts are prohibited as kinds, reasons, or generated interpretations: `self_sabotage`, `discipline`, `motivation`, `bad_habit`, `weak_will`, `true_desire`, `personality`, diagnosis, weakness, vulnerability, manipulation, addiction, protected trait, employability, creditworthiness, political targeting, religious targeting.

### 4.2 Fixed Russian templates

Question text is selected from this closed provider-free set. It MUST NOT interpolate Goal labels, option labels, raw content, Advisor wording, probabilities, or psychological claims.

| Reason code | Fixed question |
|---|---|
| `missing_goal_mapping` | `Для текущего наблюдаемого варианта ещё не задано, как он относится к выбранной цели. Хочешь проверить эту связь?` |
| `explicit_goal_conflict` | `Для текущего варианта ранее была явно подтверждена связь «конфликтует с целью». Хочешь уточнить контекст или пересмотреть эту связь?` |
| `mixed_behavior` | `В сопоставимых ситуациях были разные варианты выбора. Хочешь добавить контекст, который помогает различать эти случаи?` |
| `changed_behavior` | `В историческом и текущем окнах наблюдались разные варианты выбора. Хочешь уточнить контекст этого различия?` |
| `behavioral_evidence_insufficient` | `Пока недостаточно сопоставимых решений для устойчивого наблюдаемого паттерна. Хочешь добавить контекст?` |

These templates are neutral reflection prompts. They do not claim why the owner acted, what the owner truly wants, or what the owner should do.

## 5. Deterministic derivation and precedence

### 5.1 One foreground request

Growth Learning is evaluated only after an explicit owner action, for example `Уточнить Growth`, against the currently selected Goal. It does not run on page load, polling, background refresh, scheduler, push, notification, Advisor completion, or automatic Goal selection. One explicit request can return zero or one candidate only.

There is no queue, interview, question carousel, multi-question batch, or global list. If more than one actionable relation is available, the deterministic precedence below chooses one candidate; it is not a severity ranking.

### 5.2 Precedence

The first eligible state wins:

1. `goal_mapping_missing`
2. `conflicts_with_goal`
3. `mixed_behavior`
4. `changed_behavior`
5. `behavioral_evidence_insufficient`

The safe-boundary rationale is: first offer review of an unclassified current relation, then reflect an already explicit conflict, then clarify ambiguity/change, then clarify evidence limits. This ordering must not be presented as a psychological or business priority.

For same-state ties, derive a stable sort key from current source references only:

```text
goal_source_uuid
cohort_fingerprint (empty sorts last)
behavioral_option_fingerprint (empty sorts last)
mapping_fingerprint (empty sorts last)
behavioral_reference_fingerprint (empty sorts last)
```

The sort key is only a deterministic tie-breaker. It must never select a different Goal implicitly.

### 5.3 No-candidate result

`GrowthLearningResultV1` is either `candidate` or `no_candidate`. The closed normal no-candidate codes are:

```text
QUESTIONS_DISABLED
GOAL_SOURCE_MISSING
GOAL_SELECTION_REQUIRED
NO_ACTIONABLE_GROWTH_GAP
GROWTH_STATE_NOT_COMPARABLE
UNSUPPORTED_GROWTH_STATE
CANDIDATE_ALREADY_PRESENT_IN_PAGE_MEMORY
```

`CANDIDATE_ALREADY_PRESENT_IN_PAGE_MEMORY` suppresses only an identical candidate within the same page operation. It is not a durable opt-out and must not be stored across pages or sessions.

## 6. Candidate DTO and projections

### 6.1 Normative candidate DTO

The minimum future DTO is:

```text
GrowthLearningCandidateV1 {
    contract_version: "growth-learning-v1"
    derivation_version: "growth-learning-derivation-v1"
    candidate_id: "gl1:<64 lowercase hex>"
    kind: relation_review | reflection | context_clarification | evidence_clarification
    reason_code: missing_goal_mapping | explicit_goal_conflict | mixed_behavior | changed_behavior | behavioral_evidence_insufficient
    growth_contract_version: "growth-engine-v1"
    growth_derivation_version: "growth-engine-derivation-v1"
    growth_policy_id: "growth-engine-explicit-relation-v1"
    growth_policy_fingerprint: sha256:<64 lowercase hex>
    goal_source_uuid: UUID
    goal_identity_fingerprint: sha256:<64 lowercase hex>
    growth_state: GrowthRelationStateV1
    cohort_fingerprint: sha256:<64 lowercase hex> | null
    behavioral_option_fingerprint: sha256:<64 lowercase hex> | null
    behavioral_reference_fingerprint: sha256:<64 lowercase hex> | null
    mapping_id: UUID | null
    mapping_fingerprint: sha256:<64 lowercase hex> | null
    question: bounded fixed Russian string
    basis_fingerprint: sha256:<64 lowercase hex>
    issued_at: RFC3339 UTC timestamp
    expires_at: RFC3339 UTC timestamp
}
```

The fields required by the Issue are present without relaxation: contract version, candidate identity, kind, reason, Goal identity, Growth policy identity, Growth state, cohort/option/mapping references, question, basis, and TTL timestamps.

The DTO MUST NOT contain:

- raw Goal, option, note, journal, outcome, source body, path or front matter;
- provider, model, token, Advisor, rationale, prompt or hidden policy output;
- a canonical fact, evidence kind, self kind, belief, preference, rule or psychological claim;
- a write receipt, vault path, durable suppression flag or experiment id.

Human-readable Goal and option labels are allowed only in a transient owner-facing projection assembled at render time from current validated source. Labels are not candidate authority, identity, basis, storage, or write input.

### 6.2 Field population rules

`mapping_id` and `mapping_fingerprint` are populated only for the exact current `conflicts_with_goal` relation. They are null for missing-mapping, mixed, changed and insufficient candidates. An implementation must not copy a stale or merely nearby mapping into a candidate.

`cohort_fingerprint`, `behavioral_option_fingerprint` and `behavioral_reference_fingerprint` must bind to the current Growth result that made the candidate eligible. If the required reference is unavailable or cannot be validated, return a safe no-candidate or the relevant source error; do not produce a generic question.

### 6.3 Candidate identity and basis

Build a basis object from only these canonical fields:

```text
contract_version
derivation_version
learning_policy_id
learning_policy_fingerprint
growth_contract_version
growth_derivation_version
growth_policy_id
growth_policy_fingerprint
goal_source_uuid
goal_identity_fingerprint
growth_state
cohort_fingerprint
behavioral_option_fingerprint
behavioral_reference_fingerprint
mapping_id
mapping_fingerprint
kind
reason_code
```

`basis_fingerprint = SHA256(canonical_json(basis))` and `candidate_id = "gl1:" + lowercase_hex(SHA256(canonical_json(basis)))`. `issued_at`, `expires_at`, request ids, labels and answer text are not part of identity. This gives stable re-derivation for the same current source and does not let a timestamp or display copy create a new question.

The candidate is bounded to the mechanical Stage 8 candidate envelope: at most `16 KiB` serialized UTF-8 and one candidate. The fixed `question` is at most `1024` UTF-8 bytes. A future result envelope is at most `32 KiB`; exceeding it is `RESULT_TOO_LARGE` and must fail closed.

## 7. Freshness, TTL and invalidation

### 7.1 TTL

`expires_at = issued_at + 600 seconds`. The `600`-second TTL is reused mechanically from Stage 8 as a bounded UX safety measure. Stage 8’s contract id, reason taxonomy, candidate id namespace, semantic source, history, or ranking are not reused.

An expired candidate has no authority. It cannot be answered, reviewed, or used to retarget a new Goal. The next explicit foreground request may derive a new candidate from a fresh Growth result.

### 7.2 Invalidation

Invalidate the candidate immediately when any basis or source authority changes:

| Change | Required outcome |
|---|---|
| Goal edit, delete, replacement, source UUID change or identity fingerprint change | stale; no retarget |
| Growth derivation/policy change | policy mismatch or stale; no retarget |
| current cohort, behavioral pattern, current option, temporal comparison or provenance change | stale; no retarget |
| mapping created, superseded, invalidated, deleted or relation replaced | stale; no retarget |
| mapping store unavailable/corrupt | source error; no fallback question |
| page/session candidate already resolved | no second resolution in that page operation |
| TTL reached | expired; no action |

Revalidation compares the full relevant source identity, not only the visible question or Goal label. A stale candidate is never silently rebound to the latest Goal, option, mapping or policy.

### 7.3 Memory boundary

The operation may keep the current candidate and a resolved-candidate set in page/request memory only. It MUST NOT write a database row, JSONL entry, cache record, browser storage value, vault note, telemetry event, user profile flag, suppression history, or cross-session question history. A candidate may reappear after a new page/session if the fresh source still qualifies.

## 8. Owner controls and resolution

The future foreground UI exposes explicit owner controls:

| Control | Meaning | Automatic side effect |
|---|---|---|
| `ignore` | Do not act on this candidate now | none |
| `reject` | Decline this candidate/question | none; not evidence and not an opt-out history |
| `review` | Open the applicable existing review boundary | no write; current source is revalidated |
| `answer` | Capture an editable transient answer | no evidence, mapping, Goal or Memory mutation |

An operation must reject a second resolution of the same candidate within the same page operation. `ignore` and `reject` have no hidden telemetry, evidence, relation, ranking, suppression, or preference semantics.

An answer is an ephemeral owner draft, bounded to `4096` UTF-8 bytes, validated with existing strict text controls. It can be edited or discarded. It is not canonical and cannot itself prove a fact, behavior, belief, preference or Goal relation.

## 9. Approved handoffs

### 9.1 Relation review

For `relation_review`:

1. the owner selects `review`;
2. the system opens the existing Stage 11B mapping review projection;
3. the server revalidates the current Goal, cohort, option, source policy and mapping store against the candidate basis;
4. the owner chooses the relation explicitly;
5. the owner confirms the write in the existing Stage 11B flow;
6. only then may the existing append-only mapping flow create the mapping.

The candidate itself never creates, edits, deletes, supersedes or invalidates a mapping. If revalidation fails, return `STALE_CANDIDATE`, `GOAL_SOURCE_CHANGED`, `BEHAVIORAL_SOURCE_CHANGED`, `MAPPING_SOURCE_UNAVAILABLE` or `MAPPING_STORE_CORRUPT` as applicable.

### 9.2 Conflict reflection

For `explicit_goal_conflict`, an answer does not replace, invalidate, weaken or confirm the mapping; it does not change the Goal, preference or behavior. The owner may explicitly reopen the existing mapping review or use an approved reflection route. Any change still requires current revalidation, owner choice and explicit confirmation.

### 9.3 Context and evidence clarification

For `mixed_behavior`, `changed_behavior` and `behavioral_evidence_insufficient`:

1. owner answer becomes an editable transient draft;
2. owner may choose the existing Personal Memory review boundary;
3. the existing metadata review and `prepare` diff run before any write;
4. the owner explicitly confirms or discards;
5. only the existing Safe Write flow may apply the confirmed draft.

The question does not assign `evidence_kind`, `self_kind`, domain, time, Goal, belief, preference or rule. It does not directly write Personal Memory, vault content, evidence, Goal Progress or an experiment.

### 9.4 Stage 8 coexistence

Stage 8 remains the separate Simulate Me choice-gap flow. Mechanical reuse is limited to safe implementation patterns: deterministic hashing, `600`-second TTL, page-memory resolution, strict bounds, review handoff and current-source revalidation. The two systems retain separate:

- contract/policy ids and fingerprints;
- candidate id namespaces (`gl1:` versus `apl1:`);
- source authority and reason taxonomy;
- question kinds and fixed wording;
- triggers, resolution history and UI actions;
- no-candidate/error codes.

There is no global ranking, merged reason, shared history, cross-trigger deduplication, or conversion of a Stage 8 candidate into a Growth candidate.

## 10. Advisor separation

Growth Advisor and Growth Learning are independent explicit surfaces:

- Advisor is not a trigger for Growth Learning.
- Advisor wording, rationale, selected option, provider output and policy never enter a candidate basis.
- A Growth Learning candidate does not call Advisor, auto-open Advisor, or generate a recommendation.
- An Advisor recommendation cannot become a question merely because it exists.
- Advisor and Learning have different policy ids, source contracts, fingerprints, DTOs, resolution actions and TTL namespaces.

The user can explicitly use either surface, but one surface cannot silently chain into the other.

## 11. Privacy, safety and anti-annoyance

The contract forbids diagnosis, psychological profiling, persuasion optimization, manipulation, reinforcement learning, adaptive nudging, hidden policy, protected-trait inference, addiction framing, vulnerability exploitation, employability/creditworthiness decisions, or political/religious targeting.

There is no automatic reinforcement or adaptive question selection. The policy, templates, precedence and candidate limit are fixed for v1. Repeated foreground requests are allowed to produce a fresh candidate only when the current source and page/session boundary allow it; there is no background retry or repeated prompt loop.

“No candidate” is a valid product result. The implementation must prefer no candidate over a generic, speculative, moralizing or psychologically loaded question.

## 12. Implemented API, UI and browser boundary

The bounded Stage 11D/11E runtime implements this boundary.

### 12.1 API shape

The owner-only API exposes two endpoints analogous to Stage 8:

```text
POST /api/growth-learning/questions
POST /api/growth-learning/questions/resolve
```

The request contains only the explicit selected Goal source UUID, contract version and bounded resolution data. It must not accept a Growth result, mapping, labels, raw source body, candidate basis or source authority from the browser. The server obtains and validates the current sources.

Required boundary properties:

- trusted Host and same-origin checks;
- purpose header `growth-learning-v1`;
- strict JSON with unknown fields rejected;
- explicit `Content-Type` and bounded raw body;
- no CORS, no cache and `Cache-Control: no-store`;
- no provider/network call;
- no scheduler, background endpoint or push channel;
- structured error code without private source details;
- current-source revalidation at resolution;
- no stale DTO acceptance and no client-side retargeting.

### 12.2 UI shape

The only entry point is an explicit owner action such as `Уточнить Growth` on the current Growth surface. A candidate may render inline or in an owner-opened panel. It must not appear as a popup, unsolicited notification, auto-open modal, page-load interview, Advisor follow-up, or background reminder.

The UI must make the source context and expiry understandable without exposing raw private data. Goal/option labels are a transient projection only. `ignore`, `reject`, `review` and `answer` must be explicit and reversible before any existing confirmation boundary.

## 13. Result and error contract

### 13.1 Result shape

```text
GrowthLearningResultV1 {
    contract_version: "growth-learning-v1"
    status: candidate | no_candidate
    candidate: GrowthLearningCandidateV1 | null
    no_candidate_code: closed code | null
}
```

Exactly one of `candidate` and `no_candidate_code` is non-null. A no-candidate result contains no raw source explanation, note excerpt, hidden confidence, private body or provider message.

### 13.2 Fixed error taxonomy

The runtime exposes only stable non-private codes from this closed v1 set:

```text
INVALID_REQUEST
GROWTH_SOURCE_UNAVAILABLE
GOAL_SOURCE_CHANGED
BEHAVIORAL_SOURCE_CHANGED
MAPPING_SOURCE_UNAVAILABLE
MAPPING_STORE_CORRUPT
POLICY_MISMATCH
STALE_CANDIDATE
CANDIDATE_EXPIRED
CANDIDATE_ALREADY_RESOLVED
INVALID_RESOLUTION
ANSWER_TOO_LARGE
RESULT_TOO_LARGE
INTERNAL_CONTRACT_VIOLATION
```

All source, policy, stale and size failures are fail-closed. They do not fall back to a generic question, a stale candidate, an Advisor response, a raw error, or an automatic write. Error responses contain safe field-level context only; they do not reveal paths, source bodies, credentials, provider output or private note content.

## 14. Verification matrix

The runtime slice has deterministic tests for:

| Area | Required checks |
|---|---|
| Eligibility | all ten Growth states; only five trigger states; missing mapping is not conflict |
| Precedence | one candidate; exact priority; stable tie-break; no hidden Goal selection |
| Determinism | same source/policy gives same question, basis and `gl1:` id; timestamp does not change identity |
| Hashing | canonical JSON, lowercase SHA-256, policy/basis vectors, no raw bodies |
| TTL | issued/expires boundary, `600` seconds, expired candidate cannot resolve |
| Drift | Goal, identity, behavior, cohort, option, mapping, policy and provenance changes fail closed; no retarget |
| Resolution | ignore/reject/review/answer; no second resolution in page memory; bounded answer |
| Handoffs | mapping review and Personal Memory review require current revalidation, prepare/diff and owner confirmation |
| Boundaries | Stage 8 namespace/policy/source remain separate; Advisor cannot trigger or word Learning |
| Security | Host/same-origin/purpose/strict JSON/body cap/no-store/unknown-field rejection |
| Privacy | forbidden taxonomy and raw/private fields rejected; no telemetry/persistence/RL/auto-nudge |
| Failure | exact error codes for every source, store, policy, stale, resolution and size failure |

No live provider, credential, vault, Git, scheduler or production write is
needed for the contract/runtime tests.

## 15. Exact next runtime scope

Stage 11D runtime is limited to one vertical slice:

1. provider-free application derivation from a fresh server-side Stage 11B result;
2. one explicit foreground request for one selected Goal;
3. in-memory candidate and resolution state;
4. strict DTO/error validation and deterministic tests;
5. owner controls with no automatic write;
6. handoff adapters to the existing Stage 11B mapping review and Personal Memory/Safe Write boundaries, without duplicating their write logic.

It does not include experiments, reminders, durable question history, Goal Progress, schema changes, new providers, Advisor integration, Stage 8 semantic changes, Stage 9/10 changes, Compare, vault changes, or auto-follow-up work.

## 16. Stage status and flags

| Stage / flag | Status after this contract |
|---|---|
| Stage 11A — current Goal source | COMPLETE |
| Stage 11B — Growth Engine and mapping flow | COMPLETE |
| Stage 11C0 — Growth Advisor design | COMPLETE |
| Stage 11C — Growth Advisor runtime | COMPLETE |
| Stage 11D0 — Growth Learning / Question design | COMPLETE |
| Stage 11D — Growth Learning runtime | COMPLETE |
| Stage 11E | COMPLETE |
| Stage 12+ | NOT STARTED |
| runtime/API/UI in this issue | NO |
| new dependencies | NO |
| provider/network/credentials | NO |
| schema/persistence | NO |
| vault/Safe Write mutation | NO |
| environment-variable change | NO; `env change required: no` |

The status above records the Stage 11D0 design and the bounded Stage 11D/11E
implementation. It does not authorize experiments, durable history, provider
integration or any Stage 12+ work.

## 17. Decision register: ACCEPT / CHANGE / RISK / DEFER

| Decision | Disposition (ACCEPT / CHANGE / RISK / DEFER) | Contract consequence |
|---|---|---|
| Stage 8 mechanical reuse | ACCEPT | Reuse hashing/TTL/page-memory/revalidation patterns only; keep independent identity and semantics |
| Trigger states | ACCEPT | Only missing mapping, explicit conflict, mixed, changed and insufficient evidence can produce a candidate |
| Question taxonomy | ACCEPT | Four closed kinds and five reason codes; no moralizing or psychological labels |
| Precedence | ACCEPT | Deterministic one-candidate order; not a severity or personalization score |
| Freshness and invalidation | ACCEPT | Current server-side Growth source; exact basis revalidation; no retarget |
| Owner controls | ACCEPT | Ignore/reject/review/answer with no automatic write or evidence |
| Relation handoff | ACCEPT | Existing Stage 11B review and append-only confirmation flow only |
| Personal Memory handoff | ACCEPT | Editable transient answer -> existing metadata review -> prepare/diff -> explicit Safe Write |
| Advisor separation | ACCEPT | No trigger, wording, policy, source or automatic chaining |
| Experiments | DEFER | No schema, lifecycle, scheduler, outcome or experiment id in v1 |
| Source race and future contract drift | RISK | Exact current-source/policy revalidation may invalidate a candidate; fail closed and never retarget |
| Runtime/API/UI scope | CHANGE | Issue #274 implements only the bounded vertical slice and its tests |
| Persistence and history | DEFER | Page/request memory only; no suppression, telemetry or cross-session history |
| Anti-annoyance | ACCEPT | Foreground-only, explicit owner action, at most one candidate, no popup/poll/push |
| Privacy | ACCEPT | No diagnosis, protected-trait inference, persuasion optimization, RL or adaptive nudging |
| Environment/deployment | ACCEPT | Runtime deployment keeps the existing contract; `env change required: no`; no new setting or secret |
