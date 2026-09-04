# VPS runtime bootstrap v1

Этот runbook подготавливает воспроизводимое user-level окружение для текущих
CLI use cases Second Brain. На этом этапе приложение не является long-running
service и не публикует web/API endpoint.

## Границы и результат

Поддерживаемая модель — один Linux VPS и один непривилегированный оператор.
`second-brain` и приватный `second-brain-vault` остаются двумя независимыми
sibling Git repositories. Git submodule не используется, а vault не копируется
в программный repository.

Логический layout:

```text
<SECOND_BRAIN_ROOT>/
├── second-brain/
├── second-brain-vault/
└── runtime/
    └── .env
```

`<SECOND_BRAIN_ROOT>` — только операторский пример; приложение его не
хардкодит. `runtime/.env` находится вне обоих repositories и имеет permissions
`600`. В нём v1 нужен только `SECOND_BRAIN_VAULT_PATH`. В layout выше значение
`../second-brain-vault` разрешается относительно каталога выбранного env-файла,
то есть указывает на sibling vault.

Bootstrap проверяет Linux, не-root пользователя, Git worktrees на `main`, clean
state, отсутствие незавершённой Git operation и приватность vault/runtime. Для
проверки актуального remote он сначала выполняет только
`git fetch --no-tags origin main` в каждом repository, а затем сравнивает
`main` и обновлённый `origin/main`. После этого он проверяет Python 3.14 и `uv`
и выполняет:

```text
uv sync --locked --python 3.14
uv run --python 3.14 second-brain --env-file <env-file> doctor
uv run --python 3.14 second-brain --env-file <env-file> vault validate
```

Ошибки о dirty/diverged state являются stop conditions. Скрипт не переключает
branch, не исправляет permissions, не разрешает конфликты и не удаляет файлы.

## Prerequisites

До запуска скрипта оператор должен иметь:

- Linux и отдельного непривилегированного пользователя-владельца обоих
  worktree;
- Python `3.14.x`, доступный system-level или через managed Python `uv`;
- `uv` в `PATH`;
- `git` в `PATH`;
- сетевой доступ только для обычного clone/fetch и получения locked
  dependencies, когда это требуется окружению.

Если Python 3.14 ещё не установлен, `deploy/bootstrap.sh` по умолчанию только
сообщит об этом. Опция `--install-python` явно разрешает user-level команду
`uv python install 3.14`; это может скачать Python, но не требует root.

`uv` сам bootstrap не скачивает и не исполняет. Установите его заранее по
[официальной инструкции uv](https://docs.astral.sh/uv/getting-started/installation/)
и просмотрите выбранный installer до запуска. Вариант с system package
manager или административной установкой Python является отдельным действием
оператора; `bootstrap.sh` не вызывает для этого привилегированные команды.

Команда `research read` — отдельный optional use case. Для типов `web` и `rss`
на VPS должен быть заранее доступен системный `curl` в `PATH`; приложение его
не устанавливает автоматически. RSS/Atom adapter дополнительно делает direct
egress только после adapter-level public DNS/IP validation и использует locked
`--resolve` pinning без redirects. Для типа `youtube` оператор должен заранее
установить и проверить внешний executable `yt-dlp` (`yt-dlp --version`); Second
Brain не устанавливает и не обновляет его, не передаёт ему cookies/auth/netrc
или proxy environment и не добавляет его в Python dependencies. YouTube adapter
читает только metadata и одну public VTT caption track без media download.
Python dependencies, включая `feedparser`, ставятся штатным `uv sync --locked
--python 3.14`; вручную добавлять их в runtime не нужно. Обычный bootstrap и
vault smoke не требуют `curl` или `yt-dlp`, если research adapter не
используется.

Для proposal workflow дополнительно требуется `gh` и уже настроенная auth
сессия. Проверка включается отдельной опцией `--check-gh` и использует только
`gh auth status`. Если оператор предпочитает `GH_TOKEN`, он должен существовать
только во внешнем process environment на время команды; значение нельзя
помещать в tracked files, runtime env-файл или командные примеры.

## Первый bootstrap

Все следующие команды выполняются от будущего непривилегированного владельца
runtime. Путь — пример; замените его на выбранный оператором root, не меняя
структуру sibling directories.

1. Создайте пустой root и склонируйте два независимых repositories. Public
   `second-brain` не требует secret для read/clone. Private vault использует
   заранее настроенный Git credential mechanism пользователя; credentials не
   передаются приложению и не записываются в repository. Не клонируйте поверх
   существующего непустого каталога: при таком состоянии остановитесь.

   ```bash
   SECOND_BRAIN_ROOT="$HOME/.local/share/second-brain"
   mkdir -p "$SECOND_BRAIN_ROOT"
   git clone https://github.com/MikeMoore1337/second-brain.git "$SECOND_BRAIN_ROOT/second-brain"
   git clone https://github.com/MikeMoore1337/second-brain-vault.git "$SECOND_BRAIN_ROOT/second-brain-vault"
   ```

2. Убедитесь, что оба clone находятся на `main`, а vault доступен только его
   владельцу. Не исправляйте неожиданное состояние массовым `chown` или
   ослаблением permissions.

   ```bash
   chmod 700 "$SECOND_BRAIN_ROOT/second-brain-vault"
   mkdir -m 700 "$SECOND_BRAIN_ROOT/runtime"
   cp --no-clobber "$SECOND_BRAIN_ROOT/second-brain/deploy/env.example" \
     "$SECOND_BRAIN_ROOT/runtime/.env"
   chmod 600 "$SECOND_BRAIN_ROOT/runtime/.env"
   ```

   Если `runtime` или `.env` уже существуют, не перезаписывайте их: проверьте
   владельца и permissions вручную. В `.env` оставьте только несекретную
   настройку из примера либо явно заданный оператором абсолютный путь к
   sibling vault.

3. Запустите bootstrap из любого каталога, явно передав root:

   ```bash
   "$SECOND_BRAIN_ROOT/second-brain/deploy/bootstrap.sh" \
     --root "$SECOND_BRAIN_ROOT"
   ```

   Если Python 3.14 отсутствует и выбран managed Python, повторите с явным
   opt-in:

   ```bash
   "$SECOND_BRAIN_ROOT/second-brain/deploy/bootstrap.sh" \
     --root "$SECOND_BRAIN_ROOT" --install-python
   ```

   Для проверки будущего proposal workflow:

   ```bash
   "$SECOND_BRAIN_ROOT/second-brain/deploy/bootstrap.sh" \
     --root "$SECOND_BRAIN_ROOT" --check-gh --proposal-preflight
   ```

   `--proposal-preflight` вызывает текущий `proposal note create` без `--apply`.
   Это dry-run: он не создаёт тестовую note, не меняет vault, Git index/refs,
   branch, commit, push, PR или network state.

## Smoke и ежедневная проверка

Минимальный smoke — это именно существующие `doctor` и `vault validate`; он не
создаёт пользовательских заметок:

```bash
cd "$SECOND_BRAIN_ROOT/second-brain"
uv run --python 3.14 second-brain \
  --env-file "$SECOND_BRAIN_ROOT/runtime/.env" doctor
uv run --python 3.14 second-brain \
  --env-file "$SECOND_BRAIN_ROOT/runtime/.env" vault validate
```

Обе команды должны завершиться с code `0`. `note create` с `--apply` не входит
в smoke и запускается только как отдельная явно подтверждённая операция.

## Безопасное обновление

Обновление не выполняется через `git pull`: сначала состояние проверяется, затем
делается fetch и применяется только fast-forward. Для каждого repository
повторите следующие проверки, не меняя branch автоматически:

```bash
git -C "$SECOND_BRAIN_ROOT/second-brain" symbolic-ref --quiet --short HEAD
git -C "$SECOND_BRAIN_ROOT/second-brain" status --porcelain=v1 --untracked-files=all
git -C "$SECOND_BRAIN_ROOT/second-brain" fetch --no-tags origin main
git -C "$SECOND_BRAIN_ROOT/second-brain" merge-base --is-ancestor main origin/main
git -C "$SECOND_BRAIN_ROOT/second-brain" merge --ff-only origin/main
```

Для vault используйте те же команды, заменив путь на
`$SECOND_BRAIN_ROOT/second-brain-vault`. Первая команда должна вывести `main`,
вторая — ничего, а `merge-base --is-ancestor` должен завершиться успешно.
Перед fetch также проверьте отсутствие `MERGE_HEAD`, `CHERRY_PICK_HEAD`,
`REVERT_HEAD`, `REBASE_HEAD`, `rebase-merge`, `rebase-apply` и `BISECT_LOG` в
Git directory.

Если worktree dirty, branch не `main`, local `main` уже ушёл вперёд,
`main` и `origin/main` diverged, отсутствует credential для private vault или
fast-forward не проходит — остановитесь. Не используйте `reset`, `clean`,
rebase, force push или автоматическое разрешение конфликтов. Сначала разберите
состояние вручную, затем повторите весь preflight.

После успешного fast-forward запустите bootstrap:

```bash
"$SECOND_BRAIN_ROOT/second-brain/deploy/bootstrap.sh" \
  --root "$SECOND_BRAIN_ROOT"
```

Он выполнит `uv sync --locked --python 3.14`, повторно проверит оба sibling
repository и запустит оба CLI smoke.
Обновление vault само по себе не должно приводить к записи или миграции данных.

## GitHub credentials и proposal workflow

`second-brain` можно читать и клонировать публично. Private
`second-brain-vault` требует уже настроенный путь credentials пользователя:
credential helper, SSH key/agent или иной поддерживаемый Git механизм. Proposal
workflow выполняет push только в новую `automation/*` branch vault и создаёт PR
через `gh`; приложение не читает, не копирует и не сохраняет GitHub token.

Перед apply оператор отдельно проверяет `gh auth status` и clean/synced `main`.
Ни `bootstrap.sh`, ни приложение не создают token автоматически. Не помещайте
credentials в `.env`, `deploy/env.example`, shell command examples, tracked
files или вывод диагностики.

## Recovery и исключённые части

Остановитесь при любой неизвестной структуре layout, небезопасных permissions,
отсутствующем Python/uv/git, ошибке env/config, ошибке `doctor` или
`vault validate`. Сохраните состояние для ручного разбора; bootstrap не пытается
лечить repository или vault.

В issue #9 сознательно не входят: real SSH/deploy на VPS, systemd service или
timer/cron, Docker/Compose, Caddy/Nginx, DNS/TLS и
`brain.mikemoore.top`, FastAPI/Web UI/API, Telegram, LLM, Search/FTS, Agent
Reach, RAG/embeddings, Redis/Celery/Kafka, monitoring/alerts, auto-update daemon
и backup system сверх Git. systemd появится только вместе с реальным
long-running worker/scheduler use case; Agent Reach должен жить в отдельном
environment/config location и не добавляется в `pyproject.toml`, vault или
runtime bootstrap.
