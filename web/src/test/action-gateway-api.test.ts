import { describe, expect, it, vi } from "vitest";

import {
  loadActionGatewayStatus,
  prepareActionGatewayAction,
  type ActionGatewayIntent,
  type ActionGatewayStatusResponse,
} from "../api";

const status: ActionGatewayStatusResponse = {
  contract: "action-gateway-v1",
  connector: "github_issues",
  policy_id: "github-issues-v1",
  credential_profile_id: "github-actions-primary",
  status: "ready",
  configured: true,
  ready: true,
  repositories: ["MikeMoore1337/second-brain"],
  store_status: "ready",
  action_catalog: [],
  owner_confirmation_required: true,
  background_execution: false,
};

const intent: ActionGatewayIntent = {
  contract_version: "action-intent-v1",
  operation_id: "browser-operation-1",
  action_kind: "github.issue.create",
  connector: "github_issues",
  repository: "MikeMoore1337/second-brain",
  title: "Точная задача",
  body: "Точное содержимое",
};

function jsonResponse(body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: { "Content-Type": "application/json" },
  });
}

describe("action gateway browser transport", () => {
  it("keeps owner credentials and action responses out of browser caches", async () => {
    const fetcher = vi.fn(async (_input: RequestInfo | URL, init?: RequestInit) => {
      expect(init).toMatchObject({
        method: "POST",
        credentials: "same-origin",
        cache: "no-store",
        body: "{}",
      });
      expect(init?.headers).toMatchObject({
        Accept: "application/json",
        "Content-Type": "application/json",
        "X-Second-Brain-Request": "action-gateway-v1",
      });
      return jsonResponse(status);
    });

    await expect(loadActionGatewayStatus(fetcher)).resolves.toEqual(status);
  });

  it("sends only the typed intent envelope and the action purpose", async () => {
    const fetcher = vi.fn(async (_input: RequestInfo | URL, init?: RequestInit) => {
      expect(init).toMatchObject({
        method: "POST",
        credentials: "same-origin",
        cache: "no-store",
        body: JSON.stringify({ intent }),
      });
      expect(init?.headers).toMatchObject({ "X-Second-Brain-Request": "action-gateway-v1" });
      return jsonResponse({ prepared: {}, confirmation_token: "page-memory-only" });
    });

    const response = await prepareActionGatewayAction(intent, fetcher);

    expect(response.confirmation_token).toBe("page-memory-only");
    expect(fetcher).toHaveBeenCalledTimes(1);
  });
});
