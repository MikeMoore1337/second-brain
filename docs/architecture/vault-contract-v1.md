# Контракт vault v1

## Корневой manifest

В корне vault находится один tracked файл `second-brain.yaml`:

```yaml
schema_version: 1
vault_id: 019...
default_language: ru

paths:
  inbox: 00 Inbox
  projects: 10 Projects
  areas: 20 Areas
  resources: 30 Resources
  zettelkasten: 40 Zettelkasten
  archive: 90 Archive
  templates: _templates
  attachments: _attachments

attachments:
  warning_size_bytes: 10485760
  max_size_bytes: 52428800
```

`schema_version` — integer. `vault_id` — стабильный UUIDv7 в lowercase UUID-формате
с дефисами. Все пути относительны корню vault, используют только `/` и не могут
содержать absolute path, пустые сегменты (включая `//` и завершающий `/`) или
сегменты `.`/`..`. Неизвестные поля дают warning, чтобы reader мог пережить
расширение контракта.

## Схема Note

Managed Markdown note содержит YAML front matter:

```yaml
---
id: 019...
type: zettel
created: 2026-09-02T12:00:00+03:00
updated: 2026-09-02T12:00:00+03:00
tags: []
---
```

Обязательны `id`, `type` и `created`. `updated` и `tags` optional. Допустимые
начальные значения `type`: `note`, `zettel`, `project`, `area`, `resource`.
`inbox` и `archive` — location/lifecycle, а не `NoteType`.

`created` и `updated` должны быть RFC 3339 с явным UTC offset. `id` не зависит от
filename, title или пути и не меняется при rename/move.

Safe Write Operations v1 создаёт только `project`, `area`, `resource` или
`zettel`. Root и template выбираются по manifest; содержимое template не
дублируется в коде. `id` генерируется через stdlib `uuid.uuid7()` на Python
3.14, а `created` получает явный локальный UTC offset.

В Inbox Markdown без любого из `id`, `type`, `created` считается unmanaged и
получает warning. Если присутствует хотя бы одно из этих полей, это managed
attempt и все три поля обязаны быть корректными. В остальных content roots каждый
Markdown является managed note.

Templates и attachments не являются notes. Неизвестные front matter fields
принимаются и должны сохраняться будущими точечными write-операциями.
Корневые служебные каталоги Obsidian (`.obsidian`, `.trash`) и Git (`.git`)
не входят в объявленные content roots и поэтому не классифицируются как notes.

Создание выполняется только после dry-run и явного `--apply`. Target не может
быть существующим или linked path и не перезаписывается. Перед публикацией
используется временный файл с эксклюзивным созданием; после публикации scanner
проверяет managed metadata и весь vault. При ошибке проверки выполняется
безопасный rollback только файла, чьи identity и SHA-256 совпадают с receipt.

## Wikilinks

Read-only parser v1 распознаёт:

- `[[Note]]`;
- `[[Note|Display text]]`;
- `[[Note#Heading]]`;
- `[[Note^block-id]]`;
- `![[...]]` и path-qualified targets;
- `[[#Heading]]` и `[[^block-id]]` как same-note anchors.

Alias и fragment отделяются от note target. В v1 проверяется существование
target note/file, но не heading или block. Уникальный basename разрешается,
несколько совпадений дают `ambiguous`. Front matter, fenced code и inline code
не должны порождать wikilink diagnostics.

UUID сохраняет application identity, но не заменяет rename/move workflow для
текстовых Obsidian wikilinks. Автоматическое переписывание inbound links в v1
отсутствует.

## Attachments

`_attachments/` предназначен для небольших файлов. Warning начинается с 10 MiB,
будущие import/write-операции ограничиваются 50 MiB. Read-only `doctor` только
сообщает о превышении и показывает total size; он ничего не удаляет и не перемещает.
