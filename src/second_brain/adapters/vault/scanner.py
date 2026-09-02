"""Read-only filesystem scanner для чтения контракта vault."""

from __future__ import annotations

from pathlib import Path

from second_brain.adapters.vault.frontmatter import parse_front_matter
from second_brain.adapters.vault.manifest import load_manifest
from second_brain.adapters.vault.markdown_links import extract_links_from_body
from second_brain.application.reports import Diagnostic, DiagnosticSeverity, VaultSnapshot
from second_brain.domain.models import AttachmentRecord, LinkReference, MarkdownDocument

_CONTENT_PATH_NAMES = ("inbox", "projects", "areas", "resources", "zettelkasten", "archive")


class FileSystemVaultReader:
    """Прочитать настроенный vault без перехода по links и записи файлов."""

    def __init__(self, root: Path) -> None:
        self.root = root

    def scan(self) -> VaultSnapshot:
        """Прочитать manifest, Markdown-документы, links и attachments."""

        diagnostics: list[Diagnostic] = []
        try:
            vault_root = self.root.resolve(strict=True)
        except OSError as exc:
            return VaultSnapshot(
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
            return VaultSnapshot(
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
            return VaultSnapshot(str(vault_root), None, diagnostics=tuple(diagnostics))
        manifest_result = load_manifest(manifest_path)
        diagnostics.extend(manifest_result.diagnostics)
        manifest = manifest_result.manifest
        if manifest is None:
            return VaultSnapshot(str(vault_root), None, diagnostics=tuple(diagnostics))

        resolved_roots: dict[str, Path] = {}
        for name in (*_CONTENT_PATH_NAMES, "templates", "attachments"):
            relative = getattr(manifest.paths, name)
            candidate = vault_root / Path(relative.as_posix())
            resolved = _resolve_directory(candidate, vault_root, name, diagnostics)
            if resolved is not None:
                resolved_roots[name] = resolved

        _report_overlapping_roots(resolved_roots, diagnostics)

        documents: list[MarkdownDocument] = []
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
                documents,
                links,
                diagnostics,
            )

        attachment_root = resolved_roots.get("attachments")
        if attachment_root is not None:
            _scan_attachment_tree(attachment_root, vault_root, attachments, diagnostics)

        return VaultSnapshot(
            vault_path=str(vault_root),
            manifest=manifest,
            documents=tuple(documents),
            links=tuple(links),
            attachments=tuple(attachments),
            diagnostics=tuple(diagnostics),
        )


def _scan_content_tree(
    root: Path,
    vault_root: Path,
    inbox_root: Path | None,
    documents: list[MarkdownDocument],
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
            document = _read_document(relative_path, text, in_inbox, diagnostics)
            documents.append(document)
            links.extend(extract_links_from_body(document.body, document.relative_path))


def _read_document(
    relative_path: str,
    text: str,
    in_inbox: bool,
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
