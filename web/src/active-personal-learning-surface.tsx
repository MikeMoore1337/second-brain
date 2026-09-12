import { useEffect, useRef, useState, type ReactElement } from "react";

import {
  ActivePersonalLearningApiError,
  requestActiveLearningQuestions,
  type ActiveLearningCandidate,
  type ActiveLearningOption,
  type ActiveLearningResult,
} from "./active-personal-learning-api";
import { Icon } from "./icons";
import "./active-personal-learning-surface.css";

type ActiveLearningUiState =
  | "idle"
  | "loading"
  | "no-candidate"
  | "candidate"
  | "ignored"
  | "rejected"
  | "answer-handoff"
  | "cancelled"
  | "source-unavailable"
  | "invalid"
  | "error"
  | "stale";

type ActiveLearningSurfaceProps = {
  readonly query: string;
  readonly options: readonly ActiveLearningOption[];
  readonly disabled?: boolean;
};

type AnswerHandoff = {
  readonly task: string;
  readonly option: ActiveLearningOption;
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

const ERROR_TEXT: Record<string, { state: "source-unavailable" | "invalid" | "error"; message: string }> = {
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
  ACTIVE_LEARNING_RESULT_TOO_LARGE: {
    state: "error",
    message: "Результат уточнения модели превышает допустимый предел.",
  },
  ACTIVE_LEARNING_INVALID_RESPONSE: {
    state: "error",
    message: "Сервис уточнения модели вернул некорректный ответ.",
  },
};

function errorFor(caught: unknown): { state: Exclude<ActiveLearningUiState, "idle" | "loading" | "candidate" | "no-candidate" | "ignored" | "rejected" | "answer-handoff" | "stale">; message: string } {
  if (caught instanceof ActivePersonalLearningApiError) {
    return ERROR_TEXT[caught.code] ?? {
      state: "error",
      message: "Не удалось уточнить модель выбора.",
    };
  }
  if (typeof caught === "object" && caught !== null && "name" in caught && caught.name === "AbortError") {
    return { state: "cancelled", message: "Уточнение модели отменено." };
  }
  return { state: "error", message: "Не удалось уточнить модель выбора." };
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
  if (state === "candidate") return "Вопрос для уточнения готов.";
  if (state === "no-candidate") {
    return result?.no_candidate_code
      ? NO_CANDIDATE_TEXT[result.no_candidate_code] ?? "Сейчас вопрос не нужен."
      : "Сейчас вопрос не нужен.";
  }
  if (state === "ignored") return "Уточнение закрыто без сохранения ответа.";
  if (state === "rejected") return "Уточнение отклонено без сохранения ответа.";
  if (state === "answer-handoff") return "Ответ подготовлен только для следующего шага проверки.";
  if (state === "cancelled") return "Уточнение модели отменено.";
  if (state === "stale") return "Текущая задача изменилась: прежний вопрос больше не используется.";
  return "";
}

export function ActivePersonalLearningSurface({
  query,
  options,
  disabled = false,
}: ActiveLearningSurfaceProps): ReactElement {
  const [state, setState] = useState<ActiveLearningUiState>("idle");
  const [result, setResult] = useState<ActiveLearningResult | null>(null);
  const [selectedOptionId, setSelectedOptionId] = useState("");
  const [answer, setAnswer] = useState<AnswerHandoff | null>(null);
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
    setError("");
    setState(hadPreviousOperation ? "stale" : "idle");
    return () => controllerRef.current?.abort();
    // The serialized request is the bounded page-memory identity of this operation.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [requestKey]);

  useEffect(() => {
    if (state !== "idle" && state !== "loading") outputRef.current?.focus();
  }, [state]);

  async function requestQuestion(): Promise<void> {
    if (disabled || state === "loading" || state === "candidate" || state === "answer-handoff" || state === "ignored" || state === "rejected") return;
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
    setAnswer(null);
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
    if (state !== "loading") return;
    requestIdRef.current += 1;
    controllerRef.current?.abort();
    controllerRef.current = null;
    setError("");
    setState("cancelled");
  }

  function resolve(disposition: "ignored" | "rejected"): void {
    if (state !== "candidate") return;
    setResult(null);
    setSelectedOptionId("");
    setState(disposition);
  }

  function answerQuestion(): void {
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
    setResult(null);
    setAnswer({ task: candidate.task, option });
    setSelectedOptionId("");
    setState("answer-handoff");
  }

  const candidate = result?.candidate;
  const terminal = state === "candidate" || state === "answer-handoff" || state === "ignored" || state === "rejected";
  const liveText = resultStatusText(state, result);

  return (
    <section
      className="active-learning-surface"
      id="active-learning"
      aria-labelledby="active-learning-title"
      aria-busy={state === "loading"}
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
          disabled={disabled || state === "loading" || terminal}
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
              <button className="review-button review-button-primary" type="button" disabled={!selectedOptionId} onClick={answerQuestion}>Ответить</button>
              <button className="review-button review-button-secondary" type="button" onClick={() => resolve("ignored")}>Игнорировать</button>
              <button className="review-button review-button-quiet" type="button" onClick={() => resolve("rejected")}>Отклонить</button>
            </div>
            <p className="active-learning-privacy">Ответ останется только в памяти этой страницы и не станет каноническим свидетельством.</p>
          </section>
        ) : null}

        {state === "no-candidate" ? (
          <p className="active-learning-state active-learning-no-candidate">
            {result?.no_candidate_code ? NO_CANDIDATE_TEXT[result.no_candidate_code] : "Сейчас вопрос не нужен."}
          </p>
        ) : null}

        {state === "answer-handoff" && answer ? (
          <section className="active-learning-state active-learning-handoff" aria-labelledby="active-learning-handoff-title">
            <h4 id="active-learning-handoff-title">Ответ подготовлен для следующего шага.</h4>
            <dl>
              <div><dt>Задача</dt><dd>{answer.task}</dd></div>
              <div><dt>Выбранный вариант</dt><dd>{answer.option.label}</dd></div>
            </dl>
            <p>Полная проверка и возможное сохранение черновика относятся к следующему этапу; сейчас запись не выполнялась.</p>
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
