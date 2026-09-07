"""Bounded, read-only diagnostics for the vault and derived read models."""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Final

from second_brain.adapters.search import SqliteFts5SearchIndex
from second_brain.application.personal_memory import is_personal_memory_enrolled
from second_brain.application.ports import VaultReader
from second_brain.application.reports import (
    Diagnostic,
    DiagnosticSeverity,
    ScanReport,
    VaultSnapshot,
    diagnostic_affects_content,
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

_CONTENT_ROOT_AVAILABILITY_CODES: Final[frozenset[str]] = frozenset(
    {
        "VAULT_DIRECTORY_READ_ERROR",
        "VAULT_ENTRY_RESOLVE_ERROR",
        "VAULT_LINKED_DIRECTORY",
        "VAULT_OVERLAPPING_ROOTS",
        "VAULT_PATH_ESCAPE",
        "VAULT_ROOT_MISSING",
        "VAULT_ROOT_NOT_DIRECTORY",
    }
)
_SAFE_DIAGNOSTIC_CODE: Final[re.Pattern[str]] = re.compile(r"[A-Z][A-Z0-9_]{0,63}\Z")


class DoctorStatus(StrEnum):
    """Bounded status values for the complete report and each derived layer."""

    HEALTHY = "healthy"
    DEGRADED = "degraded"
    UNAVAILABLE = "unavailable"


@dataclass(frozen=True, slots=True)
class DoctorLayerStatus:
    """Safe status of one derived read model."""

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
    """One grouped safe diagnostic code without messages, paths or content."""

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
    """Complete bounded doctor result with no private vault projection."""

    status: DoctorStatus
    generated_at: datetime
    config_resolvable: bool
    manifest_available: bool
    content_roots_available: bool
    attachments_scan_complete: bool
    manifest_schema_version: int | None
    managed_note_count: int | None
    enrolled_personal_memory_count: int | None
    valid_decision_count: int | None
    valid_outcome_count: int | None
    attachment_bytes: int | None
    attachment_count: int | None
    timeline: DoctorLayerStatus
    self_model: DoctorLayerStatus
    self_retrieval: DoctorLayerStatus
    diagnostics: tuple[DoctorDiagnosticCount, ...]

    @property
    def attachment_total(self) -> int | None:
        """Compatibility alias for the total size of a complete attachment scan."""

        return self.attachment_bytes

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
        """Return deterministic CLI status: 0 healthy, 1 degraded, 2 unavailable."""

        if self.status is DoctorStatus.UNAVAILABLE:
            return 2
        return 0 if self.status is DoctorStatus.HEALTHY else 1

    def as_dict(self) -> dict[str, Any]:
        """Return JSON-safe status, counts and diagnostic code frequencies only."""

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
                "attachments_scan_complete": self.attachments_scan_complete,
            },
            "manifest": {
                "available": self.manifest_available,
                "schema_version": self.manifest_schema_version,
            },
            "counts": counts,
            "notes": self.managed_note_count,
            "enrolled_personal_memory": self.enrolled_personal_memory_count,
            "valid_decision_journals": self.valid_decision_count,
            "valid_outcome_observations": self.valid_outcome_count,
            "attachments": {
                "scan_complete": self.attachments_scan_complete,
                "count": self.attachment_count,
                "total_bytes": self.attachment_bytes,
            },
            "attachment_total": self.attachment_bytes,
            "attachment_bytes": self.attachment_bytes,
            "timeline": self.timeline.as_dict(),
            "self_model": self.self_model.as_dict(),
            "self_retrieval": self.self_retrieval.as_dict(),
            "errors": self.error_count,
            "warnings": self.warning_count,
            "diagnostics": [item.as_dict() for item in self.diagnostics],
            "exit_code": self.exit_code,
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

        safe_code = _safe_diagnostic_code(code)
        diagnostics = (DoctorDiagnosticCount(safe_code, DiagnosticSeverity.ERROR, 1),)
        return cls(
            status=DoctorStatus.UNAVAILABLE,
            generated_at=generated_at,
            config_resolvable=config_resolvable,
            manifest_available=False,
            content_roots_available=False,
            attachments_scan_complete=False,
            manifest_schema_version=None,
            managed_note_count=None,
            enrolled_personal_memory_count=None,
            valid_decision_count=None,
            valid_outcome_count=None,
            attachment_bytes=None,
            attachment_count=None,
            timeline=DoctorLayerStatus(DoctorStatus.UNAVAILABLE, True, safe_code),
            self_model=DoctorLayerStatus(DoctorStatus.UNAVAILABLE, True, safe_code),
            self_retrieval=DoctorLayerStatus(DoctorStatus.UNAVAILABLE, False, safe_code),
            diagnostics=diagnostics,
        )


@dataclass(frozen=True, slots=True)
class _SnapshotReader:
    """Replay exactly the immutable snapshot captured for one doctor run."""

    snapshot: VaultSnapshot

    def scan(self) -> VaultSnapshot:
        """Return the captured snapshot without touching the filesystem."""

        return self.snapshot


@dataclass(frozen=True, slots=True)
class BuildDoctorReport:
    """Build a bounded report from one configured read-only vault reader."""

    reader: VaultReader | None
    config_resolvable: bool
    clock: DiagnosticsClock = lambda: datetime.now(UTC)

    def execute(self) -> DoctorReport:
        """Return a complete report or a safe unavailable result."""

        generated_at = _read_clock(self.clock)
        if not self.config_resolvable or self.reader is None:
            return DoctorReport.unavailable(
                generated_at=generated_at,
                config_resolvable=self.config_resolvable,
                code="CONFIG_UNAVAILABLE",
            )

        try:
            snapshot = self.reader.scan()
            if type(snapshot) is not VaultSnapshot:
                raise TypeError
            report = build_report(snapshot)
            if type(report) is not ScanReport:
                raise TypeError
            return _build_report(report, snapshot=snapshot, generated_at=generated_at)
        except Exception:
            return DoctorReport.unavailable(
                generated_at=generated_at,
                config_resolvable=True,
                code="DOCTOR_SCAN_UNAVAILABLE",
            )


def _build_report(
    report: ScanReport,
    *,
    snapshot: VaultSnapshot,
    generated_at: datetime,
) -> DoctorReport:
    """Project one full internal report into safe counts and layer statuses."""

    manifest_available = report.manifest is not None
    content_roots_available = _content_roots_available(report)
    content_counts_available = manifest_available and report.content_scan_complete
    attachments_scan_complete = report.attachments_scan_complete
    diagnostics = _group_diagnostics(report.diagnostics)

    # These readers all replay the same snapshot.  They may perform their
    # ordinary in-memory validation/composition, but they cannot rescan the
    # configured vault or observe a second filesystem state.
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
    elif report.error_count:
        status = DoctorStatus.DEGRADED
    if timeline.status is DoctorStatus.UNAVAILABLE or self_model.status is DoctorStatus.UNAVAILABLE:
        status = DoctorStatus.UNAVAILABLE
    elif (
        timeline.status is not DoctorStatus.HEALTHY or self_model.status is not DoctorStatus.HEALTHY
    ):
        status = DoctorStatus.DEGRADED
    elif self_retrieval.status is not DoctorStatus.HEALTHY:
        # Stage 5 is optional for the aggregate readiness contract, but its
        # actual unavailable state is still a meaningful degraded result.
        status = DoctorStatus.DEGRADED

    managed_notes = tuple(note for note in report.notes if note.managed is True)
    return DoctorReport(
        status=status,
        generated_at=generated_at,
        config_resolvable=True,
        manifest_available=manifest_available,
        content_roots_available=content_roots_available,
        attachments_scan_complete=attachments_scan_complete,
        manifest_schema_version=report.manifest.schema_version if report.manifest else None,
        managed_note_count=len(managed_notes) if content_counts_available else None,
        enrolled_personal_memory_count=(
            sum(is_personal_memory_enrolled(note.front_matter) for note in managed_notes)
            if content_counts_available
            else None
        ),
        valid_decision_count=len(report.decision_journals) if content_counts_available else None,
        valid_outcome_count=len(report.outcome_observations) if content_counts_available else None,
        attachment_bytes=report.attachment_bytes if attachments_scan_complete else None,
        attachment_count=len(report.attachments) if attachments_scan_complete else None,
        timeline=timeline,
        self_model=self_model,
        self_retrieval=self_retrieval,
        diagnostics=diagnostics,
    )


def _build_timeline_status(reader: VaultReader, *, generated_at: datetime) -> DoctorLayerStatus:
    """Check Personal Timeline without exporting its private projection."""

    try:
        BuildPersonalTimeline(reader, clock=lambda: generated_at).execute(PersonalTimelineRequest())
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
    """Check the approved Self Model build without exporting claims or bodies."""

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
    """Exercise the merged Stage 5 core against the same snapshot."""

    index: SqliteFts5SearchIndex | None = None
    try:
        index = SqliteFts5SearchIndex()
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
        if index is not None:
            with suppress(Exception):
                index.close()
    return DoctorLayerStatus(DoctorStatus.HEALTHY, False)


def _content_roots_available(report: ScanReport) -> bool:
    """Classify declared content-root availability from typed provenance only."""

    if report.manifest is None:
        return False
    return not any(
        item.severity is DiagnosticSeverity.ERROR
        and item.code in _CONTENT_ROOT_AVAILABILITY_CODES
        and diagnostic_affects_content(item)
        for item in report.diagnostics
    )


def _group_diagnostics(
    diagnostics: tuple[Diagnostic, ...],
) -> tuple[DoctorDiagnosticCount, ...]:
    """Group only bounded code/severity pairs and discard all detail fields."""

    counts: Counter[tuple[str, DiagnosticSeverity]] = Counter()
    for diagnostic in diagnostics:
        code = _safe_diagnostic_code(diagnostic.code)
        severity = (
            diagnostic.severity
            if type(diagnostic.severity) is DiagnosticSeverity
            else DiagnosticSeverity.ERROR
        )
        counts[(code, severity)] += 1
    return tuple(
        DoctorDiagnosticCount(code, severity, count)
        for (code, severity), count in sorted(
            counts.items(), key=lambda item: (item[0][1].value, item[0][0])
        )
    )


def _safe_diagnostic_code(value: object) -> str:
    """Keep diagnostic output bounded even for a malformed injected snapshot."""

    if type(value) is str and _SAFE_DIAGNOSTIC_CODE.fullmatch(value) is not None:
        return value
    return "UNSAFE_DIAGNOSTIC"


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
