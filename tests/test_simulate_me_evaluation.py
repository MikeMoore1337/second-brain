"""Deterministic synthetic Simulate Me evaluation harness tests."""

from __future__ import annotations

import json
from dataclasses import replace
from typing import Any, cast

import pytest

import second_brain.benchmarks.simulate_me_evaluation_v1 as evaluation
from second_brain.benchmarks.simulate_me_evaluation_v1 import (
    CORPUS_VERSION,
    EVALUATION_VERSION,
    main,
    report_as_dict,
    run_evaluation,
)

EXPECTED_POLICY_FINGERPRINT = (
    "sha256:07aa1d0d57fdd2d009087c05423fc4eb9304da70e87f32b1790fbd4753f21c3a"
)


def _cases(payload: dict[str, object]) -> list[dict[str, Any]]:
    return cast(list[dict[str, Any]], payload["cases"])


def _case(payload: dict[str, object], case_id: str) -> dict[str, Any]:
    return next(case for case in _cases(payload) if case["case_id"] == case_id)


def test_evaluation_report_is_versioned_complete_and_green() -> None:
    payload = report_as_dict(run_evaluation())

    assert payload["evaluation_version"] == EVALUATION_VERSION
    assert payload["corpus_version"] == CORPUS_VERSION
    assert payload["report_schema_version"] == 1
    assert payload["synthetic_only"] is True
    assert payload["derivation_version"] == "simulate-me-v1"
    assert payload["policy_id"] == "simulate-me-direct-exact-v1"
    assert payload["policy_fingerprint"] == EXPECTED_POLICY_FINGERPRINT
    assert payload["total_cases"] == 15
    assert payload["passed_cases"] == 15
    assert payload["failed_cases"] == 0
    assert payload["all_passed"] is True
    assert payload["category_counts"] == [
        {"category": "prediction", "count": 5},
        {"category": "abstention/no_matching_evidence", "count": 7},
        {"category": "abstention/multiple_options_supported", "count": 2},
        {"category": "abstention/insufficient_or_invalid_current_context", "count": 1},
    ]

    for case in _cases(payload):
        assert case["passed"] is True
        assert case["mismatch_reason"] is None
        assert case["actual_derivation_version"] == "simulate-me-v1"
        assert case["actual_policy_id"] == "simulate-me-direct-exact-v1"
        assert case["actual_policy_fingerprint"] == EXPECTED_POLICY_FINGERPRINT
        assert "temporary" not in json.dumps(case, ensure_ascii=False).casefold()


def test_evaluation_report_is_reproducible_without_paths_or_note_bodies() -> None:
    first = report_as_dict(run_evaluation())
    second = report_as_dict(run_evaluation())

    assert first == second
    serialized = json.dumps(first, ensure_ascii=False, sort_keys=True)
    assert "second-brain-simulate-me-evaluation-v1-" not in serialized
    assert "Выбрать направление работы" not in serialized
    assert "Получится закончить быстрее." not in serialized


def test_evaluation_captures_exact_refs_and_caveats() -> None:
    payload = report_as_dict(run_evaluation())

    same_option = _case(payload, "prediction-same-option-multiple-refs")
    assert same_option["actual_evidence_refs"] == [
        {
            "claim_id": "0198f4c5-6a00-7000-8000-000000000101",
            "dimension": "preference",
            "note_ids": ["0198f4c5-6a00-7000-8000-000000000101"],
            "evidence_at": "2026-09-05T12:00:00+00:00",
        },
        {
            "claim_id": "0198f4c5-6a00-7000-8000-000000000103",
            "dimension": "goal",
            "note_ids": ["0198f4c5-6a00-7000-8000-000000000103"],
            "evidence_at": "2026-09-05T12:00:00+00:00",
        },
    ]

    unknown = _case(payload, "prediction-unknown-time-caveat")
    assert unknown["actual_temporal_caveats"] == [
        {
            "code": "evidence_at_unknown",
            "claim_id": "0198f4c5-6a00-7000-8000-000000000107",
        }
    ]

    belief = _case(payload, "abstention-belief-contextual-only")
    assert belief["actual_category"] == "abstention/no_matching_evidence"
    assert belief["actual_contextual_evidence_refs"][0]["dimension"] == "belief"

    for case_id in (
        "abstention-case-only-near-match",
        "abstention-internal-whitespace-near-match",
        "abstention-prefix-near-match",
    ):
        near_match = _case(payload, case_id)
        assert near_match["actual_category"] == "abstention/no_matching_evidence"
        assert near_match["actual_evidence_refs"] == []

    asymmetric = _case(payload, "abstention-asymmetric-count-and-recency")
    assert asymmetric["actual_category"] == "abstention/multiple_options_supported"
    assert [ref["claim_id"] for ref in asymmetric["actual_evidence_refs"]] == [
        "0198f4c5-6a00-7000-8000-000000000101",
        "0198f4c5-6a00-7000-8000-000000000102",
        "0198f4c5-6a00-7000-8000-000000000103",
    ]

    invalid = _case(payload, "abstention-invalid-current-context")
    assert invalid["actual_category"] == "abstention/insufficient_or_invalid_current_context"
    assert invalid["actual_evidence_refs"] == []


def test_evaluation_cli_renders_machine_and_human_reports(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert main(["--format", "json"]) == 0
    json_output = capsys.readouterr().out
    payload = json.loads(json_output)
    assert payload["all_passed"] is True
    assert payload["evaluation_version"] == EVALUATION_VERSION

    assert main(["--format", "markdown"]) == 0
    markdown_output = capsys.readouterr().out
    assert f"# Simulate Me Evaluation {EVALUATION_VERSION}" in markdown_output
    assert "Synthetic temporary-vault corpus only" in markdown_output
    assert "| abstention/multiple_options_supported | 2 |" in markdown_output
    assert "0198f4c5-6a00-7000-8000-000000000101" in markdown_output
    assert "evidence_at_unknown" in markdown_output


def test_evaluation_reports_exact_mismatch_reason(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original = evaluation.EVALUATION_CASES[0]
    wrong_expected = replace(
        original.expected,
        category="abstention/no_matching_evidence",
    )
    monkeypatch.setattr(
        evaluation,
        "EVALUATION_CASES",
        (replace(original, expected=wrong_expected),),
    )

    payload = evaluation.report_as_dict(evaluation.run_evaluation())
    case = _cases(payload)[0]

    assert payload["all_passed"] is False
    assert payload["failed_cases"] == 1
    assert case["mismatch_reason"] == (
        'category: expected="abstention/no_matching_evidence" actual="prediction"'
    )
