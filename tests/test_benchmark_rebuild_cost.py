"""Synthetic rebuild-cost benchmark harness tests."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast

import pytest

from second_brain.benchmarks.rebuild_cost_v1 import main, report_as_dict, run_benchmark


def test_rebuild_cost_report_has_fixed_fixture_and_operation_shape() -> None:
    payload = report_as_dict(run_benchmark())

    assert payload["benchmark_version"] == "rebuild-cost-v1"
    assert payload["report_schema_version"] == 1
    assert payload["synthetic_only"] is True
    assert payload["performance_thresholds"] == []
    fixtures = cast(list[dict[str, Any]], payload["fixtures"])
    assert fixtures == [
        {"name": "small", "note_count": 4},
        {"name": "medium", "note_count": 12},
        {"name": "large", "note_count": 24},
    ]

    measurements = cast(list[dict[str, Any]], payload["measurements"])
    assert len(measurements) == 9
    by_fixture = {fixture["name"]: fixture["note_count"] for fixture in fixtures}
    for measurement in measurements:
        fixture = cast(str, measurement["fixture"])
        note_count = cast(int, measurement["note_count"])
        operation = cast(str, measurement["operation"])
        assert note_count == by_fixture[fixture]
        assert operation in {"timeline", "self_model", "self_retrieval"}
        assert cast(float, measurement["wall_clock_ms"]) >= 0
        assert cast(int, measurement["peak_traced_bytes"]) >= 0

        output = cast(dict[str, int], measurement["output"])
        if operation == "timeline":
            assert output == {"known_total": note_count, "unknown_total": 0}
        elif operation == "self_model":
            assert output == {
                "claim_count": note_count,
                "eligible_evidence_count": note_count,
                "represented_evidence_count": note_count,
            }
        else:
            assert output == {
                "candidate_count": min(20, note_count),
                "included_count": min(20, note_count),
                "excluded_count": 0,
                "content_bytes": output["content_bytes"],
            }
            assert output["content_bytes"] > 0

    assert all("path" not in key.casefold() for key in payload)


def test_rebuild_cost_command_supports_json_markdown_and_output(
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    assert main(["--format", "json"]) == 0
    json_output = capsys.readouterr().out
    payload = json.loads(json_output)
    assert payload["benchmark_version"] == "rebuild-cost-v1"
    assert payload["synthetic_only"] is True

    assert main(["--format", "markdown"]) == 0
    markdown_output = capsys.readouterr().out
    assert "# Derived Read-Model Rebuild Cost rebuild-cost-v1" in markdown_output
    assert "No performance threshold is applied" in markdown_output
    assert "| Fixture | Notes | Operation | Wall-clock ms |" in markdown_output

    artifact = tmp_path / "rebuild-cost-v1.md"
    assert main(["--format", "markdown", "--output", str(artifact)]) == 0
    assert capsys.readouterr().out == ""
    artifact_output = artifact.read_text(encoding="utf-8")
    assert "# Derived Read-Model Rebuild Cost rebuild-cost-v1" in artifact_output
    assert "No performance threshold is applied" in artifact_output
