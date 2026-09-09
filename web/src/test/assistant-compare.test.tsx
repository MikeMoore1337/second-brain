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

function compareButton(host: HTMLDivElement): HTMLButtonElement | undefined {
  return Array.from(host.querySelectorAll<HTMLButtonElement>("button"))
    .find((item) => item.textContent?.includes("Независимый совет + прогноз + сравнение"));
}

async function submitCompare(host: HTMLDivElement): Promise<void> {
  await act(async () => compareButton(host)?.click());
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
    explanation: "Assistant and Simulate Me internal explanation must stay hidden.",
  },
};

const RELATION_CASES = [
  ["same_selected_option", "Независимый совет и прогноз вашего выбора совпали."],
  ["different_selected_options", "Независимый совет и прогноз вашего выбора указывают на разные варианты."],
  ["assistant_only_selected", "Независимый совет рекомендует вариант, а прогноз вашего выбора не смог определить вариант."],
  ["simulate_me_only_selected", "Прогноз вашего выбора определил вариант, а независимый совет не выбрал вариант."],
  ["neither_selected", "Ни независимый совет, ни прогноз вашего выбора не определили вариант."],
  ["assistant_error", "Не удалось получить независимый совет; прогноз вашего выбора сохранён отдельно."],
  ["simulate_me_error", "Не удалось получить прогноз вашего выбора; независимый совет сохранён отдельно."],
  ["both_error", "Не удалось получить ни независимый совет, ни прогноз вашего выбора."],
] as const;

const BRANCH_ERROR_CASES = [
  ["COMPARE_BRANCH_INVALID_REQUEST", "Не удалось проверить введённые данные."],
  ["COMPARE_BRANCH_CANCELLED", "Запрос был отменён."],
  ["COMPARE_BRANCH_TIMEOUT", "Сервис не ответил вовремя."],
  ["COMPARE_BRANCH_UNAVAILABLE", "Сервис сейчас недоступен."],
  ["COMPARE_BRANCH_FAILURE", "Не удалось получить результат."],
  ["COMPARE_BRANCH_MALFORMED_RESULT", "Не удалось получить корректный результат."],
  ["COMPARE_BRANCH_RESULT_TOO_LARGE", "Не удалось получить корректный результат."],
  ["COMPARE_BRANCH_RESULT_INVALID", "Не удалось получить корректный результат."],
] as const;

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

  it("renders the two user-facing perspectives and structural comparison as three separate blocks", async () => {
    vi.spyOn(window, "fetch").mockResolvedValue(jsonResponse(compareResult));
    const host = await renderSurface();
    await fillRequired(host);
    await submitCompare(host);

    const headings = Array.from(host.querySelectorAll<HTMLHeadingElement>(".stage7-result-panel h3"))
      .map((item) => item.textContent);
    expect(headings).toEqual([
      "Независимый совет",
      "Прогноз вашего выбора",
      "Структурное сравнение",
    ]);
    expect(host.textContent).toContain("Независимый совет рекомендует вариант, а прогноз вашего выбора не смог определить вариант.");
    expect(host.textContent).toContain("Независимый совет");
    expect(host.textContent).not.toContain("Assistant");
    expect(host.textContent).not.toContain("Simulate Me");
    expect(host.textContent).not.toContain("Assistant and Simulate Me internal explanation");
    expect(Array.from(host.querySelectorAll(".stage7-delta-list dt")).map((item) => item.textContent)).toEqual([
      "Независимый совет",
      "Прогноз вашего выбора",
    ]);
  });

  it("explains the Stage 7 perspectives without internal architecture names", async () => {
    const host = await renderSurface();

    expect(host.textContent).toContain("Независимый совет оценивает задачу только по данным, которые вы ввели.");
    expect(host.textContent).toContain("Прогноз вашего выбора пытается определить, какой вариант вы, вероятно, выбрали бы сами");
    expect(host.textContent).toContain("Сравнение показывает, совпали ли эти два результата.");
    expect(host.textContent).not.toContain("Прогноз моего выбора");
    expect(host.textContent).not.toContain("Assistant");
    expect(host.textContent).not.toContain("Simulate Me");
  });

  it.each(RELATION_CASES)("maps closed Compare relation %s to Russian presentation text", async (relation, expected) => {
    const result = {
      ...compareResult,
      delta: {
        ...compareResult.delta,
        relation,
        explanation: "Assistant / Simulate Me backend explanation must stay hidden.",
      },
    };
    vi.spyOn(window, "fetch").mockResolvedValue(jsonResponse(result));
    const host = await renderSurface();
    await fillRequired(host);
    await submitCompare(host);

    expect(host.textContent).toContain(expected);
    expect(host.textContent).not.toContain("Assistant");
    expect(host.textContent).not.toContain("Simulate Me");
    expect(host.textContent).not.toContain("backend explanation");
  });

  it("shows clear separate labels for Assistant and Simulate Me abstentions", async () => {
    const result = {
      ...compareResult,
      assistant: {
        state: "abstention",
        result: { ...assistantResult, kind: "abstention", abstention_code: "insufficient_basis" },
        error: null,
      },
      simulate_me: {
        state: "abstention",
        result: { kind: "abstention", selected_option: null, abstention_code: "insufficient_or_invalid_current_context" },
        error: null,
      },
      delta: {
        ...compareResult.delta,
        relation: "neither_selected",
        assistant_selected_option_id: null,
        simulate_me_selected_option_id: null,
        explanation: "internal abstention explanation",
      },
    };
    vi.spyOn(window, "fetch").mockResolvedValue(jsonResponse(result));
    const host = await renderSurface();
    await fillRequired(host);
    await submitCompare(host);

    expect(Array.from(host.querySelectorAll(".stage7-status")).map((item) => item.textContent)).toEqual([
      "Нет рекомендации",
      "Недостаточно данных",
    ]);
    expect(host.textContent).toContain("Нет рекомендации: недостаточно оснований для независимого совета.");
    expect(host.textContent).toContain("Недостаточно данных, чтобы определить ваш вероятный выбор.");
    expect(host.querySelector('[role="alert"]')).toBeNull();
    expect(host.textContent).not.toContain("Assistant");
    expect(host.textContent).not.toContain("Simulate Me");
  });

  it("keeps technical provider prose out of the UI without inferring a selection", async () => {
    const technicalResult = {
      ...assistantResult,
      kind: "recommendation",
      recommendation: "option-1",
      selected_option: null,
      rationale: ["Assistant использует Simulate Me.", "Поскольку в explicit_constraints указано ограничение."],
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
    expect(host.textContent).not.toContain("Assistant");
    expect(host.textContent).not.toContain("Simulate Me");
    expect(host.textContent).not.toContain("Выбранный вариант:");
    expect(host.textContent).toContain("технический текст скрыт");
  });

  it("shows human option labels and localizes backend branch errors", async () => {
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
    await submitCompare(host);

    expect(host.textContent).toContain("Второй");
    expect(host.textContent).not.toContain("option-2");
    expect(host.textContent).toContain("Не удалось получить корректный результат.");
    expect(host.textContent).not.toContain("compare branch result failed validation");
    expect(host.textContent).not.toContain("Assistant");
    expect(host.textContent).not.toContain("Simulate Me");
  });

  it("keeps unknown option IDs defensive without exposing the raw ID", async () => {
    const result = {
      ...compareResult,
      simulate_me: {
        ...compareResult.simulate_me,
        result: {
          kind: "prediction",
          selected_option: { id: "option-99", label: "Внутренняя подпись" },
          abstention_code: null,
        },
      },
      delta: {
        ...compareResult.delta,
        relation: "simulate_me_only_selected",
        simulate_me_selected_option_id: "option-99",
        explanation: "unknown option-99 selected by Simulate Me",
      },
    };
    vi.spyOn(window, "fetch").mockResolvedValue(jsonResponse(result));
    const host = await renderSurface();
    await fillRequired(host);
    await submitCompare(host);

    expect(host.textContent).toContain("неизвестный вариант");
    expect(host.textContent).toContain("Технические идентификаторы структурного результата скрыты");
    expect(host.textContent).not.toContain("option-99");
    expect(host.textContent).not.toContain("Внутренняя подпись");
    expect(host.textContent).not.toContain("Assistant");
    expect(host.textContent).not.toContain("Simulate Me");
  });

  it.each(BRANCH_ERROR_CASES)("maps branch error %s without exposing backend prose", async (code, expected) => {
    const result = {
      ...compareResult,
      assistant: {
        state: "error",
        result: null,
        error: { code, message: "compare branch result failed validation" },
      },
      delta: {
        ...compareResult.delta,
        relation: "assistant_error",
        explanation: "internal relation explanation",
      },
    };
    vi.spyOn(window, "fetch").mockResolvedValue(jsonResponse(result));
    const host = await renderSurface();
    await fillRequired(host);
    await submitCompare(host);

    expect(host.textContent).toContain(expected);
    expect(host.textContent).not.toContain("compare branch result failed validation");
    expect(host.textContent).not.toContain("Assistant");
    expect(host.textContent).not.toContain("Simulate Me");
  });

  it("maps top-level API errors instead of rendering raw backend English", async () => {
    vi.spyOn(window, "fetch").mockResolvedValue(new Response(
      JSON.stringify({ error: { code: "COMPARE_COMPOSITION_INVALID", message: "compare composition failed validation" } }),
      { status: 500, headers: { "Content-Type": "application/json" } },
    ));
    const host = await renderSurface();
    await fillRequired(host);
    await submitCompare(host);

    expect(host.textContent).toContain("Не удалось получить корректный результат.");
    expect(host.textContent).not.toContain("compare composition failed validation");
  });

  it("fails safely for an unknown relation without exposing its internal value", async () => {
    const result = {
      ...compareResult,
      delta: {
        ...compareResult.delta,
        relation: "future_internal_relation",
        explanation: "Assistant / Simulate Me future explanation",
      },
    };
    vi.spyOn(window, "fetch").mockResolvedValue(jsonResponse(result));
    const host = await renderSurface();
    await fillRequired(host);
    await submitCompare(host);

    expect(host.textContent).toContain("Не удалось определить, как соотносятся результаты.");
    expect(host.textContent).not.toContain("future_internal_relation");
    expect(host.textContent).not.toContain("Assistant");
    expect(host.textContent).not.toContain("Simulate Me");
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
    expect(host.textContent).toContain("Независимый совет рекомендует вариант, а прогноз вашего выбора не смог определить вариант.");

    await act(async () => {
      resolveFirst?.(jsonResponse({ ...assistantResult, rationale: ["Устаревший результат"] }));
      await Promise.resolve();
    });

    expect(host.textContent).not.toContain("Устаревший результат");
    expect(host.textContent).toContain("Независимый совет рекомендует вариант, а прогноз вашего выбора не смог определить вариант.");
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
