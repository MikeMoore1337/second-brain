# Cognitive Twin v2 / Stage 11C0 — Growth Advisor privacy/payload & branch provenance design contract v1

Статус документа: **DESIGN / NORMATIVE CONTRACT ONLY**. Это design gate
Issue [#264](https://github.com/MikeMoore1337/second-brain/issues/264).
Документ фиксирует privacy, payload, explicit-action, execution-control,
result-reference и branch-provenance boundary для будущего Growth Advisor. Он
не создаёт runtime, Web/API/UI, provider call, transport, persistence, schema,
dependency или deployment change.

Preflight этого design gate опирается на merged main после Stage 11B:

- Issue #262 закрыта;
- PR #263 merged в main;
- Stage 11A и Stage 11B находятся в текущем main;
- current Growth mapping/friction runtime уже существует и остаётся
  детерминированным;
- текущий Assistant v1 и production Advisor provider adapter уже существуют
  как отдельная explicit-only boundary.

При расхождении merged contract имеет приоритет над Issue. Этот документ
читается вместе с:

- [growth-engine-v1-contract.md](growth-engine-v1-contract.md);
- [assistant-v1-contract.md](assistant-v1-contract.md);
- [compare-v1-contract.md](compare-v1-contract.md);
- [design-roadmap-v1.md](design-roadmap-v1.md);
- текущими Stage 4 Self Model, Stage 10 Behavioral Self Model и Stage 11A/11B
  application contracts/runtime;
- [provider-decision-v1.md](../llm/provider-decision-v1.md);
- [vps.md](../deployment/vps.md) и [web-production.md](../deployment/web-production.md).

Этот contract не пересматривает принятую Stage 4–11B authority semantics и не
разрешает ей новый источник данных.

## 1. Нормативное решение

| Область | Verdict | Нормативное решение |
| --- | --- | --- |
| Advisor seam | ACCEPT | Переиспользовать текущий provider-neutral AdvisorPort и typed Assistant v1 envelope |
| Production adapter | ACCEPT | Переиспользовать существующий CloudflareWorkersAiAdvisorPort и его утверждённую transport/config boundary |
| Goal source | ACCEPT | Только exact current Goal из нового current scan → existing BuildSelfModel |
| Explicit action | ACCEPT | Один owner-selected Goal, явный preview, явное подтверждение, immediate revalidation, один execute |
| Payload | ACCEPT | Ровно один validated Goal text как explicit_goals и только caller-owned Assistant fields |
| Growth relation/context | FORBIDDEN | Не передавать mapping relation, friction, Stage 10, Stage 9, behavior или hidden Growth context |
| Result | ACCEPT | Полный validated Assistant result остаётся самостоятельной transient branch |
| Provenance | ACCEPT | Компактный transient reference без raw Goal text, prompt, response или provider details |
| Persistence | FORBIDDEN | Не сохранять Goal projection, request, result, history, cache или telemetry payload |
| Provider privacy | RISK / INHERIT | Сохранить текущую Cloudflare/third-party Customer Content boundary; не обещать provider-side no-retention |
| Compare | DEFER | Не менять Compare v1; GrowthCompare v2 остаётся отдельным будущим design gate |
| Runtime | DEFER | Stage 11C runtime не реализуется в этом Issue |
| New provider/config/dependency | FORBIDDEN | Не добавлять provider, model, secret, env key, network path или package |
| HUMAN_REQUIRED | ACCEPT | None: текущий Assistant/Advisor provider boundary может выполнить этот explicit-only contract |

Growth Advisor не является новым типом Growth relation и не получает
authority над Goal, observed behavior, mapping, recommendation correctness,
optimality или progress. Его output — независимый recommendation/analysis
branch, а не canonical Cognitive Twin fact.

## 2. Статус и границы Stage 11C0

| Checkpoint | Status |
| --- | --- |
| Stage 11A current Goal identity/context | COMPLETE |
| Stage 11B Goal-to-choice relation и friction read model | COMPLETE |
| Stage 11C0 Growth Advisor privacy/payload/provenance design | DESIGN COMPLETE |
| Stage 11C runtime | NOT IMPLEMENTED |
| Stage 11D personalized learning/question boundary | NOT STARTED |
| Stage 11E Web/API/integration QA/closeout | NOT STARTED |
| Stage 12+ | NOT STARTED |

В Stage 11C0 разрешены только чтение текущих contracts, фиксация этого
документа, минимальные cross-reference/status updates, локальные docs checks и
обычный GitHub/document delivery lifecycle. Ни один provider, worker, vault,
web route, application service, persistence adapter или live smoke не
запускается как часть design implementation.

Отсутствие runtime здесь означает отсутствие нового runtime diff. Это не
отменяет уже merged Stage 11A/11B implementation.

## 3. Термины и authority

### 3.1. Current Goal

Current Goal — это только direct Goal claim из текущего canonical scan и
текущей утверждённой Stage 4 Self Model policy:

1. прочитать current source через существующий read-only scan;
2. построить текущий report;
3. выполнить существующий BuildSelfModel;
4. выбрать claim с exact current source identity, direct Goal dimension и
   допустимой Goal evidence kind;
5. построить существующий GrowthGoalIdentityV1 без raw body в durable DTO.

Недостаточно иметь старый GrowthEngineResultV1, ранее показанный preview,
сохранённый fingerprint, Growth mapping или любой client-supplied Goal text.
Если current source исчез, изменился, стал ambiguous, нарушил policy или
потерял exact identity, execution прекращается до AdvisorPort.

### 3.2. Currentness

Current означает полный новый bounded scan/rebuild по текущей policy, а не
новейшую заметку, UUID timestamp, максимальный updated_at или recency ranking.
Goal evidence_at и Advisor request/generated time — разные факты. Unknown
остаётся literal unknown; время запроса не backfill-ит evidence time.

### 3.3. Independent branch

Advisor branch может использовать только explicit caller-owned Assistant input и
ровно один owner-approved current Goal projection. Она не получает:

- Growth state, reason/caveat codes, cohort или behavioral pattern;
- Growth mapping, relation, selected option из Stage 10;
- Stage 9/10C records, journal history, preferences, beliefs, values или
  personal-memory context;
- raw vault body, path, title, front matter, note history, source list;
- скрытые task, goal, personality, motivation, discipline, vulnerability или
  “what the user really wants”.

AssistantResultEnvelopeV1 сохраняет своё значение: independent
recommendation/analysis/abstention. Advisor не утверждает, что recommendation
поддерживает или опровергает Goal relation.

## 4. Два разных жизненных цикла

### 4.1. Read/preview lifecycle

Обычный Growth read, Growth rebuild, mapping review, mapping accept, открытие
страницы, GET preview или повторный показ уже рассчитанного deterministic
result не вызывают Advisor и не требуют provider configuration.

Будущий preview может выполнить только bounded read-only current rebuild и
показать owner exact text, который потенциально будет передан как explicit
goal. Preview не создаёт provider request, не вызывает network, не создаёт
history и не становится согласием на execution.

### 4.2. Explicit execution lifecycle

Единственный допустимый future flow:

~~~text
owner selects exact current Goal
    -> current scan/rebuild
    -> exact Goal identity and Assistant-compatible projection
    -> transient owner-facing preview
    -> owner explicit confirmation
    -> immediate current scan/rebuild and identity revalidation
    -> construct typed GrowthAdvisorRequestV1
    -> inject exactly one validated Goal into Assistant explicit_goals
    -> build canonical AssistantReasoningEnvelopeV1
    -> BuildAssistant.execute once
    -> existing AdvisorPort.advise once
    -> validate exact AssistantResultEnvelopeV1
    -> return transient independent Advisor branch + compact reference
~~~

Каждый execution начинается только с owner action, а не с GET, timer, rebuild,
background task, hydration, mapping write, Growth result rendering, page load,
orchestrator или provider callback. Повторный click/request не получает права на
retry: idempotency/replay control future transport должен либо отклонить
duplicate action до provider call, либо создать новый owner-visible explicit
action с новой confirmation. Это не разрешает автоматический повтор.

## 5. Accepted reuse of current Assistant boundary

### 5.1. Typed seam

В v1 используется существующая последовательность:

~~~text
GrowthAdvisorRequestV1
    -> existing AssistantRequest
    -> existing Assistant request validator
    -> AssistantReasoningEnvelopeV1
    -> BuildAssistant
    -> AdvisorPort
    -> AssistantResultEnvelopeV1
~~~

Growth Advisor не передаёт в LlmPort и не пытается представить advice как
NoteDraft. LlmRequest/NoteDraft имеют другую семантику и не являются
допустимым обходом Assistant policy.

### 5.2. Existing production provider

Production verdict: ACCEPT reuse текущего CloudflareWorkersAiAdvisorPort,
который уже:

- принимает только canonical AssistantReasoningEnvelopeV1;
- использует фиксированные approved provider/model/path settings;
- получает только существующие CLOUDFLARE_ACCOUNT_ID и
  CLOUDFLARE_API_TOKEN;
- передаёт secret через существующий private worker boundary;
- применяет fixed deadline, cancellation, bounded body/process controls;
- не делает retry, fallback, redirect, arbitrary URL/model selection или raw
  provider response forwarding;
- возвращает только валидируемый Assistant result либо safe Assistant error.

Growth Advisor не выбирает модель, не меняет system prompt provider adapter,
не добавляет credential, не меняет Cloudflare endpoint и не меняет provider
retention statement. Фиксированная модель текущего адаптера — operational
property existing provider boundary, а не Growth policy identity.

### 5.3. Необходимое runtime capability

Текущий AdvisorPort и Cloudflare adapter достаточны для этого design:

- BuildAssistant проверяет cancellation до и после одного вызова;
- Cloudflare adapter имеет bounded total deadline и interruptible worker;
- worker cleanup ограничен terminate/kill/wait и не оставляет orphan process;
- public ошибки не содержат raw exception, provider body, prompt, Goal text или
  token.

Если будущий новый adapter не сможет гарантировать те же interruption,
deadline, body, secret и safe-error свойства, он не считается разрешённым
reuse и открывает отдельный HUMAN_REQUIRED/provider design gate. Такой adapter
не создаётся в #264.

## 6. Exact input DTO

### 6.1. GrowthAdvisorRequestV1

Минимальный typed request для будущего explicit action:

~~~text
GrowthAdvisorRequestV1 {
  contract_version:             "growth-advisor-v1"
  goal_source_uuid:             UUIDv7
  goal_identity_fingerprint:    GrowthHashV1
  task:                         str
  options:                      tuple[AssistantOption, ...] = ()
  explicit_constraints:         tuple[str, ...] = ()
  explicit_context:             tuple[AssistantExplicitContext, ...] = ()
  max_context_bytes:            int = 65536
  max_result_bytes:             int = 65536
}
~~~

Значения request имеют следующие источники и правила:

| Поле | Источник | Правило |
| --- | --- | --- |
| contract_version | client/server protocol | Только literal growth-advisor-v1 |
| goal_source_uuid | owner-selected current Goal | Только selector; body Goal не принимается |
| goal_identity_fingerprint | transient preview echo | Server recomputes it; mismatch даёт stale/changed error |
| task | caller-owned Assistant task | Existing Assistant bounds and normalization |
| options | caller-owned Assistant options | Existing Assistant exact bounds/ID/label validation |
| explicit_constraints | caller-owned explicit constraints | Existing Assistant exact bounds |
| explicit_context | caller-owned explicit context | Existing Assistant exact bounds; no automatic enrichment |
| max_context_bytes | caller control within Assistant bounds | Integer 1..65536; bool-as-int rejected |
| max_result_bytes | caller control within Assistant bounds | Integer 304..65536; bool-as-int rejected |

Request не имеет полей goal_text, raw_goal, goal_claim, domain,
evidence_at, behavior, mapping, relation, cohort, Stage 9/10 payload,
provider, model, token, URL, prompt, result history, consent text или
arbitrary policy fingerprint. explicit_goals также не является client field:
его ровно единственное значение добавляет server после current revalidation.

Неизвестные, дублированные, missing, wrong-type и extra fields отклоняются
до execution. Client-supplied goal text, goal fingerprint или Goal identity не
могут заменить server current scan; fingerprint нужен только как stale-preview
binding и не является authority сам по себе.

### 6.2. Existing Assistant bounds remain authoritative

Growth Advisor не дублирует и не ослабляет Assistant v1 validator:

- task: 1..4096 UTF-8 bytes после strict UTF-8/NFC/edge normalization и
  control/format rejection;
- options: 0..8; ID и label — по Assistant v1 exact grammar/bounds;
- explicit_constraints: 0..16, each bounded, aggregate 8192 bytes;
- explicit_goals: 0..8, each bounded, aggregate 4096 bytes;
- explicit_context: 0..16, each bounded, aggregate 16384 bytes;
- max_context_bytes: 1..65536;
- max_result_bytes: 304..65536.

Growth Advisor всегда создаёт ровно одну explicit goal, поэтому Goal text
должен пройти existing per-value limit 512 bytes и aggregate limit 4096 bytes.
Growth Self Model claim может быть меньше Growth body cap, но это не расширяет
Assistant cap. Truncation, lossy normalization, replacement character,
silent field dropping и second validation after provider call запрещены.

## 7. Exact Goal projection

### 7.1. Source-to-preview algorithm

Будущая реализация должна выполнять все шаги в указанном порядке:

1. принять только goal_source_uuid и preview fingerprint;
2. построить current scan → report → existing BuildSelfModel;
3. найти exact current direct Goal claim;
4. построить/проверить GrowthGoalIdentityV1 и сравнить
   goal_identity_fingerprint;
5. взять claim.claim как единственный candidate text, без semantic rewrite;
6. применить только существующую Assistant text normalization/validation:
   strict UTF-8, NFC, trim edge whitespace и запрет C0/C1/DEL/Cf по Assistant
   rules;
7. проверить per-value 512-byte limit;
8. показать normalized validated text в transient owner preview;
9. после confirmation повторить шаги 2–7 и сравнить identity/text binding ещё
   до создания Assistant request;
10. положить ровно этот validated text в explicit_goals=(text,).

Нормализация на шаге 6 — техническая canonicalization существующего Assistant
contract, а не смысловая редактура. Не разрешены paraphrase, grammar fix,
summarization, translation, goal splitting, goal ranking, inference,
sentiment/personality extraction или LLM rewrite.

### 7.2. Exact bytes and preview

Preview обязан сообщать:

~~~text
GrowthAdvisorGoalPreviewV1 {
  contract_version:             "growth-advisor-v1"
  goal_source_uuid:             UUIDv7
  goal_identity_fingerprint:    GrowthHashV1
  assistant_contract_version:   "assistant-v1"
  advisor_policy_id:            "growth-advisor-owner-explicit-goal-v1"
  goal_text:                    str
  goal_text_utf8_bytes:         int
}
~~~

Preview является owner-facing transient DTO и не является provider payload.
goal_text — exact validated projection, а goal_text_utf8_bytes равен длине
этой строки в UTF-8. После подтверждения server обязан снова получить тот же
normalized text из текущего claim. Provider-facing
AssistantReasoningEnvelopeV1 должен содержать exactly the same logical string
в explicit_goals[0]; UI не показывает одну строку, а provider получает
другую.

Canonical JSON byte representation сохраняется по Assistant v1:
UTF-8, ensure_ascii=false, allow_nan=false, compact separators, fixed key
order and no hidden fields. Existing Cloudflare adapter may apply its approved
transport JSON escaping when creating the HTTPS body; это не меняет logical
Goal string и не даёт права добавить private context.

### 7.3. Fail-closed cases

До AdvisorPort отклоняются:

- missing current Goal;
- duplicate/ambiguous current Goal source;
- deleted, edited, superseded or policy-invalid source;
- preview fingerprint mismatch;
- claim no longer direct Goal;
- unsupported empty/control/format text;
- Goal text over the Assistant per-value limit;
- aggregate Assistant context overflow;
- policy ID/version mismatch;
- cancellation or expired fixed deadline.

В этих случаях не строится provider body и не делается network call. Raw source
body, path, title, front matter, claim details и diff не попадают в public error,
log или provider.

## 8. Exact provider-visible payload

### 8.1. Allowed fields

После revalidation provider получает только canonical Assistant envelope:

~~~text
AssistantReasoningEnvelopeV1 {
  task:                 caller-owned task
  options:              caller-owned options
  explicit_constraints: caller-owned constraints
  explicit_goals:       tuple[one exact current Goal projection]
  explicit_context:     caller-owned context
}
~~~

Task, options, constraints и context принадлежат явному caller request. Growth
Advisor не добавляет их и не делает их derived from vault. explicit_goals[0]
принадлежит owner-selected current Goal, но server injects it only after the
owner preview/confirmation and immediate current revalidation.

max_context_bytes и max_result_bytes — application controls; они не входят
в provider-visible envelope. Request id, policy fingerprint, Goal UUID,
identity fingerprint, evidence time, timestamps, cancellation/deadline и
provenance — internal control/reference fields; они не входят в envelope.

### 8.2. Forbidden fields and sources

| Forbidden input | Причина |
| --- | --- |
| Behavioral bodies, journal history, Stage 10C records | hidden observed-behavior context |
| Growth relation, friction state, cohort, option identity | prevents Advisor becoming relation authority |
| Stage 9/10 projections, Personal Memory, preferences, values | unrelated private context and policy drift |
| Other current goals or goal ranking | exact single selected Goal only |
| raw vault note/path/title/front matter/history | no raw vault context |
| source UUID, identity/claim fingerprint, policy fingerprint | provenance stays application-side |
| provider/model/token/URL/deadline | no provider steering or secret exposure |
| hidden system history, previous result, retry reason | no session/history inference |
| inferred traits, motivation, vulnerability or personality | no profiling policy |

No serialization helper may silently serialize the enclosing Growth object,
GrowthEngineResultV1, mapping record, source object or exception in order to
construct this envelope. The envelope must be built from the exact typed
Assistant fields only.

### 8.3. Provider instruction boundary

Existing Advisor provider instructions remain bounded to independent
recommendation/analysis based on the explicit envelope. The provider must not
be instructed to:

- infer the user's hidden goal or private preferences;
- diagnose, score, classify or predict a person;
- treat Goal as evidence that a behavioral relation is true;
- write, update, confirm or delete any vault/Growth/mapping state;
- call tools, browse, search, retrieve history or use hidden context;
- return a technical Goal UUID or provider trace in user prose.

The application validates the exact Assistant result schema after provider
return. Provider instructions do not replace application validation.

## 9. Explicit owner action and future transport boundary

Stage 11C0 does not add a route. Future implementation must reuse the existing
private owner-only web boundary and make the action explicit:

1. authenticated owner session;
2. trusted Host and same-origin protections already used by the current private
   Web surface;
3. exact request-purpose header and strict JSON content type;
4. raw body cap before parsing;
5. strict typed payload with extra fields forbidden;
6. no CORS expansion;
7. no-store response and no browser/service-worker/cache persistence;
8. explicit confirmation visible before provider execution;
9. one confirmation action maps to at most one AdvisorPort call;
10. current Goal revalidation immediately before that call.

Preview/read endpoint and execute endpoint are separate semantics. Preview can
read current Goal, but never calls Advisor. Execute cannot trust preview text
from the browser and must rebuild/revalidate current identity. A stale preview
returns a fixed safe error and preserves the deterministic Growth branch.

The future route must not silently invoke Advisor from the existing
Growth result endpoint, mapping review/accept endpoint, Assistant page load,
Compare endpoint or generic hydration. No current route is changed in #264.

## 10. Execution control

### 10.1. Internal execution context

Future coordinator uses an internal, non-serialized control context:

~~~text
GrowthAdvisorExecutionContextV1 {
  cancellation: CancellationToken
  deadline:     float  # finite absolute monotonic deadline
}
~~~

cancellation и deadline никогда не входят в GrowthAdvisorRequestV1,
AssistantReasoningEnvelopeV1, AssistantResultEnvelopeV1, policy JSON,
provenance reference, logs или provider payload.

В v1 deadline не является client setting. Coordinator создаёт finite absolute
deadline от monotonic clock, применяя существующий approved Advisor total
deadline 30 seconds. Если caller/request передаёт другое значение,
unbounded value, wall-clock timestamp, NaN или infinity, request invalid.

### 10.2. Cancellation/deadline order

Checks обязательны:

~~~text
before current rebuild
before preview/execute preparation
before AdvisorPort call
while existing provider worker is running
after AdvisorPort returns
before result serialization
~~~

Cancellation wins over timeout when both are observable at the same boundary.
After cancellation or deadline no new provider call, retry, fallback, repair or
background continuation is allowed. Current Cloudflare worker path already
supports bounded cancellation/deadline cleanup; future coordinator must not
wrap an uninterruptible call in a way that creates an orphan task/process.

Current BuildAssistant receives the existing CancellationToken and calls
AdvisorPort exactly once. The future Growth coordinator may own the fixed
deadline and pass cancellation to BuildAssistant, while the reused production
adapter remains the hard transport deadline. A new generic deadline field on
AdvisorPort is not introduced by this design contract.

### 10.3. Failure cleanup

Provider failure path must:

- stop at the first terminal error;
- map to a fixed Growth Advisor error;
- discard raw provider body and exception detail;
- perform existing bounded worker/process cleanup;
- return no partial recommendation;
- leave deterministic Growth result and mapping state untouched;
- write no request/result/history/cache.

## 11. Policy identity and binding

### 11.1. Exact Growth Advisor policy

Policy ID:

~~~text
growth-advisor-owner-explicit-goal-v1
~~~

Policy canonical JSON is UTF-8, ensure_ascii=false, compact separators and
lexicographically sorted keys:

~~~json
{"assistant_contract":"assistant-v1","automatic_behavioral_context":"none-v1","automatic_growth_context":"none-v1","contract":"growth-advisor-v1","goal_source":"stage11-current-selected-goal-v1","owner_action":"explicit-preview-confirm-request-v1","payload":"one-owner-previewed-current-goal-as-explicit-goals-v1","persistence":"ephemeral-no-application-persistence-v1","provider":"existing-advisor-port-provider-neutral-v1","result":"independent-recommendation-analysis-transient-v1","transport":"assistant-request-through-advisor-port-v1","version":"1"}
~~~

Policy fingerprint:

~~~text
sha256:78a651c2450f4c0c698e0ea4f51786ed846a80f32c2a582b6e9fda3965918c0b
~~~

Fingerprint input is exactly the one-line JSON above encoded as UTF-8, without
trailing newline. A future implementation must compute and compare the
fingerprint from its canonical serializer, not copy an unverified literal.

### 11.2. Binding hierarchy

The binding is:

~~~text
growth-advisor-owner-explicit-goal-v1
    -> growth-advisor-v1
    -> assistant-v1
    -> assistant explicit-context-only request validator
    -> existing AdvisorPort
    -> existing approved production provider adapter
~~~

Growth Advisor policy does not create a second Assistant policy ID or
provider-specific semantic version. The Assistant contract version
assistant-v1, its exact explicit-only validator and the AdvisorPort typed
signature are the binding. Provider model/config identity remains in the
existing provider decision/deployment contracts.

The existing deterministic Growth policy
growth-engine-explicit-relation-v1 and Stage 11B mapping policy remain
unchanged. They are not aliases of the Advisor policy and are not sent to the
provider.

## 12. Result and provenance DTOs

### 12.1. Compact result reference

The future ref supersedes the nullable Stage 11B placeholder while preserving
its result_kind/requested_at/generated_at intent:

~~~text
GrowthAdvisorResultRefV1 {
  request_id_fingerprint:       GrowthHashV1
  result_kind:                  "independent_recommendation_analysis"
  advisor_policy_id:            "growth-advisor-owner-explicit-goal-v1"
  advisor_policy_fingerprint:   GrowthHashV1
  assistant_contract_version:   "assistant-v1"
  assistant_context_policy:     "explicit-context-only-v1"
  goal_source_uuid:             UUIDv7
  goal_identity_fingerprint:    GrowthHashV1
  requested_at:                 RFC3339 UTC
  generated_at:                RFC3339 UTC
}
~~~

The reference is an application-side transient provenance object. It contains
no Goal body, task, options, constraints, explicit context, Assistant result,
rationale, provider model, response body, token, URL or exception. UUID and
fingerprints are not provider-visible and are not written to ordinary logs.

request_id_fingerprint is the SHA-256 fingerprint of a server-generated
one-action request identifier; the raw request identifier is never returned or
persisted. goal_identity_fingerprint is the SHA-256 fingerprint of the
canonical raw-body-free GrowthGoalIdentityV1 object. Both are binding evidence,
not user-facing content.

requested_at is when the explicit operation was accepted. generated_at is
when the validated transient result was produced. Neither is
goal.evidence_at, mapping.reviewed_at or Stage 10 generated_at.

### 12.2. Full transient Advisor branch

The full result is returned only in memory for the owner-facing response:

~~~text
GrowthAdvisorBranchV1 {
  branch:           "advisor"
  state:            "result" | "abstention" | "error"
  assistant_result: AssistantResultEnvelopeV1 | null
  error:            GrowthAdvisorErrorV1 | null
  provenance:       GrowthAdvisorResultRefV1 | null
}
~~~

Valid states:

| state | assistant_result | error | provenance |
| --- | --- | --- | --- |
| result | validated Assistant kind recommendation or analysis | null | required |
| abstention | validated Assistant kind abstention | null | required |
| error | null | fixed GrowthAdvisorErrorV1 | null unless a future transport explicitly defines a safe failed-operation ref |

For result/abstention, assistant_result is the complete existing typed
AssistantResultEnvelopeV1, including recommendation, selected option,
rationale, evidence refs, constraints used, objectives used, uncertainty and
abstention code according to Assistant v1. The wrapper does not rename,
summarize, merge or reinterpret those fields.

result_kind in the compact reference remains
independent_recommendation_analysis for both recommendation and analysis
outcomes; AssistantResultEnvelopeV1.kind remains the exact
recommendation/analysis/abstention discriminator. A valid abstention is not a
provider failure and does not change deterministic Growth state.

The branch is not embedded in or persisted with current
GrowthEngineResultV1. If a future explicit composition response returns both
branches, it must use separate named fields:

~~~text
GrowthCompositionV1 {
  deterministic_growth: GrowthEngineResultV1
  advisor:             GrowthAdvisorBranchV1
}
~~~

This wrapper is transient only and is not GrowthCompareV1.

## 13. Fixed error contract

### 13.1. Public shape

~~~text
GrowthAdvisorErrorV1 {
  code:    closed GrowthAdvisorErrorCodeV1
  message: one fixed safe string for that code
}
~~~

No public error includes raw Goal text, UUID, path, front matter, prompt,
provider/model name, HTTP body/code, exception repr, retry detail, token,
secret, filesystem location or stack trace.

### 13.2. Closed codes and messages

| Code | Exact fixed message |
| --- | --- |
| GROWTH_ADVISOR_INVALID_REQUEST | growth advisor request failed validation |
| GROWTH_ADVISOR_GOAL_UNAVAILABLE | the current goal source is unavailable |
| GROWTH_ADVISOR_GOAL_MISSING | the requested current goal is missing |
| GROWTH_ADVISOR_GOAL_CHANGED | the current goal preview is stale |
| GROWTH_ADVISOR_GOAL_TEXT_UNSUPPORTED | the current goal text is unsupported |
| GROWTH_ADVISOR_GOAL_TEXT_TOO_LARGE | the current goal text exceeds the Assistant limit |
| GROWTH_ADVISOR_CONTEXT_TOO_LARGE | the Assistant context exceeds the request limit |
| GROWTH_ADVISOR_RESULT_TOO_LARGE | the Assistant result exceeds the request limit |
| GROWTH_RECOMMENDATION_UNAVAILABLE | the independent recommendation is unavailable |
| GROWTH_ADVISOR_CANCELLED | the independent recommendation was cancelled |
| GROWTH_ADVISOR_TIMEOUT | the independent recommendation timed out |
| GROWTH_ADVISOR_FAILURE | the independent recommendation failed |
| GROWTH_ADVISOR_INVALID_RESULT | the independent recommendation result failed validation |
| GROWTH_ADVISOR_POLICY_MISMATCH | the Growth Advisor policy binding is invalid |

GROWTH_RECOMMENDATION_UNAVAILABLE is scoped to the optional Advisor branch.
It never turns a successful deterministic Growth result, relation, mapping
review or mapping store append into failure.

### 13.3. Mapping from Assistant errors

| Assistant v1 error | Growth Advisor public code |
| --- | --- |
| ASSISTANT_INVALID_REQUEST before provider call | Specific Goal/context code when known, otherwise GROWTH_ADVISOR_INVALID_REQUEST |
| ASSISTANT_CANCELLED | GROWTH_ADVISOR_CANCELLED |
| ASSISTANT_TIMEOUT | GROWTH_ADVISOR_TIMEOUT |
| ASSISTANT_PROVIDER_UNAVAILABLE | GROWTH_RECOMMENDATION_UNAVAILABLE |
| ASSISTANT_PROVIDER_FAILURE | GROWTH_ADVISOR_FAILURE |
| ASSISTANT_MALFORMED_RESULT | GROWTH_ADVISOR_INVALID_RESULT |
| ASSISTANT_RESULT_TOO_LARGE | GROWTH_ADVISOR_RESULT_TOO_LARGE |
| ASSISTANT_RESULT_INVALID | GROWTH_ADVISOR_INVALID_RESULT |

The mapping is closed and safe. It does not expose a provider-specific
diagnostic or make a second call to improve an invalid result.

## 14. Privacy, retention and logging

### 14.1. Application retention

Stage 11C runtime must be ephemeral:

- no Goal projection persistence;
- no request, preview, confirmation, result or error history;
- no Growth mapping store write;
- no Stage 9/10C/Behavioral write;
- no vault/Safe Write/canonical note update;
- no database, queue, event store, cache, indexedDB, localStorage,
  sessionStorage or service-worker cache;
- no model training dataset, embedding, vector/graph store or analytics
  event;
- no result replay endpoint.

The transient object lives only long enough to show the owner response and is
discarded at request completion/cancellation. A future UI may keep the result
in the current rendered memory of the response, but must not add browser
persistence. Refresh starts without an Advisor result.

### 14.2. Logging and telemetry

Default application logs contain only the fixed safe outcome code and ordinary
operational level. They do not contain:

- Goal text or any raw claim/body;
- full canonical Assistant envelope or provider body;
- full Assistant result, recommendation or rationale;
- task, options, constraints or explicit context;
- source path/title/front matter/history;
- Goal UUID, request UUID or private identity fingerprints;
- provider response, HTTP body/code, exception detail or credentials.

If future redacted telemetry is separately approved, it may carry only bounded
fixed status/policy version data and an irreversible non-content operation
fingerprint. It must not become a hidden retention channel. No telemetry is
added by #264.

### 14.3. Existing provider data-handling boundary

The current approved Cloudflare adapter sends explicit Assistant Customer
Content to Cloudflare and its upstream model service for processing. Existing
provider documentation governs training/retention disclosures; the
application makes no stronger provider-side no-retention guarantee. The
adapter’s bounded private worker, no-raw-response public errors and
secret-safe transport remain required, but they do not erase provider-side
processing risk.

Stage 11C adds no provider, endpoint, model, credential or retention setting.
The owner preview and explicit confirmation are the product boundary for
sending the selected Goal to the already approved Advisor provider. If product
policy later requires a stronger provider privacy guarantee, that is a new
provider/privacy decision gate and not a silent change to this contract.

## 15. Safety and prohibited provider-assisted profiling

Growth Advisor must not be used to infer, classify, score, diagnose or optimize
for a person’s:

- mental-health condition or diagnosis;
- political or religious affiliation/persuasion;
- sexuality or intimate trait;
- criminal propensity;
- employability, creditworthiness or eligibility;
- addiction, manipulation susceptibility or vulnerability;
- personality/discipline/motivation as hidden facts.

An explicit Goal is not a license to turn the provider into a profiling
classifier. The contract does not add a semantic safety classifier, automatic
sensitive-data detector or hidden policy model. A future product surface must
not offer such a task as Growth Advisor advice; any high-stakes/sensitive
provider use requires a separate reviewed safety/privacy gate.

The application also must not derive recommendation quality, user compliance,
progress or causal effect from the result. Owner choice remains the only
Growth relation authority.

## 16. Temporal semantics

| Timestamp | Meaning | Can replace another timestamp |
| --- | --- | --- |
| goal.evidence_at | When the reviewed Goal was stated | No |
| mapping.reviewed_at | Server time of explicit mapping confirmation | No |
| mapping.created_at | Durable operational mapping commit time | No |
| growth.generated_at | Clock of deterministic Growth build | No |
| advisor.requested_at | Time explicit Advisor execution was accepted | No |
| advisor.generated_at | Time validated transient Advisor result was produced | No |
| provider execution time | Internal adapter/worker timing | No |

The result reference does not assert that Goal and behavior were simultaneous,
that Advisor caused a choice, or that a later result is more current than the
Goal evidence. Unknown evidence time stays unknown. No recency, backfill,
supersede or temporal causal inference is allowed.

## 17. Branch independence and GrowthCompare

### 17.1. Deterministic Growth remains unchanged

Stage 11A/11B GrowthEngineResultV1 remains deterministic and provider-free:

- same current scan/rebuild and explicit Goal identity;
- same exact Stage 10 cohort/option relation;
- same mapping policy, store and fixed Growth reason/caveat codes;
- same safe state when mapping or behavior is missing/mixed/changed;
- no Advisor result used as Growth evidence.

The current nullable advisor field in Stage 11B remains null in the existing
runtime. The exact future Advisor reference/branch is defined here and does
not retroactively imply a Stage 11B provider call or data mutation.

### 17.2. Composition boundary

A future explicit response may present:

~~~text
deterministic_growth: GrowthEngineResultV1
advisor:             GrowthAdvisorBranchV1
~~~

The two namespaces remain separate. No score, confidence, probability,
delta, fit, best choice, supports, conflicts, progress or
recommendation-derived state is computed between them. Advisor unavailable,
abstention, timeout or failure is local to advisor.

### 17.3. GrowthCompare decision

Compare v1 is unchanged. There is no GrowthCompareV2 runtime, structural delta,
common private snapshot, Compare provider call or Compare result merge in
Stage 11C0. Any future GrowthCompare must separately define input authority,
branch provenance, privacy payload, temporal semantics and no-hidden-semantic-
delta rules before implementation.

## 18. Future Stage 11C runtime slice

The next implementation issue, if separately approved, is limited to:

1. strict parser/DTO for GrowthAdvisorRequestV1 and transient Goal preview;
2. current Goal scan/rebuild and raw-body-free identity fingerprint;
3. exact Assistant-compatible Goal projection with no semantic rewrite;
4. private owner-only preview and explicit execute transport using existing
   security/no-store boundary;
5. immediate revalidation and fail-closed stale-preview handling;
6. one BuildAssistant execution through existing AdvisorPort/provider adapter;
7. fixed safe Growth Advisor error mapping;
8. full transient Assistant result branch and compact provenance reference;
9. no persistence/log/browser-cache/result-history path;
10. deterministic provider-double tests and no-network/no-read assertions;
11. focused Python 3.14 checks followed by the repository final gate.

The slice must not include:

- new provider/model/secret/env key/network endpoint/dependency;
- LlmPort reuse for advice;
- automatic Advisor invocation;
- hidden Goal/context injection;
- Behavioral/Stage 9/Stage 10C/Personal Memory data;
- Growth mapping/relation mutation from recommendation;
- GrowthCompareV2, Compare v1 changes or Simulate Me changes;
- schema/canonical note/vault/Safe Write changes;
- persistence, cache, history, telemetry or training capture;
- live credentials or authenticated provider smoke as a default test;
- next Issue creation.

An authenticated provider smoke, if ever needed, is a separate explicit
production/provider gate and does not belong to the runtime unit tests.

## 19. Future test matrix

The implementation slice must add deterministic tests for at least the
following categories:

| Category | Required assertion |
| --- | --- |
| no implicit invocation | Growth read, rebuild, mapping review/accept and page load make zero Advisor calls |
| explicit single invocation | one confirmed owner action makes exactly one AdvisorPort call |
| stale preview | changed/deleted/policy-drifted Goal fails before provider body creation |
| exact Goal projection | preview text and explicit_goals[0] are the same Assistant-validated value |
| one Goal only | no second Goal, ranking, behavior or mapping relation enters envelope |
| caller-owned fields | task/options/constraints/context are not auto-enriched |
| malformed/extra DTO | strict parser rejects unknown fields, wrong types and bool-as-int |
| Assistant bounds | per-field and aggregate limits remain exact, no truncation |
| canonical bytes | serializer has fixed keys, UTF-8, no hidden fields and bounded context |
| provider payload allowlist | UUIDs, fingerprints, timestamps, Growth data and secrets absent |
| provider call count | timeout/failure/malformed result never retries, falls back or repairs |
| cancellation | cancellation before/while/after call is safe and wins over deadline |
| deadline | fixed finite bound, no user override, no orphan worker/process |
| provider errors | raw body/status/exception/secret never crosses public boundary |
| result validation | only exact AssistantResultEnvelopeV1 reaches transient branch |
| abstention | valid abstention is separate from failure/unavailable |
| unavailable isolation | GROWTH_RECOMMENDATION_UNAVAILABLE affects only Advisor branch |
| persistence | no vault/mapping/DB/cache/browser/history writes on all outcomes |
| logs | no Goal text, envelope, result, provider response, UUID or secret |
| branch independence | deterministic Growth result is byte/semantic unchanged with Advisor success/error |
| temporal | evidence/request/generated/mapping times remain distinct; unknown preserved |
| sensitive profiling | prohibited profiling task/path is not provided by the surface |
| no-network default | all unit/contract tests use provider doubles and no live credentials |

## 20. Acceptance checklist for this design gate

~~~text
Issue #264: OPEN at design start; close only after merged PR
Stage 11A: COMPLETE
Stage 11B: COMPLETE
Stage 11C0: DESIGN COMPLETE
Stage 11C runtime: NOT IMPLEMENTED
Stage 11D/E: NOT STARTED
Stage 12+: NOT STARTED

runtime changed: NO
dependencies changed: NO
schema changed: NO
provider/network changed: NO
env change required: NO
second-brain-vault changed: NO
live provider call: NO
new provider/model/secret/network: NO
new persistence/history/cache: NO
Growth mapping semantics changed: NO
Growth deterministic result semantics changed: NO
Compare v1 changed: NO
HUMAN_REQUIRED: none
next Issue created: NO
~~~

The env change required verdict is based on the actual design-only diff and
the existing deployment contract: CLOUDFLARE_ACCOUNT_ID and
CLOUDFLARE_API_TOKEN already exist as optional settings for the approved
Advisor/LLM paths. #264 adds no key, value, mode, placement or secret.
Ordinary production deployment therefore requires no environment operation.

## 21. ACCEPT / CHANGE / RISK / DEFER

| Area | Verdict | Decision |
| --- | --- | --- |
| Current Goal authority | ACCEPT | Existing current Stage 4 direct Goal identity and exact revalidation |
| Preview projection | ACCEPT | Existing claim text plus only Assistant canonical normalization |
| Explicit owner action | ACCEPT | Preview, confirmation and one non-automatic execution |
| Assistant DTO | ACCEPT | Existing assistant-v1 request/envelope/result validators |
| AdvisorPort | ACCEPT | Existing provider-neutral typed port, exactly one call |
| Cloudflare adapter | ACCEPT | Existing approved fixed provider/worker/deadline/secret boundary |
| LlmPort | FORBIDDEN | Note drafting boundary is not an advice boundary |
| Provider payload | ACCEPT | One exact Goal in explicit_goals plus caller-owned Assistant fields |
| Growth/Behavioral context | FORBIDDEN | No automatic Stage 9/10/Growth/mapping injection |
| Result branch | ACCEPT | Full transient Assistant result with independent label |
| Provenance | ACCEPT | Request/policy/Assistant/Goal identity/time reference without raw content |
| Persistence/logging | FORBIDDEN | No application or browser retention; fixed safe errors only |
| Provider-side retention | RISK / INHERIT | Existing Cloudflare/upstream processing disclosure remains |
| Sensitive profiling | FORBIDDEN | No diagnosis, scoring, protected-trait or vulnerability profiling |
| Cancellation/deadline | ACCEPT | Existing bounded adapter; fixed 30s non-client control |
| Growth deterministic semantics | ACCEPT | Stage 11A/11B result remains unchanged |
| Compare v1 / GrowthCompare | DEFER | Separate future composition contract |
| Web/API/UI runtime | DEFER | Stage 11C implementation and Stage 11E gate |
| Schema/vault/Safe Write | FORBIDDEN | No canonical or private-vault mutation |
| New dependencies/provider/env | FORBIDDEN | No new operational capability in #264 |
| HUMAN_REQUIRED | ACCEPT | None under current approved Assistant/provider boundary |

## 22. References and source-of-truth rules

Normative precedence for a future implementation:

1. merged current code and contracts for Assistant v1 and AdvisorPort;
2. this Growth Advisor contract;
3. growth-engine-v1-contract.md for deterministic Growth/Stage 11A/11B
   semantics;
4. compare-v1-contract.md for any separately approved Compare composition;
5. provider-decision-v1.md and deployment contracts for existing Cloudflare
   data-handling/configuration;
6. issue text only where it does not conflict with merged contracts.

Any change to Goal authority, private payload, provider/data handling,
retention, sensitive profiling, canonical schema, persistence, or Compare
composition requires a new contract decision. Silence in this document is not
permission to infer a field or data source.

Итог: Stage 11C0 design gate complete; current main runtime remains Stage 11A
и Stage 11B only; Growth Advisor is an explicit, independent, transient
future branch with one exact current Goal and no hidden private context.
