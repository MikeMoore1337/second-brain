# Cognitive Twin v3 / Stage 15 — Adaptive Cognitive Twin v1

Статус: **NORMATIVE CONTRACT / PHASE 15.0**.

Issue: [#331](https://github.com/MikeMoore1337/second-brain/issues/331).
Этот документ является единственным нормативным контрактом Stage 15. Он
разрешает только implementation gates 15.1–15.6, перечисленные в разделе 24.
Он не разрешает изменение существующих политик Stage 1–14, запись в
`second-brain-vault`, provider/network, background monitoring, automatic
activation или новый глобальный schema/NoteType.

## 1. Назначение и граница

Adaptive Cognitive Twin v1 добавляет узкий owner-controlled слой, который
может пересобрать из exact reviewed evidence bounded adaptation candidate,
показать его владельцу, а после отдельного подтверждения активировать только
новый Stage15-owned adaptive profile. Полный поток:

```text
exact current Goal
  -> exact source snapshot Stage 9 / 10 / 12 / 14
  -> deterministic bounded candidate
  -> explicit owner review
  -> explicit activation of versioned profile
  -> Stage15-owned adaptive projection
  -> explicit later descriptive evaluation
  -> explicit exact supersede or revert
```
Профиль меняет только способ представления нового adaptive projection. Он не
меняет Goal, план, progress definition, calibration policy, Behavioral Self
Model, Growth, Decision Compass, Personal Experiments или любую другую
политику in place.

### 1.1. Non-goals и запрещённые механизмы

В v1 запрещены:

- hidden reinforcement learning, gradient/online training и auto-tuning;
- background watcher, cron, timer, polling или automatic candidate generation;
- automatic activation, revert, Goal/plan/policy mutation или experiment
  creation;
- provider, LLM, network, embeddings, vector state и provider payload;
- arbitrary code, expression language, prompt fragments и arbitrary JSON
  configuration;
- learned numeric weight vectors, universal personality/risk score,
  confidence, probability, reward, significance или causal effect;
- fuzzy, label, same-text, newest, timestamp-only или semantic matching;
- promotion browser state, telemetry или provider output в evidence;
- запись adaptive state в `second-brain-vault` или release directory.

Если exact source pack не доказывает безопасный delta, результатом является
`hold` или bounded source state, а не придуманный knob.

### 1.2. Зафиксированные owner decisions

Следующие решения уже приняты владельцем и не являются `HUMAN_REQUIRED`:

1. Stage15 v1 provider-free.
2. Vault остаётся canonical user truth; adaptation state — operational model
   state вне vault.
3. Нет новой canonical vault record family, `NoteType`, global
   `schema_version`, provider secret, dependency, DB или framework.
4. Все источники привязываются exact UUID/fingerprint; ambiguity означает
   fail closed.
5. Профиль закрытый, typed, bounded, explainable, deterministic и reversible.
6. Activation, reject, supersede, revert и evaluation — только явные действия
   владельца в foreground.
7. Evaluation описывает наблюдаемое состояние и не утверждает причинность.
8. Stage 1–14 сохраняют существующую семантику и публичные/private outputs.

## 2. Authority model и слои состояния

`second-brain-vault` является единственным canonical источником пользовательских
фактов. Stage 15 не создаёт новый canonical факт.

| Слой | Authority | Содержание | Запись |
| --- | --- | --- | --- |
| Current Goal | current vault scan + Stage 11 `GrowthGoalIdentityV1` | exact Goal UUID и identity fingerprint | существующий reviewed Goal flow |
| Stage 9 source | verified prospective audit store | exact calibration result/generation/policy snapshot | существующий Stage 9 operational flow |
| Stage 10 source | current Stage 10/Growth exact relation | exact cohort, behavior pattern и explicit mapping refs | существующие Stage 10/11 flows |
| Stage 12 source | `GoalProgressResultV1` | exact Goal Progress result/definition/progress state | существующий Stage 12 read/Safe Write flow |
| Stage 14 source | terminal evaluator result + reviewed reassessment | exact experiment/result/reassessment identity | существующий Stage 14 flow |
| Stage15 source snapshot | этот контракт | bounded immutable tuple всех exact refs | derived, memory-only до candidate |
| Candidate | этот контракт | deterministic proposed closed profile/delta | derived, не active и не canonical |
| Active profile | этот контракт | versioned operational model state | append-only store вне vault |
| Evaluation | этот контракт | later bounded descriptive comparison | derived; optional append-only event |
| Browser response | Web transport | transient owner projection | memory-only; никогда не authority |

Ни один слой Stage15 не становится Personal Memory, Decision Journal, Outcome,
Goal, Progress, Experiment, Behavioral evidence или policy input Stage 1–14.

## 3. Нормативные identities, версии и bounds

```text
contract_id              = adaptive-cognitive-twin-v1
contract_version         = 1
derivation_version       = adaptive-cognitive-twin-derivation-v1
candidate_policy_id      = adaptive-candidate-exact-source-pack-v1
profile_policy_id        = stage15-adaptive-profile-v1
evaluation_policy_id     = stage15-descriptive-evaluation-v1
store_format_version     = 1
```

Все UUID — lowercase UUIDv7. Все hashes —
`sha256:` плюс 64 lowercase hexadecimal символа. Весь canonical JSON — UTF-8,
`ensure_ascii=false`, `sort_keys=true`, separators `,` и `:`, без BOM,
trailing whitespace, float, `NaN`, `Infinity` и dynamic keys.

### 3.1. Policy payload

Единственный Stage15 policy payload v1:

```json
{"adaptation_catalog":"closed-stage15-owned-projection-v1","candidate":"exact-source-pack-deterministic-v1","causality":"descriptive-non-causal-v1","contract":"adaptive-cognitive-twin-v1","evaluation":"explicit-later-comparison-v1","goal":"growth-goal-identity-exact-v1","persistence":"append-only-operational-outside-vault-v1","provider":"forbidden","source_stage10":"growth-exact-behavioral-cohort-mapping-v1","source_stage12":"goal-progress-exact-result-v1","source_stage14":"terminal-experiment-reviewed-reassessment-v1","source_stage9":"prospective-calibration-exact-result-v1","version":1}
```

Ожидаемый `policy_fingerprint`:

```text
sha256:14d5e0844bbab502854307b0513fae5e8a9785c1b5ec051e5a0bb51427878920
```

Изменение payload, closed catalog, source binding, precedence, storage,
privacy или wording требует нового policy/derivation version и отдельного
owner-approved design gate.

### 3.2. Bounds

Implementation обязана использовать фиксированные bounds, а не принимать их
от browser:

```text
max source-pack bytes              = 128 KiB
max candidate bytes                = 64 KiB
max profile bytes                  = 16 KiB
max evaluation bytes               = 64 KiB
max operational records            = 4096 per generation
max active profiles                = 1 per exact Goal
max reasons/caveats                = 16 each
max source refs per family         = 1 exact current snapshot
max stored profile versions/Goal   = 128 active-retention records
```

Полный результат, который превышает bound, отклоняется; смысл не обрезается.

## 4. Exact Goal identity

Каждый candidate/profile/evaluation относится ровно к одной паре:

```text
goal_source_uuid
goal_identity_fingerprint = H(GrowthGoalIdentityV1.as_dict())
```

`GrowthGoalIdentityV1` остаётся Stage 11 authority и содержит exact source UUID,
dimension `goal`, допустимый source evidence/self kind, domain, evidence time,
Stage 4 contract/derivation, Self Model policy fingerprint, source fingerprint
и claim fingerprint.

Backend обязан заново построить current Goal по UUID и сравнить обе части
identity. Same text, same label, newest Goal, UUID similarity или timestamp не
являются fallback. Правила результата:

| Ситуация | Результат |
| --- | --- |
| один current Goal с тем же UUID и fingerprint | `exact_current` |
| UUID отсутствует после complete scan | `source_missing` / `insufficient` |
| UUID есть, fingerprint изменился | `source_changed` |
| несколько current identity для UUID | `not_comparable` |
| scan/integrity недоступен | `unavailable` |

## 5. Exact source snapshot DTO

Source snapshot immutable, provider-free и не содержит raw body, labels,
paths, titles, URLs, full evidence или UUID inventory.

```text
SourceReadinessV1 =
  exact_current | source_missing | unavailable | stale | source_changed |
  not_comparable | policy_mismatch

GoalSourceSnapshotV1 {
  readiness:                       SourceReadinessV1
  goal_source_uuid:                UUIDv7
  goal_identity_fingerprint:       HashV1 | null
  growth_policy_fingerprint:       HashV1 | null
  source_fingerprint:              HashV1 | null
}

Stage9CalibrationSnapshotV1 {
  readiness:                       SourceReadinessV1
  generation_id:                   UUIDv7 | null
  as_of:                           UTC datetime
  result_fingerprint:              HashV1 | null
  policy_id:                       "prospective-simulate-me-explicit-link-v1" | null
  policy_fingerprint:              HashV1 | null
  audited_operations:              int >= 0 | null
  linked_actual_decisions:         int >= 0 | null
  evaluated_predictions:           int >= 0 | null
}

Stage10BehavioralSnapshotV1 {
  readiness:                       SourceReadinessV1
  goal_source_uuid:                UUIDv7
  goal_identity_fingerprint:       HashV1
  growth_policy_fingerprint:       HashV1
  behavioral_policy_fingerprint:   HashV1 | null
  cohort_fingerprint:              HashV1 | null
  pattern_fingerprint:             HashV1 | null
  behavioral_source_fingerprint:   HashV1 | null
  behavioral_provenance_fingerprint: HashV1 | null
  pattern_type:                    closed Stage10 value | null
  pattern_state:                   closed Stage10 value | null
  mapping_id:                      UUIDv7 | null
  mapping_fingerprint:             HashV1 | null
  mapping_policy_fingerprint:      HashV1 | null
  relation_state:                  closed Growth relation value | null
}

Stage12ProgressSnapshotV1 {
  readiness:                       SourceReadinessV1
  goal_source_uuid:                UUIDv7
  goal_identity_fingerprint:       HashV1
  progress_result_fingerprint:     HashV1 | null
  progress_policy_fingerprint:     HashV1 | null
  progress_as_of:                  UTC datetime
  definition_id:                   UUIDv7 | null
  definition_fingerprint:          HashV1 | null
  status:                          closed GoalProgressStatusV1 | null
}

Stage14ExperimentSnapshotV1 {
  readiness:                       SourceReadinessV1
  goal_source_uuid:                UUIDv7
  goal_identity_fingerprint:       HashV1
  experiment_definition_id:        UUIDv7 | null
  experiment_definition_fingerprint: HashV1 | null
  terminal_result_fingerprint:     HashV1 | null
  terminal_result_as_of:           UTC datetime | null
  terminal_status:                 closed PersonalExperimentResultStatusV1 | null
  experiment_policy_fingerprint:   HashV1 | null
  reassessment_id:                 UUIDv7 | null
  reassessment_fingerprint:        HashV1 | null
  reassessment_evaluation_policy_fingerprint: HashV1 | null
  disposition:                     closed PersonalExperimentDispositionV1 | null
  reassessment_reviewed_at:        UTC datetime | null
}

AdaptiveSourceSnapshotV1 {
  contract_version:                "adaptive-cognitive-twin-v1"
  snapshot_version:                "1"
  goal:                            GoalSourceSnapshotV1
  stage9_calibration:               Stage9CalibrationSnapshotV1
  stage10_behavioral:               Stage10BehavioralSnapshotV1
  stage12_progress:                 Stage12ProgressSnapshotV1
  stage14_experiment:               Stage14ExperimentSnapshotV1
  as_of:                           explicit UTC datetime
  source_snapshot_fingerprint:     HashV1
}
```

The exact DTO may be represented by equivalent Python frozen dataclasses, но
поля, states, bounds и fingerprint payload неизменны. `source_snapshot_fingerprint`
хеширует canonical DTO без самого fingerprint и без raw source content.

### 5.1. Stage 9 binding

Stage 9 prospective calibration не имеет Goal semantics: его aggregate —
operational source общего owner history. Поэтому Stage15 сохраняет его exact
generation, explicit `as_of`, result fingerprint, policy identity и bounded
integer counts; никакая запись Stage9 не сопоставляется с Goal по тексту или
времени. Source готов только при verified Stage 9 store, current generation,
валидном `ProspectiveCalibrationResultV1` и exact Stage 9 policy. Никакие raw
events, query, labels, Journal bodies или UUID lists не копируются.

`audited_operations`, `linked_actual_decisions` и `evaluated_predictions`
остаются Stage 9 descriptive counts. Stage15 не превращает их в score,
confidence или quality claim.

### 5.2. Stage 10 binding

Stage10 source строится только через текущий exact Stage 10/Growth result для
того же `goal_source_uuid` и `goal_identity_fingerprint`. При наличии subject
фиксируются cohort, pattern, source/provenance и exact explicit mapping
fingerprints. Mapping state `supports_goal`, `conflicts_with_goal` и
`neutral_or_unknown` разрешён только если `mapping_id` и fingerprint exact;
missing/stale/conflicting mapping не превращается в relation.

`mixed_behavior`, `changed_behavior`, `behavioral_evidence_insufficient`,
`goal_mapping_missing`, `goal_source_missing` и `not_comparable` — bounded
non-binary states. Они не являются trait, failure или recommendation.

### 5.3. Stage 12 binding

Stage12 result читается как `GoalProgressResultV1` с explicit UTC `progress_as_of`.
Его Goal UUID и identity fingerprint обязаны совпасть с Goal snapshot, а
`goal_progress_policy_fingerprint` — с действующей Stage12 policy. Definition
и result fingerprint сохраняются как exact refs; numeric/milestone math,
baseline и observation authority не копируются и не переопределяются.

Допустимые состояния — только существующие `GoalProgressStatusV1`:
`target_met`, `toward_target`, `away_from_target`, `unchanged`,
`milestone_observations_available`, `insufficient_observations`,
`definition_missing`, `goal_source_changed`, `not_comparable`.

### 5.4. Stage 14 binding

Stage14 source обязан содержать один явно выбранный exact
`experiment_definition_id` и его fingerprint. Candidate не выбирает
«последний» эксперимент. Terminal result должен быть exact immutable
`PersonalExperimentEvaluationResultV1` с той же Goal identity, terminal
experiment identity, result fingerprint, explicit `as_of` и Stage14 policy.
Нужна также одна exact reviewed reassessment, привязанная к тому же result
fingerprint/evaluation cutoff и имеющая valid reassessment fingerprint.

При отсутствии experiment selector, terminal result, reassessment, exact
binding или при наличии нескольких equally eligible terminal/reassessment
records результат — `insufficient` или `not_comparable`; timestamp не выбирает
победителя. Stage14 disposition остаётся owner decision и не трактуется как
causal success.

## 6. Sufficiency gate

Gate выполняется до candidate derivation и повторяется непосредственно перед
activation. Он возвращает только один из закрытых overall states:

```text
candidate | hold | insufficient | not_comparable | source_changed |
policy_mismatch
```

Порядок проверки — часть policy:

1. invalid request/bounds — `insufficient` без scan beyond request validation;
2. unavailable/corrupt complete source — `insufficient` без partial result;
3. policy/contract/derivation mismatch — `policy_mismatch`;
4. duplicate, conflicting или ambiguous exact identity — `not_comparable`;
5. any source identity drift after build — `source_changed`;
6. missing Stage 9/10/12/14 required evidence — `insufficient`;
7. all exact sources ready, но closed rules не поддерживают safe delta —
   `hold`;
8. all exact sources ready и ровно один supported deterministic delta —
   `candidate`.

Нельзя скрыть конфликт средним значением, majority, nearest source,
timestamp или provider interpretation. `hold` — valid safe result, который
может явно сохранить current profile или `none`.

## 7. Closed Stage15 adaptation profile

Profile не является исполняемой policy. Это конечный набор enum-полей,
который влияет только на новый Stage15 projection:

```text
Stage15ProjectionFocusV1 =
  hold_current_profile
  progress_context
  behavioral_context
  tradeoff_context
  experiment_context
  calibration_context

Stage15InteractionModeV1 =
  balanced_evidence
  evidence_sequence
  explicit_tradeoff
  foreground_review

Stage15MeasureV1 =
  progress_state
  behavioral_state
  experiment_state
  calibration_linkage

Stage15AdaptiveProfileV1 {
  contract_version:                "adaptive-cognitive-twin-v1"
  profile_version:                 "1"
  profile_id:                      UUIDv7 store-owned
  goal_source_uuid:                UUIDv7
  goal_identity_fingerprint:       HashV1
  source_snapshot_fingerprint:     HashV1
  projection_focus:                Stage15ProjectionFocusV1
  interaction_mode:                Stage15InteractionModeV1
  evaluation_measure:              Stage15MeasureV1
  profile_policy_id:               "stage15-adaptive-profile-v1"
  profile_policy_fingerprint:      HashV1
  profile_fingerprint:              HashV1
}

Stage15ProfileProposalV1 {
  contract_version:                "adaptive-cognitive-twin-v1"
  profile_version:                 "1"
  goal_source_uuid:                UUIDv7
  goal_identity_fingerprint:       HashV1
  source_snapshot_fingerprint:     HashV1
  projection_focus:                Stage15ProjectionFocusV1
  interaction_mode:                Stage15InteractionModeV1
  evaluation_measure:              Stage15MeasureV1
  profile_policy_id:               "stage15-adaptive-profile-v1"
  profile_policy_fingerprint:      HashV1
  profile_fingerprint:              HashV1
}
```

No labels, prompt fragments, free text, numeric weight, risk/personality
attribute, provider setting или arbitrary extension field разрешены.

`hold_current_profile` с `balanced_evidence` и `progress_state` — canonical
safe profile для no-op/contradictory evidence. `projection_focus` определяет
только порядок и bounded explanatory card в Adaptive Cognitive Twin surface;
он не меняет вычисления Stage 6, 9, 10, 11, 12, 13 или 14.

## 8. Deterministic candidate derivation

Candidate immutable, rebuildable и содержит:

```text
AdaptationCandidateV1 {
  contract_version:                "adaptive-cognitive-twin-v1"
  candidate_version:               "1"
  candidate_status:                candidate | hold | insufficient |
                                    not_comparable | source_changed |
                                    policy_mismatch
  as_of:                           explicit UTC datetime
  goal_source_uuid:                UUIDv7
  goal_identity_fingerprint:       HashV1
  source_snapshot_fingerprint:     HashV1
  prior_profile_id:                UUIDv7 | null
  prior_profile_fingerprint:       HashV1 | null
  proposed_profile:                Stage15ProfileProposalV1 | null
  reasons:                         tuple[closed Stage15 reason, ...]
  evaluation_plan:                 Stage15EvaluationPlanV1
  caveats:                         tuple[closed Stage15 caveat, ...]
  policy_id:                       candidate policy id
  policy_fingerprint:              HashV1
  candidate_fingerprint:            HashV1
}
```

Reasons — закрытые values, минимум:

```text
stage9_calibration_available
stage9_calibration_not_evaluable
stage10_supports_goal
stage10_conflicts_with_goal
stage10_state_not_comparable
stage12_target_met
stage12_toward_target
stage12_away_from_target
stage12_state_not_comparable
stage14_terminal_review_available
stage14_result_not_comparable
stage14_owner_hold
sources_agree_on_focus
sources_conflict
no_safe_delta
missing_exact_source
source_drift
policy_mismatch
```

Evaluation plan и caveats также закрыты:

```text
Stage15EvaluationPlanV1 {
  later_source_required:             true
  explicit_as_of_required:           true
  measures:                          tuple[Stage15MeasureV1, ...]
  baseline_source_snapshot_fingerprint: HashV1
  non_causal_wording_id:             "observed-change-not-causation-v1"
}
```

### 8.1. Rule precedence

Все exact sources сначала валидируются независимо. Затем применяется
следующая закрытая таблица:

| Условие | Candidate |
| --- | --- |
| policy mismatch | `policy_mismatch`, no profile |
| missing/unavailable required source | `insufficient`, no profile |
| source identity drift/conflict | `source_changed` или `not_comparable`, no profile |
| Stage10 и Stage12/14 дают противоположные actionable signals | `hold`, `hold_current_profile`, reason `sources_conflict` |
| есть один actionable focus и нет противоположного signal | профиль с этим focus |
| все signals non-actionable или delta совпадает с prior | `hold`, prior profile unchanged |

Actionable signal mapping закрыт так:

```text
stage12 target_met/toward_target       -> progress_context
stage12 away_from_target               -> tradeoff_context
stage10 supports_goal                  -> behavioral_context
stage10 conflicts_with_goal            -> tradeoff_context
stage14 exact terminal + reviewed
  with owner disposition continue      -> experiment_context
stage14 exact terminal + reviewed
  with owner disposition hold/not_decided -> hold_current_profile
stage9 exact evaluable calibration     -> calibration_context
```

`unchanged`, `milestone_observations_available`, insufficient, mixed, changed,
neutral, stop и repeat без exact supported signal не являются автоматической
рекомендацией. При нескольких одинаковых actionable signals используется
фиксированный порядок выбора focus только для layout: Stage14 → Stage12 →
Stage10 → Stage9. Это не ranking evidence и не semantic winner; противоположные
signals всегда дают `hold`.

## 9. Fingerprints

Все fingerprints вычисляются на backend из exact canonical DTO. Клиентские
fingerprints не являются authority.

```text
source_snapshot_fingerprint = H(canonical AdaptiveSourceSnapshotV1 без hash)
profile_fingerprint         = H(canonical profile fields без profile_fingerprint)
candidate_fingerprint       = H(canonical candidate fields без candidate_fingerprint)
event_fingerprint           = H(canonical event без event_fingerprint и digest)
evaluation_fingerprint      = H(canonical evaluation без evaluation_fingerprint)
```

Fingerprint payload включает соответствующие policy/version, Goal identity,
source snapshot, prior profile identity, proposed closed fields, explicit
`as_of`, reasons, plan и caveats. Он не включает raw Goal/Journal/experiment
text, paths, secrets, browser token или process-specific dynamic fields.

Candidate с тем же exact input и `as_of` обязан иметь byte-equivalent profile,
reasons, plan, caveats и fingerprint. До explicit activation candidate содержит
только `Stage15ProfileProposalV1` без store-owned UUID; UUID active profile
генерируется только при explicit activation и не участвует в candidate identity.

## 10. Operational store и lifecycle

Stage15 operational state хранится вне vault и release directories. При
production deployment используется существующий explicit `web.env` и текущий
runtime root: логический Stage15 root —
`<env-file-parent>/prospective-audit/adaptive-cognitive-twin/`; production
mapping — `/srv/second-brain/runtime/prospective-audit/adaptive-cognitive-twin`.
Новый env key не вводится. Если root нельзя безопасно вывести из explicit
env-file, operation завершается fixed unavailable error.

Файлы v1:

```text
profiles.jsonl     # append-only candidate review/profile/lifecycle/evaluation events
manifest.json      # generation, next sequence, count, head digest, fingerprint
.store.lock        # OS-level exclusive lock
```

Record envelope:

```text
AdaptiveStoreEnvelopeV1 {
  generation_id:                  UUIDv7
  sequence:                       positive uint64, contiguous
  record_type:                    closed event type
  record:                         bounded typed event
  previous_record_digest:          HashV1 | null
  event_fingerprint:               HashV1
  record_digest:                   HashV1
}
```

Canonical record digest — SHA-256 envelope без `record_digest`. Manifest
обновляется атомарно после append, flush, fsync и read-back verification.
`record_count`, sequence, generation, policy и head digest проверяются вместе.

### 10.1. Append-only event types

```text
candidate_reviewed
candidate_rejected
profile_activated
profile_superseded
profile_reverted
evaluation_recorded
```

Candidate generation сама по себе не обязана попадать на disk. Review,
activation, reject, supersede, revert и evaluation сохраняют только bounded
identity/fingerprint fields, exact references и fixed lifecycle data; raw
source payload, labels, body, path и token не сохраняются.

### 10.2. Store integrity и recovery

Read перед любым result выполняет:

1. безопасное разрешение root без symlink/path escape;
2. directory/file type, permissions и ownership check там, где задана
   production convention;
3. strict JSON/schema/unknown-field validation;
4. contiguous sequence, generation и previous-digest verification;
5. event/profile/candidate fingerprint verification;
6. manifest/head/count cross-check.

Torn append, missing sequence, reordered history, digest mismatch, oversized
record, symlink escape, permission failure или lock uncertainty дают
`ADAPTIVE_COGNITIVE_TWIN_STORE_CORRUPT` / `..._STORE_UNAVAILABLE`. Частичные
records не фильтруются, история не переписывается, destructive auto-repair и
silent truncate запрещены. Bounded recovery — только повторное чтение
неизменённого verified generation либо owner-managed восстановление внешней
копии; Stage15 сам не удаляет и не исправляет неизвестные данные.

### 10.3. State machine

```text
candidate_generated (ephemeral)
  -> explicit_review
       -> candidate_rejected
       -> profile_activated
            -> profile_superseded (new reviewed candidate)
            -> profile_reverted (exact previous profile only)
```

`hold`, `insufficient`, `not_comparable`, `source_changed` и
`policy_mismatch` не активируются. Reject не создаёт active profile.

Для одного exact `(goal_source_uuid, goal_identity_fingerprint)` разрешён
ровно один active profile. Supersede допускается только для exact current
active profile и new candidate. Revert допускается только к exact
`previous_profile_id + previous_profile_fingerprint` из verified chain; выбор
`latest valid` или fuzzy fallback запрещён.

## 11. Review, activation, reject, supersede и revert

Каждая mutating operation получает client `operation_id` до запроса. В store
сохраняется только его fingerprint. Один и тот же operation fingerprint с
byte-equivalent intent возвращает прежний outcome; тот же operation ID с любым
отличием даёт `IDEMPOTENCY_CONFLICT`.

Перед activation backend под одним store lock:

1. перечитывает и валидирует current Goal/source pack;
2. сравнивает candidate fingerprint и policy fingerprint;
3. проверяет exact prior profile identity;
4. проверяет candidate не `hold`/source drift/policy mismatch;
5. проверяет one-active-profile invariant;
6. append-ит один typed event, flush/fsync/read-back и обновляет manifest.

Lost response восстанавливается exact idempotency lookup. Duplicate/concurrent
activation не создаёт второй profile. Cross-Goal candidate, stale candidate,
tampered candidate, stale prior fingerprint или replayed operation всегда
fail closed.

Revert не вычисляет новый candidate, не меняет Goal/plan и не удаляет history;
он append-ит bounded lifecycle event, который указывает exact previous profile.

## 12. Source drift и stale behavior

Candidate stale, если изменился любой source fingerprint, Goal identity,
Stage9 generation/result/policy, Stage10 cohort/pattern/mapping, Stage12
result/definition/policy, Stage14 terminal result/reassessment или Stage15
policy. Stale activation запрещена.

После activation новые source snapshots не изменяют active profile молча.
Projection показывает `source_changed`/`stale` и требует нового explicit
candidate, explicit review или exact revert. Active profile никогда не
ретаргетится на другой Goal, cohort, progress definition или experiment.

## 13. Adaptive projection

Новый Stage15-owned read path показывает bounded:

- exact Goal UUID и identity fingerprint в technical disclosure;
- source readiness каждой из четырёх семей;
- active profile или `none`, profile version/fingerprint и lifecycle state;
- candidate status, structured reasons, profile diff и caveats;
- exact provenance/fingerprints в progressive disclosure;
- stale/valid state;
- `projection_focus`, `interaction_mode` и `evaluation_measure` как
  ограниченную интерпретацию, не как recommendation.

Projection не вызывает provider, не пишет в vault/store и не меняет output
существующих Simulate Me, Calibration, Behavioral, Growth, Goal Progress,
Decision Compass или Personal Experiments surfaces.

## 14. Explicit evaluation

Evaluation — foreground owner action. Input:

```text
active_profile_id + active_profile_fingerprint
explicit later as_of/cutoff
exact later AdaptiveSourceSnapshotV1
activation baseline source_snapshot_fingerprint
evaluation_plan
```

Result:

```text
Stage15EvaluationStateV1 =
  evaluated | insufficient | not_comparable | source_changed |
  policy_mismatch

Stage15EvaluationResultV1 {
  contract_version:                "adaptive-cognitive-twin-v1"
  profile_id:                      UUIDv7
  profile_fingerprint:             HashV1
  goal_source_uuid:                UUIDv7
  goal_identity_fingerprint:       HashV1
  activation_snapshot_fingerprint: HashV1
  later_snapshot_fingerprint:      HashV1
  as_of:                           UTC datetime
  state:                           Stage15EvaluationStateV1
  changed_sources:                 tuple[closed source family, ...]
  unchanged_sources:               tuple[closed source family, ...]
  caveats:                         tuple[closed Stage15 caveat, ...]
  evaluation_fingerprint:          HashV1
}
```

Разрешённая фиксированная фраза:

> Наблюдаемое изменение в выбранном периоде не является доказательством того,
> что профиль вызвал это изменение.

Evaluation не содержит `success`, `failure`, `reward`, `fitness`, `confidence`,
`probability`, `significance`, causal effect, automatic revert или next
candidate. Если evidence changed during active period, это показывается как
source drift; causality не выводится.

## 15. Privacy, provider и browser-storage matrix

| Данные/действие | Допустимо | Запрещено |
| --- | --- | --- |
| Stage15 core | in-memory typed DTO | provider/network/LLM |
| operational profile | bounded append-only runtime store | vault, release directory, browser authority |
| Web response | authenticated owner transient projection | localStorage, sessionStorage, IndexedDB, Cache Storage |
| Service Worker | public shell/assets cache по текущей policy | private Stage15 API payload cache |
| logs/errors | fixed safe code/message | source body, labels, paths, UUID inventory, fingerprints beyond safe technical field, token, exception |
| smoke/CI | synthetic/read-only validation | real owner activation/revert, vault mutation |

No raw source pack, candidate, profile или evaluation payload попадает в
`console.log`, telemetry, exception text, cache или persistent browser state.

## 16. Owner-only Web/API contract

Все новые private routes additive и используют current owner-only boundary:

```text
authenticated owner session
trusted Host
same-origin Origin
strict POST method for private operations
exact X-Second-Brain-Request purpose
Content-Type: application/json (UTF-8)
bounded raw body and response
unknown JSON fields forbidden
Cache-Control: no-store
current CSP, Referrer-Policy, nosniff, frame protections
no permissive CORS
```

Нормативные route families:

```text
/api/adaptive-cognitive-twin/state
/api/adaptive-cognitive-twin/candidate
/api/adaptive-cognitive-twin/review
/api/adaptive-cognitive-twin/activate
/api/adaptive-cognitive-twin/reject
/api/adaptive-cognitive-twin/evaluate
/api/adaptive-cognitive-twin/supersede
/api/adaptive-cognitive-twin/revert
```

Exact transport payloads содержат только bounded selectors, explicit UTC
`as_of`, operation IDs, exact fingerprints и confirmation. Browser не может
передать raw source/Goal/profile как authority; backend всегда rebuilds and
revalidates.

Минимальный owner path:

```text
выбрать exact Goal и exact experiment source, если требуется
  -> увидеть readiness source pack
  -> Build adaptation candidate
  -> увидеть structured explanation и profile diff
  -> explicit Review
  -> Activate или Reject
  -> позже explicit Evaluate
  -> Supersede или Revert exact version
```

## 17. Safe Russian UI copy и ошибки

Основной UI использует естественные русские формулировки, не показывает
служебные номера этапа и не оставляет английские internal terms внутри русской
фразы. Допустимы только необходимые technical identifiers в progressive
disclosure.

Минимальный fixed error vocabulary:

| Code | Safe message |
| --- | --- |
| `ADAPTIVE_COGNITIVE_TWIN_INVALID_REQUEST` | `Запрос адаптивного слоя недействителен.` |
| `ADAPTIVE_COGNITIVE_TWIN_AUTH_REQUIRED` | `Требуется вход владельца.` |
| `ADAPTIVE_COGNITIVE_TWIN_SOURCE_UNAVAILABLE` | `Точный источник адаптивного слоя сейчас недоступен.` |
| `ADAPTIVE_COGNITIVE_TWIN_INSUFFICIENT_EVIDENCE` | `Недостаточно точных проверенных данных для предложения изменения.` |
| `ADAPTIVE_COGNITIVE_TWIN_NOT_COMPARABLE` | `Выбранные источники нельзя безопасно сопоставить.` |
| `ADAPTIVE_COGNITIVE_TWIN_SOURCE_CHANGED` | `Источник изменился; требуется новая проверка.` |
| `ADAPTIVE_COGNITIVE_TWIN_POLICY_MISMATCH` | `Версия политики адаптивного слоя недействительна.` |
| `ADAPTIVE_COGNITIVE_TWIN_STALE_CANDIDATE` | `Предложение устарело; сначала пересоберите его.` |
| `ADAPTIVE_COGNITIVE_TWIN_IDEMPOTENCY_CONFLICT` | `Операция конфликтует с уже обработанным запросом.` |
| `ADAPTIVE_COGNITIVE_TWIN_CONCURRENCY_CONFLICT` | `Состояние изменилось; требуется повторная проверка.` |
| `ADAPTIVE_COGNITIVE_TWIN_STORE_UNAVAILABLE` | `Операционное состояние адаптивного слоя недоступно.` |
| `ADAPTIVE_COGNITIVE_TWIN_STORE_CORRUPT` | `Операционное состояние адаптивного слоя не прошло проверку целостности.` |
| `ADAPTIVE_COGNITIVE_TWIN_RESULT_TOO_LARGE` | `Результат адаптивного слоя превышает допустимый размер.` |

Ошибки не содержат raw body, path, private value, source pack, другой Goal,
profile, traceback, secret или provider detail.

## 18. Security invariants

Обязательные adversarial invariants:

- same text/different UUID, stale Goal, wrong Stage9/10/12/14 ref и cross-Goal
  candidate/profile никогда не проходят;
- missing, stale, conflicting, reordered или tampered source даёт bounded
  fail-closed state;
- duplicate/replayed/concurrent activation не создаёт две active versions;
- one exact Goal имеет не более одного active profile;
- revert принимает только exact previous profile/version и сохраняет chain;
- lock охватывает reread, validation, sequence, append, fsync и read-back;
- torn append, digest/manifest/sequence mismatch и unsafe recovery не дают
  partial result;
- path traversal, symlink escape, unsafe permission/root и lock contention
  завершаются safe error;
- auth, Host, Origin, request purpose, method, JSON, content type, body limit,
  unknown fields, no-store и security headers обязательны;
- private browser/PWA storage, logs и provider calls отсутствуют;
- Stage 1–14, Safe Write, auth и vault repositories не изменяют semantics.

## 19. Fixed non-causal language

Разрешены только описания вида:

```text
active profile selects a bounded Stage15 projection focus
exact sources were available / missing / changed / not comparable
later source state differs from activation baseline
```

Запрещены любые варианты:

```text
profile caused improvement
profile worked/failed
the user is more disciplined/risky/personality type
this is the best plan or winning behavior
```

Stage14 result/reassessment и Stage9 calibration могут быть exact descriptive
inputs, но не доказывают causal effect и не разрешают automatic adaptation.

## 20. Concurrency, idempotency и retention

Сначала выполняется bounded request validation, затем backend reread. Store lock
один для каждого mutation stream. `operation_id` fingerprint связывает retry
с immutable exact payload; без исходного ID retry является новой operation.

Retention ограничен 4096 verified records per generation и 128 profile records
per Goal. Retention action — explicit owner operation или documented bounded
maintenance gate; generation history не переписывается. Physical purge не
может затронуть vault, other Goal или release files и не выполняется скрыто во
время read.

## 21. Alternatives register

| Вариант | Решение | Причина |
| --- | --- | --- |
| менять Stage1–14 policies in place | REJECT | нарушает семантическую изоляцию и обратимость |
| писать profile в vault | REJECT | operational state не является user evidence |
| новый global NoteType/schema | REJECT | нет canonical fact и migration необходимости |
| LLM/embedding/semantic matcher | REJECT | не даёт exact provenance и нарушает provider-free v1 |
| arbitrary JSON/weights/prompt config | REJECT | невозможно bounded/reviewable/fail-closed |
| background watcher/auto-tuning | REJECT | скрытая автономная adaptation запрещена |
| новый DB/framework | REJECT | append-only JSONL и текущий runtime store достаточны |
| own nested root under Stage9 runtime | ACCEPT | переиспользует explicit env/runtime/locking conventions без смешения authority |
| no-op/contradictory result | ACCEPT | `hold_current_profile` безопаснее слабого inference |
| one active profile per exact Goal | ACCEPT | минимальная reversible cardinality v1 |

## 22. Implementation map

### 15.1 — Exact source pack и candidate core

Добавить immutable DTOs, strict validators, exact loaders для Stage 9/10/12/14,
fingerprints, sufficiency gate и deterministic candidate builder. Только
in-memory, no store write, Web/API/UI, provider/network.

### 15.2 — Versioned operational profile lifecycle

Добавить store/integrity envelope, lock, manifest, append-only review/activate/
reject/supersede/revert, idempotency, one-active invariant и source revalidation.

### 15.3 — Adaptive projection и evaluation

Добавить новый read/projection path и explicit later descriptive evaluation.
Не менять существующие Stage1–14 surfaces.

### 15.4 — Owner-only Web/API/UI

Добавить private additive routes и mobile-first Russian UI с focus, labels,
keyboard access, non-color states, `prefers-reduced-motion`, no-store и
memory-only private payloads.

### 15.5 — Security/privacy/integration/E2E

Добавить adversarial coverage из раздела 18, provider/network zero-call gate,
browser/PWA storage/logging checks и Stage1–14 regression checks. Исправлять
только Stage15-boundary defects.

### 15.6 — Final closeout

Собрать factual ledger 15.0–15.6, выполнить full Python 3.14/frontend/
exact-head CI gate, update only factual status/evidence, merge final PR с
`Closes #331`, дождаться post-merge CI и standard automatic deploy, проверить
`/healthz` и anonymous private boundary без mutation, cleanup и зафиксировать
production SHA. После этого Stage15/Cognitive Twin v3 complete; Stage16+ не
начинать.

## 23. Acceptance checklist

- [ ] exact Goal UUID + GrowthGoalIdentity fingerprint required;
- [ ] exact Stage9 generation/result/policy binding;
- [ ] exact Stage10 cohort/pattern/mapping binding;
- [ ] exact Stage12 result/definition/policy binding;
- [ ] exact Stage14 terminal result + reviewed reassessment binding;
- [ ] missing/stale/conflicting source fails closed;
- [ ] closed typed Stage15 profile only;
- [ ] deterministic candidate and exact fingerprints;
- [ ] candidate never activates on generation/refresh/deploy/login/timer;
- [ ] append-only operational lifecycle, lock, fsync, chain and recovery guard;
- [ ] one active profile per exact Goal and exact revert;
- [ ] adaptive output is Stage15-owned only;
- [ ] explicit descriptive non-causal evaluation;
- [ ] owner-only Web/API/UI, Russian safe copy, no private browser storage;
- [ ] zero provider/network calls and no vault mutation;
- [ ] Stage1–14 semantic regressions remain green;
- [ ] standard production deploy and exact closeout evidence.

## 24. Delivery status boundary

```text
Stage 15.0 normative contract = this document
Stage 15.1–15.6 runtime = NOT STARTED at contract merge
Stage 15 = IN PROGRESS after this contract gate
Cognitive Twin v3 = IN PROGRESS
Stage 16+ = FUTURE / NOT STARTED
HUMAN_REQUIRED = NO
provider/network introduced = NO
new dependency/DB/framework = NO
new canonical vault family/schema/NoteType = NO
env change required = no
```
