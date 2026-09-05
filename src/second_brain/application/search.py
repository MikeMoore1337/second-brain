"""Provider-neutral Search/Retrieval use cases над каноническим vault scan."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from datetime import datetime
from pathlib import PurePosixPath
from typing import Final
from uuid import UUID

from second_brain.application.ports import (
    RetrievedNote,
    SearchBackendUnavailableError,
    SearchContentTooLargeError,
    SearchDocument,
    SearchError,
    SearchErrorCode,
    SearchHit,
    SearchIdentityConflictError,
    SearchIndexFailedError,
    SearchIndexPort,
    SearchInvalidRequestError,
    SearchNotFoundError,
    SearchQueryFailedError,
    SearchRequest,
    VaultReader,
)
from second_brain.application.reports import ScanReport
from second_brain.application.validation import build_report
from second_brain.domain.models import NoteRecord, NoteType

DEFAULT_SEARCH_LIMIT: Final[int] = 20
MIN_SEARCH_LIMIT: Final[int] = 1
MAX_SEARCH_LIMIT: Final[int] = 50
MAX_SEARCH_QUERY_BYTES: Final[int] = 4 * 1024
MAX_SEARCH_QUERY_TERMS: Final[int] = 32

# Short aliases keep the policy easy to discover for adapters and tests.
MAX_QUERY_BYTES: Final[int] = MAX_SEARCH_QUERY_BYTES
MAX_QUERY_TERMS: Final[int] = MAX_SEARCH_QUERY_TERMS
MIN_LIMIT: Final[int] = MIN_SEARCH_LIMIT
MAX_LIMIT: Final[int] = MAX_SEARCH_LIMIT

_TERM_PATTERN: Final[re.Pattern[str]] = re.compile(r"[^\W_]+", re.UNICODE)
_SAFE_RELATIVE_PATH_PARTS: Final[frozenset[str]] = frozenset({"", ".", ".."})


@dataclass(frozen=True, slots=True)
class SearchVault:
    """Пересобрать derived index из current ``VaultReader`` и выполнить поиск."""

    reader: VaultReader
    index: SearchIndexPort

    def execute(self, request: SearchRequest) -> tuple[SearchHit, ...]:
        """Прочитать current vault, project notes, rebuild index и вернуть hits."""

        validate_search_request(request)
        report = _read_report(self.reader)
        documents = project_search_documents(report)
        try:
            self.index.rebuild(documents)
        except SearchError:
            raise
        except Exception:
            raise SearchIndexFailedError() from None
        try:
            return self.index.search(request)
        except SearchError:
            raise
        except Exception:
            raise SearchQueryFailedError() from None


@dataclass(frozen=True, slots=True)
class RetrieveManagedNote:
    """Получить exact current managed note повторным чтением canonical vault."""

    reader: VaultReader

    def execute(self, note_id: UUID) -> RetrievedNote:
        """Найти ровно одну searchable note по UUID без обращения к search index."""

        _validate_note_id(note_id)
        report = _read_report(self.reader)
        searchable_notes = _searchable_notes(report)
        matches = [note for note in searchable_notes if note.note_id == note_id]
        if not matches:
            raise SearchNotFoundError()
        if len(matches) != 1:
            raise SearchIdentityConflictError()
        return _retrieved_note(matches[0])


def validate_search_request(request: object) -> SearchRequest:
    """Проверить bounded user query до чтения vault или вызова index port."""

    if type(request) is not SearchRequest:
        raise SearchInvalidRequestError()
    if type(request.query) is not str:
        raise SearchInvalidRequestError()
    try:
        query_bytes = request.query.encode("utf-8")
    except UnicodeEncodeError:
        raise SearchInvalidRequestError() from None
    if len(query_bytes) > MAX_SEARCH_QUERY_BYTES:
        raise SearchInvalidRequestError()
    if not request.query.strip() or _contains_forbidden_control(request.query):
        raise SearchInvalidRequestError()
    if type(request.limit) is not int or not MIN_SEARCH_LIMIT <= request.limit <= MAX_SEARCH_LIMIT:
        raise SearchInvalidRequestError()
    return request


def build_literal_match_expression(query: str) -> str:
    """Построить deterministic FTS literal expression с AND semantics."""

    if type(query) is not str:
        raise SearchInvalidRequestError()
    try:
        if len(query.encode("utf-8")) > MAX_SEARCH_QUERY_BYTES:
            raise SearchInvalidRequestError()
    except UnicodeEncodeError:
        raise SearchInvalidRequestError() from None
    if not query.strip() or _contains_forbidden_control(query):
        raise SearchInvalidRequestError()

    normalized = unicodedata.normalize("NFKC", query)
    raw_terms = _TERM_PATTERN.findall(normalized)
    if len(raw_terms) > MAX_SEARCH_QUERY_TERMS:
        raise SearchInvalidRequestError()
    terms = tuple(dict.fromkeys(raw_terms))
    if not terms:
        raise SearchInvalidRequestError()
    return " AND ".join(_quote_fts_term(term) for term in terms)


def project_search_documents(report: ScanReport) -> tuple[SearchDocument, ...]:
    """Спроецировать только notes со stable identity из уже построенного report."""

    if report.manifest is None:
        raise SearchBackendUnavailableError()
    documents: list[SearchDocument] = []
    for note in _searchable_notes(report):
        if note.note_id is None or note.note_type is None or note.created is None:
            continue
        document = SearchDocument(
            note_id=note.note_id,
            note_type=note.note_type,
            relative_path=note.relative_path,
            title=_title_from_relative_path(note.relative_path),
            body=note.body,
            tags=note.tags,
            created=note.created,
            updated=note.updated,
        )
        validate_search_document(document)
        documents.append(document)
    return tuple(documents)


def validate_search_document(document: object) -> SearchDocument:
    """Проверить provider-neutral projection перед передачей index adapter."""

    if type(document) is not SearchDocument:
        raise SearchIndexFailedError()
    _validate_search_document(document)
    return document


def _read_report(reader: VaultReader) -> ScanReport:
    """Пройти ровно через ``reader.scan() -> build_report()`` и скрыть read details."""

    try:
        report = build_report(reader.scan())
    except SearchError:
        raise
    except Exception:
        raise SearchBackendUnavailableError() from None
    if report.manifest is None:
        raise SearchBackendUnavailableError()
    return report


def _searchable_notes(report: ScanReport) -> tuple[NoteRecord, ...]:
    """Отфильтровать stable managed notes и fail closed на duplicate UUID."""

    notes: list[NoteRecord] = []
    seen_ids: set[UUID] = set()
    for note in report.notes:
        if not (
            note.managed is True
            and note.note_id is not None
            and note.note_type is not None
            and note.created is not None
        ):
            continue
        if note.note_id in seen_ids:
            raise SearchIdentityConflictError()
        seen_ids.add(note.note_id)
        notes.append(note)
    return tuple(notes)


def _retrieved_note(note: NoteRecord) -> RetrievedNote:
    """Собрать safe DTO только из current validated ``NoteRecord``."""

    if note.note_id is None or note.note_type is None or note.created is None:
        raise SearchNotFoundError()
    return RetrievedNote(
        note_id=note.note_id,
        note_type=note.note_type,
        relative_path=note.relative_path,
        title=_title_from_relative_path(note.relative_path),
        body=note.body,
        tags=note.tags,
        created=note.created,
        updated=note.updated,
    )


def _validate_note_id(note_id: object) -> None:
    """Потребовать UUIDv7 до filesystem scan."""

    if type(note_id) is not UUID or note_id.version != 7:
        raise SearchInvalidRequestError()


def _validate_search_document(document: SearchDocument) -> None:
    """Проверить projection перед передачей provider/index adapter."""

    if type(document) is not SearchDocument:
        raise SearchIndexFailedError()
    if type(document.note_id) is not UUID or document.note_id.version != 7:
        raise SearchIndexFailedError()
    if type(document.note_type) is not NoteType:
        raise SearchIndexFailedError()
    if type(document.relative_path) is not str or not _is_safe_relative_path(
        document.relative_path
    ):
        raise SearchIndexFailedError()
    if type(document.title) is not str or type(document.body) is not str:
        raise SearchIndexFailedError()
    if type(document.tags) is not tuple or not all(type(tag) is str for tag in document.tags):
        raise SearchIndexFailedError()
    if not _has_explicit_offset(document.created):
        raise SearchIndexFailedError()
    if document.updated is not None and not _has_explicit_offset(document.updated):
        raise SearchIndexFailedError()


def _title_from_relative_path(relative_path: str) -> str:
    """Вывести title из basename path без обязательного front matter field."""

    name = PurePosixPath(relative_path).name
    return name[:-3] if name.casefold().endswith(".md") else name


def _is_safe_relative_path(value: str) -> bool:
    """Разрешить только application-owned normalized relative POSIX path."""

    if not value or "\\" in value:
        return False
    path = PurePosixPath(value)
    return not (
        path.is_absolute()
        or path.as_posix() != value
        or any(part in _SAFE_RELATIVE_PATH_PARTS for part in path.parts)
    )


def _has_explicit_offset(value: object) -> bool:
    """Проверить timezone-aware datetime для stable DTO serialization."""

    return (
        isinstance(value, datetime) and value.tzinfo is not None and value.utcoffset() is not None
    )


def _contains_forbidden_control(value: str) -> bool:
    """Отклонить C0/C1 и Unicode format controls в user query."""

    return any(unicodedata.category(char) in {"Cc", "Cf"} for char in value)


def _quote_fts_term(term: str) -> str:
    """Ограничить пользовательский term одной FTS phrase literal."""

    return '"' + term.replace('"', '""') + '"'


__all__ = [
    "DEFAULT_SEARCH_LIMIT",
    "MAX_LIMIT",
    "MAX_QUERY_BYTES",
    "MAX_QUERY_TERMS",
    "MAX_SEARCH_LIMIT",
    "MAX_SEARCH_QUERY_BYTES",
    "MAX_SEARCH_QUERY_TERMS",
    "MIN_LIMIT",
    "MIN_SEARCH_LIMIT",
    "RetrieveManagedNote",
    "RetrievedNote",
    "SearchBackendUnavailableError",
    "SearchContentTooLargeError",
    "SearchDocument",
    "SearchError",
    "SearchErrorCode",
    "SearchHit",
    "SearchIdentityConflictError",
    "SearchIndexFailedError",
    "SearchIndexPort",
    "SearchInvalidRequestError",
    "SearchNotFoundError",
    "SearchQueryFailedError",
    "SearchRequest",
    "SearchVault",
    "build_literal_match_expression",
    "project_search_documents",
    "validate_search_document",
    "validate_search_request",
]
