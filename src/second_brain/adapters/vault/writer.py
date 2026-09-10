"""Безопасный filesystem-adapter для создания одной managed note."""

from __future__ import annotations

import hashlib
import json
import os
import uuid
from collections.abc import Callable, Mapping, MutableMapping
from contextlib import suppress
from datetime import date, datetime
from io import StringIO
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING
from uuid import UUID

from ruamel.yaml import YAML

from second_brain.adapters.vault.frontmatter import FrontMatterResult, parse_front_matter
from second_brain.adapters.vault.operation_lock import (
    VaultOperationBusy,
    VaultOperationLock,
    VaultOperationLockError,
)
from second_brain.application.personal_memory import PERSONAL_MEMORY_MARKER
from second_brain.application.writes import CreateNotePlan, WriteReceipt, WriteSafetyError
from second_brain.domain.models import NoteType, VaultManifest

if TYPE_CHECKING:
    from second_brain.application.decision_journal import (
        DecisionJournalDraft,
        OutcomeObservationDraft,
    )
    from second_brain.application.llm import NoteDraft
    from second_brain.application.personal_memory import PersonalMemoryDraft
    from second_brain.application.research import SourceProvenance
    from second_brain.application.research_draft import ReviewedResearchDraft

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
_PERSONAL_MEMORY_MARKER_FIELDS = frozenset((PERSONAL_MEMORY_MARKER,))
_PERSONAL_MEMORY_CONTROLLED_FIELDS = frozenset(
    {
        PERSONAL_MEMORY_MARKER,
        "evidence_kind",
        "self_kind",
        "evidence_at",
        "evidence_at_precision",
        "domain",
    }
)
_STAGE2_CONTROLLED_FIELDS = frozenset(
    {
        PERSONAL_MEMORY_MARKER,
        "evidence_kind",
        "self_kind",
        "evidence_at",
        "evidence_at_precision",
        "domain",
        "decision_id",
    }
)


class FileSystemVaultWriter:
    """Подготовить и атомарно создать файл внутри настроенного vault."""

    def __init__(self, root: Path, *, operation_lock_path: Path | None = None) -> None:
        try:
            resolved_root = root.resolve(strict=True)
        except OSError as exc:
            raise WriteSafetyError("CREATE_VAULT_ROOT_INVALID", str(exc)) from exc
        if not resolved_root.is_dir():
            raise WriteSafetyError("CREATE_VAULT_ROOT_INVALID", "vault root is not a directory")
        self.root = resolved_root
        self.operation_lock_path = operation_lock_path

    def prepare(
        self,
        manifest: VaultManifest,
        note_type: NoteType,
        title: str,
        note_id: UUID,
        created: datetime,
    ) -> CreateNotePlan:
        """Проверить пути, прочитать template и собрать dry-run plan."""

        return self._prepare(
            manifest,
            note_type,
            title,
            note_id,
            created,
            lambda template_text: _render_template(template_text, note_type, note_id, created),
        )

    def prepare_from_draft(
        self,
        manifest: VaultManifest,
        draft: NoteDraft,
        note_id: UUID,
        created: datetime,
    ) -> CreateNotePlan:
        """Собрать draft-based plan с lossless body и additive front matter."""

        return self._prepare(
            manifest,
            draft.note_type,
            draft.title,
            note_id,
            created,
            lambda template_text: _render_draft_template(
                template_text,
                draft,
                note_id,
                created,
            ),
        )

    def prepare_from_reviewed_research_draft(
        self,
        manifest: VaultManifest,
        reviewed_draft: ReviewedResearchDraft,
        note_id: UUID,
        created: datetime,
    ) -> CreateNotePlan:
        """Собрать plan reviewed research draft с additive ``sources`` metadata."""

        draft = reviewed_draft.draft
        return self._prepare(
            manifest,
            draft.note_type,
            draft.title,
            note_id,
            created,
            lambda template_text: _render_reviewed_research_draft_template(
                template_text,
                reviewed_draft,
                note_id,
                created,
            ),
        )

    def prepare_from_personal_memory_draft(
        self,
        manifest: VaultManifest,
        personal_memory_draft: PersonalMemoryDraft,
        note_id: UUID,
        created: datetime,
    ) -> CreateNotePlan:
        """Собрать plan с application-owned Personal Memory metadata и marker."""

        draft = personal_memory_draft.draft
        return self._prepare(
            manifest,
            draft.note_type,
            draft.title,
            note_id,
            created,
            lambda template_text: _render_personal_memory_draft_template(
                template_text,
                personal_memory_draft,
                note_id,
                created,
            ),
        )

    def prepare_from_decision_journal_draft(
        self,
        manifest: VaultManifest,
        decision_journal_draft: DecisionJournalDraft,
        note_id: UUID,
        created: datetime,
    ) -> CreateNotePlan:
        """Собрать plan с application-owned Decision Journal metadata."""

        draft = decision_journal_draft.draft
        return self._prepare(
            manifest,
            draft.note_type,
            draft.title,
            note_id,
            created,
            lambda template_text: _render_decision_journal_template(
                template_text,
                decision_journal_draft,
                note_id,
                created,
            ),
            include_precondition=True,
        )

    def prepare_from_outcome_observation_draft(
        self,
        manifest: VaultManifest,
        outcome_observation_draft: OutcomeObservationDraft,
        note_id: UUID,
        created: datetime,
    ) -> CreateNotePlan:
        """Собрать plan с application-owned Outcome relation metadata."""

        draft = outcome_observation_draft.draft
        return self._prepare(
            manifest,
            draft.note_type,
            draft.title,
            note_id,
            created,
            lambda template_text: _render_outcome_observation_template(
                template_text,
                outcome_observation_draft,
                note_id,
                created,
            ),
            include_precondition=True,
        )

    def _prepare(
        self,
        manifest: VaultManifest,
        note_type: NoteType,
        title: str,
        note_id: UUID,
        created: datetime,
        render: Callable[[str], str],
        include_precondition: bool = False,
    ) -> CreateNotePlan:
        """Общий read-only planning boundary для обоих create flows."""

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
        content = render(template_text)
        plan_sha256 = (
            _stage2_plan_digest(
                manifest=manifest,
                note_type=note_type,
                title=title,
                relative_path=relative_path,
                target_root_relative=_relative(self.root, target_root),
                template_relative_path=_relative(self.root, template_resolved),
                template_text=template_text,
                rendered_content=content,
            )
            if include_precondition
            else None
        )
        return CreateNotePlan(
            note_type,
            title,
            note_id,
            created,
            relative_path,
            content,
            _relative(self.root, target_root),
            plan_sha256,
        )

    def write(self, plan: CreateNotePlan) -> WriteReceipt:
        """Записать plan через shared lock, temp file и no-overwrite publication."""

        if self.operation_lock_path is None:
            return self._write_unlocked(plan)
        try:
            with VaultOperationLock(self.operation_lock_path, operation="safe-write"):
                return self._write_unlocked(plan)
        except VaultOperationBusy as exc:
            raise WriteSafetyError(
                "CREATE_VAULT_OPERATION_BUSY",
                "another vault sync/write operation is active",
            ) from exc
        except VaultOperationLockError as exc:
            raise WriteSafetyError(
                "CREATE_VAULT_LOCK_FAILED",
                "shared vault operation lock is unavailable",
            ) from exc

    def _write_unlocked(self, plan: CreateNotePlan) -> WriteReceipt:
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

        if self.operation_lock_path is None:
            return self._rollback_unlocked(receipt)
        try:
            with VaultOperationLock(self.operation_lock_path, operation="safe-write-rollback"):
                return self._rollback_unlocked(receipt)
        except VaultOperationBusy, VaultOperationLockError:
            return False

    def _rollback_unlocked(self, receipt: WriteReceipt) -> bool:
        """Выполнить receipt rollback под уже захваченным или отсутствующим lock."""

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
    if title != title.strip():
        raise WriteSafetyError(
            "CREATE_INVALID_TITLE",
            "title must not start or end with whitespace",
        )
    value = title
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
    parsed, data = _load_template_data(template_text)
    if data is not None:
        _set_managed_metadata(data, note_type, note_id, created)
        _sanitize_template_mapping(data, _PERSONAL_MEMORY_MARKER_FIELDS)
        return _dump_without_personal_memory_marker(data, parsed.body)
    front_matter = (
        "---\n"
        f"id: {note_id}\n"
        f"type: {note_type.value}\n"
        f"created: {created.isoformat(timespec='seconds')}\n"
        "---\n"
    )
    return front_matter + template_text


def _render_draft_template(
    template_text: str,
    draft: NoteDraft,
    note_id: UUID,
    created: datetime,
) -> str:
    """Render only draft body while preserving template front matter defaults."""

    _, data = _load_template_data(template_text)
    if data is not None:
        _set_managed_metadata(data, draft.note_type, note_id, created)
        _sanitize_template_mapping(data, _PERSONAL_MEMORY_MARKER_FIELDS)
        data["tags"] = list(draft.tags)
        data["links"] = list(draft.links)
        return _dump_without_personal_memory_marker(data, draft.content)

    data = {
        "id": str(note_id),
        "type": draft.note_type.value,
        "created": created.isoformat(timespec="seconds"),
        "tags": list(draft.tags),
        "links": list(draft.links),
    }
    return _dump_front_matter(data) + draft.content


def _render_reviewed_research_draft_template(
    template_text: str,
    reviewed_draft: ReviewedResearchDraft,
    note_id: UUID,
    created: datetime,
) -> str:
    """Сохранить draft body и authoritative reviewed source list без network."""

    draft = reviewed_draft.draft
    _, data = _load_template_data(template_text)
    if data is not None:
        _set_managed_metadata(data, draft.note_type, note_id, created)
        _sanitize_template_mapping(data, _PERSONAL_MEMORY_MARKER_FIELDS)
        data["tags"] = list(draft.tags)
        data["links"] = list(draft.links)
        data["sources"] = [
            _source_provenance_to_mapping(source) for source in reviewed_draft.sources
        ]
        return _dump_without_personal_memory_marker(data, draft.content)

    data = {
        "id": str(note_id),
        "type": draft.note_type.value,
        "created": created.isoformat(timespec="seconds"),
        "tags": list(draft.tags),
        "links": list(draft.links),
        "sources": [_source_provenance_to_mapping(source) for source in reviewed_draft.sources],
    }
    return _dump_front_matter(data) + draft.content


def _render_personal_memory_draft_template(
    template_text: str,
    personal_memory_draft: PersonalMemoryDraft,
    note_id: UUID,
    created: datetime,
) -> str:
    """Сериализовать только reviewed Stage 1 fields поверх обычного NoteDraft."""

    draft = personal_memory_draft.draft
    metadata = personal_memory_draft.metadata
    _, data = _load_template_data(template_text)
    if data is None:
        data = {}
    _sanitize_template_mapping(data, _PERSONAL_MEMORY_CONTROLLED_FIELDS)
    _set_managed_metadata(data, draft.note_type, note_id, created)
    data["second_brain_personal_memory"] = 1
    data["evidence_kind"] = metadata.evidence_kind.value
    data["self_kind"] = metadata.self_kind.value
    data["evidence_at"] = (
        metadata.evidence_at
        if isinstance(metadata.evidence_at, str)
        else metadata.evidence_at.isoformat()
    )
    data["evidence_at_precision"] = metadata.evidence_at_precision.value
    if metadata.domain is None:
        data.pop("domain", None)
    else:
        data["domain"] = metadata.domain
    data["tags"] = list(draft.tags)
    data["links"] = list(draft.links)
    return _dump_front_matter(data) + draft.content


def _render_decision_journal_template(
    template_text: str,
    decision_journal_draft: DecisionJournalDraft,
    note_id: UUID,
    created: datetime,
) -> str:
    """Сериализовать только fixed reviewed Decision Journal metadata."""

    draft = decision_journal_draft.draft
    metadata = decision_journal_draft.metadata
    _, data = _load_template_data(template_text)
    if data is None:
        data = {}
    _sanitize_template_mapping(data, _STAGE2_CONTROLLED_FIELDS)
    _set_managed_metadata(data, draft.note_type, note_id, created)
    data[PERSONAL_MEMORY_MARKER] = 1
    data["evidence_kind"] = "observed_decision"
    data["self_kind"] = "decision"
    data["evidence_at"] = _serialize_evidence_at(metadata.evidence_at)
    data["evidence_at_precision"] = metadata.evidence_at_precision.value
    if metadata.domain is None:
        data.pop("domain", None)
    else:
        data["domain"] = metadata.domain
    data["tags"] = list(draft.tags)
    data["links"] = list(draft.links)
    return _dump_front_matter(data) + draft.content


def _render_outcome_observation_template(
    template_text: str,
    outcome_observation_draft: OutcomeObservationDraft,
    note_id: UUID,
    created: datetime,
) -> str:
    """Сериализовать fixed Outcome metadata и exact UUIDv7 relation."""

    draft = outcome_observation_draft.draft
    metadata = outcome_observation_draft.metadata
    assert metadata.decision_id is not None
    _, data = _load_template_data(template_text)
    if data is None:
        data = {}
    _sanitize_template_mapping(data, _STAGE2_CONTROLLED_FIELDS)
    _set_managed_metadata(data, draft.note_type, note_id, created)
    data[PERSONAL_MEMORY_MARKER] = 1
    data["evidence_kind"] = "outcome_later_observation"
    data["self_kind"] = "outcome"
    data["evidence_at"] = _serialize_evidence_at(metadata.evidence_at)
    data["evidence_at_precision"] = metadata.evidence_at_precision.value
    data["decision_id"] = str(metadata.decision_id)
    if metadata.domain is None:
        data.pop("domain", None)
    else:
        data["domain"] = metadata.domain
    data["tags"] = list(draft.tags)
    data["links"] = list(draft.links)
    return _dump_front_matter(data) + draft.content


def _serialize_evidence_at(value: object) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, datetime):
        return value.isoformat()
    raise WriteSafetyError("CREATE_INVALID_PLAN", "evidence_at is not normalized")


def _source_provenance_to_mapping(source: SourceProvenance) -> dict[str, str]:
    """Сериализовать только validated provider-neutral source metadata."""

    mapping = {
        "uri": source.uri,
        "kind": source.source_kind.value,
        "retrieved_at": source.retrieved_at.isoformat(),
    }
    if source.title is not None:
        mapping["title"] = source.title
    if source.author is not None:
        mapping["author"] = source.author
    if source.published_at is not None:
        mapping["published_at"] = source.published_at.isoformat()
    if source.upstream_id is not None:
        mapping["upstream_id"] = source.upstream_id
    return mapping


def _load_template_data(
    template_text: str,
) -> tuple[FrontMatterResult, MutableMapping[str, object] | None]:
    """Загрузить template front matter через существующую round-trip YAML policy."""

    parsed = parse_front_matter(template_text)
    if parsed.error is not None:
        raise WriteSafetyError("CREATE_TEMPLATE_INVALID", parsed.error)
    if not parsed.has_front_matter:
        return parsed, None
    if parsed.header is None:
        raise WriteSafetyError(
            "CREATE_TEMPLATE_INVALID",
            "template front matter header is unavailable",
        )
    yaml = _round_trip_yaml()
    try:
        data = yaml.load(parsed.header)
    except Exception as exc:  # ruamel exposes several parser/constructor exception types
        raise WriteSafetyError("CREATE_TEMPLATE_INVALID", str(exc)) from exc
    if data is None:
        data = {}
    if not isinstance(data, MutableMapping):
        raise WriteSafetyError("CREATE_TEMPLATE_INVALID", "front matter must be a mapping")
    return parsed, data


def _set_managed_metadata(
    data: MutableMapping[str, object],
    note_type: NoteType,
    note_id: UUID,
    created: datetime,
) -> None:
    """Заменить только application-managed id/type/created поля."""

    data["id"] = str(note_id)
    data["type"] = note_type.value
    data["created"] = created.isoformat(timespec="seconds")


def _sanitize_template_mapping(
    data: MutableMapping[str, object],
    fields: frozenset[str],
    seen: set[int] | None = None,
) -> None:
    """Удалить controlled keys из root и всех сохранённых YAML merge sources."""

    visited = set() if seen is None else seen
    identity = id(data)
    if identity in visited:
        return
    visited.add(identity)
    for field in fields:
        if field not in data:
            continue
        with suppress(KeyError):
            del data[field]
    merge = getattr(data, "merge", ())
    for source in merge or ():
        if isinstance(source, MutableMapping):
            _sanitize_template_mapping(source, fields, visited)


def _dump_without_personal_memory_marker(
    data: MutableMapping[str, object],
    body: str,
) -> str:
    """Проверить post-render, что generic path не выпускает root-level marker."""

    content = _dump_front_matter(data) + body
    parsed = parse_front_matter(content)
    if parsed.error is not None:
        raise WriteSafetyError(
            "CREATE_TEMPLATE_INVALID",
            "rendered template front matter could not be validated",
        )
    if PERSONAL_MEMORY_MARKER in parsed.data:
        raise WriteSafetyError(
            "CREATE_TEMPLATE_INVALID",
            "generic writer cannot emit the Personal Memory marker",
        )
    return content


def _dump_front_matter(data: MutableMapping[str, object]) -> str:
    """Сериализовать front matter и не менять переданный Markdown body."""

    stream = StringIO()
    _round_trip_yaml().dump(data, stream)
    return f"---\n{stream.getvalue().rstrip(chr(10))}\n---\n"


def _round_trip_yaml() -> YAML:
    """Создать YAML serializer с текущими template round-trip настройками."""

    yaml = YAML()
    yaml.allow_duplicate_keys = False
    yaml.allow_unicode = True
    yaml.preserve_quotes = True
    return yaml


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


def _stage2_plan_digest(
    *,
    manifest: VaultManifest,
    note_type: NoteType,
    title: str,
    relative_path: str,
    target_root_relative: str,
    template_relative_path: str,
    template_text: str,
    rendered_content: str,
) -> str:
    """Fingerprint only the stable inputs of a Stage 2 prepared plan.

    The generated note UUID and ``created`` timestamp are deliberately absent from
    this projection.  They remain part of the ordinary plan and post-write
    validation, while this digest binds the inputs that must not drift between
    Stage 2 prepare and apply.
    """

    parsed = parse_front_matter(rendered_content)
    if parsed.error is not None:
        raise WriteSafetyError(
            "CREATE_PLAN_FAILED",
            "rendered Stage 2 plan front matter could not be validated",
        )
    rendered_front_matter = dict(parsed.data)
    rendered_front_matter.pop("id", None)
    rendered_front_matter.pop("created", None)
    payload = {
        "manifest": {
            "schema_version": manifest.schema_version,
            "vault_id": str(manifest.vault_id),
            "default_language": manifest.default_language,
            "paths": manifest.paths.as_dict(),
            "attachments": {
                "warning_size_bytes": manifest.attachments.warning_size_bytes,
                "max_size_bytes": manifest.attachments.max_size_bytes,
            },
        },
        "note_type": note_type.value,
        "title": title,
        "relative_path": relative_path,
        "target_root_relative": target_root_relative,
        "template_relative_path": template_relative_path,
        "template_sha256": _sha256(template_text.encode("utf-8")),
        "rendered_plan_basis": {
            "front_matter": _digest_value(rendered_front_matter),
            "body": parsed.body,
        },
    }
    try:
        canonical = json.dumps(
            _digest_value(payload),
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, UnicodeEncodeError, ValueError) as exc:
        raise WriteSafetyError("CREATE_PLAN_FAILED", "Stage 2 plan fingerprint failed") from exc
    return _sha256(canonical)


def _digest_value(value: object) -> object:
    """Convert validated YAML/domain values into deterministic JSON values."""

    if value is None or type(value) in {bool, int, float, str}:
        return value
    if isinstance(value, (datetime, date, UUID)):
        return value.isoformat() if not isinstance(value, UUID) else str(value)
    if isinstance(value, Mapping):
        return {str(key): _digest_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_digest_value(item) for item in value]
    raise TypeError(f"unsupported fingerprint value: {type(value).__name__}")


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
