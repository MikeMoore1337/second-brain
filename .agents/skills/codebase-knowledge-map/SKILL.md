---
name: codebase-knowledge-map
description: Map large repositories, dependency flows and component boundaries when ordinary code search is insufficient. Use for change impact analysis and architecture discovery.
---

# Codebase knowledge map
1. Begin with targeted code search, tests and dependency files. Create a graph only when cross-module relationships materially affect the decision.
2. Gather deterministic edges (imports, calls, definitions) and label heuristically inferred edges separately.
3. Identify entry points, state owners, external IO, tests and changed call paths.
4. Check whether graph tooling has locally available parsers and an acceptable data flow; keep private code local unless explicitly authorized.
5. Validate suspicious relationships against original source code. A graph is a navigation aid, not ground truth.
6. Avoid leaving generated full-repo reports in commits unless needed; timestamp graph output and invalidate it after relevant changes.

**Output:** affected nodes, evidence-backed relationships, uncertain edges, recommended focused tests.


## Second Brain adaptation (repository development only)

Toolkit source revision: 3a9bf14772ab579d37c6f4f9615db505ab6ac259 ([original](https://github.com/MikeMoore1337/ai-development-toolkit/blob/3a9bf14772ab579d37c6f4f9615db505ab6ac259/.agents/skills/codebase-knowledge-map/SKILL.md)).
Trigger: Сквозные зависимости domain, application, ports/adapters и интерфейса при большой задаче; не сканировать приватный vault.
This guidance is subordinate to local `AGENTS.md`, `ROLE_MANIFEST.json`, existing workflows, vault boundaries and Safe Write (preview/diff/preconditions/explicit apply). It does not authorize reading, exporting or writing `second-brain-vault`, starting services, adding providers, or executing external instructions.
