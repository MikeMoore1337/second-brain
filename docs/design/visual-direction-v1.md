# Visual Direction v1 — Cognitive Signal Lattice

Статус: GREEN-контракт дизайна для production design track.

Issue #108 — REDESIGN, не cosmetic polish. Production UI здесь не меняется:
этот документ и [`DESIGN.md`](../../DESIGN.md) создают durable visual truth для
последующих #109–#118.

## Бриф и граница решения

Second Brain — локальный Markdown-first cognitive workspace. Его canonical
truth живёт во внешнем vault; Web GUI помогает захватить сигнал, найти note,
проверить draft и явно подготовить Safe Write. Первое визуальное доказательство
должно показать не «набор функций», а переход **signal → relation → verified
knowledge**.

Pinned brief:

- near-black/true-black base; graphite/ink layers only as depth;
- violet/purple primary family с несколькими разными gradient grammars;
- controlled bloom, atmospheric light, cognitive-network motifs;
- premium, intelligent, futuristic, но не cyberpunk cliché и не YFC;
- no lime/fitness language, generic SaaS card grid, Inter-everywhere,
  nested-cards или rounded-square-icon slop;
- first-class 320–390px phones, large phones, tablets и desktop;
- touch targets, dynamic viewport, no horizontal overflow, readable type,
  graceful GPU/battery and reduced-motion degradation;
- три действительно разные концепции, затем один автономно выбранный winner;
- no production UI, frontend framework, product/API/data-flow/security/privacy
  changes.

## Основания для визуальных систем

Перед concept comparison был составлен список семи систем из мира заметок,
редакционной работы, навигации и инструментального интерфейса. Он намеренно
пересекает минимум три material families и не сводит cognitive workspace к
«ещё одному graph view».

| Система | Что переносится в продукт |
| --- | --- |
| Архивный finding aid и индексный каталог | координаты, коды, плотный поиск, deep-link из индекса в объект |
| Печатная plate-секция дизайн-альманаха | регистрация, hairlines, плоскость листа, подпись и provenance |
| Монтажный стол и work print | rail, discrete frames, active marker, отложенные фрагменты на pins |
| Typewriter/ASCII live scene | glyph density как signal intensity, строгая cell-grid, текст как материал |
| Оригами и crease map | recoverable steps, fold/unfold, reversible state и точка возврата |
| Профиль глубокого погружения | слои depth, один маршрут от поверхности к evidence, bounded descent |
| Полевая записная книжка с marginalia | edge annotations, короткие traces, личный маршрут чтения |

Impeccable `concept-seed --scope direction --mode operate` зафиксировал seed
`596c201c` и назначил candidate 4. В соответствии с user brief автономный
режим заменяет интерактивный выбор владельца: назначенный материал становится
одной из трёх полноценных концепций, а итог выбирается по соответствию продукту,
читаемости, реализуемости motion, доступности, адаптации к mobile и отличию от
YFC.

## Три самостоятельных направления

### A. Cognitive Signal Lattice / Сигнальная решётка — назначенный кандидат 4

**Рамка продукта.** Personal operating system, в котором плотность глифов и узлов
показывает bounded signal intensity, а sparse traces объясняют связи между
evidence и interpretation. ASCII/typewriter — исходная discipline, но не
буквальная зелёная terminal aesthetic: ground чёрный, signal violet, текст
остаётся нормальным DOM-контентом.

**Первый экран, desktop 1440×1024.** Узкий navigation rail задаёт current
surface и не выглядит каталогом карточек. В рабочей плоскости сверху одна строка
контекста и primary `Добавить`; под ней — большой signal field с 5–9
обозначенными nodes, одной violet route и readable list/metadata рядом. Внизу
первого viewport — один текущий verified/review state, а не dashboard grid.
Каждый node ведёт к существующей Timeline/Search/Self Model/Diagnostics/
Simulate Me surface, без новых product semantics.

**Маршрут пользователя.** Пользователь видит, где находится и какой signal выбран,
следует по trace к note/evidence, затем переходит к существующему read, draft
preview или explicit prepare action. State marker всегда сопровождается label.

**Фирменное взаимодействие.** Focused node делает короткий route trace: соседние
nodes не исчезают, selected relation подсвечивается, content остаётся stable.
Scrub/snapping допускается только для редких intentional timeline transitions;
частый Search и keyboard navigation меняют состояние мгновенно.

**Распространение по поверхностям.** Одна lattice grammar переводится в Timeline (route),
Search (signal density), Self Model (layers), Self Retrieval (evidence path),
Diagnostics (health markers), Simulate Me (bounded scenario path) и capture/write
surfaces (draft/evidence/provenance).

**Честный риск.** Glyph density может стать шумом, а animated network — дорогим
декором. Контрмера: DOM-first labels, максимум редких traces, static fallback,
никаких random particles и отдельные #117 budgets.

### B. Indexed Plate / Редакционный атлас

**Рамка продукта.** Second Brain как личный редакционный atlas: каждая note — не card,
а plate с provenance, координатой и relation marks. Визуальный мир — matte
black paper, graphite rules, violet registration seal; hierarchy строится
типографикой и пустым полем, не glow.

**Первый экран, desktop 1440×1024.** Сверху тонкая mono index strip с текущим
контекстом и last action. Основной экран — одна широкая plate: крупное название,
короткий readable excerpt, справа/снизу регистрационные marks для type/evidence/
relation. Search и Timeline читаются как редакционные строки с разделителями.
Primary action стоит в одной ясной точке края plate.

**Маршрут пользователя.** Индекс → plate → provenance/relation → read или draft
review. Пользователь ощущает порядок и авторство материала, не потерю в graph.

**Фирменное взаимодействие.** Registration mark мягко замыкает контур выбранной
relation, а plate остаётся на месте. Transition — opacity/clip-path, не zoom и
не тяжёлый page morph.

**Распространение по поверхностям.** Особенно силён для Search, read-only Retrieval,
Decision Journal и Safe Write diff; Timeline получает editorial chronology,
Diagnostics — annotated report.

**Честный риск.** Может стать слишком museum/editorial и недодать живой network
metaphor; на маленьком экране plate легко превращается в длинную статичную
простыню. Требует строгой responsive reflow и deliberate active state.

### C. Signal Rail / Монтаж памяти

**Рамка продукта.** Second Brain как work print: мысли приходят фрагментами, человек
монтирует их в проверяемый маршрут, а deferred fragments остаются доступными на
pins. Black film base и violet/orchid tape mark заменяют fitness progress
language.

**Первый экран, desktop 1440×1024.** Одна горизонтальная rail фиксирует
текущий context; на ней — discrete note/evidence frames, active frame отмечен
violet flag, под rail — compact deferred bin. Secondary detail pane показывает
один выбранный fragment и его next explicit action. Это не tab strip и не
карточная доска: rail и bin — одна topology.

**Маршрут пользователя.** Выбрать frame → прочитать evidence → отметить relation или
подготовить существующий draft/save state → оставить незавершённое на pin.

**Фирменное взаимодействие.** Rail snaps по discrete frame pitch; frame не
останавливается между states. Deferred pin появляется без layout jump и
остаётся reachable. На mobile rail становится vertical strand, не требует
горизонтального scrolling.

**Распространение по поверхностям.** Сильнее всего для Timeline, Decision Journal,
Personal Memory и capture/write; Search и Diagnostics требуют дополнительных
правил, чтобы не выглядеть чужими системами.

**Честный риск.** Temporal metaphor может исказить модель vault и сделать
нелинейное знание похожим на workflow board. Нужно жёстко сохранять relation и
read-only semantics; rail не получает authority над canonical note.

## Сравнение и автономный выбор

Оценка по пятибалльной шкале — это запись design decision, а не user research
claim. 5 означает, что направление само поддерживает критерий при сохранении
product truth; 1 — что потребуется компромисс, а не маленькая полировка.

| Направление | Соответствие продукту | Читаемость | Реализуемость motion | Доступность | Mobile | Отличие от YFC | Итого |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Cognitive Signal Lattice | 5 | 4 | 4 | 4 | 5 | 5 | **27** |
| Indexed Plate | 4 | 5 | 5 | 5 | 4 | 4 | 27 |
| Signal Rail | 4 | 4 | 4 | 4 | 4 | 5 | 25 |

При равном numeric score победитель определяется pinned brief и cross-surface
reach: только Cognitive Signal Lattice одновременно делает memory network,
cognitive layers, knowledge lattice и signal traces функциональными на всех
будущих surfaces, оставаясь mobile-first при описанных degradation rules.

### Выбранное направление: Cognitive Signal Lattice

Это автономный выбор в рамках explicit user authorization; unresolved owner
decision отсутствует.

**Мир.** True-black/ink void, violet signal family, sparse typographic nodes,
registration-like junctions и bounded route traces. Люминесценция локальна,
а контент — непрерывный DOM text.

**Первый экран.** Desktop shell с компактным rail слева и одной workspace
plane. Верхняя context line + `Добавить` action; основной focus — signal field
с 5–9 nodes/labels и одной active route; рядом readable current signal list;
нижняя часть первого viewport показывает один explicit review/prepare state.
Никакой tile grid и никакого декоративного hero вместо рабочего состояния.

**Маршрут пользователя.** Context → signal/node → evidence or current note → existing
read/draft/prepare action. Every route has a textual label and keyboard target.

**Фирменное взаимодействие.** `Signal trace` — bounded CSS-переход на 180–250ms
clip-path/opacity transition for the selected relation. Frequent keyboard/Search
changes are instant; no infinite network animation.

**Распространение по поверхностям.** Timeline is the primary rail, Search exposes density
and relation marks, Self Model exposes layers, Self Retrieval explains route to
evidence, Diagnostics uses stable state markers, Simulate Me uses a bounded
scenario path, and capture/write shows provenance around draft/review/save.

**Честный риск и ограничитель.** The world can drift into unreadable terminal or
cyberpunk. The guardrail is IBM Plex Sans for body, IBM Plex Mono only for
metadata, violet glow under 20% opacity except focus, static fallback first,
and #117 contrast/performance/reduced-motion gates.

## Механический контракт реализации

1. #109 создаёт intent tokens, gradient families, type roles и contrast fixtures
   без framework migration.
2. #110 строит shell и navigation topology; rail must collapse without changing
   route semantics.
3. #112 вводит motion primitives only for named purposes and reduced-motion
   variants.
4. #113–#116 применяют world к capture/write, cognitive surfaces, mobile и
   atmosphere; content and API contracts remain unchanged.
5. #117 verifies contrast, focus, motion, no overflow, GPU budget and graceful
   fallbacks on desktop/mobile.
6. #118 performs final Impeccable + Emil review and synchronizes this contract
   with the implemented `DESIGN.md`.

No prototype or production code is part of #108. Any product flow, security,
privacy, authentication, public bind, deployment or data-flow change exits the
GREEN design scope and requires a separate HUMAN_REQUIRED decision.
