
# Decision Compass / Compare v2 / GrowthCompare v1

**Статус:** DESIGN / NORMATIVE CONTRACT GATE COMPLETE; отдельная реализация
Stage 13A без провайдера завершена в [Issue #299](https://github.com/MikeMoore1337/second-brain/issues/299)
и [PR #305](https://github.com/MikeMoore1337/second-brain/pull/305), merge SHA
`9444a0936ed0df6ecf3a84fb65eb8bbd6d910e6f`. Post-merge CI `34900681934`,
production deploy `34900890972`; этапы 13B–13E остаются **NOT STARTED**.

**Issue:** [#296](https://github.com/MikeMoore1337/second-brain/issues/296)

**Design-base snapshot:** origin/main at
'411fc05fde1c177a601dd3c343d1d8d4b109a30d' (the exact snapshot used for this
gate). The snapshot is the post-Stage-12E production baseline.

This document is the normative design contract for Cognitive Twin v3 / Stage
13. It defines an additive Decision Compass composition, also called Compare
v2 or GrowthCompare v1 in product and implementation discussions. It does not
authorize implementation, a new issue, a new provider integration, a Web/API
surface, a database, a vault change, a Safe Write operation, or a production
release. Existing Stage 12 contracts remain authoritative for their own DTOs,
policies and lifecycle.

HUMAN_REQUIRED: none. Where a source is unavailable, stale, ambiguous or
outside the contract, this design chooses the conservative result
'not_selected', 'not_comparable', abstention or a bounded error. It never
silently guesses.

## 1. Decision and scope

Decision Compass answers one bounded question:

> Given one exact current Goal and one explicit decision request, what do the
> independent layers say, what was explicitly observed in a selected current
> behavioral cohort, what reviewed Growth relation and measured Goal Progress
> are available, and what does the independent Advisor say if the owner
> explicitly asks for it?

The output is a readable, typed composition of independent authorities. It is
not an oracle that chooses a winner. It is not a universal score, a ranking,
an optimization function, a personality judgement, a causal model or a hidden
reward.

The Stage 13 gate covers only:

1. the normative authority and privacy boundaries;
2. the exact request-local option and criterion namespaces;
3. one exact selected Goal and one optional exact behavioral cohort;
4. additive composition of Simulate Me, Behavioral Self Model, Growth,
   Goal Progress and the existing independent Growth Advisor;
5. structural exact-ID relations and typed branch states;
6. bounded errors, temporal semantics, provenance and future decomposition;
7. the implementation and verification boundary for a later, separately
   authorized slice.

The gate explicitly does not include:

- Stage 13A or any other Stage 13 runtime code;
- changes to src/second_brain/application/compare.py, the Compare v1 route,
  the Compare v1 UI or any existing DTO/policy;
- a new NoteType, schema_version, vault record, database, cache authority,
  mapping store or Safe Write path;
- a provider call, provider payload, credential, environment variable or
  network behavior;
- a Web/API/UI implementation, PWA storage, telemetry, background job or
  automatic Advisor execution;
- Growth Learning, Stage 14 Experiments or Stage 15 Adaptive Cognitive Twin.

The only normative artifact produced by this gate is this contract, with the
minimal status cross-references in the two roadmap documents named by Issue
#296.

## 2. Non-negotiable invariants

The following rules apply to every future implementation and every future
consumer of the result.

1. **Exact Goal authority.** The owner selects one current Goal by the exact
   Stage 11A source UUID and GrowthGoalIdentityV1 identity fingerprint. Goal
   text is never an identity selector.
2. **Independent authorities.** The exact selected Goal, likely self-choice,
   reviewed current behavior, owner-reviewed Goal relation, measured Goal
   Progress, independent Advisor and explicit criteria remain separate layers.
3. **No hidden winner.** The composition never emits or derives a winner,
   best option, universal rank, weighted score, life score, alignment score,
   probability of success or moral/psychological verdict.
   In particular, these identifiers are forbidden in the Stage 13 v1 result
   and policy: `overall_score`, `best_option`, `winner`, `goal_optimal`,
   `growth_optimal`, `life_score`, `alignment_score`, `success_probability`,
   `recommended_because_you_are_like_this`, `habit_score`,
   `discipline_score` and `progress_score`. A later contract would need its
   own design gate before introducing any of them.
4. **Exact namespaces.** Request option IDs, behavioral option indices and
   labels have different namespaces. A label is never an identity bridge.
5. **Explicit bridge only.** A request option can be compared with a
   behavioral option only through the bounded exact binding defined here.
6. **One cohort in v1.** A behavioral view is either one explicit exact
   current cohort or not_selected. No semantic cohort selection,
   first/newest selection or aggregation is permitted.
7. **Provider-free base.** The core composition is deterministic and does not
   call a provider. The Advisor is a separate explicit action using the
   existing Growth Advisor contract.
8. **Goal Progress is context.** Stage 12 Goal Progress describes a selected
   Goal at an explicit as_of; it is not evidence that one decision option is
   better, successful or causal.
9. **Reviewed Growth relation is context.** supports_goal and
   conflicts_with_goal are owner-reviewed relations for an exact mapping;
   they are not a winner, recommendation, causal claim or personal verdict.
10. **No temporal causality.** Different timestamps are preserved as separate
    provenance facts. Temporal order never proves that a choice caused a Goal
    or Progress result.
11. **No silent writes.** A read or composition cannot mutate the vault,
    canonical records, mappings, Goal definitions, Progress definitions,
    browser storage or Advisor history.
12. **Fail closed.** Drift, source inconsistency, invalid identity, excessive
    size and policy mismatch are visible bounded states or errors; they are
    never repaired by fuzzy matching or a fallback authority.

## 3. Authority map and allowed data flow

| Layer | Authoritative source | May contribute | Must never contribute |
| --- | --- | --- | --- |
| Selected Goal | Current Stage 11A scan -> build_report -> existing Self Model -> exact Goal identity | One Goal UUID, identity fingerprint, bounded owner-visible Goal projection and exact source/provenance | A Goal chosen by text, newest/domain-only heuristic, multiple hidden Goals, option ranking or provider context by automatic union |
| Decision request | Current owner request | Task, request-local options, explicit constraints/context and explicit criteria | Private memory, inferred criteria, historical choices or labels treated as authority |
| Simulate Me | Existing Stage 6/9 Simulate Me contract and current approved Self Model boundary | Likely owner choice or typed abstention, exact request option ID and its evidence references/caveats | Recommendation, best/optimal claim, Growth relation, Goal Progress, Advisor result or behavioral fact |
| Behavioral Self Model | Existing Stage 10 current exact cohort/pattern read model | Descriptive current pattern, exact behavioral cohort/option identity, support counts and caveats | Recommendation, Goal relation, Progress, moral/trait label, semantic nearest option or cross-namespace ID |
| Growth | Existing Stage 11 owner-reviewed exact Goal-to-choice relation | Exact relation state, mapping identity, reviewed timestamps and current behavioral target | Recomputed raw behavior, universal optimality, causal “leads to Goal”, progress or Advisor payload input |
| Goal Progress | Existing Stage 12D GrowthGoalProgressCompositionResultV1 for one Goal and explicit as_of | Typed Growth and Goal Progress companion results and their independent caveats | An option score, success/winner, outcome interpretation or Growth relation inferred from numeric progress |
| Independent Advisor | Existing Stage 11C Growth Advisor / Assistant contract, only after explicit owner action | Independent recommendation/analysis/abstention/error and existing compact provenance | Automatic private context, Behavioral evidence, Growth internals, Goal Progress, Simulate Me evidence, raw vault bodies or hidden criteria |
| Structural composition | This contract | Exact IDs, typed states, pairwise structural relations, source and policy provenance | Semantic agreement, alignment, quality, correctness, winner, ranking or causal interpretation |

The composition may display all of these layers together, but it may not promote
a value from one row into another row's authority. In particular:

- a likely choice does not become a recommendation;
- an observed choice does not become a Goal;
- a reviewed supports_goal relation does not become a winning option;
- a target-met progress status does not become proof that a decision was good;
- an Advisor result does not rewrite Simulate Me, Behavior, Growth or Progress.

## 4. Relationship to Compare v1

Compare v1 remains unchanged and additive. Its normative meaning is exactly:

~~~text
Assistant independent branch
  + Simulate Me independent branch
  + structural Delta over typed terminal states and exact request-local IDs
~~~

The existing CompareRequestV1, CompareOptionV1, Assistant input bounds,
Simulate Me policy, branch wrappers, execution controls, fixed error taxonomy,
structural Delta vocabulary, policy identity and result cap remain in force.
The existing /api/compare route and Compare v1 UI retain their current
semantics and payloads.

Compare v2 is an additive outer composition. It may reuse Compare v1's exact
request-option and structural comparison rules for the Simulate Me versus
independent Advisor/Assistant projection, but it must not mutate or reinterpret
the v1 result. A future implementation must use a dedicated application
module (the preferred boundary is
src/second_brain/application/decision_compass.py) rather than growing
compare.py into a second semantic system.

No v2 field is backfilled into a v1 DTO, and no v1 branch is given hidden Goal,
Behavioral, Growth, Progress or Advisor context.

## 5. Exact selected Goal

The request contains one selected Goal selector:

~~~text
DecisionCompassGoalSelectorV1 {
  source_uuid: UUID
  identity_fingerprint: sha256 fingerprint
}
~~~

Both values are required. The server resolves the current Goal from the
existing Stage 11A authority and revalidates both values immediately before
composition. The client does not supply authoritative Goal text. A bounded
Goal projection in the result is server-owned and is not a new Goal record.

The following are invalid selectors or forbidden fallback behavior:

- selecting by Goal text, normalized text, label, alias, translation or
  embedding;
- selecting the newest Goal, the only Goal in a domain or the first Goal;
- selecting several Goals and aggregating them;
- silently replacing a missing/changed Goal with another Goal;
- deriving a Goal from Behavioral, Growth, Progress, Simulate Me or Advisor
  data.

If the Goal is unavailable, its UUID/fingerprint changes, the current scan is
inconsistent or the identity policy is not exact, the top-level result is a
bounded Goal/source error. No partial result may be presented as belonging to
the requested Goal.

The selected Goal is a context boundary, not an option selector. Its presence
does not change the meaning of the request-local options or the Simulate Me
policy.

## 6. Request-local namespaces and bounds

### 6.1. Decision options

DecisionCompassOptionV1 { id, label } is request-local. Its bounds and
normalization are exactly compatible with CompareOptionV1:

- id: ASCII, 1–64 bytes, regex
  [A-Za-z0-9][A-Za-z0-9._:-]{0,63}, unique in the request;
- label: 1–256 UTF-8 bytes after strict NFC normalization and edge-strip;
- options: 1–8, in request order;
- reject invalid UTF-8, C0/C1 controls, DEL, disallowed format characters,
  duplicate fields and unknown fields according to the existing Compare v1
  validator;
- an option label is presentation only and is never used as an identity key.

No request option can be implicitly imported from a Goal, note, Behavioral
cohort, Growth mapping, Advisor output or previous request.

### 6.2. Explicit criteria

Criteria are optional owner-authored presentation/reference inputs:

~~~text
DecisionCompassCriterionV1 {
  id:       request-local ASCII ID, 1–64 bytes, same ID grammar as options
  label:    1–256 UTF-8 bytes
  description: null or 1–512 UTF-8 bytes
}
~~~

The request allows 0–8 unique criteria and a total normalized criterion
payload of at most 8,192 bytes. Criteria have no weight, order of importance,
threshold, grade, confidence, score or hidden default. A criterion ID or label
is not evidence and cannot select a Goal, cohort or option.

Criteria may be shown beside the independent outputs. They may be supplied to
the explicit Advisor operation only when the owner deliberately represents
them inside the existing bounded explicit_constraints or explicit_context
fields supported by the Growth Advisor contract. There is no automatic
criteria-to-provider conversion and no new Advisor field in this gate.

### 6.3. Normative request envelope

The future provider-free request is conceptually the following bounded DTO.
It is additive to Compare v1 and does not change CompareRequestV1:

~~~text
DecisionCompassRequestV1 {
  contract_version:          "growth-compare-v1"
  task:                      1..4096 UTF-8 bytes
  options:                   1..8 DecisionCompassOptionV1 values
  selected_goal:             DecisionCompassGoalSelectorV1
  criteria:                  0..8 DecisionCompassCriterionV1 values
  explicit_constraints:      Compare v1 bounded caller-owned values
  explicit_context:          Compare v1 bounded caller-owned values
  progress_as_of:            explicit aware RFC3339 UTC timestamp
  behavioral_scope:          DecisionCompassBehaviorScopeV1 | null
  behavioral_option_binding: DecisionCompassBehaviorOptionBindingV1 | null
  max_result_bytes:          Compare v1 compatible bounded cap
}
~~~

The core may omit caller-owned constraint/context fields when a future UX does
not offer them, but it may not add implicit private context. The selected Goal,
progress cutoff and option namespace are always explicit. Unknown fields,
duplicate fields, invalid normalization and cap violations are rejected.

### 6.4. Explicit caller-owned fields

If the future core request exposes caller-owned constraints/context, it reuses
the exact Compare v1 bounds and normalization:

- explicit constraints: at most 16 items, 512 bytes each, 8,192 bytes total;
- explicit goals: not accepted as a second Goal authority;
- explicit context: at most 16 items, 1,024 bytes each, 16,384 bytes total;
- all strings use the existing strict UTF-8/NFC/control-character policy;
- the complete bounded result remains within the Compare v1 outer cap of
  131,072 bytes, with a minimum requested cap compatible with the v1 minimum.

The selected Goal does not silently become caller-owned explicit_goals for the
provider-free core. The existing Growth Advisor operation may inject its one
revalidated Goal into the existing Assistant explicit_goals envelope, exactly
as its own contract requires.

### 6.5. Behavioral scope selector

The optional scope is one exact current cohort:

~~~text
DecisionCompassBehaviorScopeV1 {
  behavioral_cohort_fingerprint: sha256 fingerprint
}
~~~

The fingerprint is a selector, not proof supplied by the client. The server
resolves the current Stage 10 cohort and validates its full
BehavioralCohortIdentityV1 (grouping policy, domain, situation, information,
option namespace, criteria and cohort fingerprints).

There is no domain, situation, free-text search, list of cohorts, “similar”
selector, newest selector or semantic selector in this contract. If the scope
is absent, the Behavioral branch is not_selected; the implementation must not
auto-pick a sole or newest cohort.

### 6.6. Request-local behavioral option binding

Only an explicit, bounded binding can cross the request and Behavioral
namespaces:

~~~text
DecisionCompassBehaviorOptionBindingV1 {
  request_option_id:             exact ID from this request
  behavioral_cohort_fingerprint: exact selected current cohort fingerprint
  behavioral_option_index:       exact Stage 10 option index, 0..19
  behavioral_option_fingerprint: exact Stage 10 option fingerprint
}
~~~

The binding is optional. If present, the behavioral scope must be present and
must have the same exact cohort fingerprint. The server rereads and revalidates
the current cohort, option namespace and option fingerprint. A label match is
never enough.

The binding is request-local and ephemeral. It is not a new Stage 10 mapping,
does not mutate the Stage 10 or Stage 11 mapping stores and does not create a
durable mapping between a request option and behavior. No binding is forced
when the owner has not supplied one.

Malformed bindings are invalid requests. A well-formed binding that is stale
or no longer current is not_comparable with a visible stale/unbound caveat; it
does not fall back to a label, case-fold, synonym, translation, embedding, LLM
or “closest” option.

## 7. Behavioral Self Model boundary

The Behavioral branch reads the existing Stage 10 current exact cohort result.
It may expose the typed descriptive pattern, exact behavioral option identity,
support counts/ratios and existing windows, outcome-presence metadata and
caveats. It does not recompute raw observations, reinterpret outcome text or
create a new behavioral policy.

The branch has one explicit cohort in v1 and these meaningful outcomes:

| Branch state | Meaning | Allowed next step |
| --- | --- | --- |
| not_selected | No exact cohort selector was supplied | Show that behavior was not selected; do not infer it |
| result | The selected current cohort has a valid typed Stage 10 pattern | Display it descriptively |
| insufficient | The current cohort has insufficient evidence under Stage 10 policy | Preserve the typed limitation |
| not_comparable | Cohort, option namespace or binding is stale/incompatible | Preserve the limitation and do not bridge it |
| unavailable | The selected exact cohort cannot be read from the current source | Preserve a bounded branch-local unavailability |
| error | A bounded Stage 10 read/validation failure | Preserve the typed error without sibling loss |

repeated_exact_choice, stable_over_time, mixed_exact_choices,
changed_over_time and insufficient_evidence retain their Stage 10 semantics.
Support counts are descriptive; they are not a preference score, probability,
confidence, discipline score or option ranking.

The Behavioral branch has no authority over:

- the request's likely or recommended choice;
- Goal identity or Goal selection;
- owner-reviewed Growth relation;
- Goal Progress status or numeric value;
- criteria interpretation, moral judgement, diagnosis or personality.

## 8. Growth and Goal Progress composition

### 8.1. Authoritative Stage 12D source

The growth_progress branch reuses the existing
GrowthGoalProgressCompositionResultV1 for exactly the selected Goal and an
explicit progress_as_of timestamp. It does not create a parallel composition,
recompute Stage 11 behavior or add an Advisor call.

The future request therefore has:

~~~text
progress_as_of: aware RFC3339 timestamp, normalized to UTC, explicitly supplied
~~~

There is no implicit “now” that would make two otherwise equal requests have
different semantic scope. The Stage 12D result keeps its own exact selected
Goal UUID/fingerprint, Growth and Goal Progress policies, typed nested results,
source drift checks and fixed caveats.

### 8.2. Growth authority

The Growth projection may expose only current Stage 11 semantics:

- an exact current Goal and exact Stage 11 behavioral target;
- an owner-reviewed relation supports_goal, conflicts_with_goal or
  neutral_or_unknown;
- typed states such as goal_mapping_missing, mixed_behavior,
  changed_behavior, behavioral_evidence_insufficient, not_comparable or
  goal_source_missing;
- mapping provenance, review/creation time and existing reasons/caveats.

supports_goal means only that the owner-reviewed relation says that the exact
mapped behavioral target supports the exact Goal in the current Growth policy.
conflicts_with_goal is not failure, weakness or self-sabotage.
neutral_or_unknown is not a hidden negative. Missing mapping remains
goal_mapping_missing, never conflicts_with_goal.

Growth does not recompute a raw behavior relation from this request. It does
not infer that a request option “leads to” a Goal from a temporal sequence,
label similarity, progress movement or Advisor language. A request option is
related to a Growth target only when the explicit request-local behavioral
binding exactly matches the current Growth target identity.

### 8.3. Goal Progress authority

Goal Progress preserves the Stage 12 statuses without renaming or collapsing
them:

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

It may include its bounded numeric/milestone observations, exact event times,
excluded references/counts, definition identity, explanation and provenance
under the Stage 12 contract. It does not include an option ID and cannot be
used as evidence that a choice won, succeeded or caused progress.

Outcome is not Progress. Free-text or descriptive Stage 2 Outcome remains
outside the automatic Goal Progress and option semantics.

### 8.4. Independent Stage 12 caveats

The following Stage 12D caveats remain visible and are not replaced with a
single “overall” interpretation:

~~~text
growth_and_progress_are_independent_layers
no_causal_claim
no_progress_inferred_from_growth
no_growth_relation_inferred_from_progress
temporal_alignment_not_proven
advisor_not_used
learning_not_used
~~~

The Compass may add only composition caveats defined by this contract, such
as behavioral_binding_missing, behavioral_scope_not_selected or
advisor_not_requested. It may not delete or weaken a Stage 12 caveat.

Growth Learning / Question remains a separate Stage 11D workflow. A Compass
read does not create, trigger, resolve or word a Learning question, and a
Learning candidate/result is not an option branch, Growth relation, Goal
Progress input or Advisor payload.

## 9. Simulate Me branch

Simulate Me remains the existing independent likely-choice prediction. It uses
the exact request-local options and its approved current Self Model evidence
boundary. It may return one exact request option prediction or a valid typed
abstention. It never returns a recommendation, best option, Goal-compatible
option, success claim, habit fact or Growth relation.

The Compare v1 policy and execution control remain unchanged:

- one bounded execution context and cancellation/deadline;
- no branch receives the other branch's result;
- no retry or fallback authority;
- exact whole-label/direct evidence semantics of Simulate Me;
- branch-local result, abstention or error wrapper;
- no provider call, persistence or score.

The Compass copies the typed Simulate Me result as a nested branch; it does
not normalize an abstention into “no winner” or reinterpret evidence as
criteria.

## 10. Existing Growth Advisor branch

### 10.1. Explicit action only

The Advisor branch is optional and starts as:

~~~text
advisor.state = not_requested
~~~

The provider-free Compass composition does not call an Advisor on page load,
read, rebuild, preview, opening, loading, refresh or Goal selection. The owner
must explicitly request the existing Growth Advisor preview/confirm/execute
operation under its own policy. A future UI must make this action visibly
separate from reading the Compass.

The branch states are:

| State | Meaning |
| --- | --- |
| not_requested | No explicit Advisor action occurred |
| result | Existing Growth Advisor returned a valid independent result |
| abstention | Existing Advisor validly abstained |
| error | Existing Advisor returned a bounded error |

The Compass does not silently rerun an Advisor result, persist it as history,
or turn a previous transient result into a current result without a new
explicit action.

### 10.2. Exact payload boundary

The Advisor operation reuses GrowthAdvisorRequestV1 and its exact
growth-advisor-owner-explicit-goal-v1 policy. The server injects one
revalidated selected Goal into the existing Assistant explicit_goals field.
The payload may contain only:

- caller-owned task and the exact request-local options;
- caller-owned explicit constraints/context within the existing caps;
- the one owner-approved current Goal projection required by the existing
  Growth Advisor contract.

It must not add:

- Behavioral Self Model evidence, cohort details, support counts or raw
  observations;
- Growth relation, mapping, friction, progress status or progress numbers;
- Simulate Me result/evidence, predicted option or private Self Model context;
- criteria by hidden automatic union (criteria are included only if the owner
  explicitly supplies their bounded representation through existing fields);
- vault bodies, note text, raw provider metadata, hidden prompts or other
  branches' provenance.

The existing Advisor result and compact provenance are retained exactly. The
generic Assistant port is not a substitute for the Growth Advisor policy, and
there is no new Advisor DTO in this gate.

## 11. Structural comparison semantics

The Compass compares only exact terminal identities. It never compares the
meaning, desirability, quality or semantic similarity of labels or evidence.

### 11.1. Closed relation vocabulary

For each eligible pair, a future result may contain exactly one of these
request-local relation codes:

~~~text
simulate_advisor_same_option
simulate_advisor_different_options
simulate_advisor_not_comparable

simulate_behavior_same_option
simulate_behavior_different_options
simulate_behavior_not_comparable

advisor_behavior_same_option
advisor_behavior_different_options
advisor_behavior_not_comparable

behavior_binding_missing
behavior_scope_not_selected
advisor_not_requested
~~~

same_option means the two relevant values are the same exact request-local
option ID. different_options means both values are exact request-local IDs and
differ. not_comparable means at least one side has no selected terminal option,
is an abstention/error/unsupported state, or the bridge is stale. The relation
carries the branch states and, only when present, the exact IDs; it does not
carry a “why”, score or semantic explanation.

The simulate_behavior_* and advisor_behavior_* relations require the explicit
request-to-behavior binding from Section 6.6. A Behavioral option
index/fingerprint cannot be compared with a request ID merely because labels
look equal. A Growth supports_goal relation and a Goal Progress status are
never structural option relations.

### 11.2. Pair independence matrix

| Pair | Permitted comparison | Forbidden promotion |
| --- | --- | --- |
| Simulate Me <-> Advisor | Exact request-option IDs when both branches select | “Advisor beats prediction”, “recommended because predicted” |
| Simulate Me <-> Behavior | Exact request ID <-> bound exact behavioral option | “Observed behavior validates/invalidates prediction” |
| Advisor <-> Behavior | Exact request ID <-> bound exact behavioral option | “Advisor should follow habit”, “habit is wrong” |
| Behavior <-> Growth | Exact Stage 10 identity inside the reviewed Stage 11 mapping | Recomputing a Goal relation from raw behavior or labels |
| Growth <-> Goal Progress | Side-by-side typed Stage 12D layers | Inferring relation from progress or progress from relation |
| Criteria <-> any branch | Display/reference or explicitly caller-supplied Advisor input | Weights, hidden score, inferred evaluation |
| Goal <-> any option | Context and exact provenance only | Goal-optimal option, objective choice, winner |

### 11.3. Examples of permitted disagreement

These are valid results and must remain visible as independent facts:

1. Simulate Me selects A, the explicit Advisor selects B, Behavior is bound
   to B, Growth says supports_goal, and Goal Progress is toward_target. The
   output contains those facts and exact relations; it does not recommend B or
   call B the winner.
2. Simulate Me selects A, Behavior displays an observed option with a label
   equal to A, but no explicit binding exists. The output is
   behavior_binding_missing, not same.
3. Goal Progress is target_met while the Advisor is not_requested and Simulate
   Me abstains. The output does not fabricate a recommendation or claim that
   any option achieved the target.
4. Growth says conflicts_with_goal for a reviewed exact mapping while Goal
   Progress is toward_target. Both typed states remain; the composition does
   not resolve the tension or infer causality.

## 12. Normative result shape

The future immutable bounded result is conceptually:

~~~text
GrowthCompareResultV1 {
  contract_version:       "growth-compare-v1"
  derivation_version:     "compare-v2"
  policy_id:              "growth-compare-decision-compass-v1"
  policy_fingerprint:     sha256 of the exact policy serialization

  selected_goal:          exact Goal selector and bounded current projection
  request:                validated task, options, criteria and selectors
  simulate_me:            existing typed Simulate Me branch
  behavioral:             typed one-cohort branch
  growth_progress:        existing GrowthGoalProgressCompositionResultV1
  advisor:                typed explicit Advisor branch
  structural_relations:   bounded tuple of closed exact-ID relations
  caveats:                stable deduplicated tuple of bounded codes
  provenance:             policy/source/time identities without raw bodies
}
~~~

This is a contract shape, not a request to add the DTO now. The nested
Simulate Me, Behavioral, Growth, Goal Progress and Advisor results retain
their own typed versions, policy IDs, fingerprints, states and caveats. The
outer result may not flatten them into a generic dictionary or replace them
with a single status.

The result is immutable for its lifetime. It is bounded by the Compare v1
outer result cap of 131,072 bytes and must be rejected rather than truncated.
It contains no raw note body, raw transcript, provider response metadata,
secret, absolute path, exception representation or unbounded evidence list.

### 12.1. Branch-local state and top-level validity

A branch-local failure preserves valid sibling branches. For example, an
Advisor timeout does not remove a valid Simulate Me or Goal Progress result,
and a missing Behavioral scope does not hide the selected Goal.

The top level fails closed when composition cannot be trusted as one selected
Goal request, including:

- malformed request, duplicate/unknown fields or bound overflow;
- selected Goal missing, changed or identity-policy mismatch;
- a required Stage 12D source inconsistency or cross-source identity drift;
- invalid request-local binding shape;
- policy mismatch between nested authoritative results;
- result serialization exceeding the fixed cap;
- cancellation/deadline before a valid bounded result exists.

Provider/Advisor errors, Behavioral unavailability, valid Simulate Me
abstention and Goal Progress definition_missing remain branch-local when the
outer identity and source contract are still valid.

### 12.2. Bounded error vocabulary

The future outer implementation uses stable safe codes at minimum:

~~~text
DECISION_COMPASS_INVALID_REQUEST
DECISION_COMPASS_GOAL_REQUIRED
DECISION_COMPASS_GOAL_UNAVAILABLE
DECISION_COMPASS_GOAL_SOURCE_CHANGED
DECISION_COMPASS_BEHAVIORAL_SCOPE_INVALID
DECISION_COMPASS_OPTION_BINDING_INVALID
DECISION_COMPASS_SOURCE_UNAVAILABLE
DECISION_COMPASS_SOURCE_CHANGED
DECISION_COMPASS_POLICY_MISMATCH
DECISION_COMPASS_RESULT_TOO_LARGE
DECISION_COMPASS_CANCELLED
DECISION_COMPASS_TIMEOUT
DECISION_COMPASS_INTERNAL
~~~

Exact messages are fixed, short, Russian user-safe templates in the future
implementation. Errors never include raw input beyond a safe field/code,
private evidence, Goal body, note UUID lists, provider details, exception
repr, credentials or filesystem paths.

## 13. Temporal semantics

Every temporal fact keeps its own source and meaning:

| Time | Authority/meaning | What it cannot prove |
| --- | --- | --- |
| Goal evidence/source time | Existing Goal evidence and current identity | That the Goal caused a later choice or progress |
| Behavioral evidence window | Stage 10 observations included by current cohort policy | That an observed pattern is a preference, Goal or recommendation |
| Behavioral result generated time | Read-model generation time | That the result was true outside the current source snapshot |
| Growth mapping reviewed/created time | Owner-reviewed Stage 11 relation lifecycle | That the relation caused Progress |
| Goal Progress event/observation times | Stage 12 reviewed companion records | That a choice caused the measured value |
| progress_as_of | Explicit Stage 12 evaluation cutoff | That a later event was known at that cutoff |
| Simulate Me generated/current-context time | Prediction read-model time and caveat | That the prediction was a historical fact |
| Advisor requested/generated time | Explicit independent Advisor operation | That the result was persisted, acted on or correct |
| Composition generated time | Outer read-model assembly time | Any causal sequence between branches |

Timestamps are aware RFC3339, normalized to UTC, and retain the nested
contract's precision/validation rules. Missing, invalid or ambiguous times
remain the nested source's limitation. The composition must include the
existing temporal_alignment_not_proven caveat where applicable.

The following inference is forbidden even when event order looks persuasive:

~~~text
choice at T1 -> Goal relation at T2 -> progress at T3
~~~

This sequence is a provenance display only. It is not a causal claim, Outcome
classification, experiment result or adaptation signal.

## 14. Privacy and provider-payload matrix

The provider-free core and the explicit Advisor operation have separate data
boundaries:

| Source | May be read by core | May be in existing Advisor payload | Forbidden automatic crossing |
| --- | --- | --- | --- |
| Selected Goal | Exact identity, bounded current projection and existing source refs | One owner-approved current Goal projection injected by server | Fuzzy Goal search; multiple Goals; Goal inferred from another branch |
| Request task/options | Exact caller request | Exact caller task/options | Replacing IDs with labels or adding hidden options |
| Explicit constraints/context | Caller-owned bounded fields | Same fields under Growth Advisor caps | Private memory or inferred facts |
| Criteria | Bounded presentation/reference fields | Only if owner deliberately supplies a bounded representation through existing fields | Automatic criteria union, weights or provider-generated criteria |
| Simulate Me | Typed branch result/caveats in transient composition | Never | Predicted ID, evidence or private context |
| Behavioral Self Model | Typed descriptive branch and exact identity | Never | Cohort, support counts, observations or labels as hidden advice |
| Growth | Typed Stage 11 relation/provenance | Never | Relation, mapping, friction or raw Growth context |
| Goal Progress | Typed Stage 12D result and numeric/milestone values in owner result | Never | Progress values, status or observations |
| Vault/note bodies | Only where an existing nested contract explicitly allows bounded transient owner display | No new body source | Raw body union, search corpus, note history or hidden retrieval |
| Provider metadata | No base read | Only existing safe result/provenance fields | Raw prompt, response, credentials or infrastructure detail |

The top-level result is a bounded read model. It does not concatenate raw
branches or create a “context blob”. No branch can use the Compass result as
an implicit provider context in the same operation.

## 15. Persistence, canonical state and side effects

Stage 13 core is provider-free, read-only and rebuildable from current
authoritative sources. It introduces:

- no canonical vault record;
- no new NoteType or global schema_version;
- no Goal/Progress/Behavioral/Growth mutation;
- no new database, cache authority, event log or durable Advisor history;
- no Safe Write, Vault Sync or review token;
- no browser localStorage, sessionStorage, PWA offline record or hidden
  cross-request state;
- no new dependency, credential or environment key.

An explicit Advisor result is a transient/reference branch governed by the
existing Growth Advisor retention and privacy rules. The Compass must not
silently persist or replay it. Existing Stage 11 operational mapping state
remains the authority for existing reviewed mappings; this contract does not
extend that store.

Deletion of a derived Compass result must not remove or alter any canonical
Goal, observation, relation, progress record or journal. A later rebuild may
produce a different bounded read model when the current source or policy has
changed; its exact policy fingerprint and source drift must be visible.

## 16. Future Web/API/UX boundary (not implemented here)

If a later implementation adds a surface, it must be a new owner-only Decision
Compass surface or section. It must not overload the existing Growth route or
change the existing Compare v1 route/UI semantics.

The information hierarchy is:

1. selected Goal identity and exact source freshness;
2. the owner task, options and explicit criteria;
3. Simulate Me likely choice/abstention;
4. optional selected Behavioral cohort and descriptive current pattern;
5. exact reviewed Growth relation and its caveat;
6. measured Goal Progress at the explicit as_of;
7. independent Advisor result only after an explicit action;
8. structural exact-ID relations and technical provenance.

Every layer remains visibly labelled by its authority. The UI must not use a
single “recommended”, “best”, “aligned”, “healthy”, “winning” or “score” card
for the composition. It must show not_requested, not_selected,
not_comparable, abstention and error states as distinct states, with safe
Russian copy and no raw private data.

Any future route must preserve the existing owner-only, strict same-origin,
no-store, bounded-body, CSP/security-header, CSRF and authentication
boundaries. A browser request cannot select a Goal by text or ask the server to
expand provider context. Cross-Goal leakage is a security failure.

The Stage 13 gate itself adds no route, UI, API schema, browser storage or
physical-device/browser/mock-TMA claim.

## 17. Policy identity and deterministic core

The future implementation must serialize one explicit policy object with
canonical UTF-8 JSON (sort_keys=true, separators ',' and ':', no BOM, no
trailing newline) and compute its SHA-256 fingerprint. The policy must contain
at least these exact semantic keys:

~~~json
{
  "advisor": "growth-advisor-v1-explicit-optional",
  "behavior_scope": "one-explicit-current-cohort-or-not-selected-v1",
  "behavior_option_binding": "exact-request-option-to-stage10-option-or-unbound-v1",
  "bounds": "compare-v1-option-bounds-plus-bounded-criteria-v1",
  "causal_inference": "forbidden",
  "criteria": "explicit-non-scored-v1",
  "cross_branch_inference": "structural-exact-identity-only-v1",
  "goal": "exact-selected-goal-uuid-and-growth-identity-v1",
  "growth": "stage11-owner-reviewed-relation-v1",
  "progress": "stage12d-goal-level-context-explicit-as-of-v1",
  "provider": "base-provider-free-advisor-explicit-only-v1",
  "simulate_me": "simulate-me-v1-independent-prediction-v1",
  "winner": "forbidden",
  "write": "forbidden",
  "version": "1"
}
~~~

The semantic identifiers are:

~~~text
contract_version   = growth-compare-v1
derivation_version = compare-v2
policy_id          = growth-compare-decision-compass-v1
~~~

The fingerprint is intentionally not invented in this design-only change. A
future implementation must compute it from the exact committed policy
serialization and add deterministic tests before exposing the result. A
policy mismatch is a safe error, never a best-effort compatibility mode.

The base composition may be rerun deterministically for the same validated
source snapshot and request. Fresh source reads, current timestamps and an
explicit Advisor operation are not promised to be byte-identical. No provider
call is made by opening, loading or rendering the base result.

## 18. Future implementation decomposition

This contract authorizes no implementation. If a later owner-authorized slice
is opened, the smallest compatible decomposition is:

~~~text
13A  provider-free Decision Compass core and bounded DTOs
13B  explicit Growth Advisor handoff using the existing v1 boundary
13C  new owner-only Web/API/UI surface and Russian state copy
13D  security/privacy/integration/e2e regression coverage
13E  exact-head delivery, deployment smoke and closeout
~~~

13A must compose existing Stage 10–12 ports/read models without duplicating
their policies. 13B must not add a provider payload field. 13C must leave
Compare v1 and Growth v1 behavior unchanged. 13D must test namespace
separation, stale Goal/cohort/binding, branch-local failures, no provider call
on read, no persistence and no cross-Goal leakage. 13E is a separate serial
release gate; a changed base/head invalidates prior final-gate evidence.

No implementation issue is created by this gate. A future issue must reference
this contract, choose an exact slice, declare its dependency map and repeat
the environment/persistence/provider review.

## 19. Design alternatives register

| Decision | Accepted design | Rejected alternative and reason |
| --- | --- | --- |
| Compare v1 evolution | Additive outer composition; v1 unchanged | Mutating compare.py/v1 DTOs would change a production contract and invalidate existing clients |
| Behavioral selection | One explicit exact current cohort or not_selected | Auto-selecting by domain, newest, first or semantic similarity hides authority and can leak another context |
| Advisor execution | Explicit owner action; base is provider-free | Auto Advisor on load/read would create provider, privacy, latency and consent side effects |
| Goal Progress meaning | Goal-level descriptive context at explicit as_of | Making Progress an option score or winner would cross the Stage 12 authority boundary |
| Criteria semantics | Explicit non-scored presentation/reference | Weights, grades or hidden criteria would create an unreviewed ranking oracle |
| Goal cardinality | Exactly one explicit Goal | Multiple Goals or an aggregate would hide Goal identity and create an unapproved universal context |
| Cohort cardinality | At most one explicit cohort in v1 | Aggregating cohorts would turn descriptive observations into an unsupported summary |
| Provider boundary | Provider-free core; existing explicit Growth Advisor only | Requiring a provider for the Compass would make read-only context unavailable and expand payload/privacy scope |
| Cross-namespace binding | Request-local exact option <-> cohort <-> Stage 10 option binding | Durable mapping would create a new state authority and silent label equality would be unsafe |
| Growth relation | Reuse current owner-reviewed Stage 11 relation | Recomputing relation from raw behavior, Progress or temporal order would create a new causal authority |
| Disagreement | Preserve every branch and exact structural relation | Collapsing disagreement into alignment, confidence or winner would defeat the anti-echo-chamber boundary |
| Advisor identity | Existing Growth Advisor policy/result | Generic Assistant substitution would bypass Goal revalidation and existing privacy/provenance rules |
| Persistence | Immutable transient read model | Cache/database/history would need a separate retention, deletion and privacy gate |

## 20. Acceptance and verification boundary

The design gate is accepted only when the following statements remain true:

| Check | Normative expectation |
| --- | --- |
| Contract artifact | This file is the only new Stage 13 normative contract |
| Compare v1 | Semantics, DTOs, policy, route and UI unchanged |
| Goal | One exact UUID + identity fingerprint; no fuzzy/multiple fallback |
| Options | Request-local exact IDs; bounds compatible with Compare v1 |
| Behavior | One explicit current cohort or not_selected; no aggregation |
| Binding | Exact request ID <-> cohort fingerprint <-> option index/fingerprint only |
| Growth | Stage 11 owner-reviewed relation only; missing mapping is not conflict |
| Progress | Stage 12D exact selected Goal + explicit as_of; context only |
| Advisor | Existing Growth Advisor, explicit action, no automatic payload expansion |
| Criteria | Explicit, bounded, non-scored, no inferred weights |
| Relations | Closed exact-ID structural states; no semantic winner |
| Time | Independent timestamps; no causal inference |
| Privacy | No raw body union; no automatic cross-branch provider payload |
| Persistence | No new schema/NoteType/DB/cache/Safe Write/vault change |
| Future surface | New Decision Compass surface, not Compare v1/Growth overload |
| Runtime | No Stage 13 implementation starts in this gate |

For this design-only change, normal repository verification is documentation
and repository hygiene verification only: git diff --check, Markdown/link
checks available in the repository, and the standard Python 3.14 quality
commands required by AGENTS.md. No live provider, vault, Web/API, browser,
physical-device or production behavior claim is made by the contract.

Ниже приведён исторический acceptance record для design-гейта Issue #296.
Отдельная авторизованная реализация Stage 13A завершена в Issue #299 и PR #305;
она не меняет этот нормативный контракт и не авторизует этапы 13B–13E.

The final delivery report must explicitly state:

~~~text
runtime changed: NO
Compare v1 semantics changed: NO
Stage 13 normative contract: YES
Stage 13 implementation started: NO
provider/network changed: NO
provider payload expanded: NO
new dependency: NO
new persistence: NO
new Safe Write: NO
global schema_version changed: NO
new NoteType: NO
Web/API/UI changed: NO
second-brain-vault changed: NO
env change required: no
Stage 14 started: NO
Stage 15 started: NO
~~~

## 21. Source contracts and references

The following existing documents and runtime boundaries were inspected for
this gate and remain authoritative for their own semantics:

- [Cognitive Twin v3 roadmap](cognitive-twin-v3-roadmap.md)
- [Design roadmap v1](design-roadmap-v1.md)
- [Compare v1](compare-v1-contract.md)
- [Assistant v1](assistant-v1-contract.md)
- [Simulate Me v1](simulate-me-v1-contract.md)
- [Behavioral Self Model v1](behavioral-self-model-v1-contract.md)
- [Growth Engine v1](growth-engine-v1-contract.md)
- [Growth Advisor v1](growth-advisor-v1-contract.md)
- [Goal Progress v1](goal-progress-v1-contract.md)
- [Growth + Goal Progress composition v1](growth-goal-progress-composition-v1-contract.md)
- [Growth Learning / Question v1](growth-learning-v1-contract.md)
- [Prospective Audit & Calibration v1](prospective-audit-calibration-v1-contract.md)

The existing runtime sources inspected for compatibility are the Compare,
Simulate Me, Behavioral Self Model, Growth, Growth Advisor, Goal Progress and
Growth + Goal Progress composition application modules, plus the current
owner-only Assistant/Compare and Growth Web surfaces. Inspection does not
authorize changing those sources in Stage 13's design gate.
