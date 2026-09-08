"""Low-level bounded scan DTOs for Retrospective Calibration v1.

This module intentionally has no validation, Self Model, or adapter imports.
The filesystem adapter can therefore use these DTOs without creating an
application import cycle while the calibration use case keeps the normal
unbounded ``VaultReader.scan`` boundary unavailable to itself.
"""

from __future__ import annotations

from dataclasses import dataclass

from second_brain.application.reports import VaultSnapshot


@dataclass(frozen=True, slots=True)
class RetrospectiveCalibrationScanLimitsV1:
    """Pre-materialization resource limits owned by the v1 contract."""

    max_scan_entries: int = 16_384
    max_scan_documents: int = 4_096
    max_scan_bytes: int = 16_777_216


@dataclass(frozen=True, slots=True)
class RetrospectiveCalibrationScanV1:
    """The result of one bounded scan, including boundary counters."""

    snapshot: VaultSnapshot
    entries_inspected: int
    documents_materialized: int
    raw_utf8_bytes: int
    limit_exceeded: bool = False


__all__ = [
    "RetrospectiveCalibrationScanLimitsV1",
    "RetrospectiveCalibrationScanV1",
]
