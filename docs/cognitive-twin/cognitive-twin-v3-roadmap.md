# Cognitive Twin v3 — high-level roadmap

Статус документа: **DESIGN / HIGH-LEVEL ROADMAP**.

Документ задаёт границы Cognitive Twin v3 после завершения Cognitive Twin v2 /
Stage 11E. Он не является runtime-контрактом, не меняет vault, не создаёт
новые note types и не разрешает провайдерный, фоновый или автоматический
write-back. Для текущего implementation gate нормативным документом является
отдельный [Goal Progress v1 contract](goal-progress-v1-contract.md). Для Stage
14 нормативным документом после design gate является отдельный
[Personal Experiments v1 contract](personal-experiments-v1-contract.md).

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
production deploy. Stage 12B COMPLETE в Issue #284 / PR #285; merge SHA
`1832dc66de2ea25b41bb0d613ea77aae4dcd1fe2` прошёл [post-merge CI #34851102286](https://github.com/MikeMoore1337/second-brain/actions/runs/34851102286)
и [standard production deploy #34851314130](https://github.com/MikeMoore1337/second-brain/actions/runs/34851314130); non-mutating `/healthz` вернул HTTP 200. Stage 12C COMPLETE в Issue #287 / PR #288: deterministic private read model, без Web/API route, provider, network или записи. Stage 12D COMPLETE в [Issue #290](https://github.com/MikeMoore1337/second-brain/issues/290) / [PR #291](https://github.com/MikeMoore1337/second-brain/pull/291), merge SHA `d78da72fef9f080bedfbd3ad1c87d40e49d227e8`, [post-merge CI #34877206164](https://github.com/MikeMoore1337/second-brain/actions/runs/34877206164), [standard production deploy #34877425776](https://github.com/MikeMoore1337/second-brain/actions/runs/34877425776), non-mutating `/healthz` HTTP 200. Composition bounded side-by-side для одного explicit current Goal, без cross-branch inference, Web/API/UI, provider/network или writer. Stage 12E COMPLETE в [Issue #293](https://github.com/MikeMoore1337/second-brain/issues/293) / [PR #294](https://github.com/MikeMoore1337/second-brain/pull/294), merge SHA `6068e1298ea3637d413498b72f397690a0278172`, [post-merge CI #34886130422](https://github.com/MikeMoore1337/second-brain/actions/runs/34886130422), [standard production deploy #34886348462](https://github.com/MikeMoore1337/second-brain/actions/runs/34886348462). Non-mutating production smoke on 2026-09-14 22:24 MSK: `/healthz` HTTP 200, public shell HTTP 303 `/login`, anonymous POST to both new private routes HTTP 401 `AUTH_REQUIRED`. Stage 12A–12E complete; Stage 13+ не начаты.

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
- Stage 12B explicit reviewed Safe Write с dry-run/hash/apply/full validation и
  rollback для companion records;
- Stage 12C provider-free deterministic `GoalProgressResultV1` builder/private
  read model с explicit Goal UUID, UTC `as_of`, numeric/milestone evaluation и
  bounded provenance;
- no provider, telemetry, background inference, automatic capture или
  изменения second-brain-vault.

Stage 12 не переносит progress fields в исходный Goal и не превращает
описательный Stage 2 Outcome в структурированный progress fact.

## Stage 13 — Decision Compass / Compare v2

**Статус:** DESIGN CONTRACT COMPLETE in [Issue #296](https://github.com/MikeMoore1337/second-brain/issues/296)
via [compare-v2-contract.md](compare-v2-contract.md) and [PR #298](https://github.com/MikeMoore1337/second-brain/pull/298)
(`452a51fda49fd7ed30e116b55f73fe457ae3d4c7`); Stage 13A–13D и
финальный Stage 13E release/closeout COMPLETE в Issues #299–#303. PR map:
[PR #305](https://github.com/MikeMoore1337/second-brain/pull/305) →
`9444a0936ed0df6ecf3a84fb65eb8bbd6d910e6f`,
[PR #307](https://github.com/MikeMoore1337/second-brain/pull/307) →
`cadc01b41b9c766056d45ef6b1b4d753027a236e`,
[PR #315](https://github.com/MikeMoore1337/second-brain/pull/315) →
`ab61a4f35d9ad8196b82bffef36170a889f8fc2a`,
[PR #316](https://github.com/MikeMoore1337/second-brain/pull/316) →
`7426c079bc95a2fed9f33b7f2b497a1fd5364c40`. Final runtime/deployed SHA:
`7426c079bc95a2fed9f33b7f2b497a1fd5364c40`; post-merge CI `34949012024`,
automatic deploy `34949240222`, `/healthz` HTTP 200 and anonymous private
boundary smoke 401 без раскрытия данных. `env change required: no`.

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
judgement. Нормативная граница зафиксирована в отдельном
[Compare v2 / GrowthCompare v1 contract](compare-v2-contract.md); исходный
design gate не запускал Stage 13A runtime, Web/API/UI или provider work.
Отдельные implementation gates Stage 13A–13D и serial release/closeout gate
Stage 13E завершены указанными Issues/PRs; они не изменяют Compare v1 и не
авторизуют Stage 14 или Stage 15.

## Stage 14 — Personal Experiments

**Статус:** STAGE 14 COMPLETE / PHASES 14.0–14.6 COMPLETE / PRODUCTION
CLOSEOUT COMPLETE. Контракт
[Personal Experiments v1](personal-experiments-v1-contract.md) принят в Issue
[#304](https://github.com/MikeMoore1337/second-brain/issues/304); Phase 14.1
добавил immutable DTO, strict parser, exact chain/source validators и typed scan
projections. Phase 14.2 добавил dedicated reviewed Safe Write с prepare/apply,
exact plan/token binding, no-overwrite, full validation, rollback, lifecycle и
supersession. Phase 14.3 добавил provider-free deterministic evaluator с
immutable result, exact provenance, Stage 12 authority, explicit as_of и
bounded non-causal статусами. Phase 14.4 добавил owner-only memory-only
Web/API/UI surface с PWA/security/accessibility QA; Phase 14.5 добавил
adversarial identity/isolation/lifecycle/Safe Write/privacy/semantic gate; Phase
14.6 зафиксировал final regression, release, production и cleanup evidence.

Serial delivery evidence: PR #318 → `19f6a1184ac77c9547cfa6711ef41ba2ac18991b`,
PR #322 → `229e6d75060eac239216481c48864573ef801d19`, PR #323 →
`108c5a8a77bd52f6a6d2a31a9bc00c7842b2c34b`, PR #325 →
`6be81abcc8a8e550f8ab85d2cd9ffd85fb35de9f`, PR #327 →
`d3cf87dc67f2122492e8e1e75108dec6212fdc68`, PR #328 →
`fae8ce99066f3654fb798f2800cd1209c26fd372`; exact CI/deploy run map is
recorded in the normative contract. The final release PR contains `Closes #304`;
its final main/runtime SHA and production evidence are recorded in the final
delivery report.

Предполагаемый bounded workflow:

hypothesis -> intervention -> explicit measurement -> reviewed observations
-> bounded result -> reassessment.

Stage 14 не будет утверждать causal effect только из временной близости,
не будет автоматически записывать experiment facts, и не будет скрыто
изменять Goal, progress definition или user plan. Any candidate adaptation
remains owner-reviewed and reversible.

## Stage 15 — Adaptive Cognitive Twin

**Статус:** STAGE 15 COMPLETE / PRODUCTION under Issue #331; PHASE 15.0 CONTRACT COMPLETE under Issue #331; PHASE 15.1
EXACT SOURCE/CANDIDATE CORE COMPLETE (PR #333, merged/deployed as
`e233f61feee15b27adc1bc84c43e544eff3fa62f`). PHASE 15.2 OPERATIONAL PROFILE
LIFECYCLE COMPLETE (PR #334, merged/deployed as
`f0d3f8a543c602d28ec3913281adf399c1c9e5b7`); PHASE 15.3 PROJECTION AND
EVALUATION COMPLETE (PR #335, merged/deployed as
`64c995a294cd7f25977b69e38e592d271a19bed2`); PHASE 15.4 PRIVATE WEB/API/UI
COMPLETE (PR #336, merged/deployed as
`7304488355661d99af1d79f3aac676a328597434`); PHASE 15.5
SECURITY/PRIVACY/INTEGRATION/E2E COMPLETE (PR #337, merged/deployed as
69b66fa6008cc4993d8ca1babe5edeb0e5b3247f); PHASE 15.6 FINAL
RELEASE/CLOSEOUT COMPLETE via final PR Closes #331. Depends on Stage 9 calibration, Stage 10
behavioral model, Stage 12 progress and Stage 14 experiments. Нормативная
граница зафиксирована в [adaptive-cognitive-twin-v1-contract.md](adaptive-cognitive-twin-v1-contract.md).

Stage 15 может предлагать versioned, explainable, reversible and measurable
adaptation candidates from reviewed evidence and explicit calibration. It must
remain owner-controlled and must not introduce hidden reinforcement learning,
unbounded personalization, background monitoring, autonomous goal mutation or
provider-driven canonical writes. Реализация 15.1–15.6 выполняется строго
последовательно после merge и post-merge/deploy gate каждой предыдущей фазы;
существующие semantics Stage 1–14 не меняются.

## Stage 16+

Дальнейшие stages намеренно не фиксируются как implementation commitments.
Новые capabilities требуют отдельного issue, contract/design gate, privacy
review, exact dependency map и explicit acceptance decision. Этот roadmap не
авторизует Stage 12E, Stage 13 issue или любой следующий backlog item.

## Non-goals for this roadmap

- изменение schemas/NoteType или second-brain-vault; Stage 12A runtime is
  limited to the read-only parser and validators;
- новый Safe Write, provider/network, Web/API или environment variable/systemd
  contract beyond the completed Stage 12B/12C boundaries and the bounded
  Stage 12D composition;
- Codex Review request, Vault Sync или live smoke;
- inference write-back, telemetry, embeddings, vector DB или hidden score;
- автоматический запуск Stage 12E или любой следующей стадии.
