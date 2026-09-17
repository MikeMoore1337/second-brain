import { ApiRequestError, type FetchLike } from "./api";

export interface AgentPlanningItem extends Record<string, unknown> {
  readonly item_id: string;
  readonly kind: string;
  readonly title: string;
  readonly description: string;
  readonly goal_refs: readonly Record<string, unknown>[];
  readonly action_refs: readonly Record<string, unknown>[];
  readonly effort_minutes: number;
}

export interface AgentPlanningPlan extends Record<string, unknown> {
  readonly plan_id: string;
  readonly revision: number;
  readonly policy_id: string;
  readonly policy_fingerprint: string;
  readonly plan_fingerprint: string;
  readonly start_local: string;
  readonly end_local: string;
  readonly timezone: string;
  readonly items: readonly AgentPlanningItem[];
  readonly selected_item_ids: readonly string[];
  readonly item_order: readonly string[];
}

export interface AgentExecutionProjection extends Record<string, unknown> {
  readonly item_id: string;
  readonly accepted_item_fingerprint: string;
  readonly item_kind: string;
  readonly source_status: string;
  readonly state: string;
  readonly current_block_reasons: readonly string[];
  readonly caveats: readonly string[];
}

export interface AgentExecutionItem {
  readonly item_id: string;
  readonly projection: AgentExecutionProjection;
}

export interface AgentStage19Capability extends Record<string, unknown> {
  readonly status: "disabled" | "ready" | "credential_unavailable" | string;
  readonly configured: boolean;
  readonly repositories: readonly string[];
  readonly action_catalog: readonly AgentActionCatalogItem[];
  readonly owner_confirmation_required: boolean;
  readonly background_execution: boolean;
}

export interface AgentActionCatalogItem {
  readonly action_kind: string;
  readonly risk: "controlled_write" | string;
  readonly reversibility: string;
}

export interface AgentStateResponse {
  readonly web_contract: string;
  readonly current_plan: AgentPlanningPlan | null;
  readonly execution_items: readonly AgentExecutionItem[];
  readonly stage19: AgentStage19Capability;
  readonly current_run: AgentRun | null;
  readonly run_history: readonly AgentRun[];
  readonly caveats: readonly string[];
}

export interface AgentMissionItemBinding {
  readonly item_id: string;
  readonly accepted_item_fingerprint: string;
  readonly item_kind: string;
  readonly goal_refs: readonly Record<string, unknown>[];
  readonly action_refs: readonly Record<string, unknown>[];
}

export interface AgentExternalTarget {
  readonly action_kind: string;
  readonly repository: string;
  readonly issue_number: number | null;
}

export interface AgentMission {
  readonly contract_version: "personal-agent-mission-v1";
  readonly mission_id: string;
  readonly planning_snapshot_id: string;
  readonly planning_snapshot_fingerprint: string;
  readonly planning_policy_id: string;
  readonly planning_policy_fingerprint: string;
  readonly selected_items: readonly AgentMissionItemBinding[];
  readonly task: string;
  readonly constraints: readonly string[];
  readonly current_context: readonly string[];
  readonly external_targets: readonly AgentExternalTarget[];
  readonly created_at: string;
  readonly reviewed_at: string | null;
}

export interface AgentProviderPreview {
  readonly mission_fingerprint: string;
  readonly context_pack_fingerprint: string;
  readonly canonical_json: string;
  readonly canonical_bytes_sha256: string;
  readonly assistant_envelope: Record<string, unknown>;
}

export interface AgentContextResponse {
  readonly web_contract: string;
  readonly context_pack: Record<string, unknown>;
  readonly provider_preview: AgentProviderPreview;
  readonly current_plan: AgentPlanningPlan;
}

export type AgentStep = Record<string, unknown> & {
  readonly step_id: string;
  readonly position: number;
  readonly kind: "clarify" | "checkpoint" | "stage19_action" | "hold" | string;
};

export interface AgentProposal {
  readonly contract_version: string;
  readonly proposal_id: string;
  readonly mission_fingerprint: string;
  readonly context_pack_fingerprint: string;
  readonly steps: readonly AgentStep[];
  readonly caveats: readonly string[];
  readonly provider_policy_id: string;
  readonly provider_policy_fingerprint: string;
  readonly provider_fingerprint: string;
  readonly proposal_fingerprint: string;
}

export interface AgentBuildResponse {
  readonly web_contract: string;
  readonly context_pack: Record<string, unknown>;
  readonly provider_preview: AgentProviderPreview;
  readonly proposal: AgentProposal;
}

export interface AgentRunStep {
  readonly step: AgentStep;
  readonly state: string;
  readonly owner_answer: string | null;
  readonly resolution_note: string | null;
  readonly receipt_ref: Record<string, unknown> | null;
}

export interface AgentRun extends Record<string, unknown> {
  readonly run_id: string;
  readonly revision: number;
  readonly state: string;
  readonly mission: AgentMission;
  readonly context_pack_fingerprint: string;
  readonly proposal_fingerprint: string;
  readonly steps: readonly AgentRunStep[];
  readonly receipt_refs: readonly Record<string, unknown>[];
  readonly updated_at: string;
  readonly snapshot_fingerprint: string;
}

export interface AgentPreparedAction {
  readonly prepared_action_id: string;
  readonly action_kind: string;
  readonly target_safe_identity: Record<string, unknown>;
  readonly preview: string;
  readonly preview_fingerprint: string;
  readonly expires_at: string;
  readonly reversibility: string;
}

export interface AgentPreparedResponse {
  readonly web_contract: string;
  readonly run: AgentRun;
  readonly prepared: AgentPreparedAction;
  readonly confirmation_token: string;
}

export interface AgentRunResponse {
  readonly web_contract: string;
  readonly run: AgentRun;
}

const PURPOSE = "personal-agent-v1";
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

export function loadPersonalAgentState(
  fetcher: FetchLike = fetch,
  signal?: AbortSignal,
): Promise<AgentStateResponse> {
  return requestJson(
    "/api/personal-agent/state",
    {},
    fetcher,
    "Не удалось загрузить состояние агента.",
    signal,
  );
}

export function buildPersonalAgentContext(
  mission: AgentMission,
  fetcher: FetchLike = fetch,
  signal?: AbortSignal,
): Promise<AgentContextResponse> {
  return requestJson(
    "/api/personal-agent/context",
    { mission },
    fetcher,
    "Не удалось собрать точный контекст агента.",
    signal,
  );
}

export function buildPersonalAgentRun(
  mission: AgentMission,
  contextPack: Record<string, unknown>,
  providerPreview: AgentProviderPreview,
  fetcher: FetchLike = fetch,
  signal?: AbortSignal,
): Promise<AgentBuildResponse> {
  return requestJson(
    "/api/personal-agent/build",
    { mission, context_pack: contextPack, provider_preview: providerPreview },
    fetcher,
    "Не удалось построить предложение запуска.",
    signal,
  );
}

export function reviewPersonalAgentRun(
  mission: AgentMission,
  proposal: AgentProposal,
  steps: readonly AgentStep[],
  fetcher: FetchLike = fetch,
  signal?: AbortSignal,
): Promise<{ readonly proposal: AgentProposal }> {
  return requestJson(
    "/api/personal-agent/review",
    { mission, proposal, steps },
    fetcher,
    "Не удалось проверить изменения запуска.",
    signal,
  );
}

export function acceptPersonalAgentRun(
  mission: AgentMission,
  proposal: AgentProposal,
  operationId: string,
  fetcher: FetchLike = fetch,
  signal?: AbortSignal,
): Promise<AgentRunResponse> {
  return requestJson(
    "/api/personal-agent/accept",
    { mission, proposal, operation_id: operationId },
    fetcher,
    "Не удалось принять запуск.",
    signal,
  );
}

function runRequest<T>(
  path: string,
  mission: AgentMission,
  runId: string,
  operationId: string,
  fetcher: FetchLike,
  fallback: string,
  signal?: AbortSignal,
): Promise<T> {
  return requestJson(path, { mission, run_id: runId, operation_id: operationId }, fetcher, fallback, signal);
}

export function startPersonalAgentRun(
  mission: AgentMission,
  runId: string,
  operationId: string,
  fetcher: FetchLike = fetch,
  signal?: AbortSignal,
): Promise<AgentRunResponse> {
  return runRequest("/api/personal-agent/start", mission, runId, operationId, fetcher, "Не удалось запустить операцию.", signal);
}

export function pausePersonalAgentRun(
  mission: AgentMission,
  runId: string,
  operationId: string,
  fetcher: FetchLike = fetch,
  signal?: AbortSignal,
): Promise<AgentRunResponse> {
  return runRequest("/api/personal-agent/pause", mission, runId, operationId, fetcher, "Не удалось поставить операцию на паузу.", signal);
}

export function resumePersonalAgentRun(
  mission: AgentMission,
  runId: string,
  operationId: string,
  fetcher: FetchLike = fetch,
  signal?: AbortSignal,
): Promise<AgentRunResponse> {
  return runRequest("/api/personal-agent/resume", mission, runId, operationId, fetcher, "Не удалось продолжить операцию.", signal);
}

export function continuePersonalAgentRun(
  mission: AgentMission,
  runId: string,
  operationId: string,
  fetcher: FetchLike = fetch,
  signal?: AbortSignal,
): Promise<AgentRunResponse> {
  return runRequest("/api/personal-agent/continue", mission, runId, operationId, fetcher, "Не удалось продолжить текущий шаг.", signal);
}

export function answerPersonalAgentRun(
  mission: AgentMission,
  runId: string,
  answer: string,
  operationId: string,
  fetcher: FetchLike = fetch,
  signal?: AbortSignal,
): Promise<AgentRunResponse> {
  return requestJson(
    "/api/personal-agent/answer",
    { mission, run_id: runId, answer, operation_id: operationId },
    fetcher,
    "Не удалось сохранить ответ владельца.",
    signal,
  );
}

export function skipPersonalAgentRun(
  mission: AgentMission,
  runId: string,
  reason: string,
  operationId: string,
  fetcher: FetchLike = fetch,
  signal?: AbortSignal,
): Promise<AgentRunResponse> {
  return requestJson(
    "/api/personal-agent/skip",
    { mission, run_id: runId, reason, operation_id: operationId },
    fetcher,
    "Не удалось пропустить текущий шаг.",
    signal,
  );
}

export function abandonPersonalAgentRun(
  runId: string,
  operationId: string,
  fetcher: FetchLike = fetch,
  signal?: AbortSignal,
): Promise<AgentRunResponse> {
  return requestJson(
    "/api/personal-agent/abandon",
    { run_id: runId, operation_id: operationId },
    fetcher,
    "Не удалось остановить операцию.",
    signal,
  );
}

export function completePersonalAgentRun(
  runId: string,
  operationId: string,
  fetcher: FetchLike = fetch,
  signal?: AbortSignal,
): Promise<AgentRunResponse> {
  return requestJson(
    "/api/personal-agent/complete",
    { run_id: runId, operation_id: operationId },
    fetcher,
    "Не удалось завершить операцию.",
    signal,
  );
}

export function supersedePersonalAgentRun(
  runId: string,
  operationId: string,
  fetcher: FetchLike = fetch,
  signal?: AbortSignal,
): Promise<AgentRunResponse> {
  return requestJson(
    "/api/personal-agent/supersede",
    { run_id: runId, operation_id: operationId },
    fetcher,
    "Не удалось заменить текущую операцию.",
    signal,
  );
}

export function preparePersonalAgentAction(
  mission: AgentMission,
  runId: string,
  operationId: string,
  fetcher: FetchLike = fetch,
  signal?: AbortSignal,
): Promise<AgentPreparedResponse> {
  return runRequest("/api/personal-agent/prepare", mission, runId, operationId, fetcher, "Не удалось подготовить внешнее действие.", signal);
}

export function confirmPersonalAgentAction(
  mission: AgentMission,
  runId: string,
  prepared: AgentPreparedAction,
  confirmationToken: string,
  operationId: string,
  fetcher: FetchLike = fetch,
  signal?: AbortSignal,
): Promise<AgentRunResponse> {
  return requestJson(
    "/api/personal-agent/confirm",
    {
      mission,
      run_id: runId,
      prepared,
      confirmation_token: confirmationToken,
      operation_id: operationId,
    },
    fetcher,
    "Не удалось подтвердить внешнее действие.",
    signal,
  );
}

export function reconcilePersonalAgentAction(
  mission: AgentMission,
  runId: string,
  prepared: AgentPreparedAction,
  operationId: string,
  fetcher: FetchLike = fetch,
  signal?: AbortSignal,
): Promise<AgentRunResponse> {
  return requestJson(
    "/api/personal-agent/reconcile",
    {
      mission,
      run_id: runId,
      prepared,
      operation_id: operationId,
    },
    fetcher,
    "Не удалось проверить результат внешнего действия.",
    signal,
  );
}
