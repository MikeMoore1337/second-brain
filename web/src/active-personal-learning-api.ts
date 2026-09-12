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

const OPTION_KEYS = ["id", "label"] as const;
const RESULT_KEYS = ["status", "candidate", "no_candidate_code"] as const;
const CANDIDATE_KEYS = [
  "contract_version",
  "candidate_id",
  "kind",
  "reason_code",
  "task",
  "question",
  "options",
  "source",
  "source_derivation_version",
  "source_policy_id",
  "source_policy_fingerprint",
  "evidence_note_ids",
  "basis_fingerprint",
  "issued_at",
  "expires_at",
] as const;
const RESOLUTION_KEYS = ["candidate_id", "disposition", "answer_capture"] as const;
const ANSWER_CAPTURE_KEYS = ["task", "option"] as const;
const ID_PATTERN = /^[A-Za-z0-9][A-Za-z0-9._:-]{0,63}$/u;
const CANDIDATE_ID_PATTERN = /^apl1:[0-9a-f]{64}$/u;
const FINGERPRINT_PATTERN = /^sha256:[0-9a-f]{64}$/u;
const UUID7_PATTERN = /^[0-9a-f]{8}-[0-9a-f]{4}-7[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/iu;
const FORBIDDEN_TEXT_CODEPOINT = /[\u0000-\u001f\u007f-\u009f]/u;
const FORMATTER_CODEPOINT = /\p{Cf}/u;

function hasExactKeys(value: Record<string, unknown>, keys: readonly string[]): boolean {
  const actual = Object.keys(value).sort();
  const expected = [...keys].sort();
  return actual.length === expected.length && actual.every((key, index) => key === expected[index]);
}

function boundedText(value: unknown, maxBytes: number): value is string {
  if (typeof value !== "string") return false;
  const normalized = value.normalize("NFC").trim();
  if (
    !normalized
    || normalized !== value
    || FORBIDDEN_TEXT_CODEPOINT.test(value)
    || Array.from(value).some((character) => FORMATTER_CODEPOINT.test(character))
  ) {
    return false;
  }
  return new TextEncoder().encode(value).byteLength <= maxBytes;
}

function isUtcTimestamp(value: unknown): value is string {
  if (typeof value !== "string" || !/(?:Z|\+00:00)$/u.test(value)) return false;
  return Number.isFinite(Date.parse(value));
}

function isOption(value: unknown): value is ActiveLearningOption {
  return isRecord(value)
    && hasExactKeys(value, OPTION_KEYS)
    && typeof value.id === "string"
    && ID_PATTERN.test(value.id)
    && boundedText(value.label, 256);
}

function isCandidate(value: unknown): value is ActiveLearningCandidate {
  if (
    !isRecord(value)
    || !hasExactKeys(value, CANDIDATE_KEYS)
    || value.contract_version !== "active-personal-learning-v1"
    || typeof value.candidate_id !== "string"
    || !CANDIDATE_ID_PATTERN.test(value.candidate_id)
    || value.kind !== "choice"
    || (value.reason_code !== "missing_evidence"
      && value.reason_code !== "conflicting_evidence"
      && value.reason_code !== "insufficient_evidence")
    || !boundedText(value.task, 4096)
    || !boundedText(value.question, 512)
    || !Array.isArray(value.options)
    || value.options.length < 1
    || value.options.length > 8
    || !value.options.every(isOption)
    || new Set(value.options.map((option) => option.id)).size !== value.options.length
    || typeof value.source !== "string"
    || typeof value.source_derivation_version !== "string"
    || typeof value.source_policy_id !== "string"
    || typeof value.source_policy_fingerprint !== "string"
    || !FINGERPRINT_PATTERN.test(value.source_policy_fingerprint)
    || !Array.isArray(value.evidence_note_ids)
    || value.evidence_note_ids.length > 20
    || !value.evidence_note_ids.every((id): id is string => UUID7_PATTERN.test(id))
    || new Set(value.evidence_note_ids).size !== value.evidence_note_ids.length
    || value.evidence_note_ids.some((id, index, ids) => index > 0 && ids[index - 1] > id)
    || typeof value.basis_fingerprint !== "string"
    || !FINGERPRINT_PATTERN.test(value.basis_fingerprint)
    || value.candidate_id !== `apl1:${value.basis_fingerprint.slice("sha256:".length)}`
    || !isUtcTimestamp(value.issued_at)
    || !isUtcTimestamp(value.expires_at)
  ) {
    return false;
  }
  return Date.parse(value.expires_at) - Date.parse(value.issued_at) === 600_000;
}

function invalidResponse(): ActivePersonalLearningApiError {
  return new ActivePersonalLearningApiError(
    "ACTIVE_LEARNING_INVALID_RESPONSE",
    "Сервис уточнения модели вернул некорректный ответ.",
  );
}

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

function parseResult(payload: unknown): ActiveLearningResult {
  if (!isRecord(payload) || !hasExactKeys(payload, RESULT_KEYS)) throw invalidResponse();
  if (payload.status !== "candidate" && payload.status !== "no_candidate") throw invalidResponse();
  if (payload.status === "no_candidate") {
    const code = payload.no_candidate_code;
    if (payload.candidate !== null || (code !== "questions_disabled" && code !== "no_actionable_gap" && code !== "rate_limited")) throw invalidResponse();
    return { status: "no_candidate", candidate: null, no_candidate_code: code };
  }
  if (
    payload.no_candidate_code !== null
    || !isCandidate(payload.candidate)
  ) {
    throw invalidResponse();
  }
  return {
    status: "candidate",
    no_candidate_code: null,
    candidate: payload.candidate,
  };
}

function parseResolution(payload: unknown, candidate: ActiveLearningCandidate): ActiveLearningResolution {
  if (
    !isRecord(payload)
    || !hasExactKeys(payload, RESOLUTION_KEYS)
    || typeof payload.candidate_id !== "string"
    || !CANDIDATE_ID_PATTERN.test(payload.candidate_id)
    || payload.candidate_id !== candidate.candidate_id
    || (payload.disposition !== "answer" && payload.disposition !== "ignore" && payload.disposition !== "reject")
  ) {
    throw invalidResponse();
  }
  const capture = payload.answer_capture;
  const captureOption = isRecord(capture) ? capture.option : null;
  if (payload.disposition !== "answer") {
    if (capture !== null) throw invalidResponse();
    return {
      candidate_id: payload.candidate_id,
      disposition: payload.disposition,
      answer_capture: null,
    };
  }
  if (
    !isRecord(capture)
    || !hasExactKeys(capture, ANSWER_CAPTURE_KEYS)
    || !boundedText(capture.task, 4096)
    || capture.task !== candidate.task
    || !isOption(captureOption)
    || !candidate.options.some((option) => option.id === captureOption.id && option.label === captureOption.label)
  ) {
    throw invalidResponse();
  }
  return {
    candidate_id: payload.candidate_id,
    disposition: payload.disposition,
    answer_capture: { task: capture.task, option: captureOption },
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
  return parseResolution(parsed, candidate);
}
