import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, describe, expect, it, vi } from "vitest";

import { RetrospectiveCalibrationSurface } from "../retrospective-calibration-surface";
import type { RetrospectiveCalibrationResult } from "../stage7-api";

let root: Root | undefined;

afterEach(() => {
  act(() => root?.unmount());
  root = undefined;
  vi.restoreAllMocks();
  document.body.innerHTML = "";
});

async function renderSurface(): Promise<HTMLDivElement> {
  const host = document.createElement("div");
  document.body.append(host);
  root = createRoot(host);
  await act(async () => {
    root?.render(<RetrospectiveCalibrationSurface />);
  });
  return host;
}

const result: RetrospectiveCalibrationResult = {
  derivation_version: "retrospective-calibration-v1",
  policy_id: "retrospective-simulate-me-exact-cutoff-v1",
  policy_fingerprint: "sha256:fixture",
  reconstruction_mode: "current-vault-temporal-projection-v1",
  metrics: {
    decision_notes_seen: 3,
    eligible_decisions: 2,
    predicted_decisions: 1,
    abstentions: 1,
    exact_option_match_count: 1,
    mismatch_count: 0,
    unavailable_count: 0,
    invalid_count: 0,
    coverage: { numerator: 1, denominator: 2 },
    accuracy_non_abstained: { numerator: 1, denominator: 1 },
  },
  excluded_decisions: [
    { code: "decision_body_invalid", count: 1 },
  ],
  replay_unavailable: [
    { code: "simulate_me_unavailable", count: 0 },
  ],
  replay_invalid: [
    { code: "simulate_me_result_invalid", count: 0 },
  ],
  temporal_caveats: [
    { code: "historical_snapshot_unavailable", count: 2 },
  ],
};

function jsonResponse(payload: unknown, status = 200): Response {
  return new Response(JSON.stringify(payload), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

describe("Retrospective Calibration Stage 7 surface", () => {
  it("stays idle until the owner explicitly requests a rebuild", async () => {
    const fetchSpy = vi.spyOn(window, "fetch");
    const host = await renderSurface();

    expect(fetchSpy).not.toHaveBeenCalled();
    expect(host.textContent).toContain("ретроспективная проверка");
    expect(host.textContent).toContain("Нажми кнопку");
  });

  it("sends an exact empty request and renders aggregate ratios without recomputation", async () => {
    const fetchSpy = vi.spyOn(window, "fetch").mockResolvedValue(jsonResponse(result));
    const host = await renderSurface();
    const button = Array.from(host.querySelectorAll<HTMLButtonElement>("button"))
      .find((item) => item.textContent?.includes("Проверить ретроспективно"));

    await act(async () => button?.click());

    expect(fetchSpy).toHaveBeenCalledOnce();
    const [path, init] = fetchSpy.mock.calls[0];
    expect(path).toBe("/api/retrospective-calibration");
    expect(init?.method).toBe("POST");
    expect((init?.headers as Record<string, string>)["X-Second-Brain-Request"])
      .toBe("retrospective-calibration-v1");
    expect(init?.body).toBe("{}");
    expect(host.textContent).toContain("1 / 2");
    expect(host.textContent).toContain("1 / 1");
    expect(host.textContent).toContain("Точных совпадений");
    expect(host.textContent).toContain("Временные оговорки");
    expect(host.textContent).not.toContain("%");
    expect(host.textContent).not.toContain("situation");
    expect(host.textContent).not.toContain("chosen_option");
  });

  it("renders the zero-eligible state without calling it zero accuracy", async () => {
    const zero: RetrospectiveCalibrationResult = {
      ...result,
      metrics: {
        ...result.metrics,
        decision_notes_seen: 0,
        eligible_decisions: 0,
        predicted_decisions: 0,
        abstentions: 0,
        coverage: null,
        accuracy_non_abstained: null,
      },
    };
    vi.spyOn(window, "fetch").mockResolvedValue(jsonResponse(zero));
    const host = await renderSurface();
    const button = Array.from(host.querySelectorAll<HTMLButtonElement>("button"))
      .find((item) => item.textContent?.includes("Проверить ретроспективно"));

    await act(async () => button?.click());

    expect(host.textContent).toContain("Подходящих решений не найдено.");
    expect(host.textContent).toContain("Это не считается нулевой точностью.");
    expect(host.textContent).toContain("Нет подходящих решений");
  });

  it.each([
    ["RETROSPECTIVE_CALIBRATION_SOURCE_UNAVAILABLE", "Источники ретроспективной калибровки", "source-unavailable"],
    ["RETROSPECTIVE_CALIBRATION_INVALID_REQUEST", "Не удалось проверить запрос", "invalid"],
    ["RETROSPECTIVE_CALIBRATION_TOO_LARGE", "превышает допустимый предел", "too-large"],
    ["RETROSPECTIVE_CALIBRATION_RESULT_TOO_LARGE", "Результат ретроспективной калибровки слишком велик", "too-large"],
    ["RETROSPECTIVE_CALIBRATION_CANCELLED", "Ретроспективная калибровка отменена", "cancelled"],
  ] as const)("maps bounded API error %s to a safe UI state", async (code, message, state) => {
    vi.spyOn(window, "fetch").mockResolvedValue(
      jsonResponse({ error: { code, message: "private backend diagnostic" } }, 503),
    );
    const host = await renderSurface();
    const button = Array.from(host.querySelectorAll<HTMLButtonElement>("button"))
      .find((item) => item.textContent?.includes("Проверить ретроспективно"));

    await act(async () => button?.click());

    expect(host.querySelector(".calibration-error")).not.toBeNull();
    expect(host.textContent).toContain(message);
    expect(host.textContent).not.toContain("private backend diagnostic");
    expect(host.querySelector("[aria-busy='true']")).toBeNull();
    expect(host.querySelector(`[data-calibration-state='${state}']`)).not.toBeNull();
  });

  it("supports explicit cancellation without persistence or background refresh", async () => {
    const fetchSpy = vi.spyOn(window, "fetch").mockImplementation((_input, init) => (
      new Promise<Response>((_resolve, reject) => {
        init?.signal?.addEventListener("abort", () => {
          reject(new DOMException("Aborted", "AbortError"));
        });
      })
    ));
    const host = await renderSurface();
    const refresh = Array.from(host.querySelectorAll<HTMLButtonElement>("button"))
      .find((item) => item.textContent?.includes("Проверить ретроспективно"));

    await act(async () => refresh?.click());
    expect(host.querySelector("[aria-busy='true']")).not.toBeNull();
    const cancel = Array.from(host.querySelectorAll<HTMLButtonElement>("button"))
      .find((item) => item.textContent?.includes("Отменить"));
    await act(async () => cancel?.click());

    expect(fetchSpy).toHaveBeenCalledOnce();
    expect(host.textContent).toContain("Ретроспективная калибровка отменена.");
    expect(host.textContent).not.toContain("localStorage");
    expect(host.textContent).not.toContain("sessionStorage");
  });

  it("aborts an obsolete request before starting a newer calibration request", async () => {
    let firstReject: ((reason?: unknown) => void) | undefined;
    let firstAborted = false;
    const first = new Promise<Response>((_resolve, reject) => { firstReject = reject; });
    const fetchSpy = vi.spyOn(window, "fetch")
      .mockImplementationOnce((_input, init) => {
        init?.signal?.addEventListener("abort", () => {
          firstAborted = true;
          firstReject?.(new DOMException("Aborted", "AbortError"));
        });
        return first;
      })
      .mockResolvedValueOnce(jsonResponse(result));
    const host = await renderSurface();
    const refresh = Array.from(host.querySelectorAll<HTMLButtonElement>("button"))
      .find((item) => item.textContent?.includes("Проверить ретроспективно"));

    await act(async () => {
      refresh?.dispatchEvent(new MouseEvent("click", { bubbles: true }));
      refresh?.dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });

    expect(fetchSpy).toHaveBeenCalledTimes(2);
    expect(firstAborted).toBe(true);
    expect(host.textContent).toContain("1 / 2");
    expect(host.querySelector("[data-calibration-state='success']")).not.toBeNull();
  });
});
