import { ApiRequestError, type FetchLike } from "./api";

export type ExecutionEventType =
  | "start"
  | "pause"
  | "resume"
  | "block"
  | "unblock"
  | "complete"
  | "abandon";

export type ExecutionEffortPrecision = "exact" | "unknown";
export type ExecutionResultDisposition = "as_planned" | "with_changes" | "partial" | "unknown";

export interface ExecutionPlanSummary {
  readonly planning_snapshot_id: string;
  readonly planning_snapshot_fingerprint: string;
  readonly planning_plan_revision: number;
  readonly as_of: string;
  readonly start_local: string;
  readonly end_local: string;
  readonly planning_timezone: string;
  readonly selected_item_count: number;
  readonly executable_item_count: number;
  readonly source_status: "current" | "stale" | "unavailable" | "superseded" | string;
}

export interface ExecutionGoalRef {
  readonly goal_source_uuid: string;
  readonly goal_identity_fingerprint: string;
}

export interface ExecutionActionRef {
  readonly goal_source_uuid: string;
  readonly goal_identity_fingerprint: string;
  readonly strategy_snapshot_id: string;
  readonly strategy_snapshot_fingerprint: string;
  readonly reviewed_action_id: string;
  readonly reviewed_action_fingerprint: string;
  readonly stage16_policy_id: string;
  readonly stage16_policy_fingerprint: string;
}

export interface ExecutionHistoryEvent {
  readonly event_id: string;
  readonly event_fingerprint: string;
  readonly event_type: string;
  readonly occurred_at: string;
  readonly effective: boolean;
  readonly voided: boolean;
  readonly correction: boolean;
  readonly void_target_event_id: string | null;
  readonly actual_effort_minutes: number | null;
  readonly effort_precision: ExecutionEffortPrecision | string;
  readonly actual_result_note: string;
  readonly reason_codes: readonly string[];
  readonly deviation_codes: readonly string[];
  readonly result_disposition: ExecutionResultDisposition | string | null;
}

export interface ExecutionTerminalFeedback {
  readonly event_id: string;
  readonly event_fingerprint: string;
  readonly occurred_at: string;
  readonly actual_effort_minutes: number | null;
  readonly effort_precision: ExecutionEffortPrecision | string;
  readonly actual_result_note: string;
  readonly reason_codes: readonly string[];
  readonly deviation_codes: readonly string[];
  readonly result_disposition: ExecutionResultDisposition | string | null;
}

export interface ExecutionItemState {
  readonly planning_snapshot_id: string;
  readonly planning_snapshot_fingerprint: string;
  readonly planning_plan_revision: number;
  readonly planning_policy_id: string;
  readonly planning_policy_fingerprint: string;
  readonly item_id: string;
  readonly accepted_item_fingerprint: string;
  readonly item_kind: "commitment" | "next_action" | "project" | "milestone" | "hold" | string;
  readonly title: string;
  readonly description: string;
  readonly goal_refs: readonly ExecutionGoalRef[];
  readonly action_refs: readonly ExecutionActionRef[];
  readonly parent_item_id: string | null;
  readonly target_start_local: string | null;
  readonly target_end_local: string | null;
  readonly planning_timezone: string;
  readonly planned_effort_minutes: number;
  readonly source_status: "current" | "stale" | "unavailable" | "superseded" | string;
  readonly state: "not_started" | "in_progress" | "paused" | "blocked" | "completed" | "abandoned" | string;
  readonly first_start_at: string | null;
  readonly latest_event_at: string | null;
  readonly latest_effective_event_at: string | null;
  readonly terminal_at: string | null;
  readonly current_block_reasons: readonly string[];
  readonly current_block_note: string;
  readonly actual_effort_minutes: number | null;
  readonly effort_precision: ExecutionEffortPrecision | string;
  readonly terminal_feedback: ExecutionTerminalFeedback | null;
  readonly window_relation: "within_window" | "before_window" | "after_window" | "no_window" | "unknown" | string;
  readonly event_count: number;
  readonly effective_event_count: number;
  readonly voided_event_count: number;
  readonly correction_count: number;
  readonly history: readonly ExecutionHistoryEvent[];
  readonly caveats: readonly string[];
}

export interface ExecutionReasonCount {
  readonly code: string;
  readonly count: number;
}

export interface ExecutionEffortDelta {
  readonly item_id: string;
  readonly accepted_item_fingerprint: string;
  readonly planned_effort_minutes: number;
  readonly actual_effort_minutes: number;
  readonly delta_minutes: number;
}

export interface ExecutionFeedbackReport {
  readonly planning_snapshot_id: string;
  readonly planning_snapshot_fingerprint: string;
  readonly planning_plan_revision: number;
  readonly planning_policy_id: string;
  readonly planning_policy_fingerprint: string;
  readonly plan_start_local: string;
  readonly plan_end_local: string;
  readonly planning_timezone: string;
  readonly source_status: "current" | "stale" | "unavailable" | "superseded" | string;
  readonly selected_item_count: number;
  readonly planned_executable_item_count: number;
  readonly items_with_any_event_count: number;
  readonly items_with_effective_start_count: number;
  readonly not_started_count: number;
  readonly in_progress_count: number;
  readonly paused_count: number;
  readonly blocked_count: number;
  readonly completed_count: number;
  readonly abandoned_count: number;
  readonly terminal_count: number;
  readonly terminal_exact_effort_count: number;
  readonly terminal_unknown_effort_count: number;
  readonly missing_terminal_effort_count: number;
  readonly terminal_effort_coverage_denominator: number;
  readonly comparable_effort_count: number;
  readonly comparable_planned_effort_minutes: number;
  readonly comparable_actual_effort_minutes: number;
  readonly aggregate_effort_delta_minutes: number;
  readonly effort_deltas: readonly ExecutionEffortDelta[];
  readonly within_window_count: number;
  readonly before_window_count: number;
  readonly after_window_count: number;
  readonly no_window_count: number;
  readonly unknown_window_count: number;
  readonly window_relation_denominator: number;
  readonly blocker_reason_counts: readonly ExecutionReasonCount[];
  readonly terminal_reason_counts: readonly ExecutionReasonCount[];
  readonly deviation_reason_counts: readonly ExecutionReasonCount[];
  readonly items: readonly ExecutionItemState[];
  readonly caveats: readonly string[];
}

export interface ExecutionFeedbackStateResponse {
  readonly web_contract: string;
  readonly current_plan: ExecutionPlanSummary | null;
  readonly report: ExecutionFeedbackReport | null;
  readonly available_snapshots: readonly ExecutionPlanSummary[];
  readonly caveats: readonly string[];
}

export interface ExecutionFeedbackEventPayload {
  readonly planning_snapshot_id: string;
  readonly planning_snapshot_fingerprint: string;
  readonly item_id: string;
  readonly accepted_item_fingerprint: string;
  readonly operation_id: string;
  readonly event_type: ExecutionEventType;
  readonly occurred_at: string;
  readonly actual_effort_minutes: number | null;
  readonly effort_precision: ExecutionEffortPrecision;
  readonly actual_result_note: string;
  readonly reason_codes: readonly string[];
  readonly deviation_codes: readonly string[];
  readonly result_disposition: ExecutionResultDisposition | null;
}

export interface ExecutionFeedbackCorrectionPayload {
  readonly planning_snapshot_id: string;
  readonly planning_snapshot_fingerprint: string;
  readonly item_id: string;
  readonly accepted_item_fingerprint: string;
  readonly operation_id: string;
  readonly occurred_at: string;
  readonly void_target_event_id: string;
  readonly void_target_event_fingerprint: string;
  readonly correction_reason: string;
}

export interface ExecutionFeedbackMutationResponse {
  readonly web_contract: string;
  readonly status: "recorded" | "replayed" | string;
  readonly plan: ExecutionPlanSummary;
  readonly event: ExecutionHistoryEvent;
  readonly item: ExecutionItemState | null;
  readonly report: ExecutionFeedbackReport;
}

export interface ExecutionFeedbackResponse {
  readonly web_contract: string;
  readonly status: "ok" | string;
  readonly plan: ExecutionPlanSummary;
  readonly report: ExecutionFeedbackReport;
}

export interface ExecutionCalibrationResponse {
  readonly web_contract: string;
  readonly status: "ok" | string;
  readonly calibration: ExecutionCalibration;
}

export interface ExecutionCalibration {
  readonly selected_plan_count: number;
  readonly selected_executable_item_count: number;
  readonly execution_observed_item_count: number;
  readonly not_started_count: number;
  readonly in_progress_count: number;
  readonly paused_count: number;
  readonly blocked_current_count: number;
  readonly completed_count: number;
  readonly abandoned_count: number;
  readonly terminal_exact_effort_count: number;
  readonly terminal_unknown_effort_count: number;
  readonly missing_terminal_effort_count: number;
  readonly terminal_effort_coverage_denominator: number;
  readonly effort_comparable_count: number;
  readonly sum_planned_effort_minutes: number;
  readonly sum_actual_effort_minutes: number;
  readonly sum_delta_minutes: number;
  readonly within_window_count: number;
  readonly before_window_count: number;
  readonly after_window_count: number;
  readonly no_window_count: number;
  readonly unknown_window_count: number;
  readonly window_relation_denominator: number;
  readonly blocker_reason_counts: readonly ExecutionReasonCount[];
  readonly terminal_reason_counts: readonly ExecutionReasonCount[];
  readonly deviation_reason_counts: readonly ExecutionReasonCount[];
  readonly plans: readonly ExecutionFeedbackReport[];
  readonly caveats: readonly string[];
}

const PURPOSE = "execution-feedback-v1";
const PRIVATE_CACHE_POLICY = ["no", "store"].join("-") as RequestCache;

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
    cache: PRIVATE_CACHE_POLICY,
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

export function loadExecutionFeedbackState(
  fetcher: FetchLike = fetch,
  signal?: AbortSignal,
): Promise<ExecutionFeedbackStateResponse> {
  return requestJson(
    "/api/execution-feedback/state",
    {},
    fetcher,
    "Не удалось загрузить состояние выполнения.",
    signal,
  );
}

export function recordExecutionFeedbackEvent(
  payload: ExecutionFeedbackEventPayload,
  fetcher: FetchLike = fetch,
  signal?: AbortSignal,
): Promise<ExecutionFeedbackMutationResponse> {
  return requestJson(
    "/api/execution-feedback/event",
    payload,
    fetcher,
    "Не удалось записать событие выполнения.",
    signal,
  );
}

export function loadExecutionFeedback(
  planningSnapshotId: string,
  planningSnapshotFingerprint: string,
  fetcher: FetchLike = fetch,
  signal?: AbortSignal,
): Promise<ExecutionFeedbackResponse> {
  return requestJson(
    "/api/execution-feedback/feedback",
    {
      planning_snapshot_id: planningSnapshotId,
      planning_snapshot_fingerprint: planningSnapshotFingerprint,
    },
    fetcher,
    "Не удалось загрузить сводку выполнения.",
    signal,
  );
}

export function loadExecutionCalibration(
  snapshots: readonly { readonly planning_snapshot_id: string; readonly planning_snapshot_fingerprint: string }[],
  fetcher: FetchLike = fetch,
  signal?: AbortSignal,
): Promise<ExecutionCalibrationResponse> {
  return requestJson(
    "/api/execution-feedback/calibration",
    { snapshots },
    fetcher,
    "Не удалось собрать калибровку планов.",
    signal,
  );
}

export function correctExecutionFeedback(
  payload: ExecutionFeedbackCorrectionPayload,
  fetcher: FetchLike = fetch,
  signal?: AbortSignal,
): Promise<ExecutionFeedbackMutationResponse> {
  return requestJson(
    "/api/execution-feedback/correction",
    payload,
    fetcher,
    "Не удалось исправить запись выполнения.",
    signal,
  );
}
