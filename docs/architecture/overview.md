# Обзор архитектуры

## Границы репозиториев

`second-brain` — программное ядро: domain/application, адаптеры, CLI, тесты,
автоматизация и документация. `second-brain-vault` — независимый приватный
репозиторий с пользовательскими Markdown/YAML-знаниями, вложениями, шаблонами и
безопасной частью конфигурации Obsidian.

Vault является каноническим источником истины. Индексы, SQLite, embeddings и
кэш относятся к производному состоянию и могут быть пересозданы из vault.

## Слои Foundation и Safe Write Operations

```text
entrypoints/cli
        |
application: use cases, ports, validation, reports
        |
domain: NoteRecord, MarkdownDocument, NoteType, VaultManifest, rules
        ^
        |
adapters/vault: filesystem, YAML, Markdown, wikilinks
```

`domain` не знает о filesystem, Git, Obsidian, HTTP, SQLite или LLM. `adapters/vault`
выполняет безопасное read-only чтение, containment и разбор YAML/front matter/
wikilinks, возвращая `VaultSnapshot` с `MarkdownDocument`, raw links и diagnostics
чтения/parsing. `application`
оркестрирует read-only use cases, выполняет note schema и cross-record validation,
а затем формирует `ScanReport` и diagnostics через порты. Safe Write Operations v1
добавляет отдельный `CreateManagedNote`: application сначала строит plan через
`ManagedNoteWriter`, а после `--apply` повторно читает vault тем же scanner.
Filesystem-adapter отвечает за containment, linked-path protection, temporary
write, no-overwrite publication и receipt для rollback. Git, LLM, Search, API,
Telegram и worker остаются вне этой границы. Отдельный `CreateNoteProposal`
оркестрирует существующий `CreateManagedNote` через optional
`VersionControlPort` и `PullRequestPort`; их реализации изолированы в Git/`gh`
adapters и не добавляют зависимости к domain или обычному `note create`.

## Read-only и write-политика

Foundation не пишет в vault, не вызывает Git, сеть или LLM. Относительный путь
`SECOND_BRAIN_VAULT_PATH` разрешается относительно `config_root` выбранного
env-файла. Неявный fallback на process `cwd` запрещён.

Scanner не следует symlink/junction, проверяет resolved path containment и
пропускает linked entries с diagnostic. Inbox может временно содержать unmanaged
Markdown; частично заполненная schema считается ошибкой.

`note create` по умолчанию только формирует dry-run/diff. Только `--apply`
разрешает публикацию одного нового Markdown-файла. Existing target не заменяется:
публикация использует эксклюзивное создание, а не `os.replace`. После публикации
созданная note проходит post-write validation; при ошибке rollback удаляет файл
только при совпадении сохранённых identity и SHA-256.

## Будущие границы

Proposal automation изменяет только новую `automation/*` branch и создаёт PR с
`base=main`; прямой write/commit/push в `main`, force push и auto-merge запрещены.
Dry-run proposal не вызывает сеть и не меняет Git state. При ошибке PR после
push remote branch и commit сохраняются как partial result. Будущий внешний
адрес `brain.mikemoore.top` относится только к Web UI/API и не должен появиться
в domain/application или локальном CLI.

External research — отдельная read-only application boundary. В
`application.research` находятся `ResearchRequest`, normalized `ResearchSource` и
тонкий `ResearchGateway`, а `application.ports.ExternalResearchPort` определяет
единственную операцию `read`. Gateway принимает только public `http`/`https` для
`web`, `github`, `rss` и `youtube`, проверяет URL, лимиты, cancellation и
результат, после чего оставляет внешний `content` недоверенными данными в памяти.

Production adapters v1 реализуют два узких public read path. `SourceKind.WEB`
обслуживает `jina-reader` через фиксированный `https://r.jina.ai/`, а
`SourceKind.RSS` обслуживает `feedparser` через direct bounded `curl` к
исходному HTTP(S) host. Для RSS adapter самостоятельно выполняются DNS
resolution, проверка всех candidate IP на public/global policy и pinning
выбранного address через `--resolve`; unsafe или mixed result отклоняется до
curl. `ResearchGateway` остаётся первой validation boundary, а оба adapter-а
используют explicit argv, `shell=False`, отключённый `.curlrc`, отсутствие
redirect-following, cookies, auth и proxy credentials. stdout ограничен
`request.max_bytes`, stderr не входит в public DTO, timeout и cancellation
останавливают текущий процесс. RSS parser получает только bounded bytes и не
ходит по item links, enclosures или images. Отсутствующий `curl` отображается
как `RESEARCH_BACKEND_UNAVAILABLE`.

CLI `research read` только печатает normalized `ResearchSource` и не требует
vault, не пишет файлы, не вызывает Git или LLM. `content` внешнего источника
всегда остаётся недоверенным текстом; RSS/Atom HTML нормализуется в plain text
без исполнения scripts или загрузки внешних ресурсов. YouTube, GitHub,
authenticated sources, retries и persistence в этот этап не входят.
Agent Reach не импортируется и не запускается; полная SSRF-защита внешнего
сервиса Jina (включая его redirects, DNS rebinding и egress policy) остаётся
отдельным ограничением этого узкого public-web slice.

## Поиск

SQLite FTS5 планируется как будущий search v1. Его базовый `unicode61` не решает
русскую морфологию, а Porter stemmer предназначен для английского языка. Сравнение
нормализации, лемматизации, trigram и embeddings выполняется позднее на русском
query corpus.
