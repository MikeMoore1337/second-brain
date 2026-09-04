"""Provider-neutral application contract для structured LLM note drafts."""

from __future__ import annotations

import json
import unicodedata
from collections.abc import Callable
from dataclasses import dataclass
from typing import cast

from second_brain.application.ports import (
    CancellationToken,
    LlmBackendUnavailableError,
    LlmCancelledError,
    LlmContentTooLargeError,
    LlmError,
    LlmErrorCode,
    LlmInvalidRequestError,
    LlmMalformedResultError,
    LlmPort,
    LlmTimeoutError,
    LlmUpstreamError,
)
from second_brain.domain.models import NoteType

DEFAULT_MAX_OUTPUT_BYTES = 64 * 1024
MAX_MAX_OUTPUT_BYTES = 256 * 1024
MAX_INSTRUCTION_BYTES = 8 * 1024
MAX_CONTEXT_BYTES = 128 * 1024
MAX_TITLE_BYTES = 512
MAX_CONTENT_BYTES = 256 * 1024
MAX_TAGS = 32
MAX_TAG_BYTES = 128
MAX_LINKS = 32
MAX_LINK_BYTES = 2 * 1024

_MANAGED_NOTE_TYPES = frozenset(
    {NoteType.PROJECT, NoteType.AREA, NoteType.RESOURCE, NoteType.ZETTEL}
)
_ALLOWED_TEXT_CONTROLS = frozenset({"\t", "\n", "\r"})


@dataclass(frozen=True, slots=True)
class LlmRequest:
    """Ограниченный provider-neutral запрос одной draft operation."""

    instruction: str
    context: str = ""
    max_output_bytes: int = DEFAULT_MAX_OUTPUT_BYTES


@dataclass(frozen=True, slots=True)
class NoteDraft:
    """Semantic draft без path, identity, timestamp, approval или write receipt."""

    title: str
    note_type: NoteType
    content: str
    tags: tuple[str, ...] = ()
    links: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class LlmGateway:
    """Тонкая LLM application boundary без provider, network и write capabilities."""

    port: LlmPort

    def draft_note(
        self,
        request: LlmRequest,
        *,
        cancellation: CancellationToken,
    ) -> NoteDraft:
        """Проверить request, вызвать один port и вернуть validated NoteDraft."""

        if _check_cancellation(cancellation):
            raise LlmCancelledError()
        _validate_request(request)

        try:
            draft = self.port.draft_note(request, cancellation=cancellation)
        except Exception as exc:
            raise _map_port_exception(exc) from None

        if _check_cancellation(cancellation):
            raise LlmCancelledError()
        _validate_draft(draft, request.max_output_bytes)
        return draft


def _validate_request(request: object) -> None:
    """Fail-closed request policy до единственного port call."""

    if type(request) is not LlmRequest:
        raise LlmInvalidRequestError()
    _validate_text(
        request.instruction,
        max_bytes=MAX_INSTRUCTION_BYTES,
        error=LlmInvalidRequestError,
        too_large=LlmInvalidRequestError,
        allow_line_breaks=True,
        require_non_empty=True,
    )
    _validate_text(
        request.context,
        max_bytes=MAX_CONTEXT_BYTES,
        error=LlmInvalidRequestError,
        too_large=LlmInvalidRequestError,
        allow_line_breaks=True,
    )
    if type(request.max_output_bytes) is not int or not (
        1 <= request.max_output_bytes <= MAX_MAX_OUTPUT_BYTES
    ):
        raise LlmInvalidRequestError()


def _validate_draft(draft: object, max_output_bytes: int) -> None:
    """Проверить typed result и его bounded semantic fields."""

    if type(draft) is not NoteDraft:
        raise LlmMalformedResultError()
    if type(draft.note_type) is not NoteType or draft.note_type not in _MANAGED_NOTE_TYPES:
        raise LlmMalformedResultError()
    _validate_text(
        draft.title,
        max_bytes=MAX_TITLE_BYTES,
        error=LlmMalformedResultError,
        too_large=LlmMalformedResultError,
        allow_line_breaks=False,
        require_non_empty=True,
    )
    _validate_text(
        draft.content,
        max_bytes=MAX_CONTENT_BYTES,
        error=LlmMalformedResultError,
        too_large=LlmContentTooLargeError,
        allow_line_breaks=True,
    )
    _validate_tags(draft.tags)
    _validate_links(draft.links)
    if _combined_draft_bytes(draft) > max_output_bytes:
        raise LlmContentTooLargeError()


def validate_note_draft(
    draft: object,
    max_output_bytes: int = MAX_MAX_OUTPUT_BYTES,
) -> NoteDraft:
    """Проверить и вернуть NoteDraft через тот же semantic validator, что и LLM."""

    if type(max_output_bytes) is not int or not (1 <= max_output_bytes <= MAX_MAX_OUTPUT_BYTES):
        raise LlmInvalidRequestError()
    _validate_draft(draft, max_output_bytes)
    return cast(NoteDraft, draft)


def _validate_tags(tags: object) -> None:
    """Проверить bounded tags и отклонить casefold/NFKC-дубликаты."""

    if type(tags) is not tuple or len(tags) > MAX_TAGS:
        raise LlmMalformedResultError()
    seen: set[str] = set()
    for tag in tags:
        _validate_text(
            tag,
            max_bytes=MAX_TAG_BYTES,
            error=LlmMalformedResultError,
            too_large=LlmMalformedResultError,
            allow_line_breaks=False,
            require_non_empty=True,
        )
        assert isinstance(tag, str)
        key = unicodedata.normalize("NFKC", tag).strip().casefold()
        if key in seen:
            raise LlmMalformedResultError()
        seen.add(key)


def _validate_links(links: object) -> None:
    """Проверить bounded semantic link candidates без разрешения или I/O."""

    if type(links) is not tuple or len(links) > MAX_LINKS:
        raise LlmMalformedResultError()
    for link in links:
        _validate_text(
            link,
            max_bytes=MAX_LINK_BYTES,
            error=LlmMalformedResultError,
            too_large=LlmMalformedResultError,
            allow_line_breaks=False,
            require_non_empty=True,
        )


def _validate_text(
    value: object,
    *,
    max_bytes: int,
    error: Callable[[], LlmError],
    too_large: Callable[[], LlmError],
    allow_line_breaks: bool,
    require_non_empty: bool = False,
) -> int:
    """Проверить строку без нормализации и вернуть UTF-8 byte size."""

    if type(value) is not str:
        raise error()
    if len(value) > max_bytes:
        raise too_large()
    if require_non_empty and not value.strip():
        raise error()
    if _contains_forbidden_control(value, allow_line_breaks=allow_line_breaks):
        raise error()
    try:
        size = len(value.encode("utf-8"))
    except UnicodeEncodeError:
        raise error() from None
    if size > max_bytes:
        raise too_large()
    return size


def _contains_forbidden_control(value: str, *, allow_line_breaks: bool) -> bool:
    """Разрешить только обычные Markdown/text whitespace controls."""

    for char in value:
        codepoint = ord(char)
        if codepoint < 32:
            if allow_line_breaks and char in _ALLOWED_TEXT_CONTROLS:
                continue
            return True
        if 0x7F <= codepoint <= 0x9F or char in {"\u2028", "\u2029"}:
            return True
    return False


def _combined_draft_bytes(draft: NoteDraft) -> int:
    """Посчитать размер стабильного compact UTF-8 envelope для budget check."""

    payload = {
        "title": draft.title,
        "note_type": draft.note_type.value,
        "content": draft.content,
        "tags": draft.tags,
        "links": draft.links,
    }
    try:
        return len(
            json.dumps(
                payload,
                ensure_ascii=False,
                separators=(",", ":"),
            ).encode("utf-8")
        )
    except TypeError, UnicodeEncodeError:
        raise LlmMalformedResultError() from None


def _check_cancellation(cancellation: CancellationToken) -> bool:
    """Проверить cancellation token без запуска callbacks или I/O."""

    checker = getattr(cancellation, "is_cancelled", None)
    if not callable(checker):
        raise LlmInvalidRequestError()
    try:
        value = checker()
    except Exception:
        raise LlmInvalidRequestError() from None
    if type(value) is not bool:
        raise LlmInvalidRequestError()
    return value


def _map_port_exception(error: Exception) -> LlmError:
    """Скрыть provider details и свести port exception к taxonomy."""

    if isinstance(error, LlmError):
        error_types: dict[str, Callable[[], LlmError]] = {
            LlmErrorCode.INVALID_REQUEST.value: LlmInvalidRequestError,
            LlmErrorCode.CANCELLED.value: LlmCancelledError,
            LlmErrorCode.TIMEOUT.value: LlmTimeoutError,
            LlmErrorCode.BACKEND_UNAVAILABLE.value: LlmBackendUnavailableError,
            LlmErrorCode.UPSTREAM_FAILURE.value: LlmUpstreamError,
            LlmErrorCode.MALFORMED_RESULT.value: LlmMalformedResultError,
            LlmErrorCode.CONTENT_TOO_LARGE.value: LlmContentTooLargeError,
        }
        return error_types.get(error.code, LlmUpstreamError)()
    if isinstance(error, TimeoutError):
        return LlmTimeoutError()
    if isinstance(error, (ConnectionError, OSError)):
        return LlmBackendUnavailableError()
    return LlmUpstreamError()


__all__ = [
    "DEFAULT_MAX_OUTPUT_BYTES",
    "MAX_CONTENT_BYTES",
    "MAX_CONTEXT_BYTES",
    "MAX_INSTRUCTION_BYTES",
    "MAX_LINKS",
    "MAX_LINK_BYTES",
    "MAX_MAX_OUTPUT_BYTES",
    "MAX_TAGS",
    "MAX_TAG_BYTES",
    "MAX_TITLE_BYTES",
    "CancellationToken",
    "LlmBackendUnavailableError",
    "LlmCancelledError",
    "LlmContentTooLargeError",
    "LlmError",
    "LlmErrorCode",
    "LlmGateway",
    "LlmInvalidRequestError",
    "LlmMalformedResultError",
    "LlmPort",
    "LlmRequest",
    "LlmTimeoutError",
    "LlmUpstreamError",
    "NoteDraft",
    "validate_note_draft",
]
