# Оркестратор разработки Night Shift v1

Этот документ фиксирует ограниченный протокол репозитория для issue [#79](https://github.com/MikeMoore1337/second-brain/issues/79). Он не является автономным runtime, daemon, scheduler или хранилищем изменяемого состояния.

Машиночитаемая policy находится в [`config/night-shift-v1.yaml`](../../config/night-shift-v1.yaml), а детерминированный parser и gate helpers — в [`src/second_brain/application/night_shift.py`](../../src/second_brain/application/night_shift.py). Состояния GitHub issue/PR, комментарии, существующие review threads, commit SHA и CI остаются операционным источником истины. Policy описывает правила, но не хранит историю запусков.

## Постоянная политика качества

Codex Code Review — не lifecycle-механизм и не автоматический review на каждый
commit. Это bounded финальный semantic gate после GREEN exact-head CI. На один
PR разрешены максимум два managed requests: round 1 и не более одного
re-review round 2 только после подтверждённых blocking `P0/P1`, исправленных
одним batch на новом head. После clean round 1 повторный review не запускается;
после blocking round 2 результатом является `HUMAN_REQUIRED`.

Перед запросом проверяются PR, draft-state, current head, required CI именно
для этого head/base, существующие comments/reviews/statuses и наличие уже
запущенного review для того же SHA. Pending/completed review текущего SHA
переиспользуется. MEDIUM/LOW/NIT не открывают новый round. Implementer делает
ровно один bounded self-review до commit; отдельный review-agent, reviewer
subagent, adversarial audit или второй LLM verdict для этой цели не создаются.

Внешняя настройка Codex Cloud/GitHub не хранится в репозитории. Если там
включён automatic Codex review, владелец должен вручную отключить его:
`MANUAL_EXTERNAL_SETTING`.

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

## Parallel implementation и serialized finalization

В репозитории нет controller, lease registry или daemon, поэтому для
implementation не добавляется новая инфраструктура. Policy и gate helper
фиксируют минимальный enforceable контракт:

- каждый task владеет только своим worktree/branch/task state;
- независимые implementation tasks могут работать параллельно в разных
  worktree;
- общий repository-wide `exclusive-write` lease запрещён;
- read-only/diagnostic операции не требуют implementation write lease;
- conflict возможен только для конкретного worktree/task state, production
  deployment или finalization lane;
- finalization сериализуется на repository scope: refresh от текущего `main`,
  conflict resolution, affected verification, final push, exact-head CI,
  bounded Codex review, merge и release closeout;
- stale ownership можно освободить только при явно подтверждённом stale
  evidence. Cleanup не удаляет active чужой worktree или неизвестную
  регистрацию.

Пока одна task находится в finalization, другие tasks могут продолжать
implementation/tests в своих worktree. После merge готовая task сначала
обновляется от нового base и повторяет только затронутые base-dependent
проверки. Готовая implementation не уничтожается из-за более раннего merge
другой task.

## Проверка, слияние и release gate

Каждый PR должен содержать ссылку на issue, exact base/head SHA, краткое
описание scope и checks. Последовательность unattended/autopilot workflow:

```text
implementation
  -> targeted verification
  -> final deterministic verification
  -> self-review
  -> commit/push
  -> PR
  -> required exact-head CI GREEN
  -> Codex Code Review round 1
  -> batch fix only if blocking P0/P1
  -> affected verification
  -> push and exact-head CI GREEN
  -> Codex Code Review round 2 (at most once)
  -> merge
  -> deploy
  -> production smoke/closeout
```

PR создаётся до ожидания PR-triggered CI. Review не запускается до завершения
implementation, пока CI pending/failing, после промежуточного commit, для
того же head SHA повторно или только ради подтверждения зелёных deterministic
checks. После любого code-changing push прежний review stale для merge decision.

Шаги после commit выполняются только при отсутствии явного
owner/human/external/destructive gate. Codex Review не заменяет security,
legal, credentials, production, billing, manual-device или visual gates.

GREEN merge возможен только одновременно при выполнении всех условий:

- current PR head и base — полные SHA, а все evidence привязаны к current head и
  current base `main`;
- все required ruleset checks завершились успешно именно для current head/base:
  `quality`, `windows-ssl-regression`, `frontend (ubuntu-latest)` и
  `frontend (windows-latest)`;
- Codex Review завершён с clean result на current head в round 1 или round 2;
- нет нерешённых существующих GitHub review threads и известных unresolved
  BLOCKER/HIGH из реализации или QA;
- PR имеет состояние `CLEAN`/mergeable;
- scope не изменился;
- dependency и human gates отсутствуют;
- risk lane равен `GREEN`.

`quality` включает relevant targeted tests, lint/format/typecheck и применимые
integration/e2e checks. Required contexts являются source of truth ruleset;
aggregate `checks` не считается required в текущем GitHub ruleset. После merge
для production-facing задачи обязательны штатные deploy, smoke и closeout.

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

В протоколе нет LangGraph, CrewAI, LangChain, Redis, Kafka, Celery, DB, vector/graph DB, message queue, daemon, OpenAI API key, paid orchestration, generic multi-agent runtime или автоматической записи в `second-brain-vault`. Night Shift не меняет product `schema_version`, canonical notes, privacy boundaries или product semantics.
