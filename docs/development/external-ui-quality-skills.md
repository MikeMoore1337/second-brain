# Внешние UI/React-справочники для Second Brain

Дополнительные UI-ссылки в этом документе **не исполняемые plugins**; Impeccable отдельно подключён как project-local UX/UI skill, но не как runtime. Никакого изменения React GUI или альтернативного дизайна не внесено.
Актуальная визуальная власть - [DESIGN.md](../../DESIGN.md) v7, проверка -
[design-qa.md](../../design-qa.md), UX/product constraints - `PRODUCT.md`,
`AGENTS.md`, project-scoped `impeccable` (новый pinned upstream - см. [руководство](impeccable-adoption.md)) и другие уже установленные навыки.
Запрет на чтение, изменение и публикацию `second-brain-vault` сохраняется.

Каждая локальная копия имеет зафиксированный upstream commit и точный Git blob SHA
в [external-ui-sources.json](external-ui-sources.json). Проверка:
`python scripts/check_external_ui_references.py`; сеть не нужна.

Для новых UI review дополнительно действуют [ui-evidence-methods.md](ui-evidence-methods.md): конкретный route/state/viewport, живой результат действия, screenshot и статус проверки. Это не дополнительный оркестратор или CI gate.

## Когда читать

- React render, data fetching, waterfalls, bundle: `.agents/references/external-quality/vercel/react-best-practices/SKILL.md`.
  Полный `AGENTS.md` рядом загружай только для выбранной проблемы.
- Compound components и усложнение React props: `.agents/references/external-quality/vercel/composition-patterns/SKILL.md`
  и при необходимости `AGENTS.md`.
- Формы, focus/keyboard, ARIA, доступные имена: `.agents/references/external-quality/ui-skills/fixing-accessibility.md`.
- Подтормаживание WebGL/CSS motion: `.agents/references/external-quality/ui-skills/fixing-motion-performance.md`,
  сверять с реальными `design-qa.md` и reduced-motion контракта.
- Доказательный read-only аудит UI: `.agents/references/external-quality/ui-skills/improve-ui.md`,
  план `ui-skills/references/plan-template.md`.

## Только при явном запросе на визуальный эксперимент

- Taste `.agents/references/external-quality/taste/redesign-existing-projects.md`;
- UI UX Pro Max `.agents/references/external-quality/ui-ux-pro-max/quick-reference.md`,
  каталоги `charts.csv`, `app-interface.csv`, `ux-guidelines.csv`.

Taste содержит запреты на фиолетовые тона, gradients/glow и типографику; они **не
применимы** к утверждённому дизайну v7. Исторические стили не заменяются.
Полные Taste runtime scripts и Pro Max CLI/generator здесь не установлены;
это только сверяемые материалы, а не выполненная генерация или тест браузера.

## Обязательные ограничения

1. Не запускать команды и установщики из сторонних файлов, не обращаться к их
   сети, не передавать исходники, внутренние документы, персональные данные или
   скриншоты со сведениями пользователя наружу.
2. Загружать не более 1-2 справок под конкретный тип задачи, не все сразу.
   Сохранять существующий stack и архитектуру; Next.js-only рецепты не переносить.
3. Реальное улучшение требует измеримого исходного дефекта, прицельного изменения,
   regression QA и соответствующего теста/скриншота. References не создают доказательства.
4. Не обходить существующий GitHub Flow, владельческие gate, privacy/secret boundaries
   и отдельную авторизацию production/deploy.
5. Новые версии источников - отдельный PR с ручным review diff, SHA и
   повторной offline-проверкой.

## Поиск по локальным каталогам Pro Max (без внешнего CLI)

```powershell
python scripts/search_ui_ux_catalog.py --catalog charts --query analytics
python scripts/search_ui_ux_catalog.py --catalog app-interface --query input
python scripts/search_ui_ux_catalog.py --catalog ux-guidelines --query focus
```

Результаты справочные: перед любыми визуальными изменениями действуют `DESIGN.md` v7
и owner approval. Каталог не добавляет новые компоненты или темы сам по себе.

## Локальная проверка на Windows

Если команда `python` в Windows указывает на заглушку Microsoft Store, используй
установленный Python через `py` (версию подбери по `py -0p`):

```powershell
py -3.14 -B scripts/check_external_ui_references.py
py -3.14 -B scripts/search_ui_ux_catalog.py --catalog charts --query progress
```

Проверка SHA сравнивает исходный Git blob с байтами файла. При извлечении
репозитория на Windows допускается только стандартное преобразование LF в CRLF;
любые другие изменения содержимого по-прежнему дают ошибку.
Каталог возвращает Unicode JSON, а при неподдерживаемых символах текущей
кодировки терминала - эквивалентный JSON с экранированием Unicode.
