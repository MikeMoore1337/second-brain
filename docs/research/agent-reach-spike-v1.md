# Agent Reach technical spike v1: `ExternalResearchPort` и безопасная интеграционная граница

Статус: завершённый technical/design spike; Agent Reach production adapter не
реализуется.

Дата проверки: 2026-09-02 (Europe/Moscow).

Связанные задачи: [issue #11](https://github.com/MikeMoore1337/second-brain/issues/11),
[roadmap issue #2](https://github.com/MikeMoore1337/second-brain/issues/2) и
[architecture issue #1](https://github.com/MikeMoore1337/second-brain/issues/1).

## Краткий итог

**Verdict: GO WITH LIMITATIONS.**

Разрешён следующий узкий шаг: зафиксировать Second Brain-native контракт
`ExternalResearchPort`/`ResearchGateway` и его детерминированные тесты. Это
совместимо с roadmap и текущей архитектурой: research остаётся внешним
read-only capability, а `domain`, vault и Safe Write Operations не меняются.

Не разрешено этим verdict:

- добавлять Agent Reach в основной `pyproject.toml` или `uv.lock`;
- импортировать пакет Agent Reach в runtime Second Brain;
- реализовывать production Agent Reach adapter;
- принимать cookies, browser sessions, tokens или иные authenticated sources;
- давать research layer путь к vault, `VaultReader`, `ManagedNoteWriter` или
  любой другой write-capability;
- делать live-network тесты обязательными для CI.

Иными словами, **GO** относится к контракту и безопасной границе, а не к
подключению Agent Reach как библиотеки. Для production data retrieval сначала
нужен отдельный process-based adapter и отдельное security/operations решение.

Первые узкие implementation slices реализуют public `WEB` через фиксированный
Jina Reader и public RSS/Atom через отдельный direct adapter Second Brain с
feedparser, adapter-level DNS/IP validation и `--resolve` pinning. Следующий
узкий YouTube slice использует тот же прямой process-boundary подход: внешний
operator-managed `yt-dlp` получает bounded metadata и одну public caption track;
Agent Reach integration для этого не требуется. Пакет, CLI, config, cookies и
MCP Agent Reach не используются; GitHub остаётся будущим adapter issue.

## Контекст и baseline

Spike выполнен только в `MikeMoore1337/second-brain`. Соседний
`second-brain-vault` не читался и не изменялся.

Исходной точкой является актуальная `main`:

- `b414cdbe59a17ab146cb75f69e9392fe03c34280` — VPS Runtime Bootstrap v1;
- ограничения репозитория и язык документации заданы в [`AGENTS.md`](../../AGENTS.md);
- текущие границы слоёв и будущий research candidate описаны в
  [`docs/architecture/overview.md`](../architecture/overview.md);
- правила независимости и безопасной записи описаны в
  [`docs/architecture/vault-contract-v1.md`](../architecture/vault-contract-v1.md);
- VPS layout сохраняет `second-brain` и `second-brain-vault` независимыми и
  прямо оставляет Agent Reach за пределами runtime bootstrap:
  [`docs/deployment/vps.md`](../deployment/vps.md).

Это не реализация research workflow и не изменение vault contract. Цель spike —
проверить, что именно предоставляет upstream, какие данные можно безопасно
принять внутрь Second Brain и где должна остановиться интеграционная граница.

## Точная upstream-точка

Upstream проверен через live `git ls-remote`, затем ровно этот `main` был
клонирован в disposable directory и установлен в отдельный Python 3.14.6 venv.

| Поле | Зафиксированное значение |
| --- | --- |
| Репозиторий | [`Panniantong/Agent-Reach`](https://github.com/Panniantong/Agent-Reach) |
| Проверенная ветка | `main` |
| Exact commit | `da5044d26fc6adddb6554d5679c94ac22e76e428` |
| Commit date | `2026-09-01T16:09:56+08:00` |
| Package version в этом commit | `1.5.0` |
| Tag на проверенном HEAD | нет; `main` не указывает непосредственно на tag |
| Последний опубликованный tag | `v1.5.0` → `f65526cbaaad3879473acc1ba6dbefd195caf2be` |
| Дата commit последнего tag | `2026-06-11T20:23:00+08:00` |

Первичные ссылки на зафиксированный код:

- [`pyproject.toml` на exact commit](https://github.com/Panniantong/Agent-Reach/blob/da5044d26fc6adddb6554d5679c94ac22e76e428/pyproject.toml);
- [`agent_reach/cli.py`](https://github.com/Panniantong/Agent-Reach/blob/da5044d26fc6adddb6554d5679c94ac22e76e428/agent_reach/cli.py);
- [`agent_reach/core.py`](https://github.com/Panniantong/Agent-Reach/blob/da5044d26fc6adddb6554d5679c94ac22e76e428/agent_reach/core.py);
- [`agent_reach/channels/web.py`](https://github.com/Panniantong/Agent-Reach/blob/da5044d26fc6adddb6554d5679c94ac22e76e428/agent_reach/channels/web.py);
- [`agent_reach/channels/github.py`](https://github.com/Panniantong/Agent-Reach/blob/da5044d26fc6adddb6554d5679c94ac22e76e428/agent_reach/channels/github.py);
- [`agent_reach/channels/youtube.py`](https://github.com/Panniantong/Agent-Reach/blob/da5044d26fc6adddb6554d5679c94ac22e76e428/agent_reach/channels/youtube.py);
- [`agent_reach/integrations/mcp_server.py`](https://github.com/Panniantong/Agent-Reach/blob/da5044d26fc6adddb6554d5679c94ac22e76e428/agent_reach/integrations/mcp_server.py);
- [`agent_reach/skill/SKILL.md`](https://github.com/Panniantong/Agent-Reach/blob/da5044d26fc6adddb6554d5679c94ac22e76e428/agent_reach/skill/SKILL.md).

## Методика и ограничения эксперимента

Экспериментальная среда была отдельной от репозитория Second Brain:

1. upstream clone находился в `%TEMP%`;
2. Agent Reach был установлен только editable-установкой в отдельный venv;
3. для процессов были заданы disposable `HOME`, `USERPROFILE`, `APPDATA`,
   `XDG_CONFIG_HOME` и `GH_CONFIG_DIR`;
4. `yt-dlp` запускался с `--ignore-config` и `--no-cache-dir`;
5. не использовались `agent-reach install --system`, `configure`, реальные
   cookies, browser sessions, личные аккаунты или credentials;
6. проверялись только публичные URL и публичный репозиторий;
7. результаты и временные subtitle-файлы не добавлялись в Second Brain.

Вызовы выполнялись как argv-процессы с разделёнными stdout/stderr. В исходнике
Agent Reach не найдено явного `shell=True`; при этом будущий adapter должен
задавать `shell=False` явно и никогда не собирать командную строку
конкатенацией.

## Что реально является Agent Reach API

### CLI и Python/MCP surface

| Surface | Наблюдаемое поведение | Вывод для Second Brain |
| --- | --- | --- |
| `pyproject.toml` | Console script `agent-reach = agent_reach.cli:main`; обычные зависимости — `requests`, `feedparser`, `python-dotenv`, `loguru`, `pyyaml`, `rich`, `yt-dlp[default]`. | Это отдельный installable package с широким dependency surface, но не dependency Second Brain. |
| `agent_reach.cli:main` | Команды: `setup`, `install`, `configure`, `doctor`, `uninstall`, `skill`, `format`, `transcribe`, `check-update`, `watch`, `version`. Команды `research`, `read` или `fetch` нет. | Нельзя строить adapter вокруг несуществующего общего CLI-контракта. |
| `agent_reach.core.AgentReach` | Python-класс предоставляет `doctor()` и `doctor_report()`; оба относятся к health/status. | Это не Python facade для чтения внешних источников. |
| `agent-reach doctor --json` | Единственный явно machine-readable CLI-режим: JSON со свойствами канала `status`, `name`, `message`, `tier`, `backends`, `active_backend`. | JSON описывает готовность backend, а не возвращает research content/provenance. |
| `integrations/mcp_server.py` | Optional MCP server экспортирует только `get_status`. При отсутствии MCP extra он завершается с ошибкой. | MCP surface Agent Reach не является research gateway. |
| `agent_reach/skill` | Skill содержит инструкции, по которым сам Agent вызывает `curl`, `gh`, `yt-dlp`, `feedparser`, `mcporter` и специализированные CLIs. | Agent Reach — capability layer/installer/router для других tools, а не wrapper над их результатами. |

### Установщик и routing

`agent-reach install` по умолчанию работает в safe mode и проверяет окружение.
`--dry-run` также не меняет состояние. Но `--system` разрешает upstream-логике
устанавливать или менять system/global tools, `mcporter`, optional channel tools,
skill files и `~/.agent-reach/config.yaml`; отдельные пути могут использовать
OS package manager, npm или pipx. Это существенно шире, чем безопасный runtime
research call Second Brain.

Channel registry и `ordered_backends()` поддерживают список кандидатов и
`active_backend`. `doctor` действительно пробует лёгкие локальные команды для
части backend, но это не доказательство того, что конкретный URL, feed или
video сейчас прочитается.

### Каналы, относящиеся к этому spike

| Сценарий | Фактическая реализация на exact commit | Что возвращается |
| --- | --- | --- |
| Web | `WebChannel.read(url)` нормализует public HTTP(S) URL и вызывает `https://r.jina.ai/{url}` через `urllib.request.urlopen`. Ответ ограничен 5 MiB, timeout — 30 s; есть распознавание нескольких anti-bot ответов. | `str` с Markdown. Общего `ResearchResult` нет. |
| RSS | `RSSChannel` в основном делает import/readiness check для `feedparser`. Документация вызывает `feedparser.parse()` напрямую. | `feedparser.FeedParserDict`, а не Agent Reach DTO. |
| GitHub | `GitHubChannel.check()` проверяет `gh --version` и локальную metadata-конфигурацию; реальное чтение выполняет вызываемый отдельно `gh` (`repo view`, `search` и т. п.). | Вывод downstream `gh`; общего метода чтения нет. |
| YouTube | `YouTubeChannel.check()` проверяет `yt-dlp --version`, JS runtime и конфигурацию; metadata/subtitles берутся отдельными командами `yt-dlp`. `transcribe` — отдельный download + ffmpeg + optional Whisper provider. | stdout/файлы `yt-dlp` или transcript; общего DTO нет. |

Это подтверждает исходную гипотезу architecture docs: Agent Reach нельзя
притворно оформить как стабильный provider SDK, если upstream фактически
маршрутизирует Second Brain к другим инструментам.

## Runtime surface: stdout, stderr, exit codes, timeout, cancellation

### Machine-readable output

`doctor --json` пишет валидный JSON в stdout; на успешном изолированном запуске
stderr был пуст. JSON содержит 15 channel entries, но не содержит payload
исследования, URL результата, hash, timestamp retrieval или гарантии успешного
чтения конкретного target.

Остальные read-paths не имеют одного формата:

- `gh` может вернуть собственный JSON только если запрошен его флагом;
- `yt-dlp` может печатать metadata/subtitles в stdout или сохранять subtitle
  в файл;
- `feedparser` возвращает Python object;
- web channel возвращает строку из Jina Reader;
- MCP Agent Reach возвращает status text, а не research document.

Следствие: gateway должен сам разделять payload и diagnostics, а не передавать
stdout/stderr как будто это единый доверенный ответ.

### Exit codes

На проверенном CLI фактически наблюдалось:

| Ситуация | Exit code | Канал вывода |
| --- | ---: | --- |
| `version`, `--help`, `doctor --json`, safe install, dry-run | `0` | обычный stdout; `doctor --json` — JSON |
| Ошибка argparse, например неизвестная команда | `2` | stderr |
| Ошибка разбора stdin для `format xhs` | `1` | stderr |
| downstream `gh repo view` без auth в этой среде | `4` | stderr |
| downstream `yt-dlp` для недоступного video | `1` | stderr |

Это не единый protocol-level status contract. Нельзя считать любой exit code
единственным доказательством ни наличия результата, ни отсутствия результата.
Парсер должен валидировать non-empty content и schema, а gateway — маппить
ошибку в собственный ограниченный набор кодов.

### Timeout и cancellation

Timeout существует фрагментарно:

- web `urlopen` — фиксированный 30 s и лимит 5 MiB;
- `probe_command` — по умолчанию 10 s, ловит `TimeoutExpired` и сообщает
  `status="timeout"`;
- разные installer/utility subprocess используют собственные 3/5/10/30/120/300
  секунд;
- `transcribe` имеет отдельные длительные лимиты для download/ffmpeg/provider;
- в CLI нет общего `--timeout` для research и нет public cancellation token/API.

Внешняя остановка процесса возможна только владельцем subprocess. Поэтому
`ResearchGateway`, а не Agent Reach, должен владеть deadline, cancellation
signal и mapping `timeout`/`cancelled`. Каждый adapter обязан передавать
ограничение вниз (`subprocess.run(..., timeout=...)`, `shell=False`) и не
оставлять зависший child process.

### URL и недоверенный output

Web channel отвергает часть небезопасных URL: не принимает userinfo,
localhost/internal suffixes и literal non-global IP. Но это не заменяет policy
Second Brain для redirects, DNS rebinding, egress и content size. Входной URL и
весь внешний текст считаются недоверенными данными; prompt-инструкции из текста
не получают authority.

## Public scenario smoke

Ниже приведены реальные команды exact pinned environment. Они не использовали
auth и не обращались к vault.

| Сценарий | Проверка | Результат | Вердикт по сценарию |
| --- | --- | --- | --- |
| Web | `WebChannel().read("https://example.com")` | exit `0`; непустой Markdown, 367 символов, содержит `Example Domain` | Работает как узкий Jina-backed Python method. |
| RSS | `feedparser.parse("https://www.rssboard.org/files/sample-rss-2.xml")` | exit `0`; `bozo=false`, feed `NASA Space Station News`, 5 entries | Работает напрямую через feedparser; Agent Reach сам не нормализует результат. |
| Public GitHub | `gh repo view octocat/Hello-World --json nameWithOwner,description,url` с пустым изолированным `GH_CONFIG_DIR` | exit `4`; `gh` потребовал `gh auth login` | Public route Agent Reach не подтверждён без auth в этой реальной Windows runtime; auth нельзя добавлять в этот spike. |
| YouTube metadata | `yt-dlp --ignore-config --no-cache-dir --no-playlist --skip-download --js-runtimes node --print "%(id)s|%(title)s" https://www.youtube.com/watch?v=jNQXAC9IVRw` | exit `0`; `jNQXAC9IVRw|Me at the zoo` | Metadata публичного video доступна без auth. |
| YouTube subtitles | Та же изолированная команда с `--write-auto-subs --sub-langs en --sub-format vtt --skip-download` | exit `0`; создан непустой `jNQXAC9IVRw.en.vtt` размером 440 bytes с WEBVTT captions | Subtitle path реально работает для этого video без auth; результат target-dependent. |

Дополнительный негативный YouTube smoke для старого `BaW_jenozKc` завершился
exit `1` с `This video is unavailable`. Это не объявляется отказом всего
YouTube: повторная проверка на публичном `jNQXAC9IVRw` успешно разделила
проблему конкретного target и возможность backend.

`agent-reach doctor --json` в той же среде завершился exit `0`, но показал
`youtube.active_backend = "yt-dlp"` при warning о не настроенном JS runtime.
Это наглядно подтверждает: doctor сообщает готовность инструмента, а не
доказательство конкретного subtitle fetch.

## Главные findings

1. **Agent Reach не является единым research API.** Его core API и optional MCP
   дают health/status; data retrieval выполняется другими tools.
2. **Прямой production import upstream нарушит dependency boundary.** Пакет
   тянет собственные network/config/install concerns и не нужен домену или
   application Second Brain.
3. **Web — наиболее чистый кандидат, но это Jina proxy с фиксированным
   timeout/лимитом и без normalized DTO.** Его можно обернуть только на нашей
   границе.
4. **RSS технически доступен, но сейчас это библиотечный вызов feedparser, а
   не Agent Reach facade.** Нужен отдельный parser/normalizer policy.
5. **GitHub нельзя считать подтверждённым zero-auth сценарем.** Документация
   upstream обещает public repo path, но реальный `gh` в изолированной среде
   потребовал auth и вернул exit `4`; production contract должен явно объявлять
   auth requirement или поддерживать другой public transport.
6. **YouTube без auth реально работает для проверенного metadata и captions
   target**, но результат зависит от доступности video, JS runtime, версии
   yt-dlp и внешнего сайта; `active_backend` не равен успешному fetch.
7. **stdout/stderr/exit codes не образуют общего протокола.** Нужны собственные
   error codes, parser validation и provenance в Second Brain.
8. **Timeout есть только локально и разрозненно; cancellation contract
   отсутствует.** Deadline/cancellation должны быть обязательными полями нашей
   gateway boundary.
9. **Installer surface слишком привилегирован для автоматического runtime.**
   `--system` может менять глобальные tools, config и skills; installer не
   должен запускаться из research request.
10. **Vault должен оставаться полностью снаружи.** Research результат сначала
    живёт в памяти как untrusted DTO; сохранение в note — другой, explicit и
    approval-gated use case через существующий Safe Write/Proposal workflow.

## Минимальный proposed contract

Контракт ниже является проектным предложением этого spike, а не добавленным
кодом. Он намеренно синхронный: текущий CLI/application Second Brain
синхронный, а cancellation передаётся явно. Async adapter можно добавить
позже без изменения normalized DTO.

### Request и cancellation

```python
SourceKind = Literal["web", "github", "rss", "youtube"]


@dataclass(frozen=True, slots=True)
class ResearchRequest:
    source_kind: SourceKind
    uri: str
    timeout_seconds: int = 30
    max_bytes: int = 5_000_000


class CancellationToken(Protocol):
    def is_cancelled(self) -> bool: ...
```

Contract rules:

- `uri` — только public `http(s)` без userinfo, credential query и локального
  target; для `github` и `youtube` host должен соответствовать выбранному
  source kind;
- `timeout_seconds` и `max_bytes` валидируются gateway и ограничиваются
  policy cap до вызова adapter;
- `CancellationToken` проверяется до запуска, во время bounded operation и
  после неё;
- запрос не содержит `Path`, `VaultManifest`, `NoteType`, writer, destination
  или произвольные shell fragments;
- authenticated source не является другим значением `source_kind`: это
  отдельное security decision.

### Port

```python
class ExternalResearchPort(Protocol):
    def read(
        self,
        request: ResearchRequest,
        *,
        cancellation: CancellationToken,
    ) -> "ResearchResult": ...
```

У порта нет методов `write`, `save`, `publish`, `configure`, `login` или
`install`. Он получает request и возвращает результат в памяти. Реализация
порта не знает о destination path vault и не получает write-доступ.

### Normalized DTO

Минимальный результат должен содержать только данные, необходимые для
дальнейшего review/normalization:

```python
@dataclass(frozen=True, slots=True)
class ResearchSource:
    uri: str
    source_kind: SourceKind
    title: str | None
    retrieved_at: datetime
    backend: str
    backend_revision: str | None


@dataclass(frozen=True, slots=True)
class ResearchResult:
    status: Literal[
        "ok",
        "empty",
        "invalid_request",
        "timeout",
        "cancelled",
        "upstream_error",
        "upstream_malformed",
    ]
    source: ResearchSource | None
    text: str | None
    media_type: str | None
    language: str | None
    content_sha256: str | None
    truncated: bool
    error_code: str | None
    exit_code: int | None
    warnings: tuple[str, ...]
```

Нормализация означает:

- `text` — уже проверенный непустой текстовый payload, а не raw stdout;
- `content_sha256` считается от UTF-8 текста после явно описанной normalizing
  policy;
- `backend` — например `jina-reader`, `feedparser`, `gh-cli`, `yt-dlp`, но
  не credentials и не произвольная командная строка;
- `backend_revision` может хранить версию инструмента или pinned revision,
  когда она достоверно известна;
- `retrieved_at` — UTC RFC 3339/aware datetime;
- `error_code` и `exit_code` позволяют automation различать timeout, cancel,
  malformed output и non-zero process; raw stderr в DTO не попадает;
- `warnings` короткие и sanitized; внешний текст не становится инструкцией.

### Gateway policy

`ResearchGateway` — единственный application entry point для будущего
research use case:

```text
CLI / future agent use case
            |
            v
application.research.ResearchGateway
            |
            v
ExternalResearchPort (in-memory DTO boundary)
            |
            v
future public process adapter (optional)
            |
            v
Jina / feedparser / gh / yt-dlp
```

Gateway обязан:

1. проверить `source_kind` + URI, запретить userinfo/credential query и
   private/internal destinations;
2. установить bounded deadline и передать cancellation вниз;
3. вызвать ровно один выбранный port operation без implicit install/login;
4. разделить stdout (candidate payload) и stderr (sanitized diagnostic);
5. валидировать формат, non-empty content, byte limit и provenance;
6. преобразовать downstream failure в перечисленные `status`/`error_code`;
7. вернуть DTO в память и не открывать vault path.

Gateway не обязан в v1 делать retry. Если retry будет нужен, он должен быть
ограничен idempotent read operations, включать общий deadline и быть отдельным
решением; повторять installer/configuration запрещено.

## Security decision для authenticated sources

Authenticated sources полностью вынесены за рамки этого spike и должны получить
отдельный security decision до любого кода. В него минимум должны войти:

- явный список поддерживаемых credential источников и их scope;
- запрет передачи секретов в argv, logs, DTO, PR, CI и error messages;
- политика process environment allowlist и запрет неявного наследования;
- ручная approval boundary для login/cookie import и отдельная revoke/rotation
  процедура;
- path/symlink/permission policy для локального credential store, если он
  вообще будет разрешён;
- redaction и audit policy без сохранения raw cookies/session payload;
- отдельные правила для desktop browser sessions и headless/server runtime;
- negative tests для отсутствия credential и для попытки использовать private
  source в public режиме.

До принятия этого решения не следует читать `~/.agent-reach/config.yaml`,
browser profiles, `hosts.yml`, cookie stores или inherited auth environment.

## Точный scope следующего implementation PR

Следующий PR должен быть одним небольшим Second Brain-only PR:

### Входит

1. `src/second_brain/application/ports.py` — добавить только Protocol для
   `ExternalResearchPort` и минимальные типы ошибок/отмены, не добавляя
   imports внешних SDK.
2. `src/second_brain/application/research.py` — реализовать
   `ResearchRequest`, normalized DTO и `ResearchGateway` с:
   - public URL validation;
   - bounded timeout/max-bytes policy;
   - cancellation checks;
   - deterministic mapping статусов;
   - разделением payload/diagnostics;
   - отсутствием любых vault/write dependencies.
3. `tests/test_research_gateway.py` — только deterministic fake port и fixture
   data: valid request, private/userinfo URL rejection, empty/malformed output,
   max-bytes/truncation, timeout, cancellation, exit-code mapping и доказательство
   отсутствия вызова writer/path.
4. Коротко обновить application/architecture documentation только если это
   нужно для ссылки на уже утверждённый контракт; не менять vault contract.

### Не входит

- `Agent-Reach` в `pyproject.toml`, `uv.lock` или CI dependencies;
- `src/second_brain/adapters/agent_reach.py` и любой production adapter;
- вызов `gh`, `yt-dlp`, `curl`, `feedparser`, Jina, MCP или live network;
- CLI-команда `research` и запись результата в Markdown;
- credentials, cookies, browser sessions, `--system`, installer/configuration;
- изменение `second-brain-vault`.

После этого PR отдельным решением можно рассмотреть **следующий** adapter PR:
не импортировать Agent Reach, а запускать явно выбранные внешние tools через
bounded argv runner с `shell=False`, allowlist executable, timeout/cancellation,
redaction и recorded fixtures. Даже такой adapter сначала должен покрывать
только public web/RSS/YouTube; GitHub остаётся blocked/conditional до решения
по реальному no-auth/auth surface. Live smoke для него должен быть manual,
opt-in и не частью CI.

## Финальный verdict

**GO WITH LIMITATIONS** — идти с минимальным `ExternalResearchPort`/
`ResearchGateway` и deterministic contract tests можно. Production Agent Reach
adapter, authenticated sources, dependency lock changes, live CI и любые записи
в vault на этом этапе — **NO-GO**.
