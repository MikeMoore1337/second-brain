import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, describe, expect, it, vi } from "vitest";

import { PersonalExperimentsSurface } from "../personal-experiments-surface";

let root: Root | undefined;

afterEach(() => {
  act(() => root?.unmount());
  root = undefined;
  vi.restoreAllMocks();
  document.body.innerHTML = "";
});

const goalId = "0198f4c5-6a00-7000-8000-000000000301";
const definitionId = "0198f4c5-6a00-7000-8000-000000000302";
const observationId = "0198f4c5-6a00-7000-8000-000000000303";
const experimentId = "0198f4c5-6a00-7000-8000-000000000304";
const hash = `sha256:${"1".repeat(64)}`;

const goal = {
  source_note_uuid: goalId,
  goal_text: "Завершить важный проект",
  domain: "work",
  goal_identity_fingerprint: hash,
};

const stage12Definition = {
  id: definitionId,
  goal_source_uuid: goalId,
  goal_identity_fingerprint: hash,
  definition_fingerprint: hash,
  goal_progress_policy_fingerprint: hash,
  definition_reviewed_at: "2026-09-14T10:00:00Z",
  progress_model: "numeric_target" as const,
  metric_id: "completed_tasks",
  unit: "tasks",
  baseline: "2",
  target: "8",
  direction: "increase_to",
  active: true,
};

const stage12Observation = {
  id: observationId,
  goal_source_uuid: goalId,
  goal_identity_fingerprint: hash,
  progress_definition_id: definitionId,
  definition_fingerprint: hash,
  observation_fingerprint: hash,
  observed_at: "2026-09-15T11:00:00Z",
  observed_at_precision: "exact" as const,
  observation_reviewed_at: "2026-09-15T11:05:00Z",
  progress_model: "numeric_target" as const,
  value: "5",
  unit: "tasks",
  active: true,
  eligible: true,
};

const baseState = {
  web_contract: "personal_experiments_web_v1" as const,
  contract_id: "personal-experiments-v1" as const,
  derivation_id: "personal-experiment-derivation-v1",
  policy_id: "personal-experiment-v1",
  policy_fingerprint: hash,
  generated_at: "2026-09-15T12:00:00Z",
  goals: [goal],
  stage12_definitions: [stage12Definition],
  stage12_observations: [stage12Observation],
  experiments: [],
  caveats: ["observed_change_is_not_proof_of_causation", "no_automatic_adaptation"],
};

const definition = {
  id: experimentId,
  personal_experiment_kind: "definition" as const,
  goal_source_uuid: goalId,
  goal_identity_fingerprint: hash,
  goal_progress_definition_id: definitionId,
  goal_progress_definition_fingerprint: hash,
  goal_progress_policy_fingerprint: hash,
      hypothesis: "Две короткие сессии помогут завершать больше задач.",
  intervention: "Планировать две короткие сессии каждый рабочий день.",
  baseline_strategy: "stage12_definition_explicit" as const,
  definition_reviewed_at: "2026-09-15T12:01:00Z",
  experiment_policy_id: "personal-experiment-v1",
  experiment_policy_fingerprint: hash,
  experiment_definition_fingerprint: hash,
  definition_fingerprint: hash,
  state: "active",
};

const activeExperiment = {
  definition,
  goal,
  lifecycle: {
    state: "active",
    activation: {
      id: "0198f4c5-6a00-7000-8000-000000000305",
      experiment_definition_id: experimentId,
      experiment_definition_fingerprint: hash,
      lifecycle_event: "activation",
      event_at: "2026-09-15T10:00:00Z",
      lifecycle_reviewed_at: "2026-09-15T10:01:00Z",
      lifecycle_fingerprint: hash,
    },
    terminal: null,
    issues: [],
  },
  enrollments: [],
  reassessments: [],
  eligible_stage12_observations: [stage12Observation],
};

function jsonResponse(payload: unknown, status = 200): Response {
  return new Response(JSON.stringify(payload), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

async function renderSurface(): Promise<HTMLDivElement> {
  const host = document.createElement("div");
  document.body.append(host);
  root = createRoot(host);
  await act(async () => {
    root?.render(<PersonalExperimentsSurface />);
  });
  return host;
}

function button(host: HTMLElement, label: string): HTMLButtonElement | undefined {
  return Array.from(host.querySelectorAll<HTMLButtonElement>("button")).find((item) => item.textContent?.includes(label));
}

function setValue(control: HTMLInputElement | HTMLTextAreaElement | HTMLSelectElement, value: string): void {
  const prototype = control instanceof HTMLSelectElement
    ? HTMLSelectElement.prototype
    : control instanceof HTMLTextAreaElement
      ? HTMLTextAreaElement.prototype
      : HTMLInputElement.prototype;
  Object.getOwnPropertyDescriptor(prototype, "value")?.set?.call(control, value);
  control.dispatchEvent(new Event(control instanceof HTMLSelectElement ? "change" : "input", { bubbles: true }));
}

function review(kind: "definition" | "lifecycle" | "observation" | "reassessment" = "definition") {
  return {
    web_contract: "personal_experiment_review_v1",
    status: "dry-run",
    review_token: "review-token",
    plan_sha256: hash,
    record_kind: kind,
    record_id: experimentId,
    created: "2026-09-15T12:02:00Z",
    title: "Personal experiment",
    payload: kind === "definition"
      ? { hypothesis: definition.hypothesis, intervention: definition.intervention, baseline_strategy: definition.baseline_strategy }
      : kind === "lifecycle"
        ? { lifecycle_event: "completion", event_at: "2026-09-15T12:03:00Z" }
        : { stage12_observation_id: observationId, stage12_observation_fingerprint: hash },
    record_fingerprint: hash,
    content_sha256: hash,
    bindings: {
      goal_source_uuid: goalId,
      goal_identity_fingerprint: hash,
      experiment_definition_id: experimentId,
      experiment_definition_fingerprint: hash,
      supersedes_record_id: null,
    },
  };
}

const saved = {
  web_contract: "personal_experiment_apply_v1",
  status: "saved",
  write_status: "created",
  record_kind: "definition",
  record_id: experimentId,
  plan_sha256: hash,
  rollback: "not-needed",
};

describe("Personal Experiments v1 surface", () => {
  it("stays idle and makes no request until the owner starts it", async () => {
    const fetchSpy = vi.spyOn(window, "fetch");
    const host = await renderSurface();

    expect(fetchSpy).not.toHaveBeenCalled();
    expect(host.querySelector("[data-personal-experiments-state='idle']")).not.toBeNull();
    expect(host.textContent).toContain("Личные эксперименты");
    expect(host.textContent).toContain("Изменение во время эксперимента не доказывает причинность.");
  });

  it("loads a bounded Russian list and starts creation only after explicit action", async () => {
    const fetchSpy = vi.spyOn(window, "fetch").mockResolvedValue(jsonResponse(baseState));
    const host = await renderSurface();

    await act(async () => button(host, "Загрузить эксперименты")?.click());

    expect(fetchSpy).toHaveBeenCalledOnce();
    expect(fetchSpy.mock.calls[0][0]).toBe("/api/personal-experiments");
    expect(fetchSpy.mock.calls[0][1]?.method).toBe("POST");
    expect((fetchSpy.mock.calls[0][1]?.headers as Record<string, string>)["X-Second-Brain-Request"])
      .toBe("personal-experiment-v1");
    expect(JSON.parse(String(fetchSpy.mock.calls[0][1]?.body))).toEqual({});
    expect(host.textContent).toContain("Экспериментов пока нет");

    await act(async () => button(host, "Создать эксперимент")?.click());
    expect(host.querySelector("[data-personal-experiments-review]")).toBeNull();
    expect(host.textContent).toContain("Определи эксперимент");
    expect(fetchSpy).toHaveBeenCalledOnce();
  });

  it("requires review and owner confirmation before applying a definition", async () => {
    const fetchSpy = vi.spyOn(window, "fetch")
      .mockResolvedValueOnce(jsonResponse(baseState))
      .mockResolvedValueOnce(jsonResponse(review("definition")))
      .mockResolvedValueOnce(jsonResponse(saved))
      .mockResolvedValueOnce(jsonResponse({ ...baseState, experiments: [activeExperiment] }));
    const host = await renderSurface();

    await act(async () => button(host, "Загрузить эксперименты")?.click());
    await act(async () => button(host, "Создать эксперимент")?.click());
    const selects = host.querySelectorAll<HTMLSelectElement>(".pe-editor select");
    const fields = host.querySelectorAll<HTMLTextAreaElement>(".pe-editor textarea");
    expect(selects.length).toBeGreaterThanOrEqual(2);
    expect(fields).toHaveLength(2);
    if (selects.length < 2 || fields.length !== 2) return;

    await act(async () => {
      setValue(selects[0], goalId);
      setValue(selects[1], definitionId);
      setValue(fields[0], definition.hypothesis);
      setValue(fields[1], definition.intervention);
    });
    await act(async () => button(host, "Проверить и подготовить")?.click());

    expect(fetchSpy).toHaveBeenCalledTimes(2);
    expect(fetchSpy.mock.calls[1][0]).toBe("/api/personal-experiments/definitions/prepare");
    expect(JSON.parse(String(fetchSpy.mock.calls[1][1]?.body))).toEqual({
      goal_source_uuid: goalId,
      goal_identity_fingerprint: hash,
      goal_progress_definition_id: definitionId,
      goal_progress_definition_fingerprint: hash,
      hypothesis: definition.hypothesis,
      intervention: definition.intervention,
      baseline_strategy: "stage12_definition_explicit",
      baseline_observation_uuid: null,
      baseline_observation_fingerprint: null,
      supersedes_definition_id: null,
      supersedes_definition_fingerprint: null,
    });
    expect(host.textContent).toContain("Проверь запись перед публикацией");
    expect(fetchSpy).toHaveBeenCalledTimes(2);

    const confirmation = host.querySelector<HTMLInputElement>(".pe-confirmation input");
    expect(confirmation).not.toBeNull();
    await act(async () => button(host, "Подтвердить запись")?.click());
    expect(fetchSpy).toHaveBeenCalledTimes(2);

    await act(async () => confirmation?.click());
    await act(async () => button(host, "Подтвердить запись")?.click());
    expect(fetchSpy).toHaveBeenCalledTimes(4);
    expect(fetchSpy.mock.calls[2][0]).toBe("/api/personal-experiments/definitions/apply");
    expect(JSON.parse(String(fetchSpy.mock.calls[2][1]?.body))).toEqual({
      review_token: "review-token",
      accepted_plan_sha256: hash,
      confirmed: true,
    });
    expect(host.textContent).toContain("Загружено экспериментов: 1.");
  });

  it("keeps observation enrollment and evaluation as separate explicit actions", async () => {
    const result = {
      web_contract: "personal_experiment_evaluation_web_v1",
      contract: "personal_experiment_result_v1",
      contract_id: "personal-experiments-v1",
      derivation_id: "personal-experiment-result-derivation-v1",
      experiment_definition_id: experimentId,
      experiment_definition_fingerprint: hash,
      status: "descriptive_result",
      as_of: "2026-09-15T12:10:00Z",
      result_fingerprint: hash,
      caveats: ["observed_change_is_not_proof_of_causation", "no_automatic_adaptation"],
      provenance: { source: "current_vault", provider: "none", network: "none", write: "none", as_of: "2026-09-15T12:10:00Z" },
      included_observations: [],
      excluded_observations: [],
      stage12_status: "toward_target",
    };
    const fetchSpy = vi.spyOn(window, "fetch")
      .mockResolvedValueOnce(jsonResponse({ ...baseState, experiments: [activeExperiment] }))
      .mockResolvedValueOnce(jsonResponse(review("observation")))
      .mockResolvedValueOnce(jsonResponse(result));
    const host = await renderSurface();

    await act(async () => button(host, "Загрузить эксперименты")?.click());
    expect(host.textContent).toContain("Две короткие сессии помогут завершать больше задач.");

    await act(async () => button(host, "Включить")?.click());
    expect(fetchSpy).toHaveBeenCalledTimes(2);
    expect(fetchSpy.mock.calls[1][0]).toBe("/api/personal-experiments/observations/prepare");
    expect(host.textContent).toContain("Проверь запись перед публикацией");

    await act(async () => button(host, "Построить результат сейчас")?.click());
    expect(fetchSpy).toHaveBeenCalledTimes(3);
    expect(fetchSpy.mock.calls[2][0]).toBe("/api/personal-experiments/evaluate");
    expect(host.textContent).toContain("Описательный результат");
    expect(host.textContent).toContain("Изменение во время эксперимента не доказывает причинность.");
    expect(host.textContent).toContain("current_vault");
    expect(host.textContent).toContain("провайдера и автоадаптации");
  });
});
