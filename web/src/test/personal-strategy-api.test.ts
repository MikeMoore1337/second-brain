import { describe, expect, it, vi } from "vitest";

import {
  acceptPersonalStrategy,
  buildPersonalStrategyContext,
  generatePersonalStrategy,
  loadPersonalStrategyState,
} from "../personal-strategy-api";
import type {
  StrategyContextPack,
  StrategyProviderPreview,
  StrategyProposal,
  StrategySnapshot,
} from "../personal-strategy-api";
import type { FetchLike } from "../api";

function ok(payload: unknown): Response {
  return new Response(JSON.stringify(payload), {
    status: 200,
    headers: { "Content-Type": "application/json" },
  });
}

const pack = {} as StrategyContextPack;
const preview = { canonical_json: "{\"exact\":true}" } as StrategyProviderPreview;
const proposal = {} as StrategyProposal;

describe("Personal Strategy API boundary", () => {
  it("keeps owner credentials and private responses out of browser caches", async () => {
    const fetcher = vi.fn<FetchLike>().mockResolvedValue(ok({}));

    await loadPersonalStrategyState(fetcher);

    expect(fetcher).toHaveBeenCalledOnce();
    expect(fetcher.mock.calls[0][1]).toMatchObject({
      method: "POST",
      credentials: "same-origin",
      cache: "no-store",
      headers: {
        Accept: "application/json",
        "Content-Type": "application/json",
        "X-Second-Brain-Request": "executive-strategy-v1",
      },
      body: "{}",
    });
  });

  it("sends the exact context and provider preview only on explicit operations", async () => {
    const fetcher = vi.fn<FetchLike>().mockResolvedValue(ok({}));
    const request = {
      goal_source_uuid: "0198f4c5-6a00-7000-8000-000000001200",
      goal_identity_fingerprint: "sha256:" + "a".repeat(64),
      task: "Проверить цель",
      constraints: ["Без автоисполнения"],
      current_context: "Текущий контекст",
    };

    await buildPersonalStrategyContext(request, fetcher);
    await generatePersonalStrategy(pack, preview, fetcher);

    expect(fetcher.mock.calls[0][0]).toBe("/api/personal-strategy/context");
    expect(fetcher.mock.calls[0][1]?.body).toBe(JSON.stringify(request));
    expect(fetcher.mock.calls[1][0]).toBe("/api/personal-strategy/generate");
    expect(fetcher.mock.calls[1][1]?.body).toBe(JSON.stringify({
      context_pack: pack,
      provider_preview: preview.canonical_json,
    }));
  });

  it("binds accept to the exact prior snapshot and forwards cancellation", async () => {
    const fetcher = vi.fn<FetchLike>().mockResolvedValue(ok({}));
    const controller = new AbortController();
    const snapshot = {
      snapshot_id: "0198f4c5-6a00-7000-8000-000000001201",
      snapshot_fingerprint: "b".repeat(64),
    } as StrategySnapshot;

    await acceptPersonalStrategy(pack, proposal, [], "operation-1", snapshot, fetcher, controller.signal);

    expect(fetcher.mock.calls[0][0]).toBe("/api/personal-strategy/accept");
    expect(fetcher.mock.calls[0][1]).toMatchObject({
      signal: controller.signal,
      body: JSON.stringify({
        context_pack: pack,
        proposal,
        selected_actions: [],
        operation_id: "operation-1",
        expected_prior_snapshot_id: snapshot.snapshot_id,
        expected_prior_snapshot_fingerprint: snapshot.snapshot_fingerprint,
      }),
    });
  });
});
