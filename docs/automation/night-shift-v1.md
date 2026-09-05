# Оркестратор разработки Night Shift v1

Этот документ фиксирует ограниченный протокол репозитория для issue [#79](https://github.com/MikeMoore1337/second-brain/issues/79). Он не является автономным runtime, daemon, scheduler или хранилищем изменяемого состояния.

Машиночитаемая policy находится в [`config/night-shift-v1.yaml`](../../config/night-shift-v1.yaml), а детерминированный parser и gate helpers — в [`src/second_brain/application/night_shift.py`](../../src/second_brain/application/night_shift.py). Состояния GitHub issue/PR, комментарии, review threads, commit SHA и CI остаются операционным источником истины. Policy описывает правила, но не хранит историю запусков.

## Явная активация

`enabled_by_default: false` — обязательная граница. Night mode включён только отдельной явной ночной batch/thread automation. Обычная задача не получает права на commit, push, PR или merge из-за наличия этого файла.

Исполнитель читает policy, затем проверяет текущие issue/PR через GitHub. Он не создаёт новую задачу roadmap и не придумывает следующий этап: порядок выбора — текущая явно активная задача, затем заранее разрешённая queued-задача, затем кандидат из roadmap только если policy это отдельно разрешает.

## Состояния задач и зависимости

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

Для зависимой задачи отсутствие подтверждённой предпосылки со статусом `merged` даёт `blocked`. `blocked` или `human_required` у dependency останавливает зависимую цепочку с `HUMAN_REQUIRED`; независимая задача может продолжиться. GitHub issue `closed` сам по себе не заменяет проверку merged commit/current main, если задача меняет код.

## Уровни риска

| Уровень | Реализация | Слияние |
| --- | --- | --- |
| `GREEN` | разрешена в утверждённом scope | возможно только при полном exact-SHA gate |
| `YELLOW` | разрешены реализация, tests, PR и исправления review | требуется слияние человеком; зависимые задачи ждут решения |
| `RED` | жёсткая остановка | `HUMAN_REQUIRED`, без самостоятельного решения |

RED включает canonical schema/version, `evidence_kind`, `self_kind`, migration, confidence/conflict/stale/supersede/preferences/values policy, границу Assistant/Simulate Me, provider/public Web/auth/privacy, destructive operation, новую БД/очередь/постоянный компонент, замену архитектуры, изменение roadmap и нерешённое продуктовое решение.

Для RED нужен ограниченный decision memo:

```text
Вопрос
Известные факты
Варианты
Компромиссы
Рекомендация
Затронутые задачи
```

## Failure budget

```text
max_tasks_per_night: 4
max_review_fix_cycles_per_task: 3
max_ci_fix_cycles_per_task: 3
max_scope_expansion: 0
```

Превышение любого лимита переводит задачу в `HUMAN_REQUIRED`. Повтор flaky CI не считается циклом исправления с изменением кода только при явном evidence и отсутствии изменения кода; повторение без evidence само становится human gate.

## Проверка и слияние

Каждый PR должен содержать ссылку на issue, exact base/head SHA, краткое описание scope и checks. Reviewer независимо сверяет состояние GitHub и выдаёт verdict с exact reviewed head:

```text
NIGHT_SHIFT: FIX_REQUIRED
NIGHT_SHIFT: MERGE_READY
NIGHT_SHIFT: HUMAN_REQUIRED
```

`MERGE_READY` старого SHA нельзя применять к новому head. GREEN merge возможен только одновременно при выполнении всех условий:

- exact reviewed head совпадает с current PR head;
- проверки `quality` и `windows-ssl-regression` завершились успешно;
- нет нерешённых review threads и accepted blockers;
- PR имеет состояние `CLEAN`/mergeable;
- scope не изменился;
- dependency и human gates отсутствуют;
- risk lane равен `GREEN`.

Метод merge — одобренный репозиторием squash. После merge нужно проверить новый SHA ветки `main` и закрытие issue.

## Отсечка и аудит

Текущая policy задаёт `08:00 Europe/Moscow`. На cutoff исполнитель не начинает новую задачу, не оставляет частично записанное состояние и доводит только короткий безопасный checkpoint/commit/PR. Утренний отчёт не создаётся автоматически в репозитории; используется [версионируемый шаблон](night-shift-morning-report-template.md).

Аудит восстанавливается из GitHub: URL issue/PR, commits, review comments/threads, CI runs и merge SHA. Секреты, prompts и содержимое private notes туда не записываются.

## Осознанно не поддерживается

В протоколе нет LangGraph, CrewAI, LangChain, Redis, Kafka, Celery, DB, vector/graph DB, message queue, daemon, OpenAI API key, paid orchestration, generic multi-agent runtime или автоматической записи в `second-brain-vault`. Night Shift не меняет `schema_version`, canonical notes, privacy boundaries или product semantics.
