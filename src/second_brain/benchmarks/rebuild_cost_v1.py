"""Synthetic rebuild-cost evidence for the derived read models.

The harness measures on-demand Timeline, Self Model, and Self Retrieval
operations for fixed temporary-vault sizes. Wall-clock and traced-memory runs
are intentionally separate; timings are evidence, not an SLO. The command
never reads process configuration or a user vault.
"""

from __future__ import annotations

import argparse
import gc
import json
import stat
import statistics
import time
import tracemalloc
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Final, Literal
from uuid import UUID

from second_brain.adapters.search import SqliteFts5SearchIndex
from second_brain.adapters.vault import FileSystemVaultReader
from second_brain.application.self_model import (
    DEFAULT_SELF_MODEL_POLICY,
    BuildSelfModel,
    SelfModelRequest,
)
from second_brain.application.self_retrieval import BuildSelfContext, SelfContextRequest
from second_brain.application.timeline import (
    BuildPersonalTimeline,
    PersonalTimelineRequest,
)

BENCHMARK_VERSION: Final[str] = "rebuild-cost-v1"
REPORT_SCHEMA_VERSION: Final[int] = 1
TIMING_SAMPLE_COUNT: Final[int] = 3
_FIXED_NOW: Final[datetime] = datetime(2026, 9, 6, 12, 0, tzinfo=UTC)
_VAULT_ID: Final[str] = "0198f4c5-6a00-7000-8000-000000000001"
_REPARSE_POINT_ATTRIBUTE: Final[int] = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
_OperationName = Literal["timeline", "self_model", "self_retrieval"]


@dataclass(frozen=True, slots=True)
class FixtureSize:
    """One deterministic synthetic corpus size."""

    name: str
    note_count: int


@dataclass(frozen=True, slots=True)
class OperationMeasurement:
    """One measured on-demand rebuild with shape-only output evidence."""

    fixture: str
    note_count: int
    operation: _OperationName
    wall_clock_ms: float
    peak_traced_bytes: int
    output: dict[str, int]


@dataclass(frozen=True, slots=True)
class RebuildCostReport:
    """Complete report with fixed fixture order and no local paths."""

    benchmark_version: str
    report_schema_version: int
    fixtures: tuple[FixtureSize, ...]
    measurements: tuple[OperationMeasurement, ...]


FIXTURE_SIZES: Final[tuple[FixtureSize, ...]] = (
    FixtureSize("small", 4),
    FixtureSize("medium", 12),
    FixtureSize("large", 24),
)


def run_benchmark() -> RebuildCostReport:
    """Measure each derived read model against every synthetic fixture size."""

    measurements: list[OperationMeasurement] = []
    with TemporaryDirectory(prefix="second-brain-rebuild-cost-v1-") as temporary_root:
        root = Path(temporary_root)
        for fixture in FIXTURE_SIZES:
            fixture_root = root / fixture.name
            _create_synthetic_vault(fixture_root, fixture.note_count)
            measurements.extend(_measure_fixture(fixture, fixture_root))
    return RebuildCostReport(
        benchmark_version=BENCHMARK_VERSION,
        report_schema_version=REPORT_SCHEMA_VERSION,
        fixtures=FIXTURE_SIZES,
        measurements=tuple(measurements),
    )


def report_as_dict(report: RebuildCostReport) -> dict[str, object]:
    """Serialize stable report metadata and measured evidence."""

    return {
        "benchmark_version": report.benchmark_version,
        "report_schema_version": report.report_schema_version,
        "synthetic_only": True,
        "performance_thresholds": [],
        "timing": {
            "warmup_runs": 1,
            "samples": TIMING_SAMPLE_COUNT,
            "statistic": "median",
        },
        "fixtures": [
            {"name": fixture.name, "note_count": fixture.note_count} for fixture in report.fixtures
        ],
        "measurements": [
            {
                "fixture": measurement.fixture,
                "note_count": measurement.note_count,
                "operation": measurement.operation,
                "wall_clock_ms": measurement.wall_clock_ms,
                "peak_traced_bytes": measurement.peak_traced_bytes,
                "output": measurement.output,
            }
            for measurement in report.measurements
        ],
    }


def render_markdown(report: RebuildCostReport) -> str:
    """Render an on-demand Markdown artifact without timing thresholds."""

    lines = [
        f"# Derived Read-Model Rebuild Cost {report.benchmark_version}",
        "",
        "Synthetic temporary-vault evidence only. Wall-clock and traced-memory "
        "values are environment measurements, not hard SLOs.",
        "No performance threshold is applied by this benchmark.",
        "",
        "| Fixture | Notes | Operation | Wall-clock ms | Peak traced bytes | Output |",
        "| --- | ---: | --- | ---: | ---: | --- |",
    ]
    for measurement in report.measurements:
        output = json.dumps(measurement.output, ensure_ascii=False, sort_keys=True)
        lines.append(
            f"| {measurement.fixture} | {measurement.note_count} | "
            f"{measurement.operation} | {measurement.wall_clock_ms} | "
            f"{measurement.peak_traced_bytes} | `{output}` |"
        )
    return "\n".join(lines) + "\n"


def main(argv: Sequence[str] | None = None) -> int:
    """Run the benchmark as JSON or a Markdown artifact."""

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--format",
        choices=("json", "markdown"),
        default="markdown",
        help="report format; markdown is the default",
    )
    parser.add_argument(
        "--output",
        type=Path,
        help="optional artifact path; stdout is used when omitted",
    )
    args = parser.parse_args(argv)
    if args.output is not None:
        _validate_artifact_output(args.output)
    report = run_benchmark()
    if args.format == "json":
        rendered = json.dumps(report_as_dict(report), ensure_ascii=False, indent=2) + "\n"
    else:
        rendered = render_markdown(report)
    if args.output is None:
        print(rendered, end="")
    else:
        _write_artifact(args.output, rendered)
    return 0


def _measure_fixture(
    fixture: FixtureSize,
    root: Path,
) -> tuple[OperationMeasurement, ...]:
    return (
        _measure_operation(fixture, "timeline", lambda: _run_timeline(root)),
        _measure_operation(fixture, "self_model", lambda: _run_self_model(root)),
        _measure_operation(
            fixture,
            "self_retrieval",
            lambda: _run_self_retrieval(root, fixture.note_count),
        ),
    )


def _measure_operation(
    fixture: FixtureSize,
    operation: _OperationName,
    action: Callable[[], dict[str, int]],
) -> OperationMeasurement:
    gc.collect()
    warmup_output = action()
    timings: list[float] = []
    for _ in range(TIMING_SAMPLE_COUNT):
        started = time.perf_counter()
        output = action()
        timings.append(time.perf_counter() - started)
        if output != warmup_output:
            raise RuntimeError("synthetic benchmark operation changed during timing samples")

    gc.collect()
    tracemalloc.start()
    try:
        traced_output = action()
    finally:
        _, peak_traced_bytes = tracemalloc.get_traced_memory()
        tracemalloc.stop()
    if traced_output != warmup_output:
        raise RuntimeError("synthetic benchmark operation changed between measurement runs")
    return OperationMeasurement(
        fixture=fixture.name,
        note_count=fixture.note_count,
        operation=operation,
        wall_clock_ms=round(max(0.0, statistics.median(timings)) * 1000, 3),
        peak_traced_bytes=peak_traced_bytes,
        output=warmup_output,
    )


def _validate_artifact_output(path: Path) -> None:
    """Reject overwrite, link-like paths, and managed-vault ancestors."""

    current = path
    while True:
        try:
            current.lstat()
        except FileNotFoundError:
            exists = False
        except OSError:
            raise ValueError("benchmark artifact output path is unavailable") from None
        else:
            exists = True

        if exists:
            if _is_link_like(current):
                raise ValueError("benchmark artifact output path contains a link-like entry")
            if current == path:
                raise ValueError("benchmark artifact output already exists")
            if not current.is_dir():
                raise ValueError("benchmark artifact output parent is not a directory")
            try:
                manifest_exists = (current / "second-brain.yaml").lstat()
            except FileNotFoundError:
                manifest_exists = None
            except OSError:
                raise ValueError("benchmark artifact output path is unavailable") from None
            if manifest_exists is not None:
                raise ValueError("benchmark artifact output cannot be inside a managed vault")

        parent = current.parent
        if parent == current:
            return
        current = parent


def _write_artifact(path: Path, rendered: str) -> None:
    """Create one new artifact without following a checked path or overwriting."""

    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        _validate_artifact_output(path)
        with path.open("x", encoding="utf-8", newline="") as stream:
            stream.write(rendered)
    except FileExistsError:
        raise ValueError("benchmark artifact output already exists") from None
    except OSError:
        raise ValueError("benchmark artifact output is unavailable") from None


def _is_link_like(path: Path) -> bool:
    """Detect symlink, junction, or Windows reparse entry before writing."""

    try:
        if path.is_symlink():
            return True
        is_junction = getattr(path, "is_junction", None)
        if callable(is_junction) and is_junction():
            return True
        attributes = getattr(path.lstat(), "st_file_attributes", 0)
        return bool(_REPARSE_POINT_ATTRIBUTE and attributes & _REPARSE_POINT_ATTRIBUTE)
    except FileNotFoundError:
        return False
    except OSError, RuntimeError:
        return True


def _run_timeline(root: Path) -> dict[str, int]:
    result = BuildPersonalTimeline(
        FileSystemVaultReader(root),
        clock=lambda: _FIXED_NOW,
    ).execute(PersonalTimelineRequest(known_limit=200, unknown_limit=200))
    return {
        "known_total": result.known_total,
        "unknown_total": result.unknown_total,
    }


def _run_self_model(root: Path) -> dict[str, int]:
    result = BuildSelfModel(
        FileSystemVaultReader(root),
        policy=DEFAULT_SELF_MODEL_POLICY,
        clock=lambda: _FIXED_NOW,
    ).execute(SelfModelRequest())
    return {
        "claim_count": len(result.claims),
        "eligible_evidence_count": result.eligible_evidence_count,
        "represented_evidence_count": result.represented_evidence_count,
    }


def _run_self_retrieval(root: Path, note_count: int) -> dict[str, int]:
    index = SqliteFts5SearchIndex()
    try:
        result = BuildSelfContext(
            FileSystemVaultReader(root),
            index,
            clock=lambda: _FIXED_NOW,
        ).execute(
            SelfContextRequest(
                query="benchmark context",
                limit=min(20, note_count),
            )
        )
    finally:
        index.close()
    return {
        "candidate_count": result.candidate_count,
        "included_count": result.included_count,
        "excluded_count": result.excluded_count,
        "content_bytes": result.content_bytes,
    }


def _create_synthetic_vault(root: Path, note_count: int) -> None:
    for directory in (
        "00 Inbox",
        "10 Projects",
        "20 Areas",
        "30 Resources",
        "40 Zettelkasten",
        "90 Archive",
        "_templates",
        "_attachments",
    ):
        (root / directory).mkdir(parents=True, exist_ok=True)
    (root / "second-brain.yaml").write_text(
        f"""schema_version: 1
vault_id: {_VAULT_ID}
default_language: en
paths:
  inbox: 00 Inbox
  projects: 10 Projects
  areas: 20 Areas
  resources: 30 Resources
  zettelkasten: 40 Zettelkasten
  archive: 90 Archive
  templates: _templates
  attachments: _attachments
attachments:
  warning_size_bytes: 10485760
  max_size_bytes: 52428800
""",
        encoding="utf-8",
    )
    for index in range(1, note_count + 1):
        note_id = UUID(f"0198f4c5-6a00-7000-8000-000000000{index:03d}")
        (root / f"10 Projects/Benchmark note {index:03d}.md").write_text(
            f"""---
id: {note_id}
type: zettel
created: 2026-09-01T12:00:00+00:00
tags:
  - benchmark
second_brain_personal_memory: 1
evidence_kind: user_statement
self_kind: preference
evidence_at: "2026-09-01T12:00:00+00:00"
evidence_at_precision: exact
domain: benchmark
---
# Benchmark note {index:03d}

benchmark context item {index:03d} for derived read-model measurement.
""",
            encoding="utf-8",
        )


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "BENCHMARK_VERSION",
    "FIXTURE_SIZES",
    "RebuildCostReport",
    "main",
    "render_markdown",
    "report_as_dict",
    "run_benchmark",
]
