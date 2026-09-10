# Production Web Deployment v1

Статус этого документа — repository-side contract для ручного owner-managed
deployment. Он не выполняет SSH, DNS/VPS mutation, установку Caddy, создание
OAuth App, создание secrets, выдачу сертификата, real login или production
deploy.

## Границы и архитектура

Целевой public authority — `https://brain.mikemoore.top`. Схема deployment:

```text
Internet
   |
HTTPS :443
   |
Caddy (единственная public HTTP/HTTPS boundary)
   |
127.0.0.1:8123
   |
Second Brain Web (`second-brain web serve`)
   |
second-brain-vault
```

Сохраняются следующие invariants:

- приложение слушает только `127.0.0.1:8123`; публичным интерфейсом владеет
  только Caddy;
- TLS терминируется Caddy, а HTTP для hostname штатно перенаправляется на
  HTTPS средствами automatic HTTPS;
- Web runtime работает от отдельного непривилегированного пользователя;
- `second-brain` и приватный `second-brain-vault` остаются независимыми
  sibling repositories;
- vault не становится submodule, subtree или каталогом внутри application
  repository;
- Safe Write остаётся единственной application write boundary;
- существующий owner-only GitHub OAuth сохраняется без ослабления security
  contract; anonymous application access не добавляется;
- из public auth/health surface остаются только уже разрешённые текущим
  приложением маршруты `/login`, `/auth/github/login`,
  `/auth/github/callback`, `/auth/logout` и `/healthz`;
- `web/dist` — фактический React runtime bundle текущего приложения. Каталог
  `src/second_brain/entrypoints/web/static` остаётся legacy reference fixture и
  не заменяет production build.

Текущие CLI options являются source of truth: `web serve` не принимает public
host option, а `uvicorn.run` запускается с `host="127.0.0.1"`, выбранным port и
`access_log=False`. Внешний proxy не является обходом этого ограничения.

Tracked artifacts:

- [systemd unit template](../../deploy/systemd/second-brain-web.service);
- [Caddyfile template](../../deploy/caddy/Caddyfile.example);
- [базовый VPS/bootstrap runbook](vps.md);
- [существующий OAuth contract](web-auth.md).

## Production layout

Рекомендуемый root для этого шаблона — `/srv/second-brain`:

```text
/srv/second-brain/
├── second-brain/
├── second-brain-vault/
└── runtime/
    └── web.env
```

`second-brain/` и `second-brain-vault/` должны быть отдельными обычными Git
worktrees. `runtime/web.env` находится вне обоих repositories, не копируется в
vault и не добавляется в Git.

Systemd template использует:

- `User=second-brain` и `Group=second-brain`;
- `WorkingDirectory=/srv/second-brain/second-brain`;
- protected `EnvironmentFile=/srv/second-brain/runtime/web.env`;
- `ExecStart` через абсолютный путь к `uv`, `--python 3.14`, `--no-sync`,
  текущий CLI и явный `--port 8123`.

Путь `/usr/local/bin/uv` в template — безопасный placeholder для абсолютного
пути. До установки unit его нужно заменить на фактический результат
`command -v uv` на выбранном VPS. В unit нет secrets, OAuth values или
Cloudflare credentials.

`--no-sync` намеренно запрещает изменять или обновлять environment при старте
service. Locked dependency installation выполняется отдельным release step:
`uv sync --locked --python 3.14`.

Hardening ограничен совместимыми настройками: `ProtectSystem=full` делает
системные области read-only, но не блокирует Safe Write в sibling vault под
`/srv`; `ProtectSystem=strict` в этот template не добавляется без отдельного
полного аудита writable paths. `ProtectHome=read-only` позволяет выполнить
user-level `uv` из `/home`, но не разрешает приложению писать туда. `PrivateTmp`,
`PrivateDevices`,
`NoNewPrivileges`, kernel/control-group protection, `RestrictSUIDSGID` и
ограничение address families не требуют root и не меняют application write
boundary.

Graceful shutdown сохраняется через `Type=simple`, `KillSignal=SIGTERM`,
`KillMode=control-group` и `TimeoutStopSec=30s`. Автоматический update,
daemonization, root runtime и публичный bind в service не используются.

## Runtime EnvironmentFile

Owner создаёт `/srv/second-brain/runtime/web.env` вручную вне repositories.
Файл должен:

- принадлежать service/operator user;
- иметь mode `600`, а каталог `runtime` — private permissions;
- использоваться одновременно systemd `EnvironmentFile` и CLI option
  `--env-file`;
- содержать только owner-managed production settings и secrets;
- не попадать в issue, PR, Git history, vault, argv или diagnostics.

Required variable names текущего public Web runtime:

- `SECOND_BRAIN_VAULT_PATH`;
- `SECOND_BRAIN_WEB_AUTH`;
- `SECOND_BRAIN_PUBLIC_BASE_URL`;
- `SECOND_BRAIN_GITHUB_CLIENT_ID`;
- `SECOND_BRAIN_GITHUB_CLIENT_SECRET`;
- `SECOND_BRAIN_GITHUB_ALLOWED_USER_ID`;
- `SECOND_BRAIN_SESSION_SECRET`;
- `SECOND_BRAIN_SESSION_TTL_SECONDS`;
- `SECOND_BRAIN_OAUTH_STATE_TTL_SECONDS`.

Режим, public URL, allowed numeric user ID и bounded TTL должны соответствовать
текущему [OAuth contract](web-auth.md): `github`,
`https://brain.mikemoore.top`, `42142321`, `43200` секунд для session и `600`
секунд для OAuth state. Secret values здесь намеренно не повторяются.

Если конкретный authenticated advisor/LLM path включён в текущем runtime,
owner отдельно предоставляет только существующие variables
`CLOUDFLARE_ACCOUNT_ID` и `CLOUDFLARE_API_TOKEN` по действующему adapter
contract. Они не нужны для обычного `doctor`, `vault validate` и health
smoke.

Для layout выше относительный `SECOND_BRAIN_VAULT_PATH` разрешается от
каталога `web.env` и должен указывать на sibling `second-brain-vault`. Не
используйте `deploy/env.example` как production Web env file: его строгий
bootstrap contract предназначен только для `SECOND_BRAIN_VAULT_PATH`.

## External owner actions

До первого real deployment owner должен выполнить действия за пределами этого
repository task:

1. Выбрать Linux VPS и отдельного непривилегированного service user. Убедиться,
   что user может читать application files и выполнять Safe Write в private
   vault.
2. Создать два независимых sibling checkout в layout выше и private
   `runtime/web.env` с mode `600`. Не создавать файл с production values в
   repository.
3. Создать DNS `A` и/или `AAAA` record для `brain.mikemoore.top`, указывающий
   на VPS. Конкретный DNS provider этим repository не предполагается.
4. До TLS enablement проверить, что DNS резолвится на ожидаемый VPS и что
   inbound TCP ports `80` и `443` доступны Caddy. Не направлять public DNS на
   `8123`.
5. Вручную создать GitHub OAuth App с такими значениями:

   - Homepage URL: `https://brain.mikemoore.top`;
   - Authorization callback URL:
     `https://brain.mikemoore.top/auth/github/callback`.

   В issue, PR и Codex не передаются Client ID, Client Secret или Session
   Secret. Values размещаются только в protected `web.env`.
6. Установить и проверить Caddy 2.x, поддерживающий текущую директиву
   `log_skip` (Caddy 2.8+), а также открыть его configuration path и service
   journal operator-ом. Cloudflare Tunnel и wildcard hosts в этот contract не
   входят.
7. Перед запуском проверить текущие mtproxy listeners/workloads и сохранить
   подтверждение, что deployment не занимает их ports и не изменяет их
   service.

## VPS preflight: read-only checklist

Этот checklist не меняет firewall, system packages, Caddy, Docker, DNS,
systemd units или repositories. Если команда требует privileged read access,
её запускает owner вручную; не ослабляйте permissions только ради checklist.

### Host, identity и ресурсы

```bash
uname -s
id -u
id -un
free -h
swapon --show
df -h /srv/second-brain
```

Ожидаются Linux, non-root shell для runtime и достаточные RAM/swap/disk для
locked Python environment и frontend build. Недостаток ресурсов — stop
condition, а не повод менять kernel/system packages в рамках deployment.

### Listeners и existing workloads

```bash
ss -ltnp
ss -ltnup
systemctl list-units --type=service --all --no-pager
systemctl list-unit-files --type=service --no-pager
```

В выводе нужно отдельно зафиксировать:

- свободны ли `80` и `443` для Caddy;
- занят ли `8123`, и если занят — слушает ли именно `127.0.0.1:8123` текущий
  Second Brain Web;
- нет ли existing Caddy/Nginx/Apache/httpd conflict;
- какие Docker/Compose workloads активны, если `docker` установлен:

```bash
command -v docker >/dev/null 2>&1 && docker ps --all
```

Текущий mtproxy нельзя считать сохранённым только по имени процесса:
зафиксируйте его фактический unit/process и listener из read-only вывода,
затем повторно проверьте его после Caddy/service smoke. Не выполняйте restart
mtproxy ради проверки.

### Runtime tools и vault access

```bash
python3.14 --version
uv --version
git --version
node --version
npm --version
curl --version
command -v caddy
command -v uv
command -v python3.14
test -r /srv/second-brain/runtime/web.env
test -d /srv/second-brain/second-brain
test -d /srv/second-brain/second-brain-vault
```

`node`/`npm` обязательны только если frontend build выполняется на VPS. Для
этого repository policy используется Node `24.x`, как в текущем GitHub CI; он
должен удовлетворять engines locked frontend dependencies (для текущего
`jsdom` это Node `24.15.0` или новее в линии `24.x`). Node не запускается в
production systemd service.

Проверьте permissions без вывода содержимого env:

```bash
stat -c '%A %a %U:%G %n' /srv/second-brain/runtime /srv/second-brain/runtime/web.env
stat -c '%A %a %U:%G %n' /srv/second-brain/second-brain-vault
```

### Git state

Для обоих repositories ожидаются branch `main`, чистый worktree, отсутствие
незавершённой Git operation и синхронизированный `origin/main`:

```bash
git -C /srv/second-brain/second-brain symbolic-ref --quiet --short HEAD
git -C /srv/second-brain/second-brain status --porcelain=v1 --untracked-files=all
git -C /srv/second-brain/second-brain rev-parse --verify main
git -C /srv/second-brain/second-brain rev-parse --verify origin/main

git -C /srv/second-brain/second-brain-vault symbolic-ref --quiet --short HEAD
git -C /srv/second-brain/second-brain-vault status --porcelain=v1 --untracked-files=all
git -C /srv/second-brain/second-brain-vault rev-parse --verify main
git -C /srv/second-brain/second-brain-vault rev-parse --verify origin/main
```

Любой dirty, detached, diverged, missing-credential или incomplete-operation
state — безусловный STOP. Не исправляйте его автоматически.

## Reproducible manual deploy procedure

Все команды ниже являются будущей manual procedure для owner. Они не
выполнялись в этой задаче. Перед началом owner фиксирует current known-good
application SHA во внешнем deployment record:

```bash
SECOND_BRAIN_ROOT=/srv/second-brain
APP_ROOT="$SECOND_BRAIN_ROOT/second-brain"
VAULT_ROOT="$SECOND_BRAIN_ROOT/second-brain-vault"
RUNTIME_ENV="$SECOND_BRAIN_ROOT/runtime/web.env"
PUBLIC_BASE_URL=https://brain.mikemoore.top

test "$(id -u)" != 0
test "$(git -C "$APP_ROOT" symbolic-ref --quiet --short HEAD)" = main
test -z "$(git -C "$APP_ROOT" status --porcelain=v1 --untracked-files=all)"
test "$(git -C "$VAULT_ROOT" symbolic-ref --quiet --short HEAD)" = main
test -z "$(git -C "$VAULT_ROOT" status --porcelain=v1 --untracked-files=all)"
PREVIOUS_KNOWN_GOOD_SHA="$(git -C "$APP_ROOT" rev-parse --verify HEAD)"
printf 'previous known-good application SHA: %s\n' "$PREVIOUS_KNOWN_GOOD_SHA"
```

### 1. Stop conditions и fast-forward-only update

Сначала проверьте Git operation markers, clean state и branch `main` в обоих
repositories. Затем обновляйте remote-tracking refs без implicit merge:

```bash
git -C "$APP_ROOT" fetch --no-tags origin main
git -C "$VAULT_ROOT" fetch --no-tags origin main
```

Для application release разрешён только fast-forward local `main`:

```bash
git -C "$APP_ROOT" merge-base --is-ancestor main origin/main
git -C "$APP_ROOT" merge --ff-only origin/main
APP_SHA="$(git -C "$APP_ROOT" rev-parse --verify HEAD)"
test -n "$APP_SHA"
```

После fetch vault должен оставаться чистым и синхронизированным. Web release
не выполняет автоматическое обновление vault checkout: если
`main != origin/main`, остановитесь и разберите это отдельно:

```bash
test "$(git -C "$VAULT_ROOT" rev-parse --verify main)" = \
     "$(git -C "$VAULT_ROOT" rev-parse --verify origin/main)"
```

Если application или vault dirty/diverged, есть conflict marker, detached
HEAD, local-only commit или fast-forward не проходит — STOP. Не применяйте
implicit merge, rebase, hard reset, clean, force push или automatic conflict
resolution.

### 2. Locked Python runtime

В application checkout выполните ровно locked sync на поддерживаемом Python:

```bash
cd "$APP_ROOT"
uv sync --locked --python 3.14
```

Если locked sync не проходит, service и Caddy не обновляются.

### 3. Deterministic frontend build из того же SHA

React bundle строится в том же checkout и до service restart. `vite` очищает
`web/dist` согласно текущему `vite.config.ts`; результатом считается только
bundle, созданный после успешного build из recorded `APP_SHA`:

```bash
test "$(git -C "$APP_ROOT" rev-parse --verify HEAD)" = "$APP_SHA"
cd "$APP_ROOT/web"
npm ci
npm run check
npm run build
test -s dist/index.html
cd "$APP_ROOT"
test "$(git -C "$APP_ROOT" rev-parse --verify HEAD)" = "$APP_SHA"
```

`web/dist` не коммитится и не переносится из другого release. Stale bundle,
собранный из неизвестного SHA, является STOP condition. Если `npm ci`, check
или build не проходит, service не обновляется. `npm ci` и build не являются
частью production service; Node runtime в systemd unit не нужен.

### 4. Read-only application validation

Используйте тот же protected env file, что и systemd:

```bash
cd "$APP_ROOT"
uv run --python 3.14 second-brain --env-file "$RUNTIME_ENV" doctor
uv run --python 3.14 second-brain --env-file "$RUNTIME_ENV" vault validate
```

Обе команды должны завершиться с code `0`. Они не создают canonical test note
и не выполняют Safe Write. Не передавайте в application secrets через argv.

### 5. Systemd verification

Сначала проверьте фактический путь к `uv`, service user и права на
`EnvironmentFile`. Если `command -v uv` не равен `/usr/local/bin/uv`, внесите
только это path adjustment во внешний rendered unit, не добавляя credentials.

```bash
command -v uv
sudo install --owner=root --group=root --mode=0644 \
  "$APP_ROOT/deploy/systemd/second-brain-web.service" \
  /etc/systemd/system/second-brain-web.service
sudo systemd-analyze verify /etc/systemd/system/second-brain-web.service
sudo systemctl daemon-reload
sudo systemctl cat second-brain-web.service
```

Проверка должна подтвердить `User=second-brain`, non-root `Group`, указанное
`WorkingDirectory`, существующий protected `EnvironmentFile`,
`Restart=on-failure`, loopback port `8123`, `KillSignal=SIGTERM` и отсутствие
secret assignments в unit/argv. Не используйте `systemctl show` с выводом
полного environment.

### 6. Caddy verification и controlled reload

Скопируйте template во внешний Caddy configuration path после DNS preflight,
сохранив ровно site address и upstream из template:

```bash
sudo install --owner=root --group=root --mode=0644 \
  "$APP_ROOT/deploy/caddy/Caddyfile.example" /etc/caddy/Caddyfile
sudo caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile
```

Если `caddy validate` не проходит, Caddy не reload-ится. После успешной
валидации и подтверждения DNS/ports разрешён только controlled reload:

```bash
sudo systemctl reload caddy
```

Template не содержит wildcard host, arbitrary upstream, TLS verification
disable, permissive CORS или Cloudflare Tunnel. Caddy сохраняет incoming Host
и передаёт стандартные `X-Forwarded-Host`/`X-Forwarded-Proto` к loopback
upstream. Не разрешайте произвольные forwarded headers от public clients и не
выставляйте `FORWARDED_ALLOW_IPS=*`.

Access log включён в JSON и направлен в Caddy service journal.
`log_skip /auth/github*` исключает GitHub OAuth endpoints, включая callback,
поэтому
одноразовые `code`/`state` не попадают в Caddy access log. Application access
log уже отключён текущим `web serve`. Не добавляйте отдельный proxy logger,
который пишет полный callback query.

### 7. Controlled service start/restart

Первый запуск активирует unit только после всех предыдущих gates; последующие
релизы выполняют restart только после нового `APP_SHA` build/validation:

```bash
sudo systemctl enable second-brain-web.service
sudo systemctl restart second-brain-web.service
sudo systemctl is-active --quiet second-brain-web.service
sudo journalctl --unit=second-brain-web.service --no-pager --lines=100
```

В journal сохраняйте diagnostics без env dump, tokens, callback query или
secret values. Если service не active, deployment считается failed.

### 8. Network and unauthenticated smoke

Проверка с VPS:

```bash
curl --fail --silent --show-error http://127.0.0.1:8123/healthz
```

Ожидается JSON с `status` `ok`. Проверка через public authority выполняется
после DNS/TLS:

```bash
curl --fail --silent --show-error \
  "https://brain.mikemoore.top/healthz"
curl --silent --show-error --dump-header - --output /dev/null \
  "http://brain.mikemoore.top/healthz"
```

Вторая команда должна показать HTTP-to-HTTPS redirect с `Location` на
`https://brain.mikemoore.top/healthz`; первая — успешный HTTPS health.

С отдельной external network owner подтверждает, что `127.0.0.1:8123` не
доступен извне. `ss -ltnp` на VPS должен показывать loopback listener и не
показывать public `0.0.0.0:8123` или `[::]:8123`.

Unauthenticated contract:

```bash
curl --fail --silent --show-error \
  "https://brain.mikemoore.top/login"
curl --silent --show-error --dump-header - --output /dev/null \
  "https://brain.mikemoore.top/"
curl --silent --show-error --dump-header - \
  --header 'Content-Type: application/json' \
  --request POST --data '{"query":"health","limit":1}' \
  "https://brain.mikemoore.top/api/search"
```

Ожидания: `/login` отвечает и показывает текущую login surface, `/` делает
redirect на `/login`, а private `/api/*` без session возвращает safe `401` с
`Cache-Control: no-store`. Protected UI/API и vault content не раскрываются.

### 9. OAuth и functional smoke

В этой repository task real authenticated smoke не выполняется:

```text
PRODUCTION_AUTHENTICATED_SMOKE = PENDING_OWNER_EXTERNAL_SETUP
```

После ручного создания OAuth App и размещения runtime values owner проверяет:

- GitHub authorization начинается с exact callback URL;
- разрешается только numeric GitHub user ID `42142321`;
- другой numeric user ID получает `403` без session/identity leak;
- session cookie остаётся `Secure`, `HttpOnly`, `SameSite=Lax`, с bounded TTL;
- logout очищает session, а refresh сохраняет authenticated UI;
- OAuth code/state не появляются в browser, application или Caddy access logs.

После входа owner выполняет только минимальный functional smoke: загрузка GUI,
Search read, read-only diagnostics, простой Advisor request, простой Compare
request и Safe Write `PREPARE` без обязательного `APPLY`. Тестовую canonical
note для smoke создавать нельзя. Реальный write smoke требует отдельного
owner confirmation.

## Failure and manual rollback policy

Deployment fail-closed:

- build, `check`, `doctor` или `vault validate` не проходят — service не
  обновлять;
- systemd verification/start не проходит — сохранить bounded diagnostics без
  secrets и остановиться;
- Caddy validation не проходит — не выполнять reload;
- health, redirect, unauthenticated или последующий auth smoke не проходит —
  считать deployment `FAILED`;
- previous known-good SHA фиксируется до application update.

Автоматический destructive rollback не используется. Этот v1 не применяет
reset/rebase и не пытается переписать `main` назад.

Рекомендуемый manual rollback — отдельный release worktree для уже
зафиксированного `PREVIOUS_KNOWN_GOOD_SHA`:

1. Остановить service и сохранить `systemctl status`/journal без secrets.
2. Проверить, что SHA существует локально и не является недоверенным input.
3. Создать новый adjacent detached release worktree из previous SHA, собрать
   в нём frontend обычной последовательностью `npm ci`, `npm run check`,
   `npm run build` и повторить `doctor`/`vault validate`.
4. После успешных проверок применить временный systemd drop-in только для
   `WorkingDirectory`/`ExecStart` этого release path, выполнить
   `systemd-analyze verify`, затем controlled restart и полный smoke.
5. Сохранить failed release и diagnostics до отдельного owner cleanup. Не
   удалять или перезаписывать неизвестные worktrees автоматически.

Если previous release worktree не был подготовлен заранее, безопасный исход —
остановить service и выполнить manual rebuild из recorded SHA в новом path;
не переводить production `main` назад принудительно. Rollback не откатывает
vault contents и не изменяет DNS. После исправления повторяется весь release
gate с новым exact application SHA.

## Current task boundary

```text
PRODUCTION_AUTHENTICATED_SMOKE = PENDING_OWNER_EXTERNAL_SETUP
PRODUCTION_DEPLOY = NOT_PERFORMED
DNS_CHANGED = NO
VPS_CHANGED = NO
SECRETS_CREATED = NO
second-brain-vault = UNTOUCHED
```
