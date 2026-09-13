"""Deterministic Stage 10A behavioral observation/cohort tests."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID

import pytest

from second_brain.adapters.vault import FileSystemVaultReader
from second_brain.application.behavioral_observation import (
    ACTIVE_HORIZON_DAYS,
    DEFAULT_BEHAVIORAL_OBSERVATION_POLICY,
    DERIVATION_VERSION,
    GROUPING_POLICY,
    POLICY_FINGERPRINT,
    BehavioralCohortIdentityV1,
    BehavioralObservationBuildResultV1,
    BehavioralObservationRequest,
    BehavioralOutcomePresenceV1,
    BehavioralSelfModelErrorCode,
    BehavioralSelfModelInvalidJournalError,
    BehavioralSelfModelInvalidRequestError,
    BehavioralSelfModelPolicyMismatchError,
    BehavioralSelfModelSourceUnavailableError,
    BehavioralTemporalWindowV1,
    BuildBehavioralObservations,
    behavioral_cohort_fingerprint,
    classify_behavioral_window,
    normalize_behavioral_text,
    validate_behavioral_observation_policy,
)
from second_brain.application.decision_journal import (
    render_decision_journal_body,
    render_outcome_observation_body,
)
from second_brain.application.personal_memory import PERSONAL_MEMORY_MARKER
from second_brain.application.reports import VaultSnapshot
from tests.conftest import create_vault, snapshot_tree, write_note

GENERATED_AT = datetime(2026, 9, 13, 12, 0, tzinfo=UTC)
DECISION_IDS = tuple(
    UUID(f"0198f4c5-6a00-7000-8000-0000000000{index:02d}") for index in range(10, 20)
)
OUTCOME_IDS = tuple(
    UUID(f"0198f4c5-6a00-7000-8000-0000000000{index:02d}") for index in range(30, 35)
)


class _ExplodingReader:
    def __init__(self) -> None:
        self.calls = 0

    def scan(self) -> VaultSnapshot:
        self.calls += 1
        raise RuntimeError("private body / path / UUID inventory")


type _CohortValues = tuple[str, str, str, tuple[str, ...], tuple[str, ...]]


def _cohort_fingerprint(values: _CohortValues) -> str:
    domain, situation, information, options, criteria = values
    return behavioral_cohort_fingerprint(
        domain=domain,
        situation=situation,
        information=information,
        options=options,
        criteria=criteria,
    )


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
        reasons="The selected option fits the reviewed context.",
        confidence="Medium.",
        expected_result="A bounded result.",
    )


def _outcome_body(*, actual: str = "A later observation.") -> str:
    return render_outcome_observation_body(actual_result=actual, reassessment="", notes="")


def _journal_note(
    note_id: UUID,
    *,
    body: str | None = None,
    evidence_at: str = "2026-09-13T11:00:00+00:00",
    evidence_at_precision: str = "exact",
    domain: str | None = "work",
    marker: str = "1",
    evidence_kind: str = "observed_decision",
    self_kind: str = "decision",
) -> str:
    fields = [
        "---",
        f"id: {note_id}",
        "type: zettel",
        "created: 2026-09-13T11:30:00+00:00",
        f"{PERSONAL_MEMORY_MARKER}: {marker}",
        f"evidence_kind: {evidence_kind}",
        f"self_kind: {self_kind}",
        f"evidence_at: {evidence_at}",
        f"evidence_at_precision: {evidence_at_precision}",
    ]
    if domain is not None:
        fields.append(f"domain: {domain}")
    fields.extend(("tags: [behavioral]", "links: []", "---"))
    return "\n".join(fields) + "\n" + (body or _journal_body())


def _outcome_note(
    note_id: UUID,
    decision_id: UUID,
    *,
    body: str | None = None,
) -> str:
    return _journal_note(
        note_id,
        body=body or _outcome_body(),
        evidence_at="2026-09-13T11:30:00+00:00",
        domain="work",
        evidence_kind="outcome_later_observation",
        self_kind="outcome",
    ).replace(
        "evidence_at_precision: exact\n",
        "evidence_at_precision: exact\ndecision_id: " + str(decision_id) + "\n",
        1,
    )


def _build(vault: Path) -> BehavioralObservationBuildResultV1:
    return BuildBehavioralObservations(
        FileSystemVaultReader(vault),
        clock=lambda: GENERATED_AT,
    ).execute()


def test_valid_journal_emits_immutable_raw_free_observation(tmp_path: Path) -> None:
    vault = create_vault(tmp_path / "vault")
    body = _journal_body(
        situation="PRIVATE SITUATION TEXT",
        information="PRIVATE INFORMATION TEXT",
        options=("PRIVATE ALPHA", "PRIVATE BETA"),
        chosen="PRIVATE ALPHA",
    )
    write_note(vault, "10 Projects/Decision.md", _journal_note(DECISION_IDS[0], body=body))

    result = _build(vault)
    observation = result.observations[0]

    assert observation.contract_version == "behavioral-self-model-v1"
    assert observation.derivation_version == DERIVATION_VERSION
    assert observation.source_journal_uuid == DECISION_IDS[0]
    assert observation.evidence_at == datetime(2026, 9, 13, 11, tzinfo=UTC)
    assert observation.cohort.grouping_policy == GROUPING_POLICY
    assert observation.cohort.domain == "work"
    assert observation.chosen_option.option_index == 0
    assert observation.outcome_presence is BehavioralOutcomePresenceV1.ABSENT
    serialized = observation.to_json()
    for private_value in (
        "PRIVATE SITUATION TEXT",
        "PRIVATE INFORMATION TEXT",
        "PRIVATE ALPHA",
        "PRIVATE BETA",
    ):
        assert private_value not in serialized


def test_non_enrolled_journal_like_note_is_not_behavioral_evidence(tmp_path: Path) -> None:
    vault = create_vault(tmp_path / "vault")
    write_note(vault, "10 Projects/Legacy.md", _journal_note(DECISION_IDS[0], marker="0"))

    result = _build(vault)

    assert result.observations == ()
    assert result.eligible_journal_count == 0


def test_invalid_enrolled_journal_fails_closed_with_safe_error(tmp_path: Path) -> None:
    vault = create_vault(tmp_path / "vault")
    write_note(
        vault,
        "10 Projects/Invalid.md",
        _journal_note(
            DECISION_IDS[0],
            evidence_kind="observed_decision",
            self_kind="preference",
        ),
    )

    with pytest.raises(BehavioralSelfModelInvalidJournalError) as error:
        _build(vault)

    assert error.value.code == BehavioralSelfModelErrorCode.INVALID_JOURNAL.value
    assert "Invalid.md" not in str(error.value)
    assert "PRIVATE" not in str(error.value)


def test_duplicate_current_uuid_fails_closed(tmp_path: Path) -> None:
    vault = create_vault(tmp_path / "vault")
    write_note(vault, "10 Projects/One.md", _journal_note(DECISION_IDS[0]))
    write_note(vault, "10 Projects/Two.md", _journal_note(DECISION_IDS[0]))

    with pytest.raises(BehavioralSelfModelInvalidJournalError):
        _build(vault)


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("  same\tvalue\n  ", "same value"),
        ("Case", "Case"),
        ("Café", "Café"),
    ],
)
def test_normalization_is_whitespace_only(value: str, expected: str) -> None:
    assert normalize_behavioral_text(value) == expected


def test_exact_cohort_identity_preserves_case_punctuation_and_unicode_representation() -> None:
    base: _CohortValues = (
        "work",
        "Same situation",
        "Same information",
        ("Alpha", "Beta"),
        ("Speed",),
    )

    assert _cohort_fingerprint(base) == _cohort_fingerprint(
        (base[0], " Same   situation ", base[2], base[3], base[4])
    )
    assert _cohort_fingerprint(base) != _cohort_fingerprint(
        (base[0], "same situation", base[2], base[3], base[4])
    )
    assert _cohort_fingerprint(base) != _cohort_fingerprint(
        (base[0], "Same situation.", base[2], base[3], base[4])
    )
    assert _cohort_fingerprint((base[0], "Café", base[2], base[3], base[4])) != _cohort_fingerprint(
        (base[0], "Cafe\u0301", base[2], base[3], base[4])
    )


@pytest.mark.parametrize(
    "changed",
    [
        ("personal", "Same situation", "Same information", ("Alpha", "Beta"), ("Speed", "Quality")),
        ("work", "Other situation", "Same information", ("Alpha", "Beta"), ("Speed", "Quality")),
        ("work", "Same situation", "Other information", ("Alpha", "Beta"), ("Speed", "Quality")),
        ("work", "Same situation", "Same information", ("Beta", "Alpha"), ("Speed", "Quality")),
        ("work", "Same situation", "Same information", ("Alpha", "Beta"), ("Quality", "Speed")),
    ],
)
def test_exact_cohort_key_changes_for_each_context_component(
    changed: _CohortValues,
) -> None:
    base: _CohortValues = (
        "work",
        "Same situation",
        "Same information",
        ("Alpha", "Beta"),
        ("Speed", "Quality"),
    )
    assert _cohort_fingerprint(base) != _cohort_fingerprint(changed)


def test_different_choices_share_one_cohort_and_option_order_is_preserved(tmp_path: Path) -> None:
    vault = create_vault(tmp_path / "vault")
    common_situation = "Same context"
    common_information = "Same facts"
    common_options = ("Alpha", "Beta")
    common_criteria = ("Speed", "Quality")
    write_note(
        vault,
        "10 Projects/B.md",
        _journal_note(
            DECISION_IDS[1],
            body=_journal_body(
                chosen="Beta",
                situation=common_situation,
                information=common_information,
                options=common_options,
                criteria=common_criteria,
            ),
        ),
    )
    write_note(
        vault,
        "10 Projects/A.md",
        _journal_note(
            DECISION_IDS[2],
            body=_journal_body(
                chosen="Alpha",
                situation=common_situation,
                information=common_information,
                options=common_options,
                criteria=common_criteria,
            ),
        ),
    )
    write_note(
        vault,
        "10 Projects/Reordered.md",
        _journal_note(
            DECISION_IDS[3],
            body=_journal_body(
                chosen="Beta",
                options=("Beta", "Alpha"),
                criteria=common_criteria,
                situation=common_situation,
                information=common_information,
            ),
        ),
    )

    result = _build(vault)
    assert len(result.cohorts) == 2
    same_context = next(bucket for bucket in result.cohorts if len(bucket.observations) == 2)
    assert {item.chosen_option.option_index for item in same_context.observations} == {0, 1}
    assert same_context.observations[0].cohort == same_context.observations[1].cohort
    assert same_context.observations[0].option_namespace.ordered_option_fingerprints != (
        next(bucket for bucket in result.cohorts if len(bucket.observations) == 1)
        .observations[0]
        .option_namespace.ordered_option_fingerprints
    )


def test_outcome_is_presence_only_and_rebuilds_current_state(tmp_path: Path) -> None:
    vault = create_vault(tmp_path / "vault")
    decision_path = write_note(
        vault,
        "10 Projects/Decision.md",
        _journal_note(DECISION_IDS[4]),
    )
    outcome_path = vault / "10 Projects/Outcome.md"

    absent = _build(vault)
    assert absent.observations[0].outcome_presence is BehavioralOutcomePresenceV1.ABSENT

    write_note(vault, "10 Projects/Outcome.md", _outcome_note(OUTCOME_IDS[0], DECISION_IDS[4]))
    present = _build(vault)
    present_observation = present.observations[0]
    assert present_observation.outcome_presence is BehavioralOutcomePresenceV1.PRESENT
    present_snapshot = present_observation.journal_snapshot_fingerprint

    write_note(
        vault,
        "10 Projects/Outcome-2.md",
        _outcome_note(OUTCOME_IDS[1], DECISION_IDS[4], body=_outcome_body(actual="Edited body.")),
    )
    edited = _build(vault)
    assert edited.observations[0].outcome_presence is BehavioralOutcomePresenceV1.PRESENT
    assert edited.observations[0].journal_snapshot_fingerprint == present_snapshot

    outcome_path.unlink()
    outcome_path_2 = vault / "10 Projects/Outcome-2.md"
    outcome_path_2.unlink()
    deleted = _build(vault)
    assert deleted.observations[0].outcome_presence is BehavioralOutcomePresenceV1.ABSENT
    assert deleted.observations[0].journal_snapshot_fingerprint != present_snapshot
    assert decision_path.exists()


def test_multiple_outcomes_collapse_to_presence(tmp_path: Path) -> None:
    vault = create_vault(tmp_path / "vault")
    write_note(vault, "10 Projects/Decision.md", _journal_note(DECISION_IDS[5]))
    write_note(vault, "10 Projects/Outcome-1.md", _outcome_note(OUTCOME_IDS[2], DECISION_IDS[5]))
    write_note(vault, "10 Projects/Outcome-2.md", _outcome_note(OUTCOME_IDS[3], DECISION_IDS[5]))

    result = _build(vault)

    assert result.observations[0].outcome_presence is BehavioralOutcomePresenceV1.PRESENT
    assert "success" not in result.to_json()
    assert "failure" not in result.to_json()


def test_temporal_boundaries_are_exact_and_old_observation_is_not_active(tmp_path: Path) -> None:
    current_lower = GENERATED_AT - timedelta(days=90)
    historical_lower = GENERATED_AT - timedelta(days=180)
    assert (
        classify_behavioral_window(current_lower, GENERATED_AT)
        is BehavioralTemporalWindowV1.CURRENT
    )
    assert (
        classify_behavioral_window(historical_lower, GENERATED_AT)
        is BehavioralTemporalWindowV1.HISTORICAL
    )
    assert (
        classify_behavioral_window(GENERATED_AT + timedelta(seconds=1), GENERATED_AT)
        is BehavioralTemporalWindowV1.FUTURE
    )
    assert (
        classify_behavioral_window(historical_lower - timedelta(seconds=1), GENERATED_AT)
        is BehavioralTemporalWindowV1.OUTSIDE_HORIZON
    )
    assert classify_behavioral_window("unknown", GENERATED_AT) is BehavioralTemporalWindowV1.UNKNOWN

    vault = create_vault(tmp_path / "vault")
    times = (
        GENERATED_AT,
        current_lower,
        GENERATED_AT - timedelta(days=90, seconds=-1),
        historical_lower,
        historical_lower - timedelta(seconds=1),
        GENERATED_AT + timedelta(seconds=1),
    )
    for index, evidence_at in enumerate(times):
        write_note(
            vault,
            f"10 Projects/Decision-{index}.md",
            _journal_note(
                DECISION_IDS[index],
                evidence_at=evidence_at.isoformat(),
            ),
        )

    result = _build(vault)
    bucket = result.cohorts[0]
    current = bucket.window_counts(BehavioralTemporalWindowV1.CURRENT, generated_at=GENERATED_AT)
    historical = bucket.window_counts(
        BehavioralTemporalWindowV1.HISTORICAL,
        generated_at=GENERATED_AT,
    )
    assert current.observation_count == 3
    assert historical.observation_count == 1
    assert result.excluded_future_or_invalid_time_count == 1
    assert result.excluded_outside_horizon_count == 1


def test_unknown_time_and_missing_domain_are_safe_exclusions(tmp_path: Path) -> None:
    vault = create_vault(tmp_path / "vault")
    write_note(
        vault,
        "10 Projects/Unknown.md",
        _journal_note(
            DECISION_IDS[0],
            evidence_at="unknown",
            evidence_at_precision="unknown",
        ),
    )
    write_note(
        vault,
        "10 Projects/Missing-domain.md",
        _journal_note(DECISION_IDS[1], domain=None),
    )

    result = _build(vault)

    assert result.observations == ()
    assert result.eligible_journal_count == 2
    assert result.excluded_unknown_time_count == 1
    assert result.excluded_missing_domain_count == 1


def test_rebuild_has_no_history_deduplication_or_persistence(tmp_path: Path) -> None:
    vault = create_vault(tmp_path / "vault")
    identical = _journal_body()
    write_note(vault, "10 Projects/Two.md", _journal_note(DECISION_IDS[7], body=identical))
    write_note(vault, "10 Projects/One.md", _journal_note(DECISION_IDS[8], body=identical))
    before = snapshot_tree(vault)

    first = _build(vault)
    assert len(first.observations) == 2

    write_note(
        vault,
        "10 Projects/Two.md",
        _journal_note(DECISION_IDS[7], body=_journal_body(chosen="Beta")),
    )
    edited = _build(vault)
    assert len(edited.observations) == 2
    assert {item.chosen_option.option_index for item in edited.observations} == {0, 1}

    (vault / "10 Projects/One.md").unlink()
    deleted = _build(vault)
    assert tuple(item.source_journal_uuid for item in deleted.observations) == (DECISION_IDS[7],)
    assert before != snapshot_tree(vault)
    assert all("history" not in key.casefold() for key in snapshot_tree(vault))


def test_result_order_is_independent_of_filesystem_order(tmp_path: Path) -> None:
    first_vault = create_vault(tmp_path / "first")
    second_vault = create_vault(tmp_path / "second")
    values = (
        (DECISION_IDS[1], "z.md", "Z context"),
        (DECISION_IDS[2], "a.md", "A context"),
    )
    for note_id, filename, situation in values:
        content = _journal_note(note_id, body=_journal_body(situation=situation))
        write_note(first_vault, f"10 Projects/{filename}", content)
    for note_id, filename, situation in reversed(values):
        content = _journal_note(note_id, body=_journal_body(situation=situation))
        write_note(second_vault, f"10 Projects/{filename}", content)

    assert _build(first_vault).to_json() == _build(second_vault).to_json()


def test_request_clock_and_policy_fail_before_or_at_safe_boundary(tmp_path: Path) -> None:
    vault = create_vault(tmp_path / "vault")
    reader = _ExplodingReader()
    builder = BuildBehavioralObservations(reader, clock=lambda: GENERATED_AT)

    with pytest.raises(BehavioralSelfModelInvalidRequestError):
        builder.execute(BehavioralObservationRequest(max_observations=0))
    assert reader.calls == 0

    with pytest.raises(BehavioralSelfModelPolicyMismatchError):
        BuildBehavioralObservations(
            FileSystemVaultReader(vault),
            policy=replace(DEFAULT_BEHAVIORAL_OBSERVATION_POLICY, policy_id="wrong"),
        ).execute()


def test_source_error_is_fixed_and_private_safe() -> None:
    reader = _ExplodingReader()

    with pytest.raises(BehavioralSelfModelSourceUnavailableError) as error:
        BuildBehavioralObservations(reader, clock=lambda: GENERATED_AT).execute()

    assert error.value.code == BehavioralSelfModelErrorCode.SOURCE_UNAVAILABLE.value
    assert "private" not in str(error.value)
    assert "UUID" not in str(error.value)


def test_policy_fingerprint_and_dto_contract_are_exact() -> None:
    assert validate_behavioral_observation_policy(DEFAULT_BEHAVIORAL_OBSERVATION_POLICY) == (
        POLICY_FINGERPRINT
    )
    assert DERIVATION_VERSION == "behavioral-observation-v1"
    assert GROUPING_POLICY == "exact-reviewed-decision-context-v1"
    assert ACTIVE_HORIZON_DAYS == 180

    with pytest.raises(ValueError):
        BehavioralCohortIdentityV1(
            grouping_policy=GROUPING_POLICY,
            domain="Work",
            situation_fingerprint="sha256:" + "0" * 64,
            information_fingerprint="sha256:" + "0" * 64,
            option_namespace_fingerprint="sha256:" + "0" * 64,
            criteria_fingerprint="sha256:" + "0" * 64,
            cohort_fingerprint="sha256:" + "0" * 64,
        )
