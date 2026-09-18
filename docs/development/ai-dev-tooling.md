# Graphify и Ponytail для разработки

Этот runbook относится только к локальному процессу разработки `second-brain`.
Он не меняет runtime приложения, CI, production и `second-brain-vault`.

## Graphify

### Назначение

Graphify используется как disposable code-intelligence слой для навигации по
растущей кодовой базе: связи между модулями, вызовы, импорты, пути между
компонентами и impact analysis. Каноническими источниками остаются исходный код,
тесты и нормативные документы репозитория.

### Установка на рабочей машине

В PowerShell из любого каталога:

```powershell
uv tool install graphifyy
graphify --version
```

Graphify не добавляется в `pyproject.toml`.

### Первый безопасный запуск

Начинать с code-only AST, без semantic extraction и без внешнего LLM:

```powershell
cd D:\Pet-projects\second-brain
graphify src
```

После построения графа доступны, например:

```powershell
graphify query "Как связаны Self Retrieval и Search?"
graphify path "BuildSelfContext" "SearchVault"
graphify explain "CreateManagedNote"
```

`graphify-out/` локальный и игнорируется Git.

### Что не включаем автоматически

На первом этапе не использовать:

- `graphify ..\second-brain-vault` и любой путь из
  `SECOND_BRAIN_VAULT_PATH`;
- semantic extraction пользовательских/private данных;
- `graphify --watch`;
- `graphify hook install`;
- MCP server Graphify;
- API keys или внешние semantic providers ради Graphify;
- автоматическое перестроение графа в CI или production.

Если code-only граф окажется полезным, отдельной задачей можно оценить граф
публичной документации `second-brain`. Это не даёт разрешение на обработку
private vault.

### Обновление и удаление

```powershell
uv tool upgrade graphifyy
# удалить:
uv tool uninstall graphifyy
Remove-Item -Recurse -Force graphify-out -ErrorAction SilentlyContinue
```

## Ponytail

Ponytail подключается к Codex как host-level plugin и не коммитится в
`second-brain`.

Установка:

```powershell
codex plugin marketplace add DietrichGebert/ponytail
codex plugin add ponytail@ponytail
```

После установки перезапустить Codex, открыть `/hooks`, проверить два lifecycle
hook Ponytail и доверять им только после просмотра. Для этого проекта начинать с:

```text
/ponytail lite
```

Не использовать `ultra` как режим по умолчанию. Ponytail не имеет права
сокращать security/privacy validation, обязательные проверки, accessibility,
fail-closed gates, `HUMAN_REQUIRED` или owner approval.

Удаление:

```powershell
codex plugin remove ponytail
```

Если команда `codex` недоступна в PowerShell, это проблема локальной установки
Codex CLI/PATH, а не репозитория. Репозиторная интеграция Graphify и правила
безопасности от этого не зависят.

## Проверка эффекта

Первые задачи после подключения оценивать по фактическому поведению:

- Graphify должен уменьшать повторное широкое чтение репозитория и помогать
  находить зависимости перед изменениями;
- Ponytail должен уменьшать лишние абстракции и объём изменений без потери
  обязательной проверки;
- при конфликте с `AGENTS.md`, acceptance criteria или safety gates внешний
  инструмент отключается, а правила репозитория сохраняют приоритет.
