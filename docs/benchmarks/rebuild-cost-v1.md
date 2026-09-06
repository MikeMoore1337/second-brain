# Derived Read-Model Rebuild Cost v1

Это on-demand benchmark для evidence о стоимости пересборки производных
read-моделей текущего `Timeline`, `Self Model` и `Self Retrieval`.

Benchmark создаёт только synthetic managed notes во временном каталоге. Он не
читает `.env`, реальный `second-brain-vault` или process config, не использует
network/provider/background worker и не сохраняет SQLite/cache/Redis state.
Search index для Self Retrieval создаётся существующим disposable in-memory
adapter внутри одной операции и закрывается после неё.

## Запуск

Из checkout:

```powershell
uv run python -m second_brain.benchmarks.rebuild_cost_v1
uv run python -m second_brain.benchmarks.rebuild_cost_v1 --format json
uv run python -m second_brain.benchmarks.rebuild_cost_v1 `
  --format json --output artifacts/rebuild-cost-v1.json
```

Без `--output` результат печатается в stdout. Явно указанный путь получает
обычный JSON или Markdown artifact; benchmark не создаёт artifact сам по себе.

## Фикстуры и измерения

Используются три фиксированных размера synthetic vault:

| Fixture | Managed notes |
| --- | ---: |
| `small` | 4 |
| `medium` | 12 |
| `large` | 24 |

Для каждого размера отдельно выполняется on-demand rebuild:

- `timeline` — пересборка Personal Timeline;
- `self_model` — пересборка текущего Stage 4 Self Model;
- `self_retrieval` — пересборка disposable lexical index, поиск и current
  reread bounded Self Context.

Report содержит `benchmark_version`, `report_schema_version`, fixture sizes и
по одной записи на каждую пару fixture/operation. В записи есть wall-clock
milliseconds, peak traced bytes от `tracemalloc` и shape-only counters
результата операции. Реальное время и memory зависят от машины и загруженности
окружения.

Эти значения — evidence для последующего human-reviewed решения об оптимизации,
а не product SLO. В benchmark нет hard threshold, автоматического pass/fail по
скорости, embeddings, provider, persistent DB, cache или vector search. CI
проверяет только корректность формы отчёта и безопасные synthetic boundaries;
flaky performance gate намеренно отсутствует.
