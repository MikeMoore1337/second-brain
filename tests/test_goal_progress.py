"""Provider-free deterministic tests for Cognitive Twin v3 Stage 12A."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from uuid import UUID

import pytest

from second_brain.adapters.vault import FileSystemVaultReader
from second_brain.application.goal_progress import (
    DEFAULT_GOAL_PROGRESS_POLICY,
    GOAL_PROGRESS_POLICY_CANONICAL_JSON,
    GOAL_PROGRESS_POLICY_FINGERPRINT,
    MAX_GOAL_PROGRESS_RECORD_BYTES,
    GoalProgressBindingStateV1,
    MilestoneSetDefinitionV1,
    MilestoneStateV1,
    MilestoneV1,
    NumericDirectionV1,
    NumericTargetDefinitionV1,
    ObservationEligibilityReasonV1,
    ProgressModelV1,
    SupersessionChainStateV1,
    canonical_goal_progress_json,
    evaluate_observation_eligibility,
    goal_progress_hash_json,
    is_goal_progress_enrolled,
    parse_definition_record,
    parse_goal_progress_record,
    parse_observation_record,
    validate_definition_chain,
    validate_goal_binding,
    validate_goal_progress_policy,
    validate_observation_against_definition,
    validate_observation_chain,
)
from second_brain.application.growth import GrowthGoalIdentityV1
from second_brain.application.services import ValidateVault
from tests.conftest import create_vault, managed_note, write_note

GOAL_ID = "0198f4c5-6a00-7000-8000-000000000201"
DEFINITION_ID = "0198f4c5-6a00-7000-8000-000000000202"
REPLACEMENT_DEFINITION_ID = "0198f4c5-6a00-7000-8000-000000000203"
OBSERVATION_ID = "0198f4c5-6a00-7000-8000-000000000204"
REPLACEMENT_OBSERVATION_ID = "0198f4c5-6a00-7000-8000-000000000205"
OTHER_OBSERVATION_ID = "0198f4c5-6a00-7000-8000-000000000206"
GOAL_HASH = "sha256:" + "a" * 64
OTHER_GOAL_HASH = "sha256:" + "b" * 64


def _definition_fields(
    *,
    record_id: str = DEFINITION_ID,
    marker: object = 1,
    kind: object = "definition",
    model: str = "numeric_target",
    goal_hash: str = GOAL_HASH,
    definition_reviewed_at: str = "2026-09-05T10:00:00Z",
    supersedes: str | None = None,
) -> dict[str, object]:
    fields: dict[str, object] = {
        "id": record_id,
        "second_brain_goal_progress": marker,
        "goal_progress_kind": kind,
        "goal_source_uuid": GOAL_ID,
        "goal_identity_fingerprint": goal_hash,
        "goal_progress_policy_fingerprint": GOAL_PROGRESS_POLICY_FINGERPRINT,
        "definition_reviewed_at": definition_reviewed_at,
        "progress_model": model,
    }
    if model == "numeric_target":
        fields.update(
            {
                "metric_id": "weight",
                "unit": "kg",
                "baseline": "80.000",
                "target": "72",
                "direction": "decrease_to",
                "lower_bound": "40.0",
                "upper_bound": "120",
            }
        )
    else:
        fields.update(
            {
                "ordering": "display_only_v1",
                "milestones": [
                    {"id": "finish", "label": "Завершить", "ordinal": 2},
                    {"id": "start", "label": "Начать", "ordinal": 1},
                ],
            }
        )
    if supersedes is not None:
        fields["supersedes_definition_id"] = supersedes
    return fields


def _observation_fields(
    *,
    record_id: str = OBSERVATION_ID,
    definition_id: str = DEFINITION_ID,
    definition_fingerprint: str | None = None,
    observed_at: str = "2026-09-06T10:00:00Z",
    precision: str = "exact",
    value: str = "76.800",
    supersedes: str | None = None,
    model: str = "numeric_target",
) -> dict[str, object]:
    definition = parse_definition_record(_definition_fields(model=model))
    assert definition is not None
    fields: dict[str, object] = {
        "id": record_id,
        "second_brain_goal_progress": 1,
        "goal_progress_kind": "observation",
        "goal_source_uuid": GOAL_ID,
        "goal_identity_fingerprint": GOAL_HASH,
        "goal_progress_policy_fingerprint": GOAL_PROGRESS_POLICY_FINGERPRINT,
        "progress_definition_id": definition_id,
        "definition_fingerprint": definition_fingerprint or definition.definition_fingerprint,
        "progress_model": model,
        "observed_at": observed_at,
        "observed_at_precision": precision,
        "observation_reviewed_at": "2026-09-06T11:00:00Z",
    }
    if model == "numeric_target":
        fields.update({"metric_id": "weight", "unit": "kg", "value": value})
    else:
        fields.update({"milestone_id": "start", "state": "completed"})
    if supersedes is not None:
        fields["supersedes_observation_id"] = supersedes
    return fields


def _identity(
    *,
    source_uuid: str = GOAL_ID,
    claim: str = "sha256:" + "c" * 64,
    source: str = "sha256:" + "d" * 64,
) -> GrowthGoalIdentityV1:
    return GrowthGoalIdentityV1(
        source_note_uuid=UUID(source_uuid),
        dimension="goal",
        source_evidence_kind="user_statement",
        source_self_kind="goal",
        domain="work",
        evidence_at=datetime(2026, 9, 5, 10, 0, tzinfo=UTC),
        evidence_at_precision="exact",
        source_contract_version="self-model-v1",
        source_derivation_version="self-model-derivation-v1",
        self_model_policy_fingerprint="e" * 64,
        source_fingerprint=source,
        claim_fingerprint=claim,
    )


def _canonical_yaml(fields: dict[str, object]) -> str:
    """Render only the bounded fixture shapes used by the scanner tests."""

    lines = ["---"]
    rendered_fields = dict(fields)
    rendered_fields.setdefault("type", "zettel")
    rendered_fields.setdefault("created", "2026-09-05T09:00:00+03:00")
    for key, value in rendered_fields.items():
        if key == "milestones":
            lines.append("milestones:")
            assert isinstance(value, list)
            for milestone in value:
                assert isinstance(milestone, dict)
                lines.extend(
                    [
                        f"  - id: {milestone['id']}",
                        f'    label: "{milestone["label"]}"',
                        f"    ordinal: {milestone['ordinal']}",
                    ]
                )
        elif isinstance(value, str) and (
            key.endswith("_at")
            or key.endswith("_fingerprint")
            or key in {"baseline", "target", "lower_bound", "upper_bound", "value"}
        ):
            lines.append(f'{key}: "{value}"')
        else:
            rendered = "true" if value is True else "false" if value is False else str(value)
            lines.append(f"{key}: {rendered}")
    return "\n".join([*lines, "---", "# Reviewed companion record\n"])


def test_exact_marker_is_type_strict_and_wrong_marker_is_ordinary() -> None:
    assert is_goal_progress_enrolled({"second_brain_goal_progress": 1})
    assert not is_goal_progress_enrolled({"second_brain_goal_progress": True})
    assert not is_goal_progress_enrolled({"second_brain_goal_progress": "1"})
    assert not is_goal_progress_enrolled({"second_brain_goal_progress": 1.0})
    assert parse_goal_progress_record({"goal_progress_kind": "definition"}) is None


@pytest.mark.parametrize("value", [True, "1", 1.0])
def test_wrong_marker_does_not_enroll_a_legacy_note(tmp_path: Path, value: object) -> None:
    vault = create_vault(tmp_path / "vault")
    rendered_value = "true" if value is True else '"1"' if value == "1" else "1.0"
    legacy = managed_note().replace(
        "tags: []", f"tags: []\nsecond_brain_goal_progress: {rendered_value}"
    )
    write_note(vault, "10 Projects/Legacy.md", legacy)

    report = ValidateVault(FileSystemVaultReader(vault)).execute()

    assert report.error_count == 0
    assert report.goal_progress_definitions == ()
    assert report.goal_progress_observations == ()


def test_valid_numeric_definition_is_immutable_and_fingerprint_ignores_storage_metadata() -> None:
    definition = parse_definition_record(_definition_fields())

    assert definition is not None
    assert definition.progress_model is ProgressModelV1.NUMERIC_TARGET
    assert definition.baseline == Decimal("80")
    assert definition.target == Decimal("72")
    assert definition.definition_fingerprint == goal_progress_hash_json(
        definition.fingerprint_payload()
    )
    round_trip = parse_definition_record(definition.as_dict())
    assert round_trip is not None
    assert round_trip.definition_fingerprint == definition.definition_fingerprint
    with pytest.raises((AttributeError, TypeError)):
        definition.target = Decimal("71")  # type: ignore[misc]


def test_enrolled_parser_normalizes_invalid_constructor_errors_and_bounds_record_size() -> None:
    invalid = _definition_fields()
    invalid["direction"] = "not_a_direction"
    with pytest.raises(ValueError, match="GOAL_PROGRESS_INVALID_FIELD"):
        parse_definition_record(invalid)

    oversized = _definition_fields(model="milestone_set")
    oversized["milestones"] = [
        {"id": f"m-{index}", "label": "x" * 4096, "ordinal": index + 1} for index in range(200)
    ]
    assert len(str(oversized["milestones"])) > MAX_GOAL_PROGRESS_RECORD_BYTES
    with pytest.raises(ValueError, match="GOAL_PROGRESS_RECORD_TOO_LARGE"):
        parse_definition_record(oversized)


def test_enrolled_parser_rejects_invalid_common_identity_fields() -> None:
    malformed_uuid = _definition_fields()
    malformed_uuid["goal_source_uuid"] = "not-a-uuid"
    with pytest.raises(ValueError, match="GOAL_PROGRESS_INVALID_FIELD"):
        parse_definition_record(malformed_uuid)

    malformed_hash = _definition_fields()
    malformed_hash["goal_identity_fingerprint"] = "sha256:not-a-hash"
    with pytest.raises(ValueError, match="GOAL_PROGRESS_INVALID_FIELD"):
        parse_definition_record(malformed_hash)

    malformed_policy = _definition_fields()
    malformed_policy["goal_progress_policy_fingerprint"] = "sha256:" + "0" * 64
    with pytest.raises(ValueError, match="GOAL_PROGRESS_POLICY_MISMATCH"):
        parse_definition_record(malformed_policy)

    malformed_kind = _definition_fields(kind="other")
    with pytest.raises(ValueError, match="GOAL_PROGRESS_INVALID_KIND"):
        parse_definition_record(malformed_kind)


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("0", "0"),
        ("-0", "0"),
        ("1", "1"),
        ("1.0", "1"),
        ("1.000", "1"),
        ("0.10", "0.1"),
        ("-12.3400", "-12.34"),
    ],
)
def test_decimal_canonicalization_is_exact(value: str, expected: str) -> None:
    branch = NumericTargetDefinitionV1("metric", "unit", "-100", value, "increase_to")

    assert branch.as_dict()["target"] == expected


@pytest.mark.parametrize("value", ["1e3", "1E3", "1,2", "NaN", "Infinity", float("nan"), 1.0])
def test_decimal_parser_rejects_float_exponent_locale_and_non_finite_values(value: object) -> None:
    with pytest.raises(ValueError):
        NumericTargetDefinitionV1("metric", "unit", "0", value, "increase_to")  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "direction",
    ["maintain_range", "increase_to", "decrease_to", "reach_exact"],
)
def test_numeric_direction_rules_are_closed(direction: str) -> None:
    if direction == "increase_to":
        NumericTargetDefinitionV1("metric", "unit", "1", "2", direction)
    elif direction == "decrease_to":
        NumericTargetDefinitionV1("metric", "unit", "2", "1", direction)
    elif direction == "reach_exact":
        NumericTargetDefinitionV1("metric", "unit", "1", "2", direction)
    else:
        with pytest.raises(ValueError):
            NumericTargetDefinitionV1("metric", "unit", "1", "2", direction)
    assert direction in {item.value for item in NumericDirectionV1} or direction == "maintain_range"


def test_numeric_bounds_are_inclusive_and_inverted_bounds_reject() -> None:
    definition = parse_definition_record(_definition_fields())
    assert definition is not None
    observation = parse_observation_record(_observation_fields(value="40.0"))
    assert observation is not None
    assert validate_observation_against_definition(observation, definition) == ()

    with pytest.raises(ValueError):
        NumericTargetDefinitionV1("metric", "unit", "0", "1", "increase_to", "3", "2")

    out_of_bounds = parse_observation_record(_observation_fields(value="39.999"))
    assert out_of_bounds is not None
    assert validate_observation_against_definition(out_of_bounds, definition)

    wrong_metric = _observation_fields()
    wrong_metric["metric_id"] = "height"
    wrong_metric_observation = parse_observation_record(wrong_metric)
    assert wrong_metric_observation is not None
    assert validate_observation_against_definition(wrong_metric_observation, definition)


def test_milestones_are_nonempty_unique_and_ordinal_sorted() -> None:
    definition = parse_definition_record(_definition_fields(model="milestone_set"))

    assert definition is not None
    assert definition.progress_model is ProgressModelV1.MILESTONE_SET
    assert [milestone.id for milestone in definition.milestones] == ["start", "finish"]
    assert definition.milestones[0].ordinal == 1
    assert "weight" not in definition.definition_fingerprint

    with pytest.raises(ValueError):
        MilestoneSetDefinitionV1(
            "display_only_v1",
            (MilestoneV1("same", "A", 1), MilestoneV1("same", "B", 2)),
        )
    with pytest.raises(ValueError):
        MilestoneV1("id", "", 1)


def test_valid_observation_exact_unknown_and_invalid_time_pairs() -> None:
    exact = parse_observation_record(_observation_fields())
    unknown = parse_observation_record(
        _observation_fields(observed_at="unknown", precision="unknown")
    )
    assert exact is not None and unknown is not None
    definition = parse_definition_record(_definition_fields())
    assert definition is not None
    assert (
        evaluate_observation_eligibility(
            exact,
            definition,
            as_of="2026-09-07T00:00:00Z",
        ).reason
        is ObservationEligibilityReasonV1.ELIGIBLE
    )
    assert (
        evaluate_observation_eligibility(
            unknown,
            definition,
            as_of="2026-09-07T00:00:00Z",
        ).reason
        is ObservationEligibilityReasonV1.UNKNOWN_TIME
    )
    with pytest.raises(ValueError):
        parse_observation_record(_observation_fields(observed_at="unknown", precision="exact"))
    with pytest.raises(ValueError):
        parse_observation_record(
            _observation_fields(observed_at="2026-09-07T00:00:00Z", precision="unknown")
        )


def test_observation_future_and_before_review_are_excluded_without_clock_fallback() -> None:
    definition = parse_definition_record(
        _definition_fields(definition_reviewed_at="2026-09-06T10:00:00Z")
    )
    assert definition is not None
    future = parse_observation_record(_observation_fields(observed_at="2026-09-08T00:00:00Z"))
    before = parse_observation_record(_observation_fields(observed_at="2026-09-05T09:00:00Z"))
    assert future is not None and before is not None
    assert (
        evaluate_observation_eligibility(future, definition, as_of="2026-09-07T00:00:00Z").reason
        is ObservationEligibilityReasonV1.FUTURE_AS_OF
    )
    assert (
        evaluate_observation_eligibility(before, definition, as_of="2026-09-07T00:00:00Z").reason
        is ObservationEligibilityReasonV1.BEFORE_DEFINITION_REVIEW
    )


def test_milestone_observation_requires_known_id_and_closed_state() -> None:
    definition = parse_definition_record(_definition_fields(model="milestone_set"))
    assert definition is not None
    observation_fields = _observation_fields(model="milestone_set")
    observation = parse_observation_record(observation_fields)
    assert observation is not None
    assert validate_observation_against_definition(observation, definition) == ()
    assert observation.state is MilestoneStateV1.COMPLETED

    not_completed_fields = dict(observation_fields)
    not_completed_fields["state"] = "not_completed"
    not_completed = parse_observation_record(not_completed_fields)
    assert not_completed is not None
    assert not_completed.state is MilestoneStateV1.NOT_COMPLETED

    unknown = dict(observation_fields)
    unknown["milestone_id"] = "missing"
    unknown_observation = parse_observation_record(unknown)
    assert unknown_observation is not None
    assert validate_observation_against_definition(unknown_observation, definition)

    invalid_state = dict(observation_fields)
    invalid_state["state"] = "almost"
    with pytest.raises(ValueError):
        parse_observation_record(invalid_state)


def test_policy_vector_is_regression_locked() -> None:
    assert (
        validate_goal_progress_policy(DEFAULT_GOAL_PROGRESS_POLICY)
        == GOAL_PROGRESS_POLICY_FINGERPRINT
    )
    assert (
        canonical_goal_progress_json(DEFAULT_GOAL_PROGRESS_POLICY.as_dict())
        == GOAL_PROGRESS_POLICY_CANONICAL_JSON
    )
    assert canonical_goal_progress_json({"b": 1, "a": 2}) == '{"a":2,"b":1}'


def test_definition_and_observation_fingerprints_ignore_storage_metadata() -> None:
    first = parse_definition_record(_definition_fields())
    second_fields = _definition_fields()
    second_fields["id"] = REPLACEMENT_DEFINITION_ID
    second_fields["definition_reviewed_at"] = "2099-01-01T00:00:00Z"
    second = parse_definition_record(second_fields)
    assert first is not None and second is not None
    assert first.definition_fingerprint == second.definition_fingerprint

    observation = parse_observation_record(_observation_fields())
    assert observation is not None
    changed_review = dict(_observation_fields(record_id=OTHER_OBSERVATION_ID))
    changed_review["observation_reviewed_at"] = "2099-01-01T00:00:00Z"
    other = parse_observation_record(changed_review)
    assert other is not None
    assert observation.observation_fingerprint == other.observation_fingerprint


def test_goal_binding_is_exact_and_never_same_text_retargeted() -> None:
    goal = _identity()
    goal_fingerprint = goal_progress_hash_json(goal.as_dict())
    exact = validate_goal_binding(
        GOAL_ID,
        goal_fingerprint,
        current_goals=(goal,),
    )
    assert exact.state is GoalProgressBindingStateV1.EXACT_CURRENT
    assert (
        validate_goal_binding(GOAL_ID, OTHER_GOAL_HASH, current_goals=(goal,)).state
        is GoalProgressBindingStateV1.SOURCE_CHANGED
    )
    assert (
        validate_goal_binding(GOAL_ID, goal_fingerprint, current_goals=()).state
        is GoalProgressBindingStateV1.SOURCE_MISSING
    )
    other_goal = _identity(source_uuid=OBSERVATION_ID)
    assert (
        validate_goal_binding(GOAL_ID, goal_fingerprint, current_goals=(other_goal,)).state
        is GoalProgressBindingStateV1.NON_CURRENT
    )
    duplicate_source_goal = _identity(source_uuid=GOAL_ID, source="sha256:" + "f" * 64)
    assert (
        validate_goal_binding(
            GOAL_ID,
            goal_fingerprint,
            current_goals=(goal, duplicate_source_goal),
        ).state
        is GoalProgressBindingStateV1.AMBIGUOUS
    )


def test_definition_replacement_chain_has_one_leaf_and_no_newest_fallback() -> None:
    definition = parse_definition_record(_definition_fields())
    replacement = parse_definition_record(
        _definition_fields(record_id=REPLACEMENT_DEFINITION_ID, supersedes=DEFINITION_ID)
    )
    assert definition is not None and replacement is not None
    result = validate_definition_chain((replacement, definition))
    assert result.state is SupersessionChainStateV1.ONE_ACTIVE
    assert result.active_records == (replacement,)

    conflict = parse_definition_record(
        _definition_fields(record_id=OTHER_OBSERVATION_ID, supersedes=DEFINITION_ID)
    )
    assert conflict is not None
    assert (
        validate_definition_chain((definition, replacement, conflict)).state
        is SupersessionChainStateV1.CONFLICT
    )
    parallel = parse_definition_record(_definition_fields(record_id=OTHER_OBSERVATION_ID))
    assert parallel is not None
    assert (
        validate_definition_chain((definition, parallel)).state
        is SupersessionChainStateV1.MULTIPLE_ACTIVE
    )


def test_observation_correction_replaces_predecessor_but_distinct_events_remain() -> None:
    definition = parse_definition_record(_definition_fields())
    observation = parse_observation_record(_observation_fields())
    correction = parse_observation_record(
        _observation_fields(
            record_id=REPLACEMENT_OBSERVATION_ID,
            supersedes=OBSERVATION_ID,
            value="75.000",
        )
    )
    distinct = parse_observation_record(
        _observation_fields(record_id=OTHER_OBSERVATION_ID, observed_at="2026-09-07T10:00:00Z")
    )
    assert (
        definition is not None
        and observation is not None
        and correction is not None
        and distinct is not None
    )
    result = validate_observation_chain((observation, correction, distinct))
    assert result.state is SupersessionChainStateV1.MULTIPLE_ACTIVE
    assert result.active_records == (correction, distinct)
    assert validate_observation_against_definition(correction, definition) == ()

    ambiguous_correction = parse_observation_record(
        _observation_fields(
            record_id=OTHER_OBSERVATION_ID,
            supersedes=OBSERVATION_ID,
            value="74.000",
        )
    )
    assert ambiguous_correction is not None
    assert (
        validate_observation_chain((observation, correction, ambiguous_correction)).state
        is SupersessionChainStateV1.CONFLICT
    )

    mismatched_binding = parse_observation_record(
        _observation_fields(record_id=OTHER_OBSERVATION_ID, definition_id=REPLACEMENT_DEFINITION_ID)
    )
    assert mismatched_binding is not None
    assert (
        validate_observation_chain((observation, mismatched_binding)).state
        is SupersessionChainStateV1.CONFLICT
    )


def test_missing_predecessor_and_cycle_fail_closed() -> None:
    missing = parse_definition_record(
        _definition_fields(record_id=REPLACEMENT_DEFINITION_ID, supersedes=OTHER_OBSERVATION_ID)
    )
    assert missing is not None
    assert "GOAL_PROGRESS_CHAIN_PREDECESSOR_MISSING" in validate_definition_chain((missing,)).issues

    first = parse_definition_record(_definition_fields(supersedes=REPLACEMENT_DEFINITION_ID))
    second = parse_definition_record(
        _definition_fields(record_id=REPLACEMENT_DEFINITION_ID, supersedes=DEFINITION_ID)
    )
    assert first is not None and second is not None
    cycle = validate_definition_chain((first, second))
    assert cycle.state is SupersessionChainStateV1.CONFLICT
    assert "GOAL_PROGRESS_CHAIN_CYCLE" in cycle.issues


def test_report_enrolls_only_exact_definition_and_observation_markers(tmp_path: Path) -> None:
    vault = create_vault(tmp_path / "vault")
    definition_fields = _definition_fields()
    observation_fields = _observation_fields()
    write_note(vault, "10 Projects/Definition.md", _canonical_yaml(definition_fields))
    write_note(vault, "10 Projects/Observation.md", _canonical_yaml(observation_fields))

    report = ValidateVault(FileSystemVaultReader(vault)).execute()

    assert report.error_count == 0
    assert tuple(str(item.id) for item in report.goal_progress_definitions) == (DEFINITION_ID,)
    assert tuple(str(item.id) for item in report.goal_progress_observations) == (OBSERVATION_ID,)


def test_report_rejects_observation_with_definition_drift_without_leaking_body_or_path(
    tmp_path: Path,
) -> None:
    vault = create_vault(tmp_path / "vault")
    write_note(vault, "10 Projects/Definition.md", _canonical_yaml(_definition_fields()))
    changed = _observation_fields(definition_fingerprint="sha256:" + "f" * 64)
    private_body = "Секретная личная информация, которую нельзя вернуть в diagnostic"
    write_note(
        vault,
        "10 Projects/Private Observation.md",
        _canonical_yaml(changed).replace("# Reviewed companion record", private_body),
    )

    report = ValidateVault(FileSystemVaultReader(vault)).execute()
    messages = " ".join(item.message for item in report.diagnostics)

    assert report.error_count >= 1
    assert "GOAL_PROGRESS_OBSERVATION_INVALID" in {item.code for item in report.diagnostics}
    assert private_body not in messages
    assert str(vault) not in messages
