# Основа React frontend

Этот каталог - ограниченная область реализации задачи #141.

Миграция ограничена архитектурой frontend-представления и сборки. FastAPI остаётся источником прикладной и security-логики, все существующие same-origin API-контракты сохраняются без изменений, а product, vault, canonical, provider, persistence, authentication и public-exposure semantics не переносятся в браузер.

Реализация должна следовать project-local навыкам Impeccable и Emil Kowalski, установленным в #106, использовать одобренный владельцем стек React 19 + TypeScript + Vite + Tailwind CSS 4 + только действительно необходимые Radix Primitives + Motion for React, сохранять reduced-motion и local-only security invariants, а полный перенос существующих Web-сценариев оставить задаче #142.
