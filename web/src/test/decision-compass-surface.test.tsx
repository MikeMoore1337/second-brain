import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, describe, expect, it, vi } from "vitest";

import * as api from "../api";
import { DecisionCompassSurface } from "../decision-compass-surface";

let root: Root | undefined;

afterEach(() => {
  act(() => root?.unmount());
  root = undefined;
  vi.restoreAllMocks();
  document.body.innerHTML = "";
});

const goalUuid = "0198c8a0-0000-7000-8000-000000000010";
const goalFingerprint = "sha256:" + "a".repeat(64);

const goals = {
  goals: [{
    goal: { source_note_uuid: goalUuid },
    goal_text: "Достичь ясного рабочего ритма",
    goal_identity_fingerprint: goalFingerprint,
  }],
} as unknown as api.GrowthGoalsResponse;

const advisorPreview: api.GrowthAdvisorPreviewResponse = {
  contract_version: "growth-advisor-v1",
  goal_source_uuid: goalUuid,
  goal_identity_fingerprint: goalFingerprint,
  assistant_contract_version: "assistant-v1",
  advisor_policy_id: "growth-advisor-owner-explicit-goal-v1",
  goal_text: "Достичь ясного рабочего ритма",
  goal_text_utf8_bytes: 60,
};

function compassResponse(
  advisor: api.DecisionCompassAdvisorBranch = {
    state: "not_requested",
    result: null,
    error: null,
  },
): api.DecisionCompassResponse {
  return {
    contract_version: "growth-compare-v1",
    derivation_version: "growth-compare-derivation-v1",
    policy_id: "growth-compare-v1",
    policy_fingerprint: "sha256:" + "b".repeat(64),
    selected_goal: { source_uuid: goalUuid, identity_fingerprint: goalFingerprint },
    request: {} as api.DecisionCompassRequest,
    simulate_me: {
      state: "result",
      result: {
        kind: "prediction",
        selected_option: { id: "decision-option-1", label: "Сначала прояснить задачу" },
        evidence_refs: [],
        contextual_evidence_refs: [],
        temporal_caveats: [],
      },
      error: null,
    },
    behavioral: { state: "not_selected", pattern: null, error: null },
    growth_progress: {
      growth_result: { goal_results: [{ state: "neutral_or_unknown", cohort_fingerprint: null }] },
      goal_progress_result: { status: "unchanged", as_of: "2026-09-15T10:00:00Z" },
    } as unknown as api.GrowthGoalProgressCompositionResponse,
    advisor,
    structural_relations: [{
      code: advisor.state === "not_requested" ? "advisor_not_requested" : "simulate_advisor_not_comparable",
      left_branch: "simulate_me",
      right_branch: "advisor",
      left_state: "result",
      right_state: advisor.state,
      left_option_id: "decision-option-1",
      right_option_id: null,
    }],
    caveats: [],
    provenance: { progress_as_of: "2026-09-15T10:00:00Z", provider: "not_called", write: "not_written" },
  };
}

async function renderSurface(): Promise<HTMLDivElement> {
  const host = document.createElement("div");
  document.body.append(host);
  root = createRoot(host);
  await act(async () => root?.render(<DecisionCompassSurface />));
  return host;
}

function button(host: HTMLElement, label: string): HTMLButtonElement {
  const found = Array.from(host.querySelectorAll<HTMLButtonElement>("button")).find((item) => item.textContent?.includes(label));
  if (!found) throw new Error(`button not found: ${label}`);
  return found;
}

function setValue(control: HTMLInputElement | HTMLTextAreaElement | HTMLSelectElement, value: string): void {
  const prototype = control instanceof HTMLSelectElement
    ? HTMLSelectElement.prototype
    : control instanceof HTMLTextAreaElement
      ? HTMLTextAreaElement.prototype
      : HTMLInputElement.prototype;
  const setter = Object.getOwnPropertyDescriptor(prototype, "value")?.set;
  setter?.call(control, value);
  control.dispatchEvent(new Event("input", { bubbles: true }));
  control.dispatchEvent(new Event("change", { bubbles: true }));
}

describe("Decision Compass Stage 13C owner surface", () => {
  it("stays idle, keeps the goal/context choice explicit and exposes bounded inputs", async () => {
    vi.stubGlobal("IS_REACT_ACT_ENVIRONMENT", true);
    const fetchSpy = vi.spyOn(window, "fetch");
    const host = await renderSurface();

    expect(fetchSpy).not.toHaveBeenCalled();
    expect(host.textContent).toContain("загружаются только после явного действия");
    expect(host.textContent).toContain("Варианты (1/8)");
    expect(host.textContent).toContain("Критерии (0/8)");
    expect(host.querySelector<HTMLSelectElement>("#decision-compass-goal-select")?.value).toBe("");
    expect(host.querySelector("#decision-compass-cohort-select")).toBeNull();
    expect(host.textContent).not.toContain("Рекомендация получена");
  });

  it("builds the provider-free branch first and calls Advisor only after preview and confirmation", async () => {
    vi.stubGlobal("IS_REACT_ACT_ENVIRONMENT", true);
    const loadGoalsSpy = vi.spyOn(api, "loadGrowthGoals").mockResolvedValue(goals);
    const buildSpy = vi.spyOn(api, "buildDecisionCompass").mockResolvedValue(compassResponse());
    const previewSpy = vi.spyOn(api, "previewDecisionCompassAdvisor").mockResolvedValue(advisorPreview);
    const advisorResponse = compassResponse({
      state: "result",
      result: { recommendation: "Сделай первый шаг после проверки контекста." },
      error: null,
    });
    const executeSpy = vi.spyOn(api, "executeDecisionCompassAdvisor").mockResolvedValue(advisorResponse);
    const host = await renderSurface();

    await act(async () => button(host, "Загрузить текущие цели").click());
    expect(loadGoalsSpy).toHaveBeenCalledOnce();
    expect(host.querySelector<HTMLSelectElement>("#decision-compass-goal-select")?.value).toBe("");

    await act(async () => setValue(host.querySelector<HTMLSelectElement>("#decision-compass-goal-select")!, goalUuid));
    await act(async () => setValue(host.querySelector<HTMLTextAreaElement>("#decision-compass-task")!, "Выбрать следующий шаг"));
    await act(async () => setValue(host.querySelector<HTMLInputElement>("#decision-compass-option-0")!, "Сначала прояснить задачу"));
    await act(async () => button(host, "Добавить критерий").click());
    await act(async () => setValue(host.querySelector<HTMLInputElement>("#decision-compass-criterion-0")!, "Ясность"));
    await act(async () => button(host, "Смоделировать меня").click());

    expect(buildSpy).toHaveBeenCalledWith(expect.objectContaining({
      selected_goal: { source_uuid: goalUuid, identity_fingerprint: goalFingerprint },
      options: [{ id: "decision-option-1", label: "Сначала прояснить задачу" }],
      criteria: [{ id: "decision-criterion-1", label: "Ясность", description: null }],
    }));
    expect(previewSpy).not.toHaveBeenCalled();
    expect(executeSpy).not.toHaveBeenCalled();
    expect(host.textContent).toContain("Независимая рекомендация ещё не запрошена");

    await act(async () => button(host, "Получить независимую рекомендацию").click());
    expect(previewSpy).toHaveBeenCalledOnce();
    expect(executeSpy).not.toHaveBeenCalled();
    expect(host.textContent).toContain("Проверка перед отдельным запросом");

    await act(async () => host.querySelector<HTMLInputElement>(".decision-compass-confirm input")?.click());
    await act(async () => button(host, "Подтвердить запрос").click());
    expect(executeSpy).toHaveBeenCalledOnce();
    expect(host.textContent).toContain("Сделай первый шаг после проверки контекста.");
  });

  it("keeps transport failures Russian and moves focus to the alert", async () => {
    vi.stubGlobal("IS_REACT_ACT_ENVIRONMENT", true);
    vi.spyOn(api, "loadGrowthGoals").mockResolvedValue(goals);
    vi.spyOn(api, "buildDecisionCompass").mockRejectedValue(new Error("Failed to fetch"));
    const host = await renderSurface();

    await act(async () => button(host, "Загрузить текущие цели").click());
    await act(async () => setValue(host.querySelector<HTMLSelectElement>("#decision-compass-goal-select")!, goalUuid));
    await act(async () => setValue(host.querySelector<HTMLTextAreaElement>("#decision-compass-task")!, "Выбрать следующий шаг"));
    await act(async () => setValue(host.querySelector<HTMLInputElement>("#decision-compass-option-0")!, "Сначала прояснить задачу"));
    await act(async () => button(host, "Смоделировать меня").click());

    const alert = host.querySelector<HTMLElement>("[role='alert']");
    expect(alert?.textContent).toBe("Не удалось построить компас решения.");
    expect(document.activeElement).toBe(alert);
  });
});
