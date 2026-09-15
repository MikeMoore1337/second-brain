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

export interface GrowthGoalIdentity {
  readonly source_note_uuid: string;
  readonly dimension: "goal";
  readonly source_evidence_kind: string;
  readonly source_self_kind: "goal";
  readonly domain: string | null;
  readonly evidence_at: string;
  readonly evidence_at_precision: string;
  readonly source_contract_version: string;
  readonly source_derivation_version: string;
  readonly self_model_policy_fingerprint: string;
  readonly source_fingerprint: string;
  readonly claim_fingerprint: string;
}

export interface GrowthGoalOwnerItem {
  readonly goal: GrowthGoalIdentity;
  readonly goal_text: string;
  readonly goal_identity_fingerprint: string;
}

export interface GrowthGoalsResponse {
  readonly contract_version: string;
  readonly derivation_version: string;
  readonly policy_id: string;
  readonly policy_fingerprint: string;
  readonly generated_at: string;
  readonly selection_mode: "each_current_goal";
  readonly selected_goal_source_uuid: null;
  readonly eligible_goal_count: number;
  readonly goals: readonly GrowthGoalOwnerItem[];
  readonly reason_codes: readonly string[];
  readonly caveats: readonly string[];
}

export interface GrowthBehavioralOption {
  readonly option_index: number;
  readonly option_fingerprint: string;
}

export interface GrowthBehavioralPatternReference {
  readonly contract_version: string;
  readonly derivation_version: string;
  readonly policy_id: string;
  readonly policy_fingerprint: string;
  readonly cohort_fingerprint: string;
  readonly pattern_type: string;
  readonly pattern_state: string;
  readonly provenance_fingerprint: string;
  readonly source_count: number;
  readonly reference_fingerprint: string;
  readonly current_option: GrowthBehavioralOption | null;
}

export interface GrowthMappingReference {
  readonly mapping_id: string;
  readonly mapping_policy_id: string;
  readonly mapping_fingerprint: string;
  readonly relation: GrowthRelation;
}

export type GrowthRelation = "supports_goal" | "conflicts_with_goal" | "neutral_or_unknown";

export type GrowthState =
  | "supports_goal"
  | "conflicts_with_goal"
  | "neutral_or_unknown"
  | "goal_mapping_missing"
  | "mixed_behavior"
  | "changed_behavior"
  | "behavioral_evidence_insufficient"
  | "not_comparable"
  | "goal_source_missing"
  | "goal_selection_required";

export interface GrowthTemporalContext {
  readonly goal_evidence_at: string;
  readonly goal_evidence_at_precision: string;
  readonly behavioral_generated_at: string | null;
  readonly behavioral_current_window_start: string | null;
  readonly behavioral_current_window_end: string | null;
  readonly mapping_reviewed_at: string | null;
  readonly mapping_created_at: string | null;
  readonly advisor_requested_at: string | null;
}

export interface GrowthRelationResult {
  readonly goal: GrowthGoalIdentity | null;
  readonly state: GrowthState | string;
  readonly cohort_fingerprint: string | null;
  readonly behavioral_pattern: GrowthBehavioralPatternReference | null;
  readonly behavioral_option: GrowthBehavioralOption | null;
  readonly mapping: GrowthMappingReference | null;
  readonly reason_codes: readonly string[];
  readonly caveats: readonly string[];
  readonly temporal: GrowthTemporalContext;
  readonly advisor: null;
}

export interface GrowthResponse {
  readonly contract_version: string;
  readonly derivation_version: string;
  readonly policy_id: string;
  readonly policy_fingerprint: string;
  readonly generated_at: string;
  readonly selection_mode: "selected_goal";
  readonly selected_goal_source_uuid: string;
  readonly eligible_goal_count: number;
  readonly goal_results: readonly GrowthRelationResult[];
  readonly reason_codes: readonly string[];
  readonly caveats: readonly string[];
}

export type GoalProgressStatus =
  | "target_met"
  | "toward_target"
  | "away_from_target"
  | "unchanged"
  | "milestone_observations_available"
  | "insufficient_observations"
  | "definition_missing"
  | "goal_source_changed"
  | "not_comparable";

export type GoalProgressModel = "numeric_target" | "milestone_set";
export type GoalProgressDirection = "increase_to" | "decrease_to" | "reach_exact";
export type GoalProgressMilestoneState = "completed" | "not_completed";

export interface GoalProgressGoalProjection {
  readonly source_note_uuid: string;
  readonly text: string;
  readonly domain: string | null;
  readonly identity_fingerprint: string;
}

export interface GoalProgressMilestone {
  readonly id: string;
  readonly label: string;
  readonly ordinal: number;
}

export interface GoalProgressDefinitionProjection {
  readonly id: string;
  readonly goal_source_uuid: string;
  readonly goal_identity_fingerprint: string;
  readonly goal_progress_policy_fingerprint: string;
  readonly definition_reviewed_at: string;
  readonly progress_model: GoalProgressModel;
  readonly definition_fingerprint: string;
  readonly metric_id?: string;
  readonly unit?: string;
  readonly baseline?: string;
  readonly target?: string;
  readonly direction?: GoalProgressDirection;
  readonly lower_bound?: string;
  readonly upper_bound?: string;
  readonly ordering?: string;
  readonly milestones?: readonly GoalProgressMilestone[];
}

export interface GoalProgressObservationProjection {
  readonly id: string;
  readonly progress_definition_id: string;
  readonly observed_at: string;
  readonly observed_at_precision: "exact" | "unknown";
  readonly progress_model: GoalProgressModel;
  readonly metric_id?: string;
  readonly unit?: string;
  readonly value?: string;
  readonly milestone_id?: string;
  readonly state?: GoalProgressMilestoneState;
  readonly observation_reviewed_at: string;
  readonly supersedes_observation_id?: string;
}

export interface GoalProgressResult {
  readonly contract: "goal_progress_result_v1";
  readonly selected_goal_source_uuid: string;
  readonly current_goal_identity_fingerprint: string | null;
  readonly goal_progress_policy_fingerprint: string;
  readonly active_definition_uuid: string | null;
  readonly definition_fingerprint: string | null;
  readonly as_of: string;
  readonly progress_model: GoalProgressModel | null;
  readonly status: GoalProgressStatus;
  readonly current_observation_uuids: readonly string[];
  readonly excluded_observations: readonly { readonly observation_id: string | null; readonly reason: string }[];
  readonly eligible_count: number;
  readonly unknown_time_count: number;
  readonly superseded_count: number;
  readonly invalid_count: number;
  readonly explanation: Readonly<Record<string, unknown>>;
  readonly provenance: Readonly<Record<string, unknown>>;
  readonly completed_milestone_ids: readonly string[];
  readonly not_completed_milestone_ids: readonly string[];
  readonly missing_milestone_ids: readonly string[];
}

export interface GoalProgressResponse extends GoalProgressResult {
  readonly web_contract: "goal_progress_web_v1";
  readonly goal: GoalProgressGoalProjection;
  readonly definition: GoalProgressDefinitionProjection | null;
  readonly observations: readonly GoalProgressObservationProjection[];
}

export interface GrowthGoalProgressCompositionResponse {
  readonly web_contract: "growth_goal_progress_composition_web_v1";
  readonly contract_version: string;
  readonly derivation_version: string;
  readonly policy_id: string;
  readonly policy_fingerprint: string;
  readonly selected_goal_source_uuid: string;
  readonly current_goal_identity_fingerprint: string;
  readonly progress_as_of: string;
  readonly growth_policy_fingerprint: string;
  readonly goal_progress_policy_fingerprint: string;
  readonly growth_result: GrowthResponse;
  readonly goal_progress_result: GoalProgressResult;
  readonly caveats: readonly string[];
  readonly provenance: Readonly<Record<string, unknown>>;
  readonly goal: GoalProgressGoalProjection;
  readonly definition: GoalProgressDefinitionProjection | null;
  readonly observations: readonly GoalProgressObservationProjection[];
}

export interface GoalProgressMilestoneInput {
  readonly id: string;
  readonly label: string;
  readonly ordinal: number;
}

export interface GoalProgressDefinitionPrepareRequest {
  readonly goal_source_uuid: string;
  readonly progress_model: GoalProgressModel;
  readonly metric_id: string | null;
  readonly unit: string | null;
  readonly baseline: string | null;
  readonly target: string | null;
  readonly direction: GoalProgressDirection | null;
  readonly lower_bound: string | null;
  readonly upper_bound: string | null;
  readonly milestones: readonly GoalProgressMilestoneInput[] | null;
  readonly supersedes_definition_id: string | null;
}

export interface GoalProgressObservationPrepareRequest {
  readonly goal_source_uuid: string;
  readonly progress_definition_id: string;
  readonly value: string | null;
  readonly milestone_id: string | null;
  readonly state: GoalProgressMilestoneState | null;
  readonly observed_at: string;
  readonly observed_at_precision: "exact" | "unknown";
  readonly supersedes_observation_id: string | null;
}

export interface GoalProgressReviewResponse {
  readonly web_contract: "goal_progress_review_v1";
  readonly status: "dry-run";
  readonly review_token: string;
  readonly plan_sha256: string;
  readonly record_kind: "definition" | "observation";
  readonly record_id: string;
  readonly goal: GoalProgressGoalProjection;
  readonly record: Readonly<Record<string, unknown>>;
  readonly write: {
    readonly operation: "create";
    readonly created: string;
    readonly content_sha256: string;
    readonly record_kind: "definition" | "observation";
  };
}

export interface GoalProgressApplyResponse {
  readonly web_contract: "goal_progress_apply_v1";
  readonly status: "saved";
  readonly write_status: "created";
  readonly record_kind: "definition" | "observation";
  readonly record_id: string;
  readonly plan_sha256: string;
}

export interface GrowthMappingReviewOption {
  readonly option_index: number;
  readonly option_fingerprint: string;
  readonly label: string;
}

export interface GrowthMappingReviewResponse {
  readonly generated_at: string;
  readonly goal: GrowthGoalIdentity;
  readonly behavioral_target: Readonly<Record<string, unknown>>;
  readonly candidate_mapping_fingerprint: string | null;
  readonly goal_text: string;
  readonly goal_domain: string;
  readonly goal_evidence_at: string;
  readonly goal_evidence_at_precision: string;
  readonly situation: string;
  readonly information_known_at_decision_time: string;
  readonly criteria: readonly string[];
  readonly ordered_options: readonly GrowthMappingReviewOption[];
  readonly pattern_type: string;
  readonly pattern_state: string;
  readonly selected_option: GrowthBehavioralOption;
  readonly proposed_relation: GrowthRelation | null;
  readonly caveats: readonly string[];
}

export type GrowthMappingLifecycleState = "active" | "superseded" | "invalidated" | "deleted";

export interface GrowthMappingStatusItem {
  readonly mapping_id: string;
  readonly lifecycle_state: GrowthMappingLifecycleState;
  readonly goal: {
    readonly source_note_uuid: string;
    readonly domain: string;
    readonly source_fingerprint: string;
    readonly claim_fingerprint: string;
  };
  readonly behavioral_target: {
    readonly cohort_fingerprint: string;
    readonly option_index: number;
    readonly option_fingerprint: string;
  };
  readonly relation: GrowthRelation;
  readonly mapping_fingerprint: string;
  readonly mapping_policy_id: string;
  readonly mapping_policy_fingerprint: string;
  readonly created_at: string;
  readonly reviewed_at: string;
  readonly supersedes_mapping_id: string | null;
}

export interface GrowthMappingStatusResponse {
  readonly mapping_policy_id: string;
  readonly mapping_policy_fingerprint: string;
  readonly mappings: readonly GrowthMappingStatusItem[];
  readonly active_mapping_count: number;
}

export interface GrowthMappingAcceptedResponse {
  readonly status: "accepted";
  readonly mapping: Readonly<Record<string, unknown>>;
}

export interface GrowthMappingLifecycleResponse {
  readonly status: "updated";
  readonly event: Readonly<Record<string, unknown>>;
}

export interface GrowthAdvisorRequest {
  readonly contract_version: "growth-advisor-v1";
  readonly goal_source_uuid: string;
  readonly goal_identity_fingerprint: string;
  readonly task: string;
  readonly options: readonly { readonly id: string; readonly label: string }[];
  readonly explicit_constraints: readonly string[];
  readonly explicit_context: readonly { readonly kind: "fact" | "background"; readonly text: string }[];
  readonly max_context_bytes: number;
  readonly max_result_bytes: number;
}

export interface GrowthAdvisorPreviewResponse {
  readonly contract_version: "growth-advisor-v1";
  readonly goal_source_uuid: string;
  readonly goal_identity_fingerprint: string;
  readonly assistant_contract_version: string;
  readonly advisor_policy_id: string;
  readonly goal_text: string;
  readonly goal_text_utf8_bytes: number;
}

export interface GrowthAdvisorBranchResponse {
  readonly branch: "advisor";
  readonly state: "result" | "abstention" | "error";
  readonly assistant_result: Readonly<Record<string, unknown>> | null;
  readonly error: { readonly code: string; readonly message: string } | null;
  readonly provenance: Readonly<Record<string, unknown>> | null;
}

export interface DecisionCompassOption {
  readonly id: string;
  readonly label: string;
}

export interface DecisionCompassCriterion {
  readonly id: string;
  readonly label: string;
  readonly description: string | null;
}

export interface DecisionCompassRequest {
  readonly contract_version: "growth-compare-v1";
  readonly task: string;
  readonly options: readonly DecisionCompassOption[];
  readonly selected_goal: {
    readonly source_uuid: string;
    readonly identity_fingerprint: string;
  } | null;
  readonly criteria: readonly DecisionCompassCriterion[];
  readonly explicit_constraints: readonly string[];
  readonly explicit_context: readonly { readonly kind: "fact" | "background"; readonly text: string }[];
  readonly progress_as_of: string;
  readonly behavioral_scope: { readonly behavioral_cohort_fingerprint: string } | null;
  readonly behavioral_option_binding: {
    readonly request_option_id: string;
    readonly behavioral_cohort_fingerprint: string;
    readonly behavioral_option_index: number;
    readonly behavioral_option_fingerprint: string;
  } | null;
  readonly max_result_bytes: number;
}

export interface DecisionCompassBranchError {
  readonly code: string;
  readonly message: string;
}

export interface DecisionCompassSimulateBranch {
  readonly state: string;
  readonly result: SimulateMeResponse | null;
  readonly error: DecisionCompassBranchError | null;
}

export interface DecisionCompassBehaviorBranch {
  readonly state: string;
  readonly pattern: BehavioralPattern | null;
  readonly error: DecisionCompassBranchError | null;
}

export interface DecisionCompassAdvisorBranch {
  readonly state: "not_requested" | "result" | "abstention" | "error";
  readonly result: Readonly<Record<string, unknown>> | null;
  readonly error: DecisionCompassBranchError | null;
}

export interface DecisionCompassStructuralRelation {
  readonly code: string;
  readonly left_branch: string;
  readonly right_branch: string;
  readonly left_state: string;
  readonly right_state: string;
  readonly left_option_id: string | null;
  readonly right_option_id: string | null;
}

export interface DecisionCompassResponse {
  readonly contract_version: string;
  readonly derivation_version: string;
  readonly policy_id: string;
  readonly policy_fingerprint: string;
  readonly selected_goal: NonNullable<DecisionCompassRequest["selected_goal"]>;
  readonly request: DecisionCompassRequest;
  readonly simulate_me: DecisionCompassSimulateBranch;
  readonly behavioral: DecisionCompassBehaviorBranch;
  readonly growth_progress: GrowthGoalProgressCompositionResponse;
  readonly advisor: DecisionCompassAdvisorBranch;
  readonly structural_relations: readonly DecisionCompassStructuralRelation[];
  readonly caveats: readonly string[];
  readonly provenance: Readonly<Record<string, unknown>>;
}

export interface GrowthLearningRequest {
  readonly contract_version: "growth-learning-v1";
  readonly goal_source_uuid: string | null;
}

export interface GrowthLearningCandidate {
  readonly contract_version: "growth-learning-v1";
  readonly derivation_version: string;
  readonly candidate_id: string;
  readonly kind: "relation_review" | "reflection" | "context_clarification" | "evidence_clarification";
  readonly reason_code: "missing_goal_mapping" | "explicit_goal_conflict" | "mixed_behavior" | "changed_behavior" | "behavioral_evidence_insufficient";
  readonly growth_contract_version: string;
  readonly growth_derivation_version: string;
  readonly growth_policy_id: string;
  readonly growth_policy_fingerprint: string;
  readonly goal_source_uuid: string;
  readonly goal_identity_fingerprint: string;
  readonly growth_state: GrowthState;
  readonly cohort_fingerprint: string | null;
  readonly behavioral_option_fingerprint: string | null;
  readonly behavioral_reference_fingerprint: string | null;
  readonly mapping_id: string | null;
  readonly mapping_fingerprint: string | null;
  readonly question: string;
  readonly basis_fingerprint: string;
  readonly issued_at: string;
  readonly expires_at: string;
}

export interface GrowthLearningResult {
  readonly contract_version: "growth-learning-v1";
  readonly status: "candidate" | "no_candidate";
  readonly candidate: GrowthLearningCandidate | null;
  readonly no_candidate_code: string | null;
}

export type GrowthLearningDisposition = "ignore" | "reject" | "review" | "answer";

export interface GrowthLearningResolutionResponse {
  readonly candidate_id: string;
  readonly disposition: GrowthLearningDisposition;
  readonly answer_draft: { readonly candidate_id: string; readonly text: string } | null;
  readonly handoff: {
    readonly candidate_id: string;
    readonly kind: "relation_review" | "personal_memory_review";
    readonly selector: StatedObservedMappingSelector | null;
  } | null;
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

export function loadGrowthGoals(
  fetcher: FetchLike = fetchDefault,
  signal?: AbortSignal,
): Promise<GrowthGoalsResponse> {
  return requestJson(
    "/api/growth/goals",
    "growth-engine-v1",
    {},
    fetcher,
    "Не удалось загрузить текущие цели.",
    signal,
  );
}

export function loadGrowthMappingStatus(
  fetcher: FetchLike = fetchDefault,
  signal?: AbortSignal,
): Promise<GrowthMappingStatusResponse> {
  return requestJson(
    "/api/growth/mappings/status",
    "growth-engine-v1",
    {},
    fetcher,
    "Не удалось загрузить состояние сопоставлений развития.",
    signal,
  );
}

export function loadGrowth(
  goalSourceUuid: string,
  fetcher: FetchLike = fetchDefault,
  signal?: AbortSignal,
): Promise<GrowthResponse> {
  return requestJson(
    "/api/growth",
    "growth-engine-v1",
    {
      contract_version: "growth-engine-v1",
      selection: { mode: "selected_goal", source_note_uuid: goalSourceUuid },
      max_results: 200,
      max_result_bytes: 131072,
    },
    fetcher,
    "Не удалось построить текущую картину развития.",
    signal,
  );
}

export function loadGoalProgress(
  goalSourceUuid: string,
  asOf: string,
  fetcher: FetchLike = fetchDefault,
  signal?: AbortSignal,
): Promise<GoalProgressResponse> {
  return requestJson(
    "/api/goal-progress",
    "goal-progress-v1",
    { goal_source_uuid: goalSourceUuid, as_of: asOf },
    fetcher,
    "Не удалось проверить измеряемый прогресс.",
    signal,
  );
}

export function loadGrowthGoalProgress(
  goalSourceUuid: string,
  progressAsOf: string,
  fetcher: FetchLike = fetchDefault,
  signal?: AbortSignal,
): Promise<GrowthGoalProgressCompositionResponse> {
  return requestJson(
    "/api/growth-goal-progress",
    "growth-goal-progress-composition-v1",
    { goal_source_uuid: goalSourceUuid, progress_as_of: progressAsOf },
    fetcher,
    "Не удалось проверить связь поведения с целью и измеряемый прогресс.",
    signal,
  );
}

export function prepareGoalProgressDefinition(
  request: GoalProgressDefinitionPrepareRequest,
  fetcher: FetchLike = fetchDefault,
  signal?: AbortSignal,
): Promise<GoalProgressReviewResponse> {
  return requestJson(
    "/api/goal-progress/definitions/prepare",
    "goal-progress-v1",
    request,
    fetcher,
    "Не удалось подготовить правило измерения.",
    signal,
  );
}

export function applyGoalProgressDefinition(
  reviewToken: string,
  acceptedPlanSha256: string,
  fetcher: FetchLike = fetchDefault,
  signal?: AbortSignal,
): Promise<GoalProgressApplyResponse> {
  return requestJson(
    "/api/goal-progress/definitions/apply",
    "goal-progress-v1",
    { review_token: reviewToken, accepted_plan_sha256: acceptedPlanSha256, confirmed: true },
    fetcher,
    "Не удалось сохранить правило измерения.",
    signal,
  );
}

export function prepareGoalProgressObservation(
  request: GoalProgressObservationPrepareRequest,
  fetcher: FetchLike = fetchDefault,
  signal?: AbortSignal,
): Promise<GoalProgressReviewResponse> {
  return requestJson(
    "/api/goal-progress/observations/prepare",
    "goal-progress-v1",
    request,
    fetcher,
    "Не удалось подготовить запись наблюдения.",
    signal,
  );
}

export function applyGoalProgressObservation(
  reviewToken: string,
  acceptedPlanSha256: string,
  fetcher: FetchLike = fetchDefault,
  signal?: AbortSignal,
): Promise<GoalProgressApplyResponse> {
  return requestJson(
    "/api/goal-progress/observations/apply",
    "goal-progress-v1",
    { review_token: reviewToken, accepted_plan_sha256: acceptedPlanSha256, confirmed: true },
    fetcher,
    "Не удалось сохранить запись наблюдения.",
    signal,
  );
}

export function reviewGrowthMapping(
  selector: StatedObservedMappingSelector,
  relation: GrowthRelation,
  fetcher: FetchLike = fetchDefault,
  signal?: AbortSignal,
): Promise<GrowthMappingReviewResponse> {
  return requestJson(
    "/api/growth/mappings/review",
    "growth-engine-v1",
    { ...selector, relation },
    fetcher,
    "Не удалось подготовить проверку связи развития.",
    signal,
  );
}

export function confirmGrowthMapping(
  selector: StatedObservedMappingSelector,
  relation: GrowthRelation,
  operationId: string,
  reviewFingerprint: string,
  fetcher: FetchLike = fetchDefault,
  signal?: AbortSignal,
): Promise<GrowthMappingAcceptedResponse> {
  return requestJson(
    "/api/growth/mappings/confirm",
    "growth-engine-v1",
    {
      ...selector,
      operation_id: operationId,
      relation,
      confirmed: true,
      review_fingerprint: reviewFingerprint,
      supersedes_mapping_id: null,
    },
    fetcher,
    "Связь развития не принята. Повтори проверку.",
    signal,
  );
}

export function previewGrowthAdvisor(
  request: GrowthAdvisorRequest,
  fetcher: FetchLike = fetchDefault,
  signal?: AbortSignal,
): Promise<GrowthAdvisorPreviewResponse> {
  return requestJson(
    "/api/growth-advisor/preview",
    "growth-advisor-v1",
    request,
    fetcher,
    "Не удалось подготовить независимую рекомендацию.",
    signal,
  );
}

export function executeGrowthAdvisor(
  request: GrowthAdvisorRequest,
  preview: GrowthAdvisorPreviewResponse,
  fetcher: FetchLike = fetchDefault,
  signal?: AbortSignal,
): Promise<GrowthAdvisorBranchResponse> {
  return requestJson(
    "/api/growth-advisor/execute",
    "growth-advisor-v1",
    { request, preview, confirmed: true },
    fetcher,
    "Независимая рекомендация недоступна.",
    signal,
  );
}

export function buildDecisionCompass(
  request: DecisionCompassRequest,
  fetcher: FetchLike = fetchDefault,
  signal?: AbortSignal,
): Promise<DecisionCompassResponse> {
  return requestJson(
    "/api/decision-compass",
    "decision-compass-v1",
    request,
    fetcher,
    "Не удалось построить компас решения.",
    signal,
  );
}

export function previewDecisionCompassAdvisor(
  request: DecisionCompassRequest,
  fetcher: FetchLike = fetchDefault,
  signal?: AbortSignal,
): Promise<GrowthAdvisorPreviewResponse> {
  return requestJson(
    "/api/decision-compass/advisor/preview",
    "decision-compass-v1",
    request,
    fetcher,
    "Не удалось подготовить независимую рекомендацию.",
    signal,
  );
}

export function executeDecisionCompassAdvisor(
  request: DecisionCompassRequest,
  preview: GrowthAdvisorPreviewResponse,
  fetcher: FetchLike = fetchDefault,
  signal?: AbortSignal,
): Promise<DecisionCompassResponse> {
  return requestJson(
    "/api/decision-compass/advisor/execute",
    "decision-compass-v1",
    { request, preview, confirmed: true },
    fetcher,
    "Независимая рекомендация недоступна.",
    signal,
  );
}

export function requestGrowthLearningQuestion(
  request: GrowthLearningRequest,
  fetcher: FetchLike = fetchDefault,
  signal?: AbortSignal,
): Promise<GrowthLearningResult> {
  return requestJson(
    "/api/growth-learning/questions",
    "growth-learning-v1",
    request,
    fetcher,
    "Не удалось подготовить вопрос для уточнения.",
    signal,
  );
}

export function resolveGrowthLearningQuestion(
  request: GrowthLearningRequest,
  candidate: GrowthLearningCandidate,
  disposition: GrowthLearningDisposition,
  answer: string | null,
  fetcher: FetchLike = fetchDefault,
  signal?: AbortSignal,
): Promise<GrowthLearningResolutionResponse> {
  return requestJson(
    "/api/growth-learning/questions/resolve",
    "growth-learning-v1",
    {
      request,
      candidate,
      resolution: {
        candidate_id: candidate.candidate_id,
        disposition,
        answer,
      },
    },
    fetcher,
    "Не удалось завершить вопрос для уточнения.",
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
