"""Deterministic validation for the bounded Night Shift repository protocol."""

from __future__ import annotations

from pathlib import Path

import pytest

from second_brain.application.night_shift import (
    CheckConclusion,
    CheckRunEvidence,
    FailureBudgetUsage,
    GateStatus,
    MergeGateEvidence,
    NightShiftConfigError,
    NightShiftPolicy,
    NightShiftVerdict,
    RiskLane,
    TaskCandidate,
    TaskSelectionSource,
    TaskState,
    evaluate_failure_budget,
    evaluate_merge_gate,
    evaluate_task_start,
    load_night_shift_policy,
    select_next_task,
)

POLICY_PATH = Path("config/night-shift-v1.yaml")


def policy() -> NightShiftPolicy:
    return load_night_shift_policy(POLICY_PATH)


def test_repository_policy_is_disabled_by_default_and_has_bounded_contract() -> None:
    loaded = policy()

    assert loaded.enabled_by_default is False
    assert loaded.morning_cutoff_local == "08:00"
    assert loaded.timezone == "Europe/Moscow"
    assert loaded.failure_budget.max_tasks_per_night == 4
    assert loaded.failure_budget.max_review_fix_cycles_per_task == 3
    assert loaded.failure_budget.max_ci_fix_cycles_per_task == 3
    assert loaded.failure_budget.max_scope_expansion == 0
    assert loaded.failure_budget.flaky_ci_retry_requires_no_code_change is True
    assert loaded.required_checks == ("quality", "windows-ssl-regression")
    assert loaded.merge_method == "squash"
    assert loaded.allow_roadmap_next_task is False
    assert loaded.allow_create_next_issue is False


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


def test_failure_budget_stops_review_loops_but_evidence_bound_flaky_retry_is_free() -> None:
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
        FailureBudgetUsage(review_fix_cycles=4),
    )
    assert exceeded.status is GateStatus.HUMAN_REQUIRED
    assert "max_review_fix_cycles_per_task" in exceeded.reasons[0]

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


def _green_merge_evidence(
    *,
    risk_lane: RiskLane = RiskLane.GREEN,
    review_verdict: NightShiftVerdict = NightShiftVerdict.MERGE_READY,
    review_is_latest: bool | None = True,
    current_head_sha: str = "a" * 40,
    reviewed_head_sha: str | None = None,
    current_base_ref: str = "main",
    reviewed_base_ref: str | None = None,
    current_base_sha: str = "c" * 40,
    reviewed_base_sha: str | None = None,
    check_heads: dict[str, str] | None = None,
    check_evidence: dict[str, CheckRunEvidence] | None = None,
    unresolved_review_threads: int = 0,
    accepted_blockers: int = 0,
    mergeable_clean: bool = True,
    dependency_satisfied: bool = True,
    human_gate: bool = False,
    scope_unchanged: bool = True,
) -> MergeGateEvidence:
    return MergeGateEvidence(
        risk_lane=risk_lane,
        review_verdict=review_verdict,
        review_is_latest=review_is_latest,
        current_head_sha=current_head_sha,
        reviewed_head_sha=reviewed_head_sha or current_head_sha,
        current_base_ref=current_base_ref,
        reviewed_base_ref=reviewed_base_ref or current_base_ref,
        current_base_sha=current_base_sha,
        reviewed_base_sha=reviewed_base_sha or current_base_sha,
        check_evidence=check_evidence
        or {
            check: CheckRunEvidence(
                conclusion=CheckConclusion.SUCCESS,
                head_sha=head_sha,
                run_id=index,
                run_is_latest=True,
                base_sha=current_base_sha,
            )
            for index, (check, head_sha) in enumerate(
                (
                    check_heads
                    or {
                        "quality": current_head_sha,
                        "windows-ssl-regression": current_head_sha,
                    }
                ).items(),
                start=1,
            )
        },
        unresolved_review_threads=unresolved_review_threads,
        accepted_blockers=accepted_blockers,
        mergeable_clean=mergeable_clean,
        dependency_satisfied=dependency_satisfied,
        human_gate=human_gate,
        scope_unchanged=scope_unchanged,
    )


def test_merge_gate_requires_exact_reviewed_head_and_all_required_checks() -> None:
    loaded = policy()
    ready = evaluate_merge_gate(loaded, _green_merge_evidence())
    assert ready.status is GateStatus.MERGE_READY

    stale_review = evaluate_merge_gate(
        loaded,
        _green_merge_evidence(reviewed_head_sha="b" * 40),
    )
    assert stale_review.status is GateStatus.BLOCKED

    abbreviated_review = evaluate_merge_gate(
        loaded,
        _green_merge_evidence(current_head_sha="a" * 7, reviewed_head_sha="a" * 7),
    )
    assert abbreviated_review.status is GateStatus.BLOCKED
    assert abbreviated_review.reasons == ("reviewed_head_sha_is_not_full_commit_sha",)

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


def test_merge_gate_preserves_human_required_reviewer_verdict() -> None:
    result = evaluate_merge_gate(
        policy(),
        _green_merge_evidence(review_verdict=NightShiftVerdict.HUMAN_REQUIRED),
    )

    assert result.status is GateStatus.HUMAN_REQUIRED
    assert result.reasons == ("review_verdict_is_human_required",)


def test_merge_gate_rejects_superseded_review_verdict() -> None:
    result = evaluate_merge_gate(
        policy(),
        _green_merge_evidence(review_is_latest=False),
    )

    assert result.status is GateStatus.BLOCKED
    assert result.reasons == ("review_verdict_not_latest",)


def test_merge_gate_rejects_stale_base_evidence() -> None:
    result = evaluate_merge_gate(
        policy(),
        _green_merge_evidence(reviewed_base_sha="d" * 40),
    )

    assert result.status is GateStatus.BLOCKED
    assert result.reasons == ("reviewed_base_sha_is_not_current_base",)


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
