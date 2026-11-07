# Repository agent instructions

## Purpose

This is the `second-brain` application repository, containing application code, configuration contracts, tests, automation, integrations and project documentation. Personal Markdown knowledge belongs in the separate `second-brain-vault` repository. Never copy it here.

## Repository boundary

- Obtain the vault path only from explicit configuration such as `SECOND_BRAIN_VAULT_PATH`. Do not hardcode machine-specific absolute paths.
- Never add private notes, vault attachments or Obsidian workspace state to this repository.
- A task scoped to this repository must not modify `second-brain-vault`.
- `domain` and `application` must not depend on Obsidian, Git, an LLM provider, FastAPI, Telegram or any other delivery mechanism.
- Introduce integrations only through explicit ports/adapters when the current task requires them. Do not create speculative empty modules.

## Documentation and agent language

Write human-facing project documentation in **Russian**. Keep technology names, libraries, commands, CLI options, environment variables, paths, classes, functions and API fields unchanged. Do not translate source code, test identifiers or machine-readable formats. Preserve external standards and licenses in their original language and explain them in Russian.

Write `AGENTS.md`, agent/role instructions, reusable skills and machine-directed prompts in **English**. This does not change the required language of human documentation, application UI or the owner's conversation.

## Web interface language

All user-facing website content must be in **Russian**, including headings, buttons, form labels, hints, loading and error states, empty states and accessibility text. Avoid English words unless they are brands or necessary technical identifiers. Show technical identifiers and technology names with a Russian label and only when needed for user verification or audit.

Do not leave English internal-layer terms or operation names inside Russian phrases. This includes `owner`, `review`, `preview`, `provider`, `exact`, `Growth`, `Advisor`, `Goal`, `Learning`, `Personal Memory`, `Safe Write`, `Lifecycle`, `mapping`, `transient`, `rebuild` and similar terms. Use natural Russian alternatives. The canonical label for a GitHub user identifier is `GitHub ID`, not a translated hybrid.

Before completing UI work, inspect every affected production React surface, including dynamic messages, errors, empty/loading/confirmation states, `aria-label`, `placeholder` and narrow viewports. Walk through the full changed user journey and run Russian localization regression checks. Canonical terms and exceptions are defined in [the Russian UI language contract](docs/design/russian-ui-language-v1.md).

## Section icons and internal labels

- Different application sections must not share an icon, including aliases of the same picture. Each section needs a distinctive, recognizable symbol.
- Within a section, use the same canonical symbol in navigation and its heading. Identical utility actions may reuse their matching action icons.
- If no suitable unique icon exists, draw one in the established visual system and show it to the owner at actual sizes and in context. A new icon requires explicit owner visual approval before release; until then, it is only a local review candidate.
- Never expose development stage numbers or names (for example, `Stage 12`, `10C` or their Russian equivalents) in user-facing headings, captions, hints, errors or accessibility text. Keep these only in internal documentation. Stages of a user's goal or a meaningful action sequence are not development stages.

Every ordinary single-select native `<select>` in the production React UI must display a same-origin SVG chevron in the application's signal-violet palette, with reserved space on the right. Do not use Unicode, emoji or `data:` images as replacements. Preserve native semantics, keyboard operation and visible focus; disable the custom arrow under `forced-colors`. Exceptions: `multiple` selects and selects with `size` greater than 1.

## Security and Safe Write

- Local/offline read-only commands must not invoke the network or an LLM.
- Network-enabled `llm draft` and `research draft` stay read-only and return a validated `NoteDraft` only. They must not accept `--apply` or write to the vault. `note create-from-draft --file PATH` is a separate local/offline write operation: dry-run by default; a real write requires explicit `--apply`.
- Explicitly network-enabled read-only commands may perform only the external read operation they declare. This is not blanket network permission for other read-only commands. `research read` performs at most one research operation through its declared backend, `llm draft` at most one LLM draft operation, and `research draft` at most one research operation followed by at most one LLM draft operation.
- These networked read-only commands must not write to the vault or Git. Retries, fallbacks, tools and function execution are forbidden for them.
- Credentials for networked LLM operations stay inside the existing adapter/security boundary. They must not become part of the CLI or general read-only contract.
- Vault writes require path containment, dry-run/diff, hash preconditions, a temporary file, no-overwrite publication, post-write validation and safe rollback. At the current stage, only creation of one managed `project`, `area`, `resource` or `zettel` note is allowed, with explicit `--apply` for the real write.
- Preserve unknown front matter fields and Obsidian wikilinks during targeted edits.
- Proposal automation for a new managed note must write only to a new `automation/*` branch and submit a PR into `main`. Direct automated write/commit/push to `main`, force-push and auto-merge are forbidden.
- Proposal dry-run must not use the network or change vault data, Git refs, index or worktree. Apply requires a clean and synchronized `main` and must never run pull, rebase, merge or reset automatically.
- Never commit secrets or a real `.env`.
- Commit, push, PR, merge and deploy require an explicit user request. Translation of an agent instruction does not itself authorize vault writes or production actions.

### Owner-authorized production Vault Git Sync exception (Issue #217)

The preceding boundary applies unchanged to ordinary application/user-facing Safe Write for a single managed note. Existing Issue #217, Vault Sync v1, separately permits a repository-level production Git sync **only with explicit owner authorization**. This is not Safe Write or general permission to write arbitrary vault content.

All gates must be satisfied together: exact SHA, explicit `--apply`, separate `PRODUCTION_VAULT_SYNC_ENABLED=true`, a shared lock, clean worktree, exact repository and branch, backup before fast-forward, fast-forward only, and post-sync validation. The protocol must not reset, rebase, force changes or resolve conflicts automatically. Failure of any gate or safety invariant is fail-closed with `HUMAN_REQUIRED`. This exception permits no other production vault writes.

## 21st.dev UI prototyping

The official host-level Codex `21st` plugin may serve as an **optional reference** for public React components in UI tasks. Installation and safe use are described in [AI development tooling](docs/development/ai-dev-tooling.md).

Never send `second-brain-vault` files, paths, excerpts or metadata, personal notes, user data, secrets, private screenshots or internal documents to 21st.dev. Do not import external code, themes or dependencies, use paid generation or publish anything without a separately authorized task and license review. Russian UI, privacy/Safe Write, existing design, accessibility, testing and owner pre-approval take precedence over any skill or MCP advice.

## Graphify and Ponytail during development

Graphify and Ponytail are allowed **only as local development tools**. They are not part of the application/runtime, production, CI or canonical knowledge storage.

- Use Graphify on demand for architecture navigation and impact analysis. If `graphify-out/graph.json` exists and a task requires broad dependency understanding, `graphify query`, `graphify path` or `graphify explain` may be used first. Verify the output against source code, tests and normative contracts; Graphify is not a source of truth.
- Graphify's initial mode is local code-only AST analysis of the current `second-brain` checkout. Do not scan sibling `second-brain-vault`, paths from `SECOND_BRAIN_VAULT_PATH`, secrets, `.env`, runtime/data or other personal/private directories.
- Do not automatically enable semantic extraction, external LLM credentials, `--watch`, Git hooks, MCP or background Graphify rebuilds. Each requires a separately stated task.
- `graphify-out/` is disposable derived state and must not be committed.
- Ponytail may be used only with host-level Codex. Its recommended initial mode for this project is `lite`; do not default to `ultra`.
- Ponytail must not shortcut validation, error handling, security/privacy, accessibility, mandatory tests, fail-closed behavior, `HUMAN_REQUIRED`, owner approval or any rule in this `AGENTS.md`.
- These instructions and the current task's acceptance criteria take precedence over Graphify/Ponytail suggestions.
- Do not add Caveman to the current project toolchain.

Setup, verification and rollback: [AI development tooling](docs/development/ai-dev-tooling.md).

## Development checks

Before proposing a change, run:

```text
uv run ruff format --check .
uv run ruff check .
uv run mypy src tests
uv run pytest
```

## Minimum sufficient execution

Conserve Codex usage: perform only actions that add new information or are necessary to implement and verify the current task.

- Do not repeat successful checks unless the code they cover has changed.
- The project supports **Python 3.14 only**. Do not run a multi-version Python test matrix.
- Run targeted tests during development. Run the full mandatory `ruff` / `mypy` / `pytest` gate once after all changes.
- Do not reread unchanged files, issues or documentation already inspected.
- Do not repeat full repository audits for local modifications.
- Do not rerun `git status`, `git diff`, `git log` or equivalent commands without a state change.
- Do not re-investigate accepted architecture decisions.
- Avoid unnecessary extra reports, temporary artifacts and diagnostics.
- Group related reads, edits and checks; choose the smallest reliable verification set.
- Stop once acceptance criteria and final verification are satisfied; do not perform speculative extra actions.

Efficiency must not skip required acceptance criteria, security tests or verification of changed behavior.

## Role & Agent Pack

The repository has a small set of roles and repeatable agent workflows to make Codex decisions consistent without a complex multi-agent infrastructure.

Canonical definitions:

- `ROLE_MANIFEST.json`: role registry;
- `AGENT_MANIFEST.json`: agent workflow registry;
- `ai/roles/*.md`: role responsibilities and boundaries;
- `ai/agents/*.md`: step-by-step workflows;
- `ai/prompts/*.md`: input templates;
- `ai/evals/*.md`: quality criteria.

### Conditional engineering skills from AI Development Toolkit

Task-specific, SHA-pinned Toolkit reference skills are listed in [`.agents/README.md`](.agents/README.md). They supplement roles but are **not** agent workflows, do not create autonomous orchestration, and grant no commit/deploy or private `second-brain-vault` write permission. Select at most 1-2 when actually needed. Priority: this `AGENTS.md`, current task and role, then selected skill. Vault tasks always retain dry-run/diff, preconditions, explicit apply and `HUMAN_REQUIRED` where required.

### Role routing

Choose one primary role before implementation. Add another role only if it materially changes the solution or provides a mandatory review. Do not assemble an artificial team of every role.

| Task | Primary role | Optional secondary role |
| --- | --- | --- |
| Vault structure, PARA/Zettelkasten, front matter contract | `knowledge-architect` | `security-reviewer` |
| Note quality, deduplication, links | `knowledge-curator` | `knowledge-architect` |
| CLI, cron, automation, Git workflow | `automation-engineer` | `qa-engineer` |
| LLM/API/Telegram/Obsidian/GitHub integrations | `integration-engineer` | `security-reviewer` |
| Tests, regressions, acceptance criteria | `qa-engineer` | Subject-matter role |
| Secrets, access, vault writes, privacy | `security-reviewer` | Subject-matter role |

A `security-reviewer` is mandatory for real vault writes, secret handling, unverified external data or potentially destructive actions.

### Agent workflow selection

An agent is a repeatable workflow, **not** a permanently running autonomous process.

- `inbox-processor`: process newly captured material;
- `knowledge-distiller`: turn a large source into concise knowledge and atomic notes;
- `research-agent`: collect and synthesize verifiable research;
- `vault-maintainer`: find vault structure and quality issues;
- `review-agent`: conduct daily/weekly/monthly knowledge reviews.

Use a workflow only when it matches the task. Ordinary development needs no separate agent. Here `review-agent` reviews user knowledge, **not** PR/code review release gates.

### Instruction precedence

1. This `AGENTS.md` and repository security boundaries.
2. Current task acceptance criteria.
3. Selected role profile.
4. Selected agent workflow.
5. Prompt/evaluation template.

Roles and agents cannot weaken security boundaries, allow writes without `--apply`, bypass dry-run/diff, cross repository boundaries or independently perform commit/push/PR/merge/deploy.

## Night Shift Development Orchestrator v1

The bounded repository protocol for an explicitly enabled overnight batch is specified in [Night Shift v1](docs/automation/night-shift-v1.md); the machine-readable policy is `config/night-shift-v1.yaml`.

Codex Code Review is allowed only as a bounded final semantic gate after exact-head GREEN CI: one round 1 per PR and at most one round 2 after confirmed blocking `P0/P1` findings fixed in one batch on a new head. A clean round 1 triggers no further review; blocking round 2 means `HUMAN_REQUIRED`. Reuse pending/completed review for the same SHA. `MEDIUM/LOW/NIT` findings do not trigger a re-review. Before committing, the implementer performs one bounded self-review. Do not create a separate reviewer subagent, adversarial audit or second LLM verdict.

Implement independent tasks concurrently in **separate worktrees**. Repository-wide `exclusive-write` for ordinary implementation is forbidden; only task/worktree/finalization/production-scoped locks are allowed. The only repository-scoped serialized lane is **finalization**: refresh base, resolve conflicts, verify affected code, final push, exact-head CI, Codex Review, merge and release closeout.

Current required ruleset checks are `quality`, `windows-ssl-regression`, `frontend (ubuntu-latest)` and `frontend (windows-latest)`. Aggregate `checks` is not a required ruleset context. Readiness requires those exact-head checks, the bounded review gate, no known unresolved BLOCKER/HIGH, a mergeable PR and all explicit human/external gates.

- `enabled_by_default: false`: the presence of this policy does not authorize autonomous commit, push, PR or merge for ordinary tasks.
- GitHub issue/PR state, existing review threads, exact SHA and CI remain the source of truth.
- Never bypass RED/YELLOW gates, failure budgets or dependency blockers.
- Night Shift cannot weaken Safe Write, privacy, Python 3.14 or the `second-brain-vault` boundary.
- The protocol adds no agent runtime, daemon, queue, DB or provider.
- Read-only diagnostics do not require an implementation write lease.
- After a verified GREEN merge, use the bounded Git-only cleanup in [`scripts/worktree_cleanup.py`](scripts/worktree_cleanup.py). Its receipt, safety rules and `cleanup_deferred` are described in [Night Shift v1](docs/automation/night-shift-v1.md).

### Handoff

When the task genuinely requires a change of role or agent, hand over only:

- the goal;
- verified facts;
- modified files;
- open risks;
- remaining acceptance criteria.

Do not retell the full task history or repeat completed diagnostics.

### Autonomy limits

Do not add Supervisor/Manager/Planner agents, message queues, LangGraph, CrewAI, a separate agent-state database or agent-to-agent RPC without a concrete product need and an independently authorized architecture task.

`second-brain-vault` does not contain role/agent definitions. It remains the canonical repository of personal knowledge.

## Pinned external UI/React references

For React, accessibility, motion and evidence-based UI audit tasks, use only the relevant **local** recommendations in [external UI quality skills](docs/development/external-ui-quality-skills.md). `$impeccable` is the main project UX/UI critic (pinned upstream, initialized with `git submodule update --init -- .impeccable`), not an additional role or redesign authorization. `DESIGN.md` v7, `PRODUCT.md`, `design-qa.md` and the `second-brain-vault` boundary take precedence.

A new visual direction requires a separate explicit owner request. Do not execute external scripts, upload source/data to third-party services, introduce a competing design or add mandatory CI dependencies.

## Impeccable v4.7.0: avoid generic AI-generated design (2026-10-21)

For new user-facing UI flows, screen improvements and visual critique, **first** use `$impeccable` from `.agents/skills/impeccable/SKILL.md`: inspect current source/render, run `critique` and `audit`, and propose evidence-based changes with real counterexamples. The original upstream is pinned in `.impeccable/` at SHA `dad8dbb3aac21ad1fa236c110315ef4abece23e8`; setup and requirements are in `docs/development/impeccable-adoption.md`.

This is a project Codex instruction, not a new runtime, hook or permission. Preserve `DESIGN.md` v7, Onest, black/violet colors, the ban on green/mint, and privacy of the user's vault. Do not conflate recommendations for generic SaaS screens with the approved custom design.

## UX/UI Design Orchestra (2026-10-21)

For a combined UX/UI audit and unified visual proposal of **production React UI only**, route to `$ux-ui-design-orchestra` (`.agents/skills/ux-ui-design-orchestra/SKILL.md`). Default `audit-proposal` mode is READ_ONLY: work from screenshots, observed navigation and accepted `DESIGN.md` v7. Do not modify source until explicit owner visual acceptance `«Принимаю»`. The existing `second-brain-vault` boundary, Safe Write, privacy, `design-qa.md`, product truth and separate release approval always take priority. At most three concurrently spawned read-only subagents are allowed when actual client tools support them; otherwise run sequentially and report honestly. See `docs/development/ux-ui-design-orchestra.md`.
