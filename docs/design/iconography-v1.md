# Иконография — оригинальная растровая система v4, актуальная для v7

Production React Web GUI, Issue #186 / PR #187. Эта редакция заменяет прежний
Lucide-derived SVG subset. Имена семантического API сохранены в `web/src/icons.tsx`.

## Семья и оптические размеры

Авторские мини-иллюстрации созданы для Second Brain встроенным `image_gen`
9 сентября 2026 года. Материал — глубокий фиолетовый с мягким объёмом,
внутренним светом и лавандовой гранью. Свет сверху слева, ракурс около трёх четвертей.
Служебные знаки почти фронтальны: привычный силуэт важнее сложности материала.

Для каждого из 12 символов разделов создан **отдельный исходный атлас**:
слева подробная иллюстрация, справа упрощённый оптический мастер того же символа.
Правая версия имеет меньше мелких деталей и более широкие плоскости; это не
уменьшенная левая картинка. 10 служебных символов сразу нарисованы простыми.

- Controls и навигация: 18–26 CSS px; компактный мастер 48/96 физических px,
  выбираемый через srcset для 1×/2×. Запас позволяет сохранить антиалиасинг граней.
- Заголовки разделов: 96 CSS px desktop / 72 CSS px mobile; подробный мастер 96/192 физических px для 1×/2×.
- Служебный знак остаётся простым при любом размере.
- После alpha-trim содержимое вписано в 86% квадратного поля: одинаковые поля
  и оптический вес. Размер touch wrapper остаётся минимум 44×44 px.
- Все 68 файлов — локальные WebP с настоящим alpha, без нарисованной шахматной сетки.
  Общий размер набора 247494 байта; это не объём начальной загрузки.
- `loading="lazy"`, `decoding="async"`, явные width/height; Vite
  `assetsInlineLimit: 0`. Крупный набор не встроен в JS и не загружается целиком.
  Браузер может заранее загрузить ближайшие к viewport разделы.
- Никаких CDN, runtime-генерации изображений, API-ключей или новых production-зависимостей.

## Существующие разделы и действия

| Назначение | Символ / локальное имя |
| --- | --- |
| Добавление URL / текста / голоса | Входящий лист и приёмный фрагмент — `add`, `capture` |
| Поиск и открытие найденной заметки | Линза, проявляющая лист — `search`; простая стрелка — `open` |
| Память / сохранённые материалы | Слои с закладкой — `memory`, `text`, `save` |
| Хронология | Три последовательно связанных события — `timeline`, `time` |
| Журнал решений | Развилка с освещённым направлением — `decision` |
| Модель себя | Слои персонального силуэта — `self-model` |
| Развитие | Растущая структура связей — `growth` |
| Прогноз | Два возможных продолжения исходного фрагмента — `simulate` |
| Совет / сравнение, связь, URL | Два материала с мостом — `relation`, `url` |
| Сбор контекста | Несколько материалов в собирающей дуге — `self-retrieval` |
| Диагностика | Панель с индикатором — `diagnostics` |
| Голос | Микрофон — `voice` |
| Закрыть / отменить | Простой X — `close`, `cancel` |
| Раскрыть / свернуть | Шеврон — `expand`; тот же знак развёрнут для `collapse` |
| Обновить / копировать | Круговая стрелка / два листа — `refresh`, `copy` |
| Информация / предупреждение / ошибка | i / треугольник с ! — `info`, `warning`, `error` |
| Подтверждение / результат | Галочка — `success`, `confirm`, `outcome` |
| Совместимость старой остановки сцены | Две полосы / треугольник — `pause`, `play` (aliases; текущий UI использует switch «Анимация») |

Стабильные имена включают некоторые подготовленные ранее алиасы; наличие имени
не создаёт новой функции. Русские подписи остаются обычными: «Поиск», «Модель себя»,
«Журнал решений». Большой мозг не повторяется в разделах. По уточнению владельца компактный
мозг также служит логотипом в шапке.

## Дополнение 13 сентября 2026: уникальные стеклянные иконки разделов

«Аудит прогноза» использует `prospective-audit` (лист с линзой),
«Ретроспективная проверка» — `retrospective` (часы с обратной стрелкой).
Оба символа созданы встроенным `image_gen` по референсам
`relation-detail-192.webp` и `diagnostics-detail-192.webp`: тёмное фиолетовое
стекло, лавандовые грани и верхний левый свет. Они применяются одинаково
в заголовках и меню. Служебные `success` и `refresh` остаются знаками действий.

Файлы находятся в `web/src/assets/icons/`: для каждого нового имени —
`-compact-48.webp`, `-compact-96.webp`, `-detail-192.webp`.
В отличие от исходных атласов, здесь один простой мастер на символ:
96 px переиспользуется для 2× меню и 1× заголовка, без дублирующего файла.
Новая поставка добавляет 6 WebP к историческому набору из 68 файлов.
С явного разрешения владельца нарисованная генератором шахматная подложка
удалена программно по связной внешней области; внутренние стеклянные плоскости
сохранены. Поля — 86%, WebP quality 84 / alphaQuality 100, настоящий alpha.
Новых runtime-зависимостей и настроек окружения нет.

Исходники: `exec-dbfc8a25-d7f9-4513-9354-5a36b7cb27ea.png`
(SHA-256 `6c7a81dcf759ede05200d92b71179839d594f4c262bdffaaecc3524b857f6e3f`)
и `exec-ba1c5b87-01fe-4c40-a0a3-ff1521dafaf1.png`
(SHA-256 `abfab3943a73a794fcff3aa63de86f8ca7b35067fede2b35200f268429a80b3e`).

Запрос для аудита:

```text
Style-transfer edit for Second Brain UI icon set. The two supplied images are STYLE REFERENCES only, showing the exact incumbent dark purple glass material and perspective. Replace their subjects with ONE forecast-audit symbol: an upright thick translucent violet glass document plate with two recessed lines and a small magnifying glass overlapping its lower right corner. A single coherent miniature sculpture, no checkmark. Match references closely: dark transparent amethyst glass, see-through dark broad faces, beveled lavender edges, subtle refraction, pale lilac upper-left highlights, three-quarter perspective, restrained brightness. Not opaque plastic, not rubber, no flat glyph, no metal, no white object. Output one square PNG with TRUE transparent background alpha, no checkerboard, no floor, no cast ground shadow, no glow outside silhouette. Center object within 86 percent of square, generous even transparent padding, easily readable at 48px. No text or labels. This is one icon, not a comparison sheet.
```

Запрос для ретроспективной проверки:

```text
Style-transfer edit for Second Brain UI icon set. Supplied images are STYLE REFERENCES only. Replace subjects with ONE retrospective-review icon: a translucent dark amethyst glass clock disc, two simple clock hands, surrounded on upper left by one thick counterclockwise return arrow with clear triangular arrowhead. Three-quarter view, slight tilt, bevel thickness visible. No square housing, no gauge, no checkmark, no leaves. Distinct silhouette from diagnostics reference. Match reference glass material: transparent deep dark purple broad faces, refracted inner edges, thin lavender beveled rims, restrained pale lilac highlights from upper left. NOT solid plastic or rubber. One cohesive simple sculpture legible at 48px. Output single square PNG with TRUE transparent alpha background, no checkerboard baked in, no floor or cast shadow, no glow outside silhouette. Object centered in 86 percent of square with even transparent padding. No letters or numbers or captions. Not an icon sheet.
```

## Текущий switch «Анимация»

В v7 отдельной кнопки паузы на сцене или в шапке нет. В disclosure-меню
«Разделы» находится единственный `<input type="checkbox" role="switch">` с
доступным именем «Анимация». Состояние `checked` означает включённую
продолжительную декорацию; снятие флажка передаётся в `PageMotion` и останавливает
общую сцену, фоновые слои и эффекты разделов. При системном
`prefers-reduced-motion: reduce` switch заменяется русским пояснением.

`pause` и `play` остаются совместимыми именами в `ICON_NAMES` и asset manifest:
они нужны старым импортам и историческим atlas/evidence, но не являются двумя
текущими UI-действиями. Runner должен проверять реальный switch «Анимация», а
наличие alias само по себе не доказывает отображение кнопки.

## Доступность и состояния

Изображение рядом с текстом получает `alt=""` и `aria-hidden="true"`.
Если компонент используется самостоятельно, `label` даёт русские alt/aria-label.
У icon-only кнопки доступное имя задаётся самой кнопке. Изображение не дублирует
подпись и не подменяет название действия. Disabled/active/focus обозначены
состоянием control, границей и текстом; смена цвета иконки не обязательна.
Иконки постоянно не анимируются. Галочка успеха появляется только вместе
с подтверждённым результатом приложения.

## Источник, обработка и условия использования

Генератор: встроенный инструмент создания изображений OpenAI, без стороннего
платного набора или скачанных изображений. Ни один знак не получен экспортом
Lucide/SVG в PNG. Исходные PNG сохранены инструментом; идентификаторы, SHA-256,
размеры исходников и итоговых файлов — в [assets-v4.json](assets-v4.json).

Общий запрос для символов: прозрачный alpha, два оптических мастера одного
знака рядом, левый подробный и правый упрощённый, тёмно-фиолетовый материал,
лавандовые края, свет сверху слева, единый ракурс, без текста, emoji и других цветов.
Предмет каждого запроса соответствует таблице выше. Для служебных символов:
один простой привычный знак, широкие плоскости, почти фронтальный ракурс.
Для мозга: узнаваемый силуэт в три четверти, полупрозрачные фиолетовые извилины,
мягкий внутренний свет, alpha; без планеты, розовой анатомии и пластиковой игрушки.

Обработка через sharp: разделение двух ячеек, trim прозрачных полей, приведение
полей и размера, WebP quality 84 / alphaQuality 100. Мозг: 480/800 px,
480 px — quality 76 / alphaQuality 90; 800 px — quality 65 / alphaQuality 70. Геометрия символов не заменялась векторными примитивами.
Стандартный SVG используется только для линий и импульсов декоративной сцены.

Ресурсы включены в проект под его Apache-2.0; отдельной лицензии стороннего
icon pack для новых изображений нет. Это описание происхождения, а не гарантия
исключительных авторских прав на сгенерированные изображения.
Историческое ISC notice Lucide сохранено в `third_party/licenses/LUCIDE.txt`
для прежней реализации; новые WebP не являются её производными.
Onest по-прежнему поставляется с SIL OFL 1.1.

[Набор в реальных размерах и экранах](evidence/186-v4/README.md).



## Дополнение v5: логотип и крупные композиции

Для логотипа встроенным image_gen нарисован отдельный упрощённый мозг по
собственной иллюстрации v4: широкие извилины, лавандовый свет, сильный силуэт.
Без SB внутри маленького изображения: рядом остаётся читаемое Second Brain.
WebP 48/96 px, отображение 44 px, настоящая alpha. У логотипа пустой alt,
доступное имя остаётся у ссылки «Second Brain — начало».

Три крупных символа 384×384 получены из подробных исходных мастеров v4,
не из маленьких production-иконок. Отображение 240/208 px; lazy loading,
прозрачные поля и та же палитра. Источники, SHA-256 и точные размеры:
[assets-v5.json](assets-v5.json). Нового стороннего набора или лицензии нет;
условия Apache-2.0 проекта сохранены.
