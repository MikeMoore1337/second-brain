# Оркестратор разработки Night Shift v1

Этот документ фиксирует ограниченный протокол репозитория для issue [#79](https://github.com/MikeMoore1337/second-brain/issues/79). Он не является автономным runtime, daemon, scheduler или хранилищем изменяемого состояния.

Машиночитаемая policy находится в [`config/night-shift-v1.yaml`](../../config/night-shift-v1.yaml), а детерминированный parser и gate helpers — в [`src/second_brain/application/night_shift.py`](../../src/second_brain/application/night_shift.py). Состояния GitHub issue/PR, комментарии, существующие review threads, commit SHA и CI остаются операционным источником истины. Policy описывает правила, но не хранит историю запусков.

## Постоянная политика качества

По owner-level решению Codex Code Review отключён и не используется как gate
релиза: он расходует Codex usage. Каноническая формулировка:

```text
Codex Code Review is disabled and must not be used as a release gate because it consumes Codex usage. Quality gates are deterministic CI/testing/static-analysis checks plus task-specific human/external gates where explicitly required.
```

Внутри текущей рабочей сессии implementer выполняет ограниченный self-review
перед commit. Это не отдельная review-задача и не отдельный агент. Для PR
проверяются только уже существующие GitHub review threads: если такие threads
есть, их findings должны быть фактически исправлены или resolved; новый LLM
review для этого не запускается.

Внешняя настройка Codex Cloud/GitHub, если она автоматически запускает
reviews, не хранится в этом репозитории. Это единственное действие вне repo:
owner должен отключить её вручную, если она включена.

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
| `YELLOW` | разрешены реализация, tests, PR и исправления CI/QA findings | требуется слияние человеком; зависимые задачи ждут решения |
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

## Бюджет сбоев

```text
max_tasks_per_night: 4
max_ci_fix_cycles_per_task: 3
max_scope_expansion: 0
```

Превышение любого лимита переводит задачу в `HUMAN_REQUIRED`. Повтор flaky CI не считается циклом исправления с изменением кода только при явном evidence и отсутствии изменения кода; повторение без evidence само становится human gate.

## Проверка, слияние и release gate

Каждый PR должен содержать ссылку на issue, exact base/head SHA, краткое
описание scope и checks. Последовательность unattended/autopilot workflow:

```text
implementation
  -> targeted verification
  -> self-review
  -> commit/push
  -> exact-head CI
  -> PR
  -> required GitHub checks
  -> merge
  -> deploy
  -> production smoke/closeout
```

Шаги после commit выполняются только при отсутствии явного
owner/human/external/destructive gate. Между CI и merge нет отдельного LLM
review stage или verdict dependency.

GREEN merge возможен только одновременно при выполнении всех условий:

- current PR head и base — полные SHA, а все evidence привязаны к current head и
  current base `main`;
- `quality` и `windows-ssl-regression` завершились успешно;
- обязательный aggregate GitHub status `checks` завершился успешно;
- нет нерешённых существующих GitHub review threads и известных unresolved
  BLOCKER/HIGH из реализации или QA;
- PR имеет состояние `CLEAN`/mergeable;
- scope не изменился;
- dependency и human gates отсутствуют;
- risk lane равен `GREEN`.

`quality` включает relevant targeted tests, lint/format/typecheck и применимые
integration/e2e checks. Отдельный review verdict не нужен. После merge для
production-facing задачи обязательны штатные deploy, smoke и closeout.

Метод merge — одобренный репозиторием squash. После merge нужно проверить новый SHA ветки `main` и закрытие issue.

## Post-task cleanup worktree

После каждого проверенного `GREEN` merge исполнитель обязан выполнить bounded
post-task lifecycle до запуска следующей задачи:

1. подтвердить `PR merged`;
2. подтвердить `issue closed/completed`;
3. получить новый exact SHA локального `main`;
4. прочитать только зарегистрированные worktree через `git worktree list --porcelain`;
5. в каждом кандидате проверить `git status --porcelain --ignored
   --untracked-files=all`; ignored entries также считаются dirty;
6. передать уже проверенное состояние в
   [`scripts/worktree_cleanup.py`](../../scripts/worktree_cleanup.py).

Helper принимает JSON receipt от orchestrator и не делает GitHub-запросов.
Удаление возможно только для clean, registered, non-primary, non-vault
worktree с `task_state=merged`, закрытым issue, merged PR, отсутствующим OPEN
PR для branch, доказанным `merged_sha`, отсутствующим active-use/CWD и
совпадающим branch mapping. Detached, unknown, locked, dirty, deferred,
`HUMAN_REQUIRED`, `BLOCKED`, open или unmerged worktree сохраняются.

Обычный вызов после verified merge:

```text
uv run python scripts/worktree_cleanup.py \
  --repo <primary-second-brain> \
  --manifest <verified-green-merge-receipt.json> \
  --format json
```

Для cohort/orphan pass используется тот же helper с
`lifecycle=historical_orphan_pass`; GitHub state по-прежнему разрешает
orchestrator. `--dry-run`/`--list` только классифицирует кандидатов. При
успешных удалениях helper сначала выполняет bounded preflight
`git worktree prune --dry-run --verbose`. Если preflight предлагает любую
непроверенную/чужую регистрацию, глобальный prune не запускается и возвращается
`cleanup_deferred`; чужая регистрация сохраняется. Только пустой preflight
разрешает один обычный `git worktree prune`. Helper никогда не использует
`--force`, raw filesystem deletion, remote/local branch deletion или очистку по
имени соседней папки. Ошибка cleanup даёт
`cleanup_deferred`, сохраняет worktree и не является failure задачи, CI-fix
cycle или поводом для retry.

После merge #128 этот lifecycle обязателен для всех последующих задач.

## Отсечка и аудит

Текущая policy задаёт `08:00 Europe/Moscow`. На cutoff исполнитель не начинает новую задачу, не оставляет частично записанное состояние и доводит только короткий безопасный checkpoint/commit/PR. Утренний отчёт не создаётся автоматически в репозитории; используется [версионируемый шаблон](night-shift-morning-report-template.md).

Аудит восстанавливается из GitHub: URL issue/PR, commits, review comments/threads, CI runs и merge SHA. Секреты, prompts и содержимое private notes туда не записываются.

## Осознанно не поддерживается

В протоколе нет LangGraph, CrewAI, LangChain, Redis, Kafka, Celery, DB, vector/graph DB, message queue, daemon, OpenAI API key, paid orchestration, generic multi-agent runtime или автоматической записи в `second-brain-vault`. Night Shift не меняет `schema_version`, canonical notes, privacy boundaries или product semantics.
