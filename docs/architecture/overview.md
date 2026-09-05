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

### Reviewed NoteDraft -> Safe Write

Связка networked draft generation с записью в vault намеренно остаётся
двухшаговой:

```text
networked `llm draft` / `research draft`
              |
       reviewed NoteDraft JSON
              |
offline `note create-from-draft --file PATH`
              |
       dry-run по умолчанию
              |
          explicit `--apply`
              |
       существующий Safe Write
```

`note create-from-draft` сначала bounded строго декодирует UTF-8 JSON с ровно
пятью полями `title`, `note_type`, `content`, `tags` и `links`, затем переиспользует
semantic validation `NoteDraft`. Команда не вызывает research/LLM, сеть или
Git/GitHub и не требует Cloudflare credentials. Без `--apply` vault не меняется;
JSON/text preview показывает конечный relative path и полностью rendered
Markdown. При `--apply` используется тот же preflight, containment,
symlink/junction protection, temporary file, no-overwrite publication,
post-write validation и receipt-based rollback, что и обычный `note create`.

Для draft-based создания `title` проходит существующую safe filename policy,
`id`/`created` генерируются application, `type` берётся из `note_type`, а
`content` становится body без semantic rewrite и без body template. `tags` и
`links` сохраняются как application-managed YAML lists в front matter в исходном
порядке; `links` остаётся candidate metadata и не запускает resolution или
backlink creation. Неизвестные поля front matter template сохраняются round-trip;
schema version не меняется.

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

Production adapters v1 реализуют четыре узких public read path. `SourceKind.WEB`
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

`SourceKind.YOUTUBE` обслуживает только один public video через внешний
operator-managed executable `yt-dlp`: сначала bounded `--dump-single-json`
metadata, затем одна выбранная public VTT caption track. Manual subtitles
предпочитаются automatic captions; язык выбирается детерминированно как
`ru`, `en`, затем lexical fallback, а `live_chat` исключается. Оба вызова
используют `--ignore-config`, `--no-playlist`, `--skip-download`, закрытый
argv и безопасное окружение без cookies, netrc, auth и proxy; video/audio,
thumbnails, comments и Whisper/STT не скачиваются. Caption file живёт только
в `TemporaryDirectory`, проверяется на containment/symlink/reparse/size и
удаляется после read. Общий deadline покрывает metadata, выбор, получение и
нормализацию caption. Отсутствующий `yt-dlp` отображается как
`RESEARCH_BACKEND_UNAVAILABLE`, а отсутствие public captions или ошибка одного
extraction request — как `RESEARCH_UPSTREAM_FAILURE`; внутренние requests
yt-dlp не pin-ятся нашим adapter-ом, поэтому DNS rebinding внутри trusted
runtime остаётся ограничением.

`SourceKind.GITHUB` обслуживает только URL корня public repository через
фиксированный `https://api.github.com`: сначала metadata, затем raw README.
Один read выполняет не более двух serial unauthenticated GET; `README 404`
даёт metadata-only result. Repository identity сравнивается с input без учёта
ASCII-регистра, а canonical casing сохраняется в normalized source. Private,
Enterprise, `gh`, redirects, retries и browsing других GitHub ресурсов не
поддерживаются.

CLI `research read` только печатает normalized `ResearchSource` и не требует
vault, не пишет файлы, не вызывает Git или LLM. `content` внешнего источника
всегда остаётся недоверенным текстом; RSS/Atom HTML нормализуется в plain text
без исполнения scripts или загрузки внешних ресурсов. YouTube transcript также
остаётся untrusted plain text; authenticated sources, retries и persistence в
этот этап не входят.
Agent Reach не импортируется и не запускается; полная SSRF-защита внешнего
сервиса Jina (включая его redirects, DNS rebinding и egress policy) остаётся
отдельным ограничением этого узкого public-web slice.

### LLM application boundary v1

После public research v1 добавлена отдельная provider-neutral граница для
structured note draft:

```text
networked read-only `llm draft` CLI
      |
  LlmGateway
      |
    LlmPort
      |
Cloudflare Workers AI adapter
      |
validated NoteDraft
```

`application.llm` содержит bounded `LlmRequest`, semantic `NoteDraft` и тонкий
`LlmGateway`; `application.ports.LlmPort` определяет единственную операцию
`draft_note`. `LlmRequest.context` и результат LLM считаются недоверенными
данными (включая переданный будущим caller-ом `ResearchSource.content`): текст
не интерпретируется как instruction, не исполняется и не может сам вызвать
side effect. Gateway применяет deterministic cancellation boundary и bounded
validation, а ошибки наружу сводит к стабильным кодам `LLM_INVALID_REQUEST`,
`LLM_CANCELLED`, `LLM_TIMEOUT`, `LLM_BACKEND_UNAVAILABLE`,
`LLM_UPSTREAM_FAILURE`, `LLM_MALFORMED_RESULT` и `LLM_CONTENT_TOO_LARGE` без
provider details. Gateway не вызывает `ResearchGateway`,
`ExternalResearchPort`, `VaultReader`, `ManagedNoteWriter`, filesystem, Git или
сеть, не читает provider credentials, не выбирает model и не делает retry или
fallback. В v1 этот контракт используется только networked read-only командами
`llm draft` и `research draft`;
streaming, chat, tools, embeddings и RAG отсутствуют.

`NoteDraft` содержит только `title`, `note_type`, `content`, `tags` и `links`.
Он не является `CreateManagedNoteRequest`: обычный `CreateManagedNote` по-прежнему
сам определяет UUIDv7, timestamp, path и write plan. Связка `research -> LLM ->
approval -> Safe Write` реализуется отдельной offline-командой
`note create-from-draft`: reviewed JSON является границей approval, а
networked draft-команды остаются без write capability.

### Bounded research -> LLM orchestration v1

Для одного networked read-only invocation добавлена отдельная application
граница, не зависящая от конкретных research или LLM adapters:

```text
research draft CLI
       |
ResearchDraftGateway
       |
ResearchGateway -> ResearchSource.content -> LlmGateway
       |                                      |
ExternalResearchPort                         LlmPort
                                              |
                                      validated NoteDraft
```

`ResearchDraftRequest` содержит `source_kind`, `uri`, пользовательскую
`instruction`, `research_timeout_seconds`, `max_source_bytes` и
`max_output_bytes`. `ResearchDraftGateway` проверяет, что
`max_source_bytes <= MAX_CONTEXT_BYTES`, затем выполняет максимум один
`ResearchGateway.read`. После успешного чтения он передаёт только
`ResearchSource.content` напрямую в `LlmRequest.context`, а пользовательскую
`instruction` — отдельным полем без изменений. Source title, URI, author и
backend не добавляются в LLM request; content остаётся untrusted context и не
становится instruction.

После research cancellation проверяется до вызова `LlmGateway`, поэтому один
запуск делает максимум одну research operation, затем максимум одну LLM draft
operation. Research error не запускает LLM, LLM error не повторяет research;
retry, fallback, tools, function execution, chunking и map-reduce отсутствуют.
После успешного чтения metadata источника преобразуется в provider-neutral
`SourceProvenance` и возвращается рядом с draft во внутреннем
`ResearchDraftResult`. В `LlmRequest` по-прежнему передаётся только
`ResearchSource.content` exact/unmodified; provenance не входит в `NoteDraft`,
instruction или context и не может быть изменена LLM. Команда возвращает только
пять semantic полей `NoteDraft` и не вызывает vault, Git или Safe Write.

Для будущего Web GUI reviewed boundary представлена immutable DTO
`ReviewedResearchDraft` с `draft: NoteDraft` и tuple `sources` из
`SourceProvenance`. В v1 принимается ровно один source, но при research-derived Safe Write
он сохраняется как YAML list `sources`. Этот use case переиспользует существующие
preflight, containment, dry-run/apply, no-overwrite, post-write validation и
receipt-based rollback; URL источника не добавляется в body или `links`, а запись
не выполняет network.

### Cloudflare Workers AI adapter v1

Первый production LLM adapter реализует `CloudflareWorkersAiLlmPort` с
фиксированными `cloudflare-workers-ai`, `@cf/zai-org/glm-4.7-flash` и
`api.cloudflare.com`. Parent process строит bounded canonical request и передаёт
его вместе с runtime secret через один private framed pipe в одноразовый worker.
Worker запускается с explicit sanitized environment, создаёт verified TLS
соединение через Python stdlib и делает ровно один `POST` на fixed
OpenAI-compatible endpoint; retry, fallback, redirects, proxy override,
streaming, history и tools отсутствуют. Token не попадает в argv, child env,
URL, disk, logs или public error/DTO.

Worker возвращает только bounded internal result. Adapter принимает только
exact `200`, `finish_reason=stop` и строгое JSON-сообщение с пятью полями
`NoteDraft`, после чего результат остаётся под final validation существующего
`LlmGateway`. Request/response transport caps derived из application limits и
учитывают обе JSON layers; adapter не задаёт `max_completion_tokens` без
документированного model-specific cap и не переводит bytes в неподтверждённое
число tokens. Поэтому provider-side completion limit, если он срабатывает и
возвращает не `finish_reason=stop`, является явным ограничением adapter v1 и
даёт `LLM_MALFORMED_RESULT`; application byte limits не меняются. Cancellation
и общий 30-секундный deadline останавливают текущий worker через terminate/kill
с deterministic cleanup.

Adapter подключён только к networked read-only CLI-командам `llm draft` и
`research draft`; он не подключён к Safe Write, vault, Git, Telegram или
production deployment workflow. Обычные vault/research read commands не
требуют Cloudflare settings; credentials читаются только при явном создании и
вызове adapter. Существующее provider-specific context framing не меняется:
orchestration передаёт source content в `LlmRequest.context`, а framing
остаётся внутри Cloudflare adapter.

## Поиск

SQLite FTS5 планируется как будущий search v1. Его базовый `unicode61` не решает
русскую морфологию, а Porter stemmer предназначен для английского языка. Сравнение
нормализации, лемматизации, trigram и embeddings выполняется позднее на русском
query corpus.
