"""Bounded, read-only doctor report без private vault content."""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import PurePosixPath
from typing import Any, Final

from second_brain.adapters.search import SqliteFts5SearchIndex
from second_brain.application.personal_memory import is_personal_memory_enrolled
from second_brain.application.ports import VaultReader
from second_brain.application.reports import (
    Diagnostic,
    DiagnosticSeverity,
    ScanReport,
    VaultSnapshot,
)
from second_brain.application.self_model import (
    DEFAULT_SELF_MODEL_POLICY,
    BuildSelfModel,
    SelfModelError,
    SelfModelErrorCode,
    SelfModelRequest,
)
from second_brain.application.self_retrieval import (
    BuildSelfContext,
    SelfContextRequest,
    SelfRetrievalError,
)
from second_brain.application.timeline import (
    BuildPersonalTimeline,
    PersonalTimelineRequest,
    TimelineError,
    TimelineErrorCode,
)
from second_brain.application.validation import build_report

type DiagnosticsClock = Callable[[], datetime]

_CONTENT_ROOT_FAILURE_CODES: Final[frozenset[str]] = frozenset(
    {
        "VAULT_DIRECTORY_READ_ERROR",
        "VAULT_ENTRY_RESOLVE_ERROR",
        "VAULT_LINKED_DIRECTORY",
        "VAULT_OVERLAPPING_ROOTS",
        "VAULT_PATH_ESCAPE",
    }
)
_CONTENT_ROOT_FIELDS: Final[tuple[str, ...]] = (
    "inbox",
    "projects",
    "areas",
    "resources",
    "zettelkasten",
    "archive",
)
_INCOMPLETE_COUNT_CODES: Final[frozenset[str]] = frozenset({"NOTE_READ_ERROR"})
_ATTACHMENT_SCAN_FAILURE_CODES: Final[frozenset[str]] = frozenset(
    {
        "ATTACHMENT_DIRECTORY_READ_ERROR",
        "ATTACHMENT_STAT_ERROR",
        "VAULT_DIRECTORY_READ_ERROR",
        "VAULT_ENTRY_RESOLVE_ERROR",
        "VAULT_LINKED_DIRECTORY",
        "VAULT_PATH_ESCAPE",
        "VAULT_ROOT_MISSING",
    }
)
_ALL_MANIFEST_ROOT_FIELDS: Final[tuple[str, ...]] = (
    *_CONTENT_ROOT_FIELDS,
    "templates",
    "attachments",
)


class DoctorStatus(StrEnum):
    """Bounded status values for the complete report and derived layers."""

    HEALTHY = "healthy"
    DEGRADED = "degraded"
    UNAVAILABLE = "unavailable"


@dataclass(frozen=True, slots=True)
class DoctorLayerStatus:
    """Safe status of one required or optional derived layer."""

    status: DoctorStatus
    required: bool
    code: str | None = None

    def as_dict(self) -> dict[str, Any]:
        """Return only stable status fields and a bounded diagnostic code."""

        return {
            "status": self.status.value,
            "required": self.required,
            "code": self.code,
        }


@dataclass(frozen=True, slots=True)
class DoctorDiagnosticCount:
    """One grouped diagnostic code without message, path or raw content."""

    code: str
    severity: DiagnosticSeverity
    count: int

    def as_dict(self) -> dict[str, Any]:
        """Return a safe machine-readable diagnostic count."""

        return {
            "code": self.code,
            "severity": self.severity.value,
            "count": self.count,
        }


@dataclass(frozen=True, slots=True)
class DoctorReport:
    """Complete bounded doctor result with no private content projection."""

    status: DoctorStatus
    generated_at: datetime
    config_resolvable: bool
    manifest_available: bool
    content_roots_available: bool
    manifest_schema_version: int | None
    managed_note_count: int | None
    enrolled_personal_memory_count: int | None
    valid_decision_count: int | None
    valid_outcome_count: int | None
    attachment_bytes: int | None
    timeline: DoctorLayerStatus
    self_model: DoctorLayerStatus
    self_retrieval: DoctorLayerStatus
    diagnostics: tuple[DoctorDiagnosticCount, ...]

    @property
    def error_count(self) -> int:
        """Return the grouped number of error diagnostics."""

        return sum(
            item.count for item in self.diagnostics if item.severity is DiagnosticSeverity.ERROR
        )

    @property
    def warning_count(self) -> int:
        """Return the grouped number of warning diagnostics."""

        return sum(
            item.count for item in self.diagnostics if item.severity is DiagnosticSeverity.WARNING
        )

    @property
    def exit_code(self) -> int:
        """Return deterministic CLI exit code: 0 healthy, 1 degraded, 2 unavailable."""

        if self.status is DoctorStatus.UNAVAILABLE:
            return 2
        return 0 if self.status is DoctorStatus.HEALTHY else 1

    def as_dict(self) -> dict[str, Any]:
        """Return JSON without absolute paths, bodies, URLs, secrets or raw YAML."""

        counts = {
            "managed_notes": self.managed_note_count,
            "enrolled_personal_memory": self.enrolled_personal_memory_count,
            "valid_decision_journals": self.valid_decision_count,
            "valid_outcome_observations": self.valid_outcome_count,
        }
        return {
            "status": self.status.value,
            "generated_at": self.generated_at.isoformat(),
            "config": {"resolvable": self.config_resolvable},
            "vault": {
                "manifest_available": self.manifest_available,
                "content_roots_available": self.content_roots_available,
            },
            "manifest": {
                "available": self.manifest_available,
                "schema_version": self.manifest_schema_version,
            },
            "attachment_bytes": self.attachment_bytes,
            "counts": counts,
            # Keep these compact aliases for the existing doctor CLI contract.
            "notes": self.managed_note_count,
            "enrolled_personal_memory": self.enrolled_personal_memory_count,
            "valid_decision_journals": self.valid_decision_count,
            "valid_outcome_observations": self.valid_outcome_count,
            "timeline": self.timeline.as_dict(),
            "self_model": self.self_model.as_dict(),
            "self_retrieval": self.self_retrieval.as_dict(),
            "errors": self.error_count,
            "warnings": self.warning_count,
            "diagnostics": [item.as_dict() for item in self.diagnostics],
        }

    @classmethod
    def unavailable(
        cls,
        *,
        generated_at: datetime,
        config_resolvable: bool,
        code: str,
    ) -> DoctorReport:
        """Create a safe unavailable report without preserving failure details."""

        diagnostics = (DoctorDiagnosticCount(code, DiagnosticSeverity.ERROR, 1),)
        return cls(
            status=DoctorStatus.UNAVAILABLE,
            generated_at=generated_at,
            config_resolvable=config_resolvable,
            manifest_available=False,
            content_roots_available=False,
            manifest_schema_version=None,
            managed_note_count=None,
            enrolled_personal_memory_count=None,
            valid_decision_count=None,
            valid_outcome_count=None,
            attachment_bytes=None,
            timeline=DoctorLayerStatus(DoctorStatus.UNAVAILABLE, True, code),
            self_model=DoctorLayerStatus(DoctorStatus.UNAVAILABLE, True, code),
            self_retrieval=DoctorLayerStatus(
                DoctorStatus.UNAVAILABLE,
                False,
                code,
            ),
            diagnostics=diagnostics,
        )


@dataclass(frozen=True, slots=True)
class _SnapshotReader:
    """Replay the exact initial snapshot to derived read models."""

    snapshot: VaultSnapshot

    def scan(self) -> VaultSnapshot:
        """Return the immutable snapshot captured for this doctor run."""

        return self.snapshot


@dataclass(frozen=True, slots=True)
class BuildDoctorReport:
    """Build a safe doctor report from one configured read-only reader."""

    reader: VaultReader | None
    config_resolvable: bool
    clock: DiagnosticsClock = lambda: datetime.now(UTC)

    def execute(self) -> DoctorReport:
        """Return a complete bounded report or a safe unavailable result."""

        generated_at = _read_clock(self.clock)
        if not self.config_resolvable or self.reader is None:
            return DoctorReport.unavailable(
                generated_at=generated_at,
                config_resolvable=self.config_resolvable,
                code="CONFIG_UNAVAILABLE",
            )

        try:
            snapshot = self.reader.scan()
            report = build_report(snapshot)
        except Exception:
            return DoctorReport.unavailable(
                generated_at=generated_at,
                config_resolvable=True,
                code="DOCTOR_SCAN_UNAVAILABLE",
            )
        return _build_report(report, snapshot=snapshot, generated_at=generated_at)


def _build_report(
    report: ScanReport,
    *,
    snapshot: VaultSnapshot,
    generated_at: datetime,
) -> DoctorReport:
    """Project one full internal report into safe counts and layer statuses."""

    manifest_available = report.manifest is not None
    content_roots_available = _content_roots_available(report)
    diagnostics = _group_diagnostics(report.diagnostics)
    snapshot_reader = _SnapshotReader(snapshot)
    timeline = _build_timeline_status(snapshot_reader, generated_at=generated_at)
    self_model = _build_self_model_status(snapshot_reader, generated_at=generated_at)
    self_retrieval = _build_self_retrieval_status(
        snapshot_reader,
        generated_at=generated_at,
    )

    status = DoctorStatus.HEALTHY
    if not manifest_available or not content_roots_available:
        status = DoctorStatus.UNAVAILABLE
    elif report.error_count or timeline.status is not DoctorStatus.HEALTHY:
        status = DoctorStatus.DEGRADED
    if self_model.status is DoctorStatus.UNAVAILABLE:
        status = DoctorStatus.UNAVAILABLE
    elif self_model.status is not DoctorStatus.HEALTHY:
        status = DoctorStatus.DEGRADED

    managed_notes = tuple(note for note in report.notes if note.managed is True)
    counts_available = (
        manifest_available
        and content_roots_available
        and not any(
            item.severity is DiagnosticSeverity.ERROR and item.code in _INCOMPLETE_COUNT_CODES
            for item in report.diagnostics
        )
    )
    return DoctorReport(
        status=status,
        generated_at=generated_at,
        config_resolvable=True,
        manifest_available=manifest_available,
        content_roots_available=content_roots_available,
        manifest_schema_version=report.manifest.schema_version if report.manifest else None,
        managed_note_count=len(managed_notes) if counts_available else None,
        enrolled_personal_memory_count=(
            sum(is_personal_memory_enrolled(note.front_matter) for note in managed_notes)
            if counts_available
            else None
        ),
        valid_decision_count=len(report.decision_journals) if counts_available else None,
        valid_outcome_count=len(report.outcome_observations) if counts_available else None,
        attachment_bytes=(report.attachment_bytes if _attachment_bytes_available(report) else None),
        timeline=timeline,
        self_model=self_model,
        self_retrieval=self_retrieval,
        diagnostics=diagnostics,
    )


def _build_timeline_status(reader: VaultReader, *, generated_at: datetime) -> DoctorLayerStatus:
    """Check the Timeline build without exporting its content."""

    try:
        BuildPersonalTimeline(
            reader,
            clock=lambda: generated_at,
        ).execute(PersonalTimelineRequest())
    except TimelineError as exc:
        status = (
            DoctorStatus.UNAVAILABLE
            if exc.code == TimelineErrorCode.VAULT_UNAVAILABLE.value
            else DoctorStatus.DEGRADED
        )
        return DoctorLayerStatus(status, True, exc.code)
    except Exception:
        return DoctorLayerStatus(DoctorStatus.UNAVAILABLE, True, "TIMELINE_UNAVAILABLE")
    return DoctorLayerStatus(DoctorStatus.HEALTHY, True)


def _build_self_model_status(
    reader: VaultReader,
    *,
    generated_at: datetime,
) -> DoctorLayerStatus:
    """Check the Self Model build without exporting claims or bodies."""

    try:
        BuildSelfModel(
            reader,
            DEFAULT_SELF_MODEL_POLICY,
            clock=lambda: generated_at,
        ).execute(SelfModelRequest())
    except SelfModelError as exc:
        status = (
            DoctorStatus.UNAVAILABLE
            if exc.code == SelfModelErrorCode.VAULT_UNAVAILABLE.value
            else DoctorStatus.DEGRADED
        )
        return DoctorLayerStatus(status, True, exc.code)
    except Exception:
        return DoctorLayerStatus(DoctorStatus.UNAVAILABLE, True, "SELF_MODEL_UNAVAILABLE")
    return DoctorLayerStatus(DoctorStatus.HEALTHY, True)


def _build_self_retrieval_status(
    reader: VaultReader,
    *,
    generated_at: datetime,
) -> DoctorLayerStatus:
    """Exercise the merged Stage 5 core against the same initial snapshot."""

    index = SqliteFts5SearchIndex()
    try:
        BuildSelfContext(
            reader,
            index,
            clock=lambda: generated_at,
        ).execute(SelfContextRequest(query="doctor", limit=1))
    except SelfRetrievalError as exc:
        return DoctorLayerStatus(DoctorStatus.UNAVAILABLE, False, exc.code)
    except Exception:
        return DoctorLayerStatus(DoctorStatus.UNAVAILABLE, False, "SELF_RETRIEVAL_UNAVAILABLE")
    finally:
        index.close()
    return DoctorLayerStatus(DoctorStatus.HEALTHY, False)


def _attachment_bytes_available(report: ScanReport) -> bool:
    """Keep attachment totals unknown when the attachment tree was partial."""

    manifest = report.manifest
    if manifest is None:
        return False
    attachment_root = PurePosixPath(manifest.paths.attachments.as_posix())
    for item in report.diagnostics:
        if item.severity is not DiagnosticSeverity.ERROR:
            continue
        if item.code.startswith("ATTACHMENT_"):
            return False
        if item.code not in _ATTACHMENT_SCAN_FAILURE_CODES or item.path is None:
            continue
        if _is_path_under(PurePosixPath(item.path), attachment_root):
            return False
    return True


def _content_roots_available(report: ScanReport) -> bool:
    """Check root availability without exposing diagnostic paths."""

    if report.manifest is None:
        return False
    return not any(
        item.severity is DiagnosticSeverity.ERROR
        and (item.code.startswith("VAULT_ROOT_") or item.code in _CONTENT_ROOT_FAILURE_CODES)
        and _diagnostic_affects_content_scope(item, report)
        for item in report.diagnostics
    )


def _diagnostic_affects_content_scope(item: Diagnostic, report: ScanReport) -> bool:
    """Limit content-root health to declared content roots, not support roots."""

    if report.manifest is None:
        return True
    if item.code == "VAULT_OVERLAPPING_ROOTS" and item.path is None:
        return _manifest_has_content_root_overlap(report)
    if item.path is None:
        return True
    candidate = PurePosixPath(item.path)
    return any(
        _is_path_under(candidate, getattr(report.manifest.paths, field))
        for field in _CONTENT_ROOT_FIELDS
    )


def _manifest_has_content_root_overlap(report: ScanReport) -> bool:
    """Recognize overlap only when a declared content root participates."""

    manifest = report.manifest
    if manifest is None:
        return True
    paths = {
        field: PurePosixPath(getattr(manifest.paths, field).as_posix())
        for field in _ALL_MANIFEST_ROOT_FIELDS
    }
    for content_field in _CONTENT_ROOT_FIELDS:
        content_path = paths[content_field]
        for other_field, other_path in paths.items():
            if other_field == content_field:
                continue
            if _is_path_under(content_path, other_path) or _is_path_under(other_path, content_path):
                return True
    return False


def _is_path_under(candidate: PurePosixPath, root: PurePosixPath) -> bool:
    """Return whether one manifest-relative path is equal to or below another."""

    try:
        candidate.relative_to(root)
    except ValueError:
        return False
    return True


def _group_diagnostics(diagnostics: tuple[Diagnostic, ...]) -> tuple[DoctorDiagnosticCount, ...]:
    """Group only stable code/severity pairs and discard messages/paths."""

    counts: Counter[tuple[str, DiagnosticSeverity]] = Counter(
        (item.code, item.severity) for item in diagnostics
    )
    return tuple(
        DoctorDiagnosticCount(code, severity, count)
        for (code, severity), count in sorted(
            counts.items(), key=lambda item: (item[0][1], item[0][0])
        )
    )


def _read_clock(clock: object) -> datetime:
    """Return an aware generated time without exposing clock failures."""

    if callable(clock):
        try:
            value = clock()
        except Exception:
            value = None
        if (
            isinstance(value, datetime)
            and value.tzinfo is not None
            and value.utcoffset() is not None
        ):
            return value
    return datetime.now(UTC)


__all__ = [
    "BuildDoctorReport",
    "DoctorDiagnosticCount",
    "DoctorLayerStatus",
    "DoctorReport",
    "DoctorStatus",
]
