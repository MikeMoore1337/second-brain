"""Application validation и cross-record diagnostics для read-only vault scan."""

from __future__ import annotations

import posixpath
from collections import defaultdict
from collections.abc import Iterable, Mapping
from pathlib import PurePosixPath
from typing import Any, cast
from uuid import UUID

from second_brain.application.personal_memory import (
    personal_memory_diagnostic_message,
    validate_personal_memory_fields,
)
from second_brain.application.reports import (
    Diagnostic,
    DiagnosticSeverity,
    ScanReport,
    VaultSnapshot,
)
from second_brain.application.research import SourceKind, SourceProvenance
from second_brain.domain.models import (
    AttachmentRecord,
    LinkReference,
    MarkdownDocument,
    NoteRecord,
    NoteType,
    PersonalMemoryMetadata,
    VaultManifest,
    parse_rfc3339,
    parse_uuid7,
)

_MANAGED_FIELDS = frozenset(("id", "type", "created"))


def build_report(snapshot: VaultSnapshot) -> ScanReport:
    """Собрать итоговый report из raw DTO и выполнить application validation."""

    diagnostics = list(snapshot.diagnostics)
    notes = [_validate_document(document, diagnostics) for document in snapshot.documents]
    _report_attachment_diagnostics(snapshot.attachments, snapshot.manifest, diagnostics)
    _report_duplicate_ids(notes, diagnostics)
    _report_link_diagnostics(notes, snapshot.attachments, snapshot.links, diagnostics)
    return ScanReport(
        vault_path=snapshot.vault_path,
        manifest=snapshot.manifest,
        notes=tuple(notes),
        links=snapshot.links,
        attachments=snapshot.attachments,
        diagnostics=tuple(diagnostics),
    )


def _validate_document(
    document: MarkdownDocument,
    diagnostics: list[Diagnostic],
) -> NoteRecord:
    data = dict(document.front_matter)
    _validate_sources(data, document.relative_path, diagnostics)
    marker_present = bool(_MANAGED_FIELDS.intersection(data))
    managed = not document.in_inbox or marker_present
    if not managed:
        diagnostics.append(
            Diagnostic(
                "UNMANAGED_INBOX_NOTE",
                "Inbox Markdown has no id, type, or created field and is temporarily unmanaged",
                DiagnosticSeverity.WARNING,
                document.relative_path,
            )
        )
        return NoteRecord(document.relative_path, data, document.body, False)

    note_id = _parse_note_id(data, document.relative_path, diagnostics)
    note_type = _parse_note_type(data, document.relative_path, diagnostics)
    created = _parse_note_timestamp(
        data, "created", document.relative_path, diagnostics, required=True
    )
    updated = _parse_note_timestamp(
        data, "updated", document.relative_path, diagnostics, required=False
    )
    tags = _parse_tags(data, document.relative_path, diagnostics)
    personal_memory = _parse_personal_memory(
        data,
        document.relative_path,
        diagnostics,
    )
    return NoteRecord(
        relative_path=document.relative_path,
        front_matter=data,
        body=document.body,
        managed=True,
        note_id=note_id,
        note_type=note_type,
        created=created,
        updated=updated,
        tags=tags,
        personal_memory=personal_memory,
    )


def _parse_personal_memory(
    data: Mapping[str, Any],
    path: str,
    diagnostics: list[Diagnostic],
) -> PersonalMemoryMetadata | None:
    """Запустить marker-gated Personal Memory validator."""

    metadata, issues = validate_personal_memory_fields(data)
    for issue in issues:
        diagnostics.append(
            Diagnostic(
                issue.code,
                personal_memory_diagnostic_message(issue.code),
                DiagnosticSeverity.ERROR,
                path,
            )
        )
    return metadata


def _parse_note_id(data: dict[str, Any], path: str, diagnostics: list[Diagnostic]) -> UUID | None:
    if "id" not in data:
        diagnostics.append(
            Diagnostic(
                "NOTE_MISSING_ID", "managed note requires id", DiagnosticSeverity.ERROR, path
            )
        )
        return None
    try:
        return parse_uuid7(data["id"])
    except ValueError as exc:
        diagnostics.append(Diagnostic("NOTE_INVALID_ID", str(exc), DiagnosticSeverity.ERROR, path))
        return None


def _parse_note_type(
    data: dict[str, Any], path: str, diagnostics: list[Diagnostic]
) -> NoteType | None:
    if "type" not in data:
        diagnostics.append(
            Diagnostic(
                "NOTE_MISSING_TYPE", "managed note requires type", DiagnosticSeverity.ERROR, path
            )
        )
        return None
    value = data["type"]
    try:
        return NoteType(value)
    except ValueError:
        allowed = ", ".join(item.value for item in NoteType)
        diagnostics.append(
            Diagnostic(
                "NOTE_INVALID_TYPE",
                f"type must be one of: {allowed}",
                DiagnosticSeverity.ERROR,
                path,
            )
        )
        return None


def _parse_note_timestamp(
    data: dict[str, Any],
    field: str,
    path: str,
    diagnostics: list[Diagnostic],
    required: bool,
) -> Any:
    if field not in data:
        if required:
            diagnostics.append(
                Diagnostic(
                    "NOTE_MISSING_TIMESTAMP",
                    f"managed note requires {field}",
                    DiagnosticSeverity.ERROR,
                    path,
                )
            )
        return None
    try:
        return parse_rfc3339(data[field])
    except ValueError as exc:
        diagnostics.append(
            Diagnostic("NOTE_INVALID_TIMESTAMP", f"{field}: {exc}", DiagnosticSeverity.ERROR, path)
        )
        return None


def _parse_tags(data: dict[str, Any], path: str, diagnostics: list[Diagnostic]) -> tuple[str, ...]:
    if "tags" not in data:
        return ()
    value = data["tags"]
    if not isinstance(value, list) or not all(
        isinstance(item, str) and item.strip() for item in value
    ):
        diagnostics.append(
            Diagnostic(
                "NOTE_INVALID_TAGS",
                "tags must be a list of non-empty strings",
                DiagnosticSeverity.ERROR,
                path,
            )
        )
        return ()
    return tuple(value)


def _validate_sources(
    data: Mapping[str, Any],
    path: str,
    diagnostics: list[Diagnostic],
) -> None:
    """Проверить optional persisted v1 provenance без network или write capability."""

    if "sources" not in data:
        return
    raw_sources = data["sources"]
    if not isinstance(raw_sources, list):
        diagnostics.append(
            Diagnostic(
                "NOTE_INVALID_SOURCES",
                "sources must be a list containing exactly one source record",
                DiagnosticSeverity.ERROR,
                path,
            )
        )
        return
    if len(raw_sources) != 1:
        diagnostics.append(
            Diagnostic(
                "NOTE_INVALID_SOURCE_COUNT",
                "sources must contain exactly one source record in v1",
                DiagnosticSeverity.ERROR,
                path,
            )
        )
        return
    raw_source = raw_sources[0]
    if not isinstance(raw_source, Mapping):
        diagnostics.append(
            Diagnostic(
                "NOTE_INVALID_SOURCE_RECORD",
                "sources[0] must be a mapping",
                DiagnosticSeverity.ERROR,
                path,
            )
        )
        return
    source = cast(Mapping[object, object], raw_source)
    for field, code in (
        ("uri", "NOTE_SOURCE_MISSING_URI"),
        ("kind", "NOTE_SOURCE_MISSING_KIND"),
        ("retrieved_at", "NOTE_SOURCE_MISSING_RETRIEVED_AT"),
    ):
        if field not in source:
            diagnostics.append(
                Diagnostic(
                    code,
                    f"sources[0] requires {field}",
                    DiagnosticSeverity.ERROR,
                    path,
                )
            )
    if any(field not in source for field in ("uri", "kind", "retrieved_at")):
        return
    _validate_source_record(source, path, diagnostics)


def _validate_source_record(
    source: Mapping[object, object],
    path: str,
    diagnostics: list[Diagnostic],
) -> None:
    """Разобрать persisted mapping через общую SourceProvenance policy."""

    try:
        source_kind = SourceKind(cast(str, source["kind"]))
    except TypeError, ValueError:
        diagnostics.append(
            Diagnostic(
                "NOTE_SOURCE_INVALID_KIND",
                "sources[0].kind must be one of: web, rss, youtube, github",
                DiagnosticSeverity.ERROR,
                path,
            )
        )
        source_kind = None

    retrieved_at = None
    try:
        retrieved_at = parse_rfc3339(source["retrieved_at"])
    except (TypeError, ValueError, OverflowError) as exc:
        diagnostics.append(
            Diagnostic(
                "NOTE_SOURCE_INVALID_RETRIEVED_AT",
                f"sources[0].retrieved_at: {exc}",
                DiagnosticSeverity.ERROR,
                path,
            )
        )

    published_at = None
    published_at_valid = True
    if "published_at" in source and source["published_at"] is not None:
        try:
            published_at = parse_rfc3339(source["published_at"])
        except (TypeError, ValueError, OverflowError) as exc:
            diagnostics.append(
                Diagnostic(
                    "NOTE_SOURCE_INVALID_PUBLISHED_AT",
                    f"sources[0].published_at: {exc}",
                    DiagnosticSeverity.ERROR,
                    path,
                )
            )
            published_at_valid = False

    if source_kind is None or retrieved_at is None or not published_at_valid:
        return

    try:
        SourceProvenance(
            uri=cast(str, source["uri"]),
            source_kind=source_kind,
            retrieved_at=retrieved_at,
            published_at=published_at,
            title=cast(str | None, source.get("title")),
            author=cast(str | None, source.get("author")),
            upstream_id=cast(str | None, source.get("upstream_id")),
        )
    except ValueError as exc:
        message = str(exc)
        if message.startswith("uri "):
            code = "NOTE_SOURCE_INVALID_URI"
        elif any(message.startswith(f"{field} ") for field in ("title", "author", "upstream_id")):
            code = "NOTE_SOURCE_INVALID_METADATA"
        else:
            code = "NOTE_INVALID_SOURCE"
        diagnostics.append(Diagnostic(code, message, DiagnosticSeverity.ERROR, path))


def _report_attachment_diagnostics(
    attachments: Iterable[AttachmentRecord],
    manifest: VaultManifest | None,
    diagnostics: list[Diagnostic],
) -> None:
    if manifest is None:
        return
    warning_size = manifest.attachments.warning_size_bytes
    max_size = manifest.attachments.max_size_bytes
    for attachment in attachments:
        if attachment.size_bytes > max_size:
            diagnostics.append(
                Diagnostic(
                    "ATTACHMENT_TOO_LARGE",
                    f"attachment is larger than the {max_size} byte limit",
                    DiagnosticSeverity.ERROR,
                    attachment.relative_path,
                )
            )
        elif attachment.size_bytes >= warning_size:
            diagnostics.append(
                Diagnostic(
                    "ATTACHMENT_LARGE",
                    f"attachment is at or above the {warning_size} byte warning threshold",
                    DiagnosticSeverity.WARNING,
                    attachment.relative_path,
                )
            )


def _report_duplicate_ids(notes: Iterable[NoteRecord], diagnostics: list[Diagnostic]) -> None:
    paths_by_id: defaultdict[UUID, list[str]] = defaultdict(list)
    for note in notes:
        if note.note_id is not None:
            paths_by_id[note.note_id].append(note.relative_path)
    for note_id, paths in sorted(paths_by_id.items(), key=lambda item: str(item[0])):
        if len(paths) > 1:
            diagnostics.append(
                Diagnostic(
                    "DUPLICATE_NOTE_ID",
                    f"note id {note_id} is used by: {', '.join(sorted(paths))}",
                    DiagnosticSeverity.ERROR,
                )
            )


def _report_link_diagnostics(
    notes: Iterable[NoteRecord],
    attachments: Iterable[AttachmentRecord],
    links: Iterable[LinkReference],
    diagnostics: list[Diagnostic],
) -> None:
    note_paths: defaultdict[str, list[str]] = defaultdict(list)
    attachment_paths: defaultdict[str, list[str]] = defaultdict(list)
    for note in notes:
        _add_note_keys(note.relative_path, note_paths)
    for attachment in attachments:
        _add_attachment_keys(attachment.relative_path, attachment_paths)
    for link in links:
        if not link.target and link.fragment is not None:
            continue
        if not link.target:
            diagnostics.append(
                Diagnostic(
                    "EMPTY_WIKILINK_TARGET",
                    f"wikilink has no note target: {link.raw}",
                    DiagnosticSeverity.ERROR,
                    link.source_path,
                )
            )
            continue
        if "://" in link.target:
            continue
        matches = _resolve_link(link, note_paths, attachment_paths)
        if len(matches) == 0:
            diagnostics.append(
                Diagnostic(
                    "BROKEN_WIKILINK",
                    f"wikilink target does not resolve: {link.raw}",
                    DiagnosticSeverity.ERROR,
                    link.source_path,
                )
            )
        elif len(matches) > 1:
            diagnostics.append(
                Diagnostic(
                    "AMBIGUOUS_WIKILINK",
                    f"wikilink target resolves to multiple files: {link.raw}",
                    DiagnosticSeverity.ERROR,
                    link.source_path,
                )
            )


def _add_note_keys(path: str, index: defaultdict[str, list[str]]) -> None:
    normalized = _key(path)
    index[normalized].append(path)
    if normalized.endswith(".md"):
        index[normalized[:-3]].append(path)
    basename = PurePosixPath(normalized).name
    index[basename].append(path)
    if basename.endswith(".md"):
        index[basename[:-3]].append(path)


def _add_attachment_keys(path: str, index: defaultdict[str, list[str]]) -> None:
    normalized = _key(path)
    index[normalized].append(path)
    index[PurePosixPath(normalized).name].append(path)


def _resolve_link(
    link: LinkReference,
    note_paths: defaultdict[str, list[str]],
    attachment_paths: defaultdict[str, list[str]],
) -> list[str]:
    target = link.target.replace("\\", "/").strip().lstrip("/")
    if not target:
        return []
    note_candidates = _link_candidates(target, link.source_path)
    for candidate in note_candidates:
        matches = _unique(note_paths.get(_key(candidate), []))
        if matches:
            return matches
    if link.is_embed or PurePosixPath(target).suffix:
        for candidate in note_candidates:
            matches = _unique(attachment_paths.get(_key(candidate), []))
            if matches:
                return matches
    fallback = _unique(note_paths.get(_key(target), []))
    if fallback:
        return fallback
    if link.is_embed:
        return _unique(attachment_paths.get(_key(target), []))
    return []


def _link_candidates(target: str, source_path: str) -> tuple[str, ...]:
    variants = (target,) if target.casefold().endswith(".md") else (target, f"{target}.md")
    values: list[str] = []
    source_parent = PurePosixPath(source_path).parent
    for variant in variants:
        values.append(variant)
        relative = posixpath.normpath(str(source_parent / variant))
        if relative != ".." and not relative.startswith("../"):
            values.append(relative)
    return tuple(dict.fromkeys(values))


def _unique(values: Iterable[str]) -> list[str]:
    return list(dict.fromkeys(values))


def _key(value: str) -> str:
    normalized = posixpath.normpath(value.replace("\\", "/"))
    while normalized.startswith("./"):
        normalized = normalized[2:]
    normalized = normalized.lstrip("/")
    return normalized.casefold()
