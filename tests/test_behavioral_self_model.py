"""Focused deterministic tests for the Stage 10B Behavioral Self Model."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import cast
from uuid import UUID

import pytest

from second_brain.adapters.vault import FileSystemVaultReader
from second_brain.application.behavioral_observation import (
    BehavioralOutcomePresenceV1,
    BehavioralTemporalWindowV1,
)
from second_brain.application.behavioral_self_model import (
    DEFAULT_BEHAVIORAL_SELF_MODEL_POLICY,
    MAX_BEHAVIORAL_PATTERNS,
    MAX_BEHAVIORAL_PROVENANCE_UUIDS,
    POLICY_FINGERPRINT,
    BehavioralCaveatCodeV1,
    BehavioralChoiceSupportV1,
    BehavioralPatternStateV1,
    BehavioralPatternTypeV1,
    BehavioralPatternV1,
    BehavioralProvenanceV1,
    BehavioralRatioV1,
    BehavioralSelfModelErrorCode,
    BehavioralSelfModelInvalidJournalError,
    BehavioralSelfModelInvalidRequestError,
    BehavioralSelfModelPolicyMismatchError,
    BehavioralSelfModelRequest,
    BehavioralSelfModelResultTooLargeError,
    BehavioralSelfModelResultV1,
    BehavioralSelfModelSourceUnavailableError,
    BuildBehavioralSelfModel,
    behavioral_provenance_fingerprint,
    validate_behavioral_self_model_result,
)
from second_brain.application.decision_journal import (
    render_decision_journal_body,
    render_outcome_observation_body,
)
from second_brain.application.personal_memory import PERSONAL_MEMORY_MARKER
from second_brain.application.reports import VaultSnapshot
from tests.conftest import create_vault, snapshot_tree, write_note

GENERATED_AT = datetime(2026, 9, 13, 12, 0, tzinfo=UTC)


def _id(index: int) -> UUID:
    return UUID(f"0198f4c5-6a00-7000-8000-0000000000{index:02d}")


def _journal_body(
    *,
    situation: str = "Choose a direction",
    options: tuple[str, ...] = ("Alpha", "Beta"),
    information: str = "Known constraints",
    criteria: tuple[str, ...] = ("Speed",),
    chosen: str = "Alpha",
) -> str:
    return render_decision_journal_body(
        situation=situation,
        available_options=options,
        information_known_at_decision_time=information,
        criteria=criteria,
        chosen_option=chosen,
        reasons="The reviewed context supports the selected option.",
        confidence="Medium.",
        expected_result="A bounded result.",
    )


def _journal_note(
    note_id: UUID,
    *,
    evidence_at: datetime | str = GENERATED_AT - timedelta(hours=1),
    evidence_at_precision: str = "exact",
    domain: str | None = "work",
    body: str | None = None,
    marker: int = 1,
) -> str:
    rendered_time = evidence_at.isoformat() if isinstance(evidence_at, datetime) else evidence_at
    fields = [
        "---",
        f"id: {note_id}",
        "type: zettel",
        "created: 2026-09-13T11:30:00+00:00",
        f"{PERSONAL_MEMORY_MARKER}: {marker}",
        "evidence_kind: observed_decision",
        "self_kind: decision",
        f"evidence_at: {rendered_time}",
        f"evidence_at_precision: {evidence_at_precision}",
    ]
    if domain is not None:
        fields.append(f"domain: {domain}")
    fields.extend(("tags: [behavioral]", "links: []", "---"))
    return "\n".join(fields) + "\n" + (body or _journal_body())


def _outcome_note(
    outcome_id: UUID, decision_id: UUID, *, actual: str = "Later observation."
) -> str:
    fields = [
        "---",
        f"id: {outcome_id}",
        "type: zettel",
        "created: 2026-09-13T11:30:00+00:00",
        f"{PERSONAL_MEMORY_MARKER}: 1",
        "evidence_kind: outcome_later_observation",
        "self_kind: outcome",
        "evidence_at: 2026-09-13T11:30:00+00:00",
        "evidence_at_precision: exact",
        f"decision_id: {decision_id}",
        "domain: work",
        "tags: [behavioral]",
        "links: []",
        "---",
    ]
    body = render_outcome_observation_body(actual_result=actual, reassessment="", notes="")
    return "\n".join(fields) + "\n" + body


def _build(vault: Path) -> BehavioralSelfModelResultV1:
    return BuildBehavioralSelfModel(
        FileSystemVaultReader(vault),
        clock=lambda: GENERATED_AT,
    ).execute()


def _write_decisions(
    vault: Path,
    values: tuple[tuple[int, datetime | str, str], ...],
    *,
    body: str | None = None,
) -> None:
    for index, evidence_at, chosen in values:
        write_note(
            vault,
            f"10 Projects/Decision-{index}.md",
            _journal_note(
                _id(index),
                evidence_at=evidence_at,
                body=body or _journal_body(chosen=chosen),
            ),
        )


def _single_pattern(result: BehavioralSelfModelResultV1) -> BehavioralPatternV1:
    assert len(result.patterns) == 1
    return result.patterns[0]


def _support(pattern: BehavioralPatternV1, index: int) -> BehavioralChoiceSupportV1:
    return next(item for item in pattern.choice_support if item.option.option_index == index)


def test_three_current_unanimous_builds_repeated_current_with_exact_ratio_and_provenance(
    tmp_path: Path,
) -> None:
    vault = create_vault(tmp_path / "vault")
    _write_decisions(
        vault,
        (
            (1, GENERATED_AT, "Alpha"),
            (2, GENERATED_AT - timedelta(days=1), "Alpha"),
            (3, GENERATED_AT - timedelta(days=2), "Alpha"),
        ),
    )

    result = _build(vault)
    pattern = _single_pattern(result)

    assert pattern.pattern_type is BehavioralPatternTypeV1.REPEATED_EXACT_CHOICE
    assert pattern.state is BehavioralPatternStateV1.CURRENT
    assert pattern.selected_option is not None
    assert pattern.selected_option.option_index == 0
    assert pattern.support_count == 3
    assert pattern.support_ratio == BehavioralRatioV1(3, 3)
    assert pattern.total_comparable_observations == 3
    assert pattern.windows[0].window is BehavioralTemporalWindowV1.HISTORICAL
    assert pattern.windows[0].observation_count == 0
    assert pattern.windows[1].window is BehavioralTemporalWindowV1.CURRENT
    assert pattern.windows[1].observation_count == 3
    assert _support(pattern, 0).support_ratio == BehavioralRatioV1(3, 3)
    assert pattern.outcome_presence == {"present": 0, "absent": 3}
    assert pattern.provenance.source_journal_uuids == (_id(1), _id(2), _id(3))
    assert pattern.temporal_span.earliest_evidence_at == GENERATED_AT - timedelta(days=2)
    assert pattern.temporal_span.latest_evidence_at == GENERATED_AT
    assert "support_is_descriptive" in {
        cast(BehavioralCaveatCodeV1, item).value for item in pattern.caveats
    }


def test_three_historical_unanimous_builds_repeated_historical(tmp_path: Path) -> None:
    vault = create_vault(tmp_path / "vault")
    historical = GENERATED_AT - timedelta(days=90, seconds=1)
    _write_decisions(
        vault,
        (
            (1, historical, "Alpha"),
            (2, historical - timedelta(days=1), "Alpha"),
            (3, historical - timedelta(days=2), "Alpha"),
        ),
    )

    pattern = _single_pattern(_build(vault))

    assert pattern.pattern_type is BehavioralPatternTypeV1.REPEATED_EXACT_CHOICE
    assert pattern.state is BehavioralPatternStateV1.HISTORICAL
    assert pattern.windows[0].observation_count == 3
    assert pattern.windows[1].observation_count == 0


def test_same_choice_in_both_windows_builds_stable_pattern(tmp_path: Path) -> None:
    vault = create_vault(tmp_path / "vault")
    _write_decisions(
        vault,
        (
            (1, GENERATED_AT - timedelta(days=180), "Alpha"),
            (2, GENERATED_AT - timedelta(days=91), "Alpha"),
            (3, GENERATED_AT - timedelta(days=90), "Alpha"),
        ),
    )

    pattern = _single_pattern(_build(vault))

    assert pattern.pattern_type is BehavioralPatternTypeV1.STABLE_OVER_TIME
    assert pattern.state is BehavioralPatternStateV1.STABLE
    assert [item.observation_count for item in pattern.windows] == [2, 1]
    assert pattern.selected_option is not None
    assert pattern.support_count == 3
    assert pattern.support_ratio == BehavioralRatioV1(3, 3)


def test_two_plus_two_unanimous_different_choices_builds_changed_without_winner(
    tmp_path: Path,
) -> None:
    vault = create_vault(tmp_path / "vault")
    _write_decisions(
        vault,
        (
            (1, GENERATED_AT - timedelta(days=180), "Alpha"),
            (2, GENERATED_AT - timedelta(days=179), "Alpha"),
            (3, GENERATED_AT - timedelta(days=90), "Beta"),
            (4, GENERATED_AT - timedelta(days=1), "Beta"),
        ),
    )

    pattern = _single_pattern(_build(vault))

    assert pattern.pattern_type is BehavioralPatternTypeV1.CHANGED_OVER_TIME
    assert pattern.state is BehavioralPatternStateV1.CHANGED
    assert pattern.selected_option is None
    assert pattern.support_count is None
    assert pattern.support_ratio is None
    assert [item.option.option_index for item in pattern.choice_support] == [0, 1]
    assert [_support(pattern, index).support_count for index in (0, 1)] == [2, 2]
    assert [item.observation_count for item in pattern.windows] == [2, 2]
    assert [item.choice_support[0].option.option_index for item in pattern.windows] == [0, 1]


def test_mixed_evidence_without_strict_change_has_no_winner_and_keeps_option_order(
    tmp_path: Path,
) -> None:
    vault = create_vault(tmp_path / "vault")
    _write_decisions(
        vault,
        (
            (1, GENERATED_AT - timedelta(days=2), "Beta"),
            (2, GENERATED_AT - timedelta(days=1), "Alpha"),
            (3, GENERATED_AT, "Alpha"),
        ),
    )

    pattern = _single_pattern(_build(vault))

    assert pattern.pattern_type is BehavioralPatternTypeV1.MIXED_EXACT_CHOICES
    assert pattern.state is BehavioralPatternStateV1.MIXED
    assert pattern.selected_option is None
    assert pattern.support_count is None
    assert pattern.support_ratio is None
    assert [(item.option.option_index, item.support_count) for item in pattern.choice_support] == [
        (0, 2),
        (1, 1),
    ]
    assert BehavioralCaveatCodeV1.MIXED_NO_WINNER in pattern.caveats


@pytest.mark.parametrize("count", [1, 2])
def test_one_or_two_active_observations_are_insufficient(tmp_path: Path, count: int) -> None:
    vault = create_vault(tmp_path / "vault")
    _write_decisions(
        vault,
        tuple(
            (index, GENERATED_AT - timedelta(days=index), "Alpha") for index in range(1, count + 1)
        ),
    )

    pattern = _single_pattern(_build(vault))

    assert pattern.pattern_type is BehavioralPatternTypeV1.INSUFFICIENT_EVIDENCE
    assert pattern.state is BehavioralPatternStateV1.INSUFFICIENT
    assert pattern.selected_option is None
    assert pattern.support_count is None
    assert pattern.support_ratio is None
    assert pattern.total_comparable_observations == count
    assert pattern.choice_support[0].support_ratio == BehavioralRatioV1(count, count)
    assert BehavioralCaveatCodeV1.INSUFFICIENT_COMPARABLE_EVIDENCE in pattern.caveats


def test_zero_active_observations_are_insufficient_and_outside_horizon_is_not_used(
    tmp_path: Path,
) -> None:
    vault = create_vault(tmp_path / "vault")
    _write_decisions(vault, ((1, GENERATED_AT - timedelta(days=180, seconds=1), "Alpha"),))

    result = _build(vault)
    pattern = _single_pattern(result)

    assert pattern.pattern_type is BehavioralPatternTypeV1.INSUFFICIENT_EVIDENCE
    assert pattern.total_comparable_observations == 0
    assert pattern.provenance.source_count == 0
    assert result.excluded_outside_horizon_count == 1
    assert BehavioralCaveatCodeV1.OUTSIDE_HORIZON_EXCLUDED in result.caveats


def test_missing_exact_identity_builds_safe_not_comparable_pattern(tmp_path: Path) -> None:
    vault = create_vault(tmp_path / "vault")
    write_note(
        vault,
        "10 Projects/Unknown.md",
        _journal_note(
            _id(1),
            evidence_at="unknown",
            evidence_at_precision="unknown",
        ),
    )
    write_note(
        vault,
        "10 Projects/Missing-domain.md",
        _journal_note(_id(2), domain=None),
    )

    result = _build(vault)
    pattern = _single_pattern(result)

    assert pattern.pattern_type is BehavioralPatternTypeV1.NOT_COMPARABLE
    assert pattern.state is BehavioralPatternStateV1.NOT_COMPARABLE
    assert pattern.cohort is None
    assert pattern.total_comparable_observations == 0
    assert pattern.provenance.source_journal_uuids == ()
    assert pattern.support_ratio is None
    assert BehavioralCaveatCodeV1.NOT_COMPARABLE_UNDER_V1 in result.caveats
    assert BehavioralCaveatCodeV1.UNKNOWN_TIME_EXCLUDED in result.caveats


def test_exact_window_boundaries_and_future_are_reflected_without_fallback_time(
    tmp_path: Path,
) -> None:
    vault = create_vault(tmp_path / "vault")
    _write_decisions(
        vault,
        (
            (1, GENERATED_AT - timedelta(days=180), "Alpha"),
            (2, GENERATED_AT - timedelta(days=90, seconds=1), "Alpha"),
            (3, GENERATED_AT - timedelta(days=90), "Alpha"),
            (4, GENERATED_AT, "Alpha"),
            (5, GENERATED_AT + timedelta(seconds=1), "Alpha"),
            (6, GENERATED_AT - timedelta(days=180, seconds=1), "Alpha"),
        ),
    )

    result = _build(vault)
    pattern = _single_pattern(result)

    assert [item.observation_count for item in pattern.windows] == [2, 2]
    assert result.excluded_outside_horizon_count == 1
    assert BehavioralCaveatCodeV1.FUTURE_OR_INVALID_TIME_EXCLUDED in result.caveats
    assert BehavioralCaveatCodeV1.OUTSIDE_HORIZON_EXCLUDED in result.caveats
    assert pattern.pattern_type is BehavioralPatternTypeV1.STABLE_OVER_TIME


def test_outcome_presence_is_exact_metadata_and_does_not_change_choice_pattern(
    tmp_path: Path,
) -> None:
    vault = create_vault(tmp_path / "vault")
    _write_decisions(
        vault,
        (
            (1, GENERATED_AT, "Alpha"),
            (2, GENERATED_AT - timedelta(days=1), "Alpha"),
            (3, GENERATED_AT - timedelta(days=2), "Alpha"),
        ),
    )
    write_note(vault, "10 Projects/Outcome.md", _outcome_note(_id(20), _id(1)))

    with_outcome = _single_pattern(_build(vault))
    assert with_outcome.outcome_presence == {"present": 1, "absent": 2}
    assert "success" not in str(with_outcome.as_dict())
    assert "reward" not in str(with_outcome.as_dict())

    write_note(
        vault,
        "10 Projects/Outcome.md",
        _outcome_note(_id(20), _id(1), actual="A completely different private text."),
    )
    edited_outcome = _single_pattern(_build(vault))
    assert edited_outcome.outcome_presence == {"present": 1, "absent": 2}
    assert edited_outcome.pattern_type is with_outcome.pattern_type
    assert edited_outcome.choice_support == with_outcome.choice_support

    (vault / "10 Projects/Outcome.md").unlink()
    deleted_outcome = _single_pattern(_build(vault))
    assert deleted_outcome.outcome_presence == {"present": 0, "absent": 3}
    assert deleted_outcome.choice_support == with_outcome.choice_support


def test_rebuild_edit_delete_and_threshold_crossing_never_retains_old_result(
    tmp_path: Path,
) -> None:
    vault = create_vault(tmp_path / "vault")
    _write_decisions(
        vault,
        (
            (1, GENERATED_AT, "Alpha"),
            (2, GENERATED_AT - timedelta(days=1), "Alpha"),
        ),
    )
    first = _single_pattern(_build(vault))
    assert first.pattern_type is BehavioralPatternTypeV1.INSUFFICIENT_EVIDENCE

    write_note(
        vault,
        "10 Projects/Decision-3.md",
        _journal_note(_id(3), evidence_at=GENERATED_AT - timedelta(days=2)),
    )
    crossed = _single_pattern(_build(vault))
    assert crossed.pattern_type is BehavioralPatternTypeV1.REPEATED_EXACT_CHOICE

    write_note(
        vault,
        "10 Projects/Decision-1.md",
        _journal_note(
            _id(1),
            evidence_at=GENERATED_AT,
            body=_journal_body(chosen="Beta"),
        ),
    )
    edited = _single_pattern(_build(vault))
    assert edited.pattern_type is BehavioralPatternTypeV1.MIXED_EXACT_CHOICES

    (vault / "10 Projects/Decision-3.md").unlink()
    deleted = _single_pattern(_build(vault))
    assert deleted.pattern_type is BehavioralPatternTypeV1.INSUFFICIENT_EVIDENCE
    assert deleted.provenance.source_journal_uuids == (_id(1), _id(2))


def test_result_and_choice_order_are_independent_of_filesystem_order(tmp_path: Path) -> None:
    first_vault = create_vault(tmp_path / "first")
    second_vault = create_vault(tmp_path / "second")
    values = (
        (1, GENERATED_AT, "Alpha"),
        (2, GENERATED_AT - timedelta(days=1), "Beta"),
        (3, GENERATED_AT - timedelta(days=2), "Alpha"),
    )
    for index, evidence_at, chosen in values:
        content = _journal_note(
            _id(index), evidence_at=evidence_at, body=_journal_body(chosen=chosen)
        )
        write_note(first_vault, f"10 Projects/{index}.md", content)
    for index, evidence_at, chosen in reversed(values):
        content = _journal_note(
            _id(index), evidence_at=evidence_at, body=_journal_body(chosen=chosen)
        )
        write_note(second_vault, f"10 Projects/{index}.md", content)

    assert _build(first_vault).to_json() == _build(second_vault).to_json()
    pattern = _single_pattern(_build(first_vault))
    assert [item.option.option_index for item in pattern.choice_support] == [0, 1]
    assert [item.window for item in pattern.windows] == [
        BehavioralTemporalWindowV1.HISTORICAL,
        BehavioralTemporalWindowV1.CURRENT,
    ]
    assert list(pattern.provenance.source_journal_uuids) == sorted(
        pattern.provenance.source_journal_uuids,
        key=str,
    )


def test_raw_context_labels_body_and_path_are_absent_from_emitted_result(tmp_path: Path) -> None:
    vault = create_vault(tmp_path / "vault")
    private_body = _journal_body(
        situation="PRIVATE SITUATION",
        information="PRIVATE INFORMATION",
        options=("PRIVATE ALPHA", "PRIVATE BETA"),
        criteria=("PRIVATE CRITERION",),
        chosen="PRIVATE ALPHA",
    )
    _write_decisions(
        vault,
        tuple(
            (index, GENERATED_AT - timedelta(days=index), "PRIVATE ALPHA") for index in range(1, 4)
        ),
        body=private_body,
    )

    serialized = _build(vault).to_json()
    for private_value in (
        "PRIVATE SITUATION",
        "PRIVATE INFORMATION",
        "PRIVATE ALPHA",
        "PRIVATE BETA",
        "PRIVATE CRITERION",
        "Decision-1.md",
    ):
        assert private_value not in serialized


def test_same_clock_and_source_produce_equivalent_dto_and_no_vault_write(tmp_path: Path) -> None:
    vault = create_vault(tmp_path / "vault")
    _write_decisions(
        vault,
        (
            (1, GENERATED_AT, "Alpha"),
            (2, GENERATED_AT - timedelta(days=1), "Alpha"),
            (3, GENERATED_AT - timedelta(days=2), "Alpha"),
        ),
    )
    before = snapshot_tree(vault)

    first = _build(vault)
    second = _build(vault)

    assert first.to_json() == second.to_json()
    assert before == snapshot_tree(vault)


class _ExplodingReader:
    def __init__(self) -> None:
        self.calls = 0

    def scan(self) -> VaultSnapshot:
        self.calls += 1
        raise RuntimeError("private body / path / UUID inventory")


def test_invalid_request_and_policy_fail_before_vault_read() -> None:
    reader = _ExplodingReader()
    builder = BuildBehavioralSelfModel(reader, clock=lambda: GENERATED_AT)

    with pytest.raises(BehavioralSelfModelInvalidRequestError) as invalid_request:
        builder.execute(BehavioralSelfModelRequest(max_patterns=0))
    assert invalid_request.value.code == BehavioralSelfModelErrorCode.INVALID_REQUEST.value
    assert reader.calls == 0

    with pytest.raises(BehavioralSelfModelPolicyMismatchError):
        BuildBehavioralSelfModel(
            reader,
            policy=replace(DEFAULT_BEHAVIORAL_SELF_MODEL_POLICY, policy_id="wrong"),
            clock=lambda: GENERATED_AT,
        ).execute()
    assert reader.calls == 0


def test_source_and_enrolled_journal_errors_are_fixed_and_private_safe(tmp_path: Path) -> None:
    reader = _ExplodingReader()
    with pytest.raises(BehavioralSelfModelSourceUnavailableError) as unavailable:
        BuildBehavioralSelfModel(reader, clock=lambda: GENERATED_AT).execute()
    assert unavailable.value.code == BehavioralSelfModelErrorCode.SOURCE_UNAVAILABLE.value
    assert "private" not in str(unavailable.value)
    assert "UUID" not in str(unavailable.value)

    vault = create_vault(tmp_path / "vault")
    write_note(
        vault,
        "10 Projects/Invalid.md",
        _journal_note(_id(1), body="not a Decision Journal"),
    )
    with pytest.raises(BehavioralSelfModelInvalidJournalError) as invalid:
        _build(vault)
    assert invalid.value.code == BehavioralSelfModelErrorCode.INVALID_JOURNAL.value
    assert "Invalid.md" not in str(invalid.value)


def test_policy_fingerprint_and_result_validation_are_exact(tmp_path: Path) -> None:
    vault = create_vault(tmp_path / "vault")
    _write_decisions(
        vault,
        (
            (1, GENERATED_AT, "Alpha"),
            (2, GENERATED_AT - timedelta(days=1), "Alpha"),
            (3, GENERATED_AT - timedelta(days=2), "Alpha"),
        ),
    )
    result = _build(vault)

    assert result.policy_fingerprint == POLICY_FINGERPRINT
    assert validate_behavioral_self_model_result(result) is result
    assert isinstance(result.to_json(), str)
    assert isinstance(result.patterns[0].outcome_presence["present"], int)


def test_provenance_is_sorted_unique_and_bounded() -> None:
    source_ids = tuple(_id(index) for index in (3, 1, 2))
    sorted_ids = tuple(sorted(source_ids, key=str))
    provenance = BehavioralProvenanceV1(
        source_journal_uuids=sorted_ids,
        source_count=3,
        provenance_fingerprint=behavioral_provenance_fingerprint(sorted_ids),
    )
    assert provenance.source_journal_uuids == sorted_ids
    assert provenance.source_count == 3

    too_many = tuple(
        UUID(f"0198f4c5-6a00-7000-8000-{index:012x}")
        for index in range(1, MAX_BEHAVIORAL_PROVENANCE_UUIDS + 2)
    )
    with pytest.raises(ValueError):
        BehavioralProvenanceV1(
            source_journal_uuids=too_many,
            source_count=len(too_many),
            provenance_fingerprint=behavioral_provenance_fingerprint(too_many),
        )


def test_pattern_limit_is_fail_closed_without_truncation(tmp_path: Path) -> None:
    vault = create_vault(tmp_path / "vault")
    _write_decisions(
        vault,
        (
            (1, GENERATED_AT, "Alpha"),
            (2, GENERATED_AT - timedelta(days=1), "Alpha"),
            (3, GENERATED_AT - timedelta(days=2), "Alpha"),
        ),
    )
    _write_decisions(
        vault,
        (
            (4, GENERATED_AT, "Alpha"),
            (5, GENERATED_AT - timedelta(days=1), "Alpha"),
            (6, GENERATED_AT - timedelta(days=2), "Alpha"),
        ),
        body=_journal_body(situation="A different exact context"),
    )

    with pytest.raises(BehavioralSelfModelResultTooLargeError):
        _build_with_request(vault, BehavioralSelfModelRequest(max_patterns=1))


def _build_with_request(
    vault: Path,
    request: BehavioralSelfModelRequest,
) -> BehavioralSelfModelResultV1:
    return BuildBehavioralSelfModel(
        FileSystemVaultReader(vault),
        clock=lambda: GENERATED_AT,
    ).execute(request)


def test_stage10a_outcome_presence_enum_remains_closed() -> None:
    assert tuple(item.value for item in BehavioralOutcomePresenceV1) == ("absent", "present")
    assert MAX_BEHAVIORAL_PATTERNS == MAX_BEHAVIORAL_PROVENANCE_UUIDS == 200
