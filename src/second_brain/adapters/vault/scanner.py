"""Read-only filesystem scanner для чтения контракта vault."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from second_brain.adapters.vault.frontmatter import parse_front_matter
from second_brain.adapters.vault.manifest import load_manifest
from second_brain.adapters.vault.markdown_links import extract_links_from_body
from second_brain.application.reports import (
    Diagnostic,
    DiagnosticSeverity,
    VaultRootRole,
    VaultSnapshot,
)
from second_brain.application.retrospective_calibration_scan import (
    RetrospectiveCalibrationScanLimitsV1,
    RetrospectiveCalibrationScanV1,
)
from second_brain.domain.models import AttachmentRecord, LinkReference, MarkdownDocument

_CONTENT_ROOT_ROLES: tuple[VaultRootRole, ...] = (
    VaultRootRole.INBOX,
    VaultRootRole.PROJECTS,
    VaultRootRole.AREAS,
    VaultRootRole.RESOURCES,
    VaultRootRole.ZETTELKASTEN,
    VaultRootRole.ARCHIVE,
)
_DECLARED_ROOT_ROLES: tuple[VaultRootRole, ...] = (
    *_CONTENT_ROOT_ROLES,
    VaultRootRole.TEMPLATES,
    VaultRootRole.ATTACHMENTS,
)


@dataclass(frozen=True, slots=True)
class _DeclaredRoot:
    """Manifest declaration plus its resolved directory when resolution succeeded."""

    role: VaultRootRole
    candidate: Path
    resolved: Path | None


@dataclass(slots=True)
class _ScanBudget:
    """Mutable counters kept inside one bounded scanner invocation."""

    limits: RetrospectiveCalibrationScanLimitsV1
    entries_inspected: int = 0
    documents_materialized: int = 0
    raw_utf8_bytes: int = 0
    exceeded: bool = False

    def inspect_entry(self) -> bool:
        """Count an entry before any materialization or recursive descent."""

        self.entries_inspected += 1
        if self.entries_inspected > self.limits.max_scan_entries:
            self.exceeded = True
            return False
        return True

    def reserve_document(self, raw_bytes: int) -> bool:
        """Reserve one Markdown document before reading its body."""

        self.documents_materialized += 1
        self.raw_utf8_bytes += raw_bytes
        if (
            self.documents_materialized > self.limits.max_scan_documents
            or self.raw_utf8_bytes > self.limits.max_scan_bytes
        ):
            self.exceeded = True
            return False
        return True


class FileSystemVaultReader:
    """Прочитать настроенный vault без перехода по links и записи файлов."""

    def __init__(self, root: Path) -> None:
        self.root = root

    def scan(self) -> VaultSnapshot:
        """Прочитать manifest, Markdown-документы, links и attachments."""

        return self._scan_snapshot()

    def scan_bounded(
        self,
        limits: RetrospectiveCalibrationScanLimitsV1,
    ) -> RetrospectiveCalibrationScanV1:
        """Read through a pre-materialization budget for Calibration v1 only."""

        budget = _ScanBudget(limits)
        snapshot = self._scan_snapshot(budget)
        return RetrospectiveCalibrationScanV1(
            snapshot=snapshot,
            entries_inspected=budget.entries_inspected,
            documents_materialized=budget.documents_materialized,
            raw_utf8_bytes=budget.raw_utf8_bytes,
            limit_exceeded=budget.exceeded,
        )

    def _scan_snapshot(self, budget: _ScanBudget | None = None) -> VaultSnapshot:
        """Run the shared scanner, optionally carrying a bounded budget."""

        diagnostics: list[Diagnostic] = []
        try:
            vault_root = self.root.resolve(strict=True)
        except OSError:
            return VaultSnapshot(
                str(self.root),
                None,
                diagnostics=(
                    Diagnostic(
                        "VAULT_ROOT_ERROR",
                        "cannot resolve vault root",
                        DiagnosticSeverity.ERROR,
                        root_role=VaultRootRole.GLOBAL,
                    ),
                ),
            )
        if not vault_root.is_dir():
            return VaultSnapshot(
                str(vault_root),
                None,
                diagnostics=(
                    Diagnostic(
                        "VAULT_ROOT_NOT_DIRECTORY",
                        "configured vault path is not a directory",
                        DiagnosticSeverity.ERROR,
                        root_role=VaultRootRole.GLOBAL,
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
                    root_role=VaultRootRole.GLOBAL,
                )
            )
            return VaultSnapshot(str(vault_root), None, diagnostics=tuple(diagnostics))
        manifest_result = load_manifest(manifest_path)
        diagnostics.extend(manifest_result.diagnostics)
        manifest = manifest_result.manifest
        if manifest is None:
            return VaultSnapshot(str(vault_root), None, diagnostics=tuple(diagnostics))

        declared_roots: list[_DeclaredRoot] = []
        resolved_roots: dict[VaultRootRole, Path] = {}
        for role in _DECLARED_ROOT_ROLES:
            relative = getattr(manifest.paths, role.value)
            candidate = vault_root / Path(relative.as_posix())
            resolved = _resolve_directory(candidate, vault_root, role, diagnostics)
            declared_roots.append(_DeclaredRoot(role, candidate, resolved))
            if resolved is not None:
                resolved_roots[role] = resolved

        _report_overlapping_roots(declared_roots, diagnostics)

        documents: list[MarkdownDocument] = []
        links: list[LinkReference] = []
        attachments: list[AttachmentRecord] = []
        inbox_root = resolved_roots.get(VaultRootRole.INBOX)

        for role in _CONTENT_ROOT_ROLES:
            root = resolved_roots.get(role)
            if root is None:
                continue
            _scan_content_tree(
                root,
                vault_root,
                inbox_root,
                role,
                tuple(declared_roots),
                documents,
                links,
                diagnostics,
                budget,
            )
            if budget is not None and budget.exceeded:
                break

        attachment_root = resolved_roots.get(VaultRootRole.ATTACHMENTS)
        if attachment_root is not None:
            _scan_attachment_tree(
                attachment_root,
                vault_root,
                tuple(declared_roots),
                attachments,
                diagnostics,
                budget,
            )

        return VaultSnapshot(
            vault_path=str(vault_root),
            manifest=manifest,
            documents=tuple(documents),
            links=tuple(links),
            attachments=tuple(attachments),
            diagnostics=tuple(diagnostics),
        )


class FileSystemRetrospectiveCalibrationScanner:
    """Expose only the bounded scanner seam required by Calibration v1."""

    def __init__(self, root: Path) -> None:
        self._reader = FileSystemVaultReader(root)

    def scan(
        self,
        limits: RetrospectiveCalibrationScanLimitsV1,
    ) -> RetrospectiveCalibrationScanV1:
        """Return one bounded scan without calling the ordinary scan path."""

        return self._reader.scan_bounded(limits)


def _scan_content_tree(
    root: Path,
    vault_root: Path,
    inbox_root: Path | None,
    root_role: VaultRootRole,
    declared_roots: tuple[_DeclaredRoot, ...],
    documents: list[MarkdownDocument],
    links: list[LinkReference],
    diagnostics: list[Diagnostic],
    budget: _ScanBudget | None = None,
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
            if budget is None:
                children = sorted(
                    directory.iterdir(), key=lambda item: item.name.casefold(), reverse=True
                )
            else:
                children = []
                for child in directory.iterdir():
                    if not budget.inspect_entry():
                        return
                    children.append(child)
                children.sort(key=lambda item: item.name.casefold(), reverse=True)
        except OSError:
            diagnostics.append(
                Diagnostic(
                    "VAULT_DIRECTORY_READ_ERROR",
                    "cannot read declared vault directory",
                    DiagnosticSeverity.ERROR,
                    _relative(vault_root, directory),
                    root_role=root_role,
                )
            )
            continue
        for child in children:
            # A nested declared root is scanned only by its own role.  This
            # check deliberately runs before link/resolve handling so an
            # unreadable or linked support root cannot be reclassified as a
            # content-root failure.
            if _is_nested_declared_root(child, root, root_role, declared_roots):
                continue
            relative_path = _relative(vault_root, child)
            if _is_link_like(child):
                diagnostics.append(
                    Diagnostic(
                        "VAULT_LINKED_ENTRY",
                        "symlink/junction is not followed by the scanner",
                        DiagnosticSeverity.WARNING,
                        relative_path,
                        root_role=root_role,
                    )
                )
                continue
            if child.is_dir():
                resolved = _safe_resolve(
                    child,
                    root,
                    vault_root,
                    relative_path,
                    root_role,
                    diagnostics,
                )
                if resolved is not None and not _is_nested_declared_root(
                    resolved, root, root_role, declared_roots
                ):
                    stack.append(resolved)
                continue
            if not child.is_file():
                diagnostics.append(
                    Diagnostic(
                        "VAULT_UNSUPPORTED_ENTRY",
                        "entry is neither a regular file nor a directory",
                        DiagnosticSeverity.WARNING,
                        relative_path,
                        root_role=root_role,
                    )
                )
                continue
            resolved = _safe_resolve(
                child,
                root,
                vault_root,
                relative_path,
                root_role,
                diagnostics,
            )
            if resolved is None or child.suffix.casefold() != ".md":
                continue
            try:
                file_size = resolved.stat().st_size
            except OSError:
                diagnostics.append(
                    Diagnostic(
                        "NOTE_READ_ERROR",
                        "cannot read Markdown as UTF-8",
                        DiagnosticSeverity.ERROR,
                        relative_path,
                        root_role=root_role,
                    )
                )
                continue
            if budget is not None and not budget.reserve_document(file_size):
                return
            try:
                raw = resolved.read_bytes()
            except OSError, UnicodeError:
                diagnostics.append(
                    Diagnostic(
                        "NOTE_READ_ERROR",
                        "cannot read Markdown as UTF-8",
                        DiagnosticSeverity.ERROR,
                        relative_path,
                        root_role=root_role,
                    )
                )
                continue
            if budget is not None and len(raw) > file_size:
                budget.raw_utf8_bytes += len(raw) - file_size
                if budget.raw_utf8_bytes > budget.limits.max_scan_bytes:
                    budget.exceeded = True
                    return
            try:
                text = raw.decode("utf-8")
            except UnicodeError:
                diagnostics.append(
                    Diagnostic(
                        "NOTE_READ_ERROR",
                        "cannot read Markdown as UTF-8",
                        DiagnosticSeverity.ERROR,
                        relative_path,
                        root_role=root_role,
                    )
                )
                continue
            text = text.replace("\r\n", "\n").replace("\r", "\n")
            in_inbox = inbox_root is not None and _path_is_within(resolved, inbox_root)
            document = _read_document(relative_path, text, in_inbox, root_role, diagnostics)
            documents.append(document)
            links.extend(extract_links_from_body(document.body, document.relative_path))


def _read_document(
    relative_path: str,
    text: str,
    in_inbox: bool,
    root_role: VaultRootRole,
    diagnostics: list[Diagnostic],
) -> MarkdownDocument:
    """Разобрать front matter один раз и передать body дальше без повторного разбора."""

    parsed = parse_front_matter(text)
    if parsed.error is not None:
        diagnostics.append(
            Diagnostic(
                "NOTE_FRONT_MATTER_ERROR",
                parsed.error,
                DiagnosticSeverity.ERROR,
                relative_path,
                parsed.error_line,
                root_role=root_role,
            )
        )
    return MarkdownDocument(
        relative_path=relative_path,
        front_matter=dict(parsed.data),
        body=parsed.body,
        in_inbox=in_inbox,
    )


def _scan_attachment_tree(
    root: Path,
    vault_root: Path,
    declared_roots: tuple[_DeclaredRoot, ...],
    attachments: list[AttachmentRecord],
    diagnostics: list[Diagnostic],
    budget: _ScanBudget | None = None,
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
            if budget is None:
                children = sorted(
                    directory.iterdir(), key=lambda item: item.name.casefold(), reverse=True
                )
            else:
                children = []
                for child in directory.iterdir():
                    if not budget.inspect_entry():
                        return
                    children.append(child)
                children.sort(key=lambda item: item.name.casefold(), reverse=True)
        except OSError:
            diagnostics.append(
                Diagnostic(
                    "ATTACHMENT_DIRECTORY_READ_ERROR",
                    "cannot read attachments directory",
                    DiagnosticSeverity.ERROR,
                    _relative(vault_root, directory),
                    root_role=VaultRootRole.ATTACHMENTS,
                )
            )
            continue
        for child in children:
            if _is_nested_declared_root(child, root, VaultRootRole.ATTACHMENTS, declared_roots):
                continue
            relative_path = _relative(vault_root, child)
            if _is_link_like(child):
                diagnostics.append(
                    Diagnostic(
                        "VAULT_LINKED_ENTRY",
                        "symlink/junction is not followed by the scanner",
                        DiagnosticSeverity.WARNING,
                        relative_path,
                        root_role=VaultRootRole.ATTACHMENTS,
                    )
                )
                continue
            if child.is_dir():
                resolved = _safe_resolve(
                    child,
                    root,
                    vault_root,
                    relative_path,
                    VaultRootRole.ATTACHMENTS,
                    diagnostics,
                )
                if resolved is not None and not _is_nested_declared_root(
                    resolved, root, VaultRootRole.ATTACHMENTS, declared_roots
                ):
                    stack.append(resolved)
                continue
            if not child.is_file():
                continue
            resolved = _safe_resolve(
                child,
                root,
                vault_root,
                relative_path,
                VaultRootRole.ATTACHMENTS,
                diagnostics,
            )
            if resolved is None:
                continue
            try:
                size_bytes = resolved.stat().st_size
            except OSError:
                diagnostics.append(
                    Diagnostic(
                        "ATTACHMENT_STAT_ERROR",
                        "cannot stat attachment",
                        DiagnosticSeverity.ERROR,
                        relative_path,
                        root_role=VaultRootRole.ATTACHMENTS,
                    )
                )
                continue
            attachments.append(AttachmentRecord(relative_path, size_bytes))


def _resolve_directory(
    candidate: Path,
    vault_root: Path,
    role: VaultRootRole,
    diagnostics: list[Diagnostic],
) -> Path | None:
    relative = _relative(vault_root, candidate)
    if _is_link_like(candidate):
        diagnostics.append(
            Diagnostic(
                "VAULT_LINKED_DIRECTORY",
                f"{role.value} root is a symlink/junction and is not followed",
                DiagnosticSeverity.ERROR,
                relative,
                root_role=role,
            )
        )
        return None
    try:
        resolved = candidate.resolve(strict=True)
    except OSError:
        diagnostics.append(
            Diagnostic(
                "VAULT_ROOT_MISSING",
                f"{role.value} root is missing or unreadable",
                DiagnosticSeverity.ERROR,
                relative,
                root_role=role,
            )
        )
        return None
    if not resolved.is_dir():
        diagnostics.append(
            Diagnostic(
                "VAULT_ROOT_NOT_DIRECTORY",
                f"{role.value} root is not a directory",
                DiagnosticSeverity.ERROR,
                relative,
                root_role=role,
            )
        )
        return None
    if not _path_is_within(resolved, vault_root):
        diagnostics.append(
            Diagnostic(
                "VAULT_PATH_ESCAPE",
                f"{role.value} root resolves outside the vault",
                DiagnosticSeverity.ERROR,
                relative,
                root_role=role,
            )
        )
        return None
    return resolved


def _safe_resolve(
    candidate: Path,
    content_root: Path,
    vault_root: Path,
    relative: str,
    root_role: VaultRootRole,
    diagnostics: list[Diagnostic],
) -> Path | None:
    try:
        resolved = candidate.resolve(strict=True)
    except OSError:
        diagnostics.append(
            Diagnostic(
                "VAULT_ENTRY_RESOLVE_ERROR",
                "cannot resolve vault entry",
                DiagnosticSeverity.ERROR,
                relative,
                root_role=root_role,
            )
        )
        return None
    if not _path_is_within(resolved, content_root) or not _path_is_within(resolved, vault_root):
        diagnostics.append(
            Diagnostic(
                "VAULT_PATH_ESCAPE",
                "resolved entry is outside its declared vault root",
                DiagnosticSeverity.ERROR,
                relative,
                root_role=root_role,
            )
        )
        return None
    return resolved


def _report_overlapping_roots(
    declared_roots: tuple[_DeclaredRoot, ...] | list[_DeclaredRoot],
    diagnostics: list[Diagnostic],
) -> None:
    resolved_roots = [root for root in declared_roots if root.resolved is not None]
    for index, first in enumerate(resolved_roots):
        assert first.resolved is not None
        for second in resolved_roots[index + 1 :]:
            assert second.resolved is not None
            if not _paths_overlap(first.resolved, second.resolved):
                continue
            roles = tuple(
                sorted(
                    (first.role, second.role),
                    key=lambda role: _DECLARED_ROOT_ROLES.index(role),
                )
            )
            diagnostics.append(
                Diagnostic(
                    "VAULT_OVERLAPPING_ROOTS",
                    "declared vault roots overlap: " + ", ".join(role.value for role in roles),
                    DiagnosticSeverity.ERROR,
                    root_role=VaultRootRole.GLOBAL,
                    involved_root_roles=roles,
                )
            )


def _is_nested_declared_root(
    candidate: Path,
    current_root: Path,
    current_role: VaultRootRole,
    declared_roots: tuple[_DeclaredRoot, ...],
) -> bool:
    """Return whether ``candidate`` belongs to another declared root boundary."""

    for other in declared_roots:
        if other.role is current_role:
            continue
        other_paths: tuple[Path, ...] = (other.candidate,)
        if other.resolved is not None:
            other_paths = (other.resolved, other.candidate)
        if not any(_path_is_within(other_path, current_root) for other_path in other_paths):
            continue
        if any(_path_is_within(candidate, other_path) for other_path in other_paths):
            return True
    return False


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
    """Return only safe vault-relative metadata, never an absolute fallback."""

    if not _path_is_within(path, root):
        return "<outside-vault>"
    return Path(os.path.relpath(path, root)).as_posix()


def _path_is_within(path: Path, root: Path) -> bool:
    """Compare resolved/lexical paths with the host filesystem case policy."""

    try:
        path_key = os.path.normcase(os.path.abspath(os.fspath(path)))
        root_key = os.path.normcase(os.path.abspath(os.fspath(root)))
        return os.path.commonpath((path_key, root_key)) == root_key
    except ValueError:
        return False


def _paths_overlap(first: Path, second: Path) -> bool:
    """Detect same identity or ancestor/descendant overlap after resolution."""

    if _same_filesystem_identity(first, second):
        return True
    return _path_is_within(first, second) or _path_is_within(second, first)


def _same_filesystem_identity(first: Path, second: Path) -> bool:
    try:
        return _directory_identity(first) == _directory_identity(second)
    except OSError:
        return False
