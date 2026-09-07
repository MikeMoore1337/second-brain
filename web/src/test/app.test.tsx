import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, describe, expect, it, vi } from "vitest";

import { App } from "../App";
import * as api from "../api";
import { DiagnosticsSurface } from "../diagnostics-surface";
import { CaptureSurface } from "../parity";
import { presentValue } from "../presentation";

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

async function renderDiagnostics(): Promise<HTMLDivElement> {
  const host = document.createElement("div");
  document.body.append(host);
  root = createRoot(host);
  await act(async () => {
    root?.render(<DiagnosticsSurface />);
  });
  return host;
}

const healthyDiagnostics: api.DiagnosticsResponse = {
  status: "healthy",
  generated_at: "2026-09-07T12:00:00+00:00",
  config: { resolvable: true },
  vault: { manifest_available: true, content_roots_available: true, attachments_scan_complete: true },
  manifest: { available: true, schema_version: 1 },
  counts: { managed_notes: 4, enrolled_personal_memory: 2, valid_decision_journals: 1, valid_outcome_observations: 1 },
  notes: 4,
  enrolled_personal_memory: 2,
  valid_decision_journals: 1,
  valid_outcome_observations: 1,
  attachments: { scan_complete: true, count: 3, total_bytes: 512 },
  attachment_total: 512,
  attachment_bytes: 512,
  timeline: { status: "healthy", required: true, code: null },
  self_model: { status: "healthy", required: true, code: null },
  self_retrieval: { status: "healthy", required: false, code: null },
  errors: 0,
  warnings: 0,
  diagnostics: [],
  exit_code: 0,
};

describe("React Web parity shell", () => {
  it("keeps diagnostics explicit-refresh-only and renders a healthy report safely", async () => {
    const load = vi.spyOn(api, "loadDiagnostics").mockResolvedValue(healthyDiagnostics);
    const host = await renderDiagnostics();

    expect(load).not.toHaveBeenCalled();
    expect(host.textContent).toContain("Нажми «Обновить»");
    const button = Array.from(host.querySelectorAll<HTMLButtonElement>("button")).find((item) => item.textContent?.includes("Обновить"));
    expect(button).not.toBeUndefined();
    await act(async () => button?.click());

    expect(load).toHaveBeenCalledOnce();
    expect(host.querySelector("[data-diagnostics-status='healthy']")).not.toBeNull();
    expect(host.textContent).toContain("Стабильно");
    expect(host.textContent).toContain("Последний сформированный отчёт");
    expect(host.textContent).toContain("Сигналы не обнаружены");
  });

  it.each([
    ["degraded", "Требует внимания", "UNSAFE_DIAGNOSTIC"],
    ["unavailable", "Недоступна", "CONFIG_UNAVAILABLE"],
  ] as const)("renders %s diagnostics without raw error details", async (status, label, code) => {
    const load = vi.spyOn(api, "loadDiagnostics").mockResolvedValue({
      ...healthyDiagnostics,
      status,
      config: { resolvable: status !== "unavailable" },
      diagnostics: [{ code, severity: "error", count: 1 }],
      errors: 1,
      warnings: 0,
      exit_code: status === "unavailable" ? 2 : 1,
      timeline: { status, required: true, code },
      self_model: { status, required: true, code },
    });
    const host = await renderDiagnostics();
    const button = Array.from(host.querySelectorAll<HTMLButtonElement>("button")).find((item) => item.textContent?.includes("Обновить"));
    await act(async () => button?.click());

    expect(load).toHaveBeenCalledOnce();
    expect(host.textContent).toContain(label);
    expect(host.textContent).toContain(code);
    expect(host.textContent).not.toContain("traceback");
    expect(host.querySelector(`[data-diagnostics-status='${status}']`)).not.toBeNull();
  });

  it("presents machine values with Russian labels", () => {
    expect(presentValue("note")).toBe("Заметка");
    expect(presentValue("not_assessed")).toBe("Не оценивалось");
    expect(presentValue("unknown")).toBe("Неизвестно");
  });

  it.each([320, 360, 390, 430])("mounts the Russian shell at %dpx", async (width) => {
    const originalWidth = window.innerWidth;
    Object.defineProperty(window, "innerWidth", { configurable: true, value: width });
    vi.spyOn(window, "fetch").mockImplementation((input) => {
      const path = String(input);
      const payload = path.endsWith("/api/timeline")
        ? { known_items: [], unknown_items: [], known_total: 0, unknown_total: 0 }
        : { claims: [], eligible_evidence_count: 0, represented_evidence_count: 0 };
      return Promise.resolve(new Response(JSON.stringify(payload), { status: 200 }));
    });

    try {
      const host = await renderApp();
      expect(host.querySelector('[aria-label="Навигация рабочего пространства"]')).not.toBeNull();
      expect(host.querySelector('[aria-label="Основные разделы"]')).not.toBeNull();
      expect(host.querySelector('[aria-label="Режим добавления"]')).not.toBeNull();
      expect(host.querySelector('[aria-label="Режим журнала решений"]')).not.toBeNull();
      expect(host.textContent).toContain("Сбор контекста");
      expect(host.textContent).toContain("Подтвердить сохранение");
    } finally {
      Object.defineProperty(window, "innerWidth", { configurable: true, value: originalWidth });
    }
  });

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

  it("keeps skip-link focus target and toggle relationships explicit", async () => {
    vi.spyOn(window, "fetch").mockImplementation((input) => {
      const path = String(input);
      const payload = path.endsWith("/api/timeline")
        ? { known_items: [], unknown_items: [], known_total: 0, unknown_total: 0 }
        : { claims: [], eligible_evidence_count: 0, represented_evidence_count: 0 };
      return Promise.resolve(new Response(JSON.stringify(payload), { status: 200 }));
    });
    const host = await renderApp();

    const main = host.querySelector("main#main-content");
    const skipLink = host.querySelector<HTMLAnchorElement>(".skip-link");
    expect(main?.getAttribute("tabindex")).toBe("-1");
    expect(skipLink?.getAttribute("href")).toBe("#main-content");
    skipLink?.click();
    expect(document.activeElement).toBe(main);
    expect(host.querySelectorAll("#capture-panel")).toHaveLength(1);
    expect([...host.querySelectorAll("[aria-label='Режим добавления'] button")].every((button) => button.getAttribute("aria-controls") === "capture-panel")).toBe(true);
    expect([...host.querySelectorAll("[aria-label='Режим журнала решений'] button")].every((button) => button.getAttribute("aria-controls") === "decision-journal-content")).toBe(true);
  });

  it("preserves Decision Journal input when switching modes", async () => {
    vi.spyOn(window, "fetch").mockImplementation((input) => {
      const path = String(input);
      const payload = path.endsWith("/api/timeline")
        ? { known_items: [], unknown_items: [], known_total: 0, unknown_total: 0 }
        : { claims: [], eligible_evidence_count: 0, represented_evidence_count: 0 };
      return Promise.resolve(new Response(JSON.stringify(payload), { status: 200 }));
    });
    const host = await renderApp();
    const title = host.querySelector<HTMLInputElement>("#decision-form input");
    const modes = host.querySelectorAll<HTMLButtonElement>("#decision-journal .mode-button");
    expect(title).not.toBeNull();
    expect(modes).toHaveLength(2);
    if (!title || modes.length !== 2) return;

    await act(async () => {
      const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")?.set;
      setter?.call(title, "Сохранить выбор");
      title.dispatchEvent(new Event("input", { bubbles: true }));
      modes[1]?.click();
      modes[0]?.click();
    });
    expect(title.value).toBe("Сохранить выбор");
  });

  it("moves focus to a retrieved Search note", async () => {
    vi.spyOn(window, "fetch").mockImplementation((input) => {
      const path = String(input);
      const payload = path.endsWith("/api/timeline")
        ? { known_items: [], unknown_items: [], known_total: 0, unknown_total: 0 }
        : { claims: [], eligible_evidence_count: 0, represented_evidence_count: 0 };
      return Promise.resolve(new Response(JSON.stringify(payload), { status: 200 }));
    });
    vi.spyOn(api, "searchNotes").mockResolvedValue({ hits: [{ id: "note-1", title: "Заметка", type: "resource", relative_path: "10 Projects/note.md", snippet: "Фрагмент", tags: [] }] });
    vi.spyOn(api, "retrieveNote").mockResolvedValue({ id: "note-1", title: "Заметка", type: "resource", relative_path: "10 Projects/note.md", created: "2026-01-01T00:00:00Z", updated: undefined, content: "Содержание" });
    const host = await renderApp();
    const input = host.querySelector<HTMLInputElement>("#search-input");
    const form = host.querySelector<HTMLFormElement>(".search-form");
    expect(input).not.toBeNull();
    expect(form).not.toBeNull();
    if (!input || !form) return;

    await act(async () => {
      const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")?.set;
      setter?.call(input, "проверка");
      input.dispatchEvent(new Event("input", { bubbles: true }));
      form.dispatchEvent(new Event("submit", { bubbles: true, cancelable: true }));
    });
    const open = host.querySelector<HTMLButtonElement>("#search .search-hit button");
    expect(open).not.toBeNull();
    await act(async () => open?.click());
    expect(document.activeElement).toBe(host.querySelector(".retrieved-note"));
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
