# React frontend

Этот каталог содержит production React frontend для задач #141–#142. React
портирует фактически merged Web GUI, а FastAPI остаётся владельцем application,
security и vault semantics.

## Граница

React отвечает только за frontend presentation/build layer. FastAPI остаётся
источником application и security semantics, `second-brain-vault` остаётся
каноническим, а существующие same-origin API contracts не меняются.

После `npm run build` собранный React frontend доступен по корневому `/`.
`/react/` оставлен совместимым alias для того же bundle. Legacy `/static`
entrypoint больше не монтируется FastAPI; старые исходники сохранены только как
исторический parity reference и regression-test fixture.

## Одноразовая настройка и проверки

Из каталога `web`:

```text
npm ci
npm run check
npm run build
```

`package-lock.json` — обязательный lockfile. `dist/` — локально генерируемый
артефакт и не коммитится.

После сборки обычный локальный запуск остаётся прежним:

```text
uv run second-brain --env-file .env web serve
```

Затем открой `http://127.0.0.1:8123/`. Опциональный `npm run dev`
использует только loopback Vite server и проксирует существующие `/api/*` и
`/healthz` на loopback FastAPI; он не становится runtime authority.

В frontend нет browser persistence, polling, внешних assets, новых backend
dependencies, auth/public bind или переноса trusted logic из FastAPI. Review
tokens, confirmation tokens и draft state живут только в памяти страницы.
