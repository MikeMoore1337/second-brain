# Night Shift Development Orchestrator v1

Этот документ фиксирует bounded repository protocol для issue [#79](https://github.com/MikeMoore1337/second-brain/issues/79). Он не является автономным runtime, daemon, scheduler или хранилищем mutable состояния.

Машиночитаемая policy находится в [`config/night-shift-v1.yaml`](../../config/night-shift-v1.yaml), а deterministic parser и gate helpers — в [`src/second_brain/application/night_shift.py`](../../src/second_brain/application/night_shift.py). GitHub issue/PR state, comments, review threads, commit SHA и CI остаются operational source of truth. Policy описывает правила, но не хранит историю запусков.

## Explicit activation

`enabled_by_default: false` — обязательная граница. Night mode включён только отдельным явным overnight batch/thread automation. Обычная задача не получает права на commit, push, PR или merge из-за наличия этого файла.

Runner читает policy, затем проверяет текущие issue/PR через GitHub. Он не создаёт новый roadmap issue и не придумывает следующий stage: порядок выбора — текущая явно активная задача, затем заранее разрешённая queued-задача, затем roadmap candidate только если policy это отдельно разрешает.

## Task states и dependencies

Поддерживаются только следующие состояния:

```text
queued
in_progress
pr_open
fix_required
ci_wait
merge_ready
merged
human_required
blocked
```

Для dependent task отсутствие подтверждённого `merged` prerequisite даёт `blocked`. `blocked` или `human_required` у dependency останавливает зависимую цепочку с `HUMAN_REQUIRED`; независимая задача может продолжиться. GitHub issue `closed` сам по себе не заменяет проверку merged commit/current main, если task меняет код.

## Risk lanes

| Lane | Implementation | Merge |
| --- | --- | --- |
| `GREEN` | разрешена в утверждённом scope | возможен только при полном exact-SHA gate |
| `YELLOW` | разрешены implementation, tests, PR и review fixes | human merge required; dependent tasks ждут решения |
| `RED` | hard stop | `HUMAN_REQUIRED`, без самостоятельного решения |

RED включает canonical schema/version, `evidence_kind`, `self_kind`, migration, confidence/conflict/stale/supersede/preferences/values policy, Assistant/Simulate Me boundary, provider/public Web/auth/privacy, destructive operation, новую БД/очередь/постоянный компонент, architecture replacement, roadmap change и unresolved product decision.

Для RED нужен bounded decision memo:

```text
Question
Known facts
Options
Trade-offs
Recommendation
Affected tasks
```

## Failure budget

```text
max_tasks_per_night: 4
max_review_fix_cycles_per_task: 3
max_ci_fix_cycles_per_task: 3
max_scope_expansion: 0
```

Превышение любого лимита переводит task в `HUMAN_REQUIRED`. Flaky CI retry не считается code-changing fix cycle только при явном evidence и отсутствии изменения кода; повторение без evidence само становится human gate.

## Review и merge

Каждый PR должен содержать issue reference, exact base/head SHA, scope summary и checks. Reviewer независимо сверяет GitHub state и выдаёт verdict с exact reviewed head:

```text
NIGHT_SHIFT: FIX_REQUIRED
NIGHT_SHIFT: MERGE_READY
NIGHT_SHIFT: HUMAN_REQUIRED
```

`MERGE_READY` старого SHA нельзя применять к новому head. GREEN merge возможен только одновременно при:

- exact reviewed head равен current PR head;
- `quality` и `windows-ssl-regression` зелёные;
- unresolved review threads и accepted blockers отсутствуют;
- PR `CLEAN`/mergeable;
- scope unchanged;
- dependency и human gates отсутствуют;
- risk lane `GREEN`.

Метод merge — repository-approved squash. После merge нужно проверить новый `main` SHA и закрытие issue.

## Cutoff и audit trail

Текущая policy задаёт `08:00 Europe/Moscow`. На cutoff runner не начинает новую задачу, не оставляет half-written state и доводит только короткий безопасный checkpoint/commit/PR. Конкретный morning report не создаётся автоматически в repo; используется [versioned template](night-shift-morning-report-template.md).

Audit trail восстанавливается из GitHub: issue/PR URLs, commits, review comments/threads, CI runs и merge SHA. Секреты, prompts и private note content туда не записываются.

## Deliberate non-goals

В protocol нет LangGraph, CrewAI, LangChain, Redis, Kafka, Celery, DB, vector/graph DB, message queue, daemon, OpenAI API key, paid orchestration, generic multi-agent runtime или автоматической записи в `second-brain-vault`. Night Shift не меняет `schema_version`, canonical notes, privacy boundaries или product semantics.
