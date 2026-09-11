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
       +-> uv sync --locked
       +-> npm ci/check/build/PWA QA
       +-> doctor + vault validate
       +-> final candidate/env/baseline integrity gates
       +-> sudo root-owned release-control helper
       +-> current symlink switch + systemd restart
       +-> local/public health
       +-> bounded rollback при post-activation failure
```

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
отсутствие кандидата под active `current`. После успешной классификации весь
pipeline запускается заново: `uv sync --locked`, `doctor`, `vault validate`,
`npm ci`, `npm run check`, `npm run build`, `npm run qa:pwa` и final integrity
checks. Наличие
старого `dist` или частично созданного `.venv` не пропускает ни один gate.

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
- Изменение tracked `deploy/systemd`, `deploy/caddy` или `deploy/root` между
  active и candidate release блокирует autodeploy. Root-managed integration
  выполняется owner-ом отдельно, после чего новый release можно выпустить
  штатным способом.
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
