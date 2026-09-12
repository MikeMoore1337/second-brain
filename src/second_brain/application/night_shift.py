"""Bounded Night Shift policy parser and repository gate helpers."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Final

from ruamel.yaml import YAML


class NightShiftConfigError(ValueError):
    """Policy document is not a valid versioned Night Shift contract."""


class RiskLane(StrEnum):
    """Risk classification used by implementation and merge gates."""

    GREEN = "GREEN"
    YELLOW = "YELLOW"
    RED = "RED"


class TaskState(StrEnum):
    """Small GitHub-backed task state machine."""

    QUEUED = "queued"
    IN_PROGRESS = "in_progress"
    PR_OPEN = "pr_open"
    FIX_REQUIRED = "fix_required"
    CI_WAIT = "ci_wait"
    MERGE_READY = "merge_ready"
    MERGED = "merged"
    HUMAN_REQUIRED = "human_required"
    BLOCKED = "blocked"


class TaskSelectionSource(StrEnum):
    """Allowed sources for selecting one next task."""

    CURRENT_ACTIVE = "current_explicitly_active"
    OVERNIGHT_QUEUED = "overnight_queued"
    ROADMAP_NEXT = "roadmap_next"


class GateStatus(StrEnum):
    """Bounded result of a start, budget, or merge gate."""

    READY = "ready"
    BLOCKED = "blocked"
    HUMAN_REQUIRED = "human_required"
    MERGE_READY = "merge_ready"


class CleanupLifecycle(StrEnum):
    """Bounded callers allowed to invoke the worktree cleanup helper."""

    VERIFIED_GREEN_MERGE = "verified_green_merge"
    HISTORICAL_ORPHAN_PASS = "historical_orphan_pass"


class CheckConclusion(StrEnum):
    """CI conclusion values accepted by the merge gate."""

    SUCCESS = "success"
    PENDING = "pending"
    FAILURE = "failure"


class WorktreeOperation(StrEnum):
    """Operation class used by the scoped worktree/finalization guard."""

    IMPLEMENTATION = "implementation"
    FINALIZATION = "finalization"
    READ_ONLY = "read_only"


class CodexReviewConclusion(StrEnum):
    """Semantic result of one managed Codex Code Review request."""

    CLEAN = "clean"
    BLOCKING = "blocking"


class ReviewGateAction(StrEnum):
    """Action selected by the bounded review gate."""

    REQUEST = "request"
    REUSE = "reuse"
    MERGE = "merge"


SUPPORTED_POLICY_VERSION: Final[str] = "night-shift-v1"
SUPPORTED_SCHEMA_VERSION: Final[int] = 3
REQUIRED_MERGE_CHECKS: Final[frozenset[str]] = frozenset(
    {
        "quality",
        "windows-ssl-regression",
        "frontend (ubuntu-latest)",
        "frontend (windows-latest)",
    }
)
REQUIRED_MERGE_STATUS_CONTEXTS: Final[frozenset[str]] = frozenset()
SUPPORTED_SCOPED_LOCKS: Final[frozenset[str]] = frozenset(
    {"worktree", "task_state", "finalization", "production_deployment"}
)
EXPECTED_FINALIZATION_PHASES: Final[tuple[str, ...]] = (
    "refresh_base",
    "resolve_conflicts",
    "affected_verification",
    "final_push",
    "exact_head_ci",
    "codex_review",
    "merge",
    "release_closeout",
)
SUPPORTED_RED_GATES: Final[frozenset[str]] = frozenset(
    {
        "canonical_schema_or_version",
        "evidence_kind",
        "self_kind",
        "canonical_migration",
        "inference_confidence",
        "inference_conflict",
        "inference_stale",
        "inference_supersede",
        "inference_preferences_values",
        "assistant_simulate_me_boundary",
        "provider_public_web_auth_privacy",
        "destructive_operation",
        "new_database",
        "new_vector_database",
        "new_graph_database",
        "new_redis_kafka_celery_event_store",
        "architecture_replacement",
        "roadmap_change",
        "unresolved_product_decision",
    }
)

_CUTOFF_PATTERN: Final[re.Pattern[str]] = re.compile(r"(?:[01]\d|2[0-3]):[0-5]\d")
_EXPECTED_BASE_REF: Final[str] = "main"
_ACTIVE_STATES: Final[frozenset[TaskState]] = frozenset(
    {
        TaskState.IN_PROGRESS,
        TaskState.PR_OPEN,
        TaskState.FIX_REQUIRED,
        TaskState.CI_WAIT,
        TaskState.MERGE_READY,
    }
)


@dataclass(frozen=True, slots=True)
class RiskPolicy:
    """Allowed start and merge behavior for one risk lane."""

    lane: RiskLane
    auto_merge_allowed: bool
    human_merge_required: bool
    start_allowed: bool


@dataclass(frozen=True, slots=True)
class FailureBudget:
    """Per-night and per-task limits; flaky reruns are evidence-bound."""

    max_tasks_per_night: int
    max_ci_fix_cycles_per_task: int
    max_scope_expansion: int
    flaky_ci_retry_requires_no_code_change: bool


@dataclass(frozen=True, slots=True)
class PostTaskCleanupPolicy:
    """Versioned safety switches for post-task Git worktree cleanup."""

    enabled_after_verified_green_merge: bool
    helper: str
    prune_after_successful_removals: bool
    allow_force: bool
    allow_remote_branch_delete: bool
    allow_local_branch_delete: bool
    failure_is_task_failure: bool


@dataclass(frozen=True, slots=True)
class ImplementationPolicy:
    """Ownership rules for independent task implementation worktrees."""

    parallel_independent_tasks: bool
    worktree_ownership: str
    repository_wide_exclusive_write: bool
    diagnostics_require_write_lease: bool
    scoped_locks: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class FinalizationPolicy:
    """The only repository-scoped serialized part of delivery."""

    serialized: bool
    scope: str
    phases: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class CodexReviewPolicy:
    """Bounded semantic review contract; it is not an automatic lifecycle."""

    enabled: bool
    automatic_lifecycle: bool
    max_requests_per_pr: int
    max_self_reviews: int
    first_round_requires_exact_head_ci_green: bool
    second_round_requires_blocking_p0_p1: bool
    second_round_requires_changed_head: bool
    duplicate_same_sha: str
    blocking_after_round_2: str


@dataclass(frozen=True, slots=True)
class NightShiftPolicy:
    """Validated immutable policy, not mutable runtime state."""

    policy_version: str
    schema_version: int
    enabled_by_default: bool
    morning_cutoff_local: str
    timezone: str
    task_states: tuple[TaskState, ...]
    risk_lanes: tuple[RiskPolicy, ...]
    red_gates: tuple[str, ...]
    failure_budget: FailureBudget
    dependency_satisfied_state: TaskState
    dependency_blocking_states: tuple[TaskState, ...]
    task_selection_order: tuple[TaskSelectionSource, ...]
    allow_roadmap_next_task: bool
    allow_create_next_issue: bool
    required_checks: tuple[str, ...]
    required_statuses: tuple[str, ...]
    merge_method: str
    implementation: ImplementationPolicy
    finalization: FinalizationPolicy
    codex_review: CodexReviewPolicy
    post_task_cleanup: PostTaskCleanupPolicy


@dataclass(frozen=True, slots=True)
class GateResult:
    """Deterministic gate outcome with machine-readable reasons."""

    status: GateStatus
    reasons: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ReviewGateResult:
    """Bounded review action plus its deterministic gate status."""

    status: GateStatus
    action: ReviewGateAction | None
    reasons: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class WorktreeAccessEvidence:
    """Already-resolved ownership state supplied by a lifecycle orchestrator."""

    operation: WorktreeOperation
    task_id: str
    worktree_id: str
    worktree_owner_task_id: str | None = None
    finalization_owner_task_id: str | None = None
    repository_has_other_implementation: bool = False
    stale_owner_confirmed: bool = False


@dataclass(frozen=True, slots=True)
class PostTaskCleanupEvidence:
    """Orchestrator receipt needed before a completed task may be cleaned."""

    lifecycle: CleanupLifecycle
    task_state: str
    pr_merged: bool
    issue_completed: bool
    main_sha: str
    worktree_registered: bool


def evaluate_post_task_cleanup(evidence: PostTaskCleanupEvidence) -> GateResult:
    """Require a verified merged task before invoking native Git cleanup."""

    if evidence.task_state != TaskState.MERGED.value:
        return _blocked("task_is_not_merged")
    if not evidence.pr_merged:
        return _blocked("pr_is_not_merged")
    if not evidence.issue_completed:
        return _blocked("issue_is_not_completed")
    if not _is_full_commit_sha(evidence.main_sha):
        return _blocked("new_main_sha_is_not_verified")
    if not evidence.worktree_registered:
        return _blocked("worktree_is_not_registered")
    if evidence.lifecycle is CleanupLifecycle.VERIFIED_GREEN_MERGE:
        return _ready("verified_green_merge_cleanup_required")
    if evidence.lifecycle is CleanupLifecycle.HISTORICAL_ORPHAN_PASS:
        return _ready("verified_historical_cleanup_candidate")
    return _blocked("unknown_cleanup_lifecycle")


def evaluate_worktree_access(
    policy: NightShiftPolicy, evidence: WorktreeAccessEvidence
) -> GateResult:
    """Allow parallel implementation and serialize only scoped finalization.

    The repository has no local lease registry or controller.  An orchestrator
    that has one supplies the already-resolved owner identities here.  This
    guard keeps the contract executable without introducing a daemon or a
    repository-wide lock.
    """

    if not evidence.task_id.strip():
        return _blocked("task_identity_missing")
    if not evidence.worktree_id.strip():
        return _blocked("worktree_identity_missing")
    if evidence.operation is WorktreeOperation.READ_ONLY:
        return _ready("read_only_does_not_require_implementation_lease")

    implementation = policy.implementation
    if (
        evidence.operation is WorktreeOperation.IMPLEMENTATION
        and implementation.repository_wide_exclusive_write
        and evidence.repository_has_other_implementation
    ):
        return _blocked("repository_wide_implementation_lock_conflict")

    if evidence.operation is WorktreeOperation.IMPLEMENTATION:
        owner = evidence.worktree_owner_task_id
        if owner is not None and owner != evidence.task_id:
            if evidence.stale_owner_confirmed:
                return _ready("stale_task_worktree_lease_reclaimable")
            return _blocked("worktree_owned_by_another_task")
        return _ready("parallel_implementation_allowed")

    if evidence.operation is WorktreeOperation.FINALIZATION:
        finalization_owner = evidence.finalization_owner_task_id
        if finalization_owner is not None and finalization_owner != evidence.task_id:
            if evidence.stale_owner_confirmed:
                return _ready("stale_finalization_lease_reclaimable")
            return _blocked("finalization_owned_by_another_task")
        owner = evidence.worktree_owner_task_id
        if owner is not None and owner != evidence.task_id:
            if evidence.stale_owner_confirmed:
                return _ready("stale_task_worktree_lease_reclaimable")
            return _blocked("worktree_owned_by_another_task")
        return _ready("finalization_lane_available")

    return _blocked("unknown_worktree_operation")


@dataclass(frozen=True, slots=True)
class TaskCandidate:
    """Read-only candidate assembled from GitHub issue/PR state."""

    issue_number: int
    state: TaskState
    risk_lane: RiskLane
    source: TaskSelectionSource
    dependencies: tuple[int, ...] = ()
    dependency_states: Mapping[int, TaskState] = field(default_factory=dict)
    human_required: bool = False
    scope_expansions: int = 0


@dataclass(frozen=True, slots=True)
class FailureBudgetUsage:
    """Counters reconstructed from GitHub history, never persisted here."""

    tasks_started: int = 0
    ci_fix_cycles: int = 0
    scope_expansions: int = 0
    flaky_ci_retries: int = 0
    flaky_ci_retry_has_evidence: bool = False
    flaky_ci_retry_code_changed: bool | None = None


@dataclass(frozen=True, slots=True)
class CheckRunEvidence:
    """One required check result bound to its head and latest run identity."""

    conclusion: CheckConclusion | str
    head_sha: str
    run_id: int
    run_is_latest: bool | None = None
    base_sha: str | None = None


@dataclass(frozen=True, slots=True)
class CodexReviewRequestEvidence:
    """PR, exact-head CI, and prior-review facts for one request decision."""

    pr_exists: bool
    pr_is_draft: bool
    current_head_sha: str
    current_base_sha: str
    check_evidence: Mapping[str, CheckRunEvidence]
    managed_review_count: int = 0
    existing_review_head_sha: str | None = None
    existing_review_pending_or_completed: bool = False
    prior_review_blocking_p0_p1: bool = False
    prior_review_head_sha: str | None = None


@dataclass(frozen=True, slots=True)
class CodexReviewOutcomeEvidence:
    """One completed managed review result bound to an exact PR head."""

    review_round: int
    head_sha: str
    conclusion: CodexReviewConclusion | str
    managed_review_count: int


@dataclass(frozen=True, slots=True)
class MergeGateEvidence:
    """Evidence required before a GREEN squash merge can be considered."""

    risk_lane: RiskLane
    current_head_sha: str
    current_base_ref: str
    current_base_sha: str
    check_evidence: Mapping[str, CheckRunEvidence]
    unresolved_review_threads: int
    unresolved_blockers: int
    mergeable_clean: bool
    dependency_satisfied: bool
    human_gate: bool
    scope_unchanged: bool
    codex_review: CodexReviewOutcomeEvidence | None = None


def load_night_shift_policy(path: Path) -> NightShiftPolicy:
    """Load and strictly validate one repository policy YAML."""

    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise NightShiftConfigError(f"cannot read Night Shift policy: {exc}") from None

    yaml = YAML(typ="safe")
    yaml.allow_duplicate_keys = False
    try:
        raw: object = yaml.load(text)
    except Exception as exc:  # ruamel exposes several parser/constructor types
        raise NightShiftConfigError(f"cannot parse Night Shift policy: {exc}") from None
    return _parse_policy(raw)


def evaluate_task_start(
    policy: NightShiftPolicy,
    candidate: TaskCandidate,
    *,
    night_mode_enabled: bool,
) -> GateResult:
    """Decide whether one candidate may be continued or started."""

    if not night_mode_enabled:
        return _human("night_mode_not_explicitly_enabled")
    risk_policy = _risk_policy(policy, candidate.risk_lane)
    if not risk_policy.start_allowed:
        return _human("red_risk_lane")
    if candidate.human_required or candidate.state is TaskState.HUMAN_REQUIRED:
        return _human("task_has_human_required_gate")
    if candidate.scope_expansions > policy.failure_budget.max_scope_expansion:
        return _human("scope_expansion_exceeds_budget")

    if candidate.source is TaskSelectionSource.CURRENT_ACTIVE:
        if candidate.state not in {TaskState.QUEUED, *_ACTIVE_STATES}:
            return _blocked("current_task_is_not_continuable")
    elif candidate.state is not TaskState.QUEUED:
        return _blocked("queued_candidate_has_non_queued_state")

    for dependency in candidate.dependencies:
        dependency_state = candidate.dependency_states.get(dependency)
        if dependency_state is None:
            return _blocked(f"dependency_{dependency}_state_missing")
        if dependency_state in policy.dependency_blocking_states:
            return _human(f"dependency_{dependency}_is_{dependency_state.value}")
        if dependency_state is not policy.dependency_satisfied_state:
            return _blocked(f"dependency_{dependency}_is_not_merged")

    if candidate.risk_lane is RiskLane.YELLOW:
        return _ready("yellow_implementation_allowed_human_merge_required")
    return _ready("green_task_ready")


def select_next_task(
    policy: NightShiftPolicy,
    candidates: tuple[TaskCandidate, ...],
    *,
    night_mode_enabled: bool,
) -> tuple[TaskCandidate | None, GateResult]:
    """Select one executable candidate without inventing a roadmap task."""

    if not night_mode_enabled:
        return None, _human("night_mode_not_explicitly_enabled")

    deferred_human: GateResult | None = None
    for source in policy.task_selection_order:
        if source is TaskSelectionSource.ROADMAP_NEXT and not policy.allow_roadmap_next_task:
            continue
        for candidate in candidates:
            if candidate.source is not source:
                continue
            result = evaluate_task_start(policy, candidate, night_mode_enabled=True)
            if result.status is GateStatus.READY:
                return candidate, result
            if result.status is GateStatus.HUMAN_REQUIRED:
                if result.reasons[0].startswith("dependency_") or result.reasons[0] == (
                    "task_has_human_required_gate"
                ):
                    deferred_human = deferred_human or result
                    continue
                return None, result
    return None, deferred_human or _blocked("no_executable_task")


def evaluate_failure_budget(
    policy: NightShiftPolicy,
    usage: FailureBudgetUsage,
) -> GateResult:
    """Stop the loop when a bounded failure budget is exceeded."""

    limits = policy.failure_budget
    operation_counters = (
        (usage.tasks_started, limits.max_tasks_per_night, "max_tasks_per_night"),
        (usage.ci_fix_cycles, limits.max_ci_fix_cycles_per_task, "max_ci_fix_cycles_per_task"),
    )
    for used, maximum, name in operation_counters:
        if used < 0:
            return _human(f"negative_failure_budget_counter:{name}")
        if used >= maximum:
            return _human(f"failure_budget_exceeded:{name}")
    if usage.scope_expansions < 0:
        return _human("negative_failure_budget_counter:max_scope_expansion")
    if usage.scope_expansions > limits.max_scope_expansion:
        return _human("failure_budget_exceeded:max_scope_expansion")
    if usage.flaky_ci_retries < 0:
        return _human("negative_failure_budget_counter:flaky_ci_retries")
    if usage.flaky_ci_retries and not usage.flaky_ci_retry_has_evidence:
        return _human("flaky_ci_retry_has_no_evidence")
    if (
        usage.flaky_ci_retries
        and limits.flaky_ci_retry_requires_no_code_change
        and usage.flaky_ci_retry_code_changed is not False
    ):
        if usage.flaky_ci_retry_code_changed is None:
            return _human("flaky_ci_retry_no_code_change_not_verified")
        return _human("flaky_ci_retry_changed_code")
    return _ready("failure_budget_within_bounds")


def evaluate_codex_review_request(
    policy: NightShiftPolicy, evidence: CodexReviewRequestEvidence
) -> ReviewGateResult:
    """Decide whether to request or reuse one bounded review for the PR head."""

    review_policy = policy.codex_review
    if not review_policy.enabled:
        return _review_human("codex_review_disabled_by_policy")
    if not evidence.pr_exists:
        return _review_blocked("pull_request_missing_before_review")
    if evidence.pr_is_draft:
        return _review_blocked("pull_request_is_draft")
    if not _is_full_commit_sha(evidence.current_head_sha):
        return _review_blocked("current_head_sha_is_not_full_commit_sha")
    if not _is_full_commit_sha(evidence.current_base_sha):
        return _review_blocked("current_base_sha_is_not_full_commit_sha")
    if evidence.managed_review_count < 0:
        return _review_human("negative_codex_review_counter")

    if evidence.existing_review_head_sha == evidence.current_head_sha:
        if evidence.existing_review_pending_or_completed:
            return ReviewGateResult(
                GateStatus.READY,
                ReviewGateAction.REUSE,
                ("reuse_existing_codex_review_for_current_head",),
            )
        return _review_blocked("existing_current_head_review_state_not_reusable")

    check_failure = _required_check_failure(
        policy,
        current_head_sha=evidence.current_head_sha,
        current_base_sha=evidence.current_base_sha,
        check_evidence=evidence.check_evidence,
    )
    if check_failure is not None:
        return _review_blocked(check_failure)

    if evidence.managed_review_count >= review_policy.max_requests_per_pr:
        return _review_human("codex_review_budget_exhausted")
    if evidence.managed_review_count == 0:
        return ReviewGateResult(
            GateStatus.READY,
            ReviewGateAction.REQUEST,
            ("request_codex_review_round_1",),
        )
    if evidence.managed_review_count != 1:
        return _review_human("codex_review_counter_has_unknown_round")
    if not evidence.prior_review_blocking_p0_p1:
        return _review_human("clean_review_requires_merge_without_rereview")
    if not review_policy.second_round_requires_blocking_p0_p1:
        return _review_human("round_2_disabled_without_blocking_findings")
    if evidence.prior_review_head_sha in {None, evidence.current_head_sha}:
        return _review_blocked("round_2_requires_changed_head")
    if not review_policy.second_round_requires_changed_head:
        return _review_human("round_2_changed_head_requirement_disabled")
    return ReviewGateResult(
        GateStatus.READY,
        ReviewGateAction.REQUEST,
        ("request_codex_review_round_2",),
    )


def evaluate_codex_review_outcome(
    policy: NightShiftPolicy, evidence: CodexReviewOutcomeEvidence
) -> ReviewGateResult:
    """Route a completed review without permitting an unbounded review loop."""

    review_policy = policy.codex_review
    if not review_policy.enabled:
        return _review_human("codex_review_disabled_by_policy")
    if evidence.review_round < 1:
        return _review_blocked("codex_review_round_is_invalid")
    if evidence.review_round > review_policy.max_requests_per_pr:
        return _review_human("codex_review_budget_exhausted")
    if evidence.managed_review_count != evidence.review_round:
        return _review_blocked("codex_review_count_does_not_match_round")
    if not _is_full_commit_sha(evidence.head_sha):
        return _review_blocked("codex_review_head_sha_is_not_full_commit_sha")
    try:
        conclusion = CodexReviewConclusion(evidence.conclusion)
    except ValueError:
        return _review_blocked("codex_review_conclusion_is_unknown")
    if conclusion is CodexReviewConclusion.CLEAN:
        return ReviewGateResult(
            GateStatus.MERGE_READY,
            ReviewGateAction.MERGE,
            ("codex_review_clean_no_rereview",),
        )
    if evidence.review_round < review_policy.max_requests_per_pr:
        return ReviewGateResult(
            GateStatus.READY,
            ReviewGateAction.REQUEST,
            ("blocking_review_requires_one_bounded_re_review",),
        )
    return _review_human("blocking_review_after_round_2_requires_human")


def evaluate_merge_gate(policy: NightShiftPolicy, evidence: MergeGateEvidence) -> GateResult:
    """Return merge-ready only when every configured GREEN gate is true."""

    if evidence.risk_lane is not RiskLane.GREEN:
        return _human(f"risk_lane_{evidence.risk_lane.value.lower()}_cannot_auto_merge")
    if evidence.current_base_ref != _EXPECTED_BASE_REF:
        return _blocked("merge_target_ref_is_not_main")
    if not _is_full_commit_sha(evidence.current_base_sha):
        return _blocked("current_base_sha_is_not_full_commit_sha")
    if not _is_full_commit_sha(evidence.current_head_sha):
        return _blocked("current_head_sha_is_not_full_commit_sha")
    if evidence.human_gate:
        return _human("human_gate_present")
    if not evidence.dependency_satisfied:
        return _blocked("dependency_gate_not_satisfied")
    if not evidence.scope_unchanged:
        return _human("task_scope_changed")
    if not evidence.mergeable_clean:
        return _blocked("pull_request_is_not_mergeable_clean")
    if evidence.unresolved_review_threads:
        return _blocked("unresolved_review_threads_present")
    if evidence.unresolved_blockers:
        return _human("unresolved_blockers_present")
    if evidence.codex_review is None:
        return _blocked("codex_review_not_completed")
    if not _is_full_commit_sha(evidence.codex_review.head_sha):
        return _blocked("codex_review_head_sha_is_not_full_commit_sha")
    if evidence.codex_review.head_sha != evidence.current_head_sha:
        return _blocked("codex_review_not_bound_to_current_head")
    if evidence.codex_review.review_round < 1:
        return _blocked("codex_review_round_is_invalid")
    if evidence.codex_review.review_round > policy.codex_review.max_requests_per_pr:
        return _human("codex_review_budget_exhausted")
    if evidence.codex_review.managed_review_count != evidence.codex_review.review_round:
        return _blocked("codex_review_count_does_not_match_round")
    try:
        review_conclusion = CodexReviewConclusion(evidence.codex_review.conclusion)
    except ValueError:
        return _blocked("codex_review_conclusion_is_unknown")
    if review_conclusion is CodexReviewConclusion.BLOCKING:
        if evidence.codex_review.review_round >= policy.codex_review.max_requests_per_pr:
            return _human("blocking_review_after_round_2_requires_human")
        return _blocked("codex_review_has_blocking_findings")
    check_failure = _required_check_failure(
        policy,
        current_head_sha=evidence.current_head_sha,
        current_base_sha=evidence.current_base_sha,
        check_evidence=evidence.check_evidence,
    )
    if check_failure is not None:
        return _blocked(check_failure)
    return GateResult(GateStatus.MERGE_READY, ("all_green_merge_gates_passed",))


def _parse_policy(raw: object) -> NightShiftPolicy:
    data = _mapping(raw, "policy")
    _require_exact_keys(
        data,
        {
            "policy_version",
            "schema_version",
            "enabled_by_default",
            "morning_cutoff_local",
            "timezone",
            "task_states",
            "risk_lanes",
            "red_gates",
            "failure_budget",
            "dependency_policy",
            "task_selection",
            "merge_gate",
            "implementation",
            "finalization",
            "codex_review",
            "post_task_cleanup",
        },
        "policy",
    )
    policy_version = _string(data["policy_version"], "policy_version")
    if policy_version != SUPPORTED_POLICY_VERSION:
        raise NightShiftConfigError(f"unsupported policy_version: {policy_version}")
    schema_version = _integer(data["schema_version"], "schema_version", minimum=1)
    if schema_version != SUPPORTED_SCHEMA_VERSION:
        raise NightShiftConfigError(f"unsupported schema_version: {schema_version}")
    enabled_by_default = _boolean(data["enabled_by_default"], "enabled_by_default")
    if enabled_by_default:
        raise NightShiftConfigError("night mode must be disabled by default")
    cutoff = _string(data["morning_cutoff_local"], "morning_cutoff_local")
    if _CUTOFF_PATTERN.fullmatch(cutoff) is None:
        raise NightShiftConfigError("morning_cutoff_local must be HH:MM")
    timezone = _string(data["timezone"], "timezone")

    task_states = _enum_list(data["task_states"], TaskState, "task_states")
    if set(task_states) != set(TaskState):
        raise NightShiftConfigError(
            "task_states must contain every supported task state exactly once"
        )
    risk_lanes = _parse_risk_lanes(data["risk_lanes"])
    red_gates = _unique_strings(data["red_gates"], "red_gates")
    if set(red_gates) != SUPPORTED_RED_GATES:
        raise NightShiftConfigError("red_gates must match the supported RED gate vocabulary")

    failure_data = _mapping(data["failure_budget"], "failure_budget")
    _require_exact_keys(
        failure_data,
        {
            "max_tasks_per_night",
            "max_ci_fix_cycles_per_task",
            "max_scope_expansion",
            "flaky_ci_retry_requires_no_code_change",
        },
        "failure_budget",
    )
    failure_budget = FailureBudget(
        max_tasks_per_night=_integer(
            failure_data["max_tasks_per_night"], "failure_budget.max_tasks_per_night", minimum=1
        ),
        max_ci_fix_cycles_per_task=_integer(
            failure_data["max_ci_fix_cycles_per_task"],
            "failure_budget.max_ci_fix_cycles_per_task",
            minimum=0,
        ),
        max_scope_expansion=_integer(
            failure_data["max_scope_expansion"], "failure_budget.max_scope_expansion", minimum=0
        ),
        flaky_ci_retry_requires_no_code_change=_boolean(
            failure_data["flaky_ci_retry_requires_no_code_change"],
            "failure_budget.flaky_ci_retry_requires_no_code_change",
        ),
    )

    dependency_data = _mapping(data["dependency_policy"], "dependency_policy")
    _require_exact_keys(
        dependency_data, {"satisfied_state", "blocking_states"}, "dependency_policy"
    )
    satisfied_state = _enum_value(
        dependency_data["satisfied_state"], TaskState, "dependency_policy.satisfied_state"
    )
    if satisfied_state is not TaskState.MERGED:
        raise NightShiftConfigError("dependency_policy.satisfied_state must be merged")
    blocking_states = _enum_list(
        dependency_data["blocking_states"], TaskState, "dependency_policy.blocking_states"
    )
    if set(blocking_states) != {TaskState.BLOCKED, TaskState.HUMAN_REQUIRED}:
        raise NightShiftConfigError(
            "dependency_policy.blocking_states must contain blocked and human_required"
        )

    selection_data = _mapping(data["task_selection"], "task_selection")
    _require_exact_keys(
        selection_data,
        {"order", "allow_roadmap_next_task", "allow_create_next_issue"},
        "task_selection",
    )
    selection_order = _enum_list(
        selection_data["order"], TaskSelectionSource, "task_selection.order"
    )
    if set(selection_order) != set(TaskSelectionSource):
        raise NightShiftConfigError(
            "task_selection.order must contain every selection source exactly once"
        )

    implementation = _parse_implementation_policy(data["implementation"])
    finalization = _parse_finalization_policy(data["finalization"])
    codex_review = _parse_codex_review_policy(data["codex_review"])

    merge_data = _mapping(data["merge_gate"], "merge_gate")
    _require_exact_keys(
        merge_data, {"required_checks", "required_statuses", "method"}, "merge_gate"
    )
    required_checks = _unique_strings(merge_data["required_checks"], "merge_gate.required_checks")
    if not REQUIRED_MERGE_CHECKS.issubset(required_checks):
        raise NightShiftConfigError(
            "merge_gate.required_checks must include every active ruleset check"
        )
    required_statuses = _unique_strings(
        merge_data["required_statuses"], "merge_gate.required_statuses"
    )
    if not REQUIRED_MERGE_STATUS_CONTEXTS.issubset(required_statuses):
        raise NightShiftConfigError("merge_gate.required_statuses is not compatible with ruleset")
    merge_method = _string(merge_data["method"], "merge_gate.method")
    if merge_method != "squash":
        raise NightShiftConfigError("merge_gate.method must be squash")

    cleanup_data = _mapping(data["post_task_cleanup"], "post_task_cleanup")
    _require_exact_keys(
        cleanup_data,
        {
            "enabled_after_verified_green_merge",
            "helper",
            "prune_after_successful_removals",
            "allow_force",
            "allow_remote_branch_delete",
            "allow_local_branch_delete",
            "failure_is_task_failure",
        },
        "post_task_cleanup",
    )
    cleanup_policy = PostTaskCleanupPolicy(
        enabled_after_verified_green_merge=_boolean(
            cleanup_data["enabled_after_verified_green_merge"],
            "post_task_cleanup.enabled_after_verified_green_merge",
        ),
        helper=_string(cleanup_data["helper"], "post_task_cleanup.helper"),
        prune_after_successful_removals=_boolean(
            cleanup_data["prune_after_successful_removals"],
            "post_task_cleanup.prune_after_successful_removals",
        ),
        allow_force=_boolean(cleanup_data["allow_force"], "post_task_cleanup.allow_force"),
        allow_remote_branch_delete=_boolean(
            cleanup_data["allow_remote_branch_delete"],
            "post_task_cleanup.allow_remote_branch_delete",
        ),
        allow_local_branch_delete=_boolean(
            cleanup_data["allow_local_branch_delete"],
            "post_task_cleanup.allow_local_branch_delete",
        ),
        failure_is_task_failure=_boolean(
            cleanup_data["failure_is_task_failure"],
            "post_task_cleanup.failure_is_task_failure",
        ),
    )
    if cleanup_policy.allow_force:
        raise NightShiftConfigError("post_task_cleanup.allow_force must be false")
    if cleanup_policy.allow_remote_branch_delete:
        raise NightShiftConfigError("post_task_cleanup.allow_remote_branch_delete must be false")
    if cleanup_policy.allow_local_branch_delete:
        raise NightShiftConfigError("post_task_cleanup.allow_local_branch_delete must be false")
    if cleanup_policy.failure_is_task_failure:
        raise NightShiftConfigError("post_task_cleanup.failure_is_task_failure must be false")

    return NightShiftPolicy(
        policy_version=policy_version,
        schema_version=schema_version,
        enabled_by_default=enabled_by_default,
        morning_cutoff_local=cutoff,
        timezone=timezone,
        task_states=task_states,
        risk_lanes=risk_lanes,
        red_gates=red_gates,
        failure_budget=failure_budget,
        dependency_satisfied_state=satisfied_state,
        dependency_blocking_states=blocking_states,
        task_selection_order=selection_order,
        allow_roadmap_next_task=_boolean(
            selection_data["allow_roadmap_next_task"], "task_selection.allow_roadmap_next_task"
        ),
        allow_create_next_issue=_boolean(
            selection_data["allow_create_next_issue"], "task_selection.allow_create_next_issue"
        ),
        required_checks=required_checks,
        required_statuses=required_statuses,
        merge_method=merge_method,
        implementation=implementation,
        finalization=finalization,
        codex_review=codex_review,
        post_task_cleanup=cleanup_policy,
    )


def _parse_implementation_policy(value: object) -> ImplementationPolicy:
    data = _mapping(value, "implementation")
    _require_exact_keys(
        data,
        {
            "parallel_independent_tasks",
            "worktree_ownership",
            "repository_wide_exclusive_write",
            "diagnostics_require_write_lease",
            "scoped_locks",
        },
        "implementation",
    )
    policy = ImplementationPolicy(
        parallel_independent_tasks=_boolean(
            data["parallel_independent_tasks"], "implementation.parallel_independent_tasks"
        ),
        worktree_ownership=_string(data["worktree_ownership"], "implementation.worktree_ownership"),
        repository_wide_exclusive_write=_boolean(
            data["repository_wide_exclusive_write"],
            "implementation.repository_wide_exclusive_write",
        ),
        diagnostics_require_write_lease=_boolean(
            data["diagnostics_require_write_lease"],
            "implementation.diagnostics_require_write_lease",
        ),
        scoped_locks=_unique_strings(data["scoped_locks"], "implementation.scoped_locks"),
    )
    if not policy.parallel_independent_tasks:
        raise NightShiftConfigError("implementation.parallel_independent_tasks must be true")
    if policy.worktree_ownership != "task_scoped":
        raise NightShiftConfigError("implementation.worktree_ownership must be task_scoped")
    if policy.repository_wide_exclusive_write:
        raise NightShiftConfigError("repository-wide implementation exclusive-write is forbidden")
    if policy.diagnostics_require_write_lease:
        raise NightShiftConfigError("diagnostics_require_write_lease must be false")
    if not set(policy.scoped_locks).issubset(SUPPORTED_SCOPED_LOCKS):
        raise NightShiftConfigError("implementation.scoped_locks contains an unsupported scope")
    if "repository" in policy.scoped_locks:
        raise NightShiftConfigError("implementation.scoped_locks cannot contain repository")
    return policy


def _parse_finalization_policy(value: object) -> FinalizationPolicy:
    data = _mapping(value, "finalization")
    _require_exact_keys(data, {"serialized", "scope", "phases"}, "finalization")
    policy = FinalizationPolicy(
        serialized=_boolean(data["serialized"], "finalization.serialized"),
        scope=_string(data["scope"], "finalization.scope"),
        phases=_unique_strings(data["phases"], "finalization.phases"),
    )
    if not policy.serialized:
        raise NightShiftConfigError("finalization.serialized must be true")
    if policy.scope != "repository":
        raise NightShiftConfigError("finalization.scope must be repository")
    if policy.phases != EXPECTED_FINALIZATION_PHASES:
        raise NightShiftConfigError(
            "finalization.phases must match the bounded delivery sequence exactly"
        )
    return policy


def _parse_codex_review_policy(value: object) -> CodexReviewPolicy:
    data = _mapping(value, "codex_review")
    _require_exact_keys(
        data,
        {
            "enabled",
            "automatic_lifecycle",
            "max_requests_per_pr",
            "max_self_reviews",
            "first_round_requires_exact_head_ci_green",
            "second_round_requires_blocking_p0_p1",
            "second_round_requires_changed_head",
            "duplicate_same_sha",
            "blocking_after_round_2",
        },
        "codex_review",
    )
    policy = CodexReviewPolicy(
        enabled=_boolean(data["enabled"], "codex_review.enabled"),
        automatic_lifecycle=_boolean(
            data["automatic_lifecycle"], "codex_review.automatic_lifecycle"
        ),
        max_requests_per_pr=_integer(
            data["max_requests_per_pr"], "codex_review.max_requests_per_pr", minimum=1
        ),
        max_self_reviews=_integer(
            data["max_self_reviews"], "codex_review.max_self_reviews", minimum=0
        ),
        first_round_requires_exact_head_ci_green=_boolean(
            data["first_round_requires_exact_head_ci_green"],
            "codex_review.first_round_requires_exact_head_ci_green",
        ),
        second_round_requires_blocking_p0_p1=_boolean(
            data["second_round_requires_blocking_p0_p1"],
            "codex_review.second_round_requires_blocking_p0_p1",
        ),
        second_round_requires_changed_head=_boolean(
            data["second_round_requires_changed_head"],
            "codex_review.second_round_requires_changed_head",
        ),
        duplicate_same_sha=_string(data["duplicate_same_sha"], "codex_review.duplicate_same_sha"),
        blocking_after_round_2=_string(
            data["blocking_after_round_2"], "codex_review.blocking_after_round_2"
        ),
    )
    if not policy.enabled:
        raise NightShiftConfigError("codex_review.enabled must be true")
    if policy.automatic_lifecycle:
        raise NightShiftConfigError("codex_review.automatic_lifecycle must be false")
    if policy.max_requests_per_pr != 2:
        raise NightShiftConfigError("codex_review.max_requests_per_pr must be 2")
    if policy.max_self_reviews != 1:
        raise NightShiftConfigError("codex_review.max_self_reviews must be 1")
    if not policy.first_round_requires_exact_head_ci_green:
        raise NightShiftConfigError(
            "codex_review.first_round_requires_exact_head_ci_green must be true"
        )
    if not policy.second_round_requires_blocking_p0_p1:
        raise NightShiftConfigError(
            "codex_review.second_round_requires_blocking_p0_p1 must be true"
        )
    if not policy.second_round_requires_changed_head:
        raise NightShiftConfigError("codex_review.second_round_requires_changed_head must be true")
    if policy.duplicate_same_sha != "reuse_existing":
        raise NightShiftConfigError("codex_review.duplicate_same_sha must be reuse_existing")
    if policy.blocking_after_round_2 != "human_required":
        raise NightShiftConfigError("codex_review.blocking_after_round_2 must be human_required")
    return policy


def _parse_risk_lanes(value: object) -> tuple[RiskPolicy, ...]:
    data = _mapping(value, "risk_lanes")
    if set(data) != {lane.value for lane in RiskLane}:
        raise NightShiftConfigError("risk_lanes must contain GREEN, YELLOW, and RED exactly")
    result: list[RiskPolicy] = []
    for lane in RiskLane:
        lane_data = _mapping(data[lane.value], f"risk_lanes.{lane.value}")
        _require_exact_keys(
            lane_data,
            {"auto_merge_allowed", "human_merge_required", "start_allowed"},
            f"risk_lanes.{lane.value}",
        )
        risk_policy = RiskPolicy(
            lane=lane,
            auto_merge_allowed=_boolean(
                lane_data["auto_merge_allowed"], f"risk_lanes.{lane.value}.auto_merge_allowed"
            ),
            human_merge_required=_boolean(
                lane_data["human_merge_required"],
                f"risk_lanes.{lane.value}.human_merge_required",
            ),
            start_allowed=_boolean(
                lane_data["start_allowed"], f"risk_lanes.{lane.value}.start_allowed"
            ),
        )
        expected = {
            RiskLane.GREEN: (True, False, True),
            RiskLane.YELLOW: (False, True, True),
            RiskLane.RED: (False, True, False),
        }[lane]
        actual = (
            risk_policy.auto_merge_allowed,
            risk_policy.human_merge_required,
            risk_policy.start_allowed,
        )
        if actual != expected:
            raise NightShiftConfigError(
                f"risk_lanes.{lane.value} violates its fixed gate semantics"
            )
        result.append(risk_policy)
    return tuple(result)


def _mapping(value: object, context: str) -> dict[str, object]:
    if not isinstance(value, Mapping):
        raise NightShiftConfigError(f"{context} must be a mapping")
    result: dict[str, object] = {}
    for key, item in value.items():
        if type(key) is not str:
            raise NightShiftConfigError(f"{context} keys must be strings")
        result[key] = item
    return result


def _require_exact_keys(data: Mapping[str, object], expected: set[str], context: str) -> None:
    actual = set(data)
    unknown = sorted(actual - expected)
    missing = sorted(expected - actual)
    if unknown or missing:
        details: list[str] = []
        if unknown:
            details.append(f"unknown={','.join(unknown)}")
        if missing:
            details.append(f"missing={','.join(missing)}")
        raise NightShiftConfigError(f"{context} keys invalid: {'; '.join(details)}")


def _string(value: object, context: str) -> str:
    if type(value) is not str or not value.strip() or value != value.strip():
        raise NightShiftConfigError(f"{context} must be a non-empty string")
    return value


def _boolean(value: object, context: str) -> bool:
    if type(value) is not bool:
        raise NightShiftConfigError(f"{context} must be a boolean")
    return value


def _integer(value: object, context: str, *, minimum: int) -> int:
    if type(value) is not int or value < minimum:
        raise NightShiftConfigError(f"{context} must be an integer >= {minimum}")
    return value


def _enum_value[EnumT: StrEnum](value: object, enum_type: type[EnumT], context: str) -> EnumT:
    if type(value) is not str:
        raise NightShiftConfigError(f"{context} must be a supported string value")
    try:
        return enum_type(value)
    except ValueError:
        raise NightShiftConfigError(f"{context} has unknown value: {value}") from None


def _enum_list[EnumT: StrEnum](
    value: object, enum_type: type[EnumT], context: str
) -> tuple[EnumT, ...]:
    values = _string_list(value, context)
    if len(set(values)) != len(values):
        raise NightShiftConfigError(f"{context} must not contain duplicates")
    return tuple(_enum_value(item, enum_type, context) for item in values)


def _string_list(value: object, context: str) -> tuple[str, ...]:
    if type(value) is not list:
        raise NightShiftConfigError(f"{context} must be a list")
    values = tuple(_string(item, context) for item in value)
    return values


def _unique_strings(value: object, context: str) -> tuple[str, ...]:
    values = _string_list(value, context)
    if len(set(values)) != len(values):
        raise NightShiftConfigError(f"{context} must not contain duplicates")
    return values


def _risk_policy(policy: NightShiftPolicy, lane: RiskLane) -> RiskPolicy:
    for item in policy.risk_lanes:
        if item.lane is lane:
            return item
    raise NightShiftConfigError(f"risk lane is not configured: {lane.value}")


def _check_is_success(value: CheckConclusion | str | None) -> bool:
    return value is CheckConclusion.SUCCESS or value == CheckConclusion.SUCCESS.value


def _required_check_failure(
    policy: NightShiftPolicy,
    *,
    current_head_sha: str,
    current_base_sha: str,
    check_evidence: Mapping[str, CheckRunEvidence],
) -> str | None:
    for gate_name in (*policy.required_checks, *policy.required_statuses):
        check = check_evidence.get(gate_name)
        if check is None or not _check_is_success(check.conclusion):
            return f"required_check_not_green:{gate_name}"
        if not _is_full_commit_sha(check.head_sha) or check.head_sha != current_head_sha:
            return f"required_check_not_bound_to_current_head:{gate_name}"
        if not _is_full_commit_sha(check.base_sha or "") or check.base_sha != current_base_sha:
            return f"required_check_not_bound_to_current_base:{gate_name}"
        if check.run_id <= 0:
            return f"required_check_run_id_missing:{gate_name}"
        if check.run_is_latest is not True:
            return f"required_check_run_not_latest:{gate_name}"
    return None


def _is_full_commit_sha(value: str) -> bool:
    return re.fullmatch(r"[0-9a-f]{40}", value) is not None


def _ready(reason: str) -> GateResult:
    return GateResult(GateStatus.READY, (reason,))


def _blocked(reason: str) -> GateResult:
    return GateResult(GateStatus.BLOCKED, (reason,))


def _human(reason: str) -> GateResult:
    return GateResult(GateStatus.HUMAN_REQUIRED, (reason,))


def _review_blocked(reason: str) -> ReviewGateResult:
    return ReviewGateResult(GateStatus.BLOCKED, None, (reason,))


def _review_human(reason: str) -> ReviewGateResult:
    return ReviewGateResult(GateStatus.HUMAN_REQUIRED, None, (reason,))


__all__ = [
    "EXPECTED_FINALIZATION_PHASES",
    "REQUIRED_MERGE_CHECKS",
    "REQUIRED_MERGE_STATUS_CONTEXTS",
    "SUPPORTED_POLICY_VERSION",
    "SUPPORTED_RED_GATES",
    "SUPPORTED_SCHEMA_VERSION",
    "SUPPORTED_SCOPED_LOCKS",
    "CheckConclusion",
    "CheckRunEvidence",
    "CleanupLifecycle",
    "CodexReviewConclusion",
    "CodexReviewOutcomeEvidence",
    "CodexReviewPolicy",
    "CodexReviewRequestEvidence",
    "FailureBudget",
    "FailureBudgetUsage",
    "FinalizationPolicy",
    "GateResult",
    "GateStatus",
    "ImplementationPolicy",
    "MergeGateEvidence",
    "NightShiftConfigError",
    "NightShiftPolicy",
    "PostTaskCleanupEvidence",
    "PostTaskCleanupPolicy",
    "ReviewGateAction",
    "ReviewGateResult",
    "RiskLane",
    "RiskPolicy",
    "TaskCandidate",
    "TaskSelectionSource",
    "TaskState",
    "WorktreeAccessEvidence",
    "WorktreeOperation",
    "evaluate_codex_review_outcome",
    "evaluate_codex_review_request",
    "evaluate_failure_budget",
    "evaluate_merge_gate",
    "evaluate_post_task_cleanup",
    "evaluate_task_start",
    "evaluate_worktree_access",
    "load_night_shift_policy",
    "select_next_task",
]
