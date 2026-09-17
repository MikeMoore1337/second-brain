# Second Brain v4 — операционная проверка и burn-in

## 1. Назначение

Этот документ задаёт минимальный контракт фактической проверки уже завершённого
Second Brain v4 в реальном использовании. Он собирает bounded evidence из
существующих операционных хранилищ Stage16–20 и помогает владельцу позже
ответить на вопросы Issue #386. Отчёт фиксирует только наблюдаемые факты; он не
делает выводов о качестве, продуктивности, мотивации или направлении следующей
версии.

Точка CLI:

```text
second-brain validation v4-status --env-file PATH --format text
second-brain validation v4-status --env-file PATH --format json
```

`--json` является короткой формой для `--format json`. `--env-file` выбирает
родительский каталог операционных хранилищ. Команда не читает vault для
построения отчёта и не требует новых переменных окружения.

## 2. Не входит в scope

Операционная проверка не является Stage21, Second Brain v5 или новой генерацией
продукта. Она не меняет Stage16–20, не добавляет интеграции, provider, модель,
секрет, connector, database, framework, NoteType, vault-файл или primary
навигацию Web.

Команда не принимает продуктовых решений, не выбирает между Calendar и Email,
не создаёт roadmap v5 и не закрывает Issue #386.

## 3. Измерения evidence

Отчёт разделяет следующие измерения:

- durable operational facts — принятые снимки, lifecycle-события, receipts и
  точные fingerprints;
- exact provenance — связи, доказанные UUID/ID и fingerprint, которые уже
  записаны текущими контрактами;
- natural demand — факт реального действия, а не требуемая квота;
- owner qualitative evidence — наблюдения владельца в реальной работе;
- unsupported evidence — вопрос, на который текущие durable данные ответить не
  могут.

Ни одно измерение не сворачивается в единый score.

## 4. Матрица источников

| Поверхность | Источник | Что можно агрегировать | Чего нет в источнике |
| --- | --- | --- | --- |
| Stage16 Strategy | `executive-strategy/snapshots.jsonl` и `manifest.json` | принятые снимки, exact Goal bindings, время принятия, supersession, current state | provider Proposal, generation/review cycle, Strategy text, Goal body, constraints и context |
| Stage17 Planning | `personal-planning/plans.jsonl` и `manifest.json` | принятые версии плана, exact Goal refs, выбранные executable items, revisions, время | planning body, item text, provider proposal, private constraints |
| Stage18 Execution & Feedback | `execution-feedback/events.jsonl` и `manifest.json` | число событий, exact executable item bindings, lifecycle kinds, effort precision, terminal counts, время | owner notes и выводы о результате/продуктивности |
| Stage19 Action Gateway | `action-gateway/receipts.jsonl` и `manifest.json` | число receipts, kinds, action kinds, states, reconciliation/compensation, время | outgoing title/body/comment, operation plaintext, raw response, credential и Authorization |
| Stage20 Personal Agent | `personal-agent/runs.jsonl` и `manifest.json` | Runs/Missions, lifecycle event/state, closed step kinds, receipt refs, время | Mission text, answers, step text, provider reasoning и action payload |

Все корни выводятся только из явно переданного существующего `--env-file` по
соглашению `<env-file-parent>/prospective-audit/<store-directory>`. Если env-файл
не передан, пути не угадываются.

## 5. Граница приватности

Human output и JSON могут содержать только counts, закрытые категории,
timestamps, статус evidence и exact identity relations, необходимые для
проверки. Они не содержат raw Goal text, Strategy/Planning/Run text, Stage18
notes, GitHub body/comment, clarification answer, provider prompt/output,
credentials, cookies, environment values, absolute paths, exception text или
traceback.

Отчёт не сохраняется в vault, не добавляет telemetry и не логирует полные
операционные записи. Безопасные идентификаторы используются только внутри
агрегации; наружу выдаются counts, а не UUID/fingerprint списки.

## 6. Наблюдаемое и ненаблюдаемое

`StrategySnapshotV1`, `PlanningPlanV1`, `ExecutionEventV1`,
`ActionReceiptV1` и `AgentRunSnapshotV1` являются durable источниками. Их
verified JSONL chain и manifest проверяются до агрегации.

Stage16 Proposal намеренно эфемерен. Поэтому generation/review cycles нельзя
восстановить из текущего operational store. Отчёт обязан показывать
`unsupported_by_current_operational_data` и не меняет Stage16, чтобы
дозаписывать provider payload или Proposal только ради burn-in.

Если хранилище отсутствует, dimension имеет `not_observed`. Если root уже есть,
но chain/manifest не проходит проверку, dimension имеет
`insufficient_evidence`; безопасная ошибка не раскрывает причину, путь или
данные записи.

## 7. Семантика агрегации

Агрегация выполняется только по validated records:

- Stage16 считает принятые снимки, exact `(goal_source_uuid,
  goal_identity_fingerprint)`, `accepted_at`, наличие `prior_snapshot_id` и
  current snapshots. Статус свежести Goal не угадывается без текущего source
  read.
- Stage17 считает историю принятых plan revisions, exact Goal refs и
  выбранные элементы kind `commitment`/`next_action`. Исторические revisions не
  выдаются за новые Goals.
- Stage18 считает все durable events, включая корректирующий `void`, по
  закрытым `ExecutionEventTypeV1`; actual effort считается известным только
  при `ExecutionEffortPrecisionV1.EXACT` на terminal event.
- Stage19 считает receipts по `receipt_kind`, `action_kind` и `state`.
  `reconciliation_count` и `compensation_count` — это соответствующие closed
  receipt kinds. При нуле receipts отчёт показывает ровно `0`.
- Stage20 считает distinct Run histories, accepted/started/completed/abandoned,
  pause/resume, revisions/supersession, closed step kinds и latest states.
  Повторяющиеся snapshots одной истории не дублируют Mission step counts.

Timestamps — это только нормализованные UTC timestamps. Никакой агрегацией не
выводятся productivity score, digital-self score, motivation, discipline,
quality или causal claim.

## 8. Coverage targets

Targets являются ориентирами burn-in, а не квотами:

| Dimension | Target | Правило |
| --- | ---: | --- |
| Stage16 generation/review cycles | `>=5` | текущий store не поддерживает подсчёт; статус `unsupported_by_current_operational_data` |
| Stage17 accepted planning snapshots | `>=3` | durable acceptance считается, но факт использования в реальной работе подтверждает владелец |
| Stage18 lifecycle/feedback events | `>=10` | считаются только реальные записи в Stage18 store |
| Stage18 distinct executable items | `>=3` | exact item identity, не похожий текст |
| Stage20 Missions/Runs | `>=3` | считаются реальные accepted Run histories |
| clarify/checkpoint | natural demand | не создаются для достижения числа |
| Stage19 action | natural demand | отсутствие действия — корректный результат |

Недостаток фактов никогда не компенсируется synthetic traffic.

## 9. Vocabulary статусов

Для dimensions используется закрытый vocabulary:

```text
observed
not_observed
insufficient_evidence
not_applicable
unsupported_by_current_operational_data
```

`burn_in_decision_status` имеет только contract-equivalent значения:

```text
observation_in_progress
insufficient_evidence
ready_for_owner_review
```

В первой реализации команда возвращает `observation_in_progress`: она не знает
дату production deployment, длительность окна и факт owner review. Она не может
автоматически вернуть `GO_V5`, `START_STAGE21`, `ADD_CALENDAR` или `ADD_EMAIL`.

## 10. Запрет synthetic traffic

Production evidence происходит только из реальной работы владельца. Нельзя
создавать fake Strategy acceptance, Planning acceptance, Stage18 event, Mission,
Run, GitHub action или friction event ради targets. Fixtures и doubles допустимы
только в тестах.

Для Stage19 при нулевом естественном спросе используется точная формулировка:

```text
no natural Stage19 action demand observed
```

Это не означает, что capability плоха или нужна новая интеграция.

## 11. Реальное окно burn-in

Burn-in window начинается не временем запуска CLI и не датой создания PR, а
успешным production deployment этого framework. Timestamp deployment должен
быть записан фактически после deploy. Earliest owner-review date — не ранее чем
через 14 календарных дней от этого timestamp.

До deployment нельзя hardcode предполагаемую дату старта. Issue #386 остаётся
открытым на всём окне.

## 12. Качественные наблюдения владельца

Основной durable coordination channel — комментарии Issue #386. Они не являются
canonical Personal Memory и не добавляют product-wide telemetry, click tracking,
analytics SDK, внешнюю службу, новую БД или feedback UI.

Допустимые closed friction categories:

```text
repeated_input
too_many_steps
too_much_text
unclear_label
hard_to_find
missing_context
wrong_or_stale_context
irrelevant_suggestion
missing_capability
unnecessary_confirmation
useful_confirmation
slow_or_unresponsive
visual_ui_issue
other
```

Комментарий должен описывать наблюдаемый пример и затронутую поверхность;
агент не превращает его в психологическую оценку или score.

## 13. Схема отчёта

JSON верхнего уровня имеет bounded shape:

```text
v4_validation_contract
generated_at
stage16
stage17
stage18
stage19
stage20
cross_stage
coverage_targets
unresolved_evidence_gaps
manual_evidence_required
burn_in_decision_status
```

Stage16–20 содержат `status`, `store_availability` и только безопасные
aggregates. `cross_stage` содержит relation status и counts exact matches.
`coverage_targets` хранит literal target/observed fields, но не процент и не
quality score. `generated_at` может изменяться между запусками; остальные поля
детерминированы неизменившимися stores.

Human output — краткая сводка тех же безопасных counts и statuses. Он не
печатает JSONL record, root path, private text или exception details.

## 14. Cross-stage provenance

Связь считается `observed` только если текущие контракты дают exact proof:

- Stage16 → Stage17: `strategy_snapshot_id`, snapshot fingerprint и exact
  reviewed action binding;
- Stage17 → Stage18: `planning_snapshot_id`, plan fingerprint, revision,
  source/proposal fingerprints и exact item fingerprint;
- Stage17/18 → Stage20: exact plan binding и exact Stage18 item bindings;
- Stage20 → Stage19: receipt reference fields совпадают с verified receipt.

Запрещены joins по одинаковому тексту, похожему title, latest item, времени,
fuzzy match или semantic similarity. При отсутствии exact binding отчёт
показывает `insufficient_evidence`, а не придумывает chain.

## 15. Gate будущего решения

Этот framework только проецирует evidence. Будущий owner review может
зафиксировать factual report после окна и решить, упрощать ли v4 или менять
направление. Никакое решение не принимается автоматически; framework не
создаёт Stage21/v5 tasks и не рекомендует Calendar/Email.

Issue #386 закрывается только после framework deployment, минимум 14 календарных
дней реального использования, достаточного cross-stage evidence либо явной
фиксации insufficiency, owner review и явного owner decision о следующем
направлении.

## 16. Жизненный цикл Issue #386

PR framework использует `Refs #386`, а не `Closes #386`. Issue остаётся `OPEN`.
Ожидаемая последовательность после реализации:

```text
fresh origin/main
-> isolated worktree
-> implementation/tests/gates
-> PR with Refs #386
-> exact-head CI
-> merge/post-merge CI
-> standard deploy
-> non-mutating smoke
-> factual burn-in start timestamp
-> cleanup
-> stop
```

Доставка framework не меняет status Second Brain v4: `Second Brain v4 =
COMPLETE`, `Stage16–20 = COMPLETE`. В конце первой поставки:

```text
Operational Validation / Burn-in = ACTIVE
Issue #386 = OPEN
Stage21 = NOT DEFINED / NOT STARTED
Second Brain v5 = NOT STARTED
```
