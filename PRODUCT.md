# Product

<!-- impeccable:product-schema 1 -->

## Platform

web

## Users

Владелец личной базы знаний, который собирает, проверяет и переиспользует
собственные материалы, решения, наблюдения и выводы в локальном workspace.

## Product Purpose

Second Brain — открытое Markdown-first ядро автоматизации личной базы знаний.
Оно помогает безопасно захватывать входящий материал, находить существующие
managed notes и готовить проверяемые операции над canonical vault. Успех — это
быстрый переход от сырого сигнала к проверяемому знанию без потери контроля над
данными и без подмены vault производным индексом.

## Positioning

Markdown/YAML-файлы внешнего vault являются каноническим источником истины.
Obsidian — основной поддерживаемый клиент, но ядро не зависит от Obsidian, Git,
конкретного Web UI или одного provider-а.

## Operating Context

Основной режим разработки и локального использования — Python 3.14 с `uv`.
Web GUI запускается локально на loopback и работает поверх существующих
application gateways. Vault path передаётся явной конфигурацией и находится в
отдельном `second-brain-vault` repository; этот repository не является частью
продуктового runtime.

## Capabilities and Constraints

- Web GUI поддерживает захват через URL, Text и Voice, подготовку NoteDraft,
  preview и отдельный Safe Write dry-run перед явным сохранением.
- Search/Retrieval работают как read-only проекции managed notes; disposable
  SQLite FTS5 index не является source of truth.
- Local/offline read-only операции не вызывают сеть или LLM.
- Networked draft/research операции возвращают validated DTO и не пишут в vault.
- Сохранение требует path containment, review/diff, hash preconditions и явного
  подтверждения; browser не выбирает path, identity, timestamp или Git metadata.
- Production UI и design contract должны сохранять loopback-only, privacy,
  отсутствие persistent browser storage для review state и границу
  `second-brain-vault`.

## Brand Commitments

Название продукта — Second Brain. У продукта должна быть самостоятельная
identity, отличимая от YFC; визуальные решения для этого redesign фиксируются
отдельно в `DESIGN.md` и не меняют product semantics.

## Evidence on Hand

- `README.md` — публичное описание ядра, Web GUI и границ изоляции.
- `docs/architecture/` — контракты vault, Safe Write, Search/Retrieval и
  производных read models.
- `src/second_brain/entrypoints/web/` — текущие Web routes и bounded
  application boundaries.
- Реальные пользовательские заметки, vault contents, credentials и
  customer-derived claims отсутствуют в repository и не должны создаваться для
  design work.

## Product Principles

- Каноническое знание остаётся у пользователя и проверяется перед записью.
- Read-only discovery и explicit write остаются различными режимами.
- Производные индексы и UI не получают authority над vault.
- Privacy, bounded operations и обратимая проверка важнее автоматического
  удобства.

## Accessibility & Inclusion

Web experience обязан быть first-class на телефонах 320–390px, больших телефонах,
планшетах и desktop. В design contract обязательны touch targets, readable type,
отсутствие horizontal overflow, корректная работа с dynamic browser chrome и
graceful reduced-motion/performance behavior на мобильных GPU и батарее.
