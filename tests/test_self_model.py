"""Focused deterministic tests for the Stage 4 Self Model v1 core."""

from __future__ import annotations

import shutil
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
from second_brain.application.reports import VaultSnapshot
from second_brain.application.self_model import (
    CONFIDENCE_POLICY,
    DEFAULT_SELF_MODEL_POLICY,
    DERIVATION_VERSION,
    MAX_SELF_MODEL_CLAIM_BYTES,
    BuildSelfModel,
    SelfModelClaim,
    SelfModelConfidence,
    SelfModelConfidenceState,
    SelfModelDimension,
    SelfModelErrorCode,
    SelfModelEvidenceInvalidError,
    SelfModelEvidenceRef,
    SelfModelInvalidClockError,
    SelfModelInvalidRequestError,
    SelfModelPolicy,
    SelfModelPolicyUnavailableError,
    SelfModelRequest,
    SelfModelResult,
    SelfModelResultInvalidError,
    SelfModelResultTooLargeError,
    SelfModelTemporalContext,
    SelfModelVaultUnavailableError,
    validate_self_model_claim,
    validate_self_model_result,
)
from second_brain.domain.models import (
    EvidenceAtPrecision,
    EvidenceKind,
    MarkdownDocument,
    SelfKind,
)
from tests.conftest import create_vault, snapshot_tree, write_note

GENERATED_AT = datetime(2026, 9, 6, 10, 0, tzinfo=UTC)
PREFERENCE_ID = "0198f4c5-6a00-7000-8000-000000000013"
BELIEF_ID = "0198f4c5-6a00-7000-8000-000000000014"
GOAL_ID = "0198f4c5-6a00-7000-8000-000000000015"
MEMORY_ID = "0198f4c5-6a00-7000-8000-000000000016"
DECISION_ID = "0198f4c5-6a00-7000-8000-000000000017"
OUTCOME_ID = "0198f4c5-6a00-7000-8000-000000000018"


class _SnapshotReader:
    def __init__(self, snapshot: VaultSnapshot) -> None:
        self.snapshot = snapshot
        self.calls = 0

    def scan(self) -> VaultSnapshot:
        self.calls += 1
        return self.snapshot


def _note(
    note_id: str,
    *,
    evidence_kind: str = "user_statement",
    self_kind: str = "preference",
    evidence_at: str = "2026-09-05T12:00:00Z",
    precision: str = "exact",
    created: str = "2026-09-05T18:00:00+03:00",
    updated: str | None = None,
    domain: str | None = None,
    body: str = "Пользовательская assertion.",
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
    policy: SelfModelPolicy = DEFAULT_SELF_MODEL_POLICY,
) -> SelfModelResult:
    return BuildSelfModel(
        FileSystemVaultReader(vault),
        policy=policy,
        clock=lambda: GENERATED_AT,
    ).execute(SelfModelRequest())


def _build_from_snapshot(
    snapshot: VaultSnapshot,
    *,
    policy: SelfModelPolicy = DEFAULT_SELF_MODEL_POLICY,
) -> SelfModelResult:
    return BuildSelfModel(
        _SnapshotReader(snapshot),
        policy=policy,
        clock=lambda: GENERATED_AT,
    ).execute(SelfModelRequest())


def _document(
    note_id: str,
    *,
    body: str,
    self_kind: str = "preference",
    evidence_at: str = "2026-09-05T12:00:00Z",
    precision: str = "exact",
) -> MarkdownDocument:
    front_matter = {
        "id": note_id,
        "type": "zettel",
        "created": "2026-09-05T18:00:00+03:00",
        "tags": [],
        "second_brain_personal_memory": 1,
        "evidence_kind": "user_statement",
        "self_kind": self_kind,
        "evidence_at": evidence_at,
        "evidence_at_precision": precision,
    }
    return MarkdownDocument(
        relative_path=f"10 Projects/{note_id}.md",
        front_matter=front_matter,
        body=body,
        in_inbox=False,
    )


def _snapshot_with_documents(vault: Path, *documents: MarkdownDocument) -> VaultSnapshot:
    base = FileSystemVaultReader(vault).scan()
    return replace(base, documents=documents, diagnostics=())


def _decision_body() -> str:
    return render_decision_journal_body(
        situation="Выбрать направление работы",
        available_options=("Первый вариант", "Второй вариант"),
        information_known_at_decision_time="Известны ограничения и срок.",
        criteria=("Скорость",),
        chosen_option="Второй вариант",
        reasons="Он лучше соответствует ограничению по времени.",
        confidence="Средняя",
        expected_result="Получится закончить быстрее.",
    )


def _outcome_body() -> str:
    return render_outcome_observation_body(
        actual_result="Результат оказался положительным.",
        reassessment="",
        notes="Проверено пользователем.",
    )


def test_direct_assertions_only_emit_one_explainable_claim_each(tmp_path: Path) -> None:
    vault = create_vault(tmp_path / "vault")
    write_note(
        vault,
        "10 Projects/Preference.md",
        _note(PREFERENCE_ID, self_kind="preference", domain="work", body="Люблю короткие циклы."),
    )
    write_note(
        vault,
        "10 Projects/Belief.md",
        _note(BELIEF_ID, self_kind="belief", body="Небольшие шаги надёжнее."),
    )
    write_note(
        vault,
        "10 Projects/Goal.md",
        _note(GOAL_ID, self_kind="goal", body="Хочу закончить проект."),
    )
    write_note(
        vault,
        "10 Projects/Memory.md",
        _note(MEMORY_ID, self_kind="memory", body="Контекстная память."),
    )
    write_note(vault, "30 Resources/Ordinary.md", "Обычная заметка без enrollment marker.")

    result = _build(vault)

    assert [claim.dimension for claim in result.claims] == [
        SelfModelDimension.BELIEF,
        SelfModelDimension.GOAL,
        SelfModelDimension.PREFERENCE,
    ]
    assert [claim.claim for claim in result.claims] == [
        "Небольшие шаги надёжнее.",
        "Хочу закончить проект.",
        "Люблю короткие циклы.",
    ]
    assert result.eligible_evidence_count == 4
    assert result.represented_evidence_count == 3
    for claim in result.claims:
        assert len(claim.supporting_evidence) == 1
        assert claim.contradicting_evidence == ()
        assert claim.contextual_evidence == ()
        assert claim.status is None
        assert claim.generated_at == GENERATED_AT
        assert claim.derivation_version == DERIVATION_VERSION
        assert claim.confidence == SelfModelConfidence(
            state=SelfModelConfidenceState.NOT_ASSESSED,
            score=None,
            policy_version=CONFIDENCE_POLICY,
            supporting_evidence_count=1,
            contradicting_evidence_count=0,
            unknown_time_count=0,
        )
        assert claim.temporal_context.known_evidence_count == 1
        assert claim.temporal_context.unknown_evidence_count == 0


def test_body_projection_normalizes_line_endings_without_semantic_cleanup(tmp_path: Path) -> None:
    vault = create_vault(tmp_path / "vault")
    snapshot = _snapshot_with_documents(
        vault,
        _document(PREFERENCE_ID, body="  первая\r\n\tвторая\rтретья  "),
    )

    result = _build_from_snapshot(snapshot)

    assert result.claims[0].claim == "  первая\n\tвторая\nтретья  "


@pytest.mark.parametrize(
    "body",
    ["текст\x00с control", "текст\x7fс DEL"],
)
def test_disallowed_control_characters_fail_closed(tmp_path: Path, body: str) -> None:
    vault = create_vault(tmp_path / "vault")
    snapshot = _snapshot_with_documents(vault, _document(PREFERENCE_ID, body=body))

    with pytest.raises(SelfModelEvidenceInvalidError) as error:
        _build_from_snapshot(snapshot)

    assert error.value.code == SelfModelErrorCode.EVIDENCE_INVALID.value
    assert error.value.message == "self model evidence is invalid"
    assert "control" not in str(error.value)


def test_oversized_claim_is_rejected_without_truncation(tmp_path: Path) -> None:
    vault = create_vault(tmp_path / "vault")
    body = "🧠" * ((MAX_SELF_MODEL_CLAIM_BYTES // 4) + 1)
    snapshot = _snapshot_with_documents(vault, _document(PREFERENCE_ID, body=body))

    with pytest.raises(SelfModelResultTooLargeError) as error:
        _build_from_snapshot(snapshot)

    assert error.value.code == SelfModelErrorCode.RESULT_TOO_LARGE.value
    assert body not in str(error.value)


def test_claim_limit_rejects_the_complete_result_without_partial_output(tmp_path: Path) -> None:
    vault = create_vault(tmp_path / "vault")
    write_note(vault, "10 Projects/One.md", _note(PREFERENCE_ID, body="one"))
    write_note(vault, "10 Projects/Two.md", _note(BELIEF_ID, body="two", self_kind="belief"))

    with pytest.raises(SelfModelResultTooLargeError) as error:
        BuildSelfModel(
            FileSystemVaultReader(vault),
            policy=DEFAULT_SELF_MODEL_POLICY,
            clock=lambda: GENERATED_AT,
        ).execute(SelfModelRequest(max_claims=1))

    assert error.value.code == SelfModelErrorCode.RESULT_TOO_LARGE.value


def test_missing_manifest_fails_closed_as_vault_unavailable(tmp_path: Path) -> None:
    vault = create_vault(tmp_path / "vault")
    snapshot = replace(FileSystemVaultReader(vault).scan(), manifest=None)

    with pytest.raises(SelfModelVaultUnavailableError) as error:
        _build_from_snapshot(snapshot)

    assert error.value.code == SelfModelErrorCode.VAULT_UNAVAILABLE.value


def test_request_boundary_is_strict_and_precedes_vault_scan(tmp_path: Path) -> None:
    vault = create_vault(tmp_path / "vault")
    reader = _SnapshotReader(FileSystemVaultReader(vault).scan())
    builder = BuildSelfModel(reader, DEFAULT_SELF_MODEL_POLICY, lambda: GENERATED_AT)

    requests: tuple[object, ...] = (
        object(),
        SelfModelRequest(max_claims=0),
        SelfModelRequest(max_claims=201),
        SelfModelRequest(max_claims=True),
        SelfModelRequest(max_evidence_refs_per_claim=False),
    )
    for request in requests:
        with pytest.raises(SelfModelInvalidRequestError) as error:
            builder.execute(request)  # type: ignore[arg-type]
        assert error.value.code == SelfModelErrorCode.INVALID_REQUEST.value

    assert reader.calls == 0


def test_invalid_clock_and_policy_fail_before_scan(tmp_path: Path) -> None:
    vault = create_vault(tmp_path / "vault")
    reader = _SnapshotReader(FileSystemVaultReader(vault).scan())
    with pytest.raises(SelfModelInvalidClockError):
        BuildSelfModel(reader, DEFAULT_SELF_MODEL_POLICY, lambda: datetime(2026, 9, 6)).execute(
            SelfModelRequest()
        )
    assert reader.calls == 0

    invalid_policy = replace(DEFAULT_SELF_MODEL_POLICY, confidence_policy="new-policy")
    with pytest.raises(SelfModelPolicyUnavailableError) as error:
        BuildSelfModel(reader, invalid_policy, lambda: GENERATED_AT).execute(SelfModelRequest())
    assert error.value.code == SelfModelErrorCode.POLICY_UNAVAILABLE.value
    assert reader.calls == 0


def test_marker_is_exact_and_coincidental_fields_remain_ordinary(tmp_path: Path) -> None:
    vault = create_vault(tmp_path / "vault")
    for index, marker in enumerate(("true", '"1"', "1.0", None), start=1):
        text = _note(
            f"0198f4c5-6a00-7000-8000-0000000000{20 + index}",
            self_kind="preference",
            body="Не evidence без exact marker.",
        )
        if marker is None:
            text = text.replace("second_brain_personal_memory: 1\n", "")
        else:
            text = text.replace(
                "second_brain_personal_memory: 1", f"second_brain_personal_memory: {marker}"
            )
        write_note(vault, f"30 Resources/Ordinary{index}.md", text)

    result = _build(vault)

    assert result.claims == ()
    assert result.eligible_evidence_count == 0
    assert result.represented_evidence_count == 0


def test_stage2_evidence_is_eligible_but_never_an_implicit_claim(tmp_path: Path) -> None:
    vault = create_vault(tmp_path / "vault")
    write_note(
        vault,
        "10 Projects/Decision.md",
        _note(
            DECISION_ID,
            evidence_kind="observed_decision",
            self_kind="decision",
            domain="work",
            body=_decision_body(),
        ),
    )
    write_note(
        vault,
        "10 Projects/Outcome.md",
        _note(
            OUTCOME_ID,
            evidence_kind="outcome_later_observation",
            self_kind="outcome",
            body=_outcome_body(),
            decision_id=DECISION_ID,
        ),
    )

    result = _build(vault)

    assert result.claims == ()
    assert result.eligible_evidence_count == 2
    assert result.represented_evidence_count == 0


def test_invalid_enrolled_metadata_fails_closed_without_partial_claims(tmp_path: Path) -> None:
    vault = create_vault(tmp_path / "vault")
    write_note(vault, "10 Projects/Valid.md", _note(PREFERENCE_ID, body="valid"))
    write_note(
        vault,
        "10 Projects/Invalid.md",
        _note(BELIEF_ID, body="invalid").replace("self_kind: preference\n", ""),
    )

    with pytest.raises(SelfModelEvidenceInvalidError) as error:
        _build(vault)

    assert error.value.code == SelfModelErrorCode.EVIDENCE_INVALID.value


def test_duplicate_uuid_and_broken_outcome_relation_fail_closed(tmp_path: Path) -> None:
    vault = create_vault(tmp_path / "vault")
    duplicate = _note(PREFERENCE_ID, body="one")
    write_note(vault, "10 Projects/One.md", duplicate)
    write_note(vault, "10 Projects/Two.md", duplicate)
    with pytest.raises(SelfModelEvidenceInvalidError):
        _build(vault)

    shutil.rmtree(vault / "10 Projects")
    (vault / "10 Projects").mkdir()
    write_note(
        vault,
        "10 Projects/Outcome.md",
        _note(
            OUTCOME_ID,
            evidence_kind="outcome_later_observation",
            self_kind="outcome",
            body=_outcome_body(),
            decision_id=DECISION_ID,
        ),
    )
    with pytest.raises(SelfModelEvidenceInvalidError):
        _build(vault)


def test_temporal_context_preserves_known_and_unknown_without_created_fallback(
    tmp_path: Path,
) -> None:
    vault = create_vault(tmp_path / "vault")
    write_note(
        vault,
        "10 Projects/Known.md",
        _note(
            PREFERENCE_ID,
            evidence_at="2026-09-05T12:00:00Z",
            created="2099-01-01T00:00:00+00:00",
            body="known",
        ),
    )
    write_note(
        vault,
        "10 Projects/Unknown.md",
        _note(
            BELIEF_ID,
            evidence_at="unknown",
            precision="unknown",
            created="2000-01-01T00:00:00+00:00",
            body="unknown",
        ),
    )

    result = _build(vault)
    known, unknown = result.claims
    assert known.supporting_evidence[0].evidence_at == datetime(2026, 9, 5, 12, tzinfo=UTC)
    assert unknown.supporting_evidence[0].evidence_at == "unknown"
    assert unknown.temporal_context == SelfModelTemporalContext(None, None, 0, 1)
    assert unknown.confidence.unknown_time_count == 1

    first = result
    known_path = vault / "10 Projects/Known.md"
    known_path.write_text(
        _note(
            PREFERENCE_ID,
            evidence_at="2026-09-05T12:00:00Z",
            created="2001-01-01T00:00:00+00:00",
            body="known",
        ),
        encoding="utf-8",
    )
    second = _build(vault)
    assert first == second


def test_competing_assertions_remain_separate_without_contradiction_or_newer_wins(
    tmp_path: Path,
) -> None:
    vault = create_vault(tmp_path / "vault")
    write_note(
        vault,
        "10 Projects/Older.md",
        _note(
            PREFERENCE_ID,
            evidence_at="2020-01-01T00:00:00Z",
            created="2020-01-02T00:00:00+00:00",
            body="Выбираю вариант A.",
        ),
    )
    write_note(
        vault,
        "10 Projects/Newer.md",
        _note(
            BELIEF_ID,
            evidence_at="2030-01-01T00:00:00Z",
            created="2030-01-02T00:00:00+00:00",
            body="Выбираю вариант B.",
        ),
    )

    result = _build(vault)

    assert len(result.claims) == 2
    assert all(claim.contradicting_evidence == () for claim in result.claims)
    assert all(claim.contextual_evidence == () for claim in result.claims)
    assert all(claim.status is None for claim in result.claims)
    assert {claim.claim for claim in result.claims} == {"Выбираю вариант A.", "Выбираю вариант B."}


def test_policy_fingerprint_is_present_and_result_is_reproducible(tmp_path: Path) -> None:
    vault = create_vault(tmp_path / "vault")
    write_note(vault, "10 Projects/Preference.md", _note(PREFERENCE_ID, body="same"))

    first = _build(vault)
    second = _build(vault)

    assert first == second
    assert len(first.policy_fingerprint) == 64
    assert first.policy_fingerprint == first.policy_fingerprint.lower()
    assert first.derivation_version == DERIVATION_VERSION


def test_result_validation_rejects_mixed_generation_times(tmp_path: Path) -> None:
    vault = create_vault(tmp_path / "vault")
    write_note(vault, "10 Projects/Preference.md", _note(PREFERENCE_ID, body="same"))
    result = _build(vault)
    mixed = replace(
        result,
        claims=(replace(result.claims[0], generated_at=datetime(2026, 9, 6, 11, tzinfo=UTC)),),
    )

    with pytest.raises(SelfModelResultInvalidError):
        validate_self_model_result(
            mixed,
            request=SelfModelRequest(),
            policy=DEFAULT_SELF_MODEL_POLICY,
            expected_policy_fingerprint=result.policy_fingerprint,
        )


def test_rebuild_and_deletion_use_current_vault_without_derived_state(tmp_path: Path) -> None:
    vault = create_vault(tmp_path / "vault")
    note_path = write_note(vault, "10 Projects/Preference.md", _note(PREFERENCE_ID, body="first"))
    before = snapshot_tree(vault)
    builder = BuildSelfModel(
        FileSystemVaultReader(vault),
        policy=DEFAULT_SELF_MODEL_POLICY,
        clock=lambda: GENERATED_AT,
    )

    first = builder.execute(SelfModelRequest())
    assert first.claims[0].claim == "first"
    assert snapshot_tree(vault) == before

    note_path.write_text(_note(PREFERENCE_ID, body="second"), encoding="utf-8")
    second = builder.execute(SelfModelRequest())
    assert second.claims[0].claim == "second"

    note_path.unlink()
    third = builder.execute(SelfModelRequest())
    assert third.claims == ()
    assert not list(vault.glob("**/*self*model*"))


def test_filesystem_enumeration_does_not_change_result_order(tmp_path: Path) -> None:
    vault = create_vault(tmp_path / "vault")
    first = _document(PREFERENCE_ID, body="first", self_kind="preference")
    second = _document(BELIEF_ID, body="second", self_kind="belief")
    left = _snapshot_with_documents(vault, first, second)
    right = _snapshot_with_documents(vault, second, first)

    assert _build_from_snapshot(left) == _build_from_snapshot(right)


def test_claim_result_validation_rejects_pairwise_role_overlap() -> None:
    ref = SelfModelEvidenceRef(
        note_id=UUID(PREFERENCE_ID),
        evidence_kind=EvidenceKind.USER_STATEMENT,
        self_kind=SelfKind.PREFERENCE,
        domain=None,
        evidence_at="unknown",
        evidence_at_precision=EvidenceAtPrecision.UNKNOWN,
    )
    base = SelfModelClaim(
        dimension=SelfModelDimension.PREFERENCE,
        claim="claim",
        domain=None,
        supporting_evidence=(ref,),
        contradicting_evidence=(),
        contextual_evidence=(),
        confidence=SelfModelConfidence(
            state=SelfModelConfidenceState.NOT_ASSESSED,
            score=None,
            policy_version=CONFIDENCE_POLICY,
            supporting_evidence_count=1,
            contradicting_evidence_count=0,
            unknown_time_count=1,
        ),
        temporal_context=SelfModelTemporalContext(None, None, 0, 1),
        generated_at=GENERATED_AT,
        derivation_version=DERIVATION_VERSION,
    )

    with pytest.raises(SelfModelResultInvalidError):
        validate_self_model_claim(replace(base, contradicting_evidence=(ref,)))
    with pytest.raises(SelfModelResultInvalidError):
        validate_self_model_claim(replace(base, contextual_evidence=(ref,)))


def test_safe_error_projection_is_bounded() -> None:
    error = SelfModelResultInvalidError()
    assert error.as_dict() == {
        "code": SelfModelErrorCode.RESULT_INVALID.value,
        "message": "self model result failed validation",
    }
