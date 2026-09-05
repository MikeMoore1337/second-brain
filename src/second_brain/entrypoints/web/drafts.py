"""Web composition для read-only draft generation."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final, Protocol

from second_brain.adapters.llm import CloudflareWorkersAiLlmPort
from second_brain.adapters.research.jina_reader import JinaReaderWebAdapter
from second_brain.application.llm import LlmGateway, LlmRequest, NoteDraft
from second_brain.application.ports import CancellationTokenSource
from second_brain.application.research import ResearchGateway, SourceKind
from second_brain.application.research_draft import (
    ResearchDraftGateway,
    ResearchDraftRequest,
    ResearchDraftResult,
)

CAPTURE_INSTRUCTION: Final[str] = (
    "Преврати входной материал в одну полезную долговечную заметку Second Brain.\n"
    "Сохрани факты и смысл, не выдумывай отсутствующие данные.\n"
    "Выбери подходящий managed note_type.\n"
    "Сделай content самостоятельным для будущего чтения.\n"
    "Добавляй tags и links только если они обоснованы материалом.\n"
    "Не добавляй source URL в body или links автоматически только ради provenance."
)


class DraftService(Protocol):
    """Минимальный injectable seam между HTTP boundary и application gateways."""

    def draft_text(self, text: str) -> NoteDraft:
        """Создать один draft из пользовательского текста."""

    def draft_url(self, url: str) -> ResearchDraftResult:
        """Прочитать один public WEB source и создать один draft."""


@dataclass(frozen=True, slots=True)
class GatewayDraftService:
    """Связать web input с существующими gateway без дополнительного контейнера."""

    llm_gateway: LlmGateway
    research_draft_gateway: ResearchDraftGateway

    def draft_text(self, text: str) -> NoteDraft:
        """Передать текст в ``LlmRequest.context`` без изменения или metadata."""

        cancellation = CancellationTokenSource()
        return self.llm_gateway.draft_note(
            LlmRequest(instruction=CAPTURE_INSTRUCTION, context=text),
            cancellation=cancellation.token,
        )

    def draft_url(self, url: str) -> ResearchDraftResult:
        """Ограничить Web API обычным ``SourceKind.WEB``."""

        cancellation = CancellationTokenSource()
        return self.research_draft_gateway.draft_note(
            ResearchDraftRequest(
                source_kind=SourceKind.WEB,
                uri=url,
                instruction=CAPTURE_INSTRUCTION,
            ),
            cancellation=cancellation.token,
        )


def build_production_draft_service() -> GatewayDraftService:
    """Собрать production gateways без чтения credentials или внешнего вызова."""

    llm_gateway = LlmGateway(CloudflareWorkersAiLlmPort())
    research_gateway = ResearchGateway(JinaReaderWebAdapter())
    return GatewayDraftService(
        llm_gateway=llm_gateway,
        research_draft_gateway=ResearchDraftGateway(research_gateway, llm_gateway),
    )


__all__ = [
    "CAPTURE_INSTRUCTION",
    "DraftService",
    "GatewayDraftService",
    "build_production_draft_service",
]
