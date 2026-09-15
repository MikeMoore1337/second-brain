# Production autodeploy

Этот документ описывает автоматический выпуск Second Brain Web после успешного
GitHub Actions workflow `CI` для trusted push в `main`.

Autodeploy не заменяет первый owner-managed production bootstrap из
[`web-production.md`](web-production.md). Он включается только после того, как
существующий production уже имеет healthy `current` release, настроенные
systemd/Caddy/Cloudflare/OAuth и проверенный rollback target.

## Поток выпуска

```text
push/merge -> main
       |
       v
GitHub Actions: CI
       |
       | success + trusted push + exact head SHA
       v
Deploy production
       |
       | pinned SSH host key
       v
second-brain user на VPS
       |
       v
deploy/autodeploy.sh --sha <CI_SHA>
       |
       +-> fetch control repository и определить exact current main
       +-> stale SHA guard
       +-> exact-SHA production env preflight
       +-> vault sync guard
       +-> fast-forward control checkout
       +-> strict candidate classification/recovery или создание worktree
       +-> immutable releases/<SHA> worktree
       +-> recovery-pre-build gate + bounded Python environment reset
       +-> uv sync --locked + exact direct runtime entrypoint gate
       +-> npm ci/check/build/PWA QA
       +-> doctor + vault validate
       +-> final candidate/env/baseline integrity gates
       +-> sudo root-owned release-control helper
       +-> current symlink switch + direct application process restart
       +-> local/public health
       +-> bounded rollback при post-activation failure
```

## Диагностика clean-state guard

До любого `fetch`, fast-forward, candidate creation или activation autodeploy
проверяет control-checkout и sibling vault через `git status --porcelain`.
Если `git status` сам возвращает non-zero, deploy остаётся fail-closed и
выводит только exit code, bounded/normalized stderr и bounded/normalized
stdout/status metadata, если Git что-либо вернул в stdout. Если команда успешно
возвращает dirty metadata, deploy также останавливается и выводит только
bounded porcelain status; содержимое файлов, credentials и environment values
не читаются в output. Диагностический stderr временно сохраняется вне
production repositories и удаляется после чтения.

Таким образом, сообщение о невозможности проверить clean state не означает
автоматическую очистку checkout: reset, clean, overwrite и удаление unknown
production files не выполняются. Любая следующая диагностика должна сначала
классифицировать точную причину (`permissions`, index/worktree/config или
unknown tracked/untracked state), а затем выбрать только доказанный
non-destructive repair либо `HUMAN_REQUIRED`.

## Versioned production env preflight

Каждый deploy читает `deploy/production-env-requirements.conf` из exact target
SHA как data-only Git blob. Контракт имеет `format_version=1` и поддерживает
только строки `required=NAME`, `fixed=NAME=NON_SECRET_VALUE` и
`optional=NAME`. Он не выполняется как shell-код и не может подмениться
содержимым другого release.

Текущий contract требует существующие Web settings и non-secret фиксированные
значения из [Web production runbook](web-production.md), включая:

- `SECOND_BRAIN_VAULT_OPERATION_LOCK_PATH` со значением
  `/srv/second-brain/runtime/vault-sync.lock`;
- `SECOND_BRAIN_WEB_AUTH=github`;
- `SECOND_BRAIN_PUBLIC_BASE_URL=https://brain.mikemoore.top`;
- `SECOND_BRAIN_GITHUB_ALLOWED_USER_ID=42142321`;
- bounded session/OAuth TTL.

Secret variables проверяются только на наличие непустого значения и никогда не
попадают в output. `CLOUDFLARE_ACCOUNT_ID` и `CLOUDFLARE_API_TOKEN` разрешены
как optional settings для явно включённого adapter path, но не требуются
обычному Web deploy.

Перед созданием или reuse candidate preflight проверяет exact external
`/srv/second-brain/runtime/web.env`: это обычный protected file вне обоих
repositories, его owner — service/operator user, mode — `600`, а `runtime/`
доступен только оператору. Parser fail-closed: missing, duplicate,
undeclared, malformed или empty required entry, unsupported quoting, CRLF и
fixed-value mismatch останавливают deploy. Ни `source`, ни `eval`, ни другой
способ выполнения `web.env` не используется. Preflight повторяется сразу
перед candidate filesystem mutation и ещё раз перед activation.

Failure этого gate не создаёт candidate, не переключает `current` и не
перезапускает service. Production values не добавляются в Git; исправление
выполняется owner-ом отдельно в protected `web.env`.

## Direct immutable runtime contract

Production systemd больше не запускает `uv`. После `uv sync --locked --python
3.14` candidate должен содержать exact regular executable
`<candidate>/.venv/bin/second-brain`. Read-only helper проверяет, что:

- candidate и `.venv` — directories без symlink;
- canonical candidate path равен `releases/<TARGET_SHA>`;
- entrypoint — regular executable без symlink и canonical path находится внутри
  exact candidate `.venv`;
- interpreter header entrypoint указывает в exact candidate `.venv/bin`, без
  `..`/`.` path escape;
- текущий known-good release также проходит тот же gate до candidate mutation.

Затем `doctor` и `vault validate` запускаются напрямую этим entrypoint. `uv`
остаётся инструментом build/install и не является production process
supervisor/runtime launcher. Поэтому `ProtectHome=read-only` сохраняется, а
`UV_NO_CACHE=1` не добавляется в `web.env` и не нужен direct runtime.

Если candidate entrypoint отсутствует, не executable, является symlink, либо
его canonical/interpreter path не относится к exact environment, результат —
`RUNTIME_ENTRYPOINT_NOT_READY / HUMAN_REQUIRED` до activation. Такой gate
делает rollback совместимым с тем же runtime contract: active known-good release
без entrypoint не допускается к следующему release operation.

## Interrupted candidate recovery

Candidate создаётся как detached Git worktree
`/srv/second-brain/releases/<TARGET_SHA>`. Если directory отсутствует,
используется обычный `git worktree add --detach`. Если directory уже существует,
он не удаляется и не считается автоматически готовым: exact target helper
классифицирует его read-only.

Reuse разрешён только когда одновременно доказаны ожидаемый путь без symlink,
регистрация в worktree control repository, общая repository identity и
`origin`, detached HEAD, `HEAD == TARGET_SHA == current origin/main`, clean
tracked/non-ignored tree, отсутствие Git operation state, отсутствие
`locked`, `prunable` или неизвестного состояния в worktree registration и
отсутствие кандидата под active `current`. Recovery classifier работает в
фазе `recovery-pre-build`: он перечисляет ignored paths data-only и разрешает
только generated-state contract ниже. После успешной классификации весь
pipeline запускается заново: bounded reset `.venv`, `uv sync --locked`, exact
Python 3.14 bytecode refresh, `doctor`, `vault validate`, `npm ci`, `npm run
check`, `npm run build`, `npm run qa:pwa` и final integrity checks в фазе
`final-post-build`. Наличие старого `dist` или частично созданного `.venv` не
пропускает ни один gate.

### Explicit ignored-state allowlist

Перед reuse candidate helper отдельно перечисляет весь ignored state через
`git ls-files --others --ignored --exclude-standard`. Это data-only операция:
содержимое файлов не читается и найденные paths не выводятся. На
`recovery-pre-build` layout каждого существующего generated root также
проверяется: root не может быть symlink или обычным файлом. Разрешён только
узкий generated-state contract, потому что следующий pipeline сначала
детерминированно пересоздаёт соответствующее состояние. Trust model
фазовая: на `recovery-pre-build` существующее содержимое этих roots считается
discardable только после проверки layout/containment, а на
`final-post-build` — результатом уже завершённого deterministic rebuild.
Внутри такого root filename/suffix heuristic не является security authority:
dependency-owned файл может называться как credential, token, private, secret,
password, key или иметь certificate suffix.

- `.venv/**` — перед `uv sync` выполняется только на exact
  `<candidate>/.venv` путь `uv venv --no-project --clear --python 3.14`. Это
  bounded native uv reset: существующие files/directories удаляются только
  внутри проверенного candidate `.venv`, symlink/non-directory root и
  неподходящий для безопасного reset state останавливают deploy. Затем
  `uv sync --locked --python 3.14` пересоздаёт locked dependency environment;
  поэтому все package-owned files/resources внутри свежего `.venv`, включая
  sensitive-looking names и `.pem`/`.crt`/`.cer`/`.der`, допустимы;
- `web/node_modules/**` — `npm ci` использует clean-install semantics и
  удаляет/пересоздаёт dependency tree из tracked `package-lock.json` до
  check/build; frontend root предварительно не может быть symlink;
- `web/dist/**` — Vite config exact target использует `emptyOutDir: true`, а
  `npm run build` пересоздаёт production artifact до activation;
- `src/**/__pycache__/*.cpython-314.pyc` — только Python 3.14 generated bytecode;
  pipeline принудительно переписывает bytecode tracked `src` через
  `compileall -q -f --invalidation-mode checked-hash` до `doctor` и
  `vault validate`.

Вне approved generated roots `.env`, `.env.*`, `*.env`, credential/key/private/
password/token-like paths, certificates и любой неизвестный ignored path
всегда дают `STOP / HUMAN_REQUIRED`. Filename/suffix classifier вызывается
только после структурного исключения approved generated roots и поэтому не
может ошибочно заблокировать package-owned dependency file внутри них.
`src/**/__pycache__/*.cpython-314.pyc` остаётся отдельным узким contract без
root-reset semantics. Для каждого существующего generated root проверяются
не-symlink layout и canonical containment строго внутри exact candidate; root
или candidate path escape даёт `STOP / HUMAN_REQUIRED`. Helper не удаляет и не
перезаписывает неизвестное ignored state. Final `final-post-build` classifier
повторяет структурный fail-closed contract после полного validation/build,
поэтому activation невозможна без повторной integrity проверки.

Если хотя бы одну проверку нельзя доказать, результат — `STOP / HUMAN_REQUIRED`.
Автоматический deploy не удаляет такой candidate и не выполняет blind cleanup;
directory сохраняется как diagnostics/recovery evidence. Оператор обычно **не
должен SSH-подключаться и удалять candidate вручную**. Сначала сохраните
состояние и bounded log, затем исправьте подтверждённую причину или выполните
отдельную owner-managed Git worktree procedure.

## Cancelled/interrupted deploy и retry

До activation `SIGTERM`, `SIGHUP` (включая разрыв SSH), `SIGINT` и cancellation
GitHub Actions обрабатываются bounded trap/reporting: `current` не меняется,
production service не restart-ится, предыдущий known-good release сохраняется,
deploy lock освобождается kernel/process semantics, а candidate остаётся для
диагностики и следующего retry. Cleanup evidence не выполняется.

Regression case — cancelled run `34526439632` с target SHA
`9e6609f…` (`9e6609fabfee3c935213ecb0a9f6a11c9ec30166`). До hardening run успел
fast-forward control checkout, создать `releases/<SHA>`, выполнить `uv sync`,
`doctor`, `vault validate` и начать frontend checks, но не дошёл до build или
activation. Новый retry использует safe-resume contract; отсутствие
`SECOND_BRAIN_VAULT_OPERATION_LOCK_PATH` останавливает run ещё до candidate
creation.

| Состояние | Поведение |
| --- | --- |
| Target уже active и healthy | idempotent `SUCCESS`/no-op, без rebuild |
| Candidate отсутствует | создать detached worktree и пройти полный pipeline |
| Candidate после interruption доказан recoverable | reuse, полный pipeline заново, затем обычная activation |
| Candidate state/identity не доказуем | `STOP / HUMAN_REQUIRED`, без удаления и activation |
| Target superseded более новым `main` | existing stale-SHA protection: skip без rollback |

После cancellation GitHub Actions `Re-run jobs` повторяет тот же exact tested
SHA. Если candidate проходит strict classifier, ручной VPS cleanup не нужен.
Если classifier возвращает `HUMAN_REQUIRED`, не пытайтесь сделать retry зелёным
удалением directory: сохраните evidence и передайте состояние owner-у.

## Гарантии и stop conditions

- Workflow выключен по умолчанию через repository variable
  `PRODUCTION_DEPLOY_ENABLED`.
- Запуск разрешён только после полного `success` workflow `CI`, вызванного
  `push` в `main` того же repository. PR/fork run не получает production SSH.
- В VPS передаётся exact `workflow_run.head_sha`; deploy не выбирает `HEAD`
  самостоятельно.
- Если к моменту запуска `origin/main` уже новее CI SHA, старый deploy успешно
  пропускается вместо отката production назад.
- `second-brain-vault` автоматически не обновляется. Он обязан быть clean и
  иметь `main == origin/main`; иначе deploy останавливается.
- Если vault нужно обновить после push в его `main`, owner использует отдельный
  [Vault Git Sync & Backup v1](vault-sync.md) exact-SHA workflow. Autodeploy
  остаётся application release operation: он только проверяет vault и не
  выполняет его fetch/fast-forward или backup.
- Изменение tracked `deploy/caddy` или `deploy/root` между active и candidate
  release по-прежнему безусловно блокирует autodeploy до owner-managed
  integration.
- Изменение tracked `deploy/systemd` блокирует autodeploy только до тех пор,
  пока exact target unit не доказан как установленный root-owned regular file
  `/etc/systemd/system/second-brain-web.service` с безопасными permissions и
  exact bytes. Проверка выполняется напрямую по installed state; writable
  marker от deploy user не принимается.
- Systemd gate также требует, чтобы service drop-in directory отсутствовал или
  был пустым безопасным directory. Любой obsolete `10-uv-runtime.conf` или
  неизвестный drop-in даёт `SYSTEMD_CONTRACT_NOT_INTEGRATED / HUMAN_REQUIRED`;
  autodeploy ничего не удаляет.
- После one-time integration future app-only target больше не содержит diff в
  `deploy/systemd` относительно active integrated release и проходит unattended.
  Следующий owner checkpoint нужен только при новом systemd contract diff.
- Candidate создаётся только как detached worktree `releases/<SHA>` и до
  activation проходит locked Python sync, frontend check/build/PWA artifact QA,
  `doctor`, `vault validate` и strict final integrity verification. Existing
  candidate разрешено reuse только после safe-resume classifier и с полным
  повтором pipeline.
- `/srv/second-brain` остаётся root-owned. Пользователю `second-brain` не нужен
  write-доступ к production root или `current` symlink.
- Переключение `current` и restart выполняет только заранее установленный
  root-owned helper `/usr/local/sbin/second-brain-release-control`. Он принимает
  только `activate|rollback` и exact 40-character release SHA.
- До изменения `current` проверяется health текущего known-good release. Новый
  release не используется как автоматический recovery для уже сломанного
  production.
- GitHub `concurrency` не отменяет выполняющийся deploy, а VPS `flock` не даёт
  двум процессам одновременно менять release state.
- Bounded interruption trap не вызывает activation, restart или cleanup до
  начала activation; после её начала состояние считается требующим ручной
  проверки.
- После activation проверяются systemd, loopback `/healthz` и public
  `https://brain.mikemoore.top/healthz`.
- При post-activation failure выполняется только non-destructive rollback
  `current` на предыдущий known-good SHA и controlled service restart. Git
  refs, release directories, vault, Caddy, firewall и DNS не откатываются и не
  очищаются автоматически.
- Failed candidate сохраняется для диагностики. Автоматические `reset`,
  `clean`, `rebase`, `rm -rf` и force operations не используются.

## One-time VPS prerequisites

Сначала полностью выполните owner-managed procedure из `web-production.md` и
убедитесь, что:

1. `/srv/second-brain/current` является symlink вида `releases/<40-char SHA>` на
   healthy release.
2. `second-brain-web.service` установлен, enabled/active и работает из
   `/srv/second-brain/current`.
3. Caddy/Cloudflare route для `brain.mikemoore.top` уже настроен и public
   `/healthz` проходит.
4. `/srv/second-brain/second-brain` и
   `/srv/second-brain/second-brain-vault` имеют unattended read-only доступ к
   своим private GitHub repositories. Autodeploy не создаёт Git credentials.
5. Непривилегированный user `second-brain` может создавать release worktrees в
   `/srv/second-brain/releases` и lock-файл в `/srv/second-brain/runtime`, но
   сам `/srv/second-brain` остаётся root-owned.
6. На VPS доступны `git`, `uv`, Python 3.14, Node 24.x, `npm`, `curl`, `flock`,
   `/usr/bin/sudo` и `/usr/bin/systemctl`.

## Root-owned release-control helper

Autodeploy не получает произвольный root shell и не запускает `sudo ln`,
`sudo mv` или общий `sudo systemctl` напрямую. Owner один раз устанавливает
узкий helper из проверенного `main`:

```bash
sudo install \
  --owner=root \
  --group=root \
  --mode=0755 \
  /srv/second-brain/second-brain/deploy/root/second-brain-release-control \
  /usr/local/sbin/second-brain-release-control

/usr/local/sbin/second-brain-release-control version
```

Ожидаемая версия контракта - `1`.

Helper фиксирован на `/srv/second-brain`, допускает только actions `activate`
и `rollback`, принимает только 40-character lowercase SHA, переключает только
`/srv/second-brain/current` на существующий `/srv/second-brain/releases/<SHA>`
и перезапускает только `second-brain-web.service`.

После проверки owner создаёт отдельное sudoers rule через `visudo`:

```bash
sudo visudo -f /etc/sudoers.d/second-brain-release-control
```

Содержимое:

```text
second-brain ALL=(root) NOPASSWD: /usr/local/sbin/second-brain-release-control *
```

Wildcard разрешает передать helper только аргументы. Сам root-owned helper
повторно валидирует их и не выполняет shell/eval из пользовательского ввода.
Не добавляйте `NOPASSWD: ALL`, shell, package manager, Caddy или произвольные
`systemctl *` permissions.

Изменение repository-версии `deploy/root/second-brain-release-control`
автоматически блокирует следующий deploy, пока owner не установит новую
проверенную версию helper вручную.

## One-time owner-managed systemd integration

Этот checkpoint выполняется после merge exact release и после ожидаемого
`SYSTEMD_CONTRACT_NOT_INTEGRATED / HUMAN_REQUIRED`; implementation task сама
production не меняет. Он не является частью каждого обычного app deploy.

Owner/root должен работать только с exact merged SHA в clean control checkout:

```bash
APP_ROOT=/srv/second-brain/second-brain
SECOND_BRAIN_ROOT=/srv/second-brain
MERGED_SHA=<exact merged main SHA>
CURRENT_LINK="$SECOND_BRAIN_ROOT/current"
SYSTEMD_UNIT=/etc/systemd/system/second-brain-web.service
DROPIN_DIR=/etc/systemd/system/second-brain-web.service.d
OBSOLETE_DROPIN="$DROPIN_DIR/10-uv-runtime.conf"

test "$(git -C "$APP_ROOT" rev-parse HEAD)" = "$MERGED_SHA"
test "$(git -C "$APP_ROOT" rev-parse "$MERGED_SHA^{commit}")" = "$MERGED_SHA"
test -L "$CURRENT_LINK"
test -f "$CURRENT_LINK/.venv/bin/second-brain"
test ! -L "$CURRENT_LINK/.venv/bin/second-brain"
test -x "$CURRENT_LINK/.venv/bin/second-brain"

sudo install --owner=root --group=root --mode=0644 \
  "$APP_ROOT/deploy/systemd/second-brain-web.service" \
  "$SYSTEMD_UNIT"
cmp "$APP_ROOT/deploy/systemd/second-brain-web.service" "$SYSTEMD_UNIT"
test "$(sudo stat -c '%u' "$SYSTEMD_UNIT")" = 0
test "$(sudo stat -c '%a' "$SYSTEMD_UNIT")" = 644
```

До `unlink` проверьте exact obsolete drop-in. При unknown entry, symlink,
неизвестном содержимом или дополнительном drop-in — STOP/HUMAN_REQUIRED.
Удаляется только exact regular file; directory и другие workloads не трогайте:

```bash
if sudo test -L "$DROPIN_DIR"; then
  echo 'STOP / HUMAN_REQUIRED: drop-in directory is a symlink' >&2
  exit 1
fi
if sudo test -e "$DROPIN_DIR"; then
  sudo test -d "$DROPIN_DIR"
  DROPIN_ENTRY="$(sudo find "$DROPIN_DIR" -mindepth 1 -maxdepth 1 -print -quit)"
  if test -n "$DROPIN_ENTRY"; then
    test "$DROPIN_ENTRY" = "$OBSOLETE_DROPIN"
    sudo test -f "$OBSOLETE_DROPIN"
    sudo test ! -L "$OBSOLETE_DROPIN"
    printf '%s\n' '[Service]' 'Environment=UV_NO_CACHE=1' \
      | sudo cmp - "$OBSOLETE_DROPIN"
    sudo unlink -- "$OBSOLETE_DROPIN"
  fi
  test -z "$(sudo find "$DROPIN_DIR" -mindepth 1 -maxdepth 1 -print -quit)"
fi
```

После этого owner выполняет `systemd-analyze verify`, `systemctl daemon-reload`,
controlled `systemctl restart`, local/public `/healthz`, и сохраняет только
bounded `systemctl show` (`ActiveState`, `Result`, `ExecMainStatus`) и journal
evidence без environment dump. Intentional restart не должен показывать
misleading `status=143 / Failed`; `SuccessExitStatus=143` не добавляется как
маскировка unexpected failure. Затем rerun-ится тот же exact failed deploy.

После успешной integration installed unit bytes == tracked target, drop-in state
пуст, и обычные app-only releases снова unattended. Root rollout не меняет
`web.env`, Caddy, Xray, x-ui, MTProxy, SSH, firewall, ports, vault или
`second-brain-vault`.

## Dedicated SSH key для GitHub Actions

Создайте отдельную Ed25519 key pair только для production deploy. Private key
не должен храниться на VPS или в repository. Public key добавляется в
`~second-brain/.ssh/authorized_keys`; рекомендуется запретить forwarding и PTY,
например через OpenSSH option `restrict`:

```text
restrict ssh-ed25519 <PUBLIC_KEY> second-brain-github-actions-production
```

SSH host key нельзя доверять через слепой `ssh-keyscan` внутри workflow.
Сначала получите и независимо проверьте fingerprint VPS host key, затем
сохраните canonical `known_hosts` line. Для нестандартного SSH port формат
такой:

```text
[HOST]:PORT ssh-ed25519 <VPS_HOST_PUBLIC_KEY>
```

Текущий production runbook защищает SSH listener `:25566`; фактический port
нужно проверить на VPS перед настройкой GitHub variables.

## GitHub configuration

Создайте GitHub Environment `production`. Затем настройте:

Repository variables:

```text
PRODUCTION_DEPLOY_ENABLED=false
PRODUCTION_SSH_HOST=<VPS host/IP>
PRODUCTION_SSH_PORT=<verified SSH port>
PRODUCTION_SSH_USER=second-brain
```

Environment secrets:

```text
PRODUCTION_SSH_PRIVATE_KEY=<dedicated private key>
PRODUCTION_SSH_KNOWN_HOSTS=<verified known_hosts line>
```

Не помещайте production SSH private key, OAuth secrets, `web.env`, Origin CA
private key или GitHub repository credentials в Git.

## Включение

Autodeploy включается только после успешного первого manual deploy и проверки
SSH/release-control prerequisites:

```text
PRODUCTION_DEPLOY_ENABLED=true
```

После этого следующий успешный `CI` для push/merge в `main` запустит
`Deploy production`. Для уже существующего актуального `main` можно вручную
перезапустить его исходный успешный push-run `CI`; `workflow_run` снова
проверит exact SHA и выполнит deploy только если этот SHA всё ещё является
текущим `origin/main`.

## Ошибки

Красный `Deploy production` означает, что production не считается успешно
обновлённым. До повторного запуска нужно прочитать bounded workflow log и
устранить конкретный stop condition.

Если failure или interruption произошли до activation, старый `current` и
service не меняются, а candidate сохраняется. Следующий exact-SHA retry сначала
повторяет env preflight и strict candidate classification.
Если failure произошёл после activation, скрипт пытается вернуть предыдущий
known-good release через тот же root-owned helper. Если rollback сам не
проходит health, дальнейшие автоматические mutation прекращаются и требуется
owner intervention.

Существующий failed `releases/<SHA>` не является unconditional blocker: он
переиспользуется только после всех проверок safe-resume contract. Непроверяемый
candidate не переиспользуется и не удаляется автоматически; после диагностики
owner отдельно решает дальнейшую корректную Git worktree procedure.
