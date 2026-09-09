export type Stage7Option = { id: string; label: string };
export type Stage7ExplicitContext = { kind: "fact" | "background"; text: string };

export type Stage7Request = {
  task: string;
  options: Stage7Option[];
  explicit_constraints: string[];
  explicit_goals: string[];
  explicit_context: Stage7ExplicitContext[];
};

export type AssistantResult = {
  output_label: string;
  kind: "recommendation" | "analysis" | "abstention";
  recommendation: string | null;
  selected_option: Stage7Option | null;
  rationale: string[];
  uncertainty: string[];
  abstention_code: string | null;
};

export type CompareBranch<T> = {
  state: "result" | "abstention" | "error";
  result: T | null;
  error: { code: string; message: string } | null;
};

export type SimulateMeResult = {
  kind: "prediction" | "abstention";
  selected_option: Stage7Option | null;
  abstention_code: string | null;
};

export type CompareResult = {
  assistant: CompareBranch<AssistantResult>;
  simulate_me: CompareBranch<SimulateMeResult>;
  delta: {
    relation: string;
    assistant_state: string;
    simulate_me_state: string;
    assistant_selected_option_id: string | null;
    simulate_me_selected_option_id: string | null;
    explanation: string;
  };
};

type ErrorEnvelope = { error?: { code?: unknown; message?: unknown } };

export class Stage7ApiError extends Error {
  readonly code: string;

  constructor(code: string, message: string) {
    super(message);
    this.name = "Stage7ApiError";
    this.code = code;
  }
}

async function postStage7<T>(
  path: "/api/assistant" | "/api/compare",
  purpose: "assistant-v1" | "compare-v1",
  payload: Stage7Request,
  signal?: AbortSignal,
): Promise<T> {
  const response = await fetch(path, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      "X-Second-Brain-Request": purpose,
    },
    body: JSON.stringify(payload),
    signal,
  });
  const parsed: unknown = await response.json().catch(() => null);
  if (!response.ok) {
    const envelope = parsed as ErrorEnvelope | null;
    const code = typeof envelope?.error?.code === "string" ? envelope.error.code : "STAGE7_REQUEST_FAILED";
    const message = typeof envelope?.error?.message === "string"
      ? envelope.error.message
      : "Не удалось выполнить запрос.";
    throw new Stage7ApiError(code, message);
  }
  return parsed as T;
}

export function requestAssistant(payload: Stage7Request, signal?: AbortSignal): Promise<AssistantResult> {
  return postStage7<AssistantResult>("/api/assistant", "assistant-v1", payload, signal);
}

export function requestCompare(payload: Stage7Request, signal?: AbortSignal): Promise<CompareResult> {
  return postStage7<CompareResult>("/api/compare", "compare-v1", payload, signal);
}
