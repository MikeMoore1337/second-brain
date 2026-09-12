import type { FetchLike } from "./api";

export type ProspectiveAuditOption = {
  readonly id: string;
  readonly ordinal: number;
  readonly label: string;
};

export type ProspectiveAuditEvent = {
  readonly event_id: string;
  readonly created_at: string;
  readonly kind: "prediction" | "abstention";
  readonly options: readonly ProspectiveAuditOption[];
  readonly predicted_option_id: string | null;
  readonly predicted_option_label: string | null;
  readonly abstention_code: string | null;
  readonly derivation_version: string;
  readonly policy_id: string;
};

export type ProspectiveDecisionOption = {
  readonly index: number;
  readonly label: string;
  readonly fingerprint?: string;
};

export type ProspectiveDecisionJournal = {
  readonly decision_id: string;
  readonly evidence_at: string;
  readonly evidence_at_precision: "exact";
  readonly created: string;
  readonly options: readonly ProspectiveDecisionOption[];
  readonly chosen_option_index: number;
  readonly chosen_option: string;
};

export type ProspectiveAuditPendingResponse = {
  readonly events: readonly ProspectiveAuditEvent[];
  readonly decision_journals: readonly ProspectiveDecisionJournal[];
  readonly limits: {
    readonly max_events: number;
    readonly max_decision_journals: number;
  };
};

export type ProspectiveAuditReviewResponse = {
  readonly event: ProspectiveAuditEvent;
  readonly decision: ProspectiveDecisionJournal & {
    readonly options: readonly (ProspectiveDecisionOption & { readonly fingerprint: string })[];
  };
  readonly mapping_basis: "owner-explicit-v1";
  readonly requires_confirmation: true;
};

export type ProspectiveAuditLinkResponse = {
  readonly status: "linked";
  readonly audit_event_id: string;
  readonly decision_id: string;
  readonly link_state: "LINKED_VALID";
  readonly linked_at: string;
};

export type ProspectiveAuditCalibrationRatio = {
  readonly numerator: number;
  readonly denominator: number;
};

export type ProspectiveAuditCalibrationCount = {
  readonly code: string;
  readonly count: number;
};

export type ProspectiveAuditCalibrationResult = {
  readonly contract_version: string;
  readonly derivation_version: string;
  readonly policy_id: string;
  readonly policy_fingerprint: string;
  readonly retention_policy: string;
  readonly metrics: {
    readonly audited_operations: number;
    readonly predictions: number;
    readonly abstentions: number;
    readonly linked_actual_decisions: number;
    readonly pending_unlinked_events: number;
    readonly invalid_linkage_events: number;
    readonly unavailable_linkage_events: number;
    readonly exact_option_matches: number;
    readonly mismatches: number;
    readonly coverage: ProspectiveAuditCalibrationRatio | null;
    readonly actual_linkage_coverage: ProspectiveAuditCalibrationRatio | null;
    readonly evaluated_prediction_coverage: ProspectiveAuditCalibrationRatio | null;
    readonly accuracy_non_abstained: ProspectiveAuditCalibrationRatio | null;
  };
  readonly invalid_linkage_by_code: readonly ProspectiveAuditCalibrationCount[];
  readonly unavailable_linkage_by_code: readonly ProspectiveAuditCalibrationCount[];
};

export class ProspectiveAuditApiError extends Error {
  readonly code: string;
  readonly status: number;

  constructor(code: string, message: string, status = 0) {
    super(message);
    this.name = "ProspectiveAuditApiError";
    this.code = code;
    this.status = status;
  }
}

type ErrorEnvelope = { readonly error?: { readonly code?: unknown } };
type RecordValue = Record<string, unknown>;

const PURPOSE = "prospective-audit-v1";
const ID_PATTERN = /^[A-Za-z0-9][A-Za-z0-9._:-]{0,63}$/u;
const UUID7_PATTERN = /^[0-9a-f]{8}-[0-9a-f]{4}-7[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/iu;
const HASH_PATTERN = /^sha256:[0-9a-f]{64}$/u;
const CONTROL_PATTERN = /[\u0000-\u001f\u007f-\u009f]/u;
const FORMATTER_PATTERN = /\p{Cf}/u;

function isRecord(value: unknown): value is RecordValue {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function hasExactKeys(value: RecordValue, keys: readonly string[]): boolean {
  const actual = Object.keys(value).sort();
  const expected = [...keys].sort();
  return actual.length === expected.length && actual.every((key, index) => key === expected[index]);
}

function boundedText(value: unknown, maxBytes: number): value is string {
  if (typeof value !== "string" || CONTROL_PATTERN.test(value) || FORMATTER_PATTERN.test(value)) return false;
  if (value !== value.normalize("NFC") || value.trim() !== value || !value) return false;
  return new TextEncoder().encode(value).byteLength <= maxBytes;
}

function isUtcTimestamp(value: unknown): value is string {
  return typeof value === "string"
    && /(?:Z|\+00:00)$/u.test(value)
    && Number.isFinite(Date.parse(value));
}

function isSafeInteger(value: unknown, minimum = 0): value is number {
  return typeof value === "number" && Number.isSafeInteger(value) && value >= minimum;
}

function isAuditOption(value: unknown): value is ProspectiveAuditOption {
  return isRecord(value)
    && hasExactKeys(value, ["id", "ordinal", "label"])
    && typeof value.id === "string"
    && ID_PATTERN.test(value.id)
    && isSafeInteger(value.ordinal)
    && boundedText(value.label, 16 * 1024);
}

function isAuditEvent(value: unknown): value is ProspectiveAuditEvent {
  if (!isRecord(value) || !hasExactKeys(value, [
    "event_id", "created_at", "kind", "options", "predicted_option_id",
    "predicted_option_label", "abstention_code", "derivation_version", "policy_id",
  ])) return false;
  if (
    typeof value.event_id !== "string"
    || !UUID7_PATTERN.test(value.event_id)
    || !isUtcTimestamp(value.created_at)
    || (value.kind !== "prediction" && value.kind !== "abstention")
    || !Array.isArray(value.options)
    || value.options.length < 1
    || value.options.length > 8
    || !value.options.every(isAuditOption)
    || new Set(value.options.map((option) => option.id)).size !== value.options.length
    || !boundedText(value.derivation_version, 256)
    || !boundedText(value.policy_id, 256)
  ) return false;
  if (value.kind === "prediction") {
    return typeof value.predicted_option_id === "string"
      && ID_PATTERN.test(value.predicted_option_id)
      && value.options.some((option) => option.id === value.predicted_option_id)
      && typeof value.predicted_option_label === "string"
      && value.options.some((option) => option.label === value.predicted_option_label)
      && value.abstention_code === null;
  }
  return value.predicted_option_id === null
    && value.predicted_option_label === null
    && typeof value.abstention_code === "string"
    && value.abstention_code.length <= 128;
}

function isDecisionOption(value: unknown, requireFingerprint: boolean): value is ProspectiveDecisionOption {
  if (!isRecord(value)) return false;
  const keys = requireFingerprint ? ["index", "label", "fingerprint"] : ["index", "label"];
  return hasExactKeys(value, keys)
    && isSafeInteger(value.index)
    && boundedText(value.label, 16 * 1024)
    && (!requireFingerprint || (typeof value.fingerprint === "string" && HASH_PATTERN.test(value.fingerprint)));
}

function isDecision(value: unknown, requireFingerprint: boolean): value is ProspectiveDecisionJournal {
  return isRecord(value)
    && hasExactKeys(value, ["decision_id", "evidence_at", "evidence_at_precision", "created", "options", "chosen_option_index", "chosen_option"])
    && typeof value.decision_id === "string"
    && UUID7_PATTERN.test(value.decision_id)
    && isUtcTimestamp(value.evidence_at)
    && value.evidence_at_precision === "exact"
    && isUtcTimestamp(value.created)
    && Array.isArray(value.options)
    && value.options.length >= 2
    && value.options.length <= 20
    && value.options.every((option) => isDecisionOption(option, requireFingerprint))
    && value.options.every((option, index) => option.index === index)
    && new Set(value.options.map((option) => option.label)).size === value.options.length
    && isSafeInteger(value.chosen_option_index)
    && value.chosen_option_index < value.options.length
    && typeof value.chosen_option === "string"
    && value.chosen_option === value.options[value.chosen_option_index]?.label;
}

function isPending(value: unknown): value is ProspectiveAuditPendingResponse {
  return isRecord(value)
    && hasExactKeys(value, ["events", "decision_journals", "limits"])
    && Array.isArray(value.events)
    && value.events.length <= 32
    && value.events.every(isAuditEvent)
    && Array.isArray(value.decision_journals)
    && value.decision_journals.length <= 32
    && value.decision_journals.every((decision) => isDecision(decision, false))
    && isRecord(value.limits)
    && hasExactKeys(value.limits, ["max_events", "max_decision_journals"])
    && value.limits.max_events === 32
    && value.limits.max_decision_journals === 32;
}

function isReview(value: unknown): value is ProspectiveAuditReviewResponse {
  return isRecord(value)
    && hasExactKeys(value, ["event", "decision", "mapping_basis", "requires_confirmation"])
    && isAuditEvent(value.event)
    && isDecision(value.decision, true)
    && value.mapping_basis === "owner-explicit-v1"
    && value.requires_confirmation === true;
}

function isLink(value: unknown): value is ProspectiveAuditLinkResponse {
  return isRecord(value)
    && hasExactKeys(value, ["status", "audit_event_id", "decision_id", "link_state", "linked_at"])
    && value.status === "linked"
    && typeof value.audit_event_id === "string"
    && UUID7_PATTERN.test(value.audit_event_id)
    && typeof value.decision_id === "string"
    && UUID7_PATTERN.test(value.decision_id)
    && value.link_state === "LINKED_VALID"
    && isUtcTimestamp(value.linked_at);
}

function isRatio(value: unknown): value is ProspectiveAuditCalibrationRatio | null {
  return value === null
    || (isRecord(value)
      && hasExactKeys(value, ["numerator", "denominator"])
      && isSafeInteger(value.numerator)
      && isSafeInteger(value.denominator, 1));
}

function isCount(value: unknown): value is ProspectiveAuditCalibrationCount {
  return isRecord(value)
    && hasExactKeys(value, ["code", "count"])
    && boundedText(value.code, 128)
    && isSafeInteger(value.count);
}

function isCalibration(value: unknown): value is ProspectiveAuditCalibrationResult {
  if (!isRecord(value) || !hasExactKeys(value, [
    "contract_version", "derivation_version", "policy_id", "policy_fingerprint",
    "retention_policy", "metrics", "invalid_linkage_by_code", "unavailable_linkage_by_code",
  ])) return false;
  if (
    !boundedText(value.contract_version, 256)
    || !boundedText(value.derivation_version, 256)
    || !boundedText(value.policy_id, 256)
    || typeof value.policy_fingerprint !== "string"
    || !HASH_PATTERN.test(value.policy_fingerprint)
    || !boundedText(value.retention_policy, 256)
    || !isRecord(value.metrics)
    || !hasExactKeys(value.metrics, [
      "audited_operations", "predictions", "abstentions", "linked_actual_decisions",
      "pending_unlinked_events", "invalid_linkage_events", "unavailable_linkage_events",
      "exact_option_matches", "mismatches", "coverage", "actual_linkage_coverage",
      "evaluated_prediction_coverage", "accuracy_non_abstained",
    ])
  ) return false;
  const metricValues = [
    value.metrics.audited_operations,
    value.metrics.predictions,
    value.metrics.abstentions,
    value.metrics.linked_actual_decisions,
    value.metrics.pending_unlinked_events,
    value.metrics.invalid_linkage_events,
    value.metrics.unavailable_linkage_events,
    value.metrics.exact_option_matches,
    value.metrics.mismatches,
  ];
  return metricValues.every((item) => isSafeInteger(item))
    && isRatio(value.metrics.coverage)
    && isRatio(value.metrics.actual_linkage_coverage)
    && isRatio(value.metrics.evaluated_prediction_coverage)
    && isRatio(value.metrics.accuracy_non_abstained)
    && Array.isArray(value.invalid_linkage_by_code)
    && value.invalid_linkage_by_code.length <= 32
    && value.invalid_linkage_by_code.every(isCount)
    && Array.isArray(value.unavailable_linkage_by_code)
    && value.unavailable_linkage_by_code.length <= 32
    && value.unavailable_linkage_by_code.every(isCount);
}

const invalidResponse = (): ProspectiveAuditApiError => new ProspectiveAuditApiError(
  "PROSPECTIVE_AUDIT_INVALID_RESPONSE",
  "Сервис аудита прогноза вернул некорректный ответ.",
);

async function postJson(
  path: string,
  body: unknown,
  fetcher: FetchLike,
  signal?: AbortSignal,
): Promise<unknown> {
  const response = await fetcher(path, {
    method: "POST",
    headers: {
      Accept: "application/json",
      "Content-Type": "application/json",
      "X-Second-Brain-Request": PURPOSE,
    },
    body: JSON.stringify(body),
    signal,
  });
  const payload = await response.json().catch(() => null) as unknown;
  if (!response.ok) {
    const envelope = payload as ErrorEnvelope | null;
    const code = typeof envelope?.error?.code === "string" ? envelope.error.code : "PROSPECTIVE_AUDIT_REQUEST_FAILED";
    throw new ProspectiveAuditApiError(code, "Не удалось выполнить операцию аудита прогноза.", response.status);
  }
  return payload;
}

export async function executeProspectiveAudit(
  operationId: string,
  query: string,
  options: readonly { readonly id: string; readonly label: string }[],
  fetcher: FetchLike = fetch,
  signal?: AbortSignal,
): Promise<ProspectiveAuditEvent> {
  const payload = await postJson("/api/prospective-audit/execute", { operation_id: operationId, query, options }, fetcher, signal);
  if (!isRecord(payload) || !hasExactKeys(payload, ["event"]) || !isAuditEvent(payload.event)) throw invalidResponse();
  return payload.event;
}

export async function loadProspectiveAuditPending(
  fetcher: FetchLike = fetch,
  signal?: AbortSignal,
): Promise<ProspectiveAuditPendingResponse> {
  const payload = await postJson("/api/prospective-audit/pending", {}, fetcher, signal);
  if (!isPending(payload)) throw invalidResponse();
  return payload;
}

export async function reviewProspectiveAuditLink(
  auditEventId: string,
  decisionId: string,
  fetcher: FetchLike = fetch,
  signal?: AbortSignal,
): Promise<ProspectiveAuditReviewResponse> {
  const payload = await postJson("/api/prospective-audit/link/review", { audit_event_id: auditEventId, decision_id: decisionId }, fetcher, signal);
  if (!isReview(payload)) throw invalidResponse();
  return payload;
}

export async function confirmProspectiveAuditLink(
  auditEventId: string,
  decisionId: string,
  operationId: string,
  mapping: readonly { readonly audit_option_id: string; readonly decision_option_index: number; readonly decision_option_fingerprint: string }[],
  confirmed: boolean,
  fetcher: FetchLike = fetch,
  signal?: AbortSignal,
): Promise<ProspectiveAuditLinkResponse> {
  const payload = await postJson(
    "/api/prospective-audit/link/confirm",
    { audit_event_id: auditEventId, decision_id: decisionId, operation_id: operationId, mapping, confirmed },
    fetcher,
    signal,
  );
  if (!isLink(payload)) throw invalidResponse();
  return payload;
}

export async function loadProspectiveAuditCalibration(
  fetcher: FetchLike = fetch,
  signal?: AbortSignal,
): Promise<ProspectiveAuditCalibrationResult> {
  const payload = await postJson("/api/prospective-audit/calibration", {}, fetcher, signal);
  if (!isCalibration(payload)) throw invalidResponse();
  return payload;
}
