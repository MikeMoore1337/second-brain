import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, describe, expect, it, vi } from "vitest";

import { PersonalPlanningSurface } from "../personal-planning-surface";
import type { PlanningContextResponse, PlanningGenerateResponse, PlanningStateResponse } from "../personal-planning-api";

let root: Root | undefined;

afterEach(() => {
  act(() => root?.unmount());
  root = undefined;
  vi.restoreAllMocks();
  document.body.innerHTML = "";
});

function goalState(): PlanningStateResponse {
  return {
    web_contract: "personal_planning_state_web_v1",
    goals: [{
      goal_source_uuid: "0198f4c5-6a00-7000-8000-000000000001",
      goal_identity_fingerprint: "sha256:" + "1".repeat(64),
      goal_text: "Улучшить выносливость",
      goal: {},
      strategy_snapshot: {
        snapshot_id: "0198f4c5-6a00-7000-8000-000000000002",
        snapshot_fingerprint: "a".repeat(64),
        sequence: 1,
        selected_actions: [{ action_id: "action-1", kind: "act", generated: {}, reviewed: {}, edited: false }],
      },
    }],
    eligible_goal_count: 1,
    generated_at: "2026-09-16T06:00:00Z",
    current_plan: null,
    caveats: [],
  };
}

function contextResponse(): PlanningContextResponse {
  return {
    web_contract: "personal_planning_context_web_v1",
    context_pack: {
      portfolio_order: ["0198f4c5-6a00-7000-8000-000000000001"],
      start_local: "2026-09-16",
      end_local: "2026-09-18",
      timezone: "UTC",
      capacity: [
        { date: "2026-09-16", available_minutes: 60 },
        { date: "2026-09-17", available_minutes: 60 },
        { date: "2026-09-18", available_minutes: 60 },
      ],
      fixed_windows: [],
      planning_constraints: [],
      planning_context: "",
      readiness: "exact_current",
      pack_caveats: [],
      pack_fingerprint: "b".repeat(64),
    },
    provider_preview: {
      source_pack_fingerprint: "b".repeat(64),
      canonical_json: '{"planning":"preview"}',
      canonical_bytes_sha256: "c".repeat(64),
      assistant_envelope: {},
    },
    current_plan: null,
  };
}

function generateResponse(): PlanningGenerateResponse {
  return {
    ...contextResponse(),
    web_contract: "personal_planning_generate_web_v1",
    proposal: {
      proposal_id: "0198f4c5-6a00-7000-8000-000000000003",
      result_state: "proposal",
      as_of: "2026-09-16T06:00:00Z",
      source_pack_fingerprint: "b".repeat(64),
      provider_envelope_fingerprint: "c".repeat(64),
      provider_result_fingerprint: "d".repeat(64),
      policy_id: "stage17-personal-planning-v1",
      policy_fingerprint: "e".repeat(64),
      items: [{
        item_id: "next-1",
        kind: "next_action",
        title: "Сделать небольшой шаг",
        description: "Выполнить выбранное действие.",
        goal_refs: [{ goal_source_uuid: "0198f4c5-6a00-7000-8000-000000000001" }],
        action_refs: [{ reviewed_action_id: "action-1" }],
        parent_item_id: null,
        target_start_local: null,
        target_end_local: null,
        effort_minutes: 30,
        effort_source: "provider_proposed",
        dependency_ids: [],
      }],
      suggested_order: ["next-1"],
      reasons: ["Связь с целью сохранена."],
      caveats: [],
      proposal_fingerprint: "f".repeat(64),
    },
  };
}

async function renderSurface(): Promise<HTMLDivElement> {
  const host = document.createElement("div");
  document.body.append(host);
  root = createRoot(host);
  await act(async () => {
    root?.render(<PersonalPlanningSurface />);
  });
  return host;
}

describe("Русская поверхность личного планирования", () => {
  it("не загружает и не вызывает провайдера до явного действия владельца", async () => {
    const calls: string[] = [];
    vi.spyOn(window, "fetch").mockImplementation((input) => {
      const path = String(input);
      calls.push(path);
      if (path.endsWith("/state")) return Promise.resolve(new Response(JSON.stringify(goalState()), { status: 200 }));
      if (path.endsWith("/context")) return Promise.resolve(new Response(JSON.stringify(contextResponse()), { status: 200 }));
      return Promise.resolve(new Response(JSON.stringify(generateResponse()), { status: 200 }));
    });
    const host = await renderSurface();

    expect(calls).toEqual([]);
    expect(host.textContent).toContain("Личное планирование");
    const load = Array.from(host.querySelectorAll<HTMLButtonElement>("button")).find((button) => button.textContent?.includes("Загрузить состояние"));
    expect(load).not.toBeUndefined();
    await act(async () => load?.click());
    expect(calls).toEqual(["/api/personal-planning/state"]);
    expect(host.textContent).toContain("Улучшить выносливость");
  });

  it("показывает предпросмотр и только отдельной кнопкой запрашивает предложение", async () => {
    vi.spyOn(window, "fetch").mockImplementation((input) => {
      const path = String(input);
      if (path.endsWith("/state")) return Promise.resolve(new Response(JSON.stringify(goalState()), { status: 200 }));
      if (path.endsWith("/context")) return Promise.resolve(new Response(JSON.stringify(contextResponse()), { status: 200 }));
      return Promise.resolve(new Response(JSON.stringify(generateResponse()), { status: 200 }));
    });
    const host = await renderSurface();
    const button = (label: string) => Array.from(host.querySelectorAll<HTMLButtonElement>("button")).find((item) => item.textContent?.includes(label));

    await act(async () => button("Загрузить состояние")?.click());
    const checkbox = host.querySelector<HTMLInputElement>(".personal-planning-goal input");
    await act(async () => checkbox?.click());
    await act(async () => button("Собрать контекстный пакет")?.click());
    expect(host.textContent).toContain("Точный предпросмотр");
    expect(host.textContent).toContain('{"planning":"preview"}');
    expect(host.textContent).not.toContain("Сделать небольшой шаг");

    await act(async () => button("Получить предложение")?.click());
    expect(host.textContent).toContain("Предложение для проверки");
    expect(host.textContent).toContain("Сделать небольшой шаг");
    expect(host.textContent).toContain("Ничего не исполняется автоматически");
  });
});
