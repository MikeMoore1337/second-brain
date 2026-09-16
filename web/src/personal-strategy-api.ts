import { ApiRequestError, type FetchLike } from "./api";

export type StrategyReadiness =
  | "exact_current"
  | "missing"
  | "stale"
  | "conflict"
  | "not_comparable"
  | "policy_mismatch";

export type StrategyPackReadiness = "exact_current" | "incomplete" | "conflict" | "source_changed";

export interface StrategyGoalIdentity {
  readonly source_note_uuid: string;
  readonly dimension: string;
  readonly source_evidence_kind: string;
  readonly source_self_kind: string;
  readonly domain: string | null;
  readonly evidence_at: string;
  readonly evidence_at_precision: string;
  readonly source_contract_version: string;
  readonly source_derivation_version: string;
  readonly self_model_policy_fingerprint: string;
  readonly source_fingerprint: string;
  readonly claim_fingerprint: string;
}

export interface StrategyGoal {
  readonly goal: StrategyGoalIdentity;
  readonly goal_identity_fingerprint: string;
  readonly goal_text: string;
}

export interface StrategySource {
  readonly alias: string;
  readonly readiness: StrategyReadiness;
  readonly reference_id: string | null;
  readonly reference_fingerprint: string | null;
  readonly summary: string;
  readonly policy_fingerprints: readonly string[];
  readonly as_of: string | null;
}

export interface StrategyContextPack {
  readonly contract_version: string;
  readonly pack_version: string;
  readonly as_of: string;
  readonly goal_source_uuid: string;
  readonly goal_identity_fingerprint: string;
  readonly goal_text: string;
  readonly task: string;
  readonly constraints: readonly string[];
  readonly current_context: string;
  readonly sources: readonly StrategySource[];
  readonly readiness: StrategyPackReadiness;
  readonly pack_caveats: readonly string[];
  readonly policy_id: string;
  readonly policy_fingerprint: string;
  readonly source_pack_fingerprint: string;
}

export interface StrategyProviderPreview {
  readonly source_pack_fingerprint: string;
  readonly canonical_json: string;
  readonly canonical_bytes_sha256: string;
  readonly assistant_envelope: Record<string, unknown>;
}

export interface StrategyCandidate {
  readonly action_id: string;
  readonly kind: string;
  readonly title: string;
  readonly description: string;
  readonly basis_aliases: readonly string[];
  readonly goal_relation: string;
  readonly expected_observable_signal: string;
  readonly prerequisites: readonly string[];
  readonly caveats: readonly string[];
}

export interface StrategyProposal {
  readonly proposal_id: string;
  readonly proposal_version: string;
  readonly result_state: string;
  readonly as_of: string;
  readonly goal_source_uuid: string;
  readonly goal_identity_fingerprint: string;
  readonly source_pack_fingerprint: string;
  readonly policy_id: string;
  readonly policy_fingerprint: string;
  readonly candidates: readonly StrategyCandidate[];
  readonly suggested_order: readonly string[];
  readonly reasons: readonly string[];
  readonly caveats: readonly string[];
  readonly provider_fingerprint: string;
  readonly proposal_fingerprint: string;
}

export interface ReviewedAction {
  readonly action_id: string;
  readonly kind: string;
  readonly generated: StrategyCandidate;
  readonly reviewed: StrategyCandidate;
  readonly edited: boolean;
}

export interface StrategySnapshot {
  readonly snapshot_id: string;
  readonly sequence: number;
  readonly state: "current" | "superseded" | "deactivated";
  readonly goal_source_uuid: string;
  readonly goal_identity_fingerprint: string;
  readonly source_pack_fingerprint: string;
  readonly proposal_fingerprint: string;
  readonly selected_actions: readonly ReviewedAction[];
  readonly policy_id: string;
  readonly policy_fingerprint: string;
  readonly reviewed_at: string;
  readonly accepted_at: string;
  readonly prior_snapshot_id: string | null;
  readonly prior_snapshot_fingerprint: string | null;
  readonly snapshot_fingerprint: string;
}

export interface StrategySnapshotEntry {
  readonly goal_source_uuid: string;
  readonly goal_identity_fingerprint: string;
  readonly snapshot: StrategySnapshot | null;
  readonly freshness: string;
}

export interface PersonalStrategyStateResponse {
  readonly web_contract: string;
  readonly goals: readonly StrategyGoal[];
  readonly eligible_goal_count: number;
  readonly generated_at: string;
  readonly current_snapshots: readonly StrategySnapshotEntry[];
  readonly caveats: readonly string[];
}

export interface PersonalStrategyContextResponse {
  readonly web_contract: string;
  readonly context_pack: StrategyContextPack;
  readonly provider_preview: StrategyProviderPreview;
  readonly accepted_snapshot: StrategySnapshot | null;
  readonly accepted_snapshot_status: "none" | "current" | "stale";
}

export interface PersonalStrategyGenerateResponse extends PersonalStrategyContextResponse {
  readonly web_contract: string;
  readonly proposal: StrategyProposal;
}

export interface PersonalStrategyAcceptResponse {
  readonly web_contract: string;
  readonly status: "accepted";
  readonly snapshot: StrategySnapshot;
}

export interface PersonalStrategyContextRequest {
  readonly goal_source_uuid: string;
  readonly goal_identity_fingerprint: string;
  readonly task: string;
  readonly constraints: readonly string[];
  readonly current_context: string;
}

const PURPOSE = "executive-strategy-v1";
const CACHE_POLICY: RequestCache = ["no", "store"].join("-") as RequestCache;

function errorMessage(payload: unknown, fallback: string): string {
  if (typeof payload === "object" && payload !== null && "error" in payload) {
    const error = payload.error;
    if (typeof error === "object" && error !== null && "message" in error) {
      const message = error.message;
      if (typeof message === "string" && /[А-Яа-яЁё]/u.test(message)) return message;
    }
  }
  return fallback;
}

async function readJson(response: Response): Promise<unknown> {
  try {
    return await response.json();
  } catch {
    return null;
  }
}

async function requestJson<T>(
  path: string,
  body: unknown,
  fetcher: FetchLike,
  fallback: string,
  signal?: AbortSignal,
): Promise<T> {
  const response = await fetcher(path, {
    method: "POST",
    credentials: "same-origin",
    cache: CACHE_POLICY,
    headers: {
      Accept: "application/json",
      "Content-Type": "application/json",
      "X-Second-Brain-Request": PURPOSE,
    },
    body: JSON.stringify(body),
    signal,
  });
  const payload = await readJson(response);
  if (!response.ok) throw new ApiRequestError(errorMessage(payload, fallback), response.status);
  return payload as T;
}

export function loadPersonalStrategyState(
  fetcher: FetchLike = fetch,
  signal?: AbortSignal,
): Promise<PersonalStrategyStateResponse> {
  return requestJson(
    "/api/personal-strategy/state",
    {},
    fetcher,
    "Не удалось загрузить состояние личной стратегии.",
    signal,
  );
}

export function buildPersonalStrategyContext(
  request: PersonalStrategyContextRequest,
  fetcher: FetchLike = fetch,
  signal?: AbortSignal,
): Promise<PersonalStrategyContextResponse> {
  return requestJson(
    "/api/personal-strategy/context",
    request,
    fetcher,
    "Не удалось собрать текущий контекст личной стратегии.",
    signal,
  );
}

export function generatePersonalStrategy(
  contextPack: StrategyContextPack,
  providerPreview: StrategyProviderPreview,
  fetcher: FetchLike = fetch,
  signal?: AbortSignal,
): Promise<PersonalStrategyGenerateResponse> {
  return requestJson(
    "/api/personal-strategy/generate",
    { context_pack: contextPack, provider_preview: providerPreview.canonical_json },
    fetcher,
    "Не удалось построить личную стратегию.",
    signal,
  );
}

export function rejectPersonalStrategy(
  proposal: StrategyProposal,
  operationId: string,
  fetcher: FetchLike = fetch,
  signal?: AbortSignal,
): Promise<{ readonly status: "rejected"; readonly proposal_fingerprint: string }> {
  return requestJson(
    "/api/personal-strategy/reject",
    { proposal, operation_id: operationId, reason: "owner_rejected" },
    fetcher,
    "Не удалось отклонить личную стратегию.",
    signal,
  );
}

export function acceptPersonalStrategy(
  contextPack: StrategyContextPack,
  proposal: StrategyProposal,
  selectedActions: readonly ReviewedAction[],
  operationId: string,
  acceptedSnapshot: StrategySnapshot | null,
  fetcher: FetchLike = fetch,
  signal?: AbortSignal,
): Promise<PersonalStrategyAcceptResponse> {
  return requestJson(
    "/api/personal-strategy/accept",
    {
      context_pack: contextPack,
      proposal,
      selected_actions: selectedActions,
      operation_id: operationId,
      expected_prior_snapshot_id: acceptedSnapshot?.snapshot_id ?? null,
      expected_prior_snapshot_fingerprint: acceptedSnapshot?.snapshot_fingerprint ?? null,
    },
    fetcher,
    "Не удалось принять личную стратегию.",
    signal,
  );
}
