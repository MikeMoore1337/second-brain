import { useEffect, useRef, useState, type ReactElement } from "react";

import {
  applyPersonalMemory,
  preparePersonalMemory,
  reviewActiveLearningAnswer,
  type NoteDraft,
  type PersonalMemoryPayload,
  type SavePlanResponse,
  type SavedNoteResponse,
} from "./api";
import {
  ActivePersonalLearningApiError,
  requestActiveLearningQuestions,
  resolveActiveLearningQuestion,
  type ActiveLearningAnswerCapture,
  type ActiveLearningCandidate,
  type ActiveLearningOption,
  type ActiveLearningResult,
} from "./active-personal-learning-api";
import {
  PersonalMemoryMetadataFields,
  type PersonalMemoryFieldValues,
  type PersonalMemoryTimeMode,
} from "./personal-memory-metadata-fields";
import { Icon } from "./icons";
import { presentCode, presentError } from "./presentation";
import "./active-personal-learning-surface.css";

type ActiveLearningUiState =
  | "idle"
  | "loading"
  | "no-candidate"
  | "candidate"
  | "resolving"
  | "answer-edit"
  | "reviewing"
  | "metadata-review"
  | "preparing"
  | "prepared"
  | "applying"
  | "saved"
  | "ignored"
  | "rejected"
  | "cancelled"
  | "source-unavailable"
  | "invalid"
  | "error"
  | "stale";

type ErrorUiState = "source-unavailable" | "invalid" | "error" | "stale";

type AnswerContext = ActiveLearningAnswerCapture & {
  readonly question: string;
};

type ActiveLearningSurfaceProps = {
  readonly query: string;
  readonly options: readonly ActiveLearningOption[];
  readonly disabled?: boolean;
};

const EMPTY_PERSONAL_MEMORY: PersonalMemoryFieldValues = {
  evidenceKind: "",
  selfKind: "",
  timeMode: "unknown",
  evidenceAt: "unknown",
  domain: "",
};

const REASON_EXPLANATIONS: Record<ActiveLearningCandidate["reason_code"], string> = {
  missing_evidence: "В текущей модели нет прямого или контекстного подтверждения ни одного варианта.",
  conflicting_evidence: "Текущие прямые свидетельства поддерживают несколько вариантов одновременно.",
  insufficient_evidence: "Есть только контекстное свидетельство; прямого подтверждения выбора нет.",
};

const NO_CANDIDATE_TEXT: Record<string, string> = {
  questions_disabled: "Уточнение сейчас отключено для этого запроса.",
  no_actionable_gap: "Сейчас нет пробела, который нужно уточнять.",
  rate_limited: "Для этой операции уже был показан один вопрос.",
};

const ERROR_TEXT: Record<string, { state: ErrorUiState; message: string }> = {
  ACTIVE_LEARNING_INVALID_REQUEST: {
    state: "invalid",
    message: "Запрос уточнения модели не прошёл проверку.",
  },
  ACTIVE_LEARNING_CONTENT_TOO_LARGE: {
    state: "invalid",
    message: "Запрос уточнения модели слишком велик.",
  },
  ACTIVE_LEARNING_SOURCE_INVALID: {
    state: "source-unavailable",
    message: "Текущая модель для уточнения недоступна.",
  },
  ACTIVE_LEARNING_SOURCE_UNAVAILABLE: {
    state: "source-unavailable",
    message: "Текущая модель для уточнения недоступна.",
  },
  ACTIVE_LEARNING_CANDIDATE_STALE: {
    state: "stale",
    message: "Вопрос уточнения устарел и больше не используется.",
  },
  ACTIVE_LEARNING_CANDIDATE_EXPIRED: {
    state: "stale",
    message: "Срок действия вопроса уточнения истёк.",
  },
  ACTIVE_LEARNING_CANDIDATE_ALREADY_RESOLVED: {
    state: "invalid",
    message: "Вопрос уточнения уже закрыт.",
  },
  ACTIVE_LEARNING_INVALID_ANSWER: {
    state: "invalid",
    message: "Ответ на вопрос уточнения не прошёл проверку.",
  },
  ACTIVE_LEARNING_RESULT_TOO_LARGE: {
    state: "error",
    message: "Результат уточнения модели превышает допустимый предел.",
  },
  ACTIVE_LEARNING_INVALID_RESPONSE: {
    state: "error",
    message: "Сервис уточнения модели вернул некорректный ответ.",
  },
};

function errorFor(caught: unknown): { state: ErrorUiState; message: string } {
  if (caught instanceof ActivePersonalLearningApiError) {
    return ERROR_TEXT[caught.code] ?? {
      state: "error",
      message: "Не удалось уточнить модель выбора.",
    };
  }
  if (typeof caught === "object" && caught !== null && "name" in caught && caught.name === "AbortError") {
    return { state: "stale", message: "Уточнение модели отменено." };
  }
  return { state: "error", message: "Не удалось уточнить модель выбора." };
}

function answerError(caught: unknown, fallback: string): string {
  if (caught instanceof ActivePersonalLearningApiError) {
    return ERROR_TEXT[caught.code]?.message ?? fallback;
  }
  return presentError(caught, fallback);
}

function requestOptions(options: readonly ActiveLearningOption[]): ActiveLearningOption[] {
  return options.map((option) => ({ id: option.id, label: option.label }));
}

function candidateIsUsable(query: string, options: readonly ActiveLearningOption[]): boolean {
  return Boolean(query.trim())
    && options.length >= 1
    && options.length <= 8
    && options.every((option) => option.id.trim() && option.label.trim());
}

function resultStatusText(state: ActiveLearningUiState, result: ActiveLearningResult | null): string {
  if (state === "loading") return "Проверяю текущую модель выбора…";
  if (state === "resolving") return "Проверяю, что вопрос ещё соответствует текущей модели…";
  if (state === "reviewing") return "Проверяю финальный текст ответа…";
  if (state === "preparing") return "Готовлю пробное сохранение без записи…";
  if (state === "applying") return "Сохраняю личную память через безопасное сохранение…";
  if (state === "candidate") return "Вопрос для уточнения готов.";
  if (state === "no-candidate") {
    return result?.no_candidate_code
      ? NO_CANDIDATE_TEXT[result.no_candidate_code] ?? "Сейчас вопрос не нужен."
      : "Сейчас вопрос не нужен.";
  }
  if (state === "ignored") return "Уточнение закрыто без сохранения ответа.";
  if (state === "rejected") return "Уточнение отклонено без сохранения ответа.";
  if (state === "answer-edit") return "Ответ можно отредактировать перед проверкой.";
  if (state === "metadata-review") return "Ответ проверен. Проверь метаданные личной памяти.";
  if (state === "prepared") return "Проверь полный список изменений и подтверди сохранение.";
  if (state === "saved") return "Личная память сохранена через безопасное сохранение.";
  if (state === "cancelled") return "Уточнение модели отменено.";
  if (state === "stale") return "Текущая задача изменилась: прежний вопрос больше не используется.";
  return "";
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function isNoteDraft(value: unknown): value is NoteDraft {
  return isRecord(value)
    && typeof value.title === "string"
    && typeof value.note_type === "string"
    && typeof value.content === "string"
    && Array.isArray(value.tags)
    && value.tags.every((item) => typeof item === "string")
    && Array.isArray(value.links)
    && value.links.every((item) => typeof item === "string");
}

function isReviewResponse(value: unknown): value is { readonly draft: NoteDraft; readonly review_token: string } {
  return isRecord(value)
    && typeof value.review_token === "string"
    && isNoteDraft(value.draft)
    && Array.isArray(value.sources);
}

function AnswerSavePlan({ plan }: { plan: SavePlanResponse }): ReactElement {
  return (
    <section className="save-plan active-learning-save-plan" tabIndex={-1}>
      <h4>План безопасного сохранения личной памяти (без записи)</h4>
      <p className="save-plan-explanation">Это пробная подготовка. Ничего не записано; перед применением проверь полный список изменений.</p>
      <dl className="draft-fields">
        <div className="draft-field"><dt className="draft-field-label">Тип</dt><dd className="draft-field-value">{presentCode(plan.note.type, "Тип не указан")}</dd></div>
        <div className="draft-field"><dt className="draft-field-label">Путь</dt><dd className="draft-field-value">{plan.note.relative_path ?? "—"}</dd></div>
      </dl>
      <h5>Предлагаемый Markdown-файл</h5>
      <pre className="save-diff" tabIndex={0} aria-label="Полный список изменений предлагаемого файла">{plan.diff}</pre>
    </section>
  );
}

function AnswerSavedNote({ payload }: { payload: SavedNoteResponse }): ReactElement {
  return (
    <section className="saved-note active-learning-saved-note" tabIndex={-1}>
      <h4>Личная память сохранена</h4>
      <dl className="draft-fields">
        <div className="draft-field"><dt className="draft-field-label">Путь</dt><dd className="draft-field-value">{payload.note.relative_path ?? "—"}</dd></div>
        <div className="draft-field"><dt className="draft-field-label">Идентификатор</dt><dd className="draft-field-value">{payload.note.id}</dd></div>
        <div className="draft-field"><dt className="draft-field-label">Создано</dt><dd className="draft-field-value">{payload.note.created ?? "—"}</dd></div>
      </dl>
    </section>
  );
}

export function ActivePersonalLearningSurface({
  query,
  options,
  disabled = false,
}: ActiveLearningSurfaceProps): ReactElement {
  const [state, setState] = useState<ActiveLearningUiState>("idle");
  const [result, setResult] = useState<ActiveLearningResult | null>(null);
  const [selectedOptionId, setSelectedOptionId] = useState("");
  const [answer, setAnswer] = useState<AnswerContext | null>(null);
  const [draft, setDraft] = useState<NoteDraft | null>(null);
  const [reviewToken, setReviewToken] = useState<string | null>(null);
  const [memoryFields, setMemoryFields] = useState<PersonalMemoryFieldValues>(EMPTY_PERSONAL_MEMORY);
  const [metadataConfirmed, setMetadataConfirmed] = useState(false);
  const [plan, setPlan] = useState<SavePlanResponse | null>(null);
  const [confirmationToken, setConfirmationToken] = useState<string | null>(null);
  const [saved, setSaved] = useState<SavedNoteResponse | null>(null);
  const [error, setError] = useState("");
  const controllerRef = useRef<AbortController | null>(null);
  const requestIdRef = useRef(0);
  const outputRef = useRef<HTMLDivElement>(null);
  const requestKey = JSON.stringify({ query, options });

  useEffect(() => {
    const hadPreviousOperation = requestIdRef.current > 0 || state !== "idle";
    controllerRef.current?.abort();
    requestIdRef.current += 1;
    setResult(null);
    setSelectedOptionId("");
    setAnswer(null);
    setDraft(null);
    setReviewToken(null);
    setMemoryFields(EMPTY_PERSONAL_MEMORY);
    setMetadataConfirmed(false);
    setPlan(null);
    setConfirmationToken(null);
    setSaved(null);
    setError("");
    setState(hadPreviousOperation ? "stale" : "idle");
    return () => controllerRef.current?.abort();
    // The serialized request is the bounded page-memory identity of this operation.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [requestKey]);

  useEffect(() => {
    if (state !== "idle" && state !== "loading") outputRef.current?.focus();
  }, [state]);

  function clearAnswerFlow(): void {
    setResult(null);
    setSelectedOptionId("");
    setAnswer(null);
    setDraft(null);
    setReviewToken(null);
    setMemoryFields(EMPTY_PERSONAL_MEMORY);
    setMetadataConfirmed(false);
    setPlan(null);
    setConfirmationToken(null);
    setSaved(null);
  }

  async function requestQuestion(): Promise<void> {
    const terminal = [
      "candidate",
      "resolving",
      "answer-edit",
      "reviewing",
      "metadata-review",
      "preparing",
      "prepared",
      "applying",
      "saved",
      "ignored",
      "rejected",
    ].includes(state);
    if (disabled || busyState(state) || terminal) return;
    if (!candidateIsUsable(query, options)) {
      setResult(null);
      setError("Сначала укажи задачу и хотя бы один непустой вариант.");
      setState("invalid");
      return;
    }
    controllerRef.current?.abort();
    const requestId = requestIdRef.current + 1;
    requestIdRef.current = requestId;
    const controller = new AbortController();
    controllerRef.current = controller;
    setState("loading");
    setResult(null);
    setSelectedOptionId("");
    clearAnswerFlow();
    setError("");
    try {
      const next = await requestActiveLearningQuestions(
        { query, options: requestOptions(options) },
        controller.signal,
      );
      if (requestId !== requestIdRef.current) return;
      setResult(next);
      setState(next.status === "candidate" ? "candidate" : "no-candidate");
    } catch (caught) {
      if (requestId !== requestIdRef.current) return;
      const nextError = errorFor(caught);
      setError(nextError.message);
      setState(nextError.state);
    } finally {
      if (requestId === requestIdRef.current) controllerRef.current = null;
    }
  }

  function cancel(): void {
    if (state === "idle" || state === "saved") return;
    requestIdRef.current += 1;
    controllerRef.current?.abort();
    controllerRef.current = null;
    clearAnswerFlow();
    setError("");
    setState("cancelled");
  }

  function resolveLocally(disposition: "ignored" | "rejected"): void {
    if (state !== "candidate") return;
    setResult(null);
    setSelectedOptionId("");
    setState(disposition);
  }

  async function answerQuestion(): Promise<void> {
    const candidate = result?.candidate;
    if (state !== "candidate" || !candidate || !selectedOptionId) return;
    if (!Number.isFinite(Date.parse(candidate.expires_at)) || Date.parse(candidate.expires_at) <= Date.now()) {
      setResult(null);
      setSelectedOptionId("");
      setError("Вопрос уточнения устарел и больше не используется.");
      setState("stale");
      return;
    }
    const option = candidate.options.find((item) => item.id === selectedOptionId);
    if (!option) {
      setError("Выбери один из вариантов вопроса.");
      setState("invalid");
      return;
    }
    controllerRef.current?.abort();
    const requestId = requestIdRef.current + 1;
    requestIdRef.current = requestId;
    const controller = new AbortController();
    controllerRef.current = controller;
    setState("resolving");
    setResult(null);
    setSelectedOptionId("");
    setError("");
    try {
      const next = await resolveActiveLearningQuestion(candidate, option.id, controller.signal);
      if (requestId !== requestIdRef.current) return;
      if (
        next.candidate_id !== candidate.candidate_id
        || next.disposition !== "answer"
        || !next.answer_capture
        || next.answer_capture.option.id !== option.id
      ) {
        throw new ActivePersonalLearningApiError(
          "ACTIVE_LEARNING_INVALID_RESPONSE",
          "Сервис уточнения модели вернул некорректный ответ.",
        );
      }
      const capture = next.answer_capture;
      setAnswer({ ...capture, question: candidate.question });
      setDraft({
        title: "",
        note_type: "resource",
        content: capture.option.label,
        tags: [],
        links: [],
      });
      setState("answer-edit");
    } catch (caught) {
      if (requestId !== requestIdRef.current) return;
      const nextError = errorFor(caught);
      setError(nextError.message);
      setState(nextError.state);
    } finally {
      if (requestId === requestIdRef.current) controllerRef.current = null;
    }
  }

  function updateDraft<K extends keyof NoteDraft>(key: K, value: NoteDraft[K]): void {
    setDraft((current) => current ? { ...current, [key]: value } : current);
    if (state !== "saved") {
      setReviewToken(null);
      setPlan(null);
      setConfirmationToken(null);
      setMetadataConfirmed(false);
      setState("answer-edit");
      setError("");
    }
  }

  function updateMemoryFields(update: () => void): void {
    update();
    if (state !== "saved") {
      setPlan(null);
      setConfirmationToken(null);
      setMetadataConfirmed(false);
      setState("metadata-review");
      setError("");
    }
  }

  function personalMemoryPayload(): PersonalMemoryPayload {
    return {
      evidence_kind: memoryFields.evidenceKind,
      self_kind: memoryFields.selfKind,
      evidence_at: memoryFields.timeMode === "exact" ? memoryFields.evidenceAt : "unknown",
      evidence_at_precision: memoryFields.timeMode,
      domain: memoryFields.domain.trim() ? memoryFields.domain : null,
    };
  }

  async function reviewAnswer(): Promise<void> {
    if (state !== "answer-edit" || !draft || !answer) return;
    if (!draft.title.trim() || !draft.content.trim()) {
      setError("Заполни название и непустое содержание ответа перед проверкой.");
      return;
    }
    controllerRef.current?.abort();
    const requestId = requestIdRef.current + 1;
    requestIdRef.current = requestId;
    const controller = new AbortController();
    controllerRef.current = controller;
    setState("reviewing");
    setError("");
    try {
      const next: unknown = await reviewActiveLearningAnswer(draft, undefined, controller.signal);
      if (requestId !== requestIdRef.current) return;
      if (!isReviewResponse(next)) {
        throw new ActivePersonalLearningApiError(
          "ACTIVE_LEARNING_INVALID_RESPONSE",
          "Сервис проверки ответа вернул некорректный ответ.",
        );
      }
      setDraft(next.draft);
      setReviewToken(next.review_token);
      setMemoryFields(EMPTY_PERSONAL_MEMORY);
      setMetadataConfirmed(false);
      setPlan(null);
      setConfirmationToken(null);
      setState("metadata-review");
    } catch (caught) {
      if (requestId !== requestIdRef.current) return;
      setError(answerError(caught, "Не удалось проверить ответ."));
      setState("answer-edit");
    } finally {
      if (requestId === requestIdRef.current) controllerRef.current = null;
    }
  }

  async function prepareAnswer(): Promise<void> {
    if (!draft || !reviewToken || !answer || (state !== "metadata-review" && state !== "prepared")) return;
    const memory = personalMemoryPayload();
    if (!metadataConfirmed) {
      setError("Подтверди, что проверил выбранные метаданные личной памяти.");
      return;
    }
    if (!memory.evidence_kind || !memory.self_kind || (memory.evidence_at_precision === "exact" && !memory.evidence_at)) {
      setError("Заполни обязательные поля метаданных личной памяти.");
      return;
    }
    const requestId = requestIdRef.current;
    setState("preparing");
    setError("");
    try {
      const nextPlan = await preparePersonalMemory(reviewToken, draft, memory);
      if (requestId !== requestIdRef.current) return;
      setPlan(nextPlan);
      setConfirmationToken(nextPlan.confirmation_token);
      setState("prepared");
    } catch (caught) {
      if (requestId !== requestIdRef.current) return;
      setError(answerError(caught, "Не удалось подготовить сохранение."));
      setState("metadata-review");
    }
  }

  async function confirmAnswer(): Promise<void> {
    if (!draft || !reviewToken || !confirmationToken || !answer || state !== "prepared") return;
    const requestId = requestIdRef.current;
    const memory = personalMemoryPayload();
    setState("applying");
    setError("");
    try {
      const next = await applyPersonalMemory(reviewToken, confirmationToken, draft, memory);
      if (requestId !== requestIdRef.current) return;
      setSaved(next);
      setConfirmationToken(null);
      setState("saved");
    } catch (caught) {
      if (requestId !== requestIdRef.current) return;
      setError(answerError(caught, "Не удалось сохранить личную память."));
      setState("prepared");
    }
  }

  const candidate = result?.candidate;
  const busy = busyState(state);
  const terminal = [
    "candidate",
    "resolving",
    "answer-edit",
    "reviewing",
    "metadata-review",
    "preparing",
    "prepared",
    "applying",
    "saved",
    "ignored",
    "rejected",
  ].includes(state);
  const answerEditorVisible = Boolean(answer && draft) && [
    "answer-edit",
    "reviewing",
    "metadata-review",
    "preparing",
    "prepared",
    "applying",
    "saved",
  ].includes(state);
  const metadataVisible = answerEditorVisible && [
    "metadata-review",
    "preparing",
    "prepared",
    "applying",
    "saved",
  ].includes(state);
  const liveText = resultStatusText(state, result);

  return (
    <section
      className="active-learning-surface"
      id="active-learning"
      aria-labelledby="active-learning-title"
      aria-busy={busy}
      data-active-learning-state={state}
    >
      <div className="active-learning-entry">
        <div>
          <h3 id="active-learning-title">Уточнить модель выбора.</h3>
          <p>Один явный вопрос по текущей задаче и вариантам — без записи в память.</p>
        </div>
        <button
          className="review-button review-button-secondary"
          type="button"
          disabled={disabled || busy || terminal}
          aria-busy={state === "loading"}
          aria-controls="active-learning-output"
          onClick={() => void requestQuestion()}
        >
          <Icon name="info" size={18} aria-hidden="true" />
          {state === "loading" ? "Уточняю…" : "Уточнить модель"}
        </button>
      </div>

      <div className="active-learning-live" role="status" aria-live="polite">{liveText}</div>

      <div ref={outputRef} id="active-learning-output" className="active-learning-output" tabIndex={-1}>
        {state === "loading" ? (
          <div className="active-learning-loading" aria-label="Идёт уточнение модели">
            <span className="active-learning-loading-mark" aria-hidden="true" />
            <p>Читаю только текущий серверный снимок модели и прогноза…</p>
            <button className="review-button review-button-quiet" type="button" onClick={cancel}>Отменить</button>
          </div>
        ) : null}

        {state === "resolving" ? (
          <div className="active-learning-loading" aria-label="Проверяю вопрос перед ответом">
            <span className="active-learning-loading-mark" aria-hidden="true" />
            <p>Проверяю актуальность вопроса перед открытием ответа для редактирования…</p>
            <button className="review-button review-button-quiet" type="button" onClick={cancel}>Отменить</button>
          </div>
        ) : null}

        {state === "candidate" && candidate ? (
          <section className="active-learning-question" aria-labelledby="active-learning-question-title">
            <div className="active-learning-question-heading">
              <Icon name="info" size={20} aria-hidden="true" />
              <div>
                <p className="active-learning-label">Вопрос по текущему выбору</p>
                <h4 id="active-learning-question-title">{candidate.question}</h4>
              </div>
            </div>
            <p className="active-learning-reason">{REASON_EXPLANATIONS[candidate.reason_code]}</p>
            <fieldset className="active-learning-options">
              <legend>Выбери вариант</legend>
              {candidate.options.map((option) => (
                <label className="active-learning-option" key={option.id}>
                  <input
                    type="radio"
                    name="active-learning-option"
                    value={option.id}
                    checked={selectedOptionId === option.id}
                    onChange={() => setSelectedOptionId(option.id)}
                  />
                  <span>{option.label}</span>
                </label>
              ))}
            </fieldset>
            <div className="active-learning-actions">
              <button className="review-button review-button-primary" type="button" disabled={!selectedOptionId} onClick={() => void answerQuestion()}>Ответить</button>
              <button className="review-button review-button-secondary" type="button" onClick={() => resolveLocally("ignored")}>Игнорировать</button>
              <button className="review-button review-button-quiet" type="button" onClick={() => resolveLocally("rejected")}>Отклонить</button>
            </div>
            <p className="active-learning-privacy">Ответ останется только в памяти этой страницы и не станет каноническим свидетельством.</p>
          </section>
        ) : null}

        {state === "no-candidate" ? (
          <p className="active-learning-state active-learning-no-candidate">
            {result?.no_candidate_code ? NO_CANDIDATE_TEXT[result.no_candidate_code] : "Сейчас вопрос не нужен."}
          </p>
        ) : null}

        {answerEditorVisible && answer && draft ? (
          <section className="active-learning-answer-flow" aria-labelledby="active-learning-answer-title">
            <section className="active-learning-answer-context" aria-labelledby="active-learning-answer-context-title">
              <h4 id="active-learning-answer-context-title">Контекст вопроса</h4>
              <dl>
                <div><dt>Задача</dt><dd>{answer.task}</dd></div>
                <div><dt>Вопрос</dt><dd>{answer.question}</dd></div>
                <div><dt>Выбранный вариант</dt><dd>{answer.option.label}</dd></div>
              </dl>
              <p>Контекст помогает проверить смысл, но сам по себе не попадёт в каноническую заметку.</p>
            </section>
            <section className="active-learning-answer-editor" aria-busy={busy}>
              <h4 id="active-learning-answer-title">Проверь и отредактируй ответ</h4>
              <p className="active-learning-answer-guidance">Ответ ниже — предложенный текст для редактирования. Оставь только тот текст, который действительно хочешь сохранить.</p>
              <div className="active-learning-answer-fields">
                <label className="review-field">
                  <span className="draft-field-label">Название</span>
                  <input className="review-input" id="active-learning-answer-title-input" type="text" value={draft.title} disabled={busy || state === "saved"} onChange={(event) => updateDraft("title", event.target.value)} />
                </label>
                <label className="review-field">
                  <span className="draft-field-label">Тип заметки</span>
                  <select className="review-input" value={draft.note_type} disabled={busy || state === "saved"} onChange={(event) => updateDraft("note_type", event.target.value)}>
                    <option value="project">Проект</option>
                    <option value="area">Область</option>
                    <option value="resource">Ресурс</option>
                    <option value="zettel">Зеттель</option>
                  </select>
                </label>
                <label className="review-field">
                  <span className="draft-field-label">Теги (один на строку)</span>
                  <textarea className="review-input" rows={3} value={draft.tags.join("\n")} disabled={busy || state === "saved"} onChange={(event) => updateDraft("tags", event.target.value.split(/\r?\n/).filter((item) => item.length > 0))} />
                </label>
                <label className="review-field">
                  <span className="draft-field-label">Ссылки (одна на строку)</span>
                  <textarea className="review-input" rows={3} value={draft.links.join("\n")} disabled={busy || state === "saved"} onChange={(event) => updateDraft("links", event.target.value.split(/\r?\n/).filter((item) => item.length > 0))} />
                </label>
                <label className="review-field review-field-wide">
                  <span className="draft-field-label">Содержание ответа</span>
                  <textarea className="review-input active-learning-answer-content" rows={12} value={draft.content} disabled={busy || state === "saved"} onChange={(event) => updateDraft("content", event.target.value)} />
                </label>
              </div>
              {state === "answer-edit" || state === "reviewing" ? (
                <div className="active-learning-actions">
                  <button className="review-button review-button-primary" type="button" disabled={busy || !draft.title.trim() || !draft.content.trim()} aria-busy={state === "reviewing"} onClick={() => void reviewAnswer()}>
                    {state === "reviewing" ? "Проверяю…" : "Проверить ответ"}
                  </button>
                  <button className="review-button review-button-quiet" type="button" disabled={busy} onClick={cancel}>Отменить ответ</button>
                </div>
              ) : null}
              {metadataVisible ? (
                <section className="personal-memory-panel active-learning-personal-memory" aria-labelledby="active-learning-personal-memory-title">
                  <h5 id="active-learning-personal-memory-title">Метаданные личной памяти</h5>
                  <p className="personal-memory-description">Ничего не классифицируется автоматически. Выбери и проверь каждое значение перед подготовкой.</p>
                  <PersonalMemoryMetadataFields
                    idPrefix="active-learning-personal-memory"
                    disabled={busy || state === "saved"}
                    values={memoryFields}
                    onEvidenceKindChange={(value) => updateMemoryFields(() => setMemoryFields((current) => ({ ...current, evidenceKind: value })))}
                    onSelfKindChange={(value) => updateMemoryFields(() => setMemoryFields((current) => ({ ...current, selfKind: value })))}
                    onDomainChange={(value) => updateMemoryFields(() => setMemoryFields((current) => ({ ...current, domain: value })))}
                    onTimeModeChange={(value: PersonalMemoryTimeMode) => updateMemoryFields(() => setMemoryFields((current) => ({ ...current, timeMode: value, evidenceAt: value === "exact" ? "" : "unknown" })))}
                    onEvidenceAtChange={(value) => updateMemoryFields(() => setMemoryFields((current) => ({ ...current, evidenceAt: value })))}
                    onNow={() => updateMemoryFields(() => setMemoryFields((current) => ({ ...current, timeMode: "exact", evidenceAt: new Date().toISOString() })))}
                  />
                  <label className="active-learning-metadata-confirmation">
                    <input type="checkbox" checked={metadataConfirmed} disabled={busy || state === "saved"} onChange={(event) => { setMetadataConfirmed(event.target.checked); setPlan(null); setConfirmationToken(null); if (!event.target.checked && state !== "saved") setState("metadata-review"); }} />
                    <span>Я проверил метаданные личной памяти и понимаю, что это станет каноническим свидетельством после подтверждения.</span>
                  </label>
                  <div className="active-learning-actions">
                    <button className="review-button review-button-primary" type="button" disabled={busy || state === "saved"} aria-busy={state === "preparing"} onClick={() => void prepareAnswer()}>
                      {state === "preparing" ? "Подготавливаю…" : "Подготовить сохранение"}
                    </button>
                    {confirmationToken ? <button className="review-button review-button-primary" type="button" disabled={busy || state === "saved"} aria-busy={state === "applying"} onClick={() => void confirmAnswer()}>{state === "applying" ? "Сохраняю…" : "Подтвердить сохранение"}</button> : null}
                    <button className="review-button review-button-quiet" type="button" disabled={busy || state === "saved"} onClick={cancel}>Отменить ответ</button>
                  </div>
                </section>
              ) : null}
              {plan ? <AnswerSavePlan plan={plan} /> : null}
              {saved ? <AnswerSavedNote payload={saved} /> : null}
            </section>
          </section>
        ) : null}

        {state === "ignored" || state === "rejected" ? (
          <p className="active-learning-state">{liveText}</p>
        ) : null}

        {state === "cancelled" || state === "stale" ? (
          <p className="active-learning-state">{liveText}</p>
        ) : null}

        {error ? <p className="active-learning-error" role="alert">{error}</p> : null}
      </div>
    </section>
  );
}

function busyState(state: ActiveLearningUiState): boolean {
  return ["loading", "resolving", "reviewing", "preparing", "applying"].includes(state);
}
