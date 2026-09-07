import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, describe, expect, it, vi } from "vitest";

import * as api from "../api";
import { SearchSurface } from "../search-surface";

let root: Root | undefined;

afterEach(() => {
  act(() => root?.unmount());
  root = undefined;
  vi.restoreAllMocks();
  document.body.innerHTML = "";
});

async function renderSearch(): Promise<HTMLDivElement> {
  const host = document.createElement("div");
  document.body.append(host);
  root = createRoot(host);
  await act(async () => {
    root?.render(<SearchSurface />);
  });
  return host;
}

function setInput(input: HTMLInputElement, value: string): void {
  const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")?.set;
  setter?.call(input, value);
  input.dispatchEvent(new Event("input", { bubbles: true }));
}

describe("Search focus ownership", () => {
  it("ignores an obsolete note open after a newer search and preserves input focus", async () => {
    vi.spyOn(api, "searchNotes")
      .mockResolvedValueOnce({
        hits: [{ id: "note-1", title: "Первая заметка", type: "resource", relative_path: "30 Resources/first.md", tags: [] }],
      })
      .mockResolvedValueOnce({ hits: [] });

    let resolveNote: ((note: api.RetrievedNote) => void) | undefined;
    vi.spyOn(api, "retrieveNote").mockImplementation(() => new Promise<api.RetrievedNote>((resolve) => {
      resolveNote = resolve;
    }));

    const host = await renderSearch();
    const input = host.querySelector<HTMLInputElement>("#search-input");
    const form = host.querySelector<HTMLFormElement>(".search-form");
    expect(input).not.toBeNull();
    expect(form).not.toBeNull();
    if (!input || !form) return;

    await act(async () => {
      setInput(input, "первый запрос");
      form.dispatchEvent(new Event("submit", { bubbles: true, cancelable: true }));
    });

    const open = host.querySelector<HTMLButtonElement>(".search-hit button");
    expect(open).not.toBeNull();
    await act(async () => open?.click());
    expect(api.retrieveNote).toHaveBeenCalledWith("note-1");

    input.focus();
    await act(async () => {
      setInput(input, "новый запрос");
      form.dispatchEvent(new Event("submit", { bubbles: true, cancelable: true }));
    });
    expect(document.activeElement).toBe(input);

    await act(async () => {
      resolveNote?.({
        id: "note-1",
        title: "Первая заметка",
        type: "resource",
        relative_path: "30 Resources/first.md",
        created: "2026-01-01T00:00:00Z",
        content: "Устаревшее содержимое",
      });
    });

    expect(host.querySelector(".retrieved-note")).toBeNull();
    expect(document.activeElement).toBe(input);
    expect(host.textContent).not.toContain("Устаревшее содержимое");
  });
});
