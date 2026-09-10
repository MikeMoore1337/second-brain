# Production Web Deployment v1.1

Статус этого документа — repository-side contract для ручного owner-managed
deployment. Он не выполняет SSH, DNS/VPS mutation, установку Caddy, создание
OAuth App, создание secrets, выдачу Origin CA сертификата, real login или
production deploy. Cloudflare и защищённые существующие workloads в этой
repository task не изменяются.

## Границы и архитектура

Целевой public authority — `https://brain.mikemoore.top`. Схема deployment:

```text
Cloudflare edge HTTPS :443
   |  Origin Rule для HTTPS brain.mikemoore.top -> origin port 8444
VPS Caddy HTTPS :8444
   |  explicit Origin CA tls cert/key
127.0.0.1:8123
   |
Second Brain Web (`second-brain web serve`)
   |
second-brain-vault

Cloudflare edge HTTP :80
   |
VPS Caddy HTTP :80 -> explicit portless HTTP-to-HTTPS redirect
```

Сохраняются следующие invariants:

- приложение слушает только `127.0.0.1:8123`; публичным интерфейсом владеет
  только Caddy;
- существующий Xray сохраняет свой listener на `:443`; Caddy не занимает его и
  не останавливает, не перезапускает и не перенастраивает Xray;
- Caddy принимает HTTPS origin traffic на `:8444` через site-scoped address
  `https://brain.mikemoore.top:8444`; это внутренний origin port, а не часть
  public authority;
- TLS терминируется Caddy с owner-managed Origin CA cert/key, а HTTP на `:80`
  explicit site block перенаправляет на public HTTPS без `:8444`;
- public URL, OAuth callback, browser links и Cloudflare hostname остаются
  `https://brain.mikemoore.top`, без explicit port;
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
- [Caddy site snippet](../../deploy/caddy/brain.mikemoore.top.caddy);
- [базовый VPS/bootstrap runbook](vps.md);
- [существующий OAuth contract](web-auth.md).

## Production layout

Рекомендуемый root для этого шаблона — `/srv/second-brain`:

```text
/srv/second-brain/
├── second-brain/                         # control/source checkout
├── second-brain-vault/
├── releases/
│   └── <APPLICATION_SHA>/                # clean detached release worktree
├── current -> releases/<ACTIVE_SHA>
└── runtime/
    └── web.env
```

`second-brain/` — только control/source checkout для fetch и fast-forward
обновления. Production service никогда не работает из этого mutable path.
Каждый candidate строится в отдельном clean detached worktree
`releases/<APPLICATION_SHA>`, а `current` — стабильная ссылка на последний
активированный release. `second-brain-vault/` остаётся отдельным обычным Git
worktree. `runtime/web.env` находится вне обоих repositories, не копируется в
vault и не добавляется в Git.

Systemd template использует:

- `User=second-brain` и `Group=second-brain`;
- `WorkingDirectory=/srv/second-brain/current`;
- protected `EnvironmentFile=/srv/second-brain/runtime/web.env`;
- `ExecStart` через абсолютный путь к `uv`, `--python 3.14`, `--no-sync`,
  текущий CLI и явный `--port 8123`.

Путь `/usr/local/bin/uv` в template — безопасный placeholder для абсолютного
пути. До установки unit его нужно заменить на фактический результат
`command -v uv` на выбранном VPS. В unit нет secrets, OAuth values или
Cloudflare credentials. `current` должен быть symlink на готовый release; unit
не запускает fetch, sync, npm или build.

`--no-sync` намеренно запрещает изменять или обновлять environment при старте
service. Locked dependency installation выполняется отдельным release step в
candidate worktree: `uv sync --locked --python 3.14`.

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
2. Создать control checkout, private sibling vault, `releases/` и
   `runtime/web.env` с mode `600` в layout выше. `current` должен отсутствовать
   при first deployment или уже быть symlink на release. Не создавать файл с
   production values в repository.
3. Создать DNS `A` и/или `AAAA` record для `brain.mikemoore.top`, указывающий
   на owner-verified VPS origin; конкретный DNS provider этим repository не
   предполагается. DNS record должен оставаться proxied через Cloudflare и не
   должен указывать public traffic на `8123` или публиковать `:8444`.
4. До TLS enablement выполнить read-only VPS preflight ниже: `:443` может быть
   занят защищённым Xray, `:8444` обязан быть свободен для Caddy, а `:80` —
   доступен для HTTP redirect. Если `:8444` занят, STOP; другой origin port
   автоматически не выбирается.
5. Вручную создать GitHub OAuth App с такими значениями:

   - Homepage URL: `https://brain.mikemoore.top`;
   - Authorization callback URL:
     `https://brain.mikemoore.top/auth/github/callback`.

   В issue, PR и Codex не передаются Client ID, Client Secret или Session
   Secret. Values размещаются только в protected `web.env`.
6. Установить и проверить Caddy 2.x, поддерживающий текущую директиву
   `log_skip` (Caddy 2.8+), а также открыть его configuration path и service
   journal operator-ом. Site snippet должен объявлять
   `https://brain.mikemoore.top:8444` и explicit
   `tls <cert_file> <key_file>`; global Caddy options менять нельзя.
   Cloudflare Tunnel и wildcard hosts в этот contract не входят.
7. Вручную получить Origin CA certificate, покрывающий exact hostname
   `brain.mikemoore.top`, и сохранить cert/key только на VPS в путях из Caddy
   snippet. Repository task не создаёт сертификат и не получает его значения.
8. Перед запуском проверить и зафиксировать текущие Xray `:443`, x-ui `:2096`,
   MTProxy `:8443`, SSH `:25566` и Reminder Bot Docker workload. Deployment не
   занимает их ports и не изменяет, не перезапускает и не перенастраивает их.

### Cloudflare owner actions: documented, not performed here

Следующие действия выполняет только owner вручную после отдельного deployment
authorization. В этой repository task не вызываются Cloudflare API/UI и не
изменяются DNS, SSL/TLS или Rules:

1. В Cloudflare DNS сохранить proxied `A`/`AAAA` record
   `brain.mikemoore.top`, направленный на owner-verified VPS origin. Не
   фиксировать публичный IP в application/runtime artifact; при operator
   preflight использовать внешний placeholder `VPS_ORIGIN_IPV4`.
2. В Cloudflare SSL/TLS установить и проверить `Full (strict)`. Не использовать
   Flexible SSL или промежуточный `Full` без strict verification.
3. В Cloudflare Origin CA выпустить сертификат exact hostname
   `brain.mikemoore.top`. Owner размещает его вне Git в:

   ```text
   /etc/caddy/certs/brain.mikemoore.top.pem
   /etc/caddy/certs/brain.mikemoore.top.key
   ```

   Приватный key не вставляется в issue, PR, shell history, logs или
   diagnostics; значения сертификата и key в repository не появляются.
4. В Rules -> Origin Rules создать правило только для HTTPS hostname с
   expression:

   ```text
   http.host eq "brain.mikemoore.top" and ssl
   ```

   Destination port: `8444`. Не задавать Host header override и SNI override.
   Для документационного примера допустима та же expression во внешних
   скобках: `(http.host eq "brain.mikemoore.top" and ssl)`.
5. Не-HTTPS запросы (`ssl` не совпадает) оставить на origin `:80`, чтобы Caddy
   выполнил redirect на `https://brain.mikemoore.top/...` без `:8444`.
6. После фактического owner-managed deploy выполнить Cloudflare Trace и
   внешний functional request: HTTPS hostname должен идти на origin `:8444`,
   HTTP — на origin `:80`, а public Location/authority не должен раскрывать
   `:8444`. Trace до deploy в этой task не выполняется.

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

- `:443` может быть занят protected Xray: это ожидаемое состояние, которое
  нельзя исправлять остановкой, restart или изменением Xray;
- `:8444` должен быть свободен для Caddy. Если он занят любым process/service,
  это безусловный STOP; не выбирайте другой port автоматически;
- `:80` должен быть доступен Caddy для HTTP listener и redirect;
- занят ли `8123`, и если занят — слушает ли именно `127.0.0.1:8123` текущий
  Second Brain Web;
- сохранены ли protected listeners `:2096` x-ui, `:8443` MTProxy и `:25566`
  SSH, а также Reminder Bot Docker workload;
- нет ли existing Caddy/Nginx/Apache/httpd conflict на `:80` или `:8444`;
- какие Docker/Compose workloads активны, если `docker` установлен:

```bash
command -v docker >/dev/null 2>&1 && docker ps --all
```

Текущий mtproxy нельзя считать сохранённым только по имени процесса:
зафиксируйте его фактический unit/process и listener из read-only вывода,
затем повторно проверьте его после Caddy/service smoke. Не выполняйте restart
mtproxy ради проверки.

Состояние firewall проверяйте только read-only. Не включайте UFW и не меняйте
firewall rules в рамках этого deployment: firewall hardening — отдельный
checkpoint, а `ufw inactive` не является основанием для `ufw enable`. Не
публикуйте `8123` наружу.

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
test -d /srv/second-brain/releases
if [ -e /srv/second-brain/current ] || [ -L /srv/second-brain/current ]; then
    test -L /srv/second-brain/current
    readlink /srv/second-brain/current
fi
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
RELEASES_ROOT="$SECOND_BRAIN_ROOT/releases"
CURRENT_LINK="$SECOND_BRAIN_ROOT/current"
RUNTIME_ENV="$SECOND_BRAIN_ROOT/runtime/web.env"
PUBLIC_BASE_URL=https://brain.mikemoore.top

test "$(id -u)" != 0
test "$(git -C "$APP_ROOT" symbolic-ref --quiet --short HEAD)" = main
test -z "$(git -C "$APP_ROOT" status --porcelain=v1 --untracked-files=all)"
test "$(git -C "$VAULT_ROOT" symbolic-ref --quiet --short HEAD)" = main
test -z "$(git -C "$VAULT_ROOT" status --porcelain=v1 --untracked-files=all)"
if [ -L "$CURRENT_LINK" ]; then
    PREVIOUS_CURRENT_TARGET="$(readlink "$CURRENT_LINK")"
    PREVIOUS_KNOWN_GOOD_SHA="${PREVIOUS_CURRENT_TARGET#releases/}"
    test "$PREVIOUS_CURRENT_TARGET" = "releases/$PREVIOUS_KNOWN_GOOD_SHA"
    test "${#PREVIOUS_KNOWN_GOOD_SHA}" -eq 40
    case "$PREVIOUS_KNOWN_GOOD_SHA" in
        (*[!0-9a-f]*)
            echo 'current does not point to a hexadecimal release SHA' >&2
            exit 1
            ;;
    esac
    test -d "$RELEASES_ROOT/$PREVIOUS_KNOWN_GOOD_SHA"
elif [ -e "$CURRENT_LINK" ]; then
    echo 'current exists but is not a symlink; stop' >&2
    exit 1
else
    PREVIOUS_CURRENT_TARGET=""
    PREVIOUS_KNOWN_GOOD_SHA=""
fi
printf 'previous known-good application SHA: %s\n' "$PREVIOUS_KNOWN_GOOD_SHA"
```

### 1. Stop conditions и fast-forward-only update

Сначала проверьте Git operation markers, clean state и branch `main` в обоих
repositories. Затем обновляйте remote-tracking refs без implicit merge:

```bash
git -C "$APP_ROOT" fetch --no-tags origin main
git -C "$VAULT_ROOT" fetch --no-tags origin main
```

Для application release разрешён только fast-forward local `main`. Этот
control checkout не является build target:

```bash
git -C "$APP_ROOT" merge-base --is-ancestor main origin/main
git -C "$APP_ROOT" merge --ff-only origin/main
APP_SHA="$(git -C "$APP_ROOT" rev-parse --verify HEAD)"
test "${#APP_SHA}" -eq 40
case "$APP_SHA" in
    (*[!0-9a-f]*) echo 'application SHA is not hexadecimal' >&2; exit 1 ;;
esac
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

### 2. Immutable release worktree и locked Python runtime

Создайте candidate только как отдельный detached worktree, адресованный
recorded exact SHA. Если путь уже существует, остановитесь: не переиспользуйте
и не очищайте старый release автоматически.

```bash
mkdir -p "$RELEASES_ROOT"
CANDIDATE_RELEASE="$RELEASES_ROOT/$APP_SHA"
test ! -e "$CANDIDATE_RELEASE"
test ! -L "$CANDIDATE_RELEASE"
git -C "$APP_ROOT" worktree add --detach "$CANDIDATE_RELEASE" "$APP_SHA"
test "$(git -C "$CANDIDATE_RELEASE" rev-parse --verify HEAD)" = "$APP_SHA"

cd "$CANDIDATE_RELEASE"
uv sync --locked --python 3.14
test "$(git -C "$CANDIDATE_RELEASE" rev-parse --verify HEAD)" = "$APP_SHA"
```

`releases/<SHA>` не является active path до отдельной activation step. Если
worktree registration, locked sync или последующая проверка не проходит,
оставьте candidate и diagnostics для owner review; `current` и работающий
service не изменяйте.

### 3. Deterministic frontend build внутри candidate release

React bundle строится только в immutable candidate worktree. `vite` очищает
только `web/dist` этого candidate, поэтому неудачный или прерванный build не
может удалить bundle из active release:

```bash
test "$(git -C "$CANDIDATE_RELEASE" rev-parse --verify HEAD)" = "$APP_SHA"
cd "$CANDIDATE_RELEASE/web"
npm ci
npm run check
npm run build
test -s dist/index.html
cd "$CANDIDATE_RELEASE"
test "$(git -C "$CANDIDATE_RELEASE" rev-parse --verify HEAD)" = "$APP_SHA"
```

`web/dist` не коммитится и не переносится из другого release. Stale bundle,
собранный из неизвестного SHA, является STOP condition. Если `npm ci`, check
или build не проходит, active release и service не обновляются. `npm ci` и
build не являются частью production service; Node runtime в systemd unit не
нужен.

### 4. Read-only application validation из candidate

Используйте тот же protected env file, что и systemd, но запускайте проверки
из candidate и без implicit dependency sync:

```bash
cd "$CANDIDATE_RELEASE"
uv run --python 3.14 --no-sync second-brain --env-file "$RUNTIME_ENV" doctor
uv run --python 3.14 --no-sync second-brain --env-file "$RUNTIME_ENV" vault validate
```

Обе команды должны завершиться с code `0`. Они не создают canonical test note
и не выполняют Safe Write. Не передавайте в application secrets через argv.

### 5. Systemd verification

Сначала проверьте фактический путь к `uv`, service user и права на
`EnvironmentFile`. Устанавливайте unit только из уже проверенного candidate
artifact. Если `command -v uv` не равен `/usr/local/bin/uv`, внесите только
это path adjustment во внешний rendered unit, не добавляя credentials.
Проверка не выполняет restart и не меняет `current`.

```bash
command -v uv
sudo install --owner=root --group=root --mode=0644 \
  "$CANDIDATE_RELEASE/deploy/systemd/second-brain-web.service" \
  /etc/systemd/system/second-brain-web.service
sudo systemd-analyze verify /etc/systemd/system/second-brain-web.service
```

Проверка должна подтвердить `User=second-brain`, non-root `Group`, указанное
`WorkingDirectory=/srv/second-brain/current`, существующий protected
`EnvironmentFile`,
`Restart=on-failure`, loopback port `8123`, `KillSignal=SIGTERM` и отсутствие
secret assignments в unit/argv. Не используйте `systemctl show` с выводом
полного environment.

### 6. Caddy topology, preserve-existing integration и validation

Second Brain поставляет только site snippet
`deploy/caddy/brain.mikemoore.top.caddy`. Он никогда не является заменой
глобального `/etc/caddy/Caddyfile`. Перед любым изменением определите фактическую
топологию и config path из owner-managed Caddy service. Если path отличается,
задайте его в `CADDY_CONFIG`; не предполагайте exclusive ownership. Сохраните
конфигурацию без вывода credentials в issue, PR или diagnostics:

Site snippet является self-contained и объявляет только собственные listener
адреса: HTTPS `https://brain.mikemoore.top:8444` и отдельный HTTP
`http://brain.mikemoore.top`. Никакие global Caddy options для переноса HTTPS
port не требуются или не изменяются; это предотвращает изменение listener/default
port других Caddy sites.

Полный tracked site contract:

```text
https://brain.mikemoore.top:8444 {
    log {
        output stderr
        format json
    }
    log_skip /auth/github*
    tls /etc/caddy/certs/brain.mikemoore.top.pem /etc/caddy/certs/brain.mikemoore.top.key
    reverse_proxy 127.0.0.1:8123
}

http://brain.mikemoore.top {
    redir https://brain.mikemoore.top{uri} 308
}
```

HTTP `redir` — explicit deterministic portless redirect на public authority;
он не зависит от automatic HTTPS redirect. `:80` остаётся стандартным HTTP
listener для explicit `http://` site, а `:8444` существует только как
Second Brain origin listener и не появляется в public `Location`.

Site snippet сохраняет upstream `127.0.0.1:8123`, JSON access-log policy и
explicit custom TLS:

```text
tls /etc/caddy/certs/brain.mikemoore.top.pem /etc/caddy/certs/brain.mikemoore.top.key
```

Это только logical paths. Owner заранее размещает соответствующий Origin CA
cert/key вне repository; реальные значения, private key и ACME credentials не
попадают в Git. При отсутствии или неверном чтении cert/key — STOP. Не
используйте `tls_insecure_skip_verify`, отключение TLS verification, `tls
internal` или wildcard certificate для другого hostname.

```bash
command -v caddy
CADDY_CONFIG=/etc/caddy/Caddyfile
CADDY_SITE_DIR=/etc/caddy/sites.d
if sudo test -d /etc/caddy; then
    sudo find /etc/caddy -maxdepth 2 -type f -print
fi
if sudo test -f "$CADDY_CONFIG"; then
    sudo sed -n '1,240p' "$CADDY_CONFIG"
else
    echo 'Caddyfile is absent: evaluate explicit first-install path' >&2
fi
```

Если Caddy уже использует owner-managed imported site directory, owner должен
указать именно существующий directory и существующий `import` glob из
глобального config. Только в этой ветке установите отдельный snippet внутрь
этого directory; сначала проверьте, что exact hostname и `:8444` не определены
там уже другим site block. Global config не изменяйте: self-contained snippet
не требует server-wide port/default mutation, поэтому другие Caddy sites
сохраняют свои listeners:

```bash
# Пример значений; замените их на фактический существующий import из inspection.
CADDY_SITE_DIR=/etc/caddy/sites.d
CADDY_IMPORT_GLOB=/etc/caddy/sites.d/*.caddy
sudo test -d "$CADDY_SITE_DIR"
sudo awk -v expected="import $CADDY_IMPORT_GLOB" \
  'BEGIN { found = 0 }
   /^[[:space:]]*#/ { next }
   { line = $0; sub(/^[[:space:]]+/, "", line); sub(/[[:space:]]+$/, "", line)
     if (line == expected) found = 1 }
   END { exit !found }' "$CADDY_CONFIG"
sudo install --owner=root --group=root --mode=0644 \
  "$CANDIDATE_RELEASE/deploy/caddy/brain.mikemoore.top.caddy" \
  "$CADDY_SITE_DIR/brain.mikemoore.top.caddy"
```

Для новой или подтверждённо пустой Caddy installation разрешён только explicit
first-install path. Сначала создайте site directory и snippet, затем вручную
создайте глобальный config с единственным owner-managed import через
`sudoedit`; это не команда замены существующего файла:

```bash
CADDY_SITE_DIR=/etc/caddy/sites.d
sudo install --directory --owner=root --group=root --mode=0755 "$CADDY_SITE_DIR"
sudo install --owner=root --group=root --mode=0644 \
  "$CANDIDATE_RELEASE/deploy/caddy/brain.mikemoore.top.caddy" \
  "$CADDY_SITE_DIR/brain.mikemoore.top.caddy"
sudoedit "$CADDY_CONFIG"
```

Содержимое нового глобального `/etc/caddy/Caddyfile` в этом first-install
path — только import site directory:

```text
import /etc/caddy/sites.d/*.caddy
```

Если существующий глобальный config содержит другие workloads и не использует
imports, это отдельный operator integration checkpoint. Не используйте
`install`, `cp`, `tee` или redirect поверх этого config. Сначала сохраните
backup и осмотрите файл, затем owner вручную добавляет import существующего
или нового site directory с сохранением всех текущих routes. Не добавляйте
server-wide port/default overrides:

```bash
BACKUP_DIR=/var/backups/caddy
sudo install --directory --owner=root --group=root --mode=0700 "$BACKUP_DIR"
sudo cp --preserve=mode,ownership,timestamps "$CADDY_CONFIG" \
  "$BACKUP_DIR/Caddyfile.$(date -u +%Y%m%dT%H%M%SZ)"
sudoedit "$CADDY_CONFIG"
```

После ручной интеграции snippet directory и import должны быть проверены
вместе с существующими routes, а фактические cert/key paths должны быть
readable Caddy service user. Если безопасно добавить import без потери existing config
нельзя — STOP, не overwrite. Если exact hostname или `:8444` конфликтует
с существующей site definition — STOP.

Во всех ветках `caddy validate` выполняется на фактическом полном config и
является обязательным gate перед reload:

```bash
sudo caddy validate --config "$CADDY_CONFIG" --adapter caddyfile
```

Если `caddy validate` не проходит, deployment останавливается и Caddy не
reload-ится. Сам controlled reload выполняется после activation и успешной
локальной проверки service в шаге 8. Не используйте отдельный partial config
для подмены global topology.

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

### 7. Atomic-ish activation of the candidate

Переключайте `current` только после успешных `uv sync`, frontend `check` и
`build`, `dist/index.html`, `doctor`, `vault validate`, systemd verification и
combined Caddy validation. `releases/` и `current` должны находиться на одной
filesystem. Временный symlink и `mv -T` дают короткую atomic-ish замену
symlink entry без удаления active release:

```bash
test -d "$CANDIDATE_RELEASE"
test "$(git -C "$CANDIDATE_RELEASE" rev-parse --verify HEAD)" = "$APP_SHA"
SWITCH_LINK="$SECOND_BRAIN_ROOT/.current.$APP_SHA.next"
test ! -e "$SWITCH_LINK"
test ! -L "$SWITCH_LINK"
sudo ln -s "releases/$APP_SHA" "$SWITCH_LINK"
sudo mv -T -- "$SWITCH_LINK" "$CURRENT_LINK"
test -L "$CURRENT_LINK"
test "$(readlink "$CURRENT_LINK")" = "releases/$APP_SHA"
test "$(git -C "$CURRENT_LINK" rev-parse --verify HEAD)" = "$APP_SHA"
```

Если `current` отсутствовал, это создаёт его впервые. Если `current` был
symlink на previous known-good release, его target остаётся доступен в
`releases/` для rollback до окончания всех smoke. `current` — только symlink:
если на его месте обнаружен обычный файл или directory, stop вместо замены.
Не используйте `ln -sfn`, `rm`, `git reset`, rebase или force operation.

### 8. Controlled service start/restart и Caddy reload

Первый запуск активирует unit только после всех предыдущих gates; последующие
релизы выполняют restart только после нового `APP_SHA` build/validation:

```bash
sudo systemctl daemon-reload
sudo systemctl enable second-brain-web.service
sudo systemctl restart second-brain-web.service
sudo systemctl is-active --quiet second-brain-web.service
sudo journalctl --unit=second-brain-web.service --no-pager --lines=100
curl --fail --silent --show-error http://127.0.0.1:8123/healthz
sudo systemctl reload caddy
```

`systemctl reload caddy` выполняется только после успешного combined
`caddy validate` из шага 6 и успешного local health. В journal сохраняйте
diagnostics без env dump, tokens, callback query или secret values. Если
service не active, local health не проходит или Caddy reload завершается с
ошибкой, deployment считается failed.

### 9. Network and unauthenticated smoke

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
Ни один public URL или redirect не должен содержать `:8444`: этот port
используется только между Cloudflare edge и VPS origin Caddy по Origin Rule.

После owner-managed deploy отдельно проверьте Cloudflare Trace для exact
hostname. HTTPS (`ssl`) должен resolve в origin port `8444`, HTTP — в origin
port `80`; Trace не должен показывать Host header или SNI override. Если
Cloudflare SSL/TLS не подтверждён как `Full (strict)` или Origin CA certificate
не совпадает с exact hostname, deployment считается `FAILED`.

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

### 10. OAuth и functional smoke

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

- `:8444` занят, exact Origin Rule отсутствует/не совпадает, либо Caddy
  certificate/key не читаются — не продолжать и не выбирать другой port;
- build, `check`, `doctor` или `vault validate` не проходят — service не
  обновлять и `current` не переключать;
- systemd verification/start не проходит — сохранить bounded diagnostics без
  secrets и остановиться;
- Cloudflare state не `Full (strict)`, TLS verification отключена, firewall
  изменён вне отдельного checkpoint, либо protected workload отличается от
  preflight — считать deployment `FAILED` и не продолжать;
- Caddy validation не проходит — не выполнять reload;
- health, redirect, unauthenticated или последующий auth smoke не проходит —
  считать deployment `FAILED`;
- previous known-good target фиксируется до candidate activation и сохраняется
  в `releases/` до успешного полного smoke.

Автоматический destructive rollback не используется. Этот v1 не меняет
production `main`, не удаляет releases и не выполняет repo-wide cleanup.

Rollback для уже активированного release — это только возврат stable `current`
к сохранённому previous known-good release и controlled restart. Он не требует
нового build, не заменяет systemd `WorkingDirectory` drop-in и не использует
reset/rebase/force operation:

```bash
test -n "$PREVIOUS_KNOWN_GOOD_SHA"
test -d "$RELEASES_ROOT/$PREVIOUS_KNOWN_GOOD_SHA"
test "$(git -C "$RELEASES_ROOT/$PREVIOUS_KNOWN_GOOD_SHA" rev-parse --verify HEAD)" = \
     "$PREVIOUS_KNOWN_GOOD_SHA"
ROLLBACK_LINK="$SECOND_BRAIN_ROOT/.current.$PREVIOUS_KNOWN_GOOD_SHA.rollback"
test ! -e "$ROLLBACK_LINK"
test ! -L "$ROLLBACK_LINK"
sudo ln -s "releases/$PREVIOUS_KNOWN_GOOD_SHA" "$ROLLBACK_LINK"
sudo mv -T -- "$ROLLBACK_LINK" "$CURRENT_LINK"
test "$(readlink "$CURRENT_LINK")" = \
     "releases/$PREVIOUS_KNOWN_GOOD_SHA"
sudo systemctl restart second-brain-web.service
sudo systemctl is-active --quiet second-brain-web.service
curl --fail --silent --show-error http://127.0.0.1:8123/healthz
```

Не удаляйте failed candidate или previous release автоматически: они нужны для
diagnostics и повторяемого rollback. При first deployment, когда `current`
отсутствовал и `PREVIOUS_KNOWN_GOOD_SHA` пуст, rollback target не существует;
при failure после activation остановите service, сохраните diagnostics и
разберите новый release вручную. Rollback не откатывает vault contents и не
изменяет DNS/Caddy topology.

## Current task boundary

```text
PRODUCTION_AUTHENTICATED_SMOKE = PENDING_OWNER_EXTERNAL_SETUP
PRODUCTION_DEPLOY = NOT_PERFORMED
DNS_CHANGED = NO
VPS_CHANGED = NO
SECRETS_CREATED = NO
CLOUDFLARE_CHANGED = NO
XRAY_CHANGED = NO
MTPROXY_CHANGED = NO
FIREWALL_CHANGED = NO
second-brain-vault = UNTOUCHED
```
