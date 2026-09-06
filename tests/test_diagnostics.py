"""Focused safe doctor report tests."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from typer.testing import CliRunner

from second_brain.adapters.vault import FileSystemVaultReader
from second_brain.application.diagnostics import BuildDoctorReport, DoctorStatus
from second_brain.application.reports import VaultSnapshot
from second_brain.entrypoints.cli.app import app
from tests.conftest import create_vault, managed_note, snapshot_tree, write_note

GENERATED_AT = datetime(2026, 9, 6, 12, 0, tzinfo=UTC)
runner = CliRunner()


def test_doctor_report_is_healthy_bounded_and_read_only(tmp_path: Path) -> None:
    vault = create_vault(tmp_path / "vault")
    write_note(vault, "10 Projects/Note.md", managed_note())
    before = snapshot_tree(vault)

    report = BuildDoctorReport(
        FileSystemVaultReader(vault),
        config_resolvable=True,
        clock=lambda: GENERATED_AT,
    ).execute()

    assert report.status is DoctorStatus.HEALTHY
    assert report.generated_at == GENERATED_AT
    assert report.managed_note_count == 1
    assert report.enrolled_personal_memory_count == 0
    assert report.valid_decision_count == 0
    assert report.valid_outcome_count == 0
    assert report.timeline.status is DoctorStatus.HEALTHY
    assert report.self_model.status is DoctorStatus.HEALTHY
    assert report.self_retrieval.status is DoctorStatus.UNAVAILABLE
    assert report.self_retrieval.required is False
    payload = report.as_dict()
    serialized = json.dumps(payload, ensure_ascii=False)
    assert "vault_path" not in payload
    assert "paths" not in payload["manifest"]
    assert "second-brain.yaml" not in serialized
    assert snapshot_tree(vault) == before


def test_doctor_counts_enrollment_but_exports_only_codes_for_malformed_evidence(
    tmp_path: Path,
) -> None:
    vault = create_vault(tmp_path / "vault")
    secret = "private body sentinel"
    write_note(
        vault,
        "10 Projects/Private.md",
        """---
id: 0198f4c5-6a00-7000-8000-000000000002
type: zettel
created: 2026-09-02T12:00:00+03:00
second_brain_personal_memory: 1
evidence_kind: not-a-kind
self_kind: preference
evidence_at: unknown
evidence_at_precision: unknown
---
"""
        + secret
        + "\n",
    )

    report = BuildDoctorReport(
        FileSystemVaultReader(vault),
        config_resolvable=True,
        clock=lambda: GENERATED_AT,
    ).execute()

    assert report.status is DoctorStatus.DEGRADED
    assert report.enrolled_personal_memory_count == 1
    assert report.error_count > 0
    serialized = json.dumps(report.as_dict(), ensure_ascii=False)
    assert secret not in serialized
    assert str(vault) not in serialized
    assert "PERSONAL_MEMORY_INVALID_EVIDENCE_KIND" in serialized
    assert "evidence_kind is not supported" not in serialized


def test_doctor_counts_are_unknown_when_a_declared_content_root_is_unavailable(
    tmp_path: Path,
) -> None:
    vault = create_vault(tmp_path / "vault")
    (vault / "10 Projects").rmdir()

    report = BuildDoctorReport(
        FileSystemVaultReader(vault),
        config_resolvable=True,
        clock=lambda: GENERATED_AT,
    ).execute()

    assert report.status is DoctorStatus.UNAVAILABLE
    assert report.manifest_available is True
    assert report.content_roots_available is False
    assert report.managed_note_count is None
    assert report.enrolled_personal_memory_count is None
    assert report.valid_decision_count is None
    assert report.valid_outcome_count is None


def test_doctor_support_root_failure_does_not_hide_content_counts(tmp_path: Path) -> None:
    vault = create_vault(tmp_path / "vault")
    write_note(vault, "10 Projects/Note.md", managed_note())
    (vault / "_templates").rmdir()
    (vault / "_attachments").rmdir()

    report = BuildDoctorReport(
        FileSystemVaultReader(vault),
        config_resolvable=True,
        clock=lambda: GENERATED_AT,
    ).execute()

    assert report.manifest_available is True
    assert report.content_roots_available is True
    assert report.managed_note_count == 1
    assert report.enrolled_personal_memory_count == 0
    assert report.valid_decision_count == 0
    assert report.valid_outcome_count == 0


def test_doctor_derived_layers_replay_the_initial_snapshot_once(tmp_path: Path) -> None:
    vault = create_vault(tmp_path / "vault")
    write_note(vault, "10 Projects/Note.md", managed_note())
    snapshot = FileSystemVaultReader(vault).scan()

    class SnapshotReader:
        def __init__(self, value: VaultSnapshot) -> None:
            self.value = value
            self.calls = 0

        def scan(self) -> VaultSnapshot:
            self.calls += 1
            return self.value

    reader = SnapshotReader(snapshot)
    report = BuildDoctorReport(
        reader,
        config_resolvable=True,
        clock=lambda: GENERATED_AT,
    ).execute()

    assert report.status is DoctorStatus.HEALTHY
    assert reader.calls == 1


def test_doctor_unavailable_config_has_safe_null_counts() -> None:
    report = BuildDoctorReport(
        None,
        config_resolvable=False,
        clock=lambda: GENERATED_AT,
    ).execute()

    assert report.status is DoctorStatus.UNAVAILABLE
    assert report.exit_code == 2
    assert report.managed_note_count is None
    assert report.self_retrieval.required is False
    payload = report.as_dict()
    assert payload["config"] == {"resolvable": False}
    assert payload["counts"]["managed_notes"] is None
    assert payload["diagnostics"] == [
        {"code": "CONFIG_UNAVAILABLE", "severity": "error", "count": 1}
    ]


def test_doctor_cli_does_not_leak_unresolved_path(tmp_path: Path) -> None:
    missing_vault = tmp_path / "private-missing-vault"
    result = runner.invoke(
        app,
        ["--vault-path", str(missing_vault), "doctor", "--format", "json"],
    )

    assert result.exit_code == 2
    assert str(missing_vault) not in result.stdout
    assert str(missing_vault) not in result.stderr
    payload = json.loads(result.stdout)
    assert payload["status"] == "unavailable"
    assert payload["config"]["resolvable"] is False
    assert payload["diagnostics"][0]["code"] == "CONFIG_UNAVAILABLE"
