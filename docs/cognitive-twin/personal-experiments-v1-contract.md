# Cognitive Twin v3 / Stage 14 — Personal Experiments v1

Статус: **NORMATIVE CONTRACT / PHASE 14.0 COMPLETE; PHASE 14.1 READ-SIDE RECORDS COMPLETE; PHASE 14.2 REVIEWED SAFE WRITE COMPLETE; EVALUATOR, WEB/API AND UI NOT STARTED**.

Issue: [#304](https://github.com/MikeMoore1337/second-brain/issues/304).
Этот документ является единственным нормативным контрактом Stage 14. Он
разрешает только последующие implementation gates 14.1–14.6, перечисленные в
разделе «Карта реализации». Он не сам по себе разрешает запись в vault,
Web/API, UI, provider, сеть, новые зависимости или изменение
second-brain-vault.

## 1. Назначение и граница

Personal Experiments v1 отвечает на ограниченный вопрос:

> Что было явно заявлено как гипотеза и вмешательство, какие явно выбранные
> записи Stage 12 наблюдались в заданном временном окне и какой описательный
> результат поддержан этими записями?

Полный v1-поток:

    current exact Goal
      -> exact Stage 12 progress definition
      -> reviewed experiment definition (hypothesis + intervention + baseline)
      -> explicit reviewed activation
      -> explicit enrollment of exact Stage 12 observations
      -> provider-free bounded result
      -> explicit reviewed terminal event
      -> explicit reviewed reassessment

Эксперимент не доказывает причинность. Временная близость, направление
изменения, число наблюдений и совпадение с гипотезой не являются доказательством
того, что вмешательство вызвало результат. В v1 нет причинного вывода,
вероятности, статистической значимости, confidence, универсального score,
победителя, оптимальности, рекомендации или автоматической адаптации.

### 1.1. Зафиксированные решения владельца

Следующие решения обязательны и не могут быть ослаблены implementation gate:

1. Provider-free: provider, LLM, внешний API, сеть и телеметрия не входят в
   Stage 14.
2. Каноническими являются только reviewed companion records Stage 14,
   записанные существующим Safe Write. Результат является rebuildable derived
   read model и сам не записывается в vault.
3. Не добавляются новый глобальный NoteType, новая версия глобальной схемы,
   отдельная база, operational store или изменение second-brain-vault.
   Companion records используют существующий managed type: zettel.
4. Identity эксперимента привязана к точной паре
   experiment_definition_id + experiment_definition_fingerprint.
5. Authority для Goal Progress остаётся у Stage 12. Stage 14 не копирует и не
   переопределяет measurement, baseline, target, milestone или математическую
   модель Stage 12.
6. Параллельной модели measurement нет. Каждое наблюдение Stage 14 содержит
   явную reviewed-ссылку на точный Stage 12 observation UUID и fingerprint.
7. Нет автоматического enrollment и inferred baseline. Baseline выбирается
   явно и проходит reviewed validation.
8. Lifecycle reviewed и явный: activation, затем completion или cancellation.
   По exact Goal допускается не более одного активного эксперимента.
9. Stage 13 output не создаёт, не активирует, не enroll-ит и не адаптирует
   эксперимент.
10. Stage 15 не реализуется, не помечается завершённым и не получает новых
    входов из этого контракта.

## 2. Authority model и слои данных

second-brain-vault остаётся единственным canonical source of truth. Текущий
Goal получается через существующий current-vault scan и
GrowthGoalIdentityV1; Goal не выбирается по тексту, label, newest record или
fuzzy match. Stage 12 definition и observations остаются отдельными
canonical companion records и authoritative progress source.

| Слой | Владелец истины | Разрешённое содержание | Запись |
| --- | --- | --- | --- |
| Current Goal | текущий vault + GrowthGoalIdentityV1 | UUID и exact identity fingerprint | только исходным reviewed Goal-потоком |
| Stage 12 progress | Goal Progress v1 | definition, explicit baseline/target/milestones, observation event/value | только существующим Stage 12 Safe Write |
| Stage 14 definition | этот контракт | exact Goal/Stage 12 binding, hypothesis, intervention, baseline strategy | reviewed Stage 14 Safe Write |
| Stage 14 lifecycle | этот контракт | reviewed activation/terminal events | reviewed Stage 14 Safe Write |
| Stage 14 observation enrollment | этот контракт | ссылка на exact Stage 12 observation | reviewed Stage 14 Safe Write |
| Stage 14 reassessment | этот контракт | reviewed disposition и bounded rationale | reviewed Stage 14 Safe Write |
| Experiment result | deterministic derived projection | status, source references, provenance and caveats | никогда не пишется как canonical fact |
| Review plan/token/browser state | process/request-local state | exact plan hash and one-time confirmation | memory-only, не vault и не browser storage |

Ни Outcome Observation, ни Decision Journal, ни Behavioral Self Model, ни Growth
Engine, ни Growth Advisor, ни Growth Learning, ни prospective audit не могут
заменить Stage 12 observation или незаметно стать входом для Stage 14. Они могут
быть показаны как отдельный контекст только в будущем явно разрешённом
контракте; v1 их не читает для результата.

## 3. Версионирование, fingerprints и лимиты

### 3.1. Нормативные идентификаторы

    contract_id                 = personal-experiments-v1
    contract_version            = 1
    experiment_policy_id        = personal-experiment-v1
    derivation_id               = personal-experiment-derivation-v1
    goal_identity_contract      = growth-goal-identity-v1
    progress_contract           = goal-progress-v1

Нормализация fingerprint использует уже принятую в Stage 12 процедуру:
canonical JSON, UTF-8, ensure_ascii=false, sort_keys=true,
separators=(",", ":"), SHA-256 с префиксом sha256:. Fingerprint считается по
семантическому объекту без vault storage-полей (id, type, created, updated,
path, title, review time и supersession metadata).

Нормативный policy payload v1:

    {"baseline_strategies":["stage12_definition_explicit","reviewed_pre_activation_observation"],"causality":"descriptive_non_causal_v1","contract":"personal-experiment-v1","evaluation_window":"activation_inclusive_terminal_exclusive_v1","goal_binding":"growth-goal-identity-exact-v1","lifecycle":"explicit_reviewed_activation_terminal_v1","observation_enrollment":"explicit_reviewed_stage12_link_v1","one_active_per_goal":"exact_goal_v1","persistence":"reviewed_canonical_companion_records_v1","progress_authority":"stage12-goal-progress-v1","provider":"forbidden","reassessment":"reviewed_terminal_only_v1","version":1}

Его fingerprint: sha256:84de7a118c2a17b21c0f002ccc1a542735593a26ca9dd4628808cc19b1438563.
14.1 обязан вычислить этот payload машинно и тестировать literal payload и
fingerprint как пару; расхождение — fail closed.

Ограничения v1 являются отказоустойчивыми, а не рекомендациями:

| Объект | Лимит |
| --- | ---: |
| raw HTTP body | 256 KiB |
| один bounded text (hypothesis, intervention, rationale) | 4 KiB UTF-8 |
| explicit enrolled observation references в одном эксперименте | 200 |
| один canonical companion record payload | 64 KiB |
| derived result | 128 KiB |
| список экспериментов/кандидатов в одном ответе | 200 |

Контроль длины считается до декодирования там, где это возможно, и после
UTF-8/структурной проверки. Ничего не обрезается молча. Неподдержанные поля,
дубликаты JSON-ключей, invalid UTF-8, controls, surrogate, слишком глубокая
структура и превышение лимита отклоняются.

### 3.2. Времена и UUID

Все event/review/as-of timestamps — timezone-aware RFC 3339 UTC с точностью,
принятой базовым контрактом. created не является event time. as_of всегда
передаётся явно; неявное now запрещено. UUID — UUIDv7 там, где новый
canonical record создаёт существующий writer. Клиент не назначает id, created,
path или fingerprints.

## 4. Companion record families

Каждая семья — отдельный managed Markdown/YAML type: zettel с exact scalar
markers. Generic vault metadata может сохраняться по общему контракту, но
record, содержащий Stage 14 marker, проходит отдельный строгий allowlist.
Смысловые данные не прячутся в свободном Markdown body: body остаётся
человеческим представлением, а валидатор разбирает только нормализованные
YAML-поля.

Общий обязательный envelope существующего vault (id, type, created, с
разрешёнными общими updated, tags, links) не меняется. Дополнительные Stage 14
поля перечислены ниже; неизвестное Stage 14 поле — ошибка.

### 4.1. Definition record

Обязательные поля:

    second_brain_personal_experiment: 1
    personal_experiment_kind: definition
    experiment_policy_id: personal-experiment-v1
    experiment_policy_fingerprint: sha256:<64 hex>
    goal_source_uuid: UUID
    goal_identity_fingerprint: sha256:<64 hex>
    goal_progress_definition_id: UUID
    goal_progress_definition_fingerprint: sha256:<64 hex>
    goal_progress_policy_fingerprint: sha256:<64 hex>
    hypothesis: bounded non-empty string
    intervention: bounded non-empty string
    baseline_strategy: stage12_definition_explicit | reviewed_pre_activation_observation
    definition_reviewed_at: UTC timestamp

goal_source_uuid и goal_identity_fingerprint — exact current Goal binding.
goal_progress_definition_id, его fingerprint и policy fingerprint — exact
активная Stage 12 definition chain для этого Goal на момент review.

При stage12_definition_explicit baseline берётся только из явного baseline
Stage 12 definition; отдельное baseline value не копируется. При
reviewed_pre_activation_observation обязательны парные поля:

    baseline_observation_uuid: UUID
    baseline_observation_fingerprint: sha256:<64 hex>

Они указывают на exact Stage 12 observation, созданный до activation. Нельзя
использовать latest/first/average/fallback, Outcome, Behavior, Growth или
provider как baseline.

До activation допускается парная supersession-ссылка:

    supersedes_definition_id: UUID
    supersedes_definition_fingerprint: sha256:<64 hex>

Оба поля отсутствуют или присутствуют вместе. Definition semantic fingerprint
содержит contract/policy identifiers, exact Goal and Stage 12 bindings,
hypothesis, intervention, baseline_strategy и baseline reference (если он
есть). Он не содержит storage identity, review time или supersession metadata.
Identity эксперимента — (id, definition_fingerprint).

### 4.2. Lifecycle record

Каждое событие является отдельным append-only record:

    second_brain_personal_experiment: 1
    personal_experiment_kind: lifecycle
    experiment_policy_id: personal-experiment-v1
    experiment_policy_fingerprint: sha256:<64 hex>
    experiment_definition_id: UUID
    experiment_definition_fingerprint: sha256:<64 hex>
    goal_source_uuid: UUID
    goal_identity_fingerprint: sha256:<64 hex>
    lifecycle_event: activation | completion | cancellation
    event_at: UTC timestamp
    lifecycle_reviewed_at: UTC timestamp

Необязательные supersedes_lifecycle_id и supersedes_lifecycle_fingerprint
также являются парой. Lifecycle semantic fingerprint содержит exact
experiment identity, Goal binding, event kind и event_at, но не
storage/review/supersession metadata.

### 4.3. Observation enrollment record

Observation record не является новой measurement-моделью. Он только явно
enroll-ит точный Stage 12 observation:

    second_brain_personal_experiment: 1
    personal_experiment_kind: observation
    experiment_policy_id: personal-experiment-v1
    experiment_policy_fingerprint: sha256:<64 hex>
    experiment_definition_id: UUID
    experiment_definition_fingerprint: sha256:<64 hex>
    goal_source_uuid: UUID
    goal_identity_fingerprint: sha256:<64 hex>
    goal_progress_definition_id: UUID
    goal_progress_definition_fingerprint: sha256:<64 hex>
    goal_progress_policy_fingerprint: sha256:<64 hex>
    stage12_observation_id: UUID
    stage12_observation_fingerprint: sha256:<64 hex>
    observation_reviewed_at: UTC timestamp

Stage 14 не дублирует observed_at, value, unit, target, milestone или body.
Reader заново загружает Stage 12 observation по exact UUID, проверяет его
fingerprint, текущую definition chain и использует Stage 12 как authority.
Observation semantic fingerprint содержит только перечисленные exact links и
policy identifiers; он не является measurement fingerprint.

Исправление/отмена enrollment — новый record с парной supersession-ссылкой;
исходный record не переписывается. Один и тот же source UUID не даёт два
различных active enrollment leaf в одной identity.

### 4.4. Reassessment record

Reassessment создаётся только после terminal lifecycle и не меняет upstream
данные:

    second_brain_personal_experiment: 1
    personal_experiment_kind: reassessment
    experiment_policy_id: personal-experiment-v1
    experiment_policy_fingerprint: sha256:<64 hex>
    experiment_definition_id: UUID
    experiment_definition_fingerprint: sha256:<64 hex>
    goal_source_uuid: UUID
    goal_identity_fingerprint: sha256:<64 hex>
    result_fingerprint: sha256:<64 hex>
    evaluation_as_of: UTC timestamp
    evaluation_policy_fingerprint: sha256:<64 hex>
    disposition: continue | stop | repeat_with_new_definition | hold | not_decided
    rationale: bounded non-empty string
    reassessment_reviewed_at: UTC timestamp

result_fingerprint относится к конкретному immutable derived result с теми же
identity и evaluation_as_of; устаревший или source-drift result нельзя
подтвердить. Повторная reassessment — новый append-only record с optional
supersession pair. Ни одно disposition не записывает Goal, plan, Progress,
Growth или Decision Compass.

## 5. Exact binding и жизненный цикл

### 5.1. Binding rules

Для каждой операции сервер заново читает canonical current state и проверяет:

1. exact current Goal UUID и GrowthGoalIdentityV1 fingerprint;
2. exact active Stage 12 definition UUID, definition fingerprint и policy
   fingerprint для этого Goal;
3. exact experiment definition identity и её fingerprint;
4. lifecycle chain без неоднозначных active/terminal leaves;
5. для observation — exact Stage 12 observation UUID и fingerprint, его
   definition binding, explicit observed_at и текущую source chain.

Любое несовпадение означает source drift или not-comparable state. Система не
пытается найти «похожую» цель, новую definition или другой observation.
Retargeting требует новой definition и новой exact identity.

### 5.2. State machine

| Состояние | Обязательные факты | Допустимое действие |
| --- | --- | --- |
| planned | reviewed definition, нет activation | review/apply activation или reviewed pre-activation correction |
| active | ровно одна valid activation, нет terminal | explicit observation enrollment, completion или cancellation |
| completed | activation + completion | read result и reassessment |
| cancelled | activation + cancellation | read cancelled, reassessment только явно разрешённым UI-flow |
| superseded | planned definition заменена до activation | только чтение исторической цепочки |

invalid и not_evaluated — result/read states, не lifecycle records.

Activation требует exact current Goal/Stage 12 definition, explicit event time,
reviewed apply и отсутствия другого active experiment с той же exact Goal
identity. Проверка one-active выполняется в момент apply и fail closed при
неоднозначной цепочке.

Completion/cancellation разрешены только для active identity и требуют
explicit event time не раньше activation. Terminal interval —
[activation_at, terminal_at). Для active experiment evaluation window
заканчивается explicit as_of; implicit current time запрещено. Observation
enrollment разрешён только active experiment и source observation с
observed_at внутри окна. После terminal новые semantic enrollments не
добавляются; correction может сохранить только ту же exact source identity и
проходит повторную валидацию.

Correction и supersession append-only. Исправление activation timestamp не
может сменить Goal, Stage 12 definition или experiment identity. После terminal
нельзя создать вторую activation/terminal ветку, чтобы получить удобный
результат.

### 5.3. Baseline и историческая реконструкция

Baseline valid только если стратегия definition выполнена буквально:

- stage12_definition_explicit: Stage 12 definition содержит explicit baseline,
  а её fingerprint и policy всё ещё exact;
- reviewed_pre_activation_observation: ссылка ведёт на один exact Stage 12
  observation с известным explicit event time, меньшим activation time, и
  соответствующей definition.

Нет auto enrollment, inferred baseline, created-time fallback, retrospective
выбора по newest или восстановления из удалённого/неизвестного source.

Исторический результат rebuildable по Stage 14 records, Stage 12 records и
fingerprints, доступным в указанном as_of контексте. Текущий drift не
перепривязывает историю: provenance показывает drift, а результат становится
source_changed или not_comparable, если exact source нельзя подтвердить.
Нельзя объявить историческую причинность из-за того, что современный Goal
теперь имеет другой текст или definition.

## 6. Derived result v1

### 6.1. Request и источники

Вычислитель принимает только exact request:

    experiment_definition_id: UUID
    experiment_definition_fingerprint: sha256:<64 hex>
    as_of: explicit UTC timestamp

Он provider-free, deterministic, bounded, rebuildable, без записи и сети.
Перед оценкой он заново читает definition, lifecycle, enrollment leaves,
current exact Goal и Stage 12 records; client payload не является authority.
Используются только явно enrolled exact Stage 12 observations, которые после
всех проверок попадают в activation/terminal window. Не-enrolled observations,
Outcome, Decision Journal, Behavior, Growth, Advisor и telemetry исключаются.

Математика numeric/milestone progress — только существующий Stage 12 evaluator.
Stage 14 добавляет семантическую оболочку, не второй parallel measurement
algorithm. Все входы, excluded reasons и Stage 12 status перечисляются в
bounded provenance.

### 6.2. Result vocabulary и precedence

Единственные Stage 14 result statuses:

    insufficient_evidence
    observed_toward_target
    observed_away_from_target
    observed_no_clear_change
    target_met
    not_comparable
    source_changed
    cancelled
    not_evaluated

target_met допустим только когда именно Stage 12 evaluator вернул
поддержанный target_met; Stage 14 не расширяет его смысл. Отображение Stage 12
status фиксировано:

| Stage 12 status | Stage 14 status |
| --- | --- |
| target_met | target_met |
| toward_target | observed_toward_target |
| away_from_target | observed_away_from_target |
| unchanged | observed_no_clear_change |
| milestone_observations_available | insufficient_evidence |
| insufficient_observations | insufficient_evidence |
| definition_missing | not_evaluated |
| goal_source_changed | source_changed |
| not_comparable | not_comparable |

Precedence, сверху вниз:

1. invalid request — bounded safe error, result не создаётся;
2. source unavailable — bounded safe error;
3. exact Goal/Stage 12/experiment identity или fingerprint drift —
   source_changed;
4. policy mismatch, conflicting chain, duplicate active leaf или missing exact
   link — not_comparable;
5. valid cancellation — cancelled;
6. нет valid activation/terminal context или baseline — not_evaluated;
7. нет явно enrolled eligible observation — insufficient_evidence;
8. иначе вызвать Stage 12 evaluator и применить таблицу выше.

Результат содержит immutable result_fingerprint, contract_id, derivation_id,
policy fingerprints, exact Goal/definition/lifecycle/source references, as_of,
baseline provenance, exact stage12_status, included and excluded observation
references, bounded reasons и обязательные caveats:

    observed_change_is_not_proof_of_causation
    no_automatic_adaptation

Один observation, несколько observations, совпадение с hypothesis и target_met
не дают causal/probability/significance/confidence claim. Result не содержит
caused, proved, probability, significance, confidence, universal, winner,
optimal, recommendation или adaptation.

result_fingerprint считается от canonical result без generated-at, UI order,
request token и other ephemeral data. Одинаковые canonical inputs и explicit
as_of дают byte-identical result. Oversize result отклоняется без записи.

## 7. Safe Write boundary

Stage 14 reuse-ит существующий reviewed Safe Write boundary и не создаёт
обходной writer:

    prepare -> canonical plan -> exact plan SHA-256
            -> owner/session-bound one-time review token
            -> explicit apply {review_token, accepted_plan_sha256, confirmed: true}
            -> write only approved records
            -> full vault validation/rescan
            -> receipt-based rollback on failure

Правила:

- plan и token живут только process/request-local, имеют TTL, one-time consume
  и binding к owner session;
- apply принимает только exact token, exact plan hash и literal confirmed:
  true; stale/duplicate/other-session apply fail closed;
- writer делает no-overwrite, path-containment и symlink/junction checks,
  temp-file/atomic replace по существующему контракту, post-write full scan и
  rollback receipt;
- только Stage 14 dedicated serializer пишет exact marker/allowlist fields;
  generic metadata map, raw path, client id, client time и body semantics
  запрещены;
- activation/completion/cancellation, correction/supersession и
  reassessment становятся canonical только после explicit apply;
- при one-active conflict, source drift, post-write validation или rollback
  failure операция не маскируется частичным успехом;
- result evaluation, UI read, Stage 13 composition и provider/network не имеют
  write capability;
- implementation tests используют temporary fixtures и проверяют, что
  production vault и second-brain-vault не изменены.

Нет mutation исходного Goal, Stage 12 definition/observation, Growth, Growth
Advisor, Growth Learning, Decision Compass, plan или other user note.

## 8. Web/API и privacy

### 8.1. Additive owner-only routes

Предлагаемый bounded transport добавляется только в implementation gate 14.4:

    POST /api/personal-experiments
    POST /api/personal-experiments/definitions/prepare
    POST /api/personal-experiments/definitions/apply
    POST /api/personal-experiments/lifecycle/prepare
    POST /api/personal-experiments/lifecycle/apply
    POST /api/personal-experiments/observations/prepare
    POST /api/personal-experiments/observations/apply
    POST /api/personal-experiments/reassessments/prepare
    POST /api/personal-experiments/reassessments/apply
    POST /api/personal-experiments/evaluate

Все маршруты имеют strict POST, exact
X-Second-Brain-Request: personal-experiments-v1, strict
Content-Type: application/json; charset=utf-8, raw body limit 256 KiB,
Cache-Control: no-store и текущие security headers. Read route принимает
explicit as_of и optional exact experiment identity; evaluate принимает только
exact identity + explicit as_of.

Prepare requests содержат только bounded user input и exact source selectors:
Goal UUID, Stage 12 definition/observation UUID/fingerprint, definition text,
intervention, baseline strategy, lifecycle event/time, disposition/rationale.
Сервер вычисляет identity, policies, fingerprints, storage fields и plan.
Apply requests имеют ровно:

    {"review_token":"...","accepted_plan_sha256":"sha256:<64 hex>","confirmed":true}

Нет provider/Advisor, automatic creation, background polling, automatic
enrollment, automatic lifecycle, Growth Learning или adaptation route.

### 8.2. Безопасность и ошибки

Каждый маршрут reuse-ит текущие owner authentication, trusted Host, optional
same-origin Origin, duplicate-header rejection, exact-purpose middleware,
strict JSON/Pydantic (Strict*, extra="forbid"), bounded body and no-store
policy. CORS не становится permissive. Private payload не попадает в
localStorage, sessionStorage, IndexedDB, Cache API, service-worker runtime
cache, URL, analytics, exception text или console log.

Ответы не раскрывают raw body, path, trace, secret, provider detail, vault
layout или данные другой Goal. Стабильная vocabulary ошибок:

| Код | HTTP | Безопасное сообщение |
| --- | ---: | --- |
| PERSONAL_EXPERIMENT_REQUEST_INVALID | 400 | «Запрос эксперимента не прошёл проверку.» |
| PERSONAL_EXPERIMENT_GOAL_REQUIRED | 400 | «Нужно выбрать одну текущую цель.» |
| PERSONAL_EXPERIMENT_GOAL_SOURCE_CHANGED | 409 | «Источник цели изменился; требуется новая проверка.» |
| PERSONAL_EXPERIMENT_PROGRESS_DEFINITION_REQUIRED | 400 | «Нужно выбрать точное правило измерения прогресса.» |
| PERSONAL_EXPERIMENT_PROGRESS_SOURCE_CHANGED | 409 | «Правило измерения изменилось; требуется новая проверка.» |
| PERSONAL_EXPERIMENT_DEFINITION_INVALID | 400 | «Определение эксперимента не прошло проверку.» |
| PERSONAL_EXPERIMENT_DEFINITION_STALE | 409 | «Определение устарело; его нужно проверить заново.» |
| PERSONAL_EXPERIMENT_LIFECYCLE_INVALID | 400 | «Переход состояния эксперимента недоступен.» |
| PERSONAL_EXPERIMENT_LIFECYCLE_CONFLICT | 409 | «Состояние эксперимента изменилось; повторите проверку.» |
| PERSONAL_EXPERIMENT_ACTIVE_CONFLICT | 409 | «Для этой цели уже есть активный эксперимент.» |
| PERSONAL_EXPERIMENT_OBSERVATION_INVALID | 400 | «Наблюдение не прошло проверку.» |
| PERSONAL_EXPERIMENT_OBSERVATION_SOURCE_CHANGED | 409 | «Источник наблюдения изменился; требуется новая проверка.» |
| PERSONAL_EXPERIMENT_BASELINE_REQUIRED | 400 | «Нужно явно подтвердить исходную точку.» |
| PERSONAL_EXPERIMENT_RESULT_NOT_AVAILABLE | 409 | «Результат пока нельзя вычислить по доступным данным.» |
| PERSONAL_EXPERIMENT_RESULT_TOO_LARGE | 413 | «Результат эксперимента слишком велик.» |
| PERSONAL_EXPERIMENT_REASSESSMENT_INVALID | 400 | «Переоценка не прошла проверку.» |
| PERSONAL_EXPERIMENT_SAFE_WRITE_REVIEW_REQUIRED | 409 | «Сначала нужно просмотреть подготовленную запись.» |
| PERSONAL_EXPERIMENT_VAULT_CHANGED | 409 | «Данные хранилища изменились; подготовьте запись заново.» |
| PERSONAL_EXPERIMENT_SAFE_WRITE_VALIDATION_FAILED | 409 | «Проверка записи не пройдена; изменения отменены.» |
| PERSONAL_EXPERIMENT_ROLLBACK_FAILED | 503 | «Не удалось безопасно завершить запись; требуется проверка хранилища.» |
| PERSONAL_EXPERIMENT_SOURCE_UNAVAILABLE | 503 | «Источник данных временно недоступен.» |
| PERSONAL_EXPERIMENT_POLICY_MISMATCH | 409 | «Версия правил не совпадает; требуется новая проверка.» |
| PERSONAL_EXPERIMENT_INTERNAL | 503 | «Не удалось безопасно обработать эксперимент.» |

Unknown/private failures map to bounded PERSONAL_EXPERIMENT_INTERNAL without
logging sensitive payload. Anonymous, wrong-purpose, wrong-origin и
unauthenticated requests preserve the existing generic auth/security boundary;
они не раскрывают, существует ли эксперимент или Goal.

## 9. UI contract

UI — additive owner-only surface, отдельная от Decision Compass, Goal Progress
и Growth. Он показывает list/selection, а затем vertical flow с visibly
разделёнными зонами:

1. гипотеза;
2. вмешательство;
3. исходная точка;
4. точное правило измерения Stage 12;
5. явно выбранные наблюдения;
6. описательный результат;
7. переоценка.

Порядок действий виден пользователю: create exact Goal + Stage 12 binding,
baseline, review/apply, activate, enroll observations, evaluate, complete или
cancel, reassess. Активация, enrollment, terminal event и reassessment требуют
отдельного явного действия и подтверждения. Result всегда содержит заметное
русское предупреждение «Наблюдаемое изменение не доказывает, что его вызвало
вмешательство». Технические UUID/fingerprint/policy provenance показываются
progressive disclosure, а не загромождают основной экран.

Копирайт естественный русский: владелец, проверка, предпросмотр, провайдер и
точное совпадение используются вместо внутренних английских слов. Бренд
GitHub ID сохраняется только там, где он относится к GitHub. Ошибки, пустые
состояния, подсказки, placeholder и aria-label также русские.

Поверхность наследует существующий cosmic/glass визуальный язык, Onest,
текущие токены, keyboard navigation, видимый focus, target не менее 44px,
контраст, responsive widths 320/360/390/430/768/1024/1440/1920 и reduced
motion. Нет зелёного/mint accent и unrelated redesign. Disabled/loading/error
states не скрывают active boundary. Private state живёт только в памяти React
и очищается при закрытии/перезагрузке; browser persistence запрещён.

PWA продолжает кэшировать только hashed public shell/icons/manifest/offline
assets. /api/**, auth и private response не попадают в runtime cache. Browser
QA/mock-TMA не является physical-device proof.

## 10. Alternatives register

| Отклонённая альтернатива | Причина отказа | Принятая граница |
| --- | --- | --- |
| добавить поля в Goal | смешивает цель и эксперимент, ломает ownership | отдельный companion definition |
| сделать Outcome measurement | Outcome остаётся отдельным фактом, не Stage 12 authority | exact Stage 12 observation link |
| operational DB как canonical | vault перестанет быть источником истины | managed zettel через Safe Write |
| inferred/latest baseline | непроверяемая временная и смысловая подмена | explicit Stage 12 definition или reviewed pre-activation observation |
| automatic enrollment | скрытая selection bias и background write | explicit reviewed link |
| causal score/probability/confidence | неподдержанная причинная интерпретация | descriptive closed vocabulary |
| provider interpretation | новый privacy/secret/payload contract не принят | provider forbidden |
| browser persistence | private data переживает boundary и меняет authority | memory-only request/UI state |
| новый NoteType/global schema version | unnecessary schema migration | existing type: zettel + exact marker |
| auto lifecycle/adaptation | скрытая мутация и отсутствие owner review | explicit reviewed event/reassessment |

## 11. Карта реализации

| Gate | Разрешённый результат | Явно запрещено |
| --- | --- | --- |
| 14.0 | этот contract, static contract tests, roadmap status | любой runtime/API/UI/provider/network/write |
| 14.1 | frozen DTO, strict parsers/validators, exact chains, read projections | writer, Web/API, UI, provider, network |
| 14.2 | dedicated reviewed Safe Write, prepare/apply/review/rollback, lifecycle and supersession | direct bypass, Goal/plan/Growth/Compass mutation, auto writes |
| 14.3 | pure provider-free evaluator, immutable result/provenance, deterministic bounds | causal language, parallel Stage 12 math, writes/network |
| 14.4 | additive owner-only Web/API/UI, memory-only state, PWA/security/a11y | permissive CORS, private cache, auto creation/enrollment/adaptation |
| 14.5 | adversarial identity/isolation/lifecycle/Safe Write/privacy/semantic gate | unrelated refactor or redesign; fixes outside Stage 14 boundary |
| 14.6 | final ledger, exact-head full gates, PR close #304, post-merge CI, standard deploy, health/private smoke, cleanup | manual deploy, Vault Sync, Stage 15, Codex Review |

Каждый gate выполняется serially: focused docs/tests, required local gates, PR,
exact-head CI, merge, post-merge CI, standard automatic deploy,
non-mutating health/private boundary smoke, cleanup, fresh origin/main.
Новая фаза не начинается, пока предыдущая не имеет evidence всех этих шагов.

## 12. Acceptance flags for Stage 14

    causal_inference                 = NO
    provider_or_network              = NO
    raw_payload_or_body_persistence  = NO
    Decision_Compass_mutation        = NO
    Goal_Progress_mutation           = NO
    Growth_or_plan_mutation          = NO
    automatic_creation_or_enrollment = NO
    new_dependency                   = NO
    database_or_operational_store    = NO
    browser_private_storage          = NO
    new_global_schema_version        = NO
    new_NoteType                     = NO
    second-brain-vault_change        = NO
    production_env_change            = NO
    Stage14_contract                  = YES (14.0 design gate complete)
    Stage14_runtime                   = NO / NOT STARTED
    Stage15                         = NO / NOT STARTED
    HUMAN_REQUIRED                  = NO unless an external release gate genuinely fails

Последующие gates могут пометить Stage 14 runtime complete только с exact
evidence в финальном ledger. production_env_change сообщается по actual diff и
deploy contract, а не выводится из существующего значения environment
variable.
