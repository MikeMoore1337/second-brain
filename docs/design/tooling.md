# Project-local design tooling pack v1

Этот пакет добавляет в репозиторий инструменты для production design track. Он
проектный: skill-файлы лежат в `.agents/skills/`, а не в пользовательском профиле
Codex. `.gitattributes` фиксирует LF для copied text payload, чтобы launcher и
lock hashes были одинаковыми после Windows/POSIX checkout. Production runtime
Python-проекта не изменён.

## Установленный состав

| Skill | Назначение |
| --- | --- |
| `impeccable` | design/audit/critique/polish/harden workflow и detector для UI |
| `emil-design-eng` | design-engineering принципы Emil Kowalski |
| `animate` | проектирование и реализация motion |
| `review-animations` | review существующей animation/motion реализации |
| `improve-animations` | read-only аудит motion с планом улучшений |
| `find-animation-opportunities` | поиск оправданных мест для motion |
| `prototype` | явно запрошенные альтернативные UI-варианты с visual picker |

Все семь директорий находятся в `.agents/skills/`. Для Emil-пакета выбран ровно
этот список из шести skill; остальные skill upstream-репозитория намеренно не
устанавливались. Файл `skills-lock.json` хранит источник и content hash каждого
Emil skill.

Impeccable установлен текущей CLI-командой:

```text
npx --yes impeccable install -y --providers=codex --scope=project
```

Это текущий эквивалент исторической формы `npx impeccable skills install ...`.
В проекте зафиксированы skill metadata `4.2.1` и bundled engine `0.1.2` в
`.agents/skills/impeccable/scripts/VERSION`. Bundled Windows launcher находится
в `.agents/skills/impeccable/scripts/impeccable.cmd`, поэтому для локального
вызова не нужен Node runtime.

## Вызов из Codex

В Codex выбирается project-local skill по имени `impeccable` и ему передаётся
команда вроде `shape`, `audit`, `critique` или `polish`. Для Windows прямой
эквивалент проверки контекста:

```powershell
.agents/skills/impeccable/scripts/impeccable.cmd context
```

Шесть Emil skills вызываются по своим именам: `emil-design-eng`, `animate`,
`review-animations`, `improve-animations`, `find-animation-opportunities` и
`prototype`. `review-animations` и `prototype` требуют явного запроса.

Установка не запускает `/impeccable init`: этот интерактивный шаг может создать
`PRODUCT.md`/design context и выполняется отдельно, когда он понадобится
конкретной production design задаче.

## Hook и границы безопасности

`.codex/hooks.json` — сгенерированный project-local manifest Impeccable. Он
запускает detector только после изменений и на остановке с относительным
Windows/POSIX launcher path, но сначала проверяет наличие локального engine
binary соответствующей платформы. Если binary нет, hook завершается успешно и
не запускает launcher fallback, `curl`, домашний cache или network download.
Для Codex первое включение hook требует явного подтверждения в `/hooks`; отказ,
отсутствие approval или отключённый hook не делают skills недоступными. Hook
является design-time инструментом и не входит в Python/Web production runtime.

Developer-local `config.local.json`, live state, cache, logs и pid/NDJSON
артефакты Impeccable закрыты targeted-правилами `.gitignore`; shared
`.impeccable/config.json` намеренно остаётся reviewable.

В payload и документации нет секретов, credentials или machine-local settings.
Приложение не получает новой npm/runtime-зависимости и не делает network calls
в production: bundled tooling используется только по явному design workflow.

## Обновление

Перед обновлением нужно снова проверить фактический CLI:

```powershell
npx --yes impeccable --help
npx --yes impeccable install -y --providers=codex --scope=project
npx --yes skills add emilkowalski/skills --list
```

Чтобы generated folder hashes не зависели от глобального Windows
`core.autocrlf`, перед scoped add временно задайте `core.autocrlf=false` для
Git subprocesses. После завершения переменные нужно удалить:

```powershell
$env:GIT_CONFIG_COUNT = "1"
$env:GIT_CONFIG_KEY_0 = "core.autocrlf"
$env:GIT_CONFIG_VALUE_0 = "false"
```

Затем повторить только шесть scoped add-команд (Windows не использует
`skills update`; повторный scoped add — детерминированный путь обновления):

```powershell
npx --yes skills add emilkowalski/skills --skill emil-design-eng --agent codex -y --copy
npx --yes skills add emilkowalski/skills --skill animate --agent codex -y --copy
npx --yes skills add emilkowalski/skills --skill review-animations --agent codex -y --copy
npx --yes skills add emilkowalski/skills --skill improve-animations --agent codex -y --copy
npx --yes skills add emilkowalski/skills --skill find-animation-opportunities --agent codex -y --copy
npx --yes skills add emilkowalski/skills --skill prototype --agent codex -y --copy
```

```powershell
Remove-Item Env:GIT_CONFIG_COUNT -ErrorAction SilentlyContinue
Remove-Item Env:GIT_CONFIG_KEY_0 -ErrorAction SilentlyContinue
Remove-Item Env:GIT_CONFIG_VALUE_0 -ErrorAction SilentlyContinue
```

После обновления нужно проверить список директорий, полный folder hash в
`skills-lock.json`, hook, лицензии и прогнать обычный Python 3.14 validation
gate. Нельзя коммитить
локальные credentials, `.impeccable/config.local.json`, пользовательские
settings или другие артефакты installer cache.

## Provenance и лицензии

* Impeccable: [pbakaus/impeccable](https://github.com/pbakaus/impeccable),
  Apache-2.0. Локальные копии [LICENSE](licenses/impeccable-LICENSE.txt) и
  upstream [NOTICE](licenses/impeccable-NOTICE.md) сохранены рядом с этим
  tooling pack.
* Emil skills: [emilkowalski/skills](https://github.com/emilkowalski/skills),
  MIT; локальная копия [LICENSE](licenses/emil-skills-LICENSE.txt) сохранена
  рядом с payload.
* Installer для Emil skills: [vercel-labs/skills](https://github.com/vercel-labs/skills).
* Bundled `modern-screenshot.umd.js`: пакет `modern-screenshot`, MIT; локальная
  копия [LICENSE](licenses/modern-screenshot-LICENSE.txt) сохранена рядом с
  остальными notices.

Внешние upstream-ссылки здесь нужны для обновления и аудита происхождения; они
не превращают design tooling в runtime dependency приложения.
