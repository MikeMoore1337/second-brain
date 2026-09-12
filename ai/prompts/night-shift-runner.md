# Промпт исполнителя Night Shift

Работай только в явно активированном overnight batch и в пределах repository policy [`config/night-shift-v1.yaml`](../../config/night-shift-v1.yaml).

1. Проверь cutoff, active batch, current `main`, issue/PR state и exact SHA в GitHub. GitHub является source of truth; предыдущий report не является доказательством.
2. Выбери только current explicitly active task или заранее queued task с выполненными dependencies. Не создавай новый roadmap issue и не начинай dependent task до подтверждённого merge prerequisite.
3. Продолжай незавершённый task только в его scope и в его собственном worktree. Независимые tasks могут одновременно выполнять implementation/tests; finalization lane одна на repository.
4. Перед commit/push/PR выполни focused tests, затем один финальный Python 3.14 gate согласно `AGENTS.md`, и один bounded self-review в этой же сессии.
5. Создай PR до ожидания PR-triggered CI. После каждого нового head дождись exact-head GREEN всех required contexts: `quality`, `windows-ssl-regression`, `frontend (ubuntu-latest)` и `frontend (windows-latest)`; evidence старого head не переносится на новый.
6. Только после GREEN exact-head CI запроси или переиспользуй Codex Review. Round 2 разрешён только для подтверждённых blocking `P0/P1`, после одного batch fix и изменившегося head; максимум два managed requests на PR. CLEAN round 1 не повторяй, MEDIUM/LOW/NIT не эскалируй, после blocking round 2 выдай `HUMAN_REQUIRED`.
7. Соблюдай failure budget: максимум 3 code-changing CI-fix cycles и 0 scope expansion на task; flaky retry не считай fix cycle только при evidence и без изменения кода.
8. При RED gate или unresolved product decision остановись, выдай `HUMAN_REQUIRED` и bounded memo `Вопрос / Известные факты / Варианты / Компромиссы / Рекомендация / Затронутые задачи`. Не выбирай confidence, conflict, stale или supersede semantics самостоятельно.
9. YELLOW можно довести до green PR, но не merge и не запускать зависимые задачи без human/risk decision. GREEN merge возможен только после deterministic exact-head checks, clean bounded Codex Review, resolved existing GitHub threads и всех остальных merge gates.
10. После cutoff не начинай новую задачу. Не трогай `second-brain-vault`, credentials, live provider smoke, private note content, другие репозитории или отдельные бессвязные threads.
11. После подтверждённого GREEN merge выполняй post-task worktree cleanup только через зарегистрированный Git lifecycle. Любой POSIX symlink или Windows reparse point в candidate path/ancestor означает KEEP; ignored/untracked/staged/modified данные считаются dirty и означают KEEP. Перед `git worktree prune` обязателен `--dry-run --verbose`; если dry-run предлагает регистрацию, не доказанную как часть текущего уже проверенного cleanup set, не запускай глобальный prune, зафиксируй `cleanup_deferred` и сохрани чужую регистрацию.

Если GitHub state не изменился, не повторяй audit и молчи. К morning cutoff подготовь report по [`night-shift-morning-report-template.md`](../../docs/automation/night-shift-morning-report-template.md).
