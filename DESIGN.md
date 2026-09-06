# Second Brain — визуальная система v1

Статус: выбранная visual authority для production design track после issue #108.

Этот документ заменяет текущий visual world как источник визуальных решений. Он
не меняет product semantics, API, vault contract, privacy boundary или
пользовательские операции. Старый Web GUI остаётся evidence и anti-reference:
его задача — показать существующие состояния и content, а не диктовать новый
визуальный язык.

## Тезис

Second Brain — это не dashboard с набором карточек, а локальный cognitive
workspace, в котором сигнал становится проверяемым знанием. Визуальный язык
показывает связи, плотность и маршрут мысли, но никогда не прячет читаемый
контент за атмосферой.

Выбранный мир называется **Cognitive Signal Lattice / Сигнальная решётка**.
Его материальный образ — живой типографический signal field: строгая сетка
глифов и узлов, sparse trace-линии и слои глубины на чёрном ground. Глифы и
линии — функциональная навигация по сигналу, не декоративный cyberpunk HUD.

## Цветовая стратегия

Стратегия — **committed**: near-black ground занимает 30–60% визуального поля,
а violet family несёт состояние, маршрут и фокус. Фиолетовый не является
универсальным background tint: каждая gradient family имеет одну работу.

### Семантические токены

Названия — intent, не обещание готового CSS API. Точные значения можно
переиспользовать в #109, если они сохраняют контраст и тесты.

| Роль | Намерение | Ориентир |
| --- | --- | --- |
| `void` | основной true-black ground, не чистый `#000` | `#07070A` |
| `ink` | спокойная плоскость shell и длинного чтения | `#0E0E15` |
| `basin` | поднятая поверхность для одного смыслового слоя | `#151425` |
| `focus` | выбранный signal, active route, подготовленное действие | `#211A3B` |
| `text-primary` | основной текст и важные labels | `#F4F1FA` |
| `text-secondary` | пояснения и вторичный контент | `#B7B0C8` |
| `text-tertiary` | metadata только при достаточном соседнем контрасте | `#898198` |
| `line-subtle` | hairline separation, никогда не основная рамка | `#302844` |
| `violet-deep` | затемнённый маршрут и depth | `#3B2675` |
| `violet-iris` | основной action/focus accent | `#7658E8` |
| `violet-electric` | active trace и selected node | `#9B7BFF` |
| `violet-lilac` | редкий highlight на тёмном ground | `#D8C7FF` |
| `success` | подтверждённое состояние, дозированно | `#68D9A5` |
| `warning` | требующая проверки или неполная evidence | `#F0C36A` |
| `danger` | destructive/error state, не декоративный accent | `#FF7D92` |

Body text не набирается `violet-electric` или `violet-lilac` на больших
объёмах. Для нормального текста и controls обязателен проверяемый WCAG AA
контраст; цвет никогда не является единственным носителем состояния.

### Семейства градиентов

- **Aurora** — медленный radial слой `violet-deep → transparent` за одним
  крупным active region; не повторять на каждой поверхности.
- **Orbit** — conic/radial смесь `violet-iris` и глубокого индиго вокруг
  signal field; показывает topology и направленность, а не украшает shell.
- **Trace** — узкий linear gradient по маршруту от `violet-electric` к
  прозрачности; применяется к selected path или focus rail.
- **Bloom** — маленький local radial bloom за подтверждённым action; область
  ограничена bounding box события и быстро затухает.
- **Mesh** — максимум три статичных radial layers с различными центрами для
  hero/field; не full-screen animated mesh и не универсальный backdrop.
- **Light streak** — редкий направленный linear streak на переходе контекста;
  это surface-level signal, не бесконечный background animation.

Градиенты должны различаться по surface и purpose. Нельзя заменить все
семейства одним purple-blue SaaS gradient. На слабом GPU каждый слой имеет
неподвижный solid fallback.

## Типографика

Две семейства, две роли:

- **IBM Plex Sans** — UI, body, headings и длинное чтение; выбран за ясную
  форму, хорошую кириллицу и нейтральную инженерную интонацию без
  `Inter-everywhere`.
- **IBM Plex Mono** — UUID/metadata, timestamps, counts, signal labels и
  короткие route markers; mono никогда не используется для длинного body.

Роли:

| Роль | Диапазон | Правило |
| --- | --- | --- |
| `display` | `clamp(2rem, 4vw, 4.5rem)` | один thesis/hero, короткая строка |
| `section` | `1.25–1.75rem` | задаёт слой, не кричит над content |
| `body` | `0.9375–1rem` | readable line-height `1.45–1.6` |
| `label` | `0.6875–0.75rem` | Plex Mono, tracked, только metadata |
| `control` | `0.875–1rem` | не уменьшать ради плотности |
| `micro` | не меньше `0.6875rem` | только secondary metadata, не instructions |

Текстовые колонки ограничиваются примерно 65 символами. Заголовок не должен
становиться декоративным изображением: основная мысль всегда остаётся
копируемым DOM-текстом.

## Отступы, радиусы и глубина

Базовый rhythm: `4 / 8 / 12 / 16 / 24 / 32 / 48 / 64px`. Основные вертикальные
переходы используют удвоенный шаг относительно внутреннего padding. Плотность
может расти в signal field, но не за счёт body text и touch targets.

Радиусы сдержанные: `0` для больших plane boundaries, `6px` для controls,
`10px` для focus basin, `14px` только для modal/drawer. Нет pill-everything,
гигантских rounded containers или вложенных карточек.

Глубина строится в таком порядке: расстояние и группировка, tonal surface,
hairline, затем очень мягкий local bloom. Большая чёрная тень под каждым блоком
запрещена. Один смысловой слой — одна surface; list item по умолчанию является
строкой общей поверхности, а не отдельной карточкой.

## Иерархия поверхностей

1. **Void** — спокойный общий ground, задаёт контраст и направление взгляда.
2. **Workspace plane** — текущая рабочая поверхность: Timeline, Search,
   Self Model, Self Retrieval, Decision Journal, Memory/Growth или Simulate Me.
3. **Signal basin** — один локальный cluster/route, где пользователь видит
   связи и evidence.
4. **Focus state** — selected node, draft review, prepare/diff или explicit
   save confirmation.
5. **System state** — loading/error/permission/empty; они используют тот же
   grammar, но никогда не превращаются в декоративный modal maze.

Shell, navigation и content не раскладываются в сетку одинаковых SaaS-карт.
Primary action остаётся очевидным по placement и label, а не только по glow.

## Иконография и язык линий

Иконка — тонкий 1.5px line glyph с несколькими устойчивыми геометрическими
якорями; optical size обычно 18–20px, touch wrapper минимум 44px. Узел — точка
или короткий crosshair, trace — линия с редкими junctions. Не использовать
rounded-square icon tiles, случайные emoji, толстые filled icons или новую
декоративную iconography ради заполнения пустоты.

Hairlines, crosshairs и registration marks допустимы, когда они объясняют
связь, границу или координату. Линия без семантики считается шумом и удаляется.
Focus ring всегда видим keyboard-пользователю и не заменяется violet glow.

## Атмосферные мотивы

- **Signal field:** редкая сетка координат с observable nodes; node density
  соответствует bounded derived state, а не случайному particle effect.
- **Layer contour:** 1–2 большие arcs/contours показывают переход между
  evidence, interpretation и action; не рисовать spiderweb позади каждой note.
- **Trace residue:** короткие полу-прозрачные traces оставляют маршрут active
  поиска/Timeline; они исчезают, когда маршрут больше не нужен.
- **Grain/noise:** только статичный и очень слабый texture layer, если он не
  ухудшает текст и не создаёт лишние repaint.

Любая схема или signal map должна сохранять текстовую альтернативу и state
semantics. Декоративная сеть не получает `aria` и не должна конкурировать с
content.

## Характер motion

Motion — **точный, тихий, инструментальный**: система подтверждает spatial
relationship и state, а не просит внимания ради «вау».

| Событие | Purpose | Ингредиенты |
| --- | --- | --- |
| focus/selection | state indication | `transform`/`opacity`, 160–200ms, strong `ease-out` |
| drawer/context layer | spatial consistency | `transform`/`opacity`, 240–300ms, `--ease-drawer` |
| signal route | explanation/state indication | CSS `clip-path` или opacity, 180–250ms, `ease-in-out` |
| button press | feedback | `scale(0.97)`, 100–160ms, `ease-out` |
| first successful save | rare delight | один bounded bloom, без particle burst |

Используемые curves:

```css
--ease-out: cubic-bezier(0.23, 1, 0.32, 1);
--ease-in-out: cubic-bezier(0.77, 0, 0.175, 1);
--ease-drawer: cubic-bezier(0.32, 0.72, 0, 1);
```

Не анимировать keyboard shortcuts, frequent Search navigation и content, которое
пользователь читает или редактирует. Не использовать `transition: all`,
`scale(0)`, `ease-in`, бесконечный marquee, непредсказуемый mouse-tracking или
JS-анимацию там, где достаточно CSS. Для interruptible state использовать
transitions, для жестов — spring только при реальной interruptibility.

## Адаптивность и правила mobile

Mobile — не сжатый desktop, а первый класс того же world.

- Целевые проверки: `320`, `360`, `390`, `768`, `1024`, `1280–1600px`.
- Touch target минимум `44×44px`; gap между соседними targets достаточен для
  fat-finger input.
- Использовать `100dvh` и safe-area insets; browser chrome не должен скрывать
  primary action или focus target.
- Desktop rail превращается в компактный top/bottom navigation strip; не
  уменьшать весь текст, чтобы сохранить rail.
- Signal lattice на телефоне становится ordered signal column/strand: nodes
  сохраняют порядок и labels, density графики уменьшается, horizontal overflow
  запрещён.
- Multi-column planes переходят в один поток с ясным reading order; drawer
  занимает доступную ширину с безопасными краями.
- Violet bloom, blur и mesh деградируют до одного static layer при reduced
  motion или слабой производительности; content остаётся видимым без эффекта.
- Пустые, error, permission и settings states используют компактные, но не
  обрезанные блоки; состояние не выражается только цветом.

## Reduced motion и доступность

`prefers-reduced-motion: reduce` убирает transform/position movement, stagger,
parallax и decorative trace drift, сохраняя instant state change и короткие
opacity/color transitions до 160–200ms. Controls и focus order не зависят от
animation completion. Hover-only affordance запрещён на touch; hover rules
ограничиваются `(hover: hover) and (pointer: fine)`.

Каждое значимое состояние получает текст/label/icon/structure, а не только hue.
Keyboard focus, visible error, readable line length, zoom до 200% и logical DOM
order являются acceptance criteria будущих UI tasks. SVG/canvas signal layer
не скрывает DOM fallback.

## Принципы performance budget

- Base experience не требует WebGL, canvas или animation library.
- В одном viewport не более трёх gradient layers; full-screen blur и large
  animated shadows запрещены.
- Animated properties: только `transform`, `opacity` и обоснованный `clip-path`;
  не анимировать layout properties.
- Нет бесконечных частиц; signal field обязан быть bounded и static-by-default.
- Длинный контент и controls видимы до завершения любой entrance animation.
- Heavy effect должен иметь solid fallback и не блокировать input, Search,
  Save preview или error recovery.
- Для каждой surface будущий task проверяет 1440/390 capture, no horizontal
  overflow и reduced-motion variant; performance regression не компенсируется
  красивым screenshot.

## Антипаттерны

- lime/fitness language, YFC-like coaching palette или progress-gamification;
- generic SaaS card grid, cards inside cards, rounded-square icon tile matrix;
- один повторённый purple-blue gradient на всех surfaces;
- neon/cyberpunk cliché, glowing borders everywhere, fake terminal copy;
- `Inter` everywhere, mono body text или tiny low-contrast labels;
- decorative cognitive network without meaningful state or accessible fallback;
- dense graph that removes readable labels, keyboard path or mobile order;
- hover-only actions, hidden focus, color-only semantics;
- unbounded blur, particle, parallax, canvas/WebGL or pointer-tracking effect;
- motion on frequent keyboard action, `transition: all`, `ease-in`, `scale(0)`;
- content hidden behind animation, horizontal overflow, fixed viewport height;
- changing product/API/data-flow semantics under cover of visual redesign.

## Граница реализации

Issue #108 фиксирует только visual truth. #109 переводит intent tokens и
gradient families в implementation primitives; #110 — shell/topology; #112 —
motion primitives; #113–#116 — surfaces, mobile и atmospheric layers; #117 —
performance/accessibility gate; #118 — final design QA и синхронизация этого
документа с shipped UI. Если реализация расходится с этим документом, сначала
фиксируется причина в соответствующем task, а не молча меняется visual authority.
