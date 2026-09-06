import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, describe, expect, it, vi } from "vitest";

import { App } from "../App";

let root: Root | undefined;

afterEach(() => {
  act(() => root?.unmount());
  root = undefined;
  vi.restoreAllMocks();
  document.body.innerHTML = "";
});

async function renderApp(): Promise<HTMLDivElement> {
  const host = document.createElement("div");
  document.body.append(host);
  root = createRoot(host);
  await act(async () => {
    root?.render(<App />);
  });
  return host;
}

describe("React Web parity shell", () => {
  it("mounts every current-main GUI surface with the legacy Russian entry points", async () => {
    vi.spyOn(window, "fetch").mockImplementation((input) => {
      const path = String(input);
      const payload = path.endsWith("/api/timeline")
        ? { known_items: [], unknown_items: [], known_total: 0, unknown_total: 0 }
        : { claims: [], eligible_evidence_count: 0, represented_evidence_count: 0 };
      return Promise.resolve(new Response(JSON.stringify(payload), { status: 200 }));
    });
    const host = await renderApp();

    expect(host.querySelector("main#main-content")).not.toBeNull();
    expect(host.querySelector('[aria-label="Основные разделы"]')).not.toBeNull();
    expect(host.querySelector("#hero-title")?.textContent).toContain("Сохраняй идеи");
    for (const id of ["decision-journal", "timeline", "self-model", "simulate-me", "self-retrieval", "search", "memory", "growth"]) {
      expect(host.querySelector(`#${id}`), id).not.toBeNull();
    }
    expect(host.querySelector('[data-capture-panel]')).not.toBeNull();
    expect(host.querySelector('[data-self-retrieval-surface]')).not.toBeNull();
  });

  it("reflects busy state on the complete Self Retrieval surface while the request is pending", async () => {
    let resolve: ((response: Response) => void) | undefined;
    vi.spyOn(window, "fetch").mockImplementation((input) => {
      const path = String(input);
      if (path.endsWith("/api/self-retrieval")) {
        return new Promise<Response>((done) => { resolve = done; });
      }
      if (path.endsWith("/api/timeline")) {
        return Promise.resolve(new Response(JSON.stringify({ known_items: [], unknown_items: [], known_total: 0, unknown_total: 0 }), { status: 200 }));
      }
      return Promise.resolve(new Response(JSON.stringify({ claims: [], eligible_evidence_count: 0, represented_evidence_count: 0 }), { status: 200 }));
    });
    const host = await renderApp();
    const input = host.querySelector<HTMLInputElement>("#self-retrieval-input");
    const form = host.querySelector<HTMLFormElement>(".self-retrieval-form");
    expect(input).not.toBeNull();
    if (!input || !form) return;

    await act(async () => {
      const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")?.set;
      setter?.call(input, "fastapi");
      input.dispatchEvent(new Event("input", { bubbles: true }));
      form.dispatchEvent(new Event("submit", { bubbles: true, cancelable: true }));
    });
    expect(host.querySelector("[data-self-retrieval-surface]")?.getAttribute("aria-busy")).toBe("true");
    expect(host.querySelector("[data-self-retrieval-submit]")?.getAttribute("aria-busy")).toBe("true");

    await act(async () => {
      resolve?.(new Response(JSON.stringify({ items: [], exclusions: [], candidate_count: 0, included_count: 0, excluded_count: 0 }), { status: 200 }));
    });
    expect(host.querySelector("[data-self-retrieval-surface]")?.getAttribute("aria-busy")).toBe("false");
  });
});
