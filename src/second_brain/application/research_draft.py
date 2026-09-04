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
from second_brain.application.ports import (
    CancellationToken,
    LlmCancelledError,
    ResearchCancelledError,
    ResearchInvalidRequestError,
)
from second_brain.application.research import (
    DEFAULT_TIMEOUT_SECONDS,
    ResearchGateway,
    ResearchRequest,
    SourceKind,
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
class ResearchDraftGateway:
    """Последовательно выполнить максимум один research read и один LLM draft."""

    research_gateway: ResearchGateway
    llm_gateway: LlmGateway

    def draft_note(
        self,
        request: ResearchDraftRequest,
        *,
        cancellation: CancellationToken,
    ) -> NoteDraft:
        """Прочитать один source и передать его content только как LLM context."""

        if _check_cancellation(cancellation):
            raise ResearchCancelledError()
        _validate_request(request)

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

        return self.llm_gateway.draft_note(
            LlmRequest(
                instruction=request.instruction,
                context=source.content,
                max_output_bytes=request.max_output_bytes,
            ),
            cancellation=cancellation,
        )


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
    "SourceKind",
]
