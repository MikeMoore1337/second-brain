"""Deterministic validation for the bounded Night Shift repository protocol."""

from __future__ import annotations

from pathlib import Path

import pytest

from second_brain.application.night_shift import (
    CheckConclusion,
    CheckRunEvidence,
    CleanupLifecycle,
    CodexReviewConclusion,
    CodexReviewOutcomeEvidence,
    CodexReviewRequestEvidence,
    FailureBudgetUsage,
    GateStatus,
    MergeGateEvidence,
    NightShiftConfigError,
    NightShiftPolicy,
    PostTaskCleanupEvidence,
    ReviewGateAction,
    RiskLane,
    TaskCandidate,
    TaskSelectionSource,
    TaskState,
    WorktreeAccessEvidence,
    WorktreeOperation,
    evaluate_codex_review_outcome,
    evaluate_codex_review_request,
    evaluate_failure_budget,
    evaluate_merge_gate,
    evaluate_post_task_cleanup,
    evaluate_task_start,
    evaluate_worktree_access,
    load_night_shift_policy,
    select_next_task,
)

POLICY_PATH = Path("config/night-shift-v1.yaml")


def policy() -> NightShiftPolicy:
    return load_night_shift_policy(POLICY_PATH)


def test_repository_policy_is_disabled_by_default_and_has_bounded_contract() -> None:
    loaded = policy()

    assert loaded.enabled_by_default is False
    assert loaded.schema_version == 3
    assert loaded.morning_cutoff_local == "08:00"
    assert loaded.timezone == "Europe/Moscow"
    assert loaded.failure_budget.max_tasks_per_night == 4
    assert loaded.failure_budget.max_ci_fix_cycles_per_task == 3
    assert loaded.failure_budget.max_scope_expansion == 0
    assert loaded.failure_budget.flaky_ci_retry_requires_no_code_change is True
    assert loaded.required_checks == (
        "quality",
        "windows-ssl-regression",
        "frontend (ubuntu-latest)",
        "frontend (windows-latest)",
    )
    assert loaded.required_statuses == ()
    assert loaded.merge_method == "squash"
    assert not hasattr(loaded, "verdicts")
    assert loaded.allow_roadmap_next_task is False
    assert loaded.allow_create_next_issue is False
    assert loaded.post_task_cleanup.enabled_after_verified_green_merge is True
    assert loaded.implementation.parallel_independent_tasks is True
    assert loaded.implementation.repository_wide_exclusive_write is False
    assert loaded.implementation.diagnostics_require_write_lease is False
    assert loaded.finalization.serialized is True
    assert loaded.finalization.scope == "repository"
    assert loaded.codex_review.automatic_lifecycle is False
    assert loaded.codex_review.max_requests_per_pr == 2
    assert loaded.codex_review.max_self_reviews == 1
    assert loaded.post_task_cleanup.helper == "scripts/worktree_cleanup.py"
    assert loaded.post_task_cleanup.prune_after_successful_removals is True
    assert loaded.post_task_cleanup.allow_force is False
    assert loaded.post_task_cleanup.allow_remote_branch_delete is False
    assert loaded.post_task_cleanup.allow_local_branch_delete is False
    assert loaded.post_task_cleanup.failure_is_task_failure is False


@pytest.mark.parametrize(
    ("evidence", "expected"),
    (
        (
            PostTaskCleanupEvidence(
                lifecycle=CleanupLifecycle.VERIFIED_GREEN_MERGE,
                task_state=TaskState.MERGED.value,
                pr_merged=True,
                issue_completed=True,
                main_sha="a" * 40,
                worktree_registered=True,
            ),
            (GateStatus.READY, "verified_green_merge_cleanup_required"),
        ),
        (
            PostTaskCleanupEvidence(
                lifecycle=CleanupLifecycle.HISTORICAL_ORPHAN_PASS,
                task_state=TaskState.MERGED.value,
                pr_merged=True,
                issue_completed=True,
                main_sha="a" * 40,
                worktree_registered=True,
            ),
            (GateStatus.READY, "verified_historical_cleanup_candidate"),
        ),
        (
            PostTaskCleanupEvidence(
                lifecycle=CleanupLifecycle.VERIFIED_GREEN_MERGE,
                task_state=TaskState.HUMAN_REQUIRED.value,
                pr_merged=True,
                issue_completed=True,
                main_sha="a" * 40,
                worktree_registered=True,
            ),
            (GateStatus.BLOCKED, "task_is_not_merged"),
        ),
        (
            PostTaskCleanupEvidence(
                lifecycle=CleanupLifecycle.VERIFIED_GREEN_MERGE,
                task_state=TaskState.MERGED.value,
                pr_merged=False,
                issue_completed=True,
                main_sha="a" * 40,
                worktree_registered=True,
            ),
            (GateStatus.BLOCKED, "pr_is_not_merged"),
        ),
    ),
)
def test_post_task_cleanup_requires_verified_completion(
    evidence: PostTaskCleanupEvidence,
    expected: tuple[GateStatus, str],
) -> None:
    result = evaluate_post_task_cleanup(evidence)
    assert result.status is expected[0]
    assert result.reasons == (expected[1],)


def test_policy_rejects_unknown_status_and_risk_values(tmp_path: Path) -> None:
    source = POLICY_PATH.read_text(encoding="utf-8")

    unknown_status = tmp_path / "unknown-status.yaml"
    unknown_status.write_text(source.replace("  - queued\n", "  - queued\n  - purple\n", 1))
    with pytest.raises(NightShiftConfigError, match="task_states"):
        load_night_shift_policy(unknown_status)

    unknown_risk = tmp_path / "unknown-risk.yaml"
    unknown_risk.write_text(source.replace("  GREEN:\n", "  PURPLE:\n", 1))
    with pytest.raises(NightShiftConfigError, match="risk_lanes"):
        load_night_shift_policy(unknown_risk)


def test_policy_rejects_enabled_by_default(tmp_path: Path) -> None:
    source = POLICY_PATH.read_text(encoding="utf-8")
    path = tmp_path / "enabled.yaml"
    path.write_text(source.replace("enabled_by_default: false", "enabled_by_default: true", 1))

    with pytest.raises(NightShiftConfigError, match="disabled by default"):
        load_night_shift_policy(path)


def test_policy_rejects_repository_wide_implementation_lock(tmp_path: Path) -> None:
    source = POLICY_PATH.read_text(encoding="utf-8")
    path = tmp_path / "global-lock.yaml"
    path.write_text(
        source.replace(
            "repository_wide_exclusive_write: false",
            "repository_wide_exclusive_write: true",
            1,
        )
    )

    with pytest.raises(NightShiftConfigError, match="repository-wide"):
        load_night_shift_policy(path)


def test_night_mode_requires_explicit_activation() -> None:
    candidate = TaskCandidate(
        issue_number=79,
        state=TaskState.QUEUED,
        risk_lane=RiskLane.GREEN,
        source=TaskSelectionSource.OVERNIGHT_QUEUED,
    )

    result = evaluate_task_start(policy(), candidate, night_mode_enabled=False)

    assert result.status is GateStatus.HUMAN_REQUIRED
    assert result.reasons == ("night_mode_not_explicitly_enabled",)


def test_dependency_states_block_or_stop_dependent_task() -> None:
    candidate = TaskCandidate(
        issue_number=82,
        state=TaskState.QUEUED,
        risk_lane=RiskLane.YELLOW,
        source=TaskSelectionSource.OVERNIGHT_QUEUED,
        dependencies=(81,),
        dependency_states={81: TaskState.PR_OPEN},
    )

    blocked = evaluate_task_start(policy(), candidate, night_mode_enabled=True)
    assert blocked.status is GateStatus.BLOCKED

    human = evaluate_task_start(
        policy(),
        TaskCandidate(
            issue_number=candidate.issue_number,
            state=candidate.state,
            risk_lane=candidate.risk_lane,
            source=candidate.source,
            dependencies=candidate.dependencies,
            dependency_states={81: TaskState.HUMAN_REQUIRED},
        ),
        night_mode_enabled=True,
    )
    assert human.status is GateStatus.HUMAN_REQUIRED

    blocked = evaluate_task_start(
        policy(),
        TaskCandidate(
            issue_number=candidate.issue_number,
            state=candidate.state,
            risk_lane=candidate.risk_lane,
            source=candidate.source,
            dependencies=candidate.dependencies,
            dependency_states={81: TaskState.BLOCKED},
        ),
        night_mode_enabled=True,
    )
    assert blocked.status is GateStatus.HUMAN_REQUIRED


def test_selection_skips_blocked_independent_candidate_and_never_invents_roadmap() -> None:
    candidates = (
        TaskCandidate(
            issue_number=82,
            state=TaskState.QUEUED,
            risk_lane=RiskLane.YELLOW,
            source=TaskSelectionSource.OVERNIGHT_QUEUED,
            dependencies=(81,),
            dependency_states={81: TaskState.BLOCKED},
        ),
        TaskCandidate(
            issue_number=79,
            state=TaskState.QUEUED,
            risk_lane=RiskLane.GREEN,
            source=TaskSelectionSource.OVERNIGHT_QUEUED,
        ),
        TaskCandidate(
            issue_number=999,
            state=TaskState.QUEUED,
            risk_lane=RiskLane.GREEN,
            source=TaskSelectionSource.ROADMAP_NEXT,
        ),
    )

    selected, result = select_next_task(policy(), candidates, night_mode_enabled=True)

    assert selected is not None
    assert selected.issue_number == 79
    assert result.status is GateStatus.READY


def test_selection_preserves_human_dependency_gate_when_no_independent_task_is_ready() -> None:
    candidate = TaskCandidate(
        issue_number=82,
        state=TaskState.QUEUED,
        risk_lane=RiskLane.YELLOW,
        source=TaskSelectionSource.OVERNIGHT_QUEUED,
        dependencies=(81,),
        dependency_states={81: TaskState.HUMAN_REQUIRED},
    )

    selected, result = select_next_task(policy(), (candidate,), night_mode_enabled=True)

    assert selected is None
    assert result.status is GateStatus.HUMAN_REQUIRED


def test_task_human_required_state_is_deferred_then_returned_without_ready_work() -> None:
    human_task = TaskCandidate(
        issue_number=81,
        state=TaskState.HUMAN_REQUIRED,
        risk_lane=RiskLane.YELLOW,
        source=TaskSelectionSource.CURRENT_ACTIVE,
    )
    independent_task = TaskCandidate(
        issue_number=79,
        state=TaskState.QUEUED,
        risk_lane=RiskLane.GREEN,
        source=TaskSelectionSource.OVERNIGHT_QUEUED,
    )

    selected, ready = select_next_task(
        policy(), (human_task, independent_task), night_mode_enabled=True
    )
    assert selected == independent_task
    assert ready.status is GateStatus.READY

    selected, human = select_next_task(policy(), (human_task,), night_mode_enabled=True)
    assert selected is None
    assert human.status is GateStatus.HUMAN_REQUIRED
    assert human.reasons == ("task_has_human_required_gate",)


def test_selection_hard_stops_on_persisted_red_human_gate() -> None:
    red_human_task = TaskCandidate(
        issue_number=81,
        state=TaskState.HUMAN_REQUIRED,
        risk_lane=RiskLane.RED,
        source=TaskSelectionSource.CURRENT_ACTIVE,
        human_required=True,
    )
    independent_task = TaskCandidate(
        issue_number=79,
        state=TaskState.QUEUED,
        risk_lane=RiskLane.GREEN,
        source=TaskSelectionSource.OVERNIGHT_QUEUED,
    )

    selected, result = select_next_task(
        policy(), (red_human_task, independent_task), night_mode_enabled=True
    )

    assert selected is None
    assert result.status is GateStatus.HUMAN_REQUIRED
    assert result.reasons == ("red_risk_lane",)


def test_failure_budget_stops_ci_fix_loops_but_evidence_bound_flaky_retry_is_free() -> None:
    loaded = policy()

    within = evaluate_failure_budget(
        loaded,
        FailureBudgetUsage(
            flaky_ci_retries=4,
            flaky_ci_retry_has_evidence=True,
            flaky_ci_retry_code_changed=False,
        ),
    )
    assert within.status is GateStatus.READY

    exceeded = evaluate_failure_budget(
        loaded,
        FailureBudgetUsage(ci_fix_cycles=4),
    )
    assert exceeded.status is GateStatus.HUMAN_REQUIRED
    assert "max_ci_fix_cycles_per_task" in exceeded.reasons[0]

    no_evidence = evaluate_failure_budget(
        loaded,
        FailureBudgetUsage(flaky_ci_retries=1),
    )
    assert no_evidence.status is GateStatus.HUMAN_REQUIRED

    unverified_no_code_change = evaluate_failure_budget(
        loaded,
        FailureBudgetUsage(flaky_ci_retries=1, flaky_ci_retry_has_evidence=True),
    )
    assert unverified_no_code_change.status is GateStatus.HUMAN_REQUIRED
    assert unverified_no_code_change.reasons == ("flaky_ci_retry_no_code_change_not_verified",)

    code_changed = evaluate_failure_budget(
        loaded,
        FailureBudgetUsage(
            flaky_ci_retries=1,
            flaky_ci_retry_has_evidence=True,
            flaky_ci_retry_code_changed=True,
        ),
    )
    assert code_changed.status is GateStatus.HUMAN_REQUIRED


@pytest.mark.parametrize(
    ("usage", "expected_status", "expected_reason"),
    (
        (
            FailureBudgetUsage(tasks_started=3),
            GateStatus.READY,
            "failure_budget_within_bounds",
        ),
        (
            FailureBudgetUsage(tasks_started=4),
            GateStatus.HUMAN_REQUIRED,
            "failure_budget_exceeded:max_tasks_per_night",
        ),
        (
            FailureBudgetUsage(ci_fix_cycles=2),
            GateStatus.READY,
            "failure_budget_within_bounds",
        ),
        (
            FailureBudgetUsage(ci_fix_cycles=3),
            GateStatus.HUMAN_REQUIRED,
            "failure_budget_exceeded:max_ci_fix_cycles_per_task",
        ),
    ),
)
def test_failure_budget_requires_remaining_operation_capacity(
    usage: FailureBudgetUsage,
    expected_status: GateStatus,
    expected_reason: str,
) -> None:
    result = evaluate_failure_budget(policy(), usage)

    assert result.status is expected_status
    assert result.reasons == (expected_reason,)


def _green_merge_evidence(
    *,
    risk_lane: RiskLane = RiskLane.GREEN,
    current_head_sha: str = "a" * 40,
    current_base_ref: str = "main",
    current_base_sha: str = "c" * 40,
    check_heads: dict[str, str] | None = None,
    check_evidence: dict[str, CheckRunEvidence] | None = None,
    unresolved_review_threads: int = 0,
    unresolved_blockers: int = 0,
    mergeable_clean: bool = True,
    dependency_satisfied: bool = True,
    human_gate: bool = False,
    scope_unchanged: bool = True,
) -> MergeGateEvidence:
    loaded = policy()
    default_check_heads = check_heads or {
        gate_name: current_head_sha
        for gate_name in (*loaded.required_checks, *loaded.required_statuses)
    }
    return MergeGateEvidence(
        risk_lane=risk_lane,
        current_head_sha=current_head_sha,
        current_base_ref=current_base_ref,
        current_base_sha=current_base_sha,
        check_evidence=check_evidence
        or {
            check: CheckRunEvidence(
                conclusion=CheckConclusion.SUCCESS,
                head_sha=head_sha,
                run_id=index,
                run_is_latest=True,
                base_sha=current_base_sha,
            )
            for index, (check, head_sha) in enumerate(default_check_heads.items(), start=1)
        },
        unresolved_review_threads=unresolved_review_threads,
        unresolved_blockers=unresolved_blockers,
        mergeable_clean=mergeable_clean,
        dependency_satisfied=dependency_satisfied,
        human_gate=human_gate,
        scope_unchanged=scope_unchanged,
        codex_review=CodexReviewOutcomeEvidence(
            review_round=1,
            head_sha=current_head_sha,
            conclusion=CodexReviewConclusion.CLEAN,
            managed_review_count=1,
        ),
    )


def test_merge_gate_requires_exact_current_head_and_all_required_gates() -> None:
    loaded = policy()
    ready = evaluate_merge_gate(loaded, _green_merge_evidence())
    assert ready.status is GateStatus.MERGE_READY

    missing_aggregate_status = evaluate_merge_gate(
        loaded,
        _green_merge_evidence(
            check_heads={
                "quality": "a" * 40,
                "windows-ssl-regression": "a" * 40,
                "frontend (ubuntu-latest)": "a" * 40,
            }
        ),
    )
    assert missing_aggregate_status.status is GateStatus.BLOCKED
    assert missing_aggregate_status.reasons == (
        "required_check_not_green:frontend (windows-latest)",
    )

    abbreviated_head = evaluate_merge_gate(
        loaded,
        _green_merge_evidence(current_head_sha="a" * 7),
    )
    assert abbreviated_head.status is GateStatus.BLOCKED
    assert abbreviated_head.reasons == ("current_head_sha_is_not_full_commit_sha",)

    stale_check = evaluate_merge_gate(
        loaded,
        _green_merge_evidence(
            check_heads={"quality": "b" * 40, "windows-ssl-regression": "a" * 40}
        ),
    )
    assert stale_check.status is GateStatus.BLOCKED
    assert stale_check.reasons == ("required_check_not_bound_to_current_head:quality",)

    pending_ci = evaluate_merge_gate(
        loaded,
        _green_merge_evidence(
            check_evidence={
                "quality": CheckRunEvidence(
                    conclusion=CheckConclusion.PENDING,
                    head_sha="a" * 40,
                    run_id=1,
                    run_is_latest=True,
                    base_sha="c" * 40,
                ),
                "windows-ssl-regression": CheckRunEvidence(
                    conclusion=CheckConclusion.SUCCESS,
                    head_sha="a" * 40,
                    run_id=2,
                    run_is_latest=True,
                    base_sha="c" * 40,
                ),
            }
        ),
    )
    assert pending_ci.status is GateStatus.BLOCKED

    superseded_check = evaluate_merge_gate(
        loaded,
        _green_merge_evidence(
            check_evidence={
                "quality": CheckRunEvidence(
                    conclusion=CheckConclusion.SUCCESS,
                    head_sha="a" * 40,
                    run_id=1,
                    run_is_latest=False,
                    base_sha="c" * 40,
                ),
                "windows-ssl-regression": CheckRunEvidence(
                    conclusion=CheckConclusion.SUCCESS,
                    head_sha="a" * 40,
                    run_id=2,
                    run_is_latest=True,
                    base_sha="c" * 40,
                ),
            }
        ),
    )
    assert superseded_check.status is GateStatus.BLOCKED
    assert superseded_check.reasons == ("required_check_run_not_latest:quality",)

    stale_check_base = evaluate_merge_gate(
        loaded,
        _green_merge_evidence(
            check_evidence={
                "quality": CheckRunEvidence(
                    conclusion=CheckConclusion.SUCCESS,
                    head_sha="a" * 40,
                    run_id=1,
                    run_is_latest=True,
                    base_sha="d" * 40,
                ),
                "windows-ssl-regression": CheckRunEvidence(
                    conclusion=CheckConclusion.SUCCESS,
                    head_sha="a" * 40,
                    run_id=2,
                    run_is_latest=True,
                    base_sha="c" * 40,
                ),
            }
        ),
    )
    assert stale_check_base.status is GateStatus.BLOCKED
    assert stale_check_base.reasons == ("required_check_not_bound_to_current_base:quality",)


def test_merge_gate_requires_existing_github_threads_to_be_resolved() -> None:
    result = evaluate_merge_gate(
        policy(),
        _green_merge_evidence(unresolved_review_threads=1),
    )

    assert result.status is GateStatus.BLOCKED
    assert result.reasons == ("unresolved_review_threads_present",)


def test_merge_gate_requires_no_unresolved_blockers_from_implementation_or_qa() -> None:
    result = evaluate_merge_gate(
        policy(),
        _green_merge_evidence(unresolved_blockers=1),
    )

    assert result.status is GateStatus.HUMAN_REQUIRED
    assert result.reasons == ("unresolved_blockers_present",)


def test_merge_gate_requires_full_current_base_sha() -> None:
    result = evaluate_merge_gate(
        policy(),
        _green_merge_evidence(current_base_sha="d" * 7),
    )

    assert result.status is GateStatus.BLOCKED
    assert result.reasons == ("current_base_sha_is_not_full_commit_sha",)


def test_current_merge_ready_task_remains_before_later_candidates() -> None:
    loaded = policy()
    candidate = TaskCandidate(
        issue_number=79,
        state=TaskState.MERGE_READY,
        risk_lane=RiskLane.GREEN,
        source=TaskSelectionSource.CURRENT_ACTIVE,
    )

    selected, result = select_next_task(loaded, (candidate,), night_mode_enabled=True)

    assert selected == candidate
    assert result.status is GateStatus.READY


def test_yellow_is_implementable_but_never_auto_mergeable() -> None:
    loaded = policy()
    candidate = TaskCandidate(
        issue_number=82,
        state=TaskState.QUEUED,
        risk_lane=RiskLane.YELLOW,
        source=TaskSelectionSource.OVERNIGHT_QUEUED,
    )
    start = evaluate_task_start(loaded, candidate, night_mode_enabled=True)
    assert start.status is GateStatus.READY

    merge = evaluate_merge_gate(
        loaded,
        _green_merge_evidence(risk_lane=RiskLane.YELLOW),
    )
    assert merge.status is GateStatus.HUMAN_REQUIRED


def _review_checks(
    *,
    head_sha: str = "a" * 40,
    base_sha: str = "c" * 40,
    conclusion: CheckConclusion = CheckConclusion.SUCCESS,
) -> dict[str, CheckRunEvidence]:
    return {
        name: CheckRunEvidence(
            conclusion=conclusion,
            head_sha=head_sha,
            run_id=index,
            run_is_latest=True,
            base_sha=base_sha,
        )
        for index, name in enumerate(
            (*policy().required_checks, *policy().required_statuses), start=1
        )
    }


def test_parallel_implementation_uses_task_worktree_ownership_only() -> None:
    loaded = policy()

    task_b = evaluate_worktree_access(
        loaded,
        WorktreeAccessEvidence(
            operation=WorktreeOperation.IMPLEMENTATION,
            task_id="task-b",
            worktree_id="worktree-b",
            repository_has_other_implementation=True,
        ),
    )
    assert task_b.status is GateStatus.READY
    assert task_b.reasons == ("parallel_implementation_allowed",)

    same_worktree = evaluate_worktree_access(
        loaded,
        WorktreeAccessEvidence(
            operation=WorktreeOperation.IMPLEMENTATION,
            task_id="task-b",
            worktree_id="worktree-a",
            worktree_owner_task_id="task-a",
        ),
    )
    assert same_worktree.status is GateStatus.BLOCKED
    assert same_worktree.reasons == ("worktree_owned_by_another_task",)

    implementation_during_finalization = evaluate_worktree_access(
        loaded,
        WorktreeAccessEvidence(
            operation=WorktreeOperation.IMPLEMENTATION,
            task_id="task-b",
            worktree_id="worktree-b",
            finalization_owner_task_id="task-a",
        ),
    )
    assert implementation_during_finalization.status is GateStatus.READY

    competing_finalization = evaluate_worktree_access(
        loaded,
        WorktreeAccessEvidence(
            operation=WorktreeOperation.FINALIZATION,
            task_id="task-b",
            worktree_id="worktree-b",
            finalization_owner_task_id="task-a",
        ),
    )
    assert competing_finalization.status is GateStatus.BLOCKED
    assert competing_finalization.reasons == ("finalization_owned_by_another_task",)


def test_stale_scoped_owner_can_be_reclaimed_without_global_unlock() -> None:
    result = evaluate_worktree_access(
        policy(),
        WorktreeAccessEvidence(
            operation=WorktreeOperation.IMPLEMENTATION,
            task_id="task-b",
            worktree_id="worktree-a",
            worktree_owner_task_id="crashed-task-a",
            stale_owner_confirmed=True,
        ),
    )

    assert result.status is GateStatus.READY
    assert result.reasons == ("stale_task_worktree_lease_reclaimable",)

    read_only = evaluate_worktree_access(
        policy(),
        WorktreeAccessEvidence(
            operation=WorktreeOperation.READ_ONLY,
            task_id="diagnostic",
            worktree_id="worktree-a",
            worktree_owner_task_id="task-a",
        ),
    )
    assert read_only.status is GateStatus.READY
    assert read_only.reasons == ("read_only_does_not_require_implementation_lease",)


def test_codex_review_requires_pr_and_green_exact_head_ci() -> None:
    head = "a" * 40
    base = "c" * 40
    pending = evaluate_codex_review_request(
        policy(),
        CodexReviewRequestEvidence(
            pr_exists=True,
            pr_is_draft=False,
            current_head_sha=head,
            current_base_sha=base,
            check_evidence=_review_checks(conclusion=CheckConclusion.PENDING),
        ),
    )
    assert pending.status is GateStatus.BLOCKED
    assert pending.reasons == ("required_check_not_green:quality",)

    missing_pr = evaluate_codex_review_request(
        policy(),
        CodexReviewRequestEvidence(
            pr_exists=False,
            pr_is_draft=False,
            current_head_sha=head,
            current_base_sha=base,
            check_evidence=_review_checks(),
        ),
    )
    assert missing_pr.reasons == ("pull_request_missing_before_review",)

    first = evaluate_codex_review_request(
        policy(),
        CodexReviewRequestEvidence(
            pr_exists=True,
            pr_is_draft=False,
            current_head_sha=head,
            current_base_sha=base,
            check_evidence=_review_checks(),
        ),
    )
    assert first.status is GateStatus.READY
    assert first.action is ReviewGateAction.REQUEST
    assert first.reasons == ("request_codex_review_round_1",)


def test_codex_review_reuses_same_sha_and_never_duplicates_budget() -> None:
    head = "a" * 40
    base = "c" * 40
    reused = evaluate_codex_review_request(
        policy(),
        CodexReviewRequestEvidence(
            pr_exists=True,
            pr_is_draft=False,
            current_head_sha=head,
            current_base_sha=base,
            check_evidence={},
            managed_review_count=1,
            existing_review_head_sha=head,
            existing_review_pending_or_completed=True,
        ),
    )
    assert reused.status is GateStatus.READY
    assert reused.action is ReviewGateAction.REUSE
    assert reused.reasons == ("reuse_existing_codex_review_for_current_head",)

    round_two = evaluate_codex_review_request(
        policy(),
        CodexReviewRequestEvidence(
            pr_exists=True,
            pr_is_draft=False,
            current_head_sha="b" * 40,
            current_base_sha=base,
            check_evidence=_review_checks(head_sha="b" * 40),
            managed_review_count=1,
            prior_review_blocking_p0_p1=True,
            prior_review_head_sha=head,
        ),
    )
    assert round_two.status is GateStatus.READY
    assert round_two.action is ReviewGateAction.REQUEST
    assert round_two.reasons == ("request_codex_review_round_2",)

    exhausted = evaluate_codex_review_request(
        policy(),
        CodexReviewRequestEvidence(
            pr_exists=True,
            pr_is_draft=False,
            current_head_sha="d" * 40,
            current_base_sha=base,
            check_evidence=_review_checks(head_sha="d" * 40),
            managed_review_count=2,
            prior_review_blocking_p0_p1=True,
            prior_review_head_sha="b" * 40,
        ),
    )
    assert exhausted.status is GateStatus.HUMAN_REQUIRED
    assert exhausted.reasons == ("codex_review_budget_exhausted",)


def test_codex_review_clean_first_round_merges_and_second_blocker_is_human() -> None:
    head = "a" * 40
    clean = evaluate_codex_review_outcome(
        policy(),
        CodexReviewOutcomeEvidence(
            review_round=1,
            head_sha=head,
            conclusion=CodexReviewConclusion.CLEAN,
            managed_review_count=1,
        ),
    )
    assert clean.status is GateStatus.MERGE_READY
    assert clean.action is ReviewGateAction.MERGE

    first_blocker = evaluate_codex_review_outcome(
        policy(),
        CodexReviewOutcomeEvidence(
            review_round=1,
            head_sha=head,
            conclusion=CodexReviewConclusion.BLOCKING,
            managed_review_count=1,
        ),
    )
    assert first_blocker.status is GateStatus.READY
    assert first_blocker.action is ReviewGateAction.REQUEST

    second_blocker = evaluate_codex_review_outcome(
        policy(),
        CodexReviewOutcomeEvidence(
            review_round=2,
            head_sha="b" * 40,
            conclusion=CodexReviewConclusion.BLOCKING,
            managed_review_count=2,
        ),
    )
    assert second_blocker.status is GateStatus.HUMAN_REQUIRED
    assert second_blocker.reasons == ("blocking_review_after_round_2_requires_human",)


def test_merge_gate_rejects_missing_or_stale_codex_review() -> None:
    ready = _green_merge_evidence()
    missing = evaluate_merge_gate(
        policy(),
        MergeGateEvidence(
            risk_lane=ready.risk_lane,
            current_head_sha=ready.current_head_sha,
            current_base_ref=ready.current_base_ref,
            current_base_sha=ready.current_base_sha,
            check_evidence=ready.check_evidence,
            unresolved_review_threads=ready.unresolved_review_threads,
            unresolved_blockers=ready.unresolved_blockers,
            mergeable_clean=ready.mergeable_clean,
            dependency_satisfied=ready.dependency_satisfied,
            human_gate=ready.human_gate,
            scope_unchanged=ready.scope_unchanged,
        ),
    )
    assert missing.status is GateStatus.BLOCKED
    assert missing.reasons == ("codex_review_not_completed",)

    blocking = evaluate_merge_gate(
        policy(),
        MergeGateEvidence(
            risk_lane=ready.risk_lane,
            current_head_sha=ready.current_head_sha,
            current_base_ref=ready.current_base_ref,
            current_base_sha=ready.current_base_sha,
            check_evidence=ready.check_evidence,
            unresolved_review_threads=ready.unresolved_review_threads,
            unresolved_blockers=ready.unresolved_blockers,
            mergeable_clean=ready.mergeable_clean,
            dependency_satisfied=ready.dependency_satisfied,
            human_gate=ready.human_gate,
            scope_unchanged=ready.scope_unchanged,
            codex_review=CodexReviewOutcomeEvidence(
                review_round=2,
                head_sha=ready.current_head_sha,
                conclusion=CodexReviewConclusion.BLOCKING,
                managed_review_count=2,
            ),
        ),
    )
    assert blocking.status is GateStatus.HUMAN_REQUIRED
    assert blocking.reasons == ("blocking_review_after_round_2_requires_human",)
