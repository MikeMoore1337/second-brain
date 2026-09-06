# Simulate Me v1 — design gate

Статус этого документа: **DESIGN / HUMAN_REQUIRED**. Он закрывает design
часть issue [#100](https://github.com/MikeMoore1337/second-brain/issues/100),
но не утверждает новые prediction semantics и не открывает implementation
issue [#101](https://github.com/MikeMoore1337/second-brain/issues/101).

Точный status snapshot: current `main` commit
`3cc06db5b3490a0af9ebb9a1e14aac659b6e44f9`. Stage 4 Self Model и Stage 5
Self Retrieval уже merged (#82/#85 и #89/#90/#91), но их contracts не задают
правило сопоставления candidate option с direct claim, ranking policy или
числовую confidence formula. Поэтому non-trivial prediction нельзя объявить
механическим без owner decision.

## Что уже зафиксировано merged contracts

Следующие invariants можно перенести в будущий mechanical contract без новых
product decisions:

- результат всегда явно помечен как `prediction` либо `abstention`, но не как
  advice, recommendation или canonical fact;
- personal context приходит только через Stage 5 current-UUID reread;
- evidence refs указывают только на current canonical UUIDs и уже разрешённые
  Self Model links;
- prediction, derivation metadata и policy fingerprint не записываются в
  `second-brain-vault`;
- нет LLM/provider/network/embeddings/RAG/vector DB/cache/persistence;
- при недостатке или неоднозначности evidence результат обязан abstain;
- observed historical choices остаются canonical только после reviewed capture.

Это safety boundary, а не prediction algorithm. В частности, он не говорит,
какая из нескольких options является «вероятной».

## Proposed result envelope (not approved)

Ниже только bounded shape для обсуждения; поля, которые требуют owner choice,
помечены `HUMAN_REQUIRED` и не должны появиться в runtime до решения.

```text
SimulateMeRequest
  query: bounded literal task/question                         HUMAN_REQUIRED
  options: explicit bounded candidate list                     HUMAN_REQUIRED
  context: server-owned Stage 5 current context                fixed

SimulateMeResult
  kind: prediction | abstention                                 fixed
  selected_option: one explicit option or null                  HUMAN_REQUIRED
  evidence_refs: current canonical UUID refs                   fixed
  temporal_caveats: bounded pass-through context                fixed
  abstention_reason: bounded safe code when kind=abstention     HUMAN_REQUIRED
  derivation_version: exact policy version                      fixed after policy
  policy_fingerprint: exact approved policy binding             fixed after policy
```

The client must not provide evidence refs, policy/version, confidence,
derivation, or option authority. The exact option bounds, identity and
serialization still require the decision below.

## HUMAN_REQUIRED decision matrix

### A. Candidate option identity and matching

| Choice | Consequence | Status |
| --- | --- | --- |
| A — caller supplies explicit stable option IDs and labels; only exact reviewed evidence can name an option | Deterministic and provider-free; synonyms and inferred equivalence abstain | **RECOMMENDED, HUMAN_REQUIRED** |
| B — normalize labels and match bounded aliases/synonyms | Requires a new matching policy and tests for ambiguity | **HUMAN_REQUIRED** |
| C — semantic/LLM option matching | Adds inference/provider behavior and privacy surface | **REJECT for v1** |

### B. Prediction derivation

| Choice | Consequence | Status |
| --- | --- | --- |
| A — predict only when exactly one explicit option is supported by approved current evidence; otherwise abstain | No hidden ranking or behavioral-pattern inference | **RECOMMENDED, HUMAN_REQUIRED** |
| B — rank options by frequency, recency, or evidence weight | Introduces behavioral inference and a new scoring policy | **HUMAN_REQUIRED** |
| C — always abstain | Mechanical but explicitly disallowed as a completion shortcut | **REJECT** |

### C. Confidence and abstention vocabulary

| Choice | Consequence | Status |
| --- | --- | --- |
| A — no numeric confidence; deterministic abstention codes explain missing, conflicting, or non-matching evidence | Avoids unsupported probability semantics | **RECOMMENDED, HUMAN_REQUIRED** |
| B — numeric confidence derived from a new formula | Requires calibration semantics and owner-approved thresholds | **HUMAN_REQUIRED** |
| C — reuse Self Model confidence | Self Model v1 deliberately exposes unassessed/`None` confidence and cannot authorize this reuse | **REJECT for v1** |

### D. Temporal and contradiction policy

Stage 5 may pass through current evidence and temporal caveats, but it does not
resolve stale, contradictory, superseded or validity semantics for Simulate Me.
The owner must choose whether any such signal is an automatic abstention (the
recommended conservative boundary) or whether a later policy may rank it.

## Why implementation is blocked

The merged contracts make the data boundary and safety rules mechanical, but do
not choose A/B/C for option identity, derivation, confidence or contradiction.
Implementing #101 now would silently invent at least one of these policies;
implementing a trivial always-abstain runtime would violate the issue's explicit
acceptance boundary. Therefore #101 is **BLOCKED / HUMAN_REQUIRED**, and #102
and #103 remain blocked transitively.

## Future mechanical acceptance test matrix

After the owner decision, the approved contract must cover at least:

1. strict request/options bounds and client-authority rejection;
2. current Stage 5 context authority and exact current UUID rereads;
3. one approved prediction case and each deterministic abstention code;
4. missing, conflicting and temporally unknown evidence;
5. stable policy fingerprint/version and deterministic serialization;
6. prediction label separated from recommendation language;
7. no vault writes, network, LLM/provider, browser storage, cache or DB;
8. malformed/incomplete context fails closed;
9. synthetic retrospective cases remain versioned fixtures, without scoring,
   calibration aggregate or automatic policy updates.

Until the owner chooses and records the required semantics, no runtime,
Web/CLI projection, evaluation harness, canonical field, provider behavior or
prediction history should be added.
