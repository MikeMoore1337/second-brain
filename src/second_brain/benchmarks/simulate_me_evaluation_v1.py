"""Deterministic synthetic evaluation for the Simulate Me v1 contract.

The harness rebuilds the approved Stage 6 core from temporary, versioned vault
fixtures only. It records prediction/abstention categories, exact evidence
references and policy identity; it does not tune policy, calculate quality
thresholds, call providers, or read a configured user vault.
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Final, Literal

from second_brain.adapters.vault import FileSystemVaultReader
from second_brain.application.decision_journal import render_decision_journal_body
from second_brain.application.simulate_me import (
    BuildSimulateMe,
    SimulateMeContextualEvidenceRef,
    SimulateMeEvidenceRef,
    SimulateMeOption,
    SimulateMeRequest,
    SimulateMeResult,
    SimulateMeResultKind,
    validate_simulate_me_policy,
    validate_simulate_me_result,
)

EVALUATION_VERSION: Final[str] = "simulate-me-evaluation-v1"
CORPUS_VERSION: Final[str] = "simulate-me-synthetic-corpus-v1"
REPORT_SCHEMA_VERSION: Final[int] = 1
APPROVED_DERIVATION_VERSION: Final[str] = "simulate-me-v1"
APPROVED_POLICY_ID: Final[str] = "simulate-me-direct-exact-v1"
APPROVED_POLICY_FINGERPRINT: Final[str] = (
    "sha256:07aa1d0d57fdd2d009087c05423fc4eb9304da70e87f32b1790fbd4753f21c3a"
)
_FIXED_NOW: Final[datetime] = datetime(2026, 9, 6, 12, 0, tzinfo=UTC)
_KNOWN_EVIDENCE_AT: Final[str] = "2026-09-05T12:00:00+00:00"
_UNKNOWN_EVIDENCE_AT: Final[str] = "unknown"
_OLD_EVIDENCE_AT: Final[str] = "2020-01-01T12:00:00+00:00"
_NEW_EVIDENCE_AT: Final[str] = "2030-01-01T12:00:00+00:00"
_NOTE_CREATED_AT: Final[str] = "2026-09-05T18:00:00+00:00"
_CATEGORY_ORDER: Final[tuple[str, ...]] = (
    "prediction",
    "abstention/no_matching_evidence",
    "abstention/multiple_options_supported",
    "abstention/insufficient_or_invalid_current_context",
)
ResultCategory = Literal[
    "prediction",
    "abstention/no_matching_evidence",
    "abstention/multiple_options_supported",
    "abstention/insufficient_or_invalid_current_context",
]

PREFERENCE_A_ID: Final[str] = "0198f4c5-6a00-7000-8000-000000000101"
PREFERENCE_B_ID: Final[str] = "0198f4c5-6a00-7000-8000-000000000102"
GOAL_A_ID: Final[str] = "0198f4c5-6a00-7000-8000-000000000103"
BELIEF_A_ID: Final[str] = "0198f4c5-6a00-7000-8000-000000000104"
DECISION_A_ID: Final[str] = "0198f4c5-6a00-7000-8000-000000000105"
SEARCH_ONLY_ID: Final[str] = "0198f4c5-6a00-7000-8000-000000000106"
UNKNOWN_PREFERENCE_ID: Final[str] = "0198f4c5-6a00-7000-8000-000000000107"
NFC_PREFERENCE_ID: Final[str] = "0198f4c5-6a00-7000-8000-000000000108"
GOAL_B_ID: Final[str] = "0198f4c5-6a00-7000-8000-000000000109"
INVALID_CONTEXT_ID: Final[str] = "0198f4c5-6a00-7000-8000-000000000110"


@dataclass(frozen=True, slots=True)
class SyntheticNote:
    """One bounded note fixture written into a temporary managed vault."""

    note_id: str
    relative_path: str
    body: str
    self_kind: str | None = "preference"
    evidence_kind: str = "user_statement"
    evidence_at: str = _KNOWN_EVIDENCE_AT
    evidence_at_precision: str = "exact"
    domain: str | None = None
    enrolled: bool = True
    decision_id: str | None = None


@dataclass(frozen=True, slots=True)
class OptionCapture:
    """Stable JSON/report projection of one caller-owned option."""

    id: str
    label: str


@dataclass(frozen=True, slots=True)
class EvidenceCapture:
    """Stable JSON/report projection of one result evidence reference."""

    claim_id: str
    dimension: str
    note_ids: tuple[str, ...]
    evidence_at: str


@dataclass(frozen=True, slots=True)
class CaveatCapture:
    """Stable JSON/report projection of one temporal caveat."""

    code: str
    claim_id: str


@dataclass(frozen=True, slots=True)
class ExpectedOutcome:
    """Exact expected result fields for one versioned synthetic case."""

    category: ResultCategory
    selected_option: OptionCapture | None
    evidence_refs: tuple[EvidenceCapture, ...]
    contextual_evidence_refs: tuple[EvidenceCapture, ...]
    temporal_caveats: tuple[CaveatCapture, ...]


@dataclass(frozen=True, slots=True)
class EvaluationCase:
    """One synthetic request and its expected mechanical outcome."""

    case_id: str
    description: str
    notes: tuple[SyntheticNote, ...]
    request: SimulateMeRequest
    expected: ExpectedOutcome


@dataclass(frozen=True, slots=True)
class CaseMeasurement:
    """Reproducible actual-vs-expected evidence for one case."""

    case_id: str
    description: str
    expected_category: str
    actual_category: str
    expected_selected_option: OptionCapture | None
    actual_selected_option: OptionCapture | None
    expected_evidence_refs: tuple[EvidenceCapture, ...]
    actual_evidence_refs: tuple[EvidenceCapture, ...]
    expected_contextual_evidence_refs: tuple[EvidenceCapture, ...]
    actual_contextual_evidence_refs: tuple[EvidenceCapture, ...]
    expected_temporal_caveats: tuple[CaveatCapture, ...]
    actual_temporal_caveats: tuple[CaveatCapture, ...]
    expected_derivation_version: str
    actual_derivation_version: str | None
    expected_policy_id: str
    actual_policy_id: str | None
    expected_policy_fingerprint: str
    actual_policy_fingerprint: str | None
    passed: bool
    mismatch_reason: str | None


@dataclass(frozen=True, slots=True)
class CategoryCount:
    """Stable category counter entry; no quality threshold is implied."""

    category: str
    count: int


@dataclass(frozen=True, slots=True)
class SimulateMeEvaluationReport:
    """Complete versioned report without temporary paths or private data."""

    evaluation_version: str
    corpus_version: str
    report_schema_version: int
    synthetic_only: bool
    derivation_version: str
    policy_id: str
    policy_fingerprint: str
    total_cases: int
    passed_cases: int
    failed_cases: int
    all_passed: bool
    category_counts: tuple[CategoryCount, ...]
    cases: tuple[CaseMeasurement, ...]


@dataclass(frozen=True, slots=True)
class _ResultCapture:
    """Internal immutable capture used before report serialization."""

    category: str
    selected_option: OptionCapture | None
    evidence_refs: tuple[EvidenceCapture, ...]
    contextual_evidence_refs: tuple[EvidenceCapture, ...]
    temporal_caveats: tuple[CaveatCapture, ...]
    derivation_version: str
    policy_id: str
    policy_fingerprint: str


def run_evaluation() -> SimulateMeEvaluationReport:
    """Execute every fixed case against a fresh temporary synthetic vault."""

    validate_simulate_me_policy()
    measurements: list[CaseMeasurement] = []
    with tempfile.TemporaryDirectory(
        prefix="second-brain-simulate-me-evaluation-v1-"
    ) as temporary_root:
        root = Path(temporary_root)
        for case in EVALUATION_CASES:
            case_root = root / case.case_id
            _create_synthetic_vault(case_root)
            for note in case.notes:
                _write_synthetic_note(case_root, note)
            measurements.append(_evaluate_case(case, case_root))

    category_counts = tuple(
        CategoryCount(
            category=category,
            count=sum(measurement.actual_category == category for measurement in measurements),
        )
        for category in _CATEGORY_ORDER
    )
    passed_cases = sum(measurement.passed for measurement in measurements)
    return SimulateMeEvaluationReport(
        evaluation_version=EVALUATION_VERSION,
        corpus_version=CORPUS_VERSION,
        report_schema_version=REPORT_SCHEMA_VERSION,
        synthetic_only=True,
        derivation_version=APPROVED_DERIVATION_VERSION,
        policy_id=APPROVED_POLICY_ID,
        policy_fingerprint=APPROVED_POLICY_FINGERPRINT,
        total_cases=len(measurements),
        passed_cases=passed_cases,
        failed_cases=len(measurements) - passed_cases,
        all_passed=passed_cases == len(measurements),
        category_counts=category_counts,
        cases=tuple(measurements),
    )


def report_as_dict(report: SimulateMeEvaluationReport) -> dict[str, object]:
    """Serialize a report with stable field names and list ordering."""

    return {
        "evaluation_version": report.evaluation_version,
        "corpus_version": report.corpus_version,
        "report_schema_version": report.report_schema_version,
        "synthetic_only": report.synthetic_only,
        "derivation_version": report.derivation_version,
        "policy_id": report.policy_id,
        "policy_fingerprint": report.policy_fingerprint,
        "total_cases": report.total_cases,
        "passed_cases": report.passed_cases,
        "failed_cases": report.failed_cases,
        "all_passed": report.all_passed,
        "category_counts": [
            {"category": item.category, "count": item.count} for item in report.category_counts
        ],
        "cases": [_case_as_dict(case) for case in report.cases],
    }


def render_markdown(report: SimulateMeEvaluationReport) -> str:
    """Render a compact human report without paths, bodies, or timing noise."""

    lines = [
        f"# Simulate Me Evaluation {report.evaluation_version}",
        "",
        "Synthetic temporary-vault corpus only; no private data, provider calls, "
        "calibration, or policy updates.",
        "",
        f"- Corpus: {report.corpus_version}",
        f"- Result: {'PASS' if report.all_passed else 'FAIL'} "
        f"({report.passed_cases}/{report.total_cases} cases)",
        f"- Derivation: {report.derivation_version}",
        f"- Policy: {report.policy_id}",
        f"- Fingerprint: {report.policy_fingerprint}",
        "",
        "| Category | Cases |",
        "| --- | ---: |",
    ]
    for item in report.category_counts:
        lines.append(f"| {item.category} | {item.count} |")
    lines.extend(
        (
            "",
            "| Case | Expected | Actual | Status | Selected | Evidence refs | "
            "Contextual refs | Caveats | Identity | Mismatch |",
            "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
        )
    )
    for case in report.cases:
        mismatch = case.mismatch_reason or "-"
        identity = {
            "derivation_version": case.actual_derivation_version,
            "policy_id": case.actual_policy_id,
            "policy_fingerprint": case.actual_policy_fingerprint,
        }
        lines.append(
            f"| {case.case_id} | {case.expected_category} | {case.actual_category} | "
            f"{'PASS' if case.passed else 'FAIL'} | "
            f"{_stable_value(case.actual_selected_option)} | "
            f"{_stable_value(case.actual_evidence_refs)} | "
            f"{_stable_value(case.actual_contextual_evidence_refs)} | "
            f"{_stable_value(case.actual_temporal_caveats)} | "
            f"{_stable_value(identity)} | {mismatch} |"
        )
    return "\n".join(lines) + "\n"


def main(argv: Sequence[str] | None = None) -> int:
    """Run the deterministic corpus and print JSON or Markdown."""

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--format",
        choices=("json", "markdown"),
        default="markdown",
        help="report format; markdown is the default",
    )
    args = parser.parse_args(argv)
    report = run_evaluation()
    if args.format == "json":
        rendered = (
            json.dumps(
                report_as_dict(report),
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            )
            + "\n"
        )
    else:
        rendered = render_markdown(report)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    print(rendered, end="")
    return 0 if report.all_passed else 1


def _evaluate_case(case: EvaluationCase, root: Path) -> CaseMeasurement:
    try:
        result = BuildSimulateMe(
            FileSystemVaultReader(root),
            clock=lambda: _FIXED_NOW,
        ).execute(case.request)
        validated = validate_simulate_me_result(result, request=case.request)
        actual = _capture_result(validated)
        mismatch_reason = _mismatch_reason(case.expected, actual)
        return _measurement(case, actual, mismatch_reason)
    except Exception as error:
        return _measurement(
            case,
            _ResultCapture(
                category="execution_error",
                selected_option=None,
                evidence_refs=(),
                contextual_evidence_refs=(),
                temporal_caveats=(),
                derivation_version="",
                policy_id="",
                policy_fingerprint="",
            ),
            f"execution_failed: {type(error).__name__}",
        )


def _measurement(
    case: EvaluationCase,
    actual: _ResultCapture,
    mismatch_reason: str | None,
) -> CaseMeasurement:
    expected = case.expected
    return CaseMeasurement(
        case_id=case.case_id,
        description=case.description,
        expected_category=expected.category,
        actual_category=actual.category,
        expected_selected_option=expected.selected_option,
        actual_selected_option=actual.selected_option,
        expected_evidence_refs=expected.evidence_refs,
        actual_evidence_refs=actual.evidence_refs,
        expected_contextual_evidence_refs=expected.contextual_evidence_refs,
        actual_contextual_evidence_refs=actual.contextual_evidence_refs,
        expected_temporal_caveats=expected.temporal_caveats,
        actual_temporal_caveats=actual.temporal_caveats,
        expected_derivation_version=APPROVED_DERIVATION_VERSION,
        actual_derivation_version=actual.derivation_version or None,
        expected_policy_id=APPROVED_POLICY_ID,
        actual_policy_id=actual.policy_id or None,
        expected_policy_fingerprint=APPROVED_POLICY_FINGERPRINT,
        actual_policy_fingerprint=actual.policy_fingerprint or None,
        passed=mismatch_reason is None,
        mismatch_reason=mismatch_reason,
    )


def _capture_result(result: SimulateMeResult) -> _ResultCapture:
    return _ResultCapture(
        category=_result_category(result),
        selected_option=_option_capture(result.selected_option),
        evidence_refs=tuple(_evidence_capture(ref) for ref in result.evidence_refs),
        contextual_evidence_refs=tuple(
            _evidence_capture(ref) for ref in result.contextual_evidence_refs
        ),
        temporal_caveats=tuple(
            CaveatCapture(code=caveat.code.value, claim_id=str(caveat.claim_id))
            for caveat in result.temporal_caveats
        ),
        derivation_version=result.derivation_version,
        policy_id=result.policy_id,
        policy_fingerprint=result.policy_fingerprint,
    )


def _result_category(result: SimulateMeResult) -> str:
    if result.kind is SimulateMeResultKind.PREDICTION:
        return "prediction"
    if result.abstention_code is None:
        return "abstention/invalid_result"
    return f"abstention/{result.abstention_code.value}"


def _mismatch_reason(expected: ExpectedOutcome, actual: _ResultCapture) -> str | None:
    reasons: list[str] = []
    comparisons = (
        ("category", expected.category, actual.category),
        ("selected_option", expected.selected_option, actual.selected_option),
        ("evidence_refs", expected.evidence_refs, actual.evidence_refs),
        (
            "contextual_evidence_refs",
            expected.contextual_evidence_refs,
            actual.contextual_evidence_refs,
        ),
        ("temporal_caveats", expected.temporal_caveats, actual.temporal_caveats),
        ("derivation_version", APPROVED_DERIVATION_VERSION, actual.derivation_version),
        ("policy_id", APPROVED_POLICY_ID, actual.policy_id),
        ("policy_fingerprint", APPROVED_POLICY_FINGERPRINT, actual.policy_fingerprint),
    )
    for field, expected_value, actual_value in comparisons:
        if expected_value != actual_value:
            reasons.append(
                f"{field}: expected={_stable_value(expected_value)} "
                f"actual={_stable_value(actual_value)}"
            )
    return "; ".join(reasons) or None


def _option_capture(option: SimulateMeOption | None) -> OptionCapture | None:
    if option is None:
        return None
    return OptionCapture(id=option.id, label=option.label)


def _evidence_capture(
    ref: SimulateMeEvidenceRef | SimulateMeContextualEvidenceRef,
) -> EvidenceCapture:
    return EvidenceCapture(
        claim_id=str(ref.claim_id),
        dimension=ref.dimension.value,
        note_ids=tuple(str(note_id) for note_id in ref.note_ids),
        evidence_at=ref.evidence_at
        if isinstance(ref.evidence_at, str)
        else ref.evidence_at.isoformat(),
    )


def _case_as_dict(case: CaseMeasurement) -> dict[str, object]:
    return {
        "case_id": case.case_id,
        "description": case.description,
        "expected_category": case.expected_category,
        "actual_category": case.actual_category,
        "expected_selected_option": _option_as_dict(case.expected_selected_option),
        "actual_selected_option": _option_as_dict(case.actual_selected_option),
        "expected_evidence_refs": [_evidence_as_dict(ref) for ref in case.expected_evidence_refs],
        "actual_evidence_refs": [_evidence_as_dict(ref) for ref in case.actual_evidence_refs],
        "expected_contextual_evidence_refs": [
            _evidence_as_dict(ref) for ref in case.expected_contextual_evidence_refs
        ],
        "actual_contextual_evidence_refs": [
            _evidence_as_dict(ref) for ref in case.actual_contextual_evidence_refs
        ],
        "expected_temporal_caveats": [
            _caveat_as_dict(caveat) for caveat in case.expected_temporal_caveats
        ],
        "actual_temporal_caveats": [
            _caveat_as_dict(caveat) for caveat in case.actual_temporal_caveats
        ],
        "expected_derivation_version": case.expected_derivation_version,
        "actual_derivation_version": case.actual_derivation_version,
        "expected_policy_id": case.expected_policy_id,
        "actual_policy_id": case.actual_policy_id,
        "expected_policy_fingerprint": case.expected_policy_fingerprint,
        "actual_policy_fingerprint": case.actual_policy_fingerprint,
        "passed": case.passed,
        "mismatch_reason": case.mismatch_reason,
    }


def _option_as_dict(option: OptionCapture | None) -> dict[str, str] | None:
    if option is None:
        return None
    return {"id": option.id, "label": option.label}


def _evidence_as_dict(ref: EvidenceCapture) -> dict[str, object]:
    return {
        "claim_id": ref.claim_id,
        "dimension": ref.dimension,
        "note_ids": list(ref.note_ids),
        "evidence_at": ref.evidence_at,
    }


def _caveat_as_dict(caveat: CaveatCapture) -> dict[str, str]:
    return {"code": caveat.code, "claim_id": caveat.claim_id}


def _stable_value(value: object) -> str:
    if isinstance(value, OptionCapture):
        return json.dumps(_option_as_dict(value), ensure_ascii=False, sort_keys=True)
    if isinstance(value, tuple) and all(isinstance(item, EvidenceCapture) for item in value):
        return json.dumps(
            [_evidence_as_dict(item) for item in value],
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
    if isinstance(value, tuple) and all(isinstance(item, CaveatCapture) for item in value):
        return json.dumps(
            [_caveat_as_dict(item) for item in value],
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)


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
    manifest = "\n".join(
        (
            "schema_version: 1",
            "vault_id: 0198f4c5-6a00-7000-8000-000000000001",
            "default_language: ru",
            "paths:",
            "  inbox: 00 Inbox",
            "  projects: 10 Projects",
            "  areas: 20 Areas",
            "  resources: 30 Resources",
            "  zettelkasten: 40 Zettelkasten",
            "  archive: 90 Archive",
            "  templates: _templates",
            "  attachments: _attachments",
            "attachments:",
            "  warning_size_bytes: 10485760",
            "  max_size_bytes: 52428800",
            "",
        )
    )
    (root / "second-brain.yaml").write_text(manifest, encoding="utf-8")


def _write_synthetic_note(root: Path, note: SyntheticNote) -> None:
    fields = [
        f"id: {note.note_id}",
        "type: zettel",
        f"created: {_NOTE_CREATED_AT}",
        "tags: []",
    ]
    if note.enrolled:
        fields.extend(
            (
                "second_brain_personal_memory: 1",
                f"evidence_kind: {note.evidence_kind}",
            )
        )
        if note.self_kind is not None:
            fields.append(f"self_kind: {note.self_kind}")
        fields.extend(
            (
                f'evidence_at: "{note.evidence_at}"'
                if note.evidence_at != _UNKNOWN_EVIDENCE_AT
                else "evidence_at: unknown",
                f"evidence_at_precision: {note.evidence_at_precision}",
            )
        )
        if note.domain is not None:
            fields.append(f"domain: {note.domain}")
        if note.decision_id is not None:
            fields.append(f"decision_id: {note.decision_id}")
    path = root / note.relative_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("---\n" + "\n".join(fields) + "\n---\n" + note.body, encoding="utf-8")


def _direct_note(
    note_id: str,
    relative_path: str,
    label: str,
    *,
    self_kind: str = "preference",
    evidence_at: str = _KNOWN_EVIDENCE_AT,
    evidence_at_precision: str = "exact",
) -> SyntheticNote:
    return SyntheticNote(
        note_id=note_id,
        relative_path=relative_path,
        body=label,
        self_kind=self_kind,
        evidence_at=evidence_at,
        evidence_at_precision=evidence_at_precision,
    )


def _expected_ref(
    note_id: str,
    dimension: str,
    *,
    evidence_at: str = _KNOWN_EVIDENCE_AT,
) -> EvidenceCapture:
    return EvidenceCapture(
        claim_id=note_id,
        dimension=dimension,
        note_ids=(note_id,),
        evidence_at=evidence_at,
    )


def _expected_caveat(note_id: str) -> CaveatCapture:
    return CaveatCapture(code="evidence_at_unknown", claim_id=note_id)


def _prediction(
    option_id: str,
    label: str,
    evidence: tuple[EvidenceCapture, ...],
    *,
    contextual: tuple[EvidenceCapture, ...] = (),
    caveats: tuple[CaveatCapture, ...] = (),
) -> ExpectedOutcome:
    return ExpectedOutcome(
        category="prediction",
        selected_option=OptionCapture(option_id, label),
        evidence_refs=evidence,
        contextual_evidence_refs=contextual,
        temporal_caveats=caveats,
    )


def _abstention(
    code: ResultCategory,
    evidence: tuple[EvidenceCapture, ...] = (),
    *,
    contextual: tuple[EvidenceCapture, ...] = (),
    caveats: tuple[CaveatCapture, ...] = (),
) -> ExpectedOutcome:
    if not code.startswith("abstention/"):
        raise ValueError("synthetic abstention category must be namespaced")
    return ExpectedOutcome(
        category=code,
        selected_option=None,
        evidence_refs=evidence,
        contextual_evidence_refs=contextual,
        temporal_caveats=caveats,
    )


_DECISION_BODY: Final[str] = render_decision_journal_body(
    situation="Выбрать направление работы",
    available_options=("Первый вариант", "Второй вариант"),
    information_known_at_decision_time="Известны ограничения и срок.",
    criteria=("Скорость",),
    chosen_option="Первый вариант",
    reasons="Он лучше соответствует ограничению по времени.",
    confidence="Средняя",
    expected_result="Получится закончить быстрее.",
)

EVALUATION_CASES: Final[tuple[EvaluationCase, ...]] = (
    EvaluationCase(
        case_id="prediction-single-preference",
        description="one direct preference supports exactly one caller option",
        notes=(_direct_note(PREFERENCE_A_ID, "10 Projects/Preference A.md", "Первый вариант"),),
        request=SimulateMeRequest(
            "Куда направить усилия?",
            (SimulateMeOption("a", "Первый вариант"), SimulateMeOption("b", "Второй вариант")),
        ),
        expected=_prediction(
            "a", "Первый вариант", (_expected_ref(PREFERENCE_A_ID, "preference"),)
        ),
    ),
    EvaluationCase(
        case_id="prediction-same-option-multiple-refs",
        description="preference and goal refs support one distinct option without weighting",
        notes=(
            _direct_note(PREFERENCE_A_ID, "10 Projects/Preference A.md", "Первый вариант"),
            _direct_note(
                GOAL_A_ID,
                "10 Projects/Goal A.md",
                "Первый вариант",
                self_kind="goal",
            ),
        ),
        request=SimulateMeRequest(
            "Один выбор",
            (SimulateMeOption("a", "Первый вариант"), SimulateMeOption("b", "Второй вариант")),
        ),
        expected=_prediction(
            "a",
            "Первый вариант",
            (
                _expected_ref(PREFERENCE_A_ID, "preference"),
                _expected_ref(GOAL_A_ID, "goal"),
            ),
        ),
    ),
    EvaluationCase(
        case_id="prediction-goal",
        description="one direct goal is eligible on the same exact-label boundary",
        notes=(
            _direct_note(GOAL_B_ID, "10 Projects/Goal B.md", "Завершить проект", self_kind="goal"),
        ),
        request=SimulateMeRequest(
            "Следующая цель",
            (SimulateMeOption("goal", "Завершить проект"), SimulateMeOption("other", "Отложить")),
        ),
        expected=_prediction(
            "goal",
            "Завершить проект",
            (_expected_ref(GOAL_B_ID, "goal"),),
        ),
    ),
    EvaluationCase(
        case_id="prediction-unknown-time-caveat",
        description="unknown evidence time remains a caveat and does not block one match",
        notes=(
            _direct_note(
                UNKNOWN_PREFERENCE_ID,
                "10 Projects/Unknown preference.md",
                "Старый вариант",
                evidence_at=_UNKNOWN_EVIDENCE_AT,
                evidence_at_precision="unknown",
            ),
        ),
        request=SimulateMeRequest("Выбор", (SimulateMeOption("old", "Старый вариант"),)),
        expected=_prediction(
            "old",
            "Старый вариант",
            (
                _expected_ref(
                    UNKNOWN_PREFERENCE_ID,
                    "preference",
                    evidence_at=_UNKNOWN_EVIDENCE_AT,
                ),
            ),
            caveats=(_expected_caveat(UNKNOWN_PREFERENCE_ID),),
        ),
    ),
    EvaluationCase(
        case_id="prediction-nfc-edge-trim",
        description="only documented NFC and edge trim normalization is applied",
        notes=(_direct_note(NFC_PREFERENCE_ID, "10 Projects/NFC.md", "Café"),),
        request=SimulateMeRequest("literal", (SimulateMeOption("cafe", "  Cafe\u0301  "),)),
        expected=_prediction(
            "cafe",
            "  Cafe\u0301  ",
            (_expected_ref(NFC_PREFERENCE_ID, "preference"),),
        ),
    ),
    EvaluationCase(
        case_id="abstention-case-only-near-match",
        description="case-only difference is not an exact whole-label match",
        notes=(_direct_note(PREFERENCE_A_ID, "10 Projects/Case.md", "Первый вариант"),),
        request=SimulateMeRequest("Выбор", (SimulateMeOption("a", "первый вариант"),)),
        expected=_abstention("abstention/no_matching_evidence"),
    ),
    EvaluationCase(
        case_id="abstention-internal-whitespace-near-match",
        description="internal whitespace difference is not normalized",
        notes=(_direct_note(PREFERENCE_A_ID, "10 Projects/Whitespace.md", "Первый вариант"),),
        request=SimulateMeRequest("Выбор", (SimulateMeOption("a", "Первый  вариант"),)),
        expected=_abstention("abstention/no_matching_evidence"),
    ),
    EvaluationCase(
        case_id="abstention-prefix-near-match",
        description="substring or prefix evidence cannot support an option",
        notes=(_direct_note(PREFERENCE_A_ID, "10 Projects/Prefix.md", "Первый вариант"),),
        request=SimulateMeRequest("Выбор", (SimulateMeOption("a", "Первый"),)),
        expected=_abstention("abstention/no_matching_evidence"),
    ),
    EvaluationCase(
        case_id="abstention-no-match",
        description="no exact direct evidence produces bounded abstention",
        notes=(_direct_note(PREFERENCE_A_ID, "10 Projects/Preference A.md", "Первый вариант"),),
        request=SimulateMeRequest("Выбор", (SimulateMeOption("c", "Третий вариант"),)),
        expected=_abstention("abstention/no_matching_evidence"),
    ),
    EvaluationCase(
        case_id="abstention-multiple-options",
        description="two distinct exact supported options never tie-break by count or recency",
        notes=(
            _direct_note(PREFERENCE_A_ID, "10 Projects/Preference A.md", "Первый вариант"),
            _direct_note(PREFERENCE_B_ID, "10 Projects/Preference B.md", "Второй вариант"),
        ),
        request=SimulateMeRequest(
            "Выбор",
            (SimulateMeOption("a", "Первый вариант"), SimulateMeOption("b", "Второй вариант")),
        ),
        expected=_abstention(
            "abstention/multiple_options_supported",
            (
                _expected_ref(PREFERENCE_A_ID, "preference"),
                _expected_ref(PREFERENCE_B_ID, "preference"),
            ),
        ),
    ),
    EvaluationCase(
        case_id="abstention-asymmetric-count-and-recency",
        description="unequal support counts and timestamps still produce conflict abstention",
        notes=(
            _direct_note(
                PREFERENCE_A_ID,
                "10 Projects/Older A.md",
                "Первый вариант",
                evidence_at=_OLD_EVIDENCE_AT,
            ),
            _direct_note(
                GOAL_A_ID,
                "10 Projects/Newer A.md",
                "Первый вариант",
                self_kind="goal",
                evidence_at=_NEW_EVIDENCE_AT,
            ),
            _direct_note(
                PREFERENCE_B_ID,
                "10 Projects/Middle B.md",
                "Второй вариант",
            ),
        ),
        request=SimulateMeRequest(
            "Выбор",
            (SimulateMeOption("a", "Первый вариант"), SimulateMeOption("b", "Второй вариант")),
        ),
        expected=_abstention(
            "abstention/multiple_options_supported",
            (
                _expected_ref(PREFERENCE_A_ID, "preference", evidence_at=_OLD_EVIDENCE_AT),
                _expected_ref(PREFERENCE_B_ID, "preference"),
                _expected_ref(GOAL_A_ID, "goal", evidence_at=_NEW_EVIDENCE_AT),
            ),
        ),
    ),
    EvaluationCase(
        case_id="abstention-belief-contextual-only",
        description="belief can be contextual evidence but cannot select an option",
        notes=(
            _direct_note(
                BELIEF_A_ID,
                "10 Projects/Belief A.md",
                "Первый вариант",
                self_kind="belief",
                evidence_at=_UNKNOWN_EVIDENCE_AT,
                evidence_at_precision="unknown",
            ),
        ),
        request=SimulateMeRequest("Выбор", (SimulateMeOption("a", "Первый вариант"),)),
        expected=_abstention(
            "abstention/no_matching_evidence",
            contextual=(_expected_ref(BELIEF_A_ID, "belief", evidence_at=_UNKNOWN_EVIDENCE_AT),),
            caveats=(_expected_caveat(BELIEF_A_ID),),
        ),
    ),
    EvaluationCase(
        case_id="abstention-decision-journal-no-inference",
        description="Decision Journal chosen option is not an implicit Self Model assertion",
        notes=(
            SyntheticNote(
                note_id=DECISION_A_ID,
                relative_path="10 Projects/Decision Journal.md",
                body=_DECISION_BODY,
                self_kind="decision",
                evidence_kind="observed_decision",
                domain="work",
            ),
        ),
        request=SimulateMeRequest("Выбор", (SimulateMeOption("a", "Первый вариант"),)),
        expected=_abstention("abstention/no_matching_evidence"),
    ),
    EvaluationCase(
        case_id="abstention-search-only-no-inference",
        description="an ordinary searchable note is not a direct current Self Model claim",
        notes=(
            SyntheticNote(
                note_id=SEARCH_ONLY_ID,
                relative_path="30 Resources/Search-only.md",
                body="Первый вариант",
                enrolled=False,
            ),
        ),
        request=SimulateMeRequest("Выбор", (SimulateMeOption("a", "Первый вариант"),)),
        expected=_abstention("abstention/no_matching_evidence"),
    ),
    EvaluationCase(
        case_id="abstention-invalid-current-context",
        description="malformed enrolled current context fails closed without partial refs",
        notes=(
            SyntheticNote(
                note_id=INVALID_CONTEXT_ID,
                relative_path="10 Projects/Invalid context.md",
                body="Первый вариант",
                self_kind=None,
            ),
        ),
        request=SimulateMeRequest("Выбор", (SimulateMeOption("a", "Первый вариант"),)),
        expected=_abstention("abstention/insufficient_or_invalid_current_context"),
    ),
)


__all__ = [
    "APPROVED_DERIVATION_VERSION",
    "APPROVED_POLICY_FINGERPRINT",
    "APPROVED_POLICY_ID",
    "CORPUS_VERSION",
    "EVALUATION_CASES",
    "EVALUATION_VERSION",
    "REPORT_SCHEMA_VERSION",
    "CaseMeasurement",
    "SimulateMeEvaluationReport",
    "main",
    "render_markdown",
    "report_as_dict",
    "run_evaluation",
]


if __name__ == "__main__":
    raise SystemExit(main())
