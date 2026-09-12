import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ProspectiveAuditSurface } from "../prospective-audit-surface";

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
  await act(async () => root?.render(<ProspectiveAuditSurface />));
  return host;
}

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

const decision = {
  decision_id: "0190f4c0-1234-7123-8123-abcdefabcdef",
  evidence_at: "2026-09-13T11:00:00.000Z",
  evidence_at_precision: "exact" as const,
  created: "2026-09-13T11:00:00.000Z",
  options: [
    { index: 0, label: "Первый вариант", fingerprint: "sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa" },
    { index: 1, label: "Второй вариант", fingerprint: "sha256:bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb" },
  ],
  chosen_option_index: 0,
  chosen_option: "Первый вариант",
};

function setControlValue(control: HTMLInputElement | HTMLTextAreaElement | HTMLSelectElement, value: string): void {
  const prototype = Object.getPrototypeOf(control) as object;
  const setter = Object.getOwnPropertyDescriptor(prototype, "value")?.set;
  setter?.call(control, value);
  control.dispatchEvent(new Event("input", { bubbles: true }));
  control.dispatchEvent(new Event("change", { bubbles: true }));
}

describe("Prospective Audit Stage 9 surface", () => {
  it("stays idle and does not touch the plain simulate-me route", async () => {
    const fetchSpy = vi.spyOn(window, "fetch");
    const host = await renderSurface();

    expect(fetchSpy).not.toHaveBeenCalled();
    expect(host.textContent).toContain("Выполнить и записать");
    expect(host.textContent).toContain("Обычный раздел «Прогноз» выше остаётся read-only");
  });

  it("executes, rereads, maps and confirms a linkage with bounded requests", async () => {
    const pending = {
      events: [event],
      decision_journals: [{ ...decision, options: decision.options.map(({ index, label }) => ({ index, label })) }],
      limits: { max_events: 32, max_decision_journals: 32 },
    };
    const review = { event, decision, mapping_basis: "owner-explicit-v1" as const, requires_confirmation: true as const };
    const link = {
      status: "linked" as const,
      audit_event_id: event.event_id,
      decision_id: decision.decision_id,
      link_state: "LINKED_VALID" as const,
      linked_at: "2026-09-13T11:01:00.000Z",
    };
    const fetchSpy = vi.spyOn(window, "fetch").mockImplementation((input) => {
      const path = String(input);
      if (path.endsWith("/execute")) return Promise.resolve(jsonResponse({ event }));
      if (path.endsWith("/pending")) return Promise.resolve(jsonResponse(pending));
      if (path.endsWith("/link/review")) return Promise.resolve(jsonResponse(review));
      if (path.endsWith("/link/confirm")) return Promise.resolve(jsonResponse(link));
      return Promise.reject(new Error(`unexpected route ${path}`));
    });
    const host = await renderSurface();
    setControlValue(host.querySelector<HTMLTextAreaElement>("#stage9-query")!, "Что выбрать?");
    setControlValue(host.querySelectorAll<HTMLInputElement>(".stage9-option-row input")[1], "Первый вариант");

    await act(async () => host.querySelector<HTMLFormElement>(".stage9-operation-panel")?.requestSubmit());
    expect(fetchSpy.mock.calls[0][0]).toBe("/api/prospective-audit/execute");
    expect(host.textContent).toContain("ПРОГНОЗ записан");

    await act(async () => Array.from(host.querySelectorAll<HTMLButtonElement>("button")).find((button) => button.textContent?.includes("Показать ожидающие связи"))?.click());
    expect(fetchSpy.mock.calls[1][0]).toBe("/api/prospective-audit/pending");

    await act(async () => Array.from(host.querySelectorAll<HTMLButtonElement>("button")).find((button) => button.textContent?.includes("Перечитать и проверить"))?.click());
    expect(fetchSpy.mock.calls[2][0]).toBe("/api/prospective-audit/link/review");
    expect(host.textContent).toContain("Проверить явное соответствие");

    await act(async () => setControlValue(host.querySelector<HTMLSelectElement>("select[aria-label^='Соответствие']")!, "0"));
    await act(async () => host.querySelector<HTMLInputElement>(".stage9-confirmation input")?.click());
    await act(async () => Array.from(host.querySelectorAll<HTMLButtonElement>("button")).find((button) => button.textContent?.includes("Подтвердить связь"))?.click());

    expect(fetchSpy.mock.calls[3][0]).toBe("/api/prospective-audit/link/confirm");
    const confirmBody = JSON.parse((fetchSpy.mock.calls[3][1] as RequestInit).body as string) as { mapping: { decision_option_fingerprint: string }[]; confirmed: boolean };
    expect(confirmBody.confirmed).toBe(true);
    expect(confirmBody.mapping).toEqual([{ audit_option_id: "a", decision_option_index: 0, decision_option_fingerprint: decision.options[0].fingerprint }]);
    expect(fetchSpy.mock.calls.map(([input]) => String(input))).not.toContain("/api/simulate-me");
    expect(host.textContent).toContain("Связь записана");
  });
});
