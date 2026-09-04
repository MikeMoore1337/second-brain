# Public GitHub Research Decision v1: unauthenticated REST transport и scope первого adapter

Статус: завершённый design/transport spike; production GitHub adapter в этом
документе и PR не реализуется.

Дата проверки: 2026-09-04 (Europe/Moscow).

Baseline: `main` после PR #20 — `89b0d71f75cd31b10ed35d8961e4cdedf84ec673`.

Репозиторий scope: только `MikeMoore1337/second-brain`. Соседний
`second-brain-vault` не читается и не изменяется.

## Verdict

**GO WITH LIMITATIONS.**

Public GitHub research v1 может использовать direct unauthenticated GitHub REST
API через фиксированный `https://api.github.com`. Это разрешение только на
узкий transport contract и следующий implementation PR. Оно не разрешает
authenticated research, private repositories, GitHub Enterprise, generic
GitHub browsing, Agent Reach или изменение CLI в этом spike.

## Унаследованные границы

Решение опирается на уже согласованные задачи, но не повторяет Agent Reach
spike:

| Источник | Что переносится в этот spike |
| --- | --- |
| [issue #1](https://github.com/MikeMoore1337/second-brain/issues/1) | Research остаётся read-only capability на границе ports/adapters; domain, vault и Safe Write Operations не получают внешний network/write path. |
| [issue #11](https://github.com/MikeMoore1337/second-brain/issues/11) и [`agent-reach-spike-v1.md`](./agent-reach-spike-v1.md) | Agent Reach не является стабильным content API; наблюдавшийся GitHub route зависел от `gh` и auth. Он не используется. |
| [issue #13](https://github.com/MikeMoore1337/second-brain/issues/13) | Используется существующий `ResearchRequest` / `ResearchSource` / `ExternalResearchPort` / `ResearchGateway`, без нового GitHub-specific application DTO. |
| [issue #15](https://github.com/MikeMoore1337/second-brain/issues/15) | Для bounded external process применяются explicit argv, `shell=False`, safe environment, disabled user config, bounded output и реальное timeout/cancellation. |
| [issue #17](https://github.com/MikeMoore1337/second-brain/issues/17) | Ошибки остаются в закрытой research taxonomy; нет retries, redirect-following, recursive fetch и live tests в CI. |
| [issue #19](https://github.com/MikeMoore1337/second-brain/issues/19) | GitHub остаётся отдельным adapter slice; текущий YouTube adapter не расширяется. |
| [PR #20](https://github.com/MikeMoore1337/second-brain/pull/20) | Точная исходная точка решения — указанный `89b0d71f...`. |

## Проверенная официальная REST documentation

Проверка выполнена 2026-09-04. Основные источники — официальные GitHub Docs:

* [API Versions](https://docs.github.com/en/rest/about-the-rest-api/api-versions)
  подтверждает, что текущая поддерживаемая версия — **`2026-03-10`**. Её нужно
  передавать явным `X-GitHub-Api-Version`. Если header не передан, GitHub
  использует `2022-11-28`; эта версия сейчас ещё поддерживается, но для нового
  adapter на неё полагаться не следует.
* [Get a repository](https://docs.github.com/en/rest/repos/repos#get-a-repository)
  описывает `GET /repos/{owner}/{repo}`, рекомендует
  `Accept: application/vnd.github+json` и прямо разрешает endpoint без
  authentication, если запрашиваются только public resources.
* [Get a repository README](https://docs.github.com/en/rest/repos/contents#get-a-repository-readme)
  описывает `GET /repos/{owner}/{repo}/readme`, также разрешённый без
  authentication для public resources. Не заданный `ref` означает default
  branch. Endpoint поддерживает `application/vnd.github.raw+json` для raw file
  contents и `application/vnd.github.html+json` для отрендеренного HTML.
* [Rate limits for the REST API](https://docs.github.com/en/rest/using-the-rest-api/rate-limits-for-the-rest-api)
  устанавливает для unauthenticated public requests primary limit **60
  requests/hour на originating IP**. В ответе доступны
  `X-RateLimit-Limit`, `X-RateLimit-Remaining`, `X-RateLimit-Reset` и
  `X-RateLimit-Resource`. При превышении primary или secondary limit возможны
  `403`/`429`; retry должен ждать указанный reset/`Retry-After`, а не выполнять
  немедленный повтор.
* [Troubleshooting the REST API](https://docs.github.com/en/rest/using-the-rest-api/troubleshooting-the-rest-api)
  фиксирует, что valid `User-Agent` обязателен; значение должно быть именем
  пользователя или приложения.

Документация показывает общие authenticated cURL examples, но это не меняет
условие конкретных repository endpoints: public data может быть прочитана без
authentication. Следующий adapter будет использовать только этот public path.

## Единственный disposable live smoke

Выполнен ровно один smoke-run 2026-09-04 на заведомо public
`octocat/Hello-World`. Все запросы были только `GET`; payload не сохранялся.
В smoke-run не передавался `Authorization` и не использовались token,
`gh`, `gh auth`, Agent Reach или GitHub config. Явно заданы только следующие
request headers:

```text
Accept: application/vnd.github+json
X-GitHub-Api-Version: 2026-03-10
User-Agent: Second-Brain-Public-Research-Decision-v1
```

Для media comparison README был запрошен также с отдельным raw `Accept`:
`application/vnd.github.raw+json`. Payload намеренно не копировался в отчёт.

| Запрос | Результат | Rate-limit observation |
| --- | --- | --- |
| `GET https://api.github.com/repos/octocat/Hello-World` | `200`, `application/json; charset=utf-8`; `full_name=octocat/Hello-World`, `visibility=public`. | `limit=60`, `remaining=58`, `resource=core`. |
| `GET https://api.github.com/repos/octocat/Hello-World/readme` с JSON Accept | `200`, JSON object содержит `content` и `encoding=base64`. | `limit=60`, `remaining=57`, `resource=core`. |
| `GET https://api.github.com/repos/octocat/Hello-World/readme` с raw Accept | `200`, raw body; `Content-Type=application/vnd.github.raw+json; charset=utf-8`, 46 UTF-8 bytes в этом smoke. | `limit=60`, `remaining=56`, `resource=core`. |

Smoke подтверждает именно transport hypothesis для public repository. Значения
`remaining` являются моментальным наблюдением конкретного originating IP, а не
гарантией доступного бюджета в будущем.

## Решение по README media type

Проверялись два варианта одного README endpoint:

| Вариант | Плюсы | Риски/стоимость |
| --- | --- | --- |
| JSON + `content`/`encoding=base64` | Есть envelope с `path`, `size`, `sha`, `encoding` и другими полями, которые можно отдельно валидировать. | Нужно парсить JSON, проверять типы, bounded-значение `content`, декодировать Base64 и учитывать дополнительную память/ошибки преобразования. Большинство envelope-полей для v1 не нужно. |
| Raw `application/vnd.github.raw+json` | Ответ — непосредственно bytes README; можно применить hard byte bound и strict UTF-8 decode без Base64 и без второго transport. `download_url` не нужен. | Нет JSON envelope; размер и текстовая пригодность проверяются самим adapter-ом. |

**Выбор: raw media type.** Это меньшая и более безопасная поверхность для
первого adapter: explicit `Accept: application/vnd.github.raw+json`, bounded
stdout, strict UTF-8 decode, без HTML rendering, `download_url`, redirect или
follow-up fetch. Хотя GitHub Docs называют raw вариантом default при отсутствии
media type, header будет передаваться явно, чтобы не зависеть от implicit
default.

README `404` после успешного repository metadata `200` означает
детерминированный **metadata-only success**. Это не повод переходить к
`download_url` или другому host. Непустой `200` body с invalid UTF-8 либо
несовместимым bounded payload — `RESEARCH_MALFORMED_RESULT`.

## Строгий v1 input scope

Первый adapter принимает только:

```text
https://github.com/{owner}/{repo}
```

Точная policy:

* схема — `https`;
* host — ровно `github.com`; `www.github.com`, `api.github.com`,
  `raw.githubusercontent.com`, Gist и Enterprise hosts не принимаются;
* path содержит ровно два ASCII path segments: `owner` и `repo`;
* `owner` — bounded GitHub account segment: 1–39 символов `[A-Za-z0-9-]`,
  начинается и заканчивается alphanumeric;
* `repo` — bounded single segment длиной 1–100, состоящий только из ASCII
  `[A-Za-z0-9._-]`. Начальный и конечный alphanumeric не требуются: это
  допускает реальное имя repository `.github`; suffix `.git` по-прежнему
  запрещён контрактом v1 без учёта ASCII-регистра. Это минимальный allow-list
  имени repository, а не permissive path parser;
* отсутствуют trailing slash, query, fragment, port, userinfo, whitespace,
  control characters, backslash и `%`/percent-encoding; точные dot-segments
  `.` и `..` запрещены, но leading dot в обычном имени вроде `.github`
  разрешён;
* path `/owner/repo/issues`, `/pulls`, `/tree`, `/blob` и любые другие
  subpaths отклоняются.

URI без этих условий получает `RESEARCH_INVALID_REQUEST` до запуска процесса.
`owner` и `repo` передаются в API только как уже проверенные отдельные path
segments. Они не определяют host, произвольный path, query или cURL options.

В v1 нет `ref` override: README читается только с default branch. Не входят
issues, pull requests, REST/Code search, releases, commits, files, tree
browsing, pagination, cloning, archive download, links и recursive fetch.

## Точный `ResearchSource` mapping

Adapter возвращает существующий `ResearchSource`, без изменения
`application` DTO:

| Поле | Правило |
| --- | --- |
| `uri` | Исходный canonical input URI, без silent redirect или замены на `html_url`. |
| `source_kind` | `SourceKind.GITHUB`. |
| `retrieved_at` | Значение adapter clock с явным UTC offset; naive timestamp не принимается. |
| `backend` | Фиксированный technical identifier `github-rest`. |
| `title` | Canonical `full_name` из metadata (`owner/repo`). Оба сегмента сравниваются с validated `owner/repo` ASCII-case-insensitively; case-only difference принимается. Отсутствующий, malformed или отличный identity — `RESEARCH_MALFORMED_RESULT`. |
| `author` | Canonical `owner.login`, если поле присутствует, является bounded owner segment и совпадает с validated `owner` ASCII-case-insensitively. Case-only difference принимается; invalid/mismatched значение — `RESEARCH_MALFORMED_RESULT`. `None` допустим только при действительно отсутствующем поле. |
| `upstream_id` | Decimal string из положительного repository `id`, если поле имеет ожидаемый тип. Не использовать URL или mutable name как ID. |
| `media_type` | `text/markdown`, если README принят; `text/plain`, если README отсутствует и возвращён metadata-only result. |
| `published_at` | `None`: `created_at`, `updated_at` и `pushed_at` не являются публикацией research document. |
| `content` | Deterministic bounded text: metadata block в фиксированном порядке, blank line, затем raw README. При отсутствии README — тот же metadata block с явной строкой `README: (отсутствует)`. |

Identity validation выполняется после allow-list validation metadata segments и
использует ASCII case-folding. Поэтому input `GitHub/.GitHub` и metadata
`full_name=github/.github`, `owner.login=github` дают валидный result, а
canonical casing из metadata разрешено сохранить в output. Различие не только
в регистре, отсутствие обязательного `full_name` либо mismatch остаются
`RESEARCH_MALFORMED_RESULT`.

Metadata block имеет фиксированный порядок:

```text
Repository: {full_name}
Description: {description or empty}
Default branch: {default_branch or empty}
Language: {language or empty}
Topics: {topics sorted case-insensitively and joined by ", " or empty}
README:

{strict UTF-8 README or "(отсутствует)"}
```

Metadata scalar values bounded и проверяются как untrusted text; line/control
injection не разрешается. README не интерпретируется как instructions, не
рендерится и не исполняется. Допускается только детерминированная нормализация
line endings к `LF`; silent truncation запрещена. Общий UTF-8 `content`, включая
metadata block, не превышает `ResearchRequest.max_bytes`.

## Error и rate-limit mapping

Используется существующая taxonomy из `application.ports`; новый GitHub-specific
error code не добавляется.

| Ситуация | Mapping |
| --- | --- |
| Неправильный source kind, URI или path shape | `RESEARCH_INVALID_REQUEST` до network/process launch. При прямом вызове adapter с другим `SourceKind` сохраняется существующий fail-closed convention `RESEARCH_BACKEND_UNAVAILABLE`. |
| Нет системного `curl` или process boundary не запускается | `RESEARCH_BACKEND_UNAVAILABLE`. |
| Cancellation / общий deadline process | `RESEARCH_CANCELLED` / `RESEARCH_TIMEOUT`; текущий process завершается и очищается. |
| stdout/response envelope превысил hard bound | `RESEARCH_CONTENT_TOO_LARGE`. |
| Invalid UTF-8, invalid HTTP envelope, invalid JSON metadata, unexpected field type или непустой README с некорректным raw body | `RESEARCH_MALFORMED_RESULT`. |
| Metadata `200`, затем README `404` | Успешный metadata-only `ResearchSource`. |
| Metadata `404` (включая несуществующий или private repository), `301`, `401`, `5xx` или любой другой неожиданный non-`200` | `RESEARCH_UPSTREAM_FAILURE`; private existence не раскрывается. Redirect не следует. |
| README после metadata `200`: `403`, `429`, `301`, `401`, `5xx` или иной неожиданный status | `RESEARCH_UPSTREAM_FAILURE`; metadata-only fallback не применяется. |
| `403`/`429` с `X-RateLimit-Remaining: 0`, `Retry-After` или rate-limit message | `RESEARCH_UPSTREAM_FAILURE` с тем же безопасным внешним сообщением; header используется только для внутреннего bounded diagnostic. |

Один успешный adapter read делает не более двух serial requests: metadata и
README. Retries, exponential backoff, conditional `ETag`/`If-None-Match`,
`GET /rate_limit` и fallback backend в v1 не добавляются. При rate limit adapter
останавливается; caller решает, когда повторить операцию после reset. Raw
response body, stderr и upstream error message не становятся `ResearchSource`
или публичной diagnostic строкой.

## Security/network boundary

```text
ResearchGateway
    |
    v
PublicGitHubAdapter -- fixed argv, GET only --> https://api.github.com
                                      |             /repos/{owner}/{repo}
                                      |             /repos/{owner}/{repo}/readme
                                      +-- owner/repo are validated path segments
```

Обязательная boundary policy следующего implementation PR:

* `api.github.com` — compile-time constant. User input никогда не становится
  network destination; DNS preflight/pinning user target не требуется и не
  добавляется.
* HTTPS, certificate/TLS verification и фиксированный API host сохраняются;
  `--insecure`/`-k`, arbitrary proxy, arbitrary redirect и `--location`
  запрещены. `--max-redirs 0` — fail closed на redirect, включая GitHub rename.
* Запуск — explicit argv с `shell=False`; executable/options/headers закрыты
  adapter-ом. `.curlrc` отключается через `--disable`; safe process environment
  переиспользует существующий bounded runner и не передаёт proxy/auth config.
* Не передаются `Authorization`, `GH_TOKEN`, `GITHUB_TOKEN`, PAT, OAuth/App
  credentials, cookies, browser sessions, `.netrc` или GitHub CLI config. Не
  вызываются `gh`, `gh auth login` и Agent Reach. Внешний фиксированный
  `User-Agent` — `Second-Brain-Public-Research-Decision-v1`; он не берётся из
  request.
* HTTP method, API paths, `Accept` values и
  `X-GitHub-Api-Version: 2026-03-10` фиксированы. `download_url`, `html_url`,
  item links и любые upstream-provided URLs не следуются.
* stdout ограничен hard bound, stderr ограничен и не публикуется. JSON/headers
  и raw README — untrusted external data; они не получают write, shell, Git,
  LLM или vault capability.
* `ResearchGateway` остаётся первой policy boundary, а adapter повторно
  проверяет strict repository-root shape, response identity, content size и
  explicit timestamp. Никаких изменений vault или persistence нет.

Фиксированный host уменьшает SSRF-поверхность до одного allowlisted GitHub
service, но не является общей гарантией egress-безопасности операционной
системы. Firewall/proxy policy оператора остаётся deployment boundary; adapter
не предоставляет пользовательский host override.

## Deterministic test strategy (без live GitHub в CI)

Следующий implementation PR должен использовать fake/injectable process runner
и bounded fixtures, а не сеть:

* accepted exact repository-root URL `https://github.com/github/.github`, а
  также rejection matrix для `http`, `www`, `api.github.com`, `.git`, точных
  dot-segments `.`, `..`, trailing slash, query/fragment, encoded/control
  characters, subpaths, overlong/invalid segments;
* mixed-case input, например `https://github.com/GitHub/.GitHub`, с canonical
  metadata `full_name=github/.github` и `owner.login=github`: accepted, не
  `RESEARCH_MALFORMED_RESULT`, canonical metadata casing сохраняется в output;
* exact cURL argv: два serial `GET` максимум, fixed host/path/headers,
  `--disable`, `--noproxy *`, `--proto =https`, `--max-redirs 0`, no
  `Authorization`, cookies, netrc, proxy, arbitrary options или redirect;
* response-envelope parsing, status/header extraction и bounded body; metadata
  JSON allowlist, public identity check, stable ID/title/author mapping;
* raw README strict UTF-8 normalization, total `max_bytes`, empty/malformed
  body, `README 404` metadata-only success, metadata `404`, `403`, `429`, `301`,
  transport failure и `5xx` mappings;
* cancellation/timeout/overflow cleanup, no retry и no third request;
* deterministic `ResearchSource` content, timestamp with explicit offset,
  metadata-only media type и untrusted Russian/Unicode text;
* no process launch for unsupported `SourceKind`, no vault/Git/LLM side effect;
* existing WEB/RSS/YouTube behavior remains covered by current tests; live
  GitHub smoke не добавляется в pytest или CI.

## Точный scope следующего implementation PR

Следующий PR должен быть одним узким production slice:

1. Добавить `src/second_brain/adapters/research/github.py` с
   `PublicGitHubAdapter`, реализующим только
   `ExternalResearchPort.read()` для `SourceKind.GITHUB`.
2. Переиспользовать существующий `BoundedProcessRunner`; если для mixed
   headers/body нужен seam, добавить только маленький внутренний bounded
   response-envelope parser без общего HTTP framework.
3. Выполнять ровно два фиксированных serial GET максимум: repository metadata и
   preferred README. README запрашивать raw media type; `README 404` давать
   metadata-only result. Никаких retries, redirects, `download_url`, search,
   pagination или extra endpoint.
4. Вернуть mapping из этого документа в существующий `ResearchSource` и
   существующую error taxonomy. Не менять `SourceKind`, `ResearchRequest`,
   `ResearchSource` или `ExternalResearchPort`, если это не требуется для
   исправления уже зафиксированного контракта.
5. Добавить `tests/test_github.py` с fake process fixtures и обновить только
   необходимый CLI regression/success coverage. Так как текущий CLI намеренно
   оставляет `github` unsupported, отдельным изменением следующего PR разрешить
   `research read --type github`, не меняя синтаксис остальных types.
6. При необходимости минимально обновить русские CLI/architecture docs;
   production dependency, `pyproject.toml`, `uv.lock`, Agent Reach и vault не
   менять.

В следующий PR не входят issues, PRs, search/code search, releases, commits,
files/tree browsing, generic GitHub URL browser, authenticated/private sources,
GitHub Enterprise, `gh`, PAT/OAuth/GitHub App, Agent Reach, LLM/NoteDraft,
vault write, Git proposal, persistence, scheduler и live CI tests.

## Итог

Официальный public REST path подтверждён документами и одним zero-auth smoke:
metadata и preferred README доступны через `api.github.com`, а
unauthenticated rate limit наблюдается как 60 requests/hour на IP. Direct REST
явно лучше `gh` для этого use case: он не зависит от local CLI auth/config,
имеет фиксированный host и два allowlisted read operation.

Ограничения существенны: public-only, strict repository-root URL, максимум два
GET за read, 60/hour per originating IP, no retries/fallback и metadata-only
поведение без README. Поэтому verdict — **GO WITH LIMITATIONS**, а не безусловный
GO. Этот PR заканчивается design decision; production adapter и CLI остаются
точным scope следующего отдельного implementation PR.
