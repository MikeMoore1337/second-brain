import { describe, expect, it, vi } from "vitest";

import {
  applyDecision,
  applySave,
  createTextDraft,
  loadSelfRetrieval,
  loadDiagnostics,
  prepareDecision,
  prepareSave,
  searchNotes,
  retrieveNote,
  transcribeAudio,
  type FetchLike,
} from "../api";

const draft = {
  title: "Title",
  note_type: "resource",
  content: "# Content",
  tags: ["one"],
  links: ["[[related]]"],
};

function ok(payload: unknown): Response {
  return new Response(JSON.stringify(payload), { status: 200, headers: { "Content-Type": "application/json" } });
}

describe("same-origin API seam", () => {
  it("unwraps the canonical retrieval response before rendering a note", async () => {
    const note = { id: "0198f4c5-6a00-7000-8000-000000000010", type: "resource", relative_path: "30 Resources/Идеи.md", title: "Идеи", content: "Проверенное содержание" };
    const fetcher = vi.fn<FetchLike>().mockResolvedValue(ok({ note }));
    expect(await retrieveNote(note.id, fetcher)).toEqual(note);
    expect(fetcher.mock.calls[0][0]).toBe("/api/retrieval/note");
    expect(fetcher.mock.calls[0][1]?.body).toBe(JSON.stringify({ id: note.id }));
  });
  it("uses the explicit diagnostics refresh contract", async () => {
    const fetcher = vi.fn<FetchLike>().mockResolvedValue(ok({ status: "healthy" }));

    await loadDiagnostics(fetcher);

    expect(fetcher).toHaveBeenCalledWith("/api/diagnostics", {
      body: JSON.stringify({}),
      headers: {
        Accept: "application/json",
        "Content-Type": "application/json",
        "X-Second-Brain-Request": "diagnostics-v1",
      },
      method: "POST",
    });
  });

  it("uses the existing Search request contract", async () => {
    const fetcher = vi.fn<FetchLike>().mockResolvedValue(ok({ hits: [] }));

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

  it("keeps capture, voice and Self Retrieval purpose headers and payload limits exact", async () => {
    const fetcher = vi.fn<FetchLike>()
      .mockResolvedValueOnce(ok({ review_token: "review", draft, sources: [] }))
      .mockResolvedValueOnce(ok({ transcript: { text: "transcript" } }))
      .mockResolvedValueOnce(ok({ items: [], exclusions: [], candidate_count: 0, included_count: 0, excluded_count: 0 }));

    await createTextDraft("raw text", fetcher);
    await transcribeAudio(new Blob(["audio"], { type: "audio/webm" }), "audio/webm", fetcher);
    await loadSelfRetrieval("fastapi", fetcher);

    expect(fetcher.mock.calls[0]).toEqual(["/api/drafts/text", {
      method: "POST",
      headers: { Accept: "application/json", "Content-Type": "application/json", "X-Second-Brain-Request": "draft-v1" },
      body: JSON.stringify({ text: "raw text" }),
    }]);
    expect(fetcher.mock.calls[1]).toEqual(["/api/transcriptions/audio", {
      method: "POST",
      headers: { Accept: "application/json", "Content-Type": "audio/webm", "X-Second-Brain-Request": "voice-v1" },
      body: expect.any(Blob),
    }]);
    expect(fetcher.mock.calls[2]).toEqual(["/api/self-retrieval", {
      method: "POST",
      headers: { Accept: "application/json", "Content-Type": "application/json", "X-Second-Brain-Request": "self-retrieval-v1" },
      body: JSON.stringify({ query: "fastapi", limit: 20, max_content_bytes: 65536 }),
    }]);
  });

  it("keeps generic Save as a token-bound prepare/apply pair", async () => {
    const fetcher = vi.fn<FetchLike>()
      .mockResolvedValueOnce(ok({ status: "dry-run", confirmation_token: "confirm", note: { id: "id" }, diff: "diff" }))
      .mockResolvedValueOnce(ok({ status: "created", note: { id: "id" } }));

    await prepareSave("review", draft, fetcher);
    await applySave("review", "confirm", draft, fetcher);

    expect(fetcher.mock.calls[0][0]).toBe("/api/drafts/save/prepare");
    expect(JSON.parse(String(fetcher.mock.calls[0][1]?.body))).toEqual({ review_token: "review", draft });
    expect(fetcher.mock.calls[1][0]).toBe("/api/drafts/save/apply");
    expect(JSON.parse(String(fetcher.mock.calls[1][1]?.body))).toEqual({ review_token: "review", confirmation_token: "confirm", draft });
  });

  it("keeps Decision Journal and Outcome on the existing draft-v1 two-phase routes", async () => {
    const decision = {
      title: "Decision",
      note_type: "project",
      tags: [],
      links: [],
      evidence_at: "unknown",
      evidence_at_precision: "unknown" as const,
      domain: null,
      situation: "Situation",
      available_options: ["A", "B"],
      information_known_at_decision_time: "Known",
      criteria: ["Criterion"],
      chosen_option: "A",
      reasons: "Reasons",
      confidence: "Confidence",
      expected_result: "Expected",
    };
    const fetcher = vi.fn<FetchLike>()
      .mockResolvedValueOnce(ok({ status: "dry-run", confirmation_token: "confirm", note: { id: "id" }, diff: "diff" }))
      .mockResolvedValueOnce(ok({ status: "created", note: { id: "id" } }));

    await prepareDecision(decision, fetcher);
    await applyDecision("confirm", decision, fetcher);

    expect(fetcher.mock.calls[0][0]).toBe("/api/drafts/decision-journal/save/prepare");
    expect(fetcher.mock.calls[1][0]).toBe("/api/drafts/decision-journal/save/apply");
    expect(fetcher.mock.calls[0][1]?.headers).toEqual({ Accept: "application/json", "Content-Type": "application/json", "X-Second-Brain-Request": "draft-v1" });
  });
});
