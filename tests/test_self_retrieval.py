"""Deterministic tests for the Stage 5 Self Retrieval v1 core."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

import pytest

from second_brain.adapters.search import SqliteFts5SearchIndex
from second_brain.adapters.vault import FileSystemVaultReader
from second_brain.application.ports import (
    RetrievedNote,
    SearchHit,
    SearchRequest,
)
from second_brain.application.search import SearchIdentityConflictError, SearchNotFoundError
from second_brain.application.self_model import (
    CONFIDENCE_POLICY,
    DEFAULT_SELF_MODEL_POLICY,
    DERIVATION_VERSION,
    SelfModelClaim,
    SelfModelConfidence,
    SelfModelConfidenceState,
    SelfModelDimension,
    SelfModelError,
    SelfModelEvidenceRef,
    SelfModelRequest,
    SelfModelResult,
    SelfModelTemporalContext,
    validate_self_model_policy,
)
from second_brain.application.self_retrieval import (
    DEFAULT_MAX_CONTENT_BYTES,
    MAX_MAX_CONTENT_BYTES,
    BuildSelfContext,
    SelfContextExclusionReason,
    SelfContextRequest,
    SelfRetrievalCurrentReadUnavailableError,
    SelfRetrievalErrorCode,
    SelfRetrievalInvalidRequestError,
    SelfRetrievalSearchUnavailableError,
    SelfRetrievalSelfModelUnavailableError,
)
from second_brain.domain.models import EvidenceAtPrecision, EvidenceKind, NoteType, SelfKind
from tests.conftest import VALID_NOTE_ID, create_vault, managed_note, write_note

GENERATED_AT = datetime(2026, 9, 6, 10, 0, tzinfo=UTC)
EVIDENCE_AT = datetime(2026, 9, 5, 12, 0, tzinfo=UTC)
FIRST_ID = UUID(VALID_NOTE_ID)
SECOND_ID = UUID("0198f4c5-6a00-7000-8000-000000000003")
THIRD_ID = UUID("0198f4c5-6a00-7000-8000-000000000004")


class FakeSearch:
    def __init__(self, hits: tuple[SearchHit, ...]) -> None:
        self.hits = hits
        self.calls: list[SearchRequest] = []

    def execute(self, request: SearchRequest) -> tuple[SearchHit, ...]:
        self.calls.append(request)
        return self.hits


class FakeRetriever:
    def __init__(self, results: dict[UUID, RetrievedNote | Exception | None]) -> None:
        self.results = results
        self.calls: list[UUID] = []

    def execute(self, note_id: UUID) -> RetrievedNote:
        self.calls.append(note_id)
        result = self.results.get(note_id)
        if isinstance(result, Exception):
            raise result
        if result is None:
            raise SearchNotFoundError()
        return result


class FakeSelfModel:
    def __init__(self, result: SelfModelResult | Exception | object) -> None:
        self.result = result
        self.calls: list[SelfModelRequest] = []

    def execute(self, request: SelfModelRequest) -> SelfModelResult:
        self.calls.append(request)
        if isinstance(self.result, Exception):
            raise self.result
        return self.result  # type: ignore[return-value]


def _hit(note_id: UUID, *, title: str = "Candidate", snippet: str = "stale snippet") -> SearchHit:
    return SearchHit(
        note_id=note_id,
        note_type=NoteType.RESOURCE,
        relative_path=f"30 Resources/{note_id}.md",
        title=title,
        tags=("search",),
        created=GENERATED_AT,
        updated=None,
        snippet=snippet,
    )


def _current(
    note_id: UUID,
    *,
    title: str = "Current title",
    body: str = "Current canonical body.",
    tags: tuple[str, ...] = ("current",),
) -> RetrievedNote:
    return RetrievedNote(
        note_id=note_id,
        note_type=NoteType.RESOURCE,
        relative_path=f"30 Resources/{note_id}.md",
        title=title,
        body=body,
        tags=tags,
        created=GENERATED_AT,
        updated=None,
    )


def _empty_self_model() -> SelfModelResult:
    return SelfModelResult(
        claims=(),
        eligible_evidence_count=0,
        represented_evidence_count=0,
        generated_at=GENERATED_AT,
        derivation_version=DERIVATION_VERSION,
        policy_fingerprint=validate_self_model_policy(DEFAULT_SELF_MODEL_POLICY),
    )


def _self_model_with_claim(note_id: UUID) -> SelfModelResult:
    supporting_ref = SelfModelEvidenceRef(
        note_id=note_id,
        evidence_kind=EvidenceKind.USER_STATEMENT,
        self_kind=SelfKind.PREFERENCE,
        domain=None,
        evidence_at=EVIDENCE_AT,
        evidence_at_precision=EvidenceAtPrecision.EXACT,
    )
    claim = SelfModelClaim(
        dimension=SelfModelDimension.PREFERENCE,
        claim="Пользователь предпочитает проверяемый текущий контекст.",
        domain=None,
        supporting_evidence=(supporting_ref,),
        contradicting_evidence=(),
        contextual_evidence=(),
        confidence=SelfModelConfidence(
            state=SelfModelConfidenceState.NOT_ASSESSED,
            score=None,
            policy_version=CONFIDENCE_POLICY,
            supporting_evidence_count=1,
            contradicting_evidence_count=0,
            unknown_time_count=0,
        ),
        temporal_context=SelfModelTemporalContext(
            earliest_known_evidence_at=EVIDENCE_AT,
            latest_known_evidence_at=EVIDENCE_AT,
            known_evidence_count=1,
            unknown_evidence_count=0,
        ),
        generated_at=GENERATED_AT,
        derivation_version=DERIVATION_VERSION,
    )
    return replace(
        _empty_self_model(),
        claims=(claim,),
        eligible_evidence_count=1,
        represented_evidence_count=1,
    )


def _build(
    hits: tuple[SearchHit, ...],
    results: dict[UUID, RetrievedNote | Exception | None],
    *,
    self_model: SelfModelResult | Exception | object | None = None,
) -> tuple[BuildSelfContext, FakeSearch, FakeRetriever, FakeSelfModel]:
    search = FakeSearch(hits)
    retriever = FakeRetriever(results)
    model = FakeSelfModel(_empty_self_model() if self_model is None else self_model)
    return (
        BuildSelfContext(search=search, retriever=retriever, self_model=model),
        search,
        retriever,
        model,
    )


def test_default_composition_uses_existing_search_and_current_reread_boundaries(
    tmp_path: Path,
) -> None:
    vault = create_vault(tmp_path / "vault")
    write_note(
        vault,
        "30 Resources/Current.md",
        managed_note().replace("Текст заметки.", "Канонический current context."),
    )
    index = SqliteFts5SearchIndex()
    try:
        result = BuildSelfContext(FileSystemVaultReader(vault), index).execute(
            SelfContextRequest("current context")
        )
    finally:
        index.close()

    assert result.candidate_count == 1
    assert result.items[0].body == "# Тестовая заметка\n\nКанонический current context.\n"
    assert result.items[0].self_model_claims == ()
    assert not hasattr(result.items[0], "relative_path")


@pytest.mark.parametrize(
    "invalid_request",
    [
        object(),
        SelfContextRequest(" "),
        SelfContextRequest("bad\u202equery"),
        SelfContextRequest("x", limit=0),
        SelfContextRequest("x", limit=51),
        SelfContextRequest("x", limit=True),
        SelfContextRequest("x", max_content_bytes=0),
        SelfContextRequest("x", max_content_bytes=MAX_MAX_CONTENT_BYTES + 1),
        SelfContextRequest("x", max_content_bytes=False),
        SelfContextRequest(" ".join(f"term{index}" for index in range(33))),
        SelfContextRequest("\ud800"),
    ],
)
def test_invalid_request_is_rejected_before_any_boundary_call(invalid_request: object) -> None:
    context, search, retriever, model = _build((), {})

    with pytest.raises(SelfRetrievalInvalidRequestError) as error:
        context.execute(invalid_request)  # type: ignore[arg-type]

    assert error.value.code == SelfRetrievalErrorCode.INVALID_REQUEST.value
    assert search.calls == []
    assert retriever.calls == []
    assert model.calls == []


def test_search_order_and_current_reread_replace_stale_snippets() -> None:
    hits = (_hit(FIRST_ID, snippet="STALE FIRST"), _hit(SECOND_ID, snippet="STALE SECOND"))
    context, search, retriever, model = _build(
        hits,
        {
            FIRST_ID: _current(FIRST_ID, body="FIRST CURRENT BODY"),
            SECOND_ID: _current(SECOND_ID, body="SECOND CURRENT BODY"),
        },
    )

    result = context.execute(SelfContextRequest("literal query", limit=2))

    assert search.calls == [SearchRequest("literal query", 2)]
    assert model.calls == [SelfModelRequest()]
    assert retriever.calls == [FIRST_ID, SECOND_ID]
    assert [item.search_rank for item in result.items] == [1, 2]
    assert [item.body for item in result.items] == ["FIRST CURRENT BODY", "SECOND CURRENT BODY"]
    assert "STALE" not in repr(result)
    assert result.candidate_count == result.included_count == 2
    assert result.exclusions == ()
    assert result.truncated is False


def test_missing_candidate_is_excluded_without_resurrection() -> None:
    context, _search, retriever, _model = _build(
        (_hit(FIRST_ID, snippet="deleted stale body"), _hit(SECOND_ID)),
        {FIRST_ID: None, SECOND_ID: _current(SECOND_ID, body="still current")},
    )

    result = context.execute(SelfContextRequest("query", limit=2))

    assert retriever.calls == [FIRST_ID, SECOND_ID]
    assert result.included_count == 1
    assert result.excluded_count == 1
    assert len(result.exclusions) == 1
    assert result.exclusions[0].search_rank == 1
    assert result.exclusions[0].reason is SelfContextExclusionReason.CANDIDATE_NOT_FOUND
    assert result.truncated is False


def test_content_budget_excludes_overflow_and_all_following_candidates() -> None:
    first = _current(FIRST_ID, title="A", body="one", tags=())
    second = _current(SECOND_ID, title="B", body="two", tags=())
    third = _current(THIRD_ID, title="C", body="three", tags=())
    context, _search, retriever, _model = _build(
        (_hit(FIRST_ID), _hit(SECOND_ID), _hit(THIRD_ID)),
        {FIRST_ID: first, SECOND_ID: second, THIRD_ID: third},
    )
    first_size = len(b"A\none")

    result = context.execute(SelfContextRequest("query", limit=3, max_content_bytes=first_size))

    assert result.items[0].note_id == FIRST_ID
    assert result.content_bytes == first_size
    assert result.truncated is True
    assert result.exclusions[0].reason is SelfContextExclusionReason.CONTEXT_BUDGET_EXCEEDED
    assert result.exclusions[1].reason is SelfContextExclusionReason.CONTEXT_BUDGET_EXCEEDED
    assert retriever.calls == [FIRST_ID, SECOND_ID]


def test_exact_supporting_uuid_creates_claim_link_only_for_that_note() -> None:
    context, _search, _retriever, _model = _build(
        (_hit(FIRST_ID, title="same title"), _hit(SECOND_ID, title="same title")),
        {FIRST_ID: _current(FIRST_ID, title="same title"), SECOND_ID: _current(SECOND_ID)},
        self_model=_self_model_with_claim(SECOND_ID),
    )

    result = context.execute(SelfContextRequest("same title", limit=2))

    assert result.items[0].self_model_claims == ()
    assert len(result.items[1].self_model_claims) == 1
    linked_claim = result.items[1].self_model_claims[0]
    assert linked_claim.supporting_note_ids == (SECOND_ID,)
    assert linked_claim.claim == "Пользователь предпочитает проверяемый текущий контекст."
    assert linked_claim.derivation_version == DERIVATION_VERSION
    assert linked_claim.policy_fingerprint == result.self_model_policy_fingerprint


def test_current_uuid_mismatch_is_safe_error_without_partial_context() -> None:
    secret_body = "private body must not enter the error"
    context, _search, _retriever, _model = _build(
        (_hit(FIRST_ID),),
        {FIRST_ID: _current(SECOND_ID, body=secret_body)},
    )

    with pytest.raises(SelfRetrievalCurrentReadUnavailableError) as error:
        context.execute(SelfContextRequest("query"))

    assert error.value.code == SelfRetrievalErrorCode.CURRENT_READ_UNAVAILABLE.value
    assert secret_body not in str(error.value)
    assert "30 Resources" not in str(error.value)


def test_duplicate_search_identity_is_safe_error_before_current_reads() -> None:
    duplicate_hits = (_hit(FIRST_ID), _hit(FIRST_ID, title="duplicate"))
    context, _search, retriever, model = _build(duplicate_hits, {FIRST_ID: _current(FIRST_ID)})

    with pytest.raises(SelfRetrievalSearchUnavailableError) as error:
        context.execute(SelfContextRequest("query", limit=2))

    assert error.value.code == SelfRetrievalErrorCode.SEARCH_UNAVAILABLE.value
    assert retriever.calls == []
    assert model.calls == []


def test_current_backend_failure_is_safe_error_without_partial_context() -> None:
    context, _search, retriever, _model = _build(
        (_hit(FIRST_ID), _hit(SECOND_ID)),
        {FIRST_ID: SearchIdentityConflictError("diagnostic path"), SECOND_ID: _current(SECOND_ID)},
    )

    with pytest.raises(SelfRetrievalCurrentReadUnavailableError) as error:
        context.execute(SelfContextRequest("query", limit=2))

    assert error.value.code == SelfRetrievalErrorCode.CURRENT_READ_UNAVAILABLE.value
    assert retriever.calls == [FIRST_ID]
    assert "diagnostic path" not in str(error.value)


def test_invalid_self_model_result_is_safe_error_before_current_reads() -> None:
    context, _search, retriever, model = _build(
        (_hit(FIRST_ID),),
        {FIRST_ID: _current(FIRST_ID)},
        self_model=SelfModelError("SELF_MODEL_RESULT_INVALID"),
    )

    with pytest.raises(SelfRetrievalSelfModelUnavailableError) as error:
        context.execute(SelfContextRequest("query"))

    assert error.value.code == SelfRetrievalErrorCode.SELF_MODEL_UNAVAILABLE.value
    assert model.calls == [SelfModelRequest()]
    assert retriever.calls == []


def test_public_result_budget_uses_title_body_tags_with_utf8_bytes() -> None:
    current = _current(FIRST_ID, title="Заголовок", body="Тело", tags=("тег", "two"))
    context, _search, _retriever, _model = _build((_hit(FIRST_ID),), {FIRST_ID: current})
    expected = len("\n".join(("Заголовок", "Тело", "тег", "two")).encode("utf-8"))

    result = context.execute(
        SelfContextRequest("query", max_content_bytes=max(DEFAULT_MAX_CONTENT_BYTES, expected))
    )

    assert result.content_bytes == expected
