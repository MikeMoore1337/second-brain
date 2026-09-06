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

## Тесты и границы изоляции

Обычный запуск тестов полностью локальный и не должен читать или изменять
реальный vault, обращаться к сети или запускать network-capable provider
executables. Vault fixtures создаются только под pytest `tmp_path` через
`create_vault`; унаследованный `SECOND_BRAIN_VAULT_PATH` отклоняется с
детерминированной ошибкой. Network/provider seams должны быть fake-объектами;
локальные процессы для тестов разрешены, а проверка `curl --version` сохраняет
Windows SSL regression contract.

```powershell
uv run pytest
uv run ruff format --check .
uv run ruff check .
uv run mypy src tests
```

Отдельные live smoke-тесты обязаны иметь marker `@pytest.mark.live_smoke` и не
входят в обычный CI. Их можно запускать только отдельной явной командой после
осознанного разрешения сети и provider credentials:

```powershell
$env:SECOND_BRAIN_ALLOW_LIVE_SMOKE = "1"
uv run pytest -m live_smoke
```

## Локальный Web GUI

Web GUI v1 — это небольшой packaged browser shell поверх существующих application
gateways. Shell и `GET /healthz` по-прежнему создаются без конфигурации vault,
Cloudflare credentials, сети или записи и по умолчанию доступны только локально:

```powershell
uv run second-brain web serve
uv run second-brain web serve --port 8123
```

Команда слушает только `127.0.0.1`; `--port` принимает значение от `1` до
`65535`, а default — `8000`. В GUI есть Add modes `URL`, `Text` и `Voice`.
`URL` и `Text` через same-origin API создают `NoteDraft`; URL дополнительно
показывает bounded `SourceProvenance` рядом с draft. Voice принимает запись
через native `MediaRecorder` или локальный audio file, отправляет один raw
binary запрос на transcription и возвращает только plain transcript. После
генерации пользователь редактирует
только пять semantic fields, может запросить безопасный Markdown preview и
отдельно подготовить Safe Write dry-run, проверить полный diff и подтвердить
сохранение в vault. `CLOUDFLARE_ACCOUNT_ID` и
`CLOUDFLARE_API_TOKEN` нужны только для transcription и генерации draft;
аудио поддерживает `audio/webm`, `audio/ogg`, `audio/wav`, `audio/x-wav`,
`audio/mpeg`, `audio/mp4` и `audio/x-m4a`, максимум `15 MiB`. Audio Blob/File
живёт только в памяти страницы. После transcription текст попадает в
редактируемый Text mode с обязательным сообщением о проверке; LLM вызывается
только после отдельного нажатия `Создать черновик`. Vault-конфигурация
(`--env-file`/`--vault-path`) загружается только при подготовке или применении
явного Save. Browser не
выбирает path, identity, timestamp, apply или Git metadata и не сохраняет
review state в persistent browser storage. Authentication, public bind, deploy
и PWA в этот этап не входят. Для source-free Text draft (включая уже
проверенный Voice transcript) доступен отдельный явный режим `Сохранить как
Personal Memory`, выключенный по умолчанию. Пользователь вручную выбирает
только Stage 1 `evidence_kind`/`self_kind`, optional `domain` и `exact` либо
`unknown` время; LLM не классифицирует эти поля. Research/URL draft этот режим
не получает. Personal Memory проходит отдельные prepare/apply endpoints,
полный diff и отдельное подтверждение перед существующим Safe Write.

В shell доступна отдельная Search-поверхность: она ищет только managed notes со
стабильным UUID, показывает ranked lexical hits и открывает read-only текущую
версию заметки. Search/Retrieval лениво загружают vault configuration только при
соответствующем запросе; index каждый раз пересобирается в disposable SQLite
FTS5 `:memory:` и не является source of truth. Полное содержимое заметки для
`Открыть` повторно читается через существующий vault scan. Search не вызывает
network, LLM или Safe Write и не использует browser storage.

API surface:

```text
POST /api/drafts/text {"text":"..."}
POST /api/drafts/url  {"url":"https://example.com/article"}
POST /api/transcriptions/audio <raw audio bytes>
POST /api/drafts/preview {"content":"..."}
POST /api/drafts/save/prepare {"review_token":"...", "draft": {"title", "note_type", "content", "tags", "links"}}
POST /api/drafts/save/apply {"review_token":"...", "confirmation_token":"...", "draft": {"title", "note_type", "content", "tags", "links"}}
POST /api/drafts/personal-memory/save/prepare {"review_token":"...", "draft": {"title", "note_type", "content", "tags", "links"}, "personal_memory": {"evidence_kind", "self_kind", "evidence_at", "evidence_at_precision", "domain"}}
POST /api/drafts/personal-memory/save/apply {"review_token":"...", "confirmation_token":"...", "draft": {"title", "note_type", "content", "tags", "links"}, "personal_memory": {"evidence_kind", "self_kind", "evidence_at", "evidence_at_precision", "domain"}}
POST /api/search {"query":"fastapi testing", "limit":20}
POST /api/retrieval/note {"id":"UUIDv7"}
```

CLI Search доступен без network и записи в vault:

```powershell
uv run second-brain --env-file .env search "fastapi testing" --limit 20
uv run second-brain --env-file .env search "тестирование" --format json
```

Synthetic lexical-gap benchmark v1 запускается отдельно и не читает real
vault/config:

```powershell
uv run python -m second_brain.benchmarks.lexical_gap_v1
uv run python -m second_brain.benchmarks.lexical_gap_v1 --format json
```

Private Search/Retrieval API принимает только same-origin loopback `POST` с
`X-Second-Brain-Request: search-v1` и `Content-Type: application/json`; для
успешных и ошибочных ответов используется `Cache-Control: no-store`. Query —
обычный bounded literal text с deterministic `AND` semantics, а не raw FTS5
syntax. SQLite FTS5 использует `unicode61 remove_diacritics 2` и BM25 с
приоритетом `title > tags > relative_path > body`; Russian morphology и
lemmatization не обещаются, поэтому разные падежные формы могут не совпасть.
Persistent SQLite/cache, embeddings, vector search и RAG отложены.

Ответы генерации имеют форму `{"draft": {"title", "note_type", "content", "tags", "links"},
"sources": [...], "review_token":"..."}`. Preview возвращает
только server-rendered safe HTML для одного dedicated container. Prepare
возвращает только `status: "dry-run"`, тип/relative path, полный unified diff
предлагаемого Markdown-файла и короткий process-local HMAC confirmation token;
этот этап не пишет в vault. Apply принимает тот же review token, confirmation
token и ровно пять полей draft и только затем запускает существующий Safe Write
с `apply=True`. ID и `created` из dry-run могут быть сгенерированы заново при
apply. У Save нет отдельного top-level `sources` или client-supplied provenance;
research sources появляются только внутри signed full-file diff. Абсолютные
пути, receipt и Git metadata в request/response запрещены. API принимает только
строгий JSON, все draft routes помечены
`Cache-Control: no-store`, а browser общается только с same-origin `/api/...`.
Ошибки не раскрывают provider/upstream или filesystem details.

Personal Memory endpoints принимают additive `personal_memory` только из пяти
строгих полей Stage 1 и никогда не принимают marker
`second_brain_personal_memory`, front matter, path, UUID, `created` или произвольную
metadata mapping. Сервер принимает только `ReviewTokenMode.TEXT`, поэтому
research token отклоняется до вызова vault composition. Personal Memory
confirmation token имеет отдельные purpose/version и связывает digest исходного
review token, всех пяти полей `NoteDraft` и нормализованных Personal Memory
metadata; generic confirmation token для него не подходит. Prepare не пишет,
а diff включает application-owned marker и проверенные metadata.

## Public web, RSS, YouTube и GitHub research

Read-only команда `research read` читает одну публичную web-страницу через
фиксированный Jina Reader или один публичный RSS/Atom feed напрямую через
системный `curl`, а для одного публичного YouTube video получает metadata и
одну существующую public caption track через внешний `yt-dlp`. Для RSS adapter
перед запросом выполняются public DNS/IP validation и pinning; vault для этого
не требуется. GitHub v1 читает только корень public repository через fixed
`api.github.com`: metadata и raw README, максимум два serial GET, без auth.
Команда ничего не пишет, не вызывает Git, LLM или Agent Reach:

```powershell
uv run second-brain research read `
  --type web --url "https://example.com/article"
uv run second-brain research read `
  --type rss --url "https://example.com/feed.xml"
uv run second-brain research read `
  --type youtube --url "https://www.youtube.com/watch?v=<video-id>"
uv run second-brain research read `
  --type github --url "https://github.com/owner/repo"
```

`curl` — внешний optional runtime prerequisite для этой команды и не
устанавливается приложением; Python dependency `feedparser` устанавливается
из locked `uv.lock`. RSS/Atom parsing получает только уже загруженные bounded
bytes и не загружает item links/enclosures. Для YouTube `yt-dlp` также должен
быть заранее установлен оператором как внешний executable: приложение его не
устанавливает и не обновляет. YouTube read использует только `--skip-download`
и одну VTT caption track; media download, Whisper/STT, playlists, channels,
search и live smoke не входят. GitHub принимает только URL вида
`https://github.com/{owner}/{repo}`; private/authenticated repositories,
issues, PRs, search и другие GitHub endpoints не входят.

## LLM draft

Networked read-only команда `llm draft` вызывает существующий Cloudflare
Workers AI adapter через `LlmGateway` и показывает один проверенный semantic
`NoteDraft`.
Она не читает и не изменяет vault, не вызывает Git, research или Safe Write;
отдельная команда `research draft` добавляет bounded workflow
`research -> LLM` без записи.

`research draft` выполняет networked read-only операцию: максимум один
research read, затем максимум один LLM draft. Только `ResearchSource.content`
передаётся во внешний LLM provider как untrusted `LlmRequest.context`, без
добавления metadata, prompt framing или скрытой инструкции со стороны
orchestration. Лимит `--max-source-bytes` по умолчанию равен
`MAX_CONTEXT_BYTES`; значение выше этого cap отклоняется до research call.

```powershell
uv run second-brain research draft `
  --type web --url "https://example.com/article" `
  --instruction "Сделай краткий структурированный черновик заметки" `
  --format json
```

Команда не читает конфигурацию vault, не пишет в vault или Git, не выполняет
retry, fallback, tools или function calls. JSON и text используют тот же
semantic renderer, что и `llm draft`.

Credentials берутся только из process environment и не передаются CLI options:

```powershell
$env:CLOUDFLARE_ACCOUNT_ID = "..."
$env:CLOUDFLARE_API_TOKEN = "..."
uv run second-brain llm draft --instruction "Создай заметку о резервных копиях"
```

Поддерживаются options `--instruction`, `--context`,
`--max-output-bytes` и `--format text|json`. Команда только отображает draft;
автоматического сохранения, retry, fallback и tools нет. JSON содержит ровно
`title`, `note_type`, `content`, `tags` и `links`.

## Reviewed NoteDraft -> Safe Write

Networked `llm draft` и `research draft` только возвращают JSON-драфт и не имеют
`--apply`. Пользователь сначала сохраняет и проверяет файл, затем запускает
отдельную local/offline-команду:

```powershell
uv run second-brain note create-from-draft `
  --file draft.json
uv run second-brain note create-from-draft `
  --file draft.json --apply
```

У команды ровно три options: `--file PATH`, `--apply` и `--format text|json`;
`--apply` по умолчанию выключен. Dry-run не меняет vault, Git или другие файлы
и показывает конечный relative path и rendered Markdown. Команда не вызывает
research/LLM, network или Git/GitHub и не требует Cloudflare credentials.

Маппинг reviewed draft lossless: `title` используется только существующей safe
filename policy; application генерирует UUIDv7, `created` с явным offset и
выбирает root по manifest; `note_type` становится `type`; `content` становится
body без semantic rewrite и без template body; `tags` и `links` сохраняются как
YAML front matter lists в исходном порядке. `links` не разрешаются и не
превращаются в backlinks. При явном `--apply` используется полный Safe Write
pipeline с containment, symlink/junction checks, temporary file, no-overwrite,
post-write validation и receipt-based rollback.

## VPS runtime

Первичный user-level bootstrap и безопасное обновление Linux layout описаны в
[runbook VPS runtime](docs/deployment/vps.md). Реальный SSH/deploy, web/API,
systemd и reverse proxy в текущий этап не входят.

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

## Git proposal workflow

Отдельная команда `proposal note create` превращает создание новой managed note
в reviewable proposal. По умолчанию это dry-run: выполняются только локальные
read-only Git preflight и обычный Safe Write plan; branch, vault, index, refs,
commit, push, `gh` и сеть не изменяются и не вызываются:

```powershell
uv run second-brain --env-file .env proposal note create `
  --type project --title "Мой проект"
```

Для apply нужны Git worktree vault, чистый и синхронизированный `main`,
настроенный `origin`, установленный `git` и авторизованный GitHub CLI (`gh
auth status`). После явного `--apply` workflow создаёт только новую
`automation/*` branch, публикует одну новую note, stage/commit делает только
для её exact path, выполняет non-force push и создаёт PR
`automation/* -> main`:

```powershell
uv run second-brain --env-file .env proposal note create `
  --type project --title "Мой проект" --apply
```

Workflow не выполняет pull, rebase, merge, reset, force push или auto-merge.
Если PR не удалось создать после успешного push, результат имеет status
`partial`: remote branch и commit сохраняются, а PR можно создать отдельно.
Ошибки до commit откатывают note только через её Safe Write receipt, когда это
безопасно; полезный commit автоматически не удаляется.

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
Для `llm draft` код `0` означает успешный показ draft, `1` — безопасная
ошибка `LlmError`, `2` — неожиданная локальная runtime-ошибка.

## Архитектура

См. [обзор архитектуры](docs/architecture/overview.md),
[vault contract v1](docs/architecture/vault-contract-v1.md),
[Self Model v1 design contract](docs/cognitive-twin/self-model-v1-contract.md)
и [Self Retrieval v1 design contract](docs/cognitive-twin/self-retrieval-v1-contract.md),
а также [Cognitive Twin roadmap](docs/cognitive-twin/design-roadmap-v1.md).

## Лицензия

Проект распространяется по лицензии Apache-2.0. Полный текст находится в
`LICENSE` и сохраняется на языке оригинала.
