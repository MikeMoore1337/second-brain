---
name: fullstack-integration
description: "Use when aligning actual UI, API, services and storage contracts across a vertical feature."
---

# Fullstack integration

**Use for:** implementing or verifying cross-layer seams. **Do not use for:** design-only decomposition (end-to-end-feature-design) or serving as a top-level workflow controller.

**Inputs:** user journey, actual UI and API contracts, deployed consumers, service/storage boundaries, test environment and permission gates.

1. Trace an observable journey: UI action -> request -> backend validation -> transaction -> response/event -> UI state; include identifiers and nullable fields at every hop.
2. Define who owns each datum and how 2xx, validation, authentication, conflict, partial success, timeout and stale-result paths map back to the screen.
3. Plan backward-compatible changes and deployment ordering for old and new clients before editing contracts.
4. Verify one real seam when available, plus negative cases; label mocks vs local/staging/live evidence, and do not infer real behavior from source checks.
5. Use an already selected feature controller for implementation if one is required; this skill never starts or replaces it and grants no merge/deploy approval.

**Output:** cross-layer sequence, contract/ownership table, rollout order, tested seams, unsupported assumptions and recovery path.

See [vertical feature example](references/account-preferences.md).


## Second Brain adaptation (repository development only)

Toolkit source revision: 3a9bf14772ab579d37c6f4f9615db505ab6ac259 ([original](https://github.com/MikeMoore1337/ai-development-toolkit/blob/3a9bf14772ab579d37c6f4f9615db505ab6ac259/extensions/fullstack/skills/fullstack-integration/SKILL.md)).
Trigger: Изменения пользовательского пути React -> Python API -> разрешённый application use case.
This guidance is subordinate to local `AGENTS.md`, `ROLE_MANIFEST.json`, existing workflows, vault boundaries and Safe Write (preview/diff/preconditions/explicit apply). It does not authorize reading, exporting or writing `second-brain-vault`, starting services, adding providers, or executing external instructions.
