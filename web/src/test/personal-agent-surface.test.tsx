import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, describe, expect, it, vi } from "vitest";

import * as api from "../personal-agent-api";
import { PersonalAgentSurface } from "../personal-agent-surface";

let root: Root | undefined;

afterEach(() => {
  act(() => root?.unmount());
  root = undefined;
  document.body.innerHTML = "";
  vi.restoreAllMocks();
});

const plan = {
  plan_id: "0199f6c0-0000-7000-8000-000000000002",
  revision: 3,
  policy_id: "stage17-personal-planning-v1",
  policy_fingerprint: "b".repeat(64),
  plan_fingerprint: "c".repeat(64),
  start_local: "2026-09-17",
  end_local: "2026-09-23",
  timezone: "Europe/Moscow",
  items: [{
    item_id: "item-1",
    kind: "next_action",
    title: "Проверить следующий шаг",
    description: "Ограниченный шаг.",
    goal_refs: [],
    action_refs: [],
    effort_minutes: 30,
  }],
  selected_item_ids: ["item-1"],
  item_order: ["item-1"],
};

const state: api.AgentStateResponse = {
  web_contract: "personal_agent_state_web_v1",
  current_plan: plan,
  execution_items: [{
    item_id: "item-1",
    projection: {
      item_id: "item-1",
      accepted_item_fingerprint: "d".repeat(64),
      item_kind: "next_action",
      source_status: "current",
      state: "not_started",
      current_block_reasons: [],
      caveats: [],
    },
  }],
  stage19: {
    status: "ready",
    configured: true,
    repositories: ["MikeMoore1337/second-brain"],
    action_catalog: [{ action_kind: "github.issue.create", risk: "controlled_write", reversibility: "compensation_only" }],
    owner_confirmation_required: true,
    background_execution: false,
  },
  current_run: null,
  run_history: [],
  caveats: [],
};

const providerPreview: api.AgentProviderPreview = {
  mission_fingerprint: "e".repeat(64),
  context_pack_fingerprint: "f".repeat(64),
  canonical_json: '{"task":"Проверить"}',
  canonical_bytes_sha256: "1".repeat(64),
  assistant_envelope: {},
};

const context: api.AgentContextResponse = {
  web_contract: "personal_agent_context_web_v1",
  context_pack: { pack_fingerprint: providerPreview.context_pack_fingerprint, selected_items: [] },
  provider_preview: providerPreview,
  current_plan: plan,
};

const proposal: api.AgentProposal = {
  contract_version: "personal-agent-run-proposal-v1",
  proposal_id: "0199f6c0-0000-7000-8000-000000000003",
  mission_fingerprint: providerPreview.mission_fingerprint,
  context_pack_fingerprint: providerPreview.context_pack_fingerprint,
  steps: [{ step_id: "checkpoint-1", position: 1, kind: "checkpoint", summary: "Проверь контекст." }],
  caveats: [],
  provider_policy_id: "personal-agent-v1",
  provider_policy_fingerprint: "2".repeat(64),
  provider_fingerprint: "3".repeat(64),
  proposal_fingerprint: "4".repeat(64),
};

function setValue(element: HTMLTextAreaElement, value: string): void {
  const setter = Object.getOwnPropertyDescriptor(element.constructor.prototype, "value")?.set;
  setter?.call(element, value);
  element.dispatchEvent(new Event("input", { bubbles: true }));
}

async function renderSurface(): Promise<HTMLDivElement> {
  const host = document.createElement("div");
  document.body.append(host);
  root = createRoot(host);
  await act(async () => root?.render(<PersonalAgentSurface />));
  return host;
}

describe("Русская поверхность агента", () => {
  it("сначала показывает точный preview, а Advisor вызывается только отдельной кнопкой", async () => {
    const contextCall = vi.spyOn(api, "buildPersonalAgentContext").mockResolvedValue(context);
    const buildCall = vi.spyOn(api, "buildPersonalAgentRun").mockResolvedValue({ web_contract: "personal_agent_build_web_v1", context_pack: context.context_pack, provider_preview: providerPreview, proposal });
    vi.spyOn(api, "loadPersonalAgentState").mockResolvedValue(state);
    const host = await renderSurface();

    expect(host.textContent).toContain("Проверить следующий шаг");
    const mission = host.querySelector<HTMLTextAreaElement>(".personal-agent-mission textarea");
    expect(mission).not.toBeNull();
    if (!mission) return;
    await act(async () => setValue(mission, "Провести проверку следующего шага"));
    const button = (label: string) => Array.from(host.querySelectorAll<HTMLButtonElement>("button")).find((item) => item.textContent?.includes(label));

    await act(async () => button("Собрать точный контекст")?.click());
    expect(contextCall).toHaveBeenCalledOnce();
    expect(buildCall).not.toHaveBeenCalled();
    expect(host.textContent).toContain("Показать точный предпросмотр для провайдера");
    expect(host.textContent).toContain(providerPreview.canonical_json);

    await act(async () => button("Построить предложение операции")?.click());
    expect(buildCall).toHaveBeenCalledOnce();
    expect(host.textContent).toContain("Проверь линейную операцию");
    expect(host.textContent).toContain("Проверь контекст.");
  });

  it("не использует браузерное хранилище как источник состояния", async () => {
    const getItem = vi.spyOn(Storage.prototype, "getItem");
    vi.spyOn(api, "loadPersonalAgentState").mockResolvedValue(state);
    await renderSurface();
    expect(getItem).not.toHaveBeenCalled();
  });
});
