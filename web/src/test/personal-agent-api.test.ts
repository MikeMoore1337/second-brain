import { describe, expect, it, vi } from "vitest";

import {
  buildPersonalAgentContext,
  loadPersonalAgentState,
  type AgentMission,
  type AgentStateResponse,
} from "../personal-agent-api";

const mission: AgentMission = {
  contract_version: "personal-agent-mission-v1",
  mission_id: "0199f6c0-0000-7000-8000-000000000001",
  planning_snapshot_id: "0199f6c0-0000-7000-8000-000000000002",
  planning_snapshot_fingerprint: "a".repeat(64),
  planning_policy_id: "stage17-personal-planning-v1",
  planning_policy_fingerprint: "b".repeat(64),
  selected_items: [],
  task: "Проверить выбранный шаг",
  constraints: [],
  current_context: [],
  external_targets: [],
  created_at: "2026-09-17T10:00:00Z",
  reviewed_at: "2026-09-17T10:00:00Z",
};

const state = { web_contract: "personal_agent_state_web_v1" } as AgentStateResponse;

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

describe("transport агента", () => {
  it("uses a private no-store POST boundary and the exact purpose", async () => {
    const fetcher = vi.fn(async (_input: RequestInfo | URL, init?: RequestInit) => {
      expect(init).toMatchObject({ method: "POST", credentials: "same-origin", cache: "no-store", body: "{}" });
      expect(init?.headers).toMatchObject({
        Accept: "application/json",
        "Content-Type": "application/json",
        "X-Second-Brain-Request": "personal-agent-v1",
      });
      return jsonResponse(state);
    });

    await expect(loadPersonalAgentState(fetcher)).resolves.toEqual(state);
  });

  it("keeps mission in the request body and reports a Russian server error", async () => {
    const fetcher = vi.fn(async (_input: RequestInfo | URL, init?: RequestInit) => {
      expect(init?.body).toBe(JSON.stringify({ mission }));
      return jsonResponse({ error: { code: "SOURCE_UNAVAILABLE", message: "Источник недоступен." } }, 503);
    });

    await expect(buildPersonalAgentContext(mission, fetcher)).rejects.toThrow("Источник недоступен.");
    expect(fetcher).toHaveBeenCalledOnce();
  });
});
