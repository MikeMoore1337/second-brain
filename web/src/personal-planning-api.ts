import { ApiRequestError, type FetchLike } from "./api";

export interface PlanningStrategyAction {
  readonly action_id: string;
  readonly kind: string;
  readonly generated: Record<string, unknown>;
  readonly reviewed: Record<string, unknown>;
  readonly edited: boolean;
}

export interface PlanningStrategySnapshotProjection {
  readonly snapshot_id: string;
  readonly snapshot_fingerprint: string;
  readonly sequence: number;
  readonly selected_actions: readonly PlanningStrategyAction[];
}

export interface PlanningGoalProjection {
  readonly goal_source_uuid: string;
  readonly goal_identity_fingerprint: string;
  readonly goal_text: string;
  readonly goal: Record<string, unknown>;
  readonly strategy_snapshot: PlanningStrategySnapshotProjection | null;
}

export interface PlanningStateResponse {
  readonly web_contract: string;
  readonly goals: readonly PlanningGoalProjection[];
  readonly eligible_goal_count: number;
  readonly generated_at: string;
  readonly current_plan: PlanningPlan | null;
  readonly caveats: readonly string[];
}

export interface PlanningCapacityEntry {
  readonly date: string;
  readonly available_minutes: number;
}

export interface PlanningWindow {
  readonly window_id: string;
  readonly kind: string;
  readonly title: string;
  readonly start_local: string;
  readonly end_local: string;
}

export interface PlanningActionBinding extends Record<string, unknown> {
  readonly reviewed_action_id: string;
}

export interface PlanningGoalRef extends Record<string, unknown> {
  readonly goal_source_uuid: string;
}

export interface PlanningItem extends Record<string, unknown> {
  readonly item_id: string;
  readonly kind: "project" | "milestone" | "commitment" | "next_action" | "hold" | string;
  readonly title: string;
  readonly description: string;
  readonly goal_refs: readonly PlanningGoalRef[];
  readonly action_refs: readonly PlanningActionBinding[];
  readonly parent_item_id: string | null;
  readonly target_start_local: string | null;
  readonly target_end_local: string | null;
  readonly effort_minutes: number;
  readonly effort_source: string;
  readonly dependency_ids: readonly string[];
}

export interface PlanningContextPack extends Record<string, unknown> {
  readonly portfolio_order: readonly string[];
  readonly start_local: string;
  readonly end_local: string;
  readonly timezone: string;
  readonly capacity: readonly PlanningCapacityEntry[];
  readonly fixed_windows: readonly PlanningWindow[];
  readonly planning_constraints: readonly string[];
  readonly planning_context: string;
  readonly readiness: string;
  readonly pack_caveats: readonly string[];
  readonly pack_fingerprint: string;
}

export interface PlanningProviderPreview {
  readonly source_pack_fingerprint: string;
  readonly canonical_json: string;
  readonly canonical_bytes_sha256: string;
  readonly assistant_envelope: Record<string, unknown>;
}

export interface PlanningProposal extends Record<string, unknown> {
  readonly proposal_id: string;
  readonly result_state: string;
  readonly as_of: string;
  readonly source_pack_fingerprint: string;
  readonly provider_envelope_fingerprint: string;
  readonly provider_result_fingerprint: string;
  readonly policy_id: string;
  readonly policy_fingerprint: string;
  readonly items: readonly PlanningItem[];
  readonly suggested_order: readonly string[];
  readonly reasons: readonly string[];
  readonly caveats: readonly string[];
  readonly proposal_fingerprint: string;
}

export interface PlanningPlan extends Record<string, unknown> {
  readonly plan_version: string;
  readonly plan_id: string;
  readonly revision: number;
  readonly as_of: string;
  readonly source_pack_fingerprint: string;
  readonly provider_envelope_fingerprint: string;
  readonly provider_result_fingerprint: string;
  readonly proposal_fingerprint: string;
  readonly start_local: string;
  readonly end_local: string;
  readonly timezone: string;
  readonly capacity: readonly PlanningCapacityEntry[];
  readonly fixed_windows: readonly PlanningWindow[];
  readonly items: readonly PlanningItem[];
  readonly selected_item_ids: readonly string[];
  readonly item_order: readonly string[];
  readonly plan_fingerprint: string;
}

export interface PlanningContextResponse {
  readonly web_contract: string;
  readonly context_pack: PlanningContextPack;
  readonly provider_preview: PlanningProviderPreview;
  readonly current_plan: PlanningPlan | null;
}

export interface PlanningGenerateResponse extends PlanningContextResponse {
  readonly web_contract: string;
  readonly proposal: PlanningProposal;
}

export interface PlanningMutationResponse {
  readonly web_contract: string;
  readonly status: "accepted" | "edited";
  readonly plan: PlanningPlan;
}

export interface PlanningContextRequest {
  readonly goal_source_uuids: readonly string[];
  readonly start_local: string;
  readonly end_local: string;
  readonly timezone: string;
  readonly capacity: readonly PlanningCapacityEntry[];
  readonly fixed_windows: readonly PlanningWindow[];
  readonly planning_constraints: readonly string[];
  readonly planning_context: string;
}

const PURPOSE = "personal-planning-v1";
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

export function loadPersonalPlanningState(
  fetcher: FetchLike = fetch,
  signal?: AbortSignal,
): Promise<PlanningStateResponse> {
  return requestJson(
    "/api/personal-planning/state",
    {},
    fetcher,
    "Не удалось загрузить состояние личного планирования.",
    signal,
  );
}

export function buildPersonalPlanningContext(
  request: PlanningContextRequest,
  fetcher: FetchLike = fetch,
  signal?: AbortSignal,
): Promise<PlanningContextResponse> {
  return requestJson(
    "/api/personal-planning/context",
    request,
    fetcher,
    "Не удалось собрать контекст личного плана.",
    signal,
  );
}

export function generatePersonalPlanning(
  contextPack: PlanningContextPack,
  providerPreview: PlanningProviderPreview,
  fetcher: FetchLike = fetch,
  signal?: AbortSignal,
): Promise<PlanningGenerateResponse> {
  return requestJson(
    "/api/personal-planning/generate",
    { context_pack: contextPack, provider_preview: providerPreview.canonical_json },
    fetcher,
    "Не удалось построить предложение личного плана.",
    signal,
  );
}

export function acceptPersonalPlanning(
  contextPack: PlanningContextPack,
  proposal: PlanningProposal,
  selectedItemIds: readonly string[],
  itemOrder: readonly string[],
  operationId: string,
  expectedCurrentPlanFingerprint: string | null,
  acceptedAt: string,
  fetcher: FetchLike = fetch,
  signal?: AbortSignal,
): Promise<PlanningMutationResponse> {
  return requestJson(
    "/api/personal-planning/accept",
    {
      context_pack: contextPack,
      proposal,
      selected_item_ids: selectedItemIds,
      item_order: itemOrder,
      operation_id: operationId,
      accepted_at: acceptedAt,
      expected_current_plan_fingerprint: expectedCurrentPlanFingerprint,
    },
    fetcher,
    "Не удалось принять личный план.",
    signal,
  );
}

export function editPersonalPlanning(
  items: readonly PlanningItem[],
  selectedItemIds: readonly string[],
  itemOrder: readonly string[],
  operationId: string,
  expectedCurrentPlanFingerprint: string,
  editedAt: string,
  fetcher: FetchLike = fetch,
  signal?: AbortSignal,
): Promise<PlanningMutationResponse> {
  return requestJson(
    "/api/personal-planning/edit",
    {
      items,
      selected_item_ids: selectedItemIds,
      item_order: itemOrder,
      operation_id: operationId,
      edited_at: editedAt,
      expected_current_plan_fingerprint: expectedCurrentPlanFingerprint,
    },
    fetcher,
    "Не удалось сохранить изменения личного плана.",
    signal,
  );
}
