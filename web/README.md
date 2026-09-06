# Основа React frontend

Этот каталог — ограниченная область реализации задачи #141. Он добавляет
воспроизводимую React foundation surface, не заменяя текущий legacy GUI до
завершения #142.

## Граница

React отвечает только за frontend presentation/build layer. FastAPI остаётся
источником application и security semantics, `second-brain-vault` остаётся
каноническим, а существующие same-origin API contracts не меняются.

Собранная foundation surface доступна локально по `/react/`. Корневой `/`
намеренно продолжает отдавать текущий plain frontend: полный parity-перенос всех
merged GUI surfaces выполняется только в #142.

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

Затем открой `http://127.0.0.1:8123/react/`. Опциональный `npm run dev`
использует только loopback Vite server и проксирует существующие `/api/*` и
`/healthz` на loopback FastAPI; он не становится runtime authority.

В foundation нет browser persistence, polling, внешних assets, новых backend
dependencies, auth/public bind или переноса trusted logic из FastAPI.
