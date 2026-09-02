"""Безопасный filesystem-adapter для создания одной managed note."""

from __future__ import annotations

import hashlib
import os
import uuid
from datetime import datetime
from io import StringIO
from pathlib import Path, PurePosixPath
from uuid import UUID

from ruamel.yaml import YAML

from second_brain.adapters.vault.frontmatter import parse_front_matter
from second_brain.application.writes import CreateNotePlan, WriteReceipt, WriteSafetyError
from second_brain.domain.models import NoteType, VaultManifest

_CONTENT_ROOTS = {
    NoteType.PROJECT: "projects",
    NoteType.AREA: "areas",
    NoteType.RESOURCE: "resources",
    NoteType.ZETTEL: "zettelkasten",
}
_TEMPLATE_NAMES = {
    NoteType.PROJECT: "Project.md",
    NoteType.AREA: "Area.md",
    NoteType.RESOURCE: "Resource.md",
    NoteType.ZETTEL: "Zettel.md",
}
_INVALID_FILENAME_CHARACTERS = frozenset('<>:"/\\|?*')
_WINDOWS_RESERVED_NAMES = frozenset(
    {
        "con",
        "prn",
        "aux",
        "nul",
        *(f"com{index}" for index in range(1, 10)),
        *(f"lpt{index}" for index in range(1, 10)),
    }
)


class FileSystemVaultWriter:
    """Подготовить и атомарно создать файл внутри настроенного vault."""

    def __init__(self, root: Path) -> None:
        try:
            resolved_root = root.resolve(strict=True)
        except OSError as exc:
            raise WriteSafetyError("CREATE_VAULT_ROOT_INVALID", str(exc)) from exc
        if not resolved_root.is_dir():
            raise WriteSafetyError("CREATE_VAULT_ROOT_INVALID", "vault root is not a directory")
        self.root = resolved_root

    def prepare(
        self,
        manifest: VaultManifest,
        note_type: NoteType,
        title: str,
        note_id: UUID,
        created: datetime,
    ) -> CreateNotePlan:
        """Проверить пути, прочитать template и собрать dry-run plan."""

        root_name = _CONTENT_ROOTS.get(note_type)
        template_name = _TEMPLATE_NAMES.get(note_type)
        if root_name is None or template_name is None:
            raise WriteSafetyError(
                "CREATE_UNSUPPORTED_TYPE",
                "only project, area, resource and zettel can be created in v1",
            )
        filename = _filename_from_title(title)
        target_root = self._safe_directory(getattr(manifest.paths, root_name), root_name)
        template_root = self._safe_directory(manifest.paths.templates, "templates")
        template_path = template_root / template_name
        self._check_path_components(template_path, "template")
        if _is_link_like(template_path):
            raise WriteSafetyError(
                "CREATE_LINKED_PATH",
                "template path must not be a symlink or junction",
                _relative(self.root, template_path),
            )
        try:
            template_resolved = template_path.resolve(strict=True)
        except OSError as exc:
            raise WriteSafetyError(
                "CREATE_TEMPLATE_MISSING",
                f"cannot resolve template: {exc}",
                _relative(self.root, template_path),
            ) from exc
        if not template_resolved.is_file() or not _is_relative_to(template_resolved, self.root):
            raise WriteSafetyError(
                "CREATE_PATH_ESCAPE",
                "template resolves outside the vault or is not a regular file",
                _relative(self.root, template_path),
            )
        try:
            template_text = template_resolved.read_text(encoding="utf-8")
        except (OSError, UnicodeError) as exc:
            raise WriteSafetyError(
                "CREATE_TEMPLATE_READ_FAILED",
                f"cannot read template as UTF-8: {exc}",
                _relative(self.root, template_path),
            ) from exc

        target_path = target_root / filename
        self._check_path_components(target_path, "target")
        self._ensure_target_absent(target_path, target_root)
        relative_path = _relative(self.root, target_path)
        content = _render_template(template_text, note_type, note_id, created)
        return CreateNotePlan(
            note_type,
            title,
            note_id,
            created,
            relative_path,
            content,
            _relative(self.root, target_root),
        )

    def write(self, plan: CreateNotePlan) -> WriteReceipt:
        """Записать plan через temp file и no-overwrite publication."""

        target = self._target_from_plan(plan)
        target_root = target.parent
        self._check_path_components(target, "target")
        self._ensure_target_absent(target, target_root)

        temp_path: Path | None = None
        published = False
        published_identity: tuple[int, int] | None = None
        try:
            temp_path = _create_temp_file(target_root, plan.content)
            self._ensure_target_absent(target, target_root)
            _publish_without_overwrite(temp_path, target)
            published = True
            published_identity = _file_identity(target)
            try:
                temp_path.unlink()
            except FileNotFoundError:
                pass
            except OSError as exc:
                _remove_temp_file(temp_path)
                _remove_published_if_matches(target, plan.content, published_identity)
                raise WriteSafetyError(
                    "CREATE_WRITE_FAILED",
                    f"cannot remove temporary file after publication: {exc}",
                    _relative(self.root, target),
                ) from exc
            try:
                receipt = _receipt_for(target, self.root, plan)
            except (OSError, UnicodeError) as exc:
                _remove_published_if_matches(target, plan.content, published_identity)
                raise WriteSafetyError(
                    "CREATE_WRITE_FAILED",
                    f"cannot verify published file: {exc}",
                    _relative(self.root, target),
                ) from exc
            return receipt
        except FileExistsError as exc:
            raise WriteSafetyError(
                "CREATE_TARGET_EXISTS",
                "target already exists; existing files are never overwritten",
                _relative(self.root, target),
            ) from exc
        except WriteSafetyError:
            raise
        except (OSError, UnicodeError) as exc:
            raise WriteSafetyError(
                "CREATE_WRITE_FAILED", str(exc), _relative(self.root, target)
            ) from exc
        finally:
            if temp_path is not None and temp_path.exists() and not published:
                _remove_temp_file(temp_path)

    def rollback(self, receipt: WriteReceipt) -> bool:
        """Удалить файл только при совпадении identity и SHA-256 receipt."""

        target = Path(receipt.target_path)
        if not _is_relative_to(target, self.root) or _is_link_like(target):
            return False
        try:
            stat_result = target.stat()
            if (stat_result.st_dev, stat_result.st_ino) != receipt.file_identity:
                return False
            if _sha256(target.read_bytes()) != receipt.content_sha256:
                return False
            target.unlink()
        except OSError, UnicodeError:
            return False
        return not target.exists()

    def _safe_directory(self, relative: PurePosixPath, label: str) -> Path:
        candidate = self.root.joinpath(*relative.parts)
        self._check_path_components(candidate, label)
        if _is_link_like(candidate):
            raise WriteSafetyError(
                "CREATE_LINKED_PATH",
                f"{label} root must not be a symlink or junction",
                _relative(self.root, candidate),
            )
        try:
            resolved = candidate.resolve(strict=True)
        except OSError as exc:
            raise WriteSafetyError(
                "CREATE_ROOT_MISSING",
                f"{label} root is missing or unreadable: {exc}",
                _relative(self.root, candidate),
            ) from exc
        if not resolved.is_dir():
            raise WriteSafetyError(
                "CREATE_ROOT_NOT_DIRECTORY",
                f"{label} root is not a directory",
                _relative(self.root, candidate),
            )
        if not _is_relative_to(resolved, self.root):
            raise WriteSafetyError(
                "CREATE_PATH_ESCAPE",
                f"{label} root resolves outside the vault",
                _relative(self.root, candidate),
            )
        return resolved

    def _check_path_components(self, path: Path, label: str) -> None:
        if not _is_relative_to(path, self.root):
            raise WriteSafetyError(
                "CREATE_PATH_ESCAPE",
                f"{label} path is outside the vault",
                str(path),
            )
        relative = path.relative_to(self.root)
        current = self.root
        for part in relative.parts:
            current /= part
            if _is_link_like(current):
                raise WriteSafetyError(
                    "CREATE_LINKED_PATH",
                    f"{label} path contains a symlink or junction",
                    _relative(self.root, current),
                )

    def _ensure_target_absent(self, target: Path, target_root: Path) -> None:
        if _is_link_like(target):
            raise WriteSafetyError(
                "CREATE_LINKED_PATH",
                "target path must not be a symlink or junction",
                _relative(self.root, target),
            )
        try:
            if target.exists():
                raise WriteSafetyError(
                    "CREATE_TARGET_EXISTS",
                    "target already exists; existing files are never overwritten",
                    _relative(self.root, target),
                )
            for child in target_root.iterdir():
                if child.name.casefold() != target.name.casefold():
                    continue
                if _is_link_like(child):
                    raise WriteSafetyError(
                        "CREATE_LINKED_PATH",
                        "target path must not be a symlink or junction",
                        _relative(self.root, child),
                    )
                raise WriteSafetyError(
                    "CREATE_TARGET_EXISTS",
                    "target already exists; existing files are never overwritten",
                    _relative(self.root, target),
                )
        except OSError as exc:
            raise WriteSafetyError(
                "CREATE_TARGET_CHECK_FAILED",
                f"cannot inspect target directory: {exc}",
                _relative(self.root, target_root),
            ) from exc

    def _target_from_plan(self, plan: CreateNotePlan) -> Path:
        if not plan.target_root_relative:
            raise WriteSafetyError(
                "CREATE_INVALID_PLAN",
                "plan does not declare its manifest-selected target root",
                plan.relative_path,
            )
        target_root = self._safe_directory(
            PurePosixPath(plan.target_root_relative),
            "target",
        )
        try:
            filename = _filename_from_title(plan.title)
        except WriteSafetyError as exc:
            raise WriteSafetyError(exc.code, str(exc), plan.relative_path) from exc
        candidate = target_root / filename
        if _relative(self.root, candidate) != plan.relative_path:
            raise WriteSafetyError(
                "CREATE_PATH_ESCAPE",
                "plan target is not contained by the vault",
                plan.relative_path,
            )
        return candidate


def _filename_from_title(title: str) -> str:
    if not isinstance(title, str) or not title.strip():
        raise WriteSafetyError("CREATE_INVALID_TITLE", "title must be a non-empty filename")
    value = title.strip()
    if value in {".", ".."} or any(char in _INVALID_FILENAME_CHARACTERS for char in value):
        raise WriteSafetyError(
            "CREATE_INVALID_TITLE",
            "title must be one safe filename segment without path separators",
        )
    if any(ord(char) < 32 for char in value) or value.endswith((".", " ")):
        raise WriteSafetyError("CREATE_INVALID_TITLE", "title contains unsafe filename characters")
    stem = value[:-3] if value.casefold().endswith(".md") else value
    windows_basename = stem.split(".", 1)[0]
    if not stem or windows_basename.casefold() in _WINDOWS_RESERVED_NAMES:
        raise WriteSafetyError("CREATE_INVALID_TITLE", "title uses a reserved filename")
    return value if value.casefold().endswith(".md") else f"{value}.md"


def _render_template(
    template_text: str, note_type: NoteType, note_id: UUID, created: datetime
) -> str:
    parsed = parse_front_matter(template_text)
    if parsed.error is not None:
        raise WriteSafetyError("CREATE_TEMPLATE_INVALID", parsed.error)
    if parsed.has_front_matter:
        data = dict(parsed.data)
        data["id"] = str(note_id)
        data["type"] = note_type.value
        data["created"] = created.isoformat(timespec="seconds")
        yaml = YAML(typ="safe")
        yaml.default_flow_style = False
        yaml.allow_unicode = True
        stream = StringIO()
        yaml.dump(data, stream)
        return f"---\n{stream.getvalue().rstrip(chr(10))}\n---\n{parsed.body}"
    front_matter = (
        "---\n"
        f"id: {note_id}\n"
        f"type: {note_type.value}\n"
        f"created: {created.isoformat(timespec='seconds')}\n"
        "---\n"
    )
    return front_matter + template_text


def _create_temp_file(directory: Path, content: str) -> Path:
    for _ in range(8):
        candidate = directory / f".second-brain-{uuid.uuid7().hex}.tmp"
        try:
            flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
            fd = os.open(candidate, flags, 0o600)
        except FileExistsError:
            continue
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="") as stream:
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
        except BaseException:
            _remove_temp_file(candidate)
            raise
        return candidate
    raise OSError("could not allocate a unique temporary file")


def _publish_without_overwrite(temp_path: Path, target: Path) -> None:
    if os.name == "nt":
        # Windows os.rename refuses an existing destination, unlike os.replace.
        os.rename(temp_path, target)
        return
    os.link(temp_path, target)


def _receipt_for(target: Path, root: Path, plan: CreateNotePlan) -> WriteReceipt:
    stat_result = target.stat()
    actual = target.read_bytes()
    if actual != plan.content.encode("utf-8"):
        raise OSError("published file content differs from the planned content")
    return WriteReceipt(
        target_path=str(target),
        relative_path=_relative(root, target),
        content_sha256=_sha256(actual),
        file_identity=(stat_result.st_dev, stat_result.st_ino),
    )


def _remove_temp_file(path: Path) -> None:
    try:
        path.unlink()
    except FileNotFoundError:
        pass
    except OSError:
        pass


def _remove_published_if_matches(
    target: Path, content: str, expected_identity: tuple[int, int] | None
) -> None:
    """Откатить публикацию только при совпадении identity и ожидаемого тела."""

    if expected_identity is None or _is_link_like(target):
        return
    try:
        stat_result = target.stat()
        if (stat_result.st_dev, stat_result.st_ino) != expected_identity:
            return
        if target.read_bytes() == content.encode("utf-8"):
            target.unlink()
    except OSError, UnicodeError:
        pass


def _file_identity(path: Path) -> tuple[int, int] | None:
    try:
        stat_result = path.stat()
    except OSError:
        return None
    return stat_result.st_dev, stat_result.st_ino


def _sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


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


def _is_link_like(path: Path) -> bool:
    try:
        if path.is_symlink():
            return True
        is_junction = getattr(path, "is_junction", None)
        return bool(is_junction is not None and is_junction())
    except OSError:
        return True
