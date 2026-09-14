import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, describe, expect, it, vi } from "vitest";

import * as api from "../api";
import { CognitiveTwinSurface } from "../cognitive-twin-surface";

let root: Root | undefined;

afterEach(() => {
  act(() => root?.unmount());
  root = undefined;
  vi.restoreAllMocks();
  document.body.innerHTML = "";
});

const fingerprint = (value: string): string => `sha256:${value.repeat(64)}`;
const sourceNoteUuid = "0198c8a0-0000-7000-8000-000000000010";
const journalUuid = "0198c8a0-0000-7000-8000-000000000011";
const cohortFingerprint = fingerprint("a");
const optionFingerprint = fingerprint("b");
const provenanceFingerprint = fingerprint("c");

const stated: api.SelfModelResponse = {
  claims: [{
    dimension: "preference",
    claim: "Сохранять короткий рабочий контекст перед решением",
    domain: "work-format",
    confidence: { state: "supported", supporting_evidence_count: 1, contradicting_evidence_count: 0 },
    temporal_context: { known_evidence_count: 1, unknown_evidence_count: 0 },
    supporting_evidence: [{
      id: sourceNoteUuid,
      evidence_kind: "user_statement",
      self_kind: "preference",
      domain: "work-format",
      evidence_at: "2026-09-10T12:00:00Z",
      evidence_at_precision: "exact",
      related_note_ids: [],
    }],
    contradicting_evidence: [],
    contextual_evidence: [],
  }],
  eligible_evidence_count: 1,
  represented_evidence_count: 1,
  generated_at: "2026-09-13T12:00:00Z",
  derivation_version: "self-model-derivation-v1",
  policy_fingerprint: fingerprint("d"),
};

const option: api.BehavioralOptionIdentity = {
  option_index: 0,
  option_fingerprint: optionFingerprint,
};

const choiceSupport: api.BehavioralChoiceSupport = {
  option,
  support_count: 3,
  support_ratio: { numerator: 3, denominator: 3 },
};

const behavioral: api.BehavioralSelfModelResponse = {
  contract_version: "behavioral-self-model-v1",
  derivation_version: "behavioral-self-model-derivation-v1",
  policy_id: "behavioral-observation-v1",
  policy_fingerprint: fingerprint("e"),
  generated_at: "2026-09-13T12:00:00Z",
  patterns: [{
    cohort: {
      grouping_policy: "exact-v1",
      domain: "work-format",
      situation_fingerprint: fingerprint("f"),
      information_fingerprint: fingerprint("g"),
      option_namespace_fingerprint: fingerprint("h"),
      criteria_fingerprint: fingerprint("i"),
      cohort_fingerprint: cohortFingerprint,
    },
    pattern_type: "repeated_exact_choice",
    state: "current",
    selected_option: option,
    support_count: 3,
    total_comparable_observations: 3,
    support_ratio: { numerator: 3, denominator: 3 },
    choice_support: [choiceSupport],
    temporal_span: {
      earliest_evidence_at: "2026-09-10T12:00:00Z",
      latest_evidence_at: "2026-09-12T12:00:00Z",
    },
    windows: [{ window: "current", observation_count: 3, choice_support: [choiceSupport] }],
    outcome_presence: { present: 1, absent: 2 },
    provenance: {
      source_journal_uuids: [journalUuid],
      source_count: 1,
      provenance_fingerprint: provenanceFingerprint,
    },
    caveats: ["support_is_descriptive"],
  }],
  eligible_journal_count: 3,
  comparable_observation_count: 3,
  excluded_unknown_time_count: 0,
  excluded_outside_horizon_count: 0,
  caveats: ["support_is_descriptive"],
};

const emptyMappings: api.StatedObservedMappingStatusResponse = {
  mapping_policy_id: "stated-observed-explicit-mapping-v1",
  mappings: [],
  active_mapping_count: 0,
};

const review: api.StatedObservedMappingReviewResponse = {
  generated_at: "2026-09-13T12:00:00Z",
  stated: {
    source_note_uuid: sourceNoteUuid,
    dimension: "preference",
    source_evidence_kind: "user_statement",
    source_self_kind: "preference",
    domain: "work-format",
    evidence_at: "2026-09-10T12:00:00Z",
    evidence_at_precision: "exact",
    source_contract_version: "self-model-v1",
    source_derivation_version: "self-model-derivation-v1",
    self_model_policy_fingerprint: fingerprint("j"),
    source_fingerprint: fingerprint("k"),
    claim_fingerprint: fingerprint("l"),
  },
  behavioral: {
    cohort: behavioral.patterns[0].cohort,
    option,
    pattern_type: "repeated_exact_choice",
    pattern_state: "current",
    pattern_fingerprint: fingerprint("m"),
    source_fingerprint: fingerprint("n"),
    provenance_fingerprint: provenanceFingerprint,
    source_count: 1,
    behavioral_contract_version: "behavioral-observation-v1",
    behavioral_derivation_version: "behavioral-observation-full-derivation-v1",
    observation_version: "behavioral-observation-v1",
    policy_id: "behavioral-observation-v1",
    policy_fingerprint: fingerprint("o"),
    comparison_subject: "current-exact-option-v1",
  },
  candidate_mapping_fingerprint: fingerprint("p"),
  claim_text: stated.claims[0].claim ?? "",
  cohort_domain: "work-format",
  situation: "Перед рабочим блоком",
  information_known_at_decision_time: "Доступный контекст",
  criteria: ["сфокусированность"],
  ordered_options: [
    { option_index: 0, option_fingerprint: optionFingerprint, label: "Сохранить контекст" },
    { option_index: 1, option_fingerprint: fingerprint("q"), label: "Сразу перейти к действию" },
  ],
  pattern_type: "repeated_exact_choice",
  pattern_state: "current",
  caveats: ["current_source_revalidated"],
};

const acceptedMapping: api.StatedObservedMappingStatusItem = {
  mapping_id: "0198c8a0-0000-7000-8000-000000000012",
  lifecycle_state: "active",
  source_note_uuid: sourceNoteUuid,
  domain: "work-format",
  behavioral_cohort_fingerprint: cohortFingerprint,
  behavioral_option_index: 0,
  behavioral_option_fingerprint: optionFingerprint,
  pattern_type: "repeated_exact_choice",
  pattern_state: "current",
  mapping_fingerprint: review.candidate_mapping_fingerprint,
  mapping_policy_fingerprint: fingerprint("r"),
  created_at: "2026-09-13T12:01:00Z",
  reviewed_at: "2026-09-13T12:01:00Z",
  supersedes_mapping_id: null,
};

const composition: api.StatedObservedCompositionResponse = {
  contract_version: "stated-observed-mapping-v1",
  derivation_version: "stated-observed-composition-derivation-v1",
  mapping_policy_id: "stated-observed-explicit-mapping-v1",
  mapping_policy_fingerprint: fingerprint("s"),
  generated_at: "2026-09-13T12:01:00Z",
  state: "aligned",
  reason_code: null,
  mapping_id: acceptedMapping.mapping_id,
  mapping_fingerprint: acceptedMapping.mapping_fingerprint,
  observed_option: option,
  behavioral_pattern_type: "repeated_exact_choice",
  behavioral_pattern_state: "current",
  caveats: [],
};

async function renderSurface(): Promise<HTMLDivElement> {
  const host = document.createElement("div");
  document.body.append(host);
  root = createRoot(host);
  await act(async () => root?.render(<CognitiveTwinSurface />));
  return host;
}

function button(host: HTMLElement, label: string): HTMLButtonElement {
  const found = Array.from(host.querySelectorAll<HTMLButtonElement>("button"))
    .find((item) => item.textContent?.includes(label));
  if (!found) throw new Error(`button not found: ${label}`);
  return found;
}

function choose(host: HTMLElement, selector: string, value: string): void {
  const control = host.querySelector<HTMLSelectElement>(selector);
  if (!control) throw new Error(`select not found: ${selector}`);
  const setter = Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype, "value")?.set;
  setter?.call(control, value);
  control.dispatchEvent(new Event("change", { bubbles: true }));
}

function refreshMocks(): void {
  vi.spyOn(api, "loadSelfModel").mockResolvedValue(stated);
  vi.spyOn(api, "loadBehavioralSelfModel").mockResolvedValue(behavioral);
  vi.spyOn(api, "loadStatedObservedMappingStatus").mockResolvedValue(emptyMappings);
}

function deferred<T>(): { readonly promise: Promise<T>; readonly resolve: (value: T) => void } {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((next) => {
    resolve = next;
  });
  return { promise, resolve };
}

describe("Cognitive Twin Stage 10D surface", () => {
  it("stays read-only and idle until explicit refresh", async () => {
    const fetchSpy = vi.spyOn(window, "fetch");
    const host = await renderSurface();

    expect(fetchSpy).not.toHaveBeenCalled();
    expect(host.textContent).toContain("Явное / наблюдаемое / сопоставленное сравнение");
    expect(host.textContent).toContain("Только чтение · данные не изменяются");
    expect(host.textContent).not.toContain("Этап 10D");
    expect(host.textContent).toContain("Нажми «Обновить текущие данные»");
  });

  it("renders the three layers and completes review plus explicit confirmation", async () => {
    refreshMocks();
    const reviewSpy = vi.spyOn(api, "reviewStatedObservedMapping").mockResolvedValue(review);
    const confirmSpy = vi.spyOn(api, "confirmStatedObservedMapping").mockResolvedValue({
      status: "accepted",
      mapping: acceptedMapping,
    });
    vi.spyOn(api, "loadStatedObservedComposition").mockResolvedValue(composition);
    const host = await renderSurface();

    await act(async () => button(host, "Обновить текущие данные").click());
    expect(host.textContent).toContain("Явное");
    expect(host.textContent).toContain("Наблюдаемое");
    expect(host.textContent).toContain("Сопоставление");
    expect(host.textContent).toContain("3 / 3");
    expect(host.textContent).not.toContain("100%");

    await act(async () => choose(host, "#cognitive-twin-stated-select", sourceNoteUuid));
    await act(async () => choose(host, "#cognitive-twin-observed-select", `${cohortFingerprint}:0:${optionFingerprint}`));
    await act(async () => button(host, "Показать проверку").click());

    expect(reviewSpy).toHaveBeenCalledOnce();
    expect(host.textContent).toContain("Подписи показаны только для проверки человеком.");
    expect(host.textContent).toContain("Сохранить контекст");

    const confirmation = host.querySelector<HTMLInputElement>(".cognitive-twin-confirm-label input");
    if (!confirmation) throw new Error("confirmation control not found");
    await act(async () => confirmation.click());
    await act(async () => button(host, "Подтвердить сопоставление").click());

    expect(confirmSpy).toHaveBeenCalledOnce();
    const [selector, operationId, confirmed, supersedes, projection] = confirmSpy.mock.calls[0];
    expect(selector).toEqual({
      source_note_uuid: sourceNoteUuid,
      behavioral_cohort_fingerprint: cohortFingerprint,
      behavioral_option_index: 0,
      behavioral_option_fingerprint: optionFingerprint,
    });
    expect(operationId).toMatch(/^[0-9a-f]{8}-[0-9a-f]{4}-7[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/);
    expect(confirmed).toBe(true);
    expect(supersedes).toBeNull();
    expect(projection).toBeUndefined();
    expect(host.textContent).toContain("UUID принятого сопоставления");
    expect(host.textContent).toContain("Текущее сравнение");
  });

  it("ignores a late review result after the owner changes Stated selection", async () => {
    refreshMocks();
    const pending = deferred<api.StatedObservedMappingReviewResponse>();
    let reviewSignal: AbortSignal | undefined;
    const reviewSpy = vi.spyOn(api, "reviewStatedObservedMapping").mockImplementation(
      (_selector, _fetcher, signal) => {
        reviewSignal = signal;
        return pending.promise;
      },
    );
    const host = await renderSurface();
    await act(async () => button(host, "Обновить текущие данные").click());
    await act(async () => choose(host, "#cognitive-twin-stated-select", sourceNoteUuid));
    await act(async () => choose(host, "#cognitive-twin-observed-select", `${cohortFingerprint}:0:${optionFingerprint}`));
    await act(async () => button(host, "Показать проверку").click());
    await act(async () => choose(host, "#cognitive-twin-stated-select", ""));

    expect(reviewSpy).toHaveBeenCalledOnce();
    expect(reviewSignal?.aborted).toBe(true);
    pending.resolve(review);
    await act(async () => Promise.resolve());
    expect(host.querySelector(".cognitive-twin-review")).toBeNull();
  });

  it("ignores a late composition result after the owner changes Stated selection", async () => {
    refreshMocks();
    const pending = deferred<api.StatedObservedCompositionResponse>();
    let compositionSignal: AbortSignal | undefined;
    const compositionSpy = vi.spyOn(api, "loadStatedObservedComposition").mockImplementation(
      (_sourceNoteUuid, _fetcher, signal) => {
        compositionSignal = signal;
        return pending.promise;
      },
    );
    const host = await renderSurface();
    await act(async () => button(host, "Обновить текущие данные").click());
    await act(async () => choose(host, "#cognitive-twin-stated-select", sourceNoteUuid));
    await act(async () => button(host, "Проверить текущее сравнение").click());
    await act(async () => choose(host, "#cognitive-twin-stated-select", ""));

    expect(compositionSpy).toHaveBeenCalledOnce();
    expect(compositionSignal?.aborted).toBe(true);
    pending.resolve(composition);
    await act(async () => Promise.resolve());
    expect(host.querySelector(".cognitive-twin-composition")).toBeNull();
  });

  it("cancels confirmation and suppresses a late accepted result", async () => {
    refreshMocks();
    vi.spyOn(api, "reviewStatedObservedMapping").mockResolvedValue(review);
    let resolveConfirmation: ((value: { status: "accepted"; mapping: api.StatedObservedMappingStatusItem }) => void) | undefined;
    const confirmSpy = vi.spyOn(api, "confirmStatedObservedMapping").mockImplementation(
      (...args) => new Promise((resolve) => {
        resolveConfirmation = resolve;
        expect(args[5]).toBeInstanceOf(AbortSignal);
      }),
    );
    const host = await renderSurface();
    await act(async () => button(host, "Обновить текущие данные").click());
    await act(async () => choose(host, "#cognitive-twin-stated-select", sourceNoteUuid));
    await act(async () => choose(host, "#cognitive-twin-observed-select", `${cohortFingerprint}:0:${optionFingerprint}`));
    await act(async () => button(host, "Показать проверку").click());
    const confirmation = host.querySelector<HTMLInputElement>(".cognitive-twin-confirm-label input");
    if (!confirmation) throw new Error("confirmation control not found");
    await act(async () => confirmation.click());
    await act(async () => button(host, "Подтвердить сопоставление").click());
    const cancel = button(host, "Отменить");
    await act(async () => cancel.click());

    expect(confirmSpy).toHaveBeenCalledOnce();
    expect(host.textContent).toContain("принятый результат не показан");
    resolveConfirmation?.({ status: "accepted", mapping: acceptedMapping });
    await act(async () => Promise.resolve());
    expect(host.textContent).not.toContain("UUID принятого сопоставления");
  });
});
