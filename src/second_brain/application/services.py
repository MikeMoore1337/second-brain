"""Application services Foundation и Safe Write Operations."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

from second_brain.application.ports import ManagedNoteWriter, VaultReader
from second_brain.application.reports import Diagnostic, DiagnosticSeverity, ScanReport
from second_brain.application.validation import build_report
from second_brain.application.writes import (
    CreateManagedNoteRequest,
    CreateManagedNoteResult,
    CreateNotePlan,
    CreateStatus,
    WriteReceipt,
    WriteSafetyError,
)
from second_brain.domain.models import NoteType


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

        pre_report = build_report(self.reader.scan())
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
                apply_requested=request.apply,
            )

        if request.note_type is NoteType.NOTE:
            return CreateManagedNoteResult(
                CreateStatus.REJECTED,
                diagnostics=(
                    _diagnostic(
                        "CREATE_UNSUPPORTED_TYPE",
                        "only project, area, resource and zettel can be created in v1",
                    ),
                ),
                apply_requested=request.apply,
            )

        now = (request.now or datetime.now(UTC).astimezone()).replace(microsecond=0)
        if now.tzinfo is None or now.utcoffset() is None:
            return CreateManagedNoteResult(
                CreateStatus.REJECTED,
                diagnostics=(
                    _diagnostic("CREATE_INVALID_TIMESTAMP", "created needs an UTC offset"),
                ),
                apply_requested=request.apply,
            )
        note_id = uuid.uuid7()
        try:
            plan = self.writer.prepare(
                pre_report.manifest,
                request.note_type,
                request.title,
                note_id,
                now,
            )
        except WriteSafetyError as exc:
            return CreateManagedNoteResult(
                CreateStatus.REJECTED,
                diagnostics=(_diagnostic(exc.code, str(exc), exc.path),),
                apply_requested=request.apply,
            )
        except (OSError, ValueError) as exc:
            return CreateManagedNoteResult(
                CreateStatus.REJECTED,
                diagnostics=(_diagnostic("CREATE_PLAN_FAILED", str(exc)),),
                apply_requested=request.apply,
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
                apply_requested=request.apply,
            )

        if not request.apply:
            return CreateManagedNoteResult(CreateStatus.DRY_RUN, plan=plan, apply_requested=False)

        try:
            receipt = self.writer.write(plan)
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
            post_report = build_report(self.reader.scan())
        except (OSError, UnicodeError, ValueError) as exc:
            diagnostics = [
                _diagnostic(
                    "CREATE_POST_WRITE_VALIDATION_FAILED",
                    f"post-write validation could not be completed: {exc}",
                    plan.relative_path,
                )
            ]
            rollback_succeeded = _safe_rollback(self.writer, receipt)
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
        if not _post_write_is_valid(post_report, plan):
            diagnostics = list(post_report.diagnostics)
            diagnostics.append(
                _diagnostic(
                    "CREATE_POST_WRITE_VALIDATION_FAILED",
                    "created note did not pass post-write validation",
                    plan.relative_path,
                )
            )
            rollback_succeeded = _safe_rollback(self.writer, receipt)
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

    def rollback(self, receipt: WriteReceipt) -> bool:
        """Безопасно откатить созданную note через тот же writer receipt."""

        return _safe_rollback(self.writer, receipt)


def _post_write_is_valid(report: ScanReport, plan: CreateNotePlan) -> bool:
    """Проверить именно созданную note и отсутствие новых validation errors."""

    if report.error_count:
        return False
    matches = [note for note in report.notes if note.relative_path == plan.relative_path]
    if len(matches) != 1:
        return False
    note = matches[0]
    return (
        note.managed
        and note.note_id == plan.note_id
        and note.note_type is plan.note_type
        and note.created == plan.created
    )


def _diagnostic(code: str, message: str, path: str | None = None) -> Diagnostic:
    """Создать application diagnostic без привязки к adapter."""

    return Diagnostic(code, message, DiagnosticSeverity.ERROR, path)


def _safe_rollback(writer: ManagedNoteWriter, receipt: WriteReceipt) -> bool:
    """Считать исключение rollback безопасным отказом, а не поводом удалить вслепую."""

    try:
        return writer.rollback(receipt)
    except OSError, ValueError:
        return False
