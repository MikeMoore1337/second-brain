# Доказательства #186 / #187 — мозг и растровая иконография

Production React, локальный синтетический FastAPI, изолированный Chromium.
Mobile — эмуляция 390×844, не физический телефон. Методика и ограничения:
[design-qa.md](../../../../design-qa.md). До — head 793f97d, после — доработка v4.

| Экран | Desktop | Mobile |
| --- | --- | --- |
| До | [1440](before-1440.png) | [390](before-390.png) |
| Главная | [1440](home-1440.png) | [390, верх](home-390.png), [сцена после обычной прокрутки](scene-390.png) |
| Добавление | [1440](capture-1440.png) | [390](capture-390.png) |
| Черновик | [1440](draft-1440.png) | [390](draft-390.png) |
| Предпросмотр | [1440](preview-1440.png) | [390](preview-390.png) |
| Полный diff | [1440](diff-1440.png) | [390](diff-390.png) |
| Успех после подтверждения | [1440](saved-1440.png) | [390](saved-390.png) |
| Поиск | [1440](results-1440.png) | [390](results-390.png) |
| Чтение | [1440](note-1440.png) | [390](note-390.png) |
| Хронология | [1440](populated-timeline-1440.png) | [390](populated-timeline-390.png) |
| Модель себя | [1440](populated-self-model-1440.png) | [390](populated-self-model-390.png) |
| Диагностика | [1440](populated-diagnostics-1440.png) | [390](populated-diagnostics-390.png) |
| Reduced-motion | [1440](reduced-1440.png) | [390](reduced-390.png) |
| Короткая запись сцены, паузы и переходов | [WebM](scene-1440.webm) | [WebM](scene-390.webm) |
| Запись основного рабочего сценария | [WebM](flow-1440.webm) | [WebM](flow-390.webm) |

[Семья в размерах 20/26/64 px](icon-family.png) ·
[Иконки в навигации и карточках](icons-navigation.png).

Дополнительные ширины главной:
[320](home-320.png), [360](home-360.png), [430](home-430.png),
[768](home-768.png), [1024](home-1024.png), [1920](home-1920.png).
Кадры показывают viewport; полный diff остаётся прокручиваемым настоящим текстом.
Записи получены браузером, не собраны из статичных картинок.

Машинные отчёты:
[основные сценарии и восемь ширин](qa-report.json),
[дополнительные состояния](surfaces-report.json),
[движение, ввод, клавиатура, шрифт](motion-input-report.json),
[ленивая загрузка и доступность изображений](iconography.json),
[отсутствие наложений в карточках](card-layout.json).

Замеры:
[загрузка до](before-metrics.json) / [после](after-metrics.json);
[интервалы кадров до](before-frames.json) / [после](after-frames.json).
Полные дополнительные screenshots и промежуточные варианты остаются
в локальном `.local/design-v4/`; в PR включены только полезные итоговые доказательства.

