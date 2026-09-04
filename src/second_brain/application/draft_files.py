"""Bounded strict decoder для reviewed NoteDraft JSON-файла."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from second_brain.application.llm import (
    MAX_MAX_OUTPUT_BYTES,
    NoteDraft,
    validate_note_draft,
)
from second_brain.application.ports import LlmError
from second_brain.domain.models import NoteType

MAX_NOTE_DRAFT_FILE_BYTES = MAX_MAX_OUTPUT_BYTES * 4
NOTE_DRAFT_FIELDS = ("title", "note_type", "content", "tags", "links")

_DRAFT_ERROR_MESSAGES = {
    "DRAFT_FILE_INVALID": "draft file must be a bounded UTF-8 file",
    "DRAFT_JSON_INVALID": "draft file must contain strict JSON",
    "DRAFT_SCHEMA_INVALID": "draft file must contain one valid NoteDraft",
}


class DraftFileError(ValueError):
    """Безопасная ошибка чтения или строгой проверки draft-файла."""

    def __init__(self, code: str) -> None:
        normalized = code if code in _DRAFT_ERROR_MESSAGES else "DRAFT_FILE_INVALID"
        self.code = normalized
        self.message = _DRAFT_ERROR_MESSAGES[normalized]
        super().__init__(self.message)


class _DuplicateJsonKeyError(ValueError):
    """Внутренняя ошибка duplicate-key policy."""


def read_note_draft_file(
    path: Path,
    *,
    max_bytes: int = MAX_NOTE_DRAFT_FILE_BYTES,
) -> NoteDraft:
    """Прочитать ровно один bounded reviewed NoteDraft без любого I/O кроме read."""

    if (
        not isinstance(path, Path)
        or type(max_bytes) is not int
        or not (1 <= max_bytes <= MAX_NOTE_DRAFT_FILE_BYTES)
    ):
        raise DraftFileError("DRAFT_FILE_INVALID")
    try:
        with path.open("rb") as stream:
            raw = stream.read(max_bytes + 1)
    except OSError, ValueError:
        raise DraftFileError("DRAFT_FILE_INVALID") from None
    if len(raw) > max_bytes:
        raise DraftFileError("DRAFT_FILE_INVALID")
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        raise DraftFileError("DRAFT_FILE_INVALID") from None

    try:
        payload = json.loads(
            text,
            object_pairs_hook=_reject_duplicate_keys,
            parse_constant=_reject_non_finite,
        )
    except json.JSONDecodeError, _DuplicateJsonKeyError, RecursionError, ValueError:
        raise DraftFileError("DRAFT_JSON_INVALID") from None

    draft = _note_draft_from_payload(payload)
    try:
        return validate_note_draft(draft, max_output_bytes=MAX_MAX_OUTPUT_BYTES)
    except LlmError:
        raise DraftFileError("DRAFT_SCHEMA_INVALID") from None


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    """Запретить duplicate keys на каждом JSON object уровне."""

    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise _DuplicateJsonKeyError()
        result[key] = value
    return result


def _reject_non_finite(value: str) -> None:
    """Отклонить нестандартные JSON constants NaN/Infinity."""

    del value
    raise ValueError()


def _note_draft_from_payload(payload: object) -> NoteDraft:
    """Проверить exact five-field shape и собрать typed NoteDraft без coercion."""

    if type(payload) is not dict or set(payload) != set(NOTE_DRAFT_FIELDS):
        raise DraftFileError("DRAFT_SCHEMA_INVALID")
    values = payload
    if not all(type(values[field]) is str for field in ("title", "note_type", "content")):
        raise DraftFileError("DRAFT_SCHEMA_INVALID")
    if type(values["tags"]) is not list or type(values["links"]) is not list:
        raise DraftFileError("DRAFT_SCHEMA_INVALID")
    tags = values["tags"]
    links = values["links"]
    if not all(type(item) is str for item in tags) or not all(type(item) is str for item in links):
        raise DraftFileError("DRAFT_SCHEMA_INVALID")
    try:
        note_type = NoteType(values["note_type"])
    except ValueError:
        raise DraftFileError("DRAFT_SCHEMA_INVALID") from None
    if note_type is NoteType.NOTE:
        raise DraftFileError("DRAFT_SCHEMA_INVALID")
    return NoteDraft(
        title=values["title"],
        note_type=note_type,
        content=values["content"],
        tags=tuple(tags),
        links=tuple(links),
    )


__all__ = [
    "MAX_NOTE_DRAFT_FILE_BYTES",
    "NOTE_DRAFT_FIELDS",
    "DraftFileError",
    "read_note_draft_file",
]
