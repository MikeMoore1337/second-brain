"""Application services Foundation и Safe Write Operations."""

from __future__ import annotations

import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from second_brain.application.llm import MAX_MAX_OUTPUT_BYTES, validate_note_draft
from second_brain.application.personal_memory import (
    PersonalMemoryDraftError,
    validate_personal_memory_draft,
)
from second_brain.application.ports import LlmError, ManagedNoteWriter, VaultReader
from second_brain.application.reports import Diagnostic, DiagnosticSeverity, ScanReport
from second_brain.application.research_draft import ReviewedResearchDraft
from second_brain.application.validation import build_report
from second_brain.application.writes import (
    CreateManagedNoteFromDraftRequest,
    CreateManagedNoteFromPersonalMemoryDraftRequest,
    CreateManagedNoteFromReviewedResearchDraftRequest,
    CreateManagedNoteRequest,
    CreateManagedNoteResult,
    CreateNotePlan,
    CreateStatus,
    WriteReceipt,
    WriteSafetyError,
)
from second_brain.domain.models import NoteRecord, NoteType, VaultManifest


@dataclass(frozen=True, slots=True)
class ValidateVault:
    """Запустить use case проверки vault через read-only port."""

    reader: VaultReader

    def execute(self) -> ScanReport:
        """Оркестрировать чтение и вернуть полный validation report."""

        return build_report(self.reader.scan())


@dataclass(frozen=True, slots=True)
class DoctorVault:
    """Запустить diagnostic use case через ту же read-only границу."""

    reader: VaultReader

    def execute(self) -> ScanReport:
        """Оркестрировать чтение и вернуть полный diagnostic report."""

        return build_report(self.reader.scan())


@dataclass(frozen=True, slots=True)
class CreateManagedNote:
    """Безопасно создать одну project/area/resource/zettel note."""

    reader: VaultReader
    writer: ManagedNoteWriter

    def execute(self, request: CreateManagedNoteRequest) -> CreateManagedNoteResult:
        """Выполнить preflight, dry-run либо apply с post-write validation."""

        return _execute_create(
            self.reader,
            self.writer,
            note_type=request.note_type,
            title=request.title,
            apply=request.apply,
            now=request.now,
            prepare=self.writer.prepare,
        )

    def rollback(self, receipt: WriteReceipt) -> bool:
        """Безопасно откатить созданную note через тот же writer receipt."""

        return _safe_rollback(self.writer, receipt)


@dataclass(frozen=True, slots=True)
class CreateManagedNoteFromDraft:
    """Offline Safe Write для одного уже просмотренного NoteDraft."""

    reader: VaultReader
    writer: ManagedNoteWriter

    def execute(self, request: CreateManagedNoteFromDraftRequest) -> CreateManagedNoteResult:
        """Проверить draft и переиспользовать общий preflight/write pipeline."""

        try:
            draft = validate_note_draft(request.draft, max_output_bytes=MAX_MAX_OUTPUT_BYTES)
        except LlmError:
            return CreateManagedNoteResult(
                CreateStatus.REJECTED,
                diagnostics=(
                    _diagnostic(
                        "DRAFT_SCHEMA_INVALID",
                        "draft does not satisfy the NoteDraft semantic contract",
                    ),
                ),
                apply_requested=request.apply,
            )

        def prepare(
            manifest: VaultManifest,
            note_type: NoteType,
            title: str,
            note_id: UUID,
            created: datetime,
        ) -> CreateNotePlan:
            del note_type, title
            return self.writer.prepare_from_draft(manifest, draft, note_id, created)

        return _execute_create(
            self.reader,
            self.writer,
            note_type=draft.note_type,
            title=draft.title,
            apply=request.apply,
            now=request.now,
            prepare=prepare,
        )

    def rollback(self, receipt: WriteReceipt) -> bool:
        """Безопасно откатить draft-based note через тот же writer receipt."""

        return _safe_rollback(self.writer, receipt)


@dataclass(frozen=True, slots=True)
class CreateManagedNoteFromPersonalMemoryDraft:
    """Safe Write для одного reviewed Personal Memory Stage 1 record."""

    reader: VaultReader
    writer: ManagedNoteWriter

    def execute(
        self,
        request: CreateManagedNoteFromPersonalMemoryDraftRequest,
    ) -> CreateManagedNoteResult:
        """Проверить typed wrapper и переиспользовать общий Safe Write pipeline."""

        if type(request) is not CreateManagedNoteFromPersonalMemoryDraftRequest:
            return CreateManagedNoteResult(
                CreateStatus.REJECTED,
                diagnostics=(
                    _diagnostic(
                        "PERSONAL_MEMORY_DRAFT_INVALID",
                        "request does not satisfy the Personal Memory draft contract",
                    ),
                ),
            )
        try:
            reviewed_draft = validate_personal_memory_draft(request.draft)
        except PersonalMemoryDraftError as exc:
            return CreateManagedNoteResult(
                CreateStatus.REJECTED,
                diagnostics=(_diagnostic(exc.code, exc.message),),
                apply_requested=request.apply,
            )

        expected_metadata = reviewed_draft.metadata

        def prepare(
            manifest: VaultManifest,
            note_type: NoteType,
            title: str,
            note_id: UUID,
            created: datetime,
        ) -> CreateNotePlan:
            del note_type, title
            return self.writer.prepare_from_personal_memory_draft(
                manifest,
                reviewed_draft,
                note_id,
                created,
            )

        def validate_created_note(note: NoteRecord, plan: CreateNotePlan) -> bool:
            del plan
            return note.personal_memory == expected_metadata

        return _execute_create(
            self.reader,
            self.writer,
            note_type=reviewed_draft.draft.note_type,
            title=reviewed_draft.draft.title,
            apply=request.apply,
            now=request.now,
            prepare=prepare,
            post_write_check=validate_created_note,
        )

    def rollback(self, receipt: WriteReceipt) -> bool:
        """Безопасно откатить Personal Memory note через receipt writer-а."""

        return _safe_rollback(self.writer, receipt)


@dataclass(frozen=True, slots=True)
class CreateManagedNoteFromReviewedResearchDraft:
    """Safe Write для reviewed research draft с одним source в v1."""

    reader: VaultReader
    writer: ManagedNoteWriter

    def execute(
        self,
        request: CreateManagedNoteFromReviewedResearchDraftRequest,
    ) -> CreateManagedNoteResult:
        """Проверить reviewed boundary и переиспользовать общий Safe Write pipeline."""

        if type(request) is not CreateManagedNoteFromReviewedResearchDraftRequest:
            return CreateManagedNoteResult(
                CreateStatus.REJECTED,
                diagnostics=(
                    _diagnostic(
                        "REVIEWED_RESEARCH_DRAFT_INVALID",
                        "request does not satisfy the reviewed research draft contract",
                    ),
                ),
            )
        reviewed = request.reviewed_draft
        if type(reviewed) is not ReviewedResearchDraft:
            return CreateManagedNoteResult(
                CreateStatus.REJECTED,
                diagnostics=(
                    _diagnostic(
                        "REVIEWED_RESEARCH_DRAFT_INVALID",
                        "reviewed research draft does not satisfy its application contract",
                    ),
                ),
                apply_requested=request.apply,
            )
        try:
            validate_note_draft(reviewed.draft, max_output_bytes=MAX_MAX_OUTPUT_BYTES)
        except LlmError:
            return CreateManagedNoteResult(
                CreateStatus.REJECTED,
                diagnostics=(
                    _diagnostic(
                        "DRAFT_SCHEMA_INVALID",
                        "draft does not satisfy the NoteDraft semantic contract",
                    ),
                ),
                apply_requested=request.apply,
            )

        def prepare(
            manifest: VaultManifest,
            note_type: NoteType,
            title: str,
            note_id: UUID,
            created: datetime,
        ) -> CreateNotePlan:
            del note_type, title
            return self.writer.prepare_from_reviewed_research_draft(
                manifest,
                reviewed,
                note_id,
                created,
            )

        return _execute_create(
            self.reader,
            self.writer,
            note_type=reviewed.draft.note_type,
            title=reviewed.draft.title,
            apply=request.apply,
            now=request.now,
            prepare=prepare,
        )

    def rollback(self, receipt: WriteReceipt) -> bool:
        """Безопасно откатить research-derived note через тот же writer receipt."""

        return _safe_rollback(self.writer, receipt)


def _execute_create(
    reader: VaultReader,
    writer: ManagedNoteWriter,
    *,
    note_type: NoteType,
    title: str,
    apply: bool,
    now: datetime | None,
    prepare: Callable[[VaultManifest, NoteType, str, UUID, datetime], CreateNotePlan],
    post_write_check: Callable[[NoteRecord, CreateNotePlan], bool] | None = None,
) -> CreateManagedNoteResult:
    """Общий Safe Write pipeline для обычного и draft-based создания."""

    pre_report = build_report(reader.scan())
    if pre_report.manifest is None:
        diagnostics = list(pre_report.diagnostics)
        diagnostics.append(
            _diagnostic(
                "CREATE_PREFLIGHT_FAILED",
                "vault must have a valid manifest and no validation errors before write",
            )
        )
        return CreateManagedNoteResult(
            CreateStatus.REJECTED,
            diagnostics=tuple(diagnostics),
            apply_requested=apply,
        )

    if note_type is NoteType.NOTE:
        return CreateManagedNoteResult(
            CreateStatus.REJECTED,
            diagnostics=(
                _diagnostic(
                    "CREATE_UNSUPPORTED_TYPE",
                    "only project, area, resource and zettel can be created in v1",
                ),
            ),
            apply_requested=apply,
        )

    effective_now = (now or datetime.now(UTC).astimezone()).replace(microsecond=0)
    if effective_now.tzinfo is None or effective_now.utcoffset() is None:
        return CreateManagedNoteResult(
            CreateStatus.REJECTED,
            diagnostics=(_diagnostic("CREATE_INVALID_TIMESTAMP", "created needs an UTC offset"),),
            apply_requested=apply,
        )
    note_id = uuid.uuid7()
    try:
        plan = prepare(pre_report.manifest, note_type, title, note_id, effective_now)
    except WriteSafetyError as exc:
        return CreateManagedNoteResult(
            CreateStatus.REJECTED,
            diagnostics=(_diagnostic(exc.code, str(exc), exc.path),),
            apply_requested=apply,
        )
    except (OSError, ValueError) as exc:
        return CreateManagedNoteResult(
            CreateStatus.REJECTED,
            diagnostics=(_diagnostic("CREATE_PLAN_FAILED", str(exc)),),
            apply_requested=apply,
        )

    if pre_report.error_count:
        diagnostics = list(pre_report.diagnostics)
        diagnostics.append(
            _diagnostic(
                "CREATE_PREFLIGHT_FAILED",
                "vault has validation errors; no write was attempted",
            )
        )
        return CreateManagedNoteResult(
            CreateStatus.REJECTED,
            plan=plan,
            diagnostics=tuple(diagnostics),
            apply_requested=apply,
        )

    if not apply:
        return CreateManagedNoteResult(CreateStatus.DRY_RUN, plan=plan, apply_requested=False)

    try:
        receipt = writer.write(plan)
    except WriteSafetyError as exc:
        return CreateManagedNoteResult(
            CreateStatus.REJECTED,
            plan=plan,
            diagnostics=(_diagnostic(exc.code, str(exc), exc.path),),
            apply_requested=True,
        )
    except (OSError, ValueError) as exc:
        return CreateManagedNoteResult(
            CreateStatus.REJECTED,
            plan=plan,
            diagnostics=(_diagnostic("CREATE_WRITE_FAILED", str(exc), plan.relative_path),),
            apply_requested=True,
        )

    try:
        post_report = build_report(reader.scan())
    except (OSError, UnicodeError, ValueError) as exc:
        diagnostics = [
            _diagnostic(
                "CREATE_POST_WRITE_VALIDATION_FAILED",
                f"post-write validation could not be completed: {exc}",
                plan.relative_path,
            )
        ]
        rollback_succeeded = _safe_rollback(writer, receipt)
        if not rollback_succeeded:
            diagnostics.append(
                _diagnostic(
                    "CREATE_ROLLBACK_FAILED",
                    "rollback did not remove the created file because its identity or "
                    "content changed",
                    plan.relative_path,
                )
            )
        return CreateManagedNoteResult(
            CreateStatus.ROLLED_BACK,
            plan=plan,
            diagnostics=tuple(diagnostics),
            rollback_succeeded=rollback_succeeded,
            apply_requested=True,
        )
    if not _post_write_is_valid(post_report, plan, post_write_check):
        diagnostics = list(post_report.diagnostics)
        diagnostics.append(
            _diagnostic(
                "CREATE_POST_WRITE_VALIDATION_FAILED",
                "created note did not pass post-write validation",
                plan.relative_path,
            )
        )
        rollback_succeeded = _safe_rollback(writer, receipt)
        if not rollback_succeeded:
            diagnostics.append(
                _diagnostic(
                    "CREATE_ROLLBACK_FAILED",
                    "rollback did not remove the created file because its identity or "
                    "content changed",
                    plan.relative_path,
                )
            )
        return CreateManagedNoteResult(
            CreateStatus.ROLLED_BACK,
            plan=plan,
            diagnostics=tuple(diagnostics),
            validation_report=post_report,
            rollback_succeeded=rollback_succeeded,
            apply_requested=True,
        )

    return CreateManagedNoteResult(
        CreateStatus.CREATED,
        plan=plan,
        validation_report=post_report,
        receipt=receipt,
        apply_requested=True,
    )


def _post_write_is_valid(
    report: ScanReport,
    plan: CreateNotePlan,
    extra_check: Callable[[NoteRecord, CreateNotePlan], bool] | None = None,
) -> bool:
    """Проверить именно созданную note и отсутствие новых validation errors."""

    if report.error_count:
        return False
    matches = [note for note in report.notes if note.relative_path == plan.relative_path]
    if len(matches) != 1:
        return False
    note = matches[0]
    base_valid = (
        note.managed
        and note.note_id == plan.note_id
        and note.note_type is plan.note_type
        and note.created == plan.created
    )
    return base_valid and (extra_check is None or extra_check(note, plan))


def _diagnostic(code: str, message: str, path: str | None = None) -> Diagnostic:
    """Создать application diagnostic без привязки к adapter."""

    return Diagnostic(code, message, DiagnosticSeverity.ERROR, path)


def _safe_rollback(writer: ManagedNoteWriter, receipt: WriteReceipt) -> bool:
    """Считать исключение rollback безопасным отказом, а не поводом удалить вслепую."""

    try:
        return writer.rollback(receipt)
    except OSError, ValueError:
        return False
