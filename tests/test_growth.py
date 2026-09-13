"""Focused deterministic tests for the Stage 11A Growth Goal core."""

from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

import pytest

from second_brain.adapters.vault import FileSystemVaultReader
from second_brain.application.decision_journal import (
    render_decision_journal_body,
    render_outcome_observation_body,
)
from second_brain.application.growth import (
    GROWTH_MAX_RESULT_BYTES,
    GROWTH_MAX_RESULTS,
    GROWTH_POLICY_FINGERPRINT,
    BuildGrowthGoalContext,
    GrowthEngineRequestV1,
    GrowthErrorCode,
    GrowthGoalContextV1,
    GrowthGoalIdentityV1,
    GrowthGoalMissingError,
    GrowthGoalSelectionModeV1,
    GrowthGoalSelectionV1,
    GrowthGoalSourceUnavailableError,
    GrowthInvalidRequestError,
    GrowthPolicyMismatchError,
    GrowthResultTooLargeError,
    canonical_growth_json,
    growth_hash_json,
    growth_hash_text,
)
from second_brain.application.reports import VaultSnapshot
from second_brain.application.self_model import (
    DEFAULT_SELF_MODEL_POLICY,
    SelfModelPolicy,
    validate_self_model_policy,
)
from second_brain.domain.models import SelfKind
from tests.conftest import create_vault, write_note

GENERATED_AT = datetime(2026, 9, 6, 10, 0, tzinfo=UTC)
GOAL_A_ID = "0198f4c5-6a00-7000-8000-000000000101"
GOAL_B_ID = "0198f4c5-6a00-7000-8000-000000000102"
GOAL_C_ID = "0198f4c5-6a00-7000-8000-000000000103"
PREFERENCE_ID = "0198f4c5-6a00-7000-8000-000000000104"
BELIEF_ID = "0198f4c5-6a00-7000-8000-000000000105"
MEMORY_ID = "0198f4c5-6a00-7000-8000-000000000106"
DECISION_ID = "0198f4c5-6a00-7000-8000-000000000107"
OUTCOME_ID = "0198f4c5-6a00-7000-8000-000000000108"
MISSING_ID = "0198f4c5-6a00-7000-8000-000000000199"
DEFAULT_REQUEST = GrowthEngineRequestV1()


class _SnapshotReader:
    def __init__(self, snapshot: VaultSnapshot) -> None:
        self.snapshot = snapshot
        self.calls = 0

    def scan(self) -> VaultSnapshot:
        self.calls += 1
        return self.snapshot


class _FailingReader:
    def __init__(self) -> None:
        self.calls = 0

    def scan(self) -> VaultSnapshot:
        self.calls += 1
        raise AssertionError("the reader must not be called")


def _note(
    note_id: str,
    *,
    evidence_kind: str = "user_statement",
    self_kind: str = "goal",
    evidence_at: str = "2026-09-05T12:00:00Z",
    precision: str = "exact",
    created: str = "2026-09-05T18:00:00+03:00",
    updated: str | None = None,
    domain: str | None = None,
    body: str = "Хочу закончить важный проект.",
    decision_id: str | None = None,
) -> str:
    fields = [
        f"id: {note_id}",
        "type: zettel",
        f"created: {created}",
        "tags: []",
        "second_brain_personal_memory: 1",
        f"evidence_kind: {evidence_kind}",
        f"self_kind: {self_kind}",
        f'evidence_at: "{evidence_at}"' if evidence_at != "unknown" else "evidence_at: unknown",
        f"evidence_at_precision: {precision}",
    ]
    if updated is not None:
        fields.insert(3, f"updated: {updated}")
    if domain is not None:
        fields.append(f"domain: {domain}")
    if decision_id is not None:
        fields.append(f"decision_id: {decision_id}")
    return "---\n" + "\n".join(fields) + f"\n---\n{body}"


def _build(
    vault: Path,
    *,
    request: GrowthEngineRequestV1 = DEFAULT_REQUEST,
    policy: SelfModelPolicy = DEFAULT_SELF_MODEL_POLICY,
) -> GrowthGoalContextV1:
    return BuildGrowthGoalContext(
        FileSystemVaultReader(vault),
        policy=policy,
        clock=lambda: GENERATED_AT,
    ).execute(request)


def _build_from_snapshot(
    snapshot: VaultSnapshot,
    *,
    request: GrowthEngineRequestV1 = DEFAULT_REQUEST,
    policy: SelfModelPolicy = DEFAULT_SELF_MODEL_POLICY,
) -> GrowthGoalContextV1:
    return BuildGrowthGoalContext(
        _SnapshotReader(snapshot),
        policy=policy,
        clock=lambda: GENERATED_AT,
    ).execute(request)


def _hash_json(value: object) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(payload).hexdigest()


def test_direct_goal_eligibility_and_non_goal_sources(tmp_path: Path) -> None:
    vault = create_vault(tmp_path / "vault")
    write_note(
        vault,
        "10 Projects/GoalUser.md",
        _note(GOAL_A_ID, evidence_kind="user_statement", body="Цель пользователя."),
    )
    write_note(
        vault,
        "10 Projects/GoalFact.md",
        _note(
            GOAL_B_ID,
            evidence_kind="explicit_user_fact",
            body="Явно подтверждённая цель.",
        ),
    )
    write_note(
        vault,
        "10 Projects/Preference.md",
        _note(PREFERENCE_ID, self_kind="preference", body="Предпочитаю короткие циклы."),
    )
    write_note(
        vault,
        "10 Projects/Belief.md",
        _note(BELIEF_ID, self_kind="belief", body="Небольшие шаги надёжнее."),
    )
    write_note(
        vault,
        "10 Projects/Memory.md",
        _note(MEMORY_ID, self_kind="memory", body="Контекстная память."),
    )
    write_note(
        vault,
        "10 Projects/Legacy.md",
        _note(
            MISSING_ID,
            self_kind="goal",
            body="У legacy note нет enrollment marker.",
        ).replace("second_brain_personal_memory: 1\n", ""),
    )
    write_note(
        vault,
        "10 Projects/Decision.md",
        _note(
            DECISION_ID,
            evidence_kind="observed_decision",
            self_kind="decision",
            domain="work",
            body=render_decision_journal_body(
                situation="Выбрать направление",
                available_options=("Первый вариант", "Второй вариант"),
                information_known_at_decision_time="Известны ограничения.",
                criteria=("Скорость",),
                chosen_option="Второй вариант",
                reasons="Он лучше подходит.",
                confidence="Средняя",
                expected_result="Задача будет завершена.",
            ),
        ),
    )
    write_note(
        vault,
        "10 Projects/Outcome.md",
        _note(
            OUTCOME_ID,
            evidence_kind="outcome_later_observation",
            self_kind="outcome",
            body=render_outcome_observation_body(
                actual_result="Результат получен.",
                reassessment="",
                notes="Проверено.",
            ),
            decision_id=DECISION_ID,
        ),
    )

    context = _build(vault)

    assert context.eligible_goal_count == 2
    assert [str(goal.source_note_uuid) for goal in context.goals] == [GOAL_A_ID, GOAL_B_ID]
    assert [goal.source_evidence_kind for goal in context.goals] == [
        "user_statement",
        "explicit_user_fact",
    ]
    assert all(goal.source_self_kind == SelfKind.GOAL.value for goal in context.goals)


def test_identity_matches_normative_hash_payloads_and_has_no_body(tmp_path: Path) -> None:
    vault = create_vault(tmp_path / "vault")
    body = "Хочу завершить проект 🧠."
    write_note(
        vault,
        "10 Projects/Goal.md",
        _note(GOAL_A_ID, domain="work", body=body),
    )

    context = _build(vault)
    identity = context.goals[0]
    assert isinstance(identity, GrowthGoalIdentityV1)
    assert identity.as_dict() == {
        "source_note_uuid": GOAL_A_ID,
        "dimension": "goal",
        "source_evidence_kind": "user_statement",
        "source_self_kind": "goal",
        "domain": "work",
        "evidence_at": "2026-09-05T12:00:00Z",
        "evidence_at_precision": "exact",
        "source_contract_version": "self-model-v1",
        "source_derivation_version": "self-model-derivation-v1",
        "self_model_policy_fingerprint": validate_self_model_policy(DEFAULT_SELF_MODEL_POLICY),
        "source_fingerprint": identity.source_fingerprint,
        "claim_fingerprint": identity.claim_fingerprint,
    }

    claim_text_fingerprint = growth_hash_text(body)
    claim_payload = {
        "claim_text_fingerprint": claim_text_fingerprint,
        "dimension": "goal",
        "domain": "work",
        "source_contract_version": "self-model-v1",
        "source_derivation_version": "self-model-derivation-v1",
        "source_note_uuid": GOAL_A_ID,
    }
    expected_claim_fingerprint = _hash_json(claim_payload)
    source_payload = {
        "claim_fingerprint": expected_claim_fingerprint,
        "domain": "work",
        "evidence_at": "2026-09-05T12:00:00Z",
        "evidence_at_precision": "exact",
        "evidence_kind": "user_statement",
        "self_kind": "goal",
        "source_contract_version": "self-model-v1",
        "source_note_uuid": GOAL_A_ID,
    }
    assert identity.claim_fingerprint == expected_claim_fingerprint
    assert identity.source_fingerprint == _hash_json(source_payload)
    assert identity.claim_fingerprint == growth_hash_json(claim_payload)
    assert identity.self_model_policy_fingerprint == validate_self_model_policy(
        DEFAULT_SELF_MODEL_POLICY
    )

    serialized = context.to_json()
    assert serialized == canonical_growth_json(json.loads(serialized))
    assert body not in serialized
    assert "10 Projects/Goal.md" not in serialized
    assert "second_brain_personal_memory" not in serialized
    assert "Хочу завершить проект" not in repr(context)


def test_exact_and_unknown_evidence_time_are_preserved_without_fallback(tmp_path: Path) -> None:
    vault = create_vault(tmp_path / "vault")
    write_note(
        vault,
        "10 Projects/Exact.md",
        _note(
            GOAL_A_ID,
            evidence_at="2026-09-05T15:00:00+03:00",
            created="2099-01-01T00:00:00+00:00",
            updated="2099-01-02T00:00:00+00:00",
            body="Точная дата цели.",
        ),
    )
    write_note(
        vault,
        "10 Projects/Unknown.md",
        _note(
            GOAL_B_ID,
            evidence_at="unknown",
            precision="unknown",
            created="2000-01-01T00:00:00+00:00",
            updated="2000-01-02T00:00:00+00:00",
            body="Дата цели неизвестна.",
        ),
    )

    context = _build(vault)
    exact, unknown = context.goals
    assert exact.evidence_at == datetime(2026, 9, 5, 12, tzinfo=UTC)
    assert exact.evidence_at_precision == "exact"
    assert unknown.evidence_at == "unknown"
    assert unknown.evidence_at_precision == "unknown"
    assert "goal_evidence_time_unknown" in context.caveats
    assert "2099" not in context.to_json()
    assert "2000" not in context.to_json()
    assert GENERATED_AT.isoformat() not in context.to_json()


def test_each_current_goal_is_deterministic_and_does_not_deduplicate(tmp_path: Path) -> None:
    vault = create_vault(tmp_path / "vault")
    for note_id, evidence_at in (
        (GOAL_C_ID, "2030-01-01T00:00:00Z"),
        (GOAL_A_ID, "2020-01-01T00:00:00Z"),
        (GOAL_B_ID, "2025-01-01T00:00:00Z"),
    ):
        write_note(
            vault,
            f"10 Projects/{note_id}.md",
            _note(
                note_id,
                evidence_at=evidence_at,
                created="2099-01-01T00:00:00+00:00",
                domain="same-domain",
                body="Одинаковый текст цели.",
            ),
        )

    context = _build(vault)

    assert context.selection_mode is GrowthGoalSelectionModeV1.EACH_CURRENT_GOAL
    assert context.selected_goal_source_uuid is None
    assert context.eligible_goal_count == 3
    assert [str(goal.source_note_uuid) for goal in context.goals] == [
        GOAL_A_ID,
        GOAL_B_ID,
        GOAL_C_ID,
    ]
    assert len({goal.claim_fingerprint for goal in context.goals}) == 3


def test_selected_goal_re_resolves_exact_uuid_without_fallback(tmp_path: Path) -> None:
    vault = create_vault(tmp_path / "vault")
    write_note(vault, "10 Projects/Goal.md", _note(GOAL_A_ID, body="Первая цель."))
    write_note(
        vault,
        "10 Projects/Preference.md",
        _note(PREFERENCE_ID, self_kind="preference", body="Похожая запись."),
    )
    request = GrowthEngineRequestV1(
        selection=GrowthGoalSelectionV1(
            GrowthGoalSelectionModeV1.SELECTED_GOAL,
            UUID(GOAL_A_ID),
        ),
        max_results=1,
    )

    context = _build(vault, request=request)

    assert context.selection_mode is GrowthGoalSelectionModeV1.SELECTED_GOAL
    assert context.selected_goal_source_uuid == context.goals[0].source_note_uuid
    assert str(context.goals[0].source_note_uuid) == GOAL_A_ID
    assert context.eligible_goal_count == 1

    missing_request = replace(
        request,
        selection=GrowthGoalSelectionV1(
            GrowthGoalSelectionModeV1.SELECTED_GOAL,
            UUID(MISSING_ID),
        ),
    )
    with pytest.raises(GrowthGoalMissingError) as error:
        _build(vault, request=missing_request)
    assert error.value.code == GrowthErrorCode.GOAL_MISSING.value

    wrong_dimension_request = replace(
        request,
        selection=GrowthGoalSelectionV1(
            GrowthGoalSelectionModeV1.SELECTED_GOAL,
            UUID(PREFERENCE_ID),
        ),
    )
    with pytest.raises(GrowthGoalMissingError):
        _build(vault, request=wrong_dimension_request)


def test_zero_goals_is_a_valid_empty_each_current_context(tmp_path: Path) -> None:
    vault = create_vault(tmp_path / "vault")

    context = _build(vault)

    assert context == GrowthGoalContextV1(
        contract_version="growth-engine-v1",
        derivation_version="growth-engine-derivation-v1",
        policy_id="growth-engine-explicit-relation-v1",
        policy_fingerprint=GROWTH_POLICY_FINGERPRINT,
        selection_mode=GrowthGoalSelectionModeV1.EACH_CURRENT_GOAL,
        selected_goal_source_uuid=None,
        eligible_goal_count=0,
        goals=(),
        reason_codes=(),
        caveats=("current_goal_revalidated",),
    )


def test_invalid_enrolled_source_and_duplicate_uuid_fail_closed(tmp_path: Path) -> None:
    vault = create_vault(tmp_path / "vault")
    write_note(vault, "10 Projects/Valid.md", _note(GOAL_A_ID, body="valid"))
    write_note(
        vault,
        "10 Projects/Invalid.md",
        _note(
            GOAL_B_ID,
            evidence_at="unknown",
            precision="exact",
            body="invalid metadata",
        ),
    )

    with pytest.raises(GrowthGoalSourceUnavailableError) as error:
        _build(vault)
    assert error.value.code == GrowthErrorCode.GOAL_SOURCE_UNAVAILABLE.value
    assert "invalid metadata" not in str(error.value)
    assert "10 Projects/Invalid.md" not in str(error.value)

    invalid_path = vault / "10 Projects/Invalid.md"
    invalid_path.unlink()
    write_note(vault, "10 Projects/One.md", _note(GOAL_A_ID, body="one"))
    write_note(vault, "10 Projects/Two.md", _note(GOAL_A_ID, body="two"))
    with pytest.raises(GrowthGoalSourceUnavailableError):
        _build(vault)


def test_rebuild_changes_only_the_bindings_that_contracts_require(tmp_path: Path) -> None:
    vault = create_vault(tmp_path / "vault")
    goal_path = write_note(
        vault,
        "10 Projects/Goal.md",
        _note(
            GOAL_A_ID,
            domain="work",
            body="Исходный текст.",
            created="2020-01-01T00:00:00+00:00",
        ),
    )
    first = _build(vault).goals[0]

    goal_path.write_text(
        _note(
            GOAL_A_ID,
            domain="work",
            body="Изменённый текст.",
            created="2099-01-01T00:00:00+00:00",
        ),
        encoding="utf-8",
    )
    body_changed = _build(vault).goals[0]
    assert body_changed.claim_fingerprint != first.claim_fingerprint
    assert body_changed.source_fingerprint != first.source_fingerprint

    goal_path.write_text(
        _note(
            GOAL_A_ID,
            domain="life",
            body="Изменённый текст.",
            evidence_at="2026-01-01T00:00:00Z",
            created="2001-01-01T00:00:00+00:00",
        ),
        encoding="utf-8",
    )
    metadata_changed = _build(vault).goals[0]
    assert metadata_changed.claim_fingerprint != body_changed.claim_fingerprint
    assert metadata_changed.source_fingerprint != body_changed.source_fingerprint

    goal_path.write_text(
        _note(
            GOAL_A_ID,
            domain="life",
            body="Изменённый текст.",
            evidence_at="2027-01-01T00:00:00Z",
            created="2002-01-01T00:00:00+00:00",
        ),
        encoding="utf-8",
    )
    time_changed = _build(vault).goals[0]
    assert time_changed.claim_fingerprint == metadata_changed.claim_fingerprint
    assert time_changed.source_fingerprint != metadata_changed.source_fingerprint

    goal_path.write_text(
        _note(
            GOAL_A_ID,
            domain="life",
            body="Изменённый текст.",
            evidence_at="2027-01-01T00:00:00Z",
            created="2003-01-01T00:00:00+00:00",
        ),
        encoding="utf-8",
    )
    goal_path.rename(vault / "10 Projects/Renamed.md")
    storage_only = _build(vault).goals[0]
    assert storage_only == time_changed


def test_deleted_selected_goal_is_missing_and_each_rebuilds_empty(tmp_path: Path) -> None:
    vault = create_vault(tmp_path / "vault")
    path = write_note(vault, "10 Projects/Goal.md", _note(GOAL_A_ID))
    request = GrowthEngineRequestV1(
        selection=GrowthGoalSelectionV1(
            GrowthGoalSelectionModeV1.SELECTED_GOAL,
            UUID(GOAL_A_ID),
        ),
        max_results=1,
    )
    assert _build(vault, request=request).eligible_goal_count == 1

    path.unlink()
    with pytest.raises(GrowthGoalMissingError):
        _build(vault, request=request)
    empty = _build(vault)
    assert empty.eligible_goal_count == 0
    assert empty.goals == ()


def test_request_validation_is_strict_and_precedes_vault_read() -> None:
    reader = _FailingReader()
    builder = BuildGrowthGoalContext(reader, DEFAULT_SELF_MODEL_POLICY, lambda: GENERATED_AT)

    invalid_requests: list[object] = [
        object(),
        {"contract_version": "growth-engine-v1"},
    ]
    for max_results in (0, GROWTH_MAX_RESULTS + 1, True):
        candidate = GrowthEngineRequestV1()
        object.__setattr__(candidate, "max_results", max_results)
        invalid_requests.append(candidate)
    for max_result_bytes in (0, GROWTH_MAX_RESULT_BYTES + 1, False):
        candidate = GrowthEngineRequestV1()
        object.__setattr__(candidate, "max_result_bytes", max_result_bytes)
        invalid_requests.append(candidate)

    invalid_contract = GrowthEngineRequestV1()
    object.__setattr__(invalid_contract, "contract_version", "growth-engine-v0")
    invalid_requests.append(invalid_contract)

    for invalid_candidate in invalid_requests:
        with pytest.raises(GrowthInvalidRequestError) as error:
            builder.execute(invalid_candidate)  # type: ignore[arg-type]
        assert error.value.code == GrowthErrorCode.INVALID_REQUEST.value
        assert error.value.message == "growth request failed validation"

    assert reader.calls == 0


def test_request_from_dict_rejects_unknown_fields_and_invalid_bounds_safely() -> None:
    with pytest.raises(GrowthInvalidRequestError):
        GrowthEngineRequestV1.from_dict(
            {
                "contract_version": "growth-engine-v1",
                "selection": {"mode": "each_current_goal", "source_note_uuid": None},
                "max_results": 1,
                "max_result_bytes": GROWTH_MAX_RESULT_BYTES,
                "body": "private",
            }
        )
    with pytest.raises(GrowthInvalidRequestError):
        GrowthEngineRequestV1.from_dict(
            {
                "contract_version": "growth-engine-v1",
                "selection": {"mode": "each_current_goal", "source_note_uuid": None},
                "max_results": True,
                "max_result_bytes": GROWTH_MAX_RESULT_BYTES,
            }
        )


def test_valid_lower_and_upper_bounds_are_enforced_without_truncation(tmp_path: Path) -> None:
    vault = create_vault(tmp_path / "vault")
    write_note(vault, "10 Projects/Goal.md", _note(GOAL_A_ID, body="bounded"))

    one_result = GrowthEngineRequestV1(
        selection=GrowthGoalSelectionV1(
            GrowthGoalSelectionModeV1.SELECTED_GOAL,
            UUID(GOAL_A_ID),
        ),
        max_results=1,
        max_result_bytes=1,
    )
    with pytest.raises(GrowthResultTooLargeError) as error:
        _build(vault, request=one_result)
    assert error.value.code == GrowthErrorCode.RESULT_TOO_LARGE.value
    assert str(error.value) == "the Growth result exceeds its bounded limit"

    max_request = GrowthEngineRequestV1(max_results=GROWTH_MAX_RESULTS)
    assert _build(vault, request=max_request).eligible_goal_count == 1

    write_note(vault, "10 Projects/Goal2.md", _note(GOAL_B_ID, body="second"))
    too_few_results = GrowthEngineRequestV1(max_results=1)
    with pytest.raises(GrowthResultTooLargeError):
        _build(vault, request=too_few_results)


def test_policy_and_source_failures_are_fixed_and_do_not_leak_details(tmp_path: Path) -> None:
    vault = create_vault(tmp_path / "vault")
    snapshot = FileSystemVaultReader(vault).scan()
    reader = _SnapshotReader(snapshot)
    invalid_policy = replace(DEFAULT_SELF_MODEL_POLICY, confidence_policy="changed-v1")
    with pytest.raises(GrowthPolicyMismatchError) as policy_error:
        BuildGrowthGoalContext(reader, invalid_policy, lambda: GENERATED_AT).execute(
            DEFAULT_REQUEST
        )
    assert policy_error.value.code == GrowthErrorCode.POLICY_MISMATCH.value
    assert reader.calls == 0

    invalid_clock_reader = _SnapshotReader(snapshot)
    with pytest.raises(GrowthInvalidRequestError) as clock_error:
        BuildGrowthGoalContext(
            invalid_clock_reader,
            DEFAULT_SELF_MODEL_POLICY,
            lambda: datetime(2026, 9, 6),
        ).execute(DEFAULT_REQUEST)
    assert clock_error.value.code == GrowthErrorCode.INVALID_REQUEST.value
    assert invalid_clock_reader.calls == 0

    unavailable = replace(snapshot, manifest=None)
    with pytest.raises(GrowthGoalSourceUnavailableError) as source_error:
        _build_from_snapshot(unavailable)
    assert source_error.value.code == GrowthErrorCode.GOAL_SOURCE_UNAVAILABLE.value
    assert source_error.value.message == "the current goal source is unavailable"
    assert "second-brain" not in str(source_error.value)


def test_policy_identity_and_canonical_json_are_stable() -> None:
    assert (
        growth_hash_json(
            {
                "advisor": "none-v1",
                "behavior": "stage10-current-exact-subject-v1",
                "comparison": "none-v1",
                "contract": "growth-engine-v1",
                "goal": "stage4-direct-goal-v1",
                "mapping": "owner-explicit-goal-choice-v1",
                "persistence": "dedicated-operational-growth-mapping-v1",
                "relation": "supports-conflicts-neutral-v1",
                "selection": "owner-selected-or-per-goal-v1",
                "temporal": "separate-times-no-backfill-v1",
                "version": "1",
            }
        )
        == GROWTH_POLICY_FINGERPRINT
    )
    assert canonical_growth_json({"я": "цель", "a": 1}) == '{"a":1,"я":"цель"}'
