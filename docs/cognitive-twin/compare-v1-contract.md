# Compare v1 — contract композиции Assistant, Simulate Me и structural Delta

Статус документа: **DESIGN / APPROVED MECHANICAL COMPOSITION**. Этот документ
закрывает design-only часть issue [#163](https://github.com/MikeMoore1337/second-brain/issues/163).
Он не создаёт runtime, Web/API, provider integration, persistence или новую
privacy boundary.

Контрольная база этой редакции: текущий main, commit
eec05429c277f5e544ffa74b060d06dec9b2dce4.

HUMAN_REQUIRED: none. В пределах этого контракта нет нового выбора provider,
privacy policy, canonical schema или semantic inference. Более богатое
объяснение того, почему две ветки расходятся, остаётся DEFERRED.

## 1. Назначение и главный invariant

Compare — read-only композиция двух независимых outputs:

    Assistant     = independent recommendation / analysis
    Simulate Me   = likely owner choice prediction
    Delta         = deterministic structural comparison

Compare никогда не превращает эти outputs в одну blended рекомендацию.
prediction не становится recommendation, recommendation не становится
prediction, и ни одна ветка не становится canonical fact.

Каждый успешный top-level CompareResultV1 содержит ровно три логические зоны:

1. assistant — полный typed output Assistant или его typed abstention/error;
2. simulate_me — полный typed output Simulate Me или его typed abstention/error;
3. delta — только structural relation и deterministic template text.

Наличие delta не означает, что система установила, какая ветка права, какой
вариант объективно лучше или почему владелец сделал бы выбор. При ошибке обеих
веток сохраняется structural result с двумя error wrappers, если сама
композиция остаётся безопасно представимой.

## 2. Нормативные зависимости и границы authority

Compare v1 опирается только на:

- approved Assistant v1 contract из assistant-v1-contract.md;
- текущий provider-free Stage 6 SimulateMeRequest, SimulateMeResult,
  validators и BuildSimulateMe;
- caller-owned literal task и request-local options этого Compare request.

Authority веток различается и не объединяется:

| Зона | Единственный input authority | Что ветка не получает |
| --- | --- | --- |
| Assistant | task, общий список options, explicit_constraints, explicit_goals, explicit_context текущего caller request | Simulate Me result/evidence, Stage 5, Self Model, Search, vault, UUID/path, provider metadata |
| Simulate Me | общий literal task как query, общий список options и approved current Self Model context через существующий Stage 6 boundary | Assistant constraints/goals/context, Assistant result/rationale/evidence, provider input |
| Delta | typed terminal states двух wrappers и exact request-local option IDs | любой raw text reasoning, rationale semantics, evidence body, Search/vault/Self Model direct read, provider/LLM |

В частности:

- Assistant explicit context не копируется в Simulate Me;
- current Self Model context и result Simulate Me не копируются в Assistant;
- Compare не вызывает VaultReader, Search, Self Model builder или
  second-brain-vault напрямую;
- Delta не получает даже read-only access к этим источникам;
- branch evidence остаётся в namespace своей ветки и не union-ится, не
  rank-ится и не сравнивается по содержанию.

Compare request является immutable in-memory snapshot caller inputs. Это не
private context snapshot. Один общий snapshot personal evidence для обеих
веток в v1 не создаётся: Assistant v1 явно не читает automatic private
context, а Simulate Me использует только собственный approved current-context
boundary. Добавление общего private snapshot потребовало бы нового contract и
остаётся DEFERRED.

## 3. Exact Compare request DTO

### 3.1. Shape

    CompareOptionV1 {
      id:    string
      label: string
    }

    CompareAssistantInputsV1 {
      explicit_constraints: tuple[string, ...] = ()
      explicit_goals:       tuple[string, ...] = ()
      explicit_context:     tuple[AssistantExplicitContext, ...] = ()
      max_context_bytes:    int = 65536
      max_result_bytes:     int = 65536
    }

    CompareRequestV1 {
      task:              string
      options:           tuple[CompareOptionV1, ...]
      assistant:         CompareAssistantInputsV1
      max_result_bytes:  int = 131072
    }

AssistantExplicitContext имеет ровно approved shape {kind, text}, где kind
равен fact или background. Compare не вводит иной context role. В Compare v1
options обязательны, даже несмотря на то, что отдельный Assistant request
может быть open-ended без options: Delta требует общей request-local option
namespace.

CompareRequestV1 не содержит отдельного simulate_me input object. Simulate Me
получает только точно описанный общий task и options; любое поле с его
personal context, evidence refs, policy, prediction или результатом является
неизвестным полем и отвергается.

### 3.2. Exact bounds и normalization

Compare применяет одну и ту же approved text validation к shared task и option
labels, а затем передаёт validated copies в обе ветки:

| Поле | Exact bound |
| --- | --- |
| task | required str, 1..4096 UTF-8 bytes после strict UTF-8/NFC/edge-strip; blank запрещён |
| options | 1..8 items; caller order сохраняется |
| CompareOptionV1.id | ASCII 1..64 bytes, pattern [A-Za-z0-9][A-Za-z0-9._:-]{0,63}, unique только в этом request |
| CompareOptionV1.label | non-blank str, 1..256 UTF-8 bytes после approved normalization |
| assistant.explicit_constraints | 0..16 strings, каждый 1..512 bytes, общий budget 8192 bytes |
| assistant.explicit_goals | 0..8 strings, каждый 1..512 bytes, общий budget 4096 bytes |
| assistant.explicit_context | 0..16 entries; text 1..1024 bytes, общий budget 16384 bytes |
| assistant.max_context_bytes | exact int, 115..65536; bool запрещён; фактический envelope fit проверяется до branch calls |
| assistant.max_result_bytes | exact int, 304..65536; bool запрещён |
| max_result_bytes | exact int, 824..131072; bool запрещён |

Для text применяется только уже approved normalization:

1. strict UTF-8;
2. Unicode NFC;
3. strip() Unicode whitespace только по краям;
4. запрет C0/C1 controls, DEL и Unicode category Cf;
5. внутренние whitespace, case, punctuation, locale и порядок слов не
   переписываются.

IDs не normalise-ятся. task после validation является одновременно
AssistantRequest.task и SimulateMeRequest.query; labels и IDs составляют один
общий immutable caller-owned namespace. Не создаются semantic aliases,
synonyms или server-owned IDs.

Unknown fields, missing required fields, wrong scalar/container types,
duplicate IDs, bounds overflow и invalid controls дают
COMPARE_INVALID_REQUEST до любого branch call. Валидация не читает current
context, vault или provider. Значение
max_result_bytes < MIN_MAX_RESULT_BYTES_V1 или меньше contract-derived
minimum_compare_result_bytes(validated option_ids) также даёт
COMPARE_INVALID_REQUEST до любого branch call. Второе сравнение выполняется
после validation option IDs, но до любого branch call; labels в этот minimum
не входят, поскольку минимальный error/error result содержит только root
option_ids из request namespace.

До branch calls Compare обязан построить ровно один canonical
AssistantReasoningEnvelopeV1 из validated task, options,
explicit_constraints, explicit_goals и explicit_context, используя
AssistantCanonicalJsonEncoderV1 и exact key/order rules Assistant v1. Если
context_bytes этого envelope больше assistant.max_context_bytes, request
получает COMPARE_INVALID_REQUEST до вызова Assistant или Simulate Me; silent
dropping explicit input и branch-local error вместо preflight запрещены.

Для обязательного в Compare списка хотя бы из одного минимального option
contract-derived lower bound равен:

    MIN_ASSISTANT_CONTEXT_BYTES_V1 = 115

Это canonical UTF-8 размер envelope с task="x", option {id="a", label="x"} и
тремя пустыми Assistant arrays. Значение 115 — только нижняя bound; для каждого
реального request всё равно проверяется полный context_bytes. При изменении
Assistant envelope schema, key order или Compare minimum options значение
пересчитывается до изменения request bound.

### 3.3. Operation control boundary

`CompareRequestV1` намеренно содержит только immutable semantic input. Для каждой
execution application boundary обязана получить explicit control context:

    CompareExecutionContextV1 {
      cancellation: CancellationToken
      deadline:    float  # finite absolute reading from monotonic clock
    }

    CompareExecutorV1.execute(
        request: CompareRequestV1,
        *,
        execution: CompareExecutionContextV1,
    ) -> CompareResultV1 | CompareErrorV1

`deadline` — finite absolute value одной injected monotonic clock, а не wall-clock,
user text или branch input; default/unbounded execution запрещён. Context создаётся
один раз на Compare operation, тот же cancellation token и deadline передаются
обоим branch adapters, а context не попадает в canonical request/result/policy
JSON.

Coordinator обязан проверить cancellation и deadline до preflight, перед каждым
branch invocation, во время ожидания branch и после её возврата. Adapter должен
делать underlying operation interruptible: наблюдать оба сигнала во время
блокирующего чтения/вычисления, остановить работу и вернуть typed
cancellation/timeout mapping. Adapter, который может вызвать только
`BuildSimulateMe.execute(SimulateMeRequest)` без этих controls, не является
валидной Compare dependency. Нельзя оставлять thread/task работающим после
deadline, retry или hidden fallback.

Если cancellation token замечен до завершения обеих веток, после bounded adapter
cleanup применяется top-level `COMPARE_CANCELLED` из §8.2; completed branch не
возвращается отдельно. Если истёк deadline, in-flight branch получает
`COMPARE_BRANCH_TIMEOUT`; ещё не начатая branch не вызывается и получает тот же
typed timeout wrapper. Уже завершённая другая branch сохраняется по §8.1.
Если наблюдаются оба сигнала, cancellation имеет приоритет.

## 4. Exact construction и execution independence

После успешной Compare request validation строятся две независимые typed copies:

    AssistantRequest(
        task=request.task,
        options=tuple(AssistantOption(id=o.id, label=o.label)
                      for o in request.options),
        explicit_constraints=request.assistant.explicit_constraints,
        explicit_goals=request.assistant.explicit_goals,
        explicit_context=request.assistant.explicit_context,
        max_context_bytes=request.assistant.max_context_bytes,
        max_result_bytes=request.assistant.max_result_bytes,
    )

    SimulateMeRequest(
        query=request.task,
        options=tuple(SimulateMeOption(id=o.id, label=o.label)
                      for o in request.options),
    )

Это construction rule, а не новый semantic interpretation. Compare не
добавляет поля в Assistant или Simulate Me DTO.

Обе ветки должны быть попытаны не более одного раза в рамках одного Compare
operation. Ветка не получает output другой ветки. Реализация может исполнять
эти независимые попытки последовательно в логическом порядке Assistant, затем
Simulate Me, либо безопасно параллельно, если сохраняются:

- at-most-once invocation каждой ветки;
- отсутствие shared mutable branch state;
- один и тот же immutable `CompareExecutionContextV1` с общей cancellation/
  deadline policy;
- сохранение branch result в свой wrapper;
- отсутствие retry, fallback, hidden provider replacement или short-circuit
  из-за содержания результата первой ветки.

Даже если одна ветка вернула abstention или error, вторая ветка выполняется,
если top-level operation не отменена и deadline ещё не истёк. После deadline
вторая branch не вызывается и получает typed timeout wrapper по §3.3. Execution
order не является evidence и не меняет Delta.

Текущий Stage 6 executor для Simulate Me — application-level
`BuildSimulateMe.execute(SimulateMeRequest)`. Compare не должен зависеть от Web
`SimulateMeRequestPayload`, FastAPI, browser или raw HTTP body. Однако прямой
вызов этого метода без control seam недостаточен: будущий Compare adapter
обязан передать `CompareExecutionContextV1` и сделать interruptible approved
current-context read и matching operation. Без этого adapter Stage 6 executor
не является валидной Compare dependency; failure/invalid current context
остаётся typed SimulateMeResult abstention согласно текущему контракту.

## 5. Typed branch wrappers

Abstention — это не error. Wrapper сохраняет distinction между typed result,
typed abstention и typed error, не добавляя branch semantics.

### 5.1. Assistant branch

    CompareAssistantResultBranchV1 {
      state: "result"
      result: AssistantResultEnvelopeV1  # kind = recommendation | analysis
      error: null
    }

    CompareAssistantAbstentionBranchV1 {
      state: "abstention"
      result: AssistantResultEnvelopeV1  # kind = abstention
      error: null
    }

    CompareAssistantErrorBranchV1 {
      state: "error"
      result: null
      error: CompareBranchErrorV1
    }

    AssistantBranchV1 =
        CompareAssistantResultBranchV1
      | CompareAssistantAbstentionBranchV1
      | CompareAssistantErrorBranchV1

AssistantResultEnvelopeV1 передаётся без semantic rewrite. Его output_label,
rationale, evidence_refs, constraints_used, objectives_used, uncertainty,
abstention_code и contract_version остаются authority Assistant contract.

### 5.2. Simulate Me branch

    CompareSimulateMeResultBranchV1 {
      state: "result"
      result: SimulateMeResult  # kind = prediction
      error: null
    }

    CompareSimulateMeAbstentionBranchV1 {
      state: "abstention"
      result: SimulateMeResult  # kind = abstention
      error: null
    }

    CompareSimulateMeErrorBranchV1 {
      state: "error"
      result: null
      error: CompareBranchErrorV1
    }

    SimulateMeBranchV1 =
        CompareSimulateMeResultBranchV1
      | CompareSimulateMeAbstentionBranchV1
      | CompareSimulateMeErrorBranchV1

SimulateMeResult передаётся без удаления или добавления полей:
selected_option, evidence_refs, contextual_evidence_refs, temporal_caveats,
abstention_code, derivation_version, policy_id и policy_fingerprint остаются
отдельной Simulate Me namespace.

### 5.3. Safe branch error

    CompareBranchErrorV1 {
      code:    CompareBranchErrorCodeV1
      message: string
    }

Закрытый CompareBranchErrorCodeV1:

    COMPARE_BRANCH_INVALID_REQUEST
    COMPARE_BRANCH_CANCELLED
    COMPARE_BRANCH_TIMEOUT
    COMPARE_BRANCH_UNAVAILABLE
    COMPARE_BRANCH_FAILURE
    COMPARE_BRANCH_MALFORMED_RESULT
    COMPARE_BRANCH_RESULT_TOO_LARGE
    COMPARE_BRANCH_RESULT_INVALID

message выбирается только из следующей фиксированной таблицы и не содержит
request text, rationale, claim, note body, exception, endpoint, model или
provider details:

| Code | Fixed message |
| --- | --- |
| COMPARE_BRANCH_INVALID_REQUEST | compare branch request failed validation |
| COMPARE_BRANCH_CANCELLED | compare branch operation cancelled |
| COMPARE_BRANCH_TIMEOUT | compare branch operation timed out |
| COMPARE_BRANCH_UNAVAILABLE | compare branch unavailable |
| COMPARE_BRANCH_FAILURE | compare branch failed |
| COMPARE_BRANCH_MALFORMED_RESULT | compare branch result is malformed |
| COMPARE_BRANCH_RESULT_TOO_LARGE | compare branch result exceeds byte budget |
| COMPARE_BRANCH_RESULT_INVALID | compare branch result failed validation |

Маппинг выполняется на application boundary:

- ASSISTANT_INVALID_REQUEST -> COMPARE_BRANCH_INVALID_REQUEST;
- ASSISTANT_CANCELLED -> COMPARE_BRANCH_CANCELLED;
- ASSISTANT_TIMEOUT -> COMPARE_BRANCH_TIMEOUT;
- ASSISTANT_PROVIDER_UNAVAILABLE -> COMPARE_BRANCH_UNAVAILABLE;
- ASSISTANT_PROVIDER_FAILURE -> COMPARE_BRANCH_FAILURE;
- ASSISTANT_MALFORMED_RESULT -> COMPARE_BRANCH_MALFORMED_RESULT;
- ASSISTANT_RESULT_TOO_LARGE -> COMPARE_BRANCH_RESULT_TOO_LARGE;
- ASSISTANT_RESULT_INVALID -> COMPARE_BRANCH_RESULT_INVALID;
- текущие application errors SIMULATE_ME_INVALID_REQUEST и
  SIMULATE_ME_RESULT_INVALID маппятся соответственно в
  COMPARE_BRANCH_INVALID_REQUEST и COMPARE_BRANCH_RESULT_INVALID.

Web-only errors SIMULATE_ME_CONTENT_TOO_LARGE и
SIMULATE_ME_VAULT_UNAVAILABLE не импортируются в application DTO. Если
будущая composition adapter boundary получает безопасный typed unavailable или
byte-budget outcome от внешней boundary, она маппит его в
COMPARE_BRANCH_UNAVAILABLE или COMPARE_BRANCH_RESULT_TOO_LARGE; raw Web
payload не пересекает Compare boundary. Unknown exception не становится
message: он маппится в COMPARE_BRANCH_FAILURE только на typed adapter
boundary.

## 6. Exact Compare result DTO

    CompareDeltaV1 {
      relation:                       CompareDeltaRelationV1
      assistant_state:                "result" | "abstention" | "error"
      simulate_me_state:              "result" | "abstention" | "error"
      assistant_selected_option_id:   string | null
      simulate_me_selected_option_id: string | null
      assistant_evidence_shape:       "unavailable" | "empty" | "present"
      simulate_me_evidence_shape:     "unavailable" | "empty"
                                      | "supporting_only"
                                      | "contextual_only"
                                      | "supporting_and_contextual"
      simulate_me_temporal_caveat:    bool
      explanation_template:           CompareDeltaRelationV1
      explanation:                    string
    }

    CompareResultV1 {
      option_ids:          tuple[string, ...]  # request order, 1..8
      assistant:           AssistantBranchV1
      simulate_me:         SimulateMeBranchV1
      delta:               CompareDeltaV1
      derivation_version: "compare-v1"
      policy_id:           "compare-structural-delta-v1"
      policy_fingerprint: "sha256:" + 64 lowercase hex chars
    }

option_ids — только request-local IDs в caller order; labels и semantic
identity не дублируются. Это не canonical field и не persistence identity.

assistant_state и simulate_me_state являются typed projection wrappers, а не
новым branch result. assistant_evidence_shape и simulate_me_evidence_shape
сообщают только наличие branch-local refs:

- Assistant: empty или present по длине именно
  AssistantResultEnvelopeV1.evidence_refs; Assistant input refs и rationale в
  эту shape не попадают;
- Simulate Me: supporting_only, contextual_only или
  supporting_and_contextual по двум отдельным lists, либо empty;
- unavailable используется только для error wrapper;
- перед projection Compare повторно проверяет temporal correspondence:
  `unknown_claim_ids` — claim IDs из `evidence_refs` и
  `contextual_evidence_refs` с `evidence_at="unknown"`, а
  `caveat_claim_ids` — claim IDs из `temporal_caveats` с code
  `evidence_at_unknown`; множества обязаны совпадать. При несовпадении branch
  получает safe `COMPARE_BRANCH_RESULT_INVALID` error wrapper до построения
  Delta. Затем применяется §8: valid sibling branch сохраняется в partial
  result, а Delta строится из error wrapper (`simulate_me_error` или
  `assistant_error`); при двух error wrappers используется `both_error`.
  `simulate_me_temporal_caveat` вычисляется как
  `bool(unknown_claim_ids)`, а не принимается из непроверенного boolean.

Эти shapes не сравнивают тексты, UUID, rationale, evidence strength или
количество refs между ветками. Branch refs остаются только в своём wrapper.

## 7. Closed structural Delta taxonomy

CompareDeltaRelationV1 — закрытая taxonomy из восьми mutually exclusive
codes:

    same_selected_option
    different_selected_options
    assistant_only_selected
    simulate_me_only_selected
    neither_selected
    assistant_error
    simulate_me_error
    both_error

Алгоритм выбора relation:

1. Если обе ветки state=error, relation = both_error.
2. Если только Assistant state=error, relation = assistant_error.
3. Если только Simulate Me state=error, relation = simulate_me_error.
4. Если обе ветки не error и обе имеют non-null selected option, сравнить
   только assistant_selected_option_id и
   simulate_me_selected_option_id byte-for-byte: равные ID дают
   same_selected_option, разные ID дают different_selected_options.
5. Если только Assistant имеет selected option, relation =
   assistant_only_selected.
6. Если только Simulate Me имеет selected option, relation =
   simulate_me_only_selected.
7. Если ни одна ветка не имеет selected option, relation = neither_selected.

Selected option считается существующим только для validated typed result:

- Assistant должен иметь kind=recommendation и exact caller-owned option pair;
- Simulate Me должен иметь kind=prediction и exact caller-owned option pair;
- ID обязан входить в CompareResultV1.option_ids.

Wrapper state отдельно сохраняет, была ли non-selected ветка именно
abstention, успешным analysis/open recommendation без выбора или error.
relation не скрывает это различие: оно доступно через branch wrapper и
*_state.

### 7.1. Deterministic human-readable templates

explanation_template всегда равен relation. explanation выбирается только по
этой закрытой таблице:

| Template | Exact text |
| --- | --- |
| same_selected_option | Обе ветки выбрали один и тот же вариант. |
| different_selected_options | Ветки выбрали разные варианты. |
| assistant_only_selected | Assistant выбрал вариант, а Simulate Me не выбрал вариант. |
| simulate_me_only_selected | Simulate Me выбрал вариант, а Assistant не выбрал вариант. |
| neither_selected | Ни одна ветка не вернула выбранный вариант. |
| assistant_error | Assistant завершился ошибкой; результат Simulate Me сохранён отдельно. |
| simulate_me_error | Simulate Me завершился ошибкой; результат Assistant сохранён отдельно. |
| both_error | Обе ветки завершились ошибкой. |

Template text не подставляет labels, claim text, rationale, evidence,
confidence, scores или raw error. В частности, Delta не говорит, что владелец
непоследователен, иррационален, склонен к bias, имеет risk profile или должен
следовать recommendation.

## 8. Partial, abstention и top-level error policy

### 8.1. Branch outcome matrix

| Assistant branch | Simulate Me branch | Top-level outcome | Delta |
| --- | --- | --- | --- |
| result | prediction | полный CompareResultV1 | ID equality или difference |
| result без selected option | prediction | полный result | simulate_me_only_selected |
| abstention | prediction | полный result | simulate_me_only_selected |
| result с selected option | abstention | полный result | assistant_only_selected |
| result без selected option | abstention | полный result | neither_selected |
| abstention | abstention | полный result | neither_selected |
| error | любой bounded non-error result/abstention | partial result: успешная ветка сохранена | assistant_error |
| bounded non-error result/abstention | error | partial result: успешная ветка сохранена | simulate_me_error |
| error | error | bounded structural result с двумя errors | both_error |

Abstention code каждого branch result сохраняется без reinterpretation.
Например, insufficient_or_invalid_current_context остаётся именно Simulate Me
abstention, а не превращается в error или Assistant evidence.

Branch error не отменяет и не маскирует успешный другой branch. Нельзя
заменять partial result пустым blended explanation, retry-ить неуспешную
ветку, выбирать результат другой ветки как fallback или считать abstention
ошибкой.

### 8.2. Top-level errors

    CompareErrorCodeV1:
      COMPARE_INVALID_REQUEST
      COMPARE_CANCELLED
      COMPARE_COMPOSITION_INVALID
      COMPARE_RESULT_TOO_LARGE

Публичная projection top-level error содержит только {code, message} с
фиксированными message:

| Code | Fixed message |
| --- | --- |
| COMPARE_INVALID_REQUEST | compare request failed validation |
| COMPARE_CANCELLED | compare operation cancelled |
| COMPARE_COMPOSITION_INVALID | compare composition failed validation |
| COMPARE_RESULT_TOO_LARGE | compare result exceeds requested byte budget |

Top-level error применяется ровно в следующих случаях:

- COMPARE_INVALID_REQUEST: Compare DTO не прошёл pre-branch validation;
- COMPARE_CANCELLED: global Compare cancellation обнаружена до безопасного
  завершения обеих branch attempts; completed branch не возвращается отдельно;
- истёкший operation deadline не становится top-level cancellation: in-flight или
  not-yet-started branch получает `COMPARE_BRANCH_TIMEOUT` по §3.3, а completed
  sibling сохраняется по branch outcome matrix;
- COMPARE_COMPOSITION_INVALID: Compare-owned wrapper/composition нарушает
  closed Compare invariant после того, как branch output уже был принят
  соответствующим branch validator. Это касается только дефекта самой
  composition shape, а не содержимого branch result;
- COMPARE_RESULT_TOO_LARGE: canonical full Compare result превышает
  request.max_result_bytes.

Нельзя использовать top-level error для ordinary branch unavailable, provider
failure, branch timeout, branch malformed result или branch abstention: они
остаются соответствующим branch wrapper, чтобы сохранить другую ветку.

Не допускаются truncation, dropping rationale/evidence refs, alternate
serialization, silent option removal, hidden fallback или повторная попытка
после COMPARE_RESULT_TOO_LARGE.

## 9. Policy identity и canonical serialization

Нормативные identity:

    derivation_version = "compare-v1"
    policy_id          = "compare-structural-delta-v1"

Policy fingerprint вычисляется как SHA-256 UTF-8 bytes exact canonical JSON
ниже, без завершающего newline:

    {"branch_inputs":"shared-task-options-only-v1","branch_invocation":"one-independent-attempt-each-unless-operation-cancelled-or-deadline-expires-v2","branch_output":"typed-result-abstention-error-v1","delta_evidence":"preserve-namespaces-no-cross-comparison-v1","delta_human_text":"fixed-templates-v1","delta_option_equality":"exact-request-local-id-v1","delta_relation_codes":["same_selected_option","different_selected_options","assistant_only_selected","simulate_me_only_selected","neither_selected","assistant_error","simulate_me_error","both_error"],"execution_context":"assistant-explicit-only;simulate-me-current-approved-context-only","execution_control":"shared-monotonic-deadline-and-cancellation-token-v1","no_side_effects":"ephemeral-read-only-v1","version":"1"}

Ожидаемый fingerprint:

    sha256:518da5bb49968fb22ba956b9291588c6e90fb32cd0d4458f6c17f6f26a40694e

Для outer result budget используется contract-derived floor и request-specific
minimum:

    MIN_MAX_RESULT_BYTES_V1 = 824

Это длина canonical UTF-8 bytes минимального valid CompareResultV1 с
option_ids=["a"], двумя bounded branch errors с наиболее короткой фиксированной
парой COMPARE_BRANCH_FAILURE / compare branch failed, structural relation
both_error, пустыми nullable/list fields где это разрешено и фиксированными
Compare policy identifiers. Для каждого validated request вычисляется
`minimum_compare_result_bytes(option_ids)` как длина того же canonical
error/error fixture с фактическим `option_ids` в request order. Например,
minimum для option_ids=["a","b"] равен 828 bytes, поэтому max_result_bytes
824 отклоняется до branch calls. Exact fixture определяется полями §6, fixed
messages §5.3, result key order ниже и обеими branch error wrappers; значение
не является оценкой и пересчитывается при изменении любого из этих полей.

Canonical CompareResultV1 serialization имеет следующие правила:

1. JSON UTF-8, без BOM и trailing newline;
2. separators ровно , и :, insignificant spaces отсутствуют;
3. non-ASCII символы идут напрямую в UTF-8, ensure_ascii=true запрещён;
4. string escaping использует тот же approved
   AssistantCanonicalJsonEncoderV1, включая short escapes и lowercase
   \u00xx только для остальных control code points;
5. root key order ровно option_ids, assistant, simulate_me, delta,
   derivation_version, policy_id, policy_fingerprint;
6. wrapper key order ровно state, result, error;
7. CompareBranchErrorV1 key order ровно code, message;
8. CompareDeltaV1 key order ровно в порядке полей §6;
9. Assistant result использует exact field order и serializer Assistant v1;
10. Simulate Me result использует exact field order из текущего application
    DTO: kind, selected_option, evidence_refs,
    contextual_evidence_refs, temporal_caveats, abstention_code,
    derivation_version, policy_id, policy_fingerprint;
11. nested SimulateMeOption key order ровно id, label;
12. nested SimulateMeEvidenceRef и SimulateMeContextualEvidenceRef key order
    ровно claim_id, dimension, note_ids, evidence_at;
13. nested SimulateMeTemporalCaveat key order ровно code, claim_id;
14. nullable fields всегда присутствуют как JSON null; arrays всегда
    присутствуют и сохраняют approved branch order;
15. UUID сериализуются как lowercase canonical str(UUID);
16. aware datetime сначала обязан успешно переводиться в UTC в
    representable range year 0001..9999; failure такого conversion (включая
    `OverflowError`/`ValueError` на границе диапазона) делает branch result
    invalid и не допускается до canonical serialization. После успешного
    conversion datetime сериализуется ровно как YYYY-MM-DDTHH:MM:SS.ffffffZ:
    год всегда 4 цифры, fractional seconds всегда ровно 6 цифр, включая
    trailing zeros, без удаления fractional part и без альтернативного offset
    notation; unknown остаётся literal string unknown;
17. result_bytes равен длине canonical UTF-8 bytes. Exact boundary
    result_bytes == request.max_result_bytes принимается, overflow даёт
    COMPARE_RESULT_TOO_LARGE.

Compare не пересчитывает и не заменяет Assistant contract version или
Simulate Me derivation/policy fingerprint. Composition fingerprint фиксирует
только правила orchestration и structural Delta; смена любого branch
contract требует новой Compare policy version и нового fingerprint.

## 10. Privacy и no-side-effect boundary

Compare v1 разрешает только bounded in-memory state:

- validated Compare request;
- две branch request copies;
- typed branch wrappers;
- bounded structural Delta;
- ephemeral canonical serialization metadata.

Compare v1 не:

- пишет в second-brain-vault, proposal, Safe Write, Git или новую DB;
- создаёт history, prediction log, audit record, cache, queue, telemetry или
  persistent calibration state;
- вызывает provider/LLM для Delta;
- выбирает provider или model для Assistant;
- читает Search, vault или Self Model напрямую;
- объединяет Assistant explicit context с Simulate Me personal context;
- логирует task, labels, rationale, claim text, note body, UUID mapping,
  absolute path, raw exception, secret или provider payload;
- делает behavioral/personality/bias/diagnosis/risk inference;
- вычисляет confidence, score, probability, ranking, recency, frequency или
  hidden weight.

Возможный будущий Advisor provider получает только explicit-only Assistant
payload по отдельному approved provider gate из #162. Наличие Compare contract
не является разрешением на network, credentials, real Assistant или
production enablement.

## 11. Exact runtime dependencies

Будущая provider-free composition implementation может зависеть только от:

1. approved Assistant DTO/validator/error boundary:
   AssistantRequest, AssistantOption, AssistantResultEnvelopeV1,
   AssistantAbstentionCode, AssistantErrorCode и их exact canonical
   validation;
2. текущего Stage 6 application core:
   SimulateMeRequest, SimulateMeOption, SimulateMeResult,
   SimulateMeResultKind, SimulateMeAbstentionCode, SimulateMeError,
   validate_simulate_me_request, validate_simulate_me_result, BuildSimulateMe;
3. standard-library immutable DTO/serialization и explicit
   `CompareExecutionContextV1` с `CancellationToken` и injected monotonic clock;
   оба branch adapter обязаны propagate context и быть interruptible.

Не входят dependency list:

- FileSystemVaultReader вызванный из Compare напрямую;
- SearchIndexPort, SelfRetrieval, SelfModel direct calls;
- Web Pydantic payloads, FastAPI, CLI, browser storage;
- existing LlmPort как скрытый Advisor substitute;
- provider SDK, network, credentials, embedding/RAG/vector DB;
- новые packages, schema migrations, NoteType, persistence или vault changes.

Реальный Assistant implementation и любой Advisor provider остаются отдельной
owner-gated задачей. Compare v1 design не предполагает, что этот provider уже
существует.

## 12. Deterministic test matrix для будущего runtime

| Сценарий | Обязательный результат |
| --- | --- |
| корректные shared task/options и корректные Assistant inputs | обе typed branch requests построены из immutable copies |
| отсутствующее/лишнее поле, неверный тип, пустой options, 9 options, duplicate/bad ID | `COMPARE_INVALID_REQUEST`; ни одна ветка не вызвана |
| text controls, invalid UTF-8, blank, NFC/edge-strip, per-field/aggregate overflow | exact validation; branch не вызывается; raw input отсутствует в error |
| Assistant получает Simulate Me context/result | невозможно по construction rule; regression test падает при обнаружении |
| Simulate Me получает Assistant constraints/goals/context | невозможно по construction rule; regression test падает при обнаружении |
| output одной branch передан другой branch | ни один call/order не может его наблюдать |
| обе ветки выбрали равные IDs | `same_selected_option`; labels и semantics не сравниваются |
| обе ветки выбрали разные IDs | `different_selected_options`, даже если labels выглядят похоже |
| Assistant selected, Simulate Me abstained | `assistant_only_selected`; Simulate Me abstention сохраняется |
| Simulate Me selected, Assistant abstained | `simulate_me_only_selected`; Assistant abstention сохраняется |
| Assistant recommendation/analysis без выбора, Simulate Me prediction | `simulate_me_only_selected`; Assistant kind сохраняется |
| ни одна branch не выбрала option | `neither_selected`; exact abstention/result states сохраняются |
| одна branch typed error, другая result/abstention | partial result; успешная другая branch сохраняется |
| обе branch typed errors | bounded structural result с обоими wrappers и `both_error` |
| invalid selected pair или неверная branch policy | соответствующий safe branch result-invalid error; sibling branch сохраняется |
| невозможная Compare-owned wrapper/composition shape | `COMPARE_COMPOSITION_INVALID` |
| Delta наблюдает provider/Search/vault/Self Model seam | call отсутствует; test падает |
| различаются rationale, UUID, note IDs, claim text или evidence list | branches остаются раздельными; content не сравнивается и не union-ится |
| deterministic template text | exact table text; confidence, bias, personality, risk и advice отсутствуют |
| operation deadline истёк до branch invocation | branch call не выполняется; affected branch получает `COMPARE_BRANCH_TIMEOUT`, completed sibling сохраняется |
| deadline истёк во время branch execution | interruptible adapter прекращает operation; branch получает `COMPARE_BRANCH_TIMEOUT` |
| global cancellation до завершения обеих branches | `COMPARE_CANCELLED`; partial outward result отсутствует |
| result точно на outer byte cap | accepted |
| result выше outer byte cap | `COMPARE_RESULT_TOO_LARGE`; truncation/retry отсутствуют |
| policy canonical JSON | exact policy_id, version и fingerprint |
| нет provider/network/write/persistence | deterministic no-side-effect test |

Эта матрица описывает acceptance для будущей implementation task; в #163
runtime code и runtime tests не добавляются.

## 13. ACCEPT / DEFER / HUMAN_REQUIRED

| Area | Status |
| --- | --- |
| shared caller task/options namespace | ACCEPT |
| Assistant explicit-only input isolation | ACCEPT |
| Simulate Me current approved boundary isolation | ACCEPT |
| typed result/abstention/error wrappers | ACCEPT |
| preserving successful branch on other branch error | ACCEPT |
| exact request-local option ID equality | ACCEPT |
| closed structural relation taxonomy | ACCEPT |
| deterministic template explanation | ACCEPT |
| separate branch evidence namespaces and structural presence only | ACCEPT |
| canonical bounds, serialization, version and fingerprint | ACCEPT |
| semantic “why they differ”, rationale/evidence-strength comparison | DEFER |
| fuzzy, alias, synonym, semantic, personality, bias or risk inference | DEFER |
| Assistant runtime/provider/network/credentials/privacy integration | DEFER to separately approved implementation/provider gate |
| Compare Web/CLI/API projection | DEFER to separate implementation scope |
| prospective prediction logging, calibration, Active Learning, persistence | DEFER |
| schema/vault changes, embeddings/RAG/vector DB | DEFER |
| new owner/provider/privacy/canonical semantic decision in this contract | HUMAN_REQUIRED: none |

Итог: Compare v1 остаётся механической композиционной boundary. Если будущий
consumer требует richer semantic explanation, он должен создать отдельный
design decision; его нельзя незаметно добавить в Delta.
