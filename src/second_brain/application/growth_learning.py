"""Provider-free Growth Learning / Question runtime v1.

The module is deliberately a small application boundary around the existing
Stage 11B read model.  It derives one ephemeral candidate from a fresh,
server-owned ``GrowthEngineResultV1`` and never owns a vault, mapping, or
Personal Memory writer.
"""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Final, Protocol, cast
from uuid import UUID

from second_brain.application.growth import (
    GROWTH_CONTRACT_VERSION,
    GROWTH_DERIVATION_VERSION,
    GROWTH_MAX_RESULT_BYTES,
    GROWTH_POLICY_FINGERPRINT,
    GROWTH_POLICY_ID,
    GrowthBehavioralEvidenceInsufficientError,
    GrowthBehavioralSourceUnavailableError,
    GrowthBehavioralStateNotComparableError,
    GrowthEngineRequestV1,
    GrowthEngineResultV1,
    GrowthError,
    GrowthErrorCode,
    GrowthGoalChoiceMappingReviewProjectionV1,
    GrowthGoalChoiceMappingSelectorV1,
    GrowthGoalMissingError,
    GrowthGoalRelationResultV1,
    GrowthGoalRelationV1,
    GrowthGoalSelectionModeV1,
    GrowthGoalSelectionRequiredError,
    GrowthGoalSelectionV1,
    GrowthGoalSourceChangedError,
    GrowthGoalSourceUnavailableError,
    GrowthMappingReviewRequestV1,
    GrowthMappingStoreCorruptError,
    GrowthMappingStoreUnavailableError,
    GrowthPolicyMismatchError,
    GrowthRelationStateV1,
    GrowthResultTooLargeError,
    canonical_growth_json,
    validate_growth_engine_result,
    validate_growth_hash,
)
from second_brain.domain.models import parse_uuid7

type GrowthLearningClock = Callable[[], datetime]

GROWTH_LEARNING_CONTRACT_VERSION: Final[str] = "growth-learning-v1"
GROWTH_LEARNING_DERIVATION_VERSION: Final[str] = "growth-learning-derivation-v1"
GROWTH_LEARNING_POLICY_ID: Final[str] = "growth-learning-foreground-question-v1"
GROWTH_LEARNING_POLICY_CANONICAL_JSON: Final[str] = (
    '{"answer":"explicit-owner-editable-ephemeral-v1",'
    '"anti_annoyance":"foreground-one-candidate-v1",'
    '"candidate_limit":1,'
    '"contract_version":"growth-learning-v1",'
    '"dedupe":"basis-fingerprint-page-memory-v1",'
    '"derivation_version":"growth-learning-derivation-v1",'
    '"experiments":"deferred-v1",'
    '"foreground":"explicit-owner-action-v1",'
    '"invalidation":"current-growth-source-exact-v1",'
    '"persistence":"ephemeral-no-store-v1",'
    '"policy_id":"growth-learning-foreground-question-v1",'
    '"source":"growth-engine-current-result-v1",'
    '"ttl_seconds":600,'
    '"triggers":"mapping-conflict-mixed-changed-insufficient-v1",'
    '"version":"1",'
    '"wording":"fixed-provider-free-russian-v1"}'
)
GROWTH_LEARNING_POLICY_FINGERPRINT: Final[str] = (
    "sha256:9b20c981ee4137b340ee2b183e32faec616dd258789dc84329a900bab0f8e829"
)

GROWTH_LEARNING_QUESTION_TTL_SECONDS: Final[int] = 600
GROWTH_LEARNING_QUESTION_TTL: Final[timedelta] = timedelta(
    seconds=GROWTH_LEARNING_QUESTION_TTL_SECONDS
)
GROWTH_LEARNING_MAX_CANDIDATE_BYTES: Final[int] = 16 * 1024
GROWTH_LEARNING_MAX_QUESTION_BYTES: Final[int] = 1024
GROWTH_LEARNING_MAX_RESULT_BYTES: Final[int] = 32 * 1024
GROWTH_LEARNING_MAX_ANSWER_BYTES: Final[int] = 4096

# Short aliases match the naming vocabulary used by the other application
# cores while keeping the Learning namespace visibly separate from Stage 8.
CONTRACT_VERSION: Final[str] = GROWTH_LEARNING_CONTRACT_VERSION
DERIVATION_VERSION: Final[str] = GROWTH_LEARNING_DERIVATION_VERSION
POLICY_ID: Final[str] = GROWTH_LEARNING_POLICY_ID
POLICY_CANONICAL_JSON: Final[str] = GROWTH_LEARNING_POLICY_CANONICAL_JSON
POLICY_FINGERPRINT: Final[str] = GROWTH_LEARNING_POLICY_FINGERPRINT
QUESTION_TTL_SECONDS: Final[int] = GROWTH_LEARNING_QUESTION_TTL_SECONDS
QUESTION_TTL: Final[timedelta] = GROWTH_LEARNING_QUESTION_TTL
MAX_CANDIDATE_BYTES: Final[int] = GROWTH_LEARNING_MAX_CANDIDATE_BYTES
MAX_QUESTION_BYTES: Final[int] = GROWTH_LEARNING_MAX_QUESTION_BYTES
MAX_RESULT_BYTES: Final[int] = GROWTH_LEARNING_MAX_RESULT_BYTES
MAX_ANSWER_BYTES: Final[int] = GROWTH_LEARNING_MAX_ANSWER_BYTES

_CANDIDATE_ID_PATTERN: Final[re.Pattern[str]] = re.compile(r"gl1:[0-9a-f]{64}\Z", re.ASCII)
_HASH_PATTERN: Final[re.Pattern[str]] = re.compile(r"sha256:[0-9a-f]{64}\Z", re.ASCII)
_EMPTY_SORT_KEY: Final[tuple[int, str]] = (1, "")


class GrowthLearningKindV1(StrEnum):
    """The only four question kinds in the closed v1 taxonomy."""

    RELATION_REVIEW = "relation_review"
    REFLECTION = "reflection"
    CONTEXT_CLARIFICATION = "context_clarification"
    EVIDENCE_CLARIFICATION = "evidence_clarification"


class GrowthLearningReasonCodeV1(StrEnum):
    """The only five source-backed actionable Growth reasons."""

    MISSING_GOAL_MAPPING = "missing_goal_mapping"
    EXPLICIT_GOAL_CONFLICT = "explicit_goal_conflict"
    MIXED_BEHAVIOR = "mixed_behavior"
    CHANGED_BEHAVIOR = "changed_behavior"
    BEHAVIORAL_EVIDENCE_INSUFFICIENT = "behavioral_evidence_insufficient"


class GrowthLearningStatusV1(StrEnum):
    """The two normal result statuses."""

    CANDIDATE = "candidate"
    NO_CANDIDATE = "no_candidate"


class GrowthLearningNoCandidateCodeV1(StrEnum):
    """Closed, non-error outcomes for an explicit foreground request."""

    QUESTIONS_DISABLED = "QUESTIONS_DISABLED"
    GOAL_SOURCE_MISSING = "GOAL_SOURCE_MISSING"
    GOAL_SELECTION_REQUIRED = "GOAL_SELECTION_REQUIRED"
    NO_ACTIONABLE_GROWTH_GAP = "NO_ACTIONABLE_GROWTH_GAP"
    GROWTH_STATE_NOT_COMPARABLE = "GROWTH_STATE_NOT_COMPARABLE"
    UNSUPPORTED_GROWTH_STATE = "UNSUPPORTED_GROWTH_STATE"
    CANDIDATE_ALREADY_PRESENT_IN_PAGE_MEMORY = "CANDIDATE_ALREADY_PRESENT_IN_PAGE_MEMORY"


class GrowthLearningDispositionV1(StrEnum):
    """Explicit owner controls; none is a write operation."""

    IGNORE = "ignore"
    REJECT = "reject"
    REVIEW = "review"
    ANSWER = "answer"


class GrowthLearningHandoffKindV1(StrEnum):
    """Existing review boundaries that Learning may request, never own."""

    RELATION_REVIEW = "relation_review"
    PERSONAL_MEMORY_REVIEW = "personal_memory_review"


class GrowthLearningErrorCodeV1(StrEnum):
    """The exact safe runtime error vocabulary from the contract."""

    INVALID_REQUEST = "INVALID_REQUEST"
    GROWTH_SOURCE_UNAVAILABLE = "GROWTH_SOURCE_UNAVAILABLE"
    GOAL_SOURCE_CHANGED = "GOAL_SOURCE_CHANGED"
    BEHAVIORAL_SOURCE_CHANGED = "BEHAVIORAL_SOURCE_CHANGED"
    MAPPING_SOURCE_UNAVAILABLE = "MAPPING_SOURCE_UNAVAILABLE"
    MAPPING_STORE_CORRUPT = "MAPPING_STORE_CORRUPT"
    POLICY_MISMATCH = "POLICY_MISMATCH"
    STALE_CANDIDATE = "STALE_CANDIDATE"
    CANDIDATE_EXPIRED = "CANDIDATE_EXPIRED"
    CANDIDATE_ALREADY_RESOLVED = "CANDIDATE_ALREADY_RESOLVED"
    INVALID_RESOLUTION = "INVALID_RESOLUTION"
    ANSWER_TOO_LARGE = "ANSWER_TOO_LARGE"
    RESULT_TOO_LARGE = "RESULT_TOO_LARGE"
    INTERNAL_CONTRACT_VIOLATION = "INTERNAL_CONTRACT_VIOLATION"


# Descriptive aliases are intentionally value aliases, not second enum sets.
GrowthLearningKind = GrowthLearningKindV1
GrowthLearningReasonCode = GrowthLearningReasonCodeV1
GrowthLearningStatus = GrowthLearningStatusV1
GrowthLearningNoCandidateCode = GrowthLearningNoCandidateCodeV1
GrowthLearningDisposition = GrowthLearningDispositionV1
GrowthLearningHandoffKind = GrowthLearningHandoffKindV1
GrowthLearningErrorCode = GrowthLearningErrorCodeV1


class GrowthLearningEnginePort(Protocol):
    """The only runtime dependency needed to rebuild Stage 11B."""

    def execute(self, request: GrowthEngineRequestV1) -> GrowthEngineResultV1:
        """Return a fresh current Stage 11B result."""


GrowthLearningEngine = GrowthLearningEnginePort


_QUESTION_TEMPLATES: Final[dict[GrowthLearningReasonCodeV1, str]] = {
    GrowthLearningReasonCodeV1.MISSING_GOAL_MAPPING: (
        "Для текущего наблюдаемого варианта ещё не задано, как он относится к выбранной цели. "
        "Хочешь проверить эту связь?"
    ),
    GrowthLearningReasonCodeV1.EXPLICIT_GOAL_CONFLICT: (
        "Для текущего варианта ранее была явно подтверждена связь «конфликтует с целью». "
        "Хочешь уточнить контекст или пересмотреть эту связь?"
    ),
    GrowthLearningReasonCodeV1.MIXED_BEHAVIOR: (
        "В сопоставимых ситуациях были разные варианты выбора. Хочешь добавить контекст, "
        "который помогает различать эти случаи?"
    ),
    GrowthLearningReasonCodeV1.CHANGED_BEHAVIOR: (
        "В историческом и текущем окнах наблюдались разные варианты выбора. Хочешь уточнить "
        "контекст этого различия?"
    ),
    GrowthLearningReasonCodeV1.BEHAVIORAL_EVIDENCE_INSUFFICIENT: (
        "Пока недостаточно сопоставимых решений для устойчивого наблюдаемого паттерна. "
        "Хочешь добавить контекст?"
    ),
}

_STATE_DERIVATION: Final[
    dict[GrowthRelationStateV1, tuple[GrowthLearningKindV1, GrowthLearningReasonCodeV1]]
] = {
    GrowthRelationStateV1.GOAL_MAPPING_MISSING: (
        GrowthLearningKindV1.RELATION_REVIEW,
        GrowthLearningReasonCodeV1.MISSING_GOAL_MAPPING,
    ),
    GrowthRelationStateV1.CONFLICTS_WITH_GOAL: (
        GrowthLearningKindV1.REFLECTION,
        GrowthLearningReasonCodeV1.EXPLICIT_GOAL_CONFLICT,
    ),
    GrowthRelationStateV1.MIXED_BEHAVIOR: (
        GrowthLearningKindV1.CONTEXT_CLARIFICATION,
        GrowthLearningReasonCodeV1.MIXED_BEHAVIOR,
    ),
    GrowthRelationStateV1.CHANGED_BEHAVIOR: (
        GrowthLearningKindV1.CONTEXT_CLARIFICATION,
        GrowthLearningReasonCodeV1.CHANGED_BEHAVIOR,
    ),
    GrowthRelationStateV1.BEHAVIORAL_EVIDENCE_INSUFFICIENT: (
        GrowthLearningKindV1.EVIDENCE_CLARIFICATION,
        GrowthLearningReasonCodeV1.BEHAVIORAL_EVIDENCE_INSUFFICIENT,
    ),
}
_PRECEDENCE: Final[tuple[GrowthRelationStateV1, ...]] = tuple(_STATE_DERIVATION)

_ERROR_MESSAGES: Final[dict[GrowthLearningErrorCodeV1, str]] = {
    GrowthLearningErrorCodeV1.INVALID_REQUEST: "Growth Learning request failed validation",
    GrowthLearningErrorCodeV1.GROWTH_SOURCE_UNAVAILABLE: "current Growth source is unavailable",
    GrowthLearningErrorCodeV1.GOAL_SOURCE_CHANGED: "current Goal source changed",
    GrowthLearningErrorCodeV1.BEHAVIORAL_SOURCE_CHANGED: "current behavioral source changed",
    GrowthLearningErrorCodeV1.MAPPING_SOURCE_UNAVAILABLE: "current mapping source is unavailable",
    GrowthLearningErrorCodeV1.MAPPING_STORE_CORRUPT: "current mapping store is corrupt",
    GrowthLearningErrorCodeV1.POLICY_MISMATCH: "Growth Learning policy binding is invalid",
    GrowthLearningErrorCodeV1.STALE_CANDIDATE: "Growth Learning candidate is stale",
    GrowthLearningErrorCodeV1.CANDIDATE_EXPIRED: "Growth Learning candidate has expired",
    GrowthLearningErrorCodeV1.CANDIDATE_ALREADY_RESOLVED: (
        "Growth Learning candidate is already resolved"
    ),
    GrowthLearningErrorCodeV1.INVALID_RESOLUTION: "Growth Learning resolution failed validation",
    GrowthLearningErrorCodeV1.ANSWER_TOO_LARGE: "Growth Learning answer exceeds its byte limit",
    GrowthLearningErrorCodeV1.RESULT_TOO_LARGE: "Growth Learning result exceeds its byte limit",
    GrowthLearningErrorCodeV1.INTERNAL_CONTRACT_VIOLATION: (
        "Growth Learning source violated its application contract"
    ),
}


class GrowthLearningError(RuntimeError):
    """Safe error without private source context, paths, or exception details."""

    def __init__(self, code: GrowthLearningErrorCodeV1 | str) -> None:
        normalized = _normalize_error_code(code)
        self.code = normalized.value
        self.message = _ERROR_MESSAGES[normalized]
        super().__init__(self.message)

    def as_dict(self) -> dict[str, str]:
        """Return the bounded public error projection."""

        return {"code": self.code, "message": self.message}


class GrowthLearningInvalidRequestError(GrowthLearningError):
    def __init__(self) -> None:
        super().__init__(GrowthLearningErrorCodeV1.INVALID_REQUEST)


class GrowthLearningGrowthSourceUnavailableError(GrowthLearningError):
    def __init__(self) -> None:
        super().__init__(GrowthLearningErrorCodeV1.GROWTH_SOURCE_UNAVAILABLE)


class GrowthLearningGoalSourceChangedError(GrowthLearningError):
    def __init__(self) -> None:
        super().__init__(GrowthLearningErrorCodeV1.GOAL_SOURCE_CHANGED)


class GrowthLearningBehavioralSourceChangedError(GrowthLearningError):
    def __init__(self) -> None:
        super().__init__(GrowthLearningErrorCodeV1.BEHAVIORAL_SOURCE_CHANGED)


class GrowthLearningMappingSourceUnavailableError(GrowthLearningError):
    def __init__(self) -> None:
        super().__init__(GrowthLearningErrorCodeV1.MAPPING_SOURCE_UNAVAILABLE)


class GrowthLearningMappingStoreCorruptError(GrowthLearningError):
    def __init__(self) -> None:
        super().__init__(GrowthLearningErrorCodeV1.MAPPING_STORE_CORRUPT)


class GrowthLearningPolicyMismatchError(GrowthLearningError):
    def __init__(self) -> None:
        super().__init__(GrowthLearningErrorCodeV1.POLICY_MISMATCH)


class GrowthLearningStaleCandidateError(GrowthLearningError):
    def __init__(self) -> None:
        super().__init__(GrowthLearningErrorCodeV1.STALE_CANDIDATE)


class GrowthLearningCandidateExpiredError(GrowthLearningError):
    def __init__(self) -> None:
        super().__init__(GrowthLearningErrorCodeV1.CANDIDATE_EXPIRED)


class GrowthLearningCandidateAlreadyResolvedError(GrowthLearningError):
    def __init__(self) -> None:
        super().__init__(GrowthLearningErrorCodeV1.CANDIDATE_ALREADY_RESOLVED)


class GrowthLearningInvalidResolutionError(GrowthLearningError):
    def __init__(self) -> None:
        super().__init__(GrowthLearningErrorCodeV1.INVALID_RESOLUTION)


class GrowthLearningAnswerTooLargeError(GrowthLearningError):
    def __init__(self) -> None:
        super().__init__(GrowthLearningErrorCodeV1.ANSWER_TOO_LARGE)


class GrowthLearningResultTooLargeError(GrowthLearningError):
    def __init__(self) -> None:
        super().__init__(GrowthLearningErrorCodeV1.RESULT_TOO_LARGE)


class GrowthLearningInternalContractViolationError(GrowthLearningError):
    def __init__(self) -> None:
        super().__init__(GrowthLearningErrorCodeV1.INTERNAL_CONTRACT_VIOLATION)


# Compatibility spellings follow the shorter names used by Stage 8 callers.
GrowthLearningSourceUnavailableError = GrowthLearningGrowthSourceUnavailableError
GrowthLearningCandidateStaleError = GrowthLearningStaleCandidateError


@dataclass(frozen=True, slots=True)
class GrowthLearningRequestV1:
    """The bounded explicit request: one selected Goal UUID, no source DTO."""

    contract_version: str = GROWTH_LEARNING_CONTRACT_VERSION
    goal_source_uuid: UUID | str | None = None

    @classmethod
    def from_dict(cls, value: object) -> GrowthLearningRequestV1:
        if not isinstance(value, Mapping) or set(value) != {
            "contract_version",
            "goal_source_uuid",
        }:
            raise GrowthLearningInvalidRequestError()
        raw_uuid = value["goal_source_uuid"]
        try:
            normalized_uuid = None if raw_uuid is None else _normalize_uuid7(raw_uuid)
        except ValueError:
            raise GrowthLearningInvalidRequestError() from None
        request = cls(
            contract_version=cast(str, value["contract_version"]),
            goal_source_uuid=normalized_uuid,
        )
        return validate_growth_learning_request(request)

    def as_dict(self) -> dict[str, object]:
        validated = validate_growth_learning_request(self)
        return {
            "contract_version": validated.contract_version,
            "goal_source_uuid": (
                str(validated.goal_source_uuid) if validated.goal_source_uuid is not None else None
            ),
        }


@dataclass(frozen=True, slots=True)
class GrowthLearningSourceV1:
    """Optional pure-core wrapper for one already server-owned Growth result."""

    growth_result: GrowthEngineResultV1


@dataclass(frozen=True, slots=True)
class GrowthLearningCandidateV1:
    """One raw-label-free, rebuildable, ephemeral Growth question candidate."""

    contract_version: str
    derivation_version: str
    candidate_id: str
    kind: GrowthLearningKindV1
    reason_code: GrowthLearningReasonCodeV1
    growth_contract_version: str
    growth_derivation_version: str
    growth_policy_id: str
    growth_policy_fingerprint: str
    goal_source_uuid: UUID
    goal_identity_fingerprint: str
    growth_state: GrowthRelationStateV1
    cohort_fingerprint: str | None
    behavioral_option_fingerprint: str | None
    behavioral_reference_fingerprint: str | None
    mapping_id: UUID | None
    mapping_fingerprint: str | None
    question: str
    basis_fingerprint: str
    issued_at: datetime
    expires_at: datetime

    def as_dict(self) -> dict[str, object]:
        return _candidate_payload(validate_growth_learning_candidate(self))

    def to_json(self) -> str:
        return _canonical_json(self.as_dict()).decode("utf-8")


@dataclass(frozen=True, slots=True)
class GrowthLearningResultV1:
    """Exactly one candidate or one bounded normal no-candidate outcome."""

    contract_version: str
    status: GrowthLearningStatusV1
    candidate: GrowthLearningCandidateV1 | None
    no_candidate_code: GrowthLearningNoCandidateCodeV1 | None

    def as_dict(self) -> dict[str, object]:
        return _result_payload(validate_growth_learning_result(self))

    def to_json(self) -> str:
        return _canonical_json(self.as_dict()).decode("utf-8")


@dataclass(frozen=True, slots=True)
class GrowthLearningResolutionV1:
    """An explicit terminal control with an optional editable answer."""

    candidate_id: str
    disposition: GrowthLearningDispositionV1
    answer: str | None = None

    @property
    def answer_text(self) -> str | None:
        """Compatibility spelling for callers that name the editable text."""

        return self.answer

    @classmethod
    def from_dict(cls, value: object) -> GrowthLearningResolutionV1:
        if not isinstance(value, Mapping) or set(value) != {
            "candidate_id",
            "disposition",
            "answer",
        }:
            raise GrowthLearningInvalidResolutionError()
        raw_disposition = value["disposition"]
        try:
            disposition = (
                raw_disposition
                if type(raw_disposition) is GrowthLearningDispositionV1
                else GrowthLearningDispositionV1(cast(str, raw_disposition))
            )
        except TypeError, ValueError:
            raise GrowthLearningInvalidResolutionError() from None
        resolution = cls(
            candidate_id=cast(str, value["candidate_id"]),
            disposition=disposition,
            answer=cast(str | None, value["answer"]),
        )
        return validate_growth_learning_resolution(resolution)

    def as_dict(self) -> dict[str, object]:
        validated = validate_growth_learning_resolution(self)
        return {
            "answer": validated.answer,
            "candidate_id": validated.candidate_id,
            "disposition": validated.disposition.value,
        }


@dataclass(frozen=True, slots=True)
class GrowthLearningAnswerDraftV1:
    """Only an editable, non-canonical owner draft; it is not a NoteDraft."""

    candidate_id: str
    text: str

    @property
    def answer(self) -> str:
        return self.text


@dataclass(frozen=True, slots=True)
class GrowthLearningHandoffV1:
    """A safe pointer to an existing review boundary, never a write receipt."""

    candidate_id: str
    kind: GrowthLearningHandoffKindV1
    selector: GrowthGoalChoiceMappingSelectorV1 | None = None
    review_projection: GrowthGoalChoiceMappingReviewProjectionV1 | None = None

    def as_dict(self) -> dict[str, object]:
        """Serialize only non-private handoff identity, never labels/projection text."""

        if type(self.kind) is not GrowthLearningHandoffKindV1:
            raise GrowthLearningInvalidRequestError()
        if (
            type(self.candidate_id) is not str
            or _CANDIDATE_ID_PATTERN.fullmatch(self.candidate_id) is None
        ):
            raise GrowthLearningInvalidRequestError()
        if (
            self.selector is not None
            and type(self.selector) is not GrowthGoalChoiceMappingSelectorV1
        ):
            raise GrowthLearningInvalidRequestError()
        if (
            self.review_projection is not None
            and type(self.review_projection) is not GrowthGoalChoiceMappingReviewProjectionV1
        ):
            raise GrowthLearningInvalidRequestError()
        return {
            "candidate_id": self.candidate_id,
            "kind": self.kind.value,
            "selector": self.selector.as_dict() if self.selector is not None else None,
        }


@dataclass(frozen=True, slots=True)
class GrowthLearningOperationStateV1:
    """Only page/request memory for the one-candidate anti-annoyance rule."""

    candidate_id: str | None = None
    terminal: bool = False

    def with_candidate(
        self, candidate: GrowthLearningCandidateV1
    ) -> GrowthLearningOperationStateV1:
        validate_growth_learning_candidate(candidate)
        return GrowthLearningOperationStateV1(candidate_id=candidate.candidate_id, terminal=False)

    def after_resolution(self, candidate_id: str) -> GrowthLearningOperationStateV1:
        if type(candidate_id) is not str or _CANDIDATE_ID_PATTERN.fullmatch(candidate_id) is None:
            raise GrowthLearningInvalidRequestError()
        return GrowthLearningOperationStateV1(candidate_id=candidate_id, terminal=True)


@dataclass(frozen=True, slots=True)
class GrowthLearningResolutionResultV1:
    """A pure resolution transition plus disposable answer/handoff projections."""

    candidate_id: str
    disposition: GrowthLearningDispositionV1
    answer_draft: GrowthLearningAnswerDraftV1 | None
    handoff: GrowthLearningHandoffV1 | None
    next_state: GrowthLearningOperationStateV1

    @property
    def answer(self) -> GrowthLearningAnswerDraftV1 | None:
        return self.answer_draft

    @property
    def review_handoff(self) -> GrowthLearningHandoffV1 | None:
        return self.handoff


@dataclass(slots=True)
class GrowthLearningOperationV1:
    """Pure in-memory operation helper for a caller-owned current source."""

    state: GrowthLearningOperationStateV1 = field(default_factory=GrowthLearningOperationStateV1)

    def build(
        self,
        source: object,
        *,
        questions_enabled: object = True,
        now: object,
    ) -> GrowthLearningResultV1:
        result = build_growth_learning_question(
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
    ) -> GrowthLearningResolutionResultV1:
        result = resolve_growth_learning_question(
            source,
            candidate,
            resolution,
            now=now,
            operation_state=self.state,
        )
        self.state = result.next_state
        return result


@dataclass(frozen=True, slots=True)
class BuildGrowthLearningQuestion:
    """Explicit foreground application wrapper around a Stage 11B engine."""

    growth_engine: GrowthLearningEngine | None = None
    clock: GrowthLearningClock = lambda: datetime.now(UTC)

    def execute(
        self,
        request: object,
        *,
        questions_enabled: object = True,
        operation_state: object | None = None,
        now: object | None = None,
    ) -> GrowthLearningResultV1:
        """Rebuild current Growth once for an explicit Goal request."""

        validated_state = _validate_operation_state(operation_state)
        if type(questions_enabled) is not bool:
            raise GrowthLearningInvalidRequestError()
        issued_at = self._now(now)
        if _is_growth_source(request):
            return build_growth_learning_question(
                request,
                questions_enabled=questions_enabled,
                now=issued_at,
                operation_state=validated_state,
            )
        request = _coerce_learning_request(request)
        if not questions_enabled:
            return _no_candidate(GrowthLearningNoCandidateCodeV1.QUESTIONS_DISABLED)
        if validated_state is not None and validated_state.candidate_id is not None:
            return _no_candidate(
                GrowthLearningNoCandidateCodeV1.CANDIDATE_ALREADY_PRESENT_IN_PAGE_MEMORY
            )
        result, no_candidate = self._fresh_result(request, resolving=False)
        if no_candidate is not None:
            return _no_candidate(no_candidate)
        assert result is not None
        return build_growth_learning_question(
            result,
            questions_enabled=True,
            now=issued_at,
            operation_state=validated_state,
        )

    def resolve(
        self,
        request: object,
        candidate: object,
        resolution: object,
        *,
        operation_state: object | None = None,
        now: object | None = None,
    ) -> GrowthLearningResolutionResultV1:
        """Freshly revalidate then perform only an ephemeral resolution."""

        current_time = self._now(now)
        if _is_growth_source(request):
            return resolve_growth_learning_question(
                request,
                candidate,
                resolution,
                now=current_time,
                operation_state=operation_state,
            )
        learning_request = _coerce_learning_request(request)
        result, no_candidate = self._fresh_result(learning_request, resolving=True)
        if no_candidate is not None or result is None:
            raise GrowthLearningStaleCandidateError()
        resolved = resolve_growth_learning_question(
            result,
            candidate,
            resolution,
            now=current_time,
            operation_state=operation_state,
        )
        if resolved.handoff is not None:
            return self._complete_review_handoff(result, candidate, resolved)
        return resolved

    def _now(self, supplied: object | None) -> datetime:
        value = self.clock() if supplied is None else supplied
        return _normalize_request_time(value)

    def _fresh_result(
        self,
        request: GrowthLearningRequestV1,
        *,
        resolving: bool,
    ) -> tuple[GrowthEngineResultV1 | None, GrowthLearningNoCandidateCodeV1 | None]:
        if self.growth_engine is None or not callable(getattr(self.growth_engine, "execute", None)):
            raise GrowthLearningInvalidRequestError()
        if request.goal_source_uuid is None:
            return None, GrowthLearningNoCandidateCodeV1.GOAL_SELECTION_REQUIRED
        growth_request = GrowthEngineRequestV1(
            contract_version=GROWTH_CONTRACT_VERSION,
            selection=GrowthGoalSelectionV1(
                GrowthGoalSelectionModeV1.SELECTED_GOAL,
                cast(UUID, request.goal_source_uuid),
            ),
            max_results=200,
            max_result_bytes=GROWTH_MAX_RESULT_BYTES,
        )
        try:
            result = self.growth_engine.execute(growth_request)
        except GrowthError as error:
            no_candidate = _growth_no_candidate(error, resolving=resolving)
            if no_candidate is not None:
                return None, no_candidate
            raise _translate_growth_error(error, resolving=resolving) from None
        except Exception:
            raise GrowthLearningInternalContractViolationError() from None
        try:
            validated_result = validate_growth_learning_source(result)
            if (
                validated_result.selection_mode is not GrowthGoalSelectionModeV1.SELECTED_GOAL
                or validated_result.selected_goal_source_uuid != request.goal_source_uuid
            ):
                raise GrowthLearningGoalSourceChangedError()
            return validated_result, None
        except GrowthLearningError:
            raise
        except Exception:
            raise GrowthLearningInternalContractViolationError() from None

    def _complete_review_handoff(
        self,
        current_result: GrowthEngineResultV1,
        candidate: object,
        resolved: GrowthLearningResolutionResultV1,
    ) -> GrowthLearningResolutionResultV1:
        handoff = resolved.handoff
        if handoff is None:
            return resolved
        if handoff.kind is not GrowthLearningHandoffKindV1.RELATION_REVIEW:
            return resolved
        validated_candidate = _candidate_or_stale(candidate)
        relation = _find_candidate_relation(current_result, validated_candidate)
        option = relation.behavioral_option
        cohort = relation.cohort_fingerprint
        if option is None or cohort is None:
            raise GrowthLearningInternalContractViolationError()
        selector = GrowthGoalChoiceMappingSelectorV1(
            source_note_uuid=validated_candidate.goal_source_uuid,
            behavioral_cohort_fingerprint=cohort,
            behavioral_option_index=option.option_index,
            behavioral_option_fingerprint=option.option_fingerprint,
        )
        projection: GrowthGoalChoiceMappingReviewProjectionV1 | None = None
        review = getattr(self.growth_engine, "review", None)
        if callable(review):
            try:
                projection = review(GrowthMappingReviewRequestV1(selector=selector))
            except GrowthError as error:
                raise _translate_growth_error(error, resolving=True) from None
            except Exception:
                raise GrowthLearningInternalContractViolationError() from None
            if type(projection) is not GrowthGoalChoiceMappingReviewProjectionV1:
                raise GrowthLearningInternalContractViolationError()
        completed = replace(
            handoff,
            selector=selector,
            review_projection=projection,
        )
        return replace(resolved, handoff=completed)


@dataclass(slots=True)
class GrowthLearningRuntimeOperationV1:
    """Convenience operation that keeps only current page state."""

    builder: BuildGrowthLearningQuestion
    state: GrowthLearningOperationStateV1 = field(default_factory=GrowthLearningOperationStateV1)

    def build(
        self,
        request: object,
        *,
        questions_enabled: object = True,
        now: object | None = None,
    ) -> GrowthLearningResultV1:
        result = self.builder.execute(
            request,
            questions_enabled=questions_enabled,
            operation_state=self.state,
            now=now,
        )
        if result.candidate is not None:
            self.state = self.state.with_candidate(result.candidate)
        return result

    def resolve(
        self,
        request: object,
        candidate: object,
        resolution: object,
        *,
        now: object | None = None,
    ) -> GrowthLearningResolutionResultV1:
        result = self.builder.resolve(
            request,
            candidate,
            resolution,
            operation_state=self.state,
            now=now,
        )
        self.state = result.next_state
        return result


GrowthLearningApplicationOperationV1 = GrowthLearningRuntimeOperationV1


def validate_growth_learning_policy() -> str:
    """Compute and verify the exact policy fingerprint from canonical JSON."""

    try:
        parsed = json.loads(GROWTH_LEARNING_POLICY_CANONICAL_JSON)
    except TypeError, ValueError, UnicodeError:
        raise GrowthLearningPolicyMismatchError() from None
    if (
        not isinstance(parsed, dict)
        or GROWTH_LEARNING_POLICY_CANONICAL_JSON.startswith("\ufeff")
        or GROWTH_LEARNING_POLICY_CANONICAL_JSON.endswith(("\n", "\r"))
    ):
        raise GrowthLearningPolicyMismatchError()
    fingerprint = (
        "sha256:"
        + hashlib.sha256(GROWTH_LEARNING_POLICY_CANONICAL_JSON.encode("utf-8")).hexdigest()
    )
    if fingerprint != GROWTH_LEARNING_POLICY_FINGERPRINT:
        raise GrowthLearningPolicyMismatchError()
    if parsed.get("policy_id") != GROWTH_LEARNING_POLICY_ID:
        raise GrowthLearningPolicyMismatchError()
    return fingerprint


def validate_growth_learning_request(value: object) -> GrowthLearningRequestV1:
    """Validate the exact explicit Goal request."""

    if type(value) is not GrowthLearningRequestV1:
        raise GrowthLearningInvalidRequestError()
    request = value
    if request.contract_version != GROWTH_LEARNING_CONTRACT_VERSION:
        raise GrowthLearningInvalidRequestError()
    if request.goal_source_uuid is not None:
        try:
            normalized = _normalize_uuid7(request.goal_source_uuid)
        except ValueError:
            raise GrowthLearningInvalidRequestError() from None
        if request.goal_source_uuid != normalized:
            raise GrowthLearningInvalidRequestError()
    return request


def validate_growth_learning_source(value: object) -> GrowthEngineResultV1:
    """Validate the current server-owned Stage 11B result without rebuilding it."""

    if type(value) is GrowthLearningSourceV1:
        value = value.growth_result
    if type(value) is not GrowthEngineResultV1:
        raise GrowthLearningInvalidRequestError()
    try:
        validate_growth_engine_result(value)
    except GrowthResultTooLargeError:
        raise GrowthLearningResultTooLargeError() from None
    except TypeError, ValueError, GrowthError:
        raise GrowthLearningInternalContractViolationError() from None
    try:
        validate_growth_learning_policy()
    except GrowthLearningError:
        raise
    return value


def build_growth_learning_question(
    source: object,
    *,
    questions_enabled: object = True,
    now: object,
    operation_state: object | None = None,
) -> GrowthLearningResultV1:
    """Derive zero or one candidate from one current validated Growth result."""

    validated_source = validate_growth_learning_source(source)
    state = _validate_operation_state(operation_state)
    if type(questions_enabled) is not bool:
        raise GrowthLearningInvalidRequestError()
    issued_at = _normalize_request_time(now)
    if not questions_enabled:
        return _no_candidate(GrowthLearningNoCandidateCodeV1.QUESTIONS_DISABLED)
    if state is not None and state.candidate_id is not None:
        return _no_candidate(
            GrowthLearningNoCandidateCodeV1.CANDIDATE_ALREADY_PRESENT_IN_PAGE_MEMORY
        )
    return _derive_from_growth_result(validated_source, issued_at=issued_at)


def resolve_growth_learning_question(
    source: object,
    candidate: object,
    resolution: object,
    *,
    now: object,
    operation_state: object | None = None,
) -> GrowthLearningResolutionResultV1:
    """Rebuild the current candidate and apply one explicit ephemeral control."""

    validated_candidate = _candidate_or_stale(candidate)
    state = _validate_operation_state(operation_state)
    if (
        state is not None
        and state.terminal
        and state.candidate_id == validated_candidate.candidate_id
    ):
        raise GrowthLearningCandidateAlreadyResolvedError()
    if state is not None and state.candidate_id is None:
        raise GrowthLearningStaleCandidateError()
    if state is not None and state.candidate_id not in {
        None,
        validated_candidate.candidate_id,
    }:
        raise GrowthLearningStaleCandidateError()
    current_time = _normalize_request_time(now)
    if current_time >= validated_candidate.expires_at:
        raise GrowthLearningCandidateExpiredError()
    if current_time < validated_candidate.issued_at:
        raise GrowthLearningStaleCandidateError()
    validated_resolution = validate_growth_learning_resolution(resolution)
    if validated_resolution.candidate_id != validated_candidate.candidate_id:
        raise GrowthLearningInvalidResolutionError()
    if (
        validated_resolution.disposition is GrowthLearningDispositionV1.REVIEW
        and validated_candidate.kind
        not in {GrowthLearningKindV1.RELATION_REVIEW, GrowthLearningKindV1.REFLECTION}
    ):
        raise GrowthLearningInvalidResolutionError()
    current_result = validate_growth_learning_source(source)
    current = _derive_from_growth_result(current_result, issued_at=current_time)
    current_candidate = current.candidate
    if (
        current.status is not GrowthLearningStatusV1.CANDIDATE
        or current_candidate is None
        or not _same_candidate_content(validated_candidate, current_candidate)
    ):
        raise GrowthLearningStaleCandidateError()
    answer_draft: GrowthLearningAnswerDraftV1 | None = None
    if validated_resolution.disposition is GrowthLearningDispositionV1.ANSWER:
        assert validated_resolution.answer is not None
        answer_draft = GrowthLearningAnswerDraftV1(
            candidate_id=validated_candidate.candidate_id,
            text=validated_resolution.answer,
        )
    handoff: GrowthLearningHandoffV1 | None = None
    if validated_resolution.disposition is GrowthLearningDispositionV1.REVIEW:
        handoff = GrowthLearningHandoffV1(
            candidate_id=validated_candidate.candidate_id,
            kind=GrowthLearningHandoffKindV1.RELATION_REVIEW,
        )
    return GrowthLearningResolutionResultV1(
        candidate_id=validated_candidate.candidate_id,
        disposition=validated_resolution.disposition,
        answer_draft=answer_draft,
        handoff=handoff,
        next_state=GrowthLearningOperationStateV1(
            candidate_id=validated_candidate.candidate_id,
            terminal=True,
        ),
    )


def validate_growth_learning_candidate(value: object) -> GrowthLearningCandidateV1:
    """Validate exact candidate fields, identity and 600-second TTL."""

    if type(value) is not GrowthLearningCandidateV1:
        raise GrowthLearningInvalidRequestError()
    candidate = value
    try:
        validate_growth_learning_policy()
        if (
            candidate.contract_version != GROWTH_LEARNING_CONTRACT_VERSION
            or candidate.derivation_version != GROWTH_LEARNING_DERIVATION_VERSION
            or type(candidate.candidate_id) is not str
            or _CANDIDATE_ID_PATTERN.fullmatch(candidate.candidate_id) is None
            or type(candidate.kind) is not GrowthLearningKindV1
            or type(candidate.reason_code) is not GrowthLearningReasonCodeV1
            or candidate.growth_contract_version != GROWTH_CONTRACT_VERSION
            or candidate.growth_derivation_version != GROWTH_DERIVATION_VERSION
            or candidate.growth_policy_id != GROWTH_POLICY_ID
            or type(candidate.growth_state) is not GrowthRelationStateV1
            or type(candidate.question) is not str
            or type(candidate.basis_fingerprint) is not str
            or _HASH_PATTERN.fullmatch(candidate.basis_fingerprint) is None
        ):
            raise ValueError
        validate_growth_hash(candidate.growth_policy_fingerprint)
        if candidate.growth_policy_fingerprint != GROWTH_POLICY_FINGERPRINT:
            raise ValueError
        _validate_uuid7_exact(candidate.goal_source_uuid)
        _validate_hash(candidate.goal_identity_fingerprint)
        for fingerprint in (
            candidate.cohort_fingerprint,
            candidate.behavioral_option_fingerprint,
            candidate.behavioral_reference_fingerprint,
            candidate.mapping_fingerprint,
        ):
            if fingerprint is not None:
                _validate_hash(fingerprint)
        if candidate.mapping_id is not None:
            _validate_uuid7_exact(candidate.mapping_id)
        _validate_candidate_cross_fields(candidate)
        if candidate.question != _QUESTION_TEMPLATES[candidate.reason_code]:
            raise ValueError
        if len(candidate.question.encode("utf-8")) > GROWTH_LEARNING_MAX_QUESTION_BYTES:
            raise ValueError
        issued_at = _require_canonical_utc(candidate.issued_at)
        expires_at = _require_canonical_utc(candidate.expires_at)
        if expires_at <= issued_at or expires_at - issued_at != GROWTH_LEARNING_QUESTION_TTL:
            raise ValueError
        if candidate.basis_fingerprint != _basis_fingerprint(candidate):
            raise ValueError
        if candidate.candidate_id != "gl1:" + candidate.basis_fingerprint.removeprefix("sha256:"):
            raise ValueError
    except GrowthLearningError:
        raise
    except TypeError, ValueError, UnicodeError, OverflowError:
        raise GrowthLearningInvalidRequestError() from None
    serialized = _canonical_json(_candidate_payload(candidate))
    if len(serialized) > GROWTH_LEARNING_MAX_CANDIDATE_BYTES:
        raise GrowthLearningResultTooLargeError()
    return candidate


def serialize_growth_learning_candidate(value: object) -> bytes:
    """Return bounded canonical UTF-8 candidate bytes."""

    candidate = validate_growth_learning_candidate(value)
    return _canonical_json(_candidate_payload(candidate))


def validate_growth_learning_result(value: object) -> GrowthLearningResultV1:
    """Validate the exact candidate/no-candidate envelope and 32 KiB bound."""

    if type(value) is not GrowthLearningResultV1:
        raise GrowthLearningInvalidRequestError()
    result = value
    if type(result.contract_version) is not str or result.contract_version != CONTRACT_VERSION:
        raise GrowthLearningInvalidRequestError()
    if type(result.status) is not GrowthLearningStatusV1:
        raise GrowthLearningInvalidRequestError()
    if result.status is GrowthLearningStatusV1.CANDIDATE:
        if result.candidate is None or result.no_candidate_code is not None:
            raise GrowthLearningInvalidRequestError()
        validate_growth_learning_candidate(result.candidate)
    elif result.status is GrowthLearningStatusV1.NO_CANDIDATE:
        if (
            result.candidate is not None
            or type(result.no_candidate_code) is not GrowthLearningNoCandidateCodeV1
        ):
            raise GrowthLearningInvalidRequestError()
    else:
        raise GrowthLearningInvalidRequestError()
    if len(_canonical_json(_result_payload(result))) > GROWTH_LEARNING_MAX_RESULT_BYTES:
        raise GrowthLearningResultTooLargeError()
    return result


def serialize_growth_learning_result(value: object) -> bytes:
    """Return the bounded canonical UTF-8 result envelope."""

    result = validate_growth_learning_result(value)
    return _canonical_json(_result_payload(result))


def validate_growth_learning_resolution(value: object) -> GrowthLearningResolutionV1:
    """Validate a strict owner control and its optional answer bytes."""

    if type(value) is not GrowthLearningResolutionV1:
        raise GrowthLearningInvalidResolutionError()
    resolution = value
    if (
        type(resolution.candidate_id) is not str
        or _CANDIDATE_ID_PATTERN.fullmatch(resolution.candidate_id) is None
        or type(resolution.disposition) is not GrowthLearningDispositionV1
    ):
        raise GrowthLearningInvalidResolutionError()
    if resolution.disposition is GrowthLearningDispositionV1.ANSWER:
        if type(resolution.answer) is not str:
            raise GrowthLearningInvalidResolutionError()
        try:
            normalized = _normalize_answer(resolution.answer)
        except GrowthLearningAnswerTooLargeError:
            raise
        except GrowthLearningError:
            raise GrowthLearningInvalidResolutionError() from None
        if normalized != resolution.answer:
            raise GrowthLearningInvalidResolutionError()
    elif resolution.answer is not None:
        raise GrowthLearningInvalidResolutionError()
    return resolution


def prepare_growth_learning_personal_memory_handoff(
    candidate: object,
    answer_draft: object,
) -> GrowthLearningHandoffV1:
    """Mark the existing Personal Memory review boundary without writing."""

    validated_candidate = _candidate_or_stale(candidate)
    if validated_candidate.kind not in {
        GrowthLearningKindV1.CONTEXT_CLARIFICATION,
        GrowthLearningKindV1.EVIDENCE_CLARIFICATION,
    }:
        raise GrowthLearningInvalidResolutionError()
    if type(answer_draft) is not GrowthLearningAnswerDraftV1:
        raise GrowthLearningInvalidResolutionError()
    draft = answer_draft
    if draft.candidate_id != validated_candidate.candidate_id:
        raise GrowthLearningInvalidResolutionError()
    try:
        normalized = _normalize_answer(draft.text)
    except GrowthLearningAnswerTooLargeError:
        raise
    except GrowthLearningError:
        raise GrowthLearningInvalidResolutionError() from None
    if normalized != draft.text:
        raise GrowthLearningInvalidResolutionError()
    return GrowthLearningHandoffV1(
        candidate_id=validated_candidate.candidate_id,
        kind=GrowthLearningHandoffKindV1.PERSONAL_MEMORY_REVIEW,
    )


def _derive_from_growth_result(
    result: GrowthEngineResultV1,
    *,
    issued_at: datetime,
) -> GrowthLearningResultV1:
    try:
        if result.selection_mode is not GrowthGoalSelectionModeV1.SELECTED_GOAL:
            return _no_candidate(GrowthLearningNoCandidateCodeV1.GOAL_SELECTION_REQUIRED)
        selected = result.selected_goal_source_uuid
        if selected is None:
            return _no_candidate(GrowthLearningNoCandidateCodeV1.GOAL_SELECTION_REQUIRED)
        relations = tuple(result.goal_results)
        if not relations or any(relation.goal is None for relation in relations):
            return _no_candidate(GrowthLearningNoCandidateCodeV1.GOAL_SOURCE_MISSING)
        if any(
            relation.goal is None or relation.goal.source_note_uuid != selected
            for relation in relations
        ):
            raise GrowthLearningGoalSourceChangedError()
        actionables: dict[GrowthRelationStateV1, list[GrowthGoalRelationResultV1]] = {
            state: [] for state in _PRECEDENCE
        }
        seen_states: set[GrowthRelationStateV1] = set()
        for relation in relations:
            state = relation.state
            if type(state) is not GrowthRelationStateV1:
                return _no_candidate(GrowthLearningNoCandidateCodeV1.UNSUPPORTED_GROWTH_STATE)
            seen_states.add(state)
            if state in _STATE_DERIVATION:
                _validate_actionable_relation(relation, selected)
                actionables[state].append(relation)
        chosen: GrowthGoalRelationResultV1 | None = None
        chosen_state: GrowthRelationStateV1 | None = None
        for state in _PRECEDENCE:
            options = actionables[state]
            if options:
                chosen = min(options, key=_relation_tie_key)
                chosen_state = state
                break
        if chosen is None or chosen_state is None:
            if GrowthRelationStateV1.GOAL_SOURCE_MISSING in seen_states:
                return _no_candidate(GrowthLearningNoCandidateCodeV1.GOAL_SOURCE_MISSING)
            if GrowthRelationStateV1.GOAL_SELECTION_REQUIRED in seen_states:
                return _no_candidate(GrowthLearningNoCandidateCodeV1.GOAL_SELECTION_REQUIRED)
            if GrowthRelationStateV1.NOT_COMPARABLE in seen_states:
                return _no_candidate(GrowthLearningNoCandidateCodeV1.GROWTH_STATE_NOT_COMPARABLE)
            if any(
                state not in _STATE_DERIVATION for state in seen_states
            ) and not seen_states.issubset(
                {
                    GrowthRelationStateV1.SUPPORTS_GOAL,
                    GrowthRelationStateV1.CONFLICTS_WITH_GOAL,
                    GrowthRelationStateV1.NEUTRAL_OR_UNKNOWN,
                    GrowthRelationStateV1.NOT_COMPARABLE,
                    GrowthRelationStateV1.MIXED_BEHAVIOR,
                    GrowthRelationStateV1.CHANGED_BEHAVIOR,
                    GrowthRelationStateV1.BEHAVIORAL_EVIDENCE_INSUFFICIENT,
                    GrowthRelationStateV1.GOAL_MAPPING_MISSING,
                }
            ):
                return _no_candidate(GrowthLearningNoCandidateCodeV1.UNSUPPORTED_GROWTH_STATE)
            return _no_candidate(GrowthLearningNoCandidateCodeV1.NO_ACTIONABLE_GROWTH_GAP)
        candidate = _candidate_from_relation(chosen, chosen_state, issued_at)
        validate_growth_learning_candidate(candidate)
        return validate_growth_learning_result(
            GrowthLearningResultV1(
                contract_version=CONTRACT_VERSION,
                status=GrowthLearningStatusV1.CANDIDATE,
                candidate=candidate,
                no_candidate_code=None,
            )
        )
    except GrowthLearningError:
        raise
    except TypeError, ValueError, UnicodeError, OverflowError:
        raise GrowthLearningInternalContractViolationError() from None


def _validate_actionable_relation(
    relation: GrowthGoalRelationResultV1,
    selected: UUID,
) -> None:
    if type(relation) is not GrowthGoalRelationResultV1:
        raise GrowthLearningInternalContractViolationError()
    goal = relation.goal
    if goal is None or goal.source_note_uuid != selected:
        raise GrowthLearningGoalSourceChangedError()
    state = relation.state
    pattern = relation.behavioral_pattern
    option = relation.behavioral_option
    mapping = relation.mapping
    cohort = relation.cohort_fingerprint
    if pattern is not None and (
        pattern.cohort_fingerprint != cohort or pattern.current_option != option
    ):
        raise GrowthLearningBehavioralSourceChangedError()
    if state is GrowthRelationStateV1.GOAL_MAPPING_MISSING:
        if mapping is not None or pattern is None or cohort is None or option is None:
            raise GrowthLearningInternalContractViolationError()
    elif state is GrowthRelationStateV1.CONFLICTS_WITH_GOAL:
        if (
            mapping is None
            or mapping.relation is not GrowthGoalRelationV1.CONFLICTS_WITH_GOAL
            or pattern is None
            or cohort is None
            or option is None
        ):
            raise GrowthLearningInternalContractViolationError()
    elif state in {
        GrowthRelationStateV1.MIXED_BEHAVIOR,
        GrowthRelationStateV1.CHANGED_BEHAVIOR,
    }:
        if mapping is not None or pattern is None or cohort is None:
            raise GrowthLearningInternalContractViolationError()
        if option is not None:
            raise GrowthLearningBehavioralSourceChangedError()
    elif state is GrowthRelationStateV1.BEHAVIORAL_EVIDENCE_INSUFFICIENT and mapping is not None:
        raise GrowthLearningInternalContractViolationError()


def _candidate_from_relation(
    relation: GrowthGoalRelationResultV1,
    state: GrowthRelationStateV1,
    issued_at: datetime,
) -> GrowthLearningCandidateV1:
    goal = relation.goal
    pattern = relation.behavioral_pattern
    option = relation.behavioral_option
    mapping = relation.mapping
    assert goal is not None
    kind, reason = _STATE_DERIVATION[state]
    cohort = relation.cohort_fingerprint
    candidate = GrowthLearningCandidateV1(
        contract_version=GROWTH_LEARNING_CONTRACT_VERSION,
        derivation_version=GROWTH_LEARNING_DERIVATION_VERSION,
        candidate_id="",
        kind=kind,
        reason_code=reason,
        growth_contract_version=GROWTH_CONTRACT_VERSION,
        growth_derivation_version=GROWTH_DERIVATION_VERSION,
        growth_policy_id=GROWTH_POLICY_ID,
        growth_policy_fingerprint=GROWTH_POLICY_FINGERPRINT,
        goal_source_uuid=cast(UUID, goal.source_note_uuid),
        goal_identity_fingerprint=_growth_hash_json(goal.as_dict()),
        growth_state=state,
        cohort_fingerprint=cohort,
        behavioral_option_fingerprint=(option.option_fingerprint if option is not None else None),
        behavioral_reference_fingerprint=(
            pattern.reference_fingerprint if pattern is not None else None
        ),
        mapping_id=(mapping.mapping_id if mapping is not None else None),
        mapping_fingerprint=(mapping.mapping_fingerprint if mapping is not None else None),
        question=_QUESTION_TEMPLATES[reason],
        basis_fingerprint="",
        issued_at=issued_at,
        expires_at=issued_at + GROWTH_LEARNING_QUESTION_TTL,
    )
    basis = _basis_payload(candidate)
    digest = _growth_hash_json(basis)
    return replace(
        candidate,
        basis_fingerprint=digest,
        candidate_id="gl1:" + digest.removeprefix("sha256:"),
    )


def _validate_candidate_cross_fields(candidate: GrowthLearningCandidateV1) -> None:
    expected_kind, expected_reason = _STATE_DERIVATION.get(
        candidate.growth_state,
        (None, None),
    )
    if (
        expected_kind is None
        or candidate.kind is not expected_kind
        or candidate.reason_code is not expected_reason
    ):
        raise ValueError
    if (candidate.mapping_id is None) != (candidate.mapping_fingerprint is None):
        raise ValueError
    if candidate.growth_state is GrowthRelationStateV1.CONFLICTS_WITH_GOAL:
        if candidate.mapping_id is None or candidate.mapping_fingerprint is None:
            raise ValueError
    elif candidate.mapping_id is not None or candidate.mapping_fingerprint is not None:
        raise ValueError
    if candidate.growth_state is GrowthRelationStateV1.GOAL_MAPPING_MISSING:
        if (
            candidate.cohort_fingerprint is None
            or candidate.behavioral_option_fingerprint is None
            or candidate.behavioral_reference_fingerprint is None
        ):
            raise ValueError
    elif candidate.growth_state in {
        GrowthRelationStateV1.MIXED_BEHAVIOR,
        GrowthRelationStateV1.CHANGED_BEHAVIOR,
    }:
        if (
            candidate.cohort_fingerprint is None
            or candidate.behavioral_reference_fingerprint is None
        ):
            raise ValueError
        if candidate.behavioral_option_fingerprint is not None:
            raise ValueError
    elif candidate.growth_state is GrowthRelationStateV1.CONFLICTS_WITH_GOAL and (
        candidate.cohort_fingerprint is None
        or candidate.behavioral_option_fingerprint is None
        or candidate.behavioral_reference_fingerprint is None
    ):
        raise ValueError


def _basis_payload(candidate: GrowthLearningCandidateV1) -> dict[str, object]:
    return {
        "behavioral_option_fingerprint": candidate.behavioral_option_fingerprint,
        "behavioral_reference_fingerprint": candidate.behavioral_reference_fingerprint,
        "cohort_fingerprint": candidate.cohort_fingerprint,
        "contract_version": candidate.contract_version,
        "derivation_version": candidate.derivation_version,
        "goal_identity_fingerprint": candidate.goal_identity_fingerprint,
        "goal_source_uuid": str(candidate.goal_source_uuid),
        "growth_contract_version": candidate.growth_contract_version,
        "growth_derivation_version": candidate.growth_derivation_version,
        "growth_policy_fingerprint": candidate.growth_policy_fingerprint,
        "growth_policy_id": candidate.growth_policy_id,
        "growth_state": candidate.growth_state.value,
        "kind": candidate.kind.value,
        "learning_policy_fingerprint": GROWTH_LEARNING_POLICY_FINGERPRINT,
        "learning_policy_id": GROWTH_LEARNING_POLICY_ID,
        "mapping_fingerprint": candidate.mapping_fingerprint,
        "mapping_id": str(candidate.mapping_id) if candidate.mapping_id is not None else None,
        "reason_code": candidate.reason_code.value,
    }


def _basis_fingerprint(candidate: GrowthLearningCandidateV1) -> str:
    return _growth_hash_json(_basis_payload(candidate))


def _candidate_payload(candidate: GrowthLearningCandidateV1) -> dict[str, object]:
    return {
        "basis_fingerprint": candidate.basis_fingerprint,
        "behavioral_option_fingerprint": candidate.behavioral_option_fingerprint,
        "behavioral_reference_fingerprint": candidate.behavioral_reference_fingerprint,
        "candidate_id": candidate.candidate_id,
        "cohort_fingerprint": candidate.cohort_fingerprint,
        "contract_version": candidate.contract_version,
        "derivation_version": candidate.derivation_version,
        "expires_at": _utc_iso(candidate.expires_at),
        "goal_identity_fingerprint": candidate.goal_identity_fingerprint,
        "goal_source_uuid": str(candidate.goal_source_uuid),
        "growth_contract_version": candidate.growth_contract_version,
        "growth_derivation_version": candidate.growth_derivation_version,
        "growth_policy_fingerprint": candidate.growth_policy_fingerprint,
        "growth_policy_id": candidate.growth_policy_id,
        "growth_state": candidate.growth_state.value,
        "issued_at": _utc_iso(candidate.issued_at),
        "kind": candidate.kind.value,
        "mapping_fingerprint": candidate.mapping_fingerprint,
        "mapping_id": str(candidate.mapping_id) if candidate.mapping_id is not None else None,
        "question": candidate.question,
        "reason_code": candidate.reason_code.value,
    }


def _result_payload(result: GrowthLearningResultV1) -> dict[str, object]:
    return {
        "candidate": (
            _candidate_payload(result.candidate) if result.candidate is not None else None
        ),
        "contract_version": result.contract_version,
        "no_candidate_code": (
            result.no_candidate_code.value if result.no_candidate_code is not None else None
        ),
        "status": result.status.value,
    }


def _no_candidate(code: GrowthLearningNoCandidateCodeV1) -> GrowthLearningResultV1:
    return validate_growth_learning_result(
        GrowthLearningResultV1(
            contract_version=CONTRACT_VERSION,
            status=GrowthLearningStatusV1.NO_CANDIDATE,
            candidate=None,
            no_candidate_code=code,
        )
    )


def _same_candidate_content(
    left: GrowthLearningCandidateV1,
    right: GrowthLearningCandidateV1,
) -> bool:
    return (
        left.contract_version == right.contract_version
        and left.derivation_version == right.derivation_version
        and left.candidate_id == right.candidate_id
        and left.kind is right.kind
        and left.reason_code is right.reason_code
        and left.growth_contract_version == right.growth_contract_version
        and left.growth_derivation_version == right.growth_derivation_version
        and left.growth_policy_id == right.growth_policy_id
        and left.growth_policy_fingerprint == right.growth_policy_fingerprint
        and left.goal_source_uuid == right.goal_source_uuid
        and left.goal_identity_fingerprint == right.goal_identity_fingerprint
        and left.growth_state is right.growth_state
        and left.cohort_fingerprint == right.cohort_fingerprint
        and left.behavioral_option_fingerprint == right.behavioral_option_fingerprint
        and left.behavioral_reference_fingerprint == right.behavioral_reference_fingerprint
        and left.mapping_id == right.mapping_id
        and left.mapping_fingerprint == right.mapping_fingerprint
        and left.question == right.question
        and left.basis_fingerprint == right.basis_fingerprint
    )


def _relation_tie_key(relation: GrowthGoalRelationResultV1) -> tuple[object, ...]:
    goal = relation.goal
    pattern = relation.behavioral_pattern
    option = relation.behavioral_option
    mapping = relation.mapping
    if goal is None:
        raise GrowthLearningGoalSourceChangedError()
    return (
        str(goal.source_note_uuid),
        _optional_sort_key(relation.cohort_fingerprint),
        _optional_sort_key(option.option_fingerprint if option is not None else None),
        _optional_sort_key(mapping.mapping_fingerprint if mapping is not None else None),
        _optional_sort_key(pattern.reference_fingerprint if pattern is not None else None),
    )


def _optional_sort_key(value: str | None) -> tuple[int, str]:
    return (0, value) if value is not None else _EMPTY_SORT_KEY


def _find_candidate_relation(
    result: GrowthEngineResultV1,
    candidate: GrowthLearningCandidateV1,
) -> GrowthGoalRelationResultV1:
    matches = tuple(
        relation
        for relation in result.goal_results
        if relation.goal is not None
        and relation.goal.source_note_uuid == candidate.goal_source_uuid
        and relation.state is candidate.growth_state
        and relation.cohort_fingerprint == candidate.cohort_fingerprint
        and (
            relation.behavioral_option.option_fingerprint
            if relation.behavioral_option is not None
            else None
        )
        == candidate.behavioral_option_fingerprint
        and (
            relation.behavioral_pattern.reference_fingerprint
            if relation.behavioral_pattern is not None
            else None
        )
        == candidate.behavioral_reference_fingerprint
        and (relation.mapping.mapping_id if relation.mapping is not None else None)
        == candidate.mapping_id
        and (relation.mapping.mapping_fingerprint if relation.mapping is not None else None)
        == candidate.mapping_fingerprint
    )
    if len(matches) != 1:
        raise GrowthLearningStaleCandidateError()
    return matches[0]


def _candidate_or_stale(value: object) -> GrowthLearningCandidateV1:
    try:
        return validate_growth_learning_candidate(value)
    except GrowthLearningError:
        raise GrowthLearningStaleCandidateError() from None


def _validate_operation_state(
    value: object | None,
) -> GrowthLearningOperationStateV1 | None:
    if value is None:
        return None
    if type(value) is not GrowthLearningOperationStateV1:
        raise GrowthLearningInvalidRequestError()
    state = value
    if type(state.terminal) is not bool:
        raise GrowthLearningInvalidRequestError()
    if state.candidate_id is None:
        if state.terminal:
            raise GrowthLearningInvalidRequestError()
        return state
    if (
        type(state.candidate_id) is not str
        or _CANDIDATE_ID_PATTERN.fullmatch(state.candidate_id) is None
    ):
        raise GrowthLearningInvalidRequestError()
    return state


def validate_growth_learning_operation_state(
    value: object,
) -> GrowthLearningOperationStateV1:
    state = _validate_operation_state(value)
    if state is None:
        raise GrowthLearningInvalidRequestError()
    return state


def _coerce_learning_request(value: object) -> GrowthLearningRequestV1:
    if value is None:
        return GrowthLearningRequestV1()
    if type(value) is GrowthLearningRequestV1:
        request = validate_growth_learning_request(value)
        return request
    if type(value) is UUID or type(value) is str:
        try:
            uuid = _normalize_uuid7(value)
        except ValueError:
            raise GrowthLearningInvalidRequestError() from None
        return GrowthLearningRequestV1(goal_source_uuid=uuid)
    raise GrowthLearningInvalidRequestError()


def _normalize_uuid7(value: object) -> UUID:
    try:
        parsed = parse_uuid7(value)
    except TypeError, ValueError, OverflowError:
        raise ValueError("UUIDv7 is invalid") from None
    if type(parsed) is not UUID or parsed.version != 7:
        raise ValueError("UUIDv7 is invalid")
    return parsed


def _validate_uuid7_exact(value: object) -> None:
    if type(value) is not UUID or value.version != 7:
        raise ValueError("UUIDv7 is invalid")


def _normalize_request_time(value: object) -> datetime:
    if not isinstance(value, datetime):
        raise GrowthLearningInvalidRequestError()
    try:
        offset = value.utcoffset()
        if offset is None:
            raise GrowthLearningInvalidRequestError()
        return value.astimezone(UTC)
    except GrowthLearningError:
        raise
    except Exception:
        raise GrowthLearningInvalidRequestError() from None


def _require_canonical_utc(value: object) -> datetime:
    normalized = _normalize_request_time(value)
    if value != normalized:
        raise ValueError("timestamp is not canonical UTC")
    return normalized


def _utc_iso(value: datetime) -> str:
    return _require_canonical_utc(value).isoformat(timespec="microseconds")


def _normalize_answer(value: str) -> str:
    if type(value) is not str:
        raise GrowthLearningInvalidResolutionError()
    try:
        value.encode("utf-8")
    except UnicodeEncodeError:
        raise GrowthLearningInvalidResolutionError() from None
    if _contains_forbidden_text_codepoint(value):
        raise GrowthLearningInvalidResolutionError()
    normalized = unicodedata.normalize("NFC", value).strip()
    if not normalized or _contains_forbidden_text_codepoint(normalized):
        raise GrowthLearningInvalidResolutionError()
    try:
        size = len(normalized.encode("utf-8"))
    except UnicodeEncodeError:
        raise GrowthLearningInvalidResolutionError() from None
    if size > GROWTH_LEARNING_MAX_ANSWER_BYTES:
        raise GrowthLearningAnswerTooLargeError()
    return normalized


def _contains_forbidden_text_codepoint(value: str) -> bool:
    return any(
        (ord(char) <= 0x1F) or 0x7F <= ord(char) <= 0x9F or unicodedata.category(char) == "Cf"
        for char in value
    )


def _normalize_error_code(code: GrowthLearningErrorCodeV1 | str) -> GrowthLearningErrorCodeV1:
    if isinstance(code, GrowthLearningErrorCodeV1):
        return code
    try:
        return GrowthLearningErrorCodeV1(code)
    except TypeError, ValueError:
        return GrowthLearningErrorCodeV1.INTERNAL_CONTRACT_VIOLATION


def _growth_hash_json(value: object) -> str:
    return "sha256:" + hashlib.sha256(canonical_growth_json(value).encode("utf-8")).hexdigest()


def _validate_hash(value: object) -> None:
    if type(value) is not str or _HASH_PATTERN.fullmatch(value) is None:
        raise ValueError("hash is invalid")
    validate_growth_hash(value)


def _canonical_json(value: object) -> bytes:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except TypeError, ValueError, UnicodeError:
        raise GrowthLearningInternalContractViolationError() from None


def _is_growth_source(value: object) -> bool:
    return type(value) in {GrowthEngineResultV1, GrowthLearningSourceV1}


def _growth_no_candidate(
    error: GrowthError,
    *,
    resolving: bool,
) -> GrowthLearningNoCandidateCodeV1 | None:
    if resolving:
        return None
    if isinstance(error, GrowthGoalMissingError):
        return GrowthLearningNoCandidateCodeV1.GOAL_SOURCE_MISSING
    if isinstance(error, GrowthGoalSelectionRequiredError):
        return GrowthLearningNoCandidateCodeV1.GOAL_SELECTION_REQUIRED
    return None


def _translate_growth_error(error: GrowthError, *, resolving: bool) -> GrowthLearningError:
    code = error.code
    if (
        isinstance(error, GrowthPolicyMismatchError)
        or code == GrowthErrorCode.POLICY_MISMATCH.value
    ):
        return GrowthLearningPolicyMismatchError()
    if (
        isinstance(error, GrowthResultTooLargeError)
        or code == GrowthErrorCode.RESULT_TOO_LARGE.value
    ):
        return GrowthLearningResultTooLargeError()
    if (
        isinstance(error, GrowthMappingStoreCorruptError)
        or code == GrowthErrorCode.MAPPING_STORE_CORRUPT.value
    ):
        return GrowthLearningMappingStoreCorruptError()
    if (
        isinstance(error, GrowthMappingStoreUnavailableError)
        or code == GrowthErrorCode.MAPPING_STORE_UNAVAILABLE.value
    ):
        return GrowthLearningMappingSourceUnavailableError()
    if (
        isinstance(error, GrowthGoalSourceChangedError)
        or code == GrowthErrorCode.GOAL_SOURCE_CHANGED.value
    ):
        return (
            GrowthLearningGoalSourceChangedError()
            if resolving
            else GrowthLearningGrowthSourceUnavailableError()
        )
    if (
        isinstance(error, GrowthBehavioralSourceUnavailableError)
        or code == GrowthErrorCode.BEHAVIORAL_SOURCE_UNAVAILABLE.value
    ):
        return (
            GrowthLearningBehavioralSourceChangedError()
            if resolving
            else GrowthLearningGrowthSourceUnavailableError()
        )
    if isinstance(
        error, (GrowthBehavioralEvidenceInsufficientError, GrowthBehavioralStateNotComparableError)
    ):
        return (
            GrowthLearningBehavioralSourceChangedError()
            if resolving
            else GrowthLearningGrowthSourceUnavailableError()
        )
    if (
        isinstance(error, GrowthGoalSourceUnavailableError)
        or code == GrowthErrorCode.GOAL_SOURCE_UNAVAILABLE.value
    ):
        return GrowthLearningGrowthSourceUnavailableError()
    if isinstance(error, (GrowthGoalSelectionRequiredError, GrowthGoalMissingError)):
        return (
            GrowthLearningGoalSourceChangedError()
            if resolving
            else GrowthLearningGrowthSourceUnavailableError()
        )
    return GrowthLearningInternalContractViolationError()


__all__ = [
    "CONTRACT_VERSION",
    "DERIVATION_VERSION",
    "GROWTH_LEARNING_CONTRACT_VERSION",
    "GROWTH_LEARNING_DERIVATION_VERSION",
    "GROWTH_LEARNING_MAX_ANSWER_BYTES",
    "GROWTH_LEARNING_MAX_CANDIDATE_BYTES",
    "GROWTH_LEARNING_MAX_QUESTION_BYTES",
    "GROWTH_LEARNING_MAX_RESULT_BYTES",
    "GROWTH_LEARNING_POLICY_CANONICAL_JSON",
    "GROWTH_LEARNING_POLICY_FINGERPRINT",
    "GROWTH_LEARNING_POLICY_ID",
    "GROWTH_LEARNING_QUESTION_TTL",
    "GROWTH_LEARNING_QUESTION_TTL_SECONDS",
    "MAX_ANSWER_BYTES",
    "MAX_CANDIDATE_BYTES",
    "MAX_QUESTION_BYTES",
    "MAX_RESULT_BYTES",
    "POLICY_CANONICAL_JSON",
    "POLICY_FINGERPRINT",
    "POLICY_ID",
    "QUESTION_TTL",
    "QUESTION_TTL_SECONDS",
    "BuildGrowthLearningQuestion",
    "GrowthLearningAnswerDraftV1",
    "GrowthLearningApplicationOperationV1",
    "GrowthLearningBehavioralSourceChangedError",
    "GrowthLearningCandidateAlreadyResolvedError",
    "GrowthLearningCandidateExpiredError",
    "GrowthLearningCandidateStaleError",
    "GrowthLearningCandidateV1",
    "GrowthLearningDisposition",
    "GrowthLearningDispositionV1",
    "GrowthLearningEngine",
    "GrowthLearningEnginePort",
    "GrowthLearningError",
    "GrowthLearningErrorCode",
    "GrowthLearningErrorCodeV1",
    "GrowthLearningGoalSourceChangedError",
    "GrowthLearningGrowthSourceUnavailableError",
    "GrowthLearningHandoffKind",
    "GrowthLearningHandoffKindV1",
    "GrowthLearningHandoffV1",
    "GrowthLearningInternalContractViolationError",
    "GrowthLearningInvalidRequestError",
    "GrowthLearningInvalidResolutionError",
    "GrowthLearningKind",
    "GrowthLearningKindV1",
    "GrowthLearningMappingSourceUnavailableError",
    "GrowthLearningMappingStoreCorruptError",
    "GrowthLearningNoCandidateCode",
    "GrowthLearningNoCandidateCodeV1",
    "GrowthLearningOperationStateV1",
    "GrowthLearningOperationV1",
    "GrowthLearningPolicyMismatchError",
    "GrowthLearningReasonCode",
    "GrowthLearningReasonCodeV1",
    "GrowthLearningRequestV1",
    "GrowthLearningResolutionResultV1",
    "GrowthLearningResolutionV1",
    "GrowthLearningResultTooLargeError",
    "GrowthLearningResultV1",
    "GrowthLearningRuntimeOperationV1",
    "GrowthLearningSourceUnavailableError",
    "GrowthLearningSourceV1",
    "GrowthLearningStaleCandidateError",
    "GrowthLearningStatus",
    "GrowthLearningStatusV1",
    "build_growth_learning_question",
    "prepare_growth_learning_personal_memory_handoff",
    "resolve_growth_learning_question",
    "serialize_growth_learning_candidate",
    "serialize_growth_learning_result",
    "validate_growth_learning_candidate",
    "validate_growth_learning_operation_state",
    "validate_growth_learning_policy",
    "validate_growth_learning_request",
    "validate_growth_learning_resolution",
    "validate_growth_learning_result",
    "validate_growth_learning_source",
]
