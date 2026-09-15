import { ApiRequestError, type FetchLike } from "./api";

export const PERSONAL_EXPERIMENT_REQUEST_HEADER = "personal-experiment-v1" as const;

export type PersonalExperimentBaselineStrategy =
  | "stage12_definition_explicit"
  | "reviewed_pre_activation_observation";
export type PersonalExperimentLifecycleEvent = "activation" | "completion" | "cancellation";
export type PersonalExperimentDisposition =
  | "continue"
  | "stop"
  | "repeat_with_new_definition"
  | "hold"
  | "not_decided";

export interface PersonalExperimentGoal {
  readonly source_note_uuid: string;
  readonly goal_text: string;
  readonly domain: string | null;
  readonly goal_identity_fingerprint: string;
}

export interface PersonalExperimentStage12Definition {
  readonly id: string;
  readonly goal_source_uuid: string;
  readonly goal_identity_fingerprint: string;
  readonly definition_fingerprint: string;
  readonly goal_progress_policy_fingerprint: string;
  readonly definition_reviewed_at: string;
  readonly progress_model: "numeric_target" | "milestone_set";
  readonly metric_id?: string;
  readonly unit?: string;
  readonly baseline?: string;
  readonly target?: string;
  readonly direction?: string;
  readonly lower_bound?: string;
  readonly upper_bound?: string;
  readonly ordering?: string;
  readonly milestones?: readonly { readonly id: string; readonly label: string; readonly ordinal: number }[];
  readonly active: boolean;
}

export interface PersonalExperimentStage12Observation {
  readonly id: string;
  readonly goal_source_uuid: string;
  readonly goal_identity_fingerprint: string;
  readonly progress_definition_id: string;
  readonly definition_fingerprint: string;
  readonly observation_fingerprint: string;
  readonly observed_at: string;
  readonly observed_at_precision: "exact" | "unknown";
  readonly observation_reviewed_at: string;
  readonly progress_model: "numeric_target" | "milestone_set";
  readonly value?: string;
  readonly unit?: string;
  readonly metric_id?: string;
  readonly milestone_id?: string;
  readonly state?: string;
  readonly active: boolean;
  readonly eligible?: boolean;
}

export interface PersonalExperimentDefinition {
  readonly id: string;
  readonly personal_experiment_kind: "definition";
  readonly goal_source_uuid: string;
  readonly goal_identity_fingerprint: string;
  readonly goal_progress_definition_id: string;
  readonly goal_progress_definition_fingerprint: string;
  readonly goal_progress_policy_fingerprint: string;
  readonly hypothesis: string;
  readonly intervention: string;
  readonly baseline_strategy: PersonalExperimentBaselineStrategy;
  readonly baseline_observation_uuid?: string;
  readonly baseline_observation_fingerprint?: string;
  readonly definition_reviewed_at: string;
  readonly experiment_policy_id: string;
  readonly experiment_policy_fingerprint: string;
  readonly experiment_definition_fingerprint: string;
  readonly definition_fingerprint: string;
  readonly state: "active" | "superseded" | "invalid" | string;
  readonly supersedes_definition_id?: string;
  readonly supersedes_definition_fingerprint?: string;
}

export interface PersonalExperimentLifecycleRecord {
  readonly id: string;
  readonly experiment_definition_id: string;
  readonly experiment_definition_fingerprint: string;
  readonly lifecycle_event: PersonalExperimentLifecycleEvent;
  readonly event_at: string;
  readonly lifecycle_reviewed_at: string;
  readonly lifecycle_fingerprint: string;
  readonly [key: string]: unknown;
}

export interface PersonalExperimentLifecycleProjection {
  readonly state: "planned" | "active" | "completed" | "cancelled" | "invalid" | string;
  readonly activation: PersonalExperimentLifecycleRecord | null;
  readonly terminal: PersonalExperimentLifecycleRecord | null;
  readonly issues: readonly string[];
}

export interface PersonalExperimentEnrollment {
  readonly id: string;
  readonly experiment_definition_id: string;
  readonly experiment_definition_fingerprint: string;
  readonly stage12_observation_id: string;
  readonly stage12_observation_fingerprint: string;
  readonly observed_at?: string;
  readonly observation_reviewed_at?: string;
  readonly observation_fingerprint: string;
  readonly [key: string]: unknown;
}

export interface PersonalExperimentReassessment {
  readonly id: string;
  readonly experiment_definition_id: string;
  readonly experiment_definition_fingerprint: string;
  readonly result_fingerprint: string;
  readonly evaluation_as_of: string;
  readonly evaluation_policy_fingerprint: string;
  readonly disposition: PersonalExperimentDisposition;
  readonly rationale: string;
  readonly reassessment_reviewed_at: string;
  readonly reassessment_fingerprint: string;
  readonly [key: string]: unknown;
}

export interface PersonalExperimentItem {
  readonly definition: PersonalExperimentDefinition;
  readonly goal: PersonalExperimentGoal | null;
  readonly lifecycle: PersonalExperimentLifecycleProjection;
  readonly enrollments: readonly PersonalExperimentEnrollment[];
  readonly reassessments: readonly PersonalExperimentReassessment[];
  readonly eligible_stage12_observations: readonly PersonalExperimentStage12Observation[];
}

export interface PersonalExperimentState {
  readonly web_contract: "personal_experiments_web_v1";
  readonly contract_id: "personal-experiments-v1";
  readonly derivation_id: string;
  readonly policy_id: string;
  readonly policy_fingerprint: string;
  readonly generated_at: string;
  readonly goals: readonly PersonalExperimentGoal[];
  readonly stage12_definitions: readonly PersonalExperimentStage12Definition[];
  readonly stage12_observations: readonly PersonalExperimentStage12Observation[];
  readonly experiments: readonly PersonalExperimentItem[];
  readonly caveats: readonly string[];
}

export interface PersonalExperimentReviewResponse {
  readonly web_contract: "personal_experiment_review_v1";
  readonly status: "dry-run";
  readonly review_token: string;
  readonly plan_sha256: string;
  readonly record_kind: "definition" | "lifecycle" | "observation" | "reassessment";
  readonly record_id: string;
  readonly created: string;
  readonly title: string;
  readonly payload: Readonly<Record<string, unknown>>;
  readonly record_fingerprint: string;
  readonly content_sha256: string;
  readonly bindings: {
    readonly goal_source_uuid: string;
    readonly goal_identity_fingerprint: string;
    readonly experiment_definition_id: string;
    readonly experiment_definition_fingerprint: string;
    readonly supersedes_record_id: string | null;
  };
}

export interface PersonalExperimentApplyResponse {
  readonly web_contract: "personal_experiment_apply_v1";
  readonly status: "saved" | "rolled-back";
  readonly write_status: "created" | "rolled-back";
  readonly record_kind: "definition" | "lifecycle" | "observation" | "reassessment";
  readonly record_id: string;
  readonly plan_sha256: string;
  readonly rollback: "succeeded" | "failed" | "not-needed";
}

export interface PersonalExperimentEvaluationResult {
  readonly web_contract: "personal_experiment_evaluation_web_v1";
  readonly contract: "personal_experiment_result_v1";
  readonly contract_id: "personal-experiments-v1";
  readonly derivation_id: string;
  readonly experiment_definition_id: string;
  readonly experiment_definition_fingerprint: string;
  readonly status: string;
  readonly as_of: string;
  readonly result_fingerprint: string;
  readonly caveats: readonly string[];
  readonly provenance: {
    readonly source: "current_vault";
    readonly provider: "none";
    readonly network: "none";
    readonly write: "none";
    readonly as_of: string;
    readonly [key: string]: unknown;
  };
  readonly [key: string]: unknown;
}

export interface PersonalExperimentDefinitionPrepareRequest {
  readonly goal_source_uuid: string;
  readonly goal_identity_fingerprint: string;
  readonly goal_progress_definition_id: string;
  readonly goal_progress_definition_fingerprint: string;
  readonly hypothesis: string;
  readonly intervention: string;
  readonly baseline_strategy: PersonalExperimentBaselineStrategy;
  readonly baseline_observation_uuid: string | null;
  readonly baseline_observation_fingerprint: string | null;
  readonly supersedes_definition_id: string | null;
  readonly supersedes_definition_fingerprint: string | null;
}

export interface PersonalExperimentLifecyclePrepareRequest {
  readonly experiment_definition_id: string;
  readonly experiment_definition_fingerprint: string;
  readonly lifecycle_event: PersonalExperimentLifecycleEvent;
  readonly event_at: string;
  readonly supersedes_lifecycle_id: string | null;
  readonly supersedes_lifecycle_fingerprint: string | null;
}

export interface PersonalExperimentObservationPrepareRequest {
  readonly experiment_definition_id: string;
  readonly experiment_definition_fingerprint: string;
  readonly stage12_observation_id: string;
  readonly stage12_observation_fingerprint: string;
  readonly supersedes_observation_id: string | null;
  readonly supersedes_observation_fingerprint: string | null;
}

export interface PersonalExperimentReassessmentPrepareRequest {
  readonly experiment_definition_id: string;
  readonly experiment_definition_fingerprint: string;
  readonly result_fingerprint: string;
  readonly evaluation_as_of: string;
  readonly evaluation_policy_fingerprint: string;
  readonly disposition: PersonalExperimentDisposition;
  readonly rationale: string;
  readonly supersedes_reassessment_id: string | null;
  readonly supersedes_reassessment_fingerprint: string | null;
}

const fetchDefault: FetchLike = (input, init) => fetch(input, init);

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function isString(value: unknown): value is string {
  return typeof value === "string" && value.length > 0;
}

function isStringOrNull(value: unknown): value is string | null {
  return value === null || isString(value);
}

function isOptionalStringOrNull(value: unknown): value is string | null | undefined {
  return value === undefined || isStringOrNull(value);
}

function isArrayOfStrings(value: unknown): value is readonly string[] {
  return Array.isArray(value) && value.every(isString);
}

function invalidResponse(): ApiRequestError {
  return new ApiRequestError("Ответ личных экспериментов не прошёл проверку.", 502);
}

function parseGoal(value: unknown): PersonalExperimentGoal {
  if (!isRecord(value) || !isString(value.source_note_uuid) || !isString(value.goal_text)
    || !(value.domain === null || isString(value.domain)) || !isString(value.goal_identity_fingerprint)) {
    throw invalidResponse();
  }
  return value as unknown as PersonalExperimentGoal;
}

function parseStage12Definition(value: unknown): PersonalExperimentStage12Definition {
  if (!isRecord(value) || !isString(value.id) || !isString(value.goal_source_uuid)
    || !isString(value.goal_identity_fingerprint) || !isString(value.definition_fingerprint)
    || !isString(value.goal_progress_policy_fingerprint) || !isString(value.definition_reviewed_at)
    || (value.progress_model !== "numeric_target" && value.progress_model !== "milestone_set")
    || typeof value.active !== "boolean") {
    throw invalidResponse();
  }
  return value as unknown as PersonalExperimentStage12Definition;
}

function parseStage12Observation(value: unknown): PersonalExperimentStage12Observation {
  if (!isRecord(value) || !isString(value.id) || !isString(value.goal_source_uuid)
    || !isString(value.goal_identity_fingerprint) || !isString(value.progress_definition_id)
    || !isString(value.definition_fingerprint) || !isString(value.observation_fingerprint)
    || !isString(value.observed_at) || (value.observed_at_precision !== "exact" && value.observed_at_precision !== "unknown")
    || !isString(value.observation_reviewed_at) || (value.progress_model !== "numeric_target" && value.progress_model !== "milestone_set")
    || typeof value.active !== "boolean") {
    throw invalidResponse();
  }
  return value as unknown as PersonalExperimentStage12Observation;
}

function parseLifecycleRecord(value: unknown): PersonalExperimentLifecycleRecord {
  if (!isRecord(value) || !isString(value.id) || !isString(value.experiment_definition_id)
    || !isString(value.experiment_definition_fingerprint)
    || !["activation", "completion", "cancellation"].includes(String(value.lifecycle_event))
    || !isString(value.event_at) || !isString(value.lifecycle_reviewed_at)
    || !isString(value.lifecycle_fingerprint)) {
    throw invalidResponse();
  }
  return value as PersonalExperimentLifecycleRecord;
}

function parseLifecycle(value: unknown): PersonalExperimentLifecycleProjection {
  if (!isRecord(value) || !isString(value.state) || !isArrayOfStrings(value.issues)) {
    throw invalidResponse();
  }
  const activation = value.activation === null ? null : parseLifecycleRecord(value.activation);
  const terminal = value.terminal === null ? null : parseLifecycleRecord(value.terminal);
  return { state: value.state, activation, terminal, issues: value.issues };
}

function parseDefinition(value: unknown): PersonalExperimentDefinition {
  if (!isRecord(value) || !isString(value.id) || value.personal_experiment_kind !== "definition"
    || !isString(value.goal_source_uuid) || !isString(value.goal_identity_fingerprint)
    || !isString(value.goal_progress_definition_id) || !isString(value.goal_progress_definition_fingerprint)
    || !isString(value.goal_progress_policy_fingerprint) || !isString(value.hypothesis)
    || !isString(value.intervention) || !["stage12_definition_explicit", "reviewed_pre_activation_observation"].includes(String(value.baseline_strategy))
    || !isString(value.definition_reviewed_at) || !isString(value.experiment_policy_id)
    || !isString(value.experiment_policy_fingerprint) || !isString(value.experiment_definition_fingerprint)
    || !isString(value.definition_fingerprint) || !isString(value.state)) {
    throw invalidResponse();
  }
  if (!isOptionalStringOrNull(value.baseline_observation_uuid) || !isOptionalStringOrNull(value.baseline_observation_fingerprint)
    || !isOptionalStringOrNull(value.supersedes_definition_id) || !isOptionalStringOrNull(value.supersedes_definition_fingerprint)) {
    throw invalidResponse();
  }
  return value as unknown as PersonalExperimentDefinition;
}

function parseEnrollment(value: unknown): PersonalExperimentEnrollment {
  if (!isRecord(value) || !isString(value.id) || !isString(value.experiment_definition_id)
    || !isString(value.experiment_definition_fingerprint) || !isString(value.stage12_observation_id)
    || !isString(value.stage12_observation_fingerprint)
    || !isOptionalStringOrNull(value.observed_at) || !isOptionalStringOrNull(value.observation_reviewed_at)
    || !isString(value.observation_fingerprint)) {
    throw invalidResponse();
  }
  return value as PersonalExperimentEnrollment;
}

function parseReassessment(value: unknown): PersonalExperimentReassessment {
  if (!isRecord(value) || !isString(value.id) || !isString(value.experiment_definition_id)
    || !isString(value.experiment_definition_fingerprint) || !isString(value.result_fingerprint)
    || !isString(value.evaluation_as_of) || !isString(value.evaluation_policy_fingerprint)
    || !["continue", "stop", "repeat_with_new_definition", "hold", "not_decided"].includes(String(value.disposition))
    || !isString(value.rationale) || !isString(value.reassessment_reviewed_at)
    || !isString(value.reassessment_fingerprint)) {
    throw invalidResponse();
  }
  return value as PersonalExperimentReassessment;
}

function parseExperiment(value: unknown): PersonalExperimentItem {
  if (!isRecord(value) || !isRecord(value.definition) || !isRecord(value.lifecycle) || !isArrayOfStrings(value.lifecycle.issues)) {
    throw invalidResponse();
  }
  if (!Array.isArray(value.enrollments) || !Array.isArray(value.reassessments)
    || !Array.isArray(value.eligible_stage12_observations)) {
    throw invalidResponse();
  }
  const goal = value.goal === null ? null : parseGoal(value.goal);
  return {
    definition: parseDefinition(value.definition),
    goal,
    lifecycle: parseLifecycle(value.lifecycle),
    enrollments: value.enrollments.map(parseEnrollment),
    reassessments: value.reassessments.map(parseReassessment),
    eligible_stage12_observations: value.eligible_stage12_observations.map(parseStage12Observation),
  };
}

function parseState(value: unknown): PersonalExperimentState {
  if (!isRecord(value) || value.web_contract !== "personal_experiments_web_v1"
    || value.contract_id !== "personal-experiments-v1" || !isString(value.derivation_id)
    || !isString(value.policy_id) || !isString(value.policy_fingerprint)
    || !isString(value.generated_at) || !Array.isArray(value.goals)
    || !Array.isArray(value.stage12_definitions) || !Array.isArray(value.stage12_observations)
    || !Array.isArray(value.experiments) || !isArrayOfStrings(value.caveats)) {
    throw invalidResponse();
  }
  return {
    web_contract: "personal_experiments_web_v1",
    contract_id: "personal-experiments-v1",
    derivation_id: value.derivation_id,
    policy_id: value.policy_id,
    policy_fingerprint: value.policy_fingerprint,
    generated_at: value.generated_at,
    goals: value.goals.map(parseGoal),
    stage12_definitions: value.stage12_definitions.map(parseStage12Definition),
    stage12_observations: value.stage12_observations.map(parseStage12Observation),
    experiments: value.experiments.map(parseExperiment),
    caveats: value.caveats,
  };
}

function parseReview(value: unknown): PersonalExperimentReviewResponse {
  if (!isRecord(value) || value.web_contract !== "personal_experiment_review_v1"
    || value.status !== "dry-run" || !isString(value.review_token) || !isString(value.plan_sha256)
    || !["definition", "lifecycle", "observation", "reassessment"].includes(String(value.record_kind))
    || !isString(value.record_id) || !isString(value.created) || !isString(value.title)
    || !isRecord(value.payload) || !isString(value.record_fingerprint) || !isString(value.content_sha256)
    || !isRecord(value.bindings)) {
    throw invalidResponse();
  }
  if ("content" in value.payload || "relative_path" in value.payload) throw invalidResponse();
  const bindings = value.bindings;
  if (!isString(bindings.goal_source_uuid) || !isString(bindings.goal_identity_fingerprint)
    || !isString(bindings.experiment_definition_id) || !isString(bindings.experiment_definition_fingerprint)
    || !isStringOrNull(bindings.supersedes_record_id)) {
    throw invalidResponse();
  }
  return { ...value, bindings } as PersonalExperimentReviewResponse;
}

function parseApply(value: unknown): PersonalExperimentApplyResponse {
  if (!isRecord(value) || value.web_contract !== "personal_experiment_apply_v1"
    || (value.status !== "saved" && value.status !== "rolled-back")
    || (value.write_status !== "created" && value.write_status !== "rolled-back")
    || !["definition", "lifecycle", "observation", "reassessment"].includes(String(value.record_kind))
    || !isString(value.record_id) || !isString(value.plan_sha256)
    || !["succeeded", "failed", "not-needed"].includes(String(value.rollback))) {
    throw invalidResponse();
  }
  return value as unknown as PersonalExperimentApplyResponse;
}

function parseEvaluation(value: unknown): PersonalExperimentEvaluationResult {
  if (!isRecord(value) || value.web_contract !== "personal_experiment_evaluation_web_v1"
    || value.contract !== "personal_experiment_result_v1" || value.contract_id !== "personal-experiments-v1"
    || !isString(value.derivation_id) || !isString(value.experiment_definition_id)
    || !isString(value.experiment_definition_fingerprint) || !isString(value.status)
    || !isString(value.as_of) || !isString(value.result_fingerprint) || !isArrayOfStrings(value.caveats)
    || !isRecord(value.provenance)) {
    throw invalidResponse();
  }
  const provenance = value.provenance;
  if (provenance.source !== "current_vault" || provenance.provider !== "none"
    || provenance.network !== "none" || provenance.write !== "none" || !isString(provenance.as_of)) {
    throw invalidResponse();
  }
  return value as PersonalExperimentEvaluationResult;
}

async function readJson(response: Response): Promise<unknown> {
  try {
    return await response.json();
  } catch {
    return null;
  }
}

async function request<T>(
  path: string,
  body: unknown,
  fetcher: FetchLike,
  fallback: string,
  parser: (value: unknown) => T,
  signal?: AbortSignal,
): Promise<T> {
  const init: RequestInit = {
    method: "POST",
    headers: {
      Accept: "application/json",
      "Content-Type": "application/json",
      "X-Second-Brain-Request": PERSONAL_EXPERIMENT_REQUEST_HEADER,
    },
    body: JSON.stringify(body),
  };
  if (signal) init.signal = signal;
  const response = await fetcher(path, init);
  const payload = await readJson(response);
  if (!response.ok) {
    throw new ApiRequestError(fallback, response.status);
  }
  try {
    return parser(payload);
  } catch (error) {
    if (error instanceof ApiRequestError) throw error;
    throw invalidResponse();
  }
}

export function loadPersonalExperiments(
  fetcher: FetchLike = fetchDefault,
  signal?: AbortSignal,
): Promise<PersonalExperimentState> {
  return request(
    "/api/personal-experiments",
    {},
    fetcher,
    "Не удалось загрузить личные эксперименты.",
    parseState,
    signal,
  );
}

export function preparePersonalExperimentDefinition(
  body: PersonalExperimentDefinitionPrepareRequest,
  fetcher: FetchLike = fetchDefault,
  signal?: AbortSignal,
): Promise<PersonalExperimentReviewResponse> {
  return request(
    "/api/personal-experiments/definitions/prepare",
    body,
    fetcher,
    "Не удалось подготовить определение эксперимента.",
    parseReview,
    signal,
  );
}

export function preparePersonalExperimentLifecycle(
  body: PersonalExperimentLifecyclePrepareRequest,
  fetcher: FetchLike = fetchDefault,
  signal?: AbortSignal,
): Promise<PersonalExperimentReviewResponse> {
  return request(
    "/api/personal-experiments/lifecycle/prepare",
    body,
    fetcher,
    "Не удалось подготовить изменение состояния эксперимента.",
    parseReview,
    signal,
  );
}

export function preparePersonalExperimentObservation(
  body: PersonalExperimentObservationPrepareRequest,
  fetcher: FetchLike = fetchDefault,
  signal?: AbortSignal,
): Promise<PersonalExperimentReviewResponse> {
  return request(
    "/api/personal-experiments/observations/prepare",
    body,
    fetcher,
    "Не удалось подготовить включение наблюдения.",
    parseReview,
    signal,
  );
}

export function preparePersonalExperimentReassessment(
  body: PersonalExperimentReassessmentPrepareRequest,
  fetcher: FetchLike = fetchDefault,
  signal?: AbortSignal,
): Promise<PersonalExperimentReviewResponse> {
  return request(
    "/api/personal-experiments/reassessments/prepare",
    body,
    fetcher,
    "Не удалось подготовить пересмотр результата.",
    parseReview,
    signal,
  );
}

export function applyPersonalExperimentReview(
  reviewToken: string,
  planSha256: string,
  confirmed: boolean,
  recordKind: PersonalExperimentReviewResponse["record_kind"],
  fetcher: FetchLike = fetchDefault,
  signal?: AbortSignal,
): Promise<PersonalExperimentApplyResponse> {
  const paths: Record<PersonalExperimentReviewResponse["record_kind"], string> = {
    definition: "/api/personal-experiments/definitions/apply",
    lifecycle: "/api/personal-experiments/lifecycle/apply",
    observation: "/api/personal-experiments/observations/apply",
    reassessment: "/api/personal-experiments/reassessments/apply",
  };
  return request(
    paths[recordKind],
    { review_token: reviewToken, accepted_plan_sha256: planSha256, confirmed },
    fetcher,
    "Не удалось применить проверенную запись.",
    parseApply,
    signal,
  );
}

export function evaluatePersonalExperiment(
  experimentDefinitionId: string,
  experimentDefinitionFingerprint: string,
  asOf: string,
  fetcher: FetchLike = fetchDefault,
  signal?: AbortSignal,
): Promise<PersonalExperimentEvaluationResult> {
  return request(
    "/api/personal-experiments/evaluate",
    {
      experiment_definition_id: experimentDefinitionId,
      experiment_definition_fingerprint: experimentDefinitionFingerprint,
      as_of: asOf,
    },
    fetcher,
    "Не удалось построить описательный результат эксперимента.",
    parseEvaluation,
    signal,
  );
}
