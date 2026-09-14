# Cognitive Twin v3 — high-level roadmap

Статус документа: **DESIGN / HIGH-LEVEL ROADMAP**.

Документ задаёт границы Cognitive Twin v3 после завершения Cognitive Twin v2 /
Stage 11E. Он не является runtime-контрактом, не меняет vault, не создаёт
новые note types и не разрешает провайдерный, фоновый или автоматический
write-back. Для текущего implementation gate нормативным документом является
отдельный [Goal Progress v1 contract](goal-progress-v1-contract.md).

## Generation invariants

Каждый будущий v3 slice должен сохранять следующие инварианты:

1. Vault остаётся каноническим пользовательским источником истины.
2. Derived/operational stores не становятся пользовательской evidence и
   располагаются вне vault и release directories.
3. Любой новый канонический пользовательский факт появляется только через
   reviewed Safe Write; inference, transcript, provider output и telemetry —
   лишь untrusted candidates.
4. Goal, evidence, outcome, behavioral observation, experiment observation и
   progress observation не смешиваются в один неразличимый record.
5. Goal binding является exact и versioned: UUID и соответствующий identity
   fingerprint, без fuzzy matching и silent retargeting.
6. Временная семантика использует только явно заданное событие; created,
   updated и время вычисления не подменяют event time.
7. Derived result должен быть deterministic, bounded, rebuildable и
   explainable по UUID/fingerprint ссылкам.
8. Private owner-only и privacy boundary сохраняются для поведения, целей,
   экспериментов и любых персональных производных.
9. Provider/network/inference включаются только отдельным явно принятым
   контрактом с payload, retention, failure и secret-safety границами.
10. Каждый slice имеет отдельный acceptance gate, exact-head CI evidence и
    production/env impact report; отсутствие env changes фиксируется явно.

## Stage 12 — Goal Progress & Structured Outcomes v1

**Статус:** Stage 12A COMPLETE в Issue #279 / PR #280; merge SHA
`bdde50f2e3de3953dddf1ba58b4e9b371ea050ad` прошёл post-merge CI и standard
production deploy. Stage 12B–12E не начаты.

Stage 12 вводит минимальную, reviewed и exact-bound structured progress
модель, привязанную к текущему Stage 11A Goal. Она отвечает на вопрос
«что произошло относительно явно заданного baseline/target или milestone»,
но не объявляет психологический успех, причинность, reward или универсальный
score.

Нормативный scope Stage 12 находится в
[Goal Progress v1 contract](goal-progress-v1-contract.md):

- exact Goal UUID + GrowthGoalIdentityV1 fingerprint;
- отдельные companion records для reviewed definition и reviewed observation;
- одна активная definition chain на один exact Goal в v1;
- numeric target и milestone-set модели;
- explicit baseline и explicit event time;
- deterministic current result с safe insufficient-data states;
- replacement/supersedes вместо silent mutation;
- Stage 12A read-only canonical records, pure validators и scan projections;
- no provider, telemetry, background inference, automatic Safe Write или
  изменения second-brain-vault.

Stage 12 не переносит progress fields в исходный Goal и не превращает
описательный Stage 2 Outcome в структурированный progress fact.

## Stage 13 — Decision Compass / Compare v2

**Статус:** PLANNED / NOT STARTED. Depends on Stage 12 contract and bounded
runtime.

Stage 13 может собрать в owner-only decision context:

- current Goal identity;
- relevant Behavioral Self Model;
- Growth relation/friction;
- Goal Progress result;
- existing Simulate Me and independent Advisor outputs;
- explicit comparison criteria.

Это будет composition/read model, а не hidden winner, ranking oracle или
универсальный score. Запрещены silent recommendation writes, fuzzy Goal
retargeting, provider payload expansion и смешение progress with outcome
judgement. Future normative design should live in a dedicated Compare v2 /
GrowthCompare contract.

## Stage 14 — Personal Experiments

**Статус:** PLANNED / NOT STARTED. Depends on explicit Goal Progress
definitions and observations.

Предполагаемый bounded workflow:

hypothesis -> intervention -> explicit measurement -> reviewed observations
-> bounded result -> reassessment.

Stage 14 не будет утверждать causal effect только из временной близости,
не будет автоматически записывать experiment facts, и не будет скрыто
изменять Goal, progress definition или user plan. Any candidate adaptation
remains owner-reviewed and reversible.

## Stage 15 — Adaptive Cognitive Twin

**Статус:** PLANNED / NOT STARTED. Depends on Stage 9 calibration, Stage 10
behavioral model, Stage 12 progress and Stage 14 experiments.

Stage 15 может предлагать versioned, explainable, reversible and measurable
adaptation candidates from reviewed evidence and explicit calibration. It must
remain owner-controlled and must not introduce hidden reinforcement learning,
unbounded personalization, background monitoring, autonomous goal mutation or
provider-driven canonical writes.

## Stage 16+

Дальнейшие stages намеренно не фиксируются как implementation commitments.
Новые capabilities требуют отдельного issue, contract/design gate, privacy
review, exact dependency map и explicit acceptance decision. Этот roadmap не
создаёт Stage 12B–12E, Stage 13 issue или любой следующий backlog item.

## Non-goals for this roadmap

- изменение schemas/NoteType или second-brain-vault; Stage 12A runtime is
  limited to the read-only parser and validators;
- Safe Write, provider/network, Web/API или новый environment variable/systemd
  contract;
- Codex Review request, Vault Sync или live smoke;
- inference write-back, telemetry, embeddings, vector DB или hidden score;
- автоматический запуск Stage 12B–12E или любой следующей стадии.
