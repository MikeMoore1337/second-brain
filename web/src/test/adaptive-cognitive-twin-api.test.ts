import { describe, expect, it, vi } from "vitest";

import {
  buildAdaptiveCandidate,
  loadAdaptiveState,
} from "../adaptive-cognitive-twin-api";
import type { FetchLike } from "../api";

const selection = {
  goal_source_uuid: "0198f4c5-6a00-7000-8000-000000001200",
  goal_identity_fingerprint: "sha256:" + "a".repeat(64),
  as_of: "2026-09-16T12:00:00Z",
};

function ok(payload: unknown): Response {
  return new Response(JSON.stringify(payload), {
    status: 200,
    headers: { "Content-Type": "application/json" },
  });
}

function failure(payload: unknown, status = 503): Response {
  return new Response(JSON.stringify(payload), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

describe("Adaptive Cognitive Twin API boundary", () => {
  it("keeps the owner-only request contract and parses lifecycle metadata", async () => {
    const fetcher = vi.fn<FetchLike>().mockResolvedValue(ok({
      web_contract: "adaptive_cognitive_twin_web_v1",
      as_of: selection.as_of,
      goals: [],
      selected_goal: null,
      stage14_experiment_selectors: [],
      projection: null,
      profile_history: [],
      reviewed_candidate_fingerprints: ["sha256:" + "b".repeat(64)],
      rejected_candidate_fingerprints: [],
      non_causal_phrase: "Наблюдаемое изменение не является доказательством причинности.",
    }));

    const result = await loadAdaptiveState(selection, fetcher);

    expect(result.reviewed_candidate_fingerprints).toHaveLength(1);
    expect(fetcher.mock.calls[0][0]).toBe("/api/adaptive-cognitive-twin/state");
    expect(fetcher.mock.calls[0][1]).toMatchObject({
      method: "POST",
      credentials: "same-origin",
      cache: "no-store",
      headers: {
        Accept: "application/json",
        "Content-Type": "application/json",
        "X-Second-Brain-Request": "adaptive-cognitive-twin-v1",
      },
      body: JSON.stringify(selection),
    });
  });

  it("rejects a successful response that tries to cross the selector boundary", async () => {
    const fetcher = vi.fn<FetchLike>().mockResolvedValue(ok({
      web_contract: "adaptive_cognitive_twin_web_v1",
      projection: { Content: "private source material" },
    }));

    await expect(buildAdaptiveCandidate(selection, fetcher)).rejects.toMatchObject({
      status: 502,
    });
  });

  it("uses only the closed Russian error catalog, never arbitrary server text", async () => {
    const fetcher = vi.fn<FetchLike>().mockResolvedValue(failure({
      error: {
        code: "ADAPTIVE_COGNITIVE_TWIN_SOURCE_UNAVAILABLE",
        message: "private filesystem path: C:/secret/vault/Goal.md",
      },
    }));

    await expect(loadAdaptiveState({}, fetcher)).rejects.toMatchObject({
      status: 503,
      message: "Точный источник адаптивного слоя сейчас недоступен.",
    });
  });

  it("does not trust an unknown error code or its private message", async () => {
    const fetcher = vi.fn<FetchLike>().mockResolvedValue(failure({
      error: {
        code: "PRIVATE_INTERNAL_ERROR",
        message: "secret source body",
      },
    }, 500));

    await expect(loadAdaptiveState({}, fetcher)).rejects.toMatchObject({
      status: 500,
      message: "Не удалось загрузить адаптивный профиль.",
    });
  });
});
