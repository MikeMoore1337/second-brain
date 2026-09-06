# Simulate Me evaluation harness v1

Это on-demand deterministic evaluation tooling для Stage 6. Harness создаёт
только versioned synthetic notes во временном managed vault и прогоняет
существующий BuildSimulateMe; process configuration, реальный
second-brain-vault, network, provider, LLM, cache, database и canonical write
path не используются.

## Запуск

Из checkout:

~~~powershell
uv run python -m second_brain.benchmarks.simulate_me_evaluation_v1
uv run python -m second_brain.benchmarks.simulate_me_evaluation_v1 --format json
~~~

JSON и Markdown содержат одинаковый versioned report без временных путей,
текстов заметок и пользовательских данных. Для каждой case записываются
expected/actual result category, caller option, evidence UUID refs,
contextual refs, temporal caveats, derivation_version, policy_id,
policy_fingerprint и точная mismatch_reason.

## Synthetic corpus

Corpus version: simulate-me-synthetic-corpus-v1.

Фиксированные cases проверяют:

- prediction по одной direct preference или goal;
- несколько refs для одного distinct option без weighting;
- no_matching_evidence;
- multiple_options_supported без ranking по count/recency;
- belief только как contextual evidence;
- Decision Journal chosen option без implicit inference;
- ordinary searchable note без direct Self Model claim;
- evidence_at: unknown как temporal caveat;
- только NFC и edge-trim normalization;
- malformed current context с fail-closed abstention.

Harness может сообщить pass/fail case и category counts. В нём нет quality
threshold, confidence recalibration, Brier/calibration aggregate,
prospective audit record, ML training или автоматического изменения policy.
Любое изменение prediction semantics требует отдельного reviewed task.
