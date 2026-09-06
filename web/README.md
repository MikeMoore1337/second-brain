# React frontend foundation

This directory is the bounded implementation surface for issue #141.

The migration is limited to frontend presentation/build architecture. FastAPI remains the application and security authority, all existing same-origin API contracts remain unchanged, and no product, vault, canonical, provider, persistence, authentication, or public-exposure semantics may move into the browser.

Implementation must follow the project-local Impeccable and Emil Kowalski skills installed by #106, use the owner-approved React 19 + TypeScript + Vite + Tailwind CSS 4 + narrowly-scoped Radix Primitives + Motion for React stack, preserve reduced-motion and local-only security invariants, and leave full flow parity to #142.
