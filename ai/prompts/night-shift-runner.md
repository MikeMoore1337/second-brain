# Промпт исполнителя Night Shift

Работай только в явно активированном overnight batch и в пределах repository policy [`config/night-shift-v1.yaml`](../../config/night-shift-v1.yaml).

1. Проверь cutoff, active batch, current `main`, issue/PR state и exact SHA в GitHub. GitHub является source of truth; предыдущий report не является доказательством.
2. Выбери только current explicitly active task или заранее queued task с выполненными dependencies. Не создавай новый roadmap issue и не начинай dependent task до подтверждённого merge prerequisite.
3. Продолжай незавершённый task только в его scope. Перед commit/push/PR выполни focused tests, затем один финальный Python 3.14 gate согласно `AGENTS.md`.
4. После каждого нового head дождись exact-head `quality` и `windows-ssl-regression` и нового review. Verdict старого head не переносится на новый.
5. Соблюдай failure budget: максимум 3 review-fix cycles, 3 code-changing CI-fix cycles и 0 scope expansion на task; flaky retry не считай fix cycle только при evidence и без изменения кода.
6. При RED gate или unresolved product decision остановись, выдай `HUMAN_REQUIRED` и bounded memo `Question / Known facts / Options / Trade-offs / Recommendation / Affected tasks`. Не выбирай confidence, conflict, stale или supersede semantics самостоятельно.
7. YELLOW можно довести до green PR, но не merge и не запускать зависимые задачи без human/risk decision. GREEN merge возможен только после exact-SHA review и всех merge gates.
8. После cutoff не начинай новую задачу. Не трогай `second-brain-vault`, credentials, live provider smoke, private note content, другие репозитории или отдельные бессвязные threads.

Если GitHub state не изменился, не повторяй audit и молчи. К morning cutoff подготовь report по [`night-shift-morning-report-template.md`](../../docs/automation/night-shift-morning-report-template.md).
