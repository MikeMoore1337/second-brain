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

const MOTION_FRAME_INTERVAL_MS = 1000 / 60;

function isMotionSchedulerCall(call: readonly unknown[]): boolean {
  const [handler, delay, ...args] = call;
  return (
    typeof handler === "function" &&
    handler.length === 0 &&
    typeof delay === "number" &&
    Math.abs(delay - MOTION_FRAME_INTERVAL_MS) < 0.001 &&
    args.length === 0
  );
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

  it("keeps technical provider prose out of the UI without inferring a selection", async () => {
    const technicalResult = {
      ...assistantResult,
      kind: "recommendation",
      recommendation: "option-1",
      selected_option: null,
      rationale: ["Поскольку в explicit_constraints указано ограничение."],
      uncertainty: ["evidence_refs не указаны"],
    };
    vi.spyOn(window, "fetch").mockResolvedValue(jsonResponse(technicalResult));
    const host = await renderSurface();
    await fillRequired(host);
    const button = Array.from(host.querySelectorAll<HTMLButtonElement>("button"))
      .find((item) => item.textContent?.includes("Только независимый совет"));

    await act(async () => button?.click());

    expect(host.textContent).not.toContain("option-1");
    expect(host.textContent).not.toContain("explicit_constraints");
    expect(host.textContent).not.toContain("Выбранный вариант:");
    expect(host.textContent).toContain("технический текст скрыт");
  });

  it("shows human option labels and keeps backend branch errors visible", async () => {
    const resultWithError = {
      ...compareResult,
      assistant: {
        state: "error",
        result: null,
        error: {
          code: "COMPARE_BRANCH_RESULT_INVALID",
          message: "compare branch result failed validation",
        },
      },
    };
    vi.spyOn(window, "fetch").mockResolvedValue(jsonResponse(resultWithError));
    const host = await renderSurface();
    await fillRequired(host);
    const button = Array.from(host.querySelectorAll<HTMLButtonElement>("button"))
      .find((item) => item.textContent?.includes("Совет + прогноз + сравнение"));

    await act(async () => button?.click());

    expect(host.textContent).toContain("Второй");
    expect(host.textContent).not.toContain("option-2");
    expect(host.textContent).toContain("compare branch result failed validation");
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
    expect(polling.mock.calls.every((call) => isMotionSchedulerCall(call))).toBe(true);
  });

  it("does not classify an application interval as Motion's frame scheduler", () => {
    expect(isMotionSchedulerCall([() => undefined, 50])).toBe(false);
  });
});
