"""Deterministic synthetic lexical-gap benchmark harness tests."""

from __future__ import annotations

import json
from typing import Any, cast

import pytest

from second_brain.benchmarks.lexical_gap_v1 import (
    main,
    report_as_dict,
    run_benchmark,
)


def test_lexical_gap_report_is_versioned_and_reproducible() -> None:
    first = report_as_dict(run_benchmark())
    second = report_as_dict(run_benchmark())

    assert first == second
    assert first["benchmark_version"] == "lexical-gap-v1"
    assert first["report_schema_version"] == 1
    assert first["summary"] == {
        "case_count": 4,
        "cases_with_expected_refs": 3,
        "mean_recall_at_k": 0.666667,
        "miss_cases": ["lexical_synonym_gap"],
    }
    cases = {case["case_id"]: case for case in cast(list[dict[str, Any]], first["cases"])}
    assert cases["exact_project_terms"]["k"] == 2
    assert cases["lexical_synonym_gap"]["k"] == 1
    assert cases["lexical_synonym_gap"]["failure_category"] == "lexical_miss"
    assert cases["lexical_synonym_gap"]["recall_at_k"] == 0.0
    assert cases["stale_body_current_reread"]["current_reread"] == [
        {
            "note_id": "0198f4c5-6a00-7000-8000-000000000013",
            "outcome": "stale_body_refreshed",
        }
    ]
    assert cases["stale_body_current_reread"]["correctness_check"] is True
    assert cases["deleted_candidate_not_resurrected"]["actual_refs"] == []
    assert cases["deleted_candidate_not_resurrected"]["current_reread"] == [
        {
            "note_id": "0198f4c5-6a00-7000-8000-000000000014",
            "outcome": "candidate_not_found",
        }
    ]
    assert all("path" not in key.casefold() for key in first)


def test_lexical_gap_command_supports_machine_and_human_reports(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert main(["--format", "json"]) == 0
    json_output = capsys.readouterr().out
    payload = json.loads(json_output)
    assert payload["benchmark_version"] == "lexical-gap-v1"

    assert main(["--format", "text"]) == 0
    text_output = capsys.readouterr().out
    assert "Lexical Gap Benchmark lexical-gap-v1" in text_output
    assert "current reread:" in text_output
    assert "failure category: lexical_miss" in text_output
