import { describe, expect, it, vi } from "vitest";

import { searchNotes, type FetchLike } from "../api";

describe("same-origin API seam", () => {
  it("uses the existing Search request contract", async () => {
    const fetcher = vi.fn<FetchLike>().mockResolvedValue(
      new Response(JSON.stringify({ hits: [] }), {
        headers: { "Content-Type": "application/json" },
        status: 200,
      }),
    );

    await searchNotes("текущий контекст", fetcher);

    expect(fetcher).toHaveBeenCalledWith("/api/search", {
      body: JSON.stringify({ query: "текущий контекст", limit: 20 }),
      headers: {
        Accept: "application/json",
        "Content-Type": "application/json",
        "X-Second-Brain-Request": "search-v1",
      },
      method: "POST",
    });
  });
});
