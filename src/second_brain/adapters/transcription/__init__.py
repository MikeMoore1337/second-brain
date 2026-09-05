"""Production transcription adapters."""

from .cloudflare_workers_ai import (
    CLOUDFLARE_TRANSCRIPTION_MODEL,
    CLOUDFLARE_TRANSCRIPTION_PATH,
    MAX_TRANSCRIPTION_RESPONSE_BYTES,
    TRANSCRIPTION_TIMEOUT_SECONDS,
    CloudflareWorkersAiTranscriptionPort,
)

__all__ = [
    "CLOUDFLARE_TRANSCRIPTION_MODEL",
    "CLOUDFLARE_TRANSCRIPTION_PATH",
    "MAX_TRANSCRIPTION_RESPONSE_BYTES",
    "TRANSCRIPTION_TIMEOUT_SECONDS",
    "CloudflareWorkersAiTranscriptionPort",
]
