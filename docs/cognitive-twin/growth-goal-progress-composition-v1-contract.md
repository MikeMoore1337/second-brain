# Cognitive Twin v3 / Stage 12D — Growth + Goal Progress composition v1

Статус: **STAGE 12D COMPLETE**.

Issue: [#290](https://github.com/MikeMoore1337/second-brain/issues/290).
Implementation PR: [#291](https://github.com/MikeMoore1337/second-brain/pull/291),
merged as `d78da72fef9f080bedfbd3ad1c87d40e49d227e8`.
Post-merge CI: [run #34877206164](https://github.com/MikeMoore1337/second-brain/actions/runs/34877206164), PASS.
Production deploy: [run #34877425776](https://github.com/MikeMoore1337/second-brain/actions/runs/34877425776),
PASS for the same exact SHA. Non-mutating
[`GET /healthz`](https://brain.mikemoore.top/healthz): HTTP 200,
`{"status":"ok"}`. `env change required: no`.

Документ задаёт нормативный read-only composition contract; Stage 12E и Stage
13+ этим документом не запускаются.

## 1. Назначение

Stage 12D добавляет один application-level read model, который рядом
возвращает уже существующие результаты Growth Engine и Goal Progress. Это
композиция двух независимых веток для одного явно выбранного текущего Goal,
а не новый аналитический движок.

Композиция не вычисляет общий verdict, score, recommendation, effectiveness,
winner, causal relation или ranking. В ней нет HTTP/API/UI, CLI, форм,
Advisor/Learning integration, Safe Write, writer, provider/network-вызова,
новой persistence/schema/NoteType или dependency.

## 2. Нормативные источники

Composition обязан использовать существующие контракты без изменения их DTO,
hashing и semantics:

- `docs/cognitive-twin/growth-engine-v1-contract.md` и
  `second_brain.application.growth` — Growth branch;
- `docs/cognitive-twin/goal-progress-v1-contract.md` и
  `second_brain.application.goal_progress_read` — Goal Progress branch;
- `SelfModelPolicyV1`, текущий `VaultSnapshot` и read-only
  `GrowthMappingStore` — источник данных и policy authority.

Канонический vault `second-brain-vault` в Stage 12D не изменяется.

## 3. Request

Публичный application DTO имеет ровно два поля:

```json
{
  "goal_source_uuid": "<explicit current Goal UUID>",
  "progress_as_of": "<ISO-8601 timestamp>"
}
```

`goal_source_uuid` должен быть непустым UUID выбранного текущего Goal.
`progress_as_of` передаётся только в Goal Progress branch. Growth branch
использует свой `growth_clock`, поэтому временное выравнивание между ветками
не заявляется.

## 4. Алгоритм построения и drift guard

Builder обязан:

1. Провалидировать request, outer policy и policies обеих веток.
2. Запустить `BuildGrowthEngine` с explicit `SELECTED_GOAL` для точно
   указанного `goal_source_uuid`.
3. Убедиться, что все Growth rows привязаны к одному и тому же Goal UUID и
   одному `current_goal_identity_fingerprint`.
4. Запустить `BuildGoalProgress` с тем же Goal UUID и точным
   `progress_as_of`.
5. Отказаться от успешной композиции при `goal_source_changed` или любом
   несовпадении UUID, identity fingerprint и `progress_as_of`.
6. Повторно прочитать текущую Goal identity через существующий
   `BuildGrowthGoalContext` и сравнить её с обеими ветками. Изменение identity
   между чтениями является fail-closed `SOURCE_CHANGED`.
7. Провалидировать границы, binding и детерминированный результат до возврата.

Любая ошибка branch не превращается в частично успешный composition result.
Композиция не делает cross-branch выводов: наличие Growth relation не меняет
Goal Progress status, а Goal Progress status не меняет Growth relation/state.

## 5. Growth branch

Возвращается полный `GrowthEngineResultV1`, включая все cohorts/rows,
относящиеся к выбранному текущему Goal, в порядке и с fingerprint semantics
существующего Growth contract. Composition не фильтрует отдельные relation
states и не пересчитывает их.

## 6. Goal Progress branch

Возвращается полный `GoalProgressResultV1` существующего read-only builder с
его status, definition/observation evidence, identity fingerprint и
`progress_as_of`. `goal_source_changed` допустим как branch-level diagnostic,
но не может входить в успешный composition result.

## 7. Composition result

`GrowthGoalProgressCompositionResultV1` содержит только:

- `contract_version` = `growth_goal_progress_composition_v1`;
- `derivation_version` = `growth-goal-progress-composition-derivation-v1`;
- `policy_id`, `policy_fingerprint` композиции;
- выбранный Goal UUID и общий current Goal identity fingerprint;
- `progress_as_of`;
- fingerprints Growth и Goal Progress policies;
- полный `growth_result`;
- полный `goal_progress_result`;
- фиксированный список caveats;
- фиксированный provenance с `source=current_vault_and_growth_mapping_store`.

В output запрещены поля и синонимы, создающие новый cross-branch verdict:
`overall_status`, `alignment_score`, `effectiveness`, `recommendation`,
`winner`, `best_choice`, `goal_fit`, `percentage`, `caused_by`.

## 8. Ошибки

Ошибки композиции стабильны и fail-closed:

- `GROWTH_PROGRESS_COMPOSITION_INVALID_REQUEST`;
- `GROWTH_PROGRESS_COMPOSITION_GOAL_BINDING_MISMATCH`;
- `GROWTH_PROGRESS_COMPOSITION_SOURCE_CHANGED`;
- `GROWTH_PROGRESS_COMPOSITION_SOURCE_UNAVAILABLE`;
- `GROWTH_PROGRESS_COMPOSITION_POLICY_MISMATCH`;
- `GROWTH_PROGRESS_COMPOSITION_RESULT_TOO_LARGE`;
- `GROWTH_PROGRESS_COMPOSITION_INTERNAL`.

Сообщения не раскрывают секреты, пути vault, credentials или provider details.

## 9. Policy и границы

Канонический policy payload v1:

```json
{"advisor":"forbidden","binding":"exact-current-goal-identity-v1","bounds":"growth-max-plus-progress-max-plus-overhead-v1","causal_inference":"forbidden","contract":"growth_goal_progress_composition_v1","cross_branch_inference":"forbidden","growth_branch":"growth_engine_v1","learning":"forbidden","persistence":"none-v1","progress_branch":"goal_progress_v1","provider":"forbidden","selection":"explicit-selected-goal-only-v1","temporal":"separate-times-no-alignment-v1","version":1,"write":"forbidden"}
```

Его fingerprint: `sha256:30b7c0b7aea4e90c857402ee9890c20eeaf7ec6222dad3ebab07a01deca01e5d`.

Фиксированные caveats v1:

- `growth_and_progress_are_independent_layers`;
- `no_causal_claim`;
- `no_progress_inferred_from_growth`;
- `no_growth_relation_inferred_from_progress`;
- `temporal_alignment_not_proven`;
- `advisor_not_used`;
- `learning_not_used`.

## 10. Детерминизм и size bound

Fingerprint composition вычисляется по каноническому JSON request-independent
semantic payload: policy, Goal binding, fingerprints policies и полные
результаты обеих веток с `progress_as_of`. Provenance и фиксированный caveats
не являются скрытым storage state и не меняют semantic fingerprint.

Размер ограничен до сериализации:

```text
MAX_COMPOSITION_RESULT_BYTES
  = GROWTH_MAX_RESULT_BYTES + MAX_GOAL_PROGRESS_RESULT_BYTES + 8192
  = 131072 + 65536 + 8192
  = 204800
```

При превышении возвращается `RESULT_TOO_LARGE`; усечение результата запрещено.

## 11. Side-effect boundary

Успешное и неуспешное построение composition read model не пишет в vault,
mapping store, cache или persistence и не вызывает сеть, provider, Advisor,
Learning или writer. В тестах допускаются только временные fixture vault и
mapping stores, созданные тестовой инфраструктурой.

## 12. Verification и следующий scope

Минимальный verification slice должен покрывать поддерживающие,
конфликтующие и neutral Growth states в сочетании с toward/away/target,
unchanged, definition-missing и insufficient/milestone progress states;
несколько Growth cohorts, cross-goal isolation, source/identity drift,
детерминированный JSON/fingerprint, size bound и отсутствие forbidden
интеграций/side effects.

Stage 12E (Goal Progress экран, forms, HTTP/API/UI или иная следующая
интеграция) остаётся отдельным будущим scope и этим contract не начат.

## 13. Delivery closeout

Stage 12D implementation и production closeout завершены в PR #291 после
успешных required checks, post-merge CI и standard automatic production deploy.
Мердженный и deployed SHA совпадают: `d78da72fef9f080bedfbd3ad1c87d40e49d227e8`.
Канонический `second-brain-vault` не изменялся; production environment и
systemd contract не требовали изменений (`env change required: no`).
