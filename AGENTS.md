# Инструкции репозитория

## Назначение

Это программный репозиторий `second-brain`. Здесь находятся код приложения,
контракт конфигурации, тесты, автоматизация, интеграции и документация проекта.
Пользовательские Markdown-знания находятся в независимом репозитории
`second-brain-vault` и не должны копироваться сюда.

## Граница репозитория

- Путь к vault берётся только из явной конфигурации, например
  `SECOND_BRAIN_VAULT_PATH`; машинные абсолютные пути в коде запрещены.
- Нельзя добавлять сюда личные заметки, вложения vault или состояние workspace
  Obsidian.
- Изменения, ограниченные этим репозиторием, не должны менять
  `second-brain-vault`.
- `domain` и `application` не зависят от Obsidian, Git, LLM-провайдера, FastAPI,
  Telegram или другого способа доставки.
- Интеграции добавляются только как явные ports/adapters и только в рамках
  текущей задачи. Пустые будущие модули не создаются.

## Язык документации

Человекочитаемая документация проекта пишется на русском языке. Названия
технологий, библиотек, команд, CLI-опций, переменных окружения, файлов,
каталогов, классов, функций и API-полей сохраняются в оригинальном виде.
Код, тестовые идентификаторы и машинно-читаемые форматы не переводятся.
Тексты внешних стандартов и лицензий сохраняются в оригинале, а пояснения к
ним даются по-русски.

## Безопасность

- Local/offline read-only команды не вызывают сеть или LLM.
- Networked `llm draft` и `research draft` остаются read-only и только возвращают
  validated `NoteDraft`; они не получают `--apply` и не пишут в vault. Команда
  `note create-from-draft --file PATH` является отдельной local/offline write
  operation: dry-run по умолчанию, реальная запись только с явным `--apply`.
- Explicitly networked read-only команды могут выполнять только заявленную
  внешнюю read-operation; это не является общим разрешением сети для любых
  read-only команд. `research read` выполняет максимум одну research
  operation через свой заявленный research backend, `llm draft` — максимум
  одну LLM draft operation, а `research draft` — максимум одну research
  operation, затем максимум одну LLM draft operation.
- Все эти networked read-only команды не пишут в vault или Git. Для них
  запрещены retry, fallback, tools и function execution.
- Credentials для networked LLM остаются внутри существующего adapter/security
  boundary и не становятся CLI или общим read-only контрактом.
- Запись в vault требует path containment, dry-run/diff, hash preconditions,
  временный файл, no-overwrite publication, post-write validation и безопасный
  rollback. В текущем этапе разрешён только use case создания одной managed
  note типа `project`, `area`, `resource` или `zettel`; реальная запись
  выполняется только с явным `--apply`.
- При точечных изменениях нужно сохранять неизвестные front matter fields и
  Obsidian wikilinks.
- Proposal automation для новой managed note пишет только в новую
  `automation/*` branch и публикует изменения через PR в `main`; прямой
  automation write/commit/push в `main`, force push и auto-merge запрещены.
- Proposal dry-run не вызывает network и не меняет vault, Git refs, index или
  worktree. Apply требует clean/synced `main` и не выполняет pull, rebase, merge
  или reset автоматически.
- Секреты и настоящий `.env` не коммитятся.
- Commit, push, PR, merge и deploy выполняются только по явному запросу.

## Проверки разработки

Перед предложением изменения выполните:

```text
uv run ruff format --check .
uv run ruff check .
uv run mypy src tests
uv run pytest
```

## Принцип минимально достаточного выполнения

Экономьте Codex-лимиты: выполняйте только действия, которые дают новую
информацию или необходимы для реализации и проверки текущей задачи.

- Не повторяйте успешно пройденные проверки, если затрагиваемый ими код не
  менялся.
- Проект поддерживает только Python 3.14; не запускайте проверки на нескольких
  версиях Python.
- Во время разработки используйте точечные тесты. Полный обязательный набор
  `ruff` / `mypy` / `pytest` запускайте один раз после завершения изменений.
- Не перечитывайте неизменившиеся файлы, issue и документацию, которые уже были
  изучены.
- Не выполняйте повторные полные аудиты репозитория для локальных изменений.
- Не запускайте повторно `git status`, `git diff`, `git log` и аналогичные
  команды без изменения состояния.
- Не исследуйте заново уже принятые архитектурные решения.
- Не создавайте дополнительные отчёты, временные артефакты и диагностику без
  практической необходимости.
- Группируйте связанные чтения, изменения и проверки и выбирайте минимальный
  набор команд, достаточный для надёжной проверки результата.
- После выполнения acceptance criteria и финальной проверки остановитесь; не
  выполняйте дополнительные действия "на всякий случай".

Экономия действий не должна приводить к пропуску обязательных acceptance
criteria, тестов безопасности или проверки непосредственно изменённого
поведения.


## Role & Agent Pack

В репозитории используется небольшой набор ролей и агентных workflow.
Их задача - сделать решения Codex последовательными, не создавая сложную
многоагентную инфраструктуру.

Канонические описания:

- `ROLE_MANIFEST.json` - реестр ролей;
- `AGENT_MANIFEST.json` - реестр агентных workflow;
- `ai/roles/*.md` - обязанности и границы ролей;
- `ai/agents/*.md` - пошаговые workflow;
- `ai/prompts/*.md` - готовые входные шаблоны;
- `ai/evals/*.md` - критерии качества.

### Принцип маршрутизации

Перед реализацией задачи выберите одну основную роль. Дополнительную роль
подключайте только если она реально меняет решение или нужна для обязательной
проверки. Не создавайте искусственную "команду" из всех ролей.

| Тип задачи | Основная роль | Дополнительная роль при необходимости |
| --- | --- | --- |
| Структура vault, PARA/Zettelkasten, front matter contract | `knowledge-architect` | `security-reviewer` |
| Качество заметок, дедупликация, связи | `knowledge-curator` | `knowledge-architect` |
| CLI, cron, автоматизация, Git workflow | `automation-engineer` | `qa-engineer` |
| LLM/API/Telegram/Obsidian/GitHub integrations | `integration-engineer` | `security-reviewer` |
| Тесты, регрессии, acceptance criteria | `qa-engineer` | профильная роль |
| Секреты, доступ, запись в vault, privacy | `security-reviewer` | профильная роль |

Если задача затрагивает реальную запись в vault, обработку секретов,
неподтверждённые внешние данные или потенциально разрушительные операции,
`security-reviewer` обязателен как контрольная роль.

### Выбор агентного workflow

Агент - это повторяемый workflow, а не постоянно работающий автономный процесс.

- `inbox-processor` - разобрать новый входящий материал;
- `knowledge-distiller` - превратить большой источник в краткое знание и
  атомарные заметки;
- `research-agent` - собрать и синтезировать проверяемое исследование;
- `vault-maintainer` - найти проблемы структуры и качества vault;
- `review-agent` - провести daily/weekly/monthly review.

Используйте workflow только когда задача совпадает с его назначением. Для
обычной разработки отдельный агент не нужен.

### Приоритет инструкций

1. Этот `AGENTS.md` и ограничения безопасности репозитория.
2. Acceptance criteria текущей задачи.
3. Профиль выбранной роли.
4. Workflow выбранного агента.
5. Шаблон prompt/eval.

Роль или агент не могут ослабить правила безопасности, разрешить запись без
`--apply`, обойти dry-run/diff, изменить границу репозиториев или самостоятельно
выполнить commit/push/PR/merge/deploy.

## Night Shift Development Orchestrator v1

Bounded repository protocol для явно активированного overnight batch описан в
[`docs/automation/night-shift-v1.md`](docs/automation/night-shift-v1.md), а его
машиночитаемая policy находится в `config/night-shift-v1.yaml`.

Постоянное owner-level решение: Codex Code Review отключён и не используется
как release gate, поскольку расходует Codex usage. Готовность определяется
детерминированными тестами, static analysis, exact-head CI, обязательным
aggregate GitHub status `checks`, отсутствием известных unresolved BLOCKER/HIGH,
mergeability PR и явно требуемыми human/external gates. Отдельный Codex
reviewer или LLM verdict для merge не создаётся.

- `enabled_by_default: false`: обычная задача не получает autonomous commit,
  push, PR или merge права из-за наличия policy;
- GitHub issue/PR state, existing review threads, exact SHA и CI остаются source
  of truth;
- RED/YELLOW gates, failure budget и dependency blocking нельзя обходить;
- Night Shift не ослабляет Safe Write, privacy, Python 3.14 и границу
  `second-brain-vault`;
- этот protocol не добавляет agent runtime, daemon, queue, DB или provider.
- после verified GREEN merge применяется bounded Git-only post-task cleanup из
  [`scripts/worktree_cleanup.py`](scripts/worktree_cleanup.py); его receipt,
  safety rules и `cleanup_deferred` описаны в
  [`docs/automation/night-shift-v1.md`](docs/automation/night-shift-v1.md).

### Handoff

Если задача действительно требует смены роли или агента, передайте только:

- цель;
- подтверждённые факты;
- изменённые файлы;
- открытые риски;
- оставшиеся acceptance criteria.

Не пересказывайте всю историю задачи и не повторяйте уже выполненную
диагностику.

### Ограничение автономности

Не добавляйте Supervisor/Manager/Planner Agent, очереди сообщений, LangGraph,
CrewAI, отдельную БД состояния агентов или agent-to-agent RPC до появления
конкретной продуктовой необходимости и отдельной архитектурной задачи.

`second-brain-vault` не хранит определения ролей и агентов. Он остаётся
каноническим хранилищем пользовательских знаний.
