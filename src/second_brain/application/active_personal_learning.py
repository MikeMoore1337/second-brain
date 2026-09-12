"""Provider-free deterministic Active Personal Learning v1 core.

This module builds one ephemeral choice question from a server-owned current
Self Model/Simulate Me bundle.  It deliberately has no reader, writer,
provider, network, persistence, cache, or telemetry seam.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Final
from uuid import UUID

from second_brain.application.self_model import (
    DEFAULT_SELF_MODEL_POLICY,
    SelfModelClaim,
    SelfModelError,
    SelfModelEvidenceRef,
    SelfModelRequest,
    SelfModelResult,
    SelfModelResultTooLargeError,
    validate_self_model_policy,
    validate_self_model_result,
)
from second_brain.application.simulate_me import (
    MAX_LABEL_BYTES,
    SimulateMeAbstentionCode,
    SimulateMeContextualEvidenceRef,
    SimulateMeDimension,
    SimulateMeError,
    SimulateMeEvidenceRef,
    SimulateMeOption,
    SimulateMeRequest,
    SimulateMeResult,
    SimulateMeResultKind,
    SimulateMeTemporalCaveat,
    normalize_simulate_me_text,
    validate_simulate_me_policy,
    validate_simulate_me_request,
    validate_simulate_me_result,
)
from second_brain.application.simulate_me import (
    POLICY_FINGERPRINT as SIMULATE_ME_POLICY_FINGERPRINT,
)
from second_brain.domain.models import EvidenceAt

CONTRACT_VERSION: Final[str] = "active-personal-learning-v1"
QUESTION_KIND: Final[str] = "choice"
SOURCE: Final[str] = "simulate-me-abstention-v1"
SOURCE_DERIVATION_VERSION: Final[str] = "simulate-me-v1"
SOURCE_POLICY_ID: Final[str] = "simulate-me-direct-exact-v1"
SOURCE_POLICY_FINGERPRINT: Final[str] = SIMULATE_ME_POLICY_FINGERPRINT
QUESTION_TTL_SECONDS: Final[int] = 600
QUESTION_TTL: Final[timedelta] = timedelta(seconds=QUESTION_TTL_SECONDS)
MAX_EVIDENCE_NOTE_IDS: Final[int] = 20
MAX_QUESTION_BYTES: Final[int] = 512
MAX_CANDIDATE_BYTES: Final[int] = 16384

_ID_PATTERN: Final[re.Pattern[str]] = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,63}\Z", re.ASCII)
_CANDIDATE_ID_PATTERN: Final[re.Pattern[str]] = re.compile(r"apl1:[0-9a-f]{64}\Z", re.ASCII)
_FINGERPRINT_PATTERN: Final[re.Pattern[str]] = re.compile(r"sha256:[0-9a-f]{64}\Z", re.ASCII)
_QUESTION_TEMPLATES: Final[dict[str, str]] = {
    "missing_evidence": "Какой вариант лучше всего описывает ваш текущий выбор?",
    "conflicting_evidence": "Какой вариант лучше всего описывает ваш текущий выбор сейчас?",
    "insufficient_evidence": (
        "Какой вариант лучше всего описывает ваш текущий выбор в этой ситуации?"
    ),
}


class QuestionReasonCodeV1(StrEnum):
    """The three closed, deterministic Stage 8 reason codes."""

    MISSING_EVIDENCE = "missing_evidence"
    CONFLICTING_EVIDENCE = "conflicting_evidence"
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"


class ActiveLearningStatusV1(StrEnum):
    """The two exact result statuses."""

    CANDIDATE = "candidate"
    NO_CANDIDATE = "no_candidate"


class ActiveLearningNoCandidateCodeV1(StrEnum):
    """Normal, non-error outcomes for one explicit operation."""

    QUESTIONS_DISABLED = "questions_disabled"
    NO_ACTIONABLE_GAP = "no_actionable_gap"
    RATE_LIMITED = "rate_limited"


class QuestionDispositionV1(StrEnum):
    """The only terminal controls for a question candidate."""

    IGNORE = "ignore"
    REJECT = "reject"
    ANSWER = "answer"


class ActiveLearningErrorCodeV1(StrEnum):
    """Closed public/application error vocabulary from the contract."""

    INVALID_REQUEST = "ACTIVE_LEARNING_INVALID_REQUEST"
    SOURCE_INVALID = "ACTIVE_LEARNING_SOURCE_INVALID"
    SOURCE_UNAVAILABLE = "ACTIVE_LEARNING_SOURCE_UNAVAILABLE"
    CANDIDATE_STALE = "ACTIVE_LEARNING_CANDIDATE_STALE"
    CANDIDATE_EXPIRED = "ACTIVE_LEARNING_CANDIDATE_EXPIRED"
    CANDIDATE_ALREADY_RESOLVED = "ACTIVE_LEARNING_CANDIDATE_ALREADY_RESOLVED"
    INVALID_ANSWER = "ACTIVE_LEARNING_INVALID_ANSWER"
    RESULT_TOO_LARGE = "ACTIVE_LEARNING_RESULT_TOO_LARGE"


@dataclass(frozen=True, slots=True)
class ActiveLearningSourceV1:
    """One server-owned current Self Model/Simulate Me snapshot."""

    self_model: SelfModelResult
    simulate_me_request: SimulateMeRequest
    simulate_me_result: SimulateMeResult


@dataclass(frozen=True, slots=True)
class QuestionOptionV1:
    """A request-local choice copied into a question without ranking."""

    id: str
    label: str


@dataclass(frozen=True, slots=True)
class QuestionCandidateV1:
    """One bounded, derived, choice-only question candidate."""

    contract_version: str
    candidate_id: str
    kind: str
    reason_code: QuestionReasonCodeV1
    task: str
    question: str
    options: tuple[QuestionOptionV1, ...]
    source: str
    source_derivation_version: str
    source_policy_id: str
    source_policy_fingerprint: str
    evidence_note_ids: tuple[UUID, ...]
    basis_fingerprint: str
    issued_at: datetime
    expires_at: datetime


@dataclass(frozen=True, slots=True)
class ActiveLearningResultV1:
    """Exactly one candidate or one normal no-candidate outcome."""

    status: ActiveLearningStatusV1
    candidate: QuestionCandidateV1 | None
    no_candidate_code: ActiveLearningNoCandidateCodeV1 | None


@dataclass(frozen=True, slots=True)
class QuestionResolutionV1:
    """A bounded terminal control message; it is not an evidence write."""

    candidate_id: str
    disposition: QuestionDispositionV1
    selected_option_id: str | None


@dataclass(frozen=True, slots=True)
class AnswerCaptureV1:
    """Only the current task and exact request-local option are handed off."""

    task: str
    option: QuestionOptionV1

    @property
    def selected_option(self) -> QuestionOptionV1:
        """Compatibility spelling for callers that use Stage 6 terminology."""

        return self.option


@dataclass(frozen=True, slots=True)
class ActiveLearningOperationStateV1:
    """Ephemeral page/process state for the one-candidate rate boundary."""

    candidate_id: str | None = None
    terminal: bool = False

    def with_candidate(self, candidate: QuestionCandidateV1) -> ActiveLearningOperationStateV1:
        """Return state after issuing/showing one candidate."""

        validate_question_candidate(candidate)
        return ActiveLearningOperationStateV1(candidate_id=candidate.candidate_id, terminal=False)

    def after_resolution(self, candidate_id: str) -> ActiveLearningOperationStateV1:
        """Return terminal in-memory state without persisting disposition."""

        if type(candidate_id) is not str or _CANDIDATE_ID_PATTERN.fullmatch(candidate_id) is None:
            raise ActiveLearningInvalidRequestError()
        return ActiveLearningOperationStateV1(candidate_id=candidate_id, terminal=True)


@dataclass(frozen=True, slots=True)
class ActiveLearningResolutionResultV1:
    """Pure resolution transition and its disposable next operation state."""

    candidate_id: str
    disposition: QuestionDispositionV1
    answer_capture: AnswerCaptureV1 | None
    next_state: ActiveLearningOperationStateV1


_ERROR_MESSAGES: Final[dict[ActiveLearningErrorCodeV1, str]] = {
    ActiveLearningErrorCodeV1.INVALID_REQUEST: "active personal learning request failed validation",
    ActiveLearningErrorCodeV1.SOURCE_INVALID: "active personal learning source is invalid",
    ActiveLearningErrorCodeV1.SOURCE_UNAVAILABLE: "active personal learning source is unavailable",
    ActiveLearningErrorCodeV1.CANDIDATE_STALE: "active personal learning question is stale",
    ActiveLearningErrorCodeV1.CANDIDATE_EXPIRED: "active personal learning question has expired",
    ActiveLearningErrorCodeV1.CANDIDATE_ALREADY_RESOLVED: (
        "active personal learning question is already resolved"
    ),
    ActiveLearningErrorCodeV1.INVALID_ANSWER: "active personal learning answer failed validation",
    ActiveLearningErrorCodeV1.RESULT_TOO_LARGE: (
        "active personal learning result exceeds its byte limit"
    ),
}


class ActiveLearningError(RuntimeError):
    """Safe error without task, labels, UUID inventories, paths, or causes."""

    def __init__(self, code: ActiveLearningErrorCodeV1 | str) -> None:
        normalized = _normalize_error_code(code)
        self.code = normalized.value
        self.message = _ERROR_MESSAGES[normalized]
        super().__init__(self.message)

    def as_dict(self) -> dict[str, str]:
        """Return the fixed public error projection."""

        return {"code": self.code, "message": self.message}


class ActiveLearningInvalidRequestError(ActiveLearningError):
    """The request, DTO, clock, or in-memory state is malformed."""

    def __init__(self) -> None:
        super().__init__(ActiveLearningErrorCodeV1.INVALID_REQUEST)


class ActiveLearningSourceInvalidError(ActiveLearningError):
    """The current typed source does not satisfy its exact contract."""

    def __init__(self) -> None:
        super().__init__(ActiveLearningErrorCodeV1.SOURCE_INVALID)


class ActiveLearningSourceUnavailableError(ActiveLearningError):
    """The current operation reports unavailable/invalid current context."""

    def __init__(self) -> None:
        super().__init__(ActiveLearningErrorCodeV1.SOURCE_UNAVAILABLE)


class ActiveLearningCandidateStaleError(ActiveLearningError):
    """The candidate no longer matches the current source/page state."""

    def __init__(self) -> None:
        super().__init__(ActiveLearningErrorCodeV1.CANDIDATE_STALE)


class ActiveLearningCandidateExpiredError(ActiveLearningError):
    """The candidate reached its exact 600-second expiry boundary."""

    def __init__(self) -> None:
        super().__init__(ActiveLearningErrorCodeV1.CANDIDATE_EXPIRED)


class ActiveLearningCandidateAlreadyResolvedError(ActiveLearningError):
    """The current page/process state already terminally resolved the candidate."""

    def __init__(self) -> None:
        super().__init__(ActiveLearningErrorCodeV1.CANDIDATE_ALREADY_RESOLVED)


class ActiveLearningInvalidAnswerError(ActiveLearningError):
    """The resolution is not an exact ignore/reject/one-option control."""

    def __init__(self) -> None:
        super().__init__(ActiveLearningErrorCodeV1.INVALID_ANSWER)


class ActiveLearningResultTooLargeError(ActiveLearningError):
    """The bounded candidate cannot be represented without truncation."""

    def __init__(self) -> None:
        super().__init__(ActiveLearningErrorCodeV1.RESULT_TOO_LARGE)


@dataclass(frozen=True, slots=True)
class _CurrentClaimRef:
    claim: SelfModelClaim
    note_id: UUID
    dimension: SimulateMeDimension
    note_ids: tuple[UUID, ...]
    evidence_at: EvidenceAt


@dataclass(frozen=True, slots=True)
class _SourceInspection:
    source: ActiveLearningSourceV1
    request: SimulateMeRequest
    result: SimulateMeResult
    current_refs: tuple[_CurrentClaimRef, ...]
    normalized_options: tuple[tuple[SimulateMeOption, str], ...]


def build_active_learning_question(
    source: object,
    *,
    questions_enabled: object = False,
    now: object,
    operation_state: object | None = None,
) -> ActiveLearningResultV1:
    """Build zero or one candidate from one validated current source snapshot."""

    inspection = _inspect_source(source)
    state = _validate_operation_state(operation_state)
    if type(questions_enabled) is not bool:
        raise ActiveLearningInvalidRequestError()
    issued_at = _normalize_utc_datetime(now)
    return _build_from_inspection(
        inspection,
        questions_enabled=questions_enabled,
        issued_at=issued_at,
        operation_state=state,
    )


@dataclass(frozen=True, slots=True)
class BuildActiveLearningQuestion:
    """Application-style wrapper with a composition-owned clock."""

    clock: Callable[[], datetime]

    def execute(
        self,
        source: object,
        *,
        questions_enabled: object = False,
        operation_state: object | None = None,
    ) -> ActiveLearningResultV1:
        """Build using the injected application clock, never a provider clock."""

        if not callable(self.clock):
            raise ActiveLearningInvalidRequestError()
        try:
            now = self.clock()
        except Exception:
            raise ActiveLearningInvalidRequestError() from None
        return build_active_learning_question(
            source,
            questions_enabled=questions_enabled,
            now=now,
            operation_state=operation_state,
        )


@dataclass(slots=True)
class ActiveLearningOperationV1:
    """Optional in-memory helper; it has no persistence or history capability."""

    state: ActiveLearningOperationStateV1 = field(default_factory=ActiveLearningOperationStateV1)

    def build(
        self,
        source: object,
        *,
        questions_enabled: object = False,
        now: object,
    ) -> ActiveLearningResultV1:
        """Issue at most one candidate and retain only current page state."""

        result = build_active_learning_question(
            source,
            questions_enabled=questions_enabled,
            now=now,
            operation_state=self.state,
        )
        if result.candidate is not None:
            self.state = self.state.with_candidate(result.candidate)
        return result

    def resolve(
        self,
        source: object,
        candidate: object,
        resolution: object,
        *,
        now: object,
    ) -> ActiveLearningResolutionResultV1:
        """Apply one pure resolution and retain only terminal page state."""

        result = resolve_active_learning_question(
            source,
            candidate,
            resolution,
            now=now,
            operation_state=self.state,
        )
        self.state = result.next_state
        return result


def resolve_active_learning_question(
    source: object,
    candidate: object,
    resolution: object,
    *,
    now: object,
    operation_state: object | None = None,
) -> ActiveLearningResolutionResultV1:
    """Validate a current candidate and perform only an ephemeral resolution."""

    try:
        validated_candidate = validate_question_candidate(candidate)
    except ActiveLearningError:
        raise ActiveLearningCandidateStaleError() from None

    state = _validate_operation_state(operation_state)
    if (
        state is not None
        and state.terminal
        and state.candidate_id == validated_candidate.candidate_id
    ):
        raise ActiveLearningCandidateAlreadyResolvedError()
    if state is not None and state.candidate_id is None:
        raise ActiveLearningCandidateStaleError()
    if state is not None and state.candidate_id not in {
        None,
        validated_candidate.candidate_id,
    }:
        raise ActiveLearningCandidateStaleError()

    current_time = _normalize_utc_datetime(now)
    if current_time >= validated_candidate.expires_at:
        raise ActiveLearningCandidateExpiredError()
    if current_time < validated_candidate.issued_at:
        raise ActiveLearningCandidateStaleError()

    validated_resolution = _validate_resolution_for_candidate(resolution, validated_candidate)
    inspection = _inspect_source(source)
    current_result = _build_from_inspection(
        inspection,
        questions_enabled=True,
        issued_at=current_time,
        operation_state=None,
    )
    current_candidate = current_result.candidate
    if (
        current_result.status is not ActiveLearningStatusV1.CANDIDATE
        or current_candidate is None
        or not _same_candidate_content(validated_candidate, current_candidate)
    ):
        raise ActiveLearningCandidateStaleError()

    answer_capture: AnswerCaptureV1 | None = None
    if validated_resolution.disposition is QuestionDispositionV1.ANSWER:
        selected_id = validated_resolution.selected_option_id
        assert selected_id is not None
        selected_option = next(
            option for option in validated_candidate.options if option.id == selected_id
        )
        answer_capture = AnswerCaptureV1(
            task=validated_candidate.task,
            option=selected_option,
        )

    next_state = ActiveLearningOperationStateV1(
        candidate_id=validated_candidate.candidate_id,
        terminal=True,
    )
    return ActiveLearningResolutionResultV1(
        candidate_id=validated_candidate.candidate_id,
        disposition=validated_resolution.disposition,
        answer_capture=answer_capture,
        next_state=next_state,
    )


def validate_active_learning_source(source: object) -> ActiveLearningSourceV1:
    """Validate the server-owned source bundle without reading any backend."""

    return _inspect_source(source).source


def validate_question_option(option: object) -> QuestionOptionV1:
    """Validate one normalized, request-local option."""

    if type(option) is not QuestionOptionV1:
        raise ActiveLearningInvalidRequestError()
    if type(option.id) is not str or _ID_PATTERN.fullmatch(option.id) is None:
        raise ActiveLearningInvalidRequestError()
    if type(option.label) is not str:
        raise ActiveLearningInvalidRequestError()
    try:
        normalized = normalize_simulate_me_text(option.label, MAX_LABEL_BYTES)
    except Exception:
        raise ActiveLearningInvalidRequestError() from None
    if normalized != option.label:
        raise ActiveLearningInvalidRequestError()
    return option


def validate_question_candidate(candidate: object) -> QuestionCandidateV1:
    """Validate exact candidate fields and the fixed 600-second TTL."""

    _validate_candidate_fields(candidate)
    assert isinstance(candidate, QuestionCandidateV1)
    serialized = _serialize_candidate(candidate)
    if len(serialized) > MAX_CANDIDATE_BYTES:
        raise ActiveLearningResultTooLargeError()
    return candidate


def serialize_question_candidate(candidate: object) -> bytes:
    """Return the bounded canonical UTF-8 candidate serialization."""

    return _serialize_candidate(validate_question_candidate(candidate))


def serialize_active_learning_result(result: object) -> bytes:
    """Return the exact bounded candidate/no-candidate JSON envelope."""

    validated = validate_active_learning_result(result)
    payload = {
        "candidate": (
            None if validated.candidate is None else _candidate_payload(validated.candidate)
        ),
        "no_candidate_code": (
            None if validated.no_candidate_code is None else validated.no_candidate_code.value
        ),
        "status": validated.status.value,
    }
    return _canonical_json(payload)


def validate_active_learning_result(result: object) -> ActiveLearningResultV1:
    """Validate the exact candidate/no-candidate envelope."""

    if type(result) is not ActiveLearningResultV1:
        raise ActiveLearningInvalidRequestError()
    if type(result.status) is not ActiveLearningStatusV1:
        raise ActiveLearningInvalidRequestError()
    if result.status is ActiveLearningStatusV1.CANDIDATE:
        if (
            type(result.candidate) is not QuestionCandidateV1
            or result.no_candidate_code is not None
        ):
            raise ActiveLearningInvalidRequestError()
        validate_question_candidate(result.candidate)
    elif (
        result.candidate is not None
        or type(result.no_candidate_code) is not ActiveLearningNoCandidateCodeV1
    ):
        raise ActiveLearningInvalidRequestError()
    return result


def validate_question_resolution(resolution: object) -> QuestionResolutionV1:
    """Validate the control shape before candidate-specific option matching."""

    if type(resolution) is not QuestionResolutionV1:
        raise ActiveLearningInvalidAnswerError()
    if (
        type(resolution.candidate_id) is not str
        or _CANDIDATE_ID_PATTERN.fullmatch(resolution.candidate_id) is None
        or type(resolution.disposition) is not QuestionDispositionV1
    ):
        raise ActiveLearningInvalidAnswerError()
    if resolution.disposition in {
        QuestionDispositionV1.IGNORE,
        QuestionDispositionV1.REJECT,
    }:
        if resolution.selected_option_id is not None:
            raise ActiveLearningInvalidAnswerError()
    elif (
        type(resolution.selected_option_id) is not str
        or _ID_PATTERN.fullmatch(resolution.selected_option_id) is None
    ):
        raise ActiveLearningInvalidAnswerError()
    return resolution


def validate_operation_state(
    state: object,
) -> ActiveLearningOperationStateV1:
    """Validate disposable one-candidate page/process state."""

    validated = _validate_operation_state(state)
    if validated is None:
        raise ActiveLearningInvalidRequestError()
    return validated


def _build_from_inspection(
    inspection: _SourceInspection,
    *,
    questions_enabled: bool,
    issued_at: datetime,
    operation_state: ActiveLearningOperationStateV1 | None,
) -> ActiveLearningResultV1:
    if not questions_enabled:
        return _no_candidate(ActiveLearningNoCandidateCodeV1.QUESTIONS_DISABLED)
    if operation_state is not None and operation_state.candidate_id is not None:
        return _no_candidate(ActiveLearningNoCandidateCodeV1.RATE_LIMITED)

    reason = _reason_for_source(inspection)
    if reason is None:
        return _no_candidate(ActiveLearningNoCandidateCodeV1.NO_ACTIONABLE_GAP)

    task = normalize_simulate_me_text(inspection.request.query, 4096)
    options = tuple(
        QuestionOptionV1(id=option.id, label=normalized_label)
        for option, normalized_label in inspection.normalized_options
    )
    evidence_note_ids = _candidate_note_ids(inspection.result)
    basis_fingerprint = _basis_fingerprint(
        inspection,
        reason=reason,
        task=task,
        options=options,
    )
    candidate = QuestionCandidateV1(
        contract_version=CONTRACT_VERSION,
        candidate_id=f"apl1:{basis_fingerprint.removeprefix('sha256:')}",
        kind=QUESTION_KIND,
        reason_code=reason,
        task=task,
        question=_QUESTION_TEMPLATES[reason.value],
        options=options,
        source=SOURCE,
        source_derivation_version=SOURCE_DERIVATION_VERSION,
        source_policy_id=SOURCE_POLICY_ID,
        source_policy_fingerprint=SOURCE_POLICY_FINGERPRINT,
        evidence_note_ids=evidence_note_ids,
        basis_fingerprint=basis_fingerprint,
        issued_at=issued_at,
        expires_at=issued_at + QUESTION_TTL,
    )
    validate_question_candidate(candidate)
    result = ActiveLearningResultV1(
        status=ActiveLearningStatusV1.CANDIDATE,
        candidate=candidate,
        no_candidate_code=None,
    )
    return validate_active_learning_result(result)


def _no_candidate(code: ActiveLearningNoCandidateCodeV1) -> ActiveLearningResultV1:
    return validate_active_learning_result(
        ActiveLearningResultV1(
            status=ActiveLearningStatusV1.NO_CANDIDATE,
            candidate=None,
            no_candidate_code=code,
        )
    )


def _reason_for_source(inspection: _SourceInspection) -> QuestionReasonCodeV1 | None:
    result = inspection.result
    if result.kind is SimulateMeResultKind.PREDICTION:
        return None
    if result.abstention_code is SimulateMeAbstentionCode.INSUFFICIENT_OR_INVALID_CURRENT_CONTEXT:
        raise ActiveLearningSourceUnavailableError()
    if result.abstention_code is SimulateMeAbstentionCode.NO_MATCHING_EVIDENCE:
        if result.evidence_refs:
            raise ActiveLearningSourceInvalidError()
        if result.contextual_evidence_refs:
            return QuestionReasonCodeV1.INSUFFICIENT_EVIDENCE
        return QuestionReasonCodeV1.MISSING_EVIDENCE
    if result.abstention_code is SimulateMeAbstentionCode.MULTIPLE_OPTIONS_SUPPORTED:
        if not result.evidence_refs:
            raise ActiveLearningSourceInvalidError()
        return QuestionReasonCodeV1.CONFLICTING_EVIDENCE
    raise ActiveLearningSourceInvalidError()


def _inspect_source(source: object) -> _SourceInspection:
    if type(source) is not ActiveLearningSourceV1:
        raise ActiveLearningSourceInvalidError()
    try:
        self_model_policy_fingerprint = validate_self_model_policy(DEFAULT_SELF_MODEL_POLICY)
        validated_self_model = validate_self_model_result(
            source.self_model,
            request=SelfModelRequest(),
            policy=DEFAULT_SELF_MODEL_POLICY,
            expected_policy_fingerprint=self_model_policy_fingerprint,
        )
        validated_request = validate_simulate_me_request(source.simulate_me_request)
        validate_simulate_me_policy()
        validated_result = validate_simulate_me_result(
            source.simulate_me_result,
            request=validated_request,
        )
        normalized_options = tuple(
            (
                option,
                normalize_simulate_me_text(option.label, MAX_LABEL_BYTES),
            )
            for option in validated_request.options
        )
        current_refs = _current_claim_refs(validated_self_model)
        if (
            validated_result.abstention_code
            is not SimulateMeAbstentionCode.INSUFFICIENT_OR_INVALID_CURRENT_CONTEXT
        ):
            _validate_result_against_current_claims(
                validated_self_model,
                validated_request,
                validated_result,
                current_refs,
                normalized_options,
            )
        return _SourceInspection(
            source=source,
            request=validated_request,
            result=validated_result,
            current_refs=current_refs,
            normalized_options=normalized_options,
        )
    except ActiveLearningError:
        raise
    except SelfModelResultTooLargeError:
        raise ActiveLearningResultTooLargeError() from None
    except SelfModelError, SimulateMeError, TypeError, UnicodeError, ValueError:
        raise ActiveLearningSourceInvalidError() from None
    except Exception:
        raise ActiveLearningSourceInvalidError() from None


def _current_claim_refs(self_model: SelfModelResult) -> tuple[_CurrentClaimRef, ...]:
    by_note_id: dict[UUID, _CurrentClaimRef] = {}
    for claim in self_model.claims:
        source = claim.supporting_evidence[0]
        note_ids = tuple(sorted({source.note_id, *source.related_note_ids}, key=str))
        if not 1 <= len(note_ids) <= MAX_EVIDENCE_NOTE_IDS:
            raise ActiveLearningResultTooLargeError()
        current = _CurrentClaimRef(
            claim=claim,
            note_id=source.note_id,
            dimension=SimulateMeDimension(claim.dimension.value),
            note_ids=note_ids,
            evidence_at=source.evidence_at,
        )
        previous = by_note_id.get(current.note_id)
        if previous is not None and previous != current:
            raise ActiveLearningSourceInvalidError()
        by_note_id[current.note_id] = current
    return tuple(sorted(by_note_id.values(), key=lambda item: str(item.note_id)))


def _validate_result_against_current_claims(
    self_model: SelfModelResult,
    request: SimulateMeRequest,
    result: SimulateMeResult,
    current_refs: tuple[_CurrentClaimRef, ...],
    normalized_options: tuple[tuple[SimulateMeOption, str], ...],
) -> None:
    del self_model, request
    refs_by_note_id = {item.note_id: item for item in current_refs}
    expected_direct: dict[UUID, set[str]] = {}
    expected_contextual: set[UUID] = set()
    for current in current_refs:
        matched_ids = _matching_option_ids(current.claim, normalized_options)
        if not matched_ids:
            continue
        if current.dimension is SimulateMeDimension.BELIEF:
            expected_contextual.add(current.note_id)
        elif current.dimension in {
            SimulateMeDimension.PREFERENCE,
            SimulateMeDimension.GOAL,
        }:
            expected_direct[current.note_id] = matched_ids

    actual_direct = {ref.claim_id for ref in result.evidence_refs}
    actual_contextual = {ref.claim_id for ref in result.contextual_evidence_refs}
    if actual_direct != set(expected_direct) or actual_contextual != expected_contextual:
        raise ActiveLearningSourceInvalidError()

    for direct_ref in result.evidence_refs:
        evidence_current = refs_by_note_id.get(direct_ref.claim_id)
        if evidence_current is None or evidence_current.dimension is SimulateMeDimension.BELIEF:
            raise ActiveLearningSourceInvalidError()
        if direct_ref != _direct_result_ref(evidence_current):
            raise ActiveLearningSourceInvalidError()
    for contextual_ref in result.contextual_evidence_refs:
        contextual_current = refs_by_note_id.get(contextual_ref.claim_id)
        if (
            contextual_current is None
            or contextual_current.dimension is not SimulateMeDimension.BELIEF
        ):
            raise ActiveLearningSourceInvalidError()
        if contextual_ref != _contextual_result_ref(contextual_current):
            raise ActiveLearningSourceInvalidError()

    expected_caveats = {
        ref.claim_id for ref in result.evidence_refs if ref.evidence_at == "unknown"
    } | {ref.claim_id for ref in result.contextual_evidence_refs if ref.evidence_at == "unknown"}
    actual_caveats = {caveat.claim_id for caveat in result.temporal_caveats}
    if actual_caveats != expected_caveats:
        raise ActiveLearningSourceInvalidError()

    supported_option_ids = {
        option_id for option_ids in expected_direct.values() for option_id in option_ids
    }
    if result.kind is SimulateMeResultKind.PREDICTION:
        selected = result.selected_option
        if selected is None or supported_option_ids != {selected.id}:
            raise ActiveLearningSourceInvalidError()
    elif result.abstention_code is SimulateMeAbstentionCode.NO_MATCHING_EVIDENCE:
        if supported_option_ids:
            raise ActiveLearningSourceInvalidError()
    elif result.abstention_code is SimulateMeAbstentionCode.MULTIPLE_OPTIONS_SUPPORTED:
        if len(supported_option_ids) < 2:
            raise ActiveLearningSourceInvalidError()
    else:
        raise ActiveLearningSourceInvalidError()


def _matching_option_ids(
    claim: SelfModelClaim,
    normalized_options: tuple[tuple[SimulateMeOption, str], ...],
) -> set[str]:
    try:
        normalized_claim = normalize_simulate_me_text(claim.claim, MAX_LABEL_BYTES)
    except Exception:
        raise ActiveLearningSourceInvalidError() from None
    return {
        option.id
        for option, normalized_label in normalized_options
        if normalized_claim == normalized_label
    }


def _direct_result_ref(current: _CurrentClaimRef) -> SimulateMeEvidenceRef:
    return SimulateMeEvidenceRef(
        claim_id=current.note_id,
        dimension=current.dimension,
        note_ids=current.note_ids,
        evidence_at=current.evidence_at,
    )


def _contextual_result_ref(current: _CurrentClaimRef) -> SimulateMeContextualEvidenceRef:
    return SimulateMeContextualEvidenceRef(
        claim_id=current.note_id,
        dimension=SimulateMeDimension.BELIEF,
        note_ids=current.note_ids,
        evidence_at=current.evidence_at,
    )


def _candidate_note_ids(result: SimulateMeResult) -> tuple[UUID, ...]:
    note_ids = tuple(
        sorted(
            {note_id for ref in result.evidence_refs for note_id in ref.note_ids}
            | {note_id for ref in result.contextual_evidence_refs for note_id in ref.note_ids},
            key=str,
        )
    )
    if len(note_ids) > MAX_EVIDENCE_NOTE_IDS:
        raise ActiveLearningResultTooLargeError()
    return note_ids


def _basis_fingerprint(
    inspection: _SourceInspection,
    *,
    reason: QuestionReasonCodeV1,
    task: str,
    options: tuple[QuestionOptionV1, ...],
) -> str:
    payload = {
        "contract_version": CONTRACT_VERSION,
        "kind": QUESTION_KIND,
        "reason_code": reason.value,
        "task": task,
        "options": [{"id": option.id, "label": option.label} for option in options],
        "source": SOURCE,
        "source_derivation_version": SOURCE_DERIVATION_VERSION,
        "source_policy_id": SOURCE_POLICY_ID,
        "source_policy_fingerprint": SOURCE_POLICY_FINGERPRINT,
        "current_source_evidence_refs": _serialize_result_refs(inspection.result),
        "self_model": _serialize_self_model(inspection.source.self_model),
    }
    digest = hashlib.sha256(_canonical_json(payload)).hexdigest()
    return f"sha256:{digest}"


def _serialize_self_model(result: SelfModelResult) -> dict[str, object]:
    claims = sorted(
        result.claims,
        key=lambda claim: (
            str(claim.supporting_evidence[0].note_id),
            claim.dimension.value,
            claim.claim,
        ),
    )
    return {
        "claims": [_serialize_claim(claim) for claim in claims],
        "eligible_evidence_count": result.eligible_evidence_count,
        "represented_evidence_count": result.represented_evidence_count,
        "derivation_version": result.derivation_version,
        "policy_fingerprint": result.policy_fingerprint,
    }


def _serialize_claim(claim: SelfModelClaim) -> dict[str, object]:
    return {
        "dimension": claim.dimension.value,
        "claim": claim.claim,
        "domain": claim.domain,
        "supporting_evidence": _serialize_evidence_refs(claim.supporting_evidence),
        "contradicting_evidence": _serialize_evidence_refs(claim.contradicting_evidence),
        "contextual_evidence": _serialize_evidence_refs(claim.contextual_evidence),
        "confidence": {
            "state": claim.confidence.state.value,
            "score": claim.confidence.score,
            "policy_version": claim.confidence.policy_version,
            "supporting_evidence_count": claim.confidence.supporting_evidence_count,
            "contradicting_evidence_count": claim.confidence.contradicting_evidence_count,
            "unknown_time_count": claim.confidence.unknown_time_count,
        },
        "temporal_context": {
            "earliest_known_evidence_at": _serialize_datetime_or_none(
                claim.temporal_context.earliest_known_evidence_at
            ),
            "latest_known_evidence_at": _serialize_datetime_or_none(
                claim.temporal_context.latest_known_evidence_at
            ),
            "known_evidence_count": claim.temporal_context.known_evidence_count,
            "unknown_evidence_count": claim.temporal_context.unknown_evidence_count,
        },
        "derivation_version": claim.derivation_version,
        "status": _serialize_status(claim.status),
    }


def _serialize_status(status: object) -> dict[str, object] | None:
    if status is None:
        return None
    # The current validator emits None, but retaining this bounded shape keeps
    # the hash helper fail-closed if a future validated DTO adds a status seam.
    code = getattr(status, "code", None)
    policy_version = getattr(status, "policy_version", None)
    if type(code) is not str or type(policy_version) is not str:
        raise ActiveLearningSourceInvalidError()
    return {"code": code, "policy_version": policy_version}


def _serialize_evidence_refs(refs: tuple[SelfModelEvidenceRef, ...]) -> list[object]:
    ordered = sorted(refs, key=lambda ref: str(ref.note_id))
    return [
        {
            "note_id": str(ref.note_id),
            "evidence_kind": ref.evidence_kind.value,
            "self_kind": ref.self_kind.value,
            "domain": ref.domain,
            "evidence_at": _serialize_evidence_at(ref.evidence_at),
            "evidence_at_precision": ref.evidence_at_precision.value,
            "related_note_ids": [str(note_id) for note_id in sorted(ref.related_note_ids, key=str)],
        }
        for ref in ordered
    ]


def _serialize_result_refs(result: SimulateMeResult) -> dict[str, object]:
    evidence = sorted(result.evidence_refs, key=lambda ref: str(ref.claim_id))
    contextual = sorted(result.contextual_evidence_refs, key=lambda ref: str(ref.claim_id))
    caveats = sorted(result.temporal_caveats, key=lambda caveat: str(caveat.claim_id))
    return {
        "kind": result.kind.value,
        "selected_option": (
            None
            if result.selected_option is None
            else {
                "id": result.selected_option.id,
                "label": normalize_simulate_me_text(result.selected_option.label, MAX_LABEL_BYTES),
            }
        ),
        "evidence_refs": [_serialize_simulate_evidence_ref(ref) for ref in evidence],
        "contextual_evidence_refs": [_serialize_simulate_contextual_ref(ref) for ref in contextual],
        "temporal_caveats": [_serialize_caveat(caveat) for caveat in caveats],
        "abstention_code": (
            None if result.abstention_code is None else result.abstention_code.value
        ),
        "derivation_version": result.derivation_version,
        "policy_id": result.policy_id,
        "policy_fingerprint": result.policy_fingerprint,
    }


def _serialize_simulate_evidence_ref(ref: SimulateMeEvidenceRef) -> dict[str, object]:
    return {
        "claim_id": str(ref.claim_id),
        "dimension": ref.dimension.value,
        "note_ids": [str(note_id) for note_id in sorted(ref.note_ids, key=str)],
        "evidence_at": _serialize_evidence_at(ref.evidence_at),
    }


def _serialize_simulate_contextual_ref(
    ref: SimulateMeContextualEvidenceRef,
) -> dict[str, object]:
    return {
        "claim_id": str(ref.claim_id),
        "dimension": ref.dimension.value,
        "note_ids": [str(note_id) for note_id in sorted(ref.note_ids, key=str)],
        "evidence_at": _serialize_evidence_at(ref.evidence_at),
    }


def _serialize_caveat(caveat: SimulateMeTemporalCaveat) -> dict[str, str]:
    return {"code": caveat.code.value, "claim_id": str(caveat.claim_id)}


def _serialize_evidence_at(value: EvidenceAt) -> str:
    if isinstance(value, str):
        return value
    assert isinstance(value, datetime)
    return value.isoformat()


def _serialize_datetime_or_none(value: datetime | None) -> str | None:
    return None if value is None else value.isoformat()


def _canonical_json(payload: object) -> bytes:
    return json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _serialize_candidate(candidate: QuestionCandidateV1) -> bytes:
    return _canonical_json(_candidate_payload(candidate))


def _candidate_payload(candidate: QuestionCandidateV1) -> dict[str, object]:
    _validate_candidate_fields(candidate)
    payload: dict[str, object] = {
        "basis_fingerprint": candidate.basis_fingerprint,
        "candidate_id": candidate.candidate_id,
        "contract_version": candidate.contract_version,
        "evidence_note_ids": [str(note_id) for note_id in candidate.evidence_note_ids],
        "expires_at": _utc_iso(candidate.expires_at),
        "issued_at": _utc_iso(candidate.issued_at),
        "kind": candidate.kind,
        "options": [{"id": option.id, "label": option.label} for option in candidate.options],
        "question": candidate.question,
        "reason_code": candidate.reason_code.value,
        "source": candidate.source,
        "source_derivation_version": candidate.source_derivation_version,
        "source_policy_fingerprint": candidate.source_policy_fingerprint,
        "source_policy_id": candidate.source_policy_id,
        "task": candidate.task,
    }
    return payload


def _validate_candidate_fields(candidate: object) -> None:
    if type(candidate) is not QuestionCandidateV1:
        raise ActiveLearningInvalidRequestError()
    assert isinstance(candidate, QuestionCandidateV1)
    if (
        candidate.contract_version != CONTRACT_VERSION
        or type(candidate.candidate_id) is not str
        or _CANDIDATE_ID_PATTERN.fullmatch(candidate.candidate_id) is None
        or candidate.kind != QUESTION_KIND
        or type(candidate.reason_code) is not QuestionReasonCodeV1
        or type(candidate.task) is not str
        or type(candidate.question) is not str
        or type(candidate.options) is not tuple
        or candidate.source != SOURCE
        or candidate.source_derivation_version != SOURCE_DERIVATION_VERSION
        or candidate.source_policy_id != SOURCE_POLICY_ID
        or candidate.source_policy_fingerprint != SOURCE_POLICY_FINGERPRINT
        or not _FINGERPRINT_PATTERN.fullmatch(candidate.source_policy_fingerprint)
        or type(candidate.evidence_note_ids) is not tuple
        or type(candidate.basis_fingerprint) is not str
        or _FINGERPRINT_PATTERN.fullmatch(candidate.basis_fingerprint) is None
    ):
        raise ActiveLearningInvalidRequestError()
    try:
        normalized_task = normalize_simulate_me_text(candidate.task, 4096)
    except Exception:
        raise ActiveLearningInvalidRequestError() from None
    if normalized_task != candidate.task:
        raise ActiveLearningInvalidRequestError()
    expected_question = _QUESTION_TEMPLATES[candidate.reason_code.value]
    if candidate.question != expected_question:
        raise ActiveLearningInvalidRequestError()
    if len(candidate.question.encode("utf-8")) > MAX_QUESTION_BYTES:
        raise ActiveLearningInvalidRequestError()
    if not 1 <= len(candidate.options) <= 8:
        raise ActiveLearningInvalidRequestError()
    option_ids: set[str] = set()
    for option in candidate.options:
        validate_question_option(option)
        if option.id in option_ids:
            raise ActiveLearningInvalidRequestError()
        option_ids.add(option.id)
    if (
        not 0 <= len(candidate.evidence_note_ids) <= MAX_EVIDENCE_NOTE_IDS
        or len(set(candidate.evidence_note_ids)) != len(candidate.evidence_note_ids)
        or any(
            type(note_id) is not UUID or note_id.version != 7
            for note_id in candidate.evidence_note_ids
        )
        or candidate.evidence_note_ids != tuple(sorted(candidate.evidence_note_ids, key=str))
    ):
        raise ActiveLearningInvalidRequestError()
    issued_at = _require_utc_datetime(candidate.issued_at)
    expires_at = _require_utc_datetime(candidate.expires_at)
    if expires_at <= issued_at or expires_at - issued_at != QUESTION_TTL:
        raise ActiveLearningInvalidRequestError()


def _validate_resolution_for_candidate(
    resolution: object,
    candidate: QuestionCandidateV1,
) -> QuestionResolutionV1:
    try:
        validated = validate_question_resolution(resolution)
    except ActiveLearningError:
        raise
    if validated.candidate_id != candidate.candidate_id:
        raise ActiveLearningInvalidAnswerError()
    if validated.disposition is QuestionDispositionV1.ANSWER:
        selected_id = validated.selected_option_id
        if selected_id is None or selected_id not in {option.id for option in candidate.options}:
            raise ActiveLearningInvalidAnswerError()
    return validated


def _same_candidate_content(left: QuestionCandidateV1, right: QuestionCandidateV1) -> bool:
    return (
        left.contract_version == right.contract_version
        and left.candidate_id == right.candidate_id
        and left.kind == right.kind
        and left.reason_code is right.reason_code
        and left.task == right.task
        and left.question == right.question
        and left.options == right.options
        and left.source == right.source
        and left.source_derivation_version == right.source_derivation_version
        and left.source_policy_id == right.source_policy_id
        and left.source_policy_fingerprint == right.source_policy_fingerprint
        and left.evidence_note_ids == right.evidence_note_ids
        and left.basis_fingerprint == right.basis_fingerprint
    )


def _validate_operation_state(
    state: object | None,
) -> ActiveLearningOperationStateV1 | None:
    if state is None:
        return None
    if type(state) is not ActiveLearningOperationStateV1:
        raise ActiveLearningInvalidRequestError()
    assert isinstance(state, ActiveLearningOperationStateV1)
    if type(state.terminal) is not bool:
        raise ActiveLearningInvalidRequestError()
    if state.candidate_id is None:
        if state.terminal:
            raise ActiveLearningInvalidRequestError()
        return state
    if (
        type(state.candidate_id) is not str
        or _CANDIDATE_ID_PATTERN.fullmatch(state.candidate_id) is None
    ):
        raise ActiveLearningInvalidRequestError()
    return state


def _normalize_utc_datetime(value: object) -> datetime:
    if not isinstance(value, datetime):
        raise ActiveLearningInvalidRequestError()
    try:
        offset = value.utcoffset()
        if offset is None:
            raise ActiveLearningInvalidRequestError()
        return value.astimezone(UTC)
    except ActiveLearningError:
        raise
    except Exception:
        raise ActiveLearningInvalidRequestError() from None


def _require_utc_datetime(value: object) -> datetime:
    normalized = _normalize_utc_datetime(value)
    if value != normalized:
        raise ActiveLearningInvalidRequestError()
    return normalized


def _utc_iso(value: datetime) -> str:
    return _normalize_utc_datetime(value).isoformat(timespec="microseconds")


def _normalize_error_code(code: ActiveLearningErrorCodeV1 | str) -> ActiveLearningErrorCodeV1:
    if isinstance(code, ActiveLearningErrorCodeV1):
        return code
    try:
        return ActiveLearningErrorCodeV1(code)
    except TypeError, ValueError:
        return ActiveLearningErrorCodeV1.SOURCE_INVALID


# These aliases keep the public names close to the normative contract while
# retaining the explicit v1 suffix on the implementation types.
QuestionReasonCode = QuestionReasonCodeV1
ActiveLearningResult = ActiveLearningResultV1
QuestionCandidate = QuestionCandidateV1
QuestionResolution = QuestionResolutionV1
AnswerCapture = AnswerCaptureV1
