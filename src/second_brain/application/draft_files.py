"""Bounded strict decoder для reviewed NoteDraft JSON-файла."""

from __future__ import annotations

import json
import os
import stat
from contextlib import suppress
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
        raw = _read_bounded_regular_file(path, max_bytes)
    except OSError, TypeError, UnicodeError, ValueError:
        raise DraftFileError("DRAFT_FILE_INVALID") from None
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


class _InvalidRegularFileError(ValueError):
    """Внутренний отказ для всего, что не является обычным regular file."""


def _read_bounded_regular_file(path: Path, max_bytes: int) -> bytes:
    """Безопасно прочитать bounded bytes только из открытого regular-file FD."""

    descriptor: int | None = None
    initial_stat = os.lstat(path)
    _require_regular_input(path, initial_stat)
    nofollow = getattr(os, "O_NOFOLLOW", 0) or 0
    flags = os.O_RDONLY
    flags |= getattr(os, "O_NONBLOCK", 0) or 0
    flags |= nofollow
    if os.name == "nt":
        flags |= getattr(os, "O_BINARY", 0) or 0
    try:
        descriptor = os.open(path, flags)
        opened_stat = os.fstat(descriptor)
        if not stat.S_ISREG(opened_stat.st_mode):
            raise _InvalidRegularFileError()
        current_stat = os.lstat(path)
        _require_regular_input(path, current_stat)
        if not os.path.samestat(initial_stat, opened_stat) or not os.path.samestat(
            current_stat, opened_stat
        ):
            raise _InvalidRegularFileError()
        with os.fdopen(descriptor, "rb", closefd=True) as stream:
            descriptor = None
            raw = stream.read(max_bytes + 1)
        if len(raw) > max_bytes:
            raise _InvalidRegularFileError()
        return raw
    finally:
        if descriptor is not None:
            with suppress(OSError):
                os.close(descriptor)


def _require_regular_input(path: Path, result: os.stat_result) -> None:
    """Fail closed for links, reparse-like paths and all non-regular objects."""

    if _is_link_like_input(path, result) or not stat.S_ISREG(result.st_mode):
        raise _InvalidRegularFileError()


def _is_link_like_input(path: Path, result: os.stat_result) -> bool:
    """Распознать symlink/reparse-like input без следования к target."""

    if stat.S_ISLNK(result.st_mode):
        return True
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0) or 0
    attributes = getattr(result, "st_file_attributes", 0)
    if reparse_flag and isinstance(attributes, int) and attributes & reparse_flag:
        return True
    is_junction = getattr(path, "is_junction", None)
    if is_junction is not None:
        try:
            return bool(is_junction())
        except OSError:
            return True
    return False


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
