export type ActiveLearningOption = {
  readonly id: string;
  readonly label: string;
};

export type ActiveLearningReasonCode =
  | "missing_evidence"
  | "conflicting_evidence"
  | "insufficient_evidence";

export type ActiveLearningNoCandidateCode =
  | "questions_disabled"
  | "no_actionable_gap"
  | "rate_limited";

export type ActiveLearningRequest = {
  readonly query: string;
  readonly options: readonly ActiveLearningOption[];
};

export type ActiveLearningCandidate = {
  readonly contract_version: "active-personal-learning-v1";
  readonly candidate_id: string;
  readonly kind: "choice";
  readonly reason_code: ActiveLearningReasonCode;
  readonly task: string;
  readonly question: string;
  readonly options: readonly ActiveLearningOption[];
  readonly source: string;
  readonly source_derivation_version: string;
  readonly source_policy_id: string;
  readonly source_policy_fingerprint: string;
  readonly evidence_note_ids: readonly string[];
  readonly basis_fingerprint: string;
  readonly issued_at: string;
  readonly expires_at: string;
};

export type ActiveLearningResult = {
  readonly status: "candidate" | "no_candidate";
  readonly candidate: ActiveLearningCandidate | null;
  readonly no_candidate_code: ActiveLearningNoCandidateCode | null;
};

export type ActiveLearningAnswerCapture = {
  readonly task: string;
  readonly option: ActiveLearningOption;
};

export type ActiveLearningResolution = {
  readonly candidate_id: string;
  readonly disposition: "answer" | "ignore" | "reject";
  readonly answer_capture: ActiveLearningAnswerCapture | null;
};

type ErrorEnvelope = { error?: { code?: unknown; message?: unknown } };

export class ActivePersonalLearningApiError extends Error {
  readonly code: string;

  constructor(code: string, message: string) {
    super(message);
    this.name = "ActivePersonalLearningApiError";
    this.code = code;
  }
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function isOption(value: unknown): value is ActiveLearningOption {
  return isRecord(value) && typeof value.id === "string" && typeof value.label === "string";
}

function parseResult(payload: unknown): ActiveLearningResult {
  if (!isRecord(payload) || (payload.status !== "candidate" && payload.status !== "no_candidate")) {
    throw new ActivePersonalLearningApiError(
      "ACTIVE_LEARNING_INVALID_RESPONSE",
      "Сервис уточнения модели вернул некорректный ответ.",
    );
  }
  if (payload.status === "no_candidate") {
    const code = payload.no_candidate_code;
    if (code !== "questions_disabled" && code !== "no_actionable_gap" && code !== "rate_limited") {
      throw new ActivePersonalLearningApiError(
        "ACTIVE_LEARNING_INVALID_RESPONSE",
        "Сервис уточнения модели вернул некорректный ответ.",
      );
    }
    return { status: "no_candidate", candidate: null, no_candidate_code: code };
  }
  const candidate = payload.candidate;
  if (
    !isRecord(candidate)
    || candidate.contract_version !== "active-personal-learning-v1"
    || typeof candidate.candidate_id !== "string"
    || candidate.kind !== "choice"
    || (candidate.reason_code !== "missing_evidence"
      && candidate.reason_code !== "conflicting_evidence"
      && candidate.reason_code !== "insufficient_evidence")
    || typeof candidate.task !== "string"
    || typeof candidate.question !== "string"
    || !Array.isArray(candidate.options)
    || !candidate.options.every(isOption)
    || typeof candidate.expires_at !== "string"
  ) {
    throw new ActivePersonalLearningApiError(
      "ACTIVE_LEARNING_INVALID_RESPONSE",
      "Сервис уточнения модели вернул некорректный ответ.",
    );
  }
  return {
    status: "candidate",
    no_candidate_code: null,
    candidate: candidate as unknown as ActiveLearningCandidate,
  };
}

function parseResolution(payload: unknown): ActiveLearningResolution {
  if (
    !isRecord(payload)
    || typeof payload.candidate_id !== "string"
    || (payload.disposition !== "answer" && payload.disposition !== "ignore" && payload.disposition !== "reject")
  ) {
    throw new ActivePersonalLearningApiError(
      "ACTIVE_LEARNING_INVALID_RESPONSE",
      "Сервис уточнения модели вернул некорректный ответ.",
    );
  }
  const capture = payload.answer_capture;
  if (capture === null) {
    return {
      candidate_id: payload.candidate_id,
      disposition: payload.disposition,
      answer_capture: null,
    };
  }
  if (!isRecord(capture) || typeof capture.task !== "string" || !isOption(capture.option)) {
    throw new ActivePersonalLearningApiError(
      "ACTIVE_LEARNING_INVALID_RESPONSE",
      "Сервис уточнения модели вернул некорректный ответ.",
    );
  }
  return {
    candidate_id: payload.candidate_id,
    disposition: payload.disposition,
    answer_capture: { task: capture.task, option: capture.option },
  };
}

export async function requestActiveLearningQuestions(
  payload: ActiveLearningRequest,
  signal?: AbortSignal,
): Promise<ActiveLearningResult> {
  const response = await fetch("/api/active-learning/questions", {
    method: "POST",
    headers: {
      Accept: "application/json",
      "Content-Type": "application/json",
      "X-Second-Brain-Request": "active-learning-v1",
    },
    body: JSON.stringify(payload),
    signal,
  });
  let parsed: unknown = null;
  try {
    parsed = await response.json();
  } catch {
    parsed = null;
  }
  if (!response.ok) {
    const envelope = parsed as ErrorEnvelope | null;
    const code = typeof envelope?.error?.code === "string"
      ? envelope.error.code
      : "ACTIVE_LEARNING_REQUEST_FAILED";
    const message = typeof envelope?.error?.message === "string"
      ? envelope.error.message
      : "Не удалось уточнить модель выбора.";
    throw new ActivePersonalLearningApiError(code, message);
  }
  return parseResult(parsed);
}

export async function resolveActiveLearningQuestion(
  candidate: ActiveLearningCandidate,
  selectedOptionId: string,
  signal?: AbortSignal,
): Promise<ActiveLearningResolution> {
  const init: RequestInit = {
    method: "POST",
    headers: {
      Accept: "application/json",
      "Content-Type": "application/json",
      "X-Second-Brain-Request": "active-learning-v1",
    },
    body: JSON.stringify({
      candidate,
      resolution: {
        candidate_id: candidate.candidate_id,
        disposition: "answer",
        selected_option_id: selectedOptionId,
      },
    }),
  };
  if (signal) init.signal = signal;
  const response = await fetch("/api/active-learning/questions/resolve", init);
  let parsed: unknown = null;
  try {
    parsed = await response.json();
  } catch {
    parsed = null;
  }
  if (!response.ok) {
    const envelope = parsed as ErrorEnvelope | null;
    const code = typeof envelope?.error?.code === "string"
      ? envelope.error.code
      : "ACTIVE_LEARNING_REQUEST_FAILED";
    const message = typeof envelope?.error?.message === "string"
      ? envelope.error.message
      : "Не удалось подтвердить ответ на вопрос уточнения.";
    throw new ActivePersonalLearningApiError(code, message);
  }
  return parseResolution(parsed);
}
