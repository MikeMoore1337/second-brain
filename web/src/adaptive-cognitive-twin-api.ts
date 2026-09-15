import { ApiRequestError, type FetchLike } from "./api";

export const ADAPTIVE_COGNITIVE_TWIN_REQUEST_HEADER = "adaptive-cognitive-twin-v1" as const;

export type AdaptiveGoal = {
  readonly source_note_uuid: string;
  readonly goal_identity_fingerprint: string;
  readonly goal_text: string;
  readonly domain: string | null;
};

export type AdaptiveExperimentSelector = {
  readonly experiment_definition_id: string;
  readonly experiment_definition_fingerprint: string;
};

export type AdaptiveSourceReadiness = {
  readonly family: string;
  readonly readiness: string;
  readonly source_fingerprint: string | null;
  readonly reference_id: string | null;
  readonly reference_fingerprint: string | null;
  readonly policy_fingerprints: readonly string[];
  readonly as_of: string | null;
};

export type AdaptiveProfileShape = {
  readonly profile_id?: string;
  readonly contract_version: string;
  readonly profile_version: string;
  readonly goal_source_uuid: string;
  readonly goal_identity_fingerprint: string;
  readonly source_snapshot_fingerprint: string;
  readonly projection_focus: string;
  readonly interaction_mode: string;
  readonly evaluation_measure: string;
  readonly profile_policy_id: string;
  readonly profile_policy_fingerprint: string;
  readonly profile_fingerprint?: string;
};

export type AdaptiveProfile = AdaptiveProfileShape & {
  readonly profile_id: string;
  readonly profile_fingerprint: string;
};

export type AdaptiveEvaluationPlan = {
  readonly later_source_required: true;
  readonly explicit_as_of_required: true;
  readonly baseline_source_snapshot_fingerprint: string;
  readonly measures: readonly string[];
  readonly evaluation_policy_id: string;
  readonly evaluation_policy_fingerprint: string;
  readonly non_causal_wording_id: string;
};

export type AdaptiveCandidate = {
  readonly contract_version: string;
  readonly candidate_version: string;
  readonly status: string;
  readonly as_of: string;
  readonly goal_source_uuid: string;
  readonly goal_identity_fingerprint: string;
  readonly source_snapshot_fingerprint: string;
  readonly prior_profile_id: string | null;
  readonly prior_profile_fingerprint: string | null;
  readonly proposed_profile: AdaptiveProfileShape | null;
  readonly reasons: readonly string[];
  readonly evaluation_plan: AdaptiveEvaluationPlan;
  readonly caveats: readonly string[];
  readonly policy_id: string;
  readonly policy_fingerprint: string;
  readonly candidate_fingerprint: string;
};

export type AdaptiveProfileDiff = Readonly<Record<string, unknown>>;

export type AdaptiveProjection = {
  readonly contract_version: string;
  readonly projection_version: string;
  readonly as_of: string;
  readonly goal_source_uuid: string;
  readonly goal_identity_fingerprint: string;
  readonly goal_readiness: string;
  readonly source_snapshot_fingerprint: string;
  readonly source_readiness: readonly AdaptiveSourceReadiness[];
  readonly active_profile: AdaptiveProfile | null;
  readonly lifecycle_state: string;
  readonly profile_validity: string;
  readonly candidate: AdaptiveCandidate;
  readonly profile_diff: AdaptiveProfileDiff | null;
  readonly provenance: Readonly<Record<string, string>>;
};

export type AdaptiveEvaluation = {
  readonly contract_version: string;
  readonly profile_id: string;
  readonly profile_fingerprint: string;
  readonly goal_source_uuid: string;
  readonly goal_identity_fingerprint: string;
  readonly activation_snapshot_fingerprint: string;
  readonly later_snapshot_fingerprint: string;
  readonly as_of: string;
  readonly state: string;
  readonly changed_sources: readonly string[];
  readonly unchanged_sources: readonly string[];
  readonly caveats: readonly string[];
  readonly evaluation_fingerprint: string;
};

export type AdaptiveStateResponse = {
  readonly web_contract: "adaptive_cognitive_twin_web_v1";
  readonly as_of: string;
  readonly goals: readonly AdaptiveGoal[];
  readonly selected_goal: AdaptiveGoal | null;
  readonly stage14_experiment_selectors: readonly AdaptiveExperimentSelector[];
  readonly projection: AdaptiveProjection | null;
  readonly profile_history: readonly AdaptiveProfile[];
  readonly reviewed_candidate_fingerprints: readonly string[];
  readonly rejected_candidate_fingerprints: readonly string[];
  readonly non_causal_phrase: string;
};

export type AdaptiveMutationResponse = {
  readonly web_contract: "adaptive_cognitive_twin_web_v1";
  readonly status: string;
  readonly event: Readonly<Record<string, unknown>> | null;
  readonly projection: AdaptiveProjection;
  readonly evaluation?: AdaptiveEvaluation;
};

export type AdaptiveSelectionRequest = {
  readonly goal_source_uuid: string;
  readonly goal_identity_fingerprint: string;
  readonly as_of: string;
  readonly stage14_experiment_definition_id?: string;
  readonly stage14_experiment_definition_fingerprint?: string;
};

export type AdaptiveStateRequest = Partial<AdaptiveSelectionRequest>;

export type AdaptiveCandidateOperationRequest = AdaptiveSelectionRequest & {
  readonly candidate_fingerprint: string;
  readonly source_snapshot_fingerprint: string;
  readonly operation_id: string;
  readonly confirmed: true;
};

export type AdaptiveSupersedeRequest = AdaptiveCandidateOperationRequest & {
  readonly prior_profile_id: string;
  readonly prior_profile_fingerprint: string;
};

export type AdaptiveEvaluateRequest = AdaptiveSelectionRequest & {
  readonly active_profile_id: string;
  readonly active_profile_fingerprint: string;
  readonly activation_source_snapshot_fingerprint: string;
  readonly operation_id: string;
  readonly confirmed: true;
};

export type AdaptiveRevertRequest = AdaptiveSelectionRequest & {
  readonly target_profile_id: string;
  readonly target_profile_fingerprint: string;
  readonly operation_id: string;
  readonly confirmed: true;
};

const fetchDefault: FetchLike = (input, init) => fetch(input, init);
const CACHE_POLICY: RequestCache = ["no", "store"].join("-") as RequestCache;
const UUID7_PATTERN = /^[0-9a-f]{8}-[0-9a-f]{4}-7[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/u;
const HASH_PATTERN = /^sha256:[0-9a-f]{64}$/u;
const CONTROL_PATTERN = /[\u0000-\u001f\u007f-\u009f]/u;
const BANNED_PRIVATE_KEYS = new Set(["content", "raw", "path", "relative_path", "source_body"]);

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function hasExactKeys(value: Record<string, unknown>, required: readonly string[], optional: readonly string[] = []): boolean {
  const allowed = new Set([...required, ...optional]);
  return required.every((key) => key in value) && Object.keys(value).every((key) => allowed.has(key));
}

function isBoundedText(value: unknown, maxBytes = 16 * 1024): value is string {
  return typeof value === "string"
    && value.length > 0
    && !CONTROL_PATTERN.test(value)
    && value === value.normalize("NFC")
    && new TextEncoder().encode(value).byteLength <= maxBytes;
}

function isHash(value: unknown): value is string {
  return typeof value === "string" && HASH_PATTERN.test(value);
}

function isUuid7(value: unknown): value is string {
  return typeof value === "string" && UUID7_PATTERN.test(value);
}

function isUtcTimestamp(value: unknown): value is string {
  return typeof value === "string"
    && /(?:Z|\+00:00)$/u.test(value)
    && Number.isFinite(Date.parse(value));
}

function isStringArray(value: unknown): value is readonly string[] {
  return Array.isArray(value) && value.every((item) => isBoundedText(item, 1024));
}

function containsPrivateKey(value: unknown, seen = new Set<object>()): boolean {
  if (Array.isArray(value)) return value.some((item) => containsPrivateKey(item, seen));
  if (!isRecord(value)) return false;
  if (seen.has(value)) return true;
  seen.add(value);
  return Object.entries(value).some(([key, nested]) => BANNED_PRIVATE_KEYS.has(key) || containsPrivateKey(nested, seen));
}

function invalidResponse(): ApiRequestError {
  return new ApiRequestError("Ответ адаптивного слоя не прошёл проверку.", 502);
}

function parseGoal(value: unknown): AdaptiveGoal {
  if (!isRecord(value) || !hasExactKeys(value, ["source_note_uuid", "goal_identity_fingerprint", "goal_text", "domain"])
    || !isUuid7(value.source_note_uuid) || !isHash(value.goal_identity_fingerprint)
    || !isBoundedText(value.goal_text) || !(value.domain === null || isBoundedText(value.domain, 256))) {
    throw invalidResponse();
  }
  return value as unknown as AdaptiveGoal;
}

function parseSelector(value: unknown): AdaptiveExperimentSelector {
  if (!isRecord(value) || !hasExactKeys(value, ["experiment_definition_id", "experiment_definition_fingerprint"])
    || !isUuid7(value.experiment_definition_id) || !isHash(value.experiment_definition_fingerprint)) {
    throw invalidResponse();
  }
  return value as unknown as AdaptiveExperimentSelector;
}

function parseReadiness(value: unknown): AdaptiveSourceReadiness {
  if (!isRecord(value) || !hasExactKeys(value, [
    "family", "readiness", "source_fingerprint", "reference_id", "reference_fingerprint",
    "policy_fingerprints", "as_of",
  ]) || !isBoundedText(value.family, 128) || !isBoundedText(value.readiness, 128)
    || !(value.source_fingerprint === null || isHash(value.source_fingerprint))
    || !(value.reference_id === null || isUuid7(value.reference_id))
    || !(value.reference_fingerprint === null || isHash(value.reference_fingerprint))
    || !Array.isArray(value.policy_fingerprints) || !value.policy_fingerprints.every(isHash)
    || !(value.as_of === null || isUtcTimestamp(value.as_of))) {
    throw invalidResponse();
  }
  return value as unknown as AdaptiveSourceReadiness;
}

function parseProfile(value: unknown, active: boolean): AdaptiveProfile | AdaptiveProfileShape {
  const required = [
    "contract_version", "profile_version", "goal_source_uuid", "goal_identity_fingerprint",
    "source_snapshot_fingerprint", "projection_focus", "interaction_mode", "evaluation_measure",
    "profile_policy_id", "profile_policy_fingerprint",
  ];
  const optional = active ? ["profile_id", "profile_fingerprint"] : ["profile_fingerprint"];
  if (!isRecord(value) || !hasExactKeys(value, required, optional)
    || !isBoundedText(value.contract_version, 128) || value.profile_version !== "1"
    || !isUuid7(value.goal_source_uuid) || !isHash(value.goal_identity_fingerprint)
    || !isHash(value.source_snapshot_fingerprint) || !isBoundedText(value.projection_focus, 128)
    || !isBoundedText(value.interaction_mode, 128) || !isBoundedText(value.evaluation_measure, 128)
    || !isBoundedText(value.profile_policy_id, 256) || !isHash(value.profile_policy_fingerprint)
    || ("profile_id" in value && !isUuid7(value.profile_id))
    || ("profile_fingerprint" in value && !isHash(value.profile_fingerprint))) {
    throw invalidResponse();
  }
  if (active && (!isUuid7(value.profile_id) || !isHash(value.profile_fingerprint))) throw invalidResponse();
  return value as unknown as AdaptiveProfile | AdaptiveProfileShape;
}

function parsePlan(value: unknown): AdaptiveEvaluationPlan {
  if (!isRecord(value) || !hasExactKeys(value, [
    "later_source_required", "explicit_as_of_required", "baseline_source_snapshot_fingerprint",
    "measures", "evaluation_policy_id", "evaluation_policy_fingerprint", "non_causal_wording_id",
  ]) || value.later_source_required !== true || value.explicit_as_of_required !== true
    || !isHash(value.baseline_source_snapshot_fingerprint) || !isStringArray(value.measures)
    || !isBoundedText(value.evaluation_policy_id, 256) || !isHash(value.evaluation_policy_fingerprint)
    || !isBoundedText(value.non_causal_wording_id, 256)) {
    throw invalidResponse();
  }
  return value as unknown as AdaptiveEvaluationPlan;
}

function parseCandidate(value: unknown): AdaptiveCandidate {
  if (!isRecord(value) || !hasExactKeys(value, [
    "contract_version", "candidate_version", "status", "as_of", "goal_source_uuid",
    "goal_identity_fingerprint", "source_snapshot_fingerprint", "prior_profile_id",
    "prior_profile_fingerprint", "proposed_profile", "reasons", "evaluation_plan", "caveats",
    "policy_id", "policy_fingerprint", "candidate_fingerprint",
  ]) || !isBoundedText(value.contract_version, 128) || value.candidate_version !== "1"
    || !isBoundedText(value.status, 128) || !isUtcTimestamp(value.as_of) || !isUuid7(value.goal_source_uuid)
    || !isHash(value.goal_identity_fingerprint) || !isHash(value.source_snapshot_fingerprint)
    || !(value.prior_profile_id === null || isUuid7(value.prior_profile_id))
    || !(value.prior_profile_fingerprint === null || isHash(value.prior_profile_fingerprint))
    || !(value.proposed_profile === null || isRecord(value.proposed_profile))
    || !isStringArray(value.reasons) || !isRecord(value.evaluation_plan) || !isStringArray(value.caveats)
    || !isBoundedText(value.policy_id, 256) || !isHash(value.policy_fingerprint)
    || !isHash(value.candidate_fingerprint)) {
    throw invalidResponse();
  }
  const proposedProfile = value.proposed_profile === null ? null : parseProfile(value.proposed_profile, false);
  return {
    ...(value as unknown as AdaptiveCandidate),
    proposed_profile: proposedProfile,
    evaluation_plan: parsePlan(value.evaluation_plan),
  };
}

function parseProjection(value: unknown): AdaptiveProjection {
  if (!isRecord(value) || !hasExactKeys(value, [
    "contract_version", "projection_version", "as_of", "goal_source_uuid", "goal_identity_fingerprint",
    "goal_readiness", "source_snapshot_fingerprint", "source_readiness", "active_profile",
    "lifecycle_state", "profile_validity", "candidate", "profile_diff", "provenance",
  ]) || !isBoundedText(value.contract_version, 128) || value.projection_version !== "1"
    || !isUtcTimestamp(value.as_of) || !isUuid7(value.goal_source_uuid)
    || !isHash(value.goal_identity_fingerprint) || !isBoundedText(value.goal_readiness, 128)
    || !isHash(value.source_snapshot_fingerprint) || !Array.isArray(value.source_readiness)
    || value.source_readiness.length !== 4 || !value.source_readiness.every((item) => {
      try { parseReadiness(item); return true; } catch { return false; }
    }) || !(value.active_profile === null || isRecord(value.active_profile))
    || !isBoundedText(value.lifecycle_state, 128) || !isBoundedText(value.profile_validity, 128)
    || !isRecord(value.candidate) || !(value.profile_diff === null || isRecord(value.profile_diff))
    || !isRecord(value.provenance)) {
    throw invalidResponse();
  }
  const activeProfile = value.active_profile === null ? null : parseProfile(value.active_profile, true);
  const provenance = value.provenance;
  if (!hasExactKeys(provenance, [
    "contract_version", "derivation_version", "source_snapshot_fingerprint", "candidate_policy_id",
    "candidate_policy_fingerprint", "profile_policy_id", "profile_policy_fingerprint",
    "evaluation_policy_id", "evaluation_policy_fingerprint",
  ]) || !Object.values(provenance).every((item) => isBoundedText(item, 256))) throw invalidResponse();
  return {
    ...(value as unknown as AdaptiveProjection),
    source_readiness: value.source_readiness.map(parseReadiness),
    active_profile: activeProfile as AdaptiveProfile | null,
    candidate: parseCandidate(value.candidate),
    provenance: provenance as Readonly<Record<string, string>>,
  };
}

function parseEvaluation(value: unknown): AdaptiveEvaluation {
  if (!isRecord(value) || !hasExactKeys(value, [
    "contract_version", "profile_id", "profile_fingerprint", "goal_source_uuid",
    "goal_identity_fingerprint", "activation_snapshot_fingerprint", "later_snapshot_fingerprint",
    "as_of", "state", "changed_sources", "unchanged_sources", "caveats", "evaluation_fingerprint",
  ]) || !isBoundedText(value.contract_version, 128) || !isUuid7(value.profile_id)
    || !isHash(value.profile_fingerprint) || !isUuid7(value.goal_source_uuid)
    || !isHash(value.goal_identity_fingerprint) || !isHash(value.activation_snapshot_fingerprint)
    || !isHash(value.later_snapshot_fingerprint) || !isUtcTimestamp(value.as_of)
    || !isBoundedText(value.state, 128) || !isStringArray(value.changed_sources)
    || !isStringArray(value.unchanged_sources) || !isStringArray(value.caveats)
    || !isHash(value.evaluation_fingerprint)) throw invalidResponse();
  return value as unknown as AdaptiveEvaluation;
}

function parseState(value: unknown): AdaptiveStateResponse {
  if (!isRecord(value) || !hasExactKeys(value, [
    "web_contract", "as_of", "goals", "selected_goal", "stage14_experiment_selectors",
    "projection", "profile_history", "reviewed_candidate_fingerprints",
    "rejected_candidate_fingerprints", "non_causal_phrase",
  ]) || value.web_contract !== "adaptive_cognitive_twin_web_v1" || !isUtcTimestamp(value.as_of)
    || !Array.isArray(value.goals) || !value.goals.every((item) => { try { parseGoal(item); return true; } catch { return false; } })
    || !(value.selected_goal === null || isRecord(value.selected_goal))
    || !Array.isArray(value.stage14_experiment_selectors)
    || !value.stage14_experiment_selectors.every((item) => { try { parseSelector(item); return true; } catch { return false; } })
    || !(value.projection === null || isRecord(value.projection)) || !Array.isArray(value.profile_history)
    || !value.profile_history.every((item) => { try { parseProfile(item, true); return true; } catch { return false; } })
    || !Array.isArray(value.reviewed_candidate_fingerprints) || !value.reviewed_candidate_fingerprints.every(isHash)
    || !Array.isArray(value.rejected_candidate_fingerprints) || !value.rejected_candidate_fingerprints.every(isHash)
    || !isBoundedText(value.non_causal_phrase, 1024) || containsPrivateKey(value)) throw invalidResponse();
  return {
    ...(value as unknown as AdaptiveStateResponse),
    goals: value.goals.map(parseGoal),
    selected_goal: value.selected_goal === null ? null : parseGoal(value.selected_goal),
    stage14_experiment_selectors: value.stage14_experiment_selectors.map(parseSelector),
    projection: value.projection === null ? null : parseProjection(value.projection),
    profile_history: value.profile_history.map((item) => parseProfile(item, true) as AdaptiveProfile),
    reviewed_candidate_fingerprints: value.reviewed_candidate_fingerprints,
    rejected_candidate_fingerprints: value.rejected_candidate_fingerprints,
  };
}

function parseMutation(value: unknown): AdaptiveMutationResponse {
  if (!isRecord(value) || !hasExactKeys(value, ["web_contract", "status", "event", "projection"], ["evaluation"])
    || value.web_contract !== "adaptive_cognitive_twin_web_v1" || !isBoundedText(value.status, 128)
    || !(value.event === null || isRecord(value.event)) || !isRecord(value.projection)
    || ("evaluation" in value && !isRecord(value.evaluation)) || containsPrivateKey(value)) throw invalidResponse();
  return {
    ...(value as unknown as AdaptiveMutationResponse),
    projection: parseProjection(value.projection),
    evaluation: "evaluation" in value ? parseEvaluation(value.evaluation) : undefined,
  };
}

async function readJson(response: Response): Promise<unknown> {
  try { return await response.json(); } catch { return null; }
}

function responseMessage(payload: unknown, fallback: string): string {
  if (isRecord(payload) && isRecord(payload.error) && isBoundedText(payload.error.message, 1024)
    && /[А-Яа-яЁё]/u.test(payload.error.message)) return payload.error.message;
  return fallback;
}

async function request<T>(
  path: string,
  body: unknown,
  fetcher: FetchLike,
  fallback: string,
  parser: (value: unknown) => T,
  signal?: AbortSignal,
): Promise<T> {
  const response = await fetcher(path, {
    method: "POST",
    headers: {
      Accept: "application/json",
      "Content-Type": "application/json",
      "X-Second-Brain-Request": ADAPTIVE_COGNITIVE_TWIN_REQUEST_HEADER,
    },
    credentials: "same-origin",
    cache: CACHE_POLICY,
    body: JSON.stringify(body),
    signal,
  });
  const payload = await readJson(response);
  if (!response.ok) throw new ApiRequestError(responseMessage(payload, fallback), response.status);
  try { return parser(payload); } catch (error) {
    if (error instanceof ApiRequestError) throw error;
    throw invalidResponse();
  }
}

export function newAdaptiveOperationId(): string {
  const bytes = new Uint8Array(16);
  globalThis.crypto.getRandomValues(bytes);
  let timestamp = Date.now();
  for (let index = 5; index >= 0; index -= 1) {
    bytes[index] = timestamp & 0xff;
    timestamp = Math.floor(timestamp / 256);
  }
  bytes[6] = (bytes[6] & 0x0f) | 0x70;
  bytes[8] = (bytes[8] & 0x3f) | 0x80;
  const hex = [...bytes].map((value) => value.toString(16).padStart(2, "0")).join("");
  return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`;
}

export function loadAdaptiveState(
  body: AdaptiveStateRequest = {},
  fetcher: FetchLike = fetchDefault,
  signal?: AbortSignal,
): Promise<AdaptiveStateResponse> {
  return request("/api/adaptive-cognitive-twin/state", body, fetcher, "Не удалось загрузить адаптивный профиль.", parseState, signal);
}

export function buildAdaptiveCandidate(
  body: AdaptiveSelectionRequest,
  fetcher: FetchLike = fetchDefault,
  signal?: AbortSignal,
): Promise<{ readonly web_contract: "adaptive_cognitive_twin_web_v1"; readonly projection: AdaptiveProjection }> {
  return request("/api/adaptive-cognitive-twin/candidate", body, fetcher, "Не удалось собрать предложение адаптивного профиля.", (value) => {
    if (!isRecord(value) || !hasExactKeys(value, ["web_contract", "projection"])
      || value.web_contract !== "adaptive_cognitive_twin_web_v1" || !isRecord(value.projection) || containsPrivateKey(value)) throw invalidResponse();
    return { web_contract: "adaptive_cognitive_twin_web_v1", projection: parseProjection(value.projection) };
  }, signal);
}

function mutation(
  path: string,
  body: unknown,
  fallback: string,
  fetcher: FetchLike,
  signal?: AbortSignal,
): Promise<AdaptiveMutationResponse> {
  return request(path, body, fetcher, fallback, parseMutation, signal);
}

export function reviewAdaptiveCandidate(body: AdaptiveCandidateOperationRequest, fetcher: FetchLike = fetchDefault, signal?: AbortSignal): Promise<AdaptiveMutationResponse> {
  return mutation("/api/adaptive-cognitive-twin/review", body, "Не удалось отметить предложение проверенным.", fetcher, signal);
}

export function activateAdaptiveCandidate(body: AdaptiveCandidateOperationRequest, fetcher: FetchLike = fetchDefault, signal?: AbortSignal): Promise<AdaptiveMutationResponse> {
  return mutation("/api/adaptive-cognitive-twin/activate", body, "Не удалось активировать адаптивный профиль.", fetcher, signal);
}

export function rejectAdaptiveCandidate(body: AdaptiveCandidateOperationRequest, fetcher: FetchLike = fetchDefault, signal?: AbortSignal): Promise<AdaptiveMutationResponse> {
  return mutation("/api/adaptive-cognitive-twin/reject", body, "Не удалось отклонить предложение адаптивного профиля.", fetcher, signal);
}

export function evaluateAdaptiveProfile(body: AdaptiveEvaluateRequest, fetcher: FetchLike = fetchDefault, signal?: AbortSignal): Promise<AdaptiveMutationResponse> {
  return mutation("/api/adaptive-cognitive-twin/evaluate", body, "Не удалось построить описательное сравнение.", fetcher, signal);
}

export function supersedeAdaptiveProfile(body: AdaptiveSupersedeRequest, fetcher: FetchLike = fetchDefault, signal?: AbortSignal): Promise<AdaptiveMutationResponse> {
  return mutation("/api/adaptive-cognitive-twin/supersede", body, "Не удалось заменить текущий адаптивный профиль.", fetcher, signal);
}

export function revertAdaptiveProfile(body: AdaptiveRevertRequest, fetcher: FetchLike = fetchDefault, signal?: AbortSignal): Promise<AdaptiveMutationResponse> {
  return mutation("/api/adaptive-cognitive-twin/revert", body, "Не удалось вернуть предыдущую версию профиля.", fetcher, signal);
}
