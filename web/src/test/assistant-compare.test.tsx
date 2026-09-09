import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, describe, expect, it, vi } from "vitest";

import { AssistantCompareSurface } from "../assistant-compare-surface";

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
    root?.render(<AssistantCompareSurface />);
  });
  return host;
}

function setText(element: HTMLInputElement | HTMLTextAreaElement, value: string): void {
  const prototype = element instanceof HTMLTextAreaElement
    ? HTMLTextAreaElement.prototype
    : HTMLInputElement.prototype;
  const setter = Object.getOwnPropertyDescriptor(prototype, "value")?.set;
  setter?.call(element, value);
  element.dispatchEvent(new Event("input", { bubbles: true }));
}

async function fillRequired(host: HTMLDivElement): Promise<void> {
  const textareas = host.querySelectorAll<HTMLTextAreaElement>("textarea");
  const inputs = host.querySelectorAll<HTMLInputElement>(".stage7-option-row input");
  await act(async () => {
    setText(textareas[0], "Выбрать вариант");
    setText(inputs[0], "Первый");
    setText(inputs[1], "Второй");
  });
}

const assistantResult = {
  output_label: "independent_recommendation_analysis",
  kind: "analysis",
  recommendation: null,
  selected_option: null,
  rationale: ["Независимый анализ"],
  evidence_refs: [],
  constraints_used: [],
  objectives_used: [],
  uncertainty: [],
  abstention_code: null,
  contract_version: "assistant-v1",
};

const compareResult = {
  option_ids: ["option-1", "option-2"],
  assistant: { state: "result", result: assistantResult, error: null },
  simulate_me: {
    state: "result",
    result: { kind: "prediction", selected_option: { id: "option-2", label: "Второй" }, abstention_code: null },
    error: null,
  },
  delta: {
    relation: "assistant_only_selected",
    assistant_state: "result",
    simulate_me_state: "result",
    assistant_selected_option_id: null,
    simulate_me_selected_option_id: "option-2",
    explanation: "Ветки сохранены отдельно.",
  },
};

function jsonResponse(value: unknown): Response {
  return new Response(JSON.stringify(value), {
    status: 200,
    headers: { "Content-Type": "application/json" },
  });
}

describe("Assistant + Compare Stage 7 surface", () => {
  it("sends only explicit form fields with the exact Assistant purpose header", async () => {
    const fetchSpy = vi.spyOn(window, "fetch").mockResolvedValue(jsonResponse(assistantResult));
    const host = await renderSurface();
    await fillRequired(host);
    const textareas = host.querySelectorAll<HTMLTextAreaElement>("textarea");
    await act(async () => {
      setText(textareas[1], "Без записи");
      setText(textareas[2], "Получить независимую оценку");
      setText(textareas[3], "Факт введён вручную");
      setText(textareas[4], "Фон введён вручную");
    });
    const button = Array.from(host.querySelectorAll<HTMLButtonElement>("button"))
      .find((item) => item.textContent?.includes("Только независимый совет"));

    await act(async () => button?.click());

    expect(fetchSpy).toHaveBeenCalledOnce();
    const [path, init] = fetchSpy.mock.calls[0];
    expect(path).toBe("/api/assistant");
    expect((init?.headers as Record<string, string>)["X-Second-Brain-Request"]).toBe("assistant-v1");
    const body = JSON.parse(String(init?.body)) as Record<string, unknown>;
    expect(Object.keys(body).sort()).toEqual([
      "explicit_constraints",
      "explicit_context",
      "explicit_goals",
      "options",
      "task",
    ]);
    expect(JSON.stringify(body)).not.toContain("personal_memory");
    expect(JSON.stringify(body)).not.toContain("self_model");
    expect(host.textContent).toContain("Независимый совет");
    expect(host.textContent).toContain("Независимый анализ");
  });

  it("renders Assistant, Simulate Me and structural Delta as three separate blocks", async () => {
    vi.spyOn(window, "fetch").mockResolvedValue(jsonResponse(compareResult));
    const host = await renderSurface();
    await fillRequired(host);
    const button = Array.from(host.querySelectorAll<HTMLButtonElement>("button"))
      .find((item) => item.textContent?.includes("Совет + прогноз + сравнение"));

    await act(async () => button?.click());

    const headings = Array.from(host.querySelectorAll<HTMLHeadingElement>(".stage7-result-panel h3"))
      .map((item) => item.textContent);
    expect(headings).toEqual([
      "Независимый совет",
      "Прогноз моего выбора",
      "Структурное сравнение",
    ]);
    expect(host.textContent).toContain("Ветки сохранены отдельно.");
  });

  it("ignores an older response and never steals focus when requests complete out of order", async () => {
    let resolveFirst: ((value: Response) => void) | undefined;
    const first = new Promise<Response>((resolve) => { resolveFirst = resolve; });
    const currentCompare = {
      ...compareResult,
      delta: {
        ...compareResult.delta,
        explanation: "Актуальный результат сравнения",
      },
    };
    vi.spyOn(window, "fetch")
      .mockReturnValueOnce(first)
      .mockResolvedValueOnce(jsonResponse(currentCompare));
    const host = await renderSurface();
    await fillRequired(host);
    const task = host.querySelector<HTMLTextAreaElement>("textarea");
    const form = host.querySelector<HTMLFormElement>("form");
    const assistantButton = Array.from(host.querySelectorAll<HTMLButtonElement>("button"))
      .find((item) => item.textContent?.includes("Только независимый совет"));
    task?.focus();

    await act(async () => assistantButton?.click());
    await act(async () => {
      form?.dispatchEvent(new Event("submit", { bubbles: true, cancelable: true }));
    });
    expect(document.activeElement).toBe(task);
    expect(host.textContent).toContain("Актуальный результат сравнения");

    await act(async () => {
      resolveFirst?.(jsonResponse({ ...assistantResult, rationale: ["Устаревший результат"] }));
      await Promise.resolve();
    });

    expect(host.textContent).not.toContain("Устаревший результат");
    expect(host.textContent).toContain("Актуальный результат сравнения");
    expect(document.activeElement).toBe(task);
  });

  it("does not use browser storage or polling", async () => {
    const storage = vi.spyOn(Storage.prototype, "setItem");
    const polling = vi.spyOn(window, "setInterval");
    vi.spyOn(window, "fetch").mockResolvedValue(jsonResponse(assistantResult));
    const host = await renderSurface();
    await fillRequired(host);
    const button = Array.from(host.querySelectorAll<HTMLButtonElement>("button"))
      .find((item) => item.textContent?.includes("Только независимый совет"));

    await act(async () => button?.click());

    expect(storage).not.toHaveBeenCalled();
    const applicationPolling = polling.mock.calls.filter(([, delay]) => {
      // Motion uses setInterval as a ~60 fps animation scheduler. Product polling
      // would use a materially longer interval and remains forbidden here.
      return typeof delay !== "number" || delay >= 100;
    });
    expect(applicationPolling).toHaveLength(0);
  });
});
