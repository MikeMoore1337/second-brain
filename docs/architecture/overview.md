# Обзор архитектуры

## Границы репозиториев

`second-brain` — программное ядро: domain/application, адаптеры, CLI, тесты,
автоматизация и документация. `second-brain-vault` — независимый приватный
репозиторий с пользовательскими Markdown/YAML-знаниями, вложениями, шаблонами и
безопасной частью конфигурации Obsidian.

## Web GUI draft review and save v1

Локальный Web GUI является entrypoint/adaptor поверх существующего application
core:

```text
Browser
   |
FastAPI web entrypoint
   |\
   | +-- Text -> LlmGateway -> NoteDraft + text review_token
   | +-- URL -> ResearchDraftGateway -> NoteDraft + SourceProvenance + research review_token
   | +-- Voice -> TranscriptionGateway -> TranscriptionPort -> Transcript
   | +-- Preview -> safe markdown-it-py renderer -> inert HTML
   `-- Save -> Prepare(dry-run diff) -> confirmation -> Apply -> existing Safe Write
```

`second-brain web serve` запускает packaged HTML/CSS/JavaScript shell только на
`127.0.0.1` (порт по умолчанию `8123`, опциональный ограниченный `--port`; `8000`
остаётся доступным только как явное переопределение, например для YFC).
`--env-file` и `--vault-path` передаются в app object, но lazy Save service не
загружает их до явной подготовки или применения Save. `create_app()` собирает только лёгкую
production composition: credentials, research process, LLM worker и vault не
читаются/не запускаются до соответствующего POST. `GET /`, `GET /healthz` и
Preview не вызывают vault или Safe Write; Prepare вызывает только Safe Write
dry-run и не меняет vault.

`POST /api/drafts/text` передаёт пользовательский `text` в
`LlmRequest.context` exact/unmodified и выполняет не более одного LLM draft;
`sources` всегда пуст. `POST /api/drafts/url` фиксирует `SourceKind.WEB`,
выполняет существующий `ResearchGateway(JinaReaderWebAdapter)` и затем один
`LlmGateway`, передавая только `ResearchSource.content` в LLM. Metadata source
преобразуется в bounded `SourceProvenance` рядом с draft; raw content, backend,
media type и provider details в browser не возвращаются.

HTTP boundary использует strict JSON request models с reject unknown fields,
safe error envelope и `Cache-Control: no-store`. Host allowlist ограничена
`127.0.0.1` и `localhost`; CSP разрешает только `connect-src 'self'`, а
JavaScript делает только same-origin fetch. Server-issued review token подписан
process-local HMAC-SHA256 secret и хранится только в JS memory; confirmation token
также stateless, хранится только в памяти UI и привязан к exact review token
context и ровно пяти полям edited `NoteDraft`. Research token
содержит только signed `SourceProvenance`, без raw content/backend/provider или
filesystem/Git metadata. Browser показывает provenance только для чтения.
Preview использует `markdown-it-py` с disabled raw HTML/linkify; links становятся
inert text spans, images — text placeholders, а code fence info не попадает в
HTML attributes. Единственный `innerHTML` находится в dedicated preview
container и получает только этот server-generated safe HTML.

Save не принимает отдельного client-controlled `sources` или client-controlled
`apply`. После проверки review token сервер собирает новый `NoteDraft` из пяти
editable fields. `POST
/api/drafts/save/prepare` вызывает существующий
`CreateManagedNoteFromDraft` для Text либо
`CreateManagedNoteFromReviewedResearchDraft` для URL с provenance из token с
`apply=False`, и возвращает только safe full-file unified diff и HMAC
confirmation token; этот этап не пишет. Только `POST /api/drafts/save/apply`
после проверки confirmation token вызывает тот же use case с `apply=True`.
Apply снова получает только signed provenance, не вызывает Research/LLM/network/Git
и возвращает только безопасные note fields; Prepare/Apply errors не раскрывают
absolute paths, receipts или raw diagnostics.

Для source-free Text review доступен отдельный Web projection Stage 1 Personal
Memory. Явный opt-in `Сохранить как Personal Memory` выключен по умолчанию;
Research/URL review token не проходит этот boundary. HTTP передаёт только
strict additive `personal_memory` с `evidence_kind`, `self_kind`, `evidence_at`,
`evidence_at_precision` и optional `domain`. Сервер сначала проверяет
`ReviewTokenMode.TEXT`, собирает `PersonalMemoryDraft` и вызывает существующую
Stage 1 validation, затем lazy composition
`CreateManagedNoteFromPersonalMemoryDraft` с `apply=False` или `apply=True`.
Prepare возвращает полный diff с application-owned marker и отдельный
Personal-Memory confirmation token. Этот token purpose/version отдельно от
generic Save и связывает исходный review token, пять полей `NoteDraft` и
нормализованные Personal Memory metadata. Search/Retrieval и
`second-brain-vault` этим projection не изменяются.

### Web Decision Journal v1

Локальный Web GUI имеет отдельную structured surface `Decision Journal` с
режимами `Decision` и `Outcome`. Этот flow не вызывает LLM и не переиспользует
review token Text/Research: пользовательский payload детерминированно
рендерится сервером в exact Stage 2 Markdown, затем проходит
`validate_decision_journal_draft` или `validate_outcome_observation_draft`.

Initial Decision DTO содержит только pre-choice поля; поздние result/reassessment
в нём структурно отсутствуют. `Outcome` создаётся отдельной note и принимает
только canonical UUIDv7 `decision_id`. Оба режима используют существующий
lazy Safe Write composition с dry-run full-file diff и apply только после
purpose-separated HMAC confirmation (`decision-journal-save-confirmation` или
`outcome-observation-save-confirmation`). Confirmation связывает exact
пяти-полевой `NoteDraft` и normalized Stage 2 time/domain/relation metadata;
body не помещается в token.

Новые routes остаются внутри local `draft-v1` JSON boundary с loopback
Host/same-origin Origin, raw body cap, strict `extra="forbid"`, no-store и без
OpenAPI. Outcome target повторно проверяется текущим vault scan и core на
prepare/apply; Journal не изменяется. Search API не расширен: Web лишь
показывает уже существующее безопасное поле `id` у hit/retrieved note.

Decision/Outcome state хранится только в памяти страницы; persistent browser
storage и новый frontend framework не используются. `schema_version` не меняется,
а `second-brain-vault` остаётся отдельным и нетронутым репозиторием.

### Web Personal Timeline v1

Локальный Web GUI предоставляет отдельную read-only surface `Personal Timeline`
через `POST /api/timeline` с purpose header `timeline-v1`. Это thin projection:
lazy service на каждый запрос загружает config, создаёт
`FileSystemVaultReader` и вызывает только existing
`BuildPersonalTimeline(PersonalTimelineRequest)`. Web не дублирует eligibility,
event time, evidence integrity, summary или ordering и не использует Search,
LLM, cache или persistent state.

Ответ сохраняет отдельные `known_items` и `unknown_items`: canonical event time
остаётся `event_at`, а неизвестное время передаётся literal `unknown`; storage
timestamps показываются только как отдельный audit detail. Browser рендерит
Timeline values через DOM `textContent`, не хранит response в persistent storage
и не сортирует items повторно. Initial load, смена порядка и `Обновить` каждый
раз запускают свежую on-demand сборку current vault; watcher/polling, pagination
и Stage 4 Self Model в этот slice не входят.

### Voice Capture v1

Voice mode принимает короткую запись через browser `MediaRecorder` либо локальный
audio file. Browser держит Blob/File только в памяти страницы и делает один
same-origin `POST /api/transcriptions/audio` с raw binary body, одним фиксированным
`X-Second-Brain-Request: voice-v1` header и allowlist media type:
`audio/webm`, `audio/ogg`, `audio/wav`, `audio/x-wav`, `audio/mpeg`, `audio/mp4`
или `audio/x-m4a`. Общий cap body — `15 MiB`; extra MIME parameters не
передаются, кроме optional `codecs=opus` для WebM/Ogg.

Application видит только provider-neutral `TranscriptionRequest(audio, media_type)`
и `Transcript(text)` через `TranscriptionGateway` и `TranscriptionPort`. Production
adapter делает ровно один binary `POST` на
`/client/v4/accounts/{account_id}/ai/run/@cf/openai/whisper`; provider metadata
не покидает adapter boundary, а credentials читаются только во время реальной
операции. Gateway и HTTP boundary используют bounded transcript validation и
стабильную safe error taxonomy.

Успешная расшифровка возвращается как `{"transcript": {"text": "..."}}` и
заполняет editable Text mode. Пользователь обязан проверить текст и отдельно
нажать `Создать черновик`; transcription не запускает LLM, review/save и не
пишет audio или transcript в vault. Cognitive Twin и Decision Journal в этот
вертикальный срез не входят.

Vault является каноническим источником истины. Индексы, SQLite, embeddings и
кэш относятся к производному состоянию и могут быть пересозданы из vault.

### Production application release и persistent vault sync

Application release использует immutable candidate worktree и atomic `current`
по [Web production runbook](../deployment/web-production.md). Persistent vault
остаётся отдельным sibling repository и обновляется только отдельной
[Vault Git Sync & Backup v1](../deployment/vault-sync.md) операцией: shared
lock, fetch exact target, clean relation check, recoverable backup, FF-only и
post-sync `vault validate`. Ни одна из операций не выполняет скрытый
двунаправленный merge; application deploy не заменяет vault, а vault sync не
перезапускает application service.

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
пропускает linked entries с diagnostic. Scanner diagnostics несут typed
`VaultRootRole`; root-owned failures называют ровно declared role, а overlap
использует explicit global provenance и bounded pair of involved roles. Nested
declared roots изолируются и сканируются только своим role, поэтому downstream
read models не восстанавливают availability по path spelling. Inbox может временно содержать unmanaged
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

Отдельный `CloudflareWorkersAiAdvisorPort` реализует provider-neutral
`AdvisorPort` для explicit-context-only Assistant v1. Он остаётся отдельным
high-level adapter: `LlmPort.draft_note` и `NoteDraft` не расширяются и не
используются как reasoning API. Advisor строит собственный exact request/result
contract, но переиспользует те же Cloudflare settings, fixed worker, private
framed IPC, sanitized child environment, TLS/HTTPS, bounded response и
terminate/kill cleanup. Через этот boundary проходит только canonical
`AssistantReasoningEnvelopeV1`; automatic Stage 5/Search/Self Model/Personal
Memory/vault reads и Simulate Me/Compare result отсутствуют. Result остаётся
ephemeral и после strict structural decoding проходит final semantic validation
существующего Assistant core.

## Search / Retrieval v1

Search является отдельным read-only слоем над уже существующей границей vault:

```text
FileSystemVaultReader.scan()
        -> VaultSnapshot
        -> build_report()
        -> ScanReport.notes
        -> SearchDocument projection
        -> SearchIndexPort
        -> ranked SearchHit
        -> RetrieveManagedNote(note_id)
        -> новый FileSystemVaultReader.scan()
        -> current RetrievedNote
```

`SearchIndexPort` принимает только provider-neutral `SearchDocument` и возвращает
`SearchHit` с stable `note_id`, note metadata и bounded plain-text `snippet`.
Search adapter не читает Markdown-файлы и не парсит их самостоятельно. В v1
`SqliteFts5SearchIndex` каждый раз создаёт только disposable SQLite `:memory:`
database: FTS5 table содержит searchable `title`, `tags`, `relative_path` и
`body`, а отдельная internal metadata table связывается по transient rowid.
Tokenizer — `unicode61 remove_diacritics 2`; ranking — BM25 с фиксированным
приоритетом `title > tags > relative_path > body` и Unicode-aware deterministic
tie-break. Raw BM25 score не входит в public DTO.

В projection попадают только `managed=True` notes с непустой стабильной
identity: `note_id`, `note_type` и `created`. Unmanaged Inbox без этой identity
пропускается. Duplicate UUID среди searchable notes даёт fail-closed
`SEARCH_IDENTITY_CONFLICT`; unrelated diagnostics вроде broken wikilink не
отключают поиск valid notes. Title выводится из basename relative Markdown path
без `.md`; arbitrary неизвестные front matter fields не индексируются.

User query ограничен по UTF-8 bytes и числу Unicode terms, не является raw FTS5
языком: операторные слова, кавычки, `*`, `NEAR`, `OR` и column-looking input
превращаются в безопасные quoted literals с deterministic `AND` semantics и
SQL parameters. `unicode61` поддерживает Unicode tokenization и case handling,
но не выполняет полноценную русскую морфологию или лемматизацию; разные
падежные/словообразовательные формы могут не совпасть.

`RetrieveManagedNote` никогда не читает body из SQLite. Он повторно запускает
canonical `VaultReader -> build_report` и возвращает ровно одну current
validated note по UUID; отсутствующий UUID даёт safe not-found, duplicate —
identity conflict. Web использует scoped same-origin `POST /api/search` и
`POST /api/retrieval/note` с `X-Second-Brain-Request: search-v1`, strict JSON,
loopback Host/Origin checks, raw body cap и `Cache-Control: no-store`. CLI
`second-brain search` остаётся read-only. Persistent DB/cache, file watcher,
incremental index, embeddings, vector search и RAG в этом Search/Retrieval
layer не реализуются; отдельный Stage 5 Self Retrieval status описан ниже.

## Personal Timeline v1 core

Stage 3 Personal Timeline реализован как application read model в
`application/timeline.py`. Каждый запрос заново проходит
`FileSystemVaultReader.scan() -> build_report()` и проектирует только текущие
валидные enrolled Personal Memory, Decision Journal и Outcome Observation
records. Legacy notes без exact marker, совпадающие по именам поля, Search hits,
LLM output и любые inferred claims в Timeline не входят.

Результат имеет отдельные группы `known_items` и `unknown_items`. Для known
`event_at` всегда равен canonical `evidence_at`, а для unknown сохраняется
literal `unknown`; `created` и `updated` возвращаются только как отдельное
storage/audit context и никогда не используются как event-time fallback. Known
сортируется по instant с deterministic path/UUID tie-break, unknown — только по
relative path и UUID, без использования UUIDv7 или storage timestamps.

Timeline fail-closed при ошибках целостности enrolled evidence и при
incompleteness canonical content scan; он не возвращает частичный personal
history. Read/root/directory/resolve failures блокируют только по typed
manifest content roots, тогда как template/attachment-only root failures и
unrelated diagnostics не блокируют projection. Timeline не имеет `status`,
persistence, cache, watcher, cursor или write capability: `generated_at` —
только injectable application clock в in-memory result. `schema_version` не
меняется, а `second-brain-vault` остаётся отдельным нетронутым репозиторием.

## Self Model v1 design contract

Stage 4 Self Model core и local Web projection уже merged после issue #81.
Точный application DTO, canonical/derived boundary, evidence eligibility,
owner-approved direct-assertion policy, fail-closed behavior и privacy
constraints находятся в [self-model-v1-contract.md](../cognitive-twin/self-model-v1-contract.md).
В репозитории нет persistent profile или inference write-back: Self Model
остаётся rebuildable read model.

Design-only roadmap будущего Personal Cognitive Twin:
[design-roadmap-v1.md](../cognitive-twin/design-roadmap-v1.md).

## Self Retrieval v1 design contract

Stage 5 Self Retrieval реализован в current `main`: core из #89, thin local
Web projection из #90 и CLI projection из #91 уже merged. Нормативный
approved contract из issue #88 находится в
[self-retrieval-v1-contract.md](../cognitive-twin/self-retrieval-v1-contract.md).
Он добавляет только bounded application composition над existing lexical
Search, `SearchHit` как candidate и current UUID reread через
`RetrieveManagedNote`; `SearchDocument`, `SearchHit`, `SearchIndexPort` и
canonical vault semantics не изменяются.

Final context item всегда строится из current `RetrievedNote`. Search snippet,
ordinal rank и disposable index не являются evidence truth. Exact Self Model
claim link допустим только через current supporting UUID и validated policy
fingerprint. Stage 5 не выбирает relevance/confidence semantics, не вводит
embeddings/RAG/provider, persistence/cache, prediction mode или write-back.
Эти три merged slices остаются отдельными слоями и не меняют canonical vault,
Search DTO или provider boundary.

## Simulate Me v1 local Web projection

Stage 6 Simulate Me v1 core из #101 и local Web projection из #102 остаются
разделёнными слоями. Web принимает только exact `query` и caller-owned
`options`, передаёт immutable request в `BuildSimulateMe` и сериализует только
его prediction/abstention result, evidence UUID refs и temporal caveats. Web не
добавляет matching, ranking, client inference или новый result field; UI
показывает результат как **ПРОГНОЗ**, а abstention — как недостаток evidence.

`POST /api/simulate-me` использует loopback/same-origin JSON boundary,
`X-Second-Brain-Request: simulate-me-v1`, bounded raw body, `Cache-Control:
no-store` и скрытую OpenAPI schema. Запросы lazy: current vault читается только
по явному submit; browser storage, polling, LLM/provider, cache и write path в
этом slice отсутствуют. `second-brain-vault` и canonical schema не изменяются.

## Simulate Me deterministic evaluation harness v1

Для Stage 6 добавлен on-demand evaluation harness из #103. Он использует
только versioned synthetic cases и temporary managed vault: direct
preference/goal prediction, bounded abstention categories, contextual belief,
Decision Journal/search-only non-inference, unknown-time caveat, NFC/edge-trim
matching и malformed current context. Report фиксирует actual evidence refs,
result category, derivation/policy identity и точный mismatch reason без
временных путей и note bodies. Corpus также фиксирует case-only,
internal-whitespace и prefix near-match abstention и asymmetric count/recency
conflict; Markdown и JSON сохраняют captured refs и caveats.

Harness не читает configured vault, не вызывает provider/network/LLM, не пишет
canonical notes и не вводит calibration, confidence threshold, quality SLO или
автоматическое изменение prediction policy. Полный контракт запускается как
uv run python -m second_brain.benchmarks.simulate_me_evaluation_v1.
