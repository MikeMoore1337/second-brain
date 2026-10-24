# Second Brain: применение внешних методов UI QA

Статус: условные справочные инструкции для уже существующих `web/qa`, React 19 + `motion` и утверждённой визуальной системы v7. Не устанавливает скиллы, пакеты или отдельный дизайн.

## Точка применения

| Сценарий | Внешняя методика | Текущая реализация | Новый критерий при review |
|---|---|---|---|
| Новое состояние рабочей поверхности | `daymade/frontend-visual-qa` | `web/qa/surfaces.mjs`, `design-qa.md` | В одном отчёте совпадают точные route, входные фикстуры, viewport, действие, screenshot и результат |
| Движение космоса, PageMotion, интерактивные элементы | `LottieFiles/motion-design-skill`, `iart-ai/web-animation-skills` | `web/qa/page-motion.mjs`, `motion-input.mjs`, `scene-benchmark.mjs` | Сравнить кадры, паузу, reduced-motion, взаимодействие и до/после замеры без потери утверждённой композиции |
| UX критика | `pbakaus/impeccable`, `ibelick/ui-skills` | `DESIGN.md` v7, `impeccable`, уже pinned refs | Только конкретный сбой относительно принятого дизайна, не автоматическая смена стиля |
| React и компоненты | `antfu/skills` для Vite/Vitest; `shadcn-ui/ui`, `beUI` как примеры | `web/package.json`, текущие компоненты | Документация фреймворка и реальный repro; никаких случайных копий примитивов |

## Правило доказательств

1. Перед review зафиксировать commit SHA, требование Issue, текущий `DESIGN.md` v7 и соответствующий `web/qa` сценарий; для новой композиции отдельно получить визуальное решение владельца.
2. Использовать синтетические данные и изолированную тестовую среду. Не читать и не менять `second-brain-vault` из-за UI-аудита.
3. Проверить экран **до и после действия**, включая ошибку/пустое состояние, keyboard/focus, экран 320/390px и desktop там, где это затронуто; не выдавать source-only проверки за реальный браузер.
4. Для движения сохранять анимационный характер v7 и доступность: frame timing, pause/resume, reduced-motion, отсутствие неприятного движения. Убедиться, что изменение не ломает `motion` версии из lockfile.
5. Сопоставить actual screenshot с утверждённым UI; указать конкретный дефект, воспроизведение, тест и исправление. Дополнительный неавторизованный внешний browser/MCP не нужен.
6. Команды по области: `npm --prefix web run qa:design`, `npm --prefix web run qa:performance`, `npm --prefix web run check`. Записывать только **реально полученные** результаты, не заявлять live/mobile, если это имитация.

## Не подключать автоматически

Вторая motion-библиотека (GSAP/Lottie), полные Taste/UI UX Pro Max presets, Superpowers orchestration, удалённые UI libraries и скрипты установки: есть собственные маршруты, `motion`, CSS/WebGL и документация v7. Если конкретный пробел подтвердится на реальном сценарии, предложить отдельный небольший PR с лицензией, lockfile и отрицательными тестами.

Уже проверенные локальные справочники - `docs/development/external-ui-quality-skills.md` и `external-ui-sources.json`. Новые идеи из https://github.com/daymade/claude-code-skills/tree/main/frontend-visual-qa и https://github.com/LottieFiles/motion-design-skill изложены здесь самостоятельно; чужой текст/код не скопирован.
