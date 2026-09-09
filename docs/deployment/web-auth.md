# Web авторизация Second Brain

Web GUI поддерживает owner-only режим GitHub OAuth для будущего публичного
размещения. Это не многопользовательская система: доступ разрешён только
GitHub numeric user ID `42142321`. `MikeMoore1337` показывается в интерфейсе
как display login, но username не используется для решения о доступе.

## Режимы

Локальный loopback workflow по умолчанию остаётся без OAuth:

```text
SECOND_BRAIN_WEB_AUTH=disabled
```

Для публичного запуска оператор должен явно выбрать:

```text
SECOND_BRAIN_WEB_AUTH=github
```

В этом режиме приложение завершается с configuration error, если отсутствует
`SECOND_BRAIN_PUBLIC_BASE_URL`, `SECOND_BRAIN_GITHUB_CLIENT_ID`,
`SECOND_BRAIN_GITHUB_CLIENT_SECRET`, `SECOND_BRAIN_GITHUB_ALLOWED_USER_ID` или
достаточно сильный `SECOND_BRAIN_SESSION_SECRET`. Public base URL обязан быть
HTTPS origin. По умолчанию session TTL равен 12 часам, OAuth state TTL — 10
минутам; оба значения bounded и могут быть изменены указанными переменными.

Полный список переменных:

```text
SECOND_BRAIN_WEB_AUTH=github
SECOND_BRAIN_PUBLIC_BASE_URL=https://brain.mikemoore.top
SECOND_BRAIN_GITHUB_CLIENT_ID=<owner-managed OAuth App client id>
SECOND_BRAIN_GITHUB_CLIENT_SECRET=<owner-managed OAuth App client secret>
SECOND_BRAIN_GITHUB_ALLOWED_USER_ID=42142321
SECOND_BRAIN_SESSION_SECRET=<random value, at least 32 bytes>
SECOND_BRAIN_SESSION_TTL_SECONDS=43200
SECOND_BRAIN_OAUTH_STATE_TTL_SECONDS=600
```

Сгенерировать сильный session secret можно локально или на VPS, не записывая
его в repository history:

```powershell
uv run python -c "import secrets; print(secrets.token_urlsafe(32))"
```

На Linux VPS:

```bash
python3.14 -c 'import secrets; print(secrets.token_urlsafe(32))'
```

Сохраните результат только в отдельном owner-managed runtime env-файле с
permissions `600`, который передаётся процессу Web GUI через service manager
(например, systemd `EnvironmentFile`). Не передавайте secret через CLI options
и не добавляйте его в `.env.example`, `deploy/env.example`, Git, issue или PR.
`deploy/env.example` намеренно остаётся vault-only: его строгий bootstrap
contract принимает только `SECOND_BRAIN_VAULT_PATH`. Auth variables должны
инжектироваться в окружение процесса отдельно; при таком запуске их не нужно
добавлять в bootstrap env-файл.

## Owner-only GitHub OAuth App

Владелец вручную создаёт GitHub OAuth App со следующими значениями:

```text
Application name: Second Brain
Homepage URL: https://brain.mikemoore.top
Authorization callback URL: https://brain.mikemoore.top/auth/github/callback
```

Приложение запрашивает только OAuth authorization request без `repo`, `email`,
`org` и других дополнительных scopes. После server-side exchange access token
используется только для получения authenticated user ID и затем отбрасывается;
он не становится browser session и не попадает в cookie, localStorage,
sessionStorage, vault, логи или persistent application storage.

Собственная session cookie — HttpOnly, SameSite=Lax, Path `/`, Secure в
`github` mode, подписана HMAC и содержит только stable owner ID и bounded
timestamps. OAuth state одноразовый, короткоживущий и хранится в bounded
process-local state store только в виде digest.

Команда `web serve` отключает Uvicorn access log, чтобы query string callback
с code/state не попадал в application log. Если внешний reverse proxy или
service manager ведёт собственный access log, настройте его без query string
для OAuth endpoints.

## External checkpoint

Создание OAuth App, получение и размещение production secrets, DNS,
Caddy/VPS configuration и production deployment являются owner/external
environment checkpoint. Они намеренно не выполняются автоматически в этой
задаче. До отдельного разрешения достаточно проверить локальные deterministic
тесты и PR; production deployment не выполняется.
