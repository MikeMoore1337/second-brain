# QA-контракт v7

Текущий проход: [v7 — открытые края и компактное завершение](docs/design/evidence/186-v7/README.md).
Канонический визуальный контракт: [`DESIGN.md`](DESIGN.md). PR #187 уже слит
в `main` merge-коммитом `9382cc25a87a29c07c5613b109845304958f665e`; это не
разрешение на deploy. Deploy, VPS, DNS, production-конфигурация, секреты и
`second-brain-vault` остаются вне этой проверки.

## Текущие команды и входы

Команды ниже относятся к production React GUI v7 и запускаются только на
синтетическом loopback-сервере:

```text
uv run python -m web.qa.serve
cd web
npm ci
npm run check
npm run build
SB_QA_OUT=../.local/design-v7 node qa/page-motion.mjs
SB_QA_OUT=../.local/design-v7 node qa/iconography.mjs
SB_QA_OUT=../.local/design-v7 node qa/scene-recording.mjs
```

`SB_QA_CHROMIUM` задаёт путь к локальному Chromium, если Playwright не видит
свою установку. Эти runner’ы проверяют настоящие `Разделы`/`section-dropdown`,
switch `role="switch"` с именем «Анимация», текущие `details/summary`, разделы
и `data-icon`; отсутствие целевого элемента или нулевое число проверок даёт
ошибку. `npm run qa:design`, `node qa/compact-glass.mjs` и `node qa/open-edges.mjs`
остаются отдельными текущими проверками своих контрактов.

Для проверки гонки lazy-изображений runner поддерживает изолированный режим
`SB_QA_IMAGE_DELAY_MS=250 node qa/iconography.mjs`: ответ WebP задерживается,
но условие `complete && naturalWidth > 0` ожидается polling’ом. Негативный
`SB_QA_IMAGE_TIMEOUT_MS=250 SB_QA_FAIL_ICON=timeline node qa/iconography.mjs`
намеренно завершается ошибкой с именем и состоянием недоступной иконки; его
нельзя считать успешным evidence.

В сценарии reduced-motion `page-motion.mjs` сначала меняет media preference на
видимой и работающей сцене, а затем перезагружает тот же изолированный контекст:
текущий `useReducedMotion` получает preference при монтировании, после чего
runner проверяет замену switch в disclosure-навигации русским пояснением.

## Решения по трём runner’ам

| Вход | Решение | Что проверяет сейчас |
| --- | --- | --- |
| `web/qa/page-motion.mjs` | обновлён для v7 | disclosure-навигацию, keyboard focus, switch «Анимация», фактическое `currentTime` одной hero-анимации при stop/resume, отдельные offscreen/reduced-motion состояния |
| `web/qa/iconography.mjs` | обновлён для v7 | локальный статический atlas как provenance и реальные React `data-icon`, видимость, bounded image polling, alt/aria-hidden, lazy-загрузку и иконки меню |
| `web/qa/scene-recording.mjs` | обновлён для v7 | композицию hero, два блока «Память / Развитие», восемь ширин и запись переходов через текущее меню/switch |

Статический atlas внутри `iconography.mjs` — проверка файлов и размеров,
а не браузерное доказательство React-рендера. Исторические runner’ы и их
артефакты v3–v6 сохранены по ссылкам ниже и не переписываются.

## Матрица затрагиваемых контрактов

| Документ / селектор | Реализация | Тест или runner |
| --- | --- | --- |
| `DESIGN.md` v7; `.sections-trigger`, `#section-dropdown`, `role="switch"` «Анимация» | `web/src/section-menu.tsx`, `web/src/page-motion.tsx` | `web/src/test/section-menu.test.tsx`, `web/qa/page-motion.mjs` |
| `details/summary`, текущие `#decision-journal` … `#diagnostics` | `web/src/fold-section.tsx`, `web/src/App.tsx` | `web/src/test/fold-section.test.tsx`, `web/qa/page-motion.mjs` |
| `data-icon`, локальные WebP и логотип | `web/src/icons.tsx`, `web/src/App.tsx` | `web/src/test/iconography.test.tsx`, `tests/test_web_iconography.py`, `web/qa/iconography.mjs` |
| hero `data-moving`, `data-depth`, `#memory`/`#growth` | `web/src/cinematic-hero.tsx`, `web/src/App.tsx` | `web/src/test/cinematic-hero.test.tsx`, `web/qa/scene-recording.mjs` |
| `--sb-*` palette/gradient/focus/motion tokens | `src/second_brain/entrypoints/web/static/app.css`, импорт через `web/src/styles.css` | `tests/test_design_tokens.py`, `tests/test_web_motion.py` (source-level, не browser) |
| bounded atmosphere selectors в `web/src/styles.css` | React stylesheet source | `tests/test_web_atmosphere.py` (source-level, не browser) |

`tests/test_web_atmosphere.py`, `tests/test_design_tokens.py`,
`tests/test_web_motion.py` и `tests/test_web_iconography.py` намеренно читают
исходники/static fixture. Они защищают текстовый контракт и не должны
выдаваться за проверку браузера, физического телефона или screen reader.

## Повторный проход PR #190

Исправления проверены на head `43698f29de5ab1de6361c128f94e234bdef10c0b`
(коммит `43698f2`), база PR — `1814ce74bc9a02a81ccf42d1502945ef206124b9`.
Обычный CI run `34400232997` на этом head завершился успешно для всех четырёх
job: `quality`, `windows-ssl-regression`, `frontend (ubuntu-latest)` и
`frontend (windows-latest)`. PR #190 остаётся открытым; merge и deploy не
выполнялись.

- `iconography.mjs`: 126 проверок в обычном контексте и 126 при задержке WebP
  250 мс; отдельный изолированный сценарий с повреждённой `timeline` ожидаемо
  завершился диагностикой `complete=true, naturalWidth=0` после 300 мс.
- `page-motion.mjs`: 326 проверок на ширинах 320/360/390/430/768/1024/1440/1920;
  одна и та же `brain-region-front:thought-awaken` подтверждена как running,
  paused со стабильным `currentTime`, resumed с продвижением, остановленная
  вне экрана и остановленная reduced-motion. `scene-recording.mjs` прошёл с
  85 проверками и сохранил видео/скриншоты в `.local/design-v7`.
- Локально прошли `npm run check`, `npm run build`, targeted Python-контракты
  (13 passed), `ruff format --check`, `ruff check`, `mypy` и полный Python 3.14
  suite (1867 passed, 10 skipped, 2 warnings). Физический телефон, реальная
  вкладка hidden, screen reader и live/provider операции не проверялись.

## Исторический отчёт v5 / до v7

Ниже сохранены результаты и ограничения прежнего прохода. Его числовые
замеры, старые head’ы и команды не являются текущим v7 evidence; актуальные
команды находятся выше. Ссылки на v5 и v6 оставлены для сравнения.

[Исторические материалы v5: логотип, меню и сквозное движение](docs/design/evidence/186-v5/README.md).

# Проверка смыслового редизайна — Issue #186 / PR #187

Продолжение существующего PR: художественный мозг вместо планеты, связи мыслей
вместо орбит, оригинальные растровые символы разделов и простые служебные знаки.
Onest, чёрно-фиолетовая палитра и реальные сценарии сохранены. Предыдущая версия
зафиксирована в head `793f97d75fb0d426ca108f1c6cfe3a0b6a67f8b7`; её review
не считается проверкой этой доработки.

## Что изменилось и как проверялось визуально

| Аспект | Результат |
| --- | --- |
| Главная композиция | Узнаваемый мозг в три четверти; силуэт сохраняется статичным и на мобильной ширине |
| Окружение | Четыре связи с фрагментами материалов, отдельные импульсы и области света; планета, горы и орбиты удалены |
| Текст | «Сохраняй мысли. Находи связи. Возвращайся к важному»; обычные русские названия разделов |
| Символы | 12 оригинальных символов разделов с двумя оптическими мастерами; 10 простых служебных знаков |
| Рабочие экраны | Крупные символы у заголовков, компактные — в навигации и controls; спокойное чтение и настоящие формы |
| Mobile | Текст и действия над крупной сценой; две связи, два фрагмента, облегчённый свет, постоянный доступ к быстрым действиям |
| Отклонение от исходного коллажа | Onest вместо антиквы, мозг вместо планеты и горизонта — прямые уточнения владельца; нет корпуса телефона, тарифов и фиктивных функций |

Сначала просмотрены desktop/mobile главной сцены, затем иконки в фактических
20/26/64 px и в интерфейсе. Отбракованы четыре неоднородных изображения;
их замены соответствуют общей семье и имеют alpha. После просмотра исправлено
наложение метаданных карточек «Память / Развитие» и увеличен промежуток между
мобильным мозгом и нижней подписью. Итоговые сцены пересняты.
Набор в [иконографии](docs/design/iconography-v1.md), источники и хеши в
[манифесте](docs/design/assets-v4.json).

## Среда и границы данных

Windows; Node 24.19.0; Python 3.14.6; production Vite build из `web/`;
локальный FastAPI 127.0.0.1:8137; изолированный Playwright Chromium
151.0.7922.34, ревизия 1234. Только синтетические данные.
`web/qa/serve.py` подставляет DI Draft/Save/Search/Transcription и блокирует
прочие API до реального обработчика; заполненные дополнительные состояния —
локальные JSON fixtures. Реальные LLM, провайдеры, микрофон и vault не использованы.

Safe Write проверяется через signed review/confirmation и число apply-запросов;
это проверка интерфейса, не реальная запись. Backend покрывается suite проекта.
Нет изменений API, same-origin, доступа, browser storage, production env или
`second-brain-vault`. **env change required: no**.

## Повторные проверки

- 320/360/390/430/768/1024/1440/1920 px: нет горизонтального overflow,
  основные доступные controls не меньше 44×44 px. Дополнительная проверка
  геометрии карточек подтверждает, что описания и метаданные не накладываются.
- На 1440×1000 и 390×844: текст → черновик → правка → предпросмотр →
  полный diff → отдельное подтверждение → успех. До подтверждения 0 apply,
  после ровно 1; редактирование инвалидирует старый план сохранения.
- Поиск → результат → чтение существующей заметки; пустой поиск; URL → черновик;
  синтетический WAV → распознанный текст → ручная правка → черновик: passed.
- Задержанный ответ → loading/disabled → русская ошибка 503 → восстановление
  ввода: passed. Заполненные хронология, модель себя, прогноз, совет/сравнение,
  сбор контекста и диагностика проверены отдельно.
- Полная клавиатурная цепочка от skip-link до сохранения, поиска и чтения;
  видимый фокус; короткий viewport с фокусом поля убирает нижнюю панель.
- axe WCAG 2 A/AA + 2.1 AA: 0 обнаруженных нарушений на проверенных начальных,
  рабочих, успешных и ошибочных состояниях. Это не сертификат полного WCAG.
- CDP подтвердил локальный Onest для латиницы и кириллицы; computed styles
  не содержат serif или зелёных оттенков. Растровый набор дополнительно просмотрен
  визуально: фиолетовый материал, без зелёного.
- Пауза сохраняет часы декоративных CSS-анимаций неизменными; вне экрана —
  остановка. Reduced-motion на desktop/mobile: 0 декоративных CSS-анимаций,
  без параллакса. Desktop pointer-планы имеют разную глубину, mobile — без параллакса.
- Растровые изображения возле текста имеют пустой alt и aria-hidden; самостоятельный
  label покрыт тестом. Дальние крупные иконки не запрашиваются при начальной загрузке.

Frontend: typecheck, production build, **34 Vitest tests** passed.
Ruff format/check, mypy (148 source files) passed.
Полный Python 3.14 suite: **1869 passed, 10 skipped, 2 warnings**, 86.71 с.
Пропуски: запрещённый по умолчанию live smoke, Linux bootstrap и ограничения
Windows/POSIX symlink/FIFO. Предупреждения — существующие deprecation зависимостей.

## Сопоставимые измерения

Свежий baseline снят на 793f97d непосредственно перед этой доработкой.
Условия сохранены: три холодных контекста, 1440×1000, Chromium той же версии,
CPU ×4, задержка 100 мс, download 200000 B/s, upload 100000 B/s,
PerformanceObserver и 2.5 с после load. Замеры выполнялись отдельно от тяжёлых проверок.

| Показатель | До | После |
| --- | ---: | ---: |
| FCP, медиана | 3656 мс | 3652 мс |
| LCP, медиана | 3776 мс | 3984 мс |
| Resource transferSize | 692392 байта | 669333 байта |
| JS, Vite | 414.07 КБ | 413.60 КБ |
| CSS, Vite | 97.56 КБ | 97.26 КБ |
| JS gzip | 122.38 КБ | 122.78 КБ |
| CSS gzip | 18.22 КБ | 18.17 КБ |
| Главные изображения, desktop | 94900 байт | 56482 байта |
| Локальный Onest | 83940 байт | 83940 байт |

FCP практически прежний; загрузка меньше на 3.3%; LCP больше на 208 мс (+5.5%).
Первый вариант мозга давал LCP 4208 мс: оптимизация WebP и alpha сократила
его с 98870 до 56482 байт, сохранив видимые извилины и силуэт.
Ранний preload оказался хуже для FCP и не вошёл в итоговую реализацию.
Оставшиеся 208 мс не скрыты: крупная иллюстрация теперь является кандидатом LCP.
Новые runtime-библиотеки и 3D-движок не добавлены.

requestAnimationFrame измерялся отдельными 4-секундными сериями на живой сцене:

| Условия | Медиана / p95 до | Медиана / p95 после |
| --- | ---: | ---: |
| 1440, CPU ×1 | 16.7 / 33.4 мс | 16.7 / 16.8 мс |
| 1440, CPU ×4 | 33.3 / 33.4 мс | 16.7 / 16.8 мс |
| 390, CPU ×1 | 16.7 / 16.7 мс | 16.7 / 16.7 мс |
| 390, CPU ×4 | 16.7 / 16.7 мс | 16.7 / 16.7 мс |

Это относительное измерение данного Windows/headless окружения, не гарантия
60 fps на физических устройствах и не оценка только по скриншотам.

## Ограничения

Физический телефон, настоящий IME/экранная клавиатура, screen reader,
микрофон и реальные provider/write операции **не проверялись**.
Mobile — браузерная эмуляция. INP полевых пользователей не измерялся.

Ветка visibilitychange покрыта unit-тестом. В предыдущем браузерном проходе
реальное переключение/минимизация на этом хосте оставляло visibilityState=visible;
новая графика сохраняет тот же контроллер. Остановка именно при настоящем скрытии
вкладки **не подтверждена** и не выдаётся за проверку физического устройства.

## Историческое воспроизведение и доставка

Следующие команды сохранены как воспроизводимость исторического отчёта, а не
как единственный текущий v7 вход. Из корня: `uv run python -m web.qa.serve`. Из `web/`:
`npm ci`, `npm run build`, `npx playwright install chromium`,
`npm run qa:design`. Дополнительно: `node qa/iconography.mjs`,
`node qa/scene-recording.mjs`. Для замеров отдельно:
`npm run qa:performance`, `node qa/scene-benchmark.mjs`.
`SB_QA_CHROMIUM` — необязательный путь к тестовому Chromium; production env не нужен.
`SB_QA_PHASE=before` меняет только имя baseline-файла в scene-benchmark.

[Скриншоты, иконки, записи, JSON-отчёты](docs/design/evidence/186-v4/README.md).
Расширенные локальные артефакты: `.local/design-v4/`.
Предыдущие доказательства сохранены в `docs/design/evidence/186-v3/`.

Исторический отчёт относится к PR #187; его старые review/evidence не являются
проверкой этой задачи. GitHub Codex Code Review для этой задачи не запрашивался.
Merge #187 уже состоялся (см. точный SHA в начале файла); deploy по-прежнему
не выполняется.

