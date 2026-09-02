# Second Brain Agent & Role Pack v1

Pack добавляет в `second-brain` лёгкую маршрутизацию ролей и повторяемые
агентные workflow без отдельного агентного фреймворка.

## Состав

- 6 ролей;
- 5 workflow-агентов;
- 5 готовых prompts;
- 2 eval-checklist;
- два machine-readable manifest;
- обновлённый корневой `AGENTS.md`.

## Модель

```text
Task
  -> AGENTS.md
  -> primary role
  -> optional agent workflow
  -> existing application/domain/ports/adapters
  -> safe preview/diff
  -> explicit --apply when supported
  -> vault
```

Роль отвечает за точку зрения и критерии решения.
Агент отвечает за повторяемый workflow.
Они не являются автономными сервисами и не требуют LangGraph/CrewAI.

## Где хранить

Весь pack находится в репозитории `second-brain`.

В `second-brain-vault` не копируются:

- `ROLE_MANIFEST.json`;
- `AGENT_MANIFEST.json`;
- каталог `ai/`;
- определения агентов и ролей.

Vault хранит только знания, его собственный `AGENTS.md`, конфигурацию и
Obsidian-совместимые данные.

## Как использовать Codex

Для обычной задачи достаточно описать задачу. Корневой `AGENTS.md` заставляет
Codex выбрать профиль только если он действительно нужен.

Можно вызвать workflow явно:

```text
Используй workflow inbox-processor для этой заметки. Только dry-run.
```

или:

```text
Используй роли knowledge-architect и security-reviewer.
Спроектируй изменение metadata contract, но ничего не применяй.
```

## Что pack намеренно не добавляет

- Supervisor/Manager Agent;
- LangGraph/CrewAI;
- очереди сообщений;
- отдельное хранилище состояния агента;
- автоматический commit/push;
- прямую LLM-запись в vault.
