# Настройки страницы GitHub

Файлы README и SVG доставляются через PR. **About, Topics и Social preview хранятся в настройках GitHub, а не в Git**; их нужно применять отдельно с правами администратора.

## Рекомендуемое описание About

> Second Brain - открытая система личных знаний на Markdown: безопасное сохранение, поиск и когнитивные модели. Данные под вашим контролем.

## Topics

`second-brain`, `personal-knowledge-management`, `knowledge-base`, `obsidian`, `markdown`, `zettelkasten`, `fastapi`, `python`, `react`, `privacy-first`, `self-hosted`, `cognitive-twin`.

## Homepage

В [production runbook](../deployment/web-production.md) указан `https://brain.mikemoore.top`, но приложение требует авторизацию владельца. Это не публичная демонстрация. Оставьте Homepage пустым либо укажите URL только с пониманием, что гости не увидят личные данные и рабочий интерфейс.

## Social preview

Готовый векторный исходник: [social-preview.svg](social-preview.svg) (1280×640). Экспортируйте его в PNG без изменения пропорций; в **Settings → General → Social preview → Edit** загрузите полученный PNG. GitHub не использует SVG как загруженную обложку Social preview.

На главной странице репозитория откройте редактирование **About**, заполните Description и Topics, сохраните и проверьте отображение в светлой/тёмной теме и на мобильном экране.
