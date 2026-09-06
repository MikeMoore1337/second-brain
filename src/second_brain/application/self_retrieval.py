"""Bounded, current-reread Self Retrieval composition for Stage 5 v1.

This module is deliberately additive.  Search remains the only lexical
candidate source, the canonical vault remains the only current content source,
and the approved Stage 4 Self Model remains the only claim source.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import Final, Protocol
from uuid import UUID

from second_brain.application.ports import (
    RetrievedNote,
    SearchError,
    SearchHit,
    SearchIndexPort,
    SearchNotFoundError,
    SearchRequest,
    VaultReader,
)
from second_brain.application.search import (
    MAX_SEARCH_LIMIT,
    RetrieveManagedNote,
    SearchVault,
    build_literal_match_expression,
    validate_search_request,
)
from second_brain.application.self_model import (
    DEFAULT_SELF_MODEL_POLICY,
    DERIVATION_VERSION,
    MAX_SELF_MODEL_CLAIM_BYTES,
    BuildSelfModel,
    SelfModelDimension,
    SelfModelError,
    SelfModelRequest,
    SelfModelResult,
    validate_self_model_policy,
    validate_self_model_result,
)
from second_brain.domain.models import NoteType

DEFAULT_SELF_CONTEXT_LIMIT: Final[int] = 20
MIN_SELF_CONTEXT_LIMIT: Final[int] = 1
MAX_SELF_CONTEXT_LIMIT: Final[int] = MAX_SEARCH_LIMIT
DEFAULT_MAX_CONTENT_BYTES: Final[int] = 64 * 1024
MIN_MAX_CONTENT_BYTES: Final[int] = 1
MAX_MAX_CONTENT_BYTES: Final[int] = 64 * 1024

# Short aliases keep the bounded policy easy to discover for consumers/tests.
MAX_CONTENT_BYTES: Final[int] = MAX_MAX_CONTENT_BYTES
MAX_RESULT_CONTENT_BYTES: Final[int] = MAX_MAX_CONTENT_BYTES

_FINGERPRINT_PATTERN: Final[re.Pattern[str]] = re.compile(r"[0-9a-f]{64}\Z")
_SELF_MODEL_DIMENSIONS: Final[frozenset[SelfModelDimension]] = frozenset(
    {
        SelfModelDimension.PREFERENCE,
        SelfModelDimension.BELIEF,
        SelfModelDimension.GOAL,
    }
)


class SelfRetrievalErrorCode(StrEnum):
    """Closed safe error taxonomy for the Stage 5 application boundary."""

    INVALID_REQUEST = "SELF_RETRIEVAL_INVALID_REQUEST"
    SEARCH_UNAVAILABLE = "SELF_RETRIEVAL_SEARCH_UNAVAILABLE"
    CURRENT_READ_UNAVAILABLE = "SELF_RETRIEVAL_CURRENT_READ_UNAVAILABLE"
    SELF_MODEL_UNAVAILABLE = "SELF_RETRIEVAL_SELF_MODEL_UNAVAILABLE"
    RESULT_INVALID = "SELF_RETRIEVAL_RESULT_INVALID"
    RESULT_TOO_LARGE = "SELF_RETRIEVAL_RESULT_TOO_LARGE"


_ERROR_MESSAGES: Final[dict[SelfRetrievalErrorCode, str]] = {
    SelfRetrievalErrorCode.INVALID_REQUEST: "self retrieval request failed validation",
    SelfRetrievalErrorCode.SEARCH_UNAVAILABLE: "self retrieval search is unavailable",
    SelfRetrievalErrorCode.CURRENT_READ_UNAVAILABLE: "self retrieval current read is unavailable",
    SelfRetrievalErrorCode.SELF_MODEL_UNAVAILABLE: "self retrieval self model is unavailable",
    SelfRetrievalErrorCode.RESULT_INVALID: "self retrieval result failed validation",
    SelfRetrievalErrorCode.RESULT_TOO_LARGE: "self retrieval result exceeds its bounded limit",
}


class SelfRetrievalError(RuntimeError):
    """Safe application error without query, path, body, or backend details."""

    def __init__(self, code: SelfRetrievalErrorCode | str, message: str | None = None) -> None:
        del message
        normalized = _normalize_error_code(code)
        self.code = normalized.value
        self.message = _ERROR_MESSAGES[normalized]
        super().__init__(self.message)

    def as_dict(self) -> dict[str, str]:
        """Return the stable machine-readable error projection."""

        return {"code": self.code, "message": self.message}


class SelfRetrievalInvalidRequestError(SelfRetrievalError):
    """The request type or one of its bounded fields is invalid."""

    def __init__(self, message: str | None = None) -> None:
        super().__init__(SelfRetrievalErrorCode.INVALID_REQUEST, message)


class SelfRetrievalSearchUnavailableError(SelfRetrievalError):
    """The lexical candidate boundary did not return a safe result."""

    def __init__(self, message: str | None = None) -> None:
        super().__init__(SelfRetrievalErrorCode.SEARCH_UNAVAILABLE, message)


class SelfRetrievalCurrentReadUnavailableError(SelfRetrievalError):
    """The current UUID reread could not prove canonical identity/content."""

    def __init__(self, message: str | None = None) -> None:
        super().__init__(SelfRetrievalErrorCode.CURRENT_READ_UNAVAILABLE, message)


class SelfRetrievalSelfModelUnavailableError(SelfRetrievalError):
    """The approved current Self Model result could not be validated."""

    def __init__(self, message: str | None = None) -> None:
        super().__init__(SelfRetrievalErrorCode.SELF_MODEL_UNAVAILABLE, message)


class SelfRetrievalResultInvalidError(SelfRetrievalError):
    """The assembled context violates the exact bounded DTO shape."""

    def __init__(self, message: str | None = None) -> None:
        super().__init__(SelfRetrievalErrorCode.RESULT_INVALID, message)


class SelfRetrievalResultTooLargeError(SelfRetrievalError):
    """The bounded context cannot fit the fixed content cap."""

    def __init__(self, message: str | None = None) -> None:
        super().__init__(SelfRetrievalErrorCode.RESULT_TOO_LARGE, message)


@dataclass(frozen=True, slots=True)
class SelfContextRequest:
    """One literal query and one bounded in-memory context budget."""

    query: str
    limit: int = DEFAULT_SELF_CONTEXT_LIMIT
    max_content_bytes: int = DEFAULT_MAX_CONTENT_BYTES


@dataclass(frozen=True, slots=True)
class SelfContextClaim:
    """One already-derived Self Model claim linked by exact support UUIDs."""

    dimension: SelfModelDimension
    claim: str
    supporting_note_ids: tuple[UUID, ...]
    derivation_version: str
    policy_fingerprint: str


@dataclass(frozen=True, slots=True)
class SelfContextItem:
    """One current canonical note projected without path/front matter."""

    note_id: UUID
    note_type: NoteType
    title: str
    body: str
    tags: tuple[str, ...]
    created: datetime
    updated: datetime | None
    search_rank: int
    self_model_claims: tuple[SelfContextClaim, ...]


class SelfContextExclusionReason(StrEnum):
    """Only the two bounded reasons for omitting a search candidate."""

    CONTEXT_BUDGET_EXCEEDED = "context_budget_exceeded"
    CANDIDATE_NOT_FOUND = "candidate_not_found"


@dataclass(frozen=True, slots=True)
class SelfContextExclusion:
    """Audit metadata for one omitted candidate without identity/path leakage."""

    search_rank: int
    reason: SelfContextExclusionReason


@dataclass(frozen=True, slots=True)
class SelfContextResult:
    """Complete bounded result or no result when an integrity boundary fails."""

    items: tuple[SelfContextItem, ...]
    candidate_count: int
    included_count: int
    excluded_count: int
    exclusions: tuple[SelfContextExclusion, ...]
    truncated: bool
    content_bytes: int
    self_model_derivation_version: str
    self_model_policy_fingerprint: str


class SearchExecutor(Protocol):
    """Minimal injectable lexical candidate boundary for deterministic tests."""

    def execute(self, request: SearchRequest) -> tuple[SearchHit, ...]:
        """Return ordered SearchHit candidates."""


class CurrentNoteReader(Protocol):
    """Minimal injectable current UUID reread boundary."""

    def execute(self, note_id: UUID) -> RetrievedNote:
        """Return the current note or a bounded SearchError."""


class SelfModelBuilder(Protocol):
    """Minimal injectable approved Self Model boundary."""

    def execute(self, request: SelfModelRequest) -> SelfModelResult:
        """Return one already validated current Self Model result."""


SelfRetrievalClock = Callable[[], datetime]


class BuildSelfContext:
    """Compose lexical candidates, current rereads, and exact Self Model links."""

    def __init__(
        self,
        reader: VaultReader | None = None,
        index: SearchIndexPort | None = None,
        *,
        search: SearchExecutor | None = None,
        retriever: CurrentNoteReader | None = None,
        self_model: SelfModelBuilder | None = None,
        clock: SelfRetrievalClock | None = None,
    ) -> None:
        """Create the default existing-boundary composition or injected seams."""

        search_executor = search
        retriever_executor = retriever
        self_model_executor = self_model
        if search_executor is None:
            if reader is None or index is None:
                raise ValueError("reader and index are required for default search")
            search_executor = SearchVault(reader, index)
        if retriever_executor is None:
            if reader is None:
                raise ValueError("reader is required for default current reread")
            retriever_executor = RetrieveManagedNote(reader)
        if self_model_executor is None:
            if reader is None:
                raise ValueError("reader is required for default self model")
            self_model_executor = BuildSelfModel(
                reader,
                policy=DEFAULT_SELF_MODEL_POLICY,
                clock=clock or _utc_now,
            )
        self._search: SearchExecutor = search_executor
        self._retriever: CurrentNoteReader = retriever_executor
        self._self_model: SelfModelBuilder = self_model_executor

    def execute(self, request: SelfContextRequest) -> SelfContextResult:
        """Build a complete bounded context or raise one safe application error."""

        validated_request = validate_self_context_request(request)
        candidates = self._read_candidates(validated_request)
        self_model = self._read_self_model()
        claims_by_note = _project_claims_by_note(self_model)

        items: list[SelfContextItem] = []
        exclusions: list[SelfContextExclusion] = []
        content_bytes = 0
        truncated = False
        budget_exhausted = False

        for search_rank, hit in enumerate(candidates, start=1):
            if budget_exhausted:
                exclusions.append(
                    SelfContextExclusion(
                        search_rank=search_rank,
                        reason=SelfContextExclusionReason.CONTEXT_BUDGET_EXCEEDED,
                    )
                )
                continue

            current = self._read_current(hit)
            if current is None:
                exclusions.append(
                    SelfContextExclusion(
                        search_rank=search_rank,
                        reason=SelfContextExclusionReason.CANDIDATE_NOT_FOUND,
                    )
                )
                continue

            current_bytes = _content_bytes(current)
            if content_bytes + current_bytes > validated_request.max_content_bytes:
                budget_exhausted = True
                truncated = True
                exclusions.append(
                    SelfContextExclusion(
                        search_rank=search_rank,
                        reason=SelfContextExclusionReason.CONTEXT_BUDGET_EXCEEDED,
                    )
                )
                continue

            items.append(
                SelfContextItem(
                    note_id=current.note_id,
                    note_type=current.note_type,
                    title=current.title,
                    body=current.body,
                    tags=current.tags,
                    created=current.created,
                    updated=current.updated,
                    search_rank=search_rank,
                    self_model_claims=claims_by_note.get(current.note_id, ()),
                )
            )
            content_bytes += current_bytes

        result = SelfContextResult(
            items=tuple(items),
            candidate_count=len(candidates),
            included_count=len(items),
            excluded_count=len(exclusions),
            exclusions=tuple(exclusions),
            truncated=truncated,
            content_bytes=content_bytes,
            self_model_derivation_version=self_model.derivation_version,
            self_model_policy_fingerprint=self_model.policy_fingerprint,
        )
        return validate_self_context_result(result, request=validated_request)

    def _read_candidates(self, request: SelfContextRequest) -> tuple[SearchHit, ...]:
        try:
            candidates = self._search.execute(SearchRequest(request.query, request.limit))
        except SearchError:
            raise SelfRetrievalSearchUnavailableError() from None
        except Exception:
            raise SelfRetrievalSearchUnavailableError() from None
        if type(candidates) is not tuple or len(candidates) > request.limit:
            raise SelfRetrievalSearchUnavailableError()
        seen_ids: set[UUID] = set()
        for candidate in candidates:
            if not _valid_search_hit(candidate) or candidate.note_id in seen_ids:
                raise SelfRetrievalSearchUnavailableError()
            seen_ids.add(candidate.note_id)
        return candidates

    def _read_self_model(self) -> SelfModelResult:
        try:
            request = SelfModelRequest()
            policy_fingerprint = validate_self_model_policy(DEFAULT_SELF_MODEL_POLICY)
            result = self._self_model.execute(request)
            return validate_self_model_result(
                result,
                request=request,
                policy=DEFAULT_SELF_MODEL_POLICY,
                expected_policy_fingerprint=policy_fingerprint,
            )
        except SelfModelError:
            raise SelfRetrievalSelfModelUnavailableError() from None
        except Exception:
            raise SelfRetrievalSelfModelUnavailableError() from None

    def _read_current(self, candidate: SearchHit) -> RetrievedNote | None:
        try:
            current = self._retriever.execute(candidate.note_id)
        except SearchNotFoundError:
            return None
        except SearchError:
            raise SelfRetrievalCurrentReadUnavailableError() from None
        except Exception:
            raise SelfRetrievalCurrentReadUnavailableError() from None
        if not _valid_current_note(current, candidate.note_id):
            raise SelfRetrievalCurrentReadUnavailableError()
        return current


def validate_self_context_request(request: object) -> SelfContextRequest:
    """Validate all request bounds before any reader, index, or model call."""

    if type(request) is not SelfContextRequest:
        raise SelfRetrievalInvalidRequestError()
    if type(request.max_content_bytes) is not int or not (
        MIN_MAX_CONTENT_BYTES <= request.max_content_bytes <= MAX_MAX_CONTENT_BYTES
    ):
        raise SelfRetrievalInvalidRequestError()
    try:
        search_request = SearchRequest(query=request.query, limit=request.limit)
        validate_search_request(search_request)
        # Search owns literal tokenization and its existing 32-term bound.
        build_literal_match_expression(request.query)
    except SearchError:
        raise SelfRetrievalInvalidRequestError() from None
    except Exception:
        raise SelfRetrievalInvalidRequestError() from None
    return request


def validate_self_context_result(
    result: object,
    *,
    request: SelfContextRequest,
) -> SelfContextResult:
    """Validate totals, order, content budget, and exact safe DTO fields."""

    validate_self_context_request(request)
    if type(result) is not SelfContextResult:
        raise SelfRetrievalResultInvalidError()
    if (
        type(result.items) is not tuple
        or type(result.exclusions) is not tuple
        or type(result.truncated) is not bool
        or not _valid_non_negative_int(result.candidate_count)
        or not _valid_non_negative_int(result.included_count)
        or not _valid_non_negative_int(result.excluded_count)
        or not _valid_non_negative_int(result.content_bytes)
        or result.candidate_count > request.limit
        or result.content_bytes > request.max_content_bytes
        or type(result.self_model_derivation_version) is not str
        or result.self_model_derivation_version != DERIVATION_VERSION
        or type(result.self_model_policy_fingerprint) is not str
        or _FINGERPRINT_PATTERN.fullmatch(result.self_model_policy_fingerprint) is None
    ):
        raise SelfRetrievalResultInvalidError()
    if (
        result.included_count != len(result.items)
        or result.excluded_count != len(result.exclusions)
        or result.candidate_count != result.included_count + result.excluded_count
    ):
        raise SelfRetrievalResultInvalidError()

    item_ranks: list[int] = []
    item_ids: set[UUID] = set()
    computed_bytes = 0
    for item in result.items:
        if not _valid_context_item(
            item,
            request=request,
            derivation_version=result.self_model_derivation_version,
            policy_fingerprint=result.self_model_policy_fingerprint,
        ):
            raise SelfRetrievalResultInvalidError()
        if item.search_rank in item_ranks or item.note_id in item_ids:
            raise SelfRetrievalResultInvalidError()
        item_ranks.append(item.search_rank)
        item_ids.add(item.note_id)
        computed_bytes += _content_bytes(item)

    exclusion_ranks: list[int] = []
    for exclusion in result.exclusions:
        if (
            type(exclusion) is not SelfContextExclusion
            or type(exclusion.search_rank) is not int
            or not 1 <= exclusion.search_rank <= result.candidate_count
            or type(exclusion.reason) is not SelfContextExclusionReason
            or exclusion.search_rank in item_ranks
            or exclusion.search_rank in exclusion_ranks
        ):
            raise SelfRetrievalResultInvalidError()
        exclusion_ranks.append(exclusion.search_rank)

    all_ranks = item_ranks + exclusion_ranks
    if (
        item_ranks != sorted(item_ranks)
        or exclusion_ranks != sorted(exclusion_ranks)
        or sorted(all_ranks) != list(range(1, result.candidate_count + 1))
        or result.content_bytes != computed_bytes
        or result.truncated
        != any(
            exclusion.reason is SelfContextExclusionReason.CONTEXT_BUDGET_EXCEEDED
            for exclusion in result.exclusions
        )
    ):
        raise SelfRetrievalResultInvalidError()
    return result


def _project_claims_by_note(result: SelfModelResult) -> dict[UUID, tuple[SelfContextClaim, ...]]:
    projected: dict[UUID, tuple[SelfContextClaim, ...]] = {}
    for claim in result.claims:
        context_claim = SelfContextClaim(
            dimension=claim.dimension,
            claim=claim.claim,
            supporting_note_ids=tuple(ref.note_id for ref in claim.supporting_evidence),
            derivation_version=result.derivation_version,
            policy_fingerprint=result.policy_fingerprint,
        )
        for note_id in context_claim.supporting_note_ids:
            projected[note_id] = (*projected.get(note_id, ()), context_claim)
    return projected


def _valid_search_hit(candidate: object) -> bool:
    if type(candidate) is not SearchHit:
        return False
    return (
        _valid_uuid(candidate.note_id)
        and type(candidate.note_type) is NoteType
        and type(candidate.relative_path) is str
        and type(candidate.title) is str
        and type(candidate.tags) is tuple
        and all(type(tag) is str for tag in candidate.tags)
        and _is_aware(candidate.created)
        and (candidate.updated is None or _is_aware(candidate.updated))
        and type(candidate.snippet) is str
    )


def _valid_current_note(note: object, expected_note_id: UUID) -> bool:
    if type(note) is not RetrievedNote:
        return False
    return (
        note.note_id == expected_note_id
        and _valid_uuid(note.note_id)
        and type(note.note_type) is NoteType
        and type(note.relative_path) is str
        and type(note.title) is str
        and type(note.body) is str
        and type(note.tags) is tuple
        and all(type(tag) is str for tag in note.tags)
        and _is_aware(note.created)
        and (note.updated is None or _is_aware(note.updated))
    )


def _valid_context_item(
    item: object,
    *,
    request: SelfContextRequest,
    derivation_version: str,
    policy_fingerprint: str,
) -> bool:
    if type(item) is not SelfContextItem:
        return False
    if (
        not _valid_uuid(item.note_id)
        or type(item.note_type) is not NoteType
        or type(item.title) is not str
        or type(item.body) is not str
        or type(item.tags) is not tuple
        or not all(type(tag) is str for tag in item.tags)
        or not _is_aware(item.created)
        or (item.updated is not None and not _is_aware(item.updated))
        or type(item.search_rank) is not int
        or not 1 <= item.search_rank <= request.limit
        or type(item.self_model_claims) is not tuple
    ):
        return False
    try:
        current_bytes = _content_bytes(item)
    except UnicodeEncodeError, TypeError:
        return False
    if current_bytes > request.max_content_bytes:
        return False
    return all(
        _valid_context_claim(claim, item.note_id, derivation_version, policy_fingerprint)
        for claim in item.self_model_claims
    )


def _valid_context_claim(
    claim: object,
    item_note_id: UUID,
    derivation_version: str,
    policy_fingerprint: str,
) -> bool:
    if type(claim) is not SelfContextClaim:
        return False
    if (
        type(claim.dimension) is not SelfModelDimension
        or claim.dimension not in _SELF_MODEL_DIMENSIONS
        or type(claim.claim) is not str
        or not claim.claim
        or type(claim.supporting_note_ids) is not tuple
        or not claim.supporting_note_ids
        or any(not _valid_uuid(note_id) for note_id in claim.supporting_note_ids)
        or len(set(claim.supporting_note_ids)) != len(claim.supporting_note_ids)
        or item_note_id not in claim.supporting_note_ids
        or type(claim.derivation_version) is not str
        or claim.derivation_version != derivation_version
        or type(claim.policy_fingerprint) is not str
        or claim.policy_fingerprint != policy_fingerprint
    ):
        return False
    try:
        return len(claim.claim.encode("utf-8")) <= MAX_SELF_MODEL_CLAIM_BYTES
    except UnicodeEncodeError:
        return False


def _content_bytes(value: SelfContextItem | RetrievedNote) -> int:
    fields = (value.title, value.body, *value.tags)
    return len("\n".join(fields).encode("utf-8"))


def _valid_uuid(value: object) -> bool:
    return type(value) is UUID and value.version == 7


def _valid_non_negative_int(value: object) -> bool:
    return type(value) is int and value >= 0


def _is_aware(value: object) -> bool:
    return (
        isinstance(value, datetime) and value.tzinfo is not None and value.utcoffset() is not None
    )


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _normalize_error_code(code: SelfRetrievalErrorCode | str) -> SelfRetrievalErrorCode:
    if isinstance(code, SelfRetrievalErrorCode):
        return code
    try:
        return SelfRetrievalErrorCode(code)
    except TypeError, ValueError:
        return SelfRetrievalErrorCode.RESULT_INVALID


__all__ = [
    "DEFAULT_MAX_CONTENT_BYTES",
    "DEFAULT_SELF_CONTEXT_LIMIT",
    "MAX_CONTENT_BYTES",
    "MAX_MAX_CONTENT_BYTES",
    "MAX_RESULT_CONTENT_BYTES",
    "MAX_SELF_CONTEXT_LIMIT",
    "MIN_MAX_CONTENT_BYTES",
    "MIN_SELF_CONTEXT_LIMIT",
    "BuildSelfContext",
    "CurrentNoteReader",
    "SearchExecutor",
    "SelfContextClaim",
    "SelfContextExclusion",
    "SelfContextExclusionReason",
    "SelfContextItem",
    "SelfContextRequest",
    "SelfContextResult",
    "SelfModelBuilder",
    "SelfRetrievalCurrentReadUnavailableError",
    "SelfRetrievalError",
    "SelfRetrievalErrorCode",
    "SelfRetrievalInvalidRequestError",
    "SelfRetrievalResultInvalidError",
    "SelfRetrievalResultTooLargeError",
    "SelfRetrievalSearchUnavailableError",
    "SelfRetrievalSelfModelUnavailableError",
    "validate_self_context_request",
    "validate_self_context_result",
]
