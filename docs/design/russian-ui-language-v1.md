# Русский интерфейс v1

Статус: production language contract для React Web GUI после #139.

Основной пользовательский интерфейс, состояния, ошибки и accessibility-текст
по умолчанию написаны на русском языке. Machine-readable значения API, имена
маршрутов, коды ошибок и внутренние идентификаторы остаются неизменными; на
экране они показываются только с русской подписью и только когда нужны для
проверки или аудита.

## Единый glossary

| Внутренний термин | Видимый русский термин |
| --- | --- |
| Personal Memory | Личная память |
| Decision Journal | Журнал решений |
| Outcome / Outcome Observation | Результат / Наблюдение результата |
| Personal Timeline | Личная хронология |
| Self Model | Модель себя |
| Self Retrieval | Сбор контекста |
| Simulate Me | Прогноз |
| Diagnostics | Диагностика |
| Refresh | Обновить |
| Unknown time | Время неизвестно |
| Not assessed | Не оценивалось |
| Dry run | Без записи / пробная подготовка |
| Apply / Confirm | Применить / Подтвердить |
| Search | Поиск |
| Safe Write | Безопасное сохранение |

`URL`, `UUID`, `API`, `JSON`, `Git`, `Markdown`, `YAML`, `HTTP`, названия
технологий, policy/version strings и literal machine codes могут оставаться
без перевода. У каждого такого значения должна быть русская подпись: например,
`Код ошибки: SELF_MODEL_INVALID_REQUEST` или `Канонический UUID`.

## Error presentation

Сервер сохраняет стабильное поле `error.code`, но его `error.message` — это
короткое безопасное русское описание проблемы. В интерфейс не попадают
исключения, traceback, provider details, абсолютные пути и сырые сообщения
внутренних библиотек. Если внешний ответ не содержит русское human-readable
сообщение, React показывает локальный русский fallback.

## Accessibility и mobile

`web/index.html` объявляет `lang="ru"`. Кнопки, labels, placeholders,
loading/error/empty/success states и `aria-label` используют тот же glossary.
Русский текст должен переноситься на ширине 320, 360, 390 и 430px без
горизонтального overflow; технические identifiers остаются вторичным detail.

React bundle — единственный production GUI. Legacy static tree остаётся
reference fixture и не является отдельной локализуемой production-поверхностью.
