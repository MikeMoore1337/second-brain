import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, describe, expect, it, vi } from "vitest";

import {
  ActivePersonalLearningApiError,
  requestActiveLearningQuestions,
  type ActiveLearningAnswerCapture,
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

const answerCapture: ActiveLearningAnswerCapture = {
  task: "Выбрать формат работы",
  option: { id: "b", label: "Сделать быстрее" },
};

const answerDraft = {
  title: "Мой выбор",
  note_type: "resource",
  content: "Я выбрал сделать быстрее.",
  tags: ["choice"],
  links: [],
};

const answerReview = {
  review_token: "answer-review",
  draft: answerDraft,
  sources: [],
};

const answerPlan = {
  status: "dry-run",
  confirmation_token: "pm-confirm",
  note: { id: "note-id", type: "resource", relative_path: "30 Resources/My choice.md" },
  diff: "--- /dev/null\n+++ 30 Resources/My choice.md\n@@\n+Я выбрал сделать быстрее.",
};

const answerSaved = {
  status: "created",
  note: { id: "note-id", type: "resource", relative_path: "30 Resources/My choice.md", created: "2026-09-12T10:00:00Z" },
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

function setControlValue(
  control: HTMLInputElement | HTMLTextAreaElement | HTMLSelectElement,
  value: string,
): void {
  const prototype = control instanceof HTMLSelectElement
    ? HTMLSelectElement.prototype
    : control instanceof HTMLTextAreaElement
      ? HTMLTextAreaElement.prototype
      : HTMLInputElement.prototype;
  const setter = Object.getOwnPropertyDescriptor(prototype, "value")?.set;
  setter?.call(control, value);
  control.dispatchEvent(new Event(control instanceof HTMLSelectElement ? "change" : "input", { bubbles: true }));
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

  it("requires explicit answer resolution and opens a fully editable page-memory draft", async () => {
    const fetchSpy = vi.spyOn(window, "fetch")
      .mockResolvedValueOnce(jsonResponse(candidateResult))
      .mockResolvedValueOnce(jsonResponse({
        candidate_id: candidateResult.candidate?.candidate_id,
        disposition: "answer",
        answer_capture: answerCapture,
      }));
    const host = await renderSurface();

    await act(async () => actionButton(host, "Уточнить модель")?.click());
    const secondOption = host.querySelector<HTMLInputElement>("input[value='b']");
    await act(async () => secondOption?.click());
    await act(async () => actionButton(host, "Ответить")?.click());

    expect(fetchSpy).toHaveBeenCalledTimes(2);
    expect(fetchSpy.mock.calls[1][0]).toBe("/api/active-learning/questions/resolve");
    expect(JSON.parse(String(fetchSpy.mock.calls[1][1]?.body))).toMatchObject({
      resolution: {
        candidate_id: candidateResult.candidate?.candidate_id,
        disposition: "answer",
        selected_option_id: "b",
      },
    });
    expect(host.querySelector("[data-active-learning-state='answer-edit']")).not.toBeNull();
    expect(host.querySelector<HTMLTextAreaElement>(".active-learning-answer-content")?.value)
      .toBe("Сделать быстрее");
    expect(host.textContent).toContain("Сделать быстрее");
    expect(host.textContent).toContain("Какой вариант лучше всего описывает ваш текущий выбор сейчас?");
    expect(host.querySelector(".active-learning-answer-context")).not.toBeNull();
    expect(host.querySelector(".active-learning-metadata-confirmation")).toBeNull();
    expect(host.textContent).not.toContain("localStorage");
    expect(host.textContent).not.toContain("sessionStorage");
  });

  it("requires review, explicit metadata confirmation, exact diff, and confirmation before apply", async () => {
    const fetchSpy = vi.spyOn(window, "fetch")
      .mockResolvedValueOnce(jsonResponse(candidateResult))
      .mockResolvedValueOnce(jsonResponse({
        candidate_id: candidateResult.candidate?.candidate_id,
        disposition: "answer",
        answer_capture: answerCapture,
      }))
      .mockResolvedValueOnce(jsonResponse(answerReview))
      .mockResolvedValueOnce(jsonResponse(answerPlan))
      .mockResolvedValueOnce(jsonResponse(answerSaved));
    const host = await renderSurface();

    await act(async () => actionButton(host, "Уточнить модель")?.click());
    await act(async () => host.querySelector<HTMLInputElement>("input[value='b']")?.click());
    await act(async () => actionButton(host, "Ответить")?.click());

    const title = host.querySelector<HTMLInputElement>("#active-learning-answer-title-input");
    const tags = host.querySelector<HTMLTextAreaElement>(".active-learning-answer-fields textarea:not(.active-learning-answer-content)");
    const content = host.querySelector<HTMLTextAreaElement>(".active-learning-answer-content");
    expect(title).not.toBeNull();
    expect(tags).not.toBeNull();
    expect(content).not.toBeNull();
    if (!title || !tags || !content) return;
    await act(async () => {
      setControlValue(title, answerDraft.title);
      setControlValue(tags, answerDraft.tags.join("\n"));
      setControlValue(content, answerDraft.content);
    });
    await act(async () => actionButton(host, "Проверить ответ")?.click());

    expect(fetchSpy).toHaveBeenCalledTimes(3);
    expect(fetchSpy.mock.calls[2][0]).toBe("/api/drafts/active-learning/answer/review");
    expect(JSON.parse(String(fetchSpy.mock.calls[2][1]?.body))).toEqual({ draft: answerDraft });
    expect(host.querySelector("[data-active-learning-state='metadata-review']")).not.toBeNull();
    expect(host.querySelector(".active-learning-metadata-confirmation")).not.toBeNull();

    await act(async () => actionButton(host, "Подготовить сохранение")?.click());
    expect(fetchSpy).toHaveBeenCalledTimes(3);
    expect(host.textContent).toContain("Подтверди, что проверил выбранные Personal Memory metadata.");

    const metadataFields = host.querySelectorAll<HTMLSelectElement>(".active-learning-personal-memory .personal-memory-fields select");
    const domain = host.querySelector<HTMLInputElement>(".active-learning-personal-memory .personal-memory-fields input");
    const confirmation = host.querySelector<HTMLInputElement>(".active-learning-metadata-confirmation input");
    expect(metadataFields).toHaveLength(3);
    expect(domain).not.toBeNull();
    expect(confirmation).not.toBeNull();
    if (metadataFields.length !== 3 || !domain || !confirmation) return;
    await act(async () => {
      setControlValue(metadataFields[0], "explicit_user_fact");
      setControlValue(metadataFields[1], "preference");
      setControlValue(domain, "work");
      confirmation.click();
    });
    await act(async () => actionButton(host, "Подготовить сохранение")?.click());

    expect(fetchSpy).toHaveBeenCalledTimes(4);
    expect(fetchSpy.mock.calls[3][0]).toBe("/api/drafts/personal-memory/save/prepare");
    expect(JSON.parse(String(fetchSpy.mock.calls[3][1]?.body))).toEqual({
      review_token: "answer-review",
      draft: answerDraft,
      personal_memory: {
        evidence_kind: "explicit_user_fact",
        self_kind: "preference",
        evidence_at: "unknown",
        evidence_at_precision: "unknown",
        domain: "work",
      },
    });
    expect(host.textContent).toContain("+Я выбрал сделать быстрее.");
    expect(host.textContent).toContain("Проверь полный diff и подтверди сохранение.");
    expect(host.querySelector("[data-active-learning-state='prepared']")).not.toBeNull();

    await act(async () => actionButton(host, "Подтвердить сохранение")?.click());
    expect(fetchSpy).toHaveBeenCalledTimes(5);
    expect(fetchSpy.mock.calls[4][0]).toBe("/api/drafts/personal-memory/save/apply");
    expect(JSON.parse(String(fetchSpy.mock.calls[4][1]?.body))).toEqual({
      review_token: "answer-review",
      confirmation_token: "pm-confirm",
      draft: answerDraft,
      personal_memory: {
        evidence_kind: "explicit_user_fact",
        self_kind: "preference",
        evidence_at: "unknown",
        evidence_at_precision: "unknown",
        domain: "work",
      },
    });
    expect(host.querySelector("[data-active-learning-state='saved']")).not.toBeNull();
    expect(host.textContent).toContain("Личная память сохранена");
  });

  it("does not review or prepare an empty answer and can cancel the editable state", async () => {
    const fetchSpy = vi.spyOn(window, "fetch")
      .mockResolvedValueOnce(jsonResponse(candidateResult))
      .mockResolvedValueOnce(jsonResponse({
        candidate_id: candidateResult.candidate?.candidate_id,
        disposition: "answer",
        answer_capture: answerCapture,
      }));
    const host = await renderSurface();

    await act(async () => actionButton(host, "Уточнить модель")?.click());
    await act(async () => host.querySelector<HTMLInputElement>("input[value='b']")?.click());
    await act(async () => actionButton(host, "Ответить")?.click());
    const content = host.querySelector<HTMLTextAreaElement>(".active-learning-answer-content");
    expect(content).not.toBeNull();
    if (!content) return;
    await act(async () => setControlValue(content, ""));
    expect(actionButton(host, "Проверить ответ")?.disabled).toBe(true);
    expect(fetchSpy).toHaveBeenCalledTimes(2);

    await act(async () => actionButton(host, "Отменить ответ")?.click());
    expect(fetchSpy).toHaveBeenCalledTimes(2);
    expect(host.querySelector("[data-active-learning-state='cancelled']")).not.toBeNull();
    expect(host.textContent).toContain("Уточнение модели отменено.");
  });

  it("fails closed when the server marks the candidate stale before answer handoff", async () => {
    const fetchSpy = vi.spyOn(window, "fetch")
      .mockResolvedValueOnce(jsonResponse(candidateResult))
      .mockResolvedValueOnce(jsonResponse({ error: { code: "ACTIVE_LEARNING_CANDIDATE_STALE", message: "private detail" } }, 409));
    const host = await renderSurface();

    await act(async () => actionButton(host, "Уточнить модель")?.click());
    await act(async () => host.querySelector<HTMLInputElement>("input[value='b']")?.click());
    await act(async () => actionButton(host, "Ответить")?.click());

    expect(fetchSpy).toHaveBeenCalledTimes(2);
    expect(host.querySelector("[data-active-learning-state='stale']")).not.toBeNull();
    expect(host.textContent).toContain("Вопрос уточнения устарел");
    expect(host.querySelector(".active-learning-answer-content")).toBeNull();
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
