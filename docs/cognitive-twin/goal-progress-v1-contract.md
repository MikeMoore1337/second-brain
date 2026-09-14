# Cognitive Twin v3 / Stage 12 — Goal Progress & Structured Outcomes v1

Статус: **STAGE 12B IMPLEMENTATION COMPLETE**.

Issue: [#277](https://github.com/MikeMoore1337/second-brain/issues/277).
Implementation issue: [#284](https://github.com/MikeMoore1337/second-brain/issues/284).
Implementation base: `origin/main` SHA `c7fc135dc1c26475794e780357b668e94077b52c`.
Stage 12A implementation PR: [#280](https://github.com/MikeMoore1337/second-brain/pull/280), merged as
`bdde50f2e3de3953dddf1ba58b4e9b371ea050ad`.
Stage 12B implementation PR: pending.
Post-merge CI: run [#34841812341](https://github.com/MikeMoore1337/second-brain/actions/runs/34841812341).
Production deploy: run [#34842008063](https://github.com/MikeMoore1337/second-brain/actions/runs/34842008063);
`/healthz` returned HTTP 200 with `{"status":"ok"}`.

Этот документ фиксирует контракт и границы Stage 12; Stage 12A остаётся
canonical records/pure validators, а Stage 12B добавляет только explicit
offline reviewed Safe Write для двух companion record kinds. Он не меняет
Stage 12A semantics, provider/network, Web/API или release contract, не создаёт
канонические записи автоматически и не изменяет `second-brain-vault`.
Stage 12A прошёл required CI, post-merge evidence, standard production deploy и
non-mutating `/healthz`; Stage 12C–12E остаются отдельными implementation
slices.

## Normative source map

Контракт читается вместе с текущими source-of-truth документами:

- [Cognitive Twin v1 roadmap](design-roadmap-v1.md);
- [Cognitive Twin v3 high-level roadmap](cognitive-twin-v3-roadmap.md);
- [Growth Engine v1](growth-engine-v1-contract.md);
- [Self Model v1](self-model-v1-contract.md);
- [Behavioral Self Model v1](behavioral-self-model-v1-contract.md);
- [Stated-vs-Observed Mapping v1](stated-observed-mapping-v1-contract.md);
- [Prospective Audit and Calibration v1](prospective-audit-calibration-v1-contract.md);
- [Active Personal Learning v1](active-personal-learning-v1-contract.md);
- [Vault Contract v1](../architecture/vault-contract-v1.md).

При конфликте этот документ имеет силу только для Stage 12 design scope; общие
vault, Safe Write, privacy и production rules остаются у их более общих
source-of-truth документов. Runtime не может молча расширить этот контракт.

## 1. Purpose and non-goals

Stage 12 определяет bounded read model для exact Goal:

1. owner явно задаёт измеримую progress definition;
2. owner явно подтверждает progress observations;
3. builder сравнивает observations с definition deterministic способом;
4. result объясним через exact record UUIDs, Goal identity и fingerprints.

Stage 12 не пытается ответить на вопросы «хороший ли это человек», «достиг ли
пользователь успеха во всех смыслах», «что делать дальше» или «какова
причина изменения». Result описывает только сравнение заданной модели с
заданными observations.

В Stage 12 не входят:

- психологическое или медицинское заключение;
- causal inference, reward, motivation, confidence или universal score;
- automatic progress capture из telemetry, browser, calendar, provider,
  transcript, behavior model или background watcher;
- fuzzy Goal matching, same-text retargeting или automatic Goal selection;
- изменение исходного Goal при изменении progress;
- structured rewrite существующего Stage 2 Outcome;
- provider/network/LLM call;
- embeddings, vector DB, new database, hidden operational persistence;
- изменение second-brain-vault, release directories, environment или
  systemd contract;
- Stage 12B–12E runtime, Web/API surface или Stage 13+ implementation.

## 2. Terms and authority

| Term | Meaning | Authority |
| --- | --- | --- |
| Goal | Current Stage 11A exact direct Goal claim | Vault current-vault projection |
| Goal identity | GrowthGoalIdentityV1 plus its fingerprint | Stage 11A / Growth Engine |
| Progress definition | Reviewed model of what is being measured | New reviewed companion record |
| Progress observation | Reviewed value/state at an explicit event time | New reviewed companion record |
| Outcome | Existing descriptive Stage 2 later observation | Existing Outcome contract |
| Result | Deterministic projection from current records | Rebuildable application read model |
| Active | Not superseded by a reviewed replacement and still current | Exact chain validation |
| Current | Valid under current-vault and exact Goal identity at query time | Current-vault reread |

Progress definition and progress observation are evidence-like user records,
not derived output. A result is derived and must never be treated as a new
user fact without explicit review.

## 3. Authority placement decision

### Decision

V1 uses **separate reviewed canonical companion records** for definition and
observation. The original Goal remains unchanged. Progress is not stored only
in an operational mapping database and is not computed from an ephemeral query
configuration.

| Candidate placement | Decision | Reason |
| --- | --- | --- |
| Fields inside Goal | REJECTED | Goal identity and progress measurement have different lifecycle and correction semantics |
| Existing free-text Outcome | REJECTED | Outcome is descriptive and must not acquire hidden structured authority |
| Stage 9/10 operational store | REJECTED | Derived/operational data is not canonical user evidence |
| Ephemeral query definition | REJECTED | Cannot support review, correction or reproducible history |
| Separate companion records | ACCEPTED FOR V1 | Explicit review, exact references, append-only replacement and rebuildability |

The companion record proposal is additive and opt-in. Stage 12A adds only an
explicit read-side validator and typed scan projections; it does not add a
writer, migration or new NoteType. Existing Stage 1–11 semantics remain
unchanged for notes without the exact marker.

## 4. Exact Goal binding

Every definition and observation binds to the exact current Goal through both:

- goal_source_uuid: the current Goal note UUID;
- goal_identity_fingerprint: H(GrowthGoalIdentityV1.as_dict()).

The identity fingerprint includes the exact source UUID, normalized current
Goal text, dimension, evidence kind, self kind, domain, evidence time, source
contract/derivation, policy fingerprint and claim/source fingerprints exactly
as specified by Growth Engine v1.

### Binding rules

1. A record with a missing UUID or fingerprint is invalid.
2. A same-text Goal with another UUID is a different Goal.
3. A current Goal whose identity fingerprint differs is drift, not a match.
4. A record never retargets itself to a newly current Goal.
5. A query accepts an explicit Goal UUID only; no implicit current Goal choice
   may hide ambiguity.
6. A result exposes both record references and the binding status.

| State | Meaning | Result |
| --- | --- | --- |
| exact_current | UUID and identity fingerprint equal current Goal | Eligible |
| source_missing | Referenced Goal UUID no longer exists | goal_source_changed |
| source_changed | UUID exists but identity fingerprint differs | goal_source_changed |
| non_current | Exact record is valid historically but not current | Excluded from current result |
| ambiguous | More than one explicit Goal was supplied | Safe error, no result |

### Goal edit, delete and policy drift

If the source Goal is deleted or cannot be proven current, historical
definitions and observations remain canonical records with their original
binding, while the current result is goal_source_changed. If the Goal text,
domain, evidence time, self-model policy, source derivation or claim/source
fingerprint changes, the new identity fingerprint is different and the old
records do not participate in the new current result. A domain change is
therefore drift even when the UUID is unchanged.

V1 has no owner rebind operation. A future explicit rebind would need a new
reviewed definition and new observations under the new identity; it would not
retarget historical records in place.

## 5. Additive note marker proposal

Future implementation may reserve these scalar fields for reviewed companion
records:

~~~yaml
second_brain_goal_progress: 1
goal_progress_kind: definition | observation
~~~

The existing managed note id is the definition or observation record UUID.
The proposal does not add a new NoteType, does not bump schema_version, and
does not alter the existing Goal/Outcome schemas. Unknown fields remain
tolerated under Vault Contract v1. Old readers must not infer semantics from
body text or from partial fields.

The marker and allowlisted fields may be accepted only by a future dedicated
validator on a reviewed Safe Write path. No implementation in this issue
emits, parses or migrates the marker.

### Managed-note envelope

When implemented, a companion record remains an ordinary managed Markdown
note under Vault Contract v1. It must retain the required id, type and created
envelope fields; the v1 writer should reuse the existing zettel template and
Safe Write operation. This reuses an existing NoteType and does not introduce
a new type or a global schema version. updated remains storage metadata only.
The created timestamp is never an event-time fallback. The body is not a
second semantic source: Stage 12 reads only the allowlisted reviewed fields
and does not extract progress from free text.

## 6. DefinitionRecordV1

### Common fields

| Field | Type | Rule |
| --- | --- | --- |
| id | UUID | Existing managed note UUID; immutable |
| second_brain_goal_progress | scalar 1 | Exact opt-in marker |
| goal_progress_kind | definition | Exact record kind |
| goal_source_uuid | UUID | Exact Goal binding |
| goal_identity_fingerprint | sha256 string | Exact Stage 11A identity hash |
| goal_progress_policy_fingerprint | sha256 string | Exact Stage 12 policy hash |
| definition_reviewed_at | exact UTC timestamp | Review event; not measurement time |
| progress_model | numeric_target or milestone_set | Exactly one branch |
| supersedes_definition_id | UUID or absent | Replacement chain; never silent mutation |

Definition records are immutable after Safe Write. A correction creates a new
reviewed definition and points to the record it supersedes.

### Numeric target branch

Required fields:

| Field | Type | Rule |
| --- | --- | --- |
| metric_id | bounded ASCII slug | Stable owner-chosen measurement identity |
| unit | bounded exact token | No implicit conversion or unit inference |
| baseline | exact decimal | Explicit starting value |
| target | exact decimal | Explicit target value |
| direction | enum | increase_to, decrease_to, or reach_exact |
| lower_bound / upper_bound | exact decimal or absent | Optional validity bounds, inclusive |

baseline is required even when the first observation is later than the
definition. There is no fallback to first, oldest, newest, or average value.
maintain_range is deferred because its event semantics and result vocabulary
need a separate contract.

### Milestone-set branch

Required fields:

| Field | Type | Rule |
| --- | --- | --- |
| ordering | display_only_v1 | Display order is not weight or priority |
| milestones | finite list | At least one item, unique IDs |
| milestones[].id | bounded ASCII slug | Stable milestone identity |
| milestones[].label | bounded UTF-8 text | Owner-reviewed label |
| milestones[].ordinal | positive integer | Unique display order |

V1 does not infer milestone completion from text, progress percentage, or
relative weighting. A milestone set cannot be silently changed; replacement
creates a new definition chain.

## 7. ObservationRecordV1

### Common fields

| Field | Type | Rule |
| --- | --- | --- |
| id | UUID | Existing managed note UUID; immutable |
| second_brain_goal_progress | scalar 1 | Exact opt-in marker |
| goal_progress_kind | observation | Exact record kind |
| goal_source_uuid | UUID | Exact Goal binding |
| goal_identity_fingerprint | sha256 string | Must equal definition and current Goal |
| goal_progress_policy_fingerprint | sha256 string | Must equal the active policy |
| progress_definition_id | UUID | Exact definition reference |
| definition_fingerprint | sha256 string | Hash of the active definition payload |
| progress_model | matching enum | Must equal referenced definition |
| observed_at | exact timestamp or explicit unknown | Event time; no created fallback |
| observed_at_precision | exact or unknown | unknown never participates in current comparison |
| observation_reviewed_at | exact UTC timestamp | Review event |
| supersedes_observation_id | UUID or absent | Correction/replacement chain |

### Numeric observation branch

Required fields are metric_id, unit and exact decimal value. metric_id and
unit must equal the active definition exactly. Values outside explicit
definition bounds are rejected as invalid observations; they are not clipped,
normalized or scored.

### Milestone observation branch

Required fields are milestone_id and state, where state is exactly
completed or not_completed. The milestone ID must exist in the active
definition. A later reviewed replacement may correct a state; it does not
rewrite the original record.

Observation does not inherit missing fields from any other record. One record
contains one value or one milestone state. A malformed mixed branch is invalid.

### Qualitative progress

Owner-explicit qualitative progress is deferred from v1. Free text may remain
context for a future reviewed record, but it is never parsed into toward,
away, met, failure, discipline, motivation or any other progress state.

## 8. Active chains and correction semantics

All records are append-only after review. A replacement points to the record
it supersedes and carries the full reviewed payload needed for independent
validation. A write path must never edit a prior record in place, delete it,
or silently reinterpret it.

### Definition chain

For one exact Goal:

1. a definition with no successor is a candidate active leaf;
2. a superseded definition is excluded from current comparison;
3. exactly one valid active leaf is required;
4. zero leaves means definition_missing;
5. more than one non-conflicting-looking leaf is still not a tie-breakable
   state and yields not_comparable;
6. a replacement must preserve the exact Goal binding and may change the
   progress model only through explicit review.

V1 therefore permits one active progress definition per exact Goal. Multiple
simultaneous trackers for the same Goal are deferred until a separate
multi-tracker contract defines identity, aggregation, conflict and UI rules.

### Observation chains

An observation successor makes its predecessor non-current for comparison, but
the predecessor remains available as historical provenance. Distinct event
times are separate observations and do not supersede one another. Multiple
active leaves for the same event identity are not automatically ordered and
yield not_comparable.

An observation is eligible only when all of the following hold:

- its marker and branch validate;
- its Goal binding equals the current Goal and active definition;
- its definition fingerprint equals the active definition fingerprint;
- it is not superseded;
- its event time is exact and not later than query as_of;
- its event time is not before definition_reviewed_at;
- its value/state is valid under the definition.

Unknown-time observations remain reviewed records and are shown in
provenance, but never participate in a current comparison. They cannot be
ordered by note creation, file mtime, review time or query time.

## 9. Time and freshness semantics

Stage 12 has three different time concepts:

| Time | Meaning | May order progress? |
| --- | --- | --- |
| observed_at | User-declared event time | Yes, only when exact |
| definition_reviewed_at | Review of the measurement model | Eligibility boundary |
| observation_reviewed_at | Review of the observation | Provenance only |
| created/updated | Storage metadata | Never |
| generated_at / query time | Result production metadata | Never as evidence |
| as_of | Explicit result cutoff supplied by caller | Excludes future observations |

The result request must carry an exact UTC as_of. A UI may choose the current
instant when making a request, but the chosen value is part of result
provenance and tests must be able to provide it explicitly.

No fallback is permitted from missing observed_at to created, updated,
reviewed_at, file mtime, ingestion time or current time. If a source event
time is unknown, the observation is not comparable for current progress.

An observation recorded before definition_reviewed_at is excluded because the
definition did not yet exist as a reviewed measurement contract. This rule
does not erase the record or claim that the user had no earlier experience.

## 10. Canonical fingerprints

All Stage 12 fingerprints use the repository-wide canonical JSON policy:

- UTF-8 JSON with ensure_ascii=false;
- sort_keys=true;
- separators=(",", ":");
- SHA-256 with the prefix sha256:;
- no timestamps generated during hashing;
- no filesystem paths, secrets or provider payloads.

### DefinitionProgressFingerprintV1

The semantic definition fingerprint is computed from exactly this object:

~~~json
{
  "contract": "goal_progress_definition_v1",
  "goal_identity_fingerprint": "sha256:...",
  "goal_source_uuid": "uuid",
  "model": {},
  "progress_model": "numeric_target"
}
~~~

For numeric_target, model contains metric_id, unit, baseline, target,
direction, lower_bound and upper_bound. For milestone_set, model contains
ordering and the milestones in ordinal order. Decimal values are canonical
finite decimal strings, not binary floats. Absent optional bounds are encoded
as null. Review and replacement metadata are not semantic model fields and
are not hashed.

An ObservationRecordV1 must repeat the exact definition fingerprint. The
builder recomputes it from the referenced definition and rejects a mismatch.
This prevents a record from being evaluated against a silently changed model.

### GoalProgressPolicyV1

The implementation must expose one fixed policy DTO and its fingerprint. The
semantic policy payload is exactly:

~~~json
{
  "contract": "goal_progress_policy_v1",
  "baseline": "explicit_definition_only",
  "models": ["milestone_set", "numeric_target"],
  "numeric_directions": ["decrease_to", "increase_to", "reach_exact"],
  "qualitative_progress": "deferred",
  "maintain_range": "deferred",
  "multiple_trackers": "deferred",
  "percentage": "forbidden",
  "forecast": "forbidden",
  "provider": "forbidden",
  "version": 1
}
~~~

GoalProgressPolicyFingerprintV1 is H of this canonical payload under the
repository hash rules. The exact fingerprint is included in definitions,
observations and results; it must not be inferred from a client label. Any
policy mismatch is a fail-closed state, not a best-effort evaluation.

## 11. Result DTO and status vocabulary

The future provider-free application DTO is named GoalProgressResultV1. It
must contain:

- selected exact Goal UUID;
- current Goal identity fingerprint;
- active definition UUID and definition fingerprint, when present;
- selected as_of;
- progress_model;
- status;
- current observation UUIDs used in the calculation;
- excluded observation references with machine-readable reasons;
- counts for eligible, unknown-time, superseded and invalid records;
- a bounded explanation payload containing only exact references and values
  needed to reproduce the result;
- provenance stating current-vault read, no provider, no network and no write.

The status enum is exactly:

~~~text
target_met
toward_target
away_from_target
unchanged
milestone_observations_available
insufficient_observations
definition_missing
goal_source_changed
not_comparable
~~~

The status is descriptive of the selected definition and observations. It is
not a confidence, reward, recommendation, diagnosis or universal success
claim. No percentage, normalized score, slope, forecast or aggregate is
emitted in v1.

### Safe status precedence

To keep failures deterministic, the builder applies this precedence:

1. invalid explicit request or ambiguous Goal selection: safe request error;
2. Goal source missing or changed: goal_source_changed;
3. zero active definitions: definition_missing;
4. multiple active definitions or incompatible active observation leaves:
   not_comparable;
5. no eligible exact-time observations: insufficient_observations;
6. otherwise evaluate the selected model.

Invalid records are counted and described by safe codes, but their free text
and absolute paths are not exposed in an error response.

## 12. Numeric target evaluation

Numeric values use exact finite base-10 Decimal arithmetic. Float parsing,
implicit unit conversion, locale-dependent separators and rounding are
rejected. Canonical decimal output has no exponent; trailing zeroes are
removed except that zero is represented as 0.

Let baseline be B, target be T and the latest eligible value be V. Direction
must be compatible with the definition:

- increase_to requires T > B;
- decrease_to requires T < B;
- reach_exact requires T != B.

The latest eligible observation is the one with greatest observed_at. If two
active observations have the same exact event time and are not a correction
chain, status is not_comparable.

Evaluation:

| Direction | target_met | distance to target |
| --- | --- | --- |
| increase_to | V >= T | abs(T - V) |
| decrease_to | V <= T | abs(T - V) |
| reach_exact | V == T | abs(T - V) |

If target_met is false:

- toward_target means distance(V, T) < distance(B, T);
- away_from_target means distance(V, T) > distance(B, T);
- unchanged means distance(V, T) == distance(B, T).

For example, B=79, T=72 and V=76.8 gives distances 7 and 4.8, so the
deterministic status is toward_target. V1 does not turn that comparison into
a percentage, an estimated completion date or a forecast.

The result does not say whether the movement was caused by a plan, habit,
intervention or any other factor. Bounds validate the observation; they do not
change the comparison.

## 13. Milestone-set evaluation

For each milestone, the builder orders eligible observations by exact
observed_at. The latest state for each milestone is the current state. A
correction chain contributes only its current leaf; distinct observations
remain historical provenance.

Rules:

- all milestones have a current completed state: target_met;
- a milestone is currently not_completed after a prior eligible completed
  state: away_from_target;
- at least one milestone is completed, or the set has partial coverage without
  a later reversal: milestone_observations_available;
- full coverage with all milestones not_completed and no prior completed state:
  unchanged;
- no eligible exact-time milestone observation exists: insufficient_observations.

V1 does not assign weights, percentage completion, ordinal success, or
cross-milestone arithmetic. The result carries completed milestone IDs and
missing/not-completed IDs so the owner can inspect the exact state.

## 14. Baseline policy

Baseline is an explicit field of DefinitionRecordV1 and is reviewed together
with the definition. V1 never derives baseline from:

- first observed value;
- oldest or newest existing value;
- average, median or any other statistic;
- current Goal text;
- existing Outcome free text;
- Behavioral Self Model or Stage 9 calibration;
- provider, telemetry or browser data.

If a definition has no valid explicit baseline, it is invalid and produces
definition_missing or not_comparable according to the active-chain condition.
Adding a baseline is a new reviewed definition replacement, not a mutation of
the prior definition.

This policy makes a result reproducible after a vault rebuild and prevents the
meaning of historical progress from changing when older notes are imported.

## 15. Boundary with existing Outcome

The Stage 2 Outcome record remains a descriptive later observation tied to a
Decision Journal decision. Its free text may mention a result, but it does not
become a numeric or milestone observation through parsing.

| Existing concept | Stage 12 relation |
| --- | --- |
| Decision Journal | Independent evidence and decision context |
| Outcome | Descriptive observation; not a progress value |
| Goal | Exact identity anchor |
| Progress definition | Explicit measurement contract |
| Progress observation | Explicit structured measurement event |
| GoalProgressResultV1 | Derived comparison only |

No automatic bridge is allowed from Outcome body text to
ObservationRecordV1. A user may create a new reviewed progress observation
after reviewing the Outcome, but that is a separate Safe Write and retains
separate provenance.

## 16. Boundary with Growth, Advisor, Behavior and Calibration

### Growth Engine and Goal identity

Growth Engine / Stage 11A remains authoritative for current Goal identity.
Stage 12 consumes the exact identity and never reconstructs it from text.
Goal Progress cannot update GrowthGoalIdentityV1.

### Growth Advisor and Growth Learning

Advisor and Learning remain independent candidate-generation capabilities.
Stage 12 does not call either capability, send them progress data, or turn
their output into evidence. Future composition requires a separate privacy
and payload contract.

### Behavioral Self Model

Behavioral Self Model describes reviewed Decision Journals and comparable
cohorts. It does not supply progress observations, baseline, target or
milestone completion. Stage 12 does not infer progress from behavior.

### Stage 9 prospective calibration

Prospective prediction and calibration remain operational evaluation data.
They do not become a Goal Progress baseline, observation, result or outcome.
Calibration metrics cannot be substituted for the owner's explicit model.

## 17. Multiple Goals and multiple trackers

The request must identify exactly one Goal UUID. If a caller provides zero
Goals, the application may resolve a single explicit current Goal only when
the caller's API contract says that this selection is unambiguous; the
selected UUID must still be returned. If more than one current Goal is
eligible, the request fails closed.

V1 has no aggregate across Goals. It does not calculate a portfolio score,
overall progress, priority-weighted progress or a winner among Goals.

V1 permits one active definition per exact Goal. Multiple simultaneous
definitions or tracker instances are not_comparable, not auto-merged,
latest-wins, averaged or selected by file order. A future multi-tracker
contract must define tracker identity, selection UI, conflict handling,
aggregation, corrections and privacy before implementation.

## 18. Privacy and data minimization

Stage 12 records can contain sensitive personal measurements. A future
implementation must:

- remain owner-only at Web/API/UI boundaries;
- read only the selected Goal and its exact companion records;
- avoid sending any payload to a provider;
- avoid background scans and telemetry;
- use bounded field sizes and allowlisted fields;
- reject control characters and unsafe timestamps/decimals;
- avoid absolute paths, secrets and raw free text in safe errors;
- keep derived result stores outside the vault if a cache is later needed;
- never expose another Goal through an ambiguous request;
- preserve audit references without copying unnecessary note bodies.

No implementation may use an observation as a behavioral or medical profile.
Measurement unit/value semantics are owner-provided and must not be
interpreted beyond the chosen progress model.

### Future Web/API UX boundary

This issue defines no UI or API runtime, but a future private surface must
show the evidence boundary before technical metadata:

- Goal;
- what is measured;
- current observation;
- previous or explicit baseline observation;
- target or milestone set;
- what can be proved from the available data;
- last reviewed observation time and exact event time.

Fingerprints, excluded-record reasons and provenance belong in progressive
disclosure. When no active definition exists, the primary Russian message is:
«Для этой цели пока не определено, как измерять прогресс.» The surface may
offer an owner action to create a definition, but it must not generate one,
infer a metric or display a completion percentage.

## 19. Safe Write and validation boundary

The future write workflow is:

1. parse a bounded reviewed draft;
2. validate exact Goal UUID and current identity fingerprint;
3. validate one branch and all cross-field invariants;
4. validate timestamp, decimal, bounds and marker policy;
5. build a dry-run plan with exact operation and plan fingerprint;
6. present the draft and replacement relation for owner review;
7. perform atomic no-overwrite Safe Write;
8. run full-vault validation after the write;
9. rollback on post-write validation failure;
10. reread current Goal and records before returning success.

No inferred baseline, target, unit, milestone state or event time may enter a
write plan. A provider response, transcript or behavior-derived suggestion
can at most become an untrusted draft that still requires review.

## 20. Determinism and rebuildability

Given the same current-vault snapshot, exact Goal UUID, valid active definition,
eligible observations and explicit as_of, the result must be byte-for-byte
stable apart from transport formatting.

The builder must:

- reread current source notes before evaluating;
- validate all referenced UUIDs and fingerprints;
- apply the same chain and time filters in every entrypoint;
- sort only by declared exact fields;
- use Decimal, not float;
- keep excluded-record reasons deterministic;
- avoid random IDs, current time, filesystem order and provider calls;
- expose enough provenance for a local rebuild.

Any cache is an optimization only. Cache loss must not change the result or
require vault mutation.

## 21. Safe error vocabulary

The future API/UI may use these bounded codes:

~~~text
GOAL_PROGRESS_REQUEST_INVALID
GOAL_PROGRESS_GOAL_REQUIRED
GOAL_PROGRESS_GOAL_AMBIGUOUS
GOAL_PROGRESS_GOAL_SOURCE_CHANGED
GOAL_PROGRESS_DEFINITION_MISSING
GOAL_PROGRESS_DEFINITION_INVALID
GOAL_PROGRESS_DEFINITION_STALE
GOAL_PROGRESS_DEFINITION_CONFLICT
GOAL_PROGRESS_OBSERVATION_INVALID
GOAL_PROGRESS_OBSERVATION_MISSING
GOAL_PROGRESS_OBSERVATION_SOURCE_CHANGED
GOAL_PROGRESS_UNIT_INCOMPATIBLE
GOAL_PROGRESS_MODEL_UNSUPPORTED
GOAL_PROGRESS_POLICY_MISMATCH
GOAL_PROGRESS_NOT_COMPARABLE
GOAL_PROGRESS_RESULT_TOO_LARGE
GOAL_PROGRESS_SOURCE_UNAVAILABLE
GOAL_PROGRESS_COMPARISON_UNSUPPORTED
GOAL_PROGRESS_SAFE_WRITE_REVIEW_REQUIRED
GOAL_PROGRESS_VAULT_CHANGED
GOAL_PROGRESS_INTERNAL
~~~

Messages must be Russian at user-facing surfaces, concise, actionable and
free of secrets, absolute paths, stack traces, provider details or private
value inventories. A missing definition message must explain that the owner
needs to review an explicit baseline/target or milestone set; it must not
invent a default metric.

## 22. Future implementation decomposition

Stage 12A and the explicit Stage 12B implementation are complete slices. The
remaining compatible vertical slices remain separately gated:

### Stage 12A — canonical records and pure validators

Implement bounded DTOs, marker parsing, Decimal canonicalization, exact
Goal/definition/observation validation, chain rules, deterministic
provider-free tests and read-only scan diagnostics. No Web/API, provider,
vault write or production side effect.

### Stage 12B — Safe Write

Add reviewed draft, dry-run plan, exact plan hash, atomic no-overwrite write,
full-vault validation and rollback for the two companion record kinds. Keep
the vault independent and use temporary fixtures. The implementation uses
typed definition/observation drafts, app-owned UUIDv7 identities, current
Goal/definition/lineage rereads before apply, and the existing receipt-guarded
filesystem writer. It adds no CLI/API/UI route, watcher, provider or automatic
capture.

### Stage 12C — deterministic Goal Progress builder/read model

Implement the GoalProgressResultV1 builder with exact arithmetic, milestone
state rules, status precedence and bounded provenance. Expose it through a
private owner-only read model with explicit Goal UUID and as_of. No automatic
capture.

### Stage 12D — Growth composition

Only after 12A–12C are green, define a narrow Growth composition surface.
Do not make Goal Progress an implicit input to Advisor or Learning, and do not
expand private payloads without a separate review.

### Stage 12E — Web/API QA and closeout

Add exact integration tests, frontend states, security checks, required CI,
deployment/env audit and closeout evidence. A production release remains
owner-gated by the repository lifecycle.

The exact next slices after Stage 12B, if separately approved, are Stage 12C
through Stage 12E. This issue does not start them or any Stage 13+ work.

## 23. Decision register

| Decision | V1 choice | Deferred |
| --- | --- | --- |
| Canonical authority | Separate reviewed companion records | Alternative schema strategies |
| Goal binding | Exact UUID plus identity fingerprint | Fuzzy/same-text binding |
| Active definition count | One per exact Goal | Multiple trackers |
| Numeric models | Increase, decrease, exact target | Maintain range |
| Baseline | Explicit definition field | Derived baseline |
| Observation time | Exact event time required for comparison | Created-time fallback |
| Corrections | New record plus supersedes | In-place mutation |
| Result | Deterministic bounded status | Score, percentage, forecast |
| Provider | None | Any provider/network flow |
| Vault | No change in this design | Migration/new note type |
| Stage 2 Outcome | Remains descriptive | Automatic parsing bridge |

## 24. Design-gate acceptance

The Stage 12 design gate is accepted only when all statements below remain
true in the implementation PR:

- exact Goal identity and drift behavior are tested;
- definition and observation records are separate and reviewed;
- baseline is explicit and no fallback exists;
- numeric and milestone branches are mutually exclusive and bounded;
- unknown event time is excluded from comparison;
- replacement chains are append-only and deterministic;
- Outcome, Growth, Behavior and Calibration authority remains separate;
- no provider, network, telemetry, background watcher or automatic write-back;
- no change to second-brain-vault;
- no new environment variable or systemd requirement;
- no unrelated runtime or dependency change;
- env change required: no unless a later diff proves otherwise;
- final checks run with Python 3.14 and exact-head CI evidence;
- no Codex Review request, Vault Sync or next-stage auto-start.

Current design-gate state:

~~~text
runtime changed: YES (read-only Stage 12A parser, validators and scan projections)
canonical schema changed: NO (additive marker, no NoteType/schema_version change)
Safe Write changed: NO
provider/network changed: NO
vault changed: NO
environment changed: NO
env change required: no
Stage 12A started: YES
Stage 12A implementation: COMPLETE (PR #280 merged and deployed)
Stage 12A post-merge CI: PASS
Stage 12A production deploy: PASS
Stage 12A `/healthz`: HTTP 200
Stage 12B–12E started: NO
Stage 13+ started: NO
HUMAN_REQUIRED: NO; Stage 12A implementation and closeout are complete; no
blocker remains
~~~
