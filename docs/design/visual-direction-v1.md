# Visual Direction v1 — Cognitive Signal Lattice

Статус: исторический exploratory-документ направления v1. Действующий визуальный
контракт production React GUI описан в [`DESIGN.md`](../../DESIGN.md), а его
принятые v7-доказательства собраны в [`docs/design/evidence/186-v7/README.md`](evidence/186-v7/README.md).
Этот документ сохранён для provenance; описанные ниже Cognitive Signal Lattice,
IBM Plex и ранние rail/plate-ограничения не следует трактовать как обязательные
правила текущего v7.

Issue #108 — REDESIGN, не cosmetic polish. Production UI здесь не меняется:
этот документ и [`DESIGN.md`](../../DESIGN.md) создают durable visual truth для
последующих #109–#118.

## Бриф и граница решения

Second Brain — локальный Markdown-first cognitive workspace. Его canonical
truth живёт во внешнем vault; Web GUI помогает захватить сигнал, найти note,
проверить draft и явно подготовить Safe Write. Первое визуальное доказательство
должно показать не «набор функций», а переход **signal → relation → verified
knowledge**.

Зафиксированный бриф:

- near-black/true-black ground; graphite/ink layers используются только для
  глубины;
- violet/purple — основная family с несколькими различными gradient grammars;
- controlled bloom, atmospheric light и cognitive-network motifs;
- premium, intelligent, futuristic, но не cyberpunk cliché и не YFC;
- без lime/fitness language, generic SaaS card grid, Inter-everywhere,
  nested-cards и rounded-square-icon slop;
- телефоны 320–390px, большие телефоны, tablets и desktop — first-class targets;
- touch targets, dynamic viewport, no horizontal overflow, readable type и
  graceful GPU/battery/reduced-motion degradation;
- три действительно разные концепции, затем один автономно выбранный winner;
- без production UI, frontend framework и изменений product/API/data-flow/
  security/privacy.

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
Каждый node ведёт к существующей Timeline/Search/Self Model/Self Retrieval/
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
Simulate Me (bounded scenario path) и capture/write surfaces
(draft/evidence/provenance).

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
а read-only report states получают annotated report grammar.

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
Personal Memory и capture/write; Search и report states требуют дополнительных
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
а контент остаётся непрерывным DOM-текстом.

**Первый экран.** Desktop shell с компактным rail слева и одной рабочей plane.
Сверху — context line и action `Добавить`; основной focus — signal field с 5–9
nodes/labels и одним active route; рядом — читаемый список текущих signal.
Нижняя часть первого экрана показывает одно явное review/prepare state. Никакой
tile grid и никакого декоративного hero вместо рабочего состояния.

**Маршрут пользователя.** Context → signal/node → evidence или текущая note →
существующее чтение, создание draft или prepare action. У каждого route есть
текстовая label и keyboard target.

**Фирменное взаимодействие.** `Signal trace` — bounded CSS-переход на 180–250ms
через `clip-path`/`opacity` для выбранной relation. Частые изменения через
keyboard/Search выполняются мгновенно; бесконечной network-анимации нет.

**Распространение по поверхностям.** Timeline — основной rail; Search показывает
density и relation marks; Self Model — layers; Self Retrieval объясняет маршрут
к evidence; Simulate Me использует bounded scenario path; Memory/Growth и
capture/write показывают provenance вокруг draft/review/save. Этот список
соответствует текущим shipped Web surfaces и не добавляет новую route semantics.

**Честный риск и ограничитель.** World может уйти в нечитаемый terminal или
cyberpunk. Ограничители: IBM Plex Sans для body, IBM Plex Mono только для
metadata, violet glow с opacity ниже 20% вне focus, static fallback first и
contrast/performance/reduced-motion gates из #117.

## Механический контракт реализации

1. #109 создаёт intent tokens, gradient families, type roles и contrast fixtures
   без смены framework.
2. #110 строит shell и navigation topology; rail складывается без изменения
   route semantics.
3. #112 вводит motion primitives только для названных purposes и варианты для
   reduced-motion.
4. #113–#116 применяют world к capture/write, cognitive surfaces, mobile и
   atmosphere; content и API contracts не меняются.
5. #117 проверяет contrast, focus, motion, no overflow, GPU budget и graceful
   fallbacks на desktop/mobile.
6. #118 выполняет final Impeccable + Emil review и синхронизирует этот contract
   с реализованным `DESIGN.md`.

В #108 нет prototype или production code. Любое изменение product flow,
security, privacy, authentication, public bind, deployment или data-flow выходит
за GREEN design scope и требует отдельного HUMAN_REQUIRED решения.
