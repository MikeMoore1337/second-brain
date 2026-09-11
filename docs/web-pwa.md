# Web GUI PWA Lite

Статус: реализованный baseline для Issue #207. PWA Lite добавляет
installable shell поверх существующего React Web GUI, но не меняет application,
OAuth, Safe Write или vault boundary.

## Что входит

Production build содержит:

- Web App Manifest с `display: standalone`, `start_url=/`, `scope=/`, русской
  локалью, цветами текущего Second Brain и двумя shortcuts: «Новая заметка»
  (`/#capture`) и «Поиск» (`/#search`);
- branded PNG icons 192×192, 512×512, maskable 512×512, Apple Touch Icon
  180×180, favicon 32×32 и icons для shortcuts;
- service worker на `vite-plugin-pwa`/Workbox с собственной privacy boundary;
- контролируемый offline fallback без авторизованного shell, private data или
  динамического содержимого;
- prompt обновления: новая версия не перезагружает страницу автоматически.

FastAPI раздаёт собранные `/manifest.webmanifest`, `/sw.js`, `/offline.html`,
`/offline.css` и `/icons/*` как отдельные static routes. OAuth middleware
считает их public static assets, но не делает public application surface.

## Граница кэширования

Service worker precache получает только:

- hashed Vite assets из `web/dist/assets`;
- branded static icons;
- `manifest.webmanifest`, `offline.html` и `offline.css`.

HTML-навигация не кэшируется: запрос сначала идёт в сеть. При отсутствии сети
service worker возвращает только `offline.html`, а не ранее загруженный
авторизованный shell. `/api` и `/api/**`, `/login`, `/auth/**` и внешние URL
остаются за пределами `respondWith`, runtime cache и precache. В проекте не
добавляются browser storage, offline sync, Web Push, VAPID keys или cache для
заметок, поиска, OAuth credentials и private AI responses.

Поэтому в offline режиме доступны только брендированная страница состояния и
повторная попытка. Создание, сохранение, поиск, чтение private notes и AI
операции требуют восстановления сети и действующей сессии.

## Установка

Откройте корневой URL приложения после успешной загрузки shell и входа:

- Android Chrome: меню браузера → `Install app`/`Добавить на главный экран`;
- desktop Chromium/Edge: значок установки в address bar или меню браузера →
  `Install Second Brain`;
- iOS Safari: Share → `Add to Home Screen`.

Ожидаемый результат — запуск в standalone window без обычной browser chrome.
`viewport-fit=cover`, safe-area padding и Apple status-bar metadata уже входят
в shell; это особенно важно для iPhone с вырезом и home indicator.

OAuth и private API не становятся доступными без сессии: установка описывает
способ запуска интерфейса, а не новый режим доступа.

## Обновления и восстановление

Новый service worker ждёт завершения текущей версии. Когда он готов, React
показывает компактное уведомление с «Обновить» и «Позже». Только явное
«Обновить» активирует waiting worker; автоматического reload loop нет. Draft,
который живёт в памяти страницы, не перезаписывается неожиданным обновлением.

Если браузер показывает старую версию во время локальной проверки, откройте
DevTools → Application → Service Workers/Cache Storage, выполните `Unregister`
и `Clear site data`, затем заново откройте приложение. В production release
`web/dist` всегда собирается из exact application SHA.

## Локальная проверка

Из каталога `web`:

```text
npm ci
npm run check
npm run build
npm run qa:pwa
```

`qa:pwa` проверяет итоговый manifest, routes shortcuts, PNG dimensions и
signature, head links, service-worker privacy boundary и отсутствие executable
script в offline page. Backend regression tests проверяют public PWA routes и
то, что при GitHub OAuth `/` и `/api/**` остаются защищёнными.

Для ручного frontend preview используйте production `dist` через
`npm run preview -- --host 127.0.0.1 --port 4173`, а в другом окне —
`npm run qa:pwa:browser`. Полный packaged runtime проверяется через
`uv run second-brain web serve` после сборки; FastAPI должен получить тот же
`web/dist` и отдаёт PWA files из него.

## Device status и rollout checkpoint

В рамках Issue #207 проверяются production artifact, local FastAPI routes,
desktop/headless browser behavior и responsive safe-area shell. Physical iOS,
Android и отдельная installed desktop PWA установка требуют owner-run device
check после публикации; они не считаются доказанными browser/mock-TMA тестом.

Production deploy в этой задаче не выполняется. `env change required: no`:
PWA не добавляет переменных окружения, credentials, DNS, OAuth values или
service-manager changes. Перед отдельным deploy owner должен повторно проверить
exact SHA, build, auth smoke, offline boundary и release/rollback contract из
[web production runbook](deployment/web-production.md).

Web Push/VAPID остаются отдельной будущей задачей: они потребуют отдельного
product/security contract и не должны добавляться в этот Lite baseline.
