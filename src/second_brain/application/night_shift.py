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


class NightShiftVerdict(StrEnum):
    """Exact reviewer verdict vocabulary."""

    FIX_REQUIRED = "NIGHT_SHIFT: FIX_REQUIRED"
    MERGE_READY = "NIGHT_SHIFT: MERGE_READY"
    HUMAN_REQUIRED = "NIGHT_SHIFT: HUMAN_REQUIRED"


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


class CheckConclusion(StrEnum):
    """CI conclusion values accepted by the merge gate."""

    SUCCESS = "success"
    PENDING = "pending"
    FAILURE = "failure"


SUPPORTED_POLICY_VERSION: Final[str] = "night-shift-v1"
SUPPORTED_SCHEMA_VERSION: Final[int] = 1
REQUIRED_MERGE_CHECKS: Final[frozenset[str]] = frozenset({"quality", "windows-ssl-regression"})
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
    max_review_fix_cycles_per_task: int
    max_ci_fix_cycles_per_task: int
    max_scope_expansion: int
    flaky_ci_retry_requires_no_code_change: bool


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
    verdicts: tuple[NightShiftVerdict, ...]
    red_gates: tuple[str, ...]
    failure_budget: FailureBudget
    dependency_satisfied_state: TaskState
    dependency_blocking_states: tuple[TaskState, ...]
    task_selection_order: tuple[TaskSelectionSource, ...]
    allow_roadmap_next_task: bool
    allow_create_next_issue: bool
    required_checks: tuple[str, ...]
    merge_method: str


@dataclass(frozen=True, slots=True)
class GateResult:
    """Deterministic gate outcome with machine-readable reasons."""

    status: GateStatus
    reasons: tuple[str, ...]


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
    review_fix_cycles: int = 0
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


@dataclass(frozen=True, slots=True)
class MergeGateEvidence:
    """Evidence required before a GREEN squash merge can be considered."""

    risk_lane: RiskLane
    review_verdict: NightShiftVerdict
    current_head_sha: str
    reviewed_head_sha: str
    current_base_ref: str
    reviewed_base_ref: str
    current_base_sha: str
    reviewed_base_sha: str
    check_evidence: Mapping[str, CheckRunEvidence]
    unresolved_review_threads: int
    accepted_blockers: int
    mergeable_clean: bool
    dependency_satisfied: bool
    human_gate: bool
    scope_unchanged: bool
    review_is_latest: bool | None = None


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
    if candidate.human_required or candidate.state is TaskState.HUMAN_REQUIRED:
        return _human("task_has_human_required_gate")
    if candidate.scope_expansions > policy.failure_budget.max_scope_expansion:
        return _human("scope_expansion_exceeds_budget")
    risk_policy = _risk_policy(policy, candidate.risk_lane)
    if not risk_policy.start_allowed:
        return _human("red_risk_lane")

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
    counters = (
        (usage.tasks_started, limits.max_tasks_per_night, "max_tasks_per_night"),
        (
            usage.review_fix_cycles,
            limits.max_review_fix_cycles_per_task,
            "max_review_fix_cycles_per_task",
        ),
        (usage.ci_fix_cycles, limits.max_ci_fix_cycles_per_task, "max_ci_fix_cycles_per_task"),
        (usage.scope_expansions, limits.max_scope_expansion, "max_scope_expansion"),
    )
    for used, maximum, name in counters:
        if used < 0:
            return _human(f"negative_failure_budget_counter:{name}")
        if used > maximum:
            return _human(f"failure_budget_exceeded:{name}")
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


def evaluate_merge_gate(policy: NightShiftPolicy, evidence: MergeGateEvidence) -> GateResult:
    """Return merge-ready only when every configured GREEN gate is true."""

    if evidence.risk_lane is not RiskLane.GREEN:
        return _human(f"risk_lane_{evidence.risk_lane.value.lower()}_cannot_auto_merge")
    if evidence.review_is_latest is not True:
        return _blocked("review_verdict_not_latest")
    if evidence.review_verdict is NightShiftVerdict.HUMAN_REQUIRED:
        return _human("review_verdict_is_human_required")
    if evidence.review_verdict is not NightShiftVerdict.MERGE_READY:
        return _blocked("review_verdict_is_not_merge_ready")
    if (
        evidence.current_base_ref != _EXPECTED_BASE_REF
        or evidence.reviewed_base_ref != _EXPECTED_BASE_REF
    ):
        return _blocked("merge_target_ref_is_not_main")
    if not _is_full_commit_sha(evidence.current_base_sha) or not _is_full_commit_sha(
        evidence.reviewed_base_sha
    ):
        return _blocked("reviewed_base_sha_is_not_full_commit_sha")
    if evidence.current_base_sha != evidence.reviewed_base_sha:
        return _blocked("reviewed_base_sha_is_not_current_base")
    if not _is_full_commit_sha(evidence.current_head_sha) or not _is_full_commit_sha(
        evidence.reviewed_head_sha
    ):
        return _blocked("reviewed_head_sha_is_not_full_commit_sha")
    if evidence.current_head_sha != evidence.reviewed_head_sha:
        return _blocked("reviewed_head_sha_is_not_current_head")
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
    if evidence.accepted_blockers:
        return _human("accepted_review_blocker_present")
    for check in policy.required_checks:
        check_evidence = evidence.check_evidence.get(check)
        if check_evidence is None or not _check_is_success(check_evidence.conclusion):
            return _blocked(f"required_check_not_green:{check}")
        if (
            not _is_full_commit_sha(check_evidence.head_sha)
            or check_evidence.head_sha != evidence.current_head_sha
        ):
            return _blocked(f"required_check_not_bound_to_current_head:{check}")
        if check_evidence.run_id <= 0:
            return _blocked(f"required_check_run_id_missing:{check}")
        if check_evidence.run_is_latest is not True:
            return _blocked(f"required_check_run_not_latest:{check}")
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
            "verdicts",
            "red_gates",
            "failure_budget",
            "dependency_policy",
            "task_selection",
            "merge_gate",
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
    verdicts = _enum_list(data["verdicts"], NightShiftVerdict, "verdicts")
    if set(verdicts) != set(NightShiftVerdict):
        raise NightShiftConfigError("verdicts must contain every supported verdict exactly once")
    red_gates = _unique_strings(data["red_gates"], "red_gates")
    if set(red_gates) != SUPPORTED_RED_GATES:
        raise NightShiftConfigError("red_gates must match the supported RED gate vocabulary")

    failure_data = _mapping(data["failure_budget"], "failure_budget")
    _require_exact_keys(
        failure_data,
        {
            "max_tasks_per_night",
            "max_review_fix_cycles_per_task",
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
        max_review_fix_cycles_per_task=_integer(
            failure_data["max_review_fix_cycles_per_task"],
            "failure_budget.max_review_fix_cycles_per_task",
            minimum=0,
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

    merge_data = _mapping(data["merge_gate"], "merge_gate")
    _require_exact_keys(merge_data, {"required_checks", "method"}, "merge_gate")
    required_checks = _unique_strings(merge_data["required_checks"], "merge_gate.required_checks")
    if not REQUIRED_MERGE_CHECKS.issubset(required_checks):
        raise NightShiftConfigError(
            "merge_gate.required_checks must include quality and windows-ssl-regression"
        )
    merge_method = _string(merge_data["method"], "merge_gate.method")
    if merge_method != "squash":
        raise NightShiftConfigError("merge_gate.method must be squash")

    return NightShiftPolicy(
        policy_version=policy_version,
        schema_version=schema_version,
        enabled_by_default=enabled_by_default,
        morning_cutoff_local=cutoff,
        timezone=timezone,
        task_states=task_states,
        risk_lanes=risk_lanes,
        verdicts=verdicts,
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
        merge_method=merge_method,
    )


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


def _is_full_commit_sha(value: str) -> bool:
    return re.fullmatch(r"[0-9a-f]{40}", value) is not None


def _ready(reason: str) -> GateResult:
    return GateResult(GateStatus.READY, (reason,))


def _blocked(reason: str) -> GateResult:
    return GateResult(GateStatus.BLOCKED, (reason,))


def _human(reason: str) -> GateResult:
    return GateResult(GateStatus.HUMAN_REQUIRED, (reason,))


__all__ = [
    "REQUIRED_MERGE_CHECKS",
    "SUPPORTED_POLICY_VERSION",
    "SUPPORTED_RED_GATES",
    "SUPPORTED_SCHEMA_VERSION",
    "CheckConclusion",
    "CheckRunEvidence",
    "FailureBudget",
    "FailureBudgetUsage",
    "GateResult",
    "GateStatus",
    "MergeGateEvidence",
    "NightShiftConfigError",
    "NightShiftPolicy",
    "NightShiftVerdict",
    "RiskLane",
    "RiskPolicy",
    "TaskCandidate",
    "TaskSelectionSource",
    "TaskState",
    "evaluate_failure_budget",
    "evaluate_merge_gate",
    "evaluate_task_start",
    "load_night_shift_policy",
    "select_next_task",
]
