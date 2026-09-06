import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, describe, expect, it, vi } from "vitest";

import { App } from "../App";
import * as api from "../api";
import { CaptureSurface } from "../parity";

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

async function renderCapture(): Promise<HTMLDivElement> {
  const host = document.createElement("div");
  document.body.append(host);
  root = createRoot(host);
  await act(async () => {
    root?.render(<CaptureSurface />);
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
    expect(host.querySelector('.entry-icon [data-icon="add"]')).not.toBeNull();
    expect(host.querySelector('.topnav [data-icon="decision"]')).not.toBeNull();
    expect(host.querySelector('.card-meta [data-icon="open"]')).not.toBeNull();
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

  it("keeps Personal Memory opt-in only for source-free drafts", async () => {
    vi.spyOn(api, "createUrlDraft").mockResolvedValue({
      review_token: "review",
      draft: { title: "URL", note_type: "resource", content: "content", tags: [], links: [] },
      sources: [{ uri: "https://example.test", kind: "web" }],
    });
    const host = await renderCapture();
    const input = host.querySelector<HTMLInputElement>("#source-input");
    const form = host.querySelector<HTMLFormElement>(".capture-form");
    expect(input).not.toBeNull();
    expect(form).not.toBeNull();
    if (!input || !form) return;
    await act(async () => {
      const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")?.set;
      setter?.call(input, "example.test");
      input.dispatchEvent(new Event("input", { bubbles: true }));
      form.dispatchEvent(new Event("submit", { bubbles: true, cancelable: true }));
    });
    expect(host.querySelector(".review-editor")).not.toBeNull();
    expect(host.querySelector(".personal-memory-panel")).toBeNull();
  });

  it("leaves Voice transcript in Text mode for explicit draft submission", async () => {
    const transcribe = vi.spyOn(api, "transcribeAudio").mockResolvedValue({ transcript: { text: "Проверь меня" } });
    const createDraft = vi.spyOn(api, "createTextDraft");
    const host = await renderCapture();
    const voiceButton = Array.from(host.querySelectorAll<HTMLButtonElement>("button")).find((button) => button.textContent === "Голос");
    expect(voiceButton).not.toBeUndefined();
    await act(async () => voiceButton?.click());
    const fileInput = host.querySelector<HTMLInputElement>("input[type='file']");
    expect(fileInput).not.toBeNull();
    if (!fileInput) return;
    const file = new File(["audio"], "note.webm", { type: "audio/webm" });
    Object.defineProperty(fileInput, "files", { configurable: true, value: [file] });
    await act(async () => fileInput.dispatchEvent(new Event("change", { bubbles: true })));
    const transcribeButton = Array.from(host.querySelectorAll<HTMLButtonElement>("button")).find((button) => button.textContent === "Распознать");
    expect(transcribeButton).not.toBeUndefined();
    await act(async () => transcribeButton?.click());
    expect(transcribe).toHaveBeenCalledOnce();
    expect(createDraft).not.toHaveBeenCalled();
    expect(host.querySelector<HTMLTextAreaElement>("#text-input")?.value).toBe("Проверь меня");
  });
});
