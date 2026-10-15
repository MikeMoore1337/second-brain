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

В PowerShell:

```powershell
uv tool install graphifyy
uv tool update-shell
```

После перезапуска PowerShell:

```powershell
graphify --version
```

Graphify не добавляется в `pyproject.toml`.

### Первый безопасный граф

Запускать из корня checkout только в code-only режиме, без semantic extraction
и без внешнего LLM:

```powershell
cd D:\Pet-projects\second-brain-workspace\second-brain
graphify . --code-only
```

Проверенный baseline на owner workstation: полный code-only граф всего
репозитория, включая `src`, `tests`, `scripts` и frontend. Non-code файлы
при таком запуске пропускаются.

После построения графа полезны точечные команды:

```powershell
graphify query "How are Self Retrieval and Search connected?"
graphify path "BuildSelfContext" "SearchVault"
graphify explain "BuildSelfContext"
```

Для архитектурных связей предпочтительнее `path` и `explain`: широкий
natural-language `query` может вернуть слишком большой подграф.

`graphify-out/` локальный, производный и игнорируется Git.

### Локальный Graphify skill для Codex Desktop

Graphify skill можно установить project-scoped:

```powershell
graphify install --project --platform codex
```

Installer также пытается изменить `AGENTS.md` и `.codex/hooks.json`. Эти
генерируемые изменения не являются каноническими для проекта: правила Graphify
уже закреплены в репозиторном `AGENTS.md`, а Codex Desktop получает guidance
через него.

После локальной установки:

```powershell
git restore -- AGENTS.md .codex/hooks.json
Remove-Item ".codex\hooks.json.graphify-bak" -Force -ErrorAction SilentlyContinue

if (-not (Select-String -Path ".git\info\exclude" -Pattern "^\.codex/skills/graphify/$" -Quiet -ErrorAction SilentlyContinue)) {
    Add-Content ".git\info\exclude" ".codex/skills/graphify/"
}
```

Каталог `.codex/skills/graphify/` остаётся локально доступен Codex, но не
коммитится и не vendor'ится в `second-brain`.

В текущем Codex Desktop отдельный callable `@graphify` может не отображаться.
Это не блокер: при наличии `graphify-out/` Codex использует существующие
Graphify artifacts и затем подтверждает важные выводы по исходникам и тестам.

### Что не включаем автоматически

На первом этапе не использовать:

- `graphify ..\second-brain-vault` и любой путь из
  `SECOND_BRAIN_VAULT_PATH`;
- semantic extraction пользовательских/private данных;
- `graphify --watch`;
- Git hooks Graphify;
- MCP server Graphify;
- API keys или внешние semantic providers ради Graphify;
- автоматическое перестроение графа в CI или production.

### Обновление и удаление

```powershell
uv tool upgrade graphifyy
# удалить:
uv tool uninstall graphifyy
Remove-Item -Recurse -Force graphify-out -ErrorAction SilentlyContinue
Remove-Item -Recurse -Force .codex\skills\graphify -ErrorAction SilentlyContinue
```

## Ponytail

Ponytail подключается как host-level plugin Codex. Codex CLI нужен только для
первичной установки и просмотра/trust hooks; ежедневная работа выполняется в
Codex Desktop.

Если `codex` ещё не установлен:

```powershell
npm install -g @openai/codex
codex --version
```

Установка Ponytail:

```powershell
codex plugin marketplace add DietrichGebert/ponytail
codex plugin add ponytail@ponytail
```

После установки один раз открыть интерактивный `codex`, выполнить `/hooks`,
просмотреть и доверить hooks. Ponytail 4.10.0 регистрирует три lifecycle hook:
`SessionStart`, `UserPromptSubmit` и `SubagentStart`. Существующие проектные
hooks, например Impeccable `PostToolUse` и `Stop`, проверяются отдельно.

Затем полностью перезапустить Codex Desktop и включить для нового чата:

```text
@ponytail lite
```

Для `second-brain` рекомендован `lite`: выполняется запрошенная реализация, а
более простой вариант отмечается отдельно. `ultra` не используется по
умолчанию.

Ponytail не имеет права сокращать security/privacy validation, error handling,
обязательные проверки, accessibility, fail-closed gates, `HUMAN_REQUIRED`,
owner approval или другие правила `AGENTS.md`.

Удаление:

```powershell
codex plugin remove ponytail
```

## 21st.dev (необязательный инструмент для интерфейса)

Официальный плагин 21st.dev устанавливается в **Codex Desktop пользователя** и предоставляет
каталог компонентов и MCP, но не изменяет зависимости Second Brain. В репозитории нет
обязательной зависимости от 21st.dev, `second-brain-vault` не используется и не передаётся.
Установка на доверенном ПК:

```powershell
codex plugin marketplace add 21st-dev/codex-plugin
codex plugin add 21st@21st
codex mcp login 21st
codex plugin list
codex mcp list
```

15.10.2026 на рабочем ПК подтверждены установка host-level `21st@21st` версии 0.5.0 и
успешная OAuth-авторизация MCP. Для другого ПК требуется отдельная локальная установка и вход.

### Фактическая проверка 15.10.2026

Статус MCP и авторизации: `PASS`. Из нового чата Codex Desktop выполнены только публичные
metadata-проверки:

- `mcp__21st__get_usage`: `tier=free`, `aiGenerationEnabled=false`,
  `freeRetrievalsRemaining=2/2`, `credits.balance=0`;
- `mcp__21st__search`: получены результаты для обезличенных запросов про knowledge graph,
  timeline и визуализацию работы AI-агентов;
- `get_component`, установка, генерация, публикация и платные действия не выполнялись,
  поэтому бесплатные загрузки исходного кода не расходовались.

`second-brain-vault`, заметки, исходники проекта, пользовательские данные, токены,
конфигурация и внутренние документы в MCP не передавались. `PASS` означает доступность
MCP и успешную авторизацию, а не разрешение на импорт.

### Результат сравнения

- [Note Graph (36176)](https://21st.dev/@astralyxdev/components/note-graph):
  публичный источник [astralyxdev/astralyx-ui](https://github.com/astralyxdev/astralyx-ui),
  лицензия MIT подтверждена [LICENSE](https://github.com/astralyxdev/astralyx-ui/blob/main/LICENSE).
  Registry использует внутренние модули `lib-motion`, `lib-styles`, `lib-use-force-graph`,
  `lib-utils`; через `lib-utils` требуется `cn`. Текущие React 19/Tailwind 4 и
  `motion` совместимы частично, но токены и внутренние модули не являются drop-in для
  Second Brain. В приложении уже есть канонический Timeline с known/unknown-событиями и
  безопасными evidence-полями; отдельного graph-проекционного API нет.
- [Agent Trace (26968)](https://21st.dev/@n1m4mz/components/agent-trace):
  каталог заявляет MIT и зависимости `motion`/`lucide-react`, но публичный upstream
  source и файл лицензии именно этого компонента независимо не подтверждены. Поэтому
  юридический статус для внедрения: `NOT_CONFIRMED`. Кроме того, компонент ожидает
  waterfall spans, длительности, token counts, вложенные агенты, retries и replay, тогда
  как Personal Agent сейчас хранит дискретные шаги, статусы, provenance и owner gates;
  таких telemetry-данных в контракте нет.

Решение: не импортировать компоненты и не добавлять зависимости. Note Graph — единственный
кандидат для отдельного визуального прототипа после owner approval: (A) небольшой SVG-граф
на синтетическом fixture с обязательным доступным списком как fallback; (B) переключатель
Timeline/Graph только после согласования безопасной проекции из разрешённых метаданных.
Agent Trace имеет смысл прототипировать только после отдельного продуктового решения о
фактическом trace-контракте; token counts, model names и retries нельзя додумывать из
текущих данных. Реализация возможна лишь после visual approval, проверки приватности,
keyboard/a11y, 320/360/390/430 responsive states и тестов.


- Только публичные поисковые формулировки, просмотр ссылок и примеров кода. Нельзя загружать
  личный vault, заметки, приватные файлы, проектные исходники, снимки реальных данных,
  токены, конфигурацию или внутренние документы в MCP и облачные инструменты 21st.dev.
- Не запускать импорт компонентов, генерацию UI на удалённом AI, публикацию тем/компонентов,
  платные функции и автоматическую синхронизацию проекта без отдельного разрешения.
- Прежде чем принять компонент, проверить конкретную лицензию автора и зависимостей,
  происхождение ресурсов, совместимость React 19/Tailwind и влияние на сборку/скорость.
- Сохранять действующую русскую локализацию, собственные иконки, доступность,
  мобильную верстку и единый дизайн. Новые визуальные решения сначала демонстрировать
  владельцу на макетах и реальных скриншотах, затем получать явное согласование.
- При ошибке входа или исчерпании лимитов искать решение в локальном коде и официальной
  документации: Second Brain не должен зависеть от внешнего MCP.

Откат: `codex plugin remove 21st`. Плагин установлен отдельно от Ponytail и Graphify,
поэтому их настройки и исходники приложения откат не затрагивает.

## Проверка эффекта

Первые задачи после подключения оценивать по фактическому поведению:

- Graphify должен уменьшать повторное широкое чтение репозитория и помогать
  находить зависимости перед изменениями;
- Ponytail должен уменьшать лишние абстракции и объём изменений без потери
  обязательной проверки;
- при конфликте с `AGENTS.md`, acceptance criteria или safety gates внешний
  инструмент отключается, а правила репозитория сохраняют приоритет.
