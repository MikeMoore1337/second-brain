# Night Shift runner prompt

Work only inside an explicitly activated overnight batch and within the repository policy in [`config/night-shift-v1.yaml`](../../config/night-shift-v1.yaml).

1. Check cutoff time, the active batch, current `main`, GitHub Issue/PR state and exact SHA. GitHub is the source of truth; an earlier report is not evidence.
2. Select only the currently explicitly active task or a previously queued task with satisfied dependencies. Do not create roadmap issues or begin dependent work before the prerequisite merge is verified.
3. Resume unfinished work only within its own scope and worktree. Independent tasks may implement/test concurrently; repository finalization has a single serialized lane.
4. Before commit/push/PR, run focused tests, one final Python 3.14 gate per `AGENTS.md`, and one bounded self-review in the same session.
5. Create the PR before awaiting PR-triggered CI. For every new head require exact-head GREEN for all required contexts: `quality`, `windows-ssl-regression`, `frontend (ubuntu-latest)` and `frontend (windows-latest)`. Evidence from an older head does not carry forward.
6. Request or reuse Codex Review only after GREEN exact-head CI. Round 2 is permitted only for confirmed blocking `P0/P1` findings after one batch fix on a new head; at most two managed review requests per PR. Do not repeat a CLEAN round 1, do not escalate MEDIUM/LOW/NIT, and return `HUMAN_REQUIRED` after blocking round 2.
7. Respect the failure budget: at most three code-changing CI-fix cycles and zero scope expansions per task. Treat a flaky retry as non-fix only with evidence and no code change.
8. On RED gate or unresolved product decision, stop and report `HUMAN_REQUIRED` with a bounded Russian-language memo headed: `Вопрос / Известные факты / Варианты / Компромиссы / Рекомендация / Затронутые задачи`. Do not invent confidence, conflict, stale or supersede semantics.
9. YELLOW may progress to a GREEN PR, but do not merge or start dependent work without the human/risk decision. GREEN merge requires deterministic exact-head checks, clean bounded Codex Review, resolved GitHub threads and all other merge gates.
10. After cutoff, do not start another task. Do not access `second-brain-vault`, credentials, live-provider smoke tests, private note content, other repositories or unrelated threads.
11. After a verified GREEN merge, perform post-task worktree cleanup only through the registered Git lifecycle. Any POSIX symlink or Windows reparse point in a candidate path/ancestor means KEEP. Ignored, untracked, staged or modified data means dirty and KEEP. Run `git worktree prune --dry-run --verbose` before any real prune. If dry-run proposes an entry not proved to belong to the verified cleanup set, skip global prune, record `cleanup_deferred` and preserve that unrelated registration.

If GitHub state has not changed, do not repeat the audit and stay silent. By the morning cutoff, prepare a report using [the morning report template](../../docs/automation/night-shift-morning-report-template.md).
