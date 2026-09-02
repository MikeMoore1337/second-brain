# Second Brain

Second Brain — открытое Markdown-first ядро автоматизации личной базы знаний.
Markdown/YAML-файлы внешнего vault являются каноническим источником истины.
Obsidian — основной поддерживаемый клиент, но ядро не зависит ни от Obsidian,
ни от Git.

## Agent & Role Pack v1

Навигация по ролям и повторяемым workflow: [AGENTS.md](AGENTS.md),
[ROLE_MANIFEST.json](ROLE_MANIFEST.json), [AGENT_MANIFEST.json](AGENT_MANIFEST.json)
и [ai/README.md](ai/README.md).

Релиз Foundation проверяет контракт vault, metadata заметок, Obsidian wikilinks,
path containment и размеры вложений. Safe Write Operations v1 добавляет ровно
один write use case: безопасное создание managed note типа `project`, `area`,
`resource` или `zettel` из существующего vault template.

## Настройка разработки

Требуются Python 3.14 и [uv](https://docs.astral.sh/uv/).

```powershell
uv sync
Copy-Item .env.example .env
uv run second-brain --env-file .env doctor
uv run second-brain --env-file .env vault validate
```

## Безопасное создание managed note

Команда `note create` по умолчанию работает в режиме `dry-run`: она читает
manifest и template, строит путь, front matter и diff, но не изменяет vault.

```powershell
uv run second-brain --env-file .env note create `
  --type project --title "Мой проект"
```

Поддерживаются типы `project`, `area`, `resource` и `zettel`; каждый тип пишет
только в соответствующий root из `second-brain.yaml` и использует одноимённый
файл из `_templates`. Для реальной записи требуется отдельное явное
подтверждение:

```powershell
uv run second-brain --env-file .env note create `
  --type project --title "Мой проект" --apply
```

Каждый отдельный запуск `dry-run` и последующий отдельный `--apply` получает
новые UUIDv7 и `created`; состояние между запусками не хранится.

Операция проверяет containment vault-relative пути и отсутствие symlink/junction
в target/template path, никогда не перезаписывает существующий файл, пишет
через временный файл с эксклюзивным созданием, проверяет созданную note полным
scanner validation и при ошибке удаляет только файл, совпадающий с receipt по
identity и SHA-256. Git, LLM, Search, API, Telegram, Agent Reach и Inbox promote
в этот use case не входят.

Файл окружения выбирается явно. Относительный
`SECOND_BRAIN_VAULT_PATH` разрешается относительно parent выбранного env-файла,
но никогда не относительно текущего рабочего каталога процесса. Без env-файла и
`config_root` путь к vault должен быть абсолютным.

## Коды завершения CLI

- `0`: сканирование завершено без validation errors;
- `1`: сканирование завершено и обнаружило validation errors;
- `2`: ошибка конфигурации или runtime не позволила выполнить корректное scan.

Для `note create` код `0` означает dry-run или успешно созданную note, `1` —
защитный отказ либо rollback после неуспешной post-write validation, `2` —
ошибку конфигурации или runtime.

Обе команды поддерживают `--format text` (по умолчанию) и `--format json`.

## Архитектура

См. [обзор архитектуры](docs/architecture/overview.md) и
[vault contract v1](docs/architecture/vault-contract-v1.md).

## Лицензия

Проект распространяется по лицензии Apache-2.0. Полный текст находится в
`LICENSE` и сохраняется на языке оригинала.
