"""Web composition для bounded transcription без LLM или Safe Write side effects."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from second_brain.adapters.transcription import CloudflareWorkersAiTranscriptionPort
from second_brain.application.ports import CancellationTokenSource
from second_brain.application.transcription import (
    Transcript,
    TranscriptionGateway,
    TranscriptionRequest,
)


class TranscriptionService(Protocol):
    """Минимальный injectable seam между raw audio HTTP boundary и gateway."""

    def transcribe(self, audio: bytes, media_type: str) -> Transcript:
        """Распознать один audio body без сохранения или следующего use case."""


@dataclass(frozen=True, slots=True)
class GatewayTranscriptionService:
    """Связать Web audio input с provider-neutral ``TranscriptionGateway``."""

    gateway: TranscriptionGateway

    def transcribe(self, audio: bytes, media_type: str) -> Transcript:
        """Создать request в памяти и выполнить одну bounded transcription operation."""

        cancellation = CancellationTokenSource()
        return self.gateway.transcribe(
            TranscriptionRequest(audio=audio, media_type=media_type),
            cancellation=cancellation.token,
        )


def build_production_transcription_service() -> GatewayTranscriptionService:
    """Собрать production service без чтения credentials до реального вызова."""

    return GatewayTranscriptionService(
        gateway=TranscriptionGateway(CloudflareWorkersAiTranscriptionPort())
    )


__all__ = [
    "GatewayTranscriptionService",
    "TranscriptionService",
    "build_production_transcription_service",
]
