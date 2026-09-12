"""Focused tests for the provider-free Active Personal Learning v1 core."""

from __future__ import annotations

import inspect
from dataclasses import fields, replace
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest

import second_brain.application.active_personal_learning as active_learning
from second_brain.application.active_personal_learning import (
    ActiveLearningErrorCodeV1,
    ActiveLearningInvalidRequestError,
    ActiveLearningOperationV1,
    ActiveLearningResultV1,
    ActiveLearningSourceInvalidError,
    ActiveLearningSourceUnavailableError,
    ActiveLearningStatusV1,
    AnswerCaptureV1,
    QuestionCandidateV1,
    QuestionDispositionV1,
    QuestionReasonCodeV1,
    QuestionResolutionV1,
    build_active_learning_question,
    resolve_active_learning_question,
    serialize_question_candidate,
)
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
    BuildSimulateMe,
    SimulateMeOption,
    SimulateMeRequest,
)
from second_brain.domain.models import EvidenceAt, EvidenceAtPrecision, EvidenceKind, SelfKind

NOW = datetime(2026, 9, 12, 10, 0, tzinfo=UTC)
KNOWN_AT = datetime(2026, 9, 11, 12, 0, tzinfo=UTC)


def _uuid(index: int) -> UUID:
    return UUID(f"0198f4c5-6a00-7000-8000-{index:012d}")


def _claim(
    index: int,
    text: str,
    *,
    dimension: SelfModelDimension = SelfModelDimension.PREFERENCE,
    evidence_at: EvidenceAt = KNOWN_AT,
) -> SelfModelClaim:
    self_kind = {
        SelfModelDimension.PREFERENCE: SelfKind.PREFERENCE,
        SelfModelDimension.GOAL: SelfKind.GOAL,
        SelfModelDimension.BELIEF: SelfKind.BELIEF,
    }[dimension]
    note_id = _uuid(index)
    ref = SelfModelEvidenceRef(
        note_id=note_id,
        evidence_kind=EvidenceKind.USER_STATEMENT,
        self_kind=self_kind,
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
        generated_at=NOW,
        derivation_version=SELF_MODEL_DERIVATION_VERSION,
    )


def _self_model(*claims: SelfModelClaim, generated_at: datetime = NOW) -> SelfModelResult:
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
    if generated_at != NOW:
        ordered = tuple(replace(claim, generated_at=generated_at) for claim in ordered)
    return SelfModelResult(
        claims=ordered,
        eligible_evidence_count=len(ordered),
        represented_evidence_count=len(ordered),
        generated_at=generated_at,
        derivation_version=DEFAULT_SELF_MODEL_POLICY.derivation_version,
        policy_fingerprint=validate_self_model_policy(DEFAULT_SELF_MODEL_POLICY),
    )


class _CurrentSelfModel:
    def __init__(self, result: SelfModelResult) -> None:
        self.result = result
        self.calls = 0

    def execute(self, request: SelfModelRequest) -> SelfModelResult:
        self.calls += 1
        return self.result


def _source(
    *claims: SelfModelClaim,
    options: tuple[tuple[str, str], ...] = (("a", "A"), ("b", "B")),
    query: str = "Текущий выбор",
    generated_at: datetime = NOW,
) -> active_learning.ActiveLearningSourceV1:
    request = SimulateMeRequest(
        query=query,
        options=tuple(SimulateMeOption(id=option_id, label=label) for option_id, label in options),
    )
    self_model = _self_model(*claims, generated_at=generated_at)
    result = BuildSimulateMe(self_model=_CurrentSelfModel(self_model)).execute(request)
    return active_learning.ActiveLearningSourceV1(
        self_model=self_model,
        simulate_me_request=request,
        simulate_me_result=result,
    )


def _candidate(source: active_learning.ActiveLearningSourceV1) -> QuestionCandidateV1:
    result = build_active_learning_question(source, questions_enabled=True, now=NOW)
    assert result.status is ActiveLearningStatusV1.CANDIDATE
    assert result.candidate is not None
    return result.candidate


@pytest.mark.parametrize(
    ("source", "reason", "question", "evidence_ids"),
    [
        (
            _source(),
            QuestionReasonCodeV1.MISSING_EVIDENCE,
            "Какой вариант лучше всего описывает ваш текущий выбор?",
            (),
        ),
        (
            _source(_claim(2, "A", dimension=SelfModelDimension.BELIEF)),
            QuestionReasonCodeV1.INSUFFICIENT_EVIDENCE,
            "Какой вариант лучше всего описывает ваш текущий выбор в этой ситуации?",
            (_uuid(2),),
        ),
        (
            _source(
                _claim(2, "A"),
                _claim(1, "B", dimension=SelfModelDimension.GOAL),
            ),
            QuestionReasonCodeV1.CONFLICTING_EVIDENCE,
            "Какой вариант лучше всего описывает ваш текущий выбор сейчас?",
            (_uuid(1), _uuid(2)),
        ),
    ],
)
def test_each_approved_reason_builds_one_fixed_russian_candidate(
    source: active_learning.ActiveLearningSourceV1,
    reason: QuestionReasonCodeV1,
    question: str,
    evidence_ids: tuple[UUID, ...],
) -> None:
    result = build_active_learning_question(source, questions_enabled=True, now=NOW)

    assert result.status is ActiveLearningStatusV1.CANDIDATE
    assert result.candidate is not None
    assert result.candidate.reason_code is reason
    assert result.candidate.question == question
    assert result.candidate.evidence_note_ids == evidence_ids
    assert result.candidate.task == "Текущий выбор"
    assert result.candidate.options == (
        active_learning.QuestionOptionV1("a", "A"),
        active_learning.QuestionOptionV1("b", "B"),
    )


def test_disabled_prediction_and_repeated_operation_are_normal_no_candidate_results() -> None:
    source = _source(_claim(1, "A"))

    disabled = build_active_learning_question(source, now=NOW)
    assert disabled == ActiveLearningResultV1(
        status=ActiveLearningStatusV1.NO_CANDIDATE,
        candidate=None,
        no_candidate_code=active_learning.ActiveLearningNoCandidateCodeV1.QUESTIONS_DISABLED,
    )

    operation = ActiveLearningOperationV1()
    first = operation.build(source, questions_enabled=True, now=NOW)
    assert first.status is ActiveLearningStatusV1.NO_CANDIDATE
    assert (
        first.no_candidate_code is active_learning.ActiveLearningNoCandidateCodeV1.NO_ACTIONABLE_GAP
    )

    missing = _source()
    issued = operation.build(missing, questions_enabled=True, now=NOW)
    assert issued.status is ActiveLearningStatusV1.CANDIDATE
    repeated = operation.build(missing, questions_enabled=True, now=NOW)
    assert repeated.status is ActiveLearningStatusV1.NO_CANDIDATE
    assert (
        repeated.no_candidate_code is active_learning.ActiveLearningNoCandidateCodeV1.RATE_LIMITED
    )


def test_not_assessed_does_not_create_a_low_confidence_question() -> None:
    source = _source(_claim(1, "A"))

    result = build_active_learning_question(source, questions_enabled=True, now=NOW)

    assert result.status is ActiveLearningStatusV1.NO_CANDIDATE
    assert (
        result.no_candidate_code
        is active_learning.ActiveLearningNoCandidateCodeV1.NO_ACTIONABLE_GAP
    )
    assert "low" not in inspect.getsource(active_learning).lower().split("not a model score", 1)[0]


def test_caller_order_is_preserved_and_evidence_ids_are_sorted() -> None:
    source = _source(
        _claim(2, "A"),
        _claim(1, "B", dimension=SelfModelDimension.GOAL),
        options=(("b", "B"), ("a", "A")),
    )

    result = build_active_learning_question(source, questions_enabled=True, now=NOW)

    assert result.candidate is not None
    assert tuple(option.id for option in result.candidate.options) == ("b", "a")
    assert result.candidate.evidence_note_ids == (_uuid(1), _uuid(2))


def test_candidate_identity_and_basis_are_deterministic_without_generated_at() -> None:
    first_source = _source(_claim(1, "A", dimension=SelfModelDimension.BELIEF), generated_at=NOW)
    later_generated = NOW + timedelta(hours=3)
    second_source = _source(
        _claim(1, "A", dimension=SelfModelDimension.BELIEF),
        generated_at=later_generated,
    )

    first = _candidate(first_source)
    second = _candidate(second_source)

    assert first.candidate_id == second.candidate_id
    assert first.basis_fingerprint == second.basis_fingerprint
    assert first.issued_at == second.issued_at == NOW
    assert first.expires_at - first.issued_at == timedelta(seconds=600)
    assert first.candidate_id == f"apl1:{first.basis_fingerprint.removeprefix('sha256:')}"


def test_exact_ttl_boundary_expires_and_answer_is_only_ephemeral_option_capture() -> None:
    source = _source()
    candidate = _candidate(source)
    resolution = QuestionResolutionV1(
        candidate_id=candidate.candidate_id,
        disposition=QuestionDispositionV1.ANSWER,
        selected_option_id="b",
    )

    result = resolve_active_learning_question(
        source,
        candidate,
        resolution,
        now=NOW + timedelta(seconds=599),
    )
    assert result.answer_capture == AnswerCaptureV1(
        task="Текущий выбор",
        option=active_learning.QuestionOptionV1("b", "B"),
    )
    assert result.answer_capture is not None
    assert not hasattr(result.answer_capture, "evidence_kind")
    assert not hasattr(result.answer_capture, "self_kind")
    assert not hasattr(result.answer_capture, "apply")

    with pytest.raises(active_learning.ActiveLearningCandidateExpiredError) as expired:
        resolve_active_learning_question(
            source,
            candidate,
            resolution,
            now=NOW + timedelta(seconds=600),
        )
    assert expired.value.code == ActiveLearningErrorCodeV1.CANDIDATE_EXPIRED.value
    assert str(expired.value) == "active personal learning question has expired"


def test_changed_source_or_deleted_uuid_invalidates_old_candidate() -> None:
    old_source = _source(
        _claim(1, "A"),
        _claim(2, "B", dimension=SelfModelDimension.GOAL),
    )
    old_candidate = _candidate(old_source)
    changed_source = _source(
        _claim(1, "A"),
        _claim(2, "C", dimension=SelfModelDimension.GOAL),
    )
    resolution = QuestionResolutionV1(
        candidate_id=old_candidate.candidate_id,
        disposition=QuestionDispositionV1.IGNORE,
        selected_option_id=None,
    )

    with pytest.raises(active_learning.ActiveLearningCandidateStaleError) as stale:
        resolve_active_learning_question(
            changed_source,
            old_candidate,
            resolution,
            now=NOW + timedelta(seconds=1),
        )
    assert stale.value.code == ActiveLearningErrorCodeV1.CANDIDATE_STALE.value

    deleted_source = _source()
    with pytest.raises(active_learning.ActiveLearningCandidateStaleError):
        resolve_active_learning_question(
            deleted_source,
            old_candidate,
            resolution,
            now=NOW + timedelta(seconds=1),
        )

    with pytest.raises(active_learning.ActiveLearningCandidateStaleError):
        resolve_active_learning_question(
            old_source,
            old_candidate,
            resolution,
            now=NOW + timedelta(seconds=1),
            operation_state=active_learning.ActiveLearningOperationStateV1(),
        )


@pytest.mark.parametrize(
    "disposition", [QuestionDispositionV1.IGNORE, QuestionDispositionV1.REJECT]
)
def test_ignore_and_reject_have_no_canonical_effect_and_close_operation(
    disposition: QuestionDispositionV1,
) -> None:
    source = _source()
    before = source.self_model
    operation = ActiveLearningOperationV1()
    result = operation.build(source, questions_enabled=True, now=NOW)
    assert result.candidate is not None

    transition = operation.resolve(
        source,
        result.candidate,
        QuestionResolutionV1(
            candidate_id=result.candidate.candidate_id,
            disposition=disposition,
            selected_option_id=None,
        ),
        now=NOW + timedelta(seconds=1),
    )

    assert transition.answer_capture is None
    assert transition.next_state.terminal is True
    assert source.self_model is before
    with pytest.raises(active_learning.ActiveLearningCandidateAlreadyResolvedError):
        operation.resolve(
            source,
            result.candidate,
            QuestionResolutionV1(
                candidate_id=result.candidate.candidate_id,
                disposition=disposition,
                selected_option_id=None,
            ),
            now=NOW + timedelta(seconds=2),
        )


def test_invalid_answer_and_malformed_or_unavailable_sources_fail_closed() -> None:
    source = _source()
    candidate = _candidate(source)
    invalid_resolution = QuestionResolutionV1(
        candidate_id=candidate.candidate_id,
        disposition=QuestionDispositionV1.ANSWER,
        selected_option_id="not-an-option",
    )
    with pytest.raises(active_learning.ActiveLearningInvalidAnswerError) as invalid_answer:
        resolve_active_learning_question(
            source,
            candidate,
            invalid_resolution,
            now=NOW + timedelta(seconds=1),
        )
    assert invalid_answer.value.code == ActiveLearningErrorCodeV1.INVALID_ANSWER.value

    malformed_result = replace(source.simulate_me_result, policy_id="unapproved")
    malformed_source = replace(source, simulate_me_result=malformed_result)
    with pytest.raises(ActiveLearningSourceInvalidError) as invalid_source:
        build_active_learning_question(malformed_source, questions_enabled=True, now=NOW)
    assert invalid_source.value.as_dict() == {
        "code": ActiveLearningErrorCodeV1.SOURCE_INVALID.value,
        "message": "active personal learning source is invalid",
    }
    assert "unapproved" not in str(invalid_source.value)

    unavailable_model = _self_model()
    unavailable_result = BuildSimulateMe(
        self_model=_FailingSelfModel(),
    ).execute(
        SimulateMeRequest(
            query="Текущий выбор",
            options=(SimulateMeOption("a", "A"), SimulateMeOption("b", "B")),
        )
    )
    unavailable_source = active_learning.ActiveLearningSourceV1(
        self_model=unavailable_model,
        simulate_me_request=SimulateMeRequest(
            query="Текущий выбор",
            options=(SimulateMeOption("a", "A"), SimulateMeOption("b", "B")),
        ),
        simulate_me_result=unavailable_result,
    )
    with pytest.raises(ActiveLearningSourceUnavailableError) as unavailable:
        build_active_learning_question(unavailable_source, questions_enabled=True, now=NOW)
    assert unavailable.value.code == ActiveLearningErrorCodeV1.SOURCE_UNAVAILABLE.value


class _FailingSelfModel:
    def execute(self, request: SelfModelRequest) -> SelfModelResult:
        raise RuntimeError("backend detail must not escape")


def test_exact_dto_bounds_and_unknown_fields_are_rejected() -> None:
    source = _source()
    with pytest.raises(ActiveLearningInvalidRequestError):
        build_active_learning_question(source, questions_enabled=1, now=NOW)
    with pytest.raises(ActiveLearningInvalidRequestError):
        build_active_learning_question(source, questions_enabled=True, now=datetime(2026, 9, 12))
    with pytest.raises(ActiveLearningSourceInvalidError):
        build_active_learning_question(
            {
                "self_model": source.self_model,
                "simulate_me_request": source.simulate_me_request,
                "simulate_me_result": source.simulate_me_result,
                "unknown": "field",
            },
            questions_enabled=True,
            now=NOW,
        )

    candidate = _candidate(source)
    candidate_fields = {item.name for item in fields(QuestionCandidateV1)}
    assert candidate_fields == {
        "contract_version",
        "candidate_id",
        "kind",
        "reason_code",
        "task",
        "question",
        "options",
        "source",
        "source_derivation_version",
        "source_policy_id",
        "source_policy_fingerprint",
        "evidence_note_ids",
        "basis_fingerprint",
        "issued_at",
        "expires_at",
    }
    with pytest.raises(active_learning.ActiveLearningInvalidRequestError):
        serialize_question_candidate(replace(candidate, options=()))


def test_candidate_is_not_evidence_and_core_has_no_external_or_write_seam() -> None:
    module_source = inspect.getsource(active_learning)
    for forbidden_import in ("VaultReader", "VaultWriter", "httpx", "requests", "urllib", "socket"):
        assert forbidden_import not in module_source
    assert "second-brain-vault" not in module_source

    candidate_field_names = {item.name for item in fields(QuestionCandidateV1)}
    assert not candidate_field_names & {
        "evidence_kind",
        "self_kind",
        "confidence",
        "score",
        "probability",
        "canonical",
        "provider",
        "model",
        "apply",
    }
    assert {item.name for item in fields(AnswerCaptureV1)} == {"task", "option"}
    assert not hasattr(active_learning, "VaultReader")
    assert not hasattr(active_learning, "LlmPort")
    assert not hasattr(active_learning, "TelemetryPort")
