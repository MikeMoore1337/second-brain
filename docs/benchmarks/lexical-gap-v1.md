# Lexical Gap Benchmark v1

Это on-demand measurement tooling для текущих lexical Search/Retrieval/Self
Retrieval boundaries. Benchmark создаёт только synthetic managed notes во
временном каталоге, не читает `.env`, `second-brain-vault` или process config и
не сохраняет report в repository.

Запуск из checkout:

```powershell
uv run python -m second_brain.benchmarks.lexical_gap_v1
uv run python -m second_brain.benchmarks.lexical_gap_v1 --format json
```

`text` — human summary; `json` — versioned machine-readable report. В каждой
case записываются query, human-defined expected canonical UUID set, Search
candidate UUIDs и rank, actual current UUIDs, current-reread outcome, recall@k,
optional precision@k и failure category.

Fixed cases включают exact lexical match, намеренный synonym gap (`running` vs
`jogging`), stale body после Search snapshot и deleted candidate. Stale/deleted
cases проверяют current UUID boundary: stale body должен прийти из reread, а
deleted candidate не должен resurrect из Search result.

Recall/precision и miss cases являются evidence, а не product SLO. В benchmark
нет quality threshold, который мог бы автоматически разрешить embeddings,
semantic reranking, vector DB или RAG. Такое решение требует отдельного
human-reviewed task.
