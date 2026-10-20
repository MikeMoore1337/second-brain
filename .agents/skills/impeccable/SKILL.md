---
name: impeccable
description: "Primary UX/UI design review, critique, shaping and polish for Second Brain React interface, with DESIGN.md v7, PRODUCT.md, design QA and private vault boundaries overriding upstream defaults. Not for backend-only or private notes."
metadata:
  version: "4.7.0"
  origin: "pbakaus/impeccable@dad8dbb3aac21ad1fa236c110315ef4abece23e8"
---

# Impeccable - Second Brain (проверенное обновление)

Ранее здесь находился Impeccable 4.2.1. Этот короткий project-scoped вход **заменяет предыдущую редакцию инструкций** и загружает неизменённые upstream playbooks версии 4.7.0 из закреплённого подмодуля. Старые `reference/` и `scripts/` каталоги рядом с этим файлом считаются историческими, не используются как активный upstream и не выполняются.

1. Проверить `.impeccable/.agents/skills/impeccable/SKILL.md`, затем `git -C .impeccable rev-parse HEAD` = `dad8dbb3aac21ad1fa236c110315ef4abece23e8`. Если подмодуль не загружен, сообщить, что полноценный Impeccable недоступен: `git submodule update --init -- .impeccable`.
2. Прочитать оригинальный upstream `.impeccable/.agents/skills/impeccable/SKILL.md`, затем только необходимый `reference/<command>.md`. Запуск внешнего launcher/хука для чтения и применения методики **не нужен**. User-facing mode Codex - `$impeccable`.
3. Свериться с `AGENTS.md`, `DESIGN.md` v7, `PRODUCT.md`, `design-qa.md` и существующим React. Утверждённые black/violet, Onest, герой-сцена с мозгом и отдельные рабочие поверхности имеют приоритет даже при конфликте с универсальными эстетическими запретами.
4. Для подтверждённого UX/UI запроса применять сначала `critique`/ `audit`, обнаруживать повторяющиеся случайные карточки, «SaaS панели» и лишние декоративные блоки относительно **этого** приложения. Только затем выбирать `shape`, `polish`, `distill`, `harden` или `layout`. Не исправлять дизайн ради демонстрации нового скилла.
5. Проверка обязательна на реальном синтетическом React render через `web/qa`, включая 320/360/390/430, desktop, loading/empty/error, русские строки, фокус, клавиатуру и reduced-motion. Реальные PNG/HTML и owner visual acceptance при смене композиции. Не заменять кодовые проверки красивыми статическими мокапами.
6. `second-brain-vault` никогда не передавать в ИИ-дизайн, не сканировать и не экспортировать; `NoteDraft`, explicit apply/rollback и private boundary остаются. Никаких новых hooks, network launchers, бинарников, `npx`, платных API, React пакетов или автоматических изменений `PRODUCT.md` и `DESIGN.md`.

Подробнее: `docs/development/impeccable-adoption.md`; это изменение не трогает app/runtime и пользовательские файлы.
