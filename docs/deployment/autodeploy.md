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
       +-> fetch control repositories
       +-> stale SHA guard
       +-> vault sync guard
       +-> immutable releases/<SHA> worktree
       +-> uv sync --locked
       +-> npm ci/check/build
       +-> doctor + vault validate
       +-> current symlink switch
       +-> systemd restart
       +-> local/public health
       +-> bounded rollback при post-activation failure
```

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
- Изменение tracked `deploy/systemd` или `deploy/caddy` между active и candidate
  release блокирует autodeploy. Root-managed integration выполняется owner-ом
  отдельно, после чего новый release можно выпустить штатным способом.
- Candidate создаётся только как detached worktree `releases/<SHA>` и до
  activation проходит locked Python sync, frontend check/build, `doctor` и
  `vault validate`.
- До изменения `current` проверяется health текущего known-good release. Новый
  release не используется как автоматический recovery для уже сломанного
  production.
- GitHub `concurrency` не отменяет выполняющийся deploy, а VPS `flock` не даёт
  двум процессам одновременно менять release state.
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
5. Непривилегированный user `second-brain` может создавать release worktrees,
   lock-файл в `runtime` и атомарно менять `current` symlink.
6. На VPS доступны `git`, `uv`, Python 3.14, Node 24.x, `npm`, `curl`, `flock`,
   `/usr/bin/sudo` и `/usr/bin/systemctl`.

### Минимальное sudo permission

Autodeploy не устанавливает systemd unit и не меняет Caddy. Единственная
privileged mutation - restart уже существующего service. Owner вручную создаёт
узкое sudoers rule через `visudo`:

```text
second-brain ALL=(root) NOPASSWD: /usr/bin/systemctl restart second-brain-web.service
```

Не добавляйте `NOPASSWD: ALL`, shell, package manager, Caddy или произвольные
`systemctl *` permissions.

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
SSH/sudo prerequisites:

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

Если failure произошёл до activation, старый `current` и service не меняются.
Если failure произошёл после activation, скрипт пытается вернуть предыдущий
known-good release. Если rollback сам не проходит health, дальнейшие
автоматические mutation прекращаются и требуется owner intervention.

Существующий failed `releases/<SHA>` не переиспользуется и не удаляется
автоматически. После диагностики owner отдельно решает, удалить ли его через
корректный Git worktree lifecycle или оставить как deployment evidence.
