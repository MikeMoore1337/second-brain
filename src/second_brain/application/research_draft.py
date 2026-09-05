"""Bounded provider-neutral orchestration для research -> LLM draft."""

from __future__ import annotations

from dataclasses import dataclass

from second_brain.application.llm import (
    DEFAULT_MAX_OUTPUT_BYTES,
    MAX_CONTEXT_BYTES,
    LlmGateway,
    LlmRequest,
    NoteDraft,
)
from second_brain.application.llm import _validate_request as _validate_llm_request
from second_brain.application.ports import (
    CancellationToken,
    LlmCancelledError,
    ResearchCancelledError,
    ResearchInvalidRequestError,
    ResearchMalformedResultError,
)
from second_brain.application.research import (
    DEFAULT_TIMEOUT_SECONDS,
    ResearchGateway,
    ResearchRequest,
    SourceKind,
    SourceProvenance,
)


@dataclass(frozen=True, slots=True)
class ResearchDraftRequest:
    """Входные данные одной bounded research -> LLM draft операции."""

    source_kind: SourceKind
    uri: str
    instruction: str
    research_timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS
    max_source_bytes: int = MAX_CONTEXT_BYTES
    max_output_bytes: int = DEFAULT_MAX_OUTPUT_BYTES


@dataclass(frozen=True, slots=True)
class ResearchDraftResult:
    """Внутренний результат research -> LLM с provenance рядом с draft."""

    draft: NoteDraft
    source: SourceProvenance


@dataclass(frozen=True, slots=True)
class ReviewedResearchDraft:
    """Immutable application boundary для reviewed draft будущего GUI."""

    draft: NoteDraft
    sources: tuple[SourceProvenance, ...]

    def __post_init__(self) -> None:
        """Ограничить v1 одним typed source и immutable persistence shape."""

        if type(self.draft) is not NoteDraft:
            raise ValueError("draft must be a NoteDraft")
        if type(self.sources) is not tuple or len(self.sources) != 1:
            raise ValueError("v1 reviewed research draft requires exactly one source")
        if any(type(source) is not SourceProvenance for source in self.sources):
            raise ValueError("sources must contain only SourceProvenance")

    @classmethod
    def from_result(cls, result: ResearchDraftResult) -> ReviewedResearchDraft:
        """Создать reviewed boundary из одного orchestration result."""

        if type(result) is not ResearchDraftResult:
            raise ValueError("result must be a ResearchDraftResult")
        return cls(draft=result.draft, sources=(result.source,))


@dataclass(frozen=True, slots=True)
class ResearchDraftGateway:
    """Последовательно выполнить максимум один research read и один LLM draft."""

    research_gateway: ResearchGateway
    llm_gateway: LlmGateway

    def draft_note(
        self,
        request: ResearchDraftRequest,
        *,
        cancellation: CancellationToken,
    ) -> ResearchDraftResult:
        """Прочитать один source, сохранить metadata и передать только content в LLM."""

        if _check_cancellation(cancellation):
            raise ResearchCancelledError()
        _validate_request(request)
        _validate_llm_request(
            LlmRequest(
                instruction=request.instruction,
                context="",
                max_output_bytes=request.max_output_bytes,
            )
        )

        source = self.research_gateway.read(
            ResearchRequest(
                source_kind=request.source_kind,
                uri=request.uri,
                timeout_seconds=request.research_timeout_seconds,
                max_bytes=request.max_source_bytes,
            ),
            cancellation=cancellation,
        )

        if _check_cancellation(cancellation):
            raise LlmCancelledError()

        try:
            provenance = SourceProvenance.from_research_source(source)
        except ValueError:
            raise ResearchMalformedResultError() from None

        draft = self.llm_gateway.draft_note(
            LlmRequest(
                instruction=request.instruction,
                context=source.content,
                max_output_bytes=request.max_output_bytes,
            ),
            cancellation=cancellation,
        )
        return ResearchDraftResult(draft=draft, source=provenance)


def _validate_request(request: ResearchDraftRequest) -> None:
    """Проверить orchestration-specific source cap до единственного read."""

    if type(request) is not ResearchDraftRequest:
        raise ResearchInvalidRequestError()
    if type(request.max_source_bytes) is not int or not (
        1 <= request.max_source_bytes <= MAX_CONTEXT_BYTES
    ):
        raise ResearchInvalidRequestError()


def _check_cancellation(cancellation: CancellationToken) -> bool:
    """Проверить cancellation token без callbacks, I/O или побочных эффектов."""

    checker = getattr(cancellation, "is_cancelled", None)
    if not callable(checker):
        raise ResearchInvalidRequestError()
    try:
        value = checker()
    except Exception:
        raise ResearchInvalidRequestError() from None
    if type(value) is not bool:
        raise ResearchInvalidRequestError()
    return value


__all__ = [
    "DEFAULT_MAX_OUTPUT_BYTES",
    "DEFAULT_TIMEOUT_SECONDS",
    "MAX_CONTEXT_BYTES",
    "CancellationToken",
    "ResearchDraftGateway",
    "ResearchDraftRequest",
    "ResearchDraftResult",
    "ReviewedResearchDraft",
    "SourceKind",
    "SourceProvenance",
]
