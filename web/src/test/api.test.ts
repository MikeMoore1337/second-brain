import { describe, expect, it, vi } from "vitest";

import {
  applyDecision,
  applySave,
  confirmGrowthMapping,
  createTextDraft,
  executeGrowthAdvisor,
  loadGrowth,
  loadGrowthGoals,
  loadGrowthMappingStatus,
  loadSelfRetrieval,
  loadDiagnostics,
  previewGrowthAdvisor,
  prepareDecision,
  prepareSave,
  requestGrowthLearningQuestion,
  resolveGrowthLearningQuestion,
  reviewGrowthMapping,
  searchNotes,
  retrieveNote,
  transcribeAudio,
  buildDecisionCompass,
  executeDecisionCompassAdvisor,
  previewDecisionCompassAdvisor,
  type FetchLike,
  type DecisionCompassRequest,
} from "../api";

const draft = {
  title: "Title",
  note_type: "resource",
  content: "# Content",
  tags: ["one"],
  links: ["[[related]]"],
};

const decisionCompassRequest: DecisionCompassRequest = {
  contract_version: "growth-compare-v1",
  task: "Выбрать следующий шаг",
  options: [{ id: "option-a", label: "Сначала прояснить задачу" }],
  selected_goal: {
    source_uuid: "0198c8a0-0000-7000-8000-000000000010",
    identity_fingerprint: "sha256:" + "a".repeat(64),
  },
  criteria: [{ id: "criterion-a", label: "Ясность", description: null }],
  explicit_constraints: [],
  explicit_context: [],
  progress_as_of: "2026-09-15T10:00:00Z",
  behavioral_scope: null,
  behavioral_option_binding: null,
  max_result_bytes: 65536,
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

  it("uses only explicit Stage 11E routes and preserves the selector/intent boundary", async () => {
    const selector = {
      source_note_uuid: "0198f4c5-6a00-7000-8000-000000000010",
      behavioral_cohort_fingerprint: "sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
      behavioral_option_index: 0,
      behavioral_option_fingerprint: "sha256:bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
    };
    const advisorRequest = {
      contract_version: "growth-advisor-v1" as const,
      goal_source_uuid: selector.source_note_uuid,
      goal_identity_fingerprint: "sha256:cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc",
      task: "Сравнить два варианта",
      options: [],
      explicit_constraints: [],
      explicit_context: [],
      max_context_bytes: 65536,
      max_result_bytes: 65536,
    };
    const preview = { ...advisorRequest, assistant_contract_version: "assistant-v1", advisor_policy_id: "growth-advisor-owner-explicit-goal-v1", goal_text: "Цель", goal_text_utf8_bytes: 8 };
    const candidate = {
      contract_version: "growth-learning-v1" as const,
      derivation_version: "growth-learning-derivation-v1",
      candidate_id: "gl1:" + "d".repeat(64),
      kind: "relation_review" as const,
      reason_code: "missing_goal_mapping" as const,
      growth_contract_version: "growth-engine-v1",
      growth_derivation_version: "growth-engine-derivation-v1",
      growth_policy_id: "growth-engine-v1",
      growth_policy_fingerprint: "sha256:" + "e".repeat(64),
      goal_source_uuid: selector.source_note_uuid,
      goal_identity_fingerprint: advisorRequest.goal_identity_fingerprint,
      growth_state: "goal_mapping_missing" as const,
      cohort_fingerprint: selector.behavioral_cohort_fingerprint,
      behavioral_option_fingerprint: selector.behavioral_option_fingerprint,
      behavioral_reference_fingerprint: "sha256:" + "f".repeat(64),
      mapping_id: null,
      mapping_fingerprint: null,
      question: "Для текущего варианта ещё не задано, как он относится к выбранной цели. Хочешь проверить эту связь?",
      basis_fingerprint: "sha256:" + "1".repeat(64),
      issued_at: "2026-09-14T10:00:00Z",
      expires_at: "2026-09-14T10:10:00Z",
    };
    const fetcher = vi.fn<FetchLike>()
      .mockResolvedValueOnce(ok({ goals: [] }))
      .mockResolvedValueOnce(ok({ mappings: [] }))
      .mockResolvedValueOnce(ok({ goal_results: [] }))
      .mockResolvedValueOnce(ok({ candidate_mapping_fingerprint: null }))
      .mockResolvedValueOnce(ok({ status: "accepted", mapping: {} }))
      .mockResolvedValueOnce(ok({ branch: "advisor", state: "result", assistant_result: null, error: null, provenance: {} }))
      .mockResolvedValueOnce(ok({ status: "candidate", candidate, no_candidate_code: null }))
      .mockResolvedValueOnce(ok({ status: "updated", event: {} }))
      .mockResolvedValueOnce(ok({ candidate_id: candidate.candidate_id, disposition: "ignore", answer_draft: null, handoff: null }));

    await loadGrowthGoals(fetcher);
    await loadGrowthMappingStatus(fetcher);
    await loadGrowth(selector.source_note_uuid, fetcher);
    await reviewGrowthMapping(selector, "neutral_or_unknown", fetcher);
    await confirmGrowthMapping(selector, "neutral_or_unknown", "0198f4c5-6a00-7000-7000-000000000011", "sha256:" + "2".repeat(64), fetcher);
    await previewGrowthAdvisor(advisorRequest, fetcher);
    await executeGrowthAdvisor(advisorRequest, preview, fetcher);
    await requestGrowthLearningQuestion({ contract_version: "growth-learning-v1", goal_source_uuid: selector.source_note_uuid }, fetcher);
    await resolveGrowthLearningQuestion({ contract_version: "growth-learning-v1", goal_source_uuid: selector.source_note_uuid }, candidate, "ignore", null, fetcher);

    expect(fetcher.mock.calls.map(([path]) => path)).toEqual([
      "/api/growth/goals",
      "/api/growth/mappings/status",
      "/api/growth",
      "/api/growth/mappings/review",
      "/api/growth/mappings/confirm",
      "/api/growth-advisor/preview",
      "/api/growth-advisor/execute",
      "/api/growth-learning/questions",
      "/api/growth-learning/questions/resolve",
    ]);
    expect(JSON.parse(String(fetcher.mock.calls[2][1]?.body))).toEqual({
      contract_version: "growth-engine-v1",
      selection: { mode: "selected_goal", source_note_uuid: selector.source_note_uuid },
      max_results: 200,
      max_result_bytes: 131072,
    });
    expect((fetcher.mock.calls[7][1]?.headers as Record<string, string>)["X-Second-Brain-Request"]).toBe("growth-learning-v1");
  });

  it("keeps Decision Compass base, Advisor preview and Advisor execute on explicit routes", async () => {
    const preview = {
      contract_version: "growth-advisor-v1" as const,
      goal_source_uuid: decisionCompassRequest.selected_goal!.source_uuid,
      goal_identity_fingerprint: decisionCompassRequest.selected_goal!.identity_fingerprint,
      assistant_contract_version: "assistant-v1",
      advisor_policy_id: "growth-advisor-owner-explicit-goal-v1",
      goal_text: "Цель",
      goal_text_utf8_bytes: 8,
    };
    const fetcher = vi.fn<FetchLike>()
      .mockResolvedValueOnce(ok({}))
      .mockResolvedValueOnce(ok(preview))
      .mockResolvedValueOnce(ok({}));

    await buildDecisionCompass(decisionCompassRequest, fetcher);
    await previewDecisionCompassAdvisor(decisionCompassRequest, fetcher);
    await executeDecisionCompassAdvisor(decisionCompassRequest, preview, fetcher);

    expect(fetcher.mock.calls.map(([path]) => path)).toEqual([
      "/api/decision-compass",
      "/api/decision-compass/advisor/preview",
      "/api/decision-compass/advisor/execute",
    ]);
    expect(fetcher.mock.calls.every(([, init]) => (init?.headers as Record<string, string>)["X-Second-Brain-Request"] === "decision-compass-v1")).toBe(true);
    expect(JSON.parse(String(fetcher.mock.calls[0][1]?.body))).toEqual(decisionCompassRequest);
    expect(JSON.parse(String(fetcher.mock.calls[2][1]?.body))).toEqual({
      request: decisionCompassRequest,
      preview,
      confirmed: true,
    });
  });
});
