export interface SearchHit {
  readonly id: string;
  readonly type: string;
  readonly title: string;
  readonly relative_path?: string;
  readonly snippet?: string;
  readonly tags?: readonly string[];
}

export interface SearchResponse {
  readonly hits: readonly SearchHit[];
}

export interface RetrievedNote {
  readonly id: string;
  readonly type: string;
  readonly relative_path: string;
  readonly title?: string;
  readonly created?: string;
  readonly updated?: string;
  readonly tags?: readonly string[];
  readonly content: string;
}

export interface SourceProvenance {
  readonly uri?: string;
  readonly kind?: string;
  readonly retrieved_at?: string;
  readonly published_at?: string;
  readonly title?: string;
  readonly author?: string;
  readonly upstream_id?: string;
}

export interface NoteDraft {
  readonly title: string;
  readonly note_type: string;
  readonly content: string;
  readonly tags: readonly string[];
  readonly links: readonly string[];
}

export interface DraftResponse {
  readonly review_token: string;
  readonly draft: NoteDraft;
  readonly sources: readonly SourceProvenance[];
}

export interface PreviewResponse {
  readonly html: string;
}

export interface SaveNote {
  readonly id: string;
  readonly type?: string;
  readonly relative_path?: string;
  readonly created?: string;
  readonly updated?: string;
}

export interface SavePlanResponse {
  readonly status: "dry-run";
  readonly confirmation_token: string;
  readonly note: SaveNote;
  readonly diff: string;
}

export interface SavedNoteResponse {
  readonly status: "created";
  readonly note: SaveNote;
}

export interface TimelineItem {
  readonly id: string;
  readonly event_at?: string;
  readonly event_kind?: string;
  readonly evidence_kind?: string;
  readonly summary?: string;
  readonly domain?: string;
  readonly relative_path?: string;
  readonly storage_created_at?: string;
  readonly storage_updated_at?: string;
  readonly related_note_ids?: readonly string[];
}

export interface TimelineResponse {
  readonly known_items: readonly TimelineItem[];
  readonly unknown_items: readonly TimelineItem[];
  readonly known_total: number;
  readonly unknown_total: number;
}

export interface SelfModelEvidence {
  readonly id?: string;
  readonly evidence_kind?: string;
  readonly self_kind?: string;
  readonly domain?: string;
  readonly evidence_at?: string;
  readonly evidence_at_precision?: string;
  readonly related_note_ids?: readonly string[];
}

export interface SelfModelClaim {
  readonly dimension?: string;
  readonly claim?: string;
  readonly domain?: string;
  readonly confidence: {
    readonly state?: string;
    readonly policy_version?: string;
    readonly supporting_evidence_count?: number;
    readonly contradicting_evidence_count?: number;
    readonly unknown_time_count?: number;
  };
  readonly temporal_context: {
    readonly earliest_known_evidence_at?: string;
    readonly latest_known_evidence_at?: string;
    readonly known_evidence_count?: number;
    readonly unknown_evidence_count?: number;
  };
  readonly generated_at?: string;
  readonly derivation_version?: string;
  readonly supporting_evidence: readonly SelfModelEvidence[];
  readonly contradicting_evidence: readonly SelfModelEvidence[];
  readonly contextual_evidence: readonly SelfModelEvidence[];
}

export interface SelfModelResponse {
  readonly claims: readonly SelfModelClaim[];
  readonly eligible_evidence_count: number;
  readonly represented_evidence_count: number;
  readonly generated_at?: string;
  readonly derivation_version?: string;
  readonly policy_fingerprint?: string;
}

export interface BehavioralRatio {
  readonly numerator: number;
  readonly denominator: number;
}

export interface BehavioralOptionIdentity {
  readonly option_index: number;
  readonly option_fingerprint: string;
}

export interface BehavioralChoiceSupport {
  readonly option: BehavioralOptionIdentity;
  readonly support_count: number;
  readonly support_ratio: BehavioralRatio | null;
}

export interface BehavioralWindowSummary {
  readonly window: "current" | "historical";
  readonly observation_count: number;
  readonly choice_support: readonly BehavioralChoiceSupport[];
}

export interface BehavioralPattern {
  readonly cohort: {
    readonly grouping_policy: string;
    readonly domain: string;
    readonly situation_fingerprint: string;
    readonly information_fingerprint: string;
    readonly option_namespace_fingerprint: string;
    readonly criteria_fingerprint: string;
    readonly cohort_fingerprint: string;
  } | null;
  readonly pattern_type: string;
  readonly state: string;
  readonly selected_option: BehavioralOptionIdentity | null;
  readonly support_count: number | null;
  readonly total_comparable_observations: number;
  readonly support_ratio: BehavioralRatio | null;
  readonly choice_support: readonly BehavioralChoiceSupport[];
  readonly temporal_span: {
    readonly earliest_evidence_at: string | null;
    readonly latest_evidence_at: string | null;
  };
  readonly windows: readonly BehavioralWindowSummary[];
  readonly outcome_presence: Readonly<Record<string, number>>;
  readonly provenance: {
    readonly source_journal_uuids: readonly string[];
    readonly source_count: number;
    readonly provenance_fingerprint: string;
  };
  readonly caveats: readonly string[];
}

export interface BehavioralSelfModelResponse {
  readonly contract_version: string;
  readonly derivation_version: string;
  readonly policy_id: string;
  readonly policy_fingerprint: string;
  readonly generated_at: string;
  readonly patterns: readonly BehavioralPattern[];
  readonly eligible_journal_count: number;
  readonly comparable_observation_count: number;
  readonly excluded_unknown_time_count: number;
  readonly excluded_outside_horizon_count: number;
  readonly caveats: readonly string[];
}

export interface StatedObservedMappingSelector {
  readonly source_note_uuid: string;
  readonly behavioral_cohort_fingerprint: string;
  readonly behavioral_option_index: number;
  readonly behavioral_option_fingerprint: string;
}

export interface StatedObservedMappingReviewOption {
  readonly option_index: number;
  readonly option_fingerprint: string;
  readonly label: string;
}

export interface StatedObservedMappingReviewResponse {
  readonly generated_at: string;
  readonly stated: {
    readonly source_note_uuid: string;
    readonly dimension: "preference";
    readonly source_evidence_kind: string;
    readonly source_self_kind: "preference";
    readonly domain: string;
    readonly evidence_at: string;
    readonly evidence_at_precision: string;
    readonly source_contract_version: string;
    readonly source_derivation_version: string;
    readonly self_model_policy_fingerprint: string;
    readonly source_fingerprint: string;
    readonly claim_fingerprint: string;
  };
  readonly behavioral: {
    readonly cohort: BehavioralPattern["cohort"];
    readonly option: BehavioralOptionIdentity;
    readonly pattern_type: string;
    readonly pattern_state: string;
    readonly pattern_fingerprint: string;
    readonly source_fingerprint: string;
    readonly provenance_fingerprint: string;
    readonly source_count: number;
    readonly behavioral_contract_version: string;
    readonly behavioral_derivation_version: string;
    readonly observation_version: string;
    readonly policy_id: string;
    readonly policy_fingerprint: string;
    readonly comparison_subject: string;
  };
  readonly candidate_mapping_fingerprint: string;
  readonly claim_text: string;
  readonly cohort_domain: string;
  readonly situation: string;
  readonly information_known_at_decision_time: string;
  readonly criteria: readonly string[];
  readonly ordered_options: readonly StatedObservedMappingReviewOption[];
  readonly pattern_type: string;
  readonly pattern_state: string;
  readonly caveats: readonly string[];
}

export interface StatedObservedMappingStatusItem {
  readonly mapping_id: string;
  readonly lifecycle_state: "active" | "superseded" | "invalidated" | "deleted";
  readonly source_note_uuid: string;
  readonly domain: string;
  readonly behavioral_cohort_fingerprint: string;
  readonly behavioral_option_index: number;
  readonly behavioral_option_fingerprint: string;
  readonly pattern_type: string;
  readonly pattern_state: string;
  readonly mapping_fingerprint: string;
  readonly mapping_policy_fingerprint: string;
  readonly created_at: string;
  readonly reviewed_at: string;
  readonly supersedes_mapping_id: string | null;
}

export interface StatedObservedMappingStatusResponse {
  readonly mapping_policy_id: string;
  readonly mappings: readonly StatedObservedMappingStatusItem[];
  readonly active_mapping_count: number;
}

export type StatedObservedCompositionState =
  | "aligned"
  | "divergent"
  | "stated_evidence_missing"
  | "behavioral_evidence_insufficient"
  | "not_comparable";

export interface StatedObservedCompositionResponse {
  readonly contract_version: string;
  readonly derivation_version: string;
  readonly mapping_policy_id: string;
  readonly mapping_policy_fingerprint: string;
  readonly generated_at: string;
  readonly state: StatedObservedCompositionState;
  readonly reason_code: string | null;
  readonly mapping_id: string | null;
  readonly mapping_fingerprint: string | null;
  readonly observed_option: BehavioralOptionIdentity | null;
  readonly behavioral_pattern_type: string | null;
  readonly behavioral_pattern_state: string | null;
  readonly caveats: readonly string[];
}

export interface SelfRetrievalClaim {
  readonly dimension?: string;
  readonly claim?: string;
  readonly supporting_note_ids: readonly string[];
  readonly derivation_version?: string;
  readonly policy_fingerprint?: string;
}

export interface SelfRetrievalItem {
  readonly note_id: string;
  readonly title?: string;
  readonly note_type?: string;
  readonly search_rank?: number;
  readonly created?: string;
  readonly updated?: string;
  readonly tags?: readonly string[];
  readonly body: string;
  readonly self_model_claims: readonly SelfRetrievalClaim[];
}

export interface SelfRetrievalExclusion {
  readonly search_rank?: number;
  readonly reason?: string;
}

export interface SelfRetrievalResponse {
  readonly items: readonly SelfRetrievalItem[];
  readonly exclusions: readonly SelfRetrievalExclusion[];
  readonly candidate_count: number;
  readonly included_count: number;
  readonly excluded_count: number;
  readonly content_bytes?: number;
  readonly truncated?: boolean;
  readonly self_model_derivation_version?: string;
  readonly self_model_policy_fingerprint?: string;
}

export interface SimulateMeOption {
  readonly id?: string;
  readonly label?: string;
}

export interface SimulateMeEvidence {
  readonly claim_id?: string;
  readonly dimension?: string;
  readonly note_ids: readonly string[];
  readonly evidence_at?: string;
}

export interface SimulateMeResponse {
  readonly kind: "prediction" | "abstention";
  readonly selected_option?: SimulateMeOption;
  readonly abstention_code?: string;
  readonly derivation_version?: string;
  readonly policy_id?: string;
  readonly policy_fingerprint?: string;
  readonly evidence_refs: readonly SimulateMeEvidence[];
  readonly contextual_evidence_refs: readonly SimulateMeEvidence[];
  readonly temporal_caveats: readonly { readonly code?: string; readonly claim_id?: string }[];
}

export type DiagnosticsStatus = "healthy" | "degraded" | "unavailable";

export interface DiagnosticsLayer {
  readonly status: DiagnosticsStatus;
  readonly required: boolean;
  readonly code: string | null;
}

export interface DiagnosticsCount {
  readonly code: string;
  readonly severity: "error" | "warning" | "info";
  readonly count: number;
}

export interface DiagnosticsResponse {
  readonly status: DiagnosticsStatus;
  readonly generated_at: string;
  readonly config: { readonly resolvable: boolean };
  readonly vault: {
    readonly manifest_available: boolean;
    readonly content_roots_available: boolean;
    readonly attachments_scan_complete: boolean;
  };
  readonly manifest: {
    readonly available: boolean;
    readonly schema_version: number | null;
  };
  readonly counts: {
    readonly managed_notes: number | null;
    readonly enrolled_personal_memory: number | null;
    readonly valid_decision_journals: number | null;
    readonly valid_outcome_observations: number | null;
  };
  readonly notes: number | null;
  readonly enrolled_personal_memory: number | null;
  readonly valid_decision_journals: number | null;
  readonly valid_outcome_observations: number | null;
  readonly attachments: {
    readonly scan_complete: boolean;
    readonly count: number | null;
    readonly total_bytes: number | null;
  };
  readonly attachment_total: number | null;
  readonly attachment_bytes: number | null;
  readonly timeline: DiagnosticsLayer;
  readonly self_model: DiagnosticsLayer;
  readonly self_retrieval: DiagnosticsLayer;
  readonly errors: number;
  readonly warnings: number;
  readonly diagnostics: readonly DiagnosticsCount[];
  readonly exit_code: number;
}

export interface DecisionPayload {
  readonly title: string;
  readonly note_type: string;
  readonly tags: readonly string[];
  readonly links: readonly string[];
  readonly evidence_at: string;
  readonly evidence_at_precision: "exact" | "unknown";
  readonly domain: string | null;
  readonly situation: string;
  readonly available_options: readonly string[];
  readonly information_known_at_decision_time: string;
  readonly criteria: readonly string[];
  readonly chosen_option: string;
  readonly reasons: string;
  readonly confidence: string;
  readonly expected_result: string;
}

export interface OutcomePayload {
  readonly title: string;
  readonly note_type: string;
  readonly tags: readonly string[];
  readonly links: readonly string[];
  readonly decision_id: string;
  readonly evidence_at: string;
  readonly evidence_at_precision: "exact" | "unknown";
  readonly domain: string | null;
  readonly actual_result: string;
  readonly reassessment: string;
  readonly notes: string;
}

export interface PersonalMemoryPayload {
  readonly evidence_kind: string;
  readonly self_kind: string;
  readonly evidence_at: string;
  readonly evidence_at_precision: "exact" | "unknown";
  readonly domain: string | null;
}

export interface FetchLike {
  (input: RequestInfo | URL, init?: RequestInit): Promise<Response>;
}

export class ApiRequestError extends Error {
  readonly status: number;

  constructor(message: string, status: number) {
    super(message);
    this.name = "ApiRequestError";
    this.status = status;
  }
}

const fetchDefault: FetchLike = (input, init) => fetch(input, init);

const headersFor = (purpose: string): HeadersInit => ({
  Accept: "application/json",
  "Content-Type": "application/json",
  "X-Second-Brain-Request": purpose,
});

function errorMessage(payload: unknown, fallback: string): string {
  if (typeof payload === "object" && payload !== null && "error" in payload) {
    const error = payload.error;
    if (typeof error === "object" && error !== null && "message" in error) {
      const message = error.message;
      if (typeof message === "string" && /[А-Яа-яЁё]/u.test(message)) {
        return message;
      }
    }
  }
  return fallback;
}

async function readJson(response: Response): Promise<unknown> {
  try {
    return await response.json();
  } catch {
    return null;
  }
}

async function requestJson<T>(
  path: string,
  purpose: string,
  body: unknown,
  fetcher: FetchLike,
  fallback: string,
  signal?: AbortSignal,
): Promise<T> {
  const init: RequestInit = {
    method: "POST",
    headers: headersFor(purpose),
    body: JSON.stringify(body),
  };
  if (signal) init.signal = signal;
  const response = await fetcher(path, init);
  const payload = await readJson(response);
  if (!response.ok) {
    throw new ApiRequestError(errorMessage(payload, fallback), response.status);
  }
  return payload as T;
}

export async function createTextDraft(text: string, fetcher: FetchLike = fetchDefault): Promise<DraftResponse> {
  return requestJson("/api/drafts/text", "draft-v1", { text }, fetcher, "Не удалось создать черновик.");
}

export async function reviewActiveLearningAnswer(
  draft: NoteDraft,
  fetcher: FetchLike = fetchDefault,
  signal?: AbortSignal,
): Promise<DraftResponse> {
  return requestJson(
    "/api/drafts/active-learning/answer/review",
    "draft-v1",
    { draft },
    fetcher,
    "Не удалось проверить ответ.",
    signal,
  );
}

export async function createUrlDraft(url: string, fetcher: FetchLike = fetchDefault): Promise<DraftResponse> {
  return requestJson("/api/drafts/url", "draft-v1", { url }, fetcher, "Не удалось создать черновик.");
}

export async function previewDraft(content: string, fetcher: FetchLike = fetchDefault): Promise<PreviewResponse> {
  return requestJson("/api/drafts/preview", "draft-v1", { content }, fetcher, "Не удалось построить предпросмотр.");
}

export async function prepareSave(reviewToken: string, draft: NoteDraft, fetcher: FetchLike = fetchDefault): Promise<SavePlanResponse> {
  return requestJson("/api/drafts/save/prepare", "draft-v1", { review_token: reviewToken, draft }, fetcher, "Не удалось подготовить сохранение.");
}

export async function applySave(reviewToken: string, confirmationToken: string, draft: NoteDraft, fetcher: FetchLike = fetchDefault): Promise<SavedNoteResponse> {
  return requestJson("/api/drafts/save/apply", "draft-v1", { review_token: reviewToken, confirmation_token: confirmationToken, draft }, fetcher, "Не удалось сохранить заметку.");
}

export async function preparePersonalMemory(reviewToken: string, draft: NoteDraft, personalMemory: PersonalMemoryPayload, fetcher: FetchLike = fetchDefault): Promise<SavePlanResponse> {
  return requestJson("/api/drafts/personal-memory/save/prepare", "draft-v1", { review_token: reviewToken, draft, personal_memory: personalMemory }, fetcher, "Не удалось подготовить сохранение личной памяти.");
}

export async function applyPersonalMemory(reviewToken: string, confirmationToken: string, draft: NoteDraft, personalMemory: PersonalMemoryPayload, fetcher: FetchLike = fetchDefault): Promise<SavedNoteResponse> {
  return requestJson("/api/drafts/personal-memory/save/apply", "draft-v1", { review_token: reviewToken, confirmation_token: confirmationToken, draft, personal_memory: personalMemory }, fetcher, "Не удалось сохранить личную память.");
}

export async function transcribeAudio(body: Blob, contentType: string, fetcher: FetchLike = fetchDefault): Promise<{ readonly transcript: { readonly text: string } }> {
  const response = await fetcher("/api/transcriptions/audio", {
    method: "POST",
    headers: { Accept: "application/json", "Content-Type": contentType, "X-Second-Brain-Request": "voice-v1" },
    body,
  });
  const payload = await readJson(response);
  if (!response.ok) {
    throw new ApiRequestError(errorMessage(payload, "Не удалось распознать аудио."), response.status);
  }
  return payload as { readonly transcript: { readonly text: string } };
}

export function loadTimeline(order: "asc" | "desc", fetcher: FetchLike = fetchDefault): Promise<TimelineResponse> {
  return requestJson("/api/timeline", "timeline-v1", { order, known_limit: 100, unknown_limit: 100 }, fetcher, "Не удалось загрузить хронологию.");
}

export function loadSelfModel(fetcher: FetchLike = fetchDefault, signal?: AbortSignal): Promise<SelfModelResponse> {
  return requestJson(
    "/api/self-model",
    "self-model-v1",
    { max_claims: 200, max_evidence_refs_per_claim: 200 },
    fetcher,
    "Не удалось построить модель себя.",
    signal,
  );
}

export function loadBehavioralSelfModel(
  fetcher: FetchLike = fetchDefault,
  signal?: AbortSignal,
): Promise<BehavioralSelfModelResponse> {
  return requestJson(
    "/api/behavioral-self-model",
    "cognitive-twin-v1",
    {},
    fetcher,
    "Не удалось построить наблюдаемый слой.",
    signal,
  );
}

export function loadStatedObservedMappingStatus(
  fetcher: FetchLike = fetchDefault,
  signal?: AbortSignal,
): Promise<StatedObservedMappingStatusResponse> {
  return requestJson(
    "/api/stated-observed-mapping/status",
    "cognitive-twin-v1",
    {},
    fetcher,
    "Не удалось загрузить состояние сопоставлений.",
    signal,
  );
}

export function reviewStatedObservedMapping(
  selector: StatedObservedMappingSelector,
  fetcher: FetchLike = fetchDefault,
  signal?: AbortSignal,
): Promise<StatedObservedMappingReviewResponse> {
  return requestJson(
    "/api/stated-observed-mapping/review",
    "cognitive-twin-v1",
    selector,
    fetcher,
    "Не удалось подготовить проверку сопоставления.",
    signal,
  );
}

export function confirmStatedObservedMapping(
  selector: StatedObservedMappingSelector,
  operationId: string,
  confirmed: boolean,
  supersedesMappingId: string | null,
  fetcher: FetchLike = fetchDefault,
  signal?: AbortSignal,
): Promise<{ readonly status: "accepted"; readonly mapping: StatedObservedMappingStatusItem }> {
  return requestJson(
    "/api/stated-observed-mapping/confirm",
    "cognitive-twin-v1",
    {
      ...selector,
      operation_id: operationId,
      confirmed,
      supersedes_mapping_id: supersedesMappingId,
    },
    fetcher,
    "Не удалось принять сопоставление.",
    signal,
  );
}

export function loadStatedObservedComposition(
  sourceNoteUuid: string | null,
  fetcher: FetchLike = fetchDefault,
  signal?: AbortSignal,
): Promise<StatedObservedCompositionResponse> {
  return requestJson(
    "/api/stated-observed-composition",
    "cognitive-twin-v1",
    { source_note_uuid: sourceNoteUuid },
    fetcher,
    "Не удалось проверить текущее сопоставление.",
    signal,
  );
}

export function loadSelfRetrieval(query: string, fetcher: FetchLike = fetchDefault): Promise<SelfRetrievalResponse> {
  return requestJson("/api/self-retrieval", "self-retrieval-v1", { query, limit: 20, max_content_bytes: 65536 }, fetcher, "Не удалось собрать контекст.");
}

export function simulateMe(query: string, options: readonly SimulateMeOption[], fetcher: FetchLike = fetchDefault): Promise<SimulateMeResponse> {
  return requestJson("/api/simulate-me", "simulate-me-v1", { query, options }, fetcher, "Не удалось получить прогноз.");
}

export function loadDiagnostics(fetcher: FetchLike = fetchDefault): Promise<DiagnosticsResponse> {
  return requestJson("/api/diagnostics", "diagnostics-v1", {}, fetcher, "Не удалось получить диагностику рабочего пространства.");
}

export function searchNotes(query: string, fetcher: FetchLike = fetchDefault): Promise<SearchResponse> {
  return requestJson("/api/search", "search-v1", { query, limit: 20 }, fetcher, "Не удалось выполнить поиск.");
}

export async function retrieveNote(id: string, fetcher: FetchLike = fetchDefault): Promise<RetrievedNote> {
  const response = await requestJson<{ note: RetrievedNote }>("/api/retrieval/note", "search-v1", { id }, fetcher, "Не удалось открыть заметку.");
  return response.note;
}

export function prepareDecision(decision: DecisionPayload, fetcher: FetchLike = fetchDefault): Promise<SavePlanResponse> {
  return requestJson("/api/drafts/decision-journal/save/prepare", "draft-v1", { decision }, fetcher, "Не удалось подготовить журнал решений.");
}

export function applyDecision(confirmationToken: string, decision: DecisionPayload, fetcher: FetchLike = fetchDefault): Promise<SavedNoteResponse> {
  return requestJson("/api/drafts/decision-journal/save/apply", "draft-v1", { confirmation_token: confirmationToken, decision }, fetcher, "Не удалось сохранить журнал решений.");
}

export function prepareOutcome(outcome: OutcomePayload, fetcher: FetchLike = fetchDefault): Promise<SavePlanResponse> {
  return requestJson("/api/drafts/outcome-observation/save/prepare", "draft-v1", { outcome }, fetcher, "Не удалось подготовить результат.");
}

export function applyOutcome(confirmationToken: string, outcome: OutcomePayload, fetcher: FetchLike = fetchDefault): Promise<SavedNoteResponse> {
  return requestJson("/api/drafts/outcome-observation/save/apply", "draft-v1", { confirmation_token: confirmationToken, outcome }, fetcher, "Не удалось сохранить результат.");
}
