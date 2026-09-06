# Design tokens v1 — Cognitive Signal Lattice

Статус: GREEN foundation для production design track #109.

Этот документ фиксирует token foundation, который следует из принятого в #108
направления Cognitive Signal Lattice. Источник runtime-стилей — существующий
`src/second_brain/entrypoints/web/static/app.css`. Изменения не добавляют новые
маршруты, API, persistence, auth, vault side effects или frontend framework.

## Слои токенов

Все новые роли имеют namespace `--sb-`, чтобы theme contract был одним и
проверяемым. Внутри `:root` находятся четыре уровня:

1. primitives — near-black surfaces, text hierarchy, violet signal и
   state colors;
2. RGB primitives — числовые каналы для alpha-композиций без повторения
   цветовых literals;
3. semantic gradients — именованные поверхности и atmospheric families;
4. layout, typography, shape, elevation, focus и motion roles.

Старые `--bg`, `--surface`, `--lime`, `--mint`, `--coral` и аналогичные
theme aliases удалены. Компонентам нельзя добавлять вторую локальную тему или
возвращать эти aliases.

## Цвет и контраст

- `--sb-color-void`, `--sb-color-ink`, `--sb-color-basin` и
  `--sb-color-focus` образуют доминирующую near-black depth scale.
- `--sb-color-accent` и `--sb-color-violet-signal` — signal family; акцент
  используется для действий и focus, а violet signal — для мягких traces и
  highlights.
- `--sb-color-text-primary`, `--sb-color-text-secondary` и
  `--sb-color-text-tertiary` задают читаемую иерархию текста. Фиолетовый не
  используется как основной body text, поэтому контраст текста не зависит от
  glow.
- `--sb-color-success`, `--sb-color-warning`, `--sb-color-danger` и
  `--sb-color-danger-strong` остаются семантическими состояниями, а не
  декоративной темой. Каждый state должен сопровождаться текстовой меткой или
  понятным control state.

Проверка WCAG-контраста и физические device screenshots относятся к final
review #117. Здесь закреплена сама поверхность для такой проверки: новые
цвета и alpha-слои не должны заменяться hardcoded literals в компонентах.

## Gradient families

В stylesheet объявлены и применены именованные семейства:

- `--sb-gradient-aurora` — общий мягкий атмосферный фон страницы;
- `--sb-gradient-orbit` — ограниченный conic signal вокруг hero-orbit;
- `--sb-gradient-trace` — directional trace для entry surface;
- `--sb-gradient-bloom` — локальный focus bloom вокруг orbit core;
- `--sb-gradient-streak` — доступная к переиспользованию light-streak family;
- `--sb-gradient-surface-accent`, `--sb-gradient-surface-violet`,
  `--sb-gradient-surface-success`, `--sb-gradient-surface-danger` —
  semantic state surfaces.

Нельзя заменять эти роли одним повторяющимся purple-blue background, добавлять
случайные particles или использовать gradient как единственный carrier
состояния. На `prefers-reduced-motion` статический fallback остаётся
обязательным; токены не требуют JavaScript.

## Layout, spacing и shape

`--sb-container-max` и `--sb-page-gutter` задают bounded shell; `--sb-layout-min-width`
фиксирует нижнюю границу 320px. `--sb-space-1`…`--sb-space-8` — базовая шкала
4px/8px/12px/16px/24px/32px/48px/64px. `--sb-radius-sm`, `--sb-radius-md`,
`--sb-radius-lg` и `--sb-radius-pill` описывают shape roles, а не
автоматическую округлость каждого контейнера.

`--sb-breakpoint-stack` документирует content-driven threshold для текущего
layout stack. Сам `@media` оставлен с literal `760px`, потому что CSS custom
properties нельзя использовать внутри media feature. Это breakpoint содержания,
а не название устройства; в диапазоне 320px+ layout должен reflow без forced
min-width и горизонтального overflow.

Все primary/secondary controls, включая voice и review actions, получают
`--sb-touch-target` (44px). Это не отменяет проверки реального hit-area на
mobile в #117.

## Typography и depth

- `--sb-font-sans` — IBM Plex Sans с системным fallback для body и UI;
- `--sb-font-mono` — metadata/code role, не основной текст;
- `--sb-type-body`, `--sb-type-control`, `--sb-type-label`,
  `--sb-type-title` — базовые type roles;
- `--sb-line-body`, `--sb-line-tight`, `--sb-line-label` и
  `--sb-measure-readable` — readable measure и line-height contract;
- `--sb-shadow-card`, `--sb-shadow-bloom`, `--sb-shadow-focus` — sparse depth;
  glow не должен заменять границу, label или focus state.

Z-layer roles (`--sb-z-base`, `--sb-z-header`, `--sb-z-overlay`,
`--sb-z-toast`) задают небольшой bounded stack. Новые surfaces не должны
создавать произвольные большие z-index значения.

## Focus и motion

`:focus-visible` использует `--sb-focus-width`, `--sb-focus-offset` и
`--sb-color-accent`; input и custom controls не могут убирать этот indicator.
Motion roles (`--sb-duration-press`, `--sb-duration-short`,
`--sb-duration-panel`, `--sb-ease-*`) пока являются placeholders foundation.
Они не добавляют новые transitions или product behavior: named motion primitives
будут разрешены отдельно в #112 и должны иметь reduced-motion вариант.

## Граница применения

Этот слой делает существующий Web UI готовым к потреблению единого visual
contract. Он не меняет HTML semantics, endpoints, persistence, authentication,
canonical vault или data-flow. Следующие задачи могут мигрировать визуальные
поверхности на эти роли, но обязаны сохранять существующие route semantics и
остановиться перед behavior/security/data-flow изменениями.
