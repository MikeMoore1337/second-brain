"""Deterministic synthetic lexical-gap measurement for Search/Retrieval v1.

The benchmark deliberately creates its own temporary vault and never loads the
process configuration or a user vault. It measures existing lexical Search
and current-reread behavior; it does not set a product-quality threshold or
make an embeddings decision.
"""

from __future__ import annotations

import argparse
import json
from collections.abc import Sequence
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Final, Literal
from uuid import UUID

from second_brain.adapters.search import SqliteFts5SearchIndex
from second_brain.adapters.vault import FileSystemVaultReader
from second_brain.application.ports import SearchHit, SearchRequest
from second_brain.application.search import RetrieveManagedNote, SearchVault
from second_brain.application.self_model import DEFAULT_SELF_MODEL_POLICY, BuildSelfModel
from second_brain.application.self_retrieval import (
    BuildSelfContext,
    SelfContextExclusionReason,
    SelfContextRequest,
    SelfContextResult,
)

BENCHMARK_VERSION: Final[str] = "lexical-gap-v1"
REPORT_SCHEMA_VERSION: Final[int] = 1
_FIXED_NOW: Final[datetime] = datetime(2026, 9, 6, 12, 0, tzinfo=UTC)
_VAULT_ID: Final[str] = "0198f4c5-6a00-7000-8000-000000000001"

PROJECT_ALPHA_ID: Final[UUID] = UUID("0198f4c5-6a00-7000-8000-000000000010")
PROJECT_BETA_ID: Final[UUID] = UUID("0198f4c5-6a00-7000-8000-000000000011")
JOGGING_ID: Final[UUID] = UUID("0198f4c5-6a00-7000-8000-000000000012")
MUTABLE_ID: Final[UUID] = UUID("0198f4c5-6a00-7000-8000-000000000013")
DELETED_ID: Final[UUID] = UUID("0198f4c5-6a00-7000-8000-000000000014")
_MUTABLE_CURRENT_BODY: Final[str] = "mutable context current body.\n"

_Scenario = Literal["live", "stale_body", "deleted_candidate"]


@dataclass(frozen=True, slots=True)
class SyntheticNote:
    """One fixed synthetic managed note definition."""

    note_id: UUID
    relative_path: str
    body: str


@dataclass(frozen=True, slots=True)
class BenchmarkCase:
    """One versioned query and its human-defined expected current references."""

    case_id: str
    query: str
    expected_refs: tuple[UUID, ...]
    limit: int
    scenario: _Scenario


@dataclass(frozen=True, slots=True)
class RankedCandidate:
    """Search candidate identity and its deterministic one-based rank."""

    note_id: str
    rank: int


@dataclass(frozen=True, slots=True)
class CurrentRereadOutcome:
    """Outcome observed after the Search candidate crossed the current boundary."""

    note_id: str
    outcome: str


@dataclass(frozen=True, slots=True)
class CaseMeasurement:
    """Machine-readable measurement for one benchmark case."""

    case_id: str
    query: str
    k: int
    expected_refs: tuple[str, ...]
    candidate_refs: tuple[str, ...]
    actual_refs: tuple[str, ...]
    ranked_candidates: tuple[RankedCandidate, ...]
    current_reread: tuple[CurrentRereadOutcome, ...]
    recall_at_k: float | None
    precision_at_k: float | None
    expected_refs_match: bool
    failure_category: str
    correctness_check: bool | None


@dataclass(frozen=True, slots=True)
class LexicalGapReport:
    """Complete deterministic report with no wall-clock or local-path fields."""

    benchmark_version: str
    report_schema_version: int
    cases: tuple[CaseMeasurement, ...]
    cases_with_expected_refs: int
    mean_recall_at_k: float | None
    miss_cases: tuple[str, ...]


_SYNTHETIC_NOTES: Final[tuple[SyntheticNote, ...]] = (
    SyntheticNote(
        PROJECT_ALPHA_ID,
        "30 Resources/Project planning Alpha.md",
        "project planning weekly review keeps tasks visible.",
    ),
    SyntheticNote(
        PROJECT_BETA_ID,
        "30 Resources/Project planning Beta.md",
        "project planning release notes capture milestones.",
    ),
    SyntheticNote(
        JOGGING_ID,
        "30 Resources/Jogging recovery.md",
        "jogging recovery uses easy mileage and rest days.",
    ),
    SyntheticNote(
        MUTABLE_ID,
        "30 Resources/Mutable context.md",
        "mutable context legacy body.",
    ),
    SyntheticNote(
        DELETED_ID,
        "30 Resources/Deleted candidate.md",
        "deleted candidate must not resurrect.",
    ),
)

_CASES: Final[tuple[BenchmarkCase, ...]] = (
    BenchmarkCase(
        case_id="exact_project_terms",
        query="project planning",
        expected_refs=(PROJECT_ALPHA_ID, PROJECT_BETA_ID),
        limit=2,
        scenario="live",
    ),
    BenchmarkCase(
        case_id="lexical_synonym_gap",
        query="running",
        expected_refs=(JOGGING_ID,),
        limit=1,
        scenario="live",
    ),
    BenchmarkCase(
        case_id="stale_body_current_reread",
        query="mutable context",
        expected_refs=(MUTABLE_ID,),
        limit=1,
        scenario="stale_body",
    ),
    BenchmarkCase(
        case_id="deleted_candidate_not_resurrected",
        query="deleted candidate",
        expected_refs=(),
        limit=1,
        scenario="deleted_candidate",
    ),
)


class _StaticSearch:
    """Replay one captured Search result while current reread uses live files."""

    def __init__(self, hits: tuple[SearchHit, ...]) -> None:
        self._hits = hits

    def execute(self, request: SearchRequest) -> tuple[SearchHit, ...]:
        del request
        return self._hits


def run_benchmark() -> LexicalGapReport:
    """Run all fixed cases against disposable synthetic vaults."""

    measurements: list[CaseMeasurement] = []
    with TemporaryDirectory(prefix="second-brain-lexical-gap-v1-") as temporary_root:
        root = Path(temporary_root)
        for case in _CASES:
            case_root = root / case.case_id
            _create_synthetic_vault(case_root)
            hits = _search_synthetic(case_root, case)
            if case.scenario == "stale_body":
                _rewrite_note(case_root, MUTABLE_ID, _MUTABLE_CURRENT_BODY.rstrip("\n"))
            elif case.scenario == "deleted_candidate":
                _delete_note(case_root, DELETED_ID)
            result = _build_current_context(case_root, case, hits)
            measurements.append(_measure_case(case, hits, result))

    expected_cases = tuple(measurement for measurement in measurements if measurement.expected_refs)
    recalls = [
        measurement.recall_at_k
        for measurement in expected_cases
        if measurement.recall_at_k is not None
    ]
    mean_recall = round(sum(recalls) / len(recalls), 6) if recalls else None
    return LexicalGapReport(
        benchmark_version=BENCHMARK_VERSION,
        report_schema_version=REPORT_SCHEMA_VERSION,
        cases=tuple(measurements),
        cases_with_expected_refs=len(expected_cases),
        mean_recall_at_k=mean_recall,
        miss_cases=tuple(
            measurement.case_id
            for measurement in measurements
            if measurement.failure_category == "lexical_miss"
        ),
    )


def report_as_dict(report: LexicalGapReport) -> dict[str, object]:
    """Serialize the stable report shape for machine-readable consumers."""

    return {
        "benchmark_version": report.benchmark_version,
        "report_schema_version": report.report_schema_version,
        "summary": {
            "case_count": len(report.cases),
            "cases_with_expected_refs": report.cases_with_expected_refs,
            "mean_recall_at_k": report.mean_recall_at_k,
            "miss_cases": list(report.miss_cases),
        },
        "cases": [_case_as_dict(case) for case in report.cases],
    }


def render_report_text(report: LexicalGapReport) -> str:
    """Render a stable human summary without local paths or timing noise."""

    lines = [
        f"Lexical Gap Benchmark {report.benchmark_version}",
        f"Cases: {len(report.cases)}",
        f"Cases with expected refs: {report.cases_with_expected_refs}",
        "Mean recall@k: "
        + (str(report.mean_recall_at_k) if report.mean_recall_at_k is not None else "-"),
        "Miss cases: " + (", ".join(report.miss_cases) if report.miss_cases else "-"),
    ]
    for case in report.cases:
        lines.extend(
            [
                "",
                f"[{case.case_id}] query={case.query!r} k={case.k}",
                "  expected refs: " + _join_or_dash(case.expected_refs),
                "  candidate refs: " + _join_or_dash(case.candidate_refs),
                "  actual refs: " + _join_or_dash(case.actual_refs),
                "  ranks: " + _ranks_text(case.ranked_candidates),
                "  current reread: " + _reread_text(case.current_reread),
                f"  recall@k: {_number_or_dash(case.recall_at_k)}",
                f"  precision@k: {_number_or_dash(case.precision_at_k)}",
                f"  expected refs match: {'yes' if case.expected_refs_match else 'no'}",
                f"  failure category: {case.failure_category}",
                "  correctness check: "
                + (
                    "not applicable"
                    if case.correctness_check is None
                    else ("pass" if case.correctness_check else "fail")
                ),
            ]
        )
    return "\n".join(lines)


def main(argv: Sequence[str] | None = None) -> int:
    """Run the benchmark as ``python -m second_brain.benchmarks.lexical_gap_v1``."""

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--format",
        choices=("text", "json"),
        default="text",
        help="report format; text is the default",
    )
    args = parser.parse_args(argv)
    report = run_benchmark()
    if args.format == "json":
        print(json.dumps(report_as_dict(report), ensure_ascii=False, indent=2))
    else:
        print(render_report_text(report))
    return 0


def _create_synthetic_vault(root: Path) -> None:
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
    for note in _SYNTHETIC_NOTES:
        _write_note(root, note)


def _write_note(root: Path, note: SyntheticNote) -> None:
    (root / note.relative_path).write_text(
        f"""---
id: {note.note_id}
type: zettel
created: 2026-09-01T12:00:00+00:00
tags:
  - benchmark
---
{note.body}
""",
        encoding="utf-8",
    )


def _rewrite_note(root: Path, note_id: UUID, body: str) -> None:
    original = next(note for note in _SYNTHETIC_NOTES if note.note_id == note_id)
    _write_note(root, replace(original, body=body))


def _delete_note(root: Path, note_id: UUID) -> None:
    original = next(note for note in _SYNTHETIC_NOTES if note.note_id == note_id)
    (root / original.relative_path).unlink()


def _search_synthetic(root: Path, case: BenchmarkCase) -> tuple[SearchHit, ...]:
    index = SqliteFts5SearchIndex()
    try:
        return SearchVault(FileSystemVaultReader(root), index).execute(
            SearchRequest(query=case.query, limit=case.limit)
        )
    finally:
        index.close()


def _build_current_context(
    root: Path,
    case: BenchmarkCase,
    hits: tuple[SearchHit, ...],
) -> SelfContextResult:
    reader = FileSystemVaultReader(root)
    model = BuildSelfModel(
        reader,
        policy=DEFAULT_SELF_MODEL_POLICY,
        clock=lambda: _FIXED_NOW,
    )
    return BuildSelfContext(
        search=_StaticSearch(hits),
        retriever=RetrieveManagedNote(reader),
        self_model=model,
    ).execute(SelfContextRequest(query=case.query, limit=case.limit))


def _measure_case(
    case: BenchmarkCase,
    hits: tuple[SearchHit, ...],
    result: SelfContextResult,
) -> CaseMeasurement:
    expected = tuple(str(note_id) for note_id in case.expected_refs)
    candidates = tuple(str(hit.note_id) for hit in hits)
    actual = tuple(str(item.note_id) for item in result.items)
    expected_set = set(expected)
    actual_set = set(actual)
    intersection_count = len(expected_set & actual_set)
    recall = round(intersection_count / len(expected_set), 6) if expected_set else None
    precision = round(intersection_count / len(actual_set), 6) if actual_set else None
    item_by_rank = {item.search_rank: item for item in result.items}
    exclusion_by_rank = {exclusion.search_rank: exclusion for exclusion in result.exclusions}
    current_reread: list[CurrentRereadOutcome] = []
    for rank, hit in enumerate(hits, start=1):
        item = item_by_rank.get(rank)
        if item is not None:
            if case.scenario == "stale_body":
                outcome = (
                    "stale_body_refreshed"
                    if item.body == _MUTABLE_CURRENT_BODY
                    else "stale_body_not_refreshed"
                )
            else:
                outcome = "included_current"
        else:
            exclusion = exclusion_by_rank[rank]
            outcome = exclusion.reason.value
        current_reread.append(CurrentRereadOutcome(note_id=str(hit.note_id), outcome=outcome))

    expected_match = expected_set == actual_set
    if case.scenario == "stale_body":
        correctness = (
            expected_match
            and len(result.items) == 1
            and result.items[0].body == _MUTABLE_CURRENT_BODY
            and not result.exclusions
        )
        failure_category = "stale_body_refreshed" if correctness else "stale_body_not_refreshed"
    elif case.scenario == "deleted_candidate" and not hits:
        correctness = None
        failure_category = "lexical_miss"
    elif case.scenario == "deleted_candidate":
        correctness = (
            not result.items
            and len(result.exclusions) == 1
            and result.exclusions[0].reason is SelfContextExclusionReason.CANDIDATE_NOT_FOUND
        )
        if correctness:
            failure_category = "deleted_candidate_excluded"
        elif str(DELETED_ID) in actual_set:
            failure_category = "deleted_candidate_resurrected"
        else:
            failure_category = "deleted_candidate_boundary_regression"
    elif expected_set and not expected_set.issubset(actual_set):
        correctness = None
        failure_category = "lexical_miss"
    elif expected_set != actual_set:
        correctness = None
        failure_category = "unexpected_candidate"
    else:
        correctness = None
        failure_category = "none"

    return CaseMeasurement(
        case_id=case.case_id,
        query=case.query,
        k=case.limit,
        expected_refs=expected,
        candidate_refs=candidates,
        actual_refs=actual,
        ranked_candidates=tuple(
            RankedCandidate(note_id=str(hit.note_id), rank=rank)
            for rank, hit in enumerate(hits, start=1)
        ),
        current_reread=tuple(current_reread),
        recall_at_k=recall,
        precision_at_k=precision,
        expected_refs_match=expected_match,
        failure_category=failure_category,
        correctness_check=correctness,
    )


def _case_as_dict(case: CaseMeasurement) -> dict[str, object]:
    return {
        "case_id": case.case_id,
        "query": case.query,
        "k": case.k,
        "expected_refs": list(case.expected_refs),
        "candidate_refs": list(case.candidate_refs),
        "actual_refs": list(case.actual_refs),
        "ranked_candidates": [
            {"note_id": candidate.note_id, "rank": candidate.rank}
            for candidate in case.ranked_candidates
        ],
        "current_reread": [
            {"note_id": outcome.note_id, "outcome": outcome.outcome}
            for outcome in case.current_reread
        ],
        "recall_at_k": case.recall_at_k,
        "precision_at_k": case.precision_at_k,
        "expected_refs_match": case.expected_refs_match,
        "failure_category": case.failure_category,
        "correctness_check": case.correctness_check,
    }


def _join_or_dash(values: tuple[str, ...]) -> str:
    return ", ".join(values) if values else "-"


def _ranks_text(values: tuple[RankedCandidate, ...]) -> str:
    return ", ".join(f"{value.note_id}={value.rank}" for value in values) or "-"


def _reread_text(values: tuple[CurrentRereadOutcome, ...]) -> str:
    return ", ".join(f"{value.note_id}={value.outcome}" for value in values) or "-"


def _number_or_dash(value: float | None) -> str:
    return str(value) if value is not None else "-"


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "BENCHMARK_VERSION",
    "CaseMeasurement",
    "LexicalGapReport",
    "main",
    "render_report_text",
    "report_as_dict",
    "run_benchmark",
]
