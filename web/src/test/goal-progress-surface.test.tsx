import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, describe, expect, it, vi } from "vitest";

import * as api from "../api";
import { GoalProgressSurface } from "../goal-progress-surface";

let root: Root | undefined;

afterEach(() => {
  act(() => root?.unmount());
  root = undefined;
  vi.restoreAllMocks();
  document.body.innerHTML = "";
});

const fingerprint = (value: string): string => `sha256:${value.repeat(64)}`;
const goalUuid = "0198c8a0-0000-7000-8000-000000000010";
const goalIdentityFingerprint = fingerprint("g");
const goal: api.GrowthGoalIdentity = {
  source_note_uuid: goalUuid,
  dimension: "goal",
  source_evidence_kind: "user_statement",
  source_self_kind: "goal",
  domain: "work",
  evidence_at: "unknown",
  evidence_at_precision: "unknown",
  source_contract_version: "self-model-v1",
  source_derivation_version: "self-model-derivation-v1",
  self_model_policy_fingerprint: fingerprint("a"),
  source_fingerprint: fingerprint("b"),
  claim_fingerprint: fingerprint("c"),
};
const selectedGoal: api.GrowthGoalOwnerItem = {
  goal,
  goal_text: "Достичь ясного рабочего ритма",
  goal_identity_fingerprint: goalIdentityFingerprint,
};
const goalProjection: api.GoalProgressGoalProjection = {
  source_note_uuid: goalUuid,
  text: selectedGoal.goal_text,
  domain: "work",
  identity_fingerprint: goalIdentityFingerprint,
};
const policyFingerprint = fingerprint("p");

const numericDefinition: api.GoalProgressDefinitionProjection = {
  id: "gpd-00000001",
  goal_source_uuid: goalUuid,
  goal_identity_fingerprint: goalIdentityFingerprint,
  goal_progress_policy_fingerprint: policyFingerprint,
  definition_reviewed_at: "2026-09-14T09:00:00Z",
  progress_model: "numeric_target",
  definition_fingerprint: fingerprint("d"),
  metric_id: "focus_minutes",
  unit: "минуты",
  baseline: "20",
  target: "60",
  direction: "increase_to",
};

const observation: api.GoalProgressObservationProjection = {
  id: "gpo-00000001",
  progress_definition_id: numericDefinition.id,
  observed_at: "2026-09-14T10:00:00Z",
  observed_at_precision: "exact",
  progress_model: "numeric_target",
  metric_id: "focus_minutes",
  unit: "минуты",
  value: "40",
  observation_reviewed_at: "2026-09-14T10:05:00Z",
};

function progress(
  status: api.GoalProgressStatus,
  definition: api.GoalProgressDefinitionProjection | null,
): api.GoalProgressResult {
  return {
    contract: "goal_progress_result_v1",
    selected_goal_source_uuid: goalUuid,
    current_goal_identity_fingerprint: goalIdentityFingerprint,
    goal_progress_policy_fingerprint: policyFingerprint,
    active_definition_uuid: definition?.id ?? null,
    definition_fingerprint: definition?.definition_fingerprint ?? null,
    as_of: "2026-09-14T12:00:00Z",
    progress_model: definition?.progress_model ?? null,
    status,
    current_observation_uuids: observation ? [observation.id] : [],
    excluded_observations: [],
    eligible_count: observation ? 1 : 0,
    unknown_time_count: 0,
    superseded_count: 0,
    invalid_count: 0,
    explanation: definition?.progress_model === "numeric_target"
      ? { metric_id: "focus_minutes", unit: "минуты", baseline: "20", target: "60", direction: "increase_to", current_value: "40" }
      : {},
    provenance: {},
    completed_milestone_ids: [],
    not_completed_milestone_ids: [],
    missing_milestone_ids: [],
  };
}

function composition(
  goalProgress: api.GoalProgressResult,
  definition: api.GoalProgressDefinitionProjection | null,
  observations: readonly api.GoalProgressObservationProjection[] = [],
  growthState: string = "goal_mapping_missing",
): api.GrowthGoalProgressCompositionResponse {
  return {
    web_contract: "growth_goal_progress_composition_web_v1",
    contract_version: "growth-engine-v1",
    derivation_version: "growth-engine-derivation-v1",
    policy_id: "growth-engine-v1",
    policy_fingerprint: fingerprint("q"),
    selected_goal_source_uuid: goalUuid,
    current_goal_identity_fingerprint: goalIdentityFingerprint,
    progress_as_of: goalProgress.as_of,
    growth_policy_fingerprint: fingerprint("r"),
    goal_progress_policy_fingerprint: policyFingerprint,
    growth_result: { goal_results: [{ state: growthState, mapping: null }] } as unknown as api.GrowthResponse,
    goal_progress_result: goalProgress,
    caveats: [],
    provenance: {},
    goal: goalProjection,
    definition,
    observations,
  };
}

function review(recordKind: "definition" | "observation"): api.GoalProgressReviewResponse {
  return {
    web_contract: "goal_progress_review_v1",
    status: "dry-run",
    review_token: `${recordKind}-review-token`,
    plan_sha256: fingerprint(`${recordKind}-plan`),
    record_kind: recordKind,
    record_id: recordKind === "definition" ? numericDefinition.id : "gpo-00000002",
    goal: goalProjection,
    record: recordKind === "definition"
      ? { progress_model: "numeric_target", metric_id: "focus_minutes", unit: "минуты", baseline: "20", target: "60", direction: "increase_to" }
      : { value: "50", observed_at: "unknown", observed_at_precision: "unknown" },
    write: {
      operation: "create",
      created: "2026-09-14T12:01:00Z",
      content_sha256: fingerprint(`${recordKind}-content`),
      record_kind: recordKind,
    },
  };
}

async function renderSurface(): Promise<HTMLDivElement> {
  const host = document.createElement("div");
  document.body.append(host);
  root = createRoot(host);
  await act(async () => root?.render(<GoalProgressSurface selectedGoal={selectedGoal} />));
  return host;
}

function button(host: HTMLElement, label: string): HTMLButtonElement {
  const found = Array.from(host.querySelectorAll<HTMLButtonElement>("button")).find((item) => item.textContent?.includes(label));
  if (!found) throw new Error(`button not found: ${label}`);
  return found;
}

function setValue(control: HTMLInputElement | HTMLSelectElement, value: string): void {
  const prototype = control instanceof HTMLSelectElement ? HTMLSelectElement.prototype : HTMLInputElement.prototype;
  const setter = Object.getOwnPropertyDescriptor(prototype, "value")?.set;
  setter?.call(control, value);
  control.dispatchEvent(new Event("input", { bubbles: true }));
  control.dispatchEvent(new Event("change", { bubbles: true }));
}

describe("Goal progress Stage 12E owner surface", () => {
  it("stays idle until the owner explicitly checks progress and keeps the two blocks independent", async () => {
    vi.stubGlobal("IS_REACT_ACT_ENVIRONMENT", true);
    const compositionSpy = vi.spyOn(api, "loadGrowthGoalProgress").mockResolvedValue(composition(progress("definition_missing", null), null));
    const host = await renderSurface();

    expect(compositionSpy).not.toHaveBeenCalled();
    expect(host.textContent).toContain("Проверить прогресс");
    expect(host.textContent).toContain("Проверка связи и измеряемого прогресса запускается только по явной кнопке.");

    await act(async () => button(host, "Проверить прогресс").click());
    expect(compositionSpy).toHaveBeenCalledWith(goalUuid, expect.any(String), undefined, expect.any(AbortSignal));
    expect(host.textContent).toContain("Связь поведения с целью");
    expect(host.textContent).toContain("Измеряемый прогресс");
    expect(host.textContent).toContain("Для этой цели пока не определено, как измерять прогресс.");
    expect(host.textContent).not.toMatch(/процент|score|оценк|прогноз|ETA|тренд|↗|↘|→/i);
    expect(host.innerHTML).not.toMatch(/localStorage|sessionStorage|indexedDB|Cache API/i);
  });

  it("runs definition prepare, explicit review confirmation and apply without exposing raw content", async () => {
    vi.stubGlobal("IS_REACT_ACT_ENVIRONMENT", true);
    vi.spyOn(api, "loadGrowthGoalProgress").mockResolvedValue(composition(progress("definition_missing", null), null));
    const prepareSpy = vi.spyOn(api, "prepareGoalProgressDefinition").mockResolvedValue(review("definition"));
    const applySpy = vi.spyOn(api, "applyGoalProgressDefinition").mockResolvedValue({
      web_contract: "goal_progress_apply_v1",
      status: "saved",
      write_status: "created",
      record_kind: "definition",
      record_id: numericDefinition.id,
      plan_sha256: fingerprint("definition-plan"),
    });
    const host = await renderSurface();
    await act(async () => button(host, "Проверить прогресс").click());
    await act(async () => button(host, "Настроить измерение").click());

    await act(async () => setValue(host.querySelector<HTMLSelectElement>("#goal-progress-definition-model")!, "numeric_target"));
    await act(async () => setValue(host.querySelector<HTMLInputElement>("#goal-progress-metric")!, "focus_minutes"));
    await act(async () => setValue(host.querySelector<HTMLInputElement>("#goal-progress-unit")!, "минуты"));
    await act(async () => setValue(host.querySelector<HTMLInputElement>("#goal-progress-baseline")!, "20"));
    await act(async () => setValue(host.querySelector<HTMLInputElement>("#goal-progress-target")!, "60"));
    await act(async () => setValue(host.querySelector<HTMLSelectElement>("#goal-progress-direction")!, "increase_to"));
    await act(async () => button(host, "Проверить правило").click());
    expect(prepareSpy).toHaveBeenCalledWith(expect.objectContaining({
      goal_source_uuid: goalUuid,
      progress_model: "numeric_target",
      metric_id: "focus_minutes",
      target: "60",
      direction: "increase_to",
      milestones: null,
    }), undefined, expect.any(AbortSignal));
    expect(host.textContent).toContain("Проверка перед записью");
    expect(host.textContent).toContain("Достичь ясного рабочего ритма");
    expect(host.textContent).toContain("Показатель");
    expect(host.textContent).toContain("focus_minutes");
    expect(host.textContent).toContain("Исходное значение");
    expect(host.textContent).toContain("60");
    expect(host.textContent).not.toContain("raw-content");

    await act(async () => host.querySelector<HTMLInputElement>(".goal-progress-review + .growth-confirm-label input")!.click());
    await act(async () => button(host, "Подтвердить запись правила").click());
    expect(applySpy).toHaveBeenCalledWith("definition-review-token", fingerprint("definition-plan"), undefined, expect.any(AbortSignal));
    expect(prepareSpy).toHaveBeenCalledOnce();
    expect(applySpy).toHaveBeenCalledOnce();
    expect(api.loadGrowthGoalProgress).toHaveBeenCalledTimes(2);
    expect(host.textContent).toContain("Для этой цели пока не определено, как измерять прогресс.");
  });

  it("submits an unknown-time observation and preserves explicit correction linkage", async () => {
    vi.stubGlobal("IS_REACT_ACT_ENVIRONMENT", true);
    vi.spyOn(api, "loadGrowthGoalProgress").mockResolvedValue(composition(progress("insufficient_observations", numericDefinition), numericDefinition, [observation]));
    const prepareSpy = vi.spyOn(api, "prepareGoalProgressObservation").mockResolvedValue(review("observation"));
    const applySpy = vi.spyOn(api, "applyGoalProgressObservation").mockResolvedValue({
      web_contract: "goal_progress_apply_v1",
      status: "saved",
      write_status: "created",
      record_kind: "observation",
      record_id: "gpo-00000002",
      plan_sha256: fingerprint("observation-plan"),
    });
    const host = await renderSurface();
    await act(async () => button(host, "Проверить прогресс").click());

    await act(async () => setValue(host.querySelector<HTMLInputElement>("#goal-progress-observation-value")!, "50"));
    await act(async () => setValue(host.querySelector<HTMLSelectElement>("#goal-progress-observation-time")!, "unknown"));
    await act(async () => button(host, "Проверить наблюдение").click());
    expect(prepareSpy).toHaveBeenCalledWith(expect.objectContaining({
      goal_source_uuid: goalUuid,
      progress_definition_id: numericDefinition.id,
      value: "50",
      observed_at: "unknown",
      observed_at_precision: "unknown",
      supersedes_observation_id: null,
    }), undefined, expect.any(AbortSignal));
    expect(host.textContent).toContain("не попадёт в текущее сравнение");
    await act(async () => host.querySelector<HTMLInputElement>(".goal-progress-review + .growth-confirm-label input")!.click());
    await act(async () => button(host, "Подтвердить запись наблюдения").click());
    expect(applySpy).toHaveBeenCalledWith("observation-review-token", fingerprint("observation-plan"), undefined, expect.any(AbortSignal));

    await act(async () => button(host, `Исправить запись ${observation.id}`).click());
    await act(async () => setValue(host.querySelector<HTMLInputElement>("#goal-progress-observation-value")!, "55"));
    await act(async () => setValue(host.querySelector<HTMLSelectElement>("#goal-progress-observation-time")!, "unknown"));
    await act(async () => button(host, "Проверить наблюдение").click());
    expect(prepareSpy).toHaveBeenLastCalledWith(expect.objectContaining({ supersedes_observation_id: observation.id }), undefined, expect.any(AbortSignal));
  });

  it("renders a contradictory Growth state and progress state as two neutral facts", async () => {
    vi.stubGlobal("IS_REACT_ACT_ENVIRONMENT", true);
    vi.spyOn(api, "loadGrowthGoalProgress").mockResolvedValue(
      composition(progress("away_from_target", numericDefinition), numericDefinition, [observation], "supports_goal"),
    );
    const host = await renderSurface();
    await act(async () => button(host, "Проверить прогресс").click());

    expect(host.textContent).toContain("Согласуется с целью");
    expect(host.textContent).toContain("Движение от цели");
    expect(host.textContent).not.toMatch(/неэффектив|провал|саботаж|дисциплин|причинно-следствен/i);
  });
});
