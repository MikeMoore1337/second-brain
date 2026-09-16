import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, describe, expect, it, vi } from "vitest";

import * as executionApi from "../execution-feedback-api";
import { ExecutionFeedbackSurface } from "../execution-feedback-surface";

let root: Root | undefined;
let host: HTMLDivElement | undefined;

const plan: executionApi.ExecutionPlanSummary = {
  planning_snapshot_id: "0198f4c5-6a00-7000-0000-000000000004",
  planning_snapshot_fingerprint: "a".repeat(64),
  planning_plan_revision: 1,
  as_of: "2026-09-16T06:00:00Z",
  start_local: "2026-09-16T09:00",
  end_local: "2026-09-16T18:00",
  planning_timezone: "Europe/Moscow",
  selected_item_count: 1,
  executable_item_count: 1,
  source_status: "current",
};

const item: executionApi.ExecutionItemState = {
  planning_snapshot_id: plan.planning_snapshot_id,
  planning_snapshot_fingerprint: plan.planning_snapshot_fingerprint,
  planning_plan_revision: 1,
  planning_policy_id: "stage17-personal-planning-v1",
  planning_policy_fingerprint: "c".repeat(64),
  item_id: "next-1",
  accepted_item_fingerprint: "b".repeat(64),
  item_kind: "next_action",
  title: "Сделать небольшой шаг",
  description: "Выполнить выбранное действие.",
  goal_refs: [{ goal_source_uuid: "0198f4c5-6a00-7000-8000-000000000001", goal_identity_fingerprint: "d".repeat(64) }],
  action_refs: [],
  parent_item_id: null,
  target_start_local: "2026-09-16T10:00",
  target_end_local: "2026-09-16T11:00",
  planning_timezone: plan.planning_timezone,
  planned_effort_minutes: 30,
  source_status: "current",
  state: "not_started",
  first_start_at: null,
  latest_event_at: null,
  latest_effective_event_at: null,
  terminal_at: null,
  current_block_reasons: [],
  current_block_note: "",
  actual_effort_minutes: null,
  effort_precision: "unknown",
  terminal_feedback: null,
  window_relation: "unknown",
  event_count: 0,
  effective_event_count: 0,
  voided_event_count: 0,
  correction_count: 0,
  history: [],
  caveats: [],
};

function report(overrides: Partial<executionApi.ExecutionFeedbackReport> = {}): executionApi.ExecutionFeedbackReport {
  return {
    planning_snapshot_id: plan.planning_snapshot_id,
    planning_snapshot_fingerprint: plan.planning_snapshot_fingerprint,
    planning_plan_revision: 1,
    planning_policy_id: "stage17-personal-planning-v1",
    planning_policy_fingerprint: "c".repeat(64),
    plan_start_local: plan.start_local,
    plan_end_local: plan.end_local,
    planning_timezone: plan.planning_timezone,
    source_status: "current",
    selected_item_count: 1,
    planned_executable_item_count: 1,
    items_with_any_event_count: 0,
    items_with_effective_start_count: 0,
    not_started_count: 1,
    in_progress_count: 0,
    paused_count: 0,
    blocked_count: 0,
    completed_count: 0,
    abandoned_count: 0,
    terminal_count: 0,
    terminal_exact_effort_count: 0,
    terminal_unknown_effort_count: 0,
    missing_terminal_effort_count: 0,
    terminal_effort_coverage_denominator: 0,
    comparable_effort_count: 0,
    comparable_planned_effort_minutes: 0,
    comparable_actual_effort_minutes: 0,
    aggregate_effort_delta_minutes: 0,
    effort_deltas: [],
    within_window_count: 0,
    before_window_count: 0,
    after_window_count: 0,
    no_window_count: 0,
    unknown_window_count: 0,
    window_relation_denominator: 0,
    blocker_reason_counts: [],
    terminal_reason_counts: [],
    deviation_reason_counts: [],
    items: [item],
    caveats: [],
    ...overrides,
  };
}

const state: executionApi.ExecutionFeedbackStateResponse = {
  web_contract: "execution_feedback_state_web_v1",
  current_plan: plan,
  report: report(),
  available_snapshots: [plan],
  caveats: [],
};

function eventView(eventType: string): executionApi.ExecutionHistoryEvent {
  return {
    event_id: "0198f4c5-6a00-7000-8000-000000000010",
    event_fingerprint: "e".repeat(64),
    event_type: eventType,
    occurred_at: "2026-09-16T12:30:00Z",
    effective: true,
    voided: false,
    correction: false,
    void_target_event_id: null,
    actual_effort_minutes: null,
    effort_precision: "unknown",
    actual_result_note: "",
    reason_codes: [],
    deviation_codes: [],
    result_disposition: null,
  };
}

async function renderSurface(): Promise<HTMLDivElement> {
  vi.stubGlobal("IS_REACT_ACT_ENVIRONMENT", true);
  host = document.createElement("div");
  document.body.append(host);
  root = createRoot(host);
  await act(async () => root?.render(<ExecutionFeedbackSurface />));
  return host;
}

afterEach(() => {
  act(() => root?.unmount());
  root = undefined;
  host?.remove();
  host = undefined;
  vi.restoreAllMocks();
  document.body.innerHTML = "";
});

describe("Execution Feedback surface", () => {
  it("does not load private execution data until the owner asks", async () => {
    const load = vi.spyOn(executionApi, "loadExecutionFeedbackState").mockResolvedValue(state);
    const rendered = await renderSurface();

    expect(load).not.toHaveBeenCalled();
    expect(rendered.textContent).toContain("Загрузка выполняется только по этой кнопке.");
    expect(rendered.textContent).toContain("Здесь появляется только то выполнение, которое ты явно записал.");

    await act(async () => rendered.querySelector<HTMLButtonElement>(".execution-load-button")?.click());
    expect(load).toHaveBeenCalledOnce();
    expect(rendered.textContent).toContain("Сделать небольшой шаг");
    expect(rendered.querySelector("[data-execution-action='start']")).not.toBeNull();
    expect(rendered.innerHTML).not.toMatch(/localStorage|sessionStorage|indexedDB|Cache Storage/i);
  });

  it("requires visible event time and sends exact item identity on Start", async () => {
    vi.spyOn(executionApi, "loadExecutionFeedbackState").mockResolvedValue(state);
    const record = vi.spyOn(executionApi, "recordExecutionFeedbackEvent").mockResolvedValue({
      web_contract: "execution_feedback_event_web_v1",
      status: "recorded",
      plan,
      event: eventView("start"),
      item: { ...item, state: "in_progress", event_count: 1, effective_event_count: 1, first_start_at: "2026-09-16T12:30:00Z", latest_event_at: "2026-09-16T12:30:00Z", latest_effective_event_at: "2026-09-16T12:30:00Z", history: [eventView("start")] },
      report: report({ items_with_any_event_count: 1, items_with_effective_start_count: 1, not_started_count: 0, in_progress_count: 1 }),
    });
    const rendered = await renderSurface();
    await act(async () => rendered.querySelector<HTMLButtonElement>(".execution-load-button")?.click());
    await act(async () => rendered.querySelector<HTMLButtonElement>("[data-execution-action='start']")?.click());

    const time = rendered.querySelector<HTMLInputElement>("#execution-event-time");
    expect(time?.value).toBe("");
    const submit = Array.from(rendered.querySelectorAll<HTMLButtonElement>("[data-execution-dialog] button")).find((button) => button.textContent?.includes("Подтвердить: Начать"));
    expect(submit).not.toBeUndefined();
    await act(async () => submit?.click());
    expect(record).not.toHaveBeenCalled();
    expect(time?.value).toBe("");

    const now = Array.from(rendered.querySelectorAll<HTMLButtonElement>("[data-execution-dialog] button")).find((button) => button.textContent === "Сейчас");
    expect(now).not.toBeUndefined();
    await act(async () => now?.click());
    await act(async () => Array.from(rendered.querySelectorAll<HTMLButtonElement>("[data-execution-dialog] button")).find((button) => button.textContent?.includes("Подтвердить: Начать"))?.click());

    expect(record).toHaveBeenCalledOnce();
    expect(record.mock.calls[0]?.[0]).toMatchObject({
      planning_snapshot_id: plan.planning_snapshot_id,
      planning_snapshot_fingerprint: plan.planning_snapshot_fingerprint,
      item_id: item.item_id,
      accepted_item_fingerprint: item.accepted_item_fingerprint,
      event_type: "start",
      effort_precision: "unknown",
      actual_effort_minutes: null,
    });
    expect(record.mock.calls[0]?.[0].occurred_at).toContain("T");
  });

  it("collects exact terminal effort without treating unknown as zero", async () => {
    const inProgress = { ...item, state: "in_progress" as const, first_start_at: "2026-09-16T10:00:00Z", event_count: 1, effective_event_count: 1 };
    vi.spyOn(executionApi, "loadExecutionFeedbackState").mockResolvedValue({ ...state, report: report({ items: [inProgress], not_started_count: 0, in_progress_count: 1, items_with_any_event_count: 1, items_with_effective_start_count: 1 }) });
    const record = vi.spyOn(executionApi, "recordExecutionFeedbackEvent").mockResolvedValue({
      web_contract: "execution_feedback_event_web_v1",
      status: "recorded",
      plan,
      event: { ...eventView("complete"), actual_effort_minutes: 42, effort_precision: "exact", result_disposition: "as_planned" },
      item: { ...inProgress, state: "completed", actual_effort_minutes: 42, effort_precision: "exact" },
      report: report({ items: [{ ...inProgress, state: "completed", actual_effort_minutes: 42, effort_precision: "exact" }], in_progress_count: 0, completed_count: 1, terminal_count: 1, terminal_exact_effort_count: 1, comparable_effort_count: 1, comparable_planned_effort_minutes: 30, comparable_actual_effort_minutes: 42, aggregate_effort_delta_minutes: 12 }),
    });
    const rendered = await renderSurface();
    await act(async () => rendered.querySelector<HTMLButtonElement>(".execution-load-button")?.click());
    await act(async () => rendered.querySelector<HTMLButtonElement>("[data-execution-action='complete']")?.click());
    const now = Array.from(rendered.querySelectorAll<HTMLButtonElement>("[data-execution-dialog] button")).find((button) => button.textContent === "Сейчас");
    await act(async () => now?.click());
    const exact = Array.from(rendered.querySelectorAll<HTMLInputElement>("[data-execution-dialog] input[type='radio']")).find((input) => input.parentElement?.textContent?.includes("Указать точно"));
    await act(async () => exact?.click());
    const effort = rendered.querySelector<HTMLInputElement>("#execution-effort-minutes");
    expect(effort).not.toBeNull();
    if (effort) {
      const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")?.set;
      setter?.call(effort, "42");
      effort.dispatchEvent(new Event("input", { bubbles: true }));
      effort.dispatchEvent(new Event("change", { bubbles: true }));
    }
    await act(async () => rendered.querySelector<HTMLButtonElement>("[data-execution-dialog] button[type='submit']")?.click());

    expect(record).toHaveBeenCalledOnce();
    expect(record.mock.calls[0]?.[0]).toMatchObject({ event_type: "complete", actual_effort_minutes: 42, effort_precision: "exact", result_disposition: "unknown" });
  });

  it("sends only explicitly selected snapshots to calibration", async () => {
    const historical = { ...plan, planning_plan_revision: 0, planning_snapshot_id: "0198f4c5-6a00-7000-0000-000000000005", planning_snapshot_fingerprint: "f".repeat(64), source_status: "superseded" as const };
    vi.spyOn(executionApi, "loadExecutionFeedbackState").mockResolvedValue({ ...state, available_snapshots: [plan, historical] });
    const calibration = vi.spyOn(executionApi, "loadExecutionCalibration").mockResolvedValue({ web_contract: "execution_feedback_calibration_web_v1", status: "ok", calibration: { selected_plan_count: 1, selected_executable_item_count: 1, execution_observed_item_count: 0, not_started_count: 1, in_progress_count: 0, paused_count: 0, blocked_current_count: 0, completed_count: 0, abandoned_count: 0, terminal_exact_effort_count: 0, terminal_unknown_effort_count: 0, missing_terminal_effort_count: 0, terminal_effort_coverage_denominator: 0, effort_comparable_count: 0, sum_planned_effort_minutes: 0, sum_actual_effort_minutes: 0, sum_delta_minutes: 0, within_window_count: 0, before_window_count: 0, after_window_count: 0, no_window_count: 0, unknown_window_count: 0, window_relation_denominator: 0, blocker_reason_counts: [], terminal_reason_counts: [], deviation_reason_counts: [], plans: [], caveats: [] } });
    const rendered = await renderSurface();
    await act(async () => rendered.querySelector<HTMLButtonElement>(".execution-load-button")?.click());
    const choices = rendered.querySelectorAll<HTMLInputElement>(".execution-snapshot-choice input");
    expect(choices).toHaveLength(2);
    await act(async () => choices[1]?.click());
    await act(async () => Array.from(rendered.querySelectorAll<HTMLButtonElement>(".execution-calibration button")).find((button) => button.textContent?.includes("Собрать"))?.click());

    expect(calibration).toHaveBeenCalledOnce();
    expect(calibration.mock.calls[0]?.[0]).toEqual([{ planning_snapshot_id: historical.planning_snapshot_id, planning_snapshot_fingerprint: historical.planning_snapshot_fingerprint }]);
    expect(rendered.querySelector("[data-execution-calibration-result]")).not.toBeNull();
  });
});
