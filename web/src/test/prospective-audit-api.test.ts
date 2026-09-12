import { describe, expect, it, vi } from "vitest";

import {
  executeProspectiveAudit,
  loadProspectiveAuditPending,
  ProspectiveAuditApiError,
} from "../prospective-audit-api";

function jsonResponse(payload: unknown, status = 200): Response {
  return new Response(JSON.stringify(payload), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

const event = {
  event_id: "0190f4c0-1234-7123-8123-1234567890ab",
  created_at: "2026-09-13T10:00:00.000Z",
  kind: "prediction" as const,
  options: [{ id: "a", ordinal: 0, label: "Первый вариант" }],
  predicted_option_id: "a",
  predicted_option_label: "Первый вариант",
  abstention_code: null,
  derivation_version: "simulate-me-v1",
  policy_id: "simulate-me-direct-exact-v1",
};

describe("Prospective Audit API client", () => {
  it("sends the explicit execute contract and accepts only the bounded event projection", async () => {
    const fetcher = vi.fn().mockResolvedValue(jsonResponse({ event }));

    await expect(executeProspectiveAudit("operation-1", "Что выбрать?", [{ id: "a", label: "Первый вариант" }], fetcher)).resolves.toEqual(event);

    expect(fetcher).toHaveBeenCalledOnce();
    const [path, init] = fetcher.mock.calls[0] as [string, RequestInit];
    expect(path).toBe("/api/prospective-audit/execute");
    expect(init.method).toBe("POST");
    expect(init.body).toBe(JSON.stringify({ operation_id: "operation-1", query: "Что выбрать?", options: [{ id: "a", label: "Первый вариант" }] }));
    expect((init.headers as Record<string, string>)["X-Second-Brain-Request"]).toBe("prospective-audit-v1");
  });

  it("uses an empty POST for pending reads and rejects malformed server data", async () => {
    const pendingFetcher = vi.fn().mockResolvedValue(jsonResponse({
      events: [],
      decision_journals: [],
      limits: { max_events: 32, max_decision_journals: 32 },
    }));
    await expect(loadProspectiveAuditPending(pendingFetcher)).resolves.toMatchObject({ events: [], decision_journals: [] });
    const [, pendingInit] = pendingFetcher.mock.calls[0] as [string, RequestInit];
    expect(pendingInit.body).toBe("{}");

    const malformed = vi.fn().mockResolvedValue(jsonResponse({ event: { ...event, raw_query: "must never cross the boundary" } }));
    await expect(executeProspectiveAudit("operation-2", "q", [{ id: "a", label: "A" }], malformed))
      .rejects.toMatchObject<Partial<ProspectiveAuditApiError>>({ code: "PROSPECTIVE_AUDIT_INVALID_RESPONSE" });
  });
});
