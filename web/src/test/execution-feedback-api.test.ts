import { describe, expect, it, vi } from "vitest";

import * as executionApi from "../execution-feedback-api";

function response(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });
}

describe("Execution Feedback API client", () => {
  it("uses the private cacheless boundary and sends an empty state body", async () => {
    const fetcher = vi.fn().mockResolvedValue(response({ current_plan: null, report: null, available_snapshots: [], caveats: [] }));

    await executionApi.loadExecutionFeedbackState(fetcher);

    expect(fetcher).toHaveBeenCalledOnce();
    const [path, init] = fetcher.mock.calls[0] ?? [];
    expect(path).toBe("/api/execution-feedback/state");
    expect(init).toMatchObject({ method: "POST", credentials: "same-origin", cache: "no-store", body: "{}" });
    expect(new Headers(init?.headers).get("x-second-brain-request")).toBe("execution-feedback-v1");
    expect(new Headers(init?.headers).get("content-type")).toBe("application/json");
  });

  it("keeps exact event identity and explicit effort in the request", async () => {
    const fetcher = vi.fn().mockResolvedValue(response({ status: "recorded" }));
    const payload: executionApi.ExecutionFeedbackEventPayload = {
      planning_snapshot_id: "0198f4c5-6a00-7000-0000-000000000004",
      planning_snapshot_fingerprint: "a".repeat(64),
      item_id: "next-1",
      accepted_item_fingerprint: "b".repeat(64),
      operation_id: "operation-1",
      event_type: "complete",
      occurred_at: "2026-09-16T12:30:00+03:00",
      actual_effort_minutes: 42,
      effort_precision: "exact",
      actual_result_note: "Готово",
      reason_codes: [],
      deviation_codes: ["estimate_mismatch"],
      result_disposition: "with_changes",
    };

    await executionApi.recordExecutionFeedbackEvent(payload, fetcher);

    const init = fetcher.mock.calls[0]?.[1];
    expect(JSON.parse(String(init?.body))).toEqual(payload);
    expect(init).toMatchObject({ method: "POST", credentials: "same-origin", cache: "no-store" });
  });

  it("returns the safe Russian server error without inventing a success", async () => {
    const fetcher = vi.fn().mockResolvedValue(response({ error: { code: "EXECUTION_FEEDBACK_STALE_PLAN", message: "План устарел." } }, 409));

    await expect(executionApi.loadExecutionFeedback("plan", "a".repeat(64), fetcher)).rejects.toMatchObject({ message: "План устарел.", status: 409 });
  });
});
