"""Deterministic tests for the approved mechanical Simulate Me v1 core."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

import pytest

from second_brain.application.self_model import (
    CONFIDENCE_POLICY,
    DEFAULT_SELF_MODEL_POLICY,
    SelfModelClaim,
    SelfModelConfidence,
    SelfModelConfidenceState,
    SelfModelDimension,
    SelfModelEvidenceRef,
    SelfModelRequest,
    SelfModelResult,
    SelfModelTemporalContext,
    validate_self_model_policy,
)
from second_brain.application.self_model import (
    DERIVATION_VERSION as SELF_MODEL_DERIVATION_VERSION,
)
from second_brain.application.simulate_me import (
    DERIVATION_VERSION,
    MAX_LABEL_BYTES,
    POLICY_FINGERPRINT,
    POLICY_ID,
    BuildSimulateMe,
    SimulateMeAbstentionCode,
    SimulateMeDimension,
    SimulateMeInvalidRequestError,
    SimulateMeOption,
    SimulateMeRequest,
    SimulateMeResultKind,
    SimulateMeTemporalCaveatCode,
    normalize_simulate_me_text,
    validate_simulate_me_policy,
)
from second_brain.domain.models import EvidenceAt, EvidenceAtPrecision, EvidenceKind, SelfKind

GENERATED_AT = datetime(2026, 9, 6, 10, 0, tzinfo=UTC)
KNOWN_AT = datetime(2026, 9, 5, 12, 0, tzinfo=UTC)


def _uuid(index: int) -> UUID:
    return UUID(f"0198f4c5-6a00-7000-8000-{index:012d}")


def _claim(
    index: int,
    text: str,
    *,
    dimension: SelfModelDimension = SelfModelDimension.PREFERENCE,
    evidence_at: EvidenceAt = KNOWN_AT,
) -> SelfModelClaim:
    kind = {
        SelfModelDimension.PREFERENCE: SelfKind.PREFERENCE,
        SelfModelDimension.GOAL: SelfKind.GOAL,
        SelfModelDimension.BELIEF: SelfKind.BELIEF,
        SelfModelDimension.DECISION_RULE: SelfKind.DECISION,
        SelfModelDimension.BEHAVIORAL_PATTERN: SelfKind.MEMORY,
    }[dimension]
    note_id = _uuid(index)
    ref = SelfModelEvidenceRef(
        note_id=note_id,
        evidence_kind=EvidenceKind.USER_STATEMENT,
        self_kind=kind,
        domain=None,
        evidence_at=evidence_at,
        evidence_at_precision=(
            EvidenceAtPrecision.UNKNOWN if evidence_at == "unknown" else EvidenceAtPrecision.EXACT
        ),
        related_note_ids=(),
    )
    return SelfModelClaim(
        dimension=dimension,
        claim=text,
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
            unknown_time_count=int(evidence_at == "unknown"),
        ),
        temporal_context=SelfModelTemporalContext(
            earliest_known_evidence_at=None if evidence_at == "unknown" else evidence_at,
            latest_known_evidence_at=None if evidence_at == "unknown" else evidence_at,
            known_evidence_count=int(evidence_at != "unknown"),
            unknown_evidence_count=int(evidence_at == "unknown"),
        ),
        generated_at=GENERATED_AT,
        derivation_version=SELF_MODEL_DERIVATION_VERSION,
    )


def _result(*claims: SelfModelClaim) -> SelfModelResult:
    ordered = tuple(
        sorted(
            claims,
            key=lambda claim: (
                claim.dimension.value,
                claim.domain or "",
                str(claim.supporting_evidence[0].note_id),
                claim.claim,
            ),
        )
    )
    return SelfModelResult(
        claims=ordered,
        eligible_evidence_count=len(ordered),
        represented_evidence_count=len(ordered),
        generated_at=GENERATED_AT,
        derivation_version=DEFAULT_SELF_MODEL_POLICY.derivation_version,
        policy_fingerprint=validate_self_model_policy(DEFAULT_SELF_MODEL_POLICY),
    )


@dataclass
class _FakeSelfModel:
    result: SelfModelResult | Exception
    calls: int = 0

    def execute(self, request: SelfModelRequest) -> SelfModelResult:
        self.calls += 1
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


def _service(*claims: SelfModelClaim) -> tuple[BuildSimulateMe, _FakeSelfModel]:
    fake = _FakeSelfModel(_result(*claims))
    return BuildSimulateMe(self_model=fake), fake


def test_policy_identity_matches_approved_contract() -> None:
    assert validate_simulate_me_policy() == POLICY_FINGERPRINT
    assert DERIVATION_VERSION == "simulate-me-v1"
    assert POLICY_ID == "simulate-me-direct-exact-v1"


@pytest.mark.parametrize(
    "candidate",
    (
        SimulateMeRequest("task", ()),
        SimulateMeRequest("task", tuple(SimulateMeOption(str(i), "x") for i in range(9))),
        SimulateMeRequest("task", (SimulateMeOption("bad id", "x"),)),
        SimulateMeRequest("task", (SimulateMeOption("x", "x"), SimulateMeOption("x", "y"))),
        SimulateMeRequest("line\nbreak", (SimulateMeOption("x", "x"),)),
    ),
)
def test_invalid_request_is_rejected_before_current_context_read(
    candidate: SimulateMeRequest,
) -> None:
    service, fake = _service(_claim(1, "x"))

    with pytest.raises(SimulateMeInvalidRequestError):
        service.execute(candidate)

    assert fake.calls == 0


def test_bounds_are_checked_after_nfc_and_edge_trim_but_original_option_is_preserved() -> None:
    assert normalize_simulate_me_text("  Café  ", MAX_LABEL_BYTES) == "Café"
    service, _fake = _service(_claim(1, "Café"))

    result = service.execute(
        SimulateMeRequest("  literal task  ", (SimulateMeOption("choice", "  Cafe\u0301  "),))
    )

    assert result.kind is SimulateMeResultKind.PREDICTION
    assert result.selected_option == SimulateMeOption("choice", "  Cafe\u0301  ")
    assert result.evidence_refs[0].dimension is SimulateMeDimension.PREFERENCE


def test_one_preference_or_goal_exact_match_predicts_without_using_query() -> None:
    service, _fake = _service(_claim(1, "Первый вариант", dimension=SelfModelDimension.GOAL))

    result = service.execute(
        SimulateMeRequest(
            "Совершенно другой literal task", (SimulateMeOption("a", "Первый вариант"),)
        )
    )

    assert result.kind is SimulateMeResultKind.PREDICTION
    assert result.selected_option == SimulateMeOption("a", "Первый вариант")
    assert result.abstention_code is None
    assert result.evidence_refs[0].claim_id == _uuid(1)


def test_no_match_is_safe_abstention() -> None:
    service, _fake = _service(_claim(1, "Другое"))

    result = service.execute(SimulateMeRequest("task", (SimulateMeOption("a", "Вариант"),)))

    assert result.kind is SimulateMeResultKind.ABSTENTION
    assert result.selected_option is None
    assert result.evidence_refs == ()
    assert result.abstention_code is SimulateMeAbstentionCode.NO_MATCHING_EVIDENCE


def test_different_supported_options_abstain_without_ranking_or_frequency() -> None:
    service, _fake = _service(_claim(1, "A"), _claim(2, "B"))

    result = service.execute(
        SimulateMeRequest(
            "task",
            (SimulateMeOption("a", "A"), SimulateMeOption("b", "B")),
        )
    )

    assert result.kind is SimulateMeResultKind.ABSTENTION
    assert result.abstention_code is SimulateMeAbstentionCode.MULTIPLE_OPTIONS_SUPPORTED
    assert [ref.claim_id for ref in result.evidence_refs] == [_uuid(1), _uuid(2)]


def test_multiple_refs_for_one_option_remain_one_distinct_selection() -> None:
    service, _fake = _service(
        _claim(2, "A", dimension=SelfModelDimension.GOAL),
        _claim(1, "A", dimension=SelfModelDimension.PREFERENCE),
    )

    result = service.execute(SimulateMeRequest("task", (SimulateMeOption("a", "A"),)))

    assert result.kind is SimulateMeResultKind.PREDICTION
    assert [ref.claim_id for ref in result.evidence_refs] == [_uuid(1), _uuid(2)]


def test_duplicate_labels_support_multiple_request_options_and_never_tie_break() -> None:
    service, _fake = _service(_claim(1, "A"))

    result = service.execute(
        SimulateMeRequest("task", (SimulateMeOption("a", "A"), SimulateMeOption("b", "A")))
    )

    assert result.kind is SimulateMeResultKind.ABSTENTION
    assert result.abstention_code is SimulateMeAbstentionCode.MULTIPLE_OPTIONS_SUPPORTED


@pytest.mark.parametrize("claim_text", ("a", "A", "prefix A", "A suffix", "A  B"))
def test_matching_is_whole_string_without_case_or_whitespace_semantics(claim_text: str) -> None:
    service, _fake = _service(_claim(1, claim_text))

    result = service.execute(SimulateMeRequest("task", (SimulateMeOption("a", "A B"),)))

    assert result.kind is SimulateMeResultKind.ABSTENTION
    assert result.abstention_code is SimulateMeAbstentionCode.NO_MATCHING_EVIDENCE


def test_belief_is_contextual_only_and_unknown_time_is_a_caveat() -> None:
    service, _fake = _service(
        _claim(1, "A", dimension=SelfModelDimension.BELIEF, evidence_at="unknown")
    )

    result = service.execute(SimulateMeRequest("task", (SimulateMeOption("a", "A"),)))

    assert result.kind is SimulateMeResultKind.ABSTENTION
    assert result.abstention_code is SimulateMeAbstentionCode.NO_MATCHING_EVIDENCE
    assert result.evidence_refs == ()
    assert len(result.contextual_evidence_refs) == 1
    assert result.contextual_evidence_refs[0].dimension is SimulateMeDimension.BELIEF
    assert result.temporal_caveats[0].code is SimulateMeTemporalCaveatCode.EVIDENCE_AT_UNKNOWN


def test_invalid_or_unavailable_current_context_abstains_fail_closed() -> None:
    fake = _FakeSelfModel(RuntimeError("must not leak"))
    service = BuildSimulateMe(self_model=fake)

    result = service.execute(SimulateMeRequest("task", (SimulateMeOption("a", "A"),)))

    assert result.kind is SimulateMeResultKind.ABSTENTION
    assert (
        result.abstention_code is SimulateMeAbstentionCode.INSUFFICIENT_OR_INVALID_CURRENT_CONTEXT
    )
    assert result.evidence_refs == ()
    assert result.contextual_evidence_refs == ()


def test_context_with_more_than_twenty_matching_refs_abstains_instead_of_truncating() -> None:
    service, _fake = _service(*(_claim(index, "A") for index in range(1, 22)))

    result = service.execute(SimulateMeRequest("task", (SimulateMeOption("a", "A"),)))

    assert result.kind is SimulateMeResultKind.ABSTENTION
    assert (
        result.abstention_code is SimulateMeAbstentionCode.INSUFFICIENT_OR_INVALID_CURRENT_CONTEXT
    )
    assert result.evidence_refs == ()


def test_result_is_provider_free_and_contains_only_approved_identity_fields() -> None:
    service, _fake = _service(_claim(1, "A"))

    result = service.execute(SimulateMeRequest("task", (SimulateMeOption("a", "A"),)))

    assert result.derivation_version == DERIVATION_VERSION
    assert result.policy_id == POLICY_ID
    assert result.policy_fingerprint == POLICY_FINGERPRINT
    assert not hasattr(result, "confidence")
    assert not hasattr(result, "score")
    assert not hasattr(result, "recommendation")
    assert not hasattr(result, "best")
    assert not hasattr(result, "optimal")
