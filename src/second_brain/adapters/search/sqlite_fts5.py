"""Rebuildable in-memory SQLite FTS5 adapter for local vault search."""

from __future__ import annotations

import json
import re
import sqlite3
import unicodedata
from contextlib import suppress
from datetime import datetime
from html.parser import HTMLParser
from threading import RLock
from typing import Final, cast
from uuid import UUID

from second_brain.application.ports import (
    SearchBackendUnavailableError,
    SearchDocument,
    SearchHit,
    SearchIdentityConflictError,
    SearchIndexFailedError,
    SearchInvalidRequestError,
    SearchQueryFailedError,
    SearchRequest,
)
from second_brain.application.search import (
    build_literal_match_expression,
    validate_search_document,
    validate_search_request,
)
from second_brain.domain.models import NoteType

SQLITE_FTS5_TOKENIZER: Final[str] = "unicode61 remove_diacritics 2"
SEARCH_TITLE_WEIGHT: Final[float] = 8.0
SEARCH_TAGS_WEIGHT: Final[float] = 5.0
SEARCH_PATH_WEIGHT: Final[float] = 3.0
SEARCH_BODY_WEIGHT: Final[float] = 1.0
MAX_SNIPPET_TOKENS: Final[int] = 32
MAX_SNIPPET_CHARS: Final[int] = 360

_SEARCH_METADATA_SCHEMA: Final[str] = """
CREATE TABLE search_metadata (
    rowid INTEGER PRIMARY KEY,
    note_id TEXT NOT NULL UNIQUE,
    note_type TEXT NOT NULL,
    relative_path TEXT NOT NULL,
    title TEXT NOT NULL,
    tags_json TEXT NOT NULL,
    created TEXT NOT NULL,
    updated TEXT,
    title_sort TEXT NOT NULL,
    path_sort TEXT NOT NULL
)
"""
_SEARCH_FTS_SCHEMA: Final[str] = f"""
CREATE VIRTUAL TABLE search_fts USING fts5(
    title,
    tags,
    relative_path,
    body,
    tokenize = '{SQLITE_FTS5_TOKENIZER}'
)
"""
_SEARCH_SQL: Final[str] = """
SELECT
    metadata.note_id,
    metadata.note_type,
    metadata.relative_path,
    metadata.title,
    metadata.tags_json,
    metadata.created,
    metadata.updated,
    snippet(search_fts, 3, '', '', '…', ?),
    bm25(search_fts, ?, ?, ?, ?)
FROM search_fts
JOIN search_metadata AS metadata ON metadata.rowid = search_fts.rowid
WHERE search_fts MATCH ?
ORDER BY
    bm25(search_fts, ?, ?, ?, ?) ASC,
    metadata.title_sort ASC,
    metadata.path_sort ASC,
    metadata.note_id ASC
LIMIT ?
"""


class SqliteFts5SearchIndex:
    """SearchIndexPort implementation backed only by a disposable ``:memory:`` DB."""

    def __init__(self) -> None:
        """Создать пустой adapter без filesystem, config или persistent state."""

        self._connection: sqlite3.Connection | None = None
        self._lock = RLock()

    def rebuild(self, documents: tuple[SearchDocument, ...]) -> None:
        """Атомарно построить новый FTS index и заменить старое derived state."""

        if type(documents) is not tuple:
            raise SearchIndexFailedError()
        candidate = self._create_connection()
        seen_ids: set[UUID] = set()
        try:
            with candidate:
                for rowid, document in enumerate(documents, start=1):
                    validated = validate_search_document(document)
                    if validated.note_id in seen_ids:
                        raise SearchIdentityConflictError()
                    seen_ids.add(validated.note_id)
                    self._insert_document(candidate, rowid, validated)
        except SearchInvalidRequestError:
            self._close_connection(candidate)
            raise SearchIndexFailedError() from None
        except SearchIdentityConflictError:
            self._close_connection(candidate)
            raise
        except SearchIndexFailedError:
            self._close_connection(candidate)
            raise
        except sqlite3.IntegrityError:
            self._close_connection(candidate)
            raise SearchIndexFailedError() from None
        except sqlite3.Error:
            self._close_connection(candidate)
            raise SearchIndexFailedError() from None
        except Exception:
            self._close_connection(candidate)
            raise SearchIndexFailedError() from None

        with self._lock:
            previous = self._connection
            self._connection = candidate
            self._close_connection(previous)

    def search(self, request: SearchRequest) -> tuple[SearchHit, ...]:
        """Выполнить только безопасный literal-term MATCH с deterministic ranking."""

        validate_search_request(request)
        expression = build_literal_match_expression(request.query)
        with self._lock:
            connection = self._connection
            if connection is None:
                raise SearchIndexFailedError()
            try:
                rows = connection.execute(
                    _SEARCH_SQL,
                    (
                        MAX_SNIPPET_TOKENS,
                        SEARCH_TITLE_WEIGHT,
                        SEARCH_TAGS_WEIGHT,
                        SEARCH_PATH_WEIGHT,
                        SEARCH_BODY_WEIGHT,
                        expression,
                        SEARCH_TITLE_WEIGHT,
                        SEARCH_TAGS_WEIGHT,
                        SEARCH_PATH_WEIGHT,
                        SEARCH_BODY_WEIGHT,
                        request.limit,
                    ),
                ).fetchall()
            except sqlite3.Error:
                raise SearchQueryFailedError() from None
            try:
                return tuple(_hit_from_row(row) for row in rows)
            except TypeError, ValueError, KeyError:
                raise SearchQueryFailedError() from None

    def close(self) -> None:
        """Закрыть disposable connection; повторный close безопасен."""

        with self._lock:
            connection = self._connection
            self._connection = None
            self._close_connection(connection)

    def _create_connection(self) -> sqlite3.Connection:
        """Создать SQLite FTS5 schema и классифицировать отсутствие FTS5 safe error-ом."""

        try:
            connection = sqlite3.connect(":memory:")
        except Exception:
            raise SearchBackendUnavailableError() from None
        try:
            connection.execute(_SEARCH_METADATA_SCHEMA)
            connection.execute(_SEARCH_FTS_SCHEMA)
        except sqlite3.OperationalError as error:
            self._close_connection(connection)
            if "fts5" in str(error).casefold() or "no such module" in str(error).casefold():
                raise SearchBackendUnavailableError() from None
            raise SearchIndexFailedError() from None
        except sqlite3.Error:
            self._close_connection(connection)
            raise SearchIndexFailedError() from None
        except Exception:
            self._close_connection(connection)
            raise SearchBackendUnavailableError() from None
        return connection

    @staticmethod
    def _insert_document(
        connection: sqlite3.Connection,
        rowid: int,
        document: SearchDocument,
    ) -> None:
        """Записать metadata и searchable fields только в candidate DB."""

        tags_json = json.dumps(document.tags, ensure_ascii=False, separators=(",", ":"))
        connection.execute(
            """
            INSERT INTO search_metadata (
                rowid, note_id, note_type, relative_path, title, tags_json,
                created, updated, title_sort, path_sort
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                rowid,
                str(document.note_id),
                document.note_type.value,
                document.relative_path,
                document.title,
                tags_json,
                document.created.isoformat(),
                document.updated.isoformat() if document.updated is not None else None,
                _sort_key(document.title),
                _sort_key(document.relative_path),
            ),
        )
        connection.execute(
            """
            INSERT INTO search_fts (rowid, title, tags, relative_path, body)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                rowid,
                document.title,
                " ".join(document.tags),
                document.relative_path,
                document.body,
            ),
        )

    @staticmethod
    def _close_connection(connection: sqlite3.Connection | None) -> None:
        """Закрыть SQLite connection без маскировки уже завершённой операции."""

        if connection is not None:
            with suppress(sqlite3.Error):
                connection.close()


def _hit_from_row(row: tuple[object, ...]) -> SearchHit:
    """Преобразовать internal SQL row в provider-neutral SearchHit."""

    if len(row) != 9:
        raise ValueError("unexpected search row")
    (
        note_id_text,
        note_type_text,
        relative_path,
        title,
        tags_json,
        created,
        updated,
        snippet,
        _rank,
    ) = row
    if (
        type(note_id_text) is not str
        or type(note_type_text) is not str
        or type(relative_path) is not str
        or type(title) is not str
        or type(tags_json) is not str
        or type(created) is not str
        or (updated is not None and type(updated) is not str)
        or type(snippet) is not str
    ):
        raise ValueError("invalid search row")
    try:
        note_id = UUID(note_id_text)
        note_type = NoteType(note_type_text)
        tags_value = json.loads(tags_json)
        if type(tags_value) is not list or not all(type(tag) is str for tag in tags_value):
            raise ValueError("invalid search row")
        tags = tuple(cast(list[str], tags_value))
        created_value = datetime_from_isoformat(created)
        updated_value = None if updated is None else datetime_from_isoformat(updated)
    except TypeError, ValueError:
        raise ValueError("invalid search row") from None
    if note_id.version != 7:
        raise ValueError("invalid search row")
    if type(note_type) is not NoteType:
        raise ValueError("invalid search row")
    return SearchHit(
        note_id=note_id,
        note_type=note_type,
        relative_path=relative_path,
        title=title,
        tags=tags,
        created=created_value,
        updated=updated_value,
        snippet=_bounded_plain_text(snippet),
    )


def datetime_from_isoformat(value: str) -> datetime:
    """Разобрать internal ISO timestamp, сохраняя strict timezone requirement."""

    result = datetime.fromisoformat(value)
    if result.tzinfo is None or result.utcoffset() is None:
        raise ValueError("timestamp has no explicit offset")
    return result


def _sort_key(value: str) -> str:
    """Стабильный Unicode-aware tie-break key, не зависящий от SQLite collation."""

    return unicodedata.normalize("NFKC", value).casefold()


class _PlainTextParser(HTMLParser):
    """Собрать text nodes без переноса HTML tags в SearchHit."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []

    def handle_data(self, data: str) -> None:
        self.parts.append(data)


def _bounded_plain_text(value: str) -> str:
    """Нормализовать snippet в bounded whitespace-only plain text."""

    parser = _PlainTextParser()
    try:
        parser.feed(value)
        parser.close()
        text = "".join(parser.parts)
    except Exception:
        text = re.sub(r"<[^>]*>", "", value)
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) <= MAX_SNIPPET_CHARS:
        return text
    return text[: MAX_SNIPPET_CHARS - 1].rstrip() + "…"


# Public aliases accommodate both common spellings while keeping one adapter.
SQLiteFts5SearchIndex = SqliteFts5SearchIndex
SQLiteFTS5SearchIndex = SqliteFts5SearchIndex

__all__ = [
    "MAX_SNIPPET_CHARS",
    "MAX_SNIPPET_TOKENS",
    "SEARCH_BODY_WEIGHT",
    "SEARCH_PATH_WEIGHT",
    "SEARCH_TAGS_WEIGHT",
    "SEARCH_TITLE_WEIGHT",
    "SQLITE_FTS5_TOKENIZER",
    "SQLiteFTS5SearchIndex",
    "SQLiteFts5SearchIndex",
    "SqliteFts5SearchIndex",
]
