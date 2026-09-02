"""Read-only filesystem scanner для проверки контракта vault."""

from __future__ import annotations

import posixpath
from collections import defaultdict
from collections.abc import Iterable
from pathlib import Path, PurePosixPath
from typing import Any
from uuid import UUID

from second_brain.adapters.vault.frontmatter import parse_front_matter
from second_brain.adapters.vault.manifest import load_manifest
from second_brain.adapters.vault.markdown_links import extract_links
from second_brain.domain.models import (
    AttachmentRecord,
    Diagnostic,
    DiagnosticSeverity,
    LinkReference,
    NoteRecord,
    NoteType,
    ScanReport,
    parse_rfc3339,
    parse_uuid7,
)

_MANAGED_FIELDS = frozenset(("id", "type", "created"))
_CONTENT_PATH_NAMES = ("inbox", "projects", "areas", "resources", "zettelkasten", "archive")


class FileSystemVaultReader:
    """Проверить настроенный vault без перехода по links и записи файлов."""

    def __init__(self, root: Path) -> None:
        self.root = root

    def scan(self) -> ScanReport:
        """Просканировать manifest, managed Markdown roots, links и attachments."""

        diagnostics: list[Diagnostic] = []
        try:
            vault_root = self.root.resolve(strict=True)
        except OSError as exc:
            return ScanReport(
                str(self.root),
                None,
                diagnostics=(
                    Diagnostic(
                        "VAULT_ROOT_ERROR",
                        f"cannot resolve vault root: {exc}",
                        DiagnosticSeverity.ERROR,
                    ),
                ),
            )
        if not vault_root.is_dir():
            return ScanReport(
                str(vault_root),
                None,
                diagnostics=(
                    Diagnostic(
                        "VAULT_ROOT_NOT_DIRECTORY",
                        "configured vault path is not a directory",
                        DiagnosticSeverity.ERROR,
                    ),
                ),
            )

        manifest_path = vault_root / "second-brain.yaml"
        if _is_link_like(manifest_path):
            diagnostics.append(
                Diagnostic(
                    "MANIFEST_LINKED_FILE",
                    "root manifest must not be a symlink or junction",
                    DiagnosticSeverity.ERROR,
                    "second-brain.yaml",
                )
            )
            return ScanReport(str(vault_root), None, diagnostics=tuple(diagnostics))
        manifest_result = load_manifest(manifest_path)
        diagnostics.extend(manifest_result.diagnostics)
        manifest = manifest_result.manifest
        if manifest is None:
            return ScanReport(str(vault_root), None, diagnostics=tuple(diagnostics))

        resolved_roots: dict[str, Path] = {}
        for name in (*_CONTENT_PATH_NAMES, "templates", "attachments"):
            relative = getattr(manifest.paths, name)
            candidate = vault_root / Path(relative.as_posix())
            resolved = _resolve_directory(
                candidate,
                vault_root,
                name,
                diagnostics,
            )
            if resolved is not None:
                resolved_roots[name] = resolved

        _report_overlapping_roots(resolved_roots, diagnostics)

        notes: list[NoteRecord] = []
        links: list[LinkReference] = []
        attachments: list[AttachmentRecord] = []
        inbox_root = resolved_roots.get("inbox")

        for name in _CONTENT_PATH_NAMES:
            root = resolved_roots.get(name)
            if root is None:
                continue
            _scan_content_tree(
                root,
                vault_root,
                inbox_root,
                notes,
                links,
                diagnostics,
            )

        attachment_root = resolved_roots.get("attachments")
        if attachment_root is not None:
            _scan_attachment_tree(
                attachment_root,
                vault_root,
                manifest.attachments.warning_size_bytes,
                manifest.attachments.max_size_bytes,
                attachments,
                diagnostics,
            )

        _report_duplicate_ids(notes, diagnostics)
        _report_link_diagnostics(notes, attachments, links, diagnostics)
        return ScanReport(
            vault_path=str(vault_root),
            manifest=manifest,
            notes=tuple(notes),
            links=tuple(links),
            attachments=tuple(attachments),
            diagnostics=tuple(diagnostics),
        )


def _scan_content_tree(
    root: Path,
    vault_root: Path,
    inbox_root: Path | None,
    notes: list[NoteRecord],
    links: list[LinkReference],
    diagnostics: list[Diagnostic],
) -> None:
    stack = [root]
    visited: set[tuple[int, int]] = set()
    while stack:
        directory = stack.pop()
        identity = _directory_identity(directory)
        if identity in visited:
            continue
        visited.add(identity)
        try:
            children = sorted(
                directory.iterdir(), key=lambda item: item.name.casefold(), reverse=True
            )
        except OSError as exc:
            diagnostics.append(
                Diagnostic(
                    "VAULT_DIRECTORY_READ_ERROR",
                    f"cannot read directory: {exc}",
                    DiagnosticSeverity.ERROR,
                    _relative(vault_root, directory),
                )
            )
            continue
        for child in children:
            relative_path = _relative(vault_root, child)
            if _is_link_like(child):
                diagnostics.append(
                    Diagnostic(
                        "VAULT_LINKED_ENTRY",
                        "symlink/junction is not followed by the scanner",
                        DiagnosticSeverity.WARNING,
                        relative_path,
                    )
                )
                continue
            if child.is_dir():
                resolved = _safe_resolve(child, root, vault_root, relative_path, diagnostics)
                if resolved is not None:
                    stack.append(resolved)
                continue
            if not child.is_file():
                diagnostics.append(
                    Diagnostic(
                        "VAULT_UNSUPPORTED_ENTRY",
                        "entry is neither a regular file nor a directory",
                        DiagnosticSeverity.WARNING,
                        relative_path,
                    )
                )
                continue
            resolved = _safe_resolve(child, root, vault_root, relative_path, diagnostics)
            if resolved is None or child.suffix.casefold() != ".md":
                continue
            try:
                text = resolved.read_text(encoding="utf-8")
            except (OSError, UnicodeError) as exc:
                diagnostics.append(
                    Diagnostic(
                        "NOTE_READ_ERROR",
                        f"cannot read Markdown as UTF-8: {exc}",
                        DiagnosticSeverity.ERROR,
                        relative_path,
                    )
                )
                continue
            in_inbox = inbox_root is not None and _is_relative_to(resolved, inbox_root)
            note = _parse_note(relative_path, text, in_inbox, diagnostics)
            notes.append(note)
            links.extend(extract_links(note.body, note.relative_path))


def _scan_attachment_tree(
    root: Path,
    vault_root: Path,
    warning_size: int,
    max_size: int,
    attachments: list[AttachmentRecord],
    diagnostics: list[Diagnostic],
) -> None:
    stack = [root]
    visited: set[tuple[int, int]] = set()
    while stack:
        directory = stack.pop()
        identity = _directory_identity(directory)
        if identity in visited:
            continue
        visited.add(identity)
        try:
            children = sorted(
                directory.iterdir(), key=lambda item: item.name.casefold(), reverse=True
            )
        except OSError as exc:
            diagnostics.append(
                Diagnostic(
                    "ATTACHMENT_DIRECTORY_READ_ERROR",
                    f"cannot read attachments directory: {exc}",
                    DiagnosticSeverity.ERROR,
                    _relative(vault_root, directory),
                )
            )
            continue
        for child in children:
            relative_path = _relative(vault_root, child)
            if _is_link_like(child):
                diagnostics.append(
                    Diagnostic(
                        "VAULT_LINKED_ENTRY",
                        "symlink/junction is not followed by the scanner",
                        DiagnosticSeverity.WARNING,
                        relative_path,
                    )
                )
                continue
            if child.is_dir():
                resolved = _safe_resolve(child, root, vault_root, relative_path, diagnostics)
                if resolved is not None:
                    stack.append(resolved)
                continue
            if not child.is_file():
                continue
            resolved = _safe_resolve(child, root, vault_root, relative_path, diagnostics)
            if resolved is None:
                continue
            try:
                size_bytes = resolved.stat().st_size
            except OSError as exc:
                diagnostics.append(
                    Diagnostic(
                        "ATTACHMENT_STAT_ERROR",
                        f"cannot stat attachment: {exc}",
                        DiagnosticSeverity.ERROR,
                        relative_path,
                    )
                )
                continue
            attachments.append(AttachmentRecord(relative_path, size_bytes))
            if size_bytes > max_size:
                severity = DiagnosticSeverity.ERROR
                code = "ATTACHMENT_TOO_LARGE"
                message = f"attachment is larger than the {max_size} byte limit"
            elif size_bytes >= warning_size:
                severity = DiagnosticSeverity.WARNING
                code = "ATTACHMENT_LARGE"
                message = f"attachment is at or above the {warning_size} byte warning threshold"
            else:
                continue
            diagnostics.append(Diagnostic(code, message, severity, relative_path))


def _parse_note(
    relative_path: str,
    text: str,
    in_inbox: bool,
    diagnostics: list[Diagnostic],
) -> NoteRecord:
    parsed = parse_front_matter(text)
    if parsed.error is not None:
        diagnostics.append(
            Diagnostic(
                "NOTE_FRONT_MATTER_ERROR",
                parsed.error,
                DiagnosticSeverity.ERROR,
                relative_path,
                parsed.error_line,
            )
        )
    data = dict(parsed.data)
    marker_present = bool(_MANAGED_FIELDS.intersection(data))
    managed = not in_inbox or marker_present
    if not managed:
        diagnostics.append(
            Diagnostic(
                "UNMANAGED_INBOX_NOTE",
                "Inbox Markdown has no id, type, or created field and is temporarily unmanaged",
                DiagnosticSeverity.WARNING,
                relative_path,
            )
        )
        return NoteRecord(relative_path, data, parsed.body, False)

    note_id = _parse_note_id(data, relative_path, diagnostics)
    note_type = _parse_note_type(data, relative_path, diagnostics)
    created = _parse_note_timestamp(data, "created", relative_path, diagnostics, required=True)
    updated = _parse_note_timestamp(data, "updated", relative_path, diagnostics, required=False)
    tags = _parse_tags(data, relative_path, diagnostics)
    return NoteRecord(
        relative_path=relative_path,
        front_matter=data,
        body=parsed.body,
        managed=True,
        note_id=note_id,
        note_type=note_type,
        created=created,
        updated=updated,
        tags=tags,
    )


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


def _resolve_directory(
    candidate: Path,
    vault_root: Path,
    name: str,
    diagnostics: list[Diagnostic],
) -> Path | None:
    relative = _relative(vault_root, candidate)
    if _is_link_like(candidate):
        diagnostics.append(
            Diagnostic(
                "VAULT_LINKED_DIRECTORY",
                f"{name} root is a symlink/junction and is not followed",
                DiagnosticSeverity.ERROR,
                relative,
            )
        )
        return None
    try:
        resolved = candidate.resolve(strict=True)
    except OSError as exc:
        diagnostics.append(
            Diagnostic(
                "VAULT_ROOT_MISSING",
                f"{name} root is missing or unreadable: {exc}",
                DiagnosticSeverity.ERROR,
                relative,
            )
        )
        return None
    if not resolved.is_dir():
        diagnostics.append(
            Diagnostic(
                "VAULT_ROOT_NOT_DIRECTORY",
                f"{name} root is not a directory",
                DiagnosticSeverity.ERROR,
                relative,
            )
        )
        return None
    if not _is_relative_to(resolved, vault_root):
        diagnostics.append(
            Diagnostic(
                "VAULT_PATH_ESCAPE",
                f"{name} root resolves outside the vault",
                DiagnosticSeverity.ERROR,
                relative,
            )
        )
        return None
    return resolved


def _safe_resolve(
    candidate: Path,
    content_root: Path,
    vault_root: Path,
    relative: str,
    diagnostics: list[Diagnostic],
) -> Path | None:
    try:
        resolved = candidate.resolve(strict=True)
    except OSError as exc:
        diagnostics.append(
            Diagnostic(
                "VAULT_ENTRY_RESOLVE_ERROR",
                f"cannot resolve entry: {exc}",
                DiagnosticSeverity.ERROR,
                relative,
            )
        )
        return None
    if not _is_relative_to(resolved, content_root) or not _is_relative_to(resolved, vault_root):
        diagnostics.append(
            Diagnostic(
                "VAULT_PATH_ESCAPE",
                "resolved entry is outside its declared vault root",
                DiagnosticSeverity.ERROR,
                relative,
            )
        )
        return None
    return resolved


def _report_overlapping_roots(roots: dict[str, Path], diagnostics: list[Diagnostic]) -> None:
    items = list(roots.items())
    for index, (name, root) in enumerate(items):
        for other_name, other_root in items[index + 1 :]:
            if _is_relative_to(root, other_root) or _is_relative_to(other_root, root):
                diagnostics.append(
                    Diagnostic(
                        "VAULT_OVERLAPPING_ROOTS",
                        f"{name} and {other_name} roots overlap",
                        DiagnosticSeverity.ERROR,
                    )
                )


def _directory_identity(path: Path) -> tuple[int, int]:
    stat = path.stat()
    return stat.st_dev, stat.st_ino


def _is_link_like(path: Path) -> bool:
    try:
        if path.is_symlink():
            return True
        is_junction = getattr(path, "is_junction", None)
        return bool(is_junction is not None and is_junction())
    except OSError:
        return True


def _relative(root: Path, path: Path) -> str:
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return path.as_posix()


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def _key(value: str) -> str:
    normalized = posixpath.normpath(value.replace("\\", "/"))
    while normalized.startswith("./"):
        normalized = normalized[2:]
    normalized = normalized.lstrip("/")
    return normalized.casefold()
