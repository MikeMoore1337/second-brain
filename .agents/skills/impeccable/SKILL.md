---
name: impeccable
description: "Primary UX/UI design review, critique, shaping and polish for Second Brain React interface, with DESIGN.md v7, PRODUCT.md, design QA and private vault boundaries overriding upstream defaults. Not for backend-only or private notes."
metadata:
  version: "4.7.0"
  origin: "pbakaus/impeccable@dad8dbb3aac21ad1fa236c110315ef4abece23e8"
---

# Impeccable - Second Brain (verified upgrade)

The previous entry used Impeccable 4.2.1. This short project-scoped entry **replaces the previous guidance** and loads the unchanged upstream 4.7.0 playbooks from the pinned submodule. The adjacent legacy `reference/` and `scripts/` directories are historical, not the active upstream; do not execute them.

1. Verify `.impeccable/.agents/skills/impeccable/SKILL.md` and `git -C .impeccable rev-parse HEAD` equals `dad8dbb3aac21ad1fa236c110315ef4abece23e8`. If the submodule is absent, report that full Impeccable is unavailable and give `git submodule update --init -- .impeccable`.
2. Read the original upstream `.impeccable/.agents/skills/impeccable/SKILL.md`, then only the necessary `reference/<command>.md`. No external launcher or hook execution is required merely to read or apply the documented methodology. The user-facing Codex invocation is `$impeccable`.
3. Review `AGENTS.md`, `DESIGN.md` v7, `PRODUCT.md`, `design-qa.md` and existing React. Approved black/violet colors, Onest, the hero scene with a brain and distinct work surfaces take precedence over universal aesthetic prohibitions.
4. For an authorized UX/UI request, first apply `critique`/`audit`, identifying accidental repetitive cards, generic SaaS panels and unnecessary decorative sections relative to **this application**. Only then select `shape`, `polish`, `distill`, `harden` or `layout`. Do not redesign solely to demonstrate a newly installed skill.
5. Validate against a real synthetic React render through `web/qa`, including widths 320/360/390/430 and desktop, loading/empty/error states, Russian text, visible focus, keyboard operation and reduced-motion. Provide real PNG/HTML evidence and owner visual acceptance when composition changes. Do not replace implementation checks with attractive static mockups.
6. Never scan, export or submit `second-brain-vault` to AI design tooling. Keep `NoteDraft`, explicit apply/rollback and private boundaries. Do not add hooks, network launchers, executables, `npx`, paid APIs, React packages or automatic modifications to `PRODUCT.md` or `DESIGN.md`.

See `docs/development/impeccable-adoption.md`. This translation does not modify application/runtime or user files.
