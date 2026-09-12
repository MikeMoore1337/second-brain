import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, describe, expect, it, vi } from "vitest";

import {
  ActivePersonalLearningApiError,
  requestActiveLearningQuestions,
  type ActiveLearningResult,
} from "../active-personal-learning-api";
import { ActivePersonalLearningSurface } from "../active-personal-learning-surface";

let root: Root | undefined;

afterEach(() => {
  act(() => root?.unmount());
  root = undefined;
  vi.restoreAllMocks();
  document.body.innerHTML = "";
});

const candidateResult: ActiveLearningResult = {
  status: "candidate",
  no_candidate_code: null,
  candidate: {
    contract_version: "active-personal-learning-v1",
    candidate_id: `apl1:${"0".repeat(64)}`,
    kind: "choice",
    reason_code: "conflicting_evidence",
    task: "Выбрать формат работы",
    question: "Какой вариант лучше всего описывает ваш текущий выбор сейчас?",
    options: [
      { id: "a", label: "Сфокусироваться на качестве" },
      { id: "b", label: "Сделать быстрее" },
    ],
    source: "simulate-me-abstention-v1",
    source_derivation_version: "simulate-me-v1",
    source_policy_id: "simulate-me-direct-exact-v1",
    source_policy_fingerprint: `sha256:${"1".repeat(64)}`,
    evidence_note_ids: [],
    basis_fingerprint: `sha256:${"2".repeat(64)}`,
    issued_at: "2026-09-12T10:00:00.000000+00:00",
    expires_at: "2099-01-01T00:10:00.000000+00:00",
  },
};

const noCandidateResult: ActiveLearningResult = {
  status: "no_candidate",
  candidate: null,
  no_candidate_code: "no_actionable_gap",
};

function jsonResponse(payload: unknown, status = 200): Response {
  return new Response(JSON.stringify(payload), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

async function renderSurface(query = "Выбрать формат работы"): Promise<HTMLDivElement> {
  const host = document.createElement("div");
  document.body.append(host);
  root = createRoot(host);
  await act(async () => {
    root?.render(
      <ActivePersonalLearningSurface
        query={query}
        options={candidateResult.candidate?.options ?? []}
      />,
    );
  });
  return host;
}

function actionButton(host: HTMLElement, label: string): HTMLButtonElement | undefined {
  return Array.from(host.querySelectorAll<HTMLButtonElement>("button"))
    .find((button) => button.textContent?.includes(label));
}

describe("Active Personal Learning v1 surface", () => {
  it("stays idle and makes no request until the owner clicks the entry point", async () => {
    const fetchSpy = vi.spyOn(window, "fetch");
    const host = await renderSurface();

    expect(fetchSpy).not.toHaveBeenCalled();
    expect(host.textContent).toContain("Уточнить модель");
    expect(host.querySelector("[data-active-learning-state='idle']")).not.toBeNull();
  });

  it("sends the bounded request and renders the exact question and safe reason", async () => {
    const fetchSpy = vi.spyOn(window, "fetch").mockResolvedValue(jsonResponse(candidateResult));
    const host = await renderSurface();

    await act(async () => actionButton(host, "Уточнить модель")?.click());

    expect(fetchSpy).toHaveBeenCalledOnce();
    const [path, init] = fetchSpy.mock.calls[0];
    expect(path).toBe("/api/active-learning/questions");
    expect(init?.method).toBe("POST");
    expect((init?.headers as Record<string, string>)["X-Second-Brain-Request"])
      .toBe("active-learning-v1");
    expect(JSON.parse(String(init?.body))).toEqual({
      query: "Выбрать формат работы",
      options: candidateResult.candidate?.options,
    });
    expect(host.textContent).toContain("Какой вариант лучше всего описывает ваш текущий выбор сейчас?");
    expect(host.textContent).toContain("Текущие прямые свидетельства поддерживают несколько вариантов одновременно.");
    expect(host.querySelector("[data-active-learning-state='candidate']")).not.toBeNull();
    expect(host.textContent).not.toContain("low confidence");
  });

  it("answers only in page memory and does not issue a write or second request", async () => {
    const fetchSpy = vi.spyOn(window, "fetch").mockResolvedValue(jsonResponse(candidateResult));
    const host = await renderSurface();

    await act(async () => actionButton(host, "Уточнить модель")?.click());
    const secondOption = host.querySelector<HTMLInputElement>("input[value='b']");
    await act(async () => secondOption?.click());
    await act(async () => actionButton(host, "Ответить")?.click());

    expect(fetchSpy).toHaveBeenCalledOnce();
    expect(host.querySelector("[data-active-learning-state='answer-handoff']")).not.toBeNull();
    expect(host.textContent).toContain("Сделать быстрее");
    expect(host.textContent).toContain("запись не выполнялась");
    expect(host.textContent).not.toContain("localStorage");
    expect(host.textContent).not.toContain("sessionStorage");
  });

  it.each([
    ["Игнорировать", "ignored", "Уточнение закрыто без сохранения ответа."],
    ["Отклонить", "rejected", "Уточнение отклонено без сохранения ответа."],
  ] as const)("keeps %s terminal and ephemeral", async (label, state, message) => {
    const fetchSpy = vi.spyOn(window, "fetch").mockResolvedValue(jsonResponse(candidateResult));
    const host = await renderSurface();

    await act(async () => actionButton(host, "Уточнить модель")?.click());
    await act(async () => actionButton(host, label)?.click());

    expect(fetchSpy).toHaveBeenCalledOnce();
    expect(host.querySelector(`[data-active-learning-state='${state}']`)).not.toBeNull();
    expect(host.textContent).toContain(message);
  });

  it("renders the normal no-candidate outcome without inventing a low-confidence state", async () => {
    const fetchSpy = vi.spyOn(window, "fetch").mockResolvedValue(jsonResponse(noCandidateResult));
    const host = await renderSurface();

    await act(async () => actionButton(host, "Уточнить модель")?.click());

    expect(fetchSpy).toHaveBeenCalledOnce();
    expect(host.querySelector("[data-active-learning-state='no-candidate']")).not.toBeNull();
    expect(host.textContent).toContain("Сейчас нет пробела, который нужно уточнять.");
    expect(host.textContent).not.toContain("низкая уверенность");
  });

  it("maps source and malformed errors to safe bounded UI states", async () => {
    vi.spyOn(window, "fetch").mockResolvedValue(
      jsonResponse({ error: { code: "ACTIVE_LEARNING_SOURCE_UNAVAILABLE", message: "D:\\private\\secret.md" } }, 503),
    );
    const host = await renderSurface();

    await act(async () => actionButton(host, "Уточнить модель")?.click());

    expect(host.querySelector("[data-active-learning-state='source-unavailable']")).not.toBeNull();
    expect(host.textContent).toContain("Текущая модель для уточнения недоступна.");
    expect(host.textContent).not.toContain("secret.md");
  });

  it("supports explicit cancellation", async () => {
    vi.spyOn(window, "fetch").mockImplementation((_input, init) => (
      new Promise<Response>((_resolve, reject) => {
        init?.signal?.addEventListener("abort", () => {
          reject(new DOMException("Aborted", "AbortError"));
        });
      })
    ));
    const host = await renderSurface();

    await act(async () => actionButton(host, "Уточнить модель")?.click());
    expect(host.querySelector("[data-active-learning-state='loading']")).not.toBeNull();
    await act(async () => actionButton(host, "Отменить")?.click());

    expect(host.querySelector("[data-active-learning-state='cancelled']")).not.toBeNull();
    expect(host.textContent).toContain("Уточнение модели отменено.");
  });

  it("discards a superseded response when the task changes", async () => {
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
      .mockResolvedValueOnce(jsonResponse(candidateResult));
    const host = await renderSurface();

    await act(async () => actionButton(host, "Уточнить модель")?.click());
    await act(async () => {
      root?.render(
        <ActivePersonalLearningSurface
          query="Новая задача"
          options={candidateResult.candidate?.options ?? []}
        />,
      );
    });
    expect(firstAborted).toBe(true);
    expect(host.querySelector("[data-active-learning-state='stale']")).not.toBeNull();

    await act(async () => actionButton(host, "Уточнить модель")?.click());

    expect(fetchSpy).toHaveBeenCalledTimes(2);
    expect(host.querySelector("[data-active-learning-state='candidate']")).not.toBeNull();
    expect(host.textContent).not.toContain("Новая задача");
  });
});

describe("Active Personal Learning v1 API client", () => {
  it("exposes a typed safe error without leaking the backend message", async () => {
    vi.spyOn(window, "fetch").mockResolvedValue(
      jsonResponse({ error: { code: "ACTIVE_LEARNING_INVALID_REQUEST", message: "private detail" } }, 400),
    );

    await expect(
      requestActiveLearningQuestions({ query: "Task", options: [{ id: "a", label: "A" }] }),
    ).rejects.toMatchObject({
      code: "ACTIVE_LEARNING_INVALID_REQUEST",
    } satisfies Partial<ActivePersonalLearningApiError>);
  });
});
