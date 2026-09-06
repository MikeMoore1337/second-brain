# Iconography v1 — local semantic SVG system

Статус: production icon language для React Web GUI после #142.

## Выбор pack

Основной pack — Lucide. Он выбран внутри Cognitive Signal Lattice по следующим
критериям:

| Критерий | Решение |
| --- | --- |
| Black/violet workspace | тонкая monoline-геометрия не спорит с signal field |
| Геометрия | единый 24×24 viewBox, простые устойчивые anchors |
| Readability | optical 18/20/24px, `currentColor`, 1.5px в shipped UI |
| Mobile | icon не уменьшает touch wrapper; actionable control сохраняет минимум 44px |
| Local-only | subset в `web/src/icons.tsx`, без CDN, icon font и runtime fetch |
| Customization | SVG stroke inherits `currentColor`, cap/join round |
| License | permissive ISC notice сохранён в `third_party/licenses/LUCIDE.txt` |

Tabler и Phosphor рассмотрены как близкие line-family альтернативы. Второй
pack не добавлен: у текущего GUI нет объективного coverage blocker.

## Visual contract

- `viewBox="0 0 24 24"` для каждого glyph;
- stroke-only, `fill="none"`, `stroke="currentColor"`;
- shipped stroke width `1.5`, round linecap и linejoin;
- optical sizes: 18px для navigation/metadata, 20px по умолчанию,
  24px для Capture entry;
- icon + label gap наследует control/rail spacing, без произвольного scaling;
- disabled/muted/active states наследуют цвет родительского control;
- icon-only control обязан иметь русский accessible name;
- decorative SVG получает `aria-hidden="true"`; icon не передаёт critical state
  без readable text;
- touch wrapper остаётся минимум `44×44px`; сам glyph может быть 18–24px;
- no icon animation by default. #112 может добавлять только bounded press,
  refresh или expand cue с preserved reduced-motion behavior.

## Bounded semantic mapping

| Семантика | Local name | Где используется/зарезервировано |
| --- | --- | --- |
| главная навигация | `decision`, `timeline`, `self-model`, `simulate`, `self-retrieval`, `search`, `memory`, `growth` | workspace rail |
| добавить / захват | `add`, `capture` | Capture entry и будущие capture affordances |
| URL / текст / голос | `url`, `text`, `voice` | режимы capture |
| Personal Memory | `memory` | memory route |
| Decision Journal / outcome | `decision`, `outcome` | journal route |
| хронология / поиск | `timeline`, `search` | read-only projections |
| открыть / просмотреть | `open` | card/result affordance |
| Self Model / Self Retrieval | `self-model`, `self-retrieval` | derived surfaces |
| Diagnostics / Simulate Me | `diagnostics`, `simulate` | bounded system surfaces |
| обновить / сохранить / подтвердить | `refresh`, `save`, `confirm` | async and Safe Write controls |
| отменить / закрыть | `cancel`, `close` | destructive/overlay controls |
| раскрыть / свернуть | `expand`, `collapse` | disclosure controls |
| копировать | `copy` | UUID/evidence utility |
| warning / error / success / info | `warning`, `error`, `success`, `info` | readable system states |
| время / дата / relation | `time`, `relation` | evidence metadata |

Названия mapping стабильны и не зависят от русской локализации: язык видимого
label может меняться в #139, semantic icon — нет.

## Production boundary

React bundle является единственным production GUI. Legacy `src/.../static`
файлы остаются parity/reference fixtures, но не подключаются FastAPI после
#142. Поэтому iconography runtime импортирует только локальный `icons.tsx` и
не обращается к сети.
