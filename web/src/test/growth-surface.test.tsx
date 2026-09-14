import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, describe, expect, it, vi } from "vitest";

import * as api from "../api";
import { GrowthSurface } from "../growth-surface";

let root: Root | undefined;

afterEach(() => {
  act(() => root?.unmount());
  root = undefined;
  vi.restoreAllMocks();
  vi.useRealTimers();
  document.body.innerHTML = "";
});

const fingerprint = (value: string): string => `sha256:${value.repeat(64)}`;
const goalUuid = "0198c8a0-0000-7000-8000-000000000010";
const cohortFingerprint = fingerprint("a");
const optionFingerprint = fingerprint("b");

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
  self_model_policy_fingerprint: fingerprint("c"),
  source_fingerprint: fingerprint("d"),
  claim_fingerprint: fingerprint("e"),
};

const goals: api.GrowthGoalsResponse = {
  contract_version: "growth-engine-v1",
  derivation_version: "growth-engine-derivation-v1",
  policy_id: "growth-engine-v1",
  policy_fingerprint: fingerprint("f"),
  generated_at: "2026-09-14T10:00:00Z",
  selection_mode: "each_current_goal",
  selected_goal_source_uuid: null,
  eligible_goal_count: 1,
  goals: [{ goal, goal_text: "Достичь ясного рабочего ритма", goal_identity_fingerprint: fingerprint("g") }],
  reason_codes: [],
  caveats: [],
};

const growth: api.GrowthResponse = {
  contract_version: "growth-engine-v1",
  derivation_version: "growth-engine-derivation-v1",
  policy_id: "growth-engine-v1",
  policy_fingerprint: fingerprint("h"),
  generated_at: "2026-09-14T10:00:00Z",
  selection_mode: "selected_goal",
  selected_goal_source_uuid: goalUuid,
  eligible_goal_count: 1,
  goal_results: [{
    goal,
    state: "goal_mapping_missing",
    cohort_fingerprint: cohortFingerprint,
    behavioral_pattern: {
      contract_version: "behavioral-observation-v1",
      derivation_version: "behavioral-observation-full-derivation-v1",
      policy_id: "behavioral-observation-v1",
      policy_fingerprint: fingerprint("i"),
      cohort_fingerprint: cohortFingerprint,
      pattern_type: "repeated_exact_choice",
      pattern_state: "current",
      provenance_fingerprint: fingerprint("j"),
      source_count: 1,
      reference_fingerprint: fingerprint("k"),
      current_option: { option_index: 0, option_fingerprint: optionFingerprint },
    },
    behavioral_option: { option_index: 0, option_fingerprint: optionFingerprint },
    mapping: null,
    reason_codes: ["GROWTH_GOAL_MAPPING_MISSING"],
    caveats: ["mapping_requires_owner_review"],
    temporal: {
      goal_evidence_at: "unknown",
      goal_evidence_at_precision: "unknown",
      behavioral_generated_at: "2026-09-14T10:00:00Z",
      behavioral_current_window_start: null,
      behavioral_current_window_end: null,
      mapping_reviewed_at: null,
      mapping_created_at: null,
      advisor_requested_at: null,
    },
    advisor: null,
  }],
  reason_codes: [],
  caveats: [],
};

const mappingStatus: api.GrowthMappingStatusResponse = {
  mapping_policy_id: "growth-goal-choice-explicit-mapping-v1",
  mapping_policy_fingerprint: fingerprint("l"),
  mappings: [],
  active_mapping_count: 0,
};

const mappingReview: api.GrowthMappingReviewResponse = {
  generated_at: "2026-09-14T10:00:00Z",
  goal,
  behavioral_target: {},
  candidate_mapping_fingerprint: fingerprint("m"),
  goal_text: "Достичь ясного рабочего ритма",
  goal_domain: "work",
  goal_evidence_at: "unknown",
  goal_evidence_at_precision: "unknown",
  situation: "Перед рабочим блоком",
  information_known_at_decision_time: "Доступный контекст",
  criteria: ["ясность"],
  ordered_options: [{ option_index: 0, option_fingerprint: optionFingerprint, label: "Сначала прояснить задачу" }],
  pattern_type: "repeated_exact_choice",
  pattern_state: "current",
  selected_option: { option_index: 0, option_fingerprint: optionFingerprint },
  proposed_relation: "conflicts_with_goal",
  caveats: [],
};

const candidate: api.GrowthLearningCandidate = {
  contract_version: "growth-learning-v1",
  derivation_version: "growth-learning-derivation-v1",
  candidate_id: `gl1:${"a".repeat(64)}`,
  kind: "relation_review",
  reason_code: "missing_goal_mapping",
  growth_contract_version: "growth-engine-v1",
  growth_derivation_version: "growth-engine-derivation-v1",
  growth_policy_id: "growth-engine-v1",
  growth_policy_fingerprint: fingerprint("n"),
  goal_source_uuid: goalUuid,
  goal_identity_fingerprint: goals.goals[0].goal_identity_fingerprint,
  growth_state: "goal_mapping_missing",
  cohort_fingerprint: cohortFingerprint,
  behavioral_option_fingerprint: optionFingerprint,
  behavioral_reference_fingerprint: fingerprint("o"),
  mapping_id: null,
  mapping_fingerprint: null,
  question: "Для текущего наблюдаемого варианта ещё не задано, как он относится к выбранной цели. Хочешь проверить эту связь?",
  basis_fingerprint: fingerprint("p"),
  issued_at: "2026-09-14T10:00:00Z",
  expires_at: "2026-09-14T10:10:00Z",
};

async function renderSurface(): Promise<HTMLDivElement> {
  const host = document.createElement("div");
  document.body.append(host);
  root = createRoot(host);
  await act(async () => root?.render(<GrowthSurface />));
  return host;
}

function button(host: HTMLElement, label: string): HTMLButtonElement {
  const found = Array.from(host.querySelectorAll<HTMLButtonElement>("button")).find((item) => item.textContent?.includes(label));
  if (!found) throw new Error(`button not found: ${label}`);
  return found;
}

function setValue(control: HTMLInputElement | HTMLTextAreaElement | HTMLSelectElement, value: string): void {
  const prototype = control instanceof HTMLSelectElement ? HTMLSelectElement.prototype : control instanceof HTMLTextAreaElement ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
  const setter = Object.getOwnPropertyDescriptor(prototype, "value")?.set;
  setter?.call(control, value);
  control.dispatchEvent(new Event("input", { bubbles: true }));
  control.dispatchEvent(new Event("change", { bubbles: true }));
}

function mockReadApis(): void {
  vi.spyOn(api, "loadGrowthGoals").mockResolvedValue(goals);
  vi.spyOn(api, "loadGrowthMappingStatus").mockResolvedValue(mappingStatus);
  vi.spyOn(api, "loadGrowth").mockResolvedValue(growth);
}

async function build(host: HTMLElement): Promise<void> {
  await act(async () => button(host, "Обновить данные развития").click());
  await act(async () => setValue(host.querySelector<HTMLSelectElement>("#growth-goal-select")!, goalUuid));
  await act(async () => button(host, "Построить результат развития").click());
}

describe("Growth Stage 11E owner surface", () => {
  it("stays idle, does not persist browser state and requires explicit Goal selection/build", async () => {
    vi.stubGlobal("IS_REACT_ACT_ENVIRONMENT", true);
    const fetchSpy = vi.spyOn(window, "fetch");
    const host = await renderSurface();

    expect(fetchSpy).not.toHaveBeenCalled();
    expect(host.textContent).toContain("Данные раздела «Развитие» загружаются только после явного действия");
    expect(host.textContent).not.toContain("этап 11E");
    expect(host.textContent).not.toContain("localStorage");
    expect(host.textContent).not.toContain("sessionStorage");
  });

  it("loads server-owned Goals, keeps selection inert, then renders the exact Growth state and mapping review", async () => {
    vi.stubGlobal("IS_REACT_ACT_ENVIRONMENT", true);
    mockReadApis();
    const reviewSpy = vi.spyOn(api, "reviewGrowthMapping").mockResolvedValue(mappingReview);
    const host = await renderSurface();

    await act(async () => button(host, "Обновить данные развития").click());
    expect(api.loadGrowthGoals).toHaveBeenCalledOnce();
    expect(api.loadGrowth).not.toHaveBeenCalled();
    setValue(host.querySelector<HTMLSelectElement>("#growth-goal-select")!, goalUuid);
    expect(api.loadGrowth).not.toHaveBeenCalled();
    await act(async () => button(host, "Построить результат развития").click());
    expect(api.loadGrowth).toHaveBeenCalledWith(goalUuid, undefined, expect.any(AbortSignal));
    expect(host.textContent).toContain("Связь ещё не задана");
    expect(host.textContent).toContain("Отсутствие связи не означает конфликт");

    setValue(host.querySelector<HTMLSelectElement>("#growth-relation-select")!, "conflicts_with_goal");
    await act(async () => button(host, "Уточнить связь — показать проверку").click());
    expect(reviewSpy).toHaveBeenCalledWith(expect.objectContaining({ source_note_uuid: goalUuid }), "conflicts_with_goal", undefined, expect.any(AbortSignal));
    expect(host.textContent).toContain("Достичь ясного рабочего ритма");
    expect(host.textContent).toContain("Перед рабочим блоком");
  });

  it("requires a separate Advisor preview and confirmation and keeps Learning independent", async () => {
    vi.stubGlobal("IS_REACT_ACT_ENVIRONMENT", true);
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-09-14T10:05:00Z"));
    mockReadApis();
    const preview = { ...mappingReview, contract_version: "growth-advisor-v1" as const, goal_source_uuid: goalUuid, goal_identity_fingerprint: goals.goals[0].goal_identity_fingerprint, assistant_contract_version: "assistant-v1", advisor_policy_id: "growth-advisor-owner-explicit-goal-v1", goal_text: "Достичь ясного рабочего ритма", goal_text_utf8_bytes: 42 };
    const branch: api.GrowthAdvisorBranchResponse = { branch: "advisor", state: "result", assistant_result: { output_label: "Независимый анализ", recommendation: "Проверь первый шаг", rationale: ["Только явный ввод"] }, error: null, provenance: {} };
    const learning: api.GrowthLearningResult = { contract_version: "growth-learning-v1", status: "candidate", candidate, no_candidate_code: null };
    vi.spyOn(api, "previewGrowthAdvisor").mockResolvedValue(preview);
    const executeSpy = vi.spyOn(api, "executeGrowthAdvisor").mockResolvedValue(branch);
    vi.spyOn(api, "requestGrowthLearningQuestion").mockResolvedValue(learning);
    const resolveSpy = vi.spyOn(api, "resolveGrowthLearningQuestion").mockResolvedValue({ candidate_id: candidate.candidate_id, disposition: "ignore", answer_draft: null, handoff: null });
    const host = await renderSurface();
    await build(host);

    await act(async () => setValue(host.querySelector<HTMLTextAreaElement>("#growth-advisor-task")!, "Сравнить следующий шаг"));
    await act(async () => button(host, "Показать предпросмотр цели").click());
    expect(api.previewGrowthAdvisor).toHaveBeenCalledOnce();
    expect(host.textContent).toContain("Точная цель, которая будет передана советнику");
    const advisorPanel = host.querySelector<HTMLElement>("[aria-labelledby='growth-advisor-title']")!;
    await act(async () => advisorPanel.querySelector<HTMLInputElement>("input[type='checkbox']")!.click());
    await act(async () => button(advisorPanel, "Выполнить независимый анализ").click());
    expect(executeSpy).toHaveBeenCalledOnce();
    expect(host.textContent).toContain("Проверь первый шаг");

    await act(async () => button(host, "Уточнить развитие").click());
    expect(resolveSpy).not.toHaveBeenCalled();
    await act(async () => {
      await vi.waitFor(() => expect(button(host, "Игнорировать").disabled).toBe(false));
    });
    await act(async () => {
      button(host, "Игнорировать").click();
      await Promise.resolve();
    });
    expect(resolveSpy).toHaveBeenCalledWith(expect.objectContaining({ goal_source_uuid: goalUuid }), candidate, "ignore", null, undefined, expect.any(AbortSignal));
    expect(host.textContent).toContain("Вопрос проигнорирован без записи");
  });
});
